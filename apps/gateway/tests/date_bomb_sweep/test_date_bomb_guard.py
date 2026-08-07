"""Failing-first (RED) suite — catch the absolute-seed/relative-window pairing.

date-bomb-sweep PLAN.md §4. Closes todo #84.

RED reason expected (before Build): `tests.repo_hygiene._date_bomb` does not exist, so
every test here fails at import.

The defect this guards: a test seeds a row at a hardcoded `datetime(2026, 7, 15, ...)` and
then queries an endpoint with a bare `window=month`, whose bounds come from the WALL CLOCK
via `usage/api/router.py::_compute_window_bounds`. The two agree until the clock leaves
July 2026 — then `make ci` goes red one morning with ZERO commits behind it, looking
exactly like a code regression. That happened on 2026-08-01 and was fixed in PR #92.

The audit (§3) found the tree is now clean: 77 files carry a 2026 literal, only 4 issue a
bare relative window, and all 4 are correct. So there is nothing left to sweep — which is
precisely why the guard has to be proven against PLANTED fixtures. A guard that has never
been seen to fail is not a guard.

The direction that would HURT is a false positive. A blanket "no hardcoded dates in tests"
rule would flag ~77 correct files and revert PR #92's own fix, so the guard keys on the
PAIRING, never on the literal alone — and the silent-arm tests below are gated against the
real files by name.
"""

from __future__ import annotations

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
TESTS_ROOT = Path(__file__).resolve().parents[1]


def _scan_source(path: Path) -> str | None:
    from tests.repo_hygiene._date_bomb import scan_source

    return scan_source(path.read_text())


# ─────────────────────────────────────────────────────────────────────────────
# THE GUARD FIRES
# ─────────────────────────────────────────────────────────────────────────────


def test_flags_a_planted_absolute_seed_with_bare_relative_window() -> None:
    """The PR #92 shape, reproduced exactly. covers: M1, M4"""
    reason = _scan_source(FIXTURES / "bomb_inline.py")

    assert reason is not None, (
        "the guard did not fire on the exact pairing that turned `make ci` red on "
        "2026-08-01 — an absolute datetime(2026, 7, 15) seed plus a bare `window=month`"
    )
    lowered = reason.lower()
    assert "2026" in reason, f"the message must name the seed it found; got: {reason}"
    assert "window=month" in lowered, (
        f"the message must name the relative query, not just the seed; got: {reason}"
    )
    assert "current" in lowered, (
        "the message must name the REMEDY — keep both constants, seed relative-window "
        f"tests inside the current month. Without it the next person guesses. Got: {reason}"
    )


def test_flags_the_seed_when_it_arrives_via_a_sibling_import() -> None:
    """The margin_dashboard shape — the seed is imported, not defined. covers: M1

    A detector that reads only module-local assignments sees a clean file here and would
    have missed the ONE real instance this repo has ever had.
    """
    from tests.repo_hygiene._date_bomb import scan_tree

    findings = scan_tree(FIXTURES / "imported_seed", skip_fixtures=False)
    flagged = {path.name for path, _ in findings}

    assert "mod_bomb.py" in flagged, (
        "the guard missed a seed that arrives by `from .seeds import INSIDE`. That is "
        f"exactly tests/margin_dashboard's shape. Flagged: {flagged or 'nothing'}"
    )
    assert "seeds.py" not in flagged, (
        "seeds.py only DEFINES constants — it issues no relative query, so flagging it "
        "blames the wrong file and the remedy would be applied in the wrong place"
    )


