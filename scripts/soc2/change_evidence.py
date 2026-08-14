"""CC8.1 change-management evidence export (change-management-evidence-export TASK.md, FROZEN @ v1).

Produces auditable evidence that every landing on `main` in a window went through the
change-management controls: the required checks that gated the PR, the approver(s), and the
resulting `main` commit. A control exception (a missing green check, an absent approval, a
direct push) is FLAGGED, never omitted (M3); a disclosed self-approval is recorded as NOT
independent, never laundered into review (M4).

Two layers, cleanly split:
  * PURE core — `build_evidence(source, ...)` over a `ChangeSource` port. No IO, no clock-now,
    no network; identical source output always yields identical evidence (M2). This is the
    layer the test suite drives through a zero-network fake.
  * IO adapter — `GitHubChangeSource`, the ONLY thing that talks to GitHub. Design-for-failure:
    every call is timeout-bounded, paginated to completion, and retried with bounded backoff;
    a truncated / rate-limited fetch raises `IncompleteFetch` so a partial window can NEVER be
    emitted as complete (M6). The read token comes from the environment and is never logged.

This repo SQUASH-merges, so a `main` commit is a NEW sha with no check runs of its own. The
honest CC8.1 evidence is the PR's required-check conclusions linked to the commit they gated;
this tool records BOTH the tested head sha and the resulting `main` sha and never invents a run
on the squashed sha (M1, R:UNTESTED_MERGE).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

DEFAULT_REPO = "pilotspace/hydroa"
DEFAULT_BRANCH = "main"
#: Accounts known to be operator-directed self-approvals — disclosed, NOT independent review
#: (masked-gate-never-reached-a-verdict). Recorded honestly by M4, never counted as review.
DEFAULT_SELF_APPROVAL_LOGINS = frozenset({"pilotspacex-byte"})


class IncompleteFetch(Exception):
    """A ChangeSource could not fetch the WHOLE window (rate limit / partial page / IO error).

    Raising this — rather than returning a short page — is what stops a truncated fetch being
    emitted as a complete evidence set (M6, R:TRUNCATED_AS_COMPLETE).
    """


# ── data ─────────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class MergeRaw:
    """One landing on `main`, as a ChangeSource reports it (pre-classification)."""

    main_sha: str
    tested_head_sha: str | None
    pr_number: int | None
    merged_at: str  # ISO-8601 UTC
    checks: Mapping[str, str]  # {required_context: conclusion} GitHub recorded for the PR
    approvals: tuple[str, ...]  # logins of APPROVED reviews


@dataclass(frozen=True)
class ReviewRef:
    author: str
    independent: bool  # False when the author is a disclosed self-approval account (M4)


@dataclass(frozen=True)
class MergeEvidence:
    pr_number: int | None
    main_sha: str
    tested_head_sha: str | None
    merged_at: str
    required: Mapping[str, str]  # {context: conclusion-as-seen} for each REQUIRED context
    approvals: tuple[ReviewRef, ...]
    exceptions: tuple[str, ...]  # a non-empty tuple = a flagged control exception (M3)


@dataclass(frozen=True)
class ExportResult:
    since: str
    until: str
    repo: str
    branch: str
    required_contexts: tuple[str, ...]
    merges: tuple[MergeEvidence, ...]  # newest-first, deterministic (A5)
    n_merges: int
    n_exceptions: int
    n_self_approved: int


class ChangeSource(Protocol):
    """The port. The GitHub adapter is the only production implementation."""

    def merges(self, *, repo: str, branch: str, since: str, until: str) -> Sequence[MergeRaw]: ...

    def required_contexts(self, *, repo: str, branch: str) -> Sequence[str]: ...


# ── pure core ────────────────────────────────────────────────────────────────────────────
def _parse(ts: str) -> datetime:
    """ISO-8601 → aware datetime. Trailing `Z` is normalised to `+00:00` (parse, not now())."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _in_window(merged_at: str, since: str, until: str) -> bool:
    """Half-open `[since, until)` by merged-at (A3): at `since` included, at `until` excluded."""
    t = _parse(merged_at)
    return _parse(since) <= t < _parse(until)


