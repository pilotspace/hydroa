# ruff: noqa: F821  — `seed_usage` is intentionally undefined: these fixtures are
# TEXT SPECIMENS for the date-bomb guard, never imported and never executed. Giving
# them a real helper would make them look like tests and invite someone to run them.
# pyright: reportUndefinedVariable=false, reportMissingParameterType=false
"""CLEAN — an absolute seed paired with an ABSOLUTE window.

This is `tests/margin_dashboard`'s `INSIDE` + `WINDOW_FROM`/`WINDOW_TO`, which is CORRECT
and was deliberately kept when PR #92 added `INSIDE_CURRENT_MONTH` alongside it. Flagging
this would push someone to "fix" a test that is already right — and to revert #92's fix.
"""

from __future__ import annotations

import datetime

INSIDE = datetime.datetime(2026, 7, 15, 12, 0, tzinfo=datetime.UTC)
WINDOW_FROM = datetime.datetime(2026, 7, 1, tzinfo=datetime.UTC)
WINDOW_TO = datetime.datetime(2026, 8, 1, tzinfo=datetime.UTC)


async def seed_and_query(client, tenant):  # type: ignore[no-untyped-def]
    await seed_usage(tenant, created_at=INSIDE, cost_usd="1.00")
    return await client.get(
        f"/admin/usage/margin?tenant_id={tenant}"
        f"&start={WINDOW_FROM.isoformat()}&end={WINDOW_TO.isoformat()}"
    )
