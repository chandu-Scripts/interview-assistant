"""Self-test: verifies that everything the app needs is present and loadable.

Run from source (`python -m interview_assistant --self-test`) or from the packaged exe
(`InterviewAssistant.exe --self-test --out result.json`). It reads no secrets and writes nothing
except the optional --out file."""

import json
import os
import platform
import sqlite3
import ssl
import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from interview_assistant import __version__


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    required: bool = True


def _run(name: str, fn: Callable[[], str], required: bool = True) -> Check:
    try:
        return Check(name, True, fn(), required)
    except Exception as e:  # any failure is a finding, not a crash
        return Check(name, False, f"{type(e).__name__}: {e}", required)


def _qt() -> str:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QLibraryInfo
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    plugins = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath))
    windows_plugin = plugins / "platforms" / "qwindows.dll"
    if sys.platform == "win32" and not windows_plugin.exists():
        raise FileNotFoundError(f"missing {windows_plugin}")
    return f"platform={QGuiApplication.platformName()} plugins={plugins}"


def _audio() -> str:
    import pyaudiowpatch as pa

    p = pa.PyAudio()
    try:
        n = p.get_device_count()
        try:
            loop = p.get_default_wasapi_loopback()["name"]
        except Exception:
            loop = "none"
        return f"{n} devices, default loopback: {loop}"
    finally:
        p.terminate()


def _whisper() -> str:
    import ctranslate2
    import faster_whisper
    from faster_whisper.utils import get_assets_path

    assets = Path(get_assets_path())
    onnx = sorted(f.name for f in assets.glob("*.onnx"))
    if not onnx:
        raise FileNotFoundError(f"no VAD model in {assets}")
    versions = f"faster-whisper {faster_whisper.__version__}, ctranslate2 {ctranslate2.__version__}"
    return f"{versions}, {onnx}"


def _onnx() -> str:
    import onnxruntime

    return (
        f"onnxruntime {onnxruntime.__version__} providers={onnxruntime.get_available_providers()}"
    )


def _keyring() -> str:
    import keyring

    name = type(keyring.get_keyring()).__name__
    if sys.platform == "win32" and "WinVault" not in name:
        raise RuntimeError(f"unexpected keyring backend {name}")
    return name


def _sqlite() -> str:
    db = sqlite3.connect(":memory:")
    db.execute("CREATE VIRTUAL TABLE t USING fts5(a, tokenize='porter unicode61')")
    db.execute("INSERT INTO t VALUES ('conflicts')")
    hit = db.execute("SELECT count(*) FROM t WHERE t MATCH 'conflict'").fetchone()[0]
    if hit != 1:
        raise RuntimeError("FTS5 stemming failed")
    return f"sqlite {sqlite3.sqlite_version} with FTS5"


def _tls() -> str:
    import certifi

    ca = Path(certifi.where())
    if not ca.exists():
        raise FileNotFoundError(f"CA bundle missing: {ca}")
    ssl.create_default_context(cafile=str(ca))
    return f"CA bundle {ca.name} ({ca.stat().st_size // 1024} KB)"


def _settings() -> str:
    from interview_assistant.config.settings import Settings

    s = Settings()
    return f"stt={s.stt_provider}/{s.whisper_model}"


def _data_dir() -> str:
    from interview_assistant.config.user_settings import default_data_dir

    d = default_data_dir()
    parent = d if d.exists() else d.parent
    if not os.access(parent, os.W_OK):
        raise PermissionError(f"cannot write to {parent}")
    return str(d)


def _modules() -> str:
    import importlib

    names = [
        "interview_assistant.agents.jev",
        "interview_assistant.app.controller",
        "interview_assistant.app.hotkeys",
        "interview_assistant.cost.tracker",
        "interview_assistant.history.store",
        "interview_assistant.llm.router",
        "interview_assistant.privacy.redact",
        "interview_assistant.stt.streaming",
        "interview_assistant.ui.main_window",
        "interview_assistant.ui.onboarding",
        "interview_assistant.ui.settings_dialog",
    ]
    for n in names:
        importlib.import_module(n)
    return f"{len(names)} app modules import"


def _online() -> str:
    """TLS + DNS from inside the bundle. No key is sent: 400/401/403 means the server answered."""
    import httpx

    urls = {
        "openai": "https://api.openai.com/v1/models",
        "anthropic": "https://api.anthropic.com/v1/models",
        "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
    }
    seen = []
    with httpx.Client(timeout=15) as c:
        for name, url in urls.items():
            r = c.get(url)
            if r.status_code not in (400, 401, 403):
                raise RuntimeError(f"{name}: unexpected HTTP {r.status_code}")
            seen.append(f"{name}={r.status_code}")
    return "TLS ok, " + ", ".join(seen)


def _whisper_run() -> str:
    """Load the cached Whisper model and transcribe a second of silence (expects no text)."""
    import time

    import numpy as np
    from faster_whisper import WhisperModel

    t0 = time.time()
    model = WhisperModel("base.en", device="cpu", compute_type="int8", local_files_only=True)
    load_s = time.time() - t0
    segments, _ = model.transcribe(
        np.zeros(16000, dtype=np.float32), language="en", vad_filter=True
    )
    text = " ".join(s.text for s in segments).strip()
    return f"model loaded in {load_s:.1f}s, silence -> {text!r}"


def run_checks(deep: bool = False) -> list[Check]:
    extra = (
        [
            _run("online-tls", _online, required=False),
            _run("whisper-run", _whisper_run, required=False),
        ]
        if deep
        else []
    )
    return extra + [
        _run("qt", _qt),
        _run("app-modules", _modules),
        _run("settings", _settings),
        _run("data-dir", _data_dir),
        _run("sqlite-fts5", _sqlite),
        _run("keyring", _keyring),
        _run("tls-certificates", _tls),
        _run("whisper", _whisper),
        _run("onnxruntime", _onnx),
        _run("audio", _audio, required=False),  # a PC without audio devices is still valid
    ]


def run(out: Path | None = None, deep: bool = False) -> int:
    checks = run_checks(deep)
    report = {
        "version": __version__,
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "ok": all(c.ok for c in checks if c.required),
        "checks": [asdict(c) for c in checks],
    }
    text = json.dumps(report, indent=2)
    if out is not None:
        out.write_text(text, encoding="utf-8")
    if sys.stdout is not None:
        print(text)
    return 0 if report["ok"] else 1
