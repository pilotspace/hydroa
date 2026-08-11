"""Standing guard: an absolute-duration assertion must state the margin it is betting on.

FLAKE CLASS 10 (todo #105). The shape:

    elapsed = time.monotonic() - start
    assert elapsed < 0.5

That is not a property of the code. It is a bet that the machine is fast enough, and it
comes with an alibi: when it fails, the failure LOOKS like the thing under test regressed.
`scoped_self_serve_signup` asserted `response < 0.5s` against an injected 1.5s mail delay
to prove dispatch was off the request path. Under `-n 12` it measured 0.586s. The
structural property held by ~2.5x; the assertion failed anyway — because signup's
legitimate cost IS the deliberate Argon2 timing mask, so the threshold had been set BELOW
the good path's real cost. It was fixed by parking the send on an Event, leaving the
passing path with no wall clock at all.

This guard does NOT ban absolute durations, because most of them here are sound. The
census that motivated it found 16 sites, and 15 are the defensible shape: "prove a timeout
FIRES", where a configured bound of 0.01-1.0s races an injected delay of 5-10s and the
threshold sits in the wide gap between them. Measured margins run from ~26x
(transactional_email, 0.03s actual against 1.0s) to ~15000x (lifespan PEL drain, 0.0002s
against 3.0s). Rewriting those would be ceremony, and ceremony is its own failure mode —
a guard that flags sound code gets deleted rather than obeyed.

What went wrong with signup was not that it used a clock. It was that NOBODY HAD TO WRITE
DOWN THE MARGIN. Had the author stated "good path ~0s, threshold 0.5s", the Argon2 mask
would have been the obvious counter-example while the test was being written.

So: state it. `# TIME BUDGET: <good-path cost> vs <bad-path cost>, and why the threshold
sits between them`. One line. The next reader can then judge the bet without re-deriving
the intent, and the next sweep can tell a measured margin from a guess.

Prefer a CAUSAL proof where one exists — park on an `asyncio.Event`, assert the negative
directly ("the send has provably not completed"), or let a hung dependency make the bad
path hang rather than merely be slow. A causal proof has no margin to state and cannot
flake. The declaration is for when a duration is genuinely the thing under test.

Grandfathering is by REASON, not by path — an allow-list would let a copy-paste into a new
file straight through.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = GATEWAY_ROOT / "tests"

# Names that mean "a span of time". Matched against every Name/Attribute in the compared
# expression, plus the clock calls themselves, so `time.monotonic() - start < 2.0` is
# caught even when no variable is involved.
TIMEISH = re.compile(r"elapsed|duration|took|latency|wall|_secs?$|_seconds?$", re.IGNORECASE)

CLOCK_CALLS = frozenset({"monotonic", "perf_counter", "time"})

# Mirrors the NEGATIVE WAIT marker's shape and strictness. Anchored at the line start so
# prose merely NAMING the marker cannot grandfather an assertion below it — the sibling
# guard learned that the hard way, by failing on another guard's own explanatory comment.
DECLARATION = re.compile(r"^\s*#\s*TIME BUDGET:\s*(?P<reason>.+)$")

# A reason short enough to be a shrug is not a reason. "generous" is 8 characters.
MIN_REASON_CHARS = 24

# How far above the assertion the declaration may sit. Larger than the sleep guard's 6
# because these assertions often follow a multi-line arrange block.
LOOKBACK_LINES = 8

# This module quotes the marker in prose and in its own regex.
EXEMPT_FILES = frozenset({Path(__file__).resolve()})


def _iter_test_modules() -> list[Path]:
    return sorted(
        p
        for p in TESTS_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts and p.resolve() not in EXEMPT_FILES
    )


def _mentions_a_span_of_time(node: ast.expr) -> bool:
    for inner in ast.walk(node):
        if isinstance(inner, ast.Name) and TIMEISH.search(inner.id):
            return True
        if isinstance(inner, ast.Attribute) and TIMEISH.search(inner.attr):
            return True
        if isinstance(inner, ast.Call):
            func = inner.func
            if isinstance(func, ast.Attribute) and func.attr in CLOCK_CALLS:
                return True
    return False


def _duration_upper_bounds(tree: ast.Module) -> list[ast.Assert]:
    """Assertions of the form `assert <a span of time> < <anything>`.

    Only `<` and `<=`: an upper bound is the bet ("it finished fast enough"). A LOWER
    bound (`elapsed > 1.0`, "the backoff really waited") is a different claim — it fails
    when the machine is FAST, which does not happen under the load this class is about.
    """
    found: list[ast.Assert] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        test = node.test
        if not isinstance(test, ast.Compare) or not test.ops:
            continue
        if not isinstance(test.ops[0], ast.Lt | ast.LtE):
            continue
        if not _mentions_a_span_of_time(test.left):
            continue
        found.append(node)
    return found


def _declaration_above(lines: list[str], assert_line: int) -> str | None:
    start = max(0, assert_line - 1 - LOOKBACK_LINES)
    for line in lines[start : assert_line - 1]:
        match = DECLARATION.match(line)
        if match:
            return match.group("reason").strip()
    return None


def _violations_in_source(source: str, label: str) -> tuple[list[str], int]:
    """(violations, population) for ONE module, as text — so the detector is testable.

    The original victim of this flake class has already been fixed (signup now parks on an
    Event and has no clock left), so the guard cannot be shown red against the tree that
    motivated it. Exercising it on the shape instead is the next best proof, and better
    than trusting a green suite: see [[guard-must-be-red-against-its-motivating-tree]].
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover — ruff's problem, not ours
        return [], 0
    lines = source.splitlines()
    violations: list[str] = []
    population = 0

    for node in _duration_upper_bounds(tree):
        population += 1
        reason = _declaration_above(lines, node.lineno)
        if reason is None:
            violations.append(
                f"{label}:{node.lineno} — `{ast.unparse(node.test)}` with no "
                f"`# TIME BUDGET:` line above it"
            )
        elif len(reason) < MIN_REASON_CHARS:
            violations.append(
                f"{label}:{node.lineno} — TIME BUDGET reason is {len(reason)} chars "
                f"({reason!r}); state the good-path and bad-path costs"
            )
    return violations, population


