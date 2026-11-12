"""Right-hand panel (inspector): stock controls, free-form conditions and history of one item.

tahoe inspector (components/MacOSStyle): 268 wide, stock controls in an inset group
(rows 32, label at the start, value at the end), the quantity in a filled value field.
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QHBoxLayout, QHeaderView,
    QLabel, QListWidget, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from .theme import icons, tokens
from ..core.models import STOCK_STATES, state_label
from ..core.project import Project
from ..core.services.inventory import parse_quantity, stock_info
from .alerts import confirm_delete
from .inset import InsetGroup, inset_title, value_label
from .table_model import compact_rows, state_dot

DETAIL_PRESETS = ["보관 조건", "농도", "희석 배수", "버퍼", "사용 조건", "상태", "유효기간", "주문 정보", "메모"]


class DetailPanel(QWidget):
    changed = Signal(int)  # item id

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.item_id: int | None = None
        self._loading = False
        self.setMinimumWidth(tokens.space("inspector-width"))
        # RegionLayout boxes: it sits in the window's inspector box, which pads it (margin)
        # and paints the face; the panel itself paints nothing
        self.setObjectName("inspector")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(tokens.space("space-2"))
        self.title = QLabel("항목을 선택하세요")
        self.title.setObjectName("detailTitle")
        self.title.setWordWrap(True)
        self.subtitle = QLabel("")
        self.subtitle.setObjectName("muted")
        self.subtitle.setWordWrap(True)
        layout.addWidget(self.title)
        layout.addWidget(self.subtitle)

        # stock controls: one inset group
        layout.addSpacing(tokens.space("space-2"))
        layout.addWidget(inset_title("재고"))
        stock = InsetGroup()
        self.state_dot = QLabel()
        self.state_label = QLabel("-")  # StatusIndicator: dot + word
        stock.add_row("재고 상태", self.state_dot, self.state_label)
        self.qty_label = value_label("-")
        self.qty_unit = QLabel("")
        self.qty_unit.setObjectName("muted")
        self.minus_btn = icons.set_icon(QPushButton(), "minus", "수량 1 줄이기")
        self.plus_btn = icons.set_icon(QPushButton(), "plus", "수량 1 늘리기")
        for b in (self.minus_btn, self.plus_btn):
            b.setObjectName("plain")
        self.minus_btn.clicked.connect(lambda: self._bump(-1))
        self.plus_btn.clicked.connect(lambda: self._bump(1))
        stock.add_row("수량", self.qty_label, self.qty_unit, self.minus_btn, self.plus_btn)
        self.status_combo = QComboBox()
        self._fill_status_combo("managed")
        self.status_combo.currentIndexChanged.connect(self._status_changed)
        stock.add_row("상태 지정", self.status_combo)
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(-1, 100000)
        self.threshold_spin.setDecimals(1)
        self.threshold_spin.setSpecialValueText("카테고리 기본값")
        self.threshold_spin.setToolTip("이 값 이하이면 부족으로 표시합니다. 맨 아래 값은 카테고리 기본값을 따릅니다.")
        self.threshold_spin.valueChanged.connect(self._threshold_changed)
        self.threshold_label = QLabel("부족 기준")
        self.threshold_row = stock.add_row(self.threshold_label, self.threshold_spin)
        self.stock = stock
        layout.addWidget(stock)

        # details
        layout.addWidget(inset_title("세부 조건 · 상태 · 메모"))
        head = QHBoxLayout()
        self.preset_combo = QComboBox()
        self.preset_combo.addItems(DETAIL_PRESETS)
        self.preset_combo.setEditable(True)
        add_btn = icons.set_icon(QPushButton("추가"), "plus")
        add_btn.clicked.connect(self._add_detail)
        head.addWidget(self.preset_combo, 1)
        head.addWidget(add_btn)
        layout.addLayout(head)
        self.details = QTableWidget(0, 2)
        self.details.setHorizontalHeaderLabels(["항목", "내용"])
        compact_rows(self.details)
        self.details.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.details.verticalHeader().setVisible(False)
        self.details.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.details.itemChanged.connect(self._detail_edited)
        layout.addWidget(self.details, 2)
        # Delete (hig-delete): leading, apart from 추가, "verb + object…", confirmed first
        del_row = QHBoxLayout()
        self.del_btn = QPushButton("조건 삭제…")
        self.del_btn.setObjectName("destructive")
        icons.set_icon(self.del_btn, "trash")
        self.del_btn.setToolTip("고른 세부 조건 행을 지웁니다")
        self.del_btn.clicked.connect(self._delete_detail)
        del_row.addWidget(self.del_btn)
        del_row.addStretch(1)
        layout.addLayout(del_row)

        layout.addWidget(inset_title("변경 이력"))
        self.history = QListWidget()
        self.history.setObjectName("history")
        layout.addWidget(self.history, 1)
        self.set_item(None)

    def _fill_status_combo(self, kind: str) -> None:
        was = self._loading
        self._loading = True
        self.status_combo.clear()
        self.status_combo.addItem("자동 (수량 기준)", "")
        keys = ["ok", "out"] if kind == "status" else ["ok", "soon", "low", "out"]
        for key in keys:
            self.status_combo.addItem(f"‘{state_label(key, kind)}’으로 표시", key)
        self._loading = was

    def set_project(self, project: Project) -> None:
        self.project = project
        self.set_item(None)

    def set_item(self, item_id: int | None) -> None:
        self.item_id = item_id
        enabled = item_id is not None and self.project.item(item_id) is not None
        for w in (self.minus_btn, self.plus_btn, self.status_combo, self.threshold_spin,
                  self.details, self.preset_combo, self.del_btn):
            w.setEnabled(enabled)
        if not enabled:
            self.item_id = None
            self.title.setText("항목을 선택하세요")
            self.subtitle.setText("표에서 행을 고르면 세부 조건과 상태를 기록할 수 있습니다.")
            self.state_label.setText("-")
            self.state_dot.clear()
            self.qty_label.setText("-")
            self.qty_unit.setText("")
            self.details.setRowCount(0)
            self.history.clear()
            return
        self.refresh()

    def refresh(self) -> None:
        if self.item_id is None:
            return
        self._loading = True
        item = self.project.item(self.item_id)
        cat = self.project.category(item.category_id)
        self.title.setText(self.project.item_title(item.id))
        sub = cat.name + (f" · {item.subgroup}" if item.subgroup else "")
        self.subtitle.setText(sub)
        info = stock_info(self.project, cat, item)
        self.state_label.setText(state_label(info.state, cat.kind))
        status_kind = cat.kind == "status"
        self.threshold_row.setVisible(not status_kind)
        self.stock.restripe()
        self._fill_status_combo(cat.kind)
        self.state_label.setProperty("state", info.state)
        self.state_label.style().unpolish(self.state_label)
        self.state_label.style().polish(self.state_label)
        s = tokens.space("icon-sm")
        self.state_dot.setPixmap(state_dot(info.state).pixmap(QSize(s, s)))
        qty_f = self.project.field_by_role(cat.id, "quantity")
        if qty_f:
            raw = item.get(qty_f.id)
            qty, unit = parse_quantity(raw)
            plain = qty is not None and (raw or "").replace(" ", "") == f"{qty:g}{unit}"
            self.qty_label.setText(f"{qty:g}" if plain else (raw or "-"))  # 2/7 etc. stay as written
            self.qty_unit.setText(unit if plain else "")
            self.qty_label.setToolTip(f"{qty_f.name}: {raw or '-'}")
            self.minus_btn.setEnabled(True)
            self.plus_btn.setEnabled(True)
        else:
            self.qty_label.setText("-")
            self.qty_unit.setText("")
            self.qty_label.setToolTip("수량 컬럼 없음 (컬럼 관리에서 지정)")
            self.minus_btn.setEnabled(False)
            self.plus_btn.setEnabled(False)
        idx = self.status_combo.findData(item.status or "")
        self.status_combo.setCurrentIndex(max(idx, 0))
        self.threshold_spin.setValue(item.threshold if item.threshold is not None else -1)

        self.details.setRowCount(0)
        for d in self.project.details(item.id):
            row = self.details.rowCount()
            self.details.insertRow(row)
            k = QTableWidgetItem(d.key)
            k.setData(Qt.UserRole, d.id)
            self.details.setItem(row, 0, k)
            self.details.setItem(row, 1, QTableWidgetItem(d.value))
        self.history.clear()
        for h in self.project.history(item.id, limit=30):
            self.history.addItem(f"{h['ts'].replace('T', ' ')}  {h['detail']}")
        self._loading = False

    # ---- actions ---------------------------------------------------------
    def _emit(self) -> None:
        self.refresh()
        if self.item_id is not None:
            self.changed.emit(self.item_id)

    def _bump(self, delta: int) -> None:
        item = self.project.item(self.item_id)
        qty_f = self.project.field_by_role(item.category_id, "quantity")
        if not qty_f:
            return
        raw = item.get(qty_f.id)
        qty, unit = parse_quantity(raw)
        new = max(0.0, (qty or 0) + delta)
        self.project.set_value(item.id, qty_f.id, f"{new:g}{unit}")
        self._emit()

    def _status_changed(self) -> None:
        if self._loading or self.item_id is None:
            return
        self.project.update_item(self.item_id, status=self.status_combo.currentData() or "")
        self._emit()

    def _threshold_changed(self, value: float) -> None:
        if self._loading or self.item_id is None:
            return
        self.project.update_item(self.item_id, threshold=None if value < 0 else value)
        self._emit()

    def _add_detail(self) -> None:
        if self.item_id is None:
            return
        key = self.preset_combo.currentText().strip() or "메모"
        self.project.add_detail(self.item_id, key, "")
        self.refresh()
        row = self.details.rowCount() - 1
        self.details.setCurrentCell(row, 1)
        self.details.editItem(self.details.item(row, 1))

    def _delete_detail(self) -> None:
        row = self.details.currentRow()
        if row < 0:
            return
        from PySide6.QtWidgets import QMessageBox
        key = self.details.item(row, 0).text()
        if confirm_delete(self, "조건 삭제", f"세부 조건 ‘{key}’을(를) 지울까요?") != QMessageBox.Yes:
            return
        did = self.details.item(row, 0).data(Qt.UserRole)
        self.project.delete_detail(did)
        self._emit()

    def _detail_edited(self, cell: QTableWidgetItem) -> None:
        if self._loading:
            return
        row = cell.row()
        key_item, val_item = self.details.item(row, 0), self.details.item(row, 1)
        if key_item is None or val_item is None:
            return
        self.project.update_detail(key_item.data(Qt.UserRole), key_item.text(), val_item.text())
        if self.item_id is not None:
            self.changed.emit(self.item_id)
