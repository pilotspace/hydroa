"""Standing guard: a fixed sleep before an assertion must declare why it is fixed.

The shape this catches:

    await asyncio.sleep(0.15)
    rows = await session.execute(...)
    assert len(rows) >= 1

That passes on an idle laptop and fails on a loaded 4-vCPU runner where the
fire-and-forget write simply had not landed yet. It is the largest single flake
class in this suite — 252 sleep sites across 115 files at the time of writing,
and each full CI run surfaced a DIFFERENT subset of them, so the failure list
was never the population.

The fix is `tests/_polling.py::poll_until` — but ONLY for a positive wait. Where
the sleep exists to prove something NEVER happens ("a 403 writes no usage
record"), polling returns the instant the first row exists and never gives the
unwanted write a chance to appear. That converts a real assertion into a vacuous
one, which is strictly worse than the flake. `_polling.py`'s own docstring says
so.

So this guard does not ban fixed sleeps. It bans UNDECLARED ones: a fixed sleep
followed by an assertion must either become a bounded poll, or say in one line
why the duration is load-bearing. Both outcomes leave the next reader — and the
next sweep — able to tell the two apart without re-deriving the intent.

Grandfathering is by REASON, not by path: an allow-list of files would let a
copy-paste into a new file through, which is exactly how the sibling
`test_no_ddl_after_lifespan` violation spread.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = GATEWAY_ROOT / "tests"

# The polling primitive itself sleeps between attempts — that IS the bounded wait.
EXEMPT_FILES = frozenset({TESTS_ROOT / "_polling.py"})

# An inline declaration that the duration is deliberate. Must carry a reason.
#
# Anchored at the start of the line (indentation aside) so that a comment merely NAMING
# the marker cannot grandfather a fixed sleep six lines below it. Every real declaration
# already sits on its own line; the unanchored version accepted a mid-sentence mention,
# which is a hole in exactly the direction that matters — it would have let an undeclared
# sleep through silently rather than reporting it.
DECLARATION = re.compile(r"^\s*#\s*NEGATIVE WAIT:\s*\S+")

# How far after the sleep an assertion still counts as "the thing being waited for".
LOOKAHEAD_STATEMENTS = 12


def _iter_test_modules() -> list[Path]:
    return sorted(
        p
        for p in TESTS_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts and p not in EXEMPT_FILES
    )


def _module_numeric_constants(tree: ast.Module) -> dict[str, float]:
    """Module-level `NAME = <positive number>` bindings — nothing else.

    Deliberately shallow. Only assignments directly in the module body count, only to a
    plain name, and only from a numeric literal. A value that is computed, imported, or
    rebound inside a function is NOT resolved: this exists to stop a guessed duration
    hiding behind a constant, not to constant-fold the test suite.

    Rebinding at module level is treated as unresolvable (the name is dropped), because
    two bindings mean the value at the sleep site depends on order and this helper does
    not track order.
    """
    values: dict[str, float] = {}
    rebound: set[str] = set()
    for stmt in tree.body:
        if not isinstance(stmt, ast.Assign):
            continue
        if not isinstance(stmt.value, ast.Constant) or not isinstance(
            stmt.value.value, int | float
        ):
            continue
        if isinstance(stmt.value.value, bool):
            continue  # `FLAG = True` is not a duration
        for target in stmt.targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id in values:
                rebound.add(target.id)
            values[target.id] = float(stmt.value.value)
    return {k: v for k, v in values.items() if k not in rebound}


def _is_fixed_sleep(stmt: ast.stmt, constants: dict[str, float]) -> bool:
    """`await asyncio.sleep(<positive duration>)` as a statement on its own.

    A duration is either a numeric literal or a MODULE-LEVEL numeric constant. The
    constant case was added by todo #102: `_is_fixed_sleep` originally required a
    literal, so `await asyncio.sleep(_SETTLE_SECONDS)` bucketed as computed and was
    never asked for a declaration. Nothing was flaky then — both live named-constant
    sites were already declared — but naming a duration is the most natural way to
    write one, so the exemption was pointed at exactly the next site to be added.

    A LOCAL name (`asyncio.sleep(interval)`, `asyncio.sleep(hold_s)`) is still a
    computed wait: it is a polling loop's step or a caller-chosen stub latency, its
    value is not visible here, and resolving it would need dataflow analysis. Note the
    poll-loop case is doubly safe — a poll's sleep is the last statement in the loop
    body, so no assertion follows it in the same block either way.

    `asyncio.sleep(0)` is EXCLUDED (CR v2, 2026-08-10). This guard catches "someone
    guessed a duration and the guess fails under load"; a zero sleep has no duration
    to guess wrong. It is a single event-loop yield, and whether one yield suffices is
    a deterministic property of the callee — does it await internally? — not a timing
    race. The billing path's `asyncio.ensure_future(usage_recorder.record(...))` with
    an append-only test double has zero internal awaits, so it completes on its first
    scheduling step and `sleep(0)` is exactly enough, at any host load.

    If a zero-sleep site's target DOES perform real IO, one yield is insufficient — but
    that fails deterministically, on an idle laptop too, so it is a hard red rather than
    a member of the rotating flake tail this guard is scoped to. Tracked separately.
    """
    if not isinstance(stmt, ast.Expr):
        return False
    inner = stmt.value
    if not isinstance(inner, ast.Await):
        return False
    call = inner.value
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    is_sleep = (isinstance(func, ast.Attribute) and func.attr == "sleep") or (
        isinstance(func, ast.Name) and func.id == "sleep"
    )
    if not is_sleep or not call.args:
        return False
    arg = call.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, int | float):
        duration = float(arg.value)
    elif isinstance(arg, ast.Name) and arg.id in constants:
        duration = constants[arg.id]  # todo #102 — a named duration is still a duration
    else:
        return False
    return duration > 0  # CR v2: a zero sleep is a loop yield, not a guessed duration


def _asserts_after(block: list[ast.stmt], index: int) -> ast.Assert | None:
    """The first assertion in the LOOKAHEAD window after `index`, in the same block.

    Same-block only: an assertion inside a nested `for`/`with` after the sleep is
    still reached, so `ast.walk` over each following statement covers it, but a
    sleep in one test and an assertion in the next function never pair up.
    """
    for stmt in block[index + 1 : index + 1 + LOOKAHEAD_STATEMENTS]:
        for node in ast.walk(stmt):
            if isinstance(node, ast.Assert):
                return node
    return None


def _blocks(tree: ast.Module) -> list[list[ast.stmt]]:
    """Every statement list in the module — function bodies, loop bodies, with bodies."""
    found: list[list[ast.stmt]] = []
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                found.append(block)
    return found


def _declared(source_lines: list[str], sleep_line: int) -> bool:
    """Is there a `# NEGATIVE WAIT: <reason>` on the sleep line or the 6 above it?"""
    start = max(0, sleep_line - 7)
    window = source_lines[start:sleep_line]
    return any(DECLARATION.search(line) for line in window)


def _violations_in_source(source: str, label: str) -> list[str]:
    """Detection for ONE module, as text — so the predicate is testable without a fixture file.

    Extracted from `_violations()` when todo #102 widened `_is_fixed_sleep`: a guard whose
    logic can only be exercised by planting a file in `tests/` cannot be shown red for the
    case it was written to catch, and an unproven guard is the thing this suite distrusts
    most ([[guard-must-be-red-against-its-motivating-tree]]).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover — ruff's problem, not ours
        return []
    lines = source.splitlines()
    constants = _module_numeric_constants(tree)
    found: list[str] = []

    for block in _blocks(tree):
        for index, stmt in enumerate(block):
            if not _is_fixed_sleep(stmt, constants):
                continue
            assertion = _asserts_after(block, index)
            if assertion is None:
                continue  # not an assert-wait — a stub's latency, a loop tick
            if _declared(lines, stmt.lineno):
                continue
            found.append(
                f"{label}:{stmt.lineno} — fixed sleep, then an assertion at "
                f"line {assertion.lineno}, with no declared reason"
            )
    return found


