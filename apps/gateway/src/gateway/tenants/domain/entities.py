import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

MIN_PASSWORD_LENGTH = 10


class Role(StrEnum):
    OWNER = "owner"
    ADMIN = "admin"
    OPERATOR = "operator"
    BILLING_ADMIN = "billing_admin"
    VIEWER = "viewer"
    MEMBER = "member"
    # Platform-tenant-only role (superadmin-role TASK.md §3 CONTRACT — FROZEN @ v1).
    # A superadmin's authz check may target any tenant_id (see authz.authorize_tenant_scope);
    # a DB trigger (migration 5b34ca5e1c4b) enforces this role can only exist under the
    # sole kind='platform' tenant. Never assignable via PUT /admin/users/{id}/role.
    SUPERADMIN = "superadmin"


# billing-owner-of-record TASK.md §3 (FROZEN @ v1) — NEW, public. The set of roles
# eligible to be a tenant's billing_owner_user_id; reused by both guard hook points
# (AssignUserRoleUseCase.execute, SqlAlchemyScimUserRepository.set_active) and the new
# reassignment endpoint's own target validation (ReassignBillingOwnerUseCase.execute).
BILLING_CAPABLE_ROLES: frozenset[Role] = frozenset({Role.OWNER, Role.BILLING_ADMIN})


@dataclass(frozen=True, slots=True)
class User:
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    password_hash: str
    role: Role
    # NEW additive field (scim-provisioning TASK.md §3, FROZEN @ v1). Default None keeps
    # every EXISTING User(...) construction call site unaffected. Mirrors api_keys.revoked_at's
    # nullable-timestamp soft pattern: NULL/None = active; once set, blocks all FUTURE
    # password/OIDC login (LoginUseCase / OidcLoginUseCase) — see authz enforcement there.
    deactivated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ImpersonationContext:
    """Present on an Identity iff it is an impersonation session — the REAL superadmin's
    own identity + the session-store row's id (impersonation-session-lifecycle TASK.md §3
    Part A, FROZEN @ v1). NEVER present together with role=SUPERADMIN
    (JwtTokenService.decode() defensively rejects that combination — see Part B)."""

    session_id: uuid.UUID
    actor_user_id: uuid.UUID
    actor_tenant_id: uuid.UUID
    actor_email: str


@dataclass(frozen=True, slots=True)
class Identity:
    """The authenticated principal carried by a decoded token."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    role: Role
    # NEW additive field (impersonation-session-lifecycle TASK.md §3 Part A, FROZEN @ v1).
    # Default None keeps every EXISTING Identity(...) construction call site (there is
    # exactly one, JwtTokenService.decode()) and every ordinary token unaffected.
    impersonation: ImpersonationContext | None = None


class InviteStatus(StrEnum):
    """Lifecycle of a pending tenant invite (member-invite-issuance TASK.md §3).

    pending -> accepted | revoked (both terminal; no further transition).
    """

    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"


class DomainInviteLinkStatus(StrEnum):
    """Lifecycle of a domain-restricted shareable invite link (invite-by-domain TASK.md §3).

    active -> revoked (terminal; supersede/re-create or explicit DELETE both revoke).
    """

    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class DomainInviteLink:
    """A tenant-scoped, reusable, revocable, 30-day shareable secret an eligible admin
    (member/owner-verified on `domain`) mints so any holder of an @domain mailbox may
    redeem it (after a 6-digit mailbox-proof code) to join the tenant as a MEMBER
    (invite-by-domain TASK.md §3, FROZEN @ v1). The plaintext token is returned exactly
    ONCE at creation and NEVER persisted — only its SHA256 hash lives at rest (token_hash
    is INFRA-ONLY, never on this entity). Never enables stranger auto-join."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    domain: str
    status: DomainInviteLinkStatus
    expires_at: datetime
    created_by_user_id: uuid.UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Plan:
    """A named, superadmin-assignable governance profile for a customer tenant's usage
    ceilings — seat headcount, $ budget default, rate-limit defaults (plan-catalog
    TASK.md §3, FROZEN @ v1).

    Seeded via migration with exactly 3 rows (starter/team/enterprise); no application
    code path creates/edits/deletes a row in v1 — superadmin tier-DEFINITION CRUD is an
    explicit non-goal (the table shape supports it later without a redesign).
    """

    id: uuid.UUID
    name: str
    display_name: str
    seat_cap: int | None
    budget_usd_monthly_default: Decimal | None
    rpm_limit_default: int | None
    tpm_limit_default: int | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Invite:
    """A tenant-scoped, single-use, hashed-at-rest, explicitly-expiring offer to join a
    tenant as a specific role (member-invite-issuance TASK.md §3).

    token_hash is intentionally NOT exposed past the infrastructure boundary — only the
    plaintext token (returned once, at creation) and this row's metadata are domain-visible.
    """

    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    role: Role
    status: InviteStatus
    expires_at: datetime
    invited_by_user_id: uuid.UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class InvitePreview:
    """The unauthenticated, pre-accept preview of a pending invite (member-invite-acceptance
    TASK.md §3): exactly what an invited-but-not-yet-a-user caller may see before setting a
    password. Deliberately excludes id/tenant_id/token_hash/invited_by_user_id/status — an
    unauthenticated caller needs nothing beyond what they are about to join; status is
    implicitly "pending", the ONLY status this type is ever constructed for (see §3 Access
    pattern: PreviewInviteUseCase never builds one for a non-pending invite — that path
    raises instead).
    """

    tenant_name: str
    email: str
    role: Role
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ImpersonationSession:
    """A time-boxed, revocable superadmin impersonation session-store record
    (impersonation-session-lifecycle TASK.md §3 Part D, FROZEN @ v1) — the THIRD use of the
    shipped nullable-revoked_at-timestamp pattern after api_keys and agent_tokens.

    `target_role` is the target user's Role AT MINT TIME, snapshotted — never re-derived
    live from the DB by this task (that live diff is impersonation-live-session-guard's job).
    """

    id: uuid.UUID
    actor_user_id: uuid.UUID
    actor_tenant_id: uuid.UUID
    actor_email: str
    target_user_id: uuid.UUID
    target_tenant_id: uuid.UUID
    target_role: Role
    target_email: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    revoked_reason: str | None = None


@dataclass(frozen=True, slots=True)
class PendingPersonalSignup:
    """A repository-internal transfer object for a consumed pending_personal_signups row
    (scoped-self-serve-signup TASK.md §3, FROZEN @ v1, SECURITY) — NEVER rides any API
    schema (mirrors DomainClaim's code-fields-stay-internal convention). `password_hash`
    is the already-argon2-hashed password computed at issuance (M6); confirm-time reuses
    it unchanged, never re-hashing."""

    email: str
    tenant_name: str
    password_hash: str
