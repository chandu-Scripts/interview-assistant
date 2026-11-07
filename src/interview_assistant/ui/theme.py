"""Shared blue glass theme: tokens become QSS, the application palette and painted surfaces."""

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
QScrollArea > QWidget > QWidget { background: transparent; }
QWidget#sidebar { background: transparent; }
QLabel#brand { font-size: 17px; font-weight: 700; padding: 8px 0 20px; color: ${blue_dark}; }
QLabel#title { font-size: 26px; font-weight: 650; }
QLabel#hint, QLabel#muted { color: ${muted_text}; }
QLabel#section { color: ${muted_text}; font-size: 11px; font-weight: 600; padding-top: 16px; }
QPushButton { background: rgba(${surf_rgb},${ctl}); border: 1px solid rgba(${ink_rgb},48);
    border-radius: ${r_control}px; padding: 9px 13px; }
QPushButton:hover { background: rgba(${blue_rgb},30); border-color: rgba(${blue_rgb},170); }
QPushButton:pressed { background: rgba(${blue_rgb},56); }
QPushButton:focus { border-color: ${blue}; }
QPushButton:checked { background: rgba(${blue_rgb},38); color: ${blue_dark};
    border-color: ${blue}; }
QPushButton:disabled { color: ${disabled}; background: rgba(${surf_rgb},${w_dim});
    border-color: rgba(${ink_rgb},24); }
QPushButton#create, QPushButton#connect, QPushButton#rowStart {
    background: ${blue}; color: ${on_blue}; border: 1px solid ${blue}; font-weight: 600; }
QPushButton#create:hover, QPushButton#connect:hover, QPushButton#rowStart:hover {
    background: ${blue_dark}; border-color: ${blue_dark}; }
QPushButton#create:focus, QPushButton#connect:focus, QPushButton#rowStart:focus {
    border: 1px solid ${ink}; }
QPushButton#create:disabled, QPushButton#connect:disabled, QPushButton#rowStart:disabled {
    color: ${disabled}; background: rgba(${surf_rgb},${w_dim});
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
QWidget#sessionCard { background: rgba(${surf_rgb},${card}); border-radius: ${r_card}px;
    border: 1px solid rgba(${edge_rgb},${w_edge}); }
QFrame#promptFeatures { background: rgba(${blue_rgb},20);
    border: 1px solid rgba(${blue_rgb},80); border-radius: ${r_control}px; }
QLabel#cardTitle { font-size: 15px; font-weight: 600; }
QHeaderView::section { background: rgba(${surf_rgb},${w_head}); color: ${muted_text}; border: none;
    padding: 10px; }
QLineEdit, QPlainTextEdit, QTextEdit, QTextBrowser, QComboBox, QSpinBox, QDoubleSpinBox {
    background: rgba(${surf_rgb},${ctl}); color: ${ink}; border: 1px solid rgba(${ink_rgb},56);
    border-radius: ${r_control}px; padding: 8px; selection-background-color: ${blue};
    selection-color: ${on_blue}; }
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus,
QDoubleSpinBox:focus { border: 1px solid ${blue}; }
QLineEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled { color: ${disabled}; }
QListWidget { background: rgba(${surf_rgb},${ctl}); border: 1px solid rgba(${ink_rgb},56);
    border-radius: ${r_control}px; }
QListWidget::item { padding: 12px 8px; }
QListWidget::item:selected { background: rgba(${blue_rgb},38); color: ${blue_dark}; }
QTabWidget::pane { border: 1px solid rgba(${ink_rgb},40); background: rgba(${surf_rgb},${w_pane});
    border-radius: ${r_control}px; }
QTabBar::tab { background: transparent; padding: 10px 16px; color: ${muted_text}; }
QTabBar::tab:selected { color: ${blue_dark}; border-bottom: 2px solid ${blue}; }
QComboBox QAbstractItemView, QMenu { background: ${solid_panel}; color: ${ink};
    border: 1px solid rgba(${ink_rgb},48); selection-background-color: rgba(${blue_rgb},40);
    selection-color: ${ink}; }
QMenu::item { padding: 8px 18px; }
QMenu::item:selected { background: rgba(${blue_rgb},40); }
QLabel#readyBadge { color: ${ok}; background: rgba(${ok_rgb},34); border-radius: 8px;
    padding: 5px 8px; }
