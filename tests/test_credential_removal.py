import keyring
import pytest

from interview_assistant.config.credential_ops import CredentialRemovalError
from interview_assistant.config.keys import KeyringStore, KeyStatus
from interview_assistant.config.relay import KeyringRelayTokenStore
from interview_assistant.config.settings import Settings
from interview_assistant.config.user_settings import UserSettingsStore
from interview_assistant.ui.key_row import KeyRow
from interview_assistant.ui.relay_panel import RelayPanel


class Vault:
    def __init__(self):
        self.value = "synthetic-credential"
        self.reads = 0
        self.fail_read_after = 99
        self.fail_delete = False
        self.noop_delete = False

    def get(self, *_):
        self.reads += 1
        if self.reads > self.fail_read_after:
            raise RuntimeError("synthetic-secret-in-backend-error")
        return self.value

    def delete(self, *_):
        if self.fail_delete:
            raise keyring.errors.PasswordDeleteError("synthetic-secret-in-backend-error")
        if not self.noop_delete:
            self.value = None


@pytest.fixture
def vault(monkeypatch):
    backend = Vault()
    monkeypatch.setattr(keyring, "get_password", backend.get)
    monkeypatch.setattr(keyring, "delete_password", backend.delete)
    return backend


@pytest.mark.parametrize("relay", [False, True])
@pytest.mark.parametrize("failure", ["pre_read", "delete", "post_read", "noop"])
def test_removal_requires_positive_verification(vault, relay, failure):
    if failure == "pre_read":
        vault.fail_read_after = 0
    elif failure == "delete":
        vault.fail_delete = True
        vault.fail_read_after = 1
    elif failure == "post_read":
        vault.fail_read_after = 1
    else:
        vault.noop_delete = True
    store = KeyringRelayTokenStore("isolated-test") if relay else KeyringStore("isolated-test")
    with pytest.raises(CredentialRemovalError) as error:
        store.delete() if relay else store.delete("openai")
    assert "synthetic-secret" not in str(error.value)


@pytest.mark.parametrize("relay", [False, True])
def test_successful_and_already_missing_removal(vault, relay):
    store = KeyringRelayTokenStore("isolated-test") if relay else KeyringStore("isolated-test")
    for _ in range(2):
        store.delete() if relay else store.delete("openai")
        assert vault.value is None


def test_provider_row_never_claims_success_when_delete_and_read_fail(qtbot, vault):
    row = KeyRow(
        "openai", KeyringStore("isolated-test"), lambda: KeyStatus("openai", "keyring", "masked")
    )
    qtbot.addWidget(row)
    changes = []
    row.changed.connect(lambda: changes.append(True))
    vault.fail_delete = True
    vault.fail_read_after = vault.reads + 1
    row.remove()
    assert "Could not remove" in row.result.text()
    assert "Key removed" not in row.result.text()
    assert not changes and vault.value is not None


def test_relay_removal_failure_preserves_pending_state(qtbot, vault, tmp_path):
    settings = UserSettingsStore(tmp_path / "settings.json")
    settings.update({"relay_pending_id": "synthetic-pending"})
    panel = RelayPanel(
        settings,
        KeyringRelayTokenStore("isolated-test"),
        raw_settings=lambda: Settings(_env_file=None),
    )
    qtbot.addWidget(panel)
    changes = []
    panel.connected.connect(lambda: changes.append(True))
    vault.fail_delete = True
    panel.forget_access()
    assert "Could not remove" in panel.status.text()
    assert settings.load()["relay_pending_id"] == "synthetic-pending"
    assert not changes and vault.value is not None
