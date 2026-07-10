"""RED suite for GET /admin/audit/export (compliance-export-api TASK.md §3 — FROZEN @ v1).

Compliance export is a read-only, cursor-paginated, time/actor-filtered pull over the
existing tenant audit trail (audit_events) — a NEW sibling route (audit's first `api/`
layer), distinct from the FROZEN v1 `GET /admin/audit` (usage/api/router.py:728, untouched
by this suite). Frozen contract:
  - AUDIT_READ gated: owner/admin/operator -> 200; billing_admin/viewer/member -> 403.
  - Tenant-scoped; cursor pagination only (keyset on (created_at, id) DESC, no offset).
  - limit 1..5000 (default 1000); since/until (ISO-8601, inclusive); actor_email (exact,
    case-insensitive).
  - Default NDJSON body + X-Audit-Export-{Has-More,Next-Cursor} headers; `?format=json`
    opt-in returns {items, next_cursor, has_more} — no `total`.
  - Read-only; every successful 200 fires one fire-and-forget audit-of-export write
    (action="audit.export"), fail-open.
  - Bounded query timeout -> ERR_EXPORT_TIMEOUT (504).
  - Malformed/undecodable cursor -> ERR_CURSOR_INVALID (422), distinct from
    ERR_PAYLOAD_INVALID.

RED before BUILD: the route does not exist yet (audit/api/router.py + main.py mount), so
every 200-expecting scenario 404s (or worse, is masked by an unrelated 401/403 first) — the
honest missing-implementation red. DO NOT weaken these tests to make them pass; that is
Build's job.
"""

from __future__ import annotations

import asyncio
import base64
import datetime
import json
import logging
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

from .conftest import (
    BASE,
    EXPORT,
    auth,
    count_audit_rows,
    fetch_one_audit_row,
    mint_role_token,
    seed_audit_event,
    seed_many_audit_events,
    signup_tenant,
)

# pytest asyncio_mode=auto: `async def test_*` runs without a marker.

_ITEM_FIELDS = (
    "id",
    "actor_email",
    "action",
    "target_type",
    "target_id",
    "result",
    "metadata",
    "created_at",
)


def assert_problem(resp: Any, status: int, code: str) -> None:
    assert resp.status_code == status, f"expected {status}, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("code") == code, f"expected {code!r}, got {body.get('code')!r}: {body}"


def _mins(n: int) -> datetime.datetime:
    return BASE + datetime.timedelta(minutes=n)


def _ndjson_lines(body: str) -> list[str]:
    return [ln for ln in body.split("\n") if ln]


async def _drain_fire_and_forget() -> None:
    """Let a fire-and-forget asyncio.ensure_future(record_audit(...)) task complete before
    querying audit_events (mirrors tests/admin_console_audit's own idiom)."""
    await asyncio.sleep(0.05)


# ---------------------------------------------------------------------------
# M1 — role 200 / 403
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [Role.OWNER, Role.ADMIN, Role.OPERATOR])
async def test_export_roles_200(
    client: Any, db_session: AsyncSession, app: Any, role: Role
) -> None:
    _owner_jwt, tid = await signup_tenant(
        client, tenant_name=f"Export 200 {role}", email=f"export200-owner-{role}@audit.io"
    )
    await seed_audit_event(db_session, tenant_id=tid, action="key.create", created_at=_mins(1))
    await seed_audit_event(db_session, tenant_id=tid, action="key.delete", created_at=_mins(2))
    token = mint_role_token(app, tenant_id=tid, role=role, email=f"export200-sub-{role}@audit.io")

    resp = await client.get(EXPORT, headers=auth(token))

    assert resp.status_code == 200, f"role={role} expected 200, got {resp.status_code}: {resp.text}"
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    lines = _ndjson_lines(resp.text)
    assert len(lines) == 2
    for line in lines:
        obj = json.loads(line)
        for field in _ITEM_FIELDS:
            assert field in obj, f"missing field {field!r} in line: {obj}"


