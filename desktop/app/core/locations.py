"""Data location sets: where each feature's Excel file (or folder) lives in Google Drive.

The Drive folder can be organised freely, e.g.

    lab-manager-test-asset/
      Assets/Common Asset/        <- inventory files (one file = one category)
      Assets/Personal Asset/      <- personal asset files
      Assets/Sequence Map/
      Lab Management/예산 입출 내역, 업무, 공지사항, 출퇴근 기록 (Google 시트), Asset Manager 설정.json
      Order History/              <- 공용주문 / 개인주문 records (read only)

A *location set* records, per role, the Drive id of that file or folder. It is found
once by scanning names (`detect`), checked by the user, then saved: on this Mac (the
launch picker lists the sets) and in the shared settings file (so others get it).
Files that do not exist yet (장비 예약, 주문 요청 …) are created in the set's
`lab` folder the first time something is saved.

An old-style set (`legacy`) is just one folder with everything in it, as before.
"""
from __future__ import annotations

import unicodedata
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date

# role -> (label, kind, default file name / folder name hints)
FOLDER, FILE = "folder", "file"
ROLES: dict[str, tuple[str, str]] = {
    "common": ("공용 재고", FOLDER),
    "personal": ("개인 자산", FOLDER),
    "sequence": ("Sequence Map", FOLDER),
    "order_history": ("주문 기록", FOLDER),
    "budget": ("예산 · 랩비", FILE),
    "work": ("업무", FILE),
    "notices": ("공지사항", FILE),
    "reservations": ("장비 예약", FILE),
    "order_requests": ("주문 요청", FILE),
    "attendance": ("출퇴근 기록", FILE),
    "overlay": ("앱 설정", FILE),
    "lab": ("새 파일을 만들 폴더", FOLDER),
}
FILE_ROLES = [r for r, (_, k) in ROLES.items() if k == FILE]
FOLDER_ROLES = [r for r, (_, k) in ROLES.items() if k == FOLDER]

DEFAULT_NAMES = {
    "budget": "예산 입출 내역.xlsx",
    "work": "업무.xlsx",
    "notices": "공지사항.xlsx",
    "reservations": "장비 예약.xlsx",
    "order_requests": "주문 요청.xlsx",
    "attendance": "출퇴근 기록",  # a Google Sheet (made by the app, written only by the web app)
    "overlay": "Asset Manager 설정.json",
}

# names that identify a role when scanning (lower-case, NFC; first match wins)
FOLDER_HINTS = {
    "common": ("common asset", "공용 재고", "공용 자산", "common"),
    "personal": ("personal asset", "개인 자산", "개인 품목", "personal"),
    "sequence": ("sequence map", "sequence", "시퀀스"),
    "order_history": ("order history", "주문 기록", "주문기록", "orders"),
    "lab": ("lab management", "연구실 관리", "랩 관리", "management"),
}
FILE_HINTS = {
    "budget": ("예산", "budget", "랩비"),
    "work": ("업무.xlsx",),
    "notices": ("공지사항.xlsx",),
    "reservations": ("장비 예약", "장비예약", "reservation"),
    "order_requests": ("주문 요청", "주문요청", "order request"),
    "attendance": ("출퇴근",),
    "overlay": ("asset manager 설정.json",),
}


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


@dataclass
class Location:
    id: str = ""
    path: str = ""   # shown to people: "Assets/Common Asset/" (relative to the root)

    def to_dict(self) -> dict:
        return {"id": self.id, "path": self.path}


