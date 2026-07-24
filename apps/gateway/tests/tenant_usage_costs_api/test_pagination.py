"""Red suite — keyset pagination over buckets (M7). Endpoint absent → red for missing impl."""

from __future__ import annotations

from typing import Any


from .conftest import COMPLETIONS_PATH, DAY0, DAY1, DAY2, auth_header, unix

async def test_pagination_keyset_no_overlap(
    client: Any, tenant_a: dict[str, str], seed_usage: Any
) -> None:
    """M7: three populated daily buckets, limit=1 → walk pages via next_page with no overlap/gap."""
    days = [DAY0, DAY1, DAY2]
    for i, d in enumerate(days):
        await seed_usage(
            tenant_id=tenant_a["tenant_id"], key_id=tenant_a["key_id"],
            prompt_tokens=(i + 1) * 10, created_at=d,
        )
    end = unix(DAY2) + 24 * 3600
    seen: list[int] = []
    page: str | None = None
    for _ in range(3):
        params: dict[str, Any] = {
            "start_time": unix(DAY0),
            "end_time": end,
            "bucket_width": "1d",
            "limit": "1",
        }
        if page is not None:
            params["page"] = page
        resp = await client.get(
            COMPLETIONS_PATH, params=params, headers=auth_header(tenant_a["key"])
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["data"]) == 1
        seen.append(body["data"][0]["start_time"])
        page = body.get("next_page")
        if page is None:
            break
    # Three distinct buckets walked, ascending, no repeats.
    assert len(seen) == 3
    assert seen == sorted(seen)
    assert len(set(seen)) == 3
    # Last page exhausts the set.
    assert page is None


async def test_page_cursor_malformed_422(client: Any, tenant_a: dict[str, str]) -> None:
    """R8: a malformed page cursor → 422."""
    resp = await client.get(
        COMPLETIONS_PATH,
        params={"start_time": unix(DAY0), "page": "!!!not-a-cursor!!!"},
        headers=auth_header(tenant_a["key"]),
    )
    assert resp.status_code == 422, resp.text
