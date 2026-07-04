import uuid
from dataclasses import dataclass
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
class Identity:
    """The authenticated principal carried by a decoded token."""

    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    role: Role