def _findings() -> tuple[list[str], int]:
    """(violations, total population) — the population is returned so the guard can prove
    it is not vacuously green."""
    violations: list[str] = []
    population = 0
    for path in _iter_test_modules():
        found, count = _violations_in_source(path.read_text(), str(path.relative_to(GATEWAY_ROOT)))
        violations.extend(found)
        population += count
    return sorted(violations), population


# The signup shape, as it stood when it failed under -n 12. Reconstructed here because the
# real site has since been converted to a causal Event and no longer carries a clock.
_THE_SIGNUP_SHAPE = """
import time

async def test_dispatch_is_off_the_request_path(client):
    start = time.monotonic()
    resp = await client.post("/signup", json={"email": "a@b.co"})
    elapsed = time.monotonic() - start
    assert resp.status_code == 201
    assert elapsed < 0.5
"""

_DECLARED = """
import time

async def test_thing(client):
    start = time.monotonic()
    resp = await client.post("/signup", json={"email": "a@b.co"})
    elapsed = time.monotonic() - start
    # TIME BUDGET: good path ~0.6s (the deliberate Argon2 timing mask), bad path 1.5s
    # (the injected mail delay) — so the threshold must sit ABOVE 0.6s, not below it.
    assert elapsed < 1.1
"""

_SHRUG = """
import time

async def test_thing():
    start = time.monotonic()
    elapsed = time.monotonic() - start
    # TIME BUDGET: generous
    assert elapsed < 5.0
"""

_LOWER_BOUND = """
import time

async def test_backoff_really_waited():
    start = time.monotonic()
    elapsed = time.monotonic() - start
    assert elapsed > 1.0
"""

_NOT_A_DURATION = """
async def test_row_count(session):
    rows = await session.execute("select 1")
    assert len(rows) < 5
"""

_INLINE_CLOCK = """
import time

async def test_inline():
    start = time.monotonic()
    assert time.monotonic() - start < 2.0
"""


