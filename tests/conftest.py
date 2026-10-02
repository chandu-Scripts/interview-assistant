import pytest


@pytest.fixture(autouse=True)
def isolated_data_dir(monkeypatch, tmp_path_factory):
    """Keep tests away from the real %APPDATA%/InterviewAssistant (settings, history)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path_factory.mktemp("appdata")))
