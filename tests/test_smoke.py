from interview_assistant import __version__
from interview_assistant.config.settings import Settings
from interview_assistant.ui.main_window import MainWindow


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    s = Settings(_env_file=None)
    assert s.log_level == "INFO"
    assert s.openai_api_key is None


def test_secret_not_in_repr():
    s = Settings(_env_file=None, openai_api_key="sk-secret")
    assert "sk-secret" not in repr(s)


def test_main_window(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    assert w.windowTitle() == "Interview Assistant"
    assert __version__ in w.label.text()
