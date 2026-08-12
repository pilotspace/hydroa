# TASK: Routing-editor save feedback + validation

slug: routing-editor-feedback · created: 2026-06-24 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
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
  CHANGES (this task owns — apps/dashboard/components/routing/RoutingEditor.tsx, 385 lines):
  - `handleSave()` (167): TODAY `saveRouting.mutate({routing_strategy, model_groups: groups})` with NO
    client validation. ADD a pre-flight guard: if any deployment row has `weight <= 0` (empty input
    becomes 0 at handleRowChange@156) OR a blank `model_id`, set an inline client error and DO NOT
    mutate — kills the 422 round-trip (backend emits INVALID_DEPLOYMENT_WEIGHT / DEPLOYMENT_MODEL_ID_REQUIRED).
  - `saveRouting` useMutation (104): `onSuccess` (106) TODAY only clears mutError + invalidates
    ["admin-routing"]. ADD a `saved` confirmation flag → render a DYNAMIC "Saved — restart the gateway
    to apply" affordance (role=status), distinct from the ALWAYS-ON static notice @184. Clear `saved`
    on any further edit (handleRowChange/handleAddGroup/etc.) so it only shows while saved-and-pending.
  - weight `<Input>` (263-269): optionally add `min={1}` + aria-invalid wiring to back the client guard.

  READ (mirror / unchanged):
  - static restart notice @184 "Changes take effect on next restart." — the persistent hint; the new
    affordance is the POST-SAVE dynamic confirmation, NOT a replacement.
  - mutError inline `<p role="alert">` @367 — the existing error-render pattern to mirror for the client
    validation message (reuse role=alert).
  - `getErrorTitle` @50 (reads BffError.problem.detail = the validator code) — unchanged.

  CONSUMES (FROZEN — backend exists, do NOT change):
  - GET/PUT `/admin/routing` (proxy/api/routing_admin_router.py): PUT validates via merge_routing_config
    (same boot validators) → 422 ROUTING_CONFIG_INVALID w/ validator code in `detail`
    (INVALID_DEPLOYMENT_WEIGHT etc.); restart-to-apply (PUT never mutates live router). GET returns
    `{retry_policy, cooldown, model_groups, candidates, routing_strategy, deployments}`.
  - `bffPut<T>(path, body)` / `BffError` (lib/bff-client.ts).

  OUT OF SCOPE (BE-dependent → SPEC deltas, not this FE-only task):
  - Making retry/cooldown/LOADBAL knobs EDITABLE: ROUTING_OVERRIDE_KEYS (routing_config_merge.py:23)
    makes the PUT ACCEPT them, and GET returns retry_policy+cooldown (RoutingPage shows them read-only
    @156), BUT loadbal_* is NOT in the GET response → exposing loadbal needs a BE change. Editable knobs
    are an additive FEATURE (new inputs + per-knob validation + PUT-body extension), not polish.
  - "last saved at": GET has no `updated_at` (the routing_config_orm column exists but is unexposed) →
    needs a BE additive field. Deferred.

Context (working folder):
  - v32 shipped routing-config WRITE (restart-to-apply) + the editor (strategy + deployments). This task
    closes the v32 editor-UX follow-ups: a SAVED-and-pending signal + a client-side weight guard.
  - The "exists already" trap: a STATIC restart hint is present (@184); the delta wants a DYNAMIC
    post-save confirmation. Don't duplicate the static line — add a transient saved-state affordance.

Honors (patterns / conventions):
  - Reuse the editor's existing inline role=alert pattern (@367) for the client error; role=status for
    the saved affordance (a11y live region). WCAG-AA + tokens, same bar as v23/v24.
  - FE-only against the FROZEN PUT contract; the gateway stays the single validator (the client guard is
    a fast-fail UX layer, NOT a replacement — the server 422 still backstops).

Anchors the contract cites:
  - `RoutingEditor` `handleSave` weight/model_id client guard · the `saved` post-save affordance
    (role=status) vs the static @184 notice · the FROZEN PUT /admin/routing 422 contract it fast-fails.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Routing-editor save feedback (post-save restart affordance) + client-side weight/model_id guard
