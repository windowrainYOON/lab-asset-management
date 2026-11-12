"""출퇴근 기록: clock-in times kept in a Google Sheet, read (never written) by the app.

The sheet ("출퇴근 기록" in the lab folder) has

- one tab per month named "YYYY-MM": row 1 = 날짜 | 요일 | <member> | <member> …,
  then one row per date; a cell holds the clock-in time ("09:02") or a word such as
  "휴가" / "출장". Records are written only by the future web app.
- a "설정" tab: key | value rows — 출근 시간, 지각 허용(분), 근무 요일. The app and the
  web app read the same rule; the app's settings dialog writes these cells.

A day counts only if it is a work day (근무 요일), not a Korean public holiday and not
covered by a calendar event marked "출퇴근 기록에서 제외". Late = clock-in after
출근 시간 + 지각 허용.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

SHEET_NAME = "출퇴근 기록"
SETTINGS_TAB = "설정"
KEY_START, KEY_GRACE, KEY_DAYS = "출근 시간", "지각 허용(분)", "근무 요일"
WEEKDAYS = "월화수목금토일"  # Monday first, like date.weekday()
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
EXCLUDE_KEY = "am_attendance_exclude"   # private extended property on a calendar event
# Google's public calendars of Korean holidays: official public holidays only, else all
HOLIDAY_CALENDARS = ("ko.south_korea.official#holiday@group.v.calendar.google.com",
                     "ko.south_korea#holiday@group.v.calendar.google.com")


@dataclass
class AttendanceSettings:
    start: time = time(9, 0)
    grace: int = 10                       # minutes
    workdays: frozenset = frozenset(range(5))  # 0 = Monday

    @property
    def deadline(self) -> time:
        t = datetime.combine(date.today(), self.start) + timedelta(minutes=self.grace)
        return t.time()

    def is_late(self, t: time) -> bool:
        return (t.hour, t.minute) > (self.deadline.hour, self.deadline.minute)

    def late_by(self, t: time) -> int:
        """Minutes after 출근 시간 (not after the tolerance)."""
        return max(0, (t.hour * 60 + t.minute) - (self.start.hour * 60 + self.start.minute))

    def days_text(self) -> str:
        return ",".join(WEEKDAYS[d] for d in sorted(self.workdays))

    def rows(self) -> list[list[Any]]:
        return [["항목", "값"], [KEY_START, self.start.strftime("%H:%M")], [KEY_GRACE, self.grace],
                [KEY_DAYS, self.days_text()]]


@dataclass
class Month:
    key: str                                   # "2026-11"
    people: list[str] = field(default_factory=list)
    days: dict[date, dict[str, Any]] = field(default_factory=dict)  # value: time or text

    @property
    def year(self) -> int:
        return int(self.key[:4])

    @property
    def month(self) -> int:
        return int(self.key[5:7])

    def dates(self) -> list[date]:
        """Every date of the month (whether the sheet has a row or not)."""
        d = date(self.year, self.month, 1)
        out = []
        while d.month == self.month:
            out.append(d)
            d += timedelta(days=1)
        return out

    def get(self, day: date, person: str) -> Any:
        return self.days.get(day, {}).get(person)


@dataclass
class Book:
    settings: AttendanceSettings = field(default_factory=AttendanceSettings)
    months: dict[str, Month] = field(default_factory=dict)
    has_settings_tab: bool = False

    def month(self, key: str) -> Month | None:
        return self.months.get(key)


# ---- reading cells ---------------------------------------------------------------------
def parse_time(v: Any) -> time | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.time().replace(second=0, microsecond=0)
    if isinstance(v, time):
        return v.replace(second=0, microsecond=0)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        frac = float(v) % 1
        mins = round(frac * 24 * 60)
        if 0 < mins < 24 * 60 or v == 0:
            return time(mins // 60, mins % 60)
        return None
    m = re.match(r"^\s*(\d{1,2})\s*[:시]\s*(\d{1,2})", str(v))
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        return time(int(m.group(1)), int(m.group(2)))
    return None


def parse_cell(v: Any) -> Any:
    """A clock-in time, a word (휴가 / 출장 …), or None."""
    t = parse_time(v)
    if t is not None:
        return t
    text = str(v).strip() if v is not None else ""
    return text or None


def parse_date(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool) and 20000 < v < 80000:
        return date(1899, 12, 30) + timedelta(days=int(v))
    m = re.match(r"^\s*(\d{4})[-./]\s*(\d{1,2})[-./]\s*(\d{1,2})", str(v or ""))
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def parse_settings(rows: list[list[Any]]) -> AttendanceSettings:
    s = AttendanceSettings()
    for row in rows:
        if not row or row[0] is None:
            continue
        key = str(row[0]).strip()
        val = row[1] if len(row) > 1 else None
        if key == KEY_START:
            s.start = parse_time(val) or s.start
        elif key.startswith("지각 허용"):
            try:
                s.grace = max(0, int(float(str(val).replace("분", "").strip())))
            except (TypeError, ValueError):
                pass
        elif key == KEY_DAYS and val:
            days = frozenset(WEEKDAYS.index(ch) for ch in str(val) if ch in WEEKDAYS)
            if days:
                s.workdays = days
    return s


def parse_month(key: str, rows: list[list[Any]]) -> Month:
    m = Month(key)
    header_at = next((i for i, r in enumerate(rows[:10]) if r and str(r[0] or "").strip() == "날짜"), None)
    if header_at is None:
        return m
    header = rows[header_at]
    cols = []  # (column, person)
    for c, h in enumerate(header[1:], start=1):
        name = str(h).strip() if h is not None else ""
        if name and name != "요일":
            cols.append((c, name))
    m.people = [p for _, p in cols]
    for r in rows[header_at + 1:]:
        if not r:
            continue
        d = parse_date(r[0])
        if d is None:
            continue
        vals = {}
        for c, p in cols:
            v = parse_cell(r[c] if c < len(r) else None)
            if v is not None:
                vals[p] = v
        m.days[d] = vals
    return m


def read_book(path: Path) -> Book:
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True)
    book = Book()
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        title = ws.title.strip()
        if title == SETTINGS_TAB:
            book.settings = parse_settings(rows)
            book.has_settings_tab = True
        elif MONTH_RE.match(title):
            book.months[title] = parse_month(title, rows)
    return book


# ---- what counts ----------------------------------------------------------------------
def month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


@dataclass
class DayInfo:
    workday: bool
    reason: str = ""   # why it does not count: "주말", "개천절", "연구실 워크숍" …
    kind: str = ""     # "weekend" | "holiday" | "event" | ""


def day_info(d: date, settings: AttendanceSettings, holidays: dict[date, str],
             excluded: dict[date, str]) -> DayInfo:
    if d in holidays:
        return DayInfo(False, holidays[d], "holiday")
    if d in excluded:
        return DayInfo(False, excluded[d], "event")
    if d.weekday() not in settings.workdays:
        return DayInfo(False, "주말" if d.weekday() >= 5 else "근무 요일 아님", "weekend")
    return DayInfo(True)


def status(value: Any, settings: AttendanceSettings) -> str:
    """'late' | 'ontime' | 'text' | 'none'."""
    if isinstance(value, time):
        return "late" if settings.is_late(value) else "ontime"
    if value:
        return "text"
    return "none"


@dataclass
class PersonStats:
    workdays: int = 0
    present: int = 0
    late: int = 0
    ontime: int = 0
    avg_in: time | None = None
    late_days: list[tuple[date, time]] = field(default_factory=list)


def person_stats(m: Month, person: str, settings: AttendanceSettings, holidays: dict, excluded: dict,
                 today: date) -> PersonStats:
    st = PersonStats()
    mins = []
    for d in m.dates():
        if d > today or not day_info(d, settings, holidays, excluded).workday:
            continue
        st.workdays += 1
        v = m.get(d, person)
        s = status(v, settings)
        if s in ("late", "ontime"):
            st.present += 1
            mins.append(v.hour * 60 + v.minute)
            if s == "late":
                st.late += 1
                st.late_days.append((d, v))
            else:
                st.ontime += 1
    if mins:
        avg = round(sum(mins) / len(mins))
        st.avg_in = time(avg // 60, avg % 60)
    return st


@dataclass
class DaySummary:
    present: list[str] = field(default_factory=list)
    late: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)   # no record yet (and no word like 휴가)
    away: list[str] = field(default_factory=list)      # 휴가 / 출장 …


def day_summary(m: Month, d: date, settings: AttendanceSettings) -> DaySummary:
    out = DaySummary()
    for p in m.people:
        v = m.get(d, p)
        s = status(v, settings)
        if s in ("late", "ontime"):
            out.present.append(p)
            if s == "late":
                out.late.append(p)
        elif s == "text":
            out.away.append(p)
        else:
            out.missing.append(p)
    return out


def month_late_total(m: Month, settings: AttendanceSettings, holidays: dict, excluded: dict, today: date) -> int:
    return sum(person_stats(m, p, settings, holidays, excluded, today).late for p in m.people)


# ---- calendar: holidays and excluded events -------------------------------------------
def holidays_from_events(events) -> dict[date, str]:
    """date -> holiday name, from the Korean holiday calendar's events."""
    out: dict[date, str] = {}
    for ev in events:
        desc = (getattr(ev, "description", "") or "").lower()
        if "기념일" in desc or "observance" in desc:
            continue  # 어버이날, 스승의 날 …: not days off
        for d in _event_days(ev):
            out.setdefault(d, ev.title)
    return out


