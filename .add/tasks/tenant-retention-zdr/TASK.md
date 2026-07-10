# TASK: Per-tenant retention policy + Zero-Data-Retention mode

slug: tenant-retention-zdr · created: 2026-07-10 · stage: production
milestone: enterprise-identity-compliance
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: tests   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/usage/application/retention_sweep.py:RetentionSweeper` — the ONLY retention mechanism today. `sweep_once()` runs three UNCONDITIONAL, operator-wide, NON-tenant-scoped bounded-batch DELETEs (`_DELETE_USAGE_BATCH`, `_DELETE_ALERT_BATCH`, `_DELETE_AUDIT_BATCH` — no `WHERE tenant_id` clause anywhere). `effective_audit_window` property = `max(retention_audit_events_days, retention_audit_floor_days)` — the audit floor is enforced ONLY here, in-process; there is no per-tenant hook of any kind today.
- `apps/gateway/src/gateway/core/config.py:571-587 Settings` — the 6 operator-wide retention knobs: `retention_check_interval_seconds`, `retention_usage_records_days`, `retention_alert_events_days`, `retention_audit_events_days`, `retention_audit_floor_days` (HARD floor, comment: "EFFECTIVE=max(knob,floor)"), `retention_batch_size`. No per-tenant knob exists.
- `apps/gateway/src/gateway/main.py:507-520,609-617` — sweeper wiring: `app.state.retention_sweeper_task` created via `should_start_retention_sweep(_settings)` gate at lifespan startup; cancelled + awaited at shutdown. Any extension must preserve this default-ON-when-configured, clean-cancel shape.
- `apps/gateway/src/gateway/audit/infrastructure/audit_repository.py:10-12` — `audit_events` immutability: UPDATE always RAISEs; DELETE RAISEs unless the transaction-scoped GUC `app.audit_purge='on'` is set (only `RetentionSweeper._delete_audit_batched` sets it). Migration `apps/gateway/migrations/versions/f2a4c6e8b0d3_audit_retention_trigger.py` is the trigger source.

**Payload-bearing store inventory (exhaustive — every store that can hold prompt/response/file CONTENT, not just usage metadata):**
1. `apps/gateway/src/gateway/artifacts/infrastructure/orm.py:53-55 ArtifactRow` — `content: bytes | None` (BYTEA, inline) when `storage_backend='db'`; `object_key: str | None` (pointer into the object store) when `storage_backend='s3'`. Tenant-scoped (`tenant_id`, `key_id`). Delete path for s3-backed rows must also call the object store, not just drop the DB row.
2. `apps/gateway/src/gateway/conversations/infrastructure/orm.py:36,76 ConversationRow.title` (nullable free text) + `ConversationMessageRow.content` (Text, NOT NULL) — chat-playground history. Tenant-scoped via `ConversationRow.tenant_id`.
3. `apps/gateway/src/gateway/memory/infrastructure/orm.py:50,52 MemoryRow` — `content: str` (Text) + `embedding: list[float] | None` (pgvector). Tenant-scoped.
4. `apps/gateway/src/gateway/batches/infrastructure/orm.py:120,126 BatchJobItemRow` — `request_body: dict` (JSONB, NOT NULL) + `result_body: dict | None` (JSONB) — the actual batched completion payloads. Tenant-scoped via `BatchJobItemRow.tenant_id`.
5. `apps/gateway/src/gateway/video/infrastructure/orm.py:51-52 VideoGenerationJobRow` — `prompt: str` (Text, NOT NULL) + `params: dict | None` (JSONB). `result_artifact_id` fans out to store #1 (already covered — no double-gate needed, the video use case never reaches the artifact write when its own gate fires first).
6. `apps/gateway/src/gateway/proxy/infrastructure/response_cache.py:201-231 RedisResponseCache` — exact-match completion cache. Key format `resp-cache:{tenant_id}:{sha256(...)}` (`build_cache_key`, line 38-48) — ALREADY tenant-prefixed, TTL-bound (`SET ... EX=ttl_seconds`). Value = the full response body. Write site: `apps/gateway/src/gateway/proxy/application/use_cases.py:1672-1676`.
7. `apps/gateway/src/gateway/proxy/infrastructure/vector_cache.py:69-73,137-164 RedisVectorCache` — semantic-cache pointer layer. Namespace `vec-cache:{tenant_id}:{sha256(model)}` (`_namespace`) — ALREADY tenant-prefixed. Stores an embedding vector + a pointer key into store #6 (not raw text, but still a payload-DERIVED, tenant-scoped store — included for exhaustiveness). Write site: `use_cases.py:1693-1695`.

**Ruled OUT (checked, not payload-bearing):**
- `apps/gateway/src/gateway/usage/infrastructure/orm.py:36-109 UsageRecordRow.raw` (JSONB) — confirmed metadata-only (token counts / provider usage frame), never echoes prompt or completion text; no call site writes message content into it. Billing exactness depends on this row staying untouched by ZDR.
- `audit_events` — `AuditEvent` (`apps/gateway/src/gateway/audit/domain/audit_event.py`) fields are `action`/`target_type`/`target_id`/`result`/`metadata`; every real emission site found (`platform_audit.py`, `audit_writer.py`, the various admin routers) logs config/action metadata (e.g. `{"table": ..., "rows_deleted": ...}`), never proxied request/response content. Stays governed solely by the existing operator-wide floor — never tenant-overridable (milestone shared decision: "the audit-retention compliance floor must stay un-shortenable").
- Realtime relay transcripts (`apps/gateway/src/gateway/proxy/api/realtime_ws.py`) — `audio_buffer` is an in-process `bytearray`, reset every turn (`realtime_ws.py:559,569`), never persisted. The derived text transcript is routed through `_real_chat` (`realtime_ws.py:215-299`) as an ordinary chat-completion call — it flows through stores #6/#7 above, already inventoried; no separate transcript table exists.
- `apps/gateway/src/gateway/proxy/infrastructure/openai_realtime.py` / `gemini_live.py` transcript event translators — pass-through framing only, no persistence.
- `payload-capture-store` (request/response log table) — does **not exist yet**. It is a sibling task (`.add/tasks/payload-capture-store/TASK.md`, milestone `logs-explorer-guardrails-v2`) still at `phase: ground` (blank template, confirmed by reading it) — the milestone's shared decision ("ZDR interplay", `logs-explorer-guardrails-v2/MILESTONE.md:14`) says that task "freezes that hook here", i.e. it owns wiring the actual capture-write gate. Nothing in this task's inventory can consume a hook that has not been designed. See Issues/Risks below.

**Existing tenant-scoped policy conventions this design must reuse (not invent):**
- `apps/gateway/src/gateway/tenants/infrastructure/orm.py:106-117 TenantRow` already carries additive per-tenant boolean/JSON columns set via server_default (`guardrail_configs`, `semantic_cache_enabled`, `batch_grouping_enabled`) — each threaded through `keys/infrastructure/repository.py:126-201 ApiKeyRepository.get_by_id` (LEFT JOIN `tenants`, zero extra DB reads) into `keys/domain/entities.py:33-43 ApiKey` and `:94-105 AuthzResult`, both with the exact comment pattern `"Populated via LEFT JOIN tenants in get_by_id() — zero extra DB reads."`. This is the proven per-request, always-fresh (no JWT staleness) wiring path for a new `zdr_enabled` field.
- Admin config surface precedent: `apps/gateway/src/gateway/tenants/api/guardrail_router.py` (`GET/PUT /admin/guardrails`, `guardrail_router.py:1-8` contract docstring) — GET = any authenticated role (`get_identity`), PUT = owner/admin only (`require_owner_or_admin`, `apps/gateway/src/gateway/keys/api/deps.py:73-89`), partial-merge body semantics, 422 `ERR_PAYLOAD_INVALID` on bad values.
- `apps/gateway/src/gateway/tenants/domain/authz.py:54-69 Permission` StrEnum + `:76-89 ROLE_PERMISSIONS` — `SECURITY_CONFIG` is the one permission explicitly OWNER-only-preserved (`authz.py:88` comment: "NOT PROVIDER_SECRETS, NOT SECURITY_CONFIG (owner-only preserved)"). `require_permission(Permission.SECURITY_CONFIG)` (`authz.py:230-245`) is the existing gate for exactly this class of high-stakes tenant config — reuse it rather than inventing a new Permission member (PROJECT.md DDD note: a `Permission` enum member cannot express "excludes OWNER" under the completeness guard `ROLE_PERMISSIONS[OWNER] == frozenset(Permission)`; SECURITY_CONFIG already gets this for free by simply not being granted to ADMIN).
- `apps/gateway/src/gateway/objectstore/port.py:14-29 ObjectStore` Protocol — `delete(key)` is explicitly idempotent ("absent is a no-op"), `ObjectNotFoundError` vs `ObjectStoreUnavailableError` are distinguished — the artifact-purge path must retry only on `ObjectStoreUnavailableError`, treat `ObjectNotFoundError` as success.
- Repository `create()` is the single choke point per store (`artifacts/infrastructure/repository.py:32`, `conversations/infrastructure/repository.py:29,167 create/append_message`, `memory/infrastructure/repository.py:32`, `batches/infrastructure/repository.py:47`, `video/infrastructure/repository.py:37`) — every module follows CLEAN ARCHITECTURE (CONVENTIONS.md) with one `.create()` per aggregate root, which is the natural, DRY place for a fail-closed gate (a single guard per store beats N scattered call-site guards, matching the "positive allow-list, checked fresh" precedent set by the S2-4 egress SSRF guard).

Context (working folder): `.add/milestones/enterprise-identity-compliance/MILESTONE.md` (this task's owning milestone — Shared decisions §18-24, Tasks §36, Exit criteria §43) · `.add/milestones/logs-explorer-guardrails-v2/MILESTONE.md` (sibling milestone owning the payload-capture-store hook, §14/§27/§32/§41).

Honors (patterns / conventions): CONVENTIONS.md — CLEAN ARCHITECTURE per module (domain→application→infrastructure→api, dependencies point inward only), additive Alembic migrations only, `ERR_<DOMAIN>_<REASON>` machine codes over RFC 9457 problem+json, every outbound IO gets timeout + bounded idempotent retry + circuit breaker, all tenant data `tenant_id`-scoped, red/green TDD mandatory.

Seams consulted: none in SEAMS.md yet matching this feature (no `.add/SEAMS.md` entry found for retention/ZDR).

Anchors the contract cites: `RetentionSweeper` (extend), `TenantRow` (extend), `ApiKey`/`AuthzResult` (extend), `ApiKeyRepository.get_by_id` (extend), `guardrail_router.py` (pattern-clone target), `Permission.SECURITY_CONFIG`/`require_permission` (reuse), `ObjectStore.delete` (reuse), the five `*Repository.create`/`append_message` choke points (gate insertion point).

Issues/Risks (→ feed §1):
- **The payload-capture-store hook does not exist to consume.** Both tasks are ungrounded siblings in different milestones with no ordering dependency declared between them (`enterprise-identity-compliance/MILESTONE.md` lists `tenant-retention-zdr depends-on: none`). This task can only PUBLISH a read port (`is_zdr(tenant_id) -> bool`, backed by the same fresh `tenants.zdr_enabled` column) for `payload-capture-store` to consume once IT grounds and freezes its own capture-write gate — it cannot freeze the other task's hook. Recorded as a cross-milestone open question, not silently resolved.
- Extending `RetentionSweeper` to be tenant-aware for 5 NEW tables (none currently swept) plus 2 EXISTING tables (`usage_records`, `alert_events`) is real surface growth on a component with a FROZEN v1 contract (`retention_sweep.py:3 "CONTRACT (FROZEN @ data-retention-controls v1 — TASK.md §3)"`) — this is an additive extension of that contract, not a rewrite; the existing 3 unconditional DELETEs must stay byte-identical for tenants with no override.
- Dashboard-session-JWT-authenticated writers (if any exist for artifacts/memories/conversations outside the `sk-` API-key `/v1/*` surface) resolve identity via a different path than `ApiKeyRepository.get_by_id` — the fresh-`zdr_enabled` LEFT JOIN convention must be verified against every write path at BUILD, not assumed from the api-key path alone.
- Redis cache purge on ZDR-enable (stores #6/#7) needs a bounded `SCAN MATCH {prefix}:{tenant_id}:*` + `DEL` cursor loop, never `KEYS` (blocking, unbounded) — no existing precedent for a tenant-scoped Redis SCAN in this codebase; this is new infrastructure code, not a reuse.

Related intent: PROJECT.md "Domain (DDD)" — the `Permission`-completeness-guard note (`ROLE_PERMISSIONS[OWNER] == frozenset(Permission)`) directly informs the RBAC gate choice below. `enterprise-identity-compliance/MILESTONE.md` goal line + rationale (Track A competitive-gap analysis, Tin-approved 2026-07-10) is the WHY.

Ground SHA: `2071046`

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Per-tenant retention window + Zero-Data-Retention (ZDR) mode over the existing operator-wide RetentionSweeper

Framings weighed:
- **(chosen) Two orthogonal controls on one tenant-facing surface**: (a) `window_days` — a per-tenant override (≤ operator ceiling) of how long payload-bearing + usage/alert rows live before the sweeper purges them; (b) `zdr_enabled` — a stronger, independent switch that forces the effective payload retention to ZERO (no window matters once ZDR is on) and fail-closed-blocks new payload writes at the source, not just at sweep time. Chosen because the milestone exit criterion ("zero payload rows in every inventoried store while billing stays exact") requires write-time prevention, not just faster deletion, and because "retention window" and "never write it at all" are genuinely different guarantees a compliance buyer will ask about separately.
- (alternative, rejected) ZDR as merely `window_days=0`: rejected — a 0-day window still implies "write then immediately eligible for the next sweep tick", which is an eventually-consistent guarantee, not the "never persisted anywhere" fail-closed guarantee the milestone's Shared Decision explicitly demands. A crash between write and next sweep tick would leave payload rows resting in the DB.
- (alternative, rejected) Gate every payload write at each of the ~8 call sites individually (use-case layer): rejected in favor of gating once per store inside each `*Repository.create()`/`append_message()` — DRYer, and any FUTURE caller of an existing repository automatically inherits the guard (matches the fail-closed "checked fresh, positive allow-list" precedent from the S2-4 egress guard) instead of requiring every new caller to remember to add its own check.

Must:
<must>
  - M1: `GET /admin/retention-policy` — any authenticated tenant role — returns the tenant's current `window_days` (nullable = inherits operator default), the computed `effective_window_days` per store class, `zdr_enabled`, `zdr_enabled_at`, and `operator_ceiling_days` (read-only, informational — the max a tenant may self-set).
  - M2: `PUT /admin/retention-policy` — requires `Permission.SECURITY_CONFIG` (OWNER only, reusing the existing owner-only-preserved permission — no new `Permission` enum member) — partial-merge body (`window_days?`, `zdr_enabled?`), same merge semantics as `guardrail_router.py` (absent keys preserved).
  - M3: `window_days`, when set, applies uniformly to: `usage_records`, `alert_events` (both already swept — extend with an optional per-tenant cutoff), `artifacts`, `conversation_messages`+`conversations`, `memories`, `batch_job_items`, `video_generation_jobs` (five newly-swept payload tables) + Redis stores #6/#7 (TTL clamp: a shorter tenant window than the cache's own TTL takes effect at the next sweep tick, not by rewriting live TTLs). `audit_events` is explicitly and permanently EXCLUDED from `window_days` — no field, no code path, ever touches it; it stays governed solely by `effective_audit_window` (operator knob + floor).
  - M4: `window_days` MUST be ≤ `operator_ceiling_days` (a NEW operator `Settings` field, e.g. `retention_tenant_window_ceiling_days`, default 365) — tenants can only shorten, never lengthen, retention below/above the operator's own configured per-table defaults for the swept tables they touch.
  - M5: `zdr_enabled=true` fail-closed-blocks, at the START of each of the five repository choke points (`ArtifactRepository.create`, `ConversationRepository.create`/`append_message`, `MemoryRepository.create`, `BatchJobRepository.create` for items carrying `request_body`, `VideoJobRepository.create`), returning `403 ERR_ZDR_PAYLOAD_BLOCKED` — checked via a FRESH per-request `tenants.zdr_enabled` read (no caching beyond the existing per-request `ApiKeyRepository.get_by_id` LEFT JOIN), never a cached/stale flag.
  - M6: `zdr_enabled=true` silently skips (no error, no behavior change visible in the response) the two cache writes (#6 exact response cache, #7 vector/semantic cache) — matches the existing `cache_enabled=false`/`semantic_cache_enabled=false` silent-no-op precedent; the proxied completion/embedding/audio call itself succeeds byte-identically.
  - M7: Transitioning `zdr_enabled: false -> true` triggers the sweeper's regular cycle (`RetentionSweeper.sweep_once`, extended) to unconditionally purge ALL existing rows for that tenant across the five payload tables PLUS a bounded `SCAN`+`DEL` sweep of that tenant's Redis namespaces (`resp-cache:{tenant_id}:*`, `vec-cache:{tenant_id}:*`) PLUS an `ObjectStore.delete()` call for every `artifacts` row with `storage_backend='s3'` — self-healing: re-runs on every sweep tick for as long as `zdr_enabled=true`, so a mid-purge process crash is recovered automatically at the next tick (no separate job-tracking table).
  - M8: `usage_records` rows keep being written normally under ZDR — token counts/cost/model_id are metadata (confirmed §0), never gated; billing stays exact.
  - M9: The read port (`is_zdr(tenant_id) -> bool` / equivalent) that `payload-capture-store` will consume is defined and exported by this task; this task does NOT modify the (nonexistent) capture table itself.
  - M10: Every purge/gate action (ZDR enable/disable, window change, ZDR-triggered bulk purge completing a table) emits a `record_audit` event (system-level for the sweeper-driven bulk purge, actor-level for the PUT), mirroring `RetentionSweeper._emit_purge_audit`.
</must>
Reject:
<reject>
  - R1: `window_days <= 0` or non-integer or `window_days > operator_ceiling_days` -> "ERR_RETENTION_WINDOW_INVALID" (422)
  - R2: PUT by a MEMBER/ADMIN/VIEWER/BILLING_ADMIN/OPERATOR role (SECURITY_CONFIG not held) -> "ERR_AUTH_FORBIDDEN" (403, reuses existing code — no new error needed)
  - R3: A write to any of the five payload repositories while `zdr_enabled=true` -> "ERR_ZDR_PAYLOAD_BLOCKED" (403) — the row is never created (not partially written, not written-then-deleted)
  - R4: Any attempt (request field, admin action) to set/shorten/lengthen `audit_events` retention per-tenant -> not a rejectable input because no such field is ever exposed in the contract (structurally impossible, not runtime-validated) — the audit floor stays un-shortenable by construction
  - R5: Cross-tenant `GET`/`PUT /admin/retention-policy` (a caller's `tenant_id` does not match the policy's owner) -> 404 (never a leak, matches PROJECT.md invariant "cross-tenant access returns 404, never a leak")
</reject>
After:
<after>
  - A tenant admin (OWNER) can see and shorten their effective retention window (bounded by the operator ceiling) and the sweeper honors it going forward for every payload table plus the two existing swept tables.
  - A ZDR-enabled tenant: (a) has zero rows in every payload-bearing store inventoried at §0, self-healingly re-verified every sweep tick; (b) cannot create NEW payload rows in any of the five gated repositories (403, not silent data loss); (c) still gets byte-identical proxied completions with response/vector caching silently skipped; (d) still accrues exact `usage_records` billing rows; (e) audit_events for that tenant are completely unaffected (still governed by the operator floor).
  - `payload-capture-store`, once it grounds, has a read port to consume instead of re-deriving ZDR state itself.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **Scope of `window_days`**: this draft applies the tenant window to ALL 7 tables (2 existing + 5 newly-swept) rather than the narrower alternative (leave `usage_records`/`alert_events` untouched, ship `window_days` for the 5 new payload tables only, defer usage/alert override to a delta). Lowest confidence because it is the single biggest driver of BUILD size (touches `RetentionSweeper`'s frozen-contract DELETE templates for tables it already manages) and because a tenant might reasonably want SHORTER payload retention without touching their own usage-history visibility window. If wrong: BUILD should split into two batches (5-table payload sweep first, usage/alert-extension second) rather than treating this as one atomic change — cheap to correct, not cheap to have silently under-scoped.
  ⚠ **Purge-on-enable is a recurring sweeper responsibility, not a one-shot job**: chosen over a dedicated `tenant_zdr_purge_jobs` tracking table + one-shot `asyncio.create_task` (rejected: loses progress on a process restart mid-purge, matching no existing precedent in this codebase — RetentionSweeper's own audit-emit is the ONLY fire-and-forget task pattern here, and even that is per-batch, not per-multi-table-job). If wrong (Tin wants observable purge-job status, e.g. a dashboard progress bar): §3 needs a `zdr_purge_status` computed field (e.g. "rows remaining across inventory" derived by a COUNT query) rather than a stored job row — still additive, not a redesign.
  - [ ] `operator_ceiling_days` as a NEW single Settings knob (365 default) vs. deriving the ceiling from the MIN of the three existing per-table operator defaults — confirm the simpler single-knob approach at BUILD; both are additive, low cost to flip.
  - [ ] Whether dashboard-session-JWT-authenticated writers (if any) bypass the `ApiKeyRepository.get_by_id` LEFT JOIN convention and need a second identity-resolution path threaded with `zdr_enabled` — flagged as an Issue in §0, needs a BUILD-time trace of every non-`sk-`-key write path into the five gated repositories before claiming M5 complete.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Owner reads the default retention policy   # M1
  Given a tenant with no retention_policy row overrides ever set
  When the OWNER calls GET /admin/retention-policy
  Then the response shows window_days=null, effective_window_days per store = the operator defaults, zdr_enabled=false, zdr_enabled_at=null, operator_ceiling_days=365

Scenario: Owner shortens the retention window   # M2, M3, M4
  Given a tenant with operator_ceiling_days=365
  When the OWNER PUTs {"window_days": 30}
  Then the response echoes window_days=30 and effective_window_days=30 for every swept table except audit_events
  And the next RetentionSweeper.sweep_once() purges that tenant's rows older than 30 days from usage_records, alert_events, artifacts, conversation_messages, memories, batch_job_items, video_generation_jobs
  And audit_events for that tenant is untouched by this window (still only the operator floor applies)

Scenario: Window above the operator ceiling is rejected   # R1
  Given operator_ceiling_days=365
  When the OWNER PUTs {"window_days": 400}
  Then the response is 422 ERR_RETENTION_WINDOW_INVALID
  And the tenant's stored window_days is unchanged

Scenario: Window of zero or negative is rejected   # R1
  Given any tenant
  When the OWNER PUTs {"window_days": 0}
  Then the response is 422 ERR_RETENTION_WINDOW_INVALID
  And the tenant's stored window_days is unchanged

Scenario: Non-owner cannot change the policy   # R2
  Given a tenant member with role=ADMIN (holds most permissions but not SECURITY_CONFIG)
  When that ADMIN PUTs {"zdr_enabled": true}
  Then the response is 403 ERR_AUTH_FORBIDDEN
  And the tenant's zdr_enabled is unchanged
  And a MEMBER/VIEWER/BILLING_ADMIN/OPERATOR attempting the same PUT gets the same 403 (byte-identical failure shape across all non-owning roles)

Scenario: Enabling ZDR blocks a new artifact upload   # M5, R3
  Given a tenant with zdr_enabled=true
  When a caller POSTs a new artifact via the existing artifacts create endpoint
  Then the response is 403 ERR_ZDR_PAYLOAD_BLOCKED
  And no ArtifactRow is created (not even a metadata-only stub row)

Scenario: Enabling ZDR blocks a new conversation message, memory, batch item, and video job   # M5, R3
  Given a tenant with zdr_enabled=true
  When a caller attempts to append a conversation message, create a memory, submit a batch job item with a request_body, or create a video generation job with a prompt
  Then each attempt independently returns 403 ERR_ZDR_PAYLOAD_BLOCKED
  And no row is created in any of the four corresponding tables

Scenario: ZDR tenant's proxied chat completion still succeeds, byte-identically, minus caching   # M6, M8
  Given a tenant with zdr_enabled=true and cache_enabled=true (both flags set)
  When that tenant POSTs /v1/chat/completions
  Then the response body and status are identical to what a non-ZDR tenant with the same request would receive
  And no row is written to the exact response cache (resp-cache:{tenant_id}:*) or the vector cache (vec-cache:{tenant_id}:*)
  And a usage_records row IS written with accurate prompt_tokens/completion_tokens/cost_usd

Scenario: Enabling ZDR purges pre-existing payload rows   # M7
  Given a tenant with 50 existing artifacts (30 inline BYTEA, 20 storage_backend='s3'), 100 conversation messages, and 10 memories, currently zdr_enabled=false
  When the OWNER PUTs {"zdr_enabled": true}
  Then the PUT response returns immediately (200) with zdr_enabled=true and zdr_enabled_at set
  And within the next sweep cycle, RetentionSweeper deletes all 50 artifact rows, calling ObjectStore.delete() for each of the 20 s3-backed object_keys, and deletes all 100 conversation messages and 10 memories for that tenant
  And a bounded SCAN+DEL removes every resp-cache:{tenant_id}:* and vec-cache:{tenant_id}:* key
  And each table's purge (>0 rows deleted) emits a record_audit(action="data.purge") event scoped to that tenant

Scenario: ZDR purge survives a process restart mid-purge   # M7 (design-for-failure)
  Given a tenant just enabled ZDR with 10,000 artifacts pending purge, and the sweeper process crashes after deleting only 3,000 rows in the current batch loop
  When the gateway process restarts and the next sweep tick runs
  Then RetentionSweeper.sweep_once() resumes purging the remaining ~7,000 rows for that tenant (the purge condition is "tenant_id = :tid AND zdr_enabled=true", not a one-shot job — idempotent to re-run)
  And no error is raised to any caller; the purge is entirely background and fail-open per row batch (existing sweep_once() swallow-and-log semantics)

Scenario: Object-store outage during a ZDR purge does not crash the sweep   # M7 (design-for-failure)
  Given a ZDR-enabled tenant has an s3-backed artifact and the object store is unreachable (ObjectStoreUnavailableError)
  When the sweeper attempts to purge that artifact
  Then the DB row deletion for that artifact is deferred (not deleted while its bytes are still unreachable-but-possibly-present) OR the object-store delete is retried with the same bounded/backoff pattern CONVENTIONS.md requires for outbound IO
  And the sweep continues to the next table/tenant rather than aborting the whole cycle
  And the failure is logged, not silently swallowed as success

Scenario: Cross-tenant retention-policy access is a 404, never a leak   # R5
  Given tenant A's OWNER is authenticated
  When tenant A's OWNER attempts to GET or PUT a retention-policy endpoint scoped to tenant B (e.g. via a manipulated path/header)
  Then the response is 404
  And no information about tenant B's window_days/zdr_enabled is disclosed

Scenario: audit_events retention is never affected by any tenant action   # R4
  Given a tenant sets window_days=1 and later zdr_enabled=true
  When any number of sweep cycles run
  Then that tenant's audit_events rows are purged ONLY according to the operator-wide effective_audit_window (knob+floor) — never earlier, never by the ZDR purge pass
  And GET /admin/audit for that tenant continues to return its full, operator-floor-bounded history

Scenario: Disabling ZDR does not retroactively restore purged rows   # M7 (byte-identical / honest-degradation edge case)
  Given a tenant enabled ZDR, had its payload rows purged, then the OWNER PUTs {"zdr_enabled": false}
  When the OWNER re-checks GET /admin/retention-policy
  Then zdr_enabled=false and new payload writes are accepted again
  And no purged row is resurrected (purge was a real DELETE, not a soft-delete/hide)

Scenario: is_zdr() read port answers correctly for a not-yet-existing consumer   # M9
  Given the payload-capture-store table does not exist in this build
  When another module (or a future one) calls the exported is_zdr(tenant_id) port
  Then it returns the tenant's current zdr_enabled boolean, sourced from the same fresh per-request tenants.zdr_enabled read used by M5 — no separate/stale copy
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Least-sure flag surfaced at freeze: [spec] window_days scope = ALL 7 swept tables (incl. usage_records/alert_events which carry a FROZEN v1 sweeper contract) — the biggest build-size driver; build in 2 batches so the 5-payload-table subset is a clean fallback. Decided at freeze (Tin, 2026-07-10 batch): all 4 agent recommendations accepted (7 tables; self-healing purge, no job table; operator ceiling Settings knob default 365; proceed independently of the sibling milestone via the is_zdr read port).


```
GET /admin/retention-policy   (any authenticated role — get_identity)
  200 -> {
    window_days: int | null,             # null = inherits operator per-table defaults
    effective_window_days: {             # computed, one entry per swept table-class
      usage_records: int, alert_events: int, artifacts: int,
      conversations: int, memories: int, batch_job_items: int, video_generation_jobs: int
    },
    zdr_enabled: bool,
    zdr_enabled_at: string | null,       # ISO 8601, set on false->true transition
    operator_ceiling_days: int           # read-only, informational
  }
  404 -> { error: "ERR_TENANT_NOT_FOUND" }   # cross-tenant probe — never a leak

PUT /admin/retention-policy   body: { window_days?: int, zdr_enabled?: bool }
  requires: Depends(require_permission(Permission.SECURITY_CONFIG))   # OWNER only
  200 -> same shape as GET (post-update state)
  422 -> { error: "ERR_RETENTION_WINDOW_INVALID" }   # window_days <= 0 or > operator_ceiling_days or non-integer
  403 -> { error: "ERR_AUTH_FORBIDDEN" }             # non-owner (existing code, reused)
  404 -> { error: "ERR_TENANT_NOT_FOUND" }

Payload-write rejection (5 existing repository choke points — no new endpoint, an added guard):
  ArtifactRepository.create / ConversationRepository.create|append_message /
  MemoryRepository.create / BatchJobRepository.create (item carries request_body) /
  VideoJobRepository.create
  403 -> { error: "ERR_ZDR_PAYLOAD_BLOCKED" }   # tenants.zdr_enabled=true, read fresh per request

Schema (additive, single new migration parented on current head `511ad8a7b65e`):
  ALTER TABLE tenants ADD COLUMN retention_window_days INTEGER NULL;
  ALTER TABLE tenants ADD COLUMN zdr_enabled BOOLEAN NOT NULL DEFAULT false;
  ALTER TABLE tenants ADD COLUMN zdr_enabled_at TIMESTAMPTZ NULL;
  -- No new tables. EXPECTED_TABLES manifest unchanged (mirrors guardrails-core precedent,
  -- migrations/versions/d4e7f1a2b3c5_guardrails_core.py).

  New Settings field (apps/gateway/src/gateway/core/config.py, alongside the existing
  retention_* block at line ~571): retention_tenant_window_ceiling_days: int = Field(default=365)

  ApiKey / AuthzResult (apps/gateway/src/gateway/keys/domain/entities.py:33-43,94-105):
    + zdr_enabled: bool = False   # same LEFT JOIN comment convention as guardrail_configs

  ApiKeyRepository.get_by_id (apps/gateway/src/gateway/keys/infrastructure/repository.py:134-146):
    + TenantRow.zdr_enabled added to the existing 3-table LEFT JOIN SELECT list (zero extra DB reads)

  RetentionSweeper (apps/gateway/src/gateway/usage/application/retention_sweep.py) extension
  (additive to the FROZEN v1 contract, not a rewrite):
    + per-tenant-aware DELETE pass for the 5 new payload tables + optional per-tenant cutoff
      on the 2 existing tables (usage_records, alert_events), computed as
      COALESCE(tenants.retention_window_days, <operator per-table default>)
    + unconditional per-tenant purge pass (no cutoff) for every tenant with zdr_enabled=true,
      across the 5 payload tables + ObjectStore.delete() for s3-backed artifacts +
      bounded Redis SCAN+DEL over resp-cache:{tenant_id}:* and vec-cache:{tenant_id}:*
    + audit_events untouched by any of the above — effective_audit_window logic unchanged

  New read port (module TBD at BUILD, e.g. gateway/tenants/application/retention_policy.py):
    is_zdr(tenant_id: UUID) -> bool   # for payload-capture-store to consume once it grounds
```

Glossary deltas:
- `Zero-Data-Retention (ZDR)`: a tenant-level mode in which zero payload-bearing rows are ever created or retained across every inventoried gateway store (see §0); only token/usage metadata continues to be recorded for billing.
- `Payload-bearing store`: any table or cache namespace whose primary purpose is to hold request/response CONTENT (prompt text, completion text, binary file bytes, or a content-derived embedding), as distinct from a metadata-only store (token counts, cost, status, audit action tags).
- `Tenant retention window`: a per-tenant override (in days, bounded by `operator_ceiling_days`) of how long payload-bearing and usage/alert rows are kept before the sweeper purges them; independent of, and subordinate to, ZDR (ZDR always wins).

Status: FROZEN @ v1 — approved by Tin Dang
Reported: no — awaiting the human freeze decision (see FREEZE-QUESTIONS in the final report)

<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

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

Scope (may touch): `apps/gateway/src/gateway/usage/application/retention_sweep.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/tenants/infrastructure/orm.py` `apps/gateway/src/gateway/tenants/api/` `apps/gateway/src/gateway/keys/domain/entities.py` `apps/gateway/src/gateway/keys/infrastructure/repository.py` `apps/gateway/src/gateway/artifacts/infrastructure/repository.py` `apps/gateway/src/gateway/conversations/infrastructure/repository.py` `apps/gateway/src/gateway/memory/infrastructure/repository.py` `apps/gateway/src/gateway/batches/infrastructure/repository.py` `apps/gateway/src/gateway/video/infrastructure/repository.py` `apps/gateway/src/gateway/core/error_catalog.py` `apps/gateway/migrations/versions/` `apps/gateway/src/gateway/main.py`

Strategy (ordered batches):
1. Migration + Settings + TenantRow columns (`retention_window_days`, `zdr_enabled`, `zdr_enabled_at`, `retention_tenant_window_ceiling_days`) — additive, parented on `511ad8a7b65e`.
2. `GET/PUT /admin/retention-policy` router, cloned from `guardrail_router.py`'s partial-merge + permission-gate shape; new `ERR_RETENTION_WINDOW_INVALID`/`ERR_ZDR_PAYLOAD_BLOCKED` error-catalog entries.
3. Thread `zdr_enabled` through `ApiKey`/`AuthzResult`/`ApiKeyRepository.get_by_id` (mirror the existing `semantic_cache_enabled` addition byte-for-byte in shape).
4. Gate the 5 repository choke points (`ArtifactRepository.create`, `ConversationRepository.create`/`append_message`, `MemoryRepository.create`, `BatchJobRepository.create`, `VideoJobRepository.create`) behind a fresh `tenants.zdr_enabled` check — one small helper, not 5 copies of raw SQL.
5. Skip cache writes (#6/#7) under `identity.zdr_enabled` in `use_cases.py` (2 call sites, ~1672-1676 and ~1693-1695) — mirrors the existing `if self._response_cache is not None` guard shape.
6. Extend `RetentionSweeper.sweep_once()`: per-tenant cutoff pass for the 7 tables + unconditional ZDR purge pass (5 payload tables + ObjectStore.delete + Redis SCAN/DEL) — keep the existing 3 unconditional DELETEs untouched for tenants with no override (regression-sensitive; the frozen v1 sweep behavior must stay byte-identical when no tenant has set a policy).
7. Export the `is_zdr(tenant_id)` read port for `payload-capture-store` to consume later.

Persona (required): backend-architect
Spawn isolation (default): worktree — this task's Alembic migration + RetentionSweeper edits are regression-sensitive against the shared test Postgres; isolate.
Known-problem fixes:
- trap: extending `RetentionSweeper`'s frozen-v1 DELETE templates could silently change the no-override-tenant sweep behavior -> planned fix: keep the 3 original unconditional DELETEs as a separate, untouched code path; ONLY add new per-tenant-scoped queries alongside them, never rewrite the originals in place.
- trap: Redis `KEYS` on a tenant-scoped purge would block the whole Redis instance -> planned fix: `SCAN MATCH {prefix}:{tenant_id}:* COUNT <bounded>` cursor loop only, per the existing `RedisResponseCache`/`RedisVectorCache` swallow-and-log error style.
- trap: a bare `require_superadmin`-shaped foot-gun (missing `Depends()`) silently disables an auth gate (documented gotcha from the S1 sibling task) -> planned fix: `Depends(require_permission(Permission.SECURITY_CONFIG))` always wrapped in `Depends()`, verified by a dedicated non-owner-403 test per scenario "Non-owner cannot change the policy".
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): the ZDR write-path gate (M5) must be checked and enforced BEFORE the ZDR bulk-purge pass ever runs — fail-closed ordering: block new writes first (synchronous, immediate on PUT commit), purge existing rows second (async, self-healing) — never the reverse, which would leave a window where old rows are gone but new ones can still land.
Code lives in: existing module `infrastructure`/`application`/`api` layers per CONVENTIONS.md (no new top-level module — this task extends `tenants`, `usage`, `keys`, `artifacts`, `conversations`, `memory`, `batches`, `video`).
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
- [ ] A ZDR tenant's artifact/memory/conversation-message/batch-item/video-job POST returns 403 ERR_ZDR_PAYLOAD_BLOCKED with zero rows created — confirmed by a live DB row-count check pre/post the call in the test.
- [ ] A ZDR tenant's chat completion returns byte-identical to a non-ZDR tenant's same request, with zero resp-cache/vec-cache Redis keys written — confirmed by inspecting the test fake-Redis's key set after the call.
- [ ] Enabling ZDR on a tenant with pre-existing payload rows results in zero rows across all 5 payload tables after a sweep cycle, including s3-backed artifacts calling ObjectStore.delete — confirmed by row-count + fake-object-store call assertions.
- [ ] A tenant's `window_days` override does not affect audit_events retention — confirmed by an audit_events row surviving a sweep past the tenant's window but before the operator floor.
- [ ] The original no-override-tenant sweep behavior (3 existing DELETEs) is unchanged — confirmed by re-running the existing `test_retention_sweep.py` suite green, untouched.

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

Watch (reuse scenarios as monitors): rate of `ERR_ZDR_PAYLOAD_BLOCKED` per tenant (a ZDR tenant repeatedly hitting artifacts/memory endpoints may indicate a product gap, not a bug) · sweep-cycle duration once tenant-scoped queries are added (regression risk on the operator-wide sweep's own latency) · count of s3 `ObjectStoreUnavailableError` retries during ZDR purges (object-store health signal).

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).
- [SPEC · open] `payload-capture-store` (sibling milestone `logs-explorer-guardrails-v2`) must consume this task's `is_zdr(tenant_id)` port when it grounds — no code exists yet to wire; re-enters at that task's own Specify (evidence: §0 GROUND "Ruled OUT" note, milestone shared-decision cross-reference).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
- [DDD · open] a milestone's "the sibling task freezes that hook here" cross-reference can point at a task that is itself still ungrounded — a design agent must verify the CURRENT state of a cited dependency rather than trusting the milestone prose, and record a port/contract the other side can consume later instead of assuming the hook already exists (evidence: `payload-capture-store/TASK.md` read in full — still the blank template).
