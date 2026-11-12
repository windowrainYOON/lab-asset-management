"""Google Sheets in the data folder: read like Excel files, written cell by cell."""
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.core.folder_model import FolderModel
from app.core.gsheet_ops import GSHEET_MIME, SheetMeta, cell_data, plan_requests
from app.integrations.gdrive.drive import RemoteFile

from test_folder import FakeFolder, make_xlsx


class FakeSheets(FakeFolder):
    """FakeFolder plus native Google Sheets held as grids and edited only through
    batchUpdate requests, which are checked the way the Sheets API checks them."""

    def __init__(self, root: Path):
        super().__init__(root)
        self.sheets: dict[str, list[dict]] = {}   # fid -> tabs in order
        self.batches: list[list[dict]] = []
        self.whole_uploads: list[str] = []

    def add_sheet_file(self, name, tabs):
        self.n += 1
        fid = f"g{self.n}"
        self.meta[fid] = {"name": name, "rev": 1, "native": True}
        self.sheets[fid] = [{"id": i, "title": t, "rows": [list(r) for r in rows], "rowCount": 1000, "colCount": 26}
                            for i, (t, rows) in enumerate(tabs)]
        return fid

    def _rf(self, fid):
        rf = super()._rf(fid)
        if self.meta[fid].get("native"):
            rf.mime_type = GSHEET_MIME
        return rf

    def download(self, fid, dest):
        if fid not in self.sheets:
            return super().download(fid, dest)
        wb = Workbook()
        wb.remove(wb.active)
        for tab in self.sheets[fid]:
            ws = wb.create_sheet(tab["title"])
            for r, row in enumerate(tab["rows"], 1):
                for c, v in enumerate(row, 1):
                    if v not in (None, ""):
                        ws.cell(row=r, column=c).value = v
        wb.save(dest)

    def update(self, fid, src):
        if fid in self.sheets:
            self.whole_uploads.append(fid)
            raise AssertionError("a Google Sheet must never be overwritten as a whole file")
        return super().update(fid, src)

    def sheet_meta(self, fid):
        return {t["title"]: SheetMeta(t["id"], t["rowCount"], t["colCount"]) for t in self.sheets[fid]}

    def sheet_batch(self, fid, requests):
        self.batches.append(requests)
        tabs = self.sheets[fid]

        def tab(sid):
            return next(t for t in tabs if t["id"] == sid)

        for req in requests:
            (kind, body), = req.items()
            if kind == "updateCells":
                st = body["start"]
                t = tab(st["sheetId"])
                for dr, row in enumerate(body["rows"]):
                    for dc, cd in enumerate(row["values"]):
                        r, c = st["rowIndex"] + dr, st["columnIndex"] + dc
                        assert r < t["rowCount"] and c < t["colCount"], "outside the grid"
                        uv = cd.get("userEnteredValue", {})
                        v = uv.get("numberValue", uv.get("stringValue"))
                        while len(t["rows"]) <= r:
                            t["rows"].append([])
                        while len(t["rows"][r]) <= c:
                            t["rows"][r].append(None)
                        t["rows"][r][c] = v
            elif kind == "insertDimension":
                rg = body["range"]
                t = tab(rg["sheetId"])
                assert rg["dimension"] == "ROWS"
                t["rows"].insert(rg["startIndex"], [])
                t["rowCount"] += 1
            elif kind == "deleteDimension":
                rg = body["range"]
                t = tab(rg["sheetId"])
                if rg["dimension"] == "ROWS":
                    if rg["startIndex"] < len(t["rows"]):
                        t["rows"].pop(rg["startIndex"])
                    t["rowCount"] -= 1
                else:
                    for row in t["rows"]:
                        if rg["startIndex"] < len(row):
                            row.pop(rg["startIndex"])
                    t["colCount"] -= 1
            elif kind == "appendDimension":
                t = tab(body["sheetId"])
                t["rowCount" if body["dimension"] == "ROWS" else "colCount"] += body["length"]
            elif kind == "addSheet":
                p = body["properties"]
                assert all(t["title"] != p["title"] for t in tabs)
                tabs.append({"id": p["sheetId"], "title": p["title"], "rows": [], "rowCount": 1000, "colCount": 26})
            elif kind == "updateSheetProperties":
                tab(body["properties"]["sheetId"])["title"] = body["properties"]["title"]
            elif kind == "deleteSheet":
                tabs.remove(tab(body["sheetId"]))
            else:
                raise AssertionError(kind)
        self.meta[fid]["rev"] += 1

    def rows(self, fid, title=None):
        tabs = self.sheets[fid]
        t = next(t for t in tabs if t["title"] == title) if title else tabs[0]
        return [[v for v in r] for r in t["rows"]]

    def other_user_inserts_row(self, fid, at, row, title=None):
        tabs = self.sheets[fid]
        t = next(t for t in tabs if t["title"] == title) if title else tabs[0]
        t["rows"].insert(at, list(row))
        self.meta[fid]["rev"] += 1


