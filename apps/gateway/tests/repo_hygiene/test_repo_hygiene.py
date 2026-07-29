"""Repo-hygiene gates — the checks that keep `make ci` honest.

Two kinds of test live here, and the difference matters.

*Exit-code gates* (`ruff`, `pyright`) shell out to the real tool rather than
re-implementing its judgment, which would drift the moment a rule is added.
They are red until the debt is cleared and green forever after.

*Standing guards* are the ones that still matter in six months. They fail on
the two dishonest ways this debt gets "cleared":

* loosening the linter instead of fixing the code — `select`/`ignore`, the
  pyright `report*` settings and `exclude` are pinned, and every
  per-file-ignores entry must carry a written justification;
* deleting an inconvenient test payload — the Cyrillic homoglyph in the
  Bedrock residency test is the *subject* of that test, and removing it would
  gut a security regression while leaving the suite green.
"""

from __future__ import annotations

import ast
import subprocess
import tomllib
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = GATEWAY_ROOT.parents[1]
PYPROJECT = GATEWAY_ROOT / "pyproject.toml"
ALLOWLIST = REPO_ROOT / ".add" / "dependencies.allowlist"
BEDROCK_GUARD_TEST = (
    GATEWAY_ROOT / "tests" / "bedrock_region_guard" / "test_bedrock_guard_verify2_dispatch_gate.py"
)

# Pinned at freeze. Changing these is a contract change, not a lint fix.
FROZEN_RUFF_SELECT = ["E", "F", "I", "UP", "B", "ASYNC", "S", "RUF"]
FROZEN_RUFF_IGNORE = ["S101"]
FROZEN_PYRIGHT_REPORTS = {
    "reportUnknownVariableType": False,
    "reportUnknownMemberType": False,
    "reportUnknownArgumentType": False,
    "reportUnknownLambdaType": False,
    "reportMissingTypeStubs": False,
}

# Re-pinned by CR v2 (2026-07-25): 49 pre-existing entries + the 63 frozen TEST
# files admitted when `ruff format --check` first ran (it had been short-circuited
# behind a failing `ruff check`). The list may SHRINK as no-edit contracts retire;
# it must never grow again without another contract change.
FROZEN_EXCLUDE_COUNT = 112

# CYRILLIC SMALL LETTER IE — the payload of
# test_unicode_confusable_prefix_never_reaches_bedrock_adapter.
CYRILLIC_IE = "е"  # noqa: RUF001 — this IS the character under guard, not a typo


def _pyproject() -> dict[str, object]:
    with PYPROJECT.open("rb") as handle:
        return tomllib.load(handle)


def _tool_ruff() -> dict[str, object]:
    tool = _pyproject()["tool"]
    assert isinstance(tool, dict)
    ruff = tool["ruff"]
    assert isinstance(ruff, dict)
    return ruff


