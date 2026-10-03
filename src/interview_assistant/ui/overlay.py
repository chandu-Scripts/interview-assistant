import time
from collections.abc import Callable

from PySide6.QtCore import QPoint, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizeGrip,
    QSlider,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.agents.specialists import ANSWERABLE_CATEGORIES, LABELS
from interview_assistant.agents.types import Category, Decision
from interview_assistant.app.controller import AssistantController
from interview_assistant.cost.tracker import BudgetLevel, BudgetState, Summary, format_usd
from interview_assistant.ui.screenshot import capture_screen_png

# Dark translucent live bar, matching the Parakeet layout the user asked for: one toolbar with
# labelled shortcut buttons, a listening line under it, and the answer panel below.
BG = QColor(22, 24, 32, 235)
BORDER = QColor(58, 62, 76)
STYLE = """
QLabel { color: #e8eaf2; }
#title { font-weight: 600; }
#chip { color: #9fb4ff; font-size: 11px; }
#question { color: #aab0c4; font-size: 12px; }
#warn { color: #ffb86b; font-size: 11px; }
#timer { color: #e8eaf2; font-size: 12px; }
#status { color: #aab0c4; font-size: 12px; }
QTextBrowser { background: transparent; color: #e8eaf2; border: none; font-size: 14px; }
QLineEdit { background: rgba(255,255,255,0.06); color: #e8eaf2; border: 1px solid #3a3e4c;
            border-radius: 6px; padding: 4px 8px; }
QPushButton { background: rgba(255,255,255,0.08); color: #e8eaf2; border: none;
              border-radius: 8px; padding: 5px 10px; }
QPushButton:hover { background: rgba(255,255,255,0.16); }
QPushButton:disabled { color: #5d6276; background: rgba(255,255,255,0.04); }
#iconbtn { padding: 2px 8px; font-weight: bold; }
#pill { font-weight: 600; }
#end { background: #d6363f; color: #ffffff; font-weight: 600; }
#end:hover { background: #e5484f; }
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
SCREENSHOT_TIP = "Attach a screenshot of your screen to your next question (Ctrl+Shift+Enter)"


class OverlayWindow(QWidget):
    """Compact always-on-top answer window.

    A normal, fully visible window: frameless, draggable, resizable, adjustable opacity. It
    makes no attempt to hide itself from screen capture; that is Step 10's explicit mode.
    """

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
        self._history_index = 0  # == len(self._qa_history) means "viewing the live answer"
        self._drag: QPoint | None = None
        self._press_global: QPoint | None = None
        self._collapsed = False
        self._bubble = False
        self._expanded_height = 340
        self._restore_width = 460
        self._listening = False
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
        self.setMinimumSize(320, 90)
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
        self.answer_btn = self._shortcut_button("Answer", "Ctrl ↵", "Answer the question above")
        self.answer_btn.setEnabled(False)
        self.screenshot_btn = self._shortcut_button("Screenshot", "Ctrl ⇧ ↵", SCREENSHOT_TIP)
        self.chat_btn = self._shortcut_button("Chat", "Ctrl ⇧ `", "Type a question (toggle)")
        self.fix_btn = self._icon_button("✎", "Wrong specialist? Pick the right one and redo")
        self.collapse_btn = self._icon_button("–", "Collapse / expand")
        self.close_btn = QPushButton("End")
        self.close_btn.setObjectName("end")
        self.close_btn.setToolTip("Hide the overlay (Ctrl+H collapses it to a bubble)")
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setToolTip("Clear the answers on screen (history is kept)")
        # Widgets hidden in bubble mode (everything except the status dot, which IS the bubble).
        self._header_extras = (
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
            self.close_btn,
            self.clear_btn,
        )
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        w: QWidget
        for w in (self.answer_btn, self.screenshot_btn, self.chat_btn):
            top_row.addWidget(w)
        top_row.addStretch(1)
        for w in (self.fix_btn, self.collapse_btn, self.timer_label, self.close_btn):
            top_row.addWidget(w)
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        for w in (self.dot, self.status_label, self.title):
            status_row.addWidget(w)
        status_row.addWidget(self.chip, 1)
        status_row.addWidget(self.cost_label)
        status_row.addWidget(self.clear_btn)
        header = QVBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addLayout(top_row)
        header.addLayout(status_row)
        self.header = QWidget()
        self.header.setLayout(header)

        self.question = QLabel("Waiting for a question…")
        self.question.setObjectName("question")
        self.question.setWordWrap(True)
        self.prev_btn = self._icon_button("◀", "Previous answer (Ctrl+Left)")
        self.next_btn = self._icon_button("▶", "Next answer (Ctrl+Right)")
        self.history_label = QLabel("")
        self.history_label.setObjectName("question")
        nav_row = QHBoxLayout()
        nav_row.setContentsMargins(0, 0, 0, 0)
        nav_row.addWidget(self.prev_btn)
        nav_row.addWidget(self.history_label, 1)
        nav_row.addWidget(self.next_btn)
        self.nav_row = QWidget()
        self.nav_row.setLayout(nav_row)
        self.nav_row.setVisible(False)  # appears once there's at least one completed answer
        self.warn_label = QLabel("")
        self.warn_label.setObjectName("warn")
        self.warn_label.setWordWrap(True)
        self.warn_label.setVisible(False)
        self.category_combo = QComboBox()
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
        self.answer = QTextBrowser()
        self.answer.setOpenExternalLinks(False)
        self.answer.setPlaceholderText("Answers stream in here.")

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
        self.copy_btn = QPushButton("Copy")
        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(30, 100)
        self.opacity.setFixedWidth(70)
        self.opacity.setToolTip("Opacity")
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addStretch(1)
        for extra in (self.stop_btn, self.copy_btn, self.opacity):
            footer.addWidget(extra)
        self.footer = QWidget()
        self.footer.setLayout(footer)

        self.body = QWidget()
        body = QVBoxLayout(self.body)
        body.setContentsMargins(0, 0, 0, 0)
        body.addWidget(self.question)
        body.addWidget(self.nav_row)
        body.addWidget(self.warn_label)
        body.addWidget(self.fix_row)
        body.addWidget(self.chat_row)
        body.addWidget(self.answer, 1)
        body.addWidget(self.footer)
        grip_row = QHBoxLayout()
        grip_row.addStretch(1)
        grip_row.addWidget(QSizeGrip(self))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 4)
        layout.setSpacing(6)
        layout.addWidget(self.header)
        layout.addWidget(self.body, 1)
        layout.addLayout(grip_row)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(50)
        self._render_timer.timeout.connect(self._render)

        self.fix_btn.clicked.connect(self._toggle_fix_row)
        self.fix_apply_btn.clicked.connect(self._apply_fix)
        self.fix_btn.setEnabled(False)
        self.chat_btn.clicked.connect(self._toggle_chat_row)
        self.collapse_btn.clicked.connect(self.toggle_collapsed)
        self.close_btn.clicked.connect(self.hide)
        self.copy_btn.clicked.connect(self.copy_answer)
        self.opacity.valueChanged.connect(self._set_opacity)
        self.input.returnPressed.connect(self._submit_typed)
        self.ask_btn.clicked.connect(self._submit_typed)
        self.answer_btn.clicked.connect(self._trigger_answer)
        self.screenshot_btn.clicked.connect(self._take_screenshot)
        QShortcut(QKeySequence("Ctrl+Shift+Return"), self, self._take_screenshot)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self, self._take_screenshot)
        self.clear_btn.clicked.connect(self._clear_view)
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._started = time.monotonic()
        self._clock.timeout.connect(self._tick)
        self._clock.start()
        QShortcut(QKeySequence("Ctrl+Return"), self, self._trigger_answer)
        QShortcut(QKeySequence("Ctrl+Enter"), self, self._trigger_answer)  # numpad Enter
        self.prev_btn.clicked.connect(self.prev_qa)
        self.next_btn.clicked.connect(self.next_qa)
        QShortcut(QKeySequence("Ctrl+Left"), self, self.prev_qa)
        QShortcut(QKeySequence("Ctrl+Right"), self, self.next_qa)
        QShortcut(QKeySequence("Escape"), self, self._stop)
        self.stop_btn.clicked.connect(self._stop)

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

    def _shortcut_button(self, label: str, keys: str, tip: str) -> QPushButton:
        b = QPushButton(f"{label}   {keys}")
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
        c.answer_failed.connect(self.on_failed)
        c.answer_stopped.connect(self.on_stopped)
        c.live_text.connect(self.on_live_text)
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
        self._gen = gen
        self._text = ""
        self._answer_cost = None
        self._last_decision = None
        self._current_question, self._current_source = text, source
        self._history_index = len(self._qa_history)  # a new question always means "live"
        self.answer.clear()
        self._display_question(text, source)
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
        self._display_question(text, "voice")
        self.chip.setText("ready - press Answer")
        self.chip.setToolTip("")
        self.answer_btn.setEnabled(True)

    def on_decision(self, gen: int, d: Decision) -> None:
        if gen != self._gen:
            return
        self.chip.setText(f"{d.category.value} · {d.tier}")
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
        self._render()
        self._qa_history.append((self._current_question, self._text, self._current_source))
        self._history_index = len(self._qa_history)  # still live, now one past the new entry
        self._update_nav()
        cat = self.chip.text().split(" · ")[0]
        cost = f" · {format_usd(self._answer_cost)}" if self._answer_cost is not None else ""
        self.chip.setText(f"{cat} · {route} · {first_token_s:.1f}s{cost}")
        self._update_dot()

    def on_failed(self, gen: int, message: str) -> None:
        if gen == self._gen:
            self._render()
            self.chip.setText("error")
            self.answer.append(f"\n⚠ {message}")
            self._update_dot()

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
            self.chip.setText("stopped")
            self._update_dot()

    def on_live_text(self, text: str) -> None:
        if text:
            self.question.setText(f"🎤 {text} …")

    def set_listening(self, listening: bool) -> None:
        self._listening = listening
        self._update_dot()

    # ---- rendering ----
    def _render(self) -> None:
        if self._history_index < len(self._qa_history):
            return  # the user is reviewing a past answer (prev_qa) - don't hijack their view
        bar = self.answer.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        self.answer.setMarkdown(self._text)
        if at_bottom:
            bar.setValue(bar.maximum())

    def _update_dot(self, thinking: bool = False, answering: bool = False) -> None:
        state = "answering" if answering else "thinking" if thinking else "idle"
        if state == "idle" and self._listening:
            state = "listening"
        self.dot.setStyleSheet(f"color: {DOT[state]}; font-size: 14px;")
        self.dot.setToolTip(state)
        self.status_label.setText(STATUS_TEXT[state])

    def _tick(self) -> None:
        secs = int(time.monotonic() - self._started)
        self.timer_label.setText(f"{secs // 60:02d}:{secs % 60:02d}")

    def _clear_view(self) -> None:
        """Clears what is on screen only; the Q&A history and the saved history are untouched."""
        self._text = ""
        self.answer.clear()
        self._hide_warning()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.stop_btn.setEnabled(busy)
        if not busy:
            self._update_dot()

    @property
    def answer_text(self) -> str:
        return self._text

    # ---- user actions ----
    def _submit_typed(self) -> None:
        text = self.input.text().strip()
        if text and self._controller is not None:
            self.input.clear()
            self._controller.ask(text, "typed")

    def _stop(self) -> None:
        if self._controller is not None:
            self._controller.stop()

    def _trigger_answer(self) -> None:
        if self._controller is not None:
            self._controller.trigger_answer()

    def _take_screenshot(self) -> None:
        """Explicit user action only (button or Ctrl+Shift+S) - never captured automatically."""
        if self._controller is None:
            return
        if self.before_screenshot is not None and not self.before_screenshot():
            return
        image = capture_screen_png()
        if image is not None:
            self._controller.attach_screenshot(image)

    def _toggle_fix_row(self) -> None:
        # Track state explicitly: fix_row.isVisible() is unreliable while the window itself is
        # hidden (it always reports False then, regardless of what we last set).
        self._fix_row_open = not self._fix_row_open
        self.fix_row.setVisible(self._fix_row_open)

    def _toggle_chat_row(self) -> None:
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
        self.body.setVisible(not self._bubble and not self._collapsed)
        if self._bubble:
            self.setMinimumSize(BUBBLE_SIZE, BUBBLE_SIZE)
            self.resize(BUBBLE_SIZE, BUBBLE_SIZE)
        else:
            self.setMinimumSize(320, 90)
            self.resize(self._restore_width, 48 if self._collapsed else self._expanded_height)
        self.update()  # repaint with the bubble's round-vs-rounded-rect shape

    @property
    def bubbled(self) -> bool:
        return self._bubble

    # ---- Q&A history stack (in-memory, this session only - not the persistent SQLite history) ----
    def _display_question(self, text: str, source: str) -> None:
        self.question.setText(("🎤 " if source == "voice" else "⌨ ") + text)

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
        if index >= len(self._qa_history):
            self._display_question(self._current_question, self._current_source)
            self._render()  # restores the live answer
        else:
            question, answer, source = self._qa_history[index]
            self._display_question(question, source)
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
        radius = min(self.width(), self.height()) // 2 if self._bubble else 12
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), radius, radius)

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
        self.resize(460, 340)
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
