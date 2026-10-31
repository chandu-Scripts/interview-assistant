"""Opt-in dev-project acceptance using preprovisioned synthetic accounts only."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
import subprocess
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from interview_assistant.account.access import (
    AccessAuthorization,
    AccessLease,
    SupabaseAccessProvider,
)
from interview_assistant.account.models import AuthCode, AuthError, AuthSession
from interview_assistant.account.provider import SupabaseAuthProvider

DEV_URL = "https://lctuyznspcufmzzxbjzv.supabase.co"
CAPABILITIES = {"desktop_access", "cloud_backup"}
LOGOUT_VALIDITY_MARGIN = 60
RPCS = {
    "customer_admin_list",
    "customer_admin_audit",
    "customer_admin_set_block",
    "customer_admin_set_grant",
    "customer_access_snapshot",
    "customer_require_access",
}


class Refused(Exception):
    """A fixed, nonsecret diagnostic suitable for the result report."""


class RPCError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__("RPC request failed")
        self.status = status
        self.code = code if code in {"42501", "40001"} else "other"


@dataclass(frozen=True)
class Identity:
    user_id: str
    email: str = field(repr=False)
    password: str = field(repr=False)


@dataclass(frozen=True)
class Fixtures:
    project_url: str
    publishable_key: str = field(repr=False)
    admin: Identity
    customers: tuple[Identity, Identity]

    @classmethod
    def parse(cls, raw: Any) -> Fixtures:
        try:
            if set(raw) != {"project_url", "publishable_key", "admin", "customers"}:
                raise ValueError
            if raw["project_url"] != DEV_URL or not re.fullmatch(
                r"sb_publishable_[A-Za-z0-9_-]+", raw["publishable_key"]
            ):
                raise ValueError
            if not isinstance(raw["customers"], list) or len(raw["customers"]) != 2:
                raise ValueError
            identities = []
            for item in [raw["admin"], *raw["customers"]]:
                if set(item) != {"user_id", "email", "password"}:
                    raise ValueError
                uid = str(UUID(item["user_id"]))
                email = item["email"]
                if not isinstance(email, str) or not re.fullmatch(
                    r"chandoo-access-test-[a-z0-9-]+@[a-z0-9.-]+\.[a-z]{2,}", email
                ):
                    raise ValueError
                password = item["password"]
                if not isinstance(password, str) or not 12 <= len(password) <= 1024:
                    raise ValueError
                identities.append(Identity(uid, email, password))
            if len({item.user_id for item in identities}) != 3:
                raise ValueError
            if len({item.email for item in identities}) != 3:
                raise ValueError
            return cls(
                DEV_URL, raw["publishable_key"], identities[0], (identities[1], identities[2])
            )
        except (ValueError, TypeError, KeyError, AttributeError):
            raise Refused("fixture_contract_rejected") from None


def load_fixtures(path: Path) -> Fixtures:
    try:
        source = path.resolve(strict=True)
        if source.suffix != ".json" or source.stat().st_size > 65536 or not source.is_file():
            raise ValueError
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", "--", source.name],
            cwd=source.parent,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if ignored.returncode != 0:
            raise ValueError
        return Fixtures.parse(json.loads(source.read_text(encoding="utf-8")))
    except (OSError, ValueError, subprocess.SubprocessError):
        raise Refused("fixture_file_must_be_ignored_json") from None


def require(condition: bool, label: str) -> None:
    if not condition:
        raise Refused(label)


def timestamp(value: Any) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(UTC)
    except (ValueError, TypeError, AttributeError):
        raise Refused("invalid_server_timestamp") from None


@dataclass
class Report:
    checks: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    cleanup_failures: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return {
            "passed": not self.failures and not self.cleanup_failures,
            "checks": self.checks,
            "failures": self.failures,
            "cleanup_ok": not self.cleanup_failures,
            "cleanup_failures": self.cleanup_failures,
        }


class Acceptance:
    def __init__(
        self,
        fixtures: Fixtures,
        client: httpx.AsyncClient,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        # Revalidate even callers that bypass the CLI loader.
        self.fixtures = Fixtures.parse(
            {
                "project_url": fixtures.project_url,
                "publishable_key": fixtures.publishable_key,
                "admin": vars(fixtures.admin),
                "customers": [vars(item) for item in fixtures.customers],
            }
        )
        self.client = client
        self.auth = SupabaseAuthProvider(DEV_URL, fixtures.publishable_key, http_client=client)
        self.access = SupabaseAccessProvider(DEV_URL, fixtures.publishable_key, client=client)
        self.sleep = sleep
        self.sessions: dict[str, AuthSession] = {}
        self.signed_out: set[str] = set()
        self.touched_grants: set[tuple[str, str]] = set()
        self.touched_blocks: set[str] = set()
        self.report = Report()
        self.lease = AccessLease()
        self.audit_start = 0
        self.baseline: dict[str, dict[str, Any]] = {}

    @property
    def targets(self) -> set[str]:
        return {item.user_id for item in self.fixtures.customers}

    async def rpc(
        self, session: AuthSession, name: str, params: dict[str, Any], *, denial_probe: bool = False
    ) -> Any:
        require(name in RPCS, "rpc_not_allowlisted")
        if name.startswith("customer_admin_set_"):
            require(params.get("target_user_id") in self.targets, "unrelated_mutation_refused")
            negative_block = (
                denial_probe
                and session.user_id in self.targets
                and name == "customer_admin_set_block"
                and params.get("blocked") is True
            )
            require(
                session.user_id == self.fixtures.admin.user_id or negative_block,
                "nonadmin_mutation_refused",
            )
        response = await self.client.post(
            f"{DEV_URL}/rest/v1/rpc/{name}",
            json=params,
            headers={
                "apikey": self.fixtures.publishable_key,
                "Authorization": f"Bearer {session.access_token}",
            },
        )
        if response.status_code != 200:
            code = "other"
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    code = payload.get("code", "other")
            except ValueError:
                pass
            raise RPCError(response.status_code, code)
        try:
            return response.json()
        except ValueError:
            raise Refused("malformed_rpc_response") from None

    async def denied(
        self, session: AuthSession, name: str, params: dict[str, Any], *, denial_probe: bool = False
    ) -> None:
        try:
            await self.rpc(session, name, params, denial_probe=denial_probe)
        except RPCError as error:
            require(error.status in {401, 403} or error.code == "42501", "denial_not_confirmed")
        else:
            raise Refused("expected_access_denial")

    @staticmethod
    def require_logout_validity(session: AuthSession) -> None:
        require(
            session.expires_at - time.time() >= LOGOUT_VALIDITY_MARGIN,
            "logout_token_validity_insufficient",
        )

    async def confirm_signed_out(self, label: str, session: AuthSession) -> None:
        self.require_logout_validity(session)
        try:
            await self.access.snapshot(session)
        except AuthError as error:
            require(error.code == AuthCode.INVALID, "logout_snapshot_denial_unconfirmed")
        else:
            # A successful no-grant/blocked snapshot still proves a live Auth session.
            raise Refused("logout_session_still_authorized")
        self.require_logout_validity(session)
        self.signed_out.add(label)

    async def row(self, user_id: str) -> dict[str, Any]:
        require(
            user_id in self.targets | {self.fixtures.admin.user_id}, "unrelated_identity_refused"
        )
        number = UUID(user_id).int
        rows = await self.rpc(
            self.sessions["admin"],
            "customer_admin_list",
            {
                "after_user_id": str(UUID(int=number - 1)) if number else None,
                "page_size": 1,
            },
        )
        require(isinstance(rows, list) and len(rows) == 1, "fixture_account_not_found")
        row = rows[0]
        require(isinstance(row, dict) and row.get("user_id") == user_id, "fixture_account_mismatch")
        require(type(row.get("version")) is int and row["version"] >= 0, "invalid_version")
        require(
            type(row.get("blocked")) is bool and type(row.get("is_admin")) is bool, "invalid_role"
        )
        grants = row.get("grants")
        require(isinstance(grants, list) and len(grants) <= 2, "invalid_grants")
        seen = set()
        for grant in grants:
            require(
                isinstance(grant, dict) and grant.get("capability") in CAPABILITIES,
                "invalid_grants",
            )
            require(
                grant["capability"] not in seen and type(grant.get("enabled")) is bool,
                "invalid_grants",
            )
            timestamp(grant.get("expires_at"))
            seen.add(grant["capability"])
        return row

    async def mutate(
        self,
        uid: str,
        *,
        blocked: bool | None = None,
        capability: str | None = None,
        enabled: bool = False,
        expiry: str | None = None,
        expected: int | None = None,
    ) -> Any:
        require(uid in self.targets, "unrelated_mutation_refused")
        current = await self.row(uid)
        params: dict[str, Any] = {
            "target_user_id": uid,
            "expected_version": current["version"] if expected is None else expected,
            "reason_code": "support",
        }
        if blocked is not None:
            self.touched_blocks.add(
                uid
            )  # Also track ambiguous network failures before retry/cleanup.
            params["blocked"] = blocked
            name = "customer_admin_set_block"
        else:
            require(capability in CAPABILITIES and expiry is not None, "invalid_fixture_grant")
            assert capability is not None
            self.touched_grants.add((uid, capability))
            params.update(capability=capability, enabled=enabled, expires_at=expiry)
            name = "customer_admin_set_grant"
        result = await self.rpc(self.sessions["admin"], name, params)
        require(
            isinstance(result, dict)
            and result.get("user_id") == uid
            and type(result.get("version")) is int
            and result.get("version") == params["expected_version"] + 1,
            "mutation_not_confirmed",
        )
        return result

    async def audit(self, start: int, *, verify: bool) -> int:
        cursor = start
        count = 0
        expected_fields = {
            "id",
            "actor_id",
            "target_user_id",
            "action",
            "capability",
            "expires_at",
            "reason_code",
            "version",
            "created_at",
        }
        for _ in range(20):
            rows = await self.rpc(
                self.sessions["admin"],
                "customer_admin_audit",
                {
                    "after_id": cursor or None,
                    "page_size": 100,
                },
            )
            require(isinstance(rows, list) and len(rows) <= 100, "invalid_audit_page")
            for item in rows:
                require(
                    isinstance(item, dict) and set(item) == expected_fields,
                    "audit_not_metadata_only",
                )
                require(type(item["id"]) is int and item["id"] > cursor, "invalid_audit_order")
                cursor = item["id"]
                if verify and item["actor_id"] == self.fixtures.admin.user_id:
                    require(
                        item["target_user_id"] in self.targets, "unexpected_fixture_audit_target"
                    )
                    require(
                        item["action"] in {"grant", "revoke", "block", "unblock"}
                        and item["reason_code"] == "support",
                        "unexpected_fixture_audit_action",
                    )
                    count += 1
            if len(rows) < 100:
                require(not verify or count >= 7, "fixture_audit_missing")
                return cursor
        raise Refused("audit_scan_limit")

    async def preflight(self) -> None:
        for label, identity in zip(
            ("admin", "A", "B"), (self.fixtures.admin, *self.fixtures.customers), strict=True
        ):
            session = await self.auth.login(identity.email, identity.password)
            self.sessions[label] = session  # Sign out even a mismatched returned session.
            require(session.user_id == identity.user_id, "authenticated_identity_mismatch")
            await self.auth.validate(session)
            response = await self.client.get(
                f"{DEV_URL}/auth/v1/user",
                headers={
                    "apikey": self.fixtures.publishable_key,
                    "Authorization": f"Bearer {session.access_token}",
                },
            )
            require(response.status_code == 200, "fixture_identity_unverified")
            data = response.json()
            require(
                isinstance(data, dict) and data.get("email") == identity.email,
                "authenticated_email_mismatch",
            )
            require(session.expires_at > time.time() + 120, "fixture_session_too_short")
        admin = await self.row(self.fixtures.admin.user_id)
        require(admin["is_admin"] and not admin["blocked"], "fixture_admin_role_missing")
        snapshot = await self.rpc(self.sessions["A"], "customer_access_snapshot", {})
        server_now = timestamp(snapshot.get("server_time"))
        for identity in self.fixtures.customers:
            row = await self.row(identity.user_id)
            require(not row["is_admin"] and not row["blocked"], "unsafe_customer_baseline")
            require(
                not any(
                    grant["enabled"] and timestamp(grant["expires_at"]) > server_now
                    for grant in row["grants"]
                ),
                "preexisting_active_grant",
            )
            self.baseline[identity.user_id] = row
        self.audit_start = await self.audit(0, verify=False)
        self.report.checks.append("synthetic_identities_and_clean_baseline")

    async def exercise(self) -> None:
        a, b = (self.sessions[label] for label in ("A", "B"))
        for session in (a, b):
            require(not (await self.access.snapshot(session)).allowed, "missing_grant_not_denied")
            await self.denied(
                session, "customer_admin_list", {"after_user_id": None, "page_size": 1}
            )
            await self.denied(session, "customer_admin_audit", {"after_id": None, "page_size": 1})
        self.touched_blocks.add(a.user_id)
        await self.denied(
            b,
            "customer_admin_set_block",
            {
                "target_user_id": a.user_id,
                "blocked": True,
                "reason_code": "support",
                "expected_version": (await self.row(a.user_id))["version"],
            },
            denial_probe=True,
        )
        self.report.checks.append("missing_grants_and_ordinary_admin_denial")
        raw = await self.rpc(a, "customer_access_snapshot", {})
        server_now = timestamp(raw.get("server_time"))
        expiry = (server_now + timedelta(minutes=10)).isoformat()
        old_version = (await self.row(a.user_id))["version"]
        await self.mutate(a.user_id, capability="cloud_backup", enabled=True, expiry=expiry)
        await self.mutate(a.user_id, capability="desktop_access", enabled=True, expiry=expiry)
        started = time.monotonic()
        allowed = await self.access.snapshot(a)
        require(allowed.allowed, "grant_not_allowed")
        self.lease.adopt(AccessAuthorization(a, allowed, started), a)
        require(
            self.lease.valid and not (await self.access.snapshot(b)).allowed,
            "customer_isolation_failed",
        )
        row = await self.row(a.user_id)
        require(
            all(
                any(g["capability"] == cap and g["enabled"] for g in row["grants"])
                for cap in CAPABILITIES
            ),
            "grant_capability_not_preserved",
        )
        for cap in CAPABILITIES:
            require(
                await self.rpc(a, "customer_require_access", {"capability": cap}) is True,
                "protected_grant_not_allowed",
            )
        self.report.checks.append("grant_A_lease_and_B_isolation_capabilities_preserved")
        try:
            await self.mutate(a.user_id, blocked=True, expected=old_version)
        except RPCError as error:
            require(error.code == "40001", "stale_version_not_confirmed")
        else:
            raise Refused("stale_version_accepted")
        require(not (await self.row(a.user_id))["blocked"], "stale_version_mutated_account")
        self.report.checks.append("stale_version_rejected")
        await self.mutate(a.user_id, blocked=True)
        for cap in CAPABILITIES:
            await self.denied(a, "customer_require_access", {"capability": cap})
        blocked = await self.access.snapshot(a)
        require(blocked.reason == "blocked", "block_snapshot_failed")
        try:
            self.lease.adopt(AccessAuthorization(a, blocked, time.monotonic()), a)
        except AuthError as error:
            require(
                error.code == AuthCode.INVALID and not self.lease.valid, "blocked_lease_not_cleared"
            )
        else:
            raise Refused("blocked_lease_accepted")
        await self.mutate(a.user_id, blocked=False)
        require((await self.access.snapshot(a)).allowed, "unblock_lost_valid_grant")
        self.report.checks.append(
            "block_denies_protected_access_and_clears_lease_unblock_preserves_grant"
        )
        await self.mutate(a.user_id, capability="desktop_access", enabled=False, expiry=expiry)
        require((await self.access.snapshot(a)).reason == "no_grant", "revoke_not_denied")
        require(
            await self.rpc(a, "customer_require_access", {"capability": "cloud_backup"}) is True,
            "revoke_changed_other_capability",
        )
        raw = await self.rpc(a, "customer_access_snapshot", {})
        short_expiry = (timestamp(raw.get("server_time")) + timedelta(seconds=5)).isoformat()
        await self.mutate(a.user_id, capability="desktop_access", enabled=True, expiry=short_expiry)
        await self.sleep(6)
        require((await self.access.snapshot(a)).reason == "expired", "grant_expiry_not_denied")
        await self.denied(a, "customer_require_access", {"capability": "desktop_access"})
        self.report.checks.append("revoke_and_actual_grant_expiry")
        await self.mutate(a.user_id, capability="desktop_access", enabled=True, expiry=expiry)
        require((await self.access.snapshot(a)).allowed, "prelogout_access_missing")
        self.require_logout_validity(a)
        await self.auth.signout(a)
        await self.denied(a, "customer_require_access", {"capability": "desktop_access"})
        await self.confirm_signed_out("A", a)
        require(not (await self.access.snapshot(b)).allowed, "customer_B_changed")
        untouched = await self.row(b.user_id)
        require(
            untouched["version"] == self.baseline[b.user_id]["version"]
            and untouched["grants"] == self.baseline[b.user_id]["grants"],
            "customer_B_metadata_changed",
        )
        await self.audit(self.audit_start, verify=True)
        self.report.checks.extend(["logout_rejects_old_session", "fixture_action_metadata_audit"])

    async def cleanup(self) -> None:
        # Fresh versions are used once, with no blind retry if another actor changes the fixture.
        if self.touched_blocks or self.touched_grants:
            for uid in sorted(self.touched_blocks):
                try:
                    await self.mutate(uid, blocked=False)
                except Exception:
                    self.report.cleanup_failures.append("fixture_unblock_failed")
            for uid, cap in sorted(self.touched_grants):
                try:
                    row = await self.row(uid)
                    grant = next((g for g in row["grants"] if g["capability"] == cap), None)
                    if grant is not None:
                        original = next(
                            (g for g in self.baseline[uid]["grants"] if g["capability"] == cap),
                            grant,
                        )
                        await self.mutate(
                            uid, capability=cap, enabled=False, expiry=original["expires_at"]
                        )
                except Exception:
                    self.report.cleanup_failures.append("fixture_grant_disable_failed")
            for uid in self.touched_blocks | {uid for uid, _ in self.touched_grants}:
                try:
                    row = await self.row(uid)
                    require(
                        not row["blocked"]
                        and not any(
                            grant["enabled"] and (uid, grant["capability"]) in self.touched_grants
                            for grant in row["grants"]
                        ),
                        "cleanup_state_unconfirmed",
                    )
                except Exception:
                    self.report.cleanup_failures.append("fixture_reset_not_confirmed")
        for label, session in self.sessions.items():
            if label not in self.signed_out:
                try:
                    await self.auth.signout(session)
                except Exception:
                    self.report.cleanup_failures.append(f"session_{label}_signout_failed")
                try:
                    await self.confirm_signed_out(label, session)
                except Exception:
                    self.report.cleanup_failures.append(f"session_{label}_revocation_unconfirmed")
        self.lease.clear()
        self.sessions.clear()

    async def run(self) -> Report:
        try:
            await self.preflight()
            await self.exercise()
        except Refused as error:
            self.report.failures.append(str(error))
        except asyncio.CancelledError:
            self.report.failures.append("acceptance_cancelled")
        except Exception:
            self.report.failures.append("acceptance_request_failed")
        finally:
            task = asyncio.create_task(self.cleanup())
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
        return self.report


async def live(fixtures: Fixtures) -> Report:
    async with httpx.AsyncClient(timeout=12, follow_redirects=False, trust_env=False) as client:
        return await Acceptance(fixtures, client).run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Explicitly permit dev fixture mutations"
    )
    parser.add_argument(
        "--fixtures", type=Path, required=True, help="Ignored synthetic fixture JSON"
    )
    args = parser.parse_args(argv)
    if not args.live:
        print('{"passed": false, "failures": ["explicit_live_opt_in_required"]}')
        return 2
    logging.disable(logging.CRITICAL)
    try:
        result = asyncio.run(live(load_fixtures(args.fixtures))).public()
    except Refused as error:
        result = {"passed": False, "failures": [str(error)]}
    except (Exception, KeyboardInterrupt):
        result = {"passed": False, "failures": ["acceptance_stopped_check_fixture_cleanup"]}
    print(json.dumps(result))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
