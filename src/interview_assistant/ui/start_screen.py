from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.app.interview_session import SessionDetails
from interview_assistant.app.resume_library import ResumeLibrary
from interview_assistant.history.store import HistoryStore, InterviewSession
from interview_assistant.ui.session_dialog import SessionDialog

STYLE = """
QWidget#start { background: #16181f; border: 1px solid #3a3e4c; border-radius: 12px; }
QLabel { color: #e8eaf2; background: transparent; }
#title { font-size: 16px; font-weight: 600; }
#hint { color: #aab0c4; font-size: 12px; }
QListWidget { background: rgba(255,255,255,0.04); color: #e8eaf2; border: none;
              border-radius: 8px; padding: 6px; font-size: 13px; }
QListWidget::item { padding: 10px; border-radius: 8px; }
QListWidget::item:selected { background: rgba(255,255,255,0.10); }
QPushButton { background: rgba(255,255,255,0.08); color: #e8eaf2; border: none;
              border-radius: 8px; padding: 8px 14px; }
QPushButton:hover { background: rgba(255,255,255,0.16); }
QPushButton:disabled { color: #5d6276; background: rgba(255,255,255,0.04); }
#create, #connect { background: #2f9e5b; color: #ffffff; font-weight: 600; }
#create:hover, #connect:hover { background: #38b56a; }
QPushButton:checked { background: #2f9e5b; color: #ffffff; }
QLineEdit, QPlainTextEdit, QComboBox { background: rgba(255,255,255,0.06); color: #e8eaf2;
    border: 1px solid #3a3e4c; border-radius: 6px; padding: 4px 8px; }
QComboBox QAbstractItemView { background: #1d2029; color: #e8eaf2;
    selection-background-color: #2f9e5b; }
QCheckBox { color: #e8eaf2; }
"""


def describe_session(s: InterviewSession) -> str:
    title = s.company or "Untitled session"
    if s.role_title:
        title += f" — {s.role_title}"
    when = datetime.fromtimestamp(s.created_at).strftime("%b %d, %Y")
    if s.ended_at is None:
        status = "Ready to start"
    else:
        minutes, seconds = divmod(int(s.ended_at - s.created_at), 60)
        status = f"Ended · {minutes}m {seconds:02d}s"
    return f"{title}\n{when} · {status}"


def details_of(s: InterviewSession, library: ResumeLibrary | None) -> SessionDetails:
    """A saved session's settings, to start it again with the same context."""
    return SessionDetails(
        company=s.company,
        role_title=s.role_title,
        job_description=s.job_description,
        resume_override=s.resume_override,
        ai_instructions=s.ai_instructions,
        answer_format=s.answer_format,
        answer_length=s.answer_length,
        answer_tone=s.answer_tone,
        model_route_override=s.model_route_override,
        session_type=s.session_type,
        language=s.language,
        auto_generate=s.auto_generate,
        save_transcript=s.save_transcript,
    )


