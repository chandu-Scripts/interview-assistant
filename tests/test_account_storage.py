"""Account storage tests use synthetic identities and an isolated credential backend."""

import json
from pathlib import Path
from threading import Event, Thread

import pytest

from interview_assistant.account import context as context_module
from interview_assistant.account.context import AccountContext, activate_context, current_context
from interview_assistant.config.keys import KeyringStore, MemoryKeyStore
from interview_assistant.config.loader import load_settings
from interview_assistant.config.relay import KeyringRelayTokenStore, MemoryRelayTokenStore
from interview_assistant.config.settings import Settings
from interview_assistant.config.user_settings import UserSettingsStore, default_data_dir

USER_A = "11111111-1111-4111-8111-111111111111"
USER_B = "22222222-2222-4222-8222-222222222222"
ISSUER = "https://example.supabase.co/auth/v1"


@pytest.fixture(autouse=True)
def isolate_context(monkeypatch):
    monkeypatch.setattr(context_module, "_context", None)


@pytest.fixture
def context(tmp_path):
    return AccountContext(tmp_path, ISSUER, USER_A)


def test_namespace_is_account_and_issuer_scoped(context):
    other_user = AccountContext(context.root, ISSUER, USER_B)
    other_issuer = AccountContext(context.root, "https://other.supabase.co/auth/v1", USER_A)
    assert context.account_dir != other_user.account_dir
    assert context.account_dir != other_issuer.account_dir
    assert context.credential_namespace != other_user.credential_namespace
    assert context.credential_namespace != other_issuer.credential_namespace
    assert context.account_dir.is_relative_to(context.root / "accounts")
    assert AccountContext(context.root, ISSUER + "/", USER_A).namespace == context.namespace


@pytest.mark.parametrize(
    "user_id", ["../escape", "user@example.test", "invalid", USER_A.upper() + "x"]
)
def test_invalid_identity_is_rejected(tmp_path, user_id):
    with pytest.raises(ValueError):
        AccountContext(tmp_path, ISSUER, user_id)


@pytest.mark.parametrize(
    "issuer",
    ["http://example.test", "https://user:pass@example.test", "https://example.test?key=secret"],
)
def test_invalid_issuer_is_rejected(tmp_path, issuer):
    with pytest.raises(ValueError):
        AccountContext(tmp_path, issuer, USER_A)


def test_identity_cannot_be_replaced_after_logout(context):
    activate_context(context)
    context.revoke()
    assert current_context() is context
    with pytest.raises(RuntimeError):
        activate_context(AccountContext(context.root, ISSUER, USER_B))
    with pytest.raises(PermissionError):
        default_data_dir()


def test_legacy_settings_remain_untouched_and_environment_is_not_inherited(context, monkeypatch):
    legacy = context.root / "settings.json"
    legacy.write_text('{"candidate_profile":"legacy-private-resume","history_retention_days":1}')
    original = legacy.read_bytes()
    monkeypatch.setenv("DATA_DIR", str(context.root))
    monkeypatch.setenv("CANDIDATE_PROFILE", "environment-private-resume")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-legacy-provider-key")
    monkeypatch.setenv("RELAY_TOKEN", "fake-legacy-relay-token")
    monkeypatch.setenv("BUDGET_USD", "invalid-even-if-environment-is-present")
    monkeypatch.chdir(context.root)
    (context.root / ".env").write_text("GEMINI_API_KEY=fake-dotenv-key\n")
    activate_context(context)
    settings = load_settings(MemoryKeyStore(), MemoryRelayTokenStore())
    assert settings.candidate_profile is None
    assert settings.openai_api_key is None
    assert settings.gemini_api_key is None
    assert settings.relay_token is None
    assert settings.history_retention_days is None
    assert settings.data_dir == str(context.account_dir)
    assert default_data_dir() == context.account_dir
    UserSettingsStore().save({"candidate_profile": "new-account-resume"})
    assert legacy.read_bytes() == original
    assert Settings().candidate_profile == "new-account-resume"


def test_validation_never_reads_or_quarantines_legacy_settings(tmp_path, monkeypatch):
    legacy = tmp_path / "settings.json"
    legacy.write_bytes(b"malformed private settings")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    separate = tmp_path / "isolated" / "settings.json"
    UserSettingsStore(separate).save({"budget_usd": 2.5})
    assert legacy.read_bytes() == b"malformed private settings"
    assert not legacy.with_suffix(".json.bad").exists()
    with pytest.raises(ValueError):
        UserSettingsStore(separate).save({"budget_usd": "invalid"})


def test_explicit_legacy_settings_path_is_rejected(context):
    activate_context(context)
    with pytest.raises(ValueError):
        UserSettingsStore(context.root / "settings.json")
    with pytest.raises(ValueError):
        UserSettingsStore(context.account_dir / ".." / "escape.json")


