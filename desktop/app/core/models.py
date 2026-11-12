"""Plain data classes shared by the UI, services and future integrations."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCOPES: list[tuple[str, str]] = [
    ("common", "Common Asset"),
    ("personal", "Personal Asset"),
]
SCOPE_LABELS = dict(SCOPES)

# Column types a user can pick for a field.
FIELD_TYPES = ["text", "number", "date", "bool"]

# What a field means to the app. Only one field per role per category.
FIELD_ROLES = {
    "": "(없음)",
    "name": "이름",
    "quantity": "수량",
    "stock_flag": "재고 유무 표시",
    "location": "보관 위치",
    "owner": "담당자",
    "catalog": "Cat#",
    "date": "날짜",
}

# Suggested names when adding a category; users can type any name.
PRESET_CATEGORIES = [
    "단백질 (Protein)",
    "항체 (Antibody)",
    "효소 (Enzyme)",
    "제한효소 (Restriction enzyme)",
    "Reagent",
    "플라스미드 (Plasmid)",
    "Cell line",
    "RNA",
    "Peptide",
    "Bacteria",
    "Virus",
    "Kit",
]

# How a category's stock is shown on the dashboard. Users pick one per category.
#  managed: consumables you reorder (quantity vs. low-stock threshold → 충분/부족/소진)
#  status:  things you only need to know you have (보유/없음), no low-stock alerts
CATEGORY_KINDS = {
    "managed": "재고 관리",
    "status": "재고 현황",
}
KIND_DESCRIPTIONS = {
    "managed": "수량이 부족 기준 이하로 떨어지면 알려줍니다 (시약, 효소, 항체 등 소모품).",
    "status": "보유 여부와 수량만 보여주고 부족 알림은 하지 않습니다 (플라스미드, Cell line 등).",
}

STOCK_STATES = {
    "ok": "충분",
    "soon": "주문 임박",
    "low": "부족",
    "out": "소진",
    "unknown": "미확인",
}

STATUS_STATES = {
    "ok": "보유",
    "soon": "보유",
    "low": "보유",
    "out": "없음",
    "unknown": "미확인",
}

DEFAULT_THRESHOLD = 1.0


def state_label(state: str, kind: str = "managed") -> str:
    return (STATUS_STATES if kind == "status" else STOCK_STATES)[state]


@dataclass
class Category:
    id: int
    scope: str
    name: str
    icon: str = ""
    sort_order: int = 0
    threshold: float = DEFAULT_THRESHOLD
    kind: str = "managed"


@dataclass
class Field:
    id: int
    category_id: int
    name: str
    ftype: str = "text"
    role: str = ""
    sort_order: int = 0
    virtual: bool = False  # computed in the app (e.g. from order records), never written to files


@dataclass
class Item:
    id: int
    category_id: int
    subgroup: str = ""
    values: dict[str, Any] = field(default_factory=dict)  # field id (str) -> value
    status: str = ""  # manual stock status override, a STOCK_STATES key or ""
    threshold: float | None = None  # per-item override of the category threshold
    created_at: str = ""
    updated_at: str = ""

    def get(self, field_id: int, default: Any = "") -> Any:
        return self.values.get(str(field_id), default)


@dataclass
class Detail:
    id: int
    item_id: int
    key: str
    value: str
    sort_order: int = 0
