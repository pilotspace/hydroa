"""access_requests domain entity (signup-refusal-router TASK.md §3 — FROZEN @ v1,
SECURITY task).

Zero framework imports (backend-architect discipline, mirrors domain_capture's own
domain/entities.py precedent) — a pure dataclass only.

Deliberately "dumber" than DomainClaim (§1 assumption, confirmed at freeze): NO
tenant_id/owner FK. Resolving-and-storing which tenant/owner a domain might belong to
at write time would itself become the enumeration signal this task exists to avoid
(R-sec-2) — a human can grep this table by domain later if/when an owner-facing surface
ships as a follow-on.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class AccessRequest:
    id: uuid.UUID
    email: str
    domain: str
    created_at: datetime
