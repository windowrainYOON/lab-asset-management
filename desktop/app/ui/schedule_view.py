"""일정 tab, laid out like macOS Calendar (Desktop profile, tahoe style).

    [+]        ( 일 | 주 | 월 | 년 )        [⟳ ⚙]      <- glass capsules
    2026년 4월                               (‹ 오늘 ›)
    캘린더 목록 |  day / week / month / year view   | 일정 inspector
    미니 달력   |                                    |

Events come from the chosen Google calendars; each can be assigned to a member (or
everyone) with an optional mail alert before it starts (default: no alert). Adding and
editing happen in the inspector on the right (inset groups), not in a modal dialog.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from PySide6.QtCore import QDate, QLocale, Qt, QTime, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QScrollArea, QStackedWidget, QTextEdit, QTimeEdit, QVBoxLayout, QWidget,
)

from ..core import settings as app_settings
from ..core.attendance import EXCLUDE_KEY
from ..integrations import gcalendar
from ..integrations.gcalendar import NOTIFY_CHOICES, REPEAT_CHOICES, Event
from . import calendar_views as cv
from .alerts import confirm_delete
from .inset import InsetGroup, inset_title
from .theme import icons, tokens
from .widgets import InspectorPanel, Segmented, Switch, glass_capsule

VIEWS = [("day", "일"), ("week", "주"), ("month", "월"), ("year", "년")]
HIDDEN_KEY = "schedule_hidden_calendars"  # per Mac, like the sidebar's hidden tabs
VIEW_KEY = "schedule_view"


def _esc(t: str) -> str:
    import html
    return html.escape(t or "")


def ensure_calendar_permission(parent) -> bool:
    from ..integrations.gdrive import drive
    from .cloud import run_blocking

    if gcalendar.can_use():
        return True
    if QMessageBox.question(parent, "캘린더 권한",
                            "일정을 보고 고치려면 Google 계정으로 한 번 더 로그인해 ‘캘린더’ 권한을 허용해야 합니다. "
                            "지금 로그인할까요?") != QMessageBox.Yes:
        return False
    try:
        run_blocking(parent, drive.sign_in, "브라우저에서 로그인하고 캘린더 권한을 허용해 주세요…")
    except Exception as exc:  # noqa: BLE001
        QMessageBox.warning(parent, "Google 로그인", f"로그인하지 못했습니다.\n{exc}")
        return False
    return gcalendar.can_use()


def _assignee_label(ev: Event) -> str:
    if ev.assignee == "all":
        return "전체"
    return ev.assignee_name or ev.assignee


def _when(ev: Event) -> str:
    if ev.all_day:
        return "종일"
    s = ev.start if isinstance(ev.start, datetime) else None
    e = ev.end if isinstance(ev.end, datetime) else None
    if s is None:
        return ""
    s = s.astimezone()
    return f"{s:%H:%M}" + (f"–{e.astimezone():%H:%M}" if e else "")




class CalendarSettingsDialog(QDialog):
    def __init__(self, project, parent=None):
        super().__init__(parent)
        from PySide6.QtWidgets import QListWidget, QListWidgetItem
        from ..integrations import schedule_mac
        from .cloud import run_blocking

        self.project = project
        self.setWindowTitle("일정 · 캘린더 설정")
        self.resize(520, 520)
        lay = QVBoxLayout(self)
        info = QLabel("함께 보여 줄 Google 캘린더를 모두 체크하세요. 연구실 공용 캘린더는 로그인한 계정과 "
                      "‘일정 변경 가능’으로 공유해 두면 일정을 추가·수정할 수 있습니다.")
        info.setWordWrap(True)
        lay.addWidget(info)
        cals = run_blocking(self, gcalendar.list_calendars, "캘린더 목록을 읽는 중…")
        chosen = {c["id"] for c in gcalendar.selected_calendars(project.setting("calendar"))}
        self.list = QListWidget()
        for c in sorted(cals, key=lambda c: (not c["primary"], c["name"].lower())):
            it = QListWidgetItem("● " + c["name"] + ("  (내 기본 캘린더)" if c["primary"] else "")
                                 + ("" if c["writable"] else "  · 보기만 가능"))
            it.setData(Qt.UserRole, c)
            it.setForeground(QBrush(QColor(c.get("color") or tokens.raw("accent"))))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if c["id"] in chosen else Qt.Unchecked)
            self.list.addItem(it)
        self.list.itemChanged.connect(lambda _it: self._fill_default())
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.addWidget(QLabel("새 일정 기본 캘린더"))
        self.default = QComboBox()
        row.addWidget(self.default, 1)
        lay.addLayout(row)
        self._fill_default(gcalendar.default_calendar(project.setting("calendar")))
        self.here = QCheckBox("이 컴퓨터에서 일정 알림 메일 보내기 (5분마다 확인, 앱이 꺼져 있어도 동작)")
        self.here.setChecked(schedule_mac.reminders_installed())
        self.here.setEnabled(schedule_mac.supported())
        lay.addWidget(self.here)
        note = QLabel("알림 발송은 한 대의 컴퓨터에서만 켜 두면 됩니다. 보낸 알림은 일정에 기록되어 두 번 가지 않습니다.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _checked(self) -> list[dict]:
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.Checked:
                c = it.data(Qt.UserRole)
                out.append({"id": c["id"], "name": c["name"], "color": c.get("color", ""),
                            "writable": c["writable"]})
        return out

    def _fill_default(self, want: str | None = None) -> None:
        want = want or (self.default.currentData() or {}).get("id")
        self.default.clear()
        for c in self._checked():
            if c["writable"]:
                self.default.addItem(c["name"], c)
                if c["id"] == want:
                    self.default.setCurrentIndex(self.default.count() - 1)

    def _save(self) -> None:
        from ..integrations import schedule_mac
        from .mail_ui import ensure_mail_permission

        cals = self._checked()
        if not cals:
            QMessageBox.information(self, "캘린더", "캘린더를 하나 이상 체크해 주세요.")
            return
        d = self.default.currentData()
        self.project.set_setting("calendar", {"calendars": cals, "default": d["id"] if d else cals[0]["id"]})
        if schedule_mac.supported():
            if self.here.isChecked():
                if not ensure_mail_permission(self):
                    return
                schedule_mac.install_reminders()
            elif schedule_mac.reminders_installed():
                schedule_mac.uninstall_reminders()
        self.accept()


def _dot_icon(color: str) -> QIcon:
    s = tokens.space("icon-sm")
    pm = QPixmap(s * 2, s * 2)
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(color or tokens.raw("accent")))
    d = tokens.space("space-2") + 2
    p.drawEllipse((s - d) / 2, (s - d) / 2, d, d)
    p.end()
    return QIcon(pm)


class EventInspector(QScrollArea):
    """The event inspector (tahoe inset groups): title and calendar, date and time with a 종일
    switch, repeat and mail alert, place and assignee, notes. 저장 is the one prominent
    button; 일정 삭제… is the red Delete button, leading, kept apart and confirmed.
    RegionLayout: a raised face beside the recessed canvas."""

    def __init__(self, page: "SchedulePage"):
        super().__init__()
        self.page = page
        self.ev: Event | None = None
        self.from_calendar = ""
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setObjectName("inspectorScroll")
        body = QWidget()
        body.setObjectName("inspector")
        self.setWidget(body)
        lay = QVBoxLayout(body)
        m = tokens.space("margin")
        lay.setContentsMargins(m, m, m, m)
        lay.setSpacing(tokens.space("space-2"))

        # actions at the top (never at the window's bottom edge): close · 취소 · 저장
        bar = QHBoxLayout()
        self.close_btn = icons.set_icon(QPushButton(), "xmark", "닫기 (esc)")
        self.close_btn.setObjectName("plain")
        self.close_btn.clicked.connect(self.page.close_inspector)
        self.heading = QLabel("일정")
        self.heading.setObjectName("sectionTitle")
        self.cancel_btn = QPushButton("취소")
        self.cancel_btn.setObjectName("plain")
        self.cancel_btn.clicked.connect(self.page.close_inspector)
        self.save_btn = QPushButton("저장")
        self.save_btn.setObjectName("primary")
        self.save_btn.setToolTip("Google 캘린더에 저장합니다 (⌘S)")
        self.save_btn.clicked.connect(self._save)
        bar.addWidget(self.close_btn)
        bar.addWidget(self.heading, 1)
        bar.addWidget(self.cancel_btn)
        bar.addWidget(self.save_btn)
        lay.addLayout(bar)
        self.readonly_note = QLabel("보기만 가능한 캘린더라 고칠 수 없습니다.")
        self.readonly_note.setObjectName("muted")
        self.readonly_note.setWordWrap(True)
        lay.addWidget(self.readonly_note)

        top = InsetGroup()
        self.title = QLineEdit()
        self.title.setPlaceholderText("새 일정")
        self.title.setObjectName("eventTitle")
        self.title.returnPressed.connect(self._save)
        self.cal = QComboBox()
        top.add_row("제목", self.title, expand=True)
        top.add_row("캘린더", self.cal, expand=True)
        lay.addWidget(top)

        lay.addWidget(inset_title("날짜 및 시간"))
        when = InsetGroup()
        self.all_day = Switch()
        self.all_day.setAccessibleName("종일")
        self.all_day.toggled.connect(self._all_day)
        when.add_row("종일", self.all_day)
        self.start_d, self.start_t = QDateEdit(), QTimeEdit()
        self.end_d, self.end_t = QDateEdit(), QTimeEdit()
        for d in (self.start_d, self.end_d):
            d.setCalendarPopup(True)
            d.setDisplayFormat("M/d ddd")
            d.setLocale(QLocale(QLocale.Korean, QLocale.SouthKorea))
        ko = QLocale(QLocale.Korean, QLocale.SouthKorea)
        for t in (self.start_t, self.end_t):
            t.setLocale(ko)
            # typed or arrow keys; the stepper is hidden (base.qss [compact]) to keep the row narrow
            t.setProperty("compact", True)
            t.setDisplayFormat("a h:mm")
        self.start_t.timeChanged.connect(self._keep_length)
        self.start_d.dateChanged.connect(self._keep_length)
        when.add_row("시작", self.start_d, self.start_t, expand=True, fit_label=True, stretch=(4, 3))
        when.add_row("종료", self.end_d, self.end_t, expand=True, fit_label=True, stretch=(4, 3))
        lay.addWidget(when)

        more = InsetGroup()
        self.repeat = QComboBox()
        for key, label in REPEAT_CHOICES:
            self.repeat.addItem(label, key)
        self.repeat_row = more.add_row("반복", self.repeat, expand=True)
        self.repeat_note = QLabel("반복 일정 · 이 일정만 바뀝니다")
        self.repeat_note.setObjectName("muted")
        self.repeat_note_row = more.add_row("반복", self.repeat_note)
        self.notify = QComboBox()
        for minutes, label in NOTIFY_CHOICES:
            self.notify.addItem(label, minutes)
        self.notify.setToolTip("담당자(또는 전체 구성원)에게 시작 전에 한 번 메일을 보냅니다")
        more.add_row("메일 알림", self.notify, expand=True)
        self.more = more
        lay.addWidget(more)

        att = InsetGroup()
        self.att_exclude = Switch()
        self.att_exclude.setToolTip("켜면 이 일정이 있는 날은 출퇴근 기록의 출근일·지각 계산에서 빠집니다")
        att.add_row("출퇴근 기록에서 제외", self.att_exclude)
        lay.addWidget(att)
        att_note = QLabel("켜면 이 일정이 있는 날(여러 날이면 모두)은 출퇴근 기록에서 빠집니다.")
        att_note.setObjectName("muted")
        att_note.setWordWrap(True)
        att_note.setContentsMargins(tokens.space("row-pad"), 0, 0, 0)
        lay.addWidget(att_note)

        who = InsetGroup()
        self.location = QLineEdit()
        self.location.setPlaceholderText("장소 추가")
        self.assignee = QComboBox()
        who.add_row("장소", self.location, expand=True)
        who.add_row("담당", self.assignee, expand=True)
        lay.addWidget(who)

        lay.addWidget(inset_title("메모"))
        self.desc = QTextEdit()
        self.desc.setAcceptRichText(False)
        self.desc.setPlaceholderText("메모 및 URL")
        self.desc.setFixedHeight(4 * tokens.space("row"))
        lay.addWidget(self.desc)

        links = QHBoxLayout()
        self.open_btn = QPushButton("Google 캘린더에서 열기")
        self.open_btn.setObjectName("plain")
        self.open_btn.clicked.connect(self._open_web)
        self.del_btn = QPushButton("일정 삭제…")
        self.del_btn.setObjectName("destructive")
        icons.set_icon(self.del_btn, "trash")
        self.del_btn.clicked.connect(lambda: self.page.delete_event(self.ev))
        links.addWidget(self.del_btn)  # Delete: leading, never the trailing slot
        links.addStretch(1)
        links.addWidget(self.open_btn)
        lay.addLayout(links)
        lay.addStretch(1)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self._save, context=Qt.WidgetWithChildrenShortcut)
        QShortcut(QKeySequence("Escape"), self, activated=self.page.close_inspector,
                  context=Qt.WidgetWithChildrenShortcut)

    # ---- filling -------------------------------------------------------------------
    def show_event(self, ev: Event) -> None:
        self.ev = ev
        self.from_calendar = ev.calendar_id
        project = self.page.project
        new = not ev.id
        self.heading.setText("새 일정" if new else "일정")
        self.title.setText(ev.title)
        self.cal.blockSignals(True)
        self.cal.clear()
        for c in self.page.calendars():
            if c.get("writable", True) or c["id"] == ev.calendar_id:
                self.cal.addItem(_dot_icon(c.get("color", "")), c.get("name") or c["id"], c)
        want = ev.calendar_id or gcalendar.default_calendar(project.setting("calendar") if project else None)
        idx = next((i for i in range(self.cal.count()) if self.cal.itemData(i)["id"] == want), 0)
        self.cal.setCurrentIndex(max(0, idx))
        self.cal.blockSignals(False)
        s, e = cv.span(ev)
        if ev.all_day:
            e = e - timedelta(days=1)
        self._loading = True
        self.all_day.setChecked(ev.all_day)
        self.start_d.setDate(QDate(s.year, s.month, s.day))
        self.start_t.setTime(QTime(s.hour, s.minute))
        self.end_d.setDate(QDate(e.year, e.month, e.day))
        self.end_t.setTime(QTime(e.hour, e.minute) if not ev.all_day else QTime(s.hour + 1 if s.hour < 23 else 23, 0))
        self._length = (cv.span(ev)[1] - cv.span(ev)[0]) if not ev.all_day else timedelta(hours=1)
        self._loading = False
        self._all_day(ev.all_day)
        self.repeat.setCurrentIndex(max(0, self.repeat.findData(ev.repeat)))
        self.repeat_row.setVisible(new)
        self.repeat_note_row.setVisible(bool(ev.recurring_id))
        self.more.restripe()
        self.notify.setCurrentIndex(max(0, self.notify.findData(ev.notify_min)))
        self.location.setText(ev.location)
        self.assignee.clear()
        self.assignee.addItem("지정 안 함", ("", ""))
        self.assignee.addItem("전체 구성원", ("all", "전체"))
        for m in (project.members() if project else []):
            if m.get("email"):
                self.assignee.addItem(m["name"], (m["email"], m["name"]))
                self.assignee.setItemData(self.assignee.count() - 1, m["email"], Qt.ToolTipRole)
        idx = next((i for i in range(self.assignee.count()) if self.assignee.itemData(i)[0] == ev.assignee), -1)
        if idx < 0 and ev.assignee:
            self.assignee.addItem(f"{ev.assignee_name or ev.assignee} (구성원 아님)", (ev.assignee, ev.assignee_name))
            idx = self.assignee.count() - 1
        self.assignee.setCurrentIndex(max(idx, 0))
        self.desc.setPlainText(ev.description)
        self.att_exclude.setChecked(str((ev.extra or {}).get(EXCLUDE_KEY, "")) == "1")  # new: off
        writable = self.page.writable(ev)
        for w in (self.title, self.cal, self.all_day, self.start_d, self.start_t, self.end_d, self.end_t,
                  self.repeat, self.notify, self.location, self.assignee, self.desc, self.att_exclude):
            w.setEnabled(writable)
        self.readonly_note.setVisible(not writable)
        self.save_btn.setVisible(writable)
        self.cancel_btn.setVisible(writable)
        self.del_btn.setVisible(writable and not new)
        self.open_btn.setVisible(bool(ev.html_link))
        if new:
            self.title.setFocus()
            self.title.selectAll()

    def _all_day(self, on: bool) -> None:
        self.start_t.setVisible(not on)
        self.end_t.setVisible(not on)

    def _keep_length(self) -> None:
        """Moving the start moves the end with it, like Calendar."""
        if getattr(self, "_loading", False) or self.all_day.isChecked():
            return
        s = datetime.combine(self.start_d.date().toPython(), self.start_t.time().toPython())
        e = s + getattr(self, "_length", timedelta(hours=1))
        self.end_d.setDate(QDate(e.year, e.month, e.day))
        self.end_t.setTime(QTime(e.hour, e.minute))

    def _open_web(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        if self.ev and self.ev.html_link:
            QDesktopServices.openUrl(QUrl(self.ev.html_link))

    # ---- saving --------------------------------------------------------------------
    def _save(self) -> None:
        ev = self.ev
        if ev is None or not self.page.writable(ev):
            return
        if not self.title.text().strip():
            self.title.setProperty("invalid", True)
            self.title.style().polish(self.title)
            self.title.setPlaceholderText("제목을 적어 주세요")
            self.title.setFocus()
            return
        if self.cal.count() == 0:
            QMessageBox.information(self, "일정", "일정을 넣을 수 있는 캘린더가 없습니다. ‘캘린더 설정…’에서 골라 주세요.")
            return
        all_day = self.all_day.isChecked()
        sd, ed = self.start_d.date().toPython(), self.end_d.date().toPython()
        if all_day:
            s, e = sd, max(ed, sd)
        else:
            s = datetime.combine(sd, self.start_t.time().toPython()).astimezone()
            e = datetime.combine(ed, self.end_t.time().toPython()).astimezone()
            if e <= s:
                QMessageBox.information(self, "일정", "종료 시간이 시작보다 빠릅니다.")
                return
        out = Event(**ev.__dict__)
        c = self.cal.currentData()
        out.calendar_id, out.calendar_name, out.color = c["id"], c.get("name", ""), c.get("color", "")
        out.title = self.title.text().strip()
        out.all_day = all_day
        out.start, out.end = s, e
        out.location = self.location.text().strip()
        out.description = self.desc.toPlainText().strip()
        out.extra = dict(ev.extra or {})
        # "0" rather than removing: a patch keeps private properties it is not given
        out.extra[EXCLUDE_KEY] = "1" if self.att_exclude.isChecked() else "0"
        out.repeat = self.repeat.currentData() if not ev.id else ""
        old_alert = (ev.assignee, ev.notify_min)
        out.assignee, out.assignee_name = self.assignee.currentData()
        out.notify_min = self.notify.currentData()
        if (out.assignee, out.notify_min) != old_alert:
            out.notified = ""  # alert changed: send again
        self.title.setProperty("invalid", False)
        self.title.style().polish(self.title)
        self.page.save_event(out, self.from_calendar)


class SchedulePage(QWidget):
    open_work = Signal(str)  # a 업무 task was opened: show its project in the Work tab
    open_reservation = Signal(str)  # a 장비 예약 booking was opened: show it in its tab

    def __init__(self, parent=None):
        super().__init__(parent)
        self.work = None  # WorkController: its tasks are shown as the 업무 calendar
        self.reservations = lambda: None  # () -> ReservationBoard: bookings shown as 장비 예약
        self.project = None
        self.events: list[Event] = []
        self.draft: Event | None = None      # a new event being added (not saved yet)
        self.current: Event | None = None
        self.day = date.today()               # the date the view is on
        data = app_settings.load_settings()
        self.view = data.get(VIEW_KEY, "week") if data.get(VIEW_KEY) in dict(VIEWS) else "week"
        self._loaded: tuple | None = None     # (calendar ids, first day, last day)

        root = QVBoxLayout(self)
        m = tokens.space("margin")
        root.setContentsMargins(m, tokens.space("space-2"), m, m)  # inside the canvas box
        root.setSpacing(tokens.space("space-2"))

        # row 1: capsules — list toggle + new event | view switch | reload + settings
        bar = QHBoxLayout()
        self.list_btn = icons.set_icon(QPushButton(), "sidebar.left", "캘린더 목록 보기/숨기기 (⌥⌘S)")
        self.list_btn.clicked.connect(self.toggle_list)
        self.add_btn = icons.set_icon(QPushButton(), "plus", "새 일정 (⌘N)")
        self.add_btn.clicked.connect(lambda: self.new_event())
        bar.addWidget(glass_capsule(self.list_btn))
        bar.addWidget(glass_capsule(self.add_btn))
        bar.addStretch(1)
        self.seg = Segmented(VIEWS)
        self.seg.changed.connect(self.set_view)
        for key, label in VIEWS:
            self.seg.buttons[key].setToolTip(f"{label} 단위로 보기")
        bar.addWidget(self.seg)
        bar.addStretch(1)
        self.reload_btn = icons.set_icon(QPushButton(), "arrow.clockwise", "일정 새로고침 (⌥⌘R)")
        self.reload_btn.clicked.connect(lambda: self.load(force=True))
        self.settings_btn = icons.set_icon(QPushButton(), "gearshape", "캘린더 설정… (⌥⌘,)")
        self.settings_btn.clicked.connect(self.edit_settings)
        bar.addWidget(glass_capsule(self.reload_btn, self.settings_btn))
        root.addLayout(bar)

        # row 2: big title (2026년 4월) and ‹ 오늘 ›
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.title = QLabel()
        self.title.setObjectName("calendarTitle")
        self.title.setTextFormat(Qt.RichText)
        self.subtitle = QLabel()
        self.subtitle.setObjectName("calendarSubtitle")
        self.subtitle.setVisible(False)
        titles.addWidget(self.title)
        titles.addWidget(self.subtitle)
        head.addLayout(titles)
        head.addStretch(1)
        self.status = QLabel()
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        self.status.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        head.addWidget(self.status, 1)
        prev_btn = icons.set_icon(QPushButton(), "chevron.left", "이전 (⌘←)")
        prev_btn.clicked.connect(lambda: self.step(-1))
        today_btn = QPushButton("오늘")
        today_btn.setToolTip("오늘로 가기 (⌘T)")
        today_btn.clicked.connect(self.go_today)
        next_btn = icons.set_icon(QPushButton(), "chevron.right", "다음 (⌘→)")
        next_btn.clicked.connect(lambda: self.step(1))
        head.addWidget(glass_capsule(prev_btn, today_btn, next_btn))
        root.addLayout(head)

        # body: calendar list + mini month | view | inspector
        body = QHBoxLayout()
        body.setSpacing(tokens.space("gap-group"))
        self.side = QWidget()
        self.side.setFixedWidth(tokens.space("sidebar-width") - tokens.space("gap-section"))
        sl = QVBoxLayout(self.side)
        sl.setContentsMargins(0, 0, 0, tokens.space("tahoe-inset"))
        self.cal_list = cv.CalendarList()
        self.cal_list.toggled.connect(self._visibility_changed)
        sl.addWidget(self.cal_list)
        sl.addStretch(1)
        self.mini = cv.MiniMonth()
        self.mini.date_clicked.connect(self.go_date)
        sl.addWidget(self.mini)
        body.addWidget(self.side)

        self.stack = QStackedWidget()
        self.time_view = cv.TimeView()
        self.month_view = cv.MonthGrid()
        self.year_view = cv.YearGrid()
        for w in (self.time_view, self.month_view, self.year_view):
            self.stack.addWidget(w)
        for v in self.time_view.views() + [self.month_view, self.year_view]:
            v.writable = self.writable
            v.selected.connect(self.select)
            v.opened.connect(self.open_event)
            v.create.connect(self.new_event_at)
            v.moved.connect(self.move_event)
            v.day_opened.connect(lambda d: (self.go_date(d), self.set_view("day")))
        self.time_view.header.day_clicked.connect(lambda d: (self.go_date(d), self.set_view("day")))
        self.month_view.day_clicked.connect(self._month_day)
        self.year_view.month_opened.connect(lambda d: (self.go_date(d), self.set_view("month")))
        body.addWidget(self.stack, 1)

        # right: inspector (mini month above it in the day view, like Calendar)
        # in the window's inspector box (RegionLayout boxes), which pads and paints it
        self.right = self.inspector_panel = InspectorPanel(wanted=False)
        rl = QVBoxLayout(self.right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(tokens.space("gap-group"))
        self.day_mini = cv.MiniMonth()
        self.day_mini.date_clicked.connect(self.go_date)
        rl.addWidget(self.day_mini)
        self.inspector = EventInspector(self)
        rl.addWidget(self.inspector, 1)
        self.empty_note = QLabel("일정을 고르면 여기에서 보고 고칠 수 있습니다.\n빈 시간을 끌거나 두 번 누르면 새 일정을 만듭니다.")
        self.empty_note.setObjectName("muted")
        self.empty_note.setWordWrap(True)
        self.empty_note.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        rl.addWidget(self.empty_note, 1)
        self.right.setFixedWidth(tokens.space("inspector-width"))
        root.addLayout(body, 1)

        ctx = Qt.WidgetWithChildrenShortcut
        QShortcut(QKeySequence("Ctrl+N"), self, activated=lambda: self.new_event(), context=ctx)
        QShortcut(QKeySequence("Ctrl+T"), self, activated=self.go_today, context=ctx)
        QShortcut(QKeySequence("Ctrl+Left"), self, activated=lambda: self.step(-1), context=ctx)
        QShortcut(QKeySequence("Ctrl+Right"), self, activated=lambda: self.step(1), context=ctx)
        QShortcut(QKeySequence("Backspace"), self, activated=self._delete_key, context=ctx)
        QShortcut(QKeySequence("Ctrl+Alt+S"), self, activated=self.toggle_list, context=ctx)
        QShortcut(QKeySequence("Ctrl+Alt+R"), self, activated=lambda: self.load(force=True), context=ctx)
        QShortcut(QKeySequence("Ctrl+Alt+,"), self, activated=self.edit_settings, context=ctx)
        self.seg.set_current(self.view)
        self.side.setVisible(data.get("schedule_list", True))
        self._apply_view()

    # ---- data ------------------------------------------------------------------------
    def set_project(self, project) -> None:
        self.project = project
        self._loaded = None
        self.events, self.draft, self.current = [], None, None
        self.cal_list.set_calendars(self._cal_entries(), self._hidden())

    def calendars(self) -> list[dict]:
        return gcalendar.selected_calendars(self.project.setting("calendar") if self.project else None)

    # ---- 업무 (Work) tasks as a calendar of their own ---------------------------------------
    WORK_CAL = {"id": "work", "name": "업무", "color": "", "writable": True}

    def _cal_entries(self) -> list[dict]:
        """The chosen Google calendars plus 업무 when there are work projects."""
        cals = list(self.calendars())
        if self.work is not None and self.work.projects():
            cals.append(dict(self.WORK_CAL, color=tokens.color("accent").name()))
        board = self.reservations()
        if board is not None and board.equipment():
            cals.append(dict(self.RES_CAL, color=tokens.color("orange").name()))
        return cals

    RES_CAL = {"id": "reservations", "name": "장비 예약", "color": "", "writable": True}

    @staticmethod
    def is_work(ev: Event | None) -> bool:
        """업무 tasks and 장비 예약 bookings: edited in their own tabs, not in Google Calendar."""
        return ev is not None and ev.calendar_id in ("work", "reservations")

    def work_changed(self) -> None:
        self.cal_list.set_calendars(self._cal_entries(), self._hidden())
        if self.current is not None and self.is_work(self.current):
            self.current = None
        self._refresh_views()

    def calendar_id(self) -> str:
        return ",".join(c["id"] for c in self.calendars())

    def writable(self, ev: Event) -> bool:
        if not ev.calendar_id and not ev.id:
            return True
        return next((c.get("writable", True) for c in self.calendars() if c["id"] == ev.calendar_id), True)

    @staticmethod
    def _hidden() -> set[str]:
        return set(app_settings.load_settings().get(HIDDEN_KEY, []))

    def _visibility_changed(self) -> None:
        data = app_settings.load_settings()
        data[HIDDEN_KEY] = sorted(self.cal_list.hidden())
        app_settings.save_settings(data)
        self._refresh_views()

    def visible_events(self) -> list[Event]:
        hidden = self._hidden()
        out = [e for e in self.events if e.calendar_id not in hidden]
        if self.work is not None and "work" not in hidden:
            out += self.work.calendar_events()
        board = self.reservations()
        if board is not None and "reservations" not in hidden:
            out += board.calendar_events()
        if self.draft is not None:
            out.append(self.draft)
        return out

    def range(self) -> tuple[date, date]:
        """First and last day the current view shows."""
        d = self.day
        if self.view == "day":
            return d, d
        if self.view == "week":
            s = cv.week_start(d)
            return s, s + timedelta(days=6)
        if self.view == "month":
            s = cv.month_grid_start(d)
            return s, s + timedelta(days=41)
        return date(d.year, 1, 1), date(d.year, 12, 31)

    def showEvent(self, e) -> None:  # load the first time the tab is opened
        super().showEvent(e)
        self.load()
        if self.view in ("day", "week"):
            self.time_view.scroll_to_morning()

    def load(self, force: bool = False) -> None:
        """Read the events of the shown range (a whole year at a time in the year view;
        otherwise the month around it, so stepping by day or week needs no new request)."""
        from .cloud import run_blocking
        first, last = self.range()
        if self.view != "year":
            first = min(first, cv.month_grid_start(self.day))
            last = max(last, cv.month_grid_start(self.day) + timedelta(days=41))
        cal = self.calendar_id()
        if not force and self._loaded and self._loaded[0] == cal and self._loaded[1] <= first and last <= self._loaded[2]:
            self._refresh_views()
            return
        cals = self.calendars()
        self.cal_list.set_calendars(self._cal_entries(), self._hidden())
        if not cal:
            self.events = []
            self._set_status("‘캘린더 설정…’(⚙)에서 보여 줄 Google 캘린더를 고르세요.")
            self._refresh_views()
            return
        if not gcalendar.can_use():
            self.events = []
            self._set_status("캘린더 권한이 필요합니다. ‘캘린더 설정…’(⚙)을 누르면 로그인 창이 열립니다.")
            self._refresh_views()
            return
        start = datetime.combine(first, time()).astimezone()
        end = datetime.combine(last + timedelta(days=1), time()).astimezone()
        try:
            self.events, errors = run_blocking(self, lambda: gcalendar.list_all(cals, start, end), "일정을 읽는 중…")
        except Exception as exc:  # noqa: BLE001
            self.events = []
            self._set_status(f"일정을 읽지 못했습니다: {exc}")
        else:
            self._loaded = (cal, first, last)
            self._set_status("⚠ 읽지 못한 캘린더: " + "; ".join(errors) if errors else "")
        if self.current is not None and self.current.id:  # keep the selection across reloads
            self.current = next((e for e in self.events if e.id == self.current.id), None)
            if self.current is None:
                self.close_inspector()
        self._refresh_views()

    def _set_status(self, text: str) -> None:
        self.status.setText(text)

    # ---- views -----------------------------------------------------------------------
    def set_view(self, view: str) -> None:
        self.view = view
        self.seg.set_current(view)
        data = app_settings.load_settings()
        data[VIEW_KEY] = view
        app_settings.save_settings(data)
        self._apply_view()
        self.load()
        if view in ("day", "week"):
            self.time_view.scroll_to_morning()

    def _apply_view(self) -> None:
        v = self.view
        self.stack.setCurrentWidget({"day": self.time_view, "week": self.time_view,
                                     "month": self.month_view, "year": self.year_view}[v])
        self.mini.setVisible(v != "day")
        self._update_right()
        self._refresh_views()

    def _update_right(self) -> None:
        editing = self.current is not None and not self.is_work(self.current)
        day = self.view == "day"
        self.right.set_wanted(day or editing)
        self.day_mini.setVisible(day)
        self.inspector.setVisible(editing)
        self.empty_note.setVisible(day and not editing)

    def _refresh_views(self) -> None:
        d = self.day
        first, last = self.range()
        evs = self.visible_events()
        cur = self.current
        if self.view in ("day", "week"):
            days = [first + timedelta(days=i) for i in range((last - first).days + 1)]
            self.time_view.set_days(days)
            self.time_view.set_events(evs, cur)
        elif self.view == "month":
            self.month_view.set_month(d, d)
            self.month_view.set_events(evs, cur)
        else:
            self.year_view.set_year(d.year)
            self.year_view.set_events(evs, cur)
        for mm in (self.mini, self.day_mini):
            mm.set_selected(d)
            mm.set_marked({x for e in evs for x in cv.days_of(e)})
        self._titles()

    def _titles(self) -> None:
        d = self.day
        if self.view == "day":
            self.title.setText(f"<b>{d.year}년 {d.month}월 {d.day}일</b>")
            self.subtitle.setText(cv.WEEKDAYS[(d.weekday() + 1) % 7] + "요일")
            self.subtitle.setVisible(True)
        elif self.view == "year":
            self.title.setText(f"<b>{d.year}년</b>")
            self.subtitle.setVisible(False)
        else:
            self.title.setText(f"{d.year}년 <b>{d.month}월</b>")
            self.subtitle.setVisible(False)

    def step(self, n: int) -> None:
        if self.view == "day":
            self.day += timedelta(days=n)
        elif self.view == "week":
            self.day += timedelta(days=7 * n)
        elif self.view == "month":
            self.day = cv.add_months(self.day, n)
        else:
            self.day = cv.add_months(self.day, 12 * n)
        self.load()

    def go_today(self) -> None:
        self.go_date(date.today())
        if self.view in ("day", "week"):
            self.time_view._scrolled = False
            self.time_view.scroll_to_morning()

    def go_date(self, d: date) -> None:
        self.day = d
        self.load()

    def _month_day(self, d: date) -> None:
        self.day = d
        if d.month != self.month_view.month.month:
            self.load()
        else:
            self._refresh_views()

    def toggle_list(self) -> None:
        on = not self.side.isVisible()
        self.side.setVisible(on)
        data = app_settings.load_settings()
        data["schedule_list"] = on
        app_settings.save_settings(data)

    # ---- selection and editing ----------------------------------------------------------
    def select(self, ev: Event | None) -> None:
        if ev is None:
            if self.draft is not None:  # clicking away drops an unsaved new event
                self.draft = None
            self.close_inspector()
            return
        if ev is not self.draft:
            self.draft = None
        if self.is_work(ev):  # 업무 tasks are edited in the Work tab: just highlight here
            self.current = ev
            self._update_right()
            self._refresh_views()
            return
        self.current = ev
        self.inspector.show_event(ev)
        self._update_right()
        self._refresh_views()

    def open_event(self, ev: Event) -> None:
        if self.is_work(ev):
            if ev.calendar_id == "reservations":
                self.open_reservation.emit(ev.extra.get("reservation", ""))
            else:
                self.open_work.emit(ev.extra.get("work_project", ""))
            return
        self.select(ev)
        if self.writable(ev):
            self.inspector.title.setFocus()

    def close_inspector(self) -> None:
        self.draft = None
        self.current = None
        self._update_right()
        self._refresh_views()

    def _ready(self) -> bool:
        if not self.calendar_id():
            self.edit_settings()
            return False
        return ensure_calendar_permission(self)

    def new_event(self, day: date | None = None) -> None:
        """⌘N / +: a one-hour event at the next full hour of the shown day."""
        d = day or self.day
        now = datetime.now()
        h = now.hour + 1 if d == now.date() else 9
        s = datetime.combine(d, time(min(h, 23)))
        self.new_event_at(s, s + timedelta(hours=1), False)

    def new_event_at(self, start: datetime, end: datetime, all_day: bool) -> None:
        if not self._ready():
            return
        setting = self.project.setting("calendar") if self.project else None
        cid = gcalendar.default_calendar(setting)
        c = next((c for c in self.calendars() if c["id"] == cid), {"id": cid})
        ev = Event(title="", calendar_id=cid, calendar_name=c.get("name", ""), color=c.get("color", ""))
        ev.all_day = all_day
        ev.start, ev.end = (start.date(), end.date()) if all_day else (start.astimezone(), end.astimezone())
        ev.calendar_id = ""  # not saved yet: the inspector picks the calendar
        self.draft = ev
        ev.color = c.get("color", "")
        self.current = ev
        self.inspector.show_event(ev)
        self._update_right()
        self._refresh_views()

    def save_event(self, ev: Event, from_calendar: str) -> None:
        from .cloud import run_blocking
        try:
            run_blocking(self, lambda: gcalendar.save_event(ev.calendar_id, ev, from_calendar=from_calendar),
                         "일정을 저장하는 중…")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "일정", f"저장하지 못했습니다.\n{exc}")
            return
        from ..integrations import schedule_mac
        if ev.notify_min and schedule_mac.supported() and not schedule_mac.reminders_installed():
            QMessageBox.information(self, "메일 알림", "알림 메일은 ‘캘린더 설정…’에서 ‘이 컴퓨터에서 일정 알림 메일 보내기’를 "
                                    "켠 컴퓨터가 보냅니다. 아직 켜진 컴퓨터가 없다면 한 대에서 켜 주세요.")
        self.draft = None
        self.current = None
        self._update_right()
        self.load(force=True)

    def move_event(self, ev: Event, start: datetime, end: datetime) -> None:
        """Dragged to a new time: saved right away (the drag is the confirmation)."""
        if self.is_work(ev) and ev.calendar_id == "reservations":  # moved: same checks as its tab
            board = self.reservations()
            r = next((x for x in board.reservations() if x.id == ev.extra.get("reservation")), None) if board else None
            if r is not None:
                from ..core.reservations import Reservation
                s, e = [(t.astimezone().replace(tzinfo=None) if t.tzinfo else t) for t in (start, end)]
                moved = Reservation(r.id, r.equipment_id, r.equipment, r.person, s, e, r.purpose, r.notify_min, r.created)
                problem = board.check(moved)
                if problem:
                    QMessageBox.warning(self, "장비 예약", f"옮기지 못했습니다. {problem}")
                else:
                    board.save_reservation(moved)
            self._refresh_views()
            return
        if self.is_work(ev):  # dragged to another day: the work task moves with it
            self.work.move(ev.extra["work_task"], start.date())
            return
        if ev is self.draft:
            ev.start, ev.end = start.astimezone(), end.astimezone()
            self.inspector.show_event(ev)
            self._refresh_views()
            return
        if not self.writable(ev) or not ensure_calendar_permission(self):
            return
        out = Event(**ev.__dict__)
        out.start, out.end = start.astimezone(), end.astimezone()
        out.repeat = ""
        self.save_event(out, ev.calendar_id)

    def _delete_key(self) -> None:
        if self.current is not None and self.current.id and not self.inspector.hasFocus():
            fw = self.focusWidget()
            if isinstance(fw, (QLineEdit, QTextEdit)):
                return
            self.delete_event(self.current)

    def delete_event(self, ev: Event | None) -> None:
        from .cloud import run_blocking
        if ev is None or not ev.id or not self.writable(ev) or self.is_work(ev):
            return
        msg = f"‘{ev.title}’ 일정을 삭제할까요?"
        if ev.recurring_id:
            msg += "\n반복 일정 중 이 날짜의 일정만 지웁니다."
        if confirm_delete(self, "일정 삭제", msg) != QMessageBox.Yes:
            return
        try:
            run_blocking(self, lambda: gcalendar.delete_event(ev.calendar_id, ev.id), "일정을 삭제하는 중…")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "일정", f"삭제하지 못했습니다.\n{exc}")
            return
        self.close_inspector()
        self.load(force=True)

    def edit_settings(self) -> None:
        if not ensure_calendar_permission(self):
            return
        try:
            dlg = CalendarSettingsDialog(self.project, self)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "캘린더", f"캘린더 목록을 읽지 못했습니다.\n{exc}")
            return
        if dlg.exec():
            self.cal_list.set_calendars(self.calendars(), self._hidden())
            self.load(force=True)

    def theme_changed(self) -> None:
        self._refresh_views()
        self.cal_list.update()
