"""Catalog-backed provider resolver — provider-chat-dispatch TASK.md §3 (FROZEN @ v1).

Holds an in-memory model_id → provider map, populated at startup from the catalog
DB and refreshed on /internal/catalog/sync. All reads are in-memory; the DB is
only touched during refresh() — NEVER on the chat hot path.

Fail-safe contract:
  - refresh() catches ANY loader exception, keeps the last-good map, and logs a
    warning. It MUST NOT raise.
  - provider_for() reads the in-memory map only. Returns "openrouter" for any
    unknown / unset model_id. NEVER raises.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

_log = logging.getLogger(__name__)


class CatalogProviderResolver:
    """Cached model_id→provider map backed by the catalog database.

    Implements the ProviderResolver Protocol (TASK.md §3):
      - async provider_for(model_id: str) -> str
      - async refresh() -> None

    ctor:
      loader — async callable that returns a fresh {model_id: provider} dict.
               Injected at the composition root (closure over app.state.sessionmaker).
    """

    def __init__(
        self,
        *,
        loader: Callable[[], Awaitable[dict[str, str]]],
    ) -> None:
        self._loader = loader
        self._map: dict[str, str] = {}

    async def refresh(self) -> None:
        """Reload the model→provider map from the catalog.

        Fail-safe: any exception from the loader keeps the last-good map and
        logs a warning. MUST NOT raise.
        """
        try:
            self._map = await self._loader()
        except Exception:  # intentional broad catch; fail-safe contract (never raises)
            _log.warning(
                "provider_resolver_refresh_failed",
                extra={"event": "catalog_provider_resolver.refresh.failed"},
            )
            # Keep the last-good map — do NOT clear.

    async def provider_for(self, model_id: str) -> str:
        """Return the catalog provider for model_id, or 'openrouter' for unknown.

        Reads from the in-memory map only — NO DB on this hot path.
        NEVER raises.
        """
        return self._map.get(model_id, "openrouter")


__all__ = ["CatalogProviderResolver"]
