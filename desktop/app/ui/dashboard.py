"""Main dashboard, one tab per category type (a segmented switch in the page header):

- 재고 관리 (managed): 충분/부족/소진 against a threshold, with a reorder list.
- 재고 현황 (status): 보유/없음 only, with a list of what the lab doesn't have.

Both lists have a checkbox per row, so the ticked items go into the order cart at once.
"""
from __future__ import annotations

from PySide6.QtCharts import (
    QBarCategoryAxis, QBarSet, QChart, QChartView, QHorizontalStackedBarSeries, QLegend, QPieSeries,
    QValueAxis,
)
from PySide6.QtCore import QMargins, Qt, Signal
from PySide6.QtGui import QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QListWidget, QMessageBox, QPushButton, QScrollArea, QStackedWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import settings as app_settings
from ..core.models import CATEGORY_KINDS, KIND_DESCRIPTIONS, SCOPE_LABELS, SCOPES, state_label
from ..core.project import Project
from ..core.services.inventory import Report, build_report


def hidden_categories(project) -> set[int]:
    """Lists hidden in the sidebar are left off the dashboards too."""
    from ..core import nav_tabs
    hidden = nav_tabs.hidden_tabs()
    return {t.category_id for sec in nav_tabs.all_sections(project) for t in sec.tabs
            if t.key in hidden and t.category_id}
from .table_model import STATE_COLORS, compact_rows, numeric_cell, state_dot
from .theme import icons, tokens
from .widgets import Segmented

KIND_KEY = "dashboard_kind"  # the tab the user had open, kept in the local settings

HISTORY_LABELS = {"value": "수정", "item": "상태", "item_add": "추가", "item_delete": "삭제",
                  "import": "가져오기", "category_add": "카테고리"}
CARD_COLUMNS = 4
# components/Chart: contiguous fills are split by a 2px gap in the surface color, and
# series differ by marker shape as well as color (circle, diamond, square, triangle).
SEGMENT_GAP = 2
LEGEND_SHAPES = [QLegend.MarkerShapeCircle, QLegend.MarkerShapeRotatedRectangle,
                 QLegend.MarkerShapeRectangle, QLegend.MarkerShapeTriangle, QLegend.MarkerShapeStar]

# States drawn for each kind, in legend order. "low" counts as 보유 for status categories.
KIND_STATES = {
    "managed": ["ok", "soon", "low", "out", "unknown"],
    "status": ["ok", "out", "unknown"],
}


def _card() -> QFrame:
    f = QFrame()
    f.setObjectName("card")
    return f


def _counts(summary, kind: str) -> dict[str, int]:
    c = dict(summary.counts)
    if kind == "status":
        c["ok"] += c.pop("low") + c.pop("soon")
    return c


class KpiCard(QFrame):
    def __init__(self, title: str, state: str | None = None):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(*[tokens.space("card-pad")] * 4)  # card padding space-4
        self.title = QLabel(title)
        self.title.setObjectName("muted")
        self.value = QLabel("0")
        self.value.setObjectName("kpiValue")
        self.value.setFont(tokens.tabular(self.value.font()))
        head = QHBoxLayout()
        head.setSpacing(tokens.space("gap-tight"))
        self.state = state
        self.dot = QLabel()
        if state:  # StatusIndicator --plain: the color sits in the dot, the number stays `label`
            head.addWidget(self.dot)
        self.sub = QLabel("")
        self.sub.setObjectName("muted")
        head.addWidget(self.title, 1)
        lay.addLayout(head)
        lay.addWidget(self.value)
        lay.addWidget(self.sub)

    def set(self, value: str, sub: str = "") -> None:
        if self.state:  # redrawn each refresh, so it follows the theme
            self.dot.setPixmap(state_dot(self.state).pixmap(tokens.space("icon-sm")))
        self.value.setText(value)
        self.sub.setText(sub)


