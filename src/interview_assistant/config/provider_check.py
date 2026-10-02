"""Validate an API key and list the models a provider offers, using free 'list models' calls."""

from dataclasses import dataclass, field
from typing import Any

import httpx

TIMEOUT = 15.0
_OPENAI_SKIP = ("audio", "realtime", "transcribe", "tts", "image", "search", "instruct", "embed")
_GEMINI_SKIP = (
    "tts",
    "image",
    "embedding",
    "aqa",
    "native-audio",
    "live",
    "robotics",
    "deep-research",
    "antigravity",
    "computer-use",
    "learnlm",
)


@dataclass
class CheckResult:
    ok: bool
    message: str
    models: list[str] = field(default_factory=list)


def _openai_models(data: dict[str, Any]) -> list[str]:
    ids = [m["id"] for m in data.get("data", []) if isinstance(m.get("id"), str)]
    keep = [i for i in ids if i.startswith(("gpt-", "o1", "o3", "o4", "chatgpt"))]
    return sorted(i for i in keep if not any(s in i for s in _OPENAI_SKIP))


def _anthropic_models(data: dict[str, Any]) -> list[str]:
    return sorted(m["id"] for m in data.get("data", []) if isinstance(m.get("id"), str))


def _gemini_models(data: dict[str, Any]) -> list[str]:
    out = []
    for m in data.get("models", []):
        name = str(m.get("name", "")).removeprefix("models/")
        if "generateContent" in m.get("supportedGenerationMethods", []) and not any(
            s in name for s in _GEMINI_SKIP
        ):
            out.append(name)
    return sorted(out)


def _request(provider: str, key: str) -> tuple[str, dict[str, str], dict[str, Any]]:
    if provider == "openai":
        return "https://api.openai.com/v1/models", {"Authorization": f"Bearer {key}"}, {}
    if provider == "anthropic":
        return (
            "https://api.anthropic.com/v1/models",
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
            {"limit": 100},
        )
    if provider == "gemini":
        return (
            "https://generativelanguage.googleapis.com/v1beta/models",
            {"x-goog-api-key": key},  # header, never the URL
            {"pageSize": 200},
        )
    raise ValueError(f"unknown provider {provider!r}")


def check_key(provider: str, key: str, client: httpx.Client | None = None) -> CheckResult:
    """Ask the provider for its model list. Costs nothing; messages never contain the key."""
    key = key.strip()
    if not key:
        return CheckResult(False, "No key entered.")
    url, headers, params = _request(provider, key)
    owns = client is None
    client = client or httpx.Client(timeout=TIMEOUT)
    try:
        r = client.get(url, headers=headers, params=params)
    except httpx.TimeoutException:
        return CheckResult(False, f"{provider}: timed out - check your internet connection.")
    except httpx.HTTPError as e:
        return CheckResult(False, f"{provider}: could not connect ({type(e).__name__}).")
    finally:
        if owns:
            client.close()
    if r.status_code in (401, 403) or (r.status_code == 400 and provider == "gemini"):
        return CheckResult(False, f"{provider}: the key was rejected. Check that it is correct.")
    if r.status_code == 429:
        return CheckResult(True, f"{provider}: key accepted (currently rate limited).")
    if r.status_code >= 400:
        return CheckResult(False, f"{provider}: the service returned HTTP {r.status_code}.")
    try:
        data = r.json()
    except ValueError:
        return CheckResult(False, f"{provider}: unexpected response.")
    models = {"openai": _openai_models, "anthropic": _anthropic_models, "gemini": _gemini_models}[
        provider
    ](data)
    return CheckResult(True, f"{provider}: key works ({len(models)} models available).", models)
