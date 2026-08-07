# ruff: noqa: F821  — `seed_usage` is intentionally undefined: these fixtures are
# TEXT SPECIMENS for the date-bomb guard, never imported and never executed. Giving
# them a real helper would make them look like tests and invite someone to run them.
# pyright: reportUndefinedVariable=false, reportMissingParameterType=false
"""CLEAN — a bare relative window, seeded from the wall clock.

This is `tests/spend_windows`, `tests/team_governance`, `tests/team_attribution`. The seed
moves with the window, so the pair can never drift apart.
"""

from __future__ import annotations

import datetime


async def seed_and_query(client, tenant):  # type: ignore[no-untyped-def]
    now = datetime.datetime.now(datetime.UTC)
    await seed_usage(tenant, created_at=now, cost_usd="1.00")
    return await client.get(f"/admin/usage?tenant_id={tenant}&window=month")
