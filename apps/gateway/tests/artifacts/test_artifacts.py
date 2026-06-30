"""Red/Green test suite for gateway/artifacts domain.

Contract under test:
  POST   /v1/artifacts   {name, content_type, content_base64} -> 201 {id, name, content_type, size_bytes, created_at}
  GET    /v1/artifacts   -> 200 {data:[...], limit, offset}   (NO content/content_base64 in items)
  GET    /v1/artifacts/{id} -> 200 raw bytes; Content-Type: stored; Content-Disposition: attachment
  DELETE /v1/artifacts/{id} -> 204 | 404

TENANT-ISOLATION INVARIANT: cross-tenant access must be 404 with zero data leak.
ALL repository methods must take tenant_id parameter.
"""

from __future__ import annotations

import base64
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
        tenant_name="ArtTest",
        email="art-test@example.io",
        password="art-test-battery",
    )


@pytest.fixture
async def other_tenant(client: Any) -> dict[str, str]:
    """A SECOND tenant — used for cross-tenant isolation checks."""
    return await _signup_and_key(
        client,
        tenant_name="ArtOther",
        email="art-other@example.io",
        password="art-other-battery",
    )


# ---------------------------------------------------------------------------
# test_upload_download_roundtrip
# ---------------------------------------------------------------------------


