"""Static guard rails: patterns that would open a security hole must not appear in shipped code."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "interview_assistant"
SOURCES = sorted(SRC.rglob("*.py"))

BANNED = {
    r"shell\s*=\s*True": "shell injection surface",
    r"verify\s*=\s*False": "disables TLS certificate verification",
    r"\beval\(": "dynamic code execution",
    r"(?<![.\w])exec\(": "dynamic code execution (the builtin; Qt's .exec() is fine)",
    r"\bpickle\b": "unsafe deserialization",
    r"\bmarshal\.loads": "unsafe deserialization",
    r"yaml\.load\(": "unsafe deserialization",
    r"os\.system\(": "shell command injection surface",
    r"os\.popen\(": "shell command injection surface",
    r"SetWindowDisplayAffinity|WDA_EXCLUDEFROMCAPTURE": "capture evasion (forbidden)",
    r"http://(?!localhost|127\.0\.0\.1)": "plain-HTTP endpoint",
}
KEY_SHAPES = [
    r"sk-[A-Za-z0-9_\-]{20,}",
    r"sk-ant-[A-Za-z0-9_\-]{20,}",
    r"AIza[0-9A-Za-z_\-]{30,}",
    r"gh[pousr]_[A-Za-z0-9]{30,}",
    r"AKIA[0-9A-Z]{16}",
]


def test_the_source_tree_was_found():
    assert len(SOURCES) > 50


@pytest.mark.parametrize(("pattern", "why"), BANNED.items())
def test_banned_patterns_are_absent(pattern, why):
    hits = []
    for py in SOURCES:
        for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("#") or "http://timestamp" in line:
                continue
            if re.search(pattern, line):
                hits.append(f"{py.relative_to(ROOT)}:{n}: {line.strip()[:90]}")
    assert not hits, f"{why}:\n" + "\n".join(hits)


def test_no_hard_coded_secrets_in_source_or_docs():
    files = [*SOURCES, *(ROOT / "docs").rglob("*.md"), ROOT / "AGENTS.md", ROOT / ".env.example"]
    hits = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        for shape in KEY_SHAPES:
            for m in re.finditer(shape, text):
                hits.append(f"{f.relative_to(ROOT)}: {m.group(0)[:12]}...")
    assert not hits, "\n".join(hits)


def test_env_files_and_data_are_git_ignored():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    for needed in (".env", "*.db", "dist/", "build/"):
        assert needed in ignore, f"{needed} must be in .gitignore"


def test_only_expected_modules_spawn_processes():
    """Process creation is confined to the relaunch helper (and never uses a shell)."""
    spawners = sorted(
        str(py.relative_to(SRC))
        for py in SOURCES
        if re.search(r"subprocess\.(Popen|run|call|check_)", py.read_text(encoding="utf-8"))
    )
    assert spawners == [str(Path("app") / "restart.py")]


def test_network_clients_use_https_only():
    urls = set()
    for py in SOURCES:
        urls |= set(re.findall(r'"(https?://[^"\s]+)"', py.read_text(encoding="utf-8")))
    assert urls, "expected provider endpoints to be found"
    assert all(u.startswith("https://") for u in urls), sorted(urls)


def test_secrets_are_never_stored_in_the_settings_file_schema():
    from interview_assistant.config.user_settings import EDITABLE

    secretish = re.compile(r"api_key|secret|password|access_token|auth_token|bearer")
    assert not [k for k in EDITABLE if secretish.search(k)]