def test_ruff_is_clean() -> None:
    """`make lint`'s ruff half must exit 0."""
    result = subprocess.run(
        # noqa: S607 — `uv` is resolved from the developer/CI PATH on purpose; this
        # is a repo-hygiene harness invoking the project's own dev toolchain, not a
        # service shelling out to an attacker-influenced binary.
        ["uv", "run", "ruff", "check", ".", "--output-format=concise"],  # noqa: S607
        cwd=GATEWAY_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"ruff findings remain:\n{result.stdout}"


def test_pyright_is_clean() -> None:
    """`make typecheck` must exit 0."""
    result = subprocess.run(
        ["uv", "run", "pyright"],  # noqa: S607 — same as above: project dev toolchain
        cwd=GATEWAY_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"pyright findings remain:\n{result.stdout}"


def test_allowlist_gate_passes() -> None:
    """Every dependency must be declared in the supply-chain allow-list.

    The gate this asserts had been red since #89 — four dependencies entered the
    tree without the justified entry `.add/dependencies.allowlist` demands.
    """
    result = subprocess.run(
        # noqa: S607 — `python3` from PATH; the script path is a repo-relative literal
        ["python3", "scripts/check_allowlist.py"],  # noqa: S607
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"allowlist gate failing:\n{result.stdout}"


def test_new_allowlist_entries_carry_a_justification() -> None:
    """The four dependencies added by this sweep must say why they are here.

    An allow-list entry with no reason records that someone typed a name, not
    that anyone made a decision — which is the whole control.
    """
    added = {"dnspython", "pgvector", "pytest-rerunfailures", "pytest-xdist"}
    unjustified: list[str] = []
    seen: set[str] = set()
    for raw in ALLOWLIST.read_text().splitlines():
        name = raw.split("#", 1)[0].strip()
        if name not in added:
            continue
        seen.add(name)
        if "#" not in raw or not raw.split("#", 1)[1].strip():
            unjustified.append(name)
    missing = added - seen
    assert not missing, f"not allowlisted at all: {sorted(missing)}"
    assert not unjustified, (
        f"allowlisted with no written justification: {sorted(unjustified)} — "
        "follow the python3-saml / reportlab convention"
    )


def test_linter_config_was_not_weakened() -> None:
    """A green bought by loosening the linter is not a green.

    Deliberately does NOT forbid new per-file-ignores entries — the repo's own
    convention uses them for proven false positives (two justified `S608`
    entries predate this task). It forces each to be justified in writing,
    which is the property that actually matters.
    """
    ruff = _tool_ruff()
    lint = ruff.get("lint")
    assert isinstance(lint, dict)

    assert lint.get("select") == FROZEN_RUFF_SELECT, (
        "ruff `select` changed — removing a rule is not a lint fix"
    )
    assert lint.get("ignore") == FROZEN_RUFF_IGNORE, (
        "ruff `ignore` changed — silencing a rule globally is not a lint fix"
    )

    tool = _pyproject()["tool"]
    assert isinstance(tool, dict)
    pyright = tool["pyright"]
    assert isinstance(pyright, dict)
    for key, expected in FROZEN_PYRIGHT_REPORTS.items():
        assert pyright.get(key) == expected, f"pyright setting {key} was relaxed"
    relaxed = [
        key
        for key in pyright
        if key.startswith("report") and key not in FROZEN_PYRIGHT_REPORTS and pyright[key] is False
    ]
    assert not relaxed, f"new pyright checks disabled: {relaxed}"

    per_file = lint.get("per-file-ignores")
    assert isinstance(per_file, dict)
    source = PYPROJECT.read_text()
    for pattern in per_file:
        line = next(
            (ln for ln in source.splitlines() if ln.lstrip().startswith(f'"{pattern}"')),
            None,
        )
        assert line is not None, f"could not locate per-file-ignores line for {pattern}"
        assert "#" in line and line.split("#", 1)[1].strip(), (
            f"per-file-ignores entry {pattern!r} carries no justification"
        )

    # Match the EXECUTABLE directive — one at the start of a line — not a
    # mention of it in prose. (This module discusses the directive by name; a
    # naive substring search flags itself, which is a test bug, not a finding.)
    # A directive carrying a written justification is allowed, on the same
    # reasoning as per-file-ignores; a BARE blanket suppression is not.
    unjustified_blanket: list[str] = []
    for path in GATEWAY_ROOT.rglob("*.py"):
        if ".venv" in path.parts:
            continue
        for line in path.read_text().splitlines():
            if not line.startswith("# ruff: noqa"):
                continue
            rest = line[len("# ruff: noqa") :].lstrip(": ")
            codes, _, reason = rest.partition(" ")
            if not codes or not reason.strip():
                unjustified_blanket.append(str(path.relative_to(GATEWAY_ROOT)))
    assert not unjustified_blanket, (
        "bare file-level `# ruff: noqa` (no codes, or no written reason) in "
        f"{unjustified_blanket} — suppress specific codes with a justification"
    )


def test_bedrock_homoglyph_payload_survives() -> None:
    """The Cyrillic `е` in the residency guard test is the TEST PAYLOAD.

    `test_unicode_confusable_prefix_never_reaches_bedrock_adapter` proves an
    EU-residency model id cannot slip past the catalog gate when an attacker
    substitutes U+0435 for Latin `e`. "Cleaning up the ambiguous character"
    would leave the test green while deleting the thing it tests. This guard
    makes that mistake loud.
    """
    source = BEDROCK_GUARD_TEST.read_text()
    assert f"{CYRILLIC_IE}u.anthropic" in source, (
        "the U+0435 homoglyph payload is gone from "
        f"{BEDROCK_GUARD_TEST.name} — the residency confusable test no longer "
        "tests a confusable. Restore it; suppress RUF001 per-line instead."
    )


def test_ruff_exclude_gained_no_new_path() -> None:
    """Excluding a file is not fixing it.

    The existing `exclude` list is frozen-test files with a documented
    no-edit contract; growing it to dodge a finding is `gate_weakened`.
    """
    excluded = _tool_ruff().get("exclude")
    assert isinstance(excluded, list)
    assert len(excluded) <= FROZEN_EXCLUDE_COUNT, (
        f"ruff `exclude` grew ({len(excluded)} > {FROZEN_EXCLUDE_COUNT}) — "
        "a new exclusion dodges a finding instead of fixing it"
    )

    # CR v2 rule (i): only TEST files may be excluded. An excluded `src/` file would
    # mean production code is exempt from formatting — a different thing entirely
    # from honouring another task's frozen no-edit contract.
    non_test = [path for path in excluded if not str(path).startswith("tests/")]
    assert not non_test, (
        f"non-test paths in ruff `exclude`: {non_test} — production code may not be "
        "exempted from formatting"
    )


# ---------------------------------------------------------------------------
# suite-stability: the per-test reset must stay cheap AND deterministic
# ---------------------------------------------------------------------------

CONFTEST = GATEWAY_ROOT / "tests" / "conftest.py"

# Every DDL form that can appear in a reset. The first version listed only
# drop_all/create_all/CREATE TABLE/DROP TABLE/ALTER SEQUENCE, and raw
# `ALTER TABLE` + `CREATE INDEX` in the app fixture sailed straight through it.
_DDL_TOKENS = (
    "drop_all",
    "create_all",
    "CREATE TABLE",
    "DROP TABLE",
    "ALTER SEQUENCE",
    "ALTER TABLE",
    "CREATE INDEX",
    "DROP INDEX",
    "CREATE TRIGGER",
    "DROP TRIGGER",
    "CREATE OR REPLACE",
    "TRUNCATE",
)


def _fixture_code(name: str) -> str:
    """Return the EXECUTABLE source of the `name` fixture — no docstring, no comments.

    Matching raw text finds DDL keywords in prose: the `app` fixture's own comment
    says "deliberately NOT `ALTER SEQUENCE ... RESTART`, which is DDL", and a
    substring search calls that a violation. Third time this module has had to
    separate what the code DOES from what it SAYS.
    """
    import ast

    tree = ast.parse(CONFTEST.read_text())
    node = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef | ast.FunctionDef) and n.name == name
        ),
        None,
    )
    assert node is not None, f"fixture {name!r} not found in conftest.py"
    body = list(node.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]  # drop the docstring
    # ast.unparse drops comments entirely — exactly what we want.
    return "\n".join(ast.unparse(stmt) for stmt in body)


