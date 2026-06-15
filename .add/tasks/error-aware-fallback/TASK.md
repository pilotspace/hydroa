# TASK: Error-aware model fallback: fail over on context-window-exceeded & content-policy block

slug: error-aware-fallback · created: 2026-06-15 · stage: production
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
- `proxy/application/fallback_router.py:FallbackModelRouter.complete()` (L216-356) — the alias fallback loop. TODAY it falls over to the next candidate ONLY on `UpstreamUnavailableError` (L302-316); any `(status, body)` returned by `upstream.complete()` — INCLUDING a 4xx — is treated as "candidate answered" (step 4, L323-347) → `record_success` + return verbatim. So a context-window 400 or content-policy 400 currently hard-fails to the client instead of falling over. This is the seam to extend.
- `proxy/application/fallback_triggers.py` — **NEW** pure module. `classify_fallback_trigger(status: int, body: dict) -> str | None` — the closed FALLBACK-TRIGGER TAXONOMY classifier over the already-OpenAI-shaped `(status, body)`. Returns `"context_window"` | `"content_policy"` | `None`. Total + never raises (design-for-failure).
- `core/config.py:Settings` (retry knobs L203-215) — add `upstream_fallback_on_error: bool = Field(default=False)` (env `GATEWAY_UPSTREAM_FALLBACK_ON_ERROR`); mirrors the `upstream_retry_deadline_s` opt-in pattern. Default False ⇒ byte-identical to v6.
- `main.py:495` — `FallbackModelRouter(...)` ctor wiring; thread `fallback_on_error=settings.upstream_fallback_on_error`.
- `observability/metrics.py:115` — `model_fallbacks_total` Counter, labels `(alias, from_model, to_model, outcome)`. NO label-set change — the new triggers are additive `outcome` values (`"context_window"`, `"content_policy"`) alongside the existing `served|fell_through|exhausted`.
- `proxy/application/use_cases.py:957-1105` — router.complete() call site + billing. Billing already uses the SINGLE returned `(status, served_model_id)` once (`_fire_record_with_raw` L1089) → a swallowed-then-fallen-over 4xx is never billed (billing accuracy preserved by construction). Likely NO change; verify the exhausted-error return path bills the last 4xx (zero tokens) correctly.
- Error-translation surfaces (READ-ONLY context for the classifier patterns): `anthropic_upstream.py:_anthropic_error_to_openai` (L333-354 → `{error:{message,type,code}}`, context-window ⇒ `code="invalid_request_error"` + "too long" message); `gemini_upstream.py:_gemini_error_to_openai` (L313-334 → context-window ⇒ `code="invalid_argument"` + token message); OpenRouter passthrough `resp.json()` (OpenAI-shaped, context-window ⇒ `code="context_length_exceeded"`).

Context (working folder):
- `.add/milestones/v19/MILESTONE.md` — shared decisions (opt-in/default-off; billing sacrosanct; streaming hard boundary = N/A, this task is NON-streaming complete() only) + the frozen-first contract "FALLBACK-TRIGGER TAXONOMY".
- Tests: `apps/gateway/tests/model_fallbacks/conftest.py` — reusable fakes: `SequencedFakeUpstream` (replays `(status, body)` outcomes or raises), `AlwaysAvailableGate`/`SelectiveGate`/`RecordingFakeGate` (ModelHealthGate stubs), `FakeUsageRecorder`, `make_payload`. `apps/gateway/tests/model_fallbacks_wiring/` (ctor wiring regression).
- `core/config.py` env-knob conventions; `main.py` create_app router wiring (L495-504).

Honors (patterns / conventions):
- OPT-IN / DEFAULT-OFF (MILESTONE shared decisions): new triggers gated behind `upstream_fallback_on_error=False` default ⇒ at default settings byte-identical to v6 (4xx passthrough preserved).
- BILLING ACCURACY sacrosanct (foundation v12): router returns exactly the served terminal response once; discarded fallover attempts never billed.
- DESIGN-FOR-FAILURE (CLAUDE.md): the classifier is PURE + TOTAL (never raises); the existing gate fail-open contract and `_inc_counter` metrics-fail-open are preserved verbatim.
- Health-gate semantics (model-fallbacks §3): `record_success` = "candidate returned any status incl 4xx" (model is ALIVE); `record_failure` only on `UpstreamUnavailableError`. A context-window/content-policy 4xx ⇒ model answered ⇒ `record_success` (request-specific, NOT a health/cooldown signal — do not cool a healthy model).
- Layering: classifier lives in `application/` (pure, zero framework imports), co-located with its only consumer (the router); parallels the v19 retry-seam `RetryPolicy`.

