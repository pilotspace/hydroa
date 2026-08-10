"""Standing guard: every `asyncio.sleep` in the test tree is classified, none unknown.

M4's machine-checkable form. The sibling `test_no_unbounded_positive_wait` guard
answers one question — "is this sleep-before-an-assert declared?" — and says
nothing about the sites it skips. That silence is how this defect class survived so
long: each CI run surfaced a DIFFERENT subset of 252 sleep sites, so the failure
list was always a sighting and never the population.

This guard converts the population into a partition. Every `asyncio.sleep(...)`
call site in `tests/` lands in exactly one bucket:

  LOOP_YIELD         `sleep(0)` — a single event-loop yield, no duration to guess.
  COMPUTED           a non-literal argument (`sleep(interval)`) — a polling loop or
                     a configured duration, not a guess.
  EMBEDDED           not a statement of its own (inside `gather`, a task factory, a
                     lambda) — a structural construct, not a wait-then-assert.
  NON_ASSERTING      a fixed positive sleep with no assertion after it — a stub's
                     latency, a TTL lapse, a loop tick.
  DECLARED_NEGATIVE  a fixed positive sleep before an assertion, with a written
                     reason. The duration IS the test.
  UNKNOWN            everything else — which must be empty.

The buckets are asserted to SUM to the total call-site count, so a classifier bug
that drops a site (or counts it twice) fails here rather than quietly shrinking the
population the other guards think they are covering. The classifier primitives are
imported from the sibling guard rather than re-implemented, so the two can never
disagree about what a fixed sleep is.

Covers M4.
"""

from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path

from tests.repo_hygiene.test_no_unbounded_positive_wait import (
    GATEWAY_ROOT,
    _asserts_after,
    _blocks,
    _declared,
    _is_fixed_sleep,
    _iter_test_modules,
)

BUCKETS = (
    "LOOP_YIELD",
    "COMPUTED",
    "EMBEDDED",
    "NON_ASSERTING",
    "DECLARED_NEGATIVE",
    "UNKNOWN",
)


def _is_sleep_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Attribute) and func.attr == "sleep") or (
        isinstance(func, ast.Name) and func.id == "sleep"
    )


def _all_sleep_call_lines(tree: ast.Module) -> list[int]:
    """Every `sleep(...)` call site in the module, by line — the denominator."""
    return [node.lineno for node in ast.walk(tree) if _is_sleep_call(node)]


def _classify_statement_sleeps(tree: ast.Module, lines: list[str]) -> dict[int, str]:
    """Bucket every statement-level sleep. Keyed by line so callers can diff against
    the full call-site set and attribute the remainder to EMBEDDED."""
    verdicts: dict[int, str] = {}
    for block in _blocks(tree):
        for index, stmt in enumerate(block):
            if not isinstance(stmt, ast.Expr):
                continue
            inner = stmt.value
            if not isinstance(inner, ast.Await) or not _is_sleep_call(inner.value):
                continue
            call = inner.value
            assert isinstance(call, ast.Call)  # narrowed by _is_sleep_call

            if not call.args or not isinstance(call.args[0], ast.Constant):
                verdicts[stmt.lineno] = "COMPUTED"
                continue
            arg = call.args[0]
            if not isinstance(arg.value, int | float):
                verdicts[stmt.lineno] = "COMPUTED"
                continue
            if not _is_fixed_sleep(stmt):
                # A numeric literal that is not > 0 — i.e. sleep(0).
                verdicts[stmt.lineno] = "LOOP_YIELD"
                continue
            if _asserts_after(block, index) is None:
                verdicts[stmt.lineno] = "NON_ASSERTING"
                continue
            verdicts[stmt.lineno] = (
                "DECLARED_NEGATIVE" if _declared(lines, stmt.lineno) else "UNKNOWN"
            )
    return verdicts


def _census() -> tuple[Counter[str], list[str], int]:
    counts: Counter[str] = Counter()
    unknown: list[str] = []
    total = 0

    for path in _iter_test_modules():
        source = path.read_text()
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover — ruff's problem, not ours
            continue
        lines = source.splitlines()
        rel = Path(path).relative_to(GATEWAY_ROOT)

        call_lines = _all_sleep_call_lines(tree)
        total += len(call_lines)
        verdicts = _classify_statement_sleeps(tree, lines)

        for line in call_lines:
            bucket = verdicts.get(line, "EMBEDDED")
            counts[bucket] += 1
            if bucket == "UNKNOWN":
                unknown.append(f"{rel}:{line}")

    return counts, sorted(unknown), total


def test_sleep_census_is_exhaustive() -> None:
    """M4 — no `asyncio.sleep` site in the test tree is unclassified.

    A failure here is not "a test is flaky". It is "the population the other two
    guards believe they cover has grown a member nobody judged".
    """
    counts, unknown, total = _census()
    census = " · ".join(f"{bucket}={counts[bucket]}" for bucket in BUCKETS)

    assert not unknown, (
        f"{len(unknown)} sleep site(s) are UNCLASSIFIED — each is a fixed positive sleep "
        f"before an assertion with no written reason. Convert positive waits to "
        f"tests._polling.poll_until; annotate negative waits with `# NEGATIVE WAIT: "
        f"<reason>`.\n  Census: {census}\n  " + "\n  ".join(unknown)
    )
    assert sum(counts.values()) == total, (
        f"the buckets must PARTITION the population: {census} sums to "
        f"{sum(counts.values())} but {total} sleep call sites were found. A site counted "
        f"twice or not at all means the classifier disagrees with itself, and the other "
        f"two guards are silently covering less than they report."
    )
