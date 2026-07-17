"""Tenant-scoped self-serve checkout API (self-serve-checkout TASK.md §3, FROZEN @ v1).

Every endpoint:
  - derives tenant_id/actor from `get_identity` (JWT) ONLY — never a body field (M1);
  - is gated by `require_permission(Permission.BILLING_MANAGE)` = {OWNER, BILLING_ADMIN} (M2);
  - honors the `payment_checkout_enabled` kill switch (M11);
  - carries `idempotency_key` in the JSON BODY (the BFF strips custom headers, I4/A6).

The mutation is orchestrated by CheckoutService (never here, never the adapter). Audit is
fire-and-forget/fail-open (M7) — scheduled off the hot path AFTER the mutation committed, so
an audit failure can never fail the checkout.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Request

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.payments.api.schemas import (
    ConfirmRequest,
    CreditTopupRequest,
    PlanUpgradeRequest,
)
from gateway.payments.application.checkout_service import (
    CheckoutService,
    ConfirmOutcome,
    CreateOutcome,
)
from gateway.payments.domain.errors import CheckoutError
from gateway.payments.domain.ports import PaymentProvider
from gateway.tenants.domain.authz import Permission, require_permission
from gateway.tenants.domain.entities import Identity

checkout_router = APIRouter(prefix="/admin/checkout", tags=["checkout"])


def _guard_enabled(request: Request) -> None:
    """M11 kill switch — reject cleanly with no mutation when OFF."""
    if not request.app.state.settings.payment_checkout_enabled:
        raise CheckoutError.checkout_disabled()


def _require_key(idempotency_key: str | None) -> str:
    if idempotency_key is None or not idempotency_key.strip():
        raise CheckoutError.idempotency_key_required()
    return idempotency_key


def _service(request: Request) -> CheckoutService:
    provider: PaymentProvider = request.app.state.payment_provider
    return CheckoutService(
        request.app.state.sessionmaker,
        provider,
        topup_max_usd=Decimal(str(request.app.state.settings.payment_topup_max_usd)),
    )


def _create_body(outcome: CreateOutcome) -> dict[str, Any]:
    return {
        "session_id": str(outcome.session_id),
        "provider": outcome.provider,
        "status": outcome.status,
        "redirect_url": outcome.redirect_url,
        "preview": outcome.preview,
    }


def _schedule_audit(request: Request, identity: Identity, outcome: ConfirmOutcome) -> None:
    """Fire-and-forget, fail-open tenant-scoped audit (M7) — never awaited on the hot path."""
    if not outcome.newly_applied or outcome.audit_action is None:
        return
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=identity.tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action=outcome.audit_action,
                target_type="checkout",
                target_id=str(outcome.session_id),
                result="success",
                metadata=outcome.audit_metadata,
                created_at=datetime.now(UTC),
            ),
        )
    )


# ---------------------------------------------------------------------------
# POST /admin/checkout/plan-upgrade
# ---------------------------------------------------------------------------


@checkout_router.post("/plan-upgrade")
async def post_plan_upgrade(
    body: PlanUpgradeRequest,
    request: Request,
    identity: Annotated[Identity, require_permission(Permission.BILLING_MANAGE)],
) -> dict[str, Any]:
    _guard_enabled(request)
    key = _require_key(body.idempotency_key)
    outcome = await _service(request).create_plan_upgrade(
        tenant_id=identity.tenant_id,
        actor_user_id=identity.user_id,
        actor_email=identity.email,
        target_plan_id=body.target_plan_id,
        idempotency_key=key,
    )
    return _create_body(outcome)


# ---------------------------------------------------------------------------
# POST /admin/checkout/credit-topup
# ---------------------------------------------------------------------------


@checkout_router.post("/credit-topup")
async def post_credit_topup(
    body: CreditTopupRequest,
    request: Request,
    identity: Annotated[Identity, require_permission(Permission.BILLING_MANAGE)],
) -> dict[str, Any]:
    _guard_enabled(request)
    key = _require_key(body.idempotency_key)
    outcome = await _service(request).create_credit_topup(
        tenant_id=identity.tenant_id,
        actor_user_id=identity.user_id,
        actor_email=identity.email,
        amount_raw=body.amount_usd,
        idempotency_key=key,
        note=body.note,
    )
    return _create_body(outcome)


# ---------------------------------------------------------------------------
# POST /admin/checkout/{session_id}/confirm
# ---------------------------------------------------------------------------


@checkout_router.post("/{session_id}/confirm")
async def post_confirm(
    session_id: uuid.UUID,
    _body: ConfirmRequest,
    request: Request,
    identity: Annotated[Identity, require_permission(Permission.BILLING_MANAGE)],
) -> dict[str, Any]:
    _guard_enabled(request)
    outcome = await _service(request).confirm(tenant_id=identity.tenant_id, session_id=session_id)
    _schedule_audit(request, identity, outcome)
    return {
        "session_id": str(outcome.session_id),
        "provider": outcome.provider,
        "status": outcome.status,
        "applied": outcome.applied,
    }


# ---------------------------------------------------------------------------
# GET /admin/checkout/{session_id}
# ---------------------------------------------------------------------------


@checkout_router.get("/{session_id}")
async def get_session(
    session_id: uuid.UUID,
    request: Request,
    identity: Annotated[Identity, require_permission(Permission.BILLING_MANAGE)],
) -> dict[str, Any]:
    view = await _service(request).get(tenant_id=identity.tenant_id, session_id=session_id)
    return {
        "session_id": str(view.session_id),
        "provider": view.provider,
        "status": view.status,
        "redirect_url": view.redirect_url,
        "preview": view.preview,
    }


__all__ = ["checkout_router"]
