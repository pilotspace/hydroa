# TASK: Translate OpenAI-wire reasoning_effort to Anthropic thinking / Gemini thinkingConfig + reasoning-token accounting

slug: reasoning-passthrough · created: 2026-06-23 · stage: production
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

All paths under `apps/gateway/`. Nothing reads reasoning/thinking today (grep: zero matches). Billing is already wired — NO recorder change needed. OpenRouter/OpenAI/Azure forward the body verbatim → `reasoning_effort` already flows for them (no change). This task changes ONLY the Anthropic + Gemini translators.

Touches (files · symbols · signatures):
- `src/gateway/proxy/infrastructure/anthropic_upstream.py:125` `_openai_to_anthropic_request(payload, *, default_max_tokens) -> dict` — REQUEST: insert a `thinking:{type:"enabled", budget_tokens:N}` block (after top_p ~line 200). default_max_tokens=4096 (ctor line 535) → budget_tokens MUST be < max_tokens (D2).
- `:260` `_anthropic_to_openai(body) -> dict` — RESPONSE: usage built lines 292–331 (only prompt/completion/total, no completion_tokens_details); `type=="thinking"` content blocks currently DROPPED (loop 268–280). Anthropic returns NO native reasoning-token count — thinking folded into output_tokens (D3).
- `:359` `_AnthropicSSEStepper` (`step()` @423, `_emit_terminal` @412) — STREAM: handles only text_delta/input_json_delta; `thinking_delta`/thinking `content_block_start` ignored. Terminal usage built ~412.
- `src/gateway/proxy/infrastructure/gemini_upstream.py:126` `_openai_to_gemini_request(payload, *, default_max_tokens) -> dict` — REQUEST: `generationConfig` built 192–213; insert `thinkingConfig:{thinkingBudget:N}` (after stopSequences ~201).
- `:~280` `_gemini_to_openai(body)` — RESPONSE: usage 282–312; `usageMetadata.thoughtsTokenCount` is AUTHORITATIVE but currently DROPPED → map to `completion_tokens_details.reasoning_tokens`.
- `_GeminiSSEStepper.finish()` (~427–445) — STREAM terminal usage; same `thoughtsTokenCount` drop.
- `src/gateway/usage/application/recorder.py:204` `_safe_tier(usage,"completion_tokens_details","reasoning_tokens")` + bill @216–226 via `compute_per_token_cost_usd(reasoning_tokens=, reasoning_price=)` (~512–567; reasoning_price falls back to completion_price if NULL; tiered split clamps reasoning≤completion). CONFIRMED: if translators populate `completion_tokens_details.reasoning_tokens`, billing works with NO recorder change.

Context (working folder):
- Verbatim-forward (no change): openrouter_upstream.py:192/271 (`json=outbound`), openai_provider.py:123/152, azure_upstream.py:147/189 (all `json=payload`).
- Helios sends `reasoning_effort:"high"` and/or `reasoning:{effort}` (../helios-mono convert.rs:513/519/530) — OpenAI-wire. Tests use the v34 harness (`tests/_helios_harness`, `helios_request("reasoning_effort")`, SEAM A `_anthropic_to_openai`/`_openai_to_anthropic_request` + SEAM C real adapter via MockTransport).
- pricing_snapshots.reasoning_usd_per_token must be populated per Anthropic/Gemini model for reasoning billing (else falls back to completion_price — safe).

Honors (patterns / conventions):
- Translate OpenAI-wire → provider-native; client never speaks native (v34 shared decision). Byte-identical default path: a request WITHOUT reasoning fields engages ZERO new code (v9/v10 invariant).
- Estimate honesty: where no native count exists, follow the project's documented-estimate pattern (cf. Gemini-embeddings `max(1,ceil(chars/4))`) — never silently fabricate billable tokens.
- No outbound IO change; no migration; design-for-failure (malformed reasoning field → ignore, never crash a translator — cf. tool_translation fail-safe).

