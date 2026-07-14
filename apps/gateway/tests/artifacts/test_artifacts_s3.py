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
        self.fail_delete = False
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
        if self.fail_delete:
            raise ObjectStoreUnavailableError("injected delete failure")
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

    async def test_delete_purges_object_at_router_layer(
        self, client: Any, app: Any, fake_store: FakeObjectStore, api_key_info: dict[str, str]
    ) -> None:
        """audit-remediation: DELETE /v1/artifacts/{id} now PURGES the s3 object at the
        router layer. `_get_repo` injects the request's ObjectStore into
        ArtifactRepository, so soft_delete() sets deleted_at, clears inline BYTEA, AND
        calls ObjectStore.delete(object_key). (Was `test_delete_soft_only_leaves_object`,
        which documented the pre-fix wiring gap where the object was left behind.)"""
        resp = await _upload(
            client, api_key_info["key"], name="d.bin", ct="application/octet-stream", data=b"d"
        )
        artifact_id = resp.json()["id"]

        dele = await client.delete(
            f"/v1/artifacts/{artifact_id}", headers=_bearer(api_key_info["key"])
        )
        assert dele.status_code == 204
        # the object's bytes are now purged from the store, not left dangling
        assert len(fake_store.delete_calls) == 1

        row = await _get_row(app, artifact_id)
        assert row is not None and row.deleted_at is not None
        dl = await client.get(f"/v1/artifacts/{artifact_id}", headers=_bearer(api_key_info["key"]))
        assert dl.status_code == 404  # soft-deleted


class TestSoftDeletePurgesBytes:
    """MED remediation, repository layer: ArtifactRepository.soft_delete() must best-
    effort purge the underlying bytes (s3 object + inline BYTEA), not merely flip
    deleted_at. Exercised directly against the repository (bypassing the router, whose
    own object_store wiring is a separate, out-of-scope follow-up — see the docstring
    on test_delete_soft_only_leaves_object above)."""

    async def test_soft_delete_purges_s3_object_and_clears_inline_content(
        self, app: Any, db_session: Any, api_key_info: dict[str, str]
    ) -> None:
        from gateway.artifacts.infrastructure.repository import ArtifactRepository

        store = FakeObjectStore()
        object_key = f"artifacts/{api_key_info['tenant_id']}/purge-me"
        await store.put(object_key, b"secret-bytes", "application/octet-stream")

        repo = ArtifactRepository(db_session, object_store=store)
        row = await repo.create(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(api_key_info["tenant_id"]),
            key_id=uuid.UUID(api_key_info["key_id"]),
            name="s3.bin",
            content_type="application/octet-stream",
            size_bytes=12,
            storage_backend="s3",
            object_key=object_key,
            content=None,
        )
        await db_session.commit()

        deleted = await repo.soft_delete(
            tenant_id=uuid.UUID(api_key_info["tenant_id"]), artifact_id=row.id
        )
        await db_session.commit()

        assert deleted is True
        assert object_key in store.delete_calls, (
            "soft_delete must call ObjectStore.delete() for an s3-backed artifact"
        )
        assert object_key not in store.objects, "the object bytes must actually be gone"

        persisted = await _get_row(app, str(row.id))
        assert persisted is not None
        assert persisted.deleted_at is not None

    async def test_soft_delete_clears_inline_content_bytes(
        self, app: Any, db_session: Any, api_key_info: dict[str, str]
    ) -> None:
        from gateway.artifacts.infrastructure.repository import ArtifactRepository

        repo = ArtifactRepository(db_session)  # no object_store — inline path only
        row = await repo.create(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(api_key_info["tenant_id"]),
            key_id=uuid.UUID(api_key_info["key_id"]),
            name="inline.bin",
            content_type="application/octet-stream",
            size_bytes=5,
            storage_backend="inline",
            object_key=None,
            content=b"abcde",
        )
        await db_session.commit()

        deleted = await repo.soft_delete(
            tenant_id=uuid.UUID(api_key_info["tenant_id"]), artifact_id=row.id
        )
        await db_session.commit()
        assert deleted is True

        persisted = await _get_row(app, str(row.id))
        assert persisted is not None
        assert persisted.deleted_at is not None
        assert persisted.content is None, (
            "the inline BYTEA content must be cleared on soft_delete, not merely"
            " hidden behind deleted_at"
        )

    async def test_soft_delete_object_store_failure_still_soft_deletes(
        self, app: Any, db_session: Any, api_key_info: dict[str, str]
    ) -> None:
        """Design for failure: an ObjectStoreUnavailableError during the best-effort
        purge must NEVER block or roll back the DB soft-delete."""
        from gateway.artifacts.infrastructure.repository import ArtifactRepository

        store = FakeObjectStore()
        object_key = f"artifacts/{api_key_info['tenant_id']}/unreachable"
        await store.put(object_key, b"x", "application/octet-stream")
        store.fail_delete = True

        repo = ArtifactRepository(db_session, object_store=store)
        row = await repo.create(
            id=uuid.uuid4(),
            tenant_id=uuid.UUID(api_key_info["tenant_id"]),
            key_id=uuid.UUID(api_key_info["key_id"]),
            name="s3.bin",
            content_type="application/octet-stream",
            size_bytes=1,
            storage_backend="s3",
            object_key=object_key,
            content=None,
        )
        await db_session.commit()

        deleted = await repo.soft_delete(
            tenant_id=uuid.UUID(api_key_info["tenant_id"]), artifact_id=row.id
        )
        await db_session.commit()

        assert deleted is True, (
            "the DB soft-delete must still succeed even though the object-store"
            " delete failed"
        )
        persisted = await _get_row(app, str(row.id))
        assert persisted is not None and persisted.deleted_at is not None

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
