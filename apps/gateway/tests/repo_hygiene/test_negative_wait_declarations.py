"""Standing guard: a `# NEGATIVE WAIT:` declaration must stay a real declaration.

The sibling `test_no_unbounded_positive_wait` guard accepts a fixed sleep when the
site says why the duration is load-bearing. That escape hatch is only worth
anything while the declarations mean something, and there are three ways for one
to rot:

  1. MALFORMED — written `# NEGATIVE WAIT (a deliberate race):` instead of
     `# NEGATIVE WAIT: a deliberate race`. This is not hypothetical: it happened
     during the sweep that produced these guards, and the sibling guard correctly
     kept reporting the site because the marker did not match. But a marker one
     character off is also a marker a future reader will copy.
  2. EMPTY — `# NEGATIVE WAIT: see above` says nothing a reader can act on. The
     whole point is that the NEXT sweep can tell a deliberate sleep from a guessed
     one without re-deriving the intent from scratch.
  3. ORPHANED — the sleep it defended was converted to a poll or deleted, and the
     marker stayed behind. Now it silently grandfathers the NEXT fixed sleep
     someone pastes underneath it, because the sibling guard looks 6 lines up.

So: every marker in `tests/` must parse, carry a substantive reason, and sit
directly above a fixed sleep it actually defends.

Covers M2 / R:ERR_VACUOUS_WAIT.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from pathlib import Path

from tests.repo_hygiene.test_no_unbounded_positive_wait import (
    GATEWAY_ROOT,
    _blocks,
    _is_fixed_sleep,
    _iter_test_modules,
)

# A comment ATTEMPTING to be the marker — looser than WELL_FORMED, so a malformed
# marker is caught rather than silently ignored the way the strict matcher ignores it.
#
# Case-SENSITIVE on both words: a marker written with parentheses instead of the colon
# is botched and must be reported, while prose in sentence case (`# NEGATIVE wait — do
# not convert this`) is a human sentence naming the concept, not a machine-read
# declaration.
# Only real COMMENT tokens are scanned, so a docstring or a regex that spells the
# marker out (this module and its sibling both do) is not a usage of it.
#
# Anchored at the START of the comment, because tokenising alone was not enough: a
# comment can DISCUSS the marker without being one. All 33 declarations in the suite sit
# on their own line as `# NEGATIVE WAIT: …`, so requiring that position costs nothing and
# stops prose like "same shape as the sibling `# NEGATIVE WAIT:` marker" from being read
# as a botched declaration. Found by this guard failing on a sibling guard's own comment
# during a proving run — the guard was right and its matcher was too loose.
MENTION = re.compile(r"^#\s*NEGATIVE WAIT\b")

# The one accepted shape. Mirrors the sibling guard's DECLARATION, with the reason
# captured so its substance can be judged.
WELL_FORMED = re.compile(r"#\s*NEGATIVE WAIT:\s*(?P<reason>.+)$")

# A reason short enough to be a shrug is not a reason. "the not-two half of `== 1`"
# is 26 characters; "see above" is 9.
MIN_REASON_CHARS = 18

# How far BELOW a marker the sleep it defends may sit. The sibling guard looks 6
# lines UP from the sleep, so this is the same window read from the other end.
ATTACH_WINDOW = 6


def _sleep_statement_lines(source: str) -> set[int]:
    """Line numbers of every statement-level `await …sleep(…)` in the module.

    Deliberately WIDER than the sibling guard's `_is_fixed_sleep`, which only counts a
    numeric literal argument. A marker defends whatever sleep sits under it, and two
    real sites sleep on a named constant (`await asyncio.sleep(_SETTLE_SECONDS)`) —
    those are properly declared, and reporting them as orphaned would train the next
    reader to delete a live declaration. `_is_fixed_sleep` is still imported and used
    for the narrower question the sibling guard asks.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover — ruff's problem, not ours
        return set()
    lines: set[int] = set()
    for block in _blocks(tree):
        for stmt in block:
            if _is_fixed_sleep(stmt):
                lines.add(stmt.lineno)
                continue
            if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Await):
                continue
            call = stmt.value.value
            if not isinstance(call, ast.Call):
                continue
            func = call.func
            if (isinstance(func, ast.Attribute) and func.attr == "sleep") or (
                isinstance(func, ast.Name) and func.id == "sleep"
            ):
                lines.add(stmt.lineno)
    return lines


def _comments(source: str) -> list[tuple[int, str]]:
    """Every real comment in the module, as (line number, text).

    Tokenising rather than line-matching is what keeps this guard from flagging its
    own docstring: a marker spelled inside a string literal is documentation about
    the convention, not an instance of it.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError):  # pragma: no cover — ruff's problem
        return []
    return [(tok.start[0], tok.string) for tok in tokens if tok.type == tokenize.COMMENT]


def _violations() -> list[str]:
    found: list[str] = []
    for path in _iter_test_modules():
        source = path.read_text()
        sleeps = _sleep_statement_lines(source)
        rel = Path(path).relative_to(GATEWAY_ROOT)

        for index, line in _comments(source):
            if not MENTION.search(line):
                continue

            match = WELL_FORMED.search(line)
            if match is None:
                found.append(
                    f"{rel}:{index} — MALFORMED: the marker must read exactly "
                    f"`# NEGATIVE WAIT: <reason>` (the sibling guard's matcher will not "
                    f"see this one): {line.strip()!r}"
                )
                continue

            reason = match.group("reason").strip()
            if len(reason) < MIN_REASON_CHARS:
                found.append(
                    f"{rel}:{index} — EMPTY: the reason must say why the duration is "
                    f"load-bearing, not point elsewhere: {reason!r}"
                )
                continue

            attached = any(line_no in sleeps for line_no in range(index, index + ATTACH_WINDOW + 1))
            if not attached:
                found.append(
                    f"{rel}:{index} — ORPHANED: no fixed sleep within {ATTACH_WINDOW} lines "
                    f"below this marker. Delete it — left in place it grandfathers the next "
                    f"fixed sleep pasted underneath."
                )
    return sorted(found)


def test_negative_wait_declarations_state_a_reason() -> None:
    """ERR_VACUOUS_WAIT — a retained sleep's declaration must be readable and attached.

    This guard is deliberately the mirror of `test_no_unbounded_positive_wait`: that
    one asks "does this sleep have a marker?", this one asks "does this marker have a
    sleep, and does it say anything?". Either question alone can be satisfied by a
    marker that means nothing.
    """
    violations = _violations()
    assert not violations, (
        f"ERR_VACUOUS_WAIT — {len(violations)} `# NEGATIVE WAIT` declaration(s) are "
        "malformed, empty, or orphaned:\n  " + "\n  ".join(violations)
    )
