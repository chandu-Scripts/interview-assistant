"""Offline provisioning safety checks; no native prompt, live Auth or existing accounts."""

import ast
import json
import logging
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from uuid import UUID

import httpx
import pytest
from scripts import provision_customer_access_fixtures as provisioning
from scripts.verify_customer_access import Fixtures

SECRET = "sb_secret_synthetic_never_print_this_key"
PUBLIC = provisioning.PublicConfig(provisioning.DEV_URL, "sb_publishable_synthetic")
IDS = [str(UUID(int=value)) for value in (101, 102, 103)]
OUTSIDER = str(UUID(int=999))


@pytest.fixture
def output(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(
        provisioning.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0),
    )
    return tmp_path / "fixtures.json"


class Backend:
    def __init__(self, failure: str = "") -> None:
        self.failure = failure
        self.requests: list[httpx.Request] = []
        self.users: dict[str, dict[str, Any]] = {OUTSIDER: {"email": "existing@example.test"}}
        self.posts: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        logging.getLogger("httpx").warning("Synthetic sensitive transport log: %s", SECRET)
        self.requests.append(request)
        assert str(request.url).startswith(provisioning.DEV_URL + "/auth/v1/admin/users")
        assert request.headers["apikey"] == SECRET
        assert "authorization" not in request.headers
        if request.method == "POST":
            assert request.url.path == "/auth/v1/admin/users"
            payload = json.loads(request.content)
            assert set(payload) == {"email", "password", "email_confirm"}
            assert payload["email_confirm"] is True
            self.posts.append(payload)
            number = len(self.posts)
            if number == 2:
                if self.failure == "duplicate":
                    return httpx.Response(422, json={"code": "email_exists", "message": SECRET})
                if self.failure == "timeout":
                    raise httpx.ReadTimeout(SECRET, request=request)
                if self.failure == "cancel":
                    raise KeyboardInterrupt(SECRET)
                if self.failure == "redirect":
                    return httpx.Response(302, headers={"location": "https://other.test/users"})
                if self.failure == "malformed":
                    return httpx.Response(200, text=SECRET)
                if self.failure == "mismatch":
                    return httpx.Response(200, json={"id": OUTSIDER, "email": "other@test.test"})
                if self.failure in {"delete_failure", "delete_noop", "generic_404", "write"}:
                    if self.failure != "write":
                        return httpx.Response(422, json={"message": SECRET})
            uid = IDS[number - 1]
            user = {
                "id": uid,
                "email": payload["email"],
                "email_confirmed_at": "2026-11-01T00:00:00Z",
            }
            self.users[uid] = user
            return httpx.Response(200, json=user)
        uid = request.url.path.rsplit("/", 1)[-1]
        assert uid in IDS and uid != OUTSIDER
        if request.method == "DELETE":
            self.deleted.append(uid)
            if self.failure == "delete_failure":
                return httpx.Response(500, text=SECRET)
            if self.failure != "delete_noop":
                self.users.pop(uid, None)
            return httpx.Response(200, json={})
        assert request.method == "GET"
        if self.failure == "generic_404":
            return httpx.Response(404, json={"message": SECRET})
        return (
            httpx.Response(200, json=self.users[uid])
            if uid in self.users
            else httpx.Response(404, json={"code": "user_not_found"})
        )

    def run(self, output: Path, prompt=None) -> provisioning.Result:
        with httpx.Client(
            transport=httpx.MockTransport(self.handle), follow_redirects=True
        ) as client:
            return provisioning.provision(
                PUBLIC, output, client, secret_prompt=prompt or (lambda: SECRET)
            )


def assert_no_secrets(result, backend, capsys, caplog) -> None:
    captured = capsys.readouterr()
    public_text = json.dumps(result.public()) + repr(result) + captured.out + captured.err
    assert SECRET not in public_text + caplog.text
    for post in backend.posts:
        assert post["email"] not in public_text
        assert post["password"] not in public_text


def test_exactly_three_creates_save_runner_contract_and_id_only_journal(
    output, capsys, caplog
) -> None:
    backend = Backend()
    result = backend.run(output)
    assert result.public()["passed"] and not result.public()["admin_role_assigned"]
    assert len(backend.posts) == 3 and not backend.deleted
    assert [r.method for r in backend.requests] == ["POST"] * 3
    raw = json.loads(output.read_text(encoding="utf-8"))
    fixtures = Fixtures.parse(raw)
    assert [item.user_id for item in (fixtures.admin, *fixtures.customers)] == IDS
    assert len({post["email"] for post in backend.posts}) == 3
    for post in backend.posts:
        assert post["email"].endswith("@example.test")
        password = post["password"]
        assert len(password) >= 44
        assert all(
            any(check(c) for c in password) for check in (str.isupper, str.islower, str.isdigit)
        )
        assert "!" in password
    journal = output.with_name("fixtures.created-ids.jsonl").read_text(encoding="utf-8")
    assert [json.loads(line) for line in journal.splitlines()] == IDS
    assert SECRET not in output.read_text(encoding="utf-8") + journal
    assert OUTSIDER in backend.users
    assert_no_secrets(result, backend, capsys, caplog)


