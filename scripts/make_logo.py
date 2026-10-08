"""
Собирает значок и логотип WinSpector Pro из SVG-исходников в assets/.

    python scripts/make_logo.py

- assets/app.ico — значок окна и EXE: 16–256 px; до 24 px включительно
  берётся упрощённая отрисовка logo-small.svg, иначе штрихи слишком тонкие.
- assets/logo.png и assets/logo-light.png — логотип с названием для
  README под тёмную и светлую тему GitHub.
"""

from __future__ import annotations

import sys
from io import BytesIO
from pathlib import Path

from PIL import Image
from PyQt6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication

ASSETS = Path(__file__).resolve().parent.parent / "assets"
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 96, 128, 256)
SMALL_UP_TO = 24

# Логотип с названием: значок, «WinSpector» и акцентное «Pro».
WORDMARK_HEIGHT = 320
WORDMARK_THEMES = {
    "logo.png": ("#E6E9EF", "#79A8FF"),  # тёмная тема
    "logo-light.png": ("#1A1F29", "#2F6FEB"),  # светлая тема
}


def render_svg(path: Path, size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(str(path)).render(painter, QRectF(0, 0, size, size))
    painter.end()
    return image


def to_pil(image: QImage) -> Image.Image:
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return Image.open(BytesIO(buffer.data().data())).convert("RGBA")


def make_icon() -> None:
    images = [
        to_pil(render_svg(ASSETS / ("logo-small.svg" if size <= SMALL_UP_TO else "logo.svg"), size))
        for size in ICON_SIZES
    ]
    largest = images[-1]
    largest.save(
        ASSETS / "app.ico",
        format="ICO",
        sizes=[(size, size) for size in ICON_SIZES],
        append_images=images[:-1],
    )


def make_wordmark(name: str, text_color: str, accent: str) -> None:
    height = WORDMARK_HEIGHT
    font = QFont("Segoe UI Variable Display Semibold")
    font.setPixelSize(round(height * 0.36))
    metrics = QFontMetricsF(font)
    title, suffix = "WinSpector", " Pro"
    gap = height * 0.12
    width = round(height + gap + metrics.horizontalAdvance(title + suffix) + height * 0.06)

    # Заглавные буквы — ровно по центру значка.
    baseline = height / 2 + metrics.capHeight() / 2
    x = height + gap

    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    QSvgRenderer(str(ASSETS / "logo.svg")).render(painter, QRectF(0, 0, height, height))
    painter.setFont(font)
    painter.setPen(QColor(text_color))
    painter.drawText(round(x), round(baseline), title)
    painter.setPen(QColor(accent))
    painter.drawText(round(x + metrics.horizontalAdvance(title)), round(baseline), suffix)
    painter.end()
    image.save(str(ASSETS / name))


def main() -> None:
    _app = QApplication.instance() or QApplication(sys.argv)  # шрифты без него недоступны
    make_icon()
    for name, (text_color, accent) in WORDMARK_THEMES.items():
        make_wordmark(name, text_color, accent)
    print("Готово:", ", ".join(["app.ico", *WORDMARK_THEMES]))


if __name__ == "__main__":
    main()
