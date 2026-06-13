# TASK: Anthropic JSON-mode via tool-coercion + unwrap

slug: anthropic-json-mode · created: 2026-06-13 · stage: production
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Anthropic JSON-mode — extend AnthropicCompletionUpstream's v9/v10 helpers so a
provider=anthropic chat request carrying `response_format` returns JSON-conformant
`message.content`. Anthropic has NO native response_format field, so json_schema is satisfied
by REUSING the v10 tool seam: the frozen-contract `build_json_coercion_tool` emits ONE
synthetic forced tool (`json_output`) whose input_schema IS the requested schema; on the
return leg the gateway UNWRAPS that tool_use block back into `message.content` (a JSON
string) so the caller sees content, not a tool_call. json_object (no schema) is satisfied by
a system-instruction strategy. Touches all THREE helpers (request append/force · response
unwrap · streaming unwrap).

Framings weighed: REUSE the v10 tool helpers — build the coercion tool (OpenAI shape) and run
it through `_tools_to_anthropic`/`_tool_choice_to_anthropic`, then unwrap by NAME on the way
back (chosen — the frozen contract is built for exactly this; minimal new code) · a bespoke
Anthropic "json mode" with no tool (rejected — Anthropic has no such field; the tool-force is
the documented way to guarantee schema-valid JSON) · prefill-only (assistant message starting
`{`) (rejected for json_schema — gives no schema enforcement; kept only as the json_object
strategy where there is no schema).

Must:
<must>
  - REQUEST (`_openai_to_anthropic_request`): after the existing tools/tool_choice handling,
    call `extract_response_format(payload)`. For json_schema: `build_json_coercion_tool(rf,
    existing_tool_names=<caller tool names>)` → APPEND the (translated) coercion tool to
    `result["tools"]` ALONGSIDE any caller tools, and set `result["tool_choice"]` to FORCE it
    ({type:"tool", name:"json_output"}). For json_object: append a JSON-only instruction to
    the top-level `system` string (no schema to force). A reserved-name collision raises
    ERR_RESERVED_TOOL_NAME (from the contract helper).
  - RESPONSE (`_anthropic_to_openai`): a tool_use block whose name is the coercion tool
    (`is_coercion_tool_call`) is UNWRAPPED — its `input` becomes `message.content` (a JSON
    string via `unwrap_coerced_tool_input`) and it MUST NOT appear in `tool_calls`;
    finish_reason is "stop" (not "tool_calls") when the coercion tool was the reason. A
    caller-supplied tool's tool_use still surfaces as a normal `tool_calls` entry
    (composition preserved).
  - STREAMING (`_translate_anthropic_sse`): when a streamed tool_use block IS the coercion
    tool, its `input_json_delta` fragments are emitted as `delta.content` (the partial_json
    string), NOT `delta.tool_calls`; that block is excluded from the block_to_tc tool index;
    the terminal finish_reason is "stop" when the coercion tool was the only tool_use.
  - COMPOSITION: a request with BOTH caller `tools` and a json_schema response_format keeps
    them separate — the coercion tool is appended (distinct name), caller tools still map and
    (if called) still surface as tool_calls; only the coercion block is unwrapped.
  - A request WITHOUT response_format (or {type:"text"}) is BYTE-IDENTICAL to v9/v10; the
    frozen v9 + v10 anthropic suites stay green. x-api-key auth + native usage untouched.
</must>
Reject:
<reject>
  - response_format with an unsupported `type` -> "ERR_UNSUPPORTED_RESPONSE_FORMAT"
    (propagated from extract_response_format; not dispatched).
  - json_schema with no schema object -> "ERR_INVALID_JSON_SCHEMA" (from the extractor).
  - a caller tool named `json_output` + a json_schema response_format -> "ERR_RESERVED_TOOL_NAME"
    (from build_json_coercion_tool; the reserved name is gateway-owned).
