"""Inset groups for inspectors and settings (components/MacOSStyle, tahoe style).

    재고                      <- heading: 13/600 label, on the card's text line
    ╭──────────────────────╮
    │ 재고 상태      ● 충분 │  <- rows 40 tall (components/SettingsPane): label at the
    │ ─────────────────── │     start, value at the end; separators run from the text
    │ 수량        [ 3 ] − + │     line to the inner padding; the card is filled 5% toward
    ╰──────────────────────╯     label, no border or shadow, radius-group 11
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .theme import tokens


def inset_title(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("insetTitle")
    lbl.setContentsMargins(tokens.space("row-pad"), 0, 0, 0)  # on the card's text line
    return lbl


def value_label(text: str = "-") -> QLabel:
    """hig-value: a filled 22-tall field for a number (+ unit), digits end-aligned and tabular."""
    lbl = QLabel(text)
    lbl.setObjectName("value")
    lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    lbl.setFont(tokens.tabular(lbl.font()))
    lbl.setMinimumWidth(2 * tokens.space("control-md"))  # bundle.css .hig-value min-width 56
    return lbl


class InsetGroup(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("insetGroup")
        self._lay = QVBoxLayout(self)
        pad = tokens.space("row-pad")  # the inner padding: separators stop here, labels start here
        self._lay.setContentsMargins(pad, 0, pad, 0)
        self._lay.setSpacing(0)
        self.rows: list[QFrame] = []

    def add_row(self, label: str | QLabel, *widgets: QWidget, expand: bool = False,
                fit_label: bool = False, stretch: tuple[int, ...] = ()) -> QFrame:
        """expand: the controls take the row's width and the label keeps a short fixed column
        (inspector fields); otherwise the label stretches and the value sits at the end."""
        row = QFrame()
        row.setObjectName("insetRow")
        row.setProperty("sep", bool(self.rows))
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(tokens.space("gap-tight"))
        lab = label if isinstance(label, QLabel) else QLabel(label)
        if expand:
            # one label column per group; a group of short labels (시작 / 종료) keeps them narrow
            lab.setFixedWidth(lab.sizeHint().width() if fit_label else 2 * tokens.space("control-md"))
            h.addWidget(lab)
            for i, w in enumerate(widgets):  # share the row whatever the control's own size hint
                w.setSizePolicy(QSizePolicy.Ignored, w.sizePolicy().verticalPolicy())
                h.addWidget(w, stretch[i] if i < len(stretch) else 1)
        else:
            h.addWidget(lab, 1)
            from PySide6.QtWidgets import QComboBox
            from .widgets import fit_select
            from PySide6.QtWidgets import QAbstractSpinBox, QLineEdit
            for w in widgets:
                if isinstance(w, QComboBox) and not w.isEditable():
                    fit_select(w)  # the value sits against its select-cap at the row's end
                elif isinstance(w, (QAbstractSpinBox, QLineEdit)) and w.maximumWidth() > 10_000:
                    # SettingsPane: fields and steppers share one width (140), text inside at the start
                    w.setFixedWidth(tokens.space("settings-control"))
                h.addWidget(w, 0, Qt.AlignRight | Qt.AlignVCenter)
        row.label = lab
        self._lay.addWidget(row)
        self.rows.append(row)
        return row

    def restripe(self) -> None:
        """Hairlines only between visible rows (after one is hidden or shown)."""
        first = True
        for row in self.rows:
            if row.isHidden():
                continue
            row.setProperty("sep", not first)
            row.style().unpolish(row)
            row.style().polish(row)
            first = False
