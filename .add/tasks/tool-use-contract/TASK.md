# TASK: Tool-translation contract (FREEZE FIRST)

slug: tool-use-contract · created: 2026-06-13 · stage: production
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Tool-translation contract — the canonical OpenAI⇄native tool mapping
(tools · tool_choice · tool_calls · `role:"tool"` messages · streaming
`delta.tool_calls`) that EVERY provider's chat translator honors, plus the shared
id-synthesis/reverse-correlation helper and the byte-identical-when-no-tools and
OpenRouter/OpenAI passthrough pins. This task FREEZES the seam; the two provider
tasks (anthropic-tool-use, gemini-tool-use) build their native translation against it.

Framings weighed:
  - shared-types + helper module, per-provider translation owns the mapping (chosen) —
    freeze the canonical TypedDict shapes + a deterministic `synthesize_tool_call_id`
    + a `build_tool_call_delta` streaming-chunk builder + the request-passthrough
    characterization; each provider implements OpenAI⇄native itself (mirrors v9, where
    each provider owned its own translation helpers). Minimal shared surface, no
    god-translator.
  - one central ToolTranslator class all providers call · rejected — a single class
    cannot express three divergent native shapes without a provider switch inside it,
    re-centralizing what v9 deliberately distributed per adapter.
  - parse the chat body into a Pydantic ChatRequest with typed tools · rejected — the
    chat router forwards a RAW `dict` (router.py:42) and v9 byte-identical passthrough
    depends on it; a model would strip/reshape unknown fields and break the OpenRouter
    passthrough invariant.

Must:
<must>
  - Define the CANONICAL (OpenAI) tool shapes as the frozen vocabulary every provider
    maps to/from: request `tools:[{type:"function",function:{name,description,parameters}}]`
    + `tool_choice` ∈ {"auto"|"none"|"required"|{type:"function",function:{name}}};
    assistant response `message.tool_calls:[{id,type:"function",function:{name,
    arguments:<JSON string>}}]` with `finish_reason:"tool_calls"`; follow-up
    `{role:"tool",tool_call_id,content}` messages.
  - Provide a deterministic `synthesize_tool_call_id(name, index) -> str` (gateway-owned,
    stable, prefix `call_`, no secret) for providers whose native tool call has no id
    (Gemini). Same (name,index) → same id within a response; the value never encodes a
    key/secret.
  - Provide a streaming helper `build_tool_call_delta(index, *, id=None, name=None,
    arguments_fragment=None) -> dict` that emits the OpenAI `choices[0].delta.tool_calls`
    fragment shape (`{index, id?, type:"function", function:{name?, arguments?}}`) so all
    providers stream tool calls identically; the FIRST fragment for a call carries
    id+name, later fragments carry only the `arguments` string fragment.
  - `function.arguments` crosses the wire as a JSON STRING; the contract pins that
    translators `json.dumps` a native object → string outbound and `json.loads` a string
    → native object inbound; the helpers tolerate already-string / already-object inputs.
  - The request body stays a RAW dict end-to-end: `tools`/`tool_choice` (and any prior
    `role:"tool"`/`tool_calls` messages) flow through the chat use-case UNSTRIPPED to the
    selected provider adapter — characterized + regression-pinned here.
  - OpenRouter + OpenAI are byte-identical passthrough for tool requests (they already
    speak OpenAI tools — no translation); a request WITHOUT `tools` engages ZERO tool
    plumbing and is byte-identical to v9. Billing still keys on the served model id with
    native usage (no separate tool billing).
</must>
Reject:
<reject>
  - `synthesize_tool_call_id` called with an empty/whitespace function name -> "tool_name_required"
    (a tool call with no name cannot be correlated on the follow-up turn).
  - `build_tool_call_delta` called with a negative `index` -> "tool_call_index_invalid".
  - a malformed `arguments` value that is neither a JSON object nor a JSON-parseable
    string is NOT rejected — fail-safe: the raw value is forwarded verbatim (a tool
    arguments blob must never crash the proxy or drop the request).
