"""Location sets, 장비 예약, 주문 요청, 예산 (lab fee rounds)."""
from __future__ import annotations

import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from app.core import budget as B
from app.core.locations import LocationSet, detect
from app.core.order_requests import OrderRequest, OrderRequestBoard
from app.core.reservations import Equipment, Reservation, ReservationBoard
from app.integrations.gdrive.drive import FOLDER_MIME, RemoteFile


# ---- a Drive stand-in -----------------------------------------------------------------------
class FakeClient:
    def __init__(self, root: Path):
        self.root = root
        self.ids: dict[str, Path] = {}
        self.rev: dict[str, int] = {}
        self._id(root)

    def _id(self, path: Path) -> str:
        for k, v in self.ids.items():
            if v == path:
                return k
        k = f"id{len(self.ids):04d}xxxxxxxx"
        self.ids[k] = path
        return k

    def _rf(self, path: Path) -> RemoteFile:
        fid = self._id(path)
        return RemoteFile(fid, path.name, FOLDER_MIME if path.is_dir() else "application/x", str(self.rev.get(fid, 1)))

    def folder(self, fid):
        return self._rf(self.ids[fid])

    def get(self, fid):
        return self._rf(self.ids[fid])

    def list_folder(self, fid):
        return [self._rf(p) for p in sorted(self.ids[fid].iterdir())]

    def download(self, fid, dest):
        shutil.copyfile(self.ids[fid], dest)

    def is_native(self, fid):
        return False

    def upload_new(self, parent, name, src, as_sheet=False):
        dest = self.ids[parent] / name
        shutil.copyfile(src, dest)
        return self._rf(dest)

    def update(self, fid, src, name=""):
        shutil.copyfile(src, self.ids[fid])
        self.rev[fid] = self.rev.get(fid, 1) + 1
        return self._rf(self.ids[fid])

    def subfolder(self, parent, name):
        d = self.ids[parent] / name
        d.mkdir(exist_ok=True)
        return self._id(d)

    def rename(self, fid, name):
        p = self.ids[fid]
        p.rename(p.with_name(name))
        self.ids[fid] = p.with_name(name)

    def trash(self, fid):
        self.ids[fid].unlink()

    def move(self, fid, a, b):
        p = self.ids[fid]
        dest = self.ids[b] / p.name
        p.rename(dest)
        self.ids[fid] = dest


def _xlsx(path: Path, rows: list[list]) -> None:
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(path)


@pytest.fixture()
def drive_tree(tmp_path):
    root = tmp_path / "lab-manager-test-asset"
    (root / "Assets" / "Common Asset").mkdir(parents=True)
    (root / "Assets" / "Personal Asset").mkdir(parents=True)
    (root / "Assets" / "Sequence Map").mkdir(parents=True)
    (root / "Lab Management").mkdir(parents=True)
    (root / "Order History").mkdir(parents=True)
    _xlsx(root / "Assets" / "Common Asset" / "Reagents.xlsx", [["Name", "Qty"], ["Tris", 3]])
    _xlsx(root / "Assets" / "Personal Asset" / "홍길동 개인 자산.xlsx", [["이름", "수량"], ["Pipette", 1]])
    _xlsx(root / "Lab Management" / "예산 입출 내역.xlsx", [["날짜", "내용", "수입", "지출", "잔액"],
                                                          ["2026.09.01", "인수인계", 100000, None, 100000]])
    _xlsx(root / "Lab Management" / "업무.xlsx", [["ID"]])
    (root / "Lab Management" / "Asset Manager 설정.json").write_text('{"version": 1, "categories": {}}', encoding="utf-8")
    return root


def test_detect_finds_each_feature(drive_tree):
    c = FakeClient(drive_tree)
    s = detect(c, c._id(drive_tree))
    assert not s.legacy
    assert s.path("common") == "Assets/Common Asset/"
    assert s.path("personal") == "Assets/Personal Asset/"
    assert s.path("sequence") == "Assets/Sequence Map/"
    assert s.path("order_history") == "Order History/"
    assert s.path("budget") == "Lab Management/예산 입출 내역.xlsx"
    assert s.path("overlay").endswith("Asset Manager 설정.json")
    assert s.path("lab") == "Lab Management/"
    assert not s.get("reservations")  # made on first save
    again = LocationSet.from_dict(s.to_dict())
    assert again.get("budget") == s.get("budget") and again.name == s.name


