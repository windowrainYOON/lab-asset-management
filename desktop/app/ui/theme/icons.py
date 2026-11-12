"""SF Symbols-style icons from the design system (assets/Icons/icons.json → icons.json here).

components/Icon/README.md:
- Pick from the HIG standard icon table first (`hig: true`), then the other symbols in
  icons.json, then a custom symbol drawn on the same 24×24 grid with the same stroke.
- One style for the whole app: monochrome, the label color by default; accent or a
  state color only where it means something.
- The stroke follows the weight of the text next to it (400 → regular, 600 → semibold).
- Desktop sizes (Icon README, proportions.md): icon-md 16 in buttons, rows, sidebar and
  toolbar; icon-sm 14 in menus and badges; icon-lg 20 for empty states.
- Icons sit at the start of their label with a gap-tight (6) gap.
- Icon-only buttons are square (control-md, 28) with a tooltip and an accessible name;
  the 44pt touch target is a Mobile rule and is not used here.

The app is not native AppKit, so the SF Symbols themselves are not available to Qt;
these are the design system's look-alike SVGs, rendered in the current theme's colors.
`refresh()` repaints every icon set through `set_icon()` when the theme changes.
"""
from __future__ import annotations

import json
import tempfile
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QAbstractButton, QApplication

from . import tokens

# stroke width on the 24 grid for each font weight (bundle.css .hig-icon--light … --bold)
STROKES = {300: 1.25, 400: 1.75, 500: 2.0, 600: 2.25, 700: 2.5}
# Qt puts its own small gap between a button's icon and text; the rest of gap-tight is
# drawn as empty space inside the icon pixmap.
QT_ICON_TEXT_GAP = 4

# Priority 3: custom symbols on the same grid and stroke, for actions the set lacks.
CUSTOM = {
    # Desktop disclosure triangles (layout-desktop.md: closed points to the start, open points down)
    "arrowtriangle.right.fill": {
        "hig": False, "rtl": "flip",
        "svg": '<path d="M8 4.5 18 12 8 19.5z" fill="currentColor"/>',
    },
    "arrowtriangle.down.fill": {
        "hig": False, "rtl": "keep",
        "svg": '<path d="M4.5 8 12 18 19.5 8z" fill="currentColor"/>',
    },
    "arrow.clockwise": {  # reload (SF Symbols name)
        "hig": False, "rtl": "keep",
        "svg": '<path d="M18.5 12a6.5 6.5 0 1 1-1.9-4.6"/><path d="M17.2 3.6v4h-4"/>',
    },
    "square.and.arrow.down": {  # import (SF Symbols name; mirror of square.and.arrow.up)
        "hig": False, "rtl": "keep",
        "svg": '<path d="M12 3v11"/><path d="m8 10 4 4 4-4"/>'
               '<path d="M8.5 8H6.5A1.5 1.5 0 0 0 5 9.5v9A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5v-9A1.5 1.5 0 0 0 17.5 8h-2"/>',
    },
    "tablecells": {  # columns of a table (SF Symbols name)
        "hig": False, "rtl": "keep",
        "svg": '<rect x="3.5" y="5" width="17" height="14" rx="2"/><path d="M3.5 10h17M9.5 10v9M14.5 10v9"/>',
    },
    "cart": {  # shopping cart (SF Symbols name)
        "hig": False, "rtl": "flip",
        "svg": '<path d="M2.5 4h2.6l2.2 10.2a1.5 1.5 0 0 0 1.5 1.2h8.4a1.5 1.5 0 0 0 1.5-1.1L20.5 8H6"/>'
               '<circle cx="9.5" cy="19.5" r="1.2"/><circle cx="17" cy="19.5" r="1.2"/>',
    },
    "clock": {  # reservations (SF Symbols name)
        "hig": False, "rtl": "keep",
        "svg": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    },
    "shippingbox": {  # orders (SF Symbols name)
        "hig": False, "rtl": "keep",
        "svg": '<path d="M12 3.5 20 7.5v9L12 20.5 4 16.5v-9z"/><path d="M4 7.5 12 11.5 20 7.5M12 11.5v9"/>'
               '<path d="m8 5.5 8 4"/>',
    },
    "wonsign": {  # budget (SF Symbols name)
        "hig": False, "rtl": "keep",
        "svg": '<path d="M4.5 6 8 18l4-11 4 11 3.5-12"/><path d="M3.5 10.5h17M3.5 13.5h17"/>',
    },
    "cart.badge.plus": {  # add to cart (SF Symbols name)
        "hig": False, "rtl": "flip",
        "svg": '<path d="M2.5 4h2.6l2.2 10.2a1.5 1.5 0 0 0 1.5 1.2h8.4a1.5 1.5 0 0 0 1.5-1.1L20.5 8H6"/>'
               '<circle cx="9.5" cy="19.5" r="1.2"/><circle cx="17" cy="19.5" r="1.2"/>'
               '<path d="M13 8.5v4.5M10.75 10.75h4.5"/>',
    },
}


@lru_cache(maxsize=1)
def _all() -> dict:
    data = json.loads(Path(__file__).with_name("icons.json").read_text(encoding="utf-8"))["icons"]
    return {**data, **CUSTOM}


def names() -> list[str]:
    return sorted(_all())


