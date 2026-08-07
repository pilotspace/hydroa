# ruff: noqa: F821  — `seed_usage` is intentionally undefined: these fixtures are
# TEXT SPECIMENS for the date-bomb guard, never imported and never executed. Giving
# them a real helper would make them look like tests and invite someone to run them.
# pyright: reportUndefinedVariable=false, reportMissingParameterType=false
"""PLANTED BOMB — reproduces the exact shape that turned `make ci` red on 2026-08-01.

Not a test. Not collected (no `test_` prefix, and `scan_tree` skips `fixtures/` by
default). This module exists so the guard can be SEEN to fire; a guard that has never
failed is not a guard.

The pairing: an absolute seed, and a query whose window is resolved from the wall clock.
They agree until the wall clock leaves July 2026, and then they silently stop overlapping.
"""

from __future__ import annotations

import datetime

INSIDE = datetime.datetime(2026, 7, 15, 12, 0, tzinfo=datetime.UTC)


async def seed_and_query(client, tenant):  # type: ignore[no-untyped-def]
    await seed_usage(tenant, created_at=INSIDE, cost_usd="1.00")
    return await client.get(f"/admin/usage/margin?tenant_id={tenant}&window=month")