</reject>
After:
<after>
  - A shared, frozen tool-translation module exists with the canonical TypedDict shapes,
    `synthesize_tool_call_id`, and `build_tool_call_delta`, imported by both provider tasks.
  - The chat path provably forwards `tools`/`tool_choice`/tool-messages unstripped; the
    OpenRouter tool passthrough and the no-tools-byte-identical invariants are
    regression-pinned by green tests.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Gemini's id-less `functionCall` round-trips correctly by matching the follow-up tool
    result to the native call BY FUNCTION NAME — lowest confidence because two PARALLEL
    tool calls to the SAME function name in one turn are name-ambiguous on the return
    leg (OpenAI correlates by unique `tool_call_id`, Gemini has only the name). The
    id-synthesis encodes the index so the gateway can order them, but Gemini's
    `functionResponse` carries only `name` — so a same-name parallel fan-out is
    best-effort by order. If wrong: same-name parallel Gemini tool calls mis-correlate;
    cost = a gemini-tool-use rework + a documented limitation (single-call and
    distinct-name cases — the common path — are unaffected).
  - [ ] The streaming fragment model (first fragment = id+name, rest = arguments string
    chunks, keyed by `index`) is the shape OpenAI clients expect — confirm against the
    documented `delta.tool_calls` schema; if wrong: a streaming-fragment reshape (helper
    only, providers unchanged).
  - [x] `tools`/`tool_choice` already flow unstripped — VERIFIED in code (router.py:42
    raw dict; use_cases.complete forwards body), so this is characterization, not new
    plumbing.
  - [x] No new datastore / migration — tool data is request/response-shaped only, billing
    unchanged (native usage on the served id).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: synthesize a deterministic tool-call id
  Given a provider native tool call for function "get_weather" at index 0
  When synthesize_tool_call_id("get_weather", 0) is called twice
  Then both calls return the identical id
  And the id starts with "call_" and contains no key/secret material

Scenario: distinct index yields a distinct id
  Given two native tool calls for the same function "get_weather" at indices 0 and 1
  When synthesize_tool_call_id is called for each
  Then the two ids differ (the index disambiguates same-name calls)

Scenario: first streaming fragment carries id and name
  Given a new tool call at index 0 with id "call_x" and name "get_weather"
  When build_tool_call_delta(0, id="call_x", name="get_weather") is called
  Then it returns {index:0, id:"call_x", type:"function", function:{name:"get_weather"}}
  And the function object carries no "arguments" key

Scenario: later streaming fragment carries only an arguments fragment
  Given an in-progress tool call at index 0
  When build_tool_call_delta(0, arguments_fragment='{"city":') is called
  Then it returns {index:0, type:"function", function:{arguments:'{"city":'}}
  And it carries no "id" and no "name"

Scenario: arguments cross the boundary as a JSON string
  Given a native arguments object {"city": "Paris"}
  When it is serialized for the OpenAI wire
  Then function.arguments is the JSON STRING '{"city": "Paris"}'
  And parsing that string back yields the original object

Scenario: tools and tool_choice flow through the chat path unstripped
  Given a chat request body carrying tools, tool_choice, and prior role:"tool" messages
  When the chat use-case forwards it to the selected provider adapter
  Then the adapter receives tools, tool_choice, and the tool messages unchanged
  And no field was dropped or reshaped by the gateway

Scenario: OpenRouter tool request is byte-identical passthrough
  Given provider=openrouter and a chat request with tools + tool_choice
  When the request is dispatched
  Then the upstream payload equals the client body (no tool translation applied)
  And the returned tool_calls response is forwarded verbatim

Scenario: a request without tools is byte-identical to v9
  Given a chat request with NO tools field
  When it is dispatched through any provider
  Then zero tool plumbing engages and the behavior matches v9 exactly
  And billing still keys on the served model id with native usage

Scenario: empty function name is rejected
  Given an empty/whitespace function name
  When synthesize_tool_call_id("", 0) is called
  Then it raises an error coded "tool_name_required"
  And no id is produced

Scenario: negative fragment index is rejected
  Given a negative index -1
  When build_tool_call_delta(-1, name="x") is called
  Then it raises an error coded "tool_call_index_invalid"
  And no fragment is produced

Scenario: malformed arguments are forwarded, never rejected
  Given an arguments value that is neither a JSON object nor JSON-parseable string
  When it is serialized for the wire
  Then the raw value is forwarded verbatim (fail-safe)
  And the proxy does not raise and the request is not dropped
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Shared module: `gateway/proxy/domain/tool_translation.py` (pure, no IO) — the frozen
vocabulary + helpers every provider chat translator imports. No HTTP endpoint of its
own; it shapes the EXISTING `/v1/chat/completions` request/response.

