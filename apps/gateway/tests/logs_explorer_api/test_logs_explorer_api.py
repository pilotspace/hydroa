"""RED suite for GET /admin/logs + GET /admin/logs/{log_id} (logs-explorer-api TASK.md §3
— FROZEN @ v1).

Tenant-admin request-logs query API: read-only, cursor-paginated, filtered list + single-log
detail fetch over the existing (frozen) `request_logs` store. Frozen contract:
  - Permission.LOGS_READ gated: owner/admin/operator/superadmin -> 200/404;
    billing_admin/viewer/member -> 403.
  - Tenant-scoped: only the caller's own tenant_id rows, under every filter combination.
  - Keyset-cursor pagination ONLY (no offset) — (created_at, id) DESC, limit 1..100
    default 50.
  - Filters: since/until (ISO-8601 inclusive), model_id (exact), key_id (exact UUID),
    status (exact 3-digit code OR "success"/"error" bucket), cost_min/cost_max (decimal,
    inclusive; NULL cost_usd rows never match).
  - List rows are METADATA-ONLY (never request_body/response_body/guardrail_verdict).
  - Detail adds request_body/response_body/guardrail_verdict + request_id.
  - Unknown OR cross-tenant log_id -> identical 404 ERR_LOG_NOT_FOUND (no leak).
  - Bounded asyncio.timeout on the list query -> ERR_LOGS_QUERY_TIMEOUT (504) on expiry.

RED before BUILD: the route/permission/error-codes do not exist yet, so every
200-expecting scenario 404s/403s-wrong-shape/500s — the honest missing-implementation red.
DO NOT weaken these tests to make them pass; that is Build's job.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

from .conftest import (
    BASE,
    LIST_LOGS,
    auth,
    detail_url,
    mint_role_token,
    seed_request_log,
    signup_tenant,
)

# pytest asyncio_mode=auto: `async def test_*` runs without a marker.

_LIST_ITEM_FIELDS = (
    "id",
    "key_id",
    "team_id",
    "model_id",
    "status_code",
    "stream",
    "cached",
    "scrub_status",
    "truncated",
    "cost_usd",
    "created_at",
    "latency_ms",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
)

_PAYLOAD_FIELDS = ("request_body", "response_body", "guardrail_verdict")


def assert_problem(resp: Any, status: int, code: str) -> None:
    assert resp.status_code == status, f"expected {status}, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("code") == code, f"expected {code!r}, got {body.get('code')!r}: {body}"


def _mins(n: int) -> datetime.datetime:
    return BASE + datetime.timedelta(minutes=n)


# ---------------------------------------------------------------------------
# M1 — tenant isolation on list
# ---------------------------------------------------------------------------


async def test_list_returns_only_callers_tenant(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner_a, tid_a = await signup_tenant(client, tenant_name="Tenant A", email="a@logs.io")
    _owner_b, tid_b = await signup_tenant(client, tenant_name="Tenant B", email="b@logs.io")
    id_a1 = await seed_request_log(db_session, tenant_id=tid_a, created_at=_mins(1))
    id_a2 = await seed_request_log(db_session, tenant_id=tid_a, created_at=_mins(2))
    id_a3 = await seed_request_log(db_session, tenant_id=tid_a, created_at=_mins(3))
    await seed_request_log(db_session, tenant_id=tid_b, created_at=_mins(1))
    await seed_request_log(db_session, tenant_id=tid_b, created_at=_mins(2))
    token_a = mint_role_token(app, tenant_id=tid_a, role=Role.OWNER, email="sub-a@logs.io")

    resp = await client.get(LIST_LOGS, headers=auth(token_a))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = [item["id"] for item in body["items"]]
    assert ids == [id_a3, id_a2, id_a1], "expected exactly tenant A's 3 rows, newest-first"


# ---------------------------------------------------------------------------
# M2 — filters compose with AND
# ---------------------------------------------------------------------------


async def test_filters_compose_with_and(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Filter Co", email="f@logs.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="f-sub@logs.io")

    # Matches ALL three conditions.
    match_id = await seed_request_log(
        db_session,
        tenant_id=tid,
        model_id="gpt-4o",
        status_code=500,
        cost_usd=Decimal("0.05"),
        created_at=_mins(1),
    )
    # Fails model_id only.
    await seed_request_log(
        db_session,
        tenant_id=tid,
        model_id="claude-3",
        status_code=500,
        cost_usd=Decimal("0.05"),
        created_at=_mins(2),
    )
    # Fails status only (success, not error).
    await seed_request_log(
        db_session,
        tenant_id=tid,
        model_id="gpt-4o",
        status_code=200,
        cost_usd=Decimal("0.05"),
        created_at=_mins(3),
    )
    # Fails cost_min only.
    await seed_request_log(
        db_session,
        tenant_id=tid,
        model_id="gpt-4o",
        status_code=500,
        cost_usd=Decimal("0.001"),
        created_at=_mins(4),
    )

    resp = await client.get(
        LIST_LOGS,
        params={"model_id": "gpt-4o", "status": "error", "cost_min": "0.01"},
        headers=auth(token),
    )

    assert resp.status_code == 200, resp.text
    ids = [item["id"] for item in resp.json()["items"]]
    assert ids == [match_id], f"expected only the fully-matching row, got {ids}"


# ---------------------------------------------------------------------------
# M2 — since/until, key_id, cost_max filters narrow results (positive path — the
# malformed-input tests below only prove REJECTION, not that a well-formed filter
# actually narrows correctly)
# ---------------------------------------------------------------------------


async def test_since_until_key_id_cost_max_filters_narrow_results(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Time Key Cost", email="tkc@logs.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="tkc-sub@logs.io")
    target_key = str(uuid.uuid4())
    other_key = str(uuid.uuid4())

    in_range_id = await seed_request_log(
        db_session,
        tenant_id=tid,
        key_id=target_key,
        cost_usd=Decimal("0.02"),
        created_at=_mins(2),
    )
    # Outside the time window.
    await seed_request_log(
        db_session, tenant_id=tid, key_id=target_key, cost_usd=Decimal("0.02"), created_at=_mins(10)
    )
    # Wrong key_id.
    await seed_request_log(
        db_session, tenant_id=tid, key_id=other_key, cost_usd=Decimal("0.02"), created_at=_mins(2)
    )
    # Exceeds cost_max.
    await seed_request_log(
        db_session, tenant_id=tid, key_id=target_key, cost_usd=Decimal("5.00"), created_at=_mins(2)
    )

    resp = await client.get(
        LIST_LOGS,
        params={
            "since": _mins(1).isoformat(),
            "until": _mins(3).isoformat(),
            "key_id": target_key,
            "cost_max": "1.00",
        },
        headers=auth(token),
    )

    assert resp.status_code == 200, resp.text
    ids = [item["id"] for item in resp.json()["items"]]
    assert ids == [in_range_id], f"expected only the row matching all 4 filters, got {ids}"


# ---------------------------------------------------------------------------
# M3 — list rows are metadata-only
# ---------------------------------------------------------------------------


async def test_list_rows_are_metadata_only(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Meta Only", email="mo@logs.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="mo-sub@logs.io")
    await seed_request_log(
        db_session,
        tenant_id=tid,
        request_body={"messages": [{"role": "user", "content": "hi"}]},
        response_body={"messages": [{"role": "assistant", "content": "hello"}]},
        guardrail_verdict={"blocked": False},
        created_at=_mins(1),
    )

    resp = await client.get(LIST_LOGS, headers=auth(token))

    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    for field in _PAYLOAD_FIELDS:
        assert field not in item, f"list item leaked payload-bearing field {field!r}: {item}"
    assert set(item.keys()) == set(_LIST_ITEM_FIELDS), item.keys()


# ---------------------------------------------------------------------------
# M4 — detail returns the full row
# ---------------------------------------------------------------------------


async def test_detail_returns_full_row(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Detail Co", email="d@logs.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="d-sub@logs.io")
    req_body = {"messages": [{"role": "user", "content": "hi"}]}
    resp_body = {"messages": [{"role": "assistant", "content": "hello"}]}
    row_id = await seed_request_log(
        db_session,
        tenant_id=tid,
        request_body=req_body,
        response_body=resp_body,
        created_at=_mins(1),
    )

    resp = await client.get(detail_url(row_id), headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == row_id
    assert body["request_body"] == req_body
    assert body["response_body"] == resp_body
    assert body["guardrail_verdict"] is None
    for field in _LIST_ITEM_FIELDS:
        assert field in body, f"detail missing metadata field {field!r}: {body}"


# ---------------------------------------------------------------------------
# R10 — unknown log id is 404
# ---------------------------------------------------------------------------


async def test_unknown_log_id_is_404(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Unknown Id", email="u@logs.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="u-sub@logs.io")

    resp = await client.get(detail_url(str(uuid.uuid4())), headers=auth(token))

    assert_problem(resp, 404, "ERR_LOG_NOT_FOUND")


# ---------------------------------------------------------------------------
# M5, R10 — cross-tenant log id is the SAME 404
# ---------------------------------------------------------------------------


async def test_cross_tenant_log_id_is_same_404(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner_a, tid_a = await signup_tenant(client, tenant_name="Cross A", email="ca@logs.io")
    _owner_b, tid_b = await signup_tenant(client, tenant_name="Cross B", email="cb@logs.io")
    row_id_b = await seed_request_log(db_session, tenant_id=tid_b, created_at=_mins(1))
    token_a = mint_role_token(app, tenant_id=tid_a, role=Role.OWNER, email="ca-sub@logs.io")

    resp_unknown = await client.get(detail_url(str(uuid.uuid4())), headers=auth(token_a))
    resp_cross = await client.get(detail_url(row_id_b), headers=auth(token_a))

    assert_problem(resp_unknown, 404, "ERR_LOG_NOT_FOUND")
    assert_problem(resp_cross, 404, "ERR_LOG_NOT_FOUND")
    assert resp_unknown.json() == resp_cross.json(), "unknown vs cross-tenant must be byte-identical"


# ---------------------------------------------------------------------------
# R1 — member forbidden on list
# ---------------------------------------------------------------------------


async def test_member_forbidden_on_list(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Member Co", email="m@logs.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.MEMBER, email="m-sub@logs.io")
    await seed_request_log(db_session, tenant_id=tid, created_at=_mins(1))

    resp = await client.get(LIST_LOGS, headers=auth(token))

    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")


# ---------------------------------------------------------------------------
# M6 — billing_admin + viewer forbidden on list
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [Role.BILLING_ADMIN, Role.VIEWER])
async def test_billing_admin_and_viewer_forbidden_on_list(
    client: Any, db_session: AsyncSession, app: Any, role: Role
) -> None:
    _owner, tid = await signup_tenant(
        client, tenant_name=f"Denied {role}", email=f"denied-{role}@logs.io"
    )
    token = mint_role_token(app, tenant_id=tid, role=role, email=f"denied-sub-{role}@logs.io")

    resp = await client.get(LIST_LOGS, headers=auth(token))

    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")


# ---------------------------------------------------------------------------
# R9 — member forbidden on detail
# ---------------------------------------------------------------------------


async def test_member_forbidden_on_detail(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Member Detail", email="md@logs.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.MEMBER, email="md-sub@logs.io")
    row_id = await seed_request_log(db_session, tenant_id=tid, created_at=_mins(1))

    resp = await client.get(detail_url(row_id), headers=auth(token))

    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")


# ---------------------------------------------------------------------------
# M6 — owner/admin/operator/superadmin all pass LOGS_READ
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("role", [Role.OWNER, Role.ADMIN, Role.OPERATOR, Role.SUPERADMIN])
async def test_all_logs_read_roles_pass(
    client: Any, db_session: AsyncSession, app: Any, role: Role
) -> None:
    _owner, tid = await signup_tenant(
        client, tenant_name=f"Passes {role}", email=f"passes-{role}@logs.io"
    )
    await seed_request_log(db_session, tenant_id=tid, created_at=_mins(1))
    token = mint_role_token(app, tenant_id=tid, role=role, email=f"passes-sub-{role}@logs.io")

    resp = await client.get(LIST_LOGS, headers=auth(token))

    assert resp.status_code == 200, f"role={role} expected 200, got {resp.status_code}: {resp.text}"
    assert len(resp.json()["items"]) == 1


async def test_superadmin_sees_only_own_tenant(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner_a, tid_a = await signup_tenant(client, tenant_name="SA Tenant A", email="saa@logs.io")
    _owner_b, tid_b = await signup_tenant(client, tenant_name="SA Tenant B", email="sab@logs.io")
    await seed_request_log(db_session, tenant_id=tid_a, created_at=_mins(1))
    await seed_request_log(db_session, tenant_id=tid_b, created_at=_mins(1))
    sa_token = mint_role_token(app, tenant_id=tid_a, role=Role.SUPERADMIN, email="sa@logs.io")

    resp = await client.get(LIST_LOGS, headers=auth(sa_token))

    assert resp.status_code == 200, resp.text
    ids = {item["id"] for item in resp.json()["items"]}
    assert len(ids) == 1, "a superadmin identity must stay scoped to its own tenant (dormant authorize_tenant_scope)"


# ---------------------------------------------------------------------------
# R2 — invalid limit rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("limit", ["0", "101", "abc"])
async def test_invalid_limit_rejected(
    client: Any, db_session: AsyncSession, app: Any, limit: str
) -> None:
    _owner, tid = await signup_tenant(
        client, tenant_name=f"Limit {limit}", email=f"lim-{limit}@logs.io"
    )
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email=f"lim-sub-{limit}@logs.io")

    resp = await client.get(LIST_LOGS, params={"limit": limit}, headers=auth(token))

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# edge-boundary — boundary limits accepted
# ---------------------------------------------------------------------------


async def test_boundary_limits_accepted(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Boundary Co", email="bd@logs.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="bd-sub@logs.io")
    for i in range(1, 4):
        await seed_request_log(db_session, tenant_id=tid, created_at=_mins(i))

    resp1 = await client.get(LIST_LOGS, params={"limit": "1"}, headers=auth(token))
    resp100 = await client.get(LIST_LOGS, params={"limit": "100"}, headers=auth(token))

    assert resp1.status_code == 200, resp1.text
    assert len(resp1.json()["items"]) == 1
    assert resp1.json()["has_more"] is True

    assert resp100.status_code == 200, resp100.text
    assert len(resp100.json()["items"]) == 3
    assert resp100.json()["has_more"] is False


# ---------------------------------------------------------------------------
# R3 — malformed cursor rejected
# ---------------------------------------------------------------------------


async def test_malformed_cursor_rejected(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Bad Cursor", email="bc@logs.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="bc-sub@logs.io")

    resp = await client.get(
        LIST_LOGS, params={"cursor": "not-valid-base64-or-wrong-shape"}, headers=auth(token)
    )

    assert_problem(resp, 422, "ERR_CURSOR_INVALID")


# ---------------------------------------------------------------------------
# R4 — malformed since/until rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["since", "until"])
async def test_malformed_since_until_rejected(
    client: Any, db_session: AsyncSession, app: Any, field: str
) -> None:
    _owner, tid = await signup_tenant(
        client, tenant_name=f"Bad {field}", email=f"bad-{field}@logs.io"
    )
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email=f"bad-sub-{field}@logs.io")

    resp = await client.get(LIST_LOGS, params={field: "not-a-date"}, headers=auth(token))

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# R5 — inverted time range rejected
# ---------------------------------------------------------------------------


async def test_inverted_time_range_rejected(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Inverted", email="inv@logs.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="inv-sub@logs.io")

    resp = await client.get(
        LIST_LOGS,
        params={"since": "2026-08-01T00:00:00Z", "until": "2026-01-01T00:00:00Z"},
        headers=auth(token),
    )

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# R6 — invalid status filter rejected
# ---------------------------------------------------------------------------


async def test_invalid_status_rejected(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Bad Status", email="bs@logs.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="bs-sub@logs.io")

    resp = await client.get(LIST_LOGS, params={"status": "maybe"}, headers=auth(token))

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# M2 (dual-mode) — status accepts both exact code and bucket keyword
# ---------------------------------------------------------------------------


async def test_status_dual_mode_exact_and_bucket(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Dual Mode", email="dm@logs.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="dm-sub@logs.io")
    id_200 = await seed_request_log(db_session, tenant_id=tid, status_code=200, created_at=_mins(1))
    id_429 = await seed_request_log(db_session, tenant_id=tid, status_code=429, created_at=_mins(2))
    id_500 = await seed_request_log(db_session, tenant_id=tid, status_code=500, created_at=_mins(3))

    resp_exact = await client.get(LIST_LOGS, params={"status": "429"}, headers=auth(token))
    resp_error = await client.get(LIST_LOGS, params={"status": "error"}, headers=auth(token))
    resp_success = await client.get(LIST_LOGS, params={"status": "success"}, headers=auth(token))

    assert {i["id"] for i in resp_exact.json()["items"]} == {id_429}
    assert {i["id"] for i in resp_error.json()["items"]} == {id_429, id_500}
    assert {i["id"] for i in resp_success.json()["items"]} == {id_200}


# ---------------------------------------------------------------------------
# R7 — invalid cost bounds rejected
# ---------------------------------------------------------------------------


async def test_malformed_key_id_rejected(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Bad Key Id", email="bki@logs.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="bki-sub@logs.io")

    resp = await client.get(
        LIST_LOGS, params={"key_id": "not-a-uuid"}, headers=auth(token)
    )

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


async def test_invalid_cost_bounds_rejected(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Bad Cost", email="bcst@logs.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="bcst-sub@logs.io")

    resp = await client.get(
        LIST_LOGS, params={"cost_min": "not-a-number"}, headers=auth(token)
    )

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# edge-partial-data — null cost_usd rows excluded from cost filter
# ---------------------------------------------------------------------------


async def test_null_cost_rows_excluded_from_cost_filter(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Null Cost", email="nc@logs.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="nc-sub@logs.io")
    await seed_request_log(
        db_session,
        tenant_id=tid,
        cost_usd=None,
        scrub_status="scrub_failed_metadata_only",
        created_at=_mins(1),
    )
    priced_id = await seed_request_log(
        db_session, tenant_id=tid, cost_usd=Decimal("0.05"), created_at=_mins(2)
    )

    resp = await client.get(LIST_LOGS, params={"cost_min": "0.00"}, headers=auth(token))

    assert resp.status_code == 200, resp.text
    ids = [item["id"] for item in resp.json()["items"]]
    assert ids == [priced_id], "a NULL cost_usd row must never match a cost filter"


# ---------------------------------------------------------------------------
# R8, M9 — bounded query timeout maps to a dedicated error
# ---------------------------------------------------------------------------


async def test_query_timeout_maps_to_504(
    client: Any, db_session: AsyncSession, app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Slow Logs", email="sl@logs.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="sl-sub@logs.io")
    await seed_request_log(db_session, tenant_id=tid, created_at=_mins(1))

    orig_execute = AsyncSession.execute

    async def _flaky_execute(self: AsyncSession, statement: Any, *args: Any, **kwargs: Any) -> Any:
        compiled = str(statement).lstrip()
        if compiled.startswith("SELECT") and "request_logs" in compiled:
            raise TimeoutError("simulated logs-query DB fault (test-only fault injection)")
        return await orig_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", _flaky_execute)

    resp = await client.get(LIST_LOGS, headers=auth(token))

    assert_problem(resp, 504, "ERR_LOGS_QUERY_TIMEOUT")


# ---------------------------------------------------------------------------
# edge-boundary — empty result set is 200 with an empty list
# ---------------------------------------------------------------------------


async def test_empty_result_is_200_empty_list(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Empty Co", email="e@logs.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="e-sub@logs.io")
    await seed_request_log(db_session, tenant_id=tid, model_id="a-model", created_at=_mins(1))

    resp = await client.get(
        LIST_LOGS, params={"model_id": "nonexistent-model"}, headers=auth(token)
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"items": [], "next_cursor": None, "has_more": False}


# ---------------------------------------------------------------------------
# edge-boundary — last page has no next cursor
# ---------------------------------------------------------------------------


async def test_last_page_has_no_next_cursor(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Last Page", email="lp@logs.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="lp-sub@logs.io")
    ids = [
        await seed_request_log(db_session, tenant_id=tid, created_at=_mins(i)) for i in range(1, 4)
    ]

    resp = await client.get(LIST_LOGS, params={"limit": "50"}, headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["has_more"] is False
    assert body["next_cursor"] is None
    assert {i["id"] for i in body["items"]} == set(ids)


# ---------------------------------------------------------------------------
# edge-concurrency — concurrent insert during keyset walk is never dup/skipped
# ---------------------------------------------------------------------------


async def test_concurrent_insert_during_keyset_walk_is_safe(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Keyset Concurrent", email="kc@logs.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="kc-sub@logs.io")
    row_ids = [
        await seed_request_log(db_session, tenant_id=tid, created_at=_mins(i)) for i in range(1, 6)
    ]  # row_ids[0]=oldest ... row_ids[4]=newest

    page1 = await client.get(LIST_LOGS, params={"limit": "2"}, headers=auth(token))
    assert page1.status_code == 200, page1.text
    body1 = page1.json()
    assert [i["id"] for i in body1["items"]] == [row_ids[4], row_ids[3]]
    assert body1["has_more"] is True
    cursor = body1["next_cursor"]
    assert cursor

    # Concurrent write strictly AFTER page 1 was issued, newer than every seeded row.
    new_id = await seed_request_log(db_session, tenant_id=tid, created_at=_mins(10))

    page2 = await client.get(
        LIST_LOGS, params={"limit": "2", "cursor": cursor}, headers=auth(token)
    )
    assert page2.status_code == 200, page2.text
    ids2 = [i["id"] for i in page2.json()["items"]]
    assert new_id not in ids2, "a concurrent insert must never appear mid-walk"
    assert ids2 == [row_ids[2], row_ids[1]], "page 2 must continue exactly where page 1 left off"


# ---------------------------------------------------------------------------
# edge-partial-failure — metadata-only (scrub-failed) rows honest passthrough
# ---------------------------------------------------------------------------


async def test_metadata_only_rows_listed_and_detailed_honestly(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Scrub Failed", email="sf@logs.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="sf-sub@logs.io")
    row_id = await seed_request_log(
        db_session,
        tenant_id=tid,
        request_body=None,
        response_body=None,
        scrub_status="scrub_failed_metadata_only",
        created_at=_mins(1),
    )

    list_resp = await client.get(LIST_LOGS, headers=auth(token))
    detail_resp = await client.get(detail_url(row_id), headers=auth(token))

    assert list_resp.status_code == 200, list_resp.text
    list_item = next(i for i in list_resp.json()["items"] if i["id"] == row_id)
    assert list_item["scrub_status"] == "scrub_failed_metadata_only"

    assert detail_resp.status_code == 200, detail_resp.text
    detail_body = detail_resp.json()
    assert detail_body["request_body"] is None
    assert detail_body["response_body"] is None


# ---------------------------------------------------------------------------
# After — PATCH-adjacent write surfaces (capture path) untouched
# ---------------------------------------------------------------------------


async def test_capture_write_path_byte_identical(
    client: Any, db_session: AsyncSession, app: Any
) -> None:
    """GET/PUT /admin/capture and the capture_writer persist path must be byte-identical
    to before this task's purely additive, read-only endpoints (After — §2)."""
    _owner, tid = await signup_tenant(client, tenant_name="Capture Untouched", email="cu@logs.io")
    token = mint_role_token(app, tenant_id=tid, role=Role.OWNER, email="cu-sub@logs.io")

    get_resp = await client.get("/admin/capture", headers=auth(token))
    assert get_resp.status_code == 200, get_resp.text
    assert get_resp.json() == {"enabled": False}

    put_resp = await client.put(
        "/admin/capture", json={"enabled": True}, headers=auth(token)
    )
    assert put_resp.status_code == 200, put_resp.text
    assert put_resp.json() == {"enabled": True}

    from gateway.logs.application.capture_writer import persist_request_log

    await persist_request_log(
        session_factory=app.state.sessionmaker,
        tenant_id=uuid.UUID(tid),
        key_id=uuid.uuid4(),
        model="openai/gpt-4o-mini",
        request_body={"messages": [{"role": "user", "content": "hi"}]},
        response_body={"messages": [{"role": "assistant", "content": "hello"}]},
        status=200,
        stream=False,
        cached=False,
        guardrail_configs={},
        timeout_seconds=3.0,
        max_field_bytes=10_000,
        max_body_bytes=100_000,
    )

    resp = await client.get(LIST_LOGS, headers=auth(token))
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["items"]) == 1, "the direct capture-write path must still land a row"
