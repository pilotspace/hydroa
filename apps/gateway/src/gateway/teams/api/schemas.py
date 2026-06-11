"""Pydantic schemas for teams API endpoints (contract §3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CreateTeamRequest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=False, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)


class PatchTeamBudgetRequest(BaseModel):
    """Request body for PATCH /admin/teams/{team_id} — sets or clears team_budget_usd."""

    model_config = ConfigDict(frozen=True, strict=False)

    team_budget_usd: str | None = None

    @field_validator("team_budget_usd")
    @classmethod
    def validate_team_budget_usd(cls, v: str | None) -> str | None:
        if v is None:
            return None
        try:
            val = Decimal(v)
        except Exception:
            raise ValueError("team_budget_usd must be a valid numeric string") from None
        if val <= Decimal("0"):
            raise ValueError("team_budget_usd must be positive")
        return v


class TeamResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    name: str
    tenant_id: uuid.UUID
    created_at: datetime
    member_count: int
    key_count: int
    team_budget_usd: str | None = None


class MemberResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: uuid.UUID
    role: str
    added_at: datetime


class TeamDetailResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    name: str
    tenant_id: uuid.UUID
    created_at: datetime
    member_count: int
    key_count: int
    members: list[MemberResponse]
    team_budget_usd: str | None = None


class AddMemberRequest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=False)

    user_id: uuid.UUID
    role: Literal["lead", "member"]


class AddMemberResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    team_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    added_at: datetime
