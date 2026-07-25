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
- [ ] A PR opened against main shows three green required checks and CANNOT be merged while any is red — demonstrated on a real PR, not a workflow-file diff        (← ci-restoration)   (verify: `gh pr checks <n>` reports 3 green with non-zero step counts, and `gh api repos/pilotspace/hydroa/branches/main/protection` lists them as required)
- [ ] An operator can follow a written runbook to take a 0.12.x-era Postgres volume to the pgvector image with no collation warning and no mis-ordered index, verified on a restored copy of a production-shaped dump        (← pgvector-deploy-runbook)   (verify: rehearsal on a restored dump — `SELECT * FROM pg_database WHERE datcollversion IS DISTINCT FROM pg_database_collation_actual_version(oid)` returns zero rows, and `amcheck` passes on the text indexes)
- [ ] A gateway started against a Postgres lacking the `vector` extension refuses to boot with a named, actionable error instead of serving /v1/vector_stores        (← pgvector-deploy-runbook)   (verify: test asserting create_app lifespan raises a named preflight error against a vector-less DB)
- [ ] `make ci` passes with zero ruff-format and zero pyright findings on main        (← lint-type-debt-sweep)   (verify: `make ci` exit 0)
- [ ] The full gateway suite completes green three consecutive times with no manual retry and no chunking workaround        (← suite-stability)   (verify: 3 consecutive full-suite runs, each exit 0, wall-clock recorded)

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