def test_the_signup_shape_that_actually_failed_is_caught() -> None:
    """The motivating defect, reconstructed: `assert elapsed < 0.5` with no stated margin.

    This is the assertion that failed at 0.586s under `-n 12` while the structural
    property it was testing held by ~2.5x. Had the author been required to write down
    "good path ~0.6s", the Argon2 mask would have been the obvious counter-example.
    """
    violations, population = _violations_in_source(_THE_SIGNUP_SHAPE, "signup.py")
    assert population == 1, "the detector must SEE the assertion before it can judge it"
    assert violations, "the shape that actually caused this flake class must be reported"


def test_a_declared_budget_passes() -> None:
    assert _violations_in_source(_DECLARED, "ok.py")[0] == []


def test_a_shrug_is_not_a_reason() -> None:
    """`# TIME BUDGET: generous` states nothing. A marker that can be satisfied by a word
    turns the guard into a formality, which is how declaration schemes die."""
    violations, _ = _violations_in_source(_SHRUG, "shrug.py")
    assert violations and "chars" in violations[0]


def test_a_lower_bound_is_a_different_claim() -> None:
    """`elapsed > 1.0` asserts a wait really happened. It fails when the host is FAST,
    which is not the failure mode this class is about — flagging it would demand a margin
    declaration for a claim that has no load-related margin."""
    assert _violations_in_source(_LOWER_BOUND, "lower.py") == ([], 0)


def test_a_non_duration_comparison_is_untouched() -> None:
    """`len(rows) < 5` must not be dragged in. The suite is full of `<` comparisons and a
    guard that flags all of them would be deleted within a day."""
    assert _violations_in_source(_NOT_A_DURATION, "rows.py") == ([], 0)


def test_an_inline_clock_delta_is_caught_without_a_variable() -> None:
    """`assert time.monotonic() - start < 2.0` has no `elapsed` name to match on, so the
    clock CALL has to be recognised too — otherwise the guard is one refactor from blind."""
    violations, population = _violations_in_source(_INLINE_CLOCK, "inline.py")
    assert population == 1 and violations


def test_an_absolute_duration_assertion_declares_its_margin() -> None:
    """ERR_UNDECLARED_TIME_BUDGET — flake class 10 (todo #105).

    Fix a violation one of two ways, and prefer the first:

    * Make the proof CAUSAL and delete the clock. Park the slow half on an
      `asyncio.Event` the test controls, then assert the negative directly — "the
      response returned while the send provably had not completed". That cannot flake,
      and if the property breaks the test HANGS rather than measuring 0.6 instead of
      0.5.
    * Keep the duration and declare it: `# TIME BUDGET: good path ~Xms, bad path Ys
      (the Z timeout), so the threshold sits N-fold above the good path`.

    Do not "fix" this by loosening the threshold. A threshold raised until it stops
    failing is a threshold that no longer asserts anything — and unlike a converted
    negative wait, nothing will tell you it went vacuous.
    """
    violations, population = _findings()

    assert population, (
        "no absolute-duration assertions found anywhere in tests/ — this guard is "
        "vacuous, which reports green while checking nothing. If the last one was "
        "genuinely converted to a causal proof, that is excellent; delete this guard "
        "deliberately rather than leaving it to pass on an empty set."
    )
    assert not violations, (
        f"ERR_UNDECLARED_TIME_BUDGET — {len(violations)} of {population} absolute-duration "
        "assertion(s) do not state the margin they bet on. An absolute duration is not a "
        "property of the code; it is a bet that the host is fast enough, and it fails with "
        "an alibi — the failure looks like the code regressed.\n\n"
        "Prefer a causal proof (park on an Event, assert the negative directly). Where a "
        "duration IS the thing under test, add `# TIME BUDGET: <good-path cost> vs "
        "<bad-path cost>, why the threshold sits between them`.\n\n"
        "signup asserted <0.5s and measured 0.586s under -n 12 — the structural property "
        "held by 2.5x and the assertion failed anyway, because the threshold had been set "
        "below the good path's own cost (a deliberate Argon2 timing mask). Writing the "
        "margin down is what would have caught that at authoring time.\n  "
        + "\n  ".join(violations)
    )
