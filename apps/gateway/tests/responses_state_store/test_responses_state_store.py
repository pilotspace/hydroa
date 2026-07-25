"""Red suite for responses-state-store (PLAN.md §4 — contract DRAFT, freeze pending).

One test per §1 Must/Reject + the mandatory adversarial cases: cross-tenant id
probing, chaining onto another tenant's id, deleted-response chaining, oversized
chains, concurrent delete-while-chaining, ZDR fail-closed 403, cascade DELETE,
sweeper composition (Tin's recorded decisions 2026-07-24: ZDR loud 403; cascade).

Red-for-the-right-reason: today POST/GET/DELETE /v1/responses* is absent (or, once
responses-api-core lands, store/previous_response_id still terminate 400
ERR_RESPONSES_STORE_UNSUPPORTED) and the stored_responses table does not exist.
Every 404 assertion checks the problem+json ``code`` so FastAPI's route-absent
``{"detail": "Not Found"}`` can never vacuously satisfy an isolation test.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.responses_store.infrastructure.repository import StoredResponseRepository

from .conftest import (
    RESPONSES,
    UNKNOWN_RESPONSE_ID,
    FakeCompletionUpstream,
    FakeUsageRecorder,
    auth_bearer,
    count_stored_rows,
    fetch_stored_row,
    parse_named_sse,
    responses_payload,
    seed_stored_row,
    set_zdr,
)


def _inject(app: Any, upstream: FakeCompletionUpstream) -> FakeUsageRecorder:
    app.state.completion_upstream = upstream
    recorder = FakeUsageRecorder()
    app.state.usage_recorder = recorder
    return recorder


def _assert_problem_code(resp: httpx.Response, status: int, code: str) -> None:
    assert resp.status_code == status, f"expected {status}, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("code") == code, f"expected code {code}, got: {body}"


async def _store_one(
    client: httpx.AsyncClient,
    api_key: dict[str, str],
    model: str,
    *,
    input_: Any = "remember the launch code is 4711",
    **extra: Any,
) -> dict[str, Any]:
    resp = await client.post(
        RESPONSES,
        json=responses_payload(model, input_=input_, store=True, **extra),
        headers=auth_bearer(api_key["key"]),
    )
    assert resp.status_code == 200, f"store POST failed: {resp.status_code} {resp.text}"
    return resp.json()


# ===========================================================================
# M1 / M9 — persistence on store:true, byte-identical default path
# ===========================================================================


async def test_store_true_persists_row_and_echoes(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
    db_session: AsyncSession,
) -> None:
    upstream = FakeCompletionUpstream()
    recorder = _inject(app, upstream)

    body = await _store_one(client, api_key, active_model)

    assert body["store"] is True
    assert body["id"].startswith("resp_")
    row = await fetch_stored_row(db_session, body["id"])
    assert row is not None, "store:true must persist a stored_responses row"
    assert str(row.tenant_id) == api_key["tenant_id"]
    assert str(row.key_id) == api_key["key_id"]
    assert row.model == active_model
    assert row.context_messages is not None, "context_messages must hold the upstream messages"
    assert row.response_body is not None, "response_body must hold the full Response"
    assert len(recorder.records) == 1, "storing never adds a second usage record"


async def test_store_absent_writes_nothing_default_path(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
    db_session: AsyncSession,
) -> None:
    upstream = FakeCompletionUpstream()
    _inject(app, upstream)

    resp = await client.post(
        RESPONSES, json=responses_payload(active_model), headers=auth_bearer(api_key["key"])
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["store"] is False
    assert await count_stored_rows(db_session) == 0, "default path must write ZERO state rows"


# ===========================================================================
# M2 — streaming store persists before terminal response.completed
# ===========================================================================


async def test_store_true_streaming_persists_before_completed(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
    db_session: AsyncSession,
) -> None:
    upstream = FakeCompletionUpstream()
    _inject(app, upstream)

    resp = await client.post(
        RESPONSES,
        json=responses_payload(active_model, store=True, stream=True),
        headers=auth_bearer(api_key["key"]),
    )
    assert resp.status_code == 200, resp.text
    frames = parse_named_sse(resp.content)
    event_names = [name for name, _ in frames]
    assert "response.completed" in event_names, f"no terminal completed frame: {event_names}"
    terminal = next(data for name, data in frames if name == "response.completed")
    resp_id = terminal["response"]["id"]

    row = await fetch_stored_row(db_session, resp_id)
    assert row is not None, "stream store:true must persist the row by stream end"
    assert row.response_body is not None


# ===========================================================================
# M3 / M4 — GET and DELETE lifecycle, zero usage records
# ===========================================================================


async def test_get_returns_stored_response(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
) -> None:
    upstream = FakeCompletionUpstream()
    recorder = _inject(app, upstream)
    posted = await _store_one(client, api_key, active_model)

    got = await client.get(f"{RESPONSES}/{posted['id']}", headers=auth_bearer(api_key["key"]))

    assert got.status_code == 200, got.text
    body = got.json()
    assert body["id"] == posted["id"]
    assert body["output"] == posted["output"]
    assert body["usage"] == posted["usage"]
    assert len(recorder.records) == 1, "GET must write NO usage record (no provider cost)"


async def test_delete_cascades_descendants_then_get_404(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
    db_session: AsyncSession,
) -> None:
    """Tin's recorded decision 2026-07-24: DELETE removes the row AND every
    descendant in its chain (tenant-scoped, one transaction)."""
    upstream = FakeCompletionUpstream()
    recorder = _inject(app, upstream)
    turn1 = await _store_one(client, api_key, active_model, input_="turn one")
    turn2 = await _store_one(
        client, api_key, active_model, input_="turn two", previous_response_id=turn1["id"]
    )
    headers = auth_bearer(api_key["key"])

    deleted = await client.delete(f"{RESPONSES}/{turn1['id']}", headers=headers)

    assert deleted.status_code == 200, deleted.text
    dbody = deleted.json()
    assert dbody == {"id": turn1["id"], "object": "response.deleted", "deleted": True}, (
        "pinned OpenAI wire shape — no count field"
    )
    assert await fetch_stored_row(db_session, turn1["id"]) is None, "root must be HARD-deleted"
    assert await fetch_stored_row(db_session, turn2["id"]) is None, (
        "descendant must be cascade-deleted in the same transaction"
    )
    _assert_problem_code(
        await client.get(f"{RESPONSES}/{turn1['id']}", headers=headers),
        404,
        "ERR_RESPONSE_NOT_FOUND",
    )
    _assert_problem_code(
        await client.get(f"{RESPONSES}/{turn2['id']}", headers=headers),
        404,
        "ERR_RESPONSE_NOT_FOUND",
    )
    _assert_problem_code(
        await client.delete(f"{RESPONSES}/{turn1['id']}", headers=headers),
        404,
        "ERR_RESPONSE_NOT_FOUND",
    )
    assert len(recorder.records) == 2, "DELETE/GET must write NO usage records (2 stores only)"


# ===========================================================================
# M5 — chaining materializes prior context in order
# ===========================================================================


async def test_chaining_materializes_context_in_order(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
) -> None:
    upstream = FakeCompletionUpstream()
    _inject(app, upstream)
    turn1 = await _store_one(client, api_key, active_model, input_="my name is Ada")

    resp = await client.post(
        RESPONSES,
        json=responses_payload(
            active_model, input_="what is my name?", previous_response_id=turn1["id"]
        ),
        headers=auth_bearer(api_key["key"]),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["previous_response_id"] == turn1["id"]
    assert upstream.last_payload is not None
    messages = upstream.last_payload["messages"]
    contents = [m.get("content") for m in messages]
    # Order: turn-1 user input, turn-1 assistant output, new user input.
    assert "my name is Ada" in contents, f"turn-1 input missing from context: {contents}"
    assert "Hello there!" in contents, f"turn-1 assistant output missing: {contents}"
    assert "what is my name?" in contents
    assert contents.index("my name is Ada") < contents.index("Hello there!") < contents.index(
        "what is my name?"
    ), f"materialized context out of order: {contents}"


async def test_chain_with_store_false_reads_but_persists_nothing(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
    db_session: AsyncSession,
) -> None:
    upstream = FakeCompletionUpstream()
    _inject(app, upstream)
    turn1 = await _store_one(client, api_key, active_model)

    resp = await client.post(
        RESPONSES,
        json=responses_payload(
            active_model, input_="continue", previous_response_id=turn1["id"], store=False
        ),
        headers=auth_bearer(api_key["key"]),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["store"] is False
    assert await count_stored_rows(db_session) == 1, "chaining alone must not persist a new row"


# ===========================================================================
# M6 — adversarial: cross-tenant probing (GET / DELETE / chain), enumeration
# ===========================================================================


async def test_cross_tenant_get_and_delete_404_byte_identical(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    api_key_b: dict[str, str],
    active_model: str,
    db_session: AsyncSession,
) -> None:
    upstream = FakeCompletionUpstream()
    _inject(app, upstream)
    victim = await _store_one(client, api_key, active_model)
    attacker = auth_bearer(api_key_b["key"])

    got_cross = await client.get(f"{RESPONSES}/{victim['id']}", headers=attacker)
    got_unknown = await client.get(f"{RESPONSES}/{UNKNOWN_RESPONSE_ID}", headers=attacker)
    del_cross = await client.delete(f"{RESPONSES}/{victim['id']}", headers=attacker)
    del_unknown = await client.delete(f"{RESPONSES}/{UNKNOWN_RESPONSE_ID}", headers=attacker)

    _assert_problem_code(got_cross, 404, "ERR_RESPONSE_NOT_FOUND")
    _assert_problem_code(del_cross, 404, "ERR_RESPONSE_NOT_FOUND")
    # Byte-identical: an existing-but-foreign id must be indistinguishable from
    # a never-existed id — status AND body (enumeration-oracle closure).
    assert got_cross.status_code == got_unknown.status_code
    assert got_cross.content == got_unknown.content
    assert del_cross.status_code == del_unknown.status_code
    assert del_cross.content == del_unknown.content
    # The victim row survives the attacker's DELETE.
    assert await fetch_stored_row(db_session, victim["id"]) is not None
    owner_get = await client.get(f"{RESPONSES}/{victim['id']}", headers=auth_bearer(api_key["key"]))
    assert owner_get.status_code == 200


async def test_cross_tenant_chain_probe_404_no_dial(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    api_key_b: dict[str, str],
    active_model: str,
) -> None:
    upstream = FakeCompletionUpstream()
    _inject(app, upstream)
    victim = await _store_one(client, api_key, active_model)
    calls_before = upstream.calls
    recorder = _inject(app, FakeCompletionUpstream())  # fresh recorder for the attacker act
    attacker_upstream: FakeCompletionUpstream = app.state.completion_upstream

    cross = await client.post(
        RESPONSES,
        json=responses_payload(active_model, previous_response_id=victim["id"]),
        headers=auth_bearer(api_key_b["key"]),
    )
    unknown = await client.post(
        RESPONSES,
        json=responses_payload(active_model, previous_response_id=UNKNOWN_RESPONSE_ID),
        headers=auth_bearer(api_key_b["key"]),
    )

    _assert_problem_code(cross, 404, "ERR_RESPONSE_NOT_FOUND")
    assert cross.status_code == unknown.status_code
    assert cross.content == unknown.content, "chain probe must not distinguish foreign vs unknown"
    assert attacker_upstream.calls == 0, "cross-tenant chain must reject PRE-dial"
    assert recorder.records == [], "no usage row on a rejected chain probe"
    assert calls_before == 1  # victim's own store round-trip only


async def test_cross_tenant_capped_row_still_404(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    api_key_b: dict[str, str],
    active_model: str,
    db_session: AsyncSession,
) -> None:
    """Evaluation-order oracle closure: a foreign id must terminate 404 BEFORE the
    depth check — tenant B must never learn that A's row is depth-capped (400).
    A tenant-last implementation fails exactly here."""
    recorder = _inject(app, FakeCompletionUpstream())
    upstream: FakeCompletionUpstream = app.state.completion_upstream
    deep_id = "resp_a11ce5a11ce5a11ce5a11ce5"
    await seed_stored_row(
        db_session,
        response_id=deep_id,
        tenant_id=api_key["tenant_id"],
        key_id=api_key["key_id"],
        chain_depth=64,
        context_messages=[{"role": "user", "content": "x"}],
        response_body={"id": deep_id, "object": "response", "output": []},
    )
    attacker = auth_bearer(api_key_b["key"])

    on_capped = await client.post(
        RESPONSES,
        json=responses_payload(active_model, previous_response_id=deep_id),
        headers=attacker,
    )
    on_unknown = await client.post(
        RESPONSES,
        json=responses_payload(active_model, previous_response_id=UNKNOWN_RESPONSE_ID),
        headers=attacker,
    )

    _assert_problem_code(on_capped, 404, "ERR_RESPONSE_NOT_FOUND")
    assert on_capped.content == on_unknown.content, "capped foreign row leaked (not 404-identical)"
    assert upstream.calls == 0
    assert recorder.records == []


async def test_chained_store_increments_depth_and_accumulates(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
    db_session: AsyncSession,
) -> None:
    """A build that never increments chain_depth makes the 64-cap vacuous (unbounded
    chains, unbounded O(chain²) storage)."""
    _inject(app, FakeCompletionUpstream())
    turn1 = await _store_one(client, api_key, active_model, input_="my name is Ada")

    turn2 = await _store_one(
        client,
        api_key,
        active_model,
        input_="what is my name?",
        previous_response_id=turn1["id"],
    )

    row1 = await fetch_stored_row(db_session, turn1["id"])
    row2 = await fetch_stored_row(db_session, turn2["id"])
    assert row1 is not None and row2 is not None
    assert row1.chain_depth == 0
    assert row2.chain_depth == 1, "chained store must increment chain_depth (cap vacuity guard)"
    assert row2.previous_response_id == turn1["id"]
    ctx = row2.context_messages
    ctx_text = ctx if isinstance(ctx, str) else json.dumps(ctx)
    assert "my name is Ada" in ctx_text, "turn-2 context must accumulate turn-1 input"
    assert "Hello there!" in ctx_text, "turn-2 context must accumulate turn-1 output"


async def test_deleted_and_malformed_prev_id_404_no_dial(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
) -> None:
    upstream = FakeCompletionUpstream()
    _inject(app, upstream)
    turn1 = await _store_one(client, api_key, active_model)
    deleted = await client.delete(f"{RESPONSES}/{turn1['id']}", headers=auth_bearer(api_key["key"]))
    assert deleted.status_code == 200
    recorder = _inject(app, FakeCompletionUpstream())
    fresh_upstream: FakeCompletionUpstream = app.state.completion_upstream

    on_deleted = await client.post(
        RESPONSES,
        json=responses_payload(active_model, previous_response_id=turn1["id"]),
        headers=auth_bearer(api_key["key"]),
    )
    on_malformed = await client.post(
        RESPONSES,
        json=responses_payload(active_model, previous_response_id="not-a-resp-id"),
        headers=auth_bearer(api_key["key"]),
    )

    _assert_problem_code(on_deleted, 404, "ERR_RESPONSE_NOT_FOUND")
    _assert_problem_code(on_malformed, 404, "ERR_RESPONSE_NOT_FOUND")
    assert on_deleted.content == on_malformed.content, "deleted vs malformed must be identical"
    assert fresh_upstream.calls == 0
    assert recorder.records == []


# ===========================================================================
# R — chain caps (depth, materialized size), pre-dial
# ===========================================================================


async def test_chain_depth_cap_rejects_pre_dial(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
    db_session: AsyncSession,
) -> None:
    recorder = _inject(app, FakeCompletionUpstream())
    upstream: FakeCompletionUpstream = app.state.completion_upstream
    deep_id = "resp_deadbeefdeadbeefdeadbeef"
    await seed_stored_row(
        db_session,
        response_id=deep_id,
        tenant_id=api_key["tenant_id"],
        key_id=api_key["key_id"],
        chain_depth=64,
        context_messages=[{"role": "user", "content": "x"}],
        response_body={"id": deep_id, "object": "response", "output": []},
    )

    resp = await client.post(
        RESPONSES,
        json=responses_payload(active_model, previous_response_id=deep_id),
        headers=auth_bearer(api_key["key"]),
    )

    _assert_problem_code(resp, 400, "ERR_RESPONSES_CHAIN_TOO_DEEP")
    assert upstream.calls == 0
    assert recorder.records == []


async def test_context_size_cap_rejects_pre_dial(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
    db_session: AsyncSession,
) -> None:
    recorder = _inject(app, FakeCompletionUpstream())
    upstream: FakeCompletionUpstream = app.state.completion_upstream
    big_id = "resp_b16b00b5b16b00b5b16b00b5"
    oversized = [{"role": "user", "content": "A" * (1024 * 1024 + 1)}]  # > 1 MiB serialized
    await seed_stored_row(
        db_session,
        response_id=big_id,
        tenant_id=api_key["tenant_id"],
        key_id=api_key["key_id"],
        context_messages=oversized,
        response_body={"id": big_id, "object": "response", "output": []},
    )

    resp = await client.post(
        RESPONSES,
        json=responses_payload(active_model, previous_response_id=big_id),
        headers=auth_bearer(api_key["key"]),
    )

    _assert_problem_code(resp, 413, "ERR_RESPONSES_CONTEXT_TOO_LARGE")
    assert upstream.calls == 0
    assert recorder.records == []


# ===========================================================================
# M7 — ZDR: loud fail-closed 403 (Tin's recorded decision 2026-07-24)
# ===========================================================================


async def test_zdr_store_true_403_fail_closed(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
    db_session: AsyncSession,
) -> None:
    """ZDR tenant + store:true → the WHOLE request is rejected 403 PRE-dial with the
    repo-wide ZDR refusal: zero provider cost, zero usage rows, zero stored rows."""
    recorder = _inject(app, FakeCompletionUpstream())
    upstream: FakeCompletionUpstream = app.state.completion_upstream
    await set_zdr(db_session, api_key["tenant_id"], True)

    resp = await client.post(
        RESPONSES,
        json=responses_payload(active_model, input_="PII-laden payload", store=True),
        headers=auth_bearer(api_key["key"]),
    )

    _assert_problem_code(resp, 403, "ERR_ZDR_PAYLOAD_BLOCKED")
    assert upstream.calls == 0, "the completion must NOT execute (loud, pre-dial)"
    assert recorder.records == [], "no provider cost, no usage row"
    assert await count_stored_rows(db_session) == 0, "nothing persisted"


async def test_zdr_store_false_still_serves_stateless(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
    db_session: AsyncSession,
) -> None:
    """The SAME ZDR tenant's stateless request (no payload at rest) serves normally —
    loud only where payload would rest."""
    recorder = _inject(app, FakeCompletionUpstream())
    await set_zdr(db_session, api_key["tenant_id"], True)

    resp = await client.post(
        RESPONSES,
        json=responses_payload(active_model),
        headers=auth_bearer(api_key["key"]),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["store"] is False
    assert len(recorder.records) == 1, "underlying completion billing unaffected"
    assert await count_stored_rows(db_session) == 0


# ===========================================================================
# M8 — retention sweeper composition (window delete + ZDR row-delete)
# ===========================================================================


class _SweepSettings:
    retention_check_interval_seconds = 60
    retention_usage_records_days = 0
    retention_alert_events_days = 0
    retention_audit_events_days = 0
    retention_audit_floor_days = 0
    retention_batch_size = 1000
    retention_request_logs_days = 0
    retention_tenant_window_ceiling_days = 365


async def _sweep(db_session: AsyncSession) -> None:
    from gateway.usage.application.retention_sweep import RetentionSweeper

    factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    sweeper = RetentionSweeper(session_factory=factory, settings=_SweepSettings())
    await sweeper.sweep_once()


async def test_sweeper_zdr_pass_deletes_rows(
    api_key: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Flip-ZDR-on-later: pre-existing stored rows are row-DELETED by the ZDR purge
    pass (the conversations pattern), not scrubbed — no payload-NULL state exists."""
    rid = "resp_5c4bbedf5c4bbedf5c4bbedf"
    await seed_stored_row(
        db_session,
        response_id=rid,
        tenant_id=api_key["tenant_id"],
        key_id=api_key["key_id"],
        context_messages=[{"role": "user", "content": "sensitive"}],
        response_body={"id": rid, "object": "response", "output": ["payload"]},
    )
    await set_zdr(db_session, api_key["tenant_id"], True)

    await _sweep(db_session)

    row = await fetch_stored_row(db_session, rid)
    assert row is None, "ZDR purge pass must row-delete a zdr tenant's stored responses"


