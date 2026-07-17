"""Dev payment adapter (self-serve-checkout TASK.md §3) — DEFAULT provider.

Auto-authorizes: create returns a pending session (no redirect), confirm auto-succeeds.
No outbound IO, no card/PII. Every mutation it authorizes is unmistakably marked
`provider="dev"` on the checkout_sessions row and the audit event (M5).
"""

from __future__ import annotations

from gateway.payments.domain.ports import (
    CheckoutIntent,
    ProviderConfirmation,
    ProviderName,
    ProviderSession,
)


class DevPaymentProvider:
    """PAYMENT-only dev adapter — never applies the plan/credit mutation."""

    provider: ProviderName = "dev"

    async def create_checkout(self, intent: CheckoutIntent) -> ProviderSession:
        return ProviderSession(
            provider="dev", status="pending", provider_ref=None, redirect_url=None
        )

    async def confirm(self, provider_ref: str | None) -> ProviderConfirmation:
        return ProviderConfirmation(status="succeeded", provider_ref=provider_ref, error=None)


__all__ = ["DevPaymentProvider"]
