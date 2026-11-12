"""Left sidebar tabs: one list of sections, each with its tabs, and the user's hidden ones.

Every tab has a stable key so the choice survives reloads (category ids are rebuilt
from the Drive folder on each launch, so categories are keyed by scope and name):

- "dash:common", "dash:personal", "schedule", "work", "reservations", "orders", "budget", "notices", "attendance"
- "cat:<scope>:<category name>"
- "seq:common", "seq:personal"

Which tabs are hidden is a per-user choice, kept in the local settings file.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import settings
from .models import SCOPES

HOME_TABS = [
    ("dash:common", "공용 대시보드"),
    ("dash:personal", "개인 품목 대시보드"),
    ("schedule", "일정"),
    ("work", "업무"),
    ("requests", "업무 요청"),
    ("reservations", "장비 예약"),
    ("orders", "주문"),
    ("budget", "예산"),
    ("notices", "공지사항"),
    ("attendance", "출퇴근 기록"),
]
HOME_TITLE = "메뉴"
HIDDEN_TITLE = "숨긴 항목"

# Sequence Map: the SnapGene maps in the Drive folder, one tab per subfolder.
# Both are shown to everyone, so they are not split by owner.
SEQUENCE_TITLE = "Sequence Map"
SEQUENCE_TABS = [
    ("seq:common", "Common Plasmid"),
    ("seq:personal", "Personal Plasmid"),
]


@dataclass
class Tab:
    key: str
    label: str
    category_id: int | None = None
    section: str = ""  # title of the section it belongs to


@dataclass
class Section:
    key: str  # "home", "common", "personal"
    title: str
    tabs: list[Tab] = field(default_factory=list)
    scope: str | None = None  # asset sections take new categories


def category_key(scope: str, name: str) -> str:
    return f"cat:{scope}:{name}"


def all_sections(project) -> list[Section]:
    out = [Section("home", HOME_TITLE, [Tab(k, label, section=HOME_TITLE) for k, label in HOME_TABS])]
    for scope, label in SCOPES:
        sec = Section(scope, label, scope=scope)  # no ALL CAPS labels (typography-desktop.md)
        for cat in project.categories(scope):
            sec.tabs.append(Tab(category_key(scope, cat.name), cat.name, cat.id, label))
        out.append(sec)
    out.append(Section("sequence", SEQUENCE_TITLE,
                       [Tab(k, label, section=SEQUENCE_TITLE) for k, label in SEQUENCE_TABS]))
    return out


def split(sections: list[Section], hidden: set[str]) -> tuple[list[Section], list[Tab]]:
    """Sections with only their shown tabs, and the hidden tabs in sidebar order."""
    shown, hid = [], []
    for sec in sections:
        keep = [t for t in sec.tabs if t.key not in hidden]
        hid += [t for t in sec.tabs if t.key in hidden]
        shown.append(Section(sec.key, sec.title, keep, sec.scope))
    return shown, hid


def hidden_tabs() -> set[str]:
    return set(settings.load_settings().get("hidden_tabs", []))


def set_hidden_tabs(keys) -> None:
    data = settings.load_settings()
    data["hidden_tabs"] = sorted(set(keys))
    settings.save_settings(data)


def set_tab_hidden(key: str, hidden: bool) -> None:
    keys = hidden_tabs()
    (keys.add if hidden else keys.discard)(key)
    set_hidden_tabs(keys)


def rename_tab(old: str, new: str) -> None:
    """Keep a renamed category hidden."""
    keys = hidden_tabs()
    if old in keys:
        keys.discard(old)
        keys.add(new)
        set_hidden_tabs(keys)


def collapsed_sections() -> set[str]:
    data = settings.load_settings()
    return set(data.get("collapsed_sections", ["hidden"]))  # 숨긴 항목 starts folded


def set_section_collapsed(key: str, collapsed: bool) -> None:
    keys = collapsed_sections()
    (keys.add if collapsed else keys.discard)(key)
    data = settings.load_settings()
    data["collapsed_sections"] = sorted(keys)
    settings.save_settings(data)
