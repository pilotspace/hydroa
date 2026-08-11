"""Red suite — e2e Envoy edge layer tests (contract §3, edge-envoy TASK.md).

Layer 2: @pytest.mark.e2e — requires the infra/docker-compose.e2e.yml stack to be running.
Excluded from the default pytest run via pyproject.toml addopts: -m "not e2e".
Run by scripts/e2e_edge.sh with: uv run pytest tests/ -m e2e -q --no-cov

These tests are RED-BY-ABSENCE in CI (not collected; they don't run by default).
When the stack IS up, they fail because the Envoy config, Dockerfile, and compose file
do not exist yet.

All tests hit http://localhost:8080 (Envoy listener) — NOT the gateway directly.
Base URL is configurable via E2E_BASE_URL env var for flexibility.

IMPORTANT: Do NOT mark individual tests with skip — they must FAIL (not skip) when the
stack is not present. The e2e script is responsible for ensuring the stack is up before
running these tests. Skipping would silently pass the gate.
"""

import asyncio
import os
import time
import uuid
from typing import Any

import httpx
import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

E2E_BASE_URL = os.environ.get("E2E_BASE_URL", "http://localhost:8080")
# Gateway base URL for out-of-band setup operations (signup/login/key creation)
# In e2e these go through Envoy too (port 8080)
ENVOY_BASE = E2E_BASE_URL

SIGNUP_PATH = "/admin/auth/signup"
LOGIN_PATH = "/admin/auth/login"
ADMIN_KEYS_PATH = "/admin/keys"
INTERNAL_AUTHZ_PATH = "/internal/authz"
V1_PATH = "/v1/chat/completions"
HEALTH_PATH = "/health"

# The JWT secret used by the gateway in the compose stack (must match GATEWAY_JWT_SECRET in compose)
E2E_JWT_SECRET = os.environ.get("GATEWAY_JWT_SECRET", "e2e-test-secret-change-me")

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def assert_not_envoy_authn_rejection(resp: httpx.Response) -> None:
    """Assert the response is NOT an Envoy-level 401 (jwt_authn rejection).

    A 401 from Envoy has no body or a plain-text body, not problem+json.
    A 401 from the gateway has application/problem+json content-type.
    """
    if resp.status_code == 401:
        ct = resp.headers.get("content-type", "")
        assert "application/problem+json" in ct, (
            f"Got 401 from Envoy (not gateway): content-type={ct!r}, body={resp.text!r}. "
            "This means jwt_authn rejected the request — check the JWT or the Envoy JWKS config."
        )