async def test_sweeper_window_pass_deletes_aged_rows(
    api_key: dict[str, str],
    db_session: AsyncSession,
) -> None:
    old_id = "resp_01d01d01d01d01d01d01d01d"
    new_id = "resp_new0new0new0new0new0new0"
    for rid, age in ((old_id, 400), (new_id, 0)):
        await seed_stored_row(
            db_session,
            response_id=rid,
            tenant_id=api_key["tenant_id"],
            key_id=api_key["key_id"],
            context_messages=[{"role": "user", "content": "x"}],
            response_body={"id": rid, "object": "response", "output": []},
            age_days=age,
        )

    await _sweep(db_session)

    assert await fetch_stored_row(db_session, old_id) is None, "aged row must be window-swept"
    assert await fetch_stored_row(db_session, new_id) is not None, "fresh row must survive"


# ===========================================================================
# Adversarial concurrency — delete racing a chain read
# ===========================================================================


async def test_concurrent_delete_while_chaining_is_atomic(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
) -> None:
    _inject(app, FakeCompletionUpstream())
    headers = auth_bearer(api_key["key"])

    for _ in range(5):
        target = await _store_one(client, api_key, active_model)
        recorder = _inject(app, FakeCompletionUpstream())

        chain_task = client.post(
            RESPONSES,
            json=responses_payload(active_model, previous_response_id=target["id"]),
            headers=headers,
        )
        delete_task = client.delete(f"{RESPONSES}/{target['id']}", headers=headers)
        chain_resp, delete_resp = await asyncio.gather(chain_task, delete_task)

        assert delete_resp.status_code in (200, 404), delete_resp.text
        assert chain_resp.status_code in (200, 404), (
            f"race must resolve to served-or-404, got {chain_resp.status_code}: {chain_resp.text}"
        )
        if chain_resp.status_code == 404:
            assert chain_resp.json().get("code") == "ERR_RESPONSE_NOT_FOUND"
            assert recorder.records == [], "a 404'd race loser must bill nothing"
        else:
            assert chain_resp.json()["previous_response_id"] == target["id"]
            assert len(recorder.records) == 1
        # Cascade completed regardless of race winner: the target row is gone.
        final_get = await client.get(f"{RESPONSES}/{target['id']}", headers=headers)
        _assert_problem_code(final_get, 404, "ERR_RESPONSE_NOT_FOUND")


