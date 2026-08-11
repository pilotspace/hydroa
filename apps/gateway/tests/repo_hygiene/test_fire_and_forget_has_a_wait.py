"""Standing guard: an HTTP-triggered fire-and-forget assertion must wait (CR v3, class 7).

This guard exists because of what the sleep census could NOT see, and that gap is the most
important thing this task learned.

The census enumerated every `asyncio.sleep` site in `tests/` and partitioned it — 208 sites,
UNKNOWN = 0, three guards keeping it that way. All of it true, and all of it silent about
`tests/self_serve_checkout::test_superadmin_plan_endpoint_unchanged`, which did a PUT and
then read `audit_events` with NOTHING in between. No sleep to convert, no poll on the wrong
signal: no wait at all. It passed for months because the fire-and-forget audit task usually
completes before the test's next `await`, and it failed under 12-way contention when it did
not.

The population that matters is ASSERTIONS ON FIRE-AND-FORGET WRITES. Sleep sites are a
subset of it. A site that never had a sleep was never in the census, so `UNKNOWN == 0` said
nothing about it — which is why this guard keys off the WRITE, not off the sleep.

Scope, deliberately narrow: an HTTP call that triggers the write, a read of a
fire-and-forget table, and an assertion, with no wait anywhere in the function. Tests that
INSERT their own rows are excluded — there is no dispatch to race — and that exclusion is
what keeps this guard at a true zero instead of 33 false positives.

Covers CR v3 class 7.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = GATEWAY_ROOT / "tests"

# Tables written by `asyncio.ensure_future(...)` off the request path, so a read of them
# immediately after an HTTP response is a race by construction.
FIRE_AND_FORGET_TABLES = ("audit_events",)

# An HTTP call that can trigger the write. A bare GET cannot.
HTTP_CALL = re.compile(r"client\.(post|put|patch|delete)\(")

# Any wait at all counts — this guard asks for A wait, not for a particular one. The sibling
# guards are what judge whether a fixed sleep is justified.
WAIT_MARKERS = (
    "poll_until",
    "poll_for_count",
    "asyncio.sleep",
    "await_audit",
    "audit_count",
    "_await_",
    "_settle",
)

# The write is only a race if it is triggered, not inserted by the test itself.
SELF_INSERT = re.compile(r"INSERT\s+INTO\s+audit_events", re.IGNORECASE)


def _iter_test_modules() -> list[Path]:
    return sorted(p for p in TESTS_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _violations() -> list[str]:
    found: list[str] = []
    for path in _iter_test_modules():
        source = path.read_text()
        if not any(table in source for table in FIRE_AND_FORGET_TABLES):
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
            if not any(table in body for table in FIRE_AND_FORGET_TABLES):
                continue
            if not any(isinstance(n, ast.Assert) for n in ast.walk(node)):
                continue
            if not HTTP_CALL.search(body):
                continue  # nothing triggers a dispatch — no race to lose
            if SELF_INSERT.search(body):
                continue  # the test wrote the row itself
            if any(marker in body for marker in WAIT_MARKERS):
                continue
            found.append(
                f"{rel}:{node.lineno} — {node.name} triggers a fire-and-forget write via an "
                f"HTTP call, then asserts on it with NO wait of any kind"
            )
    return sorted(found)


def test_fire_and_forget_assertion_has_a_wait() -> None:
    """CR v3 class 7 — assert on a fire-and-forget write only after waiting for it.

    `tests._polling.poll_until` for a positive assertion; a declared sleep for a negative
    one. What this bans is the third option, which reads as deliberate and is not: no wait
    at all, passing on a fast host and failing under contention.
    """
    violations = _violations()
    assert not violations, (
        f"{len(violations)} fire-and-forget assertion(s) have no wait:\n  "
        + "\n  ".join(violations)
    )
