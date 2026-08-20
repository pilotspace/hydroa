import uuid
from datetime import datetime
from typing import Protocol

from gateway.tenants.domain.entities import (
    Identity,
    ImpersonationContext,
    PasswordResetToken,
    PendingPersonalSignup,
    Role,
    User,
)
from gateway.tenants.domain.entitlements import ResolvedEntitlements


class IdentityRepository(Protocol):
    async def create_tenant_with_owner(
        self,
        *,
        tenant_name: str,
        email: str,
        password_hash: str,
        account_type: str = "business",
        plan_id: uuid.UUID | None = None,
    ) -> tuple[uuid.UUID, uuid.UUID]:
        """Create tenant + owner atomically; returns (tenant_id, user_id).

        Raises EmailAlreadyRegisteredError; on any failure neither row persists.

        ADDITIVE (account-type-discriminator TASK.md §3 — FROZEN @ v1): account_type
        (personal|business, default 'business' keeps every EXISTING call site
        byte-identical) and plan_id (the personal tier lands on the seeded `individual`
        plan; None for business — unplanned as today) are set on the new TenantRow.
        """
        ...

    async def get_plan_id_by_name(self, name: str) -> uuid.UUID | None:
        """ADDITIVE (account-type-discriminator TASK.md §3 — FROZEN @ v1): resolve a
        seed plan's id by its UNIQUE name (e.g. 'individual'); None if absent."""
        ...

    async def get_user_by_email(self, email: str) -> User | None: ...

    async def create_password_reset_token(
        self, *, user_id: uuid.UUID, token_hash: str, expires_at: datetime
    ) -> None:
        """ADDITIVE (auth-hardening-login-sessions TASK.md §3 M3 — FROZEN @ v1,
        SECURITY): persist one hashed-at-rest, TTL-bounded reset token row. The raw
        token never reaches this method."""
        ...

    async def get_password_reset_token(self, *, token_hash: str) -> PasswordResetToken | None:
        """Read one reset-token row (joined with its user) by hash; None if absent."""
        ...

    async def consume_password_reset_token(
        self, *, token_hash: str, password_hash: str, not_before: datetime
    ) -> bool:
        """ADDITIVE (auth-hardening-login-sessions TASK.md §3 M3/M4 — FROZEN @ v1,
        SECURITY): in ONE transaction, mark the token used (guarded WHERE used_at IS
        NULL — single-use under concurrency, the consume_pending_signup idiom), set the
        user's password_hash, and advance users.sessions_not_before to not_before (M4:
        every pre-reset session dies at the identity seam). False iff the guarded
        UPDATE matched no row (already consumed concurrently) — nothing else persists."""
        ...

    async def revoke_session(self, *, jti: str, user_id: uuid.UUID, expires_at: datetime) -> None:
        """ADDITIVE (auth-hardening-login-sessions TASK.md §3 M5 — FROZEN @ v1,
        SECURITY): denylist one session jti until expires_at (the token's own ceiling
        bounds row lifetime). Idempotent (double-logout is a no-op, never an error);
        opportunistically GCs already-expired rows."""
        ...

    async def issue_or_reissue_pending_signup(
        self,
        *,
        email: str,
        tenant_name: str,
        password_hash: str,
        confirm_token_hash: str,
        expires_at: datetime,
    ) -> None:
        """ADDITIVE (scoped-self-serve-signup TASK.md §3 M8 — FROZEN @ v1, SECURITY):
        UPSERT-by-email (create-or-reissue idiom, mirrors CreateDomainClaimUseCase) — a
        repeat not-yet-confirmed submission for the SAME email overwrites token hash +
        expiry; the previous token stops working."""
        ...

    async def consume_pending_signup(
        self, *, confirm_token_hash: str
    ) -> PendingPersonalSignup | None:
        """ADDITIVE (M10 — FROZEN @ v1, SECURITY): one atomic
        `DELETE ... WHERE confirm_token_hash = :hash AND expires_at > now() RETURNING *`
        — single-use by construction; a concurrent double-confirm of the SAME token can
        never both return a row. None on a miss (unknown / already-consumed / expired)."""
        ...

    async def pop_expired_pending_signup(self, *, confirm_token_hash: str) -> bool:
        """ADDITIVE (M11 — FROZEN @ v1, SECURITY): called ONLY when consume_pending_signup
        misses, to distinguish "expired" from "never existed / already consumed" and
        opportunistically clean up. True iff a matching (expired) row was deleted."""
        ...

    async def get_or_provision_oidc_user(
        self,
        *,
        email: str,
        tenant_id: uuid.UUID,
        password_hash: str,
    ) -> tuple[User, bool]:
        """Get existing user by email OR create with role=member if absent.

        Returns (user, newly_provisioned) — newly_provisioned is True IFF this
        call INSERTed the user (domain-auto-assign-login TASK.md §3 M1).

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
    ) -> tuple[User, bool]:
        """Get existing user by email OR create with role=member if absent.

        Returns (user, newly_provisioned) — newly_provisioned is True IFF this
        call INSERTed the user (domain-auto-assign-login TASK.md §3 M1).

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


class SessionRevocationGuard(Protocol):
    """Per-request revocation check for the presented session JWT itself
    (auth-hardening-login-sessions TASK.md §3 M5/M6, FROZEN @ v1). Modeled on
    ImpersonationSessionGuard above, but answers a QUESTION instead of raising: the
    caller (ensure_session_not_revoked, tenants/domain/authz.py) owns the mapping of
    True -> the same 401 as any invalid token, so a revoked session is
    indistinguishable from a never-valid one at the edge (no revocation oracle)."""

    async def is_revoked(self, identity: Identity) -> bool:
        """True iff the token must be refused: its jti is denylisted
        (POST /admin/auth/logout) or its iat strictly predates the user's
        sessions_not_before watermark (password-reset confirm). Raise
        SessionRevocationUnavailableError (tenants/domain/errors.py) iff the answer
        cannot be produced within the adapter's own bounded timeout — fail-CLOSED via
        the 503 path (M6), never a silent allow and never a lying 401."""
        ...


class PlanEntitlementResolver(Protocol):
    """Read-only, in-process entitlement resolution port (plan-enforcement TASK.md §3, M8,
    FROZEN @ v1). Named consumer: seat-billing (wave-2) calls `.resolve(tenant_id)` directly
    — same precedence order as `resolve_entitlements` (M1). ZERO new HTTP surface: no
    tenant/admin-facing endpoint exposes this (explicit Non-goal)."""

    async def resolve(self, tenant_id: uuid.UUID) -> ResolvedEntitlements:
        """Resolve every entitlement dimension for one tenant, read-only.

        Same precedence as `resolve_entitlements` (M1): explicit tenant setting > plan
        default > unlimited for budget; plan_model_allowlist/plan_feature_flags are the
        assigned plan's own values (empty/None when unplanned). Never writes anything.
        """
        ...
