"""Failing-first (RED) suite for guardrail-analytics — GET /admin/guardrails/analytics
(TASK.md §4).

Covers the read-side scenarios in §2 SCENARIOS: M3 (windowed totals default to month),
M4 (group_by=guardrail|policy_source|key_id + omitted), M5 (key_id filter + cross-tenant
404), M6 (tenant isolation), M7 (empty window → explicit zeros), R1-R7 (rejections).

TRUE-RED RULE: every test asserts TARGET behavior and fails NOW for the RIGHT reason.

Right-reason red targets:
  - GET /admin/guardrails/analytics does not exist -> FastAPI 404; asserting 200/422/
    403/401 with the target shape FAILS.
  - guardrail_verdict_events table does not exist -> the seeding helper's INSERT raises
    ProgrammingError before the request under test even runs.

Error-code note: this codebase's error_catalog.py maps EVERY PAYLOAD_* validation spec
(window/start/end/group_by/key_id-uuid) to the SAME "ERR_PAYLOAD_INVALID" code, and both
AUTH_TOKEN_MISSING/AUTH_TOKEN_INVALID to "ERR_AUTH_INVALID_TOKEN" — confirmed by reading
gateway/core/error_catalog.py and mirrored by tests/spend_windows/test_spend_windows.py's
own assertions (its sibling /admin/spend endpoint reuses the exact same specs). Asserting
those idealized per-field code names from TASK.md §3's prose would fail for a spurious
reason; these tests assert the REAL codes verbatim reuse actually produces.

Infrastructure:
  - Real Postgres at GATEWAY_TEST_DATABASE_URL (schema rebuilt per test via the root
    conftest.py `app` fixture — Base.metadata.drop_all + create_all)
  - httpx.ASGITransport (no network)
  - asyncio_mode = "auto" (set in pyproject.toml — no @pytest.mark.asyncio needed)
"""

from __future__ import annotations

import datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import (
    ADMIN_GUARDRAILS_ANALYTICS,
    assert_problem,
    auth_jwt,
    create_key,
    member_token_for,
    seed_verdict_events,
    signup_and_login,
)


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


# ===========================================================================
# Scenario — windowed totals default to the current month (M3)
# ===========================================================================


async def test_windowed_totals_default_to_current_month(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="Analytics1Co", email="owner@analytics1.io"
    )
    key_info = await create_key(client, jwt, name="a1-key")

    now = _now_utc()
    this_month = now.replace(day=1, hour=2, minute=0, second=0, microsecond=0)
    last_month_ts = (this_month - datetime.timedelta(days=1)).replace(hour=2)

    await seed_verdict_events(
        db_session,
        tenant_id=tenant_id,
        key_id=key_info["key_id"],
        rows=[
            {"guardrail": "prompt_injection", "action": "blocked", "created_at": this_month},
            {"guardrail": "pii_mask", "action": "masked", "created_at": this_month},
            {"guardrail": "pii_mask", "action": "passed", "created_at": this_month},
            {"guardrail": "prompt_injection", "action": "blocked", "created_at": last_month_ts},
        ],
    )

    resp = await client.get(ADMIN_GUARDRAILS_ANALYTICS, headers=auth_jwt(jwt))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["window"] == "month"
    assert body["totals"]["evaluations"] == 3, body["totals"]
    assert body["totals"]["blocked"] == 1
    assert body["totals"]["masked"] == 1
    assert body["totals"]["passed"] == 1
    assert body["totals"]["hits"] == 2  # evaluations(3) - passed(1)
    assert body["breakdown"] is None


# ===========================================================================
# Scenario — group_by=guardrail returns the pattern-dimension breakdown (M4)
# ===========================================================================


async def test_group_by_guardrail_returns_pattern_breakdown(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="Analytics2Co", email="owner@analytics2.io"
    )
    key_info = await create_key(client, jwt, name="a2-key")
    now = _now_utc()

    rows = [{"guardrail": "prompt_injection", "action": "blocked", "created_at": now}] * 5
    rows += [{"guardrail": "pii_mask", "action": "masked", "created_at": now}] * 2
    await seed_verdict_events(db_session, tenant_id=tenant_id, key_id=key_info["key_id"], rows=rows)

    resp = await client.get(
        ADMIN_GUARDRAILS_ANALYTICS,
        params={"window": "week", "group_by": "guardrail"},
        headers=auth_jwt(jwt),
    )
    assert resp.status_code == 200, resp.text
    breakdown = resp.json()["breakdown"]
    assert breakdown is not None
    by_guardrail = {item["guardrail"]: item for item in breakdown}
    assert by_guardrail["prompt_injection"]["evaluations"] == 5
    assert by_guardrail["pii_mask"]["evaluations"] == 2


