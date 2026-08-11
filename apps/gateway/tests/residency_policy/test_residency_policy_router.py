"""HTTP+DB suite for /admin/residency-policy CRUD (TASK.md §2 "setting/clearing a
residency pin", "invalid region value is rejected", "non-owner cannot change") plus
full end-to-end smoke tests proving the REAL main.py wiring (governance Tier 1 +
router Tier 2) works against a real Postgres — not just the unit-tested pieces in
test_residency_shared.py / test_residency_router.py / test_residency_use_case_flows.py.

Pattern: tests/retention_zdr/test_retention_zdr.py (signup->login->create-key,
real schema via the `app`/`client`/`db_session` fixtures, StubChatUpstream via
app.state.completion_upstream).
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests._polling import poll_until

from tests.residency_policy.conftest import (
    COMPLETIONS,
    EMBEDDINGS,
    RESIDENCY_POLICY,
    StubChatUpstream,
    assert_problem,
    bearer,
    insert_model,
    set_residency_pin,
    set_zdr,
    wire_model_groups,
)

pytestmark = pytest.mark.asyncio

ALIAS = "chat-default"
EU_CANDIDATE = "anthropic/claude-opus-4-eu"
US_CANDIDATE = "anthropic/claude-opus-4-us"


async def _residency_audit_count(db_session: AsyncSession, tenant_id: str) -> int:
    """Count residency_policy.update audit rows for a tenant.

    Extracted so the fire-and-forget audit write can be POLLED for instead of slept
    on — the two call sites previously guessed 0.1s and lost the guess under load.
    """
    result = await db_session.execute(
        text(
            "SELECT COUNT(*) FROM audit_events WHERE tenant_id = :tid"
            " AND action = 'residency_policy.update'"
        ),
        {"tid": tenant_id},
    )
    return result.scalar() or 0


def _issue_role_token(app: object, *, tenant_id: str, role_str: str, email: str) -> str:
    from gateway.tenants.domain.entities import Role

    token, _ = app.state.token_service.issue(  # type: ignore[attr-defined]
        user_id=uuid.uuid4(),
        tenant_id=uuid.UUID(tenant_id),
        role=Role(role_str),
        email=email,
    )
    return token


# ===========================================================================
# GET default policy
# ===========================================================================


async def test_get_default_policy_is_unpinned(
    client: httpx.AsyncClient, owner: dict[str, str]
) -> None:
    resp = await client.get(RESIDENCY_POLICY, headers=bearer(owner["jwt"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["region"] is None
    assert body["updated_at"] is None


# ===========================================================================
# Setting a residency pin (M1, M3)
# ===========================================================================


async def test_put_sets_pin_and_records_audit(
    client: httpx.AsyncClient, owner: dict[str, str], db_session: AsyncSession
) -> None:
    resp = await client.put(RESIDENCY_POLICY, json={"region": "eu"}, headers=bearer(owner["jwt"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["region"] == "eu"
    assert body["updated_at"] is not None

    row = (
        await db_session.execute(
            text(
                "SELECT residency_region, residency_region_updated_at FROM tenants WHERE id = :tid"
            ),
            {"tid": owner["tenant_id"]},
        )
    ).first()
    assert row is not None
    assert row[0] == "eu"
    assert row[1] is not None

    # POSITIVE WAIT: the audit write is fire-and-forget, and the assertion below is
    # `>= 1`, so polling for arrival is exactly equivalent and cannot flake.
    await poll_until(
        lambda: _residency_audit_count(db_session, owner["tenant_id"]), lambda n: n >= 1
    )
    audit = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM audit_events WHERE tenant_id = :tid"
                " AND action = 'residency_policy.update'"
            ),
            {"tid": owner["tenant_id"]},
        )
    ).scalar()
    assert (audit or 0) >= 1, "a fire-and-forget audit event must be recorded"


# ===========================================================================
# Clearing a residency pin (M1, M3)
# ===========================================================================


async def test_put_clears_pin_then_unrestricted(
    client: httpx.AsyncClient, owner: dict[str, str], db_session: AsyncSession
) -> None:
    await set_residency_pin(db_session, owner["tenant_id"], "eu")

    resp = await client.put(RESIDENCY_POLICY, json={"region": None}, headers=bearer(owner["jwt"]))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["region"] is None
    assert body["updated_at"] is not None

    row = (
        await db_session.execute(
            text("SELECT residency_region FROM tenants WHERE id = :tid"),
            {"tid": owner["tenant_id"]},
        )
    ).first()
    assert row is not None
    assert row[0] is None

    get_resp = await client.get(RESIDENCY_POLICY, headers=bearer(owner["jwt"]))
    assert get_resp.json()["region"] is None


# ===========================================================================
# Invalid region value is rejected (R2)
# ===========================================================================


async def test_put_invalid_region_rejected(
    client: httpx.AsyncClient, owner: dict[str, str], db_session: AsyncSession
) -> None:
    resp = await client.put(RESIDENCY_POLICY, json={"region": "apac"}, headers=bearer(owner["jwt"]))
    assert_problem(resp, 422, "ERR_RESIDENCY_REGION_INVALID")

    row = (
        await db_session.execute(
            text("SELECT residency_region FROM tenants WHERE id = :tid"),
            {"tid": owner["tenant_id"]},
        )
    ).first()
    assert row is not None
    assert row[0] is None, "rejected PUT must leave the row unchanged"

    import asyncio

    # NEGATIVE WAIT: proves a rejected PUT records NO audit event. Only elapsed time
    # can demonstrate absence — a poll against a zero count returns immediately and
    # would assert nothing at all.
    await asyncio.sleep(0.1)
    audit = (
        await db_session.execute(
            text(
                "SELECT COUNT(*) FROM audit_events WHERE tenant_id = :tid"
                " AND action = 'residency_policy.update'"
            ),
            {"tid": owner["tenant_id"]},
        )
    ).scalar()
    assert (audit or 0) == 0, "a rejected PUT must never record an audit event"


@pytest.mark.parametrize("bad_region", ["global", "US", "eu-west-1", ""])
async def test_put_other_invalid_region_values_rejected(
    client: httpx.AsyncClient, owner: dict[str, str], bad_region: str
) -> None:
    """M6: "global" is explicitly never a valid PIN value either (pinning TO global
    would be a self-contradicting no-op — see residency_policy_router.py docstring)."""
    resp = await client.put(
        RESIDENCY_POLICY, json={"region": bad_region}, headers=bearer(owner["jwt"])
    )
    assert_problem(resp, 422, "ERR_RESIDENCY_REGION_INVALID")


# ===========================================================================
# Cross-tenant / orphaned-identity access is 404, never a leak
# ===========================================================================


async def test_cross_tenant_residency_policy_404(client: httpx.AsyncClient, app: object) -> None:
    """No path/query param exists to name another tenant on this endpoint (mirrors
    retention_policy's identical R5 precedent) — the only structurally possible way to
    probe "another tenant" is an identity whose own tenant_id does not resolve to a
    real tenants row (e.g. orphaned/deleted)."""
    unknown_tenant_id = uuid.uuid4()
    token = _issue_role_token(
        app, tenant_id=str(unknown_tenant_id), role_str="owner", email="ghost@nowhere.test"
    )
    get_resp = await client.get(RESIDENCY_POLICY, headers=bearer(token))
    assert_problem(get_resp, 404, "ERR_TENANT_NOT_FOUND")

    put_resp = await client.put(RESIDENCY_POLICY, json={"region": "eu"}, headers=bearer(token))
    assert_problem(put_resp, 404, "ERR_TENANT_NOT_FOUND")


# ===========================================================================
# Non-owner cannot change the residency policy (R3)
# ===========================================================================


@pytest.mark.parametrize("role", ["admin", "operator", "billing_admin", "viewer", "member"])
async def test_put_non_owner_forbidden(
    client: httpx.AsyncClient,
    app: object,
    owner: dict[str, str],
    db_session: AsyncSession,
    role: str,
) -> None:
    domain_role = role.replace("_", "-")
    token = _issue_role_token(
        app, tenant_id=owner["tenant_id"], role_str=role, email=f"{domain_role}@nonowner.test"
    )
    resp = await client.put(RESIDENCY_POLICY, json={"region": "eu"}, headers=bearer(token))
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")

    row = (
        await db_session.execute(
            text("SELECT residency_region FROM tenants WHERE id = :tid"),
            {"tid": owner["tenant_id"]},
        )
    ).first()
    assert row is not None
    assert row[0] is None, "a forbidden PUT must leave the row unchanged"


# ===========================================================================
# End-to-end smoke: real HTTP + real Postgres proves the ACTUAL main.py wiring
# (Tier 1 governance + Tier 2 router pre-loop filter), not just the unit-level
# fakes in the sibling suites.
# ===========================================================================


async def test_e2e_eu_pinned_alias_chat_completion_served_only_by_eu(
    client: httpx.AsyncClient,
    app: object,
    owner: dict[str, str],
    db_session: AsyncSession,
) -> None:
    await insert_model(db_session, US_CANDIDATE, region="us")
    await insert_model(db_session, EU_CANDIDATE, region="eu")
    wire_model_groups(app, {ALIAS: [US_CANDIDATE, EU_CANDIDATE]})
    await set_residency_pin(db_session, owner["tenant_id"], "eu")

    upstream = StubChatUpstream()
    app.state.completion_upstream = upstream  # type: ignore[attr-defined]
    try:
        resp = await client.post(
            COMPLETIONS,
            json={"model": ALIAS, "messages": [{"role": "user", "content": "hi"}]},
            headers=bearer(owner["key"]),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["model"] == EU_CANDIDATE
        assert upstream.calls == [
            {"model": EU_CANDIDATE, "messages": [{"role": "user", "content": "hi"}]}
        ], "the us candidate must never be dialed over real HTTP + real Postgres"
    finally:
        app.state.completion_upstream = None  # type: ignore[attr-defined]


async def test_e2e_eu_pinned_alias_zero_eligible_refused_403(
    client: httpx.AsyncClient,
    app: object,
    owner: dict[str, str],
    db_session: AsyncSession,
) -> None:
    await insert_model(db_session, US_CANDIDATE, region="us")
    wire_model_groups(app, {ALIAS: [US_CANDIDATE]})
    await set_residency_pin(db_session, owner["tenant_id"], "eu")

    upstream = StubChatUpstream()
    app.state.completion_upstream = upstream  # type: ignore[attr-defined]
    try:
        resp = await client.post(
            COMPLETIONS,
            json={"model": ALIAS, "messages": [{"role": "user", "content": "hi"}]},
            headers=bearer(owner["key"]),
        )
        assert_problem(resp, 403, "ERR_RESIDENCY_NO_ELIGIBLE_REGION")
        assert upstream.calls == []
    finally:
        app.state.completion_upstream = None  # type: ignore[attr-defined]


async def test_e2e_embeddings_refused_before_routing_for_pinned_tenant(
    client: httpx.AsyncClient,
    app: object,
    owner: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Scenario: embeddings request for a residency-pinned tenant is refused before
    the routing-only catalog query (M4 non-chat, R1) — proves the shared
    NonChatGovernance.authorize() Tier 1 wiring end-to-end via the real
    embeddings_deps.py construction site."""
    us_embed_model = "openai/text-embedding-us"
    await insert_model(db_session, us_embed_model, region="us")
    await set_residency_pin(db_session, owner["tenant_id"], "eu")

    resp = await client.post(
        EMBEDDINGS,
        json={"model": us_embed_model, "input": "hello world"},
        headers=bearer(owner["key"]),
    )
    assert_problem(resp, 403, "ERR_RESIDENCY_NO_ELIGIBLE_REGION")


# ===========================================================================
# Residency composes independently with ZDR (M9)
# ===========================================================================


async def test_e2e_residency_composes_independently_with_zdr(
    client: httpx.AsyncClient,
    app: object,
    owner: dict[str, str],
    db_session: AsyncSession,
) -> None:
    await insert_model(db_session, US_CANDIDATE, region="us")
    await insert_model(db_session, EU_CANDIDATE, region="eu")
    wire_model_groups(app, {ALIAS: [US_CANDIDATE, EU_CANDIDATE]})
    await set_residency_pin(db_session, owner["tenant_id"], "eu")
    await set_zdr(db_session, owner["tenant_id"], True)

    upstream = StubChatUpstream()
    app.state.completion_upstream = upstream  # type: ignore[attr-defined]
    try:
        resp = await client.post(
            COMPLETIONS,
            json={"model": ALIAS, "messages": [{"role": "user", "content": "hi"}]},
            headers=bearer(owner["key"]),
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["model"] == EU_CANDIDATE, (
            "residency still narrows to eu exactly as if ZDR were off"
        )
        assert upstream.calls[0]["model"] == EU_CANDIDATE
    finally:
        app.state.completion_upstream = None  # type: ignore[attr-defined]
