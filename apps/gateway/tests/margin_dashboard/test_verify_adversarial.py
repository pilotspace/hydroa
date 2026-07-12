"""VERIFY-phase adversarial probes for margin-dashboard (TASK.md §6).

Written by the verify agent — NOT part of the frozen §4 red suite, NEVER edits/weakens
the existing suite. Purpose: refute the green by attacking exactly the surfaces the
build's own TASK.md §5 flagged as adaptations (Decimal-scale, TIMESTAMPTZ-vs-naive), plus
the central M3 honesty invariant, authz byte-shape, and timeout wiring across ALL 4 routes
(the frozen suite only proves timeout wiring on /summary).

Findings are reported in TASK.md §6, not fixed here — a fix during verify is a FINDING,
per the calling agent's hard rule.
"""

from __future__ import annotations

import datetime
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.usage.application.reconciliation import reconcile_window

from .conftest import INSIDE, WINDOW_FROM, WINDOW_TO, seed_invoice, seed_row, seed_tenant

MARGIN_SUMMARY = "/admin/platform/margin/summary"
MARGIN_BY_TENANT_MODEL = "/admin/platform/margin/by-tenant-model"
MARGIN_TREND = "/admin/platform/margin/trend"
MARGIN_TIE_OUT = "/admin/platform/margin/tie-out"

ALL_ROUTES = (
    ("GET", MARGIN_SUMMARY, {"window": "month"}),
    ("GET", MARGIN_BY_TENANT_MODEL, {"window": "month"}),
    ("GET", MARGIN_TREND, {"window": "month"}),
    ("GET", MARGIN_TIE_OUT, {"period": "2026-07"}),
)


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Probe 1 — mixed-provenance bucket: catalog rows must NEVER blend into the
# provider-basis margin figure, for the SAME (tenant, model) in the SAME window.
# ---------------------------------------------------------------------------
async def test_verify_mixed_provenance_bucket_no_blend(
    client: Any, db_session: AsyncSession, superadmin_token: str
) -> None:
    tid = await seed_tenant(db_session, name="Verify Mixed Provenance")
    model = "some/openrouter-model"

    # 2 provider-basis rows: cost_usd 5.00 + 5.00 = 10.00 billed, provider_cost 3.00+3.00=6.00
    for _ in range(2):
        await seed_row(
            db_session,
            tenant_id=tid,
            model_id=model,
            cost_usd=Decimal("5.00"),
            provider_cost=Decimal("3.00"),
            cost_basis="provider",
            created_at=INSIDE,
        )
    # 2 catalog-basis rows on the SAME (tenant, model): cost_usd 7.00 + 7.00 = 14.00
    for _ in range(2):
        await seed_row(
            db_session,
            tenant_id=tid,
            model_id=model,
            cost_usd=Decimal("7.00"),
            provider_cost=None,
            cost_basis="catalog",
            created_at=INSIDE,
        )

    resp = await client.get(
        MARGIN_BY_TENANT_MODEL,
        params={"window": "month", "start": "2026-07-01", "end": "2026-07-31"},
        headers=auth(superadmin_token),
    )
    assert resp.status_code == 200, resp.text
    items = [i for i in resp.json()["items"] if i["tenant_id"] == str(tid)]
    assert len(items) == 1, f"expected exactly 1 (tenant,model) bucket, got {items}"
    item = items[0]

    # The central invariant: provider-basis totals must be UNCONTAMINATED by catalog rows.
    # cost_usd is Numeric(14,8) -> Postgres SUM preserves that scale (real-Postgres known
    # scale, matching the build's own §5 "known-problem fix" note — not a float guess).
    assert Decimal(item["billed_total"]) == Decimal("10.00"), item
    assert Decimal(item["provider_cost_total"]) == Decimal("6.00"), item
    assert Decimal(item["margin"]) == Decimal("4.00"), item
    assert item["has_provider_cost_data"] is True
    # Catalog revenue reported SEPARATELY, never folded into billed_total/margin.
    assert Decimal(item["catalog_billed_total"]) == Decimal("14.00"), item


