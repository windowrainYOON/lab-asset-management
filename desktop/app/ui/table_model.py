"""Qt model that shows one category's items as an editable table."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSize, QSortFilterProxyModel, Qt
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPixmap

from ..core.models import Category, Field, Item, state_label
from ..core.project import Project
from ..core.services.inventory import parse_quantity, stock_info

# Filled from the design tokens by refresh_state_colors() whenever the theme changes;
# the dicts are updated in place so modules that imported them see new values.
STATE_COLORS: dict[str, QColor] = {}  # marks: chart slices, badge dots (3:1 like large text)
STATE_BG: dict[str, QColor] = {}
STATE_DOTS: dict[str, QIcon] = {}     # badge dot shown next to the state word (components/Badge)


def refresh_state_colors() -> None:
    from .theme import tokens
    for state, name in tokens.STATE_TOKENS.items():
        STATE_COLORS[state] = tokens.text(name, large=True)
    STATE_DOTS.clear()  # redrawn on demand by state_dot()
    STATE_BG.clear()
    for state in ("soon", "low", "out"):
        STATE_BG[state] = tokens.color(tokens.STATE_TINTS[state])  # status-*-tint token


def state_dot(state: str) -> QIcon:
    if state not in STATE_DOTS:
        from .theme import tokens
        STATE_DOTS[state] = _dot(STATE_COLORS[state], tokens.space("space-2"), tokens.space("icon-sm"))
    return STATE_DOTS[state]


def _dot(color: QColor, size: int, canvas: int) -> QIcon:
    """A `size` dot centred in an icon-sized square (badge status dot)."""
    pm = QPixmap(QSize(canvas * 2, canvas * 2))  # drawn at 2x for Retina
    pm.setDevicePixelRatio(2)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    off = (canvas - size) / 2
    p.drawEllipse(QRectF(off, off, size, size))
    p.end()
    return QIcon(pm)


# components/Table + alignment-and-direction.md: text columns start-aligned, numbers
# end-aligned with tabular digits, each header aligned like its column.
TEXT_ALIGN = Qt.AlignLeading | Qt.AlignVCenter
NUM_ALIGN = Qt.AlignTrailing | Qt.AlignVCenter


def compact_rows(view, numeric: tuple[int, ...] = ()) -> None:
    """A data table, Desktop profile (components/Table, layout-desktop.md).

    Rows 28 (desktop-row), header 24, no row separators (striped rows on wide
    tables instead), headers start-aligned and numeric columns (`numeric`, for
    QTableWidget) end-aligned with tabular digits; long text is cut at the end.
    """
    from .theme import tokens
    view.verticalHeader().setDefaultSectionSize(tokens.space("row"))
    view.verticalHeader().setMinimumSectionSize(tokens.space("row-compact"))
    if hasattr(view, "setShowGrid"):
        view.setShowGrid(False)
    header = view.horizontalHeader()
    header.setFixedHeight(tokens.space("table-header-height"))
    header.setDefaultAlignment(TEXT_ALIGN)
    header.setHighlightSections(False)
    view.setFont(tokens.tabular(view.font()))
    view.setTextElideMode(Qt.ElideRight)
    view.setWordWrap(False)
    view.setProperty("numericColumns", list(numeric))
    model = view.model()
    if model is not None and model.columnCount() >= 4:  # wide tables: striped rows (HIG)
        view.setAlternatingRowColors(True)
    if numeric and hasattr(view, "horizontalHeaderItem"):
        _align_headers(view)


def _align_headers(view) -> None:
    for col in view.property("numericColumns") or []:
        item = view.horizontalHeaderItem(col)
        if item is not None:
            item.setTextAlignment(NUM_ALIGN)


def numeric_cell(text: str):
    """A QTableWidgetItem for a number column: end-aligned."""
    from PySide6.QtWidgets import QTableWidgetItem
    item = QTableWidgetItem(text)
    item.setTextAlignment(NUM_ALIGN)
    return item


refresh_state_colors()

ITEM_ID_ROLE = Qt.UserRole + 1
STATE_ROLE = Qt.UserRole + 2
SORT_ROLE = Qt.UserRole + 3


class ItemTableModel(QAbstractTableModel):
    """Column 0 is the computed stock state, then one column per field."""

    def __init__(self, project: Project, category: Category, parent=None):
        super().__init__(parent)
        self.project = project
        self.category = category
        self.fields: list[Field] = []
        self.items: list[Item] = []
        self.states: list[str] = []
        self.reload()

    def reload(self) -> None:
        self.beginResetModel()
        self.category = self.project.category(self.category.id) or self.category
        self.fields = self.project.fields(self.category.id)
        self.items = self.project.items(self.category.id)
        cache: dict = {}
        self.states = [stock_info(self.project, self.category, it, cache).state for it in self.items]
        self.endResetModel()

    def refresh_row(self, item_id: int) -> None:
        for row, it in enumerate(self.items):
            if it.id == item_id:
                fresh = self.project.item(item_id)
                if fresh is None:
                    return
                self.items[row] = fresh
                self.states[row] = stock_info(self.project, self.category, fresh).state
                self.dataChanged.emit(self.index(row, 0), self.index(row, self.columnCount() - 1))
                return

    def row_of(self, item_id: int) -> int:
        for row, it in enumerate(self.items):
            if it.id == item_id:
                return row
        return -1

    # ---- Qt API ----------------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.items)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.fields) + 2

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            if section == 0:
                return "재고" if self.category.kind == "managed" else "보유"
            if section == 1:
                return "그룹"
            return self.fields[section - 2].name
        if orientation == Qt.Horizontal and role == Qt.ToolTipRole and section >= 2:
            f = self.fields[section - 2]
            return f"{f.name} · {f.ftype}" + (f" · 역할: {f.role}" if f.role else "")
        if orientation == Qt.Horizontal and role == Qt.TextAlignmentRole:
            numeric = section >= 2 and self._numeric(self.fields[section - 2])
            return int(NUM_ALIGN if numeric else TEXT_ALIGN)
        if orientation == Qt.Vertical and role == Qt.DisplayRole:
            return section + 1
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        item = self.items[index.row()]
        state = self.states[index.row()]
        col = index.column()
        if role == ITEM_ID_ROLE:
            return item.id
        if role == STATE_ROLE:
            return state
        if role == Qt.BackgroundRole and state in STATE_BG:
            return QBrush(STATE_BG[state])
        if col == 0:
            if role in (Qt.DisplayRole, SORT_ROLE):
                return state_label(state, self.category.kind) if role == Qt.DisplayRole else ["out", "low", "soon", "ok", "unknown"].index(state)
            if role == Qt.DecorationRole:
                return state_dot(state)
            if role == Qt.TextAlignmentRole:
                return int(TEXT_ALIGN)
            return None
        if col == 1:
            if role in (Qt.DisplayRole, Qt.EditRole, SORT_ROLE):
                return item.subgroup
            return None
        f = self.fields[col - 2]
        value = item.get(f.id)
        if role in (Qt.DisplayRole, Qt.EditRole, Qt.ToolTipRole):
            return value
        if role == SORT_ROLE:
            if f.ftype == "number" or f.role == "quantity":
                num, _ = parse_quantity(value)
                return num if num is not None else float("-inf")
            return value.lower()
        if role == Qt.TextAlignmentRole:
            return int(NUM_ALIGN if self._numeric(f) else TEXT_ALIGN)
        return None

    @staticmethod
    def _numeric(f: Field) -> bool:
        return f.ftype == "number" or f.role == "quantity"

    def flags(self, index):
        base = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if index.isValid() and index.column() >= 1:
            if index.column() >= 2 and self.fields[index.column() - 2].virtual:
                return base  # computed from order records; the files are not changed
            base |= Qt.ItemIsEditable
        return base

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid() or index.column() < 1:
            return False
        item = self.items[index.row()]
        text = "" if value is None else str(value).strip()
        if index.column() == 1:
            self.project.update_item(item.id, subgroup=text)
        else:
            self.project.set_value(item.id, self.fields[index.column() - 2].id, text)
        self.refresh_row(item.id)
        return True


class ItemFilterProxy(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ""
        self.subgroup: str | None = None
        self.state: str | None = None
        self.klass: tuple[int, str] | None = None  # (분류 field id, value); "" = 미분류
        self.setSortRole(SORT_ROLE)

    def set_filters(self, text: str | None = None, subgroup: str | None | bool = False,
                    state: str | None | bool = False, klass: "tuple[int, str] | None | bool" = False) -> None:
        if klass is not False:
            self.klass = klass
        if text is not None:
            self.text = text.lower().strip()
        if subgroup is not False:
            self.subgroup = subgroup
        if state is not False:
            self.state = state
        self.invalidate()

    def filterAcceptsRow(self, row, parent):
        model: ItemTableModel = self.sourceModel()
        item = model.items[row]
        if self.subgroup is not None and item.subgroup != self.subgroup:
            return False
        if self.klass is not None and (item.get(self.klass[0]) or "").strip() != self.klass[1]:
            return False
        if self.state is not None:
            if self.state == "short":
                if model.states[row] not in ("low", "out", "soon"):
                    return False
            elif model.states[row] != self.state:
                return False
        if self.text:
            hay = " ".join(item.values.values()).lower() + " " + item.subgroup.lower()
            return all(t in hay for t in self.text.split())
        return True
