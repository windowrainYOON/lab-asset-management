"""출퇴근 기록 (read only), Lab Management files as Google Sheets, cell-level writes."""
from datetime import date, datetime, time
from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook

from app.core import attendance as att
from app.core.gsheet_ops import SheetMeta, diff_requests, typed_cell, workbook_grids
from app.core.locations import LocationSet
from app.core.synced_file import SyncedFile
from app.integrations.gcalendar import Event
from app.integrations.gdrive import convert
from app.integrations.gdrive.drive import FOLDER_MIME, GSHEET_MIME, RemoteFile, sheet_name


# ---- the sheet ----------------------------------------------------------------------------
def _book(tmp_path: Path) -> Path:
    p = tmp_path / "a.xlsx"
    att.write_new_book(p, ["홍길동", "김하람"], ["2026-11"])
    wb = load_workbook(p)
    ws = wb["2026-11"]
    # rows: 1 header, then 2026-11-01 at row 2
    ws.cell(3, 3, "08:51")            # 11/2 홍길동
    ws.cell(3, 4, "09:14")            # 11/2 김하람 late
    ws.cell(4, 3, time(9, 10))        # 11/3 홍길동 exactly at the tolerance: on time
    ws.cell(4, 4, "휴가")
    ws.cell(5, 3, 0.3826)             # 11/4 a Sheets time value (09:11): late
    ws.cell(7, 4, "09:30")            # 11/6 김하람, but 11/6 is excluded below
    wb.save(p)
    return p


def test_read_book_and_rules(tmp_path):
    book = att.read_book(_book(tmp_path))
    assert book.has_settings_tab
    s = book.settings
    assert (s.start, s.grace, sorted(s.workdays)) == (time(9, 0), 10, [0, 1, 2, 3, 4])
    m = book.month("2026-11")
    assert m.people == ["홍길동", "김하람"]
    assert m.get(date(2026, 11, 2), "홍길동") == time(8, 51)
    assert m.get(date(2026, 11, 3), "김하람") == "휴가"
    assert m.get(date(2026, 11, 4), "홍길동") == time(9, 11)
    assert att.status(time(9, 10), s) == "ontime" and att.status(time(9, 11), s) == "late"
    assert s.late_by(time(9, 14)) == 14

    holidays = {}
    excluded = att.excluded_from_events([Event(title="워크숍", start=date(2026, 11, 6), end=date(2026, 11, 6),
                                               all_day=True, extra={att.EXCLUDE_KEY: "1"}),
                                         Event(title="회의", start=date(2026, 11, 5), end=date(2026, 11, 5),
                                               all_day=True, extra={att.EXCLUDE_KEY: "0"})])
    assert excluded == {date(2026, 11, 6): "워크숍"}
    today = date(2026, 11, 6)
    st = att.person_stats(m, "김하람", s, holidays, excluded, today)
    # work days 2,3,4,5 (6 excluded, 1 is a Sunday); present 2 (11/2 late); 휴가 not present
    assert (st.workdays, st.present, st.late) == (4, 1, 1)
    assert st.late_days == [(date(2026, 11, 2), time(9, 14))]
    info = att.day_info(date(2026, 11, 6), s, holidays, excluded)
    assert (info.workday, info.kind, info.reason) == (False, "event", "워크숍")
    assert att.day_info(date(2026, 11, 1), s, {}, {}).kind == "weekend"


def test_holidays_skip_observances_and_cover_every_day():
    evs = [Event(title="추석", start=date(2026, 9, 24), end=date(2026, 9, 26), all_day=True, description="공휴일"),
           Event(title="어버이날", start=date(2026, 5, 8), end=date(2026, 5, 8), all_day=True, description="기념일")]
    h = att.holidays_from_events(evs)
    assert set(h) == {date(2026, 9, 24), date(2026, 9, 25), date(2026, 9, 26)}
    timed = Event(title="세미나", start=datetime(2026, 11, 9, 14), end=datetime(2026, 11, 10, 0),
                  extra={att.EXCLUDE_KEY: "1"})
    assert att.excluded_from_events([timed]) == {date(2026, 11, 9): "세미나"}  # ends at midnight