# ---------------------------------------------------------------------------
# Probe 2 — M3 edge case: a cost_basis='provider' row with provider_cost=0 AND
# cost_usd=0 (a genuinely free upstream call). /summary's has_provider_cost_data is
# derived from a heuristic (totals != 0 OR unbilled_rows > 0), NOT an honest COUNT of
# provider rows the way /by-tenant-model and /trend do it — refute whether this
# heuristic silently disagrees with the honest-count definition M3 states verbatim:
# "true iff the bucket has >=1 cost_basis='provider' row".
# ---------------------------------------------------------------------------
async def test_verify_summary_has_provider_cost_data_zero_cost_provider_row(
    client: Any, db_session: AsyncSession, superadmin_token: str
) -> None:
    tid = await seed_tenant(db_session, name="Verify Zero-Cost Provider Row")
    await seed_row(
        db_session,
        tenant_id=tid,
        cost_usd=Decimal("0"),
        provider_cost=Decimal("0"),
        cost_basis="provider",
        created_at=INSIDE,
    )

    resp = await client.get(
        MARGIN_SUMMARY,
        params={"window": "month", "start": "2026-07-01", "end": "2026-07-31"},
        headers=auth(superadmin_token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Ground truth via a direct honest COUNT of cost_basis='provider' rows in the window.
    count = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM usage_records WHERE cost_basis='provider'"
                " AND tenant_id = :tid"
            ),
            {"tid": str(tid)},
        )
    ).scalar()
    assert count == 1, "fixture sanity: exactly 1 provider-basis row exists"

    # M3 contract text: has_provider_cost_data is true iff >=1 cost_basis='provider' row.
    # Document actual behavior (this is the refute-read finding, not asserted as a bug
    # fix): summary's heuristic (totals!=0 OR unbilled_rows>0) reports False here even
    # though a real provider row exists with a real (zero) margin.
    print(
        f"has_provider_cost_data reported: {body['has_provider_cost_data']!r}, margin={body['margin']!r}"
    )
    # This assertion documents the CURRENT (heuristic) behavior for the record — see §6.
    assert body["has_provider_cost_data"] is False, (
        "if this now fails, /summary's has_provider_cost_data heuristic was fixed to an "
        "honest COUNT — update TASK.md §6 finding accordingly"
    )
    assert body["margin"] is None


# ---------------------------------------------------------------------------
# Probe 3 — authz byte-shape: EVERY one of the 4 routes must 403 for a non-superadmin
# with ZERO cross-tenant fields leaked in the body, and 401 with no Authorization header.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method,path,params", ALL_ROUTES)
async def test_verify_non_superadmin_403_byte_shape(
    client: Any, owner_token: str, method: str, path: str, params: dict[str, str]
) -> None:
    resp = await client.get(path, params=params, headers=auth(owner_token))
    assert resp.status_code == 403, f"{path}: {resp.text}"
    body = resp.json()
    leaked_keys = {
        "items",
        "points",
        "provider_cost_total",
        "billed_total",
        "margin",
        "catalog_billed_total",
        "ledger_billed_total_usd",
    } & body.keys()
    assert not leaked_keys, f"{path} leaked financial fields on 403: {leaked_keys} in {body}"
    assert body.get("code") == "ERR_AUTH_FORBIDDEN" or body.get("error") == "ERR_AUTH_FORBIDDEN"


@pytest.mark.parametrize("method,path,params", ALL_ROUTES)
async def test_verify_no_token_401_byte_shape(
    client: Any, method: str, path: str, params: dict[str, str]
) -> None:
    resp = await client.get(path, params=params)
    assert resp.status_code == 401, f"{path}: {resp.text}"
    body = resp.json()
    leaked_keys = {"items", "points", "provider_cost_total", "billed_total"} & body.keys()
    assert not leaked_keys, f"{path} leaked financial fields on 401: {body}"


# ---------------------------------------------------------------------------
# Probe 4 — query-timeout wiring is real on ALL 4 routes, not just /summary (the
# frozen suite's test_m8 only forces TimeoutError against the summary code path).
# Uses the SAME low-level AsyncSession.execute fault-injection idiom test_m8 uses
# (patching the reconciliation-module function name directly does NOT work: the
# router imports it by name via `from ... import X`, so the router's own bound
# reference is untouched by patching the source module's attribute — a real
# testing-methodology trap, not a code defect; confirmed by first attempting the
# module-level patch and observing it silently no-ops, see TASK.md §6).
# ---------------------------------------------------------------------------
async def _patch_execute_to_raise_on_usage_records(monkeypatch: pytest.MonkeyPatch) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession as _AsyncSession

    orig_execute = _AsyncSession.execute

    async def _flaky_execute(self: _AsyncSession, statement: Any, *args: Any, **kwargs: Any) -> Any:
        compiled = str(statement).lstrip()
        if compiled.startswith("SELECT") and "usage_records" in compiled:
            raise TimeoutError("simulated margin-query DB fault (test-only fault injection)")
        return await orig_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(_AsyncSession, "execute", _flaky_execute)


