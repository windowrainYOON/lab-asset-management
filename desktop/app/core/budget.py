"""Budget (예산): the lab-fee account ledger and lab-fee rounds, in the existing Excel file
(예산 입출 내역.xlsx).

Ledger sheet (the first sheet whose header has 날짜 · 내용 · 수입 · 지출):
  날짜 | 내용 | 수입 | 지출 | 잔액 | (메모)
잔액 is kept as written; where it is empty (or a formula without a saved value) it is
the previous balance + 수입 − 지출. New rows are written in the same style as the
last row (date as text or as a date, the same number format; a formula balance if the
sheet uses formulas).

Lab-fee rounds: one sheet per round, "랩비 YYYY.MM":
  Target amount, Current balance, Lab size, the round's date, then one row per person:
  Name | 이름 | Unpaid carryover | Lab fee | Total | 직접 수정 | 메모
Lab fee = (Target − (Current balance + Σ carryover)) / lab size, rounded UP to 10 won.
A new round fills each carryover from the previous round: its Total minus that person's
"<이름> 랩비" deposits in the ledger since that round (never below 0). Every cell can be
changed by hand; a changed carryover is marked 직접 수정 and is not filled again.
"""
from __future__ import annotations

import copy
import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

BUDGET_NAME = "예산 입출 내역.xlsx"
ROUND_PREFIX = "랩비 "
LEDGER_HEADERS = ["날짜", "내용", "수입", "지출", "잔액"]
ROUND_HEADERS = ["Name", "이름", "Unpaid carryover", "Lab fee", "Total", "직접 수정", "메모"]


