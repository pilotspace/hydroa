"""Catalog application layer — use cases orchestrating domain ports."""

from __future__ import annotations

import uuid

import structlog

from gateway.catalog.domain.entities import CatalogModel, MarkedUpModel
from gateway.catalog.domain.errors import CatalogSourceUnavailableError
from gateway.catalog.domain.ports import CatalogRepository, CatalogSource

_log = structlog.get_logger(__name__)


class SyncCatalogUseCase:
    """Fetch current model list from CatalogSource and persist via CatalogRepository.

    The repository is responsible for the transactional upsert + snapshot
    + deactivation logic. This use case orchestrates: fetch → store.

    Safety rule (§5): the chat-catalog source fetch failure must propagate as
    CatalogSourceUnavailableError before any write reaches the repository.

    openrouter-embeddings-routing TASK.md §3: also fetches the embeddings
    catalog. A chat-fetch failure is UNCAUGHT (fails the whole sync, unchanged).
    An embeddings-fetch failure is caught HERE ONLY, logged, and degrades to
    `embedding_models=None` — the repository leaves embedding rows untouched
    rather than misreading a fetch failure as "every embedding model is gone".
    """

    def __init__(self, source: CatalogSource, repository: CatalogRepository) -> None:
        self._source = source
        self._repository = repository

    async def execute(self) -> int:
        """Fetch all models from source and sync to catalog.

        Returns the count of models processed.
        Raises CatalogSourceUnavailableError if the chat catalog cannot be reached.
        """
        models: list[CatalogModel] = []
        async for model in self._source.list_models():
            models.append(model)

        embedding_models: list[CatalogModel] | None
        try:
            embedding_models = [m async for m in self._source.list_embedding_models()]
        except CatalogSourceUnavailableError as exc:
            _log.warning("openrouter_embeddings_catalog_sync_degraded", error=str(exc))
            embedding_models = None

        return await self._repository.sync_catalog(models, embedding_models=embedding_models)


class ListModelsForTenantUseCase:
    """Return the active model catalog with per-tenant markup applied."""

    def __init__(self, repository: CatalogRepository) -> None:
        self._repository = repository

    async def execute(
        self, tenant_id: uuid.UUID, *, tier: str | None = None
    ) -> list[MarkedUpModel]:
        """Return marked-up active models for the given tenant.

        Raises CatalogEmptyError when zero active models exist.

        tier (service-tiers TASK.md §3): optional, additive — the JWT-authenticated
        catalog routes (dashboard/admin session auth) carry no per-sk-key tier signal,
        so callers on those paths never pass it (tier stays None ⇒ byte-identical).
        Exists so the seam is directly testable without a live tier context.
        """
        return await self._repository.list_active_models_with_markup(tenant_id, tier=tier)
