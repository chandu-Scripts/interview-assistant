"""Login gate, invoked before constructing any account-backed application objects."""

from hashlib import sha256

from PySide6.QtWidgets import QApplication, QDialog

from interview_assistant.account.access import SupabaseAccessProvider
from interview_assistant.account.bootstrap import Bootstrap
from interview_assistant.account.context import AccountContext, activate_context
from interview_assistant.account.provider import SupabaseAuthProvider
from interview_assistant.account.runtime import AccountRuntime
from interview_assistant.account.session import SessionService
from interview_assistant.account.vault import RefreshVault
from interview_assistant.app.single_instance import SingleInstance
from interview_assistant.ui.account_screen import AccountScreen


def authenticate(boot: Bootstrap, instance: SingleInstance) -> AccountRuntime | None:
    app = QApplication.instance()
    screen = AccountScreen()

    def raise_login() -> None:
        screen.showNormal()
        screen.raise_()
        screen.activateWindow()

    instance.on_raise = raise_login
    try:
        boot.validate()
    except ValueError:
        screen.set_busy(True)
        screen.show_message("Account service is not configured. Contact the app owner.", error=True)
        screen.exec()
        instance.on_raise = lambda: None
        screen.deleteLater()
        return None
    provider = SupabaseAuthProvider(boot.url, boot.publishable_key)
    project = sha256(boot.url.encode("utf-8")).hexdigest()[:32]
    vault = RefreshVault(project, boot.root / "auth" / project / "signed-out.json")
    runtime = AccountRuntime(
        SessionService(provider, vault),
        access=SupabaseAccessProvider(boot.url, boot.publishable_key)
        if boot.enforce_access
        else None,
    )
    try:
        if not runtime.authenticate(screen):
            runtime.shutdown()
            return None
        session = runtime.service.session
        if session is None or (boot.enforce_access and not runtime.access_lease.valid):
            runtime.shutdown()
            return None
        context = AccountContext(
            boot.root,
            boot.url,
            session.user_id,
            access_valid=(lambda: runtime.access_lease.valid) if boot.enforce_access else None,
        )
        activate_context(context)

        def startup_locked(_message: str) -> None:
            # Connected before onboarding can run a nested event loop. A later
            # workspace receiver is not enough: Qt does not replay lost signals.
            context.revoke()
            if isinstance(app, QApplication):
                modal = app.activeModalWidget()
                if isinstance(modal, QDialog):
                    modal.reject()

        runtime.locked.connect(startup_locked)
        return runtime
    except Exception:
        runtime.shutdown()
        raise
    finally:
        instance.on_raise = lambda: None
        screen.deleteLater()
