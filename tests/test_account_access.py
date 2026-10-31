"""Synthetic access/RPC and Qt lifecycle coverage; no live account or credential use."""

import asyncio
import json
import os
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PySide6.QtWidgets import QDialog

from interview_assistant.account import startup
from interview_assistant.account.access import (
    AccessAuthorization,
    AccessLease,
    AccessSnapshot,
    SupabaseAccessProvider,
)
from interview_assistant.account.bootstrap import Bootstrap
from interview_assistant.account.context import AccountContext
from interview_assistant.account.models import AuthCode, AuthError
from interview_assistant.account.runtime import AccountRuntime
from interview_assistant.account.session import SessionService
from tests.test_account_integration import (
    PUBLIC_KEY,
    URL,
    USER,
    FakeScreen,
    MemoryVault,
    Provider,
    bind_screen,
    fill_login,
    session,
)
from tests.test_account_integration import clean_bootstrap as clean_bootstrap


def payload(**overrides):
    return dict(
        user_id=USER,
        allowed=True,
        reason="allowed",
        lease_seconds=60,
        version=1,
        is_admin=False,
        **overrides,
    )


@pytest.mark.parametrize(
    "patch",
    [
        {"user_id": "22222222-2222-4222-8222-222222222222"},
        {"lease_seconds": 61},
        {"lease_seconds": float("nan")},
        {"lease_seconds": True},
        {"allowed": "true"},
        {"reason": "blocked"},
        {"version": -1},
        {"is_admin": "yes"},
    ],
)
def test_snapshot_rejects_invalid_or_foreign_response(patch):
    data = payload()
    data.update(patch)
    with pytest.raises(AuthError):
        AccessSnapshot.parse(data, USER)


async def test_rpc_uses_current_access_token_only_and_does_not_send_identity_or_user_data():
    calls = []

    def handler(request):
        calls.append(request)
        assert str(request.url) == URL + "/rest/v1/rpc/customer_access_snapshot"
        assert request.headers["apikey"] == PUBLIC_KEY
        assert request.headers["authorization"] == "Bearer test-access"
        assert json.loads(request.content) == {}
        return httpx.Response(200, json=payload())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = SupabaseAccessProvider(URL, PUBLIC_KEY, client=client)
        value = await provider.snapshot(session())
    assert value.allowed and value.user_id == USER and len(calls) == 1


@pytest.mark.parametrize(
    "status,code",
    [
        (401, AuthCode.INVALID),
        (403, AuthCode.INVALID),
        (404, AuthCode.CONFIGURATION),
        (429, AuthCode.NETWORK),
        (500, AuthCode.NETWORK),
    ],
)
async def test_rpc_errors_do_not_expose_response_or_tokens(status, code):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                status, json={"message": "private-service-detail test-access"}
            )
        )
    ) as client:
        provider = SupabaseAccessProvider(URL, PUBLIC_KEY, client=client)
        with pytest.raises(AuthError) as error:
            await provider.snapshot(session())
    assert error.value.code == code
    assert "private" not in str(error.value) and "test-access" not in str(error.value)


def test_lease_counts_network_time_and_rejects_stale_identity():
    now = [130.0]
    lease = AccessLease(lambda: now[0])
    current = session()
    result = AccessAuthorization(current, AccessSnapshot.parse(payload(), USER), 100)
    assert lease.adopt(result, current)
    assert lease.deadline == 160 and lease.valid
    now[0] = 161
    assert not lease.valid
    with pytest.raises(AuthError):
        lease.adopt(result, current)
    now[0] = 140
    assert not lease.adopt(result, replace(current, access_token="new-token"))
    lease.clear()
    assert not lease.valid


def test_context_authority_latches_expiry_without_waiting_for_qt_timer(tmp_path):
    clock = [100.0]
    current = session()
    lease = AccessLease(lambda: clock[0])
    lease.adopt(AccessAuthorization(current, AccessSnapshot.parse(payload(), USER), 100), current)
    context = AccountContext(tmp_path, URL, USER, access_valid=lambda: lease.valid)
    with context.mutation():
        assert context.active
    clock[0] = 160
    assert not context.active
    with pytest.raises(PermissionError):
        with context.mutation():
            pytest.fail("Expired authority allowed persistence")
    lease.adopt(AccessAuthorization(current, AccessSnapshot.parse(payload(), USER), 160), current)
    assert lease.valid and not context.active


