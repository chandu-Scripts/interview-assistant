"""No network, real user records, or Windows credentials are used here."""

import asyncio
import base64
import json
import threading
import time

import httpx
import pytest

from interview_assistant.account.models import AuthCode, AuthError, AuthSession
from interview_assistant.account.provider import SupabaseAuthProvider
from interview_assistant.account.session import SessionService
from interview_assistant.account.vault import RefreshVault

USER = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"


class Backend:
    def __init__(self):
        self.values = {}
        self.fail = ""

    def get_password(self, service, username):
        if self.fail == "read":
            raise RuntimeError("SECRET backend diagnostic")
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        if self.fail == "write":
            raise RuntimeError("SECRET backend diagnostic")
        self.values[service, username] = password

    def delete_password(self, service, username):
        if self.fail == "delete":
            raise RuntimeError("SECRET backend diagnostic")
        self.values.pop((service, username), None)


def session(user=USER, token="refresh-one"):
    return AuthSession(user, time.time() + 3600, "private-access", token)


class Provider:
    def __init__(self):
        self.current = session()
        self.error = None
        self.refreshes = 0
        self.refresh_tokens = []
        self.signouts = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False
        self.validation_error = None

    async def _result(self):
        self.entered.set()
        if self.block:
            await self.release.wait()
        if self.error:
            raise AuthError(self.error, "Safe error")
        return self.current

    async def login(self, email, password):
        return await self._result()

    async def verify_signup(self, email, code):
        return await self._result()

    async def refresh(self, refresh_token):
        self.refreshes += 1
        self.refresh_tokens.append(refresh_token)
        return await self._result()

    async def validate(self, candidate):
        if self.validation_error:
            raise AuthError(self.validation_error, "Safe error")

    async def signout(self, candidate):
        self.signouts.append(candidate)
        if self.error:
            raise AuthError(self.error, "Safe error")

    async def close(self):
        pass


@pytest.fixture
def setup(tmp_path):
    backend = Backend()
    vault = RefreshVault("project-a", tmp_path / "restore.json", backend=backend)
    provider = Provider()
    return SessionService(provider, vault), provider, vault, backend


def test_vault_contains_refresh_only_and_no_plaintext_files(tmp_path):
    backend = Backend()
    marker = tmp_path / "restore.json"
    vault = RefreshVault("project-a", marker, backend=backend)
    candidate = session()
    vault.save(candidate)
    assert list(backend.values.values()) == [candidate.refresh_token]
    assert candidate.refresh_token not in marker.read_text()
    assert candidate.access_token not in marker.read_text()
    assert "refresh-one" not in repr(vault.load())
    assert "private-access" not in repr(candidate)
    assert vault.load().user_id == USER
    other = RefreshVault("project-b", marker, backend=backend)
    assert other.load() is None


@pytest.mark.parametrize("failure", ["write", "read", "delete"])
def test_vault_errors_are_safe_and_failed_delete_cannot_restore(tmp_path, failure):
    backend = Backend()
    marker = tmp_path / "restore.json"
    vault = RefreshVault("project-a", marker, backend=backend)
    vault.save(session())
    backend.fail = failure
    operation = {"write": lambda: vault.save(session()), "read": vault.load, "delete": vault.clear}
    with pytest.raises(AuthError) as caught:
        operation[failure]()
    assert caught.value.code == AuthCode.STORAGE
    assert "SECRET" not in str(caught.value)
    backend.fail = ""
    if failure != "read":
        assert RefreshVault("project-a", marker, backend=backend).load() is None


def test_vault_rotation_crash_keeps_restore_disabled(tmp_path, monkeypatch):
    backend = Backend()
    vault = RefreshVault("project-a", tmp_path / "restore.json", backend=backend)
    vault.save(session())
    original = vault._write

    def crash(user, *, active):
        if active:
            raise OSError("disk unavailable")
        original(user, active=active)

    monkeypatch.setattr(vault, "_write", crash)
    with pytest.raises(AuthError):
        vault.save(session(token="rotated"))
    assert vault.load() is None


