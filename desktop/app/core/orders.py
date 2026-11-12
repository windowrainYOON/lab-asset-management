"""Order records → '마지막 주문 날짜' / '마지막 주문 수량' for each stock item.

The order spreadsheets (e.g. 공용주문) are only read. For every item the most
recent order is found by catalog number (Cat#), or by product name when the
item has no Cat#. The two columns exist only inside the app; nothing is
written to the inventory or order files.

For categories with no quantity column, the last ordered amount is used as the
current stock, so low-stock reporting works for them too.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .importer import clean_text
from .project import Project
from .sheet_io import read_grid

LAST_DATE = "마지막 주문 날짜"
LAST_QTY = "마지막 주문 수량"
FILE_LAST_DATE = "마지막 주문일"  # the same, as a real column of the lists made from orders


def last_date_field(project, category_id: int):
    """The field holding the last order date: the in-app column, or the file's own one."""
    fields = project.fields(category_id)
    return (next((f for f in fields if f.virtual and f.name == LAST_DATE), None)
            or next((f for f in fields if not f.virtual and f.name == FILE_LAST_DATE), None))

# Header words for each column of an order sheet (lower-case, matched as substrings).
_HEADERS = {
    "product": ("제품명", "품명", "product", "item"),
    "company": ("회사", "제조사", "company", "manufacturer", "brand"),
    "catalog": ("cat", "카탈로그"),
    "qty": ("수량", "qty", "quantity"),
    "unit": ("구분", "unit", "포장"),
    "order_date": ("주문일자", "주문일", "order date"),
    "date": ("날짜", "date"),
    "orderer": ("주문자", "ordered by", "requester"),
    "size": ("용량", "size", "volume"),
    "size_unit": ("단위",),
}
_DATE_RE = re.compile(r"(\d{4})\s*[-./]\s*(\d{1,2})\s*[-./]\s*(\d{1,2})")


def norm_key(text: str) -> str:
    """Catalog numbers / names compared without spaces, dashes and case: 'LM 011-51' == 'lm01151'."""
    return re.sub(r"[^0-9a-z가-힣]", "", (text or "").lower())


def norm_date(text: str) -> str:
    m = _DATE_RE.search(text or "")
    if not m:
        return ""
    y, mo, d = (int(g) for g in m.groups())
    if not (1990 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31):
        return ""
    return f"{y:04d}-{mo:02d}-{d:02d}"


@dataclass
class Order:
    date: str
    qty: float
    qty_text: str
    unit: str
    product: str
    company: str
    catalog: str
    orderer: str
    source: str = ""  # order file name
    size: str = ""
    size_unit: str = ""


@dataclass
class OrderIndex:
    by_catalog: dict[str, list[Order]] = field(default_factory=dict)
    by_name: dict[str, list[Order]] = field(default_factory=dict)
    count: int = 0
    files: list[str] = field(default_factory=list)

    def add(self, o: Order) -> None:
        self.count += 1
        if norm_key(o.catalog):
            self.by_catalog.setdefault(norm_key(o.catalog), []).append(o)
        if norm_key(o.product):
            self.by_name.setdefault(norm_key(o.product), []).append(o)

    def all(self) -> list[Order]:
        seen, out = set(), []
        for lst in list(self.by_catalog.values()) + list(self.by_name.values()):
            for o in lst:
                if id(o) not in seen:
                    seen.add(id(o))
                    out.append(o)
        return out

    def last(self, catalog: str = "", name: str = "", orderer: str = "") -> tuple[str, float, str, list[Order]] | None:
        """(date, total qty on that date, unit, orders that date) of the newest matching order,
        optionally only among one person's orders."""
        orders = self.by_catalog.get(norm_key(catalog)) if norm_key(catalog) else None
        if not orders and not norm_key(catalog) and norm_key(name):
            orders = self.by_name.get(norm_key(name))
        if orders and orderer:
            orders = [o for o in orders if o.orderer == orderer]
        if not orders:
            return None
        latest = max(o.date for o in orders)
        same = [o for o in orders if o.date == latest]
        units = {o.unit for o in same if o.unit}
        return latest, sum(o.qty for o in same), (units.pop() if len(units) == 1 else ""), same


def _columns(header: list[str]) -> dict[str, int]:
    cols: dict[str, int] = {}
    low = [h.lower().replace("\n", " ") for h in header]
    for key, words in _HEADERS.items():
        for i, h in enumerate(low):
            if h and any(w in h for w in words) and i not in cols.values():
                if key == "date" and ("last" in h or "납품" in h):
                    continue
                cols[key] = i
                break
    return cols


