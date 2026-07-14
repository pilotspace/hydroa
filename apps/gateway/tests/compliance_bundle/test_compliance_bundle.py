"""RED suite for GET /admin/compliance/art12-bundle (art12-record-keeping-preset TASK.md §3
— FROZEN @ v1).

The Art. 12 bundle is a one-click, cursor-continuable, dated evidence manifest assembled
read-only over 3 existing/new keyset reads (audit_events, request_logs, usage_records),
behind ONE pinned cover snapshot + ONE shared `bundle_token`. Frozen contract:
  - AUDIT_READ gated: owner/admin/operator/superadmin(-own-tenant) -> 200; billing_admin/
    viewer/member -> 403.
  - Tenant-scoped in every section; since/until (ISO-8601, both inclusive) REQUIRED.
  - Deterministic pinned cover (bundle_id, generated_at, residency_pin, zdr_state,
    retention_window_days, guardrail_configs_snapshot, default_tier) minted once, echoed
    verbatim across every page of the SAME bundle_token walk.
  - 3 independently keyset-paginated sections, one uniform limit (1..5000, default 1000).
  - ZDR honesty (M8) + plan-feature honesty (M9), both section-scoped to
    request_log_metadata only — the endpoint itself never 403s for M9.
  - Read-only; every successful page fires a fire-and-forget audit-of-generation row
    (action="compliance.art12_bundle"), fail-open.
  - Bounded query timeout -> ERR_EXPORT_TIMEOUT (504, reused).
  - bundle_token period mismatch -> ERR_CURSOR_INVALID (422, M14).

RED before BUILD: the route does not exist yet, so every 200-expecting scenario 404s — the
honest missing-implementation red. DO NOT weaken these tests to make them pass.
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import json
import logging
import time
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

from .conftest import (
    BASE,
    BUNDLE,
    assert_problem,
    assign_plan,
    auth,
    count_audit_rows,
    fetch_audit_rows,
    mint_role_token,
    seed_audit_event,
    seed_many_audit_events,
    seed_many_request_logs,
    seed_many_usage_records,
    seed_plan,
    seed_request_log,
    seed_usage_record,
    set_tenant_zdr,
    signup_tenant,
)

# pytest asyncio_mode=auto: `async def test_*` runs without a marker.


def _mins(n: int) -> datetime.datetime:
    return BASE + datetime.timedelta(minutes=n)


async def _drain_fire_and_forget() -> None:
    """Let a fire-and-forget asyncio.ensure_future(record_audit(...)) task complete before
    querying audit_events (mirrors tests/audit_export's own idiom)."""
    await asyncio.sleep(0.05)


async def _await_audit_count(
    session: AsyncSession,
    *,
    action: str,
    tenant_id: str | None = None,
    expected: int = 1,
    timeout: float = 3.0,  # noqa: ASYNC109 -- bounded poll loop, not a cancel scope
    interval: float = 0.02,
) -> int:
    """Poll until `count_audit_rows(action[, tenant_id])` reaches `expected`, or `timeout`
    elapses. De-flakes the fire-and-forget audit-of-generation write under `pytest -n 12`
    CPU saturation — a fixed `_drain_fire_and_forget()` sleep can read before the scheduled
    asyncio.ensure_future(record_audit(...)) task has run. Positive-assertion sites only;
    mirrors tests/superadmin_audit_foundation/conftest.py::await_audit_count. Never masks a
    genuinely-absent row — after `timeout` it returns the real count, so the caller's own
    `==`/`len(...)` assertion still fails honestly if the write never happened.
    """
    count = await count_audit_rows(session, action=action, tenant_id=tenant_id)
    deadline = time.monotonic() + timeout
    while count < expected and time.monotonic() < deadline:
        await asyncio.sleep(interval)
        count = await count_audit_rows(session, action=action, tenant_id=tenant_id)
    return count


async def _await_call_count_at_least(
    call_count: dict[str, int], *, expected: int, timeout: float = 3.0, interval: float = 0.02  # noqa: ASYNC109
) -> int:
    """Poll a mutable call-count dict until a fire-and-forget task's Nth sessionmaker() call
    has landed (or timeout) — same bounded-loop de-flake, for a non-DB fire-and-forget
    completion signal (mirrors tests/audit_export/test_audit_export.py's own helper)."""
    deadline = time.monotonic() + timeout
    while call_count["n"] < expected and time.monotonic() < deadline:  # noqa: ASYNC110
        await asyncio.sleep(interval)
    return call_count["n"]


def _period(since_min: int = 0, until_min: int = 60) -> dict[str, str]:
    return {"since": _mins(since_min).isoformat(), "until": _mins(until_min).isoformat()}


# ---------------------------------------------------------------------------
# M1 — role 200 / 403
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [Role.OWNER, Role.ADMIN, Role.OPERATOR, Role.SUPERADMIN])
async def test_bundle_roles_200(
    client: Any, db_session: AsyncSession, app: Any, role: Role
) -> None:
    _owner, tid = await signup_tenant(
        client, tenant_name=f"Bundle 200 {role}", email=f"bundle200-{role}@art12.io"
    )
    await seed_audit_event(db_session, tenant_id=tid, action="key.create", created_at=_mins(1))
    token = mint_role_token(app, tenant_id=tid, role=role, email=f"bundle200-sub-{role}@art12.io")

    resp = await client.get(BUNDLE, params=_period(), headers=auth(token))

    assert resp.status_code == 200, f"role={role} expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "cover" in body
    assert set(body["sections"].keys()) == {"audit_events", "request_log_metadata", "usage_lineage"}


