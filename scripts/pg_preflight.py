#!/usr/bin/env python3
"""Collation-lineage preflight for a pgvector-bearing deploy.

Why this exists (pgvector-deploy-runbook PLAN.md §1, FROZEN @ v2):

R5 moved the Postgres image from `postgres:16-alpine` (musl) to
`pgvector/pgvector:pg16` (Debian, glibc). A data volume initdb'd under musl and then
served by glibc keeps text/varchar btree indexes in musl collation order while every
query sorts and compares under glibc. The result is wrong `ORDER BY` and range-scan
answers, and unique constraints that stop detecting duplicates — not a crash.

Postgres warns about a collation-version mismatch only when it has a RECORDED version
to compare against. Databases created under musl have `datcollversion` SQL NULL, so
there is no warning at all. That silence is the whole reason this command exists:
without it, "did we check?" has no answer an operator can produce.

Exit codes — the runbook keys off these, not off the text:

    0  OK       recorded IS NOT DISTINCT FROM actual
    1  FAIL     recorded IS NULL (musl lineage), or recorded <> actual
    2  UNKNOWN  unreachable, auth refused, or a server too old to expose
                pg_database_collation_actual_version()

UNKNOWN is deliberately NOT 0. "Could not check" must never be reportable as "fine".

Design-for-failure: the connect is bounded (`--timeout`, default 10s) so this cannot
become the hung step in an incident; every failure path maps to UNKNOWN with a reason
rather than a traceback.

This command NEVER issues `ALTER DATABASE ... REFRESH COLLATION VERSION`. That records
the current version and rebuilds no index — it silences the warning while leaving every
index wrong, turning a detectable problem into an undetectable one. See §5 of
docs/runbooks/pgvector-deploy.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_UNKNOWN = 2

# NULL-safe on BOTH sides. `recorded <> actual` yields NULL when recorded is NULL, and
# NULL is not true — so a plain `<>` (or `= ''`) silently PASSES every musl-lineage
# database this exists to catch. Verified 2026-07-29 on hydroa-dev-postgres-1:
# `datcollversion IS NULL` -> t, `datcollversion = ''` -> NULL.
_LINEAGE_SQL = """
SELECT datname,
       datcollate,
       datctype,
       datcollversion,
       pg_database_collation_actual_version(oid) AS actual_version,
       datcollversion IS DISTINCT FROM pg_database_collation_actual_version(oid) AS mismatch
  FROM pg_database
 WHERE datname = current_database()
"""


def _normalise(url: str) -> str:
    """Accept the plain libpq URL an operator pastes, as well as the asyncpg one."""
    if url.startswith("postgresql+asyncpg://"):
        return url
    if url.startswith(("postgresql://", "postgres://")):
        scheme, rest = url.split("://", 1)
        del scheme
        return f"postgresql+asyncpg://{rest}"
    return url


async def _probe(url: str, timeout: float) -> dict[str, Any]:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(
        _normalise(url),
        connect_args={
            "timeout": timeout,
            "server_settings": {"application_name": "pg_preflight"},
        },
        pool_pre_ping=False,
    )
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(text(_LINEAGE_SQL))).mappings().one()
            server = (await conn.execute(text("SELECT version()"))).scalar_one()
        return {"row": dict(row), "server_version": str(server)}
    finally:
        await engine.dispose()


def _verdict(probe: dict[str, Any]) -> dict[str, Any]:
    row = probe["row"]
    recorded = row["datcollversion"]
    actual = row["actual_version"]
    result: dict[str, Any] = {
        "database": row["datname"],
        "collate": row["datcollate"],
        "recorded_version": recorded,
        "actual_version": actual,
        "server_version": probe["server_version"],
        "libc": actual,
        "remedy": None,
    }

    if actual is None:
        # The server cannot tell us what the collation provider's version IS, so there
        # is nothing to compare against. Not a clean bill of health — an UNKNOWN.
        result["status"] = "UNKNOWN"
        result["reason"] = (
            "this server does not report a collation version for the database "
            "(pg_database_collation_actual_version returned NULL), so the lineage "
            "cannot be judged from here"
        )
        return result

    if recorded is None:
        result["status"] = "FAIL"
        result["remedy"] = "reindex"
        result["reason"] = (
            f"no collation version was ever recorded for {row['datname']!r} while the "
            f"provider reports {actual!r}. That is the musl-lineage signature: this "
            "database was created by a Postgres that did not record one (the alpine "
            "image) and is now served by glibc. Postgres cannot warn about this case, "
            "because it has nothing to compare. Text indexes are ordered by the old "
            "collation and queried under the new one."
        )
        return result

    if recorded != actual:
        result["status"] = "FAIL"
        result["remedy"] = "reindex"
        result["reason"] = (
            f"collation version changed under this database: recorded {recorded!r}, "
            f"provider now reports {actual!r}. Text index order no longer matches the "
            "collation in use."
        )
        return result

    result["status"] = "OK"
    result["reason"] = f"recorded collation version {recorded!r} matches the provider"
    return result


def _render_text(result: dict[str, Any]) -> str:
    lines = [f"{k}: {v}" for k, v in result.items() if k != "reason"]
    lines.append(f"reason: {result['reason']}")
    if result["status"] == "FAIL":
        lines += [
            "",
            "REMEDY — see docs/runbooks/pgvector-deploy.md §4:",
            "  same cluster, same volume  ->  REINDEX DATABASE <name>;",
            "  moving to a new lineage    ->  pg_dump | pg_restore into a fresh database",
            "",
            "Do NOT run ALTER DATABASE ... REFRESH COLLATION VERSION as the fix: it",
            "records the current version and rebuilds nothing, hiding the problem.",
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pg_preflight.py",
        description="Report the collation lineage of a target Postgres database.",
    )
    parser.add_argument(
        "--database-url", required=True, help="postgresql://... (asyncpg accepted)"
    )
    parser.add_argument(
        "--json", action="store_true", help="emit the machine-readable form"
    )
    parser.add_argument(
        "--timeout", type=float, default=10.0, help="connect timeout, seconds"
    )
    args = parser.parse_args(argv)

    try:
        probe = asyncio.run(_probe(args.database_url, args.timeout))
        result = _verdict(probe)
    except Exception as exc:
        result = {
            "status": "UNKNOWN",
            "database": None,
            "collate": None,
            "recorded_version": None,
            "actual_version": None,
            "server_version": None,
            "libc": None,
            "remedy": None,
            "reason": f"could not reach or query the database: {type(exc).__name__}: {exc}",
        }

    print(json.dumps(result, indent=2) if args.json else _render_text(result))
    return {"OK": EXIT_OK, "FAIL": EXIT_FAIL, "UNKNOWN": EXIT_UNKNOWN}[
        str(result["status"])
    ]


if __name__ == "__main__":
    sys.exit(main())
