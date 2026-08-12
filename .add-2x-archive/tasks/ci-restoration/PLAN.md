# PLAN: Restore a genuinely green CI and make it required on main

slug: ci-restoration · created: 2026-07-25 · stage: production
milestone: release-integrity
autonomy: conservative
phase: done
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: `.github/workflows/ci.yml` runs to completion on a real runner, passes, and is REQUIRED on `main` — so a merge is proven by CI on the merged artifact instead of by an admin override on locally-run evidence.

Grounding — three INDEPENDENT faults, only one of which was known:
1. **No runner is ever assigned.** Every job ends in ~3s with `steps: 0` and `runner_name: ""` — an ACCOUNT-level rejection, not a workflow error. Org `pilotspace` is on the **free** plan and `hydroa` is **private**, so Actions minutes are metered; the signature matches exhausted minutes / no payment method / a $0 spending limit. Actions itself is enabled (`allowed_actions: all`) and both workflows are `active`. Confirming this needs `admin:org` scope the current token lacks — **Tin-only**.
2. **The workflow is stale and would fail even with runners.** `.github/workflows/ci.yml:17` pins `image: postgres:16-alpine`, but since #89 the suite calls `CREATE EXTENSION vector` and `Base.metadata` carries a `Vector(1536)` column. Alpine Postgres has no pgvector → every vector-store / file-search suite fails. `infra/docker-compose.dev.yml`, `infra/docker-compose.e2e.yml`, `infra/docker-compose.prod.yml` and `charts/ai-proxy/values.yaml` all already moved to `pgvector/pgvector:pg16`; **ci.yml is the only holdout**. Invisible because no run has reached step 1 in weeks.
3. **`make ci` and the workflow have drifted.** `Makefile:78` — `ci: lint typecheck allowlist allowlist-node test`. The gateway job runs lint/typecheck/allowlist/test as separate steps and NEVER runs `allowlist-node`, so the node dependency allow-list gate is unenforced in CI.