def test_detect_old_one_folder_layout(tmp_path):
    root = tmp_path / "old"
    root.mkdir()
    _xlsx(root / "Reagents.xlsx", [["Name"], ["Tris"]])
    c = FakeClient(root)
    s = detect(c, c._id(root))
    assert s.legacy and s.get("common") == s.root_id


def test_set_remote_presents_one_folder(drive_tree, tmp_path):
    from app.core.folder_model import FolderModel
    from app.integrations.gdrive.set_remote import LocationSetRemote
    c = FakeClient(drive_tree)
    s = detect(c, c._id(drive_tree))
    model = FolderModel(LocationSetRemote(c, s), tmp_path / "cache")
    model.load()
    names = sorted((f.folder, f.stem) for f in model.files.values())
    assert names == [("", "Reagents"), ("Personal Asset", "홍길동 개인 자산")]  # no budget / 업무 here
    assert model.overlay_id == s.get("overlay") and model.work_id == s.get("work")
    p = model.build(tmp_path / "cache" / "p.labproj", "x")
    scopes = {c.name: c.scope for c in p.categories()}
    assert scopes == {"Reagents": "common", "홍길동 개인 자산": "personal"}
    # a new personal category is made in the personal folder
    cat = p.add_category("personal", "김철수 개인 품목")
    assert (drive_tree / "Assets" / "Personal Asset" / "김철수 개인 품목.xlsx").exists()
    p.close()


def test_feature_file_created_in_lab_folder(drive_tree, tmp_path):
    from app.integrations.gdrive.set_remote import RoleFileRemote
    c = FakeClient(drive_tree)
    s = detect(c, c._id(drive_tree))
    made = []
    board = ReservationBoard(RoleFileRemote(c, s, "reservations", lambda r, f: made.append(r)), tmp_path / "c")
    board.load()
    board.save_equipment(Equipment("e1", "초원심분리기", "B204"))
    board.sync()
    assert (drive_tree / "Lab Management" / "장비 예약.xlsx").exists()
    assert s.get("reservations") and made == ["reservations"]


# ---- reservations -------------------------------------------------------------------------
def test_reservation_conflicts_and_limits(tmp_path):
    b = ReservationBoard(None, tmp_path)
    b.load()
    b.save_equipment(Equipment("e1", "초원심분리기", max_hours=4))
    b.save_equipment(Equipment("e2", "FACS", state="점검 중"))
    t = datetime(2026, 11, 11, 9, 0)
    r1 = Reservation("r1", "e1", "", "홍길동", t, t + timedelta(hours=2))
    assert b.check(r1) == ""
    b.save_reservation(r1)
    r2 = Reservation("r2", "e1", "", "박민서", t + timedelta(hours=1), t + timedelta(hours=3))
    assert "겹칩니다" in b.check(r2)
    r3 = Reservation("r3", "e1", "", "박민서", t + timedelta(hours=2), t + timedelta(hours=7))
    assert "최대 4시간" in b.check(r3)
    assert "점검 중" in b.check(Reservation("r4", "e2", "", "a", t, t + timedelta(hours=1)))
    b2 = ReservationBoard(None, tmp_path)  # read back from the file
    b2.load()
    assert [r.person for r in b2.on_day(t.date())] == ["홍길동"]
    assert b2.on_day(t.date(), "박민서") == []
    assert b2.calendar_events()[0].calendar_id == "reservations"


# ---- order requests -----------------------------------------------------------------------
def test_order_requests_flow(tmp_path):
    b = OrderRequestBoard(None, tmp_path)
    b.load()
    req = OrderRequest(requester="홍길동", item="Anti-GAPDH", catalog="2118S", owner="김철수", owner_email="k@x.org")
    b.save(req)
    assert b.for_owner("김철수")[0].item == "Anti-GAPDH"
    got = b.set_state(req.id, "주문함")
    assert got.ordered == date.today().isoformat()
    b.set_state(req.id, "입고됨")
    assert b.for_owner("김철수") == []  # done: not in the person's open list
    b2 = OrderRequestBoard(None, tmp_path)
    b2.load()
    assert b2.get(req.id).state == "입고됨"
    from app.core.order_requests import request_mail, state_mail
    subj, html, text = request_mail([req])
    assert "Anti-GAPDH" in html and "홍길동" in subj
    assert "입고됨" in state_mail(b2.get(req.id))[0]


