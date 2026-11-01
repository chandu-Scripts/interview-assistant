"""Exercise the opt-in runner with actual app providers and a fake HTTP transport."""

import asyncio
import copy
import json
import subprocess
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from scripts.verify_customer_access import (
    DEV_URL,
    Acceptance,
    Fixtures,
    Refused,
    load_fixtures,
    main,
)

from interview_assistant.account.models import AuthSession

ADMIN = "11111111-1111-4111-8111-111111111111"
A = "22222222-2222-4222-8222-222222222222"
B = "33333333-3333-4333-8333-333333333333"


def fixture_data() -> dict[str, Any]:
    identities = [
        {
            "user_id": uid,
            "email": f"chandoo-access-test-{label}@example.test",
            "password": f"private-fixture-password-{label}",
        }
        for uid, label in [(ADMIN, "admin"), (A, "a"), (B, "b")]
    ]
    return {
        "project_url": DEV_URL,
        "publishable_key": "sb_publishable_synthetic",
        "admin": identities[0],
        "customers": identities[1:],
    }


class Backend:
    def __init__(self) -> None:
        self.fixtures = Fixtures.parse(fixture_data())
        self.identities = {
            item.user_id: item for item in (self.fixtures.admin, *self.fixtures.customers)
        }
        self.now = datetime.now(UTC)
        self.rows: dict[str, dict[str, Any]] = {
            uid: {
                "user_id": uid,
                "blocked": False,
                "version": 0,
                "is_admin": uid == ADMIN,
                "grants": [],
            }
            for uid in self.identities
        }
        self.revoked: set[str] = set()
        self.signouts: list[str] = []
        self.session_users: dict[str, str] = {}
        self.login_counts: dict[str, int] = {}
        self.fresh_token: str | None = None
        self.original_alive_after_fresh_logout = False
        self.events: list[dict[str, Any]] = []
        self.writes: list[dict[str, Any]] = []
        self.login_as: str | None = None
        self.failure = ""
        self.failed = False

    def tokens_for(self, uid: str) -> list[str]:
        return [token for token, user_id in self.session_users.items() if user_id == uid]

    def user(self, uid: str) -> dict[str, Any]:
        return {
            "id": uid,
            "email": self.identities[uid].email,
            "role": "authenticated",
            "aud": "authenticated",
            "app_metadata": {},
            "user_metadata": {},
            "created_at": "2026-01-01T00:00:00Z",
            "email_confirmed_at": "2026-01-01T00:00:00Z",
            "is_anonymous": False,
        }

    @staticmethod
    def deny(code: str = "42501", status: int = 403) -> httpx.Response:
        return httpx.Response(status, json={"code": code, "message": "SECRET provider body"})

    def active(self, uid: str, cap: str) -> bool:
        return not self.rows[uid]["blocked"] and any(
            g["enabled"]
            and g["capability"] == cap
            and datetime.fromisoformat(g["expires_at"]) > self.now
            for g in self.rows[uid]["grants"]
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(DEV_URL + "/")
        assert request.headers["apikey"] == "sb_publishable_synthetic"
        payload = json.loads(request.content) if request.content else {}
        path = request.url.path
        if path == "/auth/v1/token":
            identity = next(
                item for item in self.identities.values() if item.email == payload["email"]
            )
            assert payload["password"] == identity.password
            count = self.login_counts.get(identity.user_id, 0) + 1
            self.login_counts[identity.user_id] = count
            fresh = identity.user_id == A and count > 1
            uid = self.login_as or identity.user_id
            if fresh and self.failure == "fresh_identity":
                uid = B
            token = f"private-access-{uid}-{len(self.session_users) + 1}"
            if fresh and self.failure == "fresh_reused_token":
                token = self.tokens_for(A)[0]
            self.session_users[token] = uid
            if fresh:
                self.fresh_token = token
            return httpx.Response(
                200,
                json={
                    "access_token": token,
                    "refresh_token": f"private-refresh-{uid}-{count}",
                    "expires_in": 3600,
                    "expires_at": int(time.time()) + 3600,
                    "token_type": "bearer",
                    "user": self.user(uid),
                },
            )
        token = request.headers["authorization"].removeprefix("Bearer ")
        uid = self.session_users[token]
        if path == "/auth/v1/user":
            if token == self.fresh_token and self.failure == "fresh_cancel" and not self.failed:
                self.failed = True
                raise asyncio.CancelledError
            user = self.user(uid)
            if token == self.fresh_token and self.failure == "fresh_email":
                user["email"] = "chandoo-access-test-wrong@example.test"
            return httpx.Response(200, json=user)
        if path == "/auth/v1/logout":
            assert request.url.params["scope"] == "local"
            self.signouts.append(token)
            if self.failure == "logout":
                return self.deny("unavailable", 500)
            if self.failure == "logout_network":
                raise httpx.ReadTimeout("SECRET logout transport diagnostic", request=request)
            if self.failure == "logout_noop":
                return httpx.Response(204)
            if self.failure == "original_logout_noop" and token == self.tokens_for(A)[0]:
                return httpx.Response(204)
            self.revoked.add(token)
            if token == self.fresh_token:
                self.original_alive_after_fresh_logout = self.tokens_for(A)[0] not in self.revoked
            return httpx.Response(204)
        if token in self.revoked:
            return self.deny()
        name = path.rsplit("/", 1)[-1]
        negative_bug = self.failure == "ordinary_write" and name == "customer_admin_set_block"
        if (
            name.startswith("customer_admin_")
            and (uid != ADMIN or not self.rows[ADMIN]["is_admin"])
            and not negative_bug
        ):
            return self.deny()
        if name == "customer_admin_list":
            rows = [
                copy.deepcopy(row)
                for key, row in sorted(self.rows.items())
                if payload["after_user_id"] is None or key > payload["after_user_id"]
            ]
            return httpx.Response(200, json=rows[: payload["page_size"]])
        if name == "customer_admin_audit":
            events = [item for item in self.events if item["id"] > (payload["after_id"] or 0)]
            if self.failure == "audit_body" and events:
                events = [{**events[0], "transcript": "SECRET customer document"}]
            return httpx.Response(200, json=events[: payload["page_size"]])
        if name == "customer_require_access":
            return (
                httpx.Response(200, json=True)
                if self.active(uid, payload["capability"])
                else self.deny()
            )
        if name == "customer_access_snapshot":
            row = self.rows[uid]
            grant = next((g for g in row["grants"] if g["capability"] == "desktop_access"), None)
            reason = (
                "blocked"
                if row["blocked"]
                else (
                    "no_grant"
                    if grant is None or not grant["enabled"]
                    else "allowed"
                    if self.active(uid, "desktop_access")
                    else "expired"
                )
            )
            if token == self.fresh_token and self.failure == "fresh_denied":
                reason = "no_grant"
            return httpx.Response(
                200,
                json={
                    "user_id": uid,
                    "allowed": reason == "allowed",
                    "reason": reason,
                    "lease_seconds": 60 if reason == "allowed" else 0,
                    "version": row["version"],
                    "is_admin": uid == ADMIN,
                    "server_time": self.now.isoformat(),
                },
            )
        assert name in {"customer_admin_set_grant", "customer_admin_set_block"}
        target = payload["target_user_id"]
        assert target in {A, B}
        row = self.rows[target]
        if payload["expected_version"] != row["version"]:
            return self.deny("40001", 409)
        if self.failure == "cancel" and not self.failed:
            self.failed = True
            raise asyncio.CancelledError
        if self.failure == "ambiguous" and not self.failed:
            self.failed = True
            row["grants"].append(
                {
                    "capability": payload["capability"],
                    "enabled": True,
                    "expires_at": payload["expires_at"],
                }
            )
            row["version"] += 1
            raise httpx.ReadTimeout("SECRET transport diagnostic", request=request)
        if self.failure == "cleanup" and payload.get("enabled") is False and self.revoked:
            return self.deny("unavailable", 500)
        row["version"] += 1
        self.writes.append(copy.deepcopy(payload))
        if name.endswith("set_block"):
            row["blocked"] = payload["blocked"]
            action = "block" if payload["blocked"] else "unblock"
        else:
            cap = payload["capability"]
            row["grants"] = [g for g in row["grants"] if g["capability"] != cap]
            row["grants"].append(
                {key: payload[key] for key in ("capability", "enabled", "expires_at")}
            )
            action = "grant" if payload["enabled"] else "revoke"
        self.events.append(
            {
                "id": len(self.events) + 1,
                "actor_id": ADMIN,
                "target_user_id": target,
                "action": action,
                "capability": payload.get("capability"),
                "expires_at": payload.get("expires_at"),
                "reason_code": payload["reason_code"],
                "version": row["version"],
                "created_at": self.now.isoformat(),
            }
        )
        return httpx.Response(200, json={"user_id": target, "version": row["version"]})

    async def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    async def run(self) -> tuple[Acceptance, dict[str, Any]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.handle)) as client:
            runner = Acceptance(self.fixtures, client, sleep=self.sleep)
            result = (await runner.run()).public()
        return runner, result


