"""Order requests (주문 요청), kept in one workbook in Drive:

  주문 요청: ID | 요청일 | 요청자 | 요청자 이메일 | 품목 | 제조사 | Cat# | 수량 | 단가 | 링크 |
             카테고리 | 담당자 | 담당자 이메일 | 용도·메모 | 상태 | 담당자 메일 | 주문일 | 입고일 | 알림

Flow: 요청 (mail to the person in charge) → 주문함 → 입고됨, or 취소. When 알림 is on,
the requester gets a mail when the state changes. Cart requests are recorded here too.
The person in charge comes from the item's category owner (담당자 지정), changeable.
"""
from __future__ import annotations

import html as _html
from dataclasses import dataclass, field
from datetime import date, datetime

from .records import RecordBoard, new_id

ORDER_REQ_NAME = "주문 요청.xlsx"
SHEET = "주문 요청"
HEADERS = ["ID", "요청일", "요청자", "요청자 이메일", "품목", "제조사", "Cat#", "수량", "단가", "링크", "카테고리",
           "담당자", "담당자 이메일", "용도·메모", "상태", "담당자 메일", "주문일", "입고일", "알림"]
STATES = ["요청", "주문함", "입고됨", "취소"]
OPEN_STATES = ("요청", "주문함")
TRUE = ("o", "y", "yes", "true", "1", "켬", "on")


@dataclass
class OrderRequest:
    id: str = field(default_factory=new_id)
    date: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M"))
    requester: str = ""
    requester_email: str = ""
    item: str = ""
    company: str = ""
    catalog: str = ""
    qty: str = "1"
    price: str = ""
    link: str = ""
    category: str = ""
    owner: str = ""
    owner_email: str = ""
    note: str = ""
    state: str = "요청"
    mailed: str = ""
    ordered: str = ""
    received: str = ""
    notify: bool = True

    @classmethod
    def from_row(cls, r: dict) -> "OrderRequest":
        return cls(r.get("ID") or new_id(), r.get("요청일", ""), r.get("요청자", ""), r.get("요청자 이메일", ""),
                   r.get("품목", ""), r.get("제조사", ""), r.get("Cat#", ""), r.get("수량", ""), r.get("단가", ""),
                   r.get("링크", ""), r.get("카테고리", ""), r.get("담당자", ""), r.get("담당자 이메일", ""),
                   r.get("용도·메모", ""), r.get("상태") if r.get("상태") in STATES else "요청",
                   r.get("담당자 메일", ""), r.get("주문일", ""), r.get("입고일", ""),
                   (r.get("알림") or "켬").strip().lower() in TRUE)

    def to_row(self) -> dict:
        return {"ID": self.id, "요청일": self.date, "요청자": self.requester, "요청자 이메일": self.requester_email,
                "품목": self.item, "제조사": self.company, "Cat#": self.catalog, "수량": self.qty, "단가": self.price,
                "링크": self.link, "카테고리": self.category, "담당자": self.owner, "담당자 이메일": self.owner_email,
                "용도·메모": self.note, "상태": self.state, "담당자 메일": self.mailed, "주문일": self.ordered,
                "입고일": self.received, "알림": "켬" if self.notify else "끔"}

    @property
    def is_open(self) -> bool:
        return self.state in OPEN_STATES

    @property
    def day(self) -> str:
        return (self.date or "")[:10]


class OrderRequestBoard(RecordBoard):
    SPEC = {SHEET: HEADERS}
    WIDTHS = {SHEET: [10, 17, 10, 22, 30, 14, 14, 7, 11, 24, 18, 10, 22, 30, 8, 17, 11, 11, 6]}
    NAME = ORDER_REQ_NAME
    CACHE = "order_requests.xlsx"

    def requests(self) -> list[OrderRequest]:
        return sorted((OrderRequest.from_row(r) for r in self.rows(SHEET)), key=lambda o: o.date, reverse=True)

    def get(self, rid: str) -> OrderRequest | None:
        return next((o for o in self.requests() if o.id == rid), None)

    def save(self, req: OrderRequest) -> None:
        self.upsert(SHEET, req.to_row())

    def remove(self, rid: str) -> None:
        self.delete(SHEET, rid)

    def set_state(self, rid: str, state: str) -> OrderRequest | None:
        req = self.get(rid)
        if req is None or state not in STATES:
            return None
        req.state = state
        today = date.today().isoformat()
        if state == "주문함" and not req.ordered:
            req.ordered = today
        if state == "입고됨" and not req.received:
            req.received = today
        self.save(req)
        return req

    def for_owner(self, name: str, email: str = "", open_only: bool = True) -> list[OrderRequest]:
        n, e = (name or "").strip().lower(), (email or "").strip().lower()
        out = [o for o in self.requests()
               if (n and o.owner.strip().lower() == n) or (e and o.owner_email.strip().lower() == e)]
        return [o for o in out if o.is_open] if open_only else out


# ---- mails ---------------------------------------------------------------------------
def _e(t) -> str:
    return _html.escape("" if t is None else str(t))


def request_mail(reqs: list[OrderRequest]) -> tuple[str, str, str]:
    """(subject, html, text) for the person in charge: one or more requests from one requester."""
    from .services.mail_reports import _table, _text_table, _wrap
    first = reqs[0]
    who = first.owner or first.owner_email
    head = ["품목", "제조사", "Cat#", "수량", "단가", "카테고리", "링크", "용도·메모"]
    rows = [[o.item, o.company, o.catalog, o.qty, o.price, o.category, o.link, o.note] for o in reqs]
    subject = f"[Asset Manager] 주문 요청 {len(reqs)}건 · {first.requester or '요청자'}"
    intro = f"{who}님, {first.requester or '요청자'}님이 {first.day}에 아래 {len(reqs)}개 품목의 주문을 요청했습니다."
    html = _wrap("주문 요청", f"<p>{_e(intro)}</p>" + _table(head, rows)
                 + "<p>앱의 ‘주문’ 탭에서 주문함 · 입고됨으로 바꾸면 요청자에게 알림이 갑니다.</p>")
    return subject, html, intro + "\n\n" + _text_table(head, rows)


def state_mail(req: OrderRequest) -> tuple[str, str, str]:
    from .services.mail_reports import _wrap
    when = {"주문함": req.ordered, "입고됨": req.received}.get(req.state, "")
    line = f"{req.requester}님이 {req.day}에 요청한 ‘{req.item}’({req.qty})이(가) {req.state} 상태가 되었습니다" \
           + (f" ({when})." if when else ".")
    subject = f"[Asset Manager] 주문 {req.state} · {req.item}"
    return subject, _wrap(f"주문 {req.state}", f"<p>{_e(line)}</p><p>담당자: {_e(req.owner)}</p>"), line


def from_cart_lines(lines, requester: str, requester_email: str, note: str) -> list[OrderRequest]:
    """Cart lines sent by mail, as order request records (already mailed)."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    return [OrderRequest(requester=requester, requester_email=requester_email, item=ln.name, company=ln.company,
                         catalog=ln.catalog, qty=ln.request_qty or "1", category=ln.category, owner=ln.owner_name,
                         owner_email=ln.owner_email, note=note, mailed=now) for ln in lines]
