import time
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.app.interview_session import InterviewSessionManager, SessionDetails
from interview_assistant.history.store import (
    ExportFormat,
    HistoryStore,
    SearchHit,
    SessionInfo,
    StoredTurn,
)
from interview_assistant.ui.session_dialog import SessionDialog

Confirm = Callable[[str], bool]
PickPath = Callable[[str], str | None]
DialogFactory = Callable[[], SessionDialog]


def _stamp(ts: float) -> str:
    return time.strftime("%b %d %H:%M", time.localtime(ts))


def _duration(seconds: float) -> str:
    total_min = max(0, int(seconds // 60))
    h, m = divmod(total_min, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def _default_confirm(text: str) -> bool:
    box = QMessageBox.question(
        None, "Confirm delete", text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    )
    return box == QMessageBox.StandardButton.Yes


def _default_pick_path(default_name: str) -> str | None:
    path, _ = QFileDialog.getSaveFileName(
        None, "Export", default_name, "Markdown (*.md);;JSON (*.json)"
    )
    return path or None


class HistoryWindow(QWidget):
    """Browse, search, export and delete saved interview history. Everything stays on this PC."""

    def __init__(
        self,
        store: HistoryStore,
        confirm: Confirm = _default_confirm,
        pick_path: PickPath = _default_pick_path,
        session_manager: InterviewSessionManager | None = None,
        session_dialog_factory: DialogFactory = SessionDialog,
    ) -> None:
        super().__init__()
        self._store = store
        self._confirm = confirm
        self._pick_path = pick_path
        self._session_manager = session_manager
        self._session_dialog_factory = session_dialog_factory
        self._session_dialog: SessionDialog | None = None  # kept alive while open (non-modal)
        self.setWindowTitle("Interview history")
        self.resize(900, 560)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search questions and answers…")
        self.search.setClearButtonEnabled(True)
        self.save_box = QCheckBox("Save history")
        self.save_box.setChecked(store.enabled)
        self.sessions = QListWidget()
        self.create_session_btn = QPushButton("+ Create Session")
        self.create_session_btn.setEnabled(session_manager is not None)
        self.turns = QListWidget()
        self.detail = QTextBrowser()
        self.status = QLabel("")
        self.view_transcript_btn = QPushButton("View Transcript")
        self.export_btn = QPushButton("Export session…")
        self.export_all_btn = QPushButton("Export all…")
        self.del_turn_btn = QPushButton("Delete question")
        self.del_session_btn = QPushButton("Delete session")
        self.del_all_btn = QPushButton("Delete all…")

        top = QHBoxLayout()
        top.addWidget(self.search, 1)
        top.addWidget(self.save_box)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(self.sessions, 1)
        left_layout.addWidget(self.create_session_btn)
        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.turns)
        right.addWidget(self.detail)
        right.setSizes([160, 340])
        main = QSplitter()
        main.addWidget(left)
        main.addWidget(right)
        main.setSizes([280, 620])
        buttons = QHBoxLayout()
        for b in (
            self.view_transcript_btn,
            self.export_btn,
            self.export_all_btn,
            self.del_turn_btn,
            self.del_session_btn,
            self.del_all_btn,
        ):
            buttons.addWidget(b)
        buttons.addStretch(1)
        buttons.addWidget(self.status)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(main, 1)
        layout.addLayout(buttons)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(250)
        self._debounce.timeout.connect(self._run_search)
        self.search.textChanged.connect(lambda _t: self._debounce.start())
        self.save_box.toggled.connect(self._toggle_saving)
        self.sessions.currentItemChanged.connect(lambda *_: self._load_turns())
        self.turns.currentItemChanged.connect(lambda *_: self._show_turn())
        self.create_session_btn.clicked.connect(self.open_create_session)
        self.view_transcript_btn.clicked.connect(self.view_transcript)
        self.export_btn.clicked.connect(self.export_session)
        self.export_all_btn.clicked.connect(self.export_all)
        self.del_turn_btn.clicked.connect(self.delete_turn)
        self.del_session_btn.clicked.connect(self.delete_session)
        self.del_all_btn.clicked.connect(self.delete_all)
        self.refresh()

    # ---- data loading ----
    def refresh(self) -> None:
        keep = self._current_session_id()
        self.sessions.blockSignals(True)
        self.sessions.clear()
        for s in self._store.list_sessions():
            header = f"{_stamp(s.started_at)} · {s.turns} Q · ${s.cost_usd:.4f}"
            if s.duration_s is not None:
                header += f" · {_duration(s.duration_s)}"
            title = s.title
            if s.company:  # linked to an InterviewSession (Step 17) - show company/role instead
                title = f"{s.company} — {s.role_title}" if s.role_title else s.company
            item = QListWidgetItem(f"{header}\n{title}")
            item.setData(Qt.ItemDataRole.UserRole, s)
            self.sessions.addItem(item)
            if s.id == keep:
                self.sessions.setCurrentItem(item)
        self.sessions.blockSignals(False)
        if self.sessions.currentItem() is None and self.sessions.count():
            self.sessions.setCurrentRow(0)
        self._load_turns()
        self._update_buttons()

    def _current_session_id(self) -> int | None:
        item = self.sessions.currentItem()
        return item.data(Qt.ItemDataRole.UserRole).id if item else None

    def select_interview_session(self, interview_session_id: int) -> None:
        """Open the newest saved transcript linked to a dashboard interview session."""
        self.search.clear()
        self._debounce.stop()
        for row in range(self.sessions.count()):
            item = self.sessions.item(row)
            if item.data(Qt.ItemDataRole.UserRole).interview_session_id == interview_session_id:
                self.sessions.setCurrentRow(row)
                self._load_turns()
                self.view_transcript()
                self.status.clear()
                return
        self.sessions.setCurrentRow(-1)
        self._load_turns()
        self.detail.setPlainText("No transcript was saved for this session.")
        self.status.setText("No saved transcript")

    def _load_turns(self) -> None:
        if self.search.text().strip():
            return  # search results own the turn list while a query is active
        self.turns.blockSignals(True)
        self.turns.clear()
        sid = self._current_session_id()
        if sid is not None:
            for t in self._store.get_turns(sid):
                self._add_turn_item(t)
        self.turns.blockSignals(False)
        if self.turns.count():
            self.turns.setCurrentRow(0)
        self._show_turn()
        self._update_buttons()

    def _add_turn_item(self, t: StoredTurn, snippet: str = "") -> None:
        flag = {"complete": "", "interrupted": " ⏹", "stopped": " ⏹", "error": " ⚠"}.get(
            t.status, ""
        )
        label = f"{time.strftime('%H:%M', time.localtime(t.ts))}  {t.question}{flag}"
        if snippet:
            label += f"\n   …{snippet}"
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, t)
        self.turns.addItem(item)

    def _run_search(self) -> None:
        text = self.search.text().strip()
        if not text:
            self._load_turns()
            return
        hits: list[SearchHit] = self._store.search(text)
        self.turns.blockSignals(True)
        self.turns.clear()
        for h in hits:
            self._add_turn_item(h.turn, h.snippet.replace("\n", " "))
        self.turns.blockSignals(False)
        self.status.setText(f"{len(hits)} match(es)")
        if self.turns.count():
            self.turns.setCurrentRow(0)
        self._show_turn()
        self._update_buttons()

    def _current_turn(self) -> StoredTurn | None:
        item = self.turns.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _show_turn(self) -> None:
        t = self._current_turn()
        if t is None:
            self.detail.clear()
            self._update_buttons()
            return
        meta = " · ".join(
            x
            for x in (
                t.category,
                f"corrected from {t.corrected_from}" if t.corrected_from else "",
                t.route,
                f"{t.first_token_s:.1f}s first token" if t.first_token_s else "",
                f"${t.cost_usd:.5f}" if t.cost_usd is not None else "",
                t.status if t.status != "complete" else "",
            )
            if x
        )
        body = f"### {t.question}\n\n*{meta}*\n\n{t.answer or '_(no answer)_'}"
        if t.error:
            body += f"\n\n> ⚠ {t.error}"
        self.detail.setMarkdown(body)
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.del_turn_btn.setEnabled(self._current_turn() is not None)
        has_session = self._current_session_id() is not None
        self.view_transcript_btn.setEnabled(has_session)
        self.export_btn.setEnabled(has_session)
        self.del_session_btn.setEnabled(has_session)
        self.export_all_btn.setEnabled(self.sessions.count() > 0)
        self.del_all_btn.setEnabled(self.sessions.count() > 0)

    # ---- actions ----
    def _toggle_saving(self, on: bool) -> None:
        self._store.enabled = on
        self.status.setText("History is being saved" if on else "History saving is OFF")

    def open_create_session(self) -> None:
        if self._session_manager is None:
            return
        dialog = self._session_dialog_factory()
        dialog.created.connect(self._activate_session)
        dialog.show()
        self._session_dialog = dialog  # non-modal: keep a reference so it isn't garbage-collected

    def _activate_session(self, details: SessionDetails) -> None:
        if self._session_manager is None:
            return
        session = self._session_manager.activate(details)
        self.status.setText(f"Interview session created: {session.company or 'Untitled'}")

    def view_transcript(self) -> None:
        """The whole session's Q&A, rendered inline (reuses export_session's own Markdown
        rendering) rather than only as a file-export action."""
        sid = self._current_session_id()
        if sid is not None:
            self.detail.setMarkdown(self._store.export_session(sid, "md"))

    def export_session(self) -> None:
        sid = self._current_session_id()
        if sid is not None:
            self._export(lambda fmt: self._store.export_session(sid, fmt), f"interview-{sid}")

    def export_all(self) -> None:
        self._export(self._store.export_all, "interview-history")

    def _export(self, render: Callable[[ExportFormat], str], base: str) -> None:
        path = self._pick_path(f"{base}.md")
        if not path:
            return
        fmt: ExportFormat = "json" if path.lower().endswith(".json") else "md"
        try:
            Path(path).write_text(render(fmt), encoding="utf-8")
            self.status.setText(f"Exported to {path}")
        except OSError as e:
            self.status.setText(f"Export failed: {e}")

    def delete_turn(self) -> None:
        t = self._current_turn()
        if t is not None and self._confirm(f"Delete this question and its answer?\n\n{t.question}"):
            self._store.delete_turn(t.id)
            self._after_delete()

    def delete_session(self) -> None:
        item = self.sessions.currentItem()
        if item is None:
            return
        s: SessionInfo = item.data(Qt.ItemDataRole.UserRole)
        if self._confirm(f"Delete this whole session ({s.turns} questions)?"):
            self._store.delete_session(s.id)
            self._after_delete()

    def delete_all(self) -> None:
        sessions, turns = self._store.counts()
        if self._confirm(
            f"Permanently delete ALL history ({sessions} sessions, {turns} questions)? "
            "This cannot be undone."
        ):
            self._store.delete_all()
            self._after_delete()

    def _after_delete(self) -> None:
        self.status.setText("Deleted")
        self.refresh()
        if self.search.text().strip():
            self._run_search()
