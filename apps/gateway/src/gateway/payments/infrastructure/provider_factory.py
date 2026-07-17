"""Composition-root selection of the PaymentProvider (self-serve-checkout TASK.md §3, M10).

dev is DEFAULT ON; stripe is selected ONLY when `payment_provider == "stripe"` (which the
Settings boot validator has already proven carries a non-empty key). An absent stripe key =
stripe disabled, dev used — no runtime branch needed here because Settings normalizes it.
"""

from __future__ import annotations

from gateway.core.config import Settings
from gateway.payments.domain.ports import PaymentProvider
from gateway.payments.infrastructure.dev_provider import DevPaymentProvider
from gateway.payments.infrastructure.stripe_provider import StripePaymentProvider
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker


def build_payment_provider(settings: Settings) -> PaymentProvider:
    if settings.payment_provider == "stripe" and settings.payment_stripe_api_key.strip():
        return StripePaymentProvider(
            api_key=settings.payment_stripe_api_key,
            base_url=settings.payment_stripe_base_url,
            breaker=CircuitBreaker(
                failure_threshold=settings.payment_stripe_breaker_failure_threshold,
                cooldown_seconds=settings.payment_stripe_breaker_cooldown_seconds,
            ),
            timeout_s=settings.payment_stripe_timeout_seconds,
            max_retries=settings.payment_stripe_max_retries,
        )
    return DevPaymentProvider()


__all__ = ["build_payment_provider"]
