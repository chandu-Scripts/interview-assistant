import pytest
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from interview_assistant.config.keys import MemoryKeyStore
from interview_assistant.config.relay import MemoryRelayTokenStore
from interview_assistant.config.settings import Settings
from interview_assistant.config.user_settings import UserSettingsStore
from interview_assistant.ui.answer_typography import FAMILIES, SIZES, WEIGHTS, AnswerTypography
from interview_assistant.ui.answer_view import AnswerView
from interview_assistant.ui.overlay import STYLE
from interview_assistant.ui.settings_dialog import SettingsDialog


@pytest.mark.parametrize("family", FAMILIES)
@pytest.mark.parametrize("weight", WEIGHTS)
@pytest.mark.parametrize("size", SIZES)
def test_themed_streaming_cards_keep_typography_and_raw_copy(qtbot, family, weight, size):
    parent = QWidget()
    qtbot.addWidget(parent)
    parent.setStyleSheet(STYLE)
    view = AnswerView()
    QVBoxLayout(parent).addWidget(view)
    parent.resize(500, 350)
    parent.show()
    view.setMarkdown("```text\nFirst answer.\n```\n\n```python\nx = 1\nprint(x)\n```")
    typography = AnswerTypography(family, weight, size)
    view.set_typography(typography)
    parent.ensurePolished()
    QApplication.processEvents()
    first = view.cards[0]
    assert first.label.font().family() == family
    assert first.label.font().pointSize() == SIZES[size]
    assert int(first.label.font().weight()) == WEIGHTS[weight]
    assert view.cards[1].label.font().family() == "Consolas"
    assert view.cards[1].label.font().weight() == QFont.Weight.Normal
    view.cards[1].copy()
    assert QApplication.clipboard().text() == "x = 1\nprint(x)"
    view.setMarkdown("```text\nFirst answer grows.\n```\n\n```text\nAnother paragraph.\n```")
    QApplication.processEvents()
    assert view.cards[0] is first
    assert view.cards[1].label.font().family() == family
    assert view.cards[1].label.font().pointSize() == SIZES[size]
    first.copy()
    assert QApplication.clipboard().text() == "First answer grows."
    assert parent.height() == 350


@pytest.fixture
def appearance(qtbot, tmp_path):
    store = UserSettingsStore(tmp_path / "appearance.json")
    dialog = SettingsDialog(
        store,
        MemoryKeyStore(),
        raw_settings=lambda: Settings(_env_file=None, **store.load()),
        relay_store=MemoryRelayTokenStore(),
    )
    qtbot.addWidget(dialog)
    return dialog, store


def choose(dialog):
    for name, value in (("family", "Arial"), ("weight", "Bold"), ("size", "Large")):
        combo = getattr(dialog, "answer_font_" + name)
        combo.setCurrentIndex(combo.findData(value))


def test_preview_cancel_save_reload_and_mixed_restart(appearance, qtbot):
    dialog, store = appearance
    applied, restarts = [], []
    dialog.typography_saved.connect(applied.append)
    dialog.saved.connect(restarts.append)
    choose(dialog)
    assert dialog.answer_preview.typography == AnswerTypography("Arial", "Bold", "Large")
    assert not applied and not store.path.exists()
    dialog.reject()
    dialog.load()
    assert dialog.answer_preview.typography == AnswerTypography()
    assert not applied and not store.path.exists()
    choose(dialog)
    dialog.save()
    assert applied == [AnswerTypography("Arial", "Bold", "Large")]
    assert restarts == [False]
    assert store.load()["answer_font_family"] == "Arial"
    dialog.load()
    assert dialog.answer_preview.typography == applied[0]
    dialog.answer_font_size.setCurrentIndex(0)
    dialog.history_on.setChecked(False)
    dialog.save()
    assert restarts == [False, True]
    assert applied[-1].size == "Small"


def test_failed_write_never_applies_appearance(appearance, monkeypatch):
    dialog, store = appearance
    applied, saved = [], []
    dialog.typography_saved.connect(applied.append)
    dialog.saved.connect(saved.append)
    choose(dialog)

    def fail(_values):
        raise OSError("Synthetic write failure")

    monkeypatch.setattr(store, "save", fail)
    dialog.save()
    assert not applied and not saved
    assert "Not saved" in dialog.message.text()


def test_invalid_font_setting_is_rejected_without_overwrite(appearance):
    _, store = appearance
    store.save({"answer_font_size": "Large"})
    with pytest.raises(ValueError):
        store.save({"answer_font_size": "Huge"})
    assert store.load() == {"answer_font_size": "Large"}


def test_typography_preserves_existing_incognito_preference_behavior(appearance):
    dialog, store = appearance
    store.save({"incognito": True})
    dialog.load()
    choose(dialog)
    dialog.save()
    assert store.load()["incognito"] is True
    assert store.load()["answer_font_family"] == "Arial"
