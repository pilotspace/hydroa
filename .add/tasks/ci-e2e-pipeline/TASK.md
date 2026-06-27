# TASK: Wire kind-up + the full e2e (API + UI) into a CI workflow + author the real-cloud (values-prod) deploy runbook (the HARD-STOP boundary)

slug: ci-e2e-pipeline · created: 2026-06-27 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `.github/workflows/ci.yml` — the EXISTING CI: two jobs, `gateway` (postgres+redis service containers; steps make install/lint/typecheck/allowlist/test + a migrate→migrate-check parity gate on a fresh `gateway_parity` DB) and `dashboard` (npm ci → vitest → next build). `on: push[main] + pull_request`, `permissions: contents: read`, per-job `timeout-minutes: 10`. NO kind/e2e job today — t10 ADDS a third job (separate workflow file to keep concerns isolated and the heavy job independently skippable).
- `.github/workflows/kind-e2e.yml` — **NEW** (the CI deliverable): a `kind-e2e` job that installs Docker/kind/helm + Chromium on the runner, runs `make kind-up` then `make kind-e2e` (API) + `make kind-e2e-ui` (browser), always tears down (`make kind-down`) in an `if: always()` step.
- `Makefile:kind-up` (kind-preflight→create cluster→kind-load both images→mint TLS→apply upstream-stub+edge-nodeport→`helm upgrade --install -f values-kind.yaml`→kind-wait bounded rollout) · `Makefile:kind-e2e` (`./scripts/e2e_kind.sh`) · `Makefile:kind-e2e-ui` (`./scripts/e2e_kind_ui.sh`) · `Makefile:kind-down` (idempotent delete) — the targets CI invokes; reused VERBATIM (no edit needed, only invoked). Optional: a `ci-e2e` aggregate target as the local mirror.
- `scripts/e2e_kind.sh` — API e2e harness (up-idempotent→seed pricing via kubectl-exec-psql→pytest `tests/kind_e2e`). `scripts/e2e_kind_ui.sh` — browser harness (kind-up→probe `${EDGE}/login`==200→`npx playwright install chromium`→`npm run test:kind`). Both already `--no-up`/`--down` aware → CI calls `kind-up` once then each script with `--no-up`.
- `charts/ai-proxy/values-prod.yaml` — the prod overlay (566B): `image.tag` swap + `gateway.env.databaseUrl/redisUrl` → `.internal` managed endpoints + `gateway.jwtSecret.existingSecret`. This IS the "values-prod swap" the runbook documents; layered `-f values.yaml -f values-prod.yaml`. NOT edited here — the runbook references it.
- `charts/ai-proxy/values-kind.yaml` — the kind overlay CI installs with (enc-key throwaway Fernet, objectStore→MinIO on, NP disabled because kindnet enforces).
- `infra/kind/cluster.yaml` — single control-plane, host:8443→nodePort 30443→envoy; `infra/kind/{upstream-stub,edge-nodeport}.yaml` applied by kind-up.

Context (working folder):
- `docs/runbooks/backup-rollback.md` — the EXISTING runbook (markdown, `## Section` headings, fenced bash blocks, dev-vs-prod split). The **style/structure convention** the NEW `docs/runbooks/cloud-deploy.md` must match.
- `.add/milestones/v53/MILESTONE.md` — exit criterion line 50 ("the full kind-up + e2e (API + UI) runs in CI on a runner, AND a deploy runbook documents the values-prod swap for a real-cloud apply") + Release-steps lines 71–74 (line 73 = the human-run HARD-STOP apply).
- Memory carryover (HARD-STOP, must appear in the runbook): kindnet ENFORCES NetworkPolicy so the kind overlay DISABLES the NPs — the prod envoy/dashboard NPs are therefore UN-validated and likely BROKEN under a real NP-enforcing CNI → the runbook MUST list "validate + fix NetworkPolicies under enforcement" as a pre-apply gate (see [[v53-kind-envoy-three-bugs]]). Plus open SPEC deltas to note: boot fail-fast on empty enc-key; aioboto3→allowlist.
- Memory carryover (operational reality): the org's **GitHub Actions billing is blocked** — every recent CI run is a 0-step red. The workflow is authored + committed but cannot EXECUTE on a runner until billing is restored. This is a documented constraint (the green is structural/local: yamllint + the local `make ci-e2e`), NOT a task failure.

