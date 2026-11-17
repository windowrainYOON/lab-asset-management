"""일정표 이미지 가져오기: grid finding, time repair, name matching, review dialog.

No OCR engine is needed: the OCR output is written by hand as boxes (with the kind of
slips a real engine makes), on a picture drawn here. Names are made up."""
from __future__ import annotations

from datetime import date, datetime, time

import pytest
from PIL import Image, ImageDraw

from app.core import schedule_image as si

W, H, TOP = 980, 900, 30          # picture size and header height
CW, RH = 140, 174                 # cell width, week-row height (5 rows)
MEMBERS = [{"name": n, "email": f"{i}@lab.test"} for i, n in enumerate(["홍길동", "김철수", "이영희", "박민수"])]


def picture(lines: bool = True) -> Image.Image:
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, TOP - 4, W, TOP], fill="#333333")  # thick line under the weekday header
    if lines:
        for c in range(8):
            d.line([c * CW, TOP, c * CW, TOP + 5 * RH], fill="#c0c0c0", width=2)
        for r in range(6):
            d.line([0, TOP + r * RH, W, TOP + r * RH], fill="#c0c0c0", width=2)
    return img


def cell_box(r: int, c: int, line: int, text: str, dx: int = 30) -> si.Box:
    x0 = c * CW + dx
    y0 = TOP + r * RH + 40 + line * 20
    return si.Box(x0, y0, x0 + 80, y0 + 14, text)


def header() -> list[si.Box]:
    return [si.Box(c * CW + 50, 6, c * CW + 90, 22, f"{ch}요일") for c, ch in enumerate("일월화수목금토")]


def day_numbers(year=2026, month=11) -> list[si.Box]:
    out = []
    first = date(year, month, 1)
    col1 = (first.weekday() + 1) % 7   # Sunday-first grid
    for day in range(1, 31):
        i = col1 + day - 1
        r, c = divmod(i, 7)
        x0 = c * CW + CW - 30
        y0 = TOP + r * RH + 6
        out.append(si.Box(x0, y0, x0 + 18, y0 + 14, f"{day:02d}"))
    return out


def sample_boxes() -> list[si.Box]:
    # Nov 2026: the 1st is a Sunday (row 0, col 0). The 2nd = (0, 1), the 10th = (1, 2), …
    return header() + day_numbers() + [
        cell_box(0, 1, 0, "오후200~"), cell_box(0, 1, 1, "홍길돔"),            # 2nd 14:00 홍길동 (misread)
        cell_box(0, 5, 0, "오전9.00~"), cell_box(0, 5, 1, "랩미팅C"),
        si.Box(5 * CW + 20, TOP + 80, 5 * CW + 120, TOP + 94, "(ABC 32013)", blue=True),  # 6th + place
        cell_box(1, 2, 0, "오전71:0O~"), cell_box(1, 2, 1, "운영희"),            # 10th 11:00 이영희
        cell_box(1, 2, 2, "오후12.30~"), cell_box(1, 2, 3, "저널클럽"),
        cell_box(2, 3, 0, "오후1:00~"), cell_box(2, 3, 1, "Endosome 팀"),        # 18th 13:00 team
        cell_box(3, 1, 0, "오후3.00~"), cell_box(3, 1, 1, "최가나"),             # 23rd: not a member
    ]


# ---- small parts ----------------------------------------------------------------------------
@pytest.mark.parametrize("text,start,end,rest", [
    ("오전11:00~", time(11), None, ""),
    ("오전71:0O ~", time(11), None, ""),
    ("오후200~", time(14), None, ""),
    ("오후12.30~", time(12, 30), None, ""),
    ("오후 2:00~3:30", time(14), time(15, 30), ""),
    ("오전9-00 ~ 홍길동", time(9), None, "홍길동"),
    ("오전11-C0~", time(11), None, ""),
    ("2:00~", time(14), None, ""),
    ("PM 1:15~", time(13, 15), None, ""),
])
def test_times_are_repaired(text, start, end, rest):
    got = si.parse_time(text)
    assert got is not None and got[0] == start and got[1] == end and got[2] == rest


