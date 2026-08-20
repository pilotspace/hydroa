"""S5 / M9 — a swallowed audit-write failure must be DETECTABLE without ever being blocking.

covers: M9, A30, A31, A32, A33, A34, A35, E8

record_audit swallows every exception by design (audit_writer.py:48 — fail-open, and
R:AUDIT_BLOCKS_REQUEST keeps it that way). Today it swallows them into a log line and nothing
else, so "the audit control stopped operating" is invisible to an operator: evidence can go
missing for a whole window with every request returning 200. That is the CC7.2 gap M9 closes —
add a signal, never a raise.

The counter this asserts is not invented for the task. The repo already has the exact
precedent: `credits_gate_degraded_total` (observability/metrics.py) pairs with the credit
guard's fail-open warning so the degrade is measurable, and `Otel._inc_counter`
(observability/otel.py:302) is the shipped shape for A33's "the counter itself can never
raise" — a best-effort try/except/pass INSIDE the already-swallowing branch, because a counter
failure must not become the audit failure it is reporting.

This check is deliberately name-agnostic: it looks for a counter family on the app's
CollectorRegistry whose name marks it as an audit-write failure, rather than pinning a literal
Build has not chosen yet. The recommended name, mirroring the precedent above, is
`gateway_audit_write_failed_total`.

RED TODAY: no such counter family exists on the registry, so leg 2 fails naming every counter
that IS registered.

GREEN-BY-DESIGN DISCLOSURE: leg 3 (A33 — a raising counter must not break fail-open) can only
run once a counter exists to break; it is skipped, loudly, until then. It is not a silent pass.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import httpx
import pytest

from gateway.audit.domain.audit_event import AuditEvent
from gateway.audit.infrastructure.audit_repository import AuditRepository

pytestmark = pytest.mark.asyncio

# Any counter family whose name marks it as "an audit write failed". Deliberately a family of
# spellings, not one literal — M9 does not name the metric, and false-redding a correct Build
# over a naming choice the contract never made is exactly the failure mode A31's probe warns
# about. What it will NOT accept is the absence of any such family.
_AUDIT_TOKENS = ("audit",)
_FAILURE_TOKENS = ("fail", "error", "drop", "swallow", "degrad", "lost", "unwritten")

RECOMMENDED_NAME = "gateway_audit_write_failed_total"

ANCHOR = "/admin/keys"


def _is_audit_failure_family(name: str) -> bool:
    lowered = name.lower()
    return any(t in lowered for t in _AUDIT_TOKENS) and any(t in lowered for t in _FAILURE_TOKENS)


def _counter_families(app: Any) -> dict[str, float]:
    """{family name -> summed _total value} for every COUNTER on this app's registry."""
    totals: dict[str, float] = {}
    for family in app.state.metrics_registry.registry.collect():
        if family.type != "counter":
            continue
        total = 0.0
        for sample in family.samples:
            if sample.name.endswith("_total"):
                total += float(sample.value)
        totals[family.name] = total
    return totals


def _audit_failure_totals(app: Any) -> dict[str, float]:
    return {n: v for n, v in _counter_families(app).items() if _is_audit_failure_family(n)}


class _Exploder:
    """Stands in for AuditRepository.record and raises — one raise per audit write attempted.

    Counting the raises is what makes A31 ("one increment per SWALLOWED exception, not per
    attempt, not per row") checkable without knowing how many audit writes the anchor endpoint
    schedules, or how many stragglers from the tenant bootstrap are still in flight.
    """

    def __init__(self) -> None:
        self.calls = 0
        # Parked on, never polled: the raise happens in a fire-and-forget task, and waiting on
        # an Event the raising code itself sets keys the wait to the DEFECT rather than to a
        # wall-clock guess.
        self.raised = asyncio.Event()

    def reset(self) -> None:
        self.calls = 0
        self.raised.clear()

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Replace AuditRepository.record with a raising FUNCTION (not this instance).

        Patching the class attribute with a callable OBJECT would not bind — `repo.record`
        would hand the event in as `self` and the write would die on a TypeError instead of
        the RuntimeError this check is simulating, which is a different failure entirely.
        """
        exploder = self

        async def _record(_self: Any, _event: AuditEvent) -> None:
            exploder.calls += 1
            exploder.raised.set()
            raise RuntimeError("audit_coverage: simulated audit-write failure (S5)")

        monkeypatch.setattr(AuditRepository, "record", _record)


async def _await_calls(exploder: _Exploder, *, budget_s: float = 1.0) -> None:
    """record_audit runs fire-and-forget, so wait for the raise to actually happen."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(exploder.raised.wait(), budget_s)
    # The increment sits in the same except branch as the raise; give that branch a beat to
    # finish before reading the registry.
    await asyncio.sleep(0.05)


