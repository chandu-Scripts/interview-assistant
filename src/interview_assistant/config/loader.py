from pydantic import SecretStr

from interview_assistant.account.context import current_context
from interview_assistant.config.keys import KEY_PROVIDERS, KeyringStore, KeyStore, effective_key
from interview_assistant.config.relay import KeyringRelayTokenStore, RelayTokenStore
from interview_assistant.config.settings import Settings


def load_settings(
    store: KeyStore | None = None, relay_store: RelayTokenStore | None = None
) -> Settings:
    """Settings from env/.env/settings.json, with API keys (and the shared-access relay token)
    resolved using explicit in-app saved-key preferences, then the environment default."""
    settings = Settings()
    context = current_context()
    if context is not None:
        context.require_active()
    store = store or KeyringStore()
    update: dict[str, SecretStr | None] = {}
    for provider in KEY_PROVIDERS:
        key = effective_key(provider, settings, store)
        update[f"{provider}_api_key"] = SecretStr(key) if key else None
    if context is not None:
        # Customer subscriptions never opt into someone else's AI billing. Leave any
        # earlier stored relay credential untouched, but do not construct or read its store.
        update.update(relay_url=None, relay_token=None, relay_pending_id=None)
    elif settings.relay_token is None or not settings.relay_token.get_secret_value():
        relay_store = relay_store or KeyringRelayTokenStore()
        stored = relay_store.get()
        if stored:
            update["relay_token"] = SecretStr(stored)
    return settings.model_copy(update=update) if update else settings