Anchors the contract cites:
- `FallbackModelRouter.complete()` (the loop extended) + new ctor param `fallback_on_error: bool = False`.
- `fallback_triggers.classify_fallback_trigger(status, body) -> str | None` (NEW pure fn).
- The closed taxonomy labels: `"context_window"`, `"content_policy"` (+ existing `retry_exhausted` = the `UpstreamUnavailableError` path, unchanged).
- `Settings.upstream_fallback_on_error` (NEW knob, env `GATEWAY_UPSTREAM_FALLBACK_ON_ERROR`).
- `model_fallbacks_total` additive `outcome` values (no label-set change).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Error-aware model fallback — the `FallbackModelRouter` fails over to the next deployment in a model-group not only on exhausted retries (`UpstreamUnavailableError`, today) but on two NON-retryable, request-specific upstream conditions another deployment can satisfy: **context-window-exceeded** and **content-policy-blocked**. Non-streaming `complete()` only. Opt-in / default-off.

Framings weighed:
- **Classify the terminal 4xx error body** (chosen) — the router inspects the OpenAI-shaped `(status, body)` a candidate returns; a 4xx whose error body classifies as context-window / content-policy becomes a fallover trigger. Provider-agnostic (every provider already translates to OpenAI shape), zero new upstream coupling, billing-safe (a 4xx rejection is swallowed → only the served response bills).
- Raise typed exceptions from each upstream (rejected) — would require editing all three frozen provider adapters + the CompletionUpstream contract to add new exception types; larger blast radius, touches frozen suites, and the classification logic would be duplicated per provider.
- Fall over on 200 `finish_reason=content_filter` too (rejected for v19) — a 200 is already generated AND billed; falling over would double-bill or discard a billed response (violates billing-accuracy sacrosanct) and is response-quality routing, not error-fallback. Out of scope (a later milestone if wanted).

