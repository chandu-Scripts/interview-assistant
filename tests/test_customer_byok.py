"""Customer billing uses only their provider keys; every credential and request is synthetic."""

from types import SimpleNamespace

import httpx
import pytest
from PySide6.QtWidgets import QLabel

from interview_assistant.account import context as context_module
from interview_assistant.account.context import AccountContext
from interview_assistant.config.keys import KEY_PROVIDERS, KeyringStore, MemoryKeyStore
from interview_assistant.config.loader import load_settings
from interview_assistant.config.provider_check import CheckResult
from interview_assistant.config.relay import MemoryRelayTokenStore
from interview_assistant.config.settings import Settings
from interview_assistant.config.user_settings import UserSettingsStore
from interview_assistant.llm.factory import build_providers
from interview_assistant.llm.router import AllRoutesFailed, NoRoutesAvailable, Route, collect_routed
from interview_assistant.llm.router_factory import build_router, default_chains
from interview_assistant.llm.types import ErrorKind, LLMError
from interview_assistant.ui.bg import run_inline
from interview_assistant.ui.onboarding import ProvidersPage
from interview_assistant.ui.provider_catalog import fingerprint
from interview_assistant.ui.provider_picker import ModelSelector
from interview_assistant.ui.settings_dialog import SettingsDialog
from tests.test_router import REQ, Script

USER = "11111111-1111-4111-8111-111111111111"


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(context_module, "_context", None)
    monkeypatch.chdir(tmp_path)
    for field in Settings.model_fields:
        if field != "data_dir":
            monkeypatch.delenv(field.upper(), raising=False)
    vault, reads = {}, []

    def get(service, account):
        reads.append((service, account))
        return vault.get((service, account))

    monkeypatch.setattr("keyring.get_password", get)
    monkeypatch.setattr("keyring.set_password", lambda s, a, k: vault.__setitem__((s, a), k))
    monkeypatch.setattr("keyring.delete_password", lambda s, a: vault.pop((s, a), None))

    def no_network(*args, **kwargs):
        pytest.fail("BYOK tests must never contact a live service")

    monkeypatch.setattr(httpx.Client, "send", no_network)
    monkeypatch.setattr(httpx.AsyncClient, "send", no_network)
    return SimpleNamespace(vault=vault, reads=reads)


@pytest.fixture
def customer(monkeypatch, tmp_path, isolated):
    context = AccountContext(tmp_path, "https://byok-test.supabase.co", USER)
    monkeypatch.setattr(context_module, "_context", context)
    return context


class ForbiddenRelay:
    def get(self):
        pytest.fail("A customer must not read relay credentials")

    def set(self, token):
        pytest.fail("A customer must not change relay credentials")

    def delete(self):
        pytest.fail("Existing relay credentials must be preserved")


def forbidden_constructor(*args, **kwargs):
    pytest.fail("Customer mode must not construct relay UI, storage or network clients")


def stale_settings(**values):
    return Settings(
        relay_url="https://synthetic-owner-relay.example",
        relay_token="synthetic-owner-relay-token",
        relay_pending_id="synthetic-pending",
        **values,
    )


def spy_factories(monkeypatch):
    records = []
    for name, cls in (
        ("openai", "OpenAIProvider"),
        ("anthropic", "AnthropicProvider"),
        ("gemini", "GeminiProvider"),
    ):

        def make(key, model, *, provider=name, **kwargs):
            records.append((provider, key, kwargs))
            return Script(provider, ["Synthetic answer"], model)

        monkeypatch.setattr(f"interview_assistant.llm.factory.{cls}", make)
    return records


def test_customer_never_inherits_owner_env_vault_or_relay(customer, isolated, monkeypatch):
    root = customer.root
    for provider in KEY_PROVIDERS:
        monkeypatch.setenv(f"{provider.upper()}_API_KEY", f"synthetic-owner-env-{provider}")
        isolated.vault[("InterviewAssistant", provider)] = f"synthetic-owner-vault-{provider}"
    monkeypatch.setenv("RELAY_TOKEN", "synthetic-owner-env-relay")
    (root / ".env").write_text("OPENAI_API_KEY=synthetic-owner-dotenv")
    (root / "settings.json").write_text('{"relay_url":"https://owner.example"}')
    original = (root / "settings.json").read_bytes()
    scoped = f"InterviewAssistant:{customer.credential_namespace}"
    relay_scoped = f"InterviewAssistantRelay:{customer.credential_namespace}"
    isolated.vault[("InterviewAssistantRelay", "token")] = "synthetic-owner-token"
    isolated.vault[(relay_scoped, "token")] = "synthetic-account-old-relay"
    UserSettingsStore().save({"relay_url": "https://stale.example", "relay_pending_id": "old"})
    monkeypatch.setattr(
        "interview_assistant.config.loader.KeyringRelayTokenStore", forbidden_constructor
    )
    settings = load_settings()
    assert all(getattr(settings, f"{p}_api_key") is None for p in KEY_PROVIDERS)
    assert settings.relay_url is settings.relay_token is settings.relay_pending_id is None
    assert isolated.reads and all(service == scoped for service, _ in isolated.reads)
    assert isolated.vault[(relay_scoped, "token")] == "synthetic-account-old-relay"
    assert UserSettingsStore().load()["relay_pending_id"] == "old"
    assert (root / "settings.json").read_bytes() == original
    # Even an explicitly supplied relay-store test double must be ignored in customer mode.
    assert load_settings(MemoryKeyStore(), ForbiddenRelay()).relay_token is None


