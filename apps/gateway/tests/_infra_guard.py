"""Test-infrastructure preflight and mid-run tripwire.

suite-infra-tripwire PLAN.md §3, FROZEN @ v1. Closes todo #83.

The incident this exists for (2026-08-05, `make ci` on main): 2265 tests passed, then
`hydroa-dev-postgres-1` exited, and the run ground on to `131 failed, 2130 errors` over
43 minutes — every one of them `OSError: Connect call failed ('127.0.0.1', 5433)`. The
result read exactly like a catastrophic code regression and was a dead container.

Two independent holes produced that, and both are closed here:

  * nothing checked the stack was up before the first test  -> `check_infra`, called from
    `pytest_sessionstart`, stops the session with a named, actionable message;
  * nothing noticed the stack dying half way through        -> `InfraTripwire`, fed from
    `pytest_runtest_logreport`, aborts once N consecutive tests fail with a
    connection-shaped error.

Everything here is a PURE function or a plain object precisely so it can be unit-tested;
the two conftest hooks stay thin wrappers around it. A hook body cannot be tested, so no
decision lives in one.

Design-for-failure: every probe is bounded by an explicit timeout, `check_infra` NEVER
raises (an unexpected error becomes a reported problem, never a collection crash), and
both dependencies are always probed so a doubly-broken stack is reported in one pass
instead of sending the operator round twice.

The dangerous direction is the FALSE POSITIVE: a guard that wrongly aborts a legitimate
run is worse than the status quo it replaces. Hence the tripwire needs N CONSECUTIVE
connection failures, resets on anything else, and `looks_like_connection_loss` refuses to
fire on an ordinary assertion failure even when its text mentions connections.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

# The command that fixes an absent stack. Deliberately does NOT name individual services:
# a Redis-only problem line that said "postgres" would blame the wrong dependency.
START_STACK = "docker compose -f infra/docker-compose.dev.yml up -d"

# Env opt-out for the sessionstart preflight. Set by `make test-fast`, whose suites are
# deliberately DB-free (MockTransport / pure-unit) — see PLAN.md M4. There is NO opt-out
# for the tripwire: it only ever fires on failures that are already happening.
SKIP_ENV_VAR = "GATEWAY_TEST_SKIP_INFRA_CHECK"

DEFAULT_THRESHOLD = 5
DEFAULT_TIMEOUT_SECONDS = 5.0


# ─────────────────────────────────────────────────────────────────────────────
# M5 — controller/worker discrimination
# ─────────────────────────────────────────────────────────────────────────────


def is_controller(config: object) -> bool:
    """True when this process is the pytest controller, False inside an xdist worker.

    xdist runs `pytest_sessionstart` in EVERY worker (xdist/remote.py), so an ungated
    preflight would open 12 extra probes per run and print 12 copies of one message.
    `workerinput` is injected onto the config only in a worker (xdist/plugin.py). The
    controller also receives every worker's `pytest_runtest_logreport`, so the same gate
    gives the tripwire whole-run visibility from one place.
    """
    return not hasattr(config, "workerinput")


# ─────────────────────────────────────────────────────────────────────────────
# M1 — the start-of-session preflight
# ─────────────────────────────────────────────────────────────────────────────


def _pg_endpoint(database_url: str) -> str:
    """host:port of a SQLAlchemy Postgres URL, for the message. Never raises."""
    try:
        parsed = urlparse(database_url)
        return f"{parsed.hostname or '?'}:{parsed.port or 5432}"
    except ValueError:  # pragma: no cover — a malformed URL still gets a message
        return database_url


def _pg_database(database_url: str) -> str:
    """The database name of a SQLAlchemy Postgres URL. Never raises."""
    return database_url.rsplit("/", 1)[-1].split("?", 1)[0]


def redis_hostport(redis_url: str) -> str:
    """`host:port` from a redis:// URL, dropping the logical-db path.

    `_redis_env._REDIS_HOST_PORT` is private (pyright reportPrivateUsage rejects an
    outside read), so callers derive the endpoint from the public `TEST_REDIS_URL`.
    """
    parsed = urlparse(redis_url)
    return f"{parsed.hostname or 'localhost'}:{parsed.port or 6379}"


async def _check_postgres(database_url: str, timeout_seconds: float) -> str | None:
    """None when reachable; otherwise ONE problem line naming the actual fault.

    "unreachable" and "database does not exist" are separate diagnoses because their
    remedies differ: telling someone to start a stack that is already running wastes the
    exact minutes this guard exists to save. The second case is real on this repo — the
    per-worker `gateway_test_gwN` databases are created and FORCE-dropped every run.
    """
    import asyncpg  # local import: the raw driver is only needed here

    dsn = database_url.replace("+asyncpg", "")
    endpoint = _pg_endpoint(database_url)
    database = _pg_database(database_url)
    try:
        async with asyncio.timeout(timeout_seconds):
            # asyncpg types its own connect timeout as int; the outer asyncio.timeout is
            # the real bound, this is belt-and-braces so a hung TCP connect cannot outlive
            # it even if the driver ignores cancellation.
            conn = await asyncpg.connect(dsn, timeout=max(1, round(timeout_seconds)))
            await conn.close()
    except asyncpg.InvalidCatalogNameError:
        return (
            f"Postgres at {endpoint} is up, but the test database {database!r} does not "
            f"exist. It is normally created by the dev stack; recreate it with "
            f"`{START_STACK}` (which runs the bootstrap), or "
            f"`createdb -h {endpoint.split(':')[0]} -p {endpoint.split(':')[1]} "
            f"-U gateway {database}`."
        )
    except asyncpg.InvalidPasswordError as exc:
        return (
            f"Postgres at {endpoint} refused the test credentials ({exc}). Check "
            f"GATEWAY_TEST_DATABASE_URL, or restart the dev stack with `{START_STACK}`."
        )
    except (TimeoutError, OSError) as exc:
        return (
            f"Postgres is unreachable at {endpoint} ({type(exc).__name__}: {exc}). Start "
            f"the test stack with `{START_STACK}`, then re-run."
        )
    except Exception as exc:  # fail-closed: an unexpected fault is still a problem
        return (
            f"Postgres at {endpoint} could not be checked ({type(exc).__name__}: {exc}). "
            f"Start or restart the test stack with `{START_STACK}`."
        )
    return None


async def _check_redis(hostport: str, timeout_seconds: float) -> str | None:
    """None when reachable; otherwise ONE problem line.

    Deliberately never says "postgres": blaming a healthy dependency for another's
    outage is the same class of mistake this whole module exists to stop.
    """
    import redis.asyncio as aioredis  # local import: mirrors conftest's own convention

    host, _, port = hostport.partition(":")
    client = aioredis.Redis(
        host=host or "localhost",
        port=int(port or 6379),
        socket_connect_timeout=timeout_seconds,
        socket_timeout=timeout_seconds,
    )
    try:
        async with asyncio.timeout(timeout_seconds):
            await client.ping()
    except Exception as exc:
        return (
            f"Redis is unreachable at {hostport} ({type(exc).__name__}: {exc}). Start the "
            f"test stack with `{START_STACK}`, then re-run."
        )
    finally:
        try:
            await client.aclose()
        except Exception:  # pragma: no cover — teardown must never mask the diagnosis
            pass
    return None


async def _check_all(database_url: str, redis_hostport_: str, timeout_seconds: float) -> list[str]:
    """Probe both dependencies CONCURRENTLY and report every failure.

    `return_exceptions=True` so one probe blowing up in an unforeseen way cannot swallow
    the other's verdict — a guard that crashes is a guard that is not there.
    """
    results = await asyncio.gather(
        _check_postgres(database_url, timeout_seconds),
        _check_redis(redis_hostport_, timeout_seconds),
        return_exceptions=True,
    )
    problems: list[str] = []
    for result in results:
        if isinstance(result, BaseException):
            problems.append(
                f"a test-infrastructure probe failed unexpectedly "
                f"({type(result).__name__}: {result}). Start the test stack with "
                f"`{START_STACK}`."
            )
        elif result is not None:
            problems.append(result)
    return problems


def check_infra(*, database_url: str, redis_hostport: str, timeout: float) -> list[str]:
    """Probe the test stack. `[]` means healthy; otherwise one line per failed dependency.

    SYNC on purpose: `pytest_sessionstart` is a sync hook that runs before any event loop
    exists, so this owns its `asyncio.run`. Keyword-only so a caller cannot silently swap
    the two string arguments and probe Redis for a Postgres URL.
    """
    return asyncio.run(_check_all(database_url, redis_hostport, timeout))


# ─────────────────────────────────────────────────────────────────────────────
# M2/M3 — the mid-run tripwire
# ─────────────────────────────────────────────────────────────────────────────

# Substrings (lowercased) that only appear when a connection actually died. Every entry
# was taken from a real failure text — the 2026-08-05 run log, or the driver source that
# produced it — rather than invented: a matcher tuned to imagined strings would have
# sailed straight past the outage it was written for.
_CONNECTION_MARKERS = (
    "connect call failed",
    "connectionrefusederror",
    "connectionreseterror",
    "connection refused",
    "connection_lost()",
    "connection is closed",
    "connection was closed in the middle of operation",
    "server closed the connection unexpectedly",
    "cannot connect to host",
    "redis.exceptions.connectionerror",
)

# An ordinary assertion failure is the TEST's verdict about the code, never evidence that
# the infrastructure died — even when its message happens to mention connections (this
# repo has assertions about connection pools and connection ids). Excluding it costs a
# false negative in the rare case where infra loss surfaces through an assert, which §1
# explicitly accepts; including it would risk aborting a legitimate run, which §1 does not.
_ORDINARY_FAILURE_MARKER = "assertionerror"


def looks_like_connection_loss(text: str) -> bool:
    """True when a failure's text is the signature of lost infrastructure."""
    lowered = text.lower()
    if _ORDINARY_FAILURE_MARKER in lowered:
        return False
    return any(marker in lowered for marker in _CONNECTION_MARKERS)