# ===========================================================================
# Scenario — group_by=policy_source returns the policy-dimension breakdown (M4)
# ===========================================================================


async def test_group_by_policy_source_returns_policy_breakdown(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="Analytics3Co", email="owner@analytics3.io"
    )
    key_info = await create_key(client, jwt, name="a3-key")
    now = _now_utc()

    rows = [
        {"guardrail": "pii_mask", "action": "masked", "policy_source": "key", "created_at": now}
    ] * 4
    rows += [
        {
            "guardrail": "pii_mask",
            "action": "masked",
            "policy_source": "tenant",
            "created_at": now,
        }
    ] * 6
    await seed_verdict_events(db_session, tenant_id=tenant_id, key_id=key_info["key_id"], rows=rows)

    resp = await client.get(
        ADMIN_GUARDRAILS_ANALYTICS,
        params={"window": "week", "group_by": "policy_source"},
        headers=auth_jwt(jwt),
    )
    assert resp.status_code == 200, resp.text
    breakdown = resp.json()["breakdown"]
    assert breakdown is not None
    by_source = {item["policy_source"]: item for item in breakdown}
    assert by_source["key"]["evaluations"] == 4
    assert by_source["tenant"]["evaluations"] == 6


# ===========================================================================
# Scenario — group_by=key_id returns the key-dimension breakdown (M4)
# ===========================================================================


async def test_group_by_key_id_returns_key_breakdown(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="Analytics4Co", email="owner@analytics4.io"
    )
    key_a = await create_key(client, jwt, name="a4-key-a")
    key_b = await create_key(client, jwt, name="a4-key-b")
    now = _now_utc()

    rows = [
        {
            "guardrail": "prompt_injection",
            "action": "blocked",
            "key_id": key_a["key_id"],
            "created_at": now,
        }
    ] * 3
    rows += [
        {
            "guardrail": "prompt_injection",
            "action": "blocked",
            "key_id": key_b["key_id"],
            "created_at": now,
        }
    ] * 7
    await seed_verdict_events(db_session, tenant_id=tenant_id, key_id=key_a["key_id"], rows=rows)

    resp = await client.get(
        ADMIN_GUARDRAILS_ANALYTICS,
        params={"window": "week", "group_by": "key_id"},
        headers=auth_jwt(jwt),
    )
    assert resp.status_code == 200, resp.text
    breakdown = resp.json()["breakdown"]
    assert breakdown is not None
    by_key = {item["key_id"]: item for item in breakdown}
    assert by_key[key_a["key_id"]]["evaluations"] == 3
    assert by_key[key_b["key_id"]]["evaluations"] == 7


# ===========================================================================
# Scenario — omitted group_by returns totals+buckets only, no breakdown (M4)
# ===========================================================================


