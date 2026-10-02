"""Client + token storage for the friend side of "shared access": requesting approval from
someone else's relay, polling for the result, and holding the token once issued.

Kept separate from config/keys.py's PROVIDERS/KeyStore: a relay token is a different kind of
credential (issued by someone else's one-click approval, for someone else's server) from "your
own AI provider key from platform.openai.com" - the UI for it (ui/relay_panel.py) is its own
small panel, not a 4th row in the provider-key list.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

import httpx

log = logging.getLogger(__name__)

RELAY_SERVICE = "InterviewAssistantRelay"
_TOKEN_KEY = "token"
TIMEOUT = 15.0


class RelayTokenStore(Protocol):
    def get(self) -> str | None: ...
    def set(self, token: str) -> None: ...
    def delete(self) -> None: ...


class KeyringRelayTokenStore:
    """The relay token in the Windows Credential Manager, under its own service name (so it
    never gets mixed in with the 3 provider keys in config/keys.py)."""

    def __init__(self, service: str = RELAY_SERVICE) -> None:
        self._service = service

    def get(self) -> str | None:
        try:
            import keyring

            return keyring.get_password(self._service, _TOKEN_KEY)
        except Exception:  # no backend / locked vault: behave as "not connected"
            log.warning("Could not read the relay token from the credential store")
            return None

    def set(self, token: str) -> None:
        import keyring

        keyring.set_password(self._service, _TOKEN_KEY, token)

    def delete(self) -> None:
        try:
            import keyring

            keyring.delete_password(self._service, _TOKEN_KEY)
        except Exception:  # not stored: nothing to delete
            log.debug("No stored relay token to delete")


class MemoryRelayTokenStore:
    """In-memory store for tests."""

    def __init__(self, initial: str | None = None) -> None:
        self.token = initial

    def get(self) -> str | None:
        return self.token

    def set(self, token: str) -> None:
        self.token = token

    def delete(self) -> None:
        self.token = None


@dataclass(frozen=True)
class RequestResult:
    public_id: str


@dataclass(frozen=True)
class StatusResult:
    status: str  # "pending" | "approved" | "denied"
    token: str | None = None  # non-None exactly once: the first poll after approval
    budget_usd: float | None = None
    budget_scope: str | None = None


def request_access(
    base_url: str, name: str, note: str = "", client: httpx.Client | None = None
) -> RequestResult:
    """POSTs the relay's friend-facing /requests endpoint. Raises httpx.HTTPError (or whatever
    httpx raises for a transport failure) on any problem - callers run this off the UI thread
    (ui/bg.py) and report the exception's type rather than letting it crash the app."""
    owns = client is None
    client = client or httpx.Client(timeout=TIMEOUT)
    try:
        r = client.post(f"{base_url.rstrip('/')}/requests", json={"name": name, "note": note})
        r.raise_for_status()
        return RequestResult(public_id=r.json()["public_id"])
    finally:
        if owns:
            client.close()


def check_status(base_url: str, public_id: str, client: httpx.Client | None = None) -> StatusResult:
    """GETs the relay's friend-facing /requests/{id} endpoint. A token in the result is
    delivered at most once - the relay clears it server-side the instant it's read, so the
    caller must store it immediately rather than polling again to "re-fetch" it."""
    owns = client is None
    client = client or httpx.Client(timeout=TIMEOUT)
    try:
        r = client.get(f"{base_url.rstrip('/')}/requests/{public_id}")
        r.raise_for_status()
        d = r.json()
        return StatusResult(d["status"], d.get("token"), d.get("budget_usd"), d.get("budget_scope"))
    finally:
        if owns:
            client.close()
