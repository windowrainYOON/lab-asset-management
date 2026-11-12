"""Write a category (or a shortage list) back to CSV / XLSX."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable

from .models import state_label
from .project import Project
from .services.inventory import Report, stock_info


def _write(path: Path, headers: list[str], rows: list[list[str]], sheet: str = "Sheet1") -> None:
    if path.suffix.lower() == ".csv":
        with path.open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(headers)
            w.writerows(rows)
        return
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = sheet[:31] or "Sheet1"
    ws.append(headers)
    for r in rows:
        ws.append(r)
    wb.save(path)


def export_category(project: Project, category_id: int, path: str | Path,
                    item_ids: Iterable[int] | None = None) -> int:
    path = Path(path)
    cat = project.category(category_id)
    fields = project.fields(category_id)
    wanted = set(item_ids) if item_ids is not None else None
    rows = []
    for item in project.items(category_id):
        if wanted is not None and item.id not in wanted:
            continue
        state = state_label(stock_info(project, cat, item).state, cat.kind)
        rows.append([item.subgroup, state] + [item.get(f.id) for f in fields])
    _write(path, ["Group", "재고 상태"] + [f.name for f in fields], rows, cat.name)
    return len(rows)


def export_shortages(report: Report, path: str | Path) -> int:
    rows = []
    for r in report.shortages:
        qty = "" if r.info.quantity is None else f"{r.info.quantity:g}{r.info.unit}"
        rows.append([r.category.name, r.subgroup, r.name, state_label(r.info.state, r.category.kind), qty,
                     "" if r.info.threshold is None else f"{r.info.threshold:g}", r.location])
    _write(Path(path), ["카테고리", "그룹", "이름", "상태", "수량", "부족 기준", "위치"], rows, "부족 재고")
    return len(rows)
