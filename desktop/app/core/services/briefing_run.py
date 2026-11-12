"""The scheduled 운영 브리핑 (per-person operations mail), run without a window:

    "Asset Manager" --briefing [--force]

macOS launchd starts it at the chosen time (see integrations.schedule_mac). It
reads the last Google Drive folder fresh (its own cache, so it never touches an
open app's session), applies the order records and mails every person in charge.
The send date is kept in the folder's settings file so a second Mac with the
same schedule does not send it again.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

from .. import settings

log = logging.getLogger("briefing")


def _logger() -> None:
    path = settings.user_data_dir() / "briefing.log"
    logging.basicConfig(filename=str(path), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")


def due(cfg: dict, now: datetime) -> bool:
    if not cfg.get("enabled"):
        return False
    days = cfg.get("weekdays", list(range(5)))
    if now.weekday() not in days:
        return False
    return cfg.get("last_sent") != now.date().isoformat()


def open_folder_readonly(location, client=None):
    """(model, project, sources) for a fresh read of the location set (or one folder id).
    sources holds the boards the 운영 브리핑 reads: reservations, work, order_requests, events."""
    from ...integrations.gdrive.drive import DriveClient
    from ...integrations.gdrive.folder_remote import DriveFolderRemote
    from ...integrations.gdrive.set_remote import LocationSetRemote, RoleFileRemote
    from ..folder_model import FolderModel
    from ..locations import LocationSet
    from ..order_requests import OrderRequestBoard
    from ..orders import apply_orders, load_order_folder
    from ..reservations import ReservationBoard
    from ..work import WorkBoard

    client = client or DriveClient()
    s = location if isinstance(location, LocationSet) else LocationSet.legacy_folder(location, "")
    key = s.root_id if s.legacy else s.id
    cache = settings.user_data_dir() / "cloud" / f"{key}-briefing"
    remote = DriveFolderRemote(client, s.root_id) if s.legacy else LocationSetRemote(client, s)
    model = FolderModel(remote, cache)
    model.load()
    project = model.build(cache / "session.labproj", s.root_id)
    project.observer = None  # read only: nothing goes back to the files
    order_id = s.get("order_history") or model.overlay.get("orders", {}).get("folder_id")
    if order_id:
        try:
            apply_orders(project, load_order_folder(DriveFolderRemote(client, order_id), cache / "orders"))
        except Exception as exc:  # noqa: BLE001
            log.warning("order records not read: %s", exc)
    sources: dict = {}
    for name, cls, role in (("reservations", ReservationBoard, "reservations"),
                            ("order_requests", OrderRequestBoard, "order_requests")):
        fid = s.get(role) if not s.legacy else model.feature_ids.get(role, "")
        if not fid:
            continue
        rem = remote if s.legacy else RoleFileRemote(client, s, role, lambda *_: None)
        try:
            board = cls(rem, cache, fid)
            board.load()
            sources[name] = board
        except Exception as exc:  # noqa: BLE001
            log.warning("%s not read: %s", name, exc)
    if model.work_id:
        try:
            work = WorkBoard(remote, cache, model.work_id)
            work.load()
            sources["work"] = work
        except Exception as exc:  # noqa: BLE001
            log.warning("업무 not read: %s", exc)
    sources["events"] = today_events(project)
    return model, project, sources


def today_events(project, today: date | None = None) -> list:
    """Today's events of the chosen Google calendars (empty without calendar access)."""
    try:
        from datetime import time, timedelta
        from ...integrations import gcalendar
        if not gcalendar.can_use():
            return []
        cals = gcalendar.selected_calendars(project.setting("calendar"))
        if not cals:
            return []
        d = today or date.today()
        start = datetime.combine(d, time()).astimezone()
        events, _errors = gcalendar.list_all(cals, start, start + timedelta(days=1))
        return events
    except Exception as exc:  # noqa: BLE001
        log.warning("calendar not read: %s", exc)
        return []


def send_briefings(project, only: str | None = None, today: date | None = None, **sources) -> list[str]:
    """Mail each member their own 운영 브리핑 (or only one address). Returns the addresses mailed."""
    from ...integrations import gmail
    from .ops_briefing import all_mails

    sent = []
    for to, subject, html, text in all_mails(project, today=today, only=only, **sources):
        gmail.send(to, subject, html, text)
        sent.append(to[0])
    return sent


def run(force: bool = False) -> int:
    _logger()
    from ..locations import last_set
    location = last_set()
    if location is None:
        log.info("no Google Drive location opened yet")
        return 0
    try:
        model, project, sources = open_folder_readonly(location)
        cfg = dict(model.overlay.get("briefing") or {})
        now = datetime.now()
        if not force and not due(cfg, now):
            log.info("not due (%s)", cfg)
            return 0
        sent = send_briefings(project, **sources)
        log.info("briefing sent to %s", ", ".join(sent) or "(nobody had anything today)")
        if not force:
            model._set_overlay(("briefing", "last_sent"), now.date().isoformat())
            model.flush()
        project.close()
        return 0
    except Exception as exc:  # noqa: BLE001
        log.exception("briefing failed: %s", exc)
        return 1


def log_path() -> Path:
    return settings.user_data_dir() / "briefing.log"
