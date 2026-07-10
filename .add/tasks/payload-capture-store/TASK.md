# TASK: Opt-in PII-scrubbed request/response payload capture store

slug: payload-capture-store · created: 2026-07-10 · stage: production
milestone: logs-explorer-guardrails-v2
sensitivity: data   <!-- tenant-isolation + PII-bearing payload store; see MILESTONE.md "Security floor" -->
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: ground   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/proxy/application/use_cases.py:CompletionUseCase.complete` — non-streaming exit point; existing usage-record fire hook `_fire_record_with_raw(...)` fires here with the full `response_body` in hand. New capture hook inserts alongside it.
- `apps/gateway/src/gateway/proxy/application/use_cases.py:CompletionUseCase.stream` — streaming exit points; THREE distinct billing-fire sites share one `collected: list[bytes]` SSE-byte buffer: clean-close (`_fire_record_with_raw`), client-disconnect (`extract_usage_from_sse(collected)`), and bandwidth-shed truncation. `collected` holds raw SSE bytes only — no assembled assistant text exists yet; capture needs its own delta-content accumulation.
- `apps/gateway/src/gateway/proxy/application/use_cases.py:_fire_record_cached` (3 call sites: exact-cache HIT, semantic-normalized HIT, vector-similarity HIT) — a cache-hit is still a served, billable request; capture must hook all 3, not just the completion path.
- `apps/gateway/src/gateway/proxy/application/use_cases.py:_dispatch_record` — the canonical fire-and-forget idiom (`asyncio.ensure_future` + `task.add_done_callback` to suppress unhandled-exception noise, task reference kept to satisfy RUF006). New capture dispatch mirrors this shape exactly.
- `apps/gateway/src/gateway/proxy/domain/ports.py:GuardrailEvaluator` (Protocol, `evaluate_pre`/`evaluate_post`) — `evaluate_post(response_body, guardrail_configs) -> dict` is the scrub-before-persist call for the RESPONSE side; documented "always fail-OPEN" (never raises, returns original body unmasked on internal error) — capture must NOT treat that as "safe to persist," see Issues below.
- `apps/gateway/src/gateway/proxy/infrastructure/guardrail_evaluator.py:_mask_pii_in_body` / `RegexGuardrailEvaluator.evaluate_post` — the actual regex-mask implementation; operates ONLY on `choices[*].message.content` (response shape). No equivalent exists for the REQUEST `messages[*].content` shape — see Issues.
- `apps/gateway/src/gateway/tenants/api/guardrail_router.py:guardrail_router` (`GET/PUT /admin/guardrails`) — reference shape for an admin toggle endpoint (role-gated PUT, partial-merge body).
- `apps/gateway/src/gateway/tenants/infrastructure/orm.py:TenantRow` — `cache_enabled`, `semantic_cache_enabled`, `batch_grouping_enabled`, `guardrail_configs` (JSONB) — the per-tenant boolean-opt-in column idiom (`Boolean, server_default=sa.false()`) a new `payload_capture_enabled` column follows.
- `apps/gateway/src/gateway/keys/infrastructure/orm.py:ApiKeyRow.cache_enabled` + `apps/gateway/src/gateway/keys/infrastructure/repository.py:SqlAlchemyApiKeyRepository.get_by_id` (line ~159: `effective_cache = bool(key.cache_enabled) or bool(tenant.cache_enabled)`) — confirmed OR-resolution (key can only turn the feature ON, never override tenant OFF). Contrast: `semantic_cache_enabled`/`batch_grouping_enabled` are tenant-only, no key column at all.
- `apps/gateway/src/gateway/keys/domain/entities.py:AuthzResult` (frozen dataclass) — additive-default-`False` field idiom for auth-time-resolved flags; new `payload_capture_enabled: bool = False` field follows this shape.
- `apps/gateway/src/gateway/tenants/api/cache_router.py:cache_router` (`GET/PUT /admin/cache`, `{enabled, semantic_enabled}`, `require_owner_or_admin` on PUT, absent-field = no-change partial update) — the exact endpoint shape §3 mirrors for a new `/admin/capture` router.
- `apps/gateway/src/gateway/keys/api/router.py:340` (`if "cache_enabled" in body.model_fields_set:`) — the per-key PATCH partial-update idiom; a new `capture_enabled` field on the same PATCH body follows it rather than a new endpoint.
- `apps/gateway/src/gateway/usage/application/retention_sweep.py:RetentionSweeper`, `_Settings` Protocol (6 `retention_*_days` fields), `sweep_once`, `_delete_batched`, `_DELETE_USAGE_BATCH` — NOT a registry: each table is hardcoded (own SQL const, own Settings field, own block in `sweep_once`). A new `request_logs` block must be added by hand, same shape as the `usage_records` block (plain `_delete_batched`, not the audit-immutability variant — see Issues on immutability).
- `apps/gateway/src/gateway/usage/infrastructure/orm.py:UsageRecordRow` — `tenant_id` (FK, NOT NULL), `key_id` (UUID, NOT NULL, **no FK** — deliberate append-only-ledger pattern), `team_id` (nullable, no FK), `raw` (JSONB NOT NULL), `Index("ix_usage_records_created_at", "created_at")` added specifically to support the retention cutoff scan — the schema template `request_logs` mirrors.
- `apps/gateway/src/gateway/audit/infrastructure/audit_events_orm.py:AuditEventRow` + `migrations/versions/f2a4c6e8b0d3_audit_retention_trigger.py` (`audit_events_immutable_guard_fn`, BEFORE UPDATE/DELETE trigger, raises unless `current_setting('app.audit_purge') = 'on'`) — the append-only-immutable posture audit_events has and `request_logs` deliberately does NOT need (no compliance requirement to make PII-bearing capture rows immutable; shorter, plain-delete retention is the right posture for a store whose whole purpose is to hold less-durable PII).
- `apps/gateway/src/gateway/usage/application/alert_writer.py:persist_soft_budget_alert` — the closest existing "insert one best-effort row, fire-and-forget" precedent (raw `session.execute` + `session.commit()`, whole body wrapped in `try/except Exception` swallow+log, dedupe via `ON CONFLICT ... DO NOTHING`). **Gap found**: it has NO explicit bounded timeout around the DB call — just the swallow. Capture's own persist function must add an explicit `asyncio.wait_for(..., timeout=...)` around the insert (CLAUDE.md IO-design-for-failure rule; also named explicitly in MILESTONE.md as "bounded timeout, fire-and-forget").
- `apps/gateway/src/gateway/usage/domain/extractor.py:extract_usage_from_sse`, `stream_usage_is_complete` — pure domain functions parsing the terminal usage frame from accumulated SSE bytes; capture's own `extract_content_from_sse` (new) mirrors this module's location/shape and should key "is this payload final vs partial" off the same `stream_usage_is_complete` check at the same 3 call sites.
- `apps/gateway/src/gateway/core/config.py:Settings` (class at line 83) — satisfies `RetentionSweeper`'s `_Settings` Protocol structurally; `retention_usage_records_days` (578, default 365), `retention_audit_events_days` (582, default 730), `artifact_max_bytes` (611, default 10 MiB, reject-not-truncate precedent), `retention_batch_size` (586, default 1000). **No existing global cache_enabled-family setting** — `cache_enabled`/`semantic_cache_enabled` are per-tenant DB columns only, resolved per-request at auth time, never a global config knob (corrects an initial assumption from the first grounding pass).
- `apps/gateway/migrations/versions/511ad8a7b65e_audit_events_actor_key_id.py` — current Alembic head (`uv run alembic heads` confirms single head, no branching). A new migration parents on `511ad8a7b65e`.
- `apps/gateway/src/gateway/proxy/infrastructure/circuit_breaker.py` / `circuit_breaker_proxy.py` — exist for OUTBOUND upstream HTTP calls (OpenRouter/provider adapters), not for internal Postgres writes; not directly reusable for the capture-store IO seam (a DB insert, not an HTTP egress call) — see §3 for the bounded-concurrency alternative chosen instead of a stateful breaker class.

Context (working folder): `.add/GLOSSARY.md` — `cache_hit` entry: "Redis stores the UNMASKED body, PII masking re-applied on read" (added v4, response-caching). This matters: a cache-hit capture MUST use the same post-mask body actually served to the client, never the raw unmasked Redis-internal representation — capturing the internal cache value would bypass scrub-before-persist entirely.

Honors (patterns / conventions):
- CONVENTIONS.md clean-architecture layering (`domain/` zero-framework ← `application/` use cases ← `infrastructure/` adapters ← `api/` routers) — a new bounded context `logs/` follows this exactly, matching `usage/`'s directory shape.
- CONVENTIONS.md IO design-for-failure: "outbound IO has timeout + bounded jittered retry (idempotent ops only) + circuit breaker" — applied to the new capture-persist IO seam (§3).
- Folded lesson (foundation-version 31, concurrency-load-guard): `asyncio.wait_for(sem.acquire(), timeout=0)` is not a reliable non-blocking acquire in 3.12; use `if not sem.locked(): await sem.acquire()` — the pattern capture's bounded-concurrency guard uses.
- Folded lesson (foundation-version 12, alert-seam): "one alert seam not a parallel re-impl" — capture reuses the EXISTING `GuardrailEvaluator` masking primitive rather than re-implementing PII regex matching a second time.
- MILESTONE.md Shared decisions (binding, verbatim-honored): request log is a NEW bounded concept, never billing truth; scrub-before-persist is an invariant (a scrub failure -> metadata-only row, never raw); capture is opt-in + fail-open for the proxy path; security floor = `data` sensitivity.

Seams consulted: none (.add/SEAMS.md not present in this repo as of ground time).

Anchors the contract cites:
`use_cases.py:CompletionUseCase.complete` · `use_cases.py:CompletionUseCase.stream` · `use_cases.py:_fire_record_cached` (×3 sites) · `use_cases.py:_dispatch_record` · `proxy/domain/ports.py:GuardrailEvaluator.evaluate_post` · `proxy/infrastructure/guardrail_evaluator.py:_mask_pii_in_body` · `usage/application/retention_sweep.py:RetentionSweeper.sweep_once` / `_delete_batched` · `usage/application/alert_writer.py:persist_soft_budget_alert` (pattern precedent) · `tenants/infrastructure/orm.py:TenantRow` · `keys/infrastructure/repository.py:get_by_id` · `keys/domain/entities.py:AuthzResult` · `tenants/api/cache_router.py:cache_router` · `keys/api/router.py` (PATCH `model_fields_set` idiom) · `core/config.py:Settings` · migration head `511ad8a7b65e`.

Issues/Risks (→ feed §1):
1. **No existing request-side PII-mask function.** `evaluate_post`/`_mask_pii_in_body` only mask the RESPONSE shape (`choices[*].message.content`). Scrubbing the REQUEST (`messages[*].content`) before persist has no precedent — needs a new, shape-generalized masking primitive in `guardrail_evaluator.py` (extract the pattern-apply core so both the response path and the new request-capture path call the SAME regex table — not a parallel reimplementation).
2. **`evaluate_post` is documented to fail OPEN** (returns the ORIGINAL unmasked body on internal error, because post-call must never break the live response). Capture's own invariant is the OPPOSITE direction: "a scrub failure drops the payload, never stores raw" — capture must wrap the scrub call in its OWN try/except and treat ANY exception (or any non-success signal it can detect) as scrub-failure -> metadata-only row, never simply forward `evaluate_post`'s fail-open return value as if it were confirmed-scrubbed.
3. **Unconfirmed: pre-call guardrail-BLOCK path.** A prompt blocked by `evaluate_pre` (BLOCK mode) never reaches the completion/stream methods at the line numbers grounded above — the request short-circuits earlier. Whether a distinct capture hook is needed at that earlier block site (to log "what got blocked and why," which the milestone's own detail-drawer scope item implies is wanted) was NOT confirmed against the actual pre-call rejection code path in this grounding pass. Flagged as the §1 ⚠ lowest-confidence item.
4. **JSONB truncation cannot naively cut a serialized blob** — mid-document truncation of a JSONB column produces invalid JSON. Any size cap must truncate leaf text fields (message content strings) BEFORE serialization, not the serialized document after the fact. No existing "truncate-with-marker" precedent exists anywhere in the codebase (`stt_max_duration_seconds` clamps a number; `artifact_max_bytes` rejects outright) — this is new ground, not a reuse.
5. **Capture-store outage vs. ZDR-check failure need OPPOSITE fail directions.** A capture DB-write failure/timeout must fail OPEN for the proxied request (never slow/fail it — MILESTONE.md invariant) but the write itself is simply dropped, no row, no drama. A ZDR-check failure must fail CLOSED for the write decision (treat "cannot confirm ZDR status" as "assume ZDR, suppress the write") — writing a payload for a tenant that might be under Zero-Data-Retention is a data-leak risk, the worse of the two failure modes. Conflating these two directions is the single highest-stakes mistake this task's build could make.
6. **Per-key/tenant opt-in resolution semantics are undecided.** MILESTONE.md's "key > tenant" resolution order is scoped explicitly to the SIBLING task `per-key-guardrail-policies` (a different resolution model: override, not merge). The existing per-tenant/per-key CACHE precedent this task is told to mirror uses OR-resolution (key can only turn cache ON, never override tenant OFF). Whether capture opt-in should mirror cache's OR-only semantics, or need "a key can be excluded even when the tenant is on" (override) is NOT settled by any existing precedent or MILESTONE.md text — a genuine freeze decision (§3 Freeze questions #1).

Related intent: MILESTONE.md `logs-explorer-guardrails-v2` §Scope item 1 + §Shared decisions (scrub-before-persist invariant, fail-open-for-proxy invariant, ZDR-override-hook ownership) + §Shared/risky contracts ("request-log row schema + capture hook placement (+ ZDR override hook) -> owning task payload-capture-store"). GLOSSARY.md `cache_hit`, `Usage record`, `Write-behind` entries. PROJECT.md Architecture section (stateless FastAPI gateway, Postgres + Redis) and the folded DDD lesson on honest-degradation (foundation-version 38: an honest 5xx beats a masking fallback that fakes success) — directly informs the "never fake a captured row as scrubbed when it wasn't" rule.

Ground SHA: `2071046` (branch `chore/add-housekeeping-clusters`)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Opt-in, PII-scrubbed request/response payload capture (new `request_logs` store + proxy-path capture hook + ZDR override hook)

Framings weighed:
- **(chosen) New bounded context `logs/` (clean-architecture layers) owning a dedicated `request_logs` table**, hooked into the proxy completion path via a `PayloadCapturePort` Protocol (mirroring `GuardrailEvaluator`/`UsageRecorder`'s additive-injection shape) — keeps the write-path a single new fire-and-forget call, keeps the store's own scrub/truncate/ZDR logic out of `use_cases.py`.
- (rejected) Fold payload capture into `usage_records.raw` (already JSONB, already fire-and-forget) — rejected because MILESTONE.md explicitly pins "request log is a NEW bounded concept distinct from usage record... never a source of billing truth," and usage_records has a 365-day default retention with no PII-scrub step; overloading it would need per-row exemptions from the existing retention/billing invariants.
- (rejected) Write-behind via Redis buffer + async flusher (mirrors `usage/infrastructure/redis_stream.py`'s write-behind pattern) — rejected for v1: payload bodies are far larger than usage-metering numbers and can carry images/audio references; buffering full bodies in Redis multiplies memory pressure for a feature that's explicitly opt-in and best-effort. A direct bounded-timeout INSERT is simpler and the existing `alert_writer.py` precedent already validates the direct-write fire-and-forget shape for non-metering rows. Revisit if the direct-write path shows contention at scale (spec delta candidate, not now).

Must:
<must>
  - Capture writes exactly one `request_logs` row per proxied call that reaches a capture hook (non-streaming completion, streaming completion — any of its 3 exit branches, or a cache-hit), IF AND ONLY IF: (a) capture is enabled (effective tenant/key opt-in resolves true) AND (b) the tenant is NOT under Zero-Data-Retention (checked live via `ZdrOverridePort.is_zdr`, not cached from toggle time).
  - Capture is fail-open for the proxied response: any capture-store failure (DB error, bounded timeout expiry, exception) is swallowed inside the fire-and-forget task and NEVER raises into, blocks, or slows the proxy request/response path. The response the caller receives is byte-identical whether or not the capture write later succeeds.
  - Every persisted row goes through scrub-before-persist FIRST: the request body's message content and the response body's message content are each run through the (generalized) guardrail PII-masking primitive using the tenant's current `guardrail_configs`, before the row is ever written. Raw, unscrubbed bodies never reach the INSERT statement.
  - A scrub-step failure (the masking call raises, or capture cannot confirm the result is masked) drops the payload content and persists a METADATA-ONLY row (`request_body=null`, `response_body=null`, `scrub_status="scrub_failed_metadata_only"`) — never the raw body, never a silently-skipped row (row still records that a call happened, for audit continuity).
  - A cache-hit capture uses the SAME post-mask body actually served to the client (the value returned after `evaluate_post`/cache-read re-masking), never the raw unmasked Redis-internal cache representation.
  - Oversize payloads are handled by truncating individual leaf text fields (message `content` strings) to a configured byte cap BEFORE JSON serialization (never truncating an already-serialized JSONB blob, which would produce invalid JSON), appending a `"...[TRUNCATED]"` marker to any cut field, and setting `truncated=true` on the row. If the row's total serialized size still exceeds a backstop cap after per-field truncation, the row falls back to metadata-only (same shape as a scrub failure).
  - Capture opt-in is resolved and exposed identically to the existing cache-opt-in family: `GET/PUT /admin/capture` (tenant-level, mirrors `cache_router.py` exactly: any role reads, owner/admin-only writes) plus a `capture_enabled` field added to the existing per-key `PATCH /admin/keys/{id}` body (mirrors the `cache_enabled` `model_fields_set` partial-update idiom) — no new per-key endpoint.
  - `PUT /admin/capture {enabled: true}` for a tenant currently under ZDR is REJECTED (409, not silently accepted-but-inert) — the toggle must never read as "on" when it can never actually capture anything; this is an honest-degradation requirement, not cosmetic.
  - The `ZdrOverridePort.is_zdr(tenant_id)` Protocol NEVER raises to its caller — any internal failure inside the port implementation is caught and mapped to `True` (fail-closed: assume ZDR, suppress capture) — this is the FROZEN hook shape the sibling `tenant-retention-zdr` task implements against; a permissive `AlwaysAllowCapture` no-op (`is_zdr` always `False`) is the default wiring until that task lands, since no tenant can be ZDR before that column/feature exists.
  - `request_logs` is wired into the existing per-table-hardcoded `RetentionSweeper.sweep_once()` (new `retention_request_logs_days` Settings field, own SQL delete constant, own sweep block, plain `_delete_batched` — no immutability trigger, unlike `audit_events`) so opted-in PII-bearing rows do not accumulate past their configured window.
  - `request_logs.cost_usd` (if present) is a denormalized display snapshot only — `usage_records` remains the sole billing source of truth; nothing reads `request_logs` for money.
  - The capture persist call is wrapped in an explicit `asyncio.wait_for(..., timeout=capture_persist_timeout_seconds)` (new bounded-timeout IO seam — the existing `alert_writer.py` precedent lacks this and is NOT copied verbatim on this point) and is NEVER retried (a fire-and-forget best-effort write retried under a struggling store would amplify load during exactly the outage it should be shedding).
  - In-flight capture tasks are bounded by a shared `asyncio.Semaphore` (non-blocking try-acquire per the folded concurrency-load-guard lesson: `if not sem.locked(): await sem.acquire()`) rather than a stateful circuit-breaker class — when the pool is saturated (the store is genuinely struggling), a new capture attempt is skipped non-blockingly rather than queued, which is the practical equivalent of a breaker's open-state shed for a fire-and-forget, non-retried write.
</must>

Reject:
<reject>
  - `PUT /admin/capture` body fails validation (non-boolean `enabled`) -> "ERR_PAYLOAD_INVALID"
  - `PUT /admin/capture` called by a `member` role -> "ERR_AUTH_FORBIDDEN"
  - `PUT /admin/capture {enabled: true}` while the tenant is under Zero-Data-Retention -> "ERR_CAPTURE_ZDR_BLOCKED" (409)
  - `PATCH /admin/keys/{id}` with `capture_enabled` set to a non-boolean -> "ERR_PAYLOAD_INVALID" (reuses the existing key-PATCH validation path, no new code needed beyond the field)
</reject>

After:
<after>
  - A tenant/key with effective capture ON and NOT under ZDR: every proxied call (streaming, non-streaming, cache-hit) that reaches a capture hook produces exactly one PII-scrubbed `request_logs` row within the bounded persist timeout, or no row at all on failure — never a raw/unscrubbed row.
  - A tenant/key with capture OFF, or a tenant under ZDR regardless of the stored toggle value: zero `request_logs` rows are ever written, and the proxied response is byte-identical to today's (no new latency, no new fields in the wire response).
  - A capture-store outage (DB down, write times out) never appears to the API caller — the proxied response is unaffected; the failure is logged and swallowed.
  - `request_logs` rows age out per the tenant's/operator's `retention_request_logs_days` window via the existing sweep cycle, same as `usage_records`/`alert_events`/`audit_events` today.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **Whether pre-call guardrail-BLOCK requests (never reach upstream) need their own capture hook at the earlier rejection site is unconfirmed** — lowest confidence because this grounding pass verified the 3 POST-response hook sites (complete/stream/cache-hit) but did NOT trace the `evaluate_pre` BLOCK short-circuit's exact code path or confirm whether it shares any of those exit points. If wrong (a capture hook is in fact needed there and is omitted): the Logs Explorer's detail-drawer promise of showing "guardrail verdicts" for BLOCKED calls (MILESTONE.md scope item 3) silently has no data to show for the single most audit-interesting case — a real product gap, not just a missed edge case. Recommend a targeted grounding sub-pass on the pre-call BLOCK path before BUILD locks in the final hook-site list; §3 marks this contract clause explicitly conditional pending that check.
  - [ ] Per-key/tenant capture opt-in resolution: OR-semantics (mirrors `cache_enabled` exactly, key can only turn capture ON) vs. override-semantics (key can also turn capture OFF even when tenant is ON, useful for excluding one sensitive/demo key) — recommend OR-semantics for v1 (matches the ONE existing precedent this task was told to mirror; override is a richer ask with no existing pattern to reuse and can be a follow-up spec delta) — confirm or deny at freeze (§3 Freeze question #1).
  - [ ] Default `retention_request_logs_days` value — recommend 30 (a privacy-by-default window, deliberately shorter than `usage_records`' 365d default, since this store holds PII payloads by design) — confirm or deny at freeze (§3 Freeze question #2), this is a business/compliance call, not a technical one.
  - [ ] Per-field truncation byte cap and row backstop cap values — recommend `capture_max_field_bytes=8192` (8 KiB per message-content string) and `capture_max_body_bytes=65536` (64 KiB per row) — reasonable defaults with no strong precedent to anchor to (artifact_max_bytes' 10 MiB target is a different medium/purpose); confirm or deny at freeze (§3 Freeze question #3).
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Non-streaming capture writes one scrubbed row   # M1/M3
  Given a tenant with payload_capture_enabled=true, not under ZDR, valid guardrail_configs
  When a non-streaming completion request completes successfully
  Then exactly one request_logs row is written within the bounded persist timeout
  And request_body/response_body contain only PII-masked content (no raw PII substrings from the original messages)

Scenario: Streaming capture assembles and writes the full response text   # M1
  Given a tenant with capture enabled, not under ZDR
  When a streaming completion request runs to a clean SSE close
  Then request_logs.response_body reflects the fully assembled, scrubbed assistant text
  And the row is written using the same terminal-frame completeness signal (stream_usage_is_complete) the billing path already uses

Scenario: Cache-hit capture uses the served (masked) body, not the raw cache value   # M5
  Given a tenant with capture enabled and semantic cache enabled, a prior identical prompt cached
  When a new request hits the semantic cache
  Then request_logs is written for the cache-hit call
  And response_body matches the masked body actually returned to the client, never the unmasked Redis-internal cache value

Scenario: Capture OFF is byte-identical to today   # After (byte-identical)
  Given a tenant with payload_capture_enabled=false (the default) on both key and tenant
  When any proxied call (streaming, non-streaming, or cache-hit) completes
  Then the proxied response body, headers, and latency are unchanged from a build with capture code entirely absent
  And zero request_logs rows exist for that tenant

Scenario: ZDR tenant never gets a payload row even if opted in   # M1, M9 (fail-closed)
  Given a tenant with payload_capture_enabled=true AND under Zero-Data-Retention (ZdrOverridePort.is_zdr returns true)
  When a proxied call completes
  Then zero request_logs rows are written
  And the proxied response is unaffected (same as capture-OFF path)

Scenario: ZDR-check failure fails closed (suppresses capture)   # M9, Issue 5
  Given a tenant with capture enabled, and the ZdrOverridePort.is_zdr call raises internally
  When a proxied call completes
  Then the port's own internal handling maps the failure to true (assume-ZDR)
  And no request_logs row is written for that call
  And the proxied response is unaffected

Scenario: Capture-store outage never affects the proxied response   # M2 (fail-open for the proxy path)
  Given a tenant with capture enabled, not under ZDR, and the request_logs INSERT times out or the DB is unreachable
  When a proxied call completes
  Then the proxied caller receives the normal response, unaffected in status/body/latency beyond the bounded capture timeout running in a detached task
  And no request_logs row is written; the failure is logged, not raised

Scenario: Scrub failure persists metadata-only, never raw   # M4
  Given a tenant with capture enabled, and the PII-masking call raises (or its result cannot be confirmed masked) for a given request/response pair
  When the capture write executes
  Then request_logs.request_body and response_body are both null
  And request_logs.scrub_status = "scrub_failed_metadata_only"
  And no raw/unscrubbed content appears anywhere in the persisted row

Scenario: Oversize payload is field-truncated with a marker, not blob-truncated   # M6
  Given a tenant with capture enabled, and a response message content string exceeding capture_max_field_bytes
  When the capture write executes
  Then the persisted response_body is valid JSON (deserializes without error)
  And the oversized content field ends with the "...[TRUNCATED]" marker
  And request_logs.truncated = true

Scenario: Row still oversized after per-field truncation falls back to metadata-only   # M6
  Given a request/response pair whose combined serialized size still exceeds capture_max_body_bytes after every individual field has been truncated to its cap
  When the capture write executes
  Then the row is persisted as metadata-only (same shape as the scrub-failure case)
  And truncated = true

Scenario: Tenant admin enables tenant-level capture   # M7
  Given an owner-role identity for a tenant with capture currently disabled
  When PUT /admin/capture {"enabled": true} is called
  Then the response is 200 {"enabled": true}
  And GET /admin/capture subsequently returns {"enabled": true}

Scenario: Member role cannot toggle capture   # R2
  Given a member-role identity
  When PUT /admin/capture {"enabled": true} is called
  Then the response is 403 "ERR_AUTH_FORBIDDEN"
  And the tenant's stored payload_capture_enabled value is unchanged

Scenario: Enabling capture for a ZDR tenant is rejected, not silently accepted   # R3
  Given an owner-role identity for a tenant currently under Zero-Data-Retention
  When PUT /admin/capture {"enabled": true} is called
  Then the response is 409 "ERR_CAPTURE_ZDR_BLOCKED"
  And the tenant's stored payload_capture_enabled value is unchanged (still whatever it was before the call)

Scenario: Invalid PUT body is rejected   # R1
  Given an owner-role identity
  When PUT /admin/capture {"enabled": "yes"} is called (non-boolean)
  Then the response is 422 "ERR_PAYLOAD_INVALID"
  And the tenant's stored payload_capture_enabled value is unchanged

Scenario: Per-key capture_enabled follows the existing PATCH partial-update idiom   # M7, R4
  Given an owner-role identity and an existing API key
  When PATCH /admin/keys/{id} {"capture_enabled": true} is called
  Then the response reflects capture_enabled=true for that key
  And a subsequent PATCH omitting capture_enabled leaves the key's stored value unchanged (absent = no-op, matching cache_enabled)

Scenario: Cross-tenant isolation on captured rows   # tenant-isolation floor
  Given two tenants A and B, both with capture enabled, each with their own request_logs rows
  When tenant A's admin identity is used for any capture-scoped read (e.g. a future logs-explorer-api query, or this task's own admin config read)
  Then only tenant A's own data is ever visible/affected
  And tenant B's rows and toggle state are unreachable (404-shaped invisibility, never a leak, per PROJECT.md's cross-tenant floor)

Scenario: Retention sweep purges aged request_logs rows   # After (retention wiring)
  Given request_logs rows older than the configured retention_request_logs_days window, and the sweep cycle runs
  Then those rows are deleted by the new sweep_once() block
  And rows within the window are untouched
  And the sweep is bounded (batched deletes via _delete_batched, same shape as usage_records)

Scenario: Capture-persist concurrency guard sheds load without blocking   # M11 (bounded-concurrency, not a stateful breaker)
  Given the shared capture-task semaphore is fully saturated (N in-flight capture tasks already running)
  When a new proxied call reaching a capture hook fires
  Then the new capture attempt is skipped non-blockingly (no row written for that call)
  And the proxied response is completely unaffected — no wait on the semaphore
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

> Status: DRAFT — awaiting human freeze. Every clause below is proposed; the "Freeze questions" block lists the decisions Tin must rule on. The pre-call-BLOCK capture hook (Freeze question #4) is the one clause most likely to change after a targeted grounding sub-pass.

### API — admin config endpoints

```
GET /admin/capture
  auth: any authenticated tenant role (owner, admin, member) — mirrors GET /admin/cache
  200 -> { "enabled": bool }

