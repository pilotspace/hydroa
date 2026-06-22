"""RED suite for upstream-health-view (TASK.md §4).

GET /admin/health/upstreams is a thin, owner/admin-scoped, READ-ONLY view that derives the
up/down status of each MONITORED upstream from the durable health events in alert_events
(system rows, tenant_id NULL). Frozen contract (§3):
  - 200 -> {checked_at:str, upstreams:[{name, status:"up"|"down", last_event_at, last_event_type}]}
  - status := down if latest health event is upstream_health_fail; up if upstream_health_recovered;
    up with null last_event_* if no health event ever recorded.
  - MONITORED today = ["openrouter"] (the only upstream actually pinged) — NO fabricated rows for
    anthropic/gemini/bedrock/azure/openai.
  - member role -> 403 ERR_AUTH_FORBIDDEN; missing bearer -> 401; READ-ONLY (never mutates rows).

RED before BUILD: the route does not exist yet, so success cases get 404 — the honest
missing-implementation red.
"""

from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

from .conftest import (
    BASE,
    HEALTH,
    alert_row_count,
    auth,
    mint_role_token,
    seed_health_event,
    signup_tenant,
)

# pytest asyncio_mode=auto: `async def test_*` runs without a marker.


def assert_problem(resp: Any, status: int, code: str) -> None:
    assert resp.status_code == status, f"expected {status}, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("code") == code, f"expected {code!r}, got {body.get('code')!r}: {body}"


def _mins(n: int) -> datetime.datetime:
    return BASE + datetime.timedelta(minutes=n)


def _openrouter(body: dict[str, Any]) -> dict[str, Any]:
    """Pluck the openrouter entry from the response upstreams list."""
    matches = [u for u in body["upstreams"] if u["name"] == "openrouter"]
    assert matches, f"openrouter not in upstreams: {body}"
    return matches[0]