def excluded_from_events(events) -> dict[date, str]:
    """date -> event title, for events marked 출퇴근 기록에서 제외 (every day they cover)."""
    out: dict[date, str] = {}
    for ev in events:
        if str((getattr(ev, "extra", None) or {}).get(EXCLUDE_KEY, "")) == "1":
            for d in _event_days(ev):
                out.setdefault(d, ev.title)
    return out


def _event_days(ev) -> list[date]:
    s, e = ev.start, ev.end or ev.start
    if isinstance(s, datetime):
        s = s.astimezone().date() if s.tzinfo else s.date()
    if isinstance(e, datetime):
        e2 = e.astimezone() if e.tzinfo else e
        # a timed event ending exactly at midnight does not cover the next day
        e = (e2 - timedelta(microseconds=1)).date() if e2.time() == time(0) and e2.date() > s else e2.date()
    out, d = [], s
    while d <= e and len(out) < 400:
        out.append(d)
        d += timedelta(days=1)
    return out


def load_calendar_days(calendar_setting: dict | None, start: date, end: date) -> tuple[dict, dict, list[str]]:
    """(holidays, excluded, errors) for start..end (inclusive), read from Google Calendar."""
    from ..integrations import gcalendar

    t0 = datetime(start.year, start.month, start.day)
    t1 = datetime(end.year, end.month, end.day) + timedelta(days=1)
    errors: list[str] = []
    holidays: dict = {}
    for cal in HOLIDAY_CALENDARS:
        try:
            holidays = holidays_from_events(gcalendar.list_events(cal, t0, t1))
            break
        except Exception as exc:  # noqa: BLE001  try the next form, then go on without
            err = str(exc)
    else:
        errors.append(f"대한민국 공휴일: {err}")
    events, errs = gcalendar.list_all(gcalendar.selected_calendars(calendar_setting), t0, t1)
    errors += errs
    return holidays, excluded_from_events(events), errors