PUT /admin/capture   body: { "enabled": bool }
  auth: owner or admin only (require_owner_or_admin) — member -> 403
  200 -> { "enabled": bool }
  422 -> { "error": "ERR_PAYLOAD_INVALID" }        # non-boolean enabled
  403 -> { "error": "ERR_AUTH_FORBIDDEN" }         # member role
  409 -> { "error": "ERR_CAPTURE_ZDR_BLOCKED" }    # enabling=true while tenant is under ZDR

PATCH /admin/keys/{id}   body: { ..., "capture_enabled"?: bool }   # EXTENDS the existing key-PATCH endpoint
  auth: unchanged from today's PATCH /admin/keys/{id} gate
  200 -> { ..., "capture_enabled": bool }          # absent field in body = no change (model_fields_set idiom)
  422 -> { "error": "ERR_PAYLOAD_INVALID" }         # non-boolean capture_enabled
```

### New module: `apps/gateway/src/gateway/logs/` (clean-architecture layers, mirrors `usage/`)

```
logs/
  domain/
    entities.py            # RequestLog (frozen dataclass, optional — mirrors AuthzResult-style shape)
    ports.py                # ZdrOverridePort (Protocol)
    sse_content_extractor.py  # extract_content_from_sse(chunks: list[bytes]) -> str | None
                               #   mirrors usage/domain/extractor.py:extract_usage_from_sse's
                               #   location/shape; keys "final vs partial" off stream_usage_is_complete
  application/
    capture_writer.py       # persist_request_log(...) — the fire-and-forget coroutine:
                               #   scrub (via guardrail_evaluator) -> truncate -> bounded-timeout INSERT
                               #   mirrors usage/application/alert_writer.py's shape, PLUS an explicit
                               #   asyncio.wait_for(...) the alert_writer precedent lacks
  infrastructure/
    orm.py                  # RequestLogRow (SQLAlchemy model, table: request_logs)
    sqlalchemy_capture.py   # SqlAlchemyPayloadCapture — implements PayloadCapturePort (see below)
    zdr_noop.py              # AlwaysAllowCapture — default ZdrOverridePort impl (is_zdr always False)
  api/
    capture_config_router.py  # GET/PUT /admin/capture (mirrors tenants/api/cache_router.py verbatim)