async def test_complete_runner_uses_real_sdk_providers_and_cleans_only_fixture_changes() -> None:
    backend = Backend()
    runner, report = await backend.run()
    assert report["passed"] is True, report
    assert len(report["checks"]) == 9
    assert "unblock_fresh_signin_and_lease" in report["checks"]
    assert len(backend.tokens_for(A)) == 2
    assert backend.original_alive_after_fresh_logout
    assert backend.revoked == set(backend.session_users)
    assert runner.signed_out == {"admin", "A", "B", "A_fresh"}
    assert not runner.sessions and not runner.lease.valid
    assert all(write["target_user_id"] == A for write in backend.writes)
    assert not backend.rows[A]["blocked"]
    assert all(not g["enabled"] for g in backend.rows[A]["grants"])
    assert backend.rows[B]["version"] == 0 and backend.rows[B]["grants"] == []
    text = json.dumps(report) + repr(backend.fixtures)
    assert "private-" not in text and "@example" not in text


@pytest.mark.parametrize(
    "change", ["host", "key", "prefix", "same_id", "same_email", "count", "extra"]
)
def test_fixture_contract_refuses_unsafe_inputs(change: str) -> None:
    raw = fixture_data()
    if change == "host":
        raw["project_url"] = "https://production.supabase.co"
    elif change == "key":
        raw["publishable_key"] = "sb_secret_not_allowed"
    elif change == "prefix":
        raw["admin"]["email"] = "personal@example.test"
    elif change == "same_id":
        raw["customers"][0]["user_id"] = ADMIN
    elif change == "same_email":
        raw["customers"][0]["email"] = raw["admin"]["email"]
    elif change == "count":
        raw["customers"].pop()
    else:
        raw["service_role"] = "not_allowed"
    with pytest.raises(Refused, match="fixture_contract_rejected"):
        Fixtures.parse(raw)