@pytest.mark.parametrize("text", ["홍길동", "Exosome 팀", "(ABC 3201호)", "17", "저널클럽"])
def test_non_times(text):
    assert si.parse_time(text) is None


@pytest.mark.parametrize("raw,clean", [("(BOIC 34013)", "BOIC 3401호"), ("(PBC 1793)", "PBC 179호"),
                                       ("(PBC 179로)", "PBC 179호"), ("(본관 101호)", "본관 101호"),
                                       ("세미나실", "세미나실")])
def test_locations(raw, clean):
    assert si.clean_location(raw) == clean


def test_similar_hangul_matches_but_endings_must_agree():
    names = [m["name"] for m in MEMBERS]
    assert si.best_match("홍길돔", names)[0] == "홍길동"
    assert si.best_match("운영희", names)[0] == "이영희" or si.best_match("운영희", names)[0] == ""
    assert si.best_match("김철슈", names)[0] == "김철수"
    assert si.best_match("완전히다른말", names)[0] == ""
    # a known '랩미팅C' lends its stem to '캠미팅A' but never turns it into C
    assert si.best_match("캠미팅A", ["랩미팅C"])[0] == "랩미팅A"
    assert si.best_match("랩미팅A", ["랩미팅C"])[0] == "랩미팅A"


# ---- the grid ---------------------------------------------------------------------------------
def test_grid_from_cell_lines():
    img = picture()
    g = si.find_grid(img, header() + day_numbers())
    assert g is not None and g.source == "lines"
    assert len(g.xs) == 8 and len(g.ys) == 6
    assert g.weekdays[0] == 6  # Sunday first
    assert g.cell_of(1 * CW + 50, TOP + 1 * RH + 60) == (1, 1)


def test_grid_from_text_when_no_lines():
    img = picture(lines=False)
    g = si.find_grid(img, header() + day_numbers())
    assert g is not None and g.source == "text"
    assert g.cell_of(2 * CW + 50, TOP + 1 * RH + 60) == (1, 2)


def test_month_is_guessed_from_where_the_first_falls():
    img = picture()
    boxes = header() + day_numbers()
    g = si.find_grid(img, boxes)
    assert si.guess_month(g, boxes, today=date(2026, 11, 16)) == (2026, 11)
    assert si.cell_date(g, 2026, 11, 1, 2) == date(2026, 11, 10)


# ---- reading the picture ----------------------------------------------------------------------------
def test_build_reads_entries_people_places_and_teams():
    img = picture()
    cands, info = si.build(img, sample_boxes(), MEMBERS, dict(si.DEFAULTS), titles=["저널클럽"])
    assert info["month"] == 11
    by = {(c.day.day, c.start): c for c in cands}
    assert len(cands) == 6
    a = by[(2, time(14))]
    assert (a.title, a.assignee_name, a.assignee, a.end) == ("발표", "홍길동", "0@lab.test", time(15))
    assert a.note  # says it corrected the name
    b = by[(6, time(9))]
    assert b.title == "랩미팅C" and b.location == "ABC 3201호" and b.status == "check"  # first time seen
    assert by[(10, time(12, 30))].title == "저널클럽" and by[(10, time(12, 30))].status == "new"
    t = by[(18, time(13))]
    assert (t.title, t.assignee_name, t.assignee) == ("발표", "Endosome 팀", "")
    s = by[(23, time(15))]
    assert s.assignee_name == "최가나" and s.status == "check"
    assert all(c.rect for c in cands)


def test_learned_fixes_are_used_next_time():
    img = picture()
    cands, _ = si.build(img, sample_boxes(), MEMBERS, dict(si.DEFAULTS))
    # the user fixes two rows in the review sheet
    for c in cands:
        if c.raw_name == "최가나":
            c.assignee_name = "최가나"
        if c.raw_name == "랩미팅C":
            c.title = "랩미팅C"
    opts = si.learn(dict(si.DEFAULTS), cands)
    assert "랩미팅C" in opts["titles"] and "저널클럽" in opts["titles"]
    again, _ = si.build(img, sample_boxes(), MEMBERS, opts)
    assert not [c for c in again if c.status == "check" and c.raw_name != "최가나"]


