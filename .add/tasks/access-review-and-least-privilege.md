---
type: Task
title: Access review + least-privilege (CC6)
status: done
depth: standard
milestone: soc2-groundwork
gives:
  - S1 a pure access-register builder + an `AccessSource` port (a READ-ONLY GitHub/registry adapter is the only IO) that enumerates every write/admin principal on the governed surfaces, classifies each grant against a DECLARED least-privilege policy (justified · over_privileged · stale), records the `pilotspacex-byte` account's true nature, and emits a dated review register — and NEVER mutates a grant — plus a thin CLI
generated: { by: add/3.2.0, at: 2026-08-14 }
verified:
  - { by: "cli", at: 2026-08-14, act: freeze, authority: process, direction: "sha256:4d6d6c36e8ea159e" }
  - { by: "cli", at: 2026-08-14, act: brief, authority: process, brief: "sha256:0e4667e833b113c1" }
  - { by: "process:run", at: 2026-08-14, act: run, authority: process, outcome: PASS, receipt: /tasks/access-review-and-least-privilege.d/runs/1.md }
  - { by: "process:verify", at: 2026-08-14, act: gate, authority: process, outcome: PASS, receipt: /tasks/access-review-and-least-privilege.d/runs/1.md, brief: "sha256:0e4667e833b113c1", reason: "10/10 red-first CHECKS green; every referent (M1-M6, A1-A6, E1-E7, R:TOOL_MUTATES_ACCESS/SILENT_JUSTIFY/CURRENT_STATE_AS_POLICY/FABRICATED_SIGNOFF) bound to a passing test; ruff clean; pure core over AccessSource port, IncompleteFetch design-for-failure, read-only tool, fail-closed on unlisted principal, unreviewed-draft signoff never fabricated." }
advised_by: appsec-engineer
---
## CARD
goal: produce SOC 2 CC6 access-review evidence — enumerate every principal with governed write/admin access, classify each grant against a DECLARED least-privilege policy with a justification, flag over-privilege/stale, and emit a dated register — WITHOUT mutating any access (removals stay a human decision)
why: CC6.1–6.3 want evidence that access is least-privilege and reviewed on a cadence; today access is unenumerated, and the `pilotspacex-byte` shared-keyring reality (the self-approval second account) must be recorded honestly. This is the register `readiness-assessment` cites and the access half of the honest baseline `independent-review-control` needs.
beat: done · next: add status

