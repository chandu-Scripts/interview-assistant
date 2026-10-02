import json
import sys
import uuid

import httpx
import pytest
from PySide6.QtCore import QSettings

from interview_assistant.app import restart
from interview_assistant.audio.types import AudioDevice, DeviceKind
from interview_assistant.config.keys import (
    KeyringStore,
    KeyStatus,
    MemoryKeyStore,
    all_status,
    key_status,
    mask,
)
from interview_assistant.config.loader import load_settings
from interview_assistant.config.provider_check import CheckResult, check_key
from interview_assistant.config.settings import Settings
from interview_assistant.config.user_settings import EDITABLE, UserSettingsStore, settings_path
from interview_assistant.privacy.consent import KEY as CONSENT_KEY
from interview_assistant.ui.audio_panel import AudioPanel
from interview_assistant.ui.bg import run_inline
from interview_assistant.ui.key_row import KeyRow
from interview_assistant.ui.main_window import MainWindow
from interview_assistant.ui.onboarding import OnboardingWizard, needs_onboarding
from interview_assistant.ui.settings_dialog import SettingsDialog

KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY")


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """No real keys or .env leak into these tests."""
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    return tmp_path


# ---------------- user settings file ----------------
def test_store_roundtrip_only_editable_and_no_secrets(clean_env):
    st = UserSettingsStore()
    st.save(
        {
            "whisper_model": "small.en",
            "openai_api_key": "sk-secret",
            "unknown": 1,
            "log_level": None,
        }
    )
    assert st.load() == {"whisper_model": "small.en"}
    assert "sk-secret" not in st.path.read_text(encoding="utf-8")
    assert not any(k.endswith("_api_key") for k in EDITABLE)


def test_store_missing_file_and_corrupt_file(clean_env):
    st = UserSettingsStore()
    assert st.load() == {} and not st.path.exists()
    st.path.parent.mkdir(parents=True, exist_ok=True)
    st.path.write_text("{not json", encoding="utf-8")
    assert st.load() == {}
    assert st.path.with_suffix(".json.bad").exists() and not st.path.exists()  # quarantined
    st.path.write_text("[1, 2]", encoding="utf-8")
    assert st.load() == {}


def test_store_rejects_invalid_values_without_touching_the_file(clean_env):
    st = UserSettingsStore()
    st.save({"whisper_model": "tiny.en"})
    with pytest.raises(ValueError):
        st.save({"stt_provider": "carrier-pigeon"})
    with pytest.raises(ValueError):
        st.save({"budget_usd": "lots"})
    assert st.load() == {"whisper_model": "tiny.en"}
    assert [p.name for p in st.path.parent.iterdir() if p.suffix == ".tmp"] == []  # atomic


def test_store_update_merges_and_none_removes(clean_env):
    st = UserSettingsStore()
    st.update({"whisper_model": "small.en", "budget_usd": 3.0})
    st.update({"budget_usd": None, "incognito": True})
    assert st.load() == {"whisper_model": "small.en", "incognito": True}


def test_settings_precedence_init_env_dotenv_json(clean_env, monkeypatch):
    UserSettingsStore().save({"whisper_model": "small.en", "openai_model": "from-json"})
    assert Settings().whisper_model == "small.en"  # json beats the built-in default
    (clean_env / ".env").write_text("OPENAI_MODEL=from-dotenv\n", encoding="utf-8")
    assert Settings().openai_model == "from-dotenv"  # .env beats json
    monkeypatch.setenv("OPENAI_MODEL", "from-env")
    assert Settings().openai_model == "from-env"  # environment beats .env
    assert Settings(openai_model="from-init").openai_model == "from-init"
    assert Settings().whisper_model == "small.en"


def test_settings_path_uses_data_dir_env(clean_env, monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "custom"))
    assert settings_path() == tmp_path / "custom" / "settings.json"


