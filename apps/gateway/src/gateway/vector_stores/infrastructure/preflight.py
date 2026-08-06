"""Boot-time fail-closed preflight for the pgvector `vector` extension.

vector-extension-preflight PLAN.md §3, FROZEN @ v1. Closes release-integrity exit
criterion 4 and todo #85.

Without this, a gateway pointed at a Postgres that never ran
`CREATE EXTENSION vector` boots perfectly happily and only falls over the first time a
tenant actually uses RAG — a 500 from `/v1/vector_stores`, discovered by the tenant
rather than by the operator at rollout. Migration `55dc3f920a38_vector_store_core`
installs the extension on the supported path; the gap is a gateway aimed at a database
where that migration never ran (an image override, a restored or replaced volume, or a
managed provider that blocks the extension — todo #67).

The milestone's recorded shared decision: "The preflight fails CLOSED. A gateway that
cannot confirm the `vector` extension refuses to boot rather than serving RAG surfaces
that 500 at first use." Tin re-confirmed at freeze (2026-08-05) that there is **no
opt-out**: a bare skip flag would simply restore the 500 this exists to remove.

NOT to be confused with `scripts/pg_preflight.py`. That is the COLLATION preflight
(todo #66) — an operator-run CLI aimed at an arbitrary target, about musl/glibc btree
ordering on an existing volume. This is an in-process boot guard on the gateway's own
connection, about a missing extension. Different hazard, different lifecycle; kept
deliberately separate rather than folded together.

Both failure modes refuse the boot. They are distinct TYPES rather than one error with a
flag so that a caller cannot accidentally treat "could not check" as "confirmed absent":
telling an operator to run `CREATE EXTENSION vector` when the real fault is a closed port
or bad credentials sends them to fix the wrong thing.
"""

from __future__ import annotations

import asyncio

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# One round trip: the database's own name (so the error can NAME what it checked) and
# whether the extension is installed in it. `pg_extension` is a catalog table, so this is
# valid BEFORE any application schema exists — which is what lets the call sit ahead of
# the dev/test `Base.metadata.create_all` bootstrap, whose Vector(1536) column would
# otherwise fail first with an opaque SQLAlchemy error.
_PROBE = text(
    "SELECT current_database() AS database, "
    "EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') AS present"
)

DEFAULT_TIMEOUT_SECONDS = 10.0


class VectorPreflightError(RuntimeError):
    """Base for both outcomes. Never raised directly — callers match the subclasses."""

    code = "ERR_VECTOR_PREFLIGHT"


class VectorExtensionMissingError(VectorPreflightError):
    """CONFIRMED absent: we reached the database and it does not have the extension."""

    code = "ERR_VECTOR_EXTENSION_MISSING"


class VectorPreflightUnknownError(VectorPreflightError):
    """COULD NOT CHECK: unreachable, auth refused, or the probe timed out.

    Deliberately NOT a subclass of VectorExtensionMissingError — "could not check" must
    never be renamed into a different, confident, wrong diagnosis. Same discipline
    `scripts/pg_preflight.py` already enforces with its distinct UNKNOWN exit code.
    """

    code = "ERR_VECTOR_PREFLIGHT_UNKNOWN"


async def assert_vector_extension(
    engine: AsyncEngine, *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
) -> None:
    """Confirm the connected database has the `vector` extension, or refuse the boot.

    Returns None when the extension is present. Raises VectorExtensionMissingError when
    it is confirmed absent, VectorPreflightUnknownError when the check could not be made.

    The wait is BOUNDED (PROJECT.md invariant: "No outbound IO without timeout"). This is
    one SELECT, not a retry loop — a preflight that can hang forever is a gateway that
    never finishes starting, which is a worse outage than the one it guards against.
    """
    try:
        async with asyncio.timeout(timeout_seconds):
            async with engine.connect() as conn:
                row = (await conn.execute(_PROBE)).one()
    except TimeoutError as exc:
        # TimeoutError subclasses OSError, so it MUST be caught before the broad clause
        # below or it would be reported with the generic message.
        raise VectorPreflightUnknownError(
            f"could not verify the pgvector 'vector' extension: the check did not complete "
            f"within {timeout_seconds}s. This is NOT a confirmation that the extension is "
            f"missing — the database did not answer. Check connectivity and credentials "
            f"before assuming anything about the schema."
        ) from exc
    except Exception as exc:
        raise VectorPreflightUnknownError(
            f"could not verify the pgvector 'vector' extension: {type(exc).__name__}: {exc}. "
            f"This is NOT a confirmation that the extension is missing — the database could "
            f"not be reached or queried. Check connectivity and credentials first."
        ) from exc

    database = str(row.database)
    if not row.present:
        raise VectorExtensionMissingError(
            f"database {database!r} does not have the required pgvector 'vector' extension, "
            f"so the gateway is refusing to start rather than serving /v1/vector_stores that "
            f"would fail at first use.\n"
            f"  Remedy:  psql -d {database} -c 'CREATE EXTENSION vector;'\n"
            f"  Then restart the gateway.\n"
            f"If that command reports the extension is unavailable, the Postgres image itself "
            f"lacks pgvector — every supported target pins pgvector/pgvector:pg16 "
            f"(infra/docker-compose.*.yml, charts/ai-proxy/values.yaml). "
            f"See docs/runbooks/pgvector-deploy.md."
        )

    structlog.get_logger(__name__).info(
        "vector_extension_preflight_ok", database=database, extension="vector"
    )
