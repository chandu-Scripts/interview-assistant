"""Application-owned refresh, logout fencing and opt-in remembered login."""

import asyncio
import threading
import time

from interview_assistant.account.models import AuthCode, AuthError, AuthProvider, AuthSession
from interview_assistant.account.vault import RefreshVault


class SessionService:
    def __init__(self, provider: AuthProvider, vault: RefreshVault) -> None:
        self.provider = provider
        self._vault = vault
        self._session: AuthSession | None = None
        self._pending_rotation: AuthSession | None = None
        self._revoked_session: AuthSession | None = None
        self._generation = 0
        self._remember = False
        self._remember_generation = 0
        self._lock = asyncio.Lock()
        self._state_lock = threading.RLock()

    @property
    def session(self) -> AuthSession | None:
        with self._state_lock:
            return self._session

    @property
    def logout_safe(self) -> bool:
        return self.session is None and self._vault.restore_blocked

    def revoke(self) -> None:
        """Immediately fence results, including work awaiting a provider response."""
        with self._state_lock:
            self._generation += 1
            if self._pending_rotation is not None or self._session is not None:
                self._revoked_session = self._pending_rotation or self._session
            self._session = None
            self._pending_rotation = None

    def _check(self, generation: int) -> None:
        with self._state_lock:
            if generation != self._generation:
                raise AuthError(AuthCode.CANCELLED, "Account request was cancelled.")

    def _persist(
        self, session: AuthSession, generation: int, remember: bool, remember_generation: int
    ) -> bool:
        with self._state_lock:
            self._check(generation)
            remember = remember and remember_generation == self._remember_generation
        if remember:
            self._vault.save(session)
        else:
            self._vault.clear()
        with self._state_lock:
            forgotten = remember_generation != self._remember_generation
            cancelled = generation != self._generation
        if forgotten or cancelled:
            self._vault.clear()
            remember = False
        if cancelled:
            self._check(generation)
        return remember

    def _commit(
        self,
        session: AuthSession,
        generation: int,
        remember: bool,
        remember_generation: int,
        *,
        pending: bool,
    ) -> bool:
        # GUI revocation must never wait for network or Credential Manager.
        # Only the final generation check and state assignments share its lock.
        try:
            while True:
                with self._state_lock:
                    self._check(generation)
                    forgotten = remember and remember_generation != self._remember_generation
                    if not forgotten:
                        if pending:
                            self._pending_rotation = session
                        else:
                            self._session = session
                            self._pending_rotation = None
                            self._remember = remember
                            self._revoked_session = None
                        return remember
                self._vault.clear()
                remember = False
        except AuthError:
            self._vault.clear()
            raise

    def _retain_rotation(
        self, session: AuthSession, generation: int, remember: bool, remember_generation: int
    ) -> bool:
        self._check(generation)
        if session.expires_at <= time.time():
            raise AuthError(AuthCode.INVALID, "Your login has expired. Sign in again.")
        # Refresh tokens rotate before /user validation. Retain the new token
        # now so a transient validation failure cannot make us reuse its parent.
        # This does not extend the old verified access session or unlock startup.
        remember = self._persist(session, generation, remember, remember_generation)
        return self._commit(session, generation, remember, remember_generation, pending=True)

    async def _adopt(self, session: AuthSession, generation: int, remember: bool) -> AuthSession:
        with self._state_lock:
            self._check(generation)
            remember_generation = self._remember_generation
        if session.expires_at <= time.time():
            raise AuthError(AuthCode.INVALID, "Your login has expired. Sign in again.")
        await self.provider.validate(session)
        remember = self._persist(session, generation, remember, remember_generation)
        self._commit(session, generation, remember, remember_generation, pending=False)
        return session

    async def login(self, email: str, password: str, remember: bool = False) -> AuthSession:
        with self._state_lock:
            generation = self._generation
        async with self._lock:
            self._check(generation)
            session = await self.provider.login(email, password)
            return await self._adopt(session, generation, remember)

    async def verify_signup(self, email: str, code: str, remember: bool = False) -> AuthSession:
        with self._state_lock:
            generation = self._generation
        async with self._lock:
            self._check(generation)
            session = await self.provider.verify_signup(email, code)
            return await self._adopt(session, generation, remember)

    async def restore(self) -> AuthSession | None:
        with self._state_lock:
            generation = self._generation
        async with self._lock:
            self._check(generation)
            saved = self._vault.load()
            if saved is None:
                return None
            with self._state_lock:
                remember_generation = self._remember_generation
            try:
                session = await self.provider.refresh(saved.refresh_token)
                self._check(generation)
                if session.user_id != saved.user_id:
                    raise AuthError(
                        AuthCode.INVALID, "Remembered account identity changed. Sign in again."
                    )
                remember = self._retain_rotation(session, generation, True, remember_generation)
                return await self._adopt(session, generation, remember)
            except AuthError as error:
                if error.code in (AuthCode.INVALID, AuthCode.UNCONFIRMED, AuthCode.STORAGE):
                    self.revoke()
                    self._vault.clear()
                raise

    async def refresh(self) -> AuthSession:
        with self._state_lock:
            generation = self._generation
            previous = self._session
        async with self._lock:
            with self._state_lock:
                self._check(generation)
                if self._session is None:
                    raise AuthError(AuthCode.INVALID, "Sign in to continue.")
                if previous is not self._session:
                    return self._session
                remember_generation = self._remember_generation
                remember = self._remember
                credential = self._pending_rotation or self._session
                user_id = self._session.user_id
            try:
                session = await self.provider.refresh(credential.refresh_token)
                self._check(generation)
                if session.user_id != user_id:
                    raise AuthError(AuthCode.INVALID, "Account identity changed. Sign in again.")
                remember = self._retain_rotation(session, generation, remember, remember_generation)
                return await self._adopt(session, generation, remember)
            except AuthError as error:
                if error.code in (AuthCode.INVALID, AuthCode.UNCONFIRMED, AuthCode.STORAGE):
                    self.revoke()
                    self._vault.clear()
                raise

    async def logout(self) -> None:
        with self._state_lock:
            session = self._pending_rotation or self._session or self._revoked_session
            self.revoke()
            self.disable_remembering()
        cleanup_error: AuthError | None = None
        # Disable restore before waiting for a cancelled provider operation.
        try:
            self._vault.clear()
        except AuthError as error:
            cleanup_error = error
        async with self._lock:
            try:
                self._vault.clear()
            except AuthError as error:
                cleanup_error = error
            if session is not None:
                try:
                    await self.provider.signout(session)
                except AuthError as error:
                    if cleanup_error is None:
                        cleanup_error = error
                else:
                    with self._state_lock:
                        self._revoked_session = None
            if cleanup_error is not None:
                raise cleanup_error

    def disable_remembering(self) -> None:
        """Synchronously fence credential writes when incognito is enabled."""
        with self._state_lock:
            self._remember = False
            self._remember_generation += 1

    async def forget_remembered(self) -> None:
        """Incognito disables persistence without ending the active login."""
        self.disable_remembering()
        async with self._lock:
            self._vault.clear()

    async def close(self) -> None:
        self.revoke()
        await self.provider.close()
