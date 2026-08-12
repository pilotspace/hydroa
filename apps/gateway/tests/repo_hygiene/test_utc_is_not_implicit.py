"""UTC must be DECLARED, not inherited from whatever the base image happens to do (todo #11).

MEASURED 2026-08-12, inside the real `0.14.1-prod` image:

    TZ env       : None
    time.tzname  : ('UTC', 'UTC')

So the container runs UTC — **by accident**. `python:3.12-slim-bookworm` ships no
`/etc/localtime`, and with `TZ` unset the C library falls back to UTC. Nothing in the repo asks
for that. A base-image change, a distro that ships a default localtime, or a node that mounts
`/etc/localtime` into the container flips every naive datetime in the process to local time, with
no error and no test failure.

TWO HALVES, and they are in different states — worth stating, because the todo reads as one
finding and only one half was still live:

1. **The idiom is already gone.** `src/gateway` contains ZERO naive `datetime.now()` and ZERO
   `utcnow()`. suite-stability M9 moved timestamp writes onto the DB clock (`func.now()`), so
   nothing in the code currently depends on the process timezone at all.
2. **The environment is still implicit.** `TZ` was unpinned.

That combination is why this is DEFENCE IN DEPTH, not a bug fix — today the pin protects nothing,
because half 1 removed every consumer. The pair exists so that stays true: the guard keeps the
consumers gone, and the pin means their return would not be silently host-dependent. Neither
assertion alone is worth much; together they close the loop.

The sibling `test_timestamp_columns_have_one_clock_owner` is a DIFFERENT property — it bans the
application clock (naive *or* aware) from DB-clock-owned columns, scoped to `*/infrastructure/`.
This one is about tz-awareness anywhere in src.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

GATEWAY_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = GATEWAY_ROOT / "src" / "gateway"
DOCKERFILE = GATEWAY_ROOT / "Dockerfile"

# `ENV TZ=UTC` / `ENV TZ="UTC"`, with or without other ENV pairs on the line.
TZ_PIN = re.compile(r"^\s*ENV\s+.*\bTZ=[\"']?UTC[\"']?", re.MULTILINE)

# Only `FROM ... AS runtime` matters. The builder stage's timezone cannot reach production.
RUNTIME_STAGE = re.compile(r"^FROM\s+\S+\s+AS\s+runtime\s*$", re.MULTILINE | re.IGNORECASE)


def test_the_runtime_image_declares_its_timezone() -> None:
    """`TZ=UTC` must be pinned in the runtime stage, not inherited from the base image."""
    source = DOCKERFILE.read_text(encoding="utf-8")

    match = RUNTIME_STAGE.search(source)
    assert match, (
        "no `FROM ... AS runtime` stage found in the gateway Dockerfile — this guard keys off "
        "that stage name and is now checking nothing. Re-point it at whatever the final stage "
        "is called."
    )
    runtime_stage = source[match.end() :]

    assert TZ_PIN.search(runtime_stage), (
        "the runtime stage does not pin `ENV TZ=UTC`.\n\n"
        "The image resolves to UTC today only because python:*-slim-bookworm ships no "
        "/etc/localtime and the C library falls back to UTC with TZ unset — that is an accident "
        "of the base image, not a decision this repo made. A base-image change, or a node that "
        "mounts /etc/localtime, moves every naive datetime in the process to local time with no "
        "error and no failing test.\n\n"
        "Add `ENV TZ=UTC` to the runtime stage."
    )


def _receiver_name(func: ast.Attribute) -> str | None:
    """The thing `.now()` is called ON: `datetime` for `datetime.now()`, `func` for `func.now()`."""
    value = func.value
    if isinstance(value, ast.Name):
        return value.id
    if isinstance(value, ast.Attribute):
        return value.attr
    return None


def _naive_clock_calls(tree: ast.Module) -> list[ast.Call]:
    """`datetime.now()` with no tz argument, and `datetime.utcnow()`.

    `datetime.now(UTC)` / `datetime.now(tz=...)` are explicit and fine — the process timezone
    cannot influence them. `utcnow()` is flagged even with no args: it returns a NAIVE datetime
    holding UTC wall-clock, the most common source of an accidental local-time comparison, and it
    is deprecated in 3.12.

    ⚠ THE RECEIVER IS LOAD-BEARING, and the first draft of this guard got it wrong. Matching any
    `.now()` with no arguments flagged **every** `func.now()` in the ORM layer — SQLAlchemy's DB
    clock, which is the CORRECT idiom here and the one suite-stability M9 deliberately moved to.
    That is 60+ false positives on the exact spelling the codebase is supposed to use, which is
    how a guard gets deleted instead of obeyed (see the sibling guard's docstring). So the check
    is anchored on the receiver: only the `datetime` class, never `func`.
    """
    found: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if _receiver_name(node.func) != "datetime":
            continue
        if node.func.attr == "utcnow":
            found.append(node)
        elif node.func.attr == "now" and not node.args and not node.keywords:
            found.append(node)
    return found


def test_src_has_no_host_timezone_dependent_clock_read() -> None:
    """Keep half 1 true: no naive clock read in src, so the pin never has to save us.

    This is the assertion that makes the Dockerfile pin meaningful rather than ceremonial. If a
    naive `datetime.now()` comes back, it inherits the process timezone — and the whole point of
    the two-part fix is that neither half silently regresses.
    """
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        offenders += [
            f"{path.relative_to(GATEWAY_ROOT)}:{call.lineno}" for call in _naive_clock_calls(tree)
        ]

    assert not offenders, (
        "these read the clock WITHOUT a timezone, so the value depends on the process "
        f"timezone:\n  " + "\n  ".join(offenders) + "\n\n"
        "Use `datetime.now(UTC)` for an application clock, or `func.now()` when the value is "
        "written into a DB-clock-owned column (see test_timestamp_columns_have_one_clock_owner). "
        "`utcnow()` is flagged even though it 'means' UTC: it returns a NAIVE datetime, which "
        "compares wrongly against aware ones, and it is deprecated in 3.12."
    )