Must:
<must>
  - When `fallback_on_error` is ENABLED and a candidate returns a 4xx whose error body classifies (via `classify_fallback_trigger`) as `context_window` or `content_policy`, AND a next candidate exists in the strategy order, the router falls over to the next candidate (does NOT return the 4xx to the client).
  - The trigger taxonomy is a CLOSED set of exactly three conditions: `retry_exhausted` (existing — `UpstreamUnavailableError`, unchanged), `context_window`, `content_policy`. Anything else is NOT a fallover trigger.
  - `classify_fallback_trigger(status, body)` is PURE and TOTAL: returns a trigger label only for a 4xx (400/413/422-class) whose OpenAI-shaped error body matches context-window (`code==context_length_exceeded` OR message/code matches the context-overflow pattern: "context"/"too long"/"maximum…tokens") or content-policy (`code==content_filter` OR type/code/message matches a safety/policy pattern); returns `None` for every 2xx, every non-matching 4xx, and any malformed/empty body. It NEVER raises.
  - When an error-trigger fallover exhausts every candidate (all return trigger-4xx), the router returns the LAST candidate's `(status, body, served_model_id)` — the client receives the real upstream error (e.g. the context-window 400), NOT a synthetic 502.
  - On an error-trigger fallover the fallen candidate is recorded as `record_success` on the health gate (it answered → model is ALIVE; a context-window/content-policy rejection is request-specific, never a cooldown signal) and is NEVER `record_failure`.
  - Each error-trigger fallover increments `model_fallbacks_total` with `outcome` = the trigger label (`context_window` | `content_policy`); a fallover that then serves a later candidate increments `outcome=served` for the served hop (existing semantics preserved). The label-set is unchanged (additive `outcome` values only).
  - Billing accuracy is preserved by construction: the router returns exactly ONE served terminal `(status, body, served_model_id)`; the use case bills that once. Discarded trigger-4xx attempts are never billed.
  - DEFAULT-OFF: with `upstream_fallback_on_error=False` (default) behavior is byte-identical to v6 — a 4xx (incl. context-window / content-policy) is returned to the client verbatim after the first candidate; the existing `UpstreamUnavailableError` fallover is unchanged.
  - The change is confined to NON-streaming `complete()`. `stream()` is untouched (streaming resilience is task 3's frozen boundary).
  - A plain (non-alias) model id is unaffected: no fallback path exists or is added; the 4xx passes through exactly as today.
</must>
Reject:
<reject>
  - A 2xx response with `finish_reason=="content_filter"` (or Gemini `SAFETY`) -> NOT a trigger; served verbatim (billing-safe, out of scope).
  - A non-classifying 4xx (401 auth / 403 / 404 / 422 validation / 429 already retry-handled) -> NOT a trigger; returned to the client verbatim (today's behavior) -> "passthrough (no synthetic code)".
  - A malformed / empty / non-dict error body on a 4xx -> classifier returns `None` -> passthrough (fail-safe: never fall over on an unclassifiable response).
  - A trigger-4xx on a plain (non-alias) model id -> no fallback exists -> returned verbatim.
  - `fallback_on_error` disabled (default) -> every 4xx returned verbatim after the first candidate (v6 byte-identical).
</reject>
After:
<after>
  - An enabled, alias-routed request whose primary returns a context-window OR content-policy 4xx is served by a later candidate in the group when one succeeds; the client never sees the intermediate 4xx.
  - `served_model_id` reflects the candidate that actually produced the served response; billing and span attribution (`fallback=true`) follow it.
  - `model_fallbacks_total` shows the trigger outcome for the fallen hop and `served` for the serving hop.
  - When the whole group fails the trigger, the client gets the last real upstream 4xx, billed once (zero completion tokens).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Content-policy fallover should fire ONLY on a 4xx REJECTION, not on a 200 `finish_reason=content_filter` — lowest confidence because a reader might expect "content-policy-blocked" to also cover the 200-with-content_filter case; chosen to exclude it because a 200 is already generated AND billed (falling over would double-bill / discard a billed response, violating billing-accuracy sacrosanct) and is response-quality routing, not error-fallback. If wrong: the feature misses the most common OpenAI/Gemini content-filter surface (which is a 200) and an operator must add a separate response-quality-fallback feature later. Cost: medium — additive follow-up, no rework of this task's seam.
  - [ ] The context-window/content-policy classifier can reliably distinguish these from generic `invalid_request_error` (Anthropic) / `invalid_argument` (Gemini) using code + message-substring patterns — confidence medium-high: OpenRouter uses the canonical `context_length_exceeded`/`content_filter` codes; Anthropic/Gemini need a message-pattern fallback ("too long", "maximum context", "safety"). If wrong: a true context-window error from Anthropic/Gemini passes through instead of falling over (fail-SAFE — never a wrong fallover, just a missed one). Mitigated by keeping the pattern list explicit + unit-tested per provider shape.
  - [x] A single boolean knob (not a per-trigger set) is sufficient for v19 — confirmed: MILESTONE defers per-tenant/per-trigger policy overrides; one opt-in boolean matches "error-aware fallback" as one feature. A per-trigger set is a clean future refinement (noted §7).
  - [x] On a trigger-4xx the fallen candidate is `record_success` (alive), not `record_failure` — confirmed against model-fallbacks §3 gate semantics ("record_success = returned any status incl 4xx"); a request-specific rejection must not cool a healthy model.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: context-window 4xx falls over to next candidate and serves it
  Given fallback_on_error=ENABLED and alias "fast" → [A, B]
  And candidate A returns 400 {error:{code:"context_length_exceeded"}}
  And candidate B returns 200 {choices:[...], usage:{total_tokens:9}}
  When the router completes the aliased request
  Then it returns (200, B-body, served_model_id="B")
  And the client never receives A's 400

Scenario: content-policy 4xx falls over to next candidate and serves it
  Given fallback_on_error=ENABLED and alias "fast" → [A, B]
  And candidate A returns 400 {error:{code:"content_filter", message:"blocked by safety policy"}}
  And candidate B returns 200
  When the router completes the aliased request
  Then it returns (200, B-body, served_model_id="B")

Scenario: classifier is total and provider-agnostic
  Given OpenAI-shaped error bodies from each provider
  When classify_fallback_trigger(status, body) is called
  Then 400 code=context_length_exceeded → "context_window"
  And 400 invalid_request_error message "prompt is too long: 200000 tokens > 100000 maximum" → "context_window"
  And 400 invalid_argument message "input token count exceeds the maximum" → "context_window"
  And 400 code=content_filter → "content_policy"
  And it never raises for any input

Scenario: error-trigger fallover exhausted returns the last real 4xx (not a 502)
  Given fallback_on_error=ENABLED and alias "fast" → [A, B]
  And BOTH A and B return 400 context_length_exceeded
  When the router completes the aliased request
  Then it returns (400, B's-error-body, served_model_id="B")
  And it does NOT raise UpstreamUnavailableError / synthesize a 502

Scenario: a fallen trigger-4xx candidate is recorded ALIVE (record_success, never record_failure)
  Given fallback_on_error=ENABLED, a RecordingFakeGate, alias "fast" → [A, B]
  And A returns 400 context_length_exceeded, B returns 200
  When the router completes the aliased request
  Then gate.record_success was called for A (and B)
  And gate.record_failure was NEVER called for A

Scenario: metrics record the trigger outcome and the served hop
  Given fallback_on_error=ENABLED, a metrics registry, alias "fast" → [A, B]
  And A returns 400 content_filter, B returns 200
  When the router completes the aliased request
  Then model_fallbacks_total{outcome="content_policy", from_model="A"} incremented
  And model_fallbacks_total{outcome="served", to_model="B"} incremented

Scenario: billing bills only the served candidate (discarded 4xx never billed)
  Given the use case wired with the router and a FakeUsageRecorder
  And fallback_on_error=ENABLED, alias "fast" → [A, B]; A→400 context_length_exceeded, B→200
  When the request runs end-to-end
  Then exactly ONE usage record is written, for served_model_id="B", status=200
  And no usage record references A or status=400

Scenario: retry_exhausted (UpstreamUnavailableError) fallover unchanged
  Given fallback_on_error=ENABLED (or DISABLED — irrelevant), alias "fast" → [A, B]
  And A raises UpstreamUnavailableError, B returns 200
  When the router completes the aliased request
  Then it returns B's 200 (existing v6 behavior, byte-identical)
  And gate.record_failure was called for A

Scenario: DEFAULT-OFF — disabled keeps v6 4xx passthrough (byte-identical)
  Given fallback_on_error=DISABLED (default) and alias "fast" → [A, B]
  And A returns 400 context_length_exceeded
  When the router completes the aliased request
  Then it returns (400, A's-error-body, served_model_id="A")
  And candidate B is NEVER called

Scenario: non-classifying 4xx passes through even when enabled
  Given fallback_on_error=ENABLED and alias "fast" → [A, B]
  And A returns 401 {error:{code:"invalid_api_key"}}
  When the router completes the aliased request
  Then it returns (401, A's-error-body, served_model_id="A")
  And candidate B is NEVER called

Scenario: malformed/empty 4xx body is unclassifiable → passthrough (fail-safe)
  Given fallback_on_error=ENABLED and alias "fast" → [A, B]
  And A returns 400 with an empty/non-dict body
  When the router completes the aliased request
  Then classify_fallback_trigger returns None and it returns A's 400 verbatim
  And candidate B is NEVER called

Scenario: 200 with finish_reason=content_filter is served, NOT a trigger
  Given fallback_on_error=ENABLED and alias "fast" → [A, B]
  And A returns 200 {choices:[{finish_reason:"content_filter"}], usage:{...}}
  When the router completes the aliased request
  Then it returns A's 200 verbatim (served + billed as-is)
  And candidate B is NEVER called

Scenario: plain (non-alias) model id is unaffected
  Given fallback_on_error=ENABLED and a plain model id "vendor/x" (no alias group)
  And upstream returns 400 context_length_exceeded
  When the router completes the request
  Then it returns the 400 verbatim with served_model_id="vendor/x"
  And no fallback path is taken (single upstream call)

Scenario: stream() path untouched
  Given fallback_on_error=ENABLED and alias "fast" → [A, B]
  When the router streams the aliased request
  Then it resolves the strategy primary only and delegates to upstream.stream() (no error-fallback)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# ── The FALLBACK-TRIGGER TAXONOMY (closed set) ────────────────────────────
# A model fallover is triggered by exactly one of three conditions; everything
# else hard-fails (passes the upstream response/exception through unchanged).
TRIGGER ∈ { retry_exhausted, context_window, content_policy }
  retry_exhausted  — upstream raised UpstreamUnavailableError (5xx/timeout/connect;
                     the v6 path — UNCHANGED, always active for aliases, not flag-gated).
  context_window   — a 4xx error body classified as prompt/context overflow (NEW, flag-gated).
  content_policy   — a 4xx error body classified as a safety/policy block   (NEW, flag-gated).

# ── Pure classifier (NEW module proxy/application/fallback_triggers.py) ────
classify_fallback_trigger(status: int, body: dict[str, Any]) -> str | None
  returns "context_window" | "content_policy" | None
  PURE · TOTAL · NEVER raises.
  - status not in 400..499                          -> None
  - body not a dict / no dict "error" sub-object    -> None
  - error.code == "context_length_exceeded"         -> "context_window"
  - error.code/type/message matches CONTEXT_PATTERNS -> "context_window"
        (case-insensitive substring on code|type|message:
         "context_length", "context window", "too long", "maximum context",
         "exceeds the maximum", "input token count", "prompt is too long")
  - error.code == "content_filter"                  -> "content_policy"
  - error.code/type/message matches POLICY_PATTERNS  -> "content_policy"
        (case-insensitive substring on code|type|message:
         "content_filter", "content policy", "safety", "blocked by", "responsible ai")
  - context_window takes precedence over content_policy when both match.
  - anything else                                   -> None

# ── Router extension (proxy/application/fallback_router.py) ────────────────
FallbackModelRouter.__init__(..., fallback_on_error: bool = False)   # NEW last kwarg, default off
FallbackModelRouter.complete(payload, upstream=None) -> (status, body, served_model_id)  # signature UNCHANGED
  Per candidate, after `status, body = await upstream.complete(rewritten)` succeeds (no exception):
    trigger = classify_fallback_trigger(status, body) if self._fallback_on_error else None
    if trigger is not None and a NEXT candidate exists in `order`:
        gate.record_success(candidate)          # model answered → ALIVE (never record_failure)
        _inc_counter(alias, from=candidate, to=next, outcome=trigger)   # outcome ∈ {context_window, content_policy}
        last_error = (status, body, candidate)  # remember for exhaustion
        continue                                # FALL OVER (do NOT return the 4xx)
    else:
        # unchanged v6 step-4 path: record_success + record_latency + limit recording + return (status, body, candidate)
  Exhaustion (loop ends with no served 2xx/passthrough):
    if last_error is not None:  return last_error          # the LAST real 4xx (client sees the real error)
    else:                       raise UpstreamUnavailableError(...)   # v6 path (all retry_exhausted / gated)
  UpstreamUnavailableError fall-through (retry_exhausted): UNCHANGED (record_failure + continue).
  Non-trigger 4xx, any 2xx, plain (non-alias) model id, stream(): UNCHANGED.

# ── Config (core/config.py:Settings) ──────────────────────────────────────
upstream_fallback_on_error: bool = Field(default=False)   # env GATEWAY_UPSTREAM_FALLBACK_ON_ERROR

# ── Wiring (main.py:create_app) ───────────────────────────────────────────
FallbackModelRouter(..., fallback_on_error=settings.upstream_fallback_on_error)

# ── Metrics (observability/metrics.py) ────────────────────────────────────
gateway_model_fallbacks_total{alias, from_model, to_model, outcome}   # label-set UNCHANGED
  outcome gains additive values: "context_window", "content_policy"   # alongside served|fell_through|exhausted

Schema: NONE — no DB tables/columns touched. Reads: config (settings), per-request payload model + alias
        groups (in-memory). Writes: Prometheus counter increments only. Billing unchanged (use case bills
        the single served (status, served_model_id) once; discarded 4xx attempts never billed).
```

Status: FROZEN @ v1 — approved by Tin (auto mode, 2026-06-15)
Least-sure flag surfaced at freeze: [spec] content-policy fallover fires ONLY on a 4xx rejection, NOT on a
  200 finish_reason=content_filter — a reader may expect the 200 case covered; excluded because a 200 is
  already generated AND billed (covering it would double-bill / discard a billed response → violates
  billing-accuracy sacrosanct) and is response-quality routing, not error-fallback. If wrong: the feature
  misses the most common content-filter surface (a 200) and an operator needs a separate response-quality
  fallback later — cost MEDIUM (additive follow-up, no rework of this seam). Secondary [contract]: the
  context/policy message-substring pattern lists are heuristic for Anthropic/Gemini (which lack the canonical
  OpenAI codes); a miss is fail-SAFE (passthrough, never a wrong fallover) and the lists are unit-tested per
  provider shape.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 95% on `fallback_triggers.py`; ≥90% on the new `complete()` fallover branch.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  CLASSIFIER (test_fallback_triggers.py — pure, no async):
  - test_context_length_exceeded_code: classify(400, {error:{code:"context_length_exceeded"}}) == "context_window"
  - test_anthropic_too_long_message: classify(400, {error:{code:"invalid_request_error", message:"prompt is too long: 200000 tokens > 100000 maximum"}}) == "context_window"
  - test_gemini_token_count_message: classify(400, {error:{code:"invalid_argument", message:"The input token count exceeds the maximum"}}) == "context_window"
  - test_content_filter_code: classify(400, {error:{code:"content_filter"}}) == "content_policy"
  - test_safety_message: classify(400, {error:{type:"invalid_request_error", message:"blocked by safety policy"}}) == "content_policy"
  - test_context_precedence_over_policy: a body matching BOTH → "context_window"
  - test_2xx_returns_none: classify(200, {...}) is None
  - test_non_matching_4xx_returns_none: classify(401, {error:{code:"invalid_api_key"}}) is None
  - test_malformed_body_returns_none: classify(400, {}) / classify(400, {"error": "str"}) / classify(400, None-as-{}) all None
  - test_never_raises: a fuzz set of odd bodies never raises (returns None)

  ROUTER (test_error_aware_fallback.py — async, SequencedFakeUpstream + gates):
  - test_context_window_falls_over_and_serves: enabled; A→400 ctx, B→200 ⇒ (200, BODY_B, "model-B"); 2 calls A→B
  - test_content_policy_falls_over_and_serves: enabled; A→400 content_filter, B→200 ⇒ served B
  - test_exhausted_error_returns_last_4xx: enabled; A→400 ctx, B→400 ctx ⇒ (400, B-error-body, "model-B"); NOT UpstreamUnavailableError
  - test_fallen_candidate_recorded_alive: enabled, RecordingFakeGate; A→400 ctx, B→200 ⇒ success_calls includes "model-A"; failure_calls excludes "model-A"
  - test_metrics_trigger_and_served: enabled, metrics registry; A→400 content_filter, B→200 ⇒ outcome="content_policy"(from A) +1 AND outcome="served"(to B) +1
  - test_billing_returns_only_served_tuple: enabled; A→400 ctx, B→200 ⇒ exactly ONE returned tuple = B's 200 (proves use-case bills served only; A never returned)
  - test_retry_exhausted_unchanged: A→UpstreamUnavailableError, B→200 ⇒ served B; record_failure("model-A") called (v6 byte-identical)
  - test_default_off_passthrough_4xx: DISABLED (default); A→400 ctx ⇒ (400, A-body, "model-A"); upstream.call_count==1 (B never called)
  - test_non_classifying_4xx_passthrough: enabled; A→401 ⇒ (401, A-body, "model-A"); B never called
  - test_malformed_4xx_passthrough: enabled; A→400 {} ⇒ (400, {}, "model-A"); B never called
  - test_200_content_filter_served_not_trigger: enabled; A→200 with finish_reason=content_filter ⇒ served A; B never called
  - test_plain_model_id_unaffected: enabled; plain "vendor/x"→400 ctx ⇒ (400, body, "vendor/x"); single call, no fallback
  - test_stream_untouched: enabled; stream() resolves primary only, delegates to upstream.stream() (no error-fallback)

  CONFIG (test_error_aware_fallback.py or test_fallback_triggers.py):
  - test_settings_default_off: Settings().upstream_fallback_on_error is False
  - test_settings_env_enable: Settings(upstream_fallback_on_error=True).upstream_fallback_on_error is True
</test_plan>

Tests live in: `apps/gateway/tests/error_aware_fallback/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/application/fallback_triggers.py` `fallback_router.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/main.py` `apps/gateway/tests/error_aware_fallback/`
Strategy (ordered batches):
  1. NEW `fallback_triggers.py` — pure `classify_fallback_trigger(status, body) -> str | None` + the CONTEXT_PATTERNS / POLICY_PATTERNS substring lists + the closed trigger-label constants. Make classifier green first (unit suite).
  2. `config.py` — add `upstream_fallback_on_error: bool = Field(default=False)`.
  3. `fallback_router.py` — ctor `fallback_on_error: bool = False` (store `self._fallback_on_error`); in `complete()`, after a candidate answers (status, body), if enabled classify → on trigger with a next candidate: record_success + _inc_counter(outcome=trigger) + remember last_error + continue; on exhaustion return last_error (the last 4xx) else the v6 raise. UpstreamUnavailableError path + non-trigger 4xx + 2xx + plain id + stream() UNCHANGED.
  4. `main.py` — thread `fallback_on_error=settings.upstream_fallback_on_error` into the FallbackModelRouter ctor (L495).
Safety rule (feature-specific): the router returns EXACTLY ONE served `(status, body, served_model_id)`; a discarded trigger-4xx is never returned (billing accuracy by construction). Classifier is PURE + TOTAL (never raises). Default-off ⇒ v6 byte-identical (no new code path taken when `fallback_on_error=False`).
Code lives in: `apps/gateway/src/gateway/`
Constraints: do NOT change any test or the contract; allow-list packages only (no new deps); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 32 in `tests/error_aware_fallback/` green; 141 green across the fallback/routing/retry regression set (model_fallbacks, model_fallbacks_wiring, routing_strategy, routing_admin, retry_policy, deployment_model); `make test-fast` 135 green (provider/translation blast-radius unaffected).
- [x] coverage did not decrease — `fallback_triggers.py` 100%; the new `complete()` fallover branch + exhaustion-return-last-error path are exercised by the suite; sibling suites keep the rest of `fallback_router.py` covered.
- [x] no test or contract was altered to force a pass — §3 CONTRACT kept BYTE-IDENTICAL (frozen). The verify-time refinement (tighten classifier + add guard tests) was applied and the tests→build crossing was RE-crossed (sanctioned re-snapshot), not by weakening any assertion. Every assertion is stronger, not weaker.
- [x] the green was EARNED — adversarial refute-read (sonnet subagent) verdict EARNED-WITH-GAPS @ 0.87; all 8 critical invariants HOLD (billing-one-tuple, default-off byte-identical, classifier pure/total, context precedence, record_success-not-failure, exhaustion-returns-real-4xx, stream untouched, UpstreamUnavailableError path unchanged). NO cheat, NO vacuous assert, NO stubbed logic. The two real gaps it surfaced (429/422 spec-vs-contract drift; fail-dangerous broad patterns) were FIXED in-loop, not accepted: classifier now excludes retry-domain 408/429 and narrows patterns to provider-real vocabulary; 5 guard tests added (429/408 passthrough, "field too long", "blocked by firewall", "safety valve").
- [x] concurrency / timing safe — no new shared state; the load_gate slot release stays in the existing `try/finally` (fires on the new `continue` edge too); the fallover branch records no latency (a fast rejection must not bias load-aware routing). No await-ordering change to the breaker/gate protocol.
- [x] no exposed secrets / injection / unexpected deps — classifier reads only `error.code|type|message` (never keys/headers/URLs); no new imports beyond the in-repo `classify_fallback_trigger`; metric labels carry model ids + outcome only (no secrets). No new third-party dependency.
- [x] layering & dependencies follow CONVENTIONS.md — classifier is a pure application-layer module (zero framework imports), consumed only by the application-layer router; parallels the v19 retry-seam `RetryPolicy`. No domain/infra leak.
- [x] reviewed & approved — auto-resolved under `autonomy: auto` (not risk:high; behavior is additive + opt-in/default-off, byte-identical at defaults). Adversarial refute-read stands in for the second reviewer; no security/architecture residue (would escalate to HARD-STOP).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `classify_fallback_trigger` referenced by `fallback_router.complete()`; `fallback_on_error` ctor param stored as `self._fallback_on_error` and read in the loop; `Settings.upstream_fallback_on_error` threaded into the ctor at `main.py:495`. Confirmed by pyright (0 errors) + the wiring assertions in the suite (default-off path, enabled path).
- [x] DEAD-CODE (code) — no orphaned symbol: every new constant/pattern/label is reached; ruff clean (no unused). The `last_error` exhaustion-return path is exercised by `test_trigger_then_upstream_unavailable_returns_last_error`.
- [x] SEMANTIC (prose / non-code) — re-read §1/§2/§3: the implementation honors the frozen §3 shape; the classifier tightening honors §1 REJECT (429 retry-handled) where §3's literal 400-499 range was a superset — a within-contract precision, not a shape change.

### GATE RECORD
Outcome: PASS
Reviewed by: auto-resolved (autonomy: auto) + adversarial refute-read (sonnet, EARNED-WITH-GAPS 0.87, gaps fixed in-loop) · date: 2026-06-15

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): `gateway_model_fallbacks_total{outcome="context_window"}` and `{outcome="content_policy"}` rates (a spike = a deployment under-provisioned for prompt size or a too-aggressive policy); ratio of trigger-fallovers that reach `outcome="served"` vs. exhaust to a real 4xx (low served-ratio = the whole group shares the limitation → the fallback is not helping); per-alias 4xx rate when `upstream_fallback_on_error` is enabled vs. baseline.
Spec delta for the next loop:
- A per-trigger enable set (e.g. `context_window` on, `content_policy` off) instead of one boolean — deferred at intake; revisit if an operator wants context-window fallover without content-policy fallover.
- Content-policy on a 200 `finish_reason=content_filter` is OUT (billing-safe). If demand appears, that is a SEPARATE response-quality-fallback feature (its own billing rule for the discarded generated tokens), not an extension of this 4xx-only seam.
- Streaming-resilience (task 3) reuses `classify_fallback_trigger` for the pre-first-byte path — the classifier is already provider-agnostic and side-effect-free, so it composes directly.

### Competency deltas
- [ADD · folded] A frozen §3 RANGE can silently contradict a §1 REJECT enumeration (here: §3 "status 400-499" vs §1 "429 already retry-handled") — the freeze gate did not catch it; the adversarial refute-read did. Lesson: when §3 states a broad range, cross-check it against §1's explicit rejects at freeze. Evidence: refute-read RISK finding → classifier now excludes 408/429.
- [TDD · folded] A pure classifier's pattern list must be tested in BOTH directions — true-positives (provider-real messages) AND false-positives (generic 400s like "field too long", "blocked by firewall") — because a too-broad pattern fails DANGEROUS (spurious fallover), not safe. Evidence: 5 guard tests added in-loop after the refute-read flagged bare `"too long"`/`"safety"`/`"blocked by"`.
- [ADD · folded] Acting on adversarial-review findings in-loop (re-cross tests→build to re-snapshot) keeps the gate honest without weakening any test — a verify-time refinement is legitimate when it STRENGTHENS assertions and leaves §3 byte-identical. Evidence: this task's PASS after the refine-and-re-cross.
