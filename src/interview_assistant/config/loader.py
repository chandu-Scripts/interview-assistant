from pydantic import SecretStr

from interview_assistant.config.keys import KEY_PROVIDERS, KeyringStore, KeyStore, effective_key
from interview_assistant.config.relay import KeyringRelayTokenStore, RelayTokenStore
from interview_assistant.config.settings import Settings


def load_settings(
    store: KeyStore | None = None, relay_store: RelayTokenStore | None = None
) -> Settings:
    """Settings from env/.env/settings.json, with API keys (and the shared-access relay token)
    resolved using explicit in-app saved-key preferences, then the environment default."""
    settings = Settings()
    store = store or KeyringStore()
    relay_store = relay_store or KeyringRelayTokenStore()
    update: dict[str, SecretStr | None] = {}
    for provider in KEY_PROVIDERS:
        key = effective_key(provider, settings, store)
        update[f"{provider}_api_key"] = SecretStr(key) if key else None
    if settings.relay_token is None or not settings.relay_token.get_secret_value():
        stored = relay_store.get()
        if stored:
            update["relay_token"] = SecretStr(stored)
    return settings.model_copy(update=update) if update else settings
