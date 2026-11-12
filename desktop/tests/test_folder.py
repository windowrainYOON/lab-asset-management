"""Drive folder as the data source: read every file, write edits back into the cells."""
import json
import shutil
from io import BytesIO
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from app.core.folder_model import OVERLAY_NAME, FolderModel
from app.integrations.gdrive.drive import RemoteFile


class FakeFolder:
    """A Drive folder stand-in backed by a local directory. Revisions bump on every write."""

    def __init__(self, root: Path):
        self.root = root
        self.meta: dict[str, dict] = {}
        self.n = 0
        self.archived: list[str] = []

    def add(self, name, src):
        self.n += 1
        fid = f"f{self.n}"
        shutil.copyfile(src, self.root / fid)
        self.meta[fid] = {"name": name, "rev": 1}
        return fid

    def _rf(self, fid):
        m = self.meta[fid]
        return RemoteFile(fid, m["name"], "", f"r{m['rev']}", folder=m.get("folder", ""))

    def list_files(self):
        return [self._rf(f) for f, m in self.meta.items() if not m.get("trashed") and not m.get("archived")]

    def download(self, fid, dest):
        shutil.copyfile(self.root / fid, dest)

    def revision(self, fid):
        return self._rf(fid).version

    def update(self, fid, src):
        shutil.copyfile(src, self.root / fid)
        self.meta[fid]["rev"] += 1
        return self._rf(fid)

    def upload_new(self, name, src, folder=""):
        rf = self._rf(self.add(name, src))
        self.meta[rf.id]["folder"] = folder
        return rf

    def rename(self, fid, name):
        self.meta[fid]["name"] = name

    def trash(self, fid):
        self.meta[fid]["trashed"] = True

    def move_to_subfolder(self, fid, sub):
        self.meta[fid]["archived"] = True
        self.archived.append(self.meta[fid]["name"])

    def path(self, name):
        fid = next(f for f, m in self.meta.items() if m["name"] == name and not m.get("trashed"))
        return self.root / fid


def make_xlsx(path, rows, title="Sheet1", extra=None):
    wb = Workbook()
    ws = wb.active
    ws.title = title
    for r in rows:
        ws.append(r)
    if extra:
        ws2 = wb.create_sheet(extra[0])
        for r in extra[1]:
            ws2.append(r)
    wb.save(path)
    return path


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "remote").mkdir()
    f = FakeFolder(tmp_path / "remote")
    src = make_xlsx(tmp_path / "a.xlsx", [
        ["Antibody list"],  # title row above the header
        ["Name", "Cat#", "Vials", "Location"],
        ["Anti-GFP", "ab1", 3, "Box1"],
        ["Anti-Actin", "ab2", 1, "Box1"],
        [None, None, None, None],
        ["최근 주문내역"],
        ["2024-01-01", "ab1"],
    ])
    f.add("Antibody.xlsx", src)
    return f


def open_model(folder, tmp_path, tag="a"):
    m = FolderModel(folder, tmp_path / f"cache-{tag}")
    m.load()
    project = m.build(tmp_path / f"cache-{tag}" / "session.labproj")
    return m, project


def cell_rows(path, sheet=None):
    wb = load_workbook(BytesIO(Path(path).read_bytes()))
    ws = wb[sheet] if sheet else wb.worksheets[0]
    return [[c for c in r] for r in ws.iter_rows(values_only=True)]


def test_folder_files_become_categories(folder, tmp_path):
    m, p = open_model(folder, tmp_path)
    cats = p.categories()
    assert [c.name for c in cats] == ["Antibody"]
    items = p.items(cats[0].id)
    assert len(items) == 2  # order history under the list is not an item
    assert p.field_by_role(cats[0].id, "quantity").name == "Vials"
    p.close()


