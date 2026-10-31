"""Full-uninstall account cleanup with synthetic files and fully injected vaults."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from interview_assistant.config.keys import KEY_PROVIDERS, MemoryKeyStore
from interview_assistant.purge import _account_credentials, _checked_target, purge

NS = "a" * 32
USER = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


def account_tree(root):
    account = root / "accounts" / NS / USER
    account.mkdir(parents=True)
    (account / "history.db").write_bytes(b"synthetic history")
    marker = root / "auth" / NS / "signed-out.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"active": False, "user_id": OTHER}))
    return account


@pytest.fixture(autouse=True)
def forbid_real_credentials(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("A purge test attempted to access real credentials")

    monkeypatch.setattr("keyring.get_password", fail)
    monkeypatch.setattr("keyring.set_password", fail)
    monkeypatch.setattr("keyring.delete_password", fail)


def test_scoped_credentials_are_removed_before_their_inventory(tmp_path):
    root = tmp_path / "app"
    account = account_tree(root)
    cleaned = []

    def cleanup(service, username):
        assert account.exists()
        cleaned.append((service, username))

    report = purge(root, MemoryKeyStore(), [], credential_cleanup=cleanup)
    assert report.data_dir_removed and not root.exists()
    assert not report.errors
    for provider in KEY_PROVIDERS:
        assert (f"InterviewAssistant:{NS}:{USER}", provider) in cleaned
    assert (f"InterviewAssistantRelay:{NS}:{USER}", "token") in cleaned
    assert (f"InterviewAssistantAuth:{NS}", USER) in cleaned
    assert (f"InterviewAssistantAuth:{NS}", OTHER) in cleaned


def test_injected_legacy_keys_never_enable_real_account_cleanup(tmp_path):
    root = tmp_path / "app"
    account_tree(root)
    keys = MemoryKeyStore({"openai": "synthetic"})
    report = purge(root, keys, [])
    assert not report.errors and report.data_dir_removed
    assert report.keys_removed == ["openai"]


def test_credential_failure_preserves_inventory_for_retry(tmp_path):
    root = tmp_path / "app"
    account = account_tree(root)

    def failed_cleanup(service, username):
        raise RuntimeError("synthetic credential backend unavailable")

    report = purge(root, MemoryKeyStore(), [], credential_cleanup=failed_cleanup)
    assert report.errors and not report.data_dir_removed
    assert account.exists()
    assert (root / "auth" / NS / "signed-out.json").exists()


def test_unknown_names_cannot_select_arbitrary_credential_services(tmp_path):
    root = tmp_path / "app"
    (root / "accounts" / "another-service" / USER).mkdir(parents=True)
    (root / "accounts" / NS / "user@example.test").mkdir(parents=True)
    marker = root / "auth" / "another-service" / "signed-out.json"
    marker.parent.mkdir(parents=True)
    marker.write_text(json.dumps({"active": True, "user_id": USER}))
    assert _account_credentials(root) == set()


def test_malformed_marker_does_not_disappear_before_it_can_be_repaired(tmp_path):
    root = tmp_path / "app"
    account_tree(root)
    marker = root / "auth" / NS / "signed-out.json"
    marker.write_text("not-json")
    called = []
    report = purge(root, MemoryKeyStore(), [], credential_cleanup=lambda *args: called.append(args))
    assert report.errors and marker.exists() and not called


def test_protected_root_is_rejected_without_deleting_anything(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        _checked_target(tmp_path)
    with pytest.raises(ValueError):
        _checked_target(Path(tmp_path.anchor))


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows junction regression")
def test_junction_cannot_remove_any_outside_files(tmp_path):
    root = tmp_path / "app"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    sentinel = outside / "keep.txt"
    sentinel.write_text("synthetic outside file")
    junction = root / "redirected"
    result = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    called = []
    report = purge(root, MemoryKeyStore(), [], credential_cleanup=lambda *args: called.append(args))
    assert report.errors and not called and root.exists()
    assert sentinel.read_text() == "synthetic outside file"
    redirected_report = purge(junction, MemoryKeyStore(), [])
    assert redirected_report.errors and sentinel.exists()
