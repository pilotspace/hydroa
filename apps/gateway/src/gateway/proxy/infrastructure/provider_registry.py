"""ProviderRegistry and select_provider — provider-seam TASK.md §3.

An explicit dict[str, UpstreamProvider] wrapper with a typed get() method.
select_provider() performs a deterministic dict lookup — NEVER uses
inspect.signature or hasattr dispatch (foundation rule TYPED_EXTRAS_NO_DISPATCH).

Placement: proxy/infrastructure/ per §5 BUILD file list.
"""

from __future__ import annotations

from gateway.core.error_catalog import PROVIDER_UNAVAILABLE
from gateway.proxy.domain.ports import UpstreamProvider


class ProviderRegistry:
    """Typed wrapper around dict[str, UpstreamProvider].

    Constructed once in create_app() and stored on app.state.provider_registry.
    Always contains "openrouter"; contains "openai" only when openai_api_key is set.
    """

    def __init__(self, providers: dict[str, UpstreamProvider]) -> None:
        self._providers: dict[str, UpstreamProvider] = dict(providers)

    def get(self, provider_name: str) -> UpstreamProvider | None:
        """Return the adapter for provider_name, or None if not registered."""
        return self._providers.get(provider_name)


def select_provider(
    modality: str,
    provider: str,
    registry: ProviderRegistry,
) -> UpstreamProvider:
    """Return the UpstreamProvider adapter for the given provider name.

    Raises ERR_PROVIDER_UNAVAILABLE (503) when the provider is absent from
    the registry (e.g. openai_api_key is empty).

    Args:
        modality: The catalog model's modality (e.g. "embedding", "image").
                  Passed for future routing strategies; not used in v7
                  (one deterministic provider per name).
        provider: The catalog model's provider name (registry key).
        registry: The ProviderRegistry built by create_app().

    Returns:
        The UpstreamProvider for the given provider name.

    Raises:
        ProblemError: status=503, code="ERR_PROVIDER_UNAVAILABLE" when absent.
    """
    # Explicit dict lookup — no inspect.signature, no hasattr dispatch.
    adapter = registry.get(provider)
    if adapter is None:
        raise PROVIDER_UNAVAILABLE.exc(provider=provider)
    return adapter


__all__ = ["ProviderRegistry", "select_provider"]
