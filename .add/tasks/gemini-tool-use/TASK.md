# TASK: Google Gemini tool-use translation

slug: gemini-tool-use · created: 2026-06-13 · stage: production
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Google Gemini tool-use translation — extend GeminiCompletionUpstream's v9
chat helpers so OpenAI tools/tool_choice/tool_calls/`role:"tool"` map to/from the
Gemini generateContent tool shape (functionDeclarations, toolConfig, functionCall
parts, functionResponse parts) on request, response, AND streaming. The id-less
Gemini functionCall gets a SYNTHESIZED id (frozen helper); the follow-up tool result
is reverse-correlated to a function NAME via the assistant tool_calls in the same request.

Framings weighed: extend the v9 module-level helpers in gemini_upstream.py (chosen —
mirrors anthropic-tool-use) · separate gemini_tools.py (rejected — splits one provider)
· reverse the synthesized id to recover the name (rejected — the hash is one-way; the
name is read from the assistant tool_calls echoed in the SAME request instead).

Must:
<must>
  - Request: OpenAI `tools` → Gemini `tools:[{functionDeclarations:[{name,description,
    parameters}]}]` (OpenAI function.parameters → Gemini parameters, verbatim).
  - Request: `tool_choice` → `toolConfig.functionCallingConfig.mode`: "auto"→AUTO,
    "required"→ANY, "none"→NONE, {type:"function",function:{name}}→ANY +
    allowedFunctionNames:[name].
  - Request: an assistant message with `tool_calls` → a `model` content whose `parts`
    carry one `{functionCall:{name,args}}` per call (`arguments` JSON string → `args`
    object via load_tool_arguments); any assistant text becomes a leading text part.
  - Request: a `role:"tool"` message → a `user` content with a
    `{functionResponse:{name,response:{result:<content>}}}` part. The function NAME is
    resolved from the tool_call_id via an id→name map built from the assistant
    `tool_calls` in the SAME request (Gemini correlates by name, not id).
  - Response: Gemini `functionCall` parts → OpenAI `message.tool_calls:[{id,type:
    "function",function:{name,arguments:<JSON string>}}]` (id = synthesize_tool_call_id
    (name,index); `args` object → arguments string via dump_tool_arguments); content null
    when only functionCall parts; finish_reason "tool_calls" when any functionCall present.
  - Streaming: a streamed part with `functionCall` → ONE OpenAI `delta.tool_calls`
    fragment carrying id+name+arguments together (Gemini sends the whole call in one
    part — no partial-arg streaming); finish_reason "tool_calls" in the terminal usage
    chunk when a functionCall was seen; the terminal usage chunk before [DONE] preserved.
  - A request WITHOUT tools is byte-identical to v9 (no tools/toolConfig emitted);
    usage (estimated for embeddings — unchanged), error, and x-goog-api-key auth untouched.
</must>
Reject:
<reject>
  - a `role:"tool"` message missing `tool_call_id` -> "tool_call_id_required" (no key to
    resolve the function name for the functionResponse part).
  - non-tool inputs are never rejected — absent tools, the v9 text translation runs unchanged.
</reject>
After:
<after>
  - GeminiCompletionUpstream round-trips a full tool exchange: tools → tool_calls (with a
    synthesized id) → `role:"tool"` follow-up (name-correlated functionResponse) → final
    answer, on stream + non-stream.
  - The no-tools path stays byte-identical to v9; the frozen v9 gemini suite stays green.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The follow-up tool result's function NAME is recoverable from the assistant
    `tool_calls` echoed in the same request (Gemini functionResponse needs the name; the
    client sends back the full history incl. the assistant tool_calls with name+synth-id)
    — lowest confidence because a client that DROPS the assistant tool_calls turn and
    sends only a tool message leaves the id unresolvable; if wrong (history dropped):
    fall back to using the tool_call_id as the functionResponse name (best-effort, may
    mis-correlate) — single well-formed round-trip (the common path) is unaffected.
  - [ ] Gemini functionResponse goes in a `user`-role content (REST `contents` accepts
    user/model; function results ride the user side) — confirm vs the live format; if
    wrong: switch the role literal, helper-local.
  - [x] Gemini sends a whole functionCall in ONE streamed part (no partial-arg deltas) →
    one combined id+name+arguments fragment — handled; differs from Anthropic's
    input_json_delta fragmentation.
  - [x] synthesize_tool_call_id / dump_tool_arguments / load_tool_arguments / build_tool_call_delta
    come from the FROZEN tool-use-contract module — no re-implementation here.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: tools translate to functionDeclarations
  Given an OpenAI request with tools=[{type:function, function:{name:"get_weather", parameters:{...}}}]
  When translated to Gemini
  Then body.tools==[{functionDeclarations:[{name:"get_weather", description, parameters:{...}}]}]