async def test_omitted_group_by_returns_totals_and_buckets_only(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="Analytics5Co", email="owner@analytics5.io"
    )
    key_info = await create_key(client, jwt, name="a5-key")
    now = _now_utc()
    await seed_verdict_events(
        db_session,
        tenant_id=tenant_id,
        key_id=key_info["key_id"],
        rows=[{"guardrail": "ml_moderation", "action": "audited", "created_at": now}],
    )

    resp = await client.get(
        ADMIN_GUARDRAILS_ANALYTICS, params={"window": "week"}, headers=auth_jwt(jwt)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["breakdown"] is None
    assert body["totals"]["evaluations"] == 1
    assert len(body["buckets"]) >= 1


# ===========================================================================
# Scenario — key_id filter narrows every query to one key (M5)
# ===========================================================================


async def test_key_id_filter_narrows_to_one_key(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    jwt, tenant_id = await signup_and_login(
        client, tenant_name="Analytics6Co", email="owner@analytics6.io"
    )
    key_a = await create_key(client, jwt, name="a6-key-a")
    key_b = await create_key(client, jwt, name="a6-key-b")
    now = _now_utc()

    await seed_verdict_events(
        db_session,
        tenant_id=tenant_id,
        key_id=key_a["key_id"],
        rows=[
            {
                "guardrail": "prompt_injection",
                "action": "blocked",
                "key_id": key_a["key_id"],
                "created_at": now,
            },
            {
                "guardrail": "prompt_injection",
                "action": "blocked",
                "key_id": key_b["key_id"],
                "created_at": now,
            },
        ],
    )

    resp = await client.get(
        ADMIN_GUARDRAILS_ANALYTICS,
        params={"window": "week", "key_id": key_a["key_id"]},
        headers=auth_jwt(jwt),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["totals"]["evaluations"] == 1


# ===========================================================================
# Scenario — cross-tenant key_id filter is invisible (M5, R5)
# ===========================================================================


async def test_cross_tenant_key_id_filter_returns_404(
    client: httpx.AsyncClient,
) -> None:
    jwt_a, _tenant_a = await signup_and_login(
        client, tenant_name="Analytics7ACo", email="owner@analytics7a.io"
    )
    jwt_b, _tenant_b = await signup_and_login(
        client, tenant_name="Analytics7BCo", email="owner@analytics7b.io"
    )
    key_b = await create_key(client, jwt_b, name="a7-foreign-key")

    resp = await client.get(
        ADMIN_GUARDRAILS_ANALYTICS,
        params={"key_id": key_b["key_id"]},
        headers=auth_jwt(jwt_a),
    )
    assert_problem(resp, 404, "ERR_KEY_NOT_FOUND")


# ===========================================================================
# Scenario — another tenant's rows are never visible (M6)
# ===========================================================================


async def test_tenant_isolation_no_leak(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    jwt_a, tenant_a = await signup_and_login(
        client, tenant_name="Analytics8ACo", email="owner@analytics8a.io"
    )
    jwt_b, tenant_b = await signup_and_login(
        client, tenant_name="Analytics8BCo", email="owner@analytics8b.io"
    )
    key_a = await create_key(client, jwt_a, name="a8-key-a")
    now = _now_utc()

    await seed_verdict_events(
        db_session,
        tenant_id=tenant_a,
        key_id=key_a["key_id"],
        rows=[{"guardrail": "prompt_injection", "action": "blocked", "created_at": now}] * 10,
    )
    _ = tenant_b

    resp = await client.get(ADMIN_GUARDRAILS_ANALYTICS, headers=auth_jwt(jwt_b))
    assert resp.status_code == 200, resp.text
    assert resp.json()["totals"]["evaluations"] == 0


# ===========================================================================
# Scenario — empty window returns explicit zeros, never 404 (M7)
# ===========================================================================


async def test_empty_window_returns_explicit_zeros(client: httpx.AsyncClient) -> None:
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="Analytics9Co", email="owner@analytics9.io"
    )
    resp = await client.get(
        ADMIN_GUARDRAILS_ANALYTICS, params={"window": "day"}, headers=auth_jwt(jwt)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["totals"]["evaluations"] == 0
    assert body["totals"]["hits"] == 0
    assert body["buckets"] == []


# ===========================================================================
# Scenario — invalid group_by is rejected (R1)
# ===========================================================================


async def test_invalid_group_by_rejected(client: httpx.AsyncClient) -> None:
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="Analytics10Co", email="owner@analytics10.io"
    )
    resp = await client.get(
        ADMIN_GUARDRAILS_ANALYTICS, params={"group_by": "team_id"}, headers=auth_jwt(jwt)
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ===========================================================================
# Scenario — invalid window is rejected (R2)
# ===========================================================================


async def test_invalid_window_rejected(client: httpx.AsyncClient) -> None:
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="Analytics11Co", email="owner@analytics11.io"
    )
    resp = await client.get(
        ADMIN_GUARDRAILS_ANALYTICS, params={"window": "year"}, headers=auth_jwt(jwt)
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ===========================================================================
# Scenario — malformed start/end date is rejected (R3)
# ===========================================================================


async def test_malformed_start_date_rejected(client: httpx.AsyncClient) -> None:
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="Analytics12Co", email="owner@analytics12.io"
    )
    resp = await client.get(
        ADMIN_GUARDRAILS_ANALYTICS, params={"start": "not-a-date"}, headers=auth_jwt(jwt)
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ===========================================================================
# Scenario — malformed key_id is rejected (R4)
# ===========================================================================


async def test_malformed_key_id_rejected(client: httpx.AsyncClient) -> None:
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="Analytics13Co", email="owner@analytics13.io"
    )
    resp = await client.get(
        ADMIN_GUARDRAILS_ANALYTICS, params={"key_id": "not-a-uuid"}, headers=auth_jwt(jwt)
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ===========================================================================
# Scenario — a member is forbidden from reading analytics (R6)
# ===========================================================================


async def test_member_forbidden(client: httpx.AsyncClient) -> None:
    jwt, _tenant_id = await signup_and_login(
        client, tenant_name="Analytics14Co", email="owner@analytics14.io"
    )
    member_jwt = member_token_for(jwt, email="member@analytics14.io")
    resp = await client.get(ADMIN_GUARDRAILS_ANALYTICS, headers=auth_jwt(member_jwt))
    body = assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")
    assert "totals" not in body
    assert "breakdown" not in body


# ===========================================================================
# Scenario — missing bearer token is rejected (R7)
# ===========================================================================


async def test_missing_token_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.get(ADMIN_GUARDRAILS_ANALYTICS)
    body = assert_problem(resp, 401, "ERR_AUTH_INVALID_TOKEN")
    assert "totals" not in body