def test_only_customer_keys_construct_direct_providers(customer, isolated, monkeypatch):
    records = spy_factories(monkeypatch)
    keys = KeyringStore()
    for provider in KEY_PROVIDERS:
        keys.set(provider, f"synthetic-customer-{provider}")
    loaded = load_settings(keys, ForbiddenRelay())
    # Factory defends independently when stale settings bypass the loader.
    forged = loaded.model_copy(
        update={
            "relay_url": stale_settings().relay_url,
            "relay_token": stale_settings().relay_token,
        }
    )
    providers = build_providers(forged)
    assert set(providers) == {"openai", "anthropic", "gemini"}
    assert {key for _, key, _ in records} == {
        "synthetic-customer-openai",
        "synthetic-customer-anthropic",
        "synthetic-customer-gemini",
    }
    assert all(not kwargs for _, _, kwargs in records)  # No owner relay base URL.


def test_fresh_customer_identity_cannot_inherit_prior_customer_keys(
    customer, isolated, monkeypatch
):
    first_keys = KeyringStore()
    first_keys.set("openai", "synthetic-first-account")
    first_service = first_keys._service
    customer.revoke()
    second = AccountContext(customer.root, customer.issuer, "22222222-2222-4222-8222-222222222222")
    # Simulate the required full-process account switch, never a production reset API.
    monkeypatch.setattr(context_module, "_context", second)
    assert load_settings().openai_api_key is None
    second_keys = KeyringStore()
    second_keys.set("openai", "synthetic-second-account")
    assert load_settings().openai_api_key.get_secret_value() == "synthetic-second-account"
    assert isolated.vault[(first_service, "openai")] == "synthetic-first-account"
    assert first_service != second_keys._service
    with pytest.raises(PermissionError):
        first_keys.get("openai")


@pytest.mark.parametrize("tier", ["fast", "smart"])
async def test_custom_routes_and_injected_maps_cannot_use_relay(customer, tier):
    own, relay = Script("openai", ["Own answer"]), Script("relay", ["Owner billed"])
    providers = {"openai": own, "relay": relay, "unknown": relay}
    settings = stale_settings(
        router_fast="relay:fast,openai:own-fast",
        router_smart="unknown:billing,relay:smart,openai:own-smart",
    )
    router = build_router(settings, providers)
    providers["relay"] = relay  # Mutating the caller's map cannot restore a preferred bypass.
    assert [route.provider for route in router.chain_for(tier)] == ["openai"]
    result = await collect_routed(router, REQ, tier)
    assert result.text == "Own answer"
    assert relay.calls == []
    for blocked in ("relay", "unknown"):
        with pytest.raises(NoRoutesAvailable):
            _ = [
                event async for event in router.stream(REQ, tier, preferred=Route(blocked, "smart"))
            ]
    assert relay.calls == [] and len(own.calls) == 1


@pytest.mark.parametrize("tier", ["fast", "smart"])
async def test_stale_relay_only_chain_uses_own_presets_and_missing_key_fails_closed(customer, tier):
    settings = stale_settings(router_fast="relay:fast", router_smart="relay:smart")
    own, relay = Script("openai", ["Own preset"]), Script("relay", ["Must not run"])
    router = build_router(settings, {"openai": own, "relay": relay})
    assert all(
        route.provider != "relay" for chain in default_chains(settings).values() for route in chain
    )
    assert (await collect_routed(router, REQ, tier)).text == "Own preset"
    empty = build_router(settings, {"relay": relay})
    with pytest.raises(NoRoutesAvailable):
        await collect_routed(empty, REQ, tier)
    assert relay.calls == []


