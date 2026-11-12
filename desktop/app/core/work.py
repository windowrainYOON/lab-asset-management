"""Work (업무): projects with people in charge, kept in one Excel file in the Drive folder.

Two kinds of project:
- milestone (마일스톤): named steps, each with a date and one person in charge.
- rotation (당번제): a duty that comes back on a schedule (매일, 평일, 격일, 매주, 격주, 매달);
  the people take turns in the project's order, one or more at a time (회차 인원: two
  people per turn take the next two names, and so on). Filling starts from a chosen first person
  and date and continues in order; re-sorting or deleting works from a date onwards.

The current task of a project is its first one not done (by date); the next is the one
after it. Marking the current one done moves the highlight on.

The file (업무.xlsx) is an ordinary workbook people can also open in Excel:
  프로젝트: ID | 이름 | 종류 | 담당자 | 주기 | 시작일 | 개수 | 업무 이름 | 메모 | 회차 인원
  업무:     ID | 프로젝트 ID | 프로젝트 | 날짜 | 담당자 (several: comma separated) | 업무 | 완료 | 완료 시각
It is not shown as an inventory category.
"""
from __future__ import annotations

import calendar
import copy
import re
import uuid
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path

from .importer import clean_text

WORK_NAME = "업무.xlsx"
KINDS = {"rotation": "당번제", "milestone": "마일스톤"}
FREQS = {"daily": "매일", "weekdays": "평일마다", "alternate": "격일", "weekly": "매주",
         "biweekly": "격주", "monthly": "매달"}
PROJECT_HEADERS = ["ID", "이름", "종류", "담당자", "주기", "시작일", "기간", "업무 이름", "메모", "회차 인원"]
TASK_HEADERS = ["ID", "프로젝트 ID", "프로젝트", "날짜", "담당자", "업무", "완료", "완료 시각"]
HALVES = {1: "상반기", 2: "하반기"}
TRUE = ("o", "y", "yes", "true", "1", "완료", "✓", "✔")


def _id() -> str:
    return uuid.uuid4().hex[:8]


@dataclass
class WorkProject:
    id: str = field(default_factory=_id)
    name: str = ""
    kind: str = "rotation"
    members: list[str] = field(default_factory=list)  # rotation order
    freq: str = "weekly"
    start: str = field(default_factory=lambda: date.today().isoformat())
    until: str = ""  # rotation: fill up to the end of this half, "2026-2" (연도-반기); "" = start's half
    task_title: str = ""  # rotation: the duty's name ("청소"); empty = the project name
    note: str = ""
    per_turn: int = 1  # rotation: how many people share one turn

    @property
    def kind_label(self) -> str:
        return KINDS.get(self.kind, self.kind)

    @property
    def freq_label(self) -> str:
        return FREQS.get(self.freq, self.freq)

    def duty(self) -> str:
        return self.task_title or self.name

    @property
    def until_half(self) -> tuple[int, int]:
        """(year, half) the rotation is laid out to; the start's own half when unset."""
        return parse_half(self.until) or half_of(_iso(self.start) or date.today())

    @property
    def period_label(self) -> str:
        y, h = self.until_half
        return half_label(y, h)


@dataclass
class WorkTask:
    id: str = field(default_factory=_id)
    project_id: str = ""
    date: str = ""  # ISO yyyy-mm-dd
    assignee: str = ""
    title: str = ""
    done: bool = False
    done_at: str = ""

    @property
    def people(self) -> list[str]:
        return split_people(self.assignee)

    @property
    def day(self) -> date | None:
        try:
            return date.fromisoformat(self.date[:10])
        except ValueError:
            return None


def split_people(text: str) -> list[str]:
    out = []
    for n in (text or "").replace("\n", ",").split(","):
        n = n.strip()
        if n and n not in out:
            out.append(n)
    return out


def join_people(names) -> str:
    return ", ".join(split_people(", ".join(names)))


# ---- schedule arithmetic ----------------------------------------------------------
def _iso(text: str) -> date | None:
    try:
        return date.fromisoformat((text or "")[:10])
    except ValueError:
        return None