def test_value_edit_is_written_to_the_cell(folder, tmp_path):
    m, p = open_model(folder, tmp_path)
    cid = p.categories()[0].id
    qty = p.field_by_role(cid, "quantity")
    item = p.items(cid)[1]
    p.set_value(item.id, qty.id, "0")
    m.flush()
    rows = cell_rows(folder.path("Antibody.xlsx"))
    assert rows[3][:3] == ["Anti-Actin", "ab2", 0]
    assert rows[5][0] == "최근 주문내역"  # rest of the sheet untouched
    p.close()
    # a fresh open shows the change
    m2, p2 = open_model(folder, tmp_path, "b")
    cid2 = p2.categories()[0].id
    assert p2.items(cid2)[1].get(p2.field_by_role(cid2, "quantity").id) == "0"
    p2.close()


def test_add_and_delete_rows(folder, tmp_path):
    m, p = open_model(folder, tmp_path)
    cid = p.categories()[0].id
    name = p.field_by_role(cid, "name")
    new_id = p.add_item(cid, {})
    p.set_value(new_id, name.id, "Anti-Tubulin")
    p.delete_items([p.items(cid)[0].id])  # Anti-GFP
    m.flush()
    rows = cell_rows(folder.path("Antibody.xlsx"))
    names = [r[0] for r in rows]
    assert names[:4] == ["Antibody list", "Name", "Anti-Actin", "Anti-Tubulin"]
    assert "최근 주문내역" in names  # pushed down, not overwritten
    p.close()


def test_new_column_and_settings_survive_reopen(folder, tmp_path):
    m, p = open_model(folder, tmp_path)
    cid = p.categories()[0].id
    f = p.add_field(cid, "Lot")
    p.set_value(p.items(cid)[0].id, f.id, "L-77")
    p.update_category(cid, kind="status", scope="personal")
    p.update_item(p.items(cid)[0].id, status="out")
    p.add_detail(p.items(cid)[0].id, "희석", "1:1000")
    m.flush()
    rows = cell_rows(folder.path("Antibody.xlsx"))
    assert rows[1][4] == "Lot" and rows[2][4] == "L-77"
    overlay = json.loads(folder.path(OVERLAY_NAME).read_text(encoding="utf-8"))
    assert overlay["categories"]
    p.close()
    m2, p2 = open_model(folder, tmp_path, "b")
    cat = p2.categories()[0]
    assert (cat.kind, cat.scope) == ("status", "personal")
    first = p2.items(cat.id)[0]
    assert first.status == "out"
    assert [(d.key, d.value) for d in p2.details(first.id)] == [("희석", "1:1000")]
    p2.close()


def test_edit_lands_on_the_right_row_after_someone_else_inserted_rows(folder, tmp_path):
    m, p = open_model(folder, tmp_path)
    cid = p.categories()[0].id
    qty = p.field_by_role(cid, "quantity")
    actin = p.items(cid)[1]
    # someone edits the file in Excel meanwhile: a new row at the top of the list
    path = folder.path("Antibody.xlsx")
    wb = load_workbook(BytesIO(path.read_bytes()))
    wb.active.insert_rows(3)
    wb.active["A3"] = "Anti-Myc"
    wb.save(path)
    folder.meta[next(iter(folder.meta))]["rev"] += 1
    p.set_value(actin.id, qty.id, "5")
    res = m.flush()
    assert res.remote_changed
    rows = cell_rows(path)
    assert [r[0] for r in rows[2:5]] == ["Anti-Myc", "Anti-GFP", "Anti-Actin"]
    assert rows[4][2] == 5 and rows[3][2] == 3
    p.close()


def test_xls_is_saved_as_xlsx_on_first_edit(tmp_path):
    xls = Path(__file__).resolve().parents[1] / "data" / "samples" / "Bacteria Compcell.xls"
    if not xls.exists():
        pytest.skip("sample .xls not available")
    (tmp_path / "remote").mkdir()
    folder = FakeFolder(tmp_path / "remote")
    folder.add("Bacteria & Compcell.xls", xls)
    m, p = open_model(folder, tmp_path)
    cid = p.categories()[0].id
    item = p.items(cid)[0]
    some_field = p.fields(cid)[0]
    p.set_value(item.id, some_field.id, "edited")
    m.flush()
    assert folder.archived == ["Bacteria & Compcell.xls"]
    names = [f.name for f in folder.list_files()]
    assert "Bacteria & Compcell.xlsx" in names
    p.close()
    m2, p2 = open_model(folder, tmp_path, "b")
    cid2 = p2.categories()[0].id
    assert any("edited" in i.values.values() for i in p2.items(cid2))
    p2.close()


