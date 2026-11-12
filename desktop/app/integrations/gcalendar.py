"""Google Calendar for the 일정 tab: read, add, edit and delete events of one calendar.

Each event can carry who it is for (one member's email, or 'all') and a mail alert
N minutes before it starts. These are stored in the event's private extended
properties, so they travel with the event and every copy of the app sees them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .gdrive.drive import CALENDAR_SCOPE, DriveError, load_credentials

NOTIFY_CHOICES = [(0, "알림 없음"), (10, "10분 전"), (30, "30분 전"), (60, "1시간 전"),
                  (180, "3시간 전"), (1440, "하루 전"), (2880, "이틀 전")]


class CalendarError(DriveError):
    pass


@dataclass
class Event:
    id: str = ""
    title: str = ""
    start: datetime | date | None = None
    end: datetime | date | None = None
    all_day: bool = False
    location: str = ""
    description: str = ""
    assignee: str = ""        # member email, "all", or ""
    assignee_name: str = ""
    notify_min: int = 0       # 0 = no alert (default)
    notified: str = ""        # start time the alert was already sent for
    html_link: str = ""
    extra: dict = field(default_factory=dict)
    calendar_id: str = ""
    calendar_name: str = ""
    color: str = ""
    repeat: str = ""          # new events only: a REPEAT_CHOICES key ("" = never)
    recurring_id: str = ""    # set on one occurrence of a repeating event (edits change that one)

    @property
    def start_dt(self) -> datetime:
        """Start as an aware datetime (all-day events start at local midnight)."""
        s = self.start
        if isinstance(s, datetime):
            return s if s.tzinfo else s.astimezone()
        return datetime(s.year, s.month, s.day).astimezone()

    def alert_at(self) -> datetime | None:
        return self.start_dt - timedelta(minutes=self.notify_min) if self.notify_min else None


# 반복: Google Calendar recurrence rules for new events
REPEAT_CHOICES = [("", "안 함"), ("daily", "매일"), ("weekly", "매주"), ("biweekly", "격주"),
                  ("monthly", "매월"), ("yearly", "매년")]
_RRULE = {"daily": "RRULE:FREQ=DAILY", "weekly": "RRULE:FREQ=WEEKLY", "biweekly": "RRULE:FREQ=WEEKLY;INTERVAL=2",
          "monthly": "RRULE:FREQ=MONTHLY", "yearly": "RRULE:FREQ=YEARLY"}


def can_use(creds=None) -> bool:
    creds = creds or load_credentials()
    return bool(creds and CALENDAR_SCOPE in (creds.scopes or []))


def _service(creds=None):
    from googleapiclient.discovery import build
    creds = creds or load_credentials()
    if not can_use(creds):
        raise CalendarError("캘린더를 쓰려면 Google 계정으로 다시 로그인해 캘린더 권한을 허용해 주세요.")
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def _call(request):
    from googleapiclient.errors import HttpError
    try:
        return request.execute(num_retries=2)
    except HttpError as exc:
        status = getattr(exc.resp, "status", 0)
        text = str(exc)
        if status == 403 and ("has not been used" in text or "disabled" in text):
            raise CalendarError("Google Cloud 프로젝트에서 Google Calendar API가 꺼져 있습니다. Cloud Console → "
                                "API 및 서비스 → 라이브러리에서 ‘Google Calendar API’를 사용 설정해 주세요.") from exc
        if status == 404:
            raise CalendarError("캘린더를 찾을 수 없습니다. 캘린더가 로그인한 계정과 공유되어 있는지 확인해 주세요.") from exc
        if status == 403:
            raise CalendarError("이 캘린더를 수정할 권한이 없습니다. 캘린더 공유 설정에서 ‘일정 변경’ 권한을 주세요.") from exc
        raise CalendarError(f"Google Calendar 오류 ({status}): {exc}") from exc
    except OSError as exc:
        raise CalendarError(f"Google Calendar에 연결하지 못했습니다: {exc}") from exc


def list_calendars(creds=None) -> list[dict]:
    """[{'id', 'name', 'primary', 'writable'}] the signed-in account can see."""
    svc = _service(creds)
    out, token = [], None
    while True:
        resp = _call(svc.calendarList().list(pageToken=token))
        for c in resp.get("items", []):
            out.append({"id": c["id"], "name": c.get("summaryOverride") or c.get("summary", c["id"]),
                        "primary": bool(c.get("primary")),
                        "writable": c.get("accessRole") in ("owner", "writer"),
                        "color": c.get("backgroundColor", "#3949ab")})
        token = resp.get("nextPageToken")
        if not token:
            return out


def _parse_time(d: dict) -> tuple[datetime | date, bool]:
    if "dateTime" in d:
        return datetime.fromisoformat(d["dateTime"].replace("Z", "+00:00")), False
    return date.fromisoformat(d["date"]), True


def from_api(d: dict) -> Event:
    start, all_day = _parse_time(d.get("start", {}))
    end, _ = _parse_time(d.get("end", {})) if d.get("end") else (start, all_day)
    if all_day and isinstance(end, date):
        end = end - timedelta(days=1)  # Google's end date is exclusive
    priv = (d.get("extendedProperties") or {}).get("private") or {}
    try:
        notify = int(priv.get("am_notify", "0") or 0)
    except ValueError:
        notify = 0
    ev = Event(d["id"], d.get("summary", "(제목 없음)"), start, end, all_day, d.get("location", ""),
               d.get("description", ""), priv.get("am_assignee", ""), priv.get("am_assignee_name", ""),
               notify, priv.get("am_notified", ""), d.get("htmlLink", ""), priv)
    ev.recurring_id = d.get("recurringEventId", "")
    return ev


def to_api(ev: Event) -> dict:
    if ev.all_day:
        s = ev.start if not isinstance(ev.start, datetime) else ev.start.date()
        e = ev.end if not isinstance(ev.end, datetime) else ev.end.date()
        start, end = {"date": s.isoformat()}, {"date": ((e or s) + timedelta(days=1)).isoformat()}
    else:
        s = ev.start if ev.start.tzinfo else ev.start.astimezone()
        e = (ev.end or ev.start + timedelta(hours=1))
        e = e if e.tzinfo else e.astimezone()
        start, end = {"dateTime": s.isoformat()}, {"dateTime": e.isoformat()}
    priv = dict(ev.extra)
    priv.update({"am_assignee": ev.assignee, "am_assignee_name": ev.assignee_name,
                 "am_notify": str(ev.notify_min), "am_notified": ev.notified})
    body = {"summary": ev.title, "location": ev.location, "description": ev.description,
            "start": start, "end": end, "extendedProperties": {"private": priv}}
    if ev.repeat in _RRULE and not ev.id:
        body["recurrence"] = [_RRULE[ev.repeat]]
        if not ev.all_day:  # a repeating timed event needs its time zone
            tz = _local_tz_name()
            if tz:
                body["start"]["timeZone"] = body["end"]["timeZone"] = tz
    return body


def _local_tz_name() -> str:
    """IANA name of this computer's time zone (e.g. Asia/Seoul), or "" if unknown."""
    import os
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return os.environ.get("TZ", "")


