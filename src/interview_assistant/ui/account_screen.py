"""Account forms without network, credential persistence, or workspace access."""

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QCloseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from interview_assistant.ui.theme import STYLE, WindowBar, center_window, paint_glass

_STAGES = {
    "login": ("Welcome back", "Sign in to your private workspace.", "Sign in"),
    "signup": (
        "Create your account",
        "Use your email and a password. Confirm your email before opening your workspace.",
        "Create account",
    ),
    "verify_signup": (
        "Confirm your email",
        "If an email was sent, enter its confirmation code below.",
        "Confirm email",
    ),
    "recover": (
        "Reset your password",
        "Enter your email. If the account is eligible, we will send a recovery code.",
        "Send recovery code",
    ),
    "verify_recovery": (
        "Enter recovery code",
        "If an email was sent, enter its recovery code below.",
        "Verify code",
    ),
    "reset": (
        "Choose a new password",
        "Save your new password, then sign in again to open your workspace.",
        "Save new password",
    ),
}


class AccountScreen(QDialog):
    """Emit requests for an external controller; never accept itself on submission."""

    submitted = Signal(str, object)
    stage_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.stage = "login"
        self._busy = False
        self._restore_available = False
        self.setWindowTitle("Chandoo Assistant - Sign in")
        self.setStyleSheet(STYLE)
        self.setWindowFlag(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        center_window(self, 840, 660)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 0, 8, 12)
        layout.addWidget(WindowBar(self))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        layout.addWidget(scroll, 1)
        body = QWidget()
        body.setObjectName("accountBody")
        body.setStyleSheet("QWidget#accountBody { background: transparent; }")
        scroll.setWidget(body)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(24, 12, 24, 12)
        body_layout.addStretch()
        self.form = QWidget()
        self.form.setMaximumWidth(500)
        form_layout = QVBoxLayout(self.form)
        form_layout.setContentsMargins(8, 0, 8, 0)
        form_layout.setSpacing(10)
        body_layout.addWidget(self.form, 1)
        body_layout.addStretch()
        brand = QLabel("Chandoo Assistant")
        brand.setObjectName("brand")
        form_layout.addWidget(brand)
        self.title = QLabel()
        self.title.setObjectName("title")
        self.title.setWordWrap(True)
        form_layout.addWidget(self.title)
        self.description = QLabel()
        self.description.setObjectName("hint")
        self.description.setWordWrap(True)
        self.description.setTextFormat(Qt.TextFormat.PlainText)
        form_layout.addWidget(self.description)

        self.email_label, self.email = self._field(form_layout, "Email", "accountEmail")
        self.email.setMaxLength(254)
        self.email.setPlaceholderText("you@example.com")
        self.password_label, self.password = self._field(
            form_layout, "Password", "accountPassword", secret=True
        )
        self.confirm_label, self.confirm_password = self._field(
            form_layout, "Confirm password", "accountConfirmPassword", secret=True
        )
        self.code_label, self.code = self._field(form_layout, "Email code", "accountCode")
        # Supabase's configured OTP length can change; do not impose a digit count here.
        self.code.setMaxLength(128)
        self.remember = QCheckBox("Remember me on this laptop")
        self.remember.setChecked(False)
        form_layout.addWidget(self.remember)
        self.message = QLabel()
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setAccessibleName("Account status")
        form_layout.addWidget(self.message)
        self.submit_button = self._button("", form_layout)
        self.submit_button.setObjectName("create")
        self.submit_button.clicked.connect(self._submit)
        self.restore_button = self._button("Continue saved sign-in", form_layout)
        self.restore_button.clicked.connect(self._restore)
        self.signup_button = self._button("Create an account", form_layout)
        self.signup_button.clicked.connect(lambda: self.set_stage("signup"))
        self.forgot_button = self._button("Forgot password?", form_layout)
        self.forgot_button.clicked.connect(lambda: self.set_stage("recover"))
        self.back_button = self._button("Back to sign in", form_layout)
        self.back_button.clicked.connect(lambda: self.set_stage("login"))
        form_layout.addStretch()
        self.set_stage("login")

    def _field(
        self, layout: QVBoxLayout, title: str, name: str, *, secret: bool = False
    ) -> tuple[QLabel, QLineEdit]:
        label = QLabel(title)
        field = QLineEdit()
        field.setObjectName(name)
        field.setAccessibleName(title)
        field.setMaxLength(1024)
        if secret:
            field.setEchoMode(QLineEdit.EchoMode.Password)
        label.setBuddy(field)
        field.returnPressed.connect(self._submit)
        layout.addWidget(label)
        layout.addWidget(field)
        return label, field

    @staticmethod
    def _button(text: str, layout: QVBoxLayout) -> QPushButton:
        button = QPushButton(text)
        button.setAutoDefault(False)
        button.setDefault(False)
        layout.addWidget(button)
        return button

    def set_stage(self, stage: str) -> None:
        if stage not in _STAGES:
            raise ValueError("Unknown account form stage")
        self.clear_secrets()
        self.stage = stage
        title, description, action = _STAGES[stage]
        self.title.setText(title)
        self.description.setText(description)
        self.submit_button.setText(action)
        self.show_message("")
        self.set_busy(False)
        email_visible = stage != "reset"
        self.email.setVisible(email_visible)
        self.email_label.setVisible(email_visible)
        self.email.setReadOnly(stage.startswith("verify_"))
        for label, field, visible in (
            (self.password_label, self.password, stage in {"login", "signup", "reset"}),
            (self.confirm_label, self.confirm_password, stage in {"signup", "reset"}),
            (self.code_label, self.code, stage.startswith("verify_")),
        ):
            label.setVisible(visible)
            field.setVisible(visible)
        self.remember.setVisible(stage == "login")
        self.signup_button.setVisible(stage == "login")
        self.forgot_button.setVisible(stage == "login")
        self.back_button.setVisible(stage != "login")
        self.restore_button.setVisible(stage == "login" and self._restore_available)
        focus = self.code if stage.startswith("verify_") else self.email
        if stage == "reset":
            focus = self.password
        focus.setFocus()
        self.stage_changed.emit(stage)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        for widget in (
            self.email,
            self.password,
            self.confirm_password,
            self.code,
            self.remember,
            self.submit_button,
            self.restore_button,
            self.signup_button,
            self.forgot_button,
            self.back_button,
        ):
            widget.setEnabled(not busy)
        self.submit_button.setText("Please wait..." if busy else _STAGES[self.stage][2])

    def show_message(self, message: str, error: bool = False) -> None:
        self.message.setText(message)
        self.message.setStyleSheet("color: #ffb5b5;" if error else "color: #a7d8da;")
        self.message.setVisible(bool(message))

    def clear_secrets(self) -> None:
        self.password.clear()
        self.confirm_password.clear()
        self.code.clear()

    def set_restore_available(self, available: bool) -> None:
        self._restore_available = available
        self.restore_button.setVisible(available and self.stage == "login")

    def _submit(self) -> None:
        if self._busy:
            return
        payload: dict[str, object] = {}
        if self.stage != "reset":
            email = self.email.text().strip()
            address = email.split("@")
            if len(address) != 2 or not all(address) or any(char.isspace() for char in email):
                self.show_message("Enter a valid email address.", error=True)
                self.email.setFocus()
                return
            payload["email"] = email
        if self.stage in {"login", "signup", "reset"}:
            password = self.password.text()
            if not password:
                self.show_message("Enter your password.", error=True)
                self.password.setFocus()
                return
            if self.stage in {"signup", "reset"} and password != self.confirm_password.text():
                self.show_message("The passwords do not match.", error=True)
                self.confirm_password.setFocus()
                return
            payload["password"] = password
        if self.stage.startswith("verify_"):
            code = self.code.text().strip()
            if not code:
                self.show_message("Enter the code from your email.", error=True)
                self.code.setFocus()
                return
            payload["code"] = code
        if self.stage == "login":
            payload["remember"] = self.remember.isChecked()
        self.clear_secrets()
        self.show_message("")
        self.set_busy(True)
        self.submitted.emit(self.stage, payload)

    def _restore(self) -> None:
        if self._busy or not self._restore_available or self.stage != "login":
            return
        self.clear_secrets()
        self.show_message("")
        self.set_busy(True)
        self.submitted.emit("restore", {})

    def reject(self) -> None:
        self.clear_secrets()
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.clear_secrets()
        super().closeEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        paint_glass(painter, QRectF(self.rect()))
        painter.end()
