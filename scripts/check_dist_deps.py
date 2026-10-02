"""Static check of the packaged app: every DLL imported by any bundled .dll/.pyd/.exe must be
either in the bundle or provided by Windows. Works even where unsigned executables are blocked
from running (e.g. Smart App Control), so it can gate a build without launching it.

Usage: python scripts/check_dist_deps.py [dist/InterviewAssistant]"""

import sys
from pathlib import Path

import pefile

ROOT = Path(__file__).resolve().parents[1]
DIST = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "InterviewAssistant"
SYSTEM_DIRS = [Path(r"C:\Windows\System32"), Path(r"C:\Windows\SysWOW64")]
# Delay-loaded / optional imports that are legitimately absent (loaded only on demand).
OPTIONAL = {"opengl32sw.dll"}


def bundled_names(root: Path) -> set[str]:
    return {p.name.lower() for p in root.rglob("*") if p.suffix.lower() in {".dll", ".pyd"}}


def is_windows_provided(name: str) -> bool:
    low = name.lower()
    if low.startswith(("api-ms-win-", "ext-ms-win-")):  # API sets are virtual
        return True
    return any((d / name).exists() for d in SYSTEM_DIRS)


def imports_of(path: Path) -> tuple[set[str], set[str]]:
    pe = pefile.PE(str(path), fast_load=True)
    pe.parse_data_directories(
        directories=[
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
            pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"],
        ]
    )
    hard = {e.dll.decode().lower() for e in getattr(pe, "DIRECTORY_ENTRY_IMPORT", [])}
    delayed = {e.dll.decode().lower() for e in getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", [])}
    pe.close()
    return hard, delayed


def main() -> int:
    if not DIST.exists():
        print(f"no build at {DIST}; run scripts/build.py first")
        return 2
    have = bundled_names(DIST)
    missing: dict[str, set[str]] = {}
    scanned = 0
    for p in DIST.rglob("*"):
        if p.suffix.lower() not in {".dll", ".pyd", ".exe"}:
            continue
        try:
            hard, delayed = imports_of(p)
        except pefile.PEFormatError:
            continue
        scanned += 1
        for dep in hard | delayed:
            if dep in have or dep in OPTIONAL or is_windows_provided(dep):
                continue
            kind = "delay-load" if dep in delayed and dep not in hard else "REQUIRED"
            missing.setdefault(f"{dep} ({kind})", set()).add(str(p.relative_to(DIST)))
    where = DIST.relative_to(ROOT) if DIST.is_relative_to(ROOT) else DIST
    print(f"scanned {scanned} binaries in {where}")
    hard_missing = {k: v for k, v in missing.items() if "REQUIRED" in k}
    for k, users in sorted(missing.items()):
        print(f"  {'MISSING' if 'REQUIRED' in k else 'note   '} {k}  <- {sorted(users)[:3]}")
    if hard_missing:
        print("FAIL: required DLLs are missing from the bundle")
        return 1
    print("OK: every hard-linked DLL is bundled or provided by Windows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
