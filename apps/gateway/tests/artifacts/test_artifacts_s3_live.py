"""Live MinIO round-trip verification of the /v1/artifacts s3 path (v51 task 3).

Skip-gated on GATEWAY_OBJECT_STORE_ENDPOINT (the dev-compose MinIO). When run it
drives the FULL HTTP path (signup→key→POST→GET→DELETE) against a REAL S3ObjectStore
— proving the v51 wiring end-to-end, not just against FakeObjectStore doubles. In
`make test-fast` / CI (no MinIO) these are SKIPPED, never failed.

Prereq: the MinIO `artifacts` bucket must exist (the minio-createbucket bootstrap):
  docker compose -f infra/docker-compose.dev.yml up -d
"""

from __future__ import annotations

import base64
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from gateway.artifacts.infrastructure.orm import ArtifactRow
from gateway.core.config import Settings
from gateway.core.db import Base
from gateway.main import create_app
from gateway.objectstore import build_object_store
from gateway.objectstore.errors import ObjectNotFoundError
from tests.conftest import TEST_DATABASE_URL, TEST_JWT_SECRET

pytestmark = pytest.mark.skipif(
    not os.getenv("GATEWAY_OBJECT_STORE_ENDPOINT"),
    reason="live MinIO not configured (set GATEWAY_OBJECT_STORE_ENDPOINT)",
)


def _live_settings() -> Settings:
    return Settings(
        database_url=TEST_DATABASE_URL,
        jwt_secret=TEST_JWT_SECRET,
        redis_url="redis://localhost:6380/9",
        object_store_enabled=True,
        object_store_endpoint=os.environ["GATEWAY_OBJECT_STORE_ENDPOINT"],
        object_store_bucket=os.getenv("GATEWAY_OBJECT_STORE_BUCKET", "artifacts"),
        object_store_region=os.getenv("GATEWAY_OBJECT_STORE_REGION", "us-east-1"),
        object_store_access_key_id=os.getenv("GATEWAY_OBJECT_STORE_ACCESS_KEY_ID", "minioadmin"),
        object_store_secret_access_key=SecretStr(
            os.getenv("GATEWAY_OBJECT_STORE_SECRET_ACCESS_KEY", "minioadmin")
        ),
    )


@pytest.fixture
async def live_app() -> AsyncIterator[Any]:
    """A real-store app on the test Postgres (schema reset per test)."""
    settings = _live_settings()
    app = create_app(settings)
    engine = app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield app
    await engine.dispose()


@pytest.fixture
async def live_client(live_app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=live_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


async def _signup_and_key(client: httpx.AsyncClient) -> dict[str, str]:
    email = f"art-live-{uuid.uuid4().hex[:8]}@example.io"
    signup = await client.post(
        "/admin/auth/signup",
        json={"tenant_name": "ArtLive", "email": email, "password": "art-live-battery"},
    )
    assert signup.status_code == 201, signup.text
    tenant_id = signup.json()["tenant_id"]
    token = (
        await client.post(
            "/admin/auth/login", json={"email": email, "password": "art-live-battery"}
        )
    ).json()["access_token"]
    created = await client.post(
        "/admin/keys", json={"name": "ci-key-live"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert created.status_code == 201, created.text
    return {"key": created.json()["key"], "tenant_id": tenant_id}


async def test_live_upload_download_roundtrip(
    live_app: Any, live_client: httpx.AsyncClient
) -> None:
    # the app wired a REAL S3 store
    assert build_object_store(_live_settings()) is not None
    assert live_app.state.object_store is not None

    info = await _signup_and_key(live_client)
    data = b"\x00live-artifact\xff bytes \x10"

    resp = await live_client.post(
        "/v1/artifacts",
        json={
            "name": "live.bin",
            "content_type": "application/pdf",
            "content_base64": base64.b64encode(data).decode(),
        },
        headers=_bearer(info["key"]),
    )
    assert resp.status_code == 201, resp.text
    artifact_id = resp.json()["id"]

    # exact bytes round-trip through real MinIO
    dl = await live_client.get(f"/v1/artifacts/{artifact_id}", headers=_bearer(info["key"]))
    assert dl.status_code == 200
    assert dl.content == data
    assert dl.headers["content-type"].startswith("application/pdf")
    assert "attachment" in dl.headers["content-disposition"]

    # DB row routed to s3, content NULL
    expected_key = f"artifacts/{info['tenant_id']}/{artifact_id}"
    async with live_app.state.sessionmaker() as session:
        row = (
            await session.execute(
                select(ArtifactRow).where(ArtifactRow.id == uuid.UUID(artifact_id))
            )
        ).scalar_one()
        assert row.storage_backend == "s3"
        assert row.content is None
        assert row.object_key == expected_key

    # the object physically exists in MinIO at the tenant-scoped key
    store = build_object_store(_live_settings())
    assert store is not None
    assert await store.get(expected_key) == data
    await store.delete(expected_key)  # cleanup


async def test_live_delete_soft_only_leaves_object(
    live_app: Any, live_client: httpx.AsyncClient
) -> None:
    info = await _signup_and_key(live_client)
    data = b"delete-leaves-object"
    resp = await live_client.post(
        "/v1/artifacts",
        json={
            "name": "d.bin",
            "content_type": "application/octet-stream",
            "content_base64": base64.b64encode(data).decode(),
        },
        headers=_bearer(info["key"]),
    )
    artifact_id = resp.json()["id"]
    expected_key = f"artifacts/{info['tenant_id']}/{artifact_id}"

    dele = await live_client.delete(f"/v1/artifacts/{artifact_id}", headers=_bearer(info["key"]))
    assert dele.status_code == 204
    # row hidden …
    gone = await live_client.get(f"/v1/artifacts/{artifact_id}", headers=_bearer(info["key"]))
    assert gone.status_code == 404
    # … but the MinIO object SURVIVES (soft-only; reaped later by the sweep)
    store = build_object_store(_live_settings())
    assert store is not None
    assert await store.get(expected_key) == data
    await store.delete(expected_key)  # cleanup
    with pytest.raises(ObjectNotFoundError):
        await store.get(expected_key)
