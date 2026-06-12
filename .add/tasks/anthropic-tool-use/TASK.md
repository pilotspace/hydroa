# TASK: Anthropic tool-use translation

slug: anthropic-tool-use · created: 2026-06-13 · stage: production
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Anthropic tool-use translation — extend AnthropicCompletionUpstream's v9
translation helpers so OpenAI tools/tool_choice/tool_calls/`role:"tool"` messages
map to/from the Anthropic Messages tool shape (tools+tool_choice, tool_use content
blocks, tool_result content blocks) on the non-stream, response, AND streaming paths,
using the frozen tool-use-contract helpers. Chat-only (Anthropic has no embeddings).

Framings weighed: extend the existing pure module-level helpers in anthropic_upstream.py
(chosen — mirrors v9; the adapter class is untouched, only the 3 translation helpers
grow) · a separate anthropic_tools.py module (rejected — splits one provider's
translation across two files) · a generic cross-provider tool mapper (rejected — the
tool-use-contract task already weighed and rejected the central translator).

Must:
<must>
  - Request: OpenAI `tools:[{type:"function",function:{name,description,parameters}}]`
    → Anthropic `tools:[{name,description,input_schema}]` (parameters→input_schema).
  - Request: `tool_choice` → Anthropic `tool_choice`: "auto"→{type:"auto"},
    "required"→{type:"any"}, {type:"function",function:{name}}→{type:"tool",name},
    "none"→{type:"none"}.
  - Request: an assistant message carrying `tool_calls` → an Anthropic assistant message
    whose `content` is a block list with one `{type:"tool_use",id,name,input}` per call
    (`arguments` JSON string → `input` object via load_tool_arguments); any assistant text
    becomes a leading `{type:"text",text}` block.
  - Request: a run of consecutive `role:"tool"` messages → ONE Anthropic user message
    whose `content` is `[{type:"tool_result",tool_use_id,content},…]` (one block per tool
    message, preserving order; tool_call_id→tool_use_id).
  - Request: string `content` still maps to a string (v9 behavior preserved); a None
    assistant `content` with tool_calls yields a content block list (no empty string).
  - Response: Anthropic `tool_use` content blocks → OpenAI
    `message.tool_calls:[{id,type:"function",function:{name,arguments:<JSON string>}}]`
    (`input` object → arguments string via dump_tool_arguments); text blocks still
    concatenate into `message.content` (null when only tool_use blocks); finish_reason
    "tool_calls" (v9 map already correct).
  - Streaming: a `content_block_start` with a `tool_use` block → a first OpenAI
    `delta.tool_calls` fragment (id+name via build_tool_call_delta, tool-call index =
    order among tool_use blocks); a `content_block_delta` with `input_json_delta` →
    a later fragment (arguments_fragment = partial_json) on that tool call's index;
    the terminal usage chunk before [DONE] is preserved.
  - A request WITHOUT tools is byte-identical to v9 (no tool fields emitted); usage/error
    paths and the x-api-key auth are unchanged.
</must>
Reject:
<reject>
  - a tool-result message missing `tool_call_id` -> "tool_call_id_required" (cannot build
    a tool_result block without the correlation id).
  - non-tool inputs are never rejected by the tool path — absent tools, the v9 text
    translation runs unchanged (no new rejection on the happy text path).
</reject>
After:
<after>
  - AnthropicCompletionUpstream round-trips a full tool exchange: tools request →
    tool_calls response → `role:"tool"` follow-up → final text answer, on stream + non-stream.
  - The no-tools path stays byte-identical to v9; the frozen v9 anthropic suite stays green.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Anthropic accepts `tool_choice:{type:"none"}` to express OpenAI "none" — lowest
    confidence because "none" is the least-used branch and Anthropic's accepted set has
    shifted across API versions; if wrong: a one-line map change (none→omit tool_choice
    while keeping tools), helper-local, no caller impact.
  - [ ] Consecutive `role:"tool"` messages must be MERGED into one Anthropic user message
    (Anthropic groups tool_result blocks under one user turn) — confirm against the
    Messages tool format; if wrong: emit one user message per tool result (Anthropic
    tolerates both for the single-call common case).
  - [x] Streaming tool-call index = order among tool_use content blocks (Anthropic block
    `index` includes text blocks; the OpenAI tool_calls index counts only tool calls) —
    handled by a dedicated tool-call counter keyed off the block index.
  - [x] dump/load_tool_arguments + build_tool_call_delta + synthesize_tool_call_id come
    from the FROZEN tool-use-contract module — no re-implementation here.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: tools translate to Anthropic input_schema
  Given an OpenAI request with tools=[{type:function, function:{name:"get_weather", parameters:{...}}}]
  When the request is translated to Anthropic
  Then the body carries tools=[{name:"get_weather", description, input_schema:{...}}]
  And parameters became input_schema verbatim

