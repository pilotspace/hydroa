"""Red/green suite for v51 artifacts-s3-persistence.

Routes the live /v1/artifacts byte path through the ObjectStore port when one is
configured on app.state, with HONEST-DEGRADE to inline BYTEA when it is not.

CONTRACT (FROZEN @ v1 — Tin 2026-06-26):
  - upload (store configured): object FIRST (artifacts/{tenant}/{id}), then row
    (storage_backend='s3', content NULL); a put failure → 503, NO row.
  - upload (store None): inline BYTEA (exact v45).
  - download: s3 row → store.get; inline row → row.content. Same attachment Response.
      s3 object gone → 404; s3 row but store now None → 503 (not a 404 lie).
  - delete: soft-delete ONLY (deleted_at); s3 object LEFT for the sweep (no reap call).
  - tenant isolation byte-identical: object_key derived from the tenant-scoped row;
    cross-tenant id → 404 BEFORE any store call.

RED until the router/repo/ORM/migration are wired (the s3 paths still hit inline).
"""

from __future__ import annotations

import base64
import uuid
from typing import Any

import pytest
from sqlalchemy import select

from gateway.artifacts.infrastructure.orm import ArtifactRow
from gateway.objectstore.errors import ObjectNotFoundError, ObjectStoreUnavailableError

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers / fakes
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
    }


