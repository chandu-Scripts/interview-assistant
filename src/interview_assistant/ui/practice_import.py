"""Paste/file JSON import, with validation before an atomic batch save."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.app.practice_library import (
    MAX_IMPORT_BYTES,
    PracticeLibrary,
    parse_qa_json,
    question_key,
)


class PracticeImport(QWidget):
    imported = Signal(int, int)
    cancelled = Signal()

    def __init__(self, library: PracticeLibrary) -> None:
        super().__init__()
        self.library = library
        self._validated = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        title = QLabel("Import Q&A from JSON")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(title)
        hint = QLabel(
            "Use questions as keys and answers as text values. Every answer is required. "
            "Existing questions are skipped; saved answers are kept. Up to 1,000 questions / 1 MB."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.file_btn = QPushButton("Choose JSON file")
        self.file_btn.clicked.connect(self._choose_file)
        layout.addWidget(self.file_btn, 0, Qt.AlignmentFlag.AlignLeft)
        self.text = QPlainTextEdit()
        self.text.setAccessibleName("JSON questions and answers")
        self.text.setPlaceholderText(
            '{\n  "How many catalogs did you use?": "Your answer",\n'
            '  "What was your team size?": "Your answer"\n}'
        )
        self.text.textChanged.connect(self._invalidate)
        layout.addWidget(self.text, 1)
        self.status = QLabel("Paste JSON or choose a file, then click Validate.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        self.privacy_notice = QLabel(
            "Incognito is on. You can check JSON here, but to save questions, "
            "open Privacy and turn off Incognito."
        )
        self.privacy_notice.setWordWrap(True)
        self.privacy_notice.setStyleSheet("color: #ffd29b;")
        layout.addWidget(self.privacy_notice)
        row = QHBoxLayout()
        self.cancel_btn = QPushButton("Back to questions")
        self.cancel_btn.clicked.connect(self.cancelled.emit)
        self.validate_btn = QPushButton("Validate")
        self.validate_btn.clicked.connect(self._validate)
        self.import_btn = QPushButton("Import questions")
        self.import_btn.setObjectName("create")
        self.import_btn.setStyleSheet(
            "QPushButton#create:disabled { background: #23374a; color: #8a9cad; "
            "border: 1px solid #354a5f; }"
        )
        self.import_btn.clicked.connect(self._import)
        row.addWidget(self.cancel_btn)
        row.addStretch()
        row.addWidget(self.validate_btn)
        row.addWidget(self.import_btn)
        layout.addLayout(row)
        self.update_privacy()

    def update_privacy(self) -> None:
        self.import_btn.setEnabled(self._validated and self.library.writable)
        self.privacy_notice.setVisible(not self.library.writable)
        self.import_btn.setToolTip(
            "Turn off Incognito in Privacy to save questions."
            if not self.library.writable
            else "Click Validate first to check your JSON."
            if not self._validated
            else "Save the validated questions to your library."
        )

    def _invalidate(self) -> None:
        self._validated = False
        self.update_privacy()
        self.status.setText("Click Validate to check this JSON. Nothing has been saved.")

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import Q&A JSON", "", "JSON files (*.json)")
        if not path:
            return
        try:
            with Path(path).open("rb") as stream:
                raw = stream.read(MAX_IMPORT_BYTES + 1)
            if len(raw) > MAX_IMPORT_BYTES:
                raise ValueError("Choose a file smaller than 1 MB.")
            text = raw.decode("utf-8-sig")
        except (OSError, ValueError) as e:
            self._invalidate()
            self.status.setText(f"Could not load JSON: {e}")
            return
        self.text.setPlainText(text)
        self._validate()

    def _validate(self) -> None:
        self._validated = False
        try:
            pairs = parse_qa_json(self.text.toPlainText())
            existing = {question_key(e.question) for e in self.library.entries()}
            skipped = sum(question_key(q) in existing for q, _ in pairs)
        except (OSError, ValueError) as e:
            self.status.setText(f"{e} Nothing has been saved.")
        else:
            self._validated = True
            self.status.setText(
                f"Ready: {len(pairs) - skipped} new questions; {skipped} existing questions "
                "will be skipped. Click Import questions to save."
            )
        self.update_privacy()

    def _import(self) -> None:
        if not self._validated:
            self._validate()
            return
        try:
            added, skipped = self.library.import_json(self.text.toPlainText())
        except (OSError, ValueError) as e:
            self.status.setText(f"{e} Nothing has been saved.")
            return
        self.text.clear()
        self.imported.emit(added, skipped)