@pytest.mark.parametrize(
    "marker", ["{broken", "[]", '{"active":true}', '{"active":true,"user_id":42}']
)
@pytest.mark.parametrize("remember", [False, True])
@pytest.mark.asyncio
async def test_corrupt_marker_repairs_without_blocking_fresh_login(
    tmp_path, marker, remember, caplog
):
    backend = Backend()
    backend.values["InterviewAssistantAuth:project-a", USER] = "orphan-refresh"
    path = tmp_path / "restore.json"
    path.write_text(marker, encoding="utf-8")
    vault = RefreshVault("project-a", path, backend=backend)
    assert vault.load() is None
    assert json.loads(path.read_text()) == {"active": False}
    assert "unreferenced credentials were not removed" in caplog.text
    assert "orphan-refresh" not in caplog.text
    # Unknown old identity is not guessed or bulk-deleted from the OS vault.
    assert backend.values["InterviewAssistantAuth:project-a", USER] == "orphan-refresh"
    service = SessionService(Provider(), vault)
    await service.login("example@test.invalid", "synthetic", remember=remember)
    assert service.session is not None
    assert (vault.load() is not None) == remember


@pytest.mark.asyncio
async def test_remember_is_opt_in_and_restore_requires_online_identity(setup):
    service, provider, vault, backend = setup
    await service.login("example@test.invalid", "synthetic")
    assert not backend.values
    await service.login("example@test.invalid", "synthetic", remember=True)
    service.revoke()
    provider.current = session(token="rotated")
    restored = await service.restore()
    assert restored.refresh_token == vault.load().refresh_token == "rotated"
    assert provider.refreshes == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [AuthCode.INVALID, AuthCode.NETWORK, AuthCode.RATE_LIMIT])
async def test_restore_invalid_clears_but_network_preserves(setup, error):
    service, provider, vault, _ = setup
    vault.save(session())
    provider.error = error
    with pytest.raises(AuthError):
        await service.restore()
    assert service.session is None
    assert (vault.load() is None) == (error == AuthCode.INVALID)


@pytest.mark.asyncio
async def test_online_identity_mismatch_never_unlocks(setup):
    service, provider, vault, _ = setup
    vault.save(session())
    provider.current = session(user=OTHER)
    with pytest.raises(AuthError):
        await service.restore()
    assert service.session is None
    assert vault.load() is None


@pytest.mark.asyncio
async def test_login_late_completion_after_revoke_cannot_persist(setup):
    service, provider, vault, _ = setup
    provider.block = True
    task = asyncio.create_task(service.login("example@test.invalid", "synthetic", remember=True))
    await provider.entered.wait()
    service.revoke()
    provider.release.set()
    with pytest.raises(AuthError) as caught:
        await task
    assert caught.value.code == AuthCode.CANCELLED
    assert service.session is None
    assert vault.load() is None


@pytest.mark.asyncio
async def test_concurrent_refresh_rotates_once(setup):
    service, provider, vault, _ = setup
    await service.login("example@test.invalid", "synthetic", remember=True)
    provider.current = session(token="rotated")
    provider.block = True
    provider.entered.clear()
    first = asyncio.create_task(service.refresh())
    await provider.entered.wait()
    second = asyncio.create_task(service.refresh())
    await asyncio.sleep(0)
    provider.release.set()
    results = await asyncio.gather(first, second)
    assert results[0] is results[1]
    assert provider.refreshes == 1
    assert vault.load().refresh_token == "rotated"


@pytest.mark.asyncio
async def test_logout_fences_refresh_and_revokes_previous_session(setup):
    service, provider, vault, _ = setup
    before = await service.login("example@test.invalid", "synthetic", remember=True)
    provider.block = True
    provider.entered.clear()
    refresh = asyncio.create_task(service.refresh())
    await provider.entered.wait()
    service.revoke()
    logout = asyncio.create_task(service.logout())
    provider.release.set()
    with pytest.raises(AuthError):
        await refresh
    await logout
    assert service.session is None
    assert vault.load() is None
    assert provider.signouts == [before]