@pytest.mark.parametrize("kind", [ErrorKind.AUTH, ErrorKind.RATE_LIMIT, ErrorKind.NETWORK])
async def test_key_failure_never_falls_back_to_owner_billing(customer, kind):
    broken = Script("openai", [LLMError("openai", kind, "Synthetic failure")])
    relay = Script("relay", ["Owner billed"])
    router = build_router(
        stale_settings(router_fast="openai:own,relay:fast"), {"openai": broken, "relay": relay}
    )
    with pytest.raises(AllRoutesFailed):
        await collect_routed(router, REQ)
    assert broken.calls == ["own"] and relay.calls == []


async def test_fallback_uses_another_customer_provider(customer):
    first = Script("openai", [LLMError("openai", ErrorKind.RATE_LIMIT, "Synthetic quota")])
    second, relay = Script("gemini", ["Own fallback"]), Script("relay", ["Owner billed"])
    router = build_router(
        stale_settings(router_fast="openai:own,relay:fast,gemini:own-fallback"),
        {"openai": first, "gemini": second, "relay": relay},
    )
    result = await collect_routed(router, REQ)
    assert result.provider == "gemini" and relay.calls == []


def customer_dialog(qtbot, monkeypatch, *, checker=None):
    monkeypatch.setattr("interview_assistant.ui.settings_dialog.RelayPanel", forbidden_constructor)
    monkeypatch.setattr(
        "interview_assistant.ui.settings_dialog.KeyringRelayTokenStore", forbidden_constructor
    )
    dialog = SettingsDialog(
        UserSettingsStore(),
        KeyringStore(),
        checker=checker or forbidden_constructor,
        runner=run_inline,
        relay_store=ForbiddenRelay(),
        relay_request_fn=forbidden_constructor,
        relay_status_fn=forbidden_constructor,
    )
    qtbot.addWidget(dialog)
    return dialog


