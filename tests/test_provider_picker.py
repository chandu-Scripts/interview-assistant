"""Provider authentication, shared model choices, lifecycle and native-compatible UI flows."""

import httpx
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication

from interview_assistant.agents.jev import Jev, JevConfig
from interview_assistant.config.keys import MemoryKeyStore
from interview_assistant.config.provider_check import CheckResult, check_key
from interview_assistant.config.relay import MemoryRelayTokenStore
from interview_assistant.config.settings import Settings
from interview_assistant.config.user_settings import UserSettingsStore
from interview_assistant.llm.router import RouteEvent
from interview_assistant.llm.router_factory import build_router
from interview_assistant.ui.bg import run_inline
from interview_assistant.ui.provider_catalog import VERIFICATION_TTL, ProviderCatalog, fingerprint
from interview_assistant.ui.provider_picker import ModelSelector
from interview_assistant.ui.session_dialog import SessionDialog
from interview_assistant.ui.settings_dialog import SettingsDialog
from tests.test_router import Script


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        if name != "data_dir":
            monkeypatch.delenv(name.upper(), raising=False)


class Deferred:
    def __init__(self):
        self.jobs = []

    def __call__(self, work, done):
        self.jobs.append((work, done))

    def finish(self):
        work, done = self.jobs.pop(0)
        done(work())


def make(qtbot, tmp_path, *, initial=None, raw=None, checker=None, runner=run_inline):
    keys = MemoryKeyStore(initial or {})
    raw = raw or (lambda: Settings(_env_file=None))
    catalog = ProviderCatalog(raw, keys, tmp_path / "models.json")
    checker = checker or (
        lambda p, k: CheckResult(
            True, "Verified", ["gpt-4o-mini", "gpt-4o", "gpt-5.4-mini", "gpt-5.4"]
        )
    )
    dialog = SettingsDialog(
        UserSettingsStore(),
        keys,
        raw,
        checker,
        runner,
        MemoryRelayTokenStore(),
        catalog=catalog,
    )
    selector = ModelSelector([])
    selector.bind(catalog)
    qtbot.addWidget(dialog)
    qtbot.addWidget(selector)
    return dialog, selector, catalog, keys


def verify(catalog, provider="openai", models=None):
    catalog.record(
        provider,
        fingerprint(catalog.key(provider)),
        CheckResult(
            True,
            "Verified",
            models or ["gpt-4o-mini", "gpt-4o", "gpt-5.4-mini", "gpt-5.4"],
            "verified",
        ),
    )


def test_saved_key_is_amber_then_verified_green_in_both_sections(qtbot, tmp_path):
    d, selector, catalog, keys = make(qtbot, tmp_path)
    assert d.provider_buttons["openai"].property("providerState") == "missing"
    row = d.key_rows["openai"]
    row.edit.setText("test-key-only")
    row.save_btn.click()
    assert d.provider_buttons["openai"].property("providerState") == "unverified"
    assert selector.count() == 1
    row.test_btn.click()
    assert d.provider_buttons["openai"].property("providerState") == "verified"
    assert selector.findData("openai:gpt-5.4-mini") >= 0
    assert d.model_boxes["openai_model"].findText("gpt-5.4-mini") >= 0
    assert catalog.models("gemini") == []
    assert keys.get("openai") == "test-key-only"


def test_unsaved_test_never_unlocks_provider_but_saving_same_tested_key_does(qtbot, tmp_path):
    d, selector, catalog, _ = make(qtbot, tmp_path)
    row = d.key_rows["openai"]
    row.edit.setText("draft-key")
    row.test()
    assert selector.count() == 1
    assert catalog.status("openai")[0] == "missing"
    row.save()
    assert catalog.status("openai")[0] == "verified"
    assert selector.count() == 3


