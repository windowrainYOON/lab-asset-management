"""Excel files in the lab folder -> Google Sheets.

Many people edit the lab's shared files (예산, 업무, 공지사항, 장비 예약, 주문 요청) at
the same time; as Google Sheets they are written cell by cell instead of being uploaded
whole. `convert_folder` makes a Google Sheet copy of every Excel file directly in the
folder, moves the original into "_원본 xlsx" (kept, not deleted) and marks it with the id
of its replacement. A Mac whose location set still points at the original follows that
mark the next time it opens the set (`follow_replaced`).
"""
from __future__ import annotations

from ...core.locations import FILE_ROLES, DEFAULT_NAMES, LocationSet, nfc
from .drive import FOLDER_MIME, GSHEET_MIME, sheet_name

REPLACED_KEY = "am_replaced_by"
BACKUP_FOLDER = "_원본 xlsx"
EXCEL = (".xlsx", ".xls", ".xlsm")


def excel_files(client, folder_id: str) -> list:
    return [f for f in client.list_folder(folder_id)
            if f.mime_type not in (FOLDER_MIME, GSHEET_MIME)
            and nfc(f.name).lower().endswith(EXCEL) and not f.name.startswith(("~$", "_", "."))]


def convert_folder(client, folder_id: str, location_set: LocationSet | None = None) -> list[tuple[str, str, str]]:
    """Convert every Excel file in folder_id. Returns [(name, old id, new id)].
    The location set (if given) is pointed at the new files."""
    done = []
    files = excel_files(client, folder_id)
    if not files:
        return done
    backup = client.subfolder(folder_id, BACKUP_FOLDER)
    for f in files:
        new = client.convert_copy(f.id, sheet_name(nfc(f.name)), folder_id)
        client.set_app_properties(f.id, {REPLACED_KEY: new.id})
        client.move(f.id, folder_id, backup)
        done.append((nfc(f.name), f.id, new.id))
        if location_set is not None:
            _repoint(location_set, f.id, new.id, new.name)
    return done


def _repoint(s: LocationSet, old: str, new: str, new_name: str) -> None:
    for role in FILE_ROLES:
        if s.get(role) == old:
            path = s.path(role)
            folder = path.rsplit("/", 1)[0] + "/" if "/" in path else ""
            s.set(role, new, folder + new_name)


def follow_replaced(client, s: LocationSet) -> bool:
    """Point roles that still name a converted Excel file at its Google Sheet, and find
    the 출퇴근 기록 sheet in the lab folder if the set does not know it. True if changed."""
    changed = False
    for role in FILE_ROLES:
        fid = s.get(role)
        if not fid or not s.path(role).lower().endswith(EXCEL):
            continue
        try:
            new = client.app_properties(fid).get(REPLACED_KEY)
            if new:
                rf = client.get(new)
                _repoint(s, fid, rf.id, rf.name)
                changed = True
        except Exception:  # noqa: BLE001  offline or no access: keep the old file
            continue
    if not s.get("attendance") and s.get("lab"):
        try:
            rf = client.find_by_name(s.get("lab"), DEFAULT_NAMES["attendance"])
        except Exception:  # noqa: BLE001
            rf = None
        if rf is not None and rf.mime_type == GSHEET_MIME:
            s.set("attendance", rf.id, (s.path("lab") or "").rstrip("/") + "/" + rf.name)
            changed = True
    return changed
