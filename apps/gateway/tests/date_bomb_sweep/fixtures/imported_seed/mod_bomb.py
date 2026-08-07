# ruff: noqa: F821  — `seed_usage` is intentionally undefined: these fixtures are
# TEXT SPECIMENS for the date-bomb guard, never imported and never executed. Giving
# them a real helper would make them look like tests and invite someone to run them.
# pyright: reportUndefinedVariable=false, reportMissingParameterType=false
"""PLANTED BOMB — the seed arrives by import, the relative window is local."""

from __future__ import annotations

from .seeds import INSIDE


async def seed_and_query(client, tenant):  # type: ignore[no-untyped-def]
    await seed_usage(tenant, created_at=INSIDE, cost_usd="1.00")
    return await client.get(f"/admin/usage/margin?tenant_id={tenant}&window=month")
