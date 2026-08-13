---
type: Task
title: eval-run-executor
status: done
milestone: evals-regression-gate
needs:
  - eval-set-store.md
gives:
  - S1 the run-execution port — tenant-keyed breaker, bounded concurrency, timeouts, one usage_record per case through the governance path
generated: { by: add/3.2.0, at: 2026-08-12 }
verified:
  - { by: "Tin Dang", at: 2026-08-13, act: freeze, authority: process, direction: "sha256:1353206db5fb398c" }
  - { by: "cli", at: 2026-08-13, act: brief, authority: process, brief: "sha256:1181f2f2c2711ebd" }
  - { by: "process:run", at: 2026-08-13, act: run, authority: process, outcome: PASS, receipt: /tasks/eval-run-executor.d/runs/1.md }
  - { by: "process:run", at: 2026-08-13, act: run, authority: process, outcome: PASS, receipt: /tasks/eval-run-executor.d/runs/2.md }
  - { by: "Tin Dang", at: 2026-08-13, act: refreeze, authority: process, direction: "sha256:2abaa8687a94642b" }
  - { by: "cli", at: 2026-08-13, act: brief, authority: process, brief: "sha256:c478d7392f02d99d" }
  - { by: "process:run", at: 2026-08-13, act: run, authority: process, outcome: PASS, receipt: /tasks/eval-run-executor.d/runs/3.md }
  - { by: "add-worker", at: 2026-08-13, act: gate, authority: process, outcome: PASS, receipt: /tasks/eval-run-executor.d/runs/3.md, brief: "sha256:c478d7392f02d99d", reason: "13/13 CHECKS bound green (M1-M8, A1-A6, E1-E6, R:*); migration-parity + guardrails manifests + pyright + ruff all green; per-tenant breaker isolation, ZDR-atomic launch/mid-run refusal, auth-scoped resume (no key material at rest), bounded per-tenant concurrency all verified" }
advised_by: appsec-engineer
---
## CARD
goal: run a set against a model through the governance path — per-tenant breaker, bounded concurrency, timeouts, partial-run resumability
why: a run is a burst of billed upstream calls; it must enter through the budget/credit/tier guards and never trip a shared breaker that degrades another tenant
beat: done · next: add status

