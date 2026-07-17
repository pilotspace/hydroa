"""CompositeCatalogSource — chains a dynamic CatalogSource with a static seed list.

minimax-catalog-seed TASK.md §3. Implements the CatalogSource Protocol unchanged (structural,
no inheritance needed — Protocol is runtime_checkable-compatible by shape). Lets SyncCatalogUseCase
and SqlAlchemyCatalogRepository.sync_catalog() stay byte-identical: MiniMax's static rows simply
become part of the SAME `models` list passed into one sync_catalog() call, which is what keeps
them out of the deactivation sweep's `notin_(incoming_ids)` blast radius (TASK.md §0 Touches).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from gateway.catalog.domain.entities import CatalogModel
from gateway.catalog.domain.ports import CatalogSource


class CompositeCatalogSource:
    """Chains `primary` (dynamic, e.g. OpenRouter) chat models with a static `static_models` list.

    list_models(): yields every model from `primary.list_models()`, THEN every entry in
    `static_models`, in that order (order asserted by TASK.md §2 scenario, not load-bearing
    elsewhere — sync_catalog()'s upsert loop is order-independent).
    list_embedding_models(): delegates to `primary` ONLY — `static_models` never contributes
    embedding rows (MiniMax has no embeddings surface; out of milestone scope).

    catalog-db-seed TASK.md §3 (FROZEN @ v1), M6 + §1 Assumption #4 (left to BUILD):
    `static_models` is now OPTIONAL (default None -> empty) — decision 1 (DB is the sole
    source of truth) retires the real-app static-seed-list wiring entirely (main.py no
    longer passes this kwarg at all), but the class itself keeps accepting an explicit list
    for minimal blast radius on any other caller/test that still constructs one directly.
    """

    def __init__(
        self, primary: CatalogSource, static_models: list[CatalogModel] | None = None
    ) -> None:
        self._primary = primary
        self._static_models = static_models or []

    async def list_models(self) -> AsyncIterator[CatalogModel]:
        async for model in self._primary.list_models():
            yield model
        for model in self._static_models:
            yield model

    async def list_embedding_models(self) -> AsyncIterator[CatalogModel]:
        async for model in self._primary.list_embedding_models():
            yield model


__all__ = ["CompositeCatalogSource"]
