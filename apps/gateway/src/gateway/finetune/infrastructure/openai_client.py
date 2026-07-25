"""Real ``FinetuneProviderPort`` implementation — OpenAI ``/v1/fine_tuning/jobs``.

finetune-broker PLAN.md §3 (FROZEN @ v1): httpx, connect/read timeouts, per-tenant
CircuitBreaker. Retry policy per §3:
  submit — 1 attempt, NO retry (non-idempotent: a retried create could double-submit
           the same training job at the provider).
  poll   — up to 2 idempotent retries (read-only).
  cancel — 1 idempotent retry (safe to repeat).

Never logs the credential; the plaintext secret lives only inside the request frame
built in each method (T4 threat model). Not exercised by the frozen §4 red suite
(which injects ``FakeFinetuneProvider`` at app.state.finetune_provider) — this is
the production adapter wired by main.py.

HEAL (D1, 2026-07-24 — mirrors the moderations CR-1 per-tenant breaker defect,
``proxy/infrastructure/ml_moderation_evaluator.py``): the breaker is now keyed PER
TENANT via ``_TenantBreakerRegistry`` (a bounded LRU, same shape as the moderations
registry) instead of a single shared instance. Before this heal, ``submit``/``poll``/
``cancel`` took no tenant key at all — every call funneled into ONE process-wide
CircuitBreaker, so 5 consecutive failures for tenant A would trip OPEN and 502 every
OTHER tenant's fine-tuning calls too. ``FinetuneBrokerService`` now threads the
authenticated ``tenant_id`` through as the first argument to all three methods.
"""

from __future__ import annotations

import uuid
from collections import OrderedDict
from typing import Any

import httpx

from gateway.proxy.domain.errors import CircuitOpenError
from gateway.proxy.domain.provider_credentials import BearerCredential
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

_DEFAULT_CONNECT_TIMEOUT_S = 5.0
_DEFAULT_READ_TIMEOUT_S = 30.0

#: Bounded so a long-lived process never accumulates one CircuitBreaker per tenant
#: forever (mirrors ``_TenantBreakerRegistry`` in ml_moderation_evaluator.py, CR-1).
_MAX_TENANT_BREAKERS = 4096


class _TenantBreakerRegistry:
    """Bounded LRU registry of per-tenant CircuitBreaker instances (D1 heal, mirrors
    the moderations CR-1 ``_TenantBreakerRegistry``).

    A single process-wide breaker would let one tenant's outage 502 every other
    tenant's fine-tuning calls — this registry gives each ``tenant_id`` its OWN
    breaker so failures never cross tenant boundaries. Bounded (LRU-evicted) so an
    unbounded number of distinct tenants over a long-lived process can never leak
    memory; an evicted, cold tenant's breaker simply resets to CLOSED on next use
    (acceptable — eviction only ever affects an inactive tenant).
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
            self._breakers.popitem(last=False)  # evict the LRU entry
        return breaker

    def __len__(self) -> int:
        return len(self._breakers)


class OpenAIFinetuneClient:
    """Bounded-IO OpenAI fine-tuning adapter; one CircuitBreaker PER TENANT (D1).

    Raises ``CircuitOpenError`` (from ``breaker.guard()``) when the calling tenant's
    breaker is open — callers map this to the same 502
    ERR_FINETUNE_PROVIDER_UNREACHABLE as any other transport failure. A different
    tenant's breaker is entirely unaffected.
    """

    def __init__(
        self,
        *,
        base_url: str = "https://api.openai.com/v1",
        connect_timeout: float = _DEFAULT_CONNECT_TIMEOUT_S,
        read_timeout: float = _DEFAULT_READ_TIMEOUT_S,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(
            connect=connect_timeout, read=read_timeout, write=connect_timeout, pool=connect_timeout
        )
        self._tenant_breakers = _TenantBreakerRegistry()

    def _breaker_for(self, tenant_id: uuid.UUID | str) -> CircuitBreaker:
        """Return THIS tenant's breaker — never a shared/global one (D1)."""
        return self._tenant_breakers.get_or_create(tenant_id)

    @staticmethod
    def _headers(credential: Any) -> dict[str, str]:
        secret = (
            credential.secret.get_secret_value()
            if isinstance(credential, BearerCredential)
            else str(getattr(credential, "secret", credential))
        )
        return {"Authorization": f"Bearer {secret}"}

    async def submit(
        self, tenant_id: uuid.UUID | str, credential: Any, request: dict[str, Any]
    ) -> str:
        """Create the job at the provider — ONE attempt, no retry (non-idempotent)."""
        breaker = self._breaker_for(tenant_id)
        breaker.guard()  # raises CircuitOpenError if open — no call attempted
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/fine_tuning/jobs",
                    json=request,
                    headers=self._headers(credential),
                )
                resp.raise_for_status()
                body = resp.json()
        except (httpx.TransportError, httpx.HTTPStatusError):
            breaker.on_upstream_error()
            raise
        breaker.record_success()
        return str(body["id"])

    async def poll(
        self, tenant_id: uuid.UUID | str, credential: Any, provider_job_id: str
    ) -> dict[str, Any]:
        """Poll job status — up to 2 idempotent retries on transport/5xx failure."""
        breaker = self._breaker_for(tenant_id)
        breaker.guard()
        last_exc: Exception | None = None
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for _attempt in range(3):  # 1 initial + 2 retries
                try:
                    resp = await client.get(
                        f"{self._base_url}/fine_tuning/jobs/{provider_job_id}",
                        headers=self._headers(credential),
                    )
                    resp.raise_for_status()
                    body = resp.json()
                    breaker.record_success()
                    return {
                        "status": body.get("status"),
                        "fine_tuned_model": body.get("fine_tuned_model"),
                    }
                except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                    breaker.on_upstream_error()
                    last_exc = exc
        assert last_exc is not None
        raise last_exc

    async def cancel(
        self, tenant_id: uuid.UUID | str, credential: Any, provider_job_id: str
    ) -> None:
        """Cancel at the provider — 1 idempotent retry on transport/5xx failure."""
        breaker = self._breaker_for(tenant_id)
        breaker.guard()
        last_exc: Exception | None = None
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for _attempt in range(2):  # 1 initial + 1 retry
                try:
                    resp = await client.post(
                        f"{self._base_url}/fine_tuning/jobs/{provider_job_id}/cancel",
                        headers=self._headers(credential),
                    )
                    resp.raise_for_status()
                    breaker.record_success()
                    return
                except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                    breaker.on_upstream_error()
                    last_exc = exc
        assert last_exc is not None
        raise last_exc


__all__ = ["CircuitOpenError", "OpenAIFinetuneClient"]
