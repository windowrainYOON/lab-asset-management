"""Left sidebar: one tree for every tab, grouped in sections of the same shape.

    메뉴              공용 대시보드 · 개인 품목 대시보드 · 일정 · 공지사항
    Common Asset  +   one row per file, its sheets under the open one
    Personal Asset +
    숨긴 항목 (n)      folded; whatever the user chose to hide

Section headers fold on click. Any tab can be hidden from its right-click menu or
from "탭 표시 설정…", and comes back from 숨긴 항목 the same way.

Look (the user's reference, on the tahoe panel): plain gray group titles, rows of
symbol + label + a count at the end, symbols in label-secondary, and the current row
as a raised pill (the level-2 surface with a soft shadow) instead of a colored fill.
File rows keep a disclosure chevron before the symbol when the file has sheets.
NavDelegate draws every row, so the pill, the chevron and the count line up.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from functools import lru_cache

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QMenu,
    QPushButton, QStyle, QStyledItemDelegate, QTreeWidget, QTreeWidgetItem, QTreeWidgetItemIterator,
    QVBoxLayout,
)

from ..core import nav_tabs
from ..core.models import CATEGORY_KINDS
from .theme import icons, tokens

if TYPE_CHECKING:
    from .main_window import MainWindow

KIND_ROLE = Qt.UserRole        # "section" | "tab" | "sheet"
KEY_ROLE = Qt.UserRole + 1     # tab key, or section key
CID_ROLE = Qt.UserRole + 2     # category id
GROUP_ROLE = Qt.UserRole + 3   # sheet name on a sheet row
SCOPE_ROLE = Qt.UserRole + 4   # scope of an asset section
HIDDEN_ROLE = Qt.UserRole + 5  # the row sits under 숨긴 항목
ICON_ROLE = Qt.UserRole + 6    # symbol name of a tab row
COUNT_ROLE = Qt.UserRole + 7   # number shown at the end of the row (None = none)
CHEV_ROLE = Qt.UserRole + 8    # None (no sheets), "open" or "closed"
LANE_ROLE = Qt.UserRole + 9    # the row's section keeps a chevron lane (asset sections)

# components/Sidebar (Desktop): familiar symbols in the app accent, icon-md (16), gap 6.
# One symbol size for every row, so the tahoe app-icon tiles (20) are not used here.
TAB_ICONS = {"dash:common": "square.grid.2x2", "dash:personal": "person", "schedule": "calendar",
             "work": "checkmark.circle", "requests": "square.and.pencil", "notices": "bell", "reservations": "clock", "orders": "shippingbox",
             "budget": "wonsign", "attendance": "alarm"}
FILE_ICON = "list.bullet"  # a file is a list of items

# Desktop sidebar: 240 wide by default, the user can drag it between 200 and 320
# (components/Sidebar, SplitView). Only the default is a token; the limits are the README's.
SIDEBAR_MIN, SIDEBAR_MAX = 200, 320
DISCLOSURE_SIZE = "mac-footnote"  # the 10px disclosure triangle, sized like 10px text


def sidebar_width() -> int:
    return tokens.space("sidebar-width")


ROW_HEIGHT = "tahoe-group-row"  # 32: the reference's roomier rows (the pill is 30 with a 2px gap)


def _row_size() -> QSize:
    return QSize(1, tokens.space(ROW_HEIGHT))  # the width is set by the column


@lru_cache(maxsize=256)
def _symbol(name: str, rgba: int, weight: int, size: int, theme: str):
    """Cached symbol pixmap (the delegate paints every row on each repaint)."""
    return icons.pixmap(name, QColor.fromRgba(rgba), weight, size)


def _pix(name: str, color: QColor, weight: int = 400, size: int | None = None):
    return _symbol(name, color.rgba(), weight, size or tokens.space("icon-md"), tokens.theme())


def _font(style: str, weight: int | None = None) -> QFont:
    st = tokens.type_style(style)
    f = QFont()
    f.setPixelSize(st["size"])
    f.setWeight(QFont.Weight(weight or st["weight"]))
    return f


class NavDelegate(QStyledItemDelegate):
    """Paints the sidebar rows: group titles, the raised selection pill, chevron lane,
    symbol, label and the trailing count (all sizes and colors from tokens)."""

    def __init__(self, tree: QTreeWidget):
        super().__init__(tree)
        self.tree = tree

    def sizeHint(self, option, index):
        return _row_size()

    @staticmethod
    def chevron_lane() -> int:
        return tokens.space("icon-md") + tokens.space("space-1")  # 16 + 4

    def trailing_rect(self, rect):
        """Where the + of an asset section sits (the last icon-md + row-pad)."""
        w = tokens.space("icon-md") + tokens.space("row-pad")
        return rect.adjusted(rect.width() - w, 0, 0, 0)

    def paint(self, painter: QPainter, option, index) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        kind = index.data(KIND_ROLE)
        rect = option.rect
        pad = tokens.space("row-pad")
        panel = "region-base"  # RegionLayout boxes: the sidebar is the window base
        hovered = bool(option.state & QStyle.State_MouseOver)
        if kind == "section":
            self._paint_section(painter, rect, index, hovered)
            painter.restore()
            return
        selected = bool(option.state & QStyle.State_Selected) and kind in ("tab", "sheet")
        pill = QRectF(rect).adjusted(0, 1, 0, -1)
        r = tokens.radius("row")
        if selected:
            # components/Sidebar: selection = a fill highlight (no shadow, no line) + 600 text
            path = QPainterPath()
            path.addRoundedRect(pill, r, r)
            painter.fillPath(path, tokens.color("fill"))
        elif hovered and kind in ("tab", "sheet"):
            path = QPainterPath()
            path.addRoundedRect(pill, r, r)
            painter.fillPath(path, tokens.hover())

        x = rect.left() + pad
        surface = panel  # the fill highlight is translucent over the raised face
        icon_md = tokens.space("icon-md")
        gap = tokens.space("gap-tight")
        cy = rect.center().y()
        if index.data(LANE_ROLE):
            chev = index.data(CHEV_ROLE)
            if chev and kind == "tab":
                size = tokens.type_style("mac-callout")["size"]
                pm = _pix("chevron.down" if chev == "open" else "chevron.right",
                          tokens.text("label-tertiary", surface, large=True), 600, size)
                painter.drawPixmap(x + (icon_md - size) // 2, cy - size // 2 + 1, pm)
            x += self.chevron_lane()
        if kind == "sheet":
            x += icon_md + gap  # the sheet name lines up with its file's label
        elif kind == "tab" and index.data(ICON_ROLE):
            colour = tokens.color("label") if selected else tokens.text("label-secondary", surface, large=True)
            painter.drawPixmap(x, cy - icon_md // 2, _pix(index.data(ICON_ROLE), colour,
                                                            600 if selected else 400))
            x += icon_md + gap

        # trailing count, tabular digits, label-secondary
        right = rect.right() - pad
        count = index.data(COUNT_ROLE)
        body = _font("mac-body")
        if count is not None:
            cf = tokens.tabular(body)
            text = f"{count:,}"
            cw = QFontMetrics(cf).horizontalAdvance(text)
            painter.setFont(cf)
            painter.setPen(tokens.text("label-secondary", surface))
            painter.drawText(QRectF(right - cw, rect.top(), cw, rect.height()), Qt.AlignVCenter | Qt.AlignRight, text)
            right -= cw + gap
        font = _font("mac-body", 600 if selected else None)
        painter.setFont(font)
        if kind == "tab":
            colour = tokens.color("label")
        elif kind == "sheet":
            colour = tokens.color("label") if selected else tokens.text("label-secondary", surface)
        else:  # "아직 없음" note
            colour = tokens.text("gray", panel)
        painter.setPen(colour)
        label = QFontMetrics(font).elidedText(index.data(Qt.DisplayRole) or "", Qt.ElideRight, max(0, right - x))
        painter.drawText(QRectF(x, rect.top(), right - x, rect.height()), Qt.AlignVCenter | Qt.AlignLeft, label)
        painter.restore()

    def _paint_section(self, painter, rect, index, hovered) -> None:
        """Group title (subhead 600, label-secondary) low in its row, so it sits closer to
        its rows; a chevron at the end on hover or while folded, + for asset sections."""
        panel = "region-base"
        pad = tokens.space("row-pad")
        right = rect.right() - pad
        icon_md = tokens.space("icon-md")
        cy = rect.center().y() + tokens.space("space-1") // 2
        if index.data(SCOPE_ROLE):
            plus = _pix("plus", tokens.text("label-secondary", panel, large=True))
            painter.drawPixmap(right - icon_md + 1, cy - icon_md // 2, plus)
            right -= icon_md + tokens.space("gap-tight")
        expanded = self.tree.isExpanded(index)
        if hovered or not expanded:
            size = tokens.type_style("mac-callout")["size"]
            pm = _pix("chevron.down" if expanded else "chevron.right",
                      tokens.text("label-tertiary", panel, large=True), 600, size)
            painter.drawPixmap(right - size + 1, cy - size // 2, pm)
            right -= size + tokens.space("gap-tight")
        font = _font("mac-subheadline", 600)
        painter.setFont(font)
        painter.setPen(tokens.text("label-secondary", panel))
        x = rect.left() + pad
        text = QFontMetrics(font).elidedText(index.data(Qt.DisplayRole) or "", Qt.ElideRight, max(0, right - x))
        painter.drawText(QRectF(x, rect.top() + tokens.space("space-1"), right - x, rect.height() - tokens.space("space-1")),
                         Qt.AlignVCenter | Qt.AlignLeft, text)


class Sidebar(QFrame):
    """tahoe: the pane is transparent and holds a floating panel (#sidebar) inset 8 from
    the window edge, radius 18 (window 26 - 8), padding 8 so the selected row's radius is
    10 (18 - 8)."""

    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.setObjectName("sidebarPane")
        inset = tokens.space("tahoe-inset")
        self.setMinimumWidth(SIDEBAR_MIN + inset)
        self.setMaximumWidth(SIDEBAR_MAX + inset)
        self.window = window
        self.current_key: str | None = None
        self.current_group: str | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(inset, 0, 0, inset)
        self.panel = QFrame()
        self.panel.setObjectName("sidebar")
        outer.addWidget(self.panel)
        lay = QVBoxLayout(self.panel)
        lay.setContentsMargins(inset, inset, inset, inset)
        lay.setSpacing(0)

        # brand row (the reference): app logo + name, the data's place under it, and the
        # sidebar toggle at the end
        brand = QHBoxLayout()
        brand.setContentsMargins(tokens.space("row-pad"), tokens.space("space-1"), 0, tokens.space("gap-group"))
        brand.setSpacing(tokens.space("space-2"))
        self.logo = QLabel()
        self.logo.setObjectName("brandLogo")
        self._set_logo()
        brand.addWidget(self.logo, 0, Qt.AlignVCenter)
        names = QVBoxLayout()
        names.setSpacing(0)
        app_name = QLabel("Asset Manager")
        app_name.setObjectName("brandTitle")
        names.addWidget(app_name)
        if hasattr(window, "toolbar_subtitle"):
            names.addWidget(window.toolbar_subtitle)
        brand.addLayout(names, 1)
        if hasattr(window, "sidebar_btn"):
            brand.addWidget(window.sidebar_btn, 0, Qt.AlignVCenter)
        lay.addLayout(brand)

        self.tree = QTreeWidget()
        self.tree.setObjectName("navTree")
        self.tree.setColumnCount(1)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setIndentation(0)  # the delegate indents: chevron lane, symbol, sheet names
        self.tree.setMouseTracking(True)  # hover fill and the group chevron
        self.delegate = NavDelegate(self.tree)
        self.tree.setItemDelegate(self.delegate)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setFocusPolicy(Qt.NoFocus)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.itemClicked.connect(self._clicked)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.tree, 1)
        if hasattr(window, "members_btn"):  # the reference's bottom row (like "Invite members")
            lay.addSpacing(tokens.space("space-1"))
            lay.addWidget(window.members_btn)

        self._project = None
        self._need: dict[str, int] = {}
        self._current_category: int | None = None

    def _set_logo(self) -> None:
        """The app icon (the same art as the Dock icon) at control height, or nothing if missing."""
        from PySide6.QtGui import QPixmap
        from ..core import settings
        path = settings.source_root() / "assets" / "icon" / "icon.png"
        size = tokens.space("control-md")
        pm = QPixmap(str(path))
        if pm.isNull():
            self.logo.setVisible(False)
            return
        ratio = 2.0
        pm = pm.scaled(round(size * ratio), round(size * ratio), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pm.setDevicePixelRatio(ratio)
        self.logo.setPixmap(pm)
        self.logo.setFixedSize(size, size)

    # ---- building --------------------------------------------------------
    def _section(self, key: str, title: str, count: int | None = None, scope: str | None = None,
                 collapsed: bool = False) -> QTreeWidgetItem:
        it = QTreeWidgetItem([title + (f" ({count})" if count is not None else "")])
        it.setData(0, KIND_ROLE, "section")
        it.setData(0, KEY_ROLE, key)
        it.setData(0, SCOPE_ROLE, scope)
        it.setFlags(Qt.ItemIsEnabled)
        it.setSizeHint(0, _row_size())
        # group title (bundle.css .hig-sidebar__group): subhead, 600, label-secondary (NavDelegate)
        it.setToolTip(0, f"클릭: 접기/펼치기 · 끝의 ‘+’: {title}에 파일(카테고리) 추가" if scope
                      else "클릭: 접기/펼치기")
        self.tree.addTopLevelItem(it)
        it.setExpanded(not collapsed)
        return it

    def _tab(self, parent: QTreeWidgetItem, tab: nav_tabs.Tab, hidden: bool) -> QTreeWidgetItem:
        it = QTreeWidgetItem(parent, [tab.label])
        it.setSizeHint(0, _row_size())
        it.setFirstColumnSpanned(True)  # one selection shape across the row
        it.setData(0, KIND_ROLE, "tab")
        it.setData(0, KEY_ROLE, tab.key)
        it.setData(0, CID_ROLE, tab.category_id)
        it.setData(0, HIDDEN_ROLE, hidden)
        it.setData(0, ICON_ROLE, TAB_ICONS.get(tab.key, FILE_ICON))
        it.setData(0, LANE_ROLE, tab.category_id is not None)
        if tab.category_id is not None and self._project is not None:
            cat = self._project.category(tab.category_id)
            n = self._project.count_items(cat.id)
            it.setData(0, COUNT_ROLE, n)
            if len(self._project.subgroups(cat.id)) > 1:
                it.setData(0, CHEV_ROLE, "closed")
            src = " · Google 시트" if self._project.get_meta(f"source:{cat.id}") == "gsheet" else ""
            it.setToolTip(0, f"{cat.name}{src} · {tab.section} · {CATEGORY_KINDS[cat.kind]} · {n}개"
                             " · 우클릭: 이름 변경, 시트 추가, 숨기기, 삭제")
        else:
            need = self._need.get(tab.key)
            if need:
                it.setData(0, COUNT_ROLE, need)
                it.setToolTip(0, f"주문이 필요한 항목 {need}개 · 우클릭: 숨기기")
            else:
                it.setToolTip(0, "우클릭: 숨기기")
        return it

    def refresh(self, project, current_category: int | None, current_group: str | None = None) -> None:
        self._project = project
        self._current_category = current_category
        self.current_group = current_group
        if current_category is not None:
            cat = project.category(current_category)
            self.current_key = nav_tabs.category_key(cat.scope, cat.name) if cat else None
        if hasattr(self.window, "toolbar_subtitle"):
            self.window.toolbar_subtitle.setText(self.window.project_title())
        hidden = nav_tabs.hidden_tabs()
        collapsed = nav_tabs.collapsed_sections()
        self._need = {}  # dashboard rows count what needs ordering in their scope
        try:
            from ..core.services.inventory import build_report
            for key, scope in (("dash:common", "common"), ("dash:personal", "personal")):
                self._need[key] = len(build_report(project, scope, "managed").shortages)
        except Exception:  # a count is a nicety; never break the sidebar for it
            self._need = {}
        shown, hid = nav_tabs.split(nav_tabs.all_sections(project), hidden)
        self.tree.clear()
        for sec in shown:
            head = self._section(sec.key, sec.title, scope=sec.scope, collapsed=sec.key in collapsed)
            for tab in sec.tabs:
                it = self._tab(head, tab, False)
                self._add_sheets(it, tab, current_category)
            if sec.scope and not sec.tabs:
                empty = QTreeWidgetItem(head, ["아직 없음 · ‘+’로 추가"])
                empty.setFlags(Qt.NoItemFlags)
                empty.setSizeHint(0, _row_size())
                empty.setData(0, LANE_ROLE, True)
        if hid:
            head = self._section("hidden", nav_tabs.HIDDEN_TITLE, len(hid), collapsed="hidden" in collapsed)
            for tab in hid:
                it = self._tab(head, tab, True)
                self._add_sheets(it, tab, current_category)
        self._select()

    def _add_sheets(self, it: QTreeWidgetItem, tab: nav_tabs.Tab, current_category: int | None) -> None:
        """The open file shows its sheets (subgroups) under it."""
        if tab.category_id is None or tab.category_id != current_category:
            return
        groups = self._project.subgroups(tab.category_id)
        if len(groups) <= 1:
            return
        counts = self._project.count_by_subgroup(tab.category_id)
        it.setData(0, CHEV_ROLE, "open")
        for g in groups:
            sub = QTreeWidgetItem(it, [g or "(그룹 없음)"])
            sub.setData(0, KIND_ROLE, "sheet")
            sub.setData(0, KEY_ROLE, tab.key)
            sub.setData(0, CID_ROLE, tab.category_id)
            sub.setData(0, GROUP_ROLE, g)
            sub.setSizeHint(0, _row_size())
            sub.setFirstColumnSpanned(True)
            sub.setData(0, LANE_ROLE, True)
            sub.setData(0, COUNT_ROLE, counts.get(g, 0))
            sub.setToolTip(0, f"시트 ‘{g}’ · 우클릭: 이름 변경, 삭제")
        it.setExpanded(True)

    def _select(self) -> None:
        self.tree.clearSelection()
        target = None
        itr = QTreeWidgetItemIterator(self.tree)
        while itr.value():
            it = itr.value()
            kind = it.data(0, KIND_ROLE)
            if it.data(0, KEY_ROLE) == self.current_key and kind in ("tab", "sheet"):
                if kind == "tab" and self.current_group is None:
                    target = it
                elif kind == "sheet" and it.data(0, GROUP_ROLE) == self.current_group:
                    target = it
            itr += 1
        # the current row is a raised pill with 600 text and a label-colored symbol (drawn by
        # NavDelegate), so it is told apart by shape and weight, never by color alone
        if target is not None:
            self.tree.setCurrentItem(target)
            target.setSelected(True)
        else:
            self.tree.setCurrentItem(None)

    def set_nav(self, key: str | None) -> None:
        """Mark a home tab (or nothing, e.g. for search) as the current one."""
        if key is not None or self.current_key is None or not self.current_key.startswith("cat:"):
            self.current_key = key
        if key is not None:
            self.current_group = None
        self._select()

    def clear_selection(self) -> None:
        self.current_key = None
        self._select()

    # ---- clicks and menus ------------------------------------------------
    def _clicked(self, it: QTreeWidgetItem, column: int) -> None:
        kind = it.data(0, KIND_ROLE)
        pos = self.tree.viewport().mapFromGlobal(QCursor.pos())
        rect = self.tree.visualItemRect(it)
        if kind == "section":
            scope = it.data(0, SCOPE_ROLE)
            if scope and self.delegate.trailing_rect(rect).contains(pos):
                self.window.add_category(scope)
                return
            self._toggle(it)
        elif kind == "tab":
            lane = rect.left() + tokens.space("row-pad") + self.delegate.chevron_lane()
            if it.data(0, CHEV_ROLE) == "open" and pos.x() < lane:  # the chevron folds the sheets
                it.setExpanded(not it.isExpanded())
                it.setData(0, CHEV_ROLE, "open" if it.isExpanded() else "closed")
                return
            self.window.open_tab(it.data(0, KEY_ROLE), it.data(0, CID_ROLE))
        elif kind == "sheet":
            self.window.show_category(it.data(0, CID_ROLE), group=it.data(0, GROUP_ROLE))

    def _toggle(self, it: QTreeWidgetItem) -> None:
        expand = not it.isExpanded()
        it.setExpanded(expand)
        nav_tabs.set_section_collapsed(it.data(0, KEY_ROLE), not expand)

    def _menu(self, pos) -> None:
        it = self.tree.itemAt(pos)
        if it is None:
            return
        kind = it.data(0, KIND_ROLE)
        w = self.window
        menu = QMenu(self)
        if kind == "section":
            if it.data(0, SCOPE_ROLE):
                menu.addAction("파일(카테고리) 추가…", lambda: w.add_category(it.data(0, SCOPE_ROLE)))
                menu.addAction("엑셀/CSV 가져오기…", w.import_file)
            menu.addAction("접기" if it.isExpanded() else "펼치기", lambda: self._toggle(it))
            menu.addAction("탭 표시 설정…", self.edit_tabs)
        elif kind == "tab":
            key, cid, hidden = it.data(0, KEY_ROLE), it.data(0, CID_ROLE), bool(it.data(0, HIDDEN_ROLE))
            menu.addAction("열기", lambda: w.open_tab(key, cid))
            if cid is not None:
                menu.addAction("이름 바꾸기…", lambda: w.rename_category(cid))
                menu.addAction("시트(하위 그룹) 추가…", lambda: w.add_sheet(cid))
                owner = w.project.owner(cid)
                menu.addAction(f"담당자 지정… ({owner.get('name') or owner.get('email') or '없음'})",
                               lambda: w.edit_owners(cid))
            menu.addSeparator()
            if hidden:
                menu.addAction("다시 표시", lambda: self._set_hidden(key, False))
            else:
                menu.addAction("숨기기 (숨긴 항목으로)", lambda: self._set_hidden(key, True))
            if cid is not None:
                menu.addSeparator()
                menu.addAction("카테고리 설정…", lambda: w.edit_category(cid))
                menu.addAction("카테고리 삭제…", lambda: w.delete_category(cid))
        elif kind == "sheet":
            cid, group = it.data(0, CID_ROLE), it.data(0, GROUP_ROLE)
            menu.addAction("이 시트만 보기", lambda: w.show_category(cid, group=group))
            menu.addAction("항목 추가", lambda: w.add_item_to(cid, group))
            menu.addSeparator()
            menu.addAction("시트 이름 바꾸기…", lambda: w.rename_sheet(cid, group))
            menu.addAction("시트 추가…", lambda: w.add_sheet(cid))
            menu.addAction("시트 삭제…", lambda: w.delete_sheet(cid, group))
        else:
            return
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _set_hidden(self, key: str, hidden: bool) -> None:
        nav_tabs.set_tab_hidden(key, hidden)
        self.refresh(self._project, self._current_category, self.current_group)
        getattr(self.window, "hidden_tabs_changed", lambda: None)()

    def edit_tabs(self) -> None:
        if self._project is None:
            return
        dlg = TabsDialog(nav_tabs.all_sections(self._project), nav_tabs.hidden_tabs(), self)
        if dlg.exec():
            nav_tabs.set_hidden_tabs(dlg.hidden())
            self.refresh(self._project, self._current_category, self.current_group)
            getattr(self.window, "hidden_tabs_changed", lambda: None)()


class TabsDialog(QDialog):
    """Tick the tabs to show on the left; the rest go to 숨긴 항목."""

    def __init__(self, sections: list[nav_tabs.Section], hidden: set[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("탭 표시 설정")
        self.resize(380, 560)
        lay = QVBoxLayout(self)
        note = QLabel("체크한 탭만 왼쪽에 보입니다. 체크를 끈 탭은 ‘숨긴 항목’을 펼치면 들어갈 수 있어요.\n"
                      "이 설정은 이 Mac에서만 적용됩니다.")
        note.setWordWrap(True)
        note.setObjectName("muted")
        lay.addWidget(note)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        lay.addWidget(self.tree, 1)
        if hasattr(window, "members_btn"):  # the reference's bottom row (like "Invite members")
            lay.addSpacing(tokens.space("space-1"))
            lay.addWidget(window.members_btn)
        for sec in sections:
            head = QTreeWidgetItem(self.tree, [sec.title])
            head.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            for tab in sec.tabs:
                it = QTreeWidgetItem(head, [tab.label])
                it.setData(0, KEY_ROLE, tab.key)
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                it.setCheckState(0, Qt.Unchecked if tab.key in hidden else Qt.Checked)
            head.setExpanded(True)
        row = QHBoxLayout()
        for text, state in (("모두 표시", Qt.Checked), ("모두 숨기기", Qt.Unchecked)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, s=state: self._all(s))
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _items(self):
        for i in range(self.tree.topLevelItemCount()):
            head = self.tree.topLevelItem(i)
            for j in range(head.childCount()):
                yield head.child(j)

    def _all(self, state) -> None:
        for it in self._items():
            it.setCheckState(0, state)

    def hidden(self) -> set[str]:
        return {it.data(0, KEY_ROLE) for it in self._items() if it.checkState(0) != Qt.Checked}
