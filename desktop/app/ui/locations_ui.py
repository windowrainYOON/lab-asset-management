"""Launch picker and data locations (데이터 위치).

StartDialog is shown every time the app opens: it lists the saved location sets (the
last opened one first, marked as the favorite) and shows where each feature's file
is. Nothing is read from Drive until the user presses 열기.

LocationSetDialog edits one set: per feature, the Drive file or folder (picked from
a browser of the set's folder tree or by pasting a link). 이름으로 다시 찾기 scans the
names again after the Drive layout changed.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from ..core import locations, settings
from ..core.locations import DEFAULT_NAMES, FILE, ROLES, LocationSet
from ..integrations.gdrive import drive
from .cloud import SIMPLE_HELP, SETUP_HELP, run_blocking
from .table_model import compact_rows
from .theme import icons, tokens

ROLE_ICONS = {"common": "list.bullet", "personal": "person", "sequence": "document.on.document",
              "order_history": "archivebox", "budget": "wonsign", "work": "checkmark.circle", "notices": "bell", "attendance": "alarm",
              "reservations": "clock", "order_requests": "shippingbox", "overlay": "gearshape", "lab": "folder"}


def _client(parent):
    if drive.load_credentials() is None:
        if not drive.has_client_config():
            QMessageBox.information(parent, "Google Drive", "먼저 OAuth 클라이언트 JSON을 등록하고 로그인해 주세요.")
            return None
        try:
            run_blocking(parent, drive.sign_in, "브라우저에서 Google 계정으로 로그인하고 권한을 허용해 주세요…")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(parent, "Google 로그인", f"로그인하지 못했습니다.\n{exc}")
            return None
    try:
        return run_blocking(parent, drive.DriveClient, "Google Drive에 연결하는 중…")
    except Exception as exc:  # noqa: BLE001
        QMessageBox.warning(parent, "Google Drive", str(exc))
        return None


def fill_location_table(table: QTableWidget, s: LocationSet, with_change: bool = False) -> None:
    roles = list(ROLES)
    table.setRowCount(len(roles))
    for r, role in enumerate(roles):
        label, kind = ROLES[role]
        it = QTableWidgetItem(label)
        it.setIcon(icons.icon(ROLE_ICONS.get(role, "folder"), tokens.text("label-secondary", large=True)))
        it.setData(Qt.UserRole, role)
        table.setItem(r, 0, it)
        table.setItem(r, 1, QTableWidgetItem("파일" if kind == FILE else "폴더"))
        if s.legacy:
            where = s.path(role) or ("폴더 안 " + DEFAULT_NAMES[role] if role in DEFAULT_NAMES else "폴더 그대로")
            state = "찾음" if s.get(role) or role not in DEFAULT_NAMES else "새로 만듦"
        else:
            where = s.path(role) or (f"{s.path('lab') or '/'}{DEFAULT_NAMES[role]}" if role in DEFAULT_NAMES else "—")
            state = "찾음" if s.get(role) else ("새로 만듦" if role in DEFAULT_NAMES else "없음")
        table.setItem(r, 2, QTableWidgetItem(where))
        st = QTableWidgetItem(state)
        st.setForeground(tokens.text({"찾음": "green", "새로 만듦": "orange"}.get(state, "label-secondary")))
        st.setToolTip({"새로 만듦": "아직 없음 · 처음 저장할 때 만듭니다", "없음": "이 기능은 쓰지 않습니다"}.get(state, ""))
        table.setItem(r, 3, st)
        if with_change:
            table.setItem(r, 4, QTableWidgetItem("바꾸기…"))
            table.item(r, 4).setForeground(tokens.text("accent"))
        for c in range(table.columnCount()):
            if table.item(r, c):
                table.item(r, c).setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)


def _location_table(with_change: bool = False) -> QTableWidget:
    cols = ["기능", "종류", "위치", "상태"] + (["" ] if with_change else [])
    t = QTableWidget(0, len(cols))
    t.setHorizontalHeaderLabels(cols)
    compact_rows(t)
    t.verticalHeader().setVisible(False)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    hh = t.horizontalHeader()
    hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
    hh.setSectionResizeMode(1, QHeaderView.ResizeToContents)
    hh.setSectionResizeMode(2, QHeaderView.Stretch)
    hh.setSectionResizeMode(3, QHeaderView.ResizeToContents)
    if with_change:
        hh.setSectionResizeMode(4, QHeaderView.ResizeToContents)
    return t


class StartDialog(QDialog):
    """어느 데이터로 시작할까요? — picked on every launch."""

    def __init__(self, parent=None, error: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Asset Manager 시작")
        self.resize(900, 620)
        self.chosen: LocationSet | None = None
        self.use_local = False
        lay = QVBoxLayout(self)
        m = tokens.space("margin")
        lay.setContentsMargins(m, m, m, m)
        lay.setSpacing(tokens.space("space-3"))
        title = QLabel("어느 데이터로 시작할까요?")
        title.setObjectName("pageTitle")
        lay.addWidget(title)
        sub = QLabel("위치 세트를 고르고 ‘열기’를 누르면 그때 드라이브의 파일을 읽습니다. "
                     "마지막으로 연 세트가 맨 위(즐겨찾기)에 있습니다.")
        sub.setObjectName("muted")
        sub.setWordWrap(True)
        lay.addWidget(sub)
        if error:
            err = QLabel(error)
            err.setObjectName("error")
            err.setWordWrap(True)
            lay.addWidget(err)
        body = QHBoxLayout()
        body.setSpacing(tokens.space("space-3"))
        left = QVBoxLayout()
        lt = QLabel("위치 세트")
        lt.setObjectName("sectionTitle")
        left.addWidget(lt)
        self.list = QListWidget()
        self.list.setMinimumWidth(260)
        self.list.currentItemChanged.connect(lambda *_: self._show())
        self.list.itemDoubleClicked.connect(lambda _: self._open())
        left.addWidget(self.list, 1)
        lb = QHBoxLayout()
        new = icons.set_icon(QPushButton("새 위치 세트…"), "plus")
        new.clicked.connect(self._new)
        lb.addWidget(new)
        lb.addStretch(1)
        left.addLayout(lb)
        body.addLayout(left, 0)
        right = QVBoxLayout()
        self.detail_title = QLabel()
        self.detail_title.setObjectName("sectionTitle")
        right.addWidget(self.detail_title)
        self.detail_sub = QLabel()
        self.detail_sub.setObjectName("muted")
        self.detail_sub.setWordWrap(True)
        right.addWidget(self.detail_sub)
        self.table = _location_table()
        right.addWidget(self.table, 1)
        rb = QHBoxLayout()
        self.edit_btn = icons.set_icon(QPushButton("위치 바꾸기…"), "pencil")
        self.edit_btn.clicked.connect(self._edit)
        self.del_btn = QPushButton("목록에서 빼기")
        self.del_btn.setObjectName("destructive")
        icons.set_icon(self.del_btn, "trash")
        self.del_btn.setToolTip("이 Mac의 목록에서만 뺍니다. 드라이브의 파일은 그대로입니다")
        self.del_btn.clicked.connect(self._remove)
        rb.addWidget(self.del_btn)
        rb.addStretch(1)
        rb.addWidget(self.edit_btn)
        right.addLayout(rb)
        body.addLayout(right, 1)
        lay.addLayout(body, 1)

        acct = QFrame()
        acct.setObjectName("card")
        al = QHBoxLayout(acct)
        self.status = QLabel()
        self.status.setWordWrap(True)
        al.addWidget(self.status, 1)
        self.login_btn = QPushButton("Google 로그인")
        self.login_btn.clicked.connect(self._sign_in)
        al.addWidget(self.login_btn)
        lay.addWidget(acct)

        buttons = QHBoxLayout()
        local = QPushButton("로컬 프로젝트로 시작")
        local.clicked.connect(self._local)
        buttons.addWidget(local)
        buttons.addStretch(1)
        quit_btn = QPushButton("종료")
        quit_btn.clicked.connect(self.reject)
        buttons.addWidget(quit_btn)
        self.open_btn = QPushButton("열기")
        self.open_btn.setObjectName("primary")
        self.open_btn.setDefault(True)
        self.open_btn.clicked.connect(self._open)
        buttons.addWidget(self.open_btn)
        lay.addLayout(buttons)
        self._fill()
        self._refresh_status()

    # ---- the list ----------------------------------------------------------------------
    def _sets(self) -> list[LocationSet]:
        sets = locations.saved_sets()
        have_roots = {s.root_id for s in sets}
        for f in settings.cloud_favorites():  # folders opened before sets existed
            if f.get("folder_id") and f["folder_id"] not in have_roots:
                sets.append(LocationSet.legacy_folder(f["folder_id"], f.get("name", "")))
        last = locations.last_set()
        if last is not None:
            sets = [last] + [s for s in sets if s.id != last.id]
        return sets

    def _fill(self, select: str | None = None) -> None:
        self.list.clear()
        sets = self._sets()
        for i, s in enumerate(sets):
            label = f"{'★ ' if i == 0 else ''}{s.name or s.root_name or s.root_id}"
            it = QListWidgetItem(label)
            it.setToolTip(("마지막으로 연 위치 · " if i == 0 else "") + s.summary())
            it.setData(Qt.UserRole, s)
            self.list.addItem(it)
            if select and s.id == select:
                self.list.setCurrentItem(it)
        if self.list.count() and self.list.currentRow() < 0:
            self.list.setCurrentRow(0)
        self._show()

    def _current(self) -> LocationSet | None:
        it = self.list.currentItem()
        return it.data(Qt.UserRole) if it else None

    def _show(self) -> None:
        s = self._current()
        self.open_btn.setEnabled(s is not None)
        self.edit_btn.setEnabled(s is not None)
        self.del_btn.setEnabled(s is not None)
        if s is None:
            self.detail_title.setText("위치 세트가 없습니다")
            self.detail_sub.setText("‘새 위치 세트…’에서 드라이브 폴더 링크를 넣으면 기능별 파일을 이름으로 찾아 채웁니다.")
            self.table.setRowCount(0)
            return
        self.detail_title.setText(s.name or s.root_name)
        bits = [f"기준 폴더: {s.root_name or s.root_id}", s.summary()]
        if s.created:
            bits.append(f"처음 만든 날 {s.created}" + (f" · {s.created_by}" if s.created_by else ""))
        self.detail_sub.setText(" · ".join(bits))
        fill_location_table(self.table, s)

    # ---- actions --------------------------------------------------------------------------
    def _refresh_status(self) -> None:
        has_client = drive.has_client_config()
        signed_in = has_client and drive.load_credentials() is not None
        if signed_in:
            self.status.setText("✅ Google 계정에 로그인되어 있습니다.")
        elif has_client:
            self.status.setText("🔑 ‘열기’를 누르면 브라우저에서 Google 로그인 창이 한 번 열립니다.")
        else:
            self.status.setText(SETUP_HELP if not drive.has_bundled_client() else SIMPLE_HELP)
            self.status.setTextFormat(Qt.RichText)
        self.login_btn.setText("다른 계정으로 로그인" if signed_in else "Google 로그인")
        self.login_btn.setEnabled(has_client)

    def _sign_in(self) -> None:
        try:
            run_blocking(self, drive.sign_in, "브라우저에서 Google 계정으로 로그인하고 권한을 허용해 주세요…")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Google 로그인", f"로그인하지 못했습니다.\n{exc}")
        self._refresh_status()

    def _new(self) -> None:
        s = new_set_from_link(self)
        if s is not None:
            locations.remember_set(s)
            self._fill(select=s.id)

    def _edit(self) -> None:
        s = self._current()
        if s is None:
            return
        dlg = LocationSetDialog(s, self)
        if dlg.exec():
            locations.remember_set(dlg.result_set)
            self._fill(select=dlg.result_set.id)

    def _remove(self) -> None:
        s = self._current()
        if s is None:
            return
        if QMessageBox.question(self, "목록에서 빼기", f"‘{s.name}’을(를) 이 Mac의 목록에서 뺄까요?\n"
                                "드라이브의 파일은 그대로 남습니다.") != QMessageBox.Yes:
            return
        locations.forget_set(s.id)
        settings.remove_cloud_favorite(s.root_id) if s.legacy else None
        self._fill()

    def _local(self) -> None:
        self.use_local = True
        self.accept()

    def _open(self) -> None:
        s = self._current()
        if s is None:
            return
        if drive.load_credentials() is None:
            if not drive.has_client_config():
                QMessageBox.information(self, "Google Drive", "먼저 OAuth 클라이언트 JSON을 등록하고 로그인해 주세요.")
                return
            self._sign_in()
            if drive.load_credentials() is None:
                return
        self.chosen = s
        self.accept()


def new_set_from_link(parent) -> LocationSet | None:
    """Ask for the top folder's link, find each feature's file by name, let the user check it."""
    from PySide6.QtWidgets import QInputDialog
    text, ok = QInputDialog.getText(parent, "새 위치 세트",
                                    "기준이 되는 Google Drive 폴더 링크를 붙여넣으세요.\n"
                                    "그 아래에서 기능별 엑셀·폴더를 이름으로 찾아 채웁니다.")
    if not ok or not text.strip():
        return None
    try:
        root = drive.parse_folder_id(text)
    except drive.DriveError as exc:
        QMessageBox.warning(parent, "폴더 링크", str(exc))
        return None
    client = _client(parent)
    if client is None:
        return None
    try:
        s = run_blocking(parent, lambda: locations.detect(client, root), "폴더 구조에서 기능별 파일을 찾는 중…")
    except Exception as exc:  # noqa: BLE001
        QMessageBox.warning(parent, "Google Drive", f"폴더를 읽지 못했습니다.\n{exc}")
        return None
    s.created_by = settings.load_settings().get("requester_name", "")
    dlg = LocationSetDialog(s, parent, client=client, fresh=True)
    return dlg.result_set if dlg.exec() else None


class LocationSetDialog(QDialog):
    """데이터 위치: which Excel file (or folder) each feature uses."""

    def __init__(self, s: LocationSet, parent=None, client=None, fresh: bool = False):
        super().__init__(parent)
        import copy
        self.s = copy.deepcopy(s)
        self.result_set: LocationSet | None = None
        self.client = client
        self.setWindowTitle("데이터 위치")
        self.resize(900, 600)
        lay = QVBoxLayout(self)
        m = tokens.space("margin")
        lay.setContentsMargins(m, m, m, m)
        head = QHBoxLayout()
        t = QLabel("데이터 위치")
        t.setObjectName("pageTitle")
        head.addWidget(t)
        sub = QLabel("기능마다 어떤 엑셀(또는 폴더)을 쓸지 정합니다")
        sub.setObjectName("muted")
        head.addWidget(sub, 1)
        rescan = icons.set_icon(QPushButton("이름으로 다시 찾기"), "arrow.clockwise")
        rescan.setToolTip("드라이브의 디렉토리 구조를 바꾼 뒤: 기준 폴더 아래를 다시 훑어 이름으로 채웁니다")
        rescan.clicked.connect(self._rescan)
        head.addWidget(rescan)
        lay.addLayout(head)
        if fresh:
            note = QLabel("폴더 안의 파일 이름으로 기능별 위치를 자동으로 채웠습니다. 확인하고 ‘세트 저장’을 누르세요. "
                          "못 찾은 파일은 직접 고르거나, 처음 저장할 때 ‘새 파일을 만들 폴더’에 새로 만듭니다.")
            note.setWordWrap(True)
            note.setObjectName("muted")
            lay.addWidget(note)
        row = QHBoxLayout()
        row.addWidget(QLabel("세트 이름"))
        self.name = QLineEdit(self.s.name)
        self.name.setPlaceholderText("예: 연구실 공용 · 11월 구조")
        row.addWidget(self.name, 1)
        lay.addLayout(row)
        self.table = _location_table(with_change=True)
        self.table.cellClicked.connect(self._clicked)
        self.table.cellDoubleClicked.connect(lambda r, _c: self._change(r))
        lay.addWidget(self.table, 1)
        hint = QLabel("세트는 이 Mac과 앱 설정 파일(구성원 모두 공유)에 함께 저장되어, 다음 실행 때 시작 창에서 고를 수 있습니다.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        bb = QDialogButtonBox()
        save = bb.addButton("세트 저장", QDialogButtonBox.AcceptRole)
        save.setObjectName("primary")
        bb.addButton("취소", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        fill_location_table(self.table, self.s, with_change=True)

    def _ensure_client(self) -> bool:
        if self.client is None:
            self.client = _client(self)
        return self.client is not None

    def _clicked(self, r: int, c: int) -> None:
        if c == 4:
            self._change(r)

    def _change(self, r: int) -> None:
        role = self.table.item(r, 0).data(Qt.UserRole)
        if not self._ensure_client():
            return
        dlg = DrivePicker(self.client, self.s.root_id, ROLES[role][1] == FILE, ROLES[role][0], self)
        if dlg.exec():
            if self.s.legacy:  # a set gets its own layout once a location is chosen
                self.s.legacy = False
                if not self.s.get("lab"):
                    self.s.set("lab", self.s.root_id, "/")
            self.s.set(role, dlg.picked_id, dlg.picked_path)
            fill_location_table(self.table, self.s, with_change=True)

    def _rescan(self) -> None:
        if not self._ensure_client():
            return
        try:
            found = run_blocking(self, lambda: locations.detect(self.client, self.s.root_id),
                                 "폴더 구조에서 기능별 파일을 다시 찾는 중…")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Google Drive", f"폴더를 읽지 못했습니다.\n{exc}")
            return
        found.id, found.name, found.created, found.created_by = self.s.id, self.s.name, self.s.created, self.s.created_by
        self.s = found
        fill_location_table(self.table, self.s, with_change=True)

    def _save(self) -> None:
        self.s.name = self.name.text().strip() or self.s.root_name or "위치 세트"
        if self.s.legacy and self.s.id.startswith("legacy-") and self.s.locations:
            pass  # still one folder: keep its id
        self.result_set = self.s
        self.accept()


class DrivePicker(QDialog):
    """A folder tree under the set's top folder; pick a file or a folder (or paste a link)."""

    def __init__(self, client, root_id: str, want_file: bool, label: str, parent=None):
        super().__init__(parent)
        self.client = client
        self.want_file = want_file
        self.picked_id = ""
        self.picked_path = ""
        self.setWindowTitle(f"위치 바꾸기 · {label}")
        self.resize(560, 560)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(("파일" if want_file else "폴더") + "을 고릅니다. 링크를 붙여넣어도 됩니다."))
        self.link = QLineEdit()
        self.link.setPlaceholderText("https://drive.google.com/…")
        lay.addWidget(self.link)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemExpanded.connect(self._expand)
        self.tree.itemDoubleClicked.connect(lambda *_: self._ok())
        lay.addWidget(self.tree, 1)
        bb = QDialogButtonBox()
        ok = bb.addButton("이 파일 쓰기" if want_file else "이 폴더 쓰기", QDialogButtonBox.AcceptRole)
        ok.setObjectName("primary")
        bb.addButton("취소", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        try:
            root = run_blocking(self, lambda: client.folder(root_id), "폴더를 읽는 중…")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Google Drive", str(exc))
            return
        top = self._node(None, root.id, root.name, True, "")
        top.setExpanded(True)

    def _node(self, parent, fid, name, is_folder, path) -> QTreeWidgetItem:
        it = QTreeWidgetItem([name])
        it.setIcon(0, icons.icon("folder" if is_folder else "document.on.document",
                                 tokens.text("label-secondary", large=True)))
        it.setData(0, Qt.UserRole, (fid, is_folder, path, False))
        if is_folder:
            it.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)
        if parent is None:
            self.tree.addTopLevelItem(it)
        else:
            parent.addChild(it)
        return it

    def _expand(self, it: QTreeWidgetItem) -> None:
        fid, is_folder, path, loaded = it.data(0, Qt.UserRole)
        if loaded or not is_folder:
            return
        it.setData(0, Qt.UserRole, (fid, is_folder, path, True))
        try:
            entries = run_blocking(self, lambda: self.client.list_folder(fid), "폴더를 읽는 중…")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Google Drive", str(exc))
            return
        for f in entries:
            folder = f.mime_type == drive.FOLDER_MIME
            if not folder and not self.want_file:
                continue
            sub = f"{path}{f.name}/" if folder else f"{path}{f.name}"
            self._node(it, f.id, f.name, folder, sub)

    def _ok(self) -> None:
        text = self.link.text().strip()
        if text:
            import re
            m = re.search(r"/(?:folders|d)/([A-Za-z0-9_\-]{10,})", text) or re.search(r"id=([A-Za-z0-9_\-]{10,})", text)
            fid = m.group(1) if m else text
            try:
                f = run_blocking(self, lambda: self.client.get(fid), "링크를 확인하는 중…")
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, "링크", str(exc))
                return
            is_folder = f.mime_type == drive.FOLDER_MIME
            if is_folder == self.want_file:
                QMessageBox.warning(self, "링크", "파일 링크를 넣어 주세요." if self.want_file else "폴더 링크를 넣어 주세요.")
                return
            self.picked_id, self.picked_path = f.id, f.name + ("/" if is_folder else "")
            self.accept()
            return
        it = self.tree.currentItem()
        if it is None:
            return
        fid, is_folder, path, _ = it.data(0, Qt.UserRole)
        if is_folder == self.want_file:
            QMessageBox.information(self, "위치", "파일을 골라 주세요." if self.want_file else "폴더를 골라 주세요.")
            return
        self.picked_id, self.picked_path = fid, path or "/"
        self.accept()
