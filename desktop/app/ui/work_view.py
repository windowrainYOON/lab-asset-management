"""Work (업무) tab: projects with people in charge (components: PageLayout, DragDrop, Table,
SettingsPane, Button, Select, ContextMenu; Desktop profile, tahoe style).

    업무                                                        [+ 새 프로젝트 ▾]
    ┌ projects ┐  청소 당번 · 당번제 · 매주                        [⚙]
    │ 청소 당번 │  담당자 순번   ⠿ 1 A   ⠿ 2 B   ⠿ 3 C   [+]      <- drag to reorder
    │ 논문      │  당번 배정     기준일 [11/2]  첫 담당자 B ◇  주기 매주 ◇  개수 [12]
    └──────────┘               [🗑]                 [이 날 이후 다시 정렬] [순번대로 채우기]
                  ‹ 2026년 11월 ›  (오늘)
                  month grid: one chip per task (순번 · 이름); drag a chip to another day
                  to move it, drop a person on a chip to hand it over; right-click for the
                  same actions (keyboard/menu path, components/DragDrop).

WorkController holds the board and the undo stack; the Work tab, the dashboard boxes and
the 일정 calendar all edit through it, so they stay in step.
"""
from __future__ import annotations

import copy
from dataclasses import replace as dc_replace
from datetime import date, timedelta

from PySide6.QtCore import QByteArray, QMimeData, QObject, QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QFontMetrics, QKeySequence, QPainter, QPainterPath, QPen, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QHeaderView,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSpinBox, QStyledItemDelegate, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import work as W
from .alerts import confirm_delete
from .inset import InsetGroup, inset_title
from .widgets import FlowLayout
from .table_model import compact_rows
from .theme import icons, tokens

MIME_PERSON = "application/x-asset-manager-person"
MIME_TASK = "application/x-asset-manager-task"
DRAG_START = 4          # px: less is a click (components/DragDrop)
WORK_CAL = "work"       # the 일정 tab shows tasks as this pseudo calendar
WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"]


def project_color(board: W.WorkBoard | None, pid: str) -> QColor:
    ids = [p.id for p in board.projects] if board else []
    return tokens.chart_color(ids.index(pid) % 6 if pid in ids else 0)


def _qdate(d: date):
    from PySide6.QtCore import QDate
    return QDate(d.year, d.month, d.day)


def _pydate(q) -> date:
    return date(q.year(), q.month(), q.day())


def _font(style: str, weight: int | None = None, strike: bool = False) -> QFont:
    st = tokens.type_style(style)
    f = QFont()
    f.setPixelSize(st["size"])
    f.setWeight(QFont.Weight(weight or st["weight"]))
    f.setStrikeOut(strike)
    return f


# ---- controller --------------------------------------------------------------------
class WorkController(QObject):
    """One place that edits the work file: every change is undoable (⌘Z) and announced."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.board: W.WorkBoard | None = None
        self.undo_stack: list[tuple[list, list]] = []
        self.parent_widget = None

    def set_board(self, board: W.WorkBoard | None) -> None:
        self.board = board
        self.undo_stack = []
        self.changed.emit()

    # -- reading --
    def projects(self) -> list[W.WorkProject]:
        return list(self.board.projects) if self.board else []

    def tasks(self, pid: str | None = None) -> list[W.WorkTask]:
        if not self.board:
            return []
        return W.of_project(self.board.tasks, pid) if pid else list(self.board.tasks)

    def project(self, pid: str) -> W.WorkProject | None:
        return self.board.project(pid) if self.board else None

    def task(self, tid: str) -> W.WorkTask | None:
        return next((t for t in self.tasks() if t.id == tid), None)

    def rank(self, p: W.WorkProject, person: str) -> int | None:
        """1-based place of a person in the project's rotation."""
        return p.members.index(person) + 1 if person in p.members else None

    def who(self, p: W.WorkProject | None, t: W.WorkTask | None, sep: str = ". ") -> str:
        """"1. 홍길동" or, for a turn shared by several, "1·2. 홍길동, 김다은"."""
        if t is None:
            return "—"
        names = t.people
        if not names:
            return "미정"
        ranks = [self.rank(p, n) for n in names] if p is not None and p.kind == "rotation" else []
        ranks = [str(r) for r in ranks if r]
        return (f"{'·'.join(ranks)}{sep}" if ranks else "") + ", ".join(names)

    # -- writing --
    def _run(self, change, message: str = "업무를 저장하는 중…", undoable: bool = True) -> bool:
        if self.board is None:
            return False
        before = (copy.deepcopy(self.board.projects), copy.deepcopy(self.board.tasks))
        try:
            self.board.save(change)  # local at once; sent to Drive with the next sync
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self.parent_widget, "업무", f"저장하지 못했습니다.\n{exc}")
            return False
        if undoable:
            self.undo_stack.append(before)
            del self.undo_stack[:-50]
        self.changed.emit()
        return True

    def undo(self) -> None:
        if not self.undo_stack or self.board is None:
            return
        projects, tasks = self.undo_stack.pop()
        self._run(lambda _p, _t: (copy.deepcopy(projects), copy.deepcopy(tasks)), "되돌리는 중…", undoable=False)

    def upsert_project(self, p: W.WorkProject) -> None:
        self._run(lambda ps, ts: ([x for x in ps if x.id != p.id] + [p] if all(x.id != p.id for x in ps)
                                  else [p if x.id == p.id else x for x in ps], ts))

    def delete_project(self, pid: str) -> None:
        self._run(lambda ps, ts: ([x for x in ps if x.id != pid], [t for t in ts if t.project_id != pid]))

    def set_members(self, pid: str, members: list[str]) -> None:
        def change(ps, ts):
            return [dc_replace(x, members=list(members)) if x.id == pid else x for x in ps], ts
        self._run(change)

    def fill(self, pid: str, first: str, start: date, until: tuple[int, int], freq: str,
             per_turn: int | None = None) -> None:
        """Lay the rotation out from `start` through the end of the half `until` (연도, 반기)."""
        def change(ps, ts):
            ps = [dc_replace(x, freq=freq, until=W.format_half(*until), start=start.isoformat(),
                             per_turn=x.per_turn if per_turn is None else per_turn) if x.id == pid else x for x in ps]
            p = next(x for x in ps if x.id == pid)
            return ps, W.fill_rotation(p, ts, first, start, W.half_end(*until))
        self._run(change)

    def resort(self, pid: str, start: date) -> None:
        def change(ps, ts):
            p = next(x for x in ps if x.id == pid)
            return ps, W.resort_from(p, ts, start)
        self._run(change)

    def delete_from(self, pid: str, start: date) -> None:
        self._run(lambda ps, ts: (ps, W.delete_from(pid, ts, start)))

    def add_task(self, t: W.WorkTask) -> None:
        self._run(lambda ps, ts: (ps, ts + [t]))

    def delete_task(self, tid: str) -> None:
        self._run(lambda ps, ts: (ps, [t for t in ts if t.id != tid]))

    def set_done(self, tid: str, done: bool = True) -> None:
        self._run(lambda ps, ts: (ps, W.set_done(ts, tid, done)))

    def move(self, tid: str, day: date) -> None:
        self._run(lambda ps, ts: (ps, W.move(ts, tid, day)))

    def assign(self, tid: str, person: str) -> None:
        self._run(lambda ps, ts: (ps, W.assign(ts, tid, person)))

    def toggle_person(self, tid: str, person: str) -> None:
        self._run(lambda ps, ts: (ps, W.toggle_person(ts, tid, person)))

    def drop_person(self, tid: str, person: str) -> None:
        """A person dropped on a task: replaces the one in charge, or for a turn shared by
        several people (회차 인원 2+) joins it while there is room."""
        t = self.task(tid)
        p = self.project(t.project_id) if t else None
        if t is None or person in t.people:
            return
        if p is not None and p.per_turn > 1 and len(t.people) < p.per_turn:
            self._run(lambda ps, ts: (ps, W.add_person(ts, tid, person)))
        else:
            self.assign(tid, person)

    def rename_task(self, tid: str, title: str) -> None:
        self._run(lambda ps, ts: (ps, [dc_replace(t, title=title) if t.id == tid else t for t in ts]))

    # -- for the 일정 calendar --
    def calendar_events(self) -> list:
        from ..integrations.gcalendar import Event
        out = []
        for p in self.projects():
            col = project_color(self.board, p.id).name()
            for t in self.tasks(p.id):
                d = t.day
                if d is None:
                    continue
                name = t.title or p.duty()
                out.append(Event(id=f"work:{t.id}", title=f"{'✓ ' if t.done else ''}{name} · {', '.join(t.people) or '미정'}",
                                 start=d, end=d, all_day=True, description=f"{p.name} ({p.kind_label})",
                                 calendar_id=WORK_CAL, calendar_name="업무", color=col,
                                 extra={"work_task": t.id, "work_project": p.id}))
        return out