Framings weighed:
  - Two FE-only polish deltas: post-save "saved — restart to apply" affordance + client weight>0 guard
    (CHOSEN) — both are pure RoutingEditor.tsx changes against the FROZEN PUT contract; high-value, low-risk.
  - Also make retry/cooldown/loadbal knobs EDITABLE — DEFERRED: loadbal isn't in the GET response (needs
    BE) and editable knobs are an additive feature, not polish (→ SPEC delta; flagged at the §3 freeze).
  - Replace the static restart notice with the dynamic one — rejected: the static hint is always-true
    guidance; the post-save affordance is a transient CONFIRMATION. Keep both (different jobs).
Must:
<must>
  - CLIENT WEIGHT GUARD: on Save, if any deployment row has weight <= 0 (incl. the empty-input → 0 case)
    OR a blank model_id, show an inline validation error (role=alert) and DO NOT call PUT — no 422 round-trip.
  - The guard message names the offending condition (e.g. "Every deployment needs a weight greater than 0").
  - POST-SAVE AFFORDANCE: after a successful PUT, show a transient "Saved — restart the gateway to apply"
    status (role=status / aria-live), distinct from the always-on static notice; it confirms the write
    AND reminds restart-to-apply.
  - The saved affordance CLEARS on any subsequent edit (strategy/row/group change) — so it only shows
    while the saved config is unapplied-and-untouched, never staically after editing resumes.
  - Server stays authoritative: a weight that passes the client guard but fails server validation still
    surfaces the existing 422 mutError (the guard is fast-fail UX, not a replacement).
  - No change to the PUT body shape, the GET shape, or any backend; strategy + deployments still the only
    persisted edits.
