"""RED suite for alerts-events-viewer (TASK.md §4).

GET /admin/alerts is a thin, owner/admin-scoped read over alert_events. Frozen contract (§3):
  - 200 -> {items:[{id,event_type,payload,created_at,delivered,delivered_at}], total:int}
  - visibility WHERE tenant_id = :tid OR tenant_id IS NULL  (Tin-approved: tenant soft-budget
    rows + platform system events), ORDER BY created_at DESC, id DESC, LIMIT/OFFSET.
  - limit 1..100 (default 50), offset >=0 (default 0); bad -> 422 ERR_PAYLOAD_INVALID.
  - member role -> 403; READ-ONLY (never mutates delivered_at / row count).

RED before BUILD: the route does not exist yet, so success cases get 404 — the honest
missing-implementation red.
"""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

from .conftest import (
    ALERTS,
    BASE,
    auth,
    mint_role_token,
    seed_alert,
    signup_tenant,
)

# pytest asyncio_mode=auto: `async def test_*` runs without a marker.


def assert_problem(resp: Any, status: int, code: str) -> None:
    assert resp.status_code == status, f"expected {status}, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("code") == code, f"expected {code!r}, got {body.get('code')!r}: {body}"


def _mins(n: int) -> datetime.datetime:
    return BASE + datetime.timedelta(minutes=n)


