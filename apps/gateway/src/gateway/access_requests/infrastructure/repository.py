"""SqlAlchemyAccessRequestRepository (signup-refusal-router TASK.md §3 — FROZEN @ v1).

ONE method, ONE INSERT — no SELECT of any kind (M5/M6/the binding SAFETY RULE: this
repository never reads any tenant/user/domain-claim table, so it cannot itself become
part of an enumeration oracle).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from gateway.access_requests.domain.entities import AccessRequest
from gateway.access_requests.infrastructure.orm import AccessRequestRow
from gateway.core.ids import uuid7


class SqlAlchemyAccessRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, *, email: str, domain: str, created_at: datetime) -> AccessRequest:
        # Build the id/entity from LOCAL values, never by re-reading `row.*` after
        # commit() — the session's default expire_on_commit would mark the just-
        # flushed instance's attributes expired, and a subsequent attribute access
        # would trigger an implicit lazy-refresh SELECT that is unsafe to await
        # outside an active greenlet context (design-for-failure: no ambient IO).
        row_id = uuid7()
        row = AccessRequestRow(id=row_id, email=email, domain=domain, created_at=created_at)
        self._session.add(row)
        await self._session.commit()
        return AccessRequest(id=row_id, email=email, domain=domain, created_at=created_at)
