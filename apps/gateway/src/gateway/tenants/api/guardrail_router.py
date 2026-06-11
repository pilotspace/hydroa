"""Admin API router for tenant-level guardrail configuration.

Contract FROZEN @ v4 (guardrails-core TASK.md §3):
  GET  /admin/guardrails  — any authenticated role; returns current guardrail config
  PUT  /admin/guardrails  — owner or admin only; member → 403
                            body: partial update (absent keys preserved)
                            returns: full merged config after update
  422 on invalid mode value (pii_mask mode=block, prompt_injection mode=mask)
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.keys.api.deps import get_identity, require_owner_or_admin
from gateway.tenants.domain.entities import Identity

guardrail_router = APIRouter(prefix="/admin/guardrails", tags=["guardrails"])

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class PromptInjectionConfig(BaseModel):
    """Config for the prompt_injection guardrail.

    Valid modes: block | audit  (mask is NOT valid for prompt injection)
    """

    enabled: bool
    mode: str

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        allowed = {"block", "audit"}
        if v not in allowed:
            raise ValueError(f"prompt_injection mode must be one of {allowed!r}, got {v!r}")
        return v


class PiiMaskConfig(BaseModel):
    """Config for the pii_mask guardrail.

    Valid modes: mask | audit  (block is NOT valid for pii_mask)
    """

    enabled: bool
    mode: str

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        allowed = {"mask", "audit"}
        if v not in allowed:
            raise ValueError(f"pii_mask mode must be one of {allowed!r}, got {v!r}")
        return v


class GuardrailConfigRequest(BaseModel):
    """PUT /admin/guardrails request body.

    Partial update: absent top-level keys preserve existing values.
    Present keys fully replace that guardrail's config.
    Sending null for a key removes/disables that guardrail.
    """

    prompt_injection: PromptInjectionConfig | None = None
    pii_mask: PiiMaskConfig | None = None


class GuardrailConfigResponse(BaseModel):
    """GET/PUT /admin/guardrails response body."""

    prompt_injection: dict[str, Any] | None = None
    pii_mask: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _build_response(configs: dict[str, Any]) -> GuardrailConfigResponse:
    """Convert raw guardrail_configs dict to GuardrailConfigResponse."""
    pi = configs.get("prompt_injection")
    pm = configs.get("pii_mask")
    return GuardrailConfigResponse(
        prompt_injection=pi if isinstance(pi, dict) else None,
        pii_mask=pm if isinstance(pm, dict) else None,
    )


async def _fetch_guardrail_configs(session: AsyncSession, tenant_id: str) -> dict[str, Any]:
    """Fetch current guardrail_configs for a tenant; return empty dict on miss."""
    row = (
        await session.execute(
            text("SELECT guardrail_configs FROM tenants WHERE id = :tid"),
            {"tid": tenant_id},
        )
    ).fetchone()
    if row is None or row[0] is None:
        return {}
    raw = row[0]
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@guardrail_router.get("", response_model=GuardrailConfigResponse)
async def get_guardrails(
    identity: Annotated[Identity, Depends(get_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GuardrailConfigResponse:
    """GET /admin/guardrails — return current tenant-level guardrail config.

    Accessible to any authenticated role (owner, admin, member).
    """
    configs = await _fetch_guardrail_configs(session, str(identity.tenant_id))
    return _build_response(configs)


@guardrail_router.put("", response_model=GuardrailConfigResponse)
async def put_guardrails(
    body: GuardrailConfigRequest,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GuardrailConfigResponse:
    """PUT /admin/guardrails — update tenant-level guardrail config (partial merge).

    Requires role owner or admin; member → 403 ERR_AUTH_FORBIDDEN.
    Absent top-level keys preserve existing values.
    Present keys fully replace that guardrail's config.
    null value for a key removes that guardrail's config.
    """
    tenant_id = str(identity.tenant_id)

    # Fetch current config for merge
    current = await _fetch_guardrail_configs(session, tenant_id)

    # Partial merge: only update keys that were explicitly provided
    updated = dict(current)

    # Check which fields were explicitly set in the request
    # Pydantic model_fields_set contains only explicitly provided fields
    fields_set = body.model_fields_set

    if "prompt_injection" in fields_set:
        if body.prompt_injection is None:
            updated.pop("prompt_injection", None)
        else:
            updated["prompt_injection"] = body.prompt_injection.model_dump()

    if "pii_mask" in fields_set:
        if body.pii_mask is None:
            updated.pop("pii_mask", None)
        else:
            updated["pii_mask"] = body.pii_mask.model_dump()

    # Persist the merged config as JSONB
    await session.execute(
        text("UPDATE tenants SET guardrail_configs = :val::jsonb WHERE id = :tid"),
        {"val": json.dumps(updated), "tid": tenant_id},
    )
    await session.commit()

    return _build_response(updated)
