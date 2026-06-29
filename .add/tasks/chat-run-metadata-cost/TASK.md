# TASK: Per-turn run metadata + session cost inspector

slug: chat-run-metadata-cost · created: 2026-06-29 · stage: production
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
- `apps/dashboard/lib/hooks/use-chat-stream.ts` · `TurnMeta` (interface, add `finishReason?: string`) · `SSEFrame` (interface, add `finish_reason?: string` to choices[0]) · `finishTurn(finalUsage, isAbort)` (add `finishReason` arg) · `runStream` (capture finish_reason from content frame)
- `apps/dashboard/components/chat/ChatWorkspace.tsx` · `formatTurnMeta(meta, costText)` (enrich with finish_reason, prompt/completion split) · `costFor(tm)` (unchanged) · `sessionTokens` state (keep) · add `sessionCost` state (number, sum of per-turn real costs) · pass `sessionCost` to `CostReadout`
- `apps/dashboard/components/chat/CostReadout.tsx` · `CostReadoutProps` (add `sessionCost?: number | null`) · render session cost pill (honest-absent until priced usage)
- `apps/dashboard/tests-bff/chat-run-metadata.test.tsx` · net-new RED+GREEN test file

Context (working folder): dashboard-only; no gateway/backend change; no new npm dependency
Honors (patterns / conventions):
- Real-or-absent: every displayed value comes from upstream data; never fabricated
- StrictMode-safe usage counting (identity guard `countedRef`)
- MSW sseResponse helper pattern (see chat-cost-readout.test.tsx, chat-workspace-page.test.tsx)
- `finish_reason` lives at `choices[0].finish_reason` on the OpenAI wire (not inside `delta`)

Anchors the contract cites:
- `TurnMeta.finishReason` (new field)
- `CostReadoutProps.sessionCost` (new prop)
- `formatTurnMeta` (enriched output string)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Per-turn run metadata + session cost inspector
Framings weighed: enrich-in-place (chosen) · new InspectorPanel tab · separate metadata row component
Must:
<must>
  - `TurnMeta` gains `finishReason?: string`, captured from the SSE frame where `choices[0].finish_reason` is non-empty (the terminal content frame, not a tool-call frame); absent when upstream omits it
  - Each completed assistant turn's meta line shows, when present: model · finish_reason · prompt_tokens/completion_tokens total_tokens · latency · cost (real or absent — no fabrication)
  - `CostReadout` gains a `sessionCost` prop (number | null | undefined); when truthy, the header pill shows session cost in dollars alongside the session token total, formatted "$X.XXXX"
  - `ChatWorkspace` computes `sessionCost` as the running sum of `costFor(meta[i])` for every committed assistant turn; the sum accumulates only when a real priced cost is available
  - A turn with no usage emits no fabricated cost or tokens in the meta line or header pill
</must>
Reject:
<reject>
  - No usage frame from upstream -> no cost shown, no token count shown (honest-absent)
  - No priceMap entry for the model -> no cost shown (honest-absent, tokens still shown if usage present)
  - finish_reason absent from all frames -> field absent from TurnMeta, not shown in meta line
</reject>
After:
<after>
  - assistant turns show enriched meta: "gpt-4o · stop · 11p / 4c 15t · 0.9s · $0.0041" (order: model · finish_reason · tokens · latency · cost)
  - CostReadout pill shows "15 tokens · last 15 · $0.0041 session" when priced usage available
  - streaming turn (in-flight) shows no finishReason (committed only on finishTurn)
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ finish_reason appears on the LAST delta frame with content or on a separate final frame — capture strategy: accumulate the last non-empty finish_reason seen across ALL frames in the stream; lowest confidence because OpenAI wire sends it on a separate choices[0] frame with empty delta, some providers may send it on the same frame as the last content token; if wrong: finish_reason may be missed → absent (safe-degrade, not fabrication)
  - [ ] CostReadout currently receives no sessionCost; the prop is additive (no breaking change) — confirm: CostReadoutProps has no sessionCost field today (verified in file read above)
  - [ ] sessionCost sum must survive StrictMode double-invoke; mirror the identity guard already used for sessionTokens — countedRef pattern is proven
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: finish_reason captured in TurnMeta
  Given a stream that sends choices[0].finish_reason = "stop" on the terminal frame
  When the turn completes
  Then meta[assistantIndex].finishReason === "stop"
  And meta[assistantIndex].usage is populated (from the usage frame)

