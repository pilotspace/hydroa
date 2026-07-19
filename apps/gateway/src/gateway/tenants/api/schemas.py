import uuid
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    tenant_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str
    # ADDITIVE (account-type-discriminator TASK.md §3 M2 — FROZEN @ v1): the personal|
    # business flavor of the new tenant. Literal → pydantic rejects any other value with
    # 422 BEFORE the use case runs (R1). Default 'business' keeps every EXISTING signup
    # body (which omits it) byte-identical to the pre-discriminator contract.
    account_type: Literal["personal", "business"] = "business"


class SignupResponse(BaseModel):
    tenant_id: uuid.UUID
    user_id: uuid.UUID
    # ADDITIVE (domain-capture TASK.md §3 M12 — FROZEN @ v1): default False keeps every
    # EXISTING SignupResponse(...) construction call site (create-new-tenant path)
    # unaffected. True ONLY on the domain-routed join-existing-tenant path (M9-M11).
    joined_existing_tenant: bool = False


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 — token scheme name, not a secret
    expires_in: int


class MeResponse(BaseModel):
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    role: str
    # NEW additive field (domain-claims-console TASK.md §4 CR 2026-07-19): the caller's
    # OWN tenant display name, so the dashboard joined-workspace callout can name the
    # workspace from session context. Empty string if the tenant row is somehow absent
    # (a valid session always has one; /me must never 500 on this).
    tenant_name: str = ""
