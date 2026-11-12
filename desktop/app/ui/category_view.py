"""One category: filters, editable table and the item detail panel."""
from __future__ import annotations

from .widgets import InspectorPanel, fit_select, glass_capsule
from .theme import icons, tokens
from .alerts import confirm_delete
from .table_model import compact_rows
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QSizePolicy, 
    QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
    QMenu, QMessageBox, QPushButton, QSplitter, QTableView, QVBoxLayout, QWidget,
)

from ..core.models import CATEGORY_KINDS, SCOPE_LABELS, STOCK_STATES, state_label
from ..core.project import Project
from .detail_panel import DetailPanel
from .table_model import ITEM_ID_ROLE, ItemFilterProxy, ItemTableModel


class CategoryPage(QWidget):
    structure_changed = Signal()  # category renamed/deleted/columns changed
    data_changed = Signal()
    group_changed = Signal(object)  # sheet (group) filter, None for all
    add_to_cart = Signal(list)      # item ids for an order request
    filter_cleared = Signal()       # the text filter was dropped (to show an item): clear the field

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
        self.category_id: int | None = None
        self.model: ItemTableModel | None = None
        self.proxy = ItemFilterProxy(self)

        root = QVBoxLayout(self)
        root.setContentsMargins(*[tokens.space("margin")] * 4)  # window margin 20
        head = QHBoxLayout()
        self.title = QLabel()
        self.title.setObjectName("pageTitle")
        self.count_label = QLabel()
        self.count_label.setObjectName("muted")
        head.addWidget(self.title)
        head.addWidget(self.count_label)
        head.addStretch(1)
        # icon-only buttons grouped in one glass capsule (like the header's), names in tooltips
        tools = []
        for symbol, text, slot in (("square.and.arrow.down", "가져오기…", self.import_file),
                                   ("square.and.arrow.up", "내보내기…", self.export_file),
                                   ("tablecells", "컬럼 관리…", self.edit_fields),
                                   ("gearshape", "카테고리 설정…", self.edit_category)):
            b = icons.set_icon(QPushButton(), symbol, text)
            b.clicked.connect(slot)
            tools.append(b)
        head.addWidget(glass_capsule(*tools))
        root.addLayout(head)
        head.setSpacing(tokens.space("space-2"))

        # the text filter is the header's search field (with 이 카테고리에서 찾기 on), and the
        # sheet / state Selects sit next to it in the header while this list is shown
        self.filter_text = ""
        self.group_combo = QComboBox()
        self.group_combo.currentIndexChanged.connect(self._group_picked)
        self.state_combo = QComboBox()
        for c in (self.group_combo, self.state_combo):  # Select: the value sits against its cap
            fit_select(c)
        self._state_kind = None
        self.state_combo.currentIndexChanged.connect(
            lambda: self.proxy.set_filters(state=self.state_combo.currentData()))
        add_btn = QPushButton()
        add_btn.setObjectName("plain")  # inside the capsule: the accent glyph marks the main action
        icons.set_icon(add_btn, "plus", "항목 추가")
        add_btn.clicked.connect(self.add_item)
        cart_btn = QPushButton()
        cart_btn.setToolTip("선택한 항목을 장바구니에 담기")
        icons.set_icon(cart_btn, "cart.badge.plus", "장바구니에 담기")
        cart_btn.clicked.connect(self._cart_selected)
        # delete lives in the row's right-click menu (and ⌫), not on a button (user's choice)
        head.addWidget(glass_capsule(cart_btn, add_btn))  # the item actions at the trailing end

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        compact_rows(self.table)
        self.table.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                   | QAbstractItemView.AnyKeyPressed)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        # right-click: column names on the header, rows in the table
        hh = self.table.horizontalHeader()
        hh.setContextMenuPolicy(Qt.CustomContextMenu)
        hh.customContextMenuRequested.connect(self._header_menu)
        hh.setToolTip("우클릭: 열 이름 바꾸기 · 열 추가 · 열 삭제")
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._row_menu)
        # 분류 chips: shown when the category has a 분류 column (Personal Asset template)
        from .widgets import FlowLayout
        self.class_box = QWidget()
        self.class_chips = FlowLayout(self.class_box)
        self.class_box.setVisible(False)
        self._class_field = None
        root.addWidget(self.class_box)
        root.addWidget(self.table, 1)
        # the item inspector goes in the window's inspector box (RegionLayout boxes)
        self.detail = DetailPanel(project)
        self.detail.changed.connect(self._detail_changed)
        self.inspector_panel = InspectorPanel()
        il = QVBoxLayout(self.inspector_panel)
        il.setContentsMargins(0, 0, 0, 0)
        il.addWidget(self.detail)
        self.inspector_panel.setMinimumWidth(self.detail.minimumWidth())

    # ---- loading ---------------------------------------------------------
    def set_project(self, project: Project) -> None:
        self.project = project
        self.detail.set_project(project)
        self.category_id = None
        self.model = None

    def _group_picked(self) -> None:
        self.proxy.set_filters(subgroup=self.group_combo.currentData())
        self.group_changed.emit(self.group_combo.currentData())

    def show_category(self, category_id: int, select_item: int | None = None, group: "str | None | bool" = False) -> None:
        cat = self.project.category(category_id)
        if cat is None:
            return
        if self.category_id != category_id or self.model is None:
            self.category_id = category_id
            self.model = ItemTableModel(self.project, cat, self)
            self.proxy.setSourceModel(self.model)
            self.table.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
            self.proxy.sort(-1)
            self.table.selectionModel().currentRowChanged.connect(self._row_changed)
            self.set_text_filter("")
            self.filter_cleared.emit()
            self.state_combo.setCurrentIndex(0)
        else:
            self.model.reload()
        self._refresh_header()
        self._fill_groups(group)
        self._fill_classes(None)
        self._size_columns()
        if select_item is not None:
            self.select_item(select_item)
        else:
            self.detail.set_item(None)

    def _refresh_header(self) -> None:
        cat = self.project.category(self.category_id)
        self.title.setText(cat.name)
        n = self.project.count_items(cat.id)
        rule = f" · 부족 기준 {cat.threshold:g} 이하" if cat.kind == "managed" else ""
        o = self.project.owner(cat.id)
        owner = f" · 담당 {o.get('name') or o.get('email')}" if o else " · 담당자 없음"
        src = "Google 시트 · " if self.project.get_meta(f"source:{cat.id}") == "gsheet" else ""
        self.count_label.setText(f"{src}{SCOPE_LABELS[cat.scope]} · {CATEGORY_KINDS[cat.kind]} · {n}개{rule}{owner}")
        self._fill_state_filter(cat.kind)

    def _fill_state_filter(self, kind: str) -> None:
        if self._state_kind == kind:
            return
        self._state_kind = kind
        self.state_combo.blockSignals(True)
        self.state_combo.clear()
        self.state_combo.addItem("모든 재고 상태", None)
        if kind == "managed":
            self.state_combo.addItem("주문 필요 (임박·부족·소진)", "short")
            keys = ["ok", "soon", "low", "out", "unknown"]
        else:
            keys = ["ok", "out", "unknown"]
        for key in keys:
            self.state_combo.addItem(state_label(key, kind), key)
        self.state_combo.blockSignals(False)
        self.proxy.set_filters(state=None)

    def _fill_groups(self, pick: str | None | bool = False) -> None:
        current = self.group_combo.currentData() if pick is False else pick
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addItem("모든 시트", None)
        for g in self.project.subgroups(self.category_id):
            self.group_combo.addItem(g or "(그룹 없음)", g)
        idx = self.group_combo.findData(current)
        self.group_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.group_combo.blockSignals(False)
        self.proxy.set_filters(subgroup=self.group_combo.currentData())
        self.table.setColumnHidden(1, len(self.project.subgroups(self.category_id)) <= 1)

    # ---- 분류 (classification) ---------------------------------------------
    CLASS_NAMES = ("분류", "category", "class", "type", "종류")

    def class_field(self):
        if self.category_id is None:
            return None
        return next((f for f in self.project.fields(self.category_id)
                     if not getattr(f, "virtual", False) and f.name.strip().lower() in self.CLASS_NAMES), None)

    def class_values(self) -> list[str]:
        f = self.class_field()
        if f is None:
            return []
        return sorted({(it.get(f.id) or "").strip() for it in self.project.items(self.category_id)} - {""})

    def _fill_classes(self, pick: "str | None | bool" = False) -> None:
        f = self._class_field = self.class_field()
        cur = self.proxy.klass[1] if (pick is False and self.proxy.klass) else (None if pick is False else pick)
        while self.class_chips.count():
            w = self.class_chips.takeAt(0).widget()
            if w is not None:
                w.deleteLater()
        values = self.class_values()
        self.class_box.setVisible(f is not None and bool(values))
        if f is None or not values:
            self.proxy.set_filters(klass=None)
            return
        items = self.project.items(self.category_id)
        counts: dict[str, int] = {}
        for it in items:
            v = (it.get(f.id) or "").strip()
            counts[v] = counts.get(v, 0) + 1
        if cur is not None and cur not in counts:
            cur = None
        entries = [(None, f"전체 {len(items)}")] + [(v, f"{v} {counts[v]}") for v in values]
        if counts.get(""):
            entries.append(("", f"미분류 {counts['']}"))
        for value, text in entries:
            chip = QPushButton(text)
            chip.setObjectName("readerChip")
            chip.setCheckable(True)
            chip.setChecked(value == cur)
            chip.clicked.connect(lambda _=False, v=value: self._class_picked(v))
            self.class_chips.addWidget(chip)
        self.proxy.set_filters(klass=None if cur is None else (f.id, cur))

    def _class_picked(self, value) -> None:
        self._fill_classes(value)

    def set_class_selected(self, value: str | None = None) -> None:
        f = self.class_field()
        if f is None:
            f = self.project.add_field(self.category_id, "분류")
            self._after_columns_changed()
        if value is None:
            from PySide6.QtWidgets import QInputDialog
            value, ok = QInputDialog.getText(self, "분류 지정", "새 분류 이름")
            value = value.strip()
            if not ok or not value:
                return
        ids = [r.data(ITEM_ID_ROLE) for r in self.table.selectionModel().selectedRows()]
        for iid in ids:
            self.project.set_value(iid, f.id, value)
        self.model.reload()
        self._fill_classes()
        self._size_columns()
        self.data_changed.emit()

    def move_selected_new(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(self, "새 시트로 옮기기", "새 시트 이름 (엑셀에 시트가 생깁니다)")
        name = name.strip()
        if ok and name:
            self.move_selected(name)

    def _size_columns(self) -> None:
        self.table.resizeColumnsToContents()
        for c in range(self.proxy.columnCount()):
            if self.table.columnWidth(c) > 260:
                self.table.setColumnWidth(c, 260)

    def select_item(self, item_id: int) -> None:
        if self.model is None:
            return
        row = self.model.row_of(item_id)
        if row < 0:
            return
        src = self.model.index(row, 0)
        idx = self.proxy.mapFromSource(src)
        if not idx.isValid():  # hidden by a filter
            self.set_text_filter("")
            self.filter_cleared.emit()
            self.group_combo.setCurrentIndex(0)
            self.state_combo.setCurrentIndex(0)
            idx = self.proxy.mapFromSource(src)
        self.table.selectRow(idx.row())
        self.table.scrollTo(idx, QAbstractItemView.PositionAtCenter)
        self.detail.set_item(item_id)

    def set_text_filter(self, text: str) -> None:
        self.filter_text = text
        self.proxy.set_filters(text=text)

    def _row_changed(self, current, _previous) -> None:
        self.detail.set_item(current.data(ITEM_ID_ROLE) if current.isValid() else None)

    def _detail_changed(self, item_id: int) -> None:
        if self.model:
            self.model.refresh_row(item_id)
        self.data_changed.emit()

    # ---- right-click menus ------------------------------------------------
    def _field_at(self, section: int):
        if self.model is None or section < 2 or section - 2 >= len(self.model.fields):
            return None
        return self.model.fields[section - 2]

    def _header_menu(self, pos) -> None:
        if self.model is None:
            return
        section = self.table.horizontalHeader().logicalIndexAt(pos)
        f = self._field_at(section)
        menu = QMenu(self)
        if f is not None and f.virtual:
            a = menu.addAction(f"‘{f.name}’은 주문 기록에서 계산된 열입니다 (엑셀에는 없음)")
            a.setEnabled(False)
            menu.addSeparator()
        elif f is not None:
            menu.addAction(f"열 이름 바꾸기 (‘{f.name}’)…", lambda: self.rename_field(f.id))
            menu.addAction("오른쪽에 열 추가…", lambda: self.add_field_after(f.id))
            menu.addAction(f"열 삭제 (‘{f.name}’)…", lambda: self.delete_field(f.id))
            menu.addSeparator()
        else:
            menu.addAction("열 추가…", lambda: self.add_field_after(None))
        menu.addAction("형식·역할 등 컬럼 관리…", self.edit_fields)
        menu.exec(self.table.horizontalHeader().viewport().mapToGlobal(pos))

    def _row_menu(self, pos) -> None:
        if self.model is None:
            return
        idx = self.table.indexAt(pos)
        menu = QMenu(self)
        menu.addAction("항목 추가", self.add_item)
        if idx.isValid():
            if not self.table.selectionModel().isRowSelected(idx.row(), idx.parent()):
                self.table.selectRow(idx.row())
            n = len(self.table.selectionModel().selectedRows())
            f = self._field_at(idx.column())
            if f is not None and not f.virtual:
                menu.addAction("이 칸 편집", lambda: self.table.edit(idx))
            groups = self.project.subgroups(self.category_id)
            move = menu.addMenu("다른 시트로 옮기기")
            for g in groups:
                move.addAction(g or "(그룹 없음)", lambda g=g: self.move_selected(g))
            move.addSeparator()
            move.addAction("새 시트…", self.move_selected_new)
            klass = menu.addMenu(f"분류 지정 ({n}개)")
            for v in self.class_values():
                klass.addAction(v, lambda v=v: self.set_class_selected(v))
            if self.class_values():
                klass.addSeparator()
            klass.addAction("새 분류…", lambda: self.set_class_selected(None))
            if self.class_field() is not None:
                klass.addAction("분류 지우기", lambda: self.set_class_selected(""))
            menu.addAction(f"장바구니에 담기 ({n}개)", self._cart_selected)
            menu.addSeparator()
            menu.addAction(f"선택한 항목 {n}개 삭제…", self.delete_selected)
        menu.addSeparator()
        f = self._field_at(idx.column()) if idx.isValid() else None
        if f is not None and not f.virtual:
            menu.addAction(f"열 이름 바꾸기 (‘{f.name}’)…", lambda: self.rename_field(f.id))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _after_columns_changed(self) -> None:
        self.model.reload()
        self._size_columns()
        self.structure_changed.emit()

    def rename_field(self, field_id: int) -> None:
        f = self.project.field(field_id)
        name, ok = QInputDialog.getText(self, "열 이름 바꾸기", "열 이름 (엑셀 머리글도 바뀝니다)", text=f.name)
        name = name.strip()
        if not ok or not name or name == f.name:
            return
        if any(o.name.lower() == name.lower() for o in self.project.fields(self.category_id) if o.id != f.id):
            QMessageBox.information(self, "열 이름 바꾸기", f"‘{name}’ 열이 이미 있습니다.")
            return
        self.project.update_field(field_id, name=name)
        self._after_columns_changed()

    def add_field_after(self, field_id: int | None) -> None:
        name, ok = QInputDialog.getText(self, "열 추가", "새 열 이름")
        name = name.strip()
        if not ok or not name:
            return
        if any(o.name.lower() == name.lower() for o in self.project.fields(self.category_id)):
            QMessageBox.information(self, "열 추가", f"‘{name}’ 열이 이미 있습니다.")
            return
        new = self.project.add_field(self.category_id, name)
        if field_id is not None:
            ids = [o.id for o in self.project.fields(self.category_id) if o.id != new.id]
            ids.insert(ids.index(field_id) + 1, new.id)
            self.project.reorder_fields(ids)
        self._after_columns_changed()

    def delete_field(self, field_id: int) -> None:
        f = self.project.field(field_id)
        if confirm_delete(self, "열 삭제", f"‘{f.name}’ 열과 그 안의 값을 모두 삭제할까요?\n"
                                "엑셀 파일에서도 이 열이 지워집니다.") != QMessageBox.Yes:
            return
        self.project.delete_field(field_id)
        self._after_columns_changed()

    def _cart_selected(self) -> None:
        ids = [r.data(ITEM_ID_ROLE) for r in self.table.selectionModel().selectedRows()]
        if ids:
            self.add_to_cart.emit(ids)

    def move_selected(self, group: str) -> None:
        ids = [r.data(ITEM_ID_ROLE) for r in self.table.selectionModel().selectedRows()]
        for iid in ids:
            self.project.update_item(iid, subgroup=group)
        self.model.reload()
        self._fill_groups()
        self.data_changed.emit()

    # ---- actions ---------------------------------------------------------
    def add_item(self) -> None:
        if self.category_id is None:
            return
        sub = self.group_combo.currentData() or ""
        item_id = self.project.add_item(self.category_id, {}, subgroup=sub)
        self.model.reload()
        self._refresh_header()
        self.select_item(item_id)
        idx = self.table.currentIndex()
        first_field = self.proxy.index(idx.row(), 2)
        self.table.setCurrentIndex(first_field)
        self.table.edit(first_field)
        self.data_changed.emit()

    def delete_selected(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        ids = [r.data(ITEM_ID_ROLE) for r in rows]
        if confirm_delete(self, "삭제", f"선택한 {len(ids)}개 항목을 삭제할까요?") != QMessageBox.Yes:
            return
        self.project.delete_items(ids)
        self.model.reload()
        self._refresh_header()
        self._fill_groups()
        self.detail.set_item(None)
        self.data_changed.emit()

    def edit_fields(self) -> None:
        from .dialogs.fields_dialog import FieldsDialog
        if self.category_id is None:
            return
        if FieldsDialog(self.project, self.category_id, self).exec():
            self.model.reload()
            self._size_columns()
            self.structure_changed.emit()

    def edit_category(self) -> None:
        from .dialogs.category_dialog import CategoryDialog
        cat = self.project.category(self.category_id)
        dlg = CategoryDialog(self, category=cat)
        delete_btn = QPushButton("카테고리 삭제…")
        delete_btn.setObjectName("destructive")
        icons.set_icon(delete_btn, "trash")
        dlg.layout().insertWidget(dlg.layout().count() - 1, delete_btn, 0, Qt.AlignLeft)
        delete_btn.clicked.connect(lambda: dlg.done(2))
        result = dlg.exec()
        if result == 2:
            n = self.project.count_items(cat.id)
            if confirm_delete(self, "카테고리 삭제",
                                    f"‘{cat.name}’과 항목 {n}개를 모두 삭제할까요? 되돌릴 수 없습니다.") == QMessageBox.Yes:
                self.project.delete_category(cat.id)
                self.category_id = None
                self.model = None
                self.structure_changed.emit()
            return
        if result:
            self.project.update_category(cat.id, **dlg.values())
            self.model.reload()
            self._refresh_header()
            self.structure_changed.emit()

    def import_file(self) -> None:
        from .dialogs.import_dialog import ImportDialog
        path, _ = QFileDialog.getOpenFileName(self, "가져올 파일", "", "표 파일 (*.xlsx *.xls *.xlsm *.csv *.tsv)")
        if not path:
            return
        cat = self.project.category(self.category_id)
        try:
            dlg = ImportDialog(self.project, path, self, scope=cat.scope, category_id=cat.id)
        except Exception as exc:  # noqa: BLE001 - show any read error to the user
            QMessageBox.warning(self, "가져오기", f"파일을 읽지 못했습니다.\n{exc}")
            return
        if dlg.exec():
            self.structure_changed.emit()
            self.show_category(dlg.result_category_id or self.category_id)

    def export_file(self) -> None:
        from ..core.exporter import export_category
        cat = self.project.category(self.category_id)
        path, _ = QFileDialog.getSaveFileName(self, "내보내기", f"{cat.name}.xlsx", "Excel (*.xlsx);;CSV (*.csv)")
        if not path:
            return
        visible = [self.proxy.mapToSource(self.proxy.index(r, 0)).row() for r in range(self.proxy.rowCount())]
        ids = [self.model.items[r].id for r in visible]
        export_category(self.project, cat.id, path, item_ids=ids)
        QMessageBox.information(self, "내보내기", f"{len(ids)}개 항목을 저장했습니다.\n{path}")
