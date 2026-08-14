"""CC9.2 vendor / subprocessor register (vendor-subprocessor-register TASK, FROZEN v1).

Produces an auditable subprocessor register: every DECLARED third-party processor of customer
data, classified documented / incomplete / dpa_expired / unused — AND reconciled against what
the system ACTUALLY reaches. A processor the system uses but which is absent from the declared
register is a HARD finding (M4, R:UNDECLARED_PROCESSOR); this is what keeps the register from
being a hand-maintained doc that quietly drifts from reality.

Two layers, cleanly split:
  * PURE core — `build_register(source, declared, *, now_iso)` over a `UsageSource` port. No IO,
    no clock-now, no network; `now_iso` is INJECTED, so identical source + declared + now yields
    byte-identical output (M2). This is the layer the suite drives through a zero-network fake.
  * IO adapter — `ConfigUsageSource`, the only thing that enumerates live processors. Design-for-
    failure: a truncated / partial enumeration raises `IncompleteFetch` so the core can NEVER
    reconcile against a partial usage set (which would hide an undeclared processor as "all
    declared", M2/E4).

Fail-closed is the spine: a declared vendor missing a required field or a signed, in-window DPA
is NEVER `documented` — it fails closed to `incomplete` (M3, R:ASSUMED_COMPLIANT); a DPA whose
expiry is on/before the injected `now_iso` is `dpa_expired`, computed not assumed (M5); the review
record reads `unreviewed — draft` until a real human signs (M6, R:FABRICATED_SIGNOFF). A vendor
that does not process customer data carries no DPA burden — it is listed for reconciliation but
NOT risk-classified (A2). The tool is READ + REPORT ONLY: it never edits the declared register
or mutates a vendor system (M1, R:TOOL_WRITES_REGISTER).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

DEFAULT_ORG = "pilotspace"
DEFAULT_REPO = "pilotspace/hydroa"
_UNREVIEWED = "unreviewed — draft"

Classification = Literal["documented", "incomplete", "dpa_expired", "unused"]

#: Listing order (A5): worst-severity first, then vendor name for a total, deterministic order.
_SEVERITY = {"dpa_expired": 0, "incomplete": 1, "documented": 2, "unused": 3}


class IncompleteFetch(Exception):
    """A UsageSource could not enumerate the WHOLE live processor set (rate limit / partial page).

    Raising this — rather than returning a short list — is what stops the core reconciling against
    a partial usage set and hiding an undeclared processor as "all declared" (M2, E4).
    """


# ── data ─────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Subprocessor:
    """One DECLARED third-party processor (scripts/soc2/subprocessors.json)."""

    name: str
    purpose: str
    data_categories: tuple[str, ...]
    region: str
    processes_customer_data: bool
    dpa_status: str  # "signed" | "pending" | "none" | ...  (anything but "signed" is incomplete)
    dpa_expiry: str | None  # ISO-8601 UTC of DPA expiry, or None if none on file


@dataclass(frozen=True)
class UsageRef:
    """A processor the system ACTUALLY reaches, as a UsageSource reports it."""

    name: str
    surface: str  # e.g. "provider-endpoint", "object-store", "egress-allow"
    observed_in: str  # where it was observed (config key / manifest)


@dataclass(frozen=True)
class ReviewedVendor:
    vendor: Subprocessor
    classification: Classification
    reason: str
    used: bool  # True when the declared vendor is present in the live UsageSource


@dataclass(frozen=True)
class ReviewRecord:
    reviewer: str  # `unreviewed — draft` until a real human signs (M6)
    generated_window: str  # human "as of" description (payload-free)


@dataclass(frozen=True)
class VendorRegister:
    vendors: tuple[ReviewedVendor, ...]  # customer-data processors only, severity-ordered (A5)
    undeclared: tuple[UsageRef, ...]  # live-reached but NOT declared — hard findings (M4)
    review: ReviewRecord
    n_vendors: int  # number of risk-classified (customer-data) vendors
    n_incomplete: int
    n_expired: int
    n_undeclared: int


class UsageSource(Protocol):
    """The port. Read-only by construction — its ONLY method enumerates live processors (M1)."""

    def processors(self, *, org: str, repo: str) -> Sequence[UsageRef]: ...


# ── pure core ────────────────────────────────────────────────────────────────────────────
def _parse(ts: str) -> datetime:
    """ISO-8601 → aware datetime. Trailing `Z` is normalised to `+00:00` (parse, not now())."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _required_present(v: Subprocessor) -> bool:
    """All fields a signed, auditable DPA needs. A blank/absent one fails CLOSED (M3, A4)."""
    return bool(
        v.purpose.strip()
        and v.region.strip()
        and v.data_categories
        and v.dpa_status == "signed"
        and v.dpa_expiry is not None
    )


