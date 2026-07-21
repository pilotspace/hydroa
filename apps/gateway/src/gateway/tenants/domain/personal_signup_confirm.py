"""generate_confirm_token — the pending-personal-signup confirm token generator
(scoped-self-serve-signup TASK.md §3, FROZEN @ v1, SECURITY). Pure, IO-free.

Mirrors domain_capture/application/create_claim_use_case.py's own _TOKEN_BYTES=32 CSPRNG
token generation (256-bit entropy) — NOT member_verify_code.py's low-entropy 6-digit +
HMAC-pepper scheme (see Ground R-sec-4: a 256-bit link-style token needs no pepper and no
attempt-cap; that heavier scheme defends against a threat this token doesn't have).
"""

from __future__ import annotations

import secrets

_TOKEN_BYTES = 32


def generate_confirm_token() -> str:
    """Return a fresh 256-bit CSPRNG URL-safe confirm token (~43 chars)."""
    return secrets.token_urlsafe(_TOKEN_BYTES)