def test_settings_requests_write_the_settings_tab():
    s = att.AttendanceSettings(time(9, 30), 5, frozenset({0, 1, 2, 3}))
    reqs = att.settings_requests({"2026-11": SheetMeta(0, 1000, 26), "설정": SheetMeta(7, 20, 4)}, s)
    (kind, body), = reqs[0].items()
    assert kind == "updateCells" and body["start"]["sheetId"] == 7
    vals = [[c.get("userEnteredValue") for c in r["values"]] for r in body["rows"]]
    assert vals[1] == [{"stringValue": "출근 시간"}, {"stringValue": "09:30"}]
    assert vals[2][1] == {"numberValue": 5}
    assert vals[3][1] == {"stringValue": "월,화,수,목"}
    # no 설정 tab yet: it is added first
    reqs = att.settings_requests({"2026-11": SheetMeta(0, 1000, 26)}, s)
    assert "addSheet" in reqs[0] and reqs[0]["addSheet"]["properties"]["title"] == "설정"
    assert reqs[1]["updateCells"]["start"]["sheetId"] == reqs[0]["addSheet"]["properties"]["sheetId"]
    assert att.parse_settings(s.rows()).workdays == s.workdays


# ---- cell-level writes for whole-state files ---------------------------------------------
class SheetsRemote:
    """A native Google Sheet held as grids; only batchUpdate changes it."""

    def __init__(self, tabs: dict[str, list[list]]):
        self.tabs = {t: {"id": i, "rows": [list(r) for r in rows], "rowCount": 50, "colCount": 8}
                     for i, (t, rows) in enumerate(tabs.items())}
        self.rev = 1
        self.batches: list[list[dict]] = []

    def is_native(self, fid):
        return True

    def revision(self, fid):
        return str(self.rev)

    def download(self, fid, dest):
        wb = Workbook()
        wb.remove(wb.active)
        for title, t in self.tabs.items():
            ws = wb.create_sheet(title)
            for r, row in enumerate(t["rows"], 1):
                for c, v in enumerate(row, 1):
                    if v not in (None, ""):
                        ws.cell(r, c, v)
        wb.save(dest)

    def update(self, fid, path):
        raise AssertionError("a Google Sheet is never uploaded whole")

    def sheet_meta(self, fid):
        return {k: SheetMeta(t["id"], t["rowCount"], t["colCount"]) for k, t in self.tabs.items()}

    def sheet_batch(self, fid, reqs):
        self.batches.append(reqs)
        by_id = lambda sid: next(t for t in self.tabs.values() if t["id"] == sid)  # noqa: E731
        for req in reqs:
            (kind, b), = req.items()
            if kind == "addSheet":
                p = b["properties"]
                self.tabs[p["title"]] = {"id": p["sheetId"], "rows": [], "rowCount": p["gridProperties"]["rowCount"],
                                         "colCount": p["gridProperties"]["columnCount"]}
            elif kind == "deleteSheet":
                self.tabs = {k: t for k, t in self.tabs.items() if t["id"] != b["sheetId"]}
            elif kind == "appendDimension":
                t = by_id(b["sheetId"])
                t["rowCount" if b["dimension"] == "ROWS" else "colCount"] += b["length"]
            elif kind == "updateCells":
                t = by_id(b["start"]["sheetId"])
                r, c = b["start"]["rowIndex"], b["start"]["columnIndex"]
                assert r < t["rowCount"] and c < t["colCount"], "outside the grid"
                uv = b["rows"][0]["values"][0].get("userEnteredValue", {})
                v = next(iter(uv.values()), None)
                while len(t["rows"]) <= r:
                    t["rows"].append([])
                while len(t["rows"][r]) <= c:
                    t["rows"][r].append(None)
                t["rows"][r][c] = v
        self.rev += 1


def _read(path):
    wb = load_workbook(path)
    return [list(r) for r in wb["목록"].iter_rows(values_only=True)]


