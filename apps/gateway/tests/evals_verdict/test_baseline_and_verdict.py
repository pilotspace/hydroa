"""RED suite for baseline-and-verdict (/v1/evals/.../baseline · /verdict) — R7 L2.

Contract under test (baseline-and-verdict PLAN.md, FROZEN @ sha256:b3b0c7c9d979e935):
  PUT /v1/evals/sets/{set_id}/baseline {run_id} -> 200 { eval_set_id, baseline_run_id, pinned_at }
      404 ERR_EVAL_SET_NOT_FOUND (absent/cross-tenant set)
      404 ERR_EVAL_RUN_NOT_FOUND (run not in set / cross-tenant)
  GET /v1/evals/runs/{run_id}/verdict -> 200 { run_id, score:{passed,total},
      baseline:{run_id, score:{passed,total}} | null, verdict: "pass"|"fail"|"no_baseline" }
      404 ERR_EVAL_RUN_NOT_FOUND (absent/cross-tenant run)

A run's SCORE is (pass_count, total) computed ON DEMAND — total = the launch snapshot
(fail-closed denominator), pass_count = completed cases the PURE scorer passes (M1). The
verdict is an EXACT integer cross-multiply — never float (M2/A3, R:FLOAT_TIE). No baseline is
the explicit `no_baseline` state, never a pass (M4). Everything tenant-scoped, uniform 404 (M5).
The pin is durable (M6).

RED until gateway/evals/verdict/ + the eval_baselines migration are wired. DO NOT edit to make
pass — that is Build's job.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text

from gateway.evals.scoring.scorers import DeterministicScorer
from gateway.evals.verdict.application.scoring import decide, score_run
from gateway.evals.verdict.domain.entities import CaseResultView, ScorableCase

from tests import _redis_env  # noqa: F401 — ensures the shared Redis env is applied

# No module-level asyncio pytestmark: this file mixes PURE sync tests (score_run/decide) with
# async integration tests, and asyncio_mode="auto" already marks the async ones. Marking the
# sync tests would warn.


# ---------------------------------------------------------------------------
# Fakes / fixtures — mirror the eval-run-executor harness.
# ---------------------------------------------------------------------------


class ScriptedUpstream:
    """CompletionUpstream fake: every dial returns 200 with content ``{prefix}:{echoed}``.

    Two runs of the SAME set can be given DIFFERENT prefixes so their stored response_text —
    and therefore their on-demand score against a `contains "echo"` assertion — differ. That is
    how a candidate run can be strictly worse/better than its baseline of the same set.
    """

    def __init__(self, *, prefix: str = "echo") -> None:
        self.prefix = prefix
        self.calls = 0
        self._usage = {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls += 1
        try:
            content = str(payload["messages"][-1]["content"])
        except Exception:
            content = ""
        return (
            200,
            {
                "id": "chatcmpl-verdict",
                "object": "chat.completion",
                "model": payload.get("model"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": f"{self.prefix}:{content}"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": dict(self._usage),
            },
        )

    def stream(self, payload: dict[str, Any]) -> Any:
        raise NotImplementedError


class _Recorder:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    async def record(
        self, *, tenant_id: Any, key_id: Any, model: str, usage: Any, status: int, **_: Any
    ) -> None:
        self.records.append({"model": model, "status": status})


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
    return {"key": created.json()["key"], "tenant_id": signup.json()["tenant_id"]}


@pytest.fixture
async def tenant_a(client: Any) -> dict[str, str]:
    return await _signup_key(client, tenant="VerdictA", email="verdict-a@example.io")


@pytest.fixture
async def tenant_b(client: Any) -> dict[str, str]:
    return await _signup_key(client, tenant="VerdictB", email="verdict-b@example.io")


@pytest.fixture
async def active_model(db_session: Any) -> str:
    model_id = "openai/gpt-4o-verdict"
    await db_session.execute(
        text("INSERT INTO models (id, name, context_length, active) VALUES (:i, :n, 128000, true)"),
        {"i": model_id, "n": "GPT-4o verdict"},
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


def _install(app: Any, upstream: Any) -> _Recorder:
    app.state.completion_upstream = upstream
    rec = _Recorder()
    app.state.usage_recorder = rec
    return rec


async def _make_set(client: Any, key: str, name: str) -> str:
    resp = await client.post("/v1/evals/sets", json={"name": name}, headers=_bearer(key))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _add_case(
    client: Any, key: str, set_id: str, content: str, expected: str = "echo"
) -> None:
    """A case whose assertion is `contains <expected>`; default 'echo' passes ScriptedUpstream('echo')."""
    resp = await client.post(
        f"/v1/evals/sets/{set_id}/cases",
        json={
            "request_body": {
                "model": "ignored",
                "messages": [{"role": "user", "content": content}],
            },
            "assertion": {"kind": "contains", "expected": expected},
        },
        headers=_bearer(key),
    )
    assert resp.status_code == 201, resp.text


async def _launch(client: Any, key: str, set_id: str, model: str) -> str:
    resp = await client.post(
        f"/v1/evals/sets/{set_id}/runs", json={"model": model}, headers=_bearer(key)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------
# PURE core — score_run + decide (no DB, no network). Bind M1, M2, A2, A3, A4.
# ---------------------------------------------------------------------------


def _case(assertion: dict[str, Any]) -> ScorableCase:
    return ScorableCase(id=uuid.uuid4(), assertion=assertion)


def _result(status: str, text_: str | None) -> CaseResultView:
    return CaseResultView(status=status, response_text=text_)


def test_score_is_completed_scorer_pass_over_snapshot_total() -> None:
    """covers: M1 · covers: A2 — denominator = snapshot; refused/errored count, never pass."""
    cases = [_case({"kind": "contains", "expected": "echo"}) for _ in range(5)]
    results = {
        cases[0].id: _result("completed", "echo:a"),  # pass
        cases[1].id: _result("completed", "echo:b"),  # pass
        cases[2].id: _result("completed", "echo:c"),  # pass
        cases[3].id: _result("errored", None),  # in total, NOT a pass
        cases[4].id: _result("refused", None),  # in total, NOT a pass
    }
    assert score_run(cases, results, DeterministicScorer()) == (3, 5)


def test_unscoreable_completed_case_is_not_a_pass() -> None:
    """covers: M1 · covers: A4 — a completed case with an unsupported kind scores False."""
    case = _case({"kind": "no_such_kind", "expected": "x"})
    results = {case.id: _result("completed", "anything at all")}
    assert score_run([case], results, DeterministicScorer()) == (0, 1)


def test_verdict_strictly_worse_fails() -> None:
    """covers: M2 — candidate 2/5 vs baseline 4/5 → fail."""
    assert decide((2, 5), (4, 5)) == "fail"


def test_verdict_strictly_better_passes() -> None:
    """covers: M2 — candidate 5/5 vs baseline 3/5 → pass."""
    assert decide((5, 5), (3, 5)) == "pass"


def test_verdict_equal_rationals_pass_exact_no_float() -> None:
    """covers: M2 · covers: A3 — 3/5 == 6/10 by EXACT cross-multiply → pass, both directions."""
    assert decide((3, 5), (6, 10)) == "pass"  # 3*10 == 6*5 → 30>=30
    assert decide((6, 10), (3, 5)) == "pass"  # symmetric at the boundary → ≥ passes both ways


# ---------------------------------------------------------------------------
# INTEGRATION — pin + verdict end to end. Bind M3, M4, M5, M6, A6.
# ---------------------------------------------------------------------------


async def test_no_baseline_is_explicit_state_not_pass(
    client: Any, app: Any, tenant_a: dict[str, str], active_model: str
) -> None:
    """covers: M4 — a run with nothing pinned yields `no_baseline`, never `pass`."""
    _install(app, ScriptedUpstream(prefix="echo"))
    set_id = await _make_set(client, tenant_a["key"], "nb")
    await _add_case(client, tenant_a["key"], set_id, "one")
    run_id = await _launch(client, tenant_a["key"], set_id, active_model)

    resp = await client.get(f"/v1/evals/runs/{run_id}/verdict", headers=_bearer(tenant_a["key"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verdict"] == "no_baseline"
    assert body["baseline"] is None
    assert body["score"] == {"passed": 1, "total": 1}


async def test_pin_and_repin_idempotent_one_baseline(
    client: Any, app: Any, tenant_a: dict[str, str], active_model: str
) -> None:
    """covers: M3 — re-pin replaces; exactly one baseline per set; verdict tracks the latest pin."""
    _install(app, ScriptedUpstream(prefix="echo"))
    set_id = await _make_set(client, tenant_a["key"], "repin")
    await _add_case(client, tenant_a["key"], set_id, "one")
    run_a = await _launch(client, tenant_a["key"], set_id, active_model)
    run_b = await _launch(client, tenant_a["key"], set_id, active_model)

    for run_id in (run_a, run_b):
        pin = await client.put(
            f"/v1/evals/sets/{set_id}/baseline",
            json={"run_id": run_id},
            headers=_bearer(tenant_a["key"]),
        )
        assert pin.status_code == 200, pin.text
    assert pin.json()["baseline_run_id"] == run_b

    async with app.state.sessionmaker() as session:
        n = int(
            (
                await session.execute(
                    text("SELECT count(*) FROM eval_baselines WHERE eval_set_id = :s"),
                    {"s": uuid.UUID(set_id.removeprefix("es_"))},
                )
            ).scalar_one()
        )
    assert n == 1, "re-pin must UPDATE the single baseline row, not insert a second"


async def test_pin_rejects_run_not_in_set(
    client: Any, app: Any, tenant_a: dict[str, str], active_model: str
) -> None:
    """covers: M3 — pinning a run that belongs to a DIFFERENT set → 404, no cross-set baseline."""
    _install(app, ScriptedUpstream(prefix="echo"))
    set_x = await _make_set(client, tenant_a["key"], "x")
    set_y = await _make_set(client, tenant_a["key"], "y")
    await _add_case(client, tenant_a["key"], set_y, "one")
    run_y = await _launch(client, tenant_a["key"], set_y, active_model)

    resp = await client.put(
        f"/v1/evals/sets/{set_x}/baseline",
        json={"run_id": run_y},
        headers=_bearer(tenant_a["key"]),
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["error"]["code"] == "ERR_EVAL_RUN_NOT_FOUND"


async def test_cross_tenant_pin_and_verdict_uniform_404(
    client: Any, app: Any, tenant_a: dict[str, str], tenant_b: dict[str, str], active_model: str
) -> None:
    """covers: M5 — tenant B cannot pin or read tenant A's set/run; uniform 404, no oracle."""
    _install(app, ScriptedUpstream(prefix="echo"))
    set_a = await _make_set(client, tenant_a["key"], "a")
    await _add_case(client, tenant_a["key"], set_a, "one")
    run_a = await _launch(client, tenant_a["key"], set_a, active_model)

    pin = await client.put(
        f"/v1/evals/sets/{set_a}/baseline",
        json={"run_id": run_a},
        headers=_bearer(tenant_b["key"]),
    )
    assert pin.status_code == 404, pin.text
    verdict = await client.get(f"/v1/evals/runs/{run_a}/verdict", headers=_bearer(tenant_b["key"]))
    assert verdict.status_code == 404, verdict.text
    assert verdict.json()["error"]["code"] == "ERR_EVAL_RUN_NOT_FOUND"

    # And tenant B never wrote a baseline for A's set.
    async with app.state.sessionmaker() as session:
        n = int(
            (
                await session.execute(
                    text("SELECT count(*) FROM eval_baselines WHERE eval_set_id = :s"),
                    {"s": uuid.UUID(set_a.removeprefix("es_"))},
                )
            ).scalar_one()
        )
    assert n == 0


