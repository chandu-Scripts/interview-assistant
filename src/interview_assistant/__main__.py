import logging
import sys

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from interview_assistant import __version__
from interview_assistant.app.composition import build_app
from interview_assistant.app.hotkeys import GlobalHotkeys
from interview_assistant.app.restart import relaunch, restart_requested
from interview_assistant.app.single_instance import SingleInstance
from interview_assistant.cli import handle as handle_cli
from interview_assistant.config.keys import KeyringStore
from interview_assistant.config.loader import load_settings
from interview_assistant.config.settings import Settings
from interview_assistant.config.user_settings import UserSettingsStore
from interview_assistant.cost.factory import data_dir
from interview_assistant.logging_setup import install_crash_handlers, setup_logging
from interview_assistant.ui.onboarding import OnboardingWizard, needs_onboarding

log = logging.getLogger(__name__)


def _consent_prompt(text: str) -> bool:
    box = QMessageBox.question(
        None,
        "Before you record",
        text + "\n\nDo you understand and want to continue?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    return box == QMessageBox.StandardButton.Yes


def _confirm_restart(parent: QWidget) -> bool:
    ask = QMessageBox.question(
        parent,
        "Restart to apply",
        "Your changes are saved. Restart Interview Assistant now to apply them?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    return ask == QMessageBox.StandardButton.Yes


def _first_run_setup(qsettings: QSettings, keys: KeyringStore) -> None:
    """Show the setup wizard once; keys are saved by the wizard, the rest is written here."""
    wizard = OnboardingWizard(keys)
    if wizard.exec():
        UserSettingsStore().update(wizard.collect())
        wizard.finish(qsettings)


def main() -> int:
    cli_code = handle_cli(sys.argv)
    if cli_code is not None:
        return cli_code
    boot = Settings()
    log_path = setup_logging(boot.log_level, data_dir(boot))
    app = QApplication(sys.argv)

    def tell_user(summary: str) -> None:
        # Once per distinct error; the details (with secrets masked) are in the log file.
        nl = chr(10)
        QMessageBox.warning(
            None,
            "Interview Assistant",
            f"Something unexpected went wrong:{nl}{nl}{summary[:300]}{nl}{nl}"
            f"The app will keep running. Details: {log_path or 'see console'}",
        )

    install_crash_handlers(tell_user)
    log.info("Starting Interview Assistant %s (frozen=%s)", __version__, hasattr(sys, "frozen"))
    instance = SingleInstance(data_dir(boot))
    if not instance.acquire():
        instance.notify_existing()  # bring the running copy to the front, then leave
        log.info("Another instance is already running; exiting")
        return 0

    keys = KeyringStore()
    app_settings = QSettings("InterviewAssistant", "App")
    if not boot.skip_onboarding and needs_onboarding(app_settings):
        _first_run_setup(app_settings, keys)
    settings = load_settings(keys)  # env / .env > settings.json, keys from the credential store

    parts = build_app(app, settings, keys, app_settings, _consent_prompt, _confirm_restart)
    instance.on_raise = parts.on_second_launch
    tray_ok = parts.tray.show()

    hotkeys = GlobalHotkeys()
    app.installNativeEventFilter(hotkeys)
    if settings.hotkey_presentation:
        hotkeys.register(settings.hotkey_presentation, parts.presentation.toggle)
    if settings.hotkey_overlay:
        hotkeys.register(settings.hotkey_overlay, parts.toggle_overlay)
    if settings.hotkey_bubble:
        hotkeys.register(settings.hotkey_bubble, parts.overlay.toggle_bubble)
    if settings.hotkey_answer:
        hotkeys.register(settings.hotkey_answer, parts.controller.trigger_answer)
    if settings.hotkey_screenshot:
        hotkeys.register(settings.hotkey_screenshot, parts.overlay.take_screenshot)
    if settings.hotkey_chat:
        hotkeys.register(settings.hotkey_chat, parts.overlay.toggle_chat)

    parts.window.show()
    parts.overlay.show()
    parts.controller.notify_cost_changed()
    if parts.history.recovered_from is not None:
        nl = chr(10)
        QMessageBox.information(
            parts.window,
            "History was repaired",
            "Your saved history file was damaged, so it was set aside and a fresh one was "
            f"started.{nl}{nl}The old file is kept at:{nl}{parts.history.recovered_from}",
        )
    log.info(
        "Application started (stt=%s, budget=%s, tray=%s, incognito=%s)",
        parts.stt_name,
        parts.tracker.budget,
        tray_ok,
        parts.privacy.incognito,
    )
    code = app.exec()
    hotkeys.unregister_all()
    parts.tray.hide()
    parts.history.close()
    instance.release()  # before relaunching, or the new copy would think one is still running
    if restart_requested():
        relaunch()  # only now: the hotkeys and database are released
    return code


if __name__ == "__main__":
    sys.exit(main())