# ===========================================================================
# Auth floor — GET/DELETE unauthenticated
# ===========================================================================


async def test_get_delete_unauthenticated_401(
    client: httpx.AsyncClient,
    app: Any,
) -> None:
    _inject(app, FakeCompletionUpstream())

    got_naked = await client.get(f"{RESPONSES}/{UNKNOWN_RESPONSE_ID}")
    got_garbage = await client.get(
        f"{RESPONSES}/{UNKNOWN_RESPONSE_ID}", headers=auth_bearer("sk-garbage")
    )
    del_naked = await client.delete(f"{RESPONSES}/{UNKNOWN_RESPONSE_ID}")

    for resp in (got_naked, got_garbage, del_naked):
        _assert_problem_code(resp, 401, "ERR_AUTH_INVALID_KEY")


# ===========================================================================
# M1 / M2 durability — store-persist FAILURE after the completion was served
# (PLAN.md §1 M1/M2, §3 Contract 500 ERR_RESPONSES_STORE_FAILED, prose build-
# guidance: "ERR_RESPONSES_STORE_FAILED (500 post-serve persist failure, non-
# stream) and the streaming persist-failure → response.failed branch (M2) are
# contract-named but not red-gated — they need DB-fault injection"). These two
# tests close that MEDIUM coverage gap (verify B, 2026-07-24). The fault is
# injected at the repository seam — StoredResponseRepository.create raises —
# so the REAL persistence orchestration (own-session open, router/stream try/
# except → contracted terminal) runs; only the DB write is faulted.
# ===========================================================================


