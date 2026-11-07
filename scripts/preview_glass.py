"""Render representative screens of the light glass theme with fictional data.

No audio, network, keyring or user data is touched: DATA_DIR, QSettings and the key store are
temporary or in-memory, and provider checks are fake.

Usage (from the repository root, with src on PYTHONPATH):
    python scripts/preview_glass.py --platform offscreen --out tmp/glass-preview/offscreen
    python scripts/preview_glass.py --platform windows   --out tmp/glass-preview/native
    python scripts/preview_glass.py ... --policy reduced-transparency|reduced-motion|high-contrast

``--platform windows`` uses the real Windows Qt backend (native fonts, DPI and window sizing);
screenshots are rendered with QWidget.grab(), never by capturing the desktop.
"""

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--platform", choices=("offscreen", "windows"), default="offscreen")
parser.add_argument("--out", default="tmp/glass-preview")
parser.add_argument(
    "--policy",
    choices=("default", "reduced-transparency", "reduced-motion", "high-contrast"),
    default="default",
)
parser.add_argument("--theme", choices=("light", "dark"), default="light")
parser.add_argument(
    "--hold", type=float, default=0.0, help="keep the synthetic dashboard open this many seconds"
)
args = parser.parse_args()

if args.platform == "offscreen":
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
else:
    os.environ.pop("QT_QPA_PLATFORM", None)
_data = tempfile.TemporaryDirectory(prefix="glass-preview-")
os.environ["DATA_DIR"] = _data.name
os.environ["SKIP_ONBOARDING"] = "true"
for name in list(os.environ):
    if name.endswith("_API_KEY") or name.startswith(("OPENAI_", "ANTHROPIC_", "GEMINI_")):
        os.environ.pop(name)

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from interview_assistant.agents.jev import JevConfig  # noqa: E402
from interview_assistant.agents.types import Category, Decision  # noqa: E402
from interview_assistant.app.interview_session import InterviewSessionManager  # noqa: E402
from interview_assistant.config.keys import MemoryKeyStore  # noqa: E402
from interview_assistant.config.provider_check import CheckResult  # noqa: E402
from interview_assistant.config.relay import MemoryRelayTokenStore  # noqa: E402
from interview_assistant.config.settings import Settings  # noqa: E402
from interview_assistant.config.user_settings import UserSettingsStore  # noqa: E402
from interview_assistant.history.store import HistoryStore  # noqa: E402
from interview_assistant.ui import motion, tokens  # noqa: E402
from interview_assistant.ui.account_screen import AccountScreen  # noqa: E402
from interview_assistant.ui.bg import run_inline  # noqa: E402
from interview_assistant.ui.onboarding import OnboardingWizard  # noqa: E402
from interview_assistant.ui.overlay import OverlayWindow  # noqa: E402
from interview_assistant.ui.provider_catalog import ProviderCatalog, fingerprint  # noqa: E402
from interview_assistant.ui.provider_picker import ModelSelector  # noqa: E402
from interview_assistant.ui.settings_dialog import SettingsDialog  # noqa: E402
from interview_assistant.ui.start_screen import StartScreen  # noqa: E402
from interview_assistant.ui.theme import apply_theme  # noqa: E402

ANSWER = (
    "**Start with the requirements.** Limit each API key to 100 requests per minute, allow "
    "short bursts, and return HTTP 429 when the quota is exhausted.\n\n"
    "1. Use a **token bucket** per API key and refill it at a fixed rate.\n"
    "2. Keep counters in Redis so every API node sees the same budget.\n\n"
    "```python\n"
    "def allow(bucket, now):\n"
    "    bucket.tokens = min(bucket.cap, bucket.tokens + (now - bucket.t) * bucket.rate)\n"
    "    bucket.t = now\n"
    "    if bucket.tokens >= 1:\n"
    "        bucket.tokens -= 1\n"
    "        return True\n"
    "    return False\n"
    "```\n"
)


def settle(app: QApplication, seconds: float = 0.15) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()


