"""Directory-scoped `app` fixture override for cross-tenant-config-budget.

TASK.md §5/shared-context: this task's router (`platform_tenant_config_router`) must NOT be
wired into `gateway.main.create_app()` by this build — the orchestrating session registers it
(and the sibling task's routers) into main.py itself, sequentially, once both parallel build
tasks in this milestone are done (main.py is the one file both tasks would otherwise collide
on). Until then, this conftest.py registers the router directly onto the `app` object for
tests running under THIS directory only — pytest resolves the nearest-scope fixture by name,
so `client`/`db_session` (defined in the root tests/conftest.py, requesting `app` by name)
transparently pick up THIS override for every test in `tests/cross_tenant_config_budget/`,
while every other test directory keeps using the root conftest.py's unmodified `app` fixture,
completely untouched by this task.

Mirrors tests/conftest.py's own `app` fixture byte-for-byte plus the one extra
`include_router` call. Intentional duplication, not drift: pytest fixture overriding cannot
"call the fixture it shadows" by the same name, and root conftest.py is shared/frozen
infrastructure this task does not edit.

Before this task's router module exists (RED phase), the import below raises ImportError at
collection time for every test in this directory — the expected, understood RED reason
alongside a bare 404 (see the test module's own docstring).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from gateway.tenants.api.platform_tenant_config_router import platform_tenant_config_router
from tests.credential_stub import install_stub_resolver


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[object]:
    application = create_app(settings)
    install_stub_resolver(application)
    application.include_router(platform_tenant_config_router)
    engine = application.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield application
    await engine.dispose()
