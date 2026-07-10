"""Admin API router for per-key guardrail policy overrides.

Contract FROZEN @ v1 (per-key-guardrail-policies TASK.md §3):
  GET    /admin/keys/{key_id}/guardrails  — any authenticated tenant role; returns the
                                             key's CURRENT EFFECTIVE config + source
  PUT    /admin/keys/{key_id}/guardrails  — owner or admin only; member -> 403
                                             body: partial update WITHIN the key's own
                                             override (absent keys preserved, present
                                             keys replaced, null removes)
  DELETE /admin/keys/{key_id}/guardrails  — owner or admin only; clears the override
                                             (revert to tenant inheritance); idempotent 204

Reuses GuardrailConfigRequest / PromptInjectionConfig / PiiMaskConfig / CustomPatternItem
and the V1-V7 custom-pattern validation VERBATIM from tenants/api/guardrail_router.py
(imported, not duplicated) — identical shape and rules to the tenant-level PUT.

Resolution (key > tenant > default-off, wholesale, no field merge) happens ONCE at
key-authentication time inside SqlAlchemyApiKeyRepository.get_by_id() — this router
does its OWN independent read (mirrors the tenant-level guardrail_router.py's own
inline-session-query shape) purely to serve the CRUD surface; it never touches the
hot auth path.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.db import get_session
from gateway.core.error_catalog import KEY_NOT_FOUND
from gateway.keys.api.deps import get_identity, require_owner_or_admin
from gateway.tenants.api.guardrail_router import (
    GuardrailConfigRequest,
    _validate_custom_patterns,  # pyright: ignore[reportPrivateUsage]  — §3 CONTRACT: reuse V1-V7 validation verbatim, never duplicate
)
from gateway.tenants.api.guardrail_router import (
    _fetch_guardrail_configs as _fetch_tenant_guardrail_configs,  # pyright: ignore[reportPrivateUsage]  — §3 CONTRACT: reuse the tenant-level fetch helper verbatim
)
from gateway.tenants.domain.entities import Identity

key_guardrail_router = APIRouter(prefix="/admin/keys/{key_id}/guardrails", tags=["api-keys"])


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class KeyGuardrailPolicyResponse(BaseModel):
    """GET/PUT /admin/keys/{key_id}/guardrails response body."""

    prompt_injection: dict[str, Any] | None = None
    pii_mask: dict[str, Any] | None = None
    source: Literal["key", "tenant"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_dict_or_none(raw: Any) -> dict[str, Any] | None:
    """Parse a raw JSONB value fetched via a `text()` query into dict | None.

    Preserves the NULL-vs-{} distinction: NULL stays None (inherit tenant); an
    explicit {} stays a (falsy but non-None) dict (wholesale-empty override).
    asyncpg may hand back a dict or a JSON string depending on driver version —
    same defensive parse as tenants/api/guardrail_router.py's own helper.
    """
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except Exception:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _build_response(
    configs: dict[str, Any], source: Literal["key", "tenant"]
) -> KeyGuardrailPolicyResponse:
    pi = configs.get("prompt_injection")
    pm = configs.get("pii_mask")
    return KeyGuardrailPolicyResponse(
        prompt_injection=pi if isinstance(pi, dict) else None,
        pii_mask=pm if isinstance(pm, dict) else None,
        source=source,
    )


async def _fetch_key_policy_row(
    session: AsyncSession, *, key_id: uuid.UUID, tenant_id: str
) -> dict[str, Any] | None | Literal["__not_found__"]:
    """Fetch the key's raw guardrail_policy for an ACTIVE, tenant-owned key.

    Returns the parsed dict|None override on success, or the sentinel
    "__not_found__" when the key is unknown, cross-tenant, or revoked (all three
    collapse to the same 404 — no existence leak, mirrors patch_key's precedent).
    """
    row = (
        await session.execute(
            text(
                "SELECT guardrail_policy FROM api_keys"
                " WHERE id = :kid AND tenant_id = :tid AND revoked_at IS NULL"
            ),
            {"kid": str(key_id), "tid": tenant_id},
        )
    ).fetchone()
    if row is None:
        return "__not_found__"
    return _coerce_dict_or_none(row[0])


def _emit_audit(
    request: Request,
    *,
    identity: Identity,
    key_id: uuid.UUID,
    action: str,
    metadata: dict[str, Any],
) -> None:
    """Fire-and-forget, fail-open audit emit — mirrors keys/api/router.py precedent.

    metadata carries ONLY enabled/mode flags + key_id — NEVER pattern regex text or
    message content (M9).
    """
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=identity.tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action=action,
                target_type="api_key",
                target_id=str(key_id),
                result="success",
                metadata=metadata,
                created_at=datetime.now(UTC),
            ),
        )
    )


def _guardrail_flags_metadata(configs: dict[str, Any], key_id: uuid.UUID) -> dict[str, Any]:
    """Build audit metadata: key_id + which guardrails are enabled+mode — never pattern text."""
    meta: dict[str, Any] = {"key_id": str(key_id)}
    for guardrail_name in ("prompt_injection", "pii_mask"):
        cfg = configs.get(guardrail_name)
        if isinstance(cfg, dict):
            meta[guardrail_name] = {
                "enabled": cfg.get("enabled"),
                "mode": cfg.get("mode"),
            }
    return meta


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@key_guardrail_router.get("", response_model=KeyGuardrailPolicyResponse)
async def get_key_guardrails(
    key_id: uuid.UUID,
    identity: Annotated[Identity, Depends(get_identity)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KeyGuardrailPolicyResponse:
    """GET /admin/keys/{key_id}/guardrails — current effective config + source.

    Accessible to any authenticated role (owner, admin, member) — mirrors the
    tenant-level GET /admin/guardrails precedent (member can view, not edit).
    """
    tenant_id = str(identity.tenant_id)
    key_policy = await _fetch_key_policy_row(session, key_id=key_id, tenant_id=tenant_id)
    if key_policy == "__not_found__":
        raise KEY_NOT_FOUND.exc()

    if key_policy is not None:
        return _build_response(key_policy, "key")

    tenant_configs = await _fetch_tenant_guardrail_configs(session, tenant_id)
    return _build_response(tenant_configs, "tenant")


@key_guardrail_router.put("", response_model=KeyGuardrailPolicyResponse)
async def put_key_guardrails(
    key_id: uuid.UUID,
    body: GuardrailConfigRequest,
    request: Request,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KeyGuardrailPolicyResponse:
    """PUT /admin/keys/{key_id}/guardrails — set/replace the key's override.

    Requires owner or admin (KEYS_MANAGE); member -> 403 ERR_AUTH_FORBIDDEN.
    Partial-merge WITHIN the key's own override (mirrors the tenant PUT): absent
    top-level key preserves that guardrail's existing key-level value; present
    key replaces; explicit null removes that one guardrail from the override.
    The tenant's config is NEVER read/merged here — this endpoint only ever
    manipulates the key's own override column.
    """
    tenant_id = str(identity.tenant_id)

    # pii-v2 validation BEFORE any DB read/write (atomic reject) — R1.
    if body.pii_mask is not None and body.pii_mask.pii_custom_patterns is not None:
        _validate_custom_patterns(body.pii_mask.pii_custom_patterns)

    current = await _fetch_key_policy_row(session, key_id=key_id, tenant_id=tenant_id)
    if current == "__not_found__":
        raise KEY_NOT_FOUND.exc()

    updated = dict(current) if current is not None else {}

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
            pii_dump = body.pii_mask.model_dump()
            if pii_dump.get("pii_custom_patterns") is None:
                pii_dump.pop("pii_custom_patterns", None)
            else:
                pii_dump["pii_custom_patterns"] = [
                    {"name": p["name"], "pattern": p["pattern"]}
                    for p in pii_dump["pii_custom_patterns"]
                ]
            updated["pii_mask"] = pii_dump

    result = await session.execute(
        text(
            "UPDATE api_keys SET guardrail_policy = :val::jsonb"
            " WHERE id = :kid AND tenant_id = :tid AND revoked_at IS NULL"
            " RETURNING id"
        ),
        {"val": json.dumps(updated), "kid": str(key_id), "tid": tenant_id},
    )
    if result.fetchone() is None:
        # Race: key revoked/deleted between the fetch above and this UPDATE.
        # Nothing was written (WHERE matched zero rows) — 404, no leak.
        raise KEY_NOT_FOUND.exc()
    await session.commit()

    _emit_audit(
        request,
        identity=identity,
        key_id=key_id,
        action="key_guardrail_policy.put",
        metadata=_guardrail_flags_metadata(updated, key_id),
    )

    return _build_response(updated, "key")


@key_guardrail_router.delete("", status_code=204)
async def delete_key_guardrails(
    key_id: uuid.UUID,
    request: Request,
    identity: Annotated[Identity, Depends(require_owner_or_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """DELETE /admin/keys/{key_id}/guardrails — clear the override, revert to inherit.

    Requires owner or admin; member -> 403. Idempotent: 204 whether or not an
    override existed (the UPDATE matches on tenant_id + revoked_at IS NULL alone,
    never on the guardrail_policy's current value).
    """
    tenant_id = str(identity.tenant_id)

    result = await session.execute(
        text(
            "UPDATE api_keys SET guardrail_policy = NULL"
            " WHERE id = :kid AND tenant_id = :tid AND revoked_at IS NULL"
            " RETURNING id"
        ),
        {"kid": str(key_id), "tid": tenant_id},
    )
    if result.fetchone() is None:
        raise KEY_NOT_FOUND.exc()
    await session.commit()

    _emit_audit(
        request,
        identity=identity,
        key_id=key_id,
        action="key_guardrail_policy.delete",
        metadata={"key_id": str(key_id)},
    )

    return Response(status_code=204)
