"""Standing guard: no test drops or creates schema after the app lifespan starts.

`Base.metadata.drop_all` needs an AccessExclusiveLock on every table. A running
app lifespan has background workers — the usage flusher, the catalog refresh
scheduler, the vector-store ingest worker — holding AccessShareLock on those
same tables. Issue the DDL after `__enter__()` and Postgres resolves the
standoff by killing one side:

    asyncpg.exceptions.DeadlockDetectedError: deadlock detected
    DETAIL: Process 13408 waits for AccessExclusiveLock on relation 351474;
            blocked by process 13394.
            Process 13394 waits for AccessShareLock on relation 351166;
            blocked by process 13408.

`tests/realtime/test_realtime_ws.py` already found this, fixed it by moving the
DDL ahead of `__enter__`, and wrote the reason down in its own docstring. That
did not stop `tests/preset_capability_validation` from copying the bootstrap
shape WITHOUT the fix, which cost CI run 31356301036 two of its four failures.

A comment is not a guard. This is the guard.

WHY A CALL-GRAPH HOP: the violation is rarely a bare `drop_all` sitting in the
test body. In `preset_capability_validation` it was three frames down — the test
called `_bootstrap_and_signup`, which defined a nested `_bootstrap`, which held
the `drop_all`. A guard that only walked the test function's own statements
would have reported a clean tree while the deadlock stayed in it. So this scans
each module for which of ITS functions perform DDL (transitively, to a
fixpoint), then asks whether a lifespan-started function reaches one of them.
"""

from __future__ import annotations

import ast
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = GATEWAY_ROOT / "tests"

# The metadata calls that take AccessExclusiveLock on every mapped table.
DDL_ATTRS = frozenset({"drop_all", "create_all"})

# `with TestClient(...)` and the manual `tc.__enter__()` both start the lifespan.
LIFESPAN_ENTER_ATTR = "__enter__"
TEST_CLIENT_NAMES = frozenset({"TestClient"})


def _iter_test_modules() -> list[Path]:
    return sorted(p for p in TESTS_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _functions(tree: ast.Module) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function in the module, nested ones included, keyed by name.

    Names are flat: a nested `_bootstrap` is reachable by that name. Collisions
    across scopes are acceptable here — this guard errs toward reporting, and a
    false name match can only make it MORE conservative, never less.
    """
    found: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            found[node.name] = node
    return found


def _performs_ddl_directly(fn: ast.AST) -> bool:
    return any(isinstance(node, ast.Attribute) and node.attr in DDL_ATTRS for node in ast.walk(fn))


def _called_names(fn: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name):
            names.add(node.func.id)
        elif isinstance(node.func, ast.Attribute):
            # `tc.portal.call(_bootstrap)` passes the DDL function as an ARGUMENT
            # rather than calling it — the portal invokes it. Treat any function
            # named in an argument position as reached.
            names.add(node.func.attr)
        for arg in node.args:
            if isinstance(arg, ast.Name):
                names.add(arg.id)
    return names


def _ddl_functions(functions: dict[str, ast.FunctionDef | ast.AsyncFunctionDef]) -> set[str]:
    """Names of functions that reach DDL, directly or through a local call. Fixpoint."""
    ddl = {name for name, fn in functions.items() if _performs_ddl_directly(fn)}
    changed = True
    while changed:
        changed = False
        for name, fn in functions.items():
            if name in ddl:
                continue
            if _called_names(fn) & ddl:
                ddl.add(name)
                changed = True
    return ddl


def _lifespan_start_line(fn: ast.AST) -> int | None:
    """Line at which this function starts an app lifespan, or None.

    Two shapes count, and they are asked in ascending-line order so the EARLIEST
    entry wins — DDL is safe only if it precedes every entry in the function.
    """
    lines: list[int] = []
    for node in ast.walk(fn):
        # tc.__enter__()
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == LIFESPAN_ENTER_ATTR
        ):
            lines.append(node.lineno)
        # with TestClient(...) as tc:  — the body runs inside the lifespan, so the
        # entry line is the `with` itself.
        if isinstance(node, ast.With | ast.AsyncWith):
            for item in node.items:
                call = item.context_expr
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Name)
                    and call.func.id in TEST_CLIENT_NAMES
                ):
                    lines.append(node.lineno)
    return min(lines) if lines else None


def _ddl_lines_after(fn: ast.AST, after: int, ddl_names: set[str]) -> list[int]:
    """Lines at which `fn` reaches DDL strictly after line `after`."""
    hits: list[int] = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Attribute) and node.attr in DDL_ATTRS and node.lineno > after:
            hits.append(node.lineno)
        if isinstance(node, ast.Call) and node.lineno > after:
            if isinstance(node.func, ast.Name) and node.func.id in ddl_names:
                hits.append(node.lineno)
            for arg in node.args:
                if isinstance(arg, ast.Name) and arg.id in ddl_names:
                    hits.append(node.lineno)
    return sorted(set(hits))


def _violations() -> list[str]:
    found: list[str] = []
    for path in _iter_test_modules():
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:  # pragma: no cover — a syntax error is ruff's problem, not ours
            continue

        functions = _functions(tree)
        if not functions:
            continue
        ddl_names = _ddl_functions(functions)
        if not ddl_names:
            continue

        rel = path.relative_to(GATEWAY_ROOT)
        for name, fn in functions.items():
            entry = _lifespan_start_line(fn)
            if entry is None:
                continue
            # A nested DDL helper DEFINED after the entry line but only ever
            # invoked through the portal still executes inside the lifespan, so
            # `_ddl_lines_after` deliberately counts the call site, not the def.
            hits = _ddl_lines_after(fn, entry, ddl_names)
            if hits:
                lines = ", ".join(str(h) for h in hits)
                found.append(
                    f"{rel}::{name} — lifespan starts at line {entry}, "
                    f"schema DDL reached at line(s) {lines}"
                )
    return sorted(found)


def test_no_test_runs_ddl_after_lifespan_entry() -> None:
    """ERR_DDL_AFTER_LIFESPAN — drop_all/create_all must precede every lifespan start.

    The fix is always the same and always cheap: hoist the schema bootstrap above
    `tc.__enter__()` / the `with TestClient(...)`, exactly as
    tests/realtime/test_realtime_ws.py does. Do not add a retry, and do not widen
    a lock timeout — those make the deadlock rarer, not absent.
    """
    violations = _violations()
    assert not violations, (
        "ERR_DDL_AFTER_LIFESPAN — schema DDL issued while an app lifespan is running "
        "will deadlock against the lifespan's own background workers.\n"
        "Hoist the DDL above the lifespan start (see tests/realtime/test_realtime_ws.py):\n  "
        + "\n  ".join(violations)
    )
