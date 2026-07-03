"""Suite-local fixtures for ops-platform-job-identity (TASK.md §4).

`resolve_platform_credential` composes THREE existing primitives — require_ops
(unchanged, tested in tests/operator_wide_reconciliation/), get_platform_tenant
(platform-tenant-seed), and resolve_provider_credential (credential-resolution-seam,
unchanged) — introducing zero new credential-resolution logic. This suite tests ONLY
the new composition function plus the ONE precondition it relies on; require_ops's own
exhaustive denial-mode coverage (OW1-OW9) is NOT duplicated here.

Uses the standard app/db_session/client fixtures (Base.metadata.create_all — exercises
the platform-tenant-seed ORM __table_args__ the same way two of
tests/platform_tenant_seed/test_platform_tenant_seed.py's own scenarios do). No
migration harness needed: this task tests neither seed idempotency nor migration
ordering (platform-tenant-seed's own job) — only resolve_platform_credential's
composition logic.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.proxy.domain.provider_credentials import (
    BearerCredential,
    ProviderCredential,
    ProviderKeyMissing,
)


class FakePlatformCredentialResolver:
    """Structural TenantCredentialResolver fake, programmable per (tenant_id, provider).

    Implements the resolver Protocol directly (.resolve, not .get) since this suite
    exercises resolve_platform_credential ABOVE the resolver layer — cache/timeout
    mechanics are already covered by tests/credential_resolution_seam/.
    """

    def __init__(self) -> None:
        self._by_key: dict[tuple[uuid.UUID, str], ProviderCredential] = {}
        self.calls: list[tuple[uuid.UUID, str]] = []

    def program(
        self, tenant_id: uuid.UUID, provider: str, credential: ProviderCredential
    ) -> None:
        self._by_key[(tenant_id, provider)] = credential

    async def resolve(self, tenant_id: uuid.UUID, provider: str) -> ProviderCredential:
        self.calls.append((tenant_id, provider))
        cred = self._by_key.get((tenant_id, provider))
        if cred is None:
            raise ProviderKeyMissing(provider)
        return cred


@pytest.fixture
def platform_resolver() -> FakePlatformCredentialResolver:
    return FakePlatformCredentialResolver()


@pytest.fixture
def minimax_credential() -> BearerCredential:
    return BearerCredential(secret="platform-minimax-secret-XYZ")  # noqa: S106


@pytest.fixture
def session_factory(app: object) -> async_sessionmaker[AsyncSession]:
    """The live sessionmaker resolve_platform_credential's new audit-write param needs.

    Added by superadmin-audit-foundation TASK.md §3 Part A (FROZEN @ v1): that task
    retrofits resolve_platform_credential with a new REQUIRED session_factory param
    (used only to schedule a fire-and-forget audit write), so every existing call site
    in this suite — a plain composition-function call, not an HTTP request — must supply
    one to keep compiling/calling. This suite's own credential-resolution assertions are
    otherwise unchanged; app.state.sessionmaker is the same live global every other
    record_audit call site in the app already uses.
    """
    return app.state.sessionmaker  # type: ignore[attr-defined,no-any-return]


async def seed_platform_tenant(session: AsyncSession, *, name: str = "Platform") -> uuid.UUID:
    """Insert a kind='platform' tenants row via raw SQL (mirrors platform_tenant_seed's
    own test style, e.g. test_get_platform_tenant_resolves_and_is_stable) and return its id.
    """
    platform_id = uuid.uuid4()
    await session.execute(
        text("INSERT INTO tenants (id, name, kind) VALUES (:id, :name, 'platform')"),
        {"id": platform_id, "name": name},
    )
    await session.commit()
    return platform_id
