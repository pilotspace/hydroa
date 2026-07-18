"""RED suite for device-activate-page (TASK.md §2 SCENARIOS, §4 TESTS).

One test per backend scenario — asserts OBSERVABLE behavior only (status codes, JSON
body BYTES, boot-error). The crown jewel is the parametrized uniform-404 byte-identity
test: preview MUST NOT be a validity oracle over the short user_code space.

RED reason before Build: POST /oauth/device/preview does not exist (405/404), the
Settings knobs (agent_oauth_preview_rpm) / dev default / boot-guard are absent, so the
preview + config tests fail at request/instantiation time.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gateway.core.config import Settings

pytestmark = pytest.mark.asyncio

PREVIEW = "/oauth/device/preview"
APPROVE = "/oauth/device/approve"
DENY = "/oauth/device/deny"
AUTHORIZE = "/oauth/device/authorize"


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# M2, M3 — prefilled pending code previews server-known facts
# ---------------------------------------------------------------------------


async def test_prefilled_code_previews_pending(
    app: Any, client: Any, db_session: AsyncSession
) -> None:
    from tests.device_activate_preview.conftest import (
        mint_token,
        seed_pending,
        signup_and_login,
    )

    _, tenant_id, _ = await signup_and_login(client)
    user_token, _ = await mint_token(app, db_session, tenant_id=tenant_id)
    user_code = "BCDF-GHJK"
    await seed_pending(app, user_code=user_code)

    resp = await client.post(PREVIEW, json={"user_code": user_code}, headers=auth(user_token))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "pending"
    assert body["scope"] == "proxy"
    assert isinstance(body["expires_in"], int) and body["expires_in"] > 0
    assert isinstance(body["interval"], int) and body["interval"] > 0
    assert "default_budget_usd" in body
    # The code appears only in the request body — never in the request URL.
    assert "user_code" not in str(resp.request.url)


async def test_preview_default_budget_is_system_cap_2dp(
    app: Any, client: Any, db_session: AsyncSession, settings: Settings
) -> None:
    from tests.device_activate_preview.conftest import (
        mint_token,
        seed_pending,
        signup_and_login,
    )

    _, tenant_id, _ = await signup_and_login(client)
    user_token, _ = await mint_token(app, db_session, tenant_id=tenant_id)
    await seed_pending(app, user_code="BCDF-LMNP")

    resp = await client.post(PREVIEW, json={"user_code": "BCDF-LMNP"}, headers=auth(user_token))

    assert resp.status_code == 200, resp.text
    expected = f"{settings.agent_oauth_default_budget_usd:.2f}"
    assert resp.json()["default_budget_usd"] == expected


# ---------------------------------------------------------------------------
# M7, R-B — every non-previewable state returns ONE byte-identical 404
# ---------------------------------------------------------------------------


async def test_preview_uniform_404_byte_identical(
    app: Any, client: Any, db_session: AsyncSession
) -> None:
    """unknown | expired | approved | denied | consumed → byte-identical 404.

    A single assertion over resp.content bytes across ALL FIVE states is the
    enumeration-oracle proof: preview leaks no distinction for a non-pending code.
    """
    from tests.device_activate_preview.conftest import (
        mint_token,
        seed_approved,
        seed_consumed,
        seed_denied,
        seed_expired,
        signup_and_login,
    )

    _, tenant_id, _ = await signup_and_login(client)
    user_token, user_id = await mint_token(app, db_session, tenant_id=tenant_id)

    # Seed one grant per non-previewable state (unknown has no row).
    await seed_expired(app, user_code="BCDF-QRST")
    await seed_approved(app, user_code="BCDF-VWXZ", tenant_id=tenant_id, user_id=user_id)
    await seed_denied(app, user_code="BCDF-MNPQ")
    await seed_consumed(app, user_code="BCDF-RSVW", tenant_id=tenant_id, user_id=user_id)

    codes = {
        "unknown": "ZZZZ-ZZZZ",
        "expired": "BCDF-QRST",
        "approved": "BCDF-VWXZ",
        "denied": "BCDF-MNPQ",
        "consumed": "BCDF-RSVW",
    }

    bodies: dict[str, bytes] = {}
    for state, code in codes.items():
        resp = await client.post(PREVIEW, json={"user_code": code}, headers=auth(user_token))
        assert resp.status_code == 404, f"{state}: {resp.status_code} {resp.text}"
        assert resp.json() == {"error": "authorization_not_previewable"}, state
        bodies[state] = resp.content

    # BYTE-IDENTICAL across every state — no oracle.
    distinct = set(bodies.values())
    assert len(distinct) == 1, f"non-previewable states leaked distinct bodies: {bodies}"


# ---------------------------------------------------------------------------
# R:invalid_request — malformed / oversized body
# ---------------------------------------------------------------------------


async def test_preview_malformed_body_422(app: Any, client: Any, db_session: AsyncSession) -> None:
    from tests.device_activate_preview.conftest import mint_token, signup_and_login

    _, tenant_id, _ = await signup_and_login(client)
    user_token, _ = await mint_token(app, db_session, tenant_id=tenant_id)

    # Missing user_code
    r1 = await client.post(PREVIEW, json={}, headers=auth(user_token))
    assert r1.status_code == 422, r1.text
    assert r1.json() == {"error": "invalid_request"}

    # Non-string user_code
    r2 = await client.post(PREVIEW, json={"user_code": 12345}, headers=auth(user_token))
    assert r2.status_code == 422, r2.text
    assert r2.json() == {"error": "invalid_request"}

    # Non-JSON body
    r3 = await client.post(
        PREVIEW,
        content=b"not-json",
        headers={**auth(user_token), "Content-Type": "application/json"},
    )
    assert r3.status_code == 422, r3.text
    assert r3.json() == {"error": "invalid_request"}


async def test_preview_oversized_body_422(app: Any, client: Any, db_session: AsyncSession) -> None:
    from tests.device_activate_preview.conftest import mint_token, signup_and_login

    _, tenant_id, _ = await signup_and_login(client)
    user_token, _ = await mint_token(app, db_session, tenant_id=tenant_id)

    # Declared Content-Length > 4096
    r1 = await client.post(
        PREVIEW,
        content=b'{"user_code":"BCDF-GHJK"}',
        headers={**auth(user_token), "Content-Type": "application/json", "Content-Length": "99999"},
    )
    assert r1.status_code == 422, r1.text
    assert r1.json() == {"error": "invalid_request"}

    # Raw oversized body
    huge = b'{"user_code":"' + b"B" * 8192 + b'"}'
    r2 = await client.post(
        PREVIEW, content=huge, headers={**auth(user_token), "Content-Type": "application/json"}
    )
    assert r2.status_code == 422, r2.text
    assert r2.json() == {"error": "invalid_request"}


# ---------------------------------------------------------------------------
# R:unauthorized — missing / invalid session JWT
# ---------------------------------------------------------------------------


async def test_preview_requires_auth_401(app: Any, client: Any) -> None:
    from tests.device_activate_preview.conftest import seed_pending

    await seed_pending(app, user_code="BCDF-GHJK")

    r1 = await client.post(PREVIEW, json={"user_code": "BCDF-GHJK"})
    assert r1.status_code == 401, r1.text
    assert "scope" not in r1.json()  # no facts leaked

    r2 = await client.post(
        PREVIEW, json={"user_code": "BCDF-GHJK"}, headers=auth("not-a-valid-jwt")
    )
    assert r2.status_code == 401, r2.text
    assert "scope" not in r2.json()


# ---------------------------------------------------------------------------
# M5, R-E — the code lives in a POST body, never a URL
# ---------------------------------------------------------------------------


async def test_preview_is_post_only_code_never_in_url(
    app: Any, client: Any, db_session: AsyncSession
) -> None:
    """A GET with ?user_code= is not the interface — the code never enters a URL/log."""
    from tests.device_activate_preview.conftest import (
        mint_token,
        seed_pending,
        signup_and_login,
    )

    _, tenant_id, _ = await signup_and_login(client)
    user_token, _ = await mint_token(app, db_session, tenant_id=tenant_id)
    await seed_pending(app, user_code="BCDF-GHJK")

    getr = await client.get(f"{PREVIEW}?user_code=BCDF-GHJK", headers=auth(user_token))
    assert getr.status_code in (404, 405), getr.text  # GET is not routed


# ---------------------------------------------------------------------------
# M6, R:rate_limited, A5 — per-user preview limit independent of approve budget
# ---------------------------------------------------------------------------


async def test_preview_per_user_rate_limit_429(
    low_preview_rpm_app_and_client: tuple[Any, Any],
) -> None:
    from tests.device_activate_preview.conftest import (
        mint_token,
        seed_pending,
        signup_and_login,
    )

    low_app, low_client = low_preview_rpm_app_and_client
    _, tenant_id, _ = await signup_and_login(low_client)
    async with low_app.state.sessionmaker() as session:
        user_token, _ = await mint_token(low_app, session, tenant_id=tenant_id)

    await seed_pending(low_app, user_code="BCDF-GHJK")

    # preview_rpm=2 → first two previews OK, third rate-limited.
    r1 = await low_client.post(PREVIEW, json={"user_code": "BCDF-GHJK"}, headers=auth(user_token))
    assert r1.status_code == 200, r1.text
    r2 = await low_client.post(PREVIEW, json={"user_code": "BCDF-GHJK"}, headers=auth(user_token))
    assert r2.status_code == 200, r2.text
    r3 = await low_client.post(PREVIEW, json={"user_code": "BCDF-GHJK"}, headers=auth(user_token))
    assert r3.status_code == 429, r3.text
    assert r3.json() == {"error": "rate_limited"}
    assert "Retry-After" in r3.headers

    # The approve budget (key approve:{user_id}) is UNAFFECTED — an approve still works.
    r_approve = await low_client.post(
        APPROVE, json={"user_code": "BCDF-GHJK"}, headers=auth(user_token)
    )
    assert r_approve.status_code == 200, r_approve.text
    assert r_approve.json() == {"status": "approved"}


# ---------------------------------------------------------------------------
# M8 — verification_uri populated by default
# ---------------------------------------------------------------------------


async def test_verification_uri_populated_by_default(app: Any, client: Any) -> None:
    resp = await client.post(AUTHORIZE, json={})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["verification_uri"] == "http://localhost:3000/activate"
    assert body["verification_uri_complete"] == (
        f"http://localhost:3000/activate?user_code={body['user_code']}"
    )
    # Wire shape otherwise byte-identical: the standard RFC 8628 fields still present.
    assert isinstance(body["expires_in"], int)
    assert isinstance(body["interval"], int)
    assert "device_code" in body and "user_code" in body


# ---------------------------------------------------------------------------
# M4, A7 — the FROZEN approve/deny wire bytes are UNCHANGED by the additive route
# ---------------------------------------------------------------------------


async def test_approve_wire_byte_identical(app: Any, client: Any, db_session: AsyncSession) -> None:
    from tests.device_activate_preview.conftest import (
        mint_token,
        seed_pending,
        signup_and_login,
    )

    _, tenant_id, _ = await signup_and_login(client)
    user_token, _ = await mint_token(app, db_session, tenant_id=tenant_id)
    await seed_pending(app, user_code="BCDF-GHJK")

    resp = await client.post(APPROVE, json={"user_code": "BCDF-GHJK"}, headers=auth(user_token))
    assert resp.status_code == 200, resp.text
    assert resp.content == b'{"status":"approved"}'


async def test_deny_wire_byte_identical(app: Any, client: Any, db_session: AsyncSession) -> None:
    from tests.device_activate_preview.conftest import (
        mint_token,
        seed_pending,
        signup_and_login,
    )

    _, tenant_id, _ = await signup_and_login(client)
    user_token, _ = await mint_token(app, db_session, tenant_id=tenant_id)
    await seed_pending(app, user_code="BCDF-QRST")

    resp = await client.post(DENY, json={"user_code": "BCDF-QRST"}, headers=auth(user_token))
    assert resp.status_code == 200, resp.text
    assert resp.content == b'{"status":"denied"}'