async def _e2e_signup_and_login(
    client: httpx.AsyncClient, *, tenant_name: str, email: str
) -> str:
    """Sign up a fresh tenant through Envoy and return a valid JWT."""
    password = "e2e-correct-horse-battery"
    sr = await client.post(
        f"{ENVOY_BASE}{SIGNUP_PATH}",
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert sr.status_code == 201, (
        f"e2e signup failed (status {sr.status_code}): {sr.text}\n"
        "Check: is the compose stack up? Is Envoy routing /admin/* to the gateway?"
    )
    lr = await client.post(
        f"{ENVOY_BASE}{LOGIN_PATH}",
        json={"email": email, "password": password},
    )
    assert lr.status_code == 200, f"e2e login failed: {lr.text}"
    return lr.json()["access_token"]


async def _e2e_create_key(
    client: httpx.AsyncClient, jwt_token: str, name: str
) -> dict[str, Any]:
    resp = await client.post(
        f"{ENVOY_BASE}{ADMIN_KEYS_PATH}",
        json={"name": name},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code == 201, (
        f"e2e key creation failed (status {resp.status_code}): {resp.text}"
    )
    return resp.json()


# ---------------------------------------------------------------------------
# Scenario S5 — /internal/* blocked at Envoy (direct_response 403)
# ---------------------------------------------------------------------------


async def test_e2e_internal_authz_blocked() -> None:
    """S5: GET /internal/authz through Envoy must return 403 direct response.

    Envoy's route config has a direct_response rule for /internal/* prefix.
    The gateway must NOT be contacted.
    RED reason: infra/envoy/envoy.yaml does not exist yet.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{ENVOY_BASE}{INTERNAL_AUTHZ_PATH}", timeout=5.0)
    assert resp.status_code == 403, (
        f"Expected 403 direct response from Envoy for /internal/*, got {resp.status_code}: {resp.text}"
    )


async def test_e2e_internal_other_path_blocked() -> None:
    """S5 extension: /internal/anything is blocked."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{ENVOY_BASE}/internal/anything",
            headers={"X-Api-Key": "sk-" + "a" * 32 + ".secret"},
            timeout=5.0,
        )
    assert resp.status_code == 403, (
        f"Expected 403 for /internal/anything, got {resp.status_code}"
    )


# ---------------------------------------------------------------------------
# Scenario S6 — valid JWT passes /admin/* through Envoy jwt_authn
# ---------------------------------------------------------------------------


async def test_e2e_valid_jwt_passes_admin_route() -> None:
    """S6: Valid gateway-issued JWT must pass Envoy jwt_authn and reach the gateway.

    We assert the response is NOT a bare Envoy 401 (which has no problem+json body).
    A 200 from the gateway (empty key list) is the expected happy path.
    RED reason: Envoy config / compose stack do not exist.
    """
    async with httpx.AsyncClient() as client:
        jwt_token = await _e2e_signup_and_login(
            client,
            tenant_name=f"E2EAdminCo-{uuid.uuid4().hex[:6]}",
            email=f"admin-{uuid.uuid4().hex[:6]}@e2e.io",
        )
        resp = await client.get(
            f"{ENVOY_BASE}{ADMIN_KEYS_PATH}",
            headers={"Authorization": f"Bearer {jwt_token}"},
            timeout=5.0,
        )

    # Must NOT be Envoy's jwt_authn rejection (bare 401 without problem+json)
    assert_not_envoy_authn_rejection(resp)
    # Gateway returns 200 (empty list) for a new tenant
    assert resp.status_code == 200, (
        f"Expected 200 from gateway via Envoy /admin/keys, got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# Scenario S7 — expired/invalid JWT rejected at Envoy jwt_authn
# ---------------------------------------------------------------------------


async def test_e2e_expired_jwt_rejected_by_envoy() -> None:
    """S7: An expired JWT must be rejected by Envoy jwt_authn before reaching the gateway.

    We mint an already-expired JWT using the known e2e secret.
    Envoy returns a bare 401 (no problem+json body) — this distinguishes it from the
    gateway's own 401 responses.
    RED reason: Envoy config / JWKS not set up.
    """
    import time

    import jwt as pyjwt

    now = int(time.time())
    expired_claims = {
        "sub": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "email": "expired@e2e.io",
        "role": "owner",
        "iss": "ai-proxy",
        "iat": now - 7200,
        "exp": now - 3600,  # expired 1 hour ago
    }
    expired_token = pyjwt.encode(expired_claims, E2E_JWT_SECRET, algorithm="HS256")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{ENVOY_BASE}{ADMIN_KEYS_PATH}",
            headers={"Authorization": f"Bearer {expired_token}"},
            timeout=5.0,
        )

    assert resp.status_code == 401, (
        f"Expected 401 from Envoy jwt_authn for expired JWT, got {resp.status_code}: {resp.text}"
    )
    # Envoy's jwt_authn response has no problem+json body — this is the distinguishing feature
    ct = resp.headers.get("content-type", "")
    assert "application/problem+json" not in ct, (
        f"Got problem+json 401 — this came from the gateway, not Envoy jwt_authn. "
        f"The Envoy jwt_authn filter is not configured or not working."
    )


async def test_e2e_wrong_issuer_jwt_rejected_by_envoy() -> None:
    """S7 extension: JWT with wrong iss must be rejected by Envoy."""
    import time

    import jwt as pyjwt

    now = int(time.time())
    wrong_iss_claims = {
        "sub": str(uuid.uuid4()),
        "tenant_id": str(uuid.uuid4()),
        "email": "wrong@e2e.io",
        "role": "owner",
        "iss": "wrong-issuer",  # should be "ai-proxy"
        "iat": now,
        "exp": now + 86400,
    }
    token = pyjwt.encode(wrong_iss_claims, E2E_JWT_SECRET, algorithm="HS256")

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{ENVOY_BASE}{ADMIN_KEYS_PATH}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5.0,
        )

    assert resp.status_code == 401, (
        f"Expected 401 for wrong-issuer JWT, got {resp.status_code}"
    )


