"""Simple round letter marks for the AI providers.

These are plain monograms in a brand-leaning colour, drawn in code. They are not the
providers' official logos, so there are no image assets to bundle or license.
"""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

# (background, letter). White letters stay at 4.4:1 or better on each background.
_MARKS: dict[str, tuple[str, str]] = {
    "openai": ("#0d7d62", "O"),
    "anthropic": ("#b5532f", "C"),
    "gemini": ("#3b6fe0", "G"),
}
_FALLBACK = ("#5a6080", "?")
_SCALE = 2


def provider_icon(provider: str, size: int = 28) -> QIcon:
    background, letter = _MARKS.get(provider, _FALLBACK)
    pixmap = QPixmap(size * _SCALE, size * _SCALE)
    pixmap.setDevicePixelRatio(_SCALE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(background))
    painter.drawEllipse(QRectF(0, 0, size, size))
    font = QFont("Segoe UI")
    font.setBold(True)
    font.setPixelSize(round(size * 0.56))
    painter.setFont(font)
    painter.setPen(QColor("#ffffff"))
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, letter)
    painter.end()
    return QIcon(pixmap)
