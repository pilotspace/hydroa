"""Object-store seam: a swappable async byte store for artifact bytes.

Public surface:
  - ObjectStore                 — the Protocol the persistence layer depends on
  - S3ObjectStore               — the S3/MinIO adapter (design-for-failure)
  - build_object_store(settings)-> ObjectStore | None  (None = honest-degrade to inline)
  - ObjectNotFoundError / ObjectStoreUnavailableError
"""

from __future__ import annotations

from gateway.objectstore.errors import (
    ObjectNotFoundError,
    ObjectStoreError,
    ObjectStoreUnavailableError,
)
from gateway.objectstore.port import ObjectStore
from gateway.objectstore.s3 import S3ObjectStore, build_object_store

__all__ = [
    "ObjectNotFoundError",
    "ObjectStore",
    "ObjectStoreError",
    "ObjectStoreUnavailableError",
    "S3ObjectStore",
    "build_object_store",
]
