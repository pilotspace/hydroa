"""SQLAlchemy implementation of the InputModalityLookup port.

unsupported-input-guard TASK.md §3 — reads input_modalities from the models table
and parses the stored CSV via the task-1 parse_input_modalities helper.

Fail-open contract (design-for-failure):
  - Unknown model id (no active row) → returns None.  The caller must allow (not 4xx).
  - The caller (guard helper) is responsible for interpreting None as fail-open.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.domain.entities import parse_input_modalities
from gateway.catalog.infrastructure.orm import ModelRow


class SqlAlchemyInputModalityLookup:
    """Read input_modalities for a model from the catalog (models table).

    Executes ``SELECT input_modalities FROM models WHERE id = :id AND active``.
    Returns a frozenset of modality tokens parsed via ``parse_input_modalities``,
    or ``None`` when no active row exists for the given id.

    Constructed per-request (same lifetime as the AsyncSession).
    """

    __slots__ = ("_session",)

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, model_id: str) -> frozenset[str] | None:
        """Return allowed input modalities for model_id, or None on missing/inactive row.

        None signals the caller to fail-open (no 4xx on absent capability data).
        """
        stmt = select(ModelRow.input_modalities).where(
            ModelRow.id == model_id,
            ModelRow.active.is_(True),
        )
        result = await self._session.execute(stmt)
        row = result.one_or_none()
        if row is None:
            return None
        return parse_input_modalities(row.input_modalities)
