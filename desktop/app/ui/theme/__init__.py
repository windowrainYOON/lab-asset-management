"""App look: Apple HIG tokens (tokens.json) rendered into a Qt stylesheet and palette.

`base.qss` holds placeholders instead of values:

    {{c:separator}}          token color             {{t:green}}   text color on `background` (4.5:1)
    {{t:gray@grouped-background}}  text color on another token
    {{fill:accent}}          button fill whose label reads    {{on:accent}}  label on that fill
    {{a:accent:opacity-pressed}}  token at an opacity token     {{s:space-3}} / {{s:control-md}}
    {{half:progress-height}} half of a size token (round ends)       {{T:green}} large text (3:1)
    {{r:control}}            corner radius (Desktop profile)  {{f:mac-body}} / {{w:mac-body}}  type size / weight
    {{rin:menu:gap-tight}}   concentric inner radius (outer radius - padding)
    {{sub:tahoe-toolbar-item:space-2}}   one size minus another (or minus a number of px)
    {{halfsub:a:b}}          half of that (capsule radius of an inner control)
    {{hover}} / {{select}} / {{select-strong}}   label 6% / accent 16% / accent 26% (bundle.css)
    {{mix:fill:label:0.14}}  color-mix of two tokens (button hover: 14% toward label)
    {{i:chevron.down:gray@background:mac-body}}  symbol image (icons.json) in a token color,
                             sized like that text style (small scale = 1em)

Theme: follows macOS (dark mode and "Increase contrast") unless the user picks one
in 보기 → 화면 모양. Widgets that paint their own colors read `tokens` and listen to
`notifier.changed`.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

from ...core import settings
from . import tokens

APPEARANCES = {"system": "시스템 설정 따르기", "light": "라이트", "dark": "다크"}
CONTRASTS = {"system": "시스템 설정 따르기", "normal": "기본 대비", "high": "고대비"}


class _Notifier(QObject):
    changed = Signal()


notifier = _Notifier()
_hooked = False


def appearance() -> tuple[str, str]:
    data = settings.load_settings()
    return data.get("appearance", "system"), data.get("contrast", "system")


def set_appearance(mode: str | None = None, contrast: str | None = None) -> None:
    data = settings.load_settings()
    if mode is not None:
        data["appearance"] = mode
    if contrast is not None:
        data["contrast"] = contrast
    settings.save_settings(data)
    app = QApplication.instance()
    if app is not None:
        apply_theme(app)


def _system_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:  # Qt < 6.5
        return False


def _system_high_contrast() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        out = subprocess.run(["defaults", "read", "com.apple.universalaccess", "increaseContrast"],
                             capture_output=True, text=True, timeout=2).stdout.strip()
        return out == "1"
    except (OSError, subprocess.SubprocessError):
        return False


def resolve_theme() -> str:
    mode, contrast = appearance()
    dark = _system_dark() if mode == "system" else mode == "dark"
    high = _system_high_contrast() if contrast == "system" else contrast == "high"
    return ("dark" if dark else "light") + ("-ic" if high else "")


_PH = re.compile(r"\{\{([^}]+)\}\}")


def _value(spec: str) -> str:
    kind, _, rest = spec.partition(":")
    if kind == "hover":
        return tokens.css(tokens.hover())
    if kind == "select":
        return tokens.css(tokens.selection())
    if kind == "select-strong":
        return tokens.css(tokens.selection(strong=True))
    if kind == "mix":
        a, b, t = rest.split(":")
        base = QColor(tokens.fill_for_text("accent")) if a == "accent-fill" else tokens.color(a)
        return tokens.css(tokens.mix(base, tokens.color(b), float(t)))
    if kind == "c":
        return tokens.css(tokens.color(rest))
    if kind in ("t", "T"):  # T: large text (3:1)
        name, _, on = rest.partition("@")
        return tokens.css(tokens.text(name, on or "background", large=kind == "T"))
    if kind == "half":  # round ends for a bar of this thickness token
        return f"{tokens.space(rest) // 2}px"
    if kind == "fill":
        return tokens.fill_for_text(rest)
    if kind == "on":
        fill = QColor(tokens.fill_for_text(rest))
        for lab in ("on-accent", "label", "background"):
            if tokens.contrast(tokens.color(lab), fill) >= tokens.MIN_TEXT_CONTRAST:
                return tokens.css(tokens.color(lab))
        return tokens.css(tokens.color("label"))
    if kind == "afill":  # button fill (see fill) at an opacity token: pressed / disabled prominent buttons
        name, _, alpha = rest.partition(":")
        return tokens.css(QColor(tokens.fill_for_text(name)), tokens.opacity(alpha))
    if kind == "a":  # token at an opacity token (or number)
        name, _, alpha = rest.partition(":")
        a = tokens.opacity(alpha) if alpha.startswith(("opacity", "clear")) else float(alpha)
        return tokens.css(tokens.color(name), tokens.color(name).alphaF() * a)
    if kind == "s":
        return f"{tokens.space(rest)}px"
    if kind == "r":
        return f"{tokens.radius(rest)}px"
    if kind == "rin":
        outer, _, inset = rest.partition(":")
        return f"{tokens.inner_radius(outer, inset)}px"
    if kind in ("sub", "halfsub"):
        a, _, b = rest.partition(":")
        v = tokens.space(a) - (int(b) if b.isdigit() else tokens.space(b))
        return f"{v // 2 if kind == 'halfsub' else v}px"
    if kind == "f":
        return f"{tokens.type_style(rest)['size']}px"
    if kind == "w":
        return str(tokens.type_style(rest)["weight"])
    if kind == "i":  # symbol: a non-text graphic, so 3:1 against its background
        from . import icons
        name, colour, *size = rest.split(":")
        cname, _, on = colour.partition("@")
        px = tokens.type_style(size[0])["size"] if size else None
        return icons.qss_url(name, tokens.text(cname, on or "background", large=True), 400, px)
    raise KeyError(spec)


def render_qss(name: str = "base") -> str:
    src = Path(__file__).with_name(f"{name}.qss").read_text(encoding="utf-8")
    return _PH.sub(lambda m: _value(m.group(1).strip()), src)


def _palette() -> QPalette:
    pal = QPalette()
    t = tokens
    roles = {
        QPalette.Window: t.color("background-secondary"), QPalette.WindowText: t.color("label"),
        QPalette.Base: t.color("background"), QPalette.AlternateBase: t.color("background-secondary"),
        QPalette.Text: t.color("label"), QPalette.PlaceholderText: t.text("gray"),
        QPalette.Button: t.color("background-tertiary"), QPalette.ButtonText: t.color("label"),
        QPalette.ToolTipBase: t.color("background-tertiary"), QPalette.ToolTipText: t.color("label"),
        QPalette.Highlight: t.color("accent"), QPalette.HighlightedText: t.on_color("accent"),
        QPalette.BrightText: t.color("on-accent"), QPalette.Link: t.text("link"),
        QPalette.Mid: t.color("separator-opaque"), QPalette.Dark: t.color("gray-2"),
        QPalette.Light: t.color("background"), QPalette.Midlight: t.color("gray-5"),
    }
    for role, c in roles.items():
        pal.setColor(role, c)
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, t.color("label-tertiary"))
    return pal


def apply_theme(app: QApplication, name: str = "base", theme: str | None = None) -> None:
    global _hooked
    tokens.set_theme(theme or resolve_theme())
    f = QFont(app.font())
    f.setPixelSize(tokens.type_style("mac-body")["size"])
    app.setFont(f)
    app.setPalette(_palette())
    app.setStyleSheet(render_qss(name))
    from ..table_model import refresh_state_colors
    refresh_state_colors()
    from . import icons
    icons.refresh()
    if not _hooked and theme is None:
        try:  # follow macOS when it switches between light and dark
            QGuiApplication.styleHints().colorSchemeChanged.connect(lambda *_: apply_theme(app, name))
        except AttributeError:
            pass
        _hooked = True
    notifier.changed.emit()
