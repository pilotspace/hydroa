# TASK: Per-turn token + cost readout

slug: chat-cost-readout · created: 2026-06-25 · stage: production
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
  - `apps/dashboard/components/chat/CostReadout.tsx` (NEW, `"use client"`) — a token-count readout pill. Props `{ sessionTokens:number; lastTurn?:Usage; className? }` (Usage imported from the frozen `use-chat-stream` seam). Renders the session total + the latest turn's tokens; an HONEST placeholder ("Session cost —") when `sessionTokens===0`. Tokens ONLY — NO "$" (the stream usage frame carries token counts, not price; we have no client-side per-model pricing, so a dollar figure would be FABRICATED — M6 honesty).
  - `apps/dashboard/components/chat/ChatWorkspace.tsx` (MODIFY) — re-add `usage` to the `useChatStream` destructure; accumulate `sessionTokens` across turns via `useEffect([usage])` guarded by a `countedRef` (idempotent per usage object — survives React StrictMode double-invoke); render `<CostReadout sessionTokens lastTurn={usage} />` in the header `data-slot="session-cost"` (replacing the static placeholder).
Context (working folder):
  - The frozen v40 t2 `useChatStream` exposes `usage?: Usage` = the LAST completed turn only (set once per `[DONE]` with a usage frame; `undefined` when a turn streamed no usage or on a fresh send). There is NO per-turn usage history on the frozen seam → per-bubble historical cost is OUT of scope here (a future hook-extension delta).
  - `Usage = { prompt_tokens, completion_tokens, total_tokens }` (owned by `use-chat-stream.ts`).
  - The header slot today shows a static "Session cost —" (the F2 honesty placeholder from t2) — this task makes it live.
Honors (patterns / conventions):
  - M6 cost honesty: never fabricate a number; absent usage ⇒ placeholder, never $0 or a guessed price.
  - Consume the frozen `useChatStream` seam; do NOT change the hook or `ChatMessage`.
  - WCAG-AA: the readout has an accessible label; design tokens.
Anchors the contract cites:
  - `CostReadout({ sessionTokens, lastTurn?, className? })` · `ChatWorkspace` session accumulation (`countedRef`-guarded `useEffect([usage])`).
  - `Usage` (from `@/lib/hooks/use-chat-stream`).
  - test harness `tests-bff/chat-cost-readout.test.tsx` (bff project; MSW SSE with/without usage frames).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Chat cost readout — the workspace surfaces token usage as turns complete: a running SESSION total plus the LATEST turn's tokens, in the header slot the chat page reserved. Fills the cost SLOT, honestly (token counts only).
Framings weighed: accumulate in ChatWorkspace via a guarded effect on the frozen `usage` (chosen — respects the frozen seam, no hook change) · extend the hook with a `usages: Usage[]` history (rejected — reopens the frozen t2 contract for per-bubble cost; deferred delta) · show a $ price (rejected — no client-side pricing ⇒ fabrication, violates M6).
Must:
<must>
  - M1 — `CostReadout({ sessionTokens, lastTurn?, className? })` renders the session total token count and, when `lastTurn` is present, that turn's tokens (prompt+completion). Token counts ONLY — it NEVER renders a "$" / price.
  - M2 — when `sessionTokens===0` (no completed usage yet), `CostReadout` shows an honest placeholder ("Session cost —"), never a fabricated 0-cost or price.
  - M3 — `ChatWorkspace` accumulates `sessionTokens` by adding each completed turn's `usage.total_tokens` EXACTLY once (idempotent per usage object — a `countedRef` guard survives StrictMode/re-render), and renders `<CostReadout>` in the header `data-slot="session-cost"`.
  - M4 — a turn that completes with NO usage frame contributes nothing to the session total (no fabricated count); the readout stays at the last real total.
</must>
Reject:
<reject>
  - absent usage (turn streamed no usage frame, or before the first turn) -> placeholder / unchanged total; NEVER a fabricated number or $.
  - a re-render with the same usage object -> MUST NOT double-count (countedRef idempotency).
</reject>
After:
<after>
  - After N turns each reporting usage, the header reads the summed total tokens; the latest turn's tokens are shown alongside; no price is ever displayed.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ accumulating on `useEffect([usage])` counts each turn exactly once — lowest confidence because React StrictMode double-invokes effects and `usage` is cleared→re-set each turn; if wrong: the session total double-counts. Mitigation + pin: a `countedRef` compares usage-object identity (only a NEW usage object adds), tested with two turns asserting an EXACT sum.
  - [x] `Usage` is exported from `use-chat-stream.ts` — CONFIRMED (frozen t2 export).
  - [x] no $ pricing is available client-side — CONFIRMED: the usage frame carries token counts only; tokens are the honest readout.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Session total accumulates across turns
  Given turn 1 completes with usage total 7 and turn 2 with usage total 15
  When both turns finish
  Then the header readout shows 22 tokens

Scenario: Latest turn shown alongside the session total
  Given a turn completes with prompt 5 + completion 2
  When it finishes
  Then the readout shows the latest turn's 7 tokens

Scenario: Honest placeholder before any usage
  Given no turn has completed
  When the chat page renders
  Then the readout shows a placeholder (no number, no $)

Scenario: No $ is ever fabricated
  Given any completed turn with usage
  When the readout renders
  Then it shows token counts and NEVER a "$" price

Scenario: A usage-less turn does not change the total
  Given the session total is 7 and a turn completes with no usage frame
  When it finishes
  Then the total remains 7
  And no fabricated count is added

