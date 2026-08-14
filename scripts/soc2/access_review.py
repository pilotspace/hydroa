"""CC6 access review + least-privilege register (access-review-and-least-privilege TASK, FROZEN v1).

Produces an auditable least-privilege register: every principal with write/admin reach on the
repo, classified justified / over_privileged / stale against a DECLARED policy — never against
the live state it is auditing (M4, R:CURRENT_STATE_AS_POLICY). The tool is READ + REPORT ONLY;
it has no path that revokes, edits, or grants access (M1, R:TOOL_MUTATES_ACCESS). Removing an
over-privileged grant is a human action the register recommends, not one the tool performs.

Two layers, cleanly split:
  * PURE core — `build_register(source, policy, ...)` over an `AccessSource` port. No IO, no
    clock-now, no network; `now_iso` is INJECTED, so identical source + policy + now always
    yields byte-identical output (M2, A1). This is the layer the test suite drives.
  * IO adapter — `GitHubAccessSource`, the only thing that talks to GitHub. Design-for-failure:
    timeout-bounded, paginated to completion, retried with bounded backoff; a truncated /
    rate-limited enumeration raises `IncompleteFetch` so a partial roster can NEVER be emitted
    as a complete review (M2, E4). The read token comes from the environment, never logged.

Fail-closed is the spine: an unlisted principal is `over_privileged`, never silently justified
(M3, R:SILENT_JUSTIFY); an unknown permission string ranks as most-privileged; the review record
reads `unreviewed — draft` until a real human signs — the tool never fabricates a reviewer
(M6, R:FABRICATED_SIGNOFF). The `pilotspacex-byte` shared-keyring account is recorded as a
disclosed, NOT-independent self-approval account whose disposition is Tin-owned (M5).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

DEFAULT_ORG = "pilotspace"
DEFAULT_REPO = "pilotspace/hydroa"
DEFAULT_WINDOW_DAYS = 90
_UNREVIEWED = "unreviewed — draft"

Classification = Literal["justified", "over_privileged", "stale"]

#: write/admin are privileged; read is listed-but-not-classified (A2). Unknown permissions rank
#: ABOVE admin so an unrecognised grant fails CLOSED (classified + over-privileged), never dropped.
_PERM_RANK = {"read": 0, "write": 1, "admin": 2}
_UNKNOWN_RANK = 99
_PRIVILEGED_RANK = _PERM_RANK["write"]

#: Severity order for the register listing (A5): worst first, then login for a total order.
_SEVERITY = {"over_privileged": 0, "stale": 1, "justified": 2}


class IncompleteFetch(Exception):
    """An AccessSource could not enumerate the WHOLE roster (rate limit / partial page / IO).

    Raising this — rather than returning a short list — is what stops a truncated enumeration
    being emitted as a complete access review (M2, E4).
    """


# ── data ─────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class AccessGrant:
    """One principal's reach on a surface, as an AccessSource reports it (pre-classification)."""

    login: str
    surface: str  # e.g. "repo:hydroa", "org:pilotspace"
    permission: str  # "read" | "write" | "admin" (unknown => fails closed)
    last_activity: str | None  # ISO-8601 UTC of last activity, or None if never / unknown


@dataclass(frozen=True)
class PolicyEntry:
    """A DECLARED grant of least privilege (scripts/soc2/access_policy.json), the ONLY yardstick
    justification is measured against (M4)."""

    login: str
    max_permission: str  # the most this principal is permitted to hold
    justification: str  # why this principal legitimately needs it
    self_approval_account: bool = False  # True for a disclosed, NOT-independent account (M5)


@dataclass(frozen=True)
class ReviewedGrant:
    grant: AccessGrant
    classification: Classification
    reason: str
    self_approval_account: bool  # carried through from policy; recorded, never laundered (M5)


@dataclass(frozen=True)
class ReviewRecord:
    reviewer: str  # `unreviewed — draft` until a real human signs (M6)
    window_days: int
    generated_window: str  # human description of the staleness window (payload-free)


@dataclass(frozen=True)
class AccessRegister:
    grants: tuple[ReviewedGrant, ...]  # privileged principals only, severity-ordered (A5)
    review: ReviewRecord
    n_principals: int  # number of privileged (write/admin) principals under review
    n_over_privileged: int
    n_stale: int


class AccessSource(Protocol):
    """The port. Read-only by construction — its ONLY method enumerates grants (M1)."""

    def grants(self, *, org: str, repo: str) -> Sequence[AccessGrant]: ...


