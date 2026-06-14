"""Red suite for api-keys (contract DRAFT — §3 of .add/tasks/api-keys/TASK.md).

One test per scenario in §2 SCENARIOS.
Asserts observable behavior only — endpoints, problem+json codes, DB row effects, authz responses.
All tests MUST fail with ImportError / 404 routing errors before the Build phase; the right
red reason is "gateway.keys.* does not exist yet".
"""

import hashlib
import re
import secrets
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Constants — mirror §3 CONTRACT
# ---------------------------------------------------------------------------
ADMIN_KEYS = "/admin/keys"
INTERNAL_AUTHZ = "/internal/authz"
SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"

_KEY_RE = re.compile(r"^sk-([0-9a-f]+)\.([A-Za-z0-9_\-]+)$")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, f"expected {status}, got {resp.status_code}: {resp.text}"
    body: dict[str, Any] = resp.json()
    assert body["code"] == code, f"expected code {code!r}, got {body.get('code')!r}"
    assert body["status"] == status
    assert "title" in body
    return body


async def signup_and_login(
    client: httpx.AsyncClient,
    *,
    tenant_name: str,
    email: str,
    password: str = "correct horse battery",
) -> str:
    """Sign up a new tenant+owner and return a valid Bearer JWT."""
    sr = await client.post(
        SIGNUP,
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert sr.status_code == 201, f"signup failed: {sr.text}"
    lr = await client.post(LOGIN, json={"email": email, "password": password})
    assert lr.status_code == 200, f"login failed: {lr.text}"
    return lr.json()["access_token"]


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _sha256_hex(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


async def _key_row_count(db: AsyncSession) -> int:
    result = await db.execute(text("SELECT count(*) FROM api_keys"))
    return int(result.scalar_one())


# ---------------------------------------------------------------------------
# Scenario 1: owner creates an API key — plaintext shown once
# ---------------------------------------------------------------------------


async def test_owner_creates_key_plaintext_shown_once(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token = await signup_and_login(client, tenant_name="Acme", email="ada@acme.io")

    resp = await client.post(ADMIN_KEYS, json={"name": "prod-key"}, headers=bearer(token))

    assert resp.status_code == 201
    body = resp.json()
    assert "key_id" in body
    assert body["name"] == "prod-key"
    full_key: str = body["key"]

    # Key must match format sk-<hex>.<urlsafe_b64_secret>
    m = _KEY_RE.fullmatch(full_key)
    assert m is not None, f"key {full_key!r} does not match expected format"
    key_id_hex, secret = m.group(1), m.group(2)
    assert len(key_id_hex) == 32, "key_id hex must be 32 chars (16-byte UUID without dashes)"

    # Exactly one row in api_keys, hash matches SHA-256(secret), revoked_at is NULL
    row = (
        await db_session.execute(
            text("SELECT key_hash, revoked_at FROM api_keys WHERE id = :id"),
            {"id": key_id_hex},  # infrastructure converts hex to uuid
        )
    ).one()
    assert row.key_hash == _sha256_hex(secret)
    assert row.revoked_at is None

    # Subsequent GET must not contain the secret or key_hash
    list_resp = await client.get(ADMIN_KEYS, headers=bearer(token))
    assert list_resp.status_code == 200
    for item in list_resp.json():
        assert "key" not in item
        assert "key_hash" not in item
        assert "secret" not in item
        assert secret not in str(item)


# ---------------------------------------------------------------------------
# Scenario 2: member JWT role check
# ---------------------------------------------------------------------------


async def test_member_jwt_cannot_create_key(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    """Member role → 403 ERR_AUTH_FORBIDDEN; no api_keys row created."""
    import jwt as pyjwt

    from tests.conftest import TEST_JWT_SECRET

    # We need a tenant to attach the member user to; sign up as owner first.
    owner_token = await signup_and_login(client, tenant_name="Acme2", email="owner2@acme.io")
    import uuid

    # Decode owner token to get tenant_id
    owner_claims = pyjwt.decode(
        owner_token, TEST_JWT_SECRET, algorithms=["HS256"], options={"verify_exp": False}
    )
    tenant_id = owner_claims["tenant_id"]

    # Mint a member-role token for the same tenant
    member_claims = {
        "sub": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "email": "member@acme.io",
        "role": "member",
        "iss": "ai-proxy",
        "iat": owner_claims["iat"],
        "exp": owner_claims["exp"],
    }
    member_token = pyjwt.encode(member_claims, TEST_JWT_SECRET, algorithm="HS256")

    before = await _key_row_count(db_session)
    resp = await client.post(ADMIN_KEYS, json={"name": "any"}, headers=bearer(member_token))

    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")
    assert await _key_row_count(db_session) == before


# ---------------------------------------------------------------------------
# Scenario 3: create key with missing JWT is rejected
# ---------------------------------------------------------------------------


async def test_create_key_no_jwt_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    before = await _key_row_count(db_session)
    resp = await client.post(ADMIN_KEYS, json={"name": "x"})

    assert_problem(resp, 401, "ERR_AUTH_INVALID_TOKEN")
    assert await _key_row_count(db_session) == before


# ---------------------------------------------------------------------------
# Scenario 4: create key with empty name is rejected
# ---------------------------------------------------------------------------


async def test_create_key_empty_name_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    token = await signup_and_login(client, tenant_name="Acme3", email="ada3@acme.io")
    before = await _key_row_count(db_session)

    resp = await client.post(ADMIN_KEYS, json={"name": ""}, headers=bearer(token))

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    assert await _key_row_count(db_session) == before


# ---------------------------------------------------------------------------
# Scenario 5: list keys returns all without secrets
# ---------------------------------------------------------------------------


async def test_list_keys_returns_all_without_secrets(
    client: httpx.AsyncClient,
) -> None:
    token = await signup_and_login(client, tenant_name="Acme4", email="ada4@acme.io")

    # Create two keys
    k1 = (await client.post(ADMIN_KEYS, json={"name": "key-one"}, headers=bearer(token))).json()
    k2 = (await client.post(ADMIN_KEYS, json={"name": "key-two"}, headers=bearer(token))).json()

    # Revoke key-two
    revoke_resp = await client.delete(f"{ADMIN_KEYS}/{k2['key_id']}", headers=bearer(token))
    assert revoke_resp.status_code == 204

    list_resp = await client.get(ADMIN_KEYS, headers=bearer(token))
    assert list_resp.status_code == 200
    items: list[dict[str, Any]] = list_resp.json()

    key_ids = {item["key_id"] for item in items}
    assert k1["key_id"] in key_ids
    assert k2["key_id"] in key_ids

    for item in items:
        # Required fields
        assert "key_id" in item
        assert "name" in item
        assert "prefix" in item
        assert "created_at" in item
        assert "revoked_at" in item
        # Forbidden fields
        assert "key" not in item
        assert "key_hash" not in item
        assert "secret" not in item


async def test_list_keys_reports_true_cache_enabled(
    client: httpx.AsyncClient,
) -> None:
    """GET /admin/keys must report each key's TRUE cache_enabled, not the default.

    RED before the fix: list_keys drops cache_enabled from KeyInfoResponse, so the
    field falls back to its schema default (False) for every key — a caching-ON key
    is reported OFF. This is the footgun behind the v15 key-editor cache toggle.
    """
    token = await signup_and_login(client, tenant_name="CacheCo", email="cache@co.io")

    # Key A: caching ON (set via PATCH — the path the editor uses)
    a = (await client.post(ADMIN_KEYS, json={"name": "cache-on"}, headers=bearer(token))).json()
    patch_resp = await client.patch(
        f"{ADMIN_KEYS}/{a['key_id']}",
        json={"cache_enabled": True},
        headers=bearer(token),
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["cache_enabled"] is True

    # Key B: default (caching OFF)
    b = (await client.post(ADMIN_KEYS, json={"name": "cache-off"}, headers=bearer(token))).json()

    list_resp = await client.get(ADMIN_KEYS, headers=bearer(token))
    assert list_resp.status_code == 200
    items: list[dict[str, Any]] = list_resp.json()
    by_id = {item["key_id"]: item for item in items}

    # The list must reflect each key's TRUE cache_enabled — not the always-false default
    assert by_id[a["key_id"]]["cache_enabled"] is True
    assert by_id[b["key_id"]]["cache_enabled"] is False


# ---------------------------------------------------------------------------
# Scenario 6: list keys is tenant-scoped
# ---------------------------------------------------------------------------


async def test_list_keys_tenant_scoped(
    client: httpx.AsyncClient,
) -> None:
    token_a = await signup_and_login(client, tenant_name="Acme5", email="ada5@acme.io")
    token_b = await signup_and_login(client, tenant_name="Beta5", email="eve5@beta.io")

    k_acme = (
        await client.post(ADMIN_KEYS, json={"name": "acme-key"}, headers=bearer(token_a))
    ).json()
    k_beta = (
        await client.post(ADMIN_KEYS, json={"name": "beta-key"}, headers=bearer(token_b))
    ).json()

    list_resp = await client.get(ADMIN_KEYS, headers=bearer(token_a))
    assert list_resp.status_code == 200
    items = list_resp.json()

    key_ids = {item["key_id"] for item in items}
    assert k_acme["key_id"] in key_ids
    assert k_beta["key_id"] not in key_ids
    assert len(items) == 1


# ---------------------------------------------------------------------------
# Scenario 7: owner revokes a key — authz fails immediately
# ---------------------------------------------------------------------------


async def test_revoke_key_fails_authz_immediately(
    client: httpx.AsyncClient,
) -> None:
    token = await signup_and_login(client, tenant_name="Acme6", email="ada6@acme.io")
    create_resp = await client.post(ADMIN_KEYS, json={"name": "short-lived"}, headers=bearer(token))
    assert create_resp.status_code == 201
    key_body = create_resp.json()
    full_key = key_body["key"]
    key_id = key_body["key_id"]

    # Verify active before revoke
    authz_before = await client.post(INTERNAL_AUTHZ, headers={"X-Api-Key": full_key})
    assert authz_before.status_code == 200

    # Revoke
    del_resp = await client.delete(f"{ADMIN_KEYS}/{key_id}", headers=bearer(token))
    assert del_resp.status_code == 204

    # authz must now fail
    authz_after = await client.post(INTERNAL_AUTHZ, headers={"X-Api-Key": full_key})
    assert_problem(authz_after, 401, "ERR_AUTH_INVALID_KEY")


# ---------------------------------------------------------------------------
# Scenario 8: delete cross-tenant key returns 404 not 403
# ---------------------------------------------------------------------------


async def test_delete_cross_tenant_key_returns_404(
    client: httpx.AsyncClient,
) -> None:
    token_a = await signup_and_login(client, tenant_name="Acme7", email="ada7@acme.io")
    token_b = await signup_and_login(client, tenant_name="Beta7", email="eve7@beta.io")

    k_beta = (
        await client.post(ADMIN_KEYS, json={"name": "beta-key"}, headers=bearer(token_b))
    ).json()
    k_beta_plaintext: str = k_beta["key"]

    # Tenant A attempts to delete tenant B's key
    resp = await client.delete(f"{ADMIN_KEYS}/{k_beta['key_id']}", headers=bearer(token_a))
    assert_problem(resp, 404, "ERR_KEY_NOT_FOUND")

    # Tenant B's key is still active
    authz = await client.post(INTERNAL_AUTHZ, headers={"X-Api-Key": k_beta_plaintext})
    assert authz.status_code == 200


# ---------------------------------------------------------------------------
# Scenario 9: member cannot revoke a key
# ---------------------------------------------------------------------------


async def test_member_cannot_revoke_key(
    client: httpx.AsyncClient,
) -> None:
    import uuid

    import jwt as pyjwt

    from tests.conftest import TEST_JWT_SECRET

    owner_token = await signup_and_login(client, tenant_name="Acme8", email="ada8@acme.io")
    k = (
        await client.post(ADMIN_KEYS, json={"name": "protected-key"}, headers=bearer(owner_token))
    ).json()
    full_key: str = k["key"]
    key_id: str = k["key_id"]

    owner_claims = pyjwt.decode(
        owner_token, TEST_JWT_SECRET, algorithms=["HS256"], options={"verify_exp": False}
    )
    member_claims = {
        "sub": str(uuid.uuid4()),
        "tenant_id": owner_claims["tenant_id"],
        "email": "member8@acme.io",
        "role": "member",
        "iss": "ai-proxy",
        "iat": owner_claims["iat"],
        "exp": owner_claims["exp"],
    }
    member_token = pyjwt.encode(member_claims, TEST_JWT_SECRET, algorithm="HS256")

    resp = await client.delete(f"{ADMIN_KEYS}/{key_id}", headers=bearer(member_token))
    assert_problem(resp, 403, "ERR_AUTH_FORBIDDEN")

    # Key must still be active
    authz = await client.post(INTERNAL_AUTHZ, headers={"X-Api-Key": full_key})
    assert authz.status_code == 200


# ---------------------------------------------------------------------------
# Scenario 10: /internal/authz validates a valid active key
# ---------------------------------------------------------------------------


async def test_authz_valid_active_key(
    client: httpx.AsyncClient,
) -> None:
    import jwt as pyjwt

    from tests.conftest import TEST_JWT_SECRET

    token = await signup_and_login(client, tenant_name="Acme9", email="ada9@acme.io")
    owner_claims = pyjwt.decode(
        token, TEST_JWT_SECRET, algorithms=["HS256"], options={"verify_exp": False}
    )
    k = (await client.post(ADMIN_KEYS, json={"name": "authz-key"}, headers=bearer(token))).json()
    full_key: str = k["key"]
    key_id: str = k["key_id"]

    resp = await client.post(INTERNAL_AUTHZ, headers={"X-Api-Key": full_key})
    assert resp.status_code == 200
    body = resp.json()
    assert body["tenant_id"] == owner_claims["tenant_id"]
    assert body["key_id"] == key_id


# ---------------------------------------------------------------------------
# Scenario 11: /internal/authz rejects a revoked key
# ---------------------------------------------------------------------------


async def test_authz_revoked_key_rejected(
    client: httpx.AsyncClient,
) -> None:
    token = await signup_and_login(client, tenant_name="Acme10", email="ada10@acme.io")
    k = (await client.post(ADMIN_KEYS, json={"name": "to-revoke"}, headers=bearer(token))).json()
    full_key: str = k["key"]
    key_id: str = k["key_id"]

    await client.delete(f"{ADMIN_KEYS}/{key_id}", headers=bearer(token))

    resp = await client.post(INTERNAL_AUTHZ, headers={"X-Api-Key": full_key})
    assert_problem(resp, 401, "ERR_AUTH_INVALID_KEY")


# ---------------------------------------------------------------------------
# Scenario 12: /internal/authz rejects malformed keys — identical response
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_key",
    [
        "not-starting-with-sk",
        "sk-nodot",
        "sk-.",
        "sk-ZZZNOTVALIDHEX.somesecretvalue",
        "sk-" + "a" * 32,  # no dot at all (duplicate test emphasis)
        "",
        "Bearer sk-abc.def",  # whole Authorization header passed by mistake
    ],
    ids=[
        "wrong_prefix",
        "no_dot",
        "empty_secret",
        "non_hex_key_id",
        "no_dot_long",
        "empty_string",
        "authorization_header_mistake",
    ],
)
async def test_authz_malformed_key_rejected(client: httpx.AsyncClient, bad_key: str) -> None:
    resp = await client.post(INTERNAL_AUTHZ, headers={"X-Api-Key": bad_key})
    assert_problem(resp, 401, "ERR_AUTH_INVALID_KEY")


async def test_authz_malformed_keys_byte_identical(
    client: httpx.AsyncClient,
) -> None:
    """All malformed-key responses must be byte-identical (no enumeration).

    Also asserts content-type is application/problem+json — this fails red
    against FastAPI's built-in 404 (which returns application/json).
    """
    bad_keys = [
        "not-starting-with-sk",
        "sk-nodot",
        "sk-ZZZNOTVALIDHEX.somesecretvalue",
    ]
    responses = [await client.post(INTERNAL_AUTHZ, headers={"X-Api-Key": k}) for k in bad_keys]
    for r in responses:
        # Must be problem+json, not FastAPI default application/json 404
        assert "application/problem+json" in r.headers.get("content-type", ""), (
            f"expected problem+json content-type, got {r.headers.get('content-type')}"
        )
    first = responses[0].content
    for r in responses[1:]:
        assert r.content == first, "malformed-key responses are not byte-identical"


# ---------------------------------------------------------------------------
# Scenario 13: /internal/authz — valid key_id but wrong secret (no enumeration)
# ---------------------------------------------------------------------------


async def test_authz_wrong_secret_rejected_constant_time(
    client: httpx.AsyncClient,
) -> None:
    """Wrong secret and unknown key_id must return byte-identical 401 responses."""
    token = await signup_and_login(client, tenant_name="Acme11", email="ada11@acme.io")
    k = (await client.post(ADMIN_KEYS, json={"name": "timing-key"}, headers=bearer(token))).json()
    full_key: str = k["key"]

    # Extract key_id hex from the key
    m = _KEY_RE.fullmatch(full_key)
    assert m is not None
    key_id_hex = m.group(1)

    # Valid key_id, wrong secret (different 32-byte random value)
    wrong_secret = secrets.token_urlsafe(32)
    wrong_key = f"sk-{key_id_hex}.{wrong_secret}"

    # Completely unknown key_id
    unknown_key = "sk-" + "deadbeef" * 4 + "." + secrets.token_urlsafe(32)

    resp_wrong = await client.post(INTERNAL_AUTHZ, headers={"X-Api-Key": wrong_key})
    resp_unknown = await client.post(INTERNAL_AUTHZ, headers={"X-Api-Key": unknown_key})

    assert_problem(resp_wrong, 401, "ERR_AUTH_INVALID_KEY")
    assert_problem(resp_unknown, 401, "ERR_AUTH_INVALID_KEY")
    # Byte-identical: no timing or content difference leaks key existence
    assert resp_wrong.content == resp_unknown.content
