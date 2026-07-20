"""domain-capture domain entities (domain-capture TASK.md §3 — FROZEN @ v1).

Zero framework imports (backend-architect discipline, §0 Honors) — pure dataclasses only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ClaimStatus(StrEnum):
    PENDING = "pending"
    VERIFIED = "verified"


@dataclass(frozen=True, slots=True)
class DomainClaim:
    id: uuid.UUID
    tenant_id: uuid.UUID
    domain: str
    verification_token: str
    status: ClaimStatus
    created_at: datetime
    verified_at: datetime | None
    expires_at: datetime
    created_by_user_id: uuid.UUID
    # ADDITIVE (domain-verify-notify TASK.md §3 — FROZEN @ v1, SECURITY): opt-in
    # timestamp (None = not opted in) + email-sent timestamp (None = not yet notified).
    notify_requested_at: datetime | None = None
    notified_at: datetime | None = None
    # ADDITIVE (member-verified-recognition TASK.md §3 — FROZEN @ v1, SECURITY): rung-1
    # mailbox-confirmation marker (None = not member-verified). The 3 in-flight code
    # columns (hash/expiry/attempt_count) stay repository-internal — the secret hash NEVER
    # rides this domain entity or any API schema (only member_verified_at is exposed).
    member_verified_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MemberVerifyState:
    """The in-flight member-verify code state read under SELECT … FOR UPDATE
    (member-verified-recognition TASK.md §3 — FROZEN @ v1). REPOSITORY-INTERNAL: carries
    the keyed hash so the verify use-case can constant-time-compare + decide, but is NEVER
    mapped onto an API schema. `code_hash`/`code_expires_at` are None once the code has
    been consumed/expired/invalidated (single-use)."""

    domain: str
    status: ClaimStatus
    member_verified_at: datetime | None
    code_hash: str | None
    code_expires_at: datetime | None
    attempt_count: int
