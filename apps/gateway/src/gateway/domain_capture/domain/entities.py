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