Scenario: Re-render does not double-count
  Given a turn counted total 7
  When the component re-renders with the same usage object
  Then the total remains 7
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
// ─ Component (shape frozen here) ─
CostReadout(props: { sessionTokens: number; lastTurn?: Usage; className?: string })
  Usage = { prompt_tokens, completion_tokens, total_tokens }   // imported from use-chat-stream (frozen)
  render: sessionTokens>0 ? `${sessionTokens.toLocaleString()} tokens` (+ ` · last ${prompt+completion}` when lastTurn)
                          : honest placeholder "Session cost —"
  INVARIANT: tokens only — never renders "$"

// ─ ChatWorkspace accumulation (modified; no hook change) ─
const [sessionTokens, setSessionTokens] = useState(0)
const countedRef = useRef<Usage>()
useEffect(() => { if (usage && usage !== countedRef.current) { countedRef.current = usage; setSessionTokens(t => t + usage.total_tokens) } }, [usage])
header slot -> <CostReadout sessionTokens={sessionTokens} lastTurn={usage} />

Schema: none — client-side only; derives from the frozen useChatStream().usage (no endpoint, no hook change).
```

Status: FROZEN @ v1 — auto-approved (full-auto drive; non-high-risk FE, honesty-bound, no security surface) 2026-06-25
Least-sure flag surfaced at freeze:
  - [contract] ⚠ idempotent accumulation under StrictMode/re-render — a `countedRef` identity guard prevents double-count; pinned by `test_session_total_accumulates` (exact sum) + `test_rerender_no_double_count`.
  - [spec] honesty: tokens-only, placeholder-when-empty — a $ figure would be fabricated; pinned by `test_no_dollar_sign` + `test_placeholder_before_usage`.
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
  - test_session_total_accumulates: stream turn1 (usage total 7) then turn2 (total 15) through ChatWorkspace; assert the header readout shows "22".
  - test_latest_turn_tokens_shown: stream a turn (prompt 5 + completion 2); assert the readout shows the latest 7.
  - test_placeholder_before_usage: render ChatWorkspace; assert the readout shows "Session cost —" (no digit).
  - test_no_dollar_sign: stream a turn with usage; assert the readout text contains no "$".
  - test_usageless_turn_no_change: stream turn1 (total 7), then a turn with NO usage frame; assert readout still "7".
  - test_rerender_no_double_count: render CostReadout-driving ChatWorkspace; after one usage turn (7), re-render; assert still "7" (countedRef idempotency).
</test_plan>

Tests live in: `apps/dashboard/tests-bff/chat-cost-readout.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/chat/CostReadout.tsx` · `apps/dashboard/components/chat/ChatWorkspace.tsx` · `apps/dashboard/tests-bff/chat-cost-readout.test.tsx` (the §4 red suite, declared up front for the scope anchor)
Strategy (ordered batches): 1. `CostReadout.tsx` — token-only pill + honest placeholder. 2. wire `ChatWorkspace.tsx` — re-add `usage` to the destructure, add `countedRef`-guarded session accumulation, render CostReadout in the header slot.
Safety rule (feature-specific): NEVER render a "$"/price (no client pricing ⇒ fabrication); accumulate each usage object exactly once (countedRef idempotency); absent usage ⇒ no change.
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the contract; do NOT modify the frozen `use-chat-stream.ts`; allow-list packages only (no new deps); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full suite **538 passed (65 files)**; chat-cost-readout 6/6 + the two sibling chat suites green (no regression).
- [x] coverage did not decrease — net-new component + 6 tests; tsc 0; eslint 0.
- [x] no test or contract altered during build — frozen `use-chat-stream.ts` + sibling tests untouched; the header placeholder span was REPLACED by the live readout (its own scope).
- [x] the green was EARNED — manual adversarial self-review (small honesty-bound FE): exact-sum accumulation (7→22), idempotency on re-render (still 7), absent-usage no-change (still 7), and the no-`$` invariant all asserted on real streamed turns.
- [x] concurrency / timing safe — `countedRef` identity guard makes accumulation idempotent per usage object (StrictMode/re-render safe); no shared mutable state.
- [x] no exposed secrets / injection / unexpected deps — derives only from the frozen `usage`; NO new dependency; NO `$`/price ever rendered (honesty invariant).
- [x] layering & deps follow CONVENTIONS.md — consumes the frozen seam; accessible label ("Session token usage"); design tokens.
- [~] a person reviewed — auto-gate (full-auto drive) + manual self-review; no security/architecture HARD-STOP.

### Build expectations — what "correct" looks like
- [x] Session total sums per-turn usage — `test_session_total_accumulates`: 7 then 22.
- [x] Latest turn shown — `test_latest_turn_tokens_shown`: "· last 7".
- [x] Honest placeholder before usage; never a $ — `test_placeholder_before_usage` (no digit) + `test_no_dollar_sign`.
- [x] Usage-less turn adds nothing; re-render doesn't double-count — `test_usageless_turn_no_change` + `test_rerender_no_double_count`.

### Deep checks
- [x] WIRING — CostReadout imported + rendered in the ChatWorkspace header slot; fed by `sessionTokens` (effect-accumulated) + `usage`. `next build` OK.
- [x] DEAD-CODE — the static placeholder span removed; `usage` re-added to the destructure and used.
- [x] SEMANTIC — n/a.

### RESIDUE
- Per-bubble historical turn cost is OUT of scope (the frozen seam has no per-turn usage history) → future delta: extend useChatStream with `usages: Usage[]` (additive) to label each assistant bubble; and a real $ cost would need a client-exposed pricing source.

### GATE RECORD
Outcome: PASS
Reviewed by: auto-gate (full-auto drive) + manual adversarial self-review · date: 2026-06-26

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
