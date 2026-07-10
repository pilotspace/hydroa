# TASK: Opt-in output JSON-schema validation with bounded retry

slug: output-schema-validation · created: 2026-07-10 · stage: production
milestone: logs-explorer-guardrails-v2
sensitivity: architecture   <!-- supersedes a frozen v11 behavioral pin (translate-don't-enforce); adds a new third-party dependency; additive extension to shared core/errors.py -->
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: ground   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/proxy/domain/response_format_translation.py` — the v11 FROZEN seam. `extract_response_format(payload) -> ResponseFormat | None` (raises `ValueError("ERR_UNSUPPORTED_RESPONSE_FORMAT" | "ERR_INVALID_JSON_SCHEMA")`); `ResponseFormat`/`JsonSchemaSpec` TypedDicts. The model output is ALWAYS `message.content` (a JSON string) regardless of provider (docstring L18-19) — this is what makes use-case-level, provider-agnostic validation possible. **This file must not be edited by this task** (see Issues/Risks).
- `apps/gateway/src/gateway/proxy/application/use_cases.py:CompletionUseCase.complete` (body L1143-1789) — the non-streaming completion path. Real upstream call: `status, response_body, served_model_id = await model_router.complete(body, upstream=upstream)` (L1559-1562, `model_router is not None` branch) else `status, response_body = await upstream.complete(body)` / `served_model_id = model_id` (L1568-1570, no-router frozen-suite fallback) — this is the exact call the bounded retry re-invokes. Response cache store (unmasked) at L1612-1703; post-call guardrail `evaluate_post` (fail-OPEN, MASK/AUDIT only, gated `status == 200`) at L1708-1718; billing fire at `_fire_record_with_raw(...)` L1727-1736 keyed on `served_model_id` (never `model_id`, never `response_body["model"]`, per the v6 settled billing rule); return `status, response_body, x_cache` at L1758.
- `apps/gateway/src/gateway/proxy/application/use_cases.py:CompletionUseCase._strip_web_search_flag` (L660-673) — the DIRECT precedent for this task's opt-in shape: a gateway-only top-level body field (`"web_search"`, not part of the OpenAI wire) that is popped from the body BEFORE dispatch when an operator Settings flag (`self._web_search_enabled`) is False, so the outbound request is byte-identical; kept when the flag is True. `web_search_enabled: bool = False` is a `CompletionUseCase.__init__` param (L544) wired from `gateway.core.config.Settings.web_search_enabled` (`config.py:354`, env `GATEWAY_WEB_SEARCH_ENABLED`, default False) via `apps/gateway/src/gateway/proxy/api/deps.py:199-201`.
- `apps/gateway/src/gateway/proxy/infrastructure/response_cache.py:build_cache_key` (L39-49) and `_CACHE_KEY_FIELDS` (L23-36 — `model, messages, temperature, top_p, max_tokens, stop, n, presence_penalty, frequency_penalty, seed`). **`response_format` is NOT in this allow-list today** — a pre-existing gap (see Issues/Risks). `x_cache = "bypass"` path (use_cases.py L1496-1503, driven by `Cache-Control: no-cache`) is the existing per-request cache-skip mechanism this task reuses.
- `apps/gateway/src/gateway/proxy/infrastructure/circuit_breaker.py:CircuitBreaker` (L35-138) — trips only on `on_upstream_error()` (transport failure: `UpstreamUnavailableError`/`CircuitOpenError`), 5-failure threshold, 30s cooldown, per-replica in-process. A schema-mismatch retry is a normal 200 call from the breaker's point of view — it must never be miscounted as an upstream failure.
- `apps/gateway/src/gateway/proxy/domain/ports.py:UsageRecordExtras` (L33-73) — the typed, additive extras seam for `UsageRecorder.record()`. `usage_source: str` (L71) is ALREADY a supported extra (values seen in the wild: `"frame"`, `"stream_fallback"`, `"client_disconnect"`, `"openrouter_recovered"`, `"batch"`, `"realtime_relay"` — `apps/gateway/src/gateway/usage/application/recorder.py` L56-68 `supported_extras` frozenset, `apps/gateway/src/gateway/usage/infrastructure/orm.py:97` `usage_source` column, `server_default="frame"`). No migration is needed to add a new `usage_source` value — this is the billing-honesty lever for the retry (see §1/§3).
- `apps/gateway/src/gateway/core/errors.py:ProblemError` (L10-25) and `problem_response(status, code, title, detail=None)` (L27-36) — the frozen RFC7807-ish envelope (`{type, title, status, code, detail?}`) EVERY error response uses. It has no field for structured extra data (e.g. raw output) today — an additive `extra: dict[str, object] | None = None` param is needed (see Issues/Risks — this is a shared, cross-feature file).
- `apps/gateway/src/gateway/core/error_catalog.py` — the `ErrorSpec(status, code, title)` registry (e.g. `RATE_LIMITED`, `UPSTREAM_UNAVAILABLE` around L355-402 in the working tree). New entries land here.
- `apps/gateway/src/gateway/proxy/infrastructure/anthropic_upstream.py` — streaming coercion unwrap (v11, unaffected by this task but grounds the "streaming is structurally hard" ruling): `self._coercion_block_index` / `self._saw_coercion` state (L680-681), `content_block_start` marks the coercion block index (L746-747), `input_json_delta` routes by that index into `delta.content` (L767-768), `message_delta` overrides `finish_reason` to `"stop"` when only the coercion tool fired (L786-787). This proves the coerced JSON is assembled INCREMENTALLY, frame-by-frame, and is already partially on the wire to the client before the SSE stream ends — there is no buffered "final body" to validate-then-swap on a stream, confirming streaming validation is structurally out of reach without a protocol change (buffer-then-flush), not just a scoping preference.
- `apps/gateway/pyproject.toml` — no `jsonschema` dependency exists today (`pydantic[email]`, `pydantic-settings` are the only validation-adjacent deps). Adding a JSON-Schema validator is a NEW third-party dependency (see Issues/Risks).

Context (working folder): the sibling milestone tasks `payload-capture-store`, `per-key-guardrail-policies`, `ml-moderation-layer`, `guardrail-analytics` are separate, own their own contracts; this task has `depends-on: none` per MILESTONE.md and does not read/write anything they own. No existing TASK.md content beyond the blank template existed before this draft.

Honors (patterns / conventions):
- PROJECT.md v11 fold: "response_format is DEPTH on the v9 ChatTranslator seam... billing still keys on the SERVED model id with native usage" — this task's retry must preserve that (bill on `served_model_id`, never `model_id`/`response_body["model"]`).
- PROJECT.md v6 fold: "a frozen behavioral pin... is changed by the SUPERSESSION pattern — record the supersession at the new task's freeze, leave the frozen file untouched, and keep the new default behavior-preserving." Applied literally: the SUPERSEDED pin's TASK.md/record is never edited; `response_format_translation.py`'s FROZEN TypedDicts/helpers are not touched by this task (the opt-in field is a sibling top-level body field, not a new key inside `json_schema`); the default (opt-in OFF) stays byte-identical to the v11 no-op path.
- PROJECT.md v8 fold: "a new domain error reuses an EXISTING error-catalog spec... no new status/code literal" — informs reusing `ERR_INVALID_JSON_SCHEMA` for the new pre-flight meta-validation Reject rather than minting a near-duplicate code.
- CLAUDE.md: "design for failure... in IO request" — the retry is a SECOND real upstream call; it must go through the SAME `CircuitBreaker`/timeout the first call did (no bypass), and must not re-run governance/budget/bandwidth admission a second time for what is still one client-admitted request (see §1 Must).
- "Honest degradation... nothing fakes success" (PROJECT.md invariants) — an unparseable or missing `message.content` counts as a validation failure exactly like a schema mismatch; never silently treated as pass.

Seams consulted: none (no `.add/SEAMS.md` entry matches; response_format's own seam is read directly above, not cited secondhand).

Anchors the contract cites: `extract_response_format` (response_format_translation.py:69), `CompletionUseCase.complete` upstream-call site (use_cases.py:1559-1570), `_strip_web_search_flag` (use_cases.py:660-673) as the opt-in-stripping template, `UsageRecordExtras.usage_source` (ports.py:71), `CircuitBreaker.on_upstream_error`/`guard` (circuit_breaker.py:91-108), `ProblemError`/`problem_response` (core/errors.py:10-36), `build_cache_key`/`_CACHE_KEY_FIELDS` (response_cache.py:23-49).

Issues/Risks (→ feed §1):
- **R1 (invariant collision, HIGH):** PROJECT.md's own domain invariant states "Every proxied request produces exactly one usage record." A billing-honest bounded retry (provider charged twice → tenant should see two priced legs) produces TWO rows for ONE client-facing request — this is not a simple feature choice, it is a proposed SECOND exception to a core invariant. PROJECT.md v29 fold already records a precedent for this ("when a contract grants a second exception to a core invariant, record the decision verbatim at the §3 freeze AND mirror it as an inline comment at the enforcing WHERE clause") — this task follows that precedent rather than inventing a new one, but Tin must explicitly ratify it (this is §1's ⚠ lowest-confidence flag).
- **R2 (pre-existing cache gap, MEDIUM, out of this task's scope to fix):** `_CACHE_KEY_FIELDS` (response_cache.py:23-36) does NOT include `response_format` at all. Two requests with identical `messages`/`model` but DIFFERENT `response_format` (or none) already collide on the same cache key today — a pre-existing gap this task inherits, not one it introduces. Left unaddressed, a `validate_output` request could receive a cache HIT that was written by an earlier, never-validated request (or a different schema entirely), silently defeating the entire feature's promise. Fixing `_CACHE_KEY_FIELDS` itself belongs to the response-caching task's own frozen contract (not touched here — see the SUPERSESSION rule); this task's own contract instead makes `validate_output:true` requests bypass the cache layer entirely (§1 Must) — narrowest blast radius, zero interaction with the frozen cache-key contract. The `_CACHE_KEY_FIELDS` gap itself is flagged forward as a SPEC delta (§7) for a future cache-controls task, not solved here.
- **R3 (new dependency):** No JSON-Schema validator library exists in this repo. `jsonschema` (pure-Python, BSD-3, supports Draft 2020-12/7/etc. via `validators.validator_for`) is the natural fit and needs pinning in `apps/gateway/pyproject.toml` — an allow-listed package addition per §5 Constraints, needing explicit freeze sign-off (this task cannot silently add a dependency).
- **R4 (shared-file edit):** `core/errors.py`'s `ProblemError`/`problem_response` is used by EVERY error response in the gateway. Carrying `raw_output` + `validation_errors` in the 422 body requires an ADDITIVE optional param (`extra: dict[str, object] | None = None`), never a shape change to the existing 4 fields — byte-identical for every other caller by construction (default `None`).
- **R5 (latency):** a validate_output request that mismatches roughly DOUBLES worst-case wall-clock latency (two sequential upstream calls, no parallelism possible — the second call only fires after the first is known to have failed validation). Worth surfacing, not blocking.
- **R6 (composability, low risk, confirmed safe):** the Anthropic json_schema COERCION path (v11) forces the model via `ToolChoiceNamed` to call exactly the `json_output` tool and unwraps it into `message.content` before this task's code ever sees the response — so validation always operates on a uniform OpenAI `message.content` string regardless of provider or coercion; no provider-specific branching is needed in this task's own code.

Related intent: PROJECT.md "Spec / Living Document" v11 fold (response_format seam); MILESTONE.md "Shared decisions" — "Output-schema validation supersedes v11 translate-don't-enforce ONLY as opt-in — record the SUPERSESSION at the freeze; a request without the opt-in stays byte-identical" (this task owns that shared/risky contract per MILESTONE.md's "Shared / risky contracts" list); GLOSSARY.md has no existing entry for this concept (delta below).

Ground SHA: `2071046`

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: opt-in response JSON-schema validation with one bounded retry (SUPERSEDES the v11 "translate-don't-enforce" pin, opt-in only)

Framings weighed:
- **(chosen) Per-request opt-in field (`validate_output: true`, top-level body, sibling to `response_format`), gated by an operator-wide kill-switch (`GATEWAY_OUTPUT_VALIDATION_ENABLED`, default False)** — mirrors the PROVEN `web_search`/`_strip_web_search_flag` shape exactly (a gateway-only body field, stripped pre-dispatch when the operator switch is off). The schema itself is inherently per-request (it lives in `response_format.json_schema.schema`, never persisted), so the opt-in naturally travels with it; zero migration, zero new admin API/dashboard surface, narrowest blast radius. The operator switch gives ops a global off-lever independent of any caller's request (defense in depth / staged rollout), matching the `web_search_enabled` / `input_modality_guard_enabled` precedent of "frozen default-OFF knob."
- Per-key/tenant config column (mirrors `tenants.semantic_cache_enabled` / `tenants.batch_grouping_enabled`, resolved into `AuthzResult` via the existing LEFT JOIN, zero extra DB reads) — rejected as PRIMARY: needs a migration + admin API + dashboard toggle for a behavior that is meaningless without a per-call schema anyway (a key-wide "always validate" flag says nothing about WHICH schema); heavier blast radius for no behavioral gain over the request field. Not ruled out entirely — could layer on top later as a tenant-wide default if callers want "always retry when I send json_schema" without repeating the flag, but that is out of THIS task's scope.
- Nest the opt-in inside `response_format.json_schema` itself (e.g. `json_schema.verify: true`, an additive `NotRequired` TypedDict field, mirroring how `strict` already sits there) — rejected: requires editing `response_format_translation.py`'s FROZEN `JsonSchemaSpec`/`ResponseFormat` shapes, which the SUPERSESSION rule says to leave untouched; a sibling top-level field achieves the same colocation-with-schema intent with zero edits to that frozen module.

Must:
<must>
  - M1 (opt-in gate): the feature engages ONLY when BOTH `settings.output_validation_enabled` (operator, default False) is True AND the request body carries `validate_output: true`. Absent either condition, `validate_output` (if present) is popped from the body before dispatch and the request/response path is byte-identical to today (mirrors `_strip_web_search_flag`) — no schema parse, no extra call, no new usage_source, no cache-bypass.
  - M2 (gateway-only field): `validate_output` is NEVER forwarded to any upstream provider — it is popped from the body immediately after being read, unconditionally (unlike `web_search`, which IS translated into a native tool when on; `validate_output` has no upstream analogue at all).
  - M3 (schema meta-validation, pre-flight): when M1's gate is engaged, the caller's `response_format.json_schema.schema` is meta-validated for JSON-Schema well-formedness BEFORE the first upstream call (`jsonschema.validators.validator_for(schema).check_schema(schema)` or equivalent). A structurally invalid schema is rejected pre-flight — never burns a paid upstream call to discover the schema itself was broken.
  - M4 (validation target): only a non-streaming request may engage the feature (streaming is rejected — see M11 / `ERR_OUTPUT_VALIDATION_UNSUPPORTED_ON_STREAM`). On a `status == 200` response, EVERY choice's `message.content` must parse as JSON and validate against the schema for the response to count as valid; a missing/non-string `content`, unparseable JSON, or a schema mismatch on ANY choice counts as a validation failure (never partial-accept some choices).
  - M5 (bounded retry, exactly one): on the FIRST validation failure, the use case re-invokes the IDENTICAL completion call used for attempt 1 — `model_router.complete(body, upstream=upstream)` when a router is wired, else `upstream.complete(body)` — with the SAME `body` (unmodified). Exactly one retry; a second failure is terminal (never a third call). The retry reuses the existing model-fallback machinery as-is (it may legitimately land on a different fallback candidate than attempt 1 — accepted, not treated as a defect).
  - M6 (no re-admission on retry): the retry does NOT re-run pre-call governance, per-key/team budget, rate-limit, or bandwidth-pacing checks — those already admitted this ONE client request once; re-running them mid-flight would risk a confusing partial-failure (billed for attempt 1, blocked before attempt 2). Mirrors how the existing model-fallback candidate loop does not re-run admission per candidate either.
  - M7 (circuit breaker unaffected by content mismatch): a schema mismatch is never reported to `CircuitBreaker.on_upstream_error()` — only a genuine transport failure (`UpstreamUnavailableError`/`CircuitOpenError`) trips it. If the breaker is OPEN when the retry attempts to fire, the retry raises the SAME 502 `ERR_UPSTREAM_UNAVAILABLE` path as any other blocked call (never bypasses the breaker for "it's just a retry").
  - M8 (billing honesty — the invariant exception, ⚠ see Assumptions): when a retry fires, TWO usage records are written for the one client-facing request — attempt 1's real usage billed with `usage_source="validation_retry"`, attempt 2's real usage billed with the normal default (`usage_source="frame"` on success, or on a final failure also `"validation_retry"`-tagged as the terminal attempt). Both key on their own call's `served_model_id`. This is an explicit, freeze-recorded SECOND exception to the "exactly one usage record per proxied request" invariant (PROJECT.md, precedented by the v29 alerts-events-viewer exception pattern) — NOT a silent violation.
  - M9 (cache bypass on opt-in): a `validate_output:true` request bypasses the response cache entirely (exact + semantic + vector layers) for BOTH read and write — treated identically to the existing `Cache-Control: no-cache` bypass path (`x_cache = "bypass"`). Reason: `response_format` is not part of `_CACHE_KEY_FIELDS` (a pre-existing gap, R2) — bypassing is the only way to guarantee a validating caller never silently receives an unvalidated cached body, without touching the frozen cache-key contract.
  - M10 (guardrail ordering): schema validation (and any retry) runs BEFORE post-call guardrail `evaluate_post` (PII masking) — masking can alter string field values or JSON structure in ways that would falsely fail a valid response's schema check. The final, validated `response_body` then flows through the existing masking/cache/usage-record steps unchanged.
  - M11 (streaming — hard reject, not silent ignore): `stream: true` combined with an engaged M1 gate is REJECTED (R1 below), never silently ignored — matches the project's existing "never silently drop a directive" convention (`extract_response_format`'s own `ERR_UNSUPPORTED_RESPONSE_FORMAT`/`ERR_INVALID_JSON_SCHEMA` docstring rule) rather than quietly serving an unvalidated stream to an opted-in caller.
  - M12 (raw output exposure): a terminal validation failure returns the caller's OWN model output (attempt 2's raw `message.content`, size-capped) in the error body. This is not a new exposure (the caller already owns/paid for that output) — it is NEVER written to application logs (mirrors the existing no-raw-payload-in-structured-logs convention).
  - M13 (error envelope, additive): the terminal-failure error body extends `core/errors.py`'s `ProblemError`/`problem_response` with an ADDITIVE optional `extra: dict[str, object] | None = None` — every other error response in the gateway is unaffected (default `None`).
  - M14 (dependency): `jsonschema` is added to `apps/gateway/pyproject.toml` as an allow-listed dependency (pure-Python, no transitive network/native deps) for both the pre-flight meta-validation (M3) and the output-vs-schema check (M4).
</must>
Reject:
<reject>
  - `stream: true` AND the M1 gate engaged (operator flag ON + `validate_output: true`) -> "ERR_OUTPUT_VALIDATION_UNSUPPORTED_ON_STREAM" (400; M11)
  - the M1 gate engaged but `response_format` is absent, `{"type":"text"}`, or `{"type":"json_object"}` (no schema to validate against) -> "ERR_OUTPUT_VALIDATION_REQUIRES_JSON_SCHEMA" (400)
  - the M1 gate engaged and `response_format.json_schema.schema` fails JSON-Schema meta-validation (M3) -> "ERR_INVALID_JSON_SCHEMA" (400 — REUSES the existing v11 code per PROJECT.md v8's "reuse an existing error-catalog spec" convention: same class of problem, new trigger)
  - both attempt 1 and attempt 2 fail validation (M5/M8 exhausted) -> "ERR_OUTPUT_SCHEMA_VALIDATION_FAILED" (422; carries `raw_output` + bounded `validation_errors[]` per M12/M13) — And both attempts' usage rows are still recorded (M8) and the client never receives partially-valid content.
</reject>
After:
<after>
  - a tenant caller can send `validate_output: true` (with the operator flag on) alongside `response_format:{type:"json_schema",...}` and receive EITHER a schema-conformant response (possibly after one silent, billed retry) OR a structured 422 carrying the raw non-conformant output — never a silently-wrong JSON body.
  - a request without `validate_output` (or with the operator flag off) is BYTE-IDENTICAL to today's response_format path — no new call, no new usage row, no cache-bypass, no new error surface.
  - PROJECT.md's v11 "translate-don't-enforce" pin is recorded as SUPERSEDED-when-opted-in at this task's freeze; the v11 pin's own historical record is left untouched.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **M8's two-usage-records-per-request billing design is the single lowest-confidence call in this draft** — it correctly preserves cost honesty (the provider WAS paid twice) but knowingly proposes a second named exception to a stated PROJECT.md domain invariant ("every proxied request produces exactly one usage record"). Lowest confidence because: (a) it is genuinely a judgment call Tin may want to make differently — e.g., absorbing attempt 1's cost as an undisclosed platform loss (ONE usage record, simpler, invariant untouched) is a legitimate alternative if margin/dashboards assume 1:1 request:row cardinality somewhere not yet audited; (b) I have not verified every downstream consumer of `usage_records` (billing dashboards, the reconciliation query in `usage/application/reconciliation.py`, per-request cost APIs) tolerates >1 row per logical request gracefully — `usage_source` grouping already handles this pattern for `client_disconnect`/`openrouter_recovered` pairs, but I have not traced every read site. If wrong: either a silent margin leak (if forced to one record and I mis-picked which call's usage to keep) or a confusing double-count in a dashboard that assumes 1 row = 1 request (if two records ship without every consumer auditing `usage_source`).
  - [ ] confirm 422 (vs 502) is the right terminal status for `ERR_OUTPUT_SCHEMA_VALIDATION_FAILED` — 422 reads as "the produced entity failed content validation" (my recommendation, matches `RequestValidationError`'s existing 422 use in `core/errors.py:48`); 502 would frame it as "upstream misbehaved" (closer to `ERR_UPSTREAM_UNAVAILABLE`'s class but that's reserved for transport/availability failures, not content-shape failures) — medium confidence, low cost if wrong (a status-code-only change, no shape impact).
  - [ ] confirm `jsonschema` (vs. hand-rolling a minimal subset validator matching OpenAI's own restricted "strict" schema subset) is the right dependency choice — medium confidence; `jsonschema` is heavier but far more correct for non-strict/complex schemas (composition, $ref, format), and this repo already allow-lists third-party deps per task rather than avoiding them on principle.
  - [ ] confirm the ~2x worst-case latency on a mismatching request (R5) is acceptable without a shorter/independent timeout budget for the retry leg specifically — low-medium confidence; no evidence either way from the codebase, purely a product-latency-tolerance call.
  - [ ] confirm validating ALL choices (M4, for `n>1` requests) rather than only `choices[0]` is the right scope — medium-high confidence (simplest, safest, and the retry regenerates every choice anyway so partial-index replacement was never viable); flagging since `n>1` + `json_schema` + `validate_output` is an unexercised corner nobody has asked for yet.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: opt-in off by default — byte-identical path   # M1
  Given GATEWAY_OUTPUT_VALIDATION_ENABLED is False (default) and a request carries validate_output:true + response_format:{type:"json_schema",...}
  When the request is dispatched
  Then validate_output is stripped before the upstream call, exactly one upstream call fires, exactly one usage record is written, and the response is returned as-is — identical to a request that never set validate_output
  And no schema is parsed, no cache-bypass occurs, no new error surface is reachable

Scenario: operator flag on, caller does not opt in — byte-identical path   # M1
  Given GATEWAY_OUTPUT_VALIDATION_ENABLED is True and a request omits validate_output (response_format may or may not be json_schema)
  When the request is dispatched
  Then the request/response path is identical to today's v11 response_format behavior (translate-don't-enforce, no validation, no retry)
  And the v11 no-op invariant (byte-identical when the directive is absent) holds unchanged

Scenario: gateway-only field never reaches upstream   # M2
  Given the M1 gate is engaged (operator flag on, validate_output:true, response_format is json_schema)
  When the outbound request is built for the upstream call
  Then the outbound body sent to the provider adapter does not contain a validate_output key on either attempt 1 or attempt 2
  And the provider's own request otherwise matches the existing v11 translation unchanged

Scenario: malformed schema rejected pre-flight, zero upstream calls   # M3, R (ERR_INVALID_JSON_SCHEMA)
  Given the M1 gate is engaged and response_format.json_schema.schema is not a structurally valid JSON Schema (e.g. an unknown/contradictory type keyword)
  When the request is dispatched
  Then the gateway returns 400 ERR_INVALID_JSON_SCHEMA before any upstream call is made
  And zero usage records are written (no call was ever billed) and the upstream circuit breaker state is untouched

Scenario: first attempt already valid — no retry, single bill   # M4, M5, After
  Given the M1 gate is engaged and the model's first response's every choice's message.content parses and validates against the schema
  When the request completes
  Then exactly one upstream call occurs, exactly one usage record is written (usage_source default "frame"), and the schema-conformant response is returned unchanged
  And no retry call, no second usage row, no ERR_OUTPUT_SCHEMA_VALIDATION_FAILED is reachable

Scenario: unparseable JSON content counts as a validation failure   # M4
  Given the M1 gate is engaged and the model's first response's message.content is not valid JSON (e.g. truncated or prose-wrapped)
  When the response is evaluated
  Then it is treated identically to a schema mismatch and the bounded retry (M5) fires
  And the malformed first attempt's content is never returned to the caller directly (only via the terminal error's raw_output if the retry also fails)

Scenario: schema mismatch triggers exactly one retry that then succeeds   # M5, M6, M7, M8, After
  Given the M1 gate is engaged and attempt 1's output fails validation but attempt 2's output (from the identical model_router.complete/upstream.complete call, same body) validates
  When the request completes
  Then exactly two upstream calls occur, TWO usage records are written (attempt 1: usage_source="validation_retry"; attempt 2: usage_source="frame"), each keyed on its own call's served_model_id
  And attempt 2's schema-conformant response is returned to the caller; pre-call governance/budget/rate-limit/bandwidth are each evaluated exactly once, not twice (M6); the circuit breaker's failure counter is untouched by the mismatch (M7)

Scenario: both attempts fail validation — terminal structured error   # R (ERR_OUTPUT_SCHEMA_VALIDATION_FAILED), M8, M12, M13
  Given the M1 gate is engaged and both attempt 1 and attempt 2 fail validation
  When the request completes
  Then the gateway returns 422 ERR_OUTPUT_SCHEMA_VALIDATION_FAILED carrying attempt 2's raw (size-capped) message.content and a bounded validation_errors[] list
  And TWO usage records are written (both real upstream calls were paid for and billed) and neither attempt's raw output is written to application logs

Scenario: circuit open during the retry — fails like any other blocked call   # M7
  Given the M1 gate is engaged, attempt 1 fails validation, and the circuit breaker is OPEN by the time the retry would fire (tripped by unrelated concurrent transport failures)
  When the retry is attempted
  Then the retry raises the same 502 ERR_UPSTREAM_UNAVAILABLE path any other circuit-open call would raise — it never bypasses the breaker
  And attempt 1's usage record (validation_retry) is still recorded; the response is the standard 502 body, not a schema-validation error

Scenario: streaming + opt-in is rejected, never silently ignored   # M11, R (ERR_OUTPUT_VALIDATION_UNSUPPORTED_ON_STREAM)
  Given the M1 gate is engaged and the request carries stream:true
  When the request is dispatched
  Then the gateway returns 400 ERR_OUTPUT_VALIDATION_UNSUPPORTED_ON_STREAM before any upstream call
  And a plain stream:true request (validate_output absent or the operator flag off) is completely unaffected — SSE streaming remains byte-identical to today

Scenario: opted-in request without a json_schema response_format is rejected   # R (ERR_OUTPUT_VALIDATION_REQUIRES_JSON_SCHEMA)
  Given the M1 gate is engaged (validate_output:true) but response_format is absent, {"type":"text"}, or {"type":"json_object"}
  When the request is dispatched
  Then the gateway returns 400 ERR_OUTPUT_VALIDATION_REQUIRES_JSON_SCHEMA before any upstream call
  And a request with the identical response_format but validate_output absent (or operator flag off) proceeds through the existing v11 path unchanged

Scenario: opted-in request bypasses the response cache on both read and write   # M9
  Given the M1 gate is engaged and an identical body (including response_format) has a fresh entry in the exact/semantic/vector cache from a prior non-validating request
  When the opted-in request is dispatched
  Then the cache is not consulted (no HIT is served from the unvalidated entry) and the opted-in request's own eventual valid response is not written to the shared cache key
  And a later non-opted-in request with the same body is unaffected — it may still read/write the cache exactly as today

Scenario: validation runs before PII masking so masking cannot manufacture a false mismatch   # M10
  Given the M1 gate is engaged, tenant guardrail PII masking is active, and attempt 1's raw output validates against the schema
  When post-call processing runs
  Then schema validation succeeds against the UNMASKED content, no retry fires, and post-call evaluate_post masking is applied afterward to the already-validated body before it is returned/cached
  And the caller receives the masked-but-schema-valid body, never an unmasked one

Scenario: n>1 requires every choice to validate   # M4 edge case
  Given the M1 gate is engaged, response_format is json_schema, and the request sets n:2
  When attempt 1 returns two choices where choice[0] validates but choice[1] does not
  Then the response as a whole counts as a validation failure and the bounded retry (M5) fires for the whole request (not a per-choice patch)
  And if attempt 2 has both choices valid, the full attempt-2 response (both choices) is returned and billed as the single successful attempt
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

**SUPERSESSION NOTICE**: this contract SUPERSEDES the PROJECT.md v11 fold "the gateway TRANSLATES the directive but does NOT validate/repair the model's output against the schema (translate-don't-enforce)" — ONLY when a request opts in (M1). The v11 pin's own historical record (its archived TASK.md and the PROJECT.md fold text itself) is left untouched, per the PROJECT.md v6 settled SUPERSESSION pattern; a future reader who diffs this task's fold entry against the v11 fold entry will see both, with this one dated later and scoped "opt-in only."

```
POST /v1/chat/completions   body: { ..., response_format: {type:"json_schema", json_schema:{name, schema, strict?}}, validate_output?: true }
  200 -> { ...existing chat-completion shape, message.content valid against schema (attempt 1 or attempt 2) }
  400 -> { code: "ERR_OUTPUT_VALIDATION_UNSUPPORTED_ON_STREAM" }     # stream:true + engaged M1 gate
  400 -> { code: "ERR_OUTPUT_VALIDATION_REQUIRES_JSON_SCHEMA" }      # engaged M1 gate, response_format not json_schema
  400 -> { code: "ERR_INVALID_JSON_SCHEMA" }                          # engaged M1 gate, schema meta-invalid (REUSED v11 code, new trigger)
  422 -> { code: "ERR_OUTPUT_SCHEMA_VALIDATION_FAILED",
           detail: "model output failed schema validation after 1 retry",
           raw_output: "<attempt 2's message.content, capped at 32000 chars, '...[truncated]' suffix if cut>",
           validation_errors: ["<up to 20 bounded, human-readable jsonschema error strings>"] }
Schema: usage_records.usage_source (EXISTING column, orm.py:97, server_default "frame") gains two new
        values written by this task: "validation_retry" (attempt 1 of a mismatching pair, and the
        terminal attempt of a fully-failed pair) — the retry's own successful attempt keeps the
        default "frame". No migration — usage_source is TEXT, not an enum (mirrors PROJECT.md's
        modality-as-TEXT precedent). No other table/column changes.
```

New module (additive, does NOT edit `response_format_translation.py`):
```python
# apps/gateway/src/gateway/proxy/domain/output_validation.py — NEW, pure, no IO
from __future__ import annotations

import json
from typing import Any, TypedDict

import jsonschema


class ValidationOutcome(TypedDict):
    valid: bool
    parsed: object | None   # the FIRST choice's parsed JSON, for observability only
    errors: list[str]       # bounded to _MAX_ERRORS, human-readable


_MAX_ERRORS = 20
_MAX_RAW_OUTPUT_CHARS = 32_000


def check_schema_well_formed(schema: dict[str, Any]) -> str | None:
    """Meta-validate the caller's JSON Schema BEFORE any upstream call (M3).

    Returns None when well-formed, else a short reason string. Never raises —
    a malformed schema must be a clean pre-flight ERR_INVALID_JSON_SCHEMA, not
    an upstream-call-wasting 500.
    """
    try:
        validator_cls = jsonschema.validators.validator_for(schema)
        validator_cls.check_schema(schema)
    except Exception as exc:
        return str(exc)
    return None


def validate_model_output(schema: dict[str, Any], response_body: dict[str, Any]) -> ValidationOutcome:
    """Validate every choice's message.content against schema (M4).

    Pure -- no IO, no retry logic (the use case owns the retry loop, M5). A
    choice whose content is missing or not valid JSON counts as a validation
    failure, same as a schema mismatch (never silently pass unparseable content).
    """
    errors: list[str] = []
    choices = response_body.get("choices")
    if not isinstance(choices, list) or not choices:
        return ValidationOutcome(valid=False, parsed=None, errors=["ERR_NO_CHOICES"])
    parsed_first: object | None = None
    validator = jsonschema.Draft202012Validator(schema)
    for i, choice in enumerate(choices):
        message = (choice or {}).get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            errors.append(f"choice[{i}]: message.content missing or not a string")
            continue
        try:
            parsed = json.loads(content)
        except ValueError as exc:
            errors.append(f"choice[{i}]: not valid JSON ({exc})")
            continue
        if i == 0:
            parsed_first = parsed
        for err in validator.iter_errors(parsed):
            errors.append(f"choice[{i}]: {err.message}")
            if len(errors) >= _MAX_ERRORS:
                break
    return ValidationOutcome(valid=not errors, parsed=parsed_first, errors=errors[:_MAX_ERRORS])
```
(Syntax-checked: `python3 -m py_compile` passes clean on this exact source.)

Integration sketch (illustrative — NOT a literal diff; the build agent owns the exact insertion):
```
CompletionUseCase.complete(), between the upstream-call try/except block (use_cases.py:1551-1610)
and the cache-store step (use_cases.py:1612-1614):

  1. _validate_flag = self._output_validation_enabled and bool(body.pop("validate_output", False))  # M1, M2
  2. if _validate_flag and status == 200:
       rf = extract_response_format(body)          # re-parse; shape already validated upstream (M3 ran pre-flight)
       outcome = validate_model_output(rf["json_schema"]["schema"], response_body)
       if not outcome["valid"]:
           _fire_record_with_raw(..., model=served_model_id, usage=usage_from_attempt_1,
                                  status=200, usage_source="validation_retry")           # M8
           retry_status, retry_body, retry_served_id = (
               await model_router.complete(body, upstream=upstream) if model_router is not None
               else (*await upstream.complete(body), model_id)
           )                                                                              # M5, M6, M7
           if retry_status == 200:
               outcome = validate_model_output(rf["json_schema"]["schema"], retry_body)
           if retry_status != 200 or not outcome["valid"]:
               _fire_record_with_raw(..., model=retry_served_id, usage=usage_from_attempt_2,
                                      status=retry_status, usage_source="validation_retry")
               raise OUTPUT_SCHEMA_VALIDATION_FAILED.exc(extra={
                   "raw_output": _truncate(retry_body, _MAX_RAW_OUTPUT_CHARS),
                   "validation_errors": outcome["errors"],
               })
           status, response_body, served_model_id = retry_status, retry_body, retry_served_id
       # falls through to the EXISTING cache-store (M9: skipped when _validate_flag) / evaluate_post
       # (M10: runs on the now-validated response_body) / _fire_record_with_raw (M8: usage_source
       # defaults to "frame" for this final successful attempt) — all unchanged code.
```

Settings/config additive extension:
```
apps/gateway/src/gateway/core/config.py Settings:
  output_validation_enabled: bool = Field(default=False)   # GATEWAY_OUTPUT_VALIDATION_ENABLED

apps/gateway/src/gateway/proxy/api/deps.py:
  output_validation_enabled: bool = bool(getattr(_settings, "output_validation_enabled", False)) if _settings else False
  # wired into CompletionUseCase.__init__ as a new keyword-only param, default False (mirrors web_search_enabled)

apps/gateway/src/gateway/core/errors.py (additive, byte-identical for every OTHER caller):
  def problem_response(status: int, code: str, title: str, detail: str | None = None,
                        extra: dict[str, object] | None = None) -> JSONResponse:
      body: dict[str, object] = {"type": "about:blank", "title": title, "status": status, "code": code}
      if detail is not None:
          body["detail"] = detail
      if extra:
          body.update(extra)
      return JSONResponse(status_code=status, content=body, media_type=PROBLEM_CONTENT_TYPE)
  # ProblemError gains an optional `extra: dict[str, object] | None = None` __init__ param,
  # threaded through register_error_handlers' on_problem() to problem_response(). Every existing
  # ProblemError(...) call site is unaffected (new param defaults to None).

apps/gateway/src/gateway/core/error_catalog.py (new entries, alongside RATE_LIMITED/UPSTREAM_UNAVAILABLE):
  OUTPUT_VALIDATION_UNSUPPORTED_ON_STREAM = ErrorSpec(400, "ERR_OUTPUT_VALIDATION_UNSUPPORTED_ON_STREAM", "...")
  OUTPUT_VALIDATION_REQUIRES_JSON_SCHEMA  = ErrorSpec(400, "ERR_OUTPUT_VALIDATION_REQUIRES_JSON_SCHEMA", "...")
  OUTPUT_SCHEMA_VALIDATION_FAILED         = ErrorSpec(422, "ERR_OUTPUT_SCHEMA_VALIDATION_FAILED", "...")
  # ERR_INVALID_JSON_SCHEMA is REUSED — response_format_translation.py already raises it as a
  # ValueError; this task's pre-flight check (M3) raises the SAME code via the existing
  # ValueError->400 mapping site (traced at BUILD time, not re-derived here).

apps/gateway/pyproject.toml: + "jsonschema>=4.23,<5" (pure-Python, BSD-3, no native/network deps)
```

Glossary deltas:
- **Output validation**: an opt-in, per-request check (gated by `GATEWAY_OUTPUT_VALIDATION_ENABLED` + the request's `validate_output:true`) that a `response_format.json_schema` completion's `message.content` validates against the caller's JSON Schema, with exactly one bounded retry (a second real upstream call) on mismatch before a structured `ERR_OUTPUT_SCHEMA_VALIDATION_FAILED` (422). SUPERSEDES the v11 translate-don't-enforce pin, opt-in only.
- **`usage_source="validation_retry"`**: a `usage_records.usage_source` value marking a real, billed upstream call consumed by the bounded-retry loop — either a mismatching first attempt (superseded by a successful retry) or the terminal attempt of a fully-failed validation pair. Distinct from `"frame"` (normal single-attempt billing).

Status: DRAFT — awaiting human freeze
Reported: no — this is the design draft; the freeze report renders when Tin reviews §3
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

## Design self-score

| Dimension | Score | Why |
|---|---|---|
| Completeness | 0.93 | Every Must has a Reject or scenario; every Reject has an error code + status + scenario; the retry/billing/cache/streaming/guardrail-ordering interactions are each traced to a concrete anchor, not asserted from memory. Residual gap: I did not trace every downstream `usage_records` reader (billing dashboards, margin reports) to confirm two-row tolerance — named explicitly in the ⚠ flag rather than papered over. |
| Clarity | 0.93 | Each Must states its rationale inline (not just the rule); the integration sketch shows exactly where in `use_cases.py` the new step lands relative to existing line anchors, not a vague "somewhere in complete()". |
| Practicality | 0.92 | Reuses four existing seams wholesale (the `web_search` strip-flag pattern, `usage_source` typed extra, the `x_cache=bypass` mechanism, and `ErrorSpec` reuse for `ERR_INVALID_JSON_SCHEMA`) rather than inventing new machinery; the one new dependency (`jsonschema`) is small, pure-Python, and explicitly flagged for sign-off rather than silently added. |
| Optimization | 0.91 | Bounded to exactly one retry (never unbounded); pre-flight schema meta-validation (M3) avoids wasting a paid upstream call on a broken schema; cache-bypass on opt-in trades a cache-hit-rate cost for correctness rather than leaving a silent gap. The ~2x latency tradeoff (R5) is named, not hidden. |
| Edge cases | 0.91 | Covers: off-by-default, flag-on-but-not-opted-in, malformed schema, unparseable content, n>1 partial-choice mismatch, circuit-open-during-retry, streaming rejection, cache interaction, guardrail-masking ordering. Not covered (deliberately out of scope, named in R2): fixing the pre-existing `_CACHE_KEY_FIELDS` gap itself — that belongs to a different frozen contract. |
| Self-evaluation | 0.90 | The two lowest-confidence points (two-usage-records-per-request invariant exception; 422 vs 502) are surfaced as ranked, named freeze questions with explicit options and a recommendation each — not resolved by silent authorial choice. |

All ≥ 0.90 — no refinement pass required before returning.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: <e.g. 90%>
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_<scenario>: arrange <Given> / act <When> / assert <Then> + assert <unchanged> · covers: <M#, R:code — optional>
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/domain/output_validation.py` (new) `apps/gateway/src/gateway/proxy/application/use_cases.py` `apps/gateway/src/gateway/proxy/api/deps.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/core/errors.py` `apps/gateway/src/gateway/core/error_catalog.py` `apps/gateway/pyproject.toml` `apps/gateway/tests/output_schema_validation` (new dir)   <!-- every token has "/" = project root, per the scope-token grammar; response_format_translation.py is DELIBERATELY excluded (SUPERSESSION rule — not edited by this task) -->
Strategy (ordered batches): 1. add `jsonschema` to pyproject.toml + lockfile, confirm `check_schema`/`Draft202012Validator` import cleanly. 2. write `output_validation.py` (pure, no IO) + its own unit tests (schema meta-validation, multi-choice validation, malformed-JSON, missing-content) BEFORE touching use_cases.py — this module has zero dependencies on the use case and can be red/green in isolation. 3. additive-extend `core/errors.py` (`extra` param) + `core/error_catalog.py` (3 new ErrorSpecs, `ERR_INVALID_JSON_SCHEMA` reused) — verify every EXISTING `ProblemError`/`problem_response` call site still compiles/passes untouched (this is the one shared-file edit with the widest blast radius; treat it as its own reviewed sub-step). 4. add `output_validation_enabled` to `config.py` + wire through `deps.py` into `CompletionUseCase.__init__` (mirrors `web_search_enabled` verbatim — diff the two wiring paths side by side). 5. insert the M1-M13 integration into `CompletionUseCase.complete()` at the sketched insertion point (between the upstream-call try/except and the cache-store step); do NOT touch `CompletionUseCase.stream()` beyond the M11 pre-flight reject. 6. wire the cache-bypass (M9) by threading `_validate_flag` into the existing bypass boolean the way `x_cache == "bypass"` already gates the store block. 7. tests last-mile: run the full scenario list from §2 as the red suite before any of steps 2-6's implementation lands.

Persona (required): protocol-translation-engineer (`.add/personas/protocol-translation-engineer.md`) — this task is depth on the same v9-v11 ChatTranslator/response_format seam that persona already owns (multi-provider shape-fidelity + byte-identical-passthrough verification is exactly the M1/M2/M11 discipline this task needs); pair with billing-precision-engineer's lens (not a full persona swap) specifically for M8's two-usage-record write, since that is a money-correctness concern the translation persona alone would not naturally scrutinize.
Spawn isolation (default): worktree — this task edits shared cross-feature files (`core/errors.py`, `core/error_catalog.py`, `config.py`) that other in-flight milestone tasks may also touch; isolate to avoid a shared-tree collision, net-diff-merge back per the worktree-agent-stale-base lesson.
Known-problem fixes: (a) FastAPI foot-gun from the S1 sibling lesson — any new operator-only gate must use `Depends(require_superadmin)`, never bare (not directly applicable here, no new auth surface, but re-check if the freeze adds one). (b) raw SQLAlchemy UPDATE lesson (chat-conversation-mgmt) does not apply — no UPDATE path here, only INSERT via the existing `usage_recorder`. (c) do not let the retry's `_fire_record_with_raw` calls race the ORIGINAL request's own `finally` block credential-contextvar reset (use_cases.py:1770-1774) — both calls must fire from inside the SAME `try` before that `finally` runs, not as a detached fire-and-forget task (unlike most other `_fire_record*` calls in this file, these two must be awaited in-line since the second one gates whether a 200 or a 422 is ultimately returned).
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): the bounded retry (M5) and its billing (M8) must be a single linear `await` chain inside `complete()`'s existing `try` block — never a background task — so that a 422 terminal failure can only be returned AFTER both usage records are durably fired (usage recording is fire-and-forget at the Redis-stream level per the existing `_fire_record_with_raw` design, but the CALL to fire it must not be skipped or reordered relative to the retry decision).
Code lives in: `apps/gateway/src/gateway/`
Constraints: do NOT change any test or the contract; allow-list packages only (jsonschema, flagged above, needs freeze sign-off); ask if unclear; do NOT edit `apps/gateway/src/gateway/proxy/domain/response_format_translation.py` (SUPERSESSION rule — frozen v11 file).

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token with "/" = project root · a bare name = sibling of the previous token's dir · a DIRECTORY token covers its whole subtree (diverges from §4's non-recursive counting) · outside-root resolutions drop fail-closed · absent line = UNDECLARED (grandfathered, never retro-red) · enforcement live: a completing verify gate refuses an out-of-scope build (scope_violation → self-heal); check surfaces it. EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

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
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [ ] <observable outcome a correct build must produce> — confirmed by <how / where>
- [ ] <another observable outcome> — confirmed by <evidence seen>

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [ ] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by <how / where>
- [ ] any anchor that moved/renamed since Ground SHA is named here, not left silent

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: <agent-id | self>
1. Security: <CLEAR | HARD-STOP: finding>
2. Concurrency: <CLEAR | RESIDUE: finding>
3. Architecture: <CLEAR | RESIDUE: finding>
Verdict: <PASS | HARD-STOP>
Residue: <none | summary>
Binding: <yes — mechanical | advisory — <sensitivity>>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- Security is ALWAYS HARD-STOP; record exactly one outcome — no silent pass. The Advisor 3-lens and Refute-read verdicts are audit-measured (`advisor_verdict_unrecorded` · `refute_unrecorded`), never engine-blocked; a human spot-audit backstops anything unrecorded. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
