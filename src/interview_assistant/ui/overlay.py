import time
from collections.abc import Callable
from functools import partial

from PySide6.QtCore import QPoint, QRectF, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QKeySequence,
    QPainter,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSizeGrip,
    QSizePolicy,
    QSlider,
    QStyle,
    QStyleOptionButton,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.agents.specialists import ANSWERABLE_CATEGORIES, LABELS
from interview_assistant.agents.types import Category, Decision
from interview_assistant.app.controller import DETECTED_QUESTION_PREFIX, AssistantController
from interview_assistant.cost.tracker import BudgetLevel, BudgetState, Summary, format_usd
from interview_assistant.stt.base import NO_WORDS_MESSAGE
from interview_assistant.ui.answer_view import AnswerView
from interview_assistant.ui.audio_panel import AudioPanel
from interview_assistant.ui.controls import ChoiceComboBox
from interview_assistant.ui.screenshot import capture_screen_png
from interview_assistant.ui.theme import paint_glass

# Navy glass live bar: one toolbar with
# labelled shortcut buttons, a listening line under it, and the answer panel below.
BG = QColor(17, 31, 52, 240)
BORDER = QColor(164, 220, 240, 65)
STYLE = """
QWidget { font-family: 'Segoe UI'; font-size: 12px; }
QLabel { color: #e6f3ff; background: transparent; }
#title { color: #9eb9cf; font-size: 10px; }
#chip { color: #a7b9d2; font-size: 11px; }
#question { color: #f0fbff; font-size: 14px; font-weight: 600; padding: 8px 0; }
#transcript { background: rgba(255,255,255,0.08); border-radius: 8px; padding: 6px 10px; }
#history { color: #a7b9d2; font-size: 11px; }
#warn { color: #ffb86b; font-size: 11px; }
#timer { color: #e8eaf2; font-size: 12px; }
#status { color: #aab0c4; font-size: 12px; }
QTextBrowser { background: transparent; color: #e6f3ff; border: none; font-size: 14px; }
QLineEdit { background: rgba(255,255,255,0.06); color: #e8eaf2; border: 1px solid #3a3e4c;
            border-radius: 6px; padding: 4px 8px; }
QPushButton { background: rgba(255,255,255,0.08); color: #e8eaf2; border: none;
              border-radius: 8px; padding: 6px 9px; }
QPushButton:hover { background: rgba(255,255,255,0.16); }
QPushButton:disabled { color: #5d6276; background: rgba(255,255,255,0.04); }
#iconbtn { padding: 2px 8px; font-weight: bold; }
#pill { font-weight: 600; background: rgba(104,225,223,28); color: #abf6f0; }
#audio { padding: 4px 8px; }
#audio:checked { background: #d6363f; color: #ffffff; }
#end { background: #d6363f; color: #ffffff; font-weight: 600; }
#end:hover { background: #e5484f; }
QMenu { background: #162b42; color: #e6f3ff; border: 1px solid #44637e; padding: 5px; }
QMenu::item { padding: 6px 18px; }
QMenu::item:selected { background: #285c74; border-radius: 4px; }
QScrollBar:vertical { background: transparent; width: 7px; }
QScrollBar::handle:vertical { background: #46657f; border-radius: 3px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""
COST_COLOR = {
    BudgetLevel.OK: "#1f9d55",
    BudgetLevel.WARN: "#b5650a",
    BudgetLevel.EXCEEDED: "#d6363f",
}
DOT = {"idle": "#9aa0b4", "listening": "#d32f2f", "thinking": "#d98c1f", "answering": "#1f9d55"}
STATUS_TEXT = {
    "idle": "Ready",
    "listening": "Listening…",
    "thinking": "Thinking…",
    "answering": "Answering…",
}
BUBBLE_SIZE = 48
SCREENSHOT_TIP = "Capture your screen and answer it now (Ctrl+Shift+Enter)"


class TranscriptLabel(QLabel):
    """Keep the latest speech bounded without making the overlay wider as it arrives."""

    def __init__(self) -> None:
        super().__init__()
        self._transcript = ""
        self.setObjectName("transcript")
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(80)
        self.set_transcript("")

    def set_transcript(self, text: str) -> None:
        self._transcript = text[-2000:]
        self.setToolTip(self._transcript)
        self._refresh()

    def _refresh(self) -> None:
        text = self._transcript or "Your live transcript will appear here"
        self.setText(
            self.fontMetrics().elidedText(
                text, Qt.TextElideMode.ElideLeft, max(0, self.width() - 24)
            )
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh()


class ShortcutButton(QPushButton):
    """Paint a separate key badge while retaining the button's accessible text and behavior."""

    def paintEvent(self, _event) -> None:  # noqa: N802
        label, _, keys = self.text().partition("   ")
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, self)
        font = QFont(self.font())
        font.setPixelSize(10)
        font.setBold(False)
        badge_width = QFontMetrics(font).horizontalAdvance(keys) + 12
        badge = QRectF(self.width() - badge_width - 6, 5, badge_width, self.height() - 10)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 22 if self.isEnabled() else 8))
        painter.drawRoundedRect(badge, 4, 4)
        painter.setPen(QColor("#e6f3ff" if self.isEnabled() else "#687e99"))
        painter.drawText(
            QRectF(9, 0, badge.left() - 12, self.height()), Qt.AlignmentFlag.AlignVCenter, label
        )
        painter.setFont(font)
        painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, keys)


