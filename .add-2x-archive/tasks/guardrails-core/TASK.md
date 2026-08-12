# TASK: Guardrail framework + prompt-injection and PII guardrails

slug: guardrails-core · created: 2026-06-11 · stage: production
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Per-tenant guardrail framework — pre/post-call hooks with prompt-injection heuristic
         (BLOCK|MASK|AUDIT) and PII detect/mask (MASK|AUDIT) guardrails

Framings weighed:
- **per-tenant JSONB config on tenants table** (chosen): add a `guardrail_configs JSONB
  NOT NULL DEFAULT '{}'` column to tenants. Config is small (a handful of guardrail entries),
  read at auth time alongside other tenant fields, and JSONB lets the schema evolve without
  a migration per new guardrail. No new table; EXPECTED_TABLES unchanged. Tradeoff: JSONB
  is less query-indexable than a normalized table, but guardrail configs are never queried
  by value — only fetched by tenant_id. This matches how most LiteLLM-style proxies store
  per-tenant guardrail toggles (flat blob, not relational rows).
- guardrail_configs as a separate normalized table (rejected): a separate table (tenant_id,
  guardrail_name, mode, enabled) would enable per-guardrail indexing and JOIN-based queries,
  but guardrail evaluation happens in-process (no DB query at eval time), and the only DB
  access pattern is "load all configs for this tenant." The join complexity adds no value;
  the migration adds a new table (enlarging EXPECTED_TABLES, a test-invasive change). JSONB
  wins on simplicity for v4.
- in-memory TTL cache for guardrail configs (chosen for config-carrier strategy): carry
  guardrail_configs on AuthzResult as a new field `guardrail_configs: dict[str, Any]`.
  The auth path already LEFT JOINs tenants (for cache_enabled); adding `guardrail_configs`
  to the same select is zero extra DB reads. Evaluated in-process on each request.
- per-request DB read for guardrail config (rejected): adds a DB call on the hot path;
  violates the zero-extra-DB-reads contract established by response-caching.
- separate GuardrailEvaluator domain service injected into CompletionUseCase (chosen):
  mirrors the ResponseCache port pattern — a `GuardrailEvaluator` Protocol in
  proxy/domain/ports.py with a `RegexGuardrailEvaluator` infrastructure implementation.
  Injection via CompletionUseCase constructor with default-None (backward-compatible with
  all frozen test fakes). This is the established seam pattern (third application).

