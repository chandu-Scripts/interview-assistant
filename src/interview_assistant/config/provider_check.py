"""Validate an API key and list the models a provider offers, using free 'list models' calls."""

from dataclasses import dataclass, field
from typing import Any

import httpx

TIMEOUT = 15.0
_OPENAI_SKIP = (
    "audio",
    "realtime",
    "transcribe",
    "tts",
    "image",
    "search",
    "instruct",
    "embed",
    "codex",
    "deep-research",
    "computer-use",
    "-pro",
)
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
    # Only an explicit authentication rejection is red; transport/quota/permission are amber.
    kind: str = "unknown"


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


def _failure(provider: str, response: httpx.Response) -> CheckResult:
    """Never echo a remote error message: it may contain credentials or user input."""
    code = ""
    try:
        body = response.json()
        err = body.get("error", {}) if isinstance(body, dict) else {}
        if isinstance(err, dict):
            code = str(err.get("code", "")) + " " + str(err.get("type", ""))
            for detail in (
                err.get("details", []) if isinstance(err.get("details", []), list) else []
            ):
                if isinstance(detail, dict):
                    code += " " + str(detail.get("reason", ""))
    except ValueError:
        pass
    if response.status_code == 401 or "API_KEY_INVALID" in code:
        return CheckResult(
            False, f"{provider}: key rejected. Check or replace the key.", kind="invalid"
        )
    if any(term in code.lower() for term in ("quota", "credit", "spend_limit", "usage_limit")):
        return CheckResult(
            False, f"{provider}: credits or usage limit reached. Check billing.", kind="quota"
        )
    if response.status_code == 429:
        return CheckResult(
            False, f"{provider}: rate limited. Wait and test again.", kind="rate_limit"
        )
    if response.status_code == 403:
        return CheckResult(
            False,
            f"{provider}: permission or region restriction. Check account access.",
            kind="permission",
        )
    return CheckResult(
        False, f"{provider}: the service returned HTTP {response.status_code}.", kind="service"
    )


def check_key(provider: str, key: str, client: httpx.Client | None = None) -> CheckResult:
    """Ask the provider for its model list. Costs nothing; messages never contain the key."""
    key = key.strip()
    if not key:
        return CheckResult(False, "No key entered.")
    url, headers, params = _request(provider, key)
    owns = client is None
    client = client or httpx.Client(timeout=TIMEOUT)
    models: set[str] = set()
    cursors: set[str] = set()
    try:
        for _ in range(10):
            r = client.get(url, headers=headers, params=params)
            if not r.is_success:
                return _failure(provider, r)
            try:
                data = r.json()
                field_name = "models" if provider == "gemini" else "data"
                if not isinstance(data, dict) or not isinstance(data.get(field_name), list):
                    raise ValueError("not a model list")
                if any(not isinstance(m, dict) for m in data[field_name]):
                    raise ValueError("not a model record")
                models.update(
                    {
                        "openai": _openai_models,
                        "anthropic": _anthropic_models,
                        "gemini": _gemini_models,
                    }[provider](data)
                )
            except (ValueError, TypeError, KeyError):
                return CheckResult(False, f"{provider}: unexpected response.", kind="service")
            cursor = (
                data.get("nextPageToken")
                if provider == "gemini"
                else (
                    data.get("last_id")
                    if provider == "anthropic" and data.get("has_more")
                    else None
                )
            )
            if not cursor:
                if provider == "anthropic" and data.get("has_more"):
                    break
                return CheckResult(
                    True,
                    f"Key verified ({len(models)} models listed). Credits and generation access "
                    "are not checked by this free test.",
                    sorted(models),
                    "verified",
                )
            if not isinstance(cursor, str) or cursor in cursors:
                break
            cursors.add(cursor)
            params["pageToken" if provider == "gemini" else "after_id"] = cursor
        return CheckResult(
            False, f"{provider}: incomplete model list. Please retry.", kind="service"
        )
    except httpx.TimeoutException:
        return CheckResult(
            False, f"{provider}: timed out - check your internet connection.", kind="network"
        )
    except httpx.HTTPError as e:
        return CheckResult(
            False, f"{provider}: could not connect ({type(e).__name__}).", kind="network"
        )
    finally:
        if owns:
            client.close()