# ─────────────────────────────────────────────────────────────────────────────
# THE GUARD STAYS SILENT — the false-positive direction, the one that would hurt
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("fixture", "why"),
    [
        (
            "clean_absolute_window.py",
            "an absolute seed with start=/end= is tests/margin_dashboard's INSIDE, which "
            "is CORRECT and was deliberately kept when PR #92 added INSIDE_CURRENT_MONTH",
        ),
        (
            "clean_period_window.py",
            "period=2026-07 pins the window as firmly as start=/end= does",
        ),
        (
            "clean_relative_seed.py",
            "a bare window= seeded from now(datetime.UTC) can never drift — the seed "
            "moves with the window",
        ),
    ],
)
def test_correct_pairings_are_not_flagged(fixture: str, why: str) -> None:
    """covers: M2, M3"""
    reason = _scan_source(FIXTURES / fixture)

    assert reason is None, (
        f"FALSE POSITIVE on {fixture} — {why}. A guard that flags this pushes someone to "
        f'"fix" a correct test and re-break what PR #92 fixed. Got: {reason}'
    )


# ─────────────────────────────────────────────────────────────────────────────
# ADDED AT v2 (CR-1) — the two detector defects the first real run exposed
# ─────────────────────────────────────────────────────────────────────────────


def test_reports_every_pairing_in_a_file_not_just_the_first() -> None:
    """A file with three bombs must not read as a file with one. covers: M1

    First-match-wins hid the third real margin_dashboard bomb
    (`test_m8_query_timeout_maps_to_504`): the file already had a finding, so the scan
    moved on and the count looked like 2 when it was 3.
    """
    from tests.repo_hygiene._date_bomb import scan_all

    reasons = scan_all((FIXTURES / "triple_bomb.py").read_text())

    assert len(reasons) == 3, (
        f"expected 3 findings (bomb_one, bomb_two, bomb_three) but got {len(reasons)}. "
        "The fourth function in that fixture pairs an absolute seed with start=/end= and "
        f"must NOT be counted. Reasons: {reasons}"
    )
    named = " ".join(reasons)
    for function in ("bomb_one", "bomb_two", "bomb_three"):
        assert function in named, f"{function} was not reported: {reasons}"
    assert "not_a_bomb" not in named, (
        "the correct absolute-window function in the same file was flagged — the count is "
        "per PAIRING, not per function"
    )


def test_prose_describing_the_hazard_is_not_the_hazard() -> None:
    """Docstrings and assertion messages are not code. covers: M2

    The guard's own §4 suite quotes `datetime(2026, 7, 15, ...)` and `window=month` while
    explaining the bug, and the first draft flagged itself. A guard that flags the test
    proving it works is self-defeating — hence AST Call/Name detection, not regex.
    """
    reason = _scan_source(FIXTURES / "clean_prose_only.py")

    assert reason is None, (
        "FALSE POSITIVE on prose. Both halves of the pattern appear only inside a "
        f"docstring and an assertion message — neither seeds nor queries anything: {reason}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# THE REAL TREE — the audit's findings turned into a standing check
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "suite",
    ["margin_dashboard", "spend_windows", "team_governance", "team_attribution"],
)
def test_the_four_real_relative_window_suites_pass_unflagged(suite: str) -> None:
    """Named individually so a regression says WHICH one broke. covers: M2, M3

    These are every suite in the tree that issues a bare `window=`. margin_dashboard is
    the one that was actually broken and is now fixed; the other three seed from the wall
    clock and always did.
    """
    from tests.repo_hygiene._date_bomb import scan_tree

    findings = scan_tree(TESTS_ROOT / suite)

    assert findings == [], (
        f"tests/{suite} was flagged: "
        + "; ".join(f"{p.name}: {r}" for p, r in findings)
        + ". Either that suite has regressed, or the guard is over-eager — check which "
        "before touching either."
    )


def test_the_whole_test_tree_is_currently_clean() -> None:
    """The 2026-08-07 audit result, made permanent. covers: M1, R

    Passes today (the audit found zero live bombs) and goes red the first time someone
    reintroduces the pairing — which is the entire point of the task.
    """
    from tests.repo_hygiene._date_bomb import scan_tree

    findings = scan_tree(TESTS_ROOT)

    assert findings == [], "date bombs found:\n" + "\n".join(
        f"  {p.relative_to(TESTS_ROOT)}: {r}" for p, r in findings
    )
