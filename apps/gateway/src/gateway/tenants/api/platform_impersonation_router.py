"""FastAPI router for superadmin impersonation session lifecycle
(impersonation-session-lifecycle TASK.md §3 CONTRACT — FROZEN @ v1).

Endpoints:
  POST   /admin/platform/tenants/{tenant_id}/users/{user_id}/impersonate   — Mint (M5-M7)
  DELETE /admin/platform/impersonation/sessions/{session_id}               — End (M8)

No shared router-level path prefix (§1 Framings weighed) — Mint's own
`/tenants/{tenant_id}/users/{user_id}/impersonate` and End's
`/impersonation/sessions/{session_id}` coexist as two independently-declared full paths,
mirroring "mounted alongside the existing platform_*_router.py siblings" (§0 GROUND).

Security (server-side, authoritative):
  - Both routes gated by `require_superadmin` (Depends) FIRST — UNCHANGED, zero lines
    modified. An impersonation identity's own `role` is always the TARGET's real role
    (never SUPERADMIN, M1), so this SAME unmodified gate is what makes both routes, and
    every other /admin/platform/* route, structurally unreachable to an active
    impersonation session — no impersonation-specific carve-out anywhere in this file.
  - Mint additionally runs `authorize_tenant_scope(identity, tenant_id)` THEN
    `get_tenant_by_id` (404) THEN M6's target-eligibility checks THEN M7's active-session
    precondition, all BEFORE any write (mirrors platform_users_router.py's own
    `_require_target_tenant` gate order).
  - End has NO `authorize_tenant_scope` call (no single target tenant_id — mirrors
    authz.py's own "list every tenant" precedent) and resolves/validates the session row
    via ONE conditional UPDATE before any other write.

Concurrency (contract §3 Part D IO note — design for failure):
  - Mint's lazy-expire-then-check-then-insert sequence is NOT assumed atomic on its own;
    the partial unique index (`uq_impersonation_sessions_actor_active`) is the actual
    race-safety backstop — a resulting IntegrityError on insert is caught and translated
    to the same 409 the ordinary precondition check returns, never a raw 500.
  - End uses ONE conditional `UPDATE ... WHERE id=:id AND revoked_at IS NULL AND
    expires_at > now() RETURNING ...` (mirrors RevokeKeyUseCase's own precedent,
    keys/infrastructure/repository.py:111-124) — NEVER a plain read-then-write, which would
    let two concurrent Ends on one session_id both observe revoked_at IS NULL before either
    commits (a double-204). A returned row -> 204. Zero rows -> a follow-up plain SELECT
    (safe to run only on this already-lost-the-race path) distinguishes 404 from 409.

Audit: both routes call emit_platform_audit() on their success path (M5/M8) —
  "platform.impersonation.start" / "platform.impersonation.end", target_type="user",
  target_id=str(<the target user's id>); identity= is always the REAL calling superadmin
  (Mint's caller and End's caller may be two different superadmins, M8 decision).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.platform_audit import emit_platform_audit
from gateway.core.config import Settings
from gateway.core.db import get_session
from gateway.core.error_catalog import (
    IMPERSONATION_SESSION_ALREADY_ACTIVE,
    IMPERSONATION_SESSION_ALREADY_ENDED,
    IMPERSONATION_SESSION_NOT_FOUND,
    IMPERSONATION_TARGET_INVALID,
    TENANT_NOT_FOUND,
    USER_NOT_FOUND,
)
from gateway.core.ids import uuid7
from gateway.tenants.domain.authz import authorize_tenant_scope, require_superadmin
from gateway.tenants.domain.entities import Identity, ImpersonationContext, Role
from gateway.tenants.infrastructure.orm import ImpersonationSessionRow, UserRow
from gateway.tenants.infrastructure.repository import get_tenant_by_id

platform_impersonation_router = APIRouter(tags=["platform-admin"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class ImpersonationTargetResponse(BaseModel):
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    role: str


class ImpersonationMintResponse(BaseModel):
    token: str
    expires_in: int
    session_id: uuid.UUID
    target: ImpersonationTargetResponse


# ---------------------------------------------------------------------------
# POST /admin/platform/tenants/{tenant_id}/users/{user_id}/impersonate
# ---------------------------------------------------------------------------


@platform_impersonation_router.post(
    "/admin/platform/tenants/{tenant_id}/users/{user_id}/impersonate",
    response_model=ImpersonationMintResponse,
    status_code=201,
)
async def mint_impersonation_session(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> ImpersonationMintResponse:
    """Mint a dual-identity impersonation session. SUPERADMIN only (M5).

    Gate order: require_superadmin -> authorize_tenant_scope -> get_tenant_by_id (404,
    R3) -> tenant-kind check (403, M6a/R4) -> target-user resolution (404, R5) ->
    target-role check (403, M6b/R6) -> M7 active-session precondition (409, R7) ->
    insert -> issue the dual-identity JWT -> emit_platform_audit -> 201.
    """
    authorize_tenant_scope(identity, tenant_id)
    tenant_row = await get_tenant_by_id(session, tenant_id)
    if tenant_row is None:
        raise TENANT_NOT_FOUND.exc()
    if tenant_row.kind == "platform":
        raise IMPERSONATION_TARGET_INVALID.exc(detail="Cannot impersonate into the platform tenant")

    user_row = (
        await session.execute(
            select(UserRow).where(UserRow.id == user_id, UserRow.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if user_row is None:
        raise USER_NOT_FOUND.exc()
    if user_row.role == Role.SUPERADMIN.value:
        # Defense-in-depth (M6b) — structurally unreachable once the tenant-kind check
        # above holds (the DB trigger already restricts superadmin to the platform
        # tenant), but checked explicitly here too, never relying solely on that trigger.
        raise IMPERSONATION_TARGET_INVALID.exc(detail="Cannot impersonate a superadmin user")

    settings: Settings = request.app.state.settings
    now = datetime.now(UTC)

    # M7: lazily revoke any of the CALLING superadmin's own prior rows that are past
    # expires_at but never explicitly ended, BEFORE checking for a remaining active row.
    await session.execute(
        update(ImpersonationSessionRow)
        .where(
            ImpersonationSessionRow.actor_user_id == identity.user_id,
            ImpersonationSessionRow.revoked_at.is_(None),
            ImpersonationSessionRow.expires_at <= now,
        )
        .values(revoked_at=now, revoked_reason="expired_lazy_cleanup")
    )
    existing_active = (
        await session.execute(
            select(ImpersonationSessionRow.id).where(
                ImpersonationSessionRow.actor_user_id == identity.user_id,
                ImpersonationSessionRow.revoked_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing_active is not None:
        await session.commit()
        raise IMPERSONATION_SESSION_ALREADY_ACTIVE.exc()

    target_role = Role(user_row.role)
    expires_at = now + timedelta(seconds=settings.impersonation_session_ttl_seconds)
    new_row = ImpersonationSessionRow(
        id=uuid7(),
        actor_user_id=identity.user_id,
        actor_tenant_id=identity.tenant_id,
        actor_email=identity.email,
        target_user_id=user_row.id,
        target_tenant_id=tenant_id,
        target_role=target_role.value,
        target_email=user_row.email,
        expires_at=expires_at,
    )
    session.add(new_row)
    try:
        await session.flush()
    except IntegrityError as exc:
        # IO note (design for failure): the precondition read above and this insert are
        # NOT assumed atomic on their own — uq_impersonation_sessions_actor_active is the
        # actual race-safety backstop. A concurrent Mint from the SAME superadmin that won
        # the race lands here as an IntegrityError, translated to the same 409 the
        # ordinary precondition check returns above, never a raw 500.
        await session.rollback()
        raise IMPERSONATION_SESSION_ALREADY_ACTIVE.exc() from exc
    await session.commit()

    impersonation_context = ImpersonationContext(
        session_id=new_row.id,
        actor_user_id=identity.user_id,
        actor_tenant_id=identity.tenant_id,
        actor_email=identity.email,
    )
    token, expires_in = request.app.state.token_service.issue(
        user_id=user_row.id,
        tenant_id=tenant_id,
        role=target_role,
        email=user_row.email,
        impersonation=impersonation_context,
        ttl_seconds=settings.impersonation_session_ttl_seconds,
    )

    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=tenant_id,
        action="platform.impersonation.start",
        target_type="user",
        target_id=str(user_id),
        metadata={
            "session_id": str(new_row.id),
            "target_role": target_role.value,
            "expires_at": expires_at.isoformat(),
        },
    )

    return ImpersonationMintResponse(
        token=token,
        expires_in=expires_in,
        session_id=new_row.id,
        target=ImpersonationTargetResponse(
            user_id=user_row.id,
            tenant_id=tenant_id,
            email=user_row.email,
            role=target_role.value,
        ),
    )


# ---------------------------------------------------------------------------
# DELETE /admin/platform/impersonation/sessions/{session_id}
# ---------------------------------------------------------------------------


@platform_impersonation_router.delete(
    "/admin/platform/impersonation/sessions/{session_id}",
    status_code=204,
)
async def end_impersonation_session(
    session_id: uuid.UUID,
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> None:
    """End (durably revoke) an impersonation session. SUPERADMIN only (M8) — no
    `authorize_tenant_scope` call (no single target tenant_id). ANY superadmin may end
    ANY active session, not only the one who started it (§1 decision).

    Concurrency-safe by construction (contract §3 Part D IO note): ONE conditional
    UPDATE...RETURNING, never a plain read-then-write — see module docstring.
    """
    now = datetime.now(UTC)
    result = await session.execute(
        update(ImpersonationSessionRow)
        .where(
            ImpersonationSessionRow.id == session_id,
            ImpersonationSessionRow.revoked_at.is_(None),
            ImpersonationSessionRow.expires_at > now,
        )
        .values(revoked_at=now, revoked_reason="explicit_end")
        .returning(
            ImpersonationSessionRow.actor_user_id,
            ImpersonationSessionRow.target_tenant_id,
            ImpersonationSessionRow.target_user_id,
        )
    )
    row = result.first()
    if row is None:
        # Lost the race, already-ended, or genuinely unknown — safe to run a follow-up
        # plain SELECT only on this already-lost-the-race path (no write is pending on
        # this path, so there is nothing left to race with).
        await session.commit()
        existing = (
            await session.execute(
                select(ImpersonationSessionRow.id).where(ImpersonationSessionRow.id == session_id)
            )
        ).scalar_one_or_none()
        if existing is None:
            raise IMPERSONATION_SESSION_NOT_FOUND.exc()
        raise IMPERSONATION_SESSION_ALREADY_ENDED.exc()
    await session.commit()

    original_actor_user_id, target_tenant_id, target_user_id = row

    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=target_tenant_id,
        action="platform.impersonation.end",
        target_type="user",
        target_id=str(target_user_id),
        metadata={
            "session_id": str(session_id),
            "ended_by_original_actor": identity.user_id == original_actor_user_id,
        },
    )
