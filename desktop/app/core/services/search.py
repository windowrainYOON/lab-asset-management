"""Unified search across every category.

Query syntax (also intended for a future Slack bot):
  BamHI                 free text, matches any value, detail or note
  location:-80          a field whose name contains "location" holds "-80"
  vials<2  qty>=3       numeric comparison on a field ("qty" = the quantity field)
  scope:personal        only Common or Personal assets
  cat:plasmid           only categories whose name contains "plasmid"
  state:low             stock state (ok / low / out / unknown, or 충분/부족/소진)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import STATUS_STATES, STOCK_STATES, Category, Item
from ..project import Project
from .inventory import parse_quantity, stock_info

_TOKEN = re.compile(r'"([^"]+)"|(\S+)')
_FILTER = re.compile(r"^([^:<>=]+)(:|<=|>=|<|>|=)(.+)$")
_STATE_ALIASES = ({"보유": "ok", "없음": "out", "임박": "soon", "주문임박": "soon"} | {v: k for k, v in STOCK_STATES.items()}
                  | {k: k for k in STOCK_STATES})


@dataclass
class Query:
    terms: list[str] = field(default_factory=list)
    filters: list[tuple[str, str, str]] = field(default_factory=list)  # (field, op, value)
    scope: str | None = None
    category: str | None = None
    state: str | None = None


def parse_query(text: str) -> Query:
    q = Query()
    for quoted, bare in _TOKEN.findall(text or ""):
        token = quoted or bare
        m = _FILTER.match(token) if not quoted else None
        if m and not re.match(r"^-?\d", token):
            key, op, value = m.group(1).lower(), m.group(2), m.group(3)
            if key == "scope" and op == ":":
                q.scope = "personal" if value.lower().startswith(("p", "개")) else "common"
            elif key in ("cat", "category") and op == ":":
                q.category = value.lower()
            elif key in ("state", "상태") and op == ":":
                q.state = _STATE_ALIASES.get(value.lower(), value.lower())
            else:
                q.filters.append((key, op, value))
        else:
            q.terms.append(token)
    return q


@dataclass
class SearchHit:
    item: Item
    category: Category
    title: str
    snippet: str
    state: str


def _candidate_ids(project: Project, terms: list[str]) -> set[int] | None:
    if not terms:
        return None
    ids: set[int] | None = None
    for term in terms:
        if len(term) >= 3:
            rows = project.conn.execute(
                "SELECT item_id FROM items_fts WHERE items_fts MATCH ?", ('"' + term.replace('"', '""') + '"',)
            )
        else:  # trigram index needs 3+ characters
            rows = project.conn.execute(
                "SELECT item_id FROM items_fts WHERE content LIKE ?", (f"%{term}%",)
            )
        found = {int(r[0]) for r in rows}
        ids = found if ids is None else ids & found
    return ids


def _match_filter(value: str, op: str, target: str) -> bool:
    if op == ":":
        return target.lower() in value.lower()
    num, _ = parse_quantity(value)
    try:
        t = float(target)
    except ValueError:
        return op == "=" and value.lower() == target.lower()
    if num is None:
        return False
    return {"<": num < t, "<=": num <= t, ">": num > t, ">=": num >= t, "=": num == t}[op]


def search(project: Project, text: str, limit: int = 200) -> list[SearchHit]:
    q = parse_query(text)
    if not (q.terms or q.filters or q.state or q.category or q.scope):
        return []
    ids = _candidate_ids(project, q.terms)
    if ids is not None and not ids:
        return []
    hits: list[SearchHit] = []
    cache: dict = {}
    for cat in project.categories(q.scope):
        if q.category and q.category not in cat.name.lower():
            continue
        fields = project.fields(cat.id)
        qty_f = project.field_by_role(cat.id, "quantity")
        for item in project.items(cat.id):
            if ids is not None and item.id not in ids:
                continue
            ok = True
            for key, op, target in q.filters:
                if key in ("qty", "수량") and qty_f:
                    candidates = [qty_f]
                else:
                    candidates = [f for f in fields if key in f.name.lower()]
                if not any(_match_filter(item.get(f.id), op, target) for f in candidates):
                    ok = False
                    break
            if not ok:
                continue
            info = stock_info(project, cat, item, cache)
            if q.state and info.state != q.state:
                continue
            hits.append(SearchHit(item, cat, project.item_title(item.id),
                                  _snippet(item, fields, q.terms), info.state))
            if len(hits) >= limit:
                return hits
    return hits


def _snippet(item: Item, fields, terms: list[str]) -> str:
    """Matched values first, then quantity and location so a hit is useful at a glance."""
    lowered = [t.lower() for t in terms]
    picked: list = []
    for f in fields:
        v = item.get(f.id)
        if v and f.role != "name" and lowered and any(t in v.lower() for t in lowered):
            picked.append(f)
    for role in ("quantity", "location", "catalog", "owner"):
        for f in fields:
            if f.role == role and item.get(f.id) and f not in picked:
                picked.append(f)
    if not picked:
        picked = [f for f in fields if item.get(f.id) and f.role != "name"]
    return " · ".join(f"{f.name}: {item.get(f.id)}" for f in picked[:4])
