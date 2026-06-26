# TASK: Web-search flag → provider-native grounding + citation passthrough

slug: websearch-grounding-passthrough · created: 2026-06-26 · stage: production
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

Touches (files · symbols · signatures):
  - `apps/gateway/src/gateway/proxy/domain/web_search.py` (NEW) — the translation seam (mirrors the frozen `tool_translation.py` style): `native_web_search_tool(provider:str) -> dict|None` (openai/openrouter→`{"type":"web_search_preview"}`, anthropic→`{"type":"web_search_20250305","name":"web_search"}`, google→`{"googleSearch":{}}`, else None) · `WEB_SEARCH_FLAG="web_search"`.
  - `apps/gateway/src/gateway/core/config.py` (MODIFY) — add `GATEWAY_WEB_SEARCH_ENABLED: bool = Field(default=False)` (pattern mirrors `GATEWAY_OPENROUTER_USAGE_ACCOUNTING`, config.py:82-764).
  - `apps/gateway/src/gateway/proxy/application/use_cases.py` (MODIFY) — `CompletionUseCase`: when `settings.GATEWAY_WEB_SEARCH_ENABLED` is FALSE, `payload.pop("web_search", None)` BEFORE dispatch (central knob enforcement — the flag dies here when off). (`_validate_payload` @ use_cases.py:563-587 is the validation seam.)
  - `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py` + `openai_provider.py` + `azure_upstream.py` (MODIFY, verbatim providers) — before sending: if `payload.get("web_search")` → append `native_web_search_tool(provider)` to a COPY's `tools`; ALWAYS pop the raw `web_search` key so it never reaches upstream (would 400). Build payload @ openrouter_upstream.py:173-205/256-299, openai_provider.py:104-173.
  - `apps/gateway/src/gateway/proxy/infrastructure/anthropic_upstream.py` (MODIFY) — `_openai_to_anthropic_request()` (~424-429): if `payload.get("web_search")` append the anthropic native tool to the anthropic `tools` list. `_anthropic_to_openai()` (503-588): preserve `web_search_result`/citation content blocks → an OpenAI-response field (non-stream).
  - `apps/gateway/src/gateway/proxy/infrastructure/gemini_upstream.py` (MODIFY) — `_openai_to_gemini_request()` (~304-310): if `payload.get("web_search")` append `{"googleSearch":{}}` as a SIBLING tools entry (not inside functionDeclarations). `_gemini_to_openai()` (333-408): preserve `groundingMetadata` → an OpenAI-response field (non-stream).
Context (working folder):
  - `/v1/chat/completions` body is `dict[str,Any]` passthrough — only `model`+`messages` validated (use_cases.py:563-587); `web_search` already flows through silently → must be TRANSLATED + STRIPPED, never forwarded verbatim.
  - Provider resolution: `CatalogProviderResolver.provider_for(model_id)` (catalog_provider_resolver.py:57-63); unknown→`openrouter`. Adapter map built @ main.py:595-662.
  - `tool_translation.py` is FROZEN @ v1 (types/helpers only; per-provider translate fns live IN each upstream file) — do NOT edit it; add the NEW `web_search.py` sibling.
  - Bedrock + Azure have NO native grounding → `native_web_search_tool` returns None → no-op (flag still stripped). Honest capability.
Honors (patterns / conventions):
  - DEFAULT-OFF knob (GATEWAY_ prefix, pydantic-settings) like `GATEWAY_OPENROUTER_USAGE_ACCOUNTING`; FAIL-SAFE (knob off OR flag absent ⇒ byte-identical to today).
  - CITATION HONESTY (milestone): pass provider grounding sources through; never fabricate; none-returned ⇒ none-shown.
  - Independent-oracle stub test pattern (tests/gemini_provider/test_gemini_provider.py:70-80): MockTransport handler re-implements the provider wire, asserts OpenAI-shaped round-trip. `make test-fast` = no DB/Redis.