## RULES
<must>
- M1 A run replays each case's stored `request_body` against the run's named model THROUGH the existing governance path (`NonChatGovernance.authorize` / the completion use-case), never around it — every ordered guard (auth · expiry · allowlist · catalog/model_checker tri-state · per-key/team/tenant budget · credit · tier · rate-limit) runs per case exactly as for a live request. A run enters no back door.
- M2 A run produces EXACTLY ONE `usage_records` row per case that actually dials upstream, with the SAME shape a live request of that `request_body` would produce (billed on the SERVED model id, not `request_body["model"]`) — no unmetered case, no double-bill, no eval side-channel. A case refused before the dial (M3) writes NO usage row.
- M3 A case whose per-case governance check fails (budget/credit/tier/rate/allowlist/disabled model) is REFUSED with NO upstream call and NO usage row for that case; the run records the case as `refused` with the governance reason. A tenant at their credit/budget limit cannot spend through a run. (Assert NO provider call was made.)
- M4 The per-case upstream dial goes through a breaker + concurrency limiter keyed by `tenant_id` — the [[per-tenant-breaker-recurring-defect]] anchor: NEVER a process-global breaker/semaphore. Tenant A's failing burst opens A's breaker only; tenant B's request in the same process is unaffected. Every outbound dial has a per-call TIMEOUT; breaker-open and timeout both fail the case CLOSED (recorded `errored`), never hang the run.
- M5 A run persists per-case RESULTS including the model's `response_text` (the payload the console diff + re-scoring read). This is a payload-at-rest surface, so ZDR is enforced: a ZDR tenant's run is REFUSED outright at launch with 403 `ERR_ZDR_PAYLOAD_BLOCKED`, and the check is ATOMIC with the first result write (`raise_if_zdr_locked`, SELECT … FOR UPDATE) — a flip landing mid-run persists NOTHING further. (Same disposition as [[eval-set-store]] M3; assertion-only/redacted-run mode is a later-milestone follow-up, not R7.)
- M6 Reads/writes are tenant-scoped in the SAME query that resolves the row: a run/set/case owned by another tenant is a uniform 404 `ERR_EVAL_SET_NOT_FOUND` / `ERR_EVAL_RUN_NOT_FOUND`, never a distinguishable error (carried isolation invariant, #84). A run grants NO model visibility a normal request lacks — the candidate model resolves under `tenant_id IS NULL OR = :tenant`, including finetuned models.
- M7 A run is DURABLE and resumable: each case result is committed as it completes, and a run interrupted (crash/redeploy) mid-flight can be resumed to drive only the cases still `pending` — no case is dialed (and billed) twice on resume (a completed/errored/refused case is terminal; re-drive is a no-op). The run's terminal status is derived from its cases, not written speculatively.
- M8 The execution engine is reached through a `typing.Protocol` port with a zero-network fake usable from `app.state` (a fake upstream that returns canned responses / raises / times out on command) — the use-case never imports the concrete provider client; the breaker/governance/timeout behavior is provable without a network (backend-architect lens).
</must>
<reject>
- R:GOVERNANCE_BYPASS a case dials upstream without passing the same governance guards a live request would -> "a run must enter through governance, never around it"
- R:ZDR_BLOCKED a ZDR tenant's run persists a response payload at rest -> "ERR_ZDR_PAYLOAD_BLOCKED"
- R:GLOBAL_BREAKER one tenant's run opens a breaker/exhausts a limiter that degrades another tenant -> "the breaker + concurrency limiter are per-tenant, never global"
- R:DOUBLE_BILL a case produces zero or more-than-one usage_record, or a resumed run re-dials a terminal case -> "exactly one usage_record per dialed case; resume never re-bills"
- R:RUN_NOT_FOUND a run/set/case owned by another tenant is distinguishable from an absent one -> "ERR_EVAL_RUN_NOT_FOUND"
</reject>

## ASSUMPTIONS
- A1 [who] covers: S1 · the request does not say who may launch a run or whose key it bills; taking "any authenticated member of the owning tenant launches a run on THAT tenant's set, billed to the launching key exactly as that key's live traffic; superadmin only via impersonation, never cross-tenant" -> if wrong, a run bills the wrong key or leaks a cross-tenant set. · probe: tenant B launching a run on tenant A's set gets 404; a run's usage_records carry the launching tenant/key.
- A2 [which] covers: S1 · the request does not say which cases a run covers; taking "a run covers ALL cases in the set AT LAUNCH TIME (a snapshot by created_at); cases added to the set after launch are NOT in the in-flight run — a later run picks them up" -> if wrong, a run's case set shifts under it and the verdict has no fixed denominator. · probe: adding a case after launch does not change the running run's case count.
- A3 [when] covers: S1 · the request does not say the concurrency/timeout bounds; taking "a bounded per-tenant concurrency (a small fixed ceiling, config-driven) and a per-call timeout from the same settings the live proxy uses — a run is throttled to protect shared upstream capacity, not fanned out unbounded" -> if wrong, a run either crawls or stampedes the provider and other tenants. · probe: a run of N>ceiling cases never has more than the ceiling in flight for that tenant at once.
- A4 [absent] covers: S1 · the request does not say what a run of an EMPTY set (or a set whose every case is refused) means; taking "an empty/all-refused run completes with status `completed`, zero dialed cases, a 0/0 result — a valid (if vacuous) run, never an error; the verdict layer decides what 0/0 means" -> if wrong, a legitimate empty set crashes the runner. · probe: running an empty set returns a completed run with an empty result list, no exception.
- A5 [order] covers: S1 · the request does not say the result/case ordering or how a tie in completion is broken; taking "results are aligned to the case's stable creation order (the eval-set-store A5 order), NOT wall-clock completion order — so a baseline and a candidate run of the same set align case-for-case regardless of which finished first" -> if wrong, the per-case diff misaligns and the verdict compares apples to oranges. · probe: two runs of the same set expose results in identical case order.
- A6 [experience] covers: S1 · the request does not say what a partially-failed run shows; taking "a run surfaces a per-case status (`completed` scored-later · `refused` with the governance reason · `errored` with a breaker/timeout reason) plus a run-level rollup, so an operator sees WHICH cases ran, which were refused for spend, which errored — never a bare 'run failed'" -> if wrong, a half-run is an unactionable red bar. · probe: a run mixing dialed + budget-refused + timed-out cases exposes a distinct, actionable status per case.

## PLAN
contract:
```
POST /v1/evals/sets/{set_id}/runs   body: { model }          # launch a run of the set against a model
  201 -> { id:"er_<32hex>", eval_set_id, model, status:"pending", case_count, created_at }
  403 -> ERR_ZDR_PAYLOAD_BLOCKED        # ZDR tenant, atomic with the first result write (M5)
  404 -> ERR_EVAL_SET_NOT_FOUND         # absent OR cross-tenant set (M6)
  402/403/429 (per governance) at PER-CASE dial time -> the case is `refused`, not the whole run
GET  /v1/evals/runs/{run_id}        -> { id, eval_set_id, model, status, case_count,
                                         counts:{completed,refused,errored,pending}, created_at }
GET  /v1/evals/runs/{run_id}/cases  -> [ { eval_case_id, status, response_text?, usage_record_id?,
                                           reason? } ]  # tenant-scoped, CASE creation order (A5)
     # response_text present only for `completed` cases; a ZDR tenant never reaches this (M5)

Schema (new tables, tenant-scoped; response_text is the ZDR-gated payload-at-rest surface):
  eval_runs(id uuid pk, tenant_id uuid fk, eval_set_id uuid fk, model text, status text,
            created_at timestamptz)                          — index (tenant_id, eval_set_id, created_at)
  eval_case_results(id uuid pk, tenant_id uuid fk, eval_run_id uuid fk ON DELETE CASCADE,
            eval_case_id uuid fk, status text, response_text text null, reason text null,
            usage_record_id uuid null, created_at timestamptz)
            — unique (eval_run_id, eval_case_id)  # idempotent per case on resume (M7, R:DOUBLE_BILL)
            — index (tenant_id, eval_run_id, created_at)

Execution (per case, bounded per-tenant concurrency, each committed as it lands):
  authorize via governance (M1) -> refused? record `refused`+reason, NO dial, NO usage (M3)
    -> else dial upstream through the PER-TENANT breaker + timeout (M4)
         -> breaker-open/timeout -> record `errored`+reason (fail closed)
         -> ok -> raise_if_zdr_locked(s, tenant) then persist `completed`+response_text, one usage row (M2/M5)
  status derived from case counts (M7); resume drives only `pending` cases, terminal cases are no-ops.
```

BUILD NOTE (reuse seam — implementation guidance, PLAN not sealed):
  - Per case, REUSE `CompletionUseCase.complete(raw_key=<launching key>, body=<case.request_body
    with model overridden to the run's model>, upstream=<see below>, usage_recorder=<app.state's>,
    ...)`. This is what makes M1 (enter through governance) + M2 (one usage_record, SAME shape as
    a live request, billed on the SERVED model) hold BY CONSTRUCTION rather than by re-implementation.
    A governance refusal surfaces as the ProblemError `complete()` already raises (budget/credit/
    tier/rate) -> map to `refused`+reason, NO dial (M3). An upstream 5xx / CircuitOpenError ->
    `errored`.
  - ⚠ M4 HARD-STOP realization: the LIVE completion path's breaker (`BoundCircuitBreakerUpstream`
    over `app.state.circuit_breaker`) is GLOBAL — one breaker per app instance, NOT per tenant
    ([[per-tenant-breaker-recurring-defect]]). Reusing it for an eval BURST would let tenant A's
    run open the SHARED breaker and DoS tenant B's LIVE traffic (R:GLOBAL_BREAKER). So the executor
    MUST pass its OWN `upstream` into `complete()`: a per-tenant `CircuitBreaker` (from a registry
    keyed by tenant_id) wrapping the raw `app.state.completion_upstream` delegate — the `upstream=`
    parameter is the injection seam. Eval dials then trip only that tenant's eval-breaker; the live
    global breaker is never touched by an eval run, and the per-tenant concurrency semaphore
    (also a tenant-keyed registry, OWNED by the executor) is separate from the breaker.
  - Response extraction (A2 handoff to scorers): pull the assistant text from the `complete()`
    json_body (choices[0].message.content) and persist it as `response_text`; scoring is a LATER
    pure pass (deterministic-scorers), not this task.
