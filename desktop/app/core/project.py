"""A project is one SQLite file (*.labproj) holding every category, item and detail.

All writes are committed immediately, so there is no separate "save" step;
"Save As" copies the file.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .models import DEFAULT_THRESHOLD, Category, Detail, Field, Item

SCHEMA_VERSION = 1
FILE_SUFFIX = ".labproj"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    scope TEXT NOT NULL DEFAULT 'common',
    name TEXT NOT NULL,
    icon TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    threshold REAL NOT NULL DEFAULT 1,
    kind TEXT NOT NULL DEFAULT 'managed'
);
CREATE TABLE IF NOT EXISTS fields (
    id INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    ftype TEXT NOT NULL DEFAULT 'text',
    role TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    subgroup TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT '',
    threshold REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_items_category ON items(category_id);
CREATE TABLE IF NOT EXISTS item_details (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    value TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_details_item ON item_details(item_id);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY,
    item_id INTEGER,
    category_id INTEGER,
    ts TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
    content, item_id UNINDEXED, tokenize = 'trigram'
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


class Project:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        # WAL lets a future Slack bot process read while the app writes.
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(_SCHEMA)
        self._migrate()
        # Receives every change (see _notify); a Drive folder uses it to write
        # the same change into the original spreadsheet.
        self.observer = None
        self.conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()

    def _notify(self, event: str, **data: Any) -> None:
        if self.observer is not None:
            self.observer(event, **data)

    def _migrate(self) -> None:
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(categories)")}
        if "kind" not in cols:
            self.conn.execute("ALTER TABLE categories ADD COLUMN kind TEXT NOT NULL DEFAULT 'managed'")
        fcols = {r["name"] for r in self.conn.execute("PRAGMA table_info(fields)")}
        if "virtual" not in fcols:
            self.conn.execute("ALTER TABLE fields ADD COLUMN virtual INTEGER NOT NULL DEFAULT 0")

    # ---- lifecycle -------------------------------------------------------
    @classmethod
    def create(cls, path: str | Path, name: str = "") -> "Project":
        path = Path(path)
        if path.exists():
            path.unlink()
        proj = cls(path)
        proj.set_meta("name", name or path.stem)
        proj.set_meta("created_at", _now())
        return proj

    def close(self) -> None:
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self.conn.close()
        except sqlite3.ProgrammingError:
            pass

    def save_as(self, new_path: str | Path) -> "Project":
        """Copy this project to a new file and return the project opened there."""
        new_path = Path(new_path)
        self.conn.commit()
        dest = sqlite3.connect(str(new_path))
        with dest:
            self.conn.backup(dest)
        dest.close()
        self.close()
        return Project(new_path)

    @property
    def name(self) -> str:
        return self.get_meta("name") or self.path.stem

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.conn.commit()

    # ---- categories ------------------------------------------------------
    @staticmethod
    def _category(row: sqlite3.Row) -> Category:
        return Category(
            id=row["id"], scope=row["scope"], name=row["name"], icon=row["icon"],
            sort_order=row["sort_order"], threshold=row["threshold"], kind=row["kind"],
        )

    def categories(self, scope: str | None = None, kind: str | None = None) -> list[Category]:
        sql = "SELECT * FROM categories WHERE 1 = 1"
        args: list = []
        if scope:
            sql += " AND scope = ?"
            args.append(scope)
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        sql += " ORDER BY scope, sort_order, id"
        return [self._category(r) for r in self.conn.execute(sql, args)]

    def category(self, category_id: int) -> Category | None:
        row = self.conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()
        return self._category(row) if row else None

    def add_category(self, scope: str, name: str, icon: str = "",
                     threshold: float = DEFAULT_THRESHOLD, kind: str = "managed") -> Category:
        order = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM categories WHERE scope = ?", (scope,)
        ).fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO categories(scope, name, icon, sort_order, threshold, kind) VALUES (?, ?, ?, ?, ?, ?)",
            (scope, name, icon, order, threshold, kind),
        )
        self._log(None, cur.lastrowid, "category_add", name)
        self.conn.commit()
        self._notify("category_add", category_id=cur.lastrowid)
        return self.category(cur.lastrowid)

    def update_category(self, category_id: int, **changes: Any) -> None:
        allowed = {"scope", "name", "icon", "sort_order", "threshold", "kind"}
        sets = {k: v for k, v in changes.items() if k in allowed}
        if not sets:
            return
        cols = ", ".join(f"{k} = ?" for k in sets)
        self.conn.execute(f"UPDATE categories SET {cols} WHERE id = ?", (*sets.values(), category_id))
        self.conn.commit()
        if "name" in sets:
            self.reindex_category(category_id)
        self._notify("category_update", category_id=category_id, changes=sets)

    def delete_category(self, category_id: int) -> None:
        self._notify("category_delete", category_id=category_id)
        ids = [r[0] for r in self.conn.execute("SELECT id FROM items WHERE category_id = ?", (category_id,))]
        self.conn.executemany("DELETE FROM items_fts WHERE item_id = ?", [(i,) for i in ids])
        self.conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        self.conn.commit()

    def reorder_categories(self, ordered_ids: Iterable[int]) -> None:
        for order, cid in enumerate(ordered_ids):
            self.conn.execute("UPDATE categories SET sort_order = ? WHERE id = ?", (order, cid))
        self.conn.commit()
        self._notify("category_reorder")

    # ---- fields ----------------------------------------------------------
    @staticmethod
    def _field(row: sqlite3.Row) -> Field:
        return Field(
            id=row["id"], category_id=row["category_id"], name=row["name"],
            ftype=row["ftype"], role=row["role"], sort_order=row["sort_order"],
            virtual=bool(row["virtual"]),
        )

    def fields(self, category_id: int) -> list[Field]:
        rows = self.conn.execute(
            "SELECT * FROM fields WHERE category_id = ? ORDER BY sort_order, id", (category_id,)
        )
        return [self._field(r) for r in rows]

    def field(self, field_id: int) -> Field | None:
        row = self.conn.execute("SELECT * FROM fields WHERE id = ?", (field_id,)).fetchone()
        return self._field(row) if row else None

    def field_by_role(self, category_id: int, role: str) -> Field | None:
        row = self.conn.execute(
            "SELECT * FROM fields WHERE category_id = ? AND role = ? ORDER BY sort_order LIMIT 1",
            (category_id, role),
        ).fetchone()
        return self._field(row) if row else None

    def add_field(self, category_id: int, name: str, ftype: str = "text", role: str = "") -> Field:
        order = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM fields WHERE category_id = ?", (category_id,)
        ).fetchone()[0]
        if role:
            self._clear_role(category_id, role)
        cur = self.conn.execute(
            "INSERT INTO fields(category_id, name, ftype, role, sort_order) VALUES (?, ?, ?, ?, ?)",
            (category_id, name, ftype, role, order),
        )
        self.conn.commit()
        self._notify("field_add", field_id=cur.lastrowid)
        return self.field(cur.lastrowid)

    def add_virtual_field(self, category_id: int, name: str, ftype: str = "text", role: str = "") -> Field:
        """A column computed by the app; not reported to the observer (never saved to files)."""
        order = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM fields WHERE category_id = ?", (category_id,)
        ).fetchone()[0]
        if role:
            self._clear_role(category_id, role)
        cur = self.conn.execute(
            "INSERT INTO fields(category_id, name, ftype, role, sort_order, virtual) VALUES (?, ?, ?, ?, ?, 1)",
            (category_id, name, ftype, role, order),
        )
        return self.field(cur.lastrowid)

    def set_virtual_values(self, field_id: int, values: dict[int, str]) -> None:
        """Fill a computed column for many items at once (no history, no observer)."""
        key = str(field_id)
        for item_id, value in values.items():
            item = self.item(item_id)
            if item is None:
                continue
            if value:
                item.values[key] = value
            else:
                item.values.pop(key, None)
            self.conn.execute("UPDATE items SET data = ? WHERE id = ?",
                              (json.dumps(item.values, ensure_ascii=False), item_id))
            self._index_item(item_id)
        self.conn.commit()

    def _clear_role(self, category_id: int, role: str) -> None:
        self.conn.execute(
            "UPDATE fields SET role = '' WHERE category_id = ? AND role = ?", (category_id, role)
        )

    def update_field(self, field_id: int, **changes: Any) -> None:
        f = self.field(field_id)
        if f is None:
            return
        allowed = {"name", "ftype", "role", "sort_order"}
        sets = {k: v for k, v in changes.items() if k in allowed and getattr(f, k) != v}
        if sets.get("role"):
            self._clear_role(f.category_id, sets["role"])
        if sets:
            cols = ", ".join(f"{k} = ?" for k in sets)
            self.conn.execute(f"UPDATE fields SET {cols} WHERE id = ?", (*sets.values(), field_id))
            self.conn.commit()
        if "name" in sets:
            self.reindex_category(f.category_id)
        if sets:
            self._notify("field_update", field_id=field_id, old=f, changes=sets)

    def delete_field(self, field_id: int) -> None:
        f = self.field(field_id)
        if f is None:
            return
        self._notify("field_delete", field=f)
        key = str(field_id)
        for item in self.items(f.category_id):
            if key in item.values:
                item.values.pop(key)
                self.conn.execute("UPDATE items SET data = ? WHERE id = ?",
                                  (json.dumps(item.values, ensure_ascii=False), item.id))
        self.conn.execute("DELETE FROM fields WHERE id = ?", (field_id,))
        self.conn.commit()
        self.reindex_category(f.category_id)

    def reorder_fields(self, ordered_ids: Iterable[int]) -> None:
        for order, fid in enumerate(ordered_ids):
            self.conn.execute("UPDATE fields SET sort_order = ? WHERE id = ?", (order, fid))
        self.conn.commit()
        self._notify("field_reorder")

    # ---- items -----------------------------------------------------------
    @staticmethod
    def _item(row: sqlite3.Row) -> Item:
        return Item(
            id=row["id"], category_id=row["category_id"], subgroup=row["subgroup"],
            values=json.loads(row["data"] or "{}"), status=row["status"],
            threshold=row["threshold"], created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def items(self, category_id: int, subgroup: str | None = None) -> list[Item]:
        sql = "SELECT * FROM items WHERE category_id = ?"
        args: list = [category_id]
        if subgroup is not None:
            sql += " AND subgroup = ?"
            args.append(subgroup)
        sql += " ORDER BY id"
        return [self._item(r) for r in self.conn.execute(sql, args)]

    def item(self, item_id: int) -> Item | None:
        row = self.conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return self._item(row) if row else None

    def subgroups(self, category_id: int) -> list[str]:
        """Groups in display order: the declared ones (e.g. a workbook's sheets), then any other."""
        rows = self.conn.execute(
            "SELECT subgroup, MIN(id) FROM items WHERE category_id = ? GROUP BY subgroup ORDER BY MIN(id)",
            (category_id,),
        )
        used = [r[0] for r in rows]
        declared = self._declared_groups(category_id)
        return declared + [g for g in used if g not in declared]

    def _declared_groups(self, category_id: int) -> list[str]:
        try:
            return json.loads(self.get_meta(f"groups:{category_id}") or "[]")
        except ValueError:
            return []

    def set_declared_groups(self, category_id: int, groups: list[str]) -> None:
        self.set_meta(f"groups:{category_id}", json.dumps(groups, ensure_ascii=False))

    # ---- owners (담당자) and the briefing schedule ---------------------------
    def owner(self, category_id: int) -> dict:
        """{'name': ..., 'email': ...} of the person in charge of a category, or {}."""
        try:
            return json.loads(self.get_meta(f"owner:{category_id}") or "{}")
        except ValueError:
            return {}

    def set_owner(self, category_id: int, name: str, email: str, notify: bool = True) -> None:
        value = {"name": name.strip(), "email": email.strip()} if (name.strip() or email.strip()) else {}
        self.set_meta(f"owner:{category_id}", json.dumps(value, ensure_ascii=False))
        if notify:
            self._notify("category_owner", category_id=category_id, owner=value)

    def setting(self, key: str, default=None):
        """A shared setting (kept in the Drive folder's settings file in folder mode)."""
        raw = self.get_meta(f"setting:{key}")
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except ValueError:
            return default

    def set_setting(self, key: str, value, notify: bool = True) -> None:
        self.set_meta(f"setting:{key}", json.dumps(value, ensure_ascii=False))
        if notify:
            self._notify("setting", key=key, value=value)

    def members(self) -> list[dict]:
        """Lab members: [{'name': ..., 'email': ...}] in the order they were added."""
        try:
            return json.loads(self.get_meta("members") or "[]")
        except ValueError:
            return []

    def set_members(self, members: list[dict], notify: bool = True) -> None:
        clean = []
        for m in members:
            if not (m.get("name", "").strip() or m.get("email", "").strip()):
                continue
            row = {"name": m.get("name", "").strip(), "email": m.get("email", "").strip()}
            if (m.get("english") or "").strip():  # 영문 이름: the lab-fee sheet uses it
                row["english"] = m["english"].strip()
            clean.append(row)
        self.set_meta("members", json.dumps(clean, ensure_ascii=False))
        if notify:
            self._notify("members", members=clean)

    def order_contact(self) -> dict:
        """Who gets order requests for items whose category has no owner."""
        try:
            return json.loads(self.get_meta("order_contact") or "{}")
        except ValueError:
            return {}

    def set_order_contact(self, value: dict, notify: bool = True) -> None:
        self.set_meta("order_contact", json.dumps(value or {}, ensure_ascii=False))
        if notify:
            self._notify("order_contact", value=value or {})

    def briefing(self) -> dict:
        """{'enabled': bool, 'time': 'HH:MM', 'weekdays': [0..6]} (0 = Monday)."""
        try:
            return json.loads(self.get_meta("briefing") or "{}")
        except ValueError:
            return {}

    def set_briefing(self, value: dict, notify: bool = True) -> None:
        self.set_meta("briefing", json.dumps(value, ensure_ascii=False))
        if notify:
            self._notify("briefing", value=value)

    def add_subgroup(self, category_id: int, name: str) -> None:
        groups = self.subgroups(category_id)
        if name in groups:
            return
        self.set_declared_groups(category_id, groups + [name])
        self._log(None, category_id, "group_add", name)
        self.conn.commit()
        self._notify("subgroup_add", category_id=category_id, name=name)

    def rename_subgroup(self, category_id: int, old: str, new: str) -> None:
        if not new or old == new or new in self.subgroups(category_id):
            return
        groups = [new if g == old else g for g in self.subgroups(category_id)]
        self.conn.execute("UPDATE items SET subgroup = ? WHERE category_id = ? AND subgroup = ?",
                          (new, category_id, old))
        self.set_declared_groups(category_id, groups)
        self._log(None, category_id, "group_rename", f"{old} → {new}")
        self.reindex_category(category_id)
        self._notify("subgroup_rename", category_id=category_id, old=old, new=new)

    def delete_subgroup(self, category_id: int, name: str) -> None:
        """Remove a group and every item in it."""
        self._notify("subgroup_delete", category_id=category_id, name=name)
        ids = [r[0] for r in self.conn.execute(
            "SELECT id FROM items WHERE category_id = ? AND subgroup = ?", (category_id, name))]
        self.conn.executemany("DELETE FROM items_fts WHERE item_id = ?", [(i,) for i in ids])
        self.conn.execute("DELETE FROM items WHERE category_id = ? AND subgroup = ?", (category_id, name))
        self.set_declared_groups(category_id, [g for g in self.subgroups(category_id) if g != name])
        self._log(None, category_id, "group_delete", name)
        self.conn.commit()

    def count_items(self, category_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM items WHERE category_id = ?", (category_id,)
        ).fetchone()[0]

    def count_by_subgroup(self, category_id: int) -> dict[str, int]:
        """Items per sheet (subgroup) of one category."""
        return dict(self.conn.execute(
            "SELECT subgroup, COUNT(*) FROM items WHERE category_id = ? GROUP BY subgroup", (category_id,)
        ).fetchall())

    def add_item(self, category_id: int, values: dict[int | str, Any] | None = None,
                 subgroup: str = "", log: bool = True, commit: bool = True) -> int:
        data = {str(k): _to_text(v) for k, v in (values or {}).items() if _to_text(v) != ""}
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO items(category_id, subgroup, data, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (category_id, subgroup, json.dumps(data, ensure_ascii=False), now, now),
        )
        item_id = cur.lastrowid
        self._index_item(item_id)
        if log:
            self._log(item_id, category_id, "item_add", self.item_title(item_id))
        if commit:
            self.conn.commit()
        self._notify("item_add", item_id=item_id)
        return item_id

    def set_value(self, item_id: int, field_id: int, value: Any) -> None:
        item = self.item(item_id)
        if item is None:
            return
        key = str(field_id)
        old = item.values.get(key, "")
        new = _to_text(value)
        if old == new:
            return
        if new == "":
            item.values.pop(key, None)
        else:
            item.values[key] = new
        self.conn.execute(
            "UPDATE items SET data = ?, updated_at = ? WHERE id = ?",
            (json.dumps(item.values, ensure_ascii=False), _now(), item_id),
        )
        f = self.field(field_id)
        self._log(item_id, item.category_id, "value", f"{f.name if f else key}: {old} → {new}")
        self._index_item(item_id)
        self.conn.commit()
        self._notify("value", item_id=item_id, field_id=field_id, old=old, new=new)

    def update_item(self, item_id: int, **changes: Any) -> None:
        allowed = {"subgroup", "status", "threshold"}
        sets = {k: v for k, v in changes.items() if k in allowed}
        if not sets:
            return
        item = self.item(item_id)
        cols = ", ".join(f"{k} = ?" for k in sets)
        self.conn.execute(
            f"UPDATE items SET {cols}, updated_at = ? WHERE id = ?", (*sets.values(), _now(), item_id)
        )
        self._log(item_id, item.category_id if item else None, "item",
                  ", ".join(f"{k}={v}" for k, v in sets.items()))
        self._index_item(item_id)
        self.conn.commit()
        self._notify("item_update", item_id=item_id, changes=sets)

    def delete_items(self, item_ids: Iterable[int]) -> None:
        for item_id in item_ids:
            item = self.item(item_id)
            if item is None:
                continue
            self._notify("item_delete", item=item)
            self._log(None, item.category_id, "item_delete", self.item_title(item_id))
            self.conn.execute("DELETE FROM items_fts WHERE item_id = ?", (item_id,))
            self.conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        self.conn.commit()

    def item_title(self, item_id: int) -> str:
        item = self.item(item_id)
        if item is None:
            return ""
        f = self.field_by_role(item.category_id, "name")
        if f and item.get(f.id):
            return item.get(f.id)
        for value in item.values.values():
            if value:
                return value
        return f"#{item_id}"

    # ---- details (free-form conditions / status per item) ----------------
    def details(self, item_id: int) -> list[Detail]:
        rows = self.conn.execute(
            "SELECT * FROM item_details WHERE item_id = ? ORDER BY sort_order, id", (item_id,)
        )
        return [Detail(r["id"], r["item_id"], r["key"], r["value"], r["sort_order"]) for r in rows]

    def add_detail(self, item_id: int, key: str, value: str = "") -> int:
        order = self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM item_details WHERE item_id = ?", (item_id,)
        ).fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO item_details(item_id, key, value, sort_order) VALUES (?, ?, ?, ?)",
            (item_id, key, value, order),
        )
        self._index_item(item_id)
        self.conn.commit()
        self._notify("details", item_id=item_id)
        return cur.lastrowid

    def update_detail(self, detail_id: int, key: str, value: str) -> None:
        row = self.conn.execute("SELECT item_id FROM item_details WHERE id = ?", (detail_id,)).fetchone()
        if row is None:
            return
        self.conn.execute("UPDATE item_details SET key = ?, value = ? WHERE id = ?", (key, value, detail_id))
        self._index_item(row["item_id"])
        self.conn.commit()
        self._notify("details", item_id=row["item_id"])

    def delete_detail(self, detail_id: int) -> None:
        row = self.conn.execute("SELECT item_id FROM item_details WHERE id = ?", (detail_id,)).fetchone()
        if row is None:
            return
        self.conn.execute("DELETE FROM item_details WHERE id = ?", (detail_id,))
        self._index_item(row["item_id"])
        self.conn.commit()
        self._notify("details", item_id=row["item_id"])

    # ---- history ---------------------------------------------------------
    def _log(self, item_id: int | None, category_id: int | None, action: str, detail: str) -> None:
        self.conn.execute(
            "INSERT INTO history(item_id, category_id, ts, action, detail) VALUES (?, ?, ?, ?, ?)",
            (item_id, category_id, _now(), action, detail),
        )

    def history(self, item_id: int | None = None, limit: int = 50) -> list[sqlite3.Row]:
        if item_id is None:
            sql, args = "SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)
        else:
            sql, args = "SELECT * FROM history WHERE item_id = ? ORDER BY id DESC LIMIT ?", (item_id, limit)
        return list(self.conn.execute(sql, args))

    # ---- search index ----------------------------------------------------
    def _index_text(self, item: Item) -> str:
        cat = self.category(item.category_id)
        parts = [cat.name if cat else "", item.subgroup]
        names = {f.id: f.name for f in self.fields(item.category_id)}
        for key, value in item.values.items():
            parts.append(f"{names.get(int(key), '')} {value}")
        for d in self.details(item.id):
            parts.append(f"{d.key} {d.value}")
        return "\n".join(p for p in parts if p)

    def _index_item(self, item_id: int) -> None:
        item = self.item(item_id)
        self.conn.execute("DELETE FROM items_fts WHERE item_id = ?", (item_id,))
        if item is not None:
            self.conn.execute(
                "INSERT INTO items_fts(content, item_id) VALUES (?, ?)", (self._index_text(item), item_id)
            )

    def reindex_category(self, category_id: int) -> None:
        for item in self.items(category_id):
            self._index_item(item.id)
        self.conn.commit()

    def commit(self) -> None:
        self.conn.commit()


def copy_project(src: str | Path, dest: str | Path) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest
