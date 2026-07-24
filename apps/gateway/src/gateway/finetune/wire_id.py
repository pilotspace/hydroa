"""OpenAI ``ftjob-<hex>`` / ``ftevent-<hex>`` wire ids <-> internal UUID.

Mirrors ``gateway.files.wire_id`` exactly (files-uploads-api PLAN.md §3 precedent) —
same reversible ``prefix + uuid.hex`` scheme, same "malformed -> None, never raises"
contract so callers can fold a bad id into the SAME uniform 404 as an unknown/
cross-tenant id (finetune-broker PLAN.md §3 T3 — no enumeration oracle).
"""

from __future__ import annotations

import uuid

_JOB_PREFIX = "ftjob-"
_EVENT_PREFIX = "ftevent-"


def to_job_wire_id(job_id: uuid.UUID) -> str:
    """Render an internal job UUID as the OpenAI ``ftjob-<hex>`` wire id."""
    return f"{_JOB_PREFIX}{job_id.hex}"


def parse_job_wire_id(wire_id: str) -> uuid.UUID | None:
    """Parse a ``ftjob-<hex>`` wire id back to a UUID, or None if malformed.

    Never raises — the caller folds None into the same 404 as an unknown or
    cross-tenant job id (byte-identical, no oracle).
    """
    if not wire_id.startswith(_JOB_PREFIX):
        return None
    raw = wire_id[len(_JOB_PREFIX) :]
    try:
        return uuid.UUID(hex=raw)
    except ValueError:
        return None


def to_event_wire_id(event_id: uuid.UUID) -> str:
    """Render an internal event UUID as the OpenAI ``ftevent-<hex>`` wire id."""
    return f"{_EVENT_PREFIX}{event_id.hex}"
