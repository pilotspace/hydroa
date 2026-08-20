"""Shared structural helpers for the `zdr-retention-inventory-extension` red suite.

Deliberately NOT a conftest: these are plain functions, imported by both test modules,
so the same definition of "the inventory the sweeper consumes" backs the structural
guard AND the behavioral sweep check. Two copies of that definition is exactly the
R:SECOND_INVENTORY defect this task exists to prevent.

DO NOT weaken anything in this module to make a test pass — that is Build's job.
"""

from __future__ import annotations

from typing import Any

# The ZDR purge inventory as it exists on the PRE-FIX tree: nine table names hardcoded
# by hand inside `_sweep_zdr_purge_pass` (usage/application/retention_sweep.py L992+),
# with no declared tuple anywhere. Recorded here VERBATIM so the metadata walk still has
# something to diff against before `ZDR_PURGE_TABLES` exists — the guard then fails
# naming the five victims (the contracted red) instead of erroring on an ImportError,
# which would be a red for the wrong reason.
#
# This constant is documentation of the pre-fix state, NOT a second inventory: once
# Build lands `ZDR_PURGE_TABLES`, `consumed_inventory()` stops reading it entirely and
# the identity assertion below binds the guard to the sweeper's own object.
PRE_FIX_HARDCODED_INVENTORY: tuple[str, ...] = (
    "artifacts",
    "conversations",
    "memories",
    "batch_job_items",
    "video_generation_jobs",
    "stored_responses",
    "files",
    "request_logs",
    "compliance_report_runs",
)

# The five payload-bearing tables the pre-fix pass silently misses (deep-review P0
# artifact 6816985f). Named here only so failure messages can say "expected exactly
# these" — the guard DERIVES its victim list from Base.metadata, never from this tuple.
EXPECTED_PRE_FIX_VICTIMS: frozenset[str] = frozenset(
    {
        "vector_store_chunks",
        "eval_cases",
        "eval_case_results",
        "finetune_jobs",
        "finetune_job_events",
    }
)

INVENTORY_TUPLE_POINTER = (
    "gateway/tenants/application/retention_policy.py :: ZDR_PURGE_TABLES "
    "(beside ALL_SWEPT_TABLES) — the ONE tuple `_sweep_zdr_purge_pass` builds its "
    "per-tenant DELETEs from (zdr-retention-inventory-extension TASK.md M2)"
)


def consumed_inventory() -> tuple[tuple[str, ...], str]:
    """The ZDR purge table set THE SWEEPER ITSELF CONSUMES, plus a human label for it.

    R:SECOND_INVENTORY — a guard that asserts against a list the sweeper does not read
    is a green guard over a dead tuple. So when `ZDR_PURGE_TABLES` exists this function
    refuses to return it unless `retention_sweep` reaches the SAME OBJECT: identity
    (`is`), not equality, because two equal-but-separate tuples is precisely the drift
    being guarded against.

    Pre-fix (no `ZDR_PURGE_TABLES` anywhere) it falls back to the documented
    `PRE_FIX_HARDCODED_INVENTORY` so the metadata walk still runs and the failure names
    the five victims — an ImportError here would be a red for the wrong reason.
    """
    from gateway.tenants.application import retention_policy
    from gateway.usage.application import retention_sweep

    declared = getattr(retention_policy, "ZDR_PURGE_TABLES", None)
    if declared is None:
        return PRE_FIX_HARDCODED_INVENTORY, "pre-fix hardcoded 9-table set (no ZDR_PURGE_TABLES)"

    reached = getattr(retention_sweep, "ZDR_PURGE_TABLES", None)
    assert reached is declared, (
        "R:SECOND_INVENTORY — retention_policy.ZDR_PURGE_TABLES exists but "
        "usage/application/retention_sweep.py does not reach that same object "
        f"(sweeper sees {reached!r}). The guard and the sweeper must read ONE tuple; "
        f"import it: {INVENTORY_TUPLE_POINTER}"
    )
    assert isinstance(declared, tuple), (
        f"ZDR_PURGE_TABLES must be a tuple of table names, got {type(declared).__name__}"
    )
    return declared, "retention_policy.ZDR_PURGE_TABLES"


def payload_shaped_census() -> dict[str, list[str]]:
    """Every Base.metadata table carrying `tenant_id` AND a payload-shaped column.

    Population rule (M3 / A11, structurally derived — never a hand-maintained list):
      tenant_id column AND at least one column typed Text / JSONB / Vector.

    A5: a table WITHOUT `tenant_id` is out of ZDR scope by construction (global/system
    rows — `tenants` itself lands here, which is why it needs no exemption row).

    Returns {table_name: [payload column names]} so a failure message can show WHY each
    victim qualified.
    """
    import importlib

    from pgvector.sqlalchemy import Vector
    from sqlalchemy import Text
    from sqlalchemy.dialects.postgresql import JSONB

    from gateway.core.db import Base

    # Side-effect import: gateway.main imports every module's ORM, which is what
    # registers all 63 tables on Base.metadata. Without it the walk sees a partial
    # schema and the guard silently under-reports (the anti-vacuity floor catches that).
    importlib.import_module("gateway.main")

    payload_types: tuple[Any, ...] = (Text, JSONB, Vector)
    census: dict[str, list[str]] = {}
    for name, table in Base.metadata.tables.items():
        if "tenant_id" not in table.c:
            continue
        hits = [c.name for c in table.c if isinstance(c.type, payload_types)]
        if hits:
            census[name] = hits
    return census
