"""An explicit in-app key save must work even when .env already supplies a key."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from interview_assistant.config.keys import MemoryKeyStore, effective_key, key_status
from interview_assistant.config.loader import load_settings
from interview_assistant.config.provider_check import CheckResult
from interview_assistant.config.relay import MemoryRelayTokenStore
from interview_assistant.config.settings import Settings
from interview_assistant.config.user_settings import UserSettingsStore
from interview_assistant.ui.bg import run_inline
from interview_assistant.ui.onboarding import OnboardingWizard
from interview_assistant.ui.settings_dialog import SettingsDialog


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for field in Settings.model_fields:
        if field != "data_dir":
            monkeypatch.delenv(field.upper(), raising=False)


def dialog(qtbot, keys, store=None):
    d = SettingsDialog(
        store or UserSettingsStore(),
        keys,
        Settings,
        checker=lambda p, k: CheckResult(True, "Verified", ["gpt-4o-mini"]),
        runner=run_inline,
        relay_store=MemoryRelayTokenStore(),
    )
    qtbot.addWidget(d)
    return d


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini", "speechmatics"])
def test_saved_key_overrides_dotenv_and_survives_restart(qtbot, tmp_path, provider):
    path = tmp_path / ".env"
    original = f"{provider.upper()}_API_KEY=old-environment-key\n"
    path.write_text(original)
    keys = MemoryKeyStore()
    d = dialog(qtbot, keys)
    row = d.key_rows[provider]
    assert row.edit.isEnabled()
    assert "Paste a replacement" in row.state.text()
    row.edit.setText("new-app-saved-key")
    assert row.save_btn.isEnabled()
    row.save_btn.click()
    assert row.edit.text() == ""
    raw = Settings()
    assert provider in raw.prefer_saved_keys
    assert key_status(provider, raw, keys).source == "keyring"
    assert effective_key(provider, raw, keys) == "new-app-saved-key"
    loaded = load_settings(keys, MemoryRelayTokenStore())
    assert getattr(loaded, f"{provider}_api_key").get_secret_value() == "new-app-saved-key"
    assert path.read_text() == original
    serialized = UserSettingsStore().path.read_text()
    assert "new-app-saved-key" not in serialized and "old-environment-key" not in serialized
    assert "Restart" in row.result.text()
    d.load()
    assert "Credential Manager" in row.state.text()
    row.remove_btn.click()
    loaded = load_settings(keys, MemoryRelayTokenStore())
    assert getattr(loaded, f"{provider}_api_key") is None
    assert key_status(provider, Settings(), keys).source == "none"


def test_ctrl_v_into_openai_field_with_environment_key(qtbot, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    d = dialog(qtbot, MemoryKeyStore())
    d.show()
    d.provider_buttons["openai"].click()
    row = d.key_rows["openai"]
    qtbot.waitUntil(lambda: row.edit.isVisible())
    clipboard = QApplication.clipboard()
    current = clipboard.mimeData()
    if current is not None and set(current.formats()) - {"text/plain"}:
        pytest.skip("Preserve the user's non-text clipboard without replacing its MIME object")
    previous = clipboard.text()
    try:
        clipboard.setText("fake-paste-test-key")
        row.edit.setFocus()
        qtbot.keyClick(row.edit, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        assert row.edit.text() == "fake-paste-test-key"
        assert row.save_btn.isEnabled()
    finally:
        # A Python-owned QMimeData crashes Qt's offscreen plugin during shutdown on Windows.
        # Let Qt own the plain-text MIME object, as the app's copy buttons already do.
        clipboard.setText(previous)


def test_preference_failure_restores_previous_credential(qtbot, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    keys = MemoryKeyStore({"openai": "old-saved-key"})
    store = UserSettingsStore()

    def fail(_changes):
        raise OSError("simulated failure")

    monkeypatch.setattr(store, "update", fail)
    d = dialog(qtbot, keys, store)
    row = d.key_rows["openai"]
    row.edit.setText("replacement-key")
    row.save()
    assert keys.get("openai") == "old-saved-key"
    assert effective_key("openai", Settings(), keys) == "environment-key"
    assert "Could not save" in row.result.text()
    assert "replacement-key" not in row.result.text()


def test_onboarding_can_replace_environment_key(qtbot, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    keys = MemoryKeyStore()
    wizard = OnboardingWizard(keys, raw_settings=Settings, runner=run_inline)
    qtbot.addWidget(wizard)
    row = wizard.providers.rows["openai"]
    assert row.edit.isEnabled()
    row.edit.setText("onboarding-saved-key")
    row.save()
    assert (
        load_settings(keys, MemoryRelayTokenStore()).openai_api_key.get_secret_value()
        == "onboarding-saved-key"
    )


def test_existing_shadowed_key_does_not_take_over_without_explicit_save(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key")
    keys = MemoryKeyStore({"openai": "old-unused-stored-key"})
    loaded = load_settings(keys, MemoryRelayTokenStore())
    assert loaded.openai_api_key.get_secret_value() == "environment-key"