class TestUploadDownloadRoundtrip:
    async def test_upload_download_roundtrip(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        raw_bytes = b"hello artifact"
        encoded = base64.b64encode(raw_bytes).decode()

        # POST — upload
        resp = await client.post(
            "/v1/artifacts",
            json={"name": "test.txt", "content_type": "text/plain", "content_base64": encoded},
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 201, f"upload failed: {resp.text}"
        body = resp.json()
        assert "id" in body
        assert body["name"] == "test.txt"
        assert body["content_type"] == "text/plain"
        assert body["size_bytes"] == len(raw_bytes)
        assert "created_at" in body

        artifact_id = body["id"]

        # GET /{id} — download
        dl = await client.get(
            f"/v1/artifacts/{artifact_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert dl.status_code == 200, f"download failed: {dl.text}"
        assert dl.content == raw_bytes
        assert dl.headers["content-type"].startswith("text/plain")
        assert "attachment" in dl.headers["content-disposition"]


# ---------------------------------------------------------------------------
# test_list_metadata_only
# ---------------------------------------------------------------------------


class TestListMetadataOnly:
    async def test_list_metadata_only(self, client: Any, api_key_info: dict[str, str]) -> None:
        raw_bytes = b"metadata test"
        encoded = base64.b64encode(raw_bytes).decode()

        await client.post(
            "/v1/artifacts",
            json={
                "name": "meta.bin",
                "content_type": "application/octet-stream",
                "content_base64": encoded,
            },
            headers=_bearer(api_key_info["key"]),
        )

        resp = await client.get("/v1/artifacts", headers=_bearer(api_key_info["key"]))
        assert resp.status_code == 200
        body = resp.json()
        assert "data" in body
        assert "limit" in body
        assert "offset" in body
        assert len(body["data"]) >= 1

        item = body["data"][0]
        # Expected fields
        assert "id" in item
        assert "name" in item
        assert "content_type" in item
        assert "size_bytes" in item
        assert "created_at" in item
        # Content MUST NOT be returned in the list
        assert "content" not in item
        assert "content_base64" not in item


# ---------------------------------------------------------------------------
# test_tenant_isolation
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    async def test_tenant_isolation(
        self, client: Any, api_key_info: dict[str, str], other_tenant: dict[str, str]
    ) -> None:
        """TENANT ISOLATION: Tenant B cannot read or delete Tenant A's artifacts."""
        raw_bytes = b"isolated content"
        encoded = base64.b64encode(raw_bytes).decode()

        # Tenant A uploads
        create_resp = await client.post(
            "/v1/artifacts",
            json={
                "name": "isolated.bin",
                "content_type": "application/octet-stream",
                "content_base64": encoded,
            },
            headers=_bearer(api_key_info["key"]),
        )
        assert create_resp.status_code == 201
        artifact_id = create_resp.json()["id"]

        # Tenant B tries to download → 404
        dl = await client.get(
            f"/v1/artifacts/{artifact_id}",
            headers=_bearer(other_tenant["key"]),
        )
        assert dl.status_code == 404

        # Tenant B list → 0 items
        list_resp = await client.get("/v1/artifacts", headers=_bearer(other_tenant["key"]))
        assert list_resp.status_code == 200
        assert list_resp.json()["data"] == []

        # Tenant B tries to delete → 404
        del_resp = await client.delete(
            f"/v1/artifacts/{artifact_id}",
            headers=_bearer(other_tenant["key"]),
        )
        assert del_resp.status_code == 404

        # Tenant A's artifact remains intact
        dl2 = await client.get(
            f"/v1/artifacts/{artifact_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert dl2.status_code == 200
        assert dl2.content == raw_bytes


# ---------------------------------------------------------------------------
# test_over_cap_413
# ---------------------------------------------------------------------------


class TestOverCap413:
    async def test_over_cap_413(self, client: Any, app: Any, api_key_info: dict[str, str]) -> None:
        """413 when decoded size exceeds artifact_max_bytes."""
        # 9 bytes of content
        raw_bytes = b"123456789"
        encoded = base64.b64encode(raw_bytes).decode()

        # Install an 8-byte cap
        original = app.state.settings.artifact_max_bytes
        object.__setattr__(app.state.settings, "artifact_max_bytes", 8)
        try:
            resp = await client.post(
                "/v1/artifacts",
                json={
                    "name": "over-cap.bin",
                    "content_type": "application/octet-stream",
                    "content_base64": encoded,
                },
                headers=_bearer(api_key_info["key"]),
            )
            assert resp.status_code == 413, f"expected 413, got {resp.status_code}: {resp.text}"

            # No row created
            list_resp = await client.get("/v1/artifacts", headers=_bearer(api_key_info["key"]))
            assert list_resp.json()["data"] == []
        finally:
            object.__setattr__(app.state.settings, "artifact_max_bytes", original)


# ---------------------------------------------------------------------------
# test_download_forces_attachment
# ---------------------------------------------------------------------------


class TestDownloadForcesAttachment:
    async def test_download_forces_attachment(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        """Content-Disposition must be attachment even for text/html (XSS guard)."""
        raw_bytes = b"<h1>XSS attempt</h1>"
        encoded = base64.b64encode(raw_bytes).decode()

        create_resp = await client.post(
            "/v1/artifacts",
            json={"name": "page.html", "content_type": "text/html", "content_base64": encoded},
            headers=_bearer(api_key_info["key"]),
        )
        assert create_resp.status_code == 201
        artifact_id = create_resp.json()["id"]

        dl = await client.get(
            f"/v1/artifacts/{artifact_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert dl.status_code == 200
        cd = dl.headers["content-disposition"]
        assert cd.startswith("attachment"), f"Expected attachment, got: {cd}"


# ---------------------------------------------------------------------------
# test_soft_delete_hides
# ---------------------------------------------------------------------------


class TestSoftDeleteHides:
    async def test_soft_delete_hides(self, client: Any, api_key_info: dict[str, str]) -> None:
        raw_bytes = b"deletable content"
        encoded = base64.b64encode(raw_bytes).decode()

        # Upload
        create_resp = await client.post(
            "/v1/artifacts",
            json={"name": "delete-me.txt", "content_type": "text/plain", "content_base64": encoded},
            headers=_bearer(api_key_info["key"]),
        )
        assert create_resp.status_code == 201
        artifact_id = create_resp.json()["id"]

        # Delete → 204
        del_resp = await client.delete(
            f"/v1/artifacts/{artifact_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert del_resp.status_code == 204

        # List → not in list
        list_resp = await client.get("/v1/artifacts", headers=_bearer(api_key_info["key"]))
        ids = [item["id"] for item in list_resp.json()["data"]]
        assert artifact_id not in ids

        # Download → 404
        dl = await client.get(
            f"/v1/artifacts/{artifact_id}",
            headers=_bearer(api_key_info["key"]),
        )
        assert dl.status_code == 404


# ---------------------------------------------------------------------------
# test_auth_and_validation
# ---------------------------------------------------------------------------


class TestAuthAndValidation:
    async def test_no_bearer_returns_401(self, client: Any) -> None:
        raw_bytes = b"test"
        encoded = base64.b64encode(raw_bytes).decode()
        resp = await client.post(
            "/v1/artifacts",
            json={
                "name": "x.bin",
                "content_type": "application/octet-stream",
                "content_base64": encoded,
            },
        )
        assert resp.status_code == 401

    async def test_invalid_bearer_returns_401(self, client: Any) -> None:
        raw_bytes = b"test"
        encoded = base64.b64encode(raw_bytes).decode()
        resp = await client.post(
            "/v1/artifacts",
            json={
                "name": "x.bin",
                "content_type": "application/octet-stream",
                "content_base64": encoded,
            },
            headers={"Authorization": "Bearer sk-invalid"},
        )
        assert resp.status_code == 401

    async def test_expired_key_returns_401(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        """Expired (but not revoked) key must be rejected with 401."""
        raw_bytes = b"test"
        encoded = base64.b64encode(raw_bytes).decode()

        # Sanity: key works before expiry
        ok = await client.post(
            "/v1/artifacts",
            json={
                "name": "ok.bin",
                "content_type": "application/octet-stream",
                "content_base64": encoded,
            },
            headers=_bearer(api_key_info["key"]),
        )
        assert ok.status_code == 201

        # Force-expire in DB
        async with app.state.sessionmaker() as session:
            await session.execute(
                update(ApiKeyRow)
                .where(ApiKeyRow.id == uuid.UUID(api_key_info["key_id"]))
                .values(expires_at=datetime.now(tz=UTC) - timedelta(hours=1))
            )
            await session.commit()

        resp = await client.post(
            "/v1/artifacts",
            json={
                "name": "post-expiry.bin",
                "content_type": "application/octet-stream",
                "content_base64": encoded,
            },
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 401

    async def test_invalid_base64_returns_422(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        resp = await client.post(
            "/v1/artifacts",
            json={
                "name": "bad.bin",
                "content_type": "application/octet-stream",
                "content_base64": "!!!not-base64!!!",
            },
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 422

    async def test_empty_name_returns_422(self, client: Any, api_key_info: dict[str, str]) -> None:
        raw_bytes = b"test"
        encoded = base64.b64encode(raw_bytes).decode()
        resp = await client.post(
            "/v1/artifacts",
            json={
                "name": "   ",
                "content_type": "application/octet-stream",
                "content_base64": encoded,
            },
            headers=_bearer(api_key_info["key"]),
        )
        assert resp.status_code == 422

    async def test_no_row_created_on_auth_or_validation_failure(
        self, client: Any, api_key_info: dict[str, str]
    ) -> None:
        """401/422 failures must NOT create any artifact row."""
        # 401 case
        resp = await client.post(
            "/v1/artifacts",
            json={
                "name": "should-not-persist.bin",
                "content_type": "application/octet-stream",
                "content_base64": base64.b64encode(b"test").decode(),
            },
            headers={"Authorization": "Bearer sk-invalid"},
        )
        assert resp.status_code == 401

        # 422 case — invalid base64
        resp2 = await client.post(
            "/v1/artifacts",
            json={
                "name": "also-not-persist.bin",
                "content_type": "application/octet-stream",
                "content_base64": "!!!bad!!!",
            },
            headers=_bearer(api_key_info["key"]),
        )
        assert resp2.status_code == 422

        # List must be empty
        list_resp = await client.get("/v1/artifacts", headers=_bearer(api_key_info["key"]))
        assert list_resp.json()["data"] == []
