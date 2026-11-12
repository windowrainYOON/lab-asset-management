"""Google Drive access: sign-in and the few file operations a cloud project needs.

Sign-in uses an OAuth "Desktop app" client that the user creates once in
Google Cloud Console (client JSON chosen in the app, kept in the user data
folder, never in the repository). The token is saved next to it with
owner-only permissions.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path

from ...core.settings import source_root, user_data_dir

# Full Drive scope: needed to open a folder the user pastes as a link and edit
# files that already exist there (drive.file only covers files the app created).
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.send"  # inventory briefings and order requests
CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"  # the 일정 tab
SCOPES = ["https://www.googleapis.com/auth/drive", GMAIL_SCOPE, CALENDAR_SCOPE]
FOLDER_MIME = "application/vnd.google-apps.folder"
SPREADSHEET_EXTS = (".xlsx", ".xlsm", ".xls", ".csv", ".tsv")
GSHEET_MIME = "application/vnd.google-apps.spreadsheet"  # a native Google Sheet
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_FOLDER_RE = re.compile(r"/folders/([A-Za-z0-9_-]{10,})")
_ID_PARAM_RE = re.compile(r"[?&]id=([A-Za-z0-9_-]{10,})")
_BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{10,}$")


class DriveError(Exception):
    """A Drive call failed; the message is safe to show to the user."""


class NotSignedIn(DriveError):
    pass


def parse_folder_id(link: str) -> str:
    """Folder id from a Drive folder link (or a bare id)."""
    text = (link or "").strip()
    for rx in (_FOLDER_RE, _ID_PARAM_RE):
        m = rx.search(text)
        if m:
            return m.group(1)
    if _BARE_ID_RE.match(text):
        return text
    raise DriveError("Google Drive 폴더 링크를 인식하지 못했습니다. "
                     "https://drive.google.com/drive/folders/… 형식의 링크를 붙여넣어 주세요.")


def folder_url(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}"


@dataclass
class RemoteFile:
    id: str
    name: str
    mime_type: str = ""
    version: str = ""
    modified_time: str = ""
    size: int = 0
    folder: str = ""  # subfolder name inside the data folder ("" = the folder itself)
    parent_id: str = ""  # id of the folder holding it, when the lister knows it

    @classmethod
    def from_api(cls, d: dict) -> "RemoteFile":
        # Drive's "version" also moves on server-side metadata updates (indexing,
        # thumbnails, sharing), which looked like someone else's edit. The head
        # revision / checksum only change when the content does.
        version = d.get("headRevisionId") or d.get("md5Checksum") or d.get("version", "")
        return cls(d["id"], d.get("name", ""), d.get("mimeType", ""), str(version),
                   d.get("modifiedTime", ""), int(d.get("size", 0) or 0))


# ---- credentials -----------------------------------------------------------
def _user_client_path() -> Path:
    return user_data_dir() / "google_client.json"


def bundled_client_path() -> Path:
    """Client JSON shipped inside the app at build time (secrets/ is gitignored),
    so lab members only sign in with Google and paste the folder link."""
    return source_root() / "secrets" / "google_client.json"


def has_bundled_client() -> bool:
    return bundled_client_path().exists()


def _client_path() -> Path:
    user = _user_client_path()
    return user if user.exists() or not has_bundled_client() else bundled_client_path()


def _token_path() -> Path:
    return user_data_dir() / "google_token.json"


def has_client_config() -> bool:
    return _client_path().exists()


def install_client_config(src: str | Path) -> None:
    """Copy the OAuth client JSON downloaded from Google Cloud Console."""
    data = json.loads(Path(src).read_text(encoding="utf-8"))
    if "installed" not in data:
        raise DriveError("‘데스크톱 앱’ 유형의 OAuth 클라이언트 JSON이 아닙니다. "
                         "Google Cloud Console에서 애플리케이션 유형을 ‘데스크톱 앱’으로 만들어 주세요.")
    dest = _user_client_path()
    shutil.copyfile(src, dest)
    os.chmod(dest, 0o600)


def load_credentials():
    """Saved credentials, refreshed if needed, or None when sign-in is required."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = _token_path()
    if not path.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(path))
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            return None
        _save_token(creds)
        return creds
    return None


