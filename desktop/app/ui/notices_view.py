"""공지사항 tab and the dashboard's latest-notices card.

The tab has two screens on the canvas: reading (the list and the chosen notice, content
only, with who has read it at the bottom) and writing (새 공지 / 수정). Opening a notice
marks it read for the person named in 설정 (보내는 사람 이름); each name chip at the bottom
can also be clicked to mark that person read or not.
"""
from __future__ import annotations

import html

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSplitter, QStackedWidget, QTextBrowser, QTextEdit, QVBoxLayout, QWidget,
)

from ..core import settings
from ..core.notices import Notice, NoticeBoard, newest
from .alerts import confirm_delete
from .theme import icons, tokens
from .widgets import FlowLayout

DASHBOARD_COUNT = 3


def _esc(t: str) -> str:
    return html.escape(t or "")


def everyone(names: list[str], notice: Notice | None) -> list[str]:
    """Lab members in their order, then anyone else who has read it."""
    out = list(dict.fromkeys(n for n in names if n))
    if notice is not None:
        out += [r for r in notice.readers if r not in out]
    return out


def read_count(notice: Notice, names: list[str]) -> tuple[int, int]:
    people = everyone(names, notice)
    return sum(1 for n in people if notice.read_by(n)), len(people)


# ---- dashboard -------------------------------------------------------------------------
class _NoticeRow(QFrame):
    clicked = Signal(str)

    def __init__(self, n: Notice, names: list[str]):
        super().__init__()
        self.nid = n.id
        self.setObjectName("noticeRow")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("눌러서 공지 열기")
        h = QHBoxLayout(self)
        pad = tokens.space("space-2")
        h.setContentsMargins(pad, pad, pad, pad)
        h.setSpacing(tokens.space("gap-group"))
        body = n.body.replace("\n", " ")
        body = body[:110] + ("…" if len(body) > 110 else "")
        muted = tokens.css(tokens.text("label-secondary", "region-box"))
        lab = QLabel(f"<b>{'📌 ' if n.pinned else ''}{_esc(n.title)}</b>&nbsp;&nbsp;<span style='color:{muted}'>"
                     f"{_esc(n.date[:10])} · {_esc(n.author)}</span><br>{_esc(body)}")
        lab.setWordWrap(True)
        lab.setTextFormat(Qt.RichText)
        lab.setAttribute(Qt.WA_TransparentForMouseEvents)
        h.addWidget(lab, 1)
        got, total = read_count(n, names)
        seen = QLabel(f"열람 {got}/{total}")
        seen.setObjectName("readCount")
        seen.setFont(tokens.tabular(seen.font()))
        seen.setToolTip(", ".join(n.readers) + " 읽음" if n.readers else "아직 아무도 읽지 않았습니다")
        h.addWidget(seen, 0, Qt.AlignTop)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit(self.nid)
        super().mouseReleaseEvent(e)


class NoticeCard(QFrame):
    """The latest three notices on the common dashboard, each with how many have read it."""
    open_notices = Signal()
    open_notice = Signal(str)

    def __init__(self, parent=None, count: int = DASHBOARD_COUNT):
        super().__init__(parent)
        self.setObjectName("card")
        self.count = count
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*[tokens.space("card-pad")] * 4)
        head = QHBoxLayout()
        t = QLabel("📢 공지사항")
        t.setObjectName("sectionTitle")
        more = QPushButton("모두 보기")
        more.clicked.connect(self.open_notices.emit)
        head.addWidget(t)
        head.addStretch(1)
        head.addWidget(more)
        lay.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(0)
        lay.addLayout(self.body)

    def set_notices(self, notices: list[Notice] | None, names: list[str] | None = None) -> None:
        while self.body.count():
            w = self.body.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        if notices is None:
            self.setVisible(False)
            return
        self.setVisible(True)
        if not notices:
            empty = QLabel("아직 공지가 없습니다. ‘모두 보기’에서 새 공지를 쓸 수 있어요.")
            empty.setObjectName("muted")
            self.body.addWidget(empty)
        for n in newest(notices, self.count):
            row = _NoticeRow(n, names or [])
            row.clicked.connect(self.open_notice.emit)
            self.body.addWidget(row)


