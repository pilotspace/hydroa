"""GET /admin/routing — read-only routing health+config surface.

Contract: routing-admin TASK.md §3 (FROZEN).

Returns retry policy, cooldown config, model groups, and per-candidate
circuit state in a single JSON response. Secrets-free: only the four named
blocks are present in the response body.

Auth: same require_owner_or_admin dependency as gateway/keys/api/router.py
(Bearer JWT; member role → 403; missing/invalid → 401).

State derivation:
  - app.state.cooldown_gate is None → all candidates "closed", zero Redis I/O.
  - Else: call snapshot_state(model_id) on the concrete RedisCooldownGate.
    snapshot_state is an additive method (NOT part of ModelHealthGate protocol).
    Any Redis exception in snapshot_state → state "unknown"; 200 still returned.

NOTE: snapshot_state is defined on RedisCooldownGate only (concrete class).
In v6 exactly one non-None gate type exists. If a second implementation is
introduced, add isinstance dispatch here or extend the gate interface.
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Request

from gateway.keys.api.deps import require_owner_or_admin
from gateway.proxy.infrastructure.redis_cooldown_gate import RedisCooldownGate
from gateway.tenants.domain.entities import Identity

routing_admin_router = APIRouter(prefix="/admin/routing", tags=["routing-admin"])


@routing_admin_router.get("")
async def get_routing_admin(
    request: Request,
    _identity: Annotated[Identity, Depends(require_owner_or_admin)],
) -> dict[str, Any]:
    """GET /admin/routing — routing config and per-candidate cooldown health.

    Returns 200 with the exact shape frozen in §3:
      {retry_policy, cooldown, model_groups, candidates}

    All four top-level keys are always present regardless of configuration.
    Response is secrets-free: only the named Settings knobs appear.
    """
    settings = request.app.state.settings

    # ── retry_policy block ──────────────────────────────────────────────────
    retry_policy = {
        "max_retries": settings.upstream_max_retries,
        "backoff_base_s": settings.upstream_retry_backoff_base_s,
    }

    # ── cooldown block ──────────────────────────────────────────────────────
    cooldown_threshold: int = settings.cooldown_failure_threshold
    cooldown = {
        "enabled": cooldown_threshold > 0,
        "threshold": cooldown_threshold,
        "ttl_s": settings.cooldown_ttl_s,
        "window_s": settings.cooldown_window_s,
    }

    # ── model_groups block ──────────────────────────────────────────────────
    # Read from app.state.model_router (wired at create_app from Settings.model_groups).
    model_router = request.app.state.model_router
    model_groups: dict[str, list[str]] = model_router.model_groups

    # ── candidates block ────────────────────────────────────────────────────
    # Narrow cast: exactly one non-None gate type in v6 is RedisCooldownGate.
    # When gate is None (cooldown disabled), all candidates are "closed" with
    # zero Redis I/O.  When gate is not None, call snapshot_state per candidate.
    # app.state is untyped (Any); narrow to RedisCooldownGate | None for the call below.
    gate: RedisCooldownGate | None = request.app.state.cooldown_gate

    candidates: list[dict[str, str]] = []
    for alias, candidate_list in model_groups.items():
        for model_id in candidate_list:
            if gate is None:
                state = "closed"
            else:
                # Direct call on the concrete gate type — snapshot_state is an
                # additive method on RedisCooldownGate, not on ModelHealthGate.
                # Defensive try/except: RedisCooldownGate.snapshot_state already
                # catches all Redis exceptions internally, but test fakes may raise
                # directly. Fail-open: any exception → "unknown", 200 still returned.
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
    }