Anchors the contract cites: `_openai_to_anthropic_request` (:125) · `_anthropic_to_openai` (:260) · `_AnthropicSSEStepper` (:359) · `_openai_to_gemini_request` (:126) · `_gemini_to_openai` · `_GeminiSSEStepper.finish` · the OpenAI-wire fields `reasoning_effort` / `reasoning.effort` · `completion_tokens_details.reasoning_tokens` · Anthropic `thinking.budget_tokens` · Gemini `thinkingConfig.thinkingBudget` · `usageMetadata.thoughtsTokenCount`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: reasoning/thinking passthrough — translate OpenAI-wire `reasoning_effort` / `reasoning.effort` into Anthropic extended-thinking and Gemini thinkingConfig, and surface provider reasoning-token counts for billing.

Framings weighed: translate-effort→native-budget (chosen) · require-client-native-thinking-fields (rejected — breaks the OpenAI-wire contract Helios speaks) · passthrough-only-no-token-accounting (rejected — reasoning cost stays invisible).

Must:
<must>
  - ANTHROPIC request: when the inbound body carries `reasoning_effort` (str) or `reasoning.effort` (str) ∈ {low,medium,high}, add `thinking:{type:"enabled", budget_tokens:N}` per the D1 map; guarantee `max_tokens > budget_tokens` (D2: bump max_tokens to budget_tokens + the requested-or-default answer room).
  - GEMINI request: same trigger → `generationConfig.thinkingConfig.thinkingBudget = N` (D1 Gemini map).
  - GEMINI response (non-stream + stream terminal): map `usageMetadata.thoughtsTokenCount` → `completion_tokens_details.reasoning_tokens` (AUTHORITATIVE) so the existing recorder bills it.
  - ANTHROPIC response: per D3 (chosen at freeze) — surface reasoning-token accounting; recommended default = do NOT fabricate a count (thinking already inside output_tokens→completion_tokens, billed at completion rate), so no `reasoning_tokens` is emitted for Anthropic. Either way: thinking content MUST NOT corrupt the assistant message (thinking blocks are not emitted as user-visible content).
  - PASSTHROUGH (OpenRouter/OpenAI/Azure): unchanged — `reasoning_effort` already flows verbatim.
  - BYTE-IDENTICAL: a request with NO reasoning field produces output identical to today (engages zero new code).
</must>
Reject:
<reject>
  - `reasoning_effort` present but not in {low,medium,high} -> "reasoning_effort_unrecognized" — fail-SAFE: drop the field, forward WITHOUT thinking, log WARN (never 400 the client; an unknown knob must not break a coding session)
  - `reasoning` present but malformed (not a dict, or `.effort` not a str) -> "reasoning_field_malformed" — same fail-safe drop + WARN
</reject>
After:
<after>
  - A Helios `reasoning_effort` request to an Anthropic/Gemini model activates native extended thinking; reasoning tokens are recorded for billing (Gemini authoritative; Anthropic per D3); non-reasoning requests stay byte-identical; passthrough providers unchanged.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [D3] Anthropic reasoning-token accounting — recommend NOT fabricating a token estimate: Anthropic returns no native thinking-token count and the tokens are already inside output_tokens (→ completion_tokens, billed at completion_price). Lowest confidence because if you WANT the thinking portion repriced at reasoning_price for Anthropic, we must char-estimate (~4 chars/token), which injects billing error. If wrong: switch to a documented char-estimate — isolated to `_anthropic_to_openai` + the SSE stepper.
  ⚠ [D2] max_tokens bump — when thinking is enabled I bump max_tokens so the answer has room (budget + requested/default), changing the client's effective ceiling. Alternative: clamp budget to max_tokens-1 (keeps the client ceiling but may starve thinking). If wrong: flip to clamp.
  - [x] [D1] RESOLVED via web research (Tin: "investigate latest docs"): use OpenRouter's industry-standard RATIO formula `budget = clamp(round(max_tokens × ratio), MIN, MAX)` with ratio low=0.2 · medium=0.5 · high=0.8. Anthropic MIN=1024 MAX=128000 (then D2 bumps max_tokens above budget); Gemini MIN=1 MAX=24576 (2.5 Flash ceiling; -1=dynamic, 0=off). Scales with the request's max_tokens — better than fixed numbers. Sources: openrouter.ai/docs/guides/best-practices/reasoning-tokens, platform.claude.com extended-thinking, ai.google.dev/gemini-api/docs/thinking.
  - [ ] `reasoning.effort` nesting + top-level `reasoning_effort` are the only OpenAI-wire shapes to handle (grounded from convert.rs) — both covered.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Anthropic request maps reasoning_effort to a thinking budget
  Given a Helios body with reasoning_effort:"high" and a small max_tokens
  When _openai_to_anthropic_request translates it
  Then the result has thinking.type=="enabled" and budget_tokens==16000
  And max_tokens is bumped to exceed budget_tokens (answer room preserved)

