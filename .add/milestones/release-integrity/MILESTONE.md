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
<!-- ⚠ These four boxes were ALSO drifted (all unticked while `add.py report release-integrity` said 12/12 tasks done, 9 PASS + 3 RISK). Ticked 2026-08-11 against that report, not from memory. The milestone grew from these 4 to 12 tasks; the 8 later ones are listed in the report, not here, because this doc is meant to stay thin. -->
- [x] ci-restoration          depends-on: none              — resolve the org-billing block; get gateway + dashboard + kind-e2e to a real green run on `make ci`; make those checks required on main.
      <!-- Gated RISK-ACCEPTED. Landed in the end, but note the framing above was wrong on two counts: the blocker was NOT (only) org billing — the branch-protection half was plan+visibility (see exit #1) — and `kind-e2e` was dropped from the required set by CR v2 and has still never passed (0 green in 30 runs, todo #109). -->
- [x] pgvector-deploy-runbook depends-on: none              — collation-change procedure for existing volumes; managed-Postgres extension prereq; boot-time fail-closed preflight. (#66, #67)
      <!-- Gated RISK-ACCEPTED; waiver open until 2026-09-30 for the M3 walkthrough on a REAL target. The 2026-08-10 rehearsal was Docker-on-a-musl-volume and found that documented §4a cannot finish — see exit #3. -->
- [x] lint-type-debt-sweep    depends-on: ci-restoration    — #36 ruff-format, #56 pyright. Format-only and type-only commits, never mixed with logic.
- [x] suite-stability         depends-on: ci-restoration    — #39 template-DB clone or `-n 8` cap; #37 per-worker Redis namespace. Full suite deterministic across 3 consecutive runs.
      <!-- Gated RISK-ACCEPTED; the streak was ultimately delivered by `flake-tail-burndown` (exit #6), not by this task alone. And "deterministic" is met in the sense the criterion states (3 consecutive green runs), NOT in the sense of "no latent flakes remain" — #102 proved otherwise within a day. todos #105/#111 carry the residue. -->
<!-- The remaining 8 tasks (vector-extension-preflight · suite-infra-tripwire · breaker-4xx-classification · date-bomb-sweep · release-provenance · ci-timeout-and-e2e-scope · ci-flake-classification · flake-tail-burndown) were scaffolded after intake as the real shape of the work emerged. `add.py report release-integrity` is the authoritative list. -->
- [x] dashboard-lint-gate + masked-gate sweep (unscaffolded, landed as PR #103) — closed todos #107/#108 and the todo-#111 flake that blocked #102.
      <!-- Not an ADD task: found while sweeping for gates that never reach a verdict, after the milestone's own tasks were all done. Recorded here so the milestone's shipped surface matches what is actually on main. -->


## Exit criteria (observable)
<!-- ⚠ BOOKKEEPING, found 2026-08-10 while closing #6: these boxes had drifted BEHIND reality. Before that pass exactly ZERO were ticked, yet #2 (allowlist), #4 (vector preflight, PR #94) and #5 (`make ci` green) had all been met in earlier sessions — the work landed, the box never moved. The milestone was reporting itself as further from done than it was, which is the less dangerous direction but still wrong.
     Repaired by RE-VERIFYING rather than by ticking from memory: each box below now carries the evidence and the date it was re-checked. A box is ticked here only when someone re-ran its stated verify command in the session that ticked it. If you find an unticked box, re-verify it — do not assume the drift runs only one way. -->

- [x] A PR opened against main shows the green required checks and CANNOT be merged while any is red — demonstrated on a real PR, not a workflow-file diff        (← ci-restoration)   (verify: `gh pr checks <n>` reports `gateway` + `dashboard` green with non-zero step counts, and `gh api repos/pilotspace/hydroa/branches/main/protection` lists them as required with `enforce_admins: true`)
      <!-- amended 2026-07-25 by ci-restoration CR v2 (Tin-approved): was "three green required checks" incl. `kind-e2e`. kind-e2e is PATH-FILTERED, so it reports no status at all on a PR touching none of its paths — requiring it would deadlock such a PR permanently at "Expected — waiting for status". It is also a 45-min job against the metered-minutes budget that is itself fault 1. It stays opt-in (workflow_dispatch) and runs before a release cut. -->
      <!-- FOUND AT BUILD: this criterion also depends on `lint-type-debt-sweep`. `make ci` is red BEFORE any of this work — 37 ruff, 3 pyright, 4 unallowlisted dependencies (dnspython, pgvector, pytest-rerunfailures, pytest-xdist; pgvector entered via #89). Restoring runners is necessary but NOT sufficient for a green check. See todos #68/#69. -->
      <!-- ⏳ HALF PROVEN 2026-08-11. The 403 that blocked this for 7+ releases was never a billing fault at all — it was PLAN + VISIBILITY: branch protection on a PRIVATE repo needs a paid org plan. Tin chose to resolve it by making `pilotspace/hydroa` PUBLIC (his call, taken knowingly; a full-history secret scan over all 1221 commits ran first and found no live credential — only AWS's published `AKIAIOSFODNN7EXAMPLE`, `sk-0000…`, a vertex test key whose body is base64 `"fake"`, and a runbook example whose secret half is elided; `.env` was never committed). Protection then applied on the first try:
             `gh api repos/pilotspace/hydroa/branches/main/protection` → contexts `["gateway","dashboard"]`, `strict: true`, `enforce_admins.enabled: true`, `allow_force_pushes: false`, `allow_deletions: false`.
           ✅ The CANNOT-BE-MERGED half is PROVEN, and proven the honest way — by a refused merge, not by reading the config back. `gh api -X PUT .../pulls/102/merge` with an ADMIN token returned `HTTP 405: Required status check "gateway" is in progress.` An admin's own merge being refused is exactly what `enforce_admins` is for and exactly what CC8.1 wants evidenced.
             (⚠ `git push --dry-run` is NOT a valid test of this and was discarded as one: --dry-run never sends the ref update, so the server never evaluates protection and it reports a cheerful success. Do not cite a dry-run as enforcement evidence.)
           ✅ The SHOWS-GREEN half is NOW PROVEN TOO, on **PR #103** (2026-08-11) — `gh pr checks 103` reported `dashboard pass 3m48s` + `gateway pass 32m3s`, with non-zero step counts on both (`gateway` 15 steps, `dashboard` 10 steps), and #103 then merged by the NORMAL path with no admin bypass (squash `2cffc36`) — the first non-admin merge in 7+ releases, which is item 1 of the Close checklist as well as this criterion.
             ⚠ It was NOT #102 that supplied this, and the reason is worth keeping. #102's gateway job FAILED on a docs-only diff (`1 failed, 4569 passed`, 1:05:42) while the identical commit had passed on main minutes earlier — a fixed-window rate-limit flake (todo #111), fixed in #103. So the gate's first act was to CATCH something and block a merge on it. That is the criterion working, not the criterion failing: a gate that has never refused anything has not been shown to work.
           ✅ CRITERION MET 2026-08-11. All four clauses hold: green on a real PR (#103), non-zero step counts, protection lists both as required with `enforce_admins: true`, and merge REFUSED while red (HTTP 405 on #102). Both halves were verified by re-running the stated commands in the session that ticked this box.
           ⚠ Residual gap, named rather than papered over: `required_approving_review_count` is **0**. Not an oversight — GitHub forbids approving your own PR, so on a solo-maintainer repo any non-zero count deadlocks main permanently. The gate therefore evidences "tests ran green on the merged artifact" (the thing 7+ releases could not evidence) but NOT four-eyes review. If SOC 2 CC8.1 is read as requiring independent approval, this needs a second human with write access, and no amount of configuration substitutes for that. Carry it into R8 `soc2-groundwork` as an open item. -->
      <!-- ⚠ `strict: true` (branch must be up to date with main before merge) is deliberate but has a real cost at this suite's size: every merge to main invalidates every other open PR and forces a fresh ~60–80 min gateway run. It is affordable now because PRs land roughly one at a time. If concurrent PRs ever become normal, the fix is a merge queue, NOT dropping `strict` — dropping it re-opens exactly the semantic-drift hole that hid 16 unregistered ORM modules behind the parity gate. -->
- [x] The dependency allow-list gate passes: every dependency in `apps/gateway/pyproject.toml` is present in `.add/dependencies.allowlist` with a written justification        (← lint-type-debt-sweep)   (verify: `make allowlist` exit 0, and each of the four added entries carries a justification comment)
      <!-- ✅ VERIFIED 2026-08-10 on `ff42e1d`, both halves. Gate: `check_allowlist: OK — 1 manifest(s) clean` and `check_node_deps: OK — 42 packages clean` (run as part of `make ci`, exit 0 on those steps). Justifications: all four added entries carry substantive ones, not placeholders — dnspython (DNS TXT lookup on the domain-ownership TRUST path, pure-Python, no system resolver), pgvector (SQLAlchemy `Vector` column type, R5 #89), pytest-rerunfailures (flaky-quarantine, test-only, never imported by src/), pytest-xdist (parallel suite execution). This box was met in an earlier session and simply never ticked — see the bookkeeping note under Exit criteria. -->
      <!-- ⚠ Worth knowing rather than acting on: pytest-rerunfailures' justification describes explicit retry of known-flaky tests, which is exactly what exit #6's evidence deliberately EXCLUDES (`make test-parallel` sets `--reruns 1`; `make test-ci` and the #6 streak do not). Both can be true — the dependency is allowed, and the stability criterion refuses to lean on it. -->
- [x] An operator can follow a written runbook to take a 0.12.x-era Postgres volume to the pgvector image with no collation warning and no mis-ordered index, verified on a restored copy of a production-shaped dump        (← pgvector-deploy-runbook)   (verify: rehearsal on a restored dump — `SELECT * FROM pg_database WHERE datcollversion IS DISTINCT FROM pg_database_collation_actual_version(oid) AND datname = current_database()` returns zero rows, and `amcheck` passes on the text indexes)
      <!-- ✅ WALKED 2026-08-10, end to end, against a GENUINE musl-lineage volume: postgres:16-alpine on a fresh volume -> alembic upgrade c7e0a4b2d9f1 (the revision before the vector migration, 52 tables) -> 2001 tenants + 3000 users with emails spanning punctuation and accents (where musl codepoint order and glibc en_US.utf8 weighting genuinely disagree) -> same volume served by pgvector/pgvector:pg16. Full record in docs/runbooks/pgvector-deploy.md §6.
           Final state: preflight `status: OK` exit 0 · datcollversion = actual = 2.36 · all btree indexes pass bt_index_check · scoped mismatch query 0 rows.
           The silent-failure claim is now CONFIRMED rather than assumed: after the image swap Postgres emitted ZERO collation warnings while recorded=NULL/actual=2.36. And the corruption was real — `ERROR: item order invariant violated for index "users_email_key"` before the remedy, clean after. -->
      <!-- ⚠ VERIFY CLAUSE AMENDED, because the criterion as written could NEVER be satisfied: the query was unscoped, and `template0`/`template1`/`postgres` on a musl-created cluster keep `datcollversion = NULL` permanently. It returned 4 rows even after a perfect remedy. Added `AND datname = current_database()`. This is a correction to the criterion's own verifier, not a relaxation of what it demands — the app database must still come back clean. -->
      <!-- ⚠ FOUR RUNBOOK DEFECTS found by walking it, all now fixed in the runbook, and NONE of them visible from reading it:
             1. §4a CANNOT FINISH on the musl case — `ALTER DATABASE ... REFRESH COLLATION VERSION` errors with `invalid collation version change` because Postgres will not move datcollversion from NULL. The preflight then reports FAIL forever. §4a was presented as the primary remedy for the most common scenario (same cluster, same volume) and it is the one that does not work. REINDEX does still repair the indexes.
             2. §4b is the ONLY remedy that reaches OK for this case — and it works on the SAME cluster and volume, because what matters is that CREATE DATABASE happens while running under the new libc, not where the bytes live. The runbook framed it as "new volume, new provider" and undersold it.
             3. The documented `python3 scripts/pg_preflight.py` invocation does not run (ModuleNotFoundError: sqlalchemy) and reports the failure as "could not reach or query the database" — fails safe, but points the operator at a network problem that does not exist.
             4. `make pg-preflight` collapses the script's FAIL (exit 1) into make's own exit 2, which the runbook's contract table defines as UNKNOWN — whose documented remedy is "check host/port/credentials", the wrong action for a FAIL. -->
      <!-- ⚠ NOT established, so do not read this tick as "pgvector is deploy-safe": no managed-provider target was tested (§3 declares managed Postgres unsupported, so CREATE EXTENSION privilege remains unverified) and the §2 Kubernetes StatefulSet/PVC path is still un-walked. This was a local Docker rehearsal on a production-SHAPED dump — exactly what the criterion asks for, and not the same as a production dry-run. -->
- [x] A gateway started against a Postgres lacking the `vector` extension refuses to boot with a named, actionable error instead of serving /v1/vector_stores        (← pgvector-deploy-runbook)   (verify: test asserting create_app lifespan raises a named preflight error against a vector-less DB)
      <!-- ✅ RE-VERIFIED 2026-08-10 on `7dbb463`: `pytest tests/vector_extension_preflight` → 5 passed in 3.76s. The five tests map onto this criterion clause by clause, which is why it can be ticked on a test run rather than a manual boot: `test_lifespan_refuses_to_boot_without_vector_extension` (refuses to boot) · `test_error_names_database_extension_and_remedy` (NAMED and ACTIONABLE — not a bare exception) · `test_ingest_worker_never_starts_when_preflight_fails` (does not go on to serve) · `test_boots_normally_when_extension_present` (the negative control, without which the other four could pass by always failing) · `test_unreachable_database_is_unknown_not_missing` (a bad password or closed port is never RENAMED to "extension missing"). Landed by PR #94; met in an earlier session and never ticked. -->
      <!-- ⚠ Does NOT cover the collation half of the same hazard — that is criterion #3, still open, and `scripts/pg_preflight.py` is a DIFFERENT preflight. Do not read this tick as pgvector being deploy-safe. -->
      
- [x] `make ci` passes with zero ruff-format and zero pyright findings on main        (← lint-type-debt-sweep)   (verify: `make ci` exit 0)
      <!-- ✅ RE-VERIFIED 2026-08-10: `make ci` exit 0 in 1114s (18m34s) — "✅ pipeline green". ruff check "All checks passed!", `ruff format --check` 1428 files already formatted (ZERO format findings), pyright "0 errors, 0 warnings, 0 informations", check_allowlist OK, check_node_deps OK, then 4570 passed · 7 skipped · 1 xfailed. -->
      <!-- ⚠ ONE HONEST GAP in this tick: the criterion says "on main" and this ran on `ff42e1d`, the head of `fix/flake-tail-burndown` — which is main plus this task's TEST-ONLY commits (zero files under apps/gateway/src/). That is the tree that will BECOME main via PR #100, and main's own last recorded `make ci` was likewise green (4559 passed), so nothing suggests a divergence. But the literal reading of the criterion is a run on main itself, and that is a post-merge confirmation nobody has done yet. Re-run `make ci` on main after #100 merges and replace this note. -->
      <!-- Note this is also the FIFTH consecutive green full-suite run of the day and the only one at `-n 4` — three at `-n 12 --no-cov` (exit #6), one at `-n 12` with coverage, one here. Different parallelisms, same verdict. -->
- [x] The full gateway suite completes green three consecutive times at `-n 12 --dist loadscope` with NO `--reruns`, no manual retry and no chunking workaround        (← suite-stability)   (verify: 3 consecutive full-suite runs in one unbroken sequence, each a single pytest invocation, each exit 0, wall-clock recorded per run; a red run RESETS the streak)
      <!-- ⚠ CORRECTION 2026-08-10, same day, by measurement: the "~2.5h per run" figure below is WRONG and is left visible rather than quietly edited out. `make ci` (which IS `-n 4 --dist loadscope`, WITH coverage) completed in 1104s — 18m24s, not 2.5 hours. The original figure was a projection extrapolated from a partial run, off by roughly 8x, and it was used as a stated reason for pinning `-n 12`. Actual measured shapes: `-n 4` + coverage 1104s · `-n 12` no-cov 824/694/332s · `-n 12` + coverage 477s. The DECISION to pin `-n 12` survives, because it never rested on that leg — the real justification is that `-n 12` imposes harsher contention on the shared Postgres/Redis and is the shape under which the tail was originally observed. But "-n 4 is impractically slow" is not a true statement about this host and must not be repeated. -->
      <!-- amended 2026-08-10 by flake-tail-burndown (Tin-approved): the criterion named no parallelism, and that is not a detail — `-n 4` and `-n 12` are different EXPERIMENTS. Measured on a 12-core dev host: `-n 4` projects ~2.5h per run [SEE CORRECTION ABOVE — actually 18m24s], `-n 12` ~7min [actual 5m30s–13m42s], and they impose different contention on the shared Postgres/Redis. The flake tail this criterion exists to detect is contention-dependent, so an unpinned criterion is satisfiable by its WEAKEST reading — a low-contention run that proves nothing. `-n 12` is the harshest shape available locally and is the shape under which the tail was originally observed (`make test-parallel`), minus its `--reruns 1`, because an auto-retrying gate hides exactly what is being measured. "Streak RESETS on red" was already the intent but was not written down; six attempts to date have each been aborted on the first red rather than continued, and that reading is now explicit. -->
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

## Learnings

The through-line of this milestone: **a check that never reaches a verdict reports green.** It
cost 6+ releases of admin-merge history and turned up in five distinct shapes, three of them
found only by going looking after the first two.

1. **Masked gates — the five shapes seen here.** (a) A gate ORDERED BEHIND a failing step, so
   one red test skips it (`migrate-parity`, which hid 16 unregistered ORM modules for months).
   (b) A gate INVOKED BY NOTHING — `npm run lint` was declared since the dashboard existed and
   never run; Next 16 silently dropped the `next build` ESLint pass and the gate went dark at
   the upgrade with nothing reporting it. (c) A gate PERMANENTLY RED AND OPT-IN, so its result
   is habitually ignored. (d) An artifact upload with `if-no-files-found: warn`, which uploads
   nothing and lets the downstream gate "pass" having measured zero. (e) A SKIPPED required
   check, which is not a failing one — a bare `needs:` on an aggregating job would have made
   every shard failure invisible to branch protection.
   The lesson is procedural, not technical: after fixing one, SWEEP for the others. Three of
   these were found only because we went looking (todos #107/#108/#109).

2. **Never accept a dry run as proof of an enforcement boundary.** `git push --dry-run`
   reported success against protected `main` because it never sends the ref update, so the
   server never evaluates protection. Only a REFUSED REAL OPERATION proves a gate — here an
   admin merge rejected with HTTP 405. This nearly produced a signed-off exit criterion on
   evidence that meant nothing.

3. **A blocked API can have two independent causes, and fixing one silently leaves the other.**
   Branch protection 403'd for ~6 releases and was recorded as an org-billing problem. It was
   actually plan + repository VISIBILITY; making the repo public fixed it instantly. Billing
   was a real but separate fault. Re-diagnose from scratch when a workaround has outlived its
   original explanation.

4. **Every timeout must come from an OBSERVED run, never an extrapolation.** Three guessed caps
   (30/75/60) produced three `cancelled` runs that proved nothing; the 75 came from scaling a
   12-core dev host to a 4-core runner. And when the unit of work changes, RE-DERIVE — after
   sharding, the whole-suite 120 left a hung shard burning two hours (todo #112).

5. **In CI, the scaling limit is usually not the one you are optimising.** More xdist workers
   on one box did nothing (4 vCPUs shared with the service containers, a ~1.92x coverage
   multiplier, one contended database). More BOXES worked — 65-82 min to ~13. But then the
   binding constraint moved again: the Free plan's 5-CONCURRENT-JOB cap means past 4 shards the
   pipeline gets SLOWER. "Free" is not "unlimited", and we shipped 6 shards before measuring.

6. **Reproduce load-dependent flakes by DELAY INJECTION, not by re-running.** Wrapping the
   deciding seam in a sleep loaded as an external `-p` plugin cracked three "cannot reproduce"
   flakes in seconds each, and forced the less-common outcome of a legal race. Re-running had
   already failed 0/20 on one of them.

7. **A guard must be RED against the tree that motivated it.** A new guard that is green on the
   pre-fix commit is worse than none, because it advertises coverage it does not have. Keep a
   `<fix>^` worktree and require the guard to name the original victim. Corollary learned the
   hard way here: guards must also cross-check DECLARATIONS OF THE SAME FACT — `SHARDS` and the
   matrix length were two spellings of one number, and the drift is silent in the direction
   that skips tests.

8. **Fixing the flake tail means working the CLASS, not the sightings.** Two consecutive runs
   had 8 failures each with only 3 in common, so "8 failures" was a sighting and not the
   population. Five of six streak attempts died on classes owning no sleep site at all, which
   is why the sleep census was the wrong population to reason from.

## Release steps
- [ ] open a PR from the Close ship-review; the human reviews + merges (the FIRST merge that goes through required checks rather than admin-merge — that is itself the milestone's proof)
- [ ] cut 0.14.0 "release integrity" — CHANGELOG + RELEASES rows
- [ ] tag / publish / deploy, following the new pgvector runbook against staging first  (human-run)
- [ ] archive the milestone; open R7 `enterprise-trust` intake
