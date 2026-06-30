"""S3/MinIO ObjectStore adapter (design-for-failure).

Wraps an aioboto3 S3 client. The SDK owns the S3 protocol (signing, path-style
addressing); THIS adapter owns the failure policy — botocore's own retries are
DISABLED (``max_attempts=1``) so the policy is explicit here, not hidden:

  - explicit per-op connect+read timeout (``object_store_timeout_seconds``)
  - idempotent reads (get/health) retry <= ``object_store_max_retries`` on
    retryable botocore errors (endpoint/connect/read timeouts, 5xx ClientError)
  - mutations (put/delete) are AT-MOST-ONCE (never retried)
  - a per-instance CircuitBreaker guards every call; OPEN -> raise without a call
  - get/head 404 / NoSuchKey -> ObjectNotFoundError (NOT a breaker failure)

The aioboto3 client is created per operation via an injectable ``client_factory``
(a zero-arg callable returning an async context manager) so unit tests pass a fake.
"""

from __future__ import annotations

# aioboto3 / botocore ship no type stubs (no py.typed) — suppress the stub-missing
# noise for this file only; the wire surface is exercised by the live MinIO tests.
# pyright: reportMissingTypeStubs=false
from collections.abc import Awaitable, Callable
from typing import Any

from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from gateway.core.config import Settings
from gateway.objectstore.errors import ObjectNotFoundError, ObjectStoreUnavailableError
from gateway.objectstore.port import ObjectStore
from gateway.proxy.domain.errors import CircuitOpenError
from gateway.proxy.infrastructure.circuit_breaker import CircuitBreaker

# Retryable transport-level botocore exceptions (no bytes meaningfully committed).
_RETRYABLE_EXC = (EndpointConnectionError, ConnectTimeoutError, ReadTimeoutError)

_NOT_FOUND_CODES = frozenset({"NoSuchKey", "NoSuchBucket", "404", "NotFound"})

ClientFactory = Callable[[], Any]  # () -> async context manager yielding an S3 client


def _default_client_factory(settings: Settings) -> ClientFactory:
    """Build the production aioboto3 client factory (imported lazily)."""
    import aioboto3

    session = aioboto3.Session()
    secret = settings.object_store_secret_access_key.get_secret_value()
    config = Config(
        connect_timeout=settings.object_store_timeout_seconds,
        read_timeout=settings.object_store_timeout_seconds,
        retries={"max_attempts": 1},  # botocore retries OFF — the policy is ours
        s3={"addressing_style": "path"},
    )

    def factory() -> Any:
        return session.client(
            "s3",
            endpoint_url=settings.object_store_endpoint,
            aws_access_key_id=settings.object_store_access_key_id,
            aws_secret_access_key=secret,
            region_name=settings.object_store_region,
            config=config,
        )

    return factory


class S3ObjectStore:
    """An ObjectStore backed by an S3-compatible store (MinIO / AWS S3)."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: ClientFactory | None = None,
        breaker: CircuitBreaker | None = None,
    ) -> None:
        self._bucket = settings.object_store_bucket
        self._max_retries = settings.object_store_max_retries
        self._client_factory = client_factory or _default_client_factory(settings)
        self._breaker = breaker or CircuitBreaker()

    def __repr__(self) -> str:  # never leak the secret
        return f"S3ObjectStore(bucket={self._bucket!r})"

    @staticmethod
    def _is_not_found(exc: ClientError) -> bool:
        err = exc.response.get("Error", {}) if exc.response else {}
        meta = exc.response.get("ResponseMetadata", {}) if exc.response else {}
        return err.get("Code") in _NOT_FOUND_CODES or meta.get("HTTPStatusCode") == 404

    def _guard(self) -> None:
        try:
            self._breaker.guard()
        except CircuitOpenError as exc:
            raise ObjectStoreUnavailableError("object store circuit is open") from exc

    async def _read(self, op: Callable[[Any], Awaitable[Any]]) -> Any:
        """Run an idempotent op with bounded retry + breaker. NotFound is not a failure."""
        last_exc: BaseException | None = None
        for _ in range(self._max_retries + 1):
            self._guard()
            try:
                async with self._client_factory() as client:
                    result = await op(client)
            except ClientError as exc:
                if self._is_not_found(exc):
                    raise ObjectNotFoundError(str(exc)) from exc
                self._breaker.on_upstream_error()
                meta = exc.response.get("ResponseMetadata", {}) if exc.response else {}
                status = meta.get("HTTPStatusCode", 0)
                if status and status < 500:
                    raise ObjectStoreUnavailableError(str(exc)) from exc
                last_exc = exc  # 5xx → retry
            except _RETRYABLE_EXC as exc:
                self._breaker.on_upstream_error()
                last_exc = exc
            else:
                self._breaker.record_success()
                return result
        raise ObjectStoreUnavailableError(str(last_exc)) from last_exc

    async def _mutate(self, op: Callable[[Any], Awaitable[Any]]) -> Any:
        """Run a mutating op AT-MOST-ONCE (never retried) with breaker."""
        self._guard()
        try:
            async with self._client_factory() as client:
                result = await op(client)
        except (ClientError, *_RETRYABLE_EXC) as exc:
            self._breaker.on_upstream_error()
            raise ObjectStoreUnavailableError(str(exc)) from exc
        self._breaker.record_success()
        return result

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        async def op(client: Any) -> Any:
            return await client.put_object(
                Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
            )

        await self._mutate(op)

    async def get(self, key: str) -> bytes:
        async def op(client: Any) -> bytes:
            resp = await client.get_object(Bucket=self._bucket, Key=key)
            return await resp["Body"].read()

        return await self._read(op)

    async def delete(self, key: str) -> None:
        async def op(client: Any) -> Any:
            return await client.delete_object(Bucket=self._bucket, Key=key)

        await self._mutate(op)

    async def health(self) -> bool:
        async def op(client: Any) -> Any:
            return await client.head_bucket(Bucket=self._bucket)

        try:
            await self._read(op)
            return True
        except (ObjectStoreUnavailableError, ObjectNotFoundError):
            return False


def build_object_store(settings: Settings) -> ObjectStore | None:
    """Return a configured ObjectStore, or None when the store is not fully configured.

    None is the HONEST-DEGRADE signal: the persistence task falls back to inline
    Postgres storage (the v45 behavior) when no object store is wired.
    """
    if not (
        settings.object_store_enabled
        and settings.object_store_endpoint
        and settings.object_store_bucket
        and settings.object_store_access_key_id
        and settings.object_store_secret_access_key.get_secret_value()
    ):
        return None
    return S3ObjectStore(settings)