class FakeObjectStore:
    """In-memory ObjectStore with failure injection + call tracking."""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.fail_put = False
        self.fail_get = False
        self.missing_get = False
        self.put_calls: list[str] = []
        self.get_calls: list[str] = []
        self.delete_calls: list[str] = []

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.put_calls.append(key)
        if self.fail_put:
            raise ObjectStoreUnavailableError("injected put failure")
        self.objects[key] = (data, content_type)

    async def get(self, key: str) -> bytes:
        self.get_calls.append(key)
        if self.fail_get:
            raise ObjectStoreUnavailableError("injected get failure")
        if self.missing_get or key not in self.objects:
            raise ObjectNotFoundError(key)
        return self.objects[key][0]

    async def delete(self, key: str) -> None:
        self.delete_calls.append(key)
        self.objects.pop(key, None)

    async def health(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def api_key_info(client: Any) -> dict[str, str]:
    return await _signup_and_key(
        client, tenant_name="ArtS3", email="art-s3@example.io", password="art-s3-battery"
    )


@pytest.fixture
async def other_tenant(client: Any) -> dict[str, str]:
    return await _signup_and_key(
        client,
        tenant_name="ArtS3Other",
        email="art-s3-other@example.io",
        password="art-s3-other-bat",
    )


@pytest.fixture
def fake_store(app: Any) -> FakeObjectStore:
    """Install a fake ObjectStore on app.state (function-scoped app → no reset needed)."""
    store = FakeObjectStore()
    app.state.object_store = store
    return store


async def _upload(client: Any, key: str, *, name: str, ct: str, data: bytes) -> Any:
    return await client.post(
        "/v1/artifacts",
        json={"name": name, "content_type": ct, "content_base64": base64.b64encode(data).decode()},
        headers=_bearer(key),
    )


async def _get_row(app: Any, artifact_id: str) -> ArtifactRow | None:
    async with app.state.sessionmaker() as session:
        res = await session.execute(
            select(ArtifactRow).where(ArtifactRow.id == uuid.UUID(artifact_id))
        )
        return res.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Tests (one per scenario)
# ---------------------------------------------------------------------------


class TestS3Persistence:
    async def test_s3_upload_download_roundtrip(
        self, client: Any, app: Any, fake_store: FakeObjectStore, api_key_info: dict[str, str]
    ) -> None:
        data = b"\x00s3-bytes\xff"
        resp = await _upload(
            client, api_key_info["key"], name="r.bin", ct="application/pdf", data=data
        )
        assert resp.status_code == 201, resp.text
        artifact_id = resp.json()["id"]

        # round-trip returns exact bytes + content-type
        dl = await client.get(f"/v1/artifacts/{artifact_id}", headers=_bearer(api_key_info["key"]))
        assert dl.status_code == 200
        assert dl.content == data
        assert dl.headers["content-type"].startswith("application/pdf")

        # row routed to s3: content NULL, object_key set, bytes physically in the store
        row = await _get_row(app, artifact_id)
        assert row is not None
        assert row.storage_backend == "s3"
        assert row.content is None
        expected_key = f"artifacts/{api_key_info['tenant_id']}/{artifact_id}"
        assert row.object_key == expected_key
        assert fake_store.objects[expected_key][0] == data

    async def test_honest_degrade_inline_when_no_store(
        self, client: Any, app: Any, api_key_info: dict[str, str]
    ) -> None:
        app.state.object_store = None  # explicit: unconfigured
        data = b"inline-bytes"
        resp = await _upload(client, api_key_info["key"], name="i.txt", ct="text/plain", data=data)
        assert resp.status_code == 201, resp.text
        artifact_id = resp.json()["id"]

        dl = await client.get(f"/v1/artifacts/{artifact_id}", headers=_bearer(api_key_info["key"]))
        assert dl.status_code == 200
        assert dl.content == data

        row = await _get_row(app, artifact_id)
        assert row is not None
        assert row.storage_backend == "inline"
        assert row.content == data
        assert row.object_key is None

    async def test_existing_inline_row_downloads_with_store_present(
        self, client: Any, app: Any, fake_store: FakeObjectStore, api_key_info: dict[str, str]
    ) -> None:
        """A legacy inline row (storage_backend defaulted) serves from row.content; store untouched."""
        data = b"legacy-inline"
        async with app.state.sessionmaker() as session:
            row = ArtifactRow(
                tenant_id=uuid.UUID(api_key_info["tenant_id"]),
                key_id=uuid.UUID(api_key_info["key_id"]),
                name="legacy.bin",
                content_type="application/octet-stream",
                size_bytes=len(data),
                content=data,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            artifact_id = str(row.id)

        dl = await client.get(f"/v1/artifacts/{artifact_id}", headers=_bearer(api_key_info["key"]))
        assert dl.status_code == 200
        assert dl.content == data
        assert fake_store.get_calls == []  # served from the DB, never the store

    async def test_upload_store_failure_503_no_row(
        self, client: Any, app: Any, fake_store: FakeObjectStore, api_key_info: dict[str, str]
    ) -> None:
        fake_store.fail_put = True
        resp = await _upload(
            client, api_key_info["key"], name="x.bin", ct="application/octet-stream", data=b"x"
        )
        assert resp.status_code == 503, resp.text
        # object-first: a failed put writes NO row
        lst = await client.get("/v1/artifacts", headers=_bearer(api_key_info["key"]))
        assert lst.json()["data"] == []

    async def test_download_s3_object_missing_404(
        self, client: Any, app: Any, fake_store: FakeObjectStore, api_key_info: dict[str, str]
    ) -> None:
        resp = await _upload(
            client, api_key_info["key"], name="m.bin", ct="application/octet-stream", data=b"gone"
        )
        artifact_id = resp.json()["id"]
        fake_store.missing_get = True  # object vanished out from under the row
        dl = await client.get(f"/v1/artifacts/{artifact_id}", headers=_bearer(api_key_info["key"]))
        assert dl.status_code == 404

    async def test_download_s3_row_store_unconfigured_503(
        self, client: Any, app: Any, fake_store: FakeObjectStore, api_key_info: dict[str, str]
    ) -> None:
        resp = await _upload(
            client, api_key_info["key"], name="u.bin", ct="application/octet-stream", data=b"u"
        )
        artifact_id = resp.json()["id"]
        app.state.object_store = None  # store removed after the row was written as s3
        dl = await client.get(f"/v1/artifacts/{artifact_id}", headers=_bearer(api_key_info["key"]))
        assert dl.status_code == 503  # honest: exists but unreachable — NOT a 404

    async def test_delete_soft_only_leaves_object(
        self, client: Any, app: Any, fake_store: FakeObjectStore, api_key_info: dict[str, str]
    ) -> None:
        resp = await _upload(
            client, api_key_info["key"], name="d.bin", ct="application/octet-stream", data=b"d"
        )
        artifact_id = resp.json()["id"]

        dele = await client.delete(
            f"/v1/artifacts/{artifact_id}", headers=_bearer(api_key_info["key"])
        )
        assert dele.status_code == 204
        assert fake_store.delete_calls == []  # soft-only: object left for the sweep

        row = await _get_row(app, artifact_id)
        assert row is not None and row.deleted_at is not None
        dl = await client.get(f"/v1/artifacts/{artifact_id}", headers=_bearer(api_key_info["key"]))
        assert dl.status_code == 404  # soft-deleted

    async def test_cross_tenant_download_404_no_store_call(
        self,
        client: Any,
        app: Any,
        fake_store: FakeObjectStore,
        api_key_info: dict[str, str],
        other_tenant: dict[str, str],
    ) -> None:
        resp = await _upload(
            client, api_key_info["key"], name="t.bin", ct="application/octet-stream", data=b"secret"
        )
        artifact_id = resp.json()["id"]
        fake_store.get_calls.clear()
        dl = await client.get(f"/v1/artifacts/{artifact_id}", headers=_bearer(other_tenant["key"]))
        assert dl.status_code == 404
        assert (
            fake_store.get_calls == []
        )  # tenant-scoped row lookup returns None BEFORE any store call