def test_new_category_creates_a_file(folder, tmp_path):
    m, p = open_model(folder, tmp_path)
    cat = p.add_category("common", "Primers")
    name = p.add_field(cat.id, "Name", role="name")
    iid = p.add_item(cat.id, {})
    p.set_value(iid, name.id, "GAPDH-F")
    m.flush()
    rows = cell_rows(folder.path("Primers.xlsx"))
    assert rows[0][0] == "Name" and rows[1][0] == "GAPDH-F"
    p.close()


def test_sheets_are_groups_and_sheet_edits_reach_the_file(tmp_path):
    (tmp_path / "remote").mkdir()
    folder = FakeFolder(tmp_path / "remote")
    src = make_xlsx(tmp_path / "s.xlsx", [["Name", "Qty"], ["Taq", 2]], title="PCR",
                    extra=("Cloning", [["Name", "Qty"], ["T4 ligase", 1]]))
    folder.add("Enzyme.xlsx", src)
    m, p = open_model(folder, tmp_path)
    cid = p.categories()[0].id
    assert p.subgroups(cid) == ["PCR", "Cloning"]
    p.add_subgroup(cid, "Restriction")
    name = p.field_by_role(cid, "name")
    iid = p.add_item(cid, {}, subgroup="Restriction")
    p.set_value(iid, name.id, "EcoRI")
    p.rename_subgroup(cid, "PCR", "PCR enzymes")
    p.delete_subgroup(cid, "Cloning")
    m.flush()
    wb = load_workbook(BytesIO(folder.path("Enzyme.xlsx").read_bytes()))
    assert wb.sheetnames == ["PCR enzymes", "Restriction"]
    assert [r[0] for r in wb["Restriction"].iter_rows(values_only=True)] == ["Name", "EcoRI"]
    p.close()
    m2, p2 = open_model(folder, tmp_path, "b")
    cid2 = p2.categories()[0].id
    assert p2.subgroups(cid2) == ["PCR enzymes", "Restriction"]
    assert p2.count_items(cid2) == 2
    p2.close()


def test_moving_an_item_to_another_sheet_moves_the_row(tmp_path):
    (tmp_path / "remote").mkdir()
    folder = FakeFolder(tmp_path / "remote")
    src = make_xlsx(tmp_path / "s.xlsx", [["Name", "Qty"], ["Taq", 2], ["Pfu", 1]], title="PCR",
                    extra=("Cloning", [["Name", "Qty"], ["T4 ligase", 1]]))
    folder.add("Enzyme.xlsx", src)
    m, p = open_model(folder, tmp_path)
    cid = p.categories()[0].id
    taq = p.items(cid)[0]
    p.update_item(taq.id, subgroup="Cloning")
    m.flush()
    wb = load_workbook(BytesIO(folder.path("Enzyme.xlsx").read_bytes()))
    assert [r[0] for r in wb["PCR"].iter_rows(values_only=True)] == ["Name", "Pfu"]
    assert [r[:2] for r in wb["Cloning"].iter_rows(values_only=True)] == [("Name", "Qty"), ("T4 ligase", 1), ("Taq", 2)]
    p.close()


