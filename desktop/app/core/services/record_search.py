"""통합 검색 for the records that are not inventory items.

Every record type becomes a RecordHit: a group (kind), a title, a one-line summary,
the full field list for the read-only inspector, and a ref the window opens it by.
Matching is plain: every free word of the query must appear somewhere in the record.
Inventory-only filters (cat:, state:, qty<2 …) leave records out.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from .search import parse_query

KINDS = {  # kind: (group label, tab key the 열기 button goes to)
    "order_request": ("주문 요청", "orders"),
    "order_history": ("주문 기록", ""),
    "reservation": ("장비 예약", "reservations"),
    "equipment": ("장비", "reservations"),
    "notice": ("공지", "notices"),
    "work": ("업무", "work"),
    "event": ("일정", "schedule"),
    "ledger": ("예산 장부", "budget"),
    "fee_round": ("랩비 회차", "budget"),
}
ORDER = list(KINDS)


@dataclass
class RecordHit:
    kind: str
    title: str
    summary: str
    fields: list[tuple[str, str]] = field(default_factory=list)
    ref: object = None
    when: str = ""

    @property
    def group(self) -> str:
        return KINDS[self.kind][0]

    @property
    def tab(self) -> str:
        return KINDS[self.kind][1]


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, int):
        return f"{v:,}"
    return str(v).strip()


def _hit(kind, title, summary, pairs, ref, when="") -> RecordHit:
    return RecordHit(kind, title or "(제목 없음)", summary, [(k, _s(v)) for k, v in pairs if _s(v)], ref, _s(when))


def _matches(hit: RecordHit, words: list[str]) -> bool:
    blob = " ".join([hit.title, hit.summary] + [f"{k} {v}" for k, v in hit.fields]).lower()
    return all(w in blob for w in words)


def collect(sources: dict) -> list[RecordHit]:
    """Every record the sources hold, as hits (no filtering)."""
    out: list[RecordHit] = []
    reqs = sources.get("order_requests")
    if reqs is not None:
        for r in reqs.requests():
            out.append(_hit("order_request", r.item, f"{r.state} · {r.requester} → {r.owner or '담당 미정'}",
                            [("품목", r.item), ("제조사", r.company), ("Cat#", r.catalog), ("수량", r.qty),
                             ("단가", r.price), ("카테고리", r.category), ("요청자", r.requester),
                             ("담당자", r.owner), ("상태", r.state), ("요청일", r.date), ("주문일", r.ordered),
                             ("입고일", r.received), ("링크", r.link), ("용도·메모", r.note)], r.id, r.date))
    orders = sources.get("orders")
    if orders is not None:
        for o in orders.all():
            out.append(_hit("order_history", o.product, f"{o.date} · {o.orderer} · {o.qty_text or _s(o.qty)}{o.unit}",
                            [("품목", o.product), ("제조사", o.company), ("Cat#", o.catalog),
                             ("수량", f"{o.qty_text or _s(o.qty)} {o.unit}".strip()), ("규격", f"{o.size} {o.size_unit}".strip()),
                             ("주문자", o.orderer), ("주문일", o.date), ("파일", o.source)], None, o.date))
    res = sources.get("reservations")
    if res is not None:
        for e in res.equipment():
            out.append(_hit("equipment", e.name, f"{e.location} · {e.state}".strip(" ·"),
                            [("장비", e.name), ("위치", e.location), ("담당자", e.owner), ("상태", e.state),
                             ("최대(시간)", e.max_hours or ""), ("메모", e.note)], e.id))
        for r in res.reservations():
            eq = res.equipment_by_id(r.equipment_id)
            name = eq.name if eq else r.equipment
            out.append(_hit("reservation", f"{name} · {r.person}", r.when(),
                            [("장비", name), ("예약자", r.person), ("시작", r.start), ("끝", r.end),
                             ("용도", r.purpose)], r.id, r.start))
    notices = sources.get("notices")
    if notices is not None:
        for n in notices:
            out.append(_hit("notice", n.title, f"{n.date} · {n.author}",
                            [("제목", n.title), ("날짜", n.date), ("작성자", n.author), ("내용", n.body),
                             ("열람", ", ".join(n.readers))], n.id, n.date))
    work = sources.get("work")
    if work is not None:
        names = {p.id: p for p in work.projects}
        for p in work.projects:
            out.append(_hit("work", p.name, f"{p.kind_label} · {', '.join(p.members)}",
                            [("업무", p.name), ("종류", p.kind_label), ("주기", p.freq_label if p.kind == "rotation" else ""),
                             ("멤버", ", ".join(p.members)), ("메모", p.note)], (p.id, ""), p.start))
        for t in work.tasks:
            p = names.get(t.project_id)
            title = t.title or (p.duty() if p else "")
            out.append(_hit("work", f"{title} · {t.assignee}", f"{t.date} · {p.name if p else ''}{' · 완료' if t.done else ''}",
                            [("업무", p.name if p else ""), ("할 일", title), ("날짜", t.date), ("담당", t.assignee),
                             ("완료", "예" if t.done else "")], (t.project_id, t.id), t.date))
    for ev in sources.get("events") or []:
        if ev.calendar_id in ("work", "reservations"):
            continue  # already listed above
        out.append(_hit("event", ev.title, f"{_s(ev.start)}{' · ' + ev.calendar_name if ev.calendar_name else ''}",
                        [("제목", ev.title), ("시작", ev.start), ("끝", ev.end), ("장소", ev.location),
                         ("담당", ev.assignee_name or ev.assignee), ("캘린더", ev.calendar_name),
                         ("내용", ev.description)], ev, ev.start))
    book = sources.get("budget")
    if book is not None:
        for i, e in enumerate(book.entries()):
            amount = f"+{_s(e.income)}" if e.income else (f"−{_s(e.expense)}" if e.expense else "")
            out.append(_hit("ledger", e.desc, f"{e.date} · {amount}",
                            [("날짜", e.date), ("내용", e.desc), ("수입", e.income), ("지출", e.expense),
                             ("잔액", e.balance), ("메모", e.memo)], ("ledger", i), e.date))
        for rnd in book.rounds():
            people = ", ".join(f"{ln.korean or ln.name} {_s(rnd.total(ln))}" for ln in rnd.lines)
            out.append(_hit("fee_round", f"랩비 {rnd.label}", f"1인 {_s(rnd.fee)}원 · {rnd.lab_size}명",
                            [("회차", rnd.label), ("기준일", rnd.made), ("Target", rnd.target), ("잔액", rnd.balance),
                             ("1인 랩비", rnd.fee), ("사람별 Total", people)], ("round", rnd.label), rnd.made))
    return out


def search_records(sources: dict, text: str, limit: int = 300) -> list[RecordHit]:
    q = parse_query(text)
    if q.filters or q.state or q.category or q.scope or not q.terms:
        return []  # inventory filters: records do not apply
    words = [t.lower() for t in q.terms]
    hits = [h for h in collect(sources) if _matches(h, words)]
    hits.sort(key=lambda h: (ORDER.index(h.kind), "" if not h.when else h.when), reverse=False)
    # newest first inside each group
    grouped: dict[str, list[RecordHit]] = {}
    for h in hits:
        grouped.setdefault(h.kind, []).append(h)
    out = []
    for k in ORDER:
        out += sorted(grouped.get(k, []), key=lambda h: h.when, reverse=True)
    return out[:limit]