@pytest.mark.parametrize(
    "failure", ["duplicate", "timeout", "cancel", "redirect", "malformed", "mismatch"]
)
def test_partial_failure_only_rolls_back_validated_current_ids(
    output, failure, capsys, caplog
) -> None:
    backend = Backend(failure)
    result = backend.run(output)
    assert not result.public()["passed"] and not output.exists()
    assert backend.deleted == [IDS[0]] and result.rolled_back_ids == [IDS[0]]
    assert result.created_user_ids == {"admin": IDS[0]}
    assert set(backend.users) == {OUTSIDER}
    if failure == "duplicate":
        assert result.public()["cleanup_ok"]
    else:
        assert result.cleanup_failures == ["unknown_create_requires_manual_reconciliation"]
    assert output.with_name("fixtures.created-ids.jsonl").read_text().strip() == json.dumps(IDS[0])
    assert_no_secrets(result, backend, capsys, caplog)


@pytest.mark.parametrize("failure", ["delete_failure", "delete_noop", "generic_404"])
def test_unconfirmed_rollback_is_explicit(output, failure) -> None:
    backend = Backend(failure)
    result = backend.run(output)
    assert not result.public()["cleanup_ok"]
    assert result.cleanup_failures == ["rollback_unconfirmed:" + IDS[0]]
    assert result.rolled_back_ids == [] and backend.deleted == [IDS[0]]
    assert OUTSIDER in backend.users


def test_final_file_failure_rolls_back_all_three_and_removes_partial_secret_file(
    output, monkeypatch, capsys, caplog
) -> None:
    backend = Backend()

    def failed_write(file, fixtures):
        file.write(json.dumps(fixtures)[:200])
        raise OSError(SECRET)

    monkeypatch.setattr(provisioning, "save_fixtures", failed_write)
    result = backend.run(output)
    assert not result.public()["passed"] and result.public()["cleanup_ok"]
    assert not output.exists() and backend.deleted == IDS[::-1]
    assert result.rolled_back_ids == IDS[::-1] and set(backend.users) == {OUTSIDER}
    assert_no_secrets(result, backend, capsys, caplog)


def test_prompt_cancel_has_no_network_and_removes_both_reservations(output) -> None:
    backend = Backend()

    def cancelled():
        assert output.exists() and output.with_name("fixtures.created-ids.jsonl").exists()
        raise provisioning.Refused("secret_prompt_cancelled")

    result = backend.run(output, cancelled)
    assert result.failures == ["secret_prompt_cancelled"] and not backend.requests
    assert list(output.parent.iterdir()) == []


def test_saved_fixture_validation_failure_rolls_back_all_accounts(output, monkeypatch) -> None:
    monkeypatch.setattr(provisioning, "load_fixtures", lambda _: None)
    backend = Backend()
    result = backend.run(output)
    assert result.failures == ["fixture_contract_verification_failed"]
    assert result.public()["cleanup_ok"] and backend.deleted == IDS[::-1]
    assert not output.exists() and set(backend.users) == {OUTSIDER}


@pytest.mark.parametrize("platform", ["offscreen", "minimal"])
def test_native_prompt_refuses_invisible_platform_without_creating_dialog(
    monkeypatch, platform
) -> None:
    widgets = ModuleType("PySide6.QtWidgets")
    widgets.QApplication = SimpleNamespace(  # type: ignore[attr-defined]
        instance=lambda: SimpleNamespace(), platformName=lambda: platform
    )
    widgets.QInputDialog = lambda: pytest.fail("dialog created")  # type: ignore[attr-defined]
    widgets.QDialog = widgets.QLineEdit = None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "PySide6.QtWidgets", widgets)
    with pytest.raises(provisioning.Refused, match="native_prompt_requires_visible_platform"):
        provisioning.prompt_secret()


