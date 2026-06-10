"""Catalog application layer — use cases orchestrating domain ports."""

from __future__ import annotations

import uuid

from gateway.catalog.domain.entities import CatalogModel, MarkedUpModel
from gateway.catalog.domain.ports import CatalogRepository, CatalogSource


class SyncCatalogUseCase:
    """Fetch current model list from CatalogSource and persist via CatalogRepository.

    The repository is responsible for the transactional upsert + snapshot
    + deactivation logic. This use case orchestrates: fetch → store.

    Safety rule (§5): source fetch failure must propagate as
    CatalogSourceUnavailableError before any write reaches the repository.
    """

    def __init__(self, source: CatalogSource, repository: CatalogRepository) -> None:
        self._source = source
        self._repository = repository

    async def execute(self) -> int:
        """Fetch all models from source and sync to catalog.

        Returns the count of models processed.
        Raises CatalogSourceUnavailableError if the upstream cannot be reached.
        """
        models: list[CatalogModel] = []
        async for model in self._source.list_models():
            models.append(model)
        return await self._repository.sync_catalog(models)


class ListModelsForTenantUseCase:
    """Return the active model catalog with per-tenant markup applied."""

    def __init__(self, repository: CatalogRepository) -> None:
        self._repository = repository

    async def execute(self, tenant_id: uuid.UUID) -> list[MarkedUpModel]:
        """Return marked-up active models for the given tenant.

        Raises CatalogEmptyError when zero active models exist.
        """
        return await self._repository.list_active_models_with_markup(tenant_id)