## RULES
<must>
- M1 The tool is READ + REPORT ONLY — it NEVER mutates access. It enumerates and classifies; a removal or role change is a human decision the register RECOMMENDS, never an action the tool takes. There is no code path that revokes, adds, or edits a grant. (An access-review tool that can change access is itself a privilege risk — the reviewer must not be able to act.)
- M2 The register builder is a PURE total function over data supplied by an `AccessSource` port (a `typing.Protocol` with a zero-network fake). No network in the core; identical source output ALWAYS yields an identical register. The READ-ONLY GitHub/registry adapter is the only IO — design-for-failure: timeout-bounded, paginated to completion, retried with bounded backoff; a truncated/rate-limited fetch raises `IncompleteFetch` and NEVER emits a partial register as complete.
- M3 Every principal with write OR admin on a governed surface appears in the register with its permission level and a classification — `justified` (matches the declared policy), `over_privileged` (more than the policy grants, OR unlisted), or `stale` (no activity in the review window). No principal is omitted; an UNLISTED principal is `over_privileged` by default (fail-closed), never silently justified.
- M4 A grant is `justified` ONLY against the DECLARED least-privilege policy — a checked-in allow-list of principal→max-role + written justification — never against the current live state. "Current access is the policy" is circular and launders over-privilege into justified; the policy is the independent reference the live state is measured against.
- M5 The `pilotspacex-byte` account is recorded with its TRUE nature: a disclosed operator second-account used for self-approval, NOT an independent human, cross-linked to the byte-approval tally (#117–#208). Its disposition (keep-as-documented-gap vs retire) is a RECORDED, Tin-owned decision field — never auto-resolved by the tool.
- M6 The register carries a dated review record — reviewer, date, window. An auto-generated draft's reviewer field is honestly `"unreviewed — draft"` until a human signs it; the tool NEVER fills a human's name (a review with a fabricated reviewer is worse than an unreviewed one). A review with no date/reviewer is not a review — CC6 wants evidence a review HAPPENED.
</must>
<reject>
- R:TOOL_MUTATES_ACCESS the tool exposes or calls any path that revokes/adds/edits a grant -> "read+report only; remediation is a human decision"
- R:CURRENT_STATE_AS_POLICY a grant is classified justified by matching the live state instead of the declared policy -> "R:CURRENT_STATE_AS_POLICY"
- R:SILENT_JUSTIFY an unlisted or unknown principal is classified justified instead of over_privileged (fail-closed) -> "an unlisted principal fails closed to over_privileged"
- R:FABRICATED_SIGNOFF the review record names a reviewer who did not review (incl. auto-filling Tin) -> "R:FABRICATED_SIGNOFF"
</reject>

## ASSUMPTIONS
- A1 [who] covers: S1 · the request does not say whose access or which surfaces; taking "the SUBJECT is the `pilotspace` org, the `hydroa` repo, and its ghcr packages — run with a READ-ONLY token, touching NO gateway runtime and NO tenant store; the tool reports on the ORG's own access, not any tenant's" -> if wrong it reads the wrong surface or needs a write token it must never hold. · probe: the tool takes the org/repo/registry surfaces and the core imports nothing from `gateway.*`; the adapter carries no write scope.
- A2 [which] covers: S1 · the request does not say which grants count; taking "WRITE or ADMIN on a governed surface (org owners/members with write+, repo collaborators write+, ghcr package admins); pure READ grants are lower-risk and are listed-but-not-classified, not treated as privileged" -> if wrong a read-only viewer is flagged as over-privileged noise, or an admin is missed. · probe: a write/admin principal is classified; a read-only one is listed without a privilege classification.
- A3 [when] covers: S1 · the request does not say what makes a grant stale; taking "a grant is `stale` when the principal has NO activity (merge/review/commit) in the review window — policy-set, default 90 days; the window boundary is explicit, not 'recently'" -> if wrong an inactive admin looks justified, the exact CC6 finding. · probe: a principal with no in-window activity is classified stale on fixture data.
- A4 [absent] covers: S1 · the request does not say what a missing policy entry or missing activity MEANS; taking "absent policy entry = over_privileged (fail-closed, M3); absent activity = stale, never justified — a null is a flag, not a pass" -> if wrong, absence launders into justified. · probe: an unlisted principal is over_privileged; a no-activity principal is stale.
- A5 [order] covers: S1 · the request does not say the register order; taking "ordered by classification severity (over_privileged, then stale, then justified) then login — deterministic; counts are order-independent" -> if wrong two runs disagree and the evidence reads unstable. · probe: two builds over identical source output are byte-identical, ordering included.
- A6 [experience] covers: S1 · the request does not say who reads it; taking "an AUDITOR (or Tin) reads it — per principal {login, surface, permission, classification, justification-or-reason}, a top-line 'N principals · X over-privileged · Y stale', the byte-account note, and the dated review record; payload/secret-free, NO token ever in output" -> if wrong the register is an unreadable API dump. · probe: the summary states the counts + the review record and contains no token/secret.

## PLAN
contract:
```
# NEW self-contained tool `scripts/soc2/access_review.py` + a DECLARED policy
# `scripts/soc2/access_policy.json`. Pure core + injected READ-ONLY adapter + CLI. No gateway.* import.

Classification = Literal["justified", "over_privileged", "stale"]

@dataclass(frozen=True)
class AccessGrant:                 # one principal's grant on one governed surface (from the source)
    login: str
    surface: str                   # "org:pilotspace" | "repo:hydroa" | "ghcr:<pkg>"
    permission: str                # "admin" | "write" | "read"
    last_activity: str | None      # ISO-8601 UTC of most recent merge/review/commit, or None

@dataclass(frozen=True)
class PolicyEntry:
    login: str
    max_permission: str            # the MOST this principal may hold (declared, M4)
    justification: str
    self_approval_account: bool = False   # pilotspacex-byte etc. (M5)

@dataclass(frozen=True)
class ReviewedGrant:
    grant: AccessGrant
    classification: Classification
    reason: str                    # justification (justified) or why-flagged (over/stale)
    self_approval_account: bool

@dataclass(frozen=True)
class ReviewRecord:
    reviewer: str                  # "unreviewed — draft" until a human signs (M6, R:FABRICATED_SIGNOFF)
    window_days: int
    generated_window: str          # "[since, until)" the staleness was computed over

@dataclass(frozen=True)
class AccessRegister:
    grants: tuple[ReviewedGrant, ...]        # severity-ordered, deterministic (A5)
    review: ReviewRecord
    n_principals: int; n_over_privileged: int; n_stale: int

class AccessSource(Protocol):      # READ-ONLY; NO mutate method exists (M1, R:TOOL_MUTATES_ACCESS)
    def grants(self, *, org: str, repo: str) -> Sequence[AccessGrant]: ...
    # raises IncompleteFetch rather than a truncated page (M2).

def build_register(source, policy, *, window_days=90, now_iso) -> AccessRegister:
    """PURE (M2): classify each write/admin grant against `policy` (M4); unlisted => over_privileged
    (M3/A4 fail-closed); no in-window activity => stale (A3). `now_iso` is INJECTED, never read from a
    clock, so the build stays pure + reproducible. Never mutates; there is no revoke path (M1)."""

# READ-ONLY adapter GitHubAccessSource: GET org members + repo collaborators + ghcr package roles,
# read scope only; timeout + bounded retry + full pagination; IncompleteFetch on any partial (M2).
# CLI: `python -m scripts.soc2.access_review --window-days 90` -> writes access_register.json +
#   prints the summary; exit non-zero if any over_privileged or stale (the review signal).
```
scope (may touch): `scripts/soc2/access_review.py` (NEW: dataclasses · `AccessSource` Protocol + `IncompleteFetch` · `build_register` pure core · `GitHubAccessSource` READ-ONLY adapter · `_render_summary` · `main()` CLI) · `scripts/soc2/access_policy.json` (NEW: the DECLARED least-privilege allow-list — known principals with justifications + the byte-account flag, marked draft-pending-Tin) · `apps/gateway/tests/soc2_access_review/test_access_review.py` (NEW). NO gateway runtime change, NO migration, NO wheel change. Reuses the `scripts/soc2/` home + the IncompleteFetch design-for-failure idiom established by [[change-management-evidence-export]].
regression floor: `make ci` stays green; the core imports nothing from `gateway.*`/`sqlalchemy`; the adapter is exercised ONLY through the port fake in tests (no live network in CI); `now_iso` is injected so no clock read (pure).
resolved (was least-sure): `now_iso` is an INJECTED parameter (not `datetime.now()`) so the pure core stays reproducible and testable — the CLI supplies the wall-clock at the edge. The declared policy is a checked-in JSON allow-list authored with the KNOWN principals and marked `reviewer:"unreviewed — draft"` (M6) — Tin confirms the real roster at review time; the tool never signs for him.

## EDGES
- E1 an UNLISTED principal with write/admin -> `over_privileged` (fail-closed, M3/A4, R:SILENT_JUSTIFY), never justified.
- E2 a principal listed with `max_permission: write` but holding `admin` live -> `over_privileged` (M4, R:CURRENT_STATE_AS_POLICY), justified only up to the declared max.
- E3 `pilotspacex-byte` -> recorded `self_approval_account: true`, its reason names the disclosed second-account nature; disposition is a Tin-owned field, not auto-resolved (M5).
- E4 the source raises `IncompleteFetch` -> `build_register` propagates and the CLI exits non-zero; NO partial register emitted (M2, design-for-failure).
- E5 two builds over identical source output + same `now_iso` -> byte-identical `AccessRegister`, ordering included (A5).
- E6 a listed, in-policy principal with NO activity in the window -> `stale` (A3/A4), never justified.
- E7 the auto-generated review record -> `reviewer == "unreviewed — draft"`, never a human name (M6, R:FABRICATED_SIGNOFF).

## CHECKS
- test_every_write_admin_principal_is_classified · covers: M3, A2 · a fixture of org/repo/ghcr grants yields one ReviewedGrant per write/admin principal with a classification; a read-only viewer is listed but not privilege-classified.
- test_tool_has_no_mutate_path · covers: M1, R:TOOL_MUTATES_ACCESS · the AccessSource Protocol and the module expose NO revoke/add/edit-grant callable (asserted by inspecting the public surface); the register is build-only.
- test_unlisted_principal_fails_closed · covers: M3, A4, E1, R:SILENT_JUSTIFY · a principal absent from the policy is `over_privileged`, never justified.
- test_justified_only_against_declared_policy · covers: M4, E2, R:CURRENT_STATE_AS_POLICY · a principal holding more than its declared `max_permission` is `over_privileged`; justified requires a policy match, not a live-state match.
- test_byte_account_recorded_non_independent · covers: M5, E3 · `pilotspacex-byte` is `self_approval_account: true` with a reason naming its disclosed nature; the disposition field is present and not auto-resolved.
- test_review_record_unreviewed_until_signed · covers: M6, E7, R:FABRICATED_SIGNOFF · an auto-generated register's `review.reviewer == "unreviewed — draft"`; no code path fills a human name.
- test_stale_grant_flagged_by_window · covers: A3, E6 · a listed in-policy principal with no activity within `window_days` is `stale`, not justified.
- test_incomplete_fetch_fails_not_truncates · covers: M2, E4 · a source that raises IncompleteFetch makes build_register raise; no partial AccessRegister is produced.
- test_build_is_pure_deterministic_and_ordered · covers: M2, A1, A5, E5 · build_register runs from a SYNC test with a zero-network fake and injected `now_iso`; two runs are byte-identical, severity-ordered (over_privileged, stale, justified) then login.
- test_summary_is_payload_free_and_counted · covers: A6 · the summary states N principals / X over-privileged / Y stale + the dated review record and contains no token/secret substring.
red-first: every check MUST fail first — `scripts/soc2/access_review.py` does not exist yet; the fake `AccessSource`, the fixtures, and a test policy are authored in the test and are red against the absent module.

## EVIDENCE
receipt: <runs/<n>.md>
gate: <PASS | RISK-ACCEPTED | HARD-STOP>

## LESSONS
- <lesson> -> add learn <lens>
