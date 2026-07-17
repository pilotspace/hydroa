"""FastAPI router for the SCIM 2.0 resource surface (scim-provisioning TASK.md §3 Part B).

Endpoints:
  GET    /scim/v2/ServiceProviderConfig
  GET    /scim/v2/ResourceTypes
  GET    /scim/v2/Schemas
  GET    /scim/v2/Users                — filter=userName eq "..." + startIndex/count
  GET    /scim/v2/Users/{id}
  POST   /scim/v2/Users
  PUT    /scim/v2/Users/{id}
  PATCH  /scim/v2/Users/{id}
  DELETE /scim/v2/Users/{id}           — alias for PATCH active:false, never a hard delete
  GET    /scim/v2/Groups               — always an honest empty collection (v1, deferred)
  POST|PUT|PATCH|DELETE /scim/v2/Groups[/*] — 501, no parallel store exists to write to

Every route (including discovery) authenticates via the SCIM bearer token (get_scim_identity)
— decided at freeze (TASK.md §1 ⚠). RFC 7644 SCIM error envelope throughout (see
gateway.scim.api.errors) — a deliberate, documented exception to the project-wide RFC 9457
convention (Part A, /admin/scim/tokens, stays RFC 9457).

Security (server-side, authoritative):
  - Tenant isolation: every read/write is tenant-scoped in the SAME query as existence
    (defense-in-depth); an unknown id and a cross-tenant id are BYTE-IDENTICAL 404s.
  - Role is NEVER read from any SCIM payload (M3) — CreateScimUserUseCase's signature
    structurally cannot accept one; a role/roles PATCH path is silently ignored, never a 400.
  - Rate limiting: every WRITE checks the per-token limiter BEFORE any use case runs — a
    rejected request performs NO mutation.
  - Audit: every mutation (create/update/deactivate/reactivate) fires fail-open,
    actor_scim_token_id set (this surface is machine-authenticated — there is no user
    identity to attribute). Idempotent no-op PATCHes do NOT fire a duplicate audit row.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.error_catalog import LAST_BILLING_OWNER
from gateway.scim.api.deps import get_scim_identity, get_scim_user_repo
from gateway.scim.api.errors import (
    scim_groups_unsupported,
    scim_rate_limited,
    scim_seat_cap_exceeded,
    scim_uniqueness,
    scim_user_not_found,
)
from gateway.scim.api.scim_schemas import (
    ParsedPatch,
    empty_list_response,
    list_response,
    parse_create_user,
    parse_patch_operations,
    parse_put_user,
    parse_username_filter,
    resource_types,
    schemas_list,
    service_provider_config,
    user_to_scim,
)
from gateway.scim.application.token_use_cases import ScimIdentity
from gateway.scim.application.user_use_cases import (
    CreateScimUserUseCase,
    GetScimUserUseCase,
    ListScimUsersUseCase,
    SetScimUserActiveUseCase,
    UpdateScimUserEmailUseCase,
)
from gateway.scim.domain.errors import (
    ScimRateLimitedError,
    ScimUniquenessError,
    ScimUserNotFoundError,
)
from gateway.scim.infrastructure.repository import SqlAlchemyScimUserRepository
from gateway.tenants.domain.entities import User
from gateway.tenants.domain.errors import LastBillingOwnerError, SeatCapExceededError

scim_router = APIRouter(prefix="/scim/v2", tags=["scim"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_user_id(raw: str) -> uuid.UUID:
    """A malformed id string is folded into the same 404 as an unknown id — no
    distinguishing detail leaks whether the id was merely malformed vs. genuinely absent.
    """
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise scim_user_not_found() from None


async def _enforce_write_rate_limit(request: Request, identity: ScimIdentity) -> None:
    """M12 — checked BEFORE any use case runs, so a rejected request performs NO
    mutation. Fail-open when the limiter isn't wired (defensive default for minimal
    test apps that don't install app.state.scim_rate_limiter)."""
    limiter = getattr(request.app.state, "scim_rate_limiter", None)
    if limiter is None:
        return
    settings = request.app.state.settings
    try:
        await limiter.check(
            scim_token_id=str(identity.scim_token_id), limit=settings.scim_write_rpm
        )
    except ScimRateLimitedError as exc:
        raise scim_rate_limited(exc.retry_after) from None


def _audit(
    request: Request,
    *,
    identity: ScimIdentity,
    action: str,
    target_id: uuid.UUID,
    metadata: dict[str, object],
) -> None:
    """Fire-and-forget audit write — actor_scim_token_id set (machine-authenticated
    surface, no user identity exists to attribute)."""
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=identity.tenant_id,
                actor_user_id=None,
                actor_email=None,
                actor_scim_token_id=identity.scim_token_id,
                action=action,
                target_type="user",
                target_id=str(target_id),
                result="success",
                metadata=metadata,
                created_at=datetime.now(UTC),
            ),
        )
    )


