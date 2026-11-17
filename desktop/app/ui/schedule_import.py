"""일정표에서 일정 가져오기: the review sheet shown after a schedule picture was read.

    ┌ 일정표에서 일정 가져오기 ───────────────────────────────────────────┐
    │ 원본 이미지 (the cell of the chosen row is outlined) │ 월 · 캘린더 · 끝 시간 · 이름만 있을 때 제목 │
    │ 고른 줄: 읽은 글자, 메모                              │ ✓ 날짜 시간 제목 담당자 위치 상태          │
    │                                         읽기 정보 ·  취소  [N개 추가] │
    └─────────────────────────────────────────────────────────────┘

Nothing is added until 추가 is pressed; every cell can be edited first (double-click),
담당자 is chosen from the member list (or typed). Rows the calendar already has start
switched off; names that were not recognised are marked 확인.
"""
from __future__ import annotations

import re
from datetime import date, time

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QSizePolicy, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import schedule_image as si
from .inset import InsetGroup, inset_title
from .theme import tokens

WD = "월화수목금토일"
COLS = ["", "날짜", "시간", "제목", "담당자", "위치", "상태"]
C_ON, C_DAY, C_TIME, C_TITLE, C_WHO, C_LOC, C_STATE = range(7)
STATUS = {"new": ("새 일정", "green"), "check": ("확인", "orange"), "dup": ("이미 있음", "label-secondary"),
          "edited": ("고침", "accent")}
MINUTE_CHOICES = [(30, "30분"), (60, "1시간"), (90, "1시간 30분"), (120, "2시간")]


def pil_to_pixmap(img) -> QPixmap:
    rgb = img.convert("RGB")
    data = rgb.tobytes("raw", "RGB")
    q = QImage(data, rgb.width, rgb.height, 3 * rgb.width, QImage.Format_RGB888)
    return QPixmap.fromImage(q.copy())


def day_label(d: date) -> str:
    return f"{d.month}/{d.day} ({WD[d.weekday()]})"


def time_label(c: si.Candidate) -> str:
    if c.start is None:
        return "종일"
    return f"{c.start:%H:%M}–{c.end:%H:%M}" if c.end else f"{c.start:%H:%M}"