@pytest.mark.parametrize("role", [Role.BILLING_ADMIN, Role.VIEWER, Role.MEMBER])
async def test_bundle_roles_403(
    client: Any, db_session: AsyncSession, app: Any, role: Role
) -> None:
    _owner, tid = await signup_tenant(
        client, tenant_name=f"Bundle 403 {role}", email=f"bundle403-{role}@art12.io"
    )
    token = mint_role_token(app, tenant_id=tid, role=role, email=f"bundle403-sub-{role}@art12.io")

    resp = await client.get(BUNDLE, params=_period(), headers=auth(token))

    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")
    await _drain_fire_and_forget()
    assert await count_audit_rows(db_session, action="compliance.art12_bundle", tenant_id=tid) == 0


# ---------------------------------------------------------------------------
# R1 — no bearer token
# ---------------------------------------------------------------------------


async def test_bundle_no_bearer_token(client: Any, db_session: AsyncSession) -> None:
    resp = await client.get(BUNDLE, params=_period())

    assert_problem(resp, 401, "ERR_AUTH_INVALID_TOKEN")
    await _drain_fire_and_forget()
    assert await count_audit_rows(db_session, action="compliance.art12_bundle") == 0


# ---------------------------------------------------------------------------
# M2 — tenant isolation across all 3 sections
# ---------------------------------------------------------------------------


async def test_bundle_tenant_isolation(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner_a, tid_a = await signup_tenant(client, tenant_name="Iso A", email="isoa@art12.io")
    _owner_b, tid_b = await signup_tenant(client, tenant_name="Iso B", email="isob@art12.io")

    id_a_audit = await seed_audit_event(db_session, tenant_id=tid_a, created_at=_mins(1))
    id_a_log = await seed_request_log(db_session, tenant_id=tid_a, created_at=_mins(1))
    id_a_usage = await seed_usage_record(db_session, tenant_id=tid_a, created_at=_mins(1))
    await seed_audit_event(db_session, tenant_id=tid_b, created_at=_mins(1))
    await seed_request_log(db_session, tenant_id=tid_b, created_at=_mins(1))
    await seed_usage_record(db_session, tenant_id=tid_b, created_at=_mins(1))

    token_a = mint_role_token(app, tenant_id=tid_a, role=Role.OWNER, email="iso-a-sub@art12.io")

    resp = await client.get(BUNDLE, params=_period(), headers=auth(token_a))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert {i["id"] for i in body["sections"]["audit_events"]["items"]} == {id_a_audit}
    assert {i["id"] for i in body["sections"]["request_log_metadata"]["items"]} == {id_a_log}
    assert {i["id"] for i in body["sections"]["usage_lineage"]["items"]} == {id_a_usage}


# ---------------------------------------------------------------------------
# M3, R3 — period is required
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"since": BASE.isoformat()},
        {"until": BASE.isoformat()},
        {},
    ],
    ids=["until-missing", "since-missing", "both-missing"],
)
async def test_bundle_period_required(
    client: Any, db_session: AsyncSession, app: Any, params: dict[str, str]
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Period Required", email="pr@art12.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="pr-sub@art12.io")

    resp = await client.get(BUNDLE, params=params, headers=auth(token))

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    await _drain_fire_and_forget()
    assert await count_audit_rows(db_session, action="compliance.art12_bundle", tenant_id=tid) == 0


