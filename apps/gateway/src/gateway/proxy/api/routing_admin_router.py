"""GET + PUT /admin/routing — routing config read + (owner/admin) write surface.

Contract: routing-admin TASK.md §3 (GET, FROZEN) + routing-config-write TASK.md §3 (PUT + the
additive GET extensions: `routing_strategy`, `deployments`).

GET returns retry policy, cooldown config, model groups, per-candidate circuit state, the routing
strategy, and a per-deployment `deployments` block (weight/rpm/tpm). PUT validates an incoming
routing config through the SAME Settings/Deployment validators (via merge_routing_config) and, on
success, persists it to the operator-wide singleton. Both render the EFFECTIVE-ON-NEXT-BOOT config
= merge_routing_config(app.state.settings, stored_row): no row → settings unchanged (the four
frozen blocks are byte-identical); a row → the persisted config (read-after-write for the editor).
candidates[].state always comes from the LIVE cooldown_gate (runtime health, not config).

Auth (both): require_superadmin (Bearer JWT; any non-SUPERADMIN role → 403; missing/invalid → 401).
The routing config is operator-wide (no tenant_id) — gated by role, never a tenant Permission.
This SUPERSEDES the 2026-06-23 "any owner/admin, always-on" decision (signup-and-routing-authz S1,
M7/M9): the single-operator/trusted-owner premise no longer holds now that tenants/plans/billing
all ship on main. `Permission.ROUTING_MANAGE` is left declared but unused (see that task's §7).

Restart-to-apply: PUT NEVER mutates app.state.settings/model_router. The persisted config is
adopted by the live router only at the next gateway boot (the shipped boot-merge).
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Body, Depends, Request

from gateway.audit.application.audit_writer import record_audit
from gateway.audit.domain.audit_event import AuditEvent
from gateway.core.config import Settings
from gateway.core.error_catalog import ROUTING_CONFIG_INVALID
from gateway.proxy.application.routing_config_merge import (
    ROUTING_OVERRIDE_KEYS,
    merge_routing_config,
)
from gateway.proxy.infrastructure.redis_cooldown_gate import RedisCooldownGate
from gateway.proxy.infrastructure.routing_config_repository import RoutingConfigRepository
from gateway.tenants.domain.authz import require_superadmin
from gateway.tenants.domain.entities import Identity

routing_admin_router = APIRouter(prefix="/admin/routing", tags=["routing-admin"])

# The set of keys a stored routing-config document may legitimately hold (model_groups + the
# scalar override knobs). The PUT persists ONLY these — a client cannot smuggle arbitrary keys
# (e.g. a stray secret) into the JSONB row; they would be ignored at boot anyway (extra="ignore").
_PERSISTABLE_KEYS: frozenset[str] = frozenset(("model_groups", *ROUTING_OVERRIDE_KEYS))

# Known Settings/Deployment validator codes (UPPER_SNAKE tokens emitted ahead of ": <message>").
# Matching against this set avoids echoing a user-controlled alias from the error field-path.
_VALIDATOR_CODES: frozenset[str] = frozenset(
    {
        "UNKNOWN_ROUTING_STRATEGY",
        "DUPLICATE_DEPLOYMENT",
        "EMPTY_CANDIDATE_LIST",
        "TOO_MANY_CANDIDATES",
        "ALIAS_COLLIDES_WITH_CANDIDATE",
        "INVALID_DEPLOYMENT_WEIGHT",
        "INVALID_DEPLOYMENT_LIMIT",
        "DEPLOYMENT_MODEL_ID_REQUIRED",
        "INVALID_LOADBAL_ALPHA",
        "INVALID_LOADBAL_TTL",
    }
)
_UPPER_TOKEN_RE = re.compile(r"[A-Z][A-Z0-9_]{4,}")


def _validator_code(exc: Exception) -> str:
    """Return the first KNOWN Settings/Deployment validator code in the error, else a generic code.

    Scans all UPPER_SNAKE tokens and returns the first that is a recognised validator code — so a
    user-controlled alias appearing earlier in Pydantic's field-path (e.g. model_groups.MYALIAS.0)
    is never echoed back as the code.
    """
    for token in _UPPER_TOKEN_RE.findall(str(exc)):
        if token in _VALIDATOR_CODES:
            return token
    return "INVALID_ROUTING_CONFIG"


async def _effective_settings(request: Request) -> Settings:
    """Return settings merged with the persisted routing config (DB-wins), env on any read error.

    Design-for-failure: a missing table / unreachable DB / invalid stored row must NOT break the
    read surface — fall back to the env settings (the four frozen blocks stay byte-identical).
    """
    settings: Settings = request.app.state.settings
    try:
        stored = await RoutingConfigRepository(request.app.state.sessionmaker).get()
        return merge_routing_config(settings, stored)
    except Exception:
        structlog.get_logger(__name__).warning(
            "routing_config_read_failed_fallback_env", exc_info=True
        )
        return settings


async def _routing_response(request: Request, effective: Settings) -> dict[str, Any]:
    """Build the extended /admin/routing body from the effective settings + live cooldown gate."""
    retry_policy = {
        "max_retries": effective.upstream_max_retries,
        "backoff_base_s": effective.upstream_retry_backoff_base_s,
    }

    cooldown_threshold: int = effective.cooldown_failure_threshold
    cooldown = {
        "enabled": cooldown_threshold > 0,
        "threshold": cooldown_threshold,
        "ttl_s": effective.cooldown_ttl_s,
        "window_s": effective.cooldown_window_s,
    }

    model_groups: dict[str, list[str]] = effective.model_groups

    # deployments block (additive): per-alias normalized Deployment detail.
    deployments: dict[str, list[dict[str, Any]]] = {
        alias: [
            {
                "model_id": d.model_id,
                "weight": d.weight,
                "rpm_limit": d.rpm_limit,
                "tpm_limit": d.tpm_limit,
            }
            for d in deps
        ]
        for alias, deps in effective.deployments.items()
    }

    # candidates: model_ids from the effective config; state from the LIVE cooldown gate.
    gate: RedisCooldownGate | None = request.app.state.cooldown_gate
    candidates: list[dict[str, str]] = []
    for alias, candidate_list in model_groups.items():
        for model_id in candidate_list:
            if gate is None:
                state = "closed"
            else:
                try:
                    state = await gate.snapshot_state(model_id)
                except Exception as exc:
                    structlog.get_logger().warning(
                        "cooldown_gate_redis_error",
                        model_id=model_id,
                        error=type(exc).__name__,
                    )
                    state = "unknown"
            candidates.append({"model_id": model_id, "alias": alias, "state": state})

    return {
        "retry_policy": retry_policy,
        "cooldown": cooldown,
        "model_groups": model_groups,
        "candidates": candidates,
        "routing_strategy": effective.routing_strategy,
        "deployments": deployments,
    }


@routing_admin_router.get("")
async def get_routing_admin(
    request: Request,
    _identity: Annotated[Identity, Depends(require_superadmin)],
) -> dict[str, Any]:
    """GET /admin/routing — effective routing config + per-candidate cooldown health."""
    effective = await _effective_settings(request)
    return await _routing_response(request, effective)


@routing_admin_router.put("")
async def put_routing_admin(
    request: Request,
    body: Annotated[dict[str, Any], Body(...)],
    identity: Annotated[Identity, Depends(require_superadmin)],
) -> dict[str, Any]:
    """PUT /admin/routing — validate (Settings/Deployment parity) then persist; restart-to-apply.

    Validation is performed by round-tripping the body through merge_routing_config against the
    live settings — the EXACT validators that run at boot. On failure, NOTHING is persisted (422
    with the underlying validator code in `detail`). On success, the singleton row is replaced and
    the effective config is returned; the live router is untouched (adopted on next restart).
    """
    settings: Settings = request.app.state.settings
    try:
        effective = merge_routing_config(settings, body)
    except ValueError as exc:
        raise ROUTING_CONFIG_INVALID.exc(detail=_validator_code(exc)) from exc

    # Persist ONLY recognised routing keys — never store arbitrary client-supplied keys in the row.
    persistable = {k: body[k] for k in _PERSISTABLE_KEYS if k in body}
    await RoutingConfigRepository(request.app.state.sessionmaker).upsert(persistable)

    # Audit emit — fail-open fire-and-forget (NEVER in the action's own transaction)
    asyncio.ensure_future(  # noqa: RUF006
        record_audit(
            request.app.state.sessionmaker,
            AuditEvent(
                id=uuid.uuid4(),
                tenant_id=identity.tenant_id,
                actor_user_id=identity.user_id,
                actor_email=identity.email,
                action="routing.update",
                target_type="routing",
                target_id="singleton",
                result="success",
                metadata={"keys_updated": sorted(persistable.keys())},
                created_at=datetime.now(UTC),
            ),
        )
    )

    return await _routing_response(request, effective)
