"""Red suite for the ObjectStore port + S3/MinIO adapter (v51 object-store-port).

All tests are RED until `gateway.objectstore` is implemented (ModuleNotFoundError).

CONTRACT (FROZEN @ v1 — Tin 2026-06-26):
  Port  gateway.objectstore.ObjectStore (Protocol, async): put / get / delete / health
  Adapter  gateway.objectstore.S3ObjectStore(settings, *, client_factory=None, breaker=None)
    - put/get/delete/health over an injected aioboto3-shaped async-ctx S3 client
    - botocore's own retries are OFF; the adapter owns the policy:
        reads (get/health) retry <= object_store_max_retries on
        EndpointConnectionError | ConnectTimeoutError | ReadTimeoutError | ClientError(5xx);
        mutations (put/delete) are AT-MOST-ONCE.
    - a per-instance CircuitBreaker guards every call; OPEN -> raise without touching the client.
    - get/head 404/NoSuchKey -> ObjectNotFoundError (NOT a breaker failure).
  Factory  build_object_store(settings) -> ObjectStore | None  (None unless fully configured)
  Errors   ObjectNotFoundError, ObjectStoreUnavailableError
"""

from __future__ import annotations

import pytest
from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from gateway.core.config import Settings
from gateway.objectstore import ObjectStore, S3ObjectStore, build_object_store
from gateway.objectstore.errors import ObjectNotFoundError, ObjectStoreUnavailableError
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

SECRET = "super-secret-minio-key"


# ---------------------------------------------------------------------------
# Helpers / fakes
# ---------------------------------------------------------------------------


def _client_error(status: int, code: str, op: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        op,
    )


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self) -> bytes:
        return self._data


class FakeS3Client:
    """A minimal stand-in for an aioboto3 S3 client.

    Per-method behavior queues hold either an Exception (raised) or a sentinel
    (proceed normally). Tracks call counts so tests can assert retry/at-most-once.
    """

    def __init__(
        self,
        *,
        get_seq: list | None = None,
        put_seq: list | None = None,
        head_seq: list | None = None,
    ) -> None:
        self.calls = {"put_object": 0, "get_object": 0, "delete_object": 0, "head_bucket": 0}
        self.store: dict[str, tuple[bytes, str]] = {}
        self.last_put: dict | None = None
        self._get_seq = list(get_seq or [])
        self._put_seq = list(put_seq or [])
        self._head_seq = list(head_seq or [])

    async def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str):
        self.calls["put_object"] += 1
        if self._put_seq:
            beh = self._put_seq.pop(0)
            if isinstance(beh, Exception):
                raise beh
        self.store[Key] = (Body, ContentType)
        self.last_put = {"Bucket": Bucket, "Key": Key, "Body": Body, "ContentType": ContentType}
        return {}

    async def get_object(self, *, Bucket: str, Key: str):
        self.calls["get_object"] += 1
        if self._get_seq:
            beh = self._get_seq.pop(0)
            if isinstance(beh, Exception):
                raise beh
        if Key not in self.store:
            raise _client_error(404, "NoSuchKey", "GetObject")
        body, ct = self.store[Key]
        return {"Body": _FakeBody(body), "ContentType": ct}

    async def delete_object(self, *, Bucket: str, Key: str):
        self.calls["delete_object"] += 1
        self.store.pop(Key, None)  # idempotent — absent key is a no-op
        return {}

    async def head_bucket(self, *, Bucket: str):
        self.calls["head_bucket"] += 1
        if self._head_seq:
            beh = self._head_seq.pop(0)
            if isinstance(beh, Exception):
                raise beh
        return {}


class FakeFactory:
    """Zero-arg callable returning an async-ctx wrapper around one FakeS3Client."""

    def __init__(self, client: FakeS3Client) -> None:
        self.client = client
        self.open_count = 0

    def __call__(self) -> FakeFactory:
        self.open_count += 1
        return self

    async def __aenter__(self) -> FakeS3Client:
        return self.client

    async def __aexit__(self, *exc) -> bool:
        return False


def _settings(**over) -> Settings:
    base = dict(
        object_store_enabled=True,
        object_store_endpoint="http://localhost:9000",
        object_store_bucket="artifacts",
        object_store_region="us-east-1",
        object_store_access_key_id="minio",
        object_store_secret_access_key=SECRET,
        object_store_timeout_seconds=5.0,
        object_store_max_retries=2,
    )
    base.update(over)
    return Settings(**base)