# ---- the tab -----------------------------------------------------------------------------
class NoticesPage(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.board: NoticeBoard | None = None
        self.project = None
        self.current: Notice | None = None
        self.editing: Notice | None = None  # None while writing a new one
        self._marking = False
        self._pending: list[tuple[str, str, bool]] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(*[tokens.space("margin")] * 4)  # window margin 20
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.stack.addWidget(self._reading_screen())
        self.stack.addWidget(self._writing_screen())

    # -- screens --
    def _reading_screen(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        title = QLabel("공지사항")
        title.setObjectName("pageTitle")
        self.info = QLabel()
        self.info.setObjectName("muted")
        self.new_btn = icons.set_icon(QPushButton("새 공지"), "square.and.pencil")
        self.new_btn.setObjectName("primary")
        self.new_btn.clicked.connect(self.new_notice)
        reload_btn = icons.set_icon(QPushButton(), "arrow.clockwise", "새로고침")
        reload_btn.clicked.connect(self.reload)
        head.addWidget(title)
        head.addWidget(self.info)
        head.addStretch(1)
        head.addWidget(reload_btn)
        head.addWidget(self.new_btn)
        lay.addLayout(head)

        split = QSplitter(Qt.Horizontal)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._pick)
        split.addWidget(self.list)

        reader = QFrame()
        reader.setObjectName("card")
        rl = QVBoxLayout(reader)
        pad = tokens.space("card-pad")
        rl.setContentsMargins(pad, pad, pad, pad)
        rl.setSpacing(tokens.space("space-2"))
        top = QHBoxLayout()
        self.del_btn = QPushButton()
        self.del_btn.setObjectName("destructive")
        icons.set_icon(self.del_btn, "trash", "공지 삭제…")
        self.del_btn.clicked.connect(self.delete_notice)
        self.edit_btn = icons.set_icon(QPushButton("수정"), "square.and.pencil")
        self.edit_btn.clicked.connect(self.edit_notice)
        top.addWidget(self.del_btn)  # destructive leads, apart from the safe action
        top.addStretch(1)
        top.addWidget(self.edit_btn)
        rl.addLayout(top)
        self.r_title = QLabel()
        self.r_title.setObjectName("noticeHeading")
        self.r_title.setWordWrap(True)
        self.r_meta = QLabel()
        self.r_meta.setObjectName("muted")
        self.r_body = QTextBrowser()
        self.r_body.setObjectName("noticeBody")
        self.r_body.setOpenExternalLinks(True)
        self.r_body.document().setDocumentMargin(0)
        rl.addWidget(self.r_title)
        rl.addWidget(self.r_meta)
        rl.addWidget(self.r_body, 1)
        line = QFrame()
        line.setObjectName("hairline")
        line.setFixedHeight(1)
        rl.addWidget(line)
        self.r_count = QLabel()
        self.r_count.setObjectName("readHeading")
        rl.addWidget(self.r_count)
        self.chips_box = QWidget()
        self.chips = FlowLayout(self.chips_box)
        rl.addWidget(self.chips_box)
        self.r_hint = QLabel()
        self.r_hint.setObjectName("muted")
        self.r_hint.setWordWrap(True)
        rl.addWidget(self.r_hint)
        self.reader = reader
        self.empty = QLabel("공지를 고르면 여기에 내용이 보입니다.")
        self.empty.setObjectName("muted")
        self.empty.setAlignment(Qt.AlignCenter)
        right = QStackedWidget()
        right.addWidget(reader)
        right.addWidget(self.empty)
        self.right = right
        split.addWidget(right)
        split.setSizes([380, 820])
        lay.addWidget(split, 1)
        return page

    def _writing_screen(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        back = icons.set_icon(QPushButton(), "chevron.left", "공지사항으로 돌아가기")
        back.clicked.connect(self.cancel_edit)
        self.w_title = QLabel("새 공지")
        self.w_title.setObjectName("pageTitle")
        cancel = QPushButton("취소")
        cancel.clicked.connect(self.cancel_edit)
        self.save_btn = QPushButton("게시")
        self.save_btn.setObjectName("primary")
        self.save_btn.clicked.connect(self.save_notice)
        head.addWidget(back)
        head.addWidget(self.w_title)
        head.addStretch(1)
        head.addWidget(cancel)
        head.addWidget(self.save_btn)
        lay.addLayout(head)
        lay.addSpacing(tokens.space("space-2"))
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("제목")
        self.title_edit.setObjectName("noticeTitle")
        lay.addWidget(self.title_edit)
        row = QHBoxLayout()
        self.author = QComboBox()
        self.author.setEditable(True)
        self.author.setToolTip("작성자")
        self.pinned = QCheckBox("📌 중요 (맨 위에 고정)")
        self.date_label = QLabel()
        self.date_label.setObjectName("muted")
        row.addWidget(QLabel("작성자"))
        row.addWidget(self.author, 1)
        row.addWidget(self.pinned)
        row.addWidget(self.date_label)
        lay.addLayout(row)
        self.body_edit = QTextEdit()
        self.body_edit.setPlaceholderText("내용")
        self.body_edit.setAcceptRichText(False)
        lay.addWidget(self.body_edit, 1)
        return page

    # -- data --
    def me(self) -> str:
        return settings.load_settings().get("requester_name", "").strip()

    def names(self) -> list[str]:
        return [m.get("name") or m.get("email", "") for m in self.project.members()] if self.project else []

    def set_board(self, board: NoticeBoard | None, project) -> None:
        self.board = board
        self.project = project
        self.author.clear()
        names = self.names()
        me = self.me()
        for n in ([me] if me and me not in names else []) + names:
            self.author.addItem(n)
        if me:
            self.author.setCurrentText(me)
        self.stack.setCurrentIndex(0)
        self._fill(self.current.id if self.current else None)

    def show_notice(self, nid: str) -> None:
        self.stack.setCurrentIndex(0)
        self._fill(nid)

    def _fill(self, select_id: str | None = None) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        notices = self.board.notices if self.board else []
        names = self.names()
        for n in notices:
            got, total = read_count(n, names)
            it = QListWidgetItem(f"{'📌 ' if n.pinned else ''}{n.title}\n{n.date[:16]} · {n.author} · 열람 {got}/{total}")
            it.setData(Qt.UserRole, n.id)
            self.list.addItem(it)
        self.list.blockSignals(False)
        self.new_btn.setEnabled(self.board is not None)
        if self.board is None:
            self.info.setText("Google Drive 폴더를 열면 쓸 수 있습니다.")
            self.current = None
            self.right.setCurrentWidget(self.empty)
            return
        self.info.setText(f"드라이브 ‘공지사항.xlsx’ · {len(notices)}개")
        ids = [n.id for n in notices]
        self.list.blockSignals(True)
        if select_id in ids:
            self.list.setCurrentRow(ids.index(select_id))
        elif notices:
            self.list.setCurrentRow(0)
        self.list.blockSignals(False)
        if not notices:
            self.current = None
            self.empty.setText("아직 공지가 없습니다. ‘새 공지’로 첫 공지를 써 보세요.")
            self.right.setCurrentWidget(self.empty)
        if notices:
            self._pick(self.list.currentRow())

    def _pick(self, row: int) -> None:
        if not self.board or row < 0 or row >= len(self.board.notices):
            return
        n = self.board.notices[row]
        self.current = n
        self.right.setCurrentWidget(self.reader)
        self.r_title.setText(f"{'📌 ' if n.pinned else ''}{n.title}")
        self.r_meta.setText(" · ".join(x for x in (n.date[:16], n.author) if x))
        self.r_body.setPlainText(n.body)
        self._show_readers(n)
        me = self.me()
        if me and not n.read_by(me):
            QTimer.singleShot(0, lambda nid=n.id: self._mark(nid, me, True, quiet=True))

    def _show_readers(self, n: Notice) -> None:
        while self.chips.count():
            w = self.chips.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        people = everyone(self.names(), n)
        got = sum(1 for p in people if n.read_by(p))
        self.r_count.setText(f"열람 {got}/{len(people)}명")
        for name in people:
            read = n.read_by(name)
            chip = QPushButton(("✓ " if read else "") + name)
            chip.setObjectName("readerChip")
            chip.setCheckable(True)
            chip.setChecked(read)
            chip.setAccessibleName(f"{name}: {'읽음' if read else '안 읽음'}")
            chip.setToolTip(f"{name}: {'읽음' if read else '아직 안 읽음'} · 누르면 "
                            f"{'안 읽음으로' if read else '읽음으로'} 바꿉니다")
            chip.clicked.connect(lambda on, name=name, nid=n.id: self._mark(nid, name, on))
            self.chips.addWidget(chip)
        self.chips_box.updateGeometry()
        self.r_hint.setVisible(not people or not self.me())
        self.r_hint.setText("구성원 관리에 사람을 추가하면 여기에서 누가 읽었는지 볼 수 있어요." if not people else
                            "설정에서 보내는 사람 이름을 적어 두면, 공지를 열 때 자동으로 읽음 표시됩니다.")

    def _mark(self, nid: str, name: str, read: bool, quiet: bool = False) -> None:
        if self.board is None:
            return
        if self._marking:  # one save at a time; the chip goes back to what is saved
            self._pending.append((nid, name, read))
            return
        self._marking = True
        try:
            self._mark_now(nid, name, read, quiet)
            while self._pending:
                self._mark_now(*self._pending.pop(0), quiet)
        finally:
            self._marking = False

    def _mark_now(self, nid: str, name: str, read: bool, quiet: bool) -> None:
        try:
            self.board.set_read(nid, name, read)  # local at once; sent with the next sync
        except Exception as exc:  # noqa: BLE001
            if not quiet:
                QMessageBox.warning(self, "공지사항", f"열람 표시를 저장하지 못했습니다.\n{exc}")
            return
        if self.current is not None and self.current.id == nid:
            self._fill(nid)
        self.changed.emit()

    # -- writing --
    def new_notice(self) -> None:
        if self.board is None:
            return
        self.editing = None
        self.w_title.setText("새 공지")
        self.save_btn.setText("게시")
        self.title_edit.clear()
        self.body_edit.clear()
        self.pinned.setChecked(False)
        me = self.me()
        if me:
            self.author.setCurrentText(me)
        self.date_label.setText("")
        self.stack.setCurrentIndex(1)
        self.title_edit.setFocus()

    def edit_notice(self) -> None:
        if self.current is None:
            return
        n = self.current
        self.editing = n
        self.w_title.setText("공지 수정")
        self.save_btn.setText("저장")
        self.title_edit.setText(n.title)
        self.body_edit.setPlainText(n.body)
        self.author.setCurrentText(n.author)
        self.pinned.setChecked(n.pinned)
        self.date_label.setText(n.date)
        self.stack.setCurrentIndex(1)
        self.body_edit.setFocus()

    def cancel_edit(self) -> None:
        self.stack.setCurrentIndex(0)

    def _run(self, fn, message: str, network: bool = False) -> bool:
        from .cloud import run_blocking
        try:
            run_blocking(self, fn, message) if network else fn()
            return True
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "공지사항", f"저장하지 못했습니다.\n{exc}")
            return False

    def save_notice(self) -> None:
        if self.board is None:
            return
        title = self.title_edit.text().strip()
        if not title:
            QMessageBox.information(self, "공지사항", "제목을 적어 주세요.")
            return
        n = Notice(**{**self.editing.__dict__}) if self.editing else Notice()
        n.title, n.body = title, self.body_edit.toPlainText().strip()
        n.author, n.pinned = self.author.currentText().strip(), self.pinned.isChecked()
        if self.editing is None and n.author:
            n.readers = [n.author]  # the writer has read it
        if self._run(lambda: self.board.upsert(n), "공지를 저장하는 중…"):
            self.stack.setCurrentIndex(0)
            self._fill(n.id)
            self.changed.emit()

    def delete_notice(self) -> None:
        if self.board is None or self.current is None:
            return
        if confirm_delete(self, "공지사항", f"‘{self.current.title}’ 공지를 삭제할까요?") != QMessageBox.Yes:
            return
        nid = self.current.id
        if self._run(lambda: self.board.delete(nid), "공지를 삭제하는 중…"):
            self.current = None
            self._fill()
            self.changed.emit()

    def reload(self) -> None:
        if self.board is None:
            return
        keep = self.current.id if self.current else None
        if self._run(self.board.sync, "공지사항을 최신화하는 중…", network=True):
            self._fill(keep)
            self.changed.emit()