class CategoryCard(QFrame):
    clicked = Signal(int)

    def __init__(self, summary, kind: str):
        super().__init__()
        self.setObjectName("card")
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("interactive", True)  # Card: hover darkens 8%
        self.category_id = summary.category.id
        counts = _counts(summary, kind)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(*[tokens.space("card-pad")] * 4)
        chart = QChart()
        series = QPieSeries()
        series.setHoleSize(0.58)
        series.setPieSize(0.95)
        for state in KIND_STATES[kind]:
            if counts[state]:
                sl = series.append(state_label(state, kind), counts[state])
                sl.setColor(STATE_COLORS[state])
                sl.setBorderColor(tokens.color("region-box"))  # card surface
                sl.setBorderWidth(SEGMENT_GAP)
        if summary.total == 0:
            sl = series.append("비어 있음", 1)
            sl.setColor(tokens.color("gray-5"))
        chart.addSeries(series)
        chart.legend().hide()
        chart.setBackgroundVisible(False)
        chart.setMargins(QMargins(0, 0, 0, 0))
        chart.layout().setContentsMargins(0, 0, 0, 0)
        view = QChartView(chart)
        view.setRenderHint(QPainter.Antialiasing)
        view.setFixedSize(92, 92)
        view.setStyleSheet("background: transparent;")
        view.setAttribute(Qt.WA_TransparentForMouseEvents)
        view.setAccessibleName(f"{summary.category.name} 상태 비율 도넛 차트")
        lay.addWidget(view)
        text = QVBoxLayout()
        name = QLabel(summary.category.name)
        name.setObjectName("cardTitle")
        name.setWordWrap(True)
        total = QLabel(f"{summary.total}")
        total.setObjectName("cardValue")
        if kind == "managed":
            line = f"임박 {counts['soon']} · 부족 {counts['low']} · 소진 {counts['out']}"
            if summary.tracked:
                note = f"주문 필요 {summary.shortage_ratio * 100:.0f}%"
            else:
                note = "수량 컬럼 미지정"
        else:
            line = f"보유 {counts['ok']} · 없음 {counts['out']}"
            note = f"미확인 {counts['unknown']}" if counts["unknown"] else "모두 확인됨"
        nb = " · ".join(part.replace(" ", "\u00a0") for part in line.split(" · "))  # wrap between counts only
        detail = QLabel(f"{nb}\n{note}")
        detail.setObjectName("muted")
        detail.setWordWrap(True)  # narrow windows wrap the counts instead of scrolling sideways
        self.setAccessibleName(f"{summary.category.name}, 전체 {summary.total}개, {line}, {note}")
        self.setToolTip("눌러서 이 파일 열기")
        text.addWidget(name)
        text.addWidget(total)
        text.addWidget(detail)
        text.addStretch(1)
        lay.addLayout(text, 1)

    def mouseReleaseEvent(self, event):
        self.clicked.emit(self.category_id)
        super().mouseReleaseEvent(event)