def main() -> int:
    policy = {
        "default": {},
        "reduced-transparency": {"reduce_transparency": True},
        "reduced-motion": {"reduce_motion": True},
        "high-contrast": {"high_contrast": True},
    }[args.policy]
    if policy:
        motion.set_override(motion.MotionPolicy(**policy))
    app = QApplication([])
    tokens.use(args.theme)
    apply_theme(app)
    for font in ("segoeui.ttf", "segoeuib.ttf", "seguisym.ttf", "consola.ttf"):
        path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / font
        if path.exists():
            QFontDatabase.addApplicationFont(str(path))
    out = Path(args.out) / args.policy
    out.mkdir(parents=True, exist_ok=True)
    temp = Path(_data.name)
    ini = QSettings(str(temp / "ui.ini"), QSettings.Format.IniFormat)
    shots: list[Path] = []

    def snap(widget, name: str) -> None:
        settle(app)
        path = out / f"{name}.png"
        widget.grab().save(str(path))
        shots.append(path)

    now = [1_790_000_000.0]
    store = HistoryStore(":memory:", clock=lambda: now[0])
    for company, role, ended in (
        ("Northwind", "Senior Software Engineer", True),
        ("Contoso", "Data Engineer", True),
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
    keys = MemoryKeyStore({"openai": "sk-fictional-preview"})
    raw = lambda: Settings(_env_file=None)  # noqa: E731
    catalog = ProviderCatalog(raw, keys, temp / "models.json")
    catalog.record(
        "openai",
        fingerprint(catalog.key("openai")),
        CheckResult(True, "Verified", ["gpt-6.1-terra", "gpt-6.1-sol"]),
    )
    settings = SettingsDialog(
        UserSettingsStore(temp / "settings.json"),
        keys,
        raw,
        lambda provider, key: CheckResult(True, "Verified", ["gpt-6.1-terra"]),
        run_inline,
        MemoryRelayTokenStore(),
        catalog=catalog,
    )
    screen.add_workspace_page("settings", "Settings", settings)
    screen.show()
    screen.resize(960, 680)
    snap(screen, "01-dashboard")
    screen.create_btn.click()
    screen.create_page.company.setText("Northwind")
    screen.create_page.role_title.setText("Senior Software Engineer")
    snap(screen, "02-create-session")
    screen.open_workspace_page("settings")
    snap(screen, "03-settings")
    screen._show_list()
    screen.sessions.selectRow(0)

    # Animation check: the active workspace animates, and hiding it stops the timer.
    screen.activateWindow()
    settle(app, 0.4)
    running_active = screen.backdrop.running
    screen.hide()
    running_hidden = screen.backdrop.running
    screen.show()
    screen.hide()

    login = AccountScreen()
    login.show()
    snap(login, "04-login")
    login.set_stage("signup")
    snap(login, "05-signup")
    login.close()

    wizard = OnboardingWizard(
        MemoryKeyStore(), raw, lambda p, k: CheckResult(True, "ok"), run_inline, lambda m: None
    )
    wizard.show()
    snap(wizard, "06-onboarding-welcome")
    wizard.welcome.agree.setChecked(True)
    wizard.next()
    snap(wizard, "07-onboarding-providers")
    wizard.close()

    selector = ModelSelector([])
    selector.bind(catalog)
    selector.show()
    selector.showPopup()
    if selector.picker is not None:
        snap(selector.picker, "08-model-picker-popup")
        selector.picker.close()
    selector.close()

    overlay = OverlayWindow(settings=ini)
    overlay.show()
    snap(overlay, "09-overlay-idle")
    overlay.on_question(1, "How would you design a rate limiter for a public API?", "typed")
    overlay.on_decision(1, Decision(Category.SYSTEM_DESIGN, "smart", 1.0, "rules", "system_design"))
    overlay.on_delta(1, ANSWER)
    overlay.on_finished(1, "openai:gpt-6.1-sol", 0.8, 2.4)
    snap(overlay, "10-overlay-answer")
    overlay.toggle_bubble()
    snap(overlay, "11-overlay-bubble")
    overlay.close()

    print(f"policy={args.policy} platform={args.platform}")
    print(f"dashboard animating while active: {running_active}; after hide: {running_hidden}")
    print("overlay: static rim, no animation timer")
    for path in shots:
        print(path)
    if args.hold:
        screen.show()
        screen.activateWindow()
        settle(app, args.hold)
        screen.close()
    app.processEvents()
    return 0


if __name__ == "__main__":
    sys.exit(main())
