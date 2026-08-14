"""Red-first suite for the CC8.1 change-management evidence export.

change-management-evidence-export TASK.md §CHECKS (soc2-groundwork). The subject is a
repo-governance TOOL at `scripts/soc2/change_evidence.py`, NOT gateway runtime — so it is
loaded by file path (mirroring how the repo_hygiene guards reach `scripts/`), and every check
drives the PURE `build_evidence` core through a zero-network `FakeChangeSource` (M2/A1): the
tests never touch GitHub, a DB, or an app fixture.

RED reason before Build: `scripts/soc2/change_evidence.py` does not exist, so the module load
fails at collection — every check is red against the absent module.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
_MODULE_PATH = REPO_ROOT / "scripts" / "soc2" / "change_evidence.py"

_spec = importlib.util.spec_from_file_location("soc2_change_evidence", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None, f"cannot load {_MODULE_PATH}"
ce = importlib.util.module_from_spec(_spec)
# Register BEFORE exec so dataclasses (under `from __future__ import annotations`) can resolve
# the module namespace via sys.modules[cls.__module__]; a path-loaded module is otherwise absent.
sys.modules[_spec.name] = ce
_spec.loader.exec_module(ce)


# ── a zero-network source: the ONLY thing the pure core talks to (M2, A1) ──────────────
class FakeChangeSource:
    def __init__(self, merges, required, *, raise_incomplete=False):
        self._merges = merges
        self._required = required
        self._raise = raise_incomplete

    def merges(self, *, repo, branch, since, until):
        if self._raise:
            raise ce.IncompleteFetch("rate limited mid-window")
        return list(self._merges)

    def required_contexts(self, *, repo, branch):
        return list(self._required)


_REQUIRED = ("ci", "dashboard")
_SINCE = "2026-08-01T00:00:00Z"
_UNTIL = "2026-08-14T00:00:00Z"


def _raw(**kw):
    base = dict(
        main_sha="a" * 12,
        tested_head_sha="b" * 12,
        pr_number=101,
        merged_at="2026-08-10T00:00:00Z",
        checks={"ci": "success", "dashboard": "success"},
        approvals=("realreviewer",),
    )
    base.update(kw)
    return ce.MergeRaw(**base)


def _build(merges, required=_REQUIRED, **kw):
    src = FakeChangeSource(merges, required)
    return ce.build_evidence(src, since=_SINCE, until=_UNTIL, **kw)


# ── CHECKS ─────────────────────────────────────────────────────────────────────────────
def test_green_checked_and_reviewed_merge_is_clean() -> None:
    """covers: M1, A6, E7"""
    res = _build([_raw()])
    assert res.n_merges == 1
    ev = res.merges[0]
    assert ev.exceptions == ()
    assert ev.main_sha == "a" * 12 and ev.tested_head_sha == "b" * 12
    assert res.n_exceptions == 0
    assert res.n_self_approved == 0
    assert any(a.independent for a in ev.approvals)


def test_missing_required_context_is_flagged() -> None:
    """covers: M5, E1, R:SILENT_OMISSION"""
    res = _build([_raw(checks={"ci": "success"})])  # dashboard absent
    assert res.n_merges == 1, "the merge must still appear, never be dropped"
    ev = res.merges[0]
    assert ev.exceptions, "a missing required context must flag an exception"
    assert any("dashboard" in x for x in ev.exceptions)
    assert res.n_exceptions == 1


def test_self_approval_not_counted_independent() -> None:
    """covers: M4, E2, R:LAUNDERED_APPROVAL"""
    res = _build([_raw(approvals=("pilotspacex-byte",))])
    ev = res.merges[0]
    assert ev.approvals[0].author == "pilotspacex-byte"
    assert ev.approvals[0].independent is False
    assert res.n_self_approved == 1
    assert not any(a.independent for a in ev.approvals), "a self-approval is never independent"


def test_direct_push_is_an_exception() -> None:
    """covers: M3, A2, E3"""
    res = _build([_raw(pr_number=None, approvals=())])
    ev = res.merges[0]
    assert any("direct push" in x.lower() for x in ev.exceptions)
    assert res.n_exceptions == 1


def test_incomplete_fetch_fails_not_truncates() -> None:
    """covers: M6, E4, R:TRUNCATED_AS_COMPLETE"""
    src = FakeChangeSource([_raw()], _REQUIRED, raise_incomplete=True)
    with pytest.raises(ce.IncompleteFetch):
        ce.build_evidence(src, since=_SINCE, until=_UNTIL)


def test_window_is_half_open() -> None:
    """covers: A3"""
    at_since = _raw(main_sha="s" * 12, merged_at=_SINCE)
    at_until = _raw(main_sha="u" * 12, merged_at=_UNTIL)
    res = _build([at_since, at_until])
    shas = {e.main_sha for e in res.merges}
    assert "s" * 12 in shas, "a merge exactly at `since` is included"
    assert "u" * 12 not in shas, "a merge exactly at `until` is excluded"


def test_build_is_pure_and_deterministic() -> None:
    """covers: M2, A1, A5, E5"""
    merges = [
        _raw(main_sha="c" * 12, merged_at="2026-08-05T00:00:00Z"),
        _raw(main_sha="d" * 12, merged_at="2026-08-09T00:00:00Z"),
    ]
    a = _build(list(merges))
    b = _build(list(merges))
    assert a == b, "two builds over identical source output must be byte-identical"
    # newest-first ordering (A5): the 08-09 merge precedes the 08-05 one
    assert [e.main_sha for e in a.merges] == ["d" * 12, "c" * 12]


def test_squash_records_both_shas_no_invented_run() -> None:
    """covers: M1, E6, R:UNTESTED_MERGE"""
    # tested head differs from the squashed main sha; checks have NO entry for main_sha
    res = _build([_raw(main_sha="m" * 12, tested_head_sha="h" * 12)])
    ev = res.merges[0]
    assert ev.tested_head_sha == "h" * 12 and ev.main_sha == "m" * 12
    assert ev.tested_head_sha != ev.main_sha
    # a merge with NO success conclusion at all is never counted green
    res2 = _build([_raw(main_sha="z" * 12, checks={})])
    assert res2.merges[0].exceptions, "no conclusion => flagged, never an invented green"


def test_summary_is_payload_free_and_counted() -> None:
    """covers: A6, A4"""
    res = _build(
        [
            _raw(main_sha="1" * 12),
            _raw(main_sha="2" * 12, approvals=()),  # no approval => exception (A4)
        ]
    )
    summary = ce.render_summary(res)
    assert "2" in summary  # n_merges == 2 appears
    assert "exception" in summary.lower()
    # the no-approval merge is flagged, not silently passed (A4)
    no_appr = next(e for e in res.merges if e.main_sha == "2" * 12)
    assert any("approval" in x.lower() for x in no_appr.exceptions)
    for secret in ("ghp_", "github_pat_", "token", "authorization"):
        assert secret not in summary.lower(), "the summary must be payload/secret free"