def half_of(d: date) -> tuple[int, int]:
    return d.year, 1 if d.month <= 6 else 2


def half_start(year: int, half: int) -> date:
    return date(year, 1 if half == 1 else 7, 1)


def half_end(year: int, half: int) -> date:
    return date(year, 6, 30) if half == 1 else date(year, 12, 31)


def half_label(year: int, half: int) -> str:
    return f"{year}년 {HALVES.get(half, '')}"


def parse_half(text: str) -> tuple[int, int] | None:
    """"2026-2", "2026 하반기", "2026년 상반기" → (2026, 2)."""
    m = re.match(r"\s*(\d{4})\D*?(상반기|하반기|[12])?\s*$", text or "")
    if not m:
        return None
    h = m.group(2) or "1"
    return int(m.group(1)), 2 if h in ("2", "하반기") else 1


def format_half(year: int, half: int) -> str:
    return f"{year}-{half}"


def _add_month(d: date, n: int = 1) -> date:
    m = d.month - 1 + n
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def first_on_or_after(d: date, freq: str) -> date:
    """Weekday duties never fall on a weekend."""
    if freq == "weekdays":
        while d.weekday() >= 5:
            d += timedelta(days=1)
    return d


def next_date(d: date, freq: str, anchor: date | None = None) -> date:
    if freq == "daily":
        return d + timedelta(days=1)
    if freq == "weekdays":
        return first_on_or_after(d + timedelta(days=1), freq)
    if freq == "alternate":
        return d + timedelta(days=2)
    if freq == "weekly":
        return d + timedelta(weeks=1)
    if freq == "biweekly":
        return d + timedelta(weeks=2)
    if freq == "monthly":  # keep the anchor's day of month (31 → 30 → 31)
        a = anchor or d
        months = (d.year - a.year) * 12 + d.month - a.month + 1
        return _add_month(a, months)
    raise ValueError(freq)


def dates(start: date, freq: str, count: int) -> list[date]:
    out, d = [], first_on_or_after(start, freq)
    for _ in range(max(0, count)):
        out.append(d)
        d = next_date(d, freq, anchor=start)
    return out


def dates_until(start: date, freq: str, end: date) -> list[date]:
    """Every turn from `start` through `end` (inclusive)."""
    out, d = [], first_on_or_after(start, freq)
    while d <= end:
        out.append(d)
        d = next_date(d, freq, anchor=start)
    return out


def _order_from(members: list[str], first: str) -> list[str]:
    """The rotation starting with `first` (a name not in the list just goes first)."""
    if not members:
        return [first] if first else []
    if first in members:
        i = members.index(first)
        return members[i:] + members[:i]
    return ([first] if first else []) + members


# ---- edits (pure: lists in, new lists out) ----------------------------------------
def of_project(tasks: list[WorkTask], project_id: str) -> list[WorkTask]:
    return sorted((t for t in tasks if t.project_id == project_id), key=lambda t: (t.date, t.id))


def fill_rotation(project: WorkProject, tasks: list[WorkTask], first: str, start: date,
                  until: date | None = None, count: int | None = None) -> list[WorkTask]:
    """Rotation: drop this project's tasks not done from `start` on and lay out new ones
    from `start` through `until` (default: the end of the project's period, 연도 + 반기),
    or exactly `count` of them, in turn from `first` in the project's order. Done ones stay."""
    if count is not None:
        days = dates(start, project.freq, count)
    else:
        days = dates_until(start, project.freq, until or half_end(*project.until_half))
    keep = [t for t in tasks if t.project_id != project.id
            or t.done or (t.day is not None and t.day < start)]
    order = _order_from(project.members, first)
    k = max(1, min(project.per_turn, len(order) or 1))
    new = [WorkTask(project_id=project.id, date=d.isoformat(),
                    assignee=join_people(order[(i * k + j) % len(order)] for j in range(k)) if order else "",
                    title=project.duty())
           for i, d in enumerate(days)]
    return keep + new


