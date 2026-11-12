"""운영 브리핑: one weekday-morning mail per lab member, holding only what is theirs.

  · 오늘 내 장비 예약          bookings made by me today
  · 내 당번·업무 (이번 주)      open 업무 tasks I am on, today through 6 days ahead
  · 오늘 내 일정              Google Calendar events assigned to me (or to everyone)
  · 처리할 주문               open 주문 요청 assigned to me, with the item's current stock.
                              Stock only appears here: an item is listed only when someone
                              asked for it with the cart (주문 요청 보내기).
A section with nothing in it is left out, and a person with nothing at all gets no mail.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from ..models import state_label
from .mail_reports import _e, _table, _text_table, _wrap, item_line


def recipients(project) -> list[dict]:
    """Members with an email, plus category owners not in the member list."""
    out, seen = [], set()
    for m in project.members():
        e = (m.get("email") or "").strip()
        if e and e.lower() not in seen:
            seen.add(e.lower())
            out.append({"name": m.get("name", "").strip() or e, "email": e})
    for cat in project.categories():
        o = project.owner(cat.id)
        e = (o.get("email") or "").strip()
        if e and e.lower() not in seen:
            seen.add(e.lower())
            out.append({"name": o.get("name", "").strip() or e, "email": e})
    return out


def _same(a: str, b: str) -> bool:
    return bool(a) and bool(b) and a.strip().lower() == b.strip().lower()


def _mine_event(ev, person: dict) -> bool:
    a = (ev.assignee or "").strip().lower()
    if a == "all":
        return True
    return _same(a, person["email"]) or _same(ev.assignee_name or "", person["name"])


def _ev_day(ev) -> date | None:
    s = ev.start
    if isinstance(s, datetime):
        return s.astimezone().date() if s.tzinfo else s.date()
    return s if isinstance(s, date) else None


def _ev_time(ev) -> str:
    if ev.all_day or not isinstance(ev.start, datetime):
        return "하루 종일"
    s = ev.start.astimezone() if ev.start.tzinfo else ev.start
    e = ev.end.astimezone() if isinstance(ev.end, datetime) and ev.end.tzinfo else ev.end
    return f"{s:%H:%M}" + (f"–{e:%H:%M}" if isinstance(e, datetime) else "")


def _stock_of(project, req) -> tuple[str, str]:
    """(current quantity, state label) of the inventory item the request is for, if found."""
    if project is None:
        return "", ""
    cat_name = (req.category or "").strip().lower()
    cats = [c for c in project.categories() if not cat_name or c.name.strip().lower() == cat_name]
    want_cat = (req.catalog or "").strip().lower()
    want_name = (req.item or "").strip().lower()
    for cat in cats:
        cat_f = project.field_by_role(cat.id, "catalog")
        for item in project.items(cat.id):
            hit = (want_cat and cat_f and (item.get(cat_f.id) or "").strip().lower() == want_cat) or \
                  (want_name and project.item_title(item.id).strip().lower() == want_name)
            if hit:
                ln = item_line(project, cat, item)
                return ln.quantity, state_label(ln.state, cat.kind)
    return "", ""


def sections(person: dict, project=None, reservations=None, work=None, order_requests=None,
             events=None, today: date | None = None) -> list[tuple[str, list[str], list[list[str]]]]:
    """[(title, headers, rows)] for one person; empty sections are left out."""
    today = today or date.today()
    out = []
    if reservations is not None:
        eq = {e.id: e for e in reservations.equipment()}
        mine = sorted(reservations.on_day(today, person["name"]), key=lambda r: r.start)
        rows = [[eq[r.equipment_id].name if r.equipment_id in eq else r.equipment,
                 f"{r.start:%H:%M}–{r.end:%H:%M}" if r.start.date() == r.end.date() else r.when(),
                 eq[r.equipment_id].location if r.equipment_id in eq else "", r.purpose] for r in mine]
        if rows:
            out.append(("오늘 내 장비 예약", ["장비", "시간", "위치", "용도"], rows))
    if work is not None:
        names = {p.id: p for p in work.projects}
        end = today + timedelta(days=6)
        mine = [t for t in work.tasks if not t.done and t.day and today <= t.day <= end
                and any(_same(p, person["name"]) for p in t.people)]
        mine.sort(key=lambda t: t.date)
        rows = []
        for t in mine:
            p = names.get(t.project_id)
            when = "오늘" if t.day == today else f"{t.day:%m/%d} ({'월화수목금토일'[t.day.weekday()]})"
            others = [x for x in t.people if not _same(x, person["name"])]
            rows.append([when, t.title or (p.duty() if p else ""), p.name if p else "", ", ".join(others)])
        if rows:
            out.append(("내 당번·업무 (이번 주)", ["날짜", "할 일", "업무", "함께"], rows))
    if events:
        mine = sorted((e for e in events if _ev_day(e) == today and e.calendar_id not in ("work", "reservations")
                       and _mine_event(e, person)), key=lambda e: (not e.all_day, _ev_time(e)))
        rows = [[_ev_time(e), e.title, e.location, e.calendar_name] for e in mine]
        if rows:
            out.append(("오늘 내 일정", ["시간", "일정", "장소", "캘린더"], rows))
    if order_requests is not None:
        mine = order_requests.for_owner(person["name"], person["email"], open_only=True)
        mine.sort(key=lambda o: (o.state != "요청", o.date))
        rows = []
        for o in mine:
            qty, state = _stock_of(project, o)
            rows.append([o.state, o.item, o.catalog, o.qty, o.requester, (o.date or "")[:10],
                         f"{qty} ({state})" if qty or state else "-"])
        if rows:
            out.append(("처리할 주문 (장바구니 요청)", ["상태", "품목", "Cat#", "수량", "요청자", "요청일", "현재 재고"], rows))
    return out


def ops_mail(person: dict, today: date | None = None, **sources) -> tuple[str, str, str] | None:
    """(subject, html, text) for one person, or None when there is nothing for them."""
    today = today or date.today()
    parts = sections(person, today=today, **sources)
    if not parts:
        return None
    counts = " · ".join(f"{t.split(' (')[0]} {len(r)}" for t, _, r in parts)
    subject = f"[Asset Manager] {today:%m/%d} 운영 브리핑 · {counts}"
    html = [f"<p>{_e(person['name'])}님, {today:%Y-%m-%d} ({'월화수목금토일'[today.weekday()]}) 브리핑입니다.</p>"]
    text = [f"{person['name']}님, {today:%Y-%m-%d} 브리핑입니다."]
    for title, headers, rows in parts:
        html.append(f'<h3 style="margin:14px 0 2px 0">{_e(title)} · {len(rows)}</h3>' + _table(headers, rows))
        text.append(f"\n[{title}] {len(rows)}\n" + _text_table(headers, rows))
    return subject, _wrap("운영 브리핑", "".join(html)), "\n".join(text)


def all_mails(project, today: date | None = None, only: str | None = None,
              **sources) -> list[tuple[list[str], str, str, str]]:
    """[([email], subject, html, text)] for everyone with something today."""
    out = []
    for person in recipients(project):
        if only and not _same(person["email"], only):
            continue
        body = ops_mail(person, today=today, project=project, **sources)
        if body:
            out.append(([person["email"]], *body))
    return out
