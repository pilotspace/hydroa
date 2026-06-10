"""Pydantic schemas for keys API endpoints (contract §3)."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CreateKeyRequest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=False, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)


class CreateKeyResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    key_id: uuid.UUID
    name: str
    key: str  # plaintext "sk-<hex>.<secret>" — shown EXACTLY ONCE


class KeyInfoResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    key_id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    revoked_at: datetime | None


class AuthzResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: uuid.UUID
    key_id: uuid.UUID
