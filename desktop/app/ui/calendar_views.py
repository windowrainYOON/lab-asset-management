"""Calendar views for the 일정 tab, laid out like macOS Calendar and drawn with the design
system's tokens (Desktop profile, tahoe style):

    TimeView   일 / 주: day header, 종일 row, hourly grid with the current-time line
    MonthGrid  월: six weeks, events as rows inside each day
    YearGrid   년: twelve small months, days with events shaded
    MiniMonth  small month for jumping to a date
    CalendarList  the chosen Google calendars with colored checkboxes (show / hide)

Events keep their Google calendar color (user data, like Calendar.app). Every text color
on an event tint is checked against it for 4.5:1, so a light calendar color is darkened.
Content stays opaque (no glass): the views paint on `background`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..integrations.gcalendar import Event
from .theme import icons, tokens

WEEKDAYS = "일월화수목금토"   # weeks start on Sunday, like Calendar.app
SNAP_MIN = 15                 # dragging snaps to 15 minutes
EVENT_BAR = 3                 # leading color bar of a timed event (Calendar.app)


# ---- dates ------------------------------------------------------------------------
def week_start(d: date) -> date:
    return d - timedelta(days=(d.weekday() + 1) % 7)


def month_grid_start(d: date) -> date:
    return week_start(d.replace(day=1))


def add_months(d: date, n: int) -> date:
    y, m = d.year, d.month + n
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last))


def local(dt: datetime) -> datetime:
    """Naive local time (the grid works in the computer's time zone)."""
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt


def span(ev: Event) -> tuple[datetime, datetime]:
    """Start and end as naive local datetimes; an all-day event ends at midnight after its last day."""
    if ev.all_day or not isinstance(ev.start, datetime):
        s = ev.start if not isinstance(ev.start, datetime) else ev.start.date()
        e = ev.end if ev.end and not isinstance(ev.end, datetime) else (
            ev.end.date() if isinstance(ev.end, datetime) else s)
        return datetime.combine(s, time()), datetime.combine(e, time()) + timedelta(days=1)
    s = local(ev.start)
    e = local(ev.end) if isinstance(ev.end, datetime) else s + timedelta(hours=1)
    return s, max(e, s + timedelta(minutes=1))


def is_long(ev: Event) -> bool:
    """Shown in the 종일 row: all-day events and timed ones of 24 hours or more."""
    s, e = span(ev)
    return ev.all_day or (e - s) >= timedelta(hours=24)


def days_of(ev: Event) -> list[date]:
    s, e = span(ev)
    out, d = [], s.date()
    last = (e - timedelta(microseconds=1)).date()
    while d <= last:
        out.append(d)
        d += timedelta(days=1)
    return out


def hour_label(h: int) -> str:
    if h == 12:
        return "정오"
    return f"{'오전' if h < 12 else '오후'} {h % 12 or 12}시"


def time_label(t: datetime) -> str:
    ap = "오전" if t.hour < 12 else "오후"
    h = t.hour % 12 or 12
    return f"{ap} {h}시" if t.minute == 0 else f"{ap} {h}:{t.minute:02d}"


def range_label(ev: Event) -> str:
    if ev.all_day:
        return "종일"
    s, e = span(ev)
    a, b = time_label(s), time_label(e)
    if a[:2] == b[:2]:  # same 오전/오후: say it once
        b = b[3:]
    return f"{a}–{b}"


# ---- colors -----------------------------------------------------------------------
def _font(style: str, weight: int | None = None) -> QFont:
    st = tokens.type_style(style)
    f = QFont()
    f.setPixelSize(st["size"])
    f.setWeight(QFont.Weight(weight or st["weight"]))
    return tokens.tabular(f)


def base_color(ev: Event) -> QColor:
    c = QColor(ev.color) if ev.color else tokens.color("accent")
    return c if c.isValid() else tokens.color("accent")


def tint(ev: Event, strength: float = 0.18) -> QColor:
    return tokens.mix(tokens.color("region-box"), base_color(ev), strength)


def readable(c: QColor, bg: QColor) -> QColor:
    """The color itself, or stepped toward `label` until it reads 4.5:1 on bg."""
    for i in range(0, 11):
        t = tokens.mix(c, tokens.color("label"), i / 10)
        if tokens.contrast(t, bg) >= tokens.MIN_TEXT_CONTRAST:
            return t
    return tokens.color("label")


def solid(ev: Event) -> tuple[QColor, QColor]:
    """Selected event: filled with its color, text white (or label) at 4.5:1."""
    bg = base_color(ev)
    for _ in range(10):
        for name in ("on-accent", "label"):
            if tokens.contrast(tokens.color(name), bg) >= tokens.MIN_TEXT_CONTRAST:
                return bg, tokens.color(name)
        bg = tokens.mix(bg, QColor("black"), 0.12)
    return bg, tokens.color("on-accent")


def today_fill() -> tuple[QColor, QColor]:
    """Today's date: a red circle with a white number (red stepped darker if needed)."""
    bg = QColor(tokens.fill_for_text("red"))
    for name in ("on-accent", "label"):
        if tokens.contrast(tokens.color(name), bg) >= tokens.MIN_TEXT_CONTRAST:
            return bg, tokens.color(name)
    return bg, tokens.color("on-accent")


# ---- shared bits ---------------------------------------------------------------------
class _Painted(QWidget):
    """Base for the painted views: events in, signals out."""

    selected = Signal(object)          # Event or None
    opened = Signal(object)            # Event (double click)
    create = Signal(object, object, bool)  # start datetime, end datetime, all_day
    moved = Signal(object, object, object)  # Event, new start, new end
    day_clicked = Signal(object)       # date
    day_opened = Signal(object)        # date (go to the day view)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.events: list[Event] = []
        self.current: Event | None = None
        self.writable = lambda ev: True
        self.setMouseTracking(True)

    def set_events(self, events: list[Event], current: Event | None) -> None:
        self.events = events
        self.current = current
        self.update()

    def _is_current(self, ev: Event) -> bool:
        c = self.current
        return c is not None and (c is ev or (bool(c.id) and c.id == ev.id and c.calendar_id == ev.calendar_id))

    def _sep(self) -> QPen:
        return QPen(tokens.color("separator"), 1)


def _draw_text(p: QPainter, rect: QRectF, text: str, font: QFont, color: QColor,
               align=Qt.AlignLeft | Qt.AlignVCenter) -> None:
    p.setFont(font)
    p.setPen(color)
    fm = QFontMetrics(font)
    p.drawText(rect, align, fm.elidedText(text, Qt.ElideRight, int(max(0, rect.width()))))


def _pill(p: QPainter, rect: QRectF, ev: Event, current: bool, font: QFont, hover: bool = False) -> None:
    """An all-day event: a capsule in the calendar's tint, or filled when selected."""
    if current:
        bg, fg = solid(ev)
    else:
        bg = tint(ev, 0.26 if hover else 0.18)
        fg = readable(base_color(ev), bg)
    p.setPen(Qt.NoPen)
    p.setBrush(bg)
    r = rect.height() / 2
    p.drawRoundedRect(rect, r, r)
    pad = tokens.space("space-2")
    _draw_text(p, rect.adjusted(pad, 0, -pad, 0), ev.title or "새 일정", font, fg)


# ---- day header (주) ------------------------------------------------------------------
class DayHeader(QWidget):
    """'일 29 · 월 30 …' above the week columns; today's number in a red circle."""

    day_clicked = Signal(object)

    def __init__(self, gutter: int, parent=None):
        super().__init__(parent)
        self.gutter = gutter
        self.days: list[date] = []
        self.right_pad = 0
        self.setFixedHeight(tokens.space("control-lg") + tokens.space("space-2"))

    def set_days(self, days: list[date]) -> None:
        self.days = days
        self.update()

    def mousePressEvent(self, e) -> None:
        i = self._col(e.position().x())
        if i is not None:
            self.day_clicked.emit(self.days[i])

    def _col(self, x: float) -> int | None:
        w = (self.width() - self.gutter - self.right_pad) / max(1, len(self.days))
        i = int((x - self.gutter) // w) if x >= self.gutter else -1
        return i if 0 <= i < len(self.days) else None

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), tokens.color("region-box"))
        n = max(1, len(self.days))
        w = (self.width() - self.gutter - self.right_pad) / n
        big = _font("mac-title-3")
        small = _font("mac-title-3")
        today = date.today()
        for i, d in enumerate(self.days):
            x = self.gutter + i * w
            cell = QRectF(x, 0, w, self.height())
            wd = WEEKDAYS[(d.weekday() + 1) % 7]
            num = str(d.day)
            fm_w = QFontMetrics(small).horizontalAdvance(wd + " ")
            fm_n = QFontMetrics(big).horizontalAdvance(num)
            dia = tokens.space("control-md")
            total = fm_w + max(fm_n, dia)
            x0 = cell.center().x() - total / 2
            weekend = d.weekday() >= 5
            sec = tokens.text("label-secondary")
            _draw_text(p, QRectF(x0, 0, fm_w, self.height()), wd, small, sec if weekend else tokens.color("label"))
            nrect = QRectF(x0 + fm_w, (self.height() - dia) / 2, max(fm_n, dia), dia)
            if d == today:
                bg, fg = today_fill()
                p.setPen(Qt.NoPen)
                p.setBrush(bg)
                p.drawEllipse(QRectF(nrect.center().x() - dia / 2, nrect.top(), dia, dia))
                _draw_text(p, nrect, num, _font("mac-title-3", 600), fg, Qt.AlignCenter)
            else:
                _draw_text(p, nrect, num, big, sec if weekend else tokens.color("label"), Qt.AlignCenter)
        p.setPen(QPen(tokens.color("separator"), 1))
        p.drawLine(QPointF(self.gutter, self.height() - 0.5), QPointF(self.width() - self.right_pad, self.height() - 0.5))


