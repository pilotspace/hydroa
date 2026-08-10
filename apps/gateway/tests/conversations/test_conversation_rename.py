"""RED suite — PATCH /v1/conversations/{id} (rename).

Contract under test (FROZEN v1):
  PATCH /v1/conversations/{id}   body: {"title": str}
    200 -> {id, title, created_at, updated_at}
    401 -> missing/invalid bearer
    404 -> unknown id, cross-tenant, or deleted (no existence leak)
    422 -> missing/blank/oversized title

TENANT-ISOLATION INVARIANT: cross-tenant rename must be 404,
no data leak, and must NOT modify the target row.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncio
import pytest
from sqlalchemy import update

from gateway.keys.infrastructure.orm import ApiKeyRow

pytestmark = pytest.mark.asyncio

# ---------------------------------------------------------------------------
# Helpers (identical pattern to test_conversations.py)
# ---------------------------------------------------------------------------


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _signup_and_key(
    client: Any, *, tenant_name: str, email: str, password: str
) -> dict[str, str]:
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
    return await _signup_and_key(
        client,
        tenant_name="RenameTest",
        email="rename-test@example.io",
        password="rename-test-battery",
    )


@pytest.fixture
async def other_tenant(client: Any) -> dict[str, str]:
    return await _signup_and_key(
        client,
        tenant_name="RenameOther",
        email="rename-other@example.io",
        password="rename-other-battery",
    )


# ---------------------------------------------------------------------------
# PATCH /v1/conversations/{id} — success
# ---------------------------------------------------------------------------


class TestRenameConversation:
    async def test_rename_success_200(self, client: Any, api_key_info: dict[str, str]) -> None:
        """PATCH with a valid title returns 200 and the updated conversation."""
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "Old Title"},
            headers=_bearer(api_key_info["key"]),
        )
        assert create_resp.status_code == 201
        conv_id = create_resp.json()["id"]

        resp = await client.patch(
            f"/v1/conversations/{conv_id}",
            json={"title": "New Title"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == conv_id
        assert body["title"] == "New Title"
        assert "created_at" in body
        assert "updated_at" in body

    async def test_rename_bumps_updated_at(self, client: Any, api_key_info: dict[str, str]) -> None:
        """updated_at after PATCH must be >= the original created_at timestamp."""
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "Bump Me"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]
        original_updated_at = create_resp.json()["updated_at"]

        # NEGATIVE WAIT: a real wall-clock gap, not a wait for state. The assertion below
        # is that `updated_at` MOVED, which requires the clock to actually advance between
        # the create and the patch. There is nothing to poll for — polling would return
        # instantly and the two timestamps could be identical, making the assertion vacuous.
        await asyncio.sleep(0.01)  # ensure clock advances

        patch_resp = await client.patch(
            f"/v1/conversations/{conv_id}",
            json={"title": "Bumped"},
            headers=_bearer(api_key_info["key"]),
        )
        assert patch_resp.status_code == 200
        new_updated_at = patch_resp.json()["updated_at"]
        assert new_updated_at >= original_updated_at

    async def test_rename_reflected_in_get(self, client: Any, api_key_info: dict[str, str]) -> None:
        """After PATCH, GET conversation/{id} returns the new title."""
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "Before"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]

        await client.patch(
            f"/v1/conversations/{conv_id}",
            json={"title": "After"},
            headers=_bearer(api_key_info["key"]),
        )

        get_resp = await client.get(
            f"/v1/conversations/{conv_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["title"] == "After"

    async def test_rename_reflected_in_list(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        """After PATCH, GET /v1/conversations returns the new title in the list."""
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "Listed Before"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]

        await client.patch(
            f"/v1/conversations/{conv_id}",
            json={"title": "Listed After"},
            headers=_bearer(api_key_info["key"]),
        )

        list_resp = await client.get("/v1/conversations", headers=_bearer(api_key_info["key"]))
        titles = [c["title"] for c in list_resp.json()["data"]]
        assert "Listed After" in titles
        assert "Listed Before" not in titles


# ---------------------------------------------------------------------------
# 404 — unknown id and cross-tenant
# ---------------------------------------------------------------------------


class TestRenameNotFound:
    async def test_rename_unknown_id_returns_404(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        resp = await client.patch(
            f"/v1/conversations/{uuid.uuid4()}",
            json={"title": "Ghost"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 404

    async def test_cross_tenant_rename_returns_404(
        self, client: Any, api_key_info: dict[str, str], other_tenant: dict[str, str]
    ) -> None:
        """TENANT ISOLATION: Tenant B cannot rename Tenant A's conversation — 404, no data leak."""
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "TenantA private"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]

        resp = await client.patch(
            f"/v1/conversations/{conv_id}",
            json={"title": "Stolen"},
            headers=_bearer(other_tenant["key"]),
        )
        assert resp.status_code == 404

        # Conversation must still have the original title for tenant A
        get_resp = await client.get(
            f"/v1/conversations/{conv_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["title"] == "TenantA private"

    async def test_rename_deleted_conversation_returns_404(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        """A soft-deleted conversation must 404 on PATCH."""
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "ToDelete"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]
        await client.delete(f"/v1/conversations/{conv_id}", headers=_bearer(api_key_info["key"]))

        resp = await client.patch(
            f"/v1/conversations/{conv_id}",
            json={"title": "Renamed after delete"},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 422 — validation
# ---------------------------------------------------------------------------


class TestRenameValidation:
    async def test_rename_blank_title_returns_422(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "Original"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]

        resp = await client.patch(
            f"/v1/conversations/{conv_id}",
            json={"title": "   "},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 422

        # Title must be unchanged
        get_resp = await client.get(
            f"/v1/conversations/{conv_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert get_resp.json()["title"] == "Original"

    async def test_rename_missing_title_returns_422(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "KeepMe"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]

        resp = await client.patch(
            f"/v1/conversations/{conv_id}",
            json={},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 422

    async def test_rename_title_too_long_returns_422(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        create_resp = await client.post(
            "/v1/conversations",
            json={"title": "Short"},
            headers=_bearer(api_key_info["key"]),
        )
        conv_id = create_resp.json()["id"]

        resp = await client.patch(
            f"/v1/conversations/{conv_id}",
            json={"title": "x" * 501},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 401 — authentication
# ---------------------------------------------------------------------------


class TestRenameAuth:
    async def test_rename_missing_bearer_returns_401(self, client: Any) -> None:
        resp = await client.patch(
            f"/v1/conversations/{uuid.uuid4()}",
            json={"title": "Anon"},
        )
        assert resp.status_code == 401

    async def test_rename_invalid_bearer_returns_401(self, client: Any) -> None:
        resp = await client.patch(
            f"/v1/conversations/{uuid.uuid4()}",
            json={"title": "Anon"},
            headers={"Authorization": "Bearer sk-invalid"},
        )
        assert resp.status_code == 401
