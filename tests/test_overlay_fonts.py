import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from interview_assistant.app.answer_appearance import bind_answer_appearance
from interview_assistant.config.keys import MemoryKeyStore
from interview_assistant.config.relay import MemoryRelayTokenStore
from interview_assistant.config.settings import Settings
from interview_assistant.config.user_settings import UserSettingsStore
from interview_assistant.ui.answer_typography import AnswerTypography
from interview_assistant.ui.overlay import OverlayWindow
from interview_assistant.ui.settings_dialog import SettingsDialog


@pytest.fixture
def editor(qtbot, tmp_path):
    store = UserSettingsStore(tmp_path / "settings.json")
    overlay = OverlayWindow(
        settings=QSettings(str(tmp_path / "ui.ini"), QSettings.Format.IniFormat)
    )
    dialog = SettingsDialog(
        store,
        MemoryKeyStore(),
        raw_settings=lambda: Settings(_env_file=None, **store.load()),
        relay_store=MemoryRelayTokenStore(),
    )
    qtbot.addWidget(overlay)
    qtbot.addWidget(dialog)
    state = {"active": True, "presenting": False}
    bind_answer_appearance(
        overlay,
        dialog,
        store,
        lambda: state["active"],
        lambda: state["active"] and not state["presenting"],
    )
    overlay.show()
    overlay.font_btn.click()
    return overlay, dialog, store, state


def select(combo, value):
    combo.setCurrentIndex(combo.findData(value))


def test_inline_persists_streaming_and_preserves_settings_draft(editor):
    overlay, dialog, store, _ = editor
    store.update({"incognito": True})
    dialog.history_on.setChecked(False)
    overlay.answer.setMarkdown("```text\nFirst answer.\n```")
    for combo, value in zip(overlay.font_controls.combos, ("Arial", "Bold", "Large"), strict=True):
        select(combo, value)
    expected = AnswerTypography("Arial", "Bold", "Large")
    assert overlay.answer.typography == expected
    assert dialog.answer_preview.typography == expected
    assert not dialog.history_on.isChecked()
    assert store.load()["incognito"] is True
    assert store.load()["answer_font_family"] == "Arial"
    overlay.answer.setMarkdown("```text\nFirst answer grows.\n```")
    QApplication.processEvents()
    assert overlay.answer.cards[0].label.font().pointSize() == 14
    overlay.answer.cards[0].copy()
    assert QApplication.clipboard().text() == "First answer grows."
    dialog.save()
    assert store.load()["answer_font_weight"] == "Bold"
    select(dialog.answer_font_size, "Small")
    dialog.save()
    assert overlay.answer.typography.size == "Small"
    assert overlay.font_controls.font_size.currentData() == "Small"


def test_failed_save_restores_last_applied_font(editor, monkeypatch):
    overlay, _, store, _ = editor
    select(overlay.font_controls.family, "Arial")

    def fail(_changes):
        raise OSError("Synthetic failure")

    monkeypatch.setattr(store, "update", fail)
    select(overlay.font_controls.family, "Calibri")
    assert overlay.answer.typography.family == "Arial"
    assert overlay.font_controls.family.currentData() == "Arial"
    assert "not saved" in overlay.font_controls.message.text()
    assert store.load()["answer_font_family"] == "Arial"


@pytest.mark.parametrize("flag", ["active", "presenting"])
def test_inactive_or_presentation_cannot_save(editor, flag):
    overlay, _, store, state = editor
    state[flag] = flag == "presenting"
    select(overlay.font_controls.family, "Arial")
    assert overlay.answer.typography == AnswerTypography()
    assert overlay.font_controls.family.currentData() == "Segoe UI"
    assert not store.path.exists()


def test_defaults_remove_only_font_overrides(editor):
    overlay, _, store, _ = editor
    store.update({"incognito": True})
    select(overlay.font_controls.family, "Arial")
    select(overlay.font_controls.family, "Segoe UI")
    assert store.load() == {"incognito": True}


def test_compact_layout_and_hidden_popup(editor, qtbot):
    overlay, _, _, _ = editor
    overlay.resize(640, 500)
    QApplication.processEvents()
    assert overlay.width() == 640
    assert overlay.height() == 500
    assert overlay.font_controls.isVisible()
    for combo in overlay.font_controls.combos:
        assert combo.width() > 70
    overlay.font_controls.family.showPopup()
    QApplication.processEvents()
    assert overlay.font_controls.family.view().isVisible()
    overlay.font_btn.click()
    qtbot.waitUntil(lambda: not overlay.font_controls.family.view().isVisible())