ENZYMES = [["효소 재고"], ["Name", "Cat#", "Qty", "Location"],
           ["EcoRI", "R0101", 2, "-20 A"], ["BamHI", "R0136", 1, "-20 A"]]


@pytest.fixture
def remote(tmp_path):
    (tmp_path / "remote").mkdir()
    r = FakeSheets(tmp_path / "remote")
    r.gid = r.add_sheet_file("Enzyme", [("NEB", ENZYMES), ("Thermo", [["Name", "Qty"], ["XhoI", 3]])])
    r.add("Antibody.xlsx", make_xlsx(tmp_path / "a.xlsx", [["Name", "Vials"], ["Anti-GFP", 3]]))
    return r


def open_model(remote, tmp_path, tag="a"):
    m = FolderModel(remote, tmp_path / f"cache-{tag}")
    m.load()
    project = m.build(tmp_path / f"cache-{tag}" / "session.labproj")
    return m, project


def _cat(project, name):
    return next(c for c in project.categories() if c.name == name)


def _item(project, cat, name):
    nf = project.field_by_role(cat.id, "name")
    return next(i for i in project.items(cat.id) if i.get(nf.id) == name)


def test_google_sheet_is_read_like_an_excel_file(remote, tmp_path):
    m, project = open_model(remote, tmp_path)
    names = sorted(c.name for c in project.categories())
    assert names == ["Antibody", "Enzyme"]  # the sheet's own name, no extension
    cat = _cat(project, "Enzyme")
    assert project.subgroups(cat.id) == ["NEB", "Thermo"]
    assert project.count_items(cat.id) == 3
    assert project.get_meta(f"source:{cat.id}") == "gsheet"
    assert project.get_meta(f"source:{_cat(project, 'Antibody').id}") is None


def test_value_edit_goes_to_the_one_cell(remote, tmp_path):
    m, project = open_model(remote, tmp_path)
    cat = _cat(project, "Enzyme")
    qty = project.field_by_role(cat.id, "quantity") or next(f for f in project.fields(cat.id) if f.name == "Qty")
    item = _item(project, cat, "BamHI")
    project.set_value(item.id, qty.id, "5")
    m.flush()
    assert remote.whole_uploads == []
    assert remote.rows(remote.gid)[3][:3] == ["BamHI", "R0136", 5]
    (req,), = remote.batches
    assert req["updateCells"]["start"] == {"sheetId": 0, "rowIndex": 3, "columnIndex": 2}


def test_edit_finds_its_row_after_someone_else_inserted_one(remote, tmp_path):
    m, project = open_model(remote, tmp_path)
    cat = _cat(project, "Enzyme")
    loc = next(f for f in project.fields(cat.id) if f.name == "Location")
    item = _item(project, cat, "BamHI")
    remote.other_user_inserts_row(remote.gid, 2, ["NotI", "R0189", 4, "-20 B"])
    project.set_value(item.id, loc.id, "-80 C")
    res = m.flush()
    rows = remote.rows(remote.gid)
    assert rows[4][0] == "BamHI" and rows[4][3] == "-80 C"
    assert rows[2][0] == "NotI" and rows[2][3] == "-20 B"  # their row is untouched
    assert res.remote_changed
    # the cached copy now holds both edits
    m.build(tmp_path / "cache-a" / "again.labproj")
    assert project is not m.project
    assert m.project.count_items(_cat(m.project, "Enzyme").id) == 4


