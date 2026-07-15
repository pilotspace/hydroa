"""RED suite for tenant-activity-tab's BACKEND scenarios (TASK.md §2/§3 — FROZEN @ v1).

GET /admin/platform/tenants/{tenant_id}/audit is a NEW superadmin-scoped, any-target-tenant
audit-read route — the SECOND door onto the same audit_events trail the self-service
GET /admin/audit (tests/audit_read) already exposes, reusing AuditRepository verbatim and the
require_superadmin -> authorize_tenant_scope -> tenant-existence-404 recipe
platform_tenants_router.py/platform_users_router.py already established.

Covers this task's own backend scenarios (§2): M1-M6, R6-R8, R10. The 6 frontend scenarios
(M7-M13 minus M13's backend half, R1-R5, R9) live in apps/dashboard/tests/platform-activity-tab.test.tsx
and platform-tenant-detail.test.tsx instead — not this file's concern.

RED before BUILD: `gateway.tenants.api.platform_audit_router` does not exist yet, and the route
is not registered in main.py, so:
  - every HTTP-driven test below gets FastAPI's own bare 404 ("no route matches this path") for
    ANY method/role/tenant_id combination — this is why every assertion below checks the EXACT
    status code AND the EXACT `code` field (e.g. "ERR_TENANT_NOT_FOUND"), never just "not 200":
    the bare pre-route 404 has no `code` key at all, so a body-shape/code assertion still fails
    honestly even where the raw status code coincidentally matches (R7's 404 case).
  - the 3 tests that directly import gateway.tenants.api.platform_audit_router (module-registration,
    timeout-wrap, and no-cross-router-import checks) fail with ModuleNotFoundError.
Both are the missing-implementation RED reason, never a fixture/harness bug — matching the
house convention named in tests/audit_read/test_audit_read.py's and
tests/platform_tenant_directory/test_platform_tenant_directory.py's own module docstrings.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.tenants.domain.entities import Role

from .conftest import (
    audit_path,
    await_audit_count,
    bearer,
    count_audit_rows,
    drain_fire_and_forget,
    fetch_one_audit_row,
    mins,
    seed_audit_event,
    seed_customer_tenant,
)

# pytest asyncio_mode=auto: `async def test_*` runs without a marker (matches every sibling
# platform_*_router.py suite this task's own §0 GROUND cites).


def assert_problem(resp: httpx.Response, status: int, code: str) -> None:
    assert resp.status_code == status, f"expected {status}, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body.get("code") == code, f"expected {code!r}, got {body.get('code')!r}: {body}"
    assert "items" not in body, f"an error response must never carry audit items: {body}"


# ===========================================================================
# M1, R6 — auth chain: missing/invalid bearer -> 401; non-superadmin role -> 403
# ===========================================================================


async def test_missing_bearer_token_401(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """M1(a), R6: no Authorization header at all -> 401 ERR_AUTH_INVALID_TOKEN, and no
    platform.audit.list row is ever scheduled (the require_superadmin Depends raises before the
    route body — and therefore emit_platform_audit — ever runs)."""
    tid = await seed_customer_tenant(db_session, name="NoAuthTenant")
    before = await count_audit_rows(db_session, action="platform.audit.list")

    resp = await client.get(audit_path(tid))

    assert_problem(resp, 401, "ERR_AUTH_INVALID_TOKEN")
    await drain_fire_and_forget()
    after = await count_audit_rows(db_session, action="platform.audit.list")
    assert after == before, "no audit-list row may be scheduled for an unauthenticated request"


async def test_invalid_bearer_token_401(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
) -> None:
    """M1(a), R6: a syntactically-present but garbage bearer token -> 401
    ERR_AUTH_INVALID_TOKEN — same code as a missing token (require_superadmin's own
    _resolve_identity step), never a 500 or a silently-accepted request."""
    tid = await seed_customer_tenant(db_session, name="BadAuthTenant")

    resp = await client.get(audit_path(tid), headers=bearer("garbage-not-a-real-jwt"))

    assert_problem(resp, 401, "ERR_AUTH_INVALID_TOKEN")


@pytest.mark.parametrize(
    "role", [Role.OWNER, Role.ADMIN, Role.OPERATOR, Role.BILLING_ADMIN, Role.VIEWER, Role.MEMBER]
)
async def test_non_superadmin_role_403(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    app: Any,
    role: Role,
) -> None:
    """M1(a), R6: every non-superadmin role is rejected 403 ERR_AUTH_FORBIDDEN, even one with
    full permissions inside its OWN tenant — this route tree is superadmin-only, mirroring
    platform_tenants_router.py's/platform_users_router.py's own established behavior exactly.
    No audit row (from ANY tenant) is ever returned in the response body."""
    tid = await seed_customer_tenant(db_session, name=f"NonSuperadmin{role}")
    own_tenant_id = uuid.uuid4()
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(), tenant_id=own_tenant_id, role=role, email=f"nonsuper-{role}@x.io"
    )
    before = await count_audit_rows(db_session, action="platform.audit.list")

    # Try both the caller's OWN tenant_id and a completely different one — neither is reachable.
    for target in (own_tenant_id, tid):
        resp = await client.get(audit_path(target), headers=bearer(str(token)))
        assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")

    await drain_fire_and_forget()
    after = await count_audit_rows(db_session, action="platform.audit.list")
    assert after == before, "no audit-list row may be scheduled for a rejected non-superadmin call"


# ===========================================================================
# M1, R7 — tenant-existence check: an unknown tenant_id 404s, AFTER auth, BEFORE any query
# ===========================================================================


async def test_unknown_tenant_id_404(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """M1(c), R7: a syntactically-valid tenant_id with no matching row -> 404
    ERR_TENANT_NOT_FOUND, BEFORE any AuditRepository call — proven indirectly by asserting no
    platform.audit.list row is scheduled for that request (the emit call only ever follows a
    successful repository read on the success path)."""
    missing_id = uuid.uuid4()
    before = await count_audit_rows(db_session, action="platform.audit.list")

    resp = await client.get(audit_path(missing_id), headers=bearer(superadmin_token))

    assert_problem(resp, 404, "ERR_TENANT_NOT_FOUND")
    await drain_fire_and_forget()
    after = await count_audit_rows(db_session, action="platform.audit.list")
    assert after == before, "no audit-list row may be scheduled when the tenant does not exist"


async def test_auth_chain_rejects_non_superadmin_before_checking_tenant_existence(
    client: httpx.AsyncClient,
    app: Any,
) -> None:
    """M1 ordering: a non-superadmin caller targeting a tenant_id that does not exist AT ALL
    still gets 403 ERR_AUTH_FORBIDDEN, never 404 — proving require_superadmin's role gate runs
    and rejects strictly BEFORE the tenant-existence check ever gets a chance to fire (mirrors
    platform_tenant_directory's own `test_get_one_rejects_non_superadmin_targeting_other_tenant`
    reasoning: this route tree is superadmin-only, so a wrong-role caller never reaches step (c)
    regardless of whether the path tenant_id happens to resolve)."""
    definitely_missing_tenant_id = uuid.uuid4()
    token, _ = app.state.token_service.issue(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), role=Role.OWNER, email="order-owner@x.io"
    )

    resp = await client.get(audit_path(definitely_missing_tenant_id), headers=bearer(str(token)))

    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")


# ===========================================================================
# M2 — the route lives in its own new router module, registered additively
# ===========================================================================


async def test_route_registered_in_its_own_new_router_module() -> None:
    """M2: platform_audit_router is its own new APIRouter (not a route grafted onto
    platform_tenants_router.py), with the exact frozen prefix/tags shape."""
    from gateway.tenants.api.platform_audit_router import platform_audit_router
    from gateway.tenants.api.platform_tenants_router import (
        platform_tenants_router,
    )

    assert platform_audit_router is not platform_tenants_router, (
        "the new route must live in its OWN router object, not be added to "
        "platform_tenants_router.py (§0 Issues/Risks #4)"
    )
    assert platform_audit_router.prefix == "/admin/platform/tenants/{tenant_id}/audit"
    assert "platform-admin" in platform_audit_router.tags

    route_paths = {getattr(r, "path", None) for r in platform_audit_router.routes}
    assert "/admin/platform/tenants/{tenant_id}/audit" in route_paths


async def test_route_is_wired_into_the_running_app(
    client: httpx.AsyncClient,
    superadmin_token: str,
) -> None:
    """M2: the route is actually reachable through the live `app` (main.py's additive
    app.include_router call), not merely defined but unregistered."""
    resp = await client.get(audit_path(uuid.uuid4()), headers=bearer(superadmin_token))
    # Reachable at all (a 404 TENANT_NOT_FOUND from OUR handler, never FastAPI's bare
    # route-not-found 404 which carries no "code" key whatsoever).
    assert resp.status_code == 404
    assert resp.json().get("code") == "ERR_TENANT_NOT_FOUND"


# ===========================================================================
# M3, R8 — pagination params: parsed + validated, never silently clamped
# ===========================================================================


@pytest.mark.parametrize(
    "params",
    [
        {"limit": "0"},
        {"limit": "101"},
        {"offset": "-1"},
        {"limit": "abc"},
        {"offset": "xyz"},
    ],
)
async def test_pagination_rejects_out_of_range_or_unparseable(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    params: dict[str, str],
) -> None:
    """M3, R8: an out-of-range or unparseable limit/offset is rejected 422
    ERR_PAYLOAD_INVALID BEFORE any repository call runs — never silently clamped or defaulted."""
    tid = await seed_customer_tenant(db_session, name=f"PagingBounds{params}")
    before = await count_audit_rows(db_session, action="platform.audit.list")

    resp = await client.get(audit_path(tid), params=params, headers=bearer(superadmin_token))

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    await drain_fire_and_forget()
    after = await count_audit_rows(db_session, action="platform.audit.list")
    assert after == before, (
        "a rejected pagination request must never reach the repository/audit-emit step"
    )


async def test_pagination_defaults_when_omitted(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """M3: omitting limit/offset entirely defaults to limit=50/offset=0 and still succeeds."""
    tid = await seed_customer_tenant(db_session, name="PagingDefaults")
    for i in range(1, 4):
        await seed_audit_event(db_session, tenant_id=tid, action=f"k.{i}", created_at=mins(i))

    resp = await client.get(audit_path(tid), headers=bearer(superadmin_token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3


# ===========================================================================
# M4 — reuses AuditRepository verbatim, scoped by the PATH tenant_id
# ===========================================================================


async def test_reuses_audit_repository_scoped_by_path_tenant_never_identity_tenant(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    platform_tenant_id: uuid.UUID,
) -> None:
    """M4: the route queries the PATH tenant_id, never identity.tenant_id (the superadmin's own
    reserved platform tenant). Seed rows under BOTH the platform tenant (the superadmin's own
    identity.tenant_id) and a separate customer tenant (the path target); only the path target's
    rows may ever appear."""
    target_tid = await seed_customer_tenant(db_session, name="PathScopedTarget")
    target_row_id = await seed_audit_event(
        db_session, tenant_id=target_tid, action="target.event", created_at=mins(1)
    )
    own_row_id = await seed_audit_event(
        db_session, tenant_id=platform_tenant_id, action="own.event", created_at=mins(1)
    )

    resp = await client.get(audit_path(target_tid), headers=bearer(superadmin_token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    ids = {item["id"] for item in body["items"]}
    assert target_row_id in ids, "the path target tenant's own row must be visible"
    assert own_row_id not in ids, (
        "the superadmin's OWN identity.tenant_id rows must never leak into a different "
        "tenant's activity view"
    )
    assert body["total"] == 1


async def test_both_repository_calls_wrapped_in_a_single_timeout_budget() -> None:
    """M4: count_for_tenant + list_for_tenant_paged are wrapped in one asyncio.timeout(...)
    block, using a new LOCAL 30.0s constant (never cross-file-imported from usage/api/router.py's
    own _AUDIT_READ_TIMEOUT_SECONDS) — mirrors get_audit's own already-shipped IO
    design-for-failure guard (CLAUDE.md's global IO rule). Verified by reading the built
    module's own source directly, matching this task's own persona instruction to verify
    security/IO-critical claims against real file content rather than assume."""
    import inspect

    from gateway.tenants.api import platform_audit_router as par

    source = inspect.getsource(par)
    assert "asyncio.timeout(" in source
    assert "_AUDIT_READ_TIMEOUT_SECONDS" in source
    assert par._AUDIT_READ_TIMEOUT_SECONDS == 30.0  # noqa: SLF001


# ===========================================================================
# M5 — response shape matches get_audit's own field-for-field, newest-first
# ===========================================================================


async def test_response_shape_matches_self_service_sibling_field_for_field(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """M5: the 200 body is { items: [...], total } with each item carrying exactly
    id/actor_email/action/target_type/target_id/result/metadata/created_at — field-for-field
    identical to get_audit's own AuditListResponse/AuditEventItem shape."""
    tid = await seed_customer_tenant(db_session, name="ShapeCo")
    await seed_audit_event(
        db_session,
        tenant_id=tid,
        action="key.create",
        actor_email="shape-actor@example.com",
        metadata={"foo": "bar"},
        created_at=mins(1),
    )

    resp = await client.get(audit_path(tid), headers=bearer(superadmin_token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body.keys()) == {"items", "total"}
    first = body["items"][0]
    assert set(first.keys()) == {
        "id",
        "actor_email",
        "action",
        "target_type",
        "target_id",
        "result",
        "metadata",
        "created_at",
    }
    assert first["actor_email"] == "shape-actor@example.com"
    assert first["metadata"] == {"foo": "bar"}
    assert isinstance(first["metadata"], dict)


async def test_response_newest_first_ordering(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """M5: rows are ordered newest-first (created_at DESC, id DESC), inherited from
    list_for_tenant_paged's own ORDER BY — matches get_audit's own ordering exactly."""
    tid = await seed_customer_tenant(db_session, name="OrderingCo")
    id1 = await seed_audit_event(db_session, tenant_id=tid, action="a.1", created_at=mins(1))
    id2 = await seed_audit_event(db_session, tenant_id=tid, action="a.2", created_at=mins(2))
    id3 = await seed_audit_event(db_session, tenant_id=tid, action="a.3", created_at=mins(3))

    resp = await client.get(audit_path(tid), headers=bearer(superadmin_token))

    assert resp.status_code == 200, resp.text
    assert [item["id"] for item in resp.json()["items"]] == [id3, id2, id1]


# ===========================================================================
# M6 — a successful list itself schedules exactly one platform.audit.list row
# ===========================================================================


async def test_successful_list_is_itself_audited(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
    superadmin_user_id: uuid.UUID,
) -> None:
    """M6: on the success path, exactly one NEW audit_events row is scheduled
    (action=platform.audit.list, target_type=audit, target_id=None,
    target_tenant_id=<path tenant_id>) — fire-and-forget, matching the zero-exception 15/15
    existing platform_*_router.py convention."""
    tid = await seed_customer_tenant(db_session, name="AuditedReadCo")
    before = await count_audit_rows(db_session, action="platform.audit.list")

    resp = await client.get(audit_path(tid), headers=bearer(superadmin_token))
    assert resp.status_code == 200, resp.text

    after = await await_audit_count(
        db_session, action="platform.audit.list", expected=before + 1
    )
    assert after == before + 1, "exactly one new platform.audit.list row must be scheduled"

    row = await fetch_one_audit_row(db_session, action="platform.audit.list")
    assert row is not None
    tenant_id, actor_user_id, _actor_email, action, target_type, target_id, result, metadata = row
    assert str(tenant_id) == str(tid), (
        "target_tenant_id must be the PATH tenant, not the caller's own"
    )
    assert actor_user_id == superadmin_user_id
    assert action == "platform.audit.list"
    assert target_type == "audit"
    assert target_id is None
    assert result == "success"
    assert metadata == {}


async def test_audit_write_never_blocks_or_changes_the_response(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """M6: the emit_platform_audit call is fire-and-forget/fail-open — the HTTP response
    itself returns 200 immediately, never waiting on or being altered by the audit write."""
    tid = await seed_customer_tenant(db_session, name="FireAndForgetCo")

    resp = await client.get(audit_path(tid), headers=bearer(superadmin_token))

    assert resp.status_code == 200
    assert "items" in resp.json() and "total" in resp.json()


# ===========================================================================
# Edge cases: zero audit history, null actor/target fields
# ===========================================================================


async def test_zero_audit_history_returns_empty_items_and_zero_total(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """Edge case (feeds M11's frontend Empty state): a tenant with no audit_events rows at all
    gets a clean 200 with items=[] and total=0 — never a 404 or an error."""
    tid = await seed_customer_tenant(db_session, name="NoHistoryCo")

    resp = await client.get(audit_path(tid), headers=bearer(superadmin_token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_null_actor_and_target_fields_pass_through_gracefully(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    superadmin_token: str,
) -> None:
    """Edge case (feeds M13's frontend graceful-placeholder rendering): a row whose
    actor_email/target_type/target_id are NULL (a system event, or a since-deleted actor/target
    — audit_events_orm.py's own no-FK-by-design note) still serializes cleanly as JSON null,
    never a crash or a dropped row."""
    tid = await seed_customer_tenant(db_session, name="NullFieldsCo")
    row_id = await seed_audit_event(
        db_session,
        tenant_id=tid,
        actor_email=None,
        target_type=None,
        target_id=None,
        action="system.sweep",
        created_at=mins(1),
    )

    resp = await client.get(audit_path(tid), headers=bearer(superadmin_token))

    assert resp.status_code == 200, resp.text
    item = next(i for i in resp.json()["items"] if i["id"] == row_id)
    assert item["actor_email"] is None
    assert item["target_type"] is None
    assert item["target_id"] is None
    assert item["action"] == "system.sweep"  # every other field on the row still renders normally


# ===========================================================================
# R10 — no cross-router-module private import
# ===========================================================================


async def test_no_cross_router_module_private_import() -> None:
    """R10: the new file does NOT import _parse_pagination, AuditListResponse, or
    AuditEventItem from usage/api/router.py — its own pagination parser and response models are
    declared locally (matches platform_users_router.py's own DTO-redeclaration convention)."""
    import inspect

    from gateway.tenants.api import platform_audit_router as par
    from gateway.usage.api import router as usage_router_mod

    source = inspect.getsource(par)
    assert "from gateway.usage.api.router import" not in source
    assert "usage.api.router" not in source, (
        "no import path referencing usage/api/router.py may appear anywhere in the new file"
    )

    # The response models must be genuinely DISTINCT classes, not the same imported object —
    # the strongest possible proof no cross-router-module import happened, not just a grep.
    assert par.AuditListResponse is not usage_router_mod.AuditListResponse
    assert par.AuditEventItem is not usage_router_mod.AuditEventItem

    # And its own local pagination parser actually exists (declared, not imported).
    assert any(name.startswith("_parse") and "pagination" in name.lower() for name in vars(par)), (
        "platform_audit_router.py must declare its own local pagination-parsing helper"
    )
