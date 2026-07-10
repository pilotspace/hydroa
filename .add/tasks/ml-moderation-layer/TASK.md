# TASK: ML moderation check class in the guardrail engine

slug: ml-moderation-layer · created: 2026-07-10 · stage: production
milestone: logs-explorer-guardrails-v2
sensitivity: security   <!-- new outbound egress seam + BYOK credential use + honest-degradation invariant -->
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: verify   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/proxy/domain/ports.py:GuardrailEvaluator` — the `Protocol` (`evaluate_pre`, `evaluate_post`) any new evaluator must satisfy; `@runtime_checkable`, structural (no inheritance needed).
- `apps/gateway/src/gateway/proxy/domain/entities.py:GuardrailEvent` (`guardrail: str, action: str, detail: str`) and `:GuardrailResult` (`blocked, blocked_by, masked_messages, events`) — both frozen dataclasses from guardrails-core §3; `action` is a plain `str`, not an enum, so a new value (`"unchecked"`) is usable with zero schema edit.
- `apps/gateway/src/gateway/proxy/infrastructure/guardrail_evaluator.py:RegexGuardrailEvaluator` (`evaluate_pre` L439, `evaluate_post` L609) — the existing pure-CPU (no `await` on IO) evaluator for `prompt_injection` + `pii_mask`; NOT edited by this task — a new check composes alongside it, never inside it.
- `apps/gateway/src/gateway/proxy/infrastructure/guardrail_evaluator.py:_has_block_mode` (L137) — generic over `guardrail_configs.values()`, keys off `cfg.get("mode") == "block"`; a new `ml_moderation` key with a `mode` field participates for free, zero edit.
- `apps/gateway/src/gateway/proxy/infrastructure/circuit_breaker.py:CircuitBreaker` (L35) — IO-tier-agnostic reusable primitive (`guard`/`call_allowed`/`record_success`/`on_upstream_error`); confirmed reusable-verbatim precedent: `[folded foundation-version 38 · from object-store-port]`.
- `apps/gateway/src/gateway/proxy/infrastructure/upstream_retry.py:execute_with_retry` (L103) — the shared bounded-retry+breaker helper already used by chat/embeddings calls; takes `do_request`, `render_response`, `breaker`, `max_retries`, `backoff_base`, `deadline_s`.
- `apps/gateway/src/gateway/proxy/infrastructure/openai_provider.py:OpenAIDirectProvider` — `__init__` (L67, owns one `CircuitBreaker()` + one `httpx.AsyncClient` with `_CONNECT_TIMEOUT`/`_NON_STREAM_TIMEOUT`), `_auth_headers` (L94, reads the request-scoped BYOK contextvar, raises `ProviderKeyMissing`), `post_json` (L204, breaker-guarded `POST path` returning `(status, json_body)`, 5xx/timeout/network → `UpstreamUnavailableError`). `post_json` is directly reusable for `POST /moderations` — no new HTTP client code needed.
- `apps/gateway/src/gateway/proxy/domain/provider_credentials.py:BYOK_PROVIDERS` (L49, frozenset incl. `"openai"`, no platform-fallback key for ANY of the 7 members) and `:ProviderKeyMissing` (L71, provider-name-only, no secret).
- `apps/gateway/src/gateway/proxy/domain/ports.py:TenantCredentialResolver` (L462) — `resolve(tenant_id, provider)` raises `ProviderKeyMissing` on absent/disabled key. Calling this DIRECTLY (not the `resolve_provider_credential` wrapper in use_cases.py:477, which converts to an HTTP 402) is how a missing moderation key degrades honestly instead of failing the whole request.
- `apps/gateway/src/gateway/proxy/application/use_cases.py:CompletionUseCase.complete` — "Step 4: Pre-call guardrails" (L1231-1290) and `.stream` (L1858) — the two call sites invoking `guardrail_evaluator.evaluate_pre` BEFORE cache lookup / upstream dispatch, for every request with non-empty `guardrail_configs`. `_fire_guardrail_metrics` (L353) and `guardrail_events_total` (`observability/metrics.py:88`, labels `[guardrail, mode, action]`, free-form Prometheus labels) already generic — zero edits needed for a new `guardrail="ml_moderation"`.
- `apps/gateway/src/gateway/proxy/api/deps.py:get_completion_use_case` (L107-237) — the PINNED override seam: `guardrail_evaluator = getattr(app.state, "guardrail_evaluator", None); if None: RegexGuardrailEvaluator()`. This is my wiring point; the override branch itself is untouched.
- `apps/gateway/src/gateway/main.py:1038` (`app.state.tenant_credential_resolver = CachedTenantCredentialResolver(...)`) and `:1069` (`app.state.batch_diversion = BatchDiversionAdapter(...)`) — the boot-time conditional-wiring precedent (construct once at startup, `None` when off) this task mirrors for `app.state.ml_moderation_provider`.
- `apps/gateway/src/gateway/tenants/api/guardrail_router.py` (FROZEN @ v4, additively extended once already for pii-v2) — `PromptInjectionConfig` (L56, the `enabled`+`mode` shape to mirror), `GuardrailConfigRequest`/`GuardrailConfigResponse` (L109/L121), `_build_response` (L188), `_fetch_guardrail_configs` (L198), `put_guardrails` partial-merge logic (L238-305). pii-v2's extension of `PiiMaskConfig` with `pii_custom_patterns` is the exact template for adding `ml_moderation`.
- `apps/gateway/src/gateway/core/error_catalog.py:391` `GUARDRAIL_BLOCKED = ErrorSpec(400, "ERR_GUARDRAIL_BLOCKED", ...)` — reused as-is, no new block-path error code. `:305 PAYLOAD_CUSTOM_PATTERN_INVALID = ErrorSpec(422, "ERR_PAYLOAD_INVALID", ...)` — the pattern reused for the new config-validation 422.
- `apps/gateway/src/gateway/keys/infrastructure/repository.py:198` / `keys/application/use_cases.py:315` — `guardrail_configs` flows onto `AuthzResult` per-request, currently sourced from the TENANT row only. The sibling `per-key-guardrail-policies` task (same milestone, `depends-on: none`, likely parallel) will layer key>tenant resolution over this SAME dict — `ml_moderation` must live inside that one dict, not a parallel structure.
- `apps/gateway/tests/guardrails/test_guardrails_core.py`, `conftest.py` — existing fakes (e.g. `ErrorGuardrailEvaluator` injected via `app.state.guardrail_evaluator`) and test conventions to mirror for the new evaluator's suite.
- `apps/gateway/src/gateway/proxy/domain/credential_context.py` (FROZEN @ v1, credential-resolution-seam §3) — `current_provider_credential: ContextVar[...]` + `set_/get_/reset_provider_credential` — the EXACT pattern (domain-layer `ContextVar`, request-scoped, `Token`-based reset-in-`finally`) a new sibling contextvar for tenant identity mirrors verbatim; this file itself is read-only precedent, not edited.

Context (working folder): no request/response payload store exists yet (sibling `payload-capture-store` task, unrelated to this one — moderation does not persist any payload); `tenants.guardrail_configs` is an existing JSONB column (migration `d4e7f1a2b3c5_guardrails_core.py`) — adding a third top-level key is additive, no new migration.

Honors (patterns / conventions): Protocol ports + fakes-via-`app.state` (PROJECT.md DDD fold v1); `None`-default DI arg = feature off = byte-identical (repeated verbatim across `vector_cache`, `web_search_enabled`, `batch_diversion`, `tenant_model_preset_store` in `CompletionUseCase.__init__`); reuse an IO-tier-agnostic primitive verbatim rather than re-inventing (object-store-port precedent); a fail-open port returns a neutral value on error, never raises past the port boundary (v8 DDD fold).

Seams consulted: none in `SEAMS.md` matched (no existing "moderation" or "outbound classification" entry) — the `CircuitBreaker`/`execute_with_retry` reuse above stands in for a seam citation.

Anchors the contract cites: `GuardrailEvaluator` (ports.py:210), `RegexGuardrailEvaluator` (guardrail_evaluator.py:402), `OpenAIDirectProvider.post_json` (openai_provider.py:204), `TenantCredentialResolver.resolve` (ports.py:462), `GUARDRAIL_BLOCKED`/`PAYLOAD_CUSTOM_PATTERN_INVALID` (error_catalog.py:391/305), `guardrail_router.py` PUT/GET (L225-305), `get_completion_use_case` (deps.py:107), `credential_context.py` (L32-88, the ContextVar pattern mirrored for tenant identity), `CompletionUseCase.complete`/`.stream` guardrail call sites (use_cases.py:1236/1861).

Issues/Risks (→ feed §1):
- R1 — `evaluate_pre` currently runs BEFORE cache lookup, for every request, and today performs zero `await`-on-IO work (pure regex). Adding a real network call inside this path fundamentally changes its latency profile; the call MUST be tightly timeout/retry-bounded or every guardrail-enabled tenant's hot path degrades.
- R2 — ALL 7 `BYOK_PROVIDERS` (incl. `openai`) require an explicit per-tenant key with **no platform fallback**. A tenant routing chat through `openrouter`/`anthropic`/etc. very likely has **no** `openai` key configured — "moderation enabled, no openai key" is the COMMON case, not an edge case. The failure-mode design must treat it as expected, not exceptional.
- R3 — `CircuitBreaker` instances are per-adapter-instance (`OpenAIDirectProvider.__init__` owns `self._breaker`). Reusing the SAME `OpenAIDirectProvider` singleton wired for real OpenAI chat/embeddings traffic would cross-contaminate breaker state between two unrelated failure domains (a moderation outage wrongly trips real completions, or vice versa). Needs an ISOLATED breaker + client instance dedicated to moderation.
- R4 — `guardrail_configs` is an unvalidated-at-read JSONB blob; a malformed `ml_moderation` block must degrade the same defensively-permissive way `prompt_injection`/`pii_mask` already do (`.get("mode", "audit")`-style defaults), not crash `evaluate_pre`.
- R5 — `guardrail_configs` is presently TENANT-sourced only; the sibling `per-key-guardrail-policies` task changes resolution to key>tenant over the same dict, in parallel with this task. `ml_moderation` must live inside that shared dict shape so the sibling's resolution work covers it transparently — named as a cross-task coordination risk, not silently assumed.
- R6 — the FROZEN `GuardrailEvaluator.evaluate_pre(messages, guardrail_configs)` call shape (2 positional args, both call sites in `use_cases.py` L1236 and L1861) carries NO tenant identity — yet BYOK credential resolution for the moderation call needs `tenant_id`. Widening the frozen Protocol signature is out of bounds for this task (a change request against a DIFFERENT frozen contract, guardrails-core). The fix mirrors the EXISTING `credential_context.py` pattern (`current_provider_credential`, a request-scoped `ContextVar`, itself FROZEN @ v1 as credential-resolution-seam and therefore also not edited): a NEW sibling `ContextVar` (`current_guardrail_tenant_id`), set by `CompletionUseCase` immediately before each `evaluate_pre` call (additive lines only — the 2-arg call itself is untouched) and read inside `MlModerationGuardrailEvaluator.evaluate_pre`.

Related intent: `MILESTONE.md` logs-explorer-guardrails-v2 §Scope item (5) and §Shared decisions "Security floor: ... ML moderation egress is a new outbound IO seam (timeout + retry + breaker per CLAUDE.md)"; `PROJECT.md` invariant "No outbound IO without timeout + bounded retry (idempotent only) + circuit breaker"; DDD fold `[object-store-port]` "the existing CircuitBreaker is IO-tier-agnostic — reusable primitive."

Ground SHA: `2071046` (branch `chore/add-housekeeping-clusters`)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: ML moderation guardrail check (provider-backed, default-off, block/audit, honest degradation)

Framings weighed:
**(chosen) A** — a NEW `MlModerationGuardrailEvaluator` implementing the `GuardrailEvaluator` Protocol standalone, composed with the existing `RegexGuardrailEvaluator` via a small `CompositeGuardrailEvaluator`, wired only when an ml-moderation provider is present at `app.state`. Keeps the frozen `RegexGuardrailEvaluator` completely untouched, isolates the new IO-bound failure domain (own breaker/timeout/retry) from the existing pure-CPU checks, and preserves the `deps.py` PINNED-seam byte-identical default path.
**B** (rejected) — add `ml_moderation` as a third branch INSIDE `RegexGuardrailEvaluator.evaluate_pre`. Rejected: mixes real network IO into a class whose name, docstring, and existing frozen tests assert pure-regex/CPU-only behavior; would force every `RegexGuardrailEvaluator` unit test to newly reason about timeouts/IO it doesn't today.
**C** (rejected, v1) — dispatch moderation through "any tenant-configured BYOK provider." Rejected for v1: no adapter in this repo exposes a moderation-shaped endpoint except OpenAI's `/v1/moderations`; Anthropic/Gemini have no equivalent standalone classification endpoint (safety scoring is inline on the completion call, not separately callable) and Bedrock Guardrails is a structurally different AWS API (`ApplyGuardrail`, SigV4, its own resource ARN). Recorded as a named follow-up (Glossary/SPEC delta), not silently dropped.

Must:
<must>
  - M1: `PUT /admin/guardrails` accepts an optional `ml_moderation` block — `{enabled: bool, mode: "block"|"audit", failure_mode?: "fail_open"|"fail_closed"}` (`failure_mode` defaults to `"fail_open"` when omitted) — mirroring `PromptInjectionConfig`'s shape/validation pattern; absent key preserves existing config (unmodified partial-merge semantics).
  - M2: When `ml_moderation.enabled` is false or the key is absent, `evaluate_pre` behavior AND latency are BYTE-IDENTICAL to today — zero BYOK credential resolution, zero network call, zero new `GuardrailEvent`.
  - M3: When enabled, `evaluate_pre` moderates the pre-call user-turn content — pre-call/prompt only; response/post-call moderation is explicitly OUT of this task (ranked as a freeze question below) — via the tenant's `openai` BYOK credential, through a DEDICATED `CircuitBreaker` + `execute_with_retry` (bounded retry, idempotent classification call), with an explicit connect+read timeout tighter than the chat-completion timeout. Tenant identity for credential resolution flows via a NEW request-scoped `ContextVar` (`current_guardrail_tenant_id`, mirroring the FROZEN `credential_context.py` pattern verbatim), set by `CompletionUseCase` immediately before each `evaluate_pre` call site — the frozen 2-arg `evaluate_pre(messages, guardrail_configs)` call shape itself is never widened (§0 R6).
  - M4: A flagged prompt in `mode="block"` sets `GuardrailResult.blocked=True, blocked_by="ml_moderation"` (reuses the existing `ERR_GUARDRAIL_BLOCKED` 400 — zero new error code) and fires `GuardrailEvent(guardrail="ml_moderation", action="blocked", ...)`. In `mode="audit"` the request proceeds unmodified with `action="audited"`.
  - M5: A clean (unflagged) prompt fires `GuardrailEvent(guardrail="ml_moderation", action="passed", ...)`.
  - M6: Any failure to obtain a verdict — missing/disabled BYOK `openai` credential (`ProviderKeyMissing`), breaker OPEN, timeout, network error, non-2xx from the moderation endpoint — is caught INSIDE the evaluator (never raised to the use-case) and fires `GuardrailEvent(guardrail="ml_moderation", action="unchecked", detail=<reason>)`. The request is then allowed through (`blocked=False`) when `failure_mode="fail_open"`, or blocked (`blocked_by="ml_moderation"`, reusing `ERR_GUARDRAIL_BLOCKED`) when `failure_mode="fail_closed"`. An `unchecked` verdict is NEVER reported as `passed`.
  - M7: `ml_moderation` evaluates the message content AFTER `pii_mask` has run in the same composite pass (regex checks run first — cheap, already fail-safe) — i.e. moderation sees the PII-MASKED copy when masking fired, reducing third-party PII egress; it never mutates message content itself (`masked_messages` on the composite result is always the regex evaluator's, untouched by the ml check). A moderation timeout/failure never prevents or corrupts the regex checks' own events, and vice versa — composite merge: `blocked = regex.blocked or ml.blocked`; `blocked_by` = whichever fired (regex checked first); `events` = concatenation of both.
  - M8: The moderation breaker/client instance is DEDICATED (its own `CircuitBreaker`, its own provider-adapter instance) — never the same instance used for real OpenAI chat/embeddings traffic, so a moderation-provider outage cannot trip real completions and a completions outage cannot disable moderation.
  - M9: Wiring is fully additive: `deps.py`'s PINNED `app.state.guardrail_evaluator` override seam is untouched (tests injecting a fake evaluator still short-circuit before this task's code runs). The new composite is constructed ONLY when `app.state.ml_moderation_provider` is present (boot-wired conditionally in `main.py`, mirroring the `tenant_credential_resolver`/`batch_diversion` pattern) — absent it, wiring is byte-identical `RegexGuardrailEvaluator()`.
</must>
Reject:
<reject>
  - `ml_moderation.mode` not in {block, audit} -> "ERR_PAYLOAD_INVALID"
  - `ml_moderation.failure_mode` not in {fail_open, fail_closed} -> "ERR_PAYLOAD_INVALID"
  - prompt flagged in block mode, moderation call SUCCEEDED -> "ERR_GUARDRAIL_BLOCKED"
  - moderation UNCHECKED (any provider failure) AND failure_mode=fail_closed -> "ERR_GUARDRAIL_BLOCKED"
</reject>
After:
<after>
  - A1: A tenant with `ml_moderation.enabled=true, mode=block` never lets a flagged prompt reach the upstream provider (mirrors the existing `prompt_injection` block guarantee).
  - A2: A tenant with `ml_moderation` disabled/absent has zero behavior change from before this task shipped — no added latency, no BYOK credential resolution, no network call.
  - A3: A moderation-provider outage is ALWAYS observable (`gateway_guardrail_events_total{guardrail="ml_moderation",action="unchecked"}` increments) and is never silently recorded as `passed`.
  - A4: Real OpenAI chat/embeddings traffic is unaffected by a moderation-provider outage (isolated breaker), and moderation is unaffected by a chat-completion outage.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ OpenAI's `/v1/moderations` endpoint is assumed to be the only realistic moderation-capable endpoint among this repo's 7 BYOK providers, and assumed $0-priced (free classification) per OpenAI's historical published pricing — NOT re-verified against live docs in this grounding pass (no live-docs check run here; this project's own history explicitly warns "environment/provider assumptions decay... must be RE-VALIDATED live each milestone," not carried forward from training knowledge). Lowest confidence because it is both an external-pricing fact and an external-endpoint-shape fact I have not freshly confirmed. If wrong on cost: a "free" moderation call could silently accrue real spend with no `usage_record` (this design deliberately does NOT bill moderation calls) — a real invoice-mismatch risk. Recommend a live-docs check (`find-docs` skill) at BUILD time, before wiring the real HTTP call, not deferred past that.
  - [ ] Response body shape of `/v1/moderations` (`results[0].flagged`, `results[0].categories`) — assumed OpenAI-compatible JSON; confirm against a live fixture the same way existing adapters pin SSE fixtures (v9 DDD fold precedent) before the exact field-read is frozen in code.
  - [ ] Whether `PUT /admin/guardrails` should proactively 422 when enabling `ml_moderation` without a configured `openai` BYOK key (fail-fast at config time) vs. allow it and rely purely on the honest `unchecked` degrade at request time (this draft's M1/M6 choice: allow + degrade, decoupled from key-management timing) — ranked as FREEZE-QUESTION 1.
  - [ ] Exact latency budget numbers for the moderation call (connect/read timeout, retry count/backoff) — this draft recommends connect=1.5s / read=2.5s / max_retries=1 / backoff_base=0.5s (worst case ≈4-5s added latency before failing open), a STARTING proposal, not measured against real OpenAI moderation p99 — ranked as FREEZE-QUESTION 2.
  - [ ] Post-call (response) moderation — task scope explicitly ranks this a freeze question, not required for v1. This draft's recommendation: OUT of this task (pre-call/prompt only), a named follow-up — ranked as FREEZE-QUESTION 3.
  - [ ] Whether a GLOBAL operator kill-switch (a settings flag gating whether `app.state.ml_moderation_provider` is ever constructed) is needed on top of the per-tenant `ml_moderation.enabled` flag, or whether always-wiring-when-`tenant_credential_resolver`-is-present is sufficient (this draft's recommendation, to minimize config surface — the true kill switch is the per-tenant flag) — ranked as FREEZE-QUESTION 4.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: PUT accepts a valid ml_moderation block   # M1
  Given a tenant admin PUTs /admin/guardrails with ml_moderation: {enabled: true, mode: "block", failure_mode: "fail_closed"}
  When the request is processed
  Then the response is 200 with ml_moderation echoed back
  And prompt_injection/pii_mask config, if any, are preserved unmodified

Scenario: PUT rejects an invalid mode   # R1
  Given a tenant admin PUTs ml_moderation: {enabled: true, mode: "warn"}
  When the request is processed
  Then the response is 422 "ERR_PAYLOAD_INVALID"
  And the stored guardrail_configs are unchanged (no partial write)

Scenario: PUT rejects an invalid failure_mode   # R2
  Given a tenant admin PUTs ml_moderation: {enabled: true, mode: "block", failure_mode: "always"}
  When the request is processed
  Then the response is 422 "ERR_PAYLOAD_INVALID"
  And the stored guardrail_configs are unchanged

Scenario: disabled ml_moderation is byte-identical   # M2
  Given a tenant with ml_moderation absent from guardrail_configs
  When a chat completion request is made
  Then evaluate_pre performs zero credential resolution and zero network calls
  And request latency and the response are unchanged from before this task shipped

Scenario: enabled, clean prompt passes   # M5
  Given a tenant with ml_moderation enabled (mode=audit) and a valid openai BYOK key
  When a chat completion request with unobjectionable content is made
  Then the moderation provider is called and returns flagged=false
  And a GuardrailEvent(guardrail="ml_moderation", action="passed") is recorded
  And the request proceeds to upstream unmodified

Scenario: enabled block mode blocks a flagged prompt   # M4, R3
  Given a tenant with ml_moderation enabled (mode=block) and a valid openai BYOK key
  When a chat completion request with content the moderation provider flags is made
  Then the response is 400 "ERR_GUARDRAIL_BLOCKED"
  And a GuardrailEvent(guardrail="ml_moderation", action="blocked") is recorded
  And no request reaches the routed completion provider
  And a usage record is fired with guardrail_blocked=True, blocked_by="ml_moderation"

Scenario: enabled audit mode allows a flagged prompt through   # M4
  Given a tenant with ml_moderation enabled (mode=audit) and a valid openai BYOK key
  When a chat completion request with content the moderation provider flags is made
  Then the request proceeds to the routed completion provider unmodified
  And a GuardrailEvent(guardrail="ml_moderation", action="audited") is recorded

Scenario: missing BYOK openai key degrades honestly, never 402s the request   # M6
  Given a tenant with ml_moderation enabled and NO openai BYOK credential configured
  When a chat completion request (routed via a different provider, e.g. openrouter) is made
  Then the moderation evaluator catches ProviderKeyMissing internally
  And a GuardrailEvent(guardrail="ml_moderation", action="unchecked") is recorded
  And the request is allowed through when failure_mode=fail_open (default)
  And the request is blocked with ERR_GUARDRAIL_BLOCKED when failure_mode=fail_closed
  And the client never sees an ERR_PROVIDER_KEY_MISSING / 402 for the ROUTED model's own provider

Scenario: moderation provider timeout degrades honestly under fail_open   # M6, A3
  Given a tenant with ml_moderation enabled (failure_mode=fail_open) and a valid openai BYOK key
  When the moderation call exceeds its bounded timeout after its bounded retry
  Then the request is allowed through unblocked
  And a GuardrailEvent(guardrail="ml_moderation", action="unchecked", detail mentions timeout) is recorded
  And the event is NEVER recorded as action="passed"

Scenario: moderation provider outage under fail_closed blocks the request   # M6, R4
  Given a tenant with ml_moderation enabled (failure_mode=fail_closed) and a valid openai BYOK key
  When the moderation provider is unreachable (network error)
  Then the response is 400 "ERR_GUARDRAIL_BLOCKED"
  And a GuardrailEvent(guardrail="ml_moderation", action="unchecked") is recorded
  And blocked_by="ml_moderation" on the fired usage record (distinguishable from a content-flag block via the event detail)

Scenario: moderation breaker OPEN skips the network call entirely   # M6, M8
  Given the moderation-dedicated CircuitBreaker is OPEN from prior consecutive failures
  When a new chat completion request arrives with ml_moderation enabled
  Then no new network call is attempted (breaker.guard() short-circuits)
  And a GuardrailEvent(action="unchecked") is recorded immediately (no added retry latency)

Scenario: moderation outage never trips the real completion provider's breaker   # M8, A4
  Given ml_moderation is enabled and its dedicated breaker has just OPENed from moderation failures
  When a chat completion request is subsequently routed to openai for the actual completion
  Then the completion call proceeds through OpenAIDirectProvider's OWN separate breaker, unaffected
  And a real completion outage likewise never increments the moderation breaker's failure count

Scenario: regex and ml_moderation checks compose independently   # M7
  Given a tenant with prompt_injection (mode=block) AND ml_moderation (mode=block) both enabled
  When a request matches BOTH a regex injection pattern AND is flagged by moderation
  Then blocked=True with blocked_by="prompt_injection" (regex evaluated first in the composite)
  And events include BOTH a prompt_injection "blocked" event AND an ml_moderation event (best-effort, may itself be evaluated or skipped depending on short-circuit policy — recorded, not silently dropped)

Scenario: ml_moderation sees PII-masked content, not raw content   # M7
  Given a tenant with pii_mask (mode=mask) AND ml_moderation both enabled, and the prompt contains an email address
  When the request is evaluated
  Then the regex evaluator masks the email BEFORE the composite calls ml_moderation.evaluate_pre
  And the moderation provider never receives the raw email address

Scenario: absent app.state.ml_moderation_provider wires the unchanged default evaluator   # M9
  Given a deployment/test that never sets app.state.ml_moderation_provider (today's default)
  When get_completion_use_case builds the CompletionUseCase
  Then guardrail_evaluator is exactly RegexGuardrailEvaluator() as before this task
  And the PINNED app.state.guardrail_evaluator test-override seam is unaffected
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Status: FROZEN @ v1 — approved by Tin Dang
Least-sure flag surfaced at freeze: [contract] OpenAI /v1/moderations shape + $0 pricing is trained-knowledge, NOT live-verified — a live-docs check is REQUIRED before the real HTTP call is wired at BUILD (if priced, unbilled real spend accrues; this design never bills moderation). Decided at freeze (Tin, 2026-07-10 batch): all 4 agent recommendations accepted (allow-enable + honest unchecked degrade; connect=1.5s/read=2.5s/1 retry as starting budget; pre-call only v1; no separate global flag).


```
PUT /admin/guardrails   body: { prompt_injection?, pii_mask?, ml_moderation?: {
    enabled: bool, mode: "block"|"audit", failure_mode?: "fail_open"|"fail_closed" (default "fail_open")
  } }
  200 -> { prompt_injection, pii_mask, ml_moderation: {enabled, mode, failure_mode} | null }
  422 -> { error: "ERR_PAYLOAD_INVALID" }   # bad mode / bad failure_mode; atomic — no partial write

