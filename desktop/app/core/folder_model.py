"""A folder of spreadsheets shown as one project.

Every spreadsheet in the folder is a category, every sheet a group, every row
an item. On open the whole folder is read fresh; nothing but the user's
settings is stored separately (in OVERLAY_NAME inside the folder): category
scope / type / low-stock threshold, column types and roles, per-item status
overrides and notes. Those settings are laid over the data after it is read.

Every change made in the app is turned into the same edit on the original
file (a cell, an added or deleted row, a new or deleted column) and uploaded.

The model is storage-agnostic: `remote` is any object with the methods used
below (Google Drive in the app, a local folder in the tests).
"""
from __future__ import annotations

import json
import shutil
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .importer import Table, plan_columns
from .models import DEFAULT_THRESHOLD
from .project import Project
from .gsheet_ops import GSHEET_MIME, plan_requests
from .sheet_io import (SPREADSHEET_SUFFIXES, _read_xlsx, apply_ops, convert_xls, data_rows, new_workbook,
                       read_grid)

OVERLAY_NAME = "Asset Manager 설정.json"
LEGACY_PROJECT = "Asset Manager.labproj"
XLS_ARCHIVE_FOLDER = "_원본 xls"
from .notices import NOTICE_NAME  # noqa: E402
from .work import WORK_NAME  # noqa: E402
# files of other features in an old one-folder layout: never inventory categories
FEATURE_FILES = {"장비 예약.xlsx": "reservations", "주문 요청.xlsx": "order_requests",
                 "예산 입출 내역.xlsx": "budget"}
PERSONAL_FOLDER = "Personal Asset"
SHARED_SETTINGS = ("calendar", "webapp")  # generic settings kept in the folder's settings file (webapp: mobile web app password)
DEFAULT_STOP_MARKERS = ["최근 주문내역"]


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def is_native_sheet(rf) -> bool:
    """A Google Sheets file (no file extension; read through an .xlsx export)."""
    return getattr(rf, "mime_type", "") == GSHEET_MIME


def is_inventory_sheet(rf, name: str) -> bool:
    if name.startswith("~$"):
        return False
    return is_native_sheet(rf) or name.lower().endswith(SPREADSHEET_SUFFIXES)


@dataclass
class SheetState:
    name: str
    header_row: int
    width: int
    last_row: int          # last data row (0-based grid row)
    has_trailing: bool     # content below the list (notes, order history)


@dataclass
class FileState:
    id: str
    name: str
    path: Path
    revision: str
    tables: list[Table] = field(default_factory=list)
    sheets: dict[str, SheetState] = field(default_factory=dict)
    folder: str = ""  # subfolder in the data folder
    ops: list[dict] = field(default_factory=list)
    file_ops: list[tuple] = field(default_factory=list)  # ("rename", name) / ("trash",)
    native: bool = False  # a Google Sheet: edited cell by cell through the Sheets API

    @property
    def suffix(self) -> str:
        return "" if self.native else Path(self.name).suffix.lower()

    @property
    def stem(self) -> str:
        return self.name if self.native else Path(self.name).stem


@dataclass
class FlushResult:
    uploaded: list[str] = field(default_factory=list)
    remote_changed: bool = False       # someone else edited a file meanwhile; reload to see it
    skipped: list[str] = field(default_factory=list)