QLabel#endedBadge { color: ${muted_text}; background: rgba(${ink_rgb},22); border-radius: 8px;
    padding: 5px 8px; }
QCheckBox { spacing: 9px; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 5px;
    border: 1px solid rgba(${ink_rgb},120); background: ${solid_control}; }
QCheckBox::indicator:checked { background: ${blue}; border: 4px solid ${solid_control}; }
QCheckBox:focus { color: ${blue_dark}; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: rgba(${ink_rgb},70); border-radius: 4px;
    min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QToolTip { color: ${ink}; background: ${solid_panel}; border: 1px solid rgba(${ink_rgb},60);
    padding: 6px; }
"""
)


def rgb(color: str) -> str:
    """'#rrggbb' -> 'r,g,b' for rgba() in QSS."""
    c = QColor(color)
    return f"{c.red()},{c.green()},{c.blue()}"


def _build_style(opaque: bool) -> str:
    """The glass stylesheet; ``opaque`` swaps every translucent surface for a solid one."""

    def alpha(value: int) -> int:
        return 255 if opaque else value

    return _STYLE_TEMPLATE.substitute(
        ink=tokens.INK,
        ink_rgb=tokens.INK_RGB,
        base=tokens.BASE,
        blue=tokens.BLUE,
        blue_rgb=rgb(tokens.BLUE),
        blue_dark=tokens.BLUE_DARK,
        on_blue=tokens.ON_BLUE,
        muted_text=tokens.MUTED_TEXT,
        disabled=tokens.DISABLED_TEXT,
        recording=tokens.RECORDING,
        ok=tokens.OK,
        ok_rgb=rgb(tokens.OK),
        surf_rgb=tokens.SURFACE_RGB,
        edge_rgb=rgb(tokens.EDGE),
        ctl=alpha(tokens.ALPHA_CONTROL),
        card=alpha(tokens.ALPHA_CARD),
        w_dim=alpha(110),
        w_head=alpha(150),
        w_pane=alpha(120),
        w_edge=alpha(tokens.EDGE_ALPHA),
        r_control=tokens.RADIUS_CONTROL,
        r_card=tokens.RADIUS_CARD,
        solid_panel=tokens.SOLID_PANEL,
        solid_control=tokens.SOLID_CONTROL,
    )


def __getattr__(name: str) -> str:
    # STYLE / OPAQUE_STYLE always reflect the active palette, so they can never be stale.
    if name == "STYLE":
        return _build_style(opaque=False)
    if name == "OPAQUE_STYLE":
        return _build_style(opaque=True)
    raise AttributeError(name)


def onboarding_style() -> str:
    return f"""
QWizardPage {{ background: transparent; }}
QRadioButton:disabled, QCheckBox:disabled, QLabel:disabled {{ color: {tokens.DISABLED_TEXT}; }}
"""


def window_style() -> str:
    """Stylesheet for top-level windows: empty in high contrast, solid with reduced transparency."""
    policy = motion.current()
    if policy.high_contrast:
        return ""
    return _build_style(opaque=policy.reduce_transparency)


def status_color(color: str) -> str:
    """A status text colour for local QSS; high contrast defers to the system text colour."""
    return "palette(window-text)" if motion.current().high_contrast else color


def white(alpha: int) -> str:
    """Translucent panel tint for local QSS (blue glass), solid when transparency is reduced."""
    solid = motion.current().reduce_transparency or motion.current().high_contrast
    return f"rgba({tokens.SURFACE_RGB}, {255 if solid else alpha})"


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
        (QPalette.ColorRole.Base, tokens.SOLID_CONTROL),
        (QPalette.ColorRole.AlternateBase, tokens.SOLID_CARD),
        (QPalette.ColorRole.Text, tokens.INK),
        (QPalette.ColorRole.Button, tokens.SOLID_CONTROL),
        (QPalette.ColorRole.ButtonText, tokens.INK),
        (QPalette.ColorRole.Highlight, tokens.BLUE),
        (QPalette.ColorRole.HighlightedText, tokens.ON_BLUE),
        (QPalette.ColorRole.PlaceholderText, tokens.MUTED_TEXT),
        (QPalette.ColorRole.Link, tokens.BLUE_DARK),
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
