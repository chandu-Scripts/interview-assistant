"""Remove everything the app stored on this PC (used by the uninstaller and 'delete all data')."""

import logging
import shutil
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QSettings

from interview_assistant.config.keys import KEY_PROVIDERS, KeyStore
from interview_assistant.config.user_settings import default_data_dir

log = logging.getLogger(__name__)


@dataclass
class PurgeReport:
    data_dir_removed: bool = False
    keys_removed: list[str] = field(default_factory=list)
    settings_cleared: int = 0
    errors: list[str] = field(default_factory=list)


def purge(
    data_dir: Path | None = None,
    keys: KeyStore | None = None,
    qsettings: Iterable[QSettings] | None = None,
) -> PurgeReport:
    """Delete the data folder (settings, history, usage log), stored API keys and saved UI state.

    Does not touch environment variables or a project .env file."""
    report = PurgeReport()
    target = data_dir or default_data_dir()
    if target.exists():
        try:
            shutil.rmtree(target)
            report.data_dir_removed = True
        except OSError as e:
            report.errors.append(f"{target}: {e}")
    if keys is None:
        from interview_assistant.config.keys import KeyringStore

        keys = KeyringStore()
    for provider in KEY_PROVIDERS:
        if keys.get(provider):
            try:
                keys.delete(provider)
                report.keys_removed.append(provider)
            except Exception as e:
                report.errors.append(f"{provider} key: {type(e).__name__}")
    stores = (
        list(qsettings)
        if qsettings is not None
        else [QSettings("InterviewAssistant", "App"), QSettings("InterviewAssistant", "Overlay")]
    )
    for qs in stores:
        qs.clear()
        qs.sync()
        report.settings_cleared += 1
    return report
