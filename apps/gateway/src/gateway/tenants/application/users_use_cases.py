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

from gateway.tenants.domain.entities import Role, User
from gateway.tenants.domain.errors import EscalationForbiddenError, UserNotFoundError
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
        # SELF-GUARD (always, even for owner)
        if caller_user_id == target_user_id:
            raise EscalationForbiddenError("Cannot change your own role")

        # ESCALATION GUARD
        if caller_role == Role.ADMIN and new_role not in _ADMIN_ASSIGNABLE:
            raise EscalationForbiddenError(
                f"Admin cannot assign role '{new_role}' (escalation forbidden)"
            )

        # Load the target user (also validates tenant membership)
        user = await self._repo.get_by_id_and_tenant(user_id=target_user_id, tenant_id=tenant_id)
        if user is None:
            raise UserNotFoundError(f"User {target_user_id} not found in tenant {tenant_id}")

        updated = await self._repo.update_role(
            user_id=target_user_id, tenant_id=tenant_id, new_role=new_role
        )

        return updated
