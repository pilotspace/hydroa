"""Failing-first (RED) suite for pgvector-deploy-runbook — PLAN.md §4, FROZEN @ v2.

Two kinds of check, both red before the build:

  * the preflight CLI (`scripts/pg_preflight.py`) does not exist yet — every test that
    invokes it fails on a missing file;
  * the docs checks are red against the CURRENT tree, not against missing code:
    `docs/runbooks/backup-rollback.md` restores into stock `postgres:16` (no pgvector,
    so a post-R5 dump carrying `vector(1536)` cannot restore) and
    `docs/runbooks/01-getting-started.md` still names `postgres:16-alpine`.

The hazard being guarded: R5 (`4a351bd`) moved the Postgres image from
`postgres:16-alpine` (musl) to `pgvector/pgvector:pg16` (Debian/glibc). A volume
initdb'd under musl and served by glibc has text indexes in musl collation order and
queries them under glibc. Postgres warns on a collation-version mismatch ONLY when it
has a recorded version to compare; musl-era databases have `datcollversion` SQL NULL,
so there is no warning at all — the symptom is wrong ORDER BY / range results, not a
crash. Measured on hydroa-dev-postgres-1 2026-07-29: a database created now records
'2.36'; every database predating the switch has NULL.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests import _redis_env
from tests.migrations.test_ci_workflow_parity import PGVECTOR_IMAGE

REPO_ROOT = Path(__file__).resolve().parents[4]
PREFLIGHT = REPO_ROOT / "scripts" / "pg_preflight.py"
RUNBOOKS = REPO_ROOT / "docs" / "runbooks"
PGVECTOR_RUNBOOK = RUNBOOKS / "pgvector-deploy.md"

EXIT_OK, EXIT_FAIL, EXIT_UNKNOWN = 0, 1, 2

# A docker image reference whose repository ends in postgres/pgvector. The negative
# lookahead after the colon keeps `postgresql://…` connection strings out: they are
# not image pins and matching them would make this guard cry wolf on every runbook.
_IMAGE_RE = re.compile(r"(?<![\w/])((?:[\w.-]+/)*(?:postgres|pgvector)[\w.-]*:(?!//)[\w.-]+)")


def _run_preflight(*args: str) -> subprocess.CompletedProcess[str]:
    # S603: the argv is this interpreter plus a repo-local script path and literals
    # from the test itself — there is no untrusted input in it.
    return subprocess.run(  # noqa: S603
        [sys.executable, str(PREFLIGHT), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )


def _preflight_json(*args: str) -> tuple[int, dict[str, object]]:
    proc = _run_preflight(*args, "--json")
    assert proc.stdout.strip(), f"preflight wrote nothing to stdout; stderr={proc.stderr!r}"
    return proc.returncode, json.loads(proc.stdout)


def _admin_url() -> str:
    return _redis_env.TEST_DATABASE_URL


def _url_for(db_name: str) -> str:
    base = _admin_url()
    return base.rsplit("/", 1)[0] + "/" + db_name


def _code_strings(path: Path) -> list[str]:
    """Every string literal the module can actually EXECUTE — docstrings excluded.

    A guard that greps raw source cannot tell "issues this SQL" from "explains why it
    must never issue this SQL". Both of the source-level guards below tripped on
    pg_preflight.py's own warning docstring on the first run, which is the guard being
    wrong, not the code. Only string literals that are not docstrings can reach a
    database, so those are what gets asserted on.
    """
    tree = ast.parse(path.read_text())
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def _pin_bearing_lines(body: str) -> list[str]:
    """The lines of a runbook where an image reference is a PIN, not a sentence.

    A pin is something an operator copies and runs, or reads as "this is what runs":
    fenced code blocks and table cells. Ordinary prose and blockquotes are excluded,
    because a runbook that explains the hazard has to be able to NAME the bad image —
    `postgres:16-alpine` appears in pgvector-deploy.md §1 for exactly that reason, and a
    guard that punishes the explanation pushes the next author to delete it.
    """
    lines: list[str] = []
    in_fence = False
    for raw in body.splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if stripped.startswith(">"):
            continue
        if in_fence or stripped.startswith("|"):
            lines.append(raw)
    return lines


def _runbook_image_pins() -> dict[Path, set[str]]:
    """Every postgres/pgvector image PIN appearing in any runbook markdown file."""
    found: dict[Path, set[str]] = {}
    for md in sorted(RUNBOOKS.rglob("*.md")):
        hits = {
            m.group(1)
            for line in _pin_bearing_lines(md.read_text())
            for m in _IMAGE_RE.finditer(line)
        }
        if hits:
            found[md] = hits
    return found


# ─────────────────────────────────────────────────────────────────────────────
# Preflight — collation lineage (M1, M2)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preflight_exits_zero_on_current_lineage() -> None:
    """A database created by the CURRENT image is healthy: recorded == actual. covers: M1"""
    db = f"preflight_ok_{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{db}"'))
    finally:
        await engine.dispose()

    try:
        code, payload = _preflight_json("--database-url", _url_for(db))
        assert code == EXIT_OK, f"expected OK for a freshly-created database, got {payload}"
        assert payload["status"] == "OK"
        assert payload["recorded_version"], (
            "a database created by the current image must carry a recorded collation "
            f"version; got {payload['recorded_version']!r}"
        )
        assert payload["recorded_version"] == payload["actual_version"]
    finally:
        engine = create_async_engine(_admin_url(), isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(text(f'DROP DATABASE IF EXISTS "{db}"'))
        finally:
            await engine.dispose()


@pytest.mark.asyncio
async def test_preflight_exits_one_when_recorded_version_is_absent() -> None:
    """The musl-era case Postgres itself cannot warn about. covers: M1

    Arranged by nulling `datcollversion` on a THROWAWAY database — the only way to
    manufacture a musl-lineage row without an alpine server. That write needs
    superuser; if it is refused this test SKIPS with the reason rather than passing,
    because a green here would claim the detector works when it was never exercised.
    """
    db = f"preflight_musl_{uuid.uuid4().hex[:8]}"
    engine = create_async_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    skip_reason: str | None = None
    try:
        async with engine.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{db}"'))
            try:
                await conn.execute(
                    text("UPDATE pg_database SET datcollversion = NULL WHERE datname = :d"),
                    {"d": db},
                )
            except Exception as exc:  # insufficient privilege on a locked-down server
                skip_reason = f"cannot null datcollversion (needs superuser): {exc}"
    finally:
        await engine.dispose()

    try:
        if skip_reason is not None:
            pytest.skip(skip_reason)
        code, payload = _preflight_json("--database-url", _url_for(db))
        assert code == EXIT_FAIL, f"a musl-lineage database must FAIL, got {payload}"
        assert payload["status"] == "FAIL"
        assert payload["recorded_version"] is None
        assert payload["remedy"] in {"reindex", "dump_restore"}, (
            "a FAIL must name the remedy — an operator reading this at 3am needs the "
            f"next action, not just a verdict; got {payload['remedy']!r}"
        )
    finally:
        engine = create_async_engine(_admin_url(), isolation_level="AUTOCOMMIT")
        try:
            async with engine.connect() as conn:
                await conn.execute(text(f'DROP DATABASE IF EXISTS "{db}"'))
        finally:
            await engine.dispose()


def test_preflight_null_recorded_is_not_confused_with_empty_string() -> None:
    """NULL is the musl signal — `= ''` would silently pass every one of them. covers: M1, R:silent_collation_mismatch

    Pins the §1 Boundary correction. `datcollversion = ''` evaluates to NULL for a NULL
    column, and NULL is not true, so a check written that way passes exactly the
    databases it exists to catch. The comparison must be NULL-safe.
    """
    executed = _code_strings(PREFLIGHT)
    assert any("IS DISTINCT FROM" in s.upper() for s in executed), (
        "the recorded-vs-actual comparison must be NULL-safe (IS DISTINCT FROM); "
        "a plain `<>` against a NULL recorded version yields NULL, never true"
    )
    offenders = [s for s in executed if re.search(r"datcollversion\s*=\s*''", s)]
    assert not offenders, (
        "datcollversion is SQL NULL on musl-lineage databases, not the empty string "
        f"(verified 2026-07-29: `IS NULL` -> t, `= ''` -> NULL); found {offenders}"
    )


def test_preflight_unknown_when_server_unreachable() -> None:
    """Cannot-check must never read as checked-and-fine. covers: M2"""
    dead = "postgresql+asyncpg://gateway:gateway@127.0.0.1:1/nope"
    code, payload = _preflight_json("--database-url", dead)
    assert code == EXIT_UNKNOWN, (
        f"an unreachable server must be UNKNOWN (exit {EXIT_UNKNOWN}), distinct from "
        f"FAIL — got exit {code}, {payload}"
    )
    assert payload["status"] == "UNKNOWN"
    assert payload["reason"], "UNKNOWN must say why it could not judge"


def test_preflight_only_ever_executes_select() -> None:
    """Read-only by construction. covers: R:collation_version_laundered

    The prohibition that matters is ISSUING `ALTER DATABASE ... REFRESH COLLATION
    VERSION` — it records the current version, rebuilds no index, and converts a
    detectable problem into an undetectable one.

    Asserting on the raw source cannot express that: two earlier versions of this guard
    tripped first on the preflight's warning DOCSTRING and then on the operator-facing
    remedy text it PRINTS, both of which name the trap precisely so a human avoids it.
    Punishing the tool for warning about the thing is backwards. What is actually
    checkable is the SQL it hands to SQLAlchemy: every statement must be a SELECT, which
    no ALTER can satisfy.
    """
    tree = ast.parse(PREFLIGHT.read_text())
    statements: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "text"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            statements.append(node.args[0].value)
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if any(n.endswith("_SQL") for n in names) and isinstance(node.value.value, str):
                statements.append(node.value.value)

    assert statements, "found no SQL in the preflight at all — this guard proves nothing"
    offenders = [s.strip()[:60] for s in statements if not s.strip().upper().startswith("SELECT")]
    assert not offenders, (
        f"the preflight must only ever execute SELECT; found {offenders}. Any DDL here "
        "means it can mutate the database it was asked to diagnose."
    )


# ─────────────────────────────────────────────────────────────────────────────
# The runbook (M3, M6)
# ─────────────────────────────────────────────────────────────────────────────


def test_runbook_documents_every_preflight_status() -> None:
    """OK, FAIL and UNKNOWN each need a documented next action. covers: M3, R:unverifiable_runbook"""
    body = PGVECTOR_RUNBOOK.read_text()
    for status in ("OK", "FAIL", "UNKNOWN"):
        assert status in body, (
            f"the runbook never mentions the {status} preflight status — an operator "
            "who hits it has nothing to follow"
        )


def test_runbook_covers_both_deploy_shapes() -> None:
    """Both supported shapes, each with its own remedy. covers: M3"""
    body = PGVECTOR_RUNBOOK.read_text().lower()
    assert "statefulset" in body, "the in-cluster StatefulSet PVC path is not covered"
    assert "managed" in body, "the managed-Postgres path is not covered"
    assert "reindex" in body, "REINDEX (same-cluster remedy) is not documented"
    assert "dump" in body and "restore" in body, "the dump/restore remedy is not documented"


def test_runbook_documents_interrupted_remedy_rollback() -> None:
    """A remedy that can be interrupted needs a stated rollback. covers: M3"""
    body = PGVECTOR_RUNBOOK.read_text().lower()
    assert "interrupt" in body, (
        "neither remedy states what to do if it is interrupted midway — a REINDEX or "
        "dump/restore killed halfway is the likeliest way this runbook is actually used"
    )


def test_runbook_warns_against_refresh_collation_version() -> None:
    """The trap must be named, not merely avoided. covers: M3, R:collation_version_laundered"""
    body = PGVECTOR_RUNBOOK.read_text().upper()
    assert "REFRESH COLLATION VERSION" in body, (
        "the runbook must name REFRESH COLLATION VERSION as a trap — it is the first "
        "thing a search engine offers for the warning text, and it hides the problem"
    )


def test_provider_privilege_claims_are_evidenced() -> None:
    """No bare provider assertions. covers: M6, R:unverified_provider_claim"""
    body = PGVECTOR_RUNBOOK.read_text()
    for claim in ("rds_superuser", "RDS_SUPERUSER"):
        if claim in body:
            assert "UNVERIFIED" in body or "http" in body, (
                f"the runbook asserts {claim!r} without a citation or an explicit "
                "UNVERIFIED marker — a provider privilege claim written from memory "
                "is how a runbook sends an operator down the wrong path"
            )
            break


# ─────────────────────────────────────────────────────────────────────────────
# Image pins in the docs (M4, M5) — RED against the current tree
# ─────────────────────────────────────────────────────────────────────────────


def test_no_runbook_pins_a_postgres_image_without_pgvector() -> None:
    """Every documented Postgres image must serve `CREATE EXTENSION vector`. covers: M4, M5, R:image_without_pgvector

    RED today on two files: backup-rollback.md's restore drill names `postgres:16`
    (so restoring any post-R5 dump fails on the missing extension) and
    01-getting-started.md still names `postgres:16-alpine`.
    """
    offenders = {
        str(path.relative_to(REPO_ROOT)): sorted(pins - {PGVECTOR_IMAGE})
        for path, pins in _runbook_image_pins().items()
        if pins - {PGVECTOR_IMAGE}
    }
    assert not offenders, (
        f"runbooks pin Postgres images that are not {PGVECTOR_IMAGE!r}: {offenders}. "
        "A dump taken after R5 carries `CREATE EXTENSION vector` and `vector(1536)` "
        "columns and cannot be restored into a stock image."
    )


def test_runbook_image_pin_matches_the_deployed_pin() -> None:
    """One source of truth for the pin, not a second copy that can drift. covers: M5"""
    pins = {pin for pins in _runbook_image_pins().values() for pin in pins}
    assert pins, "no Postgres image is pinned in any runbook at all"
    assert pins == {PGVECTOR_IMAGE}, (
        f"runbook image pins {sorted(pins)} disagree with the deployed pin "
        f"{PGVECTOR_IMAGE!r} enforced by tests/migrations/test_ci_workflow_parity.py"
    )


def _docker_or_skip() -> str:
    docker_bin = shutil.which("docker")
    if docker_bin is None:
        pytest.skip("docker not installed — restore drill not exercised")
    probe = subprocess.run(  # noqa: S603
        [docker_bin, "info"], capture_output=True, text=True, timeout=60
    )
    if probe.returncode != 0:
        pytest.skip("docker daemon unavailable — restore drill not exercised")
    return docker_bin


def test_restore_drill_accepts_a_vector_bearing_dump() -> None:
    """Actually dump and restore a vector(1536) column. covers: M4

    Deliberately the WHOLE drill, not a proxy for it. An earlier version asserted only
    that the drill's image carries `vector.control`, which stays green even if pg_dump
    emits something pg_restore cannot take back — and §1's After criterion is that a
    vector-bearing dump RESTORES.

    Readiness is polled over TCP, never the unix socket. `docker-entrypoint.sh` runs a
    TEMPORARY server on the socket while it initialises the cluster, and that server
    accepts connections and then shuts down. A socket-based `pg_isready` therefore goes
    green during init, and a fast drill can complete against the temp server and vanish
    with it — which is exactly how the first version of this test PASSED while the drill
    it claims to prove was failing with "the database system is shutting down". The
    entrypoint only opens a TCP listener once init has finished, so TCP is the honest
    signal.

    Skips with a reason when docker is unavailable. A skip that reads as a pass is how
    the backup runbook rotted through a whole release without anyone noticing.
    """
    docker_bin = _docker_or_skip()

    drill_images = _runbook_image_pins().get(RUNBOOKS / "backup-rollback.md", set())
    assert drill_images, "backup-rollback.md names no Postgres image for the restore drill"
    assert drill_images == {PGVECTOR_IMAGE}, (
        f"the restore drill names {sorted(drill_images)}; only {PGVECTOR_IMAGE!r} is "
        "known to restore a post-0.13.0 dump"
    )
    name = f"pgv-restore-drill-{uuid.uuid4().hex[:8]}"

    drill = """