def svg(name: str, color: QColor, weight: int = 400) -> bytes:
    """The symbol as a standalone SVG in one color (monochrome rendering)."""
    body = _all()[name]["svg"]
    hexv = color.name()
    body = (body.replace("currentColor", hexv)
            .replace("var(--hig-icon-knockout, var(--background))", tokens.color("background").name()))
    alpha = round(color.alphaF(), 3)
    doc = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{hexv}" '
           f'stroke-opacity="{alpha}" fill-opacity="{alpha}" stroke-width="{STROKES[weight]}" '
           f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    return doc.encode()


def pixmap(name: str, color: QColor, weight: int = 400, size: int | None = None, pad: int = 0,
           ratio: float = 2.0) -> QPixmap:
    """Rendered at `ratio` for Retina; `pad` adds empty space after the symbol (icon-to-text gap)."""
    size = size or tokens.space("icon-md")
    pm = QPixmap(QSize(round((size + pad) * ratio), round(size * ratio)))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(QByteArray(svg(name, color, weight))).render(p, QRectF(0, 0, size, size))
    p.end()
    return pm


def icon(name: str, color: QColor | str = "label", weight: int = 400, size: int | None = None,
         pad: int = 0) -> QIcon:
    """A QIcon with normal and disabled looks (disabled = opacity-disabled, like the label)."""
    c = tokens.color(color) if isinstance(color, str) else QColor(color)
    ic = QIcon(pixmap(name, c, weight, size, pad))
    dim = QColor(c)
    dim.setAlphaF(c.alphaF() * tokens.opacity("opacity-disabled"))
    ic.addPixmap(pixmap(name, dim, weight, size, pad), QIcon.Disabled)
    return ic


def icon_size(pad: int = 0) -> QSize:
    s = tokens.space("icon-md")
    return QSize(s + pad, s)


def label_gap() -> int:
    """Extra space after a symbol so icon + Qt's own gap = gap-tight (6)."""
    return max(0, tokens.space("gap-tight") - QT_ICON_TEXT_GAP)


# ---- buttons --------------------------------------------------------------
def _on_fill(name: str) -> QColor:
    from PySide6.QtGui import QColor
    fill = QColor(tokens.fill_for_text(name))
    for lab in ("on-accent", "label", "background"):
        if tokens.contrast(tokens.color(lab), fill) >= tokens.MIN_TEXT_CONTRAST:
            return tokens.color(lab)
    return tokens.color("label")


def _button_color(btn: QAbstractButton) -> QColor:
    """The same color as the button's label (see base.qss button variants)."""
    kind = btn.objectName()
    if kind == "primary":
        return tokens.on_color("accent")
    if kind in ("plain", "tinted", "addButton"):
        return tokens.text("accent")
    if kind == "destructive":  # hig-delete: white glyph on the red squircle; alerts keep red text
        from PySide6.QtWidgets import QMessageBox
        w = btn.parentWidget()
        while w is not None and not isinstance(w, QMessageBox):
            w = w.parentWidget()
        if w is not None:
            return tokens.text("red", "background-secondary")
        return _on_fill("red")
    return tokens.color("label")


def _paint(btn: QAbstractButton) -> None:
    name = btn.property("higIcon")
    pad = label_gap() if btn.text() else 0
    weight = 500 if btn.objectName() == "primary" else 400  # Desktop button labels: 400 (prominent 500)
    btn.setIcon(icon(name, _button_color(btn), weight, pad=pad))
    btn.setIconSize(icon_size(pad))


def set_icon(btn: QAbstractButton, name: str, accessible_name: str | None = None) -> QAbstractButton:
    """Put a symbol at the start of a button's label (or alone, for icon-only buttons)."""
    if name not in _all():
        raise KeyError(name)
    btn.setProperty("higIcon", name)
    if not btn.text():
        btn.setFixedWidth(tokens.space("control-md"))  # square icon button (hig-button--icon)
        btn.setProperty("iconOnly", True)  # base.qss keeps it square (no side padding)
        if accessible_name:
            btn.setAccessibleName(accessible_name)
            if not btn.toolTip():
                btn.setToolTip(accessible_name)
    _paint(btn)
    QTimer.singleShot(0, btn, lambda: _paint(btn))  # again once its objectName (variant) is set
    return btn


def refresh() -> None:
    """Repaint button icons in the current theme (called by apply_theme)."""
    app = QApplication.instance()
    if app is None:
        return
    for w in app.allWidgets():
        if isinstance(w, QAbstractButton) and w.property("higIcon"):
            _paint(w)


# ---- stylesheet images ---------------------------------------------------------
def qss_url(name: str, color: QColor, weight: int = 400, size: int | None = None) -> str:
    """Write the symbol as PNG (+ @2x, which Qt picks on Retina) and return a QSS url()."""
    size = size or tokens.space("icon-sm")
    folder = Path(tempfile.gettempdir()) / "asset-manager-icons"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{name}-{color.name(QColor.HexArgb)[1:]}-{weight}-{size}"
    base = folder / f"{stem}.png"
    if not base.exists():
        pixmap(name, color, weight, size, ratio=1.0).save(str(base))
        pixmap(name, color, weight, size, ratio=2.0).save(str(folder / f"{stem}@2x.png"))
    return f"url({base.as_posix()})"