</reject>
After:
<after>
  - A provider=anthropic chat request with response_format json_schema returns a JSON string
    in `message.content` (finish "stop"), non-stream + streaming, with no tool_calls leak;
    json_object returns JSON content via the system instruction; no-response_format
    byte-identical; frozen anthropic suites pass.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Anthropic reliably returns the coercion tool's `input` as schema-valid JSON under a
    forced tool_choice, and UNWRAPPING that input round-trips to the caller's expected JSON —
    lowest confidence because it depends on Anthropic honoring tool_choice-forced structured
    input for arbitrary schemas; if wrong: the unwrapped content is malformed/empty and the
    live json_schema check (task 4) fails loudly (no false pass); fallback is the json_object
    system-instruction path. (This is the BUNDLE's top flag, inherited from the contract.)
  - [ ] Forcing the coercion tool_choice does not break a request that ALSO carries caller
    tools — confirm the coercion tool is appended (not replacing) and the forced choice is
    acceptable (the caller asked for JSON output, so forcing json_output is correct).
  - [ ] The SSE coercion-block detection (by name at content_block_start) correctly routes
    later input_json_delta fragments to content — confirm the streamed block index is stable
    between content_block_start and its deltas (Anthropic guarantees per-index ordering).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: json_schema appends a forced coercion tool (request)
  Given an OpenAI request with response_format json_schema {name,schema}
  When translated to an Anthropic request
  Then tools contains a tool named "json_output" whose input_schema IS the schema
  And tool_choice == {type:"tool", name:"json_output"}

Scenario: json_object appends a system instruction (request)
  Given an OpenAI request with response_format {type:"json_object"}
  When translated
  Then the top-level system string instructs JSON-only output
  And no coercion tool is added (there is no schema to force)

Scenario: coercion tool_use unwraps to message.content (response)
  Given an Anthropic 200 with a tool_use block named "json_output", input {city:Paris}
  When mapped to OpenAI
  Then message.content == '{"city":"Paris"}' (a JSON string)
  And message has NO tool_calls and finish_reason is "stop"

Scenario: caller tool still surfaces as tool_calls (composition, response)
  Given an Anthropic 200 with a "json_output" tool_use AND a "get_weather" tool_use
  When mapped
  Then message.content is the json_output JSON string
  And message.tool_calls contains exactly the get_weather call (not json_output)

Scenario: streamed coercion block streams as content (streaming)
  Given an SSE with a tool_use content_block named "json_output" + input_json_delta frags
  When translated
  Then delta.content fragments carry the partial JSON (NOT delta.tool_calls)
  And the terminal finish_reason is "stop"

Scenario: no response_format is byte-identical to v9/v10
  Given a request with no response_format (or {type:"text"})
  When translated
  Then no coercion tool / no JSON system instruction is added (byte-identical)

Scenario: unsupported type rejected
  Given response_format {type:"toml"}
  When translated
  Then it raises ERR_UNSUPPORTED_RESPONSE_FORMAT

Scenario: json_schema missing schema rejected
  Given response_format {type:"json_schema", json_schema:{name:"w"}}
  When translated
  Then it raises ERR_INVALID_JSON_SCHEMA

Scenario: caller tool claiming the reserved name rejected
  Given a caller tool named "json_output" AND a json_schema response_format
  When translated
  Then it raises ERR_RESERVED_TOOL_NAME
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Extends 3 helpers in anthropic_upstream.py. Builds on the FROZEN response-format-contract
(extract_response_format, build_json_coercion_tool, is_coercion_tool_call,
unwrap_coerced_tool_input, JSON_COERCION_TOOL_NAME) + reuses v10 _tools_to_anthropic /
_tool_choice_to_anthropic.

```
_openai_to_anthropic_request(payload, *, default_max_tokens) -> dict   # EXTENDED
  rf = extract_response_format(payload)            # None | json_object | json_schema
  if rf type == json_schema:
      caller_names = [t.function.name for t in payload.get("tools", [])]
      tool, choice = build_json_coercion_tool(rf, existing_tool_names=caller_names)  # may raise ERR_RESERVED_TOOL_NAME
      result["tools"]       = result.get("tools", []) + _tools_to_anthropic([tool])  # APPEND
      result["tool_choice"] = _tool_choice_to_anthropic(choice)                       # FORCE json_output
  elif rf type == json_object:
      result["system"] = (result.get("system","") + "\n\n" + _JSON_OBJECT_INSTRUCTION).strip()
  # rf None / text -> unchanged (byte-identical v9/v10)

_anthropic_to_openai(body) -> dict   # EXTENDED
  for each tool_use block:
    if is_coercion_tool_call(block.name):  content = unwrap_coerced_tool_input(block.input)  # JSON string; NOT a tool_call
    else:                                  -> tool_calls entry (v10, unchanged)
  message.content = coerced_content if present else (text if text else (None if tool_calls else ""))
  finish_reason = "stop" if (coercion unwrapped AND no real tool_calls) else _map_finish_reason(stop_reason)

_translate_anthropic_sse(events) -> bytes   # EXTENDED
  content_block_start tool_use:
    if is_coercion_tool_call(name): mark coercion_block_index = index; saw_coercion=True; (NO tool_calls frag, NOT in block_to_tc)
    else: v10 path (emit first tool_calls frag, register block_to_tc)
  content_block_delta input_json_delta:
    if index == coercion_block_index: emit delta:{content: partial_json}
    else: v10 tool_calls arguments frag
  terminal finish_reason: "stop" if (saw_coercion AND tc_count==0) else mapped
```
Schema: NONE — no DB tables/columns touched (request/response/stream translation only).

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-13)
Least-sure flag surfaced at freeze: [scenario] Anthropic returns the forced coercion tool's
`input` as schema-valid JSON and the UNWRAP round-trips to the caller's expected JSON — the
BUNDLE's top flag (inherited from the contract); why: depends on Anthropic honoring
tool_choice-forced structured input for arbitrary schemas; cost if wrong: unwrapped content
malformed/empty, the live json_schema check (task 4) fails loudly (no false pass); fallback
is the json_object system-instruction path. Secondary [contract]: forcing the coercion
tool_choice overrides any caller tool_choice — acceptable because the caller asked for JSON
output, and caller tools remain available (just not forced).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: every new branch (request append/force + system-instruction, response
unwrap, SSE content-route + finish override) covered; suite-wide ≥80% held.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_json_schema_appends_forced_coercion_tool: json_output tool (input_schema=schema) + forced tool_choice
  - test_json_object_appends_system_instruction: system instructs JSON; no coercion tool
  - test_json_schema_composes_with_caller_tools: both get_weather + json_output present
  - test_no_response_format_byte_identical (GREEN-BY-DESIGN): no tools/tool_choice added
  - test_unsupported_type_rejected / test_json_schema_missing_schema_rejected / test_caller_tool_reserved_name_rejected → the 3 contracted errors
  - test_coercion_tool_use_unwraps_to_content: tool_use json_output → message.content JSON, no tool_calls, finish stop
  - test_coercion_composes_with_caller_tool_call: coercion → content; get_weather → tool_calls
  - test_streaming_coercion_block_streams_as_content: input_json_delta → delta.content, no tool_calls, finish stop
