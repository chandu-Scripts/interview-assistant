"""Build the Windows app: icon -> version resource -> PyInstaller (onedir) -> packaged self-test.

Usage: python scripts/build.py [--no-clean]
Output: dist/InterviewAssistant/InterviewAssistant.exe"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from interview_assistant import __version__  # noqa: E402

DIST = ROOT / "dist" / "InterviewAssistant"
EXE = DIST / "InterviewAssistant.exe"


def write_version_file() -> Path:
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo,
        StringFileInfo,
        StringStruct,
        StringTable,
        VarFileInfo,
        VarStruct,
        VSVersionInfo,
    )

    parts = (__version__.split(".") + ["0", "0", "0"])[:4]
    tup = tuple(int(p) for p in parts)
    info = VSVersionInfo(
        ffi=FixedFileInfo(
            filevers=tup, prodvers=tup, mask=0x3F, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0
        ),
        kids=[
            StringFileInfo(
                [
                    StringTable(
                        "040904B0",
                        [
                            StringStruct("CompanyName", "Interview Assistant"),
                            StringStruct("FileDescription", "Interview Assistant"),
                            StringStruct("FileVersion", __version__),
                            StringStruct("InternalName", "InterviewAssistant"),
                            StringStruct("OriginalFilename", "InterviewAssistant.exe"),
                            StringStruct("ProductName", "Interview Assistant"),
                            StringStruct("ProductVersion", __version__),
                        ],
                    )
                ]
            ),
            VarFileInfo([VarStruct("Translation", [1033, 1200])]),
        ],
    )
    path = ROOT / "packaging" / "version_info.txt"
    path.write_text(str(info), encoding="utf-8")
    return path


def run(cmd: list[str], log_name: str | None = None) -> None:
    """Run a command; keep its output in build/<log_name> and show the tail if it fails."""
    print(">", " ".join(cmd))
    if log_name is None:
        subprocess.run(cmd, cwd=ROOT, check=True)
        return
    log = ROOT / "build" / log_name
    log.parent.mkdir(exist_ok=True)
    with log.open("w", encoding="utf-8") as f:
        proc = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT)
    if proc.returncode != 0:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        print(chr(10).join(lines[-30:]))
        raise SystemExit(f"command failed (exit {proc.returncode}); full log: {log}")


def folder_stats(path: Path) -> tuple[int, int]:
    files = [f for f in path.rglob("*") if f.is_file()]
    return len(files), sum(f.stat().st_size for f in files)


def main() -> int:
    t0 = time.time()
    if not (ROOT / "packaging" / "app.ico").exists():
        run([sys.executable, "packaging/make_icon.py"])
    write_version_file()
    if "--no-clean" not in sys.argv:
        for folder in ("dist", "build"):
            for _attempt in range(5):  # a just-closed exe or AV scan can hold files briefly
                shutil.rmtree(ROOT / folder, ignore_errors=True)
                if not (ROOT / folder).exists():
                    break
                time.sleep(2)
            else:
                raise SystemExit(f"could not remove {folder}/ - is the app still running?")
    run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            "dist",
            "--workpath",
            "build",
            "packaging/interview_assistant.spec",
        ],
        log_name="pyinstaller.log",
    )
    if not EXE.exists():
        print("BUILD FAILED: exe not produced")
        return 1
    n, size = folder_stats(DIST)
    rel = EXE.relative_to(ROOT)
    print(f"\nBuilt {rel}  ({n} files, {size / 1e6:.0f} MB) in {time.time() - t0:.0f}s")

    run([sys.executable, "scripts/check_dist_deps.py"])  # fails the build on missing DLLs

    # Self-test the PACKAGED app (imports, DLLs, data files, certificates, keyring, audio).
    out = ROOT / "build" / "selftest.json"
    out.parent.mkdir(exist_ok=True)
    env = {"DATA_DIR": str(ROOT / "build" / "selftest-data")}
    import os

    t1 = time.time()
    try:
        proc = subprocess.run(
            [str(EXE), "--self-test", "--out", str(out)], env={**os.environ, **env}, timeout=180
        )
    except OSError as e:
        # Smart App Control / WDAC refuse to run unsigned executables (WinError 4551).
        print(f"{chr(10)}Could not run the packaged exe: {e}")
        print("Sign the build (see docs/packaging.md) or run this on a PC without that policy.")
        print("Static dependency check passed; packaged self-test SKIPPED.")
        return 3
    if not out.exists():
        print(f"SELF-TEST produced no report (exit {proc.returncode})")
        return 1
    report = json.loads(out.read_text(encoding="utf-8"))
    print(f"\nPackaged self-test ({time.time() - t1:.1f}s): {'OK' if report['ok'] else 'FAILED'}")
    for c in report["checks"]:
        print(f"  {'OK  ' if c['ok'] else 'FAIL'} {c['name']:18} {c['detail'][:100]}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