def read_orders(paths: list[Path], index: OrderIndex | None = None,
                labels: dict | None = None) -> OrderIndex:
    """labels: path -> original file name (cached copies are named by file id)."""
    index = index or OrderIndex()
    for path in paths:
        path = Path(path)
        label = (labels or {}).get(path, path.name)
        try:
            tables = read_grid(path)
        except Exception:  # noqa: BLE001  unreadable file: skip
            continue
        for t in tables:
            header = [clean_text(c) for c in (t.raw[t.header_row] if t.raw else [])]
            cols = _columns(header)
            if "qty" not in cols or not ({"catalog", "product"} & cols.keys()):
                continue
            index.files.append(f"{label}:{t.name}")
            head_word = header[cols.get("date", cols.get("product", 0))]
            for raw in t.raw[t.header_row + 1:]:
                cells = [clean_text(c) for c in raw]

                def get(key: str) -> str:
                    i = cols.get(key)
                    return cells[i] if i is not None and i < len(cells) else ""

                if not any(cells) or get("date") == head_word or get("product") == header[cols.get("product", 0)]:
                    continue  # empty or a repeated header row
                date = norm_date(get("order_date")) or norm_date(get("date"))
                qty_text = get("qty")
                try:
                    qty = float(re.findall(r"\d+(?:\.\d+)?", qty_text)[0]) if qty_text else 0.0
                except IndexError:
                    qty = 0.0
                if not date or not (get("catalog") or get("product")):
                    continue
                index.add(Order(date, qty, qty_text, get("unit"), get("product"), get("company"),
                                get("catalog"), get("orderer"), label, get("size"), get("size_unit")))
    return index


def load_order_folder(remote, dest: Path) -> OrderIndex:
    """Download every spreadsheet of the order-records folder into dest and read it."""
    import shutil

    from .gsheet_ops import GSHEET_MIME
    from .sheet_io import SPREADSHEET_SUFFIXES

    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    paths, labels = [], {}
    for rf in remote.list_files():
        native = getattr(rf, "mime_type", "") == GSHEET_MIME  # Google Sheet: read as .xlsx export
        if (native or rf.name.lower().endswith(SPREADSHEET_SUFFIXES)) and not rf.name.startswith("~$"):
            path = dest / f"{rf.id}{'.xlsx' if native else Path(rf.name).suffix.lower()}"
            remote.download(rf.id, path)
            paths.append(path)
            labels[path] = rf.name
    return read_orders(paths, labels=labels)


def is_personal_source(name: str) -> bool:
    n = (name or "").lower()
    return "개인" in n or "personal" in n


def _fmt_qty(qty: float, unit: str) -> str:
    text = str(int(qty)) if qty.is_integer() else f"{qty:g}"
    return f"{text} {unit}".strip()


def apply_orders(project: Project, index: OrderIndex) -> dict[int, int]:
    """Add the two computed columns to every '재고 관리' category and fill them.
    Returns matched item counts per category."""
    observer, project.observer = project.observer, None
    matched: dict[int, int] = {}
    try:
        for cat in project.categories(kind="managed"):
            # drop columns from a previous refresh
            for f in project.fields(cat.id):
                if f.virtual and f.name in (LAST_DATE, LAST_QTY):
                    project.conn.execute("DELETE FROM fields WHERE id = ?", (f.id,))
            cat_f = project.field_by_role(cat.id, "catalog")
            name_f = project.field_by_role(cat.id, "name")
            if cat_f is None and name_f is None:
                continue
            has_qty = project.field_by_role(cat.id, "quantity") is not None
            orderer = project.get_meta(f"orderer:{cat.id}") or ""
            dates: dict[int, str] = {}
            qtys: dict[int, str] = {}
            for item in project.items(cat.id):
                hit = index.last(item.get(cat_f.id) if cat_f else "", item.get(name_f.id) if name_f else "",
                                 orderer)
                if hit is None:
                    continue
                date, qty, unit, _ = hit
                dates[item.id] = date
                qtys[item.id] = _fmt_qty(qty, unit)
            matched[cat.id] = len(dates)
            if not dates:
                continue
            has_file_date = any(f.name == FILE_LAST_DATE and not f.virtual for f in project.fields(cat.id))
            fd = None if has_file_date else project.add_virtual_field(cat.id, LAST_DATE, "date")
            # No quantity column in the sheet: what was last ordered is taken as the stock.
            fq = project.add_virtual_field(cat.id, LAST_QTY, "number", "" if has_qty else "quantity")
            if fd is not None:
                project.set_virtual_values(fd.id, dates)
            project.set_virtual_values(fq.id, qtys)
        project.commit()
    finally:
        project.observer = observer
    return matched