# ---------------- keys ----------------
def test_mask():
    assert mask("sk-abcdefghijklmnop") == "sk-…mnop"
    assert mask("short") == "………"
    assert "abcdefgh" not in mask("sk-abcdefghijklmnop")


def test_key_status_sources_and_precedence(clean_env):
    store = MemoryKeyStore({"gemini": "AIza-stored-key-1234"})
    raw = Settings(openai_api_key="sk-from-env-abcdef")
    st = all_status(raw, store)
    assert st["openai"].source == "env" and not st["openai"].editable and st["openai"].present
    assert st["gemini"].source == "keyring" and st["gemini"].editable
    assert st["anthropic"].source == "none" and not st["anthropic"].present
    both = key_status("openai", raw, MemoryKeyStore({"openai": "sk-stored-zzzz9999"}))
    assert both.source == "env"  # environment wins over the stored key
    assert (
        "Credential Manager" in st["gemini"].describe() and st["anthropic"].describe() == "Not set"
    )


def test_load_settings_fills_keys_from_store_without_overriding_env(clean_env, monkeypatch):
    store = MemoryKeyStore({"openai": "sk-stored-1111", "anthropic": "ant-stored-2222"})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-9999")
    s = load_settings(store)
    assert s.openai_api_key.get_secret_value() == "sk-env-9999"
    assert s.anthropic_api_key.get_secret_value() == "ant-stored-2222"
    assert s.gemini_api_key is None
    assert "ant-stored-2222" not in repr(s)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager")
def test_real_credential_manager_roundtrip():
    store = KeyringStore(service=f"InterviewAssistant-test-{uuid.uuid4().hex[:8]}")
    assert store.get("openai") is None
    store.set("openai", "sk-roundtrip-value-123")
    try:
        assert store.get("openai") == "sk-roundtrip-value-123"
    finally:
        store.delete("openai")
    assert store.get("openai") is None
    store.delete("openai")  # deleting a missing key is harmless


def test_keyring_store_survives_backend_errors(monkeypatch):
    import keyring

    def boom(*_a, **_k):
        raise RuntimeError("vault locked")

    monkeypatch.setattr(keyring, "get_password", boom)
    monkeypatch.setattr(keyring, "delete_password", boom)
    store = KeyringStore("x")
    assert store.get("openai") is None
    store.delete("openai")  # no raise
    monkeypatch.setattr(keyring, "set_password", boom)
    with pytest.raises(RuntimeError):
        store.set("openai", "k")  # saving must fail loudly so the UI can tell the user


# ---------------- provider checks ----------------
def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_check_openai_lists_and_filters_models():
    seen = {}

    def h(req):
        seen["auth"] = req.headers["authorization"]
        ids = [
            "gpt-4o",
            "gpt-4o-mini",
            "gpt-4o-audio-preview",
            "text-embedding-3",
            "o3-mini",
            "whisper-1",
        ]
        return httpx.Response(200, json={"data": [{"id": i} for i in ids]})

    r = check_key("openai", "sk-test", client(h))
    assert r.ok and r.models == ["gpt-4o", "gpt-4o-mini", "o3-mini"]
    assert seen["auth"] == "Bearer sk-test" and "3 models" in r.message


def test_check_anthropic_and_gemini_request_shape():
    seen = {}

    def anth(req):
        seen["a"] = (req.headers["x-api-key"], req.headers["anthropic-version"])
        return httpx.Response(
            200, json={"data": [{"id": "claude-sonnet-5"}, {"id": "claude-haiku-4-5"}]}
        )

    def gem(req):
        seen["g"] = (req.headers["x-goog-api-key"], str(req.url))
        body = {
            "models": [
                {
                    "name": "models/gemini-flash-latest",
                    "supportedGenerationMethods": ["generateContent"],
                },
                {"name": "models/gemini-tts", "supportedGenerationMethods": ["generateContent"]},
                {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]},
            ]
        }
        return httpx.Response(200, json=body)

    assert check_key("anthropic", "ak", client(anth)).models == [
        "claude-haiku-4-5",
        "claude-sonnet-5",
    ]
    assert seen["a"] == ("ak", "2023-06-01")
    assert check_key("gemini", "gk", client(gem)).models == ["gemini-flash-latest"]
    assert seen["g"][0] == "gk" and "gk" not in seen["g"][1]  # header, never the URL