Verified NOT broken (deliberately out of scope): the dashboard suite is green (190 files / 1780 tests, run 2026-07-25 — todo #55's "13 pre-existing design-token failures" is STALE); every Makefile target the workflow calls exists; both workflows are enabled.

Framings weighed: **fix what the repo controls, hand back only the irreducible account action** (chosen — faults 2 and 3 are code and land now, so the first run after billing is restored is a real signal instead of another red herring; the task delivers value even if billing drags) · *wait for billing then fix the workflow* (rejected — guarantees another wasted red run and blocks the keystone task on an external actor with nothing shipped) · *self-hosted runner to sidestep billing entirely* (rejected as default — trades metered minutes for a runner-security and maintenance burden on a private commercial repo; kept as the documented fallback in §7).

Must:
<must>
  - M1 the Postgres service in `ci.yml` provides pgvector, so `CREATE EXTENSION vector` succeeds and the vector-store/file-search suites can run at all.
  - M2 CI enforces the SAME gate as `make ci` — including `allowlist-node`, currently unenforced. No gate that `make ci` runs locally may be silently absent from CI.
  - M3 a static check proves the CI Postgres image and the compose/chart Postgres images cannot drift apart again — fault 2 is exactly what any future Postgres/pgvector bump re-introduces.
  - M4 `gateway` and `dashboard` are REQUIRED checks on `main` with admins NOT exempt, demonstrated on a real PR — a red check must block merge. (CR v2: `kind-e2e` is EXCLUDED — see the CR note in §3.)
  - M5 admin-merge is retired in the written guidance: a merge requires green required checks; `--admin` is no longer a sanctioned path.
</must>
Reject:
<reject>
  - a CI that goes green because a step was removed or a suite excluded -> "gate_weakened"
  - branch protection configured but not enforcing (admins exempt, or checks listed but not required) -> "protection_not_enforcing"
  - a claimed-green CI with no run showing NON-ZERO step counts -> "unproven_green"
</reject>
After:
<after>
  - `gh pr checks <n>` on a real PR shows `gateway` and `dashboard` with non-zero step counts and green conclusions.
  - `gh api repos/pilotspace/hydroa/branches/main/protection` lists both as required with `enforce_admins: true`.
  - A PR carrying a deliberately failing test cannot be merged without an explicit, logged override.
</after>
Boundary: two external input shapes the checks must speak — (a) the GitHub Actions runner environment (Ubuntu, service containers on mapped ports 5433/6380) vs. the local dev environment (a long-lived shared Postgres/Redis on those same ports, per `[[shared-test-postgres-no-timeouts]]`); (b) the org billing state, READ-ONLY to this task and observable only with `admin:org` scope.
<assumptions>
  ⚠ that fault 1 really is billing/minutes and not another account-level cause — Actions disabled by org policy, a suspended payment method, an OIDC/permissions block, or a runner-group restriction. The 0-step + empty-`runner_name` signature is consistent with ALL of those and I cannot distinguish them without `admin:org`. If wrong: Tin enables billing, the next run still dies at 0 steps, M4 stays unmet and we re-diagnose. Mitigation: get ONE observation (the scope, or a paste of the org Actions billing page) BEFORE proposing a remedy — do not guess at a fix we cannot observe.
</assumptions>

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Contract (freeze the shape)

```
.github/workflows/ci.yml
  services.postgres.image: postgres:16-alpine -> pgvector/pgvector:pg16          (M1)
    (the reference already used by infra/docker-compose.{dev,e2e,prod}.yml
     and charts/ai-proxy/values.yaml since #89)
  gateway job: ADD a step invoking the node dependency allow-list gate           (M2)
    so the workflow's gate set is a SUPERSET of Makefile `ci:` prerequisites

apps/gateway/tests/migrations/test_ci_workflow_parity.py                          (new)
  test_ci_postgres_image_has_pgvector                                            (M1)
  test_ci_postgres_image_matches_compose_and_chart                               (M3)
  test_ci_enforces_every_make_ci_gate                                            (M2)
  pure text parsing of repo files — no network, no docker, no GitHub API

branch protection on `main`                                                       (M4)
  required_status_checks.contexts >= {gateway, dashboard}
  enforce_admins: true
  recorded as an executable command in `.github/branch-protection.sh`, run by an admin

CR v2 (Tin, 2026-07-25) — `kind-e2e` REMOVED from the required set.
  Found at build: kind-e2e triggers only on workflow_dispatch or a PATH-FILTERED
  pull_request (charts/**, infra/kind/**, apps/**, scripts/e2e_kind*.sh, Makefile,
  its own file). A path-filtered workflow never reports a status on a PR that does
  not match, so requiring it would DEADLOCK every such PR at "Expected — waiting
  for status" — it cannot merge at all. Its own header also declares it "heavy +
  opt-in by design ... NOT in the fast ci.yml lane", and at a 45-min budget on a
  2000-min/month free plan, requiring it per-PR would consume the very resource
  that is already blocking CI (fault 1). Rejected alternatives: a skip-reporter job
  that reports the context green when the filter misses (a green that proves
  nothing — adjacent to R:gate_weakened), and dropping the path filter (correct
  coverage, unaffordable minutes). kind-e2e stays opt-in and is run deliberately
  before a release cut; making it affordable-and-required is R7/R8 work, not this
  task's.

docs (M5): CONTRIBUTING.md + CLAUDE.md/.add guidance — merge requires green
  required checks; `--admin` is not a sanctioned path.

OUT OF CONTRACT (Tin-only, tracked not built):
  org Actions billing / spending limit — the runner-assignment fault.
```

Target (measurable): the three §4 parity tests run RED on today's tree and GREEN after the workflow edit; `make ci` passes locally end-to-end (the first honest measurement of that claim in weeks); a real PR shows `gateway` and `dashboard` green with **step counts > 0** (step-count>0 is the discriminator — the current failure mode is exactly 0 steps); `gh api .../branches/main/protection` lists both required with `enforce_admins: true`; a deliberately-failing-test PR is blocked from merge. The last three are gated on the Tin-only blocker and are judged at the gate via `--target-hit partial` if it is unresolved.
Status: FROZEN @ v4 — approved by Tin Dang
Reported: no

### Build-strategy
Scope (may touch): `./../../../CONTRIBUTING.md` · `./../../../CLAUDE.md` · `./../../../AGENTS.md` · `./../../../.clinerules` · `.github/` · `apps/gateway/tests/migrations/`

Regression floor: `make ci` locally, end-to-end — chunk the gateway suite per `[[gateway-suites-xdist-schema-collision]]` if the 12-core box saturates; the dashboard suite is a known-green baseline at 190 files / 1780 tests.
Persona: sre-reliability-engineer

Least-sure flag surfaced at freeze: [spec] — the fault-1 diagnosis. Faults 2 and 3 are directly observed in the files and I am confident in both. Fault 1 is INFERRED from a signature consistent with several distinct account-level causes I cannot tell apart without `admin:org` scope, and the task's headline outcome (M4) is gated on a remedy I cannot verify from here. The honest plan is therefore: ship M1–M3 + M5 now and treat M4 as blocked-pending-Tin rather than pretend a fix.

---

## 4 · TESTS & SCENARIOS — failing-first suite (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_ci_postgres_image_has_pgvector: arrange — parse `.github/workflows/ci.yml`; act — read `jobs.gateway.services.postgres.image`; assert — it is a pgvector-providing image. MUST-FAIL-FIRST: today it is `postgres:16-alpine`, which cannot serve `CREATE EXTENSION vector`. · covers: M1
  - test_ci_postgres_image_matches_compose_and_chart: arrange — read the postgres image from ci.yml, `infra/docker-compose.dev.yml` and `charts/ai-proxy/values.yaml`; assert — all three are the SAME reference. MUST-FAIL-FIRST: ci.yml is alpine while the other two moved to pgvector in #89 — the exact drift that produced fault 2 and that any future bump re-introduces. · covers: M3, R:gate_weakened
  - test_ci_enforces_every_make_ci_gate: arrange — parse the `ci:` target's prerequisite list from the `Makefile`; act — parse the run-steps of ci.yml; assert — every gate named by `make ci` is invoked somewhere in the workflow. MUST-FAIL-FIRST: `allowlist-node` is a `ci:` prerequisite and absent from the workflow. Also the standing guard against R:gate_weakened — deleting a step to force green turns this red. · covers: M2, R:gate_weakened
</test_plan>

CR v4 (2026-07-25) — §4 tests HARDENED after the independent refute, re-frozen so the
tamper baseline re-anchors. The change STRENGTHENS the suite in both directions and
weakens nothing (which would be forbidden): `test_ci_enforces_every_make_ci_gate` now
reads only the `gateway` job's UNCONDITIONAL `run:` steps with comments stripped, so
`if: false`, a gate planted in the `dashboard` job, and a commented-out gate each turn
it red; `test_ci_postgres_image_has_pgvector` asserts equality against a pinned
`PGVECTOR_IMAGE` instead of a substring, so `evil/pgvector-but-not-really:latest` no
longer satisfies it. All four attacks were RUN as proof — see §6.

Rigor: M1/M2/M3 are statically checkable and get real red tests — they are the faults that stay invisible until a runner exists. M4 and M5 are NOT unit-testable from inside the repo; they are ACCEPTANCE CHECKS verified with recorded evidence at §6. This split is deliberate: do NOT write a test that asserts branch protection from a repo file — the whole point of M4 is that it is true on GitHub, not in YAML we wrote. R:protection_not_enforcing and R:unproven_green are likewise acceptance-checked, not unit-tested.

Acceptance checks (evidence recorded at §6, red today):
- A4 (M4, R:protection_not_enforcing, R:unproven_green) — a PR URL + `gh pr checks` output showing 3 green checks with step counts > 0; `gh api .../branches/main/protection` output listing all three required with `enforce_admins: true`; evidence that a red check blocks merge. **BLOCKED-PENDING-TIN** on the runner-assignment fault; if unresolved at gate time this task gates `RISK-ACCEPTED` on M4 with the blocker named and an owner — never a silent skip.
- A5 (M5) — the docs diff retiring `--admin`.

Tests live in: `apps/gateway/tests/migrations/` — the repo's existing home for cross-manifest drift gates (same shape as `EXPECTED_TABLES`, per `[[add-cross-manifest-table-drift]]`). MUST run red before Build.

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: as planned for M1–M3 (workflow edit + three static parity tests, red→green). M5 landed as a NEW root `CONTRIBUTING.md` — the repo had none, and root is where GitHub surfaces it in the PR UI, exactly where the admin-merge temptation occurs. M4's `gh api` call was made executable and idempotent as `.github/branch-protection.sh` rather than left as prose, so an admin runs one command instead of transcribing JSON. Two deviations forced by findings:
  * CR v2 (Tin-approved) narrowed the required set — see §3.
  * The §3 Regression floor (`make ci`) turned out to be RED BEFORE this build — see the §6 evidence. Not repaired here: `.add/dependencies.allowlist` and `apps/gateway/src/` are outside the frozen Scope, and the repair is `lint-type-debt-sweep`'s whole charter. Widening scope to absorb it would have been the wrong call twice over (scope creep on an architecture-sensitivity task, and a sibling task left with no contract).
Code lives in: `.github/workflows/ci.yml` · `.github/branch-protection.sh` · `apps/gateway/tests/migrations/test_ci_workflow_parity.py` · `CONTRIBUTING.md`
Spawn (multi-agent): build/verify subagent spawns default `isolation: worktree`; cross-agent advisor — spawn `add-advisor` for the freeze `--cross` and the §6 refute-read.
Constraints: do NOT change any test or the frozen §3 contract; stay inside §3 Scope; **never make CI green by removing a step or excluding a suite** (R:gate_weakened); no new dependencies — parse YAML with the stdlib/`pyyaml` already in the dev deps.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] the §4 red suite passes — `tests/migrations` **30 passed** incl. the 3 new parity tests; each was verified RED first with the right error (`postgres:16-alpine` cannot serve `CREATE EXTENSION vector` · image drift `{ci: alpine, compose: pgvector, chart: pgvector}` · `make ci` runs `['allowlist-node']` the workflow never invokes)
- [ ] **§3 Regression floor NOT met — `make ci` is RED, pre-existing.** Verified by stashing the whole change-set and re-running: identical counts either way.
      · `lint` 37 ruff errors (13 E501, 6 I001, 6 RUF100, 3 S608, …; 17 auto-fixable)
      · `typecheck` 3 pyright errors
      · `allowlist` 4 dependencies absent from `.add/dependencies.allowlist` — `dnspython`, `pgvector`, `pytest-rerunfailures`, `pytest-xdist`
      · `allowlist-node` OK (42 packages clean) — the gate this task newly enforces in CI passes
      · `test` INCONCLUSIVE — the full suite at `-n 6 --dist loadscope` HUNG at 99% (64 min, all six workers exited, controller at 0.0% CPU, no summary line ever printed). Progress output showed 25 F + 19 E of ~4500 before the hang. NOT attributed: the run violated `[[gateway-suites-xdist-schema-collision]]` (three suites share a schema and must run serially) on a shared timeout-less `:5433`, so an unknown share is the known flake class. Attributing these is `suite-stability`'s charter; the hang is itself evidence for its exit criterion.
