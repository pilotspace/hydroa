"""Suite-local fixtures for catalog-refresh-scheduler (catalog-celery-refresh v2) — §4.

Reuses the global app/db_session fixtures (tests/conftest.py). Provides an in-memory
CatalogSource stub (identical shape to the catalog_sync_trigger suite's) so the scheduler
can be exercised against the test DB with no outbound HTTP.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FakeCatalogModel:
    """Mirrors the CatalogModel value object the domain port returns."""

    id: str
    name: str
    context_length: int | None
    prompt_usd_per_token: float
    completion_usd_per_token: float
    modality: str = "chat"
    provider: str = "openrouter"
    input_modalities: str = "text"
    cached_input_usd_per_token: float | None = None
    audio_prompt_usd_per_token: float | None = None
    audio_completion_usd_per_token: float | None = None
    audio_cached_usd_per_token: float | None = None
    region: str = "global"
    cache_creation_usd_per_token: float | None = None
    pricing_unit: str = "per_token"
    unit_usd_per_unit: float | None = None


class FakeCatalogSource:
    """In-memory stub implementing the CatalogSource Protocol port."""

    def __init__(
        self,
        models: list[FakeCatalogModel] | None = None,
        *,
        raise_unavailable: bool = False,
    ) -> None:
        self.models = models or []
        self.raise_unavailable = raise_unavailable

    async def list_models(self) -> AsyncIterator[FakeCatalogModel]:
        if self.raise_unavailable:
            from gateway.catalog.domain.errors import CatalogSourceUnavailableError

            raise CatalogSourceUnavailableError("fake upstream unavailable")
        for model in self.models:
            yield model

    async def list_embedding_models(self) -> AsyncIterator[FakeCatalogModel]:
        return
        yield  # pragma: no cover — makes this an async generator