def test_replacing_and_removing_key_resets_selection_immediately(qtbot, tmp_path):
    d, selector, catalog, _ = make(qtbot, tmp_path, initial={"openai": "first"})
    verify(catalog)
    selector.setCurrentIndex(selector.findData("openai:gpt-5.4-mini"))
    row = d.key_rows["openai"]
    row.edit.setText("second")
    row.save()
    assert catalog.status("openai")[0] == "unverified"
    assert selector.currentData() == "" and selector.count() == 1
    row.test()
    row.remove()
    assert selector.count() == 1
    assert d.provider_buttons["openai"].property("providerState") == "missing"


def test_environment_key_is_tested_instead_of_shadowed_credential(qtbot, tmp_path):
    calls = []

    def checker(provider, key):
        calls.append((provider, key))
        return CheckResult(True, "Verified", ["gpt-5.4-mini"])

    def raw():
        return Settings(_env_file=None, openai_api_key="environment-key")

    d, selector, _, keys = make(
        qtbot,
        tmp_path,
        initial={"openai": "stored-key"},
        raw=raw,
        checker=checker,
    )
    row = d.key_rows["openai"]
    row.test_btn.click()
    assert calls == [("openai", "environment-key")]
    assert selector.findData("openai:gpt-5.4-mini") >= 0
    assert row.edit.isEnabled() and not row.remove_btn.isEnabled()
    row.edit.setText("replacement-key")
    row.test()
    assert calls[-1] == ("openai", "replacement-key")
    row.save()
    assert keys.get("openai") == "replacement-key"
    assert row._key_to_test() == "replacement-key"
    row.remove()
    assert keys.get("openai") is None
    assert selector.count() == 1
    assert row._key_to_test() == ""  # removing the selected key does not reactivate .env


@pytest.mark.parametrize("mutation", ["edit", "save", "remove", "external"])
def test_inflight_result_cannot_verify_a_different_key(qtbot, tmp_path, mutation):
    runner = Deferred()
    d, selector, catalog, keys = make(qtbot, tmp_path, initial={"openai": "first"}, runner=runner)
    row = d.key_rows["openai"]
    row.test()
    row.test()
    assert len(runner.jobs) == 1 and not row.test_btn.isEnabled()
    if mutation in ("edit", "save"):
        row.edit.setText("second")
        if mutation == "save":
            row.save()
    elif mutation == "remove":
        row.remove()
    else:
        keys.set("openai", "second")
    runner.finish()
    assert catalog.status("openai")[0] != "verified"
    assert selector.count() == 1


def test_destroyed_key_widget_ignores_pending_result(qtbot, tmp_path):
    runner = Deferred()
    d, _, _, _ = make(qtbot, tmp_path, initial={"openai": "first"}, runner=runner)
    row = d.key_rows["openai"]
    row.test()
    row.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    runner.finish()  # no Qt access after deletion


@pytest.mark.parametrize(
    "kind,state",
    [
        ("invalid", "invalid"),
        ("network", "unverified"),
        ("quota", "unverified"),
        ("permission", "unverified"),
        ("rate_limit", "unverified"),
    ],
)
def test_failed_retest_revokes_green_and_clears_models(qtbot, tmp_path, kind, state):
    d, selector, catalog, _ = make(
        qtbot,
        tmp_path,
        initial={"openai": "key"},
        checker=lambda p, k: CheckResult(False, "Check failed", kind=kind),
    )
    verify(catalog)
    d.key_rows["openai"].test()
    assert catalog.status("openai")[0] == state and selector.count() == 1
    assert d.provider_buttons["openai"].property("providerState") == state
    again = ProviderCatalog(catalog.raw, catalog.keys, catalog.path)
    assert again.status("openai")[0] != "verified"


def test_cached_verification_is_key_bound_expires_and_contains_no_key(tmp_path):
    clock = [1000.0]
    keys = MemoryKeyStore({"openai": "private-key-never-on-disk"})

    def raw():
        return Settings(_env_file=None)

    path = tmp_path / "cache.json"
    catalog = ProviderCatalog(raw, keys, path, clock=lambda: clock[0])
    verify(catalog)
    assert "private-key-never-on-disk" not in path.read_text()
    restored = ProviderCatalog(raw, keys, path, clock=lambda: clock[0])
    assert restored.status("openai")[0] == "verified"
    keys.set("openai", "new-key")
    assert restored.models("openai") == []
    keys.set("openai", "private-key-never-on-disk")
    clock[0] += VERIFICATION_TTL + 1
    assert restored.status("openai")[0] == "unverified"


