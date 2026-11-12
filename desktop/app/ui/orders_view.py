"""주문 tab: every order request (from here or from the cart) with its state.

요청 → (mail to the person in charge) → 주문함 → 입고됨, or 취소. The person in charge is
filled from the item's category owner and can be changed. When 알림 is on, the
requester gets a mail each time the state changes.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import settings
from ..core.order_requests import STATES, OrderRequest, OrderRequestBoard, request_mail, state_mail
from .alerts import confirm_delete
from .inset import InsetGroup, inset_title
from .table_model import compact_rows
from .theme import icons, tokens
from .widgets import InspectorPanel, Switch

STATE_TOKEN = {"요청": "accent", "주문함": "orange", "입고됨": "green", "취소": "label-secondary"}
COLS = ["상태", "요청일", "요청자", "품목", "제조사", "Cat#", "수량", "단가", "담당자", "메일"]


def me() -> str:
    return settings.load_settings().get("requester_name", "").strip()


def email_of(project, name: str) -> str:
    n = (name or "").strip().lower()
    for m in project.members() if project else []:
        if (m.get("name") or "").strip().lower() == n:
            return m.get("email", "")
    return ""


def owner_for_category(project, category: str) -> dict:
    for c in project.categories() if project else []:
        if c.name == category:
            o = project.owner(c.id)
            if o.get("email") or o.get("name"):
                return o
    return project.order_contact() if project else {}


class OrderInspector(QWidget):
    def __init__(self, page: "OrdersPage"):
        super().__init__()
        self.page = page
        self.req: OrderRequest | None = None
        self.is_new = True
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(tokens.space("space-2"))
        bar = QHBoxLayout()
        self.heading = QLabel("새 주문")
        self.heading.setObjectName("sectionTitle")
        bar.addWidget(self.heading, 1)
        close = icons.set_icon(QPushButton(), "xmark", "닫기")
        close.setObjectName("plain")
        close.clicked.connect(self.page.close_inspector)
        bar.addWidget(close)
        lay.addLayout(bar)
        self.sub = QLabel("재고에서 고르거나 새 품목을 적습니다.")
        self.sub.setObjectName("muted")
        self.sub.setWordWrap(True)
        lay.addWidget(self.sub)
        self.find = QLineEdit()
        self.find.setPlaceholderText("재고에서 찾기 (이름, Cat#)")
        self.find.textEdited.connect(self._find)
        lay.addWidget(self.find)
        self.found = QListWidget()
        self.found.setMaximumHeight(120)
        self.found.itemClicked.connect(self._pick)
        self.found.setVisible(False)
        lay.addWidget(self.found)
        g = InsetGroup()
        self.item = QLineEdit()
        self.company = QLineEdit()
        self.catalog = QLineEdit()
        self.qty = QLineEdit("1")
        self.price = QLineEdit()
        self.price.setPlaceholderText("₩")
        self.link = QLineEdit()
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.currentTextChanged.connect(self._category_changed)
        for label, w in (("품목", self.item), ("제조사", self.company), ("Cat#", self.catalog), ("수량", self.qty),
                         ("단가", self.price), ("링크", self.link), ("카테고리", self.category)):
            g.add_row(label, w, expand=True)
        lay.addWidget(g)
        lay.addWidget(inset_title("담당자"))
        g2 = InsetGroup()
        self.owner = QComboBox()
        self.owner.setEditable(True)
        self.owner_note = QLabel()
        self.owner_note.setObjectName("muted")
        g2.add_row("담당자", self.owner, expand=True)
        g2.add_row("", self.owner_note)
        self.requester = QComboBox()
        self.requester.setEditable(True)
        g2.add_row("요청자", self.requester, expand=True)
        self.state = QComboBox()
        self.state.addItems(STATES)
        self.state_row = g2.add_row("상태", self.state, expand=True)
        lay.addWidget(g2)
        lay.addWidget(inset_title("용도 · 메모"))
        self.note = QLineEdit()
        self.note.setPlaceholderText("예: WB loading control, 급하지 않음")
        lay.addWidget(self.note)
        g3 = InsetGroup()
        self.notify = Switch()
        self.notify.setChecked(True)
        g3.add_row("상태가 바뀌면 요청자에게 알림", self.notify)
        lay.addWidget(g3)
        row = QHBoxLayout()
        self.del_btn = QPushButton("삭제…")
        self.del_btn.setObjectName("destructive")
        icons.set_icon(self.del_btn, "trash")
        self.del_btn.clicked.connect(lambda: self.page.delete(self.req))
        row.addWidget(self.del_btn)
        row.addStretch(1)
        self.save_btn = QPushButton("저장")
        self.save_btn.clicked.connect(lambda: self._save(send=False))
        self.send_btn = QPushButton("요청 보내기 ✉")
        self.send_btn.setObjectName("primary")
        self.send_btn.clicked.connect(lambda: self._save(send=True))
        row.addWidget(self.save_btn)
        row.addWidget(self.send_btn)
        lay.addLayout(row)
        lay.addStretch(1)

    def fill(self, req: OrderRequest, is_new: bool) -> None:
        self.req, self.is_new = req, is_new
        p = self.page.project
        self.heading.setText("새 주문" if is_new else "주문")
        self.find.setVisible(is_new)
        self.found.setVisible(False)
        self.find.clear()
        self.item.setText(req.item)
        self.company.setText(req.company)
        self.catalog.setText(req.catalog)
        self.qty.setText(req.qty)
        self.price.setText(req.price)
        self.link.setText(req.link)
        self.category.blockSignals(True)
        self.category.clear()
        self.category.addItems([""] + [c.name for c in p.categories()] if p else [""])
        self.category.setCurrentText(req.category)
        self.category.blockSignals(False)
        names = [m.get("name") or m.get("email", "") for m in p.members()] if p else []
        self.owner.clear()
        self.owner.addItems([""] + names)
        self.owner.setCurrentText(req.owner)
        self.requester.clear()
        self.requester.addItems(names if me() in names or not me() else [me()] + names)
        self.requester.setCurrentText(req.requester or me())
        self.state.setCurrentText(req.state)
        self.state_row.setVisible(not is_new)
        self.note.setText(req.note)
        self.notify.setChecked(req.notify)
        self.del_btn.setVisible(not is_new)
        self.send_btn.setText("요청 보내기 ✉" if is_new or not req.mailed else "다시 보내기 ✉")
        self.owner_note.setText("")
        if is_new and req.category:
            self._category_changed(req.category)

    def _find(self, text: str) -> None:
        from ..core.services.search import search
        p = self.page.project
        self.found.clear()
        if not p or len(text.strip()) < 2:
            self.found.setVisible(False)
            return
        for h in search(p, text)[:30]:
            it = QListWidgetItem(f"{h.title} · {h.category.name}")
            it.setData(Qt.UserRole, h.item.id)
            self.found.addItem(it)
        new = QListWidgetItem("＋ 재고에 없는 새 품목")
        new.setData(Qt.UserRole, None)
        self.found.addItem(new)
        self.found.setVisible(True)

    def _pick(self, it: QListWidgetItem) -> None:
        iid = it.data(Qt.UserRole)
        self.found.setVisible(False)
        p = self.page.project
        if iid is None:
            self.item.setText(self.find.text().strip())
            self.item.setFocus()
            return
        from ..core.services.mail_reports import item_line
        item = p.item(iid)
        ln = item_line(p, p.category(item.category_id), item)
        self.item.setText(ln.name)
        self.company.setText(ln.company)
        self.catalog.setText(ln.catalog)
        self.category.setCurrentText(ln.category)

    def _category_changed(self, name: str) -> None:
        o = owner_for_category(self.page.project, name)
        if o.get("name") or o.get("email"):
            self.owner.setCurrentText(o.get("name") or o.get("email"))
            self.owner_note.setText(f"{name} 담당자 (자동)" if name else "주문 담당 (자동)")
        else:
            self.owner_note.setText("이 카테고리는 담당자가 없습니다 · 직접 고르세요")

    def _save(self, send: bool) -> None:
        r = self.req
        if r is None:
            return
        if not self.item.text().strip():
            QMessageBox.warning(self, "주문", "품목 이름을 적어 주세요.")
            return
        p = self.page.project
        old_state = r.state
        r.item, r.company, r.catalog = self.item.text().strip(), self.company.text().strip(), self.catalog.text().strip()
        r.qty, r.price, r.link = self.qty.text().strip() or "1", self.price.text().strip(), self.link.text().strip()
        r.category, r.note, r.notify = self.category.currentText().strip(), self.note.text().strip(), self.notify.isChecked()
        r.owner = self.owner.currentText().strip()
        o = owner_for_category(p, r.category)
        r.owner_email = email_of(p, r.owner) or (o.get("email", "") if (o.get("name") or o.get("email")) == r.owner else "")
        r.requester = self.requester.currentText().strip()
        r.requester_email = email_of(p, r.requester) or r.requester_email
        if not self.is_new:
            r.state = self.state.currentText()
        if not r.requester:
            QMessageBox.warning(self, "주문", "요청자를 골라 주세요.")
            return
        data = settings.load_settings()
        if not data.get("requester_name"):
            data["requester_name"] = r.requester
            settings.save_settings(data)
        self.page.save(r, send=send, old_state=old_state if not self.is_new else "")


class OrdersPage(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.board: OrderRequestBoard | None = None
        self.project = None
        root = QVBoxLayout(self)
        m = tokens.space("margin")
        root.setContentsMargins(m, tokens.space("space-2"), m, m)
        root.setSpacing(tokens.space("space-2"))
        head = QHBoxLayout()
        t = QLabel("주문")
        t.setObjectName("pageTitle")
        head.addWidget(t)
        self.sub = QLabel()
        self.sub.setObjectName("muted")
        head.addWidget(self.sub, 1)
        self.f_state = QComboBox()
        self.f_owner = QComboBox()
        self.f_req = QComboBox()
        for c in (self.f_state, self.f_owner, self.f_req):
            c.currentIndexChanged.connect(lambda _: self._fill())
            head.addWidget(c)
        new = icons.set_icon(QPushButton("새 주문"), "plus")
        new.setObjectName("primary")
        new.clicked.connect(self.new_order)
        head.addWidget(new)
        root.addLayout(head)
        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        compact_rows(self.table, numeric=(6, 7))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.cellClicked.connect(lambda r, _c: self.open(self._ids[r]))
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        cl.addWidget(self.table)
        root.addWidget(card, 1)
        flow = QLabel("흐름:  요청 — 담당자에게 메일 →  주문함 — 요청자에게 알림 →  입고됨 — 재고 수량 더하기 확인   "
                      "·  장바구니의 ‘주문 요청 보내기’도 이 표에 한 줄씩 기록됩니다")
        flow.setObjectName("muted")
        flow.setWordWrap(True)
        root.addWidget(flow)
        self._ids: list[str] = []

        self.inspector_panel = InspectorPanel(wanted=False)
        il = QVBoxLayout(self.inspector_panel)
        il.setContentsMargins(0, 0, 0, 0)
        self.inspector = OrderInspector(self)
        il.addWidget(self.inspector)
        self.inspector_panel.setMinimumWidth(320)
        self.inspector_panel.setMaximumWidth(380)

    def set_board(self, board: OrderRequestBoard | None, project) -> None:
        self.board, self.project = board, project
        self.inspector_panel.set_wanted(False)
        self.refresh()

    def refresh(self) -> None:
        reqs = self.board.requests() if self.board else []
        for combo, label, values in ((self.f_state, "상태", STATES), (self.f_owner, "담당자", sorted({r.owner for r in reqs if r.owner})),
                                     (self.f_req, "요청자", sorted({r.requester for r in reqs if r.requester}))):
            cur = combo.currentData()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(f"{label}: 전체", "")
            for v in values:
                combo.addItem(f"{label}: {v}", v)
            combo.setCurrentIndex(max(0, combo.findData(cur)))
            combo.blockSignals(False)
        from datetime import date
        month = date.today().strftime("%Y-%m")
        n_open = sum(1 for r in reqs if r.is_open)
        n_in = sum(1 for r in reqs if r.state == "입고됨" and r.received.startswith(month))
        self.sub.setText(f"진행 중 {n_open} · 이번 달 입고 {n_in}" if self.board else
                         "Google Drive 위치 세트를 열면 쓸 수 있습니다")
        self._fill()
        self.changed.emit()

    def _fill(self) -> None:
        reqs = self.board.requests() if self.board else []
        st, ow, rq = self.f_state.currentData(), self.f_owner.currentData(), self.f_req.currentData()
        reqs = [r for r in reqs if (not st or r.state == st) and (not ow or r.owner == ow) and (not rq or r.requester == rq)]
        self.table.setRowCount(len(reqs))
        self._ids = [r.id for r in reqs]
        for i, r in enumerate(reqs):
            vals = [r.state, r.day[5:].replace("-", "/"), r.requester, r.item, r.company, r.catalog, r.qty,
                    r.price, r.owner, ("✉ " + r.mailed[5:].replace("-", "/")) if r.mailed else "✉ —"]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setForeground(tokens.text(STATE_TOKEN.get(r.state, "label")))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                if r.state == "취소":
                    it.setForeground(tokens.text("label-secondary"))
                self.table.setItem(i, c, it)

    # ---- actions ---------------------------------------------------------------------------
    def _ready(self) -> bool:
        if self.board is None:
            QMessageBox.information(self, "주문", "Google Drive 위치 세트를 연 뒤에 쓸 수 있습니다.")
            return False
        return True

    def new_order(self, prefill: OrderRequest | None = None) -> None:
        if not self._ready():
            return
        self.table.clearSelection()
        self.inspector.fill(prefill or OrderRequest(requester=me()), True)
        self.inspector_panel.set_wanted(True)

    def open(self, rid: str) -> None:
        req = self.board.get(rid) if self.board else None
        if req is not None:
            self.inspector.fill(req, False)
            self.inspector_panel.set_wanted(True)
            if rid in self._ids:
                self.table.selectRow(self._ids.index(rid))

    def close_inspector(self) -> None:
        self.inspector_panel.set_wanted(False)
        self.table.clearSelection()

    def _menu(self, pos) -> None:
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        rid = self._ids[idx.row()]
        req = self.board.get(rid)
        menu = QMenu(self)
        for s in STATES:
            if s != req.state:
                menu.addAction(f"{s}(으)로 바꾸기", lambda s=s: self.change_state(rid, s))
        menu.addAction("담당자에게 다시 보내기", lambda: self._send([req]))
        menu.addSeparator()
        menu.addAction("삭제…", lambda: self.delete(req))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def change_state(self, rid: str, state: str) -> None:
        req = self.board.get(rid)
        if req is None or req.state == state:
            return
        req = self.board.set_state(rid, state)
        self._notify_requester(req)
        if state == "입고됨":
            QMessageBox.information(self, "입고됨", f"‘{req.item}’이(가) 입고되었습니다. 재고 표에서 수량을 더해 주세요.")
        self.refresh()

    def _notify_requester(self, req: OrderRequest) -> None:
        if not (req and req.notify):
            return
        to = req.requester_email or email_of(self.project, req.requester)
        if not to:
            return
        from .mail_ui import send_mails
        send_mails(self, [([to], *state_mail(req))], "주문 상태 알림")

    def _send(self, reqs: list[OrderRequest]) -> bool:
        to = reqs[0].owner_email or email_of(self.project, reqs[0].owner)
        if not to:
            QMessageBox.warning(self, "주문", "담당자의 이메일이 없습니다. 메일 → 구성원 관리에서 적어 주세요.")
            return False
        from datetime import datetime

        from .mail_ui import send_mails
        if not send_mails(self, [([to], *request_mail(reqs))], "주문 요청"):
            return False
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        for r in reqs:
            r.mailed = now
            r.owner_email = to
            self.board.save(r)
        return True

    def save(self, req: OrderRequest, send: bool, old_state: str = "") -> None:
        self.board.save(req)
        if send:
            self._send([req])
        elif old_state and old_state != req.state:
            got = self.board.set_state(req.id, req.state)
            self._notify_requester(got)
        self.inspector_panel.set_wanted(False)
        self.refresh()

    def delete(self, req: OrderRequest | None) -> None:
        if req is None:
            return
        if confirm_delete(self, "주문 삭제", f"‘{req.item}’ 주문 기록을 삭제할까요?") != QMessageBox.Yes:
            return
        self.board.remove(req.id)
        self.inspector_panel.set_wanted(False)
        self.refresh()


class OrderCard(QFrame):
    """Dashboard: the orders still open (요청 · 주문함)."""
    open_order = Signal(str)
    open_tab = Signal()
    COLS = ["상태", "품목", "요청자 → 담당자", "요청일"]

    def __init__(self):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*[tokens.space("card-pad")] * 4)
        head = QHBoxLayout()
        t = QLabel("진행 중인 주문")
        t.setObjectName("sectionTitle")
        head.addWidget(t)
        head.addStretch(1)
        self.more = QPushButton("주문 탭 열기")
        self.more.setObjectName("plain")
        self.more.clicked.connect(self.open_tab.emit)
        head.addWidget(self.more)
        lay.addLayout(head)
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setFocusPolicy(Qt.NoFocus)
        compact_rows(self.table)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.cellClicked.connect(lambda r, _c: self.open_order.emit(self._ids[r]))
        lay.addWidget(self.table)
        self.empty = QLabel("진행 중인 주문이 없습니다.")
        self.empty.setObjectName("muted")
        lay.addWidget(self.empty)
        self._ids: list[str] = []

    def set_board(self, board: OrderRequestBoard | None) -> None:
        self.setVisible(board is not None)
        reqs = [r for r in (board.requests() if board else []) if r.is_open][:8]
        self.more.setText(f"{len(reqs)}건 · 주문 탭 열기")
        self.empty.setVisible(not reqs)
        self.table.setVisible(bool(reqs))
        self.table.setRowCount(len(reqs))
        self._ids = [r.id for r in reqs]
        for i, r in enumerate(reqs):
            vals = [r.state, f"{r.item}" + (f" ×{r.qty}" if r.qty not in ("", "1") else ""),
                    f"{r.requester} → {r.owner or '미정'}", r.day[5:].replace("-", "/")]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setForeground(tokens.text(STATE_TOKEN.get(r.state, "label")))
                self.table.setItem(i, c, it)
        self.table.setFixedHeight(self.table.horizontalHeader().height() + max(1, len(reqs)) * self.table.verticalHeader().defaultSectionSize() + 4)
