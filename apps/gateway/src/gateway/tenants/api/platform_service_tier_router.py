"""Superadmin API router for the FLEET-WIDE service-tier capacity configuration.

Contract FROZEN @ v1, amended (service-tiers TASK.md §3), mirrors
platform_tenant_config_router.py's Depends(require_superadmin) gate +
platform_tenants_router.py's no-target-tenant bulk-route shape:

  GET  /admin/platform/service-tiers
    -> {cluster_cap: int, priority_reserved_pct: string, standard_reserved_pct: string}

  PUT  /admin/platform/service-tiers
    body: {cluster_cap?: int, priority_reserved_pct?: number, standard_reserved_pct?: number}
    200 -> {cluster_cap: int, priority_reserved_pct: string, standard_reserved_pct: string}
    403 -> problem+json "ERR_AUTH_FORBIDDEN"   (caller is not SUPERADMIN)
    422 -> problem+json   (cluster_cap < 0 | either pct negative | pct sum > 1.0 | non-numeric)

FLEET-WIDE, no target tenant_id (mirrors platform_tenants_router.py's bulk-list
precedent: a role-only check, deliberately NOT authorize_tenant_scope). Reads/writes
the live app.state-boot singletons directly — Settings (the display-of-record GET
reads) AND, when a RedisTierCapacityGuard is wired, its own in-memory pool caps via
.reconfigure() — so a write takes effect IMMEDIATELY for the very next admission
decision, no worker restart (§3). In-flight holds are unaffected (RedisTierCapacityGuard
.reconfigure()'s own docstring). A PassthroughTierCapacityGuard (tiering unconfigured)
has no pool caps to reconfigure — the write still updates Settings (so GET reflects it
and a later-wired RedisTierCapacityGuard picks it up at ITS OWN construction time), but
has no live admission effect until an operator actually wires tiering.

Absent request fields = no change (mirrors the three-state PATCH convention's
"absent = no change" half — this route has no "clear" state, every field is always
either kept or replaced, never nulled).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, model_validator

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.error_catalog import PAYLOAD_TIER_PCT_SUM_INVALID
from gateway.tenants.domain.authz import require_superadmin
from gateway.tenants.domain.entities import Identity

platform_service_tier_router = APIRouter(
    prefix="/admin/platform/service-tiers", tags=["platform-admin"]
)


class PlatformServiceTiersResponse(BaseModel):
    cluster_cap: int
    priority_reserved_pct: str
    standard_reserved_pct: str


class PlatformServiceTiersPutRequest(BaseModel):
    """PUT body — all fields optional; absent = no change (never a "clear" state)."""

    cluster_cap: int | None = Field(default=None, ge=0)
    priority_reserved_pct: Decimal | None = Field(default=None, ge=0)
    standard_reserved_pct: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _validate_pct_sum(self) -> PlatformServiceTiersPutRequest:
        """422 (R5) — the live-write path REJECTS a >1.0 sum, unlike the env-var boot
        path (core/config.py's validator), which coerces + WARNs instead (§3: env
        misconfiguration cannot block startup, but an explicit live operator write can
        and should fail loudly)."""
        if self.priority_reserved_pct is not None and self.standard_reserved_pct is not None:
            if self.priority_reserved_pct + self.standard_reserved_pct > 1:
                raise PAYLOAD_TIER_PCT_SUM_INVALID.exc()
        return self


@platform_service_tier_router.get("", response_model=PlatformServiceTiersResponse)
async def get_platform_service_tiers(
    request: Request,
    identity: Annotated[Identity, Depends(require_superadmin)],
) -> PlatformServiceTiersResponse:
    """Return the live FLEET-WIDE tier-capacity configuration."""
    settings = request.app.state.settings
    return PlatformServiceTiersResponse(
        cluster_cap=int(settings.tier_capacity_cluster_cap),
        priority_reserved_pct=str(settings.tier_priority_reserved_pct),
        standard_reserved_pct=str(settings.tier_standard_reserved_pct),
    )


@platform_service_tier_router.put("", response_model=PlatformServiceTiersResponse)
async def put_platform_service_tiers(
    request: Request,
    body: PlatformServiceTiersPutRequest,
    identity: Annotated[Identity, Depends(require_superadmin)],
) -> PlatformServiceTiersResponse:
    """Live-mutate the FLEET-WIDE tier-capacity configuration — no worker restart.

    Merges the PUT body's present fields over the current live values (absent = no
    change), validates the merged combination (this route's own 422s — the sum>1.0
    check already ran in the body's model_validator above; a merged sum could still
    exceed 1.0 when only ONE field is supplied against an already-invalid partner, so
    the merged combination is re-checked here too), then writes BOTH the Settings
    singleton (GET's display-of-record) and, when a RedisTierCapacityGuard is wired,
    its own live pool caps via .reconfigure() (duck-typed — a PassthroughTierCapacity
    Guard has no such method and is left untouched).
    """
    settings = request.app.state.settings
    new_cluster_cap = (
        body.cluster_cap if body.cluster_cap is not None else settings.tier_capacity_cluster_cap
    )
    new_priority_pct = (
        float(body.priority_reserved_pct)
        if body.priority_reserved_pct is not None
        else settings.tier_priority_reserved_pct
    )
    new_standard_pct = (
        float(body.standard_reserved_pct)
        if body.standard_reserved_pct is not None
        else settings.tier_standard_reserved_pct
    )
    if new_priority_pct + new_standard_pct > 1.0:
        raise PAYLOAD_TIER_PCT_SUM_INVALID.exc()

    settings.tier_capacity_cluster_cap = new_cluster_cap
    settings.tier_priority_reserved_pct = new_priority_pct
    settings.tier_standard_reserved_pct = new_standard_pct

    guard = getattr(request.app.state, "tier_capacity_guard", None)
    reconfigure = getattr(guard, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(
            cluster_cap=new_cluster_cap,
            priority_reserved_pct=new_priority_pct,
            standard_reserved_pct=new_standard_pct,
        )

    # Audit emit — fail-open fire-and-forget (mirrors teams/api/router.py add_member's own
    # idiom exactly). FLEET-WIDE config has no single target tenant — tenant_id=None,
    # mirroring platform_tenants_router.py's own "platform.tenant.list" system-level-event
    # precedent (AuditEvent permits tenant_id=None as long as an actor is still attributed).
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=None,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="platform.service_tier.update",
                target_type="service_tier",
                target_id="config",
                result="success",
                metadata={
                    "cluster_cap": int(new_cluster_cap),
                    "priority_reserved_pct": str(new_priority_pct),
                    "standard_reserved_pct": str(new_standard_pct),
                },
                created_at=datetime.now(UTC),
            ),
        )
    )

    return PlatformServiceTiersResponse(
        cluster_cap=int(new_cluster_cap),
        priority_reserved_pct=str(new_priority_pct),
        standard_reserved_pct=str(new_standard_pct),
    )


__all__ = ["platform_service_tier_router"]