Must:
<must>
  - tenants gains a `guardrail_configs JSONB NOT NULL DEFAULT '{}'::jsonb` column
    (additive migration after 1c4e7a9f3b2d). Schema shape per tenant stored in the column:
      {
        "prompt_injection": { "enabled": bool, "mode": "block"|"mask"|"audit" },
        "pii_mask":         { "enabled": bool, "mode": "mask"|"audit" }
      }
    Missing key = that guardrail is disabled. Both guardrails are disabled by default
    (empty object default).

  - AuthzResult gains `guardrail_configs: dict[str, Any] = {}` (additive, default empty dict).
    Populated at auth time from the tenants row via the existing LEFT JOIN tenants in
    get_by_id() — zero extra DB reads. The JSONB value is deserialized from Postgres as a
    Python dict.

  - Admin config API (mirrors /admin/cache style):
      GET  /admin/guardrails — any authenticated role; returns the current guardrail config
           for the tenant as a JSON object matching the schema above.
           200 → { "prompt_injection": { "enabled": bool, "mode": str } | null,
                   "pii_mask":         { "enabled": bool, "mode": str } | null }
      PUT  /admin/guardrails — owner or admin only; member → 403 ERR_AUTH_FORBIDDEN.
           Body: same shape as GET response (partial update: absent top-level keys preserve
           existing values; present keys fully replace that guardrail's config).
           200 → echoes the full stored config after the update.
      Invalid mode value → 422 ERR_PAYLOAD_INVALID.

  - GuardrailEvaluator Protocol in proxy/domain/ports.py (additive, zero framework imports):
      class GuardrailEvaluator(Protocol):
          async def evaluate_pre(
              self,
              messages: list[dict[str, Any]],
              guardrail_configs: dict[str, Any],
          ) -> GuardrailResult: ...
    GuardrailResult dataclass (domain entity in proxy/domain/entities.py):
      @dataclass(frozen=True)
      class GuardrailResult:
          blocked: bool                      # True = request must be rejected
          masked_messages: list[dict] | None # Non-None when PII was masked
          events: list[GuardrailEvent]       # Zero or more events
      @dataclass(frozen=True)
      class GuardrailEvent:
          guardrail: str      # "prompt_injection" | "pii_mask"
          action: str         # "blocked" | "masked" | "audited" | "passed"
          detail: str         # human-readable; empty string if no detail

  - Pre-call guardrail enforcement in CompletionUseCase.complete() and .stream():
      Position: AFTER _enforce_governance, BEFORE upstream call (step 4.5 in enforcement order).
      Injected as `guardrail_evaluator: GuardrailEvaluator | None = None` constructor param
      (default-None = no-op; backward-compatible with frozen test fakes).
      If evaluator is not None and guardrail_configs is non-empty:
        result = await evaluator.evaluate_pre(messages, authz.guardrail_configs)
        If result.blocked:
          fire usage record (status=400, usage=None) — blocked requests are still counted
          raise ProblemError(400, "ERR_GUARDRAIL_BLOCKED", "Request blocked by guardrail policy")
        If result.masked_messages is not None:
          replace the messages field in body with result.masked_messages before upstream call.

  - Post-call guardrail enforcement: pre-call guardrails apply to stream requests identically
    to non-streaming. Post-call guardrails (inspecting the upstream response body for PII)
    are a documented limitation in v4: NOT implemented for streaming. For non-streaming:
    post-call PII masking in MASK mode is applied to the upstream 200 response content field
    before returning to the client. The post-call evaluator uses the same GuardrailEvaluator
    protocol (evaluate_post method, additive — guarded by hasattr seam).

  - Fail-CLOSED (BLOCK mode): any error during GuardrailEvaluator.evaluate_pre() when any
    guardrail is in "block" mode → the request is blocked (ProblemError 400
    ERR_GUARDRAIL_BLOCKED). If all active guardrails are in "mask" or "audit" mode, an
    evaluator error is logged and the request proceeds (fail-OPEN for MASK/AUDIT).

  - Prompt-injection heuristic — exact pattern families (deterministic, regex-based, no ML):
    Blocked patterns (matched case-insensitively against each message's "content" field):
      1. "ignore (previous|prior|all|your|any) (instructions|rules|prompt|context|guidelines)"
      2. "disregard (previous|prior|all|your|any) (instructions|rules|prompt|context|guidelines)"
      3. "forget (previous|prior|all|your|any) (instructions|rules|prompt|context|guidelines)"
      4. "you are now" followed by a role description on the same line
         (regex: r"you are now\s+\w")
      5. "act as" / "pretend to be" / "roleplay as" / "simulate being"
         (regex: r"(act as|pretend to be|roleplay as|simulate being)\s+\w")
      6. "new (instructions|rules|persona|task|objective|goal):"
         (regex: r"new\s+(instructions|rules|persona|task|objective|goal)\s*:")
      7. "system:" appearing in a non-system role message's content
         (regex: r"(?i)\bsystem\s*:")
    A match on ANY pattern in ANY message = injection detected.
    In BLOCK mode: pre-call block (400 ERR_GUARDRAIL_BLOCKED). Fail-CLOSED on eval error.
    In AUDIT mode: log event, pass through. Fail-OPEN on eval error.
    (MASK mode is not applicable to prompt injection — injection does not benefit from masking.)

  - PII detect/mask heuristic — exact replacement literals (deterministic, regex-based):
    Detected PII types and their EXACT replacement strings:
      EMAIL      regex: r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
                 replacement: "[EMAIL_REDACTED]"
      PHONE      regex: r"(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}"
                 replacement: "[PHONE_REDACTED]"
      CREDIT_CARD regex: r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|6(?:011|5[0-9]{2})[0-9]{12})\b"
                 replacement: "[CREDIT_CARD_REDACTED]"
      SSN        regex: r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b"
                 replacement: "[SSN_REDACTED]"
    In MASK mode: replace all matches in each message's "content" field before upstream call.
      Masking error → log + pass through original (fail-OPEN).
    In AUDIT mode: log detected PII types per message, pass through unmodified. Fail-OPEN.
    Post-call MASK: also applies to non-streaming upstream 200 response
      choices[*].message.content — exact same regex set. Streaming: NOT applied in v4
      (documented limitation, audit-logged at stream start when PII mode=mask+audit is active).

  - Prometheus counter: `gateway_guardrail_events_total{guardrail, mode, action}`
    labels:
      guardrail: "prompt_injection" | "pii_mask"
      mode:      "block" | "mask" | "audit"
      action:    "blocked" | "masked" | "audited" | "passed" | "error"
    Added to MetricsRegistry alongside existing counters.

  - Usage record marker: when a request is blocked by a guardrail, the usage record has
    status=400 and raw includes `guardrail_blocked: true`. When PII is masked pre-call,
    raw includes `pii_masked: true`. These are additive raw dict fields, not new columns.

  - Enforcement order (full, pinning guardrails' position):
      1. Authentication (_authenticate)
      2. Payload validation (_validate_payload)
      3. Governance (_enforce_governance): expiry → model allowlist → catalog → per-key budget
         → team budget → tenant budget → RPM → TPM
      4. Pre-call guardrails (NEW — AFTER governance): prompt-injection check, then PII mask
         4a. If BLOCK mode guardrail error → 400 ERR_GUARDRAIL_BLOCKED (usage recorded, status=400)
         4b. If MASK mode guardrail: mutate messages in body dict before step 5
      5. Upstream call
      6. Post-call guardrails (NEW — non-streaming only): PII mask on response content field
      7. Cache store (fire-and-forget on 200, after upstream responds) [if cache enabled]
      8. Usage recording (_fire_record)
      9. TPM post-accounting (_fire_record_tpm)

  - Cache interplay:
      Pre-call guardrails run on cache hits: the guardrail evaluates the request before
        cache lookup — a cached response for a prompt-injection payload should still be
        blocked (governance already runs before cache; guardrails slot in at step 4, before step 4.5).
        UPDATED enforcement order: guardrails at step 4, cache lookup at step 4.5.
      Post-call PII mask applies to cached bodies: if a cached body is returned and the
        tenant has pii_mask in MASK mode, the cached body's choices[*].message.content is
        masked before returning to the client.
      Streaming: pre-call guardrails run (both prompt-injection and PII checks on messages).
        Post-call guardrails do NOT apply to stream bodies (documented limitation).
        When pii_mask mode=mask is active on a stream request, a one-time audit-log event
        is emitted ("streaming_pii_mask_skipped") and the stream proceeds unmodified.
</must>

Reject:
<reject>
  - prompt-injection payload (matching any contracted pattern) with guardrail mode=block →
    "ERR_GUARDRAIL_BLOCKED" (400)
  - prompt-injection payload with guardrail mode=audit → request passes through, no block;
    guardrail event logged; upstream sees the original payload
  - PII-bearing message (email/phone/CC/SSN) with pii_mask mode=mask → upstream sees
    masked content (e.g. "[EMAIL_REDACTED]"); post-call PII masking ALSO applies
    automatically to the non-streaming 200 response choices[*].message.content under the
    same mode=mask (there is NO separate post-call toggle — S15; orchestrator amendment
    for consistency with §3, 2026-06-11)
  - PUT /admin/guardrails with a member-role JWT → "ERR_AUTH_FORBIDDEN" (403)
  - PUT /admin/guardrails with an invalid mode value (e.g. "block" for pii_mask, since
    block is not a valid mode for pii_mask) → "ERR_PAYLOAD_INVALID" (422)
  - a non-200 upstream response when a guardrail is in BLOCK mode → the upstream 4xx passes
    through verbatim (guardrail only fires pre-call; post-call guardrail does not transform
    upstream error responses)
  - guardrail evaluator error when any guardrail is in BLOCK mode → "ERR_GUARDRAIL_BLOCKED"
    (400, fail-CLOSED)
  - guardrail evaluator error when all active guardrails are in MASK/AUDIT mode → logged,
    request proceeds (fail-OPEN)
</reject>

After:
<after>
  - tenants.guardrail_configs column exists as JSONB NOT NULL DEFAULT '{}'::jsonb.
  - AuthzResult.guardrail_configs field carries the deserialized dict from the tenant row.
  - GET /admin/guardrails returns the current guardrail config for the caller's tenant.
  - PUT /admin/guardrails stores and echoes the updated config; member → 403.
  - A prompt-injection payload sent to a tenant with prompt_injection mode=block is rejected
    with 400 ERR_GUARDRAIL_BLOCKED; upstream is never called; a usage row is recorded with
    status=400 and raw.guardrail_blocked=true.
  - The same payload sent to a tenant with prompt_injection mode=audit passes through;
    upstream is called; no block; a guardrail audit event is emitted.
  - A PII-bearing message (e.g. "call me at 555-123-4567") with pii_mask mode=mask reaches
    upstream with "[PHONE_REDACTED]" substituted; client receives the upstream response.
  - Post-call PII mask (non-streaming): upstream response choices[*].message.content has
    PII redacted before returning to the client when pii_mask mode=mask is active.
  - Prometheus counter gateway_guardrail_events_total increments for each guardrail event.
  - Streaming request with prompt_injection mode=block: injection payload → 400 block BEFORE
    any stream bytes are emitted.
  - Streaming request with pii_mask mode=mask: stream proceeds; one audit-log event emitted
    noting post-call masking was skipped for this stream; body NOT inspected.
  - Cache interplay: pre-call guardrails run even on cache-warm requests (before cache lookup);
    cached response bodies have post-call PII mask applied when pii_mask mode=mask is active.
  - Guardrail evaluator error in BLOCK mode → request blocked (fail-CLOSED).
  - Guardrail evaluator error in MASK/AUDIT mode → request proceeds (fail-OPEN).
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ POST-CALL PII MASK ON CACHE HITS [contract]: When a cache HIT occurs and pii_mask
    mode=mask is active, the cached body must be masked before returning to the client.
    The current cache HIT path in CompletionUseCase.complete() returns `cached_body` directly
    after recording usage. Inserting a post-call mask step on the cached body requires either
    (a) a post-cache-hit masking step in the hit branch, or (b) always storing already-masked
    bodies in cache. Option (a) chosen: mask is applied to the returned cached_body dict
    in-place before returning, leaving the Redis stored body unmodified (store the raw upstream
    body, mask on each hit). This means masking CPU cost is paid on every cache hit, but the
    mask is always freshest (config change takes effect immediately without cache invalidation).
    Cost if wrong (option b chosen at build review): the cached body would be masked once at
    store time; a subsequent config change to mode=audit would still serve masked bodies until
    TTL expiry. Option (a) is more conservative and correct. Confidence: 0.82.
    This is the lowest-confidence call because it requires adding a new code path to the
    existing cache HIT branch, which is in a frozen-test-adjacent area.

  ⚠ GUARDRAIL_CONFIGS JSONB VS. SEPARATE TABLE [spec]: The choice of a JSONB column on
    tenants rather than a normalized guardrail_configs table was made for simplicity and to
    avoid adding a new table to EXPECTED_TABLES. Risk: JSONB migration validation (alembic
    check + ORM parity test) may surface unexpected behavior if SQLAlchemy/asyncpg JSON
    deserialization behaves differently than expected (e.g. returns str instead of dict on
    old versions). Mitigation: the migration adds `server_default=text("'{}'::jsonb")`
    and the ORM maps it as `Mapped[dict]` with `sa.JSON`. If the Postgres JSONB column
    returns a string instead of dict, the build must handle deserialization explicitly.
    Confidence: 0.85; lowest because this is the first JSONB column in the schema.
    Cost if wrong: the build adds an explicit `json.loads()` call; no contract change needed.

  - pii_mask mode="block" is intentionally INVALID in v4 (PII block = block all requests
    with PII, which is too broad; masking is the right primitive). The validator rejects it
    with 422. Confidence: 0.95. If wrong (user wants block-on-PII): change request to v5,
    extend mode enum, no current-contract change.

  - The `evaluate_pre` method handles both prompt-injection AND pii_mask in a single call,
    returning a GuardrailResult with blocked=True (injection in BLOCK mode) and/or
    masked_messages (PII in MASK mode). Having a single evaluate_pre call is cleaner than
    two separate port methods and matches LiteLLM's pre_call_hook pattern. Confidence: 0.92.
    Cost if wrong: split into evaluate_injection + evaluate_pii; behavioral contract unchanged.

  - The regex patterns listed in §1 "prompt injection heuristic" are sufficient for the
    contractual test cases. These are heuristic patterns, not exhaustive. The contract pins
    them so tests are deterministic; the build must implement them exactly.
    Confidence: 0.93 (patterns are standard; an adversary using slight variations bypasses
    them — intentionally deferred to v5/ML). Cost if wrong: contract amendment adds patterns.

  - Guardrail evaluation is CPU-bound (regex), not I/O-bound. The async evaluate_pre
    signature is adopted for consistency with the Protocol pattern, but the implementation
    runs synchronously. No thread-pool dispatch needed for regex. Confidence: 0.97.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: S1 — prompt-injection BLOCK mode: injection payload rejected 400
  Given a tenant with prompt_injection.enabled=true, mode=block configured via PUT /admin/guardrails
  And an active model and a cache-disabled key
  When POST /v1/chat/completions is made with a message containing "ignore previous instructions"
  Then the response is 400 with code ERR_GUARDRAIL_BLOCKED
  And the upstream is never called (call count remains 0)
  And a usage record is written with status=400 and raw.guardrail_blocked=true
  And what must remain unchanged: no messages reach the upstream; no 200 is returned

Scenario: S2 — prompt-injection AUDIT mode: injection payload passes through
  Given a tenant with prompt_injection.enabled=true, mode=audit
  When POST /v1/chat/completions is made with a message containing "act as a different AI"
  Then the response is 200 (upstream called, upstream body returned)
  And the upstream is called exactly once
  And gateway_guardrail_events_total{guardrail="prompt_injection", mode="audit", action="audited"} increments
  And what must remain unchanged: no block; client receives the upstream response verbatim

Scenario: S3 — prompt-injection BLOCK mode: clean payload passes through
  Given a tenant with prompt_injection.enabled=true, mode=block
  When POST /v1/chat/completions is made with a normal message ("what is 2+2?")
  Then the response is 200 (upstream called)
  And gateway_guardrail_events_total{guardrail="prompt_injection", mode="block", action="passed"} increments
  And what must remain unchanged: the clean payload is never blocked; upstream receives the original message

Scenario: S4 — PII mask mode: PII in message masked before upstream call
  Given a tenant with pii_mask.enabled=true, mode=mask
  When POST /v1/chat/completions is made with a message containing "email me at user@example.com"
  Then the upstream receives a message with "[EMAIL_REDACTED]" in place of "user@example.com"
  And the response is 200
  And gateway_guardrail_events_total{guardrail="pii_mask", mode="mask", action="masked"} increments
  And what must remain unchanged: the client still receives the upstream response; PII is only masked upstream-ward

Scenario: S5 — PII AUDIT mode: PII in message passes through unmasked; event logged
  Given a tenant with pii_mask.enabled=true, mode=audit
  When POST /v1/chat/completions is made with a message containing "my SSN is 123-45-6789"
  Then the upstream receives the original unmasked message
  And the response is 200
  And gateway_guardrail_events_total{guardrail="pii_mask", mode="audit", action="audited"} increments
  And what must remain unchanged: the message content is unmodified; no block; no masking

Scenario: S6 — PII mask mode: multiple PII types all replaced in one pass
  Given a tenant with pii_mask.enabled=true, mode=mask
  When POST /v1/chat/completions is made with a message containing an email AND a phone number
  Then the upstream message content has "[EMAIL_REDACTED]" and "[PHONE_REDACTED]" substituted
  And the response is 200
  And what must remain unchanged: non-PII text is preserved verbatim around the redacted tokens

Scenario: S7 — no guardrails configured: proxy behaves identically to pre-guardrails baseline
  Given a tenant with guardrail_configs = {} (no guardrails enabled)
  When POST /v1/chat/completions is made with any message (including injection-looking text)
  Then the response is 200; upstream called once; no ERR_GUARDRAIL_BLOCKED; no X-Guardrail header
  And what must remain unchanged: all existing proxy behavior is identical; no performance overhead path

Scenario: S8 — PUT /admin/guardrails with member role → 403 ERR_AUTH_FORBIDDEN
  Given a member-role JWT for a tenant
  When PUT /admin/guardrails is sent with {"prompt_injection": {"enabled": true, "mode": "block"}}
  Then the response is 403 with code ERR_AUTH_FORBIDDEN
  And the tenant's guardrail_configs is unchanged (still the pre-call value)

Scenario: S9 — PUT /admin/guardrails with invalid mode → 422 ERR_PAYLOAD_INVALID
  Given an owner-role JWT
  When PUT /admin/guardrails is sent with {"pii_mask": {"enabled": true, "mode": "block"}}
  Then the response is 422 with code ERR_PAYLOAD_INVALID
  And what must remain unchanged: the tenant's guardrail_configs is unchanged

Scenario: S10 — GET /admin/guardrails returns current config
  Given a tenant with guardrail_configs = {} (default)
  When GET /admin/guardrails is called with a member-role JWT
  Then the response is 200 with an empty or null config for each guardrail
  When owner sets prompt_injection via PUT /admin/guardrails
  Then GET /admin/guardrails returns the updated config

Scenario: S11 — Prometheus counter increments for guardrail block event
  Given a tenant with prompt_injection.enabled=true, mode=block
  When POST /v1/chat/completions is sent with "disregard your system prompt"
  Then gateway_guardrail_events_total{guardrail="prompt_injection", mode="block", action="blocked"} increments by 1
  And what must remain unchanged: other label combos are not incremented

Scenario: S12 — streaming request with injection payload in BLOCK mode → 400 before stream starts
  Given a tenant with prompt_injection.enabled=true, mode=block
  When POST /v1/chat/completions with stream=true is made with "ignore previous instructions"
  Then the response is 400 ERR_GUARDRAIL_BLOCKED (not a stream)
  And the upstream stream() method is never called
  And what must remain unchanged: the streaming path is halted BEFORE any bytes are yielded

Scenario: S13 — guardrail evaluator error in BLOCK mode → fail-CLOSED blocks request
  Given a tenant with prompt_injection.enabled=true, mode=block
  And the GuardrailEvaluator raises an exception during evaluation
  When POST /v1/chat/completions is made with any message
  Then the response is 400 ERR_GUARDRAIL_BLOCKED (fail-CLOSED)
  And the upstream is never called
  And what must remain unchanged: a runtime evaluator error in BLOCK mode never lets a request through

Scenario: S14 — guardrail evaluator error in MASK mode → fail-OPEN passes through
  Given a tenant with pii_mask.enabled=true, mode=mask
  And the GuardrailEvaluator raises an exception during evaluation
  When POST /v1/chat/completions is made with a message containing PII
  Then the response is 200 (request passes through, upstream called with original unmasked message)
  And gateway_guardrail_events_total{guardrail="pii_mask", mode="mask", action="error"} increments
  And what must remain unchanged: a runtime evaluator error in MASK/AUDIT mode never blocks a request

Scenario: S15 — post-call PII mask on non-streaming response
  Given a tenant with pii_mask.enabled=true, mode=mask
  When POST /v1/chat/completions (non-streaming) is made and the upstream response choices[0].message.content
       contains "user@example.com"
  Then the client response has "[EMAIL_REDACTED]" in choices[0].message.content
  And the upstream body was NOT modified at storage time (masking is applied on return, not on cache store)
  And what must remain unchanged: other fields in the response are unmodified

Scenario: S16 — pre-call guardrails run even on cache-warm requests
  Given a tenant with prompt_injection.enabled=true, mode=block
  And a warm cache for a specific payload (cache hit path)
  When POST /v1/chat/completions is made with an injection payload matching a cached key
  Then the response is 400 ERR_GUARDRAIL_BLOCKED (guardrail fires BEFORE cache lookup)
  And the cached body is NOT returned
  And what must remain unchanged: cache warmth does not bypass guardrail enforcement

Scenario: S17 — new guardrail migration: guardrail_configs column exists with correct default
  Given the DB schema is at migration head
  When a new tenant is created
  Then the tenants.guardrail_configs column exists and the new tenant row has '{}'::jsonb as its value
  And what must remain unchanged: EXPECTED_TABLES manifest is unchanged (no new table added)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/guardrails
  200 -> {
    "prompt_injection": { "enabled": bool, "mode": "block"|"audit" } | null,
    "pii_mask":         { "enabled": bool, "mode": "mask"|"audit" }  | null
  }
  401 -> { "code": "ERR_AUTH_INVALID_KEY" }

PUT /admin/guardrails   body: {
    "prompt_injection"?: { "enabled": bool, "mode": "block"|"audit" },
    "pii_mask"?:         { "enabled": bool, "mode": "mask"|"audit" }
  }
  Semantics: partial update — present keys replace that guardrail's config; absent keys
             are preserved (merged with existing config). Sending
             {"prompt_injection": null} disables / removes that guardrail.
  200 -> { full merged config (same shape as GET 200) }
  403 -> { "code": "ERR_AUTH_FORBIDDEN" }     # member role
  422 -> { "code": "ERR_PAYLOAD_INVALID" }    # invalid mode value or schema violation

POST /v1/chat/completions — GUARDRAIL BLOCK (any mode=block guardrail fires)
  400 -> { "type": "about:blank", "title": "...", "status": 400,
           "code": "ERR_GUARDRAIL_BLOCKED" }
  (Existing 401/402/403/422/429/502 codes unchanged)

POST /v1/chat/completions — GUARDRAIL MASK (mode=mask guardrail fires, no block)
  200 -> upstream body with masked content (upstream-facing body had PII redacted;
         response to client has post-call mask applied to choices[*].message.content)

Schema:
  tenants: ADD COLUMN guardrail_configs JSONB NOT NULL DEFAULT '{}'::jsonb
  Migration ID: <new_id>_guardrails_core (after 1c4e7a9f3b2d)
    Rollback: DROP COLUMN tenants.guardrail_configs
    (safe — additive column with server default; no existing code references it pre-migration)
  EXPECTED_TABLES: UNCHANGED (no new table — sanctioned, per §1 framing decision)

Config schema (stored in guardrail_configs JSONB column):
  {
    "prompt_injection": { "enabled": bool, "mode": "block"|"audit" },
    "pii_mask":         { "enabled": bool, "mode": "mask"|"audit" }
  }
  Absent key = guardrail is disabled (treated as {"enabled": false}).
  Mode constraint: pii_mask does NOT accept "block" mode → 422 if attempted.
  prompt_injection does NOT accept "mask" mode → 422 if attempted.

AuthzResult additive field:
  guardrail_configs: dict[str, Any] = {}
  Populated at auth time from tenants.guardrail_configs via the existing LEFT JOIN
  tenants in ApiKeyRepository.get_by_id() (already LEFT JOINs tenants for cache_enabled).
  No extra DB reads.

GuardrailEvaluator protocol (new, in proxy/domain/ports.py):
  class GuardrailEvaluator(Protocol):
      async def evaluate_pre(
          self,
          messages: list[dict[str, Any]],
          guardrail_configs: dict[str, Any],
      ) -> GuardrailResult: ...
      async def evaluate_post(
          self,
          response_body: dict[str, Any],
          guardrail_configs: dict[str, Any],
      ) -> dict[str, Any]: ...
      # evaluate_post returns the (possibly modified) response body

GuardrailResult (new domain entity, proxy/domain/entities.py):
  @dataclass(frozen=True)
  class GuardrailResult:
      blocked: bool
      blocked_by: str | None                  # guardrail name that triggered block, or None
      masked_messages: list[dict] | None      # None if no masking occurred
      events: list[GuardrailEvent]

  @dataclass(frozen=True)
  class GuardrailEvent:
      guardrail: str   # "prompt_injection" | "pii_mask"
      action: str      # "blocked" | "masked" | "audited" | "passed" | "error"
      detail: str      # empty string if no detail

RegexGuardrailEvaluator (new, in proxy/infrastructure/guardrail_evaluator.py):
  Implements GuardrailEvaluator. Stateless.
  evaluate_pre():
    1. If "prompt_injection" in guardrail_configs and enabled=true:
       Run all 7 pattern families (case-insensitive) against each message's "content".
       Match found:
         BLOCK mode → blocked=True, blocked_by="prompt_injection"
         AUDIT mode → event(action="audited"), not blocked
       No match: event(action="passed")
    2. If "pii_mask" in guardrail_configs and enabled=true:
       Run all 4 PII regexes against each message's "content". Replace all matches.
       Any replacement:
         MASK mode → masked_messages = updated copy of messages list
                     event(action="masked")
         AUDIT mode → event(action="audited"), masked_messages=None (original preserved)
       No PII found: event(action="passed")
    Returns GuardrailResult.
  evaluate_post():
    Apply PII masking (if pii_mask enabled+mode=mask) to response_body
    choices[*].message.content. Returns the modified body (deep-copied).
    On error: logs + returns original body (fail-OPEN — post-call masking is always MASK/AUDIT).

  Fail-CLOSED logic (in evaluate_pre):
    If any exception during evaluation:
      Check if ANY active guardrail has mode=block:
        Yes → return GuardrailResult(blocked=True, ...)
        No  → log warning, return GuardrailResult(blocked=False, events=[GuardrailEvent(action="error", ...)])

CompletionUseCase changes (additive):
  Constructor gains: `guardrail_evaluator: GuardrailEvaluator | None = None`
  complete() and stream() gain pre-call guardrail step (step 4, after governance):
    if guardrail_evaluator is not None and authz.guardrail_configs:
        result = await guardrail_evaluator.evaluate_pre(messages, authz.guardrail_configs)
        _fire_guardrail_metrics(metrics_registry, result.events)
        if result.blocked:
            _fire_record(usage_recorder, ..., status=400, guardrail_blocked=True)
            raise ProblemError(400, "ERR_GUARDRAIL_BLOCKED", "Request blocked by guardrail policy")
        if result.masked_messages is not None:
            body = {**body, "messages": result.masked_messages}  # mutate for upstream

  complete() gains post-call guardrail step (step 5.5, after upstream, non-streaming only):
    if guardrail_evaluator is not None and authz.guardrail_configs and status == 200:
        if hasattr(guardrail_evaluator, "evaluate_post"):
            response_body = await guardrail_evaluator.evaluate_post(response_body, authz.guardrail_configs)

  Post-call on cache HIT (step 4.5 in cache HIT branch, after returning cached_body):
    if guardrail_evaluator is not None and authz.guardrail_configs and cached_body is not None:
        if hasattr(guardrail_evaluator, "evaluate_post"):
            cached_body = await guardrail_evaluator.evaluate_post(cached_body, authz.guardrail_configs)
        return 200, cached_body, x_cache

Enforcement order (full, immutable):
  1. _authenticate
  2. _validate_payload
  3. _enforce_governance (expiry→allowlist→catalog→key budget→team budget→tenant budget→RPM→TPM)
  4. pre-call guardrails (NEW: prompt_injection check, then pii_mask)
  4.5. cache lookup (MISS/HIT/BYPASS) — AFTER guardrails, BEFORE upstream
  5. upstream call
  5.5. post-call guardrails (non-streaming, on 200 response body and on cache HIT body)
  6. cache store (fire-and-forget, 200 only — stores UNMASKED upstream body)
  7. usage recording
  8. TPM post-accounting

  Rationale for step 4 position (after governance, before cache):
    - Blocked requests have paid RPM; governance enforced; no upstream call wasted.
    - A cached response for an injection payload is still blocked (governance-equivalent).
    - Consistent with response-caching §3: "governance before cache lookup."

PII masking literals (exact — tests assert these strings verbatim):
  EMAIL       → "[EMAIL_REDACTED]"
  PHONE       → "[PHONE_REDACTED]"
  CREDIT_CARD → "[CREDIT_CARD_REDACTED]"
  SSN         → "[SSN_REDACTED]"

Prompt-injection pattern families (exact — tests use these strings to construct payloads):
  1. r"(?i)ignore\s+(previous|prior|all|your|any)\s+(instructions|rules|prompt|context|guidelines)"
  2. r"(?i)disregard\s+(previous|prior|all|your|any)\s+(instructions|rules|prompt|context|guidelines)"
  3. r"(?i)forget\s+(previous|prior|all|your|any)\s+(instructions|rules|prompt|context|guidelines)"
  4. r"(?i)you\s+are\s+now\s+\w"
  5. r"(?i)(act\s+as|pretend\s+to\s+be|roleplay\s+as|simulate\s+being)\s+\w"
  6. r"(?i)new\s+(instructions|rules|persona|task|objective|goal)\s*:"
  7. r"(?i)\bsystem\s*:"

Metrics:
  gateway_guardrail_events_total  Counter  labels: guardrail, mode, action
  Added to MetricsRegistry.__init__() alongside cache_events_total.

Usage record raw-field markers (additive dict fields, no new columns):
  On guardrail block:  raw["guardrail_blocked"] = True, raw["blocked_by"] = "<guardrail_name>"
  On PII mask (pre-call): raw["pii_masked"] = True
  Existing raw structure unchanged.

Admin guardrail router:
  New file: apps/gateway/src/gateway/tenants/api/guardrail_router.py
  APIRouter(prefix="/admin/guardrails", tags=["guardrails"])
  Uses get_identity / require_owner_or_admin from gateway.keys.api.deps (same as cache_router)
  DB access: SELECT guardrail_configs FROM tenants WHERE id = :tid
             UPDATE tenants SET guardrail_configs = :val WHERE id = :tid
  Input validation: Pydantic model GuardrailConfigRequest with per-guardrail validators
    (mode must be in allowed set for that guardrail type).

Modules touched (hard boundary — BUILD must not add new modules outside this list):
  - apps/gateway/src/gateway/proxy/domain/ports.py          (add GuardrailEvaluator protocol)
  - apps/gateway/src/gateway/proxy/domain/entities.py       (NEW: GuardrailResult, GuardrailEvent)
  - apps/gateway/src/gateway/proxy/application/use_cases.py (add guardrail step to complete+stream)
  - apps/gateway/src/gateway/proxy/infrastructure/          (NEW: guardrail_evaluator.py — RegexGuardrailEvaluator)
  - apps/gateway/src/gateway/proxy/api/deps.py              (wire RegexGuardrailEvaluator;
        PINNED override seam — get_completion_use_case reads
        getattr(request.app.state, "guardrail_evaluator", None) FIRST and uses it when set
        (S13/S14 inject ErrorGuardrailEvaluator this way), else constructs the default
        RegexGuardrailEvaluator — same app.state pattern as completion_upstream.
        Orchestrator amendment 2026-06-11.)
  - apps/gateway/src/gateway/observability/metrics.py       (add guardrail_events_total counter)
  - apps/gateway/src/gateway/keys/domain/entities.py        (add guardrail_configs to AuthzResult)
  - apps/gateway/src/gateway/keys/infrastructure/repository.py  (extend get_by_id() to fetch guardrail_configs)
  - apps/gateway/src/gateway/tenants/infrastructure/orm.py  (add guardrail_configs JSONB column to TenantRow)
  - apps/gateway/src/gateway/core/config.py                 (no changes needed — no new settings for v4)
  - apps/gateway/src/gateway/main.py                        (include guardrail_router)
  - apps/gateway/migrations/versions/<new>_guardrails_core.py
  - New: apps/gateway/src/gateway/tenants/api/guardrail_router.py
  - tests/migrations/test_migrations.py: EXPECTED_TABLES unchanged (no new table — only a column added)

EXPECTED_TABLES: UNCHANGED. The guardrail_configs column is additive to tenants; no new table.
No new Python packages; all implementation uses existing allowlist (re, json, copy).

Flags for freeze (lowest-confidence points across the bundle):
  ⚠ [contract] Post-call PII mask on cached body (step 5.5 in cache HIT branch):
    The current cache HIT path returns immediately after recording usage. Adding
    evaluate_post() call before the return requires modifying the cache HIT branch in
    CompletionUseCase.complete(). The frozen response-caching test suite (S2, S8) asserts on
    the HIT path returning the cached body "byte-identical to the first response." With
    post-call masking, the body returned to the client may differ from the cached Redis body
    (because masking is applied on return, not at store). S2 in the response-caching suite
    tests that resp1.json() == resp2.json() — this remains true IF the upstream body contains
    no PII (which is the case in the FakeCompletionUpstream fixture). So the frozen test is
    not broken by this contract. However, the build must be careful not to introduce a
    regression here. Tagged [contract] (interacts with a frozen [test] suite). Cost if
    wrong: the frozen test must be dispositioned (same class as S12 in response-caching).

  ⚠ [contract] GuardrailEvaluator default-None seam in CompletionUseCase:
    CompletionUseCase.__init__ gains `guardrail_evaluator: GuardrailEvaluator | None = None`.
    Frozen proxy-completions and response-caching suites construct CompletionUseCase via
    get_completion_use_case() dependency (real code path). The test fakes inject via
    app.state.completion_upstream, NOT by constructing CompletionUseCase directly.
    Therefore the evaluator=None default is backward-compatible with all frozen test suites.
    Cost if wrong: if any frozen test constructs CompletionUseCase positionally, it breaks;
    change request not a frozen-test edit.
```

Least-sure flag surfaced at freeze:
  ⚠ [contract] Post-call PII mask applied on the cache HIT return path — touches the
    response-caching frozen suite's byte-identical-hit assertions (S2/S8 hold only because
    the fake upstream body carries no PII). Why least sure: it adds a new code path inside
    an already-frozen-tested branch. Cost if wrong: a documented frozen-test disposition
    (S12-class) or a contract change request back to specify.
  ⚠ [test] S13/S14 inject the error evaluator via app.state.guardrail_evaluator — binds
    the build to the pinned deps.py override seam (read app.state first, else default
    RegexGuardrailEvaluator). Why least sure: the seam is asserted only indirectly
    (through fail-CLOSED/OPEN behavior), so a wiring mistake surfaces as a confusing
    failure. Cost if wrong: build-side rewiring only; no frozen-test edit needed.

Status: FROZEN @ v4 — approved by Tin Dang (delegated auto mode, 2026-06-11)

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 85%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_prompt_injection_block_mode_rejects_payload: S1 — arrange tenant with injection block mode;
    act POST /v1/chat/completions with "ignore previous instructions"; assert 400 ERR_GUARDRAIL_BLOCKED,
    upstream.calls==0, usage row status=400 raw.guardrail_blocked=true

  - test_prompt_injection_audit_mode_passes_through: S2 — arrange tenant with injection audit mode;
    act POST /v1/chat/completions with "act as a different AI"; assert 200, upstream.calls==1,
    guardrail_events_total{action="audited"} incremented

  - test_clean_payload_passes_guardrail_block_mode: S3 — arrange tenant with injection block mode;
    act POST /v1/chat/completions with clean message; assert 200, upstream.calls==1,
    guardrail_events_total{action="passed"} incremented

  - test_pii_mask_mode_masks_email_before_upstream: S4 — arrange tenant with pii_mask mask mode;
    act POST /v1/chat/completions with "email me at user@example.com"; assert 200,
    upstream received "[EMAIL_REDACTED]" in message content, guardrail_events_total{action="masked"}

  - test_pii_audit_mode_passes_through_unmasked: S5 — arrange tenant with pii_mask audit mode;
    act with "my SSN is 123-45-6789"; assert 200, upstream received original content,
    guardrail_events_total{action="audited"}

  - test_pii_mask_multiple_types_all_replaced: S6 — arrange tenant with pii_mask mask mode;
    act with message containing both email and phone; assert upstream message has both
    "[EMAIL_REDACTED]" and "[PHONE_REDACTED]", non-PII text preserved

  - test_no_guardrails_proxy_unchanged: S7 — arrange tenant with empty guardrail_configs;
    act with injection-looking text; assert 200, upstream.calls==1, no block

  - test_put_guardrails_member_forbidden: S8 — arrange member JWT;
    act PUT /admin/guardrails with valid body; assert 403 ERR_AUTH_FORBIDDEN,
    guardrail_configs unchanged

  - test_put_guardrails_invalid_mode_422: S9 — arrange owner JWT;
    act PUT /admin/guardrails with {"pii_mask": {"enabled": true, "mode": "block"}};
    assert 422 ERR_PAYLOAD_INVALID, guardrail_configs unchanged

  - test_get_guardrails_returns_current_config: S10 — arrange tenant with default config;
    act GET /admin/guardrails (member JWT); assert 200 with empty/null guardrails;
    then owner PUT to set injection=block; assert GET returns updated config

  - test_guardrail_block_increments_prometheus_counter: S11 — arrange injection block mode;
    act POST with injection payload; assert guardrail_events_total{guardrail="prompt_injection",
    mode="block", action="blocked"} incremented by 1; other labels not incremented

  - test_streaming_injection_blocked_before_stream_starts: S12 — arrange injection block mode;
    act POST /v1/chat/completions with stream=true and injection payload; assert 400 returned
    (not a streaming response), upstream.stream never called

  - test_evaluator_error_block_mode_fail_closed: S13 — arrange injection block mode + fake evaluator
    that raises RuntimeError; act POST /v1/chat/completions; assert 400 ERR_GUARDRAIL_BLOCKED,
    upstream.calls==0

  - test_evaluator_error_mask_mode_fail_open: S14 — arrange pii_mask mask mode + fake evaluator
    that raises RuntimeError; act POST /v1/chat/completions with PII-bearing message; assert 200,
    upstream.calls==1 (original unmasked message), guardrail_events_total{action="error"} incremented

  - test_post_call_pii_mask_on_response: S15 — arrange pii_mask mask mode; upstream response body
    choices[0].message.content contains "user@example.com"; assert client receives "[EMAIL_REDACTED]"
    in choices[0].message.content

  - test_pre_call_guardrails_run_on_cache_warm: S16 — arrange injection block mode + cache-enabled key
    + warm cache for specific payload; act POST with injection payload matching warm cache key;
    assert 400 ERR_GUARDRAIL_BLOCKED (not the cached 200), upstream.calls==0

  - test_guardrails_core_migration_column_exists: S17 — arrange migration head; act create new tenant;
    assert tenants.guardrail_configs column exists with value '{}', no new table in DB
</test_plan>

Tests live in: `apps/gateway/tests/guardrails/` · MUST run red (missing implementation) before Build.

Red run evidence (captured 2026-06-11):
  All 17 tests FAIL. Zero collection errors. Right-reason summary:

  S1–S6, S8–S9, S11–S16: AssertionError: PUT /admin/guardrails failed (404): {"detail":"Not Found"}
    PUT /admin/guardrails route does not exist → 404 → assert 404 == 200 fires in _set_guardrail_config().

  S7, S10: AssertionError: expected 200 from GET /admin/guardrails, got 404
    GET /admin/guardrails route does not exist → 404.

  S11 (additionally): MetricsRegistry.guardrail_events_total attribute check fires before
    the route call — AssertionError: MetricsRegistry must have guardrail_events_total counter.
    (The route 404 is the secondary failure, but asserting the attribute is the FIRST assertion.)

  S17: sqlalchemy.exc.ProgrammingError: UndefinedColumnError: column "guardrail_configs" does not exist
    tenants.guardrail_configs column absent from ORM and DB → pytest.fail() surfaces the ProgrammingError.

  Run tail:
    17 failed in 3.57s
    (coverage floor failure suppressed — expected on partial run per §4 instructions)

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): Guardrail evaluation in BLOCK mode is FAIL-CLOSED —
any exception during evaluation must block the request, never allow it through.
Pre-call guardrails MUST run before cache lookup on all paths (complete + stream + cache HIT).
Post-call guardrails store the UNMASKED body in Redis; masking is applied on return.
Streaming bodies are NEVER inspected by post-call guardrails.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — frozen suite tests/guardrails 17/17 green; full suite 296 passed via root `make ci` exit 0 (orchestrator authoritative re-run 2026-06-11)
- [x] coverage did not decrease — 81.00% ≥ 80% floor enforced by `make ci` (exit 0)
- [x] no test or contract was altered during build — EXCEPT one sanctioned frozen-test DISPOSITION (S11, below); §3 contract untouched (the option of loosening pattern 2's regex was explicitly REJECTED)
- [x] concurrency / timing of the risky operation is safe — guardrail evaluation is synchronous in-process regex (no shared state, evaluator is stateless/singleton-safe); all fire-and-forget tasks keep references + done-callbacks; fail-CLOSED check runs before any upstream call so a blocked request can never leak to upstream
- [x] no exposed secrets, injection openings, or unexpected dependencies — guardrail_router uses bound SQL params (:tid/:val with ::jsonb cast of json.dumps output, not string interpolation); message contents are NEVER logged (only pattern-match booleans and exception strings); no new packages
- [x] layering & dependencies follow CONVENTIONS.md — GuardrailEvaluator Protocol + entities in proxy/domain (zero framework imports); RegexGuardrailEvaluator in proxy/infrastructure; wiring in api/deps.py; use case depends only on the port
- [x] a person reviewed and approved the change — orchestrator line-by-line review under delegated auto mode (Tin Dang, 2026-06-11); fixes applied during review (see DISPOSITIONS)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — GuardrailEvaluator port → RegexGuardrailEvaluator constructed in deps.py get_completion_use_case behind the pinned app.state.guardrail_evaluator override seam; evaluate_pre called in complete() step 4 and stream(); evaluate_post called on fresh 200 bodies and cache HIT bodies (hasattr-guarded); guardrail_router included in main.py; guardrail_events_total registered in MetricsRegistry and incremented via _fire_guardrail_metrics; guardrail_configs threaded ORM→repository (existing tenants LEFT JOIN)→ApiKey/AuthzResult→use case; migration d4e7f1a2b3c5 chained after 1c4e7a9f3b2d — confirmed by reading every new/modified file
- [x] DEAD-CODE (code) — orchestrator removed builder's unused `import copy` and `first_mode` variable; ruff F401/F841 clean via make ci; all helpers (_dispatch_record, _fire_record_with_raw, _make_error_event, _fire_guardrail_metrics) have call sites
- [x] SEMANTIC (prose / non-code) — migration docstring read in full: matches §3 DDL exactly (additive JSONB NOT NULL DEFAULT '{}'::jsonb on tenants, EXPECTED_TABLES unchanged, downgrade drops the column); round-trip upgrade/downgrade/upgrade verified; `make migrate-check` reports no new upgrade operations

### DISPOSITIONS (orchestrator review, delegated auto mode)
1. **S11 frozen-test DISPOSITION edit** (precedent: team-governance S11,
   response-caching S12) — the builder HARD-STOPPED correctly: the frozen
   payload "disregard your system prompt entirely" matches NONE of the 7 frozen
   §3 pattern families (the intervening word "system" breaks pattern 2's
   adjacency). Loosening the frozen contract regex was REJECTED — it would
   broaden a security pattern post-freeze. In-file documented edit replaces the
   arrange payload with "disregard your guidelines now" (already in the suite's
   own INJECTION_PAYLOADS constants, verified pattern-2 match). The behavioral
   claim (block event increments the counter exactly once) is unchanged.
2. **Typed capability seam refactor** (requested by Tin Dang mid-build) — the
   inspect.signature(record) introspection in the proxy fire-record helpers is
   replaced by UsageRecordExtras (TypedDict, total=False, in
   proxy/domain/ports.py) filtered against an explicit
   `supported_extras: frozenset[str]` declaration on RecordingUsageRecorder.
   v1-Protocol fakes lack the attribute → only base kwargs. All three helpers
   delegate to a single _dispatch_record.
3. **Orchestrator fixes during review** — lint (unused import/variable, B904
   raise-from, E501), 6 mypy type-arg errors, `enabled=true` requirement added
   to the use-case-level fail-CLOSED fallback (contract says ACTIVE guardrail),
   missing streaming_pii_mask_skipped one-time audit-log event added to
   stream(), deps.py + use_cases.py formatted.
4. **pyproject ruff exclude** += tests/guardrails/test_guardrails_core.py
   (frozen file, format-exempt — sanctioned pattern).

### GATE RECORD
Outcome: PASS (auto-resolved under autonomy: auto)
Evidence: tests/guardrails 17/17; root `make ci` exit 0 (lint + typecheck +
allowlist + 296 tests with 80% coverage floor at 81.00%); `make migrate-check`
clean; migration round-trip verified on gateway_test.
Security review: no findings — real require_owner_or_admin guard exercised
(S8); bound SQL parameters in guardrail_router; message contents never logged;
fail-CLOSED verified for BLOCK mode (S13); blocked requests recorded and
metered. The two freeze flags resolved: cache-HIT masking landed without
breaking the response-caching frozen suite (byte-identical hits hold, full
suite green); the app.state evaluator seam wired exactly as pinned.
Reviewed by: Claude orchestrator under delegated auto mode, approved by Tin Dang (delegated auto mode, 2026-06-11) · date: 2026-06-11

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): guardrail block rate per tenant (spike = injection attack
or misconfigured mode); pii_mask masked rate per tenant; guardrail error rate (evaluator bugs);
latency delta on proxy path with guardrails enabled (regex should be <1ms overhead)
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence.
