"""The ObjectStore port — the seam artifacts persistence depends on.

A minimal async byte store keyed by an opaque string. The S3/MinIO adapter
(`gateway.objectstore.s3.S3ObjectStore`) is the production implementation; the
persistence task consumes ONLY this Protocol so storage is swappable.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ObjectStore(Protocol):
    """A tenant-agnostic async object byte store (keys are caller-scoped)."""

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        """Store ``data`` at ``key`` with ``content_type`` (overwrite-safe). At-most-once."""
        ...

    async def get(self, key: str) -> bytes:
        """Return the exact bytes at ``key``.

        Raises ObjectNotFoundError if absent, ObjectStoreUnavailableError on failure.
        """
        ...

    async def delete(self, key: str) -> None:
        """Remove ``key`` (idempotent — absent is a no-op). At-most-once."""
        ...

    async def health(self) -> bool:
        """True iff the store is reachable. Never raises."""
        ...
