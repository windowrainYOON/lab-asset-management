"""Draw the app icon (a flask in front of a stock box) to assets/icon/.

    python scripts/make_icon.py

Writes icon.png (1024 px) and icon.iconset/ for `iconutil -c icns` on macOS.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QBrush, QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
)

OUT = Path(__file__).resolve().parents[1] / "assets" / "icon"
SIZE = 1024


def draw(size: int = SIZE) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.scale(size / SIZE, size / SIZE)

    # macOS-style rounded square with a soft indigo gradient
    margin = 100
    body = QRectF(margin, margin, SIZE - 2 * margin, SIZE - 2 * margin)
    grad = QLinearGradient(body.topLeft(), body.bottomRight())
    grad.setColorAt(0, QColor("#5c6bc0"))
    grad.setColorAt(1, QColor("#283593"))
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(body, 185, 185)

    # stock box (back) with three vial slots
    box = QRectF(250, 330, 360, 400)
    p.setBrush(QColor(255, 255, 255, 60))
    p.drawRoundedRect(box, 40, 40)
    p.setBrush(QColor(255, 255, 255, 150))
    for i in range(3):
        for j in range(3):
            p.drawEllipse(QPointF(320 + i * 110, 410 + j * 120), 32, 32)

    # flask (front)
    flask = QPainterPath()
    flask.moveTo(600, 290)
    flask.lineTo(700, 290)
    flask.lineTo(700, 450)
    flask.lineTo(830, 700)
    flask.quadTo(850, 760, 790, 760)
    flask.lineTo(510, 760)
    flask.quadTo(450, 760, 470, 700)
    flask.lineTo(600, 450)
    flask.closeSubpath()
    p.setBrush(QColor("#ffffff"))
    p.drawPath(flask)

    liquid = QPainterPath()
    liquid.moveTo(545, 590)
    liquid.lineTo(755, 590)
    liquid.lineTo(812, 700)
    liquid.quadTo(825, 735, 785, 735)
    liquid.lineTo(515, 735)
    liquid.quadTo(475, 735, 488, 700)
    liquid.closeSubpath()
    p.setBrush(QColor("#26c6da"))
    p.drawPath(liquid)

    p.setPen(QPen(QColor("#ffffff"), 26, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(585, 290), QPointF(715, 290))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 200))
    p.drawEllipse(QPointF(620, 660), 18, 18)
    p.drawEllipse(QPointF(690, 690), 12, 12)
    p.end()
    return img


def main() -> None:
    _app = QGuiApplication(sys.argv)
    OUT.mkdir(parents=True, exist_ok=True)
    draw().save(str(OUT / "icon.png"))
    iconset = OUT / "icon.iconset"
    iconset.mkdir(exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        draw(base).save(str(iconset / f"icon_{base}x{base}.png"))
        draw(base * 2).save(str(iconset / f"icon_{base}x{base}@2x.png"))
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
