"""Payment-authorization port + value objects (self-serve-checkout TASK.md §3, FROZEN @ v1).

The PaymentProvider is the PAYMENT abstraction ONLY. It authorizes a payment (dev
auto-succeeds; stripe uses hosted checkout over HTTPS) and NEVER applies the plan/credit
mutation — that is orchestrated by `CheckoutService`, which calls the EXISTING domain ops
(`credits.application.topup_service.topup` / the extracted `tenants.application.
plan_assignment.assign_plan`). This keeps the "same domain op, never a parallel write path"
invariant (M8) enforceable.

Build note (recorded deviation, TASK.md §5): the frozen §3 Port sketch names the provider
methods `create_checkout(intent) -> CheckoutSession` / `confirm(session_id) -> CheckoutResult`.
Those two return types (`CheckoutSession`, `CheckoutResult`) are APP-LEVEL projections that
carry the persisted `preview`/`applied` state the CheckoutService owns — a payment adapter
structurally cannot produce them (a Stripe capture knows nothing about `applied.new_plan_id`
or the stored preview). So the adapter boundary returns payment-scoped value objects
(`ProviderSession` / `ProviderConfirmation`), and the CheckoutService assembles the frozen
`CheckoutSession`/`CheckoutResult` HTTP shapes from them. The external HTTP contract (§3
endpoints + envelopes) is unchanged; only the internal adapter signature is narrowed to keep
the adapter PAYMENT-only, exactly as the same §3 note mandates.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Protocol, runtime_checkable

IntentType = Literal["plan_upgrade", "credit_topup"]
ProviderName = Literal["dev", "stripe"]
SessionStatus = Literal["pending", "succeeded", "failed", "expired"]


@dataclass(frozen=True, slots=True)
class CheckoutIntent:
    """A checkout request as a value object.

    `tenant_id`/`actor_*` are ALWAYS server-set from the JWT Identity — NEVER request
    fields (M1). `seat_change` is reserved (deferred, A5) so a future variant is a purely
    additive change to `IntentType`, no reopen of this shape.
    """

    tenant_id: uuid.UUID
    actor_user_id: uuid.UUID
    actor_email: str
    intent_type: IntentType
    idempotency_key: str
    target_plan_id: uuid.UUID | None = None  # plan_upgrade only
    amount_usd: Decimal | None = None  # credit_topup only
    note: str | None = None  # credit_topup only


@dataclass(frozen=True, slots=True)
class ProviderSession:
    """The payment adapter's answer to "start a checkout for this intent".

    dev: auto-authorizes, status='pending', no redirect. stripe: creates a hosted-checkout
    session and returns its opaque id (`provider_ref`) + redirect URL. NO card/PII data.
    """

    provider: ProviderName
    status: SessionStatus
    provider_ref: str | None
    redirect_url: str | None


@dataclass(frozen=True, slots=True)
class ProviderConfirmation:
    """The payment adapter's answer to "did the payment succeed for this session?".

    The adapter returns ONLY the payment outcome; the applied plan/credit mutation is the
    CheckoutService's job (never the adapter's).
    """

    status: Literal["succeeded", "failed"]
    provider_ref: str | None
    error: str | None = None


@runtime_checkable
class PaymentProvider(Protocol):
    """The gateway-owned payment-authorization port (PAYMENT only — no mutation)."""

    provider: ProviderName

    async def create_checkout(self, intent: CheckoutIntent) -> ProviderSession:
        """Authorize/begin a payment for `intent`. May raise PaymentProviderUnavailable."""
        ...

    async def confirm(self, provider_ref: str | None) -> ProviderConfirmation:
        """Capture/confirm the payment. May raise PaymentProviderUnavailable."""
        ...
