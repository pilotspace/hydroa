"""Infrastructure adapter: ModelChecker → queries catalog ORM."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.infrastructure.orm import ModelRow


class SqlAlchemyModelChecker:
    """Checks model availability by querying the models table.

    A new instance is created per-request (session-scoped).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def is_active(self, model_id: str) -> bool:
        """Return True iff models.id = model_id AND models.active = true."""
        row = (
            await self._session.execute(select(ModelRow.active).where(ModelRow.id == model_id))
        ).scalar_one_or_none()
        if row is None:
            return False
        return bool(row)