class KindSection(QWidget):
    """KPI row, category cards and an attention list for one category type."""
    open_category = Signal(int)
    open_item = Signal(int, int)
    add_to_cart = Signal(list)  # item ids ticked in the list

    def __init__(self, kind: str):
        super().__init__()
        self.kind = kind
        self.report: Report | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(tokens.space("gap-group"))

        head = QHBoxLayout()
        title = QLabel(f"{CATEGORY_KINDS[kind]} 품목")
        title.setObjectName("sectionHeader")
        desc = QLabel(KIND_DESCRIPTIONS[kind])
        desc.setObjectName("muted")
        head.addWidget(title)
        head.addSpacing(tokens.space("gap-group"))
        head.addWidget(desc, 1)
        root.addLayout(head)

        kpis = QHBoxLayout()
        if kind == "managed":
            specs = [("total", "전체 항목", None), ("ok", "충분", "ok"), ("soon", "주문 임박", "soon"),
                     ("low", "부족", "low"), ("out", "소진", "out")]
        else:
            specs = [("total", "전체 항목", None), ("ok", "보유", "ok"), ("out", "없음", "out"),
                     ("unknown", "미확인", "unknown")]
        self.kpis: dict[str, KpiCard] = {}
        for key, label, color_state in specs:
            card = KpiCard(label, color_state if color_state and color_state != "unknown" else None)
            self.kpis[key] = card
            kpis.addWidget(card)
        root.addLayout(kpis)

        self.cards = QGridLayout()
        self.cards.setSpacing(tokens.space("gap-group"))
        root.addLayout(self.cards)

        lower = QHBoxLayout()
        lower.setSpacing(tokens.space("gap-group"))
        if kind == "managed":
            chart_card = _card()
            cl = QVBoxLayout(chart_card)
            cl.setContentsMargins(*[tokens.space("card-pad")] * 4)
            # title = the takeaway as a number, subtitle = range and context (components/Chart)
            th = QHBoxLayout()
            tv = QVBoxLayout()
            tv.setSpacing(0)
            self.chart_title = QLabel()
            self.chart_title.setObjectName("sectionTitle")
            self.chart_sub = QLabel()
            self.chart_sub.setObjectName("muted")
            self.chart_sub.setWordWrap(True)
            tv.addWidget(self.chart_title)
            tv.addWidget(self.chart_sub)
            th.addLayout(tv, 1)
            self.chart_toggle = QPushButton("표로 보기")
            self.chart_toggle.setObjectName("plain")
            self.chart_toggle.setCheckable(True)
            self.chart_toggle.setToolTip("같은 데이터를 표로 봅니다")
            self.chart_toggle.toggled.connect(self._toggle_chart)
            th.addWidget(self.chart_toggle, 0, Qt.AlignTop)
            cl.addLayout(th)
            self.chart_stack = QStackedWidget()
            self.bar_view = QChartView()
            self.bar_view.setRenderHint(QPainter.Antialiasing)
            self.bar_view.setStyleSheet("background: transparent; border: none;")
            self.bar_view.setMinimumHeight(260)
            self.chart_stack.addWidget(self.bar_view)
            self.bar_table = QTableWidget()
            self.bar_table.verticalHeader().setVisible(False)
            self.bar_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.bar_table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.chart_stack.addWidget(self.bar_table)
            cl.addWidget(self.chart_stack)
            lower.addWidget(chart_card, 1)
        else:
            self.bar_view = None

        list_card = _card()
        ll = QVBoxLayout(list_card)
        ll.setContentsMargins(*[tokens.space("card-pad")] * 4)
        lh = QHBoxLayout()
        lh.setSpacing(tokens.space("gap-tight"))
        self.list_title = QLabel()
        self.list_title.setObjectName("sectionTitle")
        lh.addWidget(self.list_title, 1)
        self.check_all_btn = QPushButton("모두 선택")
        self.check_all_btn.setObjectName("plain")
        self.check_all_btn.setToolTip("목록의 항목을 모두 체크하거나 모두 해제합니다")
        self.check_all_btn.clicked.connect(self._check_all)
        self.cart_btn = QPushButton("장바구니에 담기")
        self.cart_btn.setObjectName("primary")
        self.cart_btn.setToolTip("체크한 항목을 한 번에 장바구니에 담습니다")
        self.cart_btn.clicked.connect(self._cart_checked)
        lh.addWidget(self.check_all_btn)
        lh.addWidget(self.cart_btn)
        ll.addLayout(lh)
        self.list_sub = QLabel()
        self.list_sub.setObjectName("muted")
        self.list_sub.setWordWrap(True)
        ll.addWidget(self.list_sub)
        # column 0 is the checkbox (components/Checkbox: 16, accent when on)
        if kind == "managed":
            headers = ["", "상태", "이름", "카테고리", "수량 / 기준", "위치"]
        else:
            headers = ["", "상태", "이름", "카테고리", "그룹", "위치"]
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Fixed)
        self.table.horizontalHeader().resizeSection(0, tokens.space("check-size") + 2 * tokens.space("row-pad"))
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setMinimumHeight(260)
        compact_rows(self.table, numeric=(4,) if kind == "managed" else ())
        self.table.cellDoubleClicked.connect(self._row_clicked)
        self.table.itemChanged.connect(self._checks_changed)
        ll.addWidget(self.table)
        lower.addWidget(list_card, 1)
        root.addLayout(lower)

    def refresh(self, project: Project, scope: str | None) -> Report:
        rep = build_report(project, scope, self.kind, hidden_categories(project))
        self.report = rep
        total = rep.total_items
        if self.kind == "managed":
            tracked = total - rep.count("unknown")
            self.kpis["total"].set(f"{total:,}", f"카테고리 {len(rep.summaries)}개 · 수량 추적 {tracked:,}개")
            for key in ("ok", "soon", "low", "out"):
                n = rep.count(key)
                self.kpis[key].set(f"{n:,}", f"추적 항목의 {n / tracked * 100:.0f}%" if tracked else "")
        else:
            have = rep.count("ok") + rep.count("low") + rep.count("soon")
            self.kpis["total"].set(f"{total:,}", f"카테고리 {len(rep.summaries)}개")
            for key, n in (("ok", have), ("out", rep.count("out")), ("unknown", rep.count("unknown"))):
                self.kpis[key].set(f"{n:,}", f"전체의 {n / total * 100:.0f}%" if total else "")

        while self.cards.count():
            w = self.cards.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        for i, s in enumerate(rep.summaries):
            card = CategoryCard(s, self.kind)
            card.clicked.connect(self.open_category.emit)
            self.cards.addWidget(card, i // CARD_COLUMNS, i % CARD_COLUMNS)
        if self.bar_view is not None:
            self._draw_bars(rep)
        self._fill_table(rep)
        return rep

    def _fill_table(self, rep: Report) -> None:
        if self.kind == "managed":
            rows = rep.shortages
            self.list_title.setText(f"주문이 필요한 항목 {len(rows):,}개")
            self.list_sub.setText("임박 + 부족 + 소진 · 체크한 항목을 한 번에 장바구니에 담을 수 있어요")
        else:
            rows = [r for r in rep.shortages if r.info.state == "out"]
            self.list_title.setText(f"보유하지 않은 항목 {len(rows):,}개")
            self.list_sub.setText("없음 · 체크한 항목을 한 번에 장바구니에 담을 수 있어요")
        # a reload keeps what was ticked, so a background refresh never loses the picks
        keep = set(self.checked_ids())
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for r in rows[:500]:
            row = self.table.rowCount()
            self.table.insertRow(row)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            chk.setCheckState(Qt.Checked if r.item_id in keep else Qt.Unchecked)
            chk.setData(Qt.UserRole, (r.category.id, r.item_id))
            chk.setToolTip("체크하면 ‘장바구니에 담기’로 함께 담깁니다")
            self.table.setItem(row, 0, chk)
            st = QTableWidgetItem(state_label(r.info.state, self.kind))
            st.setIcon(state_dot(r.info.state))
            self.table.setItem(row, 1, st)
            self.table.setItem(row, 2, QTableWidgetItem(r.name))
            if self.kind == "managed":
                self.table.setItem(row, 3, QTableWidgetItem(
                    r.category.name + (f" / {r.subgroup}" if r.subgroup else "")))
                qty = "-" if r.info.quantity is None else f"{r.info.quantity:g}{r.info.unit} / {r.info.threshold:g}"
                self.table.setItem(row, 4, numeric_cell(qty))
            else:
                self.table.setItem(row, 3, QTableWidgetItem(r.category.name))
                self.table.setItem(row, 4, QTableWidgetItem(r.subgroup))
            self.table.setItem(row, 5, QTableWidgetItem(r.location))
        self.table.blockSignals(False)
        self.table.resizeColumnToContents(1)
        self._checks_changed()

    # ---- checkboxes and the cart -------------------------------------------
    def checked_ids(self) -> list[int]:
        out = []
        for row in range(self.table.rowCount()):
            it = self.table.item(row, 0)
            if it is not None and it.checkState() == Qt.Checked:
                out.append(it.data(Qt.UserRole)[1])
        return out

    def _checks_changed(self, *_):
        n, total = len(self.checked_ids()), self.table.rowCount()
        self.cart_btn.setText(f"장바구니에 담기 ({n})" if n else "장바구니에 담기")
        self.cart_btn.setEnabled(bool(n))
        self.check_all_btn.setEnabled(bool(total))
        self.check_all_btn.setText("모두 해제" if total and n == total else "모두 선택")

    def _check_all(self) -> None:
        total = self.table.rowCount()
        state = Qt.Unchecked if total and len(self.checked_ids()) == total else Qt.Checked
        self.table.blockSignals(True)
        for row in range(total):
            self.table.item(row, 0).setCheckState(state)
        self.table.blockSignals(False)
        self._checks_changed()

    def _cart_checked(self) -> None:
        ids = self.checked_ids()
        if not ids:
            return
        self.add_to_cart.emit(ids)
        self.table.blockSignals(True)  # 담은 항목은 체크를 풉니다
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.Unchecked)
        self.table.blockSignals(False)
        self._checks_changed()

    def _toggle_chart(self, as_table: bool) -> None:
        self.chart_stack.setCurrentIndex(1 if as_table else 0)
        self.chart_toggle.setText("차트로 보기" if as_table else "표로 보기")

    def _draw_bars(self, rep: Report) -> None:
        """Stacked bars per category (components/Chart).

        The marks keep the state colors (they mean 충분/부족/소진), with a legend whose
        markers also differ in shape, a takeaway title, and the same numbers as a table.
        """
        states = KIND_STATES[self.kind]
        need_states = [s for s in states if s in ("soon", "low", "out")]
        need = {s.category.name: sum(s.counts[k] for k in need_states) for s in rep.summaries}
        total_need = sum(need.values())
        top = max(need, key=need.get) if need else ""
        self.chart_title.setText(f"주문이 필요한 항목 {total_need:,}개")
        sub = f"카테고리 {len(rep.summaries)}개의 상태별 항목 수"
        if total_need:
            sub += f" · 가장 많은 곳: {top} {need[top]:,}개"
        self.chart_sub.setText(sub)

        surface = tokens.color("region-box")  # card surface, for the gaps
        chart = QChart()
        chart.setAnimationOptions(QChart.NoAnimation)
        series = QHorizontalStackedBarSeries()
        # Horizontal bars run bottom-up; reverse so the first category is on top.
        summaries = list(reversed(rep.summaries))
        names = [s.category.name for s in summaries]
        for state in states:
            bs = QBarSet(state_label(state, self.kind))
            bs.setColor(STATE_COLORS[state])
            bs.setPen(QPen(surface, SEGMENT_GAP))
            for s in summaries:
                bs.append(s.counts[state])
            series.append(bs)
        series.setBarWidth(0.6)
        chart.addSeries(series)

        tick_font = QFont(self.font())
        tick_font.setPixelSize(tokens.type_style("mac-footnote")["size"])
        axis_y = QBarCategoryAxis()  # category names are data labels: label color
        axis_y.append(names)
        axis_y.setLabelsColor(tokens.color("label"))
        axis_y.setGridLineVisible(False)
        axis_y.setLinePenColor(tokens.color("label-tertiary"))
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_y)
        axis_x = QValueAxis()  # bars start at 0, familiar steps
        axis_x.setRange(0, max([s.total for s in summaries] + [1]))
        axis_x.applyNiceNumbers()
        axis_x.setLabelFormat("%d")
        axis_x.setLabelsFont(tick_font)
        axis_x.setLabelsColor(tokens.text("label-secondary", "region-box"))
        axis_x.setGridLineColor(tokens.color("separator"))
        axis_x.setLineVisible(False)
        chart.addAxis(axis_x, Qt.AlignBottom)
        series.attachAxis(axis_x)

        legend = chart.legend()  # always shown for 2+ series
        legend.setAlignment(Qt.AlignBottom)
        legend.setLabelColor(tokens.color("label"))
        for marker, shape in zip(legend.markers(series), LEGEND_SHAPES):
            marker.setShape(shape)
        chart.setBackgroundVisible(False)
        chart.setPlotAreaBackgroundVisible(False)
        chart.setMargins(QMargins(0, 0, 0, 0))
        self.bar_view.setChart(chart)
        self.bar_view.setMinimumHeight(max(260, 46 * len(names) + 90))
        self.bar_view.setAccessibleName("카테고리별 재고 상태 가로 누적 막대 차트")
        self.bar_view.setAccessibleDescription(
            f"세로축은 카테고리 {len(names)}개, 가로축은 항목 수(0부터 {int(axis_x.max())}까지), "
            f"막대는 상태별로 나뉩니다. {sub}.")

        # the same data as a table ("표로 보기")
        heads = ["카테고리"] + [state_label(st, self.kind) for st in states] + ["합계"]
        t = self.bar_table
        t.clear()
        t.setColumnCount(len(heads))
        t.setHorizontalHeaderLabels(heads)
        compact_rows(t, numeric=tuple(range(1, len(heads))))
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        t.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        t.setRowCount(len(rep.summaries))
        for row, s in enumerate(rep.summaries):
            t.setItem(row, 0, QTableWidgetItem(s.category.name))
            for col, st in enumerate(states, start=1):
                t.setItem(row, col, numeric_cell(f"{s.counts[st]:,}"))
            t.setItem(row, len(heads) - 1, numeric_cell(f"{s.total:,}"))

    def _row_clicked(self, row: int, col: int) -> None:
        if col == 0:  # double-clicking the checkbox only ticks it
            return
        cat_id, item_id = self.table.item(row, 0).data(Qt.UserRole)
        self.open_item.emit(cat_id, item_id)