```

New Protocol — `apps/gateway/src/gateway/proxy/domain/ports.py` (additive, alongside `GuardrailEvaluator`/`UsageRecorder`):
```python
@runtime_checkable
class PayloadCapturePort(Protocol):
    async def capture(
        self,
        *,
        tenant_id: uuid.UUID,
        key_id: uuid.UUID,
        model: str,
        request_body: dict[str, Any],
        response_body: dict[str, Any] | None,
        status: int,
        stream: bool,
        cached: bool,
        guardrail_configs: dict[str, Any],
    ) -> None:
        """Fire-and-forget capture. NEVER raises — all failures (scrub, size, DB,
        ZDR-check) are caught internally and result in either a metadata-only row
        or no row at all, never an exception propagated to the caller."""
        ...
```
Default wiring in `main.py` (until capture is configured): `NoopPayloadCapture` in `proxy/infrastructure/payload_capture_noop.py`, mirroring `NoopUsageRecorder` — a no-op is the correct default because capture is opt-in and default-off.

New Protocol — `logs/domain/ports.py`:
```python
@runtime_checkable
class ZdrOverridePort(Protocol):
    async def is_zdr(self, tenant_id: uuid.UUID) -> bool:
        """Return True if this tenant is under Zero-Data-Retention (capture must be
        suppressed). NEVER raises to the caller — any internal failure (DB error,
        timeout) is caught and mapped to True (fail-closed: assume ZDR on doubt).
        This is the FROZEN hook the sibling tenant-retention-zdr task implements
        against; default wiring (AlwaysAllowCapture) always returns False until
        that task adds the real ZDR column/check."""
        ...