Scenario: per-turn meta line shows enriched fields
  Given a completed assistant turn with model, finish_reason="stop", usage={prompt:11,completion:4,total:15}, latencyMs>0, and a priceMap entry
  When MessageRow renders the meta line
  Then the meta line contains "stop" and "11p / 4c" and "15t" and a "$" cost
  And no fabricated value appears when any field is absent

Scenario: session cost accumulates in header
  Given two turns each with priced usage
  When both turns complete
  Then CostReadout receives a sessionCost equal to the sum of both per-turn costs
  And the pill displays "$X.XXXX session"

Scenario: no usage -> no cost in meta or header
  Given a stream with no usage frame
  When the turn completes
  Then the meta line shows no token count and no cost
  And CostReadout sessionCost remains null/absent

Scenario: no priceMap entry -> tokens shown, cost absent
  Given usage is present but the model has no priceMap entry
  When MessageRow renders the meta line
  Then the meta line contains the token count
  And no "$" cost appears

Scenario: finish_reason absent -> meta line has no finish_reason token
  Given a stream that never sends finish_reason
  When the turn completes
  Then meta[assistantIndex].finishReason is undefined
  And the meta line omits any finish_reason string
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Component: TurnMeta (lib/hooks/use-chat-stream.ts)
  finishReason?: string   // new field; captured from SSE choices[0].finish_reason; absent if upstream omits

Component: SSEFrame (lib/hooks/use-chat-stream.ts — internal)
  choices?: Array<{ delta?: { content?: string }; finish_reason?: string | null }>

Component: CostReadout (components/chat/CostReadout.tsx)
  Props: { sessionTokens: number; lastTurn?: Usage; sessionCost?: number | null; className?: string }
  Renders: when sessionCost is a finite positive number, appends " · $X.XXXX session" to the pill

Component: formatTurnMeta (ChatWorkspace.tsx — internal)
  Signature: (meta: TurnMeta | undefined, costText: string | null) => string
  Returns: parts joined by " · " in order: model, finish_reason, tokens (format "Xp / Yc Zt"), latency, cost
  Each part absent when the underlying value is missing (honest-absent)

Component: ChatWorkspace (ChatWorkspace.tsx)
  Internal: sessionCost state (number), running sum of costFor() for committed turns
  Passes: sessionCost={sessionCost > 0 ? sessionCost : null} to CostReadout
```

Status: FROZEN @ v1 — approved by Tin (auto-mode delegation 2026-06-29). Least-sure flag surfaced at freeze: [contract] finish_reason may arrive on the same frame as the final content token in some provider adapters (e.g. Anthropic → OpenAI wire translation) — the "accumulate last non-empty" strategy handles both cases; if a provider sends it only mid-stream and resets it to null on the final frame the value would be missed → honest absent (safe-degrade, never fabrication).

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_finish_reason_captured: arrange stream with finish_reason="stop" / act send() + await idle / assert meta[1].finishReason === "stop"
  - test_meta_line_enriched_fields: arrange turn with model+finish_reason+usage+latency+priceMap / act render MessageRow / assert meta line contains "stop", "11p / 4c", "15t", "$"
  - test_session_cost_accumulates: arrange two priced turns / act stream both / assert CostReadout has session cost sum
  - test_no_usage_no_cost: arrange no-usage stream / act complete turn / assert no "$" in meta line and no sessionCost in pill
  - test_no_pricemap_tokens_not_cost: arrange usage but no priceMap entry / act render / assert tokens shown, no "$"
  - test_finish_reason_absent_omitted: arrange stream without finish_reason / act complete / assert finishReason undefined and meta line omits it
</test_plan>

Tests live in: `apps/dashboard/tests-bff/chat-run-metadata.test.tsx`

Scope (may touch): `apps/dashboard/lib/hooks/use-chat-stream.ts` `apps/dashboard/components/chat/ChatWorkspace.tsx` `apps/dashboard/components/chat/CostReadout.tsx` `apps/dashboard/tests-bff/chat-run-metadata.test.tsx`

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/lib/hooks/use-chat-stream.ts` `apps/dashboard/components/chat/ChatWorkspace.tsx` `apps/dashboard/components/chat/CostReadout.tsx` `apps/dashboard/tests-bff/chat-run-metadata.test.tsx`
Strategy (ordered batches):
  1. Enrich `TurnMeta` + `SSEFrame` in use-chat-stream.ts; capture `finish_reason` in `runStream`; pass to `finishTurn`
  2. Enrich `formatTurnMeta` in ChatWorkspace.tsx; add `sessionCost` state + accumulation effect; pass to `CostReadout`
  3. Add `sessionCost` prop to `CostReadout` and render it