def resort_from(project: WorkProject, tasks: list[WorkTask], start: date) -> list[WorkTask]:
    """Re-lay the open tasks from `start` on: same number of tasks, dates again by the
    frequency from the first one's date, people again in order from its person."""
    open_ = [t for t in of_project(tasks, project.id) if not t.done and t.day and t.day >= start]
    if not open_:
        return tasks
    first = open_[0]
    lead = first.people[0] if first.people else ""
    return fill_rotation(project, tasks, lead, first.day, count=len(open_))


def delete_from(project_id: str, tasks: list[WorkTask], start: date) -> list[WorkTask]:
    """Remove the project's open tasks from `start` on (done ones are kept as a record)."""
    return [t for t in tasks if t.project_id != project_id or t.done or (t.day and t.day < start)]


def current_next(tasks: list[WorkTask], project_id: str) -> tuple[WorkTask | None, WorkTask | None]:
    open_ = [t for t in of_project(tasks, project_id) if not t.done]
    return (open_[0] if open_ else None), (open_[1] if len(open_) > 1 else None)


def set_done(tasks: list[WorkTask], task_id: str, done: bool = True) -> list[WorkTask]:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M") if done else ""
    return [replace(t, done=done, done_at=stamp) if t.id == task_id else t for t in tasks]


def move(tasks: list[WorkTask], task_id: str, day: date) -> list[WorkTask]:
    return [replace(t, date=day.isoformat()) if t.id == task_id else t for t in tasks]


def assign(tasks: list[WorkTask], task_id: str, person: str) -> list[WorkTask]:
    """Set the task's people (one name, or several comma separated)."""
    return [replace(t, assignee=join_people([person])) if t.id == task_id else t for t in tasks]


def add_person(tasks: list[WorkTask], task_id: str, person: str) -> list[WorkTask]:
    return [replace(t, assignee=join_people(t.people + [person])) if t.id == task_id else t for t in tasks]


def toggle_person(tasks: list[WorkTask], task_id: str, person: str) -> list[WorkTask]:
    def flip(t):
        ps = [x for x in t.people if x != person] if person in t.people else t.people + [person]
        return replace(t, assignee=join_people(ps))
    return [flip(t) if t.id == task_id else t for t in tasks]


def swap_people(tasks: list[WorkTask], a: str, b: str) -> list[WorkTask]:
    """Two tasks trade their people (a task dropped on another)."""
    ta = next(t for t in tasks if t.id == a)
    tb = next(t for t in tasks if t.id == b)
    pa, pb = ta.assignee, tb.assignee
    return [replace(t, assignee=pb) if t.id == a else replace(t, assignee=pa) if t.id == b else t for t in tasks]


def reorder(members: list[str], name: str, index: int) -> list[str]:
    out = [m for m in members if m != name]
    out.insert(max(0, min(index, len(out))), name)
    return out


# ---- the workbook -------------------------------------------------------------------
def read_work(path: Path) -> tuple[list[WorkProject], list[WorkTask]]:
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)

    def rows(name):
        if name not in wb.sheetnames:
            return [], {}
        rs = [[clean_text(c) for c in r] for r in wb[name].iter_rows(values_only=True)]
        if not rs:
            return [], {}
        head = [h.strip() for h in rs[0]]
        return [r for r in rs[1:] if any(r)], {h: i for i, h in enumerate(head)}

    def get(r, col, h):
        i = col.get(h)
        return r[i].strip() if i is not None and i < len(r) else ""

    prs, pc = rows("프로젝트")
    trs, tc = rows("업무")
    wb.close()
    kinds = {v: k for k, v in KINDS.items()}
    freqs = {v: k for k, v in FREQS.items()}
    projects = []
    for r in prs:
        until = parse_half(get(r, pc, "기간"))  # an old "개수" column is just ignored
        try:
            per_turn = max(1, int(float(get(r, pc, "회차 인원") or 1)))
        except ValueError:
            per_turn = 1
        kind = get(r, pc, "종류")
        freq = get(r, pc, "주기")
        projects.append(WorkProject(
            id=get(r, pc, "ID") or _id(), name=get(r, pc, "이름"), kind=kinds.get(kind, kind or "rotation"),
            members=[m.strip() for m in get(r, pc, "담당자").replace("\n", ",").split(",") if m.strip()],
            freq=freqs.get(freq, freq or "weekly"), start=get(r, pc, "시작일")[:10] or date.today().isoformat(),
            until=format_half(*until) if until else "", task_title=get(r, pc, "업무 이름"), note=get(r, pc, "메모"), per_turn=per_turn))
    tasks = [WorkTask(id=get(r, tc, "ID") or _id(), project_id=get(r, tc, "프로젝트 ID"), date=get(r, tc, "날짜")[:10],
                      assignee=get(r, tc, "담당자"), title=get(r, tc, "업무"),
                      done=get(r, tc, "완료").lower() in TRUE, done_at=get(r, tc, "완료 시각"))
             for r in trs]
    return projects, tasks


