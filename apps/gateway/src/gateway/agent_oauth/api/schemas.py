"""Pydantic schemas for the agent-principal admin API (contract §3, FROZEN @ v1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from gateway.core.error_catalog import PAYLOAD_MONTHLY_BUDGET_NEGATIVE
from gateway.keys.api.schemas import (
    _parse_decimal_str,  # pyright: ignore[reportPrivateUsage]
    _parse_positive_int,  # pyright: ignore[reportPrivateUsage]
)


class CreateAgentPrincipalRequest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=False, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    owner_user_id: uuid.UUID | None = None
    monthly_budget_usd: str | None = None
    rpm_limit: int | None = None
    tpm_limit: int | None = None

    @field_validator("rpm_limit", mode="before")
    @classmethod
    def validate_rpm_limit(cls, v: Any) -> int | None:
        return _parse_positive_int(v, "rpm_limit")

    @field_validator("tpm_limit", mode="before")
    @classmethod
    def validate_tpm_limit(cls, v: Any) -> int | None:
        return _parse_positive_int(v, "tpm_limit")

    @field_validator("monthly_budget_usd", mode="before")
    @classmethod
    def validate_monthly_budget(cls, v: Any) -> str | None:
        if v is None:
            return None
        d = _parse_decimal_str(v, "monthly_budget_usd")
        if d < Decimal("0"):
            raise PAYLOAD_MONTHLY_BUDGET_NEGATIVE.exc()
        return str(d)


class AgentPrincipalResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    owner_user_id: uuid.UUID | None
    monthly_budget_usd: str | None
    rpm_limit: int | None
    tpm_limit: int | None
    created_at: datetime
    last_seen_at: datetime | None
    killed_at: datetime | None
    attached_token_count: int


class ListAgentPrincipalsResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    agents: list[AgentPrincipalResponse]


class AttachTokenResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    principal_id: uuid.UUID
    token_id: uuid.UUID
    attached_at: datetime


class DetachTokenResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    principal_id: uuid.UUID
    token_id: uuid.UUID
    detached_at: datetime


class KillAgentPrincipalResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    killed_at: datetime