def test_revocation_blocks_preexisting_store_writes_and_reads(context):
    activate_context(context)
    store = UserSettingsStore(context.account_dir / "settings.json")
    store.save({"candidate_profile": "account-a"})
    original = store.path.read_bytes()
    context.revoke()
    with pytest.raises(PermissionError):
        store.save({"candidate_profile": "late-callback"})
    with pytest.raises(PermissionError):
        store.update({"budget_usd": 4})
    with pytest.raises(PermissionError):
        store.load()
    assert store.path.read_bytes() == original


def test_keyring_and_relay_use_account_namespaces_and_deny_late_calls(context, monkeypatch):
    vault = {("InterviewAssistant", "openai"): "legacy-key"}
    monkeypatch.setattr("keyring.get_password", lambda service, key: vault.get((service, key)))
    monkeypatch.setattr(
        "keyring.set_password", lambda service, key, value: vault.__setitem__((service, key), value)
    )
    monkeypatch.setattr(
        "keyring.delete_password", lambda service, key: vault.pop((service, key), None)
    )
    activate_context(context)
    keys = KeyringStore()
    relay = KeyringRelayTokenStore()
    assert keys.get("openai") is None
    keys.set("openai", "new-account-key")
    relay.set("account-relay-token")
    settings = load_settings()
    assert settings.openai_api_key.get_secret_value() == "new-account-key"
    assert settings.relay_token is None  # Customers use their own direct provider keys.
    assert vault[(relay._service, "token")] == "account-relay-token"
    assert vault[("InterviewAssistant", "openai")] == "legacy-key"
    assert keys._service.endswith(context.credential_namespace)
    assert relay._service.endswith(context.credential_namespace)
    before = dict(vault)
    context.revoke()
    for operation in [
        lambda: keys.get("openai"),
        lambda: keys.set("openai", "late"),
        lambda: keys.delete("openai"),
        relay.get,
        lambda: relay.set("late"),
        relay.delete,
    ]:
        with pytest.raises(PermissionError):
            operation()
    assert vault == before


def test_qsettings_are_local_and_revoke_writes(context):
    settings = context.qsettings("App")
    assert Path(settings.fileName()).is_relative_to(context.account_dir)
    settings.setValue("onboarding_done", True)
    original = Path(settings.fileName()).read_bytes()
    context.revoke()
    with pytest.raises(PermissionError):
        settings.setValue("onboarding_done", False)
    with pytest.raises(PermissionError):
        settings.clear()
    assert Path(settings.fileName()).read_bytes() == original


def test_fresh_process_identity_does_not_read_prior_accounts_settings(context, monkeypatch):
    activate_context(context)
    first_store = UserSettingsStore()
    first_store.save({"candidate_profile": "account-a-only", "incognito": True})
    context.qsettings("App").setValue("onboarding_done", True)
    context.revoke()
    # This private reset simulates a new process; production has no reset API.
    monkeypatch.setattr(context_module, "_context", None)
    second = AccountContext(context.root, ISSUER, USER_B)
    activate_context(second)
    assert Settings().candidate_profile is None
    assert Settings().incognito is False
    assert second.qsettings("App").value("onboarding_done") is None
    UserSettingsStore().save({"candidate_profile": "account-b-only"})
    assert json.loads(first_store.path.read_text())["candidate_profile"] == "account-a-only"
    with pytest.raises(PermissionError):
        first_store.save({"candidate_profile": "late-account-a-result"})


def test_existing_directory_junction_cannot_redirect_an_account(context, tmp_path):
    import subprocess
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows directory-junction regression")
    outside = tmp_path / "unrelated"
    outside.mkdir()
    context.account_dir.parent.mkdir(parents=True)
    result = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(context.account_dir), str(outside)],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    with pytest.raises(ValueError):
        AccountContext(context.root, ISSUER, USER_A)
    with pytest.raises(ValueError):
        with context.mutation():
            pass


def test_revocation_serializes_with_an_inflight_write(context):
    started = Event()
    release = Event()
    finished = Event()

    def writer():
        with context.mutation():
            started.set()
            assert release.wait(2)

    def revoke():
        context.revoke()
        finished.set()

    writing = Thread(target=writer)
    writing.start()
    assert started.wait(2)
    revoking = Thread(target=revoke)
    revoking.start()
    assert not finished.wait(0.05)
    release.set()
    writing.join(2)
    revoking.join(2)
    assert finished.is_set()
    with pytest.raises(PermissionError):
        with context.mutation():
            pass


def test_account_directory_redirect_is_rejected(context, monkeypatch):
    original = Path.resolve

    def redirected(path, *args, **kwargs):
        if path == context.account_dir:
            return context.root / "outside-account"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", redirected)
    with pytest.raises(ValueError):
        with context.mutation():
            pass


def test_json_has_no_credentials(context):
    activate_context(context)
    store = UserSettingsStore()
    store.save({"openai_api_key": "fake-secret", "relay_token": "fake-token", "budget_usd": 4})
    assert json.loads(store.path.read_text()) == {"budget_usd": 4}
