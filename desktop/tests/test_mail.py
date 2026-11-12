"""People in charge, the briefing mail and order requests (no mail is sent)."""
from datetime import date, datetime

from app.core.orders import LAST_DATE
from app.core.project import Project
from app.core.services.briefing_run import due
from app.core.services.mail_reports import briefing, item_line, order_requests, owners
from app.integrations import schedule_mac
from app.integrations.gmail import build_message
from test_folder import FakeFolder, make_xlsx, open_model


def _project(tmp_path):
    p = Project.create(tmp_path / "p.labproj")
    cat = p.add_category("common", "Reagents")
    name = p.add_field(cat.id, "Name", role="name")
    qty = p.add_field(cat.id, "Qty", role="quantity")
    catno = p.add_field(cat.id, "Cat#", role="catalog")
    comp = p.add_field(cat.id, "Company")
    a = p.add_item(cat.id, {name.id: "RPMI", qty.id: "0", catno.id: "LM011-51", comp.id: "Welgene"})
    b = p.add_item(cat.id, {name.id: "FBS", qty.id: "5", catno.id: "F-1", comp.id: "Gibco"})
    fd = p.add_virtual_field(cat.id, LAST_DATE, "date")
    p.set_virtual_values(fd.id, {a: "2024-03-02"})
    p.set_owner(cat.id, "홍길동", "owner@example.org")
    return p, cat, a, b


def test_briefing_lists_what_needs_ordering(tmp_path):
    p, cat, a, b = _project(tmp_path)
    assert list(owners(p)) == ["owner@example.org"]
    subject, html, text = briefing(p, "owner@example.org", date(2026, 11, 3))
    assert "주문 필요 1개" in subject
    assert "RPMI" in html and "LM011-51" in html and "2024-03-02" in html
    assert "FBS" not in text.split("주문이 필요한 항목")[-1]  # enough stock: not listed
    assert briefing(p, "nobody@example.org") is None
    p.close()


def test_order_request_table_goes_to_the_owner(tmp_path):
    p, cat, a, b = _project(tmp_path)
    lines = [item_line(p, cat, p.item(i)) for i in (a, b)]
    lines[0].request_qty, lines[1].request_qty = "2", "1 box"
    reqs = order_requests(lines, "요청자A", "급함", date(2026, 11, 3))
    subject, html, text = reqs["owner@example.org"]
    assert "주문 요청 2건" in subject
    for word in ("RPMI", "Welgene", "LM011-51", "2024-03-02", "1 box", "기록 없음", "급함"):
        assert word in html
    msg = build_message(["owner@example.org"], subject, html, text)
    assert msg["To"] == "owner@example.org" and msg.is_multipart()
    p.close()


def test_owner_and_schedule_are_kept_in_the_folder_settings(tmp_path):
    (tmp_path / "remote").mkdir()
    folder = FakeFolder(tmp_path / "remote")
    folder.add("Enzyme.xlsx", make_xlsx(tmp_path / "s.xlsx", [["Name", "Qty"], ["Taq", 2]]))
    m, p = open_model(folder, tmp_path)
    cid = p.categories()[0].id
    p.set_owner(cid, "홍길동", "owner@example.org")
    p.set_briefing({"enabled": True, "time": "08:30", "weekdays": [0, 2]})
    m.flush()
    p.close()
    m2, p2 = open_model(folder, tmp_path, "b")
    assert p2.owner(p2.categories()[0].id) == {"name": "홍길동", "email": "owner@example.org"}
    assert p2.briefing()["time"] == "08:30"
    p2.close()


def test_schedule_rules():
    cfg = {"enabled": True, "time": "09:00", "weekdays": [0, 1, 2, 3, 4]}
    assert due(cfg, datetime(2026, 11, 2, 9, 0))           # Monday
    assert not due(cfg, datetime(2026, 11, 8, 9, 0))       # Sunday
    assert not due(cfg | {"last_sent": "2026-11-02"}, datetime(2026, 11, 2, 9, 5))
    assert not due(cfg | {"enabled": False}, datetime(2026, 11, 2, 9, 0))
    pl = schedule_mac.plist("08:30", [0, 6])
    assert pl["StartCalendarInterval"] == [{"Hour": 8, "Minute": 30, "Weekday": 1},
                                           {"Hour": 8, "Minute": 30, "Weekday": 0}]
    assert pl["ProgramArguments"][-1] == "--briefing"