- [x] coverage did not decrease — additive only (one new test module, no source deleted)
- [x] no test or contract was altered during build — the §3 contract moved twice by explicit CR (v2 kind-e2e, Tin-approved; v3 scope-token spelling), both re-frozen through `add.py freeze`, never edited under a freeze
- [x] the green was EARNED — no step removed, no suite excluded, no check downgraded. This build ADDS a gate (`allowlist-node`) and adds a test that goes RED if any `make ci` gate is ever dropped from the workflow.
- [ ] acceptance checks — A5 EVIDENCED (new root `CONTRIBUTING.md`; `grep -rn -- "--admin"` over the repo returns nothing, so no sanctioned-override text remains anywhere). **A4 NOT EVIDENCED** — see the blockers below.
- [x] no exposed secrets, injection openings, or unexpected dependencies — `.github/branch-protection.sh` interpolates only `REPO`/`BRANCH` (operator-set, defaulted) and passes its JSON body via a QUOTED heredoc, so no `${{ }}` expansion and no untrusted-input path; the workflow edit adds no `${{ github.event.* }}` interpolation. The new test imports only `re`/`pathlib`/`yaml` (already a dev dep).
- [x] layering — the parity tests sit in `tests/migrations/`, the repo's established home for cross-manifest drift gates (`[[add-cross-manifest-table-drift]]`)
- [ ] a person reviewed and approved the change — **PENDING (this gate)**

