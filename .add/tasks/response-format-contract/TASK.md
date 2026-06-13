# TASK: Response-format translation contract (FREEZE FIRST)

slug: response-format-contract · created: 2026-06-13 · stage: production
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Response-format translation contract — freeze the canonical OpenAI⇄native
mapping for `response_format` (text | json_object | json_schema) across request,
response, and streaming on EVERY provider, INCLUDING the riskiest point: Anthropic has
no native response_format field, so json_schema is satisfied by REUSING the v10 tool
seam (a synthetic forced tool whose input_schema IS the requested schema) and then
UNWRAPPING the returned tool_use block back into `message.content` as a JSON string.
Prove response_format flows through the chat use-case UNSTRIPPED (raw-dict passthrough,
exactly as v10 tools do via router.py); OpenRouter/OpenAI stay byte-identical passthrough;
a no-response_format request stays byte-identical to v10. This is the FREEZE-FIRST seam —
both provider tasks (gemini-json-mode, anthropic-json-mode) build against it.

Framings weighed: a small `response_format_translation.py` domain module of PURE helpers
(canonical TypedDicts + the Anthropic schema→synthetic-tool coercion + the tool_use→
content UNWRAP + a `json_coercion_tool_name` constant), mirroring v10's frozen
tool_translation.py seam (chosen — same proven freeze-first shape, providers extend their
own request/response/SSE helpers against it) · bake the coercion inline into each provider
adapter with no shared module (rejected — duplicates the synthetic-tool + unwrap logic
across providers, no single frozen contract to build against) · a generic "constrained
decoding" abstraction over all providers (rejected — over-engineered; the three providers
have only two real mechanisms — native (Gemini) and tool-coercion (Anthropic) — passthrough
(OpenRouter) needs nothing).

Must:
<must>
  - Define the canonical OpenAI `response_format` vocabulary as frozen TypedDicts:
    `{type:"text"}` (default / no-op), `{type:"json_object"}` (free-form JSON),
    `{type:"json_schema", json_schema:{name:str, schema:dict, strict?:bool}}`. The MODEL
    OUTPUT is ALWAYS returned as OpenAI `message.content` (a JSON string) — response_format
    NEVER introduces a new response field.
  - A pure `build_json_coercion_tool(json_schema) -> Tool` that emits ONE Anthropic-style
    synthetic tool whose name is the gateway-owned constant `json_coercion_tool_name`
    (a single stable reserved name, e.g. "json_output") and whose `input_schema` IS the
    requested `json_schema.schema`; PLUS a forced tool_choice selecting that tool. This is
    ADDED to any caller-supplied tools, never replacing them (the COMPOSITION rule).
  - A pure `unwrap_coerced_tool_result(tool_use_input) -> str` that serializes the synthetic
    tool's `input` object back to a JSON STRING for `message.content` (reuse v10
    `dump_tool_arguments` fail-safe semantics — never raise on odd input).
  - A helper to detect/extract response_format from a raw request dict
    (`extract_response_format(payload) -> ResponseFormat | None`) returning None for
    absent or `{type:"text"}` (the no-op fast path that guarantees byte-identical v10).
  - CHARACTERIZATION pin: response_format flows through the chat use-case UNSTRIPPED — the
    raw request dict reaches the dispatch adapter with `response_format` intact (router.py
    forwards a raw dict; a Pydantic model would strip it). Proven against UNCHANGED v9/v10
    dispatch code.
  - CHARACTERIZATION pin: OpenRouter/OpenAI response_format requests are byte-identical
    passthrough; a request WITHOUT response_format (or with `{type:"text"}`) engages ZERO
    json plumbing (byte-identical to v10).
  - The tools+response_format COMPOSITION rule is pinned: a request carrying BOTH a real
    `tools` list AND a json_schema response_format keeps them separate — the coercion tool
    is appended alongside (distinct reserved name), and only the coercion tool's tool_use
    block is unwrapped into content (caller tools, if the model calls them, still surface
    as tool_calls).
