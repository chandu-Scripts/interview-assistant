"""Run lint, type check and tests. Exit non-zero on first failure."""

import subprocess
import sys

STEPS = [
    [sys.executable, "-m", "ruff", "check", "src", "tests", "scripts"],
    [sys.executable, "-m", "mypy", "src"],
    [sys.executable, "-m", "pytest", "-q"],
]

for cmd in STEPS:
    print(">", " ".join(cmd[2:]))
    if subprocess.call(cmd) != 0:
        sys.exit(1)
