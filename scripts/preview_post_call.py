"""Preview post-call pages using fictional saved Q&A; no provider or user data access."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from interview_assistant.agents.jev import JevConfig  # noqa: E402
from interview_assistant.app.interview_session import (  # noqa: E402
    InterviewSessionManager,
    SessionDetails,
)
from interview_assistant.history.store import HistoryStore, TurnRecord  # noqa: E402
from interview_assistant.ui.start_screen import StartScreen  # noqa: E402


def main() -> None:
    app = QApplication([])
    for font in ("segoeui.ttf", "segoeuib.ttf"):
        path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    store = HistoryStore(":memory:")
    manager = InterviewSessionManager(JevConfig(), store, None)
    session = manager.activate(SessionDetails(company="Northwind", role_title="Platform Engineer"))
    store.add_turn(
        TurnRecord(
            "How would you design a rate limiter?",
            "Use a token bucket per API key. Store counters in Redis and "
            "update them atomically. Return HTTP 429 when the quota is exhausted.",
            source="voice:loopback",
            category="system_design",
            cost_usd=0.003,
        )
    )
    store.add_turn(
        TurnRecord(
            "What happens if Redis is unavailable?",
            "Choose fail-open or fail-closed based on the risk of the endpoint. "
            "A local fallback can provide an approximate limit.",
            source="voice:loopback",
            category="system_design",
            cost_usd=0.002,
        )
    )
    manager.end_active()
    store.save_interview_notes(
        session.id,
        "Practice explaining the fail-open trade-off.\n"
        "Prepare an example of monitoring a distributed service.",
    )
    window = StartScreen(store, None, manager.activate, lambda: None, lambda: None)
    window.show()
    window.show_review(session.id)
    out = Path("tmp/post-call-preview")
    out.mkdir(parents=True, exist_ok=True)
    for index, name in enumerate(("transcript", "notes", "ask-ai", "details")):
        window.review_page.tabs.setCurrentIndex(index)
        app.processEvents()
        window.grab().save(str(out / f"{name}.png"))
    window.resize(900, 560)
    app.processEvents()
    window.grab().save(str(out / "details-small.png"))
    window.close()
    store.close()


if __name__ == "__main__":
    main()
