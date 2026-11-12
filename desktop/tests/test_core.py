import csv

import pytest

from app.core.importer import import_table, plan_columns, read_tables
from app.core.project import Project
from app.core.services.inventory import build_report, parse_quantity, report_text
from app.core.services.search import parse_query, search


@pytest.fixture
def project(tmp_path):
    p = Project.create(tmp_path / "t.labproj", "Test")
    yield p
    p.close()


def _cell_lines(project):
    cat = project.add_category("common", "Cell line")
    name = project.add_field(cat.id, "Name", role="name")
    vials = project.add_field(cat.id, "Vials", "number", role="quantity")
    loc = project.add_field(cat.id, "Location", role="location")
    a = project.add_item(cat.id, {name.id: "HeLa", vials.id: "1", loc.id: "-80˚C / 1A-20"})
    b = project.add_item(cat.id, {name.id: "HEK293", vials.id: "4", loc.id: "LN2"})
    c = project.add_item(cat.id, {name.id: "THP-1", vials.id: "0"})
    return cat, (name, vials, loc), (a, b, c)


@pytest.mark.parametrize("text,expected", [
    ("3", (3.0, "")), ("2/7", (9.0, "")), ("7ml", (7.0, "ml")), ("(4)", (4.0, "")),
    ("", (None, "")), ("R3189S", (3189.0, "S")),
])
def test_parse_quantity(text, expected):
    assert parse_quantity(text) == expected


def test_stock_states_use_threshold_of_one(project):
    cat, _, (hela, hek, thp) = _cell_lines(project)
    rep = build_report(project)
    s = rep.summaries[0]
    assert (s.counts["ok"], s.counts["low"], s.counts["out"]) == (1, 1, 1)
    assert [r.item_id for r in rep.shortages] == [thp, hela]  # out first
    project.update_item(hek, status="low")  # manual override
    assert build_report(project).summaries[0].counts["low"] == 2
    project.update_item(hela, threshold=0)
    assert build_report(project).summaries[0].counts["ok"] == 1
    assert "Cell line" in report_text(project)


def test_scopes_are_separate(project):
    _cell_lines(project)
    personal = project.add_category("personal", "My antibodies")
    assert [c.name for c in project.categories("personal")] == ["My antibodies"]
    assert build_report(project, "personal").total_items == 0
    assert build_report(project, "common").total_items == 3
    assert personal.scope == "personal"


def test_search_text_filters_and_details(project):
    cat, (name, vials, loc), (hela, hek, thp) = _cell_lines(project)
    project.add_detail(hek, "보관 조건", "passage 3 이하")
    assert [h.item.id for h in search(project, "HeLa")] == [hela]
    assert [h.item.id for h in search(project, "passage")] == [hek]
    assert {h.item.id for h in search(project, "vials<2")} == {hela, thp}
    assert [h.item.id for h in search(project, "qty>=2")] == [hek]
    assert [h.item.id for h in search(project, "location:LN2")] == [hek]
    assert [h.item.id for h in search(project, "state:소진")] == [thp]
    assert search(project, "scope:personal HeLa") == []
    # two-character Korean terms fall back to LIKE
    project.set_value(hela, loc.id, "공용 냉동고")
    assert [h.item.id for h in search(project, "공용")] == [hela]


def test_parse_query():
    q = parse_query('"pcDNA3.1 Flag" cat:plasmid vials<2 scope:개인')
    assert q.terms == ["pcDNA3.1 Flag"]
    assert q.category == "plasmid" and q.scope == "personal"
    assert q.filters == [("vials", "<", "2")]


def test_field_edit_and_delete(project):
    cat, (name, vials, loc), (hela, *_rest) = _cell_lines(project)
    project.update_field(loc.id, name="Box")
    assert [h.item.id for h in search(project, "Box")] != []
    project.delete_field(loc.id)
    assert str(loc.id) not in project.item(hela).values
    project.add_field(cat.id, "Count", "number", role="quantity")
    assert project.field(vials.id).role == ""  # one field per role


def test_csv_import_detects_header_and_skips_notes(project, tmp_path):
    path = tmp_path / "stock.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Lab stock list", "", ""])
        w.writerow(["", "", ""])
        w.writerow(["Name", "Vials", "Location"])
        w.writerow(["", "*적을수록 Red", ""])
        w.writerow(["HeLa", "2", "-80"])
        w.writerow(["HEK293", "0", "LN2"])
    (table,) = read_tables(path)
    assert table.header_row == 2
    plans = plan_columns(table)
    assert [p.role for p in plans] == ["name", "quantity", "location"]
    cat = project.add_category("common", "Cells")
    res = import_table(project, cat.id, table, plans)
    assert res.added == 2
    again = import_table(project, cat.id, table, plans)
    assert again.added == 0 and again.skipped_duplicates == 2
    assert build_report(project).count("out") == 1


def test_save_as_copies_everything(project, tmp_path):
    _cell_lines(project)
    copy = project.save_as(tmp_path / "copy.labproj")
    assert copy.count_items(copy.categories()[0].id) == 3
    copy.close()


def test_status_kind_has_no_low_state(project):
    cat, _, (hela, hek, thp) = _cell_lines(project)
    project.update_category(cat.id, kind="status")
    rep = build_report(project, kind="status")
    c = rep.summaries[0].counts
    assert (c["ok"], c["low"], c["out"]) == (2, 0, 1)
    assert build_report(project, kind="managed").summaries == []
    assert [h.item.id for h in search(project, "state:없음")] == [thp]
    assert "재고 현황" in report_text(project)