@pytest.mark.asyncio
async def test_offline_logout_still_locks_and_forgets(setup):
    service, provider, vault, _ = setup
    await service.login("example@test.invalid", "synthetic", remember=True)
    provider.error = AuthCode.NETWORK
    with pytest.raises(AuthError):
        await service.logout()
    assert service.session is None
    assert vault.load() is None


@pytest.mark.asyncio
async def test_refresh_storage_failure_locks_without_old_restore(setup):
    service, provider, vault, backend = setup
    await service.login("example@test.invalid", "synthetic", remember=True)
    backend.fail = "write"
    provider.current = session(token="rotated")
    with pytest.raises(AuthError):
        await service.refresh()
    assert service.session is None
    assert vault.load() is None


@pytest.mark.asyncio
async def test_incognito_forgets_without_logout_and_never_repersists(setup):
    service, provider, vault, _ = setup
    original = await service.login("example@test.invalid", "synthetic", remember=True)
    await service.forget_remembered()
    assert service.session is original
    assert vault.load() is None
    provider.current = session(token="rotated")
    await service.refresh()
    assert vault.load() is None


@pytest.mark.asyncio
async def test_incognito_during_refresh_validation_does_not_reenable_remember(setup):
    service, provider, vault, _ = setup
    await service.login("example@test.invalid", "synthetic", remember=True)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def validate(candidate):
        entered.set()
        await release.wait()

    provider.validate = validate
    refresh = asyncio.create_task(service.refresh())
    await entered.wait()
    forget = asyncio.create_task(service.forget_remembered())
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(refresh, forget)
    await service.refresh()
    assert service.session is not None
    assert vault.load() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [AuthCode.INVALID, AuthCode.NETWORK, AuthCode.RATE_LIMIT])
async def test_refresh_invalid_locks_but_transient_keeps_valid_session(setup, error):
    service, provider, vault, _ = setup
    original = await service.login("example@test.invalid", "synthetic", remember=True)
    provider.error = error
    with pytest.raises(AuthError):
        await service.refresh()
    if error == AuthCode.INVALID:
        assert service.session is None
        assert vault.load() is None
    else:
        assert service.session is original
        assert vault.load() is not None


@pytest.mark.asyncio
async def test_expired_provider_response_cannot_enter_workspace(setup):
    service, provider, vault, _ = setup
    provider.current = AuthSession(USER, time.time() - 1, "expired-access", "refresh")
    with pytest.raises(AuthError):
        await service.login("example@test.invalid", "synthetic", remember=True)
    assert service.session is None
    assert vault.load() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["marker", "delete"])
async def test_logout_restart_safety_distinguishes_marker_and_vault_failures(
    setup, monkeypatch, failure
):
    service, _, vault, backend = setup
    await service.login("example@test.invalid", "synthetic", remember=True)
    if failure == "marker":

        def fail_write(*args, **kwargs):
            raise OSError("synthetic disk failure")

        monkeypatch.setattr(vault, "_write", fail_write)
    else:
        backend.fail = "delete"
    with pytest.raises(AuthError) as caught:
        await service.logout()
    assert caught.value.code == AuthCode.STORAGE
    assert service.session is None
    assert service.logout_safe == (failure == "delete")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["marker", "delete"])
