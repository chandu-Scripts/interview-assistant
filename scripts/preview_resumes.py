"""Render resume management with fictional data in an isolated temporary library."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from interview_assistant.agents.jev import JevConfig  # noqa: E402
from interview_assistant.app.interview_session import InterviewSessionManager  # noqa: E402
from interview_assistant.app.resume_library import ResumeLibrary  # noqa: E402
from interview_assistant.history.store import HistoryStore  # noqa: E402
from interview_assistant.ui.start_screen import StartScreen  # noqa: E402


def main() -> None:
    app = QApplication([])
    for font in ("segoeui.ttf", "segoeuib.ttf"):
        path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    out = Path("tmp/resume-preview")
    out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=out) as temp:
        library = ResumeLibrary(Path(temp) / "resumes.json")
        library.add(
            "Backend Engineer.txt", "Alex Morgan\nBackend Engineer\n\nPython, SQL, REST APIs"
        )
        library.add(
            "Platform Engineer.txt",
            "Alex Morgan\nPlatform Engineer\n\nSUMMARY\n"
            "Software engineer building reliable services and developer tools.\n\n"
            "EXPERIENCE\nNorthwind — Platform Engineer\n"
            "Built deployment tooling and maintained shared infrastructure.\n"
            "Worked with product teams to improve service reliability.\n\n"
            "SKILLS\nPython · PostgreSQL · Docker · Cloud infrastructure",
        )
        store = HistoryStore(":memory:")
        manager = InterviewSessionManager(JevConfig(), store, None)
        window = StartScreen(store, library, manager.activate, lambda: None, lambda: None)
        window.show()
        window.resumes_btn.click()
        app.processEvents()
        window.grab().save(str(out / "resumes.png"))
        window.resize(900, 560)
        app.processEvents()
        window.grab().save(str(out / "resumes-small.png"))
        window.close()
        store.close()


if __name__ == "__main__":
    main()
