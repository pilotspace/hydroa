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
