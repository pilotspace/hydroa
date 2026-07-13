"""Fixtures for the residency-policy red/green suite (TASK.md §4, FROZEN @ v2).

Reuses the project-wide `app`/`client`/`db_session` fixtures (tests/conftest.py) plus
the signup->login->create-key pattern used across sibling suites (credits_ledger,
retention_zdr). Model-row seeding includes `region` (region-catalog-dimension is
already shipped on this branch: models.region TEXT NOT NULL DEFAULT 'global').
"""

from __future__ import annotations

import uuid
from typing import Any

import email_validator
import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True, scope="session")
def _allow_test_email_domains() -> None:  # type: ignore[return]
    """Allow RFC 2606 special-use email domains (*.test, *.example, etc.) in tests
    (mirrors tests/oidc_tenant_config/conftest.py — this suite's owner fixture uses
    *.residency.test addresses, rejected by email-validator in production mode)."""
    original = email_validator.TEST_ENVIRONMENT
    email_validator.TEST_ENVIRONMENT = True
    yield
    email_validator.TEST_ENVIRONMENT = original


SIGNUP = "/admin/auth/signup"
LOGIN = "/admin/auth/login"
ADMIN_KEYS = "/admin/keys"
RESIDENCY_POLICY = "/admin/residency-policy"
RETENTION_POLICY = "/admin/retention-policy"
COMPLETIONS = "/v1/chat/completions"
EMBEDDINGS = "/v1/embeddings"


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def auth_key(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def assert_problem(resp: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert resp.status_code == status, (
        f"expected HTTP {status}, got {resp.status_code}: {resp.text}"
    )
    body: dict[str, Any] = resp.json()
    assert body.get("code") == code, f"expected code {code!r}, got {body.get('code')!r}: {body}"
    return body


@pytest.fixture
async def owner(client: httpx.AsyncClient) -> dict[str, str]:
    """Signup -> login -> create API key (OWNER role); returns ids + jwt + plaintext key."""
    n = uuid.uuid4().hex[:8]
    email = f"owner-{n}@residency.test"
    signup = await client.post(
        SIGNUP,
        json={"tenant_name": f"ResidencyCo-{n}", "email": email, "password": PASSWORD},
    )
    assert signup.status_code == 201, signup.text
    tenant_id = signup.json()["tenant_id"]
    login = await client.post(LOGIN, json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    jwt = login.json()["access_token"]
    created = await client.post(ADMIN_KEYS, json={"name": "residency-ci"}, headers=bearer(jwt))
    assert created.status_code == 201, created.text
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
        "jwt": jwt,
        "email": email,
    }


async def insert_model(
    db_session: AsyncSession,
    model_id: str,
    *,
    region: str = "global",
    active: bool = True,
) -> None:
    """Insert (or update) a minimal catalog model row with a region tag."""
    await db_session.execute(
        text(
            "INSERT INTO models (id, name, context_length, active, region)"
            " VALUES (:i, :n, 128000, :a, :r)"
            " ON CONFLICT (id) DO UPDATE SET active = :a, region = :r"
        ),
        {"i": model_id, "n": model_id, "a": active, "r": region},
    )
    await db_session.commit()


async def set_residency_pin(db_session: AsyncSession, tenant_id: str, region: str | None) -> None:
    """Raw-SQL patch of a tenant's residency_region (bypasses the PUT router — used to
    arrange pre-conditions the router itself is not under test for in a given case)."""
    await db_session.execute(
        text(
            "UPDATE tenants SET residency_region = :r, residency_region_updated_at = now()"
            " WHERE id = :tid"
        ),
        {"r": region, "tid": tenant_id},
    )
    await db_session.commit()


async def set_zdr(db_session: AsyncSession, tenant_id: str, enabled: bool) -> None:
    await db_session.execute(
        text("UPDATE tenants SET zdr_enabled = :z WHERE id = :tid"),
        {"z": enabled, "tid": tenant_id},
    )
    await db_session.commit()


def wire_model_groups(app: Any, model_groups: dict[str, list[str]]) -> None:
    """Wire an alias group onto the live app.state.model_router (mirrors sibling
    suites — the router is constructed once at create_app() time; tests override its
    internal _model_groups dict directly since there is no public setter). Settings
    itself is not mutated: Settings.model_groups is a read-only derived property."""
    app.state.model_router._model_groups = model_groups  # noqa: SLF001


class StubChatUpstream:
    """Fake CompletionUpstream — replays a fixed (status, body); records payloads."""

    def __init__(self, body: dict[str, Any] | None = None, status: int = 200) -> None:
        self.status = status
        self.body = body or {
            "id": "gen-1",
            "model": "placeholder",
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
        self.calls: list[dict[str, Any]] = []

    async def complete(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        self.calls.append(dict(payload))
        body = {**self.body, "model": payload.get("model")}
        return self.status, body

    def stream(self, payload: dict[str, Any]) -> Any:
        self.calls.append(dict(payload))

        async def _gen() -> Any:
            yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        return _gen()
