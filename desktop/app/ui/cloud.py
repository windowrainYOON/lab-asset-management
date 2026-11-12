"""Google Drive projects in the UI: connect dialog, favorites and background sync."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)

from .theme import icons
from ..core import settings
from ..core.folder_model import FlushResult, FolderModel
from ..core.project import Project
from ..integrations.gdrive import drive

SYNC_INTERVAL_MS = 120_000  # send queued edits / pick up other people's: every 2 minutes
CHANGE_CHECK_MS = 1_000     # status line refresh

SETUP_HELP = (
    "<b>처음 한 번만 필요한 설정</b><br>"
    "1. <a href='https://console.cloud.google.com/apis/library/drive.googleapis.com'>Google Cloud Console</a>에서 "
    "프로젝트를 만들고 <b>Google Drive API</b>를 사용 설정합니다.<br>"
    "2. ‘OAuth 동의 화면’을 만들고(외부, 앱 이름 Asset Manager) 본인 Gmail을 테스트 사용자로 추가합니다.<br>"
    "3. ‘사용자 인증 정보 → OAuth 클라이언트 ID → <b>데스크톱 앱</b>’을 만들고 JSON을 다운로드합니다.<br>"
    "4. 아래 ‘클라이언트 JSON 선택’으로 그 파일을 고른 뒤 ‘Google 로그인’을 누릅니다."
)

SIMPLE_HELP = (
    "<b>Google 계정으로 로그인만 하면 됩니다.</b><br>"
    "폴더 링크를 붙여넣고 ‘연결’을 누르면 브라우저에서 로그인 창이 한 번 열립니다.<br>"
    "폴더는 Google Drive에서 ‘링크가 있는 모든 사용자 · <b>편집자</b>’로 공유되어 있어야 합니다."
)


class _Worker(QThread):
    """Runs one blocking call off the UI thread."""
    done = Signal(object)
    failed = Signal(object)

    def __init__(self, fn: Callable, parent=None):
        super().__init__(parent)
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn())
        except Exception as exc:  # noqa: BLE001 - reported to the user
            self.failed.emit(exc)


def run_blocking(parent, fn: Callable, message: str):
    """Run fn in a thread while keeping the UI responsive; returns its result or raises."""
    from PySide6.QtCore import QEventLoop
    from PySide6.QtWidgets import QProgressDialog

    dlg = QProgressDialog(message, None, 0, 0, parent)
    dlg.setWindowTitle("Google Drive")
    dlg.setWindowModality(Qt.WindowModal)
    dlg.setMinimumDuration(300)
    box: dict = {}
    loop = QEventLoop()
    worker = _Worker(fn)
    worker.done.connect(lambda r: (box.__setitem__("result", r), loop.quit()))
    worker.failed.connect(lambda e: (box.__setitem__("error", e), loop.quit()))
    worker.start()
    loop.exec()
    worker.wait()
    dlg.close()
    if "error" in box:
        raise box["error"]
    return box.get("result")


class CloudConnectDialog(QDialog):
    """Paste a Drive folder link or pick a favorite. Also handles the one-time Google setup."""

    def __init__(self, parent=None, first_run: bool = False, error: str = ""):
        super().__init__(parent)
        self.folder_id: str | None = None
        self.use_local = False
        self.setWindowTitle("Google Drive 프로젝트 연결")
        from .theme import tokens
        self.setMinimumWidth(tokens.space("sheet-width"))
        lay = QVBoxLayout(self)
        title = QLabel("Google Drive 폴더를 프로젝트로 사용합니다")
        title.setObjectName("pageTitle")
        lay.addWidget(title)
        intro = QLabel("폴더 링크를 한 번 입력하면 즐겨찾기에 저장되어 다음부터 바로 열립니다. "
                       "폴더 안의 엑셀 파일이 그대로 카테고리로 보이고, 앱에서 고친 내용은 그 파일에 바로 저장됩니다.")
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        lay.addWidget(intro)
        if error:
            err = QLabel(error)
            err.setWordWrap(True)
            err.setObjectName("error")
            lay.addWidget(err)

        lay.addWidget(QLabel("폴더 링크"))
        self.link = QLineEdit()
        self.link.setPlaceholderText("https://drive.google.com/drive/folders/…")
        lay.addWidget(self.link)

        favs = settings.cloud_favorites()
        self.fav_list = QListWidget()
        if favs:
            lay.addWidget(QLabel("즐겨찾는 프로젝트"))
            for f in favs:
                it = QListWidgetItem(f"☁  {f.get('name') or f['folder_id']}")
                it.setData(Qt.UserRole, f["folder_id"])
                it.setToolTip(drive.folder_url(f["folder_id"]))
                self.fav_list.addItem(it)
            self.fav_list.setMaximumHeight(130)
            self.fav_list.itemClicked.connect(lambda it: self.link.setText(drive.folder_url(it.data(Qt.UserRole))))
            self.fav_list.itemDoubleClicked.connect(lambda it: self._connect())
            lay.addWidget(self.fav_list)
            rm = QPushButton("즐겨찾기에서 빼기")  # only the shortcut goes; the Drive folder stays
            rm.setObjectName("destructive")
            icons.set_icon(rm, "trash")
            rm.clicked.connect(self._remove_fav)
            lay.addWidget(rm, 0, Qt.AlignLeft)

        acct = QFrame()
        acct.setObjectName("card")
        al = QVBoxLayout(acct)
        self.help = QLabel(SIMPLE_HELP if drive.has_bundled_client() else SETUP_HELP)
        self.help.setWordWrap(True)
        self.help.setOpenExternalLinks(True)
        self.help.setTextFormat(Qt.RichText)
        al.addWidget(self.help)
        row = QHBoxLayout()
        self.status = QLabel()
        row.addWidget(self.status, 1)
        self.client_btn = QPushButton("클라이언트 JSON 선택…")
        self.client_btn.clicked.connect(self._choose_client)
        self.login_btn = QPushButton("Google 로그인")
        self.login_btn.clicked.connect(self._sign_in)
        row.addWidget(self.client_btn)
        row.addWidget(self.login_btn)
        al.addLayout(row)
        lay.addWidget(acct)
        lay.addStretch(1)

        buttons = QHBoxLayout()
        if first_run:
            local = QPushButton("로컬 프로젝트로 시작")
            local.clicked.connect(self._local)
            buttons.addWidget(local)
        buttons.addStretch(1)
        cancel = QPushButton("취소")
        cancel.clicked.connect(self.reject)
        self.ok = QPushButton("연결")
        self.ok.setObjectName("primary")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self._connect)
        buttons.addWidget(cancel)
        buttons.addWidget(self.ok)
        lay.addLayout(buttons)
        if favs:
            self.fav_list.setCurrentRow(0)
            self.link.setText(drive.folder_url(favs[0]["folder_id"]))
        self._refresh_status()

    def _refresh_status(self) -> None:
        has_client = drive.has_client_config()
        signed_in = has_client and drive.load_credentials() is not None
        if signed_in:
            self.status.setText("✅ Google 계정에 로그인되어 있습니다.")
        elif has_client:
            self.status.setText("🔑 Google 로그인이 필요합니다.")
        else:
            self.status.setText("⚙️ OAuth 클라이언트 JSON을 먼저 등록해 주세요.")
        self.help.setVisible(not signed_in)
        self.client_btn.setVisible(not signed_in and not drive.has_bundled_client())
        self.login_btn.setText("다른 계정으로 로그인" if signed_in else "Google 로그인")
        self.login_btn.setEnabled(has_client)

    def _choose_client(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "OAuth 클라이언트 JSON", str(Path.home() / "Downloads"),
                                              "JSON (*.json)")
        if not path:
            return
        try:
            drive.install_client_config(path)
        except (drive.DriveError, ValueError, OSError) as exc:
            QMessageBox.warning(self, "클라이언트 등록", str(exc))
        self._refresh_status()

    def _sign_in(self) -> None:
        try:
            run_blocking(self, drive.sign_in, "브라우저에서 Google 계정으로 로그인하고 권한을 허용해 주세요…")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "Google 로그인", f"로그인하지 못했습니다.\n{exc}")
        self._refresh_status()

    def _remove_fav(self) -> None:
        it = self.fav_list.currentItem()
        if it:
            settings.remove_cloud_favorite(it.data(Qt.UserRole))
            self.fav_list.takeItem(self.fav_list.row(it))

    def _local(self) -> None:
        self.use_local = True
        self.accept()

    def _connect(self) -> None:
        try:
            self.folder_id = drive.parse_folder_id(self.link.text())
        except drive.DriveError as exc:
            QMessageBox.warning(self, "폴더 링크", str(exc))
            return
        if drive.load_credentials() is None:
            if not drive.has_client_config():
                QMessageBox.information(self, "Google Drive", "먼저 OAuth 클라이언트 JSON을 등록하고 로그인해 주세요.")
                return
            self._sign_in()
            if drive.load_credentials() is None:
                return
        self.accept()


def open_folder_session(parent, folder_id: str, location_set=None) -> tuple["FolderSession", Project] | None:
    """Connect to the folder (or a location set), read every spreadsheet in it and build
    the project. Returns None if it failed (already reported to the user)."""
    from ..core import locations
    try:
        client = run_blocking(parent, drive.DriveClient, "Google Drive에 연결하는 중…")
        session = FolderSession(client, folder_id or location_set.root_id, location_set=location_set)
        project = session.open(parent)
    except drive.DriveError as exc:
        QMessageBox.warning(parent, "Google Drive", str(exc))
        return None
    except Exception as exc:  # noqa: BLE001
        QMessageBox.warning(parent, "Google Drive", f"폴더를 열지 못했습니다.\n{exc}")
        return None
    if session.location_set is not None and not session.location_set.legacy:
        locations.remember_set(session.location_set)
    else:
        settings.remember_cloud(session.folder_id, session.name)
        if session.location_set is not None:
            locations.remember_set(session.location_set)
    return session, project


class FolderSession(QObject):
    """A Drive folder opened as a project. Edits show at once and are kept locally;
    every two minutes (or on 최신화, or before closing) one sync sends the files that
    have edits and downloads only the files someone else changed."""
    status_changed = Signal(str)
    remote_changed = Signal()   # inventory files changed elsewhere: rebuild from the cache
    boards_changed = Signal()   # 공지사항 / 업무 changed elsewhere

    order_remote = None  # tests: stand-in for the order folder

    def __init__(self, client, folder_id: str, parent=None, remote=None, location_set=None):
        super().__init__(parent)
        from ..integrations.gdrive.folder_remote import DriveFolderRemote

        self.folder_id = folder_id
        self.location_set = location_set  # core.locations.LocationSet: where each feature's file is
        if remote is None and location_set is not None and not location_set.legacy and client is not None:
            from ..integrations.gdrive.set_remote import LocationSetRemote
            remote = LocationSetRemote(client, location_set)
        self.remote = remote or DriveFolderRemote(client, folder_id)
        self.model = FolderModel(self.remote, settings.user_data_dir() / "cloud" / folder_id)
        self.name = folder_id
        self.client = client
        self.orders = None          # OrderIndex from the order-records folder, if one is set
        self.order_matches: dict[int, int] = {}
        self.order_error = ""
        self.notices = None         # NoticeBoard for 공지사항.xlsx
        self.work = None            # WorkBoard for 업무.xlsx
        self.reservations = None    # ReservationBoard for 장비 예약.xlsx
        self.order_requests = None  # OrderRequestBoard for 주문 요청.xlsx
        self.budget = None          # BudgetBook for 예산 입출 내역.xlsx
        self.attendance = None      # AttendanceStore for the 출퇴근 기록 sheet (read only)
        self.set_changed = False    # a feature file was created: the set is saved again
        self._worker: _Worker | None = None
        self._paused = 0
        self._rebuild_due = False
        self.last_sync = ""
        self.check_timer = QTimer(self, interval=CHANGE_CHECK_MS)
        self.check_timer.timeout.connect(self._check)
        self.sync_timer = QTimer(self, interval=SYNC_INTERVAL_MS)
        self.sync_timer.timeout.connect(self.sync_background)

    # ---- open / reload -------------------------------------------------------
    def _load(self) -> None:
        s = self.location_set
        if s is not None and not s.legacy and self.client is not None:
            from ..integrations.gdrive.convert import follow_replaced
            if follow_replaced(self.client, s):  # files converted to Google Sheets elsewhere
                self.set_changed = True
        self.name = self.remote.folder_name()
        self.model.load()
        self._load_orders()
        from ..core.notices import NoticeBoard
        self.notices = NoticeBoard(self.remote, self.model.cache_dir, self.model.notice_id)
        try:
            self.notices.load()
        except Exception:  # noqa: BLE001  the folder still opens without notices
            self.notices.notices = []
        from ..core.work import WorkBoard
        self.work = WorkBoard(self.remote, self.model.cache_dir, self.model.work_id)
        try:
            self.work.load()
        except Exception:  # noqa: BLE001  the folder still opens without the work file
            self.work.projects, self.work.tasks = [], []
        self._load_features()

    def _feature_remote(self, role: str):
        """Where a feature file is read from and created in."""
        if self.location_set is not None and not self.location_set.legacy and self.client is not None:
            from ..integrations.gdrive.set_remote import RoleFileRemote
            return RoleFileRemote(self.client, self.location_set, role, self._feature_created)
        return self.remote

    def _feature_id(self, role: str) -> str:
        if self.location_set is not None and self.location_set.get(role):
            return self.location_set.get(role)
        return self.model.feature_ids.get(role, "")

    def _feature_created(self, role: str, file_id: str) -> None:
        self.set_changed = True

    def create_attendance(self, people: list[str]) -> None:
        """Make the 출퇴근 기록 Google Sheet in the lab folder (this month and next, 설정 tab)."""
        from datetime import date
        from ..core import attendance as att

        s = self.location_set
        folder = s.lab_folder() if s is not None and not s.legacy else self.folder_id
        today = date.today()
        nxt = date(today.year + (today.month == 12), today.month % 12 + 1, 1)
        rf = att.create_sheet(self.client, folder, people, [att.month_key(today), att.month_key(nxt)],
                              self.model.cache_dir)
        if s is not None:
            s.set("attendance", rf.id, (s.path("lab") or "").rstrip("/") + "/" + rf.name)
        self.set_changed = True
        self.attendance = att.AttendanceStore(self._feature_remote("attendance"), self.model.cache_dir, rf.id)
        self.attendance.load()

    def _load_features(self) -> None:
        """장비 예약 / 주문 요청 / 예산: each its own file (made on first save if missing)."""
        from ..core.budget import BudgetBook
        from ..core.order_requests import OrderRequestBoard
        from ..core.reservations import ReservationBoard
        cache = self.model.cache_dir
        for attr, cls, role in (("reservations", ReservationBoard, "reservations"),
                                ("order_requests", OrderRequestBoard, "order_requests"),
                                ("budget", BudgetBook, "budget")):
            board = cls(self._feature_remote(role), cache, self._feature_id(role))
            try:
                board.load()
            except Exception:  # noqa: BLE001  the folder still opens without it
                pass
            setattr(self, attr, board)
        from ..core.attendance import AttendanceStore
        self.attendance = AttendanceStore(self._feature_remote("attendance"), cache, self._feature_id("attendance"))
        try:
            self.attendance.load()
        except Exception as exc:  # noqa: BLE001  the folder still opens without it
            self.attendance.error = str(exc)
        self._share_set()

    def _share_set(self) -> None:
        """Keep the location set in the shared settings file too (others can pick it)."""
        s = self.location_set
        if s is None or s.legacy:
            return
        shared = list(self.model.overlay.get("location_sets") or [])
        mine = s.to_dict()
        others = [d for d in shared if d.get("id") != s.id]
        if not any(d == mine for d in shared):
            self.model._set_overlay(("location_sets",), [mine] + others)

    @property
    def order_folder_id(self) -> str:
        if self.location_set is not None and self.location_set.get("order_history"):
            return self.location_set.get("order_history")
        return self.model.overlay.get("orders", {}).get("folder_id", "")

    def _load_orders(self) -> None:
        """Download and read the order-records folder (read only)."""
        from ..core.orders import load_order_folder
        from ..integrations.gdrive.folder_remote import DriveFolderRemote

        self.orders, self.order_error = None, ""
        if not self.order_folder_id:
            return
        try:
            remote = DriveFolderRemote(self.client, self.order_folder_id) if self.client else self.order_remote
            self.orders = load_order_folder(remote, self.model.cache_dir / "orders")
        except Exception as exc:  # noqa: BLE001  inventory still opens without order info
            self.order_error = str(exc)

    def open(self, parent) -> Project:
        run_blocking(parent, self._load, "폴더의 파일을 모두 읽는 중…")
        return self._build()

    def _build(self) -> Project:
        project = self.model.build(self.model.cache_dir / "session.labproj", self.name)
        if self.orders is not None:
            from ..core.orders import apply_orders
            self.order_matches = apply_orders(project, self.orders)
        self._rebuild_due = False
        self.start()
        self.status_changed.emit(self._idle_text())
        return project

    def rebuild(self) -> Project:
        """The inventory again from the files already downloaded (no network)."""
        return self._build()

    def reload(self, parent) -> Project:
        """Read the whole folder again (after sending anything queued)."""
        self.stop()
        if self.dirty():
            run_blocking(parent, self._send, "변경 사항을 저장하는 중…")
        run_blocking(parent, self._load, "폴더의 파일을 다시 읽는 중…")
        return self._build()

    def set_order_folder(self, folder_id: str) -> None:
        self.model._set_overlay(("orders", "folder_id"), folder_id or None)

    def start(self) -> None:
        self.check_timer.start()
        if not self.sync_timer.isActive():
            self.sync_timer.start()

    def stop(self) -> None:
        for t in (self.check_timer, self.sync_timer):
            t.stop()
        if self._worker is not None:
            self._worker.wait()
            self._worker = None

    def pause(self) -> None:
        self._paused += 1

    def resume(self) -> None:
        self._paused = max(0, self._paused - 1)

    def _busy_ui(self) -> bool:
        return QApplication.activeModalWidget() is not None or self._paused > 0

    # ---- what is waiting ---------------------------------------------------------
    def _boards(self) -> list:
        return [b for b in (self.notices, self.work, self.reservations, self.order_requests, self.budget,
                            self.attendance) if b is not None]

    def dirty(self) -> bool:
        return self.model.pending() or any(b.dirty() for b in self._boards())

    def _idle_text(self) -> str:
        return f"최신 상태 · {self.last_sync}" if self.last_sync else "최신 상태"

    def _check(self) -> None:
        if self._rebuild_due and not self._busy_ui() and not self.model.pending():
            self._rebuild_due = False
            self.remote_changed.emit()
            return
        if self._worker is not None:
            return
        if self.dirty():
            secs = max(0, self.sync_timer.remainingTime() // 1000)
            self.status_changed.emit(f"수정 사항을 이 Mac에 저장함 · {secs // 60}:{secs % 60:02d} 뒤 드라이브에 최신화")

    # ---- one sync: send ours, then fetch only what changed (worker thread) ----------
    def _send(self):
        from ..core.folder_model import FlushResult
        res = self.model.flush() if self.model.pending() else FlushResult()
        for b in self._boards():
            if b.dirty():
                b.sync()
        if self.set_changed:
            self.set_changed = False
            self._share_set()
            if self.model.pending():
                self.model.flush()
        return res

    def _sync_job(self):
        res = self.model.flush() if self.model.pending() else FlushResult()
        inventory = self.model.refresh_changed() or res.remote_changed
        for board, fid in ((self.notices, self.model.notice_id), (self.work, self.model.work_id)):
            if board is not None and not board.file_id and fid:
                board.file.file_id = fid  # someone else made the file meanwhile
        boards = False
        for b in self._boards():
            boards = b.sync() or boards
        if self.set_changed:
            self.set_changed = False
            self._share_set()
            from ..core import locations
            if self.location_set is not None:
                locations.remember_set(self.location_set)
        return res, inventory, boards

    def sync_background(self) -> None:
        if self._worker is not None:
            return
        self.status_changed.emit("드라이브와 최신화하는 중…")
        self._worker = _Worker(self._sync_job, self)
        self._worker.done.connect(self._synced)
        self._worker.failed.connect(self._sync_failed)
        self._worker.start()

    def _synced(self, out) -> None:
        self._worker = None
        res, inventory, boards = out
        from datetime import datetime
        self.last_sync = datetime.now().strftime("%H:%M")
        if res.skipped:
            QMessageBox.warning(
                None, "Google Drive",
                "다른 곳에서 파일이 바뀌어 일부 수정을 적용하지 못했습니다. 바뀐 파일을 다시 읽었습니다.\n\n"
                + "\n".join(res.skipped[:10]))
        if boards:
            self.boards_changed.emit()
        if inventory:
            if self._busy_ui() or self.model.pending():
                self._rebuild_due = True  # after the dialog closes / the edit is sent
            else:
                self.remote_changed.emit()
                return
        self.status_changed.emit(self._idle_text())

    def _sync_failed(self, exc) -> None:
        self._worker = None
        self.status_changed.emit(f"최신화 실패 · 2분 뒤 다시 시도 ({exc})")

    def sync_now(self, parent, orders: bool = True) -> None:
        """최신화 button: the same sync, now, and the order records too."""
        if self._worker is not None:
            self._worker.wait()
            self._worker = None
        try:
            def job():
                out = self._sync_job()
                if orders and self.order_folder_id:
                    self._load_orders()
                return out
            res, inventory, boards = run_blocking(parent, job, "드라이브와 최신화하는 중…")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(parent, "최신화", str(exc))
            return
        self.sync_timer.start()  # the next automatic one is 2 minutes from now
        self._synced((res, inventory or (orders and bool(self.order_folder_id)), boards))

    def flush(self, parent) -> bool:
        """Save before closing. Returns False if the user chose to stay."""
        self.stop()
        if not self.dirty():
            return True
        try:
            run_blocking(parent, self._send, "변경 사항을 Google Drive에 저장하는 중…")
            return True
        except Exception as exc:  # noqa: BLE001
            choice = QMessageBox.question(
                parent, "Google Drive 저장 실패",
                f"변경 사항을 저장하지 못했습니다.\n{exc}\n\n그래도 닫을까요? 저장하지 못한 수정은 사라집니다.")
            if choice != QMessageBox.Yes:
                self.start()
                return False
            return True
