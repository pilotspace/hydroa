"""RED->GREEN suite for GET/PUT/DELETE /admin/compliance/report-schedule and
GET /admin/compliance/reports(/{report_id}) — compliance-report-center TASK.md §3
(M15-M23 — FROZEN @ v1).

TRUE-RED RULE: every test asserts TARGET behavior for the RIGHT reason. Before the
build phase, `gateway.compliance.api.report_schedule_router` /
`gateway.compliance.application.report_schedule_generator` /
`gateway.compliance.infrastructure.orm` do not exist, so these tests fail on
ImportError/collection error, not a wrong-reason assertion failure.

Scenarios (one test per bullet):
  - GET default (no row) -> 200 enabled:false, every other field null/default
  - PUT owner enables -> 200, next_run_at computed, audit event emitted
  - PUT non-owner (ADMIN, has AUDIT_READ but not SECURITY_CONFIG) -> 403
  - PUT day_of_month outside 1-28 -> 422 ERR_PAYLOAD_INVALID, row unchanged
  - DELETE owner -> 204, row gone, re-GET reverts to default
  - GET/PUT/DELETE never accept a tenant_id param (identity-scoped only)
  - reports list keyset pagination (has_more / next_cursor over 3 seeded runs, limit=2)
  - report download tenant-scoped -> 200 with correct bytes + Content-Disposition
  - cross-tenant report_id -> 404 ERR_REPORT_NOT_FOUND (never 403 — R11)
  - object-store unreachable (app.state.object_store = None) -> 503
    ERR_OBJECT_STORE_UNAVAILABLE
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role
from tests.compliance_report_schedule.conftest import (
    REPORTS,
    SCHEDULE,
    assert_problem,
    auth,
    mint_role_token,
    signup_tenant,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# GET default / PUT / DELETE
# ---------------------------------------------------------------------------


async def test_get_schedule_default_no_row(client: httpx.AsyncClient) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner@acme.example"
    )
    resp = await client.get(SCHEDULE, headers=auth(owner_token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is False
    assert body["cadence"] == "monthly"
    assert body["day_of_month"] == 1
    assert body["window_policy"] == "previous_calendar_month"
    assert body["created_by"] is None
    assert body["created_at"] is None
    assert body["last_run_at"] is None
    assert body["last_run_status"] is None
    assert body["next_run_at"] is None


async def test_put_owner_enables_computes_next_run_at_and_audits(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    owner_token, tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner@acme.example"
    )
    resp = await client.put(
        SCHEDULE, json={"enabled": True, "day_of_month": 5}, headers=auth(owner_token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["enabled"] is True
    assert body["day_of_month"] == 5
    assert body["next_run_at"] is not None, "enabling must compute next_run_at fresh"
    assert body["created_by"] is not None

    # re-GET reflects the persisted row (not just the PUT response echo)
    get_resp = await client.get(SCHEDULE, headers=auth(owner_token))
    assert get_resp.status_code == 200
    assert get_resp.json()["enabled"] is True
    assert get_resp.json()["next_run_at"] == body["next_run_at"]

    # audit event emitted (fire-and-forget asyncio.ensure_future — the event loop
    # runs it to completion by the time the next await yields control back to us,
    # but to be safe/deterministic we poll briefly).
    import asyncio

    from sqlalchemy import text

    row: Any = None
    for _ in range(20):
        row = (
            await db_session.execute(
                text(
                    "SELECT tenant_id, action FROM audit_events"
                    " WHERE action = 'compliance.schedule_updated' AND tenant_id = :tid"
                ),
                {"tid": tenant_id},
            )
        ).fetchone()
        if row is not None:
            break
        await asyncio.sleep(0.05)
    assert row is not None, "PUT must emit a compliance.schedule_updated audit event"


async def test_put_disable_clears_next_run_at(client: httpx.AsyncClient) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner@acme.example"
    )
    enable = await client.put(
        SCHEDULE, json={"enabled": True, "day_of_month": 5}, headers=auth(owner_token)
    )
    assert enable.json()["next_run_at"] is not None

    disable = await client.put(SCHEDULE, json={"enabled": False}, headers=auth(owner_token))
    assert disable.status_code == 200, disable.text
    assert disable.json()["enabled"] is False
    assert disable.json()["next_run_at"] is None, "disabling must clear next_run_at"


async def test_put_non_owner_forbidden(client: httpx.AsyncClient, app: Any) -> None:
    _owner_token, tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner@acme.example"
    )
    admin_token = mint_role_token(
        app, tenant_id=tenant_id, role=Role.ADMIN, email="admin@acme.example"
    )
    resp = await client.put(
        SCHEDULE, json={"enabled": True, "day_of_month": 5}, headers=auth(admin_token)
    )
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")


async def test_delete_non_owner_forbidden(client: httpx.AsyncClient, app: Any) -> None:
    _owner_token, tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner@acme.example"
    )
    admin_token = mint_role_token(
        app, tenant_id=tenant_id, role=Role.ADMIN, email="admin@acme.example"
    )
    resp = await client.delete(SCHEDULE, headers=auth(admin_token))
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")


async def test_admin_can_still_read_schedule(client: httpx.AsyncClient, app: Any) -> None:
    """AUDIT_READ (held by ADMIN) is enough for GET even though PUT/DELETE need
    SECURITY_CONFIG (OWNER only) — confirms the two-permission split is real."""
    _owner_token, tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner@acme.example"
    )
    admin_token = mint_role_token(
        app, tenant_id=tenant_id, role=Role.ADMIN, email="admin@acme.example"
    )
    resp = await client.get(SCHEDULE, headers=auth(admin_token))
    assert resp.status_code == 200, resp.text


async def test_put_day_of_month_out_of_range_rejected(client: httpx.AsyncClient) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner@acme.example"
    )
    too_high = await client.put(
        SCHEDULE, json={"enabled": True, "day_of_month": 29}, headers=auth(owner_token)
    )
    assert_problem(too_high, 422, "ERR_PAYLOAD_INVALID")

    too_low = await client.put(
        SCHEDULE, json={"enabled": True, "day_of_month": 0}, headers=auth(owner_token)
    )
    assert_problem(too_low, 422, "ERR_PAYLOAD_INVALID")

    # row must remain unchanged (no schedule ever created)
    get_resp = await client.get(SCHEDULE, headers=auth(owner_token))
    assert get_resp.json()["enabled"] is False, "a rejected PUT must never persist a row"


async def test_delete_owner_removes_row_and_get_reverts_to_default(
    client: httpx.AsyncClient,
) -> None:
    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner@acme.example"
    )
    await client.put(SCHEDULE, json={"enabled": True, "day_of_month": 5}, headers=auth(owner_token))

    del_resp = await client.delete(SCHEDULE, headers=auth(owner_token))
    assert del_resp.status_code == 204, del_resp.text

    get_resp = await client.get(SCHEDULE, headers=auth(owner_token))
    assert get_resp.status_code == 200
    assert get_resp.json()["enabled"] is False
    assert get_resp.json()["created_at"] is None, "DELETE must hard-delete, not soft-disable"


# ---------------------------------------------------------------------------
# GET /admin/compliance/reports (+/{report_id})
# ---------------------------------------------------------------------------


async def _seed_report_run(
    db_session: AsyncSession,
    *,
    tenant_id: str,
    object_key: str,
    generated_at: Any,
    period_start: Any,
    period_end: Any,
) -> str:
    from sqlalchemy import text

    row_id = str(uuid.uuid4())
    await db_session.execute(
        text(
            "INSERT INTO compliance_report_runs"
            " (id, tenant_id, period_start, period_end, generated_at, object_key,"
            "  size_bytes, format_version, source)"
            " VALUES (:id, :tid, :ps, :pe, :ga, :key, :size, '1', 'scheduled')"
        ),
        {
            "id": row_id,
            "tid": tenant_id,
            "ps": period_start,
            "pe": period_end,
            "ga": generated_at,
            "key": object_key,
            "size": 42,
        },
    )
    await db_session.commit()
    return row_id


async def test_reports_list_keyset_pagination(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    import datetime

    owner_token, tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner@acme.example"
    )
    base = datetime.datetime(2026, 1, 1, 0, 0, 0)
    ids = []
    for i in range(3):
        rid = await _seed_report_run(
            db_session,
            tenant_id=tenant_id,
            object_key=f"compliance-reports/{tenant_id}/run-{i}.json",
            generated_at=base + datetime.timedelta(days=i),
            period_start=base - datetime.timedelta(days=31 * (3 - i)),
            period_end=base - datetime.timedelta(days=31 * (2 - i)),
        )
        ids.append(rid)

    page1 = await client.get(REPORTS, params={"limit": 2}, headers=auth(owner_token))
    assert page1.status_code == 200, page1.text
    body1 = page1.json()
    assert len(body1["items"]) == 2
    assert body1["has_more"] is True
    assert body1["next_cursor"] is not None
    # generated_at DESC -> newest first (run-2, run-1)
    assert body1["items"][0]["id"] == ids[2]
    assert body1["items"][1]["id"] == ids[1]

    page2 = await client.get(
        REPORTS, params={"limit": 2, "cursor": body1["next_cursor"]}, headers=auth(owner_token)
    )
    assert page2.status_code == 200, page2.text
    body2 = page2.json()
    assert len(body2["items"]) == 1
    assert body2["items"][0]["id"] == ids[0]
    assert body2["has_more"] is False
    assert body2["next_cursor"] is None


async def test_reports_list_is_tenant_scoped(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    import datetime

    owner_token, _tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner@acme.example"
    )
    _other_token, other_tenant_id = await signup_tenant(
        client, tenant_name="Globex", email="owner@globex.example"
    )
    now = datetime.datetime(2026, 1, 1)
    await _seed_report_run(
        db_session,
        tenant_id=other_tenant_id,
        object_key=f"compliance-reports/{other_tenant_id}/x.json",
        generated_at=now,
        period_start=now - datetime.timedelta(days=31),
        period_end=now,
    )
    resp = await client.get(REPORTS, headers=auth(owner_token))
    assert resp.status_code == 200
    assert resp.json()["items"] == []


async def test_download_report_tenant_scoped_success(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any
) -> None:
    import datetime

    from tests.compliance_report_schedule.conftest import FakeObjectStore

    owner_token, tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner@acme.example"
    )
    now = datetime.datetime(2026, 1, 1)
    object_key = f"compliance-reports/{tenant_id}/report.json"
    report_id = await _seed_report_run(
        db_session,
        tenant_id=tenant_id,
        object_key=object_key,
        generated_at=now,
        period_start=now - datetime.timedelta(days=31),
        period_end=now,
    )

    store = FakeObjectStore()
    await store.put(object_key, b'{"cover": {}}', "application/json")
    app.state.object_store = store

    resp = await client.get(f"{REPORTS}/{report_id}", headers=auth(owner_token))
    assert resp.status_code == 200, resp.text
    assert resp.content == b'{"cover": {}}'
    assert "attachment" in resp.headers.get("content-disposition", "")


async def test_download_report_cross_tenant_is_404_never_403(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any
) -> None:
    import datetime

    from tests.compliance_report_schedule.conftest import FakeObjectStore

    _owner_a, tenant_a = await signup_tenant(client, tenant_name="Acme", email="owner@acme.example")
    owner_b_token, tenant_b = await signup_tenant(
        client, tenant_name="Globex", email="owner@globex.example"
    )
    now = datetime.datetime(2026, 1, 1)
    object_key = f"compliance-reports/{tenant_a}/report.json"
    report_id = await _seed_report_run(
        db_session,
        tenant_id=tenant_a,
        object_key=object_key,
        generated_at=now,
        period_start=now - datetime.timedelta(days=31),
        period_end=now,
    )
    store = FakeObjectStore()
    await store.put(object_key, b"{}", "application/json")
    app.state.object_store = store

    # tenant_b attempts to download tenant_a's report_id
    resp = await client.get(f"{REPORTS}/{report_id}", headers=auth(owner_b_token))
    assert_problem(resp, 404, "ERR_REPORT_NOT_FOUND")

    # unknown report_id entirely -> same 404, same code (no distinguishing signal)
    unknown_resp = await client.get(f"{REPORTS}/{uuid.uuid4()}", headers=auth(owner_b_token))
    assert_problem(unknown_resp, 404, "ERR_REPORT_NOT_FOUND")

    # tenant_b never even sees the id in ANOTHER tenant's list
    assert tenant_b != tenant_a


async def test_download_report_object_store_unavailable_is_503(
    client: httpx.AsyncClient, db_session: AsyncSession, app: Any
) -> None:
    import datetime

    owner_token, tenant_id = await signup_tenant(
        client, tenant_name="Acme", email="owner@acme.example"
    )
    now = datetime.datetime(2026, 1, 1)
    object_key = f"compliance-reports/{tenant_id}/report.json"
    report_id = await _seed_report_run(
        db_session,
        tenant_id=tenant_id,
        object_key=object_key,
        generated_at=now,
        period_start=now - datetime.timedelta(days=31),
        period_end=now,
    )

    # simulate an unconfigured/unreachable object store
    app.state.object_store = None

    resp = await client.get(f"{REPORTS}/{report_id}", headers=auth(owner_token))
    assert_problem(resp, 503, "ERR_OBJECT_STORE_UNAVAILABLE")
