"""Rendered first-run setup checks using isolated, synthetic account state."""

import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import keyring
import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFontDatabase, QPalette
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QComboBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QWizard,
)

from interview_assistant.config.keys import MemoryKeyStore
from interview_assistant.config.settings import Settings
from interview_assistant.ui import onboarding
from interview_assistant.ui.bg import run_inline
from interview_assistant.ui.onboarding import OnboardingWizard
from interview_assistant.ui.theme import apply_theme


@pytest.fixture
def setup_ui(qapp, qtbot, monkeypatch, tmp_path):
    # Neither an installed .env nor operator settings/credentials can affect layout.
    for name in Settings.model_fields:
        if name.upper() != "DATA_DIR":
            monkeypatch.delenv(name.upper(), raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(onboarding, "current_context", lambda: object())
    external_calls = []

    def forbidden(*_args, **_kwargs):
        external_calls.append(True)
        raise AssertionError(
            "Appearance tests must not use credentials, network or model downloads"
        )

    for method in ("get_password", "set_password", "delete_password"):
        monkeypatch.setattr(keyring, method, forbidden)
    monkeypatch.setattr(httpx.Client, "request", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "request", forbidden)
    previous_palette = QPalette(qapp.palette())
    previous_stylesheet = qapp.styleSheet()
    previous_style = qapp.style().objectName()
    # Qt's offscreen Windows plugin does not discover installed fonts itself.
    font_path = Path(os.environ.get("SystemRoot", "C:/Windows")) / "Fonts" / "segoeui.ttf"
    font_id = QFontDatabase.addApplicationFont(str(font_path)) if font_path.is_file() else -1
    if os.name == "nt":
        assert font_id >= 0, (
            "Windows offscreen appearance tests require the installed Segoe UI font"
        )
    apply_theme(qapp)
    windows = []

    def create(area=None):
        if area is None:
            area = QRect(0, 0, 1280, 900)
        monkeypatch.setattr(
            QApplication,
            "primaryScreen",
            lambda: SimpleNamespace(availableGeometry=lambda: QRect(area)),
        )
        wizard = OnboardingWizard(
            MemoryKeyStore(),
            raw_settings=lambda: Settings(_env_file=None),
            checker=forbidden,
            runner=run_inline,
            warmup=forbidden,
        )
        windows.append(wizard)
        qtbot.addWidget(wizard)
        wizard.show()
        qtbot.waitUntil(lambda: wizard.isVisible() and wizard.currentId() == 0)
        qapp.processEvents()
        return wizard

    yield SimpleNamespace(create=create)
    for wizard in windows:
        wizard.close()
    qapp.setStyle(previous_style)
    qapp.setPalette(previous_palette)
    qapp.setStyleSheet(previous_stylesheet)
    if font_id != -1:
        QFontDatabase.removeApplicationFont(font_id)
    assert not external_calls


def advance(qtbot, wizard, page_id):
    qtbot.mouseClick(wizard.button(QWizard.WizardButton.NextButton), Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: wizard.currentId() == page_id)
    qtbot.wait(50)


def luminance(color: QColor) -> float:
    channels = [color.redF(), color.greenF(), color.blueF()]
    linear = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return sum(
        value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722), strict=True)
    )


def page_background(wizard) -> QColor:
    # Sample the rendered wizard behind the page's empty outside layout margin.
    # Using the whole window catches a native light backing behind transparent labels.
    page = wizard.currentPage()
    point = page.mapTo(wizard, QPoint(page.width() - 3, page.height() - 3))
    rendered = wizard.grab()
    ratio = rendered.devicePixelRatio()
    return rendered.toImage().pixelColor(round(point.x() * ratio), round(point.y() * ratio))


def assert_readable_page(wizard):
    background = luminance(page_background(wizard))
    assert background > 0.6, "The native wizard must paint the light glass page behind dark ink"
    labels = [
        label
        for label in wizard.findChildren(QLabel)
        if label.isVisible() and label.isEnabled() and label.text()
    ]
    assert labels
    for label in labels:
        foreground = luminance(label.palette().color(QPalette.ColorRole.WindowText))
        contrast = (max(foreground, background) + 0.05) / (min(foreground, background) + 0.05)
        assert contrast >= 4.5, "Enabled setup text must contrast with its rendered background"


