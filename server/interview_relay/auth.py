"""Token generation/verification and admin authentication.

Friend tokens are high-entropy random secrets (256 bits), so a plain SHA-256 hash of the token is
enough to store safely (unlike user-chosen passwords, there's no dictionary/brute-force risk to
defend against with a slow hash - the entropy itself is the defense). The raw token is generated
once, its hash is the permanent verifier, and the raw value is never written to the database in a
form that survives past its one-time delivery to the friend (see db.py: pending_token)."""

import hashlib
import hmac
import secrets

TOKEN_PREFIX = "ika_"  # "interview-assistant relay key" - recognizable, greppable in logs/leaks


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_public_id() -> str:
    """An unguessable id a friend uses to poll their own request - not a sequential row id, so
    nobody else can enumerate or guess someone else's request."""
    return secrets.token_urlsafe(16)


def check_admin(username: str, password: str, admin_token: str) -> bool:
    """Constant-time check; a blank admin_token always fails (never "open by default")."""
    if not admin_token:
        return False
    user_ok = hmac.compare_digest(username, "admin")
    pass_ok = hmac.compare_digest(password, admin_token)
    return user_ok and pass_ok
