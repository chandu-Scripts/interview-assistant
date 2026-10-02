"""Server configuration, entirely from environment variables (12-factor style: no secret ever
lives in a file that gets committed or shipped)."""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class RelaySettings:
    # Real OpenAI key: lives ONLY here, server-side. Never sent to, or logged for, any client.
    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    # Gates /admin*. A single owner-operated panel, so a single shared secret is enough (checked
    # with a constant-time comparison via HTTP Basic auth).
    admin_token: str = field(default_factory=lambda: os.environ.get("ADMIN_TOKEN", ""))
    # What "fast"/"smart" (the only two values a client may request) resolve to. Centralizing this
    # here means no client can ever pick an arbitrary real model or bypass the mapping.
    model_fast: str = field(default_factory=lambda: os.environ.get("MODEL_FAST", "gpt-4o-mini"))
    model_smart: str = field(default_factory=lambda: os.environ.get("MODEL_SMART", "gpt-4o"))
    max_tokens_ceiling: int = field(
        default_factory=lambda: int(os.environ.get("MAX_TOKENS_CEILING", "1600"))
    )
    default_budget_usd: float = field(
        default_factory=lambda: float(os.environ.get("DEFAULT_BUDGET_USD", "2.0"))
    )
    default_budget_scope: str = field(
        default_factory=lambda: os.environ.get("DEFAULT_BUDGET_SCOPE", "day")
    )
    database_path: Path = field(
        default_factory=lambda: Path(os.environ.get("RELAY_DB_PATH", "relay.db"))
    )

    def require_ready(self) -> list[str]:
        """Names of required settings that are missing (empty). The app still starts without
        them so /admin and tests work, but the proxy route (added in the next step) must refuse
        to serve real traffic until these are set."""
        missing = []
        if not self.admin_token:
            missing.append("ADMIN_TOKEN")
        if not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        return missing


def load_settings() -> RelaySettings:
    return RelaySettings()
