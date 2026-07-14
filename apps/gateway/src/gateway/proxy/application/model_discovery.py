"""Tenant model-discovery enumeration for the Claude gateway protocol's GET /v1/models.

New ground (claude-gateway-protocol-compat TASK.md §1 M1, flagged ⚠): no existing code
path enumerates "every model a tenant/key is currently permitted to use" — the existing
`CompletionUseCase._check_model_catalog` only checks ONE model at a time. This module is
the batched analog, reusing the SAME entitlement semantics
(`SqlAlchemyModelChecker.check_for_tenant`'s catalog-active + tenant-override rule) plus
the key-level/plan-level model_allowlist intersections `_check_model_allowlist` /
`_check_plan_model_allowlist` already apply per-request.

Pure, zero-I/O core (`resolve_claude_discovery_entries`) + one batched query
(`list_entitled_claude_models`) — mirrors `modality_guard.py::resolve_allowed`'s own
"resolve to the union of candidates" shape rather than reinventing one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.catalog.infrastructure.orm import ModelRow, TenantModelOverrideRow

#: Claude Code's own model-discovery filter (external protocol anchor,
#: `code.claude.com/docs/en/llm-gateway-protocol`): "ignores entries whose `id`
#: doesn't begin with `claude` or `anthropic`."
_CLAUDE_ID_PREFIXES = ("claude-", "anthropic-")


@dataclass(frozen=True, slots=True)
class DiscoveryEntry:
    """One GET /v1/models `data[]` row."""

    id: str
    display_name: str | None = None


def _is_claude_shaped(model_id: str) -> bool:
    return model_id.startswith(_CLAUDE_ID_PREFIXES)


async def _entitled_catalog_rows(
    session: AsyncSession, tenant_id: uuid.UUID
) -> list[tuple[str, str, bool]]:
    """Return (model_id, name, entitled) for every catalog row, tenant-override-aware.

    Single LEFT JOIN query — the batched analog of
    `SqlAlchemyModelChecker.check_for_tenant`'s per-model shape (§1 M1 ⚠: this is the
    genuinely new part; kept to ONE query rather than N catalog lookups so a tenant with
    a large catalog stays well inside Claude Code's 3-second client-side discovery
    timeout).

    entitled = catalog active=true AND (no tenant override row OR override.enabled=true).
    Key-level/plan-level model_allowlist intersection is applied by the caller (needs
    AuthzResult, not available at this query layer).
    """
    stmt = (
        select(ModelRow.id, ModelRow.name, ModelRow.active, TenantModelOverrideRow.enabled)
        .outerjoin(
            TenantModelOverrideRow,
            (TenantModelOverrideRow.model_id == ModelRow.id)
            & (TenantModelOverrideRow.tenant_id == tenant_id),
        )
        .order_by(ModelRow.id)
    )
    rows = (await session.execute(stmt)).all()
    out: list[tuple[str, str, bool]] = []
    for model_id, name, active, override_enabled in rows:
        tenant_enabled = override_enabled if override_enabled is not None else True
        entitled = bool(active) and tenant_enabled
        out.append((model_id, name, entitled))
    return out


def resolve_claude_discovery_entries(
    catalog_rows: list[tuple[str, str, bool]],
    *,
    model_groups: dict[str, list[str]],
    key_model_allowlist: list[str] | None,
    plan_model_allowlist: list[str] | None,
) -> list[DiscoveryEntry]:
    """Pure core: catalog rows + alias config -> the GET /v1/models `data[]` list.

    Contract (§3):
      - Scoped to rows the tenant/key is CURRENTLY entitled to (catalog-entitled AND,
        when set, present in BOTH the key-level and plan-level model_allowlist —
        composes by intersection, mirroring `_check_model_allowlist` /
        `_check_plan_model_allowlist`'s existing composition rule) AND that resolve to
        a claude-/anthropic--prefixed id (native id or a `model_groups` alias).
      - An entitled row with no such alias is OMITTED, never fabricated.
      - Two entitled rows sharing the same alias id collapse to ONE `data` entry (first
        candidate encountered, in model_groups iteration order, supplies display_name).
      - Never includes a duplicate id.
    """
    # Reverse index: candidate model_id -> every claude-/anthropic--prefixed alias key
    # that lists it as a candidate (bounded by config; TOO_MANY_CANDIDATES caps this).
    candidate_to_claude_aliases: dict[str, list[str]] = {}
    for alias, candidates in model_groups.items():
        if not _is_claude_shaped(alias):
            continue
        for candidate_id in candidates:
            candidate_to_claude_aliases.setdefault(candidate_id, []).append(alias)

    entries: dict[str, DiscoveryEntry] = {}
    for model_id, name, entitled in catalog_rows:
        if not entitled:
            continue
        if key_model_allowlist is not None and model_id not in key_model_allowlist:
            continue
        if plan_model_allowlist is not None and model_id not in plan_model_allowlist:
            continue

        if _is_claude_shaped(model_id):
            # Native claude-/anthropic--prefixed catalog id — include directly.
            entries.setdefault(model_id, DiscoveryEntry(id=model_id, display_name=name))

        for alias in candidate_to_claude_aliases.get(model_id, []):
            # M1 edge case: a second candidate aliased to the SAME claude id collapses
            # to the entry already recorded for that alias (never a duplicate).
            entries.setdefault(alias, DiscoveryEntry(id=alias, display_name=name))

    return [entries[k] for k in sorted(entries)]


async def list_entitled_claude_models(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    model_groups: dict[str, list[str]],
    key_model_allowlist: list[str] | None,
    plan_model_allowlist: list[str] | None,
) -> list[DiscoveryEntry]:
    """I/O + pure-core composition: the ONE query GET /v1/models needs."""
    catalog_rows = await _entitled_catalog_rows(session, tenant_id)
    return resolve_claude_discovery_entries(
        catalog_rows,
        model_groups=model_groups,
        key_model_allowlist=key_model_allowlist,
        plan_model_allowlist=plan_model_allowlist,
    )
