"""통합 검색: inventory items plus every other record (주문 요청 · 주문 기록 · 장비 예약 · 공지 ·
업무 · 일정 · 예산). Records open read-only in the inspector; ‘열기’ goes to their tab."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QScrollArea, QTreeWidget, QTreeWidgetItem,
                               QVBoxLayout, QWidget)

from ..core.models import SCOPE_LABELS, state_label
from ..core.project import Project
from ..core.services.record_search import RecordHit, search_records
from ..core.services.search import search
from .inset import InsetGroup, inset_title, value_label
from .table_model import state_dot
from .theme import tokens
from .widgets import InspectorPanel

HELP = ("검색 예시:  BamHI   ·   HeLa vials<2   ·   cat:plasmid state:소진   ·   "
        "location:-80   ·   scope:personal   ·   \"pcDNA3.1 Flag\"   ·   재고 말고도 주문 요청, 주문 기록, "
        "장비 예약, 공지, 업무, 일정, 예산 장부까지 찾습니다 (필터를 쓰면 재고만)")

REC = Qt.UserRole + 1


class RecordInspector(QWidget):
    """Read-only view of one record hit."""
    open_record = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hit: RecordHit | None = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*[tokens.space("row-pad")] * 4)
        self.kind = QLabel()
        self.kind.setObjectName("muted")
        lay.addWidget(self.kind)
        self.title = inset_title("")
        self.title.setWordWrap(True)
        lay.addWidget(self.title)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        lay.addWidget(self.scroll, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.open_btn = QPushButton("열기")
        self.open_btn.setDefault(True)
        self.open_btn.clicked.connect(lambda: self.hit and self.open_record.emit(self.hit))
        row.addWidget(self.open_btn)
        lay.addLayout(row)

    def show_hit(self, hit: RecordHit) -> None:
        self.hit = hit
        self.kind.setText(hit.group + " · 읽기 전용")
        self.title.setText(hit.title)
        group = InsetGroup()
        for k, v in hit.fields:
            lab = value_label(v)
            lab.setWordWrap(True)
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lab.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            group.add_row(k, lab, expand=True)
        holder = QWidget()
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(group)
        hl.addStretch(1)
        self.scroll.setWidget(holder)
        self.open_btn.setVisible(bool(hit.tab))
        self.open_btn.setText(f"{hit.group} 탭에서 열기" if hit.tab else "열기")


class SearchPage(QWidget):
    open_item = Signal(int, int)
    open_record = Signal(object)  # RecordHit

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.sources = lambda: {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*[tokens.space("margin")] * 4)  # window margin 20
        self.title = QLabel("통합 검색")
        self.title.setObjectName("pageTitle")
        lay.addWidget(self.title)
        hint = QLabel(HELP)
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["이름", "상태", "내용"])
        self.tree.setColumnWidth(0, 320)
        self.tree.setColumnWidth(1, 90)
        self.tree.itemActivated.connect(self._activated)
        self.tree.currentItemChanged.connect(self._selected)
        lay.addWidget(self.tree, 1)

        self.inspector_panel = InspectorPanel(wanted=False)
        il = QVBoxLayout(self.inspector_panel)
        il.setContentsMargins(0, 0, 0, 0)
        self.inspector = RecordInspector(self)
        self.inspector.open_record.connect(self.open_record)
        il.addWidget(self.inspector)
        self.inspector_panel.setMinimumWidth(320)
        self.inspector_panel.setMaximumWidth(380)

    def set_project(self, project: Project) -> None:
        self.project = project
        self.tree.clear()
        self.inspector_panel.set_wanted(False)

    def _group(self, label: str) -> QTreeWidgetItem:
        parent = QTreeWidgetItem([label, "", ""])
        f = parent.font(0)
        f.setBold(True)
        parent.setFont(0, f)
        self.tree.addTopLevelItem(parent)
        return parent

    def run(self, text: str) -> int:
        hits = search(self.project, text)
        try:
            records = search_records(self.sources() or {}, text)
        except Exception:  # noqa: BLE001 - a broken record file must not break inventory search
            records = []
        self.tree.clear()
        self.inspector_panel.set_wanted(False)
        groups: dict[object, QTreeWidgetItem] = {}
        for h in hits:
            parent = groups.get(h.category.id)
            if parent is None:
                parent = groups[h.category.id] = self._group(f"{SCOPE_LABELS[h.category.scope]} / {h.category.name}")
            label = h.title + (f"  ·  {h.item.subgroup}" if h.item.subgroup else "")
            node = QTreeWidgetItem([label, state_label(h.state, h.category.kind), h.snippet])
            node.setIcon(1, state_dot(h.state))
            node.setData(0, Qt.UserRole, (h.category.id, h.item.id))
            parent.addChild(node)
        for r in records:
            parent = groups.get(r.kind)
            if parent is None:
                parent = groups[r.kind] = self._group(r.group)
            state = next((v for k, v in r.fields if k == "상태"), "")
            node = QTreeWidgetItem([r.title, state, r.summary])
            node.setData(0, REC, r)
            parent.addChild(node)
        for parent in groups.values():
            parent.setText(0, f"{parent.text(0)}  ({parent.childCount()})")
            parent.setExpanded(True)
        total = len(hits) + len(records)
        more = " (재고는 상위 200개)" if len(hits) >= 200 else ""
        self.title.setText(f"통합 검색 — ‘{text}’ {total}건{more}")
        return total

    def _selected(self, node: QTreeWidgetItem | None, _prev=None) -> None:
        rec = node.data(0, REC) if node is not None else None
        if rec is not None:
            self.inspector.show_hit(rec)
            self.inspector_panel.set_wanted(True)
        else:
            self.inspector_panel.set_wanted(False)

    def _activated(self, node: QTreeWidgetItem, _col: int) -> None:
        data = node.data(0, Qt.UserRole)
        if data:
            self.open_item.emit(*data)
            return
        rec = node.data(0, REC)
        if rec is not None and rec.tab:
            self.open_record.emit(rec)