</must>
Reject:
<reject>
  - `response_format` present but `type` not in {text, json_object, json_schema} ->
    "ERR_UNSUPPORTED_RESPONSE_FORMAT" (a contracted client error; the gateway does not
    silently drop an unknown directive).
  - `{type:"json_schema"}` with no `json_schema.schema` object -> "ERR_INVALID_JSON_SCHEMA"
    (the schema is required to build the native mapping / coercion tool).
  - the synthetic coercion tool name COLLIDES with a caller-supplied tool of the same
    reserved name -> "ERR_RESERVED_TOOL_NAME" (the reserved name is gateway-owned; a
    caller tool may not claim it).
</reject>
After:
<after>
  - A frozen `response_format_translation.py` domain module exists with the canonical
    types + coercion/unwrap/extract helpers + the reserved tool-name constant; both
    provider tasks build their native mapping against it.
  - OpenRouter passthrough + no-response_format byte-identical are PROVEN here (against
    unchanged dispatch code), so the provider tasks cannot silently break them.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Anthropic json_schema is reliably satisfied by a SINGLE forced synthetic tool whose
    input_schema is the requested schema, and the returned tool_use.input UNWRAPS cleanly
    to the JSON the caller expects — lowest confidence because it depends on Anthropic
    honoring tool_choice-forced structured input for arbitrary schemas; if wrong: the
    coerced content is malformed/empty and the live json_schema check (task 4) fails loudly
    (no false pass) — the fallback is the documented json_object/system-instruction path.
  - [ ] The `{type:"text"}`/absent no-op fast path guarantees byte-identical v10 because
    extract_response_format returns None and ZERO plumbing runs — confirm with a
    byte-identical characterization pin against unchanged dispatch (same lever v10 used).
  - [ ] response_format flows unstripped through router.py's raw-dict passthrough exactly
    as v10 tools do — confirm in code (router forwards `dict`, no model coercion) before
    freezing, so the contract pins a real invariant (the v10 ADD lesson).
  - [ ] A single reserved coercion tool name is sufficient (no per-request unique name
    needed) because only ONE json_schema directive can be active per request — confirm the
    OpenAI surface allows at most one response_format (it does: response_format is a single
    object, not a list).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: json_schema builds a forced coercion tool
  Given a response_format {type:"json_schema", json_schema:{name:"w", schema:{...}}}
  When build_json_coercion_tool is called
  Then it returns a Tool named json_coercion_tool_name whose input_schema IS the schema
  And a forced tool_choice {type:"tool", name:json_coercion_tool_name} is produced

Scenario: coerced tool result unwraps to a JSON content string
  Given a tool_use block input {city:"Paris", temp:18}
  When unwrap_coerced_tool_result is called
  Then it returns the JSON STRING '{"city":"Paris","temp":18}' (fail-safe, never raises)

Scenario: extract_response_format no-op fast path
  Given a request with no response_format (or {type:"text"})
  When extract_response_format(payload) is called
  Then it returns None — ZERO json plumbing runs
  And the request is byte-identical to v10

Scenario: response_format flows through dispatch unstripped
  Given a chat request dict carrying response_format
  When it passes through the unchanged v9/v10 chat use-case + dispatch
  Then the adapter receives the raw dict with response_format intact
  And no production dispatch/router code was changed

Scenario: OpenRouter response_format byte-identical passthrough
  Given a provider=openrouter request with response_format
  When it is dispatched
  Then the body is forwarded unchanged (byte-identical to v10 passthrough)

Scenario: tools + response_format compose without collision
  Given a request with a caller tool "get_weather" AND a json_schema response_format
  When the coercion tool is built and appended
  Then both tools are present (caller tool + json_coercion_tool_name, distinct names)
  And only the coercion tool's result is unwrapped into content

Scenario: unsupported response_format type rejected
  Given a response_format {type:"xml"}
  When it is validated
  Then it raises ERR_UNSUPPORTED_RESPONSE_FORMAT
  And no request is dispatched

Scenario: json_schema missing schema rejected
  Given a response_format {type:"json_schema", json_schema:{name:"w"}} (no schema)
  When it is validated
  Then it raises ERR_INVALID_JSON_SCHEMA
  And no request is dispatched

