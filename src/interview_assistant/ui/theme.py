"""Shared light glass theme: tokens become QSS, the application palette and painted surfaces."""

from string import Template

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPalette
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QWidget

from interview_assistant.ui import motion, tokens
from interview_assistant.ui.surfaces import Panel, paint_panel

_STYLE_TEMPLATE = Template(
    """
QWidget { font-family: 'Segoe UI'; font-size: 13px; color: ${ink}; }
QDialog, QMainWindow, QWizard { background: ${base}; }
QLabel, QScrollArea, QStackedWidget, QWidget#sessionForm, QWidget#accountBody {
    background: transparent; }
QScrollArea QDialog { background: transparent; }
QWidget#sidebar { background: transparent; }
QLabel#brand { font-size: 17px; font-weight: 700; padding: 8px 0 20px; color: ${blue}; }
QLabel#title { font-size: 22px; font-weight: 600; }
QLabel#hint, QLabel#muted { color: ${muted_text}; }
QLabel#section { color: ${muted_text}; font-size: 11px; font-weight: 600; padding-top: 16px; }
QPushButton { background: rgba(255,255,255,${ctl}); border: 1px solid rgba(${ink_rgb},48);
    border-radius: ${r_control}px; padding: 9px 13px; }
QPushButton:hover { background: rgba(${blue_rgb},30); border-color: rgba(${blue_rgb},170); }
QPushButton:pressed { background: rgba(${blue_rgb},56); }
QPushButton:focus { border-color: ${blue}; }
QPushButton:checked { background: rgba(${blue_rgb},38); color: ${blue_dark};
    border-color: ${blue}; }
QPushButton:disabled { color: ${disabled}; background: rgba(255,255,255,${w_dim});
    border-color: rgba(${ink_rgb},24); }
QPushButton#create, QPushButton#connect, QPushButton#rowStart {
    background: ${blue}; color: #ffffff; border: 1px solid ${blue}; font-weight: 600; }
QPushButton#create:hover, QPushButton#connect:hover, QPushButton#rowStart:hover {
    background: ${blue_dark}; border-color: ${blue_dark}; }
QPushButton#create:focus, QPushButton#connect:focus, QPushButton#rowStart:focus {
    border: 1px solid ${ink}; }
QPushButton#create:disabled, QPushButton#connect:disabled, QPushButton#rowStart:disabled {
    color: ${disabled}; background: rgba(255,255,255,${w_dim});
    border: 1px solid rgba(${ink_rgb},24); }
QPushButton#nav { text-align: left; background: transparent; border: 1px solid transparent;
    padding: 11px 9px; }
QPushButton#nav:hover { background: rgba(${blue_rgb},20); border-color: transparent; }
QPushButton#nav:focus { border-color: ${blue}; }
QPushButton#nav:checked { background: rgba(${blue_rgb},34); color: ${blue_dark};
    border-color: transparent; }
QPushButton#nav:disabled { background: transparent; border-color: transparent; }
QPushButton#filter { padding: 7px 16px; }
QPushButton#windowControl { padding: 4px; min-width: 28px; border: 1px solid transparent;
    background: transparent; }
QPushButton#windowControl:hover { background: rgba(${blue_rgb},26); }
QPushButton#windowClose { padding: 4px; min-width: 28px; border: 1px solid transparent;
    background: transparent; }
QPushButton#windowClose:hover { background: ${recording}; color: #ffffff; }
QTableWidget { background: transparent; border: none; gridline-color: rgba(${ink_rgb},28);
    selection-background-color: rgba(${blue_rgb},34); selection-color: ${ink}; }
QTableWidget::item { padding: 12px; border-bottom: 1px solid rgba(${ink_rgb},28); }
QWidget#sessionCard { background: rgba(255,255,255,${card}); border-radius: ${r_card}px;
    border: 1px solid rgba(255,255,255,${w_edge}); }
QFrame#promptFeatures { background: rgba(${blue_rgb},20);
    border: 1px solid rgba(${blue_rgb},80); border-radius: ${r_control}px; }
QLabel#cardTitle { font-size: 15px; font-weight: 600; }
QHeaderView::section { background: rgba(255,255,255,${w_head}); color: ${muted_text}; border: none;
    padding: 10px; }
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QComboBox, QSpinBox, QDoubleSpinBox {
    background: rgba(255,255,255,${ctl}); color: ${ink}; border: 1px solid rgba(${ink_rgb},56);
    border-radius: ${r_control}px; padding: 8px; selection-background-color: ${blue};
    selection-color: #ffffff; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus { border: 1px solid ${blue}; }
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled { color: ${disabled}; }
QListWidget { background: rgba(255,255,255,${ctl}); border: 1px solid rgba(${ink_rgb},56);
    border-radius: ${r_control}px; }
QListWidget::item { padding: 12px 8px; }
QListWidget::item:selected { background: rgba(${blue_rgb},38); color: ${blue_dark}; }
QTabWidget::pane { border: 1px solid rgba(${ink_rgb},40); background: rgba(255,255,255,${w_pane});
    border-radius: ${r_control}px; }
QTabBar::tab { background: transparent; padding: 10px 16px; color: ${muted_text}; }
QTabBar::tab:selected { color: ${blue_dark}; border-bottom: 2px solid ${blue}; }
QComboBox QAbstractItemView, QMenu { background: ${solid_panel}; color: ${ink};
    border: 1px solid rgba(${ink_rgb},48); selection-background-color: rgba(${blue_rgb},40);
    selection-color: ${ink}; }
QMenu::item { padding: 8px 18px; }
QMenu::item:selected { background: rgba(${blue_rgb},40); }
QLabel#readyBadge { color: ${ok}; background: rgba(23,113,74,30); border-radius: 8px;
    padding: 5px 8px; }
QLabel#endedBadge { color: ${muted_text}; background: rgba(${ink_rgb},22); border-radius: 8px;
    padding: 5px 8px; }
QCheckBox { spacing: 9px; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 5px;
    border: 1px solid rgba(${ink_rgb},120); background: #ffffff; }
QCheckBox::indicator:checked { background: ${blue}; border: 4px solid #dfe3ff; }
QCheckBox:focus { color: ${blue_dark}; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: rgba(${ink_rgb},70); border-radius: 4px;
    min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { color: ${ink}; background: ${solid_panel}; border: 1px solid rgba(${ink_rgb},60);
    padding: 6px; }
"""
)