# ---------------------------------------------------------------------------
# R4, R5 — malformed / inverted period
# ---------------------------------------------------------------------------


async def test_bundle_malformed_period(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Bad Period", email="bp@art12.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="bp-sub@art12.io")

    resp = await client.get(
        BUNDLE,
        params={"since": "not-a-date", "until": _mins(5).isoformat()},
        headers=auth(token),
    )

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


async def test_bundle_inverted_period(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Inverted Period", email="ip@art12.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="ip-sub@art12.io")

    resp = await client.get(
        BUNDLE,
        params={"since": _mins(5).isoformat(), "until": _mins(1).isoformat()},
        headers=auth(token),
    )

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# M4, M7 — cover is pinned across pages of the same bundle walk
# ---------------------------------------------------------------------------


async def test_bundle_cover_pinned_across_pages(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Cover Pinned", email="cp@art12.io")
    await db_session.execute(
        text("UPDATE tenants SET residency_region = 'eu' WHERE id = :tid"), {"tid": tid}
    )
    await db_session.commit()
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="cp-sub@art12.io")
    for i in range(1, 4):
        await seed_audit_event(db_session, tenant_id=tid, action=f"ev.{i}", created_at=_mins(i))

    page1 = await client.get(BUNDLE, params={**_period(), "limit": "1"}, headers=auth(token))
    assert page1.status_code == 200, page1.text
    body1 = page1.json()
    assert body1["cover"]["zdr_state"]["enabled"] is False
    assert body1["cover"]["residency_pin"] == "eu"
    token1 = body1["bundle_token"]
    assert token1

    # Tenant flips zdr_enabled AFTER page 1 was minted.
    await set_tenant_zdr(db_session, tid, enabled=True, enabled_at=_mins(2))

    page2 = await client.get(
        BUNDLE,
        params={**_period(), "limit": "1", "bundle_token": token1},
        headers=auth(token),
    )
    assert page2.status_code == 200, page2.text
    body2 = page2.json()

    assert body2["cover"]["zdr_state"]["enabled"] is False, "must echo the MINT-TIME value"
    assert body2["cover"]["residency_pin"] == "eu"
    assert body2["cover"]["bundle_id"] == body1["cover"]["bundle_id"]
    assert body2["cover"]["generated_at"] == body1["cover"]["generated_at"]


# ---------------------------------------------------------------------------
# M5, M6, M7 — bundle walks all 3 sections to completion with no gaps or dupes
# ---------------------------------------------------------------------------