def test_customer_settings_excludes_shared_access_and_normalizes_old_chains(
    customer, qtbot, monkeypatch
):
    store = UserSettingsStore()
    store.save(
        {
            "router_fast": "relay:fast",
            "router_smart": "relay:smart,openai:own",
            "relay_url": "https://old.example",
            "relay_pending_id": "old",
        }
    )
    before = store.path.read_bytes()
    dialog = customer_dialog(qtbot, monkeypatch)
    assert dialog.relay_panel is None
    assert "Shared access" not in [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
    assert dialog.router_fast.text() == "" and dialog.router_smart.text() == "openai:own"
    assert store.path.read_bytes() == before  # Opening settings never migrates or deletes secrets.
    assert any("billed" in label.text() for label in dialog.findChildren(QLabel))
    dialog.router_fast.setText("relay:fast")
    with pytest.raises(ValueError, match="Shared access routes are unavailable"):
        dialog.collect()


def test_key_save_verify_replace_remove_is_scoped_and_models_follow(
    customer, isolated, qtbot, monkeypatch
):
    checked = []

    def checker(provider, key):
        checked.append((provider, key))
        return CheckResult(True, "Authentication verified", ["gpt-5.4-mini", "gpt-5.4"])

    dialog = customer_dialog(qtbot, monkeypatch, checker=checker)
    row = dialog.key_rows["openai"]
    assert not checked and load_settings().openai_api_key is None
    row.edit.setText("synthetic-customer-key-one")
    row.test_btn.click()
    row.save_btn.click()
    assert checked == [("openai", "synthetic-customer-key-one")]
    assert dialog.catalog.status("openai")[0] == "verified"
    assert load_settings().openai_api_key.get_secret_value() == "synthetic-customer-key-one"
    picker = ModelSelector(["relay:fast"])
    qtbot.addWidget(picker)
    picker.bind(dialog.catalog, runtime_keys={"openai": fingerprint("synthetic-customer-key-one")})
    assert picker.shared_routes == [] and not picker.provider_ready("relay")
    assert picker.findData("openai:gpt-5.4-mini") >= 0
    row.edit.setText("synthetic-customer-key-two")
    row.save_btn.click()
    assert dialog.catalog.status("openai")[0] == "unverified"
    assert not picker.provider_ready("openai")
    assert load_settings().openai_api_key.get_secret_value() == "synthetic-customer-key-two"
    row.test_btn.click()
    assert dialog.catalog.status("openai")[0] == "verified"
    row.remove_btn.click()
    assert load_settings().openai_api_key is None
    assert dialog.catalog.status("openai")[0] == "missing"
    assert picker.findData("openai:gpt-5.4-mini") == -1
    assert all(service.endswith(customer.credential_namespace) for service, _ in isolated.reads)
    assert "synthetic-customer-key" not in dialog._store.path.read_text()


@pytest.mark.parametrize("kind,expected", [("invalid", "invalid"), ("quota", "unverified")])
def test_failed_explicit_key_check_does_not_offer_models(
    customer, qtbot, monkeypatch, kind, expected
):
    keys = KeyringStore()
    keys.set("openai", "synthetic-rejected-key")
    dialog = customer_dialog(
        qtbot, monkeypatch, checker=lambda *_: CheckResult(False, "Synthetic failure", kind=kind)
    )
    dialog.key_rows["openai"].test_btn.click()
    assert dialog.catalog.status("openai")[0] == expected
    assert dialog.catalog.options("openai") == []


def test_onboarding_explains_billing_without_automatic_key_calls(customer, qtbot):
    page = ProvidersPage(KeyringStore(), Settings, forbidden_constructor, run_inline)
    qtbot.addWidget(page)
    assert "own provider API keys" in page.subTitle()
    assert "Providers bill your provider account" in page.subTitle()
    assert not page.isComplete()
    page.skip.setChecked(True)
    assert page.isComplete()  # App exploration is allowed, paid calls are not forced.


def test_audio_cloud_and_fallback_use_only_customer_scoped_credentials(
    customer, isolated, monkeypatch
):
    from interview_assistant.stt.factory import build_provider

    records = []

    def openai(key, **kwargs):
        records.append(("openai", key))
        return SimpleNamespace(name="openai")

    def speechmatics(key, **kwargs):
        records.append(("speechmatics", key, kwargs["fallback"].name))
        return SimpleNamespace(name="speechmatics")

    monkeypatch.setattr("interview_assistant.stt.openai_stt.OpenAISTTProvider", openai)
    monkeypatch.setattr("interview_assistant.stt.speechmatics.SpeechmaticsProvider", speechmatics)
    monkeypatch.setattr(
        "interview_assistant.stt.local_whisper.FasterWhisperProvider",
        lambda *args, **kwargs: SimpleNamespace(name="local"),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-owner-must-not-fallback")
    keys = KeyringStore()
    keys.set("speechmatics", "synthetic-customer-speech")
    UserSettingsStore().save({"stt_provider": "speechmatics"})
    assert build_provider(load_settings()).name == "speechmatics"
    assert records == [("speechmatics", "synthetic-customer-speech", "local")]
    keys.set("openai", "synthetic-customer-openai")
    records.clear()
    build_provider(load_settings())
    assert records == [
        ("openai", "synthetic-customer-openai"),
        ("speechmatics", "synthetic-customer-speech", "openai"),
    ]


def test_revoked_customer_cannot_fall_back_to_personal_factories(customer, monkeypatch):
    settings = stale_settings(openai_api_key="synthetic-own")
    monkeypatch.setattr("interview_assistant.llm.factory.OpenAIProvider", forbidden_constructor)
    customer.revoke()
    for operation in (
        lambda: build_providers(settings),
        lambda: default_chains(settings),
        lambda: build_router(settings, {"relay": Script("relay", ["no"])}),
        lambda: load_settings(MemoryKeyStore(), ForbiddenRelay()),
    ):
        with pytest.raises(PermissionError):
            operation()


async def test_personal_shared_relay_is_preserved(isolated, monkeypatch, qtbot):
    monkeypatch.setenv("RELAY_URL", "https://personal-relay.example")
    settings = load_settings(MemoryKeyStore(), MemoryRelayTokenStore("synthetic-personal-token"))
    assert settings.relay_token.get_secret_value() == "synthetic-personal-token"
    records = spy_factories(monkeypatch)
    providers = build_providers(settings)
    assert set(providers) == {"relay"}
    assert records == [
        (
            "openai",
            "synthetic-personal-token",
            {"base_url": "https://personal-relay.example/v1/chat/completions"},
        )
    ]
    relay = Script("relay", ["Shared answer"])
    assert (
        await collect_routed(build_router(settings, {"relay": relay}), REQ)
    ).text == "Shared answer"
    dialog = SettingsDialog(
        UserSettingsStore(),
        MemoryKeyStore(),
        runner=run_inline,
        relay_store=MemoryRelayTokenStore(),
        checker=forbidden_constructor,
    )
    qtbot.addWidget(dialog)
    assert dialog.relay_panel is not None
    assert "Shared access" in [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())]
    picker = ModelSelector(["relay:fast"])
    qtbot.addWidget(picker)
    assert picker.findData("relay:fast") >= 0 and picker.provider_ready("relay")