def _fault_store_create(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every stored_responses INSERT fail (simulated durability fault).

    Patches the repository method the persist orchestration actually calls, so
    persist_stored_response opens its own session, constructs the repo, then the
    write raises — exercising the contracted post-serve failure handling, not a
    stubbed-away persist. Nothing is committed (the raise precedes flush), so no
    partial row can survive.
    """

    async def _boom(self: StoredResponseRepository, **_kwargs: Any) -> None:
        raise RuntimeError("injected stored_responses persistence fault")

    monkeypatch.setattr(StoredResponseRepository, "create", _boom)


async def _poll_min_records(  # noqa: ASYNC109 — test-helper polling budget, not a
    # cancellation-scope parameter; the rule targets library APIs that should take a
    # deadline from the caller's scope instead.
    recorder: FakeUsageRecorder,
    minimum: int = 1,
    timeout: float = 5.0,  # noqa: ASYNC109
) -> None:
    """Poll until the fire-and-forget usage task lands (fixed-sleep-flake lesson)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if len(recorder.records) >= minimum:
            return
        await asyncio.sleep(0.02)
    raise AssertionError(
        f"usage record never landed within {timeout}s (have {len(recorder.records)})"
    )


async def test_nonstream_store_failure_500_usage_row_stands(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M1 durability: a persist failure AFTER the non-stream completion was served
    surfaces 500 ERR_RESPONSES_STORE_FAILED; the one usage row recorded for the
    served completion STANDS (provider was billed) and ZERO stored rows exist
    (the failed insert never committed — nothing half-written)."""
    upstream = FakeCompletionUpstream()
    recorder = _inject(app, upstream)
    _fault_store_create(monkeypatch)

    resp = await client.post(
        RESPONSES,
        json=responses_payload(active_model, input_="serve me then fail to persist", store=True),
        headers=auth_bearer(api_key["key"]),
    )

    # Contracted terminal — a bare re-raised exception (no mapping) would NOT carry
    # this code, so the assert is non-vacuous.
    _assert_problem_code(resp, 500, "ERR_RESPONSES_STORE_FAILED")
    # The provider WAS dialed and served — its cost is real, its usage row stands.
    assert upstream.calls == 1, "the completion was served before the persist failed"
    assert len(recorder.records) == 1, "the served completion's usage row must STAND (billed)"
    # Nothing half-written: the failed insert committed no row.
    assert await count_stored_rows(db_session) == 0, "a failed persist must leave zero rows"
    # No payload leakage into the error body (v22 no-payload-in-traceback floor).
    assert "serve me then fail to persist" not in resp.text


async def test_streaming_store_failure_emits_response_failed_not_completed(
    client: httpx.AsyncClient,
    app: Any,
    api_key: dict[str, str],
    active_model: str,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """M2 durability: a persist failure at the terminal frame emits a terminal
    response.failed carrying ERR_RESPONSES_STORE_FAILED INSTEAD of a fabricated
    response.completed; the usage row recorded inside use_case.stream() stands and
    no stored row is written."""
    upstream = FakeCompletionUpstream()
    recorder = _inject(app, upstream)
    _fault_store_create(monkeypatch)

    resp = await client.post(
        RESPONSES,
        json=responses_payload(active_model, input_="stream then fail", store=True, stream=True),
        headers=auth_bearer(api_key["key"]),
    )

    assert resp.status_code == 200, resp.text  # stream opened before the terminal fault
    frames = parse_named_sse(resp.content)
    event_names = [name for name, _ in frames]
    # No fabricated success — the terminal is response.failed, response.completed absent.
    assert "response.completed" not in event_names, (
        f"a failed persist must NEVER emit a fabricated completion: {event_names}"
    )
    assert "response.failed" in event_names, f"missing terminal response.failed: {event_names}"
    failed = next(data for name, data in frames if name == "response.failed")
    assert failed["response"]["error"]["code"] == "ERR_RESPONSES_STORE_FAILED", (
        f"terminal failure must carry the contracted code: {failed}"
    )
    # No payload leaked into the failure frame's error text.
    assert "stream then fail" not in resp.text
    # The usage row recorded inside use_case.stream() stands (fire-and-forget → poll).
    await _poll_min_records(recorder, minimum=1)
    # Nothing persisted — the faulted insert committed no row.
    assert await count_stored_rows(db_session) == 0, "a failed stream persist must leave zero rows"
