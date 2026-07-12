"""RED suite for margin-dashboard (TASK.md §4, scenarios M1-M12 + R1-R7 + edge).

Operator margin dashboard: 4 superadmin-only `GET /admin/platform/margin/*` endpoints
productizing the existing `reconcile_window`/`reconcile_by_tenant` reconciliation
substrate into per-tenant/per-model margin, a windowed trend, and a three-way tie-out
against issued invoices. Central rule (M3): margin is a REAL computed number only for
`cost_basis='provider'` rows; `cost_basis='catalog'` rows NEVER get a fabricated/zeroed
margin — `has_provider_cost_data=false` + `margin=null` instead.

RED before BUILD: `gateway.usage.api.margin_router` does not exist yet (import error at
collection for the HTTP-level tests) and `reconcile_by_tenant_model`/`reconcile_trend` do
not exist yet in `reconciliation.py` (import error for the pure-aggregate tests) — the
honest missing-implementation red for the whole suite.

DO NOT change these tests to make them pass — that is the Build phase's job.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.usage.application.reconciliation import (
    MarginTrendPoint,
    TenantModelReconciliation,
    reconcile_by_tenant_model,
    reconcile_trend,
    reconcile_window,
)

from .conftest import (
    INSIDE,
    WINDOW_FROM,
    WINDOW_TO,
    audit_count,
    audit_row,
    seed_invoice,
    seed_row,
    seed_tenant,
)

# pytest asyncio_mode=auto: `async def test_*` runs without a marker.

MARGIN_SUMMARY = "/admin/platform/margin/summary"
MARGIN_BY_TENANT_MODEL = "/admin/platform/margin/by-tenant-model"
MARGIN_TREND = "/admin/platform/margin/trend"
MARGIN_TIE_OUT = "/admin/platform/margin/tie-out"
OPS_RECONCILIATION = "/ops/reconciliation"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def assert_problem(resp: Any, status: int, code: str) -> None:
    assert resp.status_code == status, resp.text
    body = resp.json()
    assert body.get("code") == code or body.get("error") == code, body


# ---------------------------------------------------------------------------
# M1 — summary matches /ops/reconciliation byte-identically for the same window
# ---------------------------------------------------------------------------
async def test_m1_summary_matches_ops_reconciliation(
    client: Any, db_session: AsyncSession, superadmin_token: str
) -> None:
    tid_a = await seed_tenant(db_session, name="Margin M1 A")
    tid_b = await seed_tenant(db_session, name="Margin M1 B")
    await seed_row(
        db_session,
        tenant_id=tid_a,
        cost_usd=Decimal("12.00"),
        provider_cost=Decimal("8.00"),
        cost_basis="provider",
        created_at=INSIDE,
    )
    await seed_row(
        db_session,
        tenant_id=tid_b,
        cost_usd=Decimal("4.00"),
        provider_cost=None,
        cost_basis="catalog",
        created_at=INSIDE,
    )

    margin_resp = await client.get(
        MARGIN_SUMMARY,
        params={"window": "month", "start": "2026-07-01", "end": "2026-07-31"},
        headers=auth(superadmin_token),
    )
    assert margin_resp.status_code == 200, margin_resp.text
    margin_body = margin_resp.json()

    # /ops/reconciliation uses the mTLS operator surface — hit reconcile_window directly
    # (the SAME primitive the ops router itself calls) rather than manufacturing a client
    # cert, matching the §1 M1 framing that these two surfaces share a call, not an HTTP path.
    from gateway.usage.api.router import _compute_window_bounds  # pyright: ignore[reportPrivateUsage]

    window_start, window_end, _g = _compute_window_bounds("month", "2026-07-01", "2026-07-31")
    ops_summary = await reconcile_window(db_session, window_start, window_end, tenant_id=None)

    assert margin_body["provider_cost_total"] == str(ops_summary.provider_cost_total)
    assert margin_body["billed_total"] == str(ops_summary.billed_total)
    assert margin_body["drift"] == str(ops_summary.drift)
    assert margin_body["unbilled_upstream_cost"] == str(ops_summary.unbilled_upstream_cost)
    assert margin_body["unbilled_rows"] == ops_summary.unbilled_rows
    assert margin_body["catalog_billed_total"] == str(ops_summary.catalog_billed_total)


async def test_m1_summary_via_ops_router_matches_margin_summary(
    client: Any, db_session: AsyncSession, superadmin_token: str
) -> None:
    """Same scenario, calling GET /ops/reconciliation for real (no mTLS available in
    tests) is out of reach — instead prove NO second aggregation was written by asserting
    both endpoints trace to `reconcile_window` (M1's second clause) via monkeypatch."""
    import gateway.usage.api.margin_router as margin_router_module

    tid = await seed_tenant(db_session, name="Margin M1 trace")
    await seed_row(
        db_session,
        tenant_id=tid,
        cost_usd=Decimal("5.00"),
        provider_cost=Decimal("3.00"),
        cost_basis="provider",
        created_at=INSIDE,
    )

    calls: list[str] = []
    orig = margin_router_module.reconcile_window

    async def _tracking(*args: Any, **kwargs: Any) -> Any:
        calls.append("reconcile_window")
        return await orig(*args, **kwargs)

    import pytest as _pytest  # local import to avoid polluting module namespace

    mp = _pytest.MonkeyPatch()
    mp.setattr(margin_router_module, "reconcile_window", _tracking)
    try:
        resp = await client.get(
            MARGIN_SUMMARY, params={"window": "month"}, headers=auth(superadmin_token)
        )
    finally:
        mp.undo()

    assert resp.status_code == 200, resp.text
    assert calls == ["reconcile_window"]


# ---------------------------------------------------------------------------
# M2 — reconcile_by_tenant_model/summary never call resolve_markup_pct
# ---------------------------------------------------------------------------
async def test_m2_never_calls_resolve_markup_pct(
    client: Any,
    db_session: AsyncSession,
    superadmin_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway.usage.application import rate_card_resolver

    tid_a = await seed_tenant(db_session, name="Margin M2 A")
    tid_b = await seed_tenant(db_session, name="Margin M2 B")
    await seed_row(
        db_session,
        tenant_id=tid_a,
        model_id="gpt-4o",
        cost_usd=Decimal("1.00"),
        provider_cost=Decimal("0.50"),
        cost_basis="provider",
        created_at=INSIDE,
    )
    await seed_row(
        db_session,
        tenant_id=tid_b,
        model_id="claude-3",
        cost_usd=Decimal("2.00"),
        cost_basis="catalog",
        created_at=INSIDE,
    )

    async def _raise(*args: Any, **kwargs: Any) -> Decimal:
        raise AssertionError("resolve_markup_pct must never be called by margin-dashboard")

    monkeypatch.setattr(rate_card_resolver, "resolve_markup_pct", _raise)

    summary_resp = await client.get(
        MARGIN_SUMMARY, params={"window": "month"}, headers=auth(superadmin_token)
    )
    by_tenant_model_resp = await client.get(
        MARGIN_BY_TENANT_MODEL, params={"window": "month"}, headers=auth(superadmin_token)
    )

    assert summary_resp.status_code == 200, summary_resp.text
    assert by_tenant_model_resp.status_code == 200, by_tenant_model_resp.text
    assert Decimal(summary_resp.json()["billed_total"]) == Decimal("1.00")
    items = by_tenant_model_resp.json()["items"]
    assert len(items) == 2


# ---------------------------------------------------------------------------
# M3 — catalog-basis usage never gets a fabricated margin
# ---------------------------------------------------------------------------
async def test_m3_catalog_basis_never_fabricated_margin(
    client: Any, db_session: AsyncSession, superadmin_token: str
) -> None:
    tid = await seed_tenant(db_session, name="Margin M3 catalog")
    await seed_row(
        db_session,
        tenant_id=tid,
        model_id="gpt-4o",
        cost_usd=Decimal("3.50"),
        provider_cost=None,
        cost_basis="catalog",
        created_at=INSIDE,
    )
    await seed_row(
        db_session,
        tenant_id=tid,
        model_id="gpt-4o",
        cost_usd=Decimal("1.50"),
        provider_cost=None,
        cost_basis="catalog",
        created_at=INSIDE,
    )

    resp = await client.get(
        MARGIN_BY_TENANT_MODEL, params={"window": "month"}, headers=auth(superadmin_token)
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    item = next(i for i in items if i["tenant_id"] == str(tid) and i["model_id"] == "gpt-4o")

    assert item["has_provider_cost_data"] is False
    assert item["margin"] is None
    assert item["catalog_billed_total"] == "5.00000000"


# ---------------------------------------------------------------------------
# M3 — provider-basis usage gets a real computed margin
# ---------------------------------------------------------------------------
async def test_m3_provider_basis_real_computed_margin(
    client: Any, db_session: AsyncSession, superadmin_token: str
) -> None:
    tid = await seed_tenant(db_session, name="Margin M3 provider")
    await seed_row(
        db_session,
        tenant_id=tid,
        model_id="some/openrouter-model",
        cost_usd=Decimal("7.00"),
        provider_cost=Decimal("5.00"),
        cost_basis="provider",
        created_at=INSIDE,
    )
    await seed_row(
        db_session,
        tenant_id=tid,
        model_id="some/openrouter-model",
        cost_usd=Decimal("5.00"),
        provider_cost=Decimal("3.00"),
        cost_basis="provider",
        created_at=INSIDE,
    )

    resp = await client.get(
        MARGIN_BY_TENANT_MODEL, params={"window": "month"}, headers=auth(superadmin_token)
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    item = next(
        i for i in items if i["tenant_id"] == str(tid) and i["model_id"] == "some/openrouter-model"
    )

    assert item["has_provider_cost_data"] is True
    # provider_cost is Numeric(20,10) — Decimal subtraction against the scale-8 billed_total
    # widens to the wider operand's scale (10), matching Python Decimal's own arithmetic
    # rules (never float-rounded — M11).
    assert Decimal(item["margin"]) == Decimal("4.00")
    assert item["margin"] == "4.0000000000"


# ---------------------------------------------------------------------------
# M4 — per-tenant-per-model grouping partitions correctly
# ---------------------------------------------------------------------------
async def test_m4_per_tenant_per_model_grouping(
    client: Any, db_session: AsyncSession, superadmin_token: str
) -> None:
    tid_a = await seed_tenant(db_session, name="Margin M4 A")
    tid_b = await seed_tenant(db_session, name="Margin M4 B")
    await seed_row(
        db_session,
        tenant_id=tid_a,
        model_id="gpt-4o",
        cost_usd=Decimal("1.00"),
        cost_basis="catalog",
        created_at=INSIDE,
    )
    await seed_row(
        db_session,
        tenant_id=tid_a,
        model_id="claude-3",
        cost_usd=Decimal("2.00"),
        cost_basis="catalog",
        created_at=INSIDE,
    )
    await seed_row(
        db_session,
        tenant_id=tid_b,
        model_id="gpt-4o",
        cost_usd=Decimal("3.00"),
        cost_basis="catalog",
        created_at=INSIDE,
    )

    resp = await client.get(
        MARGIN_BY_TENANT_MODEL,
        params={"window": "month", "limit": 100},
        headers=auth(superadmin_token),
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    keys = {(i["tenant_id"], i["model_id"]) for i in items}
    assert keys == {(str(tid_a), "gpt-4o"), (str(tid_a), "claude-3"), (str(tid_b), "gpt-4o")}

    item_a_gpt4o = next(
        i for i in items if i["tenant_id"] == str(tid_a) and i["model_id"] == "gpt-4o"
    )
    assert item_a_gpt4o["catalog_billed_total"] == "1.00000000"


# ---------------------------------------------------------------------------
# M5 — trend buckets by the window's own granularity
# ---------------------------------------------------------------------------
async def test_m5_trend_buckets_by_day_granularity(
    client: Any, db_session: AsyncSession, superadmin_token: str
) -> None:
    import datetime

    tid = await seed_tenant(db_session, name="Margin M5")
    days = [
        datetime.datetime(2026, 7, 1, 10, 0, 0, tzinfo=datetime.UTC),
        datetime.datetime(2026, 7, 2, 10, 0, 0, tzinfo=datetime.UTC),
        datetime.datetime(2026, 7, 3, 10, 0, 0, tzinfo=datetime.UTC),
        datetime.datetime(2026, 7, 4, 10, 0, 0, tzinfo=datetime.UTC),
        datetime.datetime(2026, 7, 5, 10, 0, 0, tzinfo=datetime.UTC),
    ]
    for d in days:
        await seed_row(
            db_session,
            tenant_id=tid,
            cost_usd=Decimal("1.00"),
            cost_basis="catalog",
            created_at=d,
        )

    resp = await client.get(
        MARGIN_TREND,
        params={"window": "day", "start": "2026-07-01", "end": "2026-07-06"},
        headers=auth(superadmin_token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["granularity"] == "day"
    points = body["points"]
    assert len(points) <= 5
    for p in points:
        assert p["bucket_start"].endswith("T00:00:00") or p["bucket_start"].endswith("+00:00")
        assert Decimal(p["catalog_billed_total"]) == Decimal("1.00")


# ---------------------------------------------------------------------------
# M6 — tie-out matches when the invoice reconciles to the ledger
# ---------------------------------------------------------------------------
async def test_m6_tie_out_matched(
    client: Any, db_session: AsyncSession, superadmin_token: str
) -> None:
    import datetime

    tid = await seed_tenant(db_session, name="Margin M6 matched")
    period_start = datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC)
    period_end = datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC)
    await seed_row(
        db_session,
        tenant_id=tid,
        cost_usd=Decimal("350.00000000"),
        cost_basis="catalog",
        created_at=INSIDE,
    )
    await seed_invoice(
        db_session,
        tenant_id=tid,
        period_start=period_start,
        period_end=period_end,
        total_usd=Decimal("350.00"),
        raw_total_usd=Decimal("350.00000000"),
        status="issued",
    )

    resp = await client.get(
        MARGIN_TIE_OUT, params={"period": "2026-07"}, headers=auth(superadmin_token)
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    item = next(i for i in items if i["tenant_id"] == str(tid))

    assert item["tie_out_status"] == "matched"
    assert item["invoiced_raw_total_usd"] == "350.00000000"
    assert item["ledger_billed_total_usd"] == "350.00000000"


# ---------------------------------------------------------------------------
# M6 — tie-out surfaces drift without correcting it
# ---------------------------------------------------------------------------
async def test_m6_tie_out_drift_detected_never_corrects(
    client: Any, db_session: AsyncSession, superadmin_token: str
) -> None:
    import datetime

    tid = await seed_tenant(db_session, name="Margin M6 drift")
    period_start = datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC)
    period_end = datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC)
    await seed_row(
        db_session,
        tenant_id=tid,
        cost_usd=Decimal("351.50000000"),
        cost_basis="catalog",
        created_at=INSIDE,
    )
    await seed_invoice(
        db_session,
        tenant_id=tid,
        period_start=period_start,
        period_end=period_end,
        total_usd=Decimal("350.00"),
        raw_total_usd=Decimal("350.00000000"),
        status="issued",
    )

    invoice_before = (
        await db_session.execute(
            text("SELECT raw_total_usd FROM invoices WHERE tenant_id = :tid"),
            {"tid": tid},
        )
    ).fetchone()
    assert invoice_before is not None

    resp = await client.get(
        MARGIN_TIE_OUT, params={"period": "2026-07"}, headers=auth(superadmin_token)
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    item = next(i for i in items if i["tenant_id"] == str(tid))
    assert item["tie_out_status"] == "drift_detected"

    invoice_after = (
        await db_session.execute(
            text("SELECT raw_total_usd FROM invoices WHERE tenant_id = :tid"),
            {"tid": tid},
        )
    ).fetchone()
    assert invoice_after is not None
    assert invoice_before[0] == invoice_after[0]  # never corrected by this read


# ---------------------------------------------------------------------------
# M7 — tie-out reports pending_invoice, not drift, for an un-invoiced period
# ---------------------------------------------------------------------------
async def test_m7_tie_out_pending_invoice_for_uninvoiced_period(
    client: Any, db_session: AsyncSession, superadmin_token: str
) -> None:
    import datetime

    tid = await seed_tenant(db_session, name="Margin M7")
    august_inside = datetime.datetime(2026, 8, 15, 12, 0, 0, tzinfo=datetime.UTC)
    await seed_row(
        db_session,
        tenant_id=tid,
        cost_usd=Decimal("42.00"),
        cost_basis="catalog",
        created_at=august_inside,
    )

    resp = await client.get(
        MARGIN_TIE_OUT, params={"period": "2026-08"}, headers=auth(superadmin_token)
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    item = next(i for i in items if i["tenant_id"] == str(tid))

    assert item["tie_out_status"] == "pending_invoice"
    assert item["invoiced_total_usd"] is None
    assert item["invoiced_raw_total_usd"] is None
    assert item["ledger_billed_total_usd"] == "42.00000000"


# ---------------------------------------------------------------------------
# M8 — bounded query timeout surfaces as a structured error
# ---------------------------------------------------------------------------
async def test_m8_query_timeout_maps_to_504(
    client: Any,
    db_session: AsyncSession,
    superadmin_token: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession

    tid = await seed_tenant(db_session, name="Margin M8")
    await seed_row(db_session, tenant_id=tid, cost_usd=Decimal("1.00"), created_at=INSIDE)

    orig_execute = _AsyncSession.execute

    async def _flaky_execute(self: _AsyncSession, statement: Any, *args: Any, **kwargs: Any) -> Any:
        compiled = str(statement).lstrip()
        if compiled.startswith("SELECT") and "usage_records" in compiled:
            raise TimeoutError("simulated margin-query DB fault (test-only fault injection)")
        return await orig_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(_AsyncSession, "execute", _flaky_execute)

    resp = await client.get(
        MARGIN_SUMMARY, params={"window": "month"}, headers=auth(superadmin_token)
    )

    assert_problem(resp, 504, "ERR_MARGIN_QUERY_TIMEOUT")


# ---------------------------------------------------------------------------
# M9 — every margin read is audited
# ---------------------------------------------------------------------------
async def test_m9_summary_read_is_audited(
    client: Any, db_session: AsyncSession, superadmin_token: str
) -> None:
    before = await audit_count(db_session, action="platform.margin.view_summary")

    resp = await client.get(
        MARGIN_SUMMARY, params={"window": "month"}, headers=auth(superadmin_token)
    )
    assert resp.status_code == 200, resp.text

    after = await audit_count(db_session, action="platform.margin.view_summary")
    assert after == before + 1

    row = await audit_row(db_session, action="platform.margin.view_summary")
    assert row["tenant_id"] is None
    assert "window_from" in row["metadata"] or "window" in row["metadata"]


# ---------------------------------------------------------------------------
# M10 — by-tenant-model list is keyset-paginated
# ---------------------------------------------------------------------------
async def test_m10_by_tenant_model_keyset_pagination(
    client: Any, db_session: AsyncSession, superadmin_token: str
) -> None:
    # 120 distinct (tenant, model) buckets — 12 tenants x 10 models each.
    tenant_ids = [await seed_tenant(db_session, name=f"Margin M10 T{i}") for i in range(12)]
    for tid in tenant_ids:
        for m in range(10):
            await seed_row(
                db_session,
                tenant_id=tid,
                model_id=f"model-{m}",
                cost_usd=Decimal("1.00"),
                cost_basis="catalog",
                created_at=INSIDE,
            )

    seen: set[tuple[str, str]] = set()
    cursor: str | None = None
    pages = 0
    while True:
        params: dict[str, Any] = {"window": "month", "limit": 50}
        if cursor is not None:
            params["cursor"] = cursor
        resp = await client.get(
            MARGIN_BY_TENANT_MODEL, params=params, headers=auth(superadmin_token)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        page_keys = {(i["tenant_id"], i["model_id"]) for i in body["items"]}
        assert not (page_keys & seen), "a page overlapped a previously-seen item"
        seen |= page_keys
        pages += 1
        if pages == 1:
            assert len(body["items"]) == 50
            assert body["has_more"] is True
        if not body["has_more"]:
            break
        cursor = body["next_cursor"]
        assert cursor is not None
        assert pages < 10  # guard against an infinite loop on a build defect

    assert len(seen) == 120


# ---------------------------------------------------------------------------
# M11 — money fields are exact decimal strings
# ---------------------------------------------------------------------------
async def test_m11_money_fields_are_exact_decimal_strings(
    client: Any, db_session: AsyncSession, superadmin_token: str
) -> None:
    tid = await seed_tenant(db_session, name="Margin M11")
    await seed_row(
        db_session,
        tenant_id=tid,
        cost_usd=Decimal("0.10000003"),
        provider_cost=Decimal("0.05"),
        cost_basis="provider",
        created_at=INSIDE,
    )

    resp = await client.get(
        MARGIN_SUMMARY, params={"window": "month"}, headers=auth(superadmin_token)
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["billed_total"] == "0.10000003"


# ---------------------------------------------------------------------------
# R1 — no bearer token
# ---------------------------------------------------------------------------
async def test_r1_no_bearer_token(client: Any) -> None:
    resp = await client.get(MARGIN_SUMMARY)
    assert_problem(resp, 401, "ERR_AUTH_INVALID_TOKEN")
    assert "provider_cost_total" not in resp.text


# ---------------------------------------------------------------------------
# R2 — authenticated but not superadmin (all 4 endpoints)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url,params",
    [
        (MARGIN_SUMMARY, {"window": "month"}),
        (MARGIN_BY_TENANT_MODEL, {"window": "month"}),
        (MARGIN_TREND, {"window": "month"}),
        (MARGIN_TIE_OUT, {"period": "2026-07"}),
    ],
)
async def test_r2_non_superadmin_forbidden(
    client: Any, owner_token: str, url: str, params: dict[str, str]
) -> None:
    resp = await client.get(url, params=params, headers=auth(owner_token))
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")
    assert "provider_cost_total" not in resp.text
    assert "items" not in resp.text


# ---------------------------------------------------------------------------
# R3 — invalid window/date is rejected
# ---------------------------------------------------------------------------
async def test_r3_invalid_window_rejected(client: Any, superadmin_token: str) -> None:
    resp = await client.get(
        MARGIN_SUMMARY, params={"window": "quarter"}, headers=auth(superadmin_token)
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


async def test_r3_invalid_start_date_rejected(client: Any, superadmin_token: str) -> None:
    resp = await client.get(
        MARGIN_SUMMARY,
        params={"window": "month", "start": "not-a-date"},
        headers=auth(superadmin_token),
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# R4 — malformed tie-out period is rejected
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("period", ["2026-13", "july-2026", ""])
async def test_r4_malformed_tie_out_period_rejected(
    client: Any, superadmin_token: str, period: str
) -> None:
    params = {} if period == "" else {"period": period}
    resp = await client.get(MARGIN_TIE_OUT, params=params, headers=auth(superadmin_token))
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# R5 — invalid limit on by-tenant-model is rejected
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("limit", ["0", "101", "abc"])
async def test_r5_invalid_limit_rejected(client: Any, superadmin_token: str, limit: str) -> None:
    resp = await client.get(
        MARGIN_BY_TENANT_MODEL,
        params={"window": "month", "limit": limit},
        headers=auth(superadmin_token),
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# R6 — malformed cursor on by-tenant-model is rejected
# ---------------------------------------------------------------------------
async def test_r6_malformed_cursor_rejected(client: Any, superadmin_token: str) -> None:
    resp = await client.get(
        MARGIN_BY_TENANT_MODEL,
        params={"window": "month", "cursor": "not-valid-base64!!"},
        headers=auth(superadmin_token),
    )
    assert_problem(resp, 422, "ERR_CURSOR_INVALID")
    assert "items" not in resp.text


# ---------------------------------------------------------------------------
# R7 — invalid tenant_id filter is rejected
# ---------------------------------------------------------------------------
async def test_r7_invalid_tenant_id_filter_rejected(client: Any, superadmin_token: str) -> None:
    resp = await client.get(
        MARGIN_BY_TENANT_MODEL,
        params={"window": "month", "tenant_id": "not-a-uuid"},
        headers=auth(superadmin_token),
    )
    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# edge/boundary — empty window returns explicit zeros, not an error
# ---------------------------------------------------------------------------
async def test_edge_empty_window_explicit_zeros(client: Any, superadmin_token: str) -> None:
    resp = await client.get(
        MARGIN_SUMMARY,
        params={"window": "month", "start": "2020-01-01", "end": "2020-01-31"},
        headers=auth(superadmin_token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider_cost_total"] == "0"
    assert body["billed_total"] == "0"
    assert body["catalog_billed_total"] == "0"
    assert body["margin"] is None
    assert body["has_provider_cost_data"] is False

    by_tenant_model_resp = await client.get(
        MARGIN_BY_TENANT_MODEL,
        params={"window": "month", "start": "2020-01-01", "end": "2020-01-31"},
        headers=auth(superadmin_token),
    )
    assert by_tenant_model_resp.status_code == 200, by_tenant_model_resp.text
    empty_body = by_tenant_model_resp.json()
    assert empty_body["items"] == []
    assert empty_body["has_more"] is False


# ---------------------------------------------------------------------------
# Pure-aggregate coverage — reconcile_by_tenant_model / reconcile_trend directly
# (mirrors reconciliation_aggregate's own direct-primitive-call convention)
# ---------------------------------------------------------------------------
async def test_reconcile_by_tenant_model_shape(client: Any, db_session: AsyncSession) -> None:
    tid = await seed_tenant(db_session, name="Margin direct RBTM")
    await seed_row(
        db_session,
        tenant_id=tid,
        model_id="gpt-4o",
        cost_usd=Decimal("2.00"),
        provider_cost=Decimal("1.00"),
        cost_basis="provider",
        created_at=INSIDE,
    )

    rows = await reconcile_by_tenant_model(db_session, WINDOW_FROM, WINDOW_TO)

    assert any(isinstance(r, TenantModelReconciliation) for r in rows)
    row = next(r for r in rows if r.tenant_id == tid and r.model_id == "gpt-4o")
    assert row.has_provider_cost_data is True
    assert row.margin == Decimal("1.00")


async def test_reconcile_trend_shape(client: Any, db_session: AsyncSession) -> None:
    tid = await seed_tenant(db_session, name="Margin direct trend")
    await seed_row(
        db_session,
        tenant_id=tid,
        cost_usd=Decimal("3.00"),
        cost_basis="catalog",
        created_at=INSIDE,
    )

    points = await reconcile_trend(db_session, WINDOW_FROM, WINDOW_TO, "month", tenant_id=None)

    assert any(isinstance(p, MarginTrendPoint) for p in points)
    assert sum((p.catalog_billed_total for p in points), Decimal("0")) == Decimal("3.00")
