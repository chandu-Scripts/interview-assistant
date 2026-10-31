"""Login lifecycle tests use synthetic identities, in-memory credentials and fake I/O."""

import asyncio
import json
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QDialog

from interview_assistant.account import bootstrap, startup
from interview_assistant.account.bootstrap import Bootstrap
from interview_assistant.account.context import AccountContext
from interview_assistant.account.models import AuthCode, AuthError, AuthSession
from interview_assistant.account.runtime import AccountRuntime
from interview_assistant.account.session import SessionService
from interview_assistant.account.vault import RefreshVault
from interview_assistant.ui import bg
from interview_assistant.ui.account_screen import AccountScreen

USER = "11111111-1111-4111-8111-111111111111"
URL = "https://synthetic-project.supabase.co"
PUBLIC_KEY = "sb_publishable_synthetic_test_key"


def session(expires_at=None):
    return AuthSession(USER, expires_at or time.time() + 3600, "test-access", "test-refresh")


class MemoryVault:
    def __init__(self):
        self.saved = None

    def save(self, value):
        self.saved = value

    def load(self):
        return self.saved

    def clear(self):
        self.saved = None


class Provider:
    def __init__(self):
        self.current = session()
        self.login_error = None
        self.refresh_error = None
        self.refresh_count = 0
        self.validations = []
        self.password_changes = []
        self.signouts = []
        self.block_login = False
        self.login_started = threading.Event()
        self.release_login = threading.Event()
        self.closed = False

    async def login(self, email, password):
        self.login_started.set()
        if self.block_login:
            try:
                while not self.release_login.is_set():
                    await asyncio.sleep(0.005)
            except asyncio.CancelledError:
                # A provider that completes after cancellation must still be fenced.
                while not self.release_login.is_set():
                    await asyncio.sleep(0.005)
        if self.login_error:
            raise AuthError(self.login_error, "Sign-in failed. Try again.")
        return self.current

    async def validate(self, candidate):
        self.validations.append(candidate)

    async def signup(self, email, password):
        return None

    async def verify_signup(self, email, code):
        return self.current

    async def recover(self, email):
        return None

    async def verify_recovery(self, email, code):
        return self.current

    async def change_password(self, candidate, password):
        self.password_changes.append((candidate, password))
        await self.signout(candidate)

    async def refresh(self, refresh_token):
        self.refresh_count += 1
        if self.refresh_error:
            raise AuthError(self.refresh_error, "Refresh failed.")
        return self.current

    async def signout(self, candidate):
        self.signouts.append(candidate)

    async def close(self):
        self.closed = True


@pytest.fixture
def runtime(qtbot):
    provider = Provider()
    vault = MemoryVault()
    service = SessionService(provider, vault)
    result = AccountRuntime(service)
    yield result, provider, vault
    provider.release_login.set()
    result.shutdown()


def bind_screen(qtbot, runtime):
    screen = AccountScreen()
    qtbot.addWidget(screen)
    runtime.screen = screen
    screen.submitted.connect(runtime._submit_form)
    screen.stage_changed.connect(runtime._stage_changed)
    screen.show()
    return screen


def click(qtbot, button):
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)


def fill_login(screen):
    screen.email.setText("person@example.test")
    screen.password.setText("synthetic-password")


def test_modal_login_accepts_validated_session_and_starts_refresh(qtbot, runtime):
    manager, provider, _ = runtime
    screen = AccountScreen()
    qtbot.addWidget(screen)
    timeout = QTimer(screen)
    timeout.setSingleShot(True)
    timeout.timeout.connect(screen.reject)
    timeout.start(3000)

    def submit():
        fill_login(screen)
        screen.submit_button.click()

    QTimer.singleShot(0, submit)
    assert manager.authenticate(screen)
    timeout.stop()
    assert manager.service.session == provider.current
    assert provider.validations == [provider.current]
    assert manager.timer.isActive()
    assert manager.screen is None
    assert screen.password.text() == ""


