"""Detect the absolute-seed / relative-window pairing that makes a test a date bomb.

date-bomb-sweep PLAN.md §3 (FROZEN @ v2). See tests/date_bomb_sweep for the suite.

The hazard: a test seeds a row at a hardcoded `datetime(2026, 7, 15, ...)` and then queries
an endpoint with a bare `window=month`, whose bounds come from the WALL CLOCK
(`usage/api/router.py::_compute_window_bounds` — the one wall-clock window resolver in the
codebase; every other consumer imports it). The two agree until the clock leaves the seeded
month, and then `make ci` goes red one morning with zero commits behind it, looking exactly
like a code regression. That happened on 2026-08-01 and was fixed in PR #92.

What this deliberately does NOT do: flag hardcoded dates. 77 test files carry a 2026
literal and nearly all of them are correct — `tests/margin_dashboard`'s `INSIDE` pairs with
an absolute `start=`/`end=` window and is right. A blanket rule would flag them all and
push someone to revert PR #92's own fix. The signal is the PAIRING or it is nothing.

The pairing is scoped to a single FUNCTION BODY, not to a module (§3 CR-1). Two unrelated
tests that happen to share a file are not a date bomb, and the module-scoped rule this
started with flagged the very file PR #92 had already fixed. Verified in both directions
against the real history: the detector fires on all six functions at `8074d8d^` and is
silent on the fixed tree.

Known limits, accepted at freeze: a seed built indirectly (a helper, a `timedelta` off a
literal, a fixture two packages away) slips past. That is a FALSE NEGATIVE, and a net over
a hazard that has no net at all today is still worth having. The direction that would hurt
is a false positive, which is why the silent arms are gated against the real files by name.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

#: A `datetime(YYYY, ...)` / `datetime.datetime(YYYY, ...)` call with an explicit year.
_ABSOLUTE_SEED = re.compile(r"\bdatetime\s*\(\s*(\d{4})\s*,")

#: A relative window token in a request path, a params dict, or a parametrize table.
_RELATIVE_WINDOW = re.compile(r"""window['"]?\s*[=:]\s*['"]?(month|week|day)\b""")

#: Anything that pins the window to fixed instants, making a nearby seed safe.
_ABSOLUTE_WINDOW = re.compile(r"""\b(start|end|period|start_date|end_date)['"]?\s*[=:]""")

_Scope = ast.FunctionDef | ast.AsyncFunctionDef | ast.Module

_REMEDY = (
    "keep BOTH constants — an absolute one for start=/end=/period= windows, and a "
    "current-month one for a bare window=. See tests/margin_dashboard/conftest.py's "
    "INSIDE / INSIDE_CURRENT_MONTH pair (PR #92)."
)


def _strip_docstrings(tree: ast.AST) -> None:
    """Drop docstrings in place. Prose describing the hazard is not the hazard.

    Comments need no handling — `ast.unparse` never emits them. Docstrings survive as
    real string expressions, and this module's own §4 suite plus margin_dashboard's
    conftest both explain the bomb in prose that matches both halves of the pattern.
    """
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                body.pop(0)


def _is_absolute_datetime_call(node: ast.AST) -> bool:
    """A `datetime(YYYY, ...)` CALL — not the same four characters inside a string.

    Regex over source text cannot tell the two apart, and this guard's own §4 suite quotes
    `datetime(2026, 7, 15, ...)` inside an assertion message. An AST Call node is the
    hazard; prose describing the hazard is not.
    """
    if not isinstance(node, ast.Call):
        return False
    if not ast.unparse(node.func).split(".")[-1] == "datetime":
        return False
    if not node.args:
        return False
    first = node.args[0]
    return isinstance(first, ast.Constant) and isinstance(first.value, int) and first.value > 1900


def _has_absolute_seed_call(node: ast.AST) -> bool:
    return any(_is_absolute_datetime_call(child) for child in ast.walk(node))


def _absolute_seed_names(text: str) -> set[str]:
    """Module-level constants bound to a datetime literal with an explicit year."""
    try:
        tree = ast.parse(text)
    except SyntaxError:  # pragma: no cover - a broken module is not this guard's problem
        return set()

    names: set[str] = set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if value is None or not _has_absolute_seed_call(value):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        names.update(t.id for t in targets if isinstance(t, ast.Name))
    return names


def _references_name(scope: ast.AST, names: frozenset[str] | set[str]) -> bool:
    """Does this scope USE one of the sibling-exported seed constants?

    An `ast.Name` load only — a name inside a string or a comment is prose, and `import X`
    binds the name without seeding anything with it.
    """
    for node in ast.walk(scope):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Name) and node.id in names:
            return True
    return False


