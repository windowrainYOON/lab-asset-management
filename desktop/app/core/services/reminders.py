"""Event alert mails, run every few minutes by launchd:  "Asset Manager" --reminders

Reads only the folder's settings file (calendar id and members), looks at the
calendar's next two days and mails the person (or everyone) an event is assigned
to once its alert time has come. The send is recorded on the event itself, so
it goes out once even if several Macs run this.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from .. import settings
from ..folder_model import OVERLAY_NAME, nfc

log = logging.getLogger("reminders")
WEEKDAY = "월화수목금토일"


def recipients(assignee: str, members: list[dict]) -> list[str]:
    if assignee == "all":
        return [m["email"] for m in members if m.get("email")]
    return [assignee] if assignee and "@" in assignee else []


def due_alerts(events, now: datetime) -> list:
    """Events whose alert time has passed, that have not started and were not mailed yet."""
    out = []
    for ev in events:
        at = ev.alert_at()
        if at is None or not ev.assignee:
            continue
        start = ev.start_dt
        if at <= now < start and ev.notified != start.isoformat():
            out.append(ev)
    return out


def _when(ev) -> str:
    s = ev.start_dt
    if ev.all_day:
        return f"{s:%Y-%m-%d}({WEEKDAY[s.weekday()]}) 종일"
    return f"{s:%Y-%m-%d}({WEEKDAY[s.weekday()]}) {s:%H:%M}"


def alert_mail(ev) -> tuple[str, str, str]:
    import html as h
    when = _when(ev)
    subject = f"[Asset Manager 일정] {ev.title} · {when}"
    rows = [("일정", ev.title), ("시간", when), ("장소", ev.location), ("담당", ev.assignee_name or
            ("전체" if ev.assignee == "all" else ev.assignee)), ("내용", ev.description)]
    body = "".join(f'<tr><td style="padding:4px 12px 4px 0;color:#5f6b7a">{k}</td><td>{h.escape(v)}</td></tr>'
                   for k, v in rows if v)
    html = (f'<div style="font-family:-apple-system,sans-serif"><h3>곧 시작하는 일정</h3><table>{body}</table>'
            + (f'<p><a href="{h.escape(ev.html_link)}">Google 캘린더에서 보기</a></p>' if ev.html_link else "")
            + '<p style="color:#8a93a6;font-size:12px">Asset Manager 일정 알림</p></div>')
    text = "\n".join(f"{k}: {v}" for k, v in rows if v)
    return subject, html, text


def read_overlay(client, folder_id: str) -> dict:
    from ...integrations.gdrive.folder_remote import DriveFolderRemote
    remote = DriveFolderRemote(client, folder_id)
    for rf in client.list_folder(folder_id):
        if nfc(rf.name) == OVERLAY_NAME:
            dest = settings.user_data_dir() / "reminders-overlay.json"
            remote.download(rf.id, dest)
            return json.loads(dest.read_text(encoding="utf-8"))
    return {}


def run() -> int:
    logging.basicConfig(filename=str(settings.user_data_dir() / "reminders.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    from ...integrations import gcalendar, gmail
    from ...integrations.gdrive.drive import DriveClient

    folder_id = settings.last_cloud_folder()
    if not folder_id:
        return 0
    try:
        overlay = read_overlay(DriveClient(), folder_id)
        cals = gcalendar.selected_calendars(overlay.get("calendar"))
        if not cals:
            return 0
        now = datetime.now().astimezone()
        events, errors = gcalendar.list_all(cals, now - timedelta(hours=1), now + timedelta(days=3))
        for e in errors:
            log.warning("calendar not read: %s", e)
        members = overlay.get("members", [])
        for ev in due_alerts(events, now):
            to = recipients(ev.assignee, members)
            if not to:
                continue
            gmail.send(to, *alert_mail(ev))
            ev.notified = ev.start_dt.isoformat()
            gcalendar.mark_notified(ev.calendar_id, ev)
            log.info("alert for %s sent to %s", ev.title, ", ".join(to))
        return 0
    except Exception as exc:  # noqa: BLE001
        log.exception("reminders failed: %s", exc)
        return 1
