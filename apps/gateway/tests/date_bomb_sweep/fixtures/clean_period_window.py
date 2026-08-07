# ruff: noqa: F821  — `seed_usage` is intentionally undefined: these fixtures are
# TEXT SPECIMENS for the date-bomb guard, never imported and never executed. Giving
# them a real helper would make them look like tests and invite someone to run them.
# pyright: reportUndefinedVariable=false, reportMissingParameterType=false
"""CLEAN — `period=` pins the window as firmly as start=/end= does."""

from __future__ import annotations

import datetime

INSIDE = datetime.datetime(2026, 7, 15, 12, 0, tzinfo=datetime.UTC)


async def seed_and_query(client, tenant):  # type: ignore[no-untyped-def]
    await seed_usage(tenant, created_at=INSIDE, cost_usd="1.00")
    return await client.get(f"/admin/invoices?tenant_id={tenant}&period=2026-07")
