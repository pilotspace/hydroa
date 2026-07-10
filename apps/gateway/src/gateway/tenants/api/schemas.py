import uuid

from pydantic import BaseModel, EmailStr, Field


class SignupRequest(BaseModel):
    tenant_name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str


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
