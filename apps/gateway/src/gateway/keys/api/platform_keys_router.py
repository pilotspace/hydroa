"""FastAPI router for the superadmin cross-tenant keys surface
(cross-tenant-keys-members TASK.md §3 — FROZEN @ v1).

Endpoints (all under /admin/platform/tenants/{tenant_id}/keys):
  GET    ""              — list the target tenant's complete key list (redacted)
  POST   ""              — create a key FOR the target tenant (plaintext shown once)
  PATCH  "/{key_id}"      — update governance fields on an active key owned by the target tenant
  POST   "/{key_id}/rotate" — atomically rotate a key owned by the target tenant
  DELETE "/{key_id}"      — soft-revoke a key owned by the target tenant

Security (server-side, authoritative), identical on every route, in this order (M9):
  1. require_superadmin (Depends)                       — role-only gate, no target
  2. authorize_tenant_scope(identity, tenant_id)         — cheap in-memory check (redundant with
     (1) today since this whole tree is SUPERADMIN-only, but the semantically correct predicate
     for "may I act on tenant_id" — same disclosed tension as platform_tenants_router.py)
  3. get_tenant_by_id(session, tenant_id) -> 404 TENANT_NOT_FOUND if the target tenant_id does
     not resolve to a real row — BEFORE any keys query (CREATE strictly needs this:
     api_keys.tenant_id carries a real FK constraint; the other 4 routes get an equivalent 404
     for free via KeyNotFoundError, but the pre-check is applied uniformly per §1's Framings-
     weighed decision)

Every use-case call below is parametrized by the PATH tenant_id — NEVER identity.tenant_id (the
superadmin's own platform tenant_id). This is the one wiring detail where a mistake would be a
real cross-tenant data leak, not a cosmetic test failure: CreateKeyUseCase/ListKeysUseCase/
UpdateKeyUseCase/RotateKeyUseCase/RevokeKeyUseCase are called UNCHANGED from
keys/application/use_cases.py — this router adds ZERO new business logic, only wiring.

A key_id belonging to a DIFFERENT tenant than the path 404s IDENTICALLY to an unknown key_id
(KeyNotFoundError, no distinguishing detail) — enforced by the existing use-case/repository layer
exactly as it already does for self-service, now fed the PATH tenant_id instead of
identity.tenant_id.

No audit wiring yet — deferred entirely to admin-console-audit (task 4, depends-on this task;
§3 Least-sure flag, auto-approved under standing AUTO MODE delegation at freeze).

No playground-token route exists here (M13 — a session/auth mechanic reserved for
tenant-impersonation, milestone 3, not a key-management action).
"""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.platform_audit import emit_platform_audit
from gateway.core.db import get_session
from gateway.core.error_catalog import (
    AUTH_FORBIDDEN,
    KEY_NOT_FOUND,
    PAYLOAD_EXPIRES_AT_INVALID,
    TEAM_NOT_FOUND,
    TENANT_NOT_FOUND,
)
from gateway.core.ids import uuid7
from gateway.keys.api.schemas import (
    CreateKeyRequest,
    CreateKeyResponse,
    KeyInfoResponse,
    PatchKeyRequest,
    RotateKeyRequest,
    RotateKeyResponse,
)
from gateway.keys.application.use_cases import (
    CreateKeyUseCase,
    ListKeysUseCase,
    RevokeKeyUseCase,
    RotateKeyUseCase,
    UpdateKeyUseCase,
)
from gateway.keys.domain.errors import ForbiddenError, KeyNotFoundError
from gateway.keys.infrastructure.repository import SqlAlchemyApiKeyRepository
from gateway.keys.infrastructure.sha256_hasher import Sha256SecretHasher
from gateway.teams.api.deps import get_team_repository
from gateway.teams.infrastructure.repository import SqlAlchemyTeamRepository
from gateway.tenants.domain.authz import authorize_tenant_scope, require_superadmin
from gateway.tenants.domain.entities import Identity
from gateway.tenants.infrastructure.repository import get_tenant_by_id