def _build_style(opaque: bool) -> str:
    """The glass stylesheet; ``opaque`` swaps every translucent white for a solid one."""

    def alpha(value: int) -> int:
        return 255 if opaque else value

    return _STYLE_TEMPLATE.substitute(
        ink=tokens.INK,
        ink_rgb="35,40,71",
        base=tokens.BASE,
        blue=tokens.BLUE,
        blue_rgb="67,85,220",
        blue_dark=tokens.BLUE_DARK,
        muted_text=tokens.MUTED_TEXT,
        disabled=tokens.DISABLED_TEXT,
        recording=tokens.RECORDING,
        ok=tokens.OK,
        ctl=alpha(tokens.ALPHA_CONTROL),
        card=alpha(tokens.ALPHA_CARD),
        w_dim=alpha(110),
        w_head=alpha(150),
        w_pane=alpha(120),
        w_edge=alpha(200),
        r_control=tokens.RADIUS_CONTROL,
        r_card=tokens.RADIUS_CARD,
        solid_panel=tokens.SOLID_PANEL,
    )


STYLE = _build_style(opaque=False)
OPAQUE_STYLE = _build_style(opaque=True)

ONBOARDING_STYLE = f"""
QWizardPage {{ background: transparent; }}
QRadioButton:disabled, QCheckBox:disabled, QLabel:disabled {{ color: {tokens.DISABLED_TEXT}; }}
"""


def window_style() -> str:
    """Stylesheet for top-level windows: empty in high contrast, solid with reduced transparency."""
    policy = motion.current()
    if policy.high_contrast:
        return ""
    return OPAQUE_STYLE if policy.reduce_transparency else STYLE


def status_color(color: str) -> str:
    """A status text colour for local QSS; high contrast defers to the system text colour."""
    return "palette(window-text)" if motion.current().high_contrast else color


def white(alpha: int) -> str:
    """Translucent white for local QSS, solid when transparency is reduced."""
    solid = motion.current().reduce_transparency or motion.current().high_contrast
    return f"rgba(255, 255, 255, {255 if solid else alpha})"


def apply_theme(app: QApplication) -> None:
    app.setStyle("Fusion")
    if motion.current().high_contrast:
        # Defer to the system's high-contrast colours, discarding any earlier glass palette.
        app.setStyleSheet("")
        app.setPalette(app.style().standardPalette())
        return
    palette = QPalette()
    for role, color in (
        (QPalette.ColorRole.Window, tokens.BASE),
        (QPalette.ColorRole.WindowText, tokens.INK),
        (QPalette.ColorRole.Base, tokens.WHITE),
        (QPalette.ColorRole.AlternateBase, "#f4f5fe"),
        (QPalette.ColorRole.Text, tokens.INK),
        (QPalette.ColorRole.Button, tokens.WHITE),
        (QPalette.ColorRole.ButtonText, tokens.INK),
        (QPalette.ColorRole.Highlight, tokens.BLUE),
        (QPalette.ColorRole.HighlightedText, tokens.WHITE),
        (QPalette.ColorRole.PlaceholderText, tokens.MUTED_TEXT),
        (QPalette.ColorRole.Link, tokens.BLUE),
        (QPalette.ColorRole.ToolTipBase, tokens.SOLID_PANEL),
        (QPalette.ColorRole.ToolTipText, tokens.INK),
    ):
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(tokens.DISABLED_TEXT))
    app.setPalette(palette)
    app.setStyleSheet(window_style())


def paint_glass(painter: QPainter, rect: QRectF, radius: int = tokens.RADIUS_CARD) -> None:
    """Single translucent panel (kept for callers that paint one surface)."""
    paint_panel(painter, Panel(rect, radius, "panel"))


class WindowBar(QWidget):
    """Native window dragging and familiar controls for the floating dashboard."""

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self.setFixedHeight(44)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 4, 8, 4)
        label = QLabel("Chandoo  ·  Interview Assistant")
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        label.setStyleSheet(f"color: {tokens.MUTED_TEXT}; font-size: 11px; font-weight: 600;")
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
