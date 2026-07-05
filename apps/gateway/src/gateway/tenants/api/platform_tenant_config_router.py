"""FastAPI router for cross-tenant config + budget view/edit (superadmin).

Contract FROZEN @ v1 (cross-tenant-config-budget TASK.md §3):

  GET/PUT  /admin/platform/tenants/{tenant_id}/cache        — cache_enabled / semantic_cache_enabled
  GET/PUT  /admin/platform/tenants/{tenant_id}/guardrails    — prompt_injection / pii_mask config
  GET/PUT  /admin/platform/tenants/{tenant_id}/budget        — budget_usd_monthly + spend-to-date

Security (server-side, authoritative; mirrors platform_tenants_router.py's dual-gate
precedent exactly — M7):
  - Every route is gated by `require_superadmin` (primary FastAPI dependency) AND calls
    `authorize_tenant_scope(identity, tenant_id)` (secondary, inline, first line of the
    handler body). Neither gate alone suffices here: `authorize_tenant_scope` alone would
    also admit a non-superadmin acting on THEIR OWN tenant_id through this route tree,
    bypassing the original routers' finer-grained permission split (e.g. a MEMBER reaching
    a cross-tenant-shaped budget PUT for their own tenant, when self-service `PUT
    /admin/budget` itself requires BUDGETS_MANAGE) — see TASK.md §0/§1.
  - Every PUT checks target-tenant existence (`get_tenant_by_id`) BEFORE writing — a PUT
    against a nonexistent tenant_id 404s with zero rows written, never a silent no-op 200
    (M8). Self-service routers skip this check because identity.tenant_id is guaranteed to
    already exist (the caller's own token was issued for it); that guarantee does not hold
    for an arbitrary path tenant_id here.

Reuse (TASK.md §1 Framings weighed — hybrid strategy, not pure duplication):
  - DTOs imported verbatim from cache_router.py / guardrail_router.py / budgets/api/schemas.py
    — zero redefinition, zero shape drift risk.
  - `get_tenant_by_id` serves as a SINGLE existence-check-plus-data-fetch for all 3 GETs and
    the pre-write check for all 3 PUTs — its ORM-loaded `guardrail_configs` is already a
    plain `dict` (unlike the raw-SQL `_fetch_guardrail_configs` helper's defensive
    str-or-dict handling), so GET/PUT guardrails read it directly without a second query.
  - `_validate_custom_patterns` / `_build_response` reused verbatim from guardrail_router.py
    — avoids duplicating ~150 lines of ReDoS-sensitive V1-V7 regex validation.
  - The 3 PUTs' raw `UPDATE` statements (and cache's re-read, budget's spend-to-date SUM) are
    duplicated verbatim from the self-service routers, parametrized by the PATH tenant_id
    instead of `identity.tenant_id` — no shared write-helper exists anywhere to reuse without
    touching those 3 frozen-contract files, which this task must not modify (M11).

markup_pct is excluded entirely from every response in this contract (M9) — no field, no
write route; see TASK.md §1 Framings weighed for the reasoning.

Audit: all 6 routes call emit_platform_audit() on their success path — added by
admin-console-audit TASK.md §3 (FROZEN @ v1). Closes the previously-flagged regression:
cross-tenant `PUT .../budget` now audits (`platform.budget.update`), matching self-service
`PUT /admin/budget`'s own `record_audit` call (under a different, platform-prefixed action
name by design — see that task's own §1 Framings weighed).
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.audit.application.platform_audit import emit_platform_audit
from gateway.budgets.api.schemas import BudgetGetResponse, BudgetPutRequest, BudgetPutResponse
from gateway.core.db import get_session
from gateway.core.error_catalog import (
    PAYLOAD_BUDGET_DECIMAL_INVALID,
    PAYLOAD_BUDGET_NEGATIVE,
    TENANT_NOT_FOUND,
)
from gateway.tenants.api.cache_router import CacheGetResponse, CachePutRequest, CachePutResponse

# Deliberate, disclosed reuse of guardrail_router.py's module-private helpers (TASK.md §0/§1):
# avoids duplicating ~150 lines of ReDoS-sensitive V1-V7 custom-pattern validation — matches
# the established house pattern for this exact situation (see e.g.
# proxy/application/embeddings_use_case.py's own targeted reportPrivateUsage suppressions).
from gateway.tenants.api.guardrail_router import (
    GuardrailConfigRequest,
    GuardrailConfigResponse,
    _build_response,  # pyright: ignore[reportPrivateUsage]
    _validate_custom_patterns,  # pyright: ignore[reportPrivateUsage]
)
from gateway.tenants.domain.authz import authorize_tenant_scope, require_superadmin
from gateway.tenants.domain.entities import Identity
from gateway.tenants.infrastructure.repository import get_tenant_by_id

platform_tenant_config_router = APIRouter(
    prefix="/admin/platform/tenants/{tenant_id}", tags=["platform-admin"]
)


# ---------------------------------------------------------------------------
# GET/PUT /admin/platform/tenants/{tenant_id}/cache
# ---------------------------------------------------------------------------


@platform_tenant_config_router.get("/cache", response_model=CacheGetResponse)
async def get_platform_tenant_cache(
    tenant_id: uuid.UUID,
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> CacheGetResponse:
    """GET .../cache — view any target tenant's cache toggle. SUPERADMIN only (M1)."""
    authorize_tenant_scope(identity, tenant_id)
    row = await get_tenant_by_id(session, tenant_id)
    if row is None:
        raise TENANT_NOT_FOUND.exc()
    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=tenant_id,
        action="platform.cache.view",
        target_type="cache",
        target_id="config",
        metadata={},
    )
    return CacheGetResponse(
        enabled=bool(row.cache_enabled),
        semantic_enabled=bool(row.semantic_cache_enabled),
    )