Scenario: caller tool claiming the reserved coercion name rejected
  Given a caller tool named json_coercion_tool_name AND a json_schema response_format
  When the coercion tool is built
  Then it raises ERR_RESERVED_TOOL_NAME
  And no request is dispatched
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

New module — `apps/gateway/src/gateway/proxy/domain/response_format_translation.py`
(PURE helpers, mirrors v10 tool_translation.py; reuses v10 `dump_tool_arguments`):

```
# Canonical OpenAI response_format vocabulary (frozen TypedDicts)
JsonSchemaSpec   = TypedDict{ name: str, schema: dict, strict: NotRequired[bool] }
ResponseFormat   = TypedDict{ type: Literal["text","json_object","json_schema"],
                              json_schema: NotRequired[JsonSchemaSpec] }

JSON_COERCION_TOOL_NAME: Final[str] = "json_output"   # gateway-owned reserved name

extract_response_format(payload: dict) -> ResponseFormat | None
  # None when absent OR type=="text" (the no-op fast path → byte-identical v10).
  # type ∉ {text,json_object,json_schema}      -> raise ValueError("ERR_UNSUPPORTED_RESPONSE_FORMAT")
  # type=="json_schema" and no json_schema.schema dict -> raise ValueError("ERR_INVALID_JSON_SCHEMA")

build_json_coercion_tool(rf: ResponseFormat, *, existing_tool_names: Iterable[str]=())
    -> tuple[Tool, ToolChoiceNamed]
  # only valid for type=="json_schema". Returns:
  #   Tool         = {name: JSON_COERCION_TOOL_NAME, description: <fixed>, parameters: rf.json_schema.schema}
  #   ToolChoiceNamed = {type:"function", function:{name: JSON_COERCION_TOOL_NAME}}
  # JSON_COERCION_TOOL_NAME in existing_tool_names -> raise ValueError("ERR_RESERVED_TOOL_NAME")
  # (canonical OpenAI shape; each provider maps it to native via its v10 tool helpers)

is_coercion_tool_call(name: str) -> bool        # name == JSON_COERCION_TOOL_NAME

unwrap_coerced_tool_input(value: object) -> str # = dump_tool_arguments(value) (fail-safe JSON string)
```

Behavior contract for the PROVIDER tasks (frozen here; built there):
```
Request mapping (per provider, when response_format present & not text):
  openrouter/openai : passthrough — response_format forwarded VERBATIM (byte-identical)
  google (gemini)   : json_object  -> generationConfig.responseMimeType="application/json"
                      json_schema   -> +generationConfig.responseSchema = json_schema.schema
  anthropic         : json_object  -> system-instruction strategy (no native field)
                      json_schema   -> build_json_coercion_tool → APPEND tool + force tool_choice
                                       (alongside any caller tools; reserved-name collision rejected)
Response mapping:
  the model's JSON is returned as OpenAI message.content (a JSON STRING), finish_reason "stop".
  anthropic json_schema coercion: the json_output tool_use block is UNWRAPPED via
  unwrap_coerced_tool_input into message.content; it MUST NOT surface as a tool_calls entry.
  caller tools (if any) still surface as tool_calls normally (composition).
Streaming:
  JSON content streams as normal delta.content fragments + the terminal usage chunk.
  anthropic coercion: the json_output tool_use input_json_delta fragments are unwrapped
  into delta.content (not delta.tool_calls).
Invariants: no response_format (or type=text) -> ZERO plumbing, byte-identical v10;
  billing keys on the served model id with native usage (unchanged).
```
Schema: NONE — no DB tables/columns touched (pure request/response translation; the chat
body is the raw dict from router.py:42, confirmed forwarded unstripped).

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-13)
Least-sure flag surfaced at freeze: [scenario] Anthropic json_schema satisfied by a SINGLE
forced synthetic tool whose input_schema is the requested schema, with the returned
tool_use.input UNWRAPPING cleanly to the caller's expected JSON — why: depends on Anthropic
honoring tool_choice-forced structured input for arbitrary schemas; cost if wrong: the
coerced content is malformed/empty and the live json_schema check (task 4) fails loudly (no
false pass); documented fallback is the json_object/system-instruction path. Secondary
[contract] flag: the reserved name "json_output" could in principle collide with a real
caller tool — pinned as ERR_RESERVED_TOOL_NAME rather than silently renamed, so the
collision is observable, not hidden.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of the new pure module (it is tiny + pure); suite-wide ≥80% held.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_extract_absent_returns_none / _text_type_is_noop_none: no-op fast path → None (byte-identical v10)
  - test_extract_json_object_returned / _json_schema_returned: directive surfaced with schema intact
  - test_extract_unsupported_type_rejected → ERR_UNSUPPORTED_RESPONSE_FORMAT
  - test_extract_json_schema_missing_schema_rejected → ERR_INVALID_JSON_SCHEMA
  - test_build_coercion_tool_shape: Tool named json_output, parameters IS the schema, forced ToolChoiceNamed
  - test_build_coercion_tool_distinct_from_caller_tools: composes alongside caller tool (no collision)
  - test_build_coercion_tool_reserved_name_collision_rejected → ERR_RESERVED_TOOL_NAME
  - test_unwrap_coerced_input_to_json_string / _failsafe_never_raises: object → JSON string, fail-safe
  - test_is_coercion_tool_call: true for json_output, false otherwise
  - test_request_passthrough_response_format_unstripped (GREEN-BY-DESIGN): response_format survives dispatch
  - test_openrouter_response_format_byte_identical (GREEN-BY-DESIGN): openrouter body verbatim
  - test_no_response_format_byte_identical_v10 (GREEN-BY-DESIGN): zero plumbing when absent