```

### Schema

```sql
-- new table, migration parents on 511ad8a7b65e
CREATE TABLE request_logs (
    id              UUID PRIMARY KEY,
    tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    key_id          UUID NOT NULL,              -- no FK, mirrors usage_records (append-only ledger pattern)
    team_id         UUID NULL,                  -- no FK, mirrors usage_records (team deletion must not cascade)
    model_id        TEXT NOT NULL,
    status_code     INTEGER NOT NULL,
    stream          BOOLEAN NOT NULL DEFAULT false,
    cached          BOOLEAN NOT NULL DEFAULT false,
    request_body    JSONB NULL,                 -- null when metadata-only (scrub failure or oversize fallback)
    response_body   JSONB NULL,                 -- null when metadata-only, or when the call itself errored pre-response
    guardrail_verdict JSONB NULL,                -- {blocked, pii_masked, patterns_hit, ...} — mirrors otel_span guardrail_blocked fields
    scrub_status    TEXT NOT NULL DEFAULT 'scrubbed',  -- 'scrubbed' | 'scrub_failed_metadata_only' | 'oversize_metadata_only'
    truncated       BOOLEAN NOT NULL DEFAULT false,
    cost_usd        NUMERIC(14,8) NULL,          -- DENORMALIZED DISPLAY SNAPSHOT ONLY — usage_records remains billing truth
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_request_logs_tenant_created ON request_logs (tenant_id, created_at);   -- mirrors audit_events_tenant_created_idx
CREATE INDEX ix_request_logs_created_at ON request_logs (created_at);                   -- retention-sweep cutoff scan, mirrors usage_records
CREATE INDEX ix_request_logs_tenant_key ON request_logs (tenant_id, key_id);            -- logs-explorer-api key filter (sibling task)

-- opt-in columns, same migration
ALTER TABLE tenants ADD COLUMN payload_capture_enabled BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE api_keys ADD COLUMN capture_enabled BOOLEAN NOT NULL DEFAULT false;

-- config additions (core/config.py Settings, alongside retention_usage_records_days)
retention_request_logs_days: int = Field(default=30)          -- Freeze question #2
capture_persist_timeout_seconds: float = Field(default=3.0)
capture_max_field_bytes: int = Field(default=8192)             -- Freeze question #3
capture_max_body_bytes: int = Field(default=65536)              -- Freeze question #3
capture_max_concurrent_tasks: int = Field(default=50)           -- bounded-concurrency guard size
```

Access pattern: capture writes are INSERT-only, fire-and-forget, from the `logs/application/capture_writer.py` coroutine via `session_factory()` (same DI shape as `alert_writer.py`). No UPDATE/DELETE path outside the retention sweep. Reads (for the sibling `logs-explorer-api` task) will be tenant_id-scoped SELECTs against `ix_request_logs_tenant_created`/`ix_request_logs_tenant_key` — not this task's concern beyond providing the indexes.

Retention wiring (`usage/application/retention_sweep.py`):
```python
# _Settings Protocol gains: retention_request_logs_days: int
# sweep_once() gains a new block, same shape as the usage_records block:
request_logs_days = self._settings.retention_request_logs_days
if request_logs_days > 0:
    cutoff = _naive_utc_cutoff(request_logs_days)
    deleted = await self._delete_batched(
        "request_logs", _DELETE_REQUEST_LOGS_BATCH, cutoff, batch_size
    )
    results["request_logs"] = deleted
# _DELETE_REQUEST_LOGS_BATCH mirrors _DELETE_USAGE_BATCH exactly (plain _delete_batched,
# no immutability-trigger SET LOCAL needed — request_logs has no audit_purge-style trigger)
```

Guardrail-engine reuse (extend, not duplicate): `proxy/infrastructure/guardrail_evaluator.py` gains a shape-generalized primitive, e.g. `mask_pii_in_messages(messages: list[dict[str, Any]], guardrail_configs: dict[str, Any]) -> list[dict[str, Any]]`, extracted from `_mask_pii_in_body`'s existing pattern-apply core so BOTH `evaluate_post` (response, existing caller) and `logs/application/capture_writer.py` (request AND response, new caller) call the same regex table. Capture's own call site wraps this in a local try/except (independent of `evaluate_post`'s fail-open contract) so a scrub failure maps to `scrub_status="scrub_failed_metadata_only"` rather than ever forwarding an unmasked body.

Glossary deltas:
- **Request log**: an opt-in, PII-scrubbed, retention-governed capture row of one proxied call's request+response bodies, keyed to tenant/key/team; distinct from `Usage record` (billing truth, never PII-scrubbed, 365d default retention) and `Audit event` (compliance ledger, trigger-immutable, never PII-bearing by design). A ZDR tenant never has request-log rows, regardless of its stored opt-in toggle.
- **Scrub-before-persist**: the invariant that PII masking (via the existing guardrail masking engine) runs on a payload BEFORE any capture row is written, never as a display-time filter; a scrub failure yields a metadata-only row, never a raw one.
- **ZDR override hook**: the `ZdrOverridePort.is_zdr(tenant_id) -> bool` Protocol, fail-closed-on-error (unconfirmable -> assume ZDR -> suppress capture), checked live before every persist (not cached from the opt-in toggle) — the frozen seam `tenant-retention-zdr` implements against.

### Freeze questions (Tin rules on these before Status can move to FROZEN)

1. **Per-key/tenant capture opt-in resolution semantics.** Options: (a) OR-resolution — key can only turn capture ON, mirrors `cache_enabled` exactly (recommended: matches the one existing precedent, simplest, consistent with the codebase's cache-opt-in family); (b) override-resolution — a key-level explicit `false` can exclude one key even when the tenant is ON (richer, no existing pattern, needed only if "exclude one sensitive/demo key" is a real near-term need). Recommendation: (a) for v1, note (b) as a spec delta if needed later.
2. **Default `retention_request_logs_days`.** Recommendation: 30 days (privacy-by-default, shorter than usage_records' 365d, since this store holds PII payloads by design) — this is a business/compliance call more than a technical one.
3. **Size-cap byte values.** Recommendation: `capture_max_field_bytes=8192`, `capture_max_body_bytes=65536` — no strong existing precedent to anchor to; these are reasonable starting defaults, easy to tune later via config without a schema change.
4. **Whether a pre-call guardrail-BLOCK request needs its own capture hook** at the (unconfirmed) earlier rejection code path, so blocked prompts still appear in the Logs Explorer's guardrail-verdict view. This grounding pass could not confirm the exact BLOCK short-circuit site in the time available — recommend a targeted follow-up grounding pass BEFORE build locks the final hook-site list, rather than guessing the code path into the frozen contract. This is the single flag most likely to change §3's "Touches" list after that check.

Status: DRAFT
Reported: no — awaiting the freeze report / Tin's review of this draft.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

---

## Design self-score

- Completeness: 0.90 — every MILESTONE.md scope item for this task (table, hook placement, scrub-before-persist, size caps, retention wiring, ZDR hook) is addressed with a concrete symbol-level plan; held below 0.95 solely because the pre-call BLOCK hook site (Freeze question #4) is an honest open gap, not a guess dressed as ground truth.
- Clarity: 0.93 — every rule cites the exact file/symbol it touches or extends; the two fail-directions (store-outage fail-open vs. ZDR-check fail-closed) are stated explicitly as the single highest-stakes distinction, not left implicit.
- Practicality: 0.92 — the design reuses 6 existing precedents verbatim-in-shape (fire-and-forget dispatch, cache-opt-in columns, cache-router endpoint shape, PATCH partial-update idiom, retention-sweep hardcoded-block idiom, guardrail masking primitive) rather than inventing new patterns; the 3 genuinely new pieces (request-side masking, field-level truncation, ZDR port) are each grounded against why no existing precedent covers them.
- Optimization: 0.90 — bounded-concurrency semaphore chosen over a stateful circuit-breaker class for a fire-and-forget, non-retried write (simpler, matches an existing folded lesson, avoids over-engineering a breaker for an IO seam that's already timeout-bounded and non-retried); direct-write over Redis write-behind explicitly reasoned against for v1 given payload size.
- Edge cases: 0.91 — scenarios cover scrub failure, oversize (both field-level and row-level fallback), cache-hit body provenance, ZDR toggle-time rejection AND live-check-time suppression, cross-tenant isolation, and concurrency-guard shedding; the one deliberately-unresolved edge case (pre-call BLOCK) is flagged rather than silently scoped out.
- Self-evaluation: 0.92 — 4 freeze questions ranked with explicit recommendations + rationale (not bare options), the ⚠ lowest-confidence item names its concrete cost if wrong (a real product gap in the Logs Explorer's own headline feature) and a concrete next step (targeted grounding sub-pass) rather than "TBD."

All dimensions ≥0.90; no refinement pass required before reporting.

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

Scope (may touch): `./src/`   <fill before the §3 freeze — every file the build may write>
Strategy (ordered batches): <1. … 2. … — the planned build order; guidance, not enforced; preferred architecture/pattern strategies; advise solution/method to resolve issues/implement features; let the named Persona's domain stance (below) shape the approach, not just architecture patterns>

Persona (required): <name the persona file under `.add/personas/` this build embodies as a domain stance atop SOUL.md — advisory, never lowers a gate; name "generic" if no project persona fits yet>
Spawn isolation (default): <prefer isolation: "worktree" for any subagent build/verify spawn, not only explicit parallel mode; shared-tree needs a stated reason — see worktree-isolated-spawn-default>
Known-problem fixes: <trap → planned fix — the failure modes this build must dodge; guidance, not enforced>
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

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