</must>
Reject:
<reject>
  - Save with a weight <= 0 or empty model_id -> inline client error (role=alert), PUT NOT sent; the
    form state is preserved (no mutation, no navigation).
  - A server-side 422 (validation the client guard doesn't cover) -> existing mutError path (role=alert)
    with the validator code; the saved affordance is NOT shown.
</reject>
After:
<after>
  - An operator who saves a valid routing config sees an explicit "Saved — restart to apply" confirmation;
    an operator who fat-fingers a 0 weight is stopped client-side with a clear message before any request.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ SCOPE: dropping editable retry/cooldown/loadbal knobs from THIS task (→ SPEC delta) — lowest
    confidence because the v37 milestone exit criterion named "expose the global knobs"; if Tin wants
    them in-scope, the contract expands (new inputs + PUT-body extension + loadbal needs a BE GET field).
    Cost if I guess wrong: a re-open. THIS is the §3 freeze decision.
  - [x] weight is an integer > 0 (0/empty/negative invalid) — confirmed: backend INVALID_DEPLOYMENT_WEIGHT
    + emptyRow() defaults weight:1 + handleRowChange coerces "" → 0.
  - [x] role=status for the saved affordance is announced without stealing focus — confirmed: matches the
    dashboard's existing aria-live conventions (Loading uses role=status).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: client weight guard blocks a zero weight
  Given the editor has a deployment row with weight cleared (becomes 0)
  When the operator clicks Save
  Then an inline error "weight greater than 0" (role=alert) is shown
  And PUT /admin/routing is NOT called (no network request)
  And the form state is preserved

Scenario: client guard blocks a blank model_id
  Given a deployment row with an empty model_id
  When the operator clicks Save
  Then an inline error naming the missing model is shown
  And PUT is NOT called

Scenario: valid save shows the restart affordance
  Given all rows have weight > 0 and a model_id, PUT returns 200
  When the operator clicks Save
  Then a "Saved — restart the gateway to apply" status (role=status) is shown
  And PUT /admin/routing was called once

Scenario: saved affordance clears on edit
  Given the saved affordance is showing after a successful save
  When the operator edits any field (strategy / row / alias)
  Then the saved affordance is no longer shown
  And the static restart notice remains

Scenario: server 422 still surfaces (guard is not a replacement)
  Given a body that passes the client guard but PUT returns 422 with a validator code
  When the operator clicks Save
  Then the existing mutError (role=alert) shows the validator code
  And the saved affordance is NOT shown
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Component: RoutingEditor()  (apps/dashboard/components/routing/RoutingEditor.tsx — EDIT in place)
  CONSUMES (existing, FROZEN @ v32): PUT /admin/routing via bffPut("/admin/routing", {routing_strategy,
    model_groups})  → 200 {effective config} | 422 ROUTING_CONFIG_INVALID (.problem.detail = validator code)

  NEW client behavior (no contract/endpoint change):
  - handleSave(): isValid = every row across every alias has weight > 0 AND model_id.trim() !== "".
      invalid  -> setClientError("Every deployment needs a model and a weight greater than 0"); RETURN
                  (no bffPut call); clear any prior `saved`.
      valid    -> clear clientError; saveRouting.mutate({routing_strategy, model_groups: groups}).
  - clientError renders as <p role="alert"> (mirrors the existing mutError @367); mutually independent of
    the server mutError (server 422 still renders via the existing path).
  - saveRouting.onSuccess: setSaved(true) (in addition to the existing clear+invalidate).
  - saved === true renders <p role="status" aria-live="polite"> "Saved — restart the gateway to apply"
    (the static @184 notice stays). Any edit handler (handleRowChange / handleAddRow / handleRemoveRow /
    handleAddGroup / handleRemoveGroup / setStrategy) calls setSaved(false).

  UNCHANGED: PUT/GET body shapes; persisted edits remain strategy + deployments only. NO retry/cooldown/
    loadbal editing (deferred — see §1 ⚠ + SPEC delta). NO backend / migration / new endpoint.
Schema: none (FE-only; no DB, no new endpoint, no BFF route).
```

Status: FROZEN @ v1 — approved by Tin 2026-06-24 (chose "freeze as drafted: 2 FE-only deltas"; editable retry/cooldown/loadbal knobs + "last saved at" DEFERRED to SPEC deltas — loadbal/updated_at need BE). Least-sure flag surfaced at freeze: [contract] dropping editable retry/cooldown/loadbal knobs from this task's scope (loadbal needs a BE GET field; editable knobs are a feature not polish) — cost if wrong is only a re-open to widen the contract; Tin confirmed the narrow scope at freeze.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: the new editor behaviors (extend tests/routing-edit.test.tsx — 5 new tests, msw + userEvent)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_routing_zero_weight_blocked: render editor, clear a weight (→0), click Save / assert role=alert
    "weight greater than 0" AND assert the PUT handler was NEVER hit (a spy/flag stays false).
  - test_routing_blank_model_blocked: empty a model_id, Save / assert inline error AND no PUT.
  - test_routing_valid_save_shows_restart: valid rows, PUT 200, Save / assert role=status "restart" AND
    the PUT spy fired once.
  - test_routing_saved_clears_on_edit: after a successful save, edit a field / assert the role=status
    affordance disappears AND the static notice remains.
  - test_routing_server_422_no_saved: client-valid body, PUT 422 w/ validator code / assert mutError shows
    the code AND the saved affordance is NOT present.
</test_plan>

Tests live in: `dashboard/tests` · MUST run red (the new assertions fail on today's editor) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/routing/RoutingEditor.tsx` `dashboard/tests/routing-edit.test.tsx`
Strategy (ordered batches):
  1. Add 5 RED tests to tests/routing-edit.test.tsx (guard-blocks-zero, guard-blocks-blank, valid-shows-
     restart, saved-clears-on-edit, server-422-no-saved) → run RED.
  2. RoutingEditor.tsx: add `clientError` + `saved` state; weight/model_id guard in handleSave; setSaved
     in onSuccess; clear saved in every edit handler; render role=alert clientError + role=status saved.
  3. Run green; full dashboard vitest + lint + build.
Safety rule (feature-specific): the client guard is FAST-FAIL UX ONLY — never bypass or weaken the server
  422 (it still backstops); the saved affordance must never persist after an edit (stale "saved" lies).
Code lives in: `apps/dashboard/components/routing/`
Constraints: do NOT change any test (besides ADDING the 5) or the FROZEN PUT/GET contract; reuse existing
  @/components/ui + deps only; NO backend / migration / new endpoint; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — routing-edit.test.tsx 12/12 (6 v32 + 6 new incl. recovery); full dashboard vitest 397; build exit 0
- [x] coverage did not decrease — additive tests + behavior; 6 existing v32 tests unchanged + still green
- [x] no test or contract was altered during build — §3 frozen untouched; the 6 existing tests unchanged; new tests added (one post-refute recovery test), then re-crossed
- [x] the green was EARNED, not gamed — adversarial refute-read (sonnet) = UPHOLD, 0 blockers. Verified: saved survives the save's invalidate→reseed (reseed uses setGroups directly, NOT markEdited); guard rejects 0/NaN/negative; role=status is unique (Loading not in tree post-cache); existing weight=5 save unbroken. 2 MINORs FIXED (blank-model text assert + a fix→re-save recovery test).
- [x] concurrency / timing — synchronous client guard; saved/clientError are plain React state; no races (msw synchronous; refetch completes before the next user action)
- [x] no exposed secrets / deps — FE-only, no new package; reuses bffPut + existing role=alert/status patterns
- [x] layering & dependencies follow CONVENTIONS.md — guard is fast-fail UX only; gateway stays the single validator (server 422 backstops); FROZEN PUT/GET untouched
- [ ] a person reviewed and approved the change — PENDING Tin (commit/PR held)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] Save with weight≤0 or blank model → inline role=alert, NO PUT — confirmed by zero/blank/recovery tests (putHit stays false)
- [x] Valid save → role=status "restart to apply", PUT fired once — confirmed by valid_save + recovery tests
- [x] saved affordance clears on edit, static notice persists — confirmed by saved_clears_on_edit
- [x] server 422 still surfaces, no saved affordance — confirmed by server_422_no_saved

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — clientError/saved state + markEdited() referenced in all 5 group handlers + strategy onChange + handleSave + render; reached by the 6 new tests
- [x] DEAD-CODE (code) — markEdited/clientError/saved all on live paths; no orphan
- [x] SEMANTIC — n/a (code task)