@pytest.mark.parametrize(
    "mode", ["existing_output", "existing_journal", "unignored", "suffix", "missing_parent"]
)
def test_output_preflight_refuses_before_prompt_or_network(output, monkeypatch, mode) -> None:
    existing = None
    if mode == "existing_output":
        existing = output
    elif mode == "existing_journal":
        existing = output.with_name("fixtures.created-ids.jsonl")
    elif mode == "unignored":
        monkeypatch.setattr(
            provisioning.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 1)
        )
    elif mode == "suffix":
        output = output.with_suffix(".txt")
    else:
        output = output.parent / "missing" / output.name
    if existing:
        existing.write_text("existing user data")
    backend = Backend()
    result = backend.run(output, lambda: pytest.fail("prompt opened"))
    assert result.failures == ["output_requires_new_ignored_paths"] and not backend.requests
    if existing:
        assert existing.read_text() == "existing user data"


def test_exclusive_open_race_cannot_overwrite_a_new_file(output, monkeypatch) -> None:
    original = provisioning.checked_paths

    def race(path):
        paths = original(path)
        path.write_text("other process reservation")
        return paths

    monkeypatch.setattr(provisioning, "checked_paths", race)
    backend = Backend()
    result = backend.run(output, lambda: pytest.fail("prompt opened"))
    assert not result.public()["passed"] and not backend.requests
    assert output.read_text() == "other process reservation"


@pytest.mark.parametrize("key", ["sb_publishable_not_admin", "eyJlegacy", "bad\n" + SECRET])
def test_bad_secret_rejected_without_saving_it(output, key, capsys, caplog) -> None:
    backend = Backend()
    result = backend.run(output, lambda: key)
    assert result.failures == ["secret_key_format_rejected"]
    assert not backend.requests and not list(output.parent.iterdir())
    assert_no_secrets(result, backend, capsys, caplog)


def test_fixed_public_config_ignores_environment_and_refuses_wrong_host(
    tmp_path, monkeypatch
) -> None:
    config_path = tmp_path / "public.json"
    monkeypatch.setattr(provisioning, "PUBLIC_CONFIG", config_path)
    monkeypatch.setenv("SUPABASE_URL", "https://production.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", SECRET)
    config_path.write_text(
        json.dumps({"url": PUBLIC.url, "publishable_key": PUBLIC.publishable_key})
    )
    assert provisioning.load_public_config() == PUBLIC
    config_path.write_text(
        json.dumps({"url": "https://other.supabase.co", "publishable_key": PUBLIC.publishable_key})
    )
    with pytest.raises(provisioning.Refused, match="public_config_rejected"):
        provisioning.load_public_config()


def test_cli_opt_in_and_unknown_secret_arguments_never_open_prompt_or_echo_secret(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(provisioning, "prompt_secret", lambda: pytest.fail("prompt opened"))
    monkeypatch.setattr(provisioning, "load_public_config", lambda: pytest.fail("read config"))
    assert provisioning.main(["--fixtures", "fixture.json"]) == 1
    assert provisioning.main(["--live", "--fixtures", "fixture.json", "--secret", SECRET]) == 1
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    assert "explicit_live_opt_in_required" in captured.out and "invalid_arguments" in captured.out


def test_packaging_inputs_and_app_imports_exclude_provisioning_helper(
    tmp_path, monkeypatch
) -> None:
    root = Path(__file__).resolve().parents[1]
    hooks = ModuleType("PyInstaller.utils.hooks")
    packages = []

    def collect(package):
        packages.append(package)
        return []

    for name in ("collect_data_files", "collect_dynamic_libs", "collect_submodules"):
        setattr(hooks, name, collect)
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
    semantic = tmp_path / "src/interview_assistant/assets/semantic"
    semantic.mkdir(parents=True)
    (semantic / "model.onnx").touch()
    captured: dict[str, Any] = {}

    def analysis(scripts, **kwargs):
        captured.update(scripts=scripts, **kwargs)
        return SimpleNamespace(scripts=scripts, datas=[], binaries=[], pure=[])

    scope = {
        "SPECPATH": str(tmp_path / "packaging"),
        "Analysis": analysis,
        "PYZ": lambda *a, **k: None,
        "EXE": lambda *a, **k: None,
        "COLLECT": lambda *a, **k: None,
    }
    spec = root / "packaging/interview_assistant.spec"
    exec(compile(spec.read_text(encoding="utf-8"), str(spec), "exec"), scope)
    assert captured["scripts"] == [str(tmp_path / "packaging/launcher.py")]
    assert captured["pathex"] == [str(tmp_path / "src")]
    assert all("scripts" not in package for package in packages)
    assert all("scripts" not in source for source, _ in captured["datas"])
    for source in (root / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.Import):
                assert all("provision_customer_access_fixtures" not in n.name for n in node.names)
            elif isinstance(node, ast.ImportFrom):
                assert "provision_customer_access_fixtures" not in (node.module or "")
                assert all("provision_customer_access_fixtures" not in n.name for n in node.names)
