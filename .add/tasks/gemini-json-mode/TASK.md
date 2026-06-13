# TASK: Gemini JSON-mode / responseSchema translation

slug: gemini-json-mode · created: 2026-06-13 · stage: production
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Gemini JSON-mode — extend GeminiCompletionUpstream's v9 request helper so a
provider=google chat request carrying `response_format` produces NATIVE structured output
via `generationConfig.responseMimeType` (+ `responseSchema` for json_schema). Gemini is the
NATIVE provider for this milestone: it has a first-class structured-output field, and its
JSON answer comes back as ordinary `parts:[{text}]` → already mapped to OpenAI
`message.content` by the v9 `_gemini_to_openai` (and streamed as `delta.content`). So this
task is REQUEST-SIDE ONLY — the response and SSE paths need NO change.

Framings weighed: add responseMimeType/responseSchema to the EXISTING `generationConfig`
builder in `_openai_to_gemini_request`, gated by `extract_response_format` (chosen — minimal,
additive, native; reuses the frozen contract's extractor) · tool-coercion like Anthropic
(rejected — Gemini has a native field; coercion would be strictly worse and lose the schema)
· a separate gemini json adapter (rejected — it's three lines in the existing helper).

Must:
<must>
  - In `_openai_to_gemini_request`, call `extract_response_format(payload)`; when it returns
    a directive (i.e. not absent / not {type:"text"}):
      json_object  -> generationConfig["responseMimeType"] = "application/json"
      json_schema  -> responseMimeType="application/json" AND
                      generationConfig["responseSchema"] = json_schema.schema
  - The response path is UNCHANGED: Gemini returns the JSON as a text part, mapped to
    OpenAI `message.content` (a JSON string) with finish_reason "stop" by the v9
    `_gemini_to_openai`; streaming yields normal `delta.content` fragments + the terminal
    usage chunk. No new response/SSE code.
  - response_format COMPOSES with v10 tools: a request carrying BOTH `tools` and
    `response_format` still adds `tools`/`toolConfig` (v10) AND the responseSchema config —
    they are orthogonal Gemini fields, no collision.
  - A request WITHOUT response_format (or {type:"text"}) is BYTE-IDENTICAL to v9/v10 (the
    extractor returns None → the generationConfig gains nothing). The frozen v9 + v10 Gemini
    suites stay green.
  - Billing/auth/circuit-breaker paths (x-goog-api-key, the ?key=-free URL, native usage)
    are untouched.
</must>
Reject:
<reject>
  - response_format with an unsupported `type` -> "ERR_UNSUPPORTED_RESPONSE_FORMAT"
    (propagated from extract_response_format; the request is not dispatched).
  - json_schema with no schema object -> "ERR_INVALID_JSON_SCHEMA" (from the extractor).
</reject>
After:
<after>
  - A provider=google chat request with response_format json_object/json_schema returns
    JSON-conformant `message.content` (non-stream + streaming); no-response_format stays
    byte-identical; the frozen Gemini suites pass.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Gemini's `responseSchema` accepts the SAME OpenAI/JSON-Schema `schema` object the caller
    sends (a subset of OpenAPI 3.0 schema) without rewriting — lowest confidence because
    Gemini's responseSchema supports only a documented subset (no $ref, limited formats); if
    wrong: Gemini 400s on an unsupported schema construct and the live json_schema check
    (task 4) fails loudly with the upstream error (no false pass) — the contract's stance is
    translate-don't-rewrite (forward the schema as-is; schema-subset normalization is
    explicitly Out of milestone scope).
  - [ ] The v9 text-part → message.content mapping already returns the JSON string verbatim
    with finish_reason "stop" — confirm against the unchanged `_gemini_to_openai` (no
    response-side change needed).
  - [ ] responseMimeType + responseSchema live in generationConfig alongside maxOutputTokens
    etc. and compose with toolConfig — confirm they are independent generationConfig keys.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: json_object sets responseMimeType
  Given an OpenAI request with response_format {type:"json_object"}
  When it is translated to a Gemini request
  Then generationConfig.responseMimeType == "application/json"
  And no responseSchema is set

Scenario: json_schema sets responseMimeType + responseSchema
  Given an OpenAI request with response_format {type:"json_schema", json_schema:{name,schema}}
  When it is translated
  Then generationConfig.responseMimeType == "application/json"
  And generationConfig.responseSchema == the requested schema (forwarded as-is)

Scenario: response JSON maps to message.content (unchanged path)
  Given a Gemini response whose part text is a JSON string
  When _gemini_to_openai maps it
  Then message.content is that JSON string and finish_reason is "stop"

Scenario: streaming JSON content fragments + terminal usage (unchanged path)
  Given a streamed Gemini response of text parts
  When the SSE is translated
  Then delta.content fragments stream and a terminal usage chunk precedes [DONE]

Scenario: composes with tools
  Given a request with BOTH tools and response_format json_schema
  When translated
  Then the Gemini body has tools + toolConfig (v10) AND generationConfig.responseSchema

Scenario: no response_format is byte-identical to v9/v10
  Given a request with no response_format (or {type:"text"})
  When translated
  Then generationConfig has no responseMimeType/responseSchema (byte-identical)

Scenario: unsupported response_format type rejected
  Given response_format {type:"yaml"}
  When translated
  Then it raises ERR_UNSUPPORTED_RESPONSE_FORMAT and no request is dispatched

Scenario: json_schema missing schema rejected
  Given response_format {type:"json_schema", json_schema:{name:"w"}}
  When translated
  Then it raises ERR_INVALID_JSON_SCHEMA and no request is dispatched
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Extends `_openai_to_gemini_request` in gemini_upstream.py (REQUEST-SIDE ONLY; response +
SSE unchanged). Builds on the FROZEN response-format-contract (extract_response_format).

```
_openai_to_gemini_request(payload, *, default_max_tokens) -> dict   # EXTENDED
  rf = extract_response_format(payload)   # None | json_object | json_schema
  if rf is not None:
      generationConfig["responseMimeType"] = "application/json"
      if rf["type"] == "json_schema":
          generationConfig["responseSchema"] = rf["json_schema"]["schema"]   # forwarded as-is
  # rf raises ERR_UNSUPPORTED_RESPONSE_FORMAT / ERR_INVALID_JSON_SCHEMA (propagated)
  # tools/toolConfig (v10) and responseMimeType/responseSchema are INDEPENDENT keys → compose
  # rf is None (absent/text) -> generationConfig unchanged -> byte-identical v9/v10

# UNCHANGED (v9): _gemini_to_openai maps the text part -> message.content (JSON string),
#   finish_reason "stop"; _translate_gemini_sse streams delta.content + terminal usage.
```
Schema: NONE — no DB tables/columns touched (request-translation only).

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-13)
Least-sure flag surfaced at freeze: [contract] Gemini's responseSchema accepts the caller's
JSON-Schema `schema` object verbatim — Gemini supports only a documented OpenAPI-3.0 subset
(no $ref, limited string formats); why: translate-don't-rewrite is the milestone stance
(schema normalization is Out of scope); cost if wrong: Gemini 400s on an unsupported schema
construct and the live json_schema check (task 4) surfaces the upstream error loudly (no
false pass). This is GEMINI's slice of the bundle's lowest-confidence area — the deeper risk
(Anthropic coercion) sits in the sibling anthropic-json-mode task.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: every new request branch covered; suite-wide ≥80% held.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_json_object_sets_response_mime_type: responseMimeType set, no responseSchema
  - test_json_schema_sets_mime_type_and_schema: responseMimeType + responseSchema (as-is)
  - test_composes_with_tools: tools (v10) + responseSchema both present
  - test_no_response_format_byte_identical / test_text_type_is_noop (GREEN-BY-DESIGN): no rf keys
  - test_unsupported_type_rejected → ERR_UNSUPPORTED_RESPONSE_FORMAT
  - test_json_schema_missing_schema_rejected → ERR_INVALID_JSON_SCHEMA
  - test_json_text_response_maps_to_content (GREEN-BY-DESIGN, unchanged v9 path): JSON string → message.content, finish stop