def test_conftest_reset_does_no_ddl() -> None:
    """The per-test `app` fixture must not run schema DDL.

    A per-test `drop_all`/`create_all` over 56 tables takes an AccessExclusiveLock
    across pg_catalog; 4488 tests x N xdist workers serialise on the catalog and
    the run stalls. Schema creation belongs at session scope, once per worker.
    """
    body = _fixture_code("app")
    found = [token for token in _DDL_TOKENS if token in body]
    assert not found, (
        f"the per-test `app` fixture still performs DDL {found} — this is the "
        "pg_catalog contention that stalls the full suite (M1)"
    )


def test_schema_is_created_once_per_worker() -> None:
    """...and the DDL must still happen SOMEWHERE, at session scope.

    Guards the obvious way to make the test above pass dishonestly: delete the
    schema creation entirely.

    Parses the AST rather than matching text. A substring search finds
    `create_all` inside a session-scoped fixture's DOCSTRING (one mentions "its
    per-test create_all runs") and passes vacuously — the same prose-vs-code
    mistake this module already had to fix once.
    """
    import ast

    tree = ast.parse(CONFTEST.read_text())

    def _is_session_fixture(node: ast.AST) -> bool:
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            return False
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            for kw in decorator.keywords:
                if (
                    kw.arg == "scope"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value == "session"
                ):
                    return True
        return False

    def _calls_schema_ddl(node: ast.AST) -> bool:
        """True if the node's CODE (not its docstring) references create_all."""
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute) and sub.attr in {"create_all", "drop_all"}:
                return True
        return False

    session_creators = [
        node.name
        for node in ast.walk(tree)
        if _is_session_fixture(node) and _calls_schema_ddl(node)
    ]
    assert session_creators, (
        "no session-scoped fixture performs create_all — schema creation must MOVE "
        "to session scope, not disappear (M1)"
    )


