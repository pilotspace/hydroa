"""Wire ids for the evals domain (eval-set-store PLAN.md §3 — FROZEN @ v1).

Three reversible prefixes, mirroring ``gateway.vector_stores.wire_id``'s ``vs_<32hex>`` shape:
  - eval SET  -> ``es_<32hex>``
  - eval CASE -> ``ec_<32hex>``
  - eval RUN  -> ``er_<32hex>``  (eval-run-executor §3)

``parse_*`` returns None (never raises) for anything that is not exactly the prefix followed
by a valid 32-char hex UUID — the caller maps None to the same uniform 404 as an absent or
cross-tenant id (no enumeration oracle; M4/M5).
"""

from __future__ import annotations

import uuid

_SET_PREFIX = "es_"
_CASE_PREFIX = "ec_"
_RUN_PREFIX = "er_"


def to_set_wire_id(eval_set_id: uuid.UUID) -> str:
    return f"{_SET_PREFIX}{eval_set_id.hex}"


def parse_set_wire_id(wire_id: str) -> uuid.UUID | None:
    return _parse(wire_id, _SET_PREFIX)


def to_run_wire_id(eval_run_id: uuid.UUID) -> str:
    return f"{_RUN_PREFIX}{eval_run_id.hex}"


def parse_run_wire_id(wire_id: str) -> uuid.UUID | None:
    return _parse(wire_id, _RUN_PREFIX)


def to_case_wire_id(eval_case_id: uuid.UUID) -> str:
    return f"{_CASE_PREFIX}{eval_case_id.hex}"


def parse_case_wire_id(wire_id: str) -> uuid.UUID | None:
    return _parse(wire_id, _CASE_PREFIX)


def _parse(wire_id: str, prefix: str) -> uuid.UUID | None:
    if not wire_id.startswith(prefix):
        return None
    try:
        return uuid.UUID(hex=wire_id[len(prefix) :])
    except ValueError:
        return None
