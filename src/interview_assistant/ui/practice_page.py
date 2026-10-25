"""Question-first practice library with lightweight in-window transitions."""

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, Signal
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.app.practice_library import PracticeLibrary
from interview_assistant.ui.practice_import import PracticeImport


class PracticePage(QWidget):
    privacy_requested = Signal()

    def __init__(self, library: PracticeLibrary) -> None:
        super().__init__()
        self.library = library
        self._editing: str | None = None
        self._original = ("", "")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)
        note = QLabel("Fictional practice • Saved on this device")
        note.setObjectName("muted")
        note.setToolTip(
            "Matching questions can display these answers labelled Saved fictional Q&A. "
            "Fictional examples are not verified personal facts."
        )
        layout.addWidget(note)
        self.notice = QLabel("Incognito is on. Open Privacy to enable saving and deletion.")
        self.notice.setWordWrap(True)
        self.privacy_btn = QPushButton("Open Privacy")
        self.privacy_btn.clicked.connect(self.privacy_requested.emit)
        privacy_row = QHBoxLayout()
        privacy_row.addWidget(self.notice, 1)
        privacy_row.addWidget(self.privacy_btn)
        layout.addLayout(privacy_row)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.list_view = QWidget()
        listing = QVBoxLayout(self.list_view)
        listing.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search questions")
        self.search.setAccessibleName("Search practice questions")
        self.search.textChanged.connect(self.refresh)
        self.new_btn = QPushButton("+ New question")
        self.new_btn.setObjectName("create")
        self.new_btn.clicked.connect(self._new)
        toolbar.addWidget(self.search, 1)
        self.json_btn = QPushButton("Import JSON")
        self.json_btn.clicked.connect(self._open_import)
        toolbar.addWidget(self.json_btn)
        toolbar.addWidget(self.new_btn)
        listing.addLayout(toolbar)
        self.entries = QListWidget()
        self.entries.setAccessibleName("Saved practice questions; click or press Enter to open")
        self.entries.setWordWrap(True)
        self.entries.setStyleSheet(
            "QListWidget { border: none; background: transparent; }"
            "QListWidget::item { padding: 16px; margin: 4px 0; border-radius: 10px; "
            "background: rgba(125, 210, 220, 12); }"
            "QListWidget::item:hover { background: rgba(125, 210, 220, 32); }"
            "QListWidget::item:selected { background: rgba(125, 210, 220, 45); }"
        )
        self.entries.itemClicked.connect(self._select)
        self.entries.itemActivated.connect(self._select)
        listing.addWidget(self.entries, 1)
        self.empty = QLabel("No questions yet. Add your first question and answer.")
        self.empty.setWordWrap(True)
        listing.addWidget(self.empty)
        self.examples_btn = QPushButton("Add fictional examples")
        self.examples_btn.clicked.connect(self._examples)
        listing.addWidget(self.examples_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.stack.addWidget(self.list_view)

        self.detail_view = QWidget()
        detail = QVBoxLayout(self.detail_view)
        detail.setContentsMargins(0, 0, 0, 0)
        self.back_btn = QPushButton("← All questions")
        self.back_btn.clicked.connect(self._back)
        detail.addWidget(self.back_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.detail_question = QLabel()
        self.detail_question.setTextFormat(Qt.TextFormat.PlainText)
        self.detail_question.setWordWrap(True)
        self.detail_question.setStyleSheet("font-size: 20px; font-weight: 600;")
        detail.addWidget(self.detail_question)
        self.detail_answer = QTextEdit()
        self.detail_answer.setReadOnly(True)
        self.detail_answer.setAccessibleName("Saved practice answer")
        detail.addWidget(self.detail_answer, 1)
        actions = QHBoxLayout()
        self.edit_btn = QPushButton("Edit answer")
        self.delete_btn = QPushButton("Delete")
        self.edit_btn.clicked.connect(self._edit)
        self.delete_btn.clicked.connect(self._delete)
        actions.addWidget(self.edit_btn)
        actions.addStretch()
        actions.addWidget(self.delete_btn)
        detail.addLayout(actions)
        self.stack.addWidget(self.detail_view)

        self.editor_view = QWidget()
        editor = QVBoxLayout(self.editor_view)
        editor.setContentsMargins(0, 0, 0, 0)
        self.editor_title = QLabel("New question")
        self.editor_title.setStyleSheet("font-size: 20px; font-weight: 600;")
        editor.addWidget(self.editor_title)
        editor.addWidget(QLabel("Question *"))
        self.question = QLineEdit()
        self.question.setMaxLength(1000)
        self.question.setAccessibleName("Practice question (required)")
        editor.addWidget(self.question)
        editor.addWidget(QLabel("Answer *"))
        self.answer = QTextEdit()
        self.answer.setAcceptRichText(False)
        self.answer.setAccessibleName("Practice answer (required)")
        self.answer.setPlaceholderText("An answer is required before you can save.")
        editor.addWidget(self.answer, 1)
        row = QHBoxLayout()
        self.cancel_btn = QPushButton("Cancel")
        self.save_btn = QPushButton("Save Q&&A")
        self.save_btn.setObjectName("create")
        self.cancel_btn.clicked.connect(self._cancel)
        self.save_btn.clicked.connect(self._save)
        row.addWidget(self.cancel_btn)
        row.addStretch()
        row.addWidget(self.save_btn)
        editor.addLayout(row)
        self.stack.addWidget(self.editor_view)
        self.json_view = PracticeImport(library)
        self.json_view.cancelled.connect(self._back)
        self.json_view.imported.connect(self._imported)
        self.stack.addWidget(self.json_view)

        self.message = QLabel()
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self._opacity = QGraphicsOpacityEffect(self.stack)
        self.stack.setGraphicsEffect(self._opacity)
        self._opacity.setOpacity(1)
        self._transition = QPropertyAnimation(self._opacity, b"opacity", self)
        self._transition.setDuration(150)
        self._transition.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.refresh()
        self.update_privacy()

    def _show(self, view: QWidget) -> None:
        self._transition.stop()
        self.stack.setCurrentWidget(view)
        self.message.clear()
        self._transition.setStartValue(0.35)
        self._transition.setEndValue(1.0)
        self._transition.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._transition.stop()
        self._opacity.setOpacity(1)
        super().hideEvent(event)

    def update_privacy(self) -> None:
        writable = self.library.writable
        self.notice.setVisible(not writable)
        self.privacy_btn.setVisible(not writable)
        for button in (
            self.new_btn,
            self.save_btn,
            self.examples_btn,
            self.edit_btn,
        ):
            button.setEnabled(writable)
        self.json_view.update_privacy()
        self.delete_btn.setEnabled(writable and self._editing is not None)

    def refresh(self, *_args: object) -> None:
        try:
            entries = self.library.entries()
        except (OSError, ValueError) as e:
            self.message.setText(str(e))
            return
        query = self.search.text().casefold().split()
        self.entries.clear()
        for entry in entries:
            if not all(word in entry.question.casefold() for word in query):
                continue
            item = QListWidgetItem(entry.question)
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self.entries.addItem(item)
        self.empty.setVisible(self.entries.count() == 0)
        self.empty.setText(
            "No matching questions."
            if query
            else "No questions yet. Add your first question and answer."
        )
        self.message.setText(f"{self.entries.count()} saved questions")

    def _select(self, *_args: object) -> None:
        item = self.entries.currentItem()
        if item is None:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        self._editing = entry.id
        self.detail_question.setText(entry.question)
        self.detail_answer.setPlainText(entry.answer)
        self.update_privacy()
        self._show(self.detail_view)
        self.back_btn.setFocus()

    def _back(self) -> None:
        self._editing = None
        self._show(self.list_view)
        self.refresh()
        self.update_privacy()
        self.search.setFocus()

    def _new(self) -> None:
        self._editing = None
        self.question.clear()
        self.answer.clear()
        self._original = ("", "")
        self.editor_title.setText("New question")
        self._show(self.editor_view)
        self.question.setFocus()

    def _open_import(self) -> None:
        self._show(self.json_view)
        self.json_view._invalidate()
        self.json_view.text.setFocus()

    def _imported(self, added: int, skipped: int) -> None:
        self.search.clear()
        self._back()
        self.message.setText(f"Imported {added} questions; skipped {skipped} existing questions.")

    def _edit(self) -> None:
        self.question.setText(self.detail_question.text())
        self.answer.setPlainText(self.detail_answer.toPlainText())
        self._original = (self.question.text(), self.answer.toPlainText())
        self.editor_title.setText("Edit question and answer")
        self._show(self.editor_view)
        self.answer.setFocus()

    def _cancel(self) -> None:
        if (self.question.text(), self.answer.toPlainText()) != self._original:
            if (
                QMessageBox.question(
                    self,
                    "Discard changes?",
                    "Discard your unsaved question and answer changes?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
        if self._editing is None:
            self._back()
        else:
            self._show(self.detail_view)

    def _save(self) -> None:
        if not self.question.text().strip():
            self.message.setText("Please enter a question before saving.")
            self.question.setFocus()
            return
        if not self.answer.toPlainText().strip():
            self.message.setText("An answer is compulsory. Please enter an answer before saving.")
            self.answer.setFocus()
            return
        try:
            self.library.save(self._editing, self.question.text(), self.answer.toPlainText())
        except (OSError, ValueError) as e:
            self.message.setText(str(e))
            return
        self.search.clear()
        self._back()
        self.message.setText("Q&A saved on this device.")

    def _examples(self) -> None:
        try:
            self.library.add_examples()
        except (OSError, ValueError) as e:
            self.message.setText(str(e))
            return
        self.search.clear()
        self.refresh()

    def _delete(self) -> None:
        if self._editing is None:
            return
        if (
            QMessageBox.question(
                self,
                "Delete saved Q&A",
                "Delete this question and answer?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self.library.delete(self._editing)
        except (OSError, ValueError) as e:
            self.message.setText(str(e))
            return
        self._back()
        self.message.setText("Q&A deleted.")