```
# ── Canonical OpenAI tool vocabulary (TypedDicts; the frozen shapes) ──
ToolFunction      = TypedDict{ name: str, description: NotRequired[str], parameters: NotRequired[dict] }
Tool              = TypedDict{ type: Literal["function"], function: ToolFunction }
ToolChoiceNamed   = TypedDict{ type: Literal["function"], function: TypedDict{ name: str } }
ToolChoice        = Literal["auto","none","required"] | ToolChoiceNamed
ToolCallFunction  = TypedDict{ name: str, arguments: str }          # arguments is a JSON STRING
ToolCall          = TypedDict{ id: str, type: Literal["function"], function: ToolCallFunction }
#   assistant response message gains: tool_calls: list[ToolCall]; finish_reason == "tool_calls"
#   follow-up turn message: { role: "tool", tool_call_id: str, content: str }

# ── Helper functions (the seam both provider tasks call) ──
synthesize_tool_call_id(name: str, index: int) -> str
   returns: deterministic id == f"call_{stable8(name)}_{index}"   # stable8 = first 8 hex of a
            non-crypto hash of name; gateway-owned, secret-free, stable per (name,index)
   raises:  ValueError("tool_name_required")          when name is empty/whitespace

build_tool_call_delta(index: int, *, id: str|None = None, name: str|None = None,
                      arguments_fragment: str|None = None) -> dict
   returns: { "index": index, "type": "function", "function": {…} }
            - function carries "name" iff name is not None
            - function carries "arguments" iff arguments_fragment is not None
            - top-level carries "id" iff id is not None
            (first fragment of a call → pass id+name; later fragments → pass arguments_fragment only)
   raises:  ValueError("tool_call_index_invalid")      when index < 0

dump_tool_arguments(value: object) -> str
   returns: json.dumps(value) when value is a dict/list; value itself when already a str;
            str(value) verbatim otherwise (FAIL-SAFE — never raises, never drops)

load_tool_arguments(value: object) -> object
   returns: json.loads(value) when value is a JSON-parseable str; value unchanged otherwise
            (FAIL-SAFE — a non-JSON str is returned verbatim, never raises)

# ── Request passthrough invariant (characterized + regression-pinned, no new code) ──
#   The chat use-case forwards the RAW request dict; tools / tool_choice / role:"tool" /
#   assistant tool_calls messages reach the selected provider adapter UNSTRIPPED.
#   provider ∈ {openrouter, openai}: byte-identical passthrough (no tool translation).
#   request without "tools": zero tool plumbing; behavior byte-identical to v9.
Schema: none — no datastore change. Billing unchanged: served model id + native usage.
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-13)
Least-sure flag surfaced at freeze: [contract] Gemini's id-less `functionCall` reverse-
correlates the follow-up tool result BY FUNCTION NAME — two PARALLEL same-name calls in
one turn are name-ambiguous on the return leg (OpenAI uses a unique tool_call_id; Gemini
has only the name). `synthesize_tool_call_id` encodes the index for ordering, but Gemini's
`functionResponse` carries only `name`, so same-name parallel fan-out is best-effort by
order. If wrong: same-name parallel Gemini calls mis-correlate → a gemini-tool-use rework
+ a documented limitation; single-call and distinct-name cases (the common path) are
unaffected. Secondary [contract]: the streaming fragment shape (first=id+name, rest=
arguments string keyed by index) is pinned to the documented OpenAI `delta.tool_calls`
schema — a reshape would touch this helper only, not the providers.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (pure module — no IO; high coverage is cheap and load-bearing)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_synthesize_id_deterministic: act synthesize_tool_call_id("get_weather",0) twice / assert equal + startswith "call_" + no secret substring
  - test_synthesize_id_distinct_index: act ("get_weather",0) vs ("get_weather",1) / assert differ
  - test_synthesize_id_empty_name_rejected: act synthesize_tool_call_id("  ",0) / assert raises ValueError("tool_name_required")
  - test_first_fragment_has_id_and_name: act build_tool_call_delta(0,id="call_x",name="get_weather") / assert {index:0,id:"call_x",type:"function",function:{name:"get_weather"}} + no "arguments" key
  - test_later_fragment_args_only: act build_tool_call_delta(0,arguments_fragment='{"city":') / assert function:{arguments:'{"city":'} + no id/name keys
  - test_fragment_negative_index_rejected: act build_tool_call_delta(-1,name="x") / assert raises ValueError("tool_call_index_invalid")
  - test_dump_arguments_object_to_json_string: act dump_tool_arguments({"city":"Paris"}) / assert == '{"city": "Paris"}' and load round-trips to original
  - test_dump_arguments_already_string_passthrough: act dump_tool_arguments('{"x":1}') / assert returns it unchanged
  - test_load_arguments_non_json_string_failsafe: act load_tool_arguments("not json") / assert returns "not json" verbatim, no raise
  - test_dump_arguments_failsafe_never_raises: act dump_tool_arguments(object-with-no-json) / assert returns a str, no raise
  - test_canonical_shapes_typed: assert the TypedDict shapes exist + a sample Tool/ToolCall/ToolChoice validate structurally (mypy/pyright-checked literal)
  - test_request_passthrough_tools_unstripped: build a fake provider adapter spy; act use-case.complete with a body carrying tools+tool_choice+role:"tool" msgs / assert the adapter received them unchanged (characterization)
  - test_openrouter_tool_request_byte_identical: provider=openrouter with tools / assert upstream payload == client body (no translation)
  - test_no_tools_request_byte_identical_v9: a body with no tools / assert dispatch == v9 path (zero tool plumbing engaged)
</test_plan>

Tests live in: `apps/gateway/tests/tool_translation/test_tool_translation.py` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): a synthesized tool-call id derives from the function
NAME + index ONLY (a non-crypto blake2b digest) — never from a key, secret, or tenant
id; arguments (de)serialization is FAIL-SAFE — it never raises and never drops the
request (a malformed args blob is forwarded verbatim).
Code lives in: `apps/gateway/src/gateway/proxy/domain/tool_translation.py` (pure, no IO)
Constraints: do NOT change any test or the contract; allow-list packages only (stdlib
hashlib/json only — no new deps); ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `make ci` green: 644 passed, 19 deselected (628 prior + 16 new)
- [x] coverage did not decrease — 82.59% (≥80 gate); tool_translation.py module-local coverage 100% (pure, every branch hit incl. fail-safe paths)
- [x] no test or contract was altered during build — only NEW files added (the module + the new suite); the frozen v9 suites untouched; test file added to ruff-format exclude per the no-test-edit convention
- [x] concurrency / timing of the risky operation is safe — module is PURE (no IO, no shared mutable state, no await); synthesize/build/dump/load are referentially transparent
- [x] no exposed secrets, injection openings, or unexpected dependencies — synthesized id derives from name+index only (blake2b digest, secret-free, asserted by test_synthesize_id_deterministic); stdlib hashlib/json only — allowlist green
- [x] layering & dependencies follow CONVENTIONS.md — lives in proxy/domain (pure domain), imports only stdlib; no infra/app dependency
- [x] a person reviewed and approved the change — delegated auto mode (2026-06-13); pure-domain helper module + characterization pins, no security finding, no concurrency/architecture residue → auto-PASS per run.md

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced by the red suite (synthesize_tool_call_id, build_tool_call_delta, dump_tool_arguments, load_tool_arguments, Tool/ToolCall/ToolChoice). Production CONSUMERS (anthropic-tool-use, gemini-tool-use) are the NEXT tasks per the freeze-first decomposition — this is the shared seam frozen first, identical to v9 provider-chat-dispatch. ToolMessage/ToolChoiceNamed/ToolFunction are part of the frozen vocabulary exported for the provider tasks.
- [x] DEAD-CODE (code) — no orphaned symbol: ruff F-rules + pyright strict green; every `__all__` export is either tested now or a frozen vocabulary type the provider tasks consume next.
- [x] SEMANTIC (prose / non-code) — N/A (code task); the §3 contract + canonical shapes read in full against the OpenAI/Anthropic/Gemini tool wire formats before freeze.

### GATE RECORD
Outcome: PASS
Auto-resolved: delegated auto mode (2026-06-13) — complete evidence (make ci green,
644 passed, 82.59% cov, 0 pyright errors, allowlist green); pure-domain seam, no
security finding, no concurrency/architecture residue. Not a skip — an explicit PASS.
Reviewed by: delegated auto mode (Tin Dang) · date: 2026-06-13

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of tool_name_required / tool_call_index_invalid
raises (should be ~0 in prod — they signal a malformed provider translation upstream);
the no-tools-byte-identical pin doubles as a regression monitor for any future chat-path change.
Spec delta for the next loop: the provider tasks will reveal whether the streaming
fragment shape (first=id+name, rest=arguments) survives real provider event ordering —
Gemini emits a whole functionCall in one part (one fragment), Anthropic streams
input_json_delta (many arguments fragments); the helper must serve both without change.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [DDD · open] Tool/function-call enters the domain as a canonical (OpenAI) vocabulary every provider maps to/from, with tool-call-id SYNTHESIS as a first-class concept for id-less native providers (Gemini) — the id is gateway-owned, name+index-derived, secret-free. Evidence: tool_translation.py frozen shapes + synthesize_tool_call_id; 16/16 green.
- [SDD · open] The freeze-first SHARED-SEAM pattern (v9 dispatch wrapper) repeats for a richer request/response SHAPE: freeze the canonical types + pure helpers + the passthrough/byte-identical pins FIRST, let each provider build its native translation against them. Evidence: this task delivers zero provider logic, only the seam + characterization.
- [TDD · open] A freeze-first contract task's red suite mixes UNIT tests (the new helpers) with CHARACTERIZATION pins (tools flow unstripped through the v9 dispatch seam; no-tools byte-identical) — the pins guard a behavior that already works so the provider tasks cannot silently break it. Evidence: test_request_passthrough_tools_unstripped + test_no_tools_request_byte_identical_v9 green against unchanged v9 code.
- [ADD · open] Verified the request-side assumption IN CODE before freezing (router.py:42 forwards a raw dict) so the contract pins a real invariant, not a hoped-for one — the chat body must stay a raw dict (a Pydantic model would strip tools and break passthrough). Evidence: §1 framing rejected the ChatRequest-model option on this ground.