Scenario: tool_choice maps to the Anthropic shape
  Given tool_choice "auto" / "required" / {function:{name:"x"}} / "none"
  When each is translated
  Then it becomes {type:"auto"} / {type:"any"} / {type:"tool",name:"x"} / {type:"none"}

Scenario: an assistant tool_calls message becomes tool_use content blocks
  Given an assistant message with tool_calls=[{id:"call_1", function:{name:"get_weather", arguments:'{"city":"Paris"}'}}]
  When translated to Anthropic
  Then the assistant content is [{type:"tool_use", id:"call_1", name:"get_weather", input:{"city":"Paris"}}]
  And arguments (JSON string) became input (object)

Scenario: consecutive tool messages merge into one user tool_result turn
  Given two consecutive role:"tool" messages (tool_call_id "call_1", "call_2")
  When translated to Anthropic
  Then one user message carries content=[{type:"tool_result", tool_use_id:"call_1", ...}, {type:"tool_result", tool_use_id:"call_2", ...}]
  And the order is preserved

Scenario: plain string content is unchanged
  Given a user message with string content "hello"
  When translated
  Then the Anthropic message content is the string "hello"
  And the no-tools path equals the v9 output

Scenario: Anthropic tool_use response becomes OpenAI tool_calls
  Given an Anthropic 200 with content=[{type:"tool_use", id:"toolu_1", name:"get_weather", input:{"city":"Paris"}}], stop_reason "tool_use"
  When translated to OpenAI
  Then message.tool_calls=[{id:"toolu_1", type:"function", function:{name:"get_weather", arguments:'{"city": "Paris"}'}}]
  And message.content is null and finish_reason is "tool_calls"

Scenario: streaming tool call emits delta.tool_calls fragments
  Given an Anthropic stream: content_block_start(tool_use id/name) then input_json_delta('{"city":') then input_json_delta('"Paris"}')
  When translated to OpenAI SSE
  Then a first chunk carries delta.tool_calls=[{index:0, id, type:"function", function:{name}}]
  And later chunks carry delta.tool_calls=[{index:0, type:"function", function:{arguments:<fragment>}}]
  And a terminal usage chunk precedes data:[DONE]

Scenario: a request without tools is byte-identical to v9
  Given a chat request with no tools field
  When translated to Anthropic (non-stream and stream)
  Then no tools/tool_choice fields are emitted
  And the body equals the v9 translation exactly

Scenario: a tool message without tool_call_id is rejected
  Given a role:"tool" message missing tool_call_id
  When the request is translated
  Then it raises an error coded "tool_call_id_required"
  And no malformed tool_result block is produced
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Extends `gateway/proxy/infrastructure/anthropic_upstream.py` — the v9 pure helpers
grow; the adapter class, auth (x-api-key), usage, error, and circuit-breaker paths
are UNCHANGED. Imports from the FROZEN `proxy/domain/tool_translation.py`.

