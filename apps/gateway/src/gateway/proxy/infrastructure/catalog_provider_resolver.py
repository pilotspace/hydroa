"""Catalog-backed provider resolver — provider-chat-dispatch TASK.md §3 (FROZEN @ v1).

Holds an in-memory model_id → provider map, populated at startup from the catalog
DB and refreshed on /internal/catalog/sync. All reads are in-memory; the DB is
only touched during refresh() — NEVER on the chat hot path.

Fail-safe contract:
  - refresh() catches ANY loader exception, keeps the last-good map, and logs a
    warning. It MUST NOT raise.
  - provider_for() reads the in-memory map only. Returns "openrouter" for any
    unknown / unset model_id. NEVER raises.

Additive extension @ chat-modality-guard TASK.md §3:
  - OPTIONAL `modality_loader` ctor param + `modality_for()` method — a second,
    independent cached map (model_id -> modality), refreshed alongside the
    provider map. Backward compatible: omitting `modality_loader` (the frozen
    provider-chat-dispatch suite's construction) leaves `modality_for()`
    returning None for everything — fail-open, byte-identical.
  - refresh() now serializes concurrent calls via an internal asyncio.Lock — two
    overlapping refresh() calls (e.g. overlapping /internal/catalog/sync retries)
    could otherwise interleave main.py's paired provider/modality loader calls,
    leaving `_map` and `_modality_map` populated from two DIFFERENT refresh
    cycles (refute-read finding, chat-modality-guard VERIFY). Byte-identical for
    the common case (a single in-flight refresh at a time).
"""

from __future__ import annotations

import asyncio
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
      modality_loader — OPTIONAL async callable that returns a fresh
               {model_id: modality} dict. None (default) -> modality_for() always
               returns None (fail-open); byte-identical to before this extension.
    """

    def __init__(
        self,
        *,
        loader: Callable[[], Awaitable[dict[str, str]]],
        modality_loader: Callable[[], Awaitable[dict[str, str]]] | None = None,
    ) -> None:
        self._loader = loader
        self._modality_loader = modality_loader
        self._map: dict[str, str] = {}
        self._modality_map: dict[str, str] = {}
        # Serializes refresh() so a paired loader/modality_loader call (main.py's
        # provider+modality query) always completes as ONE atomic cycle — never
        # interleaved with another concurrent refresh() (chat-modality-guard fix).
        self._refresh_lock = asyncio.Lock()

    async def refresh(self) -> None:
        """Reload the model→provider map (and, if wired, the model→modality map).

        Serialized: concurrent refresh() calls run one at a time, so a paired
        loader/modality_loader read is never torn by an overlapping call.
        Fail-safe: any exception from either loader keeps that loader's last-good
        map and logs a warning. MUST NOT raise.
        """
        async with self._refresh_lock:
            try:
                self._map = await self._loader()
            except Exception:  # intentional broad catch; fail-safe contract (never raises)
                _log.warning(
                    "provider_resolver_refresh_failed",
                    extra={"event": "catalog_provider_resolver.refresh.failed"},
                )
                # Keep the last-good map — do NOT clear.
            if self._modality_loader is not None:
                try:
                    self._modality_map = await self._modality_loader()
                except Exception:  # intentional broad catch; fail-safe contract (never raises)
                    _log.warning(
                        "provider_resolver_modality_refresh_failed",
                        extra={"event": "catalog_provider_resolver.modality_refresh.failed"},
                    )
                    # Keep the last-good modality map — do NOT clear.

    async def provider_for(self, model_id: str) -> str:
        """Return the catalog provider for model_id, or 'openrouter' for unknown.

        Reads from the in-memory map only — NO DB on this hot path.
        NEVER raises.
        """
        return self._map.get(model_id, "openrouter")

    async def modality_for(self, model_id: str) -> str | None:
        """Return the cached catalog modality for model_id, or None if unknown/uncached.

        Reads from the in-memory map only — NO DB on this hot path. NEVER raises.
        A None result signals the caller to fail-open (treat as compatible) — either
        no modality_loader was wired, or the model isn't in the last-refreshed map.
        """
        return self._modality_map.get(model_id)


__all__ = ["CatalogProviderResolver"]
