"""Compact glass provider windows shared by settings and Preferred model."""

from functools import partial

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.config.keys import PROVIDERS
from interview_assistant.ui.controls import ChoiceComboBox
from interview_assistant.ui.provider_catalog import NAMES, ProviderCatalog, fingerprint
from interview_assistant.ui.theme import STYLE, paint_glass

COLORS = {
    "verified": "#8af0b6",
    "missing": "#ff9aab",
    "invalid": "#ff9aab",
    "unverified": "#ffd29b",
}


def update_provider_button(button: QPushButton, provider: str, catalog: ProviderCatalog) -> None:
    state, detail = catalog.status(provider)
    label = {
        "verified": "Verified",
        "missing": "No key",
        "invalid": "Invalid key",
        "unverified": "Not verified",
    }[state]
    button.setText(f"●  {NAMES[provider]}\n{label}")
    button.setProperty("providerState", state)
    button.setStyleSheet(
        f"QPushButton {{ color: {COLORS[state]}; text-align: left; padding: 14px; }}"
    )
    button.setToolTip(detail)
    button.setAccessibleName(f"{NAMES[provider]}: {label}")


class GlassDialog(QDialog):
    def __init__(self, title: str, parent: QWidget) -> None:
        super().__init__(parent, Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet(STYLE + "QDialog { background: transparent; }")
        self.resize(570, 530)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 20)
        header = QHBoxLayout()
        label = QLabel(title)
        label.setObjectName("cardTitle")
        header.addWidget(label, 1)
        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("windowClose")
        self.close_btn.setAccessibleName("Close provider window")
        self.close_btn.clicked.connect(self.reject)
        header.addWidget(self.close_btn)
        layout.addLayout(header)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("sessionForm")
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(2, 8, 12, 2)
        self.body.setSpacing(14)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

    def present(self) -> None:
        parent = self.parentWidget()
        screen = self.screen()
        if screen is not None:
            area = screen.availableGeometry()
            self.resize(min(570, area.width() - 32), min(560, area.height() - 48))
            center = (
                parent.mapToGlobal(parent.rect().center()) if parent is not None else area.center()
            )
            pos = center - self.rect().center()
            pos.setX(max(area.left(), min(pos.x(), area.right() - self.width())))
            pos.setY(max(area.top(), min(pos.y(), area.bottom() - self.height())))
            self.move(pos)
        self.show()
        self.raise_()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        paint_glass(painter, QRectF(self.rect()))

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() < 60:
            handle = self.windowHandle()
            if handle is not None:
                handle.startSystemMove()
        super().mousePressEvent(event)


