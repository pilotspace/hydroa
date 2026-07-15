"""RED suite: M3/M4/R1/R2 — tenant-layer plan rpm/tpm enforcement over real HTTP,
real Postgres, real Redis (plan-rate-enforcement TASK.md §3, FROZEN @ v1).

Mirrors tests/rate_limits/test_rate_limits.py's own real-infra HTTP round-trip style
exactly (canonical routes only: /admin/auth/signup, /admin/auth/login, /admin/keys,
/v1/chat/completions).

RED targets (right reason):
  - `wire_plan_rate_limit_resolver` ImportErrors until
    gateway.rate_limits.infrastructure.plan_rate_limit_resolver exists (M2).
  - Even once that module exists, a 3rd request across two keys on a tenant rpm=2 plan
    gets 200 instead of 429 until CompletionUseCase._enforce_rate_limits actually checks
    the tenant window (M3) AND proxy/api/deps.py wires plan_rate_limit_resolver through
    to CompletionUseCase (the DI seam this task's build also had to add).
  - The tenant TPM sum key never accumulates from a real completion's usage until
    _fire_record_tpm_tenant is wired at the post-response call site (M4).
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import (
    COMPLETIONS,
    FakeCompletionUpstream,
    assert_problem,
    assign_plan,
    auth_key,
    create_key,
    seed_plan,
    set_tenant_rate_limits,
    signup_and_login,
    wire_budget_guard,
    wire_plan_rate_limit_resolver,
    wire_rate_limiter,
)


def _tenant_tpm_sum_key(tenant_id: str) -> str:
    return f"ratelimit:tpm_sum:{tenant_id}"


async def _poll_until(condition: Any, *, timeout_s: float = 2.0, interval_s: float = 0.05) -> bool:
    """Poll `condition()` (an async callable returning bool) until True or timeout.

    Fire-and-forget accounting (_fire_record_tpm_tenant) is scheduled via
    asyncio.ensure_future, not awaited before the HTTP response returns — a fixed sleep()
    is flake-prone under load, so poll instead (mirrors the project's own
    fire-and-forget-audit-test-flake lesson: poll-until-present, not sleep-then-assert).
    """
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if await condition():
            return True
        await asyncio.sleep(interval_s)
    return await condition()


async def test_tenant_rpm_throttles_across_keys(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    active_model: str,
    redis_client: Any,
) -> None:
    """§2 Scenario 'tenant RPM ceiling throttles across keys' — a tenant on a plan with
    rpm_limit_default=2 and two keys, NEITHER with its own per-key rpm_limit: the 3rd
    request within the window, across those two keys, is rejected 429 ERR_RATE_LIMITED
    with Retry-After. A DIFFERENT tenant (own plan, own key) is unaffected. Covers: M3, R1.
    """
    jwt, tenant_id = await signup_and_login(client, tenant_name="TenantRpmCo")
    plan_id = await seed_plan(db_session, name="tenant-rpm-plan", rpm_limit_default=2)
    await assign_plan(db_session, tenant_id=tenant_id, plan_id=plan_id)

    key_a = await create_key(client, jwt=jwt, name="key-a")
    key_b = await create_key(client, jwt=jwt, name="key-b")

    # Sibling tenant — own plan, own key, must stay wholly unaffected.
    jwt_sib, tenant_sib = await signup_and_login(client, tenant_name="TenantRpmSiblingCo")
    plan_sib = await seed_plan(db_session, name="tenant-rpm-sibling-plan", rpm_limit_default=2)
    await assign_plan(db_session, tenant_id=tenant_sib, plan_id=plan_sib)
    key_sib = await create_key(client, jwt=jwt_sib, name="key-sib")

    wire_rate_limiter(app, redis_client)
    wire_budget_guard(app, redis_client)
    wire_plan_rate_limit_resolver(app)

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    payload = {"model": active_model, "messages": [{"role": "user", "content": "hi"}]}

    # Request 1 on key_a — admitted (tenant window: 1/2).
    r1 = await client.post(COMPLETIONS, json=payload, headers=auth_key(key_a["key"]))
    assert r1.status_code == 200, f"request 1 (key_a) expected 200: {r1.text}"

    # Request 2 on key_b — admitted (tenant window: 2/2, SAME tenant, DIFFERENT key).
    r2 = await client.post(COMPLETIONS, json=payload, headers=auth_key(key_b["key"]))
    assert r2.status_code == 200, f"request 2 (key_b) expected 200: {r2.text}"

    # Request 3 on key_a again — tenant window full (2/2) -> 429, even though key_a's OWN
    # per-key window only saw 1 prior request.
    r3 = await client.post(COMPLETIONS, json=payload, headers=auth_key(key_a["key"]))
    body = assert_problem(r3, 429, "ERR_RATE_LIMITED")
    retry_after = r3.headers.get("Retry-After")
    assert retry_after is not None, "Retry-After header missing on tenant RPM 429"
    assert int(retry_after) >= 1

    assert upstream.calls == 2, f"expected exactly 2 admitted calls, got {upstream.calls}"

    # detail should reference the tenant/RPM, not leak an unrelated identifier.
    detail = body.get("detail", "")
    assert "RPM" in detail or "rpm" in detail or tenant_id in detail

    # Sibling tenant's OWN window (2/2 allowance, fresh) is completely unaffected.
    r_sib = await client.post(COMPLETIONS, json=payload, headers=auth_key(key_sib["key"]))
    assert r_sib.status_code == 200, (
        f"sibling tenant must be unaffected by tenant A's rate limit, got "
        f"{r_sib.status_code}: {r_sib.text}"
    )


async def test_tenant_tpm_throttles_preflight_and_accumulates(
    client: httpx.AsyncClient,
    app: Any,
    db_session: AsyncSession,
    active_model: str,
    redis_client: Any,
) -> None:
    """§2 Scenario 'tenant TPM ceiling throttles pre-flight' — a tenant on a plan with
    tpm_limit_default set: (a) a successful completion's REAL usage accumulates into the
    tenant TPM window (M4), not only the per-key window; (b) once the tenant window is at
    (or past) the ceiling, the next request is rejected 429 ERR_RATE_LIMITED pre-flight
    (upstream never called). Covers: M3, M4, R2.
    """
    jwt, tenant_id = await signup_and_login(client, tenant_name="TenantTpmCo")
    plan_id = await seed_plan(db_session, name="tenant-tpm-plan", tpm_limit_default=100)
    await assign_plan(db_session, tenant_id=tenant_id, plan_id=plan_id)
    key = await create_key(client, jwt=jwt, name="tpm-key")  # no per-key tpm_limit

    wire_rate_limiter(app, redis_client)
    wire_budget_guard(app, redis_client)
    wire_plan_rate_limit_resolver(app)

    upstream = FakeCompletionUpstream(
        body={
            "id": "gen-plan-rate-tpm",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }
    )
    app.state.completion_upstream = upstream

    payload = {"model": active_model, "messages": [{"role": "user", "content": "hi"}]}

    # (a) M4 — a successful completion (well under tpm_limit_default=100) must ALSO
    # record its real usage (8 tokens) into the TENANT tpm sum window, not only the
    # (nonexistent, since this key has no per-key tpm_limit) key window.
    r1 = await client.post(COMPLETIONS, json=payload, headers=auth_key(key["key"]))
    assert r1.status_code == 200, f"request 1 expected 200: {r1.text}"

    sum_key = _tenant_tpm_sum_key(tenant_id)

    async def _tenant_sum_recorded() -> bool:
        raw = await redis_client.get(sum_key)
        return raw is not None and float(raw) >= 8.0

    recorded = await _poll_until(_tenant_sum_recorded)
    assert recorded, (
        "tenant TPM sum key was never written by the post-response accounting "
        f"(M4) — checked {sum_key!r}"
    )

    # (b) R2 — push the tenant TPM sum to (>=) the ceiling directly, then confirm the
    # NEXT request is rejected pre-flight (mirrors tests/rate_limits/test_rate_limits.py's
    # own TPM pre-seed idiom).
    await redis_client.set(sum_key, b"100")
    now_ms = int(datetime.datetime.now(datetime.UTC).timestamp() * 1000)
    zset_key = f"ratelimit:tpm:{tenant_id}"
    await redis_client.zadd(zset_key, {f"100:{tenant_id[:8]}": now_ms - 30000})
    await redis_client.expire(zset_key, 61)

    r2 = await client.post(COMPLETIONS, json=payload, headers=auth_key(key["key"]))
    assert_problem(r2, 429, "ERR_RATE_LIMITED")
    retry_after = r2.headers.get("Retry-After")
    assert retry_after is not None, "Retry-After header missing on tenant TPM 429"
    assert int(retry_after) >= 1

    assert upstream.calls == 1, (
        f"upstream must NOT be called on the tenant TPM pre-flight reject; "
        f"got {upstream.calls} total calls"
    )


async def test_per_key_ceiling_still_fires_for_unplanned_tenant(
    client: httpx.AsyncClient,
    app: Any,
    active_model: str,
    redis_client: Any,
) -> None:
    """§2 Scenario 'per-key ceiling still fires independently' — a tenant with NO plan
    (plan_id NULL) but a key with per-key rpm_limit=1: the 2nd request is still rejected
    429 by the PER-KEY window, byte-identical to pre-task behavior — proves tenant-layer
    enforcement composes rather than replacing/breaking the existing per-key path even
    when wired (an unplanned tenant's resolver call resolves (None, None) and is a no-op).
    Covers: M3 (compose-not-replace).
    """
    jwt, _tenant_id = await signup_and_login(client, tenant_name="PerKeyStillFiresCo")
    key = await create_key(client, jwt=jwt, name="per-key-limited", rpm_limit=1)

    wire_rate_limiter(app, redis_client)
    wire_budget_guard(app, redis_client)
    wire_plan_rate_limit_resolver(app)  # wired, but this tenant has NO plan -> inert

    upstream = FakeCompletionUpstream()
    app.state.completion_upstream = upstream

    payload = {"model": active_model, "messages": [{"role": "user", "content": "hi"}]}

    r1 = await client.post(COMPLETIONS, json=payload, headers=auth_key(key["key"]))
    assert r1.status_code == 200, f"request 1 expected 200: {r1.text}"

    r2 = await client.post(COMPLETIONS, json=payload, headers=auth_key(key["key"]))
    assert_problem(r2, 429, "ERR_RATE_LIMITED")

    assert upstream.calls == 1, f"expected exactly 1 admitted call, got {upstream.calls}"
