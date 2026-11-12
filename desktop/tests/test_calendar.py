"""일정 tab: date math, event layout and the Google payload for repeating events."""
from datetime import date, datetime, timedelta

import pytest
from PySide6.QtWidgets import QApplication

from app.integrations.gcalendar import Event, to_api
from app.ui import calendar_views as cv


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_weeks_start_on_sunday():
    assert cv.week_start(date(2026, 4, 1)) == date(2026, 3, 29)  # Wed -> Sun
    assert cv.week_start(date(2026, 3, 29)) == date(2026, 3, 29)
    assert cv.month_grid_start(date(2026, 4, 15)) == date(2026, 3, 29)
    assert cv.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert cv.add_months(date(2026, 11, 3), 14) == date(2028, 1, 3)


def test_spans_and_labels():
    ad = Event(title="학회", start=date(2026, 4, 1), end=date(2026, 4, 2), all_day=True)
    assert cv.days_of(ad) == [date(2026, 4, 1), date(2026, 4, 2)]
    assert cv.is_long(ad)
    t = Event(title="랩미팅", start=datetime(2026, 4, 1, 10).astimezone(), end=datetime(2026, 4, 1, 11, 30).astimezone())
    assert not cv.is_long(t) and cv.days_of(t) == [date(2026, 4, 1)]
    assert cv.range_label(t) == "오전 10시–11:30"
    assert cv.hour_label(12) == "정오" and cv.hour_label(13) == "오후 1시"


def test_overlapping_events_share_the_column(app):
    g = cv.TimeGrid(56)
    g.resize(700, 1200)
    d = date(2026, 4, 1)
    a = Event(id="a", title="A", start=datetime(2026, 4, 1, 9).astimezone(), end=datetime(2026, 4, 1, 11).astimezone())
    b = Event(id="b", title="B", start=datetime(2026, 4, 1, 10).astimezone(), end=datetime(2026, 4, 1, 12).astimezone())
    c = Event(id="c", title="C", start=datetime(2026, 4, 1, 13).astimezone(), end=datetime(2026, 4, 1, 14).astimezone())
    g.set_days([d])
    g.set_events([a, b, c], None)
    r = {blk.ev.id: blk.rect for blk in g.blocks}
    assert r["a"].width() == pytest.approx(r["b"].width()) and r["a"].right() <= r["b"].left()
    assert r["c"].width() > r["a"].width()  # alone again: full width
    assert r["a"].top() == pytest.approx(g.y_of(datetime(2026, 4, 1, 9)) + 1)


def test_repeat_only_on_new_events():
    s = datetime(2026, 4, 1, 9).astimezone()
    ev = Event(title="랩미팅", start=s, end=s + timedelta(hours=1), repeat="weekly")
    assert to_api(ev)["recurrence"] == ["RRULE:FREQ=WEEKLY"]
    ev.id = "x"  # an existing occurrence is edited alone, never turned into a series here
    assert "recurrence" not in to_api(ev)


def test_drag_on_empty_time_creates(app):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    g = cv.TimeGrid(56)
    g.resize(700, 1200)
    g.set_days([date(2026, 4, 1)])
    got = []
    g.create.connect(lambda s, e, ad: got.append((s, e, ad)))
    y9, y10 = int(g.y_of(datetime(2026, 4, 1, 9))) + 2, int(g.y_of(datetime(2026, 4, 1, 10))) + 2
    QTest.mousePress(g, Qt.LeftButton, pos=QPoint(300, y9))
    QTest.mouseMove(g, QPoint(300, y9 + 20))
    QTest.mouseMove(g, QPoint(300, y10))
    QTest.mouseRelease(g, Qt.LeftButton, pos=QPoint(300, y10))
    assert got and got[0][0] == datetime(2026, 4, 1, 9) and got[0][1] == datetime(2026, 4, 1, 10, 15)
