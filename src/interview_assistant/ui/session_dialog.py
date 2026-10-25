"""The "Create Interview Session" wizard, in two steps like the reference app:

1. Details - session type (Interview or Regular), company, role, job description, and a CV/Resume
   dropdown that lists resumes used before, with "Attach new resume" for a new file.
2. Preferences - language, model, answer format/length/tone, AI instructions (typed),
   Auto Generate and Save Transcript.

No "paste a job link" import and no Documents picker (not wanted), and no cross-user Question
Bank (conflicts with AGENTS.md's data-stays-local-by-default stance). The dialog only collects a
SessionDetails; activation is app/interview_session.py's job.
"""

from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.agents.specialists import BUILTIN_FEATURE_TITLES
from interview_assistant.app.attachments import ATTACHMENT_FILTER, AttachmentError, read_attachment
from interview_assistant.app.interview_session import SessionDetails
from interview_assistant.app.resume_library import ResumeLibrary
from interview_assistant.llm.router_factory import parse_chain
from interview_assistant.ui.controls import ChoiceComboBox, ToggleSwitch
from interview_assistant.ui.provider_catalog import SUGGESTIONS
from interview_assistant.ui.provider_picker import ModelSelector

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

    def __init__(
        self, library: ResumeLibrary | None = None, model_choices: list[str] | None = None
    ) -> None:
        super().__init__()
        self._library = library
        self._model_choices = model_choices or []
        self._resume_texts: dict[str, str] = {}
        self._chosen_resume = NO_RESUME

        self.pages = QStackedWidget()
        for page in (self._details_page(), self._preferences_page()):
            page.setObjectName("sessionForm")
            page.setStyleSheet("QWidget#sessionForm { background: transparent; }")
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(page)
            self.pages.addWidget(scroll)
        self._refresh_resumes()

        self.message = QLabel("")
        self.message.setStyleSheet("color: #ff9aab;")
        self.message.setWordWrap(True)
        self.back_btn = QPushButton("Back")
        self.cancel_btn = QPushButton("Cancel")
        self.next_btn = QPushButton("Next")
        self.next_btn.setObjectName("create")
        buttons = QHBoxLayout()
        buttons.addWidget(self.message, 1)
        buttons.addWidget(self.back_btn)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.next_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 28, 32, 24)
        layout.setSpacing(18)
        title = QLabel("Create Interview Session")
        title.setObjectName("title")
        self.progress = QLabel()
        self.progress.setStyleSheet("color: #77e5df; font-weight: 600;")
        self.subtitle = QLabel()
        self.subtitle.setWordWrap(True)
        self.subtitle.setObjectName("hint")
        layout.addWidget(title)
        layout.addWidget(self.progress)
        layout.addWidget(self.subtitle)
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
        form.setContentsMargins(0, 8, 16, 8)
        form.setVerticalSpacing(16)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        self.type_interview = QPushButton("Interview")
        self.type_regular = QPushButton("Regular")
        self._type_group = QButtonGroup(self)
        for i, b in enumerate((self.type_interview, self.type_regular)):
            b.setCheckable(True)
            self._type_group.addButton(b, i)
        self.type_interview.setToolTip("Prepare answers using the role, job description and resume")
        self.type_regular.setToolTip(
            "A regular conversation, such as an HR or group discussion round"
        )
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
        self.job_description.setMinimumHeight(110)
        self.job_description.setMaximumHeight(170)
        self.job_description.setPlaceholderText(
            "Paste the full job description, or just the role, e.g. Senior Azure Data Engineer"
        )
        self.resume_combo = ChoiceComboBox()
        self.resume_combo.activated.connect(self._on_resume_chosen)
        self.resume_status = QLabel("")

        form.addRow("Session type", types)
        form.addRow("Company *", self.company)
        form.addRow("Role (optional)", self.role_title)
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
            if self._library.error:
                self.resume_status.setText(self._library.error)
        self.resume_combo.addItem("Attach new resume…", ATTACH_NEW)
        index = max(self.resume_combo.findData(select), 0)
        self.resume_combo.setCurrentIndex(index)
        self._chosen_resume = self.resume_combo.currentData()
        self.resume_combo.blockSignals(False)

    def refresh_resumes(self) -> None:
        previous = self._chosen_resume
        self._refresh_resumes(select=previous)
        if previous and previous != self._chosen_resume:
            self.resume_status.setText(
                "The selected resume was renamed or deleted. Choose a resume again."
            )

    def _on_resume_chosen(self, index: int) -> None:
        choice = self.resume_combo.itemData(index)
        if choice != ATTACH_NEW or index != self.resume_combo.count() - 1:
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
        name = Path(path).name
        if name in self._resume_texts:
            box = QMessageBox(self)
            box.setWindowTitle("Replace saved resume")
            box.setTextFormat(Qt.TextFormat.PlainText)
            box.setText(
                f'Replace the saved text for "{name}"? Existing session copies stay unchanged.'
            )
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            if box.exec() != QMessageBox.StandardButton.Yes:
                self._refresh_resumes(select=self._chosen_resume)
                return
        try:
            self._library.add(name, text)
        except (OSError, ValueError) as e:
            self.resume_status.setText(str(e))
            self._refresh_resumes(select=self._chosen_resume)
            return
        self._refresh_resumes(select=Path(path).name)
        self.resume_status.setText(f"Saved {Path(path).name} for future sessions")

    # ---- preferences (step 2) ----
    def _preferences_page(self) -> QWidget:
        w = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(0, 8, 16, 8)
        form.setVerticalSpacing(8)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.language = ChoiceComboBox()
        self.language.addItems(LANGUAGES)  # English first: the default for most interviews
        self.fast_model_override = ModelSelector(self._model_choices, title="Fast model")
        self.coding_model_override = ModelSelector(self._model_choices, title="Coding model")
        self.model_override = self.fast_model_override  # compatibility for UI integrations
        self.model_selectors = (self.coding_model_override, self.fast_model_override)
        self.answer_format = ChoiceComboBox()
        self.answer_length = ChoiceComboBox()
        self.answer_tone = ChoiceComboBox()
        for combo, values in (
            (self.answer_format, ANSWER_FORMATS),
            (self.answer_length, ANSWER_LENGTHS),
            (self.answer_tone, ANSWER_TONES),
        ):
            for value in values:
                combo.addItem(value or "Specialist default", value)
        self.ai_instructions = QPlainTextEdit()
        self.ai_instructions.setFixedHeight(90)
        self.ai_instructions.setPlaceholderText(
            "Optional - extra notes the AI should use during this session"
        )
        self.auto_generate = QCheckBox("Answer questions automatically (Beta)")
        self.auto_generate.setToolTip("Off: you press Answer or Ctrl+Enter for each question")
        self.save_transcript = QCheckBox("Save this session's transcript")
        self.save_transcript.setChecked(True)
        form.addRow("Answer language", self.language)
        language_help = QLabel("Audio transcription currently supports English speech.")
        language_help.setObjectName("hint")
        language_help.setWordWrap(True)
        form.addRow("", language_help)
        model_title = QLabel("Preferred models")
        model_title.setObjectName("cardTitle")
        form.addRow(model_title)
        form.addRow("Coding model", self.coding_model_override)
        coding_help = QLabel("Coding and SQL questions · Recommended: GPT-5.4")
        coding_help.setObjectName("hint")
        coding_help.setWordWrap(True)
        form.addRow("", coding_help)
        form.addRow("Fast model", self.fast_model_override)
        fast_help = QLabel("All other interview questions · Recommended: GPT-5.4 mini")
        fast_help.setObjectName("hint")
        fast_help.setWordWrap(True)
        form.addRow("", fast_help)
        self.recommend_models_btn = QPushButton("Use recommended models")
        self.recommend_models_btn.clicked.connect(self._recommend_models)
        form.addRow("", self.recommend_models_btn)
        self.model_notice = QLabel()
        self.model_notice.setWordWrap(True)
        self.model_notice.setObjectName("hint")
        self.model_notice.hide()
        form.addRow("", self.model_notice)
        model_help = QLabel(
            "The app identifies the question type and uses your selection. "
            "Configured fallback models remain available."
        )
        model_help.setObjectName("hint")
        model_help.setWordWrap(True)
        form.addRow("", model_help)
        self.preference_notice = QLabel(
            "Turn off Built-in prompt to customise answer format, length and tone."
        )
        self.preference_notice.setObjectName("hint")
        self.preference_notice.setWordWrap(True)
        form.addRow("", self.preference_notice)
        form.addRow("Answer format", self.answer_format)
        form.addRow("Answer length", self.answer_length)
        form.addRow("Answer tone", self.answer_tone)
        form.addRow("AI instructions", self.ai_instructions)
        self.builtin_prompt_btn = ToggleSwitch()
        self.builtin_prompt_btn.setChecked(True)
        self.builtin_prompt_btn.setAccessibleName("Use built-in answer prompt")
        self.builtin_prompt_help = QLabel()
        self.builtin_prompt_help.setObjectName("hint")
        self.builtin_prompt_help.setWordWrap(True)
        self.prompt_features_btn = QPushButton("Features")
        self.prompt_features_btn.setCheckable(True)
        self.prompt_features_btn.setAccessibleName("Show built-in prompt features")
        prompt_controls = QHBoxLayout()
        prompt_controls.addWidget(self.builtin_prompt_btn)
        prompt_controls.addStretch()
        prompt_controls.addWidget(self.prompt_features_btn)
        self.prompt_features_panel = QFrame()
        self.prompt_features_panel.setObjectName("promptFeatures")
        features_layout = QVBoxLayout(self.prompt_features_panel)
        features_title = QLabel("Built-in prompt features")
        features_title.setObjectName("cardTitle")
        feature_header = QHBoxLayout()
        feature_header.addWidget(features_title, 1)
        self.prompt_features_close = QPushButton("Close")
        self.prompt_features_close.setAccessibleName("Close built-in prompt features")
        self.prompt_features_close.clicked.connect(
            lambda: self.prompt_features_btn.setChecked(False)
        )
        feature_header.addWidget(self.prompt_features_close)
        features_layout.addLayout(feature_header)
        feature_scroll = QScrollArea()
        feature_scroll.setWidgetResizable(True)
        feature_scroll.setFrameShape(QFrame.Shape.NoFrame)
        feature_scroll.setFixedHeight(200)
        feature_content = QWidget()
        feature_content.setObjectName("sessionForm")
        feature_items = QVBoxLayout(feature_content)
        for title in BUILTIN_FEATURE_TITLES:
            label = QLabel(title)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            feature_items.addWidget(label)
        feature_scroll.setWidget(feature_content)
        features_layout.addWidget(feature_scroll)
        self.prompt_features_panel.hide()
        self.prompt_features_btn.toggled.connect(self._show_prompt_features)
        self.builtin_prompt_btn.toggled.connect(self._update_builtin_prompt)
        self._update_builtin_prompt(True)
        form.addRow("", prompt_controls)
        form.addRow("", self.builtin_prompt_help)
        form.addRow(self.prompt_features_panel)
        form.addRow(self.auto_generate)
        form.addRow(self.save_transcript)
        return w

    def _show_prompt_features(self, visible: bool) -> None:
        self.prompt_features_panel.setVisible(visible)
        # Wait for the form's layout to expand before bringing the panel into view.
        QTimer.singleShot(0, self._reveal_prompt_features)

    def _reveal_prompt_features(self) -> None:
        target = (
            self.prompt_features_panel
            if self.prompt_features_btn.isChecked()
            else self.prompt_features_btn
        )
        if not target.isVisible():
            return
        parent = target.parentWidget()
        while parent is not None and not isinstance(parent, QScrollArea):
            parent = parent.parentWidget()
        if isinstance(parent, QScrollArea):
            parent.ensureWidgetVisible(target, 12, 12)

    def _update_builtin_prompt(self, enabled: bool) -> None:
        notice = self.preference_notice.text() if enabled else ""
        for combo in (self.answer_format, self.answer_length, self.answer_tone):
            combo.setEnabled(not enabled)
            combo.setToolTip(notice)
            combo.setAccessibleDescription(notice)
        self.preference_notice.setVisible(enabled)
        self.builtin_prompt_btn.setText(
            "Built-in prompt: On" if enabled else "Built-in prompt: Off"
        )
        self.builtin_prompt_help.setText(
            "Built-in style is active. Format, length and tone selections are ignored. "
            "AI instructions can still add extra guidance."
            if enabled
            else "Use your AI instructions and selected preferences without the app's style rules. "
            "Resume context and copy buttons remain available."
        )

    def _recommend_models(self) -> None:
        fast, coding = SUGGESTIONS["openai"]
        choices = ((self.coding_model_override, coding), (self.fast_model_override, fast))
        for selector, model in choices:
            selector.refresh_models()
            if selector.findData(f"openai:{model}") < 0:
                self.model_notice.setText(
                    "Verify your OpenAI key in Settings → AI providers. Both recommended "
                    "models must be available before applying recommendations."
                )
                self.model_notice.show()
                return
            if not selector.provider_ready("openai"):
                self.model_notice.setText("Restart the app to use your changed OpenAI key.")
                self.model_notice.show()
                return
        for selector, model in choices:
            selector.setCurrentIndex(selector.findData(f"openai:{model}"))
        self.model_notice.setText("Recommended models selected for this session.")
        self.model_notice.show()

    # ---- wizard flow ----
    def _update_buttons(self) -> None:
        on_first = self.pages.currentIndex() == 0
        self.progress.setText(
            "1  Session details     /     2  Answer preferences"
            if on_first
            else "1  Details complete     /     2  Answer preferences"
        )
        self.subtitle.setText(
            "Add context so answers fit your interview. Fields without * are optional."
            if on_first
            else f"Set up answers for {self.company.text().strip()}. "
            "Audio starts only after you choose Connect."
        )
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
                self.company.setFocus()
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
            try:
                self._library.touch(details.resume_name)
            except (OSError, ValueError) as e:
                self.message.setText(str(e))
                return
        self.created.emit(details)
        self._reset()

    def _cancel(self) -> None:
        self._reset()
        self.cancelled.emit()

    def _reset(self) -> None:
        self.company.clear()
        self.role_title.clear()
        self.job_description.clear()
        self.ai_instructions.clear()
        self.prompt_features_btn.setChecked(False)
        self.builtin_prompt_btn.setChecked(True)
        self.type_interview.setChecked(True)
        for combo in (
            self.language,
            self.model_override,
            self.coding_model_override,
            self.answer_format,
            self.answer_length,
            self.answer_tone,
        ):
            combo.setCurrentIndex(0)
        self.auto_generate.setChecked(False)
        self.save_transcript.setChecked(True)
        self._refresh_resumes()
        self.resume_status.clear()
        self.pages.setCurrentIndex(0)
        self.message.setText("")
        self.model_notice.clear()
        self.model_notice.hide()
        self._update_buttons()

    def collect(self) -> SessionDetails:
        """Raises ValueError with a user-readable reason if the model override is invalid."""
        routes = []
        for selector in self.model_selectors:
            selected = selector.currentData()
            selector.refresh_models()
            if selected and selected != selector.currentData():
                raise ValueError(
                    f"{selector.picker_title}: the provider is no longer verified. "
                    "Test its key or choose Auto."
                )
            route = selector.currentData() or ""
            if route and not selector.provider_ready(route.partition(":")[0]):
                raise ValueError("Restart the app to use the changed provider key.")
            if route and len(parse_chain(route)) != 1:
                raise ValueError("Choose one model per selection or Auto.")
            routes.append(route)
        if not self.company.text().strip():
            raise ValueError("Enter a company name.")
        resume_name = self._chosen_resume
        return SessionDetails(
            company=self.company.text().strip(),
            role_title=self.role_title.text().strip(),
            job_description=self.job_description.toPlainText().strip(),
            resume_override=self._resume_texts.get(resume_name, ""),
            resume_name=resume_name,
            ai_instructions=self.ai_instructions.toPlainText().strip(),
            use_builtin_prompt=self.builtin_prompt_btn.isChecked(),
            answer_format=self.answer_format.currentData(),
            answer_length=self.answer_length.currentData(),
            answer_tone=self.answer_tone.currentData(),
            coding_model_route=routes[0],
            fast_model_route=routes[1],
            session_type=SESSION_TYPES[self._type_group.checkedId()],
            language=self.language.currentText().strip() or "English",
            auto_generate=self.auto_generate.isChecked(),
            save_transcript=self.save_transcript.isChecked(),
        )
