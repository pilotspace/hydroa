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

from enum import StrEnum
from typing import Annotated

import fastapi

from gateway.core.error_catalog import AUTH_FORBIDDEN, AUTH_TOKEN_INVALID, AUTH_TOKEN_MISSING
from gateway.tenants.domain.entities import Identity, Role
from gateway.tenants.domain.errors import InvalidTokenError

__all__ = ["ROLE_PERMISSIONS", "Permission", "Role", "require_permission"]


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
        }
    ),
    Role.BILLING_ADMIN: frozenset(
        {
            Permission.BUDGETS_MANAGE,
            Permission.USAGE_READ,
            Permission.OPS_READ,
        }
    ),
    Role.VIEWER: frozenset(
        {
            Permission.USAGE_READ,
            Permission.OPS_READ,
        }
    ),
    Role.MEMBER: frozenset(),  # none of the admin permissions
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
# Inline identity resolver (avoids circular import with keys/api/deps)
# ---------------------------------------------------------------------------


def _resolve_identity(request: fastapi.Request) -> Identity:
    """Decode the Bearer JWT from the request; raise 401 on any failure."""
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AUTH_TOKEN_MISSING.exc()
    import gateway.tenants.domain.ports as _ports  # local import avoids cycle

    token_service: _ports.TokenService = request.app.state.token_service
    try:
        return token_service.decode(token)
    except InvalidTokenError:
        raise AUTH_TOKEN_INVALID.exc() from None


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