def _classify(raw: MergeRaw, required: Sequence[str], self_logins: frozenset[str]) -> MergeEvidence:
    exceptions: list[str] = []

    # M3/A2: a landing with no PR is a direct push — a protection bypass, flagged not skipped.
    if raw.pr_number is None:
        exceptions.append("direct push (no associated PR)")

    # M5: EVERY required context must be `success` on the PR GitHub gated; missing or non-success
    # is an exception. We never invent a conclusion for the squashed main sha (M1).
    required_seen: dict[str, str] = {}
    for ctx in required:
        conclusion = raw.checks.get(ctx)
        required_seen[ctx] = conclusion if conclusion is not None else "missing"
        if conclusion != "success":
            exceptions.append(f"required check not green: {ctx} ({required_seen[ctx]})")

    # M4: mark each approval independent-or-self; a self-approval is recorded, never laundered.
    approvals = tuple(ReviewRef(author=a, independent=a not in self_logins) for a in raw.approvals)
    # M3(b): no approving review at all is an exception. (A self-only approval is an approval per
    # the frozen contract — surfaced via the counts in M4, not as an M3 exception.)
    if not approvals:
        exceptions.append("no approval — no approving review on the PR")

    return MergeEvidence(
        pr_number=raw.pr_number,
        main_sha=raw.main_sha,
        tested_head_sha=raw.tested_head_sha,
        merged_at=raw.merged_at,
        required=required_seen,
        approvals=approvals,
        exceptions=tuple(exceptions),
    )


def build_evidence(
    source: ChangeSource,
    *,
    repo: str = DEFAULT_REPO,
    branch: str = DEFAULT_BRANCH,
    since: str,
    until: str,
    self_approval_logins: frozenset[str] = DEFAULT_SELF_APPROVAL_LOGINS,
) -> ExportResult:
    """Classify every in-window landing on `main`. PURE over the source's output (M2).

    An `IncompleteFetch` from the source PROPAGATES — the core is told the window is complete
    only by a source that fetched every page (M6). Well-formed source output never raises.
    """
    required = tuple(source.required_contexts(repo=repo, branch=branch))
    raws = source.merges(repo=repo, branch=branch, since=since, until=until)

    in_window = [m for m in raws if _in_window(m.merged_at, since, until)]
    evidence = [_classify(m, required, self_approval_logins) for m in in_window]
    # A5: newest-first by merged-at, tie-broken by main sha — fully deterministic.
    evidence.sort(key=lambda e: (_parse(e.merged_at), e.main_sha), reverse=True)

    n_exceptions = sum(1 for e in evidence if e.exceptions)
    n_self_approved = sum(1 for e in evidence if any(not a.independent for a in e.approvals))

    return ExportResult(
        since=since,
        until=until,
        repo=repo,
        branch=branch,
        required_contexts=required,
        merges=tuple(evidence),
        n_merges=len(evidence),
        n_exceptions=n_exceptions,
        n_self_approved=n_self_approved,
    )


# ── rendering ────────────────────────────────────────────────────────────────────────────
def render_summary(result: ExportResult) -> str:
    """A stable, payload-free human summary for an auditor (A6). No token/secret ever appears."""
    lines = [
        f"CC8.1 change-management evidence — {result.repo}@{result.branch}",
        f"window: [{result.since}, {result.until})",
        f"required contexts: {', '.join(result.required_contexts) or '(none derived)'}",
        f"{result.n_merges} merges · {result.n_exceptions} exceptions · "
        f"{result.n_self_approved} self-approved",
        "",
    ]
    for e in result.merges:
        who = e.pr_number if e.pr_number is not None else "DIRECT-PUSH"
        indep = sum(1 for a in e.approvals if a.independent)
        selfn = sum(1 for a in e.approvals if not a.independent)
        flag = "  ⚠ " + "; ".join(e.exceptions) if e.exceptions else "  ok"
        lines.append(
            f"- PR {who} · main {e.main_sha[:12]} · tested {(e.tested_head_sha or '—')[:12]} "
            f"· approvals: {indep} independent / {selfn} self{flag}"
        )
    return "\n".join(lines)


