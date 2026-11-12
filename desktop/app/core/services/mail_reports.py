"""Mail bodies: the inventory briefing for each person in charge (담당자) and
order requests from the cart. UI-free; sending is done by integrations.gmail."""
from __future__ import annotations

import html as _html
from dataclasses import dataclass
from datetime import date

from ..models import Category, Item, state_label
from ..orders import last_date_field
from ..project import Project
from .inventory import NEEDS_ORDER, CategorySummary, stock_info

COMPANY_WORDS = ("제조사", "회사", "company", "manufacturer", "brand", "vendor", "maker", "업체")
STATE_HEX = {"out": "#c62828", "low": "#ef6c00", "soon": "#b58100", "ok": "#2e7d32", "unknown": "#8a93a6"}


@dataclass
class ItemLine:
    item_id: int
    category: str
    group: str
    name: str
    company: str
    catalog: str
    quantity: str
    last_order: str
    location: str
    state: str
    owner_email: str = ""
    owner_name: str = ""
    request_qty: str = ""


def _company_field(project: Project, cid: int):
    for f in project.fields(cid):
        if not f.virtual and any(w in f.name.lower() for w in COMPANY_WORDS):
            return f
    return None


def item_line(project: Project, cat: Category, item: Item, cache: dict | None = None) -> ItemLine:
    def val(role):
        f = project.field_by_role(cat.id, role)
        return item.get(f.id) if f else ""

    comp = _company_field(project, cat.id)
    date_f = last_date_field(project, cat.id)
    info = stock_info(project, cat, item, cache)
    owner = project.owner(cat.id)
    return ItemLine(
        item_id=item.id, category=cat.name, group=item.subgroup,
        name=val("name") or project.item_title(item.id), company=item.get(comp.id) if comp else "",
        catalog=val("catalog"), quantity=val("quantity"),
        last_order=item.get(date_f.id) if date_f else "", location=val("location"),
        state=info.state, owner_email=owner.get("email", ""), owner_name=owner.get("name", ""),
    )


def owners(project: Project) -> dict[str, dict]:
    """email -> {'name': ..., 'categories': [Category, ...]}"""
    out: dict[str, dict] = {}
    for cat in project.categories():
        o = project.owner(cat.id)
        email = o.get("email", "").strip()
        if email:
            entry = out.setdefault(email.lower(), {"email": email, "name": o.get("name", ""), "categories": []})
            entry["categories"].append(cat)
    return out


# ---- html helpers ------------------------------------------------------------
_CSS_TABLE = "border-collapse:collapse;font-size:13px;margin:6px 0 18px 0"
_CSS_TH = "background:#eef1f8;border:1px solid #d5dae6;padding:6px 10px;text-align:left"
_CSS_TD = "border:1px solid #e1e5ee;padding:5px 10px"


def _e(text) -> str:
    return _html.escape("" if text is None else str(text))