def test_invalid_login_stays_in_dialog_and_can_retry(qtbot, runtime):
    manager, provider, _ = runtime
    provider.login_error = AuthCode.INVALID
    screen = bind_screen(qtbot, manager)
    fill_login(screen)
    click(qtbot, screen.submit_button)
    qtbot.waitUntil(lambda: "Sign-in failed" in screen.message.text())
    assert manager.service.session is None
    assert screen.result() != QDialog.DialogCode.Accepted
    assert screen.submit_button.isEnabled()
    provider.login_error = None
    screen.password.setText("synthetic-password")
    click(qtbot, screen.submit_button)
    qtbot.waitUntil(lambda: screen.result() == QDialog.DialogCode.Accepted)
    assert manager.service.session is not None


def test_missing_restore_does_not_unlock(qtbot, runtime):
    manager, _, _ = runtime
    screen = bind_screen(qtbot, manager)
    screen.set_restore_available(True)
    click(qtbot, screen.restore_button)
    qtbot.waitUntil(lambda: "No remembered" in screen.message.text())
    assert screen.result() != QDialog.DialogCode.Accepted
    assert manager.service.session is None


def test_recovery_reset_requires_new_login_and_never_adopts_recovery(qtbot, runtime):
    manager, provider, vault = runtime
    screen = bind_screen(qtbot, manager)
    screen.email.setText("person@example.test")
    click(qtbot, screen.forgot_button)
    click(qtbot, screen.submit_button)
    qtbot.waitUntil(lambda: screen.stage == "verify_recovery")
    screen.code.setText("12345678")
    click(qtbot, screen.submit_button)
    qtbot.waitUntil(lambda: screen.stage == "reset")
    assert manager.service.session is None
    assert vault.saved is None
    assert screen.result() != QDialog.DialogCode.Accepted
    screen.password.setText("new-synthetic-password")
    screen.confirm_password.setText("new-synthetic-password")
    click(qtbot, screen.submit_button)
    qtbot.waitUntil(lambda: screen.stage == "login")
    assert provider.password_changes == [(provider.current, "new-synthetic-password")]
    assert provider.signouts == [provider.current]
    assert manager.service.session is None
    assert manager._recovery is None
    assert screen.result() != QDialog.DialogCode.Accepted


def test_leaving_reset_discards_recovery_authority(qtbot, runtime):
    manager, provider, _ = runtime
    screen = bind_screen(qtbot, manager)
    manager._recovery = provider.current
    screen.set_stage("reset")
    click(qtbot, screen.back_button)
    assert manager._recovery is None
    assert manager.service.session is None


def test_closing_login_rejects_late_provider_completion(qtbot, runtime):
    manager, provider, vault = runtime
    provider.block_login = True
    screen = AccountScreen()
    qtbot.addWidget(screen)
    poll = QTimer(screen)
    poll.setInterval(5)
    poll.timeout.connect(lambda: screen.reject() if provider.login_started.is_set() else None)
    poll.start()
    timeout = QTimer(screen)
    timeout.setSingleShot(True)
    timeout.timeout.connect(screen.reject)
    timeout.start(3000)

    def submit():
        fill_login(screen)
        screen.remember.setChecked(True)
        screen.submit_button.click()

    QTimer.singleShot(0, submit)
    assert not manager.authenticate(screen)
    poll.stop()
    timeout.stop()
    assert provider.login_started.is_set()
    provider.release_login.set()
    marker = threading.Event()
    manager.runner.call_soon(marker.set)
    qtbot.waitUntil(marker.is_set)
    qtbot.wait(30)
    assert manager.service.session is None
    assert vault.saved is None
    assert screen.result() != QDialog.DialogCode.Accepted
    assert not manager.timer.isActive()


def test_transient_refresh_retries_until_expiry_then_locks(qtbot, runtime):
    manager, provider, _ = runtime
    now = [time.time()]
    manager.clock = lambda: now[0]
    manager.monotonic = lambda: now[0]
    provider.current = session(now[0] + 25)
    manager.runner.submit(manager.service.login("person@example.test", "password")).result(2)
    provider.refresh_error = AuthCode.NETWORK
    locks = []
    manager.locked.connect(locks.append)
    manager.check_session()
    qtbot.waitUntil(lambda: manager._next_retry > manager.monotonic())
    assert not locks
    assert manager.service.session is not None
    assert provider.refresh_count == 1
    manager.check_session()
    assert provider.refresh_count == 1
    now[0] += 3
    manager.check_session()
    qtbot.waitUntil(lambda: provider.refresh_count == 2 and manager._pending is None)
    assert not locks
    now[0] += 30
    manager.check_session()
    assert len(locks) == 1
    assert manager.service.session is None


