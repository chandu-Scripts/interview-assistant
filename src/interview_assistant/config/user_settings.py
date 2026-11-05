import json
import logging
import os
import tempfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from interview_assistant.account.context import current_context

log = logging.getLogger(__name__)

# Settings the user may change in the app. API keys are deliberately NOT here: they live in the
# Windows Credential Manager, never in a file.
EDITABLE = frozenset(
    {
        "log_level",
        "prefer_saved_keys",
        "stt_provider",
        "whisper_model",
        "stt_language",
        "stt_vocabulary",
        "default_audio_kind",
        "openai_model",
        "openai_smart_model",
        "anthropic_model",
        "anthropic_fast_model",
        "gemini_model",
        "gemini_fast_model",
        "router_fast",
        "router_smart",
        "router_first_token_timeout_s",
        "candidate_profile",
        "budget_usd",
        "budget_scope",
        "budget_hard_stop",
        "history_enabled",
        "history_retention_days",
        "incognito",
        "redact_pii",
        "require_consent",
        "hotkey_presentation",
        "hotkey_overlay",
        "hotkey_bubble",
        "hotkey_answer",
        "hotkey_screenshot",
        "hotkey_chat",
        "relay_url",
        "relay_pending_id",
        "answer_mode",
        "answer_font_family",
        "answer_font_weight",
        "answer_font_size",
        "dual_audio_capture",
    }
)


def default_data_dir() -> Path:
    context = current_context()
    if context is not None:
        context.require_active()
        return context.account_dir
    env = os.environ.get("DATA_DIR")
    if env:
        return Path(env)
    return Path(os.environ.get("APPDATA") or Path.home()) / "InterviewAssistant"


def settings_path() -> Path:
    return default_data_dir() / "settings.json"


class UserSettingsStore:
    """The user's saved settings (JSON). Tolerant of a missing or corrupt file."""

    def __init__(self, path: Path | None = None) -> None:
        self._context = current_context()
        self.path = path or settings_path()
        if self._context is not None:
            self._context.require_active()
            self._context.validate_path(self.path)

    def load(self) -> dict[str, Any]:
        with self._context.mutation() if self._context else nullcontext():
            return self._load()

    def _load(self) -> dict[str, Any]:
        if self._context is not None:
            self._context.require_active()
            self._context.validate_path(self.path)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as e:
            log.warning("Ignoring unreadable settings file %s: %s", self.path, e)
            self._quarantine()
            return {}
        if not isinstance(raw, dict):
            self._quarantine()
            return {}
        return {k: v for k, v in raw.items() if k in EDITABLE}

    def _quarantine(self) -> None:
        with self._context.mutation() if self._context else nullcontext():
            try:  # keep the bad file for inspection instead of silently overwriting it
                self.path.replace(self.path.with_suffix(".json.bad"))
            except OSError:
                pass

    def save(self, values: dict[str, Any]) -> None:
        """Replace the saved settings. None removes a key (back to the default).

        Raises ValueError if the values do not validate as app settings."""
        with self._context.mutation() if self._context else nullcontext():
            self._save(values)

    def _save(self, values: dict[str, Any]) -> None:
        if self._context is not None:
            self._context.validate_path(self.path)
        clean = {k: v for k, v in values.items() if k in EDITABLE and v is not None}
        from interview_assistant.config.settings import Settings

        try:
            Settings.validate_saved_values(clean)
        except Exception as e:
            raise ValueError(f"invalid settings: {e}") from e
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(clean, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)  # atomic: a crash never leaves half a file
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def update(self, changes: dict[str, Any]) -> None:
        merged = self.load()
        for k, v in changes.items():
            if v is None:
                merged.pop(k, None)
            else:
                merged[k] = v
        self.save(merged)


class JsonUserSource(PydanticBaseSettingsSource):
    """Lowest-priority source: env vars and .env always win over saved settings."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self._data = UserSettingsStore().load()

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(self._data)
