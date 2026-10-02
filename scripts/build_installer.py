"""Compile installer/interview_assistant.iss with Inno Setup (after `python scripts/build.py`).

Usage: python scripts/build_installer.py
Needs Inno Setup 6:  winget install JRSoftware.InnoSetup"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from interview_assistant import __version__  # noqa: E402

CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]


def find_iscc() -> str | None:
    on_path = shutil.which("iscc")
    if on_path:
        return on_path
    return next((str(p) for p in CANDIDATES if p.exists()), None)


def main() -> int:
    if not (ROOT / "dist" / "InterviewAssistant" / "InterviewAssistant.exe").exists():
        print("No build found. Run: python scripts/build.py")
        return 1
    iscc = find_iscc()
    if iscc is None:
        print("Inno Setup 6 was not found. Install it with: winget install JRSoftware.InnoSetup")
        return 4
    cmd = [
        iscc,
        f"/DMyAppVersion={__version__}",
        str(ROOT / "installer" / "interview_assistant.iss"),
    ]
    print(">", " ".join(cmd))
    code = subprocess.run(cmd, cwd=ROOT).returncode
    out = ROOT / "dist" / "installer" / f"InterviewAssistant-Setup-{__version__}.exe"
    if code == 0 and out.exists():
        print(f"\nInstaller: {out}  ({out.stat().st_size / 1e6:.0f} MB)")
        print("Unsigned installers are blocked by Smart App Control; sign it (docs/packaging.md).")
    return code


if __name__ == "__main__":
    sys.exit(main())