async def test_audit_write_failure_is_detectable(
    app: Any,
    client: httpx.AsyncClient,
    tenant: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing audit write leaves the request untouched AND moves a counter."""
    auth = {"Authorization": f"Bearer {tenant['jwt']}"}
    before = _audit_failure_totals(app)
    all_counters_before = sorted(_counter_families(app))

    exploder = _Exploder()
    exploder.install(monkeypatch)

    resp = await client.post(ANCHOR, json={"name": "s5-detectable"}, headers=auth)

    # LEG 1 — E8 / R:AUDIT_BLOCKS_REQUEST: fail-open posture is UNCHANGED. This leg is green
    # today and must stay green; M9 adds a signal, it does not add a failure mode.
    assert resp.status_code == 201, (
        "R:AUDIT_BLOCKS_REQUEST: a failing audit write must never change the HTTP outcome of "
        f"the mutation — POST {ANCHOR} returned {resp.status_code}: {resp.text}"
    )

    await _await_calls(exploder)
    assert exploder.calls >= 1, (
        f"POST {ANCHOR} is the M5 audit anchor — it must attempt an audit write, or this check "
        "is vacuous. No write was attempted within the budget."
    )

    # LEG 2 — M9 / A31: the swallowed failure moved a counter. THE RED.
    after = _audit_failure_totals(app)
    assert after, (
        f"{exploder.calls} audit write(s) failed and were swallowed, and NOTHING on the metrics "
        "surface says so — a window of silent evidence loss is invisible to an operator (M9, "
        f"CC7.2). No counter family on the registry names an audit-write failure; recommended: "
        f"{RECOMMENDED_NAME}, mirroring credits_gate_degraded_total's own fail-open pairing.\n"
        f"  counter families registered today: {all_counters_before}"
    )

    moved = {name: after[name] - before.get(name, 0.0) for name in after}
    total_moved = sum(moved.values())
    assert total_moved == float(exploder.calls), (
        f"A31: exactly one increment per SWALLOWED exception — {exploder.calls} write(s) raised "
        f"and were swallowed, but the counter(s) moved by {total_moved}: {moved}. Counting "
        "attempts, rows, or retries instead of swallowed exceptions makes the signal unreadable "
        "as 'how many audit events were lost'."
    )

    # LEG 3 — A33: the counter itself can never raise. Only runnable once leg 2 has something
    # to break; until then it is loudly skipped rather than silently passing.
    families = sorted(after)
    attr = _counter_attribute(app, families[0])
    if attr is None:
        pytest.skip(
            f"A33 deferred: found counter family {families[0]!r} but no attribute on "
            "MetricsRegistry holds it, so it cannot be replaced with a raising stub here."
        )
    monkeypatch.setattr(app.state.metrics_registry, attr, _RaisingCounter())
    exploder.reset()
    resp2 = await client.post(ANCHOR, json={"name": "s5-counter-raises"}, headers=auth)
    assert resp2.status_code == 201, (
        "A33: a counter that raises must not become the audit failure it is reporting — the "
        f"increment is best-effort INSIDE the already-swallowing branch (otel.py:302 is the "
        f"shipped shape). POST {ANCHOR} returned {resp2.status_code}: {resp2.text}"
    )
    await _await_calls(exploder)
    assert exploder.calls >= 1, "the second drive must still attempt (and fail) an audit write"


class _RaisingCounter:
    """A metric object that raises on every use — A33's adversary."""

    def labels(self, *_args: Any, **_kwargs: Any) -> _RaisingCounter:
        raise RuntimeError("audit_coverage: simulated metrics-surface outage (A33)")

    def inc(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("audit_coverage: simulated metrics-surface outage (A33)")


def _counter_attribute(app: Any, family_name: str) -> str | None:
    """The MetricsRegistry attribute holding the counter for `family_name`, if any.

    prometheus_client strips the `_total` suffix into `_name`, so match on both spellings.
    """
    registry = app.state.metrics_registry
    bare = family_name[: -len("_total")] if family_name.endswith("_total") else family_name
    for attr, value in vars(registry).items():
        name = getattr(value, "_name", None)
        if isinstance(name, str) and name in (family_name, bare):
            return attr
    return None