def _write(path, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "목록"
    for r in rows:
        ws.append(r)
    wb.save(path)


def test_synced_file_sends_only_changed_cells_to_a_google_sheet(tmp_path):
    remote = SheetsRemote({"목록": [["제목", "내용", "날짜"], ["a", "x", "2026-11-01"], ["b", "y", "2026-11-02"]]})
    f = SyncedFile(remote, tmp_path / "n.xlsx", "g1", "공지사항.xlsx", _read, _write, list)
    f.load()
    f.edit(lambda rows: rows[:1] + [["a", "x 고침", "2026-11-01"]] + rows[2:] + [["c", "z", None]])
    # meanwhile someone else typed in an unrelated cell
    remote.tabs["목록"]["rows"][2].append("메모")
    remote.rev += 1
    f.sync()
    rows = remote.tabs["목록"]["rows"]
    assert rows[1][1] == "x 고침" and rows[3][:2] == ["c", "z"]
    assert rows[2][3] == "메모"  # their edit survived: nothing was uploaded whole
    sent = [r for b in remote.batches for r in b]
    assert all("updateCells" in r for r in sent) and len(sent) == 3  # one changed cell + two new
    assert not f.dirty()


def test_diff_requests_add_delete_tabs_and_grow_the_grid():
    old = {"원장": [["날짜", "금액"], ["2026-11-01", 100]], "회차 9월": [["x"]]}
    new = {"원장": [["날짜", "금액"], ["2026-11-01", 100]] + [[None, i] for i in range(60)],
           "회차 10월": [["y", True, "=A1"]]}
    meta = {"원장": SheetMeta(1, 50, 8), "회차 9월": SheetMeta(2, 50, 8)}
    reqs = diff_requests(old, new, meta)
    kinds = [next(iter(r)) for r in reqs]
    assert kinds[0] == "addSheet" and "deleteSheet" in kinds and "appendDimension" in kinds
    cells = [r["updateCells"]["rows"][0]["values"][0] for r in reqs if "updateCells" in r]
    assert {"userEnteredValue": {"boolValue": True}} in cells
    assert {"userEnteredValue": {"formulaValue": "=A1"}} in cells
    assert typed_cell(date(2026, 11, 11))["userEnteredFormat"]["numberFormat"]["type"] == "DATE"
    assert typed_cell(datetime(1899, 12, 31))["userEnteredValue"]["numberValue"] == 1


# ---- converting the lab folder ------------------------------------------------------------
class DriveFake:
    def __init__(self):
        self.files: dict[str, dict] = {}
        self.n = 0
        self.props: dict[str, dict] = {}

    def add(self, name, parent, mime=""):
        self.n += 1
        fid = f"f{self.n}"
        self.files[fid] = {"name": name, "parent": parent, "mime": mime}
        return fid

    def _rf(self, fid):
        f = self.files[fid]
        return RemoteFile(fid, f["name"], f["mime"])

    def list_folder(self, folder):
        return [self._rf(i) for i, f in self.files.items() if f["parent"] == folder]

    def get(self, fid):
        return self._rf(fid)

    def find_by_name(self, folder, name):
        return next((self._rf(i) for i, f in self.files.items() if f["parent"] == folder and f["name"] == name), None)

    def subfolder(self, parent, name):
        hit = self.find_by_name(parent, name)
        return hit.id if hit else self.add(name, parent, FOLDER_MIME)

    def convert_copy(self, fid, name, parent):
        return self._rf(self.add(name, parent, GSHEET_MIME))

    def set_app_properties(self, fid, props):
        self.props.setdefault(fid, {}).update(props)

    def app_properties(self, fid):
        return self.props.get(fid, {})

    def move(self, fid, frm, to):
        assert self.files[fid]["parent"] == frm
        self.files[fid]["parent"] = to


def test_convert_lab_folder_and_follow_on_another_mac():
    d = DriveFake()
    lab = d.add("Lab Management", "root", FOLDER_MIME)
    work = d.add("업무.xlsx", lab)
    budget = d.add("예산 입출 내역.xlsx", lab)
    d.add("README.md", lab, "text/markdown")
    d.add("Asset Manager 설정.json", lab, "application/json")
    s = LocationSet(name="lab", root_id="root")
    s.set("lab", lab, "Lab Management/")
    s.set("work", work, "Lab Management/업무.xlsx")
    s.set("budget", budget, "Lab Management/예산 입출 내역.xlsx")
    other_mac = LocationSet.from_dict(s.to_dict())

    done = convert.convert_folder(d, lab, s)
    assert [n for n, _, _ in done] == ["업무.xlsx", "예산 입출 내역.xlsx"]
    names = {f["name"]: f for f in d.files.values() if f["parent"] == lab}
    assert names["업무"]["mime"] == GSHEET_MIME and "README.md" in names and "업무.xlsx" not in names
    backup = d.find_by_name(lab, convert.BACKUP_FOLDER)
    assert {d.files[work]["parent"], d.files[budget]["parent"]} == {backup.id}  # kept, not deleted
    assert s.path("work") == "Lab Management/업무" and d.files[s.get("work")]["mime"] == GSHEET_MIME
    assert convert.convert_folder(d, lab, s) == []  # nothing left to convert

    # another Mac still points at the Excel files: it follows the mark
    sheet = d.add("출퇴근 기록", lab, GSHEET_MIME)
    assert convert.follow_replaced(d, other_mac)
    assert other_mac.get("work") == s.get("work") and other_mac.get("budget") == s.get("budget")
    assert other_mac.get("attendance") == sheet
    assert not convert.follow_replaced(d, other_mac)  # once


def test_sheet_name_and_detect_native_role_files():
    from app.core.locations import detect
    assert sheet_name("업무.xlsx") == "업무" and sheet_name("공지사항") == "공지사항"
    d = DriveFake()
    lab = d.add("Lab Management", "root", FOLDER_MIME)
    d.add("Common Asset", "root", FOLDER_MIME)
    ids = {n: d.add(n, lab, GSHEET_MIME) for n in ("업무", "공지사항", "예산 입출 내역", "출퇴근 기록")}
    d.files["root"] = {"name": "root", "parent": "", "mime": FOLDER_MIME}
    d.folder = d.get
    s = detect(d, "root")
    assert s.get("work") == ids["업무"] and s.get("notices") == ids["공지사항"]
    assert s.get("budget") == ids["예산 입출 내역"] and s.get("attendance") == ids["출퇴근 기록"]


# ---- the page ----------------------------------------------------------------------------
@pytest.fixture
def qtbot(tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication
    from app.core import settings
    monkeypatch.setattr(settings, "_settings_path", lambda: tmp_path / "settings.json")
    app = QApplication.instance() or QApplication([])

    class Bot:
        def addWidget(self, w):
            w.resize(1100, 800)
            w.show()
            app.processEvents()
    yield Bot()


class StoreStub(att.AttendanceStore):
    def __init__(self, book, fid="g9"):
        super().__init__(None, Path("."), fid)
        self.book = book
        self.saved = None

    def save_settings(self, s):
        self.saved = s
        self.book.settings = s


def test_attendance_page_shows_month_and_person(qtbot, tmp_path, monkeypatch):
    from app.ui import attendance_view as av

    monkeypatch.setattr(av.AttendancePage, "_fetch_calendar", lambda self, force=False: None)
    book = att.read_book(_book(tmp_path))
    page = av.AttendancePage()
    qtbot.addWidget(page)
    page.month_key = "2026-11"
    page.excluded = {date(2026, 11, 6): "워크숍"}
    page._cal_cache["2026-11"] = ({}, page.excluded)
    page.set_store(StoreStub(book), None, "Lab Management / 출퇴근 기록")
    t = page.table
    assert t.isVisible()
    assert [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())] == ["날짜", "홍길동", "김하람"]
    folded = [r for r in range(t.rowCount()) if t.item(r, 0).data(av.KIND_ROLE) == "fold"]
    labels = [t.item(r, 1).text() for r in folded]
    assert "워크숍 · 일정에서 출퇴근 기록 제외" in labels and "주말" in labels
    page._header_clicked(2)
    assert page.sel_person == "김하람" and page.inspector.t_late.value.text() == "1회"
    page._set_late_only("late")
    assert t.rowCount() == 2  # 11/2 (김하람) and 11/4 (홍길동)
    page.people.setCurrentIndex(page.people.findData("홍길동"))
    assert t.columnCount() == 2 and t.rowCount() == 1


def test_attendance_settings_dialog_saves(qtbot):
    from PySide6.QtCore import QTime
    from app.ui import attendance_view as av

    store = StoreStub(att.Book())
    dlg = av.AttendanceSettingsDialog(store, "Lab Management / 출퇴근 기록", 3, "2026-11")
    qtbot.addWidget(dlg)
    dlg.start.setTime(QTime(9, 30))
    dlg.grace.setValue(5)
    dlg.day_btns[4].setChecked(False)
    assert "09:35까지 출근하면 정시" in dlg.rule.text()
    import app.ui.cloud as cloud
    orig = cloud.run_blocking
    cloud.run_blocking = lambda parent, fn, msg: fn()
    try:
        dlg._save()
    finally:
        cloud.run_blocking = orig
    assert store.saved.start == time(9, 30) and store.saved.grace == 5 and 4 not in store.saved.workdays
