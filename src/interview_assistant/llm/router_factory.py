from collections.abc import Callable, Mapping

from interview_assistant.account.context import current_context
from interview_assistant.config.keys import PROVIDERS
from interview_assistant.config.settings import Settings
from interview_assistant.llm.base import LLMProvider
from interview_assistant.llm.factory import build_providers
from interview_assistant.llm.router import AttemptRecord, Route, Router, RouterConfig


def parse_chain(spec: str) -> list[Route]:
    """'openai:gpt-4o-mini, gemini:gemini-flash-latest' -> [Route, Route]."""
    routes = []
    for item in spec.split(","):
        item = item.strip()
        if not item:
            continue
        provider, sep, model = item.partition(":")
        if not sep or not provider.strip() or not model.strip():
            raise ValueError(f"bad route {item!r}; expected provider:model")
        routes.append(Route(provider.strip().lower(), model.strip()))
    return routes


def default_chains(s: Settings) -> dict[str, list[Route]]:
    context = current_context()
    if context is not None:
        context.require_active()
    if context is None and s.relay_url and s.relay_token:
        # Connected via someone else's shared access (config/relay.py): route everything through
        # them instead of needing your own provider keys at all.
        fast, smart = "relay:fast", "relay:smart"
    else:
        # fast: low latency first (measured: gemini-flash-latest took 5-29 s to first token).
        fast = (
            f"openai:{s.openai_model},gemini:{s.gemini_fast_model},"
            f"anthropic:{s.anthropic_fast_model}"
        )
        # smart: quality first.
        smart = (
            f"anthropic:{s.anthropic_model},openai:{s.openai_smart_model},gemini:{s.gemini_model}"
        )
    chains = {
        "fast": parse_chain(s.router_fast or fast),
        "smart": parse_chain(s.router_smart or smart),
    }
    if context is not None:
        # Old or imported relay-only preferences fall back to the customer's own
        # provider presets. A missing customer key still leaves no usable route.
        for tier, default in (("fast", fast), ("smart", smart)):
            chains[tier] = [route for route in chains[tier] if route.provider in PROVIDERS]
            if not chains[tier]:
                chains[tier] = parse_chain(default)
    return chains


def build_router(
    settings: Settings,
    providers: Mapping[str, LLMProvider] | None = None,
    on_attempt: Callable[[AttemptRecord], None] | None = None,
) -> Router:
    context = current_context()
    if context is not None:
        context.require_active()
    providers = build_providers(settings) if providers is None else providers
    if context is not None:
        # Copy even injected maps: later mutation must not introduce a relay that a
        # historical preferred route could otherwise select outside the default chains.
        providers = {name: provider for name, provider in providers.items() if name in PROVIDERS}
    config = RouterConfig(
        chains=default_chains(settings),
        first_token_timeout_s=settings.router_first_token_timeout_s,
    )
    return Router(providers, config, on_attempt)