def test_incognito_verification_does_not_write_cache(tmp_path):
    keys = MemoryKeyStore({"openai": "key"})
    path = tmp_path / "cache.json"
    catalog = ProviderCatalog(lambda: Settings(_env_file=None), keys, path, can_write=lambda: False)
    verify(catalog)
    assert catalog.models("openai") and not path.exists()


@pytest.mark.parametrize(
    "payload", ["[1]", "broken json", '{"openai": null}', '{"openai": {"checked_at": "bad"}}']
)
def test_corrupt_cache_cannot_unlock_models(tmp_path, payload):
    path = tmp_path / "cache.json"
    path.write_text(payload)
    catalog = ProviderCatalog(
        lambda: Settings(_env_file=None), MemoryKeyStore({"openai": "key"}), path
    )
    assert catalog.models("openai") == []


def test_glass_popup_selects_same_models_and_closes_with_escape(qtbot, tmp_path):
    d, selector, catalog, _ = make(qtbot, tmp_path, initial={"openai": "key", "anthropic": "key2"})
    verify(catalog)
    verify(catalog, "anthropic", ["claude-haiku-4-5-20251001", "claude-sonnet-5"])
    d.show()
    d.provider_buttons["openai"].click()
    popup = d.provider_dialogs["openai"]
    assert popup.isVisible() and popup.width() <= 570
    assert popup.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert d.key_rows["openai"].isVisible()
    assert not d.key_rows["anthropic"].isVisible()
    qtbot.keyClick(popup, Qt.Key.Key_Escape)
    assert not popup.isVisible()
    selector.show()
    selector.showPopup()
    picker = selector.picker
    assert picker.isVisible()
    assert picker.provider_buttons["gemini"].isHidden()
    picker.provider_buttons["openai"].click()
    labels = [picker.models.item(i).text() for i in range(picker.models.count())]
    assert labels == [
        "gpt-5.4-mini · Recommended for Fast",
        "gpt-5.4 · Recommended for Smart",
    ]
    assert picker.search.isHidden()
    item = picker.models.item(0)
    picker.models.itemClicked.emit(item)
    assert selector.currentData() == "openai:gpt-5.4-mini" and not picker.isVisible()


def test_expiry_while_picker_open_prevents_stale_selection(qtbot, tmp_path):
    _, selector, catalog, _ = make(qtbot, tmp_path, initial={"openai": "key"})
    clock = [1000.0]
    catalog.clock = lambda: clock[0]
    verify(catalog)
    selector.showPopup()
    clock[0] += VERIFICATION_TTL + 1
    selector.picker.choose("openai:gpt-5.4-mini")
    assert selector.currentData() == ""
    assert selector.picker.provider_buttons["openai"].isHidden()


def test_changed_key_requires_restart_before_new_model_is_used(qtbot, tmp_path):
    _, selector, catalog, _ = make(qtbot, tmp_path, initial={"openai": "new-key"})
    selector.runtime_keys = {"openai": fingerprint("previous-key")}
    verify(catalog)
    selector.showPopup()
    selector.picker.show_provider("openai")
    assert "Restart" in selector.picker.info.text()
    selector.picker.choose("openai:gpt-5.4-mini")
    assert selector.currentData() == ""
    wizard = SessionDialog()
    qtbot.addWidget(wizard)
    wizard.company.setText("Example")
    wizard.model_override.bind(catalog, runtime_keys={"openai": fingerprint("previous-key")})
    wizard.model_override.setCurrentIndex(wizard.model_override.findData("openai:gpt-5.4-mini"))
    with pytest.raises(ValueError, match="Restart"):
        wizard.collect()


def test_relay_choices_remain_available_without_personal_provider_keys(qtbot, tmp_path):
    _, _, catalog, _ = make(qtbot, tmp_path)
    selector = ModelSelector(["relay:fast", "relay:smart"])
    qtbot.addWidget(selector)
    selector.bind(catalog)
    selector.showPopup()
    selector.picker.choose("relay:smart")
    assert selector.currentData() == "relay:smart"


