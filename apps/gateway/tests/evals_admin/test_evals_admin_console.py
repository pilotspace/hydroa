"""RED suite for evals-console backend (/admin/evals/*) — R7 L3, session-authed twin of /v1.

Contract under test (evals-console PLAN.md, FROZEN @ sha256:60cf00293d919fbc):
  A session-authed control-plane surface serves the console. It resolves the tenant from the
  session Identity (JWT via get_current_identity), REUSES the eval stores + the ONE verdict core
  (no logic fork), is READ + basic authoring only (NO launch, never a raw key), and is uniformly
  tenant-scoped (absent/cross-tenant -> 404).

These 4 checks bind the backend Musts/Rejects:
  M1, A1, A2, R:CROSS_TENANT, E5 -> test_admin_evals_session_scoped_reads
  M1, R:LOGIC_FORK             -> test_admin_evals_verdict_matches_v1_reuse
  M2, A1                        -> test_admin_evals_authoring_session_writes
  M2, R:RAW_KEY_IN_CONSOLE      -> test_admin_evals_has_no_launch_and_no_raw_key

RED until gateway/evals/console/ is wired into the app. DO NOT edit to make pass — that is
Build's job. Launch (the only path that needs a raw key) stays on /v1; these tests launch runs
through the /v1 API-key surface and then READ/AUTHOR them through the session surface.
"""

from __future__ import annotations

import inspect
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from tests import _redis_env  # noqa: F401 — ensures the shared Redis env is applied

_PASSWORD = "correct horse battery staple"


class ScriptedUpstream:
    """CompletionUpstream fake: every dial returns 200 with content ``{prefix}:{echoed}``."""

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
                "id": "chatcmpl-console",
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


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _signup(client: Any, *, tenant: str, email: str) -> dict[str, str]:
    """Sign up a tenant and return BOTH its session JWT (`token`) and an API `key`.

    The session token authenticates /admin/evals (console); the API key authenticates /v1 (the
    only surface that can launch a run).
    """
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant, "email": email, "password": _PASSWORD},
    )
    assert signup.status_code == 201, signup.text
    token = (
        await client.post("/admin/auth/login", json={"email": email, "password": _PASSWORD})
    ).json()["access_token"]
    created = await client.post("/admin/keys", json={"name": f"k-{tenant}"}, headers=_bearer(token))
    assert created.status_code == 201, created.text
    return {
        "token": token,
        "key": created.json()["key"],
        "tenant_id": signup.json()["tenant_id"],
    }


@pytest.fixture
async def tenant_a(client: Any) -> dict[str, str]:
    return await _signup(client, tenant="ConsoleA", email="console-a@example.io")


@pytest.fixture
async def tenant_b(client: Any) -> dict[str, str]:
    return await _signup(client, tenant="ConsoleB", email="console-b@example.io")


