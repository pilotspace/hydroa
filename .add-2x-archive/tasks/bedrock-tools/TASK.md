# TASK: Converse toolConfig <-> v10 canonical tool seam (toolUse/toolResult <-> tool_calls)

slug: bedrock-tools · created: 2026-06-15 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it. -->
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
- `apps/gateway/src/gateway/proxy/infrastructure/bedrock_upstream.py` (MODIFY) — extend the two pure mapping
  helpers for tools (NON-streaming round-trip): `_openai_to_converse_request` gains toolConfig + assistant
  tool_calls → toolUse content + role:"tool" → toolResult content; `_converse_to_openai` gains output toolUse
  blocks → OpenAI `tool_calls`. Add module helpers `_tools_to_converse(tools)`, `_tool_choice_to_converse(choice)`,
  `_assistant_tool_calls_to_content(msg)`. Import `load_tool_arguments`/`dump_tool_arguments` from the v10 seam.
- REUSE: `gateway.proxy.domain.tool_translation` (v10 frozen canonical seam) — `load_tool_arguments` (OpenAI
  arguments JSON string → dict, for the request toolUse.input) and `dump_tool_arguments` (dict → JSON string,
  for the response tool_calls.function.arguments). Bedrock toolUse carries a provider id (toolUseId) like
  Anthropic → NO synthesize_tool_call_id needed.

Context (working folder): apps/gateway. Tests: `cd apps/gateway && uv run pytest -p no:cacheprovider --no-cov
-q tests/bedrock_tool_use`. Tool round-trip is unit-tested on the pure helpers directly + one end-to-end
complete() via httpx.MockTransport (mirrors tests/anthropic_tool_use). The Converse tool shapes were CONFIRMED
against the botocore bedrock-runtime service model: toolConfig={tools:[{toolSpec:{name,description,inputSchema:{json:<schema>}}}],
toolChoice:union{auto:{}|any:{}|tool:{name}}}; output content block toolUse={toolUseId,name,input:<dict>};
request content block toolResult={toolUseId,content:[{text}|{json}],status}.