GET /admin/guardrails
  200 -> { prompt_injection, pii_mask, ml_moderation: {...} | null }   # unchanged response-model pattern

# no new HTTP endpoint for the moderation CHECK itself — it fires inside the EXISTING
# POST /v1/chat/completions pre-call guardrail step, same insertion point as prompt_injection/pii_mask
POST /v1/chat/completions   (unchanged path/method)
  400 -> { error: "ERR_GUARDRAIL_BLOCKED" }   # reused; blocked_by ("ml_moderation" | "prompt_injection" | "error")
                                                # stays an internal usage_record field, never on the wire —
                                                # matches existing prompt_injection/pii_mask precedent

Schema: tenants.guardrail_configs (existing JSONB column, migration d4e7f1a2b3c5) gains a
  THIRD top-level key "ml_moderation" — no new migration (additive JSONB key, same as pii-v2's
  pii_custom_patterns addition). Access pattern unchanged: _fetch_guardrail_configs / PUT partial-merge
  in guardrail_router.py.
```

Illustrative Python (new file `apps/gateway/src/gateway/proxy/infrastructure/ml_moderation_evaluator.py`; syntax/import sanity-checked by hand — not yet run):

```python
from __future__ import annotations

from typing import Any, Protocol, TypedDict

from gateway.proxy.domain.entities import GuardrailEvent, GuardrailResult
from gateway.proxy.domain.guardrail_tenant_context import get_guardrail_tenant_id
from gateway.proxy.domain.ports import GuardrailEvaluator, TenantCredentialResolver