set -e
psql -h 127.0.0.1 -U postgres -q -c 'CREATE DATABASE src'
psql -h 127.0.0.1 -U postgres -q -d src -c 'CREATE EXTENSION IF NOT EXISTS vector'
psql -h 127.0.0.1 -U postgres -q -d src \
  -c 'CREATE TABLE chunks (id int primary key, embedding vector(1536))'
psql -h 127.0.0.1 -U postgres -q -d src \
  -c "INSERT INTO chunks VALUES (1, array_fill(0.5::real, ARRAY[1536])::vector)"
pg_dump -h 127.0.0.1 -U postgres --format=custom src > /tmp/src.dump
psql -h 127.0.0.1 -U postgres -q -c 'CREATE DATABASE tgt'
pg_restore -h 127.0.0.1 -U postgres --dbname=tgt --no-owner /tmp/src.dump
psql -h 127.0.0.1 -U postgres -At -d tgt \
  -c "SELECT count(*) FROM chunks WHERE embedding IS NOT NULL"
psql -h 127.0.0.1 -U postgres -At -d tgt \
  -c "SELECT extname FROM pg_extension WHERE extname='vector'"
"""

    started = subprocess.run(  # noqa: S603
        [
            docker_bin,
            "run",
            "-d",
            "--name",
            name,
            "-e",
            "POSTGRES_PASSWORD=drill",
            "-e",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            PGVECTOR_IMAGE,
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert started.returncode == 0, f"could not start the drill container: {started.stderr}"

    try:
        deadline = time.monotonic() + 120.0
        ready = False
        while time.monotonic() < deadline:
            probe = subprocess.run(  # noqa: S603
                [docker_bin, "exec", name, "pg_isready", "-h", "127.0.0.1", "-U", "postgres"],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if probe.returncode == 0:
                ready = True
                break
            time.sleep(1.0)
        assert ready, "the drill container never accepted TCP connections within 120s"

        run = subprocess.run(  # noqa: S603
            [docker_bin, "exec", name, "bash", "-c", drill],
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert run.returncode == 0, (
            "the documented restore drill FAILED end-to-end for a dump carrying a "
            f"vector(1536) column.\nstdout:\n{run.stdout}\nstderr:\n{run.stderr}"
        )
        out = run.stdout.split()
        assert "1" in out, f"the restored row is missing; drill output: {run.stdout!r}"
        assert "vector" in out, f"the vector extension did not survive the restore: {run.stdout!r}"
    finally:
        subprocess.run(  # noqa: S603
            [docker_bin, "rm", "-f", name], capture_output=True, text=True, timeout=120
        )
