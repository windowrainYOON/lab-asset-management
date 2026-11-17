"""일정표 이미지 → 일정 후보 (schedule-table image import).

A monthly lab schedule is often shared as a picture of a calendar grid: weekday header on
top, one cell per day, and in each cell lines like

    오전11:00~        <- a time starts an entry
    홍길동            <- who presents (a member), a team ("… 팀") or an event name (저널클럽)
    (BOIC 3201호)     <- blue text in parentheses: the place

Reading has two parts. An OCR engine (``app.core.ocr_engine``) turns the picture into text
boxes; this module turns those boxes into event candidates. It finds the grid (cell lines in
the picture, else the weekday header and day numbers), works out which date each cell is,
splits each cell into entries, repairs common OCR slips in times ("오전71:0O~" → 11:00), and
matches names against the member list and words seen before (similar Hangul letters count,
so "운창우" still finds 윤창우). Everything here is pure Python + Pillow/numpy, so it is
tested without an OCR engine.
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from difflib import SequenceMatcher

SETTING_KEY = "schedule_import"   # shared setting (folder settings JSON)
DEFAULTS = {"person_title": "발표", "minutes": 60, "aliases": {}, "titles": []}

WEEKDAY_CHARS = "월화수목금토일"     # Python weekday() order (Monday = 0)


# ---- data -------------------------------------------------------------------------------
@dataclass
class Box:
    """One OCR text box in image pixels."""
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    score: float = 1.0
    blue: bool = False

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return self.y1 - self.y0


@dataclass
class Grid:
    xs: list[float]           # column edges, len = columns + 1
    ys: list[float]           # week-row edges, len = rows + 1
    weekdays: list[int]       # weekday() of each column (Sunday-first grid: [6, 0, 1, ...])
    source: str = "lines"     # "lines" (cell borders) or "text" (header + day numbers)

    def cell_of(self, x: float, y: float) -> tuple[int, int] | None:
        c = next((i for i in range(len(self.xs) - 1) if self.xs[i] <= x < self.xs[i + 1]), None)
        r = next((i for i in range(len(self.ys) - 1) if self.ys[i] <= y < self.ys[i + 1]), None)
        return None if c is None or r is None else (r, c)

    def cell_rect(self, r: int, c: int) -> tuple[float, float, float, float]:
        return self.xs[c], self.ys[r], self.xs[c + 1], self.ys[r + 1]


@dataclass
class Candidate:
    day: date
    start: time | None            # None = all-day entry (no time in the cell)
    end: time | None
    title: str
    assignee: str = ""            # member email ("" = none or not a member)
    assignee_name: str = ""
    location: str = ""
    raw: str = ""                 # what was read, for the review screen
    raw_name: str = ""            # the name/title text as read (key for learned fixes)
    rect: tuple = ()              # the cell in image pixels
    status: str = "new"           # new | check | dup
    note: str = ""
    include: bool = True

    def start_dt(self) -> datetime:
        return datetime.combine(self.day, self.start or time())

    def end_dt(self) -> datetime:
        if self.start is None:
            return datetime.combine(self.day, time())
        e = datetime.combine(self.day, self.end) if self.end else self.start_dt() + timedelta(hours=1)
        return e if e > self.start_dt() else self.start_dt() + timedelta(hours=1)


def options(project) -> dict:
    """The shared import settings merged over the defaults."""
    out = dict(DEFAULTS)
    saved = project.setting(SETTING_KEY, {}) if project is not None else {}
    if isinstance(saved, dict):
        out.update({k: v for k, v in saved.items() if v is not None})
    out["aliases"] = dict(out.get("aliases") or {})
    out["titles"] = list(out.get("titles") or [])
    return out


# ---- Hangul similarity -------------------------------------------------------------------
_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"


def jamo(text: str) -> str:
    """Split Hangul syllables into letters so near misses (운/윤, 반/빈) stay similar."""
    out = []
    for ch in unicodedata.normalize("NFC", text or ""):
        code = ord(ch) - 0xAC00
        if 0 <= code < 11172:
            out.append(_CHO[code // 588])
            out.append(_JUNG[(code % 588) // 28])
            if code % 28:
                out.append(_JONG[code % 28])
        else:
            out.append(ch.lower())
    return "".join(out)


def _key(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFC", text or "")).lower()


def similarity(a: str, b: str) -> float:
    a, b = _key(a), _key(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, jamo(a), jamo(b)).ratio()


_STEM = re.compile(r"^(.*?[가-힣])\s*([A-Za-z0-9]{1,3})$")


def best_match(text: str, words: list[str], cutoff: float = 0.72) -> tuple[str, float]:
    """The most similar word. A short Latin/number ending must match exactly
    ('랩미팅A' never becomes '랩미팅C'); a word seen with another ending lends its stem
    ('캠미팅B' + known '랩미팅C' → '랩미팅B')."""
    best, score = "", 0.0
    m = _STEM.match(text.strip())
    for w in words:
        mw = _STEM.match(w.strip())
        if m or mw:
            if not (m and mw):
                continue
            s = similarity(m.group(1), mw.group(1))
            cand = mw.group(1) + m.group(2)
        else:
            s = similarity(text, w)
            cand = w
        if s > score:
            best, score = cand, s
    return (best, score) if score >= cutoff else ("", score)


# ---- times ---------------------------------------------------------------------------------
_DIGITISH = str.maketrans({"O": "0", "o": "0", "D": "0", "C": "0", "c": "0", "Q": "0", "U": "0",
                           "l": "1", "I": "1", "|": "1", "!": "1", "i": "1", "L": "1",
                           "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2", "g": "9", "q": "9"})
_TIME_HEAD = re.compile(r"^\s*(오\s*[전후천주우루]|[AaPp]\.?\s*[Mm]\.?)?\s*(\d[\dOoDCcQUlI|!iLSsBZzgq]{0,4}(?:\s*[:.\-·;,]\s*[\dOoDCcQUlI|!iLSsBZzgq]{1,2})?)\s*(~|-|〜|∼)?")


def _ampm(word: str) -> str:
    w = (word or "").replace(" ", "")
    if not w:
        return ""
    if w[0] in "Pp" or (w[0] == "오" and len(w) > 1 and w[1] in "후우주루"):
        return "pm"
    return "am"


def _hm(raw: str) -> tuple[int, int] | None:
    s = raw.translate(_DIGITISH)
    parts = re.split(r"\s*[:.\-·;,]\s*", s)
    if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
        h, m = parts
    else:
        d = re.sub(r"\D", "", s)
        if len(d) <= 2:
            h, m = d, "00"
        elif len(d) == 3:
            h, m = d[0], d[1:]
        elif len(d) == 4:
            h, m = d[:2], d[2:]
        else:
            return None
    if not h:
        return None
    hh, mm = int(h), int(m[:2].ljust(2, "0"))
    if hh > 23 and len(h) == 2 and h[0] == "7":   # "71:00" — a 1 read as 7
        hh = int("1" + h[1])
    if hh > 23 or mm > 59:
        return None
    return hh, mm


def parse_time(line: str) -> tuple[time, time | None, str] | None:
    """'오전11:00~' → (11:00, None, ''), '오후2:00~3:30 홍길동' → (14:00, 15:30, '홍길동').
    None when the line does not start with a time."""
    text = unicodedata.normalize("NFC", line or "").strip()
    m = _TIME_HEAD.match(text)
    if not m:
        return None
    word, num, tilde = m.group(1), m.group(2), m.group(3)
    if not word and not tilde and not re.search(r"\d\s*[:.]\s*\d", num):
        return None  # a bare number (a day number, a room) is not a time
    hm = _hm(num)
    if hm is None:
        return None
    ap = _ampm(word) if word else ""
    h, mi = hm
    if ap == "pm" and h < 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    elif not ap and 1 <= h <= 7:
        h += 12  # "2:00~" in a lab schedule is in the afternoon
    start = time(h, mi)
    rest = text[m.end():].strip()
    end = None
    if tilde and rest:
        m2 = _TIME_HEAD.match(rest)
        if m2 and m2.group(2) and (m2.group(1) or re.search(r"\d\s*[:.]\s*\d", m2.group(2))):
            hm2 = _hm(m2.group(2))
            if hm2:
                h2, mi2 = hm2
                ap2 = _ampm(m2.group(1)) if m2.group(1) else ap
                if ap2 == "pm" and h2 < 12:
                    h2 += 12
                if (h2, mi2) > (start.hour, start.minute):
                    end = time(h2, mi2)
                    rest = rest[m2.end():].strip()
    return start, end, rest.lstrip("~ ").strip()


def clean_location(text: str) -> str:
    """'(BOIC 34013)' → 'BOIC 3401호', '(PBC 179로)' → 'PBC 179호'."""
    t = unicodedata.normalize("NFC", text or "").strip().strip("()[]{}<> ").strip()
    m = re.match(r"^(.*?\d{3,})(\d|[로오호초도토]|ㅎ)$", t)
    if m and not t.endswith("호"):
        t = m.group(1) + "호"
    return re.sub(r"\s+", " ", t)


# ---- the grid --------------------------------------------------------------------------------
def _runs(mask, min_len: int = 1) -> list[tuple[int, int]]:
    out, start = [], None
    for i, v in enumerate(list(mask) + [False]):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_len:
                out.append((start, i))
            start = None
    return out


def detect_lines(image) -> tuple[list[float], list[float]]:
    """Long horizontal and vertical lines (cell borders) of a grid picture: their centers."""
    import numpy as np
    g = np.asarray(image.convert("L"), dtype=np.int16)
    h, w = g.shape
    dark = g < 225
    rows = dark.mean(axis=1) > 0.55
    cols = dark.mean(axis=0) > 0.45
    hs = [(a + b - 1) / 2 for a, b in _runs(rows)]
    vs = [(a + b - 1) / 2 for a, b in _runs(cols)]

    def merge(vals, gap):
        out = []
        for v in vals:
            if out and v - out[-1][-1] <= gap:
                out[-1].append(v)
            else:
                out.append([v])
        return [sum(g_) / len(g_) for g_ in out]
    return merge(hs, max(4, h // 150)), merge(vs, max(4, w // 150))


def _weekday_of(text: str) -> int | None:
    t = re.sub(r"\s", "", text or "")
    if not t or len(t) > 4:
        return None
    for ch in t[:1]:
        if ch in WEEKDAY_CHARS:
            return WEEKDAY_CHARS.index(ch)
    low = t.lower()[:3]
    for i, en in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun")):
        if low == en:
            return i
    return None


def _day_number(b: Box) -> int | None:
    t = b.text.strip().translate(_DIGITISH)
    if re.fullmatch(r"\d{1,2}", t) and 1 <= int(t) <= 31:
        return int(t)
    return None


def find_grid(image, boxes: list[Box]) -> Grid | None:
    """Columns and week rows: from the cell borders when the picture has them, else from
    the weekday header and the day numbers."""
    width, height = image.size
    header = [(b, _weekday_of(b.text)) for b in boxes]
    header = [(b, d) for b, d in header if d is not None]
    header.sort(key=lambda t: t[0].cx)
    top_header = min((b.y1 for b, _ in header), default=0)
    header = [(b, d) for b, d in header if b.y0 <= top_header + 20]  # only the top row
    order = [d for _, d in header]
    weekdays = order if len(order) == 7 and len(set(order)) == 7 else [6, 0, 1, 2, 3, 4, 5]
    header_bottom = max((b.y1 for b, _ in header), default=0)

    hs, vs = detect_lines(image)
    min_cell_w, min_cell_h = width / 14, height / 14
    xs = [v for v in vs]
    if xs and xs[0] > min_cell_w:
        xs.insert(0, 0)
    if xs and width - xs[-1] > min_cell_w:
        xs.append(width - 1)
    cols = [(a, b) for a, b in zip(xs, xs[1:]) if b - a >= min_cell_w]
    ys = [y for y in hs if y >= header_bottom - 2]
    if ys and height - ys[-1] > min_cell_h:
        ys.append(height - 1)
    rows = [(a, b) for a, b in zip(ys, ys[1:]) if b - a >= min_cell_h]
    if len(cols) == 7 and 4 <= len(rows) <= 6:
        return Grid([c[0] for c in cols] + [cols[-1][1]], [r[0] for r in rows] + [rows[-1][1]], weekdays, "lines")

    # no usable borders: columns between the header names, rows from the day numbers
    if len(header) != 7:
        return None
    centers = [b.cx for b, _ in header]
    xs = [0.0] + [(a + b) / 2 for a, b in zip(centers, centers[1:])] + [float(width)]
    nums = sorted((b for b in boxes if _day_number(b) and b.y0 > header_bottom), key=lambda b: b.cy)
    bands: list[list[Box]] = []
    for b in nums:
        if bands and abs(b.cy - bands[-1][0].cy) < max(b.h, 6):
            bands[-1].append(b)
        else:
            bands.append([b])
    bands = [bd for bd in bands if len(bd) >= 3]
    if not 4 <= len(bands) <= 6:
        return None
    tops = [min(b.y0 for b in bd) - 4 for bd in bands]
    return Grid(xs, tops + [float(height)], weekdays, "text")


def guess_month(grid: Grid, boxes: list[Box], today: date | None = None) -> tuple[int, int]:
    """The month the picture shows: the one whose 1st falls where a '1' sits in the first
    week row (and whose last day matches), nearest to today; else next month after the 20th."""
    today = today or date.today()
    ones = []
    lasts = set()
    for b in boxes:
        n = _day_number(b)
        cell = grid.cell_of(b.cx, b.cy) if n else None
        if not cell:
            continue
        r, c = cell
        if n == 1 and r == 0:
            ones.append(grid.weekdays[c])
        if n in (28, 29, 30, 31) and r >= 3:
            lasts.add(n)
    cands = []
    for k in range(-2, 4):
        y, m = (today.year * 12 + today.month - 1 + k) // 12, (today.year * 12 + today.month - 1 + k) % 12 + 1
        first_wd = date(y, m, 1).weekday()
        ndays = calendar.monthrange(y, m)[1]
        score = (2 if ones and first_wd in ones else 0) + (1 if lasts and max(lasts) == ndays else 0)
        cands.append((score, -abs(k - 0.5), y, m))
    cands.sort(reverse=True)
    if cands[0][0] > 0:
        return cands[0][2], cands[0][3]
    nxt = today.replace(day=28) + timedelta(days=4)
    return (nxt.year, nxt.month) if today.day > 20 else (today.year, today.month)


def cell_date(grid: Grid, year: int, month: int, r: int, c: int) -> date:
    first = date(year, month, 1)
    col1 = grid.weekdays.index(first.weekday())
    return first + timedelta(days=7 * r + c - col1)


# ---- reading the cells -------------------------------------------------------------------------
def _lines(boxes: list[Box]) -> list[list[Box]]:
    out: list[list[Box]] = []
    for b in sorted(boxes, key=lambda b: b.cy):
        if out and abs(b.cy - sum(x.cy for x in out[-1]) / len(out[-1])) < max(b.h, 8) * 0.55:
            out[-1].append(b)
        else:
            out.append([b])
    return [sorted(line, key=lambda b: b.x0) for line in out]


def _is_location(text: str, blue: bool) -> bool:
    t = text.strip()
    return blue or (t.startswith("(") or t.startswith("（")) or (t.endswith(")") and "(" in t)


@dataclass
class _Entry:
    start: time | None = None
    end: time | None = None
    words: list[str] = field(default_factory=list)
    location: str = ""
    raw: list[str] = field(default_factory=list)


def read_cell(boxes: list[Box]) -> list[_Entry]:
    entries: list[_Entry] = []
    for line in _lines(boxes):
        text = " ".join(b.text.strip() for b in line).strip()
        if not text:
            continue
        blue = all(b.blue for b in line)
        t = parse_time(text)
        if t is not None:
            e = _Entry(t[0], t[1])
            e.raw.append(text)
            if t[2]:
                e.words.append(t[2])
            entries.append(e)
            continue
        if not entries:
            entries.append(_Entry())
        e = entries[-1]
        e.raw.append(text)
        if _is_location(text, blue):
            e.location = (e.location + " " if e.location else "") + clean_location(text)
        else:
            e.words.append(text)
    return [e for e in entries if e.words or e.location or e.start]


def build(image, boxes: list[Box], members: list[dict], opts: dict, year: int | None = None,
          month: int | None = None, existing=(), titles: list[str] = ()) -> tuple[list[Candidate], dict]:
    """Event candidates from OCR boxes. Returns (candidates, info) where info has the month,
    the grid and a note when the grid could not be found."""
    grid = find_grid(image, boxes)
    info: dict = {"grid": grid}
    if grid is None:
        info["error"] = "달력 칸을 찾지 못했습니다. 요일 머리줄과 날짜 칸이 보이게 다시 찍어 주세요."
        return [], info
    if year is None or month is None:
        year, month = guess_month(grid, boxes)
    info["year"], info["month"] = year, month

    cells: dict[tuple[int, int], list[Box]] = {}
    header_bottom = grid.ys[0]
    for b in boxes:
        if b.cy < header_bottom:
            continue
        cell = grid.cell_of(b.cx, b.cy)
        if cell is None:
            continue
        x0, y0, x1, y1 = grid.cell_rect(*cell)
        t = b.text.strip()
        if b.y0 < y0 + (y1 - y0) * 0.25 and (_day_number(b) or (len(t) <= 3 and re.search(r"\d", t))):
            continue  # the day number in the corner (also a misread one like "0스")
        cells.setdefault(cell, []).append(b)

    aliases = {_key(k): v for k, v in (opts.get("aliases") or {}).items()}
    people = [m for m in members if (m.get("name") or "").strip()]
    names = [m["name"] for m in people]
    words = list(dict.fromkeys([*opts.get("titles", []), *titles]))
    person_title = opts.get("person_title") or DEFAULTS["person_title"]
    minutes = int(opts.get("minutes") or DEFAULTS["minutes"])

    out: list[Candidate] = []
    for (r, c), bxs in sorted(cells.items()):
        day = cell_date(grid, year, month, r, c)
        for e in read_cell(bxs):
            raw_name = " ".join(e.words).strip()
            cand = Candidate(day, e.start, e.end, "", location=e.location, raw=" / ".join(e.raw),
                             raw_name=raw_name, rect=grid.cell_rect(r, c))
            odd_time = ""
            if e.start and e.start.hour < 7:  # 오전 1~6시: a leading "1" was most likely lost
                guess = time(e.start.hour + 10, e.start.minute)
                odd_time = f"‘{e.start:%H:%M}’로 읽혀 {guess:%H:%M}로 고쳤습니다. 시간을 확인해 주세요"
                cand.start = e.start = guess
                if e.end and e.end <= guess:
                    e.end = cand.end = None
            if e.start and not e.end:
                cand.end = (datetime.combine(day, e.start) + timedelta(minutes=minutes)).time()
                if cand.end <= e.start:
                    cand.end = time(23, 59)
            classify(cand, raw_name, people, names, words, aliases, person_title)
            if odd_time:
                cand.status, cand.note = "check", odd_time
            if day.month != month:
                cand.status, cand.include = "check", False
                cand.note = "다른 달 칸에 있는 일정"
            out.append(cand)
    mark_duplicates(out, existing)
    return out, info


def classify(cand: Candidate, text: str, people, names, words, aliases, person_title) -> None:
    """Title / assignee from the name text: member → 담당자 (title = person_title),
    '… 팀' → 담당자 as written, a known event name → title; else ask to check."""
    text = re.sub(r"\s+", " ", text or "").strip()
    fixed = aliases.get(_key(text))
    learned = fixed is not None
    if learned:
        text = fixed
    elif text:
        team = re.match(r"^(.*?)\s*(팀|team|Team)$", text)
        if not team:
            exact = next((n for n in names if _key(n) == _key(text)), None)
            if exact is None:
                exact = next((w for w in words if _key(w) == _key(text)), None)
            if exact is None:
                n, s = best_match(text, names)
                w, s2 = best_match(text, words)
                if n and s >= s2:
                    cand.note = f"‘{text}’을(를) {n}(으)로 읽었습니다"
                    text = n
                elif w:
                    cand.note = f"‘{text}’을(를) {w}(으)로 읽었습니다"
                    text = w
            else:
                text = exact
    member = next((m for m in people if _key(m["name"]) == _key(text)), None)
    if member:
        cand.title = person_title
        cand.assignee, cand.assignee_name = member.get("email", ""), member["name"]
        return
    if re.search(r"(팀|team|Team)$", text):
        cand.title = person_title
        cand.assignee_name = text
        return
    if not text:
        cand.title = "(제목 없음)"
        cand.status = "check"
        return
    if any(_key(w) == _key(text) for w in words):
        cand.title = text
        return
    if learned:  # fixed by hand before: trust it
        if re.fullmatch(r"[가-힣]{2,5}", text) and not any(_key(w) == _key(text) for w in words) \
                and fixed_is_person(text, aliases):
            cand.title, cand.assignee_name = person_title, text
        else:
            cand.title = text
        return
    if fixed_is_person(text, aliases):  # a non-member person confirmed before
        cand.title, cand.assignee_name = person_title, text
        return
    if re.fullmatch(r"[가-힣]{3}", text) and not any(_key(w) == _key(text) for w in words):
        # three Hangul letters: most likely a person who is not on the member list
        cand.title = person_title
        cand.assignee_name = text
        cand.status = "check"
        cand.note = cand.note or "구성원 목록에 없는 이름입니다"
        return
    cand.title = text
    if not cand.note:  # an event name read for the first time
        cand.status = "check"
        cand.note = "처음 보는 이름입니다. 맞게 읽었는지 확인해 주세요"


def fixed_is_person(text: str, aliases: dict) -> bool:
    """A learned fix is a person when it was saved as one (see learn: '@' marks a person)."""
    return aliases.get("@" + _key(text)) is not None


def mark_duplicates(cands: list[Candidate], existing) -> None:
    """Switch off entries the calendar already has (same day and start, same title or person)."""
    have = []
    for ev in existing or ():
        s = getattr(ev, "start", None)
        if isinstance(s, datetime):
            s = s.astimezone().replace(tzinfo=None)
            have.append((s.date(), s.time().replace(second=0, microsecond=0), _key(ev.title),
                         _key(getattr(ev, "assignee_name", ""))))
        elif isinstance(s, date):
            have.append((s, None, _key(ev.title), _key(getattr(ev, "assignee_name", ""))))
    for c in cands:
        for d, t, title, who in have:
            if d == c.day and t == c.start and (title in (_key(c.title), _key(c.raw_name))
                                                or (who and who == _key(c.assignee_name))):
                c.status, c.include, c.note = "dup", False, "캘린더에 이미 있습니다"
                break


def mark_blue(image, boxes: list[Box]) -> None:
    """Flag boxes whose letters are blue (places are often written in blue)."""
    import numpy as np
    rgb = image.convert("RGB")
    for b in boxes:
        x0, y0, x1, y1 = (int(max(0, b.x0)), int(max(0, b.y0)), int(b.x1), int(b.y1))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        a = np.asarray(rgb.crop((x0, y0, x1, y1)), dtype=np.int16).reshape(-1, 3)
        lum = a.mean(axis=1)
        ink = a[lum < 200]
        if len(ink) < 5:
            continue
        r, g, bl = ink.mean(axis=0)
        b.blue = bool(bl - r > 35 and bl > g)


def learn(opts: dict, cands: list[Candidate]) -> dict:
    """After an import: remember fixes the user made (read text → final name or title) and
    the event names used, so the next month's picture is read right away."""
    out = dict(opts)
    aliases = dict(out.get("aliases") or {})
    titles = list(out.get("titles") or [])
    person_title = out.get("person_title") or DEFAULTS["person_title"]
    for c in cands:
        if not c.include:
            continue
        final = c.assignee_name if c.title == person_title and c.assignee_name else c.title
        if c.raw_name and final and _key(c.raw_name) != _key(final):
            aliases[c.raw_name] = final
        if c.title == person_title and c.assignee_name and not c.assignee:
            aliases["@" + _key(c.assignee_name)] = c.assignee_name  # a person who is not a member
        if c.title and c.title != person_title and c.title not in titles:
            titles.append(c.title)
    out["aliases"], out["titles"] = aliases, titles[-200:]
    return out