@pytest.mark.parametrize("role", [Role.BILLING_ADMIN, Role.VIEWER, Role.MEMBER])
async def test_export_roles_403(
    client: Any, db_session: AsyncSession, app: Any, role: Role
) -> None:
    _owner_jwt, tid = await signup_tenant(
        client, tenant_name=f"Export 403 {role}", email=f"export403-owner-{role}@audit.io"
    )
    token = mint_role_token(app, tenant_id=tid, role=role, email=f"export403-sub-{role}@audit.io")

    resp = await client.get(EXPORT, headers=auth(token))

    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")
    await _drain_fire_and_forget()
    assert await count_audit_rows(db_session, action="audit.export", tenant_id=tid) == 0, (
        "a denied (403) attempt must never write an audit-of-export row"
    )


# ---------------------------------------------------------------------------
# R1 — no bearer token
# ---------------------------------------------------------------------------


async def test_export_no_bearer_token(client: Any, db_session: AsyncSession) -> None:
    resp = await client.get(EXPORT)

    assert_problem(resp, 401, "ERR_AUTH_INVALID_TOKEN")
    await _drain_fire_and_forget()
    assert await count_audit_rows(db_session, action="audit.export") == 0


# ---------------------------------------------------------------------------
# M2 — tenant isolation under export filters
# ---------------------------------------------------------------------------


