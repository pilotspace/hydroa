"""access_requests domain ports (signup-refusal-router TASK.md §3 — FROZEN @ v1).

A single Protocol, one method — mirrors domain_capture's own domain/ports.py precedent
at the much smaller scale this bounded context needs (§0 GROUND: "one entity, one use
case, one repository, one rate limiter, one router").
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from gateway.access_requests.domain.entities import AccessRequest


class AccessRequestRepository(Protocol):
    async def create(self, *, email: str, domain: str, created_at: datetime) -> AccessRequest: ...