def _store(client: FakeS3Client, **over) -> tuple[S3ObjectStore, FakeFactory, CircuitBreaker]:
    factory = FakeFactory(client)
    breaker = over.pop("breaker", None) or CircuitBreaker()
    store = S3ObjectStore(_settings(**over), client_factory=factory, breaker=breaker)
    return store, factory, breaker


# ---------------------------------------------------------------------------
# Tests (one per scenario)
# ---------------------------------------------------------------------------


def test_port_protocol_is_runtime_shape() -> None:
    assert hasattr(ObjectStore, "__mro__") or hasattr(ObjectStore, "_is_protocol")


async def test_put_then_get_roundtrips_exact_bytes() -> None:
    client = FakeS3Client()
    store, _, _ = _store(client)
    data = b"\x00bytes\xff\x10"
    await store.put("artifacts/t/a", data, "application/pdf")
    assert await store.get("artifacts/t/a") == data


async def test_put_passes_body_and_content_type() -> None:
    client = FakeS3Client()
    store, _, _ = _store(client)
    await store.put("artifacts/t/a", b"hi", "text/plain")
    assert client.last_put["Body"] == b"hi"
    assert client.last_put["ContentType"] == "text/plain"
    assert client.last_put["Bucket"] == "artifacts"
    assert client.last_put["Key"] == "artifacts/t/a"


async def test_get_missing_key_raises_not_found() -> None:
    client = FakeS3Client()
    store, _, breaker = _store(client)
    with pytest.raises(ObjectNotFoundError):
        await store.get("missing")
    # a 404 is not an outage — the breaker must not be tripped toward OPEN
    assert breaker.call_allowed() is True


async def test_timeout_surfaces_unavailable() -> None:
    client = FakeS3Client(get_seq=[ReadTimeoutError(endpoint_url="http://localhost:9000")])
    store, _, _ = _store(client, object_store_max_retries=0)
    with pytest.raises(ObjectStoreUnavailableError):
        await store.get("k")


async def test_read_retries_then_succeeds() -> None:
    client = FakeS3Client(get_seq=[EndpointConnectionError(endpoint_url="http://localhost:9000")])
    client.store["k"] = (b"ok", "text/plain")
    store, _, _ = _store(client, object_store_max_retries=2)
    assert await store.get("k") == b"ok"
    assert client.calls["get_object"] == 2  # one failure + one success


async def test_mutation_not_retried() -> None:
    client = FakeS3Client(put_seq=[EndpointConnectionError(endpoint_url="http://localhost:9000")])
    store, _, _ = _store(client, object_store_max_retries=5)
    with pytest.raises(ObjectStoreUnavailableError):
        await store.put("k", b"data", "application/octet-stream")
    assert client.calls["put_object"] == 1  # at-most-once: no blind retry of a mutation


async def test_open_breaker_rejects_without_client() -> None:
    client = FakeS3Client()
    tripped = CircuitBreaker()
    for _ in range(10):
        tripped.record_failure()
    assert tripped.is_open() is True
    store, factory, _ = _store(client, breaker=tripped)
    with pytest.raises(ObjectStoreUnavailableError):
        await store.get("k")
    assert factory.open_count == 0  # never touched the network


async def test_delete_absent_is_noop() -> None:
    client = FakeS3Client()
    store, _, _ = _store(client)
    await store.delete("nope")  # must not raise
    assert client.calls["delete_object"] == 1


async def test_delete_removes_then_get_not_found() -> None:
    client = FakeS3Client()
    store, _, _ = _store(client)
    await store.put("k", b"x", "text/plain")
    await store.delete("k")
    with pytest.raises(ObjectNotFoundError):
        await store.get("k")


async def test_health_false_on_error_never_raises() -> None:
    client = FakeS3Client(head_seq=[EndpointConnectionError(endpoint_url="http://localhost:9000")])
    store, _, _ = _store(client, object_store_max_retries=0)
    assert await store.health() is False


async def test_health_true_when_reachable() -> None:
    client = FakeS3Client()
    store, _, _ = _store(client)
    assert await store.health() is True


def test_factory_none_when_unconfigured() -> None:
    assert build_object_store(_settings(object_store_enabled=False)) is None
    assert build_object_store(_settings(object_store_bucket="")) is None
    assert build_object_store(_settings(object_store_endpoint="")) is None


def test_factory_builds_when_configured() -> None:
    store = build_object_store(_settings())
    assert isinstance(store, S3ObjectStore)


def test_secret_not_in_repr() -> None:
    store = build_object_store(_settings())
    assert SECRET not in repr(store)