platform_keys_router = APIRouter(
    prefix="/admin/platform/tenants/{tenant_id}/keys", tags=["platform-admin"]
)

# Singleton hasher — stateless, safe to share (mirrors keys/api/deps.py's module-level instance).
_hasher = Sha256SecretHasher()


# ---------------------------------------------------------------------------
# Shared helpers (verbatim copies of keys/api/router.py's private helpers — kept local
# to avoid importing private `_`-prefixed names across router modules)
# ---------------------------------------------------------------------------


def _decimal_or_none(s: str | None) -> Decimal | None:
    return Decimal(s) if s is not None else None


def _datetime_or_none(s: str | None) -> dt.datetime | None:
    if s is None:
        return None
    try:
        parsed = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return parsed.astimezone(dt.UTC).replace(tzinfo=dt.UTC)
    except ValueError:
        raise PAYLOAD_EXPIRES_AT_INVALID.exc(value=s) from None


def _fmt_expires(ts: dt.datetime | None) -> str | None:
    if ts is None:
        return None
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _require_target_tenant(
    identity: Identity, tenant_id: uuid.UUID, session: AsyncSession
) -> None:
    """M9: authorize_tenant_scope THEN tenant-existence check — BEFORE any keys query."""
    authorize_tenant_scope(identity, tenant_id)
    row = await get_tenant_by_id(session, tenant_id)
    if row is None:
        raise TENANT_NOT_FOUND.exc()


# ---------------------------------------------------------------------------
# Dependency factories (session-only, no tenant coupling — mirrors keys/api/deps.py)
# ---------------------------------------------------------------------------


def _get_create_key_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CreateKeyUseCase:
    return CreateKeyUseCase(SqlAlchemyApiKeyRepository(session), _hasher)


def _get_list_keys_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ListKeysUseCase:
    return ListKeysUseCase(SqlAlchemyApiKeyRepository(session))


def _get_update_key_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UpdateKeyUseCase:
    return UpdateKeyUseCase(SqlAlchemyApiKeyRepository(session))


def _get_rotate_key_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RotateKeyUseCase:
    return RotateKeyUseCase(SqlAlchemyApiKeyRepository(session), _hasher)


def _get_revoke_key_use_case(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RevokeKeyUseCase:
    return RevokeKeyUseCase(SqlAlchemyApiKeyRepository(session))


# ---------------------------------------------------------------------------
# GET /admin/platform/tenants/{tenant_id}/keys
# ---------------------------------------------------------------------------


@platform_keys_router.get("", response_model=list[KeyInfoResponse])
async def list_platform_tenant_keys(
    tenant_id: uuid.UUID,
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    use_case: Annotated[ListKeysUseCase, Depends(_get_list_keys_use_case)],
    request: Request,
) -> list[KeyInfoResponse]:
    """GET .../keys — list the target tenant's COMPLETE key list (active + revoked,
    unfiltered — matches self-service), redacted (KeyInfoResponse — no hash/secret).
    SUPERADMIN only.
    """
    await _require_target_tenant(identity, tenant_id, session)
    items = await use_case.execute(tenant_id=tenant_id)
    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=tenant_id,
        action="platform.key.list",
        target_type="api_key",
        target_id=None,
        metadata={},
    )
    return [
        KeyInfoResponse(
            key_id=item.key_id,
            name=item.name,
            prefix=item.prefix,
            created_at=item.created_at,
            revoked_at=item.revoked_at,
            monthly_budget_usd=(
                str(item.monthly_budget_usd) if item.monthly_budget_usd is not None else None
            ),
            soft_budget_usd=(
                str(item.soft_budget_usd) if item.soft_budget_usd is not None else None
            ),
            expires_at=item.expires_at,
            model_allowlist=item.model_allowlist,
            rpm_limit=item.rpm_limit,
            tpm_limit=item.tpm_limit,
            team_id=item.team_id,
            cache_enabled=item.cache_enabled,
        )
        for item in items
    ]