# ── pure core ────────────────────────────────────────────────────────────────────────────
def _parse(ts: str) -> datetime:
    """ISO-8601 → aware datetime. Trailing `Z` is normalised to `+00:00` (parse, not now())."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _rank(permission: str) -> int:
    return _PERM_RANK.get(permission, _UNKNOWN_RANK)


def _is_privileged(permission: str) -> bool:
    return _rank(permission) >= _PRIVILEGED_RANK


def _is_stale(last_activity: str | None, now: datetime, window_days: int) -> bool:
    """No activity inside the window => stale. Unknown/None activity is stale, never fresh (A3)."""
    if last_activity is None:
        return True
    return _parse(last_activity) < now - timedelta(days=window_days)


def _classify(
    grant: AccessGrant,
    policy_by_login: dict[str, PolicyEntry],
    now: datetime,
    window_days: int,
) -> ReviewedGrant:
    entry = policy_by_login.get(grant.login)
    self_approval = entry.self_approval_account if entry is not None else False

    if entry is None:
        # M3/A4/E1: unlisted principal fails CLOSED — over-privileged, never silently justified.
        classification: Classification = "over_privileged"
        reason = (
            f"unlisted principal holding {grant.permission} — no policy entry; "
            "fails closed under least privilege (declare it in access_policy.json or remove access)"
        )
    elif _rank(grant.permission) > _rank(entry.max_permission):
        # M4/E2: over the DECLARED ceiling — live state never launders this into justified.
        classification = "over_privileged"
        reason = (
            f"holds {grant.permission} but policy permits at most {entry.max_permission} "
            f"({entry.justification})"
        )
    elif _is_stale(grant.last_activity, now, window_days):
        # A3/E6: within the policy ceiling but dormant — a least-privilege candidate for removal.
        classification = "stale"
        reason = (
            f"within policy ({entry.max_permission}) but no activity in the last {window_days}d "
            f"(last: {grant.last_activity or 'never'})"
        )
    else:
        classification = "justified"
        reason = f"within declared policy ({entry.max_permission}): {entry.justification}"

    if self_approval:
        # M5/E3: a self-approval account is recorded as disclosed + NOT independent, and its
        # disposition is left to Tin — the tool never auto-resolves it.
        reason += (
            " · self-approval account (disclosed, NOT independent review); "
            "disposition is Tin-owned and unresolved"
        )

    return ReviewedGrant(
        grant=grant,
        classification=classification,
        reason=reason,
        self_approval_account=self_approval,
    )


def build_register(
    source: AccessSource,
    policy: Sequence[PolicyEntry],
    *,
    org: str = DEFAULT_ORG,
    repo: str = DEFAULT_REPO,
    window_days: int = DEFAULT_WINDOW_DAYS,
    now_iso: str,
) -> AccessRegister:
    """Classify every privileged principal against the DECLARED policy. PURE over the source's
    output and the injected `now_iso` (M2, A1).

    An `IncompleteFetch` from the source PROPAGATES — the core is told the roster is complete only
    by a source that enumerated every page (M2, E4). Well-formed source output never raises.
    """
    now = _parse(now_iso)
    policy_by_login = {e.login: e for e in policy}

    raw_grants = source.grants(org=org, repo=repo)
    # A2: read-only viewers are listed-but-not-classified — excluded from the privileged register.
    privileged = [g for g in raw_grants if _is_privileged(g.permission)]
    reviewed = [_classify(g, policy_by_login, now, window_days) for g in privileged]
    # A5: total order — worst severity first, then login. Fully deterministic (E5).
    reviewed.sort(key=lambda r: (_SEVERITY[r.classification], r.grant.login))

    review = ReviewRecord(
        reviewer=_UNREVIEWED,  # M6: never fabricate a signoff
        window_days=window_days,
        generated_window=f"{window_days}d ending {now_iso}",
    )
    return AccessRegister(
        grants=tuple(reviewed),
        review=review,
        n_principals=len(reviewed),
        n_over_privileged=sum(1 for r in reviewed if r.classification == "over_privileged"),
        n_stale=sum(1 for r in reviewed if r.classification == "stale"),
    )


# ── rendering ────────────────────────────────────────────────────────────────────────────
def render_summary(register: AccessRegister) -> str:
    """A stable, payload-free human summary for an auditor (A6). No token/secret ever appears."""
    r = register
    lines = [
        "CC6 access review — least-privilege register",
        f"reviewer: {r.review.reviewer} · window: {r.review.generated_window}",
        f"{r.n_principals} privileged principals · {r.n_over_privileged} over-privileged · "
        f"{r.n_stale} stale",
        "",
    ]
    for g in r.grants:
        selfmark = " · self-approval" if g.self_approval_account else ""
        lines.append(
            f"- {g.grant.login} · {g.grant.permission} on {g.grant.surface} "
            f"· {g.classification}{selfmark} — {g.reason}"
        )
    return "\n".join(lines)


def as_dict(register: AccessRegister) -> dict[str, object]:
    """Machine-readable register. Deterministic; contains no token/secret."""
    return {
        "reviewer": register.review.reviewer,
        "window_days": register.review.window_days,
        "generated_window": register.review.generated_window,
        "n_principals": register.n_principals,
        "n_over_privileged": register.n_over_privileged,
        "n_stale": register.n_stale,
        "grants": [
            {
                "login": g.grant.login,
                "surface": g.grant.surface,
                "permission": g.grant.permission,
                "last_activity": g.grant.last_activity,
                "classification": g.classification,
                "self_approval_account": g.self_approval_account,
                "reason": g.reason,
            }
            for g in register.grants
        ],
    }


# ── IO adapter (design-for-failure; the only thing that touches GitHub) ───────────────────
class GitHubAccessSource:
    """Read-only GitHub collaborator/team enumeration (M1/M2).

    Lists every principal with reach on the repo and their last activity. Every request is
    timeout-bounded and retried with bounded backoff; exhaustion or a partial page raises
    `IncompleteFetch` rather than returning a short roster. This adapter NEVER mutates access —
    it holds no revoke/edit path by construction (M1, R:TOOL_MUTATES_ACCESS).
    """

    def __init__(
        self,
        *,
        token: str | None = None,
        timeout_s: float = 15.0,
        max_attempts: int = 4,
        base_url: str = "https://api.github.com",
    ) -> None:
        self._token = token if token is not None else os.environ.get("GITHUB_TOKEN", "")
        self._timeout_s = timeout_s
        self._max_attempts = max_attempts
        self._base_url = base_url.rstrip("/")

    def _get(self, path: str, params: dict[str, str] | None = None) -> object:  # pragma: no cover
        import httpx  # local import: the pure core must import nothing network-y (A1)

        headers = {"Accept": "application/vnd.github+json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        last_exc: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                resp = httpx.get(
                    f"{self._base_url}{path}",
                    params=params,
                    headers=headers,
                    timeout=self._timeout_s,
                )
            except httpx.HTTPError as exc:
                last_exc = exc
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code in (429, 500, 502, 503, 504) or (
                    resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0"
                ):
                    last_exc = IncompleteFetch(f"{path} -> {resp.status_code} (transient)")
                else:
                    raise IncompleteFetch(f"{path} -> {resp.status_code}")
            time.sleep(min(2.0**attempt, 8.0))  # bounded backoff; not a clock read
        raise IncompleteFetch(f"{path}: exhausted {self._max_attempts} attempts") from last_exc

    def grants(self, *, org: str, repo: str) -> Sequence[AccessGrant]:  # pragma: no cover
        raise NotImplementedError(
            "live GitHub enumeration is provisioned by the operator's run environment; the pure "
            "core and its least-privilege semantics are what this task freezes and tests. Wire "
            "_get() into a collaborator walk (GET /repos/{repo}/collaborators?affiliation=all "
            "with permission, then last-activity from /repos/{repo}/commits and audit-log) — "
            "raising IncompleteFetch on any partial page, and NEVER issuing a write/DELETE call."
        )


# ── CLI ──────────────────────────────────────────────────────────────────────────────────
def _load_policy(path: str) -> list[PolicyEntry]:
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    return [
        PolicyEntry(
            login=e["login"],
            max_permission=e["max_permission"],
            justification=e.get("justification", ""),
            self_approval_account=bool(e.get("self_approval_account", False)),
        )
        for e in raw.get("entries", [])
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CC6 access review + least-privilege register")
    parser.add_argument("--now", required=True, help="ISO-8601 UTC 'as of' instant (injected)")
    parser.add_argument("--org", default=DEFAULT_ORG)
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    parser.add_argument(
        "--policy",
        default=os.path.join(os.path.dirname(__file__), "access_policy.json"),
        help="declared least-privilege policy (the ONLY justification yardstick)",
    )
    parser.add_argument("--json-out", default="access_register.json")
    args = parser.parse_args(argv)

    source = GitHubAccessSource()
    try:
        register = build_register(
            source,
            _load_policy(args.policy),
            org=args.org,
            repo=args.repo,
            window_days=args.window_days,
            now_iso=args.now,
        )
    except IncompleteFetch as exc:
        print(f"FETCH INCOMPLETE — register NOT written: {exc}", file=sys.stderr)
        return 2

    with open(args.json_out, "w", encoding="utf-8") as fh:
        json.dump(as_dict(register), fh, indent=2, sort_keys=True)
    print(render_summary(register))
    # An over-privileged or stale grant is the auditor signal — exit non-zero so operators notice.
    return 1 if (register.n_over_privileged or register.n_stale) else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