Honors (patterns / conventions):
- CLAUDE.md DESIGN-FOR-FAILURE: the CI job must bound every wait (reuse kind-up's `KIND_WAIT_TIMEOUT` + a job `timeout-minutes`) and tear down on failure (`if: always()` → `make kind-down`) so a hung cluster never wedges the runner.
- CLAUDE.md SECRETS-NEVER-IN-CHART / MILESTONE "SECRETS NEVER IN THE CHART": the runbook references k8s Secrets populated out-of-band, never inlines a real secret; the kind throwaway Fernet stays publicly-known-and-fake.
- MILESTONE "CLOUD-READY, KIND-VALIDATED" + "E2E THROUGH THE EDGE": CI proves the stack on kind only; the real-cloud apply stays a runbook (the single HARD-STOP boundary, no cloud creds executed here).
- Existing ci.yml shape: pinned `actions/*@v4`, `permissions: contents: read`, explicit `timeout-minutes` — the new workflow mirrors this.

Anchors the contract cites:
- `.github/workflows/kind-e2e.yml` (NEW) — the kind-in-CI workflow.
- `docs/runbooks/cloud-deploy.md` (NEW) — the real-cloud deploy runbook (values-prod swap + the NP-under-enforcement HARD-STOP pre-apply gate).
- `Makefile:ci-e2e` (NEW, optional aggregate) + the reused `kind-up`/`kind-e2e`/`kind-e2e-ui`/`kind-down` targets.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: kind-in-CI e2e pipeline + the real-cloud (values-prod) deploy runbook — the milestone's last task: wire the whole kind stack + both e2e suites (API + UI) into a CI workflow, mirror it with a locally-runnable target, and document the one HARD-STOP boundary (the real-cloud apply) as a runbook.

Framings weighed: NEW dedicated workflow + local mirror target + runbook (chosen) · extend the existing `ci.yml` with a third job (rejected — couples a ~20-min Docker/kind job to the fast lint/test lane; a separate workflow keeps `ci.yml` fast and lets the heavy job be triggered/skipped independently) · runbook-only, no workflow (rejected — Tin chose "scaffold + start t10": author BOTH the CI workflow and the runbook)

Must:
<must>
  - M1 (CI workflow): a NEW `.github/workflows/kind-e2e.yml` defines a job that, on a runner, installs Docker+kind+helm+Chromium, runs `make kind-up`, then runs the API e2e (`make kind-e2e`) AND the browser e2e (`make kind-e2e-ui`), and tears the cluster down unconditionally (`if: always()` → `make kind-down`).
  - M2 (well-formed + additive): the workflow parses as valid YAML, pins `actions/*` by major version, sets `permissions: contents: read` and a bounded `timeout-minutes`, and does NOT remove or weaken the existing `ci.yml` `gateway`/`dashboard` jobs (purely additive).
  - M3 (local mirror = the real proof surface): a NEW `make ci-e2e` aggregate target runs the SAME kind-up + both e2e suites locally, so the pipeline is provable without a hosted runner (Actions billing is blocked org-wide). This local green is THIS task's verify evidence.
  - M4 (deploy runbook): a NEW `docs/runbooks/cloud-deploy.md` (matching the existing runbook style) documents the real-cloud apply end-to-end: prerequisites (cluster · kubeconfig · registry · DNS · TLS · secrets), the values-prod swap (`helm upgrade --install -f values.yaml -f values-prod.yaml`), out-of-band Secret population, post-apply verification, and rollback (pointer to `backup-rollback.md`).
  - M5 (HARD-STOP boundary documented): the runbook explicitly marks the real-cloud apply as HUMAN-RUN, never CI-executed, and lists the carryover **NetworkPolicy-under-enforcement** item as a REQUIRED pre-apply gate (the prod envoy/dashboard NPs are kind-disabled hence un-validated → must be validated+fixed under a real enforcing CNI first) plus the open boot-fail-fast-on-empty-enc-key item.
</must>
Reject:
<reject>
  - R1 a real or new secret VALUE committed in the workflow or the runbook -> "secret_inlined" (use `${{ secrets.* }}` / k8s Secret refs only; the kind throwaway Fernet is the sole pre-existing fake and is unchanged)
  - R2 the change edits/weakens the existing `ci.yml` gateway or dashboard jobs -> "existing_ci_regressed" (t10 is additive)
  - R3 the runbook presents the cloud apply as automated / CI-run, or omits the NetworkPolicy-under-enforcement pre-apply gate -> "hardstop_boundary_unmarked"
</reject>
After:
<after>
  - `make ci-e2e` runs green locally: kind-up → API e2e (4 passed) → UI e2e (3 passed).
  - `.github/workflows/kind-e2e.yml` exists, parses, lints, and invokes the three make targets with an always-teardown.
  - `docs/runbooks/cloud-deploy.md` exists with the prerequisites · values-prod swap · secrets · verify · rollback sections AND the NetworkPolicy HARD-STOP pre-apply gate.
  - the existing `.github/workflows/ci.yml` is byte-unchanged (gateway + dashboard jobs intact).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ A hosted GitHub Actions runner can actually run the kind stack (Docker present, ~10 pods fit the runner's CPU/RAM/disk + time budget) — LOWEST confidence because Actions billing is BLOCKED org-wide, so the workflow CANNOT be empirically observed green on a runner during this task; the proof surface is therefore the LOCAL `make ci-e2e`, and the workflow is authored best-effort + structurally validated. If wrong: the committed workflow would fail/timeout on a real runner once billing returns; cost = a follow-up tune (runner size, disk-free, timeout), NOT a re-architecture — the local pipeline and the runbook still stand.
  - [ ] Chromium on the runner needs `npx playwright install --with-deps chromium` (system libs), not the bare `install chromium` the local script uses — confirm the workflow uses `--with-deps`. If wrong: browser launch fails on a fresh runner.
  - [ ] the e2e scripts' `--no-up` path lets CI call `make kind-up` ONCE then each script without re-upping (avoids a double bring-up) — confirmed in §0 (both scripts honor `--no-up`); if wrong: redundant ~3-min bring-up, not a failure.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: CI workflow drives the full kind e2e (M1)
  Given the new .github/workflows/kind-e2e.yml
  When its kind-e2e job is read
  Then it has steps that run `make kind-up`, `make kind-e2e`, and `make kind-e2e-ui` in that order
  And a final teardown step runs `make kind-down` under `if: always()`

Scenario: workflow is well-formed and pins its dependencies (M2)
  Given the new workflow file
  When it is parsed as YAML
  Then it parses without error, sets `permissions: contents: read`, declares a bounded `timeout-minutes`, and every `uses:` action is version-pinned (`@vN` or a SHA)

Scenario: the new workflow is additive — existing CI is untouched (M2)
  Given the existing .github/workflows/ci.yml
  When the repo is inspected after this task
  Then ci.yml still defines both the `gateway` and `dashboard` jobs
  And no existing ci.yml step was removed or weakened

Scenario: a local target mirrors the CI pipeline and runs green (M3)
  Given a developer on a machine with Docker + kind + helm
  When they run `make ci-e2e`
  Then the kind stack comes up and both e2e suites pass (API 4 + UI 3) without a hosted runner

Scenario: the deploy runbook documents the real-cloud apply (M4)
  Given the new docs/runbooks/cloud-deploy.md
  When it is read
  Then it covers prerequisites, the values-prod swap (`-f values.yaml -f values-prod.yaml` via `helm upgrade --install`), out-of-band Secret population, post-apply verification, and rollback (pointer to backup-rollback.md)

Scenario: the runbook marks the HARD-STOP boundary and the NP pre-apply gate (M5)
  Given the deploy runbook
  When the boundary section is read
  Then it states the real-cloud apply is human-run and never CI-executed
  And it lists "validate + fix NetworkPolicies under enforcement" as a REQUIRED pre-apply gate (prod NPs are kind-disabled) plus the boot-fail-fast-on-empty-enc-key open item

Scenario: no secret value is inlined (R1)
  Given the new workflow and runbook
  When they are scanned for secret material
  Then no real or new secret VALUE appears — only `${{ secrets.* }}` placeholders and k8s Secret references
  And the sole pre-existing fake (the kind throwaway Fernet in values-kind.yaml) is unchanged

Scenario: existing CI must not be regressed (R2)
  Given a change that would remove or alter a ci.yml gateway/dashboard step
  When this task is verified
  Then it is rejected as "existing_ci_regressed"
  And ci.yml's two jobs remain intact

Scenario: the cloud apply must not be presented as automated (R3)
  Given the deploy runbook
  When it describes the apply
  Then it does not present the apply as CI-run, and it does not omit the NetworkPolicy-under-enforcement pre-apply gate
  And the milestone's HARD-STOP boundary stays explicit (human-run)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This is a CI-config + documentation task; the "contract" is the SHAPE of three artifacts (a
workflow, a make target, a runbook) — not an HTTP/DB surface. Frozen shapes:

```
ARTIFACT 1 — .github/workflows/kind-e2e.yml   (NEW)
  name: kind-e2e
  on: { workflow_dispatch: {}, pull_request: { paths: [charts/**, infra/kind/**, scripts/e2e_kind*.sh,
        apps/**, Makefile, .github/workflows/kind-e2e.yml] } }   # opt-in + path-scoped (heavy job)
  permissions: { contents: read }
  jobs.kind-e2e:
    runs-on: ubuntu-latest
    timeout-minutes: 45                       # bounded — design-for-failure
    steps (in order, observable):
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5  with: { python-version: "3.12" }   # gateway pytest (e2e harness)
      - uses: actions/setup-node@v4  with: { node-version: "22" }       # dashboard playwright
      - install kind binary (pinned ~v0.32) + helm (azure/setup-helm@v4)  # binaries only — NOT a cluster
      - run: cd apps/dashboard && npx playwright install --with-deps chromium   # runner needs system libs
      - run: make ci-e2e                       # = kind-up (build+load+install+wait) → API e2e → UI e2e
      - if: always() -> run: make kind-down    # unconditional teardown
  INVARIANTS: valid YAML · contents:read · bounded timeout · every `uses:` pinned `@vN`/SHA ·
              references `make ci-e2e` (which fans out to kind-up + both suites) + `make kind-down` ·
              always-teardown present · installs kind/helm BINARIES (kind-up owns cluster creation,
              so its cluster.yaml extraPortMappings host:8443 survive — do NOT let an action pre-create one)

ARTIFACT 2 — Makefile : ci-e2e   (NEW target, additive)
  ci-e2e: kind-up           # then run both suites against the already-up cluster
      ./scripts/e2e_kind.sh --no-up
      ./scripts/e2e_kind_ui.sh --no-up
  .PHONY += ci-e2e
  NOTE: the e2e scripts accept --no-up/--down already (§0). CI passes --no-up via ARGS so a
        single kind-up serves both suites. Existing kind-* targets are reused VERBATIM (unedited).

ARTIFACT 3 — docs/runbooks/cloud-deploy.md   (NEW, matches backup-rollback.md style)
  REQUIRED sections (## headings), each observable by a content check:
    ## Scope & the HARD-STOP boundary  -> states: real-cloud apply is HUMAN-RUN, never CI-executed
    ## Prerequisites                   -> cluster · kubeconfig · image registry · DNS · TLS · secrets
    ## Pre-apply gates (MUST pass first)
        - validate + fix NetworkPolicies under a real enforcing CNI (prod NPs are kind-DISABLED,
          hence UN-validated) — REQUIRED before apply   [the carryover HARD-STOP]
        - boot fail-fast on empty provider-key encryption key (open SPEC item)
    ## Populate secrets (out of band)  -> `kubectl create secret ...` from a vault; NO value in git
    ## Apply (values-prod swap)        -> `helm upgrade --install ai-proxy charts/ai-proxy \
                                            -f charts/ai-proxy/values.yaml -f charts/ai-proxy/values-prod.yaml`
    ## Verify the apply                -> rollout status + an edge smoke against the real host
    ## Rollback                        -> pointer to docs/runbooks/backup-rollback.md (helm rollback + alembic)
  INVARIANTS: no secret VALUE inlined · the apply is marked human-run/non-CI · the NP pre-apply
              gate is present · references values-prod.yaml + `helm upgrade --install`

REJECTIONS (verify-time, asserted by tests):
  secret_inlined        -> a new/real secret value appears in the workflow or runbook
  existing_ci_regressed -> ci.yml no longer defines BOTH gateway and dashboard jobs
  hardstop_boundary_unmarked -> runbook omits the human-run mark OR the NP pre-apply gate
Scope (files touched): .github/workflows/kind-e2e.yml (new) · Makefile (add ci-e2e) ·
  docs/runbooks/cloud-deploy.md (new) · tests/ci_pipeline/ (new, structural assertions).
  NOT touched: ci.yml, the kind-* targets, any chart/script (reused as-is).
```

Least-sure flag surfaced at freeze: [contract] success = a structurally-valid + pinned + bounded +
always-teardown workflow + a LOCALLY-green `make ci-e2e` + a complete runbook — NOT "observed green
on a hosted runner" (Actions billing is blocked org-wide, so the runner path is authored best-effort
+ cannot be empirically exercised this task). If the runner later needs tuning (size/disk/timeout)
that is a follow-up, not a re-architecture. Tin approved this framing.