def _table(headers: list[str], rows: list[list[str]], colors: list[str | None] | None = None) -> str:
    head = "".join(f'<th style="{_CSS_TH}">{_e(h)}</th>' for h in headers)
    body = []
    for i, r in enumerate(rows):
        color = colors[i] if colors else None
        cells = []
        for j, c in enumerate(r):
            style = _CSS_TD + (f";color:{color};font-weight:600" if color and j == 0 else "")
            cells.append(f'<td style="{style}">{_e(c)}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f'<table style="{_CSS_TABLE}"><tr>{head}</tr>{"".join(body)}</table>'


def _text_table(headers: list[str], rows: list[list[str]]) -> str:
    return "\n".join([" | ".join(headers)] + [" | ".join(str(c or "-") for c in r) for r in rows])


def _wrap(title: str, inner: str) -> str:
    return (f'<div style="font-family:-apple-system,\'Apple SD Gothic Neo\',\'Malgun Gothic\',sans-serif;'
            f'color:#1f2433;max-width:900px"><h2 style="margin:0 0 4px 0">{_e(title)}</h2>{inner}'
            '<p style="color:#8a93a6;font-size:12px;margin-top:20px">Asset Manager에서 자동으로 보낸 메일입니다.</p></div>')


# ---- briefing ------------------------------------------------------------------
def briefing(project: Project, owner_email: str, today: date | None = None) -> tuple[str, str, str] | None:
    """(subject, html, text) of the inventory briefing for one person, or None if they own nothing."""
    entry = owners(project).get(owner_email.strip().lower())
    if not entry:
        return None
    today = today or date.today()
    name = entry["name"] or entry["email"]
    cache: dict = {}
    parts_html, parts_text, need_total = [], [], 0
    summary_rows = []
    for cat in entry["categories"]:
        s = CategorySummary(cat)
        lines = []
        for item in project.items(cat.id):
            line = item_line(project, cat, item, cache)
            s.total += 1
            s.counts[line.state] += 1
            # '재고 현황' categories only record what the lab has; nothing to reorder there
            if cat.kind == "managed" and line.state in NEEDS_ORDER:
                lines.append(line)
        if cat.kind == "managed":
            summary_rows.append([cat.name, s.total, s.counts["ok"], s.counts["soon"], s.counts["low"],
                                 s.counts["out"], s.counts["unknown"]])
        else:
            summary_rows.append([cat.name, s.total, s.counts["ok"] + s.counts["low"] + s.counts["soon"], "-", "-",
                                 s.counts["out"], s.counts["unknown"]])
        lines.sort(key=lambda ln: (NEEDS_ORDER.index(ln.state), ln.name.lower()))
        need_total += len(lines)
        if lines:
            headers = ["상태", "이름", "시트", "제조사", "Cat#", "현재 수량", "마지막 주문일", "위치"]
            rows = [[state_label(ln.state, cat.kind), ln.name, ln.group, ln.company, ln.catalog, ln.quantity,
                     ln.last_order, ln.location] for ln in lines]
            parts_html.append(f'<h3 style="margin:14px 0 2px 0">{_e(cat.name)} · 주문이 필요한 항목 {len(lines)}개</h3>'
                              + _table(headers, rows, [STATE_HEX[ln.state] for ln in lines]))
            parts_text.append(f"\n[{cat.name}] 주문이 필요한 항목 {len(lines)}개\n" + _text_table(headers, rows))
    sum_headers = ["카테고리", "전체", "충분·보유", "주문 임박", "부족", "소진·없음", "미확인"]
    subject = f"[Asset Manager] {today:%m/%d} 재고 브리핑 · 주문 필요 {need_total}개"
    intro = (f"<p>{_e(name)}님, {today:%Y-%m-%d} 기준 담당 재고 현황입니다. "
             f"주문이 필요한 항목은 <b>{need_total}개</b>입니다.</p>")
    html = _wrap("재고 브리핑", intro + _table(sum_headers, summary_rows)
                 + ("".join(parts_html) or "<p>주문이 필요한 항목이 없습니다.</p>"))
    text = (f"{name}님, {today:%Y-%m-%d} 기준 담당 재고 현황입니다. 주문 필요 {need_total}개.\n\n"
            + _text_table(sum_headers, summary_rows) + "\n" + "\n".join(parts_text))
    return subject, html, text


# ---- order request --------------------------------------------------------------
ORDER_HEADERS = ["이름", "제조사", "Cat#", "요청 수량", "마지막 주문일", "카테고리", "현재 수량"]


def order_requests(lines: list[ItemLine], requester: str, note: str = "",
                   today: date | None = None) -> dict[str, tuple[str, str, str]]:
    """Group the cart by the person in charge: owner email -> (subject, html, text).
    Lines whose category has no owner are left out (the UI asks for one first)."""
    today = today or date.today()
    by_owner: dict[str, list[ItemLine]] = {}
    for ln in lines:
        if ln.owner_email:
            by_owner.setdefault(ln.owner_email, []).append(ln)
    out = {}
    for email, items in by_owner.items():
        rows = [[ln.name, ln.company, ln.catalog, ln.request_qty, ln.last_order or "기록 없음", ln.category,
                 ln.quantity] for ln in items]
        who = items[0].owner_name or email
        subject = f"[Asset Manager] 주문 요청 {len(items)}건 · {requester or '요청자'}"
        note_html = f'<p style="background:#f6f7fb;padding:8px 12px;border-radius:6px">메모: {_e(note)}</p>' if note else ""
        html = _wrap("주문 요청", f"<p>{_e(who)}님, {_e(requester or '요청자')}님이 {today:%Y-%m-%d}에 "
                     f"아래 {len(items)}개 품목의 주문을 요청했습니다.</p>{note_html}" + _table(ORDER_HEADERS, rows))
        text = (f"{who}님, {requester or '요청자'}님이 {today:%Y-%m-%d}에 아래 {len(items)}개 품목의 주문을 요청했습니다.\n"
                + (f"메모: {note}\n" if note else "") + "\n" + _text_table(ORDER_HEADERS, rows))
        out[email] = (subject, html, text)
    return out