@pytest.mark.parametrize(
    "provider,status,body,kind",
    [
        ("gemini", 400, {"error": {"details": [{"reason": "API_KEY_INVALID"}]}}, "invalid"),
        ("openai", 429, {"error": {"code": "insufficient_quota"}}, "quota"),
        ("openai", 429, {"error": {"code": "credit_balance_exhausted"}}, "quota"),
        ("openai", 429, {"error": {"code": "rate_limit_exceeded"}}, "rate_limit"),
        ("anthropic", 403, {"error": {"message": "private-secret-value"}}, "permission"),
    ],
)
def test_specific_failures_without_exposing_response_or_key(provider, status, body, kind):
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(status, json=body))
    ) as client:
        result = check_key(provider, "private-secret-value", client)
    assert not result.ok and result.kind == kind
    assert "private-secret-value" not in repr(result)


@pytest.mark.parametrize("provider", ["anthropic", "gemini"])
def test_account_model_pagination(provider):
    requests = []

    def handler(req):
        requests.append(req)
        if provider == "anthropic":
            if len(requests) == 1:
                return httpx.Response(
                    200,
                    json={
                        "data": [{"id": "claude-first"}],
                        "has_more": True,
                        "last_id": "claude-first",
                    },
                )
            assert req.url.params["after_id"] == "claude-first"
            return httpx.Response(200, json={"data": [{"id": "claude-second"}], "has_more": False})
        body = {
            "models": [
                {
                    "name": "models/gemini-" + str(len(requests)),
                    "supportedGenerationMethods": ["generateContent"],
                }
            ]
        }
        if len(requests) == 1:
            body["nextPageToken"] = "page2"
        else:
            assert req.url.params["pageToken"] == "page2"
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = check_key(provider, "test-key", client)
    assert result.ok and len(result.models) == 2 and len(requests) == 2
    assert all("test-key" not in str(r.url) for r in requests)


@pytest.mark.parametrize("body", [[], {}, {"data": [None]}, {"data": "bad"}])
def test_malformed_success_response_never_turns_green(body):
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body))
    ) as client:
        assert not check_key("openai", "key", client).ok


def test_provider_popup_sizing_and_no_unrequested_network(qtbot, tmp_path):
    calls = []
    d, _, catalog, _ = make(
        qtbot, tmp_path, initial={"openai": "key"}, checker=lambda p, k: calls.append(p)
    )
    d.resize(640, 560)
    d.show()
    d.provider_buttons["openai"].click()
    popup = d.provider_dialogs["openai"]
    qtbot.waitUntil(lambda: popup.isVisible())
    QApplication.processEvents()
    assert popup.height() <= 560 and popup.width() <= 570
    assert d.height() <= 560
    assert not calls and catalog.status("openai")[0] == "unverified"
    popup.grab().save(str(tmp_path / "provider-popup.png"))
    d.hide()
    assert not popup.isVisible()


def test_fast_smart_changes_match_preferred_labels_after_saving(qtbot, tmp_path):
    d, selector, catalog, _ = make(qtbot, tmp_path, initial={"openai": "key"})
    verify(catalog)
    d.model_boxes["openai_model"].setCurrentText("gpt-5.4-mini")
    d.save()
    label = selector.itemText(selector.findData("openai:gpt-5.4-mini"))
    assert "Fast" in label
    assert d._store.load()["openai_model"] == "gpt-5.4-mini"
    restored = ProviderCatalog(catalog.raw, catalog.keys, catalog.path)
    assert (
        "gpt-5.4-mini · Recommended for Fast (current Fast)",
        "openai:gpt-5.4-mini",
    ) in restored.options("openai")


