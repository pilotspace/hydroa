"""Standing guard: a quiescence settle must take an expected count (CR v3, class 9).

The "wait until it stops changing" settle is the most convincing wrong wait in this
codebase, because it looks like the careful alternative to a fixed sleep — and
`tests/cost_attribution_tags` had one with a docstring explaining the exact
`asyncio.ensure_future` dispatch race it was written to close.

It polled `XLEN` every 10 ms and returned once the length was UNCHANGED for three
consecutive samples. A stream sitting at 0 is trivially unchanged, so quiescence was
declared after ~40 ms, `flush_once()` drained an empty stream, and the assertion read an
empty table:

    assert [] == [{'Team': 'a', 'team': 'b'}]

Stability cannot distinguish "everything arrived" from "nothing has started". Its docstring
sold the count-free design as the feature — "scales to any number of in-flight dispatches
without hardcoding a count" — and that flexibility was the defect: without an expected
count there is no floor below which stability is meaningless.

So any settle that watches a length for stability must take the count its caller's assertion
depends on, and start the quiet window at `length >= expect`. `expect=0` is the negative
case and needs a different mechanism entirely: nothing is coming, so only elapsed time can
show a stray write.

Covers CR v3 class 9.
"""

from __future__ import annotations

import ast
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = GATEWAY_ROOT / "tests"

# The shape: a helper that reads a length/count in a loop and returns on repetition.
LENGTH_READS = ("xlen", "llen", "scard", "zcard")
STABILITY_NAMES = ("stable", "quiet", "unchanged", "settle")
COUNT_PARAMS = ("expect", "expected", "min_length", "at_least", "count")


def _iter_test_modules() -> list[Path]:
    return sorted(p for p in TESTS_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _reads_a_length(fn: ast.AST) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr.lower() in LENGTH_READS:
                return True
    return False


def _has_a_loop(fn: ast.AST) -> bool:
    return any(isinstance(n, ast.While | ast.For | ast.AsyncFor) for n in ast.walk(fn))


def _tracks_stability(fn: ast.AST, body: str) -> bool:
    """Does this function decide it is done by watching for a value to REPEAT?"""
    lowered = body.lower()
    return any(name in lowered for name in STABILITY_NAMES)


def _takes_a_count(fn: ast.AsyncFunctionDef | ast.FunctionDef) -> bool:
    args = [a.arg for a in fn.args.args + fn.args.kwonlyargs]
    return any(p in args for p in COUNT_PARAMS)


def _violations() -> list[str]:
    found: list[str] = []
    for path in _iter_test_modules():
        source = path.read_text()
        if not any(read in source.lower() for read in LENGTH_READS):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover — ruff's problem, not ours
            continue
        rel = path.relative_to(GATEWAY_ROOT)

        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            body = ast.get_source_segment(source, node) or ""
            if not (_reads_a_length(node) and _has_a_loop(node)):
                continue
            if not _tracks_stability(node, body):
                continue  # polls for a CONDITION, not for stability — a different shape
            if _takes_a_count(node):
                continue
            found.append(
                f"{rel}:{node.lineno} — {node.name} decides it is done when a length STOPS "
                f"CHANGING, but takes no expected count. A length of 0 is trivially stable, "
                f"so this returns before the first write lands"
            )
    return sorted(found)


def test_quiescence_settle_takes_a_count() -> None:
    """CR v3 class 9 — stability is only meaningful above an expected floor.

    Take the count the caller's assertion depends on and begin the quiet window at
    `length >= expect`. For the negative case (`expect == 0`) stability proves nothing at
    all; use a declared elapsed window instead.
    """
    violations = _violations()
    assert not violations, f"{len(violations)} stability-only settle(s) found:\n  " + "\n  ".join(
        violations
    )
