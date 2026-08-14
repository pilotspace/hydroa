"""Red-first suite for the CC6 access-review + least-privilege register.

access-review-and-least-privilege TASK.md §CHECKS (soc2-groundwork). The subject is the
repo-governance tool `scripts/soc2/access_review.py`, loaded by file path (like task 1's
change-management export). Every check drives the PURE `build_register` core through a
zero-network `FakeAccessSource` with an INJECTED `now_iso` — no GitHub, no clock, no DB.

RED before Build: `scripts/soc2/access_review.py` does not exist — collection fails on the
absent module.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
_MODULE_PATH = REPO_ROOT / "scripts" / "soc2" / "access_review.py"

_spec = importlib.util.spec_from_file_location("soc2_access_review", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None, f"cannot load {_MODULE_PATH}"
ar = importlib.util.module_from_spec(_spec)
# Register BEFORE exec so dataclasses (under `from __future__ import annotations`) resolve the
# module namespace via sys.modules — a path-loaded module is otherwise absent (task-1 lesson).
sys.modules[_spec.name] = ar
_spec.loader.exec_module(ar)


class FakeAccessSource:
    def __init__(self, grants, *, raise_incomplete=False):
        self._grants = grants
        self._raise = raise_incomplete

    def grants(self, *, org, repo):
        if self._raise:
            raise ar.IncompleteFetch("rate limited mid-enumeration")
        return list(self._grants)


_NOW = "2026-08-14T00:00:00Z"
_RECENT = "2026-08-10T00:00:00Z"  # within 90d
_OLD = "2026-01-01T00:00:00Z"  # > 90d before _NOW


def _grant(login, permission, *, surface="repo:hydroa", last_activity=_RECENT):
    return ar.AccessGrant(
        login=login, surface=surface, permission=permission, last_activity=last_activity
    )


def _policy(*entries):
    return list(entries)


def _entry(login, max_permission, *, self_approval=False):
    return ar.PolicyEntry(
        login=login,
        max_permission=max_permission,
        justification=f"{login} needs {max_permission}",
        self_approval_account=self_approval,
    )


def _build(grants, policy, **kw):
    src = FakeAccessSource(grants)
    return ar.build_register(src, policy, now_iso=_NOW, **kw)


# ── CHECKS ─────────────────────────────────────────────────────────────────────────────
def test_every_write_admin_principal_is_classified() -> None:
    """covers: M3, A2"""
    grants = [_grant("owner", "admin"), _grant("dev", "write"), _grant("viewer", "read")]
    policy = _policy(_entry("owner", "admin"), _entry("dev", "write"))
    reg = _build(grants, policy)
    classified = {g.grant.login for g in reg.grants}
    assert classified == {"owner", "dev"}, "write/admin classified; a read viewer is not privileged"
    assert all(g.classification in ("justified", "over_privileged", "stale") for g in reg.grants)


def test_tool_has_no_mutate_path() -> None:
    """covers: M1, R:TOOL_MUTATES_ACCESS"""
    forbidden = (
        "revoke",
        "remove",
        "delete",
        "set_permission",
        "grant_access",
        "mutate",
        "add_member",
    )
    public = [n for n in dir(ar) if not n.startswith("_")]
    for name in public:
        assert not any(bad in name.lower() for bad in forbidden), f"{name} looks like a mutate path"
    # the port is read-only: its only method is the read `grants`
    proto_methods = {m for m in dir(ar.AccessSource) if not m.startswith("_")}
    assert proto_methods == {"grants"}, f"AccessSource must be read-only, got {proto_methods}"


def test_unlisted_principal_fails_closed() -> None:
    """covers: M3, A4, E1, R:SILENT_JUSTIFY"""
    reg = _build([_grant("stranger", "write")], _policy())  # empty policy
    g = reg.grants[0]
    assert g.classification == "over_privileged", (
        "an unlisted principal fails closed, never justified"
    )
    assert reg.n_over_privileged == 1


def test_justified_only_against_declared_policy() -> None:
    """covers: M4, E2, R:CURRENT_STATE_AS_POLICY"""
    # holds admin live but policy grants only write => over_privileged (not laundered by live state)
    reg = _build([_grant("dev", "admin")], _policy(_entry("dev", "write")))
    assert reg.grants[0].classification == "over_privileged"


def test_byte_account_recorded_non_independent() -> None:
    """covers: M5, E3"""
    reg = _build(
        [_grant("pilotspacex-byte", "write")],
        _policy(_entry("pilotspacex-byte", "write", self_approval=True)),
    )
    g = reg.grants[0]
    assert g.self_approval_account is True
    assert "self" in g.reason.lower() and "disposition" in g.reason.lower(), (
        "a self-approval account's reason must name its nature AND leave disposition to Tin"
    )


def test_review_record_unreviewed_until_signed() -> None:
    """covers: M6, E7, R:FABRICATED_SIGNOFF"""
    reg = _build([_grant("owner", "admin")], _policy(_entry("owner", "admin")))
    assert reg.review.reviewer == "unreviewed — draft", "the tool never signs for a human"
    assert reg.review.window_days == 90


def test_stale_grant_flagged_by_window() -> None:
    """covers: A3, E6"""
    reg = _build([_grant("dozer", "write", last_activity=_OLD)], _policy(_entry("dozer", "write")))
    assert reg.grants[0].classification == "stale", "no in-window activity => stale, not justified"
    assert reg.n_stale == 1


def test_incomplete_fetch_fails_not_truncates() -> None:
    """covers: M2, E4"""
    src = FakeAccessSource([_grant("owner", "admin")], raise_incomplete=True)
    with pytest.raises(ar.IncompleteFetch):
        ar.build_register(src, _policy(_entry("owner", "admin")), now_iso=_NOW)


def test_build_is_pure_deterministic_and_ordered() -> None:
    """covers: M2, A1, A5, E5"""
    grants = [
        _grant("good", "write"),  # justified
        _grant("stranger", "admin"),  # over_privileged (unlisted)
        _grant("idle", "write", last_activity=_OLD),  # stale
    ]
    policy = _policy(_entry("good", "write"), _entry("idle", "write"))
    a = _build(list(grants), policy)
    b = _build(list(grants), policy)
    assert a == b, "two builds over identical source + now_iso must be byte-identical"
    # severity order: over_privileged, then stale, then justified (A5)
    assert [g.classification for g in a.grants] == ["over_privileged", "stale", "justified"]


def test_summary_is_payload_free_and_counted() -> None:
    """covers: A6"""
    reg = _build(
        [_grant("owner", "admin"), _grant("stranger", "write")],
        _policy(_entry("owner", "admin")),
    )
    summary = ar.render_summary(reg)
    assert "over-privileged" in summary.lower() or "over_privileged" in summary.lower()
    assert "unreviewed — draft" in summary, "the dated review record shows in the summary"
    for secret in ("ghp_", "github_pat_", "token", "authorization"):
        assert secret not in summary.lower(), "the summary must be payload/secret free"
