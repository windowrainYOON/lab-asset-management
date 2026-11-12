"""장비 예약 tab: equipment list | week (or day) timetable of the chosen machine | inspector.

Drag on empty time to book, drag a booking to move it, click it to edit. A booking that
would overlap another one of the same machine is drawn as a red dashed box and is not
saved. Machines marked 점검 중 are dimmed and take no bookings.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from PySide6.QtCore import QDate, QPoint, QRect, QRectF, Qt, QTime, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QColorDialog, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu,
    QMessageBox, QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QTimeEdit, QVBoxLayout,
    QWidget,
)

from ..core import settings
from ..core.reservations import COLORS, STATES, Equipment, Reservation, ReservationBoard, same_person
from ..core.records import new_id
from .alerts import confirm_delete
from .inset import InsetGroup, inset_title
from .table_model import compact_rows
from .theme import icons, tokens
from .widgets import InspectorPanel, Segmented, glass_capsule

HOUR_H = 44
HEAD_H = 34
LEFT_W = 52
WEEKDAYS = "월화수목금토일"


def me() -> str:
    return settings.load_settings().get("requester_name", "").strip()


def member_names(project) -> list[str]:
    return [m.get("name") or m.get("email", "") for m in project.members()] if project else []


def dot_icon(color: str, dim: bool = False) -> QIcon:
    pm = QPixmap(24, 24)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = QColor(color or COLORS[0])
    if dim:
        c.setAlphaF(0.35)
    p.setBrush(c)
    p.setPen(Qt.NoPen)
    p.drawEllipse(6, 6, 12, 12)
    p.end()
    return QIcon(pm)


class TimeGrid(QWidget):
    """Days across, hours down. Emits the new range for a drag on empty time or a move."""
    create = Signal(object, object)          # start, end
    picked = Signal(object)                  # Reservation | None
    moved = Signal(object, object, object)   # Reservation, start, end

    def __init__(self, page: "ReservationPage"):
        super().__init__()
        self.page = page
        self.days: list[date] = []
        self.items: list[Reservation] = []
        self.color = QColor(COLORS[0])
        self.step = 30
        self.draft: tuple[datetime, datetime] | None = None
        self.draft_bad = False
        self.selected = ""
        self._drag = None  # ("new", start_dt) | ("move", res, offset_minutes)
        self._preview: tuple[datetime, datetime] | None = None
        self.setMinimumHeight(HEAD_H + 24 * HOUR_H)
        self.setMouseTracking(True)

    def set_data(self, days, items, color, step, draft=None, bad=False, selected="") -> None:
        self.days, self.items, self.color, self.step = days, items, QColor(color or COLORS[0]), max(5, step)
        self.draft, self.draft_bad, self.selected = draft, bad, selected
        self.update()

    # geometry
    def _col_w(self) -> float:
        return (self.width() - LEFT_W) / max(1, len(self.days))

    def _y(self, t: datetime, day: date) -> float:
        mins = (t - datetime.combine(day, time())).total_seconds() / 60
        return HEAD_H + max(0, min(24 * 60, mins)) * HOUR_H / 60

    def _at(self, pos: QPoint) -> datetime | None:
        if pos.x() < LEFT_W or pos.y() < HEAD_H or not self.days:
            return None
        i = min(len(self.days) - 1, int((pos.x() - LEFT_W) // self._col_w()))
        mins = (pos.y() - HEAD_H) * 60 / HOUR_H
        mins = int(mins // self.step * self.step)
        return datetime.combine(self.days[i], time()) + timedelta(minutes=max(0, min(24 * 60 - self.step, mins)))

    def _rects(self, s: datetime, e: datetime) -> list[QRectF]:
        out = []
        for i, d in enumerate(self.days):
            ds, de = datetime.combine(d, time()), datetime.combine(d, time()) + timedelta(days=1)
            if s < de and ds < e:
                x = LEFT_W + i * self._col_w()
                out.append(QRectF(x + 3, self._y(max(s, ds), d), self._col_w() - 6,
                                  max(14, self._y(min(e, de), d) - self._y(max(s, ds), d)) - 2))
        return out

    def _hit(self, pos: QPoint) -> Reservation | None:
        for r in self.items:
            if any(rc.contains(pos) for rc in self._rects(r.start, r.end)):
                return r
        return None

    # painting
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        sec = tokens.text("label-secondary", "region-box")
        sep = tokens.color("separator")
        f = QFont(self.font())
        cw = self._col_w()
        today = date.today()
        for i, d in enumerate(self.days):
            x = LEFT_W + i * cw
            f.setBold(d == today)
            p.setFont(f)
            p.setPen(tokens.text("accent", "region-box") if d == today else sec)
            p.drawText(QRectF(x, 0, cw, HEAD_H), Qt.AlignCenter, f"{d.day} {WEEKDAYS[d.weekday()]}")
            p.setPen(QPen(sep, 1))
            p.drawLine(int(x), HEAD_H, int(x), self.height())
        f.setBold(False)
        p.setFont(f)
        for h in range(25):
            y = HEAD_H + h * HOUR_H
            p.setPen(QPen(sep, 1))
            p.drawLine(LEFT_W, y, self.width(), y)
            if h < 24:
                p.setPen(sec)
                p.drawText(QRect(0, y - 8, LEFT_W - 8, 16), Qt.AlignRight | Qt.AlignVCenter, f"{h}:00")
        now = datetime.now()
        if today in self.days:
            y = self._y(now, today)
            x = LEFT_W + self.days.index(today) * cw
            p.setPen(QPen(tokens.color("red"), 2))
            p.drawLine(int(x), int(y), int(x + cw), int(y))
        mine = me()
        for r in self.items:
            moving = self._preview is not None and self._drag and self._drag[0] == "move" and self._drag[1].id == r.id
            s, e = (self._preview if moving else (r.start, r.end))
            self._block(p, s, e, r.person, f"{s:%H:%M}–{e:%H:%M}" + (f" {r.purpose}" if r.purpose else ""),
                        strong=r.id == self.selected, mine=same_person(r.person, mine))
        draft = self._preview if (self._drag and self._drag[0] == "new") else self.draft
        if draft:
            s, e = draft
            self._block(p, s, e, "새 예약", f"{s:%H:%M}–{e:%H:%M}", strong=True, bad=self.draft_bad)
        p.end()

    def _block(self, p, s, e, title, sub, strong=False, mine=False, bad=False) -> None:
        for rc in self._rects(s, e):
            if bad:
                p.setBrush(tokens.tint("red", 0.12))
                p.setPen(QPen(tokens.color("red"), 2, Qt.DashLine))
                p.drawRoundedRect(rc, 6, 6)
                fg = tokens.text("red", "region-box")
            else:
                fill = QColor(self.color)
                fill.setAlphaF(0.9 if strong else (0.32 if mine else 0.2))
                p.setBrush(fill)
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(rc, 6, 6)
                bar = QRectF(rc.x(), rc.y(), 3, rc.height())
                p.setBrush(self.color)
                p.drawRect(bar)
                fg = QColor("white") if strong else tokens.color("label")
            p.setPen(fg)
            f = QFont(self.font())
            f.setBold(True)
            p.setFont(f)
            inner = rc.adjusted(8, 3, -4, -2)
            p.drawText(inner, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                       ("겹침 · 저장 안 됨\n" if bad else "") + title)
            f.setBold(False)
            p.setFont(f)
            p.drawText(inner.adjusted(0, 17 + (17 if bad else 0), 0, 0), Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap, sub)

    # mouse
    def mousePressEvent(self, e) -> None:
        if e.button() != Qt.LeftButton:
            return
        hit = self._hit(e.position().toPoint())
        at = self._at(e.position().toPoint())
        if hit is not None:
            self.picked.emit(hit)
            off = int((at - hit.start).total_seconds() // 60) if at else 0
            self._drag = ("move", hit, off)
            self._press = e.position().toPoint()
        elif at is not None and self.page.can_book():
            self._drag = ("new", at)
            self._preview = (at, at + timedelta(minutes=max(self.step, 60)))
            self.update()

    def mouseMoveEvent(self, e) -> None:
        if not self._drag:
            return
        at = self._at(e.position().toPoint())
        if at is None:
            return
        if self._drag[0] == "new":
            s = self._drag[1]
            end = at + timedelta(minutes=self.step)
            self._preview = (min(s, at), max(end, s + timedelta(minutes=self.step)))
        else:
            if (e.position().toPoint() - self._press).manhattanLength() < 6:
                return
            r, off = self._drag[1], self._drag[2]
            s = at - timedelta(minutes=off)
            self._preview = (s, s + (r.end - r.start))
        self.update()

    def mouseReleaseEvent(self, e) -> None:
        drag, prev = self._drag, self._preview
        self._drag, self._preview = None, None
        if drag and prev:
            if drag[0] == "new":
                self.create.emit(*prev)
            elif (prev[0], prev[1]) != (drag[1].start, drag[1].end):
                self.moved.emit(drag[1], *prev)
        elif drag is None and self._hit(e.position().toPoint()) is None:
            self.picked.emit(None)
        self.update()


class EquipmentDialog(QDialog):
    def __init__(self, eq: Equipment, names: list[str], parent=None):
        super().__init__(parent)
        self.eq = eq
        self.setWindowTitle("장비 편집" if eq.name else "장비 추가")
        lay = QFormLayout(self)
        self.name = QLineEdit(eq.name)
        self.name.setPlaceholderText("예: 초원심분리기")
        self.loc = QLineEdit(eq.location)
        self.owner = QComboBox()
        self.owner.setEditable(True)
        self.owner.addItems([""] + names)
        self.owner.setCurrentText(eq.owner)
        self.state = QComboBox()
        self.state.addItems(list(STATES))
        self.state.setCurrentText(eq.state)
        self.step = QSpinBox()
        self.step.setRange(5, 240)
        self.step.setSingleStep(5)
        self.step.setSuffix(" 분")
        self.step.setValue(eq.step)
        self.maxh = QDoubleSpinBox()
        self.maxh.setRange(0, 168)
        self.maxh.setSpecialValueText("제한 없음")
        self.maxh.setSuffix(" 시간")
        self.maxh.setValue(eq.max_hours)
        self.color_btn = QPushButton()
        self.color = eq.color or COLORS[0]
        self.color_btn.setIcon(dot_icon(self.color))
        self.color_btn.setText("색 바꾸기…")
        self.color_btn.clicked.connect(self._pick)
        self.note = QLineEdit(eq.note)
        lay.addRow("이름", self.name)
        lay.addRow("위치", self.loc)
        lay.addRow("담당자", self.owner)
        lay.addRow("상태", self.state)
        lay.addRow("예약 단위", self.step)
        lay.addRow("최대 예약", self.maxh)
        lay.addRow("색", self.color_btn)
        lay.addRow("메모", self.note)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addRow(bb)

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self.color), self, "장비 색")
        if c.isValid():
            self.color = c.name()
            self.color_btn.setIcon(dot_icon(self.color))

    def _ok(self) -> None:
        if not self.name.text().strip():
            QMessageBox.warning(self, "장비", "장비 이름을 적어 주세요.")
            return
        e = self.eq
        e.name, e.location, e.owner = self.name.text().strip(), self.loc.text().strip(), self.owner.currentText().strip()
        e.state, e.step, e.max_hours = self.state.currentText(), self.step.value(), self.maxh.value()
        e.color, e.note = self.color, self.note.text().strip()
        self.accept()


class ReservationInspector(QWidget):
    def __init__(self, page: "ReservationPage"):
        super().__init__()
        self.page = page
        self.res: Reservation | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(tokens.space("space-2"))
        bar = QHBoxLayout()
        self.heading = QLabel("새 예약")
        self.heading.setObjectName("sectionTitle")
        bar.addWidget(self.heading, 1)
        self.save_btn = QPushButton("저장")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self._save)
        bar.addWidget(self.save_btn)
        lay.addLayout(bar)
        self.sub = QLabel()
        self.sub.setObjectName("muted")
        self.sub.setWordWrap(True)
        lay.addWidget(self.sub)
        g = InsetGroup()
        self.eq = QComboBox()
        self.person = QComboBox()
        self.person.setEditable(True)
        self.day = QDateEdit()
        self.day.setCalendarPopup(True)
        self.day.setDisplayFormat("M월 d일 (ddd)")
        self.start = QTimeEdit()
        self.end = QTimeEdit()
        for t in (self.start, self.end):
            t.setDisplayFormat("HH:mm")
            t.setProperty("compact", True)
        g.add_row("장비", self.eq, expand=True)
        g.add_row("예약자", self.person, expand=True)
        g.add_row("날짜", self.day, expand=True)
        g.add_row("시작", self.start, expand=True)
        g.add_row("끝", self.end, expand=True)
        lay.addWidget(g)
        lay.addWidget(inset_title("용도"))
        self.purpose = QLineEdit()
        self.purpose.setPlaceholderText("예) 샘플 원심, 4 °C")
        lay.addWidget(self.purpose)
        lay.addWidget(inset_title("알림"))
        g2 = InsetGroup()
        self.notify = QComboBox()
        for m, label in ((0, "없음"), (10, "10분 전 메일"), (30, "30분 전 메일"), (60, "1시간 전 메일")):
            self.notify.addItem(label, m)
        g2.add_row("메일 알림", self.notify, expand=True)
        lay.addWidget(g2)
        self.warn = QLabel()
        self.warn.setObjectName("error")
        self.warn.setWordWrap(True)
        lay.addWidget(self.warn)
        lay.addWidget(inset_title("장비 정보"))
        self.info = InsetGroup()
        self.i_loc = QLabel("-")
        self.i_owner = QLabel("-")
        self.i_state = QLabel("-")
        self.info.add_row("위치", self.i_loc)
        self.info.add_row("담당자", self.i_owner)
        self.info.add_row("상태", self.i_state)
        lay.addWidget(self.info)
        row = QHBoxLayout()
        self.del_btn = QPushButton("예약 취소…")
        self.del_btn.setObjectName("destructive")
        icons.set_icon(self.del_btn, "trash")
        self.del_btn.clicked.connect(self._delete)
        row.addWidget(self.del_btn)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)
        for w in (self.start, self.end):
            w.timeChanged.connect(lambda _: self.page.preview(self.current()))
        self.day.dateChanged.connect(lambda _: self.page.preview(self.current()))
        self.eq.currentIndexChanged.connect(lambda _: self._eq_info())

    def fill(self, res: Reservation, is_new: bool) -> None:
        self.res = res
        self.heading.setText("새 예약" if is_new else "예약")
        self.sub.setText("시간표에서 끌어 만든 예약" if is_new else f"{res.person} · {res.when()}")
        self.eq.blockSignals(True)
        self.eq.clear()
        for e in self.page.board.equipment():
            self.eq.addItem(dot_icon(e.color, not e.available), e.name, e.id)
        self.eq.setCurrentIndex(max(0, self.eq.findData(res.equipment_id)))
        self.eq.blockSignals(False)
        self.person.clear()
        names = member_names(self.page.project)
        if me() and me() not in names:
            names = [me()] + names
        self.person.addItems(names)
        self.person.setCurrentText(res.person or me())
        for w in (self.day, self.start, self.end):
            w.blockSignals(True)
        self.day.setDate(QDate(res.start.year, res.start.month, res.start.day))
        self.start.setTime(QTime(res.start.hour, res.start.minute))
        self.end.setTime(QTime(res.end.hour, res.end.minute))
        for w in (self.day, self.start, self.end):
            w.blockSignals(False)
        self.purpose.setText(res.purpose)
        self.notify.setCurrentIndex(max(0, self.notify.findData(res.notify_min)))
        self.del_btn.setVisible(not is_new)
        self._eq_info()

    def _eq_info(self) -> None:
        eq = self.page.board.equipment_by_id(self.eq.currentData() or "") if self.page.board else None
        self.i_loc.setText(eq.location or "-" if eq else "-")
        self.i_owner.setText(eq.owner or "-" if eq else "-")
        self.i_state.setText(eq.state if eq else "-")

    def current(self) -> Reservation | None:
        if self.res is None:
            return None
        d = self.day.date().toPython()
        s = datetime.combine(d, self.start.time().toPython())
        e = datetime.combine(d, self.end.time().toPython())
        if e <= s:  # ends after midnight (overnight)
            e += timedelta(days=1)
        r = Reservation(self.res.id, self.eq.currentData() or "", self.eq.currentText(), self.person.currentText().strip(),
                        s, e, self.purpose.text().strip(), int(self.notify.currentData() or 0), self.res.created)
        return r

    def show_problem(self, text: str) -> None:
        self.warn.setText(text)
        self.warn.setVisible(bool(text))
        self.save_btn.setEnabled(not text)

    def _save(self) -> None:
        r = self.current()
        if r is not None:
            self.page.save(r)

    def _delete(self) -> None:
        if self.res is not None:
            self.page.delete(self.res)


class ReservationPage(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.board: ReservationBoard | None = None
        self.project = None
        self.eq_id = ""
        self.view = settings.load_settings().get("reservation_view", "week")
        self.day = date.today()
        self.draft: Reservation | None = None
        self.selected: Reservation | None = None
        root = QVBoxLayout(self)
        m = tokens.space("margin")
        root.setContentsMargins(m, tokens.space("space-2"), m, m)
        root.setSpacing(tokens.space("space-2"))
        head = QHBoxLayout()
        title = QLabel("장비 예약")
        title.setObjectName("pageTitle")
        head.addWidget(title)
        self.sub = QLabel()
        self.sub.setObjectName("muted")
        head.addWidget(self.sub, 1)
        add_eq = icons.set_icon(QPushButton("장비"), "plus")
        add_eq.setToolTip("관리할 장비 추가")
        add_eq.clicked.connect(self.add_equipment)
        head.addWidget(add_eq)
        self.seg = Segmented([("day", "일"), ("week", "주")])
        self.seg.set_current(self.view if self.view in ("day", "week") else "week")
        self.seg.changed.connect(self.set_view)
        head.addWidget(self.seg)
        prev = icons.set_icon(QPushButton(), "chevron.left", "이전")
        prev.clicked.connect(lambda: self.step(-1))
        today = QPushButton("오늘")
        today.clicked.connect(self.go_today)
        nxt = icons.set_icon(QPushButton(), "chevron.right", "다음")
        nxt.clicked.connect(lambda: self.step(1))
        head.addWidget(glass_capsule(prev, today, nxt))
        root.addLayout(head)
        body = QHBoxLayout()
        body.setSpacing(tokens.space("space-3"))
        left = QFrame()
        left.setObjectName("card")
        ll = QVBoxLayout(left)
        lt = QLabel("장비")
        lt.setObjectName("sectionTitle")
        ll.addWidget(lt)
        self.eq_list = QListWidget()
        self.eq_list.setFixedWidth(220)
        self.eq_list.currentItemChanged.connect(self._eq_changed)
        self.eq_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.eq_list.customContextMenuRequested.connect(self._eq_menu)
        self.eq_list.itemDoubleClicked.connect(lambda it: self.edit_equipment(it.data(Qt.UserRole)))
        ll.addWidget(self.eq_list, 1)
        hint = QLabel("우클릭: 장비 편집 · 점검 중으로 표시 · 삭제")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        ll.addWidget(hint)
        body.addWidget(left)
        center = QFrame()
        center.setObjectName("card")
        cl = QVBoxLayout(center)
        self.grid_title = QLabel()
        self.grid_title.setObjectName("sectionTitle")
        cl.addWidget(self.grid_title)
        self.grid = TimeGrid(self)
        self.grid.create.connect(self._create)
        self.grid.picked.connect(self._picked)
        self.grid.moved.connect(self._moved)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidget(self.grid)
        cl.addWidget(self.scroll, 1)
        self.empty = QLabel("‘+ 장비’로 관리할 장비를 먼저 추가하세요. 그다음 시간표를 끌어 예약합니다.")
        self.empty.setObjectName("muted")
        self.empty.setWordWrap(True)
        cl.addWidget(self.empty)
        body.addWidget(center, 1)
        root.addLayout(body, 1)

        self.inspector_panel = InspectorPanel(wanted=False)
        il = QVBoxLayout(self.inspector_panel)
        il.setContentsMargins(0, 0, 0, 0)
        self.inspector = ReservationInspector(self)
        il.addWidget(self.inspector)
        self.inspector_panel.setMinimumWidth(300)
        self.inspector_panel.setMaximumWidth(360)

    # ---- data ----------------------------------------------------------------------------
    def set_board(self, board: ReservationBoard | None, project) -> None:
        self.board = board
        self.project = project
        self.draft = self.selected = None
        self.inspector_panel.set_wanted(False)
        self.refresh()

    def refresh(self) -> None:
        eqs = self.board.equipment() if self.board else []
        self.eq_list.blockSignals(True)
        self.eq_list.clear()
        for e in eqs:
            it = QListWidgetItem(dot_icon(e.color, not e.available), f"{e.name}\n{e.state if not e.available else e.location}")
            it.setData(Qt.UserRole, e.id)
            if not e.available:
                it.setForeground(tokens.text("label-secondary"))
            self.eq_list.addItem(it)
            if e.id == self.eq_id:
                self.eq_list.setCurrentItem(it)
        if self.eq_list.currentRow() < 0 and eqs:
            self.eq_list.setCurrentRow(0)
            self.eq_id = eqs[0].id
        self.eq_list.blockSignals(False)
        days = self.days()
        week = [r for r in (self.board.between(datetime.combine(days[0], time()),
                                               datetime.combine(days[-1], time()) + timedelta(days=1))
                            if self.board else [])]
        self.sub.setText(f"장비 {len(eqs)}대 · 이번 {'주' if self.view == 'week' else '날'} 예약 {len(week)}건"
                         if self.board else "Google Drive 위치 세트를 열면 쓸 수 있습니다")
        self.empty.setVisible(not eqs)
        self._draw()
        self.changed.emit()

    def days(self) -> list[date]:
        if self.view == "day":
            return [self.day]
        start = self.day - timedelta(days=self.day.weekday())
        return [start + timedelta(days=i) for i in range(7)]

    def _draw(self) -> None:
        eq = self.board.equipment_by_id(self.eq_id) if self.board else None
        days = self.days()
        items = self.board.reservations(self.eq_id) if (self.board and eq) else []
        draft, bad = None, False
        if self.draft is not None and self.draft.equipment_id == self.eq_id:
            draft = (self.draft.start, self.draft.end)
            bad = bool(self.board.check(self.draft)) and bool(self.board.conflicts(self.draft))
        cur = self.inspector.current() if self.selected is not None else None
        if cur is not None and self.selected is not None:
            items = [cur if r.id == cur.id else r for r in items]
        if eq is not None:
            lim = f" · 최대 {eq.max_hours:g}시간" if eq.max_hours else ""
            self.grid_title.setText(f"{eq.name}  {days[0]:%Y년 %m월 %d일}" + (f" – {days[-1]:%d일}" if len(days) > 1 else "")
                                    + f" · {eq.step}분 단위{lim}" + (" · 점검 중" if not eq.available else ""))
        else:
            self.grid_title.setText("")
        self.grid.set_data(days, items, eq.color if eq else COLORS[0], eq.step if eq else 30, draft, bad,
                           self.selected.id if self.selected else "")

    def can_book(self) -> bool:
        eq = self.board.equipment_by_id(self.eq_id) if self.board else None
        if eq is None:
            return False
        if not eq.available:
            QMessageBox.information(self, "장비 예약", f"{eq.name}은(는) 점검 중이라 예약할 수 없습니다.")
            return False
        return True

    # ---- navigation ---------------------------------------------------------------------
    def set_view(self, view: str) -> None:
        self.view = view
        data = settings.load_settings()
        data["reservation_view"] = view
        settings.save_settings(data)
        self.refresh()

    def step(self, n: int) -> None:
        self.day += timedelta(days=n * (7 if self.view == "week" else 1))
        self.refresh()

    def go_today(self) -> None:
        self.day = date.today()
        self.refresh()
        self.scroll.verticalScrollBar().setValue(max(0, (datetime.now().hour - 2) * HOUR_H))

    def show_equipment(self, eid: str, when: datetime | None = None) -> None:
        self.eq_id = eid
        if when is not None:
            self.day = when.date()
        self.refresh()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if not getattr(self, "_scrolled", False):
            self._scrolled = True
            self.scroll.verticalScrollBar().setValue(8 * HOUR_H)

    def _eq_changed(self, cur, _prev) -> None:
        self.eq_id = cur.data(Qt.UserRole) if cur else ""
        self.draft = self.selected = None
        self.inspector_panel.set_wanted(False)
        self._draw()

    # ---- equipment ------------------------------------------------------------------------
    def add_equipment(self) -> None:
        if self.board is None:
            QMessageBox.information(self, "장비 예약", "Google Drive 위치 세트를 연 뒤에 쓸 수 있습니다.")
            return
        n = len(self.board.equipment())
        eq = Equipment(new_id(), "", color=COLORS[n % len(COLORS)])
        if EquipmentDialog(eq, member_names(self.project), self).exec():
            self.board.save_equipment(eq)
            self.eq_id = eq.id
            self.refresh()

    def edit_equipment(self, eid: str) -> None:
        eq = self.board.equipment_by_id(eid) if self.board else None
        if eq and EquipmentDialog(eq, member_names(self.project), self).exec():
            self.board.save_equipment(eq)
            self.refresh()

    def _eq_menu(self, pos) -> None:
        it = self.eq_list.itemAt(pos)
        if it is None or self.board is None:
            return
        eq = self.board.equipment_by_id(it.data(Qt.UserRole))
        if eq is None:
            return
        menu = QMenu(self)
        menu.addAction("장비 편집…", lambda: self.edit_equipment(eq.id))
        menu.addAction("사용 가능으로 표시" if not eq.available else "점검 중으로 표시", lambda: self._toggle(eq))
        menu.addSeparator()
        menu.addAction("장비 삭제…", lambda: self._delete_eq(eq))
        menu.exec(self.eq_list.mapToGlobal(pos))

    def _toggle(self, eq: Equipment) -> None:
        eq.state = "사용 가능" if not eq.available else "점검 중"
        self.board.save_equipment(eq)
        self.refresh()

    def _delete_eq(self, eq: Equipment) -> None:
        n = len(self.board.reservations(eq.id))
        if confirm_delete(self, "장비 삭제", f"‘{eq.name}’과 예약 {n}건을 삭제할까요?") != QMessageBox.Yes:
            return
        self.board.delete_equipment(eq.id)
        self.eq_id = ""
        self.refresh()

    # ---- bookings -------------------------------------------------------------------------
    def _create(self, s: datetime, e: datetime) -> None:
        eq = self.board.equipment_by_id(self.eq_id)
        self.draft = Reservation(new_id(), self.eq_id, eq.name if eq else "", me(), s, e)
        self.selected = self.draft
        self.inspector.fill(self.draft, True)
        self.inspector_panel.set_wanted(True)
        self.preview(self.draft)

    def _picked(self, r: Reservation | None) -> None:
        if r is None:
            self.draft = self.selected = None
            self.inspector_panel.set_wanted(False)
            self._draw()
            return
        self.draft = None
        self.selected = r
        self.inspector.fill(r, False)
        self.inspector_panel.set_wanted(True)
        self.preview(r)

    def _moved(self, r: Reservation, s: datetime, e: datetime) -> None:
        moved = Reservation(r.id, r.equipment_id, r.equipment, r.person, s, e, r.purpose, r.notify_min, r.created)
        problem = self.board.check(moved)
        if problem:
            QMessageBox.warning(self, "장비 예약", f"옮기지 못했습니다. {problem}")
            self._draw()
            return
        self.board.save_reservation(moved)
        self.selected = moved
        self.inspector.fill(moved, False)
        self.refresh()

    def preview(self, r: Reservation | None) -> None:
        if r is None or self.board is None:
            return
        problem = self.board.check(r)
        self.inspector.show_problem(problem)
        if self.draft is not None and r.id == self.draft.id:
            self.draft = r
        self._draw()

    def save(self, r: Reservation) -> None:
        problem = self.board.check(r)
        if problem:
            self.inspector.show_problem(problem)
            return
        self.board.save_reservation(r)
        self.draft = None
        self.selected = None
        self.inspector_panel.set_wanted(False)
        data = settings.load_settings()
        if r.person and not data.get("requester_name"):
            data["requester_name"] = r.person
            settings.save_settings(data)
        self.refresh()

    def delete(self, r: Reservation) -> None:
        if confirm_delete(self, "예약 취소", f"{r.person}님의 {r.equipment} 예약({r.when()})을 취소할까요?") != QMessageBox.Yes:
            return
        self.board.delete_reservation(r.id)
        self.selected = None
        self.inspector_panel.set_wanted(False)
        self.refresh()


class ReservationCard(QFrame):
    """Dashboard: one row per machine — who is using it now (until), the next booking."""
    open_equipment = Signal(str)
    open_tab = Signal()
    COLS = ["장비", "지금", "다음 예약", "시간"]

    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*[tokens.space("card-pad")] * 4)
        head = QHBoxLayout()
        t = QLabel("장비 예약")
        t.setObjectName("sectionTitle")
        head.addWidget(t)
        head.addStretch(1)
        more = QPushButton("오늘 · 장비 예약 탭 열기")
        more.setObjectName("plain")
        more.clicked.connect(self.open_tab.emit)
        head.addWidget(more)
        lay.addLayout(head)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        compact_rows(self.table)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.cellClicked.connect(lambda r, _c: self.open_equipment.emit(self._ids[r]))
        lay.addWidget(self.table)
        self._ids: list[str] = []

    def set_board(self, board: ReservationBoard | None) -> None:
        eqs = board.equipment() if board else []
        self.setVisible(board is not None and bool(eqs))
        self.table.setRowCount(len(eqs))
        self._ids = [e.id for e in eqs]
        now = datetime.now()
        for r, e in enumerate(eqs):
            res = board.reservations(e.id)
            cur = next((x for x in res if x.start <= now < x.end), None)
            nxt = next((x for x in res if x.start > now), None)
            if not e.available:
                now_text = "점검 중"
            else:
                now_text = f"{cur.person} · ~{cur.end:%H:%M}" if cur else "비어 있음"
            vals = [e.name, now_text, nxt.person if nxt else "—",
                    (nxt.when() if nxt and nxt.start.date() != now.date() else
                     f"{nxt.start:%H:%M}–{nxt.end:%H:%M}") if nxt else ""]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 1 and not e.available:
                    it.setForeground(tokens.text("orange"))
                elif c == 1 and not cur:
                    it.setForeground(tokens.text("label-secondary"))
                self.table.setItem(r, c, it)
        self.table.setFixedHeight(self.table.horizontalHeader().height() + max(1, len(eqs)) * self.table.verticalHeader().defaultSectionSize() + 4)
