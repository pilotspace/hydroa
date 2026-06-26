"""Live MinIO round-trip tests for the S3 adapter (v51 object-store-port).

Skip-gated: these run ONLY when GATEWAY_OBJECT_STORE_ENDPOINT is set (the dev
compose MinIO). They exercise the REAL aioboto3 wire — signing, path-style
addressing, and the object lifecycle — proving the adapter against real S3.
In `make test-fast` (no MinIO) they are skipped, not failed.

The full end-to-end proof lives in the artifacts-s3-live-verify task; these are
the adapter-level live tests.
"""

from __future__ import annotations

import os

import pytest

from gateway.core.config import Settings
from gateway.objectstore import build_object_store
from gateway.objectstore.errors import ObjectNotFoundError

pytestmark = pytest.mark.skipif(
    not os.getenv("GATEWAY_OBJECT_STORE_ENDPOINT"),
    reason="live MinIO not configured (set GATEWAY_OBJECT_STORE_ENDPOINT)",
)


def _live_settings() -> Settings:
    return Settings(
        object_store_enabled=True,
        object_store_endpoint=os.environ["GATEWAY_OBJECT_STORE_ENDPOINT"],
        object_store_bucket=os.getenv("GATEWAY_OBJECT_STORE_BUCKET", "artifacts"),
        object_store_region=os.getenv("GATEWAY_OBJECT_STORE_REGION", "us-east-1"),
        object_store_access_key_id=os.getenv("GATEWAY_OBJECT_STORE_ACCESS_KEY_ID", "minioadmin"),
        object_store_secret_access_key=os.getenv(
            "GATEWAY_OBJECT_STORE_SECRET_ACCESS_KEY", "minioadmin"
        ),
    )


async def test_live_put_get_roundtrip_exact_bytes() -> None:
    store = build_object_store(_live_settings())
    assert store is not None
    key = "artifacts/live-test/roundtrip"
    payload = b"\x00\x01live\xff bytes"
    await store.put(key, payload, "application/octet-stream")
    assert await store.get(key) == payload
    await store.delete(key)


async def test_live_delete_removes_then_get_not_found() -> None:
    store = build_object_store(_live_settings())
    assert store is not None
    key = "artifacts/live-test/delete-me"
    await store.put(key, b"x", "text/plain")
    await store.delete(key)
    with pytest.raises(ObjectNotFoundError):
        await store.get(key)


async def test_live_delete_absent_is_noop() -> None:
    store = build_object_store(_live_settings())
    assert store is not None
    await store.delete("artifacts/live-test/never-existed")  # must not raise


async def test_live_health_true_when_reachable() -> None:
    store = build_object_store(_live_settings())
    assert store is not None
    assert await store.health() is True