# ---------------------------------------------------------------------------
# POST /admin/platform/tenants/{tenant_id}/keys
# ---------------------------------------------------------------------------


@platform_keys_router.post("", status_code=201, response_model=CreateKeyResponse)
async def create_platform_tenant_key(
    tenant_id: uuid.UUID,
    body: CreateKeyRequest,
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    use_case: Annotated[CreateKeyUseCase, Depends(_get_create_key_use_case)],
    team_repo: Annotated[SqlAlchemyTeamRepository, Depends(get_team_repository)],
    request: Request,
) -> CreateKeyResponse:
    """POST .../keys — create a key FOR the target tenant (tenant_id = PATH value, never
    identity.tenant_id) via CreateKeyUseCase verbatim. A body `team_id` must belong to the
    TARGET tenant. Returns the plaintext `key` EXACTLY ONCE — identical reveal-once UX to
    self-service (suppressing it would mint a permanently unusable key, since the secret is
    one-way-hashed with no re-derivation path — a correctness bug, not a safer default; see
    §1 Framings weighed). SUPERADMIN only.
    """
    await _require_target_tenant(identity, tenant_id, session)

    if body.team_id is not None:
        if not await team_repo.get_team_for_tenant(body.team_id, tenant_id):
            raise TEAM_NOT_FOUND.exc()

    key_id: uuid.UUID = uuid7()
    result = await use_case.execute(
        tenant_id=tenant_id,
        name=body.name,
        key_id=key_id,
        monthly_budget_usd=_decimal_or_none(body.monthly_budget_usd),
        soft_budget_usd=_decimal_or_none(body.soft_budget_usd),
        expires_at=_datetime_or_none(body.expires_at),
        model_allowlist=body.model_allowlist,
        rpm_limit=body.rpm_limit,
        tpm_limit=body.tpm_limit,
        team_id=body.team_id,
        cache_enabled=body.cache_enabled,
    )

    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=tenant_id,
        action="platform.key.create",
        target_type="api_key",
        target_id=str(result.key_id),
        metadata={"key_name": result.name},
    )
    return CreateKeyResponse(
        key_id=result.key_id,
        name=result.name,
        key=result.key,
        monthly_budget_usd=(
            str(result.monthly_budget_usd) if result.monthly_budget_usd is not None else None
        ),
        soft_budget_usd=(
            str(result.soft_budget_usd) if result.soft_budget_usd is not None else None
        ),
        expires_at=_fmt_expires(result.expires_at),
        model_allowlist=result.model_allowlist,
        rpm_limit=result.rpm_limit,
        tpm_limit=result.tpm_limit,
        team_id=result.team_id,
        cache_enabled=result.cache_enabled,
    )


# ---------------------------------------------------------------------------
# PATCH /admin/platform/tenants/{tenant_id}/keys/{key_id}
# ---------------------------------------------------------------------------


