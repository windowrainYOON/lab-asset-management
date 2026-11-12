"""Turn order records into item lists:

- 기타: things ordered on the common order sheet that no inventory file lists.
- one file per person for the personal order sheet (Personal Asset).

Items are told apart by catalog number (Cat#); only an order without a Cat#
falls back to the product name. Repeated orders of one item become one row with
the latest order date. On the personal sheet an item ordered by several people
goes to the person who ordered it last.

Running it again appends new items and refreshes the last order date (and
person) of rows it made before; nothing else in the files is changed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .orders import Order, OrderIndex, is_personal_source, norm_key
from .project import Project

OTHER_NAME = "기타"
OTHER_OLD_NAMES = ("기타 품목",)
PERSONAL_FOLDER = "Personal Asset"
LAST_ORDER_DATE = "마지막 주문일"
LAST_ORDERER = "마지막 주문자"
HEADERS = ["Name (제품명)", "Company (제조사)", "Cat#", "용량", "단위", "구분", LAST_ORDER_DATE, LAST_ORDERER,
           "Location (위치)", "비고"]


@dataclass
class PlannedFile:
    name: str                 # file name without extension
    scope: str                # common / personal
    folder: str = ""          # subfolder in the data folder
    orderer: str = ""         # personal lists: whose orders
    rows: list[list[str]] = field(default_factory=list)
    category_id: int | None = None  # the file already exists: rows are appended
    updates: list[tuple[int, str, str]] = field(default_factory=list)  # (item id, newer date, orderer)


def item_key(catalog: str, name: str) -> str:
    return ("c:" + norm_key(catalog)) if norm_key(catalog) else ("n:" + norm_key(name))


def _row(o: Order) -> list[str]:
    size, unit = _size(o)
    return [o.product, o.company, o.catalog, size, unit, o.unit, o.date, o.orderer, "", ""]


def _size(o: Order) -> tuple[str, str]:
    return o.size, o.size_unit


def _unique_latest(orders: list[Order]) -> dict[str, Order]:
    """One order per item (the newest), keyed by Cat# or name."""
    out: dict[str, Order] = {}
    for o in orders:
        if not (o.product or o.catalog):
            continue
        k = item_key(o.catalog, o.product)
        if k in ("c:", "n:"):
            continue
        # newest wins; on the same day the later row in the sheet does
        if k not in out or o.date >= out[k].date:
            out[k] = o
    return out


def _keys_of_category(project: Project, cid: int) -> dict[str, int]:
    """item key -> item id. Items with a Cat# are known by it; others by name."""
    cat_f = project.field_by_role(cid, "catalog")
    name_f = project.field_by_role(cid, "name")
    keys: dict[str, int] = {}
    for it in project.items(cid):
        c = it.get(cat_f.id) if cat_f else ""
        n = it.get(name_f.id) if name_f else ""
        if norm_key(c) or norm_key(n):
            keys.setdefault(item_key(c, n), it.id)
    return keys


def _field_named(project: Project, cid: int, name: str):
    return next((f for f in project.fields(cid) if f.name == name and not f.virtual), None)


def _fill(project: Project, pf: PlannedFile, latest: dict[str, Order], have: dict[str, int]) -> None:
    """New rows for unseen items; date/person refresh for rows already there."""
    date_f = _field_named(project, pf.category_id, LAST_ORDER_DATE) if pf.category_id else None
    who_f = _field_named(project, pf.category_id, LAST_ORDERER) if pf.category_id else None
    for k, o in sorted(latest.items(), key=lambda kv: kv[1].product.lower()):
        if k not in have:
            pf.rows.append(_row(o))
        elif date_f is not None:
            item = project.item(have[k])
            if o.date > item.get(date_f.id) or (who_f and o.date == item.get(date_f.id)
                                                and o.orderer and item.get(who_f.id) != o.orderer):
                pf.updates.append((item.id, o.date, o.orderer))