async def test_verify_timeout_wiring_by_tenant_model(
    client: Any, superadmin_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _patch_execute_to_raise_on_usage_records(monkeypatch)
    resp = await client.get(
        MARGIN_BY_TENANT_MODEL, params={"window": "month"}, headers=auth(superadmin_token)
    )
    assert resp.status_code == 504, resp.text
    body = resp.json()
    assert (
        body.get("code") == "ERR_MARGIN_QUERY_TIMEOUT"
        or body.get("error") == "ERR_MARGIN_QUERY_TIMEOUT"
    )


async def test_verify_timeout_wiring_trend(
    client: Any, superadmin_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _patch_execute_to_raise_on_usage_records(monkeypatch)
    resp = await client.get(
        MARGIN_TREND, params={"window": "month"}, headers=auth(superadmin_token)
    )
    assert resp.status_code == 504, resp.text


async def test_verify_timeout_wiring_tie_out(
    client: Any, superadmin_token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _patch_execute_to_raise_on_usage_records(monkeypatch)
    resp = await client.get(
        MARGIN_TIE_OUT, params={"period": "2026-07"}, headers=auth(superadmin_token)
    )
    assert resp.status_code == 504, resp.text


# ---------------------------------------------------------------------------
# Probe 5 — Decimal fidelity: high-precision costs sum with NO float drift, across
# both /summary and /by-tenant-model, and margin subtraction preserves it.
# ---------------------------------------------------------------------------
async def test_verify_decimal_fidelity_no_float_drift(
    client: Any, db_session: AsyncSession, superadmin_token: str
) -> None:
    tid = await seed_tenant(db_session, name="Verify Decimal Fidelity")
    # Three rows whose sum is exact in Decimal but NOT exactly representable in float
    # (0.1 + 0.2 != 0.3 in IEEE754) — scaled up to usage_records' real column precision.
    await seed_row(
        db_session,
        tenant_id=tid,
        cost_usd=Decimal("0.10000003"),
        provider_cost=Decimal("0.05000001"),
        cost_basis="provider",
        created_at=INSIDE,
    )
    await seed_row(
        db_session,
        tenant_id=tid,
        cost_usd=Decimal("0.20000005"),
        provider_cost=Decimal("0.10000002"),
        cost_basis="provider",
        created_at=INSIDE,
    )
    await seed_row(
        db_session,
        tenant_id=tid,
        cost_usd=Decimal("0.30000007"),
        provider_cost=Decimal("0.15000003"),
        cost_basis="provider",
        created_at=INSIDE,
    )

    resp = await client.get(
        MARGIN_BY_TENANT_MODEL,
        params={"window": "month", "start": "2026-07-01", "end": "2026-07-31"},
        headers=auth(superadmin_token),
    )
    assert resp.status_code == 200, resp.text
    items = [i for i in resp.json()["items"] if i["tenant_id"] == str(tid)]
    assert len(items) == 1
    item = items[0]

    expected_billed = Decimal("0.10000003") + Decimal("0.20000005") + Decimal("0.30000007")
    expected_provider = Decimal("0.05000001") + Decimal("0.10000002") + Decimal("0.15000003")
    expected_margin = expected_billed - expected_provider

    assert Decimal(item["billed_total"]) == expected_billed, item["billed_total"]
    assert Decimal(item["provider_cost_total"]) == expected_provider, item["provider_cost_total"]
    assert Decimal(item["margin"]) == expected_margin, item["margin"]
    # Byte-level: the wire string must not have been float-rounded (e.g. to 2dp).
    assert item["billed_total"] == str(expected_billed), item["billed_total"]


# ---------------------------------------------------------------------------
# Probe 6 — window boundary: half-open [from, to) must be exact — a row exactly AT
# window_to must be EXCLUDED, a row exactly AT window_from must be INCLUDED.
# ---------------------------------------------------------------------------
async def test_verify_window_boundary_half_open_exact(
    client: Any, db_session: AsyncSession, superadmin_token: str
) -> None:
    tid = await seed_tenant(db_session, name="Verify Window Boundary")
    # Exactly at window_from (2026-07-01T00:00:00Z) -> INCLUDED
    await seed_row(
        db_session,
        tenant_id=tid,
        cost_usd=Decimal("1.00"),
        provider_cost=Decimal("0.50"),
        cost_basis="provider",
        created_at=WINDOW_FROM,
    )
    # Exactly at window_to (2026-08-01T00:00:00Z) -> EXCLUDED (next window)
    await seed_row(
        db_session,
        tenant_id=tid,
        cost_usd=Decimal("99.00"),
        provider_cost=Decimal("50.00"),
        cost_basis="provider",
        created_at=WINDOW_TO,
    )

    resp = await client.get(
        MARGIN_SUMMARY,
        params={"window": "month", "start": "2026-07-01", "end": "2026-07-31"},
        headers=auth(superadmin_token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert Decimal(body["billed_total"]) == Decimal("1.00"), (
        f"window boundary leak: expected only the window_from row (1.00), got {body['billed_total']} "
        "(a row exactly at window_to was likely NOT excluded, or window_from was excluded)"
    )


# ---------------------------------------------------------------------------
# Probe 7 — tie-out Decimal exactness: the tie-out reconciles EXACTLY vs the ledger,
# even at 8-decimal-place granularity (real cost_usd column scale), never a
# float-rounded near-match.
# ---------------------------------------------------------------------------
async def test_verify_tie_out_decimal_exactness(
    client: Any, db_session: AsyncSession, superadmin_token: str
) -> None:
    tid = await seed_tenant(db_session, name="Verify Tie-Out Decimal")
    period_start = datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC)
    period_end = datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC)

    # Ledger sum across 3 rows: 116.66666667 (a value that is NOT exactly representable
    # in binary float at this precision).
    for cost in (Decimal("38.88888889"), Decimal("38.88888889"), Decimal("38.88888889")):
        await seed_row(
            db_session, tenant_id=tid, cost_usd=cost, cost_basis="catalog", created_at=INSIDE
        )
    ledger_sum = Decimal("38.88888889") * 3  # 116.66666667

    await seed_invoice(
        db_session,
        tenant_id=tid,
        period_start=period_start,
        period_end=period_end,
        total_usd=ledger_sum,
        raw_total_usd=ledger_sum,
        status="issued",
    )

    resp = await client.get(
        MARGIN_TIE_OUT, params={"period": "2026-07"}, headers=auth(superadmin_token)
    )
    assert resp.status_code == 200, resp.text
    items = [i for i in resp.json()["items"] if i["tenant_id"] == str(tid)]
    assert len(items) == 1
    item = items[0]
    assert item["tie_out_status"] == "matched", item
    assert item["ledger_billed_total_usd"] == str(ledger_sum), item
    assert Decimal(item["ledger_billed_total_usd"]) == ledger_sum


# ---------------------------------------------------------------------------
# Probe 8 — tenant_id filter isolation: the optional ?tenant_id= filter on
# /by-tenant-model and /tie-out must show ONLY that tenant's rows, never leak a
# second tenant's figures alongside it (a cross-tenant financial visibility surface
# where a broken filter would be a real data-isolation defect, not just cosmetic).
# ---------------------------------------------------------------------------
async def test_verify_tenant_id_filter_isolates_by_tenant_model(
    client: Any, db_session: AsyncSession, superadmin_token: str
) -> None:
    tid_a = await seed_tenant(db_session, name="Verify Filter A")
    tid_b = await seed_tenant(db_session, name="Verify Filter B")
    await seed_row(
        db_session,
        tenant_id=tid_a,
        cost_usd=Decimal("5.00"),
        cost_basis="catalog",
        created_at=INSIDE,
    )
    await seed_row(
        db_session,
        tenant_id=tid_b,
        cost_usd=Decimal("9.00"),
        cost_basis="catalog",
        created_at=INSIDE,
    )

    resp = await client.get(
        MARGIN_BY_TENANT_MODEL,
        params={"window": "month", "tenant_id": str(tid_a)},
        headers=auth(superadmin_token),
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    tenant_ids_returned = {i["tenant_id"] for i in items}
    assert str(tid_b) not in tenant_ids_returned, f"tenant_id filter leaked tenant B: {items}"
    assert all(i["tenant_id"] == str(tid_a) for i in items), items


async def test_verify_tenant_id_filter_isolates_tie_out(
    client: Any, db_session: AsyncSession, superadmin_token: str
) -> None:
    tid_a = await seed_tenant(db_session, name="Verify Tie-Out Filter A")
    tid_b = await seed_tenant(db_session, name="Verify Tie-Out Filter B")
    await seed_row(
        db_session,
        tenant_id=tid_a,
        cost_usd=Decimal("5.00"),
        cost_basis="catalog",
        created_at=INSIDE,
    )
    await seed_row(
        db_session,
        tenant_id=tid_b,
        cost_usd=Decimal("9.00"),
        cost_basis="catalog",
        created_at=INSIDE,
    )

    resp = await client.get(
        MARGIN_TIE_OUT,
        params={"period": "2026-07", "tenant_id": str(tid_a)},
        headers=auth(superadmin_token),
    )
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    tenant_ids_returned = {i["tenant_id"] for i in items}
    assert str(tid_b) not in tenant_ids_returned, (
        f"tie-out tenant_id filter leaked tenant B: {items}"
    )
    assert tenant_ids_returned == {str(tid_a)}, items
