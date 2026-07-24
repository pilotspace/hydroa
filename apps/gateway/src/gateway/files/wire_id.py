"""OpenAI ``file-<hex>`` wire id <-> internal UUID (files-uploads-api PLAN.md §3).

The wire id is ``"file-" + uuid.hex`` (32 lowercase hex chars) — reversible, parsed
back on retrieve / content / delete and on the /v1/batches ``input_file_id`` reference.
Shared by the files router and the batches router so there is never a second, drifting
copy of the id encoding.
"""

from __future__ import annotations

import uuid

_PREFIX = "file-"


def to_wire_id(file_id: uuid.UUID) -> str:
    """Render an internal UUID as the OpenAI ``file-<hex>`` wire id."""
    return f"{_PREFIX}{file_id.hex}"


def parse_wire_id(wire_id: str) -> uuid.UUID | None:
    """Parse a ``file-<hex>`` wire id back to a UUID, or None if malformed.

    Returns None (never raises) for any input that is not exactly the ``file-``
    prefix followed by a valid 32-char hex UUID — the caller maps None to the same
    uniform rejection as an absent/cross-tenant file (no enumeration oracle).
    """
    if not wire_id.startswith(_PREFIX):
        return None
    raw = wire_id[len(_PREFIX) :]
    try:
        return uuid.UUID(hex=raw)
    except ValueError:
        return None
