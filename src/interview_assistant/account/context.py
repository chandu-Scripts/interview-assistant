"""Immutable account scope and revocable authority for local persistence."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from threading import Event, RLock
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from PySide6.QtCore import QSettings


@dataclass(frozen=True)
class AccountContext:
    root: Path
    issuer: str
    user_id: str
    _revoked: Event = field(default_factory=Event, init=False, repr=False, compare=False)
    _lock: RLock = field(default_factory=RLock, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if str(UUID(self.user_id)) != self.user_id:
            raise ValueError("Account identity must be a canonical UUID")
        parsed = urlsplit(self.issuer)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Account issuer must be an HTTPS URL")
        issuer = urlunsplit(("https", parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))
        object.__setattr__(self, "issuer", issuer)
        object.__setattr__(self, "root", self.root.resolve())
        self.validate_path(self.account_dir)

    @property
    def namespace(self) -> str:
        return sha256(self.issuer.encode("utf-8")).hexdigest()[:32]

    @property
    def account_dir(self) -> Path:
        return self.root / "accounts" / self.namespace / self.user_id

    @property
    def credential_namespace(self) -> str:
        return f"{self.namespace}:{self.user_id}"

    @property
    def active(self) -> bool:
        return not self._revoked.is_set()

    @property
    def persistence_lock(self) -> RLock:
        """Store transactions share the revocation lock to close check/write races."""
        return self._lock

    def revoke(self) -> None:
        with self._lock:
            self._revoked.set()

    def require_active(self) -> None:
        if not self.active:
            raise PermissionError("Account session is no longer active")

    def validate_path(self, path: Path) -> None:
        """Reject traversal and existing symlink/junction redirects below the root."""
        expected = path.absolute()
        if not expected.is_relative_to(self.account_dir) or expected.resolve() != expected:
            raise ValueError("Account storage path escapes its workspace")

    @contextmanager
    def mutation(self) -> Iterator[None]:
        # Serialize revocation with the final disk/vault operation, not merely its scheduling.
        with self._lock:
            self.require_active()
            self.validate_path(self.account_dir)
            yield

    def qsettings(self, scope: str) -> QSettings:
        if not scope or not scope.replace("_", "").replace("-", "").isalnum():
            raise ValueError("Invalid settings scope")
        with self.mutation():
            path = self.account_dir / f"{scope}.ini"
            self.validate_path(path)
            return _AccountQSettings(path, self)


class _AccountQSettings(QSettings):
    def __init__(self, path: Path, context: AccountContext) -> None:
        super().__init__(str(path), QSettings.Format.IniFormat)
        self._context = context
        self._path = path
        self.setFallbacksEnabled(False)

    def setValue(self, key: str, value: object) -> None:
        with self._context.mutation():
            self._context.validate_path(self._path)
            super().setValue(key, value)
            super().sync()

    def remove(self, key: str) -> None:
        with self._context.mutation():
            self._context.validate_path(self._path)
            super().remove(key)
            super().sync()

    def clear(self) -> None:
        with self._context.mutation():
            self._context.validate_path(self._path)
            super().clear()
            super().sync()


_context: AccountContext | None = None
_activation_lock = RLock()


def activate_context(context: AccountContext) -> None:
    """Install the verified identity once. Account changes require a new process."""
    global _context
    with _activation_lock:
        if _context is not None:
            raise RuntimeError("Account context is already installed; restart to change accounts")
        context.require_active()
        _context = context


def current_context() -> AccountContext | None:
    # Keep revoked identity installed, so a late callback cannot fall back to legacy storage.
    return _context
