from collections.abc import Callable
from datetime import datetime
from functools import partial

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QStackedWidget,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.app.interview_session import SessionDetails
from interview_assistant.app.post_call import PostCallController
from interview_assistant.app.resume_library import ResumeLibrary
from interview_assistant.history.store import HistoryStore, InterviewSession
from interview_assistant.ui.post_call_page import PostCallPage
from interview_assistant.ui.resume_page import ResumePage
from interview_assistant.ui.session_dialog import SessionDialog
from interview_assistant.ui.theme import STYLE, WindowBar, center_window, paint_glass


class SessionCardDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index) -> None:  # noqa: N802
        super().initStyleOption(option, index)
        # Keep item text for selection/accessibility; the card renders its visual content.
        option.text = ""


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
        use_builtin_prompt=s.use_builtin_prompt,
        answer_format=s.answer_format,
        answer_length=s.answer_length,
        answer_tone=s.answer_tone,
        model_route_override=s.model_route_override,
        coding_model_route=s.coding_model_route,
        fast_model_route=s.fast_model_route,
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
        model_choices: list[str] | None = None,
        on_settings: Callable[[], None] | None = None,
        on_privacy: Callable[[], None] | None = None,
        on_view_session: Callable[[int], None] | None = None,
        on_resume: Callable[[int], InterviewSession] | None = None,
        review_controller: PostCallController | None = None,
        on_audio: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._store = store
        self._library = library
        self._model_choices = model_choices or []
        self._activate = activate
        self._on_resume = on_resume
        self._on_connect = on_connect
        self._on_view_session = on_view_session
        self._on_dashboard = on_dashboard
        self._on_audio = on_audio
        self._filter = "All"
        self._connecting: InterviewSession | None = None
        self._workspace_pages: dict[str, QWidget] = {}
        self._workspace_widgets: dict[str, QWidget] = {}
        self.extra_nav: dict[str, QPushButton] = {}
        self.setObjectName("start")
        self.setWindowTitle("Interview Assistant")
        self.setStyleSheet(STYLE)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        center_window(self)

        self.pages = QStackedWidget()
        # Hidden review/resume pages must not force the startup window to their size hints.
        self.pages.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.pages.addWidget(self._list_page())
        self.pages.addWidget(self._connect_page())
        self.create_page = SessionDialog(library, self._model_choices)
        self.create_page.created.connect(self._session_created)
        self.create_page.cancelled.connect(self._show_list)
        self.pages.addWidget(self.create_page)
        self.review_page = PostCallPage(store, review_controller)
        self.review_page.back_requested.connect(self._show_list)
        self.pages.addWidget(self.review_page)
        self.resume_page = ResumePage(library) if library is not None else None
        if self.resume_page is not None:
            self.pages.addWidget(self.resume_page)
            self.resume_page.changed.connect(self.create_page.refresh_resumes)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 6)
        layout.setSpacing(0)
        self.window_bar = WindowBar(self)
        layout.addWidget(self.window_bar)
        self.notice = QLabel()
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.setWordWrap(True)
        self.notice.setStyleSheet("color: #ffd29b; padding: 4px 16px;")
        self.notice.hide()
        layout.addWidget(self.notice)
        content = QHBoxLayout()
        content.setSpacing(0)
        content.addWidget(self._sidebar(on_settings, on_privacy))
        content.addWidget(self.pages, 1)
        layout.addLayout(content, 1)
        grip = QSizeGrip(self)
        layout.addWidget(grip, 0, Qt.AlignmentFlag.AlignRight)
        self.refresh()

    def _sidebar(
        self, on_settings: Callable[[], None] | None, on_privacy: Callable[[], None] | None
    ) -> QWidget:
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(176)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 18, 14, 16)
        brand = QLabel("Chandoo\nAssistant")
        brand.setObjectName("brand")
        layout.addWidget(brand)
        section = QLabel("WORKSPACE")
        section.setObjectName("section")
        layout.addWidget(section)
        self.sessions_btn = QPushButton("Call Sessions")
        self.sessions_btn.setObjectName("nav")
        self.sessions_btn.setCheckable(True)
        self.sessions_btn.setChecked(True)
        self.sessions_btn.clicked.connect(self._show_list)
        layout.addWidget(self.sessions_btn)
        self.resumes_btn = QPushButton("Resumes")
        self.resumes_btn.setObjectName("nav")
        self.resumes_btn.setCheckable(True)
        self.resumes_btn.setEnabled(self.resume_page is not None)
        self.resumes_btn.clicked.connect(self._show_resumes)
        layout.addWidget(self.resumes_btn)
        practice_btn = QPushButton("Fictional / Realistic")
        practice_btn.setObjectName("nav")
        practice_btn.setCheckable(True)
        practice_btn.setEnabled(False)
        practice_btn.setToolTip("Fictional / Realistic Q&A — practice answer bank")
        self.extra_nav["practice"] = practice_btn
        layout.addWidget(practice_btn)
        self.dashboard_btn = QPushButton("History && transcripts")
        self.dashboard_btn.setObjectName("nav")
        self.dashboard_btn.setCheckable(True)
        self.dashboard_btn.clicked.connect(self._on_dashboard)
        layout.addWidget(self.dashboard_btn)
        layout.addStretch(1)
        for key, label, callback in (
            ("controls", "Audio && transcript", self._on_audio),
            ("settings", "Settings", on_settings),
            ("privacy", "Privacy", on_privacy),
        ):
            if callback is not None:
                button = QPushButton(label)
                button.setObjectName("nav")
                button.setCheckable(True)
                self.extra_nav[key] = button
                button.clicked.connect(callback)
                layout.addWidget(button)
        local = QLabel("Your workspace\nSaved on this device")
        local.setObjectName("muted")
        layout.addWidget(local)
        return sidebar

    def add_workspace_page(self, key: str, title: str, widget: QWidget) -> None:
        """Host an existing screen in the shell, with scrolling for smaller laptops."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(24, 20, 24, 12)
        heading = QHBoxLayout()
        label = QLabel(title)
        label.setObjectName("title")
        heading.addWidget(label, 1)
        back = QPushButton("Back to sessions")
        back.clicked.connect(self._show_list)
        heading.addWidget(back)
        layout.addLayout(heading)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        widget.setParent(scroll)
        widget.setWindowFlags(Qt.WindowType.Widget)
        scroll.setWidget(widget)
        layout.addWidget(scroll, 1)
        self.pages.addWidget(page)
        self._workspace_pages[key] = page
        self._workspace_widgets[key] = widget
        if isinstance(widget, QDialog):
            widget.finished.connect(lambda _result: self._workspace_finished(key))

    def _workspace_finished(self, key: str) -> None:
        if self.pages.currentWidget() is self._workspace_pages[key]:
            self._show_list()

    def _select_nav(self, key: str) -> None:
        self.sessions_btn.setChecked(key == "sessions")
        self.resumes_btn.setChecked(key == "resumes")
        self.dashboard_btn.setChecked(key == "history")
        for name, button in self.extra_nav.items():
            button.setChecked(key == name)

    def open_workspace_page(self, key: str) -> None:
        self._select_nav(key)
        self.pages.setCurrentWidget(self._workspace_pages[key])
        self._workspace_widgets[key].show()
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    # ---- page 1: sessions ----
    def _list_page(self) -> QWidget:
        page = QWidget()
        self.title = QLabel("Call Sessions")
        self.title.setObjectName("title")
        self.hint = QLabel("Prepare for calls and review past sessions.")
        self.hint.setObjectName("hint")
        self.sessions = QTableWidget(0, 7)
        self.sessions.setItemDelegateForColumn(0, SessionCardDelegate(self.sessions))
        self.sessions.setHorizontalHeaderLabels(
            ["Session", "Mode", "Duration", "Created", "Transcript", "State", ""]
        )
        self.sessions.verticalHeader().hide()
        self.sessions.setShowGrid(False)
        self.sessions.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.sessions.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.sessions.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.sessions.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        for col, width in enumerate((82, 88, 114, 88, 140, 170), start=1):
            self.sessions.setColumnWidth(col, width)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.hide()
        for column in range(1, 6):
            self.sessions.hideColumn(column)
        self.sessions.setColumnWidth(6, 154)
        self.sessions.itemSelectionChanged.connect(self._update_buttons)
        self.start_btn = QPushButton("Start Session")
        self.start_btn.clicked.connect(self._start_selected)
        self.create_btn = QPushButton("+ Create Session")
        self.create_btn.setObjectName("create")
        self.create_btn.clicked.connect(self._open_create)

        top = QHBoxLayout()
        heading = QVBoxLayout()
        heading.addWidget(self.title)
        heading.addWidget(self.hint)
        top.addLayout(heading, 1)
        top.addWidget(self.create_btn)
        filters = QHBoxLayout()
        self.filter_group = QButtonGroup(self)
        self.filter_buttons: dict[str, QPushButton] = {}
        for name in ("All", "Active", "Ended"):
            button = QPushButton(name)
            button.setObjectName("filter")
            button.setCheckable(True)
            button.setChecked(name == "All")
            button.clicked.connect(partial(self._set_filter, name))
            self.filter_group.addButton(button)
            self.filter_buttons[name] = button
            filters.addWidget(button)
        self.filter_buttons["Active"].setToolTip("Sessions that have not ended")
        filters.addStretch(1)
        self.count_label = QLabel()
        self.count_label.setObjectName("muted")
        filters.addWidget(self.count_label)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search by company, role or description")
        self.search.setClearButtonEnabled(True)
        self.search.setMaximumWidth(550)
        self.search.textChanged.connect(self.refresh)
        self.empty_label = QLabel("No sessions yet\nCreate your first session to get started.")
        self.empty_label.setObjectName("muted")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(self.start_btn)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(26, 28, 26, 20)
        layout.setSpacing(18)
        layout.addLayout(top)
        layout.addLayout(filters)
        layout.addWidget(self.search)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.sessions, 1)
        layout.addLayout(bottom)
        return page

    def _set_filter(self, name: str) -> None:
        self._filter = name
        self.refresh()

    # ---- page 2: connect ----
    def _connect_page(self) -> QWidget:
        page = QWidget()
        self.connect_title = QLabel("Connect Call Session")
        self.connect_title.setObjectName("title")
        self.connect_summary = QLabel("")
        self.connect_summary.setTextFormat(Qt.TextFormat.PlainText)
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
        layout.setContentsMargins(40, 40, 40, 30)
        layout.addWidget(self.connect_title)
        layout.addWidget(self.connect_summary)
        layout.addWidget(self.connect_notice)
        layout.addStretch(1)
        layout.addLayout(bottom)
        return page

    def _show_connect(self, session: InterviewSession) -> None:
        self._connecting = session
        coding = session.coding_model_route or session.model_route_override or "Auto"
        fast = session.fast_model_route or session.model_route_override or "Auto"
        self.connect_summary.setText(
            describe_session(session)
            + f"\n\nCoding model: {coding}\nFast model: {fast}"
            + f"\nAnswer language: {session.language}"
            + f"\nBuilt-in prompt: {'On' if session.use_builtin_prompt else 'Off'}"
            + "\nAnswering: "
            + ("Automatic" if session.auto_generate else "Press Answer for each question")
            + f"\nSave transcript: {'Yes' if session.save_transcript else 'No'}"
        )
        self.pages.setCurrentIndex(1)

    def _show_list(self) -> None:
        self._select_nav("sessions")
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
        self._select_nav("sessions")
        self.create_page.refresh_resumes()
        self.pages.setCurrentWidget(self.create_page)

    def _show_resumes(self) -> None:
        if self.resume_page is not None:
            self.resume_page.refresh()
            self._select_nav("resumes")
            self.pages.setCurrentWidget(self.resume_page)

    def show_review(self, session_id: int) -> None:
        self.review_page.load(session_id)
        self._select_nav("sessions")
        self.pages.setCurrentWidget(self.review_page)

    def _session_created(self, details: SessionDetails) -> None:
        self._show_connect(self._activate(details))

    def _selected(self) -> InterviewSession | None:
        item = self.sessions.item(self.sessions.currentRow(), 0)
        if item is None:
            return None
        return self._store.get_interview_session(item.data(Qt.ItemDataRole.UserRole))

    def _start_selected(self) -> None:
        saved = self._selected()
        if saved is not None:
            self._start_session(saved.id)

    def _start_session(self, session_id: int) -> None:
        saved = self._store.get_interview_session(session_id)
        if saved is None or saved.ended_at is not None:
            return
        self._show_connect(
            self._on_resume(saved.id)
            if self._on_resume is not None
            else self._activate(details_of(saved, self._library))
        )

    def _update_buttons(self) -> None:
        saved = self._selected()
        self.start_btn.setEnabled(saved is not None and saved.ended_at is None)

    def refresh(self) -> None:
        keep = self._selected()
        all_sessions = self._store.list_interview_sessions()
        query = self.search.text().strip().casefold()
        visible = [
            s
            for s in all_sessions
            if (self._filter == "All" or (s.ended_at is None) == (self._filter == "Active"))
            and query in f"{s.company} {s.role_title} {s.job_description}".casefold()
        ]
        self.sessions.setRowCount(0)
        for row, s in enumerate(visible):
            self.sessions.insertRow(row)
            self.sessions.setRowHeight(row, 126)
            duration = "—"
            if s.duration_s is not None:
                minutes, seconds = divmod(max(0, int(s.duration_s)), 60)
                duration = f"{minutes}m {seconds:02d}s"
            for col, text in enumerate(
                (
                    f"{s.company or 'Untitled session'}\n{s.role_title or 'General conversation'}",
                    s.session_type,
                    duration,
                    datetime.fromtimestamp(s.created_at).strftime("%b %d, %Y"),
                    "Yes" if s.save_transcript else "No",
                )
            ):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, s.id)
                self.sessions.setItem(row, col, item)
            card = QWidget()
            card.setObjectName("sessionCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 10, 12)
            card_layout.setSpacing(5)
            for text, name in (
                (s.company or "Untitled session", "cardTitle"),
                (s.role_title or "General conversation", "hint"),
                (
                    datetime.fromtimestamp(s.created_at).strftime("%b %d, %Y")
                    + "  ·  "
                    + ("Ready to start" if s.ended_at is None else f"Ended · {duration}"),
                    "muted",
                ),
            ):
                label = QLabel(text)
                label.setTextFormat(Qt.TextFormat.PlainText)
                label.setObjectName(name)
                label.setWordWrap(True)
                label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                card_layout.addWidget(label)
            self.sessions.setCellWidget(row, 0, card)
            badge = QLabel("Ready to start" if s.ended_at is None else "Ended")
            badge.setObjectName("readyBadge" if s.ended_at is None else "endedBadge")
            self.sessions.setCellWidget(row, 5, self._cell(badge))
            action = QPushButton("Start Session" if s.ended_at is None else "View Session")
            if s.ended_at is None:
                action.setObjectName("rowStart")
                action.clicked.connect(partial(self._start_session, s.id))
            else:
                action.clicked.connect(partial(self._view_session, s.id))
            self.sessions.setCellWidget(row, 6, self._cell(action))
        selected = next((i for i, s in enumerate(visible) if keep and s.id == keep.id), 0)
        if visible:
            self.sessions.selectRow(selected)
        self.count_label.setText(f"{len(visible)} of {len(all_sessions)} sessions")
        self.empty_label.setVisible(not visible)
        self.empty_label.setText(
            "No sessions match your search or filter."
            if all_sessions
            else "No sessions yet\nCreate your first session to get started."
        )
        self._update_buttons()

    @staticmethod
    def _cell(widget: QWidget) -> QWidget:
        cell = QWidget()
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.addWidget(widget)
        layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        return cell

    def _view_session(self, session_id: int) -> None:
        if self._on_view_session is not None:
            self._on_view_session(session_id)
        else:
            self._on_dashboard()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        paint_glass(painter, QRectF(self.rect()))

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        # Wrapped text belongs to scrollable pages, not the native window's minimum height.
        return False

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return -1

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        if self.pages.currentIndex() == 0:
            self.refresh()
        super().showEvent(event)