def test_recommended_shortlist_filters_cached_and_new_models(qtbot, tmp_path):
    d, selector, catalog, _ = make(qtbot, tmp_path, initial={"openai": "key"})
    verify(
        catalog,
        models=[
            "gpt-5.4-mini",
            "gpt-5.4",
            "gpt-5.4-2026-03-05",
            "gpt-4o",
            "gpt-audio",
            "gpt-image-1",
            "gpt-5.4-pro",
            "gpt-5.5",
        ],
    )
    restored = ProviderCatalog(catalog.raw, catalog.keys, catalog.path)
    assert restored.models("openai") == ["gpt-5.4-mini", "gpt-5.4"]
    assert selector.count() == 3  # Auto and the two recommended models, no duplicate tiers
    for field in ("openai_model", "openai_smart_model"):
        box = d.model_boxes[field]
        assert [box.itemText(i) for i in range(box.count())] == ["gpt-5.4-mini", "gpt-5.4"]
    assert d.model_boxes["openai_model"].currentText() == "gpt-4o-mini"
    assert not d._store.load()  # opening / verifying never rewrites saved presets
    d.recommended_models_btn.click()
    assert not d._store.load()
    assert d.model_boxes["openai_model"].currentText() == "gpt-5.4-mini"
    assert d.model_boxes["openai_smart_model"].currentText() == "gpt-5.4"
    d.save()
    assert d._store.load()["openai_smart_model"] == "gpt-5.4"
    assert Settings(_env_file=None).openai_model == "gpt-5.4-mini"
    assert Settings(_env_file=None).openai_smart_model == "gpt-5.4"


def test_unavailable_recommendations_are_not_unlocked(qtbot, tmp_path):
    d, selector, catalog, _ = make(qtbot, tmp_path, initial={"openai": "key"})
    verify(catalog, models=["gpt-5.4-mini", "gpt-4o"])
    assert selector.findData("openai:gpt-5.4") == -1
    assert not d.recommended_models_btn.isEnabled()
    verify(catalog, models=["gpt-4o"])
    assert selector.count() == 1
    selector.showPopup()
    selector.picker.show_provider("openai")
    assert selector.picker.models.count() == 0
    assert "neither recommended" in selector.picker.info.text()


@pytest.mark.parametrize("model", ["gpt-5.4-mini", "gpt-5.4"])
@pytest.mark.parametrize("question", ["What is a tuple?", "Design a scalable chat system."])
async def test_picker_selection_reaches_answer_even_outside_auto_presets(
    qtbot, tmp_path, model, question
):
    _, _, catalog, _ = make(qtbot, tmp_path, initial={"openai": "test-key"})
    verify(catalog)
    wizard = SessionDialog()
    qtbot.addWidget(wizard)
    wizard.company.setText("Test company")
    wizard.model_override.bind(catalog)
    wizard.model_override.showPopup()
    wizard.model_override.picker.show_provider("openai")
    wizard.model_override.picker.choose(f"openai:{model}")
    details = wizard.collect()
    assert details.fast_model_route == f"openai:{model}"
    provider = Script("openai", ["Test answer from the selected model."])
    router = build_router(catalog.raw(), {"openai": provider})
    jev = Jev(
        router,
        JevConfig(classify_with_llm=False, fast_model_route=details.fast_model_route),
    )
    events = [event async for event in jev.ask(question)]
    text = "".join(event.event.delta for event in events if isinstance(event, RouteEvent))
    assert text == "Test answer from the selected model."
    assert provider.calls == [model]


def test_cache_write_failure_keeps_ui_usable(qtbot, tmp_path):
    _, selector, catalog, _ = make(qtbot, tmp_path, initial={"openai": "key"})
    catalog.path = tmp_path  # a directory cannot be replaced with a JSON file
    verify(catalog)
    assert catalog.persistence_error and selector.findData("openai:gpt-5.4-mini") >= 0


def test_remove_failure_keeps_active_key_and_reports_failure(qtbot, tmp_path):
    d, _, catalog, keys = make(qtbot, tmp_path, initial={"openai": "key"})
    verify(catalog)
    keys.delete = lambda provider: None
    row = d.key_rows["openai"]
    row.remove()
    assert "still in" in row.result.text()
    assert catalog.status("openai")[0] == "verified"
