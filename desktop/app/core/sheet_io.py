"""Read spreadsheet files cell-for-cell and write cell edits back into them.

Used when a Google Drive folder is the data source: the app shows every file
in the folder and every in-app change is written to the same cell of the
original file. Coordinates are kept exact (raw[r][c] is Excel row r+1,
column c+1) so edits land in the right place.

.xlsx/.xlsm are edited with openpyxl, which keeps the workbook's formatting.
.xls (the old binary format) can't be edited in place; see `convert_xls`.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .importer import Table, _read_csv, clean_text, detect_header_row

SPREADSHEET_SUFFIXES = (".xlsx", ".xlsm", ".xls", ".csv", ".tsv")
WRITABLE_SUFFIXES = (".xlsx", ".xlsm", ".csv", ".tsv")
_NUM_RE = re.compile(r"^[-+]?\d+(\.\d+)?$")


def read_grid(path: str | Path) -> list[Table]:
    """Every non-empty sheet as a Table whose raw grid starts at cell A1."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".csv", ".tsv"):
        tables = [Table("", _read_csv(path, "\t" if suffix == ".tsv" else None))]
    elif suffix == ".xls":
        tables = _read_xls(path)
    else:
        tables = _read_xlsx(path)
    tables = [t for t in tables if any(clean_text(c) for r in t.raw for c in r)]
    for t in tables:
        t.header_row = detect_header_row(t.raw)
    return tables


def _read_xlsx(path: Path) -> list[Table]:
    from openpyxl import load_workbook

    # read_only streams the sheet. Sheets often claim 16,384 columns because of
    # whole-row formatting or a stray cell far to the right; the real table width
    # is taken from the first rows, which also makes big files fast to read.
    wb = load_workbook(path, data_only=True, read_only=True)
    out = []
    for ws in wb.worksheets:
        probe = [list(r) for r in ws.iter_rows(min_row=1, max_row=300, min_col=1,
                                                 max_col=min(ws.max_column or 1, 500), values_only=True)]
        limit = _used_width(probe) + 20
        rows = [list(r) for r in ws.iter_rows(min_row=1, min_col=1, max_col=limit, values_only=True)]
        width = _used_width(rows)
        rows = [r[:width] + [None] * (width - len(r[:width])) for r in rows]
        while rows and not any(c not in (None, "") for c in rows[-1]):
            rows.pop()
        out.append(Table(ws.title, rows))
    wb.close()
    return out


def _used_width(rows: list[list[Any]], max_gap: int = 30) -> int:
    """Rightmost used column, ignoring cells separated from the table by a wide empty gap."""
    used = set()
    for r in rows:
        for i, v in enumerate(r):
            if v is not None and v != "":
                used.add(i)
    width, last = 0, -1
    for i in sorted(used):
        if i - last > max_gap and last >= 0:
            break
        last = i
        width = i + 1
    return width


def _read_xls(path: Path) -> list[Table]:
    import xlrd

    book = xlrd.open_workbook(str(path))
    out = []
    for sh in book.sheets():
        rows = []
        for r in range(sh.nrows):
            row = []
            for c in range(sh.ncols):
                cell = sh.cell(r, c)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    try:
                        row.append(xlrd.xldate_as_datetime(cell.value, book.datemode))
                        continue
                    except (ValueError, OverflowError):
                        pass
                row.append(cell.value if cell.ctype != xlrd.XL_CELL_EMPTY else None)
            rows.append(row)
        out.append(Table(sh.name, rows))
    return out


def data_rows(table: Table) -> list[tuple[int, list[str]]]:
    """(0-based grid row, cells as text) for every non-empty row under the header,
    stopping at a stop marker (e.g. an order-history block under the list)."""
    out = []
    width = table.width
    for r in range(table.header_row + 1, len(table.raw)):
        raw = table.raw[r]
        cells = [clean_text(raw[i] if i < len(raw) else None) for i in range(width)]
        if any(c in table.stop_markers for c in cells if c):
            break
        if any(cells):
            out.append((r, cells))
    return out


def to_cell_value(text: str) -> Any:
    """Store numbers as numbers so Excel formulas and sorting keep working."""
    text = text if text is not None else ""
    if _NUM_RE.match(text.strip()):
        num = float(text)
        return int(num) if num.is_integer() and "." not in text else num
    return text if text != "" else None


# ---- edit operations ------------------------------------------------------
# Each op is a dict: {"op": "set"|"insert_row"|"delete_row"|"delete_col",
#   "sheet": name, "row": 0-based, "col": 0-based, "value": text,
#   "anchor": (col, text) identifying the row in case the file changed meanwhile}

@dataclass
class ApplyResult:
    applied: int = 0
    skipped: list[str] = None  # descriptions of edits that could not be placed

    def __post_init__(self):
        if self.skipped is None:
            self.skipped = []