def selected_calendars(setting: dict | None) -> list[dict]:
    """Calendars chosen in the app: [{'id', 'name', 'color', 'writable'}]. Accepts the older
    single-calendar form {'id': ..., 'name': ...}."""
    setting = setting or {}
    if setting.get("calendars"):
        return [c for c in setting["calendars"] if c.get("id")]
    if setting.get("id"):
        return [{"id": setting["id"], "name": setting.get("name", ""), "color": "#3949ab", "writable": True}]
    return []


def default_calendar(setting: dict | None) -> str:
    cals = selected_calendars(setting)
    d = (setting or {}).get("default")
    if d and any(c["id"] == d for c in cals):
        return d
    writable = [c for c in cals if c.get("writable", True)]
    return (writable or cals or [{"id": ""}])[0]["id"]


def list_all(calendars: list[dict], start: datetime, end: datetime, creds=None) -> tuple[list[Event], list[str]]:
    """Events of several calendars, merged by start time; plus error messages per calendar."""
    out, errors = [], []
    for c in calendars:
        try:
            evs = list_events(c["id"], start, end, creds)
        except CalendarError as exc:
            errors.append(f"{c.get('name') or c['id']}: {exc}")
            continue
        for ev in evs:
            ev.calendar_id, ev.calendar_name, ev.color = c["id"], c.get("name", ""), c.get("color", "")
        out += evs
    out.sort(key=lambda e: e.start_dt)
    return out, errors


def list_events(calendar_id: str, start: datetime, end: datetime, creds=None) -> list[Event]:
    svc = _service(creds)
    out, token = [], None
    tmin = (start if start.tzinfo else start.astimezone()).astimezone(timezone.utc).isoformat()
    tmax = (end if end.tzinfo else end.astimezone()).astimezone(timezone.utc).isoformat()
    while True:
        resp = _call(svc.events().list(calendarId=calendar_id, timeMin=tmin, timeMax=tmax, singleEvents=True,
                                       orderBy="startTime", maxResults=250, pageToken=token))
        out += [from_api(d) for d in resp.get("items", []) if d.get("status") != "cancelled"]
        token = resp.get("nextPageToken")
        if not token:
            return out


def save_event(calendar_id: str, ev: Event, creds=None, from_calendar: str = "") -> Event:
    """Insert or update. With from_calendar set to another calendar, the event is moved there first."""
    svc = _service(creds)
    body = to_api(ev)
    if ev.id and from_calendar and from_calendar != calendar_id:
        _call(svc.events().move(calendarId=from_calendar, eventId=ev.id, destination=calendar_id))
    if ev.id:
        d = _call(svc.events().patch(calendarId=calendar_id, eventId=ev.id, body=body))
    else:
        d = _call(svc.events().insert(calendarId=calendar_id, body=body))
    return from_api(d)


def mark_notified(calendar_id: str, ev: Event, creds=None) -> None:
    svc = _service(creds)
    _call(svc.events().patch(calendarId=calendar_id, eventId=ev.id,
                             body={"extendedProperties": {"private": {"am_notified": ev.notified}}}))


def delete_event(calendar_id: str, event_id: str, creds=None) -> None:
    _call(_service(creds).events().delete(calendarId=calendar_id, eventId=event_id))
