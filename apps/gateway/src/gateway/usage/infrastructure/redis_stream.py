"""Thin async wrapper around redis.asyncio stream operations.

Handles:
  - stream_id_to_uuid: convert Redis auto-generated stream id to UUID
  - Stream key / consumer group constants
"""

from __future__ import annotations

import uuid

# Stream key and consumer group name (TASK.md §1 assumptions — configuration constants)
STREAM_KEY: str = "usage:events"
CONSUMER_GROUP: str = "ledger-flusher"
CONSUMER_NAME: str = "flusher-0"


def stream_id_to_uuid(stream_id: str | bytes) -> uuid.UUID:
    """Convert a Redis stream id (e.g. '1718000000000-0') to a stable UUID.

    Strategy: SHA-based namespace UUID (UUID5) using the stream id string as
    the name under the DNS namespace.  This produces a deterministic, collision-
    resistant UUID that is the same on every call for a given stream id, which
    is the idempotency key for the Postgres ON CONFLICT DO NOTHING guard.
    """
    if isinstance(stream_id, bytes):
        stream_id = stream_id.decode("utf-8")
    return uuid.uuid5(uuid.NAMESPACE_DNS, f"usage-event:{stream_id}")
