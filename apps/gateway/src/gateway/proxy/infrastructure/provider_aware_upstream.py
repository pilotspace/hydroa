"""Provider-aware chat dispatch — provider-chat-dispatch TASK.md §3 (FROZEN @ v1).

Wraps a map of CompletionUpstream adapters and a ProviderResolver to dispatch
each chat request to the correct upstream based on the catalog provider of the
served model. provider="openrouter" (default/unknown) is byte-identical to v8:
the same OpenRouterCompletionUpstream is selected, no extra behavior.

Selection is FAIL-SAFE: unknown provider / provider absent from the adapter map
falls back to the "openrouter" adapter. This layer NEVER 500s on its own.
It adds NO retry, rewrite, or billing of its own — selection only.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from gateway.proxy.domain.ports import CompletionUpstream, ProviderResolver


class ProviderAwareCompletionUpstream:
    """Dispatch each chat request to the correct adapter by resolved provider.

    Implements the CompletionUpstream Protocol (complete + stream).

    Contract (FROZEN @ v1):
      - ctor: adapters: dict[str, CompletionUpstream], resolver: ProviderResolver,
        default_provider: str = "openrouter"
      - complete: resolves provider via await resolver.provider_for(model_id),
        selects adapter (falls back to default if absent), delegates.
      - stream: same selection inside the inner async generator (stream() is a
        sync def returning an AsyncIterator, mirroring OpenRouterCompletionUpstream).
      - No retry, rewrite, or billing — selection only.
    """

    def __init__(
        self,
        *,
        adapters: dict[str, CompletionUpstream],
        resolver: ProviderResolver,
        default_provider: str = "openrouter",
    ) -> None:
        self._adapters = adapters
        self._resolver = resolver
        self._default = default_provider

    async def complete(self, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        """Resolve provider and delegate to the matching adapter.

        Fail-safe: unknown provider or provider absent from the map → default adapter.
        """
        model = str(payload.get("model", ""))
        provider = await self._resolver.provider_for(model)
        adapter = self._adapters.get(provider) or self._adapters[self._default]
        return await adapter.complete(payload)

    def stream(self, payload: dict[str, object]) -> AsyncIterator[bytes]:
        """Return an async generator that resolves the provider on first iteration.

        Resolution happens INSIDE the inner generator so that stream() stays a
        sync def returning AsyncIterator — mirroring openrouter_upstream.py idiom.
        Fail-safe: unknown/absent provider → default adapter.
        """
        resolver = self._resolver
        adapters = self._adapters
        default = self._default

        async def _gen() -> AsyncIterator[bytes]:
            model = str(payload.get("model", ""))
            provider = await resolver.provider_for(model)
            adapter = adapters.get(provider) or adapters[default]
            async for chunk in adapter.stream(payload):
                yield chunk

        return _gen()


__all__ = ["ProviderAwareCompletionUpstream"]
