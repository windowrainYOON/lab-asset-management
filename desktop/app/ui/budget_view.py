"""예산 tab: 장부 (the lab-fee account ledger) and 랩비 회차 (lab-fee rounds).

장부: every deposit and payment with the running balance; 거래 추가 in the inspector.
랩비 회차: Current balance + Σ carryover vs. the target → the lab fee (rounded up to 10
won). 새 랩비 회차 fills each person's carryover from the previous round (its Total minus
that person's "<이름> 랩비" deposits since). Every cell can be changed; a changed
carryover is marked 직접 수정 and ↺ restores the automatic value.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDateEdit, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSpinBox, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import budget as B
from ..core.budget import BudgetBook, Entry, FeeLine, FeeRound
from .alerts import confirm_delete
from .inset import InsetGroup, inset_title
from .table_model import NUM_ALIGN, compact_rows
from .theme import icons, tokens
from .widgets import InspectorPanel, Segmented

LEDGER_COLS = ["날짜", "내용", "수입", "지출", "잔액", "메모"]
LINE_COLS = ["이름", "한글", "이월 (수정 가능)", "Lab fee", "Total", "어떻게 채웠나", "메모"]


def _w(n) -> str:
    return "" if n in (None, 0) else f"₩ {n:,}"


def _amount(text: str) -> int | None:
    return B.parse_amount(text)


class EntryInspector(QWidget):
    def __init__(self, page: "BudgetPage"):
        super().__init__()
        self.page = page
        self.index: int | None = None
        self.old: Entry | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(tokens.space("space-2"))
        bar = QHBoxLayout()
        self.heading = QLabel("거래 추가")
        self.heading.setObjectName("sectionTitle")
        bar.addWidget(self.heading, 1)
        close = icons.set_icon(QPushButton(), "xmark", "닫기")
        close.setObjectName("plain")
        close.clicked.connect(lambda: self.page.inspector_panel.set_wanted(False))
        bar.addWidget(close)
        lay.addLayout(bar)
        g = InsetGroup()
        self.day = QDateEdit()
        self.day.setCalendarPopup(True)
        self.day.setDisplayFormat("yyyy.MM.dd")
        self.desc = QLineEdit()
        self.desc.setPlaceholderText("예: 홍길동 랩비, 종이컵 구입")
        self.kind = QComboBox()
        self.kind.addItems(["수입", "지출"])
        self.amount = QLineEdit()
        self.amount.setPlaceholderText("₩")
        self.amount.setAlignment(NUM_ALIGN)
        self.memo = QLineEdit()
        g.add_row("날짜", self.day, expand=True)
        g.add_row("내용", self.desc, expand=True)
        g.add_row("구분", self.kind, expand=True)
        g.add_row("금액", self.amount, expand=True)
        g.add_row("메모", self.memo, expand=True)
        lay.addWidget(g)
        self.after = QLabel()
        self.after.setObjectName("muted")
        lay.addWidget(self.after)
        self.amount.textEdited.connect(lambda _: self._preview())
        self.kind.currentIndexChanged.connect(lambda _: self._preview())
        row = QHBoxLayout()
        self.del_btn = QPushButton("삭제…")
        self.del_btn.setObjectName("destructive")
        icons.set_icon(self.del_btn, "trash")
        self.del_btn.clicked.connect(self._delete)
        row.addWidget(self.del_btn)
        row.addStretch(1)
        save = QPushButton("저장")
        save.setObjectName("primary")
        save.clicked.connect(self._save)
        row.addWidget(save)
        lay.addLayout(row)
        hint = QLabel("저장하면 엑셀 장부 맨 아래(또는 그 줄)에 그대로 들어가고, 잔액은 앞 줄에서 이어 계산합니다.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addStretch(1)

    def fill(self, index: int | None, e: Entry | None) -> None:
        self.index, self.old = index, e
        self.heading.setText("거래 추가" if e is None else "거래 고치기")
        d = (e.day if e else None) or date.today()
        self.day.setDate(QDate(d.year, d.month, d.day))
        self.desc.setText(e.desc if e else "")
        exp = bool(e and e.expense)
        self.kind.setCurrentIndex(1 if exp else 0)
        amt = (e.expense if exp else e.income) if e else None
        self.amount.setText(f"{amt:,}" if amt else "")
        self.memo.setText(e.memo if e else "")
        self.del_btn.setVisible(e is not None)
        self._preview()
        self.desc.setFocus()

    def _preview(self) -> None:
        n = _amount(self.amount.text()) or 0
        bal = self.page.book.balance() if self.page.book else 0
        if self.old is None:
            after = bal + (n if self.kind.currentIndex() == 0 else -n)
            self.after.setText(f"저장 뒤 잔액 ₩ {after:,}")
        else:
            self.after.setText("")

    def entry(self) -> Entry | None:
        n = _amount(self.amount.text())
        if not self.desc.text().strip():
            QMessageBox.warning(self, "장부", "내용을 적어 주세요.")
            return None
        inc, exp = (n, None) if self.kind.currentIndex() == 0 else (None, n)
        return Entry(self.day.date().toPython().strftime("%Y.%m.%d"), self.desc.text().strip(), inc, exp, None,
                     self.memo.text().strip())

    def _save(self) -> None:
        e = self.entry()
        if e is None:
            return
        if self.old is None:
            self.page.book.add_entry(e)
        else:
            self.page.book.update_entry(self.index, e, self.old)
        self.page.inspector_panel.set_wanted(False)
        self.page.refresh()

    def _delete(self) -> None:
        if self.old is None:
            return
        if confirm_delete(self, "거래 삭제", f"‘{self.old.date} {self.old.desc}’ 줄을 장부에서 지울까요?") != QMessageBox.Yes:
            return
        self.page.book.delete_entry(self.index, self.old)
        self.page.inspector_panel.set_wanted(False)
        self.page.refresh()


class BudgetPage(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.book: BudgetBook | None = None
        self.project = None
        self.draft: FeeRound | None = None   # a new round not confirmed yet
        self.round_label = ""
        root = QVBoxLayout(self)
        m = tokens.space("margin")
        root.setContentsMargins(m, tokens.space("space-2"), m, m)
        root.setSpacing(tokens.space("space-2"))
        head = QHBoxLayout()
        t = QLabel("예산")
        t.setObjectName("pageTitle")
        head.addWidget(t)
        self.sub = QLabel()
        self.sub.setObjectName("muted")
        head.addWidget(self.sub, 1)
        self.seg = Segmented([("ledger", "장부"), ("rounds", "랩비 회차")])
        self.seg.set_current("ledger")
        self.seg.changed.connect(self.set_view)
        head.addWidget(self.seg)
        self.round_combo = QComboBox()
        self.round_combo.currentIndexChanged.connect(lambda _: self._round_picked())
        head.addWidget(self.round_combo)
        self.add_btn = icons.set_icon(QPushButton("거래 추가"), "plus")
        self.add_btn.setObjectName("primary")
        self.add_btn.clicked.connect(self.add_entry)
        head.addWidget(self.add_btn)
        self.new_round_btn = icons.set_icon(QPushButton("새 랩비 회차"), "plus")
        self.new_round_btn.setObjectName("primary")
        self.new_round_btn.clicked.connect(self.new_round)
        head.addWidget(self.new_round_btn)
        root.addLayout(head)
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(self._ledger_page())
        self.stack.addWidget(self._rounds_page())

        self.inspector_panel = InspectorPanel(wanted=False)
        il = QVBoxLayout(self.inspector_panel)
        il.setContentsMargins(0, 0, 0, 0)
        self.inspector = EntryInspector(self)
        il.addWidget(self.inspector)
        self.inspector_panel.setMinimumWidth(300)
        self.inspector_panel.setMaximumWidth(360)
        self.set_view("ledger")

    # ---- pages -----------------------------------------------------------------------------
    def _ledger_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        kpis = QHBoxLayout()
        self.k_bal, self.k_in, self.k_out = QLabel(), QLabel(), QLabel()
        for title, lbl in (("현재 잔액", self.k_bal), ("이번 달 수입", self.k_in), ("이번 달 지출", self.k_out)):
            card = QFrame()
            card.setObjectName("card")
            cl = QVBoxLayout(card)
            tl = QLabel(title)
            tl.setObjectName("muted")
            lbl.setObjectName("kpiValue")
            cl.addWidget(tl)
            cl.addWidget(lbl)
            kpis.addWidget(card)
        lay.addLayout(kpis)
        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        self.ledger = QTableWidget(0, len(LEDGER_COLS))
        self.ledger.setHorizontalHeaderLabels(LEDGER_COLS)
        compact_rows(self.ledger, numeric=(2, 3, 4))
        self.ledger.verticalHeader().setVisible(False)
        self.ledger.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.ledger.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.ledger.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.ledger.cellDoubleClicked.connect(lambda r, _c: self._edit_entry(r))
        self.ledger.setContextMenuPolicy(Qt.CustomContextMenu)
        self.ledger.customContextMenuRequested.connect(self._ledger_menu)
        cl.addWidget(self.ledger)
        lay.addWidget(card, 1)
        return w

    def _rounds_page(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        top = QFrame()
        top.setObjectName("card")
        tl = QVBoxLayout(top)
        th = QHBoxLayout()
        self.calc_title = QLabel()
        self.calc_title.setObjectName("sectionTitle")
        th.addWidget(self.calc_title, 1)
        self.round_actions = QHBoxLayout()
        self.reset_btn = icons.set_icon(QPushButton("모두 자동값으로"), "arrow.uturn.backward")
        self.reset_btn.clicked.connect(self._reset_all)
        self.cancel_btn = QPushButton("취소")
        self.cancel_btn.clicked.connect(self._cancel_draft)
        self.confirm_btn = QPushButton("확정 · 시트 만들기")
        self.confirm_btn.setObjectName("primary")
        self.confirm_btn.clicked.connect(self._confirm)
        self.del_round_btn = QPushButton("회차 삭제…")
        self.del_round_btn.setObjectName("destructive")
        self.del_round_btn.clicked.connect(self._delete_round)
        for b in (self.del_round_btn, self.reset_btn, self.cancel_btn, self.confirm_btn):
            th.addWidget(b)
        tl.addLayout(th)
        grid = QGridLayout()
        heads = ["Current balance", "+ Unpaid carryover", "Target amount", "Balance due", "Lab size", "Lab fee (10원 올림)"]
        for i, h in enumerate(heads):
            lab = QLabel(h)
            lab.setObjectName("muted")
            grid.addWidget(lab, 0, i)
        self.c_bal = QLabel()
        self.c_carry = QLabel()
        self.c_target = QSpinBox()
        self.c_target.setRange(0, 100_000_000)
        self.c_target.setSingleStep(10_000)
        self.c_target.setPrefix("₩ ")
        self.c_target.setGroupSeparatorShown(True)
        self.c_target.valueChanged.connect(lambda v: self._set_round(target=v))
        self.c_due = QLabel()
        self.c_size = QSpinBox()
        self.c_size.setRange(1, 200)
        self.c_size.valueChanged.connect(lambda v: self._set_round(size=v))
        self.c_fee = QLabel()
        self.c_fee.setObjectName("kpiValue")
        for i, wdg in enumerate((self.c_bal, self.c_carry, self.c_target, self.c_due, self.c_size, self.c_fee)):
            grid.addWidget(wdg, 1, i)
        tl.addLayout(grid)
        self.calc_note = QLabel()
        self.calc_note.setObjectName("muted")
        self.calc_note.setWordWrap(True)
        tl.addWidget(self.calc_note)
        lay.addWidget(top)
        card = QFrame()
        card.setObjectName("card")
        cl = QVBoxLayout(card)
        lt = QLabel("Unpaid carryover · 칸을 눌러 직접 고칠 수 있습니다")
        lt.setObjectName("sectionTitle")
        cl.addWidget(lt)
        self.lines = QTableWidget(0, len(LINE_COLS))
        self.lines.setHorizontalHeaderLabels(LINE_COLS)
        compact_rows(self.lines, numeric=(2, 3, 4))
        self.lines.verticalHeader().setVisible(False)
        self.lines.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.lines.itemChanged.connect(self._line_edited)
        self.lines.setContextMenuPolicy(Qt.CustomContextMenu)
        self.lines.customContextMenuRequested.connect(self._line_menu)
        cl.addWidget(self.lines)
        self.empty_round = QLabel("아직 랩비 회차가 없습니다. ‘새 랩비 회차’로 만드세요. 이전 회차가 있으면 납부 상태로 이월을 채웁니다.")
        self.empty_round.setObjectName("muted")
        self.empty_round.setWordWrap(True)
        cl.addWidget(self.empty_round)
        lay.addWidget(card, 1)
        return w

    # ---- data --------------------------------------------------------------------------------
    def set_board(self, book: BudgetBook | None, project) -> None:
        self.book, self.project = book, project
        self.draft = None
        self.inspector_panel.set_wanted(False)
        self.refresh()

    def set_view(self, view: str) -> None:
        self.view = view
        self.seg.set_current(view)
        self.stack.setCurrentIndex(0 if view == "ledger" else 1)
        self.add_btn.setVisible(view == "ledger")
        self.new_round_btn.setVisible(view == "rounds")
        self.round_combo.setVisible(view == "rounds")
        if view != "ledger":
            self.inspector_panel.set_wanted(False)
        self.refresh()

    def refresh(self) -> None:
        if self.book is None:
            self.sub.setText("Google Drive 위치 세트를 열면 쓸 수 있습니다")
            self.ledger.setRowCount(0)
            self.lines.setRowCount(0)
            return
        self.sub.setText(f"{B.BUDGET_NAME if not self.book.file_id else '랩비 통장'} · 잔액 ₩ {self.book.balance():,}")
        if self.view == "ledger":
            self._fill_ledger()
        else:
            self._fill_rounds()
        self.changed.emit()

    def _fill_ledger(self) -> None:
        es = self.book.entries()
        self.ledger.setRowCount(len(es))
        month = date.today().strftime("%Y.%m")
        inc = sum(e.income or 0 for e in es if e.date.startswith(month))
        out = sum(e.expense or 0 for e in es if e.date.startswith(month))
        self.k_bal.setText(f"₩ {self.book.balance():,}")
        self.k_in.setText(f"₩ {inc:,}")
        self.k_out.setText(f"₩ {out:,}")
        for r, e in enumerate(es):
            vals = [e.date, e.desc, _w(e.income), _w(e.expense), _w(e.balance) or "₩ 0", e.memo]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c in (2, 3, 4):
                    it.setTextAlignment(NUM_ALIGN)
                if c == 2 and e.income:
                    it.setForeground(tokens.text("green"))
                self.ledger.setItem(r, c, it)
        self.ledger.scrollToBottom()

    # ---- ledger actions --------------------------------------------------------------------
    def add_entry(self) -> None:
        if self.book is None:
            QMessageBox.information(self, "예산", "Google Drive 위치 세트를 연 뒤에 쓸 수 있습니다.")
            return
        self.inspector.fill(None, None)
        self.inspector_panel.set_wanted(True)

    def _edit_entry(self, row: int) -> None:
        es = self.book.entries()
        if 0 <= row < len(es):
            self.inspector.fill(row, es[row])
            self.inspector_panel.set_wanted(True)

    def _ledger_menu(self, pos) -> None:
        idx = self.ledger.indexAt(pos)
        if not idx.isValid():
            return
        menu = QMenu(self)
        menu.addAction("고치기…", lambda: self._edit_entry(idx.row()))
        menu.exec(self.ledger.viewport().mapToGlobal(pos))

    # ---- rounds ------------------------------------------------------------------------------
    def _rounds(self) -> list[FeeRound]:
        rs = self.book.rounds() if self.book else []
        if self.draft is not None:
            rs = [r for r in rs if r.label != self.draft.label] + [self.draft]
        return sorted(rs, key=lambda r: r.label)

    def _round(self) -> FeeRound | None:
        if self.draft is not None and self.round_label == self.draft.label:
            return self.draft
        return next((r for r in self._rounds() if r.label == self.round_label), None)

    def _fill_rounds(self) -> None:
        rs = self._rounds()
        if rs and self.round_label not in [r.label for r in rs]:
            self.round_label = rs[-1].label
        self.round_combo.blockSignals(True)
        self.round_combo.clear()
        for r in rs:
            self.round_combo.addItem(r.label + (" (확정 전)" if self.draft is not None and r is self.draft else ""), r.label)
        self.round_combo.setCurrentIndex(max(0, self.round_combo.findData(self.round_label)))
        self.round_combo.blockSignals(False)
        rnd = self._round()
        self.empty_round.setVisible(rnd is None)
        is_draft = rnd is not None and rnd is self.draft
        for b in (self.cancel_btn, self.confirm_btn):
            b.setVisible(is_draft)
        self.del_round_btn.setVisible(rnd is not None and not is_draft)
        self.reset_btn.setVisible(rnd is not None)
        if rnd is None:
            self.calc_title.setText("랩비 회차")
            self.lines.setRowCount(0)
            for lbl in (self.c_bal, self.c_carry, self.c_due, self.c_fee):
                lbl.setText("-")
            return
        prev = [r for r in self._rounds() if r.label < rnd.label]
        self.calc_title.setText(f"{rnd.label} 계산" + (" · 값을 고치면 바로 다시 계산" if is_draft else ""))
        self.c_bal.setText(f"₩ {rnd.balance:,}")
        self.c_carry.setText(f"₩ {rnd.balance + rnd.carry_total:,}  (이월 {rnd.carry_total:,})")
        for sp, v in ((self.c_target, rnd.target), (self.c_size, rnd.lab_size)):
            sp.blockSignals(True)
            sp.setValue(v)
            sp.blockSignals(False)
        self.c_due.setText(f"₩ {rnd.due:,}")
        self.c_fee.setText(f"₩ {rnd.fee:,}")
        self.calc_note.setText(
            (f"이전 회차 {prev[-1].label}에서 이월을 채웠습니다. " if prev else "이전 회차가 없어 이월은 0에서 시작합니다. ")
            + "이월 = 이전 회차 Total − 그 뒤 장부에 들어온 그 사람의 ‘<이름> 랩비’ 입금 (더 낸 사람은 0). "
            + f"기준일 {rnd.made or '-'}.")
        self.lines.blockSignals(True)
        self.lines.setRowCount(len(rnd.lines))
        for r, ln in enumerate(rnd.lines):
            if ln.manual:
                how = f"직접 수정 · 자동 {ln.auto:,}" + (f" · {ln.memo}" if ln.memo else "")
            elif prev:
                how = f"자동 · {prev[-1].label} Total − 납부"
            else:
                how = "자동"
            vals = [ln.name, ln.korean, f"{ln.carryover:,}", f"{rnd.fee:,}", f"{rnd.total(ln):,}", how, ln.memo]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c in (2, 3, 4):
                    it.setTextAlignment(NUM_ALIGN)
                if c not in (0, 1, 2, 6):
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                if c == 5 and ln.manual:
                    it.setForeground(tokens.text("orange"))
                if c == 2 and ln.manual:
                    it.setBackground(tokens.tint("orange", 0.14))
                self.lines.setItem(r, c, it)
        self.lines.blockSignals(False)

    def _round_picked(self) -> None:
        self.round_label = self.round_combo.currentData() or ""
        self._fill_rounds()

    def _store(self, rnd: FeeRound) -> None:
        """A confirmed round is saved at once; a draft waits for 확정."""
        if rnd is not self.draft:
            self.book.save_round(rnd)
        self._fill_rounds()
        self.changed.emit()

    def _set_round(self, **changes) -> None:
        rnd = self._round()
        if rnd is None:
            return
        for k, v in changes.items():
            setattr(rnd, k, v)
        self._store(rnd)

    def _line_edited(self, it: QTableWidgetItem) -> None:
        rnd = self._round()
        if rnd is None:
            return
        ln = rnd.lines[it.row()]
        c = it.column()
        if c == 0:
            ln.name = it.text().strip()
        elif c == 1:
            ln.korean = it.text().strip()
        elif c == 2:
            v = _amount(it.text()) or 0
            ln.carryover = v
            ln.manual = v != ln.auto
        elif c == 6:
            ln.memo = it.text().strip()
        self._store(rnd)

    def _line_menu(self, pos) -> None:
        rnd = self._round()
        idx = self.lines.indexAt(pos)
        if rnd is None or not idx.isValid():
            return
        ln = rnd.lines[idx.row()]
        menu = QMenu(self)
        a = menu.addAction(f"↺ 자동값으로 되돌리기 ({ln.auto:,})", lambda: self._reset(ln))
        a.setEnabled(ln.manual)
        menu.addAction(f"납부 기록 · 장부에 ‘{ln.korean or ln.name} 랩비’ 입금 추가", lambda: self._paid(rnd, ln))
        menu.addSeparator()
        menu.addAction("이 사람 빼기", lambda: self._remove_line(rnd, ln))
        menu.addAction("사람 추가…", lambda: self._add_line(rnd))
        menu.exec(self.lines.viewport().mapToGlobal(pos))

    def _reset(self, ln: FeeLine) -> None:
        ln.carryover, ln.manual = ln.auto, False
        self._store(self._round())

    def _reset_all(self) -> None:
        rnd = self._round()
        if rnd is None:
            return
        for ln in rnd.lines:
            ln.carryover, ln.manual = ln.auto, False
        self._store(rnd)

    def _paid(self, rnd: FeeRound, ln: FeeLine) -> None:
        amount, ok = QInputDialog.getInt(self, "납부 기록", f"{ln.korean or ln.name}님이 낸 금액", rnd.total(ln), 0, 10_000_000, 10)
        if not ok or not amount:
            return
        self.book.add_entry(Entry(date.today().strftime("%Y.%m.%d"), f"{ln.korean or ln.name} 랩비", amount, None, None, ""))
        self.refresh()

    def _remove_line(self, rnd: FeeRound, ln: FeeLine) -> None:
        rnd.lines.remove(ln)
        rnd.size = len(rnd.lines)
        self._store(rnd)

    def _add_line(self, rnd: FeeRound) -> None:
        name, ok = QInputDialog.getText(self, "사람 추가", "이름 (영문 또는 한글)")
        if ok and name.strip():
            rnd.lines.append(FeeLine(name.strip(), name.strip() if not name.isascii() else ""))
            rnd.size = len(rnd.lines)
            self._store(rnd)

    def people(self) -> list[tuple[str, str]]:
        out = []
        for m in self.project.members() if self.project else []:
            ko = m.get("name", "")
            out.append((m.get("english") or ko, ko))
        return out

    def new_round(self) -> None:
        if self.book is None:
            QMessageBox.information(self, "예산", "Google Drive 위치 세트를 연 뒤에 쓸 수 있습니다.")
            return
        label, ok = QInputDialog.getText(self, "새 랩비 회차", "회차 이름 (예: 2026.11)", text=date.today().strftime("%Y.%m"))
        label = label.strip()
        if not ok or not label:
            return
        if any(r.label == label for r in self.book.rounds()):
            QMessageBox.information(self, "새 랩비 회차", f"‘{label}’ 회차가 이미 있습니다.")
            return
        people = self.people()
        prev = self.book.rounds()
        if not people and prev:
            people = [(ln.name, ln.korean) for ln in prev[-1].lines]
        if not people:
            QMessageBox.information(self, "새 랩비 회차", "메일 → 구성원 관리에서 구성원(영문 이름 포함)을 먼저 적어 주세요.")
            return
        self.draft = B.new_round(self.book.state, label, people)
        self.round_label = label
        self._fill_rounds()

    def _cancel_draft(self) -> None:
        self.draft = None
        self.round_label = ""
        self._fill_rounds()

    def _confirm(self) -> None:
        if self.draft is None:
            return
        self.book.save_round(self.draft)
        self.round_label = self.draft.label
        self.draft = None
        self.refresh()

    def _delete_round(self) -> None:
        rnd = self._round()
        if rnd is None:
            return
        if confirm_delete(self, "회차 삭제", f"‘{rnd.label}’ 랩비 회차 시트를 지울까요?") != QMessageBox.Yes:
            return
        self.book.delete_round(rnd.label)
        self.round_label = ""
        self.refresh()
