"""RED->GREEN suite for POST /admin/auth/access-requests (signup-refusal-router TASK.md
§3/§4 — FROZEN @ v1, SECURITY task).

Gateway-scoped half of §4's split test-author pass: the dashboard/BFF pass (Vitest) could
not itself prove the TRUE anti-enumeration invariant (R3 — "no branch in the request-
access code path may read resolve_verified_tenant, any user/tenant table, or any
domain-claim state") because that property only exists where the actual branching logic
COULD exist — the gateway's own handler. Written here at BUILD per TASK.md §4's own
"Open item for the orchestrator" flag (no gateway-side red suite was authored upstream).

One test per §2 scenario this file's tooling CAN prove (HTTP status/body, DB state) — every
assertion is behavioral, never an implementation internal.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from redis.exceptions import RedisError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.access_requests.infrastructure.orm import AccessRequestRow
from gateway.domain_capture.domain.entities import ClaimStatus
from gateway.domain_capture.infrastructure.orm import TenantDomainClaimRow

PATH = "/admin/auth/access-requests"


async def _signup_owner(client: httpx.AsyncClient, *, email: str | None = None) -> dict[str, Any]:
    """Signup a fresh tenant+owner; return {tenant_id, user_id (via /me), email}."""
    tenant_name = f"AccReqCo-{uuid.uuid4().hex[:8]}"
    owner_email = email or f"owner-{uuid.uuid4().hex[:8]}@accreqtest.io"
    password = "access-request-horse-01"
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": owner_email, "password": password},
    )
    assert signup.status_code == 201, signup.text
    login = await client.post(
        "/admin/auth/login", json={"email": owner_email, "password": password}
    )
    assert login.status_code == 200, login.text
    me = await client.get(
        "/admin/auth/me", headers={"Authorization": f"Bearer {login.json()['access_token']}"}
    )
    assert me.status_code == 200, me.text
    return {
        "tenant_id": uuid.UUID(signup.json()["tenant_id"]),
        "user_id": uuid.UUID(me.json()["user_id"]),
        "email": owner_email,
    }


async def _count_access_requests(db_session: AsyncSession) -> int:
    result = await db_session.execute(select(text("count(*)")).select_from(AccessRequestRow))
    return int(result.scalar_one())


# ---------------------------------------------------------------------------
# M4, M5, M6 — capture + uniform success
# ---------------------------------------------------------------------------


async def test_access_request_success_returns_uniform_202_and_captures_row(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    resp = await client.post(PATH, json={"email": "Sam@Acme.com"})

    assert resp.status_code == 202, resp.text
    assert resp.json() == {"ok": True}

    rows = (
        await db_session.execute(select(AccessRequestRow).where(AccessRequestRow.email == "sam@acme.com"))
    ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.domain == "acme.com"
    assert row.created_at is not None


# ---------------------------------------------------------------------------
# R1 — malformed email rejected before any capture
# ---------------------------------------------------------------------------


async def test_access_request_malformed_email_422_no_capture(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    before = await _count_access_requests(db_session)

    resp = await client.post(PATH, json={"email": "not-an-email"})

    assert resp.status_code == 422, resp.text
    after = await _count_access_requests(db_session)
    assert after == before


# ---------------------------------------------------------------------------
# R3 — the binding anti-enumeration SAFETY RULE, probed two ways per §2
# ---------------------------------------------------------------------------


async def test_access_request_uniform_for_existing_account_vs_unknown_email(
    client: httpx.AsyncClient,
) -> None:
    """A visitor whose email already has a full account gets the IDENTICAL response
    (status + body) as a never-seen email — no distinguishing field anywhere."""
    owner = await _signup_owner(client)

    known_resp = await client.post(PATH, json={"email": owner["email"]})
    unknown_resp = await client.post(
        PATH, json={"email": f"never-seen-{uuid.uuid4().hex[:8]}@nowhere.example"}
    )

    assert known_resp.status_code == unknown_resp.status_code == 202
    assert known_resp.json() == unknown_resp.json() == {"ok": True}


async def test_access_request_uniform_for_verified_customer_domain_vs_unknown_domain(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """A visitor whose domain already holds a VERIFIED tenant claim gets the IDENTICAL
    response as an entirely unseen domain — proves no branch reads the domain-claim
    table (R3's binding SAFETY RULE), via a real verified TenantDomainClaimRow, not a
    hypothetical one."""
    owner = await _signup_owner(client)
    claim_domain = f"verified-{uuid.uuid4().hex[:8]}.example"
    db_session.add(
        TenantDomainClaimRow(
            id=uuid.uuid4(),
            tenant_id=owner["tenant_id"],
            domain=claim_domain,
            verification_token="test-token",
            status=ClaimStatus.VERIFIED.value,
            verified_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(days=7),
            created_by_user_id=owner["user_id"],
        )
    )
    await db_session.commit()

    claimed_resp = await client.post(PATH, json={"email": f"anyone@{claim_domain}"})
    unknown_resp = await client.post(
        PATH, json={"email": f"anyone@unclaimed-{uuid.uuid4().hex[:8]}.example"}
    )

    assert claimed_resp.status_code == unknown_resp.status_code == 202
    assert claimed_resp.json() == unknown_resp.json() == {"ok": True}


# ---------------------------------------------------------------------------
# M7, R2 — per-IP rate limiting, fail-open on Redis outage
# ---------------------------------------------------------------------------


async def test_access_request_rate_limited_429_no_capture_for_blocked_call(
    low_rpm_app_and_client: tuple[Any, httpx.AsyncClient],
) -> None:
    _application, low_rpm_client = low_rpm_app_and_client

    ok1 = await low_rpm_client.post(PATH, json={"email": "first@ratelimit.example"})
    ok2 = await low_rpm_client.post(PATH, json={"email": "second@ratelimit.example"})
    assert ok1.status_code == 202, ok1.text
    assert ok2.status_code == 202, ok2.text

    async with _application.state.sessionmaker() as session:
        before = await _count_access_requests(session)

    blocked = await low_rpm_client.post(PATH, json={"email": "third@ratelimit.example"})
    assert blocked.status_code == 429, blocked.text
    assert blocked.json().get("code") == "ERR_RATE_LIMITED"

    async with _application.state.sessionmaker() as session:
        after = await _count_access_requests(session)
    assert after == before


async def test_access_request_redis_outage_fails_open(
    client: httpx.AsyncClient, db_session: AsyncSession, app: object
) -> None:
    class _BrokenRedis:
        async def incr(self, *_args: object, **_kwargs: object) -> int:
            raise RedisError("simulated outage")

        async def expire(self, *_args: object, **_kwargs: object) -> None:
            raise RedisError("simulated outage")

    app.state.access_request_limiter._redis = _BrokenRedis()  # type: ignore[attr-defined]

    resp = await client.post(PATH, json={"email": f"failopen-{uuid.uuid4().hex[:8]}@example.com"})

    assert resp.status_code == 202, resp.text
    assert resp.json() == {"ok": True}
