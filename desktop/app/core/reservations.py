"""Equipment reservations (장비 예약), kept in one workbook in Drive:

  장비: ID | 이름 | 위치 | 담당자 | 상태 | 색 | 단위(분) | 최대(시간) | 메모
  예약: ID | 장비 ID | 장비 | 예약자 | 시작 | 끝 | 용도 | 알림(분) | 만든 시각

시작/끝 are "YYYY-MM-DD HH:MM". 상태 is 사용 가능 or 점검 중 (no new bookings).
Two bookings of one machine may not overlap.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .records import RecordBoard, new_id

RES_NAME = "장비 예약.xlsx"
EQ, RES = "장비", "예약"
EQ_HEADERS = ["ID", "이름", "위치", "담당자", "상태", "색", "단위(분)", "최대(시간)", "메모"]
RES_HEADERS = ["ID", "장비 ID", "장비", "예약자", "시작", "끝", "용도", "알림(분)", "만든 시각"]
STATES = ("사용 가능", "점검 중")
COLORS = ["#0a84ff", "#30a46c", "#f76b15", "#8e4ec6", "#12a594", "#e5484d", "#ffb224", "#3e63dd"]
FMT = "%Y-%m-%d %H:%M"


def parse_dt(text: str) -> datetime | None:
    text = (text or "").strip().replace("T", " ")
    for fmt in (FMT, "%Y-%m-%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19] if fmt.endswith("%S") else text[:16], fmt)
        except ValueError:
            continue
    return None


@dataclass
class Equipment:
    id: str
    name: str
    location: str = ""
    owner: str = ""
    state: str = "사용 가능"
    color: str = ""
    step: int = 30
    max_hours: float = 0  # 0 = no limit
    note: str = ""

    @property
    def available(self) -> bool:
        return self.state != "점검 중"

    @classmethod
    def from_row(cls, r: dict, i: int = 0) -> "Equipment":
        def num(v, d):
            try:
                return type(d)(float(v))
            except (TypeError, ValueError):
                return d
        return cls(r.get("ID") or new_id(), r.get("이름", ""), r.get("위치", ""), r.get("담당자", ""),
                   r.get("상태") or "사용 가능", r.get("색") or COLORS[i % len(COLORS)],
                   num(r.get("단위(분)"), 30) or 30, num(r.get("최대(시간)"), 0.0), r.get("메모", ""))

    def to_row(self) -> dict:
        return {"ID": self.id, "이름": self.name, "위치": self.location, "담당자": self.owner, "상태": self.state,
                "색": self.color, "단위(분)": str(self.step), "최대(시간)": _num_text(self.max_hours),
                "메모": self.note}


@dataclass
class Reservation:
    id: str
    equipment_id: str
    equipment: str
    person: str
    start: datetime
    end: datetime
    purpose: str = ""
    notify_min: int = 0
    created: str = ""

    @classmethod
    def from_row(cls, r: dict) -> "Reservation | None":
        s, e = parse_dt(r.get("시작", "")), parse_dt(r.get("끝", ""))
        if s is None or e is None:
            return None
        try:
            n = int(float(r.get("알림(분)") or 0))
        except ValueError:
            n = 0
        return cls(r.get("ID") or new_id(), r.get("장비 ID", ""), r.get("장비", ""), r.get("예약자", ""),
                   s, e, r.get("용도", ""), n, r.get("만든 시각", ""))

    def to_row(self) -> dict:
        return {"ID": self.id, "장비 ID": self.equipment_id, "장비": self.equipment, "예약자": self.person,
                "시작": self.start.strftime(FMT), "끝": self.end.strftime(FMT), "용도": self.purpose,
                "알림(분)": str(self.notify_min or ""), "만든 시각": self.created or datetime.now().strftime(FMT)}

    def overlaps(self, other: "Reservation") -> bool:
        return (self.equipment_id == other.equipment_id and self.id != other.id
                and self.start < other.end and other.start < self.end)

    def when(self) -> str:
        same = self.start.date() == self.end.date()
        return (f"{self.start:%m/%d %H:%M}–{self.end:%H:%M}" if same
                else f"{self.start:%m/%d %H:%M} – {self.end:%m/%d %H:%M}")


def _num_text(v: float) -> str:
    return "" if not v else (str(int(v)) if float(v).is_integer() else str(v))


class ReservationBoard(RecordBoard):
    SPEC = {EQ: EQ_HEADERS, RES: RES_HEADERS}
    WIDTHS = {EQ: [10, 24, 12, 12, 10, 10, 9, 9, 30], RES: [10, 10, 22, 12, 17, 17, 30, 9, 17]}
    NAME = RES_NAME
    CACHE = "reservations.xlsx"

    def equipment(self) -> list[Equipment]:
        return [Equipment.from_row(r, i) for i, r in enumerate(self.rows(EQ))]

    def equipment_by_id(self, eid: str) -> Equipment | None:
        return next((e for e in self.equipment() if e.id == eid), None)

    def reservations(self, equipment_id: str | None = None) -> list[Reservation]:
        out = [r for r in (Reservation.from_row(x) for x in self.rows(RES)) if r is not None]
        if equipment_id:
            out = [r for r in out if r.equipment_id == equipment_id]
        return sorted(out, key=lambda r: r.start)

    def between(self, start: datetime, end: datetime) -> list[Reservation]:
        return [r for r in self.reservations() if r.start < end and start < r.end]

    def on_day(self, day: date, person: str | None = None) -> list[Reservation]:
        s = datetime.combine(day, datetime.min.time())
        out = self.between(s, s + timedelta(days=1))
        if person:
            out = [r for r in out if same_person(r.person, person)]
        return out

    def conflicts(self, res: Reservation) -> list[Reservation]:
        return [r for r in self.reservations(res.equipment_id) if res.overlaps(r)]

    def check(self, res: Reservation) -> str:
        """'' when the booking can be saved, else why not."""
        eq = self.equipment_by_id(res.equipment_id)
        if eq is None:
            return "장비를 골라 주세요."
        if not eq.available:
            return f"{eq.name}은(는) 점검 중이라 예약할 수 없습니다."
        if not res.person.strip():
            return "예약자를 골라 주세요."
        if res.end <= res.start:
            return "끝나는 시간이 시작보다 늦어야 합니다."
        if eq.max_hours and (res.end - res.start) > timedelta(hours=eq.max_hours):
            return f"{eq.name}은(는) 한 번에 최대 {_num_text(eq.max_hours)}시간까지 예약할 수 있습니다."
        clash = self.conflicts(res)
        if clash:
            c = clash[0]
            return f"{c.person}님의 예약({c.when()})과 겹칩니다."
        return ""

    # edits
    def save_equipment(self, eq: Equipment) -> None:
        self.upsert(EQ, eq.to_row())

    def delete_equipment(self, eid: str) -> None:
        def change(st):
            st[EQ] = [r for r in st[EQ] if r.get("ID") != eid]
            st[RES] = [r for r in st[RES] if r.get("장비 ID") != eid]
            return st
        self.edit(change)

    def save_reservation(self, res: Reservation) -> None:
        eq = self.equipment_by_id(res.equipment_id)
        if eq is not None:
            res.equipment = eq.name
        self.upsert(RES, res.to_row())

    def delete_reservation(self, rid: str) -> None:
        self.delete(RES, rid)

    # the 일정 calendar
    def calendar_events(self) -> list:
        from ..integrations.gcalendar import Event
        colors = {e.id: e.color for e in self.equipment()}
        out = []
        for r in self.reservations():
            out.append(Event(id=f"res:{r.id}", title=f"{r.equipment} · {r.person}", start=r.start, end=r.end,
                             description=r.purpose, calendar_id="reservations", calendar_name="장비 예약",
                             color=colors.get(r.equipment_id, COLORS[0]),
                             extra={"reservation": r.id, "equipment": r.equipment_id}))
        return out


def same_person(a: str, b: str) -> bool:
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    return bool(a) and a == b
