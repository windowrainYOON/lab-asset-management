"""A location set seen by FolderModel as if it were one folder.

FolderModel expects: the settings file, 공지사항 and 업무 at the top ("" folder),
inventory spreadsheets at the top or in a subfolder, personal ones in a subfolder
named like "Personal Asset". This remote presents the set that way:

- the role files (overlay / notices / work) under their canonical names, folder "";
- every spreadsheet in the common folder (folder "") and its subfolders (one level);
- every spreadsheet in the personal folder, folder "Personal Asset" (+ "/sub");
- files used by other features (budget, reservations, order requests) are left out.

New files are created where they belong: role files in the lab folder, inventory
files in the common folder, personal ones in the personal folder.
"""
from __future__ import annotations

from pathlib import Path

from ...core.locations import DEFAULT_NAMES, LocationSet, nfc
from .drive import FOLDER_MIME, DriveClient, RemoteFile

PERSONAL = "Personal Asset"
_ROLE_BY_NAME = {nfc(v): k for k, v in DEFAULT_NAMES.items()}
_SHOWN_ROLES = ("overlay", "notices", "work")  # role files FolderModel itself looks for


class LocationSetRemote:
    def __init__(self, client: DriveClient, location_set: LocationSet):
        self.client = client
        self.set = location_set
        self.folder_id = location_set.root_id
        self._names: dict[str, str] = {}
        self._parents: dict[str, str] = {}
        self._subs: dict[tuple[str, str], str] = {}

    def folder_name(self) -> str:
        return self.set.name or self.client.folder(self.set.root_id).name

    def reserved_ids(self) -> set[str]:
        """Files that belong to another feature (never shown as a category)."""
        return {self.set.get(r) for r in ("budget", "reservations", "order_requests", "attendance")
                if self.set.get(r)}

    def _list(self, folder_id: str, folder_label: str, parent_label_sub: bool, skip: set[str]) -> list[RemoteFile]:
        out = []
        entries = self.client.list_folder(folder_id)
        for f in entries:
            if f.mime_type == FOLDER_MIME or f.id in skip:
                continue
            f.folder = folder_label
            self._parents[f.id] = folder_id
            out.append(f)
        for sub in entries:
            if sub.mime_type != FOLDER_MIME or sub.name.startswith("_") or sub.id in skip:
                continue
            self._subs[(folder_id, nfc(sub.name))] = sub.id
            label = f"{folder_label}/{nfc(sub.name)}" if folder_label else nfc(sub.name)
            for f in self.client.list_folder(sub.id):
                if f.mime_type != FOLDER_MIME and f.id not in skip:
                    f.folder = label
                    self._parents[f.id] = sub.id
                    out.append(f)
        return out

    def list_files(self) -> list[RemoteFile]:
        s = self.set
        skip = self.reserved_ids()
        # other features' folders never count as inventory subfolders
        skip |= {s.get(r) for r in ("personal", "sequence", "order_history", "lab") if s.get(r)}
        skip.discard(s.get("common"))
        role_ids = {s.get(r): r for r in _SHOWN_ROLES if s.get(r)}
        files: list[RemoteFile] = []
        for fid, role in role_ids.items():
            try:
                rf = self.client.get(fid)
            except Exception:  # noqa: BLE001  moved or deleted: made again in the lab folder later
                continue
            rf.name, rf.folder = DEFAULT_NAMES[role], ""
            self._parents.setdefault(fid, s.lab_folder())
            files.append(rf)
        skip |= set(role_ids)
        if s.get("common"):
            files += [f for f in self._list(s.get("common"), "", True, skip)
                      if _ROLE_BY_NAME.get(nfc(f.name)) not in _SHOWN_ROLES]
        if s.get("personal") and s.get("personal") != s.get("common"):
            pskip = skip - {s.get("personal")}
            files += self._list(s.get("personal"), PERSONAL, True, pskip)
        self._names.update({f.id: f.name for f in files})
        return files

    # ---- writing --------------------------------------------------------------------
    def _parent_for(self, name: str, folder: str) -> str:
        role = _ROLE_BY_NAME.get(nfc(name))
        if role and not folder:
            return self.set.lab_folder()
        if folder == PERSONAL or folder.startswith(PERSONAL + "/"):
            base = self.set.get("personal") or self.set.get("common") or self.set.root_id
            rest = folder[len(PERSONAL):].strip("/")
            return self._sub(base, rest) if rest else base
        base = self.set.get("common") or self.set.root_id
        return self._sub(base, folder) if folder else base

    def _sub(self, parent: str, name: str) -> str:
        key = (parent, nfc(name))
        if key not in self._subs:
            self._subs[key] = self.client.subfolder(parent, name)
        return self._subs[key]

    def upload_new(self, name: str, path: Path, folder: str = "") -> RemoteFile:
        parent = self._parent_for(name, folder)
        role = _ROLE_BY_NAME.get(nfc(name))
        as_sheet = bool(role and not folder and role != "overlay")  # feature files are Google Sheets
        rf = self.client.upload_new(parent, name, path, as_sheet=as_sheet)
        rf.folder = folder
        self._names[rf.id] = rf.name
        self._parents[rf.id] = parent
        if role and not folder and not self.set.get(role):
            self.set.set(role, rf.id, (self.set.path("lab") or "").rstrip("/") + "/" + rf.name)
        return rf

    def download(self, file_id: str, dest: Path) -> None:
        self.client.download(file_id, dest)

    def revision(self, file_id: str) -> str:
        return self.client.get(file_id).version

    def sheet_meta(self, file_id: str) -> dict:
        return self.client.sheet_meta(file_id)

    def sheet_batch(self, file_id: str, requests: list[dict]) -> None:
        self.client.sheet_batch(file_id, requests)

    def is_native(self, file_id: str) -> bool:
        return self.client.is_native(file_id)

    def update(self, file_id: str, path: Path) -> RemoteFile:
        return self.client.update(file_id, path, name=self._names.get(file_id, ""))

    def rename(self, file_id: str, name: str) -> None:
        self.client.rename(file_id, name)
        self._names[file_id] = name

    def trash(self, file_id: str) -> None:
        self.client.trash(file_id)

    def move_to_subfolder(self, file_id: str, name: str) -> None:
        parent = self._parents.get(file_id) or self.set.get("common") or self.set.root_id
        sub = self.client.subfolder(parent, name)
        self.client.move(file_id, parent, sub)