### Blockers on M4 — two, and only one was known at freeze
1. **Account-level runner fault (Tin-only, known at freeze).** No run can produce a green check while jobs terminate at 0 steps. Needs `admin:org` to even diagnose, which the session token lacks.
2. **`make ci` debt (FOUND AT BUILD, not anticipated).** Even with runners restored, the `gateway` job would fail at the `lint` step. So restoring runners is NECESSARY BUT NOT SUFFICIENT — M4 has a hard prerequisite on `lint-type-debt-sweep`. Recorded as todos #68/#69 and written into the milestone exit criterion.

### Control-gap note (recorded, not waved through)
The `allowlist` gate is a SUPPLY-CHAIN control, and it has been red since #89 merged — four dependencies entered the tree without the justified PR the gate demands, `pgvector` among them. Presented to Tin as a possible HARD-STOP; Tin chose to allowlist all four WITH written justification, owned by `lint-type-debt-sweep`. Recording it here so the bypass is visible in the audit trail rather than absorbed silently into a lint sweep: the finding is a lapsed control with a named owner and a next task, not a clean bill of health.

### Refute-read verdict — the earned-green check
Verdict: EARNED, for the three Musts this task actually closed (M1/M2/M3) plus M5 — with the honest limit that M4 is UNPROVEN, not met.
By: self · adversarially checked: (a) that the parity tests fail for the RIGHT reason, by reading each red message rather than the exit code — one initially failed on a `KeyError` from the wrong chart path (`values.postgres` vs `values.datastores.postgres`), i.e. red for a bug in the test, and was fixed before it counted; (b) that the `make ci` reds are genuinely PRE-EXISTING and not self-inflicted, by `git stash -u` + re-running lint and allowlist and comparing counts (37/4 both ways); (c) that `test_ci_enforces_every_make_ci_gate` cannot be satisfied by deleting a step — it parses the Makefile as the source of truth, so removing the workflow step turns it red; (d) that `kind-e2e` genuinely cannot be a required check, by reading its `on:` block rather than assuming — it is `workflow_dispatch` + path-filtered `pull_request`.
**Independent adversarial refute — `add-advisor` a6fd3f4e03c6d739c (sonnet), default position "refuted=true".** All four claims **NOT-REFUTED** (C1 red-for-right-reason · C2 `make ci` reds pre-existing · C3 gate-parity test cannot be beaten by deleting a step · C4 kind-e2e genuinely unrequireable). Working tree verified byte-identical before/after its experiments.

