"""Design tokens: every theme renders, and text pairs meet the HIG 4.5:1 contrast."""
import re
from pathlib import Path

import pytest

pytest.importorskip("PySide6")
from app.ui import theme  # noqa: E402
from app.ui.theme import tokens  # noqa: E402

QSS = Path(theme.__file__).with_name("base.qss")


@pytest.mark.parametrize("name", tokens.THEMES)
def test_theme_contrast(name):
    tokens.set_theme(name)
    for bg in ("background", "background-secondary", "grouped-background-secondary",
               "region-base", "region-canvas", "region-box"):
        assert tokens.contrast(tokens.color("label"), tokens.color(bg)) >= 4.5
        for t in ("gray", "green", "yellow", "orange", "red", "accent", "link"):
            assert tokens.contrast(tokens.text(t, bg), tokens.color(bg)) >= 4.5, (name, t, bg)
    fill = tokens.color("accent").__class__(tokens.fill_for_text("accent"))
    on = tokens.color("label").__class__(theme._value("on:accent"))
    assert tokens.contrast(on, fill) >= 4.5  # prominent button label
    css = theme.render_qss()
    assert "{{" not in css
    tokens.set_theme("light")


def test_stylesheet_has_no_raw_values():
    src = re.sub(r"/\*.*?\*/", "", QSS.read_text(encoding="utf-8"), flags=re.S)
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", src)
    # only 1px hairlines and the 2px Desktop focus ring (bundle.css --hig-focus-ring) are written
    assert set(re.findall(r"\b(\d+)px", src)) <= {"1", "2"}


def test_desktop_profile():
    """platforms.md: a Mac app uses the Desktop sizes, not the touch ones."""
    assert tokens.PLATFORM == "desktop"
    assert tokens.space("control-md") == 28 and tokens.space("row") == 28
    assert tokens.space("icon-md") == 16 and tokens.space("bar-height") == 52
    assert tokens.type_style("mac-body")["size"] == 13
    assert "44px" not in theme.render_qss()


def test_tahoe_style():
    """components/MacOSStyle: capsule controls, concentric radii from tahoe-* tokens only."""
    assert tokens.STYLE == "tahoe"
    assert tokens.radius("control") == tokens.space("control-md") // 2  # capsule
    assert tokens.radius("field") == 8 and tokens.radius("group") == 12 and tokens.radius("menu") == 12
    # window 26 -> panel 18 (inset 8) -> selected row 10 (inset 8)
    assert tokens.inner_radius("window", "tahoe-inset") == tokens.radius("panel") == 18
    assert tokens.inner_radius("panel", "tahoe-inset") == tokens.radius("row") == 10
    assert tokens.space("tahoe-toolbar-item") == 34 and tokens.space("tahoe-group-row") == 32
    assert tokens.space("inspector-width") == 268
    qss = theme.render_qss()
    # components/RegionLayout 띄운 상자: window base = sidebar face, canvas and inspector boxes
    # (radius 16, 1px region-box-edge), the inspector box the brightest
    canvas = re.search(r"#mainArea \{([^}]*)\}", qss).group(1)
    box = re.search(r"#inspectorBox \{([^}]*)\}", qss).group(1)
    edge = tokens.css(tokens.color("region-box-edge"))
    assert tokens.css(tokens.color("region-canvas")) in canvas and edge in canvas and "16px" in canvas
    assert tokens.css(tokens.color("region-box")) in box and edge in box
    assert tokens.css(tokens.color("region-base")) in re.search(r"#windowBack \{([^}]*)\}", qss).group(1)
    assert "transparent" in re.search(r"#sidebar \{([^}]*)\}", qss).group(1)
    # Delete (hig-delete): a red fill, never plain red text outside alerts
    delete = re.search(r"\n#destructive \{([^}]*)\}", qss).group(1)
    assert tokens.fill_for_text("red") in delete
    # SettingsPane card: filled, no border, radius-group
    group = re.search(r"#insetGroup \{([^}]*)\}", qss).group(1)
    assert "border: none" in group and f"border-radius: {tokens.radius('radius-group')}px" in group
    assert re.search(r"QAbstractItemView::indicator:checked \{[^}]*background: #", qss)  # accent checkbox


@pytest.mark.parametrize("name", tokens.THEMES)
def test_chart_series_tokens(name):
    """components/Chart: six series colors, fixed order, defined for every theme."""
    tokens.set_theme(name)
    assert [tokens.chart_color(i).isValid() for i in range(6)] == [True] * 6
    with pytest.raises(IndexError):
        tokens.chart_color(6)  # a 7th series is folded into 기타, never a new color
    tokens.set_theme("light")


def test_icons_come_from_the_design_system():
    """Every symbol the UI asks for exists in icons.json (or the custom set) and renders."""
    from PySide6.QtWidgets import QApplication
    from app.ui import sidebar
    from app.ui.theme import icons

    QApplication.instance() or QApplication([])
    src = "\n".join(p.read_text(encoding="utf-8") for p in Path(sidebar.__file__).parent.glob("*.py"))
    used = set(re.findall(r'set_icon\([^\n]*?"([a-z0-9.]+)"', src))
    used |= set(re.findall(r'icons\.(?:icon|pixmap)\(\s*"([a-z0-9.]+)"', src))
    used |= set(sidebar.TAB_ICONS.values()) | {sidebar.FILE_ICON, "chevron.down", "chevron.right"}
    assert used <= set(icons.names()), used - set(icons.names())
    assert "magnifyingglass" in used and "minus" in used  # destructive buttons are plain red text, no symbol
    for name in used:
        img = icons.pixmap(name, tokens.color("label")).toImage()
        assert any(img.pixelColor(x, y).alpha() for x in range(img.width()) for y in range(img.height()))


def test_number_columns_end_aligned():
    from PySide6.QtCore import Qt
    from app.ui.table_model import NUM_ALIGN, TEXT_ALIGN
    assert NUM_ALIGN & Qt.AlignTrailing and TEXT_ALIGN & Qt.AlignLeading