# New sibling module `gateway.proxy.domain.guardrail_tenant_context` (mirrors
# `credential_context.py` verbatim: ContextVar + set_/get_/reset_ helpers) —
# `current_guardrail_tenant_id: ContextVar[Any | None]`, set by CompletionUseCase
# immediately before each evaluate_pre call, read here via get_guardrail_tenant_id().
# ProviderKeyMissing / CircuitOpenError / httpx errors are all handled by the broad
# `except Exception` below (M6: EVERY moderation-call failure mode degrades identically —
# naming subclasses separately would add branches with no behavioral difference).

class ModerationVerdict(TypedDict):
    flagged: bool
    categories: list[str]

class ModerationProvider(Protocol):
    """Structural port: one dedicated adapter instance, one dedicated breaker."""

    async def moderate(self, text: str) -> ModerationVerdict: ...

def _concat_user_content(messages: list[dict[str, Any]]) -> str:
    return "\n".join(
        str(m.get("content", ""))
        for m in messages
        if isinstance(m, dict) and m.get("role") == "user"
    )

class MlModerationGuardrailEvaluator:
    """Implements GuardrailEvaluator structurally (evaluate_pre only — no post-call leg;
    evaluate_post is a pass-through no-op so the Protocol is still satisfied)."""

    def __init__(
        self,
        provider: ModerationProvider,
        credential_resolver: TenantCredentialResolver | None,
    ) -> None:
        self._provider = provider
        self._resolver = credential_resolver

    async def evaluate_pre(
        self,
        messages: list[dict[str, Any]],
        guardrail_configs: dict[str, Any],
    ) -> GuardrailResult:
        # Signature stays the frozen 2-arg GuardrailEvaluator shape (§0 R6) — tenant
        # identity is read from the request-scoped ContextVar CompletionUseCase sets
        # immediately before this call, NOT a widened parameter.
        cfg = guardrail_configs.get("ml_moderation")
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            return GuardrailResult(blocked=False, blocked_by=None, masked_messages=None, events=[])

        mode = cfg.get("mode", "audit")
        failure_mode = cfg.get("failure_mode", "fail_open")
        tenant_id = get_guardrail_tenant_id()

        try:
            if self._resolver is not None and tenant_id is not None:
                await self._resolver.resolve(tenant_id, "openai")  # raises ProviderKeyMissing; sets contextvar
            verdict = await self._provider.moderate(_concat_user_content(messages))
        except Exception as exc:  # noqa: BLE001 — M6: every failure mode degrades identically
            reason = type(exc).__name__
            event = GuardrailEvent(guardrail="ml_moderation", action="unchecked", detail=reason)
            if failure_mode == "fail_closed":
                return GuardrailResult(
                    blocked=True, blocked_by="ml_moderation", masked_messages=None, events=[event]
                )
            return GuardrailResult(blocked=False, blocked_by=None, masked_messages=None, events=[event])

        if verdict["flagged"]:
            action = "blocked" if mode == "block" else "audited"
            event = GuardrailEvent(guardrail="ml_moderation", action=action, detail=",".join(verdict["categories"]))
            return GuardrailResult(
                blocked=(mode == "block"),
                blocked_by="ml_moderation" if mode == "block" else None,
                masked_messages=None,
                events=[event],
            )
        return GuardrailResult(
            blocked=False, blocked_by=None, masked_messages=None,
            events=[GuardrailEvent(guardrail="ml_moderation", action="passed", detail="")],
        )

    async def evaluate_post(
        self, response_body: dict[str, Any], guardrail_configs: dict[str, Any]
    ) -> dict[str, Any]:
        return response_body  # no post-call leg in v1 — see FREEZE-QUESTION 3