@pytest.mark.parametrize(
    ("provider", "status", "ok", "fragment"),
    [
        ("openai", 401, False, "rejected"),
        ("anthropic", 403, False, "rejected"),
        ("gemini", 400, False, "rejected"),
        ("openai", 429, True, "rate limited"),
        ("openai", 500, False, "HTTP 500"),
    ],
)
def test_check_status_mapping(provider, status, ok, fragment):
    r = check_key(provider, "secret-key-value", client(lambda req: httpx.Response(status)))
    assert r.ok is ok and fragment in r.message and "secret-key-value" not in r.message


def test_check_network_errors_and_garbage():
    def timeout(_r):
        raise httpx.ReadTimeout("slow")

    def down(_r):
        raise httpx.ConnectError("dns")

    assert "timed out" in check_key("openai", "k", client(timeout)).message
    assert "could not connect" in check_key("openai", "k", client(down)).message
    assert (
        "unexpected"
        in check_key("openai", "k", client(lambda r: httpx.Response(200, text="<html>"))).message
    )
    assert check_key("openai", "   ").ok is False
    with pytest.raises(ValueError):
        check_key("mystery", "k")


# ---------------- KeyRow ----------------
def fake_checker(result):
    calls = []

    def check(provider, key):
        calls.append((provider, key))
        if isinstance(result, Exception):
            raise result
        return result

    check.calls = calls
    return check


def make_row(qtbot, provider="openai", store=None, checker=None, env_key=None):
    store = store if store is not None else MemoryKeyStore()
    raw = Settings(_env_file=None, openai_api_key=env_key) if env_key else Settings(_env_file=None)
    row = KeyRow(
        provider,
        store,
        lambda: key_status(provider, raw, store),
        checker or fake_checker(CheckResult(True, "ok")),
        run_inline,
    )
    qtbot.addWidget(row)
    return row, store


def test_key_row_states_and_buttons(qtbot, clean_env):
    row, store = make_row(qtbot)
    assert row.state.text() == "Not set"
    assert (
        not row.test_btn.isEnabled()
        and not row.save_btn.isEnabled()
        and not row.remove_btn.isEnabled()
    )
    qtbot.keyClicks(row.edit, "sk-new-key-1234")
    assert row.test_btn.isEnabled() and row.save_btn.isEnabled()
    assert row.edit.echoMode().name == "Password"


def test_key_row_save_clears_field_and_emits(qtbot, clean_env):
    row, store = make_row(qtbot)
    qtbot.keyClicks(row.edit, "sk-new-key-1234")
    with qtbot.waitSignal(row.changed):
        row.save_btn.click()
    assert store.get("openai") == "sk-new-key-1234" and row.edit.text() == ""
    assert "Credential Manager" in row.state.text() and "sk-…1234" in row.state.text()
    assert "sk-new-key-1234" not in row.state.text() + row.result.text()
    assert row.remove_btn.isEnabled()
    with qtbot.waitSignal(row.changed):
        row.remove_btn.click()
    assert store.get("openai") is None and row.state.text() == "Not set"


def test_key_row_test_reports_and_emits_models(qtbot, clean_env):
    chk = fake_checker(CheckResult(True, "openai: key works (2 models available).", ["a", "b"]))
    row, store = make_row(qtbot, checker=chk)
    qtbot.keyClicks(row.edit, "sk-typed-key-1234")
    with qtbot.waitSignal(row.models_found) as sig:
        row.test_btn.click()
    assert sig.args == ["openai", ["a", "b"]] and row.result.text().startswith("✔")
    assert chk.calls == [("openai", "sk-typed-key-1234")]
    store.set("openai", "sk-stored-key-5678")  # empty field -> tests the stored key
    row.edit.clear()
    row.refresh()
    row.test_btn.click()
    assert chk.calls[-1] == ("openai", "sk-stored-key-5678")


