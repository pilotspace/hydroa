"""FastAPI router for the billing-owner-of-record designation (billing-owner-of-record
TASK.md §3 CONTRACT — FROZEN @ v1).

Endpoints:
  GET /admin/billing-owner — any authenticated role, own-tenant only (M6). Returns the
                              current designation, or {user_id,email,role}=null for the
                              reserved platform tenant / an unresolved backfill edge.
  PUT /admin/billing-owner — requires Permission.SECURITY_CONFIG (OWNER only); body
                              {user_id}; the ONLY sanctioned way to change
                              billing_owner_user_id (M5).

Security (server-side, authoritative):
  - PUT requires Permission.SECURITY_CONFIG (OWNER only) — REUSED, no new Permission enum
    member (mirrors retention_policy_router.py / residency_policy_router.py's own
    documented convention for this exact shape: singleton, own-tenant-only, OWNER-only).
  - Both routes operate ONLY on identity.tenant_id — no path/body tenant param exists,
    structurally impossible to target another tenant (R6/R5).
  - Cross-tenant / unknown target user_id -> 404 ERR_USER_NOT_FOUND, byte-identical (no
    oracle) — reuses UserRoleRepository.get_by_id_and_tenant's existing tenant-filtered,
    confused-deputy-safe lookup.
  - The reassignment write is enclosed in the M4 FOR-UPDATE lock (ReassignBillingOwnerUseCase),
    serializing against HOOK 1 (role-change) and HOOK 2 (deactivation) — closes the R9 race.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.error_catalog import BILLING_OWNER_INELIGIBLE, USER_NOT_FOUND
from gateway.keys.api.deps import get_identity
from gateway.tenants.application.billing_owner_use_cases import (
    GetBillingOwnerUseCase,
    ReassignBillingOwnerUseCase,
)
from gateway.tenants.domain.authz import Permission, require_permission
from gateway.tenants.domain.entities import Identity
from gateway.tenants.domain.errors import BillingOwnerIneligibleError, UserNotFoundError
from gateway.tenants.infrastructure.users_repository import UserRoleRepository

billing_owner_router = APIRouter(prefix="/admin/billing-owner", tags=["billing-owner"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class BillingOwnerResponse(BaseModel):
    user_id: uuid.UUID | None
    email: str | None
    role: str | None


class ReassignBillingOwnerRequest(BaseModel):
    user_id: uuid.UUID


# ---------------------------------------------------------------------------
# Dependency factories
# ---------------------------------------------------------------------------


def _get_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserRoleRepository:
    return UserRoleRepository(session)


# ---------------------------------------------------------------------------
# GET /admin/billing-owner
# ---------------------------------------------------------------------------


@billing_owner_router.get("", response_model=BillingOwnerResponse)
async def get_billing_owner(
    identity: Annotated[Identity, Depends(get_identity)],
    repo: Annotated[UserRoleRepository, Depends(_get_repo)],
) -> BillingOwnerResponse:
    """GET /admin/billing-owner — any authenticated role, own-tenant only (M6)."""
    use_case = GetBillingOwnerUseCase(repo)
    owner = await use_case.execute(tenant_id=identity.tenant_id)
    if owner is None:
        return BillingOwnerResponse(user_id=None, email=None, role=None)
    return BillingOwnerResponse(user_id=owner.id, email=owner.email, role=owner.role.value)


# ---------------------------------------------------------------------------
# PUT /admin/billing-owner
# ---------------------------------------------------------------------------


@billing_owner_router.put("", response_model=BillingOwnerResponse)
async def put_billing_owner(
    body: ReassignBillingOwnerRequest,
    identity: Annotated[Identity, require_permission(Permission.SECURITY_CONFIG)],
    repo: Annotated[UserRoleRepository, Depends(_get_repo)],
) -> BillingOwnerResponse:
    """PUT /admin/billing-owner — OWNER only (Permission.SECURITY_CONFIG), own-tenant
    only (M5).

    403 ERR_AUTH_FORBIDDEN — non-OWNER caller (R6, via require_permission — zero new code).
    404 ERR_USER_NOT_FOUND — absent OR cross-tenant target (R5).
    422 ERR_BILLING_OWNER_INELIGIBLE — target not active + billing-capable (R3/R4).
    200 — the new (or unchanged, idempotent no-op) billing owner.
    """
    use_case = ReassignBillingOwnerUseCase(repo)
    try:
        owner = await use_case.execute(tenant_id=identity.tenant_id, user_id=body.user_id)
    except UserNotFoundError:
        raise USER_NOT_FOUND.exc() from None
    except BillingOwnerIneligibleError:
        raise BILLING_OWNER_INELIGIBLE.exc() from None
    return BillingOwnerResponse(user_id=owner.id, email=owner.email, role=owner.role.value)