class RoleFileRemote:
    """For a feature file of the set (budget, reservations, order requests): a SyncedFile
    remote whose upload_new creates the file in the lab folder and records it in the set."""

    def __init__(self, client: DriveClient, location_set: LocationSet, role: str, on_created=None):
        self.client = client
        self.set = location_set
        self.role = role
        self.on_created = on_created

    def download(self, file_id: str, dest: Path) -> None:
        self.client.download(file_id, dest)

    def revision(self, file_id: str) -> str:
        return self.client.get(file_id).version

    def update(self, file_id: str, path: Path) -> RemoteFile:
        return self.client.update(file_id, path, name=DEFAULT_NAMES.get(self.role, ""))

    def is_native(self, file_id: str) -> bool:
        return self.client.is_native(file_id)

    def sheet_meta(self, file_id: str) -> dict:
        return self.client.sheet_meta(file_id)

    def sheet_batch(self, file_id: str, requests: list[dict]) -> None:
        self.client.sheet_batch(file_id, requests)

    def upload_new(self, name: str, path: Path, folder: str = "") -> RemoteFile:
        # feature files are made as Google Sheets (several people edit them at once)
        rf = self.client.upload_new(self.set.lab_folder(), name, path, as_sheet=name.lower().endswith(".xlsx"))
        self.set.set(self.role, rf.id, (self.set.path("lab") or "").rstrip("/") + "/" + rf.name)
        if self.on_created:
            self.on_created(self.role, rf.id)
        return rf
