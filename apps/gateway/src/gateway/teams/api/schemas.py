"""Pydantic schemas for teams API endpoints (contract §3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CreateTeamRequest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=False, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)


class TeamResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    name: str
    tenant_id: uuid.UUID
    created_at: datetime
    member_count: int
    key_count: int


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
