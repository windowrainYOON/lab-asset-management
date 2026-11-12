"""Apple HIG design tokens (tokens.json, copied from the team's design system) for the Qt UI.

Only token values are used for color, type size, spacing and corner radius. Four
themes come with the tokens: light, dark, light-ic, dark-ic (increased contrast).

Text contrast: the HIG asks for 4.5:1 for text up to 17pt. Some regular token
values miss that on their own background (e.g. green or blue text on white), so
`text()` falls back to the same token's increased-contrast value, then to `label`.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QColor

THEMES = ("light", "dark", "light-ic", "dark-ic")
# platforms.md: Asset Manager is a Mac app (pointer + keyboard), so sizes come from the
# Desktop profile: `space("control-md")` reads `desktop-control-md` (28), never the
# Mobile/touch values (44 hit targets, capsules).
PLATFORM = "desktop"
# components/MacOSStyle: the macOS Liquid Glass style layer on top of the Desktop profile
# (data-style="tahoe"). One style for the whole app, never mixed with the classic look.
# Concentric radii: window 26 -> sidebar panel 18 (inset 8) -> selected row 10 (inset 8).
STYLE = "tahoe"
# bundle.css [data-style="tahoe"]: which tahoe-* token stands in for a Desktop size / radius
TAHOE_SPACE = {"bar-height": "tahoe-toolbar-height", "sidebar-width": "tahoe-panel-width",
               "inspector-width": "tahoe-inspector-width"}
TAHOE_RADIUS = {"field": "tahoe-radius-field", "row": "tahoe-radius-row", "group": "tahoe-radius-group",
                "card": "tahoe-radius-tile", "window": "tahoe-radius-window", "sheet": "tahoe-radius-window",
                "menu": "tahoe-radius-menu", "panel": "tahoe-radius-panel", "tile": "tahoe-radius-tile",
                "value": "tahoe-radius-value", "icon-tile": "tahoe-radius-icon-tile"}
# buttons, segments, chips and search are capsules: radius = half the control's height
TAHOE_CAPSULE = {"control": "control-md", "chip": "chip-height", "search": "control-md"}
MIN_TEXT_CONTRAST = 4.5
MIN_LARGE_TEXT_CONTRAST = 3.0  # 18pt and up (layout-and-accessibility.md)

_theme = "light"


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads(Path(__file__).with_name("tokens.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _colors() -> dict[str, dict[str, str]]:
    return {t["name"]: t["value"] for t in _data()["color"]["tokens"]}


def _px(group: str, name: str) -> int:
    values = {t["name"]: t["value"] for t in _data()[group]["tokens"]}
    for key in (f"{PLATFORM}-{name}", name):  # the platform's own value first
        if key in values:
            return int(str(values[key]).rstrip("px"))
    raise KeyError(name)


# components/RegionLayout, 띄운 상자 style (hig-regions--boxes): the README fixes these as
# the CSS variables --_box-r and --_box-gap, not as tokens, so they are named here once.
REGION_BOX = {"region-box-radius": 16, "region-box-gap": 10}
# components/SettingsPane: inputs, steppers in a settings row share one width
# (hig-settings__control--field, --hig-settings-control-w: Desktop 140)
COMPONENT_SIZES = {**REGION_BOX, "settings-control": 140}


def space(name: str) -> int:
    """space-1 … space-10 (shared 4px scale) and the Desktop sizes: control-sm/md/lg,
    control-pad, field-pad, icon-sm/md/lg, row, row-pad, bar-height, table-header-height,
    sidebar-width, margin, gap-tight/group/section, card-pad, indent, … (proportions.md)."""
    if name in COMPONENT_SIZES:
        return COMPONENT_SIZES[name]
    if STYLE == "tahoe" and name in TAHOE_SPACE:
        name = TAHOE_SPACE[name]
    return _px("spacing", name if not name.isdigit() else f"space-{name}")


def opacity(name: str) -> float:
    """opacity-pressed, opacity-disabled, opacity-scrim, clear-glass-dim."""
    for t in _data()["opacity"]["tokens"]:
        if t["name"] == name:
            return float(t["value"])
    raise KeyError(name)


def radius(name: str) -> int:
    """control, field, row, list-row, group, card, window, sheet, chip, and in the tahoe
    style also menu, panel, tile, value, icon-tile (tahoe: capsule / 8 / 10 / 12 / 22 / 26)."""
    if name in REGION_BOX:
        return REGION_BOX[name]
    if name.startswith(("radius-", "tahoe-")):
        return _px("radius", name)
    if STYLE == "tahoe":
        if name in TAHOE_CAPSULE:
            return space(TAHOE_CAPSULE[name]) // 2
        if name in TAHOE_RADIUS:
            return _px("radius", TAHOE_RADIUS[name])
    return _px("radius", f"radius-{name}")


def inner_radius(outer: str, inset: str) -> int:
    """Concentric corner: inner radius = outer radius - the padding between them."""
    return max(0, radius(outer) - space(inset))


def type_style(name: str) -> dict:
    """mac-body, mac-headline, …: {'size': px, 'weight': css weight}."""
    for g in _data()["type"]["groups"]:
        for s in g["styles"]:
            if s["name"] == name:
                return {"size": int(s["fontSize"].rstrip("px")), "weight": int(s["fontWeight"])}
    raise KeyError(name)


def font_family() -> str:
    return _data()["type"]["families"]["sans"]


# ---- theme ----------------------------------------------------------------
def set_theme(theme: str) -> None:
    global _theme
    if theme not in THEMES:
        raise ValueError(theme)
    _theme = theme


def theme() -> str:
    return _theme


def is_dark() -> bool:
    return _theme.startswith("dark")


def _ic(theme: str) -> str:
    return theme if theme.endswith("-ic") else f"{theme}-ic"


def raw(name: str, theme: str | None = None) -> str:
    v = _colors()[name][theme or _theme]
    if v.startswith("{"):  # alias, e.g. accent -> {blue}
        return raw(v.strip("{}"), theme)
    return v


def _qcolor(hexv: str) -> QColor:
    h = hexv.lstrip("#")
    c = QColor(f"#{h[:6]}")
    if len(h) == 8:
        c.setAlpha(int(h[6:8], 16))
    return c


def color(name: str, theme: str | None = None) -> QColor:
    return _qcolor(raw(name, theme))


# ---- contrast ---------------------------------------------------------------
def _over(c: QColor, bg: QColor) -> QColor:
    a = c.alphaF()
    return QColor.fromRgbF(c.redF() * a + bg.redF() * (1 - a), c.greenF() * a + bg.greenF() * (1 - a),
                           c.blueF() * a + bg.blueF() * (1 - a))


def _lum(c: QColor) -> float:
    def ch(v: float) -> float:
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.redF()) + 0.7152 * ch(c.greenF()) + 0.0722 * ch(c.blueF())


def contrast(fg: QColor, bg: QColor) -> float:
    bg = _over(bg, color("background")) if bg.alpha() < 255 else bg
    fg = _over(fg, bg)
    a, b = sorted((_lum(fg), _lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def text(name: str, on: str = "background", large: bool = False) -> QColor:
    """The token as a text color on token `on`, meeting 4.5:1 (3:1 for large text; see module doc)."""
    bg = color(on)
    need = MIN_LARGE_TEXT_CONTRAST if large else MIN_TEXT_CONTRAST
    for t in (_theme, _ic(_theme)):
        c = color(name, t)
        if contrast(c, bg) >= need:
            return c
    # keep the hue (a red that means "destructive" stays red): step the increased-contrast
    # value toward `label` until it reads
    ic = color(name, _ic(_theme))
    for i in range(1, 11):
        c = mix(ic, color("label"), i / 10)
        if contrast(c, bg) >= need:
            return c
    return color("label")


def on_color(fill: str) -> QColor:
    """Label color for text sitting on a filled token (e.g. a prominent button)."""
    bg = color(fill)
    for name in ("on-accent", "label", "background"):
        c = color(name)
        if contrast(c, bg) >= MIN_TEXT_CONTRAST:
            return c
    return color("label")


def fill_for_text(name: str) -> str:
    """The token value to use as a button fill so its label still reads (4.5:1)."""
    # white text first (prominent and delete buttons keep their white label), taking the
    # increased-contrast value of the color when only that one reaches 4.5:1
    for lab in ("on-accent", "label", "background"):
        for t in (_theme, _ic(_theme)):
            bg = color(name, t)
            if contrast(color(lab), bg) >= MIN_TEXT_CONTRAST:
                return css(bg)
    return css(color(name))


def accent_fill() -> QColor:
    """The accent as a fill whose white (or label) text still reads 4.5:1."""
    return QColor(fill_for_text("accent"))


def on_accent_fill() -> QColor:
    """Text / symbol color on accent_fill(): the tahoe selection pill and menu highlight."""
    bg = accent_fill()
    for name in ("on-accent", "label", "background"):
        if contrast(color(name), bg) >= MIN_TEXT_CONTRAST:
            return color(name)
    return color("label")


def css(c: QColor, alpha: float | None = None) -> str:
    a = c.alphaF() if alpha is None else alpha
    if a >= 0.999:
        return c.name()
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {round(a, 3)})"


def mix(a: QColor, b: QColor, t: float) -> QColor:
    """CSS color-mix(a (1-t), b t): how bundle.css derives hover and selection tints."""
    return QColor.fromRgbF(a.redF() * (1 - t) + b.redF() * t, a.greenF() * (1 - t) + b.greenF() * t,
                           a.blueF() * (1 - t) + b.blueF() * t, a.alphaF() * (1 - t) + b.alphaF() * t)


# states-and-interaction.md: hover = accent 6%, selection = accent 16% (strong 26%),
# always different so a hovered row never looks selected.
HOVER_MIX, SELECT_MIX, SELECT_STRONG_MIX, BUTTON_HOVER_MIX = 0.06, 0.16, 0.26, 0.14


def hover() -> QColor:
    return tint("accent", HOVER_MIX)


def selection(strong: bool = False) -> QColor:
    return tint("accent", SELECT_STRONG_MIX if strong else SELECT_MIX)


def tint(name: str, alpha: float) -> QColor:
    """A see-through version of a token, for row/background tints."""
    c = color(name)
    c.setAlphaF(alpha)
    return c


STATE_TOKENS = {"ok": "green", "soon": "yellow", "low": "orange", "out": "red", "unknown": "gray"}
# StatusIndicator: background tints come from the status-*-tint tokens (never a made-up rgba)
STATE_TINTS = {"ok": "status-success-tint", "soon": "status-warning-tint", "low": "status-warning-tint",
               "out": "status-error-tint", "unknown": "status-neutral-tint"}


CHART_SERIES = tuple(f"chart-{i}" for i in range(1, 7))


def chart_color(index: int) -> QColor:
    """Series color by position, in the fixed order chart-1 … chart-6 (components/Chart).

    Never cycled: a 7th series is folded into "기타" or split into another chart.
    State charts (충분/부족/소진 …) keep the state colors, since those carry meaning.
    """
    if not 0 <= index < len(CHART_SERIES):
        raise IndexError("charts take at most six colored series")
    return color(CHART_SERIES[index])


def tabular(font):
    """Same font with tabular (fixed-width) digits, for tables, stats and amounts."""
    from PySide6.QtGui import QFont
    f = QFont(font)
    try:
        f.setFeature(QFont.Tag("tnum"), 1)  # Qt 6.7+
    except AttributeError:
        pass
    return f


def sp(step: int) -> int:
    """Spacing token by step: sp(3) == space-3 (12px)."""
    return space(f"space-{step}")