async def _apply_patch(
    request: Request,
    identity: ScimIdentity,
    repo: SqlAlchemyScimUserRepository,
    user_id: uuid.UUID,
    parsed: ParsedPatch,
) -> dict[str, Any]:
    user: User | None = None

    if parsed.set_username is not None:
        use_case = UpdateScimUserEmailUseCase(repo)
        try:
            user = await use_case.execute(
                tenant_id=identity.tenant_id, user_id=user_id, email=parsed.set_username
            )
        except ScimUserNotFoundError:
            raise scim_user_not_found() from None
        except ScimUniquenessError:
            raise scim_uniqueness() from None
        _audit(
            request,
            identity=identity,
            action="scim.user_update",
            target_id=user_id,
            metadata={"userName": parsed.set_username},
        )

    if parsed.set_active is not None:
        active_use_case = SetScimUserActiveUseCase(repo)
        try:
            user, changed = await active_use_case.execute(
                tenant_id=identity.tenant_id, user_id=user_id, active=parsed.set_active
            )
        except ScimUserNotFoundError:
            raise scim_user_not_found() from None
        except LastBillingOwnerError:
            # billing-owner-of-record TASK.md §3 — a deliberate, documented exception to
            # this module's own RFC 7644 envelope convention: this ONE code re-uses the
            # RFC 9457 ProblemError shape (byte-identical to the self-service/superadmin
            # role-change paths' own 409 ERR_LAST_BILLING_OWNER), since it is the SAME
            # invariant/code, not a SCIM-specific concern.
            raise LAST_BILLING_OWNER.exc() from None
        # M5: idempotent no-op repeats never fire a duplicate audit row.
        if changed:
            action = "scim.user_reactivate" if parsed.set_active else "scim.user_deactivate"
            _audit(
                request,
                identity=identity,
                action=action,
                target_id=user_id,
                metadata={"active": parsed.set_active},
            )

    if user is None:
        # Neither recognized field was present in the patch — fetch + return current
        # state as a true no-op 200 (never an error for an empty-but-valid patch).
        get_use_case = GetScimUserUseCase(repo)
        try:
            user = await get_use_case.execute(tenant_id=identity.tenant_id, user_id=user_id)
        except ScimUserNotFoundError:
            raise scim_user_not_found() from None

    return user_to_scim(user)


# ---------------------------------------------------------------------------
# Discovery endpoints (M9) — authenticated the same as every other /scim/v2/* route
# ---------------------------------------------------------------------------


@scim_router.get("/ServiceProviderConfig")
async def get_service_provider_config(
    _identity: Annotated[ScimIdentity, Depends(get_scim_identity)],
) -> dict[str, Any]:
    return service_provider_config()


@scim_router.get("/ResourceTypes")
async def get_resource_types(
    _identity: Annotated[ScimIdentity, Depends(get_scim_identity)],
) -> list[dict[str, Any]]:
    return resource_types()


@scim_router.get("/Schemas")
async def get_schemas(
    _identity: Annotated[ScimIdentity, Depends(get_scim_identity)],
) -> list[dict[str, Any]]:
    return schemas_list()


# ---------------------------------------------------------------------------
# GET /scim/v2/Users, GET /scim/v2/Users/{id}
# ---------------------------------------------------------------------------


