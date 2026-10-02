from interview_assistant.config.settings import Settings
from interview_assistant.llm.anthropic_llm import AnthropicProvider
from interview_assistant.llm.base import LLMProvider
from interview_assistant.llm.gemini_llm import GeminiProvider
from interview_assistant.llm.openai_llm import OpenAIProvider


def build_providers(settings: Settings) -> dict[str, LLMProvider]:
    """Providers for which an API key is configured, keyed by name."""
    out: dict[str, LLMProvider] = {}
    if settings.openai_api_key:
        out["openai"] = OpenAIProvider(
            settings.openai_api_key.get_secret_value(), settings.openai_model
        )
    if settings.anthropic_api_key:
        out["anthropic"] = AnthropicProvider(
            settings.anthropic_api_key.get_secret_value(), settings.anthropic_model
        )
    if settings.gemini_api_key:
        out["gemini"] = GeminiProvider(
            settings.gemini_api_key.get_secret_value(), settings.gemini_model
        )
    if settings.relay_url and settings.relay_token:
        # OpenAIProvider's base_url param already speaks the relay's wire format exactly - no new
        # client code needed. "fast"/"smart" are sent as the literal model string; the relay (not
        # this client) decides what real model that maps to.
        out["relay"] = OpenAIProvider(
            settings.relay_token.get_secret_value(),
            "fast",
            base_url=f"{settings.relay_url.rstrip('/')}/v1/chat/completions",
        )
    return out
