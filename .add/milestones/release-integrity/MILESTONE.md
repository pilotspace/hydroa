# MILESTONE: Release integrity: restore CI, close the deploy blockers, clear lint/type/suite debt

goal: Every merge to main is proven by a green CI run on the merged artifact — no admin-merge — and a pgvector-bearing release can be deployed to managed Postgres from a written runbook without index or extension surprises
rationale: new-major (intake 2026-07-25, Tin-approved). The R5 PR review surfaced that the gating risk on this project is no longer missing features but an unattested delivery substrate: CI has failed at 0 steps for 7+ releases (org-billing block, `[[org-billing-0step-ci]]`), so every merge since 0.8.0 landed by admin-merge on locally-run pytest. That is the single hardest thing to defend under SOC 2 CC8.1 change management — you cannot evidence that tests ran on the merged artifact. R5 additionally shipped an unrunbooked Postgres image swap and an unrunbooked `CREATE EXTENSION` dependency. This milestone is the prerequisite for R7 enterprise-trust and R8 soc2-groundwork; it delivers no product features by design.
stage: production · status: active · created: 2026-07-25T07:35:36+00:00
relations: relates-to: managed-rag-finetune

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/PLAN.md`.

## Scope
In:  GitHub Actions billing/runner restoration to a genuinely green gateway + dashboard + kind-e2e run · a branch-protection rule that makes green CI required (ending admin-merge) · the pgvector deploy runbook (glibc/musl collation change on existing volumes, managed-Postgres extension prerequisite) · a boot-time preflight that fails loudly when the `vector` extension is absent · the pre-existing ruff-format debt (#36) and pyright debt on main (#56) · test-suite determinism (#39 shared-Postgres DDL contention, #37 shared-Redis cross-test contamination).
Out: every PR #89 code residue (#59–#65) — those belong to R7 `pr89-residue`, not here · the external pentest and security-debt closure (R7) · SOC 2 evidence collection (R8) · any product feature · the 273-delta triage (#38, R7) · the dashboard's 13 pre-existing design-token failures (#55 — routed to the design-foundation owner, not release-integrity debt).

## Ground   (shared real-code context)
Touches (shared files · symbols): `.github/workflows/` (the three failing jobs: gateway, dashboard, kind-e2e) · `infra/docker-compose.prod.yml` + `charts/ai-proxy/values.yaml` (the `pgvector/pgvector:pg16` swap) · `apps/gateway/migrations/versions/55dc3f920a38_vector_store_core.py` (the `CREATE EXTENSION` site) · `apps/gateway/src/gateway/main.py` (lifespan — the preflight's home) · `apps/gateway/tests/conftest.py` (xdist/Redis isolation) · `docs/` runbooks.
Anchors: `should_start_vector_store_ingest_worker` and the lifespan's existing boot-order (the preflight must land before the ingest worker starts) · the existing `make ci` target (ruff + pyright + pytest) — CI must run exactly that, not a divergent script.
Honors (conventions): CONVENTIONS.md layering · `[[shared-test-postgres-no-timeouts]]` (unique `GATEWAY_TEST_DATABASE_URL` per worktree) · `[[gateway-suites-xdist-schema-collision]]` (the three schema-sharing suites run serially) · `[[git-push-https-gotcha]]`.
Issues/Risks (shared): the CI failure is an ORG BILLING condition, not code — task 1 may be blocked on an action only Tin can take (payment method / plan), so it is sequenced first and explicitly allowed to hand back a human-action item rather than stall the milestone · the collation fix is destructive-adjacent (REINDEX or dump/restore on a live volume) and must be rehearsed against a copy before it is written as a runbook step · #39's fix (template-DB clone) changes test isolation for the WHOLE suite — a regression there is invisible until it flakes weeks later.

## Shared decisions & glossary deltas
- **"Green CI" means green on the merged artifact.** A locally-green suite is evidence of correctness, never evidence of change management. Once task `ci-restoration` lands, admin-merge is retired; a red gate is a blocked merge.
- **The preflight fails CLOSED.** A gateway that cannot confirm the `vector` extension refuses to boot rather than serving RAG surfaces that 500 at first use.
- **No feature work in this milestone.** Any defect found that is not CI/deploy/debt is captured as a todo for R7, never fixed here — this milestone's value is entirely in being small enough to finish in a week.

## Shared / risky contracts (freeze these first)
- the CI job matrix + the required-checks branch-protection contract -> owning task `ci-restoration` (every later task's evidence depends on what "green" means)

## Tasks (breadth-first decomposition)
- [ ] ci-restoration          depends-on: none              — resolve the org-billing block; get gateway + dashboard + kind-e2e to a real green run on `make ci`; make those checks required on main.
- [ ] pgvector-deploy-runbook depends-on: none              — collation-change procedure for existing volumes; managed-Postgres extension prereq; boot-time fail-closed preflight. (#66, #67)
- [ ] lint-type-debt-sweep    depends-on: ci-restoration    — #36 ruff-format, #56 pyright. Format-only and type-only commits, never mixed with logic.
- [ ] suite-stability         depends-on: ci-restoration    — #39 template-DB clone or `-n 8` cap; #37 per-worker Redis namespace. Full suite deterministic across 3 consecutive runs.

## Exit criteria (observable)
- [ ] A PR opened against main shows the green required checks and CANNOT be merged while any is red — demonstrated on a real PR, not a workflow-file diff        (← ci-restoration)   (verify: `gh pr checks <n>` reports `gateway` + `dashboard` green with non-zero step counts, and `gh api repos/pilotspace/hydroa/branches/main/protection` lists them as required with `enforce_admins: true`)
      <!-- amended 2026-07-25 by ci-restoration CR v2 (Tin-approved): was "three green required checks" incl. `kind-e2e`. kind-e2e is PATH-FILTERED, so it reports no status at all on a PR touching none of its paths — requiring it would deadlock such a PR permanently at "Expected — waiting for status". It is also a 45-min job against the metered-minutes budget that is itself fault 1. It stays opt-in (workflow_dispatch) and runs before a release cut. -->
      <!-- FOUND AT BUILD: this criterion also depends on `lint-type-debt-sweep`. `make ci` is red BEFORE any of this work — 37 ruff, 3 pyright, 4 unallowlisted dependencies (dnspython, pgvector, pytest-rerunfailures, pytest-xdist; pgvector entered via #89). Restoring runners is necessary but NOT sufficient for a green check. See todos #68/#69. -->
- [ ] The dependency allow-list gate passes: every dependency in `apps/gateway/pyproject.toml` is present in `.add/dependencies.allowlist` with a written justification        (← lint-type-debt-sweep)   (verify: `make allowlist` exit 0, and each of the four added entries carries a justification comment)
- [ ] An operator can follow a written runbook to take a 0.12.x-era Postgres volume to the pgvector image with no collation warning and no mis-ordered index, verified on a restored copy of a production-shaped dump        (← pgvector-deploy-runbook)   (verify: rehearsal on a restored dump — `SELECT * FROM pg_database WHERE datcollversion IS DISTINCT FROM pg_database_collation_actual_version(oid)` returns zero rows, and `amcheck` passes on the text indexes)
- [ ] A gateway started against a Postgres lacking the `vector` extension refuses to boot with a named, actionable error instead of serving /v1/vector_stores        (← pgvector-deploy-runbook)   (verify: test asserting create_app lifespan raises a named preflight error against a vector-less DB)
- [ ] `make ci` passes with zero ruff-format and zero pyright findings on main        (← lint-type-debt-sweep)   (verify: `make ci` exit 0)
- [x] The full gateway suite completes green three consecutive times at `-n 12 --dist loadscope` with NO `--reruns`, no manual retry and no chunking workaround        (← suite-stability)   (verify: 3 consecutive full-suite runs in one unbroken sequence, each a single pytest invocation, each exit 0, wall-clock recorded per run; a red run RESETS the streak)
      <!-- amended 2026-08-10 by flake-tail-burndown (Tin-approved): the criterion named no parallelism, and that is not a detail — `-n 4` and `-n 12` are different EXPERIMENTS. Measured on a 12-core dev host: `-n 4` projects ~2.5h per run, `-n 12` ~7min, and they impose different contention on the shared Postgres/Redis. The flake tail this criterion exists to detect is contention-dependent, so an unpinned criterion is satisfiable by its WEAKEST reading — a low-contention run that proves nothing. `-n 12` is the harshest shape available locally and is the shape under which the tail was originally observed (`make test-parallel`), minus its `--reruns 1`, because an auto-retrying gate hides exactly what is being measured. "Streak RESETS on red" was already the intent but was not written down; six attempts to date have each been aborted on the first red rather than continued, and that reading is now explicit. -->
      <!-- FOUND AT BUILD (flake-tail-burndown): this criterion measures MORE than the frozen task's scope. The sleep-census work is complete and machine-checked (210 sites partitioned, UNKNOWN=0), yet five of six streak attempts died on latent-race classes that are NOT sleep sites: a DB singleton row read without schema ownership, a live DNS lookup in a "no network required" unit test, a poll on the wrong signal, a fire-and-forget assertion with no wait at all, a self-contradicting race assertion, a settle that cannot distinguish "all landed" from "none started", and finally an absolute wall-clock latency budget. All TEN classes are fixed and each was reproduced deterministically. Guard count grew 4 -> 8 (CR v3) once it was clear the SLEEP population was the wrong population: sleep sites are a subset of "assertions on fire-and-forget writes", so `UNKNOWN == 0` was silent about the classes that actually kept killing the streak. Treat this criterion as a full latent-race audit of 4570 tests, not as a corollary of the census. -->
      <!-- Coverage is deliberately NOT part of this criterion: it is a measured 1.92x wall-clock multiplier that cannot change a test's verdict. The coverage gate lives in the `make ci` criterion above. -->
      <!-- ✅ MET 2026-08-10T12:36:18Z by attempt A7, the FIRST of the 2 Tin-capped attempts. Three consecutive single-invocation runs at `-n 12 --dist loadscope --no-cov`, no `--reruns`, no retry, no chunking, all exit 0, on tree `7b96dee`:
             RUN 1  start 12:05:27Z  824s  4570 passed · 7 skipped · 1 xfailed
             RUN 2  start 12:19:12Z  694s  4570 passed · 7 skipped · 1 xfailed
             RUN 3  start 12:30:45Z  332s  4570 passed · 7 skipped · 1 xfailed
           Duration spread (824s -> 332s) is host warmth, NOT skipped work: all three collected and passed the identical 4570/7/1, and all three brought up 12 xdist nodes. Runs 1-2 additionally competed with light interactive `uv run python` census invocations; run 3 had the host to itself with warm Postgres/Redis and page cache. The criterion requires wall-clock to be RECORDED, not to be uniform, so this is reported rather than smoothed.
           Attempt log, full: A1 red@run1 (6 failures, 2 classes) · A2 green,red@run2 · A3 green,red@run2 · A4 red@run1 · A5 red@run1 · A6 red@run1 (2 failures / 4566, 894s) · A7 GREEN,GREEN,GREEN. Per-attempt defect count 6 -> 1 -> 1 -> 1 -> 1 -> 2 -> 0. -->
      <!-- ⚠ What this criterion does NOT establish, stated so the tick is not read as more than it is: three green runs are evidence about the classes that were REACHED, not a proof that none remain. Classes 6 and 8 are unguarded by design (they need judgement no AST scan can do) and class 10 (absolute wall-clock thresholds) is unguarded AND unenumerated — todo #105. The eight standing guards in tests/repo_hygiene/ are what make a REGRESSION of the nine fixed classes fail loudly; they say nothing about a tenth. -->
      <!-- The evidence generator is kept: $SCRATCHPAD/proving_runs.sh (aborts the streak on the first red, so a green record cannot be assembled from a retry). Re-running it is the cheapest way to re-establish this criterion after any broad test-suite change. -->
      <!-- A6's two failures are worth naming because neither was a latent race in product code, and one was SELF-INFLICTED:
             (a) tests/repo_hygiene/test_negative_wait_declarations flagged a SIBLING GUARD's explanatory comment as an orphaned marker. The guard was right and its matcher was too loose — it could not tell a declaration from a comment discussing the convention. Anchoring the marker to the start of the comment fixed both it and the mirror hole in M7's guard, where a prose mention would have GRANDFATHERED an undeclared sleep. A guard's own false positive is cheap; the mirror false NEGATIVE it revealed is what mattered.
             (b) scoped_self_serve_signup::test_email_dispatch_never_blocks_the_response is class 10, a WALL-CLOCK LATENCY BUDGET: response must return in <0.5s against an injected 1.5s mail delay. It measured 0.586s, so the structural property it exists to prove (dispatch is off the request path) HELD by a factor of ~2.5 and the assertion failed anyway, because signup's own cost is the deliberate Argon2 timing mask. Rewritten to park the send on an Event so the passing path contains no wall-clock at all.
           Class 10 generalises: any absolute-duration threshold measured on a contended host is a flake with an alibi. Not guarded — the population is unenumerated (todo #105). -->
      <!-- Tin 2026-08-10: capped at 2 further attempts after A6. If no green streak lands, this criterion is tracked as its own audit task rather than holding the milestone open indefinitely. -->


## Strategy
- Approach (sequencing): **first-slice-unblocks.** `ci-restoration` is the keystone — until it is green, no other task in this milestone can produce the evidence that IS its exit criterion, and the whole SOC 2 track stays unreachable. It also carries the only dependency this project cannot resolve itself (billing), so it goes first to surface that blocker on day one rather than day five.
- Freeze-first: the CI job matrix + required-checks contract, in `ci-restoration`. Everything downstream cites it.
- Waves (parallel): wave 1 = `ci-restoration` ∥ `pgvector-deploy-runbook` (fully independent — the runbook needs no CI). Wave 2 = `lint-type-debt-sweep` ∥ `suite-stability` (both need a green baseline to prove against, and they touch disjoint files: source formatting vs `conftest.py`).
- Tradeoffs weighed: *(a) fold the debt sweep into R7* — rejected: ruff/pyright noise makes a genuinely-green CI unachievable, so the debt is a prerequisite for the keystone's own exit criterion, not separable. *(b) fix the deploy runbook inside R5's PR* — rejected: Tin scoped #89 to the HARD-STOP only, and the runbook needs a rehearsal against a real dump that would block the merge for days. *(c) do the pentest here too* — rejected: an external engagement against a codebase whose CI cannot prove what shipped is money spent on a moving target; it belongs after this milestone, which is exactly why R7 depends on R6.

## Close — ship review
> AI fills when every task is done.

### Ship by domain
- tooling : <fill at close>
- skill   : <fill at close>
- book    : <fill at close>

### Cross-task evidence
- <slug> : gate=<PASS|RISK-ACCEPTED> · tests=<n green> · residue=<none|note>

### Goal met?
- [ ] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: <restate + the one evidence line that proves it>

## Release steps
- [ ] open a PR from the Close ship-review; the human reviews + merges (the FIRST merge that goes through required checks rather than admin-merge — that is itself the milestone's proof)
- [ ] cut 0.14.0 "release integrity" — CHANGELOG + RELEASES rows
- [ ] tag / publish / deploy, following the new pgvector runbook against staging first  (human-run)
- [ ] archive the milestone; open R7 `enterprise-trust` intake