def test_wall_clock_rollback_cannot_extend_session_lifetime(qtbot, runtime):
    manager, provider, _ = runtime
    wall = [time.time()]
    elapsed = [100.0]
    manager.clock = lambda: wall[0]
    manager.monotonic = lambda: elapsed[0]
    provider.current = session(wall[0] + 120)
    manager.runner.submit(manager.service.login("person@example.test", "password")).result(2)
    manager.check_session()
    assert provider.refresh_count == 0
    wall[0] -= 3600
    elapsed[0] += 121
    with qtbot.waitSignal(manager.locked):
        manager.check_session()
    assert manager.service.session is None


@pytest.mark.parametrize("code", [AuthCode.INVALID, AuthCode.UNCONFIRMED, AuthCode.STORAGE])
def test_unusable_refresh_locks_immediately(qtbot, runtime, code):
    manager, provider, _ = runtime
    provider.current = session(time.time() + 25)
    manager.runner.submit(manager.service.login("person@example.test", "password")).result(2)
    provider.refresh_error = code
    with qtbot.waitSignal(manager.locked):
        manager.check_session()
    assert manager.service.session is None


def test_successful_refresh_updates_session_without_lock(qtbot, runtime):
    manager, provider, _ = runtime
    provider.current = session(time.time() + 25)
    manager.runner.submit(manager.service.login("person@example.test", "password")).result(2)
    provider.current = session(time.time() + 3600)
    locks = []
    manager.locked.connect(locks.append)
    manager.check_session()
    qtbot.waitUntil(lambda: manager.service.session == provider.current)
    assert not locks


def test_incognito_disables_remembering_before_async_cleanup_runs(qtbot, runtime):
    manager, _, vault = runtime
    manager.runner.submit(
        manager.service.login("person@example.test", "password", remember=True)
    ).result(2)
    entered = threading.Event()
    release = threading.Event()

    def block_loop():
        entered.set()
        release.wait(2)

    manager.runner.call_soon(block_loop)
    assert entered.wait(2)
    generation = manager.service._remember_generation
    try:
        manager.forget_remembered()
        assert manager.service._remember is False
        assert manager.service._remember_generation > generation
        assert vault.saved is not None  # Async cleanup has not run yet.
    finally:
        release.set()
    qtbot.waitUntil(lambda: vault.saved is None)
    assert manager.service.session is not None
    assert manager.lock_message is None


def test_incognito_cleanup_failure_locks_and_latches_once(qtbot, runtime, monkeypatch):
    manager, _, vault = runtime
    manager.runner.submit(
        manager.service.login("person@example.test", "password", remember=True)
    ).result(2)

    def fail_clear():
        raise AuthError(AuthCode.STORAGE, "Synthetic credential failure")

    monkeypatch.setattr(vault, "clear", fail_clear)
    locks = []
    manager.locked.connect(locks.append)
    with qtbot.waitSignal(manager.locked):
        manager.forget_remembered()
    assert manager.service.session is None
    assert manager.service._remember is False
    assert "Incognito" in manager.lock_message
    manager.check_session()
    assert len(locks) == 1


