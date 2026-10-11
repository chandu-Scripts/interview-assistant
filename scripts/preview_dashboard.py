"""Render the dashboard with fictional data, without audio, API keys or the user's database."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from interview_assistant.agents.jev import JevConfig  # noqa: E402
from interview_assistant.app.interview_session import InterviewSessionManager  # noqa: E402
from interview_assistant.history.store import HistoryStore  # noqa: E402
from interview_assistant.ui.start_screen import StartScreen  # noqa: E402


def main() -> None:
    app = QApplication([])
    # The offscreen Qt backend has no system font database on some Windows installations.
    for font in ("segoeui.ttf", "segoeuib.ttf"):
        path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    now = [1_790_000_000.0]
    store = HistoryStore(":memory:", clock=lambda: now[0])
    for company, role, ended in (
        ("Northwind", "Senior Software Engineer", True),
        ("Contoso", "Data Engineer", True),
        ("Fabrikam", "Platform Engineer", True),
        ("Adventure Works", "System Architect", False),
        ("Tailspin", "Backend Engineer", False),
    ):
        sid = store.create_interview_session(company=company, role_title=role)
        now[0] += 1823
        if ended:
            store.end_interview_session(sid)
        now[0] += 86400
    manager = InterviewSessionManager(JevConfig(), store, None)
    screen = StartScreen(
        store,
        None,
        manager.activate,
        lambda: None,
        lambda: None,
        on_settings=lambda: None,
        on_privacy=lambda: None,
    )
    screen.show()
    app.processEvents()
    out = Path("tmp/dashboard-preview")
    out.mkdir(parents=True, exist_ok=True)
    screen.grab().save(str(out / "sessions.png"))
    screen.create_btn.click()
    app.processEvents()
    screen.grab().save(str(out / "create-session.png"))
    screen.create_page.company.setText("Northwind")
    screen.create_page.role_title.setText("Senior Software Engineer")
    screen.create_page.next_btn.click()
    app.processEvents()
    screen.grab().save(str(out / "session-preferences.png"))
    screen.resize(900, 560)
    app.processEvents()
    screen.grab().save(str(out / "session-preferences-small.png"))
    screen.close()
    store.close()


if __name__ == "__main__":
    main()