@scim_router.get("/Users")
async def list_users(
    identity: Annotated[ScimIdentity, Depends(get_scim_identity)],
    repo: Annotated[SqlAlchemyScimUserRepository, Depends(get_scim_user_repo)],
    filter: str | None = Query(default=None),
    startIndex: int = Query(default=1, ge=1),
    count: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:
    username_filter = parse_username_filter(filter)
    use_case = ListScimUsersUseCase(repo)
    users, total = await use_case.execute(
        tenant_id=identity.tenant_id,
        username_filter=username_filter,
        start_index=startIndex,
        count=count,
    )
    return list_response(users, total=total, start_index=startIndex, count=count)


@scim_router.get("/Users/{user_id}")
async def get_user(
    user_id: str,
    identity: Annotated[ScimIdentity, Depends(get_scim_identity)],
    repo: Annotated[SqlAlchemyScimUserRepository, Depends(get_scim_user_repo)],
) -> dict[str, Any]:
    uid = _parse_user_id(user_id)
    use_case = GetScimUserUseCase(repo)
    try:
        user = await use_case.execute(tenant_id=identity.tenant_id, user_id=uid)
    except ScimUserNotFoundError:
        raise scim_user_not_found() from None
    return user_to_scim(user)


# ---------------------------------------------------------------------------
# POST /scim/v2/Users
# ---------------------------------------------------------------------------


@scim_router.post("/Users", status_code=201)
async def create_user(
    request: Request,
    body: dict[str, Any],
    identity: Annotated[ScimIdentity, Depends(get_scim_identity)],
    repo: Annotated[SqlAlchemyScimUserRepository, Depends(get_scim_user_repo)],
) -> dict[str, Any]:
    await _enforce_write_rate_limit(request, identity)
    username = parse_create_user(body)  # raises scim_invalid_value(400) on malformed input

    use_case = CreateScimUserUseCase(repo)
    try:
        user = await use_case.execute(tenant_id=identity.tenant_id, email=username)
    except ScimUniquenessError:
        raise scim_uniqueness() from None
    except SeatCapExceededError:
        # plan-seat-cap TASK.md §3 (FROZEN @ v1, M5/R4) — RFC 7644 envelope, the
        # deliberate documented exception to the project-wide RFC 9457 convention (this
        # module's own docstring).
        raise scim_seat_cap_exceeded() from None

    _audit(
        request,
        identity=identity,
        action="scim.user_create",
        target_id=user.id,
        metadata={"userName": user.email},
    )
    return user_to_scim(user)


# ---------------------------------------------------------------------------
# PUT / PATCH /scim/v2/Users/{id}
# ---------------------------------------------------------------------------


@scim_router.put("/Users/{user_id}")
async def replace_user(
    request: Request,
    user_id: str,
    body: dict[str, Any],
    identity: Annotated[ScimIdentity, Depends(get_scim_identity)],
    repo: Annotated[SqlAlchemyScimUserRepository, Depends(get_scim_user_repo)],
) -> dict[str, Any]:
    await _enforce_write_rate_limit(request, identity)
    uid = _parse_user_id(user_id)
    parsed = parse_put_user(body)
    return await _apply_patch(request, identity, repo, uid, parsed)


@scim_router.patch("/Users/{user_id}")
async def patch_user(
    request: Request,
    user_id: str,
    body: dict[str, Any],
    identity: Annotated[ScimIdentity, Depends(get_scim_identity)],
    repo: Annotated[SqlAlchemyScimUserRepository, Depends(get_scim_user_repo)],
) -> dict[str, Any]:
    await _enforce_write_rate_limit(request, identity)
    uid = _parse_user_id(user_id)
    parsed = parse_patch_operations(body)
    return await _apply_patch(request, identity, repo, uid, parsed)


# ---------------------------------------------------------------------------
# DELETE /scim/v2/Users/{id} — ALIAS for PATCH active:false (M6)
# ---------------------------------------------------------------------------


@scim_router.delete("/Users/{user_id}", status_code=204)
async def delete_user(
    request: Request,
    user_id: str,
    identity: Annotated[ScimIdentity, Depends(get_scim_identity)],
    repo: Annotated[SqlAlchemyScimUserRepository, Depends(get_scim_user_repo)],
) -> None:
    await _enforce_write_rate_limit(request, identity)
    uid = _parse_user_id(user_id)
    use_case = SetScimUserActiveUseCase(repo)
    try:
        _user, changed = await use_case.execute(
            tenant_id=identity.tenant_id, user_id=uid, active=False
        )
    except ScimUserNotFoundError:
        raise scim_user_not_found() from None
    except LastBillingOwnerError:
        # billing-owner-of-record TASK.md §3 — same deliberate RFC 9457 exception as
        # _apply_patch's own PATCH active:false path (this IS the DELETE alias, M6).
        raise LAST_BILLING_OWNER.exc() from None
    if changed:
        _audit(
            request,
            identity=identity,
            action="scim.user_deactivate",
            target_id=uid,
            metadata={"via": "delete_alias"},
        )


# ---------------------------------------------------------------------------
# Groups — M10: honest empty read, 501 on any write (no parallel store exists)
# ---------------------------------------------------------------------------


@scim_router.get("/Groups")
async def list_groups(
    _identity: Annotated[ScimIdentity, Depends(get_scim_identity)],
) -> dict[str, Any]:
    return empty_list_response()


@scim_router.post("/Groups")
async def create_group_unsupported(
    _identity: Annotated[ScimIdentity, Depends(get_scim_identity)],
) -> None:
    raise scim_groups_unsupported()


@scim_router.put("/Groups/{group_id}")
async def replace_group_unsupported(
    group_id: str,
    _identity: Annotated[ScimIdentity, Depends(get_scim_identity)],
) -> None:
    raise scim_groups_unsupported()


@scim_router.patch("/Groups/{group_id}")
async def patch_group_unsupported(
    group_id: str,
    _identity: Annotated[ScimIdentity, Depends(get_scim_identity)],
) -> None:
    raise scim_groups_unsupported()


@scim_router.delete("/Groups/{group_id}")
async def delete_group_unsupported(
    group_id: str,
    _identity: Annotated[ScimIdentity, Depends(get_scim_identity)],
) -> None:
    raise scim_groups_unsupported()
