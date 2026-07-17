"""Stripe payment adapter (self-serve-checkout TASK.md §3, M6/M12) — config-gated.

Uses Stripe HOSTED CHECKOUT over HTTPS (dependency-free: the existing `httpx`, no Stripe
SDK — the card is entered on Stripe's page, never on the gateway; only an opaque
`provider_ref` (the checkout-session id) is persisted, NEVER card data, M6).

DESIGNED FOR FAILURE (M12, PROJECT.md invariant): every outbound call carries a bounded
timeout + bounded retry + a per-instance circuit breaker. A breaker-open / timeout /
transport failure degrades to `PaymentProviderUnavailable` (the CheckoutService maps it to
503 payment_provider_unavailable) with NO partial mutation — the mutation is applied by the
CheckoutService only AFTER a confirmed capture, never speculatively.
"""

from __future__ import annotations

import asyncio

import httpx

from gateway.payments.domain.errors import PaymentProviderUnavailable
from gateway.payments.domain.ports import (
    CheckoutIntent,
    ProviderConfirmation,
    ProviderName,
    ProviderSession,
)
from gateway.proxy.domain.errors import CircuitOpenError
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker


class StripePaymentProvider:
    """PAYMENT-only Stripe adapter — never applies the plan/credit mutation."""

    provider: ProviderName = "stripe"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.stripe.com",
        breaker: CircuitBreaker | None = None,
        http_client: httpx.AsyncClient | None = None,
        timeout_s: float = 10.0,
        max_retries: int = 2,
        retry_backoff_base_s: float = 0.2,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._breaker = breaker if breaker is not None else CircuitBreaker()
        self._client = http_client
        self._timeout_s = timeout_s
        self._max_retries = max_retries
        self._backoff = retry_backoff_base_s

    async def create_checkout(self, intent: CheckoutIntent) -> ProviderSession:
        body = await self._request(
            "POST",
            "/v1/checkout/sessions",
            data={"mode": "payment", "client_reference_id": str(intent.tenant_id)},
        )
        raw_id = body.get("id")
        raw_url = body.get("url")
        return ProviderSession(
            provider="stripe",
            status="pending",
            provider_ref=str(raw_id) if raw_id is not None else None,
            redirect_url=raw_url if isinstance(raw_url, str) else None,
        )

    async def confirm(self, provider_ref: str | None) -> ProviderConfirmation:
        if provider_ref is None:
            return ProviderConfirmation(status="failed", provider_ref=None, error="no_provider_ref")
        body = await self._request("GET", f"/v1/checkout/sessions/{provider_ref}")
        paid = body.get("payment_status") == "paid" or body.get("status") == "complete"
        return ProviderConfirmation(
            status="succeeded" if paid else "failed",
            provider_ref=provider_ref,
            error=None if paid else "payment_not_completed",
        )

    # -- outbound IO with timeout + bounded retry + breaker (M12) ------------
    async def _request(
        self, method: str, path: str, *, data: dict[str, str] | None = None
    ) -> dict[str, object]:
        # Breaker FIRST: an open circuit short-circuits with no dial (fail-fast).
        try:
            self._breaker.guard()
        except CircuitOpenError as exc:
            raise PaymentProviderUnavailable("stripe circuit open") from exc

        url = f"{self._base_url}{path}"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = await self._send(method, url, headers=headers, data=data)
                if resp.status_code >= 500:
                    # upstream server error — retryable, counts toward the breaker.
                    raise httpx.HTTPStatusError("stripe 5xx", request=resp.request, response=resp)
                self._breaker.record_success()
                return resp.json()
            except (httpx.HTTPError, TimeoutError) as exc:
                last_exc = exc
                self._breaker.on_upstream_error()
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff * (2**attempt))
                    continue
        raise PaymentProviderUnavailable("stripe unreachable") from last_exc

    async def _send(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        data: dict[str, str] | None,
    ) -> httpx.Response:
        if self._client is not None:
            return await self._client.request(
                method, url, headers=headers, data=data, timeout=self._timeout_s
            )
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            return await client.request(method, url, headers=headers, data=data)


__all__ = ["StripePaymentProvider"]
