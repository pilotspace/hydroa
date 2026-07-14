"""Single source of truth for per-worker test Redis / Postgres isolation.

ci-pytest-xdist-parallel — under `pytest -n N`, every worker runs in its own process
but shares the one dev Postgres (:5433) and Redis (:6380). Two isolation mechanisms in
tests/conftest.py assume exclusive access and would corrupt across workers otherwise:

  * Postgres — the `app` fixture does `Base.metadata.drop_all/create_all` per test. Two
    workers on the same database would drop each other's schema mid-test.
  * Redis   — the autouse `_isolate_stores` fixture clears (almost) the whole logical db
    before each test. Two workers on the same db would wipe each other's just-seeded keys.

So each worker needs a PRIVATE Postgres database and a PRIVATE Redis logical db. This
module derives both from the xdist worker id and is imported everywhere a test opens a
Redis/Postgres connection, so a `-n auto` run is deterministic. A non-xdist run keeps the
historical `db 9` / `gateway_test` names, so existing tooling, evidence and the migrations
conftest are unaffected when you don't pass `-n`.

Design-for-failure: the derivations are pure, env-only and total (no I/O, no exceptions) —
an unexpected worker-id shape folds to a stable in-range db rather than raising, so a
mis-shaped id degrades to a valid private slot instead of crashing collection.
"""

from __future__ import annotations

import os

# Base endpoints — mirror the historical hardcoded literals; both overridable via env so
# CI or a second dev stack can retarget without editing tests.
_BASE_DATABASE_URL = os.environ.get(
    "GATEWAY_TEST_DATABASE_URL",
    "postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
)
_REDIS_HOST_PORT = os.environ.get("GATEWAY_TEST_REDIS_HOSTPORT", "localhost:6380")

_LEGACY_REDIS_DB = 9  # the single-process db every suite historically shared
_REDIS_DB_COUNT = 16  # redis default `databases 16` → logical dbs 0..15
# db 0 is reserved (ops/probes defaults elsewhere use it), so worker dbs live in [1, 15]:
# a fleet of up to 15 workers gets a fully distinct db. `-n auto` on a >15-core host must
# cap workers (Makefile `test-parallel` pins `-n 12`), well inside the range.


def worker_id() -> str:
    """xdist worker id ('gw0'..'gwN'), or '' when not running under xdist.

    xdist sets PYTEST_XDIST_WORKER in each worker subprocess before it imports the test
    tree, so reading it at import time here yields the right worker. 'master' is xdist's
    controller sentinel and is treated as non-xdist.
    """
    wid = os.environ.get("PYTEST_XDIST_WORKER", "")
    return "" if wid == "master" else wid


def _worker_index() -> int | None:
    """Numeric index of this worker, or None for a non-xdist run."""
    wid = worker_id()
    if not wid:
        return None
    if wid.startswith("gw") and wid[2:].isdigit():
        return int(wid[2:])
    # Defensive: an unexpected id shape folds to a stable, in-range slot (never raises).
    return abs(hash(wid)) % (_REDIS_DB_COUNT - 1)


def redis_db() -> int:
    """Private Redis logical db for this worker; legacy db 9 when not under xdist."""
    idx = _worker_index()
    if idx is None:
        return _LEGACY_REDIS_DB
    return (idx % (_REDIS_DB_COUNT - 1)) + 1  # → [1, 15], never the reserved db 0


def redis_url() -> str:
    """Full Redis URL on this worker's private logical db."""
    return f"redis://{_REDIS_HOST_PORT}/{redis_db()}"


def database_url() -> str:
    """Private Postgres database URL for this worker; the base db when not under xdist.

    Only the final path segment (the database name) is suffixed, so credentials, host and
    port are preserved: gateway_test → gateway_test_gwN.
    """
    idx = _worker_index()
    if idx is None:
        return _BASE_DATABASE_URL
    base, _, dbname = _BASE_DATABASE_URL.rpartition("/")
    return f"{base}/{dbname}_gw{idx}"


# Import-time constants — evaluated once per worker process, after xdist has set the env.
TEST_REDIS_URL = redis_url()
TEST_DATABASE_URL = database_url()