# ---- 종일 row ------------------------------------------------------------------------
class AllDayRow(_Painted):
    """All-day (and 24 h+) events as capsules across the days they cover."""

    MAX_LANES = 4

    def __init__(self, gutter: int, parent=None):
        super().__init__(parent)
        self.gutter = gutter
        self.days: list[date] = []
        self.right_pad = 0
        self._lanes: list[tuple[Event, int, int, int]] = []  # event, first col, last col, lane
        self._more: dict[int, int] = {}
        self._hover: Event | None = None

    def lane_h(self) -> int:
        return tokens.space("chip-height")

    def set_days(self, days: list[date]) -> None:
        self.days = days
        self._layout()

    def set_events(self, events, current) -> None:
        super().set_events(events, current)
        self._layout()

    def _layout(self) -> None:
        self._lanes, self._more = [], {}
        if not self.days:
            return
        first, last = self.days[0], self.days[-1]
        lanes: list[list[tuple[int, int]]] = []
        for ev in sorted((e for e in self.events if is_long(e)), key=lambda e: (span(e)[0], -(span(e)[1] - span(e)[0]))):
            ds = days_of(ev)
            if not ds or ds[-1] < first or ds[0] > last:
                continue
            a = max(0, (ds[0] - first).days)
            b = min(len(self.days) - 1, (ds[-1] - first).days)
            for li, used in enumerate(lanes):
                if all(b < u0 or a > u1 for u0, u1 in used):
                    used.append((a, b))
                    break
            else:
                li = len(lanes)
                lanes.append([(a, b)])
            if li < self.MAX_LANES:
                self._lanes.append((ev, a, b, li))
            else:
                for c in range(a, b + 1):
                    self._more[c] = self._more.get(c, 0) + 1
        n = min(len(lanes), self.MAX_LANES) + (1 if self._more else 0)
        gap = tokens.space("space-1")
        self.setFixedHeight(max(1, n) * (self.lane_h() + gap) + gap + 1)
        self.update()

    def _col_w(self) -> float:
        return (self.width() - self.gutter - self.right_pad) / max(1, len(self.days))

    def _rect(self, a: int, b: int, lane: int) -> QRectF:
        w = self._col_w()
        gap = tokens.space("space-1")
        y = gap + lane * (self.lane_h() + gap)
        return QRectF(self.gutter + a * w + 2, y, (b - a + 1) * w - 4, self.lane_h())

    def _hit(self, pos: QPointF) -> Event | None:
        for ev, a, b, lane in self._lanes:
            if self._rect(a, b, lane).contains(pos):
                return ev
        return None

    def mouseMoveEvent(self, e) -> None:
        h = self._hit(e.position())
        if h is not self._hover:
            self._hover = h
            self.setCursor(Qt.PointingHandCursor if h else Qt.ArrowCursor)
            self.setToolTip(f"{h.title}\n{h.calendar_name}" if h else "")
            self.update()

    def leaveEvent(self, _e) -> None:
        self._hover = None
        self.update()

    def mousePressEvent(self, e) -> None:
        self.selected.emit(self._hit(e.position()))

    def mouseDoubleClickEvent(self, e) -> None:
        ev = self._hit(e.position())
        if ev:
            self.opened.emit(ev)
            return
        x = e.position().x()
        if x >= self.gutter and self.days:
            i = min(len(self.days) - 1, int((x - self.gutter) // self._col_w()))
            d = self.days[i]
            self.create.emit(datetime.combine(d, time()), datetime.combine(d, time()), True)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), tokens.color("region-box"))
        _draw_text(p, QRectF(0, 0, self.gutter - tokens.space("space-2"), self.lane_h() + 2 * tokens.space("space-1")),
                   "종일", _font("mac-footnote"), tokens.text("label-secondary"), Qt.AlignRight | Qt.AlignVCenter)
        w = self._col_w()
        p.setPen(self._sep())
        for i in range(1, len(self.days)):
            x = self.gutter + i * w + 0.5
            p.drawLine(QPointF(x, 0), QPointF(x, self.height()))
        font = _font("mac-callout", 600)
        for ev, a, b, lane in self._lanes:
            _pill(p, self._rect(a, b, lane), ev, self._is_current(ev), font, ev is self._hover)
        for c, n in self._more.items():
            r = self._rect(c, c, self.MAX_LANES)
            _draw_text(p, r.adjusted(tokens.space("space-2"), 0, 0, 0), f"+{n}개 더", _font("mac-callout"),
                       tokens.text("label-secondary"))
        p.setPen(QPen(tokens.color("separator"), 1))
        p.drawLine(QPointF(0, self.height() - 0.5), QPointF(self.width(), self.height() - 0.5))


