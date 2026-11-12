"""Read CSV / XLSX / XLS files into tables that can be mapped onto a category.

Lab spreadsheets rarely start at A1: they have title rows, notes under the
header and empty leading columns. `read_tables` finds the header row on its
own; the import dialog lets the user correct it.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .project import Project

SUPPORTED_SUFFIXES = {".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls"}
HEADER_SCAN_ROWS = 15


@dataclass
class Table:
    """One sheet (or one CSV file) as raw cell rows."""
    name: str
    raw: list[list[Any]]
    header_row: int = 0
    stop_markers: list[str] = field(default_factory=list)

    @property
    def width(self) -> int:
        return max((len(r) for r in self.raw), default=0)

    def headers(self) -> list[str]:
        if not self.raw:
            return []
        row = self.raw[self.header_row] if self.header_row < len(self.raw) else []
        names: list[str] = []
        for i in range(self.width):
            text = clean_text(row[i] if i < len(row) else None)
            if not text:
                text = f"Column {i + 1}"
            base, n = text, 2
            while text in names:
                text = f"{base} ({n})"
                n += 1
            names.append(text)
        return names

    def rows(self) -> list[list[str]]:
        """Data rows below the header as text, dropping empty rows."""
        out: list[list[str]] = []
        for raw in self.raw[self.header_row + 1:]:
            cells = [clean_text(raw[i] if i < len(raw) else None) for i in range(self.width)]
            if any(c in self.stop_markers for c in cells if c):
                break
            if any(cells):
                out.append(cells)
        return out

    def used_columns(self) -> list[int]:
        """Columns that have a real header or any data."""
        rows = self.rows()
        header_row = self.raw[self.header_row] if self.raw else []
        used = []
        for i in range(self.width):
            has_header = i < len(header_row) and clean_text(header_row[i]) != ""
            has_data = any(r[i] for r in rows)
            if has_data or (has_header and rows == []):
                used.append(i)
        return used


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if value != value:  # NaN
            return ""
        if value.is_integer():
            return str(int(value))
    if isinstance(value, datetime):
        if value.hour == value.minute == value.second == 0:
            return value.strftime("%Y-%m-%d")
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    text = str(value).replace("\xa0", " ").strip()
    if text.lower() == "nan":
        return ""
    return text


def detect_header_row(raw: list[list[Any]]) -> int:
    """Pick the first row that looks like a header: several short text cells
    and data under it."""
    best, best_score = 0, -1.0
    for idx, row in enumerate(raw[:HEADER_SCAN_ROWS]):
        cells = [clean_text(c) for c in row]
        filled = [c for c in cells if c]
        if len(filled) < 2:
            continue
        texty = [c for c in filled if not _looks_numeric(c) and len(c) <= 40]
        below = raw[idx + 1: idx + 6]
        below_filled = sum(1 for r in below for c in r if clean_text(c))
        score = len(texty) * 2 + min(below_filled, 10) * 0.3 - idx * 0.2
        if len(texty) < len(filled) * 0.6:
            score -= 5
        if score > best_score:
            best, best_score = idx, score
    return best


def _looks_numeric(text: str) -> bool:
    return bool(re.fullmatch(r"[-+]?\d+(\.\d+)?", text))


def read_tables(path: str | Path) -> list[Table]:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"지원하지 않는 파일 형식입니다: {path.suffix}")
    if suffix in {".csv", ".tsv", ".txt"}:
        raw = _read_csv(path, "\t" if suffix == ".tsv" else None)
        tables = [Table(path.stem, raw)]
    else:
        import pandas as pd
        engine = "xlrd" if suffix == ".xls" else "openpyxl"
        sheets = pd.read_excel(path, sheet_name=None, header=None, engine=engine, dtype=object)
        tables = []
        for name, df in sheets.items():
            raw = df.where(df.notna(), None).values.tolist()
            if any(clean_text(c) for r in raw for c in r):
                tables.append(Table(str(name), raw))
    for t in tables:
        t.header_row = detect_header_row(t.raw)
    return tables


def _read_csv(path: Path, delimiter: str | None) -> list[list[Any]]:
    data = None
    for enc in ("utf-8-sig", "cp949", "latin-1"):
        try:
            data = path.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    if data is None:
        data = path.read_text(errors="replace")
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(data[:4096], delimiters=",;\t").delimiter
        except csv.Error:
            delimiter = ","
    return [row for row in csv.reader(data.splitlines(), delimiter=delimiter)]


# ---- type and role guessing ----------------------------------------------
_DATE_RE = re.compile(r"^\d{4}[-./]\s?\d{1,2}([-./]\s?\d{1,2})?\.?$")
_QTY_NAMES = ("vial", "vials", "수량", "qty", "quantity", "stock number", "tubes", "개수", "volume", "재고")
_FLAG_NAMES = ("유무",)
_NAME_NAMES = ("name", "이름", "명칭", "restriction enzyme stock")
_LOC_NAMES = ("location", "위치", "보관")
_OWNER_NAMES = ("deposited by", "purchased by", "purchsased by", "made by", "담당", "owner")
_CAT_NAMES = ("cat#", "cat no", "catalog")


def guess_type(values: list[str]) -> str:
    vals = [v for v in values if v]
    if not vals:
        return "text"
    if sum(1 for v in vals if _DATE_RE.match(v)) >= len(vals) * 0.8:
        return "date"
    if sum(1 for v in vals if _looks_numeric(v)) >= len(vals) * 0.9:
        return "number"
    return "text"


def guess_role(header: str) -> str:
    h = header.lower().strip()
    if any(k == h or k in h for k in _FLAG_NAMES):
        return "stock_flag"
    if h in _NAME_NAMES or h.startswith("name"):
        return "name"
    if any(k in h for k in _QTY_NAMES):
        return "quantity"
    if any(k in h for k in _CAT_NAMES):
        return "catalog"
    if any(k in h for k in _LOC_NAMES):
        return "location"
    if any(k in h for k in _OWNER_NAMES):
        return "owner"
    return ""


@dataclass
class ColumnPlan:
    index: int
    header: str
    include: bool = True
    target_field_id: int | None = None  # existing field, or None to create
    ftype: str = "text"
    role: str = ""


def plan_columns(table: Table) -> list[ColumnPlan]:
    headers = table.headers()
    rows = table.rows()
    plans: list[ColumnPlan] = []
    used_roles: set[str] = set()
    for i in table.used_columns():
        values = [r[i] for r in rows]
        role = guess_role(headers[i])
        if role in used_roles:
            role = ""
        if role:
            used_roles.add(role)
        plans.append(ColumnPlan(index=i, header=headers[i], ftype=guess_type(values), role=role))
    return plans


@dataclass
class ImportResult:
    category_id: int
    added: int = 0
    skipped_duplicates: int = 0
    created_fields: list[str] = field(default_factory=list)


def import_table(project: Project, category_id: int, table: Table, plans: list[ColumnPlan],
                 subgroup: str = "", skip_duplicates: bool = True) -> ImportResult:
    """Add the table's rows to a category, creating fields as needed."""
    result = ImportResult(category_id)
    existing = {f.name.lower(): f for f in project.fields(category_id)}
    col_to_field: dict[int, int] = {}
    for plan in plans:
        if not plan.include:
            continue
        fid = plan.target_field_id
        if fid is None:
            match = existing.get(plan.header.lower())
            if match:
                fid = match.id
                if plan.role and not match.role and project.field_by_role(category_id, plan.role) is None:
                    project.update_field(fid, role=plan.role)
            else:
                role = plan.role if plan.role and project.field_by_role(category_id, plan.role) is None else ""
                f = project.add_field(category_id, plan.header, plan.ftype, role)
                existing[f.name.lower()] = f
                result.created_fields.append(f.name)
                fid = f.id
        col_to_field[plan.index] = fid

    seen: set[tuple] = set()
    if skip_duplicates:
        for item in project.items(category_id):
            seen.add(_dup_key(item.values, col_to_field.values(), item.subgroup))

    # Rows without a name are notes or legends under the header, not items.
    name_cols = [p.index for p in plans if p.include and p.role == "name"]
    for row in table.rows():
        values = {fid: row[col] for col, fid in col_to_field.items() if row[col]}
        if not values or (name_cols and not row[name_cols[0]]):
            continue
        key = _dup_key({str(k): v for k, v in values.items()}, col_to_field.values(), subgroup)
        if skip_duplicates and key in seen:
            result.skipped_duplicates += 1
            continue
        seen.add(key)
        project.add_item(category_id, values, subgroup=subgroup, log=False, commit=False)
        result.added += 1
    project.commit()
    project._log(None, category_id, "import", f"{table.name}: {result.added}건")
    project.commit()
    return result


def _dup_key(values: dict[str, str], field_ids, subgroup: str) -> tuple:
    return (subgroup,) + tuple(values.get(str(fid), "") for fid in sorted(field_ids))
