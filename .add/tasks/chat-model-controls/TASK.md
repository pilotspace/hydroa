# TASK: Chat model picker + request controls

slug: chat-model-controls · created: 2026-06-25 · stage: production
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
  - `apps/dashboard/components/chat/ModelPicker.tsx` (NEW, `"use client"`) — catalog-model `<select>` sourced from `GET /api/gw/v1/models` (bffGet + TanStack `useQuery`). Shape (existing `ModelCatalogTable.ModelsData`): `{ object:"list", data: { id, name?, context_length }[] }`. Props `{ value:string; onChange:(id:string)=>void }`. FAIL-OPEN: loading/error/empty never blocks sending — the current `value` stays selected (no fabricated list; the gateway is the source of truth).
  - `apps/dashboard/components/chat/ModelControls.tsx` (NEW, `"use client"`) — a collapsed-by-default disclosure revealing a system-prompt `Textarea` + a temperature `<input type=range>` (0–2, step 0.1). Props `{ system:string; onSystemChange; temperature:number; onTemperatureChange }`. Collapsed default keeps exactly ONE textbox in the composer so the frozen chat-workspace-page tests (getByRole("textbox")) stay green.
  - `apps/dashboard/components/chat/ChatWorkspace.tsx` (MODIFY) — lift `model` (default `openai/gpt-4o`), `system` (""), `temperature` (1) state; render `ModelPicker` in the header `data-slot="model-picker"` (replacing the static label) + `ModelControls` in the composer; thread them into `send({ model, text, system: system.trim()||undefined, temperature })`. The frozen `useChatStream` seam already accepts `system`/`temperature` — NO hook change.
Context (working folder):
  - `GET /v1/models` is available to ANY authenticated caller (the OpenAI-compatible list) — unlike `/admin/models` which 403s on members; the chat picker is role-open so it must use `/v1/models`.
  - shadcn `Select` exists but Radix-in-jsdom option selection is flaky; a NATIVE `<select>` (role combobox, `userEvent.selectOptions`) is reliable + accessible. The shadcn-Select restyle is a deferred polish delta.
  - NO `Slider`/`Label` primitive in `components/ui` → native `<input type=range>` + a `<label>`.
Honors (patterns / conventions):
  - `lib/bff-client.ts` `bffGet` cookie-only; `BffError` title in `ErrorState`; TanStack Query (`app/providers.tsx`); `ModelCatalogTable.ModelsData`/`ModelEntry` shape reused (no new type invented).
  - `useChatStream.SendInput` ({model,text,system?,temperature?}) is the FROZEN seam this task drives — consume it, do not change it.
  - WCAG-AA: every control has an accessible name (`aria-label`/`<label>`); focus-ring tokens.
Anchors the contract cites:
  - `ModelPicker({value,onChange})` · `ModelControls({system,onSystemChange,temperature,onTemperatureChange})` · `ChatWorkspace` lifted state → `send({model,text,system?,temperature})`.
  - `GET /api/gw/v1/models` → `ModelsData` ({object,data:ModelEntry[]}).
  - test harness `tests-bff/chat-model-controls.test.tsx` (bff project; MSW for `/v1/models` + capture the chat POST body).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Chat model picker + per-request controls — the user picks which catalog model answers and (optionally) sets a system prompt + temperature, all feeding the chat-workspace-page `send()`. Fills the two SLOTS that page rendered.
Framings weighed: native `<select>` + collapsed disclosure (chosen — reliable, accessible, testable) · shadcn Radix `Select` (rejected for now — flaky in jsdom; deferred polish) · always-expanded controls (rejected — a second textbox breaks the frozen chat tests + clutters the composer).
Must:
<must>
  - M1 — `ModelPicker({value,onChange})` fetches `GET /api/gw/v1/models` (bffGet+useQuery) and renders a `<select>` of `data[].id`; choosing an option calls `onChange(id)`. The picker's accessible name is "Model".
  - M2 — `ModelPicker` FAILS OPEN: while loading, on error, or on an empty list, it still renders the current `value` as a selectable option so the user is NEVER blocked from sending; it shows no fabricated models and surfaces nothing as a hard error in the composer.
  - M3 — `ModelControls({system,onSystemChange,temperature,onTemperatureChange})` is collapsed by default (a "Model settings" toggle); expanded it shows a system-prompt textarea + a temperature range input (0–2, step 0.1) each with an accessible name.
  - M4 — `ChatWorkspace` holds `model`/`system`/`temperature`, renders the picker (header) + controls (composer), and sends `send({ model, text, system: system.trim()||undefined, temperature })`; the chosen model + a non-empty system + the temperature reach the chat POST body (system ⇒ `messages[0] {role:'system'}`).
