"""Create three dev-only synthetic Auth users after an explicit, masked local prompt.

No admin role or access grant is assigned. The privileged key is never persisted.
The adjacent created-ids journal is recovery evidence only, never an input to deletion.
A process kill or ambiguous create can require manual reconciliation.

Contracts checked against official createUser docs, auth-js GoTrueAdminApi.ts and
https://raw.githubusercontent.com/supabase/auth/master/internal/api/admin.go
New secret keys use apikey only: https://supabase.com/docs/guides/getting-started/api-keys
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import secrets
import subprocess
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Never, TextIO
from uuid import UUID

import httpx
from scripts.verify_customer_access import Fixtures, load_fixtures

DEV_URL = "https://lctuyznspcufmzzxbjzv.supabase.co"
PUBLIC_CONFIG = Path(__file__).resolve().parents[1] / "src/interview_assistant/assets/auth.json"


class Refused(Exception):
    """Only fixed local failure labels may be used here."""


@dataclass(frozen=True)
class PublicConfig:
    url: str
    publishable_key: str

    def validate(self) -> None:
        if self.url != DEV_URL or not re.fullmatch(
            r"sb_publishable_[A-Za-z0-9_-]+", self.publishable_key
        ):
            raise Refused("public_config_rejected")


def load_public_config() -> PublicConfig:
    # Deliberately no environment, vault, private file or arbitrary config-file option.
    try:
        raw = json.loads(PUBLIC_CONFIG.read_text(encoding="utf-8"))
        config = PublicConfig(raw["url"], raw["publishable_key"])
        config.validate()
        return config
    except (OSError, ValueError, KeyError, TypeError):
        raise Refused("public_config_rejected") from None


def checked_paths(output: Path) -> tuple[Path, Path]:
    try:
        output = output.parent.resolve(strict=True) / output.name
        if output.suffix != ".json":
            raise ValueError
        journal = output.with_name(output.stem + ".created-ids.jsonl")
        for path in (output, journal):
            if path.exists() or path.is_symlink():
                raise ValueError
            ignored = subprocess.run(
                ["git", "check-ignore", "--quiet", "--", path.name],
                cwd=path.parent,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if ignored.returncode != 0:
                raise ValueError
        return output, journal
    except (OSError, ValueError, subprocess.SubprocessError):
        raise Refused("output_requires_new_ignored_paths") from None


def prompt_secret() -> str:
    # No terminal/getpass fallback: a tool session may not provide a non-echoing TTY.
    from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QLineEdit

    application = QApplication.instance() or QApplication(["Dev fixture provisioning"])
    if QApplication.platformName().lower() in {"offscreen", "minimal"}:
        raise Refused("native_prompt_requires_visible_platform")
    dialog = QInputDialog()
    dialog.setWindowTitle("Development test accounts only")
    dialog.setLabelText(
        "Paste the Supabase sb_secret_ key for lctuyznspcufmzzxbjzv.\n"
        "Creates 3 synthetic Auth users. No admin role or access grant is assigned.\n"
        "The key stays in memory and is not saved."
    )
    dialog.setTextEchoMode(QLineEdit.EchoMode.Password)
    try:
        if dialog.exec() != QDialog.DialogCode.Accepted:
            raise Refused("secret_prompt_cancelled")
        return dialog.textValue().strip()
    finally:
        dialog.setTextValue("")
        dialog.deleteLater()
        application.processEvents()


@dataclass
class Result:
    created_user_ids: dict[str, str] = field(default_factory=dict)
    rolled_back_ids: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    cleanup_failures: list[str] = field(default_factory=list)
    fixtures_saved: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "passed": self.fixtures_saved and not self.failures and not self.cleanup_failures,
            "fixtures_saved": self.fixtures_saved,
            "admin_role_assigned": False,
            "created_user_ids": self.created_user_ids,
            "rolled_back_ids": self.rolled_back_ids,
            "failures": self.failures,
            "cleanup_ok": not self.cleanup_failures,
            "cleanup_failures": self.cleanup_failures,
        }


def save_fixtures(file: TextIO, fixtures: dict[str, Any]) -> None:
    json.dump(fixtures, file, indent=2)
    file.flush()
    os.fsync(file.fileno())


def rollback(client: httpx.Client, headers: dict[str, str], result: Result) -> None:
    for uid in reversed(list(result.created_user_ids.values())):
        try:
            response = client.delete(
                f"{DEV_URL}/auth/v1/admin/users/{uid}", headers=headers, follow_redirects=False
            )
            if response.status_code not in {200, 204}:
                raise Refused("rollback_delete_failed")
            proof = client.get(
                f"{DEV_URL}/auth/v1/admin/users/{uid}", headers=headers, follow_redirects=False
            )
            if proof.status_code != 404 or proof.json().get("code") != "user_not_found":
                raise Refused("rollback_not_confirmed")
            result.rolled_back_ids.append(uid)
        except (Exception, KeyboardInterrupt):
            result.cleanup_failures.append("rollback_unconfirmed:" + uid)


def provision(
    config: PublicConfig,
    output: Path,
    client: httpx.Client,
    *,
    secret_prompt: Callable[[], str] = prompt_secret,
) -> Result:
    result = Result()
    reserved: list[Path] = []
    headers: dict[str, str] = {}
    unknown_create = False
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        config.validate()
        output, journal_path = checked_paths(output)
        with ExitStack() as stack:
            # Reserve both files before asking for a privileged key or making any request.
            file = stack.enter_context(output.open("x", encoding="utf-8"))
            reserved.append(output)
            journal = stack.enter_context(journal_path.open("x", encoding="utf-8"))
            reserved.append(journal_path)
            secret = secret_prompt()
            if not re.fullmatch(r"sb_secret_[A-Za-z0-9_-]{16,256}", secret):
                raise Refused("secret_key_format_rejected")
            headers = {"apikey": secret}
            secret = ""
            nonce = secrets.token_hex(16)
            identities = []
            for label in ("admin", "A", "B"):
                email = f"chandoo-access-test-{label.lower()}-{nonce}@example.test"
                password = secrets.token_urlsafe(32) + "Aa1!"
                unknown_create = True
                response = client.post(
                    f"{DEV_URL}/auth/v1/admin/users",
                    headers=headers,
                    follow_redirects=False,
                    json={"email": email, "password": password, "email_confirm": True},
                )
                if 400 <= response.status_code < 500 and response.status_code != 408:
                    unknown_create = False
                    raise Refused("auth_create_rejected")
                if response.status_code not in {200, 201}:
                    raise Refused("auth_create_unconfirmed")
                user = response.json()
                uid = str(UUID(user["id"]))
                if (
                    user.get("email") != email
                    or UUID(uid).int == 0
                    or uid in result.created_user_ids.values()
                ):
                    raise Refused("auth_create_identity_unconfirmed")
                result.created_user_ids[label] = uid
                unknown_create = False
                # Journal only validated UUIDs, immediately flushed for manual crash recovery.
                journal.write(json.dumps(uid) + "\n")
                journal.flush()
                os.fsync(journal.fileno())
                if not user.get("email_confirmed_at"):
                    raise Refused("auth_email_confirmation_missing")
                identities.append({"user_id": uid, "email": email, "password": password})
            payload = {
                "project_url": DEV_URL,
                "publishable_key": config.publishable_key,
                "admin": identities[0],
                "customers": identities[1:],
            }
            expected = Fixtures.parse(payload)
            save_fixtures(file, payload)
        # Read only this invocation's new file through the actual acceptance loader.
        if load_fixtures(output) != expected:
            raise Refused("fixture_contract_verification_failed")
        result.fixtures_saved = True
    except Refused as error:
        result.failures.append(str(error))
    except KeyboardInterrupt:
        result.failures.append("provision_cancelled")
    except Exception:
        result.failures.append("provision_failed")
    finally:
        if not result.fixtures_saved:
            if unknown_create:
                result.cleanup_failures.append("unknown_create_requires_manual_reconciliation")
            rollback(client, headers, result)
            # Never remove an existing file; only reservations created by this invocation.
            for path in reserved[:1] + (reserved[1:] if not result.created_user_ids else []):
                try:
                    path.unlink()
                except OSError:
                    result.cleanup_failures.append("reserved_file_removal_failed")
        headers.clear()
        logging.disable(previous_logging)
    return result


class Arguments(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        # argparse's normal error embeds unknown arguments, which may contain a pasted key.
        raise Refused("invalid_arguments")


def main(argv: list[str] | None = None) -> int:
    parser = Arguments(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--fixtures", type=Path, required=True)
    try:
        args = parser.parse_args(argv)
        if not args.live:
            raise Refused("explicit_live_opt_in_required")
        config = load_public_config()
        with httpx.Client(timeout=12, follow_redirects=False, trust_env=False) as client:
            result = provision(config, args.fixtures, client, secret_prompt=prompt_secret).public()
    except Refused as error:
        result = {"passed": False, "failures": [str(error)]}
    except (Exception, KeyboardInterrupt):
        result = {"passed": False, "failures": ["provision_stopped_check_created_ids_journal"]}
    print(json.dumps(result))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