def test_reset_is_not_stats_dependent() -> None:
    """Correctness must not rest on asynchronously-collected statistics.

    A `pg_stat_user_tables`-driven "only truncate dirty tables" reset is ~1.6x
    faster than the chosen DELETE sweep, and buys that with a silent-leak failure
    mode when the stats collector lags. This repo has already been bitten by
    cross-test contamination (todo #37).
    """
    # Bans the stats DEPENDENCY, not one view name: an earlier version checked only
    # `pg_stat_user_tables`, and the identical rejected design written against
    # `pg_stat_all_tables` sailed through it.
    source = CONFTEST.read_text()
    consulted = [
        view for view in ("pg_stat_user_tables", "pg_stat_all_tables", "pg_stat_") if view in source
    ]
    assert not consulted, (
        f"the reset consults {consulted} — stats are collected asynchronously, so a "
        "lagging counter silently skips a table (R:nondeterministic_reset)"
    )


def test_sequence_list_is_discovered_not_hardcoded() -> None:
    """Sequences to reset come from pg_sequences, not a literal list.

    A hand-maintained list drifts the moment a migration adds a sequence — the
    same cross-manifest drift class the EXPECTED_TABLES manifests keep hitting.

    Asserted against EXECUTABLE code (`ast.unparse`, which drops comments and
    docstrings). The substring version of this test was satisfiable by the
    fixture's own docstring: replacing the live query with a hardcoded list left
    the words "DISCOVERED from pg_sequences" in prose, and the guard stayed green.
    """
    tree = ast.parse(CONFTEST.read_text())
    chunks: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
            continue
        node.body = [
            stmt
            for stmt in node.body
            if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
        ]
        if node.body:
            chunks.append(ast.unparse(node))
    executable = "\n".join(chunks)
    assert "pg_sequences" in executable, (
        "the per-test reset does not DISCOVER sequences from pg_sequences in executable "
        "code — a comment or docstring mentioning it does not count (M6)"
    )


# ---------------------------------------------------------------------------
# suite-stability CR v2 — the HANG guards (M7, M8)
#
# A fixture that starts an app lifespan and never stops it leaks ~18
# `run_forever()` tasks (main.py:623-948). They keep the xdist worker's event
# loop alive forever, and the run stalls with no summary line — observed for
# 10h23m on 2026-07-26, with a `brpop` client still looping in Redis.
#
# `main.py` cancels every one of those tasks on shutdown, correctly. The bug is
# only ever that shutdown never RUNS.
# ---------------------------------------------------------------------------

TESTS_ROOT = GATEWAY_ROOT / "tests"

_LIFESPAN_ENTER = {"__enter__", "__aenter__"}
_LIFESPAN_EXIT = {"__exit__", "__aexit__"}

# Constructing one of these and ENTERING it is what starts the app lifespan, and
# therefore the ~18 run_forever() tasks. Deliberately narrow: an earlier draft of
# this guard flagged every `__enter__`/`__aenter__` in the tree and produced two
# false positives — a session-proxy class delegating `__aexit__`
# (file_search_tool/test_zdr_persist_recheck.py) and a plain DB-session entry
# (health_alerting:217). Neither starts a lifespan or a background task. A guard
# that cries wolf gets deleted, so it tracks lifespan starters only.
_LIFESPAN_STARTERS = {"TestClient", "LifespanManager"}


