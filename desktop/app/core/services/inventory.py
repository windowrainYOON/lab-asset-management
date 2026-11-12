"""Stock state and dashboard reports.

UI-free so the same report can later answer a Slack query.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from ..models import CATEGORY_KINDS, SCOPE_LABELS, STOCK_STATES, Category, Item, state_label
from ..project import Project

_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")
_SPLIT = re.compile(r"^\s*\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)+\s*$")
ABSENT_FLAGS = {"x", "no", "n", "없음", "무", "-", "0", "false", "소진"}
NEEDS_ORDER = ("out", "low", "soon")  # states that go on the reorder list
SOON_SHARE = 0.10  # of the items down to one, the longest since last ordered are 'soon'


def parse_quantity(text: str) -> tuple[float | None, str]:
    """'3' -> 3, '2/7' (two boxes) -> 9, '7ml' -> (7, 'ml'), '(4)' -> 4."""
    text = (text or "").strip()
    if not text:
        return None, ""
    if _SPLIT.match(text):
        return float(sum(float(n) for n in _NUM.findall(text))), ""
    m = _NUM.search(text)
    if not m:
        return None, ""
    unit = text[m.end():].strip(" ()")
    return float(m.group()), unit if len(unit) <= 6 else ""


@dataclass
class StockInfo:
    state: str  # key of STOCK_STATES
    quantity: float | None = None
    unit: str = ""
    threshold: float | None = None

    @property
    def shortage(self) -> float:
        if self.quantity is None or self.threshold is None:
            return 0.0
        return max(0.0, self.threshold + 1 - self.quantity)


def order_soon(project: Project, category: Category, qty_field=None) -> set[int] | None:
    """Items with one left whose last order is the oldest 10% (at least one) of those
    with an order date. None when the category has no order dates."""
    from ..orders import last_date_field

    date_f = last_date_field(project, category.id)
    qty_field = qty_field or project.field_by_role(category.id, "quantity")
    if category.kind != "managed" or date_f is None or qty_field is None:
        return None
    ones = []
    for item in project.items(category.id):
        if item.status and item.status != "unknown":
            continue
        if parse_quantity(item.get(qty_field.id))[0] == 1 and item.get(date_f.id):
            ones.append((item.get(date_f.id), item.id))
    ones.sort()
    n = math.ceil(len(ones) * SOON_SHARE)
    return {iid for _d, iid in ones[:n]} | {-iid for _d, iid in ones[n:]}


def stock_info(project: Project, category: Category, item: Item, _cache: dict | None = None) -> StockInfo:
    cache = _cache if _cache is not None else {}
    key = category.id
    if key not in cache:
        qf = project.field_by_role(category.id, "quantity")
        cache[key] = (qf, project.field_by_role(category.id, "stock_flag"), order_soon(project, category, qf))
    qty_field, flag_field, soon = cache[key]
    threshold = item.threshold if item.threshold is not None else category.threshold
    qty, unit = (None, "")
    if qty_field:
        qty, unit = parse_quantity(item.get(qty_field.id))
    status_kind = category.kind == "status"
    if item.status in STOCK_STATES and item.status != "unknown":
        state = "ok" if status_kind and item.status in ("low", "soon") else item.status
        return StockInfo(state, qty, unit, threshold)
    if qty_field and qty is not None:
        if qty <= 0:
            state = "out"
        elif soon and item.id in soon:
            state = "soon"
        elif soon and -item.id in soon:
            state = "ok"  # one left but ordered recently enough
        elif not status_kind and qty <= threshold:
            state = "low"
        else:
            state = "ok"
        return StockInfo(state, qty, unit, threshold)
    if flag_field:
        flag = item.get(flag_field.id).strip().lower()
        if flag in ABSENT_FLAGS:
            return StockInfo("out", None, "", threshold)
        if flag:
            return StockInfo("ok", None, "", threshold)
    return StockInfo("unknown", qty, unit, threshold)


@dataclass
class CategorySummary:
    category: Category
    total: int = 0
    counts: dict[str, int] = field(default_factory=lambda: {k: 0 for k in STOCK_STATES})

    @property
    def tracked(self) -> int:
        return self.total - self.counts["unknown"]

    @property
    def shortage_ratio(self) -> float:
        tracked = self.tracked
        if tracked == 0:
            return 0.0
        return sum(self.counts[k] for k in NEEDS_ORDER) / tracked


@dataclass
class ShortageRow:
    item_id: int
    category: Category
    name: str
    subgroup: str
    info: StockInfo
    location: str = ""


@dataclass
class Report:
    summaries: list[CategorySummary]
    shortages: list[ShortageRow]  # managed: out + low + soon; status: out ("없음")

    @property
    def total_items(self) -> int:
        return sum(s.total for s in self.summaries)

    def count(self, state: str) -> int:
        return sum(s.counts[state] for s in self.summaries)


def build_report(project: Project, scope: str | None = None, kind: str | None = None,
                 exclude: set[int] | frozenset = frozenset()) -> Report:
    """`exclude`: category ids left out (lists hidden in the sidebar)."""
    summaries: list[CategorySummary] = []
    shortages: list[ShortageRow] = []
    cache: dict = {}
    for cat in project.categories(scope, kind):
        if cat.id in exclude:
            continue
        summary = CategorySummary(cat)
        name_f = project.field_by_role(cat.id, "name")
        loc_f = project.field_by_role(cat.id, "location")
        for item in project.items(cat.id):
            info = stock_info(project, cat, item, cache)
            summary.total += 1
            summary.counts[info.state] += 1
            if info.state in NEEDS_ORDER:
                shortages.append(ShortageRow(
                    item_id=item.id, category=cat,
                    name=item.get(name_f.id) if name_f else project.item_title(item.id),
                    subgroup=item.subgroup, info=info,
                    location=item.get(loc_f.id) if loc_f else "",
                ))
        summaries.append(summary)
    shortages.sort(key=lambda r: (NEEDS_ORDER.index(r.info.state), r.category.sort_order, r.name.lower()))
    return Report(summaries, shortages)


def report_text(project: Project, scope: str | None = None, limit: int = 20) -> str:
    """Plain-text report, for exports and a future Slack bot."""
    title = SCOPE_LABELS.get(scope or "", "전체")
    lines = [f"[{title}]"]
    for kind, kind_label in CATEGORY_KINDS.items():
        rep = build_report(project, scope, kind)
        if not rep.summaries:
            continue
        if kind == "managed":
            lines.append(f"{kind_label}: 항목 {rep.total_items}개 · 주문 임박 {rep.count('soon')} · 부족 {rep.count('low')} · 소진 {rep.count('out')}")
        else:
            lines.append(f"{kind_label}: 항목 {rep.total_items}개 · 보유 {rep.count('ok') + rep.count('low') + rep.count('soon')} · 없음 {rep.count('out')}")
        for s in rep.summaries:
            c = s.counts
            if kind == "managed":
                lines.append(f"- {s.category.name}: {s.total}개 (주문 임박 {c['soon']}, 부족 {c['low']}, 소진 {c['out']})")
            else:
                lines.append(f"- {s.category.name}: {s.total}개 (보유 {c['ok'] + c['low'] + c['soon']}, 없음 {c['out']})")
        if kind == "managed" and rep.shortages:
            lines.append("주문이 필요한 항목:")
            for r in rep.shortages[:limit]:
                qty = "" if r.info.quantity is None else f" {r.info.quantity:g}{r.info.unit}"
                lines.append(f"  · {r.name} ({r.category.name}){qty} — {state_label(r.info.state, kind)}")
    return "\n".join(lines)
