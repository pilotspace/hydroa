# TASK: Chat tool / function calling

slug: chat-tools-functions · created: 2026-06-28 · stage: production
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

Touches (files · symbols · signatures): the chat send seam + the inspector Tools tab + the thread. `lib/hooks/use-chat-stream.ts` — `SendInput` (no tools today) + `runStream()` body builder (`SSEFrame { choices[].delta.content }` line 88-90; delta loop reads ONLY `delta.content` lines 268-271; `finishTurn()` fires unconditionally; no `tool_calls`/`finish_reason` handling). `components/chat/InspectorPanel.tsx` — the Tools tab is an `<Empty>` scaffold (chat-playground-shell) to fill. `components/chat/ChatWorkspace.tsx` — lifts state + threads `send()`; renders the `role=log` thread of `MessageRow`s. Catalog has `ToolCallCard` (cited, unbuilt). GATEWAY (verified file:line via research): FULL end-to-end tool support already — inbound `tools`/`tool_choice` forwarded (OpenAI/OpenRouter) or translated (Anthropic `_tools_to_anthropic`/`input_schema`; Gemini `functionDeclarations`; Bedrock `toolSpec`); OUTBOUND streaming emits OpenAI-shape `delta.tool_calls` (id · function.name · incremental function.arguments) for ALL providers via `tool_translation.build_tool_call_delta`; `finish_reason:"tool_calls"` emitted (NOT normalized to stop for real caller tools). NO inbound tool validation/caps (`use_cases._validate_payload` checks only model+messages). ⇒ this task is DASHBOARD-ONLY (no gateway change).
Context (working folder): `.add/milestones/chat-playground/MILESTONE.md` — In: "Tool/function calling (define JSON-schema tools, render the model's tool_calls, supply a tool result, continue the run)"; Out: "Server-side tool EXECUTION (we render tool_calls + accept a manually-supplied result; the gateway does NOT run tools)"; exit criterion: "An operator can define a tool, see the model's tool_call, supply a result, and the run continues with that result." Shared decision: pass-through-first (tools ride the existing /v1/chat/completions wire); feature-rebuild (chat tests evolve via TDD); four UI states + role=log live region preserved.
Honors (patterns / conventions): omitted-when-unset body construction (no tools ⇒ no `tools` key ⇒ byte-identical off path) — the invariant chat-parameters-panel just reinforced; tokens-only UI, WCAG 2.2 AA stable aria-labels, decorative icons aria-hidden; design-for-failure (validate tool JSON client-side before the wire; honest degrade on malformed schema); streaming/abort/cost/conversation seams + frozen aria-labels intact; the OpenAI tool-call wire shape (assistant message with `tool_calls[]`, then `role:"tool"` messages keyed by `tool_call_id`) is the reference.
Anchors the contract cites: `SendInput` (gains `tools?`/`toolChoice?`) · `SSEFrame` + the delta loop (accumulate `delta.tool_calls`, detect `finish_reason:"tool_calls"`) · a new pending-tool-calls turn state the hook exposes · `ChatWorkspace` tool state + the continue-the-run round-trip (append `role:"tool"` result messages, re-send) · `InspectorPanel` Tools tab (define/edit/remove tools) · `ToolCallCard` (render a tool_call + capture its result) · the preserved POST `/api/gw/v1/chat/completions` seam (additive `tools`/`tool_choice` only).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Chat tool / function calling — define tools, render tool_calls, supply a result, continue the run
Framings weighed: a JSON-editor tools panel + inline per-tool-call result cards wired pass-through to /v1/chat/completions (chosen — Tin's 3 AskUserQuestion decisions 2026-06-28: JSON editor · Auto/Required/None selector · inline per-card result+continue) · a structured per-field tool form (rejected — less flexible, can't paste a definition) · a single combined results panel (rejected — weaker result→call mapping) · server-side tool EXECUTION (OUT per milestone — we render + accept a manual result, the gateway never runs tools)
Must:
<must>
  - The inspector Tools tab lets an operator define 0+ tools via a JSON editor — one entry per tool holding the OpenAI function schema { name, description?, parameters (a JSON-Schema object) }; add / edit / remove. Each entry is validated client-side (parses as JSON AND has a non-empty string name AND an object parameters) before it can be sent; an invalid entry is flagged and EXCLUDED from the request, never sent malformed.
  - A tool_choice selector (Auto [default] · Required · None) sets tool_choice on the request.
  - When ≥1 VALID tool is defined, the NEXT run's POST /api/gw/v1/chat/completions body carries tools: [{ type:"function", function:{ name, description?, parameters } }, …] and tool_choice (auto|required|none). Pass-through ONLY — no gateway change.
  - Omitted-when-unset: zero valid tools ⇒ NO tools key AND NO tool_choice key ⇒ body byte-identical to today (the v40/parameters invariant).
  - The streaming consumer accumulates delta.tool_calls (assembled by index: id + function.name once, function.arguments concatenated across fragments) and detects finish_reason:"tool_calls". On a tool-call turn it commits the assistant message carrying the assembled tool_calls and enters a PENDING-TOOL-CALLS state — it does NOT treat it as an empty content turn (today's bug: content-only consumer would commit "" and fire onTurnComplete).
  - The thread renders each tool_call as a ToolCallCard: function name + pretty-printed arguments + the tool_call_id + an inline result textarea + "Submit result".
  - Continuing the run is PARTIAL-friendly (Tin's freeze refinement 2026-06-28): the operator may continue after answering SOME (or all) of a turn's tool_calls. On continue, the assistant tool_calls message (already in the thread) is followed by EXACTLY ONE role:"tool" message PER pending call — content = the operator-supplied result, or "" (empty placeholder) for a call left unanswered — each keyed by its tool_call_id, then the stream re-sends with the same tools. Every tool_call is always answered on the wire (the OpenAI API 400s if any tool_call lacks a matching tool message), but the operator is NOT forced to fill every box. If the continuation returns more tool_calls the loop repeats; if it returns content it streams normally.
  - a11y/tokens + seams: each editor/selector/result input keyed by a stable aria-label (Tools / Tool choice / Tool result); tokens only; streaming/abort/cost/conversation seams + the System prompt/Temperature/Web search/Message/Model aria-labels + role=log live region + four states stay intact; the sampling-params behaviour (chat-parameters-panel) is unaffected.
</must>
Reject:
<reject>
  - a defined VALID tool not reaching the next request body as tools[] with its tool_choice -> "tool_not_sent"
  - a malformed/invalid tool (unparseable JSON, missing name, non-object parameters) emitted to the wire -> "invalid_tool_sent"
  - the model emitting tool_calls but the UI dropping them / committing an empty assistant turn / firing onTurnComplete as a normal turn -> "tool_calls_dropped"
  - a submitted result not appended as a role:"tool" message keyed by its tool_call_id, or the run not continuing after results -> "result_not_continued"
  - zero valid tools defined yet a tools/tool_choice key appearing in the body (off path mutated) -> "tools_leaked"
</reject>
After:
<after>
  - the Tools tab defines validated JSON-schema tools; a defined tool + choice ride the next run; the model's tool_calls render as cards; supplying results appends role:"tool" messages and continues the run; defaults stay omitted (byte-identical off path); the chat suite is green by co-evolution (no seam weakened); tsc + eslint + add.py check clean.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The continue-the-run model: results captured INLINE per ToolCallCard; continuing is PARTIAL-friendly (Tin's freeze refinement) — the operator may answer some calls and continue, but the wire STILL emits a role:"tool" per pending call (unanswered ⇒ "" placeholder) because OpenAI 400s if a tool_call is unanswered. Lowest confidence: whether an EMPTY tool result is the right placeholder (vs a sentinel like "[no result provided]") — the model may behave oddly on "". If wrong: change the placeholder string only (no wire/contract shape change; assistant tool_calls → N role:"tool" by tool_call_id is fixed by OpenAI).
  - [ ] Tool validation is SHAPE-level only (JSON parses · string name · object parameters), NOT a full JSON-Schema validator — an over-permissive schema reaches the model and the model/provider rejects it honestly (already-handled upstream 4xx). Confirm shape-level is enough (vs bundling a JSON-Schema validator dependency — heavier, still not a guarantee).
  - [ ] tool_calls are accumulated by the delta `index` field (OpenAI streams multiple calls interleaved by index); a missing index defaults to 0. Confirm index-keyed assembly (vs id-keyed) matches the gateway's emitted shape (research: build_tool_call_delta emits {index,id,function}).
  - [ ] "Tools" are session state (in-memory, lifted to ChatWorkspace; persist across turns + tab switches; reset on reload) — NOT persisted server-side (no preset store; milestone OUT).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: A defined tool + choice ride the next run
  Given the Tools tab with one valid tool {name:"get_weather", parameters:{type:"object",...}} and Tool choice = Required
  When I run a turn
  Then the POST body carries tools:[{type:"function",function:{name:"get_weather",...}}] and tool_choice:"required"
  And the streaming seam (stream:true, stream_options.include_usage:true) is otherwise unchanged

Scenario: No tools ⇒ byte-identical off path
  Given no tools defined
  When I run a turn
  Then the body has NO tools key and NO tool_choice key
  And it is byte-identical to today's body -> "tools_leaked" if violated

Scenario: An invalid tool is never sent (rejection)
  Given a tool entry whose JSON does not parse (or has no name, or non-object parameters)
  When I run a turn
  Then that entry is flagged invalid and EXCLUDED from tools[]
  And no malformed tool object reaches the body -> "invalid_tool_sent"

Scenario: The model's tool_calls render (not dropped)
  Given a defined tool and a stream whose deltas carry tool_calls (id, function.name, incremental function.arguments) then finish_reason:"tool_calls"
  When the turn completes
  Then the assembled tool_call renders as a ToolCallCard (name + pretty arguments + tool_call_id)
  And the turn is held PENDING (no empty assistant turn committed, onTurnComplete NOT fired as a normal turn) -> "tool_calls_dropped"

Scenario: Supplying a result continues the run
  Given a pending tool_call card with tool_call_id "call_1"
  When I type a result and Submit
  Then a role:"tool" message {tool_call_id:"call_1", content:<result>} is appended after the assistant tool_calls message
  And the stream re-sends (same tools) to continue -> "result_not_continued"
  And a content continuation streams normally; a further tool_calls continuation renders new cards

Scenario: Partial continue still answers every call (Tin's refinement)
  Given a turn with two tool_calls "call_1" and "call_2"
  When I supply a result only for "call_1" and continue
  Then the wire appends role:"tool" for BOTH — {tool_call_id:"call_1", content:<result>} and {tool_call_id:"call_2", content:""} (empty placeholder)
  And the run continues (no tool_call left unanswered ⇒ no upstream 400) -> "result_not_continued"

Scenario: tool_choice selector maps
  Given tools defined
  When I switch Tool choice between Auto / Required / None
  Then the next body carries tool_choice "auto" / "required" / "none" respectively
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
TOOL-CALLING CONTRACT — additive · pass-through · omitted-when-unset · DASHBOARD-ONLY (gateway already supports tools end-to-end).
Seam UNCHANGED: POST /api/gw/v1/chat/completions (stream:true, stream_options.include_usage:true). NO gateway change.

TYPES (lib/hooks/use-chat-stream.ts):
  ToolDef        = { name: string; description?: string; parameters: Record<string,unknown> }   // an OpenAI function
  ToolChoice     = "auto" | "required" | "none"
  ToolCall       = { id: string; name: string; arguments: string }   // assembled from the stream (arguments = raw JSON text)
  SendInput gains OPTIONAL: tools?: ToolDef[] · toolChoice?: ToolChoice
  ChatMessage gains the OpenAI tool-turn shapes (additive, optional):
     assistant turn may carry tool_calls?: Array<{ id; type:"function"; function:{ name; arguments } }>
     a new role "tool": { role:"tool"; content:string; tool_call_id:string }

runStream() body — included ONLY when tools?.length > 0 (else BOTH keys ABSENT ⇒ byte-identical off path):
  tools: tools.map(t => ({ type:"function", function:{ name:t.name, ...(t.description?{description:t.description}:{}), parameters:t.parameters } }))
  tool_choice: toolChoice ?? "auto"
  (sampling keys from chat-parameters-panel + temperature/web_search unchanged.)

STREAM CONSUMER (the delta loop) — extend SSEFrame:
  choices[].delta.tool_calls?: Array<{ index:number; id?:string; type?:"function"; function?:{ name?:string; arguments?:string } }>
  choices[].finish_reason?: string
  Assemble by index: first frame sets id+name; every frame appends function.arguments. Content frames stream as today.
  On finish_reason === "tool_calls" (or stream end WITH assembled calls): COMMIT the assistant message with tool_calls[] +
  enter status "awaiting_tool" exposing pendingToolCalls: ToolCall[]. Do NOT fire onTurnComplete as a normal content turn.

CONTINUE API (hook) — submitToolResults(results: Array<{ tool_call_id:string; content:string }>): void
  PARTIAL-friendly (Tin's freeze refinement): `results` may cover SOME of the pending calls. The hook appends one
  { role:"tool", content, tool_call_id } per PENDING tool_call (not per supplied result) AFTER the assistant tool_calls
  message — content = the matching supplied result, else "" (empty placeholder) — so EVERY tool_call is answered on the
  wire (OpenAI 400s otherwise). Then it clears the pending state and re-runs the stream with the SAME tools/toolChoice/
  sampling (a normal new round; may itself end in tool_calls). Placeholder = "" (a §7 delta may tune the sentinel).

UI:
  InspectorPanel Tools tab (replaces the <Empty> scaffold): a list of JSON editors (aria-label "Tool definition"), each a
  textarea holding one function-schema JSON + a live valid/invalid badge + remove; an "Add tool" control; a Tool choice
  segmented (aria-label "Tool choice": Auto|Required|None). Tools/choice are LIFTED to ChatWorkspace (in-memory; persist
  across turns + inspector tab switches). Validation: JSON.parse + string name + object parameters ⇒ valid; invalid entries
  are excluded from the request (and visibly flagged).
  ToolCallCard (components/chat/ToolCallCard.tsx, NEW): renders one tool_call — function name, pretty-printed arguments,
  tool_call_id, a result textarea (aria-label "Tool result"). A single per-turn "Continue" re-sends with whatever results
  are filled (unanswered calls ⇒ "" placeholder, partial-friendly). Rendered inline in the role=log thread for an assistant
  turn that carries tool_calls.

VALIDATION/SAFETY: tool JSON validated client-side BEFORE send (shape-level: parse + name + parameters object); an invalid
  tool is never emitted. Streaming/abort/cost/conversation seams byte-identical; off/default ⇒ byte-identical body; tokens only.
INVARIANTS: pass-through only (no gateway change); the OpenAI tool-call wire shape (assistant tool_calls → role:"tool" by
  tool_call_id) is authoritative; all frozen aria-labels/testids/role=log/data-role/four-states intact.
```

Status: FROZEN @ v1 — approved by Tin
Least-sure flag surfaced at freeze: [spec] the continue-the-run model — Tin chose PARTIAL-friendly at freeze: the operator may answer some of a turn's tool_calls and continue; the wire still emits a role:"tool" for EVERY pending call (unanswered ⇒ "" placeholder) because OpenAI 400s on an unanswered tool_call. Residual risk: whether "" is the right placeholder vs a sentinel like "[no result provided]" (the model may behave oddly on empty) — a §7-delta tweak, no wire/contract shape change. Secondary [contract]: tool validation is SHAPE-level (parse + string name + object parameters), not a full JSON-Schema validator — an over-permissive schema reaches the model and is rejected honestly upstream (avoids a heavy dep that still wouldn't guarantee correctness).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥80% per project; net-additive (tools wiring + stream tool-call handling + continue round-trip), behavior-preserving — zero existing assertions weakened.
Plan (one test per scenario — body-capture + mocked tool-call stream via MSW, the chat-parameters harness):
<test_plan>
  - test_tools_and_choice_reach_body: define a valid tool (JSON editor) + Tool choice Required → run → body.tools==[{type:"function",function:{name,description,parameters}}], body.tool_choice=="required"; stream/stream_options unchanged.
  - test_no_tools_omitted_byte_identical: no tools → run → body has NO tools key AND NO tool_choice key (off path).
  - test_invalid_tool_excluded (rejection): enter unparseable JSON (and a no-name entry) → the entry shows an "invalid" badge AND is excluded from tools[]; if it's the only entry, no tools key ships -> "invalid_tool_sent".
  - test_tool_calls_render_not_dropped (rejection): tool defined, stream emits delta.tool_calls (id+name then incremental arguments) + finish_reason:"tool_calls" → a ToolCallCard renders (name + pretty args + tool_call_id); NO empty assistant content bubble committed; onTurnComplete NOT fired as a normal turn -> "tool_calls_dropped".
  - test_supply_result_continues (rejection): from the pending card, type a Tool result + Continue → the NEXT request body.messages ends with the assistant tool_calls message then {role:"tool", tool_call_id, content:<result>}; tools still sent -> "result_not_continued".
  - test_partial_continue_answers_all (rejection): two tool_calls, answer only call_1 → Continue → next body.messages carries role:"tool" for BOTH (call_1 content=<result>, call_2 content=="") -> "result_not_continued".
  - test_tool_choice_maps: switch Auto/Required/None → body.tool_choice == "auto"/"required"/"none".
  Existing chat suites stay green UNCHANGED (no tools ⇒ no tools/tool_choice key ⇒ byte-identical; content-only streams unaffected by the new tool_calls branch); co-evolve only on a real collision.
</test_plan>

Tests live in: `apps/dashboard/tests-bff/chat-tools.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/lib/hooks/use-chat-stream.ts` `apps/dashboard/lib/chat/tool-defs.ts` `apps/dashboard/components/chat/ChatWorkspace.tsx` `apps/dashboard/components/chat/InspectorPanel.tsx` `apps/dashboard/components/chat/ToolsEditor.tsx` `apps/dashboard/components/chat/ToolCallCard.tsx` `apps/dashboard/tests-bff/chat-tools.test.tsx` `apps/dashboard/tests-bff/use-chat-stream-tools.test.ts`
Strategy (ordered batches): 1. NEW red suite `chat-tools.test.tsx` (7 cases) → red. 2. `lib/chat/tool-defs.ts` — ToolDef/ToolChoice types + `parseToolDef(json)` (shape-level validate) + `toWireTools(defs)`. 3. use-chat-stream: extend SendInput (tools/toolChoice) + ChatMessage (tool_calls / role:"tool"+tool_call_id) + SSEFrame (delta.tool_calls + finish_reason); assemble tool_calls by index; on finish_reason:"tool_calls" commit the assistant tool_calls msg + status "awaiting_tool" exposing pendingToolCalls; add `submitToolResults()` (answer EVERY pending call, "" placeholder for blanks) that re-runs the stream. 4. `ToolsEditor` (JSON-editor list + add/remove + valid badge + Tool choice segmented). 5. `ToolCallCard` (name + pretty args + tool_call_id + Tool result textarea). 6. ChatWorkspace: lift tools/toolChoice state, thread into send(), render cards for assistant turns carrying tool_calls + wire Continue → submitToolResults. 7. green + tsc + eslint + add.py check + capture + refute-read.
Known-problem fixes: omitted-when-unset → tools/tool_choice only when ≥1 VALID tool (guards tools_leaked) · invalid tool → parseToolDef returns null + a badge; null entries filtered before the wire (guards invalid_tool_sent) · tool_calls dropped → the stream loop must branch on delta.tool_calls AND finish_reason BEFORE the unconditional finishTurn (guards tool_calls_dropped — today's real bug) · partial continue → submitToolResults iterates PENDING calls (not supplied results) so every tool_call gets a role:"tool" (guards a provider 400) · index assembly → accumulate by delta.index, first frame sets id+name, every frame concatenates arguments · streaming content path UNCHANGED (the tool branch is additive) so the 801 existing chat tests stay byte-identical · JSON editor tests use fireEvent.change (not user.type — JSON braces are userEvent specials).
Strategy actually used: as planned (7 batches). The streaming-consumer branch triggers on `assembled.length > 0` (a robust superset of the contracted finish_reason read — §7 C1). Refactored send() into a shared `startStream(input)` reused by both send() and submitToolResults() so a continuation replays the same tools/system/sampling. Post-refute strengthening added a hook-level invariant test (`use-chat-stream-tools.test.ts`, declared in scope) for D1 (onTurnComplete-not-fired) + a mixed valid/invalid UI case for B1 — both close contracted-invariant coverage gaps, no test weakened, contract untouched.
Safety rule (feature-specific): every tool_call is answered on the wire before the next turn (OpenAI 400s otherwise); an invalid tool never ships; off/default ⇒ byte-identical body; the streaming/abort/cost/conversation seams stay byte-identical; no gateway change.
Code lives in: `apps/dashboard/components/chat` + `apps/dashboard/lib`
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
- [x] The Tools tab defines validated JSON-schema tools (Add/edit/remove + valid/invalid badge) + an Auto/Required/None tool_choice selector; a valid tool + choice ride the next run as tools[]/tool_choice — confirmed by the captured Tools tab (chat-tools.png: Required active, Tool 1 Valid, Tool 2 Invalid JSON) + test_tools_and_choice_reach_body / test_tool_choice_maps; no tools ⇒ neither key (test_no_tools_omitted_byte_identical, real `in`-operator check).
- [x] An invalid tool entry is flagged and EXCLUDED from the wire — confirmed by test_invalid_tool_excluded (all-invalid) + test_mixed_valid_invalid_only_valid_ships (B1: one valid + one broken → only the valid TOOL ships) + the "Invalid JSON" badge in the capture. Wire is built from validTools (parseToolDef’d), never raw drafts.
- [x] The model's streamed tool_calls (assembled by index across fragments) render as ToolCallCards (name + pretty args + tool_call_id) with a Continue affordance — NOT dropped as an empty turn — confirmed by test_tool_calls_render_not_dropped + the hook-level invariant test (awaiting_tool, pendingToolCalls populated, onTurnComplete NOT fired, no empty content commit) + the capture.
- [x] Supplying results continues the run: every pending call is answered on the wire as role:"tool" keyed by tool_call_id (unanswered ⇒ "" placeholder, partial-friendly — submitToolResults maps over PENDING calls, not supplied results) and the stream re-sends with the same tools — confirmed by test_supply_result_continues + test_partial_continue_answers_all + the hook continuation test (onTurnComplete fires once on the content turn).
- [x] The off path is byte-identical (no tools ⇒ no tools/tool_choice; content-only streams unaffected) — confirmed by the ~801 pre-existing chat tests staying green untouched (full suite 827/827; +10 chat-tools incl. 3 strengthening cases).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced: tool-defs (parseToolDef/toWireTools/ToolDef/ToolChoice/ToolCall) ← use-chat-stream body builder + ChatWorkspace validTools; ToolsEditor ← InspectorPanel Tools tab; ToolCallCard ← ChatWorkspace ToolCallTurn; submitToolResults/pendingToolCalls ← ChatWorkspace handleContinue. tsc clean (all referenced).
- [x] DEAD-CODE (code) — no orphaned symbol; tsc + eslint clean (0). Empty still used by the Code tab. The finish_reason fixture frame is realistic but not the trigger (see §7 C1).
- [x] SEMANTIC (prose / non-code) — read the frozen §3 contract in full: every Must/Reject has a real test assertion that fails if the guarded code is removed (refute-read confirmed all 5 rejections covered).

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: agent ad96e9a9325cbea97 (frontend-expert) · adversarially checked: byte-identical off path, invalid-tool exclusion (wire from validTools not drafts), index-assembly across fragments, dropped-empty-turn (finishToolTurn never calls onTurnComplete; strict if/else with finishTurn), partial-continue (maps PENDING not supplied), re-send fidelity (same tools/system/sampling), security (JSON.parse-only, React-escaped render — no eval/XSS), state leaks (reset/load clear pendingToolCalls). Verdict EARNED-GREEN, no security/cheat findings. Two MED + one LOW NON-BLOCK coverage gaps (D1 onTurnComplete-not-fired unasserted; B1 mixed valid/invalid untested; C1 finish_reason not the trigger) — D1 + B1 CLOSED by strengthening (hook test + mixed case); C1 recorded as a §7 delta (impl is a robust superset, observable behaviour correct).

### GATE RECORD
Outcome: PASS
Reviewed by: self (auto-gate, autonomy:auto) · refute-read by agent ad96e9a9325cbea97 · date: 2026-06-29

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin)
- [AI] build — strategy used: as planned (7 batches). The streaming-consumer branch triggers on `assembled.length > 0` (a robust superset of the contracted finish_reason read — §7 C1). Refactored send() into a shared `startStream(input)` reused by both send() and submitToolResults() so a continuation replays the same tools/system/sampling. Post-refute strengthening added a hook-level invariant test (`use-chat-stream-tools.test.ts`, declared in scope) for D1 (onTurnComplete-not-fired) + a mixed valid/invalid UI case for B1 — both close contracted-invariant coverage gaps, no test weakened, contract untouched.
- [AI] verify — gate PASS (reviewed by self (auto-gate, autonomy:auto))

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] C1: the tool-turn trigger is `assembled.length > 0`, not the contracted `finish_reason:"tool_calls"` read (evidence: refute-read — impl is a robust superset that survives a dropped finish_reason frame, but the contract clause is untested). Decide: keep robust trigger + amend wording, OR also read finish_reason for an explicit signal.
- [SPEC · open] Persist tool-call turns + role:"tool" results to the conversation store (today onTurnComplete only fires on the final content turn, so the intermediate tool round-trip is live-thread-only and lost on resume) (evidence: finishToolTurn deliberately skips onTurnComplete).
- [SPEC · open] Parallel/streaming tool-result execution adapter (today results are operator-typed; a real tool runner would auto-fill) — server-side EXECUTION is explicitly OUT of this milestone (evidence: MILESTONE.md Out-of-scope).
- [SPEC · open] During awaiting_tool the composer Send is still enabled — sending a new user message instead of continuing produces an out-of-order thread (user after assistant tool_calls). Consider gating Send to Continue while awaiting_tool (evidence: isStreaming=false in awaiting_tool).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
- [TDD · folded] An auto-PASS suite can be green yet leave a forbidden-behaviour unasserted (D1: "onTurnComplete must NOT fire on a tool turn" was structurally true but unpinned). The adversarial refute-read caught it; closing it needed a hook-level test, not another UI test (evidence: ChatWorkspace owns onTurnComplete internally — the seam is only assertable at useChatStream). [folded foundation-version 40]
- [ADD · folded] A frozen-contract clause can be satisfied by a more-robust SUPERSET of its literal wording (C1: detect-tool-calls vs detect-finish_reason). Honest path = record the deviation as a SPEC delta, not silently edit the contract (evidence: refute-read flagged the literal mismatch; behaviour is correct). [folded foundation-version 40]
