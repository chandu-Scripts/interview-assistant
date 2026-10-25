"""Shared, credential-bound model discovery for Settings and session preferences.

Only model ids, timestamps and one-way key fingerprints are persisted. No credentials.
Opening a screen never sends a request; verification is an explicit user action.
"""

import hashlib
import json
import os
import re
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Signal

from interview_assistant.config.keys import PROVIDERS, KeyStore, effective_key
from interview_assistant.config.provider_check import CheckResult
from interview_assistant.config.settings import Settings

NAMES = {"openai": "OpenAI", "anthropic": "Anthropic (Claude)", "gemini": "Google (Gemini)"}
MODEL_FIELDS = {
    "openai": ("openai_model", "openai_smart_model"),
    "anthropic": ("anthropic_fast_model", "anthropic_model"),
    "gemini": ("gemini_fast_model", "gemini_model"),
}
SUGGESTIONS = {"openai": ["gpt-5.4-mini", "gpt-5.4"]}
VERIFICATION_TTL = 24 * 60 * 60


def fingerprint(key: str) -> str:
    return hashlib.sha256(key.strip().encode()).hexdigest() if key.strip() else ""


def clean_models(models: list[str]) -> list[str]:
    return sorted({m for m in models if isinstance(m, str) and re.fullmatch(r"[\w./-]{1,160}", m)})


class ProviderCatalog(QObject):
    changed = Signal()

    def __init__(
        self,
        raw: Callable[[], Settings],
        keys: KeyStore,
        path: Path | None = None,
        *,
        clock: Callable[[], float] = time.time,
        can_write: Callable[[], bool] = lambda: True,
    ) -> None:
        super().__init__()
        self.raw, self.keys, self.path = raw, keys, path
        self.clock, self.can_write = clock, can_write
        self._records: dict[str, dict[str, Any]] = {}
        self._issues: dict[str, tuple[str, CheckResult]] = {}
        self.persistence_error = ""
        if path is not None:
            try:
                records = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(records, dict):
                    self._records = {
                        p: r for p, r in records.items() if p in PROVIDERS and isinstance(r, dict)
                    }
            except (OSError, ValueError):
                pass

    def key(self, provider: str) -> str:
        return effective_key(provider, self.raw(), self.keys)

    def status(self, provider: str) -> tuple[str, str]:
        token = fingerprint(self.key(provider))
        if not token:
            return "missing", "No key saved"
        issue = self._issues.get(provider)
        if issue and issue[0] == token:
            return ("invalid" if issue[1].kind == "invalid" else "unverified", issue[1].message)
        record = self._records.get(provider, {})
        stamp = record.get("checked_at", 0)
        if (
            record.get("fingerprint") == token
            and isinstance(stamp, (int, float))
            and 0 <= self.clock() - stamp < VERIFICATION_TTL
            and isinstance(record.get("models"), list)
        ):
            return "verified", "Key verified · credits and individual model access not checked"
        return "unverified", "Key saved · test to verify"

    def models(self, provider: str) -> list[str]:
        if self.status(provider)[0] != "verified":
            return []
        models = clean_models(self._records[provider]["models"])
        if provider in SUGGESTIONS:
            return [model for model in SUGGESTIONS[provider] if model in models]
        return models

    def options(self, provider: str) -> list[tuple[str, str]]:
        """Same labelled Fast/Smart and account models in every picker."""
        models = self.models(provider)
        if not models:
            return []
        settings = self.raw()
        options: list[tuple[str, str]] = []
        if provider in SUGGESTIONS:
            for tier, model in zip(("Fast", "Smart"), SUGGESTIONS[provider], strict=True):
                if model in models:
                    current = [
                        name
                        for name, field in zip(
                            ("Fast", "Smart"), MODEL_FIELDS[provider], strict=True
                        )
                        if getattr(settings, field) == model
                    ]
                    label = f"{model} · Recommended for {tier}"
                    if current:
                        label += f" (current {' / '.join(current)})"
                    options.append((label, f"{provider}:{model}"))
            return options
        for tier, field in zip(("Fast", "Smart"), MODEL_FIELDS[provider], strict=True):
            model = getattr(settings, field)
            if model in models:
                options.append((f"{tier} · {model}", f"{provider}:{model}"))
        used = {route for _, route in options}
        options += [(m, f"{provider}:{m}") for m in models if f"{provider}:{m}" not in used]
        return options

    def record(self, provider: str, token: str, result: CheckResult) -> None:
        if provider not in PROVIDERS or not token or token != fingerprint(self.key(provider)):
            return  # testing an unsaved draft must never unlock the active provider
        self._records.pop(provider, None)
        if result.ok:
            self._issues.pop(provider, None)
            self._records[provider] = {
                "fingerprint": token,
                "checked_at": self.clock(),
                "models": clean_models(result.models),
            }
        else:
            self._issues[provider] = (token, result)
        self._save()
        self.changed.emit()

    def invalidate(self, provider: str) -> None:
        self._records.pop(provider, None)
        self._issues.pop(provider, None)
        self._save()
        self.changed.emit()

    def _save(self) -> None:
        if self.path is None or not self.can_write():
            return
        tmp: str | None = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(self._records, file)
            os.replace(tmp, self.path)
            self.persistence_error = ""
        except OSError:
            self.persistence_error = "Verification works for this run; could not save its cache."
        finally:
            if tmp and Path(tmp).exists():
                try:
                    Path(tmp).unlink(missing_ok=True)
                except OSError:
                    pass  # a cache cleanup error must not crash the key-management UI