def test_key_row_failed_and_crashing_checks(qtbot, clean_env):
    row, _ = make_row(
        qtbot, checker=fake_checker(CheckResult(False, "openai: the key was rejected."))
    )
    qtbot.keyClicks(row.edit, "bad-key-000000")
    row.test_btn.click()
    assert row.result.text().startswith("✖") and "rejected" in row.result.text()
    row2, _ = make_row(qtbot, checker=fake_checker(RuntimeError("boom")))
    qtbot.keyClicks(row2.edit, "bad-key-000000")
    row2.test_btn.click()
    assert "Could not test" in row2.result.text() and "boom" not in row2.result.text()


def test_key_row_env_key_is_read_only_and_store_failure_reported(qtbot, clean_env):
    row, _ = make_row(qtbot, env_key="sk-env-key-abcdef")
    assert (
        "environment" in row.state.text()
        and not row.edit.isEnabled()
        and not row.remove_btn.isEnabled()
    )

    class Broken(MemoryKeyStore):
        def set(self, provider, key):
            raise OSError("vault unavailable")

    row2, _ = make_row(qtbot, store=Broken())
    qtbot.keyClicks(row2.edit, "sk-new-key-1234")
    row2.save_btn.click()
    assert "Could not save" in row2.result.text() and row2.edit.text() != ""


# ---------------- settings dialog ----------------
@pytest.fixture
def dialog(qtbot, clean_env):
    keys = MemoryKeyStore()
    d = SettingsDialog(
        UserSettingsStore(),
        keys,
        lambda: Settings(_env_file=None),
        fake_checker(CheckResult(True, "ok")),
        run_inline,
    )
    qtbot.addWidget(d)
    return d, keys


def test_dialog_loads_current_values(dialog):
    d, _ = dialog
    assert d.model_boxes["openai_model"].currentText() == "gpt-4o-mini"
    assert d.stt_provider.currentData() == "local" and d.whisper_model.currentData() == "base.en"
    assert d.default_audio_kind.currentData() == "loopback"
    assert not d.budget_on.isChecked() and d.redact.isChecked() and d.history_on.isChecked()
    assert d.hotkey_presentation.text() == "ctrl+alt+p"


def test_dialog_save_writes_only_non_defaults_and_requests_restart(qtbot, dialog):
    d, _ = dialog
    d.whisper_model.setCurrentIndex(d.whisper_model.findData("small.en"))
    d.budget_on.setChecked(True)
    d.budget_usd.setValue(7.5)
    d.budget_scope.setCurrentIndex(d.budget_scope.findData("day"))
    d.profile.setPlainText("Backend engineer, 5 years")
    with qtbot.waitSignal(d.saved) as sig:
        d.save_btn.click()
    assert sig.args == [True]
    saved = json.loads(settings_path().read_text(encoding="utf-8"))
    assert saved == {
        "whisper_model": "small.en",
        "budget_usd": 7.5,
        "budget_scope": "day",
        "candidate_profile": "Backend engineer, 5 years",
    }  # defaults are NOT pinned into the file
    s = Settings(_env_file=None)  # simulates the next start
    assert (s.whisper_model, s.budget_usd, s.budget_scope) == ("small.en", 7.5, "day")


def test_dialog_unchanged_save_does_not_ask_for_restart(qtbot, dialog):
    d, _ = dialog
    with qtbot.waitSignal(d.saved) as sig:
        d.save_btn.click()
    assert sig.args == [False] and not settings_path().exists()


def test_dialog_key_change_needs_restart(qtbot, dialog):
    d, keys = dialog
    row = d.key_rows["gemini"]
    qtbot.keyClicks(row.edit, "AIza-key-000000")
    row.save_btn.click()
    assert keys.get("gemini") == "AIza-key-000000" and "Restart" in d.message.text()
    with qtbot.waitSignal(d.saved) as sig:
        d.save_btn.click()
    assert sig.args == [True]