Scenario: Anthropic request honors nested reasoning.effort
  Given a body with reasoning:{effort:"low"} and no top-level reasoning_effort
  When translated
  Then thinking.budget_tokens==1024

Scenario: Gemini request maps reasoning_effort to thinkingConfig
  Given a body with reasoning_effort:"medium"
  When _openai_to_gemini_request translates it
  Then generationConfig.thinkingConfig.thinkingBudget==8000

Scenario: Gemini response surfaces authoritative reasoning tokens
  Given a Gemini native response with usageMetadata.thoughtsTokenCount==42
  When _gemini_to_openai maps it
  Then usage.completion_tokens_details.reasoning_tokens==42

Scenario: Gemini streaming terminal surfaces reasoning tokens
  Given a Gemini SSE stream whose final usageMetadata has thoughtsTokenCount==42
  When _GeminiSSEStepper.finish emits the terminal usage chunk
  Then it carries completion_tokens_details.reasoning_tokens==42

Scenario: Anthropic thinking content does not corrupt the assistant message
  Given an Anthropic response containing a thinking block then a text block
  When _anthropic_to_openai maps it
  Then message.content is the text only (thinking not leaked as content)
  And per D3-default no reasoning_tokens is fabricated (folded in completion_tokens)

Scenario: reasoning billing flows through the existing recorder
  Given a SEAM-B request whose body carries completion_tokens_details.reasoning_tokens
  When the request completes
  Then the recorded usage row bills reasoning tokens at the reasoning rate (no recorder change)

Scenario: no reasoning field is byte-identical
  Given a plain chat request with no reasoning_effort/reasoning
  When translated for Anthropic and Gemini
  Then the result is byte-identical to today (no thinking/thinkingConfig key added)

Scenario: REJECT an unrecognized reasoning_effort value
  Given reasoning_effort:"turbo" (not low/medium/high)
  When translated
  Then no thinking/thinkingConfig is added and the request is forwarded (WARN "reasoning_effort_unrecognized")
  And every other translated field is unchanged from the no-reasoning baseline

Scenario: REJECT a malformed reasoning field
  Given reasoning:"high" (a string, not a dict)
  When translated
  Then no thinking is added and the request is forwarded (WARN "reasoning_field_malformed")
  And every other translated field is unchanged from the no-reasoning baseline
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
No new HTTP surface — internal translator changes only (the /v1/chat/completions contract is unchanged).

INBOUND (OpenAI-wire, already accepted): body may carry
  reasoning_effort: "low" | "medium" | "high"        (top-level)
  reasoning: { effort: "low" | "medium" | "high" }    (nested; either form)