@pytest.fixture
async def active_model(db_session: Any) -> str:
    model_id = "openai/gpt-4o-console"
    await db_session.execute(
        text("INSERT INTO models (id, name, context_length, active) VALUES (:i, :n, 128000, true)"),
        {"i": model_id, "n": "GPT-4o console"},
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


# --- authoring via the /v1 API-key surface (launch is /v1-only) ------------------------------


async def _v1_make_set(client: Any, key: str, name: str) -> str:
    resp = await client.post("/v1/evals/sets", json={"name": name}, headers=_bearer(key))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _v1_add_case(client: Any, key: str, set_id: str, content: str) -> None:
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


async def _v1_launch(client: Any, key: str, set_id: str, model: str) -> str:
    resp = await client.post(
        f"/v1/evals/sets/{set_id}/runs", json={"model": model}, headers=_bearer(key)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------------------------------------


async def test_admin_evals_session_scoped_reads(
    client: Any,
    app: Any,
    tenant_a: dict[str, str],
    tenant_b: dict[str, str],
    active_model: str,
) -> None:
    """covers: M1, A1, A2, R:CROSS_TENANT, E5 — the session reads its OWN tenant; B's ids -> 404."""
    _install(app, ScriptedUpstream(prefix="echo"))
    key_a, tok_a = tenant_a["key"], tenant_a["token"]
    set_id = await _v1_make_set(client, key_a, "reads")
    await _v1_add_case(client, key_a, set_id, "one")
    run_id = await _v1_launch(client, key_a, set_id, active_model)

    # A member session lists its tenant's sets (A1) and sees this set.
    listing = await client.get("/admin/evals/sets", headers=_bearer(tok_a))
    assert listing.status_code == 200, listing.text
    assert set_id in {s["id"] for s in listing.json()["data"]}

    # Set detail carries cases + runs + baseline pointer.
    detail = await client.get(f"/admin/evals/sets/{set_id}", headers=_bearer(tok_a))
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert len(body["cases"]) == 1
    assert run_id in {r["id"] for r in body["runs"]}
    assert body["baseline_run_id"] is None

    # Verdict + per-case diff read through the session.
    assert (
        await client.get(f"/admin/evals/runs/{run_id}/verdict", headers=_bearer(tok_a))
    ).status_code == 200
    cases = await client.get(f"/admin/evals/runs/{run_id}/cases", headers=_bearer(tok_a))
    assert cases.status_code == 200, cases.text
    assert len(cases.json()["data"]) == 1

    # Tenant B's session cannot read A's set or run — uniform 404, and A's set is absent from B's list.
    tok_b = tenant_b["token"]
    b_list = await client.get("/admin/evals/sets", headers=_bearer(tok_b))
    assert set_id not in {s["id"] for s in b_list.json()["data"]}
    assert (
        await client.get(f"/admin/evals/sets/{set_id}", headers=_bearer(tok_b))
    ).status_code == 404
    assert (
        await client.get(f"/admin/evals/runs/{run_id}/verdict", headers=_bearer(tok_b))
    ).status_code == 404
    assert (
        await client.get(f"/admin/evals/runs/{run_id}/cases", headers=_bearer(tok_b))
    ).status_code == 404


async def test_admin_evals_verdict_matches_v1_reuse(
    client: Any, app: Any, tenant_a: dict[str, str], active_model: str
) -> None:
    """covers: M1, R:LOGIC_FORK — /admin verdict is byte-identical to /v1 for the same run."""
    _install(app, ScriptedUpstream(prefix="echo"))
    key_a, tok_a = tenant_a["key"], tenant_a["token"]
    set_id = await _v1_make_set(client, key_a, "reuse")
    await _v1_add_case(client, key_a, set_id, "one")
    baseline_run = await _v1_launch(client, key_a, set_id, active_model)
    candidate_run = await _v1_launch(client, key_a, set_id, active_model)

    pin = await client.put(
        f"/v1/evals/sets/{set_id}/baseline",
        json={"run_id": baseline_run},
        headers=_bearer(key_a),
    )
    assert pin.status_code == 200, pin.text

    v1 = await client.get(f"/v1/evals/runs/{candidate_run}/verdict", headers=_bearer(key_a))
    admin = await client.get(f"/admin/evals/runs/{candidate_run}/verdict", headers=_bearer(tok_a))
    assert v1.status_code == 200 and admin.status_code == 200, (v1.text, admin.text)
    # Same scoring core, same run -> identical verdict body (proves reuse, not a fork).
    assert admin.json() == v1.json()
    assert admin.json()["verdict"] in {"pass", "fail"}


async def test_admin_evals_authoring_session_writes(
    client: Any, app: Any, tenant_a: dict[str, str], active_model: str
) -> None:
    """covers: M2, A1 — POST set + POST case + PUT baseline all succeed via the session, tenant-scoped."""
    _install(app, ScriptedUpstream(prefix="echo"))
    tok_a, key_a = tenant_a["token"], tenant_a["key"]

    made = await client.post("/admin/evals/sets", json={"name": "authored"}, headers=_bearer(tok_a))
    assert made.status_code == 201, made.text
    set_id = made.json()["id"]

    cased = await client.post(
        f"/admin/evals/sets/{set_id}/cases",
        json={
            "request_body": {"model": "ignored", "messages": [{"role": "user", "content": "hi"}]},
            "assertion": {"kind": "contains", "expected": "echo"},
        },
        headers=_bearer(tok_a),
    )
    assert cased.status_code == 201, cased.text

    # Launch is /v1-only; use the key to produce a run, then pin it via the session.
    run_id = await _v1_launch(client, key_a, set_id, active_model)
    pinned = await client.put(
        f"/admin/evals/sets/{set_id}/baseline",
        json={"run_id": run_id},
        headers=_bearer(tok_a),
    )
    assert pinned.status_code == 200, pinned.text
    assert pinned.json()["baseline_run_id"] == run_id

    # The authored set is owned by tenant A (session tenant), and the pin landed.
    async with app.state.sessionmaker() as session:
        owner = (
            await session.execute(
                text("SELECT tenant_id FROM eval_sets WHERE id = :s"),
                {"s": uuid.UUID(set_id.removeprefix("es_"))},
            )
        ).scalar_one()
        assert str(owner) == tenant_a["tenant_id"]
        n = int(
            (
                await session.execute(
                    text("SELECT count(*) FROM eval_baselines WHERE eval_set_id = :s"),
                    {"s": uuid.UUID(set_id.removeprefix("es_"))},
                )
            ).scalar_one()
        )
        assert n == 1


def test_admin_evals_has_no_launch_and_no_raw_key() -> None:
    """covers: M2, R:RAW_KEY_IN_CONSOLE — no launch route; the module never touches a raw key."""
    from gateway.evals.console import router as console_mod

    routes = [
        r
        for r in console_mod.evals_console_router.routes
        if hasattr(r, "path") and hasattr(r, "methods")
    ]
    assert routes, "the console router must expose routes"
    # Every route is under /admin/evals — the session surface, never /v1.
    assert all(r.path.startswith("/admin/evals") for r in routes)  # type: ignore[attr-defined]
    # There is NO run-launch route (a POST to .../runs) — launch bills a live key and stays on /v1.
    for r in routes:
        methods = r.methods or set()  # type: ignore[attr-defined]
        assert not (
            "POST" in methods and r.path.rstrip("/").endswith("/runs")  # type: ignore[attr-defined]
        ), "the console must NOT launch runs (that needs a raw key)"
    # The console never imports the raw-key extractor, so it CANNOT dial an upstream / handle a
    # raw key — proven by its absence from the module namespace (a symbol you never imported you
    # cannot call). The docstring may name it to explain the absence; the code must not bind it.
    assert not hasattr(console_mod, "_extract_raw_key")
    # And no code line calls it or reads a raw_key (scan the SOURCE minus the module docstring).
    src = inspect.getsource(console_mod)
    doc = console_mod.__doc__ or ""
    code_only = src.replace(doc, "", 1)
    assert "_extract_raw_key" not in code_only
    assert "raw_key" not in code_only