# ---------------------------------------------------------------------------
# No incident ever recorded -> monitored upstream reads up
# ---------------------------------------------------------------------------
async def test_no_events_reports_up(client: Any, db_session: AsyncSession) -> None:
    token, _tid = await signup_tenant(client, tenant_name="Health A", email="a@health.io")

    resp = await client.get(HEALTH, headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("checked_at"), body
    entry = _openrouter(body)
    assert entry["status"] == "up"
    assert entry["last_event_at"] is None
    assert entry["last_event_type"] is None


# ---------------------------------------------------------------------------
# Latest health event is a failure -> upstream reads down
# ---------------------------------------------------------------------------
async def test_latest_fail_reports_down(client: Any, db_session: AsyncSession) -> None:
    token, _tid = await signup_tenant(client, tenant_name="Health B", email="b@health.io")
    await seed_health_event(db_session, event_type="upstream_health_fail", created_at=_mins(5))

    resp = await client.get(HEALTH, headers=auth(token))

    assert resp.status_code == 200, resp.text
    entry = _openrouter(resp.json())
    assert entry["status"] == "down"
    assert entry["last_event_type"] == "upstream_health_fail"
    assert entry["last_event_at"] == _mins(5).replace(tzinfo=None).isoformat()


# ---------------------------------------------------------------------------
# Recovery after a failure -> upstream reads up again
# ---------------------------------------------------------------------------
async def test_recovery_reports_up(client: Any, db_session: AsyncSession) -> None:
    token, _tid = await signup_tenant(client, tenant_name="Health C", email="c@health.io")
    await seed_health_event(db_session, event_type="upstream_health_fail", created_at=_mins(1))
    await seed_health_event(db_session, event_type="upstream_health_recovered", created_at=_mins(2))

    resp = await client.get(HEALTH, headers=auth(token))

    assert resp.status_code == 200, resp.text
    entry = _openrouter(resp.json())
    assert entry["status"] == "up"
    assert entry["last_event_type"] == "upstream_health_recovered"
    assert entry["last_event_at"] == _mins(2).replace(tzinfo=None).isoformat()


# ---------------------------------------------------------------------------
# Symmetric ordering: recovered THEN a later fail -> reads down (guards ORDER BY DESC)
# (refute EG-1)
# ---------------------------------------------------------------------------
async def test_recovered_then_fail_reports_down(client: Any, db_session: AsyncSession) -> None:
    token, _tid = await signup_tenant(client, tenant_name="Health RF", email="rf@health.io")
    await seed_health_event(db_session, event_type="upstream_health_recovered", created_at=_mins(1))
    await seed_health_event(db_session, event_type="upstream_health_fail", created_at=_mins(2))

    resp = await client.get(HEALTH, headers=auth(token))

    assert resp.status_code == 200, resp.text
    entry = _openrouter(resp.json())
    assert entry["status"] == "down"
    assert entry["last_event_type"] == "upstream_health_fail"
    assert entry["last_event_at"] == _mins(2).replace(tzinfo=None).isoformat()


# ---------------------------------------------------------------------------
# checked_at is a real ISO-8601 timestamp, not just any truthy string (refute EG-3)
# ---------------------------------------------------------------------------
async def test_checked_at_is_isoformat(client: Any, db_session: AsyncSession) -> None:
    token, _tid = await signup_tenant(client, tenant_name="Health CA", email="ca@health.io")

    resp = await client.get(HEALTH, headers=auth(token))

    assert resp.status_code == 200, resp.text
    checked_at = resp.json()["checked_at"]
    # raises ValueError if not a valid isoformat -> a non-ISO string fails the test
    parsed = datetime.datetime.fromisoformat(checked_at)
    assert parsed.tzinfo is not None  # contract: UTC-aware isoformat


# ---------------------------------------------------------------------------
# Isolation guard: a tenant-OWNED health row (tenant_id != NULL) must NOT flip status
# (refute EG-4 — proves the WHERE tenant_id IS NULL filter is strict, not cosmetic)
# ---------------------------------------------------------------------------
async def test_tenant_owned_health_row_excluded(client: Any, db_session: AsyncSession) -> None:
    token, tid = await signup_tenant(client, tenant_name="Health ISO", email="iso@health.io")
    # A tenant-owned fail row exists, but NO system (NULL-tenant) health row does.
    await seed_health_event(
        db_session, event_type="upstream_health_fail", created_at=_mins(9), tenant_id=tid
    )

    resp = await client.get(HEALTH, headers=auth(token))

    assert resp.status_code == 200, resp.text
    entry = _openrouter(resp.json())
    # the tenant-owned row must be invisible to the platform derivation -> stays up
    assert entry["status"] == "up"
    assert entry["last_event_at"] is None
    assert entry["last_event_type"] is None


# ---------------------------------------------------------------------------
# Status is read-only — the GET never mutates alert_events
# ---------------------------------------------------------------------------
async def test_read_only_no_row_mutation(client: Any, db_session: AsyncSession) -> None:
    token, _tid = await signup_tenant(client, tenant_name="Health RO", email="ro@health.io")
    await seed_health_event(db_session, event_type="upstream_health_fail", created_at=_mins(1))
    await seed_health_event(db_session, event_type="upstream_health_recovered", created_at=_mins(2))
    before = await alert_row_count(db_session)

    resp = await client.get(HEALTH, headers=auth(token))
    assert resp.status_code == 200, resp.text

    after = await alert_row_count(db_session)
    assert after == before, f"row count changed {before} -> {after}"


# ---------------------------------------------------------------------------
# Admin (non-owner) is allowed
# ---------------------------------------------------------------------------
async def test_admin_allowed(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Health AD", email="ad-owner@health.io")
    admin_jwt = await mint_role_token(
        app, db_session, tenant_id=tid, role=Role.ADMIN, email="ad-admin@health.io"
    )

    resp = await client.get(HEALTH, headers=auth(admin_jwt))

    assert resp.status_code == 200, resp.text
    # admin gets the same real content an owner would (not merely an empty list)
    entry = _openrouter(resp.json())
    assert entry["status"] == "up"


# ---------------------------------------------------------------------------
# Member is forbidden (403) and nothing is mutated
# ---------------------------------------------------------------------------
async def test_member_forbidden(client: Any, db_session: AsyncSession, app: Any) -> None:
    _owner, tid = await signup_tenant(client, tenant_name="Health M", email="m-owner@health.io")
    member_jwt = await mint_role_token(
        app, db_session, tenant_id=tid, role=Role.MEMBER, email="m-member@health.io"
    )
    before = await alert_row_count(db_session)

    resp = await client.get(HEALTH, headers=auth(member_jwt))

    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")
    assert await alert_row_count(db_session) == before


# ---------------------------------------------------------------------------
# Missing bearer is unauthorized (401) and nothing is mutated
# ---------------------------------------------------------------------------
async def test_missing_bearer_unauthorized(client: Any, db_session: AsyncSession) -> None:
    before = await alert_row_count(db_session)

    resp = await client.get(HEALTH)

    assert resp.status_code == 401, resp.text
    assert await alert_row_count(db_session) == before


# ---------------------------------------------------------------------------
# Honesty: no fabricated rows for unpinged providers — only monitored upstreams listed
# ---------------------------------------------------------------------------
async def test_unmonitored_providers_absent(client: Any, db_session: AsyncSession) -> None:
    token, _tid = await signup_tenant(client, tenant_name="Health U", email="u@health.io")
    await seed_health_event(db_session, event_type="upstream_health_recovered", created_at=_mins(1))

    resp = await client.get(HEALTH, headers=auth(token))

    assert resp.status_code == 200, resp.text
    names = {u["name"] for u in resp.json()["upstreams"]}
    assert names == {"openrouter"}, f"unexpected fabricated upstreams: {names}"
