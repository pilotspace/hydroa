"""Failing-first (RED) suite — the test-infrastructure preflight and mid-run tripwire.

suite-infra-tripwire PLAN.md §4. Closes todo #83.

RED reason expected (before Build): `tests._infra_guard` does not exist, so every test
here fails at import/collection.

The incident of record (2026-08-05, `make ci` on main): 2265 tests passed, then
`hydroa-dev-postgres-1` exited, and the run ground on to `131 failed, 2130 errors` over
43 minutes — every one of them `OSError: Connect call failed ('127.0.0.1', 5433)`. The
run LOOKED like a catastrophic code regression and was in fact a dead container. Two
distinct holes produced that: nothing checks the stack is up before the first test, and
nothing notices when it dies half way through.

Design note on the false-positive direction, which is the dangerous one: a guard that
wrongly aborts a legitimate run is worse than the status quo it replaces. So the tripwire
requires N CONSECUTIVE connection-shaped failures and resets on anything else, and the
matcher is gated in BOTH directions — it must recognise the strings the real incident
produced, and must NOT fire on an ordinary assertion that happens to say "connection".

Tests that deliberately probe a dead port (e.g. `vector_extension_preflight::
test_unreachable_database_is_unknown_not_missing`, and several below) are safe: they
PASS, and only failures/errors are counted.
"""

from __future__ import annotations

import uuid

import pytest

from tests import _redis_env

# Port 1 is IANA-reserved and never listening. Bounded by the guard's own connect timeout.
DEAD_HOSTPORT = "127.0.0.1:1"
DEAD_DATABASE_URL = "postgresql+asyncpg://gateway:gateway@127.0.0.1:1/gateway_test"

# `check_infra` is SYNC on purpose: `pytest_sessionstart` is a sync hook that runs before
# any event loop exists, so the guard owns its own `asyncio.run`. That is also why every
# test in this file is a plain `def` — calling it from inside an async test would hit
# "asyncio.run() cannot be called from a running event loop".

# Short enough that a red/green cycle stays quick, long enough that a loaded 12-core host
# running -n 12 does not time out against a HEALTHY stack and manufacture a false problem.
PROBE_TIMEOUT = 5.0


def _live_redis_hostport() -> str:
    """The real Redis endpoint, derived from _redis_env's PUBLIC url.

    `_REDIS_HOST_PORT` is private (pyright reportPrivateUsage), so both this suite and the
    guard itself read the public `TEST_REDIS_URL` and strip the scheme + logical db.
    """
    return _redis_env.TEST_REDIS_URL.removeprefix("redis://").rsplit("/", 1)[0]


def _live_database_url() -> str:
    """This worker's real database — created by conftest's `_ensure_worker_database`."""
    return _redis_env.TEST_DATABASE_URL


def _missing_database_url() -> str:
    """A live server, but a database name that certainly does not exist."""
    base, _, _ = _redis_env.TEST_DATABASE_URL.rpartition("/")
    return f"{base}/absent_{uuid.uuid4().hex[:8]}"


# ─────────────────────────────────────────────────────────────────────────────
# M1 — the session refuses to start when its infrastructure is absent
# ─────────────────────────────────────────────────────────────────────────────


def test_check_infra_reports_unreachable_postgres() -> None:
    """An unreachable Postgres is reported, naming the dependency. covers: M1"""
    from tests._infra_guard import check_infra

    problems = check_infra(
        database_url=DEAD_DATABASE_URL,
        redis_hostport=_live_redis_hostport(),
        timeout=PROBE_TIMEOUT,
    )

    assert problems, "an unreachable Postgres must produce a problem, not an empty list"
    joined = "\n".join(problems).lower()
    assert "postgres" in joined, f"the problem must name Postgres; got: {problems}"
    assert "5432" not in joined or ":1" in joined, (
        f"the problem must name the endpoint actually tried, not a default; got: {problems}"
    )