### GATE RECORD
Outcome: PASS
Evidence: routing-edit.test.tsx 12/12 · full dashboard vitest 397 passed · tsc clean · eslint clean on
RoutingEditor.tsx · `npm run build` exit 0 · refute-read (sonnet) UPHOLD 0-blockers, 2 MINORs FIXED.
FE-only against the FROZEN v32 PUT contract; server stays the single validator.
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a
Reviewed by: AI auto-gate (autonomy:auto) · human approval (Tin) PENDING for commit/PR · date: 2026-06-24

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

- [SPEC · open] make retry/cooldown knobs EDITABLE in the routing editor (GET already returns them, PUT accepts ROUTING_OVERRIDE_KEYS) — deferred from this task as an additive feature (evidence: Tin froze the narrow 2-delta scope; v37 exit criterion narrowed accordingly).
- [SPEC · open] expose loadbal_* knobs — needs a BE GET field first (loadbal_ewma_alpha / loadbal_inflight_ttl_s are in ROUTING_OVERRIDE_KEYS but NOT in the /admin/routing GET response) (evidence: §0 OUT-OF-SCOPE).
- [SPEC · open] "last saved at" — expose routing_config_orm.updated_at on GET /admin/routing so the editor can show it (evidence: column exists, no read path).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [UDD · folded] when a STATIC always-on hint already exists, a "saved" CONFIRMATION is a SEPARATE transient affordance (role=status) that must survive the save's own refetch-reseed but clear on the next user edit — gate it on user-edit handlers, NOT on a [data]-dep effect (evidence: refute attack-vector 1; reseed uses setGroups directly so saved survives). [folded foundation-version 34]
- [TDD · folded] a client-side guard test must assert BOTH the inline error TEXT and that the network call never fired (a spy flag) — asserting only "an alert appeared" passes vacuously on any unrelated alert (evidence: refute MINOR on test_routing_blank_model_blocked, strengthened). [folded foundation-version 34]
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
