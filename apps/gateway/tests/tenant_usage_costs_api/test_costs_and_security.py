"""Red suite — GET /v1/organization/costs + the data-sensitivity guarantees.

Covers M2 (billed-cost-only, Decimal-exact), M3 (hard tenant isolation), M5/R-FILTER
(cross-tenant api_key_ids → empty, never 404), R1/R2 (auth), M9 edge (ZDR reads metadata),
After (read writes nothing). Endpoint absent → red for missing implementation.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


from .conftest import (
    COMPLETIONS_PATH,
    COSTS_PATH,
    DAY0,
    DAY1,
    DAY2,
    auth_header,
    unix,
)

async def test_costs_amount_billed_only_decimal_exact(
    client: Any, tenant_a: dict[str, str], seed_usage: Any
) -> None:
    """M2: amount.value == exact SUM(cost_usd), currency usd; NO provider/basis/markup leak."""
    await seed_usage(
        tenant_id=tenant_a["tenant_id"], key_id=tenant_a["key_id"],
        cost_usd=Decimal("0.00012345"), created_at=DAY0,
    )
    await seed_usage(
        tenant_id=tenant_a["tenant_id"], key_id=tenant_a["key_id"],
        cost_usd=Decimal("0.00087655"), created_at=DAY0,
    )
    resp = await client.get(
        COSTS_PATH,
        params={"start_time": unix(DAY0), "end_time": unix(DAY1)},
        headers=auth_header(tenant_a["key"]),
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["data"][0]["results"][0]
    assert result["object"] == "organization.costs.result"
    assert result["amount"]["currency"] == "usd"
    # 0.00012345 + 0.00087655 == 0.00100000 exactly
    assert Decimal(str(result["amount"]["value"])) == Decimal("0.00100000")
    # Billed-cost-only: internal provenance columns must never appear on the wire.
    for forbidden in ("provider_cost", "cost_basis", "markup", "provider_cost_total"):
        assert forbidden not in result


async def test_costs_bucket_width_hour_rejected(client: Any, tenant_a: dict[str, str]) -> None:
    """M6/R5: /costs supports only 1d — 1h → 422."""
    resp = await client.get(
        COSTS_PATH,
        params={"start_time": unix(DAY0), "bucket_width": "1h"},
        headers=auth_header(tenant_a["key"]),
    )
    assert resp.status_code == 422, resp.text


async def test_tenant_isolation_two_tenants(
    client: Any, tenant_a: dict[str, str], tenant_b: dict[str, str], seed_usage: Any
) -> None:
    """M3: tenant A's key sees ONLY A's rows on both endpoints — B's tokens/cost never appear."""
    await seed_usage(
        tenant_id=tenant_a["tenant_id"], key_id=tenant_a["key_id"],
        prompt_tokens=11, cost_usd=Decimal("0.01"), created_at=DAY0,
    )
    await seed_usage(
        tenant_id=tenant_b["tenant_id"], key_id=tenant_b["key_id"],
        prompt_tokens=999, cost_usd=Decimal("9.99"), created_at=DAY0,
    )
    # Completions: A must total 11 input tokens, never 999.
    comp = await client.get(
        COMPLETIONS_PATH,
        params={"start_time": unix(DAY0), "end_time": unix(DAY1)},
        headers=auth_header(tenant_a["key"]),
    )
    assert comp.status_code == 200, comp.text
    total_input = sum(
        r["input_tokens"] for b in comp.json()["data"] for r in b["results"]
    )
    assert total_input == 11

    # Costs: A must total 0.01, never 9.99.
    costs = await client.get(
        COSTS_PATH,
        params={"start_time": unix(DAY0), "end_time": unix(DAY1)},
        headers=auth_header(tenant_a["key"]),
    )
    assert costs.status_code == 200, costs.text
    total_cost = sum(
        Decimal(str(r["amount"]["value"]))
        for b in costs.json()["data"]
        for r in b["results"]
    )
    assert total_cost == Decimal("0.01")


