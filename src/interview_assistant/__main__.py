import logging
import sys

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from interview_assistant import __version__
from interview_assistant.account.bootstrap import Bootstrap
from interview_assistant.account.context import current_context
from interview_assistant.app.composition import build_app
from interview_assistant.app.hotkeys import GlobalHotkeys
from interview_assistant.app.restart import relaunch, request_restart, restart_requested
from interview_assistant.app.single_instance import SingleInstance
from interview_assistant.cli import handle as handle_cli
from interview_assistant.config.keys import KeyringStore
from interview_assistant.config.loader import load_settings
from interview_assistant.config.settings import Settings
from interview_assistant.config.user_settings import UserSettingsStore
from interview_assistant.cost.factory import data_dir
from interview_assistant.logging_setup import install_crash_handlers, setup_logging
from interview_assistant.ui.onboarding import OnboardingWizard, needs_onboarding
from interview_assistant.ui.theme import apply_theme

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
    context = current_context()
    if wizard.exec() and (context is None or context.active):
        UserSettingsStore().update(wizard.collect())
        wizard.finish(qsettings)


def main() -> int:
    cli_code = handle_cli(sys.argv)
    if cli_code is not None:
        return cli_code
    bootstrap = Bootstrap.load()
    # Customer bootstrap must never instantiate Settings against the unowned legacy root.
    boot = None if bootstrap.customer else Settings()
    root = bootstrap.root if boot is None else data_dir(boot)
    log_path = setup_logging(boot.log_level if boot else "INFO", root)
    app = QApplication(sys.argv)
    apply_theme(app)

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
    instance = SingleInstance(root)
    if not instance.acquire():
        instance.notify_existing()  # bring the running copy to the front, then leave
        log.info("Another instance is already running; exiting")
        return 0

    account_runtime = None
    if bootstrap.customer:
        from interview_assistant.account.startup import authenticate

        account_runtime = authenticate(bootstrap, instance)
        if account_runtime is None:
            instance.release()
            return 0
    context = current_context()
    keys = KeyringStore()
    app_settings = context.qsettings("App") if context else QSettings("InterviewAssistant", "App")
    if not (boot and boot.skip_onboarding) and needs_onboarding(app_settings):
        _first_run_setup(app_settings, keys)
    if account_runtime is not None:
        account_runtime.check_session()
        if account_runtime.lock_message or account_runtime.service.session is None:
            account_runtime.shutdown()
            instance.release()
            QMessageBox.information(
                None, "Sign in required", "Your session expired during setup. Open the app again."
            )
            return 1
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
        hotkeys.register(settings.hotkey_answer, parts.overlay.answer_now)
    if settings.hotkey_screenshot:
        hotkeys.register(settings.hotkey_screenshot, parts.overlay.take_screenshot)
    if settings.hotkey_chat:
        hotkeys.register(settings.hotkey_chat, parts.overlay.toggle_chat)

    logging_out = False
    shutting_down = False

    def cleanup() -> None:
        nonlocal shutting_down
        if shutting_down:
            return
        shutting_down = True
        if context is not None:
            context.revoke()
        if account_runtime is not None:
            account_runtime.invalidate()
        hotkeys.unregister_all()
        instance.on_raise = lambda: None
        parts.shutdown()

    def logout() -> None:
        nonlocal logging_out
        logging_out = True
        request_restart()
        cleanup()
        app.quit()

    def lock_account(message: str) -> None:
        cleanup()
        QMessageBox.information(None, "Sign in required", message)
        request_restart()
        app.quit()

    if account_runtime is not None:
        parts.start_screen.logout_button.show()
        parts.start_screen.logout_button.clicked.connect(logout)
        account_runtime.locked.connect(lock_account)
        account_runtime.notice.connect(parts.start_screen.set_account_notice)

        def apply_incognito() -> None:
            if parts.privacy.incognito:
                account_runtime.forget_remembered()

        parts.privacy.changed.connect(apply_incognito)
        apply_incognito()

    app.aboutToQuit.connect(cleanup)

    if account_runtime is not None:
        account_runtime.check_session()
        if account_runtime.lock_message or shutting_down:
            cleanup()
            account_runtime.shutdown()
            instance.release()
            return 1
    parts.start_screen.show()
    app.setQuitOnLastWindowClosed(True)
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
    cleanup()
    restart_safe = True
    if account_runtime is not None:
        auth_error = account_runtime.shutdown(logout=logging_out)
        if auth_error:
            restart_safe = account_runtime.service.logout_safe
            QMessageBox.warning(None, "Sign-out cleanup", auth_error)
    instance.release()  # before relaunching, or the new copy would think one is still running
    if restart_requested() and restart_safe:
        relaunch()  # only now: the hotkeys and database are released
    return code


if __name__ == "__main__":
    sys.exit(main())