def test_duplicates_are_switched_off():
    from app.integrations.gcalendar import Event
    img = picture()
    have = [Event(title="저널클럽", start=datetime(2026, 11, 10, 12, 30).astimezone())]
    cands, _ = si.build(img, sample_boxes(), MEMBERS, dict(si.DEFAULTS), existing=have)
    dup = next(c for c in cands if c.day.day == 10 and c.start == time(12, 30))
    assert dup.status == "dup" and not dup.include


def test_blue_text_is_found():
    img = Image.new("RGB", (200, 40), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, 90, 30], fill=(30, 100, 210))
    d.rectangle([110, 10, 190, 30], fill=(20, 20, 20))
    a, b = si.Box(10, 10, 90, 30, "(x)"), si.Box(110, 10, 190, 30, "y")
    si.mark_blue(img, [a, b])
    assert a.blue and not b.blue


def test_no_grid_gives_a_message():
    img = Image.new("RGB", (400, 300), "white")
    cands, info = si.build(img, [si.Box(10, 10, 60, 20, "안녕")], MEMBERS, dict(si.DEFAULTS))
    assert not cands and "칸" in info["error"]


# ---- the review sheet -------------------------------------------------------------------------------
def test_review_dialog_edits_and_result(qapp=None):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from app.ui import schedule_import as ui

    QApplication.instance() or QApplication([])
    img = picture()
    cands, info = si.build(img, sample_boxes(), MEMBERS, dict(si.DEFAULTS), titles=["저널클럽"])
    dlg = ui.ScheduleImportDialog(None, img, cands, info, MEMBERS, [{"id": "cal1", "name": "연구실"}], "cal1",
                                  dict(si.DEFAULTS), "11월.png", 3)
    assert dlg.table.rowCount() == 6 and dlg.add_btn.text() == "6개 추가"
    assert "11월.png" in dlg.summary.text() and "확인" in dlg.summary.text()
    # switch one row off, fix a name and a time
    dlg.table.item(0, ui.C_ON).setCheckState(Qt.Unchecked)
    assert dlg.add_btn.text() == "5개 추가"
    r = next(i for i, c in enumerate(dlg.cands) if c.raw_name == "최가나")
    dlg.table.cellWidget(r, ui.C_WHO).setCurrentText("박민수")
    dlg.table.item(r, ui.C_TIME).setText("15:30–17:00")
    assert dlg.cands[r].status == "edited"
    dlg.table.selectRow(r)
    assert dlg.source.rect_px == dlg.cands[r].rect
    dlg._accept()
    rows = dlg.result_candidates()
    assert len(rows) == 5
    fixed = next(c for c in rows if c.raw_name == "최가나")
    assert (fixed.assignee, fixed.start, fixed.end) == ("3@lab.test", time(15, 30), time(17))
    assert dlg.calendar_id() == "cal1"


def test_parse_helpers():
    from app.ui import schedule_import as ui
    assert ui.parse_day("11/5 (목)", 2026) == date(2026, 11, 5)
    assert ui.parse_day("오늘", 2026) is None
    assert ui.parse_span("09:00–10:30") == (time(9), time(10, 30))
    assert ui.parse_span("종일") == (None, None)
    assert ui.parse_span("아홉시") is None


def test_a_lost_leading_one_in_the_morning_is_fixed_and_flagged():
    img = picture()
    boxes = header() + day_numbers() + [cell_box(1, 2, 0, "오전1:00~"), cell_box(1, 2, 1, "홍길동")]
    cands, _ = si.build(img, boxes, MEMBERS, dict(si.DEFAULTS))
    (c,) = cands
    assert c.start == time(11) and c.end == time(12) and c.status == "check" and "확인" in c.note