def test_context_access_predicate_errors_fail_closed(tmp_path):
    def fail():
        raise RuntimeError("synthetic failure")

    context = AccountContext(tmp_path, URL, USER, access_valid=fail)
    assert not context.active


class Access:
    def __init__(self):
        self.value = AccessSnapshot.parse(payload(), USER)
        self.error = None
        self.calls = 0
        self.block = False
        self.started = threading.Event()
        self.release = threading.Event()

    async def snapshot(self, current):
        self.calls += 1
        self.started.set()
        if self.block:
            try:
                while not self.release.is_set():
                    await asyncio.sleep(0.005)
            except asyncio.CancelledError:
                while not self.release.is_set():
                    await asyncio.sleep(0.005)
        if self.error:
            raise self.error
        return self.value

    async def close(self):
        return None


@pytest.fixture
def access_runtime(qtbot):
    clock = [100.0]
    access = Access()
    service = SessionService(Provider(), MemoryVault())
    runtime = AccountRuntime(service, access=access, monotonic=lambda: clock[0])
    screen = bind_screen(qtbot, runtime)
    yield runtime, access, clock, screen
    access.release.set()
    runtime.shutdown()


def sign_in(qtbot, fixture):
    runtime, access, clock, screen = fixture
    fill_login(screen)
    screen.submit_button.click()
    qtbot.waitUntil(lambda: screen.result() == QDialog.DialogCode.Accepted)
    assert runtime.access_lease.valid


def test_enforced_login_waits_for_access_snapshot(access_runtime, qtbot):
    runtime, access, clock, screen = access_runtime
    access.block = True
    fill_login(screen)
    screen.submit_button.click()
    qtbot.waitUntil(access.started.is_set)
    assert screen.result() != QDialog.DialogCode.Accepted
    assert not runtime.access_lease.valid
    access.release.set()
    qtbot.waitUntil(lambda: screen.result() == QDialog.DialogCode.Accepted)
    assert runtime.access_lease.valid


@pytest.mark.parametrize("reason", ["blocked", "no_grant", "expired"])
def test_denied_login_never_unlocks(access_runtime, qtbot, reason):
    runtime, access, clock, screen = access_runtime
    access.value = replace(access.value, allowed=False, reason=reason, lease_seconds=0)
    fill_login(screen)
    screen.submit_button.click()
    qtbot.waitUntil(lambda: "Contact the app owner" in screen.message.text())
    assert screen.result() != QDialog.DialogCode.Accepted
    assert runtime.service.session is None and not runtime.access_lease.valid


def test_network_failure_at_login_cannot_bypass_access(access_runtime, qtbot):
    runtime, access, clock, screen = access_runtime
    access.error = AuthError(AuthCode.NETWORK, "Unavailable")
    fill_login(screen)
    screen.submit_button.click()
    qtbot.waitUntil(lambda: screen.message.text() == "Unavailable")
    assert screen.result() != QDialog.DialogCode.Accepted
    assert runtime.service.session is None


def test_block_during_active_session_locks_and_revokes_authority(access_runtime, qtbot):
    runtime, access, clock, screen = access_runtime
    sign_in(qtbot, access_runtime)
    access.value = replace(access.value, allowed=False, reason="blocked", lease_seconds=0)
    clock[0] = 121
    with qtbot.waitSignal(runtime.locked):
        runtime.check_session()
    assert runtime.service.session is None and not runtime.access_lease.valid
    assert "blocked" in runtime.lock_message


def test_network_outage_cannot_extend_monotonic_lease(access_runtime, qtbot):
    runtime, access, clock, screen = access_runtime
    sign_in(qtbot, access_runtime)
    access.error = AuthError(AuthCode.NETWORK, "Unavailable")
    clock[0] = 121
    runtime.check_session()
    qtbot.waitUntil(lambda: runtime._access_pending is None)
    assert runtime.access_lease.deadline == 160
    assert runtime.service.session is not None
    runtime.clock = lambda: time.time() - 3600
    clock[0] = 160
    with qtbot.waitSignal(runtime.locked):
        runtime.check_session()
    assert runtime.service.session is None


def test_late_success_cannot_revive_expired_lease(access_runtime, qtbot):
    runtime, access, clock, screen = access_runtime
    sign_in(qtbot, access_runtime)
    access.block = True
    access.started.clear()
    clock[0] = 121
    runtime.check_session()
    qtbot.waitUntil(access.started.is_set)
    clock[0] = 161
    with qtbot.waitSignal(runtime.locked):
        access.release.set()
    assert runtime.service.session is None and not runtime.access_lease.valid