class DashboardPage(QWidget):
    open_category = Signal(int)
    open_item = Signal(int, int)  # category id, item id
    add_to_cart = Signal(list)    # item ids

    TITLES = {"common": "공용 대시보드", "personal": "개인 품목 대시보드", None: "대시보드"}

    def __init__(self, project: Project, parent=None, scope: str | None = None, work=None):
        super().__init__(parent)
        self.project = project
        self.scope: str | None = scope

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        body.setObjectName("pageBody")
        scroll.setWidget(body)
        root = QVBoxLayout(body)
        root.setContentsMargins(*[tokens.space("margin")] * 4)  # window margin 20
        root.setSpacing(tokens.space("gap-section"))  # 24 between sections

        head = QHBoxLayout()
        title = QLabel(self.TITLES.get(scope, "대시보드"))
        title.setObjectName("pageTitle")
        head.addWidget(title)
        head.addSpacing(tokens.space("gap-group"))
        sub_title = QLabel({"common": "Common Asset의 모든 파일", "personal": "Personal Asset의 모든 파일"}
                           .get(scope, ""))
        sub_title.setObjectName("muted")
        head.addWidget(sub_title)
        self.scope_group = QButtonGroup(self)
        if scope is None:  # the old all-in-one dashboard keeps its scope switch
            for key, label in [(None, "전체")] + SCOPES:
                b = QPushButton(label)
                b.setCheckable(True)
                b.setObjectName("segment")
                b.setProperty("scope", key)
                self.scope_group.addButton(b)
                head.addWidget(b)
                if key is None:
                    b.setChecked(True)
            self.scope_group.buttonClicked.connect(self._scope_clicked)
        head.addStretch(1)
        # one tab per category type: a view switch of this page (components/SegmentedControl)
        self.kind = app_settings.load_settings().get(KIND_KEY, "managed")
        if self.kind not in CATEGORY_KINDS:
            self.kind = "managed"
        self.kind_switch = Segmented(list(CATEGORY_KINDS.items()))
        self.kind_switch.set_current(self.kind)
        self.kind_switch.setToolTip("재고 관리 / 재고 현황 카테고리를 탭으로 나눠 봅니다")
        self.kind_switch.changed.connect(self.set_kind)
        head.addWidget(self.kind_switch)
        head.addSpacing(tokens.space("gap-group"))
        self.export_btn = QPushButton("주문 필요 목록 내보내기")
        icons.set_icon(self.export_btn, "square.and.arrow.up")  # HIG: Share, Export
        self.export_btn.clicked.connect(self._export_shortages)
        head.addWidget(self.export_btn)
        root.addLayout(head)

        # 장비 예약 | 진행 중인 주문: two boxes side by side on the common dashboard
        from .orders_view import OrderCard
        from .reservation_view import ReservationCard
        self.reservation_card = ReservationCard()
        self.order_card = OrderCard()
        self.reservation_card.setVisible(False)
        self.order_card.setVisible(False)
        if scope != "personal":
            pair = QHBoxLayout()
            pair.setSpacing(tokens.space("gap-section"))
            pair.addWidget(self.reservation_card, 1)
            pair.addWidget(self.order_card, 1)
            root.addLayout(pair)
        from .notices_view import NoticeCard
        self.notice_card = NoticeCard()
        self.notice_card.setVisible(False)
        if scope != "personal":  # notices are shown on the common dashboard only
            root.addWidget(self.notice_card)
        # 업무: one box per project (this turn, the next, 업무 완료) on the common dashboard
        self.work_section = None
        if work is not None:
            from .work_view import WorkSection
            self.work_section = WorkSection(work)
            root.addWidget(self.work_section)

        self.empty_hint = QLabel()
        self.empty_hint.setObjectName("muted")
        self.empty_hint.setWordWrap(True)
        root.addWidget(self.empty_hint)

        self.sections = {kind: KindSection(kind) for kind in CATEGORY_KINDS}
        for section in self.sections.values():
            section.open_category.connect(self.open_category.emit)
            section.open_item.connect(self.open_item.emit)
            section.add_to_cart.connect(self.add_to_cart.emit)
            root.addWidget(section)

        self.hist_card = _card()
        hl = QVBoxLayout(self.hist_card)
        hl.setContentsMargins(*[tokens.space("card-pad")] * 4)
        ht = QLabel("최근 변경")
        ht.setObjectName("sectionTitle")
        hl.addWidget(ht)
        self.history = QListWidget()
        self.history.setObjectName("history")
        self.history.setMinimumHeight(160)
        hl.addWidget(self.history)
        root.addWidget(self.hist_card)
        root.addStretch(1)

    def set_project(self, project: Project) -> None:
        self.project = project
        self.refresh()

    def _scope_clicked(self, button) -> None:
        self.scope = button.property("scope")
        self.refresh()

    def set_kind(self, kind: str) -> None:
        if kind not in CATEGORY_KINDS:
            return
        self.kind = kind
        self.kind_switch.set_current(kind)
        data = app_settings.load_settings()
        data[KIND_KEY] = kind
        app_settings.save_settings(data)
        self.refresh()

    def refresh(self) -> None:
        reports = {k: s.refresh(self.project, self.scope) for k, s in self.sections.items()}
        rep = reports[self.kind]
        in_scope = {s.category.id for s in rep.summaries}
        for k, s in self.sections.items():
            s.setVisible(k == self.kind and bool(reports[k].summaries))
        self.export_btn.setVisible(self.kind == "managed")
        scope_name = SCOPE_LABELS.get(self.scope or "", "")
        where = scope_name + "에 " if scope_name else ""
        if any(r.summaries for r in reports.values()):
            other = next(CATEGORY_KINDS[k] for k in CATEGORY_KINDS if k != self.kind)
            self.empty_hint.setText(
                f"{where}‘{CATEGORY_KINDS[self.kind]}’ 카테고리가 없습니다. ‘{other}’ 탭을 보거나, "
                "카테고리를 우클릭해 카테고리 설정에서 종류를 바꾸세요.")
        else:
            self.empty_hint.setText(
                f"{where}아직 카테고리가 없습니다. 왼쪽 ‘+’ 버튼으로 카테고리를 "
                "만들거나 파일 > 가져오기로 엑셀/CSV를 불러오세요.")
        self.empty_hint.setVisible(not in_scope)
        self.hist_card.setVisible(bool(in_scope))

        self.history.clear()
        for h in self.project.history(limit=200):
            if h["category_id"] not in in_scope:
                continue
            if self.history.count() >= 40:
                break
            label = HISTORY_LABELS.get(h["action"], h["action"])
            title = self.project.item_title(h["item_id"]) if h["item_id"] else ""
            detail = h["detail"] if h["detail"] != title else ""
            self.history.addItem(f"{h['ts'].replace('T', ' ')}   [{label}] {title}  {detail}".strip())

    def _export_shortages(self) -> None:
        from ..core.exporter import export_shortages
        rep = self.sections["managed"].report
        if not rep:
            return
        scope = SCOPE_LABELS.get(self.scope or "", "전체")
        path, _ = QFileDialog.getSaveFileName(self, "주문 필요 목록 내보내기", f"주문 필요 ({scope}).xlsx",
                                              "Excel (*.xlsx);;CSV (*.csv)")
        if path:
            n = export_shortages(rep, path)
            QMessageBox.information(self, "내보내기", f"{n}개 항목을 저장했습니다.\n{path}")