# ---- budget ---------------------------------------------------------------------------------
SEP_PEOPLE = [("Younghee Lee", "이영희", 12760), ("Haneul Lee", "이하늘", 24300), ("Seoyun Jo", "조서윤", 12760),
              ("Dohyun Kim", "김도현", 12760), ("Cheolsu Kim", "김철수", 0), ("Gaon Jeong", "정가온", 0),
              ("Minseo Park", "박민서", 0), ("Yerin Kang", "강예린", 12760), ("Gildong Hong", "홍길동", 0),
              ("Jimin Kim", "김지민", 12760), ("Daon Park", "박다온", 0)]


def test_lab_fee_matches_september_sheet():
    rnd = B.FeeRound("2026.09", "2026-09-28", 300_000, 87_071, 11,
                     [B.FeeLine(en, ko, c) for en, ko, c in SEP_PEOPLE])
    assert rnd.carry_total == 88_100
    assert rnd.due == 124_829
    assert rnd.fee == 11_350
    assert rnd.total(rnd.lines[1]) == 35_650


def test_budget_ledger_round_trip_and_new_round(tmp_path):
    path = tmp_path / "예산 입출 내역.xlsx"
    _xlsx(path, [["날짜", "내용", "수입", "지출", "잔액", None],
                 ["2026.09.26", "입출금통장 이자", 7, None, 62771, None],
                 ["2026.09.28", "정가온 랩비", 24300, None, 87071, None]])
    book = B.BudgetBook(None, tmp_path)
    book.file.path = path
    book.load()
    assert book.balance() == 87_071
    rnd = B.FeeRound("2026.09", "2026-09-28", 300_000, 87_071, 11, [B.FeeLine(en, ko, c) for en, ko, c in SEP_PEOPLE])
    book.save_round(rnd)
    book.add_entry(B.Entry("2026.10.02", "홍길동 랩비", 11_350))
    book.add_entry(B.Entry("2026.10.03", "이하늘 랩비", 20_000))
    book.add_entry(B.Entry("2026.10.05", "종이컵", None, 5_000))
    # read back from the Excel file
    again = B.read_budget(path)
    assert [e["desc"] for e in again["ledger"]][-3:] == ["홍길동 랩비", "이하늘 랩비", "종이컵"]
    assert B.current_balance(again) == 87_071 + 11_350 + 20_000 - 5_000
    assert B.rounds(again)[0].fee == 11_350
    nxt = B.new_round(again, "2026.11", [(en, ko) for en, ko, _ in SEP_PEOPLE], today=date(2026, 11, 9))
    by = {ln.korean: ln for ln in nxt.lines}
    assert by["홍길동"].carryover == 0                     # paid in full
    assert by["이하늘"].carryover == 24_300 + 11_350 - 20_000  # partly paid
    assert by["이영희"].carryover == 12_760 + 11_350        # nothing paid
    assert by["정가온"].carryover == 0  # the 09.28 deposit on the round date counts
    by["이영희"].carryover, by["이영희"].manual = 0, True     # cash, marked by hand
    B.fill_auto(again, nxt)
    assert by["이영희"].carryover == 0 and by["이영희"].auto == 12_760 + 11_350