# ---------------------------------------------------------------------------
# Owner reads tenant alert history newest-first
# ---------------------------------------------------------------------------
async def test_owner_reads_history_newest_first(client: Any, db_session: AsyncSession) -> None:
    token, tid = await signup_tenant(client, tenant_name="Alerts A", email="a@alerts.io")
    id1 = await seed_alert(
        db_session, tenant_id=tid, event_type="soft_budget_exceeded", created_at=_mins(1)
    )
    id2 = await seed_alert(
        db_session, tenant_id=tid, event_type="soft_budget_exceeded", created_at=_mins(2)
    )
    id3 = await seed_alert(
        db_session, tenant_id=tid, event_type="soft_budget_exceeded", created_at=_mins(3)
    )

    resp = await client.get(ALERTS, headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert [item["id"] for item in body["items"]] == [id3, id2, id1]  # created_at DESC
    first = body["items"][0]
    for field in ("id", "event_type", "payload", "created_at", "delivered", "delivered_at"):
        assert field in first, f"missing field {field!r} in item: {first}"


# ---------------------------------------------------------------------------
# System (NULL-tenant) events are visible alongside tenant rows
# ---------------------------------------------------------------------------
async def test_system_null_tenant_events_visible(client: Any, db_session: AsyncSession) -> None:
    token, tid = await signup_tenant(client, tenant_name="Alerts B", email="b@alerts.io")
    await seed_alert(
        db_session, tenant_id=tid, event_type="soft_budget_exceeded", created_at=_mins(1)
    )
    await seed_alert(
        db_session, tenant_id=None, event_type="circuit_breaker_open", created_at=_mins(2)
    )
    await seed_alert(
        db_session, tenant_id=None, event_type="upstream_health_fail", created_at=_mins(3)
    )

    resp = await client.get(ALERTS, headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    types = {item["event_type"] for item in body["items"]}
    assert types == {"soft_budget_exceeded", "circuit_breaker_open", "upstream_health_fail"}


# ---------------------------------------------------------------------------
# Another tenant's rows never leak
# ---------------------------------------------------------------------------
async def test_other_tenant_rows_absent(client: Any, db_session: AsyncSession) -> None:
    token_t, tid_t = await signup_tenant(client, tenant_name="Alerts T", email="t@alerts.io")
    _token_u, tid_u = await signup_tenant(client, tenant_name="Alerts U", email="u@alerts.io")
    await seed_alert(
        db_session, tenant_id=tid_t, event_type="soft_budget_exceeded", created_at=_mins(1)
    )
    leak_id = await seed_alert(
        db_session, tenant_id=tid_u, event_type="soft_budget_exceeded", created_at=_mins(2)
    )

    resp = await client.get(ALERTS, headers=auth(token_t))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert leak_id not in {item["id"] for item in body["items"]}
    assert body["total"] == 1


# ---------------------------------------------------------------------------
# delivered flag is derived from delivered_at
# ---------------------------------------------------------------------------
async def test_delivered_flag_derived(client: Any, db_session: AsyncSession) -> None:
    token, tid = await signup_tenant(client, tenant_name="Alerts D", email="d@alerts.io")
    await seed_alert(
        db_session,
        tenant_id=tid,
        event_type="soft_budget_exceeded",
        created_at=_mins(2),
        delivered_at=_mins(5),
    )
    await seed_alert(
        db_session,
        tenant_id=tid,
        event_type="soft_budget_exceeded",
        created_at=_mins(1),
        delivered_at=None,
    )

    resp = await client.get(ALERTS, headers=auth(token))

    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    # newest-first: delivered row (created_at +2) is first, undelivered (+1) second
    assert items[0]["delivered"] is True
    assert items[0]["delivered_at"] is not None
    assert items[1]["delivered"] is False
    assert items[1]["delivered_at"] is None


# ---------------------------------------------------------------------------
# Pagination with limit and offset
# ---------------------------------------------------------------------------
async def test_pagination_limit_offset(client: Any, db_session: AsyncSession) -> None:
    token, tid = await signup_tenant(client, tenant_name="Alerts P", email="p@alerts.io")
    ids = [
        await seed_alert(
            db_session, tenant_id=tid, event_type="soft_budget_exceeded", created_at=_mins(i)
        )
        for i in range(1, 6)  # 5 rows, created_at +1..+5
    ]
    # newest-first full order = ids[4], ids[3], ids[2], ids[1], ids[0]
    resp = await client.get(ALERTS, params={"limit": 2, "offset": 2}, headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 5
    assert [item["id"] for item in body["items"]] == [ids[2], ids[1]]  # 3rd & 4th newest


# ---------------------------------------------------------------------------
# Default pagination caps at 50
# ---------------------------------------------------------------------------
async def test_default_pagination(client: Any, db_session: AsyncSession) -> None:
    token, tid = await signup_tenant(client, tenant_name="Alerts DP", email="dp@alerts.io")
    for i in range(60):
        await seed_alert(
            db_session, tenant_id=tid, event_type="soft_budget_exceeded", created_at=_mins(i)
        )

    resp = await client.get(ALERTS, headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 50
    assert body["total"] == 60


# ---------------------------------------------------------------------------
# Read is side-effect free
# ---------------------------------------------------------------------------
async def test_read_is_side_effect_free(client: Any, db_session: AsyncSession) -> None:
    token, tid = await signup_tenant(client, tenant_name="Alerts S", email="s@alerts.io")
    row_id = await seed_alert(
        db_session, tenant_id=tid, event_type="soft_budget_exceeded", delivered_at=None
    )

    before_count = (await db_session.execute(text("SELECT COUNT(*) FROM alert_events"))).fetchone()[
        0
    ]

    resp = await client.get(ALERTS, headers=auth(token))
    assert resp.status_code == 200, resp.text

    # delivered_at must still be NULL; the SAME row id must still exist; the total row
    # count must be unchanged (guards against a DELETE+re-INSERT masquerading as a read).
    after = (
        await db_session.execute(
            text("SELECT delivered_at FROM alert_events WHERE id = :id"),
            {"id": row_id},
        )
    ).fetchone()
    assert after is not None  # the original row survives (not deleted/replaced)
    assert after[0] is None
    after_count = (await db_session.execute(text("SELECT COUNT(*) FROM alert_events"))).fetchone()[
        0
    ]
    assert after_count == before_count


# ---------------------------------------------------------------------------
# Admin role (not just owner) is allowed  (refute EG-3)
# ---------------------------------------------------------------------------
async def test_admin_role_allowed(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Alerts AD", email="ad-owner@alerts.io")
    admin_jwt = await mint_role_token(
        app, db_session, tenant_id=tid, role=Role.ADMIN, email="ad-admin@alerts.io"
    )
    await seed_alert(db_session, tenant_id=tid, event_type="soft_budget_exceeded")

    resp = await client.get(ALERTS, headers=auth(admin_jwt))

    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 1


# ---------------------------------------------------------------------------
# Combined visibility: own + NULL visible, OTHER tenant hidden, total = own+NULL
# (refute EG-5 — the security-critical relaxation, all three branches at once)
# ---------------------------------------------------------------------------
async def test_combined_visibility(client: Any, db_session: AsyncSession) -> None:
    token_t, tid_t = await signup_tenant(client, tenant_name="Alerts CV", email="cv-t@alerts.io")
    _token_u, tid_u = await signup_tenant(client, tenant_name="Alerts CVU", email="cv-u@alerts.io")
    own_id = await seed_alert(
        db_session, tenant_id=tid_t, event_type="soft_budget_exceeded", created_at=_mins(1)
    )
    sys_id = await seed_alert(
        db_session, tenant_id=None, event_type="circuit_breaker_open", created_at=_mins(2)
    )
    other_id = await seed_alert(
        db_session, tenant_id=tid_u, event_type="soft_budget_exceeded", created_at=_mins(3)
    )

    resp = await client.get(ALERTS, headers=auth(token_t))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {item["id"] for item in body["items"]}
    assert own_id in ids  # own tenant row visible
    assert sys_id in ids  # NULL-tenant platform row visible
    assert other_id not in ids  # a DIFFERENT tenant's row is NEVER visible
    assert body["total"] == 2  # own + NULL only — the other tenant's row is not counted


# ---------------------------------------------------------------------------
# ORDER BY id DESC tiebreaker for equal created_at  (refute EG-4)
# ---------------------------------------------------------------------------
async def test_id_desc_tiebreak(client: Any, db_session: AsyncSession) -> None:
    token, tid = await signup_tenant(client, tenant_name="Alerts TB", email="tb@alerts.io")
    same = _mins(7)
    id_a = await seed_alert(
        db_session, tenant_id=tid, event_type="soft_budget_exceeded", created_at=same
    )
    id_b = await seed_alert(
        db_session, tenant_id=tid, event_type="soft_budget_exceeded", created_at=same
    )

    resp = await client.get(ALERTS, headers=auth(token))

    assert resp.status_code == 200, resp.text
    returned = [item["id"] for item in resp.json()["items"]]
    # equal created_at → deterministic id DESC tiebreak (stable pagination)
    assert returned == sorted([id_a, id_b], reverse=True)


# ---------------------------------------------------------------------------
# Reject non-integer offset  (refute EG-1)
# ---------------------------------------------------------------------------
async def test_reject_non_integer_offset(client: Any, db_session: AsyncSession) -> None:
    token, _tid = await signup_tenant(client, tenant_name="Alerts NO", email="no@alerts.io")
    resp = await client.get(ALERTS, params={"offset": "abc"}, headers=auth(token))
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# Missing Bearer is rejected (no anonymous read)  (refute EG-2)
# ---------------------------------------------------------------------------
async def test_missing_bearer_denied(client: Any, db_session: AsyncSession) -> None:
    resp = await client.get(ALERTS)
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# Reject limit out of range
# ---------------------------------------------------------------------------
async def test_reject_limit_out_of_range(client: Any, db_session: AsyncSession) -> None:
    token, _tid = await signup_tenant(client, tenant_name="Alerts L", email="l@alerts.io")
    for bad in ("0", "101", "abc"):
        resp = await client.get(ALERTS, params={"limit": bad}, headers=auth(token))
        assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# Reject negative offset
# ---------------------------------------------------------------------------
async def test_reject_negative_offset(client: Any, db_session: AsyncSession) -> None:
    token, _tid = await signup_tenant(client, tenant_name="Alerts O", email="o@alerts.io")
    resp = await client.get(ALERTS, params={"offset": "-1"}, headers=auth(token))
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# Member role is denied
# ---------------------------------------------------------------------------
async def test_member_denied(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Alerts M", email="m-owner@alerts.io")
    member_jwt = await mint_role_token(
        app, db_session, tenant_id=tid, role=Role.MEMBER, email="m-member@alerts.io"
    )

    resp = await client.get(ALERTS, headers=auth(member_jwt))

    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")
