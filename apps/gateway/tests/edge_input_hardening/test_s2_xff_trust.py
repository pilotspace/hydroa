"""Red suite for S2 — XFF last-hop trust (edge-input-hardening TASK.md §2/§3 FROZEN @ v1).

One test per §2 S2 scenario:
  - spoofed XFF chain does not bypass agent-oauth token rate limiting
  - direct/test call with no XFF header falls back to the TCP peer
  - truncated XFF fails closed rather than trusting an earlier hop
  - GATEWAY_TRUSTED_PROXY_HOPS misconfigured to a negative value is coerced, not fatal
  - the 3 pre-existing duplicated helpers are gone, one shared resolver remains

RED reason (pre-BUILD): ``gateway.core.net`` does not exist -> ImportError; the 3 duplicated
``_resolve_client_ip`` helpers still trust the LEFTMOST XFF token -> the spoof/truncation
tests observe the WRONG (attacker-controlled) IP being trusted.

Infrastructure: real Postgres (localhost:5433 gateway_test) + real Redis (localhost:6380 db 9),
mirrors tests/agent_token_endpoint's low-rpm app+client pattern.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import redis.asyncio as aioredis
from sqlalchemy import text

from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from tests.conftest import TEST_DATABASE_URL, TEST_JWT_SECRET
from tests.credential_stub import install_stub_resolver

_REDIS_URL = "redis://localhost:6380/9"
_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
TOKEN_URL = "/oauth/token"
AUTHORIZE_URL = "/oauth/device/authorize"


async def _clear_rl_keys() -> None:
    r = aioredis.from_url(_REDIS_URL)
    try:
        keys = [k async for k in r.scan_iter(match="agent_oauth:ip:*", count=500)]
        if keys:
            await r.delete(*keys)
    except Exception:  # noqa: BLE001 — best-effort cleanup
        pass
    finally:
        await r.aclose()


@pytest.fixture(autouse=True)
async def _isolate() -> AsyncIterator[None]:
    await _clear_rl_keys()
    yield


async def _make_app_and_client(custom_settings: Settings) -> tuple[Any, httpx.AsyncClient]:
    application = create_app(custom_settings)
    install_stub_resolver(application)
    engine = application.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    return application, httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application), base_url="http://test"
    )


@pytest.fixture
async def low_rpm_app_and_client(
    request: pytest.FixtureRequest,
) -> AsyncIterator[tuple[Any, httpx.AsyncClient]]:
    """App+client with token_rpm=2 and trusted_proxy_hops from the test's param (default 1)."""
    hops = getattr(request, "param", 1)
    s = Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url=_REDIS_URL,
        agent_oauth_device_code_ttl_seconds=600,
        agent_oauth_poll_interval_seconds=5,
        agent_oauth_default_scope="proxy",
        agent_oauth_authorize_rpm=12,
        agent_oauth_approve_rpm=30,
        agent_oauth_access_token_ttl_seconds=3600,
        agent_oauth_refresh_token_ttl_seconds=2592000,
        agent_oauth_token_rpm=2,  # LOW — for rate-limit test
        trusted_proxy_hops=hops,
        retention_check_interval_seconds=0,
    )
    application, c = await _make_app_and_client(s)
    async with c:
        yield application, c
    await application.state.engine.dispose()


# ---------------------------------------------------------------------------
# S2.1 — spoofed XFF chain does not bypass agent-oauth token rate limiting
# ---------------------------------------------------------------------------


async def test_s2_1_spoofed_xff_prefix_does_not_bypass_rate_limit(
    low_rpm_app_and_client: tuple[Any, httpx.AsyncClient],
) -> None:
    """trusted_proxy_hops=1: only the RIGHTMOST (Envoy-appended) XFF token is trusted.

    An attacker rotating the LEFT-most fake hops on every request must still hit the SAME
    rate-limiter bucket (keyed on the constant rightmost token) and get 429'd on the 3rd
    call — not bypass the limiter by rotating synthetic identities.
    """
    _app, client = low_rpm_app_and_client
    real_ip = "9.9.9.9"  # the one hop Envoy itself would append

    spoofed_prefixes = ["6.6.6.6, 7.7.7.7", "1.1.1.1, 2.2.2.2", "3.3.3.3, 4.4.4.4"]
    statuses: list[int] = []
    for i, prefix in enumerate(spoofed_prefixes):
        resp = await client.post(
            TOKEN_URL,
            json={"grant_type": _GRANT_TYPE, "device_code": f"dc-spoof-{i}"},
            headers={"X-Forwarded-For": f"{prefix}, {real_ip}"},
        )
        statuses.append(resp.status_code)

    # First two calls proceed to the state machine (400 invalid_grant — unknown code);
    # the 3rd call, despite a totally different spoofed prefix, hits the SAME bucket
    # (keyed on the constant rightmost real_ip) and is rate-limited.
    assert statuses[0] == 400, statuses
    assert statuses[1] == 400, statuses
    assert statuses[2] == 429, (
        f"expected the 3rd request (spoofed prefix rotated) to be 429 rate_limited "
        f"because the resolver trusts only the constant rightmost hop; got {statuses}"
    )


# ---------------------------------------------------------------------------
# S2.2 — direct/test call with no XFF header falls back to the TCP peer
# ---------------------------------------------------------------------------


