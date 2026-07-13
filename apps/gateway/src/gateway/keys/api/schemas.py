"""Pydantic schemas for keys API endpoints (contract §3)."""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from gateway.core.error_catalog import (
    PAYLOAD_ALLOWLIST_BAD_ELEMENT,
    PAYLOAD_ALLOWLIST_NOT_LIST,
    PAYLOAD_DECIMAL_INVALID,
    PAYLOAD_INT_INVALID,
    PAYLOAD_INT_NOT_POSITIVE,
    PAYLOAD_MONTHLY_BUDGET_NEGATIVE,
    PAYLOAD_SOFT_BUDGET_NEGATIVE,
    PAYLOAD_SOFT_EXCEEDS_HARD,
    PAYLOAD_TIER_INVALID,
)

_VALID_TIERS = {"priority", "standard"}


def _validate_tier(v: Any) -> str | None:
    """service-tiers TASK.md §3 R1: tier not in {priority, standard, null} -> 422."""
    if v is None:
        return None
    if not isinstance(v, str) or v not in _VALID_TIERS:
        raise PAYLOAD_TIER_INVALID.exc()
    return v


class CreateKeyRequest(BaseModel):
    model_config = ConfigDict(frozen=True, strict=False, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    monthly_budget_usd: str | None = None
    soft_budget_usd: str | None = None
    expires_at: str | None = None
    model_allowlist: list[str] | None = None
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    # teams-core additive field — optional; null = un-teamed
    team_id: uuid.UUID | None = None
    # response-caching additive field — default false
    cache_enabled: bool = False
    # service-tiers additive field — omit/null = inherit tenant default (§3 M2)
    tier: str | None = None

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

    @field_validator("soft_budget_usd", mode="before")
    @classmethod
    def validate_soft_budget(cls, v: Any) -> str | None:
        if v is None:
            return None
        d = _parse_decimal_str(v, "soft_budget_usd")
        if d < Decimal("0"):
            raise PAYLOAD_SOFT_BUDGET_NEGATIVE.exc()
        return str(d)

    @field_validator("model_allowlist", mode="before")
    @classmethod
    def validate_allowlist(cls, v: Any) -> list[str] | None:
        if v is None:
            return None
        if not isinstance(v, list):
            raise PAYLOAD_ALLOWLIST_NOT_LIST.exc()
        for item in v:
            if not isinstance(item, str) or item == "":
                raise PAYLOAD_ALLOWLIST_BAD_ELEMENT.exc()
        return v

    @field_validator("tier", mode="before")
    @classmethod
    def validate_tier(cls, v: Any) -> str | None:
        return _validate_tier(v)

    @model_validator(mode="after")
    def validate_soft_lte_hard(self) -> "CreateKeyRequest":
        if self.monthly_budget_usd is not None and self.soft_budget_usd is not None:
            hard = Decimal(self.monthly_budget_usd)
            soft = Decimal(self.soft_budget_usd)
            if soft > hard:
                raise PAYLOAD_SOFT_EXCEEDS_HARD.exc()
        return self


class PatchKeyRequest(BaseModel):
    """PATCH /admin/keys/{key_id} body — all fields optional; omit = no change.

    Uses a sentinel UNSET value so the router can distinguish:
      - field absent from JSON → not in model_fields_set → no change
      - field present as null  → in model_fields_set, value None → clear to NULL
      - field present as value → in model_fields_set, value str → update
    """

    model_config = ConfigDict(frozen=True, strict=False, str_strip_whitespace=True)

    monthly_budget_usd: str | None = None
    soft_budget_usd: str | None = None
    expires_at: str | None = None
    model_allowlist: list[str] | None = None
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    # teams-core additive field — absent = no change; null = clear; UUID = set
    team_id: uuid.UUID | None = None
    # response-caching additive field — absent = no change; True/False = set
    cache_enabled: bool | None = None
    # payload-capture-store additive field — absent = no change; True/False = set
    capture_enabled: bool | None = None
    # service-tiers additive field — absent = no change; null = clear (revert to
    # tenant default); value = set the key-level override (§3 M2)
    tier: str | None = None

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

    @field_validator("soft_budget_usd", mode="before")
    @classmethod
    def validate_soft_budget(cls, v: Any) -> str | None:
        if v is None:
            return None
        d = _parse_decimal_str(v, "soft_budget_usd")
        if d < Decimal("0"):
            raise PAYLOAD_SOFT_BUDGET_NEGATIVE.exc()
        return str(d)

    @field_validator("model_allowlist", mode="before")
    @classmethod
    def validate_allowlist(cls, v: Any) -> list[str] | None:
        if v is None:
            return None
        if not isinstance(v, list):
            raise PAYLOAD_ALLOWLIST_NOT_LIST.exc()
        for item in v:
            if not isinstance(item, str) or item == "":
                raise PAYLOAD_ALLOWLIST_BAD_ELEMENT.exc()
        return v

    @field_validator("tier", mode="before")
    @classmethod
    def validate_tier(cls, v: Any) -> str | None:
        return _validate_tier(v)

    @model_validator(mode="after")
    def validate_soft_lte_hard(self) -> "PatchKeyRequest":
        if self.monthly_budget_usd is not None and self.soft_budget_usd is not None:
            hard = Decimal(self.monthly_budget_usd)
            soft = Decimal(self.soft_budget_usd)
            if soft > hard:
                raise PAYLOAD_SOFT_EXCEEDS_HARD.exc()
        return self


class RotateKeyRequest(BaseModel):
    """POST /admin/keys/{key_id}/rotate body — all fields optional; omit = inherit from old row."""

    model_config = ConfigDict(frozen=True, strict=False, str_strip_whitespace=True)

    monthly_budget_usd: str | None = None
    soft_budget_usd: str | None = None
    expires_at: str | None = None
    model_allowlist: list[str] | None = None

    @field_validator("monthly_budget_usd", mode="before")
    @classmethod
    def validate_monthly_budget(cls, v: Any) -> str | None:
        if v is None:
            return None
        d = _parse_decimal_str(v, "monthly_budget_usd")
        if d < Decimal("0"):
            raise PAYLOAD_MONTHLY_BUDGET_NEGATIVE.exc()
        return str(d)

    @field_validator("soft_budget_usd", mode="before")
    @classmethod
    def validate_soft_budget(cls, v: Any) -> str | None:
        if v is None:
            return None
        d = _parse_decimal_str(v, "soft_budget_usd")
        if d < Decimal("0"):
            raise PAYLOAD_SOFT_BUDGET_NEGATIVE.exc()
        return str(d)

    @field_validator("model_allowlist", mode="before")
    @classmethod
    def validate_allowlist(cls, v: Any) -> list[str] | None:
        if v is None:
            return None
        if not isinstance(v, list):
            raise PAYLOAD_ALLOWLIST_NOT_LIST.exc()
        for item in v:
            if not isinstance(item, str) or item == "":
                raise PAYLOAD_ALLOWLIST_BAD_ELEMENT.exc()
        return v


class CreateKeyResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    key_id: uuid.UUID
    name: str
    key: str  # plaintext "sk-<hex>.<secret>" — shown EXACTLY ONCE
    monthly_budget_usd: str | None = None
    soft_budget_usd: str | None = None
    expires_at: str | None = None
    model_allowlist: list[str] | None = None
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    # teams-core additive field — null = un-teamed
    team_id: uuid.UUID | None = None
    # response-caching additive field
    cache_enabled: bool = False
    # service-tiers additive field — null = inherit tenant default
    tier: str | None = None


class PlaygroundTokenResponse(BaseModel):
    """POST /admin/keys/playground-token — a short-lived, spend-capped sk- key for the
    dashboard BFF's server-side /v1 calls. The secret is shown EXACTLY ONCE; the browser
    never receives it (the BFF caches it server-side until expires_at)."""

    model_config = ConfigDict(frozen=True)

    key: str  # plaintext "sk-<hex>.<secret>" — shown ONCE, BFF-side only
    expires_at: str | None = None


class RotateKeyResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    new_key_id: uuid.UUID
    superseded_key_id: uuid.UUID
    key: str  # plaintext new secret, shown ONCE
    name: str
    monthly_budget_usd: str | None = None
    soft_budget_usd: str | None = None
    expires_at: str | None = None
    model_allowlist: list[str] | None = None


class KeyInfoResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    key_id: uuid.UUID
    name: str
    prefix: str
    created_at: datetime
    revoked_at: datetime | None
    monthly_budget_usd: str | None = None
    soft_budget_usd: str | None = None
    expires_at: datetime | None = None
    model_allowlist: list[str] | None = None
    rpm_limit: int | None = None
    tpm_limit: int | None = None
    # teams-core additive field — null = un-teamed
    team_id: uuid.UUID | None = None
    # response-caching additive field
    cache_enabled: bool = False
    # payload-capture-store additive field
    capture_enabled: bool = False
    # service-tiers additive field — null = inherit tenant default
    tier: str | None = None


class AuthzResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: uuid.UUID
    key_id: uuid.UUID


def _parse_decimal_str(v: Any, field_name: str) -> Decimal:
    """Parse a value to Decimal; raise ProblemError 422 on failure."""
    try:
        return Decimal(str(v))
    except Exception:
        raise PAYLOAD_DECIMAL_INVALID.exc(field_name=field_name) from None


def _parse_positive_int(v: Any, field_name: str) -> int | None:
    """Parse a value to a positive integer; raise ProblemError 422 if zero/negative."""
    if v is None:
        return None
    try:
        val = int(v)
    except (TypeError, ValueError):
        raise PAYLOAD_INT_INVALID.exc(field_name=field_name) from None
    if val <= 0:
        raise PAYLOAD_INT_NOT_POSITIVE.exc(field_name=field_name)
    return val
