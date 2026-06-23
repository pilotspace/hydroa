"""Repository for the operator-wide routing config singleton (v32 routing-config-store §3).

Takes a session_factory (async_sessionmaker) and opens its own session per call — the
boot-time / standalone pattern used by the lifespan startup (no request-scoped session there).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from gateway.proxy.infrastructure.routing_config_orm import RoutingConfigRow

# The fixed singleton primary key (mirrors the id IS TRUE CHECK).
_SINGLETON_ID = True


class RoutingConfigRepository:
    """get()/upsert() over the single routing_config row."""

    def __init__(self, session_factory: async_sessionmaker[Any]) -> None:
        self._session_factory = session_factory

    async def get(self) -> dict[str, Any] | None:
        """Return the stored config doc, or None when unset."""
        async with self._session_factory() as session:
            row = (
                await session.execute(
                    select(RoutingConfigRow.config).where(RoutingConfigRow.id == _SINGLETON_ID)
                )
            ).fetchone()
            return dict(row[0]) if row is not None and row[0] is not None else None

    async def upsert(self, config: dict[str, Any]) -> None:
        """Persist (replace) the single routing config row."""
        async with self._session_factory() as session:
            stmt = pg_insert(RoutingConfigRow).values(id=_SINGLETON_ID, config=config)
            stmt = stmt.on_conflict_do_update(
                index_elements=[RoutingConfigRow.id], set_={"config": config}
            )
            await session.execute(stmt)
            await session.commit()