def assert_controls_fit(wizard, area):
    assert area.contains(wizard.frameGeometry()), "Setup must remain within the available work area"
    page = wizard.currentPage()
    for child in wizard.findChildren(QLabel):
        if child.isVisible() and child.text() and child.wordWrap():
            required = child.heightForWidth(child.width())
            assert required <= child.height(), (
                f"Page {wizard.currentId()} clipped label {child.text()!r}: "
                f"needs {required}px, has {child.height()}px at width {child.width()}px"
            )
    controls = (
        page.findChildren(QAbstractButton)
        + page.findChildren(QLabel)
        + page.findChildren(QLineEdit)
        + page.findChildren(QPlainTextEdit)
        + page.findChildren(QComboBox)
    )
    for child in controls:
        if not child.isVisible():
            continue
        container = page
        ancestor = child.parentWidget()
        while ancestor is not None and ancestor is not page:
            if isinstance(ancestor, QScrollArea):
                # QLineEdit requests cursor visibility, which can leave its border clipped.
                # Scroll its whole provider row, as a user does to reach the field/buttons.
                row = child
                while row.parentWidget() is not ancestor.widget():
                    row = row.parentWidget()
                ancestor.ensureWidgetVisible(row, 0, 0)
                QApplication.processEvents()
                container = ancestor.viewport()
                break
            ancestor = ancestor.parentWidget()
        bounds = QRect(child.mapTo(container, QPoint()), child.size())
        assert container.rect().contains(bounds), "Each page control must be reachable in full"
        if isinstance(child, QAbstractButton | QLineEdit):
            assert child.height() >= child.sizeHint().height(), (
                "Scrolling must preserve usable fields and buttons instead of collapsing them"
            )
    for child in wizard.findChildren(QAbstractButton):
        if child.isVisible() and not page.isAncestorOf(child):
            bounds = QRect(child.mapTo(wizard, QPoint()), child.size())
            assert wizard.rect().contains(bounds), "Navigation controls must remain reachable"


def test_consent_and_provider_skip_are_visible_and_keep_setup_gates(setup_ui, qtbot):
    wizard = setup_ui.create()
    next_button = wizard.button(QWizard.WizardButton.NextButton)
    assert_readable_page(wizard)
    assert wizard.welcome.agree.isVisible()
    assert not next_button.isEnabled()
    qtbot.mouseClick(next_button, Qt.MouseButton.LeftButton)
    assert wizard.currentId() == 0
    qtbot.mouseClick(wizard.welcome.agree, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    assert next_button.isEnabled()
    advance(qtbot, wizard, 1)
    assert "own provider API keys" in wizard.providers.subTitle()
    assert not next_button.isEnabled()
    assert wizard.providers.skip.isVisible()
    qtbot.mouseClick(wizard.providers.skip, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    assert next_button.isEnabled()
    advance(qtbot, wizard, 2)
    assert not wizard.speech.cloud.isEnabled()
    qtbot.mouseClick(wizard.button(QWizard.WizardButton.CancelButton), Qt.MouseButton.LeftButton)
    assert not wizard.isVisible()
    assert wizard.result() == QWizard.DialogCode.Rejected


@pytest.mark.parametrize(
    "area", [QRect(0, 0, 1280, 900), QRect(0, 0, 840, 620), QRect(0, 0, 960, 510)]
)
def test_each_setup_page_fits_and_has_readable_surfaces(setup_ui, qtbot, area):
    wizard = setup_ui.create(area)
    wizard.welcome.agree.setChecked(True)
    wizard.providers.skip.setChecked(True)
    for page_id in range(4):
        if page_id:
            advance(qtbot, wizard, page_id)
        assert_controls_fit(wizard, area)
        assert_readable_page(wizard)
    finish_button = wizard.button(QWizard.WizardButton.FinishButton)
    assert finish_button.isVisible() and finish_button.isEnabled()


def test_provider_status_wraps_without_hiding_skip_or_navigation(setup_ui, qtbot):
    area = QRect(0, 0, 840, 620)
    wizard = setup_ui.create(area)
    wizard.welcome.agree.setChecked(True)
    advance(qtbot, wizard, 1)
    row = wizard.providers.rows["openai"]
    row.result.setText(
        "The authentication check could not finish because the connection was interrupted. "
        "Check your network connection and try again, or skip this step and add your key later."
    )
    qtbot.wait(50)
    assert_controls_fit(wizard, area)
    assert_readable_page(wizard)
    qtbot.mouseClick(wizard.providers.skip, Qt.MouseButton.LeftButton, pos=QPoint(8, 8))
    advance(qtbot, wizard, 2)
