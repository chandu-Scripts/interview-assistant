"""Shared navy/cyan surfaces, with translucent glass on floating windows."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QWidget

STYLE = """
QWidget { font-family: 'Segoe UI'; font-size: 13px; color: #e6f3ff; }
QDialog, QMainWindow, QWizard { background: #101c30; }
QLabel, QScrollArea, QStackedWidget, QWidget#sessionForm { background: transparent; }
QWidget#sidebar { background: rgba(7,17,32,150); border-radius: 16px; }
QLabel#brand { font-size: 17px; font-weight: 700; padding: 8px 0 20px; color: #7ee9ed; }
QLabel#title { font-size: 22px; font-weight: 600; }
QLabel#hint, QLabel#muted { color: #a7b9d2; }
QLabel#section { color: #7e9ab9; font-size: 10px; padding-top: 16px; }
QPushButton { background: rgba(133,174,211,22); border: 1px solid rgba(171,210,240,35);
    border-radius: 10px; padding: 9px 13px; }
QPushButton:hover { background: rgba(100,205,224,40); border-color: #4c9faf; }
QPushButton:checked { background: #173e51; color: #8af0ed; border-color: #3e899a; }
QPushButton:disabled { color: #687e99; background: rgba(133,174,211,10); }
QPushButton#create, QPushButton#connect, QPushButton#rowStart {
    background: #77e5df; color: #08232f; border: none; font-weight: 600; }
QPushButton#create:hover, QPushButton#connect:hover, QPushButton#rowStart:hover {
    background: #a2f5ed; }
QPushButton#nav { text-align: left; background: transparent; border: none; padding: 11px 9px; }
QPushButton#nav:checked { background: rgba(95,220,222,28); color: #8af0ed; }
QPushButton#filter { padding: 7px 16px; }
QPushButton#windowControl { padding: 4px; min-width: 28px; border: none; background: transparent; }
QPushButton#windowControl:hover { background: #28415a; }
QPushButton#windowClose { padding: 4px; min-width: 28px; border: none; background: #442c40; }
QPushButton#windowClose:hover { background: #a63954; }
QTableWidget { background: transparent; border: none; gridline-color: #29425b;
    selection-background-color: #1e3b50; selection-color: #e6f3ff; }
QTableWidget::item { padding: 12px; border-bottom: 1px solid #29425b; }
QWidget#sessionCard { background: rgba(123,168,209,16); border-radius: 12px; }
QFrame#promptFeatures { background: rgba(25,58,77,150);
    border: 1px solid rgba(119,229,223,55); border-radius: 12px; }
QLabel#cardTitle { font-size: 15px; font-weight: 600; }
QHeaderView::section { background: #15273d; color: #a7b9d2; border: none; padding: 10px; }
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #12253a; color: #e6f3ff; border: 1px solid #334b66;
    border-radius: 9px; padding: 8px; selection-background-color: #285c74; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border-color: #77e5df; }
QListWidget { background: #12253a; border: 1px solid #334b66; border-radius: 9px; }
QListWidget::item { padding: 12px 8px; }
QListWidget::item:selected { background: #21485c; color: #9af4eb; }
QTabWidget::pane { border: 1px solid #334b66; background: #101c30; }
QTabBar::tab { background: #15273d; padding: 10px 16px; color: #a7b9d2; }
QTabBar::tab:selected { color: #8af0ed; border-bottom: 2px solid #77e5df; }
QComboBox QAbstractItemView, QMenu { background: #162b42; color: #e6f3ff;
    selection-background-color: #285c74; selection-color: #ffffff; }
QMenu::item { padding: 8px 18px; }
QMenu::item:selected { background: #285c74; }
QLabel#readyBadge { color: #8af0d1; background: #173d3e; border-radius: 6px; padding: 5px 8px; }
QLabel#endedBadge { color: #afbed2; background: #24344b; border-radius: 6px; padding: 5px 8px; }
QCheckBox { spacing: 9px; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 5px;
    border: 1px solid #60849d; background: #12253a; }
QCheckBox::indicator:checked { background: #77e5df; border: 3px solid #347c86; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: #46657f; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { color: #e6f3ff; background: #162b42; border: 1px solid #44637e; padding: 6px; }
"""


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, "#101c30"),
        (QPalette.ColorRole.WindowText, "#e6f3ff"),
        (QPalette.ColorRole.Base, "#12253a"),
        (QPalette.ColorRole.AlternateBase, "#1b3249"),
        (QPalette.ColorRole.Text, "#e6f3ff"),
        (QPalette.ColorRole.Button, "#1b3249"),
        (QPalette.ColorRole.ButtonText, "#e6f3ff"),
        (QPalette.ColorRole.Highlight, "#285c74"),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
        (QPalette.ColorRole.PlaceholderText, "#91a7bf"),
        (QPalette.ColorRole.Link, "#77e5df"),
    ):
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#687e99"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#687e99"))
    app.setPalette(palette)
    app.setStyleSheet(STYLE)


def paint_glass(painter: QPainter, rect: QRectF, radius: int = 18) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0, QColor(29, 57, 79, 245))
    gradient.setColorAt(0.45, QColor(17, 31, 52, 240))
    gradient.setColorAt(1, QColor(19, 32, 57, 245))
    painter.setBrush(gradient)
    painter.setPen(QColor(164, 220, 240, 65))
    painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), radius, radius)


class WindowBar(QWidget):
    """Native window dragging and familiar controls for the floating dashboard."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.setFixedHeight(44)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 4, 8, 4)
        label = QLabel("CHANDOO  /  INTERVIEW ASSISTANT")
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        label.setStyleSheet("color: #a6c6dc; font-size: 10px; font-weight: 600;")
        layout.addWidget(label)
        layout.addStretch()
        for text, tip, callback in (
            ("−", "Minimize", window.showMinimized),
            ("□", "Maximize / Restore", self.toggle_maximized),
            ("×", "Close", window.close),
        ):
            button = QPushButton(text)
            button.setObjectName("windowClose" if tip == "Close" else "windowControl")
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            button.clicked.connect(callback)
            layout.addWidget(button)

    def toggle_maximized(self) -> None:
        window = self.window()
        window.showNormal() if window.isMaximized() else window.showMaximized()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximized()


def center_window(window: QWidget, width: int = 960, height: int = 680) -> None:
    screen = QApplication.primaryScreen()
    if screen is not None:
        area = screen.availableGeometry()
        width = min(width, max(640, area.width() - 64))
        height = min(height, max(480, area.height() - 64))
        window.setMinimumSize(min(800, width), min(560, height))
        window.resize(width, height)
        window.move(area.center() - window.rect().center())
    else:
        window.resize(width, height)
