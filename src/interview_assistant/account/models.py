"""Shared account contracts. Never include credentials in representations."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class AuthCode(StrEnum):
    INVALID = "invalid"
    UNCONFIRMED = "unconfirmed"
    NETWORK = "network"
    RATE_LIMIT = "rate_limit"
    CONFIGURATION = "configuration"
    STORAGE = "storage"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class AuthError(Exception):
    def __init__(self, code: AuthCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AuthSession:
    user_id: str
    expires_at: float
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)


class AuthProvider(Protocol):
    async def login(self, email: str, password: str) -> AuthSession: ...
    async def signup(self, email: str, password: str) -> None: ...
    async def verify_signup(self, email: str, code: str) -> AuthSession: ...
    async def recover(self, email: str) -> None: ...
    async def verify_recovery(self, email: str, code: str) -> AuthSession: ...
    async def change_password(self, session: AuthSession, password: str) -> None: ...
    async def refresh(self, refresh_token: str) -> AuthSession: ...
    async def validate(self, session: AuthSession) -> None: ...
    async def signout(self, session: AuthSession) -> None: ...
    async def close(self) -> None: ...
