"""Notices (공지사항) kept in one Excel file in the Drive folder.

The file is an ordinary sheet people can also open in Excel:
ID | 날짜 | 제목 | 내용 | 작성자 | 중요 | 열람
열람 lists who has read the notice, comma separated.
It is not shown as an inventory category.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .importer import clean_text

NOTICE_NAME = "공지사항.xlsx"
HEADERS = ["ID", "날짜", "제목", "내용", "작성자", "중요", "열람"]


@dataclass
class Notice:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))
    title: str = ""
    body: str = ""
    author: str = ""
    pinned: bool = False
    readers: list[str] = field(default_factory=list)

    def read_by(self, name: str) -> bool:
        return bool(name) and name in self.readers


def read_notices(path: Path) -> list[Notice]:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = [[clean_text(c) for c in r] for r in ws.iter_rows(values_only=True)]
    wb.close()
    if not rows:
        return []
    head = [h.strip() for h in rows[0]]
    col = {h: head.index(h) for h in HEADERS if h in head}

    def get(r, h):
        i = col.get(h)
        return r[i] if i is not None and i < len(r) else ""

    out = []
    for r in rows[1:]:
        if not any(r):
            continue
        out.append(Notice(get(r, "ID") or uuid.uuid4().hex[:8], get(r, "날짜"), get(r, "제목"), get(r, "내용"),
                          get(r, "작성자"), get(r, "중요").strip().lower() in ("o", "y", "yes", "true", "1", "중요", "★"),
                          split_names(get(r, "열람"))))
    return sort_notices(out)


def split_names(text: str) -> list[str]:
    out = []
    for n in (text or "").replace("\n", ",").split(","):
        n = n.strip()
        if n and n not in out:
            out.append(n)
    return out


def newest(notices: list[Notice], count: int = 3) -> list[Notice]:
    """The latest `count` notices by date (the dashboard)."""
    return sorted(notices, key=lambda n: _neg(n.date))[:count]


def sort_notices(notices: list[Notice]) -> list[Notice]:
    """Pinned first, then newest first."""
    return sorted(notices, key=lambda n: (not n.pinned, _neg(n.date)))


def _neg(text: str) -> str:
    return "".join(chr(0x10FFFF - ord(c)) for c in text)


def write_notices(path: Path, notices: list[Notice]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    ws = wb.active
    ws.title = "공지사항"
    ws.append(HEADERS)
    for c in ws[1]:
        c.font = Font(bold=True)
    for n in sort_notices(notices):
        ws.append([n.id, n.date, n.title, n.body, n.author, "★" if n.pinned else "", ", ".join(n.readers)])
    for col, width in zip("ABCDEFG", (10, 17, 40, 80, 12, 6, 30)):
        ws.column_dimensions[col].width = width
    for row in ws.iter_rows(min_row=2):
        row[3].alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"
    wb.save(path)


class NoticeBoard:
    """The notice file in the Drive folder. Edits show at once and are sent to Drive in
    batches by sync() (see synced_file); a change re-applies on whatever is newest there,
    so notices written by someone else meanwhile are kept."""

    def __init__(self, remote, cache_dir: Path, file_id: str = ""):
        from .synced_file import SyncedFile
        self.file = SyncedFile(remote, Path(cache_dir) / "notices.xlsx", file_id, NOTICE_NAME,
                               read_notices, write_notices, list)

    @property
    def remote(self):
        return self.file.remote

    @property
    def file_id(self) -> str:
        return self.file.file_id

    @property
    def path(self) -> Path:
        return self.file.path

    @property
    def notices(self) -> list[Notice]:
        return self.file.state

    @notices.setter
    def notices(self, value: list[Notice]) -> None:
        self.file.state = value

    def load(self) -> list[Notice]:
        return self.file.load()

    def sync(self) -> bool:
        return self.file.sync()

    def dirty(self) -> bool:
        return self.file.dirty()

    def _save(self, change) -> list[Notice]:
        return self.file.edit(lambda ns: sort_notices(change(ns)))

    def upsert(self, notice: Notice) -> list[Notice]:
        return self._save(lambda ns: [n for n in ns if n.id != notice.id] + [notice])

    def set_read(self, notice_id: str, name: str, read: bool = True) -> list[Notice]:
        def change(ns):
            for n in ns:
                if n.id == notice_id:
                    n.readers = [r for r in n.readers if r != name] + ([name] if read else [])
            return ns
        return self._save(change)

    def delete(self, notice_id: str) -> list[Notice]:
        return self._save(lambda ns: [n for n in ns if n.id != notice_id])