async def test_export_tenant_isolation_under_filters(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner_a, tid_a = await signup_tenant(client, tenant_name="Isolation A", email="isoa@audit.io")
    _owner_b, tid_b = await signup_tenant(client, tenant_name="Isolation B", email="isob@audit.io")
    id_a1 = await seed_audit_event(
        db_session, tenant_id=tid_a, actor_email="shared@x.io", created_at=_mins(1)
    )
    id_a2 = await seed_audit_event(
        db_session, tenant_id=tid_a, actor_email="shared@x.io", created_at=_mins(2)
    )
    await seed_audit_event(
        db_session, tenant_id=tid_b, actor_email="shared@x.io", created_at=_mins(1)
    )
    token_a = mint_role_token(app, tenant_id=tid_a, role=Role.OWNER, email="iso-a-sub@audit.io")

    # No filters at all.
    resp = await client.get(EXPORT, params={"format": "json"}, headers=auth(token_a))
    assert resp.status_code == 200, resp.text
    ids = {item["id"] for item in resp.json()["items"]}
    assert ids == {id_a1, id_a2}

    # Filters that would ALSO match tenant B's row must still never surface it.
    resp2 = await client.get(
        EXPORT,
        params={
            "format": "json",
            "actor_email": "shared@x.io",
            "since": _mins(0).isoformat(),
            "until": _mins(5).isoformat(),
        },
        headers=auth(token_a),
    )
    assert resp2.status_code == 200, resp2.text
    ids2 = {item["id"] for item in resp2.json()["items"]}
    assert ids2 == {id_a1, id_a2}, "tenant B's row must never leak, even under matching filters"


# ---------------------------------------------------------------------------
# M3 — deterministic keyset ordering survives a concurrent insert
# ---------------------------------------------------------------------------


async def test_export_keyset_survives_concurrent_insert(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Keyset Concurrent", email="ks@audit.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="ks-sub@audit.io")
    row_ids = [
        await seed_audit_event(db_session, tenant_id=tid, action=f"ev.{i}", created_at=_mins(i))
        for i in range(1, 6)  # row_ids[0]=oldest (min1) ... row_ids[4]=newest (min5)
    ]

    page1 = await client.get(
        EXPORT, params={"format": "json", "limit": "2"}, headers=auth(token)
    )
    assert page1.status_code == 200, page1.text
    body1 = page1.json()
    assert [i["id"] for i in body1["items"]] == [row_ids[4], row_ids[3]]
    assert body1["has_more"] is True
    cursor = body1["next_cursor"]
    assert cursor

    # A concurrent write happens strictly AFTER page 1 was issued, newer than every seeded row.
    new_id = await seed_audit_event(
        db_session, tenant_id=tid, action="ev.new", created_at=_mins(10)
    )

    page2 = await client.get(
        EXPORT,
        params={"format": "json", "limit": "2", "cursor": cursor},
        headers=auth(token),
    )
    assert page2.status_code == 200, page2.text
    body2 = page2.json()
    ids2 = [i["id"] for i in body2["items"]]
    assert new_id not in ids2, "a concurrent insert must never appear mid-walk"
    assert ids2 == [row_ids[2], row_ids[1]], "page 2 must continue exactly where page 1 left off"


# ---------------------------------------------------------------------------
# M3, M4 — cursor pagination walks the full set with no gaps or dupes
# ---------------------------------------------------------------------------


async def test_export_cursor_walks_full_set_no_gaps_or_dupes(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Walk All", email="walk@audit.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="walk-sub@audit.io")
    row_ids = [
        await seed_audit_event(db_session, tenant_id=tid, action=f"ev.{i}", created_at=_mins(i))
        for i in range(1, 8)  # 7 rows, oldest->newest
    ]
    expected_desc = list(reversed(row_ids))

    collected: list[str] = []
    cursor: str | None = None
    for _ in range(10):  # generous cap — a bug here must not hang the suite
        params: dict[str, str] = {"format": "json", "limit": "3"}
        if cursor is not None:
            params["cursor"] = cursor
        resp = await client.get(EXPORT, params=params, headers=auth(token))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        collected.extend(i["id"] for i in body["items"])
        if not body["has_more"]:
            assert body["next_cursor"] is None
            break
        cursor = body["next_cursor"]
    else:
        pytest.fail("export pagination never terminated (has_more stayed true)")

    assert collected == expected_desc, "every id exactly once, in created_at DESC, id DESC order"


# ---------------------------------------------------------------------------
# M4 — no offset parameter accepted (silently ignored, not a reject)
# ---------------------------------------------------------------------------


async def test_export_offset_param_ignored(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Offset Ignored", email="off@audit.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="off-sub@audit.io")
    await seed_audit_event(db_session, tenant_id=tid, created_at=_mins(1))

    resp = await client.get(EXPORT, params={"offset": "5"}, headers=auth(token))

    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# M5 — default and max page size
# ---------------------------------------------------------------------------


async def test_export_default_page_size(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Default Page", email="dp@audit.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="dp-sub@audit.io")
    await seed_many_audit_events(db_session, tenant_id=tid, count=1200)

    resp = await client.get(EXPORT, params={"format": "json"}, headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 1000
    assert body["has_more"] is True
    assert body["next_cursor"] is not None


async def test_export_last_page_boundary_exact_multiple_of_limit(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Exact Boundary", email="eb@audit.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="eb-sub@audit.io")
    await seed_many_audit_events(db_session, tenant_id=tid, count=1000)

    resp = await client.get(EXPORT, headers=auth(token))  # NDJSON default, default limit

    assert resp.status_code == 200, resp.text
    assert len(_ndjson_lines(resp.text)) == 1000
    assert resp.headers["x-audit-export-has-more"] == "false"
    assert "x-audit-export-next-cursor" not in resp.headers


@pytest.mark.parametrize("limit", ["0", "5001", "abc"])
async def test_export_limit_out_of_bounds(
    client: Any, db_session: AsyncSession, app: Any, limit: str
) -> None:
    _owner, tid = await signup_tenant(
        client, tenant_name=f"Limit Bounds {limit}", email=f"lb-{limit}@audit.io"
    )
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email=f"lb-sub-{limit}@audit.io")

    resp = await client.get(EXPORT, params={"limit": limit}, headers=auth(token))

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    await _drain_fire_and_forget()
    assert await count_audit_rows(db_session, action="audit.export", tenant_id=tid) == 0


# ---------------------------------------------------------------------------
# M6 — since/until filter
# ---------------------------------------------------------------------------


async def test_export_since_until_filter_narrows_results(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Time Filter", email="tf@audit.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="tf-sub@audit.io")
    id1 = await seed_audit_event(db_session, tenant_id=tid, action="ev.1", created_at=_mins(1))
    id2 = await seed_audit_event(db_session, tenant_id=tid, action="ev.2", created_at=_mins(2))
    id3 = await seed_audit_event(db_session, tenant_id=tid, action="ev.3", created_at=_mins(3))

    resp = await client.get(
        EXPORT,
        params={
            "format": "json",
            "since": _mins(2).isoformat(),
            "until": _mins(3).isoformat(),
        },
        headers=auth(token),
    )

    assert resp.status_code == 200, resp.text
    ids = {item["id"] for item in resp.json()["items"]}
    assert ids == {id2, id3}
    assert id1 not in ids


async def test_export_malformed_time_range(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Bad Time", email="bt@audit.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="bt-sub@audit.io")

    resp = await client.get(EXPORT, params={"since": "not-a-date"}, headers=auth(token))

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


async def test_export_inverted_time_range(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Inverted Time", email="it@audit.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="it-sub@audit.io")

    resp = await client.get(
        EXPORT,
        params={"since": _mins(5).isoformat(), "until": _mins(1).isoformat()},
        headers=auth(token),
    )

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    await _drain_fire_and_forget()
    assert await count_audit_rows(db_session, action="audit.export", tenant_id=tid) == 0


# ---------------------------------------------------------------------------
# M7 — actor_email filter (case-insensitive exact match)
# ---------------------------------------------------------------------------


async def test_export_actor_email_filter_case_insensitive(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Actor Filter", email="af@audit.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="af-sub@audit.io")
    id_a = await seed_audit_event(
        db_session, tenant_id=tid, actor_email="a@x.io", created_at=_mins(1)
    )
    await seed_audit_event(db_session, tenant_id=tid, actor_email="b@x.io", created_at=_mins(2))

    resp = await client.get(
        EXPORT, params={"format": "json", "actor_email": "A@X.IO"}, headers=auth(token)
    )

    assert resp.status_code == 200, resp.text
    ids = {item["id"] for item in resp.json()["items"]}
    assert ids == {id_a}


# ---------------------------------------------------------------------------
# M8, M9 — NDJSON default vs JSON opt-in
# ---------------------------------------------------------------------------


async def test_export_ndjson_is_default_and_line_pure(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="NDJSON Default", email="nd@audit.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="nd-sub@audit.io")
    for i in range(1, 4):
        await seed_audit_event(db_session, tenant_id=tid, action=f"ev.{i}", created_at=_mins(i))

    resp = await client.get(EXPORT, headers=auth(token))

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/x-ndjson")
    lines = _ndjson_lines(resp.text)
    assert len(lines) == 3
    for line in lines:
        obj = json.loads(line)
        assert set(obj.keys()) == set(_ITEM_FIELDS)
    assert resp.headers["x-audit-export-has-more"] == "false"
    assert "x-audit-export-next-cursor" not in resp.headers


async def test_export_json_format_opt_in(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="JSON Opt-in", email="jo@audit.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="jo-sub@audit.io")
    for i in range(1, 4):
        await seed_audit_event(db_session, tenant_id=tid, action=f"ev.{i}", created_at=_mins(i))

    resp = await client.get(EXPORT, params={"format": "json"}, headers=auth(token))

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert len(body["items"]) == 3
    assert body["next_cursor"] is None
    assert body["has_more"] is False
    assert "total" not in body


async def test_export_invalid_format_value(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Bad Format", email="bf@audit.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="bf-sub@audit.io")

    resp = await client.get(EXPORT, params={"format": "csv"}, headers=auth(token))

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# R7 — malformed cursor
# ---------------------------------------------------------------------------


async def test_export_malformed_cursor(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Bad Cursor", email="bc@audit.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="bc-sub@audit.io")

    resp = await client.get(
        EXPORT, params={"cursor": "not-valid-base64-or-wrong-shape"}, headers=auth(token)
    )

    assert_problem(resp, 422, "ERR_CURSOR_INVALID")
    await _drain_fire_and_forget()
    assert await count_audit_rows(db_session, action="audit.export", tenant_id=tid) == 0


async def test_export_cursor_wrong_shape_valid_base64(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    """A cursor that decodes as valid base64url JSON but the wrong shape (missing keys)
    must still be ERR_CURSOR_INVALID, not a 500 KeyError."""
    _owner, tid = await signup_tenant(client, tenant_name="Wrong Shape", email="ws@audit.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="ws-sub@audit.io")
    bogus = base64.urlsafe_b64encode(json.dumps({"nope": True}).encode()).decode("ascii")

    resp = await client.get(EXPORT, params={"cursor": bogus}, headers=auth(token))

    assert_problem(resp, 422, "ERR_CURSOR_INVALID")


# ---------------------------------------------------------------------------
# M10 — export never mutates audit_events
# ---------------------------------------------------------------------------


async def test_export_never_mutates_audit_events(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="No Mutate", email="nm@audit.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="nm-sub@audit.io")
    ids = [
        await seed_audit_event(db_session, tenant_id=tid, action=f"ev.{i}", created_at=_mins(i))
        for i in range(1, 4)
    ]

    async def _snapshot() -> list[tuple[Any, ...]]:
        result = await db_session.execute(
            text(
                "SELECT id, action, result, metadata, created_at FROM audit_events "
                "WHERE id = ANY(:ids) ORDER BY id"
            ),
            {"ids": ids},
        )
        return [tuple(row) for row in result.fetchall()]

    before = await _snapshot()

    resp = await client.get(EXPORT, params={"limit": "2"}, headers=auth(token))
    assert resp.status_code == 200, resp.text

    after = await _snapshot()
    assert before == after, "no seeded row's fields may change as a side effect of the read"


# ---------------------------------------------------------------------------
# M11 — export access is itself audited (+ fail-open)
# ---------------------------------------------------------------------------


async def test_export_success_is_itself_audited(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Self Audited", email="sa@audit.io")
    exporter_id = uuid.uuid4()
    token, _ = app.state.token_service.issue(
        user_id=exporter_id, tenant_id=uuid.UUID(tid), role=Role.OWNER, email="sa-sub@audit.io"
    )
    await seed_audit_event(db_session, tenant_id=tid, created_at=_mins(1))

    resp = await client.get(
        EXPORT,
        params={"actor_email": "someone@x.io", "limit": "10"},
        headers=auth(str(token)),
    )
    assert resp.status_code == 200, resp.text

    await _drain_fire_and_forget()
    row = await fetch_one_audit_row(db_session, action="audit.export", tenant_id=tid)
    assert row is not None, "expected exactly one audit.export row after a successful export"
    (
        r_tenant_id,
        r_actor_user_id,
        _r_actor_email,
        r_action,
        _r_target_type,
        _r_target_id,
        r_result,
        r_metadata,
    ) = row
    assert str(r_tenant_id) == tid
    assert r_actor_user_id == exporter_id
    assert r_action == "audit.export"
    assert r_result == "success"
    metadata = json.loads(r_metadata) if isinstance(r_metadata, str) else r_metadata
    for key in ("since", "until", "actor_email", "cursor_used", "limit", "row_count", "format"):
        assert key in metadata, f"missing metadata key {key!r}: {metadata}"
    assert metadata["actor_email"] == "someone@x.io"
    assert metadata["cursor_used"] is False
    assert metadata["limit"] == 10
    assert metadata["format"] == "ndjson"


async def test_export_audit_write_failure_does_not_fail_export(
    client: Any,
    db_session: AsyncSession,
    app: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Simulates the audit writer's OWN session factory raising, per the writer's fail-open
    contract (M11) — the request's own `Depends(get_session)` call must still succeed, only
    the SECOND (fire-and-forget) sessionmaker() call fails (mirrors tests/admin_console_audit's
    `_fail_after_first_call` pattern; a blanket sessionmaker override would also break the
    route's own primary session, which is a different — untested-here — scenario)."""
    _owner, tid = await signup_tenant(client, tenant_name="Fail Open", email="fo@audit.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="fo-sub@audit.io")
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
            resp = await client.get(EXPORT, headers=auth(token))
            assert resp.status_code == 200, resp.text
            assert len(_ndjson_lines(resp.text)) == 1
            await _drain_fire_and_forget()
    finally:
        app.state.sessionmaker = real_sessionmaker

    assert call_count["n"] >= 2, "expected the request session + the fire-and-forget audit write"
    assert "failed to persist audit event" in caplog.text


# ---------------------------------------------------------------------------
# M12, R8 — bounded query timeout surfaces honestly
# ---------------------------------------------------------------------------


async def test_export_timeout_surfaces_honestly(
    client: Any, db_session: AsyncSession, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Slow Export", email="se@audit.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="se-sub@audit.io")
    await seed_audit_event(db_session, tenant_id=tid, created_at=_mins(1))

    orig_execute = AsyncSession.execute

    async def _flaky_execute(self: AsyncSession, statement: Any, *args: Any, **kwargs: Any) -> Any:
        compiled = str(statement).lstrip()
        if compiled.startswith("SELECT") and "audit_events" in compiled:
            raise TimeoutError("simulated export DB fault (test-only fault injection)")
        return await orig_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", _flaky_execute)

    resp = await client.get(EXPORT, headers=auth(token))

    assert_problem(resp, 504, "ERR_EXPORT_TIMEOUT")
    # No partial/truncated NDJSON body sent as if it were a complete page.
    assert resp.headers["content-type"] != "application/x-ndjson"


# ---------------------------------------------------------------------------
# M13 — purge mid-export is a silent, honest gap
# ---------------------------------------------------------------------------


async def test_export_purge_mid_export_is_silent_honest_gap(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Purge Mid", email="pm@audit.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="pm-sub@audit.io")
    row_ids = [
        await seed_audit_event(db_session, tenant_id=tid, action=f"ev.{i}", created_at=_mins(i))
        for i in range(1, 5)  # oldest(0)->newest(3): row_ids[3] newest
    ]

    page1 = await client.get(
        EXPORT, params={"format": "json", "limit": "2"}, headers=auth(token)
    )
    assert page1.status_code == 200, page1.text
    body1 = page1.json()
    assert body1["has_more"] is True
    cursor = body1["next_cursor"]

    # Simulate the retention sweeper purging one of the two remaining (un-issued) rows.
    # The test schema (Base.metadata.create_all) has no immutability trigger — a raw DELETE
    # here stands in for the sweeper's own `SET LOCAL app.audit_purge='on'`-guarded DELETE.
    await db_session.execute(text("DELETE FROM audit_events WHERE id = :id"), {"id": row_ids[0]})
    await db_session.commit()

    page2 = await client.get(
        EXPORT,
        params={"format": "json", "limit": "2", "cursor": cursor},
        headers=auth(token),
    )

    assert page2.status_code == 200, page2.text
    body2 = page2.json()
    ids2 = [i["id"] for i in body2["items"]]
    assert ids2 == [row_ids[1]], "the purged row is simply absent — no error, no gap marker"
    assert body2["has_more"] is False


# ---------------------------------------------------------------------------
# Boundary — empty result set
# ---------------------------------------------------------------------------


async def test_export_empty_result_set(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Empty Set", email="es@audit.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="es-sub@audit.io")

    resp = await client.get(EXPORT, headers=auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.text == ""
    assert resp.headers["x-audit-export-has-more"] == "false"

    resp_json = await client.get(EXPORT, params={"format": "json"}, headers=auth(token))
    assert resp_json.status_code == 200, resp_json.text
    assert resp_json.json() == {"items": [], "next_cursor": None, "has_more": False}


# ---------------------------------------------------------------------------
# Concurrency/retry-safety — duplicate cursor request is idempotent
# ---------------------------------------------------------------------------


async def test_export_duplicate_cursor_request_is_idempotent(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Dup Cursor", email="dc@audit.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="dc-sub@audit.io")
    for i in range(1, 6):
        await seed_audit_event(db_session, tenant_id=tid, action=f"ev.{i}", created_at=_mins(i))

    page1 = await client.get(
        EXPORT, params={"format": "json", "limit": "2"}, headers=auth(token)
    )
    assert page1.status_code == 200, page1.text
    cursor = page1.json()["next_cursor"]
    assert cursor

    await _drain_fire_and_forget()
    baseline = await count_audit_rows(db_session, action="audit.export", tenant_id=tid)

    retry_params = {"limit": "2", "cursor": cursor}
    retry1 = await client.get(EXPORT, params=retry_params, headers=auth(token))
    retry2 = await client.get(EXPORT, params=retry_params, headers=auth(token))

    assert retry1.status_code == 200, retry1.text
    assert retry2.status_code == 200, retry2.text
    assert retry1.text == retry2.text, "identical cursor+params must produce byte-identical bodies"
    assert retry1.headers["x-audit-export-has-more"] == retry2.headers["x-audit-export-has-more"]

    await _drain_fire_and_forget()
    after = await count_audit_rows(db_session, action="audit.export", tenant_id=tid)
    assert after == baseline + 2, "each successful read fires its own audit-of-export row (M11)"
