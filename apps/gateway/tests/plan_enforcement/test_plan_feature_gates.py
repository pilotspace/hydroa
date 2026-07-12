"""RED suite: M6/R3-R5/M9 — feature-gate rejections at the batch-policy / guardrails /
logs-explorer seams (TASK.md §3, FROZEN @ v1). WS realtime seam (R6) is covered separately
in test_plan_feature_realtime_ws.py (different harness — Starlette TestClient, not httpx).
"""

from __future__ import annotations

import json

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .conftest import assert_problem, assign_plan, auth, seed_plan, signup_owner

BATCH_POLICY = "/admin/batch-policy"
GUARDRAILS = "/admin/guardrails"
LOGS = "/admin/logs"


async def _owner(client: httpx.AsyncClient, *, name: str, email: str) -> dict[str, str]:
    return await signup_owner(client, tenant_name=name, email=email)


# ---------------------------------------------------------------------------
# M6/R3 — put_batch_policy: enabling refused for a plan lacking "batch"
# ---------------------------------------------------------------------------


async def test_enabling_batch_refused_for_plan_lacking_feature(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await _owner(client, name="BatchGateCo", email="owner@batchgate.io")
    plan_id = await seed_plan(db_session, name="starter", feature_flags=["logs_explorer"])
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)

    resp = await client.put(BATCH_POLICY, json={"enabled": True}, headers=auth(owner["jwt"]))

    assert_problem(resp, 403, "ERR_PLAN_FEATURE_NOT_ENABLED")
    hint = resp.json().get("upgrade_hint")
    assert hint is not None
    assert hint["plan_id"] == plan_id
    assert hint["feature"] == "batch"

    row = (
        await db_session.execute(
            text("SELECT batch_grouping_enabled FROM tenants WHERE id = :tid"),
            {"tid": owner["tenant_id"]},
        )
    ).fetchone()
    assert row[0] is False, "tenants.batch_grouping_enabled must remain unchanged"


async def test_disabling_batch_is_never_gated(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await _owner(client, name="BatchGateOffCo", email="owner@batchgateoff.io")
    plan_id = await seed_plan(db_session, name="starter", feature_flags=[])
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)

    resp = await client.put(BATCH_POLICY, json={"enabled": False}, headers=auth(owner["jwt"]))

    assert resp.status_code == 200


async def test_batch_enable_succeeds_for_plan_granting_feature(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await _owner(client, name="BatchGateOkCo", email="owner@batchgateok.io")
    plan_id = await seed_plan(db_session, name="team", feature_flags=["batch"])
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)

    resp = await client.put(BATCH_POLICY, json={"enabled": True}, headers=auth(owner["jwt"]))

    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


# ---------------------------------------------------------------------------
# M7 — unplanned tenant: batch enable is byte-identical to pre-task behavior
# ---------------------------------------------------------------------------


async def test_unplanned_tenant_can_enable_batch_exactly_as_before(
    client: httpx.AsyncClient,
) -> None:
    owner = await _owner(client, name="BatchUnplannedCo", email="owner@batchunplanned.io")

    resp = await client.put(BATCH_POLICY, json={"enabled": True}, headers=auth(owner["jwt"]))

    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


# ---------------------------------------------------------------------------
# M6/R4 — put_guardrails: ml_moderation key gated; other keys unaffected
# ---------------------------------------------------------------------------


async def test_configuring_ml_moderation_refused_for_plan_lacking_feature(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await _owner(client, name="MlModGateCo", email="owner@mlmodgate.io")
    plan_id = await seed_plan(db_session, name="starter", feature_flags=["logs_explorer"])
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)

    resp = await client.put(
        GUARDRAILS,
        json={"ml_moderation": {"enabled": True, "mode": "block"}},
        headers=auth(owner["jwt"]),
    )

    assert_problem(resp, 403, "ERR_PLAN_FEATURE_NOT_ENABLED")
    hint = resp.json().get("upgrade_hint")
    assert hint is not None
    assert hint["feature"] == "ml_moderation"

    row = (
        await db_session.execute(
            text("SELECT guardrail_configs FROM tenants WHERE id = :tid"),
            {"tid": owner["tenant_id"]},
        )
    ).fetchone()
    configs = row[0] if not isinstance(row[0], str) else json.loads(row[0])
    assert "ml_moderation" not in (configs or {}), "no partial write on rejection"


async def test_editing_unrelated_guardrail_key_unaffected_by_ml_moderation_gate(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await _owner(client, name="MlModEdgeCo", email="owner@mlmodedge.io")
    plan_id = await seed_plan(db_session, name="starter", feature_flags=[])
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)

    resp = await client.put(
        GUARDRAILS,
        json={"prompt_injection": {"enabled": True, "mode": "block"}},
        headers=auth(owner["jwt"]),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["prompt_injection"] == {"enabled": True, "mode": "block"}


async def test_ml_moderation_configure_succeeds_for_plan_granting_feature(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await _owner(client, name="MlModOkCo", email="owner@mlmodok.io")
    plan_id = await seed_plan(db_session, name="enterprise", feature_flags=["ml_moderation"])
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)

    resp = await client.put(
        GUARDRAILS,
        json={"ml_moderation": {"enabled": True, "mode": "block"}},
        headers=auth(owner["jwt"]),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["ml_moderation"]["enabled"] is True


async def test_unplanned_tenant_can_configure_ml_moderation_exactly_as_before(
    client: httpx.AsyncClient,
) -> None:
    owner = await _owner(client, name="MlModUnplannedCo", email="owner@mlmodunplanned.io")

    resp = await client.put(
        GUARDRAILS,
        json={"ml_moderation": {"enabled": True, "mode": "block"}},
        headers=auth(owner["jwt"]),
    )

    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# M6/R5 — list_logs / get_log: query itself refused for a plan lacking "logs_explorer"
# ---------------------------------------------------------------------------


async def test_list_logs_refused_for_plan_lacking_feature(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await _owner(client, name="LogsGateCo", email="owner@logsgate.io")
    plan_id = await seed_plan(db_session, name="starter", feature_flags=["batch"])
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)

    resp = await client.get(LOGS, headers=auth(owner["jwt"]))

    assert_problem(resp, 403, "ERR_PLAN_FEATURE_NOT_ENABLED")
    hint = resp.json().get("upgrade_hint")
    assert hint is not None
    assert hint["feature"] == "logs_explorer"


async def test_get_log_refused_for_plan_lacking_feature(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    import uuid

    owner = await _owner(client, name="LogsDetailGateCo", email="owner@logsdetailgate.io")
    plan_id = await seed_plan(db_session, name="starter", feature_flags=["batch"])
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)

    resp = await client.get(f"{LOGS}/{uuid.uuid4()}", headers=auth(owner["jwt"]))

    assert_problem(resp, 403, "ERR_PLAN_FEATURE_NOT_ENABLED")


async def test_list_logs_succeeds_for_plan_granting_feature(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner = await _owner(client, name="LogsGateOkCo", email="owner@logsgateok.io")
    plan_id = await seed_plan(db_session, name="starter", feature_flags=["logs_explorer"])
    await assign_plan(db_session, tenant_id=owner["tenant_id"], plan_id=plan_id)

    resp = await client.get(LOGS, headers=auth(owner["jwt"]))

    assert resp.status_code == 200, resp.text


async def test_unplanned_tenant_can_query_logs_exactly_as_before(
    client: httpx.AsyncClient,
) -> None:
    owner = await _owner(client, name="LogsUnplannedCo", email="owner@logsunplanned.io")

    resp = await client.get(LOGS, headers=auth(owner["jwt"]))

    assert resp.status_code == 200, resp.text