Honors (patterns / conventions):
- MIRROR AnthropicCompletionUpstream's tool handling (anthropic_upstream.py): Bedrock is the Anthropic-shaped
  case (provider-assigned toolUseId; input is a dict, not a JSON string) — same `_assistant_tool_calls_to_content`
  + consecutive `role:"tool"` collapse-into-one-user-message pattern; differs only in the Converse key names
  (toolUse/toolResult/inputSchema.json/toolChoice object keys vs Anthropic's tool_use/tool_result/input_schema).
- v10 CANONICAL SHAPES (frozen): request `tools`=[{type:function,function:{name,description?,parameters?}}],
  `tool_choice`∈{"auto","none","required",{type:function,function:{name}}}; response `tool_calls`=[{id,type:"function",
  function:{name,arguments:<JSON string>}}]; `role:"tool"` message={role,tool_call_id,content}.
- CONTENT CONVENTION (anthropic/gemini parity): assistant `content` = None when only tool_calls, the text string
  when text is present (never "" alongside tool_calls). finish_reason "tool_calls" (stopReason "tool_use" — already
  mapped in `_map_finish_reason`).
- BILLING/SECRET/RETRY unchanged from task 2 (this is a pure-mapping DEPTH task; no IO/signing change).

Anchors the contract cites: `_openai_to_converse_request`, `_converse_to_openai`, `_tools_to_converse`,
`_tool_choice_to_converse`, `_assistant_tool_calls_to_content`, `_map_finish_reason`; `load_tool_arguments`,
`dump_tool_arguments`; the Converse toolConfig/toolUse/toolResult shapes + OpenAI tools/tool_calls canonical shapes.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Bedrock tool-use — round-trip OpenAI tools/tool_calls ⇄ Bedrock Converse toolConfig/toolUse/toolResult
on the NON-streaming chat path, reusing the v10 canonical seam.

Framings weighed: extend the existing Converse mapping helpers + reuse v10 tool_translation (chosen — Bedrock
toolUse is Anthropic-shaped, so the proven Anthropic mapping pattern applies one-to-one; one translation surface)
· a separate Bedrock tool module (rejected — the mapping belongs with the request/response translators it
extends) · synthesize ids (rejected — Bedrock supplies toolUseId, like Anthropic).

Must:
<must>
  - _openai_to_converse_request maps payload "tools" → toolConfig.tools=[{toolSpec:{name,description?,inputSchema:{json:<parameters>}}}]
    and payload "tool_choice" → toolConfig.toolChoice ("auto"→{auto:{}}, "required"→{any:{}}, {type:function,
    function:{name}}→{tool:{name}}). toolConfig is OMITTED when no tools are present.
  - an assistant message carrying tool_calls maps to Converse content blocks: any text → {text}; each tool_call →
    {toolUse:{toolUseId:<id>, name, input: load_tool_arguments(arguments)}} (arguments JSON string → dict).
  - role:"tool" messages map to a user turn of toolResult blocks {toolResult:{toolUseId:<tool_call_id>,
    content:[{text:<content>}], status:"success"}}; CONSECUTIVE tool messages collapse into ONE user message
    (mirrors the Anthropic pattern).
  - _converse_to_openai maps output.message.content toolUse blocks → OpenAI tool_calls=[{id:<toolUseId>,
    type:"function", function:{name, arguments: dump_tool_arguments(input)}}]; content = the concatenated text,
    or None when only tool_calls are present; finish_reason "tool_calls" via the existing stopReason map.
  - the plain-text path (no tools) is BYTE-IDENTICAL to task 2 (additive only); secret/signing/billing unchanged.
</must>
Reject:
<reject>
  - tool_choice "none" -> toolChoice is OMITTED (Bedrock Converse has no explicit "none"; documented limitation —
    tools stay declared, the model is not forced). Never emit an invalid toolChoice key.
  - a tool_call with malformed/non-JSON arguments -> load_tool_arguments returns the raw value (fail-safe, never
    raises); the request still builds (no crash).
  - a toolResult/tool message whose tool_call_id is absent -> still emit the toolResult with whatever id is given
    (no silent drop); correlation is the caller's contract.
</reject>
After:
<after>
  - a tool-use request to a Bedrock model round-trips: OpenAI tools→toolConfig, assistant tool_calls→toolUse,
    tool results→toolResult on the request; Bedrock toolUse→OpenAI tool_calls with finish_reason "tool_calls" on
    the response. No regression on the plain-text path.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ CONVERSE TOOL FIELD NAMES — lowest confidence: the exact key names (inputSchema.json vs inputSchema;
    toolChoice object-keys auto/any/tool; toolResult.status). Mitigation: CONFIRMED against the botocore
    bedrock-runtime service model (Converse op input + ContentBlock union) → high confidence now; task-6 live
    double-pass confirms against a real-shaped stub. If wrong: 400 from Bedrock — caught by the live verify.
    Confidence: 0.9 (was the risk; oracle-narrowed).
  - [x] Bedrock toolUse carries a provider id (toolUseId) like Anthropic — confirmed (service model toolUse has
    toolUseId); no id synthesis. Confidence: 0.95.
  - [x] streaming tool-call deltas are OUT OF SCOPE here (the milestone carries "parallel-tool streaming" as a
    separate open; exit criterion 4 is request+response translation). Confidence: 0.95.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: BT1 — tools → toolConfig
  Given an OpenAI request with tools=[{type:function,function:{name,description,parameters}}]
  When _openai_to_converse_request runs
  Then body.toolConfig.tools == [{toolSpec:{name,description,inputSchema:{json:<parameters>}}}]

Scenario: BT2 — tool_choice variants
  Given tool_choice "auto" / "required" / {type:function,function:{name:"x"}} / "none"
  When _openai_to_converse_request runs
  Then toolChoice == {auto:{}} / {any:{}} / {tool:{name:"x"}} / (omitted for "none")
  And the toolConfig key is omitted entirely when no tools are present

Scenario: BT3 — assistant tool_calls → toolUse content
  Given an assistant message {content:null, tool_calls:[{id:"tu_1",function:{name:"get_weather",arguments:'{"city":"Paris"}'}}]}
  When _openai_to_converse_request runs
  Then that message → {role:"assistant", content:[{toolUse:{toolUseId:"tu_1",name:"get_weather",input:{"city":"Paris"}}}]}

Scenario: BT4 — tool results → toolResult, consecutive collapse
  Given two consecutive role:"tool" messages with tool_call_ids "tu_1","tu_2"
  When _openai_to_converse_request runs
  Then they collapse into ONE user message content=[{toolResult:{toolUseId:"tu_1",...}},{toolResult:{toolUseId:"tu_2",...}}]

Scenario: BT5 — toolUse response → tool_calls
  Given a Converse 200 whose output.message.content=[{toolUse:{toolUseId:"tu_9",name:"get_weather",input:{"city":"Paris"}}}] and stopReason "tool_use"
  When _converse_to_openai runs
  Then choices[0].message.tool_calls==[{id:"tu_9",type:"function",function:{name:"get_weather",arguments:'{"city": "Paris"}'}}]
  And message.content is None and finish_reason=="tool_calls"

Scenario: BT6 — mixed text + toolUse
  Given a Converse 200 content=[{text:"Let me check"},{toolUse:{...}}]
  When _converse_to_openai runs
  Then message.content=="Let me check" and tool_calls has one entry

Scenario: BT7 — end-to-end complete() tool round-trip
  Given complete() over a MockTransport returning the BT5 toolUse Converse body
  When complete() runs
  Then it returns (200, body) with body.choices[0].message.tool_calls populated and finish_reason "tool_calls"

Scenario: BT8 — plain-text path unchanged (no regression)
  Given a request with NO tools
  When _openai_to_converse_request / _converse_to_openai run
  Then the output is byte-identical to task 2 (no toolConfig key; message.content is the text string, no tool_calls key)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# MODIFY apps/gateway/src/gateway/proxy/infrastructure/bedrock_upstream.py
# New module helpers (pure):
def _tools_to_converse(tools: list[dict]) -> list[dict]
    # [{type:function,function:{name,description?,parameters?}}] ->
    # [{"toolSpec":{"name":fn["name"], ("description":fn["description"])?, "inputSchema":{"json": fn.get("parameters",{})}}}]
def _tool_choice_to_converse(choice) -> dict | None
    # "auto"->{"auto":{}}; "required"->{"any":{}}; {type:function,function:{name}}->{"tool":{"name":name}};
    # "none"->None (omit); None/unknown->None
def _assistant_tool_calls_to_content(msg: dict) -> list[dict]
    # text(content) -> {"text":content} (only if truthy); each tc -> {"toolUse":{"toolUseId":tc["id"],
    #   "name":tc["function"]["name"], "input": load_tool_arguments(tc["function"]["arguments"])}}

# EXTEND _openai_to_converse_request(payload, *, default_max_tokens) -> (model_id, body):
#   - role=="tool": buffer consecutive into ONE user msg: content += [{"toolResult":{"toolUseId":m["tool_call_id"],
#       "content":[{"text":str(m.get("content",""))}], "status":"success"}}]; flush the buffer to a {role:"user",content:[...]} on role change.
#   - role=="assistant" with m.get("tool_calls"): {role:"assistant", content:_assistant_tool_calls_to_content(m)}
#   - else: unchanged ({role, content:[{text:str(content)}]}); system lift unchanged.
#   - if payload.get("tools"): body["toolConfig"] = {"tools": _tools_to_converse(tools)}; tc=_tool_choice_to_converse(payload.get("tool_choice")); if tc is not None: body["toolConfig"]["toolChoice"]=tc
#   - NO toolConfig key when no tools.

# EXTEND _converse_to_openai(resp_json, *, model_id) -> dict:
#   - walk output.message.content[]: {text} -> accumulate text; {toolUse} -> tool_calls.append({"id":tu["toolUseId"],
#       "type":"function","function":{"name":tu["name"],"arguments":dump_tool_arguments(tu.get("input",{}))}})
#   - message = {"role":"assistant", "content": text if text else (None if tool_calls else "")};
#     if tool_calls: message["tool_calls"]=tool_calls
#   - finish_reason via _map_finish_reason(stopReason) (tool_use->tool_calls already mapped); usage unchanged.
```
Schema: no DB/schema change. Plain-text path byte-identical to task 2 (additive tool branches only).

Least-sure flag surfaced at freeze: [contract] the Converse tool key names (inputSchema.json, toolChoice
object-keys auto/any/tool, toolResult.status) — but CONFIRMED against the botocore bedrock-runtime service model
(Converse input shape + ContentBlock union) so the residual risk is low; cost if a name still drifts = a 400 from
Bedrock, caught by the task-6 live double-pass. (Tool id needs NO synthesis — toolUseId is provider-assigned.)

Status: FROZEN @ v1 — approved by ADD auto-gate (autonomy:auto; non-security; tool shapes oracle-confirmed) · 2026-06-15
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥90% on the extended mapping helpers.
Plan (one test per scenario, asserting behavior not internals); reuse tests/tool_translation _TOOLS/_TOOL_MESSAGES style:
<test_plan>
  - test_tools_to_toolconfig (BT1): _openai_to_converse_request with tools → toolConfig.tools toolSpec/inputSchema.json shape.
  - test_tool_choice_variants (BT2): auto/required/named/none → {auto:{}}/{any:{}}/{tool:{name}}/omitted; toolConfig omitted when no tools.
  - test_assistant_tool_calls_to_touse (BT3): assistant tool_calls → toolUse content blocks; input is a dict (load_tool_arguments).
  - test_tool_results_collapse (BT4): two consecutive role:tool → ONE user message with two toolResult blocks (toolUseId preserved).
  - test_touse_response_to_tool_calls (BT5): _converse_to_openai toolUse → tool_calls [{id:toolUseId,...,arguments:JSON str}]; content None; finish_reason tool_calls.
  - test_mixed_text_and_touse (BT6): text+toolUse → content==text + one tool_call.
  - test_complete_tool_roundtrip (BT7): complete() over MockTransport toolUse body → (200, tool_calls populated).
  - test_plain_text_unchanged (BT8): no-tools request/response byte-identical to task 2 (no toolConfig, no tool_calls key, content is text).
</test_plan>

Tests live in: `apps/gateway/tests/bedrock_tool_use/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/bedrock_upstream.py`
Strategy (ordered batches): 1. add _tools_to_converse + _tool_choice_to_converse + _assistant_tool_calls_to_content + the tool_translation import. 2. extend _openai_to_converse_request (tool branches + toolConfig). 3. extend _converse_to_openai (toolUse → tool_calls). Keep the plain-text path byte-identical.
Safety rule (feature-specific): additive only — the no-tools path must be byte-identical to task 2; load_tool_arguments/dump_tool_arguments are fail-safe (never raise); no IO/signing/billing change.
Code lives in: bedrock_upstream.py (the single declared §5 file).
Constraints: do NOT change any test or the contract; reuse the v10 tool_translation seam; no new dependency; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 9/9 bedrock_tool_use; 53 with bedrock_provider/bedrock_streaming/anthropic; no-DB floor 135 passed exit 0
- [x] coverage did not decrease — additive tool branches on existing helpers + new pure helpers + tests
- [x] no test or contract was altered during build — the §3 contract was HONORED; only the single declared §5 file was modified; no test touched
- [x] the green was EARNED — adversarial diff review: the tool branches are ADDITIVE; the no-tools path is byte-identical (plain messages hit the unchanged `else` branch; response `content` is the text string / "" when empty with no tool_calls; no tool_calls key added); BT8 + the full task-2 suite (test_response_mapping, complete signing) guard the regression. Shapes confirmed against the botocore service-model oracle (inputSchema.json, toolChoice union, toolResult). load/dump_tool_arguments reused (fail-safe).
- [x] concurrency / timing — pure mapping functions, no IO/signing/billing change; nothing concurrent introduced.
- [x] no exposed secrets, injection openings, or unexpected dependencies — no IO touched; no secret surface change; reuses the v10 tool_translation seam (no new dependency).
- [x] layering & dependencies follow CONVENTIONS.md — mirrors the Anthropic tool-mapping pattern; consumes the frozen domain/tool_translation seam from infrastructure (correct direction).
- [x] a person reviewed and approved — auto-resolved under autonomy:auto on complete evidence (non-security); orchestrator adversarial diff review + botocore service-model oracle.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — _tools_to_converse/_tool_choice_to_converse/_assistant_tool_calls_to_content all consumed by _openai_to_converse_request; _converse_to_openai toolUse branch consumed on the response; load/dump_tool_arguments imported + used. complete()/stream() automatically carry tools (they call these helpers). No main.py change.
- [x] DEAD-CODE (code) — no orphaned symbol; every new helper is referenced.
- [x] SEMANTIC (prose / non-code) — read the full diff vs the Anthropic tool pattern + the botocore-confirmed Converse shapes; verified the toolResult consecutive-collapse (flush before each non-tool msg AND after the loop → no lost/misordered trailing results) and the byte-identical no-tools path.

### GATE RECORD
Outcome: PASS
Reviewed by: ADD auto-gate (orchestrator adversarial diff review + botocore bedrock-runtime service-model oracle) · date: 2026-06-15

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): Bedrock tool-call rate; tool-result correlation correctness; tool_choice
distribution; no-regression on the plain-text path.
Spec delta for the next loop: STREAMING tool-call deltas (toolUse over ConverseStream contentBlockStart/Delta)
remain unimplemented — the carried "parallel-tool streaming" open; a follow-up extends _converse_stream_to_openai_sse
with build_tool_call_delta fragments. bedrock-embeddings (task 5) is next (Titan via InvokeModel).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.

- [SDD] `open` — the botocore SERVICE MODEL (not just SigV4Auth/EventStreamBuffer) is an oracle too:
  `get_service_model("bedrock-runtime").operation_model("Converse")` + the ContentBlock union pinned the exact
  Converse tool key names (toolConfig.tools[].toolSpec.inputSchema.json, toolChoice union auto/any/tool,
  toolResult{toolUseId,content,status}) BEFORE freezing — turning the top §1 risk into a 0.9. Reusable for any
  AWS JSON-shape task. Evidence: BT1-BT5 assert these exact shapes.
- [DDD] `open` — Bedrock toolUse is the Anthropic-shaped tool case (provider toolUseId + dict input), so the v10
  canonical seam + the Anthropic mapping pattern (consecutive role:"tool" collapse-into-one-user-message,
  load/dump_tool_arguments) ported one-to-one; only key names differ. A third provider on the same seam with
  zero new canonical surface. Evidence: bedrock_upstream reuses domain/tool_translation unchanged.
- [TDD] `open` — a dedicated no-regression scenario (BT8: no-tools request has no toolConfig key; no-toolUse
  response has no tool_calls key + content is the text string) is the cheap guard that an ADDITIVE feature
  didn't perturb the existing path — pairs with the unchanged task-2 suite. Evidence: BT8 + 53-test green.