def sign_in():
    """Open the browser for Google sign-in (blocking) and save the token."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not has_client_config():
        raise DriveError("먼저 OAuth 클라이언트 JSON 파일을 등록해야 합니다.")
    flow = InstalledAppFlow.from_client_secrets_file(str(_client_path()), SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True,
                                  success_message="Asset Manager 로그인이 완료되었습니다. 이 창을 닫아도 됩니다.")
    _save_token(creds)
    return creds


def sign_out() -> None:
    if _token_path().exists():
        _token_path().unlink()


def _save_token(creds) -> None:
    path = _token_path()
    path.write_text(creds.to_json(), encoding="utf-8")
    os.chmod(path, 0o600)


# ---- client ----------------------------------------------------------------
class DriveClient:
    FIELDS = "id, name, mimeType, version, headRevisionId, md5Checksum, modifiedTime, size, trashed"

    def __init__(self, creds=None):
        creds = creds or load_credentials()
        if creds is None:
            raise NotSignedIn("Google 계정 로그인이 필요합니다.")
        self._creds = creds
        self._local = threading.local()
        self._native: set[str] = set()  # ids of Google Sheets seen so far (read by export)
        self.service  # build once now so sign-in problems show up here

    def _rf(self, d: dict) -> RemoteFile:
        rf = RemoteFile.from_api(d)
        if rf.mime_type == GSHEET_MIME:
            self._native.add(rf.id)
        return rf

    @property
    def sheets(self):
        """Sheets API connection (per thread, like `service`). Covered by the Drive scope,
        so no new sign-in is needed; the API must be enabled in the Cloud project."""
        svc = getattr(self._local, "sheets", None)
        if svc is None:
            from googleapiclient.discovery import build
            svc = build("sheets", "v4", credentials=self._creds, cache_discovery=False)
            self._local.sheets = svc
        return svc

    @property
    def service(self):
        """One Drive connection per thread: the HTTP layer (httplib2) is not thread-safe,
        and background sync runs at the same time as imports/downloads."""
        svc = getattr(self._local, "service", None)
        if svc is None:
            from googleapiclient.discovery import build
            svc = build("drive", "v3", credentials=self._creds, cache_discovery=False)
            self._local.service = svc
        return svc

    def _call(self, request):
        from googleapiclient.errors import HttpError
        try:
            return request.execute(num_retries=3)
        except HttpError as exc:
            status = getattr(exc.resp, "status", 0)
            if status == 404:
                raise DriveError("파일이나 폴더를 찾을 수 없습니다. 링크와 공유 권한을 확인해 주세요.") from exc
            if status == 403 and _sheets_api_off(exc):
                raise DriveError("Google Sheets API가 꺼져 있어 구글 시트에 저장하지 못했습니다. "
                                 "Google Cloud Console의 ‘API 및 서비스 → 라이브러리’에서 "
                                 "Google Sheets API를 사용 설정해 주세요.") from exc
            if status == 403:
                raise DriveError("이 폴더에 접근할 권한이 없습니다. 로그인한 계정에 ‘편집자’ 권한이 있는지 확인해 주세요.") from exc
            raise DriveError(f"Google Drive 오류 ({status}): {exc}") from exc
        except OSError as exc:
            raise DriveError(f"Google Drive에 연결하지 못했습니다: {exc}") from exc

    def get(self, file_id: str) -> RemoteFile:
        d = self._call(self.service.files().get(fileId=file_id, fields=self.FIELDS, supportsAllDrives=True))
        if d.get("trashed"):
            raise DriveError("휴지통에 있는 파일입니다.")
        return self._rf(d)

    def folder(self, folder_id: str) -> RemoteFile:
        f = self.get(folder_id)
        if f.mime_type != FOLDER_MIME:
            raise DriveError("링크가 폴더가 아닙니다. Google Drive 폴더 링크를 넣어 주세요.")
        return f

    def list_folder(self, folder_id: str) -> list[RemoteFile]:
        files: list[RemoteFile] = []
        token = None
        while True:
            resp = self._call(self.service.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields=f"nextPageToken, files({self.FIELDS})", pageSize=200, pageToken=token,
                supportsAllDrives=True, includeItemsFromAllDrives=True, orderBy="name"))
            files += [self._rf(d) for d in resp.get("files", [])]
            token = resp.get("nextPageToken")
            if not token:
                return files

    def find_by_name(self, folder_id: str, name: str) -> RemoteFile | None:
        want = unicodedata.normalize("NFC", name)
        for f in self.list_folder(folder_id):
            if unicodedata.normalize("NFC", f.name) == want:  # Drive may store Hangul as NFD
                return f
        return None

    def download(self, file_id: str, dest: str | Path) -> None:
        from googleapiclient.http import MediaIoBaseDownload

        dest = Path(dest)
        # a name of its own per download: two reads of the same file at once must not share it
        tmp = dest.with_name(f"{dest.name}.{uuid.uuid4().hex[:8]}.part")
        try:
            with tmp.open("wb") as fh:
                if file_id in self._native:  # Google Sheet: no file to fetch, export it as .xlsx
                    req = self.service.files().export_media(fileId=file_id, mimeType=XLSX_MIME)
                else:
                    req = self.service.files().get_media(fileId=file_id, supportsAllDrives=True)
                dl = MediaIoBaseDownload(fh, req)
                done = False
                while not done:
                    _, done = dl.next_chunk(num_retries=3)
        except Exception as exc:  # noqa: BLE001
            tmp.unlink(missing_ok=True)
            raise DriveError(f"다운로드에 실패했습니다: {exc}") from exc
        tmp.replace(dest)

    # ---- Google Sheets (cell-level writes) ---------------------------------------
    def sheet_meta(self, spreadsheet_id: str) -> dict:
        """Sheet title -> SheetMeta (id, grid rows, grid columns), in tab order."""
        from ...core.gsheet_ops import SheetMeta

        d = self._call(self.sheets.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets.properties(sheetId,title,index,sheetType,gridProperties(rowCount,columnCount))"))
        props = sorted((s["properties"] for s in d.get("sheets", [])), key=lambda p: p.get("index", 0))
        out = {}
        for p in props:
            if p.get("sheetType", "GRID") != "GRID":
                continue  # chart sheets have no cells
            g = p.get("gridProperties", {})
            out[p["title"]] = SheetMeta(int(p["sheetId"]), int(g.get("rowCount", 0)), int(g.get("columnCount", 0)))
        return out

    def sheet_batch(self, spreadsheet_id: str, requests: list[dict]) -> None:
        if requests:
            self._call(self.sheets.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id, body={"requests": requests}))

    def upload_new(self, folder_id: str, name: str, src: str | Path, as_sheet: bool = False) -> RemoteFile:
        """Upload a new file. With as_sheet, an .xlsx is converted to a Google Sheet on the
        way in (named without the extension)."""
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(str(src), mimetype=_mime(name if not as_sheet else str(src)), resumable=False)
        body = {"name": name, "parents": [folder_id]}
        if as_sheet:
            body = {"name": sheet_name(name), "parents": [folder_id], "mimeType": GSHEET_MIME}
        d = self._call(self.service.files().create(
            body=body, media_body=media, fields=self.FIELDS, supportsAllDrives=True))
        return self._rf(d)

    def is_native(self, file_id: str) -> bool:
        """A Google Sheet (seen in a listing or get), written cell by cell, never uploaded."""
        return file_id in self._native

    def convert_copy(self, file_id: str, name: str, parent_id: str) -> RemoteFile:
        """A Google Sheet copy of an Excel file (Drive converts it), in parent_id."""
        d = self._call(self.service.files().copy(
            fileId=file_id, body={"name": name, "mimeType": GSHEET_MIME, "parents": [parent_id]},
            fields=self.FIELDS, supportsAllDrives=True))
        return self._rf(d)

    def app_properties(self, file_id: str) -> dict:
        d = self._call(self.service.files().get(fileId=file_id, fields="appProperties", supportsAllDrives=True))
        return d.get("appProperties") or {}

    def set_app_properties(self, file_id: str, props: dict) -> None:
        self._call(self.service.files().update(fileId=file_id, body={"appProperties": props},
                                               fields="id", supportsAllDrives=True))

    def update(self, file_id: str, src: str | Path, name: str = "") -> RemoteFile:
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(str(src), mimetype=_mime(name or str(src)), resumable=False)
        d = self._call(self.service.files().update(
            fileId=file_id, media_body=media, fields=self.FIELDS, supportsAllDrives=True))
        return RemoteFile.from_api(d)

    def rename(self, file_id: str, name: str) -> RemoteFile:
        return RemoteFile.from_api(self._call(self.service.files().update(
            fileId=file_id, body={"name": name}, fields=self.FIELDS, supportsAllDrives=True)))

    def trash(self, file_id: str) -> None:
        self._call(self.service.files().update(fileId=file_id, body={"trashed": True}, supportsAllDrives=True))

    def subfolder(self, parent_id: str, name: str) -> str:
        """Id of a subfolder, created if missing."""
        for f in self.list_folder(parent_id):
            if f.mime_type == FOLDER_MIME and f.name == name:
                return f.id
        d = self._call(self.service.files().create(
            body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}, fields="id",
            supportsAllDrives=True))
        return d["id"]

    def move(self, file_id: str, from_id: str, to_id: str) -> None:
        self._call(self.service.files().update(
            fileId=file_id, addParents=to_id, removeParents=from_id, fields="id", supportsAllDrives=True))


_MIMES = {
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".xls": "application/vnd.ms-excel",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".json": "application/json",
}


def sheet_name(name: str) -> str:
    """'업무.xlsx' -> '업무': a Google Sheet's name has no extension."""
    low = name.lower()
    for ext in (".xlsx", ".xlsm", ".xls"):
        if low.endswith(ext):
            return name[: -len(ext)]
    return name


def _sheets_api_off(exc) -> bool:
    text = str(getattr(exc, "content", b"") or exc)
    return "sheets.googleapis.com" in text and ("SERVICE_DISABLED" in text or "has not been used" in text
                                                  or "is disabled" in text)


def _mime(name: str) -> str:
    return _MIMES.get(Path(name).suffix.lower(), "application/octet-stream")
