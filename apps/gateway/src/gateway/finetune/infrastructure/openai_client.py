"""Real ``FinetuneProviderPort`` implementation — OpenAI ``/v1/fine_tuning/jobs``.

finetune-broker PLAN.md §3 (FROZEN @ v1): httpx, connect/read timeouts, per-tenant
CircuitBreaker (mirrors ``proxy/infrastructure/circuit_breaker.py`` — CR-1
precedent: one breaker instance per tenant so one tenant's outage never trips
another's — T6 unbounded-outbound-IO closure). Retry policy per §3:
  submit — 1 attempt, NO retry (non-idempotent: a retried create could double-submit
           the same training job at the provider).
  poll   — up to 2 idempotent retries (read-only).
  cancel — 1 idempotent retry (safe to repeat).

Never logs the credential; the plaintext secret lives only inside the request frame
built in each method (T4 threat model). Not exercised by the frozen §4 red suite
(which injects ``FakeFinetuneProvider`` at app.state.finetune_provider) — this is
the production adapter wired by main.py.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from gateway.proxy.domain.errors import CircuitOpenError
from gateway.proxy.domain.provider_credentials import BearerCredential
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

_DEFAULT_CONNECT_TIMEOUT_S = 5.0
_DEFAULT_READ_TIMEOUT_S = 30.0


class OpenAIFinetuneClient:
    """Bounded-IO OpenAI fine-tuning adapter; one CircuitBreaker per tenant.

    Raises ``CircuitOpenError`` (from ``breaker.guard()``) when the per-tenant
    breaker is open — callers map this to the same 502
    ERR_FINETUNE_PROVIDER_UNREACHABLE as any other transport failure.
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
        self._breakers: dict[uuid.UUID | str, CircuitBreaker] = {}

    def _breaker_for(self, tenant_key: uuid.UUID | str) -> CircuitBreaker:
        breaker = self._breakers.get(tenant_key)
        if breaker is None:
            breaker = CircuitBreaker()
            self._breakers[tenant_key] = breaker
        return breaker

    @staticmethod
    def _headers(credential: Any) -> dict[str, str]:
        secret = (
            credential.secret.get_secret_value()
            if isinstance(credential, BearerCredential)
            else str(getattr(credential, "secret", credential))
        )
        return {"Authorization": f"Bearer {secret}"}

    async def submit(
        self, credential: Any, request: dict[str, Any], *, tenant_key: uuid.UUID | str = "_"
    ) -> str:
        """Create the job at the provider — ONE attempt, no retry (non-idempotent)."""
        breaker = self._breaker_for(tenant_key)
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
        self, credential: Any, provider_job_id: str, *, tenant_key: uuid.UUID | str = "_"
    ) -> dict[str, Any]:
        """Poll job status — up to 2 idempotent retries on transport/5xx failure."""
        breaker = self._breaker_for(tenant_key)
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
        self, credential: Any, provider_job_id: str, *, tenant_key: uuid.UUID | str = "_"
    ) -> None:
        """Cancel at the provider — 1 idempotent retry on transport/5xx failure."""
        breaker = self._breaker_for(tenant_key)
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


__all__ = ["OpenAIFinetuneClient", "CircuitOpenError"]