def _lifespan_bound_names(tree: ast.AST) -> set[str]:
    """Names bound to a `TestClient(...)` / `LifespanManager(...)` instance.

    Covers plain assignment, ANNOTATED assignment (`c: TestClient = TestClient(app)`)
    and tuple unpacking (`c, k = TestClient(app), 'k'`) — a scan that handled only
    `ast.Assign` with a bare `ast.Name` target missed all three.
    """
    names: set[str] = set()

    def _ctor_of(value: ast.expr) -> str | None:
        if not isinstance(value, ast.Call):
            return None
        func = value.func
        return func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)

    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            if (
                node.value is not None
                and _ctor_of(node.value) in _LIFESPAN_STARTERS
                and isinstance(node.target, ast.Name)
            ):
                names.add(node.target.id)
            continue
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            # tuple unpacking: pair each element with the matching RHS element
            if isinstance(target, ast.Tuple) and isinstance(node.value, ast.Tuple):
                for elt, val in zip(target.elts, node.value.elts, strict=False):
                    if isinstance(elt, ast.Name) and _ctor_of(val) in _LIFESPAN_STARTERS:
                        names.add(elt.id)
            elif isinstance(target, ast.Name) and _ctor_of(node.value) in _LIFESPAN_STARTERS:
                names.add(target.id)
    return names


def _manual_lifespan_enters(tree: ast.AST, tracked: set[str]) -> bool:
    """True when a tracked lifespan object is entered by hand.

    A `with` block uses the same protocol but is exception-safe by construction
    and emits no explicit Call node — so this matches exactly the hand-rolled
    cases, which are the only ones that can leak.
    """
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _LIFESPAN_ENTER
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in tracked
        for node in ast.walk(tree)
    )


