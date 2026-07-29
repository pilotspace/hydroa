"""Clean up the singleton `routing_config` row this suite persists.

suite-stability M12. This suite is the only one that writes the operator-wide
`routing_config` singleton, and it relied on the NEXT test's reset to remove the
row. That reset never arrives for a suite which builds its own app:
`tests/routing_admin` calls `create_app(make_settings(...))` directly and never
requests the `app` fixture, so it never runs the per-test sweep — it just reads
whatever `routing_config` happens to hold. The result is four routing_admin
failures whenever `--dist loadscope` puts the two modules on the same worker in
that order, which is exactly what happened once M10 changed module durations
enough to reshuffle the assignment.

PRE-EXISTING, not introduced here: reproduced with the `drop_all`-per-test
conftest at HEAD — same four failures. The DELETE sweep is not the cause; a suite
that leaves global state behind is.

Same fault class as M2, where five suites installed triggers and relied on the
next test's `drop_all` to drop them. The rule that came out of M2 applies here
too: a suite cleans up what it writes, rather than trusting the next test to do
it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests import _redis_env


@pytest.fixture(autouse=True)
async def _clear_routing_config_singleton() -> AsyncIterator[None]:
    """Remove the persisted routing config after every test in this suite.

    Deliberately does NOT depend on the `app` fixture: half this module is pure
    merge unit tests that need no database, and several of the rest dispose
    `app.state.engine` before teardown runs. A short-lived engine keeps the
    cleanup independent of both.
    """
    yield
    engine = create_async_engine(_redis_env.TEST_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DELETE FROM routing_config"))
    finally:
        await engine.dispose()
