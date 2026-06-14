"""Pydantic schemas for teams API endpoints (contract §3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    """Add a member by user_id OR by email (exactly one) — the email is resolved
    server-side to a tenant-scoped user_id (teams-add-by-email CR §3)."""

    model_config = ConfigDict(frozen=True, strict=False, str_strip_whitespace=True)

    user_id: uuid.UUID | None = None
    email: str | None = None
    role: Literal["lead", "member"]

    @model_validator(mode="after")
    def _exactly_one_identifier(self) -> AddMemberRequest:
        has_user_id = self.user_id is not None
        has_email = self.email is not None and self.email != ""
        if has_user_id == has_email:
            raise ValueError("provide exactly one of 'user_id' or 'email'")
        return self


class AddMemberResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    team_id: uuid.UUID
    user_id: uuid.UUID
    role: str
    added_at: datetime
