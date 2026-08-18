"""Enterprise RBAC authorization vocabulary (rbac-roles TASK.md §3 CONTRACT — FROZEN @ v1).

Defines:
  - Permission  — enum of all capabilities the gateway can require
  - ROLE_PERMISSIONS — allowlist mapping: Role → frozenset[Permission]
  - require_permission(perm) — FastAPI dependency factory
  - Import-time completeness guard (incomplete_matrix → RuntimeError)

ALLOWLIST semantics: a role holds ONLY what is listed.  A new/unknown role
defaults to NO admin access.

Back-compat: owner/admin hold every permission that was previously guarded by
require_owner_or_admin; member holds none.  So surfaces re-bound to
require_permission(<specific_perm>) return identical status codes for
OWNER/ADMIN/MEMBER as they did before the refactor.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import TYPE_CHECKING, Annotated

import fastapi
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.error_catalog import (
    AUTH_FORBIDDEN,
    AUTH_TOKEN_INVALID,
    AUTH_TOKEN_MISSING,
    AUTH_UNAVAILABLE,
)
from gateway.tenants.domain.entities import Identity, Role
from gateway.tenants.domain.errors import InvalidTokenError, SessionRevocationUnavailableError

if TYPE_CHECKING:
    # Type-only (impersonation-live-session-guard TASK.md §3 Part B): ensure_impersonation_
    # session_live's own body imports nothing at runtime (zero framework/infra imports,
    # CONVENTIONS.md), so this stays TYPE_CHECKING-only rather than a real top-level import.
    from gateway.tenants.domain.ports import ImpersonationSessionGuard, SessionRevocationGuard

__all__ = [
    "ROLE_PERMISSIONS",
    "Permission",
    "Role",
    "authorize_tenant_scope",
    "ensure_impersonation_session_live",
    "ensure_session_not_revoked",
    "require_active_user",
    "require_permission",
    "require_superadmin",
]


# ---------------------------------------------------------------------------
# Permission vocabulary
# ---------------------------------------------------------------------------


class Permission(StrEnum):
    """Every capability the gateway can require on a surface."""

    KEYS_MANAGE = "keys_manage"
    ROUTING_MANAGE = "routing_manage"
    CATALOG_SYNC = "catalog_sync"
    BUDGETS_MANAGE = "budgets_manage"
    USAGE_READ = "usage_read"
    OPS_READ = "ops_read"
    MEMBERS_MANAGE = "members_manage"
    PROVIDER_SECRETS = "provider_secrets"
    SECURITY_CONFIG = "security_config"
    AUDIT_READ = "audit_read"
    # tiered-rate-cards TASK.md §3: OWNER-only (markup is the platform's margin
    # over provider cost; auto-holds via ROLE_PERMISSIONS[OWNER] = frozenset(Permission)).
    RATE_CARDS_MANAGE = "rate_cards_manage"
    # logs-explorer-api TASK.md §3: PII-payload-bearing read surface — same additive
    # precedent as RATE_CARDS_MANAGE, role set mirrors AUDIT_READ exactly (owner/admin/
    # operator/superadmin; NOT billing_admin/viewer/member).
    LOGS_READ = "logs_read"
    # invoice-generation TASK.md §3: financial-document read surface — additive,
    # same precedent as RATE_CARDS_MANAGE/LOGS_READ. Role set deliberately diverges
    # from AUDIT_READ/LOGS_READ: BILLING_ADMIN (not OPERATOR) is the financial-
    # document persona (owner/admin/billing_admin/superadmin; NOT operator/viewer/
    # member — confirmed at freeze).
    INVOICES_READ = "invoices_read"
    # agent-identity-governance TASK.md §3 (FROZEN @ v1): create/attach/detach/kill
    # on a named agent principal — a NARROWER ceiling than KEYS_MANAGE (which OPERATOR
    # also holds). A kill switch's blast radius (every session of a named agent,
    # tenant-wide, in one action) reads as categorically more destructive than
    # revoking one API key, so this is OWNER/ADMIN only (CONFIRMED by Tin at freeze —
    # see TASK.md §3 "Least-sure flag").
    AGENTS_MANAGE = "agents_manage"
    # self-serve-checkout TASK.md §3 (FROZEN @ v1, M2/A2): tenant-self self-serve checkout
    # write (plan upgrade + credit top-up). A NEW money-mutation ceiling held by exactly
    # {OWNER, BILLING_ADMIN} (mirrors the shipped BILLING_CAPABLE_ROLES). Deliberately NOT
    # a reuse of BUDGETS_MANAGE — that admits ADMIN, widening the money-mutation floor
    # beyond intent (A2, CONFIRMED at freeze). OWNER auto-holds via frozenset(Permission);
    # BILLING_ADMIN is added explicitly to ROLE_PERMISSIONS below.
    BILLING_MANAGE = "billing_manage"


# ---------------------------------------------------------------------------
# Allowlist matrix (FROZEN @ v1 — approved by Tin 2026-06-25)
# ---------------------------------------------------------------------------

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OWNER: frozenset(Permission),  # ALL permissions
    Role.ADMIN: frozenset(
        {
            Permission.KEYS_MANAGE,
            Permission.ROUTING_MANAGE,
            Permission.CATALOG_SYNC,
            Permission.BUDGETS_MANAGE,
            Permission.USAGE_READ,
            Permission.OPS_READ,
            Permission.MEMBERS_MANAGE,
            Permission.AUDIT_READ,
            Permission.LOGS_READ,
            Permission.INVOICES_READ,
            Permission.AGENTS_MANAGE,
            # NOT PROVIDER_SECRETS, NOT SECURITY_CONFIG (owner-only preserved)
        }
    ),
    Role.OPERATOR: frozenset(
        {
            Permission.ROUTING_MANAGE,
            Permission.CATALOG_SYNC,
            Permission.KEYS_MANAGE,
            Permission.USAGE_READ,
            Permission.OPS_READ,
            Permission.AUDIT_READ,
            Permission.LOGS_READ,
        }
    ),
    Role.BILLING_ADMIN: frozenset(
        {
            Permission.BUDGETS_MANAGE,
            Permission.USAGE_READ,
            Permission.OPS_READ,
            Permission.INVOICES_READ,
            # self-serve-checkout TASK.md §3 (FROZEN @ v1, M2): the self-serve checkout
            # money-mutation floor is exactly {OWNER, BILLING_ADMIN}. OWNER auto-holds
            # (frozenset(Permission)); BILLING_ADMIN is granted it here explicitly.
            Permission.BILLING_MANAGE,
        }
    ),
    Role.VIEWER: frozenset(
        {
            Permission.USAGE_READ,
            Permission.OPS_READ,
        }
    ),
    Role.MEMBER: frozenset(),  # none of the admin permissions
    # Platform-tenant-only role (superadmin-role TASK.md §3 CONTRACT — FROZEN @ v1).
    # Full parity with OWNER. Note: a Permission says nothing about WHICH tenant —
    # every repository still filters WHERE tenant_id = identity.tenant_id regardless
    # of role; cross-tenant reach is a SEPARATE, explicit predicate — see
    # authorize_tenant_scope() below, not this matrix.
    Role.SUPERADMIN: frozenset(Permission),
}

# ---------------------------------------------------------------------------
# Completeness guard — fires at import time
# ---------------------------------------------------------------------------
_missing_roles = [r for r in Role if r not in ROLE_PERMISSIONS]
if _missing_roles:
    raise RuntimeError(
        "incomplete_matrix: the following Role values have no ROLE_PERMISSIONS entry: "
        + ", ".join(r.value for r in _missing_roles)
    )

_all_perms = frozenset(Permission)
if ROLE_PERMISSIONS[Role.OWNER] != _all_perms:
    raise RuntimeError(
        "incomplete_matrix: Role.OWNER must hold ALL permissions; "
        f"missing: {_all_perms - ROLE_PERMISSIONS[Role.OWNER]}"
    )


# ---------------------------------------------------------------------------
# Cross-tenant authorization predicate (superadmin-role TASK.md §3 CONTRACT — FROZEN @ v1)
# ---------------------------------------------------------------------------


def authorize_tenant_scope(identity: Identity, target_tenant_id: uuid.UUID) -> None:
    """Raise 403 unless ``identity`` may act on ``target_tenant_id``.

    A SUPERADMIN identity (platform-tenant-only, see Role.SUPERADMIN) may target any
    tenant_id. Every other role may only target its own tenant_id — this is the
    baseline (non-bypass) behavior proven for all 6 other roles.

    This predicate answers "which tenant" — a question ``require_permission``
    deliberately does not ask (it only answers "does this role hold this capability").
    Keeping the two orthogonal keeps the ROLE_PERMISSIONS matrix honest: a Permission
    says nothing about which tenant, and every repository still filters
    ``WHERE tenant_id = identity.tenant_id`` regardless of role.

    Dormant by design: no repository or endpoint calls this yet (§1 ⚠ flag, surfaced
    at freeze) — ``platform-admin-console`` wires the first real caller.

    Raises:
        403 ERR_AUTH_FORBIDDEN — identity.role is not SUPERADMIN and
            target_tenant_id != identity.tenant_id.
    """
    if identity.role == Role.SUPERADMIN or identity.tenant_id == target_tenant_id:
        return
    raise AUTH_FORBIDDEN.exc()


# ---------------------------------------------------------------------------
# Impersonation live-session guard (impersonation-live-session-guard TASK.md §3 Part B)
# ---------------------------------------------------------------------------


async def ensure_impersonation_session_live(
    identity: Identity, guard: ImpersonationSessionGuard
) -> None:
    """The ONE place the 'zero overhead for ordinary identities' guarantee is enforced:
    guard.ensure_live() is called iff identity.impersonation is not None. An ordinary
    identity's resolution never constructs a DB-bound guard call, never touches
    impersonation_sessions."""
    if identity.impersonation is not None:
        await guard.ensure_live(identity.impersonation)


async def ensure_session_not_revoked(identity: Identity, guard: SessionRevocationGuard) -> None:
    """The ONE place a revoked session becomes a 401 (auth-hardening-login-sessions
    TASK.md §3 M5, FROZEN @ v1): a revoked=True answer raises the SAME InvalidTokenError
    as a forged/expired token, so the edge response is indistinguishable (no revocation
    oracle). A SessionRevocationUnavailableError from the guard propagates UNTOUCHED —
    the call sites map it to ERR_AUTH_UNAVAILABLE (503, M6), never to a 401 that would
    lie about a live token."""
    try:
        revoked = await guard.is_revoked(identity)
    except SessionRevocationUnavailableError:
        raise
    except Exception as exc:
        # Defense in depth (M6): even an adapter BUG (an exception the guard failed to
        # translate itself) is an unavailable store, never a silent allow and never a
        # 401 — the fail-closed mapping lives HERE so no concrete guard can drop it.
        raise SessionRevocationUnavailableError from exc
    if revoked:
        raise InvalidTokenError


# ---------------------------------------------------------------------------
# Inline identity resolver (avoids circular import with keys/api/deps)
# ---------------------------------------------------------------------------


async def _resolve_identity(
    request: fastapi.Request,
    session: Annotated[AsyncSession, fastapi.Depends(get_session)],
) -> Identity:
    """Decode the Bearer JWT from the request; raise 401 on any failure."""
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AUTH_TOKEN_MISSING.exc()
    import gateway.tenants.domain.ports as _ports  # local import avoids cycle

    token_service: _ports.TokenService = request.app.state.token_service
    try:
        identity = token_service.decode(token)
        # impersonation-live-session-guard TASK.md §3 Part D.1 — call site 1/5.
        from gateway.tenants.infrastructure.impersonation_session_guard import (
            DbImpersonationSessionGuard,
        )

        await ensure_impersonation_session_live(
            identity,
            DbImpersonationSessionGuard(
                session=session,
                timeout_seconds=(
                    request.app.state.settings.impersonation_live_check_timeout_seconds
                ),
            ),
        )
        # auth-hardening-login-sessions TASK.md §3 M5 — revocation call site 1/5,
        # wired at the SAME seam as the impersonation guard above.
        from gateway.tenants.infrastructure.session_revocation import (
            DbSessionRevocationGuard,
        )

        await ensure_session_not_revoked(
            identity,
            DbSessionRevocationGuard(
                session=session,
                timeout_seconds=(
                    request.app.state.settings.session_revocation_check_timeout_seconds
                ),
            ),
        )
        return identity
    except SessionRevocationUnavailableError:
        # M6: the store blinked — NOT a known-bad token, so never the 401 below.
        raise AUTH_UNAVAILABLE.exc() from None
    except InvalidTokenError:
        raise AUTH_TOKEN_INVALID.exc() from None


# ---------------------------------------------------------------------------
# Credential-minting liveness gate (deactivation-escalation fix — verify-phase HARD-STOP
# finding, scim-provisioning TASK.md). NOT part of the frozen §3 contract shape (no route
# signature/response shape changed) — this closes an unintended widening of the CONTRACT's
# own documented M7 residual, it does not alter it.
# ---------------------------------------------------------------------------


async def require_active_user(
    request: fastapi.Request,
    identity: Annotated[Identity, fastapi.Depends(_resolve_identity)],
    session: Annotated[AsyncSession, fastapi.Depends(get_session)],
) -> Identity:
    """Credential-minting liveness gate (scim-provisioning deactivation-escalation fix —
    verify-phase HARD-STOP finding).

    A session JWT is stateless; its documented residual (scim-provisioning TASK.md §1 M7)
    is "usable for ORDINARY requests up to jwt_ttl_seconds after deactivation" — role stays
    baked into the token and deactivation is DB-side only, by design. That residual must
    NOT extend to minting a NEW, independently-long-lived credential (a fresh SCIM token or
    API key that would outlive the JWT itself) — doing so would let a deactivated OWNER/
    ADMIN re-arm their own access indefinitely from an offboarded session. This dependency
    is wired ONLY onto credential-minting routes (SCIM token / API key create + rotate) —
    deliberately NOT onto every hot /admin read, which stays covered by the accepted
    residual above (the builder's own latency tradeoff for ordinary traffic, left intact).

    Fail-CLOSED, bounded-timeout DB read of users.deactivated_at (mirrors
    DbImpersonationSessionGuard's fail-closed convention exactly — a credential-revocation
    decision, not an availability gate, so a transient DB blip must not silently reopen the
    mint-time escalation window this guard exists to close).

    Raises:
        401 ERR_AUTH_INVALID_TOKEN — missing/invalid Bearer token, OR the identity behind
            it has since been deactivated (byte-identical status/body to any other
            invalid-token failure — no oracle distinguishing "deactivated" from
            "malformed").

    Usage::

        @router.post("/admin/scim/tokens")
        async def create_scim_token(
            identity: Annotated[Identity, require_permission(Permission.MEMBERS_MANAGE)],
            _active: Annotated[Identity, fastapi.Depends(require_active_user)],
        ) -> ...: ...
    """
    from gateway.tenants.infrastructure.user_liveness_guard import DbUserLivenessGuard

    guard = DbUserLivenessGuard(
        session=session,
        timeout_seconds=request.app.state.settings.impersonation_live_check_timeout_seconds,
    )
    try:
        await guard.ensure_active(identity.user_id)
    except InvalidTokenError:
        raise AUTH_TOKEN_INVALID.exc() from None
    return identity


# ---------------------------------------------------------------------------
# FastAPI dependency factory
# ---------------------------------------------------------------------------


def require_permission(perm: Permission) -> fastapi.Depends:  # type: ignore[type-arg]
    """Return a FastAPI Depends that passes iff the caller's role holds ``perm``.

    Raises:
        401 ERR_AUTH_INVALID_TOKEN — missing/invalid Bearer token.
        403 ERR_AUTH_FORBIDDEN — role lacks the required permission.

    Usage::

        @router.get("/admin/routing")
        async def get_routing(
            _: Annotated[Identity, require_permission(Permission.ROUTING_MANAGE)],
        ) -> ...: ...
    """

    def _check(
        identity: Annotated[Identity, fastapi.Depends(_resolve_identity)],
    ) -> Identity:
        held = ROLE_PERMISSIONS.get(identity.role, frozenset())
        if perm not in held:
            raise AUTH_FORBIDDEN.exc()
        return identity

    return fastapi.Depends(_check)


def require_superadmin(
    identity: Annotated[Identity, fastapi.Depends(_resolve_identity)],
) -> Identity:
    """FastAPI dependency that passes iff the caller's role is SUPERADMIN.

    Role-only — deliberately NOT a Permission (platform-tenant-directory TASK.md §1
    Framings weighed): "list every tenant" has no single target_tenant_id, so
    ``authorize_tenant_scope`` doesn't apply, and no Permission should imply a
    non-superadmin role could ever meaningfully hold this capability.

    Raises:
        401 ERR_AUTH_INVALID_TOKEN — missing/invalid Bearer token.
        403 ERR_AUTH_FORBIDDEN — role is not SUPERADMIN.

    Usage::

        @router.get("/admin/platform/tenants")
        async def list_tenants(
            identity: Annotated[Identity, Depends(require_superadmin)],
        ) -> ...: ...
    """
    if identity.role != Role.SUPERADMIN:
        raise AUTH_FORBIDDEN.exc()
    return identity