def apply_ops(path: str | Path, ops: list[dict], relocate: bool = False) -> ApplyResult:
    """Apply edits to the file in place. relocate=True when the file may have
    changed since the edits were made: rows are found again by their anchor."""
    path = Path(path)
    if path.suffix.lower() in (".csv", ".tsv"):
        return _apply_csv(path, ops, relocate)
    from openpyxl import load_workbook

    keep_vba = path.suffix.lower() == ".xlsm"
    wb = load_workbook(path, keep_vba=keep_vba)
    res = ApplyResult()
    for op in ops:
        kind = op["op"]
        if kind == "add_sheet":
            if op["sheet"] not in wb.sheetnames:
                ws = wb.create_sheet(op["sheet"][:31])
                for c, h in enumerate(op.get("headers", []), 1):
                    ws.cell(row=1, column=c).value = h
            res.applied += 1
            continue
        if kind == "rename_sheet":
            if op["old"] in wb.sheetnames:
                wb[op["old"]].title = op["new"][:31]
            res.applied += 1
            continue
        if kind == "delete_sheet":
            if op["sheet"] in wb.sheetnames and len(wb.sheetnames) > 1:
                wb.remove(wb[op["sheet"]])
            res.applied += 1
            continue
        if op.get("sheet") not in wb.sheetnames and len(wb.sheetnames) > 1:
            res.skipped.append(_describe(op))
            continue
        ws = wb[op["sheet"]] if op.get("sheet") in wb.sheetnames else wb.worksheets[0]
        row = op.get("row")
        if relocate and row is not None and op.get("anchor"):
            row = _relocate(ws, op["anchor"], row)
            if row is None:
                res.skipped.append(_describe(op))
                continue
        if kind == "set":
            ws.cell(row=row + 1, column=op["col"] + 1).value = to_cell_value(op.get("value", ""))
        elif kind == "insert_row":
            if op.get("shift"):
                ws.insert_rows(row + 1)
            for col, text in (op.get("values") or {}).items():
                ws.cell(row=row + 1, column=int(col) + 1).value = to_cell_value(text)
        elif kind == "delete_row":
            ws.delete_rows(row + 1)
        elif kind == "delete_col":
            ws.delete_cols(op["col"] + 1)
        res.applied += 1
    wb.save(path)
    wb.close()
    return res


def _relocate(ws, anchor, row: int) -> int | None:
    col, text = anchor
    if not text:
        return row
    cell = ws.cell(row=row + 1, column=col + 1).value
    if clean_text(cell) == text:
        return row
    hits = [r for r in range(1, ws.max_row + 1) if clean_text(ws.cell(row=r, column=col + 1).value) == text]
    return hits[0] - 1 if len(hits) == 1 else None


def _describe(op: dict) -> str:
    anchor = (op.get("anchor") or (0, ""))[1]
    return f"{op.get('sheet') or ''} {anchor}: {op['op']}".strip()


def _apply_csv(path: Path, ops: list[dict], relocate: bool) -> ApplyResult:
    delim = "\t" if path.suffix.lower() == ".tsv" else ","
    rows = [list(r) for r in _read_csv(path, delim)]
    res = ApplyResult()
    for op in ops:
        row = op.get("row")
        if relocate and row is not None and op.get("anchor"):
            col, text = op["anchor"]
            if not (row < len(rows) and col < len(rows[row]) and rows[row][col].strip() == text):
                hits = [i for i, r in enumerate(rows) if col < len(r) and r[col].strip() == text]
                if len(hits) != 1:
                    res.skipped.append(_describe(op))
                    continue
                row = hits[0]
        if op["op"] == "set":
            while len(rows) <= row:
                rows.append([])
            r = rows[row]
            while len(r) <= op["col"]:
                r.append("")
            r[op["col"]] = op.get("value", "")
        elif op["op"] == "insert_row":
            new = [""] * (max((int(c) for c in (op.get("values") or {})), default=-1) + 1)
            for c, text in (op.get("values") or {}).items():
                new[int(c)] = text
            if op.get("shift"):
                rows.insert(row, new)
            else:
                while len(rows) <= row:
                    rows.append([])
                rows[row] = new
        elif op["op"] == "delete_row" and row < len(rows):
            rows.pop(row)
        elif op["op"] == "delete_col":
            for r in rows:
                if op["col"] < len(r):
                    r.pop(op["col"])
        res.applied += 1
    with path.open("w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh, delimiter=delim).writerows(rows)
    return res


def convert_xls(src: str | Path, dest: str | Path) -> Path:
    """Copy an .xls workbook's values into a new .xlsx (formatting is not carried over)."""
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for t in _read_xls(Path(src)):
        ws = wb.create_sheet(title=t.name[:31] or "Sheet1")
        for r, row in enumerate(t.raw, 1):
            for c, value in enumerate(row, 1):
                if value is None or value == "":
                    continue
                if isinstance(value, float) and value.is_integer():
                    value = int(value)
                ws.cell(row=r, column=c).value = value
    if not wb.worksheets:
        wb.create_sheet("Sheet1")
    wb.save(dest)
    return Path(dest)


def new_workbook(dest: str | Path, headers: list[str], sheet: str = "Sheet1") -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    for c, h in enumerate(headers, 1):
        ws.cell(row=1, column=c).value = h
        ws.cell(row=1, column=c).font = Font(bold=True)
    wb.save(dest)
    return Path(dest)


def stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")