# ---- the sheet -------------------------------------------------------------------------
def month_rows(key: str, people: list[str]) -> list[list[Any]]:
    m = Month(key)
    rows: list[list[Any]] = [["날짜", "요일"] + list(people)]
    for d in m.dates():
        rows.append([d.isoformat(), WEEKDAYS[d.weekday()]] + [None] * len(people))
    return rows


def write_new_book(path: Path, people: list[str], months: list[str],
                   settings: AttendanceSettings | None = None) -> None:
    """The workbook a new 출퇴근 기록 sheet is made from (uploaded as a Google Sheet)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    wb.remove(wb.active)
    bold = Font(bold=True)
    for key in months:
        ws = wb.create_sheet(key)
        for row in month_rows(key, people):
            ws.append(row)
        for c in ws[1]:
            c.font = bold
        ws.freeze_panes = "C2"
        ws.column_dimensions["A"].width = 12
        ws.column_dimensions["B"].width = 6
    ws = wb.create_sheet(SETTINGS_TAB)
    for row in (settings or AttendanceSettings()).rows():
        ws.append(row)
    for c in ws[1]:
        c.font = bold
    ws.column_dimensions["A"].width = 16
    ws.column_dimensions["B"].width = 16
    wb.save(path)


def settings_requests(meta: dict, settings: AttendanceSettings) -> list[dict]:
    """Sheets batchUpdate requests that write the 설정 tab (adding it if missing).
    Times and numbers are written as text the web app can read the same way."""
    from .gsheet_ops import SheetMeta, typed_cell

    reqs: list[dict] = []
    m = meta.get(SETTINGS_TAB)
    if m is None:
        sid = 900_000_000 + len(meta)
        while any(x.sheet_id == sid for x in meta.values()):
            sid += 1
        reqs.append({"addSheet": {"properties": {"sheetId": sid, "title": SETTINGS_TAB,
                                                 "gridProperties": {"rowCount": 20, "columnCount": 4}}}})
        m = SheetMeta(sid, 20, 4)
    rows = settings.rows()
    reqs.append({"updateCells": {
        "start": {"sheetId": m.sheet_id, "rowIndex": 0, "columnIndex": 0},
        "rows": [{"values": [typed_cell(v) for v in r]} for r in rows],
        "fields": "userEnteredValue"}})
    return reqs


class AttendanceStore:
    """The 출퇴근 기록 sheet for the app: read on open and whenever its Drive revision
    moves (every sync); settings are written straight to its 설정 tab."""

    def __init__(self, remote, cache_dir: Path, file_id: str = ""):
        self.remote = remote
        self.path = Path(cache_dir) / "attendance.xlsx"
        self.file_id = file_id
        self.rev = ""
        self.book = Book()
        self.error = ""

    def load(self) -> Book:
        if not self.file_id or self.remote is None:
            return self.book
        rev = self.remote.revision(self.file_id)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.remote.download(self.file_id, self.path)
        self.book, self.rev = read_book(self.path), rev
        return self.book

    # board protocol (FolderSession syncs every board)
    def dirty(self) -> bool:
        return False

    def sync(self) -> bool:
        if not self.file_id or self.remote is None:
            return False
        if self.remote.revision(self.file_id) == self.rev:
            return False
        self.load()
        return True

    def save_settings(self, settings: AttendanceSettings) -> None:
        if not self.file_id:
            raise RuntimeError("출퇴근 기록 시트가 아직 없습니다.")
        reqs = settings_requests(self.remote.sheet_meta(self.file_id), settings)
        self.remote.sheet_batch(self.file_id, reqs)
        self.book.settings = settings
        self.book.has_settings_tab = True
        self.rev = self.remote.revision(self.file_id)


def create_sheet(client, folder_id: str, people: list[str], months: list[str], tmp_dir: Path):
    """Make the 출퇴근 기록 Google Sheet in folder_id. Returns its RemoteFile."""
    tmp = Path(tmp_dir) / "출퇴근 기록.new.xlsx"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    write_new_book(tmp, people, months)
    try:
        return client.upload_new(folder_id, SHEET_NAME + ".xlsx", tmp, as_sheet=True)
    finally:
        tmp.unlink(missing_ok=True)
