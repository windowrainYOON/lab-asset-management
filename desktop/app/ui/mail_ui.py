"""Mail features: people in charge (담당자), the scheduled briefing and the order cart."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, QTime
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTimeEdit,
    QVBoxLayout,
)

from ..core import settings
from .table_model import NUM_ALIGN, compact_rows
from .theme import icons, tokens
from ..core.project import Project
from ..core.services.mail_reports import ItemLine, briefing, item_line, order_requests, owners

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


def ensure_mail_permission(parent) -> bool:
    """Make sure the signed-in Google account may send mail; sign in again if not."""
    from ..integrations import gmail
    from ..integrations.gdrive import drive
    from .cloud import run_blocking

    if gmail.can_send():
        return True
    if QMessageBox.question(
            parent, "메일 보내기 권한",
            "메일을 보내려면 Google 계정으로 한 번 더 로그인해 ‘Gmail로 메일 보내기’ 권한을 허용해야 합니다.\n"
            "메일은 로그인한 계정 이름으로 보내집니다. 지금 로그인할까요?") != QMessageBox.Yes:
        return False
    try:
        run_blocking(parent, drive.sign_in, "브라우저에서 로그인하고 메일 보내기 권한을 허용해 주세요…")
    except Exception as exc:  # noqa: BLE001
        QMessageBox.warning(parent, "Google 로그인", f"로그인하지 못했습니다.\n{exc}")
        return False
    if not gmail.can_send():
        QMessageBox.warning(parent, "메일 보내기 권한", "메일 보내기 권한이 허용되지 않았습니다. 로그인 화면에서 체크해 주세요.")
        return False
    return True


def send_mails(parent, mails: list[tuple[list[str], str, str, str]], what: str) -> bool:
    """mails: (to, subject, html, text). Shows the result. True if all were sent."""
    from ..integrations import gmail
    from .cloud import run_blocking

    if not mails or not ensure_mail_permission(parent):
        return False

    def work():
        for to, subject, html, text in mails:
            gmail.send(to, subject, html, text)

    try:
        run_blocking(parent, work, f"{what} 메일을 보내는 중…")
    except Exception as exc:  # noqa: BLE001
        QMessageBox.warning(parent, what, f"메일을 보내지 못했습니다.\n{exc}")
        return False
    to = sorted({a for m in mails for a in m[0]})
    QMessageBox.information(parent, what, f"메일을 보냈습니다.\n받는 사람: {', '.join(to)}")
    return True


# ---- members and people in charge ---------------------------------------------
class MembersDialog(QDialog):
    """Lab members with their email. Owners, order contacts and event alerts pick from this list."""

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("구성원 관리")
        self.resize(560, 460)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("구성원을 추가하고 이메일을 적어 두면 담당자 지정, 주문 요청, 일정 알림에서 고를 수 있습니다."))
        members = project.members()
        self.table = QTableWidget(len(members), 3)
        self.table.setHorizontalHeaderLabels(["이름", "이메일", "영문 이름 (랩비 표)"])
        compact_rows(self.table)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 160)
        for r, m in enumerate(members):
            self.table.setItem(r, 0, QTableWidgetItem(m.get("name", "")))
            self.table.setItem(r, 1, QTableWidgetItem(m.get("email", "")))
            self.table.setItem(r, 2, QTableWidgetItem(m.get("english", "")))
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        add = icons.set_icon(QPushButton("구성원 추가"), "plus")
        add.clicked.connect(self._add)
        rm = QPushButton("선택 구성원 빼기")  # nothing is saved until 저장, so no alert
        rm.setObjectName("destructive")
        icons.set_icon(rm, "trash")
        rm.clicked.connect(self._remove)
        # Delete never takes the trailing slot and stays apart from the safe action
        row.addWidget(rm)
        row.addStretch(1)
        row.addWidget(add)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        if not members:
            self._add()

    def _add(self) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(""))
        self.table.setItem(r, 1, QTableWidgetItem(""))
        self.table.setItem(r, 2, QTableWidgetItem(""))
        self.table.setCurrentCell(r, 0)
        self.table.editItem(self.table.item(r, 0))

    def _remove(self) -> None:
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)

    def _save(self) -> None:
        out = []
        for r in range(self.table.rowCount()):
            name = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            email = (self.table.item(r, 1).text() if self.table.item(r, 1) else "").strip()
            if not name and not email:
                continue
            if not name:
                QMessageBox.warning(self, "구성원 관리", f"{r + 1}번째 줄에 이름을 적어 주세요.")
                return
            if email and not EMAIL_RE.match(email):
                QMessageBox.warning(self, "구성원 관리", f"{name}님의 이메일 주소가 올바르지 않습니다: {email}")
                return
            eng = (self.table.item(r, 2).text() if self.table.item(r, 2) else "").strip()
            out.append({"name": name, "email": email, "english": eng})
        self.project.set_members(out)
        self.accept()


def member_combo(project: Project, current: dict | None, none_label: str = "(없음)") -> QComboBox:
    """A picker of lab members; keeps an older owner who is not (or no longer) a member."""
    combo = QComboBox()
    combo.addItem(none_label, {})
    for m in project.members():
        combo.addItem(f"{m['name']} <{m['email']}>" if m.get("email") else m["name"], m)
    if current and (current.get("name") or current.get("email")):
        for i in range(combo.count()):
            d = combo.itemData(i) or {}
            if d and d.get("email", "").lower() == current.get("email", "").lower() and d.get("name") == current.get("name"):
                combo.setCurrentIndex(i)
                break
        else:
            combo.addItem(f"{current.get('name', '')} <{current.get('email', '')}> (구성원 아님)", current)
            combo.setCurrentIndex(combo.count() - 1)
    return combo


class OwnersDialog(QDialog):
    """담당자 for every category (= every Excel file) and the order folder, picked from the members."""

    def __init__(self, project: Project, parent=None, focus_category: int | None = None):
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("담당자 지정")
        self.resize(660, 520)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("엑셀 파일(카테고리)마다 구성원 중에서 담당자를 고릅니다. 주문 요청 메일과 운영 브리핑의 ‘처리할 주문’이 담당자에게 갑니다.\n"
                             "‘주문 폴더’ 담당자는 담당자가 없는 품목의 주문 요청을 받습니다."))
        self.cats = project.categories()
        self.table = QTableWidget(len(self.cats) + 1, 2)
        self.table.setHorizontalHeaderLabels(["카테고리 (파일)", "담당자"])
        compact_rows(self.table)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self._fill(focus_category)
        lay.addWidget(self.table, 1)
        row = QHBoxLayout()
        members_btn = icons.set_icon(QPushButton("구성원 관리…"), "person.crop.circle")
        members_btn.clicked.connect(self._members)
        fill = QPushButton("선택한 줄의 담당자를 모두에 적용")
        fill.clicked.connect(self._fill_all)
        row.addWidget(members_btn)
        row.addWidget(fill)
        row.addStretch(1)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _fill(self, focus: int | None = None, keep: list | None = None) -> None:
        self.combos: list[QComboBox] = []
        rows = [("📦 주문 폴더 (담당자 없는 품목)", self.project.order_contact())] + \
               [(c.name, self.project.owner(c.id)) for c in self.cats]
        for r, (label, cur) in enumerate(rows):
            it = QTableWidgetItem(label)
            it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.table.setItem(r, 0, it)
            combo = member_combo(self.project, keep[r] if keep else cur)
            self.table.setCellWidget(r, 1, combo)
            self.combos.append(combo)
            if r > 0 and self.cats[r - 1].id == focus:
                self.table.setCurrentCell(r, 1)

    def _members(self) -> None:
        keep = [c.currentData() for c in self.combos]
        if MembersDialog(self.project, self).exec():
            self._fill(keep=keep)

    def _fill_all(self) -> None:
        r = max(self.table.currentRow(), 0)
        value = self.combos[r].currentData()
        for c in self.combos:
            idx = next((i for i in range(c.count()) if c.itemData(i) == value), 0)
            c.setCurrentIndex(idx)

    def _save(self) -> None:
        contact = self.combos[0].currentData() or {}
        if contact != self.project.order_contact():
            self.project.set_order_contact(contact)
        for cat, combo in zip(self.cats, self.combos[1:]):
            o = combo.currentData() or {}
            if self.project.owner(cat.id) != o:
                self.project.set_owner(cat.id, o.get("name", ""), o.get("email", ""))
        self.accept()


# ---- scheduled briefing ------------------------------------------------------------
class BriefingDialog(QDialog):
    def __init__(self, project: Project, parent=None, drive_mode: bool = True, sources=None):
        super().__init__(parent)
        from ..core.services.ops_briefing import recipients
        from ..integrations import schedule_mac

        self.project = project
        self.sources = sources or (lambda: {})
        self.setWindowTitle("운영 브리핑 메일")
        cfg = project.briefing()
        lay = QVBoxLayout(self)
        who = ", ".join(p["name"] for p in recipients(project))
        info = QLabel("정해진 시간에 사람마다 자기 것만 담은 메일을 보냅니다: 오늘 내 장비 예약, "
                      "이번 주 내 당번·업무, 오늘 내 일정(나에게 지정된 것), 나에게 배정된 주문 요청"
                      "(장바구니로 요청된 품목과 현재 재고). 해당 내용이 없는 칸은 빠지고, "
                      "아무것도 없는 사람에게는 보내지 않습니다.\n"
                      f"받는 사람: {who or '아직 없음 · 멤버와 이메일을 먼저 등록하세요'}")
        info.setWordWrap(True)
        lay.addWidget(info)
        form = QFormLayout()
        self.enabled = QCheckBox("정해진 시간에 자동으로 보내기")
        self.enabled.setChecked(bool(cfg.get("enabled")))
        form.addRow(self.enabled)
        self.time = QTimeEdit(QTime.fromString(cfg.get("time", "09:00"), "HH:mm"))
        self.time.setDisplayFormat("HH:mm")
        form.addRow("보낼 시각", self.time)
        days_row = QHBoxLayout()
        self.days = []
        chosen = cfg.get("weekdays", [0, 1, 2, 3, 4])
        for i, d in enumerate(WEEKDAYS):
            cb = QCheckBox(d)
            cb.setChecked(i in chosen)
            self.days.append(cb)
            days_row.addWidget(cb)
        form.addRow("요일", days_row)
        lay.addLayout(form)
        self.here = QCheckBox("이 컴퓨터에서 보내기 (앱이 꺼져 있어도 이 Mac이 켜져 있으면 발송)")
        self.here.setChecked(schedule_mac.installed())
        self.here.setEnabled(schedule_mac.supported() and drive_mode)
        lay.addWidget(self.here)
        note = QLabel("발송은 한 대의 컴퓨터에서만 켜 두세요. 같은 날 두 번 보내지 않도록 보낸 날짜를 폴더 설정에 기록합니다.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        row = QHBoxLayout()
        preview = QPushButton("미리 보기")
        preview.clicked.connect(self._preview)
        now = QPushButton("지금 보내기")
        now.clicked.connect(self._send_now)
        row.addWidget(preview)
        row.addWidget(now)
        row.addStretch(1)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _mails(self) -> list[tuple[list[str], str, str, str]]:
        from ..core.services.ops_briefing import all_mails
        return all_mails(self.project, **(self.sources() or {}))

    def _preview(self) -> None:
        mails = self._mails()
        if not mails:
            QMessageBox.information(self, "미리 보기", "오늘은 누구에게도 보낼 내용이 없습니다.")
            return
        show_html(self, f"미리 보기 · {mails[0][0][0]}", mails[0][2])

    def _send_now(self) -> None:
        mails = self._mails()
        if not mails:
            QMessageBox.information(self, "운영 브리핑", "오늘은 누구에게도 보낼 내용이 없습니다.")
            return
        send_mails(self, mails, "운영 브리핑")

    def _save(self) -> None:
        from ..integrations import schedule_mac

        days = [i for i, cb in enumerate(self.days) if cb.isChecked()]
        if self.enabled.isChecked() and not days:
            QMessageBox.warning(self, "운영 브리핑", "요일을 하나 이상 고르세요.")
            return
        cfg = dict(self.project.briefing())
        cfg.update({"enabled": self.enabled.isChecked(), "time": self.time.time().toString("HH:mm"),
                    "weekdays": days})
        self.project.set_briefing(cfg)
        if schedule_mac.supported():
            try:
                if self.enabled.isChecked() and self.here.isChecked():
                    if not ensure_mail_permission(self):
                        return
                    schedule_mac.install(cfg["time"], days)
                elif schedule_mac.installed():
                    schedule_mac.uninstall()
            except OSError as exc:
                QMessageBox.warning(self, "운영 브리핑", f"자동 실행을 설정하지 못했습니다.\n{exc}")
                return
        self.accept()


def show_html(parent, title: str, html: str) -> None:
    from PySide6.QtWidgets import QTextBrowser
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.resize(900, 620)
    lay = QVBoxLayout(dlg)
    view = QTextBrowser()
    view.setHtml(html)
    lay.addWidget(view)
    bb = QDialogButtonBox(QDialogButtonBox.Close)
    bb.rejected.connect(dlg.reject)
    lay.addWidget(bb)
    dlg.exec()


# ---- cart -----------------------------------------------------------------------------
class Cart:
    """Items picked for an order request. Lines are snapshots, so they survive a folder reload."""

    def __init__(self):
        self.lines: list[ItemLine] = []

    def __len__(self) -> int:
        return len(self.lines)

    def add(self, project: Project, item_ids: list[int]) -> int:
        added = 0
        have = {(ln.category, ln.name, ln.catalog) for ln in self.lines}
        cache: dict = {}
        for iid in item_ids:
            item = project.item(iid)
            if item is None:
                continue
            ln = item_line(project, project.category(item.category_id), item, cache)
            if (ln.category, ln.name, ln.catalog) in have:
                continue
            ln.request_qty = "1"
            self.lines.append(ln)
            have.add((ln.category, ln.name, ln.catalog))
            added += 1
        return added

    def refresh_owners(self, project: Project) -> None:
        """Owner of each line's category; the order folder's contact when it has none."""
        by_name = {c.name: project.owner(c.id) for c in project.categories()}
        fallback = project.order_contact()
        for ln in self.lines:
            o = by_name.get(ln.category) or fallback
            ln.owner_name, ln.owner_email = o.get("name", ""), o.get("email", "")


class CartDialog(QDialog):
    COLS = ["이름", "제조사", "Cat#", "요청 수량", "마지막 주문일", "카테고리", "담당자"]

    def __init__(self, project: Project, cart: Cart, parent=None, on_sent=None):
        super().__init__(parent)
        self.project = project
        self.cart = cart
        self.on_sent = on_sent  # (lines, requester, note): recorded in 주문 요청
        self.cart.refresh_owners(project)
        self.setWindowTitle(f"장바구니 · {len(cart)}개")
        self.resize(980, 520)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("요청 수량을 정하고 ‘주문 요청 보내기’를 누르면 품목 표가 카테고리 담당자에게 메일로 갑니다."))
        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(self.COLS)
        compact_rows(self.table, numeric=(3,))  # 요청 수량
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)
        self._fill()
        row = QHBoxLayout()
        rm = icons.set_icon(QPushButton("선택 빼기"), "xmark")
        rm.clicked.connect(self._remove)
        clear = QPushButton("모두 비우기")
        clear.clicked.connect(self._clear)
        owner_btn = QPushButton("담당자 지정…")
        owner_btn.clicked.connect(self._owners)
        row.addWidget(rm)
        row.addWidget(clear)
        row.addWidget(owner_btn)
        row.addStretch(1)
        lay.addLayout(row)
        form = QFormLayout()
        self.requester = QLineEdit(settings.load_settings().get("requester_name", ""))
        self.requester.setPlaceholderText("예: 홍길동")
        form.addRow("요청자", self.requester)
        self.note = QLineEdit()
        self.note.setPlaceholderText("선택: 급한 이유, 배송 위치 등")
        form.addRow("메모", self.note)
        lay.addLayout(form)
        bb = QDialogButtonBox()
        self.send_btn = bb.addButton("주문 요청 보내기", QDialogButtonBox.AcceptRole)
        self.send_btn.setObjectName("primary")
        bb.addButton("닫기", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._send)
        bb.rejected.connect(self._close)
        lay.addWidget(bb)

    def _fill(self) -> None:
        self.table.setRowCount(len(self.cart.lines))
        for r, ln in enumerate(self.cart.lines):
            owner = f"{ln.owner_name} <{ln.owner_email}>" if ln.owner_email else "담당자 없음"
            values = [ln.name, ln.company, ln.catalog, ln.request_qty, ln.last_order or "기록 없음", ln.category, owner]
            for c, v in enumerate(values):
                it = QTableWidgetItem(v)
                if c != 3:
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                else:
                    it.setToolTip("요청할 수량 (예: 2, 3 box)")
                    it.setTextAlignment(NUM_ALIGN)
                if c == 6 and not ln.owner_email:  # warning: symbol plus the words, not color alone
                    it.setIcon(icons.icon("exclamationmark.triangle", tokens.text("orange", large=True)))
                self.table.setItem(r, c, it)
        self.setWindowTitle(f"장바구니 · {len(self.cart)}개")

    def _sync_qty(self) -> None:
        for r, ln in enumerate(self.cart.lines):
            ln.request_qty = self.table.item(r, 3).text().strip()

    def _remove(self) -> None:
        self._sync_qty()
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            del self.cart.lines[r]
        self._fill()

    def _clear(self) -> None:
        self.cart.lines.clear()
        self._fill()

    def _owners(self) -> None:
        self._sync_qty()
        if OwnersDialog(self.project, self).exec():
            self.cart.refresh_owners(self.project)
            self._fill()

    def _close(self) -> None:
        self._sync_qty()
        self.reject()

    def _send(self) -> None:
        self._sync_qty()
        if not self.cart.lines:
            return
        missing = sorted({ln.category for ln in self.cart.lines if not ln.owner_email})
        if missing:
            QMessageBox.warning(self, "주문 요청", "담당자가 없는 카테고리가 있습니다: " + ", ".join(missing)
                                + "\n‘담당자 지정…’에서 먼저 정해 주세요.")
            return
        bad = [ln.name for ln in self.cart.lines if not ln.request_qty]
        if bad:
            QMessageBox.warning(self, "주문 요청", "요청 수량을 적어 주세요: " + ", ".join(bad[:5]))
            return
        requester = self.requester.text().strip()
        if not requester:
            QMessageBox.warning(self, "주문 요청", "요청자 이름을 적어 주세요.")
            return
        data = settings.load_settings()
        data["requester_name"] = requester
        settings.save_settings(data)
        reqs = order_requests(self.cart.lines, requester, self.note.text().strip())
        mails = [([email], *body) for email, body in reqs.items()]
        if send_mails(self, mails, "주문 요청"):
            if self.on_sent is not None:
                try:
                    self.on_sent(list(self.cart.lines), requester, self.note.text().strip())
                except Exception as exc:  # noqa: BLE001  the mail went out; only the record failed
                    QMessageBox.warning(self, "주문 요청", f"메일은 보냈지만 주문 탭에 기록하지 못했습니다.\n{exc}")
            self.cart.lines.clear()
            self.accept()
