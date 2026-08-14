---
type: Task
title: Change-management evidence export (CC8.1)
status: done
depth: standard
milestone: soc2-groundwork
gives:
  - S1 a pure evidence-builder + a `ChangeSource` port (GitHub adapter is the only IO) that, over a date window, maps every merge to `main` to {PR, the PR's required-check conclusions at merge, approver(s)} and the resulting `main` commit — FLAGGING any merge missing a green required check or an approval, and recording a self-approval as NOT independent — plus a thin CLI that emits the report (JSON + human summary)
generated: { by: add/3.2.0, at: 2026-08-14 }
verified:
  - { by: "cli", at: 2026-08-14, act: freeze, authority: process, direction: "sha256:6dad0c2741b7fa78" }
  - { by: "cli", at: 2026-08-14, act: brief, authority: process, brief: "sha256:6f407825726d4564" }
  - { by: "process:run", at: 2026-08-14, act: run, authority: process, outcome: PASS, receipt: /tasks/change-management-evidence-export.d/runs/1.md }
  - { by: "process:verify", at: 2026-08-14, act: gate, authority: process, outcome: PASS, receipt: /tasks/change-management-evidence-export.d/runs/1.md, brief: "sha256:6f407825726d4564", reason: "Self-driving verify per the frozen contract (sha256:6dad0c27). 9 tests green binding M1-M6, R:UNTESTED_MERGE/SILENT_OMISSION/LAUNDERED_APPROVAL/TRUNCATED_AS_COMPLETE, A1-A6, E1-E7: clean green+reviewed merge; missing required context flagged (never dropped); self-approval recorded independent=false and counted separately (never laundered); direct-push exception; IncompleteFetch propagates (partial never emitted as complete); half-open window; pure+deterministic build (byte-identical, zero-network fake, no gateway/DB import); squash records BOTH shas and invents no run on the squashed sha; payload/secret-free summary. ruff check+format clean; scripts/soc2 is outside the gateway pyright scope by design (not shipped in the wheel). make ci full-suite regression floor to be confirmed on the PR (no gateway runtime touched). Human four-eyes owed at the PR gate." }
advised_by: appsec-engineer
---
## CARD
goal: produce auditable SOC 2 CC8.1 change-management evidence — for every merge to `main` in a window, the required checks that gated it + the approver(s), with any control exception FLAGGED not hidden and a self-approval never laundered into independent review
why: CC8.1 wants evidence that changes went through the change-management controls (tests + review) on the artifact that landed; today that is a manual reconstruction, and the byte-approval reality (#117/#118/#199–#207) must be recorded honestly. This is the tooling `readiness-assessment` cites and the honest baseline `independent-review-control` measures against.
beat: done · next: add status

## RULES
<must>
- M1 Evidence for a merge is the required-check conclusions GitHub recorded for the merged PR, PLUS the resulting `main` commit sha AND the head sha the checks evaluated. A required context counts as green ONLY if GitHub reports it `success` for that PR's evaluated head at merge. This repo SQUASH-merges, so the `main` commit is a NEW sha with no check runs of its own — the honest CC8.1 evidence is the PR's required-check conclusions linked to the commit they gated, NEVER a fabricated or assumed run on the squashed sha. The report records BOTH shas so a reader sees they differ under squash.
- M2 The evidence-builder is a PURE total function over data supplied by a `ChangeSource` port (a `typing.Protocol` with a zero-network fake usable from a test). No network in the core; identical `ChangeSource` output ALWAYS yields identical evidence. The GitHub adapter is the ONLY IO and is injected — no use-case reaches httpx/`gh` directly.
- M3 A merge that lacks EITHER (a) a green required-check conclusion for its PR, OR (b) at least one approving review, is emitted as a FLAGGED EXCEPTION carrying the reason — never omitted, never silently counted compliant. A commit on `main` with NO associated PR (a direct push — a protection bypass) is itself a flagged exception, not skipped. (Absence of evidence is not evidence of a control — [[masked-gate-never-reached-a-verdict]].)
- M4 An approval whose author is a disclosed self-approval account (the config'd set, default `pilotspacex-byte`) is recorded `independent: false, self_approved: true`; the export summary counts independent vs self-approved SEPARATELY and never presents a self-approval as independent review. (Honest record over a tidy one; the byte tally is the auditor sample set.)
- M5 The required-context set is DERIVED from the repo's live branch-protection required contexts (today `ci`,`dashboard`), never hard-coded to a stale list; "checks-green" requires EVERY required context `success`. A required context that is missing or non-success on the PR is an exception (M3). (A shard rename or a newly-added required context must not silently pass — the same trap as the masked ci contexts.)
- M6 Design-for-failure at the IO boundary (CLAUDE.md): the GitHub adapter bounds every call with a timeout, paginates to completion, retries transient failures (5xx / rate-limit) with bounded backoff, and surfaces exhaustion as a LOUD error. A truncated or rate-limited fetch FAILS the export — it is never emitted as a short-but-complete evidence set (the negative-poll / "no rows == settled" bug). The core is told "this window is complete" only by a source that fetched every page.
</must>
<reject>
- R:UNTESTED_MERGE a merge is counted green when GitHub has no `success` conclusion for its PR's required contexts (incl. an invented run on the squashed sha) -> "a merge counts green only from a real success conclusion on the PR GitHub gated"
- R:SILENT_OMISSION a merge (or a direct push) with no green required check or no approval is dropped from the report instead of flagged -> "every landing appears; an exception is flagged, never omitted"
- R:LAUNDERED_APPROVAL a self-approval (byte account) is presented as, or counted among, independent reviews -> "R:LAUNDERED_APPROVAL"
- R:TRUNCATED_AS_COMPLETE a truncated / rate-limited / partial fetch is emitted as a complete evidence set -> "a partial fetch FAILS the export; it never reports as complete"
</reject>

## ASSUMPTIONS
- A1 [who] covers: S1 · the request does not say whose changes or which repo; taking "the SUBJECT is Hydroa's OWN repo `pilotspace/hydroa` `main` — org-internal change-management evidence, NOT any tenant's data — run with a READ-ONLY GitHub token, touching NO gateway runtime and NO tenant store" -> if wrong it couples to the gateway app or spans repos it must not read. · probe: the tool takes repo+branch (defaults pilotspace/hydroa · main) and the core module imports nothing from `gateway.*` runtime and opens no DB.
- A2 [which] covers: S1 · the request does not say which commits are "changes"; taking "the unit is a landing on `main` in the window — a squash/merge commit that closed a PR; a `main` commit with no associated PR is a direct-push EXCEPTION (M3), never skipped" -> if wrong a direct push (a bypass) becomes invisible, the exact thing CC8.1 must catch. · probe: a `main` commit with no PR is emitted as a flagged direct-push exception.
- A3 [when] covers: S1 · the request does not say the window boundary; taking "a half-open `[since, until)` by the PR's merged-at timestamp, both explicit ISO-8601 UTC — a merge exactly at `until` is EXCLUDED, at `since` INCLUDED; no implicit 'last N days'" -> if wrong a merge double-counts across adjacent windows or falls between them. · probe: a merge at the `until` instant is excluded and at `since` included, asserted on fixture data.
- A4 [absent] covers: S1 · the request does not say what a missing approval or check MEANS; taking "absent = NOT compliant — a merge with no approval record, or no `success` required-check conclusion, is an EXCEPTION (M3), never assumed fine; a null is a red flag, not a pass" -> if wrong, absence launders into compliance (the masked-gate failure mode). · probe: a merge whose PR has no required-check success is flagged, never counted green.
- A5 [order] covers: S1 · the request does not say the report order / tie-break; taking "merges listed newest-first by merged-at, tie-broken by `main` sha — fully deterministic; summary counts are order-independent" -> if wrong two runs over one window disagree in order and the evidence reads as unstable. · probe: two builds over identical `ChangeSource` output are byte-identical, ordering included.
- A6 [experience] covers: S1 · the request does not say who reads it; taking "an AUDITOR (or Tin preparing for one) reads it — the artifact is stable + plain (machine JSON + a human summary), per merge: PR#, tested-head sha, resulting `main` sha, each required context + its conclusion, approver(s) with independent-vs-self-approved, and a top line 'N merges · M exceptions · K self-approved'; payload-free, NO token/secret ever in output" -> if wrong the evidence is an unreadable API dump an auditor can't sample. · probe: the summary states merges/exceptions/self-approved counts and no token or secret appears anywhere in the output.

## PLAN
contract:
```
# NEW self-contained tool `scripts/soc2/change_evidence.py` (+ package __init__) — pure core +
# injected GitHub adapter + thin CLI. NOT shipped in the gateway wheel; imports nothing from gateway.*.

@dataclass(frozen=True)
class ReviewRef:
    author: str            # GitHub login of an APPROVED review
    independent: bool      # False when author in the self-approval set (M4)

@dataclass(frozen=True)
class MergeEvidence:
    pr_number: int | None          # None => direct push (M3/A2 exception)
    main_sha: str                  # the commit that landed on main (squash result)
    tested_head_sha: str | None    # the head GitHub gated (differs from main_sha under squash, M1)
    merged_at: str                 # ISO-8601 UTC
    required: Mapping[str, str]    # {context: conclusion} for each required context (M5)
    approvals: tuple[ReviewRef, ...]
    exceptions: tuple[str, ...]    # e.g. ("no green ci on PR", "no approval", "direct push") — M3

@dataclass(frozen=True)
class ExportResult:
    since: str; until: str; repo: str; branch: str
    required_contexts: tuple[str, ...]         # DERIVED from branch protection (M5)
    merges: tuple[MergeEvidence, ...]          # newest-first, deterministic (A5)
    n_merges: int; n_exceptions: int; n_self_approved: int   # summary (A6)

class ChangeSource(Protocol):                  # the port — the GitHub adapter is the only impl
    def merges(self, *, repo: str, branch: str, since: str, until: str) -> Sequence[MergeRaw]: ...
    def required_contexts(self, *, repo: str, branch: str) -> Sequence[str]: ...
    # MUST raise IncompleteFetch rather than return a truncated page (M6, R:TRUNCATED_AS_COMPLETE).

def build_evidence(source: ChangeSource, *, repo, branch, since, until,
                   self_approval_logins: frozenset[str]) -> ExportResult:
    """PURE over the source's output (M2): classify each landing, flag exceptions (M3),
    mark self-approvals (M4), require every required context success (M5). Never raises on
    a well-formed source; an IncompleteFetch from the source propagates (M6)."""

# GitHub adapter: httpx with per-call timeout + bounded retry/backoff + full pagination; a
# rate-limit / partial page => raise IncompleteFetch (M6). Token from env, READ-ONLY, never logged.
# CLI: `python -m scripts.soc2.change_evidence --since ... --until ... [--repo ... --branch main]`
#   -> writes evidence.json + prints the human summary; exit non-zero if any exception (auditor signal).
```
scope (may touch): `scripts/soc2/__init__.py` · `scripts/soc2/change_evidence.py` (NEW: dataclasses · `ChangeSource` Protocol + `IncompleteFetch` · `build_evidence` pure core · `GitHubChangeSource` adapter · `_render_summary` · `main()` CLI) · `apps/gateway/tests/soc2_change_evidence/test_change_evidence.py` (NEW). NO gateway runtime change, NO migration, NO router, NO wheel change.
regression floor: `make ci` stays green; the core imports nothing from `gateway.*`/`sqlalchemy`/`fastapi` (grep-verified); the GitHub adapter is exercised ONLY through the port fake in tests (no live network in CI).
resolved (was least-sure): HOME = `scripts/soc2/` (repo-governance tooling, sibling to `check_allowlist.py`), NOT the gateway package — it must not ship in the runtime wheel; tests live in the gateway suite so `make ci` runs them, importing the module by path (mirrors how `tests/repo_hygiene` tests `scripts/`). The squash-merge/new-sha reality (M1) is the sharp design point — recorded, not papered over.

## EDGES
- E1 a merge whose PR has a green `ci` but a MISSING or failing `dashboard` context -> flagged exception (M5), never counted green.
- E2 a merge approved ONLY by the byte account -> `independent: false, self_approved: true`, counted separately, never among independent reviews (M4, R:LAUNDERED_APPROVAL).
- E3 a `main` commit with no associated PR (direct push) -> a flagged direct-push exception (M3/A2), never skipped.
- E4 the source signals a rate-limited / truncated fetch (raises `IncompleteFetch`) -> `build_evidence` propagates and the CLI exits non-zero; NO partial evidence set is emitted as complete (M6, R:TRUNCATED_AS_COMPLETE).
- E5 two builds over identical `ChangeSource` output -> byte-identical `ExportResult`, ordering included (A5).
- E6 a squash merge where `tested_head_sha != main_sha` -> both shas recorded; the required-check conclusions are credited from the PR's gated head, and nothing invents a run on `main_sha` (M1, R:UNTESTED_MERGE).
- E7 a merge with a green check AND a genuine independent approval -> NOT an exception; contributes to `n_merges` with zero exceptions and (if independent) to the independent count.

## CHECKS
- test_green_checked_and_reviewed_merge_is_clean · covers: M1, A6, E7 · a fixture merge with every required context `success` on the PR + one independent approval yields a MergeEvidence with no exceptions, both shas recorded, and shows in the summary as a clean merge.
- test_missing_required_context_is_flagged · covers: M5, E1, R:SILENT_OMISSION · a merge green on `ci` but missing `dashboard` is emitted WITH the merge, carrying a "missing required context: dashboard" exception, never dropped and never green.
- test_self_approval_not_counted_independent · covers: M4, E2, R:LAUNDERED_APPROVAL · a merge approved only by the byte account is `self_approved:true, independent:false` and the summary's independent count excludes it.
- test_direct_push_is_an_exception · covers: M3, A2, E3 · a `main` commit with pr_number=None is emitted as a direct-push exception, not skipped.
- test_incomplete_fetch_fails_not_truncates · covers: M6, E4, R:TRUNCATED_AS_COMPLETE · a source that raises IncompleteFetch makes build_evidence raise (and the CLI exit non-zero); no partial ExportResult is produced.
- test_window_is_half_open · covers: A3 · a merge at `until` is excluded and one at `since` included, over fixture data.
- test_build_is_pure_and_deterministic · covers: M2, A1, A5, E5 · build_evidence runs from a SYNC test with a zero-network fake source (no gateway/DB/network fixture) and two runs over identical input are byte-identical, ordering included.
- test_squash_records_both_shas_no_invented_run · covers: M1, E6, R:UNTESTED_MERGE · a fixture where tested_head_sha != main_sha records both and credits checks only from the gated head; asserting no green is attributed to main_sha absent a real conclusion.
- test_summary_is_payload_free_and_counted · covers: A6, A4 · the rendered summary states N merges / M exceptions / K self-approved, flags an absent approval as an exception (not a pass), and contains no token/secret substring.
red-first: every check MUST fail first — `scripts/soc2/change_evidence.py` does not exist yet; the fake `ChangeSource` and fixtures are authored in the test and are red against the absent module.

## EVIDENCE
receipt: <runs/<n>.md>
gate: <PASS | RISK-ACCEPTED | HARD-STOP>

## LESSONS
- <lesson> -> add learn <lens>