@platform_tenant_config_router.put("/cache", response_model=CachePutResponse)
async def put_platform_tenant_cache(
    tenant_id: uuid.UUID,
    body: CachePutRequest,
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> CachePutResponse:
    """PUT .../cache — update any target tenant's cache toggle. SUPERADMIN only (M4).

    Same partial-update semantics as self-service PUT /admin/cache (absent field =
    unchanged). 404s before any write if tenant_id does not exist (M8).
    """
    authorize_tenant_scope(identity, tenant_id)
    row = await get_tenant_by_id(session, tenant_id)
    if row is None:
        raise TENANT_NOT_FOUND.exc()

    set_parts = []
    params: dict[str, object] = {"tid": str(tenant_id)}
    if body.enabled is not None:
        set_parts.append("cache_enabled = :cache_enabled")
        params["cache_enabled"] = body.enabled
    if body.semantic_enabled is not None:
        set_parts.append("semantic_cache_enabled = :semantic_cache_enabled")
        params["semantic_cache_enabled"] = body.semantic_enabled

    if set_parts:
        # set_parts contains only hard-coded column=:param strings — no user input in SQL.
        sql = f"UPDATE tenants SET {', '.join(set_parts)} WHERE id = :tid"  # noqa: S608
        await session.execute(text(sql), params)
        await session.commit()

    # Re-read via a fresh raw SELECT (never the stale ORM `row` post-commit — async
    # SQLAlchemy would raise MissingGreenlet on an expired-attribute lazy reload).
    refreshed = (
        await session.execute(
            text("SELECT cache_enabled, semantic_cache_enabled FROM tenants WHERE id = :tid"),
            {"tid": str(tenant_id)},
        )
    ).fetchone()
    current_enabled = (
        bool(refreshed[0]) if refreshed is not None and refreshed[0] is not None else False
    )
    current_semantic = (
        bool(refreshed[1]) if refreshed is not None and refreshed[1] is not None else False
    )
    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=tenant_id,
        action="platform.cache.update",
        target_type="cache",
        target_id="config",
        metadata={"enabled": current_enabled, "semantic_enabled": current_semantic},
    )
    return CachePutResponse(enabled=current_enabled, semantic_enabled=current_semantic)


# ---------------------------------------------------------------------------
# GET/PUT /admin/platform/tenants/{tenant_id}/guardrails
# ---------------------------------------------------------------------------


@platform_tenant_config_router.get("/guardrails", response_model=GuardrailConfigResponse)
async def get_platform_tenant_guardrails(
    tenant_id: uuid.UUID,
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> GuardrailConfigResponse:
    """GET .../guardrails — view any target tenant's guardrail config. SUPERADMIN only (M2)."""
    authorize_tenant_scope(identity, tenant_id)
    row = await get_tenant_by_id(session, tenant_id)
    if row is None:
        raise TENANT_NOT_FOUND.exc()
    # row.guardrail_configs is ORM-deserialized to a plain dict (Mapped[dict[str, Any]],
    # NOT NULL) — no str-or-dict ambiguity here, unlike the raw-SQL _fetch_guardrail_configs
    # helper's own defensive handling (TASK.md §0 GROUND).
    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=tenant_id,
        action="platform.guardrails.view",
        target_type="guardrails",
        target_id="config",
        metadata={},
    )
    return _build_response(row.guardrail_configs)


