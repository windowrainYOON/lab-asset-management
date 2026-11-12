"""출퇴근 기록 tab (view only): a month of clock-in times, dates down, members across.

Records come from the 출퇴근 기록 Google Sheet and are changed only by the web app;
this page reads them, marks late arrivals, and folds the days that do not count
(weekends, Korean public holidays, calendar events marked 출퇴근 기록에서 제외).
The inspector sums up one member's month. 설정 writes 출근 시간 / 지각 허용 / 근무 요일
to the sheet's 설정 tab.
"""
from __future__ import annotations

from datetime import date, time, timedelta

from PySide6.QtCore import QRectF, Qt, QThread, QTime, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QPushButton, QScrollArea, QSpinBox, QStyledItemDelegate, QTableWidget,
    QTableWidgetItem, QTimeEdit, QVBoxLayout, QWidget,
)

from ..core import attendance as att
from .inset import InsetGroup, inset_title
from .theme import icons, tokens
from .widgets import InspectorPanel, Segmented, glass_capsule

ROW_H = 34
FOLD_H = 24
DATE_W = 76
KIND_ROLE = Qt.UserRole + 1   # "day" | "fold"
VALUE_ROLE = Qt.UserRole + 2  # time | text | None
DATE_ROLE = Qt.UserRole + 3


def _hm(t: time | None) -> str:
    return t.strftime("%H:%M") if t else "—"


def _day_label(d: date) -> str:
    return f"{d.month}월 {d.day}일 ({att.WEEKDAYS[d.weekday()]})"


class _CalWorker(QThread):
    done = Signal(str, object, object, object)  # month key, holidays, excluded, errors

    def __init__(self, key: str, setting, start: date, end: date, parent=None):
        super().__init__(parent)
        self.key, self.setting, self.start, self.end = key, setting, start, end

    def run(self):
        try:
            h, e, errs = att.load_calendar_days(self.setting, self.start, self.end)
        except Exception as exc:  # noqa: BLE001  shown as a note; the table still works
            h, e, errs = {}, {}, [str(exc)]
        self.done.emit(self.key, h, e, errs)


