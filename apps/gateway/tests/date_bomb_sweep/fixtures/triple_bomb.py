# ruff: noqa: F821  — `seed_usage` is intentionally undefined: these fixtures are
# TEXT SPECIMENS for the date-bomb guard, never imported and never executed. Giving
# them a real helper would make them look like tests and invite someone to run them.
# pyright: reportUndefinedVariable=false, reportMissingParameterType=false
"""PLANTED BOMB ×3 — three bombed functions in one module.

First-match-wins reporting hid the third real margin_dashboard bomb
(`test_m8_query_timeout_maps_to_504`) until it was found by hand: the file already had a
finding, so the scan moved on. A file with three bombs must never read as a file with one.
"""

from __future__ import annotations

import datetime

INSIDE = datetime.datetime(2026, 7, 15, 12, 0, tzinfo=datetime.UTC)


async def bomb_one(client, tenant):  # type: ignore[no-untyped-def]
    await seed_usage(tenant, created_at=INSIDE)
    return await client.get(f"/admin/usage?tenant_id={tenant}&window=month")


async def bomb_two(client, tenant):  # type: ignore[no-untyped-def]
    await seed_usage(tenant, created_at=datetime.datetime(2026, 7, 2, tzinfo=datetime.UTC))
    return await client.get(MARGIN_SUMMARY, params={"window": "month"})


async def bomb_three(client, tenant):  # type: ignore[no-untyped-def]
    await seed_usage(tenant, created_at=INSIDE)
    return await client.get(MARGIN_SUMMARY, params={"window": "week"})


async def not_a_bomb(client, tenant):  # type: ignore[no-untyped-def]
    """Same file, correct pairing — proves the count is 3 and not 'every function'."""
    await seed_usage(tenant, created_at=INSIDE)
    return await client.get(MARGIN_SUMMARY, params={"start": "2026-07-01", "end": "2026-08-01"})