Status: FROZEN @ v1 — approved by Tin Dang 2026-06-27
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: structural — every contract INVARIANT + every REJECTION has an asserting test (no % gate; these are config/doc-structure tests, like `tests/helm/`). The LIVE `make ci-e2e` run is the verify-phase runtime evidence (heavy, not in the default suite — mirrors how t7–t9's kind e2e stays out of the default run).
Plan (one test per scenario, asserting observable structure not internals — pytest parsing YAML/text):
<test_plan>
  - test_workflow_runs_full_kind_e2e (M1): parse kind-e2e.yml / assert the kind-e2e job's run-steps reference `make ci-e2e` (or kind-up+kind-e2e+kind-e2e-ui) AND an `if: always()` step runs `make kind-down`
  - test_workflow_is_wellformed_and_pinned (M2): assert YAML parses · `permissions.contents == read` · `timeout-minutes` is an int>0 · every `uses:` value matches `@v\d+` or `@<40-hex sha>`
  - test_existing_ci_unchanged (M2/R2): assert ci.yml still defines jobs `gateway` AND `dashboard` (additive guard — anchors so the suite can't pass vacuously)
  - test_ci_e2e_target_exists (M3): assert the Makefile declares a `ci-e2e:` target AND lists it in `.PHONY`, and the recipe invokes both e2e scripts (or kind-e2e/kind-e2e-ui)
  - test_runbook_has_required_sections (M4): assert cloud-deploy.md contains the Prerequisites · values-prod swap (`helm upgrade --install` + `values-prod.yaml`) · secrets-out-of-band · Verify · Rollback(→backup-rollback.md) sections
  - test_runbook_marks_hardstop_and_np_gate (M5/R3): assert the runbook says the apply is human-run/non-CI AND names the NetworkPolicy-under-enforcement pre-apply gate AND the enc-key fail-fast item
  - test_no_secret_inlined (R1): scan kind-e2e.yml + cloud-deploy.md → no high-entropy/Fernet-shaped/`PASSWORD=`-style literal; only `${{ secrets.* }}` / k8s Secret refs allowed
</test_plan>

Tests live in: `tests/ci_pipeline/` · MUST run red (files absent) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `.github/workflows/kind-e2e.yml` `Makefile` `docs/runbooks/cloud-deploy.md` `tests/ci_pipeline/`
Strategy (ordered batches): 1. red structural tests in `tests/ci_pipeline/` (parse the to-be-written artifacts → fail on absence). 2. add the `ci-e2e` Makefile target (additive; reuse the e2e scripts with `--no-up`). 3. author `.github/workflows/kind-e2e.yml` (binaries-only kind/helm, `make ci-e2e`, always-teardown). 4. author `docs/runbooks/cloud-deploy.md` (all required sections + the NP HARD-STOP pre-apply gate). 5. structural suite green; then the LIVE `make ci-e2e` run as verify evidence.
Safety rule (feature-specific): reuse the existing `kind-*` targets + e2e scripts VERBATIM (no edit); ci.yml is byte-untouched; NO secret value committed (kind throwaway Fernet is pre-existing + unchanged).
Code lives in: `.github/workflows/`, `Makefile`, `docs/runbooks/`, `tests/ci_pipeline/`
Constraints: do NOT change any test or the contract; allow-list packages only (pyyaml already used by tests/helm — no new dep); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `tests/ci_pipeline` 7 passed; live `make ci-e2e`: kind Ready → API e2e 9 passed (4 core + 5 platform) → UI e2e 3 passed → exit 0
- [x] coverage did not decrease — structural config/doc tests, no coverage gate (like `tests/helm/`); no production code touched
- [x] no test or contract was altered during build — §3 FROZEN @ v1 intact; the only build-side test-driven change was a Makefile `.PHONY` placement to satisfy the frozen `test_ci_e2e_target_exists` (NOT a test edit)
- [x] the green was EARNED, not gamed — adversarial refute-read (general-purpose subagent): VERDICT = EARNED-GREEN, no HARD-STOP, confidence 0.87. NO injection vector, NO secret leak, ci.yml byte-unchanged, runbook not hollow. Findings were all test-EXPRESSIVENESS gaps (artifact provably correct): [MED] `test_existing_ci_unchanged` checked jobs-exist not byte-equality → **HARDENED NOW (Tin gate-direction): the test now byte-compares `_read(CI_WF)` against `git show HEAD:.github/workflows/ci.yml`, catching ANY edit, not just a job removal — 7/7 still green**; [LOW] `@vN` not SHA-pinned (per contract/ci.yml style → §7 delta); [LOW] secret regex covers only Fernet-shape, no secrets of any shape present (→ §7 delta)
- [x] concurrency / timing of the risky operation is safe — the workflow bounds the cluster (timeout-minutes 45 + kind-up's rollout waits) and tears down unconditionally (`if: always()`); no shared-state concurrency
- [x] no exposed secrets, injection openings, or unexpected dependencies — `test_no_secret_inlined` green; NO `${{ github.event.* }}` untrusted input in any `run:` (no injection vector); no new dependency (pyyaml already used by tests/helm)
- [x] layering & dependencies follow CONVENTIONS.md — additive only; reuses the existing kind-* targets + e2e scripts verbatim; ci.yml byte-unchanged
- [x] a person reviewed and approved the change — Tin gate (security-adjacent surface: CI workflow + secrets-handling runbook → escalated, not auto-PASS): "PASS, but harden the MED now" (AskUserQuestion) → MED hardened, then PASS

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] the 7 structural tests in `tests/ci_pipeline/` all pass — `uv run pytest tests/ci_pipeline` → 7 passed (was 6-red/1-anchor before build)
- [x] `make ci-e2e` runs the WHOLE pipeline green LIVE — terminal run: kind-up "✅ kind stack Ready" → API e2e "9 passed" (4 core + 5 platform; the API suite holds both) → UI e2e "3 passed (3.3s)" → make exit 0 (the §1-M3 proof surface; the runner path stays best-effort per the freeze flag)
- [x] `.github/workflows/kind-e2e.yml` parses + lints clean — `yaml.safe_load` green in-test + structural review: permissions.contents:read, timeout-minutes 45, every `uses:` pinned (@v4/@v5), an `if: always()` step runs `make kind-down`
- [x] the existing `.github/workflows/ci.yml` is byte-identical to HEAD — `git diff HEAD -- .github/workflows/ci.yml` empty
- [x] `docs/runbooks/cloud-deploy.md` reads as a real operator runbook (not a stub) — full read confirms prerequisites, the `helm upgrade --install -f values.yaml -f values-prod.yaml` swap, the NetworkPolicy-under-enforcement HARD-STOP pre-apply gate (with staging-first guidance), the enc-key fail-fast item, secrets-out-of-band, verify + rollback(→backup-rollback.md)
- [x] no secret value is inlined in either new artifact — `test_no_secret_inlined` green; manual grep: no Fernet/44-char-base64; secrets are angle-bracket placeholder tokens only (no real values)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `ci-e2e` is in `.PHONY` (first line) and its recipe runs both e2e scripts with `--no-up`; `make -n ci-e2e` + the awk recipe dump confirm `kind-up` prerequisite → `e2e_kind.sh` → `e2e_kind_ui.sh`; the workflow's `make ci-e2e` + `make kind-down` resolve to real targets
- [x] DEAD-CODE (code) — no orphaned target/step; every workflow step contributes (toolchains → kind/helm install → chromium → ci-e2e → always-teardown)
- [x] SEMANTIC (prose / non-code) — read `docs/runbooks/cloud-deploy.md` IN FULL: apply marked HUMAN-RUN / "never executed by CI"; NP pre-apply gate + enc-key fail-fast present with substance; the helm command + values-files are correct; no secret leaks

### GATE RECORD
Outcome: PASS — Tin signed "PASS, but harden the MED now" (AskUserQuestion, security-adjacent surface
escalated not auto-PASS). Evidence: structural 7/7 (incl. the hardened byte-identity ci.yml guard);
live `make ci-e2e` green end-to-end (kind Ready → API e2e 9 passed → UI e2e 3 passed → exit 0); ci.yml
byte-unchanged; no injection vector; no secret leak. Adversarial refute-read = EARNED-GREEN 0.87, no
HARD-STOP. The one MED (anchor strength) was hardened to an un-gameable byte-compare before the gate.
Reviewed by: Tin Dang · date: 2026-06-27

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): once Actions billing is restored — the kind-e2e workflow's run duration vs the 45-min budget, and its pass/fail (the first real-runner observation); locally — `make ci-e2e` green per deploy-shaped change.

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] strengthen `test_existing_ci_unchanged` to a byte/hash compare against `git show HEAD:.github/workflows/ci.yml` (evidence: refute-read MED — current guard only checks the two jobs exist, not byte-equality; artifact IS unchanged but the guard is weaker than the claim).
- [SPEC · open] once Actions billing is restored, RUN the kind-e2e workflow on a real runner and tune for the runner's CPU/RAM/disk + the 45-min budget (free disk, runner size) — the freeze flag's accepted unknown (evidence: workflow authored but never observed on a hosted runner).
- [SPEC · open] SHA-pin the workflow's `uses:` actions (or adopt Dependabot/Renovate digest-pinning) for supply-chain immutability beyond mutable `@vN` tags (evidence: refute-read LOW; mirrors the v53 envoy digest-pin delta).
- [SPEC · open] broaden `test_no_secret_inlined` beyond the Fernet shape (40-hex API keys, JWTs, base64 tokens, `-----BEGIN ... KEY-----`) (evidence: refute-read LOW — current regex is Fernet-only; no secrets present today).
- [SPEC · carried] the v53 cloud-runbook HARD-STOP items are now DOCUMENTED in `docs/runbooks/cloud-deploy.md` (NetworkPolicy-under-enforcement validation + enc-key fail-fast) — they remain REQUIRED pre-apply work before any real-cloud apply (evidence: [[v53-kind-envoy-three-bugs]]).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [ADD · open] when the real proof surface is blocked by an external constraint (Actions billing), name the SUBSTITUTE proof surface IN the frozen contract (here: a locally-green `make ci-e2e` + structural validation) so "done" is honest and un-gameable, not a green that was never run (evidence: the freeze flag Tin approved; `make ci-e2e` ran green live while the workflow stayed un-exercised).
- [TDD · open] a CI workflow + a runbook ARE testable without executing them — parse the YAML/Makefile/markdown structurally (pinning, bounded timeout, always-teardown, required runbook sections, no-secret-scan) for a real red→green, with one always-true anchor (existing ci.yml) proving the suite isn't vacuous (evidence: 6-red/1-anchor before build → 7-green after).
- [ADD · open] a structural test asserts PRESENCE, not byte-IDENTITY — pair an "additive-guard" test with an out-of-band `git diff HEAD` check at verify, because "the two jobs still exist" is weaker than "the file is unchanged" (evidence: refute-read MED on `test_existing_ci_unchanged`).
