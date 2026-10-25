import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings

from interview_assistant import __version__, cli, selftest
from interview_assistant import purge as purge_mod
from interview_assistant.app import restart
from interview_assistant.config.keys import MemoryKeyStore
from interview_assistant.purge import purge

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def ini(tmp_path, name):
    return QSettings(str(tmp_path / f"{name}.ini"), QSettings.Format.IniFormat)


# ---------------- CLI ----------------
def test_no_switch_starts_the_ui():
    assert cli.handle(["app"]) is None
    assert cli.handle(["app", "--something-else"]) is None


def test_version_switch_prints_and_writes_file(tmp_path, capsys):
    out = tmp_path / "v.txt"
    assert cli.handle(["app", "--version", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").strip() == f"Interview Assistant {__version__}"
    assert __version__ in capsys.readouterr().out


def test_option_parsing():
    assert cli._option(["--out", "x"], "--out") == "x"
    assert cli._option(["--out"], "--out") is None
    assert cli._option([], "--out") is None


def test_version_matches_pyproject():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(rf'^version = "{re.escape(__version__)}"', text, re.M)


# ---------------- self-test ----------------
def test_selftest_required_checks_pass_from_source():
    checks = selftest.run_checks()
    failed = [(c.name, c.detail) for c in checks if c.required and not c.ok]
    assert failed == []
    assert {c.name for c in checks} >= {
        "qt",
        "sqlite-fts5",
        "keyring",
        "tls-certificates",
        "whisper",
    }


def test_selftest_report_file_and_exit_code(tmp_path, monkeypatch):
    out = tmp_path / "r.json"
    monkeypatch.setattr(
        selftest,
        "run_checks",
        lambda deep=False: [selftest.Check("a", True), selftest.Check("b", True)],
    )
    assert selftest.run(out) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["ok"] is True and report["version"] == __version__ and len(report["checks"]) == 2


def test_selftest_failure_and_optional_checks(tmp_path, monkeypatch):
    def boom():
        raise FileNotFoundError("missing vad model")

    bad = selftest._run("whisper", boom)
    assert not bad.ok and "FileNotFoundError" in bad.detail and "missing vad model" in bad.detail
    optional_fail = selftest.Check("audio", False, "no devices", required=False)
    monkeypatch.setattr(
        selftest, "run_checks", lambda deep=False: [selftest.Check("a", True), optional_fail]
    )
    assert selftest.run(tmp_path / "ok.json") == 0  # a missing audio device is not a failed install
    monkeypatch.setattr(selftest, "run_checks", lambda deep=False: [bad])
    assert selftest.run(tmp_path / "bad.json") == 1


def test_deep_flag_is_passed_through(monkeypatch):
    seen = {}
    monkeypatch.setattr(selftest, "run", lambda out=None, deep=False: seen.update(deep=deep) or 0)
    assert cli.handle(["app", "--self-test", "--deep"]) == 0 and seen["deep"] is True
    assert cli.handle(["app", "--self-test"]) == 0 and seen["deep"] is False


def test_deep_checks_are_added_only_on_request(monkeypatch):
    monkeypatch.setattr(selftest, "_online", lambda: "stub")
    monkeypatch.setattr(selftest, "_whisper_run", lambda: "stub")
    names = {c.name for c in selftest.run_checks(deep=True)}
    assert {"online-tls", "whisper-run"} <= names
    assert "online-tls" not in {c.name for c in selftest.run_checks(deep=False)}


# ---------------- purge (never touches the real credential store / registry) ----------------
def test_purge_removes_data_keys_and_saved_state(tmp_path):
    data = tmp_path / "InterviewAssistant"
    data.mkdir()
    (data / "settings.json").write_text("{}", encoding="utf-8")
    (data / "history.db").write_bytes(b"secret history")
    keys = MemoryKeyStore({"openai": "sk-x", "gemini": "AIza-y", "speechmatics": "test-key"})
    qs = [ini(tmp_path, "a"), ini(tmp_path, "b")]
    for q in qs:
        q.setValue("onboarding/done_v1", True)
        q.sync()
    report = purge(data, keys, qs)
    assert report.data_dir_removed and not data.exists()
    assert sorted(report.keys_removed) == ["gemini", "openai", "speechmatics"] and keys.data == {}
    assert report.settings_cleared == 2 and all(q.allKeys() == [] for q in qs)
    assert report.errors == []


def test_purge_is_idempotent_and_reports_errors(tmp_path, monkeypatch):
    report = purge(tmp_path / "nothing-here", MemoryKeyStore(), [])
    assert not report.data_dir_removed and report.errors == []
    data = tmp_path / "d"
    data.mkdir()

    def locked(_p):
        raise PermissionError("in use")

    monkeypatch.setattr(purge_mod.shutil, "rmtree", locked)
    r2 = purge(data, MemoryKeyStore(), [])
    assert not r2.data_dir_removed and "in use" in r2.errors[0]


def test_purge_switch_uses_data_dir_env_and_injected_stores(tmp_path, monkeypatch):
    data = tmp_path / "custom"
    data.mkdir()
    (data / "history.db").write_bytes(b"x")
    monkeypatch.setenv("DATA_DIR", str(data))
    store = MemoryKeyStore({"anthropic": "ant-key"})
    monkeypatch.setattr("interview_assistant.config.keys.KeyringStore", lambda *a, **k: store)
    monkeypatch.setattr(purge_mod, "QSettings", lambda *a, **k: ini(tmp_path, "purge-test"))
    out = tmp_path / "out.txt"
    assert cli.handle(["app", "--purge-data", "--out", str(out)]) == 0
    assert not data.exists() and store.data == {}
    assert "data folder removed: True" in out.read_text(encoding="utf-8")


# ---------------- restart when packaged ----------------
def test_relaunch_uses_the_exe_itself_when_frozen(monkeypatch):
    started = []
    monkeypatch.setattr(restart.subprocess, "Popen", lambda cmd, **kw: started.append(cmd))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    restart.relaunch()
    assert started == [[sys.executable]]
    monkeypatch.delattr(sys, "frozen")
    restart.relaunch()
    assert started[1] == [sys.executable, "-m", "interview_assistant"]


# ---------------- build inputs ----------------
def test_spec_and_installer_reference_existing_files():
    spec = (ROOT / "packaging" / "interview_assistant.spec").read_text(encoding="utf-8")
    assert (ROOT / "packaging" / "launcher.py").exists() and "launcher.py" in spec
    assert (ROOT / "packaging" / "app.ico").exists() and (
        ROOT / "packaging" / "app.ico"
    ).stat().st_size > 1000
    iss = (ROOT / "installer" / "interview_assistant.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in iss and "--purge-data" in iss
    assert "InterviewAssistant.exe" in iss and "UninstallSilent" in iss
    # a function must be defined before it is used in Inno's Pascal
    assert iss.index("function CmdLineParamExists") < iss.index("CmdLineParamExists('/PURGE')")


def test_launcher_entry_point_imports():
    src = (ROOT / "packaging" / "launcher.py").read_text(encoding="utf-8")
    assert "from interview_assistant.__main__ import main" in src


def test_build_version_resource_contains_version():
    build = load_script("build")
    text = build.write_version_file().read_text(encoding="utf-8")
    assert __version__ in text and "InterviewAssistant.exe" in text and "040904B0" in text


def test_hidden_imports_cover_all_packages():
    spec = (ROOT / "packaging" / "interview_assistant.spec").read_text(encoding="utf-8")
    assert 'collect_submodules("interview_assistant")' in spec  # lazy imports are still bundled
    assert 'collect_data_files("faster_whisper")' in spec  # the bundled VAD model
    assert 'collect_data_files("certifi")' in spec  # HTTPS certificates


# ---------------- static dependency check ----------------
def test_dependency_checker_flags_and_accepts(tmp_path):
    mod = load_script("check_dist_deps")
    exe = Path(sys.base_prefix) / "python.exe"
    dll = Path(sys.base_prefix) / f"python{sys.version_info.major}{sys.version_info.minor}.dll"
    if not exe.exists():
        pytest.skip("no base python.exe")
    dist = tmp_path / "dist"
    dist.mkdir()
    shutil.copy(exe, dist / "python.exe")
    mod.DIST = dist
    assert mod.main() == 1  # python.exe needs pythonXY.dll, which is not in the bundle
    shutil.copy(dll, dist / dll.name)
    assert mod.main() == 0
    assert mod.main.__module__ and mod.is_windows_provided("api-ms-win-core-file-l1-1-0.dll")
    assert mod.is_windows_provided("KERNEL32.dll") and not mod.is_windows_provided("nope-xyz.dll")


def test_dependency_checker_without_a_build(tmp_path):
    mod = load_script("check_dist_deps")
    mod.DIST = tmp_path / "missing"
    assert mod.main() == 2