# ---- menus shared by the month view and the dashboard -----------------------------------
def task_menu(ctrl: WorkController, t: W.WorkTask, parent: QWidget, pos) -> None:
    """ContextMenu: most used first, the destructive one last in red (components/ContextMenu)."""
    p = ctrl.project(t.project_id)
    menu = QMenu(parent)
    menu.addAction("완료 취소" if t.done else "업무 완료", lambda: ctrl.set_done(t.id, not t.done))
    shared = p is not None and (p.per_turn > 1 or len(t.people) > 1)
    people = menu.addMenu("담당자 (여러 명 선택)" if shared else "담당자 바꾸기")
    for m in (p.members if p else []):
        a = people.addAction(m, (lambda m=m: ctrl.toggle_person(t.id, m)) if shared else (lambda m=m: ctrl.assign(t.id, m)))
        a.setCheckable(True)
        a.setChecked(m in t.people)
    menu.addAction("날짜 바꾸기…", lambda: _ask_date(ctrl, t, parent))
    if p and p.kind == "milestone":
        menu.addAction("이름 바꾸기…", lambda: _ask_title(ctrl, t, parent))
    if p and p.kind == "rotation":
        menu.addAction("이 날부터 순번대로 다시 정렬", lambda: ctrl.resort(p.id, t.day))
    menu.addSeparator()
    menu.addAction("업무 삭제", lambda: ctrl.delete_task(t.id))
    menu.exec(pos)


def _ask_date(ctrl, t, parent) -> None:
    dlg = QDialog(parent)
    dlg.setWindowTitle("날짜 바꾸기")
    lay = QVBoxLayout(dlg)
    edit = QDateEdit(_qdate(t.day or date.today()))
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("yyyy-MM-dd")
    lay.addWidget(edit)
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.accepted.connect(dlg.accept)
    bb.rejected.connect(dlg.reject)
    lay.addWidget(bb)
    if dlg.exec():
        ctrl.move(t.id, _pydate(edit.date()))


def _ask_title(ctrl, t, parent) -> None:
    text, ok = QInputDialog.getText(parent, "이름 바꾸기", "마일스톤 이름", text=t.title)
    if ok and text.strip():
        ctrl.rename_task(t.id, text.strip())


