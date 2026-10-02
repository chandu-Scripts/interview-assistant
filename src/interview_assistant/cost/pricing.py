import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

log = logging.getLogger(__name__)

V = TypeVar("V")

# Rates verified against the vendors' public pricing pages on this date. Prices change: override
# with a pricing file (see PriceBook.load_overrides) instead of editing this table.
PRICES_AS_OF = "2026-09-27"


@dataclass(frozen=True)
class TokenPrice:
    input_per_m: float  # USD per 1M input tokens
    output_per_m: float  # USD per 1M output tokens

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input_per_m + output_tokens * self.output_per_m) / 1_000_000


# (provider, model-name prefix) -> price. The longest matching prefix wins, so "gpt-4o-mini"
# beats "gpt-4o" and dated model ids (claude-haiku-4-5-20251001) match their family.
DEFAULT_TOKEN_PRICES: dict[tuple[str, str], TokenPrice] = {
    ("openai", "gpt-4o-mini"): TokenPrice(0.15, 0.60),
    ("openai", "gpt-4o"): TokenPrice(2.50, 10.00),
    ("anthropic", "claude-sonnet-5"): TokenPrice(2.00, 10.00),
    ("anthropic", "claude-sonnet-4"): TokenPrice(3.00, 15.00),
    ("anthropic", "claude-haiku-4-5"): TokenPrice(1.00, 5.00),
    ("anthropic", "claude-opus-5-5"): TokenPrice(4.00, 20.00),
    ("anthropic", "claude-opus-5"): TokenPrice(5.00, 25.00),
    ("anthropic", "claude-opus-4"): TokenPrice(5.00, 25.00),
    ("anthropic", "claude-fable-5"): TokenPrice(10.00, 50.00),
    ("gemini", "gemini-3.1-flash-lite"): TokenPrice(0.25, 1.50),
    ("gemini", "gemini-3.5-flash-lite"): TokenPrice(0.30, 2.50),
    ("gemini", "gemini-3.5-flash"): TokenPrice(1.50, 9.00),
    ("gemini", "gemini-3.6-flash"): TokenPrice(0.75, 3.75),  # promo through 2026-12-31
    ("gemini", "gemini-3.7-flash"): TokenPrice(0.75, 3.75),
    ("gemini", "gemini-3.8-flash"): TokenPrice(0.75, 3.75),
    ("gemini", "gemini-2.5-flash-lite"): TokenPrice(0.10, 0.40),
    ("gemini", "gemini-2.5-flash"): TokenPrice(0.30, 2.50),
    # Alias for the newest Flash model; priced as that model (an estimate that can drift).
    ("gemini", "gemini-flash-latest"): TokenPrice(0.75, 3.75),
}

# USD per audio minute for cloud speech-to-text; local models are free.
DEFAULT_AUDIO_PRICES: dict[tuple[str, str], float] = {
    ("openai", "gpt-4o-mini-transcribe"): 0.003,
    ("openai", "whisper-1"): 0.006,
    ("local-whisper", ""): 0.0,
}


def _longest_prefix(table: dict[tuple[str, str], V], provider: str, model: str) -> V | None:
    best: tuple[int, V] | None = None
    for (p, prefix), value in table.items():
        if p == provider and model.startswith(prefix) and (best is None or len(prefix) > best[0]):
            best = (len(prefix), value)
    return best[1] if best else None


class PriceBook:
    def __init__(
        self,
        tokens: dict[tuple[str, str], TokenPrice] | None = None,
        audio: dict[tuple[str, str], float] | None = None,
    ) -> None:
        self._tokens = dict(DEFAULT_TOKEN_PRICES if tokens is None else tokens)
        self._audio = dict(DEFAULT_AUDIO_PRICES if audio is None else audio)

    def token_price(self, provider: str, model: str) -> TokenPrice | None:
        return _longest_prefix(self._tokens, provider, model)

    def audio_price_per_min(self, provider: str, model: str) -> float | None:
        return _longest_prefix(self._audio, provider, model)

    def llm_cost(self, provider: str, model: str, input_tokens: int, output_tokens: int):
        """USD, or None when the model has no known price (never guess zero)."""
        price = self.token_price(provider, model)
        return None if price is None else price.cost(input_tokens, output_tokens)

    def stt_cost(self, provider: str, model: str, seconds: float) -> float | None:
        per_min = self.audio_price_per_min(provider, model)
        return None if per_min is None else per_min * seconds / 60

    def load_overrides(self, path: Path) -> int:
        """Merge a JSON file: {"tokens": {"provider:model-prefix": {"input": 1, "output": 2}},
        "audio_per_min": {"provider:model": 0.004}}. Returns the number of entries applied."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("Could not read pricing overrides %s: %s", path, e)
            return 0
        n = 0
        for key, v in (data.get("tokens") or {}).items():
            provider, _, prefix = key.partition(":")
            try:
                self._tokens[(provider, prefix)] = TokenPrice(float(v["input"]), float(v["output"]))
                n += 1
            except (KeyError, TypeError, ValueError):
                log.warning("Ignoring bad pricing entry %r", key)
        for key, v in (data.get("audio_per_min") or {}).items():
            provider, _, model = key.partition(":")
            try:
                self._audio[(provider, model)] = float(v)
                n += 1
            except (TypeError, ValueError):
                log.warning("Ignoring bad audio pricing entry %r", key)
        return n
