"""Shared helpers for the small record workbooks (장비 예약, 주문 요청): each sheet is a
table with a header row; rows are read as dicts keyed by header. The files are ordinary
workbooks people can open in Excel; unknown extra columns are kept as they are.
"""
from __future__ import annotations

import copy
import uuid
from pathlib import Path
from typing import Callable

from .importer import clean_text


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def read_sheets(path: Path, spec: dict[str, list[str]]) -> dict[str, list[dict]]:
    """{sheet: [row dict]} for each sheet named in spec (missing sheets are empty)."""
    from openpyxl import load_workbook

    out: dict[str, list[dict]] = {name: [] for name in spec}
    wb = load_workbook(path, read_only=True, data_only=True)
    try:
        for ws in wb.worksheets:
            if ws.title not in spec:
                continue
            rows = [[clean_text(c) for c in r] for r in ws.iter_rows(values_only=True)]
            if not rows:
                continue
            head = [h.strip() for h in rows[0]]
            for r in rows[1:]:
                if not any(r):
                    continue
                out[ws.title].append({h: (r[i] if i < len(r) else "") for i, h in enumerate(head) if h})
    finally:
        wb.close()
    return out


def write_sheets(path: Path, spec: dict[str, list[str]], data: dict[str, list[dict]],
                 widths: dict[str, list[int]] | None = None) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    for name, headers in spec.items():
        ws = wb.create_sheet(name)
        rows = data.get(name, [])
        extra = []
        for r in rows:  # columns people added by hand stay
            extra += [k for k in r if k not in headers and k not in extra and not k.startswith("_")]
        cols = headers + extra
        ws.append(cols)
        for c in ws[1]:
            c.font = Font(bold=True)
        for r in rows:
            ws.append([r.get(h, "") for h in cols])
        for i, w in enumerate((widths or {}).get(name, [])):
            ws.column_dimensions[get_column_letter(i + 1)].width = w
        ws.freeze_panes = "A2"
    wb.save(path)


class RecordBoard:
    """A record workbook in Drive, edited locally first and sent in batches (SyncedFile)."""

    SPEC: dict[str, list[str]] = {}
    WIDTHS: dict[str, list[int]] = {}
    NAME = ""
    CACHE = ""

    def __init__(self, remote, cache_dir: Path, file_id: str = ""):
        from .synced_file import SyncedFile
        self.file = SyncedFile(remote, Path(cache_dir) / self.CACHE, file_id, self.NAME,
                               self._read, self._write, self._empty)
        self.listeners: list[Callable[[], None]] = []

    def _empty(self) -> dict:
        return {name: [] for name in self.SPEC}

    def _read(self, path: Path) -> dict:
        return read_sheets(path, self.SPEC)

    def _write(self, path: Path, state: dict) -> None:
        write_sheets(path, self.SPEC, state, self.WIDTHS)

    @property
    def file_id(self) -> str:
        return self.file.file_id

    @property
    def state(self) -> dict:
        return self.file.state

    def rows(self, sheet: str) -> list[dict]:
        return self.file.state.get(sheet, [])

    def load(self):
        try:
            return self.file.load()
        except Exception:  # noqa: BLE001  the app still opens without this file
            self.file.state = self._empty()
            raise

    def sync(self) -> bool:
        return self.file.sync()

    def dirty(self) -> bool:
        return self.file.dirty()

    def edit(self, change: Callable[[dict], dict]) -> dict:
        state = self.file.edit(lambda st: change(_fill(st, self.SPEC)))
        for fn in list(self.listeners):
            fn()
        return state

    # row helpers used by subclasses
    def upsert(self, sheet: str, row: dict) -> None:
        row = copy.deepcopy(row)
        row.setdefault("ID", new_id())

        def change(st):
            rows = st[sheet]
            for i, r in enumerate(rows):
                if r.get("ID") == row["ID"]:
                    rows[i] = {**r, **row}
                    break
            else:
                rows.append(row)
            return st
        self.edit(change)

    def delete(self, sheet: str, rid: str) -> None:
        def change(st):
            st[sheet] = [r for r in st[sheet] if r.get("ID") != rid]
            return st
        self.edit(change)


def _fill(st: dict, spec: dict) -> dict:
    for name in spec:
        st.setdefault(name, [])
    return st