</test_plan>
15 tests total: 12 unit (RED on missing module) + 3 characterization pins (GREEN-BY-DESIGN
against unchanged v9/v10 dispatch — they guard a behavior that already works).

Tests live in: `response-format-contract/tests/` declared as `apps/gateway/tests/response_format_translation/test_response_format_translation.py` · ran RED (ModuleNotFoundError) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): the no-op fast path (extract returns None for absent /
type=text) MUST engage ZERO plumbing — byte-identical v10 — and the coercion helpers are
PURE (no IO, no secrets); the reserved tool name is gateway-owned and a caller collision is
REJECTED, never silently renamed.
Code lives in: `apps/gateway/src/gateway/proxy/domain/response_format_translation.py`
Constraints: do NOT change any test or the contract; stdlib + the frozen v10 tool_translation
helpers only; no new dependency.

Built: response_format_translation.py — canonical TypedDicts (JsonSchemaSpec/ResponseFormat),
JSON_COERCION_TOOL_NAME="json_output", extract_response_format (no-op None path + the two
rejections), build_json_coercion_tool (reuses v10 Tool/ToolChoiceNamed/ToolFunction;
reserved-name collision guard), is_coercion_tool_call, unwrap_coerced_tool_input (delegates
to v10 dump_tool_arguments — fail-safe). NO production dispatch/router/adapter change (the
provider tasks consume this seam next).

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass (in scope) — ruff + pyright clean; the 15 new tests 15/15 green; the FULL blast radius (response_format_translation + tool_translation + anthropic/gemini_tool_use + provider_chat_dispatch + anthropic/gemini_provider) = 100/100 green, deterministic, no-DB, 1.2s. NOTE: the full `-m 'not e2e'` run shows 16–44 failures whose COUNT VARIES run-to-run — all in DB-touching suites (keys/catalog/guardrails/health_alerting/images/key_governance) failing on `ForeignKeyViolationError: api_keys_tenant_id_fkey`. Proven PRE-EXISTING shared-Postgres test-pollution, NOT this change: `tests/keys/test_api_keys.py` passes 20/20 IN ISOLATION; zero failures touch the translation/dispatch paths; the new module is imported by ZERO production code. (Documented flake — see ADD delta below.)
- [x] coverage did not decrease — `make ci` (one run) passed --cov-fail-under=80 at 82.90%; the new pure module is fully exercised by its 15 tests
- [x] no test or contract was altered during build — only one line of the contract's prose docstring shortened for E501; the frozen §3 shape + tests untouched
- [x] concurrency / timing of the risky operation is safe — module is PURE (no IO, no shared state, no async); nothing to race
- [x] no exposed secrets, injection openings, or unexpected dependencies — no secrets; coercion tool name is a fixed constant + caller's own schema; stdlib + frozen v10 helpers only
- [x] layering & dependencies follow CONVENTIONS.md — domain module imports only from domain/tool_translation (peer), zero infra/IO; mirrors the v10 tool_translation seam exactly
- [x] a person reviewed and approved the change — delegated auto mode (Tin Dang, 2026-06-13); freeze-first contract, no production behavior changed yet

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — all 7 exports referenced by the frozen test suite NOW; production consumers land in tasks 2 (gemini) + 3 (anthropic) per the freeze-first pattern (same as v10 tool_translation, whose helpers were consumed by the later provider tasks). The 3 characterization pins reference the EXISTING ProviderAwareCompletionUpstream (unchanged).
- [x] DEAD-CODE (code) — no orphan: pyright strict passes (no unused-symbol error); a freeze-first seam being ahead of its consumers is intentional, not dead code (the test suite is the live consumer + the v11 provider tasks are the production consumers)
- [x] SEMANTIC (prose / non-code) — read response_format_translation.py + the §3 contract in full: the no-op None fast path, the two extract rejections, the reserved-name collision guard, and the fail-safe unwrap (delegates to v10 dump_tool_arguments) all match the frozen contract verbatim

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (delegated auto mode) · date: 2026-06-13

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): per-rejection rate (ERR_UNSUPPORTED_RESPONSE_FORMAT /
ERR_INVALID_JSON_SCHEMA / ERR_RESERVED_TOOL_NAME), no-response_format byte-identical rate,
json_schema-coercion success rate (does the coerced content parse).
Spec delta for the next loop: response_format is a SECOND canonical directive (after v10
tools) that some providers satisfy NATIVELY (Gemini) and others by REUSING an existing seam
(Anthropic borrows the v10 tool path) — the contract's job is to name the one canonical shape
+ the gateway-owned coercion primitive, then let each provider pick native-vs-borrow.