async def test_logout_attempts_remote_revocation_even_if_local_cleanup_fails(
    setup, monkeypatch, failure
):
    service, provider, vault, backend = setup
    original = await service.login("example@test.invalid", "synthetic", remember=True)
    if failure == "marker":

        def fail_write(*args, **kwargs):
            raise OSError("synthetic disk failure")

        monkeypatch.setattr(vault, "_write", fail_write)
    else:
        backend.fail = "delete"
    provider.error = AuthCode.NETWORK
    with pytest.raises(AuthError) as caught:
        await service.logout()
    assert caught.value.code == AuthCode.STORAGE
    assert provider.signouts == [original]
    assert service.session is None


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["login", "refresh"])
async def test_thread_revoke_between_persistence_check_and_commit_cannot_adopt(
    setup, monkeypatch, operation
):
    service, provider, vault, _ = setup
    if operation == "refresh":
        await service.login("example@test.invalid", "synthetic", remember=True)
        provider.current = session(token="rotated")
    persist = service._persist

    def revoke_after_persist(*args):
        remembered = persist(*args)
        thread = threading.Thread(target=service.revoke)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        return remembered

    monkeypatch.setattr(service, "_persist", revoke_after_persist)
    with pytest.raises(AuthError) as caught:
        if operation == "login":
            await service.login("example@test.invalid", "synthetic", remember=True)
        else:
            await service.refresh()
    assert caught.value.code == AuthCode.CANCELLED
    assert service.session is None
    assert service._pending_rotation is None
    assert vault.load() is None


def test_gui_revoke_does_not_wait_for_credential_write(tmp_path):
    backend = Backend()
    started = threading.Event()
    release = threading.Event()
    original = backend.set_password

    def blocked_set(*args):
        started.set()
        assert release.wait(timeout=5)
        original(*args)

    backend.set_password = blocked_set
    vault = RefreshVault("project-a", tmp_path / "restore.json", backend=backend)
    service = SessionService(Provider(), vault)
    failures = []

    def work():
        try:
            asyncio.run(service.login("example@test.invalid", "synthetic", remember=True))
        except AuthError as error:
            failures.append(error.code)

    worker = threading.Thread(target=work)
    worker.start()
    try:
        assert started.wait(timeout=2)
        revoked = threading.Event()
        revoker = threading.Thread(target=lambda: (service.revoke(), revoked.set()))
        revoker.start()
        assert revoked.wait(timeout=1)
        revoker.join(timeout=1)
        assert service.session is None
    finally:
        release.set()
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert failures == [AuthCode.CANCELLED]
    assert vault.load() is None


def test_commit_check_and_assignment_are_atomic_with_thread_revoke(tmp_path, monkeypatch):
    vault = RefreshVault("project-a", tmp_path / "restore.json", backend=Backend())
    service = SessionService(Provider(), vault)
    checked = threading.Event()
    release = threading.Event()
    revoked = threading.Event()
    original = service._check
    candidate = session()

    def paused_check(generation):
        original(generation)
        if threading.current_thread().name == "auth-commit-test":
            checked.set()
            assert release.wait(timeout=5)

    monkeypatch.setattr(service, "_check", paused_check)
    worker = threading.Thread(
        target=lambda: service._commit(candidate, 0, False, 0, pending=False),
        name="auth-commit-test",
    )
    worker.start()
    try:
        assert checked.wait(timeout=2)
        revoker = threading.Thread(target=lambda: (service.revoke(), revoked.set()))
        revoker.start()
        assert not revoked.wait(timeout=0.05)
    finally:
        release.set()
        worker.join(timeout=5)
    revoker.join(timeout=2)
    assert revoked.is_set()
    assert service.session is None


@pytest.mark.asyncio
@pytest.mark.parametrize("remember", [False, True])
async def test_refresh_validation_outage_retains_rotated_token_without_extending_access(
    setup, remember
):
    service, provider, vault, _ = setup
    original = await service.login("example@test.invalid", "synthetic", remember=remember)
    provider.current = session(token="rotated-once")
    provider.validation_error = AuthCode.NETWORK
    with pytest.raises(AuthError):
        await service.refresh()
    assert service.session is original
    assert service.session.expires_at == original.expires_at
    if remember:
        assert vault.load().refresh_token == "rotated-once"
    else:
        assert vault.load() is None
    provider.validation_error = None
    provider.current = session(token="rotated-twice")
    await service.refresh()
    assert provider.refresh_tokens == ["refresh-one", "rotated-once"]
    assert service.session.refresh_token == "rotated-twice"