async def test_baseline_durable_across_store_instances(
    client: Any, app: Any, tenant_a: dict[str, str], active_model: str
) -> None:
    """covers: M6 — a pin written via one store is read via a FRESH store instance (redeploy proxy)."""
    from gateway.evals.verdict.infrastructure.repository import SqlAlchemyEvalBaselineStore

    _install(app, ScriptedUpstream(prefix="echo"))
    set_id = await _make_set(client, tenant_a["key"], "dur")
    await _add_case(client, tenant_a["key"], set_id, "one")
    run_id = await _launch(client, tenant_a["key"], set_id, active_model)

    tenant = uuid.UUID(tenant_a["tenant_id"])
    set_uuid = uuid.UUID(set_id.removeprefix("es_"))
    run_uuid = uuid.UUID(run_id.removeprefix("er_"))

    writer = SqlAlchemyEvalBaselineStore(app.state.sessionmaker)
    await writer.pin_baseline(tenant_id=tenant, eval_set_id=set_uuid, run_id=run_uuid)

    reader = SqlAlchemyEvalBaselineStore(app.state.sessionmaker)  # fresh instance
    row = await reader.get_baseline(tenant_id=tenant, eval_set_id=set_uuid)
    assert row is not None
    assert row.run_id == run_uuid


async def test_fail_verdict_reports_both_scores(
    client: Any, app: Any, tenant_a: dict[str, str], active_model: str
) -> None:
    """covers: A6 — a strictly-worse candidate FAILS, and the response names BOTH scores."""
    # Baseline run: prefix 'echo' → every case passes `contains echo`.
    _install(app, ScriptedUpstream(prefix="echo"))
    set_id = await _make_set(client, tenant_a["key"], "fail")
    for i in range(2):
        await _add_case(client, tenant_a["key"], set_id, f"case-{i}")
    baseline_run = await _launch(client, tenant_a["key"], set_id, active_model)

    pin = await client.put(
        f"/v1/evals/sets/{set_id}/baseline",
        json={"run_id": baseline_run},
        headers=_bearer(tenant_a["key"]),
    )
    assert pin.status_code == 200, pin.text

    # Candidate run: prefix 'nope' → NO case contains 'echo' → strictly worse (0/2 vs 2/2).
    _install(app, ScriptedUpstream(prefix="nope"))
    candidate_run = await _launch(client, tenant_a["key"], set_id, active_model)

    resp = await client.get(
        f"/v1/evals/runs/{candidate_run}/verdict", headers=_bearer(tenant_a["key"])
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verdict"] == "fail"
    assert body["score"] == {"passed": 0, "total": 2}
    assert body["baseline"]["run_id"] == baseline_run
    assert body["baseline"]["score"] == {"passed": 2, "total": 2}