def test_check_infra_reports_missing_database() -> None:
    """A live server with the test database GONE is its own diagnosis. covers: M1

    Distinct from "unreachable" on purpose: the remedy differs. "start the stack" is the
    wrong advice when the stack is already up and the database was dropped — a real case
    on this repo, where per-worker databases are created and FORCE-dropped every run.
    """
    from tests._infra_guard import check_infra

    url = _missing_database_url()
    expected_db = url.rsplit("/", 1)[-1]

    problems = check_infra(
        database_url=url,
        redis_hostport=_live_redis_hostport(),
        timeout=PROBE_TIMEOUT,
    )

    assert problems, "a missing database must produce a problem"
    joined = "\n".join(problems)
    assert expected_db in joined, (
        f"the problem must name the database that is missing ({expected_db!r}); got: {problems}"
    )
    assert "exist" in joined.lower(), (
        f"the problem must say the database does not exist, not merely that something "
        f"failed; got: {problems}"
    )


def test_check_infra_reports_unreachable_redis() -> None:
    """An unreachable Redis is reported, and Postgres is NOT blamed. covers: M1"""
    from tests._infra_guard import check_infra

    problems = check_infra(
        database_url=_live_database_url(),
        redis_hostport=DEAD_HOSTPORT,
        timeout=PROBE_TIMEOUT,
    )

    assert problems, "an unreachable Redis must produce a problem"
    joined = "\n".join(problems).lower()
    assert "redis" in joined, f"the problem must name Redis; got: {problems}"
    assert "postgres" not in joined, (
        f"only the failed dependency may be blamed — Postgres is up here; got: {problems}"
    )


def test_check_infra_returns_empty_when_everything_is_up() -> None:
    """The healthy stack produces NO problems. covers: M1, M4

    The false-positive arm, and the reason it is gated rather than assumed: this check
    runs at the head of EVERY session, so a guard that cries wolf blocks every run on
    this repo. It must be silent when the stack is fine.
    """
    from tests._infra_guard import check_infra

    problems = check_infra(
        database_url=_live_database_url(),
        redis_hostport=_live_redis_hostport(),
        timeout=PROBE_TIMEOUT,
    )

    assert problems == [], (
        f"the dev stack is up (this test is talking to it), so the guard must report "
        f"nothing; got: {problems}"
    )


