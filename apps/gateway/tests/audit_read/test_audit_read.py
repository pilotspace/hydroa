"""RED suite for audit-read surface (TASK.md §3 v1).

GET /admin/audit is a tenant-scoped paginated read over audit_events. Frozen contract:
  - 200 -> { items:[{id, actor_email, action, target_type, target_id, result, metadata, created_at}], total }
  - AUDIT_READ gated: owner/admin/operator → 200; billing_admin/viewer/member → 403
  - tenant-scoped (only caller's tenant rows), newest-first (created_at DESC, id DESC)
  - limit 1..100 (default 50), offset >=0 (default 0); bad params → 422 ERR_PAYLOAD_INVALID
  - READ-ONLY — never mutates rows

RED before BUILD: the route does not exist yet, so success cases get 404 or 403 — the honest
missing-implementation red.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

from .conftest import (
    AUDIT,
    BASE,
    auth,
    seed_audit_event,
    signup_tenant,
)

# pytest asyncio_mode=auto: `async def test_*` runs without a marker.


def assert_problem(resp: Any, status: int, code: str) -> None:
    assert resp.status_code == status, f"expected {status}, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("code") == code, f"expected {code!r}, got {body.get('code')!r}: {body}"


def _mins(n: int) -> datetime.datetime:
    return BASE + datetime.timedelta(minutes=n)


# ---------------------------------------------------------------------------
# Role 200: owner, admin, operator can read audit log
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("role", [Role.OWNER, Role.ADMIN, Role.OPERATOR])
async def test_audit_read_roles_200(
    client: Any, db_session: AsyncSession, app: Any, role: Role
) -> None:
    # Sign up a real tenant so audit_events reads have a valid tenant_id context.
    # Mint tokens directly via token_service (no DB user insert needed — the endpoint
    # only reads audit_events, never joins to users). This avoids the users.role check
    # constraint which only allows the legacy three roles (owner/admin/member).
    _owner_jwt, tid = await signup_tenant(
        client, tenant_name=f"Audit 200 {role}", email=f"audit200-owner-{role}@audit.io"
    )
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=uuid.UUID(tid),
        role=role,
        email=f"audit200-sub-{role}@audit.io",
    )

    resp = await client.get(AUDIT, headers=auth(str(token)))

    assert resp.status_code == 200, f"role={role} expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "items" in body
    assert "total" in body


# ---------------------------------------------------------------------------
# Role 403: billing_admin, viewer, member are denied (no AUDIT_READ)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("role", [Role.BILLING_ADMIN, Role.VIEWER, Role.MEMBER])
async def test_audit_read_roles_403(
    client: Any, db_session: AsyncSession, app: Any, role: Role
) -> None:
    # Mint tokens directly — no DB insert needed to verify a 403 (permission check fires
    # before any DB read). This also avoids the users.role check constraint.
    _owner_jwt, tid = await signup_tenant(
        client, tenant_name=f"Audit 403 {role}", email=f"audit403-owner-{role}@audit.io"
    )
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(),
        tenant_id=uuid.UUID(tid),
        role=role,
        email=f"audit403-sub-{role}@audit.io",
    )

    resp = await client.get(AUDIT, headers=auth(str(token)))

    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")


# ---------------------------------------------------------------------------
# Shape, total, newest-first ordering
# ---------------------------------------------------------------------------
async def test_audit_items_shape_and_total(client: Any, db_session: AsyncSession) -> None:
    token, tid = await signup_tenant(client, tenant_name="Audit Shape", email="shape@audit.io")
    id1 = await seed_audit_event(db_session, tenant_id=tid, action="key.create", created_at=_mins(1))
    id2 = await seed_audit_event(db_session, tenant_id=tid, action="key.delete", created_at=_mins(2))
    id3 = await seed_audit_event(db_session, tenant_id=tid, action="user.invite", created_at=_mins(3))

    resp = await client.get(AUDIT, headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3, f"expected total=3, got {body['total']}"
    # newest-first: id3 (_mins(3)) > id2 > id1
    assert [item["id"] for item in body["items"]] == [id3, id2, id1]
    first = body["items"][0]
    for field in ("id", "actor_email", "action", "target_type", "target_id", "result", "metadata", "created_at"):
        assert field in first, f"missing field {field!r} in item: {first}"
    # verify metadata is a dict
    assert isinstance(first["metadata"], dict)


# ---------------------------------------------------------------------------
# Pagination bounds / bad parameters → 400 ERR_PAYLOAD_INVALID
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "params",
    [
        {"limit": "0"},
        {"limit": "500"},
        {"offset": "-1"},
        {"limit": "abc"},
        {"offset": "xyz"},
    ],
)
async def test_audit_pagination_bounds(client: Any, db_session: AsyncSession, params: dict[str, str]) -> None:
    token, _tid = await signup_tenant(
        client,
        tenant_name=f"Audit Bounds {list(params.items())}",
        email=f"bounds-{hash(str(params)) % 100000}@audit.io",
    )

    resp = await client.get(AUDIT, params=params, headers=auth(token))

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")


# ---------------------------------------------------------------------------
# Tenant isolation: tenant A only sees A's rows
# ---------------------------------------------------------------------------
async def test_audit_tenant_isolation(client: Any, db_session: AsyncSession) -> None:
    token_a, tid_a = await signup_tenant(client, tenant_name="Audit Iso A", email="iso-a@audit.io")
    _token_b, tid_b = await signup_tenant(client, tenant_name="Audit Iso B", email="iso-b@audit.io")

    id_a1 = await seed_audit_event(db_session, tenant_id=tid_a, action="key.create", created_at=_mins(1))
    id_a2 = await seed_audit_event(db_session, tenant_id=tid_a, action="key.delete", created_at=_mins(2))
    id_b = await seed_audit_event(db_session, tenant_id=tid_b, action="user.invite", created_at=_mins(3))

    resp = await client.get(AUDIT, headers=auth(token_a))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {item["id"] for item in body["items"]}
    assert id_a1 in ids, "tenant A row 1 must be visible"
    assert id_a2 in ids, "tenant A row 2 must be visible"
    assert id_b not in ids, "tenant B row must NOT be visible to tenant A"
    assert body["total"] == 2, f"expected total=2 (only A's rows), got {body['total']}"


# ---------------------------------------------------------------------------
# Unauthenticated → 401
# ---------------------------------------------------------------------------
async def test_audit_no_bearer_denied(client: Any) -> None:
    resp = await client.get(AUDIT)
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# Pagination limit + offset actually pages correctly
# ---------------------------------------------------------------------------
async def test_audit_pagination_pages(client: Any, db_session: AsyncSession) -> None:
    token, tid = await signup_tenant(client, tenant_name="Audit Page", email="page@audit.io")
    ids = [
        await seed_audit_event(db_session, tenant_id=tid, action="key.create", created_at=_mins(i))
        for i in range(1, 6)  # 5 rows, _mins(1) .. _mins(5)
    ]
    # newest-first: ids[4], ids[3], ids[2], ids[1], ids[0]
    resp = await client.get(AUDIT, params={"limit": 2, "offset": 2}, headers=auth(token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 5
    assert [item["id"] for item in body["items"]] == [ids[2], ids[1]]  # 3rd & 4th newest
