"""Admin API router for tenant-level guardrail configuration.

Contract FROZEN @ v4 (guardrails-core TASK.md §3):
  GET  /admin/guardrails  — any authenticated role; returns current guardrail config
  PUT  /admin/guardrails  — owner or admin only; member → 403
                            body: partial update (absent keys preserved)
                            returns: full merged config after update
  422 on invalid mode value (pii_mask mode=block, prompt_injection mode=mask)

pii-v2 extension (TASK.md §3 CONTRACT — FROZEN):
  PiiMaskConfig gains optional pii_custom_patterns field.
  CustomPatternItem: name (^[A-Z][A-Z0-9_]{0,31}$), pattern (str)
  Validation rules V1-V7 applied at PUT time; first failure → 422 ERR_PAYLOAD_INVALID.
  Literal is NOT stored — derived server-side as "[{name}_REDACTED]".
  pii_custom_patterns absent in PUT pii_mask block → removes custom list (list-replace).
  pii_mask absent entirely → preserves existing pii_mask config (standard partial-merge).
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.db import get_session
from gateway.core.error_catalog import PAYLOAD_CUSTOM_PATTERN_INVALID
from gateway.keys.api.deps import get_identity, require_owner_or_admin
from gateway.tenants.application.entitlements import check_plan_feature
from gateway.tenants.domain.entities import Identity

guardrail_router = APIRouter(prefix="/admin/guardrails", tags=["guardrails"])

# ---------------------------------------------------------------------------
# pii-v2 validation constants (§3 CONTRACT — FROZEN)
# ---------------------------------------------------------------------------
_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
_NESTED_QUANTIFIER_RE = re.compile(r"\([^)]*[+*?][^)]*\)[+*?{]")
# Numeric backrefs (\1..\9) by regex; named backrefs ((?P=name)) by plain substring —
# the substring cannot occur in a correctly-escaped literal, and rejecting the odd
# char-class containing it is the right conservative direction for untrusted input.
_BACKREFERENCE_RE = re.compile(r"\\[1-9]")
_NAMED_BACKREFERENCE_TOKEN = "(?P="  # noqa: S105 — regex syntax fragment, not a secret
_MAX_CUSTOM_PATTERNS = 8
_MAX_PATTERN_BYTES = 256


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


class CustomPatternItem(BaseModel):
    """A single tenant-supplied custom PII pattern.

    name:    must match ^[A-Z][A-Z0-9_]{0,31}$
    pattern: tenant regex string (validated V1-V7 at PUT time)

    The literal field is NOT accepted here — it is derived server-side as
    f"[{name}_REDACTED]". Any literal supplied in the JSON body is silently
    ignored by Pydantic (model does not declare the field).
    """

    name: str
    pattern: str


class PiiMaskConfig(BaseModel):
    """Config for the pii_mask guardrail.

    Valid modes: mask | audit  (block is NOT valid for pii_mask)
    pii_custom_patterns: optional list of custom patterns (validated at PUT time).
    """

    enabled: bool
    mode: str
    pii_custom_patterns: list[CustomPatternItem] | None = None

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        allowed = {"mask", "audit"}
        if v not in allowed:
            raise ValueError(f"pii_mask mode must be one of {allowed!r}, got {v!r}")
        return v


class MlModerationConfig(BaseModel):
    """Config for the ml_moderation guardrail.

    ml-moderation-layer TASK.md §3 CONTRACT — FROZEN @ v1.
    Valid modes: block | audit. failure_mode is orthogonal to mode — it governs what
    happens when the CHECK ITSELF could not be evaluated (missing key, timeout,
    provider outage), not what happens when content IS flagged. Defaults to
    "fail_open" when omitted.
    """

    enabled: bool
    mode: str
    failure_mode: str = "fail_open"

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        allowed = {"block", "audit"}
        if v not in allowed:
            raise ValueError(f"ml_moderation mode must be one of {allowed!r}, got {v!r}")
        return v

    @field_validator("failure_mode")
    @classmethod
    def validate_failure_mode(cls, v: str) -> str:
        allowed = {"fail_open", "fail_closed"}
        if v not in allowed:
            raise ValueError(f"ml_moderation failure_mode must be one of {allowed!r}, got {v!r}")
        return v


class GuardrailConfigRequest(BaseModel):
    """PUT /admin/guardrails request body.

    Partial update: absent top-level keys preserve existing values.
    Present keys fully replace that guardrail's config.
    Sending null for a key removes/disables that guardrail.
    """

    prompt_injection: PromptInjectionConfig | None = None
    pii_mask: PiiMaskConfig | None = None
    ml_moderation: MlModerationConfig | None = None


class GuardrailConfigResponse(BaseModel):
    """GET/PUT /admin/guardrails response body."""

    prompt_injection: dict[str, Any] | None = None
    pii_mask: dict[str, Any] | None = None
    ml_moderation: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# pii-v2 validation (§3 CONTRACT V1-V7, applied in order at PUT time)
# ---------------------------------------------------------------------------


def _validate_custom_patterns(patterns: list[CustomPatternItem]) -> None:
    """Apply V1-V7 validation rules to the custom pattern list.

    Raises ProblemError 422 ERR_PAYLOAD_INVALID on the first violation.
    Validation order: V1 (count) → then per-pattern V2-V7.
    """
    # V1: count check
    if len(patterns) > _MAX_CUSTOM_PATTERNS:
        raise PAYLOAD_CUSTOM_PATTERN_INVALID.exc(detail="too many custom patterns (max 8)")

    for item in patterns:
        name = item.name
        pattern_str = item.pattern

        # V2: name format
        if not _NAME_RE.match(name):
            raise PAYLOAD_CUSTOM_PATTERN_INVALID.exc(detail=f"invalid pattern name: {name}")

        # V3: pattern length
        if len(pattern_str.encode()) > _MAX_PATTERN_BYTES:
            raise PAYLOAD_CUSTOM_PATTERN_INVALID.exc(
                detail=f"pattern too long (max 256 bytes): {name}"
            )

        # V4: valid regex syntax
        try:
            compiled = re.compile(pattern_str)
        except re.error:
            raise PAYLOAD_CUSTOM_PATTERN_INVALID.exc(
                detail=f"invalid regex syntax: {name}"
            ) from None

        # V5: must not match empty string
        if compiled.search("") is not None:
            raise PAYLOAD_CUSTOM_PATTERN_INVALID.exc(detail=f"pattern matches empty string: {name}")

        # V6: no backreferences (numeric \1..\9 or named (?P=name) — both enable
        # super-linear backtracking that the V7 heuristic cannot see)
        if _BACKREFERENCE_RE.search(pattern_str) or _NAMED_BACKREFERENCE_TOKEN in pattern_str:
            raise PAYLOAD_CUSTOM_PATTERN_INVALID.exc(
                detail=f"pattern contains backreferences: {name}"
            )

        # V7: no nested quantifiers (ReDoS heuristic)
        if _NESTED_QUANTIFIER_RE.search(pattern_str):
            raise PAYLOAD_CUSTOM_PATTERN_INVALID.exc(
                detail=f"pattern contains nested quantifiers (ReDoS risk): {name}"
            )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _build_response(configs: dict[str, Any]) -> GuardrailConfigResponse:
    """Convert raw guardrail_configs dict to GuardrailConfigResponse."""
    pi = configs.get("prompt_injection")
    pm = configs.get("pii_mask")
    mm = configs.get("ml_moderation")
    return GuardrailConfigResponse(
        prompt_injection=pi if isinstance(pi, dict) else None,
        pii_mask=pm if isinstance(pm, dict) else None,
        ml_moderation=mm if isinstance(mm, dict) else None,
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

    pii-v2: pii_custom_patterns validation (V1-V7) applied atomically at PUT time.
    On any validation failure: 422 ERR_PAYLOAD_INVALID; guardrail_configs NOT updated.
    pii_custom_patterns absent in present pii_mask block → removes custom list.
    pii_mask absent entirely → preserves existing pii_mask config.
    """
    tenant_id = str(identity.tenant_id)

    # pii-v2: validate custom patterns BEFORE any DB read/write (atomic reject)
    if body.pii_mask is not None and body.pii_mask.pii_custom_patterns is not None:
        _validate_custom_patterns(body.pii_mask.pii_custom_patterns)

    # plan-enforcement (TASK.md §3, M6/R4): setting/CHANGING the ml_moderation key
    # specifically is refused with 403 ERR_PLAN_FEATURE_NOT_ENABLED when the tenant has an
    # assigned plan whose feature_flags lacks "ml_moderation" — checked BEFORE any write
    # (§5 Safety rule). Gated on body.model_fields_set below (inspects the SPECIFIC key
    # touched, never the presence of any key) — an unrelated guardrail edit is unaffected.
    # Only the forward/configuring direction is gated (mirrors put_batch_policy) — clearing
    # ml_moderation (explicit null) is never blocked. Inert for an unplanned tenant (M7).
    if "ml_moderation" in body.model_fields_set and body.ml_moderation is not None:
        await check_plan_feature(session, identity.tenant_id, "ml_moderation")

    # Fetch current config for merge
    current = await _fetch_guardrail_configs(session, tenant_id)

    # Partial merge: only update keys that were explicitly provided
    updated = dict(current)

    # Check which fields were explicitly set in the request
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
            # Build the pii_mask dict to store
            pii_dump = body.pii_mask.model_dump()
            # pii_custom_patterns: if present (even as empty list), replace entire list.
            # If absent in the pii_mask block (None from Pydantic default), remove key.
            # model_dump() returns None for absent optional field.
            if pii_dump.get("pii_custom_patterns") is None:
                # Absent pii_custom_patterns in the PUT pii_mask block → remove custom list
                pii_dump.pop("pii_custom_patterns", None)
            else:
                # Present list (even empty): serialize as list of {name, pattern} dicts.
                # Literals are NOT stored — they are derived server-side.
                pii_dump["pii_custom_patterns"] = [
                    {"name": p["name"], "pattern": p["pattern"]}
                    for p in pii_dump["pii_custom_patterns"]
                ]
            updated["pii_mask"] = pii_dump

    if "ml_moderation" in fields_set:
        if body.ml_moderation is None:
            updated.pop("ml_moderation", None)
        else:
            updated["ml_moderation"] = body.ml_moderation.model_dump()

    # Persist the merged config as JSONB
    await session.execute(
        text("UPDATE tenants SET guardrail_configs = :val::jsonb WHERE id = :tid"),
        {"val": json.dumps(updated), "tid": tenant_id},
    )
    await session.commit()

    return _build_response(updated)