```
_openai_to_anthropic_request(payload, *, default_max_tokens) -> dict   # EXTENDED
  + when payload["tools"]: body["tools"] = [{name, description?, input_schema} ...]
      (OpenAI function.{name,description,parameters} -> {name,description,input_schema})
  + when payload["tool_choice"]: body["tool_choice"] =
      "auto"->{type:"auto"} | "required"->{type:"any"} | "none"->{type:"none"} |
      {type:"function",function:{name}}->{type:"tool",name}
  + assistant msg with tool_calls -> {role:"assistant", content:[ {type:"text",text}? ,
      {type:"tool_use", id, name, input: load_tool_arguments(arguments)} ... ]}
  + a run of consecutive role:"tool" msgs -> ONE {role:"user", content:[
      {type:"tool_result", tool_use_id: <tool_call_id>, content} ... ]}
      raises ValueError("tool_call_id_required") when a tool msg lacks tool_call_id
  + string content unchanged (v9); no-tools body byte-identical to v9

_anthropic_to_openai(body) -> dict                                     # EXTENDED
  + tool_use content blocks -> message.tool_calls=[{id, type:"function",
      function:{name, arguments: dump_tool_arguments(input)}} ...]
  + message.content = concatenated text blocks, or null when only tool_use blocks
  + finish_reason via existing _map_finish_reason (tool_use -> "tool_calls")

_translate_anthropic_sse(events) -> Iterable[bytes]                    # EXTENDED
  + content_block_start(content_block.type=="tool_use") -> build_tool_call_delta(
      tc_index, id=block.id, name=block.name) wrapped as delta.tool_calls=[frag]
  + content_block_delta(delta.type=="input_json_delta") -> build_tool_call_delta(
      tc_index, arguments_fragment=delta.partial_json) wrapped as delta.tool_calls=[frag]
  + tc_index counts tool_use blocks only (text blocks excluded), keyed off block index
  + terminal usage chunk before data:[DONE] preserved (v9)

Schema: none — no datastore change. Billing unchanged: served id + native usage.
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-13)
Least-sure flag surfaced at freeze: [contract] `tool_choice:"none"` → `{type:"none"}`
assumes Anthropic accepts the `none` type (least-used branch; the accepted set has
shifted across API versions). If wrong: a one-line helper-local change (none → omit
tool_choice, keep tools); no caller/contract impact. Secondary [scenario]: consecutive
`role:"tool"` messages merge into ONE Anthropic user turn (tool_result blocks group
under one user message) — if a provider rejects the merge, fall back to one user message
per result; single-call common path unaffected.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (pure helpers; high coverage is cheap)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_tools_to_input_schema: req with tools / assert anthropic body tools[0]=={name,description,input_schema} (parameters->input_schema)
  - test_tool_choice_mapping[auto|required|named|none]: parametrized / assert {type:auto}|{type:any}|{type:tool,name}|{type:none}
  - test_assistant_tool_calls_to_tool_use: assistant msg w/ tool_calls / assert content==[{type:tool_use,id,name,input}] + arguments str->input obj
  - test_consecutive_tool_msgs_merge: two role:tool msgs / assert ONE user msg with 2 tool_result blocks, order preserved
  - test_string_content_unchanged: user string content / assert content=="hello" (v9 parity)
  - test_tool_use_response_to_openai: anthropic tool_use 200 / assert message.tool_calls + content None + finish_reason tool_calls
  - test_text_and_tool_use_response: mixed text+tool_use blocks / assert content==text + tool_calls present
  - test_streaming_tool_call_fragments: SSE fixture content_block_start(tool_use)+2 input_json_delta / assert first frag id+name, later frags arguments, terminal usage chunk before [DONE]
  - test_no_tools_request_byte_identical_v9: no tools / assert body == v9 _openai_to_anthropic_request output (no tools/tool_choice keys)
  - test_tool_msg_missing_id_rejected: role:tool w/o tool_call_id / assert raises ValueError("tool_call_id_required")
</test_plan>

Tests live in: `apps/gateway/tests/anthropic_tool_use/test_anthropic_tool_use.py` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): the no-tools path stays byte-identical to v9 (the v9
anthropic suite is the regression guard); tool-call ids are passed through verbatim
from the provider (Anthropic supplies real tool_use ids — no synthesis needed here);
the api_key (x-api-key) handling is untouched (never logged/echoed).
Code lives in: `apps/gateway/src/gateway/proxy/infrastructure/anthropic_upstream.py`
(the 3 pure helpers extended; adapter class unchanged); imports the FROZEN
`proxy/domain/tool_translation.py`.
Constraints: do NOT change any test or the contract; allow-list packages only (no new
deps — stdlib + frozen helpers); ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full suite 657 passed, 19 deselected (644 prior + 13 new); new + v9 anthropic suites: 29 passed
- [x] coverage did not decrease — 82.80% (≥80 gate), up from 82.59%
- [x] no test or contract was altered during build — only the 3 v9 helpers extended + new suite added; the frozen v9 anthropic_provider suite (16 tests) stays green = proof the no-tools path is byte-identical; new test file added to ruff-format exclude
- [x] concurrency / timing of the risky operation is safe — translation helpers are PURE (no IO/await/shared state); the adapter's httpx/circuit-breaker/stream paths are unchanged from v9
- [x] no exposed secrets, injection openings, or unexpected dependencies — x-api-key handling untouched; tool ids pass through from the provider (no synthesis); no new dep (stdlib + frozen tool_translation helpers); ruff S-rules + allowlist green
- [x] layering & dependencies follow CONVENTIONS.md — infra adapter imports the proxy/domain frozen helpers (correct direction: infra→domain); no upward dependency
- [x] a person reviewed and approved the change — delegated auto mode (2026-06-13); manual diff review of all 3 extended helpers; no security finding, no concurrency/architecture residue → auto-PASS per run.md

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — the 3 extended helpers are called by the AnthropicCompletionUpstream adapter (complete/stream) exactly as in v9; the new imports (build_tool_call_delta/dump_tool_arguments/load_tool_arguments) are each referenced (response tool_calls / streaming frags / request tool_use input). New helpers _tools_to_anthropic/_tool_choice_to_anthropic/_assistant_tool_calls_to_content are called inside _openai_to_anthropic_request and unit-tested.
- [x] DEAD-CODE (code) — no orphaned symbol: ruff F + pyright strict green; every new helper is exercised by the red→green suite.
- [x] SEMANTIC (prose / non-code) — N/A (code task); the §3 contract was read in full against the Anthropic Messages tool wire format before freeze.

### GATE RECORD
Outcome: PASS
Auto-resolved: delegated auto mode (2026-06-13) — complete evidence (657 passed,
82.80% cov, 0 pyright errors, ruff+allowlist green, frozen v9 suite intact); pure
translation extension, no security finding, no concurrency/architecture residue.
Reviewed by: delegated auto mode (Tin Dang) · date: 2026-06-13

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of tool_call_id_required raises (signals a
malformed client follow-up); whether the live `tool_choice:"none"`→`{type:"none"}`
branch is ever exercised (the freeze's least-sure flag).
Spec delta for the next loop: the live-verify (task 4) must drive a FULL round-trip
(tools → tool_calls → role:"tool" follow-up → final answer) through the stub to confirm
the merge-consecutive-tool-results decision against a real Anthropic-shaped exchange.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [DDD · folded] Anthropic's tool model is CONTENT-BLOCK-based (tool_use / tool_result blocks inside a message's content list), not a sibling field like OpenAI's tool_calls — translation restructures the MESSAGE shape (assistant tool_calls → content blocks; a run of role:"tool" → one user turn of tool_result blocks), not just adds a field. Evidence: _assistant_tool_calls_to_content + the merge loop; 13/13 green.
- [SDD · folded] The v9 per-provider helper triad (request/response/SSE) is the natural extension point for a richer shape: tools landed as additive branches in the SAME 3 helpers with zero adapter-class change — the v9 seam absorbed a non-trivial new request/response shape without a re-freeze. Evidence: anthropic_upstream.py adapter unchanged; only the 3 pure helpers grew.
- [TDD · folded] Two of the 10 red tests (no-tools-byte-identical, string-content-unchanged) were GREEN-BY-DESIGN from the start — they pin v9 preservation, so they MUST stay green through the build; the other 8 drive the new behavior. The frozen v9 suite (16 tests) is the load-bearing regression guard. Evidence: 2 passed at red phase, all 29 green after build.
- [ADD · folded] A streaming tool-call needs an index REMAP: Anthropic content-block index (text + tool blocks interleaved) ≠ OpenAI tool_calls index (tool calls only) — a block_to_tc dict keyed off the content-block index bridges them. This remap recurs for any provider whose stream interleaves text and tool events. Evidence: test_streaming_tool_call_fragments green with the block_to_tc mapping.
