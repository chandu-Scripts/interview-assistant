"""Remembered-login credentials, with a fail-closed nonsecret restore marker."""

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import UUID

from interview_assistant.account.models import AuthCode, AuthError, AuthSession

log = logging.getLogger(__name__)


class CredentialBackend(Protocol):
    def get_password(self, service: str, username: str) -> str | None: ...
    def set_password(self, service: str, username: str, password: str) -> None: ...
    def delete_password(self, service: str, username: str) -> None: ...


@dataclass(frozen=True)
class RememberedSession:
    user_id: str
    refresh_token: str = field(repr=False)


class RefreshVault:
    """Only refresh credentials enter Credential Manager, never ordinary files.

    A marker is disabled before rotating or deleting a credential. A crash or
    failed deletion therefore cannot silently restore a signed-out account.
    """

    def __init__(
        self,
        project: str,
        tombstone_path: Path,
        *,
        backend: CredentialBackend | None = None,
    ) -> None:
        if not project or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in project):
            raise ValueError("Invalid authentication project namespace")
        if backend is None:
            import keyring

            backend = keyring
        self._backend = backend
        self._service = f"InterviewAssistantAuth:{project}"
        self._path = tombstone_path
        self._lock = threading.RLock()

    def _marker(self) -> dict[str, str | bool] | None:
        if not self._path.exists():
            return None
        data = json.loads(self._path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("active"), bool):
            raise ValueError("Invalid restore marker")
        if not data["active"] and "user_id" not in data:
            return {"active": False}
        if not isinstance(data.get("user_id"), str):
            raise ValueError("Invalid restore identity")
        user = str(UUID(data["user_id"]))
        return {"active": data["active"], "user_id": user}

    def _marker_or_repair(self) -> dict[str, str | bool] | None:
        try:
            return self._marker()
        except (ValueError, KeyError, TypeError):
            # Do not guess an identity or enumerate unrelated OS credentials.
            self._write(None, active=False)
            log.warning(
                "Damaged remembered-login marker disabled; "
                "unreferenced credentials were not removed"
            )
            return {"active": False}

    def _write(self, user_id: str | None, *, active: bool) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(self._path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            marker: dict[str, str | bool] = {"active": active}
            if user_id is not None:
                marker["user_id"] = user_id
            json.dump(marker, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self._path)

    def load(self) -> RememberedSession | None:
        with self._lock:
            try:
                marker = self._marker_or_repair()
                if marker is None or not marker["active"]:
                    return None
                user = str(marker["user_id"])
                token = self._backend.get_password(self._service, user)
                return RememberedSession(user, token) if token else None
            except Exception:
                raise AuthError(
                    AuthCode.STORAGE, "Cannot read remembered login. Try signing in again."
                ) from None

    @property
    def restore_blocked(self) -> bool:
        """Whether a fresh process is unable to restore this vault credential."""
        try:
            return self.load() is None
        except AuthError:
            return False

    def save(self, session: AuthSession) -> None:
        with self._lock:
            try:
                user = str(UUID(session.user_id))
                previous = self._marker_or_repair()
                if previous and previous.get("user_id") != user:
                    self.clear()
                self._write(user, active=False)
                self._backend.set_password(self._service, user, session.refresh_token)
                self._write(user, active=True)
            except Exception:
                raise AuthError(
                    AuthCode.STORAGE, "Could not remember this login. Sign in without Remember me."
                ) from None

    def clear(self) -> None:
        with self._lock:
            try:
                marker = self._marker_or_repair()
                if marker is None:
                    return
                if "user_id" not in marker:
                    return
                user = str(marker["user_id"])
                self._write(user, active=False)
                # The tombstone remains even when Credential Manager is unavailable.
                if self._backend.get_password(self._service, user) is not None:
                    self._backend.delete_password(self._service, user)
            except Exception:
                raise AuthError(
                    AuthCode.STORAGE,
                    "Signed out locally, but credential cleanup failed. Retry logout.",
                ) from None