def _exits_in_finally(tree: ast.AST, tracked: set[str]) -> bool:
    """True when a tracked lifespan object is exited from inside a `finally:`.

    `finally` is the whole point: an exit reached only after `yield` is skipped
    when a setup step between entry and yield raises, which is precisely the
    leak. Checked structurally — a comment claiming the fixture "manages
    entry/exit manually" must not be able to satisfy it.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try) or not node.finalbody:
            continue
        for stmt in node.finalbody:
            for inner in ast.walk(stmt):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr in _LIFESPAN_EXIT
                    and isinstance(inner.func.value, ast.Name)
                    and inner.func.value.id in tracked
                ):
                    return True
    return False


def test_no_fixture_enters_a_lifespan_without_exiting_it() -> None:
    """M7 / R:lifespan_leaked — every manual lifespan entry is finally-protected.

    Checked PER FUNCTION, not per module. A module-wide check passes as soon as
    ONE function in the file exits from a `finally`, which excuses every other
    function in it — and the two modules that already contain a compliant manual
    entry are exactly where the next one gets written.
    """
    offenders: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        module = ast.parse(path.read_text(encoding="utf-8"))
        for func in ast.walk(module):
            if not isinstance(func, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            tracked = _lifespan_bound_names(func)
            if not tracked or not _manual_lifespan_enters(func, tracked):
                continue
            if not _exits_in_finally(func, tracked):
                offenders.append(f"{path.relative_to(GATEWAY_ROOT)}::{func.name}")
    assert not offenders, (
        "these modules call __enter__/__aenter__ directly but never exit from a "
        f"`finally:` — a raise before `yield` leaks the app lifespan: {offenders}. "
        "A leaked lifespan keeps ~18 run_forever() tasks alive and hangs the whole "
        "xdist run with no summary line (M7, R:lifespan_leaked)"
    )


def test_hang_tripwire_is_armed() -> None:
    """M8 — an overrunning test must fail with a stack, not stall the run.

    Pinned to a literal rather than read back from the config under test: a
    test that computes its expectation from the file it is checking passes for
    any value, including a disabled one.
    """
    # The plugin must actually be INSTALLED. Without it `timeout = 300` is an
    # unknown ini key that pytest merely warns about (`--strict-config` is not in
    # addopts), so M8 could be fully disarmed with this test still green.
    import importlib.util

    assert importlib.util.find_spec("pytest_timeout") is not None, (
        "pytest-timeout is not installed — `timeout` in pyproject is then an inert "
        "unknown ini key and nothing bounds a hanging test (M8)"
    )
    config = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    # Declared as a DEV dependency (`[dependency-groups]`), which is correct for a
    # test-only plugin — check there as well as in project.dependencies.
    declared = " ".join(config["project"].get("dependencies", []))
    for group in config.get("dependency-groups", {}).values():
        declared += " " + " ".join(str(entry) for entry in group)
    assert "pytest-timeout" in declared, (
        "pytest-timeout is installed but not DECLARED — a fresh `uv sync` would "
        "silently disarm the tripwire (M8)"
    )
    pytest_ini = config["tool"]["pytest"]["ini_options"]
    timeout = pytest_ini.get("timeout")
    assert timeout is not None, (
        "no per-test timeout configured — run 2 of M3 stalled for 10h23m before a "
        "human noticed, and printed nothing naming the test (M8)"
    )
    assert isinstance(timeout, int) and 0 < timeout <= 900, (
        f"timeout={timeout!r} is not a bounded positive number of seconds (M8)"
    )


# ---------------------------------------------------------------------------
# suite-stability CR v3 — ONE CLOCK owns a timestamp column (M9)
#
# `created_at`/`updated_at` carry `server_default=func.now()` — the POSTGRES
# clock, where `now()` is transaction-START time. Writing a bump from
# `datetime.now(tz=UTC)` mixes in the APPLICATION clock, and whenever the app
# host trails the database host the bump moves the timestamp BACKWARDS. Observed
# 22 ms backwards on 2026-07-28, and it is not cosmetic: conversations indexes
# `updated_at DESC` for the list view, so a just-touched row sorts below
# untouched ones.
# ---------------------------------------------------------------------------

SRC_ROOT = GATEWAY_ROOT / "src" / "gateway"
_CLOCK_OWNED_COLUMNS = {"created_at", "updated_at"}

# `updated_at` columns that predate the onupdate rule (M9), per file. MAY SHRINK,
# NEVER GROW — same convention as FROZEN_EXCLUDE_COUNT above. tenants carries three:
# two with written reasons in the ORM (a `plans` row nothing updates after seeding,
# and one declared without onupdate to match its migration DDL).
_ONUPDATE_BASELINE = {
    "src/gateway/catalog/infrastructure/orm.py": 1,
    "src/gateway/credits/infrastructure/orm.py": 1,
    "src/gateway/tenants/infrastructure/orm.py": 3,
}


def _is_func_now(node: ast.expr) -> bool:
    """True for `func.now()` (and `sa.func.now()`), the Postgres-clock call."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "now"
        and isinstance(node.func.value, ast.Attribute | ast.Name)
        and getattr(node.func.value, "attr", getattr(node.func.value, "id", "")) == "func"
    )


