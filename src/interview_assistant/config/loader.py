from pydantic import SecretStr

from interview_assistant.config.keys import PROVIDERS, KeyringStore, KeyStore
from interview_assistant.config.settings import Settings


def load_settings(store: KeyStore | None = None) -> Settings:
    """Settings from env/.env/settings.json, with API keys filled in from the credential store
    for any provider whose key is not already provided by the environment."""
    settings = Settings()
    store = store or KeyringStore()
    update: dict[str, SecretStr] = {}
    for provider in PROVIDERS:
        current = getattr(settings, f"{provider}_api_key")
        if current is None or not current.get_secret_value():
            stored = store.get(provider)
            if stored:
                update[f"{provider}_api_key"] = SecretStr(stored)
    return settings.model_copy(update=update) if update else settings