# ---- people chips (drag to reorder / drop on a task to hand it over) ----------------------
class PersonChip(QFrame):
    """⠿ 2 B: the grip is always visible at the start (components/DragDrop)."""

    def __init__(self, row: "PeopleRow", name: str, rank: int):
        super().__init__()
        self.row, self.name, self.rank = row, name, rank
        self.setObjectName("personChip")
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip(f"{name}: 끌어서 순번을 바꾸거나, 달력의 업무 위에 놓아 담당자를 바꿉니다")
        self.setAccessibleName(f"이동: {name}")
        self.setFocusPolicy(Qt.StrongFocus)
        self._press: QPointF | None = None
        h = QHBoxLayout(self)
        h.setContentsMargins(tokens.space("space-1"), 0, tokens.space("control-pad"), 0)
        h.setSpacing(tokens.space("gap-tight"))
        grip = QLabel()
        grip.setPixmap(_grip_pixmap())
        num = QLabel(str(rank))
        num.setObjectName("chipRank")
        num.setFont(tokens.tabular(num.font()))
        lab = QLabel(name)
        for w in (grip, num, lab):
            h.addWidget(w)
        self.setFixedHeight(tokens.space("control-md"))

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.LeftButton:
            self._press = e.position()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e) -> None:
        if self._press is None or (e.position() - self._press).manhattanLength() < DRAG_START:
            return
        self._press = None
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(MIME_PERSON, QByteArray(self.name.encode("utf-8")))
        drag.setMimeData(mime)
        drag.setPixmap(_ghost(self))
        drag.setHotSpot(QPoint(self.width() // 2, self.height() // 2))
        self.setProperty("dragging", True)
        self._repolish()
        drag.exec(Qt.MoveAction | Qt.CopyAction)
        self.setProperty("dragging", False)
        self._repolish()

    def mouseReleaseEvent(self, e) -> None:
        self._press = None
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e) -> None:  # keyboard path: ← → move one place
        if e.key() in (Qt.Key_Left, Qt.Key_Right):
            self.row.nudge(self.name, -1 if e.key() == Qt.Key_Left else 1)
            return
        super().keyPressEvent(e)

    def contextMenuEvent(self, e) -> None:
        menu = QMenu(self)
        menu.addAction("앞으로 이동", lambda: self.row.nudge(self.name, -1))
        menu.addAction("뒤로 이동", lambda: self.row.nudge(self.name, 1))
        menu.addAction("맨 앞으로", lambda: self.row.place(self.name, 0))
        menu.addAction("맨 뒤로", lambda: self.row.place(self.name, len(self.row.names)))
        menu.addSeparator()
        menu.addAction("프로젝트에서 빼기", lambda: self.row.remove(self.name))
        menu.exec(e.globalPos())

    def _repolish(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)


def _grip_pixmap():
    from PySide6.QtGui import QPixmap
    w, h, r = 8, 14, 2.0
    pm = QPixmap(w * 2, h * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(tokens.text("label-secondary", "region-box", large=True))
    for cx in (2, 6):
        for cy in (3, 7, 11):
            p.drawEllipse(QPointF(cx, cy), r / 2 + 0.2, r / 2 + 0.2)
    p.end()
    return pm


def _ghost(w: QWidget):
    """The lifted copy: opacity .96 (the floating shadow is the system drag image's)."""
    from PySide6.QtGui import QPixmap
    src = w.grab()
    pm = QPixmap(src.size())
    pm.setDevicePixelRatio(src.devicePixelRatio())
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setOpacity(0.96)
    p.drawPixmap(0, 0, src)
    p.end()
    return pm


class PeopleRow(QWidget):
    """The project's rotation order as chips; a drop between chips shows a 2px accent line
    with a 6px end dot (components/DragDrop)."""
    reordered = Signal(list)
    removed = Signal(str)
    add_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.names: list[str] = []
        self.setAcceptDrops(True)
        self._line: tuple[int, int, int] | None = None  # x, top, height of the drop line
        self.lay = FlowLayout(self)  # wraps onto more rows when the inspector is narrow
        self.add_btn = icons.set_icon(QPushButton(), "plus", "담당자 추가")
        self.add_btn.clicked.connect(self.add_clicked.emit)
        self.chips: list[PersonChip] = []

    def set_names(self, names: list[str]) -> None:
        self.names = list(names)
        while self.lay.count():
            it = self.lay.takeAt(0)
            if it.widget() and it.widget() is not self.add_btn:
                it.widget().hide()
                it.widget().setParent(None)
                it.widget().deleteLater()
        self.chips = [PersonChip(self, n, i + 1) for i, n in enumerate(self.names)]
        for c in self.chips:
            self.lay.addWidget(c)
        self.lay.addWidget(self.add_btn)
        self.add_btn.show()
        self.updateGeometry()

    def nudge(self, name: str, step: int) -> None:
        self.place(name, self.names.index(name) + step)

    def place(self, name: str, index: int) -> None:
        new = W.reorder(self.names, name, index)
        if new != self.names:
            self.reordered.emit(new)

    def remove(self, name: str) -> None:
        self.removed.emit(name)

    def _index_at(self, pos) -> int:
        """The insertion place under the pointer (chips wrap, so by row, then by x)."""
        x, y = pos.x(), pos.y()
        for i, c in enumerate(self.chips):
            g = c.geometry()
            if y < g.top():
                return i
            if g.top() <= y <= g.bottom() and x < g.center().x():
                return i
        return len(self.chips)

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasFormat(MIME_PERSON):
            e.acceptProposedAction()

    def dragMoveEvent(self, e) -> None:
        if not e.mimeData().hasFormat(MIME_PERSON):
            return
        i = self._index_at(e.position())
        gap = tokens.space("gap-tight") // 2
        if i < len(self.chips):
            g = self.chips[i].geometry()
            self._line = (g.left() - gap, g.top(), g.height())
        elif self.chips:
            g = self.chips[-1].geometry()
            self._line = (g.right() + gap, g.top(), g.height())
        else:
            self._line = (0, 0, tokens.space("control-md"))
        e.acceptProposedAction()
        self.update()

    def dragLeaveEvent(self, _e) -> None:
        self._line = None
        self.update()

    def dropEvent(self, e) -> None:
        name = bytes(e.mimeData().data(MIME_PERSON)).decode("utf-8")
        i = self._index_at(e.position())
        self._line = None
        self.update()
        if name in self.names:
            old = self.names.index(name)
            self.place(name, i - 1 if i > old else i)
        else:
            new = list(self.names)
            new.insert(i, name)
            self.reordered.emit(new)
        e.acceptProposedAction()

    def paintEvent(self, e) -> None:
        super().paintEvent(e)
        if self._line is None:
            return
        x, top, h = self._line
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = tokens.color("accent")
        p.setPen(QPen(c, 2))
        p.drawLine(QPointF(x, top + 3), QPointF(x, top + h))
        p.setBrush(c)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(x, top + 3), 3, 3)
        p.end()


# ---- month grid of tasks ---------------------------------------------------------------
class WorkMonth(QWidget):
    """A month of one project's tasks. Chips: 순번 · 이름; this turn = accent fill, the next
    one = accent outline, done = struck through in label-secondary."""
    task_moved = Signal(str, object)     # task id, date
    task_assigned = Signal(str, str)     # task id, person
    day_dropped = Signal(object, str)    # date, person (a person dropped on an empty day)
    task_menu = Signal(str, object)      # task id, global pos
    day_activated = Signal(object)       # date (double click on an empty day)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self.month = date.today().replace(day=1)
        self.tasks: list[W.WorkTask] = []
        self.ranks: dict[str, int] = {}
        self.current_id = self.next_id = ""
        self.color = tokens.color("accent")
        self._chips: list[tuple[QRectF, W.WorkTask]] = []
        self._press: tuple[QPointF, W.WorkTask] | None = None
        self._target: tuple[date | None, str | None, bool] | None = None  # day, task id, valid
        self.setMinimumHeight(6 * 4 * tokens.space("row-compact"))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def _who(self, t: W.WorkTask) -> str:
        ranks = [str(self.ranks[n]) for n in t.people if n in self.ranks]
        return (f"{'·'.join(ranks)} · " if ranks else "") + (", ".join(t.people) or "미정")

    def set_tasks(self, tasks, ranks, current_id, next_id, color) -> None:
        self.tasks, self.ranks = tasks, ranks
        self.current_id, self.next_id, self.color = current_id, next_id, color
        self.update()

    def grid_start(self) -> date:
        first = self.month
        return first - timedelta(days=(first.weekday() + 1) % 7)  # weeks start on Sunday

    def _geom(self):
        head = tokens.space("row-compact")
        cw = self.width() / 7
        ch = (self.height() - head) / 6
        return head, cw, ch

    def _cell(self, d: date) -> QRectF:
        head, cw, ch = self._geom()
        i = (d - self.grid_start()).days
        return QRectF((i % 7) * cw, head + (i // 7) * ch, cw, ch)

    def _day_at(self, pos: QPointF) -> date | None:
        head, cw, ch = self._geom()
        if pos.y() < head:
            return None
        col, row = int(pos.x() // cw), int((pos.y() - head) // ch)
        if not (0 <= col < 7 and 0 <= row < 6):
            return None
        return self.grid_start() + timedelta(days=row * 7 + col)

    def _chip_at(self, pos: QPointF) -> W.WorkTask | None:
        return next((t for r, t in self._chips if r.contains(pos)), None)

    # -- painting --
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        head, cw, ch = self._geom()
        sec = tokens.text("label-secondary", "region-box")
        p.setFont(_font("mac-footnote", 600))
        p.setPen(sec)
        for i, wd in enumerate(WEEKDAYS):
            p.drawText(QRectF(i * cw, 0, cw, head), Qt.AlignCenter, wd)
        line = tokens.color("separator")
        today = date.today()
        start = self.grid_start()
        by_day: dict[date, list[W.WorkTask]] = {}
        for t in self.tasks:
            if t.day:
                by_day.setdefault(t.day, []).append(t)
        self._chips = []
        row_h = tokens.space("row-compact")
        pad = tokens.space("space-1")
        for i in range(42):
            d = start + timedelta(days=i)
            r = self._cell(d)
            inside = d.month == self.month.month
            p.setPen(QPen(line, 1))
            p.setBrush(tokens.color("region-box") if inside else tokens.color("region-canvas"))
            p.drawRect(r)
            # day number (today = accent circle, white digits)
            num = QRectF(r.left() + pad, r.top() + pad, row_h, row_h - 4)
            p.setFont(_font("mac-footnote", 600 if d == today else None))
            if d == today:
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(tokens.fill_for_text("accent")))
                p.drawEllipse(num.center(), (row_h - 4) / 2, (row_h - 4) / 2)
                p.setPen(tokens.on_color("accent"))
            else:
                p.setPen(tokens.color("label") if inside else sec)
            p.drawText(num, Qt.AlignCenter, str(d.day))
            # drop target: 2px accent outline + 8% tint (red when it can't be dropped)
            if self._target and self._target[0] == d and self._target[1] is None:
                c = tokens.color("accent" if self._target[2] else "red")
                p.setBrush(tokens.mix(tokens.color("region-box"), c, 0.08))
                p.setPen(QPen(c, 2))
                p.drawRoundedRect(r.adjusted(1, 1, -1, -1), tokens.radius("field"), tokens.radius("field"))
            y = r.top() + row_h
            for t in sorted(by_day.get(d, []), key=lambda t: (t.done, t.id)):
                cr = QRectF(r.left() + pad, y, r.width() - 2 * pad, row_h - 2)
                if cr.bottom() > r.bottom() - 1:
                    break
                self._paint_chip(p, cr, t)
                self._chips.append((cr, t))
                y += row_h
        p.end()

    def _paint_chip(self, p: QPainter, r: QRectF, t: W.WorkTask) -> None:
        rad = tokens.radius("value")
        bg = tokens.color("region-box")
        label = self._who(t)
        if t.title and t.title not in label and self.ranks == {}:
            label = f"{t.title} · {label}"
        is_target = self._target and self._target[1] == t.id
        if t.id == self.current_id:
            fill = QColor(tokens.fill_for_text("accent"))
            fg = tokens.on_color("accent")
        else:
            fill = tokens.mix(bg, self.color, 0.16 if not t.done else 0.06)
            fg = tokens.color("label") if not t.done else tokens.text("label-secondary", "region-box")
        path = QPainterPath()
        path.addRoundedRect(r, rad, rad)
        p.fillPath(path, fill)
        if t.id == self.next_id or is_target:
            p.setPen(QPen(tokens.color("accent"), 2 if is_target else 1))
            p.drawPath(path)
        p.setPen(fg)
        p.setFont(_font("mac-footnote", 600 if t.id == self.current_id else None, strike=t.done))
        fm = QFontMetrics(p.font())
        inner = r.adjusted(tokens.space("space-1") + 2, 0, -tokens.space("space-1"), 0)
        p.drawText(inner, Qt.AlignVCenter | Qt.AlignLeft, fm.elidedText(label, Qt.ElideRight, int(inner.width())))

    # -- mouse: press + 4px = drag a chip --
    def mousePressEvent(self, e) -> None:
        t = self._chip_at(e.position())
        self._press = (e.position(), t) if (t is not None and e.button() == Qt.LeftButton) else None

    def mouseMoveEvent(self, e) -> None:
        t = self._chip_at(e.position())
        self.setCursor(Qt.OpenHandCursor if t else Qt.ArrowCursor)
        if t is not None:
            self.setToolTip(f"{t.date} · {self._who(t)}"
                            + (" (완료)" if t.done else "") + "\n끌어서 날짜를 바꾸고, 우클릭하면 메뉴가 열립니다")
        if self._press is None or (e.position() - self._press[0]).manhattanLength() < DRAG_START:
            return
        _, task = self._press
        self._press = None
        rect = next(r for r, x in self._chips if x.id == task.id)
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(MIME_TASK, QByteArray(task.id.encode()))
        drag.setMimeData(mime)
        from PySide6.QtGui import QPixmap
        src = self.grab(rect.toAlignedRect())
        ghost = QPixmap(src.size())
        ghost.setDevicePixelRatio(src.devicePixelRatio())
        ghost.fill(Qt.transparent)
        gp = QPainter(ghost)
        gp.setOpacity(0.96)
        gp.drawPixmap(0, 0, src)
        gp.end()
        drag.setPixmap(ghost)
        drag.setHotSpot((e.position() - rect.topLeft()).toPoint())
        drag.exec(Qt.MoveAction)

    def mouseReleaseEvent(self, _e) -> None:
        self._press = None

    def mouseDoubleClickEvent(self, e) -> None:
        t = self._chip_at(e.position())
        if t is None:
            d = self._day_at(e.position())
            if d:
                self.day_activated.emit(d)

    def contextMenuEvent(self, e) -> None:
        t = self._chip_at(QPointF(e.pos()))
        if t is not None:
            self.task_menu.emit(t.id, e.globalPos())

    # -- drops --
    def _update_target(self, e) -> None:
        pos = e.position()
        md = e.mimeData()
        if md.hasFormat(MIME_TASK):
            d = self._day_at(pos)
            self._target = (d, None, d is not None)
        elif md.hasFormat(MIME_PERSON):
            t = self._chip_at(pos)
            d = self._day_at(pos)
            self._target = (d, t.id if t else None, d is not None)
        else:
            self._target = None
        self.update()

    def dragEnterEvent(self, e) -> None:
        if e.mimeData().hasFormat(MIME_TASK) or e.mimeData().hasFormat(MIME_PERSON):
            e.acceptProposedAction()
            self._update_target(e)

    def dragMoveEvent(self, e) -> None:
        self._update_target(e)
        if self._target and self._target[2]:
            e.acceptProposedAction()
        else:
            e.ignore()

    def dragLeaveEvent(self, _e) -> None:
        self._target = None
        self.update()

    def dropEvent(self, e) -> None:
        target, self._target = self._target, None
        self.update()
        if not target or not target[2]:
            return
        md = e.mimeData()
        if md.hasFormat(MIME_TASK):
            self.task_moved.emit(bytes(md.data(MIME_TASK)).decode(), target[0])
        elif md.hasFormat(MIME_PERSON):
            person = bytes(md.data(MIME_PERSON)).decode("utf-8")
            if target[1]:
                self.task_assigned.emit(target[1], person)
            else:
                self.day_dropped.emit(target[0], person)
        e.acceptProposedAction()


class HalfStepper(QSpinBox):
    """상반기까지 / 하반기까지 as a two-step stepper (1, 2); typing 상 or 하 works too."""

    def __init__(self):
        super().__init__()
        self.setRange(1, 2)
        self.setWrapping(False)

    def textFromValue(self, v: int) -> str:
        return W.HALVES.get(v, "") + "까지"

    def valueFromText(self, text: str) -> int:
        return 2 if ("하" in text or "2" in text) else 1

    def validate(self, text, pos):
        from PySide6.QtGui import QValidator
        t = text.strip()
        if t in (h + "까지" for h in W.HALVES.values()):
            return QValidator.Acceptable, text, pos
        if t and all(c in "상하반기까지12" for c in t):
            return QValidator.Intermediate, text, pos
        return QValidator.Invalid, text, pos

    def fixup(self, text: str) -> str:
        return self.textFromValue(self.valueFromText(text))


# ---- dialogs ---------------------------------------------------------------------------
class ProjectDialog(QDialog):
    """New / edit project (SettingsPane groups)."""

    def __init__(self, parent, lab_members: list[str], project: W.WorkProject | None = None,
                 kind: str = "rotation"):
        super().__init__(parent)
        self.project = project
        self.kind = project.kind if project else kind
        self.setWindowTitle(("프로젝트 설정" if project else f"새 {W.KINDS[self.kind]} 프로젝트"))
        self.setMinimumWidth(tokens.space("inspector-width") * 2)
        lay = QVBoxLayout(self)
        lay.setSpacing(tokens.space("gap-group"))
        g = InsetGroup()
        self.name = QLineEdit(project.name if project else "")
        self.name.setPlaceholderText("예: 실험실 청소" if self.kind == "rotation" else "예: 논문 투고")
        g.add_row("이름", self.name, expand=True)
        self.duty = QLineEdit(project.task_title if project else "")
        self.duty.setPlaceholderText("비우면 프로젝트 이름")
        if self.kind == "rotation":
            g.add_row("업무 이름", self.duty, expand=True)
        lay.addWidget(inset_title(f"{W.KINDS[self.kind]} 프로젝트"))
        lay.addWidget(g)

        lay.addWidget(inset_title("담당자 (체크한 순서가 처음 순번)"))
        self.people = QListWidget()
        self.people.setMinimumHeight(5 * tokens.space("row"))
        chosen = list(project.members) if project else []
        self.order: list[str] = list(chosen)
        for n in chosen + [m for m in lab_members if m not in chosen]:
            it = QListWidgetItem(n)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if n in chosen else Qt.Unchecked)
            self.people.addItem(it)
        self.people.itemChanged.connect(self._ticked)
        lay.addWidget(self.people)
        add = QHBoxLayout()
        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText("구성원에 없는 이름 추가")
        self.new_name.returnPressed.connect(self._add_name)
        add_btn = icons.set_icon(QPushButton(), "plus", "이름 추가")
        add_btn.clicked.connect(self._add_name)
        add.addWidget(self.new_name, 1)
        add.addWidget(add_btn)
        lay.addLayout(add)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("저장" if project else "만들기")
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _ticked(self, it: QListWidgetItem) -> None:
        n = it.text()
        if it.checkState() == Qt.Checked and n not in self.order:
            self.order.append(n)
        elif it.checkState() != Qt.Checked and n in self.order:
            self.order.remove(n)

    def _add_name(self) -> None:
        n = self.new_name.text().strip()
        if not n:
            return
        it = QListWidgetItem(n)
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(Qt.Unchecked)
        self.people.addItem(it)
        it.setCheckState(Qt.Checked)
        self.new_name.clear()

    def _ok(self) -> None:
        if not self.name.text().strip():
            self.name.setFocus()
            return
        self.accept()

    def result_project(self) -> W.WorkProject:
        base = copy.deepcopy(self.project) if self.project else W.WorkProject(kind=self.kind)
        base.name = self.name.text().strip()
        base.task_title = self.duty.text().strip()
        base.members = list(self.order)
        return base


class MilestoneDialog(QDialog):
    def __init__(self, parent, members: list[str], day: date | None = None, person: str = ""):
        super().__init__(parent)
        self.setWindowTitle("새 마일스톤")
        lay = QVBoxLayout(self)
        g = InsetGroup()
        self.title = QLineEdit()
        self.title.setPlaceholderText("예: 초고 완성")
        self.day = QDateEdit(_qdate(day or date.today()))
        self.day.setCalendarPopup(True)
        self.day.setDisplayFormat("yyyy-MM-dd")
        self.person = QComboBox()
        self.person.addItems(members or ["미정"])
        if person in members:
            self.person.setCurrentText(person)
        g.add_row("이름", self.title, expand=True)
        g.add_row("날짜", self.day)
        g.add_row("담당자", self.person)
        lay.addWidget(g)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("추가")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)


# ---- the Work tab ------------------------------------------------------------------------
class WorkPage(QWidget):
    def __init__(self, ctrl: WorkController, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.project_obj = None  # the inventory Project (for lab members)
        self.pid = ""
        ctrl.changed.connect(self.refresh)

        root = QVBoxLayout(self)
        m = tokens.space("margin")
        root.setContentsMargins(m, m, m, m)
        root.setSpacing(tokens.space("gap-section"))
        head = QHBoxLayout()
        title = QLabel("업무")
        title.setObjectName("pageTitle")
        sub = QLabel("프로젝트별 담당자와 당번 순서")
        sub.setObjectName("muted")
        head.addWidget(title)
        head.addSpacing(tokens.space("gap-group"))
        head.addWidget(sub)
        head.addStretch(1)
        self.new_btn = icons.set_icon(QPushButton("새 프로젝트"), "plus")
        self.new_btn.setObjectName("primary")
        new_menu = QMenu(self.new_btn)
        new_menu.addAction("당번제 업무…", lambda: self.new_project("rotation"))
        new_menu.addAction("마일스톤 프로젝트…", lambda: self.new_project("milestone"))
        self.new_btn.setMenu(new_menu)
        head.addWidget(self.new_btn)  # the prominent action: trailing end of the page header
        root.addLayout(head)

        body = QHBoxLayout()
        body.setSpacing(tokens.space("gap-group"))
        self.list = QListWidget()
        self.list.setFixedWidth(tokens.space("sidebar-width"))
        self.list.currentItemChanged.connect(self._picked)
        body.addWidget(self.list)

        self.detail = QWidget()
        dl = QVBoxLayout(self.detail)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(tokens.space("gap-group"))
        ph = QHBoxLayout()
        self.p_title = QLabel()
        self.p_title.setObjectName("sectionHeader")
        self.p_meta = QLabel()
        self.p_meta.setObjectName("muted")
        self.edit_btn = icons.set_icon(QPushButton(), "gearshape", "프로젝트 설정…")
        self.edit_btn.clicked.connect(self.edit_project)
        self.del_btn = QPushButton()
        self.del_btn.setObjectName("destructive")
        self.del_btn.setProperty("iconOnly", True)
        icons.set_icon(self.del_btn, "trash", "프로젝트 삭제…")
        self.del_btn.clicked.connect(self.delete_project)
        ph.addWidget(self.p_title)
        ph.addSpacing(tokens.space("gap-group"))
        ph.addWidget(self.p_meta, 1)
        ph.addWidget(self.del_btn)  # Delete apart from the safe action
        ph.addSpacing(tokens.space("gap-section"))
        ph.addWidget(self.edit_btn)
        dl.addLayout(ph)

        dl.addWidget(inset_title("담당자 순번"))
        self.people = PeopleRow()
        self.people.reordered.connect(lambda names: self.ctrl.set_members(self.pid, names))
        self.people.removed.connect(self._remove_person)
        self.people.add_clicked.connect(self.edit_project)
        dl.addWidget(self.people)

        # rotation controls (SettingsPane-like card)
        self.rot_title = inset_title("당번 배정")
        dl.addWidget(self.rot_title)
        self.rot = InsetGroup()
        self.base_day = QDateEdit(_qdate(date.today()))
        self.base_day.setCalendarPopup(True)
        self.base_day.setDisplayFormat("yyyy-MM-dd")
        self.first = QComboBox()
        self.freq = QComboBox()
        for k, v in W.FREQS.items():
            self.freq.addItem(v, k)
        # 기간 (until the end of): 연도 stepper + 상반기/하반기 stepper (components/Stepper:
        # attached at the field's end, the side at its limit dims, not hidden)
        self.until_year = QSpinBox()
        self.until_year.setRange(date.today().year - 1, date.today().year + 10)
        self.until_year.setValue(date.today().year)
        self.until_year.setSuffix(" 년")
        self.until_year.setGroupSeparatorShown(False)
        self.until_half = HalfStepper()
        self.until_half.setValue(W.half_of(date.today())[1])
        period = QWidget()
        pl = QHBoxLayout(period)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(tokens.space("space-2"))
        for sb in (self.until_year, self.until_half):  # SettingsPane field width, text at the start
            sb.setFixedWidth(tokens.space("settings-control"))
            pl.addWidget(sb)
        period.setToolTip("기준일부터 이 반기의 끝날(6월 30일 또는 12월 31일)까지 채웁니다")
        self.until_year.valueChanged.connect(self._period_changed)
        self.until_half.valueChanged.connect(self._period_changed)
        self.base_day.dateChanged.connect(self._period_changed)
        self.per_turn = QSpinBox()
        self.per_turn.setRange(1, 1)
        self.per_turn.setSuffix(" 명")
        self.per_turn.setToolTip("한 회차를 같이 맡는 사람 수 (2명이면 순번대로 두 명씩 돕니다)")
        self.rot.add_row("기준일", self.base_day)
        self.rot.add_row("첫 담당자", self.first)
        self.rot.add_row("주기", self.freq)
        self.rot.add_row("회차 인원", self.per_turn)
        self.rot.add_row("기간", period)
        acts = QHBoxLayout()
        acts.setContentsMargins(0, tokens.space("space-2"), 0, tokens.space("space-2"))
        self.cut_btn = QPushButton()
        self.cut_btn.setObjectName("destructive")
        self.cut_btn.setProperty("iconOnly", True)
        icons.set_icon(self.cut_btn, "trash", "기준일 이후 업무 삭제…")
        self.cut_btn.clicked.connect(self.cut_after)
        self.resort_btn = QPushButton("기준일 이후 다시 정렬")
        self.resort_btn.setToolTip("기준일 이후 남은 업무를 첫 업무의 담당자부터 순번·주기대로 다시 배치합니다")
        self.resort_btn.clicked.connect(lambda: self.ctrl.resort(self.pid, _pydate(self.base_day.date())))
        self.fill_btn = QPushButton("순번대로 채우기")
        self.fill_btn.setObjectName("primary")
        self.fill_btn.clicked.connect(self.fill)
        acts.addWidget(self.cut_btn)
        acts.addStretch(1)
        acts.addWidget(self.resort_btn)
        acts.addWidget(self.fill_btn)
        row = QWidget()
        row.setLayout(acts)
        self.rot._lay.addWidget(row)
        dl.addWidget(self.rot)

        # milestone action
        self.ms_row = QHBoxLayout()
        self.add_ms = icons.set_icon(QPushButton("마일스톤 추가"), "plus")
        self.add_ms.clicked.connect(lambda: self.add_milestone())
        self.ms_row.addStretch(1)
        self.ms_row.addWidget(self.add_ms)
        dl.addLayout(self.ms_row)

        # month
        nav = QHBoxLayout()
        self.prev_btn = icons.set_icon(QPushButton(), "chevron.left", "이전 달")
        self.next_btn = icons.set_icon(QPushButton(), "chevron.right", "다음 달")
        self.today_btn = QPushButton("오늘")
        self.month_label = QLabel()
        self.month_label.setObjectName("sectionTitle")
        self.prev_btn.clicked.connect(lambda: self._step(-1))
        self.next_btn.clicked.connect(lambda: self._step(1))
        self.today_btn.clicked.connect(self._today)
        nav.addWidget(self.month_label)
        nav.addStretch(1)
        self.hint = QLabel("업무를 끌어 날짜를 바꾸고, 담당자를 업무 위에 놓아 바꿉니다 · ⌘Z 되돌리기")
        self.hint.setObjectName("muted")
        nav.addWidget(self.prev_btn)
        nav.addWidget(self.today_btn)
        nav.addWidget(self.next_btn)
        dl.addLayout(nav)
        dl.addWidget(self.hint)  # its own line under the month title: wraps when narrow
        self.month = WorkMonth()
        self.month.task_moved.connect(self.ctrl.move)
        self.month.task_assigned.connect(self.ctrl.drop_person)
        self.month.day_dropped.connect(self._person_on_day)
        self.month.task_menu.connect(lambda tid, pos: self._menu(tid, pos))
        self.month.day_activated.connect(self._day_activated)
        dl.addWidget(self.month, 1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        # responsive inspector: never wider than the window; rows wrap, text wraps,
        # only vertical scrolling (design system: Inspector · responsive)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.hint.setWordWrap(True)
        self.p_meta.setWordWrap(True)
        self.p_meta.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.p_title.setWordWrap(True)
        self.month.setMinimumWidth(7 * tokens.space("control-md"))
        holder = QWidget()
        holder.setObjectName("pageBody")
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(self.detail)
        self.empty = QLabel("왼쪽 위 ‘새 프로젝트’로 당번제 업무나 마일스톤 프로젝트를 만드세요.")
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignCenter)
        hl.addWidget(self.empty, 1)
        scroll.setWidget(holder)
        body.addWidget(scroll, 1)
        root.addLayout(body, 1)
        QShortcut(QKeySequence.Undo, self, activated=self.ctrl.undo, context=Qt.WidgetWithChildrenShortcut)

    # -- data --
    def set_project(self, project) -> None:
        self.project_obj = project

    def lab_members(self) -> list[str]:
        if self.project_obj is None:
            return []
        return [m.get("name") or m.get("email", "") for m in self.project_obj.members()]

    def show_project(self, pid: str) -> None:
        self.pid = pid
        self.refresh()
        t, _ = W.current_next(self.ctrl.tasks(), pid)
        if t and t.day:
            self.month.month = t.day.replace(day=1)
            self.refresh()

    def refresh(self) -> None:
        projects = self.ctrl.projects()
        if self.pid not in {p.id for p in projects}:
            self.pid = projects[0].id if projects else ""
        self.list.blockSignals(True)
        self.list.clear()
        for p in projects:
            cur, _ = W.current_next(self.ctrl.tasks(), p.id)
            sub = f"{p.kind_label}" + (f" · 이번 {cur.assignee}" if cur else "")
            it = QListWidgetItem(f"{p.name}\n{sub}")
            it.setData(Qt.UserRole, p.id)
            it.setSizeHint(QSize(1, 2 * tokens.space("row-compact") + tokens.space("space-2")))
            it.setToolTip(f"{p.name} · {sub}")
            self.list.addItem(it)
            if p.id == self.pid:
                self.list.setCurrentItem(it)
        self.list.blockSignals(False)
        p = self.ctrl.project(self.pid)
        self.detail.setVisible(p is not None)
        self.empty.setVisible(p is None)
        self.list.setVisible(bool(projects))
        if p is None:
            return
        rot = p.kind == "rotation"
        self.p_title.setText(p.name)
        self.p_meta.setText(" · ".join(x for x in (p.kind_label, p.freq_label if rot else "",
                                                     f"담당 {len(p.members)}명", f"{p.per_turn}명씩" if rot and p.per_turn > 1 else "", p.duty() if rot and p.task_title else "")
                                       if x))
        self.people.set_names(p.members)
        for w in (self.rot_title, self.rot):
            w.setVisible(rot)
        self.add_ms.setVisible(not rot)
        self.first.blockSignals(True)
        keep = self.first.currentText()
        self.first.clear()
        self.first.addItems(p.members)
        cur, nxt = W.current_next(self.ctrl.tasks(), p.id)
        want = keep if keep in p.members else ((cur.people or [""])[0] if cur else (p.members[0] if p.members else ""))
        if want:
            self.first.setCurrentText(want)
        self.first.blockSignals(False)
        i = self.freq.findData(p.freq)
        self.freq.setCurrentIndex(max(0, i))
        y, h = p.until_half
        for sb, v in ((self.until_year, y), (self.until_half, h)):
            sb.blockSignals(True)
            if sb is self.until_year:
                sb.setRange(min(sb.minimum(), y), max(sb.maximum(), y))
            sb.setValue(v)
            sb.blockSignals(False)
        self._period_changed()
        self.per_turn.setRange(1, max(1, len(p.members)))
        self.per_turn.setValue(max(1, min(p.per_turn, len(p.members) or 1)))
        ranks = {m: i + 1 for i, m in enumerate(p.members)} if rot else {}
        self.month.set_tasks(self.ctrl.tasks(p.id), ranks, cur.id if cur else "", nxt.id if nxt else "",
                             project_color(self.ctrl.board, p.id))
        self.month_label.setText(f"{self.month.month.year}년 {self.month.month.month}월")

    def _picked(self, it, _old=None) -> None:
        if it is not None:
            self.pid = it.data(Qt.UserRole)
            self.refresh()

    def _step(self, n: int) -> None:
        m = self.month.month
        y, mo = divmod(m.month - 1 + n, 12)
        self.month.month = date(m.year + y, mo + 1, 1)
        self.refresh()

    def _today(self) -> None:
        self.month.month = date.today().replace(day=1)
        self.refresh()

    # -- actions --
    def new_project(self, kind: str) -> None:
        if self.ctrl.board is None:
            QMessageBox.information(self, "업무", "먼저 데이터 폴더를 여세요.")
            return
        dlg = ProjectDialog(self, self.lab_members(), kind=kind)
        if dlg.exec():
            p = dlg.result_project()
            self.ctrl.upsert_project(p)
            self.pid = p.id
            self.refresh()

    def edit_project(self) -> None:
        p = self.ctrl.project(self.pid)
        if p is None:
            return
        dlg = ProjectDialog(self, self.lab_members(), project=p)
        if dlg.exec():
            self.ctrl.upsert_project(dlg.result_project())

    def delete_project(self) -> None:
        p = self.ctrl.project(self.pid)
        if p is None:
            return
        n = len(self.ctrl.tasks(p.id))
        if confirm_delete(self, "프로젝트 삭제", f"‘{p.name}’과 업무 {n}개를 삭제할까요?") == QMessageBox.Yes:
            self.ctrl.delete_project(p.id)

    def _remove_person(self, name: str) -> None:
        p = self.ctrl.project(self.pid)
        if p is not None:
            self.ctrl.set_members(p.id, [m for m in p.members if m != name])

    def fill(self) -> None:
        p = self.ctrl.project(self.pid)
        if p is None:
            return
        if not p.members:
            QMessageBox.information(self, "당번 배정", "먼저 담당자를 추가하세요 (담당자 순번의 +).")
            return
        self.ctrl.fill(p.id, self.first.currentText(), _pydate(self.base_day.date()), self.period(),
                       self.freq.currentData(), self.per_turn.value())

    def period(self) -> tuple[int, int]:
        return self.until_year.value(), self.until_half.value()

    def _period_changed(self, *_) -> None:
        """순번대로 채우기 needs the period to end on or after the 기준일."""
        ok = W.half_end(*self.period()) >= _pydate(self.base_day.date())
        self.fill_btn.setEnabled(ok)
        self.fill_btn.setToolTip("기준일부터 기간 끝날까지 첫 담당자, 그다음 순번 순서로 채웁니다 (완료된 업무는 그대로)"
                                 if ok else "기간이 기준일보다 앞입니다. 연도나 반기를 늘리세요")

    def cut_after(self) -> None:
        p = self.ctrl.project(self.pid)
        if p is None:
            return
        d = _pydate(self.base_day.date())
        n = len([t for t in self.ctrl.tasks(p.id) if not t.done and t.day and t.day >= d])
        if n and confirm_delete(self, "업무 삭제", f"{d:%Y-%m-%d} 이후의 남은 업무 {n}개를 삭제할까요? "
                                "완료한 업무는 기록으로 남습니다.") == QMessageBox.Yes:
            self.ctrl.delete_from(p.id, d)

    def add_milestone(self, day: date | None = None, person: str = "") -> None:
        p = self.ctrl.project(self.pid)
        if p is None:
            return
        dlg = MilestoneDialog(self, p.members, day, person)
        if dlg.exec() and dlg.title.text().strip():
            self.ctrl.add_task(W.WorkTask(project_id=p.id, date=_pydate(dlg.day.date()).isoformat(),
                                          assignee=dlg.person.currentText(), title=dlg.title.text().strip()))

    def _person_on_day(self, day: date, person: str) -> None:
        p = self.ctrl.project(self.pid)
        if p is None:
            return
        if p.kind == "milestone":
            self.add_milestone(day, person)
        else:
            self.ctrl.add_task(W.WorkTask(project_id=p.id, date=day.isoformat(), assignee=person, title=p.duty()))

    def _day_activated(self, day: date) -> None:
        p = self.ctrl.project(self.pid)
        if p is None:
            return
        if p.kind == "milestone":
            self.add_milestone(day)
        else:
            self.base_day.setDate(_qdate(day))

    def _menu(self, tid: str, pos) -> None:
        t = self.ctrl.task(tid)
        if t is not None:
            task_menu(self.ctrl, t, self, pos)


# ---- dashboard box -------------------------------------------------------------------------
def turn(tasks: list[W.WorkTask], pid: str, today: date | None = None):
    """(this turn, the next one, struck): this turn is the first open task; a task done
    today stays as this turn, struck through, with the highlight on the next person."""
    today = today or date.today()
    ts = W.of_project(tasks, pid)
    open_ = [t for t in ts if not t.done]
    done_today = [t for t in ts if t.done and t.done_at[:10] == today.isoformat()]
    if done_today:
        return done_today[-1], (open_[0] if open_ else None), True
    return (open_[0] if open_ else None), (open_[1] if len(open_) > 1 else None), False


class _TintDelegate(QStyledItemDelegate):
    """Paints an item's BackgroundRole as a rounded tint (the style sheet would hide it):
    the highlight on this turn's / the next person's cells."""

    def paint(self, painter, option, index) -> None:
        bg = index.data(Qt.BackgroundRole)
        if bg is not None:
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(Qt.NoPen)
            painter.setBrush(bg)
            painter.drawRect(option.rect)
            painter.restore()
        super().paint(painter, option, index)


class WorkSection(QFrame):
    """Dashboard: one box, like the shortage list box. One row per 업무:
    업무 | 이번 담당자 | 날짜 | 다음 담당자 | 날짜. Clicking the highlighted (blue) person
    marks that turn done: it is struck through and the highlight moves to the next person.
    The undo button next to the title takes the last change back."""
    open_project = Signal(str)
    COLS = ["업무", "이번 담당자", "날짜", "다음 담당자", "날짜"]

    def __init__(self, ctrl: WorkController):
        super().__init__()
        self.ctrl = ctrl
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*[tokens.space("card-pad")] * 4)
        lay.setSpacing(tokens.space("space-1"))
        head = QHBoxLayout()
        self.title = QLabel()
        self.title.setObjectName("sectionTitle")
        head.addWidget(self.title)
        self.undo_btn = icons.set_icon(QPushButton(), "arrow.uturn.backward", "실행 취소 (⌘Z)")
        self.undo_btn.clicked.connect(ctrl.undo)
        head.addWidget(self.undo_btn)
        head.addStretch(1)
        self.open_btn = QPushButton("업무 탭 열기")
        self.open_btn.setObjectName("plain")
        self.open_btn.clicked.connect(lambda: self.open_project.emit(""))
        head.addWidget(self.open_btn)
        lay.addLayout(head)
        sub = QLabel("파란 담당자를 누르면 완료되어 가로줄이 그어지고 다음 담당자로 넘어갑니다 · 두 번 누르면 업무 탭에서 열립니다")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 3):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        for c in (2, 4):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        compact_rows(self.table, numeric=(2, 4))
        self.table.setItemDelegate(_TintDelegate(self.table))
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.cellClicked.connect(self._clicked)
        self.table.setMouseTracking(True)
        self.table.cellEntered.connect(self._hover)
        self.table.cellDoubleClicked.connect(lambda r, _c: self.open_project.emit(self._pids[r]))
        lay.addWidget(self.table)
        self._pids: list[str] = []
        self._blue: list[str] = []  # per row: the highlighted task id (click = done)
        ctrl.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        projects = self.ctrl.projects()
        self.setVisible(bool(projects))
        tasks = self.ctrl.tasks()
        self.title.setText(f"업무 {len(projects)}개")
        self.undo_btn.setEnabled(bool(self.ctrl.undo_stack))
        self.table.setRowCount(0)
        self.table.setRowCount(len(projects))
        self._pids = [p.id for p in projects]
        self._blue = []
        sec = tokens.text("label-secondary", "region-box")
        for r, p in enumerate(projects):
            cur, nxt, struck = turn(tasks, p.id)
            name = p.duty() if p.kind == "rotation" else f"{p.name} · {cur.title}" if cur else p.name

            def who(t):
                return self.ctrl.who(p, t)

            cells = [(name, None), (who(cur), cur), (cur.date if cur else "", cur),
                     (who(nxt), nxt), (nxt.date if nxt else "", nxt)]
            if p.kind == "milestone" and nxt:
                cells[3] = (f"{who(nxt)} ({nxt.title})", nxt)
            highlight = nxt if struck else cur
            self._blue.append(highlight.id if highlight else "")
            for c, (text, t) in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setData(Qt.UserRole, t.id if t else "")
                f = it.font()
                if t is not None and t is cur and struck:
                    f.setStrikeOut(True)
                    it.setForeground(sec)
                if t is not None and highlight is not None and t.id == highlight.id:
                    f.setWeight(QFont.Weight(600))
                    it.setBackground(tokens.selection(strong=True))
                    it.setToolTip(f"눌러서 업무 완료: {', '.join(t.people) or '미정'} · {t.date}")
                it.setFont(f)
                if c in (2, 4):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 0:
                    it.setToolTip(f"{p.name} · {p.kind_label}" + (f" · {p.freq_label}" if p.kind == "rotation" else ""))
                self.table.setItem(r, c, it)
            self.table.setRowHeight(r, tokens.space("control-md") + tokens.space("space-2"))
        h = self.table.horizontalHeader().height() + sum(self.table.rowHeight(r) for r in range(len(projects))) + 2
        self.table.setFixedHeight(max(h, self.table.horizontalHeader().height() + 2))

    def _task_id(self, r: int, c: int) -> str:
        it = self.table.item(r, c)
        return it.data(Qt.UserRole) if it is not None else ""

    def _clicked(self, r: int, c: int) -> None:
        tid = self._task_id(r, c)
        if tid and r < len(self._blue) and tid == self._blue[r]:
            self.ctrl.set_done(tid, True)

    def _hover(self, r: int, c: int) -> None:
        on = bool(self._task_id(r, c)) and r < len(self._blue) and self._task_id(r, c) == self._blue[r]
        self.table.viewport().setCursor(Qt.PointingHandCursor if on else Qt.ArrowCursor)

    def _menu(self, pos) -> None:
        it = self.table.itemAt(pos)
        tid = it.data(Qt.UserRole) if it else ""
        t = self.ctrl.task(tid) if tid else None
        if t is None and it is not None:  # the 업무 column: this turn of that row
            cur, _n, _s = turn(self.ctrl.tasks(), self._pids[it.row()])
            t = cur
        if t is not None:
            task_menu(self.ctrl, t, self, self.table.viewport().mapToGlobal(pos))
