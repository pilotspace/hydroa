"""Typed errors for the object-store seam.

ObjectNotFoundError      — a get/head for a key that does not exist (a 404, not an outage).
ObjectStoreUnavailableError — the store could not be reached or failed (timeout, transport,
                              5xx, breaker open). Maps to ERR_OBJECT_STORE_UNAVAILABLE (503).
"""

from __future__ import annotations


class ObjectStoreError(Exception):
    """Base class for all object-store errors."""


class ObjectNotFoundError(ObjectStoreError):
    """The requested object key does not exist (a 404 — NOT a store outage)."""


class ObjectStoreUnavailableError(ObjectStoreError):
    """The object store is unreachable or failed (timeout, transport, 5xx, breaker open)."""
