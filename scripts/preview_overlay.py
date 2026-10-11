"""Preview the real overlay using fictional content and isolated settings; no capture or APIs."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from interview_assistant.agents.types import Category, Decision  # noqa: E402
from interview_assistant.ui.overlay import OverlayWindow  # noqa: E402


def main() -> None:
    app = QApplication([])
    for font in ("segoeui.ttf", "segoeuib.ttf", "seguisym.ttf"):
        path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    out = Path("tmp/overlay-preview")
    out.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=out) as temp:
        window = OverlayWindow(
            settings=QSettings(str(Path(temp) / "preview.ini"), QSettings.Format.IniFormat)
        )
        window.show()
        window.on_question(1, "How would you design a rate limiter for a public API?", "typed")
        window.on_decision(
            1, Decision(Category.SYSTEM_DESIGN, "smart", 1.0, "rules", "system_design")
        )
        window.on_delta(
            1,
            "**Start with the requirements.** Limit each API key to 100 requests per minute, "
            "allow short bursts, and return HTTP 429 when the quota is exhausted.\n\n"
            "1. Use a **token bucket** per API key. Refill tokens at a fixed rate.\n"
            "2. Store buckets in **Redis** and update them atomically.\n"
            "3. Include a **Retry-After** header so clients can back off.\n\n"
            "**Trade-off:** a shared store gives consistent limits, but adds a network hop. "
            "Define how the service behaves if Redis is unavailable.",
        )
        window.on_finished(1, "demo", 0.4, 1.2)
        window.on_live_text("How would you handle bursts across multiple regions?")
        app.processEvents()
        window.grab().save(str(out / "answer.png"))
        window.toggle_chat()
        app.processEvents()
        window.grab().save(str(out / "chat.png"))
        window.toggle_collapsed()
        app.processEvents()
        window.grab().save(str(out / "collapsed.png"))
        window.toggle_bubble()
        app.processEvents()
        window.grab().save(str(out / "bubble.png"))
        window.close()


if __name__ == "__main__":
    main()
