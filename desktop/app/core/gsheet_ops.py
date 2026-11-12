"""Turn the app's spreadsheet edits into Google Sheets API requests.

A native Google Sheet in the data folder is read like an Excel file (Drive
exports it as .xlsx), but it is never re-uploaded as a whole: many people may
be editing it at the same time, and a whole-file upload would erase what they
typed meanwhile. Each edit becomes the smallest Sheets API request instead
(one cell, one inserted or deleted row or column, one sheet added, renamed
or deleted), sent in one `spreadsheets.batchUpdate`.

The same edit dicts as `sheet_io.apply_ops` are used. They are replayed on a
fresh copy of the sheet's values so that rows are found again by their anchor
when someone else inserted or deleted rows, and so that later edits in the
same batch see the effect of earlier ones.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from .importer import clean_text
from .sheet_io import ApplyResult, _describe, to_cell_value

GSHEET_MIME = "application/vnd.google-apps.spreadsheet"


@dataclass
class SheetMeta:
    sheet_id: int
    rows: int      # grid size (Sheets refuses writes outside it)
    cols: int


def cell_data(text: str | None) -> dict:
    """CellData for a text typed in the app: numbers stay numbers, empty clears."""
    value = to_cell_value(text if text is not None else "")
    if value is None:
        return {}
    if isinstance(value, (int, float)):
        return {"userEnteredValue": {"numberValue": value}}
    return {"userEnteredValue": {"stringValue": str(value)}}


class _Planner:
    def __init__(self, grids: dict[str, list[list[Any]]], meta: dict[str, SheetMeta]):
        # copies: the plan must not change what the caller holds
        self.grids = {k: [list(r) for r in v] for k, v in grids.items()}
        self.meta = {k: SheetMeta(m.sheet_id, m.rows, m.cols) for k, m in meta.items()}
        self.requests: list[dict] = []
        self.res = ApplyResult()

    # ---- helpers -----------------------------------------------------------------
    def _grid(self, title: str) -> list[list[Any]]:
        return self.grids.setdefault(title, [])

    def _ensure(self, title: str, rows: int, cols: int) -> None:
        m = self.meta[title]
        if rows > m.rows:
            self.requests.append({"appendDimension": {"sheetId": m.sheet_id, "dimension": "ROWS",
                                                      "length": rows - m.rows}})
            m.rows = rows
        if cols > m.cols:
            self.requests.append({"appendDimension": {"sheetId": m.sheet_id, "dimension": "COLUMNS",
                                                      "length": cols - m.cols}})
            m.cols = cols

    def _set(self, title: str, row: int, col: int, text: str | None) -> None:
        self._ensure(title, row + 1, col + 1)
        self.requests.append({"updateCells": {
            "start": {"sheetId": self.meta[title].sheet_id, "rowIndex": row, "columnIndex": col},
            "rows": [{"values": [cell_data(text)]}],
            "fields": "userEnteredValue"}})
        grid = self._grid(title)
        while len(grid) <= row:
            grid.append([])
        r = grid[row]
        while len(r) <= col:
            r.append(None)
        r[col] = to_cell_value(text if text is not None else "")

    def _relocate(self, title: str, anchor, row: int) -> int | None:
        col, text = anchor
        if not text:
            return row
        grid = self._grid(title)

        def at(r: int):
            return grid[r][col] if r < len(grid) and col < len(grid[r]) else None

        if clean_text(at(row)) == text:
            return row
        hits = [r for r in range(len(grid)) if clean_text(at(r)) == text]
        return hits[0] if len(hits) == 1 else None

    def _new_sheet_id(self) -> int:
        used = {m.sheet_id for m in self.meta.values()}
        while True:
            sid = random.randint(1_000_000, 2_000_000_000)
            if sid not in used:
                return sid

    # ---- ops -----------------------------------------------------------------------
    def apply(self, op: dict) -> None:
        kind = op["op"]
        if kind == "add_sheet":
            title = op["sheet"]
            if title not in self.meta:
                sid = self._new_sheet_id()
                self.requests.append({"addSheet": {"properties": {"sheetId": sid, "title": title}}})
                self.meta[title] = SheetMeta(sid, 1000, 26)
                self.grids[title] = []
                for c, h in enumerate(op.get("headers", [])):
                    self._set(title, 0, c, h)
            self.res.applied += 1
            return
        if kind == "rename_sheet":
            old, new = op["old"], op["new"]
            if old in self.meta and new not in self.meta:
                m = self.meta.pop(old)
                self.requests.append({"updateSheetProperties": {
                    "properties": {"sheetId": m.sheet_id, "title": new}, "fields": "title"}})
                self.meta[new] = m
                self.grids[new] = self.grids.pop(old, [])
            self.res.applied += 1
            return
        if kind == "delete_sheet":
            title = op["sheet"]
            if title in self.meta and len(self.meta) > 1:
                self.requests.append({"deleteSheet": {"sheetId": self.meta.pop(title).sheet_id}})
                self.grids.pop(title, None)
            self.res.applied += 1
            return

        title = op.get("sheet")
        if title not in self.meta:
            if len(self.meta) != 1:
                self.res.skipped.append(_describe(op))
                return
            title = next(iter(self.meta))
        m = self.meta[title]
        grid = self._grid(title)
        row = op.get("row")
        if row is not None and op.get("anchor"):
            row = self._relocate(title, op["anchor"], row)
            if row is None:
                self.res.skipped.append(_describe(op))
                return
        if kind == "set":
            self._set(title, row, op["col"], op.get("value", ""))
        elif kind == "insert_row":
            # someone may have typed into the row we meant to fill: put ours above it
            taken = row < len(grid) and any(v not in (None, "") for v in grid[row])
            if op.get("shift") or taken:
                self._ensure(title, row, 0)
                self.requests.append({"insertDimension": {
                    "range": {"sheetId": m.sheet_id, "dimension": "ROWS", "startIndex": row, "endIndex": row + 1},
                    "inheritFromBefore": row > 0}})
                m.rows += 1
                if row <= len(grid):
                    grid.insert(row, [])
            for col, text in sorted((op.get("values") or {}).items(), key=lambda kv: int(kv[0])):
                self._set(title, row, int(col), text)
        elif kind == "delete_row":
            if row < m.rows:
                self.requests.append({"deleteDimension": {"range": {
                    "sheetId": m.sheet_id, "dimension": "ROWS", "startIndex": row, "endIndex": row + 1}}})
                m.rows -= 1
                if row < len(grid):
                    grid.pop(row)
        elif kind == "delete_col":
            col = op["col"]
            if col < m.cols:
                self.requests.append({"deleteDimension": {"range": {
                    "sheetId": m.sheet_id, "dimension": "COLUMNS", "startIndex": col, "endIndex": col + 1}}})
                m.cols -= 1
                for r in grid:
                    if col < len(r):
                        r.pop(col)
        self.res.applied += 1


def plan_requests(grids: dict[str, list[list[Any]]], meta: dict[str, SheetMeta],
                  ops: list[dict]) -> tuple[list[dict], ApplyResult]:
    """Sheets API batchUpdate requests for `ops`, given the sheet's current values
    (`grids`: sheet title -> rows from A1) and its sheets (`meta`, in tab order).
    Rows are always found again by their anchor: the sheet is edited live."""
    planner = _Planner(grids, meta)
    for op in ops:
        planner.apply(op)
    return planner.requests, planner.res


# ---- whole-state edits (feature files: 공지사항, 업무, 예산, 장비 예약, 주문 요청) -------------
# These files are written by turning the whole state into a workbook. For a Google Sheet
# the workbook is never uploaded: the state before and after the queued edits are both
# written the same way, compared cell by cell, and only the cells that differ are sent.

def workbook_grids(path) -> dict[str, list[list[Any]]]:
    """Sheet title -> rows of raw cell values (formulas kept as "=…" text), in tab order."""
    from openpyxl import load_workbook

    wb = load_workbook(path)  # not data_only: formulas are compared as written
    out: dict[str, list[list[Any]]] = {}
    for ws in wb.worksheets:
        out[ws.title] = [list(row) for row in ws.iter_rows(values_only=True)]
    return out


def _same(a: Any, b: Any) -> bool:
    if a in (None, "") and b in (None, ""):
        return True
    return type(a) is type(b) and a == b or (isinstance(a, (int, float)) and isinstance(b, (int, float))
                                              and not isinstance(a, bool) and not isinstance(b, bool) and a == b)


_EPOCH = None


def typed_cell(value: Any) -> dict:
    """CellData for a Python value written by the app (keeps its type)."""
    import datetime as dt
    global _EPOCH
    if value is None or value == "":
        return {}
    if isinstance(value, bool):
        return {"userEnteredValue": {"boolValue": value}}
    if isinstance(value, (int, float)):
        return {"userEnteredValue": {"numberValue": value}}
    if isinstance(value, (dt.datetime, dt.date)):
        _EPOCH = _EPOCH or dt.datetime(1899, 12, 30)
        d = value if isinstance(value, dt.datetime) else dt.datetime(value.year, value.month, value.day)
        serial = (d - _EPOCH).total_seconds() / 86400
        kind = "DATE_TIME" if isinstance(value, dt.datetime) and (d.hour or d.minute or d.second) else "DATE"
        pattern = "yyyy-mm-dd hh:mm" if kind == "DATE_TIME" else "yyyy-mm-dd"
        return {"userEnteredValue": {"numberValue": serial},
                "userEnteredFormat": {"numberFormat": {"type": kind, "pattern": pattern}}}
    if isinstance(value, dt.time):
        return {"userEnteredValue": {"numberValue": (value.hour * 3600 + value.minute * 60 + value.second) / 86400},
                "userEnteredFormat": {"numberFormat": {"type": "TIME", "pattern": "hh:mm"}}}
    text = str(value)
    if text.startswith("="):
        return {"userEnteredValue": {"formulaValue": text}}
    return {"userEnteredValue": {"stringValue": text}}


def diff_requests(old: dict[str, list[list[Any]]], new: dict[str, list[list[Any]]],
                  meta: dict[str, SheetMeta]) -> list[dict]:
    """batchUpdate requests that turn `old` into `new` (both: title -> rows), touching
    only the cells that differ. `meta` is the live spreadsheet's tabs. Tabs only in `old`
    are deleted, tabs only in `new` are added; nothing else in the spreadsheet moves."""
    planner = _Planner({}, meta)
    meta = planner.meta  # the planner's own copy: grid sizes grow as cells are added
    reqs = planner.requests
    for title in new:
        if title not in meta:
            sid = planner._new_sheet_id()
            rows = max(1000, len(new[title]) + 10)
            cols = max(26, max((len(r) for r in new[title]), default=0))
            reqs.append({"addSheet": {"properties": {"sheetId": sid, "title": title,
                                                     "gridProperties": {"rowCount": rows, "columnCount": cols}}}})
            meta[title] = SheetMeta(sid, rows, cols)
    for title in old:
        if title not in new and title in meta and len(meta) > 1:
            reqs.append({"deleteSheet": {"sheetId": meta.pop(title).sheet_id}})
    for title, rows in new.items():
        before = old.get(title, [])
        m = meta[title]
        n_rows = max(len(rows), len(before))
        for r in range(n_rows):
            a = before[r] if r < len(before) else []
            b = rows[r] if r < len(rows) else []
            for c in range(max(len(a), len(b))):
                va = a[c] if c < len(a) else None
                vb = b[c] if c < len(b) else None
                if _same(va, vb):
                    continue
                if vb in (None, "") and (r >= m.rows or c >= m.cols):
                    continue  # clearing a cell the sheet does not have
                planner._ensure(title, r + 1, c + 1)
                cd = typed_cell(vb)
                fields = "userEnteredValue" + (",userEnteredFormat.numberFormat" if "userEnteredFormat" in cd else "")
                reqs.append({"updateCells": {
                    "start": {"sheetId": m.sheet_id, "rowIndex": r, "columnIndex": c},
                    "rows": [{"values": [cd]}], "fields": fields}})
    return reqs
