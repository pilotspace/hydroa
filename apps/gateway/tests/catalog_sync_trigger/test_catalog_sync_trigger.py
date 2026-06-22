"""RED suite for catalog-sync-trigger (TASK.md §4).

POST /admin/catalog/sync — owner/admin wrapper over SyncCatalogUseCase. Frozen contract (§3):
  - 200 -> { synced:int, synced_at:ISO-8601 } ; member -> 403 ERR_AUTH_FORBIDDEN ;
    missing bearer -> 401 ; upstream down -> 502 ERR_UPSTREAM_UNAVAILABLE (before any write).
  - idempotent; synced_at monotonic (gateway clock).

RED before BUILD: the /admin/catalog/sync route does not exist yet → success cases get 404.
"""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

from .conftest import (
    OPUS,
    SONNET,
    SYNC,
    FakeCatalogSource,
    active_model_count,
    auth,
    install_fake_source,
    mint_role_token,
    signup_and_login,
)


def assert_problem(resp: Any, status: int, code: str) -> None:
    assert resp.status_code == status, f"expected {status}, got {resp.status_code}: {resp.text}"
    assert resp.json().get("code") == code, resp.text


# ---------------------------------------------------------------------------
async def test_owner_sync_success(
    client: Any, app: Any, db_session: AsyncSession
) -> None:
    token, _tid = await signup_and_login(client, tenant_name="Cat A", email="a@cat.io")
    install_fake_source(app, FakeCatalogSource(models=[OPUS, SONNET]))

    resp = await client.post(SYNC, headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["synced"] == 2
    # synced_at is a parseable ISO-8601 timestamp
    datetime.datetime.fromisoformat(body["synced_at"])
    assert await active_model_count(db_session) == 2


# ---------------------------------------------------------------------------
async def test_admin_sync_success(
    client: Any, app: Any, db_session: AsyncSession
) -> None:
    _owner, tid = await signup_and_login(client, tenant_name="Cat AD", email="ad-o@cat.io")
    admin_jwt = await mint_role_token(
        app, db_session, tenant_id=tid, role=Role.ADMIN, email="ad@cat.io"
    )
    install_fake_source(app, FakeCatalogSource(models=[OPUS]))

    resp = await client.post(SYNC, headers=auth(admin_jwt))

    assert resp.status_code == 200, resp.text
    assert resp.json()["synced"] == 1


# ---------------------------------------------------------------------------
async def test_member_denied(client: Any, app: Any, db_session: AsyncSession) -> None:
    _owner, tid = await signup_and_login(client, tenant_name="Cat M", email="m-o@cat.io")
    member_jwt = await mint_role_token(
        app, db_session, tenant_id=tid, role=Role.MEMBER, email="m@cat.io"
    )
    install_fake_source(app, FakeCatalogSource(models=[OPUS, SONNET]))

    resp = await client.post(SYNC, headers=auth(member_jwt))

    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")
    assert await active_model_count(db_session) == 0  # no sync ran


# ---------------------------------------------------------------------------
async def test_member_denied_leaves_existing_catalog_intact(
    client: Any, app: Any, db_session: AsyncSession
) -> None:
    """A member-denied call must not run the sync on a NON-EMPTY catalog (refute EG-2).

    Seed via a real owner sync (2 models), then a member attempts a sync with a DIFFERENT
    source (1 model). The 403 must leave the catalog at 2 — not 0, not 1 — proving the
    denied request never reached the upsert/deactivation path.
    """
    owner, tid = await signup_and_login(client, tenant_name="Cat MP", email="mp-o@cat.io")
    install_fake_source(app, FakeCatalogSource(models=[OPUS, SONNET]))
    assert (await client.post(SYNC, headers=auth(owner))).status_code == 200
    assert await active_model_count(db_session) == 2

    member_jwt = await mint_role_token(
        app, db_session, tenant_id=tid, role=Role.MEMBER, email="mp-m@cat.io"
    )
    install_fake_source(app, FakeCatalogSource(models=[OPUS]))  # would shrink to 1 IF it ran
    resp = await client.post(SYNC, headers=auth(member_jwt))

    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")
    assert await active_model_count(db_session) == 2  # unchanged — the denied sync never ran


# ---------------------------------------------------------------------------
async def test_missing_bearer_denied(
    client: Any, app: Any, db_session: AsyncSession
) -> None:
    install_fake_source(app, FakeCatalogSource(models=[OPUS]))

    resp = await client.post(SYNC)

    assert resp.status_code == 401, resp.text
    assert await active_model_count(db_session) == 0


# ---------------------------------------------------------------------------
async def test_upstream_unavailable(
    client: Any, app: Any, db_session: AsyncSession
) -> None:
    token, _tid = await signup_and_login(client, tenant_name="Cat U", email="u@cat.io")
    install_fake_source(app, FakeCatalogSource(raise_unavailable=True))

    resp = await client.post(SYNC, headers=auth(token))

    assert_problem(resp, 502, "ERR_UPSTREAM_UNAVAILABLE")
    assert await active_model_count(db_session) == 0  # failure raised before any write


# ---------------------------------------------------------------------------
async def test_idempotent(client: Any, app: Any, db_session: AsyncSession) -> None:
    token, _tid = await signup_and_login(client, tenant_name="Cat I", email="i@cat.io")

    install_fake_source(app, FakeCatalogSource(models=[OPUS]))
    first = await client.post(SYNC, headers=auth(token))
    assert first.status_code == 200, first.text

    install_fake_source(app, FakeCatalogSource(models=[OPUS]))
    second = await client.post(SYNC, headers=auth(token))
    assert second.status_code == 200, second.text

    assert first.json()["synced"] == second.json()["synced"] == 1
    assert await active_model_count(db_session) == 1  # converged, no divergent rows


# ---------------------------------------------------------------------------
async def test_synced_at_monotonic(
    client: Any, app: Any, db_session: AsyncSession
) -> None:
    token, _tid = await signup_and_login(client, tenant_name="Cat T", email="t@cat.io")

    install_fake_source(app, FakeCatalogSource(models=[OPUS]))
    first = await client.post(SYNC, headers=auth(token))
    install_fake_source(app, FakeCatalogSource(models=[OPUS]))
    second = await client.post(SYNC, headers=auth(token))

    assert first.status_code == second.status_code == 200
    t1 = datetime.datetime.fromisoformat(first.json()["synced_at"])
    t2 = datetime.datetime.fromisoformat(second.json()["synced_at"])
    assert t2 >= t1  # monotonic gateway clock


# ---------------------------------------------------------------------------
async def test_internal_sync_response_unchanged(
    client: Any, app: Any, db_session: AsyncSession
) -> None:
    """The internal endpoint keeps its byte-identical {synced} shape (no synced_at)."""
    install_fake_source(app, FakeCatalogSource(models=[OPUS]))

    resp = await client.post("/internal/catalog/sync")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"synced": 1}  # exactly — admin's synced_at must NOT leak here
