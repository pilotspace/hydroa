# TASK: Chat composer web-search toggle → web_search flag

slug: chat-websearch-toggle · created: 2026-06-26 · stage: production
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
  - `apps/dashboard/lib/hooks/use-chat-stream.ts` (MODIFY — ADDITIVE to the v40-frozen seam) — `SendInput` gains `webSearch?: boolean`; `runStream` body adds `...(input.webSearch ? { web_search: true } : {})` (body-build @ ts:112-118). Everything else byte-identical.
  - `apps/dashboard/components/chat/ModelControls.tsx` (MODIFY) — add a labeled "Web search" toggle (a checkbox/switch) alongside the existing system-prompt + temperature controls; props gain `webSearch`/`onWebSearchChange`.
  - `apps/dashboard/components/chat/ChatWorkspace.tsx` (MODIFY) — hold `webSearch` state, pass it to `<ModelControls>` and into `send({ …, webSearch })`.
  - `apps/dashboard/tests-bff/chat-websearch-toggle.test.tsx` (NEW) — MSW captures the chat POST body; assert default-off → no web_search, toggle-on → web_search:true.
Context (working folder):
  - The frozen v40 `useChatStream` seam: `SendInput = {model,text,system?,temperature?}`; `send()` → `runStream` POSTs to `/api/gw/v1/chat/completions`. v40 MILESTONE explicitly deferred the web-search toggle to v41, so this ADDITIVE optional extension is anticipated (no existing caller breaks; off ⇒ identical body).
  - v41 t1 (committed) makes the gateway honor `web_search:true`; this task supplies it from the UI.
  - tests-bff harness: jsdom @ localhost:3000, MSW intercepts the chat POST; pattern mirrors `tests-bff/chat-model-controls.test.tsx` (captures the request body).
Honors (patterns / conventions):
  - FAIL-SAFE/byte-identical-when-off (mirrors v40 + t1): toggle off ⇒ no `web_search` key in the body.
  - WCAG-AA: the toggle is keyboard-operable + labeled (aria); v23/v24 design-token bar.
  - Additive-only to a frozen seam (new optional field) — never change the existing `SendInput` fields or the off-path body.
Anchors the contract cites:
  - `SendInput.webSearch?` · the `runStream` body conditional · `ModelControls` webSearch props · `ChatWorkspace` webSearch state → send().

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Chat web-search toggle. A labeled, keyboard-operable "Web search" toggle in the chat composer controls; when ON, the chat POST carries `web_search:true` (which v41 t1 translates to native grounding); when OFF the body is byte-identical to today.
Framings weighed: additive optional `SendInput.webSearch?` + toggle in ModelControls (chosen — smallest seam change, keeps the frozen composer structure, web-search IS a per-request control) · a visible composer button left of Send (deferred — risks frozen v40 composer tests; promote later) · overloading `tools` from the UI (rejected — t1 owns the flag→tool translation).
Must:
<must>
  - M1 — a "Web search" toggle is present in the chat controls, labeled + keyboard-operable (a11y).
  - M2 — with the toggle ON, `send()` carries `webSearch:true` and the chat POST body includes `web_search:true`.
  - M3 — with the toggle OFF (default), the POST body contains NO `web_search` key — byte-identical to v40.
  - M4 — the `SendInput` extension is ADDITIVE only: existing fields + the off-path body are unchanged (the frozen v40 chat/cost/model tests stay green).
</must>
Reject:
<reject>
  - toggle OFF / never touched -> no `web_search` key in the body (M3).
  - the extension must not alter any existing SendInput field or the streaming/usage behavior (M4).
</reject>
After:
<after>
  - The user can toggle web search in `/app/chat`; flagged sends reach the gateway with `web_search:true`; off-path behavior is unchanged.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ adding the toggle to ModelControls (not a visible composer button) won't break the frozen v40 chat tests — lowest confidence because those tests assert composer/controls structure; if wrong: a frozen test goes red. Cost bounded: ModelControls already has controls + its own test suite; I'll run the full tests-bff suite to confirm no v40 regression before gate.
  - [x] the v40 seam tolerates an additive optional field — CONFIRMED (TypeScript optional; off-path body identical).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Toggle present and labeled
  Given the chat workspace is open
  When the user opens the chat controls
  Then a keyboard-operable control labeled "Web search" is present

Scenario: Toggle on sends the flag
  Given the user turns Web search on
  When the user sends a message
  Then the chat POST body includes web_search: true

Scenario: Toggle off is byte-identical
  Given Web search is off (default)
  When the user sends a message
  Then the chat POST body has NO web_search key
  And model/messages/stream/stream_options are unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
// ─ Frozen v40 seam, extended ADDITIVELY ─
interface SendInput {            // v40 fields UNCHANGED
  model: string; text: string; system?: string; temperature?: number;
  webSearch?: boolean;           // NEW — optional; default undefined ⇒ off
}
// runStream body (the only behavioral change):
//   { model, messages, stream:true, stream_options, ...(temperature?), 
//     ...(input.webSearch ? { web_search: true } : {}) }   // off ⇒ key absent

