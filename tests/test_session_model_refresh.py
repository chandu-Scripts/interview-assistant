import pytest

from interview_assistant.config.provider_check import CheckResult
from interview_assistant.ui.bg import run_inline
from interview_assistant.ui.provider_catalog import MODEL_CHOICES, VERIFICATION_TTL
from interview_assistant.ui.provider_picker import ModelSelector
from tests.test_provider_picker import isolated, make, verify  # noqa: F401


def test_expired_list_refresh_updates_both_selectors_and_presets(qtbot, tmp_path):
    dialog, fast, catalog, _ = make(qtbot, tmp_path, initial={"openai": "key"})
    clock = [1000.0]
    catalog.clock = lambda: clock[0]
    verify(catalog)
    clock[0] += VERIFICATION_TTL + 1
    coding = ModelSelector([])
    qtbot.addWidget(coding)
    coding.bind(catalog)
    models = MODEL_CHOICES["openai"]
    calls = []

    def check(provider, key):
        calls.append(provider)
        return CheckResult(True, "Verified", models + ["gpt-4o", "gpt-image-1"], "verified")

    fast.model_checker, fast.model_runner = check, run_inline
    fast.showPopup()
    picker = fast.picker
    assert picker.provider == "openai"
    assert not calls
    assert picker.models.count() == 0
    assert picker.refresh_btn.isVisible()
    picker.refresh_btn.click()
    assert calls == ["openai"]
    assert picker.models.count() == 4
    assert picker.count_label.text() == "4 of 4 available models"
    assert fast.count() == coding.count() == 5
    assert dialog.model_boxes["openai_model"].findText(models[-1]) >= 0
    picker.search.setText(models[-1])
    assert picker.models.count() == 1
    picker.choose("openai:" + models[-1])
    assert fast.currentData() == "openai:" + models[-1]


@pytest.mark.parametrize("change", ["close", "key", "delete", "inactive"])
def test_late_refresh_is_ignored(qtbot, tmp_path, change):
    _, selector, catalog, keys = make(qtbot, tmp_path, initial={"openai": "key"})
    pending = []
    selector.model_runner = lambda work, done: pending.append(done)
    selector.showPopup()
    picker = selector.picker
    picker.refresh_btn.click()
    picker.refresh_btn.click()
    assert len(pending) == 1
    if change == "close":
        picker.reject()
    elif change == "key":
        keys.set("openai", "replacement")
    elif change == "delete":
        keys.delete("openai")
    else:
        from types import SimpleNamespace

        picker._context = SimpleNamespace(active=False)
    pending[0](CheckResult(True, "Verified", ["gpt-late"], "verified"))
    assert catalog.models("openai") == []


@pytest.mark.parametrize(
    "result",
    [
        CheckResult(False, "Network unavailable", kind="network"),
        CheckResult(False, "Key rejected", kind="invalid"),
        RuntimeError("private error must not appear"),
    ],
)
def test_failed_refresh_can_retry_without_unlocking_models(qtbot, tmp_path, result):
    _, selector, catalog, _ = make(qtbot, tmp_path, initial={"openai": "key"})
    verify(catalog)
    selector.model_runner = lambda work, done: done(result)
    selector.showPopup()
    picker = selector.picker
    picker.refresh_btn.click()
    assert picker.refresh_btn.isEnabled()
    assert catalog.models("openai") == []
    assert "Refresh models" in picker.info.text()
    assert "private error" not in picker.info.text()


def test_two_saved_providers_require_explicit_choice(qtbot, tmp_path):
    _, selector, _, _ = make(qtbot, tmp_path, initial={"openai": "key", "gemini": "key2"})
    selector.showPopup()
    picker = selector.picker
    assert not picker.provider
    assert not picker.refresh_btn.isVisible()
    picker.show_provider("openai")
    assert picker.refresh_btn.isVisible()
