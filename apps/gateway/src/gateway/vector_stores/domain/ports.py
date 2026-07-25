"""ChunkEmbedder port — vector-store-files PLAN.md §3 (FROZEN @ v1).

A thin Protocol seam between the ingestion worker and an embeddings provider.
Deliberately NOT the full ``EmbeddingsUseCase`` (that would drag governance /
cache / guardrails into an internal server-side call and double-couple billing —
rejected in §1 framing). The worker makes exactly ONE batched call per file.

Raises ``UpstreamUnavailableError`` (upstream 5xx/timeout/transport error) or
``CircuitOpenError`` (this tenant's breaker is open) on failure — both from
``gateway.proxy.domain.errors`` — mapped by the worker to
``status="failed", last_error.code="embedding_unavailable"``.
"""

from __future__ import annotations

import uuid
from typing import Protocol


class ChunkEmbedder(Protocol):
    """Port: batch-embed every chunk text for one tenant/model in ONE call."""

    async def embed(
        self, tenant_id: uuid.UUID, model: str, texts: list[str]
    ) -> tuple[list[list[float]], dict[str, object] | None]:
        """Return (vectors, usage) — one vector per text, in the same order.

        ``usage`` mirrors an OpenAI-shaped usage dict (e.g. ``prompt_tokens``,
        ``total_tokens``) or None. Raises UpstreamUnavailableError/CircuitOpenError
        on failure — never returns a partial result.
        """
        ...
