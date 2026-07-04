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


@dataclass(frozen=True, slots=True)
class User:
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    password_hash: str
    role: Role


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
