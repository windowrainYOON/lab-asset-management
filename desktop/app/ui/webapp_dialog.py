"""설정 · 웹앱: the mobile web app's lab password (see core/webapp_pin.py)."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from ..core import settings as app_settings
from ..core import webapp_pin as wp
from .inset import InsetGroup, inset_title
from .theme import tokens


class WebAppDialog(QDialog):
    """Change the password. The new value is saved to the shared settings file (hash only)."""

    def __init__(self, project, sync=None, parent=None):
        super().__init__(parent)
        self.project = project
        self.sync = sync            # callable that sends the change to Drive now (or None)
        self.setWindowTitle("설정 · 웹앱")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setSpacing(tokens.space("space-3"))
        lay.addWidget(inset_title("모바일 웹앱 비밀번호"))
        g = InsetGroup()
        self.current = QLabel()
        self.current.setObjectName("muted")
        g.add_row("현재 비밀번호", self.current)
        self.new = QLineEdit()
        self.new.setEchoMode(QLineEdit.Password)
        self.new.setPlaceholderText(f"{wp.MIN_LENGTH}자 이상")
        self.again = QLineEdit()
        self.again.setEchoMode(QLineEdit.Password)
        g.add_row("새 비밀번호", self.new)
        g.add_row("새 비밀번호 확인", self.again)
        keep = QWidget()
        kl = QVBoxLayout(keep)
        kl.setContentsMargins(0, 0, 0, 0)
        kl.setSpacing(2)
        self.keep = QCheckBox("이미 들어온 휴대폰은 계속 열어 두기")
        self.keep.setChecked(True)
        hint = QLabel("끄면 모든 휴대폰이 새 비밀번호를 다시 넣어야 해요. 사람이 나갔거나 휴대폰을 잃어버렸을 때 끄세요.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        kl.addWidget(self.keep)
        kl.addWidget(hint)
        g.add_row("", keep)
        row = QWidget()
        rl = QHBoxLayout(row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addStretch(1)
        self.change_btn = QPushButton("비밀번호 바꾸기")
        self.change_btn.setDefault(True)
        self.change_btn.clicked.connect(self._change)
        rl.addWidget(self.change_btn)
        g.add_row("", row)
        lay.addWidget(g)
        note = QLabel("비밀번호는 Drive의 설정 파일(Asset Manager 설정.json)에 원문이 아닌 해시로 저장돼요. "
                      "웹앱은 최대 1분 안에 새 비밀번호를 써요.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addWidget(inset_title("변경 내역"))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["날짜", "바꾼 사람", "Mac", "휴대폰"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionMode(QTableWidget.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setMinimumHeight(150)
        lay.addWidget(self.table)
        bb = QDialogButtonBox(QDialogButtonBox.Close)
        bb.button(QDialogButtonBox.Close).setText("닫기")
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._fill()

    def setting(self) -> dict:
        v = self.project.setting(wp.KEY, {}) if self.project is not None else {}
        return v if isinstance(v, dict) else {}

    def _fill(self) -> None:
        s = self.setting()
        hist = wp.history(s)
        if wp.is_set(s) and hist:
            h = hist[0]
            self.current.setText(f"••••••  ·  {h.get('at', '')} {'처음 설정' if h.get('first') else '변경'} · {h.get('by', '')}")
        else:
            self.current.setText("처음 값 사용 중 (웹앱 서버에 들어 있는 값)")
        self.table.setRowCount(len(hist))
        for r, h in enumerate(hist):
            phones = "—" if h.get("first") and h.get("keep_devices") else ("계속 열림" if h.get("keep_devices") else "모두 다시 입력")
            for c, text in enumerate((h.get("at", ""), h.get("by", ""), h.get("mac", ""), phones)):
                item = QTableWidgetItem(str(text))
                item.setFlags(Qt.ItemIsEnabled)
                self.table.setItem(r, c, item)

    def _change(self) -> None:
        new, again = self.new.text(), self.again.text()
        why = wp.problem(new, again)
        if why:
            QMessageBox.warning(self, "웹앱 비밀번호", why)
            return
        if self.project is None:
            QMessageBox.warning(self, "웹앱 비밀번호", "Google Drive 폴더 프로젝트를 먼저 여세요.")
            return
        keep = self.keep.isChecked()
        if not keep and QMessageBox.question(
                self, "웹앱 비밀번호", "모든 휴대폰이 새 비밀번호를 다시 넣어야 합니다. 바꿀까요?") != QMessageBox.Yes:
            return
        me = (app_settings.load_settings().get("requester_name") or "").strip()
        value = wp.change(self.setting(), new, keep, me)
        self.project.set_setting(wp.KEY, value)
        self.new.clear()
        self.again.clear()
        if self.sync is not None:
            try:
                self.sync()
            except Exception:  # noqa: BLE001  the regular sync sends it within two minutes
                pass
        self._fill()
        QMessageBox.information(self, "웹앱 비밀번호", "바꿨습니다. Drive에 저장되면 웹앱이 1분 안에 새 비밀번호를 씁니다.")