def test_members_and_notices_live_in_the_drive_folder(tmp_path):
    from app.core.notices import NOTICE_NAME, Notice, NoticeBoard
    (tmp_path / "remote").mkdir()
    folder = FakeFolder(tmp_path / "remote")
    folder.add("Enzyme.xlsx", make_xlsx(tmp_path / "s.xlsx", [["Name", "Qty"], ["Taq", 2]]))
    m, p = open_model(folder, tmp_path)
    p.set_members([{"name": "홍길동", "email": "a@example.org"}, {"name": "", "email": ""}])
    p.set_setting("calendar", {"id": "cal1", "name": "Lab"})
    m.flush()
    board = NoticeBoard(folder, tmp_path / "nb")
    (tmp_path / "nb").mkdir()
    board.upsert(Notice(title="액체질소 배송", body="화요일 오전", author="홍길동"))
    board.upsert(Notice(title="중요 공지", pinned=True, date="2020-01-01 00:00"))
    assert board.file_id == "" and len(board.notices) == 2  # kept here until the sync
    board.sync()
    p.close()
    m2, p2 = open_model(folder, tmp_path, "b")
    assert [c.name for c in p2.categories()] == ["Enzyme"]  # the notice file is not a category
    assert p2.members() == [{"name": "홍길동", "email": "a@example.org"}]
    assert p2.setting("calendar")["id"] == "cal1"
    b2 = NoticeBoard(folder, tmp_path / "nb", m2.notice_id)
    titles = [n.title for n in b2.load()]
    assert titles == ["중요 공지", "액체질소 배송"]  # pinned first
    b2.delete(b2.notices[1].id)
    assert [n.title for n in b2.notices] == ["중요 공지"]  # at once, before any upload
    # meanwhile someone else adds a notice: our queued delete is replayed on top of theirs
    other = NoticeBoard(folder, tmp_path / "other", m2.notice_id)
    (tmp_path / "other").mkdir()
    other.load()
    other.upsert(Notice(title="다른 사람 공지"))
    other.sync()
    b2.sync()
    assert sorted(n.title for n in NoticeBoard(folder, tmp_path / "chk", m2.notice_id).load()) == ["다른 사람 공지", "중요 공지"]
    assert sorted(n.title for n in b2.notices) == ["다른 사람 공지", "중요 공지"]
    assert folder.path(NOTICE_NAME).exists()
    # who has read it
    nid = b2.notices[0].id
    b2.set_read(nid, "홍길동")
    b2.set_read(nid, "김다은")
    b2.set_read(nid, "홍길동", False)
    b2.sync()
    assert NoticeBoard(folder, tmp_path / "nb", b2.file_id).load()[0].readers == ["김다은"]
    from app.core.notices import newest
    assert [n.title for n in newest(b2.notices + [Notice(title="새것", date="2999-01-01 00:00")], 1)] == ["새것"]  # by date, not pinned
    p2.close()


def test_event_alerts():
    from datetime import timedelta, timezone
    from app.core.services.reminders import alert_mail, due_alerts, recipients
    from app.integrations.gcalendar import Event, from_api, to_api
    now = datetime(2026, 11, 3, 9, 0, tzinfo=timezone.utc)
    soon = Event("e1", "랩미팅", now + timedelta(minutes=20), now + timedelta(hours=1), assignee="all", notify_min=30)
    later = Event("e2", "세미나", now + timedelta(hours=5), assignee="a@x.org", notify_min=60)
    silent = Event("e3", "기본", now + timedelta(minutes=5), assignee="a@x.org")  # no alert by default
    sent = Event("e4", "보냄", now + timedelta(minutes=5), assignee="a@x.org", notify_min=10,
                 notified=(now + timedelta(minutes=5)).isoformat())
    assert [e.id for e in due_alerts([soon, later, silent, sent], now)] == ["e1"]
    members = [{"name": "A", "email": "a@x.org"}, {"name": "B", "email": ""}]
    assert recipients("all", members) == ["a@x.org"] and recipients("", members) == []
    assert "랩미팅" in alert_mail(soon)[0]
    back = from_api({"id": "e1", **to_api(soon)})
    assert (back.assignee, back.notify_min, back.title) == ("all", 30, "랩미팅")
    allday = from_api({"id": "e5", **to_api(Event(title="휴일", start=date(2026, 11, 5), end=date(2026, 11, 5), all_day=True))})
    assert allday.all_day and allday.end == date(2026, 11, 5)


def test_several_calendars():
    from app.integrations import gcalendar
    old = {"id": "a", "name": "Lab"}
    assert [c["id"] for c in gcalendar.selected_calendars(old)] == ["a"]
    new = {"calendars": [{"id": "a", "name": "Lab", "writable": False}, {"id": "b", "name": "Me", "writable": True}]}
    assert gcalendar.default_calendar(new) == "b"
    assert gcalendar.default_calendar(new | {"default": "a"}) == "a"
    assert gcalendar.selected_calendars(None) == []
