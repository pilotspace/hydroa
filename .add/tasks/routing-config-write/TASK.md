# TASK: Routing config write — editable model-groups / strategy / deployment limits + /routing editor

slug: routing-config-write · created: 2026-06-23 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
phase: ground   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
SCOPE VERDICT (the deciding finding — recon 2026-06-23): routing config is TODAY purely BOOT-TIME env vars; making it writable is a LARGE architectural change, NOT a single task → re-size as a sub-milestone (the milestone pre-flagged "may warrant its own sub-milestone — re-size at open"). Evidence:
- SOURCE OF TRUTH = `core/config.py:Settings` (pydantic-settings, `GATEWAY_` env): `deployments`(dict[str,list[Deployment]] via GATEWAY_MODEL_GROUPS; Deployment={model_id,weight>0,tpm_limit?,rpm_limit?}), `routing_strategy`(ordered|simple-shuffle|least-busy|latency), `cooldown_failure_threshold/_ttl_s/_window_s` (circuit, GLOBAL not per-deployment), `upstream_max_retries(0..5)/_retry_backoff_base_s/_retry_deadline_s/_fallback_on_error/_stream_resilience_enabled`, `loadbal_ewma_alpha/_inflight_ttl_s`. NO ORM table, NO migration for deployments/model_groups/routing_config (latest head `f4a9b3c7e8d2_alert_events`; baseline = 6 tables, none routing).
- RUNTIME = `main.py:617` builds `app.state.model_router = FallbackModelRouter(...)` ONCE at create_app from Settings; stores `_model_groups/_deployments/_strategy/_load_gate/_limit_gate` privately, NEVER re-read per request. NO refresh()/reload() exists (contrast catalog's `provider_resolver.refresh()`). → a write needs a runtime reload of the LIVE routing singleton on the request HOT-PATH (the hard, risky part) OR restart-to-apply.
- READ side = `proxy/api/routing_admin_router.py:get_routing_admin` (`GET /admin/routing`, require_owner_or_admin, read-only FROZEN) returns {retry_policy{max_retries,backoff_base_s}, cooldown{enabled,threshold,ttl_s,window_s}, model_groups{alias:[model_id]}, candidates[{model_id,alias,state}]}; reads app.state.settings + app.state.model_router.model_groups + cooldown_gate.snapshot_state. NOTE: it does NOT currently expose deployment weight/tpm/rpm — the write side must extend the read shape.
- NO write/mutation of routing config exists anywhere today (no POST/PUT/PATCH on routing_admin_router).
- DASHBOARD = `app/(dashboard)/routing/page.tsx`→`components/routing/RoutingPage.tsx` is READ-ONLY (bffGet "/admin/routing", 4 cards: retry/cooldown/model-groups/candidates; ZERO edit UI, imports only bffGet).
- CLOSEST ANALOG for the build = tenant OIDC config (`a9b3c4d5e6f7_oidc_tenant_config` migration + ORM + encrypted store + TTL-cache resolver + GET/PUT editor) — similar scope MINUS routing's extra live-reload-of-a-singleton-on-the-hot-path complexity.
WHAT A WRITABLE ROUTING CONFIG NEEDS (sub-milestone task breakdown, est. 3–4 tasks):
1. persistence: new migration + ORM `routing_config` (singleton/operator-wide row: model_groups+deployments JSONB, strategy, retry/cooldown knobs) + repository.
2. runtime reload: a safe `FallbackModelRouter.reload(config)` (or atomic app.state.model_router swap) + strategy/gate reconstruction — design-for-failure on the hot path (a bad config must NOT break live routing; validate-then-swap, keep old on failure).
3. write endpoint: `PUT /admin/routing` (owner/admin) that round-trips ALL Settings validators (empty-candidate-list, duplicate-deployment, too-many-candidates, alias-collides-with-candidate, unknown-strategy, positive weight/limits) BEFORE persist+reload.
4. dashboard editor: forms for model-groups (alias/deployment CRUD + weight/limits), strategy dropdown, retry/cooldown knobs; optimistic invalidate ["admin-routing"].
Context (working folder):
- `.add/milestones/v31/MILESTONE.md` task "routing-config-write" + criterion "An owner edits model-groups / routing strategy / deployment limits from the dashboard." This task is the LAST of v31; its size (sub-milestone) is the open re-size decision surfaced to Tin 2026-06-23.
Honors (patterns / conventions):
- CONVENTIONS.md: Clean-Arch (api→application→infrastructure), migration in BOTH orm __table_args__ AND alembic, owner/admin gate, frozen Pydantic, design-for-failure on IO + (here) on the hot-path reload.
- PROJECT.md: routing decides the request hot-path; an operator-wide config write must never break live routing (validate-then-atomic-swap; restart-to-apply is the safe fallback).
Anchors the contract cites: `Settings`(config.py) · `FallbackModelRouter`(main.py:617) · `get_routing_admin`/`GET /admin/routing` · `RoutingPage.tsx` · a NEW `routing_config` table/ORM + reload mechanism (sub-milestone).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: <name>
Framings weighed: <chosen> (chosen) · <alternative> · <alternative>
Must:
<must>
  - <required behavior>
</must>
Reject:
<reject>
  - <bad input / situation> -> "<error_code>"
</reject>
After:
<after>
  - <state that is true once it succeeds>
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ <the one assumption most likely to be wrong> — lowest confidence because <why>; if wrong: <cost>
  - [ ] <next assumption, ranked> — confirm or deny; never carry an open one forward
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: <short name>
  Given <starting situation>
  When <action>
  Then <expected result>
  And <what must remain unchanged>   # required for every rejection
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
<METHOD> <path>   body: { <fields> }
  200 -> { <success fields> }
  4xx -> { error: "<code>" | "<code>" }
Schema: <tables/fields touched, and access pattern>
```

Status: DRAFT
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: <e.g. 90%>
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_<scenario>: arrange <Given> / act <When> / assert <Then> + assert <unchanged>
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `./src/`   <fill before the §3 freeze — every file the build may write>
Strategy (ordered batches): <1. … 2. … — the planned build order; guidance, not enforced>
Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [ ] <observable outcome a correct build must produce> — confirmed by <how / where>
- [ ] <another observable outcome> — confirmed by <evidence seen>

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### GATE RECORD
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
