"""Red suite — GET /v1/organization/usage/completions (OpenAI-wire usage series).

Covers M1 (page/bucket envelope + token/request fields), M4 (group_by), M8 (bounds/empty),
M6 (bucket_width), R3/R4/R5/R6/R7/R9 (param rejects). Endpoint absent → red for missing impl.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


from .conftest import (
    COMPLETIONS_PATH,
    DAY0,
    DAY1,
    DAY2,
    auth_header,
    unix,
)

async def test_completions_happy_daily_buckets(
    client: Any, tenant_a: dict[str, str], seed_usage: Any
) -> None:
    """M1/M8: two rows on two UTC days → two 1d buckets ASC with correct token/request totals."""
    await seed_usage(
        tenant_id=tenant_a["tenant_id"], key_id=tenant_a["key_id"],
        prompt_tokens=100, completion_tokens=50, created_at=DAY0,
    )
    await seed_usage(
        tenant_id=tenant_a["tenant_id"], key_id=tenant_a["key_id"],
        prompt_tokens=7, completion_tokens=3, created_at=DAY1,
    )
    resp = await client.get(
        COMPLETIONS_PATH,
        params={"start_time": unix(DAY0), "end_time": unix(DAY2), "bucket_width": "1d"},
        headers=auth_header(tenant_a["key"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["object"] == "page"
    buckets = body["data"]
    assert len(buckets) == 2
    starts = [b["start_time"] for b in buckets]
    assert starts == sorted(starts)  # ASC
    r0 = buckets[0]["results"][0]
    assert r0["object"] == "organization.usage.completions.result"
    assert r0["input_tokens"] == 100
    assert r0["output_tokens"] == 50
    assert r0["num_model_requests"] == 1
    assert body["has_more"] is False


async def test_group_by_model_splits_results(
    client: Any, tenant_a: dict[str, str], seed_usage: Any
) -> None:
    """M4: two models in one bucket, group_by=model → one result per model, key echoed."""
    await seed_usage(
        tenant_id=tenant_a["tenant_id"], key_id=tenant_a["key_id"],
        model_id="openai/gpt-4o", prompt_tokens=10, created_at=DAY0,
    )
    await seed_usage(
        tenant_id=tenant_a["tenant_id"], key_id=tenant_a["key_id"],
        model_id="anthropic/claude-3", prompt_tokens=20, created_at=DAY0,
    )
    resp = await client.get(
        COMPLETIONS_PATH,
        params={"start_time": unix(DAY0), "end_time": unix(DAY1), "group_by": "model"},
        headers=auth_header(tenant_a["key"]),
    )
    assert resp.status_code == 200, resp.text
    results = resp.json()["data"][0]["results"]
    models = {r["model"] for r in results}
    assert models == {"openai/gpt-4o", "anthropic/claude-3"}


async def test_group_by_api_key_id_completions(
    client: Any, tenant_a: dict[str, str], second_key_a: str, seed_usage: Any
) -> None:
    """M4: group_by=api_key_id → per-key results within the tenant."""
    await seed_usage(
        tenant_id=tenant_a["tenant_id"], key_id=tenant_a["key_id"], prompt_tokens=5, created_at=DAY0
    )
    await seed_usage(
        tenant_id=tenant_a["tenant_id"], key_id=second_key_a, prompt_tokens=9, created_at=DAY0
    )
    resp = await client.get(
        COMPLETIONS_PATH,
        params={"start_time": unix(DAY0), "end_time": unix(DAY1), "group_by": "api_key_id"},
        headers=auth_header(tenant_a["key"]),
    )
    assert resp.status_code == 200, resp.text
    key_ids = {r["api_key_id"] for r in resp.json()["data"][0]["results"]}
    assert {tenant_a["key_id"], second_key_a} <= key_ids


async def test_empty_window_returns_empty_page_not_404(
    client: Any, tenant_a: dict[str, str]
) -> None:
    """M8: a window with no rows → 200 empty page, never 404."""
    resp = await client.get(
        COMPLETIONS_PATH,
        params={"start_time": unix(DAY0), "end_time": unix(DAY1)},
        headers=auth_header(tenant_a["key"]),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"] == []
    assert body["has_more"] is False
    assert body["next_page"] is None


async def test_start_time_required_422(client: Any, tenant_a: dict[str, str]) -> None:
    """R3: start_time absent → 422 ERR_PAYLOAD_INVALID."""
    resp = await client.get(COMPLETIONS_PATH, headers=auth_header(tenant_a["key"]))
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"] == "ERR_PAYLOAD_INVALID"


async def test_end_time_not_after_start_422(client: Any, tenant_a: dict[str, str]) -> None:
    """R4: end_time <= start_time → 422."""
    resp = await client.get(
        COMPLETIONS_PATH,
        params={"start_time": unix(DAY1), "end_time": unix(DAY0)},
        headers=auth_header(tenant_a["key"]),
    )
    assert resp.status_code == 422, resp.text


async def test_bucket_width_invalid_422(client: Any, tenant_a: dict[str, str]) -> None:
    """R5/M6: an unsupported bucket_width → 422."""
    resp = await client.get(
        COMPLETIONS_PATH,
        params={"start_time": unix(DAY0), "bucket_width": "1w"},
        headers=auth_header(tenant_a["key"]),
    )
    assert resp.status_code == 422, resp.text


async def test_group_by_unknown_field_422(client: Any, tenant_a: dict[str, str]) -> None:
    """R6: unknown group_by field → 422."""
    resp = await client.get(
        COMPLETIONS_PATH,
        params={"start_time": unix(DAY0), "group_by": "project"},
        headers=auth_header(tenant_a["key"]),
    )
    assert resp.status_code == 422, resp.text


async def test_range_too_large_422(client: Any, tenant_a: dict[str, str]) -> None:
    """R7: minute buckets over a span beyond the cap → 422 (USAGE_RANGE_TOO_LARGE)."""
    thirty_days = 30 * 24 * 3600
    resp = await client.get(
        COMPLETIONS_PATH,
        params={
            "start_time": unix(DAY0),
            "end_time": unix(DAY0) + thirty_days,
            "bucket_width": "1m",
        },
        headers=auth_header(tenant_a["key"]),
    )
    assert resp.status_code == 422, resp.text


async def test_limit_out_of_range_422(client: Any, tenant_a: dict[str, str]) -> None:
    """R9: limit outside 1..180 → 422."""
    for bad in ("0", "500"):
        resp = await client.get(
            COMPLETIONS_PATH,
            params={"start_time": unix(DAY0), "limit": bad},
            headers=auth_header(tenant_a["key"]),
        )
        assert resp.status_code == 422, (bad, resp.text)
