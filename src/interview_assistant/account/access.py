"""Server-owned access snapshots and bounded, memory-only desktop leases."""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import httpx

from interview_assistant.account.models import AuthCode, AuthError, AuthSession

MAX_LEASE_SECONDS = 60.0


@dataclass(frozen=True)
class AccessSnapshot:
    user_id: str
    allowed: bool
    reason: str
    lease_seconds: float
    version: int
    is_admin: bool

    @classmethod
    def parse(cls, data: object, expected_user: str) -> "AccessSnapshot":
        try:
            if not isinstance(data, dict):
                raise ValueError
            user_id = str(UUID(data["user_id"]))
            allowed, admin = data["allowed"], data["is_admin"]
            ttl, version = data["lease_seconds"], data["version"]
            reason = data["reason"]
            if (
                user_id != expected_user
                or type(allowed) is not bool
                or type(admin) is not bool
                or type(ttl) not in (float, int)
                or not math.isfinite(ttl)
                or not 0 <= ttl <= MAX_LEASE_SECONDS
                or type(version) is not int
                or version < 0
                or reason not in {"allowed", "blocked", "no_grant", "expired"}
                or allowed != (reason == "allowed")
                or (allowed and ttl <= 0)
                or (not allowed and ttl != 0)
            ):
                raise ValueError
            return cls(user_id, allowed, reason, float(ttl), version, admin)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise AuthError(
                AuthCode.CONFIGURATION, "Access service returned an invalid response."
            ) from None


class AccessProvider(Protocol):
    async def snapshot(self, session: AuthSession) -> AccessSnapshot: ...
    async def close(self) -> None: ...


class SupabaseAccessProvider:
    def __init__(
        self, url: str, publishable_key: str, *, client: httpx.AsyncClient | None = None
    ) -> None:
        self._url = url.rstrip("/") + "/rest/v1/rpc/customer_access_snapshot"
        self._key = publishable_key
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=5.0))

    async def snapshot(self, session: AuthSession) -> AccessSnapshot:
        try:
            response = await self._client.post(
                self._url,
                headers={"apikey": self._key, "Authorization": f"Bearer {session.access_token}"},
                json={},
            )
        except httpx.HTTPError:
            raise AuthError(
                AuthCode.NETWORK, "Access service is unavailable. Reconnect and retry."
            ) from None
        if response.status_code in (401, 403):
            raise AuthError(AuthCode.INVALID, "Account access was denied. Sign in again.")
        if response.status_code == 429 or response.status_code >= 500:
            raise AuthError(AuthCode.NETWORK, "Access service is unavailable. Reconnect and retry.")
        if response.status_code != 200:
            raise AuthError(
                AuthCode.CONFIGURATION, "Account access is not configured. Contact the app owner."
            )
        try:
            data = response.json()
        except ValueError:
            raise AuthError(
                AuthCode.CONFIGURATION, "Access service returned an invalid response."
            ) from None
        return AccessSnapshot.parse(data, session.user_id)

    async def close(self) -> None:
        await self._client.aclose()


@dataclass(frozen=True)
class AccessAuthorization:
    session: AuthSession
    snapshot: AccessSnapshot
    started_at: float


class AccessLease:
    def __init__(self, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._clock = monotonic
        self.deadline = 0.0
        self.refresh_at = 0.0
        self.snapshot: AccessSnapshot | None = None

    def adopt(self, result: AccessAuthorization, session: AuthSession | None) -> bool:
        if session is None or result.session is not session:
            return False
        snapshot = result.snapshot
        if snapshot.user_id != session.user_id:
            raise AuthError(AuthCode.INVALID, "Account identity changed. Sign in again.")
        if not snapshot.allowed:
            self.clear()
            message = (
                "This account is blocked. Contact the app owner."
                if snapshot.reason == "blocked"
                else "This account has no active access. Contact the app owner."
            )
            raise AuthError(AuthCode.INVALID, message)
        deadline = result.started_at + min(snapshot.lease_seconds, MAX_LEASE_SECONDS)
        if deadline <= self._clock():
            raise AuthError(AuthCode.NETWORK, "Access check expired. Reconnect and retry.")
        self.snapshot = snapshot
        self.deadline = deadline
        self.refresh_at = result.started_at + min(20.0, snapshot.lease_seconds / 3)
        return True

    @property
    def valid(self) -> bool:
        return self.snapshot is not None and self._clock() < self.deadline

    def clear(self) -> None:
        self.snapshot = None
        self.deadline = self.refresh_at = 0.0
