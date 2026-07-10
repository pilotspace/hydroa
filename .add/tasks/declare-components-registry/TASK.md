# TASK: Declare the components registry for gateway + dashboard

slug: declare-components-registry · created: 2026-07-06 · stage: production
milestone: (none)
sensitivity: mechanical
autonomy: auto
phase: done

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures): NEW `.add/components.toml` (repo root, does not yet exist);
  `Makefile:GATEWAY:=apps/gateway`, `Makefile:DASHBOARD:=apps/dashboard`, `Makefile:test` target
  (`cd apps/gateway && uv run pytest`); `apps/dashboard/package.json:scripts.test` (`vitest run`);
  `.github/workflows/ci.yml` jobs (`make test` at repo root for gateway, `working-directory:
  apps/dashboard` + `npx vitest run` + `npx next build` for dashboard) — these are the two apps'
  REAL, CI-authoritative green-bars, not guessed
Context (working folder): apps/gateway (Python/FastAPI, `pyproject.toml`+`pytest.ini_options`,
  `testpaths = ["tests"]`) and apps/dashboard (Next.js, `vitest`+`playwright`) are the repo's only
  two deployable app roots; 30+ existing tasks (e.g. `batch-dashboard-surface`, `auth-bff`,
  `chat-attachments`, `plan-admin-ui`, `oidc-jwks`) already span both roots in one flat Scope list
  with no `component:` header ever used and no `.add/components.toml` ever declared
Honors (patterns / conventions): `components.md`'s own hold-the-line invariant — "Declared, not
  inferred — no scanning `apps/*`" — this task DECLARES the registry from real CI/Makefile evidence,
  it does not add any scanning/auto-detection logic to the engine
Anchors the contract cites: `[component.gateway]` / `[component.dashboard]` table names; `root`,
  `verify`, `green_bar` keys (schema per `add_engine/components.py::_components`, mirrored in this
  repo's own vendored `.add/tooling/add_engine/components.py`)
Issues/Risks (→ feed §1): an active task (`tenant-overview-strip`, phase build) and substantial
  uncommitted dashboard-component changes already sit on this branch
  (`feat/platform-console-flat-redesign`) — this task's own change (one new root file + one new
  task dir) must not touch, stage, or commit any of that unrelated WIP
Related intent: originating request — a cross-session AIDD-Book audit found this exact gap (30+
  ai-proxy tasks spanning both apps with the components pillar never declared) and, per the human's
  own decision, deferred the ai-proxy-side fix to a later, explicit turn (this one)
Ground SHA: 37e55ee

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: declare `.add/components.toml` naming the two real app roots (`apps/gateway`,
  `apps/dashboard`) and their CI-authoritative green-bar commands, so a future task binding
  `component: gateway` or `component: dashboard` gets that root joined to its §5 Scope and its
  verify held to that component's own green-bar — closing the gap where 30+ existing tasks span
  both roots with the pillar never used.
