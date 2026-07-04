"""FastAPI router for tenant user role management (rbac-admin-ui TASK.md §3).

Endpoints:
  GET  /admin/users                       — list caller's tenant users (MEMBERS_MANAGE)
  PUT  /admin/users/{user_id}/role        — assign a role (MEMBERS_MANAGE + escalation + self-guard)

Security (server-side, authoritative):
  - require_permission(MEMBERS_MANAGE) → only owner/admin pass; others get 403.
  - SELF-GUARD enforced in use-case: caller cannot change their own role.
  - ESCALATION GUARD enforced in use-case: admin cannot assign owner/admin.
  - Tenant isolation enforced in repository: only the caller's tenant users.
  - Audit: every successful role change fires user.role_assign (fail-open).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.db import get_session
from gateway.core.error_catalog import AUTH_FORBIDDEN, PAYLOAD_INVALID, USER_NOT_FOUND
from gateway.tenants.application.users_use_cases import (
    AssignUserRoleUseCase,
    ListTenantUsersUseCase,
)
from gateway.tenants.domain.authz import Permission, require_permission
from gateway.tenants.domain.entities import Identity, Role
from gateway.tenants.domain.errors import EscalationForbiddenError, UserNotFoundError
from gateway.tenants.infrastructure.users_repository import UserRoleRepository

users_router = APIRouter(prefix="/admin/users", tags=["users"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: str


class UsersListResponse(BaseModel):
    users: list[UserResponse]


class AssignRoleRequest(BaseModel):
    role: str


# ---------------------------------------------------------------------------
# Dependency factories
# ---------------------------------------------------------------------------


def _get_repo(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UserRoleRepository:
    return UserRoleRepository(session)


# ---------------------------------------------------------------------------
# GET /admin/users
# ---------------------------------------------------------------------------


@users_router.get("", response_model=UsersListResponse)
async def list_users(
    identity: Annotated[Identity, require_permission(Permission.MEMBERS_MANAGE)],
    repo: Annotated[UserRoleRepository, Depends(_get_repo)],
) -> UsersListResponse:
    """GET /admin/users — list all users in the caller's tenant.

    Requires MEMBERS_MANAGE (owner/admin only). Returns tenant-scoped list.
    """
    use_case = ListTenantUsersUseCase(repo)
    users = await use_case.execute(tenant_id=identity.tenant_id)
    return UsersListResponse(
        users=[UserResponse(id=u.id, email=u.email, role=u.role.value) for u in users]
    )


# ---------------------------------------------------------------------------
# PUT /admin/users/{user_id}/role
# ---------------------------------------------------------------------------


@users_router.put("/{user_id}/role", response_model=UserResponse)
async def assign_user_role(
    request: Request,
    user_id: uuid.UUID,
    body: AssignRoleRequest,
    identity: Annotated[Identity, require_permission(Permission.MEMBERS_MANAGE)],
    repo: Annotated[UserRoleRepository, Depends(_get_repo)],
) -> UserResponse:
    """PUT /admin/users/{user_id}/role — assign a role to a tenant user.

    Security (all enforced server-side):
      - MEMBERS_MANAGE gating (owner/admin only).
      - SELF-GUARD: caller cannot change their own role → 403.
      - ESCALATION GUARD: admin cannot assign owner/admin → 403.
      - Tenant isolation: target must be in caller's tenant → 404.
      - Validation: role must be a valid Role value → 400.
    """
    # Validate the role value before hitting the use-case
    try:
        new_role = Role(body.role)
    except ValueError:
        raise PAYLOAD_INVALID.exc(detail=f"Unknown role: {body.role!r}") from None

    # superadmin-role TASK.md §3: "superadmin" is a VALID Role member (unlike a truly
    # unknown literal), so Role(body.role) above no longer raises ValueError for it —
    # it must be rejected here, explicitly, with the SAME 422 ERR_PAYLOAD_INVALID shape
    # as any unparseable role literal. Never assignable via this generic, unaudited
    # endpoint, for EVERY caller regardless of role/tenant (never routed through the
    # escalation-guard 403 path, or test_users_role.py::test_role_validation's existing
    # 422 assertion for this exact payload would break).
    if new_role == Role.SUPERADMIN:
        raise PAYLOAD_INVALID.exc(detail=f"Unknown role: {body.role!r}") from None

    # Load old role for audit metadata (before update)
    old_user = await repo.get_by_id_and_tenant(user_id=user_id, tenant_id=identity.tenant_id)
    old_role_str = old_user.role.value if old_user else None

    use_case = AssignUserRoleUseCase(repo)
    try:
        updated = await use_case.execute(
            caller_user_id=identity.user_id,
            caller_role=identity.role,
            tenant_id=identity.tenant_id,
            target_user_id=user_id,
            new_role=new_role,
        )
    except EscalationForbiddenError:
        raise AUTH_FORBIDDEN.exc() from None
    except UserNotFoundError:
        raise USER_NOT_FOUND.exc() from None

    # Audit — fire-and-forget, fail-open
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=identity.tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="user.role_assign",
                target_type="user",
                target_id=str(user_id),
                result="success",
                metadata={
                    "target_user_id": str(user_id),
                    "old_role": old_role_str,
                    "new_role": updated.role.value,
                },
                created_at=datetime.now(UTC),
            ),
        )
    )

    return UserResponse(id=updated.id, email=updated.email, role=updated.role.value)
