# TASK: Add latency + token counts + usage_records correlation to request_logs capture (change-request from Tin's Logs-Explorer freeze)

slug: request-log-metering-fields · created: 2026-07-10 · stage: production
milestone: logs-explorer-guardrails-v2
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/logs/infrastructure/orm.py:RequestLogRow` — the FROZEN @ v1 (payload-capture-store TASK.md §3) table; gains 5 additive NULLABLE columns (`request_id`, `latency_ms`, `prompt_tokens`, `completion_tokens`, `total_tokens`).
- `apps/gateway/src/gateway/logs/application/capture_writer.py:_INSERT_REQUEST_LOG` / `_insert` / `_do_persist` / `persist_request_log` — the raw `text()` INSERT chain (mirrors `alert_writer.py`); gains 5 new pass-through named params threaded straight to the INSERT (no scrub/truncate logic applies — these are numeric/uuid fields, not message content).
- `apps/gateway/src/gateway/logs/infrastructure/sqlalchemy_capture.py:SqlAlchemyPayloadCapture.capture` — forwards the 3 new kwargs to `persist_request_log`, after the existing ZDR-check/concurrency-admission gates (unchanged).
- `apps/gateway/src/gateway/logs/domain/entities.py:RequestLog` — frozen read-side dataclass projection (`@dataclass(frozen=True, slots=True)`); gains 5 matching fields so the sibling `logs-explorer-api` task (currently phase=ground, its own §0 Issue 1 flags exactly this gap — see Related intent) has a ready-made projection.
- `apps/gateway/src/gateway/proxy/domain/ports.py:PayloadCapturePort.capture` — gains 3 additive OPTIONAL kwargs (`usage: dict[str, Any] | None = None`, `latency_ms: int | None = None`, `request_id: uuid.UUID | None = None`), all defaulting `None` — the frozen 9 required kwargs are untouched.
- `apps/gateway/src/gateway/proxy/infrastructure/payload_capture_noop.py:NoopPayloadCapture.capture` — mirrors the same 3 additive kwargs (accepted, discarded, unchanged no-op behavior).
- `apps/gateway/src/gateway/proxy/application/use_cases.py:_dispatch_capture` (line 580) — the SINGLE funnel every capture-hook call site goes through; gains `usage`, `latency_ms`, `request_id` params, forwarded verbatim into `payload_capture.capture(...)`.
- `apps/gateway/src/gateway/proxy/application/use_cases.py:CompletionUseCase.complete` (`_start_ns = time.time_ns()` at line 1685) and `.stream` (`_start_ns = time.time_ns()` at line 2278) — the SAME per-call start timestamp already threaded into `_emit_span_fire_forget`'s `OtelSpan(start_time_ns=...)` (lines 2251, 2920, 2961). `latency_ms` for capture is derived from this SAME `_start_ns` at the point capture dispatches — never a second/independent clock read. A fresh `_request_id = uuid.uuid4()` is minted once per call, alongside `_start_ns`, mirroring `_emit_span_fire_forget`'s own local-generation idiom (`trace_id = os.urandom(16).hex()`, line 167).
- `apps/gateway/src/gateway/proxy/application/use_cases.py:_try_cache_lookup` (defined line 1393, sole caller `complete()` at line 1872, AFTER `_start_ns` is set) — the 3 cache-HIT `_dispatch_capture` sites (exact/semantic/vector, lines 1476/1548/~1621) live INSIDE this separate method, which does NOT currently receive `_start_ns`/`_request_id` in its own scope. Both must be added as new required keyword params (private method, not part of any frozen contract) threaded from the one caller.
- 11 `_dispatch_capture(...)` call sites total as of Ground SHA (lines 1476, 1548, 1621, 1791, 1834, 2189, 2357, 2391, 2628, 2748, 2879) — every site already has the relevant `usage` dict in scope under a call-site-local name (`cached_usage`, `sem_usage`, `vec_usage`, `usage`, `None` at the 4 guardrail-BLOCK sites, `_bw_usage`, `disconnect_usage`, `extracted_usage`) — the SAME dict already passed to that site's adjacent `_fire_record_with_raw`/`_fire_record_cached` call. No new extraction logic anywhere — thread the existing local through.
- `apps/gateway/src/gateway/proxy/application/use_cases.py:_fire_record` / `_fire_record_cached` / `_fire_record_with_raw` (lines 265, 290, 396) — each gains one new optional `request_id: uuid.UUID | None = None` param, setting `extras["request_id"] = request_id` when not None — mirrors the existing `team_id`/`pii_masked` extras-population idiom verbatim.
- `apps/gateway/src/gateway/usage/application/recorder.py:RecordingUsageRecorder._record_internal` (`raw_payload: dict[str, object]` built ONCE at line 337 — confirmed shared by both the XADD-success path, line 371, AND the `_fallback_insert` durable-fallback path, line 417, since `event_fields["raw"]` is built once and reused for both) and `.record`/`.supported_extras` (lines 56-69, 80-98) — gains one new optional kwarg `request_id: uuid.UUID | None = None`, injected via `if request_id is not None: raw_payload["request_id"] = str(request_id)` — the SAME `if X: raw_payload["Y"] = ...` idiom already used for `cached`/`guardrail_blocked`/`blocked_by`/`pii_masked` (lines 344-351). NO new column, NO migration on `usage_records` — its FROZEN column list (confirmed via its own docstring "Schema contract (FROZEN @ v1 — TASK.md §3)") is untouched.
- `apps/gateway/src/gateway/proxy/domain/ports.py:UsageRecordExtras` (TypedDict, lines 33-73) — gains one new optional key `request_id: uuid.UUID`, forwarded by `_dispatch_record`'s existing `supported: frozenset[str] = getattr(usage_recorder, "supported_extras", frozenset())` filter (line 250) — the SAME typed-capability seam already carrying `team_id`/`cached`/`guardrail_blocked`/`blocked_by`/`pii_masked`/`pricing_unit`/`quantity`/`usage_source`/`provider_generation_id`/`disconnect_estimate`.
- `apps/gateway/src/gateway/usage/api/router.py:1074-1182` — the existing HONEST-omission precedent for a DIFFERENT, unrelated endpoint (`AuditEventItem`-adjacent usage summary): `latency_ms: None = None  # HONEST omission — no stored latency`. Confirms today's global absence of any stored latency and that this task does NOT touch or fix that endpoint (out of scope; a future task could wire it from `request_logs` once this lands).
- `apps/gateway/src/gateway/observability/middleware.py:RequestIdMiddleware` — generates its OWN per-HTTP-request `request_id` (`str(uuid.uuid4())`, line 68) bound via `structlog.contextvars.bind_contextvars`, already emitted in every `http_request` access-log line together with its own `duration_ms` (a BROADER `time.perf_counter()`-based span covering the full ASGI request — auth, governance, model resolution — measured at a different layer than `_start_ns`, which starts later, inside `complete()`/`stream()`). CONSIDERED and REJECTED as the correlation-key source for this task — see Assumptions ⚠.
- `apps/gateway/migrations/versions/a1c5e7f9b3d6_request_logs.py` — CURRENT Alembic head, RE-CONFIRMED via `uv run alembic heads` at this task's own ground time (single head, no branching) — NOT the stale `511ad8a7b65e` payload-capture-store's own TASK.md cites from ITS earlier ground pass; the head has since advanced (payload-capture-store's own migration became the new head after it built). This task's new migration parents on `a1c5e7f9b3d6`.
- `apps/gateway/src/gateway/usage/infrastructure/orm.py:UsageRecordRow` — read-only confirmation: no `latency`/`duration` column exists anywhere on this FROZEN row; `raw` (JSONB NOT NULL) is the only free-form field. This task adds ONE supplementary, index-only, partial expression index (`raw ->> 'request_id'` WHERE NOT NULL) in the same migration — no column, does not reopen the FROZEN column list.

Context (working folder): `.add/tasks/payload-capture-store/TASK.md` §3 (FROZEN @ v1) — the exact ORM/INSERT/Protocol shapes this task amends additively. `.add/tasks/logs-explorer-api/TASK.md` §0 Issue 1 / §1 ⚠ (phase=ground, NOT yet frozen — its own contract has not been drafted) — that sibling task's OWN independent grounding pass confirmed verbatim: "`request_logs` has NO duration/latency column and NO token-count columns, and NO correlation key linking a row back to its `usage_records` billing row... CONFIRMED NONE EXISTS," explicitly recommending its downstream `logs-explorer-ui` sibling "react to this now" and flagging that closing the gap would require "a change request back to the FROZEN `payload-capture-store` contract" — this task IS that change request, Tin-approved at the Logs-Explorer freeze per the dispatch objective.

Honors (patterns / conventions):
- CONVENTIONS.md clean-architecture layering — unchanged; new fields flow through the EXISTING `logs/` bounded context (domain/application/infrastructure), no new module, no new layer crossing.
- CLAUDE.md IO design-for-failure — the capture-persist call is ALREADY bounded-timeout/fire-and-forget/never-retried (payload-capture-store's own §1 Musts, unchanged); this task adds NO new IO seam, only more columns riding the SAME already-bounded INSERT — a Must below makes this explicit so BUILD cannot "improve" it into a second seam.
- MILESTONE.md's binding decision "request log is a NEW bounded concept... never a source of billing truth" — `latency_ms`/`prompt_tokens`/`completion_tokens`/`total_tokens` are DISPLAY-ONLY metadata snapshots, the SAME posture the existing `cost_usd` column's own inline comment already states ("DENORMALIZED DISPLAY SNAPSHOT ONLY, never billing truth") — nothing ever reads `request_logs` for money, rate-limiting, or budget enforcement; `usage_records` remains sole truth for both tokens and cost.
- `usage/application/recorder.py`'s established "typed capability seam" (`supported_extras` + `raw_payload["key"] = value` injection) — reused verbatim rather than inventing a parallel mechanism, per the folded alert-seam lesson ("one seam, not a parallel re-impl").

Seams consulted: none (`.add/SEAMS.md` absent — matches every sibling task's own finding at ground time).

Anchors the contract cites: `logs/infrastructure/orm.py:RequestLogRow` · `logs/application/capture_writer.py:persist_request_log` / `_do_persist` / `_insert` / `_INSERT_REQUEST_LOG` · `logs/infrastructure/sqlalchemy_capture.py:SqlAlchemyPayloadCapture.capture` · `logs/domain/entities.py:RequestLog` · `proxy/domain/ports.py:PayloadCapturePort.capture` / `UsageRecordExtras` · `proxy/infrastructure/payload_capture_noop.py:NoopPayloadCapture.capture` · `proxy/application/use_cases.py:_dispatch_capture` / `CompletionUseCase.complete` / `.stream` / `_try_cache_lookup` / `_fire_record` / `_fire_record_cached` / `_fire_record_with_raw` · `usage/application/recorder.py:RecordingUsageRecorder._record_internal` / `.supported_extras` · migration head `a1c5e7f9b3d6`.

Issues/Risks (→ feed §1):
1. No stored latency exists anywhere in the codebase today (`usage_records` has none, confirmed by its own read-API's "HONEST omission" comment) — the only existing per-call timer at the RIGHT layer (inside the completion use case, not the outer ASGI middleware) is `_start_ns`, already used for the OtelSpan. Reusing it is a deliberate design choice, not an obvious given — flagged in §1 Must, not left implicit.
2. `_try_cache_lookup` doesn't currently receive `_start_ns`; its 3 `_dispatch_capture` call sites need `start_ns`/`request_id` threaded in as new params on a small, private, non-frozen method — mechanical but real surface for a build to miss.
3. TWO different "request_id" concepts exist in this codebase: `RequestIdMiddleware`'s ASGI-layer, structlog-bound, full-HTTP-request id (already logged) vs. this task's proposed use-case-layer, capture-scoped id. Silently conflating them, or picking one without stating the trade-off, would be a genuine design miss — flagged explicitly in Assumptions ⚠ below, not left as an implicit choice.
4. `usage_records` is FROZEN and append-only (no UPDATE/DELETE ever, confirmed by its own docstring) — this task must NOT add a column there; the JSONB-extras design respects that by construction, but must be stated as an explicit Must/Reject pair so BUILD doesn't "fix" it into a real column later.
5. 11 call sites is real mechanical repetition — a genuine risk of missing one on a copy-paste build; §5 Strategy below calls for a grep-verified count-match (11 `_dispatch_capture(` calls before the change, 11 after, all touched) as a build-time self-check.

Related intent: MILESTONE.md `logs-explorer-guardrails-v2` (the Logs Explorer needs latency+tokens to be a genuinely useful table, per Tin's standing polished-UI-surface bar — bare CRUD/table reskins are explicitly rejected); this task's own dispatch objective (a Tin-approved change-request raised at the Logs-Explorer freeze); `logs-explorer-api` TASK.md §1 ⚠ (the exact gap this task closes, independently confirmed by a sibling's own grounding pass).

Ground SHA: `998947e` (branch `chore/add-housekeeping-clusters`)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Additive latency + token-count + billing-correlation fields on `request_logs`

Framings weighed:
- **(chosen)** 5 new NULLABLE columns on `request_logs` (`request_id`, `latency_ms`, `prompt_tokens`, `completion_tokens`, `total_tokens`) populated at capture time from data ALREADY computed at the SAME call site (the `usage` dict, `_start_ns`), plus one new optional extras kwarg on `UsageRecorder.record()` (`request_id`) stored into the EXISTING `usage_records.raw` JSONB (no schema change there) — additive on both sides, zero new IO seam, zero new failure mode.
- (rejected) Correlate `request_logs` to `usage_records` at READ time via a `logs-explorer-api` heuristic JOIN on tenant+model+timestamp proximity (no stored id) — rejected: both writes are independent fire-and-forget tasks racing each other; timestamps can collide or miss under load; no LEFT JOIN guarantee of a unique match. A stored id is the only correct correlation.
- (rejected) Add a real `request_logs_id` FK column on `usage_records` — rejected: `usage_records` is FROZEN @ v1 and strictly append-only (no UPDATE ever, per its own docstring), and its own `id` is assigned by Redis XADD at flush time — not known synchronously in the request path when capture fires. There is no value to backfill it with without a forbidden second UPDATE pass. The JSONB-extras path needs no UPDATE, ever.
- (rejected) Reuse `RequestIdMiddleware`'s existing ASGI-layer `request_id` (already structlog-bound, already in every access-log line) as the correlation key — rejected for v1: requires either `proxy/application/use_cases.py` (application layer, zero observability imports today) importing `structlog.contextvars` — a new coupling this codebase's own conventions consistently avoid (the analogous `OtelSpanEmitter` is an injected Protocol, never read from ambient state) — or threading it down as a new parameter through `complete()`/`stream()`'s FROZEN `proxy-completions` public signatures plus an ungrounded router file, outside this task's declared additive scope. This is the ⚠ lowest-confidence call below.

Must:
<must>
  - Every `request_logs` row written by any of the 11 existing `_dispatch_capture` call sites (unchanged hook sites — this task adds NO new hook) carries, when available at that call site: `latency_ms` (elapsed time from the call's `_start_ns` to the moment capture is dispatched, whole milliseconds), `prompt_tokens`/`completion_tokens`/`total_tokens` (read verbatim from the SAME `usage` dict already passed to that site's adjacent `_fire_record_with_raw`/`_fire_record_cached` call — never recomputed, never a second extraction), and `request_id` (one UUID minted once per proxied call, alongside `_start_ns`).
  - A capture row with no `usage` dict in scope at that call site (the 4 guardrail-BLOCK short-circuits — 2 in `complete()`, 2 in `stream()` — the request never reached upstream) persists `prompt_tokens=NULL, completion_tokens=NULL, total_tokens=NULL` — never `0`, which would misreport "confirmed zero tokens" as "not applicable."
  - `latency_ms` is always derived from the SAME `_start_ns` timestamp already threaded into that call's `_emit_span_fire_forget(...)` (the OtelSpan the call already emits) — never an independently-read clock, so a `request_logs` row's latency and that call's own OTel span duration can never diverge from two different timers measuring the same event.
  - `request_id` is forwarded to the SAME call's usage-record fire function (`_fire_record_with_raw`/`_fire_record_cached`/`_fire_record`) via a new optional `UsageRecordExtras["request_id"]` key, stored by `RecordingUsageRecorder` into `usage_records.raw["request_id"]` (JSONB) — NOT a new `usage_records` column. A `request_logs.request_id` value and its matching `usage_records.raw->>'request_id'` value are byte-identical strings for the same proxied call, letting a reader join the two tables.
  - The 5 new `request_logs` columns are 100% additive and NULLABLE with no `NOT NULL` and no non-null `DEFAULT` — a row written before this migration reads back with all 5 new fields NULL, never an error, never a backfill pass.
  - `NoopPayloadCapture.capture()`, and any other `PayloadCapturePort` caller that does NOT pass the 3 new optional kwargs (`usage`, `latency_ms`, `request_id`), continues to work byte-identically — all 3 default to `None` on the Protocol; no existing frozen test or fake breaks.
  - Adding these fields introduces NO new IO seam, NO new timeout, NO new retry, and NO new failure mode: the bounded-timeout / fire-and-forget / never-retried / fail-open posture of `persist_request_log`/`SqlAlchemyPayloadCapture.capture` (payload-capture-store §1 Musts) is unchanged — the new fields ride inside the SAME already-bounded INSERT statement.
  - `request_logs.latency_ms`/`prompt_tokens`/`completion_tokens`/`total_tokens` remain DISPLAY-ONLY metadata (mirrors the existing `cost_usd` column's own "never billing truth" posture) — nothing in this task wires them into rate-limiting, budget enforcement, or billing computation; `usage_records` remains the sole source of truth for tokens/cost, unchanged.
  - The new supplementary index `ix_usage_records_request_id` (a partial expression index on `raw ->> 'request_id'` WHERE NOT NULL) is index-only — it adds NO column to the FROZEN `usage_records` table and requires NO backfill (pre-existing rows simply have no matching index entry, which is correct — they predate this feature).
  - The migration parents on the CURRENT Alembic head (`a1c5e7f9b3d6`, re-confirmed via `uv run alembic heads` at THIS task's own ground time, not any older/stale rev a prior task's own TASK.md may cite from an earlier ground pass) and is purely additive (`ALTER TABLE ... ADD COLUMN ... NULL`, `CREATE INDEX`) — reversible by a symmetric downgrade dropping the same columns/index.
</must>

Reject:
<reject>
  - A build that makes `latency_ms`/any token field `NOT NULL` or backfills a non-null default (e.g. `0`) for historical or BLOCK-site rows -> "ERR_SCHEMA_INVARIANT_VIOLATED" (a build/code-review-time rejection — this task adds no new HTTP endpoint, so "Reject" binds BUILD discipline, not a client-facing status code)
  - A build that adds a NEW column to `usage_records` (reopening the FROZEN payload-capture-store/usage-metering schema) instead of using the existing `raw` JSONB extras seam -> "ERR_FROZEN_CONTRACT_VIOLATED" (build-time discipline)
  - A build that computes `latency_ms` from a NEW/second clock read instead of the call's existing `_start_ns` -> flagged at VERIFY as a Must violation (risk: the persisted latency silently diverges from that call's own OTel span duration)
  - A build that recomputes `prompt_tokens`/`completion_tokens` from response bodies instead of reading the SAME `usage` dict already passed to the adjacent usage-record fire call -> flagged at VERIFY (risk: silent divergence from the billing path's own token count)
</reject>

After:
<after>
  - A proxied call that reaches any of the 11 existing capture hook sites, with capture effectively enabled and the tenant not under ZDR (unchanged gating from payload-capture-store), produces a `request_logs` row whose `latency_ms` and (where applicable) `prompt_tokens`/`completion_tokens`/`total_tokens` are populated from the SAME values that call's billing/observability paths already computed, plus a `request_id` that also appears verbatim in that call's `usage_records.raw->>'request_id'`.
  - A `request_logs` row from a guardrail-BLOCK hook site (no upstream call made) has `latency_ms` populated (time-to-block is still meaningful) but `prompt_tokens`/`completion_tokens`/`total_tokens` all NULL.
  - Every pre-existing `request_logs` row (written before this migration) reads back with all 5 new columns NULL — no error, no migration-time backfill pass.
  - The proxied response the caller receives is COMPLETELY unaffected — byte-identical status/body/headers/latency-to-the-CLIENT — whether or not any of these new fields end up populated (capture remains entirely fire-and-forget/fail-open, unchanged from payload-capture-store).
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **Minting a fresh `uuid.uuid4()` locally inside `complete()`/`stream()` for `request_id`, rather than reusing `RequestIdMiddleware`'s existing per-HTTP-request id** (already generated, already structlog-bound, already in every access-log line) — lowest confidence because this is a genuine, deliberate trade-off, not a gap in understanding: reusing the middleware's id would give a richer correlation story (one id joining the access-log line, the `request_logs` row, AND the `usage_records` row for the same call) but requires either (a) `proxy/application/use_cases.py` importing `structlog.contextvars` — a new application-layer→observability-infrastructure coupling this codebase has consistently avoided (the analogous `OtelSpanEmitter` is an injected Protocol, never read from ambient state), or (b) threading it down as a new parameter from the API router through `complete()`/`stream()`'s public signatures — which touches the ALREADY-FROZEN `proxy-completions` contract and an ungrounded router file, outside this task's declared additive scope. Chose local-mint (self-contained within this task's own file list) as the default. If wrong (a human decides the richer cross-log correlation is worth the coupling or the signature change): the fix is a small, contained follow-up — swap the local `uuid.uuid4()` source for the ASGI-generated id; the `request_logs.request_id` / `usage_records.raw["request_id"]` shapes themselves would NOT need to change, only where the value comes from. Confirm or override at freeze.
  - [ ] Whether `total_tokens` should be stored verbatim from `usage.get("total_tokens")` (recommended — matches the OpenAI-shaped usage dict's own field, zero computation) vs. derived as `prompt_tokens + completion_tokens` at write time (rejected — would silently diverge from the upstream-reported total on any provider whose total includes tiers, e.g. cached/reasoning tokens, not captured in this task's minimal 3-field set) — recommend verbatim storage; confirm at freeze.
  - [ ] Whether the richer per-tier token columns `usage_records` already carries (`cached_tokens`, `reasoning_tokens`, `cache_creation_tokens`, `audio_*_tokens`) should also be mirrored onto `request_logs` in this same pass, or deferred as a spec delta if the Logs Explorer UI later wants tier-level display — recommend DEFER (the stated exit gap is "can't show latency/tokens," not tier-level billing detail; keeps this change-request minimal and matches the dispatch objective's own framing) — confirm at freeze.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Non-streaming completion capture row carries latency, tokens, and a correlation id   # M1
  Given a tenant with capture enabled, not under ZDR, and a non-streaming completion that succeeds with a usage frame {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46}
  When the call completes and its capture hook fires
  Then the resulting request_logs row has prompt_tokens=12, completion_tokens=34, total_tokens=46
  And latency_ms is a positive integer
  And request_id is a non-null UUID
  And the matching usage_records row's raw->>'request_id' equals the SAME UUID string

Scenario: Streaming clean-close capture row carries latency, tokens, and a correlation id   # M1
  Given a tenant with capture enabled, not under ZDR, and a streaming completion that runs to a clean SSE close with a terminal usage frame
  When the stream's clean-close capture hook fires (3rd of stream()'s 3 exit branches)
  Then the resulting request_logs row's prompt_tokens/completion_tokens/total_tokens match the terminal frame's usage dict exactly
  And latency_ms is a positive integer measured from the SAME _start_ns the call's OtelSpan used
  And request_id matches the SAME call's usage_records.raw->>'request_id'

Scenario: Cache-hit capture row carries the cached usage's tokens, not a re-derived count   # M1
  Given a tenant with capture and exact-match cache enabled, a prior identical prompt cached with usage {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12}
  When a new request hits the exact cache
  Then the request_logs row for the cache-hit carries prompt_tokens=5, completion_tokens=7, total_tokens=12 (the cached usage, unchanged/unrecomputed)
  And latency_ms and request_id are both populated (the cache-hit path reaches _dispatch_capture same as any other hook site)

Scenario: Guardrail-BLOCK capture row has latency but no token counts   # M1, M2
  Given a tenant with capture enabled and a guardrail configured in block mode, not under ZDR
  When a prompt is blocked pre-call (evaluate_pre BLOCK, request never reaches upstream)
  Then the resulting request_logs row has prompt_tokens=NULL, completion_tokens=NULL, total_tokens=NULL
  And latency_ms is still populated (a positive integer — time-to-block is meaningful)
  And request_id is still populated

Scenario: latency_ms is derived from the call's own _start_ns, never a second clock   # M3
  Given a fake/controlled time source and a non-streaming completion whose _start_ns is known
  When the capture hook fires at a known elapsed offset from _start_ns
  Then request_logs.latency_ms equals round((dispatch_time_ns - _start_ns) / 1_000_000) exactly
  And no other clock read (e.g. a fresh time.time_ns() unrelated to _start_ns) was used to compute it

Scenario: request_id correlates a request_logs row to its usage_records row, never a new usage_records column   # M4, Reject (frozen-contract)
  Given any proxied call that reaches a capture hook AND fires a usage record
  When both fire-and-forget writes land
  Then request_logs.request_id and usage_records.raw->>'request_id' are the SAME UUID string
  And usage_records' column list is UNCHANGED from before this task (no new column, verified against the FROZEN schema in its own docstring)

Scenario: Pre-existing request_logs rows read back with all 5 new columns NULL   # After (migration backward-compat)
  Given a request_logs row written before this migration (no request_id/latency_ms/prompt_tokens/completion_tokens/total_tokens ever set)
  When the migration runs and the row is re-read
  Then request_id, latency_ms, prompt_tokens, completion_tokens, total_tokens are all NULL
  And no migration error occurs, and no backfill pass runs
  And every pre-existing column's value is unchanged

Scenario: NoopPayloadCapture and callers that omit the new kwargs stay byte-identical   # M6
  Given a wiring with payload_capture=NoopPayloadCapture() (the default when capture is unconfigured)
  When a proxied call fires _dispatch_capture(..., usage=..., latency_ms=..., request_id=...)
  Then NoopPayloadCapture.capture() accepts all 3 new kwargs without raising
  And it remains a complete no-op (no row written anywhere, no exception, no change from today's behavior)

Scenario: Capture-store outage remains fail-open with the new fields present   # M7 (no new failure mode)
  Given a tenant with capture enabled, not under ZDR, and the request_logs INSERT (now carrying the 5 new columns) times out or the DB is unreachable
  When a proxied call completes
  Then the proxied caller receives the normal response, unaffected in status/body/latency beyond the bounded capture timeout running in a detached task
  And no request_logs row is written; the failure is logged, not raised
  And this failure mode is IDENTICAL to the pre-existing (payload-capture-store) fail-open behavior — no new timeout, no new retry, no new exception type introduced

Scenario: A build that tries to add a NOT NULL / defaulted column is rejected at review   # Reject
  Given a build diff that adds `latency_ms INTEGER NOT NULL DEFAULT 0` (or any non-null default on any of the 5 new columns)
  When the diff is reviewed against this contract
  Then it is flagged "ERR_SCHEMA_INVARIANT_VIOLATED" and rejected before merge
  And the existing pre-migration rows (which cannot supply a real value) are the concrete reason cited

Scenario: A build that adds a new usage_records column instead of using the raw JSONB extras seam is rejected   # Reject
  Given a build diff that adds `ALTER TABLE usage_records ADD COLUMN request_id UUID`
  When the diff is reviewed against this contract
  Then it is flagged "ERR_FROZEN_CONTRACT_VIOLATED" and rejected before merge
  And the diff is redirected to the UsageRecordExtras/raw-JSONB seam this contract specifies instead

Scenario: Tokens are stored verbatim from the usage dict, never recomputed from response bodies   # Reject (divergence risk)
  Given a call whose usage dict reports {"prompt_tokens": 8, "completion_tokens": 3} but whose response body's assembled text WOULD imply a different count if independently re-tokenized
  When the capture row is written
  Then request_logs.prompt_tokens=8 and completion_tokens=3 (the usage dict's values, verbatim)
  And no independent re-tokenization/recomputation code path exists anywhere in the capture write chain
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Least-sure flag surfaced at freeze: [spec] Correlation `request_id` is minted LOCALLY inside `complete()`/`stream()` (`uuid.uuid4()`), NOT reused from `RequestIdMiddleware`'s existing per-HTTP-request id (already structlog-bound, already in every access-log line) — chosen to avoid a new application→observability layering coupling or reopening the FROZEN `proxy-completions` public signatures. Tin should confirm this default or ask for the richer (but more invasive) middleware-id-reuse alternative — see Freeze question #1.

> Status: DRAFT — awaiting human freeze. No code has been written; this is the proposed additive shape only.

### No new HTTP surface — this is a schema + internal wiring change only
This task adds no endpoint and no request/response envelope. The externally-observable
change is: `request_logs` rows (read via the sibling `logs-explorer-api`, not directly by
this task) gain 5 populated fields when the underlying data was available at capture time.

### Schema (migration parents on CURRENT head `a1c5e7f9b3d6`, re-confirmed via `uv run alembic heads`)

```sql
-- request_logs: 5 additive NULLABLE columns, no default, no backfill
ALTER TABLE request_logs ADD COLUMN request_id        UUID    NULL;
ALTER TABLE request_logs ADD COLUMN latency_ms         INTEGER NULL;
ALTER TABLE request_logs ADD COLUMN prompt_tokens       INTEGER NULL;
ALTER TABLE request_logs ADD COLUMN completion_tokens   INTEGER NULL;
ALTER TABLE request_logs ADD COLUMN total_tokens        INTEGER NULL;

CREATE INDEX ix_request_logs_request_id ON request_logs (request_id)
  WHERE request_id IS NOT NULL;

-- usage_records: INDEX-ONLY — no column added, FROZEN column list untouched.
-- Supports "find the billing row for this request_logs row" without a table scan.
CREATE INDEX ix_usage_records_request_id ON usage_records ((raw ->> 'request_id'))
  WHERE raw ->> 'request_id' IS NOT NULL;

-- Downgrade: drop both indexes, then drop the 5 request_logs columns (symmetric,
-- reversible; usage_records itself is never touched beyond the index).
```

### `RequestLogRow` / `RequestLog` — new fields (mirror each other exactly)

```python
# logs/infrastructure/orm.py:RequestLogRow — 5 new mapped_column additions
request_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True, default=None)
latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)

# logs/domain/entities.py:RequestLog — matching frozen-dataclass fields (same 5, same order/types)
```

### `PayloadCapturePort.capture` — additive optional kwargs (frozen 9 kwargs unchanged)

```python
# proxy/domain/ports.py
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
    usage: dict[str, Any] | None = None,        # NEW additive — request-log-metering-fields §3
    latency_ms: int | None = None,               # NEW additive
    request_id: uuid.UUID | None = None,          # NEW additive
) -> None:
    """... (docstring gains: 'usage/latency_ms/request_id are DISPLAY-ONLY metadata,
    verbatim from the SAME values the call's billing/observability paths already
    computed — never independently recomputed, never billing truth.')"""
```

`NoopPayloadCapture.capture` and `SqlAlchemyPayloadCapture.capture` both gain the identical
3 optional kwargs; `SqlAlchemyPayloadCapture.capture` forwards them to `persist_request_log`
(itself gaining the same 3 optional kwargs, threaded straight into `_do_persist`/`_insert`/
`_INSERT_REQUEST_LOG`'s VALUES list — no scrub/truncate logic applies to these fields).

### `UsageRecordExtras` / `RecordingUsageRecorder` — one new optional extras key

```python
# proxy/domain/ports.py:UsageRecordExtras (TypedDict, total=False) — one new key
request_id: uuid.UUID

# usage/application/recorder.py:RecordingUsageRecorder
supported_extras: frozenset[str] = frozenset({..., "request_id"})  # existing set + 1

async def record(self, *, ..., request_id: uuid.UUID | None = None) -> None: ...
async def _record_internal(self, *, ..., request_id: uuid.UUID | None = None) -> None:
    ...
    if request_id is not None:
        raw_payload["request_id"] = str(request_id)   # same idiom as cached/pii_masked
```

### `use_cases.py` internal wiring (private, non-frozen — no public signature of any
FROZEN contract changes)

```python
# _dispatch_capture gains 3 forwarded params:
def _dispatch_capture(
    payload_capture: PayloadCapturePort | None, *, enabled: bool, tenant_id: ..., key_id: ...,
    model: str, request_body: dict[str, Any], response_body: dict[str, Any] | None, status: int,
    stream: bool, cached: bool, guardrail_configs: dict[str, Any],
    usage: dict[str, Any] | None = None,
    latency_ms: int | None = None,
    request_id: uuid.UUID | None = None,
) -> None: ...  # forwards all 3 straight into payload_capture.capture(...)

# _fire_record / _fire_record_cached / _fire_record_with_raw each gain:
    request_id: uuid.UUID | None = None,   # -> extras["request_id"] = request_id when not None

# CompletionUseCase.complete / .stream: alongside the existing `_start_ns = time.time_ns()`,
# mint `_request_id = uuid.uuid4()` ONCE per call. At each of the 11 _dispatch_capture call
# sites: latency_ms=round((time.time_ns() - _start_ns) / 1_000_000), request_id=_request_id,
# usage=<the already-in-scope local usage dict, or None at the 4 BLOCK sites>. The SAME
# _request_id is also passed to that site's adjacent _fire_record_*(... , request_id=_request_id).

# _try_cache_lookup gains 2 new REQUIRED kwargs (private method, single caller):
async def _try_cache_lookup(self, *, ..., start_ns: int, request_id: uuid.UUID) -> ...:
    ...  # its 3 internal _dispatch_capture/_fire_record_cached calls use start_ns/request_id
```

Access pattern: unchanged from payload-capture-store — INSERT-only, fire-and-forget, via
`logs/application/capture_writer.py`'s `session_factory()`. No UPDATE/DELETE path outside the
existing retention sweep (unaffected by this task — new columns are swept identically, no
special handling needed since DELETE doesn't reference a column list).

Glossary deltas:
- **Metering fields**: the additive `request_logs.latency_ms` / `prompt_tokens` / `completion_tokens` / `total_tokens` columns — DISPLAY-ONLY metadata snapshots sourced verbatim from the SAME in-flight values the proxy already computes for observability (the call's OtelSpan `_start_ns`) and billing (the call's `usage` dict), never independently computed and never billing truth.
- **Correlation key (request_id)**: a UUID minted once per proxied call in `proxy/application/use_cases.py`, stored verbatim on `request_logs.request_id` AND inside `usage_records.raw->>'request_id'` (no new `usage_records` column) — the join key between a captured log row and its billing-ledger row for the SAME call. Distinct from `RequestIdMiddleware`'s separate ASGI-layer per-HTTP-request id (not reused in this task — see Freeze question #1). [folded foundation-version 50]

### Freeze questions (Tin rules on these before Status can move to FROZEN)

1. **Local-mint `request_id` (this draft's default) vs. reuse `RequestIdMiddleware`'s existing ASGI-layer id.** Local-mint is self-contained (touches only files already in this task's Scope, no layering coupling, no reopening the FROZEN `proxy-completions` signatures). Reusing the middleware id gives a richer correlation (one id spanning the access-log line + `request_logs` + `usage_records`) but costs either a new `structlog.contextvars` import in the application layer or a signature change to `complete()`/`stream()` plus a router-layer edit outside this task's grounded file list. Recommendation: local-mint for v1; richer reuse as a follow-up spec delta if ops actually needs cross-log-line correlation.
2. **`total_tokens` stored verbatim vs. derived.** Recommendation: verbatim from `usage.get("total_tokens")` — zero computation, matches the upstream-reported figure exactly, including any tier the minimal 3-field set doesn't itself track.
3. **Whether to also mirror `usage_records`' richer per-tier token columns (`cached_tokens`, `reasoning_tokens`, `cache_creation_tokens`, `audio_*_tokens`) onto `request_logs` now.** Recommendation: DEFER — the stated gap is "can't show latency/tokens," not tier-level billing detail; keeps this change-request minimal and reversible.

Status: FROZEN @ v1 — approved by Tin Dang
Reported: yes — presented for freeze 2026-07-10.
Decided at freeze (orchestrator auto-mode, 2026-07-10; additive BE change-request Tin approved): mint
`request_id` LOCALLY inside `complete()`/`stream()` (option A) — self-contained, no application→
observability layering coupling, no reopening of the frozen proxy-completions signatures. Cross-log
correlation with the ASGI `RequestIdMiddleware` id is deferred as an OBSERVE delta. Per-tier token
columns (freeze Q3) DEFERRED — v1 carries latency_ms + prompt/completion/total tokens only.

Least-sure flag surfaced at freeze: [spec] correlation `request_id` minted LOCALLY inside
`complete()`/`stream()` vs reused from `RequestIdMiddleware` — RESOLVED at freeze: local mint (option A),
cross-log correlation deferred.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: <e.g. 90%>
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_<scenario>: arrange <Given> / act <When> / assert <Then> + assert <unchanged> · covers: <M#, R:code — optional>
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/logs/infrastructure/orm.py` · `apps/gateway/src/gateway/logs/application/capture_writer.py` · `apps/gateway/src/gateway/logs/infrastructure/sqlalchemy_capture.py` · `apps/gateway/src/gateway/logs/domain/entities.py` · `apps/gateway/src/gateway/proxy/domain/ports.py` · `apps/gateway/src/gateway/proxy/infrastructure/payload_capture_noop.py` · `apps/gateway/src/gateway/proxy/application/use_cases.py` · `apps/gateway/src/gateway/usage/application/recorder.py` · `apps/gateway/migrations/versions/` · `apps/gateway/migrations/env.py`

Strategy (ordered batches): 1. Schema: new migration (5 `request_logs` columns + 2 index-only additions), parented on the CURRENT head, single-head verified after. 2. `logs/infrastructure/orm.py:RequestLogRow` + `logs/domain/entities.py:RequestLog` — add the 5 matching fields (mechanical, mirror each other). 3. `logs/application/capture_writer.py` — thread `usage`/`latency_ms`/`request_id` through `persist_request_log` → `_do_persist` → `_insert` → `_INSERT_REQUEST_LOG`'s column/VALUES list (no scrub/truncate logic touches these — pure pass-through). 4. `proxy/domain/ports.py` — add the 3 optional kwargs to `PayloadCapturePort.capture`, and the 1 new key to `UsageRecordExtras`. 5. `proxy/infrastructure/payload_capture_noop.py:NoopPayloadCapture.capture` — mirror the 3 new optional kwargs (discard). 6. `logs/infrastructure/sqlalchemy_capture.py:SqlAlchemyPayloadCapture.capture` — forward the 3 kwargs to `persist_request_log`, after the existing ZDR/concurrency gates (unchanged). 7. `usage/application/recorder.py` — add `request_id` to `supported_extras` + `record()`/`_record_internal()` + the ONE shared `raw_payload` build site (confirm via re-read that it still feeds both the XADD-success and `_fallback_insert` paths). 8. `use_cases.py` — mint `_start_ns`-adjacent `_request_id = uuid.uuid4()` once per call in `complete()`/`stream()`; add `start_ns`/`request_id` params to `_try_cache_lookup`; add `request_id` param to `_fire_record`/`_fire_record_cached`/`_fire_record_with_raw`; add `usage`/`latency_ms`/`request_id` params to `_dispatch_capture`; update all 11 `_dispatch_capture(...)` call sites AND their adjacent usage-record-fire calls to pass the newly-available values — grep-verify the call count is still 11 before considering this batch done. 9. Tests + red/green verification, including the fake-clock latency-source test (Scenario M3) and the request_id-correlation test (Scenario M4) as the two highest-value new assertions (existing payload-capture-store scenarios must stay green unmodified).

Persona (required): generic (backend/data-model discipline) — no project `flow: design` persona is a close domain fit for a backend schema+wiring change (the closest domain personas, `backend-architect`/`billing-precision-engineer`, are tagged `flow: build, advisor`, not `design`); BUILD should still honor `backend-architect.md`'s clean-architecture/Protocol-port discipline and `billing-precision-engineer.md`'s "never a bare number, always provenanced" instinct (applied here as: these new fields are metadata snapshots, never silently promoted to billing truth) as domain stances, without formally adopting either as this task's Persona line.
Spawn isolation (default): worktree — mirrors payload-capture-store's own precedent; no stated reason to deviate.
Known-problem fixes: 11 mechanical call-site edits is the single biggest risk of a silent miss → grep-verify `_dispatch_capture(` count stays 11 (pre-change) = 11 (post-change), and that every one of the 11 now passes `usage=`/`latency_ms=`/`request_id=` (or the BLOCK sites' explicit `usage=None`) · a build tempted to "simplify" by also adding a real `request_id` column to `usage_records` must be stopped — Reject scenario + Must both name this explicitly · `_try_cache_lookup`'s new required kwargs must be added at its ONE call site (line ~1872) in the same commit as the signature change, or the suite fails to import, not just fails a test — sequence batch 8 as one atomic edit, not a signature-then-callsite split.
Safety rule (feature-specific): `request_id` must NEVER be treated as a uniqueness/idempotency key anywhere (no `ON CONFLICT`, no unique constraint) — it is a plain best-effort correlation label on two independent fire-and-forget writes; a build that adds a unique constraint would risk turning a rare, harmless dual-fire (e.g. a retried internal call) into an unhandled IntegrityError on the fail-open capture path, which is exactly the failure mode payload-capture-store's Must #2 (fail-open for the proxied response) forbids.
Code lives in: the Scope list above (existing `logs/`, `proxy/`, `usage/` bounded contexts — no new module).
Constraints: do NOT change any test or the contract; allow-list packages only (no new third-party dependency expected — `uuid`, `time` are stdlib, already imported in `use_cases.py`); ask if unclear.

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
- [x] A non-streaming/streaming/cache-hit/BLOCK completion each produce a `request_logs` row whose `request_id` is byte-identical to the SAME call's `usage_records.raw->>'request_id'` — confirmed by real Postgres HTTP-level tests (`tests/request_log_metering_fields/test_request_log_metering_fields.py::test_non_streaming_capture_carries_latency_tokens_and_correlation_id`, `::test_streaming_clean_close_...`, `::test_request_id_correlates_rows_and_usage_records_has_no_new_column`) AND by my own throwaway 5-concurrent-call probe (deleted after use) asserting `{request_logs.request_id} == {usage_records.raw->>'request_id'}` set-equality with zero cross-contamination.
- [x] `usage_records` gains NO new column (correlation rides the `raw` JSONB extras seam only) — confirmed by `information_schema.columns` introspection in `test_request_id_correlates_rows_and_usage_records_has_no_new_column` + `test_usage_records_gains_no_new_column_standalone`, and by reading `usage/application/recorder.py:_record_internal` (request_id write is `raw_payload["request_id"] = str(request_id)`, before `event_fields` construction — no schema touch).
- [x] Guardrail-BLOCK rows have `latency_ms` populated but `prompt_tokens`/`completion_tokens`/`total_tokens` all NULL (never 0) — confirmed by `test_guardrail_block_capture_has_latency_but_no_tokens` (real HTTP call through a block-mode guardrail) AND by reading `sqlalchemy_capture.py:_verbatim_token_count` (`usage=None` → `not isinstance(None, dict)` → `None`, never `0`).
- [x] `latency_ms` is derived from the SAME `_start_ns`/`_request_id` as the call's OtelSpan, never a second clock — confirmed by `test_latency_ms_derived_from_start_ns_never_second_clock` (monkeypatches `use_cases.time.time_ns` to a constant; asserts `latency_ms == 0`, which only holds if both the start and dispatch reads go through the SAME patched clock).
- [x] Pre-existing `request_logs` rows read back with all 5 new columns NULL, no backfill, no error — confirmed by `test_pre_existing_rows_read_back_with_new_columns_null` (raw INSERT omitting the 5 new columns, then SELECT) AND by live `alembic upgrade head` on a fresh `gateway_migrations_test_vm` DB (clean single-head run, no errors, `a1c5e7f9b3d6 -> a55ddcebaac6` applies additively).
- [x] Billing exactness (cost_usd/quantity/prompt_tokens/completion_tokens on `usage_records`) is UNCHANGED by metering, whether capture is ON or OFF — confirmed by my own throwaway probe (`test_billing_cost_and_quantity_unaffected_by_metering`, deleted after use): two tenants, identical call, capture ON vs OFF → byte-identical `cost_usd`/`quantity`/`pricing_unit`/token counts on `usage_records`, and exactly 1 `usage_records` row per call.
- [x] ZDR-suppressed tenants get NO `request_logs` row at all (metering columns cannot resurrect a suppressed row) — confirmed by reading `sqlalchemy_capture.py:capture()`: the `if is_zdr: return` early-exit is BEFORE `persist_request_log(...)` is ever called, untouched by this task's diff (0 lines changed in that gate).
- [x] `NoopPayloadCapture`/omitted-kwarg callers stay byte-identical — confirmed by `test_noop_and_omitted_kwargs_stay_byte_identical` (all-3-kwargs-supplied AND all-3-omitted, both no-op, no raise).
- [x] Capture-store outage stays fail-open, no new failure mode, with the 5 new fields present — confirmed by `test_capture_store_outage_fail_open_with_new_fields_present` (simulated 5s-hung DB session, `timeout_seconds=0.1`, elapsed < 2.0s, no raise).
- [x] Tokens stored verbatim from the `usage` dict, never re-derived from response-body content — confirmed by `test_tokens_stored_verbatim_never_recomputed_from_response_body` (usage dict deliberately mismatched vs. a much-longer response body; stored values match the usage dict exactly).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced; record where / how confirmed — `usage`/`latency_ms`/`request_id` on `_dispatch_capture` are read at all 11 call sites (grep-verified `_dispatch_capture(` count = 12 = 1 def + 11 calls, matching TASK.md §5's own self-check); `_request_id`/`start_ns` added to `_try_cache_lookup`'s ONE caller (`complete()`); `request_id` param on `_fire_record`/`_fire_record_cached`/`_fire_record_with_raw` feeds `UsageRecordExtras["request_id"]` at every call site touched; `_verbatim_token_count` is called exactly once, inline in `SqlAlchemyPayloadCapture.capture`, for all 3 token fields.
- [x] DEAD-CODE (code) — no new unused or orphaned symbol introduced — every new column/kwarg/param traced to a live read (5 ORM columns read back in tests + the INSERT's VALUES list; `UsageRecordExtras["request_id"]` consumed by `_record_internal`'s `supported_extras` filter); no new helper left uncalled.
- [ ] SEMANTIC (prose / non-code) — not applicable (this is a pure schema+wiring change, no prose/doc artifact in scope).

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by direct read of the integrated diff (`0c46c99..9369cf0`) for all 9 touched files (`orm.py`, `entities.py`, `ports.py`, `payload_capture_noop.py`, `use_cases.py`, `recorder.py`, `capture_writer.py`, `sqlalchemy_capture.py`, the new migration) — every signature matches §3 verbatim (kwarg names, defaults, types).
- [x] any anchor that moved/renamed since Ground SHA is named here, not left silent — ONE real anchor drift, benign: the migration's `down_revision` (`a1c5e7f9b3d6`) is no longer the alembic HEAD in the fully-integrated tree — `domain-capture` (`b3d8e1f4a7c2`) and later tasks re-parented onto THIS task's own new revision (`a55ddcebaac6`) as the wave-2 integration proceeded (see commit `dca783b` "re-parent domain-capture onto metering head"), which is the CORRECT direction (this task's migration became an intermediate link, not orphaned) — confirmed via a clean `alembic upgrade head` on a fresh `gateway_migrations_test_vm` DB, single head (`69cfdc584129`), no branch/multi-head error.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: add-verify (self) · adversarially checked: (1) union-merge corruption between this task and the concurrently-merged `guardrail-analytics` task, both editing the SAME 2 BLOCK call sites in `use_cases.py` — read the integrated file directly, confirmed both `_dispatch_guardrail_verdicts(...)` and `_dispatch_capture(..., request_id=_request_id)` are present, neither clobbered the other, pyright 0 errors on the file. (2) Billing-exactness + 1:1 usage_records-per-request + concurrent-call request_id non-collision — wrote and RAN a throwaway 2-test probe (5 concurrent calls: unique, non-cross-contaminated request_ids, exact set-equality between `request_logs.request_id` and `usage_records.raw->>'request_id'`; capture-ON vs capture-OFF tenant: byte-identical cost_usd/quantity/tokens, exactly 1 usage_records row) — both passed, then deleted per rules. (3) ZDR-suppression-resurrection — read `sqlalchemy_capture.py:capture()`: the `if is_zdr: return` gate is untouched (0 lines in the diff), sits BEFORE `persist_request_log` is ever called, so the 5 new columns cannot resurrect a suppressed row. (4) Token-verbatim-extraction divergence between the metering read path and the billing read path on the SAME `usage` dict — found and confirmed a REAL (low-probability, non-security, non-billing-affecting) divergence: see FINDINGS below.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: add-verify (self)
1. Security: CLEAR — no new IO seam, no new secret/credential surface, no injection opening (raw INSERT uses SQLAlchemy `text()` bound params, unchanged idiom); `request_id` is explicitly barred from ever becoming a uniqueness/idempotency constraint (§5 Safety rule honored — no `ON CONFLICT`/unique index added on it, confirmed by reading the migration); tenant-scoping unchanged (every new field rides inside the existing tenant-scoped INSERT/extras path, no new cross-tenant read surface introduced by this task).
2. Concurrency: CLEAR — fire-and-forget/bounded-timeout/never-retried posture is byte-identical (0 new IO seam per Must); `_request_id`/`_start_ns` are per-call locals (no shared mutable state), confirmed non-colliding under 5 real concurrent HTTP calls in my own probe.
3. Architecture: CLEAR — additive-only on both sides (5 NULLABLE `request_logs` columns, 1 JSONB extras key on `usage_records`, zero new columns on the FROZEN `usage_records` table); clean-architecture layering unchanged (no new cross-module import beyond stdlib `uuid`/`time`, already present); the union-merge with the concurrently-built `guardrail-analytics` task at the SAME 2 call sites in `use_cases.py` integrated cleanly with no logic loss (verified above).
Verdict: PASS
Residue: 1 MINOR/note finding (token-verbatim type-coercion divergence, see FINDINGS) — accepted, not a blocker; see Residual risks.
Binding: advisory — non-mechanical, standard-sensitivity additive change-request

### GATE RECORD
Reported: yes — this verify pass (evidence gathered: 12/12 task-suite tests pass, 34/34 sibling payload-capture-store+usage-metering tests pass unmodified, clean single-head migration apply, 2/2 throwaway adversarial probes pass, pyright 0 errors) is the gate report.
Outcome: PASS
Reviewed by: add-verify (self, recommendation — human/orchestrator records the binding outcome) · date: 2026-07-11

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by add-verify (self, recommendation — human/orchestrator records the binding outcome))

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

## Design self-score

- Completeness: 0.93 — every field the dispatch objective named (latency, prompt/completion tokens, correlation key) is traced to a concrete symbol-level source (the SAME `usage` dict, the SAME `_start_ns`), all 11 real call sites enumerated by line number and cross-checked against the frozen `PayloadCapturePort` docstring's own hook-site list, and the `usage_records`-side correlation is solved without reopening its FROZEN column list. Held below 0.95 because the `request_id` source (local-mint vs. `RequestIdMiddleware` reuse) is a genuine, unresolved product/architecture trade-off, not a guess dressed as ground truth — surfaced honestly as the ⚠ flag rather than silently picked.
- Clarity: 0.93 — every Must names its exact symbol and exact source value (never "compute latency" without naming `_start_ns`); the Schema/Protocol/wiring deltas in §3 are copy-pasteable signatures, not prose descriptions; Freeze questions are phrased as concrete choices with a stated recommendation each.
- Practicality: 0.92 — no new IO seam, no new dependency, no reopened frozen contract; the ONE mechanical risk (11 call sites) is named explicitly with a concrete grep-count self-check in §5 Known-problem fixes rather than left to build-time discovery.
- Optimization: 0.90 — chose the minimal viable field set (3 token fields, not the full 8-field tiered set `usage_records` carries) and an index-only `usage_records` touch (no column) — deliberately NOT gold-plating a display-only metadata store; held at 0.90 rather than higher because the tier-deferral (Assumption #2) is itself a judgment call a human may weigh differently.
- Edge cases: 0.92 — guardrail-BLOCK rows (no usage dict), pre-existing rows (NULL backfill), Noop/byte-identical callers, capture-store-outage fail-open with the new columns present, and the "don't let a build silently reopen the frozen usage_records schema" case are all scenario-ized, not just narrated.
- Self-evaluation: 0.92 — this file names 2 concrete alternative designs it rejected (heuristic JOIN, real FK column) with the specific reason each fails, and 1 it deliberately did NOT choose despite being architecturally richer (middleware-id reuse), with the exact file/line cost of reversing that choice spelled out for a future build.

All six ≥ 0.90; no refinement pass required before returning.
