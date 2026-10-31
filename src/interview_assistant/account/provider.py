"""Public-client Supabase Auth adapter using the maintained async auth SDK."""

import asyncio
import base64
import json
import math
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from supabase_auth import AsyncGoTrueClient
from supabase_auth.errors import AuthError as SDKAuthError
from supabase_auth.types import AuthResponse

from interview_assistant.account.models import AuthCode, AuthError, AuthSession

T = TypeVar("T")


def _mapped(error: Exception) -> AuthError:
    code = getattr(error, "code", None)
    status = getattr(error, "status", None)
    if code == "email_not_confirmed":
        return AuthError(AuthCode.UNCONFIRMED, "Confirm your email before signing in.")
    if code in {"over_request_rate_limit", "over_email_send_rate_limit"} or status == 429:
        return AuthError(AuthCode.RATE_LIMIT, "Too many requests. Please wait before trying again.")
    if (
        code
        in {
            "invalid_credentials",
            "user_not_found",
            "user_banned",
            "session_not_found",
            "refresh_token_not_found",
            "refresh_token_already_used",
            "bad_jwt",
            "invalid_jwt",
            "otp_expired",
            "flow_state_expired",
            "no_authorization",
        }
        or status == 401
    ):
        return AuthError(
            AuthCode.INVALID, "Login or verification code is invalid or expired. Try again."
        )
    if code in {"weak_password", "same_password", "validation_failed", "email_address_invalid"}:
        return AuthError(
            AuthCode.INVALID, "Check your email and password. Use a different, stronger password."
        )
    if code in {
        "signup_disabled",
        "email_provider_disabled",
        "email_address_not_authorized",
        "otp_disabled",
    }:
        return AuthError(
            AuthCode.CONFIGURATION, "Account email service is not configured for this request."
        )
    if isinstance(error, (httpx.TransportError, TimeoutError)) or (
        isinstance(status, int) and status >= 500
    ):
        return AuthError(
            AuthCode.NETWORK, "Cannot reach the account service. Check your connection and retry."
        )
    return AuthError(AuthCode.UNKNOWN, "The account request could not be completed. Please retry.")


class SupabaseAuthProvider:
    def __init__(
        self,
        url: str,
        publishable_key: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise AuthError(
                AuthCode.CONFIGURATION, "Account service requires a valid HTTPS project URL."
            )
        public = publishable_key.startswith("sb_publishable_")
        if not public:
            try:
                payload = publishable_key.split(".")[1]
                public = (
                    json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))[
                        "role"
                    ]
                    == "anon"
                )
            except (ValueError, IndexError, KeyError, TypeError):
                public = False
        if not public:
            raise AuthError(
                AuthCode.CONFIGURATION, "Use a Supabase publishable key for desktop login."
            )
        self._url = url.rstrip("/") + "/auth/v1"
        self._key = publishable_key
        self._http = http_client or httpx.AsyncClient(timeout=12.0, follow_redirects=False)
        self._clock = clock

    async def _run(self, operation: Callable[[AsyncGoTrueClient], Awaitable[T]]) -> T:
        # A new memory-only SDK client prevents a recovery session from affecting
        # another operation. The transport is reused; the application owns refresh.
        client = AsyncGoTrueClient(
            url=self._url,
            headers={"apikey": self._key},
            auto_refresh_token=False,
            persist_session=False,
            http_client=self._http,
        )
        try:
            async with asyncio.timeout(15):
                return await operation(client)
        except AuthError:
            raise
        except Exception as error:
            raise _mapped(error) from None

    def _session(self, response: AuthResponse) -> AuthSession:
        session = response.session
        if session is None or not session.access_token or not session.refresh_token:
            raise AuthError(AuthCode.INVALID, "No verified session was returned. Sign in again.")
        try:
            user_id = str(UUID(session.user.id))
            expires_at = float(session.expires_at or 0)
            if (
                not math.isfinite(expires_at)
                or not self._clock() < expires_at <= self._clock() + 7 * 86400
            ):
                raise ValueError("Invalid expiry")
        except (ValueError, TypeError):
            raise AuthError(AuthCode.INVALID, "Invalid account session. Sign in again.") from None
        return AuthSession(user_id, expires_at, session.access_token, session.refresh_token)

    async def login(self, email: str, password: str) -> AuthSession:
        async def run(client: AsyncGoTrueClient) -> AuthSession:
            return self._session(
                await client.sign_in_with_password({"email": email, "password": password})
            )

        return await self._run(run)

    async def signup(self, email: str, password: str) -> None:
        async def run(client: AsyncGoTrueClient) -> None:
            try:
                result = await client.sign_up({"email": email, "password": password})
            except SDKAuthError as error:
                if error.code in {"email_exists", "user_already_exists"}:
                    return
                raise
            if result.session is not None:
                await client.admin.sign_out(result.session.access_token, "local")

        await self._run(run)

    async def verify_signup(self, email: str, code: str) -> AuthSession:
        async def run(client: AsyncGoTrueClient) -> AuthSession:
            return self._session(
                await client.verify_otp({"email": email, "token": code, "type": "email"})
            )

        return await self._run(run)

    async def recover(self, email: str) -> None:
        async def run(client: AsyncGoTrueClient) -> None:
            try:
                await client.reset_password_email(email)
            except SDKAuthError as error:
                if error.code == "user_not_found":
                    return
                raise

        await self._run(run)

    async def verify_recovery(self, email: str, code: str) -> AuthSession:
        async def run(client: AsyncGoTrueClient) -> AuthSession:
            return self._session(
                await client.verify_otp({"email": email, "token": code, "type": "recovery"})
            )

        return await self._run(run)

    async def change_password(self, session: AuthSession, password: str) -> None:
        async def run(client: AsyncGoTrueClient) -> None:
            if session.expires_at <= self._clock() + 60:
                raise AuthError(AuthCode.INVALID, "Reset code expired. Request a new code.")
            established = self._session(
                await client.set_session(session.access_token, session.refresh_token)
            )
            if (
                established.user_id != session.user_id
                or established.access_token != session.access_token
            ):
                raise AuthError(AuthCode.INVALID, "Reset session changed. Request a new code.")
            await client.update_user({"password": password})

        await self._run(run)
        # Password update is already committed. A cleanup outage must not tell
        # the user the old password still works or encourage repeating the reset.
        # The caller discards this recovery-only session and requires normal login.
        try:
            await self.signout(session)
        except AuthError:
            pass

    async def refresh(self, refresh_token: str) -> AuthSession:
        async def run(client: AsyncGoTrueClient) -> AuthSession:
            return self._session(await client.refresh_session(refresh_token))

        return await self._run(run)

    async def validate(self, session: AuthSession) -> None:
        async def run(client: AsyncGoTrueClient) -> None:
            if session.expires_at <= self._clock():
                raise AuthError(AuthCode.INVALID, "Your login has expired. Sign in again.")
            response = await client.get_user(session.access_token)
            if (
                response is None
                or str(UUID(response.user.id)) != session.user_id
                or response.user.is_anonymous
            ):
                raise AuthError(AuthCode.INVALID, "Account verification failed. Sign in again.")
            if not response.user.email_confirmed_at:
                raise AuthError(AuthCode.UNCONFIRMED, "Confirm your email before signing in.")

        await self._run(run)

    async def signout(self, session: AuthSession) -> None:
        async def run(client: AsyncGoTrueClient) -> None:
            # This SDK method calls the public /logout endpoint with the user's
            # token. Unlike sign_out(), it does not suppress remote failures.
            await client.admin.sign_out(session.access_token, "local")

        await self._run(run)

    async def close(self) -> None:
        await self._http.aclose()