def test_cli_requires_explicit_live_flag_before_reading_any_secret(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "scripts.verify_customer_access.load_fixtures", lambda _: pytest.fail("read")
    )
    assert main(["--fixtures", "missing.json"]) == 2
    assert "explicit_live_opt_in_required" in capsys.readouterr().out


def test_loader_requires_explicit_ignored_json_and_does_not_read_environment(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "fixtures.json"
    source.write_text(json.dumps(fixture_data()), encoding="utf-8")
    monkeypatch.setenv("SUPABASE_URL", "https://production.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "ignored-secret")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 1))
    with pytest.raises(Refused, match="fixture_file_must_be_ignored_json"):
        load_fixtures(source)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0))
    assert load_fixtures(source).project_url == DEV_URL
    source = tmp_path / ".env"
    source.write_text(json.dumps(fixture_data()), encoding="utf-8")
    with pytest.raises(Refused, match="fixture_file_must_be_ignored_json"):
        load_fixtures(source)


@pytest.mark.parametrize(
    "mode", ["identity", "customer_admin", "admin_missing", "blocked", "active"]
)
async def test_preflight_refuses_before_any_customer_mutation(mode: str) -> None:
    backend = Backend()
    if mode == "identity":
        backend.login_as = A
    elif mode == "customer_admin":
        backend.rows[B]["is_admin"] = True
    elif mode == "admin_missing":
        backend.rows[ADMIN]["is_admin"] = False
    elif mode == "blocked":
        backend.rows[A]["blocked"] = True
    else:
        backend.rows[B]["grants"] = [
            {
                "capability": "cloud_backup",
                "enabled": True,
                "expires_at": (backend.now + timedelta(days=1)).isoformat(),
            }
        ]
    _, report = await backend.run()
    assert not report["passed"] and not backend.writes
    assert report["cleanup_ok"]


@pytest.mark.parametrize("failure", ["cancel", "ambiguous", "audit_body", "ordinary_write"])
async def test_failure_cleans_committed_and_uncertain_grants_without_leaking_errors(
    failure: str,
) -> None:
    backend = Backend()
    backend.failure = failure
    runner, report = await backend.run()
    assert not report["passed"] and report["cleanup_ok"], report
    assert all(not g["enabled"] for g in backend.rows[A]["grants"])
    assert not backend.rows[A]["blocked"] and not runner.sessions
    assert "SECRET" not in json.dumps(report)
    assert backend.revoked == set(backend.session_users)


@pytest.mark.parametrize("failure", ["cleanup", "logout", "logout_network"])
async def test_cleanup_and_logout_failures_are_explicit(failure: str) -> None:
    backend = Backend()
    backend.failure = failure
    _, report = await backend.run()
    assert not report["passed"] and not report["cleanup_ok"]
    assert report["cleanup_failures"] and "SECRET" not in json.dumps(report)