@platform_tenant_config_router.put("/guardrails", response_model=GuardrailConfigResponse)
async def put_platform_tenant_guardrails(
    tenant_id: uuid.UUID,
    body: GuardrailConfigRequest,
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> GuardrailConfigResponse:
    """PUT .../guardrails — update any target tenant's guardrail config. SUPERADMIN only (M5).

    Same partial-merge semantics as self-service PUT /admin/guardrails, reusing
    _validate_custom_patterns/_build_response verbatim. V1-V7 custom-pattern validation
    runs BEFORE any read-modify-write (atomic reject, R5). 404s before any write if
    tenant_id does not exist (M8).
    """
    authorize_tenant_scope(identity, tenant_id)
    row = await get_tenant_by_id(session, tenant_id)
    if row is None:
        raise TENANT_NOT_FOUND.exc()

    # pii-v2: validate custom patterns BEFORE any write (atomic reject) — mirrors
    # self-service ordering exactly.
    if body.pii_mask is not None and body.pii_mask.pii_custom_patterns is not None:
        _validate_custom_patterns(body.pii_mask.pii_custom_patterns)

    # row.guardrail_configs is ORM-deserialized to a plain dict (Mapped[dict[str, Any]],
    # NOT NULL) — no str-or-dict ambiguity here, unlike the raw-SQL _fetch_guardrail_configs
    # helper's own defensive handling (TASK.md §0 GROUND).
    updated = dict(row.guardrail_configs)

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
            # pii_custom_patterns: if present (even as empty list), replace entire list.
            # If absent in the pii_mask block (None from Pydantic default), remove key.
            if pii_dump.get("pii_custom_patterns") is None:
                pii_dump.pop("pii_custom_patterns", None)
            else:
                pii_dump["pii_custom_patterns"] = [
                    {"name": p["name"], "pattern": p["pattern"]}
                    for p in pii_dump["pii_custom_patterns"]
                ]
            updated["pii_mask"] = pii_dump

    await session.execute(
        text("UPDATE tenants SET guardrail_configs = :val::jsonb WHERE id = :tid"),
        {"val": json.dumps(updated), "tid": str(tenant_id)},
    )
    await session.commit()

    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=tenant_id,
        action="platform.guardrails.update",
        target_type="guardrails",
        target_id="config",
        metadata={"fields_changed": sorted(fields_set)},
    )
    return _build_response(updated)


# ---------------------------------------------------------------------------
# GET/PUT /admin/platform/tenants/{tenant_id}/budget
# ---------------------------------------------------------------------------


@platform_tenant_config_router.get("/budget", response_model=BudgetGetResponse)
async def get_platform_tenant_budget(
    tenant_id: uuid.UUID,
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> BudgetGetResponse:
    """GET .../budget — view any target tenant's ceiling + spend-to-date. SUPERADMIN only (M3).

    spent_usd_month is sourced from usage_records (ledger), matching self-service parity —
    duplicated verbatim from budget's self-service GET, parametrized by the PATH tenant_id.
    """
    authorize_tenant_scope(identity, tenant_id)
    row = await get_tenant_by_id(session, tenant_id)
    if row is None:
        raise TENANT_NOT_FOUND.exc()

    budget_str: str | None = None
    if row.budget_usd_monthly is not None:
        budget_str = str(Decimal(str(row.budget_usd_monthly)))

    spent_row = (
        await session.execute(
            text(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM usage_records"
                " WHERE tenant_id = :tid"
                " AND date_trunc('month', created_at AT TIME ZONE 'UTC')"
                "   = date_trunc('month', now() AT TIME ZONE 'UTC')"
            ),
            {"tid": str(tenant_id)},
        )
    ).fetchone()
    spent_usd = (
        Decimal(str(spent_row[0])) if spent_row and spent_row[0] is not None else Decimal("0")
    )
    spent_str = f"{spent_usd:.2f}"

    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=tenant_id,
        action="platform.budget.view",
        target_type="budget",
        target_id="monthly",
        metadata={},
    )
    return BudgetGetResponse(budget_usd_monthly=budget_str, spent_usd_month=spent_str)


@platform_tenant_config_router.put("/budget", response_model=BudgetPutResponse)
async def put_platform_tenant_budget(
    tenant_id: uuid.UUID,
    body: BudgetPutRequest,
    identity: Annotated[Identity, Depends(require_superadmin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    request: Request,
) -> BudgetPutResponse:
    """PUT .../budget — set or clear any target tenant's monthly ceiling. SUPERADMIN only (M6).

    Same decimal/non-negative validation as self-service PUT /admin/budget. 404s before any
    write if tenant_id does not exist (M8). Now calls emit_platform_audit() — closes the
    previously-flagged regression versus self-service `PUT /admin/budget` (admin-console-audit
    TASK.md §3, FROZEN @ v1).
    """
    authorize_tenant_scope(identity, tenant_id)
    row = await get_tenant_by_id(session, tenant_id)
    if row is None:
        raise TENANT_NOT_FOUND.exc()

    new_value = body.budget_usd_monthly
    persisted_str: str | None = None

    if new_value is not None:
        try:
            parsed = Decimal(new_value)
        except InvalidOperation:
            raise PAYLOAD_BUDGET_DECIMAL_INVALID.exc() from None

        if parsed < Decimal("0"):
            raise PAYLOAD_BUDGET_NEGATIVE.exc()

        persisted_str = str(parsed)
        await session.execute(
            text("UPDATE tenants SET budget_usd_monthly = :b WHERE id = :tid"),
            {"b": persisted_str, "tid": str(tenant_id)},
        )
    else:
        await session.execute(
            text("UPDATE tenants SET budget_usd_monthly = NULL WHERE id = :tid"),
            {"tid": str(tenant_id)},
        )

    await session.commit()

    await emit_platform_audit(
        request.app.state.sessionmaker,
        identity=identity,
        target_tenant_id=tenant_id,
        action="platform.budget.update",
        target_type="budget",
        target_id="monthly",
        metadata={"budget_usd_monthly": persisted_str},
    )
    return BudgetPutResponse(budget_usd_monthly=persisted_str)
