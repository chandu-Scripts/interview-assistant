import logging
from dataclasses import dataclass
from typing import Literal, Protocol

from interview_assistant.config.settings import Settings

log = logging.getLogger(__name__)

PROVIDERS = ("openai", "anthropic", "gemini")
SERVICE = "InterviewAssistant"
Source = Literal["env", "keyring", "none"]


class KeyStore(Protocol):
    def get(self, provider: str) -> str | None: ...
    def set(self, provider: str, key: str) -> None: ...
    def delete(self, provider: str) -> None: ...


class KeyringStore:
    """API keys in the Windows Credential Manager (encrypted for the current Windows user)."""

    def __init__(self, service: str = SERVICE) -> None:
        self._service = service

    def get(self, provider: str) -> str | None:
        try:
            import keyring

            return keyring.get_password(self._service, provider)
        except Exception:  # no backend / locked vault: behave as "not stored"
            log.warning("Could not read %s key from the credential store", provider)
            return None

    def set(self, provider: str, key: str) -> None:
        import keyring

        keyring.set_password(self._service, provider, key)

    def delete(self, provider: str) -> None:
        try:
            import keyring

            keyring.delete_password(self._service, provider)
        except Exception:  # not stored: nothing to delete
            log.debug("No stored %s key to delete", provider)


class MemoryKeyStore:
    """In-memory store for tests and demos."""

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self.data = dict(initial or {})

    def get(self, provider: str) -> str | None:
        return self.data.get(provider)

    def set(self, provider: str, key: str) -> None:
        self.data[provider] = key

    def delete(self, provider: str) -> None:
        self.data.pop(provider, None)


@dataclass(frozen=True)
class KeyStatus:
    provider: str
    source: Source
    hint: str  # masked, safe to show: "sk-...abcd"

    @property
    def present(self) -> bool:
        return self.source != "none"

    @property
    def editable(self) -> bool:
        return self.source != "env"  # environment/.env values win; edit them there

    def describe(self) -> str:
        return {
            "env": f"From environment / .env ({self.hint})",
            "keyring": f"Saved in Windows Credential Manager ({self.hint})",
            "none": "Not set",
        }[self.source]


def mask(key: str) -> str:
    key = key.strip()
    if len(key) <= 8:
        return "…" * 3
    return f"{key[:3]}…{key[-4:]}"


def key_status(provider: str, settings: Settings, store: KeyStore) -> KeyStatus:
    """Where the key comes from. Pass RAW settings (Settings(), not load_settings())."""
    env_key = getattr(settings, f"{provider}_api_key")
    if env_key is not None and env_key.get_secret_value():
        return KeyStatus(provider, "env", mask(env_key.get_secret_value()))
    stored = store.get(provider)
    if stored:
        return KeyStatus(provider, "keyring", mask(stored))
    return KeyStatus(provider, "none", "")


def all_status(settings: Settings, store: KeyStore) -> dict[str, KeyStatus]:
    return {p: key_status(p, settings, store) for p in PROVIDERS}
