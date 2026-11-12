"""The Drive folder as seen by FolderModel."""
from __future__ import annotations

import unicodedata
from pathlib import Path

from .drive import FOLDER_MIME, DriveClient, RemoteFile


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


class DriveFolderRemote:
    def __init__(self, client: DriveClient, folder_id: str):
        self.client = client
        self.folder_id = folder_id
        self._names: dict[str, str] = {}
        self._parents: dict[str, str] = {}
        self._subs: dict[str, str] = {}

    def folder_name(self) -> str:
        return self.client.folder(self.folder_id).name

    def list_files(self) -> list[RemoteFile]:
        """Files in the folder and in its subfolders (one level). Subfolders whose
        name starts with '_' (e.g. the archived .xls originals) are skipped."""
        entries = self.client.list_folder(self.folder_id)
        files = [f for f in entries if f.mime_type != FOLDER_MIME]
        for f in files:
            self._parents[f.id] = self.folder_id
        for sub in entries:
            if sub.mime_type == FOLDER_MIME and not sub.name.startswith("_"):
                self._subs[sub.name] = sub.id
                for f in self.client.list_folder(sub.id):
                    if f.mime_type != FOLDER_MIME:
                        f.folder = sub.name
                        self._parents[f.id] = sub.id
                        files.append(f)
        self._names.update({f.id: f.name for f in files})
        return files

    def list_sequence_files(self, folder_name: str = "Sequence Map") -> list[RemoteFile]:
        """The SnapGene maps, which sit one level deeper than everything else."""
        root, files = sequence_files(self.client, self.folder_id, folder_name)
        if root:
            self._subs.setdefault(nfc(folder_name), root)
        for f in files:
            self._parents[f.id] = getattr(f, "parent_id", root) or root
        self._names.update({f.id: f.name for f in files})
        return files

    def _sub_id(self, name: str) -> str:
        if not name:
            return self.folder_id
        if name not in self._subs:
            self._subs[name] = self.client.subfolder(self.folder_id, name)
        return self._subs[name]

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

    def upload_new(self, name: str, path: Path, folder: str = "") -> RemoteFile:
        parent = self._sub_id(folder)
        rf = self.client.upload_new(parent, name, path)
        rf.folder = folder
        self._names[rf.id] = rf.name
        self._parents[rf.id] = parent
        return rf

    def rename(self, file_id: str, name: str) -> None:
        self.client.rename(file_id, name)
        self._names[file_id] = name

    def trash(self, file_id: str) -> None:
        self.client.trash(file_id)

    def move_to_subfolder(self, file_id: str, name: str) -> None:
        sub = self.client.subfolder(self.folder_id, name)
        self.client.move(file_id, self._parents.get(file_id, self.folder_id), sub)


def _walk(client: DriveClient, folder_id: str) -> list[RemoteFile]:
    """Files directly in this folder and in its subfolders, one level down."""
    out: list[RemoteFile] = []
    for entry in client.list_folder(folder_id):
        if entry.mime_type != FOLDER_MIME:
            entry.folder = ""
            entry.parent_id = folder_id
            out.append(entry)
        elif not entry.name.startswith("_"):
            for f in client.list_folder(entry.id):
                if f.mime_type != FOLDER_MIME:
                    f.folder = nfc(entry.name)
                    f.parent_id = entry.id
                    out.append(f)
    return out


def sequence_files(client: DriveClient, start_id: str,
                   folder_name: str = "Sequence Map") -> tuple[str, list[RemoteFile]]:
    """The SnapGene maps under `start_id`, wherever the caller starts from.

    `start_id` may be the folder the app opened, or the Sequence Map folder
    itself when a location set points straight at it. Maps sit one level deeper
    than the stock spreadsheets (Sequence Map / Common Plasmid / *.dna), which
    is more than `DriveFolderRemote.list_files` walks, so they are collected
    here. Returns the folder the maps came from and its files, each one's
    `folder` set to the subfolder holding it.
    """
    here = _walk(client, start_id)
    if any(f.name.lower().endswith(".dna") for f in here):
        return start_id, here
    for entry in client.list_folder(start_id):
        if entry.mime_type == FOLDER_MIME and nfc(entry.name) == nfc(folder_name):
            return entry.id, _walk(client, entry.id)
    return "", []
