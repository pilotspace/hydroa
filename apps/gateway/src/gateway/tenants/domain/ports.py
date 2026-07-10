import uuid
from typing import Protocol

from gateway.tenants.domain.entities import Identity, ImpersonationContext, Role, User


class IdentityRepository(Protocol):
    async def create_tenant_with_owner(
        self, *, tenant_name: str, email: str, password_hash: str
    ) -> tuple[uuid.UUID, uuid.UUID]:
        """Create tenant + owner atomically; returns (tenant_id, user_id).

        Raises EmailAlreadyRegisteredError; on any failure neither row persists.
        """
        ...

    async def get_user_by_email(self, email: str) -> User | None: ...

    async def get_or_provision_oidc_user(
        self,
        *,
        email: str,
        tenant_id: uuid.UUID,
        password_hash: str,
    ) -> User:
        """Get existing user by email OR create with role=member if absent.

        Raises OidcTenantConflictError if the user exists bound to a different tenant_id.
        The provisioned user always has role=member regardless of any claims.
        """
        ...

    async def get_or_provision_saml_user(
        self,
        *,
        email: str,
        tenant_id: uuid.UUID,
        password_hash: str,
    ) -> User:
        """Get existing user by email OR create with role=member if absent.

        ADDITIVE (saml-sso TASK.md §3 Part E — FROZEN @ v1): mirrors
        get_or_provision_oidc_user byte-for-byte (existing method's signature
        is untouched); both delegate to a shared private repository helper
        parameterized by auth_method ("oidc" vs "saml").

        Raises SamlTenantConflictError if the user exists bound to a different tenant_id.
        The provisioned user always has role=member regardless of any assertion attribute.
        """
        ...

    async def join_verified_tenant_domain(
        self, *, tenant_id: uuid.UUID, email: str, password_hash: str
    ) -> uuid.UUID:
        """ADDITIVE (domain-capture TASK.md §3 M9 — FROZEN @ v1): ONE INSERT of a new
        UserRow(tenant_id=<claimed tenant>, role=Role.MEMBER); returns the new user_id.

        Deliberately INSERT-only, NOT get-or-provision: catches the `users.email` UNIQUE
        `IntegrityError` and re-raises EmailAlreadyRegisteredError — see
        domain-capture TASK.md §0 Issues for why this does NOT share
        _get_or_provision_sso_user's get-or-return-existing shape (that shape is correct
        for repeat SSO LOGIN, but a real account-enumeration + false-success bug for a
        SIGNUP-shaped operation: it would let an attacker submit ANY password against a
        real victim's already-registered email and receive a misleading 201 success).
        """
        ...


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...

    def verify(self, password_hash: str | None, password: str) -> bool:
        """False on mismatch or None hash; MUST cost the same time either way
        (no user enumeration through timing)."""
        ...


class TokenService(Protocol):
    def issue(
        self,
        *,
        user_id: uuid.UUID,
        tenant_id: uuid.UUID,
        role: Role,
        email: str,
        # NEW optional kwargs (impersonation-session-lifecycle TASK.md §3 Part B, FROZEN @
        # v1) — both None ⇒ byte-identical claims dict to today for every EXISTING caller.
        impersonation: ImpersonationContext | None = None,
        ttl_seconds: int | None = None,
    ) -> tuple[str, int]:
        """Returns (signed token, expires_in seconds)."""
        ...

    def decode(self, token: str) -> Identity:
        """Raises InvalidTokenError on any failure (signature, expiry, issuer, shape)."""
        ...


class ImpersonationSessionGuard(Protocol):
    """Per-request liveness check for an impersonation session (impersonation-live-session-
    guard TASK.md §3 Part A, FROZEN @ v1) — modeled on TokenService above."""

    async def ensure_live(self, impersonation: ImpersonationContext) -> None:
        """Raise InvalidTokenError (tenants/domain/errors.py) iff the session named by
        impersonation.session_id is not live, OR liveness cannot be confirmed within the
        adapter's own bounded timeout (fail-CLOSED — no distinction surfaced to the
        caller). No-op (returns None) iff the session IS live. Never called for an
        ordinary (non-impersonation) identity — see ensure_impersonation_session_live."""
        ...


class UserLivenessGuard(Protocol):
    """Per-request liveness check for the user BEHIND a session JWT (scim-provisioning
    deactivation-escalation fix — verify-phase HARD-STOP finding). Modeled directly on
    ImpersonationSessionGuard above: a stateless session JWT's documented residual is
    "usable for ORDINARY requests up to jwt_ttl_seconds after deactivation" — it must NOT
    extend to minting a NEW, independently-long-lived credential (a SCIM token or API key
    that would outlive the JWT itself). Only wired onto credential-MINTING routes, never
    onto every hot /admin read — see require_active_user's own docstring."""

    async def ensure_active(self, user_id: uuid.UUID) -> None:
        """Raise InvalidTokenError (tenants/domain/errors.py) iff an EXISTING UserRow named
        by user_id has deactivated_at set, OR liveness cannot be confirmed within the
        adapter's own bounded timeout (fail-CLOSED on a DB error/timeout — mirrors
        ImpersonationSessionGuard, a credential-revocation decision, not an availability
        gate). A MISSING row is deliberately NOT a failure here (out of scope for this
        guard — every other identity check in this codebase is role/tenant-claims-only and
        never validates user_id resolves to a real row). No-op (returns None) iff the user
        is active or the row does not exist."""
        ...