def test_header_rename_and_column_edits_on_every_sheet(tmp_path):
    (tmp_path / "remote").mkdir()
    folder = FakeFolder(tmp_path / "remote")
    src = make_xlsx(tmp_path / "s.xlsx", [["Name", "Qty"], ["Taq", 2]], title="PCR",
                    extra=("Cloning", [["Name", "Qty"], ["T4 ligase", 1]]))
    folder.add("Enzyme.xlsx", src)
    m, p = open_model(folder, tmp_path)
    cid = p.categories()[0].id
    qty = p.field_by_role(cid, "quantity")
    p.update_field(qty.id, name="Tubes")
    p.add_field(cid, "Lot")
    m.flush()
    wb = load_workbook(BytesIO(folder.path("Enzyme.xlsx").read_bytes()))
    for sh in ("PCR", "Cloning"):
        assert [c.value for c in wb[sh][1]] == ["Name", "Tubes", "Lot"]
    p.delete_field(p.field_by_role(cid, "quantity").id)
    m.flush()
    wb = load_workbook(BytesIO(folder.path("Enzyme.xlsx").read_bytes()))
    assert [c.value for c in wb["Cloning"][1]] == ["Name", "Lot"]
    assert wb["Cloning"]["A2"].value == "T4 ligase"
    p.close()


def test_sync_downloads_only_the_files_that_changed(folder, tmp_path):
    folder.add("Enzyme.xlsx", make_xlsx(tmp_path / "e.xlsx", [["Name", "Qty"], ["Taq", 2]]))
    m, p = open_model(folder, tmp_path)
    assert m.refresh_changed() is False  # nothing new
    # someone else edits Enzyme only
    m2, p2 = open_model(folder, tmp_path, "b")
    cid2 = next(c.id for c in p2.categories() if c.name == "Enzyme")
    p2.set_value(p2.items(cid2)[0].id, p2.field_by_role(cid2, "quantity").id, "7")
    m2.flush()
    p2.close()
    got = []
    real = folder.download
    folder.download = lambda fid, dest: (got.append(folder.meta[fid]["name"]), real(fid, dest))
    # our own edit to Antibody is kept and sent first; then only Enzyme comes down
    cid = next(c.id for c in p.categories() if c.name == "Antibody")
    p.set_value(p.items(cid)[0].id, p.field_by_role(cid, "quantity").id, "9")
    m.flush()
    assert m.refresh_changed() is True
    assert got == ["Enzyme.xlsx"]
    p.close()
    p = m.build(tmp_path / "cache-a" / "again.labproj")
    by = {c.name: c.id for c in p.categories()}
    q = lambda cid: p.items(cid)[0].get(p.field_by_role(cid, "quantity").id)  # noqa: E731
    assert (q(by["Antibody"]), q(by["Enzyme"])) == ("9", "7")
    p.close()


def test_session_keeps_edits_here_and_syncs_in_one_go(folder, tmp_path, monkeypatch):
    """Edits show at once and stay on this Mac; 최신화 (or the 2-minute timer) sends them."""
    from PySide6.QtWidgets import QApplication
    from app.core import settings
    from app.core.notices import NOTICE_NAME, Notice
    from app.core.work import WORK_NAME, WorkProject
    from app.ui import cloud

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(settings, "user_data_dir", lambda: tmp_path)
    folder.folder_name = lambda: "Lab"
    s = cloud.FolderSession(None, "lab", remote=folder)
    p = s.open(None)
    assert s.sync_timer.interval() == 120_000
    s.notices.upsert(Notice(title="공지"))
    s.work.save(lambda ps, ts: (ps + [WorkProject(name="청소", members=["A"])], ts))
    cid = p.categories()[0].id
    p.set_value(p.items(cid)[0].id, p.field_by_role(cid, "quantity").id, "5")
    names = lambda: {m["name"] for m in folder.meta.values()}  # noqa: E731
    assert s.dirty() and NOTICE_NAME not in names() and WORK_NAME not in names()
    assert [n.title for n in s.notices.notices] == ["공지"] and s.work.projects[0].name == "청소"
    s.sync_now(None, orders=False)
    assert not s.dirty() and {NOTICE_NAME, WORK_NAME} <= names()
    assert cell_rows(folder.path("Antibody.xlsx"))[2][2] == 5
    s.stop()
    p.close()
