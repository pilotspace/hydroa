"""Catalog domain ports — typing.Protocol only, zero framework imports."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Protocol

from gateway.catalog.domain.entities import CatalogModel, MarkedUpModel


class CatalogSource(Protocol):
    """Port for fetching the upstream model catalog.

    Implementations must raise CatalogSourceUnavailableError on any network
    or upstream failure — never propagate raw httpx/aiohttp exceptions.
    """

    def list_models(self) -> AsyncIterator[CatalogModel]:
        """Stream all available models from the upstream source."""
        ...


class CatalogRepository(Protocol):
    """Port for persisting and querying the model catalog."""

    async def sync_catalog(self, models: list[CatalogModel]) -> int:
        """Upsert model rows, append pricing snapshots for price changes,
        mark absent models inactive — all in ONE transaction.

        Returns the number of models processed (active from source count).
        """
        ...

    async def list_active_models_with_markup(self, tenant_id: uuid.UUID) -> list[MarkedUpModel]:
        """Return active models with the tenant's markup_pct applied.

        Single joined query — no N+1.
        Raises CatalogEmptyError when zero active models exist.
        """
        ...

    async def get_latest_snapshot_prices(self, model_id: str) -> tuple[Decimal, Decimal] | None:
        """Return (prompt_usd_per_token, completion_usd_per_token) from the
        most recent snapshot for model_id, or None if no snapshot exists.
        """
        ...
