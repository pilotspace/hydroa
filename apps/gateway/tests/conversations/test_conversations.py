"""Red/Green test suite for gateway/conversations domain (v40).

Contract under test:
  POST   /v1/conversations              -> 201
  GET    /v1/conversations              -> 200 list (paginated)
  GET    /v1/conversations/{id}         -> 200 detail | 404
  POST   /v1/conversations/{id}/messages -> 201 | 404 | 422
  DELETE /v1/conversations/{id}         -> 204 | 404

TENANT-ISOLATION INVARIANT: cross-tenant access must be 404 with zero data leak.
ALL repository methods must take tenant_id parameter.

Infra: real Postgres (localhost:5433) via root conftest.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import update

from gateway.keys.infrastructure.orm import ApiKeyRow

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _signup_and_key(
    client: Any, *, tenant_name: str, email: str, password: str
) -> dict[str, str]:
    """Register a new tenant, log in, create an API key. Returns {key, key_id, tenant_id, jwt}."""
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": tenant_name, "email": email, "password": password},
    )
    assert signup.status_code == 201, f"signup failed: {signup.text}"
    tenant_id: str = signup.json()["tenant_id"]

    token = (
        await client.post("/admin/auth/login", json={"email": email, "password": password})
    ).json()["access_token"]

    created = await client.post(
        "/admin/keys",
        json={"name": f"ci-key-{tenant_name}"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, f"key creation failed: {created.text}"
    return {
        "key": created.json()["key"],
        "key_id": created.json()["key_id"],
        "tenant_id": tenant_id,
        "jwt": token,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def api_key_info(client: Any) -> dict[str, str]:
    """Primary tenant fixture."""
    return await _signup_and_key(
        client,
        tenant_name="ConvTest",
        email="conv-test@example.io",
        password="conv-test-battery",
    )


@pytest.fixture
async def other_tenant(client: Any) -> dict[str, str]:
    """A SECOND tenant — used for cross-tenant isolation checks."""
    return await _signup_and_key(
        client,
        tenant_name="ConvOther",
        email="conv-other@example.io",
        password="conv-other-battery",
    )


# ---------------------------------------------------------------------------
# POST /v1/conversations
# ---------------------------------------------------------------------------


class TestCreateConversation:
    async def test_creates_with_title(self, client: Any, api_key_info: dict[str, str]) -> None:
        resp = await client.post(
            "/v1/conversations",
            json={"title": "Hello world"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["title"] == "Hello world"
        assert "id" in body
        assert "created_at" in body
        assert "updated_at" in body
        # validate UUID format
        uuid.UUID(body["id"])

    async def test_creates_without_title(self, client: Any, api_key_info: dict[str, str]) -> None:
        resp = await client.post(
            "/v1/conversations",
            json={},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["title"] is None

    async def test_creates_with_null_title(self, client: Any, api_key_info: dict[str, str]) -> None:
        resp = await client.post(
            "/v1/conversations",
            json={"title": None},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 201
        assert resp.json()["title"] is None

    async def test_rejects_missing_bearer(self, client: Any) -> None:
        resp = await client.post("/v1/conversations", json={"title": "x"})
        assert resp.status_code == 401

    async def test_rejects_invalid_bearer(self, client: Any) -> None:
        resp = await client.post(
            "/v1/conversations",
            json={"title": "x"},
            headers={"Authorization": "Bearer sk-invalid"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /v1/conversations
# ---------------------------------------------------------------------------


class TestListConversations:
    async def test_empty_list(self, client: Any, api_key_info: dict[str, str]) -> None:
        resp = await client.get("/v1/conversations", headers=_bearer(api_key_info["key"]))
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["limit"] == 50
        assert body["offset"] == 0

    async def test_lists_created_conversations(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        # Create two conversations
        await client.post(
            "/v1/conversations",
            json={"title": "First"},
            headers=_bearer(api_key_info["key"]),
        )
        await client.post(
            "/v1/conversations",
            json={"title": "Second"},
            headers=_bearer(api_key_info["key"]),
        )
        resp = await client.get("/v1/conversations", headers=_bearer(api_key_info["key"]))
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert len(data) == 2
        titles = {c["title"] for c in data}
        assert titles == {"First", "Second"}
        # Each item has message_count
        for item in data:
            assert "message_count" in item
            assert item["message_count"] == 0

    async def test_message_count_reflects_messages(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "Counted"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]
        await client.post(
            f"/v1/conversations/{conv_id}/messages",
            json={"role": "user", "content": "hello"},
            headers=_bearer(api_key_info["key"]),
        )
        resp = await client.get("/v1/conversations", headers=_bearer(api_key_info["key"]))
        items = resp.json()["data"]
        assert items[0]["message_count"] == 1

    async def test_pagination_limit_offset(self, client: Any, api_key_info: dict[str, str]) -> None:
        for i in range(3):
            await client.post(
                "/v1/conversations",
                json={"title": f"conv-{i}"},
                headers=_bearer(api_key_info["key"]),
            )
        resp = await client.get(
            "/v1/conversations?limit=2&offset=0",
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["limit"] == 2
        assert body["offset"] == 0
        assert len(body["data"]) == 2

    async def test_limit_cap_at_200(self, client: Any, api_key_info: dict[str, str]) -> None:
        resp = await client.get(
            "/v1/conversations?limit=999",
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200
        assert resp.json()["limit"] == 200

    async def test_excludes_deleted_conversations(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "ToDelete"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]
        del_resp = await client.delete(
            f"/v1/conversations/{conv_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert del_resp.status_code == 204

        list_resp = await client.get("/v1/conversations", headers=_bearer(api_key_info["key"]))
        ids = [c["id"] for c in list_resp.json()["data"]]
        assert conv_id not in ids

    async def test_tenant_isolation_list(
        self, client: Any, api_key_info: dict[str, str], other_tenant: dict[str, str]
    ) -> None:
        """Tenant A's conversations are NOT visible to Tenant B."""
        await client.post(
            "/v1/conversations",
            json={"title": "TenantA conv"},
            headers=_bearer(api_key_info["key"]),
        )
        resp = await client.get("/v1/conversations", headers=_bearer(other_tenant["key"]))
        assert resp.status_code == 200
        assert resp.json()["data"] == []

    async def test_rejects_missing_bearer(self, client: Any) -> None:
        resp = await client.get("/v1/conversations")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /v1/conversations/{id}
