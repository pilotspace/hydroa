"""RED suite for eval-run-executor (/v1/evals/.../runs) — the R7 governed-replay task.

Contract under test (eval-run-executor PLAN.md §3, FROZEN @ sha256:1353206d):
  POST /v1/evals/sets/{set_id}/runs {model} -> 201 { id:"er_<32hex>", eval_set_id, model,
                                     status, case_count, created_at }
      403 ERR_ZDR_PAYLOAD_BLOCKED (ZDR tenant, refused outright at launch, M5)
      404 ERR_EVAL_SET_NOT_FOUND  (absent OR cross-tenant set, M6)
  GET  /v1/evals/runs/{run_id}         -> 200 { ..., status, case_count, counts:{...} }
  GET  /v1/evals/runs/{run_id}/cases   -> 200 list, case-creation order (A5)

A run REPLAYS each case's stored request_body against the run's model THROUGH the real
governance path (CompletionUseCase.complete) — so governance (M1) + one-usage-record (M2) hold
by construction; a governance refusal never dials (M3); the breaker + concurrency are
PER-TENANT, never the global one (M4, R:GLOBAL_BREAKER); ZDR refuses outright + atomic (M5); a
resume never re-bills a terminal case (M7).

RED until the evals/runs module + migration f5b2d8c41a37 are wired. DO NOT edit to make pass —
that is Build's job.
"""

from __future__ import annotations

import asyncio
import datetime
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from gateway.proxy.domain.errors import UpstreamUnavailableError

from tests import _redis_env

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fakes — the M8 zero-network seam: a fake upstream + a counting usage recorder.
# ---------------------------------------------------------------------------