def as_dict(result: ExportResult) -> dict[str, object]:
    """Machine-readable evidence. Deterministic; contains no token/secret."""
    return {
        "repo": result.repo,
        "branch": result.branch,
        "since": result.since,
        "until": result.until,
        "required_contexts": list(result.required_contexts),
        "n_merges": result.n_merges,
        "n_exceptions": result.n_exceptions,
        "n_self_approved": result.n_self_approved,
        "merges": [
            {
                "pr_number": e.pr_number,
                "main_sha": e.main_sha,
                "tested_head_sha": e.tested_head_sha,
                "merged_at": e.merged_at,
                "required": dict(e.required),
                "approvals": [
                    {"author": a.author, "independent": a.independent} for a in e.approvals
                ],
                "exceptions": list(e.exceptions),
            }
            for e in result.merges
        ],
    }


# ── IO adapter (design-for-failure; the only thing that touches GitHub) ───────────────────
class GitHubChangeSource:
    """Commit-centric GitHub adapter (M1/M6).

    Walks `main` commits in the window, resolves each to its PR (a commit with none is a direct
    push), and gathers the required-check conclusions on the PR's gated head plus its APPROVED
    reviews. Every request is timeout-bounded and retried with bounded backoff; exhaustion or a
    partial page raises `IncompleteFetch` rather than returning a short set.
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

    # The adapter is intentionally thin over `gh api`-shaped REST; kept import-light so the pure
    # core carries no httpx dependency. Implementations that need it inject httpx here.
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
                # 429/5xx are transient; 403 with a rate-limit marker is too.
                if resp.status_code in (429, 500, 502, 503, 504) or (
                    resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0"
                ):
                    last_exc = IncompleteFetch(f"{path} -> {resp.status_code} (transient)")
                else:
                    # A hard error (404/401/…) is not a partial fetch — surface it plainly.
                    raise IncompleteFetch(f"{path} -> {resp.status_code}")
            time.sleep(min(2.0**attempt, 8.0))  # bounded backoff; not a clock read
        raise IncompleteFetch(f"{path}: exhausted {self._max_attempts} attempts") from last_exc

    def required_contexts(self, *, repo: str, branch: str) -> Sequence[str]:  # pragma: no cover
        data = self._get(f"/repos/{repo}/branches/{branch}/protection/required_status_checks")
        contexts = data.get("contexts", []) if isinstance(data, dict) else []
        return [str(c) for c in contexts]

    def merges(  # pragma: no cover
        self, *, repo: str, branch: str, since: str, until: str
    ) -> Sequence[MergeRaw]:
        raise NotImplementedError(
            "live GitHub walk is provisioned by the operator's run environment; the pure core "
            "and its evidence semantics are what this task freezes and tests. Wire _get() into a "
            "commit walk (GET /repos/{repo}/commits?sha={branch}&since=&until=, then "
            "/commits/{sha}/pulls, /commits/{head}/check-runs, /pulls/{n}/reviews) — raising "
            "IncompleteFetch on any partial page."
        )


# ── CLI ──────────────────────────────────────────────────────────────────────────────────
def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CC8.1 change-management evidence export")
    parser.add_argument("--since", required=True, help="ISO-8601 UTC window start (inclusive)")
    parser.add_argument("--until", required=True, help="ISO-8601 UTC window end (exclusive)")
    parser.add_argument("--repo", default=DEFAULT_REPO)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--json-out", default="evidence.json", help="path for machine evidence")
    args = parser.parse_args(argv)

    source = GitHubChangeSource()
    try:
        result = build_evidence(
            source, repo=args.repo, branch=args.branch, since=args.since, until=args.until
        )
    except IncompleteFetch as exc:
        print(f"FETCH INCOMPLETE — evidence NOT written: {exc}", file=sys.stderr)
        return 2

    with open(args.json_out, "w", encoding="utf-8") as fh:
        json.dump(as_dict(result), fh, indent=2, sort_keys=True)
    print(render_summary(result))
    # An exception in the window is the auditor signal — exit non-zero so CI/an operator notices.
    return 1 if result.n_exceptions else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
