from typing import Literal

from pydantic import SecretStr
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from interview_assistant.config.user_settings import JsonUserSource

DEFAULT_VOCABULARY = (
    "Software engineering interview. Terms: API, REST, GraphQL, SQL, NoSQL, Kubernetes, "
    "Docker, Redis, Kafka, OAuth, JWT, latency, throughput, microservices, rate limiter, "
    "load balancer, Python, Java, TypeScript."
)


class Settings(BaseSettings):
    """App settings. Precedence (highest first): explicit values, environment variables, .env,
    the user's saved settings.json. API keys additionally come from Windows Credential Manager
    (see config/loader.py). Keys are never logged."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            JsonUserSource(settings_cls),
            file_secret_settings,
        )

    log_level: str = "INFO"
    skip_onboarding: bool = False  # automation / managed installs: never show the first-run wizard
    stt_provider: Literal["local", "openai"] = "local"
    whisper_model: str = "base.en"
    stt_language: str | None = "en"
    default_audio_kind: Literal["loopback", "mic"] = "loopback"  # source pre-selected at start
    # Capture system audio AND the microphone at the same time instead of picking one
    # (default_audio_kind is ignored when this is on - both are always loopback+mic). Loads a
    # second local speech-recognition model if stt_provider="local" (~2x memory).
    dual_audio_capture: bool = True
    # Hint for the local speech model (measured: halves technical-term errors). Empty = off.
    stt_vocabulary: str | None = DEFAULT_VOCABULARY
    openai_model: str = "gpt-4o-mini"
    anthropic_model: str = "claude-sonnet-5"
    gemini_model: str = "gemini-flash-latest"
    gemini_fast_model: str = "gemini-3.1-flash-lite"
    anthropic_fast_model: str = "claude-haiku-4-5-20251001"
    openai_smart_model: str = "gpt-4o"
    # Optional chain overrides, e.g. ROUTER_FAST="openai:gpt-4o-mini,gemini:gemini-flash-latest"
    router_fast: str | None = None
    router_smart: str | None = None
    router_first_token_timeout_s: float = 10.0
    # Candidate background injected into every answer prompt (resume summary, target role).
    candidate_profile: str | None = None
    jev_classify_timeout_s: float = 1.0
    # "manual": a detected voice question waits for the user to click Answer/Ctrl+Enter before
    # it's sent to the AI. "auto": answered immediately, the old always-on behavior. Typed
    # questions and Chat always answer immediately regardless of this setting.
    answer_mode: Literal["manual", "auto"] = "manual"
    # Cost tracking. budget_usd=None disables budgets; hard stop blocks new answers once exceeded.
    budget_usd: float | None = None
    budget_scope: Literal["session", "day", "month"] = "session"
    budget_hard_stop: bool = False
    usage_log: bool = True
    # Conversation history (questions + answers) is stored locally in SQLite.
    history_enabled: bool = True
    # Privacy
    incognito: bool = False  # start with nothing saved (togglable in the app)
    redact_pii: bool = True  # mask emails/phones/cards/keys in requests to AI providers
    require_consent: bool = True  # recording notice before the first capture
    hotkey_presentation: str = "ctrl+alt+p"  # toggle presentation mode; empty = disabled
    hotkey_overlay: str = "ctrl+alt+o"  # show/hide the overlay; empty = disabled
    hotkey_bubble: str = "ctrl+h"  # collapse/restore to a small bubble; empty = disabled
    hotkey_answer: str = "ctrl+enter"  # answer the waiting question from any window; empty = off
    hotkey_screenshot: str = "ctrl+shift+enter"  # attach a screenshot from any window; empty = off
    hotkey_chat: str = "ctrl+shift+space"  # show/hide the Chat box from any window; empty = off
    history_retention_days: int | None = None  # auto-delete sessions older than this
    data_dir: str | None = None  # default: %APPDATA%/InterviewAssistant
    pricing_file: str | None = None  # JSON overrides, see cost/pricing.py
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    # Shared access (config/relay.py): connecting to someone else's relay instead of using your
    # own provider keys. relay_url/relay_pending_id are ordinary settings; relay_token is a
    # credential and is NEVER written to settings.json (see config/loader.py).
    relay_url: str | None = None
    relay_pending_id: str | None = None  # an outstanding access request awaiting approval
    relay_token: SecretStr | None = None