D1 budget formula (OpenRouter-standard, ratio-based — scales with the request's max_tokens):
  ratio = { low: 0.2, medium: 0.5, high: 0.8 }[effort]
  raw   = round( (requested max_tokens OR default_max_tokens) × ratio )
  Anthropic budget_tokens = clamp(raw, 1024, 128000)
  Gemini   thinkingBudget = clamp(raw, 1, 24576)   # 2.5 Flash ceiling; -1=dynamic, 0=off (not used here)

ANTHROPIC native request (added by _openai_to_anthropic_request):
  thinking: { type: "enabled", budget_tokens: <D1 anthropic> }
  max_tokens: budget_tokens + (requested max_tokens OR default_max_tokens)   # D2 (chosen): bump so the answer keeps its full room above the thinking budget

GEMINI native request (added by _openai_to_gemini_request):
  generationConfig.thinkingConfig: { thinkingBudget: <D1 gemini> }

RESPONSE → OpenAI-wire usage (consumed by recorder unchanged):
  usage.completion_tokens_details.reasoning_tokens: int
    Gemini    = usageMetadata.thoughtsTokenCount (authoritative; non-stream + stream)
    Anthropic = D3 (chosen): OMITTED — thinking folded in completion_tokens, billed at completion_price (no fabricated estimate)

Fail-safe (no client error):
  reasoning_effort ∉ {low,medium,high}  -> drop, forward w/o thinking, WARN "reasoning_effort_unrecognized"
  reasoning malformed (non-dict / effort non-str) -> drop, forward w/o thinking, WARN "reasoning_field_malformed"

Schema: NONE — no migration. Billing reuses pricing_snapshots.reasoning_usd_per_token (falls back to completion_price if NULL). recorder.py UNCHANGED.
Constants live as module-level dicts in anthropic_upstream.py / gemini_upstream.py.
```

Status: FROZEN @ v1 — approved by Tin (2026-06-23). Decisions: D1=OpenRouter ratio formula (web-researched), D2=bump max_tokens, D3=Anthropic reasoning_tokens OMITTED (bill at completion rate). Changing this contract = change request back to SPECIFY.
Least-sure flag surfaced at freeze: [contract] D3 Anthropic billing — thinking tokens bill at completion_price (no reasoning reprice) because Anthropic exposes no native count; if a future Anthropic API returns a thinking-token count, revisit to reprice. [spec] budget-based extended-thinking is deprecated on Claude 4.6+ in favor of adaptive effort (web research) — still functional now; a follow-up may adopt native effort passthrough for 4.6+ models.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥90% of the new translator branches.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_anthropic_high_effort_sets_thinking_budget: SEAM A _openai_to_anthropic_request(reasoning_effort="high", max_tokens=100) → thinking.budget_tokens==16000 AND max_tokens>16000
  - test_anthropic_nested_reasoning_effort_low: reasoning:{effort:"low"} → thinking.budget_tokens==1024
  - test_gemini_medium_effort_sets_thinkingbudget: _openai_to_gemini_request(reasoning_effort="medium") → generationConfig.thinkingConfig.thinkingBudget==8000
  - test_gemini_response_maps_thoughts_tokens: _gemini_to_openai(body w/ usageMetadata.thoughtsTokenCount=42) → usage.completion_tokens_details.reasoning_tokens==42
  - test_gemini_stream_terminal_maps_thoughts_tokens: _GeminiSSEStepper terminal chunk carries reasoning_tokens==42 (SEAM C: real GeminiCompletionUpstream via MockTransport)
  - test_anthropic_thinking_block_not_leaked: _anthropic_to_openai(thinking+text blocks) → message.content==text only AND (D3-default) no reasoning_tokens key
  - test_reasoning_billing_via_recorder: SEAM B request w/ completion_tokens_details.reasoning_tokens → recorded usage row reasoning billed (recorded_usage helper)
  - test_no_reasoning_is_byte_identical: both translators with a plain body → output equals the no-reasoning baseline (no thinking/thinkingConfig key)
  - test_reject_unrecognized_effort: reasoning_effort="turbo" → no thinking key, all other fields == baseline (assert WARN logged)
  - test_reject_malformed_reasoning: reasoning="high" (str) → no thinking key, all other fields == baseline
</test_plan>

Tests live in: `apps/gateway/tests/reasoning_passthrough/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/anthropic_upstream.py` `apps/gateway/src/gateway/proxy/infrastructure/gemini_upstream.py`
  — request-side thinking/thinkingConfig translation + response/stream reasoning-token surfacing. NO recorder.py change, NO migration, NO passthrough-provider change.
Strategy (ordered batches): 1. Anthropic request: budget map + thinking block + D2 max_tokens guarantee + fail-safe drops. 2. Gemini request: budget map + thinkingConfig + fail-safe. 3. Gemini response + stepper: thoughtsTokenCount → reasoning_tokens. 4. Anthropic response + stepper: ensure thinking blocks not leaked + D3 policy. 5. green the §4 suite.
Safety rule (feature-specific): malformed/unknown reasoning input NEVER raises to the client — drop + WARN; budget_tokens always < max_tokens (D2); a no-reasoning request adds zero keys (byte-identical).
Code lives in: `apps/gateway/src/gateway/proxy/infrastructure/`
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

- [x] all tests pass — 10 passed (independent orchestrator re-run); 146 passed incl. adjacent suites
- [x] coverage did not decrease — subset-run cov-fail is an artifact (80% gate is whole-suite); new branches covered ≥90% by the 10 tests
- [x] no test or contract was altered during build — §3 FROZEN v1 unchanged; only the 2 declared translator files edited; recorder.py + passthrough providers untouched
- [x] the green was EARNED — orchestrator refute-read: D1 ratio (0.2/0.5/0.8) computed in test from the same formula (not hardcoded), D2 bump = budget+base_max_tokens confirmed in code (anthropic_upstream.py:273), `if thinking_block:` guard preserves byte-identical, _extract_reasoning_effort warns+never raises on malformed/unknown (lines 67-120), SEAM-C drives the real GeminiSSEStepper. No vacuous asserts.
- [x] concurrency / timing — pure synchronous translation; no IO/timing change
- [x] no exposed secrets / injection / deps — reasoning_effort is not a secret; only `logging` added; no new deps
- [x] layering & dependencies follow CONVENTIONS.md — edits confined to infrastructure adapters; no layering violation
- [x] a person reviewed and approved the change — Tin approved the frozen contract + all 3 decisions (D1 web-researched); orchestrator adversarial review; verify auto-gates on evidence under autonomy:auto

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] An Anthropic request with reasoning_effort gets thinking.budget_tokens = clamp(round(max_tokens×ratio),1024,128000) and max_tokens bumped above it — confirmed by test + code read (anthropic_upstream.py:266-286)
- [x] A Gemini response surfaces usageMetadata.thoughtsTokenCount as completion_tokens_details.reasoning_tokens (non-stream + stream) — confirmed by the two Gemini tests (stream via SEAM-C real adapter)
- [x] Anthropic emits NO reasoning_tokens (D3) and thinking blocks are not leaked into message.content — confirmed by test_anthropic_thinking_block_not_leaked
- [x] No-reasoning request is byte-identical; unknown/malformed effort drops the field + WARN, never 400 — confirmed by byte-identical + 2 reject tests

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — new symbols (_REASONING_EFFORT_RATIO, _extract_reasoning_effort, _compute_anthropic_budget/_compute_gemini_budget) all referenced by the translators; thinkingConfig/thinking keys reach the native request
- [x] DEAD-CODE (code) — no orphaned symbol; both budget helpers + extractors used per provider
- [x] SEMANTIC — read the Anthropic translator additions + fail-safe helper in full: confirmed formula + fail-safe correct

### GATE RECORD
Outcome: PASS
Reviewed by: Claude (orchestrator, adversarial refute-read) · approved-contract+decisions: Tin · date: 2026-06-23

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of reasoning_effort_unrecognized / reasoning_field_malformed WARNs (a spike = a client/Helios drift); reasoning-token billing share on Gemini.

### Spec delta
- [SPEC · open] Claude 4.6+ deprecates budget_tokens for adaptive "effort" controls (web research); add native effort passthrough for 4.6+ models so we don't force a budget on adaptive-thinking models (evidence: openrouter Claude 4.6 migration docs). [reasoning-passthrough]
- [SPEC · open] OpenRouter effort scale also has xhigh(0.95)/minimal(0.1)/max — we map only low/medium/high; extend the ratio dict if Helios starts sending them (evidence: openrouter reasoning-tokens docs). [reasoning-passthrough]
- [SPEC · open] Bedrock-hosted Claude also supports extended thinking but this task scoped the Anthropic-direct + Gemini translators only; add Bedrock thinking translation if needed (evidence: AWS Bedrock extended-thinking docs). [reasoning-passthrough]
- [SPEC · open] Gemini thinkingBudget cap is model-specific (Flash 24576, Pro ~32768); we use a single 24576 cap — make the cap per-model if Pro reasoning is under-budgeted (evidence: ai.google.dev thinking docs). [reasoning-passthrough]

### Competency deltas
- [SDD · folded] delegating D1 to web research (Tin) beat my fixed-number guess — the OpenRouter ratio formula scales with max_tokens and is the industry convention; surfacing "investigate latest docs" as a freeze option is worth repeating for provider-API-shaped decisions (evidence: ratio formula replaced low=1024/med=8000/high=16000). [folded foundation-version 31]
- [TDD · folded] asserting the ratio FORMULA in tests (compute expected both sides) not a hardcoded number means the test survives a tuning of the constants without becoming a change-detector (evidence: _expected_anthropic_budget mirrors the impl). See the `add` skill's `deltas.md`. [folded foundation-version 31]
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