# ---- hourly grid (일 / 주) --------------------------------------------------------------
@dataclass
class _Block:
    ev: Event
    col: int
    rect: QRectF


class TimeGrid(_Painted):
    """24 hours of the given days. Drag on empty space to make an event, drag an event to
    move it (15-minute steps), double-click to open or create."""

    def __init__(self, gutter: int, parent=None):
        super().__init__(parent)
        self.gutter = gutter
        self.days: list[date] = []
        self.blocks: list[_Block] = []
        self._hover: Event | None = None
        self._press: QPointF | None = None
        self._drag: tuple[str, Event | None, QPointF] | None = None  # ("move"|"create", ev, origin)
        self._preview: tuple[datetime, datetime] | None = None
        self.setMinimumHeight(self.top() + 24 * self.hour_h() + tokens.space("space-2"))
        self._clock = QTimer(self, interval=60_000, timeout=self.update)  # the current-time line
        self._clock.start()

    @staticmethod
    def hour_h() -> int:
        return 2 * tokens.space("row-compact")  # 48 per hour: a 30-minute event fits one line

    @staticmethod
    def top() -> int:
        return tokens.space("space-2")

    def set_days(self, days: list[date]) -> None:
        self.days = days
        self._layout()

    def set_events(self, events, current) -> None:
        super().set_events(events, current)
        self._layout()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._layout()

    def y_of(self, t: time | datetime) -> float:
        return self.top() + (t.hour + t.minute / 60) * self.hour_h()

    def _col_w(self) -> float:
        return (self.width() - self.gutter) / max(1, len(self.days))

    def _at(self, pos: QPointF) -> tuple[int, datetime] | None:
        if pos.x() < self.gutter or not self.days:
            return None
        i = min(len(self.days) - 1, int((pos.x() - self.gutter) // self._col_w()))
        minutes = max(0, min(24 * 60 - SNAP_MIN, (pos.y() - self.top()) / self.hour_h() * 60))
        minutes = int(minutes // SNAP_MIN * SNAP_MIN)
        return i, datetime.combine(self.days[i], time()) + timedelta(minutes=minutes)

    def _layout(self) -> None:
        self.blocks = []
        if not self.days:
            self.update()
            return
        w = self._col_w()
        min_h = tokens.type_style("mac-callout")["size"] + tokens.space("space-2")
        for ci, d in enumerate(self.days):
            day0 = datetime.combine(d, time())
            segs = []
            for ev in self.events:
                if is_long(ev):
                    continue
                s, e = span(ev)
                if e <= day0 or s >= day0 + timedelta(days=1):
                    continue
                segs.append((max(s, day0), min(e, day0 + timedelta(days=1)), ev))
            segs.sort(key=lambda t: (t[0], -(t[1] - t[0]).total_seconds()))
            # clusters of overlapping events share the column side by side
            cluster: list = []
            cluster_end = None

            def flush():
                cols: list[datetime] = []
                placed = []
                for s, e, ev in cluster:
                    for k, end in enumerate(cols):
                        if end <= s:
                            cols[k] = e
                            break
                    else:
                        k = len(cols)
                        cols.append(e)
                    placed.append((s, e, ev, k))
                n = max(1, len(cols))
                for s, e, ev, k in placed:
                    y0 = self.y_of(s)
                    y1 = self.y_of(e) if e < day0 + timedelta(days=1) else self.top() + 24 * self.hour_h()
                    x = self.gutter + ci * w + k * w / n + 1
                    self.blocks.append(_Block(ev, ci, QRectF(x, y0 + 1, w / n - 3, max(min_h, y1 - y0 - 2))))

            for s, e, ev in segs:
                if cluster and s >= cluster_end:
                    flush()
                    cluster = []
                if not cluster:
                    cluster_end = e
                cluster.append((s, e, ev))
                cluster_end = max(cluster_end, e)
            if cluster:
                flush()
        self.update()

    def _hit(self, pos: QPointF) -> Event | None:
        for b in reversed(self.blocks):
            if b.rect.contains(pos):
                return b.ev
        return None

    # -- mouse --
    def mouseMoveEvent(self, e) -> None:
        pos = e.position()
        if self._press is not None and self._drag is None and (pos - self._press).manhattanLength() > 4:
            ev = self._hit(self._press)
            if ev is not None and self.writable(ev):
                self._drag = ("move", ev, self._press)
            elif ev is None:
                self._drag = ("create", None, self._press)
        if self._drag:
            kind, ev, origin = self._drag
            a, b = self._at(origin), self._at(pos)
            if a and b:
                if kind == "create":
                    s, t = sorted((a[1], b[1]))
                    if s.date() != t.date():  # creating stays inside the first day
                        t = datetime.combine(s.date(), time(23, 45))
                    self._preview = (s, t + timedelta(minutes=SNAP_MIN))
                else:
                    s0, e0 = span(ev)
                    delta = b[1] - a[1]
                    self._preview = (s0 + delta, e0 + delta)
            self.update()
            return
        h = self._hit(pos)
        if h is not self._hover:
            self._hover = h
            self.setCursor(Qt.PointingHandCursor if h else Qt.ArrowCursor)
            self.setToolTip(f"{h.title}\n{range_label(h)}" + (f"\n{h.location}" if h.location else "") if h else "")
            self.update()

    def leaveEvent(self, _e) -> None:
        self._hover = None
        self.update()

    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.LeftButton:
            return
        self._press = e.position()
        self._drag = None
        self._preview = None

    def mouseReleaseEvent(self, e) -> None:
        drag, preview, press = self._drag, self._preview, self._press
        self._drag = self._preview = self._press = None
        if drag and preview:
            kind, ev, _ = drag
            if kind == "create":
                self.create.emit(preview[0], preview[1], False)
            elif preview[0] != span(ev)[0]:
                self.moved.emit(ev, preview[0], preview[1])
            self.update()
            return
        if press is not None:
            self.selected.emit(self._hit(press))
        self.update()

    def mouseDoubleClickEvent(self, e) -> None:
        ev = self._hit(e.position())
        if ev:
            self.opened.emit(ev)
            return
        at = self._at(e.position())
        if at:
            s = at[1].replace(minute=0 if at[1].minute < 30 else 30)
            self.create.emit(s, s + timedelta(hours=1), False)

    # -- paint --
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), tokens.color("region-box"))
        hh = self.hour_h()
        small = _font("mac-footnote")
        sec = tokens.text("label-secondary")
        sep = self._sep()
        now = datetime.now()
        now_y = self.y_of(now) if now.date() in self.days else None
        for h in range(25):
            y = self.top() + h * hh + 0.5
            p.setPen(sep)
            p.drawLine(QPointF(self.gutter, y), QPointF(self.width(), y))
            if 0 < h < 24 and (now_y is None or abs(now_y - y) > hh / 3):  # the time badge replaces it
                _draw_text(p, QRectF(0, y - hh / 2, self.gutter - tokens.space("space-2"), hh), hour_label(h), small, sec,
                           Qt.AlignRight | Qt.AlignVCenter)
        w = self._col_w()
        p.setPen(sep)
        for i in range(len(self.days) + 1):
            x = self.gutter + i * w + 0.5
            p.drawLine(QPointF(x, self.top()), QPointF(x, self.top() + 24 * hh))
        title_f, sub_f = _font("mac-callout", 600), _font("mac-footnote")
        pad = tokens.space("space-1")
        rad = tokens.radius("value")
        for b in self.blocks:
            ev, r = b.ev, b.rect
            cur = self._is_current(ev)
            if cur:
                bg, fg = solid(ev)
                sub = fg
            else:
                bg = tint(ev, 0.26 if ev is self._hover else 0.18)
                fg = readable(base_color(ev), bg)
                sub = fg
            path = QPainterPath()
            path.addRoundedRect(r, rad, rad)
            p.save()
            p.setClipPath(path)  # rounded containers clip what is inside them
            p.fillRect(r, bg)
            if not cur:
                p.fillRect(QRectF(r.left(), r.top(), EVENT_BAR, r.height()), base_color(ev))
            line = QFontMetrics(title_f).height()
            tx = r.adjusted(EVENT_BAR + pad + 1, pad / 2, -pad, 0)
            if r.height() < 2 * line + pad:  # one line: title and time side by side
                _draw_text(p, QRectF(tx.left(), r.top(), tx.width(), min(r.height(), line + pad)),
                           ev.title or "새 일정", title_f, fg)
            else:
                _draw_text(p, QRectF(tx.left(), tx.top(), tx.width(), line), ev.title or "새 일정", title_f, fg)
                y = tx.top() + line
                _draw_text(p, QRectF(tx.left(), y, tx.width(), line), range_label(ev), sub_f, sub)
                if ev.location and r.height() >= 3 * line + pad:
                    _draw_text(p, QRectF(tx.left(), y + line, tx.width(), line), ev.location, sub_f, sub)
            p.restore()
        if self._preview:
            s, e = self._preview
            for ci, d in enumerate(self.days):
                if d == s.date():
                    y0, y1 = self.y_of(s), self.y_of(e) if e.date() == d else self.top() + 24 * hh
                    r = QRectF(self.gutter + ci * w + 1, y0 + 1, w - 3, max(8, y1 - y0 - 2))
                    acc = tokens.color("accent")
                    fill = tokens.mix(tokens.color("region-box"), acc, 0.22)
                    p.setPen(QPen(acc, 1, Qt.DashLine))
                    p.setBrush(fill)
                    p.drawRoundedRect(r, rad, rad)
                    _draw_text(p, r.adjusted(pad + 2, 0, -pad, 0), f"{time_label(s)}–{time_label(e)}",
                               _font("mac-callout", 600), readable(acc, fill), Qt.AlignLeft | Qt.AlignTop)
        self._paint_now(p, w)

    def _paint_now(self, p: QPainter, w: float) -> None:
        now = datetime.now()
        if now.date() not in self.days:
            return
        red = QColor(tokens.fill_for_text("red"))
        y = self.y_of(now)
        ci = self.days.index(now.date())
        x0 = self.gutter + ci * w
        p.setPen(QPen(red, 1))
        p.drawLine(QPointF(self.gutter, y), QPointF(self.width(), y)) if len(self.days) == 1 else \
            p.drawLine(QPointF(x0, y), QPointF(x0 + w, y))
        p.setPen(Qt.NoPen)
        p.setBrush(red)
        dot = tokens.space("space-2")
        p.drawEllipse(QPointF(x0, y), dot / 2, dot / 2)
        # time badge in the gutter: red capsule, white digits
        f = _font("mac-footnote", 600)
        text = f"{now.hour % 12 or 12}:{now.minute:02d}"
        tw = QFontMetrics(f).horizontalAdvance(text) + 2 * tokens.space("space-1")
        hgt = tokens.space("badge-height")
        badge = QRectF(self.gutter - tw - tokens.space("space-1"), y - hgt / 2, tw, hgt)
        bg, fg = today_fill()
        p.setBrush(bg)
        p.drawRoundedRect(badge, hgt / 2, hgt / 2)
        _draw_text(p, badge, text, f, fg, Qt.AlignCenter)


class TimeView(QWidget):
    """Header + 종일 row + scrolling hourly grid; one day or a week."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QScrollArea
        gutter = 2 * tokens.space("control-md") + tokens.space("space-2")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.header = DayHeader(gutter)
        self.allday = AllDayRow(gutter)
        self.grid = TimeGrid(gutter)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.grid)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        pad = self.scroll.verticalScrollBar().sizeHint().width()
        self.header.right_pad = self.allday.right_pad = pad
        lay.addWidget(self.header)
        lay.addWidget(self.allday)
        lay.addWidget(self.scroll, 1)
        self._scrolled = False

    def set_days(self, days: list[date]) -> None:
        self.header.setVisible(len(days) > 1)
        self.header.set_days(days)
        self.allday.set_days(days)
        self.grid.set_days(days)

    def set_events(self, events, current) -> None:
        self.allday.set_events(events, current)
        self.grid.set_events(events, current)

    def scroll_to_morning(self) -> None:
        """Open at 8 o'clock (or an hour before now when that is later), like Calendar."""
        h = max(8, min(datetime.now().hour - 1, 16)) if not self._scrolled else None
        if h is not None:
            QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(int(self.grid.y_of(time(h)))
                                                                                  - self.grid.top()))
            self._scrolled = True

    def views(self) -> list[_Painted]:
        return [self.allday, self.grid]