class FolderModel:
    def __init__(self, remote, cache_dir: Path, xls_policy: str = "convert"):
        self.remote = remote
        self.cache_dir = Path(cache_dir)
        self.files_dir = self.cache_dir / "files"
        self.xls_policy = xls_policy
        self.files: dict[str, FileState] = {}
        self.overlay: dict = {"version": 1, "categories": {}}
        self.overlay_id = ""
        self.notice_id = ""
        self.work_id = ""
        self.feature_ids: dict[str, str] = {}
        self.overlay_rev = ""
        self.overlay_patches: list[tuple[tuple, Any]] = []
        self.lock = threading.RLock()
        self.project: Project | None = None
        self._reset_maps()

    def _reset_maps(self) -> None:
        self.cat_file: dict[int, str] = {}
        self.item_loc: dict[int, tuple[str, int]] = {}
        self.item_key: dict[int, str] = {}
        self.field_cols: dict[int, dict[str, int]] = {}
        self._fcat: dict[int, int] = {}

    # ---- loading (no sqlite here: may run off the UI thread) ---------------
    def load(self) -> None:
        """Download every spreadsheet and the settings file into a fresh cache."""
        if self.files_dir.exists():
            shutil.rmtree(self.files_dir)
        self.files_dir.mkdir(parents=True)
        self.files.clear()
        self.notice_id = ""
        self.work_id = ""
        self.feature_ids = {}
        legacy = None
        for rf in self.remote.list_files():
            name = nfc(rf.name)
            if self._feature_file(rf, name):
                continue
            if name == OVERLAY_NAME and not getattr(rf, "folder", ""):
                self.overlay_id, self.overlay_rev = rf.id, rf.version
            elif name == NOTICE_NAME and not getattr(rf, "folder", ""):
                self.notice_id = rf.id  # notices: read by NoticeBoard, not an inventory category
            elif name == WORK_NAME and not getattr(rf, "folder", ""):
                self.work_id = rf.id  # 업무: read by WorkBoard, not an inventory category
            elif name == LEGACY_PROJECT:
                legacy = rf
            elif is_inventory_sheet(rf, name):
                self._download_file(rf)
        if self.overlay_id:
            dest = self.cache_dir / "overlay.json"
            self.remote.download(self.overlay_id, dest)
            try:
                self.overlay = json.loads(dest.read_text(encoding="utf-8"))
            except ValueError:
                self.overlay = {"version": 1, "categories": {}}
            self.overlay.setdefault("categories", {})
        elif legacy is not None:
            self._migrate_legacy(legacy)

    def _feature_file(self, rf, name: str) -> bool:
        """A file of another feature (budget, reservations, order requests): remember its
        id for that feature and keep it out of the inventory."""
        reserved = getattr(self.remote, "reserved_ids", None)
        if reserved is not None and rf.id in reserved():
            return True
        role = FEATURE_FILES.get(name) if not getattr(rf, "folder", "") else None
        if role:
            self.feature_ids[role] = rf.id
            return True
        return False

    def _download_file(self, rf) -> FileState:
        native = is_native_sheet(rf)
        path = self.files_dir / f"{rf.id}{'.xlsx' if native else Path(rf.name).suffix.lower()}"
        self.remote.download(rf.id, path)  # a Google Sheet arrives as an .xlsx export
        fs = FileState(rf.id, nfc(rf.name), path, rf.version, folder=nfc(getattr(rf, "folder", "") or ""),
                       native=native)
        try:
            fs.tables = read_grid(path)
        except Exception:  # noqa: BLE001  unreadable file: show it empty rather than fail the folder
            fs.tables = []
        self.files[rf.id] = fs
        return fs

    def refresh_changed(self) -> bool:
        """Download only the files that changed elsewhere since they were read (new ones,
        a new revision, the settings file), forget removed ones. True when the inventory
        has to be rebuilt. Run after flush(), so nothing of ours is lost."""
        changed = False
        seen: set[str] = set()
        for rf in self.remote.list_files():
            name = nfc(rf.name)
            top = not getattr(rf, "folder", "")
            if self._feature_file(rf, name):
                continue
            if name == OVERLAY_NAME and top:
                if rf.version != self.overlay_rev:
                    dest = self.cache_dir / "overlay.json"
                    self.remote.download(rf.id, dest)
                    try:
                        overlay = json.loads(dest.read_text(encoding="utf-8"))
                    except ValueError:
                        overlay = {"version": 1, "categories": {}}
                    overlay.setdefault("categories", {})
                    with self.lock:
                        self.overlay, self.overlay_id, self.overlay_rev = overlay, rf.id, rf.version
                    changed = True
                continue
            if name == NOTICE_NAME and top:
                self.notice_id = rf.id
                continue
            if name == WORK_NAME and top:
                self.work_id = rf.id
                continue
            if name == LEGACY_PROJECT or not is_inventory_sheet(rf, name):
                continue
            seen.add(rf.id)
            fs = self.files.get(rf.id)
            if fs is not None and (fs.ops or fs.file_ops):
                continue  # edits made meanwhile go first; the next sync reads it again
            if fs is None or fs.revision != rf.version:
                self.files_dir.mkdir(parents=True, exist_ok=True)
                with self.lock:
                    self._download_file(rf)
                changed = True
            elif fs.name != name or fs.folder != nfc(getattr(rf, "folder", "") or ""):
                fs.name, fs.folder = name, nfc(getattr(rf, "folder", "") or "")
                changed = True
        with self.lock:
            for fid in set(self.files) - seen:
                if not (self.files[fid].ops or self.files[fid].file_ops):
                    self.files.pop(fid).path.unlink(missing_ok=True)
                    changed = True
        return changed

    def changed_files(self) -> bool:
        """True when the folder differs from what was loaded (poll; network only)."""
        seen = set()
        for rf in self.remote.list_files():
            name = nfc(rf.name)
            if self._feature_file(rf, name):
                continue
            if name == OVERLAY_NAME:
                if rf.version != self.overlay_rev:
                    return True
                continue
            if name in (NOTICE_NAME, WORK_NAME):
                continue
            if not is_inventory_sheet(rf, name):
                continue
            seen.add(rf.id)
            fs = self.files.get(rf.id)
            if fs is None or fs.revision != rf.version or fs.name != name:
                return True
        return seen != set(self.files)

    # ---- building the project (UI thread) ---------------------------------
    def build(self, project_path: Path, name: str = "") -> Project:
        if self.project is not None:
            self.project.observer = None
            self.project.close()
        project = Project.create(project_path, name or "Google Drive")
        self._reset_maps()
        self.project = project
        cats = self.overlay.setdefault("categories", {})
        order = sorted(self.files.values(),
                       key=lambda f: (cats.get(f.id, {}).get("sort_order", 10_000), f.name.lower()))
        for fs in order:
            self._build_category(project, fs, cats.get(fs.id, {}))
        if self.overlay.get("briefing"):
            project.set_briefing(self.overlay["briefing"], notify=False)
        for key in SHARED_SETTINGS:
            if key in self.overlay:
                project.set_setting(key, self.overlay[key], notify=False)
        if self.overlay.get("members"):
            project.set_members(self.overlay["members"], notify=False)
        if self.overlay.get("orders", {}).get("contact"):
            project.set_order_contact(self.overlay["orders"]["contact"], notify=False)
        project.commit()
        project.observer = self._on_change
        return project

    def _build_category(self, project: Project, fs: FileState, co: dict) -> None:
        cat = project.add_category(co.get("scope", default_scope(fs.folder)), fs.stem, co.get("icon", ""),
                                   co.get("threshold", DEFAULT_THRESHOLD), co.get("kind", "managed"))
        cid = cat.id
        self.cat_file[cid] = fs.id
        fo_all = co.get("fields", {})
        sheets_o = co.get("sheets", {})
        active = []
        for t in fs.tables:
            so = sheets_o.get(t.name, {})
            if so.get("skip"):
                continue
            if "header_row" in so:
                t.header_row = int(so["header_row"])
            t.stop_markers = so.get("stop_markers", DEFAULT_STOP_MARKERS)
            active.append(t)
        fmap: dict[str, int] = {}
        used_roles = {fo["role"] for fo in fo_all.values() if fo.get("role")}
        fs.sheets.clear()
        for t in active:
            plans = plan_columns(t)
            for plan in plans:
                key = plan.header.lower()
                if key not in fmap:
                    fo = fo_all.get(key, {})
                    role = fo["role"] if "role" in fo else ("" if plan.role in used_roles else plan.role)
                    if role and "role" not in fo:
                        used_roles.add(role)
                    f = project.add_field(cid, plan.header, fo.get("ftype", plan.ftype), role)
                    fmap[key] = f.id
                    self.field_cols[f.id] = {}
                self.field_cols[fmap[key]].setdefault(t.name, plan.index)
            rows = data_rows(t)
            last = rows[-1][0] if rows else t.header_row
            trailing = any(any(c not in (None, "") for c in r) for r in t.raw[last + 1:])
            fs.sheets[t.name] = SheetState(t.name, t.header_row, t.width, last, trailing)
            name_col = self._col_for_role(project, cid, "name", t.name)
            counts: dict[str, int] = {}
            for r, cells in rows:
                values = {fid: cells[cols[t.name]] for fid, cols in self.field_cols.items()
                          if t.name in cols and self._field_cat(project, fid) == cid and cells[cols[t.name]]}
                if not values or (name_col is not None and not cells[name_col]):
                    continue
                item_id = project.add_item(cid, values, subgroup=t.name, log=False, commit=False)
                self.item_loc[item_id] = (t.name, r)
                nm = cells[name_col] if name_col is not None else ""
                n = counts.get(nm, 0)
                counts[nm] = n + 1
                self.item_key[item_id] = f"{t.name}|{nm}|{n}"
        if not fs.sheets:  # empty workbook (e.g. just created)
            fs.sheets["Sheet1"] = SheetState("Sheet1", 0, 0, 0, False)
        # every sheet is a group, in workbook order, even when it has no rows yet
        project.set_declared_groups(cid, list(fs.sheets))
        if fs.native:  # shown as "Google 시트" in the sidebar and the page header
            project.set_meta(f"source:{cid}", "gsheet")
        if co.get("orderer"):  # a person's own order list: match only their orders
            project.set_meta(f"orderer:{cid}", co["orderer"])
        if co.get("owner"):
            o = co["owner"]
            project.set_owner(cid, o.get("name", ""), o.get("email", ""), notify=False)
        # column order chosen in the app
        orders = {fid: fo_all.get(project.field(fid).name.lower(), {}).get("order") for fid in fmap.values()}
        if any(v is not None for v in orders.values()):
            ordered = sorted(fmap.values(), key=lambda fid: (orders[fid] if orders[fid] is not None else 999, fid))
            for i, fid in enumerate(ordered):
                project.conn.execute("UPDATE fields SET sort_order = ? WHERE id = ?", (i, fid))
        # per-item settings
        items_o = co.get("items", {})
        if items_o:
            by_key = {k: iid for iid, k in self.item_key.items() if self._item_cat(iid) == cid}
            for key, io in items_o.items():
                iid = by_key.get(key)
                if iid is None:
                    continue
                if io.get("status") or io.get("threshold") is not None:
                    project.conn.execute("UPDATE items SET status = ?, threshold = ? WHERE id = ?",
                                         (io.get("status", ""), io.get("threshold"), iid))
                for k, v in io.get("details", []):
                    project.conn.execute(
                        "INSERT INTO item_details(item_id, key, value, sort_order) VALUES (?, ?, ?, 0)", (iid, k, v))
        for iid in [i for i in self.item_loc if self._item_cat(i) == cid]:
            project._index_item(iid)

    def _field_cat(self, project: Project, fid: int) -> int:
        if fid not in self._fcat:
            self._fcat[fid] = project.field(fid).category_id
        return self._fcat[fid]

    def _item_cat(self, item_id: int) -> int | None:
        row = self.project.conn.execute("SELECT category_id FROM items WHERE id = ?", (item_id,)).fetchone()
        return row[0] if row else None

    def _col_for_role(self, project: Project, cid: int, role: str, sheet: str) -> int | None:
        f = project.field_by_role(cid, role)
        return self.field_cols.get(f.id, {}).get(sheet) if f else None

    # ---- overlay ------------------------------------------------------------
    def _set_overlay(self, path: tuple, value: Any) -> None:
        with self.lock:
            _put(self.overlay, path, value)
            self.overlay_patches.append((path, value))

    # ---- change → file edits --------------------------------------------------
    def _on_change(self, event: str, **d: Any) -> None:
        handler = getattr(self, f"_ev_{event}", None)
        if handler is not None:
            handler(**d)

    def _file_of_cat(self, cid: int) -> FileState | None:
        fid = self.cat_file.get(cid)
        return self.files.get(fid) if fid else None

    def _queue(self, fs: FileState, op: dict) -> None:
        with self.lock:
            fs.ops.append(op)

    def _writable(self, fs: FileState) -> FileState:
        if fs.suffix != ".xls":
            return fs
        # The old binary format can't be edited in place: save it as .xlsx next to it
        # and move the original into a subfolder (nothing is deleted).
        new_path = fs.path.with_suffix(".xlsx")
        convert_xls(fs.path, new_path)
        rf = self.remote.upload_new(f"{fs.stem}.xlsx", new_path, fs.folder) if fs.folder \
            else self.remote.upload_new(f"{fs.stem}.xlsx", new_path)
        self.remote.move_to_subfolder(fs.id, XLS_ARCHIVE_FOLDER)
        old_id = fs.id
        with self.lock:
            del self.files[old_id]
            fs.id, fs.name, fs.revision = rf.id, nfc(rf.name), rf.version
            final = self.files_dir / f"{rf.id}.xlsx"
            new_path.replace(final)
            fs.path = final
            self.files[fs.id] = fs
            for cid, f in list(self.cat_file.items()):
                if f == old_id:
                    self.cat_file[cid] = fs.id
            cats = self.overlay.setdefault("categories", {})
            if old_id in cats:
                cats[fs.id] = cats.pop(old_id)
                self.overlay_patches.append((("categories", fs.id), cats[fs.id]))
                self.overlay_patches.append((("categories", old_id), None))
        return fs

    def _anchor(self, item_id: int, sheet: str, override: str | None = None) -> tuple[int, str] | None:
        item = self.project.item(item_id)
        if item is None:
            return None
        col = self._col_for_role(self.project, item.category_id, "name", sheet)
        if col is None:
            return None
        nf = self.project.field_by_role(item.category_id, "name")
        return (col, override if override is not None else item.get(nf.id))

    def _ensure_col(self, fs: FileState, fid: int, sheet: str) -> int:
        cols = self.field_cols.setdefault(fid, {})
        if sheet in cols:
            return cols[sheet]
        st = fs.sheets[sheet]
        col = st.width
        st.width += 1
        cols[sheet] = col
        f = self.project.field(fid)
        self._queue(fs, {"op": "set", "sheet": sheet, "row": st.header_row, "col": col, "value": f.name})
        return col

    def _is_virtual(self, field_id: int) -> bool:
        f = self.project.field(field_id)
        return f is None or f.virtual

    def _ev_value(self, item_id: int, field_id: int, old: str, new: str) -> None:
        if self._is_virtual(field_id):
            return
        loc = self.item_loc.get(item_id)
        item = self.project.item(item_id)
        fs = self._file_of_cat(item.category_id) if item else None
        if loc is None or fs is None:
            return
        fs = self._writable(fs)
        sheet, row = loc
        col = self._ensure_col(fs, field_id, sheet)
        f = self.project.field(field_id)
        is_name = f is not None and f.role == "name"
        anchor = self._anchor(item_id, sheet, override=old if is_name else None)
        self._queue(fs, {"op": "set", "sheet": sheet, "row": row, "col": col, "value": new, "anchor": anchor})
        if is_name:
            self._rekey_item(item_id, sheet, new)

    def _rekey_item(self, item_id: int, sheet: str, name: str) -> None:
        old_key = self.item_key.get(item_id)
        new_key = f"{sheet}|{name}|0"
        n = 0
        taken = set(self.item_key.values())
        while new_key in taken and new_key != old_key:
            n += 1
            new_key = f"{sheet}|{name}|{n}"
        self.item_key[item_id] = new_key
        item = self.project.item(item_id)
        fid = self.cat_file.get(item.category_id)
        items_o = self.overlay.get("categories", {}).get(fid, {}).get("items", {})
        if old_key in items_o and old_key != new_key:
            entry = items_o[old_key]
            self._set_overlay(("categories", fid, "items", old_key), None)
            self._set_overlay(("categories", fid, "items", new_key), entry)

    def _ev_item_add(self, item_id: int) -> None:
        item = self.project.item(item_id)
        fs = self._file_of_cat(item.category_id)
        if fs is None:
            return
        fs = self._writable(fs)
        sheet = item.subgroup if item.subgroup in fs.sheets else next(iter(fs.sheets))
        st = fs.sheets[sheet]
        row = st.last_row + 1
        values = {}
        for key, text in item.values.items():
            if not self._is_virtual(int(key)):
                values[str(self._ensure_col(fs, int(key), sheet))] = text
        self._queue(fs, {"op": "insert_row", "sheet": sheet, "row": row, "shift": st.has_trailing,
                         "values": values})
        st.last_row = row
        self.item_loc[item_id] = (sheet, row)
        nf = self.project.field_by_role(item.category_id, "name")
        self._rekey_item(item_id, sheet, item.get(nf.id) if nf else "")

    def _ev_item_delete(self, item) -> None:
        loc = self.item_loc.pop(item.id, None)
        fs = self._file_of_cat(item.category_id)
        if loc is None or fs is None:
            return
        fs = self._writable(fs)
        sheet, row = loc
        nf = self.project.field_by_role(item.category_id, "name")
        col = self._col_for_role(self.project, item.category_id, "name", sheet)
        anchor = (col, item.get(nf.id)) if nf and col is not None else None
        self._queue(fs, {"op": "delete_row", "sheet": sheet, "row": row, "anchor": anchor})
        for iid, (s, r) in list(self.item_loc.items()):
            if s == sheet and r > row and self._item_cat(iid) == item.category_id:
                self.item_loc[iid] = (s, r - 1)
        fs.sheets[sheet].last_row -= 1
        key = self.item_key.pop(item.id, None)
        if key and key in self.overlay.get("categories", {}).get(fs.id, {}).get("items", {}):
            self._set_overlay(("categories", fs.id, "items", key), None)

    def _ev_field_add(self, field_id: int) -> None:
        f = self.project.field(field_id)
        fs = self._file_of_cat(f.category_id)
        if fs is None:
            return
        fs = self._writable(fs)
        for sheet in fs.sheets:
            self._ensure_col(fs, field_id, sheet)
        self._field_overlay(f)

    def _field_overlay(self, f) -> None:
        if f.virtual:
            return
        fid = self.cat_file.get(f.category_id)
        self._set_overlay(("categories", fid, "fields", f.name.lower()),
                          {"ftype": f.ftype, "role": f.role, "order": f.sort_order})

    def _ev_field_update(self, field_id: int, old, changes: dict) -> None:
        f = self.project.field(field_id)
        if f.virtual:
            return
        fs = self._file_of_cat(f.category_id)
        if fs is None:
            return
        if "name" in changes:
            fs = self._writable(fs)
            for sheet, col in self.field_cols.get(field_id, {}).items():
                self._queue(fs, {"op": "set", "sheet": sheet, "row": fs.sheets[sheet].header_row,
                                 "col": col, "value": f.name})
            self._set_overlay(("categories", fs.id, "fields", old.name.lower()), None)
        if "role" in changes:  # another field may have lost the role
            for other in self.project.fields(f.category_id):
                self._field_overlay(other)
        else:
            self._field_overlay(f)

    def _ev_field_delete(self, field) -> None:
        if field.virtual:
            return
        fs = self._file_of_cat(field.category_id)
        cols = self.field_cols.pop(field.id, {})
        if fs is None:
            return
        fs = self._writable(fs)
        for sheet, col in cols.items():
            self._queue(fs, {"op": "delete_col", "sheet": sheet, "col": col})
            for other in self.field_cols.values():
                if other.get(sheet, -1) > col and self._same_file(other, fs):
                    other[sheet] -= 1
            fs.sheets[sheet].width -= 1
        self._set_overlay(("categories", fs.id, "fields", field.name.lower()), None)

    def _same_file(self, cols: dict, fs: FileState) -> bool:
        return any(s in fs.sheets for s in cols)

    def _ev_field_reorder(self) -> None:
        for cid in self.cat_file:
            for f in self.project.fields(cid):
                self._field_overlay(f)

    def _ev_category_add(self, category_id: int) -> None:
        cat = self.project.category(category_id)
        tmp = self.files_dir / f"new-{category_id}.xlsx"
        new_workbook(tmp, [])
        # a Personal Asset category goes into the personal folder, a common one at the top
        folder = PERSONAL_FOLDER if cat.scope == "personal" else ""
        rf = (self.remote.upload_new(f"{cat.name}.xlsx", tmp, folder) if folder
              else self.remote.upload_new(f"{cat.name}.xlsx", tmp))
        path = self.files_dir / f"{rf.id}.xlsx"
        tmp.replace(path)
        fs = FileState(rf.id, nfc(rf.name), path, rf.version, folder=folder)
        fs.sheets["Sheet1"] = SheetState("Sheet1", 0, 0, 0, False)
        with self.lock:
            self.files[rf.id] = fs
        self.cat_file[category_id] = rf.id
        self._category_overlay(cat)

    def _category_overlay(self, cat) -> None:
        fid = self.cat_file.get(cat.id)
        if fid:
            for k in ("scope", "kind", "threshold", "icon", "sort_order"):
                self._set_overlay(("categories", fid, k), getattr(cat, k))

    def _ev_category_update(self, category_id: int, changes: dict) -> None:
        cat = self.project.category(category_id)
        fs = self._file_of_cat(category_id)
        if fs is None:
            return
        if "name" in changes:
            new_name = f"{changes['name']}{fs.suffix}"
            with self.lock:
                fs.file_ops.append(("rename", new_name))
            fs.name = nfc(new_name)
        self._category_overlay(cat)

    def _ev_category_delete(self, category_id: int) -> None:
        fs = self._file_of_cat(category_id)
        if fs is None:
            return
        with self.lock:
            fs.ops.clear()
            fs.file_ops.append(("trash",))
        self._set_overlay(("categories", fs.id), None)

    def _ev_category_reorder(self) -> None:
        for cat in self.project.categories():
            self._category_overlay(cat)

    def _ev_category_owner(self, category_id: int, owner: dict) -> None:
        fid = self.cat_file.get(category_id)
        if fid:
            self._set_overlay(("categories", fid, "owner"), owner or None)

    def _ev_setting(self, key: str, value) -> None:
        self._set_overlay((key,), value)

    def _ev_members(self, members: list) -> None:
        self._set_overlay(("members",), members)

    def _ev_order_contact(self, value: dict) -> None:
        self._set_overlay(("orders", "contact"), value or None)

    def _ev_briefing(self, value: dict) -> None:
        self._set_overlay(("briefing",), value)

    # sheets = groups
    def _ev_subgroup_add(self, category_id: int, name: str) -> None:
        fs = self._file_of_cat(category_id)
        if fs is None or fs.suffix in (".csv", ".tsv"):
            return
        fs = self._writable(fs)
        fields = [f for f in self.project.fields(category_id) if not f.virtual]
        self._queue(fs, {"op": "add_sheet", "sheet": name, "headers": [f.name for f in fields]})
        fs.sheets[name] = SheetState(name, 0, len(fields), 0, False)
        for i, f in enumerate(fields):
            self.field_cols.setdefault(f.id, {})[name] = i

    def _ev_subgroup_rename(self, category_id: int, old: str, new: str) -> None:
        fs = self._file_of_cat(category_id)
        if fs is None or old not in fs.sheets:
            return
        fs = self._writable(fs)
        self._queue(fs, {"op": "rename_sheet", "old": old, "new": new})
        st = fs.sheets.pop(old)
        st.name = new
        fs.sheets[new] = st
        for fid, cols in self.field_cols.items():
            if old in cols and self._field_cat(self.project, fid) == category_id:
                cols[new] = cols.pop(old)
        for iid, (sh, r) in list(self.item_loc.items()):
            if sh == old and self._item_cat(iid) == category_id:
                self.item_loc[iid] = (new, r)
                key = self.item_key.get(iid, "")
                if key.startswith(old + "|"):
                    self.item_key[iid] = new + key[len(old):]
        co = self.overlay.get("categories", {}).get(fs.id, {})
        for key, entry in list(co.get("items", {}).items()):
            if key.startswith(old + "|"):
                self._set_overlay(("categories", fs.id, "items", key), None)
                self._set_overlay(("categories", fs.id, "items", new + key[len(old):]), entry)
        if old in co.get("sheets", {}):
            entry = co["sheets"][old]
            self._set_overlay(("categories", fs.id, "sheets", old), None)
            self._set_overlay(("categories", fs.id, "sheets", new), entry)

    def _ev_subgroup_delete(self, category_id: int, name: str) -> None:
        fs = self._file_of_cat(category_id)
        if fs is None or name not in fs.sheets:
            return
        fs = self._writable(fs)
        self._queue(fs, {"op": "delete_sheet", "sheet": name})
        del fs.sheets[name]
        for fid, cols in self.field_cols.items():
            if name in cols and self._field_cat(self.project, fid) == category_id:
                del cols[name]
        co = self.overlay.get("categories", {}).get(fs.id, {})
        for key in list(co.get("items", {})):
            if key.startswith(name + "|"):
                self._set_overlay(("categories", fs.id, "items", key), None)
        for iid, (sh, _r) in list(self.item_loc.items()):
            if sh == name and self._item_cat(iid) == category_id:
                del self.item_loc[iid]
                self.item_key.pop(iid, None)

    def _item_overlay(self, item_id: int) -> None:
        item = self.project.item(item_id)
        key = self.item_key.get(item_id)
        fid = self.cat_file.get(item.category_id) if item else None
        if not key or not fid:
            return
        details = [[d.key, d.value] for d in self.project.details(item_id)]
        entry = {"status": item.status, "threshold": item.threshold, "details": details}
        if not item.status and item.threshold is None and not details:
            entry = None
        self._set_overlay(("categories", fid, "items", key), entry)

    def _ev_item_update(self, item_id: int, changes: dict) -> None:
        item = self.project.item(item_id)
        fs = self._file_of_cat(item.category_id) if item else None
        loc = self.item_loc.get(item_id)
        if "subgroup" in changes and fs is not None and loc and loc[0] != item.subgroup:
            # moved to another sheet: the row leaves the old sheet and is appended to the new one
            if item.subgroup not in fs.sheets and fs.suffix not in (".csv", ".tsv"):
                self._ev_subgroup_add(item.category_id, item.subgroup)
            if item.subgroup in fs.sheets:
                self._ev_item_delete(item)
                self._ev_item_add(item_id)
        self._item_overlay(item_id)

    def _ev_details(self, item_id: int) -> None:
        self._item_overlay(item_id)

    # ---- sync (network; may run off the UI thread) ---------------------------
    def pending(self) -> bool:
        with self.lock:
            return bool(self.overlay_patches) or any(f.ops or f.file_ops for f in self.files.values())

    def flush(self) -> FlushResult:
        res = FlushResult()
        with self.lock:
            work = [(fs, fs.ops[:], fs.file_ops[:]) for fs in list(self.files.values()) if fs.ops or fs.file_ops]
            for fs, ops, fops in work:
                del fs.ops[:len(ops)]
                del fs.file_ops[:len(fops)]
        for i, (fs, ops, fops) in enumerate(work):
            try:
                self._flush_file(fs, ops, fops, res)
            except Exception:
                with self.lock:  # keep everything not yet sent for the next try
                    for fs2, ops2, fops2 in work[i:]:
                        fs2.ops[:0] = ops2
                        fs2.file_ops[:0] = fops2
                raise
        self._flush_overlay(res)
        return res

    def _flush_file(self, fs: FileState, ops: list[dict], fops: list[tuple], res: FlushResult) -> None:
        if ("trash",) in fops:
            self.remote.trash(fs.id)
            with self.lock:
                self.files.pop(fs.id, None)
            res.uploaded.append(fs.name)
            return
        if ops and fs.native:
            self._flush_native(fs, ops, res)
        elif ops:
            rev = self.remote.revision(fs.id)
            changed = rev != fs.revision
            if changed:
                self.remote.download(fs.id, fs.path)
                res.remote_changed = True
            tmp = fs.path.with_name(fs.path.stem + ".upload" + fs.path.suffix)
            shutil.copyfile(fs.path, tmp)
            try:
                result = apply_ops(tmp, ops, relocate=changed)
                rf = self.remote.update(fs.id, tmp)
                tmp.replace(fs.path)
            finally:
                if tmp.exists():
                    tmp.unlink()
            fs.revision = rf.version
            try:  # the cached grid now holds our edits too (a later rebuild must not lose them)
                fs.tables = read_grid(fs.path)
            except Exception:  # noqa: BLE001
                pass
            res.skipped += [f"{fs.stem} {s}" for s in result.skipped]
        for op in fops:
            if op[0] == "rename":
                self.remote.rename(fs.id, op[1])
        res.uploaded.append(fs.name)

    def _flush_native(self, fs: FileState, ops: list[dict], res: FlushResult) -> None:
        """Send edits to a Google Sheet as cell/row/column requests (never a whole-file
        upload: other people may be typing in the sheet right now)."""
        if self.remote.revision(fs.id) != fs.revision:
            res.remote_changed = True
        fresh = fs.path.with_name(fs.path.stem + ".fresh.xlsx")
        try:
            self.remote.download(fs.id, fresh)  # the values as they are now
            grids = {t.name: t.raw for t in _read_xlsx(fresh)}
        finally:
            fresh.unlink(missing_ok=True)
        meta = self.remote.sheet_meta(fs.id)
        requests, result = plan_requests(grids, meta, ops)
        if requests:
            self.remote.sheet_batch(fs.id, requests)
        # revision first, then the copy: an edit landing in between makes the next
        # sync read the sheet again instead of being missed
        fs.revision = self.remote.revision(fs.id)
        self.remote.download(fs.id, fs.path)
        try:
            fs.tables = read_grid(fs.path)
        except Exception:  # noqa: BLE001
            pass
        res.skipped += [f"{fs.stem} {s}" for s in result.skipped]

    def _flush_overlay(self, res: FlushResult) -> None:
        with self.lock:
            patches = self.overlay_patches[:]
            if not patches:
                return
            del self.overlay_patches[:len(patches)]
        try:
            path = self.cache_dir / "overlay.upload.json"
            if self.overlay_id:
                rev = self.remote.revision(self.overlay_id)
                if rev != self.overlay_rev:  # someone else changed settings: merge ours onto theirs
                    self.remote.download(self.overlay_id, path)
                    try:
                        theirs = json.loads(path.read_text(encoding="utf-8"))
                    except ValueError:
                        theirs = {"version": 1, "categories": {}}
                    with self.lock:
                        for p, v in patches:
                            _put(theirs, p, v)
                        self.overlay = theirs
                    res.remote_changed = True
            with self.lock:
                text = json.dumps(self.overlay, ensure_ascii=False, indent=1)
            path.write_text(text, encoding="utf-8")
            if self.overlay_id:
                rf = self.remote.update(self.overlay_id, path)
            else:
                rf = self.remote.upload_new(OVERLAY_NAME, path)
                self.overlay_id = rf.id
            self.overlay_rev = rf.version
            res.uploaded.append(OVERLAY_NAME)
        except Exception:
            with self.lock:
                self.overlay_patches[:0] = patches
            raise

    # ---- old single-file projects ---------------------------------------------
    def _migrate_legacy(self, rf) -> None:
        """Carry category settings over from the earlier 'Asset Manager.labproj' design."""
        dest = self.cache_dir / "legacy.labproj"
        try:
            self.remote.download(rf.id, dest)
            old = Project(dest)
        except Exception:  # noqa: BLE001
            return
        by_stem = {nfc(fs.stem).lower(): fs for fs in self.files.values()}
        try:
            for cat in old.categories():
                fs = by_stem.get(nfc(cat.name).lower())
                if fs is None:
                    continue
                base = ("categories", fs.id)
                for k in ("scope", "kind", "threshold", "icon", "sort_order"):
                    self._set_overlay(base + (k,), getattr(cat, k))
                for f in old.fields(cat.id):
                    self._set_overlay(base + ("fields", f.name.lower()), {"ftype": f.ftype, "role": f.role})
        finally:
            old.close()


def default_scope(folder: str) -> str:
    """Files in a subfolder named like 'Personal Asset' / '개인 …' start as Personal Asset."""
    f = (folder or "").lower()
    return "personal" if ("personal" in f or "개인" in f) else "common"


def _put(d: dict, path: tuple, value: Any) -> None:
    for key in path[:-1]:
        nxt = d.get(key)
        if not isinstance(nxt, dict):
            nxt = d[key] = {}
        d = nxt
    if value is None:
        d.pop(path[-1], None)
    else:
        d[path[-1]] = value