# ---------------------------------------------------------------------------


class TestGetConversation:
    async def test_get_returns_conversation_with_messages(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "Detail test"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]

        msg_resp = await client.post(
            f"/v1/conversations/{conv_id}/messages",
            json={"role": "user", "content": "hi there"},
            headers=_bearer(api_key_info["key"]),
        )
        assert msg_resp.status_code == 201

        resp = await client.get(
            f"/v1/conversations/{conv_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == conv_id
        assert body["title"] == "Detail test"
        assert len(body["messages"]) == 1
        msg = body["messages"][0]
        assert msg["role"] == "user"
        assert msg["content"] == "hi there"
        assert "id" in msg
        assert "created_at" in msg

    async def test_get_empty_messages(self, client: Any, api_key_info: dict[str, str]) -> None:
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "No messages"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]

        resp = await client.get(
            f"/v1/conversations/{conv_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200
        assert resp.json()["messages"] == []

    async def test_get_unknown_id_returns_404(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        resp = await client.get(
            f"/v1/conversations/{uuid.uuid4()}",
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 404

    async def test_get_deleted_returns_404(self, client: Any, api_key_info: dict[str, str]) -> None:
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "ToDeleteGet"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]
        await client.delete(
            f"/v1/conversations/{conv_id}",
            headers=_bearer(api_key_info["key"]),
        )
        resp = await client.get(
            f"/v1/conversations/{conv_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 404

    async def test_cross_tenant_get_returns_404(
        self, client: Any, api_key_info: dict[str, str], other_tenant: dict[str, str]
    ) -> None:
        """TENANT ISOLATION: Tenant B cannot see Tenant A's conversation — 404, no data leak."""
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "TenantA private"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]

        resp = await client.get(
            f"/v1/conversations/{conv_id}",
            headers=_bearer(other_tenant["key"]),
        )
        assert resp.status_code == 404

    async def test_rejects_missing_bearer(self, client: Any) -> None:
        resp = await client.get(f"/v1/conversations/{uuid.uuid4()}")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# POST /v1/conversations/{id}/messages
# ---------------------------------------------------------------------------


class TestAppendMessage:
    async def test_append_user_message(self, client: Any, api_key_info: dict[str, str]) -> None:
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "Msg test"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]

        resp = await client.post(
            f"/v1/conversations/{conv_id}/messages",
            json={"role": "user", "content": "Hello!"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["role"] == "user"
        assert body["content"] == "Hello!"
        assert "id" in body
        assert "created_at" in body

    async def test_append_assistant_message(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "Assist test"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]

        resp = await client.post(
            f"/v1/conversations/{conv_id}/messages",
            json={"role": "assistant", "content": "I can help."},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 201
        assert resp.json()["role"] == "assistant"

    async def test_append_system_message(self, client: Any, api_key_info: dict[str, str]) -> None:
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "Sys test"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]

        resp = await client.post(
            f"/v1/conversations/{conv_id}/messages",
            json={"role": "system", "content": "You are helpful."},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 201
        assert resp.json()["role"] == "system"

    async def test_append_bumps_updated_at(self, client: Any, api_key_info: dict[str, str]) -> None:
        """Appending a message must bump the parent conversation's updated_at."""
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "Bump test"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]
        original_updated_at = create_resp.json()["updated_at"]

        import asyncio

        # NEGATIVE WAIT: a real wall-clock gap so `updated_at` can differ — same reasoning
        # as test_conversation_rename.py. Nothing to poll for; the elapsed time is the point.
        await asyncio.sleep(0.01)  # ensure clock advances

        await client.post(
            f"/v1/conversations/{conv_id}/messages",
            json={"role": "user", "content": "bump"},
            headers=_bearer(api_key_info["key"]),
        )

        detail = await client.get(
            f"/v1/conversations/{conv_id}",
            headers=_bearer(api_key_info["key"]),
        )
        new_updated_at = detail.json()["updated_at"]
        assert new_updated_at >= original_updated_at

    async def test_reject_invalid_role(self, client: Any, api_key_info: dict[str, str]) -> None:
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "Role test"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]

        resp = await client.post(
            f"/v1/conversations/{conv_id}/messages",
            json={"role": "admin", "content": "bad role"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 422

    async def test_reject_empty_content(self, client: Any, api_key_info: dict[str, str]) -> None:
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "Empty content"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]

        resp = await client.post(
            f"/v1/conversations/{conv_id}/messages",
            json={"role": "user", "content": "   "},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 422

    async def test_reject_content_too_long(self, client: Any, api_key_info: dict[str, str]) -> None:
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "Long content"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]

        resp = await client.post(
            f"/v1/conversations/{conv_id}/messages",
            json={"role": "user", "content": "x" * 1_000_001},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 422

    async def test_append_unknown_conversation_returns_404(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        resp = await client.post(
            f"/v1/conversations/{uuid.uuid4()}/messages",
            json={"role": "user", "content": "hello"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 404

    async def test_cross_tenant_append_returns_404(
        self, client: Any, api_key_info: dict[str, str], other_tenant: dict[str, str]
    ) -> None:
        """TENANT ISOLATION: Tenant B cannot append to Tenant A's conversation."""
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "TenantA msg isolation"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]

        resp = await client.post(
            f"/v1/conversations/{conv_id}/messages",
            json={"role": "user", "content": "spy message"},
            headers=_bearer(other_tenant["key"]),
        )
        assert resp.status_code == 404

    async def test_rejects_missing_bearer(self, client: Any) -> None:
        resp = await client.post(
            f"/v1/conversations/{uuid.uuid4()}/messages",
            json={"role": "user", "content": "hi"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /v1/conversations/{id}
# ---------------------------------------------------------------------------


class TestDeleteConversation:
    async def test_delete_returns_204(self, client: Any, api_key_info: dict[str, str]) -> None:
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "DeleteMe"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]

        resp = await client.delete(
            f"/v1/conversations/{conv_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 204

    async def test_delete_unknown_returns_404(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        resp = await client.delete(
            f"/v1/conversations/{uuid.uuid4()}",
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 404

    async def test_delete_already_deleted_returns_404(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "DoubleDelete"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]
        await client.delete(
            f"/v1/conversations/{conv_id}",
            headers=_bearer(api_key_info["key"]),
        )
        resp = await client.delete(
            f"/v1/conversations/{conv_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 404

    async def test_cross_tenant_delete_returns_404(
        self, client: Any, api_key_info: dict[str, str], other_tenant: dict[str, str]
    ) -> None:
        """TENANT ISOLATION: Tenant B cannot delete Tenant A's conversation."""
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "TenantA delete isolation"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]

        resp = await client.delete(
            f"/v1/conversations/{conv_id}",
            headers=_bearer(other_tenant["key"]),
        )
        assert resp.status_code == 404

        # Verify the conversation still exists for the right tenant
        get_resp = await client.get(
            f"/v1/conversations/{conv_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert get_resp.status_code == 200

    async def test_rejects_missing_bearer(self, client: Any) -> None:
        resp = await client.delete(f"/v1/conversations/{uuid.uuid4()}")
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Key expiry — parity with the proxy/governance auth path
# ---------------------------------------------------------------------------


class TestExpiredKey:
    """An expired-but-not-revoked key must NOT reach the conversations store.

    The authenticate use-case checks revocation but not expiry; the conversations
    router gates expiry itself (parity with proxy/application/governance). Without
    that gate an expired credential would persist indefinitely on this domain.
    """

    async def test_expired_key_is_rejected_401(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        # Sanity: the key works before expiry.
        ok = await client.post(
            "/v1/conversations", json={"title": "pre"}, headers=_bearer(api_key_info["key"])
        )
        assert ok.status_code == 201

        # Force-expire the key in the DB (1 hour in the past), NOT revoke it.
        async with app.state.sessionmaker() as session:
            await session.execute(
                update(ApiKeyRow)
                .where(ApiKeyRow.id == uuid.UUID(api_key_info["key_id"]))
                .values(expires_at=datetime.now(tz=UTC) - timedelta(hours=1))
            )
            await session.commit()

        # The same key is now rejected with 401.
        resp = await client.post(
            "/v1/conversations", json={"title": "post"}, headers=_bearer(api_key_info["key"])
        )
        assert resp.status_code == 401