def parse_day(text: str, year: int) -> date | None:
    m = re.match(r"\s*(\d{1,2})\s*[/.월\-]\s*(\d{1,2})", text or "")
    if not m:
        return None
    try:
        return date(year, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def parse_span(text: str) -> tuple[time | None, time | None] | None:
    """'11:00–12:00', '9:30', '종일' → (start, end); None when unreadable."""
    t = (text or "").strip()
    if not t or t in ("종일", "-"):
        return None, None
    found = re.findall(r"(\d{1,2})\s*:\s*(\d{2})", t)
    try:
        times = [time(int(h), int(m)) for h, m in found[:2]]
    except ValueError:
        return None
    if not times:
        return None
    return times[0], (times[1] if len(times) > 1 else None)


class SourceView(QWidget):
    """The picture, scaled to fit, with the chosen cell outlined."""

    def __init__(self, pixmap: QPixmap):
        super().__init__()
        self.pix = pixmap
        self.rect_px: tuple = ()
        self.setMinimumSize(280, 220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def show_rect(self, rect: tuple) -> None:
        self.rect_px = rect
        self.update()

    def _frame(self) -> QRectF:
        w, h = self.width(), self.height()
        s = min(w / self.pix.width(), h / self.pix.height())
        return QRectF((w - self.pix.width() * s) / 2, 0, self.pix.width() * s, self.pix.height() * s)

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(400, self.heightForWidth(400))

    def heightForWidth(self, w: int) -> int:
        return round(w * self.pix.height() / max(1, self.pix.width()))

    def hasHeightForWidth(self) -> bool:
        return True

    def paintEvent(self, e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setRenderHint(QPainter.Antialiasing)
        f = self._frame()
        p.drawPixmap(f, self.pix, QRectF(self.pix.rect()))
        if self.rect_px:
            s = f.width() / self.pix.width()
            x0, y0, x1, y1 = self.rect_px
            r = QRectF(f.left() + x0 * s, f.top() + y0 * s, (x1 - x0) * s, (y1 - y0) * s)
            acc = tokens.color("accent")
            fill = QColor(acc)
            fill.setAlphaF(0.12)
            p.setBrush(fill)
            p.setPen(QPen(acc, 2.5))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 4, 4)


class ScheduleImportDialog(QDialog):
    """Review what was read. ``result_candidates()`` gives the rows to add after exec()."""
    rebuild = Signal()  # month / length / person title changed: the page re-reads the boxes

    def __init__(self, parent, image, cands: list[si.Candidate], info: dict, members: list[dict],
                 calendars: list[dict], calendar_id: str, opts: dict, file_name: str = "", seconds: float = 0):
        super().__init__(parent)
        self.setWindowTitle("일정표에서 일정 가져오기")
        self.setWindowModality(Qt.WindowModal)
        self.resize(1240, 780)
        self.members = members
        self.opts = dict(opts)
        self.year, self.month = info.get("year", date.today().year), info.get("month", date.today().month)
        self.cands: list[si.Candidate] = []
        self._filling = False

        root = QVBoxLayout(self)
        m = tokens.space("margin")
        root.setContentsMargins(m, m, m, m)
        root.setSpacing(tokens.space("space-2"))
        head = QLabel("일정표에서 일정 가져오기")
        head.setObjectName("sectionTitle")
        root.addWidget(head)
        self.summary = QLabel()
        self.summary.setObjectName("muted")
        self.summary.setWordWrap(True)
        self.file_name = file_name
        root.addWidget(self.summary)

        body = QHBoxLayout()
        body.setSpacing(tokens.space("gap-group"))
        left = QFrame()
        left.setObjectName("card")
        left.setFixedWidth(430)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(m, m, m, m)
        lt = QHBoxLayout()
        t = QLabel("원본 이미지")
        t.setObjectName("sectionTitle")
        lt.addWidget(t)
        lt.addStretch(1)
        hint = QLabel("줄을 고르면 원본 칸이 표시됩니다")
        hint.setObjectName("muted")
        lt.addWidget(hint)
        ll.addLayout(lt)
        self.source = SourceView(pil_to_pixmap(image))
        ll.addWidget(self.source)
        ll.addWidget(inset_title("고른 줄"))
        picked = InsetGroup()
        self.read_lbl = QLabel("—")
        self.read_lbl.setWordWrap(True)
        self.read_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.note_lbl = QLabel("—")
        self.note_lbl.setWordWrap(True)
        picked.add_row("읽은 글자", self.read_lbl, expand=True)
        picked.add_row("메모", self.note_lbl, expand=True)
        ll.addWidget(picked)
        ll.addStretch(1)
        body.addWidget(left)

        right = QVBoxLayout()
        opts_row = QHBoxLayout()
        opts_row.setSpacing(tokens.space("gap-group"))
        self.month_box = QComboBox()
        for k in range(-3, 4):
            n = self.year * 12 + self.month - 1 + k
            self.month_box.addItem(f"{n // 12}년 {n % 12 + 1}월", (n // 12, n % 12 + 1))
        self.month_box.setCurrentIndex(3)
        self.month_box.setToolTip("달력 모양(1일의 요일, 마지막 날)으로 맞춘 달입니다. 다르면 고르세요.")
        self.cal_box = QComboBox()
        for c in calendars:
            if c.get("writable", True):
                self.cal_box.addItem(c.get("name") or c["id"], c["id"])
        i = self.cal_box.findData(calendar_id)
        self.cal_box.setCurrentIndex(max(i, 0))
        self.len_box = QComboBox()
        for mins, label in MINUTE_CHOICES:
            self.len_box.addItem(label, mins)
        self.len_box.setCurrentIndex(max(0, self.len_box.findData(int(self.opts.get("minutes") or 60))))
        self.len_box.setToolTip("‘오전11:00~’처럼 끝 시간이 없을 때의 길이")
        self.person_title = QLineEdit(self.opts.get("person_title") or si.DEFAULTS["person_title"])
        self.person_title.setFixedWidth(tokens.space("control-md") * 3)
        self.person_title.setToolTip("칸에 사람 이름이나 팀만 있을 때 쓸 일정 제목")
        for label, w in (("월", self.month_box), ("캘린더", self.cal_box), ("끝 시간 없을 때", self.len_box),
                         ("이름만 있을 때 제목", self.person_title)):
            lab = QLabel(label)
            lab.setObjectName("muted")
            opts_row.addWidget(lab)
            opts_row.addWidget(w)
        opts_row.addStretch(1)
        right.addLayout(opts_row)
        self.month_box.currentIndexChanged.connect(self._options_changed)
        self.len_box.currentIndexChanged.connect(self._options_changed)
        self.person_title.editingFinished.connect(self._options_changed)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.setAlternatingRowColors(False)
        hh = self.table.horizontalHeader()
        for c, mode in ((C_ON, QHeaderView.ResizeToContents), (C_DAY, QHeaderView.ResizeToContents),
                        (C_TIME, QHeaderView.ResizeToContents), (C_TITLE, QHeaderView.Stretch),
                        (C_WHO, QHeaderView.Stretch), (C_LOC, QHeaderView.Stretch),
                        (C_STATE, QHeaderView.ResizeToContents)):
            hh.setSectionResizeMode(c, mode)
        self.table.itemSelectionChanged.connect(self._picked)
        self.table.itemChanged.connect(self._edited)
        right.addWidget(self.table, 1)
        body.addLayout(right, 1)
        root.addLayout(body, 1)

        foot = QHBoxLayout()
        self.info_lbl = QLabel(f"읽기: 앱 내장 OCR (이 컴퓨터에서, 인터넷 없이) · {seconds:.0f}초")
        self.info_lbl.setObjectName("muted")
        self.info_lbl.setWordWrap(True)
        foot.addWidget(self.info_lbl, 1)
        cancel = QPushButton("취소")
        cancel.clicked.connect(self.reject)
        self.add_btn = QPushButton()
        self.add_btn.setObjectName("primary")
        self.add_btn.setDefault(True)
        self.add_btn.clicked.connect(self._accept)
        foot.addWidget(cancel)
        foot.addWidget(self.add_btn)
        root.addLayout(foot)
        self.set_candidates(cands)

    # ---- options ----------------------------------------------------------------------
    def chosen_month(self) -> tuple[int, int]:
        return self.month_box.currentData()

    def calendar_id(self) -> str:
        return self.cal_box.currentData() or ""

    def current_opts(self) -> dict:
        out = dict(self.opts)
        out["minutes"] = int(self.len_box.currentData() or 60)
        out["person_title"] = self.person_title.text().strip() or si.DEFAULTS["person_title"]
        return out

    def _options_changed(self) -> None:
        self.year, self.month = self.chosen_month()
        self.rebuild.emit()

    # ---- rows ---------------------------------------------------------------------------
    def set_candidates(self, cands: list[si.Candidate]) -> None:
        self.cands = sorted(cands, key=lambda c: (c.day, c.start or time()))
        self._filling = True
        self.table.setRowCount(len(self.cands))
        names = [m["name"] for m in self.members if m.get("name")]
        for r, c in enumerate(self.cands):
            on = QTableWidgetItem()
            on.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            on.setCheckState(Qt.Checked if c.include else Qt.Unchecked)
            self.table.setItem(r, C_ON, on)
            self.table.setItem(r, C_DAY, QTableWidgetItem(day_label(c.day)))
            self.table.setItem(r, C_TIME, QTableWidgetItem(time_label(c)))
            self.table.setItem(r, C_TITLE, QTableWidgetItem(c.title))
            who = QComboBox()
            who.setEditable(True)
            who.addItem("")
            who.addItems(names)
            who.setCurrentText(c.assignee_name)
            who.setToolTip("구성원 목록에서 고르거나 직접 적습니다 (팀 이름 등)")
            who.currentTextChanged.connect(lambda _t, row=r: self._who_changed(row))
            self.table.setCellWidget(r, C_WHO, who)
            loc = QTableWidgetItem(c.location)
            loc.setForeground(tokens.text("accent"))
            self.table.setItem(r, C_LOC, loc)
            st = QTableWidgetItem()
            st.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(r, C_STATE, st)
            self._paint_status(r)
            for col in (C_DAY, C_TIME, C_TITLE, C_LOC):
                self.table.item(r, col).setToolTip(c.raw)
        self._filling = False
        self._count()
        if self.cands:
            self.table.selectRow(0)

    def _paint_status(self, r: int) -> None:
        c = self.cands[r]
        label, tone = STATUS.get(c.status, STATUS["new"])
        it = self.table.item(r, C_STATE)
        it.setText(label)
        it.setForeground(tokens.text(tone))
        it.setToolTip(c.note)
        dim = not c.include
        for col in (C_DAY, C_TIME, C_TITLE, C_LOC):
            item = self.table.item(r, col)
            if item:
                item.setForeground(tokens.text("label-tertiary" if dim else ("accent" if col == C_LOC else "label")))

    def _who_changed(self, r: int) -> None:
        if self._filling or r >= len(self.cands):
            return
        self._mark_edited(r)
        self._picked()

    def _mark_edited(self, r: int) -> None:
        c = self.cands[r]
        if c.status in ("check", "new"):
            c.status = "edited" if c.status == "check" else c.status
            self._paint_status(r)

    def _edited(self, item: QTableWidgetItem) -> None:
        if self._filling:
            return
        r = item.row()
        c = self.cands[r]
        if item.column() == C_ON:
            c.include = item.checkState() == Qt.Checked
            self._filling = True
            self._paint_status(r)
            self._filling = False
            self._count()
            return
        self._filling = True
        self._mark_edited(r)
        self._filling = False

    def _count(self) -> None:
        n = sum(1 for r in range(self.table.rowCount()) if self.table.item(r, C_ON).checkState() == Qt.Checked)
        checks = sum(1 for c in self.cands if c.status == "check")
        dups = sum(1 for c in self.cands if c.status == "dup")
        self.add_btn.setText(f"{n}개 추가")
        self.add_btn.setEnabled(n > 0)
        parts = [f"{len(self.cands)}개를 읽었습니다"]
        if dups:
            parts.append(f"{dups}개는 이미 있습니다")
        if checks:
            parts.append(f"{checks}개는 확인이 필요합니다")
        self.summary.setText(" · ".join(p for p in [self.file_name, *parts] if p))

    def _picked(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        c = self.cands[rows[0].row()]
        self.source.show_rect(c.rect)
        self.read_lbl.setText(c.raw or "—")
        self.note_lbl.setText(c.note or "—")

    # ---- result -----------------------------------------------------------------------
    def _row(self, r: int) -> si.Candidate | str:
        """The row as edited, or an error text."""
        c = self.cands[r]
        d = parse_day(self.table.item(r, C_DAY).text(), c.day.year)
        if d is None:
            return f"{r + 1}번째 줄의 날짜를 읽을 수 없습니다 (예: 11/5)."
        span = parse_span(self.table.item(r, C_TIME).text())
        if span is None:
            return f"{r + 1}번째 줄의 시간을 읽을 수 없습니다 (예: 11:00–12:00 또는 종일)."
        start, end = span
        if start and not end:
            from datetime import datetime, timedelta
            end = (datetime.combine(d, start) + timedelta(minutes=int(self.len_box.currentData() or 60))).time()
        who = self.table.cellWidget(r, C_WHO).currentText().strip()
        member = next((m for m in self.members if m.get("name") == who), None)
        out = si.Candidate(d, start, end, self.table.item(r, C_TITLE).text().strip() or "(제목 없음)",
                           assignee=(member or {}).get("email", ""), assignee_name=who,
                           location=self.table.item(r, C_LOC).text().strip(), raw=c.raw, raw_name=c.raw_name,
                           rect=c.rect, status=c.status, note=c.note, include=True)
        return out

    def _accept(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        rows = []
        for r in range(self.table.rowCount()):
            if self.table.item(r, C_ON).checkState() != Qt.Checked:
                continue
            got = self._row(r)
            if isinstance(got, str):
                QMessageBox.warning(self, "일정 가져오기", got)
                self.table.selectRow(r)
                return
            rows.append(got)
        if not self.calendar_id():
            QMessageBox.warning(self, "일정 가져오기", "일정을 넣을 캘린더를 고르세요.")
            return
        self._result = rows
        self.accept()

    def result_candidates(self) -> list[si.Candidate]:
        return list(getattr(self, "_result", []))
