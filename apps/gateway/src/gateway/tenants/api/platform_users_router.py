"""FastAPI router for the superadmin cross-tenant members surface
(cross-tenant-keys-members TASK.md §3 — FROZEN @ v1).

Endpoints (all under /admin/platform/tenants/{tenant_id}/users):
  GET  ""              — list the target tenant's complete user/member roster
  PUT  "/{user_id}/role" — reassign a role to a user owned by the target tenant

Security (server-side, authoritative), identical on every route, in this order (M9):
  1. require_superadmin (Depends)                       — role-only gate, no target
  2. authorize_tenant_scope(identity, tenant_id)         — cheap in-memory check (same disclosed
     redundancy as platform_tenants_router.py / platform_keys_router.py: this whole tree is
     SUPERADMIN-only, so authorize_tenant_scope's reject-branch is unreachable here today, but
     it's the semantically correct predicate for "may I act on tenant_id")
  3. get_tenant_by_id(session, tenant_id) -> 404 TENANT_NOT_FOUND if the target tenant_id does
     not resolve to a real row — BEFORE any users query (uniform pre-check, §1's Framings-weighed
     decision; the assign-role route gets an equivalent 404 for free via UserNotFoundError, but
     the pre-check is applied uniformly regardless)

Both use-case calls below are parametrized by the PATH tenant_id — NEVER identity.tenant_id (the
superadmin's own platform tenant_id). ListTenantUsersUseCase/AssignUserRoleUseCase are called
UNCHANGED from users_use_cases.py — this router adds ZERO new business logic, only wiring.

A user_id belonging to a DIFFERENT tenant than the path 404s IDENTICALLY to an unknown user_id
(UserNotFoundError, no distinguishing detail) — enforced by
UserRoleRepository.get_by_id_and_tenant's existing tenant-filtered lookup, now fed the PATH
tenant_id instead of identity.tenant_id.

role == "superadmin" (or any literal that fails to parse as a Role) is hard-rejected with 422
ERR_PAYLOAD_INVALID BEFORE AssignUserRoleUseCase is invoked — the pre-check below is a VERBATIM
replica of users_router.py's own (L116-129), including the exact `detail` string, so the
rejection is byte-identical to self-service's for the same payload (M8).

No audit wiring yet — deferred entirely to admin-console-audit (task 4, depends-on this task;
§3 Least-sure flag). No invite/remove-member route exists here (M12 — self-service itself offers
nothing more than list + reassign-role).

UserResponse/UsersListResponse/AssignRoleRequest are re-declared locally (tiny Pydantic models)
rather than imported from users_router.py — avoids a router-module-to-router-module import for
DTOs that are trivial to redeclare; not contract-binding which container holds them (§3).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.platform_audit import emit_platform_audit
from gateway.core.db import get_session
from gateway.core.error_catalog import (
    LAST_BILLING_OWNER,
    PAYLOAD_INVALID,
    TENANT_NOT_FOUND,
    USER_NOT_FOUND,
)
from gateway.tenants.application.users_use_cases import (
    AssignUserRoleUseCase,
    ListTenantUsersUseCase,
)
from gateway.tenants.domain.authz import authorize_tenant_scope, require_superadmin
from gateway.tenants.domain.entities import Identity, Role
from gateway.tenants.domain.errors import (
    EscalationForbiddenError,
    LastBillingOwnerError,
    UserNotFoundError,
)
from gateway.tenants.infrastructure.repository import get_tenant_by_id
from gateway.tenants.infrastructure.users_repository import UserRoleRepository

platform_users_router = APIRouter(
    prefix="/admin/platform/tenants/{tenant_id}/users", tags=["platform-admin"]
)


# ---------------------------------------------------------------------------
# Schemas (re-declared verbatim from tenants/api/users_router.py — see module docstring)
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


async def _require_target_tenant(
    identity: Identity, tenant_id: uuid.UUID, session: AsyncSession
) -> None:
    """M9: authorize_tenant_scope THEN tenant-existence check — BEFORE any users query."""
    authorize_tenant_scope(identity, tenant_id)
    row = await get_tenant_by_id(session, tenant_id)
    if row is None:
        raise TENANT_NOT_FOUND.exc()


# ---------------------------------------------------------------------------
# GET /admin/platform/tenants/{tenant_id}/users
# ---------------------------------------------------------------------------


@platform_users_router.get("", response_model=UsersListResponse)
async def list_platform_tenant_users(
    tenant_id: uuid.UUID,
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    repo: Annotated[UserRoleRepository, Depends(_get_repo)],
    request: Request,
) -> UsersListResponse:
    """GET .../users — list the target tenant's complete user/member roster via
    ListTenantUsersUseCase verbatim (tenant_id = PATH value). SUPERADMIN only.
    """
    await _require_target_tenant(identity, tenant_id, session)
    use_case = ListTenantUsersUseCase(repo)
    users = await use_case.execute(tenant_id=tenant_id)
    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=tenant_id,
        action="platform.user.list",
        target_type="user",
        target_id=None,
        metadata={},
    )
    return UsersListResponse(
        users=[UserResponse(id=u.id, email=u.email, role=u.role.value) for u in users]
    )


# ---------------------------------------------------------------------------
# PUT /admin/platform/tenants/{tenant_id}/users/{user_id}/role
# ---------------------------------------------------------------------------


@platform_users_router.put("/{user_id}/role", response_model=UserResponse)
async def assign_platform_tenant_user_role(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    body: AssignRoleRequest,
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    repo: Annotated[UserRoleRepository, Depends(_get_repo)],
    request: Request,
) -> UserResponse:
    """PUT .../users/{user_id}/role — assign a new role to a user owned by the target tenant
    (tenant_id = PATH value) via AssignUserRoleUseCase verbatim (caller_user_id/caller_role =
    identity.user_id/identity.role — the SUPERADMIN caller's OWN identity, since that use-case's
    caller_* params always mean "who is acting", never "which tenant" — the tenant_id param
    covers that). A user_id belonging to a different tenant 404s identically (UserNotFoundError).
    role == "superadmin" (or unparseable) 422s BEFORE the use-case runs, byte-identical to
    self-service (M8). Also fetches the user's OLD role BEFORE reassignment (mirrors
    users_router.py's own self-service pattern) to populate old_role in the audit metadata
    (admin-console-audit TASK.md §3 M7/M10b, FROZEN @ v1). SUPERADMIN only.
    """
    await _require_target_tenant(identity, tenant_id, session)

    try:
        new_role = Role(body.role)
    except ValueError:
        raise PAYLOAD_INVALID.exc(detail=f"Unknown role: {body.role!r}") from None

    # M8: byte-identical to users_router.py's own pre-check (L121-129) — superadmin is never
    # assignable via this path either, rejected BEFORE AssignUserRoleUseCase runs so the shape
    # matches self-service's rejection of the same payload exactly (422, not a 403 via the
    # use-case's own internal escalation guard).
    if new_role == Role.SUPERADMIN:
        raise PAYLOAD_INVALID.exc(detail=f"Unknown role: {body.role!r}") from None

    # admin-console-audit M10b: fetch the OLD role BEFORE reassignment — mirrors
    # users_router.py's own self-service precedent (L131-133) — tenant_id here is the PATH
    # value (the target tenant), never identity.tenant_id (the superadmin's own tenant).
    old_user = await repo.get_by_id_and_tenant(user_id=user_id, tenant_id=tenant_id)
    old_role_str = old_user.role.value if old_user else None

    use_case = AssignUserRoleUseCase(repo)
    try:
        updated = await use_case.execute(
            caller_user_id=identity.user_id,
            caller_role=identity.role,
            tenant_id=tenant_id,
            target_user_id=user_id,
            new_role=new_role,
        )
    except EscalationForbiddenError:
        # Defense-in-depth only (superadmin already excluded above; self-guard is a UUID
        # coincidence between the caller's own user_id and the target — astronomically
        # unlikely across tenants, but handled identically to self-service regardless).
        from gateway.core.error_catalog import AUTH_FORBIDDEN

        raise AUTH_FORBIDDEN.exc() from None
    except UserNotFoundError:
        raise USER_NOT_FOUND.exc() from None
    except LastBillingOwnerError:
        # billing-owner-of-record TASK.md §3 (R8): identical to self-service's own
        # rejection — confirms AssignUserRoleUseCase's single hook point covers this
        # superadmin cross-tenant surface too, no bypass.
        raise LAST_BILLING_OWNER.exc() from None

    # NOTE (verified during admin-console-audit's own build — see TASK.md §7 OBSERVE): this
    # comment previously claimed UserRoleRepository.update_role only calls session.flush(),
    # never session.commit(). A direct read of users_repository.py:50-70 (as of this build)
    # shows update_role DOES call self._session.commit() internally (after its own flush +
    # re-select) — so this router's own explicit commit() below is a harmless redundant
    # second commit, not a fix for a still-open gap. Left in place unchanged (removing it is
    # a separate behavior question, out of this task's audit-only mandate) — the audit call
    # below still correctly lands AFTER both commits, on the success path only.
    await session.commit()

    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=tenant_id,
        action="platform.user.role_assign",
        target_type="user",
        target_id=str(user_id),
        metadata={
            "target_user_id": str(user_id),
            "old_role": old_role_str,
            "new_role": updated.role.value,
        },
    )

    return UserResponse(id=updated.id, email=updated.email, role=updated.role.value)