class FakeEvalUpstream:
    """CompletionUpstream fake: counts dials; returns 200 / raises / sleeps-on-marker.

    ``mode`` = 'ok' (200 echo), 'fail' (UpstreamUnavailableError — drives the breaker), or
    'slow_marker' (sleep past the per-call timeout iff the message content contains 'SLOW').
    """

    def __init__(self, *, mode: str = "ok", dwell: float = 0.0) -> None:
        self.mode = mode
        self.calls = 0
        self._dwell = dwell  # seconds each dial lingers (to create in-flight overlap for A3)
        self._inflight = 0
        self.max_inflight = 0
        self._usage = {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        self._inflight += 1
        self.max_inflight = max(self.max_inflight, self._inflight)
        content = ""
        try:
            content = str(payload["messages"][-1]["content"])
        except Exception:
            content = ""
        try:
            if self.mode == "fail":
                raise UpstreamUnavailableError("eval upstream boom")
            if self._dwell:
                await asyncio.sleep(self._dwell)
            if self.mode == "slow_marker" and "SLOW" in content:
                await asyncio.sleep(1.0)
        finally:
            self._inflight -= 1
        return (
            200,
            {
                "id": "chatcmpl-eval",
                "object": "chat.completion",
                "model": payload.get("model"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": f"echo:{content}"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": dict(self._usage),
            },
        )

    def stream(self, payload: dict[str, Any]) -> Any:
        raise NotImplementedError


class CountingRecorder:
    """In-memory UsageRecorder — captures each record (avoids the Redis→PG flush timing)."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(
        self, *, tenant_id: Any, key_id: Any, model: str, usage: Any, status: int, **_: Any
    ) -> None:
        self.records.append({"tenant_id": tenant_id, "model": model, "status": status})


# ---------------------------------------------------------------------------
# Fixtures — tenant + key + an active catalog model (mirror the harness tests).
# ---------------------------------------------------------------------------


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _signup_key(client: Any, *, tenant: str, email: str) -> dict[str, str]:
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant, "email": email, "password": "correct horse battery staple"},
    )
    assert signup.status_code == 201, signup.text
    token = (
        await client.post(
            "/admin/auth/login", json={"email": email, "password": "correct horse battery staple"}
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys", json={"name": f"k-{tenant}"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert created.status_code == 201, created.text
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": signup.json()["tenant_id"],
    }


@pytest.fixture
async def tenant_a(client: Any) -> dict[str, str]:
    return await _signup_key(client, tenant="RunA", email="run-a@example.io")


@pytest.fixture
async def tenant_b(client: Any) -> dict[str, str]:
    return await _signup_key(client, tenant="RunB", email="run-b@example.io")


@pytest.fixture
async def active_model(db_session: Any) -> str:
    model_id = "openai/gpt-4o-eval"
    await db_session.execute(
        text("INSERT INTO models (id, name, context_length, active) VALUES (:i, :n, 128000, true)"),
        {"i": model_id, "n": "GPT-4o eval"},
    )
    await db_session.execute(
        text(
            "INSERT INTO pricing_snapshots "
            "(id, model_id, prompt_usd_per_token, completion_usd_per_token, captured_at) "
            "VALUES (:id, :m, 0.0000025, 0.00001, now())"
        ),
        {"id": str(uuid.uuid4()), "m": model_id},
    )
    await db_session.commit()
    return model_id


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_set(client: Any, key: str, name: str) -> str:
    resp = await client.post("/v1/evals/sets", json={"name": name}, headers=_bearer(key))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _add_case(client: Any, key: str, set_id: str, content: str) -> None:
    resp = await client.post(
        f"/v1/evals/sets/{set_id}/cases",
        json={
            "request_body": {
                "model": "ignored",
                "messages": [{"role": "user", "content": content}],
            },
            "assertion": {"kind": "contains", "expected": "echo"},
        },
        headers=_bearer(key),
    )
    assert resp.status_code == 201, resp.text


async def _count(app: Any, sql: str, **params: Any) -> int:
    async with app.state.sessionmaker() as session:
        return int((await session.execute(text(sql), params)).scalar_one())


async def _poll(predicate: Any, *, ticks: int = 250) -> bool:
    for _ in range(ticks):
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


def _install(app: Any, upstream: Any, recorder: Any | None = None) -> CountingRecorder:
    app.state.completion_upstream = upstream
    rec = recorder or CountingRecorder()
    app.state.usage_recorder = rec
    return rec


# ---------------------------------------------------------------------------
# M1 — a run enters governance per case; a dial happens once per case
# ---------------------------------------------------------------------------


async def test_run_enters_governance_per_case(
    client: Any, app: Any, tenant_a: dict[str, str], active_model: str
) -> None:
    """covers: M1, R:GOVERNANCE_BYPASS — every case dials THROUGH complete()'s guards, once each."""
    rec = _install(app, FakeEvalUpstream(mode="ok"))
    upstream = app.state.completion_upstream
    set_id = await _make_set(client, tenant_a["key"], "gov")
    for i in range(3):
        await _add_case(client, tenant_a["key"], set_id, f"case-{i}")

    run = await client.post(
        f"/v1/evals/sets/{set_id}/runs",
        json={"model": active_model},
        headers=_bearer(tenant_a["key"]),
    )
    assert run.status_code == 201, run.text
    run_id = run.json()["id"]
    assert run.json()["case_count"] == 3

    cases = (
        await client.get(f"/v1/evals/runs/{run_id}/cases", headers=_bearer(tenant_a["key"]))
    ).json()
    assert [c["status"] for c in cases["data"]] == ["completed", "completed", "completed"]
    # one dial per case (governance passed for each, then dialed) — never more, never fewer.
    assert upstream.calls == 3
    # each dialed case produced one usage record billed on the SERVED (run's) model id (M2 hook).
    assert await _poll(lambda: len(rec.records) == 3)
    assert {r["model"] for r in rec.records} == {active_model}


# ---------------------------------------------------------------------------
# M3 / E1 — an over-budget run refuses every case with NO dial and NO usage
# ---------------------------------------------------------------------------


async def test_over_budget_run_refuses_every_case_no_dial(
    client: Any, app: Any, tenant_a: dict[str, str], active_model: str
) -> None:
    """covers: M3, E1, R:GOVERNANCE_BYPASS — a spend-capped tenant dials ZERO, bills ZERO."""
    from gateway.budgets.infrastructure.redis_guard import RedisBudgetGuard

    import redis.asyncio as aioredis

    set_id = await _make_set(client, tenant_a["key"], "poor")
    for i in range(3):
        await _add_case(client, tenant_a["key"], set_id, f"case-{i}")

    # Give the key a tiny cap and seed the per-key spend counter past it → every case refused
    # at the budget guard, BEFORE any dial (mirrors the agent-oauth over-cap test).
    await _count(app, "SELECT 1")  # no-op touch to open a session cleanly
    async with app.state.sessionmaker() as s:
        await s.execute(
            text("UPDATE api_keys SET monthly_budget_usd = '0.01' WHERE id = :id"),
            {"id": tenant_a["key_id"]},
        )
        await s.commit()

    redis_client = aioredis.from_url(_redis_env.TEST_REDIS_URL)
    try:
        yyyymm = datetime.datetime.now(datetime.UTC).strftime("%Y%m")
        await redis_client.set(f"usage:spend:key:{tenant_a['key_id']}:{yyyymm}", b"100.00")
        app.state.budget_guard = RedisBudgetGuard(
            redis=redis_client, session_factory=app.state.sessionmaker
        )
        rec = _install(app, FakeEvalUpstream(mode="ok"))
        upstream = app.state.completion_upstream

        run = await client.post(
            f"/v1/evals/sets/{set_id}/runs",
            json={"model": active_model},
            headers=_bearer(tenant_a["key"]),
        )
        assert run.status_code == 201, run.text
        run_id = run.json()["id"]

        cases = (
            await client.get(f"/v1/evals/runs/{run_id}/cases", headers=_bearer(tenant_a["key"]))
        ).json()
        assert [c["status"] for c in cases["data"]] == ["refused", "refused", "refused"]
        assert upstream.calls == 0, "a budget-refused case must never dial upstream"
        # zero usage rows AND zero persisted response payloads.
        assert len(rec.records) == 0
        assert (
            await _count(
                app,
                "SELECT count(*) FROM eval_case_results WHERE tenant_id = :t"
                " AND response_text IS NOT NULL",
                t=tenant_a["tenant_id"],
            )
            == 0
        )
    finally:
        await redis_client.aclose()


# ---------------------------------------------------------------------------
# M2 — exactly one usage record per dialed case, on the served model id
# ---------------------------------------------------------------------------


async def test_one_usage_record_per_dialed_case(
    client: Any, app: Any, tenant_a: dict[str, str], active_model: str
) -> None:
    """covers: M2, R:DOUBLE_BILL — N dial-able cases -> exactly N usage records, served model."""
    rec = _install(app, FakeEvalUpstream(mode="ok"))
    set_id = await _make_set(client, tenant_a["key"], "bill")
    for i in range(4):
        await _add_case(client, tenant_a["key"], set_id, f"distinct-{i}")

    run = await client.post(
        f"/v1/evals/sets/{set_id}/runs",
        json={"model": active_model},
        headers=_bearer(tenant_a["key"]),
    )
    assert run.status_code == 201, run.text

    assert await _poll(lambda: len(rec.records) == 4)
    # exactly N — not N-1 (unmetered) and not N+1 (double-bill); each on the served model id.
    assert len(rec.records) == 4
    assert all(r["model"] == active_model and r["status"] == 200 for r in rec.records)


# ---------------------------------------------------------------------------
# M4 / E2 — the breaker is PER-TENANT: A's failing run never opens B's breaker
# ---------------------------------------------------------------------------


async def test_per_tenant_breaker_isolation(
    client: Any, app: Any, tenant_a: dict[str, str], tenant_b: dict[str, str], active_model: str
) -> None:
    """covers: M4, E2, R:GLOBAL_BREAKER — A's burst opens A's breaker only; B is untouched."""
    executor = app.state.eval_run_executor
    registry = executor._registry  # noqa: SLF001 — asserting the per-tenant keying is the point
    a_tid = uuid.UUID(tenant_a["tenant_id"])
    b_tid = uuid.UUID(tenant_b["tenant_id"])

    # Tenant A runs against an always-failing upstream: enough cases to trip the 5-failure breaker.
    _install(app, FakeEvalUpstream(mode="fail"))
    set_a = await _make_set(client, tenant_a["key"], "breaker-a")
    for i in range(6):
        await _add_case(client, tenant_a["key"], set_a, f"a-{i}")
    run_a = await client.post(
        f"/v1/evals/sets/{set_a}/runs",
        json={"model": active_model},
        headers=_bearer(tenant_a["key"]),
    )
    assert run_a.status_code == 201, run_a.text
    cases_a = (
        await client.get(
            f"/v1/evals/runs/{run_a.json()['id']}/cases", headers=_bearer(tenant_a["key"])
        )
    ).json()
    assert all(c["status"] == "errored" for c in cases_a["data"])
    assert registry.breaker_for(a_tid).is_open() is True
    # B's breaker is a DIFFERENT object and was never touched by A's failures.
    assert registry.breaker_for(b_tid).is_open() is False

    # And B can still complete a run despite A's breaker being open (no shared state).
    _install(app, FakeEvalUpstream(mode="ok"))
    set_b = await _make_set(client, tenant_b["key"], "breaker-b")
    for i in range(2):
        await _add_case(client, tenant_b["key"], set_b, f"b-{i}")
    run_b = await client.post(
        f"/v1/evals/sets/{set_b}/runs",
        json={"model": active_model},
        headers=_bearer(tenant_b["key"]),
    )
    assert run_b.status_code == 201, run_b.text
    cases_b = (
        await client.get(
            f"/v1/evals/runs/{run_b.json()['id']}/cases", headers=_bearer(tenant_b["key"])
        )
    ).json()
    assert all(c["status"] == "completed" for c in cases_b["data"])


# ---------------------------------------------------------------------------
# M5 / E3 — a ZDR run persists ZERO results (outright at launch + atomic mid-run)
# ---------------------------------------------------------------------------


async def test_zdr_run_refused_atomically_zero_results(
    client: Any, app: Any, tenant_a: dict[str, str], active_model: str
) -> None:
    """covers: M5, E3, R:ZDR_BLOCKED — asserted on the PERSISTED ROW count, not the response."""
    _install(app, FakeEvalUpstream(mode="ok"))
    set_id = await _make_set(client, tenant_a["key"], "zdr")
    for i in range(2):
        await _add_case(client, tenant_a["key"], set_id, f"z-{i}")

    # (a) ZDR set BEFORE launch -> refused outright at launch (403), nothing created.
    async with app.state.sessionmaker() as s:
        await s.execute(
            text("UPDATE tenants SET zdr_enabled = true WHERE id = :id"),
            {"id": tenant_a["tenant_id"]},
        )
        await s.commit()
    launch = await client.post(
        f"/v1/evals/sets/{set_id}/runs",
        json={"model": active_model},
        headers=_bearer(tenant_a["key"]),
    )
    assert launch.status_code == 403
    assert launch.json()["error"]["code"] == "ERR_ZDR_PAYLOAD_BLOCKED"
    assert (
        await _count(
            app,
            "SELECT count(*) FROM eval_case_results WHERE tenant_id = :t",
            t=tenant_a["tenant_id"],
        )
        == 0
    )

    # (b) TOCTOU: launch with ZDR OFF, flip it ON, then drive -> the atomic locked re-check at
    # the first payload write refuses; the run is `blocked` and ZERO results persist.
    async with app.state.sessionmaker() as s:
        await s.execute(
            text("UPDATE tenants SET zdr_enabled = false WHERE id = :id"),
            {"id": tenant_a["tenant_id"]},
        )
        await s.commit()
    executor = app.state.eval_run_executor
    run = await executor.launch(
        tenant_id=uuid.UUID(tenant_a["tenant_id"]),
        key_id=uuid.UUID(tenant_a["key_id"]),
        raw_key=tenant_a["key"],
        eval_set_id=uuid.UUID(_strip_prefix(set_id)),
        model=active_model,
    )
    async with app.state.sessionmaker() as s:
        await s.execute(
            text("UPDATE tenants SET zdr_enabled = true WHERE id = :id"),
            {"id": tenant_a["tenant_id"]},
        )
        await s.commit()
    await executor.drive(run.id, raw_key=tenant_a["key"])
    async with app.state.sessionmaker() as s:
        status = (
            await s.execute(text("SELECT status FROM eval_runs WHERE id = :id"), {"id": run.id})
        ).scalar_one()
    assert status == "blocked"
    assert (
        await _count(
            app,
            "SELECT count(*) FROM eval_case_results WHERE eval_run_id = :r",
            r=run.id,
        )
        == 0
    )


# ---------------------------------------------------------------------------
# M7 / E4 — a resumed run dials only pending cases; terminal cases are not re-billed
# ---------------------------------------------------------------------------


async def test_resume_does_not_rebill_terminal_cases(
    client: Any, app: Any, tenant_a: dict[str, str], active_model: str
) -> None:
    """covers: M7, E4, R:DOUBLE_BILL — a second drive re-dials nothing; total dials == N once."""
    rec = _install(app, FakeEvalUpstream(mode="ok"))
    upstream = app.state.completion_upstream
    set_id = await _make_set(client, tenant_a["key"], "resume")
    for i in range(3):
        await _add_case(client, tenant_a["key"], set_id, f"r-{i}")

    run = await client.post(
        f"/v1/evals/sets/{set_id}/runs",
        json={"model": active_model},
        headers=_bearer(tenant_a["key"]),
    )
    assert run.status_code == 201, run.text
    run_id_wire = run.json()["id"]
    assert await _poll(lambda: len(rec.records) == 3)
    assert upstream.calls == 3

    # Resume the SAME run (all cases terminal) -> zero new dials, zero new bills.
    executor = app.state.eval_run_executor
    await executor.drive(uuid.UUID(_strip_prefix(run_id_wire)), raw_key=tenant_a["key"])
    await asyncio.sleep(0.05)
    assert upstream.calls == 3, "a resumed drive must not re-dial a terminal case"
    assert len(rec.records) == 3, "a resumed drive must not re-bill a terminal case"
    # exactly N result rows — the UNIQUE(run, case) idempotency held.
    assert (
        await _count(
            app,
            "SELECT count(*) FROM eval_case_results WHERE eval_run_id = :r",
            r=uuid.UUID(_strip_prefix(run_id_wire)),
        )
        == 3
    )


# ---------------------------------------------------------------------------
# M4 / E5 — a per-call timeout errors ONE case; the run continues
# ---------------------------------------------------------------------------


async def test_timeout_case_errored_run_continues(
    client: Any, app: Any, tenant_a: dict[str, str], active_model: str
) -> None:
    """covers: M4, E5 — one case times out (errored), the rest complete; the run is a mix."""
    _install(app, FakeEvalUpstream(mode="slow_marker"))
    # Shorten the per-call timeout so the SLOW case trips it fast (no wall-clock assertion).
    app.state.eval_run_executor._timeout = 0.1  # noqa: SLF001 — test-time knob
    set_id = await _make_set(client, tenant_a["key"], "timeout")
    await _add_case(client, tenant_a["key"], set_id, "fast-1")
    await _add_case(client, tenant_a["key"], set_id, "please-be-SLOW")
    await _add_case(client, tenant_a["key"], set_id, "fast-2")

    run = await client.post(
        f"/v1/evals/sets/{set_id}/runs",
        json={"model": active_model},
        headers=_bearer(tenant_a["key"]),
    )
    assert run.status_code == 201, run.text
    cases = (
        await client.get(
            f"/v1/evals/runs/{run.json()['id']}/cases", headers=_bearer(tenant_a["key"])
        )
    ).json()["data"]
    by_status = sorted(c["status"] for c in cases)
    assert by_status == ["completed", "completed", "errored"], by_status
    # the run still reaches a terminal rollup — one bad case never sinks it.
    got = (
        await client.get(f"/v1/evals/runs/{run.json()['id']}", headers=_bearer(tenant_a["key"]))
    ).json()
    assert got["status"] == "completed"
    assert got["counts"]["errored"] == 1 and got["counts"]["completed"] == 2


# ---------------------------------------------------------------------------
# M6 — a cross-tenant / absent run id is a uniform 404, no oracle
# ---------------------------------------------------------------------------


async def test_cross_tenant_and_absent_run_uniform_404(
    client: Any, app: Any, tenant_a: dict[str, str], tenant_b: dict[str, str], active_model: str
) -> None:
    """covers: M6, R:RUN_NOT_FOUND — cross-tenant and absent are byte-identical 404s."""
    _install(app, FakeEvalUpstream(mode="ok"))
    set_id = await _make_set(client, tenant_a["key"], "iso")
    await _add_case(client, tenant_a["key"], set_id, "x")
    run = await client.post(
        f"/v1/evals/sets/{set_id}/runs",
        json={"model": active_model},
        headers=_bearer(tenant_a["key"]),
    )
    run_id = run.json()["id"]

    # Tenant B asking for A's run.
    cross = await client.get(f"/v1/evals/runs/{run_id}", headers=_bearer(tenant_b["key"]))
    # A wholly-absent run id.
    absent = await client.get(
        f"/v1/evals/runs/er_{uuid.uuid4().hex}", headers=_bearer(tenant_b["key"])
    )
    assert cross.status_code == 404 and absent.status_code == 404
    assert cross.json() == absent.json(), (
        "cross-tenant must be byte-identical to absent (no oracle)"
    )
    assert cross.json()["error"]["code"] == "ERR_EVAL_RUN_NOT_FOUND"
    # A malformed id resolves to the same 404, never a 500.
    malformed = await client.get("/v1/evals/runs/not-a-run", headers=_bearer(tenant_b["key"]))
    assert malformed.status_code == 404


# ---------------------------------------------------------------------------
# A5 — results align to CASE creation order, not completion order
# ---------------------------------------------------------------------------


async def test_results_aligned_to_case_creation_order(
    client: Any, app: Any, tenant_a: dict[str, str], active_model: str
) -> None:
    """covers: A5 — two runs of the same set expose results in identical case-creation order."""
    _install(app, FakeEvalUpstream(mode="ok"))
    set_id = await _make_set(client, tenant_a["key"], "order")
    for i in range(5):
        await _add_case(client, tenant_a["key"], set_id, f"ordered-{i}")

    async def _run_case_ids() -> list[str]:
        run = await client.post(
            f"/v1/evals/sets/{set_id}/runs",
            json={"model": active_model},
            headers=_bearer(tenant_a["key"]),
        )
        assert run.status_code == 201, run.text
        data = (
            await client.get(
                f"/v1/evals/runs/{run.json()['id']}/cases", headers=_bearer(tenant_a["key"])
            )
        ).json()["data"]
        return [c["eval_case_id"] for c in data]

    first = await _run_case_ids()
    second = await _run_case_ids()
    assert first == second, "two runs must align case-for-case regardless of completion order"
    # And that order is the set's own creation order.
    listed = (
        await client.get(f"/v1/evals/sets/{set_id}/cases", headers=_bearer(tenant_a["key"]))
    ).json()["data"]
    assert first == [c["id"] for c in listed]


# ---------------------------------------------------------------------------
# A4 / E6 — an empty set runs vacuously: completed, 0/0, no exception
# ---------------------------------------------------------------------------


async def test_empty_set_run_completes_vacuously(
    client: Any, app: Any, tenant_a: dict[str, str], active_model: str
) -> None:
    """covers: A4, E6 — running an empty set is a completed 0/0 run, never an error."""
    _install(app, FakeEvalUpstream(mode="ok"))
    set_id = await _make_set(client, tenant_a["key"], "empty")

    run = await client.post(
        f"/v1/evals/sets/{set_id}/runs",
        json={"model": active_model},
        headers=_bearer(tenant_a["key"]),
    )
    assert run.status_code == 201, run.text
    body = run.json()
    assert body["case_count"] == 0
    got = (
        await client.get(f"/v1/evals/runs/{body['id']}", headers=_bearer(tenant_a["key"]))
    ).json()
    assert got["status"] == "completed"
    assert got["counts"] == {"completed": 0, "refused": 0, "errored": 0, "pending": 0}
    cases = (
        await client.get(f"/v1/evals/runs/{body['id']}/cases", headers=_bearer(tenant_a["key"]))
    ).json()
    assert cases["data"] == []


# ---------------------------------------------------------------------------
# A1 — a run is launched+billed by the launching key; cross-tenant launch is a 404
# ---------------------------------------------------------------------------


async def test_cross_tenant_launch_and_billing_identity(
    client: Any, app: Any, tenant_a: dict[str, str], tenant_b: dict[str, str], active_model: str
) -> None:
    """covers: A1 — B cannot launch a run on A's set (404); a run bills the LAUNCHING tenant/key."""
    rec = _install(app, FakeEvalUpstream(mode="ok"))
    set_id = await _make_set(client, tenant_a["key"], "a1")
    await _add_case(client, tenant_a["key"], set_id, "own")

    # Tenant B launching a run on tenant A's set -> uniform 404 (A's set is invisible to B).
    forbidden = await client.post(
        f"/v1/evals/sets/{set_id}/runs",
        json={"model": active_model},
        headers=_bearer(tenant_b["key"]),
    )
    assert forbidden.status_code == 404
    assert forbidden.json()["error"]["code"] == "ERR_EVAL_SET_NOT_FOUND"

    # Tenant A's own run bills A's tenant, never B's (the usage record carries the launcher).
    run = await client.post(
        f"/v1/evals/sets/{set_id}/runs",
        json={"model": active_model},
        headers=_bearer(tenant_a["key"]),
    )
    assert run.status_code == 201, run.text
    assert await _poll(lambda: len(rec.records) == 1)
    assert rec.records[0]["tenant_id"] == uuid.UUID(tenant_a["tenant_id"])


# ---------------------------------------------------------------------------
# A2 — the run's case set is snapshotted AT LAUNCH; later cases do not join it
# ---------------------------------------------------------------------------


async def test_run_snapshot_fixed_at_launch(
    client: Any, app: Any, tenant_a: dict[str, str], active_model: str
) -> None:
    """covers: A2 — a case added AFTER launch does not change the launched run's case_count."""
    _install(app, FakeEvalUpstream(mode="ok"))
    set_id = await _make_set(client, tenant_a["key"], "snapshot")
    await _add_case(client, tenant_a["key"], set_id, "at-launch-1")
    await _add_case(client, tenant_a["key"], set_id, "at-launch-2")

    run = await client.post(
        f"/v1/evals/sets/{set_id}/runs",
        json={"model": active_model},
        headers=_bearer(tenant_a["key"]),
    )
    assert run.status_code == 201, run.text
    assert run.json()["case_count"] == 2

    # A case added after launch is NOT in the already-launched run's fixed denominator.
    await _add_case(client, tenant_a["key"], set_id, "after-launch-3")
    got = (
        await client.get(f"/v1/evals/runs/{run.json()['id']}", headers=_bearer(tenant_a["key"]))
    ).json()
    assert got["case_count"] == 2, "the run's case set is fixed at launch (A2)"


# ---------------------------------------------------------------------------
# A3 — per-tenant concurrency is BOUNDED: never more than the ceiling in flight
# ---------------------------------------------------------------------------


async def test_bounded_per_tenant_concurrency(
    client: Any, app: Any, tenant_a: dict[str, str], active_model: str
) -> None:
    """covers: A3 — a run of N>ceiling cases never has more than the ceiling dialing at once."""
    from gateway.evals.runs.infrastructure.upstream_adapter import TenantExecutionRegistry

    fake = FakeEvalUpstream(mode="ok", dwell=0.05)  # each dial lingers so overlap is observable
    _install(app, fake)
    # Pin a small, known ceiling on the executor's registry so the bound is unambiguous.
    executor = app.state.eval_run_executor
    executor._registry = TenantExecutionRegistry(concurrency=2)  # noqa: SLF001 — test knob

    set_id = await _make_set(client, tenant_a["key"], "concurrency")
    for i in range(6):
        await _add_case(client, tenant_a["key"], set_id, f"c-{i}")

    run = await client.post(
        f"/v1/evals/sets/{set_id}/runs",
        json={"model": active_model},
        headers=_bearer(tenant_a["key"]),
    )
    assert run.status_code == 201, run.text
    assert fake.calls == 6
    assert fake.max_inflight <= 2, f"exceeded the per-tenant ceiling: {fake.max_inflight}"


def _strip_prefix(wire_id: str) -> str:
    """'er_<hex>' / 'es_<hex>' -> the raw 32-hex (test helper for direct executor calls)."""
    return wire_id.split("_", 1)[1]
