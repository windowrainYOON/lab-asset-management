"""Sequence Map: a plasmid's whole sequence with its features, the lab's
restriction sites and the oligos that bind it.

Layout of one 60 bp row, top to bottom (the draft the user approved):

    NdeI   NheI                     <- enzymes that cut inside this row
    CCTTACAACCC…   qPCR_F ▸         <- oligos that run 5'→3', above the duplex
    AGCAGCGGCCTGGTGCCGCGCGGCAGCCA…  <- top strand, with the cut marks on it
    TCGTCGCCGGACCACGGCGCGCCGTCGGT…  <- the complement, always shown
    ◂ GA-XhoI_rev   TGAGCTCGT       <- oligos on the bottom strand, below it
    [ thrombin site ][ T7 tag ]     <- features

Clicking an enzyme name or an oligo row selects it, and the inspector shows that
kind of thing: a cut site and the enzyme's stock for an enzyme, the binding
position and the Oligos sheet for an oligo.
"""
from __future__ import annotations

import zlib
from dataclasses import dataclass

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QHBoxLayout, QLabel,
    QPushButton, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from ..core import settings
from ..core.sequence import SequenceLibrary
from ..core.sequence.enzymes import EnzymeHit
from ..core.sequence.library import Plasmid
from ..core.sequence.oligo_match import OligoHit
from .inset import InsetGroup, inset_title, value_label
from .theme import tokens
from .widgets import InspectorPanel, fit_select

TAB_KEYS = {"seq:common": "Common Plasmid", "seq:personal": "Personal Plasmid"}
DEFAULT_PER_ROW = 60
FEATURE_TOKENS = ("chart-1", "chart-2", "chart-3", "chart-4", "chart-5", "chart-6")


def _mono(size: int = 12) -> QFont:
    font = QFont("SF Mono")
    font.setStyleHint(QFont.Monospace)
    font.setFixedPitch(True)
    font.setPixelSize(size)
    return font


def _ui(size: int = 11, weight: int = QFont.Normal) -> QFont:
    font = QFont(tokens.font_family())
    font.setPixelSize(size)
    font.setWeight(weight)
    return font


def feature_token(name: str) -> str:
    """A stable colour per feature name. Python's own hash() is salted per run,
    so a feature would change colour every launch; this does not."""
    return FEATURE_TOKENS[zlib.crc32(name.encode("utf-8")) % len(FEATURE_TOKENS)]


def feature_color(name: str) -> QColor:
    return tokens.color(feature_token(name))


def _lanes(items: list[tuple[float, float, object]], gap: float = 4.0) -> list[list[tuple]]:
    """Stack overlapping spans into as few rows as they need."""
    lanes: list[list[tuple]] = []
    for span in sorted(items, key=lambda t: t[0]):
        for lane in lanes:
            if span[0] >= lane[-1][1] + gap:
                lane.append(span)
                break
        else:
            lanes.append([span])
    return lanes


@dataclass
class Pick:
    kind: str      # "enzyme" | "oligo" | "feature"
    name: str


