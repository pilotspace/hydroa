"""Red-first suite for the CC9.2 vendor / subprocessor register.

vendor-subprocessor-register TASK §CHECKS (soc2-groundwork). The subject is the repo-governance
tool `scripts/soc2/vendor_register.py`, loaded by file path (like the CC8.1 change-evidence and
CC6 access-review tools). Every check drives the PURE `build_register` core through a zero-network
`FakeUsageSource` with an INJECTED `now_iso` — no config read, no clock, no network.

RED before Build: `scripts/soc2/vendor_register.py` does not exist — collection fails on the
absent module.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
_MODULE_PATH = REPO_ROOT / "scripts" / "soc2" / "vendor_register.py"

_spec = importlib.util.spec_from_file_location("soc2_vendor_register", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None, f"cannot load {_MODULE_PATH}"
vr = importlib.util.module_from_spec(_spec)
# Register BEFORE exec so dataclasses (under `from __future__ import annotations`) resolve the
# module namespace via sys.modules — a path-loaded module is otherwise absent (task-1/2 lesson).
sys.modules[_spec.name] = vr
_spec.loader.exec_module(vr)


class FakeUsageSource:
    def __init__(self, refs, *, raise_incomplete=False):
        self._refs = refs
        self._raise = raise_incomplete

    def processors(self, *, org, repo):
        if self._raise:
            raise vr.IncompleteFetch("rate limited mid-enumeration")
        return list(self._refs)


_NOW = "2026-08-14T00:00:00Z"
_FUTURE = "2027-01-01T00:00:00Z"
_PAST = "2026-01-01T00:00:00Z"


def _vendor(
    name,
    *,
    purpose="model inference",
    region="us",
    data_categories=("prompts",),
    processes_customer_data=True,
    dpa_status="signed",
    dpa_expiry=_FUTURE,
):
    return vr.Subprocessor(
        name=name,
        purpose=purpose,
        data_categories=tuple(data_categories),
        region=region,
        processes_customer_data=processes_customer_data,
        dpa_status=dpa_status,
        dpa_expiry=dpa_expiry,
    )


def _ref(name, *, surface="provider-endpoint", observed_in="config"):
    return vr.UsageRef(name=name, surface=surface, observed_in=observed_in)


def _build(declared, usage, **kw):
    return vr.build_register(FakeUsageSource(usage), declared, now_iso=_NOW, **kw)


# ── CHECKS ─────────────────────────────────────────────────────────────────────────────
def test_used_but_undeclared_is_hard_finding() -> None:
    """covers: M4, E1, R:UNDECLARED_PROCESSOR"""
    reg = _build([_vendor("openai")], [_ref("openai"), _ref("shadow-analytics")])
    undeclared_names = {u.name for u in reg.undeclared}
    assert undeclared_names == {"shadow-analytics"}, (
        "a live-reached, undeclared processor is a hard finding"
    )
    assert reg.n_undeclared == 1


def test_tool_has_no_write_path() -> None:
    """covers: M1, R:TOOL_WRITES_REGISTER"""
    forbidden = ("write", "edit", "mutate", "delete", "revoke", "remove", "upsert")
    for name in (n for n in dir(vr) if not n.startswith("_")):
        assert not any(bad in name.lower() for bad in forbidden), f"{name} looks like a write path"
    proto_methods = {m for m in dir(vr.UsageSource) if not m.startswith("_")}
    assert proto_methods == {"processors"}, f"UsageSource must be read-only, got {proto_methods}"


def test_missing_dpa_never_documented_fails_closed() -> None:
    """covers: M3, A4, E5, R:ASSUMED_COMPLIANT"""
    # dpa not signed -> incomplete
    r1 = _build([_vendor("v_pending", dpa_status="pending")], [_ref("v_pending")])
    assert r1.vendors[0].classification == "incomplete"
    # signed but a required field missing (region) -> still incomplete, never documented
    r2 = _build([_vendor("v_blank", region="")], [_ref("v_blank")])
    assert r2.vendors[0].classification == "incomplete"
    assert r1.n_incomplete == 1 and r2.n_incomplete == 1


def test_expired_dpa_flagged_against_injected_now() -> None:
    """covers: M5, A3, E3"""
    # expiry exactly at now_iso is expired (fail-closed at the boundary)
    r_eq = _build([_vendor("v_edge", dpa_expiry=_NOW)], [_ref("v_edge")])
    assert r_eq.vendors[0].classification == "dpa_expired"
    r_past = _build([_vendor("v_old", dpa_expiry=_PAST)], [_ref("v_old")])
    assert r_past.vendors[0].classification == "dpa_expired"
    assert r_eq.n_expired == 1 and r_past.n_expired == 1


def test_declared_but_unused_flagged_not_hard() -> None:
    """covers: E2"""
    # fully compliant but not reached by the live system -> unused, not a hard finding
    reg = _build([_vendor("ghost")], usage=[])
    assert reg.vendors[0].classification == "unused"
    assert reg.vendors[0].used is False
    assert reg.n_undeclared == 0 and reg.n_incomplete == 0 and reg.n_expired == 0


def test_non_customer_data_vendor_listed_not_classified() -> None:
    """covers: A2, E6"""
    # infra that never touches customer data, missing its DPA, but reached live:
    infra = _vendor(
        "grafana-cloud", processes_customer_data=False, dpa_status="none", dpa_expiry=None
    )
    reg = _build([infra], [_ref("grafana-cloud")])
    classified_names = {v.vendor.name for v in reg.vendors}
    assert "grafana-cloud" not in classified_names, "infra is not risk-classified"
    # its declaration still suppresses a false undeclared finding (it appears in reconciliation)
    assert reg.n_undeclared == 0
    # and a missing DPA on non-customer-data infra never inflates the risk counts
    assert reg.n_incomplete == 0 and reg.n_expired == 0 and reg.n_vendors == 0


def test_review_record_unreviewed_until_signed() -> None:
    """covers: M6, E8, R:FABRICATED_SIGNOFF"""
    reg = _build([_vendor("openai")], [_ref("openai")])
    assert reg.review.reviewer == "unreviewed — draft", "the tool never signs for a human"


def test_incomplete_fetch_fails_not_truncates() -> None:
    """covers: M2, E4"""
    src = FakeUsageSource([_ref("openai")], raise_incomplete=True)
    with pytest.raises(vr.IncompleteFetch):
        vr.build_register(src, [_vendor("openai")], now_iso=_NOW)


def test_build_is_pure_deterministic_and_ordered() -> None:
    """covers: M2, A5, E7"""
    declared = [
        _vendor("d_ok"),  # documented (signed, future, used)
        _vendor("d_exp", dpa_expiry=_PAST),  # dpa_expired
        _vendor("d_inc", dpa_status="pending"),  # incomplete
        _vendor("d_unused"),  # unused (compliant but not reached)
    ]
    usage = [_ref("d_ok"), _ref("d_exp"), _ref("d_inc")]
    a = _build(list(declared), list(usage))
    b = _build(list(declared), list(usage))
    assert a == b, "identical declared + usage + now must be byte-identical"
    # A5 worst-severity first: dpa_expired, incomplete, documented, unused
    assert [v.classification for v in a.vendors] == [
        "dpa_expired",
        "incomplete",
        "documented",
        "unused",
    ]


def test_summary_is_payload_free_and_counted() -> None:
    """covers: A6"""
    reg = _build(
        [_vendor("openai"), _vendor("v_bad", dpa_status="pending")],
        [_ref("openai"), _ref("v_bad"), _ref("shadow-x")],
    )
    summary = vr.render_summary(reg)
    assert "undeclared" in summary.lower()
    assert "unreviewed — draft" in summary, "the review record shows in the summary"
    for secret in ("ghp_", "github_pat_", "token", "authorization", "secret"):
        assert secret not in summary.lower(), "the summary must be payload/secret free"
