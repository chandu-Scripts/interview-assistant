"""Account UI requests are local signals; no real accounts or provider calls."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit

from interview_assistant.ui.account_screen import AccountScreen


@pytest.fixture
def screen(qtbot):
    dialog = AccountScreen()
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


def click(qtbot, button):
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)


def test_login_emits_once_clears_secret_and_does_not_unlock(qtbot, screen):
    requests = []
    screen.submitted.connect(lambda action, payload: requests.append((action, payload)))
    screen.email.setText("  person@example.test  ")
    screen.password.setText("test-password")
    screen.remember.setChecked(True)
    click(qtbot, screen.submit_button)
    click(qtbot, screen.submit_button)
    assert requests == [
        ("login", {"email": "person@example.test", "password": "test-password", "remember": True})
    ]
    assert screen.password.text() == ""
    assert screen.result() != QDialog.DialogCode.Accepted
    assert screen.isVisible()
    assert not screen.forgot_button.isEnabled()
    screen.set_busy(False)
    assert screen.forgot_button.isEnabled()


def test_validation_is_local_and_does_not_send_incomplete_requests(qtbot, screen):
    requests = []
    screen.submitted.connect(lambda *args: requests.append(args))
    click(qtbot, screen.submit_button)
    assert "email" in screen.message.text()
    screen.email.setText("person@example.test")
    click(qtbot, screen.submit_button)
    assert "password" in screen.message.text()
    assert not requests
    assert screen.submit_button.isEnabled()


@pytest.mark.parametrize("email", ["@", "person@", "@example.test", "a@b@c", "a b@c"])
def test_invalid_email_cannot_submit(qtbot, screen, email):
    requests = []
    screen.submitted.connect(lambda *args: requests.append(args))
    screen.email.setText(email)
    screen.password.setText("test-password")
    click(qtbot, screen.submit_button)
    assert not requests
    assert "email" in screen.message.text()


def test_signup_requires_matching_passwords_and_preserves_email(qtbot, screen):
    screen.email.setText("person@example.test")
    click(qtbot, screen.signup_button)
    assert screen.stage == "signup"
    assert screen.email.text() == "person@example.test"
    screen.password.setText("test-password")
    screen.confirm_password.setText("different-password")
    click(qtbot, screen.submit_button)
    assert "do not match" in screen.message.text()
    screen.confirm_password.setText("test-password")
    with qtbot.waitSignal(screen.submitted) as emitted:
        click(qtbot, screen.submit_button)
    assert emitted.args[0] == "signup"
    assert "remember" not in emitted.args[1]
    screen.set_stage("verify_signup")
    assert screen.password.text() == screen.confirm_password.text() == ""
    assert screen.email.text() == "person@example.test"
    assert screen.email.isReadOnly()
    assert not screen.password.isVisible()


@pytest.mark.parametrize("stage", ["verify_signup", "verify_recovery"])
@pytest.mark.parametrize("code", ["123456", "12345678"])
def test_verification_accepts_configured_code_lengths(qtbot, screen, stage, code):
    screen.email.setText("person@example.test")
    screen.set_stage(stage)
    click(qtbot, screen.submit_button)
    assert "code" in screen.message.text()
    screen.code.setText(code)
    with qtbot.waitSignal(screen.submitted) as emitted:
        click(qtbot, screen.submit_button)
    assert emitted.args == [stage, {"email": "person@example.test", "code": code}]
    assert screen.code.text() == ""


def test_recovery_and_reset_remain_in_same_window(qtbot, screen):
    screen.email.setText("person@example.test")
    click(qtbot, screen.forgot_button)
    assert screen.stage == "recover"
    with qtbot.waitSignal(screen.submitted) as emitted:
        click(qtbot, screen.submit_button)
    assert emitted.args == ["recover", {"email": "person@example.test"}]
    screen.set_stage("reset")
    assert not screen.email.isVisible()
    screen.password.setText("new-test-password")
    screen.confirm_password.setText("new-test-password")
    with qtbot.waitSignal(screen.submitted) as emitted:
        click(qtbot, screen.submit_button)
    assert emitted.args == ["reset", {"password": "new-test-password"}]
    assert screen.result() != QDialog.DialogCode.Accepted
    screen.set_stage("login")
    assert not screen.email.isReadOnly()
    assert screen.email.text() == "person@example.test"


def test_secrets_are_masked_pasteable_and_cleared_on_navigation(qtbot, screen):
    assert screen.password.echoMode() == QLineEdit.EchoMode.Password
    assert screen.confirm_password.echoMode() == QLineEdit.EchoMode.Password
    assert not screen.remember.isChecked()
    clipboard = QApplication.clipboard()
    previous = clipboard.text()
    try:
        clipboard.setText("synthetic-password")
        screen.password.setFocus()
        qtbot.keyClick(screen.password, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
        assert screen.password.text() == "synthetic-password"
    finally:
        clipboard.setText(previous)
    click(qtbot, screen.forgot_button)
    assert screen.password.text() == ""
    screen.code.setText("12345678")
    click(qtbot, screen.back_button)
    assert screen.code.text() == ""


def test_restore_is_explicit_and_busy_gated(qtbot, screen):
    assert not screen.restore_button.isVisible()
    screen.set_restore_available(True)
    assert screen.restore_button.isVisible()
    with qtbot.waitSignal(screen.submitted) as emitted:
        click(qtbot, screen.restore_button)
    assert emitted.args == ["restore", {}]
    assert not screen.restore_button.isEnabled()
    screen.set_stage("signup")
    assert not screen.restore_button.isVisible()


def test_back_navigation_notifies_controller_to_clear_recovery(qtbot, screen):
    screen.set_stage("reset")
    with qtbot.waitSignal(screen.stage_changed) as emitted:
        click(qtbot, screen.back_button)
    assert emitted.args == ["login"]
    assert screen.stage == "login"


def test_escape_clears_secrets_even_when_busy(qtbot, screen):
    screen.password.setText("synthetic-secret")
    screen.set_busy(True)
    with qtbot.waitSignal(screen.rejected):
        qtbot.keyClick(screen, Qt.Key.Key_Escape)
    assert screen.password.text() == ""


def test_long_status_is_plain_text_and_does_not_grow_window(qtbot, screen):
    before = screen.size()
    screen.show_message("<b>Connection failed</b> " * 50, error=True)
    QApplication.processEvents()
    assert screen.message.textFormat() == Qt.TextFormat.PlainText
    assert screen.size() == before
    assert screen.width() <= 960 and screen.height() <= 680
    with pytest.raises(ValueError, match="Unknown account"):
        screen.set_stage("unexpected")
