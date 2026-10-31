"""Remove everything the app stored on this PC (used by the uninstaller and 'delete all data')."""

import json
import logging
import os
import re
import shutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

from PySide6.QtCore import QSettings

from interview_assistant.config.keys import KEY_PROVIDERS, KeyStore
from interview_assistant.config.user_settings import default_data_dir

log = logging.getLogger(__name__)

_NAMESPACE = re.compile(r"[0-9a-f]{32}")


def _checked_target(target: Path) -> Path:
    """Validate the exact requested root and all descendants before recursive deletion."""
    target = target.absolute()
    protected = {Path(target.anchor), Path.home().resolve(), Path.cwd().resolve()}
    if os.environ.get("APPDATA"):
        protected.add(Path(os.environ["APPDATA"]).resolve())
    if target in protected or target.resolve() != target:
        raise ValueError("Refusing to remove a protected or redirected data directory")
    if target.exists():
        if not target.is_dir():
            raise ValueError("Data directory is not a directory")

        def unreadable(error: OSError) -> None:
            raise error

        for parent, directories, files in os.walk(target, followlinks=False, onerror=unreadable):
            for name in (*directories, *files):
                path = Path(parent) / name
                if path.is_symlink() or path.resolve() != path:
                    raise ValueError("Data directory contains a redirected path")
    return target


def _account_credentials(target: Path) -> set[tuple[str, str]]:
    """Inventory only known app namespaces; read no resumes/history/provider key values."""
    credentials: set[tuple[str, str]] = set()
    accounts = target / "accounts"
    if accounts.is_dir():
        for namespace in accounts.iterdir():
            if not namespace.is_dir() or not _NAMESPACE.fullmatch(namespace.name):
                continue
            for account in namespace.iterdir():
                if not account.is_dir():
                    continue
                try:
                    user = str(UUID(account.name))
                except ValueError:
                    continue
                if user != account.name:
                    continue
                service = f"InterviewAssistant:{namespace.name}:{user}"
                credentials.update((service, provider) for provider in KEY_PROVIDERS)
                credentials.add((f"InterviewAssistantRelay:{namespace.name}:{user}", "token"))
                credentials.add((f"InterviewAssistantAuth:{namespace.name}", user))
    auth = target / "auth"
    if auth.is_dir():
        for project in auth.iterdir():
            if not project.is_dir() or not _NAMESPACE.fullmatch(project.name):
                continue
            marker = project / "signed-out.json"
            if not marker.is_file():
                continue
            if marker.stat().st_size > 4096:
                raise ValueError("Remembered-login marker is invalid")
            try:
                raw = json.loads(marker.read_text(encoding="utf-8"))
                user = str(UUID(raw["user_id"]))
                if not isinstance(raw.get("active"), bool):
                    raise ValueError
            except (ValueError, TypeError, KeyError):
                raise ValueError("Remembered-login marker is invalid") from None
            credentials.add((f"InterviewAssistantAuth:{project.name}", user))
    return credentials


def _delete_account_credential(service: str, username: str) -> None:
    import keyring
    from keyring.errors import PasswordDeleteError

    try:
        keyring.delete_password(service, username)
    except PasswordDeleteError:
        # Windows raises this for an absent credential. A vault-access failure must surface.
        if keyring.get_password(service, username) is not None:
            raise


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
    *,
    credential_cleanup: Callable[[str, str], None] | None = None,
) -> PurgeReport:
    """Delete the data folder (settings, history, usage log), stored API keys and saved UI state.

    Does not touch environment variables or a project .env file. Injected legacy
    keys disable real account-vault cleanup unless a cleanup callable is supplied."""
    report = PurgeReport()
    target = data_dir or default_data_dir()
    try:
        target = _checked_target(target)
        credentials = _account_credentials(target)
    except (OSError, ValueError) as error:
        report.errors.append(f"Data directory safety check: {type(error).__name__}")
        return report
    cleanup = credential_cleanup
    if cleanup is None and keys is None:
        cleanup = _delete_account_credential
    if cleanup is not None:
        for service, username in sorted(credentials):
            try:
                cleanup(service, username)
                report.keys_removed.append(f"{service}/{username}")
            except Exception as error:
                report.errors.append(f"Account credential cleanup: {type(error).__name__}")
    if report.errors:
        # Keep inventory for a later retry instead of orphaning remaining vault credentials.
        return report
    if target.exists():
        try:
            _checked_target(target)
            shutil.rmtree(target)
            report.data_dir_removed = True
        except (OSError, ValueError) as e:
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
