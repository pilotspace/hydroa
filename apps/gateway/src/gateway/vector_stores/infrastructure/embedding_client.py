"""VectorStoreEmbeddingClient — production ChunkEmbedder adapter (M10, FROZEN @ v1).

Bounded IO: httpx connect/read timeouts, at most ONE idempotent retry (embed is a
read-only op — safe to repeat). One CircuitBreaker PER TENANT via a bounded-LRU
registry — mirrors ``OpenAIFinetuneClient._TenantBreakerRegistry`` in
``gateway.finetune.infrastructure.openai_client`` (this repo HARD-STOPPED twice on
a shared breaker: moderations CR-1, finetune D1 — never repeat that defect here).

Not exercised by the §4 red suite (which injects a ``FakeChunkEmbedder`` at
``app.state.vector_store_embedder``) except for the per-tenant-breaker structural
probe (M10) — this is the production adapter wired by main.py.
"""

from __future__ import annotations

import uuid
from collections import OrderedDict
from typing import Any

import httpx

from gateway.proxy.domain.errors import UpstreamUnavailableError
from gateway.proxy.infrastructure.circuit_breaker import (
    CircuitBreaker,
    status_counts_as_upstream_failure,
)

_DEFAULT_CONNECT_TIMEOUT_S = 5.0
_DEFAULT_READ_TIMEOUT_S = 30.0
_DEFAULT_BASE_URL = "https://api.openai.com/v1"

#: Bounded so a long-lived process never accumulates one CircuitBreaker per tenant
#: forever (mirrors OpenAIFinetuneClient._TenantBreakerRegistry).
_MAX_TENANT_BREAKERS = 4096


class _TenantBreakerRegistry:
    """Bounded LRU registry of per-tenant CircuitBreaker instances.

    A single process-wide breaker would let one tenant's embeddings outage 502
    every other tenant's ingestion — this registry gives each ``tenant_id`` its
    OWN breaker. Bounded (LRU-evicted); an evicted cold tenant's breaker simply
    resets to CLOSED on next use.
    """

    def __init__(self, max_size: int = _MAX_TENANT_BREAKERS) -> None:
        self._max_size = max_size
        self._breakers: OrderedDict[Any, CircuitBreaker] = OrderedDict()

    def get_or_create(self, tenant_key: Any) -> CircuitBreaker:
        existing = self._breakers.get(tenant_key)
        if existing is not None:
            self._breakers.move_to_end(tenant_key)
            return existing
        breaker = CircuitBreaker()
        self._breakers[tenant_key] = breaker
        if len(self._breakers) > self._max_size:
            self._breakers.popitem(last=False)
        return breaker

    def __len__(self) -> int:
        return len(self._breakers)


class VectorStoreEmbeddingClient:
    """Bounded-IO embeddings adapter; one CircuitBreaker PER TENANT (M10)."""

    def __init__(
        self,
        *,
        base_url: str = _DEFAULT_BASE_URL,
        connect_timeout: float = _DEFAULT_CONNECT_TIMEOUT_S,
        read_timeout: float = _DEFAULT_READ_TIMEOUT_S,
        api_key: str | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(
            connect=connect_timeout, read=read_timeout, write=connect_timeout, pool=connect_timeout
        )
        self._api_key = api_key
        self._tenant_breakers = _TenantBreakerRegistry()

    def _breaker_for(self, tenant_id: uuid.UUID | str) -> CircuitBreaker:
        """Return THIS tenant's breaker — never a shared/global one (M10)."""
        return self._tenant_breakers.get_or_create(tenant_id)

    async def embed(
        self, tenant_id: uuid.UUID, model: str, texts: list[str]
    ) -> tuple[list[list[float]], dict[str, object] | None]:
        """ONE batched embeddings call for every chunk text; ≤1 idempotent retry."""
        breaker = self._breaker_for(tenant_id)
        breaker.guard()  # raises CircuitOpenError if open — no call attempted
        headers = {"Authorization": f"Bearer {self._api_key or ''}"}
        last_exc: Exception | None = None
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for _attempt in range(2):  # 1 initial + 1 idempotent retry
                try:
                    resp = await client.post(
                        f"{self._base_url}/embeddings",
                        json={"model": model, "input": list(texts)},
                        headers=headers,
                    )
                    resp.raise_for_status()
                    body = resp.json()
                    breaker.record_success()
                    vectors = [item["embedding"] for item in body["data"]]
                    usage = body.get("usage")
                    return vectors, usage
                except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                    last_exc = exc
                    if isinstance(exc, httpx.HTTPStatusError) and (
                        not status_counts_as_upstream_failure(exc.response.status_code)
                    ):
                        # Terminal 4xx: the upstream answered correctly. A revoked key is
                        # not going to become valid between attempts, and counting it
                        # would open THIS tenant's breaker over their own credential.
                        breaker.record_success()
                        break
                    breaker.on_upstream_error()
        assert last_exc is not None
        raise UpstreamUnavailableError(str(last_exc)) from last_exc


__all__ = ["VectorStoreEmbeddingClient"]
