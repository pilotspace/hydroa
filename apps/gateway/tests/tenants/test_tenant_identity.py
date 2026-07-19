"""Red suite for tenant-identity (contract FROZEN @ v1).

One test per scenario in .add/tasks/tenant-identity/TASK.md §2.
Asserts observable behavior only — endpoints, problem+json codes, DB row effects.
"""

import time
from typing import Any

import httpx
import jwt
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import TEST_JWT_SECRET

SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ME = "/admin/auth/me"

ADA = {"tenant_name": "Acme", "email": "ada@acme.io", "password": "correct horse battery"}


async def row_counts(db: AsyncSession) -> tuple[int, int]:
    tenants = (await db.execute(text("SELECT count(*) FROM tenants"))).scalar_one()
    users = (await db.execute(text("SELECT count(*) FROM users"))).scalar_one()
    return int(tenants), int(users)


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status
    body: dict[str, Any] = resp.json()
    assert body["code"] == code
    assert body["status"] == status
    assert "title" in body
    return body


async def test_signup_creates_tenant_and_owner_atomically(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    resp = await client.post(SIGNUP, json=ADA)

    assert resp.status_code == 201
    body = resp.json()
    assert body["tenant_id"] and body["user_id"]

    tenants, users = await row_counts(db_session)
    assert (tenants, users) == (1, 1)
    role = (
        await db_session.execute(text("SELECT role FROM users WHERE email = 'ada@acme.io'"))
    ).scalar_one()
    assert role == "owner"


async def test_signup_taken_email_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    assert (await client.post(SIGNUP, json=ADA)).status_code == 201

    again = {**ADA, "tenant_name": "Other Org", "email": "ADA@ACME.IO"}
    resp = await client.post(SIGNUP, json=again)

    assert_problem(resp, 409, "ERR_TENANT_EMAIL_TAKEN")
    assert await row_counts(db_session) == (1, 1)


async def test_signup_weak_password_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    resp = await client.post(
        SIGNUP, json={"tenant_name": "Acme", "email": "bob@acme.io", "password": "ninechars"}
    )

    assert_problem(resp, 400, "ERR_AUTH_PASSWORD_WEAK")
    assert await row_counts(db_session) == (0, 0)


async def test_signup_malformed_payload_rejected(
    client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    resp = await client.post(
        SIGNUP, json={"tenant_name": "Acme", "email": "not-an-email", "password": "long enough pw"}
    )

    assert_problem(resp, 422, "ERR_PAYLOAD_INVALID")
    assert await row_counts(db_session) == (0, 0)


async def test_login_returns_tenant_scoped_jwt(client: httpx.AsyncClient) -> None:
    signup = await client.post(SIGNUP, json=ADA)
    assert signup.status_code == 201

    resp = await client.post(LOGIN, json={"email": ADA["email"], "password": ADA["password"]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 86400

    claims = jwt.decode(
        body["access_token"], TEST_JWT_SECRET, algorithms=["HS256"], issuer="ai-proxy"
    )
    assert claims["sub"] == signup.json()["user_id"]
    assert claims["tenant_id"] == signup.json()["tenant_id"]
    assert claims["role"] == "owner"
    assert claims["exp"] - claims["iat"] == 86400
    assert claims["exp"] > time.time()


async def test_login_wrong_password_no_enumeration(client: httpx.AsyncClient) -> None:
    assert (await client.post(SIGNUP, json=ADA)).status_code == 201

    wrong_pw = await client.post(LOGIN, json={"email": ADA["email"], "password": "wrong password!"})
    ghost = await client.post(LOGIN, json={"email": "ghost@acme.io", "password": "wrong password!"})

    assert_problem(wrong_pw, 401, "ERR_AUTH_INVALID_CREDENTIALS")
    assert_problem(ghost, 401, "ERR_AUTH_INVALID_CREDENTIALS")
    assert wrong_pw.content == ghost.content  # byte-identical: no user enumeration
    assert "access_token" not in wrong_pw.text


async def test_me_returns_identity(client: httpx.AsyncClient) -> None:
    signup = await client.post(SIGNUP, json=ADA)
    token = (
        await client.post(LOGIN, json={"email": ADA["email"], "password": ADA["password"]})
    ).json()["access_token"]

    resp = await client.get(ME, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "user_id": signup.json()["user_id"],
        "tenant_id": signup.json()["tenant_id"],
        "email": "ada@acme.io",
        "role": "owner",
        # CROSS-TASK RECONCILIATION (domain-claims-console TASK.md §4 CR 2026-07-19,
        # Tin-approved): /me now additively carries the caller's OWN tenant display name.
        # ADA signs up with tenant_name "Acme", so /me echoes it. This assertion stays a
        # COMPLETE exact-equality on the /me shape (still fails on any UNEXPECTED field) —
        # the new key is added, nothing weakened.
        "tenant_name": "Acme",
    }


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer not-a-jwt"},
        {"Authorization": "Bearer " + jwt.encode({"sub": "x"}, "wrong-secret", algorithm="HS256")},
        {
            "Authorization": "Bearer "
            + jwt.encode(
                {"sub": "x", "iss": "ai-proxy", "iat": 0, "exp": 1},  # expired in 1970
                TEST_JWT_SECRET,
                algorithm="HS256",
            )
        },
    ],
    ids=["missing", "malformed", "wrong-signature", "expired"],
)
async def test_me_invalid_token_rejected(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    resp = await client.get(ME, headers=headers)

    body = assert_problem(resp, 401, "ERR_AUTH_INVALID_TOKEN")
    assert "email" not in body and "user_id" not in body  # nothing leaked