@platform_keys_router.patch("/{key_id}", response_model=KeyInfoResponse)
async def patch_platform_tenant_key(
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    body: PatchKeyRequest,
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    use_case: Annotated[UpdateKeyUseCase, Depends(_get_update_key_use_case)],
    team_repo: Annotated[SqlAlchemyTeamRepository, Depends(get_team_repository)],
    request: Request,
) -> KeyInfoResponse:
    """PATCH .../keys/{key_id} — update governance fields on an ACTIVE key owned by the
    target tenant (tenant_id = PATH value) via UpdateKeyUseCase verbatim (role =
    identity.role — the SUPERADMIN caller's own role, which is never MEMBER so the
    use-case's role guard never fires here). A key_id belonging to a different tenant
    (or revoked, or unknown) 404s identically — no distinguishing detail. SUPERADMIN only.
    """
    await _require_target_tenant(identity, tenant_id, session)

    fields_to_clear: set[str] = set()
    new_monthly: Decimal | None = None
    new_soft: Decimal | None = None
    new_expires: dt.datetime | None = None
    new_allowlist: list[str] | None = None
    new_rpm: int | None = None
    new_tpm: int | None = None
    new_team_id: uuid.UUID | None = None
    new_cache_enabled: bool | None = None

    if "monthly_budget_usd" in body.model_fields_set:
        if body.monthly_budget_usd is None:
            fields_to_clear.add("monthly_budget_usd")
        else:
            new_monthly = _decimal_or_none(body.monthly_budget_usd)

    if "soft_budget_usd" in body.model_fields_set:
        if body.soft_budget_usd is None:
            fields_to_clear.add("soft_budget_usd")
        else:
            new_soft = _decimal_or_none(body.soft_budget_usd)

    if "expires_at" in body.model_fields_set:
        if body.expires_at is None:
            fields_to_clear.add("expires_at")
        else:
            new_expires = _datetime_or_none(body.expires_at)

    if "model_allowlist" in body.model_fields_set:
        if body.model_allowlist is None:
            fields_to_clear.add("model_allowlist")
        else:
            new_allowlist = body.model_allowlist

    if "rpm_limit" in body.model_fields_set:
        if body.rpm_limit is None:
            fields_to_clear.add("rpm_limit")
        else:
            new_rpm = body.rpm_limit

    if "tpm_limit" in body.model_fields_set:
        if body.tpm_limit is None:
            fields_to_clear.add("tpm_limit")
        else:
            new_tpm = body.tpm_limit

    if "team_id" in body.model_fields_set:
        if body.team_id is None:
            fields_to_clear.add("team_id")
        else:
            # Validate team belongs to the TARGET tenant (404, not 403 — no existence leak).
            if not await team_repo.get_team_for_tenant(body.team_id, tenant_id):
                raise TEAM_NOT_FOUND.exc()
            new_team_id = body.team_id

    if "cache_enabled" in body.model_fields_set:
        new_cache_enabled = body.cache_enabled

    try:
        updated = await use_case.execute(
            key_id=key_id,
            tenant_id=tenant_id,
            role=identity.role,
            monthly_budget_usd=new_monthly,
            soft_budget_usd=new_soft,
            expires_at=new_expires,
            model_allowlist=new_allowlist,
            rpm_limit=new_rpm,
            tpm_limit=new_tpm,
            team_id=new_team_id,
            cache_enabled=new_cache_enabled,
            _fields_to_clear=fields_to_clear,
        )
    except ForbiddenError:
        raise AUTH_FORBIDDEN.exc() from None
    except KeyNotFoundError:
        raise KEY_NOT_FOUND.exc() from None

    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=tenant_id,
        action="platform.key.patch",
        target_type="api_key",
        target_id=str(key_id),
        metadata={"fields_changed": sorted(body.model_fields_set)},
    )
    return KeyInfoResponse(
        key_id=updated.id,
        name=updated.name,
        prefix=f"sk-{updated.id.hex[:8]}",
        created_at=updated.created_at,
        revoked_at=updated.revoked_at,
        monthly_budget_usd=(
            str(updated.monthly_budget_usd) if updated.monthly_budget_usd is not None else None
        ),
        soft_budget_usd=(
            str(updated.soft_budget_usd) if updated.soft_budget_usd is not None else None
        ),
        expires_at=updated.expires_at,
        model_allowlist=updated.model_allowlist,
        rpm_limit=updated.rpm_limit,
        tpm_limit=updated.tpm_limit,
        team_id=updated.team_id,
        cache_enabled=updated.cache_enabled,
    )


# ---------------------------------------------------------------------------
# POST /admin/platform/tenants/{tenant_id}/keys/{key_id}/rotate
# ---------------------------------------------------------------------------


