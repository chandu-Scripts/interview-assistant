"""Qt bridge for authentication. Provider I/O stays on a separate asyncio loop."""

import time
from collections.abc import Callable, Coroutine
from concurrent.futures import Future
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QDialog

from interview_assistant.account.access import AccessAuthorization, AccessLease, AccessProvider
from interview_assistant.account.models import AuthCode, AuthError, AuthSession
from interview_assistant.account.session import SessionService
from interview_assistant.app.async_runner import AsyncRunner
from interview_assistant.ui.account_screen import AccountScreen


class AccountRuntime(QObject):
    completed = Signal(int, str, object)
    locked = Signal(str)
    notice = Signal(str)

    def __init__(
        self,
        service: SessionService,
        *,
        clock: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        runner: AsyncRunner | None = None,
        access: AccessProvider | None = None,
    ) -> None:
        super().__init__()
        self.service = service
        self.runner = runner or AsyncRunner()
        self.clock = clock
        self.monotonic = monotonic
        self.access = access
        self.access_lease = AccessLease(lambda: self.monotonic())
        self._access_pending: Future[Any] | None = None
        self._observed_session: AuthSession | None = None
        self._deadline = 0.0
        self.screen: AccountScreen | None = None
        self._generation = 0
        self._pending: Future[Any] | None = None
        self._recovery: AuthSession | None = None
        self._next_retry = 0.0
        self._retry_delay = 2.0
        self._closed = False
        self.lock_message: str | None = None
        self.completed.connect(self._finish)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.check_session)

    def authenticate(self, screen: AccountScreen) -> bool:
        self.screen = screen
        screen.submitted.connect(self._submit_form)
        screen.stage_changed.connect(self._stage_changed)
        screen.set_restore_available(True)
        accepted = screen.exec() == QDialog.DialogCode.Accepted
        self.screen = None
        screen.clear_secrets()
        if not accepted:
            self.invalidate()
        else:
            self._observe_session()
            self.timer.start()
        return accepted

    def _stage_changed(self, stage: str) -> None:
        if stage != "reset":
            self._recovery = None

    def _submit(self, action: str, work: Coroutine[Any, Any, Any]) -> None:
        generation = self._generation
        future = self.runner.submit(work)
        if action == "access":
            self._access_pending = future
        else:
            self._pending = future

        def done(future: Future[Any]) -> None:
            try:
                result = future.result()
            except AuthError as exc:
                result = exc
            except BaseException:
                result = AuthError(AuthCode.UNKNOWN, "Unable to complete this action. Try again.")
            if not self._closed:
                self.completed.emit(generation, action, result)

        future.add_done_callback(done)

    async def _access_check(self, session: AuthSession) -> AccessAuthorization:
        assert self.access is not None
        started = self.monotonic()
        snapshot = await self.access.snapshot(session)
        return AccessAuthorization(session, snapshot, started)

    async def _authorize(self, work: Coroutine[Any, Any, Any]) -> Any:
        session = await work
        if self.access is not None and isinstance(session, AuthSession):
            return await self._access_check(session)
        return session

    def _submit_form(self, action: str, payload: dict[str, Any]) -> None:
        if self._pending is not None and not self._pending.done():
            return
        if self.screen is None:
            return
        email = str(payload.get("email", ""))
        password = str(payload.get("password", ""))
        code = str(payload.get("code", ""))
        remember = bool(payload.get("remember", False))
        provider = self.service.provider
        work: Coroutine[Any, Any, Any]
        if action == "login":
            work = self.service.login(email, password, remember=remember)
        elif action == "signup":
            work = provider.signup(email, password)
        elif action == "verify_signup":
            work = self.service.verify_signup(email, code, remember=remember)
        elif action == "recover":
            work = provider.recover(email)
        elif action == "verify_recovery":
            work = provider.verify_recovery(email, code)
        elif action == "reset" and self._recovery is not None:
            work = self._reset_password(self._recovery, password)
        elif action == "restore":
            work = self.service.restore()
        else:
            return
        self.screen.set_busy(True)
        if action in {"login", "verify_signup", "restore"}:
            work = self._authorize(work)
        self._submit(action, work)

    async def _reset_password(self, recovery: AuthSession, password: str) -> None:
        await self.service.provider.change_password(recovery, password)

    def _finish(self, generation: int, action: str, result: object) -> None:
        if generation != self._generation or self._closed:
            return
        if action == "forget":
            if isinstance(result, AuthError):
                self._lock("Could not clear remembered login for Incognito. Restart and retry.")
            return
        if action == "access":
            self._access_pending = None
            if not self.access_lease.valid:
                self._lock("Account access expired. Reconnect and sign in again.")
                return
            if isinstance(result, AccessAuthorization):
                try:
                    self.access_lease.adopt(result, self.service.session)
                except AuthError as exc:
                    result = exc
                else:
                    self.notice.emit("")
            if isinstance(result, AuthError):
                if result.code == AuthCode.NETWORK:
                    self.access_lease.refresh_at = self.monotonic() + 2.0
                    self.notice.emit("Access connection interrupted. Reconnecting...")
                else:
                    self._lock(str(result))
            self.check_session()
            return
        self._pending = None
        if action == "refresh":
            if isinstance(result, AuthError):
                if result.code in (AuthCode.INVALID, AuthCode.UNCONFIRMED, AuthCode.STORAGE):
                    self._lock("Your session needs attention. Sign in again.")
                else:
                    self._next_retry = self.monotonic() + self._retry_delay
                    self._retry_delay = min(30.0, self._retry_delay * 2)
                    self.notice.emit("Account connection interrupted. Reconnecting...")
                    self.check_session()
            else:
                self._retry_delay = 2.0
                self._next_retry = self.monotonic() + 2.0
                self.notice.emit("")
            return
        screen = self.screen
        if screen is None:
            return
        screen.set_busy(False)
        if isinstance(result, AccessAuthorization):
            try:
                if not self.access_lease.adopt(result, self.service.session):
                    return
                result = result.session
            except AuthError as exc:
                result = exc
        if isinstance(result, AuthError):
            if self.access is not None and action in {"login", "verify_signup", "restore"}:
                self.service.revoke()
                self.access_lease.clear()
            screen.show_message(str(result), error=True)
            return
        if action in ("login", "verify_signup", "restore"):
            if isinstance(result, AuthSession):
                self._recovery = None
                screen.accept()
            else:
                screen.show_message("No remembered sign-in. Enter your email and password.")
        elif action == "signup":
            screen.set_stage("verify_signup")
            screen.show_message("If eligible, a confirmation code has been sent. Check your email.")
        elif action == "recover":
            screen.set_stage("verify_recovery")
            screen.show_message(
                "If an account exists, a reset code has been sent. Check your email."
            )
        elif action == "verify_recovery" and isinstance(result, AuthSession):
            self._recovery = result
            screen.set_stage("reset")
        elif action == "reset":
            self._recovery = None
            screen.set_stage("login")
            screen.show_message("Password updated. Sign in with your new password.")

    def check_session(self) -> None:
        session = self.service.session
        if self._closed:
            return
        self._observe_session()
        remaining = min(
            session.expires_at - self.clock() if session is not None else 0.0,
            self._deadline - self.monotonic(),
        )
        if session is None or remaining <= 0:
            self._lock("Your session has expired. Reconnect and sign in again.")
            return
        if self.access is not None:
            if not self.access_lease.valid:
                self._lock("Account access expired. Reconnect and sign in again.")
                return
            if self._access_pending is None and self.monotonic() >= self.access_lease.refresh_at:
                self._submit("access", self._access_check(session))
        if remaining > 60 or self.monotonic() < self._next_retry:
            return
        if self._pending is None:
            self._submit("refresh", self.service.refresh())

    def _observe_session(self) -> None:
        session = self.service.session
        if session is not None and session is not self._observed_session:
            self._observed_session = session
            self._deadline = self.monotonic() + max(0.0, session.expires_at - self.clock())

    def forget_remembered(self) -> None:
        self.service.disable_remembering()
        generation = self._generation
        future = self.runner.submit(self.service.forget_remembered())

        def finished(task: Future[Any]) -> None:
            result = None
            try:
                task.result()
            except Exception:
                result = AuthError(AuthCode.STORAGE, "Could not clear remembered login.")
            if not self._closed:
                self.completed.emit(generation, "forget", result)

        future.add_done_callback(finished)

    def _lock(self, message: str) -> None:
        if self.lock_message is not None:
            return
        self.lock_message = message
        self.invalidate()
        self.locked.emit(message)

    def invalidate(self) -> None:
        self.timer.stop()
        self._generation += 1
        self.service.revoke()
        self.access_lease.clear()
        self._recovery = None
        if self._pending is not None:
            self._pending.cancel()
            self._pending = None
        if self._access_pending is not None:
            self._access_pending.cancel()
            self._access_pending = None

    def shutdown(self, *, logout: bool = False) -> str | None:
        self.timer.stop()
        self._generation += 1
        self._closed = True
        if self._pending is not None:
            self._pending.cancel()
        if self._access_pending is not None:
            self._access_pending.cancel()
        self.access_lease.clear()
        error = None
        try:
            if logout:
                self.runner.submit(self.service.logout()).result(timeout=16)
        except Exception:
            error = "Signed out locally. Account-service or credential cleanup needs attention."
        finally:
            self.service.revoke()
            try:
                self.runner.submit(self.service.close()).result(timeout=3)
            except Exception:
                pass
            if self.access is not None:
                try:
                    self.runner.submit(self.access.close()).result(timeout=3)
                except Exception:
                    pass
            self.runner.stop()
        return error
