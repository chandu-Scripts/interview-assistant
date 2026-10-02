from collections.abc import Callable

from PySide6.QtCore import QObject
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

COLORS = {"normal": "#5c6378", "incognito": "#7a5cf5", "presentation": "#d32f2f"}


def make_icon(color: str) -> QIcon:
    pm = QPixmap(32, 32)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(QColor("#ffffff"))
    p.drawEllipse(3, 3, 26, 26)
    p.end()
    return QIcon(pm)


class TrayController(QObject):
    """System-tray indicator + menu. It is the always-available way back while presenting."""

    def __init__(
        self,
        toggle_overlay: Callable[[], None],
        toggle_presentation: Callable[[], None],
        toggle_incognito: Callable[[], None],
        open_main: Callable[[], None],
        quit_app: Callable[[], None],
    ) -> None:
        super().__init__()
        self.tray = QSystemTrayIcon(make_icon(COLORS["normal"]))
        self.menu = QMenu()
        self.overlay_action = self._action("Show / hide overlay", toggle_overlay)
        self.presentation_action = self._action(
            "Presentation mode (hide + pause)", toggle_presentation, checkable=True
        )
        self.incognito_action = self._action(
            "Incognito (save nothing)", toggle_incognito, checkable=True
        )
        self.menu.addSeparator()
        self._action("Open main window", open_main)
        self._action("Quit", quit_app)
        self.tray.setContextMenu(self.menu)
        self.set_state(False, False)

    @staticmethod
    def available() -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def _action(self, text: str, fn: Callable[[], None], checkable: bool = False) -> QAction:
        a = QAction(text, self.menu)
        a.setCheckable(checkable)
        a.triggered.connect(lambda _checked=False: fn())
        self.menu.addAction(a)
        return a

    def set_state(self, presentation: bool, incognito: bool) -> None:
        self.presentation_action.setChecked(presentation)
        self.incognito_action.setChecked(incognito)
        key = "presentation" if presentation else "incognito" if incognito else "normal"
        self.tray.setIcon(make_icon(COLORS[key]))
        parts = ["Interview Assistant"]
        if presentation:
            parts.append("PRESENTATION MODE: hidden and paused")
        if incognito:
            parts.append("INCOGNITO: nothing is saved")
        self.tray.setToolTip(" - ".join(parts))

    def show(self) -> bool:
        if not self.available():
            return False
        self.tray.show()
        return True

    def hide(self) -> None:
        self.tray.hide()