// ─ ModelControls props (extended) ─
ModelControls({ system, onSystemChange, temperature, onTemperatureChange,
                webSearch, onWebSearchChange })   // a labeled checkbox/switch

// ─ ChatWorkspace ─
const [webSearch, setWebSearch] = useState(false)
<ModelControls … webSearch={webSearch} onWebSearchChange={setWebSearch} />
send({ model, text, system, temperature, webSearch })

Schema: none — UI + request-body only; consumes the v41 t1 gateway flag. No new dep.
```

Status: FROZEN @ v1 — auto-approved (full-auto; additive-only optional field + default-off ⇒ off-path byte-identical; the v40 MILESTONE pre-authorized this v41 extension) 2026-06-26
Least-sure flag surfaced at freeze:
  - [test] placing the toggle in ModelControls vs a visible composer button — must not regress the frozen v40 chat/cost/model tests; the full tests-bff suite run at the gate is the check (off-path body identical is the M3/M4 pin).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral — one test per scenario; zero v40 regression (full tests-bff green).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_toggle_present: render ChatWorkspace, open controls → a control labeled /web search/i exists and is focusable.
  - test_toggle_on_sends_flag: MSW captures the chat POST; toggle on, type + send → captured body.web_search === true.
  - test_toggle_off_no_flag: send without toggling → "web_search" NOT in captured body (and model/messages/stream present unchanged).
</test_plan>

Tests live in: `apps/dashboard/tests-bff/chat-websearch-toggle.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/lib/hooks/use-chat-stream.ts` · `apps/dashboard/components/chat/ModelControls.tsx` · `apps/dashboard/components/chat/ChatWorkspace.tsx` · `apps/dashboard/tests-bff/chat-websearch-toggle.test.tsx`
Strategy (ordered batches): 1. extend SendInput + runStream body (off ⇒ identical). 2. ModelControls toggle + props. 3. ChatWorkspace state + wiring. 4. tests-bff suite.
Safety rule (feature-specific): ADDITIVE-only — never alter an existing SendInput field or the off-path body; the frozen v40 chat/cost/model tests-bff suites MUST stay green.
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the contract; do NOT alter existing SendInput fields or the off-path body; allow-list packages only (no new deps); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — new suite 3/3; FULL tests-bff 541 passed (66 files; was 538 in v40, +3) — zero v40 regression; tsc clean; eslint 0 errors; `next build` OK with `/app/chat` prerendered.
- [x] coverage did not decrease — +3 behavioral tests; no suite removed.
- [x] no test or contract was altered during build — frozen v40 chat/cost/model suites untouched + still green; only an ADDITIVE optional `SendInput.webSearch?`.
- [x] the green was EARNED — adversarial self-review: the byte-identical-OFF invariant (the only real risk) is pinned by `test_toggle_off_no_flag` (asserts no `web_search` key + `stream:true` + user message intact); the on-path asserts the captured POST body, not internals. Low-risk additive FE (the high-risk BE half was t1, which got the independent refute-read).
- [x] concurrency / timing safe — pure React state; no async/shared-state change; streaming/usage path untouched.
- [x] no exposed secrets, injection openings, or unexpected dependencies — UI + request-body only; no new packages; cookie-only BFF auth unchanged.
- [x] layering & dependencies follow CONVENTIONS.md — state lifted to ChatWorkspace; ModelControls stays presentation; the hook owns the wire.
- [x] a person reviewed and approved the change — full-auto drive; adversarial self-review at the gate; additive + default-off keeps it auto-gateable.

### Build expectations — what "correct" looks like
- [x] Toggle ON ⇒ chat POST body has `web_search:true` — confirmed by `test_toggle_on_sends_flag` (captured body.web_search === true).
- [x] Toggle OFF (default) ⇒ NO `web_search` key, v40 fields intact — confirmed by `test_toggle_off_no_flag` + the full v40 suite staying green.
- [x] A labeled, keyboard-operable "Web search" control exists — confirmed by `test_toggle_present` (role=checkbox, name=/web search/i, not disabled) + the visible label/help text.

### Deep checks
- [x] WIRING — `SendInput.webSearch?` consumed in `runStream` body; `ModelControls` webSearch props rendered as the checkbox; `ChatWorkspace` state threads to both ModelControls and `send()`. tsc proves the required props are supplied everywhere ModelControls renders.
- [x] DEAD-CODE — no orphaned symbols; every new prop/state referenced.
- [x] SEMANTIC — n/a (code task); the off-path byte-identical claim was verified by test + full-suite green.

### Residue / deltas
- The toggle lives in the collapsed "Model settings" disclosure (kept the frozen composer structure). Promote to a visible composer affordance = polish delta.
- Rich in-thread citation RENDERING (source cards from response["grounding"]) deferred (milestone-level delta; the BE passes citations through).

### GATE RECORD
Outcome: PASS
Reviewed by: full-auto drive + adversarial self-review · date: 2026-06-26

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
