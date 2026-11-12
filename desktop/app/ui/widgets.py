"""Small shared controls drawn to the design system (tahoe style, Desktop profile)."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLayout, QPushButton, QWidget,
)

from .theme import tokens


def glass_capsule(*buttons: QPushButton) -> QFrame:
    """A floating toolbar capsule (tahoe hig-glass-capsule): 34 tall, the buttons inside are
    transparent and 8 shorter (26); icon-only ones are round."""
    cap = QFrame()
    cap.setObjectName("glassCapsule")
    lay = QHBoxLayout(cap)
    inner = tokens.space("tahoe-toolbar-item") - tokens.space("space-2")
    pad = (tokens.space("tahoe-toolbar-item") - inner) // 2
    lay.setContentsMargins(pad, 0, pad, 0)
    lay.setSpacing(0)
    for b in buttons:
        if not b.text():
            b.setFixedWidth(inner)
        lay.addWidget(b)
    return cap


class Segmented(QFrame):
    """SegmentedControl in a glass capsule: a view switch (일 · 주 · 월 · 년), never navigation.
    The chosen segment is a raised pill with 600 text (bundle.css .hig-segmented)."""

    changed = Signal(str)

    def __init__(self, items: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.setObjectName("glassCapsule")
        lay = QHBoxLayout(self)
        inner = tokens.space("tahoe-toolbar-item") - tokens.space("space-2")
        pad = (tokens.space("tahoe-toolbar-item") - inner) // 2
        lay.setContentsMargins(pad, 0, pad, 0)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QPushButton] = {}
        for key, label in items:
            b = QPushButton(label)
            b.setObjectName("segment")
            b.setCheckable(True)
            b.setMinimumWidth(2 * tokens.space("control-md"))
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self.group.addButton(b)
            self.buttons[key] = b
            lay.addWidget(b)

    def set_current(self, key: str) -> None:
        self.buttons[key].setChecked(True)


class Switch(QAbstractButton):
    """macOS switch (components/Toggle, Desktop): 38 x 22 track, accent when on, white knob.
    Use it for a change that applies at once (here: 종일 on/off)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

    def sizeHint(self) -> QSize:
        return QSize(tokens.space("switch-width"), tokens.space("switch-height"))

    def paintEvent(self, _e) -> None:
        w, h = tokens.space("switch-width"), tokens.space("switch-height")
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if not self.isEnabled():
            p.setOpacity(tokens.opacity("opacity-disabled"))
        y = (self.height() - h) / 2
        track = QRectF(0, y, w, h)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(tokens.fill_for_text("accent")) if self.isChecked() else tokens.color("fill-secondary"))
        p.drawRoundedRect(track, h / 2, h / 2)
        inset = 2  # bundle.css: the knob sits 2px inside the track
        d = h - 2 * inset
        x = w - inset - d if self.isChecked() else inset
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(x, y + inset, d, d))
        if self.hasFocus():  # focus ring (Desktop)
            ring = tokens.color("accent")
            ring.setAlphaF(0.45)
            pen = p.pen()
            pen.setColor(ring)
            pen.setWidth(tokens.space("tahoe-focus-ring"))
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(track.adjusted(-1, -1, 1, 1), h / 2 + 1, h / 2 + 1)


class InspectorPanel(QWidget):
    """A page's inspector, shown in the window's inspector box while the page is open.
    The page says whether it wants the box with set_wanted() (never hide the panel itself)."""
    wanted_changed = Signal()

    def __init__(self, wanted: bool = True):
        super().__init__()
        self.wanted = wanted

    def set_wanted(self, on: bool) -> None:
        if on != self.wanted:
            self.wanted = on
            self.wanted_changed.emit()


class InspectorBox(QFrame):
    """components/RegionLayout, 띄운 상자 style: the inspector box beside the canvas box
    (region-box, the brightest face; radius 16 and a 1px region-box-edge border in QSS).
    Inner padding is the margin (20); the panels inside paint nothing. Hidden on pages
    without an inspector, so the canvas box takes the width."""

    def __init__(self):
        super().__init__()
        self.setObjectName("inspectorBox")
        from PySide6.QtWidgets import QStackedWidget, QVBoxLayout
        lay = QVBoxLayout(self)
        m = tokens.space("margin")
        lay.setContentsMargins(m, m, m, m)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack)
        self.panels: dict[int, InspectorPanel] = {}
        self.page = None
        self.setVisible(False)

    def add(self, page: QWidget, panel: InspectorPanel) -> None:
        self.panels[id(page)] = panel
        self.stack.addWidget(panel)
        panel.wanted_changed.connect(lambda: self.show_for(self.page))

    def show_for(self, page) -> None:
        self.page = page
        panel = self.panels.get(id(page))
        if panel is not None:
            self.stack.setCurrentWidget(panel)
            m = self.layout().contentsMargins()
            need = max(panel.minimumWidth(), panel.minimumSizeHint().width())
            self.setMinimumWidth(need + m.left() + m.right())
            self.setMaximumWidth(panel.maximumWidth() + m.left() + m.right()
                                 if panel.maximumWidth() < 10_000 else 16_777_215)
        self.setVisible(panel is not None and panel.wanted)