def _scopes(tree: ast.Module) -> list[tuple[str, _Scope]]:
    """Every function body, plus module level as one implicit scope.

    A function is listed with its OWN nested functions included — a helper defined inside
    a test still belongs to that test — but a top-level function's body is not merged with
    module level, which is what keeps unrelated tests in one file from pairing up.
    """
    found: list[tuple[str, _Scope]] = [("<module>", tree)]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.append((node.name, node))
    return found


def _scope_source(scope: _Scope) -> str:
    """The scope's own statements, unparsed — for Module, excluding function bodies."""
    if isinstance(scope, ast.Module):
        statements = [
            node
            for node in scope.body
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        return "\n".join(ast.unparse(node) for node in statements)
    return "\n".join(ast.unparse(node) for node in scope.body)


def _line_of(text: str, pattern: re.Pattern[str], source_lines: list[str]) -> tuple[int, str]:
    """Best-effort map of a match back to a line in the ORIGINAL source, for the message."""
    match = pattern.search(text)
    fragment = match.group(0) if match else ""
    for number, line in enumerate(source_lines, start=1):
        if fragment and fragment in line:
            return number, line.strip()
    return 0, fragment


def scan_source(
    text: str, *, imported_names: frozenset[str] | set[str] = frozenset()
) -> str | None:
    """Return None if clean, else a reason naming BOTH halves of the pairing.

    One reason — the FIRST pairing found. `scan_tree` reports every pairing in a file;
    a module with three bombs must not look like a module with one.

    `imported_names` are absolute-seed constants this module may have imported from a
    sibling — see `scan_tree`, which resolves them per directory. Without it, the
    margin_dashboard shape (constant in the package conftest, query in the test module)
    reads as clean in both files.
    """
    findings = scan_all(text, imported_names=imported_names)
    return findings[0] if findings else None


def scan_all(text: str, *, imported_names: frozenset[str] | set[str] = frozenset()) -> list[str]:
    """Every absolute-seed/relative-window pairing in `text`, one reason per scope."""
    try:
        tree = ast.parse(text)
    except SyntaxError:  # pragma: no cover - a broken module is not this guard's problem
        return []
    _strip_docstrings(tree)

    seed_names = imported_names | _absolute_seed_names(text)
    uses_name = (
        re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(seed_names)) + r")\b")
        if seed_names
        else None
    )
    source_lines = text.splitlines()

    reasons: list[str] = []
    for scope_name, scope in _scopes(tree):
        body = _scope_source(scope)

        window = _RELATIVE_WINDOW.search(body)
        if window is None or _ABSOLUTE_WINDOW.search(body):
            continue  # no bare relative window here, or it is pinned in this same scope

        has_literal = _has_absolute_seed_call(scope)
        has_named_seed = _references_name(scope, seed_names) if seed_names else False
        if not (has_literal or has_named_seed):
            continue

        seed_pattern = _ABSOLUTE_SEED if has_literal else uses_name
        assert seed_pattern is not None
        seed_no, seed_text = _line_of(body, seed_pattern, source_lines)
        window_no, window_text = _line_of(body, _RELATIVE_WINDOW, source_lines)
        where = "module level" if scope_name == "<module>" else f"`{scope_name}`"
        reasons.append(
            f"in {where}: an absolute seed (line {seed_no}: `{seed_text}`) is paired with "
            f"a wall-clock relative window (line {window_no}: `{window_text}`). They agree "
            f"until the clock leaves the seeded month, then this test fails on a date, not "
            f"on a commit. Remedy: {_REMEDY}"
        )
    return reasons


def scan_tree(root: Path, *, skip_fixtures: bool = True) -> list[tuple[Path, str]]:
    """Scan every ``*.py`` under `root`, following sibling seed imports one level.

    `skip_fixtures=True` (the default) ignores any path with a `fixtures/` segment. The
    planted bomb modules that prove this detector fires live there, and a guard that
    flagged its own evidence would be permanently red. The §4 suite passes False to aim
    the detector AT them.
    """
    paths = sorted(p for p in root.rglob("*.py") if p.name != Path(__file__).name)
    if skip_fixtures:
        paths = [p for p in paths if "fixtures" not in p.parts]

    sources = {path: path.read_text(encoding="utf-8") for path in paths}
    # Absolute-seed constants exported by each directory, so a sibling import resolves.
    exported: dict[Path, set[str]] = {}
    for path, text in sources.items():
        exported.setdefault(path.parent, set()).update(_absolute_seed_names(text))

    findings: list[tuple[Path, str]] = []
    for path, text in sources.items():
        for reason in scan_all(text, imported_names=exported.get(path.parent, set())):
            findings.append((path, reason))
    return findings


__all__ = ["scan_all", "scan_source", "scan_tree"]
