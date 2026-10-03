"""UI for Step 17's "Create Interview Session": a 2-step wizard (Details -> Preferences), mirroring
the shape of a competing product's flow but deliberately leaving out two things that don't fit this
app's design: no "paste a job link" auto-import (not requested, extra scope) and no cross-user
"Question Bank" (conflicts with AGENTS.md's data-stays-local-by-default stance).

This dialog only collects a SessionDetails - it does not touch Jev/history itself. Activating it
(mutating the live config, tagging new history turns) is app/interview_session.py's job, kept
separate so each half is trivially testable on its own. Wiring a button to open this dialog is
Step 18 (the session dashboard)."""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
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

from interview_assistant.app.interview_session import SessionDetails
from interview_assistant.llm.router_factory import parse_chain

ANSWER_FORMATS = ["", "Bullet points", "Short paragraph", "STAR structure"]
ANSWER_LENGTHS = ["", "Short", "Balanced", "Detailed"]
ANSWER_TONES = ["", "Simple", "Formal", "Casual", "Technical"]


class SessionDialog(QDialog):
    """Collects company/role/JD/resume override, then model/format/length/tone preferences and
    free-text AI instructions. Emits `created` with the resulting SessionDetails on the final
    step; the caller (composition.py, via InterviewSessionManager) does the actual activation."""

    created = Signal(object)  # SessionDetails

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Create Interview Session")
        self.resize(480, 440)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._details_page())
        self.pages.addWidget(self._preferences_page())

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
        self.cancel_btn.clicked.connect(self.reject)
        self._update_buttons()

    # ---- pages ----
    def _details_page(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        self.company = QLineEdit()
        self.company.setPlaceholderText("Enter company name")
        self.role_title = QLineEdit()
        self.role_title.setPlaceholderText("e.g. Senior Backend Engineer")
        self.job_description = QPlainTextEdit()
        self.job_description.setPlaceholderText("Paste or type the job description")
        self.resume_override = QPlainTextEdit()
        self.resume_override.setPlaceholderText(
            "Optional - overrides your profile (Settings -> Profile) for this session only"
        )
        form.addRow("Company", self.company)
        form.addRow("Role", self.role_title)
        form.addRow("Job Description", self.job_description)
        form.addRow("Resume (optional)", self.resume_override)
        return w

    def _preferences_page(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
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
        form.addRow("Model (advanced)", self.model_override)
        form.addRow("Answer format", self.answer_format)
        form.addRow("Answer length", self.answer_length)
        form.addRow("Answer tone", self.answer_tone)
        form.addRow("AI instructions", self.ai_instructions)
        return w

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
        self.created.emit(details)
        self.accept()

    def collect(self) -> SessionDetails:
        """Raises ValueError with a user-readable reason if the model override is invalid."""
        model_override = self.model_override.text().strip()
        if model_override:
            try:
                parse_chain(model_override)
            except ValueError as e:
                raise ValueError(f"Model override: {e}") from e
        return SessionDetails(
            company=self.company.text().strip(),
            role_title=self.role_title.text().strip(),
            job_description=self.job_description.toPlainText().strip(),
            resume_override=self.resume_override.toPlainText().strip(),
            ai_instructions=self.ai_instructions.toPlainText().strip(),
            answer_format=self.answer_format.currentText().strip(),
            answer_length=self.answer_length.currentText().strip(),
            answer_tone=self.answer_tone.currentText().strip(),
            model_route_override=model_override,
        )
