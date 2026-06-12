"""OpenRouterUpstreamFacade — wraps CompletionUpstream to satisfy UpstreamProvider.

provider-seam TASK.md §3 CONTRACT:
  post_json     → delegates to completion_upstream.complete(payload)
  stream_bytes  → delegates to completion_upstream.stream(payload)
  post_multipart → raises UpstreamUnavailableError (OpenRouter does not support multipart;
                   a loud error on misuse is correct — chat never calls post_multipart)

The facade is stored in app.state.provider_registry under the key "openrouter" so
non-chat endpoint tasks can route through the registry without special-casing chat.
The v6 chat path continues to use app.state.completion_upstream directly — the facade
is never consulted by the chat route.

Placement: proxy/infrastructure/ per §5 BUILD file list.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.domain.ports import CompletionUpstream


class OpenRouterUpstreamFacade:
    """Thin adapter that makes CompletionUpstream satisfy the UpstreamProvider protocol.

    The facade does NOT change the behavior of the existing completion_upstream —
    it merely provides the three-method UpstreamProvider surface so the registry
    entry is type-consistent with OpenAIDirectProvider.
    """

    def __init__(self, upstream: CompletionUpstream) -> None:
        self._upstream = upstream

    async def post_json(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        """Delegate to CompletionUpstream.complete(payload).

        The path argument is accepted for interface consistency but OpenRouter
        always uses /chat/completions — the path is not forwarded.
        """
        return await self._upstream.complete(payload)

    async def post_multipart(
        self,
        path: str,
        files: dict[str, Any],
        data: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        """Raises UpstreamUnavailableError — OpenRouter does not support multipart.

        This method MUST be present to satisfy the UpstreamProvider protocol, but
        it should never be called for the "openrouter" provider entry.  A loud error
        on misuse is intentional: it surfaces incorrect routing immediately.
        """
        raise UpstreamUnavailableError(
            "OpenRouter does not support multipart — "
            "non-chat multipart requests must use a direct provider (e.g. openai)"
        )

    def stream_bytes(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        """Delegate to CompletionUpstream.stream(payload).

        The path argument is accepted for interface consistency but OpenRouter
        always uses /chat/completions — the path is not forwarded.
        """
        return self._upstream.stream(payload)


__all__ = ["OpenRouterUpstreamFacade"]