async def test_successful_noop_logout_is_retried_and_cannot_claim_cleanup_success() -> None:
    backend = Backend()
    backend.failure = "logout_noop"
    runner, report = await backend.run()
    assert not report["passed"] and not report["cleanup_ok"], report
    original, fresh = backend.tokens_for(A)
    assert backend.signouts.count(fresh) == 2
    assert backend.signouts.count(original) == 1
    assert backend.signouts.count(backend.tokens_for(B)[0]) == 1
    assert backend.signouts.count(backend.tokens_for(ADMIN)[0]) == 1
    assert set(report["cleanup_failures"]) == {
        f"session_{label}_revocation_unconfirmed" for label in ("admin", "A", "B", "A_fresh")
    }
    assert backend.rows[B]["grants"] == []  # No grant must not be mistaken for logout.
    assert not backend.revoked and not runner.signed_out and not runner.sessions
    assert not runner.lease.valid and "SECRET" not in json.dumps(report)


@pytest.mark.parametrize(
    "failure,label",
    [
        ("fresh_identity", "authenticated_identity_mismatch"),
        ("fresh_email", "authenticated_email_mismatch"),
        ("fresh_reused_token", "fresh_signin_reused_session"),
        ("fresh_denied", "fresh_signin_grant_missing"),
        ("fresh_cancel", "acceptance_cancelled"),
    ],
)
async def test_failed_fresh_signin_keeps_every_returned_session_tracked_for_cleanup(
    failure: str, label: str
) -> None:
    backend = Backend()
    backend.failure = failure
    runner, report = await backend.run()
    assert not report["passed"] and report["cleanup_ok"], report
    assert label in report["failures"]
    assert backend.login_counts[A] == 2
    assert backend.revoked == set(backend.session_users)
    assert runner.signed_out == {"admin", "A", "B", "A_fresh"}
    assert not runner.sessions and not runner.lease.valid
    assert all(not grant["enabled"] for grant in backend.rows[A]["grants"])
    assert "private-" not in json.dumps(report) and "SECRET" not in json.dumps(report)


async def test_fresh_logout_does_not_hide_a_still_live_original_session() -> None:
    backend = Backend()
    backend.failure = "original_logout_noop"
    runner, report = await backend.run()
    assert not report["passed"] and not report["cleanup_ok"], report
    assert report["cleanup_failures"] == ["session_A_revocation_unconfirmed"]
    original, fresh = backend.tokens_for(A)
    assert original not in backend.revoked and fresh in backend.revoked
    assert backend.signouts.count(original) == backend.signouts.count(fresh) == 1
    assert "A_fresh" in runner.signed_out and "A" not in runner.signed_out
    assert not runner.sessions and not runner.lease.valid


@pytest.mark.parametrize("stage", ["before", "after"])
async def test_logout_proof_requires_token_validity_before_and_after_request(
    stage: str, monkeypatch
) -> None:
    clock = [3550 if stage == "before" else 3500]
    calls = []
    monkeypatch.setattr(
        "scripts.verify_customer_access.time", SimpleNamespace(time=lambda: clock[0])
    )

    def denied(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        clock[0] = 3550
        return Backend.deny()

    session = AuthSession(A, 3600, "private-access-token", "private-refresh-token")
    async with httpx.AsyncClient(transport=httpx.MockTransport(denied)) as client:
        runner = Acceptance(Fixtures.parse(fixture_data()), client)
        with pytest.raises(Refused, match="logout_token_validity_insufficient"):
            await runner.confirm_signed_out("A", session)
        assert not runner.signed_out
    assert len(calls) == (0 if stage == "before" else 1)


async def test_unrelated_target_guard_prevents_network_mutation() -> None:
    backend = Backend()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("network"))
    ) as client:
        runner = Acceptance(backend.fixtures, client)
        with pytest.raises(Refused, match="unrelated_mutation_refused"):
            await runner.mutate(ADMIN, blocked=False)


async def test_cleanup_preserves_preexisting_disabled_grant_expiry() -> None:
    backend = Backend()
    original = (backend.now - timedelta(days=2)).isoformat()
    backend.rows[A]["grants"] = [
        {"capability": "cloud_backup", "enabled": False, "expires_at": original}
    ]
    _, report = await backend.run()
    assert report["passed"], report
    grant = next(g for g in backend.rows[A]["grants"] if g["capability"] == "cloud_backup")
    assert not grant["enabled"] and grant["expires_at"] == original