class SequenceCanvas(QWidget):
    """The sequence itself, painted row by row."""

    picked = Signal(object)          # Pick or None

    GUTTER = 52
    ROW_GAP = 22
    LANE = 14
    OLIGO_LANE = 16

    def __init__(self, parent=None):
        super().__init__(parent)
        self.library: SequenceLibrary | None = None
        self.plasmid: Plasmid | None = None
        self.per_row = DEFAULT_PER_ROW
        self.show_features = True
        self.show_enzymes = True
        self.show_oligos = True
        self.selection: Pick | None = None
        self._hits: list[tuple[QRect, Pick]] = []
        self._row_tops: list[tuple[int, int]] = []   # (start base, y)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

    # ---- data ----
    def set_plasmid(self, library: SequenceLibrary, plasmid: Plasmid | None) -> None:
        self.library = library
        self.plasmid = plasmid
        self.selection = None
        self._relayout()

    def set_flags(self, features=None, enzymes=None, oligos=None) -> None:
        if features is not None:
            self.show_features = features
        if enzymes is not None:
            self.show_enzymes = enzymes
        if oligos is not None:
            self.show_oligos = oligos
        self._relayout()

    def select(self, pick: Pick | None) -> None:
        self.selection = pick
        self.update()

    # ---- geometry ----
    def base_width(self) -> float:
        return QFontMetrics(_mono()).horizontalAdvance("G") + 2.2

    def _fit_per_row(self) -> int:
        usable = self.width() - self.GUTTER - 140      # room for oligo names
        n = int(usable // self.base_width())
        return max(20, min(DEFAULT_PER_ROW, n - n % 10)) if n > 0 else DEFAULT_PER_ROW

    def _rows(self) -> list[int]:
        if self.plasmid is None:
            return []
        return list(range(1, self.plasmid.length + 1, self.per_row))

    def _row_items(self, start: int, end: int):
        """What belongs on the row covering bases start..end."""
        p = self.plasmid
        enzymes: list[tuple[str, EnzymeHit]] = []
        if self.show_enzymes:
            for name, hits in p.cuts.items():
                if not hits or len(hits) > 4:
                    continue
                enzymes += [(name, h) for h in hits if start <= h.pos <= end]
        oligos = {"+": [], "-": []}
        if self.show_oligos and self.library is not None:
            for hit in self.library.plasmid_hits(p.key):
                seq = self.library.oligos[hit.oligo].seq if hit.oligo in self.library.oligos else ""
                a = hit.anchor
                b = a + len(seq) - 1
                if b >= start and a <= end:
                    oligos[hit.strand].append((hit, seq))
        feats = [f for f in p.record.features if f.end >= start and f.start <= end] \
            if self.show_features else []
        return enzymes, oligos, feats

    def _relayout(self) -> None:
        self.per_row = self._fit_per_row()
        self.updateGeometry()
        self.update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout()

    def sizeHint(self) -> QSize:  # noqa: N802
        if self.plasmid is None:
            return QSize(600, 200)
        height = 0
        for start in self._rows():
            height += self._row_height(start)
        return QSize(600, height + self.ROW_GAP)

    def _row_height(self, start: int) -> int:
        end = min(start + self.per_row - 1, self.plasmid.length)
        enzymes, oligos, feats = self._row_items(start, end)
        w = self.base_width()
        h = 0
        if enzymes:
            spans = [(self._x(e[1].pos, start) - 28, self._x(e[1].pos, start) + 28, e) for e in enzymes]
            h += len(_lanes(spans, 6)) * self.LANE + 4
        if oligos["+"]:
            h += len(self._oligo_lanes(oligos["+"], start, end, w)) * self.OLIGO_LANE + 4
        h += 2 * (QFontMetrics(_mono()).height() + 2)
        if oligos["-"]:
            h += len(self._oligo_lanes(oligos["-"], start, end, w)) * self.OLIGO_LANE + 4
        if feats:
            spans = [(self._x(max(f.start, start), start), self._x(min(f.end, end) + 1, start), f)
                     for f in feats]
            h += len(_lanes(spans, 2)) * self.LANE + 6
        return h + self.ROW_GAP

    def _oligo_lanes(self, items, start: int, end: int, w: float):
        spans = []
        for hit, seq in items:
            a, b = hit.anchor, hit.anchor + len(seq) - 1
            x0, x1 = self._x(max(a, start), start), self._x(min(b, end) + 1, start)
            label = QFontMetrics(_ui(10)).horizontalAdvance(hit.oligo) + 18
            if hit.strand == "+":
                spans.append((x0, x1 + label, (hit, seq)))
            else:
                spans.append((max(x0 - label, 0), x1, (hit, seq)))
        return _lanes(spans, 6)

    def _x(self, base: int, row_start: int) -> float:
        return self.GUTTER + (base - row_start) * self.base_width()

    # ---- painting ----
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        self._hits.clear()
        self._row_tops.clear()
        if self.plasmid is None:
            painter.setPen(tokens.text("label-secondary"))
            painter.setFont(_ui(13))
            painter.drawText(self.rect(), Qt.AlignCenter, "플라스미드를 선택하세요")
            return
        y = 0
        for start in self._rows():
            self._row_tops.append((start, y))
            y += self._paint_row(painter, start, y)

    def _paint_row(self, painter: QPainter, start: int, top: int) -> int:
        p = self.plasmid
        end = min(start + self.per_row - 1, p.length)
        seq = p.record.seq[start - 1:end]
        enzymes, oligos, feats = self._row_items(start, end)
        w = self.base_width()
        mono = _mono()
        fm = QFontMetrics(mono)
        line_h = fm.height() + 2
        y = top

        # enzymes
        if enzymes:
            spans = [(self._x(h.pos, start) - 28, self._x(h.pos, start) + 28, (n, h)) for n, h in enzymes]
            lanes = _lanes(spans, 6)
            lane_top = y
            for i, lane in enumerate(lanes):
                for _, _, (name, hit) in lane:
                    self._paint_enzyme(painter, name, hit, start, lane_top + i * self.LANE,
                                       lane_top + len(lanes) * self.LANE)
            y += len(lanes) * self.LANE + 4

        # forward oligos
        if oligos["+"]:
            lanes = self._oligo_lanes(oligos["+"], start, end, w)
            for i, lane in enumerate(lanes):
                for _, _, (hit, oseq) in lane:
                    self._paint_oligo(painter, hit, oseq, start, end, y + i * self.OLIGO_LANE)
            y += len(lanes) * self.OLIGO_LANE + 4

        # the duplex
        painter.setFont(mono)
        painter.setPen(tokens.text("label"))
        for i, base in enumerate(seq):
            painter.drawText(QRect(int(self.GUTTER + i * w), y, int(w), line_h),
                             Qt.AlignCenter, base)
        painter.setPen(tokens.text("label-tertiary"))
        comp = seq.translate(str.maketrans("ACGTN", "TGCAN"))
        for i, base in enumerate(comp):
            painter.drawText(QRect(int(self.GUTTER + i * w), y + line_h, int(w), line_h),
                             Qt.AlignCenter, base)
        painter.setFont(_ui(10))
        painter.setPen(tokens.text("label-tertiary"))
        painter.drawText(QRect(0, y, self.GUTTER - 8, line_h), Qt.AlignRight | Qt.AlignVCenter,
                         f"{start:,}")
        if self.show_enzymes:
            for name, hit in enzymes:
                pen = QPen(tokens.color("red"), 1.6)
                painter.setPen(pen)
                x = self._x(hit.pos + 1, start)
                painter.drawLine(int(x), y - 1, int(x), y + 2 * line_h + 1)
        y += 2 * line_h + 2

        # reverse oligos
        if oligos["-"]:
            lanes = self._oligo_lanes(oligos["-"], start, end, w)
            for i, lane in enumerate(lanes):
                for _, _, (hit, oseq) in lane:
                    self._paint_oligo(painter, hit, oseq, start, end, y + i * self.OLIGO_LANE)
            y += len(lanes) * self.OLIGO_LANE + 4

        # features
        if feats:
            spans = [(self._x(max(f.start, start), start), self._x(min(f.end, end) + 1, start), f)
                     for f in feats]
            lanes = _lanes(spans, 2)
            for i, lane in enumerate(lanes):
                for x0, x1, feat in lane:
                    self._paint_feature(painter, feat, x0, x1, y + i * self.LANE)
            y += len(lanes) * self.LANE + 6
        return y - top + self.ROW_GAP

    def _paint_enzyme(self, painter: QPainter, name: str, hit: EnzymeHit, start: int,
                      y: int, stem_bottom: int) -> None:
        x = self._x(hit.pos + 1, start)
        unique = len(self.plasmid.cuts.get(name, ())) == 1
        colour = QColor(tokens.color("red"))
        if not unique:
            colour.setAlphaF(0.55)
        painter.setPen(QPen(colour, 1.0))
        painter.drawLine(int(x), y + 11, int(x), stem_bottom)
        label = name if unique else f"{name} ({len(self.plasmid.cuts[name])})"
        painter.setFont(_ui(10, QFont.DemiBold if unique else QFont.Normal))
        fm = QFontMetrics(painter.font())
        rect = QRect(int(x - fm.horizontalAdvance(label) / 2), y, fm.horizontalAdvance(label) + 2, 12)
        selected = self.selection and self.selection.kind == "enzyme" and self.selection.name == name
        if selected:
            path = QPainterPath()
            path.addRoundedRect(rect.adjusted(-4, -1, 4, 1), 4, 4)
            painter.fillPath(path, tokens.color("red"))
            painter.setPen(tokens.on_color("red"))
        else:
            painter.setPen(colour)
        painter.drawText(rect, Qt.AlignCenter, label)
        self._hits.append((rect.adjusted(-4, -2, 4, 2), Pick("enzyme", name)))

    def _paint_oligo(self, painter: QPainter, hit: OligoHit, oseq: str, start: int,
                     end: int, y: int) -> None:
        w = self.base_width()
        drawn = hit.drawn(oseq)
        anchor = hit.anchor
        painter.setFont(_mono(11))
        colour = tokens.color("blue") if hit.anneals else tokens.color("orange")
        off = tokens.text("label-tertiary")
        first_x = last_x = None
        for i, base in enumerate(drawn):
            pos = anchor + i
            if pos < start or pos > end:
                continue
            x = self._x(pos, start)
            first_x = x if first_x is None else first_x
            last_x = x + w
            inside = hit.q_from <= i + 1 <= hit.q_to
            painter.setPen(colour if inside else off)
            painter.drawText(QRect(int(x), y, int(w), self.OLIGO_LANE), Qt.AlignCenter, base)
        if first_x is None:
            return
        painter.setFont(_ui(10))
        fm = QFontMetrics(painter.font())
        arrow = "▸" if hit.strand == "+" else "◂"
        text = f"{hit.oligo} {arrow}" if hit.strand == "+" else f"{arrow} {hit.oligo}"
        tw = fm.horizontalAdvance(text) + 6
        right = last_x + 6
        if hit.strand == "+" and right + tw > self.width() and first_x - 6 - tw > 0:
            right = first_x - 6 - tw
        elif hit.strand == "-":
            right = first_x - 6 - tw if first_x - 6 - tw > 0 else last_x + 6
        rect = QRect(int(right), y, int(tw), self.OLIGO_LANE)
        selected = self.selection and self.selection.kind == "oligo" and self.selection.name == hit.oligo
        painter.setPen(tokens.text("label") if selected else tokens.text("label-secondary"))
        if selected:
            painter.setFont(_ui(10, QFont.Bold))
        painter.drawText(rect, Qt.AlignVCenter | Qt.AlignLeft, text)
        row = QRect(int(min(first_x, rect.left())), y,
                    int(max(last_x, rect.right()) - min(first_x, rect.left())), self.OLIGO_LANE)
        self._hits.append((row, Pick("oligo", hit.oligo)))

    def _paint_feature(self, painter: QPainter, feat, x0: float, x1: float, y: int) -> None:
        colour = feature_color(feat.name)
        path = QPainterPath()
        path.addRoundedRect(x0, y, max(x1 - x0, 2), 11, 3, 3)
        painter.fillPath(path, colour)
        painter.setFont(_ui(9))
        fm = QFontMetrics(painter.font())
        if x1 - x0 > fm.horizontalAdvance(feat.name) + 8:
            painter.setPen(tokens.on_color(feature_token(feat.name)))
            painter.drawText(QRect(int(x0), y, int(x1 - x0), 11), Qt.AlignCenter, feat.name)
        self._hits.append((QRect(int(x0), y, int(max(x1 - x0, 2)), 11), Pick("feature", feat.name)))

    # ---- picking ----
    def mousePressEvent(self, event) -> None:  # noqa: N802
        pos = event.position().toPoint()
        for rect, pick in reversed(self._hits):
            if rect.contains(pos):
                self.select(pick)
                self.picked.emit(pick)
                return
        self.select(None)
        self.picked.emit(None)

    def row_at(self, base: int) -> int:
        """Pixel y of the row holding this base."""
        for start, y in self._row_tops:
            if start <= base < start + self.per_row:
                return y
        return 0


class OverviewStrip(QWidget):
    """The whole plasmid in one line: features, the enzymes that cut once, and a
    box around the part of the sequence that is on screen."""

    moved = Signal(int)   # first base the user wants to see

    def __init__(self, parent=None):
        super().__init__(parent)
        self.plasmid: Plasmid | None = None
        self.window = (1, 1)
        self.setFixedHeight(74)
        self.setCursor(Qt.PointingHandCursor)

    def set_plasmid(self, plasmid: Plasmid | None) -> None:
        self.plasmid = plasmid
        self.update()

    def set_window(self, start: int, end: int) -> None:
        self.window = (start, end)
        self.update()

    def _scale(self) -> float:
        return (self.width() - 24) / max(self.plasmid.length, 1) if self.plasmid else 1.0

    def paintEvent(self, event) -> None:  # noqa: N802
        if self.plasmid is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        sc = self._scale()
        axis_y = 46
        painter.setPen(QPen(tokens.color("separator"), 1))
        painter.drawLine(12, axis_y, self.width() - 12, axis_y)
        for feat in self.plasmid.record.features:
            path = QPainterPath()
            path.addRoundedRect(12 + feat.start * sc, 32,
                                max((feat.end - feat.start) * sc, 2), 9, 3, 3)
            painter.fillPath(path, feature_color(feat.name))
        painter.setFont(_ui(9))
        fm = QFontMetrics(painter.font())
        last = -40.0
        for name in self.plasmid.unique_cutters():
            hit = self.plasmid.cuts[name][0]
            x = 12 + hit.pos * sc
            painter.setPen(QPen(tokens.color("label-secondary"), 1))
            painter.drawLine(int(x), axis_y + 1, int(x), axis_y + 9)
            if x - last > fm.horizontalAdvance(name) + 10:
                painter.setPen(tokens.text("label-secondary"))
                painter.drawText(QRect(int(x - 40), axis_y + 10, 80, 12), Qt.AlignCenter, name)
                last = x
        painter.setPen(tokens.text("label-tertiary"))
        step = 1000 if self.plasmid.length > 2500 else 500
        for tick in range(step, self.plasmid.length, step):
            painter.drawText(QRect(int(12 + tick * sc - 20), 8, 40, 12), Qt.AlignCenter,
                             f"{tick // 1000}k" if step >= 1000 else str(tick))
        a, b = self.window
        path = QPainterPath()
        path.addRoundedRect(12 + a * sc, 26, max((b - a) * sc, 6), 22, 4, 4)
        painter.fillPath(path, tokens.tint("blue", 0.12))
        painter.setPen(QPen(tokens.color("blue"), 1.5))
        painter.drawPath(path)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.plasmid is None:
            return
        base = int((event.position().x() - 12) / max(self._scale(), 1e-6))
        self.moved.emit(max(1, min(self.plasmid.length, base)))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.LeftButton:
            self.mousePressEvent(event)


class SequenceInspector(InspectorPanel):
    """What the inspector shows depends on what is selected on the canvas:
    a restriction enzyme, an oligo, or the plasmid itself."""

    open_stock = Signal(str, str)     # category name, item name
    fill_requested = Signal()

    def __init__(self, parent=None):
        super().__init__()
        self.setMinimumWidth(tokens.space("inspector-width"))
        self.setObjectName("inspector")
        self.library: SequenceLibrary | None = None
        self.plasmid: Plasmid | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.box = QVBoxLayout(self.body)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(tokens.space("gap-group"))
        self.box.addStretch(1)
        self.scroll.setWidget(self.body)
        outer.addWidget(self.scroll)

    # ---- building blocks ----
    def _clear(self) -> None:
        while self.box.count():
            item = self.box.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _crumb(self, text: str) -> None:
        label = QLabel(text)
        label.setObjectName("crumb")
        label.setFont(_ui(11))
        label.setStyleSheet(f"color: {tokens.css(tokens.text('label-secondary'))}")
        self.box.addWidget(label)

    def _title(self, text: str, sub: str) -> None:
        head = QLabel(text)
        head.setFont(_ui(16, QFont.DemiBold))
        head.setWordWrap(True)
        self.box.addWidget(head)
        line = QLabel(sub)
        line.setFont(_ui(11))
        line.setWordWrap(True)
        line.setStyleSheet(f"color: {tokens.css(tokens.text('label-secondary'))}")
        self.box.addWidget(line)

    def _group(self, title: str, rows: list[tuple[str, str]]) -> None:
        if title:
            self.box.addWidget(inset_title(title))
        group = InsetGroup()
        for key, value in rows:
            group.add_row(key, value_label(value))
        self.box.addWidget(group)

    def _note(self, title: str, body: str) -> None:
        label = QLabel(f"<b>{title}</b><br>{body}")
        label.setWordWrap(True)
        label.setFont(_ui(11))
        label.setObjectName("sequenceNote")
        label.setStyleSheet(
            f"background: {tokens.css(tokens.tint('yellow', 0.16))};"
            f"border-radius: {tokens.radius('group')}px; padding: 10px 12px;")
        self.box.addWidget(label)

    def _buttons(self, *buttons: QPushButton) -> None:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addStretch(1)
        for button in buttons:
            lay.addWidget(button)
        self.box.addWidget(row)

    # ---- the three kinds ----
    def show_plasmid(self, library: SequenceLibrary, plasmid: Plasmid | None) -> None:
        self.library, self.plasmid = library, plasmid
        self._clear()
        if plasmid is None:
            self._crumb("Sequence Map")
            self._title("플라스미드 없음", "Sequence Map 폴더에서 .dna 파일을 찾지 못했습니다.")
            self.box.addStretch(1)
            return
        self._crumb("선택한 플라스미드")
        self._title(plasmid.name,
                    f"{plasmid.length:,} bp · {plasmid.topology} · feature {len(plasmid.record.features)}개")
        unique = plasmid.unique_cutters()
        twice = plasmid.cutters(2)
        none = plasmid.non_cutters()
        hits = library.plasmid_hits(plasmid.key) if library else []
        anneal = sum(1 for h in hits if h.anneals)
        self._group("색인 결과", [
            ("1회 자르는 보유 효소", f"{len(unique)}종"),
            ("2회 자르는 효소", f"{len(twice)}종"),
            ("자르지 않는 효소", f"{len(none)}종"),
            ("결합 올리고", f"{len(hits)}개 · 3′ 붙음 {anneal}개"),
        ])
        if unique:
            self._group("1회만 자르는 효소", [
                (name, f"{plasmid.cuts[name][0].pos:,}") for name in unique[:12]])
        if none:
            self._note("자르지 않는 보유 효소", ", ".join(none[:12])
                       + (f" 외 {len(none) - 12}종" if len(none) > 12 else ""))
        self.box.addStretch(1)

    def show_enzyme(self, library: SequenceLibrary, plasmid: Plasmid, name: str) -> None:
        self.library, self.plasmid = library, plasmid
        stock = library.enzymes.get(name)
        hits = plasmid.cuts.get(name, [])
        self._clear()
        self._crumb("캔버스에서 선택 · restriction enzyme")
        site = stock.site if stock else None
        self._title(stock.display if stock else name,
                    site.notation if site else "cut site 열이 비어 있습니다")
        where = ", ".join(f"{h.pos:,}" for h in hits) or "자르지 않음"
        unique = " · 이 벡터에서 유일" if len(hits) == 1 else ""
        feats = []
        if hits:
            feats = [f.name for f in plasmid.record.features
                     if f.start - 6 <= hits[0].pos <= f.end + 6]
        self._group("", [
            ("cut site", site.notation if site else "-"),
            ("인식 서열", site.site if site else "-"),
            ("절단 위치", where + unique),
            ("말단", site.end_label if site else "-"),
            ("겹치는 feature", ", ".join(feats[:2]) or "없음"),
        ])
        if stock is not None and stock.from_rebase:
            self._note("cut site 열이 비어 있습니다",
                       "REBASE 값으로 임시 표시 중입니다. 아래 버튼으로 재고 파일에 채워 넣을 수 있습니다.")
            fill = QPushButton("재고 파일에 cut site 채우기")
            fill.clicked.connect(self.fill_requested.emit)
            self._buttons(fill)
        self._group("restriction enzyme 재고 현황", self._enzyme_stock_rows(stock))
        open_btn = QPushButton("재고에서 열기")
        open_btn.clicked.connect(lambda: self.open_stock.emit("restriction enzyme",
                                                              stock.name if stock else name))
        self._buttons(open_btn)
        self.box.addStretch(1)

    @staticmethod
    def _enzyme_stock_rows(stock) -> list[tuple[str, str]]:
        if stock is None:
            return [("재고", "재고 목록에 없습니다")]
        return [("이름", stock.name), ("cut site 출처", "재고 파일" if not stock.from_rebase else "REBASE")]

    def show_oligo(self, library: SequenceLibrary, plasmid: Plasmid, name: str) -> None:
        self.library, self.plasmid = library, plasmid
        oligo = library.oligos.get(name)
        hit = next((h for h in library.plasmid_hits(plasmid.key) if h.oligo == name), None)
        self._clear()
        self._crumb("캔버스에서 선택 · oligo")
        sub = " · ".join(x for x in (oligo.order if oligo else "",
                                     f"{oligo.length} nt" if oligo else "",
                                     f"정제 {oligo.purification}" if oligo and oligo.purification else "",
                                     f"{oligo.ordered[:10]} 주문" if oligo and oligo.ordered else "") if x)
        self._title(name, sub or "Oligos 재고")
        if hit is not None:
            strand = "정방향" if hit.strand == "+" else "역방향"
            self._group("", [
                ("결합 위치", f"{hit.start:,} – {hit.end:,} · {strand}"),
                ("연속 일치", f"{hit.length} / {hit.oligo_len} nt · Tm {hit.tm:.1f} °C"),
                ("겹치는 feature", ", ".join(hit.features[:2]) or "없음"),
                ("판정", "3′ 붙음 (연장 가능)" if hit.anneals else "5′ 상동팔 · 부분 일치"),
            ])
            if not hit.anneals:
                self._note("3′ 말단이 붙지 않습니다",
                           "이 올리고는 벡터에 없는 꼬리를 가지고 있어 그대로는 연장되지 않습니다. "
                           "Gibson 상동팔이나 제한효소 자리를 붙인 올리고입니다.")
        self._group("Oligos 재고 현황", [
            ("합성 scale", (oligo.scale if oligo and oligo.scale else "-")),
            ("정제", (oligo.purification if oligo and oligo.purification else "-")),
            ("주문", (oligo.ordered[:10] if oligo and oligo.ordered else "-")),
            ("주문번호", (oligo.order if oligo and oligo.order else "-")),
        ])
        open_btn = QPushButton("Oligos 재고에서 열기")
        open_btn.clicked.connect(lambda: self.open_stock.emit("Oligos", name))
        self._buttons(open_btn)
        self.box.addStretch(1)


class SequenceView(QWidget):
    """One Sequence Map tab: the plasmids of one group and the map of the chosen one."""

    def __init__(self, window, group: str, parent=None):
        super().__init__(parent)
        self.window_ref = window
        self.group = group
        self.library: SequenceLibrary | None = None
        self.plasmid: Plasmid | None = None
        self.inspector = SequenceInspector()
        self.inspector.open_stock.connect(self._open_stock)
        self.inspector.fill_requested.connect(self.fill_cut_sites)

        layout = QVBoxLayout(self)
        margin = tokens.space("margin")
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(tokens.space("gap-group"))

        self.crumb = QLabel(f"Sequence Map › {group}")
        self.crumb.setFont(_ui(11))
        self.crumb.setStyleSheet(f"color: {tokens.css(tokens.text('label-secondary'))}")
        layout.addWidget(self.crumb)

        head = QHBoxLayout()
        self.title = QLabel(group)
        self.title.setFont(_ui(21, QFont.DemiBold))
        head.addWidget(self.title)
        head.addStretch(1)
        self.picker = QComboBox()
        self.picker.setMinimumWidth(240)
        self.picker.currentIndexChanged.connect(self._picked_plasmid)
        head.addWidget(self.picker)
        layout.addLayout(head)

        self.sub = QLabel("")
        self.sub.setFont(_ui(12))
        self.sub.setStyleSheet(f"color: {tokens.css(tokens.text('label-secondary'))}")
        layout.addWidget(self.sub)

        chips = QHBoxLayout()
        chips.setSpacing(7)
        self.chips: dict[str, QPushButton] = {}
        for key, text in (("features", "feature"), ("enzymes", "보유 효소 site"), ("oligos", "oligo")):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setChecked(True)
            button.setProperty("chip", True)
            button.clicked.connect(self._chips_changed)
            chips.addWidget(button)
            self.chips[key] = button
        chips.addStretch(1)
        self.settings_btn = QPushButton("색인 설정")
        self.settings_btn.clicked.connect(self.edit_settings)
        chips.addWidget(self.settings_btn)
        layout.addLayout(chips)

        self.overview = OverviewStrip()
        self.overview.moved.connect(self._scroll_to_base)
        layout.addWidget(self.overview)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.canvas = SequenceCanvas()
        self.canvas.picked.connect(self._picked_object)
        self.scroll.setWidget(self.canvas)
        self.scroll.verticalScrollBar().valueChanged.connect(self._sync_overview)
        layout.addWidget(self.scroll, 1)

        self.legend = QLabel("")
        self.legend.setFont(_ui(10))
        self.legend.setStyleSheet(f"color: {tokens.css(tokens.text('label-secondary'))}")
        layout.addWidget(self.legend)

    # ---- data ----
    def set_library(self, library: SequenceLibrary | None) -> None:
        self.library = library
        self.picker.blockSignals(True)
        self.picker.clear()
        plasmids = library.in_group(self.group) if library else []
        for plasmid in plasmids:
            self.picker.addItem(plasmid.name, plasmid.key)
        fit_select(self.picker)
        self.picker.blockSignals(False)
        self._show_plasmid(plasmids[0] if plasmids else None)

    def refresh(self) -> None:
        if self.library is not None:
            self._show_plasmid(self.plasmid)

    def _show_plasmid(self, plasmid: Plasmid | None) -> None:
        self.plasmid = plasmid
        self.canvas.set_plasmid(self.library, plasmid)
        self.overview.set_plasmid(plasmid)
        if plasmid is None:
            self.title.setText(self.group)
            self.sub.setText("Sequence Map 폴더에 .dna 파일이 없습니다.")
            self.inspector.show_plasmid(self.library, None)
            return
        self.title.setText(plasmid.name)
        cutting = sum(1 for hits in plasmid.cuts.values() if hits)
        hits = self.library.plasmid_hits(plasmid.key) if self.library else []
        self.sub.setText(f"{plasmid.length:,} bp · {plasmid.topology} · "
                         f"보유 효소 {len(plasmid.cuts)}종 중 {cutting}종이 절단 · "
                         f"결합 올리고 {len(hits)}개")
        self.legend.setText("빨강 절단 위치 · 파랑 3′ 붙는 올리고 · 주황 상동팔·부분 일치 · "
                            "정방향 위 · 역방향 아래 · 상보 가닥 항상 표시")
        self.inspector.show_plasmid(self.library, plasmid)
        self._sync_overview()

    def _picked_plasmid(self, index: int) -> None:
        if self.library is None or index < 0:
            return
        key = self.picker.itemData(index)
        self._show_plasmid(self.library.plasmids.get(key))

    def _chips_changed(self) -> None:
        self.canvas.set_flags(features=self.chips["features"].isChecked(),
                              enzymes=self.chips["enzymes"].isChecked(),
                              oligos=self.chips["oligos"].isChecked())
        self._sync_overview()

    # ---- selection ----
    def _picked_object(self, pick: Pick | None) -> None:
        if self.library is None or self.plasmid is None:
            return
        if pick is None or pick.kind == "feature":
            self.inspector.show_plasmid(self.library, self.plasmid)
        elif pick.kind == "enzyme":
            self.inspector.show_enzyme(self.library, self.plasmid, pick.name)
        elif pick.kind == "oligo":
            self.inspector.show_oligo(self.library, self.plasmid, pick.name)

    def _open_stock(self, category: str, name: str) -> None:
        """Jump to the row this thing comes from in its stock list."""
        project = getattr(self.window_ref, "project", None)
        if project is None:
            return
        for cat in project.categories():
            if cat.name.strip().lower().startswith(category.lower()):
                self.window_ref.show_category(cat.id)
                return

    def fill_cut_sites(self) -> None:
        project = getattr(self.window_ref, "project", None)
        if self.library is None or project is None:
            return
        written = self.library.fill_cut_sites(project)
        self.library.reindex()
        self._show_plasmid(self.plasmid)
        status = getattr(self.window_ref, "statusBar", None)
        if callable(status):
            status().showMessage(f"cut site 열에 {written}개 효소를 채웠습니다.", 4000)

    def edit_settings(self) -> None:
        dialog = IndexSettingsDialog(self)
        if dialog.exec() != QDialog.Accepted or self.library is None:
            return
        prefs = dialog.save()
        self.library.set_limits(min_match=prefs.get("sequence_min_match"),
                                min_tm=prefs.get("sequence_min_tm"),
                                full_length=prefs.get("sequence_full_length"))
        self.library.reindex()
        self._show_plasmid(self.plasmid)

    # ---- the window box on the overview ----
    def _sync_overview(self) -> None:
        if self.plasmid is None:
            return
        top = self.scroll.verticalScrollBar().value()
        bottom = top + self.scroll.viewport().height()
        rows = self.canvas._row_tops or []
        visible = [start for start, y in rows if bottom > y >= top - 40]
        if not visible:
            visible = [1]
        self.overview.set_window(visible[0], min(visible[-1] + self.canvas.per_row,
                                                 self.plasmid.length))

    def _scroll_to_base(self, base: int) -> None:
        y = self.canvas.row_at(base)
        self.scroll.verticalScrollBar().setValue(max(0, y - 20))


class IndexSettingsDialog(QDialog):
    """How much of an oligo has to match before it is shown on a map.

    The defaults are SnapGene's own: a run of at least 10 bases, and a run
    shorter than 15 counts only when it would melt at 40 °C or more.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("서열 색인 설정")
        prefs = settings.load_settings()
        layout = QVBoxLayout(self)
        layout.setSpacing(tokens.space("gap-group"))
        layout.addWidget(inset_title("일치로 볼 기준"))
        group = InsetGroup()
        self.min_match = QSpinBox()
        self.min_match.setRange(4, 40)
        self.min_match.setSuffix(" nt")
        self.min_match.setValue(int(prefs.get("sequence_min_match") or 10))
        group.add_row("최소 연속 일치", self.min_match)
        self.full_length = QSpinBox()
        self.full_length.setRange(5, 60)
        self.full_length.setSuffix(" nt")
        self.full_length.setValue(int(prefs.get("sequence_full_length") or 15))
        group.add_row("길이만으로 인정", self.full_length)
        self.min_tm = QDoubleSpinBox()
        self.min_tm.setRange(0.0, 90.0)
        self.min_tm.setDecimals(1)
        self.min_tm.setSuffix(" °C")
        self.min_tm.setValue(float(prefs.get("sequence_min_tm") or 40.0))
        group.add_row("그보다 짧으면 Tm", self.min_tm)
        layout.addWidget(group)
        hint = QLabel("짧은 일치는 Tm이 기준을 넘을 때만 표시됩니다. "
                      "기준을 낮추면 우연한 일치가 늘어납니다.")
        hint.setWordWrap(True)
        hint.setFont(_ui(11))
        hint.setStyleSheet(f"color: {tokens.css(tokens.text('label-secondary'))}")
        layout.addWidget(hint)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def save(self) -> dict:
        data = settings.load_settings()
        data["sequence_min_match"] = self.min_match.value()
        data["sequence_full_length"] = self.full_length.value()
        data["sequence_min_tm"] = self.min_tm.value()
        settings.save_settings(data)
        return data