class StartScreen(QWidget):
    """The first screen. Page 1 lists previous sessions with Create Session and the dashboard.
    Page 2 is the Connect step: Connect starts capture and opens the live bar."""

    def __init__(
        self,
        store: HistoryStore,
        library: ResumeLibrary | None,
        activate: Callable[[SessionDetails], InterviewSession],
        on_connect: Callable[[], None],
        on_dashboard: Callable[[], None],
    ) -> None:
        super().__init__()
        self._store = store
        self._library = library
        self._activate = activate
        self._on_connect = on_connect
        self._connecting: InterviewSession | None = None
        self.setObjectName("start")
        self.setWindowTitle("Interview Assistant")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(STYLE)
        self.resize(560, 600)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._list_page(on_dashboard))
        self.pages.addWidget(self._connect_page())
        self.create_page = SessionDialog(library)
        self.create_page.created.connect(self._session_created)
        self.create_page.cancelled.connect(self._show_list)
        self.pages.addWidget(self.create_page)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.addWidget(self.pages)
        self.refresh()

    # ---- page 1: sessions ----
    def _list_page(self, on_dashboard: Callable[[], None]) -> QWidget:
        page = QWidget()
        self.title = QLabel("Interview Assistant")
        self.title.setObjectName("title")
        self.hint = QLabel("Previous sessions")
        self.hint.setObjectName("hint")
        self.sessions = QListWidget()
        self.sessions.currentRowChanged.connect(lambda _row: self._update_buttons())
        self.start_btn = QPushButton("Start Session")
        self.start_btn.clicked.connect(self._start_selected)
        self.dashboard_btn = QPushButton("View in Dashboard")
        self.dashboard_btn.clicked.connect(on_dashboard)
        self.create_btn = QPushButton("+ Create Session")
        self.create_btn.setObjectName("create")
        self.create_btn.clicked.connect(self._open_create)

        bottom = QHBoxLayout()
        bottom.addWidget(self.dashboard_btn)
        bottom.addStretch(1)
        bottom.addWidget(self.start_btn)
        bottom.addWidget(self.create_btn)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.title)
        layout.addWidget(self.hint)
        layout.addWidget(self.sessions, 1)
        layout.addLayout(bottom)
        return page

    # ---- page 2: connect ----
    def _connect_page(self) -> QWidget:
        page = QWidget()
        self.connect_title = QLabel("Connect Call Session")
        self.connect_title.setObjectName("title")
        self.connect_summary = QLabel("")
        self.connect_summary.setWordWrap(True)
        self.connect_notice = QLabel(
            "Connect turns on the audio sources you enabled and opens the live bar. Use it only "
            "where everyone in the conversation has agreed to be recorded. You will be asked to "
            "confirm the recording notice the first time."
        )
        self.connect_notice.setObjectName("hint")
        self.connect_notice.setWordWrap(True)
        self.back_btn = QPushButton("Back")
        self.back_btn.clicked.connect(self._show_list)
        self.connect_btn = QPushButton("Connect")
        self.connect_btn.setObjectName("connect")
        self.connect_btn.clicked.connect(self._connect)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(self.back_btn)
        bottom.addWidget(self.connect_btn)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.connect_title)
        layout.addWidget(self.connect_summary)
        layout.addWidget(self.connect_notice)
        layout.addStretch(1)
        layout.addLayout(bottom)
        return page

    def _show_connect(self, session: InterviewSession) -> None:
        self._connecting = session
        self.connect_summary.setText(describe_session(session))
        self.pages.setCurrentIndex(1)

    def _show_list(self) -> None:
        self._connecting = None
        self.pages.setCurrentIndex(0)
        self.refresh()

    def _connect(self) -> None:
        if self._connecting is None:
            return
        self.hide()
        self._on_connect()

    # ---- actions ----
    def _open_create(self) -> None:
        self.pages.setCurrentWidget(self.create_page)

    def _session_created(self, details: SessionDetails) -> None:
        self._show_connect(self._activate(details))

    def _selected(self) -> InterviewSession | None:
        item = self.sessions.currentItem()
        if item is None:
            return None
        return self._store.get_interview_session(item.data(Qt.ItemDataRole.UserRole))

    def _start_selected(self) -> None:
        saved = self._selected()
        if saved is None or saved.ended_at is not None:
            return
        self._show_connect(self._activate(details_of(saved, self._library)))

    def _update_buttons(self) -> None:
        saved = self._selected()
        self.start_btn.setEnabled(saved is not None and saved.ended_at is None)

    def refresh(self) -> None:
        keep = self._selected()
        self.sessions.clear()
        for s in self._store.list_interview_sessions():
            item = QListWidgetItem(describe_session(s))
            item.setData(Qt.ItemDataRole.UserRole, s.id)
            self.sessions.addItem(item)
            if keep is not None and s.id == keep.id:
                self.sessions.setCurrentItem(item)
        if self.sessions.count() and self.sessions.currentRow() < 0:
            self.sessions.setCurrentRow(0)
        self.hint.setText(
            "No sessions yet - create one to start"
            if self.sessions.count() == 0
            else "Previous sessions"
        )
        self._update_buttons()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        if self.pages.currentIndex() == 0:
            self.refresh()
        super().showEvent(event)