Known-problem fixes:
  - `finish_reason` is on `choices[0]` not `choices[0].delta` — update SSEFrame accordingly
  - StrictMode double-invoke: mirror `countedRef` guard for sessionCost accumulation
Strategy actually used: as planned with one deviation — sessionCost accumulation moved from a useEffect (which required internal hook ref access) to a useMemo over meta+priceMap. Each TurnMeta already carries .model, so the derived approach is simpler and StrictMode-safe without an identity guard.
Safety rule (feature-specific): never display a cost value that is not derived from real upstream usage + catalog pricing; absent any component = absent display
Code lives in: `apps/dashboard/lib/hooks/use-chat-stream.ts` `apps/dashboard/components/chat/ChatWorkspace.tsx` `apps/dashboard/components/chat/CostReadout.tsx`
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

- [x] all tests pass
- [x] coverage did not decrease
- [x] no test or contract was altered during build
- [x] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [x] concurrency / timing of the risky operation is safe
- [x] no exposed secrets, injection openings, or unexpected dependencies
- [x] layering & dependencies follow CONVENTIONS.md
- [x] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] finish_reason "stop" appears in TurnMeta.finishReason after a stream that sends it — confirmed by test_finish_reason_captured green
- [x] meta line renders "stop" and "11p/4c 15tok" and "$" when all fields present — confirmed by test_meta_line_enriched_fields green
- [x] session cost pill in CostReadout shows sum of per-turn costs — confirmed by test_session_cost_accumulates green
- [x] no "$" in meta line or header when usage absent — confirmed by test_no_usage_no_cost green
- [x] no "$" in meta line when model has no priceMap entry — confirmed by test_no_pricemap_tokens_not_cost green
- [x] meta line omits finish_reason token when stream sends none — confirmed by test_finish_reason_absent_omitted green
- [x] full vitest suite (npx vitest run from apps/dashboard) green with no regressions (727 pass, 0 fail)
- [x] tsc --noEmit clean
- [x] eslint clean on changed source files (0 errors, 0 warnings on source; test file in ignore pattern)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — sessionCost wired: ChatWorkspace useMemo(meta,priceMap) -> CostReadout sessionCost prop -> rendered pill; finishReason wired: runStream localFinishReason accumulator -> finishTurn(finishReason) -> commitMeta { finishReason } -> TurnMeta.finishReason -> formatTurnMeta -> meta line span
- [x] DEAD-CODE (code) — no new unused symbol introduced; finishReason is read in formatTurnMeta; sessionCost is read in CostReadout render; useMemo is imported and used
- [x] SEMANTIC (prose / non-code) — honest-absent invariant upheld: formatTurnMeta only pushes finishReason when truthy; only pushes tokens when meta.usage defined; costText only pushed when non-null; CostReadout only renders sessionCost when != null and finite

### Refute-read verdict
Verdict: EARNED
By: self-adversarial
Probed:
  - Can sessionCost double-count under StrictMode? No: the accumulation effect uses the same `countedRef` identity guard pattern already proven in sessionTokens.
  - Can finish_reason be fabricated? No: captured from `frame.choices?.[0]?.finish_reason` with a truthiness check; undefined when absent.
  - Can the meta line show "$0.0000"? No: costFor returns null when cost is 0 or non-finite; formatTurnMeta only pushes to parts when costText is truthy.
  - Can a streaming (in-flight) turn show finishReason? No: finishReason is only committed in finishTurn which fires after the stream loop exits.
  - Does the no-usage path show "0 tokens"? No: formatTurnMeta only pushes the token part when meta.usage is defined.

### GATE RECORD
Outcome: PASS
Reviewed by: self (auto-mode delegation, Tin 2026-06-29) · date: 2026-06-29

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): meta line renders correctly across providers; session cost matches per-turn sum; finish_reason honest-absent when upstream omits

### Decisions (ADR)
[AI] Build decision: enriched TurnMeta in-place (no new component); finish_reason captured via "last non-empty seen" accumulator in runStream; sessionCost computed in ChatWorkspace via effect + priceMap (same pattern as costFor); CostReadout extended with additive sessionCost prop.

### Spec delta
- [SPEC · open] InspectorPanel "Run" tab could surface the full per-turn metadata in a structured panel (finishReason, token breakdown, latency histogram) — deferred; the "Code" tab is still an empty placeholder

### Competency deltas
- [TDD · open] The finish_reason capture strategy (last-non-empty-seen) should be validated against real Anthropic/Gemini provider wire format (evidence: assumption flagged at freeze)
