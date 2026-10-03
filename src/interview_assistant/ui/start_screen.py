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
    QVBoxLayout,
    QWidget,
)

from interview_assistant.history.store import HistoryStore, InterviewSession

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
#create { background: #2f9e5b; color: #ffffff; font-weight: 600; }
#create:hover { background: #38b56a; }
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


class StartScreen(QWidget):
    """The first screen: previous interview sessions, plus Create Session and the dashboard."""

    def __init__(
        self,
        store: HistoryStore,
        on_create: Callable[[], None],
        on_dashboard: Callable[[], None],
    ) -> None:
        super().__init__()
        self._store = store
        self.setObjectName("start")
        self.setWindowTitle("Interview Assistant")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setStyleSheet(STYLE)
        self.resize(560, 520)

        title = QLabel("Interview Assistant")
        title.setObjectName("title")
        self.hint = QLabel("Previous sessions")
        self.hint.setObjectName("hint")
        self.sessions = QListWidget()
        self.dashboard_btn = QPushButton("View in Dashboard")
        self.dashboard_btn.clicked.connect(on_dashboard)
        self.create_btn = QPushButton("+ Create Session")
        self.create_btn.setObjectName("create")
        self.create_btn.clicked.connect(on_create)

        bottom = QHBoxLayout()
        bottom.addWidget(self.dashboard_btn)
        bottom.addStretch(1)
        bottom.addWidget(self.create_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.addWidget(title)
        layout.addWidget(self.hint)
        layout.addWidget(self.sessions, 1)
        layout.addLayout(bottom)
        self.refresh()

    def refresh(self) -> None:
        self.sessions.clear()
        for s in self._store.list_interview_sessions():
            item = QListWidgetItem(describe_session(s))
            item.setData(Qt.ItemDataRole.UserRole, s.id)
            self.sessions.addItem(item)
        self.hint.setText(
            "No sessions yet - create one to start"
            if self.sessions.count() == 0
            else "Previous sessions"
        )

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        self.refresh()
        super().showEvent(event)
