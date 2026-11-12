from openpyxl import Workbook

from app.core.orders import LAST_DATE, LAST_QTY, apply_orders, norm_date, norm_key, read_orders
from app.core.project import Project
from app.core.services.inventory import stock_info


def _orders(path):
    wb = Workbook()
    ws = wb.active
    rows = [
        ["날짜", "제품명", "용량", "단위", "회사", "# cat", "수량", "구분", "주문자", "업체명", "주문일자"],
        ["2023-01-05", "RPMI 1640", "500", "ml", "Welgene", "LM 011-51", "5", "bottle", "A", "", "2023-01-06"],
        ["날짜", "제품명", "용량", "단위", "회사", "# cat", "수량", "구분", "주문자", "업체명", "주문일자"],
        ["2024-03-02", "RPMI 1640 medium", "500", "ml", "welgene", "LM011-51", "3", "bottle", "B", "", ""],
        ["2024-03-02", "RPMI", "500", "ml", "welgene", "LM011-51", "1", "bottle", "B", "", ""],
        ["2022-07-01", "EcoRI", "", "", "NEB", "", "1", "ea", "C", "", ""],
    ]
    for r in rows:
        ws.append(r)
    wb.save(path)
    return path


def test_read_and_match_orders(tmp_path):
    idx = read_orders([_orders(tmp_path / "o.xlsx")])
    assert idx.count == 4  # repeated header skipped
    date, qty, unit, _ = idx.last(catalog="lm 01151")
    assert (date, qty, unit) == ("2024-03-02", 4.0, "bottle")
    assert idx.last(name="ecori")[0] == "2022-07-01"
    assert idx.last(catalog="NOPE", name="EcoRI") is None  # has a Cat# that was never ordered
    assert norm_date("2009.5.19") == "2009-05-19" and norm_key("LM 011-51") == "lm01151"


def test_columns_only_in_app_and_used_as_stock(tmp_path):
    idx = read_orders([_orders(tmp_path / "o.xlsx")])
    p = Project.create(tmp_path / "p.labproj")
    seen = []
    p.observer = lambda ev, **d: seen.append(ev)
    cat = p.add_category("common", "Reagents")
    name = p.add_field(cat.id, "Name", role="name")
    catf = p.add_field(cat.id, "Cat#", role="catalog")
    a = p.add_item(cat.id, {name.id: "RPMI", catf.id: "LM011-51"})
    b = p.add_item(cat.id, {name.id: "Unknown", catf.id: "X-1"})
    seen.clear()
    matched = apply_orders(p, idx)
    assert matched[cat.id] == 1 and seen == []  # nothing reported to the file writer
    fields = {f.name: f for f in p.fields(cat.id)}
    assert fields[LAST_DATE].virtual and fields[LAST_QTY].role == "quantity"
    item = p.item(a)
    assert item.get(fields[LAST_DATE].id) == "2024-03-02"
    assert item.get(fields[LAST_QTY].id) == "4 bottle"
    assert stock_info(p, p.category(cat.id), item).state == "ok"
    assert stock_info(p, p.category(cat.id), p.item(b)).state == "unknown"
    p.close()


def test_oldest_tenth_of_single_items_are_soon(tmp_path):
    p = Project.create(tmp_path / "p.labproj")
    cat = p.add_category("common", "Media")
    name = p.add_field(cat.id, "Name", role="name")
    qty = p.add_field(cat.id, "Qty", role="quantity")
    ids = [p.add_item(cat.id, {name.id: f"m{i}", qty.id: "1"}) for i in range(20)]
    two = p.add_item(cat.id, {name.id: "two", qty.id: "2"})
    none = p.add_item(cat.id, {name.id: "none", qty.id: "0"})
    fd = p.add_virtual_field(cat.id, LAST_DATE, "date")
    p.set_virtual_values(fd.id, {iid: f"2024-01-{i + 1:02d}" for i, iid in enumerate(ids)}
                         | {two: "2020-01-01", none: "2020-01-01"})
    c = p.category(cat.id)
    cache: dict = {}
    states = [stock_info(p, c, p.item(i), cache).state for i in ids]
    assert states[:2] == ["soon", "soon"] and set(states[2:]) == {"ok"}  # 10% of 20, oldest first
    assert stock_info(p, c, p.item(two), cache).state == "ok"
    assert stock_info(p, c, p.item(none), cache).state == "out"
    p.close()