def _classify(v: Subprocessor, *, used: bool, now: datetime) -> ReviewedVendor:
    if not _required_present(v):
        # M3/A4/E5: missing required field or unsigned DPA — never documented, fails closed.
        classification: Classification = "incomplete"
        reason = (
            f"missing a required field or signed DPA (dpa_status={v.dpa_status!r}, "
            f"expiry={v.dpa_expiry!r}) — fails closed under CC9.2"
        )
    elif now >= _parse(v.dpa_expiry):  # type: ignore[arg-type]  # _required_present proved not-None
        # M5/A3/E3: expiry on/before injected now — lapsed, never "valid" by omission.
        classification = "dpa_expired"
        reason = f"DPA expired (expiry {v.dpa_expiry} on/before as-of instant)"
    elif not used:
        # E2: compliant but not reached by the live system — a removal candidate, not a hard fail.
        classification = "unused"
        reason = "declared with a signed, in-window DPA but not observed in live usage"
    else:
        classification = "documented"
        reason = f"signed DPA in window ({v.dpa_expiry}); processes {', '.join(v.data_categories)}"

    return ReviewedVendor(vendor=v, classification=classification, reason=reason, used=used)


def build_register(
    source: UsageSource,
    declared: Sequence[Subprocessor],
    *,
    org: str = DEFAULT_ORG,
    repo: str = DEFAULT_REPO,
    now_iso: str,
) -> VendorRegister:
    """Classify every customer-data subprocessor and reconcile the whole declared list against
    live usage. PURE over the source's output and the injected `now_iso` (M2).

    An `IncompleteFetch` from the source PROPAGATES — the core reconciles only against a usage set
    a source enumerated in full (M2, E4). Well-formed source output never raises.
    """
    now = _parse(now_iso)
    usage = source.processors(org=org, repo=repo)
    usage_names = {u.name for u in usage}
    # ALL declared names (incl. non-customer-data infra) suppress false undeclared findings (A2).
    declared_names = {v.name for v in declared}

    # M4/E1: a live-reached processor absent from the declared register is a hard finding.
    undeclared = tuple(u for u in usage if u.name not in declared_names)

    # A2/E6: only customer-data processors are risk-classified; infra is listed-for-reconciliation.
    risk_vendors = [v for v in declared if v.processes_customer_data]
    reviewed = [_classify(v, used=v.name in usage_names, now=now) for v in risk_vendors]
    # A5: total order — worst severity first, then name. Fully deterministic (E7).
    reviewed.sort(key=lambda r: (_SEVERITY[r.classification], r.vendor.name))

    review = ReviewRecord(reviewer=_UNREVIEWED, generated_window=f"as of {now_iso}")
    return VendorRegister(
        vendors=tuple(reviewed),
        undeclared=undeclared,
        review=review,
        n_vendors=len(reviewed),
        n_incomplete=sum(1 for r in reviewed if r.classification == "incomplete"),
        n_expired=sum(1 for r in reviewed if r.classification == "dpa_expired"),
        n_undeclared=len(undeclared),
    )


# ── rendering ────────────────────────────────────────────────────────────────────────────
def render_summary(register: VendorRegister) -> str:
    """A stable, payload-free human summary for an auditor (A6). No token/secret ever appears."""
    r = register
    lines = [
        "CC9.2 vendor / subprocessor register",
        f"reviewer: {r.review.reviewer} · {r.review.generated_window}",
        f"{r.n_vendors} customer-data processors · {r.n_incomplete} incomplete · "
        f"{r.n_expired} dpa-expired · {r.n_undeclared} undeclared",
        "",
    ]
    for v in r.vendors:
        lines.append(
            f"- {v.vendor.name} · {v.vendor.region} · {v.classification} "
            f"· {'used' if v.used else 'unused'} — {v.reason}"
        )
    for u in r.undeclared:
        lines.append(f"- ⚠ UNDECLARED: {u.name} (observed in {u.observed_in}) — add to register")
    return "\n".join(lines)