def test_logout_fences_access_response_that_ignores_cancellation(access_runtime, qtbot):
    runtime, access, clock, screen = access_runtime
    sign_in(qtbot, access_runtime)
    access.block = True
    access.started.clear()
    clock[0] = 121
    runtime.check_session()
    qtbot.waitUntil(access.started.is_set)
    runtime.invalidate()
    access.release.set()
    qtbot.wait(60)
    assert runtime.service.session is None and not runtime.access_lease.valid


def test_frequent_session_checks_do_not_call_server_per_answer(access_runtime, qtbot):
    runtime, access, clock, screen = access_runtime
    sign_in(qtbot, access_runtime)
    for _ in range(100):
        runtime.check_session()
    assert access.calls == 1


@pytest.mark.parametrize(
    "environment,enforce,valid",
    [
        ("development", False, True),
        ("development", True, True),
        ("production", True, True),
        ("production", False, False),
        ("production", "true", False),
        ("unknown", True, False),
    ],
)
def test_bootstrap_enforcement_is_explicit_and_fail_closed(tmp_path, environment, enforce, valid):
    boot = Bootstrap(tmp_path, True, URL, PUBLIC_KEY, environment, enforce)
    if valid:
        boot.validate()
    else:
        with pytest.raises(ValueError):
            boot.validate()


def test_bundled_production_access_cannot_be_disabled_from_environment(
    clean_bootstrap, monkeypatch
):
    import sys

    assets = clean_bootstrap / "src" / "assets"
    assets.mkdir(parents=True)
    (assets / "auth.json").write_text(
        json.dumps(
            {
                "url": URL,
                "publishable_key": PUBLIC_KEY,
                "environment": "production",
                "enforce_access": True,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "frozen", True)
    monkeypatch.setenv("CHANDOO_ENFORCE_ACCESS", "false")
    boot = Bootstrap.load()
    boot.validate()
    assert boot.enforce_access and boot.environment == "production"


def test_enforced_startup_cannot_activate_context_without_access_lease(
    qtbot, monkeypatch, tmp_path
):
    screen = FakeScreen()
    monkeypatch.setattr(startup, "AccountScreen", lambda: screen)
    monkeypatch.setattr(startup, "SupabaseAuthProvider", lambda *args: Provider())
    monkeypatch.setattr(startup, "SupabaseAccessProvider", lambda *args: Access())
    monkeypatch.setattr(startup, "RefreshVault", lambda *args: MemoryVault())
    activated = []
    monkeypatch.setattr(startup, "activate_context", activated.append)
    manager = SimpleNamespace(
        authenticate=lambda screen: True,
        service=SimpleNamespace(session=session()),
        access_lease=AccessLease(),
        shutdown=lambda: None,
    )
    monkeypatch.setattr(startup, "AccountRuntime", lambda *args, **kwargs: manager)
    boot = Bootstrap(tmp_path, True, URL, PUBLIC_KEY, "production", True)
    assert startup.authenticate(boot, SimpleNamespace()) is None
    assert not activated and not list(tmp_path.iterdir())


@pytest.mark.skipif(
    not os.environ.get("PGLITE_MODULE"), reason="Optional real Postgres engine fixture"
)
def test_postgres_rpc_outputs_pass_through_http_adapter_and_desktop_gate(qtbot, access_runtime):
    project = Path(__file__).parents[1]
    result = subprocess.run(
        ["node", str(project / "supabase/tests/access.test.mjs")],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    responses = json.loads(result.stdout)["snapshots"]
    runtime, previous, clock, screen = access_runtime
    current = replace(runtime.service.provider.current, user_id=responses["approved"]["user_id"])
    runtime.service.provider.current = current
    body = [responses["approved"]]
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body[0]))
    )
    runtime.access = SupabaseAccessProvider(URL, PUBLIC_KEY, client=client)
    sign_in(qtbot, access_runtime)
    assert runtime.access_lease.snapshot.user_id == current.user_id
    body[0] = responses["blockedA"]
    clock[0] = 121
    with qtbot.waitSignal(runtime.locked):
        runtime.check_session()
    assert runtime.service.session is None and not runtime.access_lease.valid
