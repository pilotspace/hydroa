"""Admin API router for per-tenant fail-closed residency policy (region pin).

Contract FROZEN @ v2 (residency-policy TASK.md §3 — CR-1 added `ap` to the region enum):
  GET  /admin/residency-policy — any authenticated role (get_identity); returns the
                                  tenant's current policy.
  PUT  /admin/residency-policy — requires Permission.SECURITY_CONFIG (OWNER only);
                                  body { region: "us" | "eu" | "ap" | null }; sets or
                                  clears the pin. 422 ERR_RESIDENCY_REGION_INVALID on
                                  any other value.

Mirrors retention_policy_router.py's shape+idiom exactly: OWNER-only via the SAME
reused Permission.SECURITY_CONFIG (no new Permission enum member — PROJECT.md DDD
note), fire-and-forget record_audit on every successful write, both operate ONLY on
the caller's own identity.tenant_id (no tenant_id path/query param — structurally
impossible to target another tenant). The confirm-gate is a FRONTEND-ONLY concern
(residency-tiers-ui, sibling task) — this contract carries no `confirm` field,
mirroring ZDR exactly.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.db import get_session
from gateway.core.error_catalog import RESIDENCY_REGION_INVALID, TENANT_NOT_FOUND
from gateway.keys.api.deps import get_identity
from gateway.tenants.domain.authz import Permission, require_permission
from gateway.tenants.domain.entities import Identity

residency_policy_router = APIRouter(prefix="/admin/residency-policy", tags=["residency"])

# The four-value catalog Region Literal minus "global" — a tenant pin is always a
# SPECIFIC region (or unset); "global" is never a valid pin value (M6 — a candidate
# tagged "global" never satisfies ANY specific pin, so pinning TO "global" would be
# a self-contradicting no-op the API refuses to accept).
_VALID_PIN_REGIONS: frozenset[str] = frozenset({"us", "eu", "ap"})


class ResidencyPolicyBody(BaseModel):
    """PUT body — sets or clears the pin. `region` absent is NOT the same as
    `region: null` in the wire shape; both a missing key and an explicit null clear
    the pin (Pydantic model_fields_set is not consulted here — unlike the ZDR
    partial-merge body, this contract's PUT is a full replace, mirrored from §3)."""

    region: str | None = None


class ResidencyPolicyResponse(BaseModel):
    region: str | None
    updated_at: str | None


def _validate_region(region: str | None) -> None:
    if region is not None and region not in _VALID_PIN_REGIONS:
        raise RESIDENCY_REGION_INVALID.exc()


async def _fetch_tenant_row(
    session: AsyncSession, tenant_id: uuid.UUID
) -> tuple[str | None, datetime | None] | None:
    row = (
        await session.execute(
            text(
                "SELECT residency_region, residency_region_updated_at FROM tenants WHERE id = :tid"
            ),
            {"tid": str(tenant_id)},
        )
    ).first()
    if row is None:
        return None
    return (row[0], row[1])


def _build_response(row: tuple[str | None, datetime | None]) -> ResidencyPolicyResponse:
    region, updated_at = row
    return ResidencyPolicyResponse(
        region=region,
        updated_at=updated_at.isoformat() if updated_at is not None else None,
    )


@residency_policy_router.get("", response_model=ResidencyPolicyResponse)
async def get_residency_policy(
    identity: Annotated[Identity, Depends(get_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ResidencyPolicyResponse:
    """GET /admin/residency-policy — any authenticated role (M2)."""
    row = await _fetch_tenant_row(session, identity.tenant_id)
    if row is None:
        raise TENANT_NOT_FOUND.exc()
    return _build_response(row)


@residency_policy_router.put("", response_model=ResidencyPolicyResponse)
async def put_residency_policy(
    body: ResidencyPolicyBody,
    identity: Annotated[Identity, require_permission(Permission.SECURITY_CONFIG)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> ResidencyPolicyResponse:
    """PUT /admin/residency-policy — OWNER only (M3).

    422 ERR_RESIDENCY_REGION_INVALID when region is not one of {null,"us","eu","ap"}
    (R2) — the row is left UNCHANGED and no audit event is recorded. Every successful
    write (set, change, or clear) updates residency_region_updated_at and emits a
    fire-and-forget audit event (action "residency_policy.update"), whether the
    value actually changed or not (mirrors ZDR's set idiom — M3 says "whether the
    region is being set, changed, or cleared").
    """
    _validate_region(body.region)

    tenant_id = identity.tenant_id
    current = await _fetch_tenant_row(session, tenant_id)
    if current is None:
        raise TENANT_NOT_FOUND.exc()

    new_region = body.region
    new_updated_at = datetime.now(UTC)

    await session.execute(
        text(
            "UPDATE tenants SET residency_region = :region,"
            " residency_region_updated_at = :updated_at WHERE id = :tid"
        ),
        {"region": new_region, "updated_at": new_updated_at, "tid": str(tenant_id)},
    )
    await session.commit()

    # Audit emit — fail-open fire-and-forget (mirrors retention_policy_router.py /
    # region_pricing_router.py precedent).
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="residency_policy.update",
                target_type="residency_policy",
                target_id=str(tenant_id),
                result="success",
                metadata={"region": new_region},
                created_at=datetime.now(UTC),
            ),
        )
    )

    return _build_response((new_region, new_updated_at))


__all__: list[str] = ["residency_policy_router"]
