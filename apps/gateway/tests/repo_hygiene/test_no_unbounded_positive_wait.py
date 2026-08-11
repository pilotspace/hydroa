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


def _is_fixed_sleep(stmt: ast.stmt) -> bool:
    """`await asyncio.sleep(<numeric literal greater than zero>)` as a statement on its own.

    A non-literal argument (`asyncio.sleep(interval)`) is a computed wait — a
    polling loop or a configured duration — and is not this defect.

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
    if not isinstance(arg, ast.Constant) or not isinstance(arg.value, int | float):
        return False
    return arg.value > 0  # CR v2: a zero sleep is a loop yield, not a guessed duration


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


def _violations() -> list[str]:
    found: list[str] = []
    for path in _iter_test_modules():
        source = path.read_text()
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover — ruff's problem, not ours
            continue
        lines = source.splitlines()
        rel = path.relative_to(GATEWAY_ROOT)

        for block in _blocks(tree):
            for index, stmt in enumerate(block):
                if not _is_fixed_sleep(stmt):
                    continue
                assertion = _asserts_after(block, index)
                if assertion is None:
                    continue  # not an assert-wait — a stub's latency, a loop tick
                if _declared(lines, stmt.lineno):
                    continue
                found.append(
                    f"{rel}:{stmt.lineno} — fixed sleep, then an assertion at "
                    f"line {assertion.lineno}, with no declared reason"
                )
    return sorted(found)


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