def split_detected_question(text: str) -> tuple[str | None, str]:
    """A screenshot answer starts with a 'Question:' line naming what was detected on screen.
    Returns (detected question or None, the answer that follows). Works mid-stream too."""
    if not text.startswith(DETECTED_QUESTION_PREFIX):
        return None, text
    first, _, rest = text.partition("\n")
    question = first[len(DETECTED_QUESTION_PREFIX) :].strip()
    return question or None, rest.lstrip()


class OverlayWindow(QWidget):
    """Compact always-on-top answer window.

    A normal, fully visible window: frameless, draggable, resizable, adjustable opacity. It
    makes no attempt to hide itself from screen capture; that is Step 10's explicit mode.
    """

    end_requested = Signal()  # the End button: the app ends the session and closes

    def __init__(
        self, controller: AssistantController | None = None, settings: QSettings | None = None
    ) -> None:
        super().__init__()
        self._settings = settings or QSettings("InterviewAssistant", "Overlay")
        self._gen = 0
        self._text = ""
        self._current_question = ""
        self._current_source = "typed"
        self._qa_history: list[tuple[str, str, str]] = []  # (question, answer, source)
        self._answer_origins: list[str] = []
        self._live_origin = ""
        self._locally_formatted = False
        self._history_index = 0  # == len(self._qa_history) means "viewing the live answer"
        self._drag: QPoint | None = None
        self._press_global: QPoint | None = None
        self._collapsed = False
        self._bubble = False
        self._expanded_height = 420
        self._restore_width = 800
        self._listening = False
        self._mic_bound = False
        self._busy = False
        self._answer_cost: float | None = None
        self._last_decision: Decision | None = None
        self._fix_row_open = False
        self._chat_row_open = False
        # Set by MainWindow.attach_privacy(): asks for screenshot consent before the first capture.
        self.before_screenshot: Callable[[], bool] | None = None
        self.setObjectName("overlay")
        self.setWindowTitle("Interview Assistant Overlay")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(640, 90)
        self.setStyleSheet(STYLE)

        self.dot = QLabel("●")
        self.title = QLabel("Interview Assistant")
        self.title.setObjectName("title")
        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("status")
        self.chip = QLabel("")
        self.chip.setObjectName("chip")
        self.cost_label = QLabel("")
        self.cost_label.setToolTip("Session cost")
        self.timer_label = QLabel("00:00")
        self.timer_label.setObjectName("timer")
        self.timer_label.setToolTip("Time since the overlay started")
        self.system_toggle = self._audio_toggle("🔊", "System audio (what the interviewer says)")
        self.mic_toggle = self._audio_toggle("🎤", "Microphone (what you say)")
        self.mic_toggle.setVisible(False)  # shown once bind_audio() gets a microphone panel
        self.answer_btn = self._shortcut_button("Answer", "Ctrl ↵", "Answer the question above")
        self.answer_btn.setEnabled(False)
        self.screenshot_btn = self._shortcut_button("Screenshot", "Ctrl ⇧ ↵", SCREENSHOT_TIP)
        self.chat_btn = self._shortcut_button("Chat", "Ctrl ⇧ Space", "Type a question (toggle)")
        self.fix_btn = self._icon_button("✎", "Wrong specialist? Pick the right one and redo")
        self.collapse_btn = self._icon_button("–", "Collapse / expand")
        self.more_btn = self._icon_button("⋮", "More: devices, history, settings, privacy")
        self.close_btn = QPushButton("End")
        self.close_btn.setObjectName("end")
        self.close_btn.setToolTip("End the session and close the app")
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setToolTip("Clear the answers on screen (history is kept)")
        self.transcript = TranscriptLabel()
        self.transcript_clear_btn = QPushButton("Clear")
        self.transcript_clear_btn.setToolTip(
            "Clear the visible transcript; the waiting question is kept"
        )
        # Widgets hidden in bubble mode (everything except the status dot, which IS the bubble).
        self._header_extras = (
            self.system_toggle,
            self.mic_toggle,
            self.title,
            self.status_label,
            self.chip,
            self.cost_label,
            self.timer_label,
            self.answer_btn,
            self.screenshot_btn,
            self.chat_btn,
            self.fix_btn,
            self.collapse_btn,
            self.more_btn,
            self.close_btn,
            self.clear_btn,
        )
        top_row = QHBoxLayout()
        self._toolbar_layout = top_row
        top_row.setContentsMargins(10, 8, 10, 8)
        top_row.setSpacing(5)
        w: QWidget
        for w in (
            self.system_toggle,
            self.mic_toggle,
            self.answer_btn,
            self.screenshot_btn,
            self.chat_btn,
        ):
            top_row.addWidget(w)
        top_row.addStretch(1)
        for w in (self.fix_btn, self.collapse_btn, self.timer_label, self.more_btn, self.close_btn):
            top_row.addWidget(w)
        self.header = QWidget()
        self.header.setLayout(top_row)
        self.transcript_panel = QWidget()
        transcript_row = QHBoxLayout(self.transcript_panel)
        self._transcript_layout = transcript_row
        transcript_row.setContentsMargins(10, 6, 10, 6)
        transcript_row.setSpacing(8)
        for w in (self.dot, self.status_label):
            transcript_row.addWidget(w)
        transcript_row.addWidget(self.transcript, 1)
        transcript_row.addWidget(self.transcript_clear_btn)

        self.question = QLabel("Waiting for a question…")
        self.question.setObjectName("question")
        self.question.setWordWrap(True)
        self.prev_btn = self._icon_button("◀", "Previous answer (Ctrl+Left)")
        self.next_btn = self._icon_button("▶", "Next answer (Ctrl+Right)")
        self.history_label = QLabel("")
        self.history_label.setObjectName("history")
        nav_row = QHBoxLayout()
        nav_row.setContentsMargins(0, 0, 0, 0)
        nav_row.addWidget(self.prev_btn)
        nav_row.addWidget(self.history_label)
        nav_row.addWidget(self.next_btn)
        self.nav_row = QWidget()
        self.nav_row.setLayout(nav_row)
        self.nav_row.setVisible(False)  # appears once there's at least one completed answer
        self.warn_label = QLabel("")
        self.warn_label.setObjectName("warn")
        self.warn_label.setWordWrap(True)
        self.warn_label.setVisible(False)
        self.category_combo = ChoiceComboBox()
        for cat in ANSWERABLE_CATEGORIES:
            self.category_combo.addItem(LABELS[cat], cat.value)
        self.fix_apply_btn = QPushButton("Redo")
        fix_row = QHBoxLayout()
        fix_row.setContentsMargins(0, 0, 0, 0)
        fix_row.addWidget(QLabel("Answer as:"))
        fix_row.addWidget(self.category_combo, 1)
        fix_row.addWidget(self.fix_apply_btn)
        self.fix_row = QWidget()
        self.fix_row.setLayout(fix_row)
        self.fix_row.setVisible(False)
        self.answer = AnswerView()
        self.answer.setPlaceholderText("Answers stream in here.")
        self.origin_label = QLabel()
        self.origin_label.setWordWrap(True)

        self.input = QLineEdit()
        self.input.setPlaceholderText("Type a question and press Enter…")
        self.ask_btn = QPushButton("Ask")
        chat_row = QHBoxLayout()
        chat_row.setContentsMargins(0, 0, 0, 0)
        chat_row.addWidget(self.input, 1)
        chat_row.addWidget(self.ask_btn)
        self.chat_row = QWidget()
        self.chat_row.setLayout(chat_row)
        self.chat_row.setVisible(False)

        self.stop_btn = QPushButton("Stop")
        self.continue_btn = QPushButton("Continue answer")
        self.continue_btn.setToolTip("Request the missing text. Uses additional AI tokens.")
        self.continue_btn.setVisible(False)
        self._incomplete = False
        self.copy_btn = QPushButton("Copy")
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(30, 100)
        self.opacity.setFixedWidth(70)
        self.opacity.setToolTip("Opacity")
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addWidget(self.title)
        footer.addWidget(self.chip, 1)
        for extra in (self.cost_label, self.stop_btn, self.opacity):
            footer.addWidget(extra)
        self.footer = QWidget()
        self.footer.setLayout(footer)

        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(12, 8, 12, 8)
        answer_toolbar = QHBoxLayout()
        answer_toolbar.addWidget(self.nav_row)
        answer_toolbar.addStretch(1)
        answer_toolbar.addWidget(self.continue_btn)
        answer_toolbar.addWidget(self.clear_btn)
        answer_toolbar.addWidget(self.copy_btn)
        body.addLayout(answer_toolbar)
        body.addWidget(self.question)
        body.addWidget(self.warn_label)
        body.addWidget(self.fix_row)
        body.addWidget(self.chat_row)
        body.addWidget(self.origin_label)
        body.addWidget(self.answer, 1)
        body.addWidget(self.footer)
        self.grip = QSizeGrip(self)
        footer.addWidget(self.grip)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(6)
        layout.addWidget(self.header)
        layout.addWidget(self.transcript_panel)
        layout.addWidget(self.body, 1)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(25)
        self._render_timer.timeout.connect(self._render)

        self.fix_btn.clicked.connect(self._toggle_fix_row)
        self.fix_apply_btn.clicked.connect(self._apply_fix)
        self.fix_btn.setEnabled(False)
        self.chat_btn.clicked.connect(self.toggle_chat)
        self.collapse_btn.clicked.connect(self.toggle_collapsed)
        self.close_btn.clicked.connect(self.end_requested)
        self.copy_btn.clicked.connect(self.copy_answer)
        self.opacity.valueChanged.connect(self._set_opacity)
        self.input.returnPressed.connect(self._submit_typed)
        self.ask_btn.clicked.connect(self._submit_typed)
        self.answer_btn.clicked.connect(self.answer_now)
        self.screenshot_btn.clicked.connect(self.take_screenshot)
        QShortcut(QKeySequence("Ctrl+Shift+Return"), self, self.take_screenshot)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self, self.take_screenshot)
        self.clear_btn.clicked.connect(self._clear_view)
        self.transcript_clear_btn.clicked.connect(lambda: self.transcript.set_transcript(""))
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._started = time.monotonic()
        self._clock.timeout.connect(self._tick)
        self._clock.start()
        QShortcut(QKeySequence("Ctrl+Return"), self, self.answer_now)
        QShortcut(QKeySequence("Ctrl+Enter"), self, self.answer_now)  # numpad Enter
        self.prev_btn.clicked.connect(self.prev_qa)
        self.next_btn.clicked.connect(self.next_qa)
        QShortcut(QKeySequence("Ctrl+Left"), self, self.prev_qa)
        QShortcut(QKeySequence("Ctrl+Right"), self, self.next_qa)
        QShortcut(QKeySequence("Escape"), self, self._stop)
        self.stop_btn.clicked.connect(self._stop)
        self.continue_btn.clicked.connect(self._continue_answer)

        self._menu_actions: list[tuple[str, Callable[[], None]]] = []
        self._menu_devices: list[tuple[str, AudioPanel]] = []
        self._controller: AssistantController | None = None
        self._restore()
        self._set_busy(False)
        self._update_dot()
        if controller is not None:
            self.bind(controller)

    # ---- construction helpers ----
    def _icon_button(self, text: str, tip: str) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("iconbtn")
        b.setToolTip(tip)
        return b

    def _audio_toggle(self, icon: str, tip: str) -> QPushButton:
        b = QPushButton(icon)
        b.setObjectName("audio")
        b.setCheckable(True)
        b.setToolTip(tip + " - click to turn on or off")
        return b

    def bind_audio(self, system: AudioPanel, mic: AudioPanel | None = None) -> None:
        """The toggles start and stop the real capture panels, so their state always matches."""
        system.recording_changed.connect(self.system_toggle.setChecked)
        self.system_toggle.setChecked(system.recording)
        self.system_toggle.clicked.connect(system.toggle)
        if mic is not None:
            self._mic_bound = True
            self.mic_toggle.setVisible(True)
            mic.recording_changed.connect(self.mic_toggle.setChecked)
            self.mic_toggle.setChecked(mic.recording)
            self.mic_toggle.clicked.connect(mic.toggle)

    def _shortcut_button(self, label: str, keys: str, tip: str) -> QPushButton:
        b = ShortcutButton(f"{label}   {keys}")
        b.setObjectName("pill")
        b.setToolTip(tip)
        return b

    def bind(self, c: AssistantController) -> None:
        self._controller = c
        c.question_started.connect(self.on_question)
        c.question_pending.connect(self.on_question_pending)
        c.decision_ready.connect(self.on_decision)
        c.answer_delta.connect(self.on_delta)
        c.answer_reset.connect(self.on_reset)
        c.answer_finished.connect(self.on_finished)
        c.answer_incomplete.connect(self.on_incomplete)
        c.continuation_failed.connect(self.on_continuation_failed)
        c.answer_failed.connect(self.on_failed)
        c.answer_stopped.connect(self.on_stopped)
        c.live_text.connect(self.on_live_text)
        c.transcription_status.connect(self.on_transcription_status)
        c.busy_changed.connect(self._set_busy)
        c.answer_cost.connect(self.on_answer_cost)
        c.latency_ready.connect(self.on_latency)
        c.cost_updated.connect(self.on_cost_updated)
        c.answer_flags.connect(self.on_flags)
        c.category_corrected.connect(self.on_category_corrected)
        c.screenshot_pending.connect(self.on_screenshot_pending)

    # ---- controller events (all run on the UI thread) ----
    def on_screenshot_pending(self, pending: bool) -> None:
        self.screenshot_btn.setText(
            "Screenshot ✓   Ctrl ⇧ ↵" if pending else "Screenshot   Ctrl ⇧ ↵"
        )
        self.screenshot_btn.setToolTip(
            "Screenshot attached - it goes with your next question" if pending else SCREENSHOT_TIP
        )

    def on_question(self, gen: int, text: str, source: str) -> None:
        self._incomplete = False
        self.continue_btn.hide()
        self._locally_formatted = False
        self._live_origin = ""
        self.origin_label.clear()
        self._gen = gen
        self._text = ""
        self._answer_cost = None
        self._last_decision = None
        self._current_question, self._current_source = text, source
        self._history_index = len(self._qa_history)  # a new question always means "live"
        self.answer.clear()
        self._display_question(
            "Reading the screenshot…" if source == "screenshot" else text, source
        )
        self.chip.setText("classifying…")
        self.chip.setToolTip("")
        self._hide_warning()
        self._fix_row_open = False
        self.fix_row.setVisible(False)
        self.fix_btn.setEnabled(False)
        self.answer_btn.setEnabled(False)
        self._update_dot(thinking=True)
        self._update_nav()

    def on_question_pending(self, text: str, lag: object) -> None:
        """Manual mode: a voice question was heard and is waiting for Answer/Ctrl+Enter."""
        self.transcript.set_transcript(text)
        if not self._text and self._history_index >= len(self._qa_history):
            self._display_question(text, "voice")
        self.chip.setText("ready - press Answer")
        self.chip.setToolTip("")
        self.answer_btn.setEnabled(True)

    def on_decision(self, gen: int, d: Decision) -> None:
        if gen != self._gen:
            return
        self.chip.setText(f"{d.category.value} · {d.tier}")
        self._live_origin = "AI-generated"
        self.origin_label.setText(self._live_origin)
        self._last_decision = d
        self.fix_btn.setEnabled(d.should_answer)
        if d.should_answer:
            i = self.category_combo.findData(d.category.value)
            if i >= 0:
                self.category_combo.setCurrentIndex(i)

    def on_delta(self, gen: int, delta: str) -> None:
        if gen != self._gen:
            return
        self._text += delta
        self._update_dot(answering=True)
        if not self._render_timer.isActive():
            self._render_timer.start()

    def on_reset(self, gen: int) -> None:
        if gen == self._gen:
            self._text = ""
            self.chip.setText("switching provider…")
            self._hide_warning()
            self._render()

    def on_finished(self, gen: int, route: str, first_token_s: float, total_s: float) -> None:
        if gen != self._gen:
            return
        self._live_origin = (
            "Saved fictional Q&A" if route == "Saved fictional Q&A" else "AI-generated"
        )
        self._render()
        self.origin_label.setText(self._live_origin)
        self._answer_origins.append(self._live_origin)
        detected, answer = split_detected_question(self._text)
        if self._live_origin == "Saved fictional Q&A":
            detected, answer = "", self._text
        question = detected or self._current_question
        self._qa_history.append((question, answer, self._current_source))
        self._history_index = len(self._qa_history)  # still live, now one past the new entry
        self._update_nav()
        cat = self.chip.text().split(" · ")[0]
        cost = f" · {format_usd(self._answer_cost)}" if self._answer_cost is not None else ""
        if route == "Saved fictional Q&A":
            self.chip.setText(f"Local lookup · {first_token_s * 1000:.0f} ms · $0")
        else:
            self.chip.setText(f"{cat} · {route} · {first_token_s:.1f}s{cost}")
        self._update_dot()

    def on_failed(self, gen: int, message: str) -> None:
        if gen == self._gen:
            self._render()
            self.chip.setText("error")
            self.answer.append(f"\n⚠ {message}")
            self._update_dot()

    def on_incomplete(self, gen: int, route: str, first_token_s: float, total_s: float) -> None:
        if gen != self._gen:
            return
        self.on_finished(gen, route, first_token_s, total_s)
        self._incomplete = True
        self._live_origin = "AI-generated · Incomplete — output limit reached"
        self.origin_label.setText(self._live_origin)
        self._answer_origins[-1] = self._live_origin
        self.continue_btn.setVisible(True)
        self.continue_btn.setEnabled(not self._busy)
        self.chip.setText("Incomplete — click Continue answer")

    def _continue_answer(self) -> None:
        if self._controller is not None and self._incomplete and not self._busy:
            self.continue_btn.setEnabled(False)
            self._controller.continue_answer(self._gen)

    def on_continuation_failed(self, gen: int) -> None:
        if gen == self._gen:
            self._incomplete = True
            self._live_origin = "AI-generated · Incomplete — continuation failed"
            self.origin_label.setText(self._live_origin)
            self.continue_btn.show()

    def on_latency(self, gen: int, timings: dict) -> None:
        """Hover the chip to see where the time went."""
        if gen != self._gen:
            return
        names = (
            ("speech_to_question_s", "Speech ended -> question ready (transcribe + merge)"),
            ("question_to_first_token_s", "Question -> first answer word (classify + model)"),
            ("total_s", "Whole answer"),
        )
        lines = [f"{label}: {timings[k]:.1f}s" for k, label in names if timings.get(k) is not None]
        self.chip.setToolTip(chr(10).join(lines))

    def on_flags(self, gen: int, claims: list) -> None:
        """Numeric-sounding claims the answer stated that were not in the profile/question."""
        if gen != self._gen:
            return
        if claims:
            self.warn_label.setText("⚠ Verify before saying: " + ", ".join(claims))
            self.warn_label.setVisible(True)
        else:
            self._hide_warning()

    def _hide_warning(self) -> None:
        self.warn_label.setText("")
        self.warn_label.setVisible(False)

    def on_category_corrected(self, _question: str, original: str, corrected: str) -> None:
        # A brief hint on the chip; on_finished/on_latency overwrite it once the redo completes.
        self.chip.setToolTip(f"You corrected this from {original} to {corrected}.")

    def on_answer_cost(self, gen: int, usd: object) -> None:
        if gen == self._gen and isinstance(usd, float):
            self._answer_cost = usd

    def on_cost_updated(self, summary: Summary, state: BudgetState) -> None:
        text = format_usd(summary.total_usd, summary.unpriced_calls > 0)
        if state.limit_usd is not None:
            text += f" / {format_usd(state.limit_usd)}"
        self.cost_label.setText(text)
        self.cost_label.setStyleSheet(f"color: {COST_COLOR[state.level]}; font-size: 11px;")
        lines = [f"Session: {format_usd(summary.total_usd)} ({summary.calls} calls)"]
        lines += [f"  {k}: {format_usd(v)}" for k, v in sorted(summary.by_model.items())]
        if summary.stt_usd:
            lines.append(f"  speech-to-text: {format_usd(summary.stt_usd)}")
        if summary.wasted_usd:
            lines.append(f"  wasted (failed/cancelled): {format_usd(summary.wasted_usd)}")
        if summary.unpriced_calls:
            lines.append(f"  {summary.unpriced_calls} call(s) on unpriced models (not counted)")
        self.cost_label.setToolTip("\n".join(lines))

    def on_stopped(self, gen: int) -> None:
        if gen == self._gen:
            self._incomplete = False
            self.continue_btn.hide()
            self.chip.setText("stopped")
            self._update_dot()

    def on_live_text(self, text: str) -> None:
        if text.strip():
            self.transcript.setToolTip("")
            if self.status_label.text() in ("No words", "Speech status"):
                self._update_dot()
            self.transcript.set_transcript(text)

    def on_transcription_status(self, message: str) -> None:
        if message:
            self.status_label.setText(
                "No words" if message == NO_WORDS_MESSAGE else "Speech status"
            )
            self.transcript.set_transcript(message)
            self.transcript.setToolTip(message)
        else:
            self.transcript.setToolTip("")
            self._update_dot()

    def set_listening(self, listening: bool) -> None:
        self._listening = listening
        self._update_dot()

    # ---- rendering ----
    def _render(self) -> None:
        if self._history_index < len(self._qa_history):
            return  # the user is reviewing a past answer (prev_qa) - don't hijack their view
        if self._live_origin == "Saved fictional Q&A" and not self._locally_formatted:
            self.answer.setPlainText(self._text)
            return
        bar = self.answer.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        detected, answer = split_detected_question(self._text)
        if detected:
            self._display_question(detected, self._current_source)
        self.answer.setMarkdown(answer)
        if at_bottom:
            bar.setValue(bar.maximum())

    def _update_dot(self, thinking: bool = False, answering: bool = False) -> None:
        state = "answering" if answering else "thinking" if thinking else "idle"
        if state == "idle" and self._listening:
            state = "listening"
        self.dot.setStyleSheet(f"color: {DOT[state]}; font-size: 14px;")
        self.dot.setToolTip(state)
        self.status_label.setText(STATUS_TEXT[state])

    def bind_menu(
        self,
        actions: list[tuple[str, Callable[[], None]]],
        devices: list[tuple[str, AudioPanel]],
    ) -> None:
        """The ⋮ menu: pick each source's device, plus the actions that used to live in the
        separate main window (History, Settings, Privacy, New interview, Show transcript)."""
        self._menu_actions = actions
        self._menu_devices = devices
        self.more_btn.clicked.connect(self._show_more_menu)

    def _build_more_menu(self) -> QMenu:
        menu = QMenu(self)
        for label, panel in self._menu_devices:
            sub = menu.addMenu(f"{label} device")
            combo = panel.device_combo
            for i in range(combo.count()):
                sub.addAction(combo.itemText(i), partial(combo.setCurrentIndex, i))
        if self._menu_devices:
            menu.addSeparator()
        for label, action in self._menu_actions:
            menu.addAction(label, action)
        return menu

    def _show_more_menu(self) -> None:
        menu = self._build_more_menu()
        menu.exec(self.more_btn.mapToGlobal(self.more_btn.rect().bottomLeft()))

    def _tick(self) -> None:
        secs = int(time.monotonic() - self._started)
        self.timer_label.setText(f"{secs // 60:02d}:{secs % 60:02d}")

    def _clear_view(self) -> None:
        """Clears what is on screen only; the Q&A history and the saved history are untouched."""
        self._text = ""
        self.answer.clear()
        self._hide_warning()
        self.continue_btn.hide()
        self._incomplete = False

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.stop_btn.setEnabled(busy)
        self.continue_btn.setEnabled(not busy)
        if not busy:
            self._update_dot()

    @property
    def answer_text(self) -> str:
        return self._text

    # ---- user actions ----
    def _submit_typed(self) -> None:
        text = self.input.text().strip()
        if text.casefold() == "fix format":
            from interview_assistant.app.answer_format import format_answer_blocks

            source = (
                self._qa_history[self._history_index][1]
                if self._history_index < len(self._qa_history)
                else self._text
            )
            if source:
                if self._busy:
                    self.chip.setText(
                        "Still streaming — format is checked when the answer finishes"
                    )
                else:
                    formatted = format_answer_blocks(source)
                    if self._history_index < len(self._qa_history):
                        question, _, origin = self._qa_history[self._history_index]
                        self._qa_history[self._history_index] = (question, formatted, origin)
                    else:
                        self._text = formatted
                        self._locally_formatted = True
                    self.answer.setMarkdown(formatted)
                    self.chip.setText("Format fixed locally")
            else:
                self.chip.setText("No answer to format yet")
            self.input.clear()
            return
        if text and self._controller is not None:
            self.input.clear()
            self._controller.ask(text, "typed")

    def _stop(self) -> None:
        if self._controller is not None:
            self._incomplete = False
            self.continue_btn.hide()
            self._controller.stop()
            self.chip.setText("stopped")

    def answer_now(self) -> None:
        """Answer the waiting question (Answer button, Ctrl+Enter, or the global hotkey)."""
        if self._controller is None:
            return
        if not self.answer_btn.isEnabled():
            self.chip.setText(
                "no question waiting - speak one, or type it in Chat (Ctrl+Shift+Space)"
            )
            return
        self._controller.trigger_answer()

    def take_screenshot(self) -> None:
        """Explicit user action only (button or shortcut) - never captured automatically. The
        screenshot is answered immediately."""
        if self._controller is None:
            return
        if self.before_screenshot is not None and not self.before_screenshot():
            return
        image = capture_screen_png()
        if image is not None:
            self._controller.ask_screenshot(image)

    def _toggle_fix_row(self) -> None:
        # Track state explicitly: fix_row.isVisible() is unreliable while the window itself is
        # hidden (it always reports False then, regardless of what we last set).
        self._fix_row_open = not self._fix_row_open
        self.fix_row.setVisible(self._fix_row_open)

    def toggle_chat(self) -> None:
        # Same explicit-flag idiom as _toggle_fix_row, and for the same reason. Unlike the fix
        # row, this one is left open after sending a question - it's a small persistent chat box,
        # not a one-shot action.
        self._chat_row_open = not self._chat_row_open
        self.chat_row.setVisible(self._chat_row_open)
        if self._chat_row_open:
            self.input.setFocus()

    def _apply_fix(self) -> None:
        if self._controller is None:
            return
        category = self.category_combo.currentData()
        if category:
            self._controller.correct_category(Category(category))
        self._fix_row_open = False
        self.fix_row.setVisible(False)

    def copy_answer(self) -> None:
        """Copies whatever is currently shown - the live answer, or a past one if navigating
        the Q&A history (see prev_qa/next_qa)."""
        if self._history_index < len(self._qa_history):
            text = self._qa_history[self._history_index][1]
        else:
            text = self._text
        QApplication.clipboard().setText(text)

    def toggle_collapsed(self) -> None:
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._expanded_height = self.height()
        self.body.setVisible(not self._collapsed)
        self.transcript_panel.setVisible(not self._collapsed)
        self.collapse_btn.setText("+" if self._collapsed else "–")
        self.setMinimumHeight(48 if self._collapsed else 90)
        self.resize(self.width(), 48 if self._collapsed else self._expanded_height)

    def toggle_bubble(self) -> None:
        """Collapse the whole window down to a small draggable dot (Hide-to-bubble, Ctrl+H by
        default) - further than toggle_collapsed(), which still shows the header. Click the
        bubble (a plain click, not a drag) to restore."""
        self._bubble = not self._bubble
        if self._bubble:
            if not self._collapsed:
                self._expanded_height = self.height()
            self._restore_width = self.width()
        for w in self._header_extras:
            w.setVisible(not self._bubble)
        # Move the status dot into the toolbar so the bubble retains a visible indicator.
        if self._bubble:
            self._toolbar_layout.addWidget(self.dot)
        else:
            self._transcript_layout.insertWidget(0, self.dot)
            self.mic_toggle.setVisible(self._mic_bound)
        self.dot.show()
        self.transcript_panel.setVisible(not self._bubble and not self._collapsed)
        self.body.setVisible(not self._bubble and not self._collapsed)
        if self._bubble:
            self.setMinimumSize(BUBBLE_SIZE, BUBBLE_SIZE)
            self.resize(BUBBLE_SIZE, BUBBLE_SIZE)
        else:
            self.setMinimumSize(640, 48 if self._collapsed else 90)
            self.resize(self._restore_width, 48 if self._collapsed else self._expanded_height)
        self.update()  # repaint with the bubble's round-vs-rounded-rect shape

    @property
    def bubbled(self) -> bool:
        return self._bubble

    # ---- Q&A history stack (in-memory, this session only - not the persistent SQLite history) ----
    def _display_question(self, text: str, source: str) -> None:
        icon = {"voice": "🎤 ", "screenshot": "📷 "}.get(source, "⌨ ")
        self.question.setText(icon + text)

    def _update_nav(self) -> None:
        n = len(self._qa_history)
        self.nav_row.setVisible(n > 0)
        if n == 0:
            return
        live = self._history_index >= n
        self.history_label.setText("Live" if live else f"{self._history_index + 1} of {n}")
        self.prev_btn.setEnabled(self._history_index > 0)
        self.next_btn.setEnabled(not live)

    def _show_at(self, index: int) -> None:
        """Read-only: displays a past Q&A without touching self._text or re-triggering anything."""
        self.continue_btn.setVisible(self._incomplete and index >= len(self._qa_history))
        if index >= len(self._qa_history):
            self.origin_label.setText(self._live_origin)
            self._display_question(self._current_question, self._current_source)
            self._render()  # restores the live answer
        else:
            self.origin_label.setText(
                self._answer_origins[index] if index < len(self._answer_origins) else ""
            )
            question, answer, source = self._qa_history[index]
            self._display_question(question, source)
            if self.origin_label.text() == "Saved fictional Q&A":
                self.answer.setPlainText(answer)
            else:
                self.answer.setMarkdown(answer)
        self._update_nav()

    def prev_qa(self) -> None:
        """One step per call, on purpose: the first prev_qa() from live lands on index
        len-1, which holds the SAME content as what was just live (confirming "yes, this is
        your most recent answer, 2 of 2") - a second call is needed to reach genuinely older
        content. Not a bug; matches how the index/count label should read."""
        if self._history_index > 0:
            self._history_index -= 1
            self._show_at(self._history_index)

    def next_qa(self) -> None:
        if self._history_index < len(self._qa_history):
            self._history_index += 1
            self._show_at(self._history_index)

    def _set_opacity(self, value: int) -> None:
        self.setWindowOpacity(value / 100)
        self._settings.setValue("opacity", value)

    # ---- window behaviour ----
    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(BG)
        p.setPen(BORDER)
        if self._bubble:
            p.drawEllipse(self.rect().adjusted(0, 0, -1, -1))
        else:
            for panel in (self.header, self.transcript_panel, self.body):
                if panel.isVisibleTo(self):
                    paint_glass(p, QRectF(panel.geometry()), 14)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton and (self._bubble or e.position().y() < 44):
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._press_global = e.globalPosition().toPoint()

    def mouseMoveEvent(self, e) -> None:  # noqa: N802
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e) -> None:  # noqa: N802
        if self._bubble and self._press_global is not None:
            moved = (e.globalPosition().toPoint() - self._press_global).manhattanLength()
            if moved < 4:  # a click, not a drag: restore from the bubble
                self.toggle_bubble()
        self._drag = None
        self._press_global = None

    def _restore(self) -> None:
        s = self._settings
        try:
            op = int(str(s.value("opacity", 95)))
        except (TypeError, ValueError):
            op = 95
        self.opacity.setValue(max(30, min(100, op)))
        self.setWindowOpacity(self.opacity.value() / 100)
        geo = s.value("geometry")
        if geo is not None and self.restoreGeometry(geo):
            return
        self.resize(800, 420)
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - 24, area.top() + 24)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._settings.setValue("geometry", self.saveGeometry())
        super().hideEvent(event)