It filed TWO CONFIRMED findings against MY OWN TESTS — both the "green that proves nothing" class this task exists to prevent:
  1. `test_ci_enforces_every_make_ci_gate` passed even when the gate step was neutered with `if: false`, or planted as an unrelated command in the WRONG job (`dashboard`, a different working-directory and toolchain). It string-matched the whole workflow.
  2. `test_ci_postgres_image_has_pgvector` passed on a garbage image via bare substring — `evil/pgvector-but-not-really:latest` satisfied it.

BOTH FIXED, then the refuter's attacks were re-run as executable proof (not reasoned about):
  · `if: false` on the gate step -> 1 failed  · gate moved to the `dashboard` job -> 1 failed
  · gate reduced to a comment (`run: echo skipping # make allowlist-node`) -> 1 failed
  · `image: evil/pgvector-but-not-really:latest` -> 2 failed (image + drift)
  · restored -> 3 passed
The hardening: `_gateway_unconditional_run_steps()` reads only the `gateway` job, only steps with NO `if:` key, only `run:` bodies, and requires the gate to be a step's own command with comments stripped; the image check asserts equality against a pinned `PGVECTOR_IMAGE` instead of a substring. The new module is itself ruff- and pyright-clean, so it adds nothing to the pre-existing debt.

Verdict after hardening: EARNED for M1/M2/M3/M5. M4 remains UNPROVEN — not met, not waived.

### GATE RECORD
Reported: <yes | no>
Outcome: RISK-ACCEPTED
If RISK-ACCEPTED -> owner: Tin Dang · ticket: todos #68/#69 + release-integrity MILESTONE.md exit criteria · expires: 2026-08-15
Reviewed by: Tin Dang · date: 2026-07-25

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
- [AI] specify — chose **fix what the repo controls, hand back only the irreducible account action**; rejected *wait for billing then fix the workflow* (rejected — guarantees another wasted red run and blocks the keystone task on an external actor with nothing shipped) · *self-hosted runner to sidestep billing entirely* (rejected as default — trades metered minutes for a runner-security and maintenance burden on a private commercial repo; kept as the documented fallback in §7).
- [human] freeze — froze §3 @ v4 (approved by Tin Dang)
- [AI] build — strategy used: as planned for M1–M3 (workflow edit + three static parity tests, red→green). M5 landed as a NEW root `CONTRIBUTING.md` — the repo had none, and root is where GitHub surfaces it in the PR UI, exactly where the admin-merge temptation occurs. M4's `gh api` call was made executable and idempotent as `.github/branch-protection.sh` rather than left as prose, so an admin runs one command instead of transcribing JSON. Two deviations forced by findings: * CR v2 (Tin-approved) narrowed the required set — see §3. * The §3 Regression floor (`make ci`) turned out to be RED BEFORE this build — see the §6 evidence. Not repaired here: `.add/dependencies.allowlist` and `apps/gateway/src/` are outside the frozen Scope, and the repair is `lint-type-debt-sweep`'s whole charter. Widening scope to absorb it would have been the wrong call twice over (scope creep on an architecture-sensitivity task, and a sibling task left with no contract).
- [human] verify — gate RISK-ACCEPTED (reviewed by Tin Dang)