def _violations() -> list[str]:
    found: list[str] = []
    for path in _iter_test_modules():
        rel = path.relative_to(GATEWAY_ROOT)
        found.extend(_violations_in_source(path.read_text(), str(rel)))
    return sorted(found)


_NAMED_CONSTANT_HIDES_A_GUESS = """
import asyncio

_SETTLE_SECONDS = 0.15

async def test_thing(session):
    await asyncio.sleep(_SETTLE_SECONDS)
    rows = await session.execute("select 1")
    assert len(rows) == 1
"""

_NAMED_CONSTANT_DECLARED = """
import asyncio

_SETTLE_SECONDS = 0.15

async def test_thing(session):
    # NEGATIVE WAIT: proving no SECOND row appears; polling would return on the first.
    await asyncio.sleep(_SETTLE_SECONDS)
    rows = await session.execute("select 1")
    assert len(rows) == 1
"""

_POLL_LOOP_INTERVAL = """
import asyncio, time

_POLL_INTERVAL_S = 0.05

async def _wait_for(fetch, expected, timeout=5.0):
    deadline = time.monotonic() + timeout
    row = await fetch()
    while row != expected and time.monotonic() < deadline:
        await asyncio.sleep(_POLL_INTERVAL_S)
        row = await fetch()
    assert row == expected
    return row
"""

