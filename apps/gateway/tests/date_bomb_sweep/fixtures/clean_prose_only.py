# ruff: noqa: F821  — `result` and friends are intentionally unresolved: this file is a
# TEXT SPECIMEN for the date-bomb guard, never imported and never executed. It exists to
# prove that prose DESCRIBING the hazard is not flagged as the hazard.
# pyright: reportUndefinedVariable=false, reportMissingParameterType=false
"""CLEAN — every match is PROSE, not code.

This module's docstring describes the hazard: a test that seeds at
`datetime(2026, 7, 15, 12, 0)` and then queries `window=month` is a date bomb. Describing
it is not doing it, and the guard's own §4 suite is written exactly like this.
"""

from __future__ import annotations


def assert_something(result):  # type: ignore[no-untyped-def]
    assert result is not None, (
        "expected a result — the seed uses datetime(2026, 7, 15) and the query uses "
        "window=month, so if this is empty the pairing has drifted"
    )