def plan(project: Project, index: OrderIndex) -> list[PlannedFile]:
    cats = {c.name: c for c in project.categories()}
    orders = index.all()
    common = [o for o in orders if not is_personal_source(o.source)]
    personal = [o for o in orders if is_personal_source(o.source)]
    out: list[PlannedFile] = []

    # 1. common orders that no inventory file has
    other_cat = next((cats[n] for n in (OTHER_NAME, *OTHER_OLD_NAMES) if n in cats), None)
    known: set[str] = set()
    for c in project.categories():
        if project.get_meta(f"orderer:{c.id}") or (other_cat and c.id == other_cat.id):
            continue  # personal lists and 기타 itself are not inventory
        known |= set(_keys_of_category(project, c.id))
    other = PlannedFile(other_cat.name if other_cat else OTHER_NAME, "common")
    other.category_id = other_cat.id if other_cat else None
    have = _keys_of_category(project, other.category_id) if other_cat else {}
    latest = {k: o for k, o in _unique_latest(common).items() if k not in known}
    _fill(project, other, latest, have)
    out.append(other)

    # 2. personal orders: one row per item (by Cat#), filed under whoever ordered it last
    latest_personal = _unique_latest([o for o in personal if (o.orderer or "").strip()])
    by_person: dict[str, dict[str, Order]] = {}
    for k, o in latest_personal.items():
        by_person.setdefault(o.orderer.strip(), {})[k] = o
    for who in sorted(by_person):
        fname = f"{who} 개인 품목"
        pf = PlannedFile(fname, "personal", PERSONAL_FOLDER, who)
        have = {}
        if fname in cats:
            pf.category_id = cats[fname].id
            have = _keys_of_category(project, pf.category_id)
        _fill(project, pf, by_person[who], have)
        out.append(pf)
    return [p for p in out if p.rows or p.updates]


def write_new_file(pf: PlannedFile, dest: Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "품목"
    ws.append(HEADERS)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in pf.rows:
        ws.append([v if v != "" else None for v in r])
    for col, width in zip("ABCDEFGHIJ", (44, 18, 16, 8, 6, 8, 13, 12, 16, 20)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    wb.save(dest)
    return dest


def append_rows(project: Project, pf: PlannedFile) -> int:
    """Add rows to an existing list through the project (the folder model writes them to the file),
    and refresh the last order date / person of rows already there."""
    for name in (LAST_ORDER_DATE, LAST_ORDERER):
        if pf.updates and _field_named(project, pf.category_id, name) is None:
            project.add_field(pf.category_id, name)
    by_header = {f.name: f for f in project.fields(pf.category_id) if not f.virtual}
    for iid, d, who in pf.updates:
        project.set_value(iid, by_header[LAST_ORDER_DATE].id, d)
        if who:
            project.set_value(iid, by_header[LAST_ORDERER].id, who)
    n = len(pf.updates)
    for r in pf.rows:
        values = {by_header[h].id: v for h, v in zip(HEADERS, r) if v and h in by_header}
        if values:
            project.add_item(pf.category_id, values)
            n += 1
    return n


def apply(model, project: Project, plans: list[PlannedFile]) -> dict[str, int]:
    """Create the new files in the Drive folder (or append to existing ones) and save.
    Network work: call off the UI thread only for the parts that don't touch the project."""
    done: dict[str, int] = {}
    tmp = model.cache_dir / "new-lists"
    tmp.mkdir(parents=True, exist_ok=True)
    for pf in plans:
        if pf.category_id is not None:
            done[pf.name] = append_rows(project, pf)
            continue
        path = write_new_file(pf, tmp / f"{abs(hash(pf.name))}.xlsx")
        rf = model.remote.upload_new(f"{pf.name}.xlsx", path, pf.folder)
        # the package size is not stock: the last ordered amount stands in for it
        entry = {"scope": pf.scope, "kind": "managed",
                 "fields": {h.lower(): {"role": ""} for h in ("용량", "단위", "구분", "비고", LAST_ORDERER)}
                 | {LAST_ORDER_DATE.lower(): {"role": "", "ftype": "date"}}}
        if pf.orderer:
            entry["orderer"] = pf.orderer
        model._set_overlay(("categories", rf.id), entry)
        done[pf.name] = len(pf.rows)
    return done