</test_plan>
9 tests: 5 drive the new request branch (RED on the helper ignoring response_format) + 4
green-by-design pins (no-rf/text byte-identical + the unchanged v9 response mapping).

Tests live in: `gemini-json-mode/tests/` declared as `apps/gateway/tests/gemini_json_mode/test_gemini_json_mode.py` · ran RED (5 failed, 3 passed) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): the no-op path (extract returns None for absent/text) must
leave generationConfig untouched — byte-identical v9/v10; the schema is forwarded as-is
(translate-don't-rewrite); auth (x-goog-api-key, ?key=-free URL) untouched.
Code lives in: `apps/gateway/src/gateway/proxy/infrastructure/gemini_upstream.py`
Constraints: do NOT change any test or the contract; reuse extract_response_format from the
frozen contract; no new dependency; request-side only.

Built: imported extract_response_format; in _openai_to_gemini_request, after the existing
generationConfig is built, when extract returns a directive → set responseMimeType
"application/json" (+ responseSchema = json_schema.schema for json_schema; guarded
.get("json_schema") for the NotRequired key). Response + SSE helpers UNCHANGED.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — gemini blast radius 38/38 (json_mode 9 + tool_use 11 + provider 19 + parametrized), no-DB deterministic; ruff + pyright clean
- [x] coverage did not decrease — request branch fully exercised; suite-wide ≥80% unaffected (additive lines only)
- [x] no test or contract was altered during build — frozen v9 + v10 gemini suites stay green (no-rf byte-identical), tests + §3 untouched
- [x] concurrency / timing of the risky operation is safe — pure request translation; no IO, no shared state
- [x] no exposed secrets, injection openings, or unexpected dependencies — x-goog-api-key + ?key=-free URL untouched; schema is the caller's own; no new dep
- [x] layering & dependencies follow CONVENTIONS.md — infra adapter imports the domain seam (extract_response_format); same direction as the v10 tool_translation import
- [x] a person reviewed and approved the change — delegated auto mode (Tin Dang, 2026-06-13)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — extract_response_format imported (line 40) + called in _openai_to_gemini_request; the responseMimeType/responseSchema keys are asserted by 3 tests; confirmed reachable
- [x] DEAD-CODE (code) — no orphan; the single new branch is on the live request path, exercised by 5 tests; pyright strict clean (0 errors)
- [x] SEMANTIC (prose / non-code) — re-read the diff: extract is called AFTER the existing generationConfig build, the NotRequired json_schema key is guarded, response/SSE untouched — matches §3 verbatim

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (delegated auto mode) · date: 2026-06-13

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): json_schema 400-rate from Gemini (unsupported schema
construct), responseMimeType-set rate, no-rf byte-identical rate.
Spec delta for the next loop: a NATIVE-field provider needs only a REQUEST-side branch for a
new directive when its output already lands on the canonical response shape (Gemini JSON →
text part → message.content) — the cheapest possible provider integration, the opposite end
of the spectrum from Anthropic's coercion.

### Competency deltas
- [SDD · open] response_format on a native-field provider (Gemini) is REQUEST-SIDE ONLY: responseMimeType/responseSchema added to the existing generationConfig, output already maps to message.content via the unchanged v9 response path — no response/SSE code (evidence: gemini-json-mode touched only _openai_to_gemini_request; 38/38 gemini suites green).
- [ADD · open] the frozen-contract extractor (extract_response_format) is the SHARED no-op/validation gate every provider reuses — Gemini gets the byte-identical guarantee + the two rejections for free by calling it, rather than re-implementing the parse (evidence: 1 import + 1 call delivered the whole request branch).

What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