def test_dialog_validation_blocks_save(qtbot, dialog):
    d, _ = dialog
    d.hotkey_presentation.setText("ctrl+alt+enter")
    d.save_btn.click()
    assert "Not saved" in d.message.text() and not settings_path().exists()
    d.hotkey_presentation.setText("ctrl+alt+p")
    d.router_fast.setText("nomodel")
    d.save_btn.click()
    assert "Not saved" in d.message.text() and "router_fast" in d.message.text()
    d.router_fast.setText("openai:gpt-4o-mini,gemini:gemini-flash-latest")
    d.stt_provider.setCurrentIndex(d.stt_provider.findData("openai"))
    d.save_btn.click()
    assert "OpenAI key" in d.message.text()  # cloud STT without a key is refused
    d.hotkey_overlay.setText("")  # empty disables a hotkey and is valid
    d.stt_provider.setCurrentIndex(d.stt_provider.findData("local"))
    d.save_btn.click()
    assert d.message.text().startswith("Saved")
    assert json.loads(settings_path().read_text(encoding="utf-8"))["hotkey_overlay"] == ""


def test_dialog_models_fill_from_key_test(qtbot, dialog):
    d, _ = dialog
    d.key_rows["openai"].models_found.emit("openai", ["gpt-x", "gpt-y"])
    box = d.model_boxes["openai_model"]
    assert [box.itemText(i) for i in range(box.count())] == ["gpt-x", "gpt-y"]
    assert box.currentText() == "gpt-4o-mini"  # the current choice is kept even if not listed
    assert d.model_boxes["gemini_model"].count() == 0  # other providers untouched


def test_dialog_none_values_remove_saved_keys(qtbot, dialog):
    d, _ = dialog
    d.budget_on.setChecked(True)
    d.save_btn.click()
    assert "budget_usd" in UserSettingsStore().load()
    d.load()
    d.budget_on.setChecked(False)
    d.save_btn.click()
    assert "budget_usd" not in UserSettingsStore().load()


def test_main_window_settings_button(qtbot, dialog):
    d, _ = dialog
    win = MainWindow()
    qtbot.addWidget(win)
    assert not win.settings_button.isEnabled()
    win.attach_settings(d)
    assert win.settings_button.isEnabled()
    win.show_settings()
    assert d.isVisible()
    d.close()


# ---------------- onboarding ----------------
def make_wizard(qtbot, keys=None, warmup=None):
    keys = keys if keys is not None else MemoryKeyStore()
    w = OnboardingWizard(
        keys,
        lambda: Settings(_env_file=None),
        fake_checker(CheckResult(True, "ok")),
        run_inline,
        warmup or (lambda m: None),
    )
    qtbot.addWidget(w)
    return w, keys


def test_onboarding_gate_and_completion_rules(qtbot, clean_env, tmp_path):
    qs = QSettings(str(tmp_path / "a.ini"), QSettings.Format.IniFormat)
    assert needs_onboarding(qs)
    w, keys = make_wizard(qtbot)
    assert not w.welcome.isComplete()
    w.welcome.agree.setChecked(True)
    assert w.welcome.isComplete()
    assert not w.providers.isComplete()  # needs a key or an explicit skip
    row = w.providers.rows["openai"]
    qtbot.keyClicks(row.edit, "sk-onboard-key-1234")
    row.save_btn.click()
    assert w.providers.isComplete() and keys.get("openai") == "sk-onboard-key-1234"
    w.finish(qs)
    assert not needs_onboarding(qs) and qs.value(CONSENT_KEY, False, type=bool)
    w2, _ = make_wizard(qtbot)
    w2.providers.skip.setChecked(True)
    assert w2.providers.isComplete()