@pytest.fixture
def clean_bootstrap(monkeypatch, tmp_path):
    for key in ("CHANDOO_MODE", "SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "argv", ["interview-assistant"])
    monkeypatch.setattr(bootstrap, "__file__", str(tmp_path / "src" / "account" / "bootstrap.py"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "personal"))
    return tmp_path


def test_source_development_is_explicitly_distinct_from_customer(clean_bootstrap, monkeypatch):
    assert not Bootstrap.load().customer
    monkeypatch.setenv("CHANDOO_MODE", "customer")
    assert Bootstrap.load().customer
    monkeypatch.setenv("CHANDOO_MODE", "development")
    monkeypatch.setattr(sys, "argv", ["interview-assistant", "--customer"])
    assert Bootstrap.load().customer


def test_frozen_build_cannot_disable_auth_with_development_mode(clean_bootstrap, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True)
    monkeypatch.setenv("CHANDOO_MODE", "development")
    loaded = Bootstrap.load()
    assert loaded.customer
    with pytest.raises(ValueError):
        loaded.validate()


@pytest.mark.parametrize("frozen", [True, False])
def test_packaged_project_ignores_environment_redirect_but_source_allows_it(
    clean_bootstrap, monkeypatch, frozen
):
    assets = clean_bootstrap / "src" / "assets"
    assets.mkdir(parents=True)
    (assets / "auth.json").write_text(
        json.dumps({"url": URL, "publishable_key": PUBLIC_KEY}), encoding="utf-8"
    )
    override_url = "https://different-project.supabase.co"
    override_key = "sb_publishable_different_project"
    monkeypatch.setenv("SUPABASE_URL", override_url)
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", override_key)
    monkeypatch.setattr(sys, "frozen", frozen)
    loaded = Bootstrap.load()
    loaded.validate()
    assert loaded.url == (URL if frozen else override_url)
    assert loaded.publishable_key == (PUBLIC_KEY if frozen else override_key)
    if frozen:
        assert loaded.customer


def test_corrupt_public_config_stays_customer_and_does_not_read_legacy(
    clean_bootstrap, monkeypatch
):
    assets = clean_bootstrap / "src" / "assets"
    assets.mkdir(parents=True)
    (assets / "auth.json").write_text("not-json", encoding="utf-8")
    root = clean_bootstrap / "personal"
    root.mkdir()
    legacy = root / "settings.json"
    legacy.write_text('{"customer": false, "private": "untouched"}', encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True)
    loaded = Bootstrap.load()
    assert loaded.customer
    with pytest.raises(ValueError):
        loaded.validate()
    assert legacy.read_text(encoding="utf-8") == '{"customer": false, "private": "untouched"}'
    assert list(root.iterdir()) == [legacy]


@pytest.mark.parametrize(
    "url,key",
    [
        ("http://project.supabase.co", PUBLIC_KEY),
        ("https://project.supabase.co.attacker.test", PUBLIC_KEY),
        ("https://user:pass@project.supabase.co", PUBLIC_KEY),
        ("https://project.supabase.co/path", PUBLIC_KEY),
        ("https://project.supabase.co:invalid", PUBLIC_KEY),
        (URL, "sb_secret_do_not_ship"),
    ],
)
def test_bad_customer_configuration_rejected(tmp_path, url, key):
    with pytest.raises(ValueError):
        Bootstrap(tmp_path, True, url, key).validate()


class FakeScreen:
    def __init__(self):
        self.messages = []
        self.busy = False
        self.deleted = False

    def set_busy(self, value):
        self.busy = value

    def show_message(self, text, error=False):
        self.messages.append((text, error))

    def exec(self):
        return QDialog.DialogCode.Rejected

    def deleteLater(self):
        self.deleted = True


def test_unconfigured_startup_never_constructs_provider_or_workspace(qtbot, monkeypatch, tmp_path):
    screen = FakeScreen()
    monkeypatch.setattr(startup, "AccountScreen", lambda: screen)

    def forbidden(*args, **kwargs):
        pytest.fail("Unconfigured login tried to open a provider or workspace")

    monkeypatch.setattr(startup, "SupabaseAuthProvider", forbidden)
    monkeypatch.setattr(startup, "activate_context", forbidden)
    instance = SimpleNamespace(on_raise=None)
    assert startup.authenticate(Bootstrap(tmp_path, True), instance) is None
    assert screen.busy
    assert screen.deleted
    assert "not configured" in screen.messages[-1][0]
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("accepted", [False, True])
def test_startup_uses_valid_vault_namespace_and_only_activates_verified_account(
    qtbot, monkeypatch, tmp_path, accepted
):
    screen = FakeScreen()
    monkeypatch.setattr(startup, "AccountScreen", lambda: screen)
    monkeypatch.setattr(startup, "SupabaseAuthProvider", lambda *args: Provider())
    actual_vault = RefreshVault
    backend = SimpleNamespace(
        get_password=lambda *args: None,
        set_password=lambda *args: None,
        delete_password=lambda *args: None,
    )
    monkeypatch.setattr(
        startup, "RefreshVault", lambda project, path: actual_vault(project, path, backend=backend)
    )
    activated = []
    monkeypatch.setattr(startup, "activate_context", activated.append)

    class Runtime(QObject):
        locked = Signal(str)

        def __init__(self, service, **kwargs):
            super().__init__()
            self.service = SimpleNamespace(session=session() if accepted else None)
            self.closed = False

        def authenticate(self, candidate):
            assert candidate is screen
            return accepted

        def shutdown(self):
            self.closed = True

    monkeypatch.setattr(startup, "AccountRuntime", Runtime)
    result = startup.authenticate(Bootstrap(tmp_path, True, URL, PUBLIC_KEY), SimpleNamespace())
    if accepted:
        assert result is not None
        assert [item.user_id for item in activated] == [USER]
        assert activated[0].account_dir != tmp_path
    else:
        assert result is None
        assert not activated
    assert screen.deleted
    assert not list(tmp_path.iterdir())


def test_lock_during_onboarding_revokes_context_before_workspace_receiver_exists(
    qtbot, monkeypatch, tmp_path, runtime
):
    manager, _, vault = runtime
    manager.runner.submit(manager.service.login("person@example.test", "password")).result(2)
    screen = FakeScreen()
    monkeypatch.setattr(startup, "AccountScreen", lambda: screen)
    monkeypatch.setattr(startup, "SupabaseAuthProvider", lambda *args: Provider())
    monkeypatch.setattr(startup, "RefreshVault", lambda *args: vault)
    monkeypatch.setattr(startup, "AccountRuntime", lambda _, **kwargs: manager)
    monkeypatch.setattr(manager, "authenticate", lambda _: True)
    activated = []
    monkeypatch.setattr(startup, "activate_context", activated.append)
    result = startup.authenticate(Bootstrap(tmp_path, True, URL, PUBLIC_KEY), SimpleNamespace())
    assert result is manager
    assert len(activated) == 1
    assert activated[0].active
    onboarding = QDialog()
    qtbot.addWidget(onboarding)
    QTimer.singleShot(0, lambda: manager._lock("Synthetic session expired during setup"))
    # The startup receiver must close the modal wizard without a workspace existing yet.
    deadline = QTimer(onboarding)
    deadline.setSingleShot(True)
    deadline.timeout.connect(onboarding.accept)
    deadline.start(3000)
    assert onboarding.exec() == QDialog.DialogCode.Rejected
    deadline.stop()
    assert not activated[0].active
    assert manager.lock_message == "Synthetic session expired during setup"
    manager.check_session()
    assert not activated[0].active
    assert manager.lock_message == "Synthetic session expired during setup"
    assert manager.service.session is None


def test_bg_completion_after_account_revocation_never_reaches_ui(qtbot, monkeypatch, tmp_path):
    context = AccountContext(tmp_path, URL, USER)
    monkeypatch.setattr(bg, "current_context", lambda: context)
    started = threading.Event()
    release = threading.Event()
    delivered = []
    before = len(bg._alive)

    def work():
        started.set()
        assert release.wait(2)
        return "synthetic extracted resume"

    try:
        bg.run_in_thread(work, delivered.append)
        qtbot.waitUntil(started.is_set)
        context.revoke()
    finally:
        release.set()
    qtbot.waitUntil(lambda: len(bg._alive) == before)
    assert delivered == []


def test_bg_active_account_receives_completion(qtbot, monkeypatch, tmp_path):
    context = AccountContext(tmp_path, URL, USER)
    monkeypatch.setattr(bg, "current_context", lambda: context)
    delivered = []
    bg.run_in_thread(lambda: "synthetic result", delivered.append)
    qtbot.waitUntil(lambda: bool(delivered))
    assert delivered == ["synthetic result"]


def test_bg_already_revoked_account_never_runs_work(qtbot, monkeypatch, tmp_path):
    context = AccountContext(tmp_path, URL, USER)
    context.revoke()
    monkeypatch.setattr(bg, "current_context", lambda: context)
    called = []
    before = len(bg._alive)
    bg.run_in_thread(lambda: called.append("work"), lambda _: called.append("completion"))
    qtbot.waitUntil(lambda: len(bg._alive) == before)
    assert called == []