@platform_keys_router.post("/{key_id}/rotate", status_code=201, response_model=RotateKeyResponse)
async def rotate_platform_tenant_key(
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    body: RotateKeyRequest,
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    use_case: Annotated[RotateKeyUseCase, Depends(_get_rotate_key_use_case)],
    request: Request,
) -> RotateKeyResponse:
    """POST .../keys/{key_id}/rotate — atomically rotate a key owned by the target tenant
    (tenant_id = PATH value) via RotateKeyUseCase verbatim. Returns the NEW plaintext key
    EXACTLY ONCE. A key_id belonging to a different tenant 404s identically — enforced by
    RotateKeyUseCase's own `old_key.tenant_id != tenant_id` check (use_cases.py), fed the
    PATH tenant_id here, never identity.tenant_id. SUPERADMIN only.
    """
    await _require_target_tenant(identity, tenant_id, session)

    budget_provided = "monthly_budget_usd" in body.model_fields_set
    soft_provided = "soft_budget_usd" in body.model_fields_set
    expires_provided = "expires_at" in body.model_fields_set
    allowlist_provided = "model_allowlist" in body.model_fields_set

    try:
        result = await use_case.execute(
            old_key_id=key_id,
            tenant_id=tenant_id,
            role=identity.role,
            monthly_budget_usd=(
                _decimal_or_none(body.monthly_budget_usd) if budget_provided else None
            ),
            soft_budget_usd=(_decimal_or_none(body.soft_budget_usd) if soft_provided else None),
            expires_at=_datetime_or_none(body.expires_at) if expires_provided else None,
            model_allowlist=body.model_allowlist if allowlist_provided else None,
            _budget_provided=budget_provided,
            _soft_budget_provided=soft_provided,
            _expires_provided=expires_provided,
            _allowlist_provided=allowlist_provided,
        )
    except ForbiddenError:
        raise AUTH_FORBIDDEN.exc() from None
    except KeyNotFoundError:
        raise KEY_NOT_FOUND.exc() from None

    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=tenant_id,
        action="platform.key.rotate",
        target_type="api_key",
        target_id=str(result.new_key_id),
        metadata={
            "superseded_key_id": str(result.superseded_key_id),
            "key_name": result.name,
        },
    )
    return RotateKeyResponse(
        new_key_id=result.new_key_id,
        superseded_key_id=result.superseded_key_id,
        key=result.key,
        name=result.name,
        monthly_budget_usd=(
            str(result.monthly_budget_usd) if result.monthly_budget_usd is not None else None
        ),
        soft_budget_usd=(
            str(result.soft_budget_usd) if result.soft_budget_usd is not None else None
        ),
        expires_at=_fmt_expires(result.expires_at),
        model_allowlist=result.model_allowlist,
    )


# ---------------------------------------------------------------------------
# DELETE /admin/platform/tenants/{tenant_id}/keys/{key_id}
# ---------------------------------------------------------------------------


@platform_keys_router.delete("/{key_id}", status_code=204)
async def revoke_platform_tenant_key(
    tenant_id: uuid.UUID,
    key_id: uuid.UUID,
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    use_case: Annotated[RevokeKeyUseCase, Depends(_get_revoke_key_use_case)],
    request: Request,
) -> None:
    """DELETE .../keys/{key_id} — soft-revoke a key owned by the target tenant (tenant_id =
    PATH value) via RevokeKeyUseCase verbatim. Cross-tenant key_id 404s identically
    (KeyNotFoundError — no leak). SUPERADMIN only.
    """
    await _require_target_tenant(identity, tenant_id, session)

    try:
        await use_case.execute(
            key_id=key_id,
            tenant_id=tenant_id,
            role=identity.role,
        )
    except ForbiddenError:
        raise AUTH_FORBIDDEN.exc() from None
    except KeyNotFoundError:
        raise KEY_NOT_FOUND.exc() from None

    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=tenant_id,
        action="platform.key.revoke",
        target_type="api_key",
        target_id=str(key_id),
        metadata={},
    )
