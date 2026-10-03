"""The "Create Interview Session" wizard, in two steps like the reference app:

1. Details - session type (Interview or Regular), company, role, job description, and a CV/Resume
   dropdown that lists resumes used before, with "Attach new resume" for a new file.
2. Preferences - language, model, answer format/length/tone, AI instructions (typed or attached),
   Auto Generate and Save Transcript.

No "paste a job link" import and no Documents picker (not wanted), and no cross-user Question
Bank (conflicts with AGENTS.md's data-stays-local-by-default stance). The dialog only collects a
SessionDetails; activation is app/interview_session.py's job.
"""

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.app.attachments import ATTACHMENT_FILTER, AttachmentError, read_attachment
from interview_assistant.app.interview_session import SessionDetails
from interview_assistant.app.resume_library import ResumeLibrary
from interview_assistant.llm.router_factory import parse_chain

SESSION_TYPES = ["Interview", "Regular"]
LANGUAGES = ["English", "Hindi", "Telugu", "Tamil", "Spanish", "French", "German"]
ANSWER_FORMATS = ["", "Bullet points", "Short paragraph", "STAR structure"]
ANSWER_LENGTHS = ["", "Short", "Balanced", "Detailed"]
ANSWER_TONES = ["", "Simple", "Formal", "Casual", "Technical"]
NO_RESUME = ""
ATTACH_NEW = "__attach__"


