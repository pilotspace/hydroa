"""Red suite — Bearer path for /internal/authz (contract §3, edge-envoy TASK.md).

Layer 1: in-process FastAPI tests, no Docker required.
All tests in this file MUST fail red before the Build phase because:
  - /internal/authz currently reads ONLY X-Api-Key; it does not inspect Authorization: Bearer.
  - Expected failure mode: 401 ERR_AUTH_INVALID_KEY on every Bearer request (key is
    never found because the header is never read), or 404 if the parser short-circuits.

Run with: uv run pytest tests/edge/test_authz_bearer.py -q --no-cov
These tests are NOT @pytest.mark.e2e and run in the default CI suite.
"""

from typing import Any

import httpx
import pytest

INTERNAL_AUTHZ = "/internal/authz"
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"


# ---------------------------------------------------------------------------
# Helpers (duplicated from tests/keys/ intentionally — no cross-test imports)
# ---------------------------------------------------------------------------


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, (
        f"expected HTTP {status}, got {resp.status_code}: {resp.text}"
    )
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, (
        f"expected problem code {code!r}, got {body.get('code')!r}: {body}"
    )
    assert body.get("status") == status
    assert "title" in body
    return body


async def _signup_and_login(
    client: httpx.AsyncClient,
    *,
    tenant_name: str,
    email: str,
    password: str = "correct horse battery staple",
) -> str:
    sr = await client.post(
        SIGNUP,
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    lr = await client.post(LOGIN, json={"email": email, "password": password})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    return lr.json()["access_token"]


def _bearer_jwt(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_key(client: httpx.AsyncClient, jwt_token: str, name: str) -> dict[str, Any]:
    resp = await client.post(ADMIN_KEYS, json={"name": name}, headers=_bearer_jwt(jwt_token))
    assert resp.status_code == 201, f"key creation failed: {resp.text}"
    return resp.json()


# ---------------------------------------------------------------------------
# Scenario S1 — valid Bearer key accepted by /internal/authz
# ---------------------------------------------------------------------------


async def test_bearer_valid_key_accepted(client: httpx.AsyncClient) -> None:
    """S1: POST /internal/authz with Authorization: Bearer <valid-key> returns 200.

    RED reason: /internal/authz only reads X-Api-Key today; Bearer is not inspected.
    Expected current failure: 401 ERR_AUTH_INVALID_KEY (key never looked up).
    """
    jwt_token = await _signup_and_login(
        client, tenant_name="BearerCo", email="ada@bearer.io"
    )
    key_body = await _create_key(client, jwt_token, "bearer-key-1")
    full_key: str = key_body["key"]
    key_id: str = key_body["key_id"]

    # Use Bearer path — the one NOT implemented yet
    resp = await client.post(
        INTERNAL_AUTHZ, headers={"Authorization": f"Bearer {full_key}"}
    )

    assert resp.status_code == 200, (
        f"expected 200 from Bearer authz, got {resp.status_code}: {resp.text}"
    )
    body = resp.json()
    assert "tenant_id" in body, f"missing tenant_id in response: {body}"
    assert "key_id" in body, f"missing key_id in response: {body}"
    assert body["key_id"] == key_id, (
        f"key_id mismatch: expected {key_id}, got {body['key_id']}"
    )

    # Confirm response headers that Envoy ext_authz will forward upstream
    assert "x-tenant-id" in resp.headers, (
        f"missing x-tenant-id response header (required by ext_authz allowed_upstream_headers)"
    )
    assert "x-key-id" in resp.headers, (
        f"missing x-key-id response header (required by ext_authz allowed_upstream_headers)"
    )
    assert resp.headers["x-tenant-id"] == body["tenant_id"]
    assert resp.headers["x-key-id"] == key_id


async def test_bearer_does_not_break_xapikey_path(client: httpx.AsyncClient) -> None:
    """S1 continuation: existing X-Api-Key path must still return 200 after Bearer addition.

    This guards the frozen api-keys contract — the x-api-key path must be completely unchanged.
    """
    jwt_token = await _signup_and_login(
        client, tenant_name="DualPathCo", email="dual@path.io"
    )
    key_body = await _create_key(client, jwt_token, "dual-key")
    full_key: str = key_body["key"]
    key_id: str = key_body["key_id"]

    # x-api-key path (must stay working)
    resp_xkey = await client.post(INTERNAL_AUTHZ, headers={"X-Api-Key": full_key})
    assert resp_xkey.status_code == 200, (
        f"X-Api-Key path broken: {resp_xkey.status_code}: {resp_xkey.text}"
    )
    body_xkey = resp_xkey.json()
    assert body_xkey["key_id"] == key_id

    # Bearer path (new — currently red)
    resp_bearer = await client.post(
        INTERNAL_AUTHZ, headers={"Authorization": f"Bearer {full_key}"}
    )
    assert resp_bearer.status_code == 200, (
        f"Bearer path not returning 200: {resp_bearer.status_code}: {resp_bearer.text}"
    )
    assert resp_bearer.json()["key_id"] == key_id


# ---------------------------------------------------------------------------
# Scenario S2 — revoked key rejected via Bearer path
# ---------------------------------------------------------------------------


async def test_bearer_revoked_key_rejected(client: httpx.AsyncClient) -> None:
    """S2: Revoked key must return 401 ERR_AUTH_INVALID_KEY via Bearer path.

    RED reason: Bearer not implemented yet → 401, but for the WRONG reason (key not found
    because header not read). Once implemented, must still be 401 for the RIGHT reason (revoked).
    We assert the error code to ensure the right semantic is implemented.
    """
    jwt_token = await _signup_and_login(
        client, tenant_name="RevokeCo", email="rev@revoke.io"
    )
    key_body = await _create_key(client, jwt_token, "to-revoke-bearer")
    full_key: str = key_body["key"]
    key_id: str = key_body["key_id"]

    # Revoke the key
    del_resp = await client.delete(
        f"{ADMIN_KEYS}/{key_id}", headers=_bearer_jwt(jwt_token)
    )
    assert del_resp.status_code == 204, f"revoke failed: {del_resp.text}"

    # Confirm x-api-key path still rejects (existing behaviour, must stay green)
    xkey_resp = await client.post(INTERNAL_AUTHZ, headers={"X-Api-Key": full_key})
    assert_problem(xkey_resp, 401, "ERR_AUTH_INVALID_KEY")

    # Bearer path — must also reject the revoked key
    bearer_resp = await client.post(
        INTERNAL_AUTHZ, headers={"Authorization": f"Bearer {full_key}"}
    )
    assert_problem(bearer_resp, 401, "ERR_AUTH_INVALID_KEY")


# ---------------------------------------------------------------------------
# Scenario S3 — malformed Bearer values rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_bearer_value",
    [
        "not-starting-with-sk",
        "sk-nodot",
        "sk-.",
        "sk-ZZZNOTVALIDHEX.somesecretvalue",
        "sk-" + "a" * 32,  # no dot at all
        "",
        "sk-" + "0" * 32 + "." + "x" * 4,  # too short secret
    ],
    ids=[
        "wrong_prefix",
        "no_dot",
        "empty_secret",
        "non_hex_key_id",
        "no_dot_long",
        "empty_bearer_value",
        "short_secret",
    ],
)
async def test_bearer_malformed_key_rejected(
    client: httpx.AsyncClient, bad_bearer_value: str
) -> None:
    """S3: Malformed key in Bearer value returns 401 ERR_AUTH_INVALID_KEY.

    RED reason: Bearer header not read at all today; any Bearer will hit the
    "no header found" path (which currently returns the same 401 anyway).
    After implementation, must return ERR_AUTH_INVALID_KEY for parsing failures.
    """
    resp = await client.post(
        INTERNAL_AUTHZ, headers={"Authorization": f"Bearer {bad_bearer_value}"}
    )
    assert_problem(resp, 401, "ERR_AUTH_INVALID_KEY")


async def test_bearer_malformed_responses_byte_identical_to_xapikey(
    client: httpx.AsyncClient,
) -> None:
    """S3 extension: Bearer malformed response must be byte-identical to X-Api-Key malformed.

    Prevents timing/content enumeration across auth paths.
    RED reason: Bearer path not implemented; response will differ until implemented.
    """
    bad_key = "not-starting-with-sk"

    resp_bearer = await client.post(
        INTERNAL_AUTHZ, headers={"Authorization": f"Bearer {bad_key}"}
    )
    resp_xkey = await client.post(INTERNAL_AUTHZ, headers={"X-Api-Key": bad_key})

    # Both must be problem+json
    assert "application/problem+json" in resp_bearer.headers.get("content-type", ""), (
        f"Bearer path not returning problem+json: {resp_bearer.headers.get('content-type')}"
    )
    assert "application/problem+json" in resp_xkey.headers.get("content-type", ""), (
        f"X-Api-Key path not returning problem+json: {resp_xkey.headers.get('content-type')}"
    )

    # Byte-identical bodies (no path information leakage)
    assert resp_bearer.content == resp_xkey.content, (
        "Bearer and X-Api-Key malformed responses are not byte-identical — "
        f"Bearer: {resp_bearer.text!r}, X-Api-Key: {resp_xkey.text!r}"
    )


# ---------------------------------------------------------------------------
# Scenario S4 — no auth header at all
# ---------------------------------------------------------------------------


async def test_bearer_no_auth_header_rejected(client: httpx.AsyncClient) -> None:
    """S4: POST /internal/authz with no headers returns 401 ERR_AUTH_INVALID_KEY.

    This tests the "neither X-Api-Key nor Authorization present" path.
    Currently already returns 401 (X-Api-Key is empty string). Must stay 401 after
    Bearer addition, for the same ERR_AUTH_INVALID_KEY code.
    NOTE: this test may already be GREEN before implementation if the existing
    empty-X-Api-Key path produces ERR_AUTH_INVALID_KEY. That is acceptable —
    the new code must not break it.
    """
    resp = await client.post(INTERNAL_AUTHZ)
    assert_problem(resp, 401, "ERR_AUTH_INVALID_KEY")


async def test_bearer_authorization_header_non_bearer_scheme_rejected(
    client: httpx.AsyncClient,
) -> None:
    """S4 extension: Authorization header with non-Bearer scheme is rejected.

    e.g. Authorization: Basic ... or Authorization: Token ... must not be accepted.
    RED reason: after implementation, the Bearer scheme check must be explicit.
    """
    resp = await client.post(
        INTERNAL_AUTHZ,
        headers={"Authorization": "Basic dXNlcjpwYXNz"},  # base64("user:pass")
    )
    assert_problem(resp, 401, "ERR_AUTH_INVALID_KEY")


async def test_bearer_priority_over_xapikey_when_both_present(
    client: httpx.AsyncClient,
) -> None:
    """Contract priority: Authorization: Bearer is evaluated first; X-Api-Key ignored if Bearer present.

    Arrange: valid API key, but send it as X-Api-Key AND a different (invalid) Bearer.
    Expect: 401 (Bearer is invalid and takes priority).
    RED reason: priority logic not implemented.
    """
    jwt_token = await _signup_and_login(
        client, tenant_name="PriorityCo", email="pri@priority.io"
    )
    key_body = await _create_key(client, jwt_token, "priority-key")
    full_key: str = key_body["key"]

    # Valid X-Api-Key, invalid Bearer — Bearer must take priority → 401
    resp = await client.post(
        INTERNAL_AUTHZ,
        headers={
            "X-Api-Key": full_key,  # valid
            "Authorization": "Bearer sk-invalid.invalid",  # invalid
        },
    )
    assert_problem(resp, 401, "ERR_AUTH_INVALID_KEY")