@pytest.mark.asyncio
async def test_restore_validation_outage_retains_rotation_without_unlocking_after_restart(setup):
    service, provider, vault, _ = setup
    vault.save(session())
    provider.current = session(token="rotated-once")
    provider.validation_error = AuthCode.NETWORK
    with pytest.raises(AuthError):
        await service.restore()
    assert service.session is None
    assert vault.load().refresh_token == "rotated-once"
    restarted = SessionService(provider, vault)
    provider.validation_error = None
    provider.current = session(token="rotated-twice")
    await restarted.restore()
    assert provider.refresh_tokens == ["refresh-one", "rotated-once"]
    assert restarted.session.refresh_token == "rotated-twice"


@pytest.mark.asyncio
async def test_logout_after_rotation_fences_validation_and_clears_new_credential(setup):
    service, provider, vault, _ = setup
    await service.login("example@test.invalid", "synthetic", remember=True)
    provider.current = session(token="rotated")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def validate(candidate):
        entered.set()
        await release.wait()

    provider.validate = validate
    refresh = asyncio.create_task(service.refresh())
    await entered.wait()
    assert vault.load().refresh_token == "rotated"
    service.revoke()
    logout = asyncio.create_task(service.logout())
    await asyncio.sleep(0)
    assert vault.load() is None
    release.set()
    with pytest.raises(AuthError):
        await refresh
    await logout
    assert service.session is None
    assert vault.load() is None
    assert provider.signouts[-1].refresh_token == "rotated"


def user_payload(user=USER):
    return {
        "id": user,
        "app_metadata": {},
        "user_metadata": {},
        "aud": "authenticated",
        "created_at": "2026-01-01T00:00:00Z",
        "email_confirmed_at": "2026-01-01T00:00:00Z",
        "email": "example@test.invalid",
        "is_anonymous": False,
    }


def response_payload(**changes):
    expiry = int(time.time()) + 3600
    encoded = (
        base64.urlsafe_b64encode(json.dumps({"sub": USER, "exp": expiry}).encode())
        .decode()
        .rstrip("=")
    )
    result = {
        "access_token": f"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.{encoded}.c3ludGhldGlj",
        "refresh_token": "synthetic-refresh",
        "expires_in": 3600,
        "expires_at": expiry,
        "token_type": "bearer",
        "user": user_payload(),
    }
    result.update(changes)
    return result


def http_provider(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SupabaseAuthProvider(
        "https://example.supabase.co", "sb_publishable_synthetic", http_client=client
    )


@pytest.mark.asyncio
async def test_real_sdk_login_validate_refresh_and_local_signout():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["apikey"] == "sb_publishable_synthetic"
        if request.url.path.endswith("/user"):
            return httpx.Response(200, json=user_payload())
        if request.url.path.endswith("/logout"):
            assert request.url.params["scope"] == "local"
            return httpx.Response(204)
        return httpx.Response(200, json=response_payload())

    provider = http_provider(handler)
    current = await provider.login("example@test.invalid", "synthetic")
    await provider.validate(current)
    await provider.refresh(current.refresh_token)
    await provider.signout(current)
    assert len(calls) == 4
    assert calls[0].url.params["grant_type"] == "password"
    assert calls[2].url.params["grant_type"] == "refresh_token"
    await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code,expected",
    [
        (400, "invalid_credentials", AuthCode.INVALID),
        (400, "email_not_confirmed", AuthCode.UNCONFIRMED),
        (403, "user_banned", AuthCode.INVALID),
        (403, "email_address_not_authorized", AuthCode.CONFIGURATION),
        (422, "otp_expired", AuthCode.INVALID),
        (429, "over_email_send_rate_limit", AuthCode.RATE_LIMIT),
        (503, "unexpected_failure", AuthCode.NETWORK),
    ],
)
async def test_sdk_error_codes_never_expose_remote_messages(status, code, expected):
    provider = http_provider(
        lambda _: httpx.Response(
            status,
            json={"code": code, "msg": "SECRET payload"},
            headers={"x-supabase-api-version": "2024-01-01"},
        )
    )
    with pytest.raises(AuthError) as caught:
        await provider.login("example@test.invalid", "synthetic")
    assert caught.value.code == expected
    assert "SECRET" not in str(caught.value)
    await provider.close()