scope (may touch): `apps/gateway/src/gateway/evals/` (NEW: `runs/domain/{ports,entities,errors}.py` · `runs/application/run_executor.py` [the use-case + resume driver] · `runs/infrastructure/{orm,repository,upstream_adapter}.py` · `api/run_router.py`) · `core/error_catalog.py` (NEW `ERR_EVAL_RUN_NOT_FOUND`; REUSE `ERR_ZDR_PAYLOAD_BLOCKED`, `ERR_EVAL_SET_NOT_FOUND`, and the governance error specs) · a new Alembic migration (eval_runs, eval_case_results — the THREE-plus manifests: migration + EXPECTED_TABLES + alembic env.py import + guardrails allow-list, see [[gateway-new-table-four-manifests]]) · `main.py` (mount router + side-effect ORM import) · `apps/gateway/tests/evals_runs/`.
regression floor: `make ci` green incl. the migration-parity gate and the guardrails no-new-tables allow-list; NO change to `governance.py`/`use_cases.py` behavior (the run REUSES them; if a seam needs a param, it is additive and behavior-preserving) or to any existing payload store.
resolved (was least-sure, confirmed at freeze 2026-08-13): a run executes as a BACKGROUND, DURABLE job — launch enqueues and returns `pending` fast; a worker drives cases and commits each result as it lands, mirroring the vector-store ingest worker's enqueue-or-fail-open idiom (fail-open to an inline drive when the queue/redis is absent, e.g. under ASGITransport tests). This is what makes M7 resume real across crash/redeploy. A tiny-set synchronous path is a later optimization, not the contract.