class ModelPicker(GlassDialog):
    def __init__(self, selector: "ModelSelector", catalog: ProviderCatalog) -> None:
        super().__init__(selector.picker_title, selector)
        self.selector, self.catalog = selector, catalog
        self.provider = ""
        self.auto_btn = QPushButton("Auto · use configured routing")
        self.auto_btn.clicked.connect(lambda: self.choose(""))
        self.body.addWidget(self.auto_btn)
        for route in selector.shared_routes:
            button = QPushButton("Shared access · " + route.partition(":")[2].title())
            button.clicked.connect(partial(self.choose, route))
            self.body.addWidget(button)
        self.provider_buttons: dict[str, QPushButton] = {}
        for provider in PROVIDERS:
            button = QPushButton()
            button.clicked.connect(partial(self.show_provider, provider))
            self.provider_buttons[provider] = button
            self.body.addWidget(button)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.body.addWidget(self.info)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search this provider's models")
        self.search.textChanged.connect(self.fill_models)
        self.body.addWidget(self.search)
        self.models = QListWidget()
        self.models.setMinimumHeight(150)
        self.models.itemClicked.connect(
            lambda item: self.choose(item.data(Qt.ItemDataRole.UserRole))
        )
        self.models.itemActivated.connect(
            lambda item: self.choose(item.data(Qt.ItemDataRole.UserRole))
        )
        self.body.addWidget(self.models, 1)
        catalog.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        available = []
        for provider, button in self.provider_buttons.items():
            update_provider_button(button, provider, self.catalog)
            verified = self.catalog.status(provider)[0] == "verified"
            button.setVisible(verified)
            if verified:
                available.append(provider)
        if self.provider not in available:
            self.provider = ""
        self.info.setText(
            "Choose a provider. OpenAI shows the two recommended interview models."
            if available
            else "No verified providers. In Settings → AI providers, save a key, then Test key."
        )
        self.fill_models()

    def show_provider(self, provider: str) -> None:
        if self.catalog.status(provider)[0] != "verified":
            return
        self.provider = provider
        self.search.clear()
        self.info.setText(
            f"{NAMES[provider]} · select a model. Key verified; generation access may vary."
        )
        if not self.selector.provider_ready(provider):
            self.info.setText(
                "Key verified. Restart the app to use this changed credential for answers."
            )
        self.fill_models()

    def fill_models(self) -> None:
        self.models.clear()
        self.models.setVisible(bool(self.provider))
        self.search.setVisible(bool(self.provider) and self.provider != "openai")
        if not self.provider:
            return
        query = self.search.text().casefold()
        for label, route in self.catalog.options(self.provider):
            if query not in label.casefold():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, route)
            if not self.selector.provider_ready(self.provider):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setToolTip("Restart the app to use the changed key.")
            self.models.addItem(item)
        if not self.catalog.models(self.provider):
            self.info.setText(
                "Key verified, but neither recommended OpenAI model is listed for this account."
                if self.provider == "openai"
                else "Key verified, but the provider returned no supported text models."
            )

    def choose(self, route: str) -> None:
        if route and not self.selector.provider_ready(route.partition(":")[0]):
            self.info.setText("Restart the app to use this changed credential for answers.")
            return
        self.selector.refresh_models()
        index = self.selector.findData(route)
        if index < 0:
            self.refresh()
            return
        self.selector.setCurrentIndex(index)
        self.accept()


class ModelSelector(ChoiceComboBox):
    def __init__(self, choices: list[str], *, title: str = "Preferred model") -> None:
        super().__init__()
        self.picker_title = title
        self.setAccessibleName(title)
        self.catalog: ProviderCatalog | None = None
        self.runtime_keys: dict[str, str] | None = None
        self.shared_routes = [route for route in choices if route.startswith("relay:")]
        self.picker: ModelPicker | None = None
        self.addItem("Auto (recommended)", "")
        for route in dict.fromkeys(choices):
            self.addItem(route, route)

    def bind(self, catalog: ProviderCatalog, *, runtime_keys: dict[str, str] | None = None) -> None:
        self.catalog = catalog
        self.runtime_keys = runtime_keys
        catalog.changed.connect(self.refresh_models)
        self.refresh_models()

    def provider_ready(self, provider: str) -> bool:
        return (
            provider == "relay"
            or self.runtime_keys is None
            or self.catalog is None
            or self.runtime_keys.get(provider) == fingerprint(self.catalog.key(provider))
        )

    def refresh_models(self) -> None:
        if self.catalog is None:
            return
        previous = self.currentData()
        self.blockSignals(True)
        self.clear()
        self.addItem("Auto (recommended)", "")
        for route in self.shared_routes:
            self.addItem("Shared access · " + route.partition(":")[2].title(), route)
        routes: set[str] = set()
        for provider in PROVIDERS:
            for label, route in self.catalog.options(provider):
                if route not in routes:
                    self.addItem(f"{NAMES[provider]} · {label}", route)
                    routes.add(route)
        self.setCurrentIndex(max(0, self.findData(previous)))
        self.blockSignals(False)
        self.setToolTip(
            "Previous model is no longer verified. Auto is selected."
            if previous and previous not in routes and previous not in self.shared_routes
            else "Choose a verified provider, then its model."
        )

    def hideEvent(self, event) -> None:  # noqa: N802
        if self.picker is not None:
            self.picker.reject()
        super().hideEvent(event)

    def showPopup(self) -> None:  # noqa: N802
        if self.catalog is None:
            super().showPopup()
            return
        self.refresh_models()
        if self.picker is None:
            self.picker = ModelPicker(self, self.catalog)
            self.picker.finished.connect(self.hidePopup)
        self.picker.refresh()
        self.picker.present()