def test_rows_columns_and_sheets(remote, tmp_path):
    m, project = open_model(remote, tmp_path)
    cat = _cat(project, "Enzyme")
    nf = project.field_by_role(cat.id, "name")
    project.add_item(cat.id, {nf.id: "HindIII"}, subgroup="NEB")
    project.delete_items([_item(project, cat, "EcoRI").id])
    f = project.add_field(cat.id, "Lot")
    project.add_subgroup(cat.id, "Takara")
    project.rename_subgroup(cat.id, "Thermo", "Thermo Fisher")
    m.flush()
    titles = [t["title"] for t in remote.sheets[remote.gid]]
    assert titles == ["NEB", "Thermo Fisher", "Takara"]
    neb = remote.rows(remote.gid, "NEB")
    assert [r[0] for r in neb[2:] if r] == ["BamHI", "HindIII"]
    assert neb[1][4] == "Lot"
    assert remote.rows(remote.gid, "Takara")[0][:2] == ["Name", "Cat#"]
    project.delete_field(f.id)
    project.delete_subgroup(cat.id, "Takara")
    m.flush()
    assert [t["title"] for t in remote.sheets[remote.gid]] == ["NEB", "Thermo Fisher"]
    assert len([c for c in remote.rows(remote.gid, "NEB")[1] if c]) == 4
    assert remote.whole_uploads == []


def test_excel_files_still_upload_as_before(remote, tmp_path):
    m, project = open_model(remote, tmp_path)
    cat = _cat(project, "Antibody")
    vials = next(f for f in project.fields(cat.id) if f.name == "Vials")
    project.set_value(_item(project, cat, "Anti-GFP").id, vials.id, "7")
    m.flush()
    assert remote.batches == []
    assert remote.meta["f2"]["rev"] == 2


def test_sync_reads_a_sheet_again_only_when_it_changed(remote, tmp_path):
    m, project = open_model(remote, tmp_path)
    assert m.refresh_changed() is False
    remote.other_user_inserts_row(remote.gid, 4, ["KpnI", "R0142", 2, "-20 A"])
    assert m.refresh_changed() is True
    m.build(tmp_path / "cache-a" / "b.labproj")
    assert m.project.count_items(_cat(m.project, "Enzyme").id) == 4


def test_plan_grows_the_grid_and_keeps_numbers():
    meta = {"S": SheetMeta(7, 2, 2)}
    reqs, res = plan_requests({"S": [["a", "b"]]}, meta, [{"op": "set", "sheet": "S", "row": 4, "col": 3, "value": "12"}])
    kinds = [next(iter(r)) for r in reqs]
    assert kinds == ["appendDimension", "appendDimension", "updateCells"]
    assert reqs[-1]["updateCells"]["rows"][0]["values"][0] == {"userEnteredValue": {"numberValue": 12}}
    assert meta["S"].rows == 2  # the caller's meta is not changed
    assert cell_data("") == {} and cell_data("R0101") == {"userEnteredValue": {"stringValue": "R0101"}}


def test_unplaceable_edit_is_reported_not_guessed():
    grid = {"S": [["Name"], ["EcoRI"], ["EcoRI"]]}  # anchor no longer unique
    reqs, res = plan_requests(grid, {"S": SheetMeta(0, 10, 5)},
                              [{"op": "set", "sheet": "S", "row": 5, "col": 1, "value": "x", "anchor": (0, "EcoRI")}])
    assert reqs == [] and res.skipped


def test_new_row_does_not_overwrite_a_row_someone_just_added(remote, tmp_path):
    m, project = open_model(remote, tmp_path)
    cat = _cat(project, "Enzyme")
    nf = project.field_by_role(cat.id, "name")
    remote.other_user_inserts_row(remote.gid, 4, ["KpnI", "R0142", 2, "-20 A"])
    project.add_item(cat.id, {nf.id: "SalI"}, subgroup="NEB")
    m.flush()
    names = [r[0] for r in remote.rows(remote.gid, "NEB")[2:] if r]
    assert sorted(names) == ["BamHI", "EcoRI", "KpnI", "SalI"]