Scenario: tool_choice maps to functionCallingConfig
  Given tool_choice "auto" / "required" / "none" / {function:{name:"x"}}
  When translated
  Then toolConfig.functionCallingConfig.mode is AUTO / ANY / NONE / ANY (the named case also sets allowedFunctionNames:["x"])

Scenario: an assistant tool_calls message becomes functionCall parts
  Given an assistant message with tool_calls=[{id:"call_1", function:{name:"get_weather", arguments:'{"city":"Paris"}'}}]
  When translated to Gemini
  Then a model content carries parts=[{functionCall:{name:"get_weather", args:{"city":"Paris"}}}]

Scenario: a tool result is name-correlated into a functionResponse
  Given an assistant tool_calls turn (id "call_1" name "get_weather") then a role:"tool" message (tool_call_id "call_1", content "sunny")
  When translated to Gemini
  Then a user content carries parts=[{functionResponse:{name:"get_weather", response:{result:"sunny"}}}]
  And the name was resolved from the assistant tool_calls (not the id)

Scenario: Gemini functionCall response becomes OpenAI tool_calls
  Given a Gemini 200 with candidates[0].content.parts=[{functionCall:{name:"get_weather", args:{"city":"Paris"}}}]
  When translated to OpenAI
  Then message.tool_calls=[{id:<synth>, type:"function", function:{name:"get_weather", arguments:'{"city": "Paris"}'}}]
  And message.content is null and finish_reason is "tool_calls"

Scenario: streaming functionCall emits one combined delta.tool_calls fragment
  Given a Gemini stream chunk with a functionCall part (name + args)
  When translated to OpenAI SSE
  Then one chunk carries delta.tool_calls=[{index:0, id:<synth>, type:"function", function:{name, arguments:<json>}}]
  And the terminal usage chunk has finish_reason "tool_calls" before data:[DONE]

Scenario: a request without tools is byte-identical to v9
  Given a chat request with no tools field
  When translated to Gemini (non-stream and stream)
  Then no tools/toolConfig fields are emitted
  And the body equals the v9 translation exactly

Scenario: a tool message without tool_call_id is rejected
  Given a role:"tool" message missing tool_call_id
  When the request is translated
  Then it raises an error coded "tool_call_id_required"
  And no malformed functionResponse part is produced
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Extends `gateway/proxy/infrastructure/gemini_upstream.py` — the v9 chat helpers grow;
the adapter class, x-goog-api-key auth, usage (incl. embeddings estimate), error, and
circuit-breaker paths UNCHANGED. Imports the FROZEN `proxy/domain/tool_translation.py`.