</must>
Reject:
<reject>
  - empty `/v1/models` list or fetch error -> NOT an error_code; fail-open to the current model (M2), composer stays usable.
  - blank system prompt (collapsed or empty) -> no `messages[0]` system turn is sent (omit, don't send role:'system' with "").
</reject>
After:
<after>
  - The chat POST body carries the user-selected model, optional system turn, and temperature; defaults (`openai/gpt-4o`, no system, temp 1) apply when untouched.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ `GET /v1/models` returns `{object,data:[{id,…}]}` for a normal (non-admin) signed-in user via the BFF — lowest confidence because the dashboard has only consumed `/admin/models` (owner/admin) on the read side; if wrong (e.g. members get an empty/403): the fail-open path (M2) is exactly what catches it — the picker degrades to the default model, no crash, so the cost is only "no choice of models for some roles", not a broken composer.
  - [x] `useChatStream.SendInput` already accepts `system?`/`temperature?` — CONFIRMED in the frozen v40 t2 hook (no hook change).
  - [x] default temperature 1 always sent does not break the frozen chat-workspace-page tests — CONFIRMED: those tests assert response handling, not the request body.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Picker lists catalog models and drives the model
  Given GET /v1/models returns ids [openai/gpt-4o, anthropic/claude-3]
  When the user selects anthropic/claude-3 and sends "hi"
  Then the chat POST body.model is "anthropic/claude-3"

Scenario: Picker fails open on error
  Given GET /v1/models returns 500
  When the user sends "hi" without touching the picker
  Then the composer still works and the chat POST body.model is the default "openai/gpt-4o"
  And no crash or hard error is shown in the composer

Scenario: System prompt feeds the request
  Given the user opens Model settings and types a system prompt "Be terse"
  When they send "hi"
  Then the chat POST body.messages[0] is { role:'system', content:'Be terse' } and messages[1] is the user turn

Scenario: Blank system prompt sends no system turn
  Given Model settings is collapsed (or the system field is empty)
  When the user sends "hi"
  Then the chat POST body.messages has NO role:'system' entry

Scenario: Temperature feeds the request
  Given the user opens Model settings and sets temperature to 0.2
  When they send "hi"
  Then the chat POST body.temperature is 0.2

Scenario: Default model used when untouched
  Given the picker has not been changed
  When the user sends "hi"
  Then the chat POST body.model is "openai/gpt-4o"
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
// ─ Components (the shapes this task freezes) ─
ModelPicker(props: { value: string; onChange: (id: string) => void; className?: string })
  reads:  GET /api/gw/v1/models  (bffGet + useQuery, queryKey ["chat-models"])
          -> ModelsData { object:"list", data: ModelEntry[] }   (ModelEntry = { id, name?, context_length })
  render: <select aria-label="Model"> of data[].id; onChange(id)
  fail-open: loading | isError | empty data -> render the current `value` as the only option; never throws, never blocks send

ModelControls(props: {
  system: string; onSystemChange: (s: string) => void;
  temperature: number; onTemperatureChange: (t: number) => void;
})
  collapsed by default behind a "Model settings" toggle (aria-expanded);
  expanded: <textarea aria-label="System prompt"> + <input type=range aria-label="Temperature" min=0 max=2 step=0.1>

// ─ ChatWorkspace wiring (modified, no new public prop) ─
state: model="openai/gpt-4o", system="", temperature=1
header slot  -> <ModelPicker value={model} onChange={setModel} />
composer     -> <ModelControls system temperature on…/>
submit       -> send({ model, text, system: system.trim() || undefined, temperature })

Schema: none — client-side only; reuses existing GET /v1/models (no gateway/BFF change, no new endpoint).
```

Status: FROZEN @ v1 — auto-approved (full-auto drive; non-high-risk FE, no security surface) 2026-06-25
Least-sure flag surfaced at freeze:
  - [spec] ⚠ `GET /v1/models` may be empty/403 for non-admin roles via the BFF — but M2 fail-open makes that a graceful degrade (default model still sends), not a break; pinned by `test_picker_failopen_on_error`.
  - [contract] system is omitted (not sent as role:'system' with "") when blank — a wrong choice here would inject an empty system turn; pinned by `test_blank_system_no_system_turn`.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral — one test per scenario; no dashboard coverage regression.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_picker_lists_and_drives_model: MSW /v1/models → 2 ids; render ChatWorkspace; selectOptions claude; send "hi"; assert captured chat POST body.model === "anthropic/claude-3".
  - test_picker_failopen_on_error: MSW /v1/models → 500; send "hi" untouched; assert body.model === "openai/gpt-4o" + no role=alert in the composer + textbox still present.
  - test_system_prompt_feeds_send: open Model settings; type system "Be terse"; send "hi"; assert body.messages[0]={role:'system',content:'Be terse'} and messages[1].role==='user'.
  - test_blank_system_no_system_turn: collapsed; send "hi"; assert body.messages has no role:'system'.
  - test_temperature_feeds_send: open settings; set range to 0.2; send "hi"; assert body.temperature === 0.2.
  - test_default_model_used: send untouched; assert body.model === "openai/gpt-4o".
</test_plan>

Tests live in: `apps/dashboard/tests-bff/chat-model-controls.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/chat/ModelPicker.tsx` · `apps/dashboard/components/chat/ModelControls.tsx` · `apps/dashboard/components/chat/ChatWorkspace.tsx` · `apps/dashboard/tests-bff/chat-model-controls.test.tsx` (the §4 red suite, declared up front for the scope anchor) · `apps/dashboard/tests-bff/mocks/handlers.ts` (shared INFRA: a baseline `/api/gw/v1/models` default handler so the frozen chat-workspace-page renders — which now mount ModelPicker — don't trip `onUnhandledRequest:"error"`; additive, no existing handler changed)
Strategy (ordered batches): 1. `ModelPicker.tsx` — useQuery /v1/models + native `<select>` + fail-open. 2. `ModelControls.tsx` — collapsed disclosure + system textarea + temperature range. 3. wire both into `ChatWorkspace.tsx` (lift state, feed send). Keep the default render to ONE textbox (controls collapsed) so the frozen chat-workspace-page tests stay green.
Safety rule (feature-specific): the picker MUST fail open (never block send on a models fetch error/empty); a blank system prompt MUST NOT inject an empty role:'system' turn; no new dependency (native select + range only).
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the contract; do NOT modify the frozen `use-chat-stream.ts` seam; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full suite **532 passed (64 files)**; chat-model-controls 6/6 + chat-workspace-page 12/12 (no regression).
- [x] coverage did not decrease — net-new components + 6 tests; tsc 0; eslint 0.
- [x] no test or contract was altered during build — frozen chat-workspace-page tests UNTOUCHED (the new mount-time /v1/models fetch is covered by an additive baseline mock handler, not a test edit); frozen `use-chat-stream.ts` seam unchanged.
- [x] the green was EARNED — manual adversarial self-review (small read-only FE; no subagent): fail-open verified (catch→`[value]`), blank-system omission verified (`system.trim()||undefined`→hook skips role:system), temperature & model captured from the real POST body. No overfit/vacuous asserts.
- [x] concurrency / timing safe — picker fetch is fire-once (`useEffect [] `, `active` guard on unmount); no shared mutable state; send guard unchanged.
- [x] no exposed secrets / injection / unexpected deps — cookie-only `bffGet`; NO new dependency (native `<select>` + `<input type=range>`); a blank system NEVER injects an empty role:'system' turn.
- [x] layering & deps follow CONVENTIONS.md — reuses bff-client + the frozen hook seam; WCAG-AA accessible names (Model / System prompt / Temperature / Model settings aria-expanded).
- [~] a person reviewed — auto-gate (full-auto drive); contract auto-frozen; manual self-review stood in. No security/architecture HARD-STOP.

### Build expectations — what "correct" looks like
- [x] Selecting a model drives the request — `test_picker_lists_and_drives_model`: chat POST body.model === selected id.
- [x] Picker fails open on /v1/models error — `test_picker_failopen_on_error`: default model still sends, no role=alert.
- [x] System prompt becomes messages[0] role:system; blank omits it — `test_system_prompt_feeds_send` + `test_blank_system_no_system_turn`.
- [x] Temperature reaches the body — `test_temperature_feeds_send`: body.temperature === 0.2.

### Deep checks
- [x] WIRING — ModelPicker + ModelControls imported & rendered by ChatWorkspace (header slot + composer); both feed `send`. `next build` OK.
- [x] DEAD-CODE — no orphan; ChatWorkspace `model` is now stateful (setModel wired to the picker).
- [x] SEMANTIC — n/a (code task).

### RESIDUE
- shadcn `Select` restyle of the native `<select>` picker — deferred polish delta (native chosen for jsdom reliability + a11y; visually plain).

### GATE RECORD
Outcome: PASS
Reviewed by: auto-gate (full-auto drive) + manual adversarial self-review · date: 2026-06-25

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