def _connection_line(text: str) -> str:
    """The most informative single line of a traceback: the one that names the fault.

    A raw `splitlines()[-1]` picks pytest's source-location footer
    (`tests/x.py:5: OSError`), which tells an operator where the test was, not what died.
    Scanning for the marker line surfaces `OSError: Connect call failed ('127.0.0.1',
    5433)` instead — the fact they actually need.
    """
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return text
    for line in reversed(lines):
        if looks_like_connection_loss(line):
            return line
    return lines[-1]


class InfraTripwire:
    """Counts CONSECUTIVE connection-shaped failures and trips at `threshold`.

    Consecutive, not cumulative, and reset by any pass or by any non-connection failure.
    That is what separates "the database died and everything after it is noise" from "a
    test deliberately probed a closed port" or "the suite is genuinely broken". A broken
    suite must be allowed to report its own failures rather than being cut short and
    blamed on infrastructure.
    """

    def __init__(self, threshold: int = DEFAULT_THRESHOLD) -> None:
        self.threshold = threshold
        self._consecutive = 0
        self._last_text = ""

    def record(self, failed: bool, text: str) -> None:
        """Feed one test outcome. `failed` covers both failures and errors."""
        if failed and looks_like_connection_loss(text):
            self._consecutive += 1
            self._last_text = _connection_line(text)
        else:
            self._consecutive = 0

    @property
    def tripped(self) -> bool:
        return self._consecutive >= self.threshold

    @property
    def reason(self) -> str:
        """The abort message. States the run is INVALID as evidence (M3)."""
        return (
            f"\nINFRASTRUCTURE LOST — this run is INVALID as evidence.\n"
            f"{self._consecutive} consecutive tests failed with a connection error; the "
            f"last was:\n    {self._last_text}\n"
            f"These failures are the test infrastructure, NOT the code under test. "
            f"Continuing would produce thousands more of them and a red result that "
            f"looks like a code regression.\n"
            f"Fix:  {START_STACK}\n"
            f"Then re-run. Nothing about this run's pass/fail counts can be trusted.\n"
        )