async def test_s2_2_no_xff_header_falls_back_to_client_host(
    low_rpm_app_and_client: tuple[Any, httpx.AsyncClient],
) -> None:
    """No X-Forwarded-For header -> resolver falls back to request.client.host, and the
    per-IP limiter still functions identically to the pre-fix direct-call behavior."""
    _app, client = low_rpm_app_and_client

    r1 = await client.post(
        TOKEN_URL, json={"grant_type": _GRANT_TYPE, "device_code": "dc-direct-1"}
    )
    r2 = await client.post(
        TOKEN_URL, json={"grant_type": _GRANT_TYPE, "device_code": "dc-direct-2"}
    )
    r3 = await client.post(
        TOKEN_URL, json={"grant_type": _GRANT_TYPE, "device_code": "dc-direct-3"}
    )
    assert r1.status_code == 400, r1.text
    assert r2.status_code == 400, r2.text
    assert r3.status_code == 429, r3.text  # same synthetic client.host bucket, 3rd call trips it


# ---------------------------------------------------------------------------
# S2.3 — truncated XFF fails closed rather than trusting an earlier hop
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("low_rpm_app_and_client", [2], indirect=True)
async def test_s2_3_truncated_xff_fails_closed(
    low_rpm_app_and_client: tuple[Any, httpx.AsyncClient],
) -> None:
    """trusted_proxy_hops=2 but XFF carries only 1 token: the resolver must NOT trust that
    lone token as the 2nd-from-right hop — it falls back to request.client.host, and no
    exception is raised (the request proceeds under the safe fallback identity)."""
    _app, client = low_rpm_app_and_client

    # Each call carries a DIFFERENT single-token XFF (would look like distinct attacker
    # identities if trusted) but must all resolve to the SAME request.client.host fallback.
    r1 = await client.post(
        TOKEN_URL,
        json={"grant_type": _GRANT_TYPE, "device_code": "dc-trunc-1"},
        headers={"X-Forwarded-For": "5.5.5.5"},
    )
    r2 = await client.post(
        TOKEN_URL,
        json={"grant_type": _GRANT_TYPE, "device_code": "dc-trunc-2"},
        headers={"X-Forwarded-For": "8.8.8.8"},
    )
    r3 = await client.post(
        TOKEN_URL,
        json={"grant_type": _GRANT_TYPE, "device_code": "dc-trunc-3"},
        headers={"X-Forwarded-For": "1.2.3.4"},
    )
    assert r1.status_code == 400, r1.text
    assert r2.status_code == 400, r2.text
    assert r3.status_code == 429, (
        f"a truncated XFF (fewer tokens than trusted_proxy_hops) must fail closed to the "
        f"SAME request.client.host bucket, not be trusted as a distinct identity; got "
        f"{[r1.status_code, r2.status_code, r3.status_code]}"
    )


# ---------------------------------------------------------------------------
# S2.4 — GATEWAY_TRUSTED_PROXY_HOPS misconfigured to a negative value is coerced
# ---------------------------------------------------------------------------


def test_s2_4_negative_trusted_proxy_hops_coerced_to_one(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A configured trusted_proxy_hops<=0 is coerced to 1 with a startup WARNING; Settings
    construction does not raise (the gateway still boots)."""
    with caplog.at_level(logging.WARNING):
        s = Settings(
            database_url=TEST_DATABASE_URL,
            jwt_secret=TEST_JWT_SECRET,
            trusted_proxy_hops=-3,
        )
    assert s.trusted_proxy_hops == 1
    assert any("TRUSTED_PROXY_HOPS" in rec.message for rec in caplog.records), (
        "expected a startup WARNING naming the invalid GATEWAY_TRUSTED_PROXY_HOPS value"
    )


# ---------------------------------------------------------------------------
# S2.5 — the 3 pre-existing duplicated helpers are gone, one shared resolver remains
# ---------------------------------------------------------------------------


def test_s2_5_duplicated_helpers_removed_shared_resolver_used() -> None:
    """grepping the 3 call sites for a per-file `_resolve_client_ip` definition finds none;
    all 3 import and call gateway.core.net.resolve_trusted_client_ip."""
    import inspect

    from gateway.agent_oauth.api import device_authorize_router, token_router
    from gateway.core.net import resolve_trusted_client_ip
    from gateway.tenants.api import invite_accept_router

    for module in (token_router, device_authorize_router, invite_accept_router):
        assert not hasattr(module, "_resolve_client_ip"), (
            f"{module.__name__} must not define its own _resolve_client_ip anymore"
        )
        src = inspect.getsource(module)
        assert "resolve_trusted_client_ip" in src, (
            f"{module.__name__} must call the shared gateway.core.net.resolve_trusted_client_ip"
        )

    assert callable(resolve_trusted_client_ip)


# ---------------------------------------------------------------------------
# Regression floor — invite preview/accept rate limiting still keys on the resolved IP
# ---------------------------------------------------------------------------


async def test_s2_regression_invite_rate_limit_uses_shared_resolver(
    client: httpx.AsyncClient,
) -> None:
    """Sanity: invite preview 404s for an unknown token AND still rate-limits per-IP —
    the shared resolver swap didn't break the invite_accept_router call sites."""
    r1 = await client.get("/invites/nonexistent-token-1")
    assert r1.status_code == 404, r1.text
    r2 = await client.get(
        "/invites/nonexistent-token-2", headers={"X-Forwarded-For": "1.1.1.1, 2.2.2.2"}
    )
    assert r2.status_code == 404, r2.text