### Competency deltas
- [DDD · open] response_format enters the domain as a canonical OpenAI directive with TWO native mechanisms across providers — native structured-output (Gemini responseMimeType/responseSchema) vs tool-COERCION (Anthropic, no native field) — plus a gateway-owned reserved coercion tool name; the model output always returns as message.content JSON string, never a new field (evidence: response_format_translation.py frozen; 15/15 green).
- [SDD · open] the freeze-first SHARED-SEAM pattern (v9 dispatch, v10 tools) repeats a THIRD time for response_format, and this time the seam COMPOSES with a prior seam — the Anthropic json_schema path reuses v10's Tool/ToolChoiceNamed + tool helpers rather than inventing a parallel mechanism (evidence: build_json_coercion_tool returns canonical v10 types).
- [ADD · open] a contract task can prove its passthrough/byte-identical pins GREEN-BY-DESIGN against UNCHANGED dispatch code with a spy adapter — 3 of 15 tests guard response_format-unstripped + openrouter-verbatim + no-rf-byte-identical and pass before any provider build (evidence: reused the v10 _SpyAdapter/_ScriptedResolver pattern verbatim).
- [ADD · open] verified the raw-dict passthrough invariant IN CODE before freezing (router.py:42 reads `body: dict[str, Any]` and forwards it) so the contract pins a real invariant — the recurring v10 lesson applied again (evidence: §1 assumption confirmed pre-freeze).
- [TDD · open] the full `-m 'not e2e'` suite is NON-DETERMINISTIC against the shared dev Postgres (5433): 16/34/44 failures across runs, all `ForeignKeyViolationError: api_keys_tenant_id_fkey` in DB-touching suites; each failing suite passes IN ISOLATION (tests/keys 20/20). The trustworthy per-change gate is the no-DB blast-radius run (translation+dispatch suites, 100/100, 1.2s) — the recurring v8 CI-flake; the foundation needs per-test DB isolation (txn-rollback fixture / template DB) so the full suite is deterministic, OR a documented `make test-fast` that excludes DB suites for per-change gating (evidence: this build's 16/34/44 variance with a zero-blast-radius pure module).