class CountBadge(QLabel):
    """components/Badge (Desktop): a red capsule 16 tall (min 16 wide, round), 11px 600
    tabular digits in white, over the top-end corner of a button; hidden at 0, 99+ above.
    It lives on `host` (an ancestor that does not clip it) and follows the button."""

    def __init__(self, target: QWidget, host: QWidget):
        super().__init__(host)
        self.target = target
        self.setObjectName("countBadge")
        self.setAlignment(Qt.AlignCenter)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        f = tokens.tabular(self.font())
        f.setPixelSize(tokens.type_style("mac-subheadline")["size"])  # 11
        f.setWeight(QFont.Weight(600))
        self.setFont(f)
        self.setFixedHeight(tokens.space("icon-md"))  # 16
        self.setMinimumWidth(tokens.space("icon-md"))
        for w in (target, target.parentWidget()):
            if w is not None:
                w.installEventFilter(self)
        self.set_count(0)

    def set_count(self, n: int) -> None:
        self.setText("99+" if n > 99 else str(n))
        self.adjustSize()
        self.setVisible(n > 0 and self.target.isVisible())
        self._place()

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type() in (QEvent.Move, QEvent.Resize, QEvent.Show, QEvent.LayoutRequest):
            self._place()
        return False

    def _place(self) -> None:
        t = self.target
        if t.parentWidget() is None:
            return
        corner = t.mapTo(self.parentWidget(), t.rect().topRight())
        h = self.height()
        x = corner.x() - self.width() + h // 2  # its centre sits near the button's corner
        y = max(0, corner.y() - h // 2 + 2)
        self.move(x, y)
        self.raise_()


def fit_select(combo) -> None:
    """Select (no box): the control is as wide as the chosen value, so the value sits right
    against its select-cap; the popup still fits the longest choice."""
    from PySide6.QtGui import QFontMetrics
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QSizePolicy, QStyle, QStyleOptionComboBox

    def fit(*_):
        combo.ensurePolished()  # use the stylesheet font, not the default one
        fm = QFontMetrics(combo.font())
        need = fm.horizontalAdvance(combo.currentText()) + 2
        w = need + 3 * tokens.space("space-1") + tokens.space("select-cap")
        # the style decides how much of the control the value may use; grow by what it holds back
        opt = QStyleOptionComboBox()
        combo.initStyleOption(opt)
        opt.rect = QRect(0, 0, w, max(combo.height(), tokens.space("control-md")))
        field = combo.style().subControlRect(QStyle.CC_ComboBox, opt, QStyle.SC_ComboBoxEditField, combo)
        combo.setFixedWidth(w + max(0, need - field.width()))
        view = combo.view()
        longest = max((fm.horizontalAdvance(combo.itemText(i)) for i in range(combo.count())), default=0)
        view.setMinimumWidth(longest + 3 * tokens.space("control-pad"))

    combo.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    combo.currentIndexChanged.connect(fit)
    # items often arrive with the combo's signals blocked; fit again once the value is set
    later = lambda *_: QTimer.singleShot(0, combo, fit)  # noqa: E731
    combo.model().rowsInserted.connect(later)
    combo.model().modelReset.connect(later)
    combo.model().rowsRemoved.connect(later)
    fit()


class FlowLayout(QLayout):
    """Lays its widgets out in rows and wraps them, like text (chips)."""

    def __init__(self, parent=None, spacing: int | None = None):
        super().__init__(parent)
        self._items = []
        gap = tokens.space("gap-tight") if spacing is None else spacing
        self.setSpacing(gap)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientations(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, w: int) -> int:
        return self._lay(QRectF(0, 0, w, 0).toRect(), True)

    def setGeometry(self, rect) -> None:
        super().setGeometry(rect)
        self._lay(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _lay(self, rect, test: bool) -> int:
        x, y, line = rect.x(), rect.y(), 0
        gap = self.spacing()
        for it in self._items:
            hint = it.sizeHint()
            if x + hint.width() > rect.right() + 1 and line > 0:
                x, y, line = rect.x(), y + line + gap, 0
            if not test:
                from PySide6.QtCore import QRect, QPoint
                it.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + gap
            line = max(line, hint.height())
        return y + line - rect.y()