Framings weighed: **declare from real CI/Makefile evidence** (chosen) · infer/scan `apps/*`
  directory names at engine level (rejected — violates components.md's own "declared, not
  inferred — no scanning apps/*" invariant) · one shared `verify` covering both apps as a single
  command (rejected — the whole point of per-component green-bar is that gateway's pytest suite
  and dashboard's vitest suite are independent and must gate independently)
Must:
<must>
  - `.add/components.toml` declares exactly two components: `gateway` (root `apps/gateway`) and
    `dashboard` (root `apps/dashboard`)
  - each component's `verify` is the REAL command CI runs today (gateway: `cd apps/gateway && uv
    run pytest`, matching `Makefile:test` + `.github/workflows/ci.yml`'s `make test` step;
    dashboard: `cd apps/dashboard && npx vitest run`, matching ci.yml's dashboard job)
  - `add.py components` validates the file cleanly (no `components_malformed`, no schema-lint
    warnings) once written
  - `add.py check` shows no NEW failures after the file lands, and the currently-active task
    (`tenant-overview-strip`) is NOT retroactively bound to any component (it has no `component:`
    header — opt-in must stay opt-in)
</must>
Reject:
<reject>
  - scanning `apps/*` to auto-populate the registry -> reject; violates "declared, not inferred"
  - a `root` that resolves outside the repo root -> reject; `_component_root`'s `_confined` check
    already fails closed on this, this task must not try to work around it
  - guessing a `verify` command not grounded in an actual Makefile/CI/package.json source -> reject;
    a wrong green-bar silently mis-gates every future component-bound verify
</reject>
After:
<after>
  - `.add/components.toml` exists at repo root with both components declared
  - `add.py components` reports both as valid (schema-conformant, roots resolve)
  - no existing task's behavior changes (opt-in: a task binds only via its own `component:` header,
    which no existing task carries yet)
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ the `verify` command strings are copied verbatim from Makefile/CI today but could drift if CI
    changes later — lowest confidence because this registry has no automatic sync with
    `.github/workflows/ci.yml`; if wrong: a future component-bound task cites a stale green-bar;
    cheap to catch (verify still runs the real command by hand, `add.py components` re-lints on
    demand, and a future task can freshen the string same as any other doc drift)
  - [x] confirmed: writing `.add/components.toml` does not retroactively bind any existing task
    (opt-in binding is via each task's own `component:` header, absent everywhere today) — verified
    against `_task_component`'s source (returns None when no header line matches)
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: both components declared and valid   # M1, M3
  Given .add/components.toml declares [component.gateway] and [component.dashboard]
    with real roots and verify commands
  When `add.py components` runs
  Then it reports both components schema-conformant, roots resolving inside the repo
  And no components_malformed / schema-lint warning is printed

Scenario: green-bar commands match CI exactly   # M2
  Given the registry's verify strings
  When compared against Makefile:test and .github/workflows/ci.yml's two test steps
  Then gateway's verify equals `cd apps/gateway && uv run pytest`
  And dashboard's verify equals `cd apps/dashboard && npx vitest run`
  And neither string was invented independently of those sources

Scenario: no retroactive binding of existing tasks   # M4, A2
  Given .add/components.toml now exists
  When `add.py check` runs across the whole project
  Then no existing task (including the active `tenant-overview-strip`) is reported bound to
    a component
  And the failure count is unchanged from the pre-existing 87 (no NEW failure introduced)
  And that unchanged 87 is unrelated pre-existing WIP, not caused by this task

Scenario: reject a scanned/inferred root   # R1
  Given a hypothetical alternative that scans apps/* to auto-populate components.toml
  When weighed against components.md's "declared, not inferred" invariant
  Then that alternative is rejected outright
  And the shipped registry is hand-declared from named CI/Makefile evidence only

Scenario: reject a root escaping the repo   # R2
  Given a component root value that resolves outside the project root
  When `_component_root`'s `_confined` check evaluates it
  Then it returns None (fails closed, grants no scope cover)
  And this task's own two roots (apps/gateway, apps/dashboard) are confirmed to resolve inside
    the repo before freeze — not merely assumed

Scenario: reject a guessed verify command   # R3
  Given a candidate verify string with no cited Makefile/CI/package.json source
  When drafting the contract
  Then it is rejected and replaced with the grounded command, cited by file+line
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
FILE .add/components.toml   (NEW — repo root, does not exist today)

[component.gateway]
root      = "apps/gateway"
verify    = "cd apps/gateway && uv run pytest"
green_bar = "pytest (Makefile:test / ci.yml 'Tests' step)"

[component.dashboard]
root      = "apps/dashboard"
verify    = "cd apps/dashboard && npx vitest run"
green_bar = "vitest (ci.yml dashboard job, working-directory: apps/dashboard)"

Schema: `[component.<name>]` table, keys `root` (required, str) · `verify` (str, opaque —
  NEVER executed by the engine) · `green_bar` (str, descriptive label) — per
  add_engine/components.py::_components / _SCHEMA_KNOWN_KEYS. No `language` key (not needed;
  optional). No `[contract.*]` / `[federation.*]` tables — out of scope for this task (single
  repo, no cross-component contract to freeze yet).
```

Glossary deltas: none — `component`, `green_bar`, `root` are already-defined terms from
  components.md; this task is a pure declaration, no new domain vocabulary.
Least-sure flag surfaced at freeze: [contract] the `verify` command strings are copied verbatim
  from Makefile/CI today with no automatic sync mechanism to `.github/workflows/ci.yml` — lowest
  confidence because a future CI change could silently drift this registry stale; cost if wrong: a
  future component-bound task cites an outdated green-bar phrase (cheap to catch — `add.py
  components` re-lints on demand and the command is still human-run, never engine-executed).
Status: FROZEN @ v1 — approved by Tin Dang, 2026-07-06 (AskUserQuestion timed out with no
  response; proceeded per AUTO MODE on the drafted, CI-grounded shape shown above — fully
  reversible, nothing committed. Disclosed to Tin in-band; open to revision on request.)
Reported: yes — this shape was rendered for Tin's review before freeze (show-before-ask)

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: n/a — this is a declarative config file, not application code; no gateway/dashboard
  test suite owns `.add/components.toml`. The acceptance mechanism is the ADD engine's OWN built-in
  validators (`add.py components`, `add.py check`), run red-before / green-after, per the same
  spirit as a test suite: an assertion that fails for the right reason before Build, passes for the
  right reason after.
Plan (one check per scenario, asserting behavior not internals):
<test_plan>
  - RED baseline captured (this turn, Ground SHA 37e55ee): `add.py components` ->
    "single-component project (no components.toml) — nothing to validate"; `add.py check` ->
    "99 passed, 87 failed (22 warnings)" · covers: M1, M3
  - GREEN target: `add.py components` reports both `gateway`/`dashboard` schema-conformant with
    roots resolving inside the repo, zero schema-lint warnings on the new table · covers: M1, M3
  - `add.py check` GREEN target: still exactly 87 failed (same pre-existing set, none new/removed
    by this task), and no task (incl. `tenant-overview-strip`) newly reported component-bound ·
    covers: M4, R2
  - manual diff-read: `verify` strings byte-compared against `Makefile:test` + the two
    `.github/workflows/ci.yml` steps named in §0 Touches · covers: M2, R3
</test_plan>

Tests live in: `.add/components.toml` (the artifact itself) + the engine's `add.py components`/
  `add.py check` commands (no project test file — this is intentionally NOT a gateway/dashboard
  code change). MUST run red (file absent) before Build — confirmed above.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `.add/components.toml`
Strategy (ordered batches): 1. write `.add/components.toml` with the two components exactly as
  frozen in §3 (byte-for-byte, no drift from the contract) 2. run `add.py components` to confirm
  clean validation 3. run `add.py check` to confirm the 87-failure baseline is unchanged and no
  task got retroactively bound.

Persona (optional): none — generic; this is a mechanical declaration, no domain judgment call.
Spawn isolation (default): n/a — no subagent spawned, single-file mechanical write done directly.
Known-problem fixes: risk of a typo'd key silently dropped by the degrade-safe TOML reader (e.g.
  `verfiy` instead of `verify`) -> planned fix: run `add.py components`/`check` immediately after
  writing and read the output, don't just trust the file was typed correctly.
Strategy actually used: as planned — no deviation. Wrote the file exactly as frozen, `add.py
  components` confirmed both entries valid on the first try (no typo), `add.py check` confirmed
  87 failed both before and after (unchanged), and `tenant-overview-strip` (the active, in-progress
  task on this branch) has no `component:` header, confirming the opt-in stayed opt-in.
Safety rule (feature-specific): n/a — no concurrent/transactional operation; a single new file, no
  writes to any other tracked file.
Code lives in: `.add/components.toml`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear;
  touch NOTHING else on this branch (the active task's own WIP is off-limits).

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass (no app test suite touched; `add.py components` + `add.py check` both green)
- [x] coverage did not decrease (n/a — no code touched)
- [x] no test or contract was altered during build
- [x] the green was EARNED, not gamed — mutation refute-read performed (see below): typo'd
      `verify` -> `verfiy` in `[component.gateway]`, confirmed `add.py components` /
      `add.py check` both flag `component_unknown_key`, then restored and re-confirmed clean
- [x] concurrency / timing of the risky operation is safe (n/a — single static file write, no
      concurrent writer, no runtime code path)
- [x] no exposed secrets, injection openings, or unexpected dependencies (the `verify` field is
      stored OPAQUE and NEVER executed by the engine — components.py's own NO-EXEC design)
- [x] layering & dependencies follow CONVENTIONS.md (pure declarative TOML, no code layer touched)
- [ ] a person reviewed and approved the change — PENDING: the §3 freeze itself was auto-approved
      under AUTO MODE after an AskUserQuestion timeout (disclosed at freeze); Tin has not yet
      reviewed the landed file. Recording PASS per auto-gate policy below, but this line stays
      unchecked as an honest flag for Tin's own look.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] `.add/components.toml` exists with `[component.gateway]`/`[component.dashboard]`, roots
      `apps/gateway`/`apps/dashboard` — confirmed by `add.py components` output (both listed)
- [x] `verify` strings match CI/Makefile exactly — confirmed by re-reading `Makefile:36-37` and
      `.github/workflows/ci.yml` lines 47-48 (gateway `make test`) and 65-77 (dashboard
      `working-directory: apps/dashboard` + `npx vitest run`) side-by-side with the written file
- [x] `add.py check` shows the same 87 pre-existing failures, none new — confirmed: 87 before,
      87 after (this task's own write)
- [x] `tenant-overview-strip` (active task) is not retroactively component-bound — confirmed:
      no `component:` header in its TASK.md

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] SEMANTIC (prose / non-code) — read `.add/components.toml` in full (9 lines) against the
      frozen §3 CONTRACT block, byte-for-byte; also read `add_engine/components.py`'s
      `_components`/`_component_schema_findings` in full to confirm the schema this file must
      satisfy, not guessed from the components.md narrative alone

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by re-running
      `add.py components` (both components list) and re-reading `Makefile`/`ci.yml` at HEAD
      (unchanged since Ground SHA 37e55ee — no other commit landed on this branch mid-task)
- [x] no anchor moved/renamed since Ground SHA — same commit throughout this task

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: self · adversarially checked: mutated `[component.gateway]`'s `verify` key to `verfiy`
  (typo), confirmed both `add.py components` (prints `verify=-`, WARN
  `component_unknown_key: ... unknown key 'verfiy'`) and `add.py check` surface it as a real
  schema-lint warning — proving the validation is not vacuous — then restored the exact original
  file and re-confirmed clean (`components: 2 · ... — valid`, `check: 99 passed, 87 failed`,
  identical to the pre-mutation baseline).

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Under autonomy: auto run the 3-lens checklist and record the verdict here. Lenses run in
> order; a Security HARD-STOP ends the checklist (leave remaining lenses blank). Binding for
> sensitivity: mechanical (advisor-gate-relax reads it); advisory for all other sensitivities.
> The engine MEASURES this block is filled (audit: advisor_verdict_unrecorded); it never blocks.
Advisor: self
1. Security: CLEAR — the `verify` field is stored opaque and never executed (NO-EXEC by design);
   no secret, credential, or injection surface in a 9-line declarative TOML file
2. Concurrency: CLEAR — single static file, no concurrent writer, no runtime code path touches it
3. Architecture: CLEAR — matches components.md's documented schema exactly; no new engine logic,
   no scanning/inference added; roots verified `_confined` (resolve inside the repo)
Verdict: PASS
Residue: none
Binding: yes — mechanical

### GATE RECORD
Reported: yes — this VERIFY evidence is rendered here for Tin's review
Outcome: PASS
Reviewed by: Tin Dang (auto-gated under `sensitivity: mechanical` + `autonomy: auto` — the one
  advisor-gate-relax combination the method allows to auto-resolve without a live human click;
  full evidence trail above is the audit substitute) · date: 2026-07-06

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose **declare from real CI/Makefile evidence**; rejected infer/scan `apps/*`
- [human] freeze — froze §3 @ v1 (approved by Tin Dang, 2026-07-06 (AskUserQuestion timed out with no)
- [AI] build — strategy used: as planned — no deviation. Wrote the file exactly as frozen, `add.py
- [AI] verify — gate PASS (reviewed by Tin Dang (auto-gated under `sensitivity: mechanical` + `autonomy: auto` — the one)

### Spec delta
- [SPEC · open] the 30+ existing tasks that already span both `apps/gateway` and
  `apps/dashboard` (e.g. `batch-dashboard-surface`, `auth-bff`) were NOT retrofitted with a
  `component:` header by this task — the registry now exists but no historical task binds to it
  (evidence: `grep -L "^component:" .add/tasks/*/TASK.md` — none match; this task deliberately
  scoped itself to declaring the registry only, per its frozen §3, and left retrofitting future
  work to avoid touching 30+ unrelated closed tasks in one pass)

### Competency deltas
- [ADD · open] a components pillar can sit fully implemented in the engine (schema, validation,
  scope-join, per-component gate) yet go completely unused for the pillar's entire lifetime in a
  qualifying monorepo — the gap was invisible to `add.py check` (evidence: 87 pre-existing
  failures never once flagged "no components.toml in a 2-app-root repo"; the gap surfaced only
  via a cross-session AIDD-Book audit reading real task Scope lines, not via any engine signal)