</test_plan>
10 tests: 9 drive new behavior (RED) + 1 green-by-design (no-rf byte-identical).

Tests live in: `anthropic-json-mode/tests/` declared as `apps/gateway/tests/anthropic_json_mode/test_anthropic_json_mode.py` · ran RED (9 failed, 1 passed) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): the coercion tool is APPENDED (never replaces caller tools);
only the json_output block is unwrapped (no caller tool_call leaks into content / vice versa);
no-rf path leaves the body byte-identical; x-api-key auth untouched.
Code lives in: `apps/gateway/src/gateway/proxy/infrastructure/anthropic_upstream.py`
Constraints: do NOT change any test or the contract; reuse the frozen contract helpers +
v10 _tools_to_anthropic/_tool_choice_to_anthropic; no new dependency.

Built: imported build_json_coercion_tool/extract_response_format/is_coercion_tool_call/
unwrap_coerced_tool_input + _JSON_OBJECT_INSTRUCTION constant. REQUEST: after tools/
tool_choice, append the coercion tool (dict(coercion_tool) → _tools_to_anthropic) + force its
choice (json_schema), or append the system instruction (json_object). RESPONSE: unwrap a
json_output tool_use block into message.content + finish "stop" when no real tool calls. SSE:
coercion block → delta.content fragments (excluded from block_to_tc), finish "stop" override.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — anthropic blast radius 39/39 (json_mode 10 + tool_use 13 + provider 16); full translation+dispatch blast radius 118/118 (no-DB, deterministic); ruff + pyright clean
- [x] coverage did not decrease — all new branches exercised; additive lines only
- [x] no test or contract was altered during build — frozen v9 + v10 anthropic suites stay green (no-rf byte-identical); tests + §3 untouched
- [x] concurrency / timing of the risky operation is safe — pure translation; the SSE state (coercion_block_index/saw_coercion) is per-call local, no shared state
- [x] no exposed secrets, injection openings, or unexpected dependencies — x-api-key + never-Bearer invariant untouched; coercion tool name is the gateway constant + caller's own schema; no new dep
- [x] layering & dependencies follow CONVENTIONS.md — infra adapter imports the domain seam (response_format_translation + tool_translation), same direction as v10
- [x] a person reviewed and approved the change — delegated auto mode (Tin Dang, 2026-06-13)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — all 4 contract helpers imported (lines 32-37) + used: extract in request, build_json_coercion_tool in request, is_coercion_tool_call in response+SSE, unwrap in response; the new branches asserted by 9 tests
- [x] DEAD-CODE (code) — no orphan: every branch (request schema/object, response unwrap, SSE content-route + finish override) is on the live path and test-covered; pyright strict clean
- [x] SEMANTIC (prose / non-code) — re-read the 3-helper diff: coercion tool APPENDED not replacing (result.get("tools",[]) + …); response prefers coerced_content then text; SSE coercion block excluded from block_to_tc and routed to delta.content; finish "stop" only when saw_coercion and tc_count==0 — matches §3 verbatim

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (delegated auto mode) · date: 2026-06-13

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): json_schema coercion success rate (does the unwrapped
content parse + validate), tool_calls-leak rate (must be 0 — coercion never surfaces as a
tool_call), finish_reason=stop rate on JSON-mode, no-rf byte-identical rate.
Spec delta for the next loop: a provider WITHOUT a native directive (Anthropic) borrows a
SIBLING seam (v10 tool-use) and INVERTS it on the return leg (tool_use → content) — the same
gateway-owned coercion primitive serves both request (build) and response/stream (unwrap).
The cross-seam composition (response_format reuses tools) is the milestone's key insight.

### Competency deltas
- [DDD · open] a provider with no native structured-output field satisfies json_schema by COERCION — a synthetic forced tool whose tool_use is UNWRAPPED back into message.content (tool_use → content inversion); the gateway-owned json_output name is the correlation key on every leg (request build, response unwrap, stream route) (evidence: anthropic-json-mode 10/10 green incl. streaming unwrap).
- [SDD · open] response_format COMPOSES with v10 tools rather than conflicting — the coercion tool is APPENDED alongside caller tools, only the json_output block is unwrapped, caller tools still surface as tool_calls; a new directive seam reused a prior seam's machinery wholesale (evidence: test_coercion_composes_with_caller_tool_call + test_json_schema_composes_with_caller_tools green).
- [ADD · open] the streaming unwrap needed THREE coordinated touchpoints in one SSE pass (content_block_start marks the coercion block, input_json_delta routes by that index to delta.content, message_delta overrides finish to "stop") — a per-call state pair (coercion_block_index/saw_coercion) bridges them; the same shape recurs for any provider that streams a coerced block (evidence: test_streaming_coercion_block_streams_as_content green).
