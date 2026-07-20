"""Keyed hash-at-rest + constant-time compare for the 6-digit member-verify code
(member-verified-recognition TASK.md §3/§5 — FROZEN @ v1, SECURITY; DECIDED Option A).

Option A (Tin-ratified at freeze): store the code ONLY as
    HMAC-SHA256(code, key = HMAC(jwt_secret, b"member-verify-code"))    (hex)
— a domain-separated pepper off the EXISTING required, dev-default-guarded `jwt_secret`,
so NO new config secret is introduced and a DB-only compromise cannot reverse a code.
Compared with constant-time `hmac.compare_digest`. Never store plaintext; the 3 code
columns are cleared on success/expiry/cap (single-use).

Pure, IO-free, stdlib-only (hmac/hashlib/secrets — no new dependency).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_HMAC_LABEL = b"member-verify-code"


def _derived_key(jwt_secret: str) -> bytes:
    """Domain-separated pepper key = HMAC(jwt_secret, "member-verify-code")."""
    return hmac.new(jwt_secret.encode(), _HMAC_LABEL, hashlib.sha256).digest()


def hash_member_verify_code(code: str, jwt_secret: str) -> str:
    """HMAC-SHA256(code, key=HMAC(jwt_secret, "member-verify-code")), hex-encoded (Option A)."""
    return hmac.new(_derived_key(jwt_secret), code.encode(), hashlib.sha256).hexdigest()


def verify_member_verify_code(*, code: str, code_hash: str, jwt_secret: str) -> bool:
    """Constant-time compare of a submitted code against the stored keyed hash."""
    candidate = hash_member_verify_code(code, jwt_secret)
    return hmac.compare_digest(candidate, code_hash)


def generate_member_verify_code() -> str:
    """A cryptographically-random, zero-padded 6-digit code (from `secrets`)."""
    return f"{secrets.randbelow(1_000_000):06d}"