class SessionDialog(QWidget):
    """A page, not a window: the start screen shows it in its own window. Emits `created` with the
    details when the session is created, or `cancelled`; either way it returns to its first page."""

    created = Signal(object)  # SessionDetails
    cancelled = Signal()

    def __init__(self, library: ResumeLibrary | None = None) -> None:
        super().__init__()
        self._library = library
        self._resume_texts: dict[str, str] = {}
        self._chosen_resume = NO_RESUME

        self.pages = QStackedWidget()
        self.pages.addWidget(self._details_page())
        self.pages.addWidget(self._preferences_page())
        self._refresh_resumes()

        self.message = QLabel("")
        self.message.setWordWrap(True)
        self.back_btn = QPushButton("Back")
        self.cancel_btn = QPushButton("Cancel")
        self.next_btn = QPushButton("Next")
        buttons = QHBoxLayout()
        buttons.addWidget(self.message, 1)
        buttons.addWidget(self.back_btn)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.next_btn)

        layout = QVBoxLayout(self)
        layout.addWidget(self.pages, 1)
        layout.addLayout(buttons)

        self.back_btn.clicked.connect(self._back)
        self.next_btn.clicked.connect(self._next_or_create)
        self.cancel_btn.clicked.connect(self._cancel)
        self._update_buttons()

    # ---- details (step 1) ----
    def _details_page(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)

        self.type_interview = QPushButton("Interview")
        self.type_regular = QPushButton("Regular")
        self._type_group = QButtonGroup(self)
        for i, b in enumerate((self.type_interview, self.type_regular)):
            b.setCheckable(True)
            self._type_group.addButton(b, i)
            b.setToolTip("A regular conversation, such as an HR or group discussion round")
        self.type_interview.setChecked(True)
        types = QHBoxLayout()
        types.addWidget(self.type_interview)
        types.addWidget(self.type_regular)
        types.addStretch(1)

        self.company = QLineEdit()
        self.company.setPlaceholderText("Enter company name")
        self.role_title = QLineEdit()
        self.role_title.setPlaceholderText("e.g. Senior Azure Data Engineer")
        self.job_description = QPlainTextEdit()
        self.job_description.setPlaceholderText(
            "Paste the full job description, or just the role, e.g. Senior Azure Data Engineer"
        )
        self.resume_combo = QComboBox()
        self.resume_combo.activated.connect(self._on_resume_chosen)
        self.resume_status = QLabel("")

        form.addRow("Session type", types)
        form.addRow("Company", self.company)
        form.addRow("Role", self.role_title)
        form.addRow("Job description", self.job_description)
        form.addRow("CV / Resume", self.resume_combo)
        form.addRow(self.resume_status)
        return w

    def _refresh_resumes(self, select: str = NO_RESUME) -> None:
        self.resume_combo.blockSignals(True)
        self.resume_combo.clear()
        self.resume_combo.addItem("No resume (use my profile)", NO_RESUME)
        self._resume_texts = {}
        if self._library is not None:
            for resume in self._library.list():
                self._resume_texts[resume.name] = resume.text
                self.resume_combo.addItem(resume.name, resume.name)
        self.resume_combo.addItem("Attach new resume…", ATTACH_NEW)
        index = max(self.resume_combo.findData(select), 0)
        self.resume_combo.setCurrentIndex(index)
        self._chosen_resume = self.resume_combo.currentData()
        self.resume_combo.blockSignals(False)

    def _on_resume_chosen(self, index: int) -> None:
        choice = self.resume_combo.itemData(index)
        if choice != ATTACH_NEW:
            self._chosen_resume = choice
            self.resume_status.setText("")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Attach resume", "", ATTACHMENT_FILTER)
        if not path or self._library is None:
            self._refresh_resumes(select=self._chosen_resume)
            return
        try:
            text = read_attachment(Path(path))
        except AttachmentError as e:
            self.resume_status.setText(str(e))
            self._refresh_resumes(select=self._chosen_resume)
            return
        self._library.add(Path(path).name, text)
        self._refresh_resumes(select=Path(path).name)
        self.resume_status.setText(f"Saved {Path(path).name} for future sessions")

    # ---- preferences (step 2) ----
    def _preferences_page(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.language = QComboBox()
        self.language.setEditable(True)
        self.language.addItems(LANGUAGES)  # English first: the default for most interviews
        self.model_override = QLineEdit()
        self.model_override.setPlaceholderText("auto (e.g. openai:gpt-4o)")
        self.answer_format = QComboBox()
        self.answer_format.setEditable(True)
        self.answer_format.addItems(ANSWER_FORMATS)
        self.answer_length = QComboBox()
        self.answer_length.addItems(ANSWER_LENGTHS)
        self.answer_tone = QComboBox()
        self.answer_tone.setEditable(True)
        self.answer_tone.addItems(ANSWER_TONES)
        self.ai_instructions = QPlainTextEdit()
        self.ai_instructions.setPlaceholderText(
            "Optional - extra notes the AI should use during this session"
        )
        self.prompt_status = QLabel("")
        attach_prompt = QPushButton("Attach prompt…")
        attach_prompt.setToolTip("Load AI instructions from a PDF, DOCX, TXT or MD file")
        attach_prompt.clicked.connect(
            lambda: self._attach_into(self.ai_instructions, self.prompt_status)
        )
        self.auto_generate = QCheckBox("Answer questions automatically (Beta)")
        self.auto_generate.setToolTip("Off: you press Answer or Ctrl+Enter for each question")
        self.save_transcript = QCheckBox("Save this session's transcript")
        self.save_transcript.setChecked(True)
        form.addRow("Language", self.language)
        form.addRow("Model (advanced)", self.model_override)
        form.addRow("Answer format", self.answer_format)
        form.addRow("Answer length", self.answer_length)
        form.addRow("Answer tone", self.answer_tone)
        form.addRow("AI instructions", self.ai_instructions)
        form.addRow(attach_prompt)
        form.addRow(self.prompt_status)
        form.addRow(self.auto_generate)
        form.addRow(self.save_transcript)
        return w

    def _attach_into(self, target: QPlainTextEdit, status: QLabel) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Attach file", "", ATTACHMENT_FILTER)
        if not path:
            return
        try:
            text = read_attachment(Path(path))
        except AttachmentError as e:
            status.setText(str(e))
            return
        target.setPlainText(text)
        status.setText(f"Attached {Path(path).name} ({len(text)} characters)")

    # ---- wizard flow ----
    def _update_buttons(self) -> None:
        on_first = self.pages.currentIndex() == 0
        self.back_btn.setVisible(not on_first)
        self.next_btn.setText("Next" if on_first else "Create Interview Session")

    def _back(self) -> None:
        self.pages.setCurrentIndex(0)
        self.message.setText("")
        self._update_buttons()

    def _next_or_create(self) -> None:
        if self.pages.currentIndex() == 0:
            if not self.company.text().strip():
                self.message.setText("Enter a company name.")
                return
            self.message.setText("")
            self.pages.setCurrentIndex(1)
            self._update_buttons()
            return
        try:
            details = self.collect()
        except ValueError as e:
            self.message.setText(str(e))
            return
        if self._library is not None and details.resume_name:
            self._library.touch(details.resume_name)
        self.created.emit(details)
        self._reset()

    def _cancel(self) -> None:
        self._reset()
        self.cancelled.emit()

    def _reset(self) -> None:
        self.pages.setCurrentIndex(0)
        self.message.setText("")
        self._update_buttons()

    def collect(self) -> SessionDetails:
        """Raises ValueError with a user-readable reason if the model override is invalid."""
        model_override = self.model_override.text().strip()
        if model_override:
            try:
                parse_chain(model_override)
            except ValueError as e:
                raise ValueError(f"Model override: {e}") from e
        resume_name = self._chosen_resume
        return SessionDetails(
            company=self.company.text().strip(),
            role_title=self.role_title.text().strip(),
            job_description=self.job_description.toPlainText().strip(),
            resume_override=self._resume_texts.get(resume_name, ""),
            resume_name=resume_name,
            ai_instructions=self.ai_instructions.toPlainText().strip(),
            answer_format=self.answer_format.currentText().strip(),
            answer_length=self.answer_length.currentText().strip(),
            answer_tone=self.answer_tone.currentText().strip(),
            model_route_override=model_override,
            session_type=SESSION_TYPES[self._type_group.checkedId()],
            language=self.language.currentText().strip() or "English",
            auto_generate=self.auto_generate.isChecked(),
            save_transcript=self.save_transcript.isChecked(),
        )
