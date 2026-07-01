# TASK: Chat parameters — full sampling control

slug: chat-parameters-panel · created: 2026-06-28 · stage: production
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

Touches (files · symbols · signatures): the chat send seam + the shell's Parameters tab. `lib/hooks/use-chat-stream.ts` — `SendInput { model · text · system? · temperature? · webSearch? }` (line 47) is the per-turn request contract; `runStream()` (line 167) builds the POST body to `/api/gw/v1/chat/completions` with `{ model, messages, stream:true, stream_options:{include_usage:true}, …temperature?, …(webSearch?{web_search:true}) }` — every optional field is included ONLY when set (omitted-when-unset ⇒ byte-identical off path; the established pass-through pattern this task extends for top_p/max_tokens/stop/frequency_penalty/presence_penalty/seed/response_format). `components/chat/ChatWorkspace.tsx` — lifts the param state (`system/temperature/webSearch` today) and threads it into `send({ model, text, system, temperature, webSearch })` at submit; passes the same state to `InspectorPanel`. `components/chat/InspectorPanel.tsx` — the frozen Parameters tab from chat-playground-shell: renders `<ModelControls>` (System prompt/Temperature/Web search) + a DISABLED `<fieldset>` of `ScaffoldParam` rows (Top P · Max tokens · Frequency penalty · Presence penalty · Seed · Stop sequences · Response format) — THIS task replaces those scaffolds with real, validated controls. `components/chat/ModelControls.tsx` — the directly-rendered control block (no disclosure) the new controls sit beside.
Context (working folder): `.add/milestones/chat-playground/MILESTONE.md` — Scope names the exact sampling set (temperature·top_p·max_tokens·stop·frequency/presence penalty·seed·response_format) wired pass-through to `/v1/chat/completions`, "validated, persisted per session"; Shared decisions: pass-through-first (NO gateway change), feature-rebuild (chat tests evolve via TDD), design-for-failure (validate client-side before the wire; no retry-storm). The shell froze the Parameters-tab anatomy this fills (`.add/tasks/chat-playground-shell/TASK.md` §3 + `.add/design/prototypes/chat-playground.json` insp_sampling/insp_response slots). OpenAI Chat Completions param semantics (top_p 0–1, penalties −2..2, response_format `{type:"text"|"json_object"}`) are the reference.
Honors (patterns / conventions): omitted-when-unset body construction (off/default ⇒ no key ⇒ byte-identical) is the v40/v41 invariant — every new param follows it; tokens-only UI, WCAG 2.2 AA (each control keyed by a stable aria-label), decorative icons aria-hidden; design-for-failure = client-side validation (clamp/reject out-of-range) before send(), never a silent bad request. Pure presentation + lifted state (ChatWorkspace owns the values; the panel is controlled) — no new dependency, no gateway change.
Anchors the contract cites: `SendInput` (extended with the sampling fields, all optional) · `runStream()` body builder (each new field included only when set) · `ChatWorkspace` lifted param state + `submit()`→`send()` threading · `InspectorPanel` Parameters tab (the ScaffoldParam rows become real `ParameterField` controls) · the validation rules per field · the preserved seam POST `/api/gw/v1/chat/completions` (unchanged shape except additive optional keys).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Chat parameters — full sampling control
Framings weighed: per-request controls in the inspector Parameters tab wired pass-through to /v1/chat/completions (chosen — fills the shell's frozen slots, no gateway change) · a separate "Advanced" modal/drawer · gateway-side stored param presets (OUT — milestone defers a preset store)
Must:
<must>
  - The inspector Parameters tab exposes the full OpenAI-compatible sampling set as real, validated controls — Top P, Max tokens, Frequency penalty, Presence penalty, Seed, Stop sequences, Response format (Text|JSON) — alongside the existing Temperature (and System prompt / Web search).
  - Each set value is sent on the NEXT run via the existing POST /api/gw/v1/chat/completions body using the canonical OpenAI key (top_p · max_tokens · stop · frequency_penalty · presence_penalty · seed · response_format). Pass-through ONLY — no gateway change.
  - Omitted-when-unset: a control left at its unset/default state adds NO key to the body (off path byte-identical to today; temperature + web_search behaviour preserved).
  - Values persist across turns within the session (lifted state in ChatWorkspace) and survive switching inspector tabs — not reset each turn.
  - Client-side validation before send(): each value is constrained to its valid range (top_p 0–1 · penalties −2..2 · max_tokens ≥ 1 · seed an integer · stop ≤ 4 non-empty sequences); an out-of-range/empty entry is clamped or omitted, never emitted as a malformed request key.
  - Response format: selecting JSON sends response_format {type:"json_object"} AND injects a short fixed system instruction ("Respond only with valid JSON.") merged with any user System prompt, so providers that 400 a json_object request lacking "json" in the prompt still succeed; Text (default) sends no key and no hint. The reply itself is NOT validated/parsed (honest pass-through of the result).
  - Provider-aware capability gating (Tin's decision via AskUserQuestion, 2026-06-28 — the change-request that reopened this bundle): a param the selected model's provider does NOT honor is disabled + annotated ("Ignored by <Provider>") in the panel AND omitted from the request body — never shipped as a silent no-op key. The capability matrix is a shared module keyed by the model-id provider prefix, reflecting how THIS gateway actually translates per provider (verified file:line in research): frequency_penalty · presence_penalty · seed are unsupported on Anthropic / Google(Gemini) / Bedrock (the gateway drops them); response_format is additionally unsupported on Bedrock. Unknown / OpenAI / OpenRouter prefixes ⇒ all supported (passthrough). Switching the model re-gates live; when response_format is gated off, its JSON system hint is omitted too.
  - a11y/tokens: each control keyed by a stable aria-label (Top P / Max tokens / Frequency penalty / Presence penalty / Seed / Stop sequences / Response format); tokens only; the System prompt/Temperature/Web search aria-labels + the streaming/cost/conversation seams stay unchanged.
</must>
Reject:
<reject>
  - a set sampling value not reaching the next request body with its canonical OpenAI key -> "param_not_sent"
  - an out-of-range / malformed value emitted as a request key (top_p 5, max_tokens 0, empty seed string, blank stop entry) -> "invalid_param_sent"
  - a control at its default mutating the body (a key added when nothing was set) -> "default_leaked"
  - a param the selected model's provider does NOT honor reaching the body, or its control left enabled/un-annotated (a known silent no-op presented as active) -> "unsupported_param_sent"
</reject>
After:
<after>
  - the Parameters tab's disabled scaffold rows are replaced by live validated controls; setting any sampling param sends it on the next run with the canonical key; defaults stay omitted (byte-identical off path); the chat suite is green by co-evolution (no seam weakened); tsc + eslint + add.py check clean.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Capability gating is at PROVIDER granularity (the model-id prefix), NOT per-model. This honestly covers the gateway's drop behaviour (penalties/seed on Anthropic/Gemini/Bedrock, response_format on Bedrock). It deliberately does NOT gate the finer per-model 400 edges the research surfaced — temperature/top_p return 400 on Claude Opus 4.7+; max_tokens is rejected by OpenAI o-series (needs max_completion_tokens); seed is ignored on o3/o4-mini — because a per-model matrix drifts fast and temperature already ships ungated. Those surface as honest upstream 4xx (already handled by the error state) and are recorded as §7 deltas + a gateway-robustness follow-up. Lowest confidence: provider granularity is the right line to draw for this UI task (vs. pushing capability handling into the gateway). If wrong: a few newer-model requests 400 visibly instead of being pre-empted — recoverable, no data risk.
  ⚠ response_format json_object = the response_format key PLUS a fixed merged system hint "Respond only with valid JSON." (Tin's freeze choice, to satisfy providers that 400 without "json" in the prompt). We still do NOT validate the reply parses as JSON — the result is honest pass-through. The single fixed hint wording may not suit every provider/use-case; if wrong: an honest upstream 4xx (already handled), tunable later. The hint is appended to a user System prompt when present (one system message), else sent alone; it is omitted entirely when response_format is gated off for the provider (Bedrock).
  - [ ] "persisted per session" = in-memory lifted state (survives across turns + inspector tab switches, resets on full reload) — NOT localStorage and NOT a server preset store (both explicitly OUT in the milestone) — confirm in-memory is the intended scope.
  - [ ] Stop sequences = up to 4 short strings entered as chips/tags, sent as a string[] (OpenAI caps stop at 4) — confirm the 4-cap + array wire shape (vs a single comma-joined string).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: A set sampling value reaches the next request
  Given the inspector Parameters tab
  When I set Top P to 0.5 and run a turn
  Then the POST body carries top_p: 0.5
  And the streaming seam (POST /api/gw/v1/chat/completions, stream:true, stream_options.include_usage:true) is otherwise unchanged

Scenario: Each canonical key maps correctly
  Given the Parameters tab
  When I set Max tokens 256, Frequency penalty 0.5, Presence penalty -0.5, Seed 42 and run
  Then the body carries max_tokens:256, frequency_penalty:0.5, presence_penalty:-0.5, seed:42

Scenario: Response format JSON
  Given the Parameters tab
  When I switch Response format to JSON and run
  Then the body carries response_format {type:"json_object"}
  And a system message instructs JSON-only output ("Respond only with valid JSON.", merged with any user System prompt)
  And switching back to Text removes both the key and the hint on the next run

Scenario: Values persist across turns and tab switches
  Given I set Top P 0.3
  When I run a turn, open the Tools tab, then return to Parameters
  Then Top P is still 0.3 and is sent again on the next run

Scenario: Defaults stay omitted (byte-identical off path)
  Given I have not touched the sampling controls
  When I run a turn
  Then the body has NO top_p / max_tokens / stop / frequency_penalty / presence_penalty / seed / response_format key
  And it is byte-identical to today's body (model · messages · stream · stream_options [· temperature/web_search if set])

Scenario: A default control does not leak (rejection)
  Given Top P untouched at its default
  When I run a turn
  Then no top_p key is added -> "default_leaked"
  And the rest of the body is unchanged

Scenario: An out-of-range value is never sent (rejection)
  Given the Parameters tab
  When I try to set Top P above 1, or Max tokens to 0, or leave a blank stop entry
  Then the control clamps/omits the value
  And no top_p:5, max_tokens:0, or empty stop string is ever emitted -> "invalid_param_sent"

Scenario: A set value must reach the wire (rejection)
  Given I set Seed 7
  When I run a turn
  Then seed:7 is present in the body -> "param_not_sent"
  And no other key was dropped

Scenario: Provider-aware gating for an unsupported param (rejection)
  Given I set Seed 7 while the model is openai/gpt-4o (where it is honored)
  When I switch the model to anthropic/claude-3.5-sonnet
  Then the Seed, Frequency penalty and Presence penalty controls are disabled and annotated "Ignored by Anthropic"
  And running a turn sends NO seed / frequency_penalty / presence_penalty key (the gateway would silently drop them) -> "unsupported_param_sent"
  And Top P, Max tokens, Stop and Response format remain enabled and still send

Scenario: Capability matrix maps providers correctly (unit)
  Given the shared capability module keyed by the model-id provider prefix
  Then openai/* and openrouter/* (and any unknown prefix) support every param
  And anthropic/* and google/* drop frequency_penalty, presence_penalty, seed
  And bedrock/* additionally drops response_format
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
COMPLETIONS REQUEST PARAM CONTRACT — additive · pass-through · omitted-when-unset.
Seam UNCHANGED: POST /api/gw/v1/chat/completions (stream:true, stream_options.include_usage:true). NO gateway change.

SendInput (lib/hooks/use-chat-stream.ts) gains OPTIONAL fields:
  topP? number(0..1) · maxTokens? int(≥1) · frequencyPenalty? number(−2..2) ·
  presencePenalty? number(−2..2) · seed? int · stop? string[] (≤4, each non-empty) ·
  responseFormat? "text" | "json_object"

CAPABILITY MATRIX (NEW shared module `lib/chat/param-capabilities.ts`) — keyed by the model-id provider prefix:
  providerOf(model) = model.split("/")[0].toLowerCase()
  CapKey = "topP"|"maxTokens"|"frequencyPenalty"|"presencePenalty"|"seed"|"stop"|"responseFormat"
  UNSUPPORTED (everything not listed ⇒ supported):
    anthropic           → frequencyPenalty, presencePenalty, seed
    google | gemini     → frequencyPenalty, presencePenalty, seed
    bedrock | amazon    → frequencyPenalty, presencePenalty, seed, responseFormat
  isSupported(model, key) = !(UNSUPPORTED[providerOf(model)] ?? []).includes(key)
  providerLabel(model) = { anthropic:"Anthropic", google/gemini:"Gemini", bedrock/amazon:"Bedrock" }[prov] ?? prov
  (Unknown / openai / openrouter ⇒ all supported — the gateway passthrough providers.)

runStream() body — each key included ONLY when its field is set AND valid AND isSupported(model, key) (else ABSENT):
  top_p · max_tokens · stop · frequency_penalty · presence_penalty · seed ·
  response_format: { type: "json_object" }   (ONLY when responseFormat==="json_object" AND isSupported(model,"responseFormat"))
  (existing temperature / web_search inclusion unchanged. top_p/max_tokens/stop are supported on every provider.)

SYSTEM-MESSAGE CONSTRUCTION (send/runStream wire build) — JSON hint injection (Tin's freeze choice), gated by capability:
  JSON_HINT = "Respond only with valid JSON."
  jsonOn = responseFormat === "json_object" && isSupported(model, "responseFormat")
  effectiveSystem = jsonOn ? (system ? `${system}\n\n${JSON_HINT}` : JSON_HINT) : system
  wire = effectiveSystem ? [{ role:"system", content: effectiveSystem }, ...messages] : messages
  ⇒ JSON (supported) ⇒ exactly ONE system message carrying the hint (merged with any user System prompt);
    Text/unset OR provider-gated ⇒ the system message is the user prompt only (unchanged from today; absent when blank).

UI — InspectorPanel Parameters tab replaces the disabled ScaffoldParam fieldset with live controls, each lifted to
ChatWorkspace state (persisted across turns + inspector tab switches; in-memory per session — NOT localStorage):
  aria-labels (stable): "Top P" (slider 0–1) · "Max tokens" (number ≥1) · "Frequency penalty" (slider −2..2) ·
  "Presence penalty" (slider −2..2) · "Seed" (number, optional) · "Stop sequences" (chip/tag input, ≤4) ·
  "Response format" (segmented Text|JSON). Temperature / System prompt / Web search aria-labels UNCHANGED.
  PROVIDER GATING: a control where !isSupported(model, key) is rendered DISABLED with a muted note "Ignored by <providerLabel>".
  The panel receives the current model; switching the model re-gates live. (ParameterField gains disabled? + note? props.)

VALIDATION (client-side, BEFORE send): clamp sliders to range; coerce number inputs (empty seed/max_tokens ⇒ unset ⇒ key
  omitted); stop = trimmed non-empty strings, capped at 4. An invalid/empty value ⇒ key OMITTED, never emitted malformed.

INVARIANTS: pass-through only (no gateway change); off/default ⇒ byte-identical body; a provider-unsupported param never ships;
  streaming/abort/cost/conversation seams + all frozen aria-labels/testids/role=log/data-role intact; tokens only; four states unaffected.
```

Status: FROZEN @ v2 — approved by Tin (v2 = the provider-aware gating change-request; Tin chose "Provider-aware UI (gate + annotate)" via AskUserQuestion 2026-06-28 after the API/gateway research. v1 froze the pass-through param set + JSON-hint.)
Least-sure flag surfaced at freeze: [spec] (v2) capability gating granularity — PROVIDER-level (model-id prefix), not per-model. Honestly covers the gateway's silent drops (penalties/seed on Anthropic/Gemini/Bedrock; response_format on Bedrock) but deliberately leaves the per-model 400 edges (temperature/top_p on Claude Opus 4.7+; max_tokens on OpenAI o-series; seed on o3/o4-mini) to surface as honest upstream 4xx → recorded as §7 deltas + a gateway-robustness follow-up. Cost if wrong: a few newer-model requests 400 visibly instead of being pre-empted (recoverable, no data risk). Carried from v1: the fixed JSON-hint wording may not suit every provider (tunable later); "persisted per session" = in-memory lifted state; Stop = string[] ≤4.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥80% per project (the standing gate); net-additive controls + body fields, behavior-preserving — zero assertions weakened.
Plan (one test per scenario, asserting the POST body via MSW capture — the same harness as chat-model-controls/chat-websearch-toggle):
<test_plan>
  - test_top_p_reaches_body: set Top P 0.5 → run → body.top_p === 0.5; model/messages/stream/stream_options unchanged.
  - test_canonical_keys_map: set Max tokens 256 / Freq 0.5 / Pres -0.5 / Seed 42 → body has max_tokens:256, frequency_penalty:0.5, presence_penalty:-0.5, seed:42.
  - test_response_format_json_sends_key_and_hint: select JSON → body.response_format == {type:"json_object"} AND a system message content includes "Respond only with valid JSON."; then select Text → next body has NO response_format and the system hint is gone.
  - test_json_hint_merges_with_user_system: set System prompt "Be terse" + JSON → the single system message content is "Be terse\n\nRespond only with valid JSON.".
  - test_values_persist_across_turns_and_tabs: set Top P 0.3 → run → open Tools tab → return to Parameters → control still 0.3 → run again → body.top_p still 0.3.
  - test_defaults_omitted_byte_identical: untouched controls → run → body has NONE of top_p/max_tokens/stop/frequency_penalty/presence_penalty/seed/response_format; equals today's shape.
  - test_out_of_range_clamped_or_omitted (rejection): drive Top P > 1 / Max tokens 0 / blank stop → body never carries top_p>1, max_tokens:0, or an empty stop string (clamped or key omitted) -> "invalid_param_sent".
  - test_default_does_not_leak (rejection): Top P at default → run → no top_p key -> "default_leaked".
  - test_set_value_reaches_wire (rejection): Seed 7 → run → body.seed === 7 -> "param_not_sent".
  - test_unsupported_param_gated_on_provider_switch (rejection): set Seed 7 on openai/gpt-4o → switch model to anthropic/claude-3.5-sonnet → Seed/Frequency/Presence controls disabled + annotated "Ignored by Anthropic" → run → body has NO seed/frequency_penalty/presence_penalty; Top P/Max tokens/Stop/Response format still send -> "unsupported_param_sent".
  - (unit) param-capabilities matrix: providerOf prefix parsing; openai/openrouter/unknown ⇒ every key supported; anthropic + google ⇒ freq/pres/seed unsupported; bedrock ⇒ + responseFormat; providerLabel maps anthropic→Anthropic, google→Gemini, bedrock→Bedrock.
  Existing chat suites stay green UNCHANGED (default model openai/gpt-4o ⇒ all supported; default responseFormat=text ⇒ no hint ⇒ system construction byte-identical; new controls are name-scoped, no query collision); co-evolve only if the build reveals a real collision.
</test_plan>

Tests live in: `apps/dashboard/tests-bff/chat-parameters.test.tsx` `apps/dashboard/tests-bff/param-capabilities.test.ts` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/lib/hooks/use-chat-stream.ts` `apps/dashboard/lib/chat/param-capabilities.ts` `apps/dashboard/components/chat/ChatWorkspace.tsx` `apps/dashboard/components/chat/InspectorPanel.tsx` `apps/dashboard/components/chat/ParameterField.tsx` `apps/dashboard/components/chat/ModelControls.tsx` `apps/dashboard/tests-bff/chat-parameters.test.tsx` `apps/dashboard/tests-bff/param-capabilities.test.ts`
Strategy (ordered batches): 1. NEW red suite `chat-parameters.test.tsx` (9 body-capture cases) → red. 2. Extend `SendInput` with the optional sampling fields + a `responseFormat` type; extend `runStream()` body (each key only when set+valid) and the system-message build with the JSON_HINT merge. 3. Add a reusable `ParameterField` control (slider / number / tags / segmented) bound to tokens. 4. Lift the new param state in `ChatWorkspace` (in-memory; persists across turns + tab switches) and thread it into `send()`. 5. Replace the InspectorPanel disabled ScaffoldParam fieldset with live ParameterField controls wired to that state; client-side validation (clamp/coerce/omit). 6. green new suite + full chat suite + tsc + eslint + add.py check; capture the built Parameters tab at verify.
Known-problem fixes: omitted-when-unset → build the body with conditional spreads (…(topP!=null?{top_p:topP}:{})) so a default never adds a key (guards default_leaked) · validation → clamp sliders, coerce empty number inputs to undefined (key omitted), drop blank stop entries + cap 4 (guards invalid_param_sent) · JSON hint → merge into ONE system message (user prompt + "\n\n" + JSON_HINT), and when Text/unset send NO hint so chat-model-controls test_system_prompt_feeds_send stays byte-identical · persistence → lift state in ChatWorkspace (NOT inside InspectorPanel, which unmounts when a different tab is active) so values survive tab switches · name-scoping → new controls keep distinct aria-labels so existing name-scoped queries (temperature/system prompt) stay unique · tokens only (no raw hex/px).
Strategy actually used: As planned, with the v2 capability-gating change-request woven in (research before build surfaced provider variance → Tin chose gate+annotate). Order: red suite (10 body-capture + 1 gating + 5-case capability unit) → `param-capabilities.ts` shared module (providerOf/isSupported/providerLabel) → `SendInput` + `runStream` body gated by `isSupported(model,key)` + JSON-hint gated on responseFormat support → `ParameterField` primitives gained `disabled`/`note` → `InspectorPanel` renders live controls with a `gate(key)` helper → `ChatWorkspace` lifted `sampling` state (shallow-patch `onSampling`, pure `samplingToInput` spread into both submit() and regenerateFrom()). Body inclusion lives in ONE place (runStream), the UI reads the SAME `isSupported` so gating + omission can't drift. Off/default path proven byte-identical (existing 801 chat tests green untouched). 816/816, tsc clean (cleared a stale `.next/dev/types` validator from the throwaway capture route), eslint 0. Capture: live-vs-gated side-by-side at `.add/design/captures/chat-parameters.png`. HEAL (attempt 1/3): the refute-read BLOCKed on a real gap — the contract promises "clamp sliders to range" but ParamSlider passed the raw value and the out-of-range test only drove the number input. Fixed by adding the clamp `Math.min(max,Math.max(min,n))` in ParamSlider + STRENGTHENING test_out_of_range to drive Top P 5 / Frequency 3 (the §4-named scenario) + adding a Bedrock JSON+hint suppression integration test (MED gap). No test weakened; the contract was untouched. Re-green 817/817.
Safety rule (feature-specific): the streaming/abort/cost/conversation seams stay byte-identical; default/off path produces a byte-identical request body (only set+valid params add keys); no gateway change; an invalid value NEVER ships as a request key.
Code lives in: `apps/dashboard/components/chat` + `apps/dashboard/lib/hooks/use-chat-stream.ts`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) is live: a completing verify gate refuses an
     out-of-scope build (scope_violation → self-heal) and add.py check surfaces it.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — vitest 816/816 (both projects: legacy + bff); +15 net new (10 body-capture/gating + 5 capability matrix).
- [x] coverage did not decrease — net-additive tests; new module + controls fully exercised.
- [x] no test or contract was altered during build — §3 FROZEN @ v2 untouched; the red suite drove the build unchanged.
- [x] the green was EARNED, not gamed — adversarial refute-read recorded below; tests assert the real POST body via MSW capture (not internals) + the live capability matrix; off-path proven by 801 pre-existing chat tests staying green.
- [x] concurrency / timing of the risky operation is safe — no new async/concurrency; the SSE stream + AbortController seam is byte-identical; sampling is pure synchronous React state.
- [x] no exposed secrets, injection openings, or unexpected dependencies — pass-through body only, no gateway change, no new package; no token/secret touched.
- [x] layering & dependencies follow CONVENTIONS.md — UI reads a shared `lib/chat` capability module; body inclusion + UI gating share ONE `isSupported` so they can't drift.
- [ ] a person reviewed and approved the change — autonomy:auto (refute-read substitutes); Tin's gate/merge is the human checkpoint.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] The Parameters tab shows live controls (Top P · Max tokens · Frequency/Presence penalty · Seed · Stop sequences · Response format) replacing the disabled scaffold — confirmed by the captured built Parameters tab (`.add/design/captures/chat-parameters.png`) + test_top_p_reaches_body/test_canonical_keys_map green.
- [x] A set value reaches the next request body with its canonical OpenAI key (top_p/max_tokens/stop/frequency_penalty/presence_penalty/seed/response_format) — confirmed by the body-capture tests; defaults add NO key (byte-identical off path) — confirmed by test_defaults_omitted_byte_identical + the 801 pre-existing chat tests staying green untouched.
- [x] Selecting JSON sends response_format {type:"json_object"} AND a merged "Respond only with valid JSON." system message; Text drops both — confirmed by test_response_format_json_sends_key_and_hint + test_json_hint_merges_with_user_system.
- [x] An invalid value (max_tokens 0, blank stop) never ships as a key, and values persist across turns + inspector tab switches — confirmed by test_out_of_range_clamped_or_omitted + test_values_persist_across_turns_and_tabs; the existing chat suite stays green (no seam weakened).
- [x] Provider-aware gating: on a non-OpenAI model (Claude) the unsupported controls (Seed/Frequency/Presence) are visibly disabled + annotated "Ignored by Anthropic" AND omitted from the body; the capability matrix matches the gateway's real per-provider drops — confirmed by test_unsupported_param_gated_on_provider_switch + the param-capabilities unit matrix + the capture (right panel shows the three gated, Top P/Max tokens/Stop still live).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced: `param-capabilities.{providerOf,isSupported,providerLabel,CapKey}` ← use-chat-stream (body+hint gate) + InspectorPanel (gate()); `ParameterField.{ParamSlider,ParamNumber,ParamTags,ParamSegmented}` ← InspectorPanel; `SamplingState`/`EMPTY_SAMPLING` ← ChatWorkspace; `samplingToInput` ← submit()+regenerateFrom(); `InspectorPanel` new props (model/sampling/onSampling) ← ChatWorkspace render. tsc clean proves no dangling reference.
- [x] DEAD-CODE (code) — the old `ScaffoldParam` helper was REMOVED with the scaffold fieldset (not left orphaned); no new unused symbol (eslint no-unused clean; tsc clean). The `note` prop on ParamTags is plumbed though stop is never gated today — harmless forward-compat, not dead (it's a declared prop).
- [ ] SEMANTIC (prose / non-code) — n/a (code task).

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED (after heal — the refute-read did its job)
By: agent-ac6d06cdbbda2127b (frontend-expert, independent) · adversarially checked: off/default byte-identical path; gating real in the body builder (not UI-only); capability matrix vs contract; validation coercion; seam integrity; a11y. Found 1 HIGH (missing slider clamp + the §4 "Top P > 1" assertion never written — a genuine earned-green gap), 1 MED (Bedrock JSON-suppression untested), 1 LOW (no slider reset). HIGH+MED FIXED this heal (clamp added + tests strengthened, re-green 817/817); LOW seeded as a delta. Re-verified: the strengthened test would ship top_p:5 without the clamp → now clamped to 1. No false-green remains.

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-06-28

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of upstream 4xx on /v1/chat/completions broken down by provider + param (a spike on Claude+top_p or OpenAI-o-series+max_tokens = the un-gated per-model edge biting); rate of response_format requests that still return non-JSON (the honest-passthrough reality).

### Decisions (ADR)
- [AI] specify — chose per-request controls in the inspector Parameters tab wired pass-through to /v1/chat/completions; rejected a separate "Advanced" modal/drawer · gateway-side stored param presets (OUT — milestone defers a preset store)
- [human] freeze — froze §3 @ v2 (approved by Tin (v2 = the provider-aware gating change-request; Tin chose "Provider-aware UI (gate + annotate)" via AskUserQuestion 2026-06-28 after the API/gateway research. v1 froze the pass-through param set + JSON-hint.))
- [AI] build — strategy used: As planned, with the v2 capability-gating change-request woven in (research before build surfaced provider variance → Tin chose gate+annotate). Order: red suite (10 body-capture + 1 gating + 5-case capability unit) → `param-capabilities.ts` shared module (providerOf/isSupported/providerLabel) → `SendInput` + `runStream` body gated by `isSupported(model,key)` + JSON-hint gated on responseFormat support → `ParameterField` primitives gained `disabled`/`note` → `InspectorPanel` renders live controls with a `gate(key)` helper → `ChatWorkspace` lifted `sampling` state (shallow-patch `onSampling`, pure `samplingToInput` spread into both submit() and regenerateFrom()). Body inclusion lives in ONE place (runStream), the UI reads the SAME `isSupported` so gating + omission can't drift. Off/default path proven byte-identical (existing 801 chat tests green untouched). 816/816, tsc clean (cleared a stale `.next/dev/types` validator from the throwaway capture route), eslint 0. Capture: live-vs-gated side-by-side at `.add/design/captures/chat-parameters.png`. HEAL (attempt 1/3): the refute-read BLOCKed on a real gap — the contract promises "clamp sliders to range" but ParamSlider passed the raw value and the out-of-range test only drove the number input. Fixed by adding the clamp `Math.min(max,Math.max(min,n))` in ParamSlider + STRENGTHENING test_out_of_range to drive Top P 5 / Frequency 3 (the §4-named scenario) + adding a Bedrock JSON+hint suppression integration test (MED gap). No test weakened; the contract was untouched. Re-green 817/817.
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] gateway-robustness: translate/guard the per-MODEL 400 edges the research found — drop or rename max_tokens→max_completion_tokens for OpenAI o-series; drop temperature/top_p for Claude Opus 4.7+ (they 400); drop seed for o3/o4-mini (evidence: live OpenAI/Anthropic API docs 2026-06-28; the gateway forwards these raw → honest upstream 400). This task gates at PROVIDER granularity only; per-model is a gateway concern.
- [SPEC · open] richer response_format: json_schema (Structured Outputs) is supported by OpenAI + translated by the gateway for Anthropic/Gemini, but this UI exposes only Text|JSON. A schema editor is a future control (evidence: gateway anthropic_upstream.py json_schema coercion path).
- [SPEC · seeded] the fixed JSON hint wording ("Respond only with valid JSON.") is one-size — consider per-provider/tunable phrasing if a provider still 400s or ignores it (evidence: Tin's v1 freeze flag).
- [SPEC · open] capability matrix maintenance: provider support drifts (e.g. Claude Opus dropping temperature) — the matrix in `lib/chat/param-capabilities.ts` needs a review cadence or a gateway-sourced capability feed (evidence: Opus 4.7+ temperature 400 is already a per-model exception the provider map can't express).
- [SPEC · seeded] per-control "reset to default" affordance: a slider moved to its default position then sent is `top_p:1` (the OpenAI default) rather than omitted — semantically a no-op but not byte-identical, and there's no UI to return a touched slider to unset (evidence: refute-read LOW, ParameterField ParamSlider has no reset). Byte-identical guarantee holds for never-touched controls (the contracted scope).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
- [DDD · folded] "pass-through" is not capability-neutral: the OpenAI-compatible wire hides that providers DROP or 400 on params. Research-before-build (verify the seam against the live provider APIs + the gateway's real translation) caught a misleading-no-op UX before it shipped (evidence: Tin's "research first" instruction → the provider-variance findings → v2 gating change-request). [folded foundation-version 40]
- [TDD · folded] a body-capture MSW harness (assert the POST body, not component internals) makes pass-through param wiring + provider gating provable without a real gateway (evidence: chat-parameters.test.tsx body box + the model-switch gating case). [folded foundation-version 40]
- [UDD · folded] honest gating > silent no-op: disabling + annotating ("Ignored by <Provider>") an unsupported control, and omitting it from the body, is the truthful UI when the backend would silently drop it (evidence: the live-vs-gated capture). [folded foundation-version 40]