class CellDelegate(QStyledItemDelegate):
    """Clock-in times; late ones on a warning pill, words (휴가 …) on a gray pill,
    folded rows (weekend / holiday / excluded) as one line of text."""

    def __init__(self, page: "AttendancePage"):
        super().__init__(page.table)
        self.page = page

    def paint(self, p: QPainter, opt, index) -> None:
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(opt.rect)
        kind = index.data(KIND_ROLE)
        d = index.data(DATE_ROLE)
        sep = tokens.color("separator")
        if kind == "fold":
            p.fillRect(r, tokens.tint("fill", 0.14))
        elif d == date.today():
            p.fillRect(r, tokens.selection())
        p.setPen(QPen(sep, 1))
        p.drawLine(r.bottomLeft(), r.bottomRight())
        f = QFont(opt.font)
        f = tokens.tabular(f)
        if index.column() == 0:
            f.setBold(d == date.today())
            p.setFont(f)
            p.setPen(tokens.text("label-secondary" if kind == "fold" else "label", "region-box"))
            p.drawText(r.adjusted(8, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, index.data(Qt.DisplayRole) or "")
            p.restore()
            return
        if kind == "fold":
            text = index.data(Qt.DisplayRole) or ""
            if text:
                p.setFont(f)
                p.setPen(tokens.text("label-secondary", "region-box"))
                p.drawText(r.adjusted(10, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft, text)
            p.restore()
            return
        v = index.data(VALUE_ROLE)
        settings = self.page.settings()
        selected = (self.page.sel_person == self.page.col_person(index.column()) and self.page.sel_day == d)
        if isinstance(v, time):
            late = settings.is_late(v)
            text = v.strftime("%H:%M")
            f.setBold(late)
            p.setFont(f)
            w = p.fontMetrics().horizontalAdvance(text) + 14
            pill = QRectF(r.center().x() - w / 2, r.center().y() - 11, w, 22)
            if late:
                p.setPen(Qt.NoPen)
                p.setBrush(tokens.color("status-warning-tint"))
                p.drawRoundedRect(pill, 7, 7)
                p.setPen(tokens.text("orange", "region-box"))
            else:
                p.setPen(tokens.text("label", "region-box"))
            p.drawText(pill, Qt.AlignCenter, text)
        elif v:
            text = str(v)
            p.setFont(f)
            w = p.fontMetrics().horizontalAdvance(text) + 14
            pill = QRectF(r.center().x() - w / 2, r.center().y() - 10, w, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(tokens.color("fill"))
            p.drawRoundedRect(pill, 6, 6)
            p.setPen(tokens.text("label-secondary", "region-box"))
            p.drawText(pill, Qt.AlignCenter, text)
        elif d is not None and d <= date.today():
            p.setFont(f)
            p.setPen(tokens.text("label-tertiary", "region-box") if tokens.raw("label-tertiary") else
                     tokens.text("label-secondary", "region-box"))
            p.drawText(r, Qt.AlignCenter, "—")
        if selected:
            w = 62
            box = QRectF(r.center().x() - w / 2, r.center().y() - 13, w, 26)
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(tokens.color("accent"), 2))
            p.drawRoundedRect(box, 8, 8)
        p.restore()


class Tile(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setObjectName("insetGroup")
        lay = QVBoxLayout(self)
        pad = tokens.space("row-pad")
        lay.setContentsMargins(pad, tokens.space("space-2"), pad, tokens.space("space-2"))
        lay.setSpacing(2)
        self.title = QLabel(title)
        self.title.setObjectName("muted")
        self.value = QLabel("—")
        self.value.setObjectName("cardValue")  # type size from the theme
        self.value.setFont(tokens.tabular(self.value.font()))
        lay.addWidget(self.title)
        lay.addWidget(self.value)

    def set(self, title: str, value: str, warn: bool = False) -> None:
        self.title.setText(title)
        self.value.setText(value)
        c = tokens.text("orange", "region-box") if warn else tokens.text("label", "region-box")
        self.value.setStyleSheet(f"color: {tokens.css(c)};")


class AttendanceInspector(QScrollArea):
    """One member's month: counts, the chosen day, and the late days."""

    def __init__(self, page: "AttendancePage"):
        super().__init__()
        self.page = page
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        self.setWidget(body)
        self.lay = QVBoxLayout(body)
        self.lay.setContentsMargins(0, 0, 0, 0)
        self.lay.setSpacing(tokens.space("space-3"))
        self.name = QLabel()
        self.name.setObjectName("sectionTitle")
        self.sub = QLabel()
        self.sub.setObjectName("muted")
        self.lay.addWidget(self.name)
        self.lay.addWidget(self.sub)
        grid = QGridLayout()
        grid.setSpacing(tokens.space("space-2"))
        self.t_present, self.t_late = Tile("출근"), Tile("지각")
        self.t_avg, self.t_ontime = Tile("평균 출근"), Tile("정시 출근")
        for i, t in enumerate((self.t_present, self.t_late, self.t_avg, self.t_ontime)):
            grid.addWidget(t, i // 2, i % 2)
        self.lay.addLayout(grid)
        self.day_title = inset_title("")
        self.lay.addWidget(self.day_title)
        self.day_group = InsetGroup()
        self.day_in = QLabel()
        self.day_rule = QLabel()
        self.day_group.add_row("출근", self.day_in)
        self.day_group.add_row("기준", self.day_rule)
        self.lay.addWidget(self.day_group)
        self.lay.addWidget(inset_title("이번 달 지각"))
        self.late_group = QVBoxLayout()
        self.late_group.setSpacing(0)
        self.late_holder = QWidget()
        self.late_holder.setLayout(self.late_group)
        self.lay.addWidget(self.late_holder)
        hint = QLabel("기록은 출퇴근 웹앱에서만 남기고 고칠 수 있습니다. 이 앱에서는 열람만 됩니다.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        self.lay.addWidget(hint)
        self.lay.addStretch(1)
        self._late_box: InsetGroup | None = None

    def show_person(self, person: str, m: att.Month | None, day: date | None) -> None:
        page = self.page
        s = page.settings()
        self.name.setText(person or "구성원을 고르세요")
        if not person or m is None:
            self.sub.setText("")
            for t in (self.t_present, self.t_late, self.t_avg, self.t_ontime):
                t.set(t.title.text(), "—")
            self.day_title.setVisible(False)
            self.day_group.setVisible(False)
            self._set_late([])
            return
        st = att.person_stats(m, person, s, page.holidays, page.excluded, date.today())
        self.sub.setText(f"{m.year}년 {m.month}월 · 근무일 {st.workdays}일")
        self.t_present.set("출근", f"{st.present} / {st.workdays}일")
        self.t_late.set("지각", f"{st.late}회", warn=st.late > 0)
        self.t_avg.set("평균 출근", _hm(st.avg_in))
        self.t_ontime.set("정시 출근", f"{st.ontime}회")
        show_day = day is not None and day.month == m.month
        self.day_title.setVisible(show_day)
        self.day_group.setVisible(show_day)
        if show_day:
            self.day_title.setText(_day_label(day))
            info = att.day_info(day, s, page.holidays, page.excluded)
            v = m.get(day, person)
            if not info.workday:
                self.day_in.setText(f"{info.reason} · 계산 제외")
                self.day_in.setStyleSheet("")
            elif isinstance(v, time):
                late = s.is_late(v)
                self.day_in.setText(f"{_hm(v)} · 지각 {s.late_by(v)}분" if late else f"{_hm(v)} · 정시")
                c = tokens.text("orange", "region-box") if late else tokens.text("label", "region-box")
                self.day_in.setStyleSheet(f"color: {tokens.css(c)}; font-weight: {600 if late else 400};")
            else:
                self.day_in.setText(str(v) if v else "기록 없음")
                self.day_in.setStyleSheet("")
            self.day_rule.setText(f"{s.start.strftime('%H:%M')} · 허용 {s.grace}분")
        self._set_late(st.late_days)

    def _set_late(self, days: list[tuple[date, time]]) -> None:
        if self._late_box is not None:
            self._late_box.setParent(None)
            self._late_box.deleteLater()
            self._late_box = None
        box = InsetGroup()
        if not days:
            lbl = QLabel("없음")
            lbl.setObjectName("muted")
            box.add_row(lbl)
        for d, t in days:
            v = QLabel(_hm(t))
            v.setStyleSheet(f"color: {tokens.css(tokens.text('orange', 'region-box'))}; font-weight: 600;")
            box.add_row(_day_label(d), v)
        self._late_box = box
        self.late_group.addWidget(box)


class AttendanceSettingsDialog(QDialog):
    """출근 시간 · 지각 허용 · 근무 요일, written to the sheet's 설정 tab."""

    def __init__(self, store: att.AttendanceStore, location: str, people: int, month: str, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("출퇴근 기록 · 설정")
        self.setMinimumWidth(460)
        s = store.book.settings
        lay = QVBoxLayout(self)
        lay.setSpacing(tokens.space("space-3"))
        info = QLabel("기록 시트의 ‘설정’ 탭에 저장됩니다. 웹앱도 같은 기준으로 지각을 판단합니다.")
        info.setObjectName("muted")
        info.setWordWrap(True)
        lay.addWidget(info)
        lay.addWidget(inset_title("기준"))
        g = InsetGroup()
        self.start = QTimeEdit(QTime(s.start.hour, s.start.minute))
        self.start.setDisplayFormat("HH:mm")
        self.grace = QSpinBox()
        self.grace.setRange(0, 180)
        self.grace.setSuffix("분")
        self.grace.setValue(s.grace)
        g.add_row("출근 시간", self.start)
        g.add_row("지각 허용 범위", self.grace)
        days = QWidget()
        dl = QHBoxLayout(days)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(4)
        self.day_btns = []
        for i, ch in enumerate(att.WEEKDAYS):
            b = QPushButton(ch)
            b.setObjectName("segment")
            b.setCheckable(True)
            b.setChecked(i in s.workdays)
            b.setFixedWidth(30)
            self.day_btns.append(b)
            dl.addWidget(b)
        g.add_row("근무 요일", days)
        hol = QLabel("자동 제외 · Google 캘린더 ‘대한민국 공휴일’")
        hol.setObjectName("muted")
        g.add_row("한국 공휴일", hol)
        lay.addWidget(g)
        self.rule = QLabel()
        self.rule.setObjectName("muted")
        self.rule.setWordWrap(True)
        lay.addWidget(self.rule)
        self.start.timeChanged.connect(self._rule)
        self.grace.valueChanged.connect(self._rule)
        self._rule()
        lay.addWidget(inset_title("기록 시트"))
        g2 = InsetGroup()
        for label, value in (("위치", location), ("현재 시트", f"{month} · 구성원 {people}명"),
                             ("이 앱에서의 권한", "열람만")):
            v = QLabel(value)
            v.setObjectName("muted")
            g2.add_row(label, v)
        lay.addWidget(g2)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setText("저장")
        bb.button(QDialogButtonBox.Cancel).setText("취소")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def value(self) -> att.AttendanceSettings:
        t = self.start.time()
        days = frozenset(i for i, b in enumerate(self.day_btns) if b.isChecked())
        return att.AttendanceSettings(time(t.hour(), t.minute()), self.grace.value(), days or frozenset(range(5)))

    def _rule(self) -> None:
        s = self.value()
        late = (s.deadline.hour * 60 + s.deadline.minute + 1)
        self.rule.setText(f"{s.deadline.strftime('%H:%M')}까지 출근하면 정시, {late // 60:02d}:{late % 60:02d}부터 "
                          "지각으로 표시됩니다. 근무 요일이 아닌 날, 공휴일, 일정에서 ‘출퇴근 기록에서 제외’를 켠 날은 "
                          "접혀 보이고 계산에서 빠집니다.")

    def _save(self) -> None:
        from .cloud import run_blocking
        new = self.value()
        try:
            run_blocking(self, lambda: self.store.save_settings(new), "출퇴근 설정을 저장하는 중…")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "출퇴근 기록", f"설정을 저장하지 못했습니다.\n{exc}")
            return
        self.accept()


class AttendancePage(QWidget):
    create_requested = Signal()  # no 출퇴근 기록 sheet yet: the window makes one

    def __init__(self, parent=None):
        super().__init__(parent)
        self.store: att.AttendanceStore | None = None
        self.project = None
        self.location = ""
        today = date.today()
        self.month_key = att.month_key(today)
        self.only_late = False
        self.person_filter = ""
        self.sel_person = ""
        self.sel_day: date | None = None
        self.holidays: dict = {}
        self.excluded: dict = {}
        self._cal_cache: dict[str, tuple[dict, dict]] = {}
        self._cal_note = ""
        self._workers: list[_CalWorker] = []
        self._cols: list[str] = []

        root = QVBoxLayout(self)
        m = tokens.space("margin")
        root.setContentsMargins(m, tokens.space("space-2"), m, m)
        root.setSpacing(tokens.space("space-2"))
        head = QHBoxLayout()
        title = QLabel("출퇴근 기록")
        title.setObjectName("pageTitle")
        head.addWidget(title)
        head.addStretch(1)
        lock = QLabel("열람 전용")
        lock.setObjectName("muted")
        lock.setToolTip("기록은 출퇴근 웹앱에서만 남기고 고칠 수 있습니다")
        head.addWidget(lock)
        self.open_btn = icons.set_icon(QPushButton(), "square.and.arrow.up", "Google 시트에서 열기")
        self.open_btn.clicked.connect(self._open_sheet)
        self.settings_btn = icons.set_icon(QPushButton(), "gearshape", "출퇴근 설정")
        self.settings_btn.clicked.connect(self.edit_settings)
        head.addWidget(glass_capsule(self.open_btn, self.settings_btn))
        root.addLayout(head)
        self.sub = QLabel()
        self.sub.setObjectName("muted")
        self.sub.setWordWrap(True)
        root.addWidget(self.sub)

        ctrl = QHBoxLayout()
        prev = icons.set_icon(QPushButton(), "chevron.left", "이전 달")
        prev.clicked.connect(lambda: self.step(-1))
        today_btn = QPushButton("이번 달")
        today_btn.clicked.connect(self.go_today)
        nxt = icons.set_icon(QPushButton(), "chevron.right", "다음 달")
        nxt.clicked.connect(lambda: self.step(1))
        ctrl.addWidget(glass_capsule(prev, today_btn, nxt))
        self.month_label = QLabel()
        self.month_label.setObjectName("sectionTitle")
        ctrl.addWidget(self.month_label)
        self.seg = Segmented([("all", "전체"), ("late", "지각만")])
        self.seg.set_current("all")
        self.seg.changed.connect(self._set_late_only)
        ctrl.addWidget(self.seg)
        self.people = QComboBox()
        self.people.currentIndexChanged.connect(self._people_changed)
        ctrl.addWidget(self.people)
        ctrl.addStretch(1)
        root.addLayout(ctrl)

        tiles = QHBoxLayout()
        tiles.setSpacing(tokens.space("space-2"))
        self.tiles = [Tile(""), Tile(""), Tile(""), Tile("")]
        for t in self.tiles:
            tiles.addWidget(t, 1)
        root.addLayout(tiles)

        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setShowGrid(False)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.horizontalHeader().sectionClicked.connect(self._header_clicked)
        self.table.setItemDelegate(CellDelegate(self))
        self.table.cellClicked.connect(self._cell_clicked)
        cl.addWidget(self.table, 1)
        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        self.empty_text = QLabel()
        self.empty_text.setObjectName("muted")
        self.empty_text.setWordWrap(True)
        el.addWidget(self.empty_text)
        self.create_btn = QPushButton("출퇴근 기록 시트 만들기")
        self.create_btn.setObjectName("primary")
        self.create_btn.clicked.connect(self.create_requested.emit)
        el.addWidget(self.create_btn, 0, Qt.AlignLeft)
        el.addStretch(1)
        cl.addWidget(self.empty)
        self.legend = QLabel("주황 = 지각 · — = 기록 없음 · 휴가·출장 = 시트에 적힌 글자 · 접힌 줄 = 주말·공휴일·제외 일정")
        self.legend.setObjectName("muted")
        self.legend.setWordWrap(True)
        cl.addWidget(self.legend)
        root.addWidget(card, 1)

        self.inspector_panel = InspectorPanel(wanted=False)
        il = QVBoxLayout(self.inspector_panel)
        il.setContentsMargins(0, 0, 0, 0)
        self.inspector = AttendanceInspector(self)
        il.addWidget(self.inspector)
        self.inspector_panel.setMinimumWidth(280)
        self.inspector_panel.setMaximumWidth(340)

    # ---- data ----------------------------------------------------------------------------
    def settings(self) -> att.AttendanceSettings:
        return self.store.book.settings if self.store else att.AttendanceSettings()

    def month(self) -> att.Month | None:
        return self.store.book.month(self.month_key) if self.store else None

    def set_store(self, store: att.AttendanceStore | None, project, location: str = "") -> None:
        self.store, self.project, self.location = store, project, location
        self.refresh(fetch=True)

    def col_person(self, col: int) -> str:
        return self._cols[col - 1] if 0 < col <= len(self._cols) else ""

    def _year_month(self) -> tuple[int, int]:
        return int(self.month_key[:4]), int(self.month_key[5:7])

    def step(self, delta: int) -> None:
        y, mo = self._year_month()
        mo += delta
        y, mo = y + (mo - 1) // 12, (mo - 1) % 12 + 1
        self.month_key = f"{y:04d}-{mo:02d}"
        self.sel_day = None
        self.refresh(fetch=True)

    def go_today(self) -> None:
        self.month_key = att.month_key(date.today())
        self.refresh(fetch=True)

    def _set_late_only(self, key: str) -> None:
        self.only_late = key == "late"
        self.refresh()

    def _people_changed(self, _i: int) -> None:
        if getattr(self, "_filling", False):
            return
        self.person_filter = self.people.currentData() or ""
        if self.person_filter:
            self.sel_person = self.person_filter
        self.refresh()

    # ---- calendar (holidays / excluded events), fetched off the UI thread ------------------
    def _fetch_calendar(self, force: bool = False) -> None:
        key = self.month_key
        if key in self._cal_cache and not force:
            self.holidays, self.excluded = self._cal_cache[key]
            return
        if any(w.key == key and w.isRunning() for w in self._workers):
            return
        y, mo = self._year_month()
        start = date(y, mo, 1)
        end = (date(y + (mo == 12), mo % 12 + 1, 1)) - timedelta(days=1)
        setting = self.project.setting("calendar") if self.project is not None else None
        w = _CalWorker(key, setting, start, end, self)
        w.done.connect(self._calendar_loaded)
        w.finished.connect(lambda w=w: self._workers.remove(w) if w in self._workers else None)
        self._workers.append(w)
        w.start()

    def _calendar_loaded(self, key: str, holidays, excluded, errors) -> None:
        self._cal_cache[key] = (holidays, excluded)
        self._cal_note = " · ".join(errors[:2]) if errors else ""
        if key == self.month_key:
            self.holidays, self.excluded = holidays, excluded
            self.refresh()

    # ---- drawing -------------------------------------------------------------------------
    def refresh(self, fetch: bool = False) -> None:
        if fetch:
            self._fetch_calendar(force=True)
        elif self.month_key in self._cal_cache:
            self.holidays, self.excluded = self._cal_cache[self.month_key]
        y, mo = self._year_month()
        self.month_label.setText(f"{y}년 {mo}월")
        s = self.settings()
        has_sheet = bool(self.store and self.store.file_id)
        m = self.month()
        self.open_btn.setEnabled(has_sheet)
        self.settings_btn.setEnabled(has_sheet)
        rule = f"출근 {s.start.strftime('%H:%M')} · 지각 허용 {s.grace}분 ({s.deadline.strftime('%H:%M')}까지 정시)"
        if not has_sheet:
            self.sub.setText("Lab Management 폴더에 출퇴근 기록 시트가 아직 없습니다.")
        elif m is None:
            self.sub.setText(f"Google 시트 · {self.month_key} 시트 없음 · {rule}")
        else:
            self.sub.setText(f"Google 시트 · {self.month_key} 시트 · 구성원 {len(m.people)}명 · {rule}"
                             + (f" · 캘린더: {self._cal_note}" if self._cal_note else ""))
        self.create_btn.setVisible(not has_sheet and self.store is not None)
        if not has_sheet or m is None:
            self.table.setVisible(False)
            self.empty.setVisible(True)
            self.legend.setVisible(False)
            self.empty_text.setText(
                "출퇴근 기록 시트가 없습니다. 아래 버튼으로 Lab Management 폴더에 만들 수 있습니다 "
                "(이번 달·다음 달 시트와 설정 탭, 구성원 목록으로 열을 만듭니다)." if not has_sheet else
                f"시트에 {self.month_key} 탭이 없습니다. 웹앱이 그 달 기록을 남기면 여기에 보입니다.")
            self._fill_tiles(None)
            self.inspector_panel.set_wanted(False)
            return
        self.table.setVisible(True)
        self.empty.setVisible(False)
        self.legend.setVisible(True)
        self._fill_people(m)
        self._fill_table(m)
        self._fill_tiles(m)
        if not self.sel_person or self.sel_person not in m.people:
            self.sel_person = m.people[0] if m.people else ""
        self.inspector.show_person(self.sel_person, m, self.sel_day)
        self.inspector_panel.set_wanted(bool(self.sel_person))

    def _fill_people(self, m: att.Month) -> None:
        self._filling = True
        cur = self.person_filter
        self.people.clear()
        self.people.addItem("모든 구성원", "")
        for p in m.people:
            self.people.addItem(p, p)
        i = self.people.findData(cur)
        self.people.setCurrentIndex(max(0, i))
        if i < 0:
            self.person_filter = ""
        self._filling = False

    def _fill_table(self, m: att.Month) -> None:
        s = self.settings()
        today = date.today()
        cols = [p for p in m.people if not self.person_filter or p == self.person_filter]
        self._cols = cols
        dates = [d for d in m.dates() if d <= today] if self.month_key == att.month_key(today) else m.dates()
        rows = []
        for d in dates:
            info = att.day_info(d, s, self.holidays, self.excluded)
            if self.only_late:
                if not info.workday or not any(att.status(m.get(d, p), s) == "late" for p in cols):
                    continue
            rows.append((d, info))
        t = self.table
        t.clearSpans()
        t.clear()
        t.setColumnCount(1 + len(cols))
        t.setRowCount(len(rows))
        t.setHorizontalHeaderLabels(["날짜"] + cols)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        t.setColumnWidth(0, DATE_W)
        for r, (d, info) in enumerate(rows):
            kind = "day" if info.workday else "fold"
            it = QTableWidgetItem(f"{d.month}/{d.day} {att.WEEKDAYS[d.weekday()]}")
            it.setData(KIND_ROLE, kind)
            it.setData(DATE_ROLE, d)
            t.setItem(r, 0, it)
            if kind == "fold":
                label = info.reason if info.kind == "weekend" else (
                    f"{info.reason} · 공휴일" if info.kind == "holiday" else f"{info.reason} · 일정에서 출퇴근 기록 제외")
                fold = QTableWidgetItem(label)
                fold.setData(KIND_ROLE, "fold")
                fold.setData(DATE_ROLE, d)
                t.setItem(r, 1, fold)
                if len(cols) > 1:
                    t.setSpan(r, 1, 1, len(cols))
                t.setRowHeight(r, FOLD_H)
                continue
            for c, p in enumerate(cols, start=1):
                v = m.get(d, p)
                cell = QTableWidgetItem()
                cell.setData(KIND_ROLE, "day")
                cell.setData(DATE_ROLE, d)
                cell.setData(VALUE_ROLE, v)
                tip = (f"{p} · {_day_label(d)} · {v.strftime('%H:%M')}" + (" · 지각" if s.is_late(v) else "")
                       if isinstance(v, time) else f"{p} · {_day_label(d)} · {v or '기록 없음'}")
                cell.setToolTip(tip)
                t.setItem(r, c, cell)
            t.setRowHeight(r, ROW_H)
        hdr = t.horizontalHeader()
        for c, p in enumerate(cols, start=1):
            item = t.horizontalHeaderItem(c)
            if item is not None:
                f = item.font()
                f.setBold(p == self.sel_person)
                item.setFont(f)
                if p == self.sel_person:
                    item.setForeground(tokens.text("accent", "region-box"))
        hdr.viewport().update()

    def _fill_tiles(self, m: att.Month | None) -> None:
        s = self.settings()
        today = date.today()
        if m is None:
            for t, name in zip(self.tiles, ("오늘 출근", "오늘 지각", "아직 기록 없음", "이번 달 지각")):
                t.set(name, "—")
            return
        total_late = att.month_late_total(m, s, self.holidays, self.excluded, today)
        if self.month_key == att.month_key(today) and att.day_info(today, s, self.holidays, self.excluded).workday:
            ds = att.day_summary(m, today, s)
            self.tiles[0].set(f"오늘 출근 · {_day_label(today)}", f"{len(ds.present)} / {len(m.people)}명")
            self.tiles[1].set("오늘 지각", f"{len(ds.late)}명" + (f" · {', '.join(ds.late[:3])}" if ds.late else ""),
                              warn=bool(ds.late))
            self.tiles[2].set("아직 기록 없음", f"{len(ds.missing)}명"
                              + (f" · {', '.join(ds.missing[:3])}" if ds.missing else ""))
        else:
            work = [d for d in m.dates() if d <= today and att.day_info(d, s, self.holidays, self.excluded).workday]
            present = sum(1 for d in work for p in m.people if isinstance(m.get(d, p), time))
            info = att.day_info(today, s, self.holidays, self.excluded) if self.month_key == att.month_key(today) else None
            self.tiles[0].set("근무일", f"{len(work)}일" + (f" · 오늘은 {info.reason}" if info else ""))
            self.tiles[1].set("출근 기록", f"{present}칸")
            slots = len(work) * len(m.people)
            self.tiles[2].set("정시 출근 비율", f"{round((present - total_late) / slots * 100)}%" if slots else "—")
        self.tiles[3].set("이번 달 지각 (전체)", f"{total_late}회", warn=total_late > 0)

    # ---- picking -------------------------------------------------------------------------
    def _cell_clicked(self, row: int, col: int) -> None:
        it = self.table.item(row, col) or self.table.item(row, 0)
        d = it.data(DATE_ROLE) if it is not None else None
        if col > 0 and self.col_person(col) and (it is None or it.data(KIND_ROLE) != "fold"):
            self.sel_person = self.col_person(col)
        self.sel_day = d
        self.inspector.show_person(self.sel_person, self.month(), self.sel_day)
        self.inspector_panel.set_wanted(bool(self.sel_person))
        self._fill_table(self.month())

    def _header_clicked(self, col: int) -> None:
        if col > 0:
            self.sel_person = self.col_person(col)
            self.inspector.show_person(self.sel_person, self.month(), self.sel_day)
            self.inspector_panel.set_wanted(True)
            self._fill_table(self.month())

    # ---- actions -------------------------------------------------------------------------
    def _open_sheet(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        if self.store and self.store.file_id:
            QDesktopServices.openUrl(QUrl(f"https://docs.google.com/spreadsheets/d/{self.store.file_id}/edit"))

    def edit_settings(self) -> None:
        if not (self.store and self.store.file_id):
            return
        m = self.month()
        dlg = AttendanceSettingsDialog(self.store, self.location or "Lab Management / 출퇴근 기록",
                                       len(m.people) if m else 0, self.month_key, self)
        if dlg.exec():
            self.refresh()