## EDGES
- E1 A tenant over budget/credit launches a run of a non-empty set -> the run launches but EVERY case is `refused` at the governance guard with NO upstream call and NO usage row; assert zero provider dials (M3, R:GOVERNANCE_BYPASS).
- E2 Tenant A's run drives A's per-tenant breaker OPEN (repeated upstream failures); tenant B's request in the same process still succeeds -> isolation proven (M4, R:GLOBAL_BREAKER).
- E3 A ZDR tenant (flag set before launch, and flipped ON mid-run by a slow double) launches a run -> refused 403 atomic with the first result write; assert ZERO eval_case_results rows persisted (M5, R:ZDR_BLOCKED — the TOCTOU-safe pattern).
- E4 A run is interrupted after K of N cases commit, then resumed -> only the N-K `pending` cases are dialed; the K terminal cases are NOT re-dialed or re-billed; final usage_records count == dialed-case count exactly once (M7, R:DOUBLE_BILL).
- E5 An upstream call for one case times out -> that case is `errored` with a timeout reason and the run CONTINUES with the remaining cases (one bad case never sinks the run); the run's rollup reflects the mix (M4, A6).
- E6 A run of an empty set (or one whose every case is refused) -> `completed`, 0 dialed, empty/all-refused result list, no exception (A4).

## CHECKS
- test_run_enters_governance_per_case · covers: M1, M8, R:GOVERNANCE_BYPASS · a run of a set dials upstream only after the governance guard passes; one dial per case, driven entirely through a zero-network fake upstream (the Protocol port, M8), in the guard order a live request uses.
- test_over_budget_run_refuses_every_case_no_dial · covers: M3, E1, R:GOVERNANCE_BYPASS · a tenant forced over budget launches a run; every case is `refused`, the fake upstream records ZERO dials, and NO usage_records rows are written.
- test_one_usage_record_per_dialed_case · covers: M2, R:DOUBLE_BILL · a run of N dial-able cases writes exactly N usage_records, each on the served model id, matching the shape a live request of that request_body produces.
- test_per_tenant_breaker_isolation · covers: M4, E2, R:GLOBAL_BREAKER · tenant A's run drives A's breaker open (fake upstream fails); a concurrent tenant B request in the same process still succeeds — the breaker is keyed by tenant, not global.
- test_zdr_run_refused_atomically_zero_results · covers: M5, E3, R:ZDR_BLOCKED · a ZDR tenant's run (incl. a slow double flipping ZDR mid-run between the lock and the first result write) persists ZERO eval_case_results; asserted on the PERSISTED ROW count, not the response.
- test_resume_does_not_rebill_terminal_cases · covers: M7, E4, R:DOUBLE_BILL · a run interrupted after K/N committed results, then resumed, dials only the N-K pending cases; total upstream dials == N exactly once, terminal cases untouched.
- test_timeout_case_errored_run_continues · covers: M4, E5, A6 · one case's upstream dial times out -> that case is `errored` with a reason and the remaining cases still complete; the run rollup exposes a distinct, actionable per-case status (A6).
- test_cross_tenant_and_absent_run_uniform_404 · covers: M6, R:RUN_NOT_FOUND · a run/set owned by another tenant and an absent id both return byte-identical 404, no oracle.
- test_results_aligned_to_case_creation_order · covers: A5 · two runs of the same set expose their case results in identical (case-creation) order regardless of completion order.
- test_empty_set_run_completes_vacuously · covers: A4, E6 · running an empty set returns a `completed` run with an empty result list and no exception.
- test_cross_tenant_launch_and_billing_identity · covers: A1 · tenant B launching a run on tenant A's set gets a uniform 404; tenant A's own run bills the LAUNCHING tenant/key (the usage record carries A's tenant).
- test_run_snapshot_fixed_at_launch · covers: A2 · a case added AFTER a run launches does not change that run's case_count — the case set is snapshotted at launch time.
- test_bounded_per_tenant_concurrency · covers: A3 · a run of N>ceiling cases never has more than the per-tenant ceiling dialing at once (the tenant semaphore bounds in-flight dials).
red-first: every check MUST fail first (the run module + tables do not exist yet).

## EVIDENCE
receipt: <runs/<n>.md>
gate: <PASS | RISK-ACCEPTED | HARD-STOP>

## LESSONS
- <lesson> -> add learn <lens>
