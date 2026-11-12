"""업무 요청 tab: short memos (no title) to the people of one 업무.

The table lists every request (filter: 전체 / 나에게 온 요청 / 내가 보낸 요청); the compose
panel on the right picks the 업무, shows who gets it (an owner duty's people, or this turn's
people of a rotation / milestone) and sends the memo. Requests live in the 요청 tab of the 업무
file, the same tab the mobile web app reads and writes. Rows sent from here are mailed by the
web app's server (time trigger, about every 5 minutes), so no app needs to stay open.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QPlainTextEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import settings
from ..core import work as W
from .inset import InsetGroup, inset_title
from .table_model import compact_rows
from .theme import tokens
from .widgets import InspectorPanel, Segmented

COLS = ["날짜", "업무", "요청 내용", "보낸 사람", "받는 사람", "상태"]
FILTERS = [("all", "전체"), ("to", "나에게 온 요청"), ("from", "내가 보낸 요청")]


def me() -> str:
    return settings.load_settings().get("requester_name", "").strip()


def _same(a: str, b: str) -> bool:
    return bool(a) and a.strip().lower() == (b or "").strip().lower()


def is_to(r: W.WorkRequest, name: str) -> bool:
    return any(_same(n, name) for n in r.to)


def filtered(reqs: list[W.WorkRequest], key: str, name: str) -> list[W.WorkRequest]:
    if key == "to":
        return [r for r in reqs if is_to(r, name)]
    if key == "from":
        return [r for r in reqs if _same(r.sender, name)]
    return list(reqs)


class RequestCompose(QWidget):
    def __init__(self, page: "RequestsPage"):
        super().__init__()
        self.page = page
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(tokens.space("space-2"))
        head = QLabel("요청 보내기")
        head.setObjectName("sectionTitle")
        lay.addWidget(head)
        sub = QLabel("업무를 고르면 그 업무 담당자에게 갑니다. 제목 없이 내용만 적어요.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        g = InsetGroup()
        self.work = QComboBox()
        self.work.currentIndexChanged.connect(lambda _: self._target())
        g.add_row("업무", self.work, expand=True)
        self.to = QLabel()
        self.to.setWordWrap(True)
        g.add_row("받는 사람", self.to, expand=True)
        lay.addWidget(g)
        lay.addWidget(inset_title("내용"))
        self.memo = QPlainTextEdit()
        self.memo.setPlaceholderText("예: 내일 오전에 액체질소 충전 업체가 와요. 문 열어 주실 수 있나요?")
        self.memo.setMinimumHeight(120)
        self.memo.textChanged.connect(self._enable)
        lay.addWidget(self.memo)
        note = QLabel("받는 사람에게 메일도 가요 (몇 분 안에).")
        note.setObjectName("muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        row = QHBoxLayout()
        row.addStretch(1)
        self.send = QPushButton("보내기")
        self.send.setObjectName("primary")
        self.send.clicked.connect(self._send)
        row.addWidget(self.send)
        lay.addLayout(row)
        lay.addStretch(1)
        self._people: dict[str, list[str]] = {}

    def fill(self, targets: list[tuple[W.WorkProject, list[str]]], select: str = "") -> None:
        cur = select or self.work.currentData() or ""
        self.work.blockSignals(True)
        self.work.clear()
        self._people = {}
        self.work.addItem("업무를 고르세요", "")
        group = None
        for p, people in targets:
            g = "담당" if p.kind == "owner" else "당번 · 마일스톤"
            if g != group:
                if group is not None:
                    self.work.insertSeparator(self.work.count())
                group = g
            self.work.addItem(f"{p.name}  ·  {g.split(' ')[0]}", p.id)
            self._people[p.id] = people
        self.work.setCurrentIndex(max(0, self.work.findData(cur)))
        self.work.blockSignals(False)
        self._target()

    def _target(self) -> None:
        wid = self.work.currentData() or ""
        people = self._people.get(wid, [])
        self.to.setText(", ".join(people) if wid else "업무를 고르면 자동으로 정해져요")
        if wid and not people:
            self.to.setText("담당자가 없어요 · 업무 탭에서 먼저 정해 주세요")
        self._enable()

    def _enable(self) -> None:
        wid = self.work.currentData() or ""
        self.send.setEnabled(bool(wid and self._people.get(wid) and self.memo.toPlainText().strip()))

    def _send(self) -> None:
        if self.page.send(self.work.currentData() or "", self.memo.toPlainText()):
            self.memo.clear()


class RequestsPage(QWidget):
    changed = Signal()

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl  # WorkController
        self.filter = "all"
        root = QVBoxLayout(self)
        m = tokens.space("margin")
        root.setContentsMargins(m, tokens.space("space-2"), m, m)
        root.setSpacing(tokens.space("space-2"))
        head = QHBoxLayout()
        t = QLabel("업무 요청")
        t.setObjectName("pageTitle")
        head.addWidget(t)
        self.sub = QLabel()
        self.sub.setObjectName("muted")
        head.addWidget(self.sub, 1)
        self.seg = Segmented(FILTERS)
        self.seg.changed.connect(self.set_filter)
        head.addWidget(self.seg)
        root.addLayout(head)
        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        self.table = QTableWidget(0, len(COLS) + 1)
        self.table.setHorizontalHeaderLabels(COLS + [""])
        compact_rows(self.table)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setWordWrap(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        for c in (0, 1, 3, 4, 5, 6):
            hh.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        cl.addWidget(self.table)
        self.empty = QLabel()
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignCenter)
        cl.addWidget(self.empty)
        root.addWidget(card, 1)
        foot = QLabel("요청은 업무 파일의 ‘요청’ 탭에 저장되고 웹앱과 같이 봐요 · 받은 사람이 확인을 누르면 "
                      "보낸 사람에게 ‘확인함’으로 보여요")
        foot.setObjectName("muted")
        foot.setWordWrap(True)
        root.addWidget(foot)

        self.inspector_panel = InspectorPanel(wanted=True)
        il = QVBoxLayout(self.inspector_panel)
        il.setContentsMargins(0, 0, 0, 0)
        self.compose = RequestCompose(self)
        il.addWidget(self.compose)
        self.inspector_panel.setMinimumWidth(300)
        self.inspector_panel.setMaximumWidth(360)
        self.ctrl.changed.connect(self.refresh)
        self.set_filter("all")

    def set_filter(self, key: str) -> None:
        self.filter = key
        self.seg.set_current(key)
        self._fill()

    def choose(self, work_id: str) -> None:
        """Open with this 업무 picked (from the 업무 page's ‘이 업무에 요청 보내기…’)."""
        self.compose.fill(self.ctrl.targets(), work_id)
        self.compose.memo.setFocus()

    def refresh(self) -> None:
        self.compose.fill(self.ctrl.targets())
        self._fill()

    def _fill(self) -> None:
        name = me()
        reqs = self.ctrl.requests()
        waiting = sum(1 for r in reqs if is_to(r, name) and not r.acked)
        if self.ctrl.board is None:
            self.sub.setText("Google Drive 위치 세트를 열면 쓸 수 있습니다")
        elif not name:
            self.sub.setText("설정에서 ‘보내는 사람 이름’을 정하면 나에게 온 요청을 따로 봐요")
        else:
            self.sub.setText(f"{name}님에게 온 요청 중 확인 전 {waiting}")
        rows = filtered(reqs, self.filter, name)
        for i in range(self.table.rowCount()):
            old = self.table.cellWidget(i, len(COLS))
            if old is not None:
                old.hide()  # deleted later by the table; don't let it paint over the new rows meanwhile
                self.table.removeCellWidget(i, len(COLS))
        self.table.setRowCount(len(rows))
        muted = tokens.text("label-secondary")
        for i, r in enumerate(rows):
            vals = [r.date[5:16].replace("-", "/"), r.work, r.memo.replace("\n", " "), r.sender, ", ".join(r.to),
                    "확인함" if r.acked else "확인 전"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 2:
                    it.setToolTip(r.memo)
                if c == 5:
                    it.setForeground(tokens.text("green" if r.acked else "accent"))
                    if r.acked and r.ack_by:
                        it.setToolTip(f"{r.ack_by} · {r.ack_at}")
                elif r.acked:
                    it.setForeground(muted)
                self.table.setItem(i, c, it)
            if name and is_to(r, name) and not r.acked:
                b = QPushButton("확인")
                b.setToolTip("봤다고 보낸 사람에게 알려요")
                b.clicked.connect(lambda _=False, rid=r.id: self.ctrl.ack_request(rid, name))
                self.table.setCellWidget(i, len(COLS), b)
        self.table.setVisible(bool(rows))
        self.empty.setVisible(not rows)
        self.empty.setText({"to": "나에게 온 요청이 없어요.", "from": "보낸 요청이 없어요."}.get(
            self.filter, "아직 요청이 없어요. 오른쪽에서 업무를 골라 보내 보세요."))

    def send(self, work_id: str, memo: str) -> bool:
        name = me()
        if not name:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "업무 요청", "설정에서 ‘보내는 사람 이름’을 먼저 정해 주세요.")
            return False
        return self.ctrl.send_request(name, work_id, memo) is not None