@pytest.mark.asyncio
async def test_duplicate_signup_and_unknown_recovery_have_generic_success():
    def handler(request):
        code = "user_already_exists" if request.url.path.endswith("signup") else "user_not_found"
        return httpx.Response(
            400,
            json={"code": code, "msg": "SECRET"},
            headers={"x-supabase-api-version": "2024-01-01"},
        )

    provider = http_provider(handler)
    assert await provider.signup("example@test.invalid", "synthetic") is None
    assert await provider.recover("example@test.invalid") is None
    await provider.close()


@pytest.mark.asyncio
async def test_recovery_is_separate_and_password_change_revokes_session(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/user"):
            return httpx.Response(200, json=user_payload())
        if request.url.path.endswith("/logout"):
            return httpx.Response(204)
        return httpx.Response(200, json=response_payload())

    provider = http_provider(handler)
    vault = RefreshVault("project-a", tmp_path / "restore.json", backend=Backend())
    service = SessionService(provider, vault)
    recovery = await provider.verify_recovery("example@test.invalid", "12345678")
    assert json.loads(calls[0].content)["type"] == "recovery"
    assert service.session is None
    await provider.change_password(recovery, "new-synthetic-password")
    assert any(request.method == "PUT" for request in calls)
    assert calls[-1].url.path.endswith("/logout")
    assert service.session is None
    assert vault.load() is None
    await provider.close()


@pytest.mark.asyncio
async def test_password_change_success_survives_remote_signout_outage():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("/user"):
            return httpx.Response(200, json=user_payload())
        if request.url.path.endswith("/logout"):
            raise httpx.ConnectError("synthetic connection outage", request=request)
        return httpx.Response(200, json=response_payload())

    provider = http_provider(handler)
    recovery = await provider.verify_recovery("example@test.invalid", "12345678")
    assert await provider.change_password(recovery, "new-synthetic-password") is None
    assert len([request for request in calls if request.method == "PUT"]) == 1
    assert calls[-1].url.path.endswith("/logout")
    await provider.close()


@pytest.mark.asyncio
async def test_signup_otp_uses_email_type():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=response_payload())

    provider = http_provider(handler)
    await provider.verify_signup("example@test.invalid", "12345678")
    assert calls[0]["type"] == "email"
    await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry", [-1, int(time.time()) + 8 * 86400])
async def test_invalid_session_expiry_never_unlocks(expiry):
    provider = http_provider(
        lambda _: httpx.Response(200, json=response_payload(expires_at=expiry))
    )
    with pytest.raises(AuthError):
        await provider.login("example@test.invalid", "synthetic")
    await provider.close()


@pytest.mark.asyncio
async def test_mismatched_online_user_is_rejected():
    provider = http_provider(lambda _: httpx.Response(200, json=user_payload(OTHER)))
    with pytest.raises(AuthError) as caught:
        await provider.validate(session())
    assert caught.value.code == AuthCode.INVALID
    await provider.close()


@pytest.mark.asyncio
async def test_transport_timeout_is_safe_network_error():
    def handler(request):
        raise httpx.ReadTimeout("SECRET", request=request)

    provider = http_provider(handler)
    with pytest.raises(AuthError) as caught:
        await provider.login("example@test.invalid", "synthetic")
    assert caught.value.code == AuthCode.NETWORK
    assert "SECRET" not in str(caught.value)
    await provider.close()


@pytest.mark.parametrize(
    "url,key",
    [
        ("http://example.supabase.co", "sb_publishable_test"),
        ("https://example.supabase.co", "sb_secret_private"),
        ("https://user:password@example.supabase.co", "sb_publishable_test"),
    ],
)
def test_desktop_rejects_secret_keys_and_insecure_urls(url, key):
    with pytest.raises(AuthError):
        SupabaseAuthProvider(url, key)