def test_problem_lines_say_how_to_start_the_stack() -> None:
    """Every problem is ACTIONABLE without reading source. covers: M1

    The whole point is to replace a 2130-error wall with an answer. "Postgres is
    unreachable" is only half an answer; the command that fixes it is the other half.
    """
    from tests._infra_guard import check_infra

    problems = check_infra(
        database_url=DEAD_DATABASE_URL,
        redis_hostport=DEAD_HOSTPORT,
        timeout=PROBE_TIMEOUT,
    )

    assert len(problems) == 2, (
        f"both dependencies are down, so both must be reported — a guard that stops at "
        f"the first failure sends the operator round twice; got: {problems}"
    )
    for problem in problems:
        assert "docker compose" in problem, (
            f"each problem must name the command that starts the stack; got: {problem!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# M2/M3 — the run ABORTS when infrastructure dies mid-run
# ─────────────────────────────────────────────────────────────────────────────

CONNECTION_FAILURE = "OSError: Connect call failed ('127.0.0.1', 5433)"
ORDINARY_FAILURE = "AssertionError: assert 3 == 4"


def test_tripwire_fires_after_consecutive_connection_failures() -> None:
    """N consecutive connection failures trip the wire. covers: M2, M3"""
    from tests._infra_guard import InfraTripwire

    tripwire = InfraTripwire(threshold=3)
    for _ in range(3):
        assert not tripwire.tripped, "must not trip before the threshold is reached"
        tripwire.record(failed=True, text=CONNECTION_FAILURE)

    assert tripwire.tripped, (
        "3 consecutive connection failures at threshold=3 must trip — this is the "
        "2026-08-05 incident, where 2130 of them did not"
    )
    reason = tripwire.reason.lower()
    assert "invalid" in reason, (
        f"M3: the abort must state the run is INVALID as evidence, so a reader does not "
        f"mistake it for a code regression; got: {tripwire.reason!r}"
    )


def test_tripwire_ignores_a_single_isolated_connection_failure() -> None:
    """One connection failure, then a pass, is a flake — not an abort. covers: R"""
    from tests._infra_guard import InfraTripwire

    tripwire = InfraTripwire(threshold=3)
    tripwire.record(failed=True, text=CONNECTION_FAILURE)
    tripwire.record(failed=False, text="")

    assert not tripwire.tripped, (
        "a single isolated connection error (a flake, or a test that deliberately probes "
        "a closed port) must never abort the run"
    )


def test_tripwire_resets_on_a_non_connection_failure() -> None:
    """An ordinary failure between connection errors resets the counter. covers: R

    A genuinely broken suite must be allowed to report its own failures rather than being
    blamed on infrastructure and cut short.
    """
    from tests._infra_guard import InfraTripwire

    tripwire = InfraTripwire(threshold=3)
    tripwire.record(failed=True, text=CONNECTION_FAILURE)
    tripwire.record(failed=True, text=CONNECTION_FAILURE)
    tripwire.record(failed=True, text=ORDINARY_FAILURE)
    tripwire.record(failed=True, text=CONNECTION_FAILURE)

    assert not tripwire.tripped, (
        "the run is 4 failures deep but never 3 CONSECUTIVE connection failures — the "
        "counter must reset on a non-connection failure"
    )


def test_matcher_recognises_the_observed_incident_strings() -> None:
    """The matcher fires on the texts the real incident produced. covers: M2

    Literal strings taken from the 2026-08-05 run log, not invented — a matcher tuned to
    imagined text would have sailed past the actual outage.
    """
    from tests._infra_guard import looks_like_connection_loss

    observed = [
        "OSError: Connect call failed ('127.0.0.1', 5433)",
        "ConnectionRefusedError: [Errno 61] Connect call failed",
        "RuntimeError: unexpected connection_lost() call",
        "redis.exceptions.ConnectionError: Error 61 connecting to localhost:6380. "
        "Connection refused.",
        "sqlalchemy.exc.InterfaceError: connection is closed",
    ]
    for text in observed:
        assert looks_like_connection_loss(text), (
            f"this text was produced by a real infrastructure outage and must be "
            f"recognised: {text!r}"
        )


def test_matcher_does_not_match_ordinary_assertion_text() -> None:
    """The matcher stays silent on ordinary failures. covers: R

    The false-positive direction. These are real-shaped test failures from this repo's
    own domain, several of which contain the word "connection" — recognising them would
    abort legitimate runs on legitimate bugs.
    """
    from tests._infra_guard import looks_like_connection_loss

    benign = [
        "AssertionError: assert 3 == 4",
        "AssertionError: expected the connection pool size to be 10, got 5",
        "assert response.status_code == 200",
        "KeyError: 'tenant_id'",
        "AssertionError: connection_id should be persisted on the usage record",
    ]
    for text in benign:
        assert not looks_like_connection_loss(text), (
            f"an ordinary test failure must NOT be mistaken for infrastructure loss: {text!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# M5 — under xdist the preflight runs ONCE, on the controller
# ─────────────────────────────────────────────────────────────────────────────


def test_preflight_is_controller_only_under_xdist() -> None:
    """Only the controller checks; workers skip. covers: M5

    xdist runs `pytest_sessionstart` in EVERY worker (xdist/remote.py), so an ungated
    preflight would open 12 extra connection probes per run and print 12 copies of the
    same message. `workerinput` exists only in a worker (xdist/plugin.py) — verified by
    reading the installed package.
    """
    from tests._infra_guard import is_controller

    class _Config:
        pass

    controller = _Config()
    worker = _Config()
    worker.workerinput = {"workerid": "gw3"}  # type: ignore[attr-defined]

    assert is_controller(controller), "a config with no workerinput IS the controller"
    assert not is_controller(worker), (
        "a config carrying xdist's workerinput is a WORKER and must skip the preflight"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Guard against a vacuous suite
# ─────────────────────────────────────────────────────────────────────────────


def test_the_guard_module_is_importable_and_pure() -> None:
    """`tests/_infra_guard.py` exists and needs no pytest plugin to import.

    Not a Must — a tripwire that only works when loaded as a plugin could not be unit
    tested at all, and this suite would silently become a no-op. Cheap to assert.
    """
    import tests._infra_guard as guard

    for name in ("check_infra", "InfraTripwire", "looks_like_connection_loss", "is_controller"):
        assert hasattr(guard, name), f"the frozen §3 contract requires {name}"

    with pytest.raises(TypeError):
        guard.check_infra(DEAD_DATABASE_URL)  # type: ignore[call-arg]  # keyword-only