def test_onboarding_speech_page_and_collect(qtbot, clean_env):
    loaded = []
    w, keys = make_wizard(qtbot, warmup=loaded.append)
    sp = w.speech
    sp.initializePage()
    assert not sp.cloud.isEnabled()  # no OpenAI key yet
    sp.model.setCurrentIndex(sp.model.findData("small.en"))
    sp.download.click()
    assert loaded == ["small.en"] and "ready" in sp.status.text()
    keys.set("openai", "sk-abcdefgh-1234")
    sp.initializePage()
    assert sp.cloud.isEnabled()
    sp.cloud.setChecked(True)
    assert not sp.model.isEnabled() and not sp.download.isEnabled()
    w.profile.profile.setPlainText("  Data engineer  ")
    w.profile.budget_on.setChecked(True)
    w.profile.budget.setValue(12)
    w.profile.incognito.setChecked(True)
    sp.source.setCurrentIndex(sp.source.findData("mic"))
    assert w.collect() == {
        "stt_provider": "openai",
        "whisper_model": "small.en",
        "default_audio_kind": "mic",
        "candidate_profile": "Data engineer",
        "budget_usd": 12.0,
        "budget_scope": "session",
        "incognito": True,
    }


def test_onboarding_download_failure_is_shown(qtbot, clean_env):
    def fail(_model):
        raise OSError("offline")

    w, _ = make_wizard(qtbot, warmup=fail)
    w.speech.download.click()
    assert "Could not load" in w.speech.status.text() and w.speech.download.isEnabled()


def test_onboarding_result_round_trips_through_the_store(qtbot, clean_env):
    w, _ = make_wizard(qtbot)
    w.profile.budget_on.setChecked(True)
    w.profile.budget.setValue(3)
    UserSettingsStore().update(w.collect())
    s = Settings(_env_file=None)
    assert s.budget_usd == 3.0 and s.whisper_model == "base.en" and s.stt_provider == "local"


# ---------------- audio default kind ----------------
DEVICES = [
    AudioDevice(1, "Mic A", DeviceKind.MIC, 2, 48000, True),
    AudioDevice(2, "Mic B", DeviceKind.MIC, 2, 48000, False),
    AudioDevice(3, "Speakers [Loopback]", DeviceKind.LOOPBACK, 2, 48000, True),
]


@pytest.mark.parametrize(
    ("kind", "expected"),
    [(DeviceKind.LOOPBACK, "Speakers"), (DeviceKind.MIC, "Mic A"), (None, "Mic A")],
)
def test_audio_panel_preselects_configured_kind(qtbot, kind, expected):
    p = AudioPanel(DEVICES, default_kind=kind)
    qtbot.addWidget(p)
    assert expected in p.device_combo.currentText()


def test_audio_panel_kind_without_default_falls_back(qtbot):
    only_mics = [d for d in DEVICES if d.kind is DeviceKind.MIC]
    p = AudioPanel(only_mics, default_kind=DeviceKind.LOOPBACK)
    qtbot.addWidget(p)
    assert "Mic A" in p.device_combo.currentText()
    nodefault = [AudioDevice(2, "Mic B", DeviceKind.MIC, 2, 48000, False)]
    p2 = AudioPanel(nodefault, default_kind=DeviceKind.MIC)
    qtbot.addWidget(p2)
    assert "Mic B" in p2.device_combo.currentText()


# ---------------- restart ----------------
def test_restart_flag_and_relaunch(monkeypatch):
    monkeypatch.setattr(restart, "_requested", False)
    assert not restart.restart_requested()
    restart.request_restart()
    assert restart.restart_requested()
    started = []
    monkeypatch.setattr(restart.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    restart.relaunch()
    assert started == [[sys.executable, "-m", "interview_assistant"]]


def test_key_status_dataclass_helpers():
    assert KeyStatus("openai", "none", "").present is False
    assert KeyStatus("openai", "env", "sk-…1234").editable is False
    assert KeyStatus("openai", "keyring", "sk-…1234").editable is True