async def test_api_key_ids_filter_cross_tenant_is_empty_not_404(
    client: Any, tenant_a: dict[str, str], tenant_b: dict[str, str], seed_usage: Any
) -> None:
    """R-FILTER/M5: A filters by B's key_id → 200 with zero rows, NEVER 404 (no existence oracle)."""
    await seed_usage(
        tenant_id=tenant_b["tenant_id"], key_id=tenant_b["key_id"],
        prompt_tokens=42, created_at=DAY0,
    )
    resp = await client.get(
        COMPLETIONS_PATH,
        params={
            "start_time": unix(DAY0),
            "end_time": unix(DAY1),
            "api_key_ids": tenant_b["key_id"],
        },
        headers=auth_header(tenant_a["key"]),
    )
    assert resp.status_code == 200, resp.text  # explicitly NOT 404
    total = sum(r["input_tokens"] for b in resp.json()["data"] for r in b["results"])
    assert total == 0


async def test_auth_missing_key_401(client: Any) -> None:
    """R1: no Authorization header → 401 ERR_AUTH_INVALID_KEY (route exists, so not 404)."""
    resp = await client.get(COMPLETIONS_PATH, params={"start_time": unix(DAY0)})
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"] == "ERR_AUTH_INVALID_KEY"


async def test_auth_revoked_key_401(
    client: Any, tenant_a: dict[str, str]
) -> None:
    """R1: a revoked key → 401 ERR_AUTH_INVALID_KEY."""
    revoke = await client.delete(
        f"/admin/keys/{tenant_a['key_id']}",
        headers={"Authorization": f"Bearer {tenant_a['jwt']}"},
    )
    assert revoke.status_code in (200, 204), revoke.text
    resp = await client.get(
        COMPLETIONS_PATH,
        params={"start_time": unix(DAY0)},
        headers=auth_header(tenant_a["key"]),
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["error"] == "ERR_AUTH_INVALID_KEY"


async def test_zdr_key_still_reads_usage(
    client: Any, tenant_a: dict[str, str], seed_usage: Any
) -> None:
    """M9(edge): usage/costs is metadata (token counts, billed cost), not payload — always readable.

    Even without asserting a ZDR flag toggle here, the guarantee is that reading aggregate usage is a
    metadata read that composes with ZDR; this test pins the happy read so a future ZDR gate cannot
    silently start refusing it.
    """
    await seed_usage(
        tenant_id=tenant_a["tenant_id"], key_id=tenant_a["key_id"],
        prompt_tokens=3, created_at=DAY0,
    )
    resp = await client.get(
        COSTS_PATH,
        params={"start_time": unix(DAY0), "end_time": unix(DAY1)},
        headers=auth_header(tenant_a["key"]),
    )
    assert resp.status_code == 200, resp.text


async def test_read_writes_no_usage_record(
    client: Any, tenant_a: dict[str, str], seed_usage: Any, ledger_count: Any
) -> None:
    """After: reads never write — usage_records COUNT is unchanged across both endpoints."""
    await seed_usage(
        tenant_id=tenant_a["tenant_id"], key_id=tenant_a["key_id"],
        prompt_tokens=1, cost_usd=Decimal("0.02"), created_at=DAY0,
    )
    before = await ledger_count()
    comp = await client.get(
        COMPLETIONS_PATH,
        params={"start_time": unix(DAY0), "end_time": unix(DAY2)},
        headers=auth_header(tenant_a["key"]),
    )
    costs = await client.get(
        COSTS_PATH,
        params={"start_time": unix(DAY0), "end_time": unix(DAY2)},
        headers=auth_header(tenant_a["key"]),
    )
    # Both must actually SERVE (200) — a 404 would also write nothing, making the
    # no-write assertion vacuously true; require a real read before trusting it.
    assert comp.status_code == 200, comp.text
    assert costs.status_code == 200, costs.text
    after = await ledger_count()
    assert after == before
