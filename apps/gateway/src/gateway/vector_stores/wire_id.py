"""OpenAI ``vs_<32hex>`` wire id <-> internal UUID (vector-store-core PLAN.md §3).

The wire id is ``"vs_" + uuid.hex`` (32 lowercase hex chars) — reversible, parsed
back on retrieve/delete. Mirrors ``gateway.files.wire_id`` exactly (underscore
separator per the OpenAI vector-store id shape, vs the files ``file-`` hyphen).
"""

from __future__ import annotations

import uuid

_PREFIX = "vs_"


def to_wire_id(vector_store_id: uuid.UUID) -> str:
    """Render an internal UUID as the OpenAI ``vs_<32hex>`` wire id."""
    return f"{_PREFIX}{vector_store_id.hex}"


def parse_wire_id(wire_id: str) -> uuid.UUID | None:
    """Parse a ``vs_<32hex>`` wire id back to a UUID, or None if malformed.

    Returns None (never raises) for any input that is not exactly the ``vs_``
    prefix followed by a valid 32-char hex UUID — the caller maps None to the
    same uniform rejection as an absent/cross-tenant store (no enumeration
    oracle).
    """
    if not wire_id.startswith(_PREFIX):
        return None
    raw = wire_id[len(_PREFIX) :]
    try:
        return uuid.UUID(hex=raw)
    except ValueError:
        return None