```
_openai_to_gemini_request(payload, *, default_max_tokens) -> dict        # EXTENDED
  + payload["tools"] -> body["tools"] = [{functionDeclarations:[{name,description?,
      parameters} ...]}]
  + payload["tool_choice"] -> body["toolConfig"]["functionCallingConfig"] =
      "auto"->{mode:"AUTO"} | "required"->{mode:"ANY"} | "none"->{mode:"NONE"} |
      {type:"function",function:{name}}->{mode:"ANY", allowedFunctionNames:[name]}
  + assistant msg with tool_calls -> {role:"model", parts:[ {text}? ,
      {functionCall:{name, args: load_tool_arguments(arguments)}} ... ]}
  + role:"tool" msg -> {role:"user", parts:[{functionResponse:{name:<resolved>,
      response:{result: content}}}]}; name resolved via an id->name map built from
      assistant tool_calls; raises ValueError("tool_call_id_required") when no tool_call_id
  + no-tools body byte-identical to v9

_gemini_to_openai(body, *, model) -> dict                                # EXTENDED
  + functionCall parts -> message.tool_calls=[{id: synthesize_tool_call_id(name,i),
      type:"function", function:{name, arguments: dump_tool_arguments(args)}} ...]
  + message.content = concatenated text parts, or null when only functionCall parts
  + finish_reason "tool_calls" when any functionCall part present (override the map)

_translate_gemini_sse(chunks) -> Iterable[bytes]                         # EXTENDED
  + a part with functionCall -> ONE delta.tool_calls=[build_tool_call_delta(i,
      id=synth, name=name, arguments_fragment=dump_tool_arguments(args))] (combined)
  + finish_reason "tool_calls" in the terminal usage chunk when a functionCall was seen
  + terminal usage chunk before data:[DONE] preserved (v9)

Schema: none — no datastore change. Billing unchanged: served id + native usage.
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-13)
Least-sure flag surfaced at freeze: [contract] the follow-up tool result's function
NAME is recovered from the assistant `tool_calls` echoed in the SAME request (Gemini's
functionResponse correlates by name; OpenAI tool messages carry only tool_call_id). If a
client drops the assistant tool_calls turn, the id→name map is empty → fall back to using
the tool_call_id AS the name (best-effort, may mis-correlate); the well-formed single
round-trip (the common path) is unaffected. Secondary [contract]: same-name PARALLEL
calls remain name-ambiguous on the return leg (the tool-use-contract milestone-level flag).
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
  - test_tools_to_function_declarations: req w/ tools / assert body.tools==[{functionDeclarations:[{name,description,parameters}]}]
  - test_tool_choice_mapping[auto|required|none|named]: parametrized / assert functionCallingConfig.mode AUTO|ANY|NONE|ANY(+allowedFunctionNames)
  - test_assistant_tool_calls_to_function_call: assistant tool_calls / assert model content parts=[{functionCall:{name,args}}] (arguments str->args obj)
  - test_tool_result_name_correlated: assistant(call_1=get_weather)+tool(call_1) / assert user content parts=[{functionResponse:{name:get_weather, response:{result:...}}}]
  - test_function_call_response_to_openai: gemini functionCall 200 / assert tool_calls[id synth, name, arguments] + content None + finish_reason tool_calls
  - test_streaming_function_call_fragment: gemini stream chunk w/ functionCall / assert one delta.tool_calls combined frag + terminal usage finish_reason tool_calls
  - test_no_tools_request_byte_identical_v9: no tools / assert body==v9 _openai_to_gemini_request output (no tools/toolConfig)
  - test_tool_msg_missing_id_rejected: role:tool w/o tool_call_id / assert raises ValueError("tool_call_id_required")
</test_plan>

Tests live in: `apps/gateway/tests/gemini_tool_use/test_gemini_tool_use.py` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): the no-tools path stays byte-identical to v9 (the v9
gemini suite is the regression guard); the synthesized tool-call id derives from
name+index only (frozen helper, secret-free); the x-goog-api-key auth + the ?key=-free
URL invariant are untouched (the key never enters a URL/log).
Code lives in: `apps/gateway/src/gateway/proxy/infrastructure/gemini_upstream.py`
(the 3 chat helpers extended; adapter classes unchanged); imports the FROZEN
`proxy/domain/tool_translation.py`.
Constraints: do NOT change any test or the contract; allow-list packages only (no new
deps — stdlib + frozen helpers); ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full suite 668 passed (657 prior + 11 new); new + v9 gemini suites: 30 passed
- [x] coverage did not decrease — 82.96% (≥80 gate), up from 82.80%
- [x] no test or contract was altered during build — only the 3 v9 chat helpers extended + new suite added; the frozen v9 gemini_provider suite (19 tests) stays green = no-tools path byte-identical; new test file added to ruff-format exclude
- [x] concurrency / timing of the risky operation is safe — translation helpers are PURE (no IO/await/shared state); the adapter's httpx/circuit-breaker/stream paths are unchanged from v9
- [x] no exposed secrets, injection openings, or unexpected dependencies — x-goog-api-key + the ?key=-free URL invariant untouched; synthesized ids are name+index-derived (secret-free); no new dep; ruff S-rules + allowlist green
- [x] layering & dependencies follow CONVENTIONS.md — infra adapter imports the proxy/domain frozen helpers (infra→domain); no upward dependency
- [x] a person reviewed and approved the change — delegated auto mode (2026-06-13); manual diff review of all 3 extended helpers + the id→name correlation pass; no security finding, no concurrency/architecture residue → auto-PASS per run.md

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — the 3 extended helpers are called by GeminiCompletionUpstream (complete/stream) as in v9; new imports (build_tool_call_delta/dump_tool_arguments/load_tool_arguments/synthesize_tool_call_id) each referenced (streaming frag / response args / request args / response+stream id). New helpers _tools_to_gemini/_tool_choice_to_gemini/_assistant_tool_calls_to_parts called inside _openai_to_gemini_request and unit-tested.
- [x] DEAD-CODE (code) — no orphaned symbol: ruff F + pyright strict green; every new helper exercised by the red→green suite.
- [x] SEMANTIC (prose / non-code) — N/A (code task); the §3 contract was read in full against the Gemini generateContent tool wire format before freeze.

### GATE RECORD
Outcome: PASS
Auto-resolved: delegated auto mode (2026-06-13) — complete evidence (668 passed,
82.96% cov, 0 pyright errors, ruff+allowlist green, frozen v9 gemini suite intact);
pure translation extension, no security finding, no concurrency/architecture residue.
Reviewed by: delegated auto mode (Tin Dang) · date: 2026-06-13

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of tool_call_id_required raises; the
fall-back-name branch firing (id→name map empty → client dropped the assistant turn) —
a signal the common-path assumption is being violated in the wild.
Spec delta for the next loop: the live-verify (task 4) must drive a full Gemini
round-trip (tools → functionCall → functionResponse follow-up → final answer) to confirm
the name-correlation against a real generateContent exchange.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [DDD · open] The tool-call-id SYNTHESIS concept (frozen v10) earns its keep on Gemini: the response synthesizes ids for id-less functionCalls, and the follow-up correlates the result back BY NAME via an id→name map rebuilt from the assistant tool_calls echoed in the same request — id is for the OpenAI client, name is for Gemini. Evidence: _gemini_to_openai synth + the id_to_name pre-pass; 11/11 green.
- [SDD · open] Each provider's tool translation is ASYMMETRIC in streaming granularity but UNIFORM at the OpenAI seam: Gemini emits one combined id+name+arguments fragment (whole functionCall in one part) while Anthropic streams id+name then incremental input_json_delta — both produced via the SAME build_tool_call_delta helper. The frozen streaming-fragment shape absorbed both without change. Evidence: gemini one-shot vs anthropic multi-fragment, identical helper.
- [TDD · open] The name-correlation needed a TWO-MESSAGE fixture (assistant tool_calls turn + the tool message) to test honestly — a tool message alone cannot exercise the id→name resolution. Single-message unit fixtures would have hidden the core risk. Evidence: test_tool_result_name_correlated uses the full 3-message history.
- [ADD · open] Provider tool-translation is now a REPEATABLE 4-step shape (request tools/tool_choice + message restructure · response native-call→tool_calls · streaming native-event→delta fragment · no-tools byte-identical pin), proven twice (anthropic + gemini). The next provider (Bedrock/Azure) follows the same template against the frozen contract. Evidence: anthropic-tool-use + gemini-tool-use landed identically-shaped.