_LOCAL_PARAMETER_INTERVAL = """
import asyncio

async def _hold(hold_s):
    await asyncio.sleep(hold_s)
    assert True
"""


def test_a_named_constant_sleep_is_not_invisible_to_the_guard() -> None:
    """ERR_NAMED_CONSTANT_WAIT — todo #102: a module constant must not launder a guess.

    `_is_fixed_sleep` originally required a numeric LITERAL, so
    `await asyncio.sleep(_SETTLE_SECONDS)` bucketed as a COMPUTED wait — the same
    bucket as a polling loop's `interval` — and was never asked for a declaration.
    Nothing was flaky at the time: both live named-constant sites were already
    declared. The hole is that the NEXT one would be silently exempt, and hiding a
    guessed duration behind a constant is the most natural way to write it.

    Red before the fix: the first case below produced no violation.
    """
    assert _violations_in_source(_NAMED_CONSTANT_HIDES_A_GUESS, "hides.py"), (
        "a sleep on a module-level numeric constant, followed by an assertion, with no "
        "declaration must be reported — otherwise naming the duration exempts it"
    )


def test_a_declared_named_constant_sleep_still_passes() -> None:
    """Widening the predicate must not invalidate the two real declared sites."""
    assert not _violations_in_source(_NAMED_CONSTANT_DECLARED, "declared.py")


def test_a_poll_loop_interval_constant_is_not_a_guessed_duration() -> None:
    """The critical non-regression: a poll interval is the OPPOSITE of this defect.

    A bounded poll already sleeps between attempts — that IS the correct pattern this
    guard steers people toward. Flagging it would tell readers to fix the fix, and a
    guard that cries wolf on the recommended idiom gets deleted rather than obeyed.
    """
    assert not _violations_in_source(_POLL_LOOP_INTERVAL, "poll.py")


def test_a_local_variable_duration_is_still_computed() -> None:
    """Only MODULE-LEVEL constants resolve. A parameter is genuinely computed.

    `async def _hold(hold_s)` is a stub whose latency the CALLER chose; the value is
    not visible here and may well be derived. Resolving locals would require dataflow
    analysis and would flag every configurable helper.
    """
    assert not _violations_in_source(_LOCAL_PARAMETER_INTERVAL, "local.py")


def test_fixed_sleep_before_an_assertion_is_declared_or_polled() -> None:
    """ERR_UNBOUNDED_WAIT — a fixed sleep before an assertion must be a poll or be reasoned.

    Two legitimate outcomes, and the choice is per-site:

    * POSITIVE wait ("the row appears") -> `tests._polling.poll_until` /
      `poll_for_count`. The assertion is unchanged; it just runs once the write has
      landed instead of once a guessed duration has elapsed.
    * NEGATIVE wait ("no second row is ever written") -> KEEP the sleep and add
      `# NEGATIVE WAIT: <why the duration is load-bearing>`. Do NOT convert it:
      polling would return on the first iteration and assert nothing.

    When the direction is genuinely unclear after reading the whole enclosing
    function, keep the sleep and declare it. A retained sleep on a positive wait
    costs one more flaky run; a converted negative wait costs a permanently dead
    assertion.
    """
    violations = _violations()
    assert not violations, (
        f"ERR_UNBOUNDED_WAIT — {len(violations)} fixed sleep(s) precede an assertion with no "
        "declared reason. Convert positive waits to tests._polling.poll_until; annotate "
        "negative waits with `# NEGATIVE WAIT: <reason>`:\n  " + "\n  ".join(violations)
    )