def as_dict(register: VendorRegister) -> dict[str, object]:
    """Machine-readable register. Deterministic; contains no token/secret."""
    return {
        "reviewer": register.review.reviewer,
        "generated_window": register.review.generated_window,
        "n_vendors": register.n_vendors,
        "n_incomplete": register.n_incomplete,
        "n_expired": register.n_expired,
        "n_undeclared": register.n_undeclared,
        "vendors": [
            {
                "name": v.vendor.name,
                "purpose": v.vendor.purpose,
                "data_categories": list(v.vendor.data_categories),
                "region": v.vendor.region,
                "dpa_status": v.vendor.dpa_status,
                "dpa_expiry": v.vendor.dpa_expiry,
                "classification": v.classification,
                "used": v.used,
                "reason": v.reason,
            }
            for v in register.vendors
        ],
        "undeclared": [
            {"name": u.name, "surface": u.surface, "observed_in": u.observed_in}
            for u in register.undeclared
        ],
    }


# ── IO adapter (design-for-failure; the only thing that enumerates live usage) ────────────
class ConfigUsageSource:
    """Enumerates the external processors the system ACTUALLY reaches (M2/M4).

    In production this is derived deterministically from the deploy surface — the gateway model-
    provider registry, the egress allow-list, and the object-store / datastore config — NOT the
    network. It is read-only by construction: no revoke/edit/mutate path exists (M1). A partial
    or unreadable config raises `IncompleteFetch` rather than returning a short list, so the core
    never reconciles against an incomplete usage set (E4).
    """

    def __init__(self, *, config_root: str | None = None) -> None:
        self._config_root = config_root or os.environ.get("HYDROA_CONFIG_ROOT", "")

    def processors(self, *, org: str, repo: str) -> Sequence[UsageRef]:  # pragma: no cover
        raise NotImplementedError(
            "live usage enumeration is provisioned by the operator's run environment; the pure "
            "core and its reconciliation semantics are what this task freezes and tests. Wire this "
            "to the deterministic deploy surface — the model-provider registry, the egress "
            "allow-list (infra/), and the object-store/datastore config — returning a UsageRef per "
            "external processor, and raising IncompleteFetch on any unreadable/partial source. "
            "NEVER issue a write/DELETE against a vendor system from here."
        )


# ── CLI ──────────────────────────────────────────────────────────────────────────────────
def _load_declared(path: str) -> list[Subprocessor]:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return [
        Subprocessor(
            name=e["name"],
            purpose=e.get("purpose", ""),
            data_categories=tuple(e.get("data_categories", [])),
            region=e.get("region", ""),
            processes_customer_data=bool(e.get("processes_customer_data", False)),
            dpa_status=e.get("dpa_status", "none"),
            dpa_expiry=e.get("dpa_expiry"),
        )
        for e in raw.get("subprocessors", [])
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CC9.2 vendor / subprocessor register")
    parser.add_argument("--now", required=True, help="ISO-8601 UTC 'as of' instant (injected)")
    parser.add_argument("--org", default=DEFAULT_ORG)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument(
        "--declared",
        default=os.path.join(os.path.dirname(__file__), "subprocessors.json"),
        help="the human-maintained declared subprocessor register",
    )
    parser.add_argument("--json-out", default="vendor_register.json")
    args = parser.parse_args(argv)

    source = ConfigUsageSource()
    try:
        register = build_register(
            source,
            _load_declared(args.declared),
            org=args.org,
            repo=args.repo,
            now_iso=args.now,
        )
    except IncompleteFetch as exc:
        print(f"USAGE ENUMERATION INCOMPLETE — register NOT written: {exc}", file=sys.stderr)
        return 2

    with open(args.json_out, "w", encoding="utf-8") as fh:
        json.dump(as_dict(register), fh, indent=2, sort_keys=True)
    print(render_summary(register))
    # An undeclared / incomplete / expired processor is the auditor signal — exit non-zero.
    signal = register.n_undeclared or register.n_incomplete or register.n_expired
    return 1 if signal else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