# ---- 통합 검색 over records, 운영 브리핑 per person ----------------------------------------
def test_record_search_and_ops_briefing(tmp_path):
    from app.core.services.ops_briefing import ops_mail, sections
    from app.core.services.record_search import search_records
    from app.core.work import WorkBoard, WorkProject, WorkTask
    from app.integrations.gcalendar import Event

    res = ReservationBoard(None, tmp_path)
    res.load()
    res.save_equipment(Equipment("e1", "초원심분리기", "B204"))
    t = datetime.combine(date(2026, 11, 11), datetime.min.time()).replace(hour=10)
    res.save_reservation(Reservation("r1", "e1", "", "홍길동", t, t + timedelta(hours=1), "pellet"))
    res.save_reservation(Reservation("r2", "e1", "", "박민서", t + timedelta(hours=2), t + timedelta(hours=3)))
    reqs = OrderRequestBoard(None, tmp_path)
    reqs.load()
    reqs.save(OrderRequest(requester="박민서", item="Anti-GAPDH", catalog="2118S", owner="홍길동"))
    reqs.save(OrderRequest(requester="홍길동", item="Tips", owner="김철수"))
    work = WorkBoard(None, tmp_path, local=tmp_path / "w.xlsx")
    work.projects = [WorkProject(id="p1", name="청소", members=["홍길동", "박민서"])]
    work.tasks = [WorkTask(id="t1", project_id="p1", date="2026-11-12", assignee="홍길동"),
                  WorkTask(id="t2", project_id="p1", date="2026-11-11", assignee="박민서")]
    ev = [Event(id="g1", title="랩미팅", start=t, end=t + timedelta(hours=1), assignee="all"),
          Event(id="g2", title="세미나", start=t, end=t, assignee="p@x.org", assignee_name="박민서")]
    src = {"reservations": res, "order_requests": reqs, "work": work, "events": ev}

    hits = search_records(src, "GAPDH")
    assert [h.kind for h in hits] == ["order_request"] and hits[0].ref
    assert {h.kind for h in search_records(src, "초원심분리기")} == {"equipment", "reservation"}
    assert search_records(src, "cat:x GAPDH") == []  # inventory filters: records left out

    me = {"name": "홍길동", "email": "y@x.org"}
    got = {title: rows for title, _, rows in sections(me, today=date(2026, 11, 11), **src)}
    assert [r[0] for r in got["오늘 내 장비 예약"]] == ["초원심분리기"]
    assert len(got["내 당번·업무 (이번 주)"]) == 1
    assert [r[1] for r in got["오늘 내 일정"]] == ["랩미팅"]
    assert [r[1] for r in got["처리할 주문 (장바구니 요청)"]] == ["Anti-GAPDH"]
    nobody = {"name": "강예린", "email": "k@x.org"}
    assert [t for t, _, _ in sections(nobody, today=date(2026, 11, 11), **src)] == ["오늘 내 일정"]
    assert ops_mail({"name": "강예린", "email": "k@x.org"}, today=date(2026, 11, 11), reservations=res) is None


def test_window_pages_search_and_personal_classes(tmp_path, monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    from app.core import settings
    from app.core.project import Project
    monkeypatch.setattr(settings, "_settings_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr(settings, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(settings, "remember_project", lambda *_: None)
    from app.ui.dialogs.category_dialog import PERSONAL_FIELDS
    from app.ui.main_window import MainWindow
    proj = Project.create(tmp_path / "t.labproj", "t")
    cat = proj.add_category("personal", "홍길동 개인 자산")
    for name, ftype, role in PERSONAL_FIELDS:
        proj.add_field(cat.id, name, ftype, role)
    a = proj.add_item(cat.id, {})
    b = proj.add_item(cat.id, {})
    name_f = proj.field_by_role(cat.id, "name")
    proj.set_value(a, name_f.id, "Pipette P20")
    proj.set_value(b, name_f.id, "Anti-GAPDH")
    win = MainWindow(proj)
    for key in ("reservations", "orders", "budget"):
        win.open_tab(key)
        app.processEvents()
    win.show_category(cat.id)
    page = win.category_page
    assert page.class_field() is not None and not page.class_box.isVisible()
    page.table.selectRow(0)
    page.set_class_selected("피펫")
    assert page.class_values() == ["피펫"]
    page._class_picked("피펫")
    assert page.proxy.rowCount() == 1
    page._class_picked(None)
    assert page.proxy.rowCount() == 2
    win.show_dashboard()
    win.search_edit.setText("GAPDH")
    win.run_search()
    assert win.stack.currentWidget() is win.search_page
    assert win.search_page.tree.topLevelItemCount() == 1
    win.close()