def write_work(path: Path, projects: list[WorkProject], tasks: list[WorkTask]) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "프로젝트"
    ws.append(PROJECT_HEADERS)
    for p in projects:
        ws.append([p.id, p.name, p.kind_label, ", ".join(p.members),
                   p.freq_label if p.kind == "rotation" else "", p.start,
                   p.period_label if p.kind == "rotation" else "", p.task_title, p.note,
                   p.per_turn if p.kind == "rotation" else ""])
    wt = wb.create_sheet("업무")
    wt.append(TASK_HEADERS)
    names = {p.id: p.name for p in projects}
    for t in sorted(tasks, key=lambda t: (names.get(t.project_id, ""), t.date)):
        wt.append([t.id, t.project_id, names.get(t.project_id, ""), t.date, t.assignee, t.title,
                   "완료" if t.done else "", t.done_at])
    for sheet, widths in ((ws, (10, 24, 10, 36, 10, 12, 6, 16, 30, 9)), (wt, (10, 10, 24, 12, 12, 24, 6, 17))):
        for c in sheet[1]:
            c.font = Font(bold=True)
        for i, w in enumerate(widths):
            sheet.column_dimensions[chr(65 + i)].width = w
        sheet.freeze_panes = "A2"
    wb.save(path)


class WorkBoard:
    """업무.xlsx in the Drive folder (or a local file when the project is not a Drive
    folder). Edits show at once and go to Drive in batches by sync(); each change is
    re-applied on the newest file there, so what someone else wrote meanwhile is kept."""

    def __init__(self, remote, cache_dir: Path, file_id: str = "", local: Path | None = None):
        from .synced_file import SyncedFile
        self.local = Path(local) if local else None
        path = self.local or Path(cache_dir) / "work.xlsx"
        self.file = SyncedFile(remote, path, file_id, WORK_NAME,
                               lambda p: read_work(p), lambda p, st: write_work(p, *st), lambda: ([], []))

    remote = property(lambda self: self.file.remote)
    file_id = property(lambda self: self.file.file_id)
    path = property(lambda self: self.file.path)

    @property
    def projects(self) -> list[WorkProject]:
        return self.file.state[0]

    @projects.setter
    def projects(self, value) -> None:
        self.file.state = (list(value), self.file.state[1])

    @property
    def tasks(self) -> list[WorkTask]:
        return self.file.state[1]

    @tasks.setter
    def tasks(self, value) -> None:
        self.file.state = (self.file.state[0], list(value))

    def load(self) -> tuple[list[WorkProject], list[WorkTask]]:
        return self.file.load()

    def sync(self) -> bool:
        return self.file.sync()

    def dirty(self) -> bool:
        return self.file.dirty()

    def save(self, change) -> None:
        """`change(projects, tasks) -> (projects, tasks)`; shows at once, sent with the next sync."""
        self.file.edit(lambda st: tuple(change(list(st[0]), list(st[1]))))

    def replace_all(self, projects: list[WorkProject], tasks: list[WorkTask]) -> None:
        """Write this exact state (undo)."""
        ps, ts = copy.deepcopy(projects), copy.deepcopy(tasks)
        self.save(lambda _p, _t: (copy.deepcopy(ps), copy.deepcopy(ts)))

    def project(self, pid: str) -> WorkProject | None:
        return next((p for p in self.projects if p.id == pid), None)