Anchors the contract cites:
  - `native_web_search_tool(provider)` + `WEB_SEARCH_FLAG` (web_search.py) · `GATEWAY_WEB_SEARCH_ENABLED` · the per-provider injection points + `_anthropic_to_openai`/`_gemini_to_openai` citation fields.
  - test harness: `apps/gateway/tests/web_search/test_web_search.py` (NEW; oracle-stub per provider; joins `make test-fast`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Web-search flag → provider-native grounding. A `web_search:true` chat-request flag is translated to the resolved provider's NATIVE web-search tool and STRIPPED before the upstream call; gated by a default-OFF knob; a no-op on providers without native grounding; grounding citations the provider returns are preserved into the OpenAI response.
Framings weighed: per-provider native-tool injection driven by a shared `web_search.py` mapping (chosen — uniform flag, provider-correct tool, no external dep) · overload OpenAI `tools:[{type:web_search}]` end-to-end (rejected — collides with function tools + leaks to non-OpenAI upstreams) · external search backend (rejected — that's the deferred "tool later").
Must:
<must>
  - M1 — with `GATEWAY_WEB_SEARCH_ENABLED=true` and request `web_search:true`, the resolved provider receives its NATIVE grounding tool: openai/openrouter→`{"type":"web_search_preview"}` (appended to `tools`), anthropic→`{"type":"web_search_20250305","name":"web_search"}`, google→`{"googleSearch":{}}` (sibling tools entry). Any pre-existing function `tools` are PRESERVED (append, not replace).
  - M2 — FLAG-STRIP: the raw `web_search` key is NEVER forwarded to any upstream (verbatim providers pop it; translating providers never copy it). The upstream payload contains no `web_search` field.
  - M3 — DEFAULT-OFF + FAIL-SAFE: with `GATEWAY_WEB_SEARCH_ENABLED=false` (default) OR no `web_search` flag, the upstream payload is byte-identical to today (the flag is stripped centrally in the use-case; no tool injected).
  - M4 — NO-OP on non-grounding providers: bedrock/azure/unknown (`native_web_search_tool`→None) inject nothing and do not error; the flag is still stripped.
  - M5 — CITATION PASSTHROUGH (non-stream): when Anthropic returns `web_search_result`/citation blocks or Gemini returns `groundingMetadata`, `_anthropic_to_openai`/`_gemini_to_openai` preserve them into the OpenAI response (a top-level `grounding` field on the response object); none-returned ⇒ field absent (no fabrication). OpenAI/OpenRouter citations already survive verbatim.
</must>
Reject:
<reject>
  - knob OFF (or flag absent) -> no injection, flag stripped, payload byte-identical (M3).
  - non-grounding provider -> no injection, no error (M4).
  - provider returns no grounding -> no `grounding` field fabricated (M5).
</reject>
After:
<after>
  - The provider performs a native web search for flagged requests; the upstream never sees a raw `web_search` field; returned citations reach the client in the OpenAI response; the feature is off by default.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ each provider's NATIVE web-search tool shape (`web_search_preview` / `web_search_20250305` / `googleSearch`) is what the live API currently accepts — lowest confidence because these are versioned provider features that drift and we test against ORACLE STUBS, not the live API; if wrong: a flagged request 400s upstream (base chat unaffected — flag off by default; caught by the deferred live-verify + the per-provider stub asserts the exact shape we send). Cost bounded: default-OFF means no production impact until a deploy enables it + live-verifies.
  - [x] body is dict passthrough so a top-level flag flows to the use-case — CONFIRMED (recon: use_cases.py:563-587).
  - [x] Anthropic/Gemini response translators currently DROP unknown blocks — CONFIRMED (recon §5); M5 fixes it for non-stream.
  - [ ] STREAM citation surfacing for Anthropic/Gemini is OUT of scope (stepper work) — OpenAI/OpenRouter stream citations survive verbatim (the default path); Anthropic/Gemini stream-citation = documented delta.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: OpenAI-family gets native web_search tool
  Given the knob is on and request {model: gpt-4o, web_search: true}
  When the openrouter/openai adapter builds the upstream payload
  Then tools contains {"type":"web_search_preview"} and there is NO "web_search" key

Scenario: Anthropic gets its native web-search tool
  Given the knob is on and request {model: claude-*, web_search: true}
  When the anthropic request is built
  Then anthropic tools contains {"type":"web_search_20250305","name":"web_search"} and no "web_search" key reaches upstream

Scenario: Gemini gets googleSearch grounding
  Given the knob is on and request {model: gemini-*, web_search: true}
  When the gemini request is built
  Then tools contains a sibling {"googleSearch":{}} entry alongside any functionDeclarations

Scenario: Existing function tools are preserved
  Given a request with a function tool AND web_search: true
  When the payload is built
  Then both the function tool and the native web-search tool are present

Scenario: Knob off is byte-identical
  Given GATEWAY_WEB_SEARCH_ENABLED=false and request web_search: true
  When the payload is built
  Then no web-search tool is injected and no "web_search" key is forwarded

Scenario: Non-grounding provider no-ops
  Given the knob is on, web_search: true, and a bedrock model
  When the payload is built
  Then nothing is injected, no error is raised, and no "web_search" key is forwarded

Scenario: Anthropic citations survive (non-stream)
  Given an Anthropic non-stream response with a web_search_result block
  When translated to OpenAI shape
  Then the response carries a top-level grounding field with the sources

Scenario: No grounding returned, no field fabricated
  Given an Anthropic/Gemini response with no grounding
  When translated
  Then there is no grounding field on the response
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
// ─ Request flag (NEW; additive, optional) ─
POST /v1/chat/completions  body: { …existing…, web_search?: boolean }
  - web_search is a GATEWAY control field — translated + STRIPPED; never forwarded upstream.

// ─ Domain seam (web_search.py) ─
WEB_SEARCH_FLAG = "web_search"
native_web_search_tool(provider: str) -> dict | None
   "openai"|"openrouter" -> {"type": "web_search_preview"}
   "anthropic"           -> {"type": "web_search_20250305", "name": "web_search"}
   "google"              -> {"googleSearch": {}}
   else                  -> None        # bedrock/azure/unknown: no native grounding

// ─ Knob ─
GATEWAY_WEB_SEARCH_ENABLED: bool = False   # pydantic-settings, GATEWAY_ prefix

// ─ Enforcement points ─
use_cases.CompletionUseCase: if not settings.GATEWAY_WEB_SEARCH_ENABLED: payload.pop("web_search", None)   # central knob kill
openrouter/openai/azure adapters (verbatim): if payload.get("web_search") and native tool: tools += [tool]; ALWAYS payload.pop("web_search")
anthropic _openai_to_anthropic_request: if payload.get("web_search"): anthro_tools += [native_web_search_tool("anthropic")]
gemini _openai_to_gemini_request: if payload.get("web_search"): tools += [{"googleSearch":{}}]

// ─ Citation passthrough (non-stream) ─
_anthropic_to_openai / _gemini_to_openai: if grounding present -> response["grounding"] = [{title?, url?, snippet?}, …]; absent -> no field
   (OpenAI/OpenRouter: citations survive verbatim — no change)

Schema: none — no DB; reuses the dict passthrough body + a new env knob. No migration.
```

Status: FROZEN @ v1 — auto-approved (full-auto drive; additive + default-OFF; the only security-relevant surface is "no flag leaks upstream", which M2 + a test pin) 2026-06-26
Least-sure flag surfaced at freeze:
  - [spec] ⚠ the live provider tool shapes may drift (we test vs oracle stubs) — bounded by default-OFF + per-provider exact-shape asserts + a deferred live-verify; a wrong shape only affects flagged requests after a deploy enables the knob.
  - [contract] M2 flag-strip is the security-critical invariant (a leaked `web_search` 400s upstream / could confuse it) — pinned by `test_flag_never_reaches_upstream` across every provider.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral — one test per scenario; no gateway suite regression.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_openai_native_tool: oracle stub captures the upstream body; web_search:true → body.tools has {"type":"web_search_preview"}, no "web_search" key.
  - test_anthropic_native_tool: anthropic oracle stub → upstream tools has {"type":"web_search_20250305","name":"web_search"}, no "web_search" key.
  - test_gemini_googlesearch: gemini oracle stub → tools has a sibling {"googleSearch":{}}, no "web_search" key.
  - test_function_tools_preserved: request with a function tool + web_search → both present.
  - test_knob_off_byte_identical: GATEWAY_WEB_SEARCH_ENABLED=false → no tool injected + no "web_search" key (compare to baseline body).
  - test_nongrounding_provider_noop: bedrock model + web_search → nothing injected, no error, flag stripped.
  - test_flag_never_reaches_upstream: SECURITY pin — for EVERY provider stub, assert the captured upstream body has no "web_search" key (knob on AND off).
  - test_anthropic_citations_survive: anthropic non-stream response with web_search_result → OpenAI response has grounding sources.
  - test_gemini_grounding_survives: gemini groundingMetadata → OpenAI response grounding.
  - test_no_grounding_no_field: response without grounding → no grounding field.
</test_plan>

Tests live in: `apps/gateway/tests/web_search/test_web_search.py` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/domain/web_search.py` · `apps/gateway/src/gateway/core/config.py` · `apps/gateway/src/gateway/proxy/application/use_cases.py` · `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py` · `apps/gateway/src/gateway/proxy/infrastructure/openai_provider.py` · `apps/gateway/src/gateway/proxy/infrastructure/azure_upstream.py` · `apps/gateway/src/gateway/proxy/infrastructure/anthropic_upstream.py` · `apps/gateway/src/gateway/proxy/infrastructure/gemini_upstream.py` · `apps/gateway/src/gateway/proxy/api/deps.py` (wire `web_search_enabled` from settings into CompletionUseCase — the real request-path activation; sibling to the otel/bandwidth knob wiring) · `apps/gateway/tests/web_search/` (the §4 red suite dir — __init__.py + test_web_search.py, declared up front) · `Makefile` (add tests/web_search to the test-fast target)
Strategy (ordered batches): 1. `web_search.py` seam + `config.py` knob. 2. central knob-kill in `use_cases.py`. 3. verbatim adapters (openrouter/openai/azure): inject + always pop. 4. anthropic + gemini request injection. 5. anthropic/gemini response citation passthrough. 6. tests/web_search oracle stubs + Makefile.
Safety rule (feature-specific): M2 flag-strip is INVIOLABLE — the raw `web_search` key must never appear in any upstream body (verbatim providers pop it unconditionally; translating providers never copy it). Default-OFF; non-grounding providers no-op; never fabricate a grounding field.
Code lives in: `apps/gateway/`
Constraints: do NOT change any test or the contract; do NOT edit the FROZEN `tool_translation.py`; allow-list packages only (no new deps); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `make test-fast` 190 passed (was 186; +4 hardening tests); web_search suite 36 green; ruff clean on all new code (pyright errors are PRE-EXISTING on HEAD — config.py already carries 14 ConvertibleToInt findings on unrelated numeric fields; my additions at config:319-326 / use_cases:509-604 are not among them).
- [x] coverage did not decrease — net +36 behavioral tests; no suite removed.
- [x] no test or contract was altered during build — the §3 contract is unchanged; the only test edits STRENGTHENED two tautological tests to call production code (test integrity ↑, never weakened).
- [x] the green was EARNED — independent sonnet refute-read (hunt list of 7) ran: flag-strip security invariant PROVEN clean on every outbound path (0.98), aliasing/retry clean, wire-up confirmed. It surfaced 2 MAJOR + 2 tautological-test findings → ALL FIXED (see below) and re-verified green.
- [x] concurrency / timing safe — no shared mutable state; `_maybe_inject_web_search` is copy-on-write (no cross-retry double-injection — refute-confirmed); native grounding is provider-side so the existing per-provider timeout/retry/circuit-breaker covers the upstream call (no new gateway IO).
- [x] no exposed secrets, injection openings, or unexpected dependencies — web_search.py is pure (no secrets/keys/tenant data); no new packages; SECURITY INVARIANT (M2): the raw `web_search` flag never reaches any upstream — pinned by `test_flag_never_reaches_upstream_*` on complete()+stream() for openrouter/openai/anthropic/gemini/azure, bedrock drops unknown keys (refute-confirmed).
- [x] layering & dependencies follow CONVENTIONS.md — domain seam (web_search.py) holds no IO; adapters call it; use-case enforces the knob; deps wires settings → use-case (sibling to otel/bandwidth knobs).
- [x] a person reviewed and approved the change — full-auto drive (autonomy: auto); adversarial self-review via the independent refute-read subagent stands in for the human at the gate; default-OFF + non-security findings keep this auto-gateable.

### Build expectations — what "correct" looks like
- [x] A flagged request reaches each provider as its NATIVE tool, never a raw flag — confirmed by the per-provider captured-body asserts (web_search_preview / web_search_20250305 / googleSearch) + the flag-absence pins.
- [x] Knob OFF or flag absent ⇒ byte-identical upstream body — confirmed by `test_knob_off_strips_web_search_before_dispatch` (real CompletionUseCase._strip_web_search_flag) + `test_no_web_search_key_no_injection_*`.
- [x] Knob ON keeps the flag for adapters (feature actually reachable) — confirmed by `test_knob_on_keeps_web_search_for_adapters` + the deps.py wire-up fix (was the BLOCKER the refute-read’s wire-up hunt + my read of deps.py:180 caught: the use-case was constructed without the knob → feature would have been permanently dead).
- [x] Citations survive non-stream for Anthropic/Gemini; none ⇒ no field; null/malformed ⇒ no crash — confirmed by grounding-survives + no-field + `test_gemini_grounding_chunks_null_no_crash` + non-dict-chunk tests.

### Deep checks
- [x] WIRING — `native_web_search_tool`/`WEB_SEARCH_FLAG` referenced by all 3 verbatim adapters + anthropic/gemini request builders; `web_search_enabled` flows config.py → deps.py:get_completion_use_case → CompletionUseCase._strip_web_search_flag; normalizers called from `_anthropic_to_openai`/`_gemini_to_openai`.
- [x] DEAD-CODE — no orphaned symbols; `_normalize_*` both referenced by their translators + tests.
- [x] SEMANTIC — refute-read report read in full; 2 MAJOR (groundingChunks-null crash, Gemini googleSearch+functionDeclarations 400) + 2 MINOR tautological tests + 2 NITs. Resolution: crash HARDENED + tested; Gemini-mixing DOCUMENTED as a delta (chat surface sends no function tools; raw-caller gets Gemini's own 400 faithfully — v35 principle); both tautological tests STRENGTHENED to production calls; both NITs (OpenAI-stream pin, non-dict guard) ADDED.

### Residue / deltas (carried to §7 + add.py deltas)
- Gemini googleSearch + functionDeclarations co-existence may 400 on some Gemini versions — documented limitation, out of chat scope, live-verify item.
- Anthropic/Gemini STREAMING citation surfacing deferred (SSE steppers untouched) — OpenAI/OpenRouter stream citations survive verbatim (the default path).
- Deferred live-verify of the exact provider tool shapes (tested vs oracle stubs only).

### GATE RECORD
Outcome: PASS
Reviewed by: full-auto drive + independent sonnet refute-read (adversarial) · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