@dataclass
class LocationSet:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = ""
    root_id: str = ""
    root_name: str = ""
    legacy: bool = False  # one folder holds everything (the old layout)
    created: str = field(default_factory=lambda: date.today().isoformat())
    created_by: str = ""
    locations: dict[str, Location] = field(default_factory=dict)

    def get(self, role: str) -> str:
        loc = self.locations.get(role)
        return loc.id if loc else ""

    def path(self, role: str) -> str:
        loc = self.locations.get(role)
        return loc.path if loc else ""

    def set(self, role: str, file_id: str, path: str = "") -> None:
        if file_id:
            self.locations[role] = Location(file_id, path)
        else:
            self.locations.pop(role, None)

    def lab_folder(self) -> str:
        """Where new shared files go: the lab folder, else the settings file's, else the root."""
        return self.get("lab") or self.root_id

    def to_dict(self) -> dict:
        d = {k: v for k, v in asdict(self).items() if k != "locations"}
        d["locations"] = {r: loc.to_dict() for r, loc in self.locations.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "LocationSet":
        s = cls(**{k: d[k] for k in ("id", "name", "root_id", "root_name", "legacy", "created", "created_by")
                   if k in d})
        for role, loc in (d.get("locations") or {}).items():
            if isinstance(loc, dict) and loc.get("id"):
                s.locations[role] = Location(loc["id"], loc.get("path", ""))
        return s

    @classmethod
    def legacy_folder(cls, folder_id: str, name: str = "") -> "LocationSet":
        return cls(id=f"legacy-{folder_id}", name=name or folder_id, root_id=folder_id, root_name=name, legacy=True)

    def summary(self) -> str:
        if self.legacy:
            return "폴더 하나에 모두"
        found = sum(1 for r in ROLES if self.get(r))
        return f"위치 {found}/{len(ROLES)}개"


# ---- finding the locations by name -------------------------------------------------------
def _match(name: str, hints: tuple[str, ...]) -> bool:
    n = nfc(name).lower()
    return any(h in n for h in hints)


def detect(client, root_id: str, max_depth: int = 3) -> LocationSet:
    """Scan the folder tree under root_id and fill every role whose name is found.
    `client` is a DriveClient (list_folder / folder)."""
    from ..integrations.gdrive.drive import FOLDER_MIME, GSHEET_MIME

    root = client.folder(root_id)
    s = LocationSet(name=nfc(root.name), root_id=root_id, root_name=nfc(root.name))
    stack = [(root_id, "", 0)]
    found_sheets_at_root = False
    while stack:
        fid, path, depth = stack.pop(0)
        for f in client.list_folder(fid):
            name = nfc(f.name)
            native = f.mime_type == GSHEET_MIME
            if native and not name.lower().endswith(".xlsx"):
                name_for_hints = name + ".xlsx"  # a Google Sheet "업무" stands for 업무.xlsx
            else:
                name_for_hints = name
            if name.startswith("_") or name.startswith("~$"):
                continue
            if f.mime_type == FOLDER_MIME:
                sub = f"{path}{name}/"
                for role, hints in FOLDER_HINTS.items():
                    if not s.get(role) and _match(name, hints):
                        s.set(role, f.id, sub)
                        break
                if depth + 1 < max_depth:
                    stack.append((f.id, sub, depth + 1))
                continue
            for role, hints in FILE_HINTS.items():
                if (not s.get(role) and _match(name_for_hints, hints)
                        and (role != "budget" or native or name.lower().endswith((".xlsx", ".xls")))):
                    s.set(role, f.id, f"{path}{name}")
                    if role in ("overlay", "notices", "work") and not s.get("lab"):
                        s.set("lab", fid, path or "/")
                    break
            else:
                if depth == 0 and (native or name.lower().endswith((".xlsx", ".xls", ".csv"))):
                    found_sheets_at_root = True
    if not s.get("common") and (found_sheets_at_root or not s.locations):
        # nothing laid out by feature: the old one-folder layout
        legacy = LocationSet.legacy_folder(root_id, s.name)
        legacy.locations = s.locations
        legacy.set("common", root_id, "/")
        return legacy
    if not s.get("lab"):
        s.set("lab", root_id, "/")
    return s


# ---- kept on this Mac ---------------------------------------------------------------------
def saved_sets() -> list[LocationSet]:
    from . import settings
    data = settings.load_settings()
    return [LocationSet.from_dict(d) for d in data.get("location_sets", [])]


def remember_set(s: LocationSet) -> None:
    """Save the set (first in the list = last opened, the favorite at the top)."""
    from . import settings
    data = settings.load_settings()
    sets = [d for d in data.get("location_sets", []) if d.get("id") != s.id]
    data["location_sets"] = [s.to_dict()] + sets
    data["last_location_set"] = s.id
    data["last_project"] = f"gdrive:{s.root_id}"
    save_folder_favorite(data, s)
    settings.save_settings(data)


def save_folder_favorite(data: dict, s: LocationSet) -> None:
    favs = [f for f in data.get("cloud_favorites", []) if f.get("folder_id") != s.root_id]
    data["cloud_favorites"] = [{"folder_id": s.root_id, "name": s.root_name or s.name}] + favs


def forget_set(set_id: str) -> None:
    from . import settings
    data = settings.load_settings()
    data["location_sets"] = [d for d in data.get("location_sets", []) if d.get("id") != set_id]
    if data.get("last_location_set") == set_id:
        data.pop("last_location_set")
    settings.save_settings(data)


def last_set() -> LocationSet | None:
    from . import settings
    data = settings.load_settings()
    sets = saved_sets()
    want = data.get("last_location_set")
    for s in sets:
        if s.id == want:
            return s
    if sets:
        return sets[0]
    folder = settings.last_cloud_folder()
    if folder:  # opened before location sets existed: the folder itself
        fav = next((f for f in settings.cloud_favorites() if f.get("folder_id") == folder), {})
        return LocationSet.legacy_folder(folder, fav.get("name", ""))
    return None


def merge_shared(local: list[LocationSet], shared: list[dict]) -> list[LocationSet]:
    """Sets saved in the shared settings file that this Mac does not know yet."""
    have = {s.id for s in local}
    return local + [LocationSet.from_dict(d) for d in shared or [] if d.get("id") not in have]
