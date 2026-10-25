"""Review one interview without changing live interview state."""

import sqlite3
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.app.post_call import CONTEXT_LIMIT, PostCallController
from interview_assistant.history.store import HistoryStore, StoredTurn


def transcript_text(turns: list[StoredTurn]) -> str:
    sources = {
        "voice:loopback": "System audio",
        "voice:mic": "Microphone",
        "voice": "Voice",
        "typed": "Typed question",
        "screenshot": "Screenshot",
    }
    return "\n\n".join(
        f"Question {i} · {datetime.fromtimestamp(t.ts).strftime('%H:%M:%S')} · "
        f"{sources.get(t.source, 'Question')}\n"
        f"{t.question}\n\nAI answer ({t.status}):\n{t.answer or '(no answer saved)'}"
        for i, t in enumerate(turns, 1)
    )


class PostCallPage(QWidget):
    back_requested = Signal()

    def __init__(self, store: HistoryStore, controller: PostCallController | None = None) -> None:
        super().__init__()
        self._store, self._controller = store, controller
        self._session_id: int | None = None
        self._generation = -1
        self._transcript = ""
        self._preferred = ""
        self._drafts: dict[int, str] = {}
        self.title = QLabel("Session review")
        self.title.setObjectName("title")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setWordWrap(True)
        self.back_btn = QPushButton("Back to sessions")
        self.back_btn.clicked.connect(self.back_requested)
        top = QHBoxLayout()
        top.addWidget(self.title, 1)
        top.addWidget(self.back_btn)
        hint = QLabel("Review saved questions and AI answers. This is not a full call recording.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        self.tabs = QTabWidget()
        self.transcript = QPlainTextEdit()
        self.transcript.setReadOnly(True)
        self.tabs.addTab(self.transcript, "Transcript")
        notes_page = QWidget()
        notes_layout = QVBoxLayout(notes_page)
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("What went well? What would you improve next time?")
        self.notes_status = QLabel("Notes are saved locally only when you click Save Notes.")
        self.notes_status.setWordWrap(True)
        self.save_btn = QPushButton("Save Notes")
        self.save_btn.setObjectName("create")
        notes_layout.addWidget(self.notes, 1)
        notes_layout.addWidget(self.notes_status)
        notes_layout.addWidget(self.save_btn, alignment=Qt.AlignmentFlag.AlignRight)
        self.tabs.addTab(notes_page, "Notes")
        ask_page = QWidget()
        ask_layout = QVBoxLayout(ask_page)
        self.context_notice = QLabel()
        self.context_notice.setWordWrap(True)
        self.answer = QTextBrowser()
        self.answer.setOpenLinks(False)
        self.answer.setPlaceholderText(
            "Ask about the topics covered, strengths, or areas to practice."
        )
        self.question = QLineEdit()
        self.question.setPlaceholderText("What should I practice based on this session?")
        self.question.setMaxLength(4000)
        self.ask_btn = QPushButton("Ask AI")
        self.ask_btn.setObjectName("create")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.ask_status = QLabel()
        self.ask_status.setWordWrap(True)
        row = QHBoxLayout()
        row.addWidget(self.question, 1)
        row.addWidget(self.ask_btn)
        row.addWidget(self.stop_btn)
        ask_layout.addWidget(self.context_notice)
        ask_layout.addWidget(self.answer, 1)
        ask_layout.addLayout(row)
        ask_layout.addWidget(self.ask_status)
        self.tabs.addTab(ask_page, "Ask AI")
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.tabs.addTab(self.details, "Call details")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(16)
        layout.addLayout(top)
        layout.addWidget(hint)
        layout.addWidget(self.tabs, 1)
        self.save_btn.clicked.connect(self.save_notes)
        self.notes.textChanged.connect(self._remember_draft)
        self.ask_btn.clicked.connect(self.ask)
        self.question.returnPressed.connect(self.ask)
        self.stop_btn.clicked.connect(self.stop)
        if controller is not None:
            controller.updated.connect(self._updated)
            controller.finished.connect(self._finished)

    def load(self, session_id: int) -> None:
        self.stop()
        self._session_id = session_id
        session = self._store.get_interview_session(session_id)
        self.title.setText(
            session.company or "Untitled session" if session else "Session unavailable"
        )
        turns = self._store.get_interview_turns(session_id)
        self._transcript = transcript_text(turns)
        self.transcript.setPlainText(self._transcript or "No Q&A was saved for this session.")
        self.notes.blockSignals(True)
        self.notes.setPlainText(
            self._drafts.get(session_id, self._store.get_interview_notes(session_id))
        )
        self.notes.blockSignals(False)
        self.notes_status.setText(
            "Unsaved notes"
            if session_id in self._drafts
            else "Notes are saved locally only when you click Save Notes."
        )
        self.save_btn.setEnabled(session is not None)
        self.answer.clear()
        self.question.clear()
        self.ask_status.clear()
        self.ask_btn.setEnabled(bool(turns) and self._controller is not None)
        self.context_notice.setText(
            "Ask sends this session's saved Q&A and your question to your configured AI provider. "
            "Responses are not saved. "
            + (
                f"Only the first {CONTEXT_LIMIT:,} characters are used."
                if len(self._transcript) > CONTEXT_LIMIT
                else ""
            )
        )
        self._preferred = (
            (session.fast_model_route or session.model_route_override) if session else ""
        )
        ended = (
            datetime.fromtimestamp(session.ended_at).strftime("%b %d, %Y %H:%M")
            if session and session.ended_at is not None
            else "Not ended"
        )
        self.details.setPlainText(
            f"Company: {session.company}\nRole: {session.role_title}\n"
            f"Type: {session.session_type}\n"
            f"Created: {datetime.fromtimestamp(session.created_at):%b %d, %Y %H:%M}\n"
            f"Ended: {ended}\nSaved questions: {len(turns)}\n"
            f"Recorded answer cost: ${sum(t.cost_usd or 0 for t in turns):.4f}\n"
            f"Preferred model: {self._preferred or 'Auto'}\nAnswer language: {session.language}\n\n"
            f"Job description\n{session.job_description or '(not provided)'}"
            if session
            else "This session no longer exists."
        )
        self.tabs.setCurrentIndex(0)

    def _remember_draft(self) -> None:
        if self._session_id is not None:
            self._drafts[self._session_id] = self.notes.toPlainText()
            self.notes_status.setText("Unsaved notes · click Save Notes before closing the app")

    def save_notes(self) -> None:
        if self._session_id is None:
            return
        try:
            self._store.save_interview_notes(self._session_id, self.notes.toPlainText())
        except (ValueError, sqlite3.Error) as e:
            self.notes_status.setText(str(e))
            return
        self._drafts.pop(self._session_id, None)
        self.notes_status.setText("Notes saved on this device")

    def ask(self) -> None:
        if (
            self._controller is None
            or not self.ask_btn.isEnabled()
            or not self.question.text().strip()
        ):
            return
        if self._session_id is None:
            return
        self._transcript = transcript_text(self._store.get_interview_turns(self._session_id))
        if not self._transcript:
            self.ask_status.setText("No saved Q&A remains for this session.")
            self.ask_btn.setEnabled(False)
            return
        self.answer.clear()
        self.ask_status.setText("Reviewing saved Q&A…")
        self.ask_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self._generation = self._controller.ask(
            self.question.text().strip(), self._transcript, self._preferred
        )

    def _updated(self, generation: int, text: str) -> None:
        if generation == self._generation:
            self.answer.setMarkdown(text)

    def _finished(self, generation: int, status: str) -> None:
        if generation == self._generation:
            self.ask_status.setText(status)
            self.ask_btn.setEnabled(bool(self._transcript))
            self.stop_btn.setEnabled(False)

    def stop(self) -> None:
        self._generation = -1
        if self._controller is not None:
            self._controller.stop()
        self.stop_btn.setEnabled(False)
        self.ask_btn.setEnabled(bool(self._transcript) and self._controller is not None)
        self.ask_status.setText("Stopped")

    def hideEvent(self, event) -> None:  # noqa: N802
        self.stop()
        super().hideEvent(event)
