"""One shared workbook in the Drive folder, edited locally first and sent in batches.

An edit is a function `change(state) -> state`. It is applied at once to the state in
memory (and the local copy of the file), so the screen answers immediately, and it is
kept in a queue. sync() (every two minutes, on 최신화, before closing) sends the queue:
it reads the file again only when its Drive revision moved, replays the queued edits on
that, and uploads once. With nothing queued, sync() just downloads the file if someone
else changed it. Without a remote (a local project) every edit is written straight away.

A Google Sheet is never uploaded as a whole (others may be typing in it): the state
before and after the queued edits are both written as workbooks, compared, and only the
cells that differ are sent through the Sheets API (gsheet_ops.diff_requests).
"""
from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Any, Callable


class SyncedFile:
    def __init__(self, remote, path: Path, file_id: str, name: str,
                 read: Callable[[Path], Any], write: Callable[[Path, Any], None], empty: Callable[[], Any]):
        self.remote = remote
        self.path = Path(path)
        self.file_id = file_id
        self.name = name
        self._read, self._write, self._empty = read, write, empty
        self.rev = ""
        self.base = empty()      # the file as last read from / written to Drive
        self.state = empty()     # base + the edits not sent yet
        self.pending: list[Callable] = []
        self.lock = threading.RLock()

    # -- reading --
    def load(self) -> Any:
        """Read the file from Drive (sending anything queued first)."""
        if self.pending:
            self.sync()
            return self.state
        if self.remote is not None and self.file_id:
            rev = self.remote.revision(self.file_id)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.remote.download(self.file_id, self.path)
            base = self._read(self.path)
            with self.lock:
                self.base, self.rev = base, rev
                self.state = self._replay(base, self.pending)
        elif self.remote is None and self.path.exists():
            with self.lock:
                self.base = self.state = self._read(self.path)
        else:
            with self.lock:
                self.base, self.state = self._empty(), self._empty()
        return self.state

    @staticmethod
    def _replay(base, changes):
        state = copy.deepcopy(base)
        for change in changes:
            state = change(state)
        return state

    # -- editing (UI thread; no network) --
    def edit(self, change: Callable) -> Any:
        with self.lock:
            self.state = change(copy.deepcopy(self.state))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._write(self.path, self.state)
            if self.remote is None:
                self.base = copy.deepcopy(self.state)
            else:
                self.pending.append(change)
        return self.state

    def dirty(self) -> bool:
        return bool(self.pending)

    # -- sending / receiving (may run off the UI thread) --
    def sync(self) -> bool:
        """Send queued edits and pick up other people's. True when someone else had
        changed the file (the screen should show the new state)."""
        if self.remote is None:
            return False
        with self.lock:
            sent = list(self.pending)
        if not sent and not self.file_id:
            return False
        rev = self.remote.revision(self.file_id) if self.file_id else ""
        theirs = bool(self.file_id) and rev != self.rev
        if not sent and not theirs:
            return False
        if theirs:
            tmp = self.path.with_name(self.path.stem + ".remote" + self.path.suffix)
            self.remote.download(self.file_id, tmp)
            base = self._read(tmp)
            tmp.unlink(missing_ok=True)
        else:
            with self.lock:
                base = copy.deepcopy(self.base)
        if sent and self.file_id and self._native():
            new = self._replay(base, sent)
            self._send_cells(base, new)
            rev = self.remote.revision(self.file_id)
            base = new
        elif sent:
            new = self._replay(base, sent)
            up = self.path.with_name(self.path.name + ".upload" + self.path.suffix)
            self._write(up, new)
            try:
                rf = self.remote.update(self.file_id, up) if self.file_id else self.remote.upload_new(self.name, up)
            finally:
                up.unlink(missing_ok=True)
            self.file_id = rf.id if not self.file_id else self.file_id
            rev = getattr(rf, "version", "") or self.remote.revision(self.file_id)
            base = new
        with self.lock:
            del self.pending[:len(sent)]  # edits made meanwhile stay queued
            self.base, self.rev = base, rev
            self.state = self._replay(base, self.pending)
            self._write(self.path, self.state)
        return theirs

    def _native(self) -> bool:
        check = getattr(self.remote, "is_native", None)
        return bool(check and check(self.file_id))

    def _send_cells(self, base, new) -> None:
        from .gsheet_ops import diff_requests, workbook_grids

        a = self.path.with_name(self.path.stem + ".before" + self.path.suffix)
        b = self.path.with_name(self.path.stem + ".after" + self.path.suffix)
        try:
            self._write(a, base)
            self._write(b, new)
            reqs = diff_requests(workbook_grids(a), workbook_grids(b), self.remote.sheet_meta(self.file_id))
        finally:
            a.unlink(missing_ok=True)
            b.unlink(missing_ok=True)
        if reqs:
            self.remote.sheet_batch(self.file_id, reqs)