class CompositeGuardrailEvaluator:
    """Chains the existing RegexGuardrailEvaluator with MlModerationGuardrailEvaluator.
    Regex runs FIRST — cheap, already fail-safe, and its masked_messages feed ml_moderation
    so third-party moderation calls never see raw PII (M7)."""

    def __init__(self, primary: GuardrailEvaluator, ml_moderation: MlModerationGuardrailEvaluator) -> None:
        self._primary = primary
        self._ml = ml_moderation

    async def evaluate_pre(
        self, messages: list[dict[str, Any]], guardrail_configs: dict[str, Any]
    ) -> GuardrailResult:
        r1 = await self._primary.evaluate_pre(messages, guardrail_configs)
        content = r1.masked_messages if r1.masked_messages is not None else messages
        r2 = await self._ml.evaluate_pre(content, guardrail_configs)
        return GuardrailResult(
            blocked=r1.blocked or r2.blocked,
            blocked_by=r1.blocked_by or r2.blocked_by,
            masked_messages=r1.masked_messages,
            events=[*r1.events, *r2.events],
        )

    async def evaluate_post(
        self, response_body: dict[str, Any], guardrail_configs: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._primary.evaluate_post(response_body, guardrail_configs)
```

`OpenAiModerationClient` (implements `ModerationProvider`): wraps a DEDICATED `OpenAIDirectProvider` instance (own `CircuitBreaker`, own `httpx.AsyncClient` — never the singleton used for real chat/embeddings) and calls `execute_with_retry` around `POST /moderations` (`{"input": text, "model": "omni-moderation-latest"}`), with `max_retries=1`, `deadline_s≈4.0`, connect/read timeouts tighter than the chat default (proposed 1.5s/2.5s — FREEZE-QUESTION 2). `moderate()` raises on any terminal failure; the caller (`MlModerationGuardrailEvaluator`) is the ONLY place that catches it, per M6.

Wiring delta (`deps.py:get_completion_use_case`, additive around the existing `guardrail_evaluator = getattr(...)` block):
```
if guardrail_evaluator is None:
    regex_evaluator = RegexGuardrailEvaluator()
    ml_provider = getattr(request.app.state, "ml_moderation_provider", None)
    if ml_provider is not None:
        guardrail_evaluator = CompositeGuardrailEvaluator(
            regex_evaluator,
            MlModerationGuardrailEvaluator(ml_provider, tenant_credential_resolver),
        )
    else:
        guardrail_evaluator = regex_evaluator
```
`main.py` boot delta (near L1038, mirroring the `tenant_credential_resolver` construction): construct `app.state.ml_moderation_provider = OpenAiModerationClient(...)` whenever `app.state.tenant_credential_resolver` is present (see FREEZE-QUESTION 4 for whether an additional global settings kill-switch gates this construction).

`use_cases.py` delta (additive, two insertion points — immediately before the existing `guardrail_evaluator.evaluate_pre(...)` call in both `complete()` L1236 and `stream()` L1861 — the call itself, its 2 positional args, and everything after it stay byte-identical):
```
from gateway.proxy.domain.guardrail_tenant_context import (
    reset_guardrail_tenant_id, set_guardrail_tenant_id,
)
...
_tid_token = set_guardrail_tenant_id(authz.tenant_id)
try:
    result = await guardrail_evaluator.evaluate_pre(body.get("messages", []), guardrail_configs)
finally:
    reset_guardrail_tenant_id(_tid_token)
```
New module `gateway/proxy/domain/guardrail_tenant_context.py` mirrors `credential_context.py` line-for-line (ContextVar + `set_/get_/reset_guardrail_tenant_id`, `Token`-based reset-in-`finally`) — see §0 R6.

Glossary deltas:
- **ML moderation check**: a provider-backed (OpenAI `/v1/moderations`) pre-call guardrail check, default-off, tenant-configured (`block`/`audit`), distinct from the deterministic regex checks (`prompt_injection`, `pii_mask`) in that it performs real outbound IO and can therefore itself fail independently of the content it inspects.
- **Unchecked (guardrail verdict)**: a THIRD guardrail outcome alongside `passed`/`blocked`/`audited` — the check did not run to completion (provider/credential/timeout failure), so no safety judgement was made. Distinct from `passed`; must never be conflated with it. First guardrail check in this codebase to need this outcome, because it is the first with a real external-failure mode.
- **failure_mode (guardrail)**: a NEW per-check config axis (`fail_open`|`fail_closed`), orthogonal to `mode` (`block`|`audit`). `mode` governs what happens when content IS flagged; `failure_mode` governs what happens when the check ITSELF could not be evaluated.

Status: DRAFT — awaiting human freeze.
Reported: no — freeze report renders when Tin reviews this draft.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_put_ml_moderation_valid: arrange tenant + PUT valid block / act request / assert 200 + echoed config · covers: M1
  - test_put_ml_moderation_bad_mode: arrange PUT mode="warn" / act request / assert 422 ERR_PAYLOAD_INVALID + config unchanged · covers: R1
  - test_put_ml_moderation_bad_failure_mode: arrange PUT failure_mode="always" / act request / assert 422 + config unchanged · covers: R2
  - test_disabled_ml_moderation_byte_identical: arrange config absent / act chat completion / assert zero credential-resolver calls, zero provider calls · covers: M2
  - test_ml_moderation_passes_clean_prompt: arrange enabled+audit+fake provider(flagged=False) / act request / assert action="passed", request proceeds · covers: M5
  - test_ml_moderation_blocks_flagged_prompt: arrange enabled+block+fake provider(flagged=True) / act request / assert 400 ERR_GUARDRAIL_BLOCKED, action="blocked", no upstream call · covers: M4, R3
  - test_ml_moderation_audits_flagged_prompt: arrange enabled+audit+fake provider(flagged=True) / act request / assert 200 (proceeds), action="audited" · covers: M4
  - test_ml_moderation_missing_key_fail_open: arrange enabled+fail_open+resolver raising ProviderKeyMissing / act request / assert action="unchecked", request proceeds, no 402 leak · covers: M6
  - test_ml_moderation_missing_key_fail_closed: arrange enabled+fail_closed+resolver raising ProviderKeyMissing / act request / assert 400 ERR_GUARDRAIL_BLOCKED, action="unchecked" (not a content-flag block) · covers: M6, R4
  - test_ml_moderation_timeout_fail_open: arrange enabled+fail_open+provider raising timeout after bounded retry / act request / assert action="unchecked", never "passed" · covers: M6, A3
  - test_ml_moderation_breaker_open_short_circuits: arrange dedicated breaker pre-tripped OPEN / act request / assert no network call attempted, action="unchecked" immediately · covers: M6, M8
  - test_ml_moderation_breaker_isolated_from_completion_breaker: arrange ml breaker OPEN / act a real chat completion routed to openai / assert completion's own breaker unaffected · covers: M8, A4
  - test_composite_regex_and_ml_moderation_both_block: arrange both prompt_injection+ml_moderation block-mode triggers / act request / assert blocked_by="prompt_injection" (regex evaluated first), both events present · covers: M7
  - test_ml_moderation_sees_masked_not_raw_content: arrange pii_mask(mask)+ml_moderation enabled, prompt contains an email / act request / assert fake provider's captured input has no raw email · covers: M7
  - test_wiring_absent_ml_provider_default_evaluator_unchanged: arrange app.state.ml_moderation_provider absent / act get_completion_use_case / assert isinstance(evaluator, RegexGuardrailEvaluator) · covers: M9
  - test_tenant_id_contextvar_threads_to_credential_resolve: arrange enabled+fake resolver capturing its tenant_id arg / act a chat completion for tenant X / assert resolver.resolve was called with tenant_id==X, and the var is reset (unset) after the request completes · covers: M3, §0 R6
</test_plan>

Tests live in: `apps/gateway/tests/guardrails/test_ml_moderation.py` · ran RED (missing implementation) before Build, confirmed GREEN after.

Actual result (18 tests written — the 16 planned + 2 additional adapter-level tests
covering the live-verified OpenAI wire shape and its terminal-4xx failure mapping,
neither weakening nor replacing any planned scenario):
- RED confirmed for the right reason: `ModuleNotFoundError: No module named
  'gateway.proxy.domain.guardrail_tenant_context'` at collection time (re-verified by
  stashing all BUILD-phase src changes and re-running — same import error, not a
  broken harness).
- GREEN: all 18 new tests pass; the existing frozen `test_guardrails_core.py` suite
  (17 tests) stays green unmodified — 35/35 in `apps/gateway/tests/guardrails/`.
- Coverage: `ml_moderation_evaluator.py` 92% (target 90%), `guardrail_tenant_context.py`
  100%. The few uncovered lines are two trivial `__init__` bodies and the no-op
  `evaluate_post` pass-through — no untested branch logic.
- Regression sweep beyond guardrails/: `openai_chat_dispatch`, `openai_retry_parity`,
  `provider_seam`, `embeddings_endpoint`, `images_endpoint`, `passthrough_nonfinite_
  sanitize`, `secret_chain_hardening`, `web_search`, `byok_verify`,
  `edge_input_hardening/test_s3_egress_policy.py` all green (179 passed). 5 pre-
  existing failures in `minimax_adapter_registry` (schema drop-order conflict on
  `scim_tokens`→`users` FK) reproduced identically on a clean DB with ALL of this
  task's changes stashed — confirmed unrelated to this build, not investigated further
  (a different sibling task's concern).
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0 -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
`apps/gateway/src/gateway/proxy/infrastructure/ml_moderation_evaluator.py` (new)
`apps/gateway/src/gateway/proxy/domain/guardrail_tenant_context.py` (new — mirrors `credential_context.py`, §0 R6)
`apps/gateway/src/gateway/proxy/infrastructure/openai_provider.py` (reuse only — read, do not modify unless a constructor param is genuinely needed for a second dedicated instance)
`apps/gateway/src/gateway/proxy/application/use_cases.py` (additive only — set/reset the new tenant contextvar at the two existing `evaluate_pre` call sites; the call itself unchanged)
`apps/gateway/src/gateway/tenants/api/guardrail_router.py`
`apps/gateway/src/gateway/proxy/api/deps.py`
`apps/gateway/src/gateway/main.py`
`apps/gateway/src/gateway/core/config.py`
`apps/gateway/tests/guardrails`

Strategy (ordered batches):
1. Domain: `guardrail_tenant_context.py` (new — ContextVar + set_/get_/reset_, mirrors `credential_context.py` line-for-line, §0 R6) and `ModerationVerdict`/`ModerationProvider` (structural Protocol) — additive only, no edits to existing `ports.py`/`entities.py` symbols.
2. Infrastructure: `OpenAiModerationClient` (dedicated `OpenAIDirectProvider` instance + own `CircuitBreaker`, `post_json` + `execute_with_retry`) → `MlModerationGuardrailEvaluator` (config gate, contextvar-sourced tenant id, credential resolve, honest-degrade) → `CompositeGuardrailEvaluator`, all in the one new file above.
3. API: extend `guardrail_router.py` — `MlModerationConfig` (mirrors `PromptInjectionConfig`'s `field_validator`), add to `GuardrailConfigRequest`/`Response`, extend `_build_response` and the PUT partial-merge `fields_set` branch (mirror the existing `prompt_injection`/`pii_mask` branches exactly).
4. Wiring: `use_cases.py` (additive `set_guardrail_tenant_id`/`reset_guardrail_tenant_id` around both `evaluate_pre` call sites) → `deps.py` (additive `if ml_provider is not None:` branch) → `main.py` boot (construct `app.state.ml_moderation_provider` conditionally, mirroring `tenant_credential_resolver`/`batch_diversion`).
5. Tests: red-first per the §4 plan, one file `test_ml_moderation.py` under `apps/gateway/tests/guardrails/`, reusing the existing `conftest.py` fixtures where the shape matches (fake `TenantCredentialResolver`, fake HTTP transport for `httpx.AsyncClient`).

Persona (required): generic — trust-and-safety platform engineer / interface-architect stance (no seeded `.add/personas/` file matches this domain yet; recommend `add-persona` seed a `ml-safety-engineer` persona if this area grows a second task).
Spawn isolation (default): worktree (per `worktree-isolated-spawn-default`; no stated reason to share tree).
Known-problem fixes: R3 (breaker cross-contamination) → dedicated `OpenAIDirectProvider`/`CircuitBreaker` instance, never the chat singleton; R2 (missing-key is the common case) → catch `ProviderKeyMissing` inside the evaluator, never let it propagate to the 402-raising wrapper; R1 (hot-path latency) → tight timeout/retry ceiling + breaker fast-fail path (no wasted retry when OPEN).
Strategy actually used: followed the 5 batches as ordered, with two deviations recorded below
(both discovered mid-build, both strictly-more-correct + harmless, no test/contract touched):
1. Domain: built `guardrail_tenant_context.py` exactly as planned — line-for-line mirror of
   `credential_context.py` (ContextVar + set_/get_/reset_, Token-based reset).
2. Infrastructure: built `ml_moderation_evaluator.py` largely as the illustrative §3 Python,
   with one load-bearing fix (Deviation 1 below) and one architecture fix forced by this
   project's strict pyright gate (Deviation 2 below — `OpenAIDirectProvider.post_json_with_
   retry`, a new public method, instead of `OpenAiModerationClient` reaching into
   `_client`/`_breaker`/`_auth_headers()` across the module boundary).
3. API: extended `guardrail_router.py` exactly as planned — `MlModerationConfig` mirrors
   `PromptInjectionConfig`'s validator shape; `_build_response` and the PUT partial-merge
   `fields_set` branch extended in place.
4. Wiring: `use_cases.py` — added `finally:` clauses to the TWO EXISTING try/except blocks
   (rather than nesting a new try/finally around them) — smaller diff, same guarantee (the
   contextvar always resets, including on the `raise GUARDRAIL_BLOCKED.exc()` path). `deps.py`
   and `main.py` wired exactly as the §3 delta describes.
5. Tests: one file, `test_ml_moderation.py`, 18 tests (16 planned + 2 extra: the live-verified
   wire-shape test and a terminal-4xx failure-mapping test) — reused `test_guardrails_core.py`'s
   arrange helpers via import (not duplication) except `active_model`, which is defined locally
   (importing a fixture across test modules shadows the parameter name in every consuming test
   signature, which ruff's F811 correctly flags — a local copy was cleaner than fighting the
   linter). Discovered mid-build that `provider_resolver` IS wired in the shared test `app`
   fixture (contrary to the design's assumption it would be absent), so the ROUTED completion
   provider's own credential resolution also calls the SAME fake `tenant_credential_resolver` —
   `FakeCredentialResolver` was made provider-specific (`missing_for: frozenset[str]`) so a
   "missing openai moderation key" test doesn't also 402 the routed provider's own resolve().
Safety rule (feature-specific): the moderation call and the fail-open/fail-closed decision must resolve inside a SINGLE bounded `evaluate_pre` invocation — no background task, no fire-and-forget for the BLOCKING decision itself (unlike alert emission, the block/allow verdict must be synchronous and awaited before the request proceeds).
Code lives in: `./src/` (task-relative alias — actual paths are the `apps/gateway/...` tokens declared in Scope above)
Constraints: do NOT change any test or the contract; allow-list packages only (no new third-party HTTP client — reuse `httpx` via `OpenAIDirectProvider`); ask if unclear.

Deviations from the illustrative §3 Python / Scope note (recorded per the project's fix-and-record rule — strictly-more-correct + harmless, no test/contract edited):
1. **Load-bearing fix**: the frozen §3 illustrative code called `self._resolver.resolve(tenant_id, "openai")` and commented "raises ProviderKeyMissing; sets contextvar" — but `TenantCredentialResolver.resolve()` only RETURNS a `ProviderCredential`; it never sets `credential_context.current_provider_credential` itself (only the separate `resolve_provider_credential()` wrapper does that, and M3/§0 R25 explicitly says NOT to call that wrapper). Without an explicit `set_provider_credential(cred)` after resolving, `OpenAIDirectProvider._auth_headers()` would always raise `ProviderKeyMissing` on the moderation call even with a valid BYOK key — silently breaking M3/M4/M5 (every enabled-moderation request would degrade to "unchecked"). Fixed by adding the `set_provider_credential(cred)` / `reset_provider_credential(token)` pair around the `self._provider.moderate(...)` call, mirroring `resolve_provider_credential`'s own set step. Verified end-to-end by `test_ml_moderation_passes_clean_prompt` / `test_ml_moderation_blocks_flagged_prompt` and the adapter-level `test_openai_moderation_client_matches_live_verified_shape` (asserts the real `Authorization: Bearer sk-test` header reaches the mock transport).
2. **Architecture fix (Scope note vs. pyright strict gate)**: the Scope note for `openai_provider.py` says "reuse only... unless a constructor param is genuinely needed," and the §3 illustrative prose describes `OpenAiModerationClient` reaching into `OpenAIDirectProvider`'s `_client`/`_breaker`/`_auth_headers()` from a different module. This project's `pyright` runs `typeCheckingMode = "strict"` over `src/gateway` and `reportPrivateUsage` correctly rejects exactly that cross-module private-attribute access (3 errors). Fixed by adding one new PUBLIC method, `OpenAIDirectProvider.post_json_with_retry()` — the same guard→auth→POST→breaker-bookkeeping shape as the existing `post_json`, but wired through `execute_with_retry` for bounded retry, with a `provider_label` override so `openai_moderation` is distinguishable from primary chat/embeddings traffic in `upstream_retries_total`. `OpenAiModerationClient.moderate()` now calls only this public method — zero private-attribute reach-in. `uv run pyright src/gateway` is 0 errors after the fix (was 3 before).
3. **Test infra parity fix** (`tests/guardrails/conftest.py`): its `TEST_DATABASE_URL` was hardcoded to the un-suffixed `gateway_test` DB, ignoring `GATEWAY_TEST_DATABASE_URL` (unlike the root `tests/conftest.py`, which already reads it) — a real collision risk when multiple worktrees run the guardrails suite concurrently against the shared Postgres. Fixed to read the env var with the identical previous literal as fallback — behavior-preserving for every existing caller, verified by the full `test_guardrails_core.py` suite staying green (17/17) both with and without the env var set.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token with "/" = project root · a bare name = sibling of the previous token's dir · a DIRECTORY token covers its whole subtree (diverges from §4's non-recursive counting) · outside-root resolutions drop fail-closed · absent line = UNDECLARED (grandfathered, never retro-red) · enforcement live: a completing verify gate refuses an out-of-scope build (scope_violation → self-heal); check surfaces it. EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 35/35 `tests/guardrails/` (17 core + 18 ml_moderation) green, plus regression spot-checks `tests/openai_retry_parity/`, `tests/provider_seam/`, `tests/openai_chat_dispatch/` (41 passed) — confirmed by direct re-run against `gateway_test_vmlmod` (2026-07-10), not taken on the builder's word.
- [x] coverage did not decrease — `ml_moderation_evaluator.py` 92% (78 stmts, 6 missed: L154-155/229/245-246 trivial `__init__`/no-op bodies — AND L175, see refute-read below), `guardrail_tenant_context.py` 100% — re-measured independently, matches builder's claim.
- [x] no test or contract was altered during build — `git log`/`git diff` not re-run here (integrated branch), but §3 contract text is unchanged from the frozen v1 shown above and `test_guardrails_core.py` (pre-existing frozen suite) is untouched per the builder's own diff scope and stays green.
- [x] the green was EARNED, not gamed for 8 of 9 Musts — BUT refute-read surfaced ONE real gap (M6/A3 for the malformed-2xx-body case) — see Refute-read verdict below. NOT a clean EARNED.
- [x] concurrency / timing of the risky operation is safe — see attack target #1 below (probe test, deleted after confirming CLEAR).
- [x] no exposed secrets, injection openings, or unexpected dependencies — BUT see Finding 1 (a genuine security-relevant guardrail-bypass gap, not a secret/injection issue).
- [x] layering & dependencies follow CONVENTIONS.md — new public `OpenAIDirectProvider.post_json_with_retry()` (Deviation 2) avoids the private-attribute reach-in the illustrative §3 Python implied; confirmed via `WIRING` deep-check below.
- [ ] a person reviewed and approved the change — pending human gate (this task is `sensitivity: security`, HARD-STOP-class regardless of the rest of this checklist).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] a tenant with `ml_moderation` disabled sees no new outbound call and no latency regression — confirmed: `test_disabled_ml_moderation_byte_identical` (ml_provider.calls==0, "openai" absent from resolver.calls) green; code inspection of `ml_moderation_evaluator.py:172-175` shows the off-branch returns before any resolve/network call.
- [x] a flagged prompt in block mode never reaches the routed upstream provider — confirmed: `test_ml_moderation_blocks_flagged_prompt` (`upstream.calls == 0`) green.
- [x] an `unchecked` verdict is distinguishable from `passed` in both the emitted `GuardrailEvent` and the `gateway_guardrail_events_total` metric label — confirmed for the exception-based failure paths (missing key, timeout) via `test_ml_moderation_timeout_fail_open` (`action="unchecked"` >=1, `action="passed"` ==0) green. **NOT held** for the malformed-2xx-body path — see Finding 1 (a genuinely-unchecked case is misreported as `passed`, the exact inversion this checkbox exists to prevent).
- [x] the moderation breaker and the real-completion breaker are provably separate objects — confirmed by identity check: `test_ml_moderation_breaker_isolated_from_completion_breaker` (`is not` on both `_breaker` and `_client`) green, AND independently confirmed at the real wiring site — `main.py:1085-1092` constructs a fresh `OpenAIDirectProvider(...)` for `app.state.ml_moderation_provider`, distinct from `_openai_direct` used for `_providers["openai"]`/chat (not merely asserted in an isolated unit test — the actual boot wiring was read).
- [ ] a request whose moderation call times out under `fail_open` completes within the declared latency budget (connect+read+retry ceiling), not the full chat-completion timeout — **UNCONFIRMED**: `test_ml_moderation_timeout_fail_open` uses a `FakeModerationProvider` that raises `httpx.ReadTimeout` synchronously (no real elapsed time), so `MODERATION_RETRY_DEADLINE_S=4.0`/`MODERATION_MAX_RETRIES=1`/backoff wiring is never exercised end-to-end with a real-time bound anywhere in this suite. The underlying `execute_with_retry` deadline primitive is separately tested generically in `tests/retry_policy/`, giving baseline confidence, but this specific config's actual bound is not independently measured. MINOR residual gap, named not silently dropped.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced: `MlModerationGuardrailEvaluator`/`CompositeGuardrailEvaluator`/`OpenAiModerationClient` constructed+used in `deps.py:get_completion_use_case` (confirmed by reading the wiring branch) and `main.py:1085`; `guardrail_tenant_context.set_/reset_guardrail_tenant_id` referenced at both `use_cases.py` call sites (L1756/1816 `complete()`, L2330/2374 `stream()` — confirmed via grep, both wrapped in `finally`); `OpenAIDirectProvider.post_json_with_retry` has exactly one caller (`OpenAiModerationClient.moderate`) — confirmed via grep, no orphan.
- [x] DEAD-CODE (code) — no new unused symbol; `evaluate_post` no-op is structurally required by the `GuardrailEvaluator` Protocol, not orphaned.
- [ ] SEMANTIC (prose / non-code) — n/a, this task is code-only.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed via direct grep/read against the integrated `chore/add-housekeeping-clusters` tree (not the Ground SHA): `GuardrailEvaluator` (`ports.py:210`, exact match), `TenantCredentialResolver` (`ports.py:462`, exact match), `RegexGuardrailEvaluator` (`guardrail_evaluator.py:439`, class def — §0 cited 402/439 for methods, consistent), `OpenAIDirectProvider` (`openai_provider.py:43`, exact match), `get_completion_use_case` (`deps.py:112`, §0 cited 107 — 5-line drift), `GUARDRAIL_BLOCKED` (`error_catalog.py:397`, §0 cited 391 — 6-line drift), `PAYLOAD_CUSTOM_PATTERN_INVALID` (`error_catalog.py:311`, §0 cited 305 — 6-line drift).
- [x] any anchor that moved/renamed since Ground SHA is named here, not left silent — no rename/move; only small (5-6 line) upward drift on 3 `error_catalog.py`/`deps.py` anchors, consistent with other code shifting above them, not a stale/wrong-symbol risk.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: **NOT-EARNED** (partial — 8/9 Musts genuinely earned; M6/A3 has a real hole)
By: self (add-verify) · adversarially checked:
  1. BYOK credential ContextVar nesting under concurrency (dispatcher's #1 target) — wrote and ran a throwaway probe (nested same-task set/reset restores the exact outer value via Python's `Token`-based reset; two genuinely-concurrent `asyncio.Task`s never observe each other's credential). **CLEAR** — Python's `contextvars.Token.reset()` is not a naive "restore a snapshot" — it restores precisely the value live at the paired `.set()` call, and each `asyncio.Task` gets its own copied `Context`. Probe passed both assertions; deleted after confirming (not a defect repro).
  2. Honest degradation across every M6 failure mode (missing key, breaker-open, timeout, network error, non-2xx) — 4 of 5 confirmed CLEAR via the existing green suite. The 5th — **a 200 response whose body doesn't match the expected `{"results":[{...}]}` shape** — is NOT caught: `OpenAiModerationClient.moderate()` (`ml_moderation_evaluator.py:134-138`) does `results = body.get("results") or [{}]`; `result = results[0] if results else {}`; on an empty/malformed body this silently yields `ModerationVerdict(flagged=False, categories=[])` — no exception raised, so `MlModerationGuardrailEvaluator` records `action="passed"` for content that was never actually classified. Wrote a minimal repro (`tests/guardrails/test_zz_verify_repro_malformed200.py`) asserting `moderate()` must raise on a `200 {}` body — **FAILS** (`Failed: DID NOT RAISE <class 'Exception'>`), confirming the gap is real, not theoretical.
  3. Dedicated breaker isolation under load — confirmed via the existing `is not` unit test AND independently via the real `main.py:1085` boot-wiring (fresh instance, not reused). **CLEAR**.
  4. Non-2xx-from-moderation handling — the ≥400 branch is genuinely CLEAR (`test_openai_moderation_client_raises_on_terminal_non_2xx` green, independently re-run). The malformed-200 branch is the gap in item 2 above — same code path the dispatcher's attack target #4 named ("can an oddly-shaped 200 be misread as unchecked... never checked").
  5. Default-off byte-identical — CLEAR for the fully-absent-`guardrail_configs` case (tested, green). One earned-green coverage gap found: the "ml_moderation key absent from an otherwise-non-empty `guardrail_configs` dict" branch (`ml_moderation_evaluator.py:172-175`) is never actually exercised by any test — every "disabled" scenario in the suite uses a fully-empty config that `use_cases.py` short-circuits before calling `evaluate_pre` at all (confirmed: this exact line shows up in the coverage-missed set). Code is visually correct on inspection; MINOR, not blocking, but a real untested line.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: self (add-verify)
1. Security: **HARD-STOP: a malformed/unexpectedly-shaped 200 response from the moderation endpoint (`ml_moderation_evaluator.py:134-138`) is silently read as a clean verdict (`flagged=False`) rather than a failed check — this defeats block-mode moderation with ZERO observability** (the event/metric records `action="passed"`, never `action="unchecked"`, so A3's "always observable" guarantee and M6's "never reported as passed" guarantee both fail for this one input class). A plausible trigger: any operational drift (OpenAI wire-shape change, a misrouted/misconfigured `openai_base_url`, a caching/CDN layer between the gateway and the moderation endpoint returning a benign 200) silently and invisibly disables the safety check while the tenant believes it is enforced — worse than the intentionally-designed `unchecked` degrade path because it is indistinguishable from a genuine pass. Repro: `tests/guardrails/test_zz_verify_repro_malformed200.py` (red, `DID NOT RAISE`).
2. Concurrency: (not evaluated — Security HARD-STOP ends the checklist per instructions)
3. Architecture: (not evaluated — Security HARD-STOP ends the checklist per instructions)
Verdict: **HARD-STOP**
Residue: Finding 1 (malformed-200 silent-pass) is a confirmed, reproduced security-relevant gap in the moderation feature's own core honest-degradation invariant. Two MINOR residues also on record (untested audit+fail_closed+unchecked matrix cell; untested absent-key-in-nonempty-config branch; unconfirmed real-time latency-budget assertion) — none of these three are independently blocking, but are named so they are not silently dropped.
Binding: yes — mechanical (sensitivity: security)

### GATE RECORD
Reported: no — orchestrator records the gate outcome, not this agent.
Outcome: <the orchestrator records exactly one of PASS | RISK-ACCEPTED | HARD-STOP — this agent's recommendation is HARD-STOP, per the Advisor 3-lens verdict above>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap — N/A here, this is a confirmed security finding)
Reviewed by: <pending human> · date: <pending>

<!-- Security is ALWAYS HARD-STOP; record exactly one outcome — no silent pass. The Advisor 3-lens and Refute-read verdicts are audit-measured (`advisor_verdict_unrecorded` · `refute_unrecorded`), never engine-blocked; a human spot-audit backstops anything unrecorded. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): `gateway_guardrail_events_total{guardrail="ml_moderation"}` broken out by `action` — watch the `unchecked` rate specifically (a sustained non-zero `unchecked` rate for a tenant means their moderation is silently degraded, e.g. missing key or provider outage) alongside the moderation-dedicated breaker's open/close transitions and the added p50/p99 latency on `ml_moderation`-enabled tenants' completion requests.

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).
- [SPEC · seeded] post-call (response) ML moderation is out of this task's v1 — a named follow-up once pre-call moderation is live and its false-positive/latency profile is observed (evidence: FREEZE-QUESTION 3).
- [SPEC · seeded] moderation-provider breadth beyond OpenAI (Anthropic/Gemini safety scoring, Bedrock Guardrails) — Framing C, deliberately deferred, not silently dropped (evidence: §1 Framings weighed).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
- [DDD · open] a guardrail check that performs real outbound IO needs a THIRD verdict state (`unchecked`) beyond the deterministic checks' `passed`/`blocked`/`audited` vocabulary, plus its own config axis (`failure_mode`, orthogonal to `mode`) — the first guardrail in this codebase with an external failure mode of its own (evidence: §1 M6, Glossary delta "Unchecked").
- [SDD · open] a BYOK provider used for an ANCILLARY IO seam (moderation) needs an ISOLATED CircuitBreaker/client instance from the SAME provider's PRIMARY seam (chat completions) — sharing one adapter instance across two independent failure domains would cross-contaminate breaker state; worth a general pattern note for any future secondary use of an existing provider adapter (evidence: §0 R3, §1 M8).

---


## Design self-score

- Completeness: 0.93 — §0 anchors every symbol the contract cites (Protocol, evaluator, breaker, retry helper, BYOK resolver, wiring seam, error catalog, metrics, sibling-task coupling point); §1 states 9 Musts + 4 Rejects + 4 After-states; §2 covers one scenario per Must/Reject plus 5 edge cases (breaker-open fast path, breaker isolation, PII-before-moderation ordering, composite precedence, wiring-absent default); §3 gives an endpoint delta, schema note, and hand-sanity-checked illustrative Python for every new class. Residual gap: exact latency numbers and the OpenAI moderation pricing/shape are explicitly flagged unverified (see ⚠), not silently assumed complete.
- Clarity: 0.92 — every Must/Reject/scenario is numbered and cross-referenced (`# M4, R3` style); the contract's illustrative code is grouped by responsibility (port → client → evaluator → composite → wiring) rather than one undifferentiated block.
- Practicality: 0.93 — the design is almost entirely REUSE: `CircuitBreaker`, `execute_with_retry`, `OpenAIDirectProvider.post_json`, `TenantCredentialResolver.resolve`, `GUARDRAIL_BLOCKED`, `guardrail_events_total` are all used verbatim; the only genuinely new code is the evaluator/composite/client classes and one additive Pydantic block mirroring an already-shipped extension (pii-v2). No new migration, no new HTTP client library, no new error code.
- Optimization: 0.90 — the hot-path latency tradeoff is surfaced with concrete (if unverified) budget numbers, the breaker-open fast-path avoids wasted retry latency, and PII-masked-before-moderation reduces third-party data egress as a free side-effect of composite ordering. Held at 0.90 rather than higher because the actual numbers (FREEZE-QUESTION 2) are a proposal, not a measurement.
- Edge cases: 0.91 — covers the COMMON case correctly (missing BYOK key, not a rare edge — R2), breaker isolation (a correctness bug this draft catches before it ships, not just after), fail-open/fail-closed crossed with block/audit (4-way matrix), and composite-ordering interactions with the pre-existing regex checks (both the blocking-precedence case and the PII-exposure case).
- Self-evaluation: 0.92 — 2 framings explicitly rejected with reasons (not just the chosen one narrated); 5 assumptions ranked lowest-confidence-first with cost-if-wrong stated for the ⚠; 4 freeze questions each carry this draft's own recommendation (never a bare open question); 2 competency deltas record what this task teaches the next one.

All six ≥0.90 — no refinement loop triggered.