# ---- month ---------------------------------------------------------------------------
class MonthGrid(_Painted):
    """Six weeks; each day lists its events (all-day as capsules, timed as dot + title + time)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.month = date.today().replace(day=1)
        self.selected_day: date | None = None
        self._rows: list[tuple[QRectF, Event]] = []
        self._more: list[tuple[QRectF, date]] = []
        self._hover: Event | None = None

    def set_month(self, month: date, selected_day: date | None) -> None:
        self.month = month.replace(day=1)
        self.selected_day = selected_day
        self.update()

    def head_h(self) -> int:
        return tokens.space("control-md")

    def _cell(self, i: int) -> QRectF:
        w = self.width() / 7
        h = (self.height() - self.head_h()) / 6
        return QRectF((i % 7) * w, self.head_h() + (i // 7) * h, w, h)

    def _day_at(self, pos: QPointF) -> date | None:
        if pos.y() < self.head_h():
            return None
        for i in range(42):
            if self._cell(i).contains(pos):
                return month_grid_start(self.month) + timedelta(days=i)
        return None

    def _hit(self, pos: QPointF) -> Event | None:
        for r, ev in self._rows:
            if r.contains(pos):
                return ev
        return None

    def mouseMoveEvent(self, e) -> None:
        h = self._hit(e.position())
        if h is not self._hover:
            self._hover = h
            self.setCursor(Qt.PointingHandCursor if h else Qt.ArrowCursor)
            self.setToolTip(f"{h.title}\n{range_label(h)}" if h else "")
            self.update()

    def leaveEvent(self, _e) -> None:
        self._hover = None
        self.update()

    def mousePressEvent(self, e) -> None:
        ev = self._hit(e.position())
        if ev:
            self.selected.emit(ev)
            return
        for r, d in self._more:
            if r.contains(e.position()):
                self.day_opened.emit(d)
                return
        d = self._day_at(e.position())
        if d:
            self.selected_day = d
            self.selected.emit(None)
            self.day_clicked.emit(d)
            self.update()

    def mouseDoubleClickEvent(self, e) -> None:
        ev = self._hit(e.position())
        if ev:
            self.opened.emit(ev)
            return
        d = self._day_at(e.position())
        if d:
            self.day_opened.emit(d)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), tokens.color("region-box"))
        sec = tokens.text("label-secondary")
        w = self.width() / 7
        hf = _font("mac-callout", 600)
        pad = tokens.space("space-2")
        for i, wd in enumerate(WEEKDAYS):
            _draw_text(p, QRectF(i * w, 0, w - pad, self.head_h()), wd, hf, sec, Qt.AlignRight | Qt.AlignVCenter)
        start = month_grid_start(self.month)
        today = date.today()
        by_day: dict[date, list[Event]] = {}
        for ev in self.events:
            for d in days_of(ev):
                by_day.setdefault(d, []).append(ev)
        self._rows, self._more = [], []
        num_f = _font("mac-title-3")
        row_h = tokens.space("status-height")
        ev_f, time_f = _font("mac-callout", 600), _font("mac-callout")
        p.setPen(self._sep())
        p.drawLine(QPointF(0, self.head_h() - 0.5), QPointF(self.width(), self.head_h() - 0.5))
        for i in range(42):
            d = start + timedelta(days=i)
            c = self._cell(i)
            inside = d.month == self.month.month
            if d.weekday() >= 5 or not inside:  # weekends and the neighbour months sit back a step
                p.fillRect(c, tokens.color("region-canvas") if not inside else tokens.color("region-box"))
            p.setPen(self._sep())
            p.drawLine(QPointF(c.left() + 0.5, c.top()), QPointF(c.left() + 0.5, c.bottom()))
            p.drawLine(QPointF(c.left(), c.bottom() - 0.5), QPointF(c.right(), c.bottom() - 0.5))
            label = f"{d.month}월 {d.day}일" if d.day == 1 else str(d.day)
            dia = tokens.space("control-md")
            fm = QFontMetrics(num_f)
            nw = max(dia, fm.horizontalAdvance(label) + pad)
            nrect = QRectF(c.right() - nw - tokens.space("space-1"), c.top() + tokens.space("space-1"), nw, dia)
            if d == today:
                bg, fg = today_fill()
                p.setPen(Qt.NoPen)
                p.setBrush(bg)
                p.drawRoundedRect(nrect, dia / 2, dia / 2)
                _draw_text(p, nrect, label, _font("mac-title-3", 600), fg, Qt.AlignCenter)
            else:
                col = tokens.color("label") if inside else tokens.text("label-secondary", "region-canvas")
                _draw_text(p, nrect.adjusted(0, 0, -pad / 2, 0), label, num_f, col, Qt.AlignRight | Qt.AlignVCenter)
            if d == self.selected_day and d != today:
                p.setPen(QPen(tokens.color("accent"), 2))
                p.setBrush(Qt.NoBrush)
                p.drawRect(c.adjusted(1, 1, -2, -2))
            evs = sorted(by_day.get(d, []), key=lambda e: (not is_long(e), span(e)[0]))
            y = nrect.bottom() + tokens.space("space-1")
            room = int((c.bottom() - y - tokens.space("space-1")) // row_h)
            show = evs if len(evs) <= room else evs[:max(0, room - 1)]
            for ev in show:
                r = QRectF(c.left() + tokens.space("space-1"), y, c.width() - 2 * tokens.space("space-1"), row_h - 2)
                cur = self._is_current(ev)
                if is_long(ev):
                    _pill(p, r, ev, cur, ev_f, ev is self._hover)
                else:
                    if cur or ev is self._hover:
                        bg = solid(ev)[0] if cur else tint(ev, 0.18)
                        p.setPen(Qt.NoPen)
                        p.setBrush(bg)
                        p.drawRoundedRect(r, tokens.radius("value"), tokens.radius("value"))
                        fg = solid(ev)[1] if cur else tokens.color("label")
                    else:
                        fg = tokens.color("label")
                    dot = tokens.space("space-2")
                    p.setPen(Qt.NoPen)
                    p.setBrush(fg if cur else base_color(ev))
                    p.drawEllipse(QPointF(r.left() + pad / 2 + dot / 2, r.center().y()), dot / 2, dot / 2)
                    s = span(ev)[0]
                    tl = time_label(s) if s.date() == d else ""
                    tw = QFontMetrics(time_f).horizontalAdvance(tl) + pad / 2 if tl else 0
                    _draw_text(p, QRectF(r.left() + pad / 2 + dot + pad / 2, r.top(), r.width() - dot - pad - tw, r.height()),
                               ev.title or "새 일정", ev_f, fg)
                    if tl:
                        _draw_text(p, QRectF(r.right() - tw, r.top(), tw - pad / 4, r.height()), tl, time_f,
                                   fg if cur else tokens.text("label-secondary", "region-box" if inside else "region-canvas"),
                                   Qt.AlignRight | Qt.AlignVCenter)
                self._rows.append((r, ev))
                y += row_h
            if len(show) < len(evs):
                r = QRectF(c.left() + pad, y, c.width() - 2 * pad, row_h - 2)
                _draw_text(p, r, f"{len(evs) - len(show)}개 더", time_f, tokens.text("label-secondary"))
                self._more.append((r, d))


# ---- year ----------------------------------------------------------------------------
class YearGrid(_Painted):
    """Twelve months; days with events get a tint that deepens with how many there are."""

    month_opened = Signal(object)   # first day of a month

    def __init__(self, parent=None):
        super().__init__(parent)
        self.year = date.today().year
        self._days: list[tuple[QRectF, date]] = []
        self._titles: list[tuple[QRectF, date]] = []

    def set_year(self, year: int) -> None:
        self.year = year
        self.update()

    def mousePressEvent(self, e) -> None:
        for r, m in self._titles:
            if r.contains(e.position()):
                self.month_opened.emit(m)
                return
        for r, d in self._days:
            if r.contains(e.position()):
                self.day_opened.emit(d)
                return

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), tokens.color("region-box"))
        counts: dict[date, int] = {}
        for ev in self.events:
            for d in days_of(ev):
                counts[d] = counts.get(d, 0) + 1
        self._days, self._titles = [], []
        cols, rows = 4, 3
        gap = tokens.space("gap-section")
        cw = (self.width() - gap * (cols - 1)) / cols
        ch = (self.height() - gap * (rows - 1)) / rows
        today = date.today()
        title_f = _font("mac-title-3", 600)
        wd_f, day_f = _font("mac-footnote", 600), _font("mac-callout")
        sec = tokens.text("label-secondary")
        acc = tokens.color("accent")
        for m in range(12):
            x = (m % cols) * (cw + gap)
            y = (m // cols) * (ch + gap)
            first = date(self.year, m + 1, 1)
            th = tokens.space("control-md")
            tr = QRectF(x, y, cw, th)
            red = QColor(tokens.fill_for_text("red"))
            is_now = first.year == today.year and first.month == today.month
            _draw_text(p, tr, f"{m + 1}월", title_f, readable(red, tokens.color("region-box")) if is_now
                       else tokens.color("label"))
            self._titles.append((tr, first))
            cell_w = cw / 7
            cell_h = (ch - th) / 7
            for i, wd in enumerate(WEEKDAYS):
                _draw_text(p, QRectF(x + i * cell_w, y + th, cell_w, cell_h), wd, wd_f, sec, Qt.AlignCenter)
            start = week_start(first)
            for i in range(42):
                d = start + timedelta(days=i)
                if d.month != first.month:
                    continue
                r = QRectF(x + (i % 7) * cell_w, y + th + (1 + i // 7) * cell_h, cell_w, cell_h)
                dia = min(cell_w, cell_h) - 2
                circ = QRectF(r.center().x() - dia / 2, r.center().y() - dia / 2, dia, dia)
                n = counts.get(d, 0)
                if d == today:
                    bg, fg = today_fill()
                    p.setPen(Qt.NoPen)
                    p.setBrush(bg)
                    p.drawEllipse(circ)
                    _draw_text(p, r, str(d.day), _font("mac-callout", 600), fg, Qt.AlignCenter)
                else:
                    if n:
                        p.setPen(Qt.NoPen)
                        bg = tokens.mix(tokens.color("region-box"), acc, min(0.42, 0.14 * n))
                        p.setBrush(bg)
                        p.drawEllipse(circ)
                        fg = readable(tokens.color("label"), bg)
                    else:
                        fg = sec if d.weekday() >= 5 else tokens.color("label")
                    _draw_text(p, r, str(d.day), _font("mac-callout", 600) if n else day_f, fg, Qt.AlignCenter)
                self._days.append((r, d))


# ---- mini month -----------------------------------------------------------------------
class MiniMonth(QWidget):
    """A small month to jump to a date: today in red, the chosen day in a filled circle."""

    date_clicked = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.month = date.today().replace(day=1)
        self.selected: date | None = date.today()
        self.marked: set[date] = set()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(tokens.space("space-1"))
        head = QHBoxLayout()
        self.title = QLabel()
        self.title.setObjectName("scopeTitle")
        prev = icons.set_icon(QPushButton(), "chevron.left", "이전 달")
        prev.setObjectName("plain")
        nxt = icons.set_icon(QPushButton(), "chevron.right", "다음 달")
        nxt.setObjectName("plain")
        prev.clicked.connect(lambda: self.show_month(add_months(self.month, -1)))
        nxt.clicked.connect(lambda: self.show_month(add_months(self.month, 1)))
        head.addWidget(prev)
        head.addWidget(self.title, 1, Qt.AlignCenter)
        head.addWidget(nxt)
        lay.addLayout(head)
        self.days = _MiniDays(self)
        lay.addWidget(self.days)
        self.show_month(self.month)

    def show_month(self, month: date) -> None:
        self.month = month.replace(day=1)
        self.title.setText(f"{self.month.year}년 {self.month.month}월")
        self.days.update()

    def set_selected(self, d: date) -> None:
        self.selected = d
        if (d.year, d.month) != (self.month.year, self.month.month):
            self.show_month(d)
        self.days.update()

    def set_marked(self, marked: set[date]) -> None:
        self.marked = marked
        self.days.update()


class _MiniDays(QWidget):
    def __init__(self, owner: MiniMonth):
        super().__init__(owner)
        self.owner = owner
        self.setFixedHeight(7 * tokens.space("chip-height"))
        self.setCursor(Qt.PointingHandCursor)

    def _cell(self, i: int) -> QRectF:
        w = self.width() / 7
        h = self.height() / 7
        return QRectF((i % 7) * w, (1 + i // 7) * h, w, h)

    def mousePressEvent(self, e) -> None:
        start = month_grid_start(self.owner.month)
        for i in range(42):
            if self._cell(i).contains(e.position()):
                d = start + timedelta(days=i)
                self.owner.set_selected(d)
                self.owner.date_clicked.emit(d)
                return

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width() / 7, self.height() / 7
        sec = tokens.text("label-secondary", "region-box")
        for i, wd in enumerate(WEEKDAYS):
            _draw_text(p, QRectF(i * w, 0, w, h), wd, _font("mac-footnote", 600), sec, Qt.AlignCenter)
        start = month_grid_start(self.owner.month)
        today = date.today()
        for i in range(42):
            d = start + timedelta(days=i)
            r = self._cell(i)
            dia = min(w, h) - 2
            circ = QRectF(r.center().x() - dia / 2, r.center().y() - dia / 2, dia, dia)
            inside = d.month == self.owner.month.month
            bold = d in self.owner.marked
            f = _font("mac-callout", 600 if bold or d == today else None)
            if d == self.owner.selected:
                bg, fg = today_fill() if d == today else (tokens.color("label"), tokens.color("region-box"))
                p.setPen(Qt.NoPen)
                p.setBrush(bg)
                p.drawEllipse(circ)
            elif d == today:
                fg = readable(QColor(tokens.fill_for_text("red")), tokens.color("region-canvas"))
            else:
                fg = tokens.color("label") if inside else sec
            _draw_text(p, r, str(d.day), f, fg, Qt.AlignCenter)


# ---- calendar list -------------------------------------------------------------------------
class CalendarCheck(QAbstractButton):
    """One calendar: a check square in the calendar's color + its name (show / hide its events)."""

    def __init__(self, cal: dict, parent=None):
        super().__init__(parent)
        self.cal = cal
        self.setCheckable(True)
        self.setText(cal.get("name") or cal["id"])
        self.setToolTip(("보기만 가능 · " if not cal.get("writable", True) else "") + "눌러서 보이기/숨기기")
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self) -> QSize:
        return QSize(tokens.space("sidebar-width"), tokens.space("row"))

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.underMouse():
            p.setPen(Qt.NoPen)
            p.setBrush(tokens.hover())
            p.drawRoundedRect(QRectF(self.rect()), tokens.radius("row"), tokens.radius("row"))
        s = tokens.space("check-size")
        x = tokens.space("row-pad")
        box = QRectF(x, (self.height() - s) / 2, s, s)
        c = QColor(self.cal.get("color") or tokens.raw("accent"))
        rad = s * 0.28  # bundle.css .hig-check radius
        if self.isChecked():
            p.setPen(Qt.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(box, rad, rad)
            on = tokens.color("on-accent") if tokens.contrast(tokens.color("on-accent"), c) >= 3 else tokens.color("label")
            pm = icons.pixmap("checkmark", on, 600, int(s - 4))
            p.drawPixmap(QPointF(box.left() + 2, box.top() + 2), pm)
        else:
            p.setPen(QPen(c, 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(box.adjusted(0.75, 0.75, -0.75, -0.75), rad, rad)
        _draw_text(p, QRectF(box.right() + tokens.space("gap-tight"), 0, self.width() - box.right(), self.height()),
                   self.text(), _font("mac-body"), tokens.color("label"))


class CalendarList(QWidget):
    toggled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(0)
        self.title = QLabel("Google 캘린더")
        self.title.setObjectName("groupHeader")
        self.title.setContentsMargins(tokens.space("row-pad"), 0, 0, tokens.space("space-1"))
        self.lay.addWidget(self.title)
        self.checks: list[CalendarCheck] = []

    def set_calendars(self, cals: list[dict], hidden: set[str]) -> None:
        for c in self.checks:
            c.hide()
            c.setParent(None)
            c.deleteLater()
        self.checks = []
        for cal in cals:
            b = CalendarCheck(cal)
            b.setChecked(cal["id"] not in hidden)
            b.toggled.connect(lambda _on: self.toggled.emit())
            self.lay.addWidget(b)
            self.checks.append(b)

    def hidden(self) -> set[str]:
        return {b.cal["id"] for b in self.checks if not b.isChecked()}