def test_order_lists_for_unlisted_and_personal_items(tmp_path):
    from openpyxl import load_workbook
    from io import BytesIO
    from app.core import order_items
    from test_folder import FakeFolder, make_xlsx, open_model

    (tmp_path / "remote").mkdir()
    folder = FakeFolder(tmp_path / "remote")
    folder.add("Reagents.xlsx", make_xlsx(tmp_path / "r.xlsx", [["Name", "Cat#", "Qty"], ["RPMI", "LM011-51", 2]]))
    head = ["날짜", "제품명", "용량", "단위", "회사", "# cat", "수량", "구분", "주문자", "업체명", "주문일자"]
    common = make_xlsx(tmp_path / "c.xlsx", [head,
        ["2024-03-02", "RPMI", "500", "ml", "Welgene", "LM 011-51", "1", "bottle", "A", "", ""],
        ["2024-05-01", "Trypsin", "100", "ml", "Gibco", "T-1", "1", "bottle", "B", "", ""]])
    personal = make_xlsx(tmp_path / "p.xlsx", [head,
        ["2023-01-01", "IBMX", "100", "mg", "sigma", "I-7018", "1", "ea", "김다은", "", ""],
        ["2023-02-01", "IBMX", "100", "mg", "sigma", "I-7018", "2", "ea", "김다은", "", ""],
        ["2023-02-01", "Dex", "25", "mg", "sigma", "D-4902", "1", "ea", "노하준", "", ""],
        ["2023-03-05", "IBMX 3-isobutyl", "100", "mg", "sigma", "I7018", "1", "ea", "노하준", "", ""],
        ["2022-12-01", "Dexamethasone", "25", "mg", "sigma", "D-4902", "1", "ea", "김다은", "", ""]])
    idx = read_orders([common, personal], labels={common: "공용주문.xlsx", personal: "개인주문 (Personal order).xlsx"})
    m, p = open_model(folder, tmp_path)
    plans = order_items.plan(p, idx)
    # IBMX (same Cat#, different names) was last ordered by 노하준, so it is filed under 노하준 only
    assert [(x.name, len(x.rows)) for x in plans] == [("기타", 1), ("노하준 개인 품목", 2)]
    noh = plans[1].rows
    assert {(r[2], r[6], r[7]) for r in noh} == {("I7018", "2023-03-05", "노하준"), ("D-4902", "2023-02-01", "노하준")}
    order_items.apply(m, p, plans)
    m.flush()
    p.close()
    rows = [r for r in load_workbook(BytesIO(folder.path("기타.xlsx").read_bytes())).active.iter_rows(values_only=True)]
    assert rows[1][:3] == ("Trypsin", "Gibco", "T-1") and rows[1][6:8] == ("2024-05-01", "B")
    m2, p2 = open_model(folder, tmp_path, "b")
    cats = {c.name: c for c in p2.categories()}
    assert cats["노하준 개인 품목"].scope == "personal" and cats["기타"].scope == "common"
    matched = apply_orders(p2, idx)
    noh = cats["노하준 개인 품목"]
    names = {f.name for f in p2.fields(noh.id)}
    assert "마지막 주문일" in names and LAST_DATE not in names  # the file's own date column is used
    assert matched[noh.id] == 2
    assert order_items.plan(p2, idx) == []  # nothing new the second time
    # a newer order of a listed item refreshes its date instead of adding a row
    idx2 = read_orders([common, personal], labels={common: "공용주문.xlsx", personal: "개인주문 (Personal order).xlsx"})
    from app.core.orders import Order
    newer = next(o for o in idx2.all() if o.catalog == "T-1")
    idx2.add(Order(**{**newer.__dict__, "date": "2025-01-10", "orderer": "C"}))
    plans2 = order_items.plan(p2, idx2)
    assert [(x.name, len(x.rows), len(x.updates)) for x in plans2] == [("기타", 0, 1)]
    order_items.apply(m2, p2, plans2)
    other = cats["기타"]
    date_f = next(f for f in p2.fields(other.id) if f.name == "마지막 주문일")
    assert p2.items(other.id)[0].get(date_f.id) == "2025-01-10"
    p2.close()
