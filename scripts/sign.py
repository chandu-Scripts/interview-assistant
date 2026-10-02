"""Code-sign the packaged app (and installer) with signtool.

Unsigned executables are blocked by Windows Smart App Control / WDAC on many PCs, so a release
build must be signed with a certificate that Windows trusts (an OV/EV code-signing certificate or
Azure Trusted Signing). A self-signed certificate does NOT satisfy Smart App Control.

Configure with environment variables (nothing is stored in the repo):
  SIGN_PFX / SIGN_PASSWORD    a .pfx file and its password, or
  SIGN_THUMBPRINT             SHA1 thumbprint of a certificate in the Windows store
  SIGN_TIMESTAMP_URL          default http://timestamp.digicert.com

Usage: python scripts/sign.py [path ...]     (default: dist/InterviewAssistant + dist/installer)
NOTE: written against signtool's documented CLI; not exercised here (no certificate available)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = {".exe", ".dll", ".pyd"}
BATCH = 25


def find_signtool() -> str | None:
    found = shutil.which("signtool")
    if found:
        return found
    kits = Path(r"C:\Program Files (x86)\Windows Kits\10\bin")
    candidates = sorted(kits.glob("*/x64/signtool.exe"), reverse=True) if kits.exists() else []
    return str(candidates[0]) if candidates else None


def cert_args() -> list[str] | None:
    if os.environ.get("SIGN_PFX"):
        args = ["/f", os.environ["SIGN_PFX"]]
        if os.environ.get("SIGN_PASSWORD"):
            args += ["/p", os.environ["SIGN_PASSWORD"]]
        return args
    if os.environ.get("SIGN_THUMBPRINT"):
        return ["/sha1", os.environ["SIGN_THUMBPRINT"]]
    return None


def collect(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        if p.is_file() and p.suffix.lower() in SUFFIXES:
            files.append(p)
        elif p.is_dir():
            files += [f for f in p.rglob("*") if f.is_file() and f.suffix.lower() in SUFFIXES]
    return files


def main() -> int:
    signtool = find_signtool()
    cert = cert_args()
    if signtool is None:
        print("signtool not found (install the Windows SDK).")
        return 4
    if cert is None:
        print("No certificate configured: set SIGN_PFX (+SIGN_PASSWORD) or SIGN_THUMBPRINT.")
        return 5
    targets = [Path(a) for a in sys.argv[1:]] or [
        ROOT / "dist" / "InterviewAssistant",
        ROOT / "dist" / "installer",
    ]
    files = collect(targets)
    if not files:
        print("nothing to sign")
        return 1
    ts = os.environ.get("SIGN_TIMESTAMP_URL", "http://timestamp.digicert.com")
    for i in range(0, len(files), BATCH):
        batch = [str(f) for f in files[i : i + BATCH]]
        cmd = [signtool, "sign", *cert, "/fd", "sha256", "/tr", ts, "/td", "sha256", *batch]
        code = subprocess.run(cmd).returncode
        if code != 0:
            print(f"signtool failed (exit {code})")
            return code
    print(f"signed {len(files)} files")
    verify = subprocess.run([signtool, "verify", "/pa", str(files[0])]).returncode
    return verify


if __name__ == "__main__":
    sys.exit(main())