async def test_e2e_no_jwt_admin_rejected_by_envoy() -> None:
    """S7 extension: /admin/* with no Authorization header is rejected by Envoy jwt_authn."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{ENVOY_BASE}{ADMIN_KEYS_PATH}",
            timeout=5.0,
        )

    assert resp.status_code == 401, (
        f"Expected 401 from Envoy (no JWT), got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# Scenario S8 — valid API key via Authorization: Bearer passes /v1/* ext_authz
# ---------------------------------------------------------------------------


async def test_e2e_valid_key_bearer_passes_v1() -> None:
    """S8: A valid API key in Authorization: Bearer passes Envoy ext_authz for /v1/*.

    We do NOT assert a 200 from the gateway (it will fail upstream OpenRouter call).
    We assert that Envoy did NOT reject it (not 401/403 from Envoy ext_authz).
    A 4xx/5xx from the gateway itself (missing model, no OpenRouter key) is acceptable.
    RED reason: Envoy config + Bearer path in /internal/authz both missing.
    """
    async with httpx.AsyncClient() as client:
        jwt_token = await _e2e_signup_and_login(
            client,
            tenant_name=f"E2EV1Co-{uuid.uuid4().hex[:6]}",
            email=f"v1-{uuid.uuid4().hex[:6]}@e2e.io",
        )
        key_body = await _e2e_create_key(client, jwt_token, "e2e-bearer-v1")
        full_key: str = key_body["key"]

        resp = await client.post(
            f"{ENVOY_BASE}{V1_PATH}",
            json={
                "model": "openai/gpt-3.5-turbo",
                "messages": [{"role": "user", "content": "ping"}],
            },
            headers={"Authorization": f"Bearer {full_key}"},
            timeout=10.0,
        )

    # ext_authz passed the request to the gateway if status is NOT 401/403 from Envoy
    # (Envoy ext_authz returns 403 by default on authz failure, gateway returns problem+json 401)
    assert resp.status_code not in (401, 403), (
        f"Envoy rejected the request before reaching the gateway. "
        f"Status: {resp.status_code}, body: {resp.text[:500]!r}. "
        f"This means ext_authz or the Bearer path in /internal/authz is not working."
    )


# ---------------------------------------------------------------------------
# Scenario S9 — invalid API key rejected via ext_authz
# ---------------------------------------------------------------------------


async def test_e2e_invalid_key_rejected_v1() -> None:
    """S9: An invalid API key in Authorization: Bearer is rejected by Envoy ext_authz.

    ext_authz calls /internal/authz, which returns 401.
    Envoy must forward the 401 to the client (not swallow it).
    RED reason: Envoy config missing.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{ENVOY_BASE}{V1_PATH}",
            json={"model": "openai/gpt-3.5-turbo", "messages": [{"role": "user", "content": "ping"}]},
            headers={"Authorization": "Bearer sk-" + "0" * 32 + ".invalidinvalidinvalid"},
            timeout=5.0,
        )

    assert resp.status_code == 401, (
        f"Expected 401 for invalid key via ext_authz, got {resp.status_code}: {resp.text}"
    )


async def test_e2e_no_key_v1_rejected() -> None:
    """S9 extension: /v1/* with no Authorization header is rejected by ext_authz."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{ENVOY_BASE}{V1_PATH}",
            json={"model": "openai/gpt-3.5-turbo", "messages": []},
            timeout=5.0,
        )

    assert resp.status_code == 401, (
        f"Expected 401 for missing key, got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# Scenario S10 — rate limit enforced at 50 req/s
# ---------------------------------------------------------------------------


async def test_e2e_rate_limit_enforced() -> None:
    """S10: Sending 60+ rapid requests triggers the local rate limiter (429).

    The token bucket fills at 50/s; sending 60 synchronously within 1 second
    must result in at least one 429 response.
    RED reason: Envoy config missing; rate limit filter not configured.

    Note: this test uses an unauthenticated path (/health) to avoid the
    ext_authz overhead and keep the burst tight. The rate limit applies globally.
    """
    async with httpx.AsyncClient() as client:
        tasks = [
            client.get(f"{ENVOY_BASE}{HEALTH_PATH}", timeout=2.0)
            for _ in range(60)
        ]
        start = time.monotonic()
        responses = await asyncio.gather(*tasks, return_exceptions=True)
        elapsed = time.monotonic() - start

    status_codes = [
        r.status_code
        for r in responses
        if isinstance(r, httpx.Response)
    ]

    # Must have fired within a short window for the burst to be meaningful
    # TIME BUDGET: NOT MEASURED — this is the weakest of the 16 and is stated as such
    # rather than given a fabricated margin. It bounds N real HTTP round-trips through
    # Docker + Envoy, so the good path is genuine network work whose cost is unknown here;
    # the edge suite is dispatch-only (make e2e-edge) so it has never run under -n 12 load.
    # It is also flake class 10's SECOND population (todo #111): the clock stands in for
    # 'all N requests landed inside one rate-limit window', which nothing here measures.
    # Convert to a causal window assertion under #111, do not just raise this number.
    assert elapsed < 3.0, (
        f"Rate limit test took {elapsed:.1f}s — requests were too slow to test burst semantics"
    )

    rate_limited = [s for s in status_codes if s == 429]
    assert len(rate_limited) > 0, (
        f"Expected at least one 429 from local rate limiter after 60 rapid requests, "
        f"got none. Status codes seen: {sorted(set(status_codes))}. "
        f"Check Envoy local_ratelimit filter config (token_bucket max_tokens=50)."
    )

    non_limited = [s for s in status_codes if s != 429]
    assert len(non_limited) > 0, (
        f"ALL {len(status_codes)} requests were rate-limited — the token bucket may be "
        f"misconfigured (max_tokens too low or fill_interval wrong)."
    )