def _is_app_clock(node: ast.expr) -> bool:
    """True for `datetime.now(...)` / `datetime.utcnow()` — the APPLICATION clock."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"now", "utcnow"}
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "datetime"
    )


def test_timestamp_columns_have_one_clock_owner() -> None:
    """M9 — infrastructure writes these columns from the DB clock, never the app clock.

    Two narrowings, both learned by getting it wrong first:

    * scoped to `*/infrastructure/` — an API module may legitimately put an
      `updated_at` STRING in a response body (credits/api/router.py does), which
      is not a column write at all;
    * flags only values that come from the APPLICATION CLOCK. A first draft
      flagged everything that was not `func.now()` and produced 40 hits, almost
      all of them `created_at=row.created_at` — reading a stored value back into
      a domain object, which is exactly right. A guard that cries wolf gets
      deleted rather than obeyed.

    So the rule is precise: `datetime.now(...)`/`utcnow()` — directly, or via a
    local bound to one — must not be written into a DB-clock-owned column.
    `text('now()')` and `func.now()` are the DB clock and are fine.
    """
    offenders: list[str] = []
    # rglob, not glob: the single-level version could not see a nested
    # `<module>/<sub>/infrastructure/` package at all.
    for path in sorted(SRC_ROOT.rglob("infrastructure/*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # Names bound to an app-clock value, INCLUDING parameters named for a
        # clock: compliance/repository.py took `now: datetime` as a parameter and
        # wrote it straight into the column, which a value-only scan cannot see.
        app_clock_names = {
            target.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign) and _is_app_clock(node.value)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                app_clock_names |= {
                    arg.arg
                    for arg in [*node.args.args, *node.args.kwonlyargs]
                    if arg.arg in {"now", "utcnow", "timestamp"}
                }

        # Names that are spread into a call as `**name` — i.e. dicts that really are
        # column-value maps, not raw-SQL bind parameters. See branch (d) below.
        spread_names = {
            kw.value.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for kw in node.keywords
            if kw.arg is None and isinstance(kw.value, ast.Name)
        }

        def _is_offending(value: ast.expr, names: set[str] = app_clock_names) -> bool:
            return _is_app_clock(value) or (isinstance(value, ast.Name) and value.id in names)

        for node in ast.walk(tree):
            # (a) keyword argument: `.values(updated_at=...)`, `Row(updated_at=...)`
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg in _CLOCK_OWNED_COLUMNS and _is_offending(kw.value):
                        offenders.append(
                            f"{path.relative_to(GATEWAY_ROOT)}:{kw.value.lineno} "
                            f"{kw.arg}={ast.unparse(kw.value)}"
                        )
            # (b) attribute assignment: `existing.updated_at = now`
            # (c) dict-subscript assignment: `values["updated_at"] = now`, later **values
            elif isinstance(node, ast.Assign) and _is_offending(node.value):
                for target in node.targets:
                    name = None
                    if isinstance(target, ast.Attribute):
                        name = target.attr
                    elif isinstance(target, ast.Subscript) and isinstance(
                        target.slice, ast.Constant
                    ):
                        name = target.slice.value
                    if name in _CLOCK_OWNED_COLUMNS:
                        offenders.append(
                            f"{path.relative_to(GATEWAY_ROOT)}:{node.lineno} "
                            f"{ast.unparse(target)}={ast.unparse(node.value)}"
                        )
            # (d) dict LITERAL later spread into a call: `values = {"updated_at": now}`
            # ... `.values(**values)`. Added after this guard missed a live violation —
            # finetune's apply_poll_result built exactly this shape, and (c) only sees
            # the SUBSCRIPT form written on a later line.
            #
            # Restricted to names that ARE spread with `**`, because the first version
            # flagged every dict literal with an `updated_at` key and immediately cried
            # wolf: mcp_connector passes `{"updated_at": ...}` as raw-SQL BIND PARAMETERS
            # to `session.execute(text(...), params)`. That column
            # (`mcp_allowed_servers_updated_at`) is `DEFAULT NULL` — no server clock, so
            # the app clock is its sole writer and there is nothing to mix. Fourth guard
            # in this task to need narrowing before it was trustworthy.
            elif isinstance(node, ast.Assign | ast.AnnAssign) and isinstance(node.value, ast.Dict):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if not any(isinstance(t, ast.Name) and t.id in spread_names for t in targets):
                    continue
                for key, value in zip(node.value.keys, node.value.values, strict=True):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value in _CLOCK_OWNED_COLUMNS
                        and _is_offending(value)
                    ):
                        offenders.append(
                            f"{path.relative_to(GATEWAY_ROOT)}:{value.lineno} "
                            f"{key.value!r}: {ast.unparse(value)}"
                        )
    assert not offenders, (
        "these infrastructure writes set a timestamp column from the APPLICATION "
        f"clock while its server_default is the POSTGRES clock: {offenders}. "
        "A bump can then land EARLIER than the value it replaces and invert "
        "`ORDER BY updated_at DESC` (M9)"
    )


def test_updated_at_columns_declare_onupdate() -> None:
    """M9 — the DB clock can only OWN `updated_at` if the column declares `onupdate`.

    This is the other half of the guard above, and without it that guard is a trap.
    The fix for an app-clock write is to stop writing the column and let the DB do
    it; that is only correct when the column carries `onupdate=func.now()`. Remove
    the write from a column that lacks it and `updated_at` silently FREEZES at its
    insert value — a worse bug than the 22 ms inversion, and one no test would
    notice. `compliance` was exactly this case and had to have `onupdate` added
    before its explicit bump could go.

    Why not simply ban every explicit `updated_at=...` write instead: one is
    legitimate. `conversations.add_message` bumps the parent row and has no other
    column to SET, so `.values(updated_at=func.now())` is the whole statement.

    Five columns predate this rule and are BASELINED below rather than changed —
    they live in modules this task never touched, and two carry written reasons
    (a `plans` row nothing updates after seeding; one declared to match its
    migration DDL). The baseline may SHRINK, never grow: a NEW column that forgets
    `onupdate` fails here, which is the property worth having.

    A note for whoever writes the next one: a SQL function in `.values()` is NOT
    Python-evaluatable, so `synchronize_session` cannot update the in-session row and
    EXPIRES it instead. Any SYNC attribute read afterwards then attempts IO and raises
    `MissingGreenlet`. That is precisely how the first version of this M9 fix broke
    three finetune tests: the router's `_job_object` is a plain `def` reading
    `row.status`/`row.finished_at`. Prefer letting `onupdate` do it silently.
    """
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.rglob("infrastructure/orm.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AnnAssign | ast.Assign):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {t.id for t in targets if isinstance(t, ast.Name)}
            if "updated_at" not in names:
                continue
            call = node.value
            if not isinstance(call, ast.Call):
                continue
            kwargs = {kw.arg for kw in call.keywords}
            if "server_default" in kwargs and "onupdate" not in kwargs:
                offenders.append(f"{path.relative_to(GATEWAY_ROOT)}:{node.lineno}")

    # Counted per FILE, not pinned per line: line numbers move with unrelated edits,
    # but a NEW forgotten column raises its file's count and fails.
    counts: dict[str, int] = {}
    for entry in offenders:
        counts[entry.rsplit(":", 1)[0]] = counts.get(entry.rsplit(":", 1)[0], 0) + 1
    grew = {
        path: (count, _ONUPDATE_BASELINE.get(path, 0))
        for path, count in counts.items()
        if count > _ONUPDATE_BASELINE.get(path, 0)
    }
    assert not grew, (
        f"these `updated_at` columns take their INSERT value from the Postgres clock "
        f"but declare no `onupdate`, so nothing bumps them on UPDATE unless a caller "
        f"writes the column by hand: {grew} (file -> (found, allowed)). "
        f"Add `onupdate=func.now()` (M9). All offenders: {offenders}"
    )


def test_test_installed_ddl_is_dropped() -> None:
    """M2 / R:isolation_broken — a test that installs DDL must remove it.

    `create_all` does not replay the migrations' triggers, so several suites
    install them by hand. That was harmless while the schema was rebuilt for
    EVERY test: `drop_all` took the trigger with it. The schema is now built once
    per xdist worker, so a trigger left behind changes the behaviour of every
    later test on that worker.

    This is not hypothetical. It made
    `tests/audit_export::test_export_purge_mid_export_is_silent_honest_gap` fail
    with `audit_immutable_violation` when it happened to land on a worker that
    had already run `tests/audit::test_db_enforced_immutability` — and pass on
    its own. `--dist loadscope` assigns modules to workers dynamically, so it
    presents as a 1-in-N flake rather than a stable red.

    DELETE cannot undo DDL, so the DDL has to undo itself.
    """
    offenders: list[str] = []
    for path in sorted(TESTS_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        if "CREATE TRIGGER" not in source and "CREATE OR REPLACE TRIGGER" not in source:
            continue
        if "DROP TRIGGER" not in source:
            offenders.append(str(path.relative_to(GATEWAY_ROOT)))
    assert not offenders, (
        f"these test modules install a TRIGGER and never drop it: {offenders}. "
        "The schema is built once per worker now, so it outlives the test and "
        "silently changes every later test on that worker (M2, R:isolation_broken)"
    )
