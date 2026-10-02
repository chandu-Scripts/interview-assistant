"""Render the app icon (the same disc used by the tray) into packaging/app.ico.

Run: python packaging/make_icon.py"""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter  # noqa: E402

OUT = Path(__file__).with_name("app.ico")


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    m = size * 0.06
    p.setBrush(QColor("#3b6cf6"))
    p.setPen(QColor("#ffffff"))
    p.drawEllipse(int(m), int(m), int(size - 2 * m), int(size - 2 * m))
    # a simple "sound wave" motif so the icon is recognisable at 16 px
    p.setPen(QColor("#ffffff"))
    bar_w = size * 0.07
    heights = [0.22, 0.42, 0.6, 0.42, 0.22]
    x = size * 0.24
    step = size * 0.13
    for h in heights:
        bh = size * h
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(int(x), int((size - bh) / 2), int(bar_w), int(bh), bar_w / 2, bar_w / 2)
        x += step
    p.end()
    return img


def main() -> None:
    QGuiApplication.instance() or QGuiApplication([])
    big = render(256)
    tmp = OUT.with_suffix(".png")
    big.save(str(tmp))
    Image.open(tmp).save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    tmp.unlink()
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