async def test_bundle_walks_all_sections_no_gaps_or_dupes(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Walk All", email="walk@art12.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="walk-sub@art12.io")

    audit_ids = [
        await seed_audit_event(db_session, tenant_id=tid, action=f"a.{i}", created_at=_mins(i))
        for i in range(1, 8)
    ]
    log_ids = [
        await seed_request_log(db_session, tenant_id=tid, created_at=_mins(i)) for i in range(1, 6)
    ]
    usage_ids = [
        await seed_usage_record(db_session, tenant_id=tid, created_at=_mins(i))
        for i in range(1, 10)
    ]

    collected: dict[str, list[str]] = {
        "audit_events": [],
        "request_log_metadata": [],
        "usage_lineage": [],
    }
    bundle_token: str | None = None
    for _ in range(20):
        params: dict[str, str] = {**_period(0, 20), "limit": "3"}
        if bundle_token is not None:
            params["bundle_token"] = bundle_token
        resp = await client.get(BUNDLE, params=params, headers=auth(token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        for section in collected:
            collected[section].extend(i["id"] for i in body["sections"][section]["items"])
        bundle_token = body["bundle_token"]
        if bundle_token is None:
            break
    else:
        pytest.fail("bundle pagination never terminated (bundle_token stayed non-null)")

    assert collected["audit_events"] == list(reversed(audit_ids))
    assert collected["request_log_metadata"] == list(reversed(log_ids))
    assert collected["usage_lineage"] == list(reversed(usage_ids))


# ---------------------------------------------------------------------------
# M5 — default and max page size, applied uniformly
# ---------------------------------------------------------------------------


async def test_bundle_default_page_size_uniform(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Default Page", email="dp@art12.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="dp-sub@art12.io")
    await seed_many_audit_events(db_session, tenant_id=tid, count=1200)
    await seed_many_request_logs(db_session, tenant_id=tid, count=1200)
    await seed_many_usage_records(db_session, tenant_id=tid, count=1200)

    resp = await client.get(BUNDLE, params=_period(0, 30000), headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    for section in ("audit_events", "request_log_metadata", "usage_lineage"):
        assert len(body["sections"][section]["items"]) == 1000, section
        assert body["sections"][section]["has_more"] is True, section
    assert body["bundle_token"] is not None


@pytest.mark.parametrize("limit", ["0", "5001", "abc"])
async def test_bundle_limit_out_of_bounds(
    client: Any, db_session: AsyncSession, app: Any, limit: str
) -> None:
    _owner, tid = await signup_tenant(
        client, tenant_name=f"Limit Bounds {limit}", email=f"lb-{limit}@art12.io"
    )
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email=f"lb-sub-{limit}@art12.io")

    resp = await client.get(BUNDLE, params={**_period(), "limit": limit}, headers=auth(token))

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    await _drain_fire_and_forget()
    assert await count_audit_rows(db_session, action="compliance.art12_bundle", tenant_id=tid) == 0


# ---------------------------------------------------------------------------
# M8 — ZDR tenant's log section is honestly empty
# ---------------------------------------------------------------------------


async def test_bundle_zdr_tenant_log_section_honest(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="ZDR Honest", email="zdr@art12.io")
    await set_tenant_zdr(db_session, tid, enabled=True, enabled_at=_mins(0))
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="zdr-sub@art12.io")
    await seed_audit_event(db_session, tenant_id=tid, created_at=_mins(1))
    await seed_usage_record(db_session, tenant_id=tid, created_at=_mins(1))

    resp = await client.get(BUNDLE, params=_period(), headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    log_section = body["sections"]["request_log_metadata"]
    assert log_section["items"] == []
    assert log_section["has_more"] is False
    assert log_section["note"] is not None
    assert "Zero-Data-Retention" in log_section["note"]
    assert len(body["sections"]["audit_events"]["items"]) == 1
    assert len(body["sections"]["usage_lineage"]["items"]) == 1


# ---------------------------------------------------------------------------
# M9 — tenant without the logs_explorer plan feature
# ---------------------------------------------------------------------------


async def test_bundle_missing_logs_explorer_plan_feature(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="No Logs Plan", email="nlp@art12.io")
    plan_id = await seed_plan(db_session, name="art12-no-logs", feature_flags=["batch"])
    await assign_plan(db_session, tenant_id=tid, plan_id=plan_id)
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="nlp-sub@art12.io")
    await seed_audit_event(db_session, tenant_id=tid, created_at=_mins(1))
    await seed_usage_record(db_session, tenant_id=tid, created_at=_mins(1))

    resp = await client.get(BUNDLE, params=_period(), headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    log_section = body["sections"]["request_log_metadata"]
    assert log_section["items"] == []
    assert log_section["has_more"] is False
    assert log_section["note"] == (
        "tenant plan does not include logs_explorer; audit_events and usage_lineage are unaffected"
    )
    assert len(body["sections"]["audit_events"]["items"]) == 1
    assert len(body["sections"]["usage_lineage"]["items"]) == 1


# ---------------------------------------------------------------------------
# M10 — bundle generation never mutates the underlying stores
# ---------------------------------------------------------------------------


async def test_bundle_never_mutates_underlying_stores(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="No Mutate", email="nm@art12.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="nm-sub@art12.io")
    audit_id = await seed_audit_event(db_session, tenant_id=tid, created_at=_mins(1))
    log_id = await seed_request_log(db_session, tenant_id=tid, created_at=_mins(1))
    usage_id = await seed_usage_record(db_session, tenant_id=tid, created_at=_mins(1))

    async def _snapshot() -> tuple[Any, Any, Any]:
        a = (
            await db_session.execute(
                text("SELECT action, result, created_at FROM audit_events WHERE id = :id"),
                {"id": audit_id},
            )
        ).fetchone()
        rl = (
            await db_session.execute(
                text("SELECT status_code, cost_usd, created_at FROM request_logs WHERE id = :id"),
                {"id": log_id},
            )
        ).fetchone()
        u = (
            await db_session.execute(
                text(
                    "SELECT prompt_tokens, cost_usd, created_at FROM usage_records WHERE id = :id"
                ),
                {"id": usage_id},
            )
        ).fetchone()
        return (tuple(a) if a else None, tuple(rl) if rl else None, tuple(u) if u else None)

    before = await _snapshot()
    resp = await client.get(BUNDLE, params=_period(), headers=auth(token))
    assert resp.status_code == 200, resp.text
    after = await _snapshot()

    assert before == after


# ---------------------------------------------------------------------------
# M11 — bundle generation is itself audited (+ fail-open)
# ---------------------------------------------------------------------------


async def test_bundle_success_is_itself_audited(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Self Audited", email="sa@art12.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="sa-sub@art12.io")
    for i in range(1, 4):
        await seed_audit_event(db_session, tenant_id=tid, action=f"a.{i}", created_at=_mins(i))

    page1 = await client.get(BUNDLE, params={**_period(), "limit": "1"}, headers=auth(token))
    assert page1.status_code == 200, page1.text
    token1 = page1.json()["bundle_token"]
    assert token1

    page2 = await client.get(
        BUNDLE,
        params={**_period(), "limit": "1", "bundle_token": token1},
        headers=auth(token),
    )
    assert page2.status_code == 200, page2.text

    await _await_audit_count(db_session, action="compliance.art12_bundle", tenant_id=tid, expected=2)
    rows = await fetch_audit_rows(db_session, action="compliance.art12_bundle", tenant_id=tid)
    assert len(rows) == 2, "expected exactly 2 audit rows after 2 successful bundle pages"
    for r_tenant_id, _r_actor_user_id, r_action, r_result, r_metadata, _r_created_at in rows:
        assert str(r_tenant_id) == tid
        assert r_action == "compliance.art12_bundle"
        assert r_result == "success"
        metadata = json.loads(r_metadata) if isinstance(r_metadata, str) else r_metadata
        for key in ("since", "until", "bundle_id", "page_token_used", "limit", "row_counts"):
            assert key in metadata, f"missing metadata key {key!r}: {metadata}"
        assert set(metadata["row_counts"].keys()) == {
            "audit_events",
            "request_log_metadata",
            "usage_lineage",
        }
    page_token_used_values = {
        (json.loads(r[4]) if isinstance(r[4], str) else r[4])["page_token_used"] for r in rows
    }
    assert page_token_used_values == {False, True}, "mint call vs continuation call must differ"


async def test_bundle_audit_write_failure_does_not_fail_bundle(
    client: Any,
    db_session: AsyncSession,
    app: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Mirrors tests/audit_export's own fail-open test — the audit WRITER's own separate
    session-factory call fails; the bundle's own primary session (Depends(get_session)) must
    still succeed."""
    _owner, tid = await signup_tenant(client, tenant_name="Fail Open", email="fo@art12.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="fo-sub@art12.io")
    await seed_audit_event(db_session, tenant_id=tid, action="ev.1", created_at=_mins(1))

    real_sessionmaker = app.state.sessionmaker
    call_count = {"n": 0}

    def _fail_after_first_call(*args: object, **kwargs: object) -> Any:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return real_sessionmaker(*args, **kwargs)
        raise RuntimeError("audit db unreachable (simulated outage)")

    app.state.sessionmaker = _fail_after_first_call
    try:
        with caplog.at_level(logging.WARNING):
            resp = await client.get(BUNDLE, params=_period(), headers=auth(token))
            assert resp.status_code == 200, resp.text
            assert len(resp.json()["sections"]["audit_events"]["items"]) == 1
            await _await_call_count_at_least(call_count, expected=2)
    finally:
        app.state.sessionmaker = real_sessionmaker

    assert call_count["n"] >= 2
    assert "failed to persist audit event" in caplog.text


# ---------------------------------------------------------------------------
# M12, R9 — bounded query timeout surfaces honestly
# ---------------------------------------------------------------------------


async def test_bundle_timeout_surfaces_honestly(
    client: Any, db_session: AsyncSession, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Slow Bundle", email="sb@art12.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="sb-sub@art12.io")
    await seed_usage_record(db_session, tenant_id=tid, created_at=_mins(1))

    orig_execute = AsyncSession.execute

    async def _flaky_execute(self: AsyncSession, statement: Any, *args: Any, **kwargs: Any) -> Any:
        compiled = str(statement).lstrip()
        if compiled.upper().startswith("SELECT") and "usage_records" in compiled:
            raise TimeoutError("simulated bundle DB fault (test-only fault injection)")
        return await orig_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", _flaky_execute)

    resp = await client.get(BUNDLE, params=_period(), headers=auth(token))

    assert_problem(resp, 504, "ERR_EXPORT_TIMEOUT")


# ---------------------------------------------------------------------------
# M13 — purge mid-walk is a silent, honest gap
# ---------------------------------------------------------------------------


async def test_bundle_purge_mid_walk_is_silent_honest_gap(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Purge Mid", email="pm@art12.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="pm-sub@art12.io")
    usage_ids = [
        await seed_usage_record(db_session, tenant_id=tid, created_at=_mins(i))
        for i in range(1, 5)  # oldest(1)->newest(4)
    ]

    page1 = await client.get(BUNDLE, params={**_period(), "limit": "2"}, headers=auth(token))
    assert page1.status_code == 200, page1.text
    body1 = page1.json()
    assert body1["sections"]["usage_lineage"]["has_more"] is True
    token1 = body1["bundle_token"]
    assert token1

    # Simulate the retention sweeper purging one remaining (un-issued) usage_records row.
    await db_session.execute(text("DELETE FROM usage_records WHERE id = :id"), {"id": usage_ids[0]})
    await db_session.commit()

    page2 = await client.get(
        BUNDLE,
        params={**_period(), "limit": "2", "bundle_token": token1},
        headers=auth(token),
    )
    assert page2.status_code == 200, page2.text
    body2 = page2.json()
    usage_section = body2["sections"]["usage_lineage"]
    ids2 = [i["id"] for i in usage_section["items"]]
    assert ids2 == [usage_ids[1]], "the purged row is simply absent — no error, no gap marker"
    assert usage_section["has_more"] is False
    assert usage_section["note"] is None, "M13 is never surfaced via the note field (M8/M9 only)"


# ---------------------------------------------------------------------------
# R7 — malformed bundle_token
# ---------------------------------------------------------------------------


async def test_bundle_malformed_bundle_token(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Bad Token", email="bt@art12.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="bt-sub@art12.io")

    resp = await client.get(
        BUNDLE,
        params={**_period(), "bundle_token": "not-valid-base64-or-wrong-shape"},
        headers=auth(token),
    )

    assert_problem(resp, 422, "ERR_CURSOR_INVALID")
    await _drain_fire_and_forget()
    assert await count_audit_rows(db_session, action="compliance.art12_bundle", tenant_id=tid) == 0


async def test_bundle_token_wrong_shape_valid_base64(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Wrong Shape", email="ws@art12.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="ws-sub@art12.io")
    bogus = base64.urlsafe_b64encode(json.dumps({"nope": True}).encode()).decode("ascii")

    resp = await client.get(
        BUNDLE, params={**_period(), "bundle_token": bogus}, headers=auth(token)
    )

    assert_problem(resp, 422, "ERR_CURSOR_INVALID")


# ---------------------------------------------------------------------------
# M14, R8 — bundle_token period mismatch is rejected
# ---------------------------------------------------------------------------


async def test_bundle_token_period_mismatch_rejected(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Period Mismatch", email="pmm@art12.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="pmm-sub@art12.io")
    for i in range(1, 4):
        await seed_audit_event(db_session, tenant_id=tid, action=f"a.{i}", created_at=_mins(i))

    page1 = await client.get(BUNDLE, params={**_period(0, 10), "limit": "1"}, headers=auth(token))
    assert page1.status_code == 200, page1.text
    token1 = page1.json()["bundle_token"]
    assert token1

    resp = await client.get(
        BUNDLE,
        params={
            "since": _mins(100).isoformat(),
            "until": _mins(200).isoformat(),
            "limit": "1",
            "bundle_token": token1,
        },
        headers=auth(token),
    )

    assert_problem(resp, 422, "ERR_CURSOR_INVALID")
    await _await_audit_count(db_session, action="compliance.art12_bundle", tenant_id=tid, expected=1)
    # Rejected before any DB read — only whatever mint-page-1 wrote exists (never a 2nd row).
    assert await count_audit_rows(db_session, action="compliance.art12_bundle", tenant_id=tid) == 1


# ---------------------------------------------------------------------------
# Boundary — empty bundle
# ---------------------------------------------------------------------------


async def test_bundle_empty_bundle(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Empty Bundle", email="eb@art12.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="eb-sub@art12.io")

    resp = await client.get(BUNDLE, params=_period(), headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    for section in ("audit_events", "request_log_metadata", "usage_lineage"):
        assert body["sections"][section]["items"] == []
        assert body["sections"][section]["next_cursor"] is None
        assert body["sections"][section]["has_more"] is False
    assert body["bundle_token"] is None
    cover = body["cover"]
    assert cover["tenant_id"] == tid
    assert cover["period"]["since"]
    assert cover["period"]["until"]
    assert cover["bundle_id"]
    assert cover["generated_at"]


# ---------------------------------------------------------------------------
# Boundary — last-page boundary, exact multiple of limit
# ---------------------------------------------------------------------------


async def test_bundle_last_page_boundary_exact_multiple_of_limit(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Exact Boundary", email="ebd@art12.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="ebd-sub@art12.io")
    await seed_many_audit_events(db_session, tenant_id=tid, count=1000)

    resp = await client.get(BUNDLE, params=_period(0, 30000), headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    audit_section = body["sections"]["audit_events"]
    assert len(audit_section["items"]) == 1000
    assert audit_section["has_more"] is False
    assert audit_section["next_cursor"] is None
    assert body["bundle_token"] is None


# ---------------------------------------------------------------------------
# Concurrency/retry-safety — duplicate bundle_token request is idempotent
# ---------------------------------------------------------------------------


async def test_bundle_duplicate_token_request_idempotent(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Dup Token", email="dt@art12.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="dt-sub@art12.io")
    for i in range(1, 6):
        await seed_audit_event(db_session, tenant_id=tid, action=f"a.{i}", created_at=_mins(i))

    page1 = await client.get(BUNDLE, params={**_period(), "limit": "2"}, headers=auth(token))
    assert page1.status_code == 200, page1.text
    token1 = page1.json()["bundle_token"]
    assert token1

    baseline = await _await_audit_count(
        db_session, action="compliance.art12_bundle", tenant_id=tid, expected=1
    )

    retry_params = {**_period(), "limit": "2", "bundle_token": token1}
    retry1 = await client.get(BUNDLE, params=retry_params, headers=auth(token))
    retry2 = await client.get(BUNDLE, params=retry_params, headers=auth(token))

    assert retry1.status_code == 200, retry1.text
    assert retry2.status_code == 200, retry2.text
    assert retry1.json()["cover"] == retry2.json()["cover"]
    assert retry1.json()["sections"] == retry2.json()["sections"]

    after = await _await_audit_count(
        db_session, action="compliance.art12_bundle", tenant_id=tid, expected=baseline + 2
    )
    assert after == baseline + 2, "each successful read fires its own audit row (M11, no dedupe)"
