"""CheckoutService — orchestrates a self-serve checkout (self-serve-checkout TASK.md §3).

The ONLY place a checkout mutation is applied. It reuses the EXISTING domain ops verbatim
(`credits.application.topup_service.topup` / the extracted `tenants.application.
plan_assignment.assign_plan`) — never a parallel write path (M8). The payment adapter is
consulted for PAYMENT authorization only; this service owns persistence (checkout_sessions),
preview price math, the row-locked idempotent confirm, and the applied mutation.

Safety rule (§5): on confirm, the plan/credit mutation AND the session pending->succeeded
flip happen under one SELECT ... FOR UPDATE on the checkout_sessions row (M3). The credit
path additionally threads the session's idempotency_key into topup_service (defense-in-depth
via the credit_ledger unique index). No card/PII is ever persisted (M6).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.credits.application.topup_service import topup as topup_service
from gateway.payments.domain.errors import CheckoutError, PaymentProviderUnavailable
from gateway.payments.domain.ports import CheckoutIntent, PaymentProvider
from gateway.payments.infrastructure import checkout_repo
from gateway.payments.infrastructure.orm import CheckoutSessionRow
from gateway.tenants.application.plan_assignment import assign_plan
from gateway.tenants.infrastructure.orm import PlanRow, TenantRow

_SESSION_TTL = timedelta(hours=24)


def _money2(value: Decimal | None) -> str:
    """Format a base-fee amount to 2dp; NULL base (free/enterprise) renders as 0.00."""
    d = value if value is not None else Decimal("0")
    return str(d.quantize(Decimal("0.01")))


@dataclass(frozen=True, slots=True)
class CreateOutcome:
    session_id: uuid.UUID
    provider: str
    status: str
    redirect_url: str | None
    preview: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ConfirmOutcome:
    session_id: uuid.UUID
    provider: str
    status: str
    applied: dict[str, Any]
    newly_applied: bool
    audit_action: str | None
    audit_metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class SessionView:
    session_id: uuid.UUID
    provider: str
    status: str
    redirect_url: str | None
    preview: dict[str, Any]


class CheckoutService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        provider: PaymentProvider,
        *,
        topup_max_usd: Decimal,
    ) -> None:
        self._sf = session_factory
        self._provider = provider
        self._topup_max = topup_max_usd

    # ------------------------------------------------------------------ create
    async def create_plan_upgrade(
        self,
        *,
        tenant_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_email: str,
        target_plan_id: uuid.UUID,
        idempotency_key: str,
    ) -> CreateOutcome:
        intent = CheckoutIntent(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            intent_type="plan_upgrade",
            idempotency_key=idempotency_key,
            target_plan_id=target_plan_id,
        )

        # Idempotent-create / conflict pre-check.
        replay = await self._existing_or_conflict(intent)
        if replay is not None:
            return replay

        async with self._sf() as s:
            tenant_row = await self._require_tenant(s, tenant_id)
            target = await self._require_plan(s, target_plan_id)
            current = (
                await self._load_plan(s, tenant_row.plan_id)
                if tenant_row.plan_id is not None
                else None
            )
            self._validate_plan_upgrade(tenant_row, current, target)
            preview = self._plan_preview(current, target)

        return await self._begin_and_persist(intent, preview)

    async def create_credit_topup(
        self,
        *,
        tenant_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_email: str,
        amount_raw: str,
        idempotency_key: str,
        note: str | None,
    ) -> CreateOutcome:
        amount = self._parse_amount(amount_raw)
        intent = CheckoutIntent(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            intent_type="credit_topup",
            idempotency_key=idempotency_key,
            amount_usd=amount,
            note=note,
        )

        replay = await self._existing_or_conflict(intent)
        if replay is not None:
            return replay

        async with self._sf() as s:
            balance_before = await self._balance(s, tenant_id)
        preview = {
            "intent": "credit_topup",
            "amount_usd": str(amount),
            "currency": "USD",
            "balance_before_usd": str(balance_before),
            "balance_after_preview_usd": str(balance_before + amount),
        }
        return await self._begin_and_persist(intent, preview)

    # ------------------------------------------------------------------ confirm
    async def confirm(self, *, tenant_id: uuid.UUID, session_id: uuid.UUID) -> ConfirmOutcome:
        async with self._sf() as s, s.begin():
            row = await checkout_repo.lock_for_confirm(s, tenant_id, session_id)
            if row is None:
                raise CheckoutError.checkout_not_found()  # unknown OR cross-tenant (M1)
            if row.status == "succeeded":
                # Idempotent replay — re-apply NOTHING (M3).
                applied = dict(row.applied_ref or {})
                return ConfirmOutcome(
                    session_id=row.id,
                    provider=row.provider,
                    status="succeeded",
                    applied=applied,
                    newly_applied=False,
                    audit_action=None,
                    audit_metadata={},
                )
            if row.status != "pending":
                raise CheckoutError.checkout_not_confirmable()

            # PAYMENT capture (adapter is PAYMENT-only). Failure => 503, NO mutation.
            try:
                confirmation = await self._provider.confirm(row.provider_ref)
            except PaymentProviderUnavailable as exc:
                raise CheckoutError.payment_provider_unavailable() from exc
            if confirmation.status != "succeeded":
                raise CheckoutError.payment_provider_unavailable()

            if row.intent_type == "plan_upgrade":
                applied, audit_meta = await self._apply_plan_upgrade(s, row)
                audit_action = "checkout.plan_upgrade"
            else:
                applied, audit_meta = await self._apply_credit_topup(row)
                audit_action = "checkout.credit_topup"

            row.status = "succeeded"
            row.applied_ref = applied
            row.confirmed_at = datetime.now(UTC)

        return ConfirmOutcome(
            session_id=session_id,
            provider=row.provider,
            status="succeeded",
            applied=applied,
            newly_applied=True,
            audit_action=audit_action,
            audit_metadata=audit_meta,
        )

    # ------------------------------------------------------------------ get
    async def get(self, *, tenant_id: uuid.UUID, session_id: uuid.UUID) -> SessionView:
        async with self._sf() as s:
            row = await checkout_repo.find_by_id(s, tenant_id, session_id)
        if row is None:
            raise CheckoutError.checkout_not_found()
        return SessionView(
            session_id=row.id,
            provider=row.provider,
            status=row.status,
            redirect_url=None,
            preview=dict(row.preview),
        )

    # ------------------------------------------------------------- apply helpers
    async def _apply_plan_upgrade(
        self, s: AsyncSession, row: CheckoutSessionRow
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        target_plan_id = uuid.UUID(str(row.intent_payload["target_plan_id"]))
        tenant_row = await self._require_tenant(s, row.tenant_id)
        target = await self._require_plan(s, target_plan_id)
        assignment = assign_plan(tenant_row, plan_id=target.id, seat_cap=target.seat_cap)
        applied = {
            "new_plan_id": str(target.id),
            "from_plan_id": (
                str(assignment.old_plan_id) if assignment.old_plan_id is not None else None
            ),
            "seat_cap": assignment.new_seat_cap,
        }
        audit_meta: dict[str, Any] = {
            "provider": row.provider,
            "session_id": str(row.id),
            "from_plan": (
                str(assignment.old_plan_id) if assignment.old_plan_id is not None else None
            ),
            "to_plan": str(target.id),
        }
        return applied, audit_meta

    async def _apply_credit_topup(
        self, row: CheckoutSessionRow
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        amount = Decimal(str(row.intent_payload["amount_usd"]))
        note = row.intent_payload.get("note")
        # Reuse the idempotent, row-locked domain op VERBATIM (M8). The session's own
        # idempotency_key is threaded through as the ledger key — double idempotency.
        result = await topup_service(
            self._sf,
            tenant_id=row.tenant_id,
            amount_usd=amount,
            idempotency_key=row.idempotency_key,
            note=note,
            actor_user_id=row.actor_user_id,
        )
        applied = {
            "ledger_entry_id": str(result.id),
            "balance_after_usd": str(result.balance_after_usd),
        }
        audit_meta = {
            "provider": row.provider,
            "session_id": str(row.id),
            "amount_usd": str(result.amount_usd),
            "balance_after_usd": str(result.balance_after_usd),
        }
        return applied, audit_meta

    # ------------------------------------------------------------- create helpers
    async def _existing_or_conflict(self, intent: CheckoutIntent) -> CreateOutcome | None:
        """M4 idempotent-create pre-check: same key + same payload -> replay the existing
        session; same key + different payload -> 409 idempotency_key_conflict."""
        async with self._sf() as s:
            existing = await checkout_repo.find_by_idempotency_key(
                s, intent.tenant_id, intent.idempotency_key
            )
        if existing is None:
            return None
        if not self._payload_matches(existing, intent):
            raise CheckoutError.idempotency_key_conflict()
        return CreateOutcome(
            session_id=existing.id,
            provider=existing.provider,
            status=existing.status,
            redirect_url=None,
            preview=dict(existing.preview),
        )

    async def _begin_and_persist(
        self, intent: CheckoutIntent, preview: dict[str, Any]
    ) -> CreateOutcome:
        # PAYMENT authorization (adapter). Failure => 503, NO session persisted.
        try:
            provider_session = await self._provider.create_checkout(intent)
        except PaymentProviderUnavailable as exc:
            raise CheckoutError.payment_provider_unavailable() from exc

        now = datetime.now(UTC)
        row_id = uuid.uuid4()
        payload = self._intent_payload(intent)
        try:
            async with self._sf() as s, s.begin():
                checkout_repo.insert_session(
                    s,
                    row_id=row_id,
                    tenant_id=intent.tenant_id,
                    actor_user_id=intent.actor_user_id,
                    provider=provider_session.provider,
                    intent_type=intent.intent_type,
                    intent_payload=payload,
                    idempotency_key=intent.idempotency_key,
                    status=provider_session.status,
                    provider_ref=provider_session.provider_ref,
                    preview=preview,
                    created_at=now,
                    expires_at=now + _SESSION_TTL,
                )
            return CreateOutcome(
                session_id=row_id,
                provider=provider_session.provider,
                status=provider_session.status,
                redirect_url=provider_session.redirect_url,
                preview=preview,
            )
        except IntegrityError:
            # A concurrent create won the UNIQUE(tenant_id, idempotency_key) race — re-resolve
            # replay-vs-conflict against whichever row landed (mirrors topup_service).
            replay = await self._existing_or_conflict(intent)
            if replay is None:  # pragma: no cover — defensive; a row must exist
                raise
            return replay

    # ------------------------------------------------------------- validation
    def _validate_plan_upgrade(
        self, tenant_row: TenantRow, current: PlanRow | None, target: PlanRow
    ) -> None:
        # Order: unchanged -> not-self-serve -> audience -> downgrade (see §1 reject table).
        if current is not None and current.id == target.id:
            raise CheckoutError.plan_unchanged()
        if not target.self_serve:
            raise CheckoutError.plan_not_self_serve()
        if target.audience is not None and target.audience != tenant_row.account_type:
            raise CheckoutError.plan_account_type_mismatch()
        current_base = current.base_price_usd_monthly if current is not None else None
        cur = current_base if current_base is not None else Decimal("0")
        target_base = target.base_price_usd_monthly
        tgt = target_base if target_base is not None else Decimal("0")
        if tgt <= cur:  # target != current already established above -> a downgrade/lateral
            raise CheckoutError.plan_downgrade_not_self_serve()

    def _parse_amount(self, raw: str) -> Decimal:
        try:
            parsed = Decimal(raw)
        except (InvalidOperation, ValueError, TypeError):
            raise CheckoutError.amount_invalid() from None
        if not parsed.is_finite() or parsed <= Decimal("0"):
            raise CheckoutError.amount_invalid()
        if parsed > self._topup_max:
            raise CheckoutError.amount_exceeds_max()
        return parsed

    # ------------------------------------------------------------- small reads
    async def _require_tenant(self, s: AsyncSession, tenant_id: uuid.UUID) -> TenantRow:
        row = (
            await s.execute(select(TenantRow).where(TenantRow.id == tenant_id))
        ).scalar_one_or_none()
        if row is None:  # pragma: no cover — the JWT tenant always exists
            raise CheckoutError.checkout_not_found()
        return row

    async def _require_plan(self, s: AsyncSession, plan_id: uuid.UUID) -> PlanRow:
        row = await self._load_plan(s, plan_id)
        if row is None:
            raise CheckoutError.plan_not_found()
        return row

    async def _load_plan(self, s: AsyncSession, plan_id: uuid.UUID | None) -> PlanRow | None:
        if plan_id is None:
            return None
        return (await s.execute(select(PlanRow).where(PlanRow.id == plan_id))).scalar_one_or_none()

    async def _balance(self, s: AsyncSession, tenant_id: uuid.UUID) -> Decimal:
        row = (
            await s.execute(
                text("SELECT balance_usd FROM tenant_credit_balances WHERE tenant_id = :t"),
                {"t": str(tenant_id)},
            )
        ).fetchone()
        return Decimal(str(row[0])) if row is not None else Decimal("0")

    # ------------------------------------------------------------- misc helpers
    @staticmethod
    def _intent_payload(intent: CheckoutIntent) -> dict[str, Any]:
        if intent.intent_type == "plan_upgrade":
            return {"target_plan_id": str(intent.target_plan_id)}
        payload: dict[str, Any] = {"amount_usd": str(intent.amount_usd)}
        if intent.note is not None:
            payload["note"] = intent.note
        return payload

    @staticmethod
    def _payload_matches(existing: CheckoutSessionRow, intent: CheckoutIntent) -> bool:
        if existing.intent_type != intent.intent_type:
            return False
        if intent.intent_type == "plan_upgrade":
            return str(existing.intent_payload.get("target_plan_id")) == str(intent.target_plan_id)
        return Decimal(str(existing.intent_payload.get("amount_usd"))) == intent.amount_usd

    @staticmethod
    def _plan_preview(current: PlanRow | None, target: PlanRow) -> dict[str, Any]:
        current_base = current.base_price_usd_monthly if current is not None else None
        target_base = target.base_price_usd_monthly
        delta = (target_base if target_base is not None else Decimal("0")) - (
            current_base if current_base is not None else Decimal("0")
        )
        return {
            "intent": "plan_upgrade",
            "current_plan": current.name if current is not None else None,
            "target_plan": target.name,
            "current_base_usd": _money2(current_base),
            "target_base_usd": _money2(target_base),
            "delta_usd": _money2(delta),
            "currency": "USD",
            "effective": "immediate",
        }


__all__ = ["CheckoutService", "ConfirmOutcome", "CreateOutcome", "SessionView"]