def parse_amount(v) -> int | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(round(v))
    s = re.sub(r"[^\d\-]", "", str(v))
    if not s or s == "-":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def parse_day(v) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    m = re.search(r"(\d{2,4})[.\-/](\d{1,2})[.\-/](\d{1,2})", str(v or ""))
    if not m:
        return None
    y = int(m.group(1))
    y = y + 2000 if y < 100 else y
    try:
        return date(y, int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def won(n: int | None) -> str:
    return "" if n is None else f"₩{n:,}"


@dataclass
class Entry:
    date: str = ""         # as shown: "2026.09.28"
    desc: str = ""
    income: int | None = None
    expense: int | None = None
    balance: int | None = None   # computed when the sheet has none
    memo: str = ""

    @property
    def day(self) -> date | None:
        return parse_day(self.date)


@dataclass
class FeeLine:
    name: str = ""         # English (as in the fee sheet)
    korean: str = ""
    carryover: int = 0
    manual: bool = False
    memo: str = ""
    auto: int = 0          # what the previous round would give (for ↺)


@dataclass
class FeeRound:
    label: str = ""        # "2026.09"
    made: str = ""         # ISO date the round was made (payments counted after it)
    target: int = 300_000
    balance: int = 0
    size: int = 0          # 0 = number of lines
    lines: list[FeeLine] = field(default_factory=list)
    fee_override: int | None = None

    @property
    def carry_total(self) -> int:
        return sum(ln.carryover for ln in self.lines)

    @property
    def lab_size(self) -> int:
        return self.size or len(self.lines) or 1

    @property
    def due(self) -> int:
        return self.target - (self.balance + self.carry_total)

    @property
    def fee(self) -> int:
        if self.fee_override is not None:
            return self.fee_override
        return max(0, math.ceil(self.due / self.lab_size / 10) * 10)

    def total(self, ln: FeeLine) -> int:
        return ln.carryover + self.fee

    @property
    def sheet(self) -> str:
        return ROUND_PREFIX + self.label


# ---- reading -----------------------------------------------------------------------------
def _find_header(ws) -> tuple[int, dict[str, int]] | None:
    for r_idx, row in enumerate(ws.iter_rows(min_row=1, max_row=15, values_only=True), start=1):
        cells = [str(c).strip() if c is not None else "" for c in row]
        if all(h in cells for h in ("날짜", "내용", "수입", "지출")):
            cols = {h: cells.index(h) for h in LEDGER_HEADERS if h in cells}
            # the column after 잔액 (unnamed) holds notes like "이월금 확인"
            memo = next((i for i, c in enumerate(cells) if c in ("메모", "비고")), None)
            if memo is None and "잔액" in cols:
                memo = cols["잔액"] + 1
            cols["메모"] = memo
            return r_idx, cols
    return None


def read_budget(path: Path) -> dict:
    from openpyxl import load_workbook

    wb = load_workbook(path, data_only=True)
    state = {"sheet": "", "ledger": [], "rounds": [], "date_text": True}
    try:
        for ws in wb.worksheets:
            if ws.title.startswith(ROUND_PREFIX):
                state["rounds"].append(asdict(_read_round(ws)))
                continue
            if state["sheet"]:
                continue
            found = _find_header(ws)
            if not found:
                continue
            hr, cols = found
            state["sheet"] = ws.title
            prev = 0
            for row in ws.iter_rows(min_row=hr + 1, values_only=True):
                def get(h):
                    i = cols.get(h)
                    return row[i] if i is not None and i < len(row) else None
                d, desc = get("날짜"), get("내용")
                inc, exp, bal = parse_amount(get("수입")), parse_amount(get("지출")), parse_amount(get("잔액"))
                memo = get("메모")
                if d is None and desc is None and inc is None and exp is None and bal is None:
                    continue
                if isinstance(d, (datetime, date)):
                    state["date_text"] = False
                    dtext = (d.date() if isinstance(d, datetime) else d).strftime("%Y.%m.%d")
                else:
                    dtext = str(d or "").strip()
                if bal is None:
                    bal = prev + (inc or 0) - (exp or 0)
                prev = bal
                state["ledger"].append(asdict(Entry(dtext, str(desc or "").strip(), inc, exp, bal,
                                                    str(memo or "").strip())))
    finally:
        wb.close()
    return state


def _read_round(ws) -> FeeRound:
    rnd = FeeRound(label=ws.title[len(ROUND_PREFIX):])
    rows = list(ws.iter_rows(values_only=True))
    head_at = None
    for i, row in enumerate(rows):
        cells = [str(c).strip() if c is not None else "" for c in row]
        key = cells[0] if cells else ""
        val = row[1] if len(row) > 1 else None
        if key == "Target amount":
            rnd.target = parse_amount(val) or 0
        elif key == "Current balance":
            rnd.balance = parse_amount(val) or 0
        elif key == "Lab size":
            rnd.size = parse_amount(val) or 0
        elif key == "기준일":
            d = parse_day(val)
            rnd.made = d.isoformat() if d else ""
        elif key == "Lab fee (직접)":
            rnd.fee_override = parse_amount(val)
        elif key == "Name" and "Unpaid carryover" in cells:
            head_at = i
            break
    if head_at is not None:
        head = [str(c).strip() if c is not None else "" for c in rows[head_at]]
        col = {h: head.index(h) for h in ROUND_HEADERS if h in head}
        for row in rows[head_at + 1:]:
            def get(h):
                j = col.get(h)
                return row[j] if j is not None and j < len(row) else None
            name = str(get("Name") or "").strip()
            if not name or name.lower().startswith("total"):
                continue
            rnd.lines.append(FeeLine(name, str(get("이름") or "").strip(), parse_amount(get("Unpaid carryover")) or 0,
                                     str(get("직접 수정") or "").strip() not in ("", "-"), str(get("메모") or "").strip()))
    return rnd


# ---- writing (keeps the file's own sheets and look) --------------------------------------
def write_budget(path: Path, state: dict, template: Path | None = None) -> None:
    from openpyxl import Workbook, load_workbook

    src = template if template is not None and Path(template).exists() else (path if Path(path).exists() else None)
    wb = load_workbook(src) if src else Workbook()
    if not src:
        ws = wb.active
        ws.title = state.get("sheet") or "입출 내역"
        ws.append(LEDGER_HEADERS)
    ws = wb[state["sheet"]] if state.get("sheet") in wb.sheetnames else wb.worksheets[0]
    found = _find_header(ws)
    if not found:
        ws.insert_rows(1)
        for i, h in enumerate(LEDGER_HEADERS, start=1):
            ws.cell(1, i, h)
        found = _find_header(ws)
    hr, cols = found
    _write_ledger(ws, hr, cols, state)
    for name in [n for n in wb.sheetnames if n.startswith(ROUND_PREFIX)]:
        del wb[name]
    for rd in sorted(state.get("rounds", []), key=lambda r: r.get("label", "")):
        _write_round(wb.create_sheet(ROUND_PREFIX + rd["label"]), _round(rd))
    wb.save(path)


def _write_ledger(ws, hr: int, cols: dict, state: dict) -> None:
    from copy import copy as _copy

    entries = [Entry(**e) for e in state.get("ledger", [])]
    first = hr + 1
    last_old = ws.max_row
    # the style to copy for rows the sheet did not have yet: its last data row
    style_row = last_old if last_old > hr else None
    bal_col = cols.get("잔액")
    formula = False
    if bal_col is not None:
        for r in range(first, last_old + 1):
            v = ws.cell(r, bal_col + 1).value
            if isinstance(v, str) and v.startswith("="):
                formula = True
                break
    old_date_cell = ws.cell(style_row, cols["날짜"] + 1).value if style_row else None
    as_date = not state.get("date_text", True) or isinstance(old_date_cell, (datetime, date))
    for i, e in enumerate(entries):
        r = first + i
        if style_row and r > last_old:
            for c in range(1, ws.max_column + 1):
                src, dst = ws.cell(style_row, c), ws.cell(r, c)
                if src.has_style:
                    dst._style = _copy(src._style)
        d = e.day
        ws.cell(r, cols["날짜"] + 1, d if (as_date and d) else e.date)
        ws.cell(r, cols["내용"] + 1, e.desc)
        ws.cell(r, cols["수입"] + 1, e.income)
        ws.cell(r, cols["지출"] + 1, e.expense)
        if bal_col is not None:
            if formula and r > first:
                L = lambda k: _letter(cols[k] + 1)  # noqa: E731
                ws.cell(r, bal_col + 1, f"={L('잔액')}{r - 1}+N({L('수입')}{r})-N({L('지출')}{r})")
            else:
                ws.cell(r, bal_col + 1, e.balance)
        if cols.get("메모") is not None:
            ws.cell(r, cols["메모"] + 1, e.memo or None)
    for r in range(first + len(entries), last_old + 1):
        for c in range(1, ws.max_column + 1):
            ws.cell(r, c).value = None


def _letter(n: int) -> str:
    from openpyxl.utils import get_column_letter
    return get_column_letter(n)


def _write_round(ws, rnd: FeeRound) -> None:
    from openpyxl.styles import Font

    bold = Font(bold=True)
    ws.append([f"Lab fee {rnd.label}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append(["기준일", rnd.made])
    ws.append(["Current balance", rnd.balance])
    ws.append(["Unpaid carryover", rnd.carry_total])
    ws.append(["Target amount", rnd.target])
    ws.append(["Balance due", rnd.due])
    ws.append(["Lab size", rnd.lab_size])
    ws.append(["Lab fee", rnd.fee])
    if rnd.fee_override is not None:
        ws.append(["Lab fee (직접)", rnd.fee_override])
    ws.append([])
    ws.append(ROUND_HEADERS)
    for c in ws[ws.max_row]:
        c.font = bold
    for ln in rnd.lines:
        ws.append([ln.name, ln.korean, ln.carryover, rnd.fee, rnd.total(ln), "직접 수정" if ln.manual else "", ln.memo])
    ws.append(["Total", "", rnd.carry_total, rnd.fee * len(rnd.lines), sum(rnd.total(x) for x in rnd.lines)])
    for row in ws.iter_rows(min_row=3):
        for c in row[1:5]:
            if isinstance(c.value, int):
                c.number_format = '"₩"#,##0'
    for col, w in zip("ABCDEFG", (22, 12, 18, 12, 12, 10, 30)):
        ws.column_dimensions[col].width = w


def _round(d: dict) -> FeeRound:
    d = dict(d)
    d["lines"] = [FeeLine(**x) for x in d.get("lines", [])]
    return FeeRound(**d)


# ---- the rules -------------------------------------------------------------------------
def entries(state: dict) -> list[Entry]:
    return [Entry(**e) for e in state.get("ledger", [])]


def rounds(state: dict) -> list[FeeRound]:
    return sorted((_round(r) for r in state.get("rounds", [])), key=lambda r: r.label)


def current_balance(state: dict) -> int:
    es = entries(state)
    return es[-1].balance or 0 if es else 0


def recompute(state: dict) -> dict:
    prev = 0
    for e in state.get("ledger", []):
        e["balance"] = prev + (e.get("income") or 0) - (e.get("expense") or 0)
        prev = e["balance"]
    return state


def paid_since(state: dict, korean: str, english: str, since: str) -> int:
    """Lab-fee deposits by one person in the ledger on/after `since` (ISO date)."""
    start = parse_day(since) if since else None
    total = 0
    for e in entries(state):
        if not e.income or "랩비" not in e.desc:
            continue
        if start and (e.day is None or e.day < start):
            continue
        names = [n for n in (korean, english) if n]
        if any(n in e.desc for n in names):
            total += e.income
    return total


def new_round(state: dict, label: str, people: list[tuple[str, str]], target: int | None = None,
              today: date | None = None) -> FeeRound:
    """A round filled from the previous one. people: [(English, 한글)]."""
    today = today or date.today()
    prev = rounds(state)[-1] if rounds(state) else None
    rnd = FeeRound(label=label, made=today.isoformat(), target=target or (prev.target if prev else 300_000),
                   balance=current_balance(state))
    by_name = {}
    if prev:
        by_name = {(ln.korean or ln.name): ln for ln in prev.lines} | {ln.name: ln for ln in prev.lines}
    for en, ko in people:
        ln = FeeLine(en or ko, ko)
        old = by_name.get(ko) or by_name.get(en)
        if prev and old is not None:
            owed = prev.total(old)
            paid = paid_since(state, ko, en, prev.made)
            ln.auto = max(0, owed - paid)
            ln.carryover = ln.auto
        rnd.lines.append(ln)
    rnd.size = len(rnd.lines)
    return rnd


def fill_auto(state: dict, rnd: FeeRound) -> FeeRound:
    """Refresh the automatic carryovers (a ledger change); hand-edited ones stay."""
    prevs = [r for r in rounds(state) if r.label < rnd.label]
    if not prevs:
        return rnd
    prev = prevs[-1]
    by_name = {(ln.korean or ln.name): ln for ln in prev.lines} | {ln.name: ln for ln in prev.lines}
    for ln in rnd.lines:
        old = by_name.get(ln.korean) or by_name.get(ln.name)
        if old is None:
            continue
        ln.auto = max(0, prev.total(old) - paid_since(state, ln.korean, ln.name, prev.made))
        if not ln.manual:
            ln.carryover = ln.auto
    return rnd


class BudgetBook:
    """The budget file in Drive (SyncedFile). Edits show at once and are sent in batches."""

    def __init__(self, remote, cache_dir: Path, file_id: str = ""):
        from .synced_file import SyncedFile
        self.cache_dir = Path(cache_dir)
        self.template = self.cache_dir / "budget.template.xlsx"
        self.file = SyncedFile(remote, self.cache_dir / "budget.xlsx", file_id, BUDGET_NAME,
                               self._read, self._write, lambda: {"sheet": "", "ledger": [], "rounds": [],
                                                                 "date_text": True})

    def _read(self, path: Path) -> dict:
        import shutil
        state = read_budget(path)
        self.template.parent.mkdir(parents=True, exist_ok=True)
        if Path(path) != self.template:
            shutil.copyfile(path, self.template)  # the look and other sheets to write back into
        return state

    def _write(self, path: Path, state: dict) -> None:
        write_budget(path, state, self.template if self.template.exists() else None)

    @property
    def file_id(self) -> str:
        return self.file.file_id

    @property
    def state(self) -> dict:
        return self.file.state

    def load(self):
        return self.file.load()

    def sync(self) -> bool:
        return self.file.sync()

    def dirty(self) -> bool:
        return self.file.dirty()

    def entries(self) -> list[Entry]:
        return entries(self.state)

    def rounds(self) -> list[FeeRound]:
        return rounds(self.state)

    def balance(self) -> int:
        return current_balance(self.state)

    # edits (each is replayed on the newest file when sent)
    def add_entry(self, e: Entry) -> None:
        row = asdict(e)
        row["balance"] = None

        def change(st):
            st.setdefault("ledger", []).append(copy.deepcopy(row))
            return recompute_tail(st)
        self.file.edit(change)

    def update_entry(self, index: int, e: Entry, old: Entry) -> None:
        new, before = asdict(e), asdict(old)
        new["balance"] = None

        def change(st):
            led = st.get("ledger", [])
            i = index if index < len(led) and _same(led[index], before) else \
                next((k for k, x in enumerate(led) if _same(x, before)), None)
            if i is not None:
                led[i] = copy.deepcopy(new)
            return recompute_tail(st)
        self.file.edit(change)

    def delete_entry(self, index: int, old: Entry) -> None:
        before = asdict(old)

        def change(st):
            led = st.get("ledger", [])
            i = index if index < len(led) and _same(led[index], before) else \
                next((k for k, x in enumerate(led) if _same(x, before)), None)
            if i is not None:
                del led[i]
                if i < len(led):
                    led[i]["balance"] = None
            return recompute_tail(st)
        self.file.edit(change)

    def save_round(self, rnd: FeeRound) -> None:
        d = asdict(rnd)

        def change(st):
            st["rounds"] = [r for r in st.get("rounds", []) if r.get("label") != d["label"]] + [copy.deepcopy(d)]
            return st
        self.file.edit(change)

    def delete_round(self, label: str) -> None:
        def change(st):
            st["rounds"] = [r for r in st.get("rounds", []) if r.get("label") != label]
            return st
        self.file.edit(change)


def _same(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in ("date", "desc", "income", "expense"))


def recompute_tail(st: dict) -> dict:
    """Balances after an edit: rows keep their written balance until the first changed
    one; from there on each is the previous + 수입 − 지출."""
    prev, dirty = 0, False
    for e in st.get("ledger", []):
        dirty = dirty or e.get("balance") is None
        if dirty:
            e["balance"] = prev + (e.get("income") or 0) - (e.get("expense") or 0)
        prev = e["balance"]
    return st