### Spec delta
- `[SPEC · open]` A self-hosted runner is the documented fallback if org billing is not the chosen remedy — it removes metered minutes entirely for a private repo. Rejected as the default here (runner-security + maintenance burden on a commercial repo), but re-weigh it if minutes cost becomes material at R7/R8 volume.
- `[SPEC · open]` Todo #55 ("13 pre-existing dashboard design-token failures") is STALE — the dashboard suite is green at 190 files / 1780 tests as of 2026-07-25. Close it.
- `[SPEC · open]` M4's evidence has a hard prerequisite on `lint-type-debt-sweep`: `make ci` is red before any of this work (37 ruff · 3 pyright · 4 unallowlisted deps). Restoring runners is necessary but NOT sufficient. Todos #68/#69; written into the milestone exit criteria.
- `[SPEC · open]` The `allowlist` supply-chain gate has been RED since #89 — `dnspython`, `pgvector`, `pytest-rerunfailures`, `pytest-xdist` entered without the justified PR it demands. Tin chose allowlist-with-justification over HARD-STOP; owned by `lint-type-debt-sweep`. Recorded as a LAPSED CONTROL, not a clean bill of health.
- `[SPEC · open]` `kind-e2e` is unrequireable while it is path-filtered. If per-PR edge-stack proof is wanted at R7/R8, that needs a merge queue or a restructured trigger — not a skip-reporter, which would be a green that proves nothing.
- `[SPEC · open]` The full gateway suite HUNG at 99% for 64 min at `-n 6 --dist loadscope` (all six workers exited, controller at 0.0% CPU, no summary). Direct evidence for `suite-stability`'s exit criterion; 25 F + 19 E were visible but are UNATTRIBUTED because the run violated the serial-suite rule.
- `[SPEC · open]` The ADD scope grammar has no token that names a PROJECT-ROOT FILE — bare names resolve as siblings of the previous token's dir, and only tokens containing "/" are root-relative, so root files need `./../../../NAME`. Worth an engine fix or a documented idiom.

### Competency deltas
- `[ADD · open]` A CI that has been red for weeks ROTS silently: three independent faults had accumulated here — a stale service image that #89 invalidated, an unenforced `make ci` gate, and the account-level block — and only the last was known. When CI is down, every subsequent merge also skipped CI's *config* review; re-diagnose the whole pipeline before declaring it fixed, and treat every gate CI stopped enforcing as PRESUMED RED. (evidence: ci-restoration grounding, 2026-07-25)
- `[TDD · open]` A static test asserting a CONFIG file's shape is vacuous by default. Two of three parity tests here passed under attack until an independent refuter probed them — a gate neutered with `if: false` or planted in the wrong job satisfied a whole-file string match, and `evil/pgvector-but-not-really:latest` satisfied a substring check. Scope the parse to the section that would actually execute, exclude conditional steps, strip comments, assert EQUALITY against a pinned literal — then RUN the attacks as proof. (evidence: refute a6fd3f4e03c6d739c, 2 confirmed findings, both fixed and re-attacked)
- `[ADD · open]` Hardening a frozen test mid-BUILD trips `build_tampered` exactly as weakening one would — the tripwire cannot tell the direction. Re-cross tests→build (`phase direction` → `freeze`) BEFORE gating, or the honest strengthening burns a heal attempt. Confirms `[[add-tamper-tripwire-ordering]]`; second occurrence. (evidence: ci-restoration gate attempt 1 of 3, 2026-07-25)
