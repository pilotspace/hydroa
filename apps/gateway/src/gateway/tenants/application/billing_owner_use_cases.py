"""Use-cases for the billing-owner-of-record designation (billing-owner-of-record
TASK.md §3 CONTRACT — FROZEN @ v1).

Provides:
  - ReassignBillingOwnerUseCase: PUT /admin/billing-owner (M5) — OWNER-only reassignment,
    validated against BILLING_CAPABLE_ROLES + active-user, inside the M4 FOR-UPDATE lock.
  - GetBillingOwnerUseCase: GET /admin/billing-owner (M6) — read the current designation.

Pure application-layer objects; all security logic lives here (server-side enforcement
per the frozen contract). The router calls them after SECURITY_CONFIG gating (PUT only —
GET is open to any authenticated tenant member).
"""

from __future__ import annotations

import uuid

from gateway.tenants.domain.entities import BILLING_CAPABLE_ROLES, User
from gateway.tenants.domain.errors import BillingOwnerIneligibleError, UserNotFoundError
from gateway.tenants.infrastructure.users_repository import UserRoleRepository


class ReassignBillingOwnerUseCase:
    """Reassign the tenant's billing-owner-of-record (M5).

    Invariants (all enforced SERVER-SIDE, inside ONE FOR-UPDATE-locked transaction):
      - the M4 lock is acquired FIRST — serializes against HOOK 1/HOOK 2's own
        identically-shaped lock (R9 race-safety).
      - target must exist in the caller's own tenant -> UserNotFoundError (404, R5 —
        confused-deputy-safe: byte-identical to an unknown user_id).
      - target must be ACTIVE (deactivated_at IS NULL) and billing-capable
        (role in BILLING_CAPABLE_ROLES) -> BillingOwnerIneligibleError (422, R3/R4).
      - reassigning to the CURRENT billing owner is an idempotent no-op 200 (the UPDATE
        is a harmless no-op write, never a special-cased skip — same value in, same
        value out).
    """

    def __init__(self, repo: UserRoleRepository) -> None:
        self._repo = repo

    async def execute(self, *, tenant_id: uuid.UUID, user_id: uuid.UUID) -> User:
        # M4: lock the tenants row FIRST, before resolving the target — closes the R9
        # race (concurrent reassign-to-X vs demote-X never both succeed).
        await self._repo.lock_and_get_billing_owner_user_id(tenant_id=tenant_id)

        target = await self._repo.get_by_id_and_tenant(user_id=user_id, tenant_id=tenant_id)
        if target is None:
            raise UserNotFoundError(f"User {user_id} not found in tenant {tenant_id}")

        if target.deactivated_at is not None or target.role not in BILLING_CAPABLE_ROLES:
            raise BillingOwnerIneligibleError(
                f"User {user_id} is not an active, billing-capable member of tenant {tenant_id}"
            )

        await self._repo.set_billing_owner_user_id(tenant_id=tenant_id, user_id=user_id)
        return target


class GetBillingOwnerUseCase:
    """Read the tenant's current billing-owner-of-record designation (M6).

    Returns None when the tenant carries no designation (the reserved platform tenant,
    or an unresolved backfill edge — §1 Assumptions) — the router maps that to
    ``{user_id: null, email: null, role: null}``, never an error.
    """

    def __init__(self, repo: UserRoleRepository) -> None:
        self._repo = repo

    async def execute(self, *, tenant_id: uuid.UUID) -> User | None:
        owner_id = await self._repo.get_billing_owner_user_id(tenant_id=tenant_id)
        if owner_id is None:
            return None
        return await self._repo.get_by_id_and_tenant(user_id=owner_id, tenant_id=tenant_id)
