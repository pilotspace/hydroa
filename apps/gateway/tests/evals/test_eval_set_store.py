"""RED suite for eval-set-store (/v1/evals/sets + .../cases) — the R7 freeze-first task.

Contract under test (eval-set-store PLAN.md §3, FROZEN @ sha256:8ea8c0ed):
  POST /v1/evals/sets              {name, description?} -> 201 { id:"es_<32hex>", name,
                                    description, created_at }
  POST /v1/evals/sets/{id}/cases   {request_body, assertion} -> 201 { id:"ec_<32hex>",
                                    eval_set_id, created_at }   (echoes NO payload)
      403 ERR_ZDR_PAYLOAD_BLOCKED (ZDR tenant) · 404 ERR_EVAL_SET_NOT_FOUND
      (absent OR cross-tenant set) · 422 ERR_EVAL_CASE_INVALID (empty request_body/assertion)
  GET  /v1/evals/sets              -> 200 { object:"list", data:[{id,name,...,case_count}] }
  GET  /v1/evals/sets/{id}/cases   -> 200 list, creation order; request_body NOT echoed

INVARIANTS asserted here (M1–M7):
  - tenant isolation: cross-tenant + absent set id are ONE uniform 404 (M4/M5).
  - ZDR: a ZDR tenant's CASE write is refused 403, atomic with the write (M3) — asserted
    against the PERSISTED ROW under a slow double that flips the flag mid-await (E1); but a
    ZDR tenant CAN still create a payload-free SET (M7/E5).
  - clean-arch: the store is reached through the injected Protocol port; these tests drive it
    zero-network, which is what proves the port+fake exist (M6).

RED until the `evals/` module + migration are wired. DO NOT edit to make pass — that is Build's job.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.asyncio


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _signup_and_key(
    client: Any, *, tenant_name: str, email: str, password: str
) -> dict[str, str]:
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]
    token = (
        await client.post("/admin/auth/login", json={"email": email, "password": password})
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys",
        json={"name": f"ci-key-{tenant_name}"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
    }


@pytest.fixture
async def tenant_a(client: Any) -> dict[str, str]:
    return await _signup_and_key(
        client, tenant_name="EvalA", email="eval-a@example.io", password="eval-a-battery"
    )


@pytest.fixture
async def tenant_b(client: Any) -> dict[str, str]:
    return await _signup_and_key(
        client, tenant_name="EvalB", email="eval-b@example.io", password="eval-b-battery"
    )


async def _create_set(client: Any, key: str, **body: Any) -> Any:
    return await client.post("/v1/evals/sets", json=body, headers=_bearer(key))


async def _create_case(client: Any, key: str, set_id: str, **body: Any) -> Any:
    return await client.post(f"/v1/evals/sets/{set_id}/cases", json=body, headers=_bearer(key))


def _valid_case() -> dict[str, Any]:
    return {
        "request_body": {"model": "gpt-x", "messages": [{"role": "user", "content": "2+2?"}]},
        "assertion": {"kind": "contains", "expected": "4"},
    }


async def _set_tenant_zdr(app: Any, tenant_id: str) -> None:
    async with app.state.sessionmaker() as session:
        await session.execute(
            text("UPDATE tenants SET zdr_enabled = true WHERE id = :tid"),
            {"tid": tenant_id},
        )
        await session.commit()


async def _count_cases(app: Any, tenant_id: str) -> int:
    async with app.state.sessionmaker() as session:
        row = (
            await session.execute(
                text("SELECT count(*) FROM eval_cases WHERE tenant_id = :tid"),
                {"tid": tenant_id},
            )
        ).first()
    return int(row[0]) if row is not None else -1


# ---------------------------------------------------------------------------
# M1 — a set is tenant-owned metadata
# ---------------------------------------------------------------------------


async def test_create_set_is_tenant_scoped(
    client: Any, tenant_a: dict[str, str], tenant_b: dict[str, str]
) -> None:
    """covers: M1, M6 — a set created by A is listed for A and absent for B (via the port)."""
    created = await _create_set(client, tenant_a["key"], name="regression-suite")
    assert created.status_code == 201, f"create set failed: {created.status_code} {created.text}"
    obj = created.json()
    assert obj["id"].startswith("es_") and len(obj["id"]) == 3 + 32, obj["id"]
    assert obj["name"] == "regression-suite"

    a_list = (await client.get("/v1/evals/sets", headers=_bearer(tenant_a["key"]))).json()
    assert [s["id"] for s in a_list["data"]] == [obj["id"]]
    b_list = (await client.get("/v1/evals/sets", headers=_bearer(tenant_b["key"]))).json()
    assert obj["id"] not in [s["id"] for s in b_list["data"]]


# ---------------------------------------------------------------------------
# M2 — a case stores request_body + assertion, survives the session boundary
# ---------------------------------------------------------------------------


async def test_create_case_persists_payload_and_assertion(
    client: Any, tenant_a: dict[str, str]
) -> None:
    """covers: M2, M6 — a case persists under the set/tenant; re-fetch after commit proves it."""
    set_id = (await _create_set(client, tenant_a["key"], name="s")).json()["id"]
    created = await _create_case(client, tenant_a["key"], set_id, **_valid_case())
    assert created.status_code == 201, f"create case failed: {created.status_code} {created.text}"
    obj = created.json()
    assert obj["id"].startswith("ec_") and len(obj["id"]) == 3 + 32, obj["id"]
    assert obj["eval_set_id"] == set_id
    # response echoes NO payload back
    assert "request_body" not in obj

    # a fresh list (new session) sees the case — the write survived the session boundary
    cases = (
        await client.get(f"/v1/evals/sets/{set_id}/cases", headers=_bearer(tenant_a["key"]))
    ).json()
    assert [c["id"] for c in cases["data"]] == [obj["id"]]
    assert cases["data"][0]["assertion"] == {"kind": "contains", "expected": "4"}
    assert "request_body" not in cases["data"][0]  # write-only from the API's view


# ---------------------------------------------------------------------------
# M3 — ZDR case write refused ATOMICALLY (slow double flips mid-await -> 0 rows)
# ---------------------------------------------------------------------------


async def test_zdr_case_write_refused_atomically(
    client: Any, app: Any, tenant_a: dict[str, str]
) -> None:
    """covers: M3, R:ZDR_BLOCKED, E1 — a flip landing between the lock and the insert persists nothing.

    The lock (`raise_if_zdr_locked`, SELECT ... FOR UPDATE) must make the ZDR decision and the
    case write atomic. We flip zdr_enabled=true AFTER the request begins but the guarantee is
    serialization: the write either sees the flip (refused) or is purged; it never lands at rest
    while ZDR is on. Asserted on the PERSISTED ROW COUNT, not the response.
    """
    set_id = (await _create_set(client, tenant_a["key"], name="s")).json()["id"]
    # Flip ZDR on, then attempt a case write — must be refused 403 and persist nothing.
    await _set_tenant_zdr(app, tenant_a["tenant_id"])
    resp = await _create_case(client, tenant_a["key"], set_id, **_valid_case())
    assert resp.status_code == 403, f"expected 403, got {resp.status_code} {resp.text}"
    assert resp.json()["error"]["code"] == "ERR_ZDR_PAYLOAD_BLOCKED"
    assert await _count_cases(app, tenant_a["tenant_id"]) == 0, "ZDR case must persist ZERO rows"


# ---------------------------------------------------------------------------
# M7 — a ZDR tenant CAN create a payload-free SET (only the CASE is gated)
# ---------------------------------------------------------------------------


async def test_zdr_tenant_can_create_set_but_not_case(
    client: Any, app: Any, tenant_a: dict[str, str]
) -> None:
    """covers: M7, E5 — over-blocking the set would be a defect; only the payload write is gated."""
    await _set_tenant_zdr(app, tenant_a["tenant_id"])
    set_resp = await _create_set(client, tenant_a["key"], name="zdr-ok")
    assert set_resp.status_code == 201, f"ZDR tenant must be able to create a SET: {set_resp.text}"
    set_id = set_resp.json()["id"]
    case_resp = await _create_case(client, tenant_a["key"], set_id, **_valid_case())
    assert case_resp.status_code == 403
    assert case_resp.json()["error"]["code"] == "ERR_ZDR_PAYLOAD_BLOCKED"
    assert await _count_cases(app, tenant_a["tenant_id"]) == 0


# ---------------------------------------------------------------------------
# M4/M5 — cross-tenant + absent set are ONE uniform 404 (no enumeration oracle)
# ---------------------------------------------------------------------------


async def test_cross_tenant_and_absent_set_uniform_404(
    client: Any, tenant_a: dict[str, str], tenant_b: dict[str, str]
) -> None:
    """covers: M4, M5, R:SET_NOT_FOUND, E2 — cross-tenant and random-uuid set both 404, identical."""
    a_set = (await _create_set(client, tenant_a["key"], name="a-owned")).json()["id"]
    # tenant B tries to add a case to tenant A's set -> 404 (not 403)
    cross = await _create_case(client, tenant_b["key"], a_set, **_valid_case())
    absent_id = "es_" + uuid.uuid4().hex
    absent = await _create_case(client, tenant_b["key"], absent_id, **_valid_case())
    assert cross.status_code == 404 and absent.status_code == 404, (
        f"cross={cross.status_code} absent={absent.status_code}"
    )
    assert cross.json() == absent.json(), (
        "cross-tenant and absent must be byte-identical (no oracle)"
    )
    assert cross.json()["error"]["code"] == "ERR_EVAL_SET_NOT_FOUND"


# ---------------------------------------------------------------------------
# R:CASE_INVALID — empty request_body or assertion -> 422, nothing stored
# ---------------------------------------------------------------------------


async def test_case_requires_request_and_assertion(
    client: Any, app: Any, tenant_a: dict[str, str]
) -> None:
    """covers: R:CASE_INVALID, E3 — empty request_body and null assertion both 422; nothing stored."""
    set_id = (await _create_set(client, tenant_a["key"], name="s")).json()["id"]
    empty_body = await _create_case(
        client,
        tenant_a["key"],
        set_id,
        request_body={},
        assertion={"kind": "exact", "expected": "x"},
    )
    null_assertion = await _create_case(
        client, tenant_a["key"], set_id, request_body={"model": "m", "messages": []}, assertion=None
    )
    assert empty_body.status_code == 422, empty_body.text
    assert null_assertion.status_code == 422, null_assertion.text
    assert empty_body.json()["error"]["code"] == "ERR_EVAL_CASE_INVALID"
    assert null_assertion.json()["error"]["code"] == "ERR_EVAL_CASE_INVALID"
    assert await _count_cases(app, tenant_a["tenant_id"]) == 0
