"""Use-cases for tenant user management (rbac-admin-ui TASK.md §3).

Provides:
  - ListTenantUsersUseCase: GET /admin/users — list all users in the caller's tenant.
  - AssignUserRoleUseCase: PUT /admin/users/{id}/role — update a user's role with
    escalation guard + self-guard enforcement.

These are pure application-layer objects; all security logic lives here (server-side
enforcement per the frozen contract). The router calls them after MEMBERS_MANAGE gating.
"""

from __future__ import annotations

import uuid

from gateway.tenants.domain.entities import BILLING_CAPABLE_ROLES, Role, User
from gateway.tenants.domain.errors import (
    EscalationForbiddenError,
    LastBillingOwnerError,
    UserNotFoundError,
)
from gateway.tenants.infrastructure.users_repository import UserRoleRepository

# ---------------------------------------------------------------------------
# Escalation policy (FROZEN @ v1, approved by Tin 2026-06-25)
# ---------------------------------------------------------------------------
# owner may assign any of the 6 tiers
# admin may assign ONLY {operator, billing_admin, viewer, member} — NOT owner/admin
# ---------------------------------------------------------------------------
_ADMIN_ASSIGNABLE: frozenset[Role] = frozenset(
    {Role.OPERATOR, Role.BILLING_ADMIN, Role.VIEWER, Role.MEMBER}
)


def assert_role_within_ceiling(*, caller_role: Role, target_role: Role) -> None:
    """Enforce the per-caller-role escalation ceiling (member-invite-issuance TASK.md §3
    NEW shared symbol) — extracted VERBATIM from the SUPERADMIN GUARD + ESCALATION GUARD
    previously inlined in AssignUserRoleUseCase.execute (ground: ex-lines 64-82, pre-
    extraction) so role-reassignment (AssignUserRoleUseCase) and invite-issuance
    (CreateInviteUseCase) can never silently drift apart — ONE shared predicate, never a
    second hand-rolled copy of ``_ADMIN_ASSIGNABLE`` (§1 Framings weighed; §0 GROUND names
    this drift as a privilege-escalation-shaped bug, not a style nit).

    - SUPERADMIN GUARD (first, unconditional): ``target_role == SUPERADMIN`` is NEVER
      assignable through this predicate, for ANY ``caller_role`` — not even OWNER. Defense-
      in-depth: both routers (users_router.py, invites_router.py, platform_users_router.py)
      already reject a ``{"role": "superadmin"}`` payload with 422 ERR_PAYLOAD_INVALID
      BEFORE either use case is ever reached; this guard exists for any future non-HTTP
      caller of either use case, so even an OWNER caller can never mint a superadmin
      through this path.
    - ESCALATION GUARD: OWNER may grant any of the other 6 self-service tiers (no ceiling);
      ADMIN may grant only ``_ADMIN_ASSIGNABLE`` (operator/billing_admin/viewer/member).

    Raises:
        EscalationForbiddenError: ``target_role`` is SUPERADMIN, or ``caller_role`` is
            ADMIN and ``target_role`` is outside ``_ADMIN_ASSIGNABLE``.

    Behavior-preserving for the existing ``PUT /admin/users/{id}/role`` endpoint (and its
    superadmin-caller variant on platform_users_router.py): both routers'
    ``except EscalationForbiddenError: raise AUTH_FORBIDDEN.exc()`` never surface this
    error's internal message, so bundling these two checks ahead of the (still-local)
    self-guard changes zero observable HTTP responses for either endpoint.
    """
    # SUPERADMIN GUARD (first, unconditional — see docstring).
    if target_role == Role.SUPERADMIN:
        raise EscalationForbiddenError("superadmin is not assignable via this use case")

    # ESCALATION GUARD
    if caller_role == Role.ADMIN and target_role not in _ADMIN_ASSIGNABLE:
        raise EscalationForbiddenError(
            f"Admin cannot assign role '{target_role}' (escalation forbidden)"
        )


class ListTenantUsersUseCase:
    """Return all users in the caller's tenant (id, email, role)."""

    def __init__(self, repo: UserRoleRepository) -> None:
        self._repo = repo

    async def execute(self, *, tenant_id: uuid.UUID) -> list[User]:
        return await self._repo.list_by_tenant(tenant_id=tenant_id)


class AssignUserRoleUseCase:
    """Assign a new role to a target user, enforcing escalation + self-guard.

    Invariants (all enforced SERVER-SIDE):
      - SELF-GUARD: caller_user_id == target_user_id → EscalationForbiddenError
      - OWNER: may assign any of the 6 Role values
      - ADMIN: may assign only {operator, billing_admin, viewer, member}
               → assigning owner/admin → EscalationForbiddenError
      - target user must exist in the caller's tenant → UserNotFoundError
    """

    def __init__(self, repo: UserRoleRepository) -> None:
        self._repo = repo

    async def execute(
        self,
        *,
        caller_user_id: uuid.UUID,
        caller_role: Role,
        tenant_id: uuid.UUID,
        target_user_id: uuid.UUID,
        new_role: Role,
    ) -> User:
        # SUPERADMIN GUARD + ESCALATION GUARD — shared predicate (member-invite-issuance
        # TASK.md §3), called BEFORE the self-guard so the superadmin-guard stays
        # unconditional and first-checked (even an OWNER caller — who has no other
        # restriction on this endpoint — can never mint a superadmin through this path;
        # preserves the original guard's ordering intent verbatim). Also called from
        # CreateInviteUseCase.execute so the two ceilings can never silently drift apart.
        assert_role_within_ceiling(caller_role=caller_role, target_role=new_role)

        # SELF-GUARD (always, even for owner) — stays local; has no meaning for
        # invite-creation (an invite has no pre-existing target_user_id to compare against).
        if caller_user_id == target_user_id:
            raise EscalationForbiddenError("Cannot change your own role")

        # Load the target user (also validates tenant membership)
        user = await self._repo.get_by_id_and_tenant(user_id=target_user_id, tenant_id=tenant_id)
        if user is None:
            raise UserNotFoundError(f"User {target_user_id} not found in tenant {tenant_id}")

        # billing-owner-of-record TASK.md §3 M2/M4 — HOOK 1: lock the tenants row FOR
        # UPDATE (the SAME lock HOOK 2 and the reassignment endpoint acquire, closing the
        # R9 race) THEN reject a role change that would leave the tenant's CURRENT billing
        # owner non-billing-capable. Runs AFTER the self-guard + tenant-membership check,
        # BEFORE update_role (§3 CONTRACT order, verbatim).
        billing_owner_user_id = await self._repo.lock_and_get_billing_owner_user_id(
            tenant_id=tenant_id
        )
        if target_user_id == billing_owner_user_id and new_role not in BILLING_CAPABLE_ROLES:
            raise LastBillingOwnerError(
                f"User {target_user_id} is tenant {tenant_id}'s billing owner; cannot "
                f"assign non-billing-capable role {new_role!r}"
            )

        updated = await self._repo.update_role(
            user_id=target_user_id, tenant_id=tenant_id, new_role=new_role
        )

        return updated
