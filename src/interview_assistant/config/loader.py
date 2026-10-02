from pydantic import SecretStr

from interview_assistant.config.keys import PROVIDERS, KeyringStore, KeyStore
from interview_assistant.config.relay import KeyringRelayTokenStore, RelayTokenStore
from interview_assistant.config.settings import Settings


def load_settings(
    store: KeyStore | None = None, relay_store: RelayTokenStore | None = None
) -> Settings:
    """Settings from env/.env/settings.json, with API keys (and the shared-access relay token)
    filled in from the credential store for whichever ones aren't already provided by the
    environment."""
    settings = Settings()
    store = store or KeyringStore()
    relay_store = relay_store or KeyringRelayTokenStore()
    update: dict[str, SecretStr] = {}
    for provider in PROVIDERS:
        current = getattr(settings, f"{provider}_api_key")
        if current is None or not current.get_secret_value():
            stored = store.get(provider)
            if stored:
                update[f"{provider}_api_key"] = SecretStr(stored)
    if settings.relay_token is None or not settings.relay_token.get_secret_value():
        stored = relay_store.get()
        if stored:
            update["relay_token"] = SecretStr(stored)
    return settings.model_copy(update=update) if update else settings
