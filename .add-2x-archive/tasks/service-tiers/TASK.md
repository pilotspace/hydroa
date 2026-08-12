# TASK: Priority/standard service tiers — capacity preference, overflow, tier markup

slug: service-tiers · created: 2026-07-12 · stage: production
milestone: residency-service-tiers
autonomy: auto
phase: done
sensitivity: data

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/proxy/api/concurrency_guard.py:GlobalBackPressureMiddleware` — the shipped global back-pressure guard, **Status: FROZEN @ v1** (concurrency-load-guard TASK.md, approved 2026-06-23). A single per-worker `asyncio.BoundedSemaphore(max_concurrent)`, registered in `main.py` OUTERMOST-but-one (`app.add_middleware` order: `BodySizeLimitMiddleware` → `GlobalBackPressureMiddleware` → `RequestIdMiddleware` → routing → auth). It runs on the raw ASGI `scope` BEFORE routing, BEFORE `RequestIdMiddleware`, and — critically — **BEFORE any auth**: this codebase has zero ASGI-level `AuthenticationMiddleware`, so `GlobalBackPressureMiddleware` has NO access to `tenant_id`/`key_id`/tier at the point it admits or sheds a request (confirmed: only 3 `app.add_middleware` calls exist in the whole file). MUST NOT be edited — it is frozen and this task is additive only.
- `apps/gateway/src/gateway/keys/api/deps.py:get_raw_api_key` / `apps/gateway/src/gateway/keys/application/use_cases.py:AuthzUseCase.execute` (~line 274-326) — where identity actually resolves: a FastAPI `Depends()` used INSIDE the route handler body, one call deep from `CompletionUseCase._authenticate`/`NonChatGovernance.authorize` Step 1 — i.e., strictly AFTER the full ASGI middleware stack (including the frozen guard) has already run. `AuthzUseCase.execute` builds `AuthzResult` from `self._repo.get_by_id(key_id)` (a LEFT JOIN `tenants` row, zero extra DB reads) — the exact site a new `tier` resolution slots into, mirroring the EXISTING `cache_enabled = api_keys.cache_enabled OR tenants.cache_enabled` / `guardrail_policy_source: Literal["key","tenant","none"]` override-resolution precedent on the same dataclass.
- `apps/gateway/src/gateway/keys/domain/entities.py:AuthzResult` (frozen dataclass, additive-fields-only convention, `apps/gateway/src/gateway/keys/domain/entities.py:91-151`) — new field `tier: Literal["priority","standard"] = "standard"` + `tier_source: Literal["key","tenant"] = "tenant"` (mirrors `policy_source`).
- `apps/gateway/src/gateway/keys/infrastructure/orm.py:ApiKeyRow` (~17-108, no tier field today) and `apps/gateway/src/gateway/tenants/infrastructure/orm.py:TenantRow` (~91-186, `markup_pct: Mapped[Decimal] = mapped_column(Numeric(7,4), nullable=False, server_default="20.0")` is the column-convention template) — natural additive homes for `api_keys.tier` (nullable override) and `tenants.default_tier` (NOT NULL, server_default).
- `apps/gateway/src/gateway/keys/api/schemas.py:CreateKeyRequest` (line 22, `cache_enabled: bool = False` is the precedent) and `:PatchKeyRequest` (line 89, three-state PATCH convention: absent=no-change / null=clear-to-tenant-default / value=set, same as `team_id`/`cache_enabled`) — where tier is set at key creation and changed mid-flight. `apps/gateway/src/gateway/keys/api/router.py:patch_key` (275-395, `@admin_router.patch("/{key_id}")`, gated `require_owner_or_admin`) is the exact route the "tier change mid-flight" scenario extends.
- `apps/gateway/src/gateway/proxy/application/use_cases.py:CompletionUseCase._enforce_governance` (1383-1474) and `apps/gateway/src/gateway/proxy/application/governance.py:NonChatGovernance.authorize` (104-218) — the TWO governance choke points ("dual-copy governance, never staggered" per the credits-ledger comment at governance.py:173) where `AuthzResult` (with the now-resolved `.tier`) is available post-auth, pre-upstream. Both already run an admission-time HOLD/release pattern for money (`credits/domain/ports.py:CreditGuard.check_and_hold`/`.release`, `apps/gateway/src/gateway/credits/domain/ports.py:15-73`) inserted immediately after the budget ladder, wrapped so a LATER RPM/TPM rejection reverses the hold (`except Exception: await self._credit_guard.release(...); raise`, use_cases.py ~1461-1474, governance.py ~175-218). This is the exact shape a tier-capacity admission hold composes with — NOT the frozen ASGI guard, which cannot see identity.
- `apps/gateway/src/gateway/proxy/application/use_cases.py:_credit_hold_ctx` / `_settle_or_release_hold` / `_dispatch_record` (292-359) — a `contextvars.ContextVar` published right after the credit hold succeeds, consumed by a `asyncio.Task.add_done_callback` on the SAME fire-and-forget usage-recording Task that `_dispatch_record` schedules once a stream is FULLY DRAINED — this is the established mechanism in this codebase for "hold a resource across the whole (possibly-streaming) request without threading it through ~25 call sites," and is the pattern a tier-capacity hold's release must reuse (NOT the ASGI-middleware "hold across `await self.app(...)`" pattern the frozen guard uses, which is structurally unavailable here).
- `apps/gateway/src/gateway/proxy/infrastructure/redis_load_gate.py:RedisDeploymentLoadGate` (30-133) — the closest EXISTING precedent for "in-flight counting wrapped around a request/stream at the APPLICATION layer, not ASGI" (`acquire`/`release`/`in_flight`, Redis INCR/DECR, fail-open on error) — per-DEPLOYMENT routing telemetry, not a capacity CAP (no admission decision, never rejects). Named but not the load-bearing precedent — see the atomic-cap precedents below.
- **[amended at freeze-review]** `apps/gateway/src/gateway/rate_limits/infrastructure/redis_lua_limiter.py:RedisLuaRateLimiter` (191-305) — the load-bearing precedent for cross-worker ATOMIC admission: `check_rpm`/`check_tpm` run ONE Lua script per call (`register_script`, cached SHA, no manual EVALSHA management) implementing a sliding-window ZSET (`ratelimit:rpm:{key_id}` / `ratelimit:tpm:{key_id}`, member=random uuid, score=now_ms, pruned via a score-range op INSIDE the same script before counting) — atomic count-then-admit in one round trip, exactly the "two workers race the last slot, exactly one wins" property tier admission now needs. `_TTL_S` bounds every key's lifetime (self-expiring, no separate sweep). **Fail-open is explicit and total**: "any Exception from Redis/Lua → log warning + admit (never propagate)" (docstring, line 197) — `except Exception: _log.warning(...); return` (no raise) on every method, including the fire-and-forget `record_tpm`.
- **[amended at freeze-review]** `apps/gateway/src/gateway/rate_limits/infrastructure/redis_token_bucket.py:RedisTokenBucket` (111-248, "Contract FROZEN @ bandwidth-token-bucket TASK.md §3 v1") — a SECOND atomic-Lua precedent, closer in shape to tier admission than rate limiting: `_BUCKET_LUA` does refill-then-conditional-consume in ONE script (`bandwidth:bucket:{key_id}` / `bandwidth:bucket_ts:{key_id}`, `EXPIRE` on every write — TTL = `ceil(burst/rate)+60s`), returns `{granted, level, retry_after}`. Same fail-open idiom verbatim ("Redis error (fail-open)" → admit). `reconcile()` demonstrates a signed-delta correction pattern (not needed here — tier holds are binary, not a metered quantity).
- **[amended at freeze-review]** `apps/gateway/src/gateway/credits/application/recovery_sweep.py:CreditHoldRecoverySweeper` (54-135) — the project's precedent for "a crashed/never-finalized request must not permanently strand a held resource": a periodic `sweep_once()` releases any hold older than `hold_timeout_s`, batch-limited, swallows all errors, default-OFF via `should_start_credit_recovery_sweep(interval_seconds) -> bool` (zero/negative = disabled). This is a Postgres-row sweep because Postgres has no native TTL; **evaluated and NOT reused as a literal sweep process** — Redis's native `EXPIRE`/score-pruning gives the SAME crashed-worker-can't-strand-a-slot guarantee passively (§1), so the tier-capacity design achieves the credit sweep's INTENT with a simpler, TTL-native mechanism, not a copy of its machinery. `postgres_guard.py:152-156`'s documented race (a late completion vs. the sweep double-finalizing the SAME hold — fixed by lock-then-decide ordering) is the concrete reason release must be idempotent-by-construction here too (§1 M9/M10).
- **[amended at freeze-review]** `apps/gateway/src/gateway/tenants/domain/authz.py:require_superadmin` (329) and its usage across every `platform_*_router.py` (`platform_tenant_config_router.py`, `platform_plans_router.py`, `platform_tenants_router.py` — ALWAYS `identity: Annotated[Identity, Depends(require_superadmin)]`, never a bare call — a bare-call-without-`Depends()` foot-gun this project has hit before, memory: "a silently-absent auth gate, 422 for all"). `platform_tenants_router.py`'s bulk-list route (`GET /admin/platform/tenants`, "gated by require_superadmin — a role-only check, deliberately NOT authorize_tenant_scope", no target tenant_id) is the exact precedent for a GLOBAL, non-tenant-scoped platform capacity knob — the reservation-split/cluster-cap route has no target tenant either.
- `apps/gateway/src/gateway/usage/application/recorder.py:RecordingUsageRecorder` (65-674) — `record`/`record_with_outcome`/`_record_internal` stamp discriminator fields (`cost_basis`, `usage_source`, ~line 492/497) into an `event_fields: dict[str,str]` XADD'd to Redis; `cost_basis`/`usage_source` (ORM `Text, nullable=False, server_default=...`) are the exact template for a new `tier_served` column.
- `apps/gateway/src/gateway/proxy/domain/ports.py:UsageRecordExtras` (`TypedDict, total=False`, 33-85) + `recorder.py:RecordingUsageRecorder.supported_extras` (81-96, a `frozenset[str]` class attribute, NOT derived from the TypedDict) — the typed-extras seam (memory's summary location was imprecise; ground truth corrects it: the TypedDict lives in `proxy/domain/ports.py`, not `usage/`). Adding `tier_served` needs: (a) new TypedDict key, (b) added to `supported_extras`, (c) new kwarg threaded through `record`/`record_with_outcome`/`_record_internal`, (d) stamped into `event_fields`. Checked/filtered at `apps/gateway/src/gateway/proxy/application/use_cases.py:363`.
- `apps/gateway/src/gateway/usage/application/flusher.py:insert_usage_row` (58-203) — a THIRD site that must change: it hand-parses the Redis-stream fields dict and builds the raw `INSERT INTO usage_records (...)` text-SQL (columns ~155-169, params ~178-200); an old-event-safe fallback (`_event_field(fields, "tier_served") or "standard"`) is required, mirroring `cost_basis`'s own fallback idiom.
- `apps/gateway/src/gateway/usage/infrastructure/orm.py:UsageRecordRow` (36-126, `__tablename__ = "usage_records"`) — `cost_basis`/`usage_source` columns are the template for `tier_served: Mapped[str] = mapped_column(Text, nullable=False, server_default="standard")`.
- `apps/gateway/src/gateway/usage/application/rate_card_resolver.py:resolve_markup_pct` (FROZEN v1, untouched) and the NEW `resolve_region_multiplier` + the RESERVED `resolve_tier_multiplier(session, tenant_id, model_id, tier) -> Decimal` — region-pricing TASK.md §3 (**Status: FROZEN @ v1**, approved by Tin) reserves this EXACT 4-arg signature for this task to fill; `model_id` is accepted-but-UNUSED in the body (tier markup is not model-specific — a stated scope-cut, not an oversight) — implemented against the frozen signature verbatim, never renegotiated.
- `apps/gateway/src/gateway/usage/application/recorder.py::RecordingUsageRecorder.record` (~line 282 resolves `markup_pct`, ~line 415 applies `region_multiplier`, ~line 430 divides it back out for disconnect-provider-cost) · `cost_recovery.py::OpenRouterCostRecovery` (~161-223) · `catalog/infrastructure/repository.py::CatalogRepository.list_active_models_with_markup` (~87-186, single bulk `multiplier` scalar at ~153) — the THREE multiplication sites region-pricing's M9 already named as the tier-multiplier's future insertion points; this task fills them.
- `apps/gateway/src/gateway/tenants/domain/authz.py:Permission` (StrEnum, 55-80) and `apps/gateway/src/gateway/tenants/api/rate_card_router.py` / `region_pricing_router.py` — the OWNER-only-permission + dedicated-router-file precedent this task's admin surface mirrors (`Permission.KEYS_MANAGE` already gates key create/patch — reused here rather than minting a redundant permission for the key-level `tier` field; a NEW `service-tiers` admin router mirrors `region_pricing_router.py`'s file shape for the tenant-level default-tier / priority-markup routes).

Context (working folder): sibling task `region-pricing` is **Status: FROZEN @ v1** (its `resolve_tier_multiplier` reservation is the load-bearing citation for this task) and `region-catalog-dimension` is **Status: FROZEN @ v1**; `concurrency-load-guard` is **Status: FROZEN @ v1** and shipped (2026-06-23, pre-dates this milestone entirely — NOT part of residency-service-tiers). `.add/milestones/residency-service-tiers/MILESTONE.md` binding rule #4 ("tier is a capacity PREFERENCE... priority admitted ahead... standard never starved... tier actually SERVED is billed") is DIRECTION, not ground truth about HOW — the milestone text says "capacity preference in the concurrency guard" (singular), but the real guard cannot see identity and is frozen; this task corrects that framing (§1 Framings weighed) rather than silently editing a frozen contract.

Honors (patterns / conventions): CONVENTIONS.md additive-migration/rollback discipline (same as region-pricing/region-catalog-dimension); the `check_and_hold`/`release` admission-hold shape (`credits/domain/ports.py:CreditGuard`) — "reserve-then-release, best-effort, never raises on infra failure" (ports.py:15-31); the `_credit_hold_ctx`/`_settle_or_release_hold`/`_dispatch_record` contextvar-plus-task-done-callback idiom for spanning a hold across a possibly-streaming request WITHOUT threading a new parameter through ~25 call sites; the "dual-copy governance, never staggered" discipline (both `CompletionUseCase._enforce_governance` AND `NonChatGovernance.authorize` get the SAME insertion — the credits-ledger build originally MISSED `governance.py` and had to heal it in, "HEAL (finding 2)" comment at governance.py:178 — this task wires both from the start to avoid repeating that trap); typed-extras declared-capability filtering (never `inspect.signature` dispatch); `Numeric(7,4)` percentage-additive columns for anything that composes like `markup_pct` (vs `Numeric(6,4)` raw-multiplier columns like region); PUT-idempotent-upsert / DELETE-always-204 / OWNER-only-via-Permission admin-router precedent (`rate_card_router.py`, `region_pricing_router.py`). **[amended]** `RedisLuaRateLimiter`/`RedisTokenBucket`'s ONE-script-per-decision idiom (`register_script` once at construction, no manual EVALSHA, atomic count-then-admit) and TOTAL fail-open on any Redis/Lua exception (log + admit, never propagate, never raise); `require_superadmin` ALWAYS via `Annotated[Identity, Depends(require_superadmin)]`, never a bare call (this project's own documented foot-gun).

Anchors the contract cites: `keys/domain/entities.py:AuthzResult.tier` (NEW) · `keys/application/use_cases.py:AuthzUseCase.execute` (extension) · `keys/infrastructure/orm.py:ApiKeyRow.tier` (NEW) · `tenants/infrastructure/orm.py:TenantRow.default_tier` (NEW) · `keys/api/schemas.py:CreateKeyRequest.tier` / `PatchKeyRequest.tier` (NEW) · `proxy/domain/tier_capacity.py:TierCapacityGuard` (NEW Protocol) · `proxy/infrastructure/tier_capacity_guard.py:RedisTierCapacityGuard` / `PassthroughTierCapacityGuard` (NEW — **amended**: Redis-backed, not in-process) · `proxy/application/use_cases.py:CompletionUseCase._enforce_governance` (extension) · `proxy/application/governance.py:NonChatGovernance.authorize` (extension) · `usage/application/rate_card_resolver.py:resolve_tier_multiplier` (fills the RESERVED frozen signature) · `usage/application/recorder.py::RecordingUsageRecorder.record` (extension, 4th multiplication site correction) · `usage/application/cost_recovery.py::OpenRouterCostRecovery` (extension) · `catalog/infrastructure/repository.py::CatalogRepository.list_active_models_with_markup` (extension, new `tier` param) · `proxy/domain/ports.py:UsageRecordExtras.tier_served` / `.tier_capacity_degraded` (NEW) · `usage/infrastructure/orm.py:UsageRecordRow.tier_served` / `.tier_capacity_degraded` (NEW) · `usage/application/flusher.py:insert_usage_row` (extension) · `tenants/api/service_tier_router.py` (NEW, tenant-owner) · `tenants/api/platform_service_tier_router.py` (NEW, superadmin — **amended**).

Issues/Risks (→ feed §1):
1. **The frozen guard cannot see tier — architecturally, not incidentally.** `GlobalBackPressureMiddleware` runs before auth exists in this codebase (no ASGI `AuthenticationMiddleware` anywhere — confirmed by exhaustive search). Any design that tries to make the ASGI guard itself tier-aware would require either (a) editing a FROZEN contract (disallowed) or (b) duplicating auth inside the middleware (a second, drift-prone identity-resolution path, and a new external dependency on the hot ASGI path — which the guard's own frozen contract explicitly rejected for the base cap: "no new external dependency on the hot path"). This is the central finding driving the whole design: tier admission preference must be a SEPARATE, additive mechanism at the governance choke point, not an edit to the guard. See §1 Framings weighed.
2. **[SUPERSEDED at freeze-review, 2026-07-12, Tin]** Per-worker in-process accounting was this draft's original choice, explicitly REJECTED by Tin at freeze review: a monetized SLA must be honest fleet-wide, not per-worker. The design now uses `RedisLuaRateLimiter`/`RedisTokenBucket`'s atomic-Lua idiom (§0 Touches) for cross-worker-correct admission — the SAME class of cross-cutting Redis dependency the base guard itself declined to add for its OWN coarser cap, but justified here because the promise being sold (differentiated SLA) is a cluster-wide fact, not a per-process one; a customer's request landing on a different worker must not silently change whether "priority" means anything.
3. **Non-blocking, single-round-trip admission (mirrors `RedisLuaRateLimiter`'s own "no waiter queue" posture) means "priority admitted ahead of standard" is still not a literal queue-reordering guarantee** — there is no queue, cross-worker or not. The deterministic mechanism is RESERVED CAPACITY (a ZSET-pool only one tier may draw from), which is why the milestone's own language is "preference... not a guarantee" (MILESTONE.md shared decision #4) — the mechanism is built to match that literal framing, not to over-promise a hard ordering guarantee.
4. **`tier_served` can legitimately differ from the tier selected on the key** — MILESTONE.md rule #4's own wording ("tier actually served... is what gets billed") is a stronger claim than "served == requested-or-shed"; if served always equaled requested-or-503, the rule would be a trivial echo of the key's own `tier` field, not worth stating as its own binding rule. This task's mechanism (§1 M-items) makes a genuine downgrade path real: a priority request that exhausts BOTH its own reserved capacity AND the shared pool may still be admitted through standard's reserved floor as a last resort, billed and recorded as `tier_served="standard"` — this is the actual "priority→standard overflow" the milestone names, not a synonym for "priority always bills priority." **DECIDED at freeze-review (Tin): confirmed, do not reopen.**
5. **Cost-recovery must re-resolve `tier_multiplier` FRESH against the CURRENT rate-card state** (not replay a stored multiplier value), mirroring region-pricing's own accepted precedent for `resolve_region_multiplier` in `cost_recovery.py` — but keyed by the ORIGINAL request's `tier_served` (read from the `usage_records` row being recovered), never re-derived from current admission state (admission is a point-in-time capacity fact; billing rate is a point-in-time rate-card fact — conflating them would let a stale recovery silently re-run admission logic that no longer applies).
6. **[NEW at freeze-review]** A Redis outage must not hard-fail the request path (design-for-failure, PROJECT.md invariant) — but unlike RPM/TPM's fail-open (where "admit" is simply the ONLY sane choice, since a rate limiter with no state is not meaningfully wrong to admit), a tier gate that fails open loses its ability to tell whether a paying priority customer's promise was actually honored. Silently billing the priority markup for a request that received NO differentiated admission during an outage would be dishonest. This is why `tier_served` and billing must degrade together (§1 new Must), not just admission — see the new `tier_capacity_degraded` discriminator.
7. **[NEW at freeze-review]** Redis's native `EXPIRE`/sorted-set score-pruning gives passive held-slot leak protection for a crashed worker WITHOUT a separate sweep process (unlike credits' Postgres-backed `CreditHoldRecoverySweeper`, which needs one because Postgres has no TTL primitive) — but this only holds if every hold's max lifetime is bounded by a constant that safely exceeds any real request's duration. Set too short, a legitimately slow priority stream gets silently evicted from its own pool mid-flight (not rejected — just no longer counted, which is harmless for the CAP but would let another request take "its" slot, a soft over-admission, not a correctness break, since the semaphore's true state is always `ZCARD`, never trusted client-side). Set too long, a crashed worker's slot stays falsely occupied for up to that long. §1 flags the constant's value as the new lowest-confidence item.

Related intent: MILESTONE.md shared decision #4 ("tier is a capacity preference, not a guarantee") and the DECIDED seed "+25% priority markup, tenant-overridable" (MILESTONE.md, 2026-07-12 intake); GLOSSARY.md `Markup`/`Cost` (extended by region-pricing's "region multiplier" delta, extended again here by "tier markup"); region-pricing TASK.md §1 assumption #3 (the deliberate multiplicative-region / additive-percentage-tier asymmetry) — this task's storage follows the SAME percentage-additive convention as `markup_pct`, not region's raw-multiplier convention.
Ground SHA: 853afa8

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Priority/standard service tiers — per-tenant/key capacity preference, overflow, and tier-differentiated markup, composed with (not edited into) the frozen global back-pressure guard.

Framings weighed:
  (chosen — **amended at freeze-review, 2026-07-12, Tin**) A NEW, additive `TierCapacityGuard` admission-hold — mirroring `CreditGuard`'s exact `check_and_hold`/`release` shape and insertion point (immediately after the credit hold, inside `_enforce_governance`/`NonChatGovernance.authorize`, wrapped so a later RPM/TPM/credit rejection reverses it; hold spans the stream via the `_credit_hold_ctx`-style contextvar/done-callback seam) — that Protocol/insertion shape is UNCHANGED from the original draft. What changed: the CONCRETE implementation is now **Redis-backed atomic cross-worker accounting**, mirroring `RedisLuaRateLimiter`/`RedisTokenBucket`'s ONE-Lua-script-per-decision idiom (§0 Touches) — three sliding-window-style Redis ZSETs (`priority_floor`, `standard_floor`, `shared_pool`), a hold = one ZSET member (the request_id) with score=now_ms, admission = one atomic Lua script per pool tried (prune-then-ZCARD-then-conditional-ZADD), release = `ZREM` (idempotent by construction — removing an absent member is a harmless no-op, so a hold that already TTL-expired or was already released cannot be double-released or double-freed, since occupancy is always `ZCARD`-derived, never a separately-mutated counter). Explicitly REJECTED: the original per-worker `asyncio.BoundedSemaphore` design (Tin, freeze review: "the monetized SLA must be honest fleet-wide" — a per-worker approximation was judged too weak a promise for a paid differentiator, unlike the base guard's own coarser per-worker cap, which only needs to protect ANY worker from collapse, not deliver a cluster-wide fairness promise).
  · Edit `GlobalBackPressureMiddleware` itself to read tier from the request — REJECTED (unchanged from original draft): the middleware runs before auth exists anywhere in this codebase (§0 issue #1), and the guard is a FROZEN v1 contract (boundary: "MUST NOT edit a frozen contract"); making it tier-aware would require either duplicating auth on the ASGI hot path (a second identity-resolution source of drift) or a new external dependency on that path, which the guard's own frozen contract explicitly rejected.
  · A real priority queue (waiters ordered, priority dequeued first) — REJECTED: the frozen base guard's admission semantics are explicitly non-blocking/shed-now ("chosen so saturation sheds immediately rather than building an unbounded waiter queue" — concurrency-load-guard TASK.md §3), and `RedisLuaRateLimiter`'s own chosen idiom is likewise a single atomic round trip, never a queue. A queue at the tier layer would contradict both postures and reintroduce unbounded-waiter memory risk.
  · A Postgres-backed hold ledger, mirroring `CreditHoldRecoverySweeper`'s row-lock + periodic-sweep shape exactly — REJECTED: Postgres has no native key TTL, so a literal port would need a NEW periodic sweep PROCESS (background task, interval knob, batch limit) just to reclaim a crashed worker's held slot — real, working machinery, but strictly more moving parts than Redis's native `EXPIRE`/score-pruning, which reclaims passively on the NEXT script invocation that touches the same key, no separate process required. Credits needed Postgres because a balance is a durable financial fact; a tier-capacity hold is not — it is disposable admission-control state, a better fit for Redis's weaker but sufficient durability.

Must:
<must>
  - M1: `api_keys.tier: Text | NULL` (CHECK IN ('priority','standard') OR NULL) is an OPTIONAL per-key override; `tenants.default_tier: Text NOT NULL DEFAULT 'standard'` (CHECK IN ('priority','standard')) is the tenant-wide fallback. `AuthzUseCase.execute` resolves `AuthzResult.tier = api_keys.tier if not NULL else tenants.default_tier` via the EXISTING LEFT JOIN in `ApiKeyRepository.get_by_id` (zero extra DB reads), stamping `AuthzResult.tier_source: Literal["key","tenant"]` (mirrors `policy_source`).
  - M2: `CreateKeyRequest.tier: Literal["priority","standard"] | None = None` (omit = inherit tenant default at creation time, resolved fresh on every request via M1 — NOT frozen at create time) and `PatchKeyRequest.tier: str | None = None` follow the EXISTING three-state PATCH convention (absent=no-change; present+null=clear the key-level override, reverting to tenant default; present+value=set) — the "tier change mid-flight" capability, wired through `patch_key`/`UpdateKeyUseCase.execute` exactly like `team_id`/`cache_enabled`.
  - M3: A NEW `TierCapacityGuard` Protocol (`check_and_hold(tenant_id, tier, request_id) -> tier_served`; `release(tenant_id, request_id) -> None`) is called from BOTH `CompletionUseCase._enforce_governance` AND `NonChatGovernance.authorize` (dual-copy, §0 Honors), inserted immediately BEFORE the credit hold (capacity is a gateway-wide scarce-resource concern, closer to the base back-pressure guard's job than to per-tenant affordability); a later credit-hold OR RPM/TPM rejection releases the tier hold too (extend the existing `except Exception: ... raise` blocks in both call sites).
  - M4: Default wiring is `PassthroughTierCapacityGuard` (`check_and_hold` always returns the REQUESTED tier unchanged, `release` is a no-op) — mirrors `PassthroughCreditGuard` exactly. Byte-identical to today until an operator wires `RedisTierCapacityGuard` AND sets `tier_capacity_cluster_cap` > 0 (M6) — matches this codebase's pervasive "new gate defaults to a no-op" convention.
  - M5: **[amended]** `RedisTierCapacityGuard` partitions a NEW, EXPLICIT `settings.tier_capacity_cluster_cap` (C — the fleet-wide total, deliberately NOT derived from or coupled to the base guard's per-worker `max_concurrent_requests`, which has no notion of worker count today; an operator states the real cluster capacity directly) into THREE Redis sorted-set pools keyed `tier:pool:priority_floor` / `tier:pool:standard_floor` / `tier:pool:shared`: `priority_floor` (cap = round(C × `tier_priority_reserved_pct`), priority-EXCLUSIVE), `standard_floor` (cap = round(C × `tier_standard_reserved_pct`), standard-EXCLUSIVE — the starvation bound), `shared_pool` (cap = C − both floors, open to both). Each pool is a ZSET (member = hex `request_id`, score = now_ms); a hold = one ZADD, admission is atomic per pool: ONE Lua script does `ZREMRANGEBYSCORE` (prune members older than `tier_capacity_hold_ttl_s` — the passive leak-protection window, §0 issue #7) THEN `ZCARD` THEN conditional `ZADD` in one round trip (mirrors `RedisLuaRateLimiter`'s prune-then-count-then-admit shape exactly). When C = 0 (default — disabled), `RedisTierCapacityGuard` is a pass-through (no scarcity to prefer against, no Redis touched).
  - M6: **[amended]** NEW `Settings` knobs, seeded with REAL defaults (Tin, freeze review — stronger guarantee than the originally-recommended 10/10/80 split): `tier_capacity_cluster_cap: int = Field(default=0)` (env `GATEWAY_TIER_CAPACITY_CLUSTER_CAP`; 0 = disabled — this number is inherently deployment-specific and cannot have a universal non-zero default) · `tier_priority_reserved_pct: float = Field(default=0.20)` (env `GATEWAY_TIER_PRIORITY_RESERVED_PCT`) · `tier_standard_reserved_pct: float = Field(default=0.20)` (env `GATEWAY_TIER_STANDARD_RESERVED_PCT`) — 20% priority floor / 20% standard floor / 60% shared, inert until `tier_capacity_cluster_cap` > 0. A sum of the two percentages > 1.0, or either negative, is coerced to 0.20/0.20 + a startup WARN (mirrors the base guard's negative-knob-coerced-to-0 precedent) — boot never crashes on a bad knob. Superadmin-adjustable at runtime (M13) without a restart (Redis-backed — unlike the base guard's own restart-to-apply per-worker semaphore sizing).
  - M7: Admission order — PRIORITY: try `priority_floor` → else `shared_pool` → else `standard_floor` (the true overflow-into-standard's-own-lane path, LAST resort before shedding) → else shed. STANDARD: try `standard_floor` → else `shared_pool` → else shed (standard NEVER draws from `priority_floor`). `tier_served` = "priority" when admitted via `priority_floor` or `shared_pool`; = "standard" when a priority request is admitted via `standard_floor` (the overflow case) or for any standard-tier admission. Each pool attempt is its OWN atomic Lua round trip (M5) — two workers racing the LAST slot in any single pool: exactly one script's conditional `ZADD` observes `ZCARD < cap` and succeeds; the other observes the post-ZADD count and is rejected for that pool (falls through to the next pool in its own order, or sheds).
  - M8: SHED ON EXHAUSTION: `check_and_hold` raises `ProblemError(503, "ERR_TIER_CAPACITY_EXHAUSTED")` + `Retry-After` header (mirrors the base guard's `ERR_OVERLOADED` shape exactly, distinct code so callers can tell "gateway globally overloaded" apart from "this tier's reserved capacity is exhausted even though the base guard admitted you") when all applicable pools are exhausted for that tier.
  - M8a: **[NEW at freeze-review]** DEGRADED-REDIS BEHAVIOR (design-for-failure, PROJECT.md IO invariant): any Exception from a `RedisTierCapacityGuard` Lua call — mirrors `RedisLuaRateLimiter`/`RedisTokenBucket`'s idiom verbatim: log a structured WARNING (tenant_id/request_id only, never secrets) + FAIL OPEN to untiered admission (the request proceeds; the base `GlobalBackPressureMiddleware` is still the process's own overload protection and is entirely unaffected by a Redis outage, since it never touches Redis). `check_and_hold` returns `tier_served="standard"` UNCONDITIONALLY during a degraded call, regardless of the requested tier, AND the caller stamps a NEW `tier_capacity_degraded: bool = True` discriminator — honest because no differentiated admission was actually possible; a priority customer is served (never hard-failed by the tier layer) but is billed at the standard rate for that specific request, with `tier_capacity_degraded=true` as the audit trail explaining why (never silently billed the priority markup for a promise the outage made it impossible to keep). `release()` degrades the same way: a Redis exception on release is logged + swallowed (never raises, never blocks the response) — the hold's own TTL (M5) reclaims it passively if the release truly never lands.
  - M9: The hold spans the WHOLE (possibly-streaming) request via a NEW `_tier_hold_ctx` contextvar, set alongside `_credit_hold_ctx` right after M3's hold succeeds, released by extending the EXISTING `_settle_or_release_hold` task-done-callback (fires once `_dispatch_record`'s Task completes, i.e., after full stream drain) — NOT the ASGI "hold across `await self.app(...)`" pattern, which is unavailable at this layer.
  - M10: `tier_served` (the value M7/M8a resolved, NEVER the requested tier) is stamped onto the usage record via the typed-extras seam: NEW `UsageRecordExtras.tier_served: Literal["priority","standard"]` + `UsageRecordExtras.tier_capacity_degraded: bool` keys, added to `RecordingUsageRecorder.supported_extras`, threaded through `record`/`record_with_outcome`/`_record_internal` into `event_fields`, persisted as `usage_records.tier_served: Text NOT NULL DEFAULT 'standard'` + `usage_records.tier_capacity_degraded: Boolean NOT NULL DEFAULT false` (mirrors `cost_basis`/`usage_source` and the `guardrail_blocked`-style boolean-flag convention respectively), with an old-event-safe fallback in `flusher.py::insert_usage_row`.
  - M11: `resolve_tier_multiplier(session, tenant_id, model_id, tier) -> Decimal` fills region-pricing's RESERVED frozen signature in `rate_card_resolver.py` (SAME module, `model_id` accepted-but-unused): `tier != "priority"` → `Decimal("1")` (standard is definitionally the zero-markup baseline, never overridable); else a tenant override in NEW table `tenant_priority_markup_overrides` (tenant_id UNIQUE, `markup_pct Numeric(7,4)`) wins, ELSE the DECIDED seed `Decimal("25")` (MILESTONE.md, +25%) — returns `Decimal("1") + pct/100` so callers compose it by straight multiplication, matching region-pricing's own M9 formula `cost_usd = cost_usd * region_multiplier * tier_multiplier`. **DECIDED at freeze-review (Tin): confirmed, do not reopen.**
  - M12: The tier multiplier is applied at the SAME 3 sites region-pricing named as its own reserved insertion points, using `tier_served` (never the requested tier — M10's after-the-fact value, resolved ONCE per request alongside `region_multiplier`): (a) `recorder.py::record` — `cost_usd = cost_usd * region_multiplier * tier_multiplier`, and the disconnect-provider-cost back-derivation divides `tier_multiplier` back out too (mirrors region-pricing's own M3 correction); (b) `cost_recovery.py` — `target = cost.total_cost * (1+markup/100) * region_multiplier * tier_multiplier`, resolved FRESH against current rate-card state but keyed by the ORIGINAL row's stored `tier_served` (§0 issue #5); (c) `catalog/infrastructure/repository.py::list_active_models_with_markup` gains a NEW `tier: Literal["priority","standard"] | None = None` parameter — tier is CALLER-specific, not model-row-keyed like region, so this is a single extra scalar query resolved ONCE (not a per-row JOIN), folded into the existing bulk `multiplier` expression uniformly across every row. **DECIDED at freeze-review (Tin): overflow bills the tier SERVED (standard rate) per MILESTONE.md binding rule #4 — confirmed, do not reopen.**
  - M13: **[amended]** Two-tier permission split (Tin, freeze review — no new `Permission` enum value): (a) `PATCH /admin/keys/{key_id}` tier field, `POST /admin/keys` tier field, and the TENANT-scoped `PUT /admin/service-tiers/priority-markup` / `PUT /admin/service-tiers/default-tier` / `GET /admin/service-tiers` routes are gated by `Permission.KEYS_MANAGE` (owner-or-admin, reused — tier is key/tenant governance), scoped to the caller's own tenant, mirroring `region_pricing_router.py`'s file shape; (b) the FLEET-WIDE `tier_capacity_cluster_cap` / `tier_priority_reserved_pct` / `tier_standard_reserved_pct` knobs are a SEPARATE, SUPERADMIN-ONLY platform route, `GET/PUT /admin/platform/service-tiers`, gated by `Annotated[Identity, Depends(require_superadmin)]` (ALWAYS via `Depends()` — never a bare call, this project's own documented foot-gun, §0 Touches), mirroring `platform_tenants_router.py`'s bulk-list precedent (role-only check, no target tenant_id — this is a fleet setting, not a per-tenant one).
  - M14: Invoice generation and the margin dashboard require ZERO code changes (same free-inheritance guarantee region-pricing already established — both sum `usage_records.cost_usd` without recomputing).
</must>
Reject:
<reject>
  - R1: `tier` value outside {priority, standard, null} on `CreateKeyRequest`/`PatchKeyRequest`/admin routes -> "422 problem+json"
  - R2: negative or non-numeric `markup_pct` on `PUT /admin/service-tiers/priority-markup` -> "422 problem+json" (mirrors `Field(ge=0, max_digits=7, decimal_places=4)`)
  - R3: non-OWNER/non-ADMIN caller on any tenant-scoped `/admin/service-tiers/*` route or `PATCH /admin/keys/{id}` tier field -> "ERR_AUTH_FORBIDDEN" (403); non-SUPERADMIN caller on `/admin/platform/service-tiers` -> "ERR_AUTH_FORBIDDEN" (403), gate is `require_superadmin` via `Depends()`
  - R4: both `priority_floor` and `shared_pool` (and, for a priority request, `standard_floor`) exhausted at admission time -> "ERR_TIER_CAPACITY_EXHAUSTED" (503) + Retry-After, app/upstream NEVER invoked, NO usage record written (never billed for a shed request)
  - R5: `tier_priority_reserved_pct + tier_standard_reserved_pct > 1.0`, or either negative, at write time (platform route) or boot (env-sourced default) -> coerced to 0.20/0.20 + a WARN (route: 422 rejecting the write instead — a superadmin's explicit bad input is loud, unlike an env-var default; §1 assumption below), boot never crashes
  - R6: duplicate `PUT /admin/service-tiers/priority-markup` -> NOT an error — idempotent UPSERT (mirrors RC/region-pricing precedent)
  - R7: **[NEW]** Redis unavailable at admission time -> NEVER a 5xx from the tier layer itself — fail-open to `tier_served="standard"` + `tier_capacity_degraded=true`, admitted (M8a); the ONLY way a Redis outage produces a 503 is indirectly, via the (unrelated, unaffected) base `GlobalBackPressureMiddleware` if the process is independently overloaded
</reject>
After:
<after>
  - A tenant/key's resolved tier is known with zero extra DB reads at auth time; changing it takes effect on the VERY NEXT request (never cached/frozen at key-creation time).
  - Under contention (cluster cap reached), a priority request is admitted via a strictly wider set of pools than a standard request (priority_floor ∪ shared_pool ∪ standard_floor-as-last-resort vs standard_floor ∪ shared_pool) — a genuine, deterministic, CLUSTER-WIDE structural edge (atomic Redis Lua, not a per-worker approximation), not a race-dependent one.
  - `standard_floor` is a hard, priority-proof guarantee FLEET-WIDE: a standard request that arrives while `standard_floor` has room is NEVER blocked by priority load on ANY worker (priority only reaches `standard_floor` as its OWN last resort, after both its pools are exhausted, cluster-wide).
  - The tier ACTUALLY SERVED (which may be "standard" for an overflowed priority request, or for any request admitted during a Redis degradation) — never the tier selected on the key — lands on `usage_records.tier_served` and is the ONLY input to `resolve_tier_multiplier`'s billing composition; `tier_capacity_degraded` distinguishes "billed standard because the tier gate genuinely degraded" from "billed standard because that's simply what was served."
  - A Redis outage degrades tier differentiation (fail-open, `tier_served="standard"`, honestly billed) but NEVER produces a 5xx from the tier layer itself, and never touches the frozen `GlobalBackPressureMiddleware`'s own independent overload protection.
  - A crashed worker mid-hold cannot permanently strand a slot: the hold's ZSET score expires passively within `tier_capacity_hold_ttl_s`, no separate sweep process required; a subsequent `release()` (e.g. after the crash, from wherever the request actually landed) is a no-op `ZREM` if the member is already gone — never a double-release, never a negative count.
  - The frozen `GlobalBackPressureMiddleware` v1 contract is byte-identical, untouched, and still the outermost per-worker safety net (a request must clear ITS cap before the tier gate is ever consulted).
  - Catalog price display (via `list_active_models_with_markup`), `usage_records.cost_usd`, and invoice lines for a priority-served request all reflect the SAME `region_multiplier × tier_multiplier` composition, through the one shared resolver — zero drift, provable the same way region-pricing already proved it.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ #1 **[replaces the superseded per-worker flag]** `tier_capacity_hold_ttl_s`'s concrete value (§0 issue #7) — the passive-expiry window bounding how long a crashed worker's hold can falsely occupy a pool slot, and equally the SHORTEST duration a legitimately slow priority stream must never exceed without being silently un-counted. I do not have a verified figure for this gateway's actual longest realistic single-request duration (a slow non-streaming image/audio generation, or a very long streaming completion) — proposing 300s (5 minutes) as a conservative starting constant, refreshed via a lightweight heartbeat TOUCH (re-`ZADD` with a fresh score) at the natural mid-stream point if Build finds 300s too tight against real traffic. Lowest confidence because it is an empirical fact about real request-duration distribution, not something derivable from reading code. If wrong (too short): a slow legitimate stream loses its accounted slot mid-flight — soft over-admission of the pool it was "in," never a correctness break (occupancy is always live `ZCARD`, nothing trusts a stale client-side count) — cheap to raise the constant. If wrong (too long): a crashed worker's slot stays falsely occupied for up to that long, artificially shrinking real availability until it expires — moderate cost only under BOTH sustained load AND worker crashes at the same time, self-healing regardless.
  - [ ] #2 Cluster-cap MIGRATION: `tier_capacity_cluster_cap` (fleet-wide, Redis-visible) is a NEW, independently-operator-set number, deliberately NOT derived from `settings.max_concurrent_requests × worker_count` (the base guard has no worker-count knob today) — an operator must consciously set BOTH numbers to consistent values for the two guards to agree on real fleet capacity; nothing enforces that they stay in sync. If an operator sets `tier_capacity_cluster_cap` HIGHER than the base guard's true aggregate capacity, the tier gate could admit a request the base guard's own per-worker cap then sheds anyway (wasted Redis round trip, not a correctness bug — the base guard is still the final word) — confirm this two-knob design (rather than auto-deriving one from the other, which would need a new worker-count setting neither guard has today) is acceptable, or whether a worker-count knob should be introduced as a shared basis for both.
  - [ ] #3 300s default `tier_capacity_hold_ttl_s` combined with the base guard's OWN unrelated per-worker cap could, in a pathological combination (a worker crash mid-hold, immediately followed by a burst of new requests before the TTL elapses), transiently under-report true availability on the crashed pool — self-healing within the TTL window, flagged for Build to load-test rather than reason about purely on paper.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Key-level tier override wins over tenant default   # M1
  Given a tenant with default_tier="standard" and a key with tier="priority"
  When that key authenticates
  Then AuthzResult.tier == "priority" and tier_source == "key"
  And a sibling key on the same tenant with no override still resolves "standard"

Scenario: Absent key override falls back to the tenant default   # M1
  Given a tenant with default_tier="priority" and a key with tier=NULL
  When that key authenticates
  Then AuthzResult.tier == "priority" and tier_source == "tenant"
  And this required zero extra DB queries beyond the existing get_by_id LEFT JOIN

Scenario: Tier change mid-flight takes effect on the very next request   # M2
  Given an active key currently resolving tier="standard"
  When an OWNER PATCHes /admin/keys/{id} with {tier: "priority"}
  Then the NEXT request on that key resolves AuthzResult.tier == "priority"
  And an in-flight request that started BEFORE the PATCH keeps its already-resolved tier (no retroactive change)

Scenario: Clearing a key-level tier override reverts to tenant default   # M2
  Given a key with tier="priority" override and tenant default_tier="standard"
  When an OWNER PATCHes /admin/keys/{id} with {tier: null}
  Then the key resolves "standard" (tenant default) on its next request
  And the tenant's default_tier itself is unchanged

Scenario: Priority admitted via its own reserved floor under contention   # M3, M5, M7 [re-expressed against Redis state]
  Given tier_priority_reserved_pct=0.20, tier_standard_reserved_pct=0.20, tier_capacity_cluster_cap=10 (priority_floor cap=2, standard_floor cap=2, shared cap=6, each a Redis ZSET)
  When a priority request arrives while ZCARD(tier:pool:priority_floor) < 2
  Then the atomic Lua script prunes-then-ZADDs the request_id into priority_floor in ONE round trip, admitted with tier_served="priority"
  And ZCARD(tier:pool:shared) and ZCARD(tier:pool:standard_floor) are untouched by this admission

Scenario: Priority overflows into the shared pool once its own floor is full, still billed priority   # M3, M7, M10
  Given ZCARD(tier:pool:priority_floor) == cap (full) and ZCARD(tier:pool:shared) < cap
  When a new priority request arrives
  Then the priority_floor Lua script observes ZCARD==cap and does NOT add; the shared_pool Lua script (tried next) admits it, tier_served="priority"
  And usage_records.tier_served == "priority" for that request, billed with the priority markup

Scenario: Priority overflows all the way into standard's floor as a last resort — billed as standard   # M3, M7, M10, the milestone's "overflow" rule
  Given priority_floor AND shared_pool are both at capacity, standard_floor has room
  When a new priority request arrives
  Then it is admitted (not shed) via the standard_floor Lua script as its 3rd and final attempt, tier_served="standard"
  And usage_records.tier_served == "standard" for that request — NO priority markup applied, billed at the standard rate

Scenario: Standard is never starved — its reserved floor is priority-proof CLUSTER-WIDE   # M5, M7, the starvation bound
  Given sustained priority load has fully occupied priority_floor AND shared_pool across EVERY worker (a cluster-wide Redis fact, not a per-worker one) — standard_floor is untouched, since priority only reaches it as its own last resort AFTER both are full
  When a standard request arrives at ANY worker
  Then it is admitted via the standard_floor Lua script (its FIRST attempt) with tier_served="standard"
  And this holds deterministically for every standard request up to standard_floor's cap, regardless of how much priority load is sustained or which worker handles it — a cluster-wide guarantee, not a per-process one

Scenario: Cross-worker contention for the last priority slot — exactly one wins, atomically   # M7, the coordinator's cross-worker requirement
  Given priority_floor has exactly 1 free slot (ZCARD == cap-1) and TWO different gateway workers each receive a priority request at the same instant
  When both workers' Lua scripts execute against the SAME Redis instance
  Then Redis serializes the two EVALSHA calls (single-threaded script execution) — exactly ONE script observes ZCARD < cap and ZADDs successfully; the other observes the post-ZADD count and is rejected for that pool
  And the winner is admitted tier_served="priority" via priority_floor; the loser falls through to try shared_pool next (not shed outright, per M7's ordered attempts)

Scenario: All applicable pools exhausted sheds with the tier-specific code   # M8, R4
  Given priority_floor, shared_pool, AND standard_floor are all at capacity (cluster-wide)
  When a priority OR a standard request arrives (on any worker)
  Then it is rejected 503 + Retry-After + "ERR_TIER_CAPACITY_EXHAUSTED", the app/upstream is NEVER invoked
  And no usage record is written for the shed request (never billed)

Scenario: Slot held for the whole streaming response, then released   # M9
  Given a priority request admitted via priority_floor (its request_id is a ZSET member), streaming a slow response
  When the stream is still draining
  Then the ZSET member (and thus ZCARD(priority_floor)) stays present until _dispatch_record's Task (fired at full stream drain) completes
  And _settle_or_release_hold then calls ZREM — the member is removed, ZCARD(priority_floor) drops by 1

Scenario: A later governance rejection (credit/RPM/TPM) reverses an already-placed tier hold   # M3
  Given a request's tier-capacity hold succeeded (a ZSET member was added)
  When a LATER governance step (credit exhaustion or RPM/TPM) rejects the same request
  Then release() ZREMs the member immediately (not held until stream drain, since no stream ever starts)
  And the original rejection's error code/status is what the caller sees (the release never masks it)

Scenario: Redis blip mid-hold — reconnect-then-release must not double-release   # coordinator's requirement, M5/M9
  Given a request's hold ZADD succeeded, then Redis becomes briefly unavailable, then reconnects before the request completes
  When _settle_or_release_hold fires its ZREM after reconnect
  Then ZREM removes the member if still present (normal release) — but if the hold's TTL window (tier_capacity_hold_ttl_s) already elapsed DURING the blip and ZREMRANGEBYSCORE pruned it on some other script's invocation, this ZREM is a no-op (member already absent)
  And in NEITHER case does the pool's ZCARD go negative or get decremented twice — occupancy is always derived live from ZCARD, never a separately-mutated counter, so "double release" is structurally not a distinct failure mode from "release of an absent member"

Scenario: Redis unavailable at admission time — fail-open, honestly degraded, never a 5xx from the tier layer   # M8a, R7, the coordinator's degraded-Redis requirement
  Given Redis is unreachable (connection error / timeout) when check_and_hold's Lua call is attempted
  When a priority-tier request arrives
  Then the exception is caught, logged as a WARNING (tenant_id/request_id only), and the request is ADMITTED (fail-open) — tier_served="standard" and tier_capacity_degraded=true, regardless of the requested tier
  And usage_records for that request shows tier_served="standard" + tier_capacity_degraded=true — NOT billed the priority markup, and the frozen GlobalBackPressureMiddleware (which never touches Redis) is completely unaffected and still protects the process

Scenario: Redis unavailable at release time — swallowed, never blocks the response   # M8a
  Given a request was admitted (hold placed) before Redis became unavailable
  When _settle_or_release_hold's ZREM call raises a Redis exception
  Then the exception is logged as a WARNING and swallowed — the response to the caller is completely unaffected
  And the orphaned hold self-heals once its TTL window elapses (§0 issue #7) — no separate sweep process needed

Scenario: Disabled tiering (default cluster cap = 0) is byte-identical   # M4, M6
  Given tier_capacity_cluster_cap=0 (the default — the 20/20/60 split percentages are inert until this is set)
  When priority and standard requests both arrive under contention
  Then RedisTierCapacityGuard is a pass-through — no Redis touched, no accounting, tier_served == tier requested, no 503 from this layer
  And this matches today's tier-blind behavior exactly

Scenario: Superadmin-adjustable split takes effect without a restart   # M6, M13
  Given tier_capacity_cluster_cap=100 already active with the 20/20/60 default split
  When a SUPERADMIN PUTs /admin/platform/service-tiers with {priority_reserved_pct: 0.3, standard_reserved_pct: 0.1}
  Then subsequent admission decisions use the new 30/10/60 split immediately (Redis-backed — no per-worker restart-to-apply, unlike the base guard's own semaphore sizing)
  And in-flight holds already placed under the OLD split are unaffected (their ZSET membership doesn't change; only NEW admission decisions read the new caps)

Scenario: Catalog price display matches billed price for a priority key — the drift scenario   # M12, milestone exit criterion
  Given a tenant with no priority-markup override, priority_markup seed=25%
  When that tenant's priority-tier key calls the admin catalog listing
  Then the displayed price already reflects the 1.25x priority premium composed with any region multiplier
  And a chargeable request against the same model, served at "priority", bills the IDENTICAL effective multiplier — zero drift

Scenario: Tenant overrides the priority markup   # M11, M13
  Given a tenant PUTs {markup_pct: 15} to /admin/service-tiers/priority-markup
  When that tenant's priority-served requests are billed
  Then cost_usd reflects the 1.15x override, not the 1.25x seed
  And every OTHER tenant with no override still resolves priority markup at 1.25x, unchanged

Scenario: Cost recovery on a priority-served request matches the original effective rate   # M12
  Given a priority-served request's usage_records row (tier_served="priority") was under-billed pending upstream settlement
  When cost_recovery.py computes the recovery delta
  Then target == settled_upstream_cost × (1+markup_pct/100) × region_multiplier × tier_multiplier, re-resolved FRESH against current rate-card state but keyed by the STORED tier_served (not re-derived from live admission)
  And the recovered row's implied rate matches what a fresh record() call for that (tenant, model, tier_served) would produce

Scenario: Invalid tier value on any admin/key surface is rejected   # R1
  Given PATCH /admin/keys/{id} with {tier: "gold"} or PUT /admin/service-tiers/default-tier with {default_tier: "vip"}
  When the request is made
  Then 422 problem+json is returned
  And no row is written/changed

Scenario: Invalid markup_pct is rejected   # R2
  Given PUT /admin/service-tiers/priority-markup with {markup_pct: -5} or {markup_pct: "abc"}, called by an OWNER
  When the request is made
  Then 422 problem+json is returned
  And no override row is written

Scenario: Non-owner cannot manage tenant-scoped service tiers   # R3
  Given a MEMBER-role caller
  When they PUT /admin/service-tiers/priority-markup or PATCH a key's tier field
  Then 403 ERR_AUTH_FORBIDDEN is returned
  And no row is written/changed

Scenario: Non-superadmin cannot touch the fleet-wide platform knob, even a tenant OWNER   # R3, the coordinator's permission split
  Given a tenant OWNER caller (has Permission.KEYS_MANAGE for their own tenant, but is not SUPERADMIN)
  When they PUT /admin/platform/service-tiers with {priority_reserved_pct: 0.5}
  Then 403 ERR_AUTH_FORBIDDEN is returned — require_superadmin (via Depends()) rejects before any tenant-scope logic runs
  And the fleet-wide split is unchanged for every tenant

Scenario: Malformed reservation-fraction write is rejected loudly on the platform route   # R5
  Given a SUPERADMIN PUTs /admin/platform/service-tiers with {priority_reserved_pct: 0.7, standard_reserved_pct: 0.6} (sum > 1.0)
  When the request is made
  Then 422 problem+json is returned — an explicit superadmin write gets a loud rejection, NOT a silent coerce
  And the previously-active split is unchanged

Scenario: Malformed env-sourced defaults never crash boot   # R5
  Given GATEWAY_TIER_PRIORITY_RESERVED_PCT=0.7 and GATEWAY_TIER_STANDARD_RESERVED_PCT=0.6 (sum > 1.0) at process start
  When the app boots
  Then both are coerced to 0.20/0.20 (the DECIDED default) + a startup WARN is logged, boot succeeds
  And RedisTierCapacityGuard uses the coerced 20/20/60 split, not a crash

Scenario: Duplicate priority-markup PUT is an idempotent upsert   # R6
  Given an existing override at 15%
  When the same tenant PUTs {markup_pct: 18} again
  Then the SAME row is UPDATEd to 18%, not duplicated
  And exactly one row exists for that tenant afterward
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
PATCH  /admin/keys/{key_id}   body: { ..., tier: "priority"|"standard"|null }   [EXTEND existing PatchKeyRequest]
  200 -> KeyInfoResponse (+= tier: "priority"|"standard")
  403 -> problem+json "ERR_AUTH_FORBIDDEN"   (caller lacks KEYS_MANAGE / not owner-or-admin)
  404 -> problem+json "KEY_NOT_FOUND"        (revoked or cross-tenant — unchanged, no new leak)
  422 -> problem+json                        (tier not in {priority, standard, null})

POST   /admin/keys   body: { ..., tier: "priority"|"standard"|null }   [EXTEND existing CreateKeyRequest]
  200 -> KeyInfoResponse (+= tier)   (omit/null tier = inherit tenant default, resolved live per-request)
  422 -> problem+json                (tier not in {priority, standard, null})

PUT    /admin/service-tiers/default-tier   body: { default_tier: "priority"|"standard" }
  200 -> { default_tier: string }
  403 -> problem+json "ERR_AUTH_FORBIDDEN"
  422 -> problem+json                        (default_tier not in {priority, standard})

PUT    /admin/service-tiers/priority-markup   body: { markup_pct: number }
  200 -> { markup_pct: string }
  403 -> problem+json "ERR_AUTH_FORBIDDEN"
  422 -> problem+json                        (negative | non-numeric | exceeds Numeric(7,4))

GET    /admin/service-tiers   -> { default_tier: string, priority_markup_pct: string }   (effective: override-or-seed)

Every /admin/service-tiers/* route and the tier field on /admin/keys/* require Permission.KEYS_MANAGE
(owner-or-admin, reused) and act on the CALLER'S OWN tenant only.

GET    /admin/platform/service-tiers   [NEW — amended, SUPERADMIN-ONLY, mirrors platform_tenants_router.py]
  -> { cluster_cap: int, priority_reserved_pct: string, standard_reserved_pct: string }
PUT    /admin/platform/service-tiers   body: { cluster_cap?: int, priority_reserved_pct?: number, standard_reserved_pct?: number }
  200 -> { cluster_cap: int, priority_reserved_pct: string, standard_reserved_pct: string }
  403 -> problem+json "ERR_AUTH_FORBIDDEN"   (caller is not SUPERADMIN — gate is Depends(require_superadmin), never a bare call)
  422 -> problem+json   (cluster_cap < 0 | either pct negative | pct sum > 1.0 | non-numeric)
  -- FLEET-WIDE, no target tenant_id (mirrors platform_tenants_router.py's bulk-list precedent: a
     role-only check, deliberately NOT authorize_tenant_scope). Takes effect immediately (Redis-
     backed, no worker restart) for subsequent admission decisions; in-flight holds are unaffected.

No new HTTP route for admission itself — TierCapacityGuard is an internal port called from governance;
a shed surfaces as the existing chat/embeddings/images/audio completion routes returning:
  <any chargeable route>
  503 -> problem+json "ERR_TIER_CAPACITY_EXHAUSTED"   header: Retry-After: <int seconds>
    (distinct from the base guard's "ERR_OVERLOADED" — this code means "the base guard admitted you,
    but your tier's reserved+shared capacity is exhausted"; app/upstream never invoked, never billed)
```

Schema (additive only):
```
ALTER TABLE api_keys ADD COLUMN tier TEXT NULL
  CHECK (tier IS NULL OR tier IN ('priority', 'standard'));
ALTER TABLE tenants  ADD COLUMN default_tier TEXT NOT NULL DEFAULT 'standard'
  CHECK (default_tier IN ('priority', 'standard'));

NEW TABLE tenant_priority_markup_overrides   (one row per tenant — only "priority" ever carries a markup)
  id          uuid7 PK
  tenant_id   FK tenants.id ON DELETE CASCADE, NOT NULL, UNIQUE
  markup_pct  Numeric(7,4), NOT NULL, CHECK (markup_pct >= 0)   -- mirrors tenants.markup_pct convention
  created_at, updated_at

ALTER TABLE usage_records ADD COLUMN tier_served TEXT NOT NULL DEFAULT 'standard'
  -- mirrors cost_basis/usage_source discriminator-column convention exactly; no backfill needed
  -- (append-only ledger; every pre-existing row is honestly "standard" — tiering did not exist yet)
ALTER TABLE usage_records ADD COLUMN tier_capacity_degraded BOOLEAN NOT NULL DEFAULT false
  -- [NEW, amended] mirrors the guardrail_blocked-style boolean-flag convention — true only when
  -- Redis was unavailable at admission time for THIS specific request (M8a); the audit trail
  -- distinguishing "billed standard because the gate genuinely degraded" from "billed standard
  -- because that's simply what was served."

Reads only, never writes (owned by region-pricing's frozen tenants.markup_pct / models table — not
redefined here): tenants.markup_pct, models.region, models.id.

Redis keys (no SQL schema — ephemeral, TTL-bounded, mirrors ratelimit:*/bandwidth:* key-naming):
  tier:pool:priority_floor   ZSET  member=hex(request_id)  score=now_ms
  tier:pool:standard_floor   ZSET  member=hex(request_id)  score=now_ms
  tier:pool:shared           ZSET  member=hex(request_id)  score=now_ms
  -- each entry pruned (ZREMRANGEBYSCORE, score < now_ms - tier_capacity_hold_ttl_s*1000) at the
  -- START of every admission-Lua-script invocation touching that key — passive, no sweep process.
```

New/extended symbols:
```
gateway.keys.domain.entities
  AuthzResult
    + tier: Literal["priority", "standard"] = "standard"          [NEW, additive default]
    + tier_source: Literal["key", "tenant"] = "tenant"             [NEW, mirrors policy_source]

gateway.keys.application.use_cases :: AuthzUseCase.execute
  [EXTEND] resolve tier = row.tier if row.tier is not None else getattr(row, "default_tier", "standard")
           via the EXISTING LEFT JOIN tenants in ApiKeyRepository.get_by_id — zero extra DB reads

gateway.keys.infrastructure.orm
  ApiKeyRow.tier: Mapped[str | None] = mapped_column(Text, nullable=True)                    [NEW]
gateway.tenants.infrastructure.orm
  TenantRow.default_tier: Mapped[str] = mapped_column(Text, nullable=False, server_default="standard")  [NEW]

gateway.keys.api.schemas
  CreateKeyRequest.tier: Literal["priority", "standard"] | None = None                        [NEW]
  PatchKeyRequest.tier: str | None = None    -- three-state PATCH convention (§1 M2)           [NEW]

gateway.proxy.domain.tier_capacity   [NEW MODULE]
  ServiceTier = Literal["priority", "standard"]
  class TierHold(NamedTuple):    -- [NEW, amended] what check_and_hold returns, replaces a bare ServiceTier
      tier_served: ServiceTier
      degraded: bool             -- True only when Redis was unavailable at admission time (M8a)
  class TierCapacityGuard(Protocol):
      async def check_and_hold(self, tenant_id: UUID, tier: ServiceTier, request_id: UUID) -> TierHold: ...
          # [amended] returns TierHold(tier_served, degraded) — tier_served may differ from `tier`
          # on overflow (§1 M7) OR on degradation (§1 M8a, degraded=True forces tier_served="standard").
          # Raises ProblemError(503, "ERR_TIER_CAPACITY_EXHAUSTED") ONLY when Redis is reachable and
          # every applicable pool is genuinely full — NEVER raises for a Redis/infra failure itself
          # (that path fails OPEN into a degraded TierHold, not an exception — §1 M8a).
      async def release(self, tenant_id: UUID, request_id: UUID) -> None: ...
          # best-effort, never raises — same idiom as CreditGuard.release / RedisTokenBucket.reconcile;
          # a Redis exception here is logged + swallowed (M8a), the hold's own TTL is the backstop.

gateway.proxy.infrastructure.tier_capacity_guard   [NEW MODULE, mirrors redis_lua_limiter.py's/redis_token_bucket.py's shape]
  class PassthroughTierCapacityGuard:
      async def check_and_hold(self, tenant_id, tier, request_id) -> TierHold: return TierHold(tier, False)
      async def release(self, tenant_id, request_id) -> None: return
  class RedisTierCapacityGuard:    -- [REPLACES InProcessTierCapacityGuard, amended at freeze-review]
      __init__(self, *, redis: Any, cluster_cap: int, priority_reserved_pct: float,
                standard_reserved_pct: float, hold_ttl_s: int = 300)
          # constructed once at create_app() (register_script only — no Redis round trip, safe
          # without lifespan, same precedent as RedisLuaRateLimiter/RedisDeploymentLoadGate);
          # Pp = round(cluster_cap*priority_pct), Ps = round(cluster_cap*standard_pct),
          # Psh = cluster_cap - Pp - Ps; cluster_cap<=0 -> pass-through mode (no Redis touched).
          # registers ONE Lua script (_POOL_ADMIT_LUA, parameterized by pool key + cap) reused for
          # all three pools — mirrors RedisLuaRateLimiter's register-once-reuse-via-args pattern.
      _POOL_ADMIT_LUA:  -- KEYS[1]=pool zset key  ARGV: now_ms, ttl_ms, cap, member(request_id)
          # 1. ZREMRANGEBYSCORE key 0 (now_ms - ttl_ms)     -- passive prune (§0 issue #7)
          # 2. local n = ZCARD(key)
          # 3. if n < cap: ZADD(key, now_ms, member); EXPIRE(key, ttl_s*2); return 1
          #    else: return 0
          # atomic — mirrors RedisLuaRateLimiter's prune-then-count-then-admit shape exactly (§0).
      check_and_hold: tries pools per §1 M7 order (priority: floor -> shared -> standard_floor -> shed;
          standard: floor -> shared -> shed), ONE _POOL_ADMIT_LUA call per pool attempted; ANY
          Exception (connection error, timeout) on ANY call -> catch, log WARNING (tenant_id/
          request_id only), return TierHold("standard", degraded=True) immediately — fail-open,
          mirrors RedisLuaRateLimiter's "any Exception -> admit" idiom exactly (§0 Honors).
      release: ZREM the pool key the hold actually landed in (tracked via an in-memory
          dict[request_id -> pool_name] scoped to THIS guard instance for the request's lifetime —
          same bookkeeping need the original per-worker design had, just remembering a Redis key
          name instead of a Semaphore object); Exception on ZREM -> log WARNING + swallow (M8a).

gateway.proxy.application.use_cases :: CompletionUseCase.__init__ / _enforce_governance
  [EXTEND __init__] + tier_capacity_guard: TierCapacityGuard = PassthroughTierCapacityGuard()
  [EXTEND _enforce_governance] insert immediately BEFORE the credit hold:
      _tier_request_id = request_id if request_id is not None else uuid.uuid4()
      _tier_hold = await self._tier_capacity_guard.check_and_hold(   # [amended] TierHold, not bare tier
          authz.tenant_id, authz.tier, _tier_request_id
      )
      tier_served, tier_capacity_degraded = _tier_hold.tier_served, _tier_hold.degraded
      # wrap the credit-hold call itself so a LATER credit rejection also releases the tier hold:
      try:
          await self._credit_guard.check_and_hold(...)
      except Exception:
          await self._tier_capacity_guard.release(authz.tenant_id, _tier_request_id)
          raise
      # extend the EXISTING RPM/TPM try/except to release BOTH holds:
      except Exception:
          await self._credit_guard.release(authz.tenant_id, _credit_request_id)
          await self._tier_capacity_guard.release(authz.tenant_id, _tier_request_id)
          raise

gateway.proxy.application.governance :: NonChatGovernance.__init__ / authorize
  [EXTEND] identical insertion — dual-copy governance, §0 Honors (the credits-ledger build's own
  "HEAL (finding 2)" note is the documented trap this task avoids by wiring both from the start).

gateway.proxy.application.use_cases
  _tier_hold_ctx: ContextVar[tuple[TierCapacityGuard, uuid.UUID] | None]                      [NEW]
      -- set alongside _credit_hold_ctx.set(...), immediately after the tier hold succeeds.
  _settle_or_release_hold   [EXTEND] also consume _tier_hold_ctx and call
      tier_capacity_guard.release(tenant_id, request_id) unconditionally (no settle/release split
      needed — tier capacity is a binary occupied/free slot, not a money amount; deliberate
      simplification vs CreditGuard's separate settle()/release()).

gateway.usage.application.rate_card_resolver
  resolve_markup_pct(...)                — FROZEN v1, UNTOUCHED (cited, not edited)
  resolve_region_multiplier(...)         — FROZEN v1 (region-pricing), UNTOUCHED (cited, not edited)
  + resolve_tier_multiplier(session, tenant_id, model_id, tier) -> Decimal   [FILLS the RESERVED signature]
      1. if tier != "priority": return Decimal("1")
      2. SELECT markup_pct FROM tenant_priority_markup_overrides WHERE tenant_id=:t
      3. ELSE the DECIDED seed: Decimal("25")   -- +25%, MILESTONE.md
      4. return Decimal("1") + pct / Decimal("100")

gateway.usage.application.recorder.py :: RecordingUsageRecorder.record / record_with_outcome / _record_internal
  [EXTEND] resolve tier_multiplier once (alongside region_multiplier, same batch, no N+1), using tier_served
  [EXTEND] cost_usd = cost_usd * region_multiplier * tier_multiplier   (extends region-pricing's own line)
  [EXTEND] disconnect-provider-cost back-derivation also divides out tier_multiplier:
           provider_cost = cost_usd / ((Decimal("1")+markup_pct/100) * region_multiplier * tier_multiplier)
  [EXTEND] event_fields["tier_served"] = tier_served   (new discriminator, mirrors cost_basis/usage_source)
  [EXTEND] event_fields["tier_capacity_degraded"] = tier_capacity_degraded   (new bool discriminator, M8a)

gateway.usage.application.cost_recovery.py :: OpenRouterCostRecovery
  [EXTEND] resolve tier_multiplier via resolve_tier_multiplier, keyed by the ORIGINAL row's stored
           tier_served (read from usage_records, never re-derived from live admission — §0 issue #5)
  [EXTEND] target = cost.total_cost * (1+markup/100) * region_multiplier * tier_multiplier

gateway.catalog.infrastructure.repository.py :: CatalogRepository.list_active_models_with_markup
  [EXTEND] new parameter tier: Literal["priority","standard"] | None = None
  [EXTEND] ONE extra scalar query (tenant-scoped, not per-row — tier is caller-specific, not model-row-
           keyed like region) resolves tier_multiplier once; folds into the existing bulk multiplier:
           multiplier = float(((1+row.markup_pct/100)) * effective_region_multiplier * tier_multiplier)

gateway.proxy.domain.ports :: UsageRecordExtras (TypedDict, total=False)
  + tier_served: Literal["priority", "standard"]                                              [NEW key]
  + tier_capacity_degraded: bool                                                     [NEW key, amended]
gateway.usage.application.recorder.py :: RecordingUsageRecorder.supported_extras
  [EXTEND] add "tier_served" and "tier_capacity_degraded" to the frozenset
gateway.usage.infrastructure.orm.py :: UsageRecordRow
  + tier_served: Mapped[str] = mapped_column(Text, nullable=False, server_default="standard")  [NEW]
  + tier_capacity_degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa.false())  [NEW, amended]
gateway.usage.application.flusher.py :: insert_usage_row
  [EXTEND] INSERT columns + params tier_served / tier_capacity_degraded, fallbacks:
           _event_field(fields, "tier_served") or "standard" ·
           _event_field(fields, "tier_capacity_degraded") or False

gateway.core.config.py :: Settings   -- [amended, all three NEW]
  + tier_capacity_cluster_cap: int = Field(default=0)         # env GATEWAY_TIER_CAPACITY_CLUSTER_CAP
  + tier_priority_reserved_pct: float = Field(default=0.20)   # env GATEWAY_TIER_PRIORITY_RESERVED_PCT
  + tier_standard_reserved_pct: float = Field(default=0.20)   # env GATEWAY_TIER_STANDARD_RESERVED_PCT
  + tier_capacity_hold_ttl_s: int = Field(default=300)        # env GATEWAY_TIER_CAPACITY_HOLD_TTL_S
  validator: either pct negative, or pct sum > 1.0 -> coerce both pcts to 0.20/0.20 + WARN (env path
             only; the platform ROUTE below rejects the equivalent write with 422 instead — R5)

gateway.tenants.api.service_tier_router.py       [NEW — tenant-owner, mirrors region_pricing_router.py]
gateway.tenants.infrastructure.tier_markup_orm.py   [NEW — mirrors region_pricing_orm.py]
gateway.tenants.api.platform_service_tier_router.py   [NEW, amended — SUPERADMIN-ONLY, mirrors
  platform_tenant_config_router.py's Depends(require_superadmin) gate + platform_tenants_router.py's
  no-target-tenant bulk-route shape; reads/writes Settings-backed live values (a small KV row or the
  Settings singleton's mutable fields — NOT a per-tenant table, this is ONE fleet-wide row)]

gateway.billing.application.invoice_generator.py       [ZERO CHANGES — cited as proof]
gateway.usage.api.margin_router.py                     [ZERO CHANGES — cited as proof]
gateway.proxy.api.concurrency_guard.py :: GlobalBackPressureMiddleware   [ZERO CHANGES — frozen, cited as proof]
```

Glossary deltas:
- **Service tier**: `priority` | `standard` — a per-key (overridable per-tenant-default) capacity PREFERENCE (not a guarantee) resolved fresh at auth time via `AuthzResult.tier`. Distinct from the frozen global back-pressure guard's cap, which is tier-blind by construction.
- **Tier served** (`tier_served`): the tier a request was ACTUALLY admitted at — may differ from the requested/selected tier on overflow (a priority request admitted through standard's reserved floor is `tier_served="standard"`) OR on Redis degradation (§1 M8a). The ONLY input to `resolve_tier_multiplier`'s billing composition and the value stamped on `usage_records.tier_served`; never the requested tier. **DECIDED at freeze-review (Tin): overflow bills the SERVED tier — confirmed.**
- **Tier capacity degraded** (`tier_capacity_degraded`): **[NEW, amended]** a boolean discriminator on `usage_records`, true only when the Redis-backed tier gate was unreachable at admission time for that specific request — distinguishes an honest standard-rate bill caused by infrastructure degradation from one caused by ordinary overflow.
- **Priority-reserved / standard-reserved floor**: **[amended]** the two tier-exclusive Redis sorted-set pools (`tier:pool:priority_floor` / `tier:pool:standard_floor`), atomically admitted/pruned via one Lua script per attempt, carved out of the FLEET-WIDE `settings.tier_capacity_cluster_cap` by `tier_priority_reserved_pct`/`tier_standard_reserved_pct` (DECIDED default 20%/20%, 60% shared); `standard_floor` is the concrete, testable, CLUSTER-WIDE "standard never starved" bound — priority reaches it only as its own last resort.
- **Tier capacity cluster cap** (`tier_capacity_cluster_cap`): **[NEW, amended]** the explicit, operator-set, fleet-wide admission budget the tier pools partition — deliberately independent of the base guard's per-worker `max_concurrent_requests` (§1 assumption #2), superadmin-adjustable at runtime with no restart.
- **Tier markup**: the priority-tier price premium composed via `resolve_tier_multiplier`, DECIDED seed +25% (percentage-additive, mirrors `markup_pct`'s convention — deliberately NOT multiplicative like region), tenant-overridable via `tenant_priority_markup_overrides`, standard is definitionally 1.0 (never overridable). **DECIDED at freeze-review (Tin): confirmed.** [folded foundation-version 52]

Status: FROZEN @ v1 — approved by Tin Dang
Reported: no

Least-sure flag surfaced at freeze: ⚠ [contract] `tier_capacity_hold_ttl_s`'s proposed default
(300s, §1 assumption #1) — the Redis ZSET-score passive-expiry window bounding both (a) how long a
crashed worker's hold can falsely occupy a pool slot, and (b) the shortest duration a legitimately
slow priority stream must never exceed without being silently un-counted (soft over-admission of
its pool, never a correctness break — occupancy is always live ZCARD). I do not have a verified
figure for this gateway's actual longest realistic single-request duration; 300s is a conservative
starting constant, not an empirically-grounded one. Low cost if wrong-but-close (self-healing
either direction, bounded by the TTL itself). Moderate cost if a real slow-stream population
regularly exceeds it under real priority traffic (transient false capacity pressure until the
window rolls over) — cheap to raise the constant once Build/Verify has real latency data; no
call-site or schema change needed either way. Runner-up ⚠ [contract] assumption #2 — the fleet-wide
`tier_capacity_cluster_cap` is a SEPARATE, independently-operator-set number from the base guard's
per-worker `max_concurrent_requests` (no shared worker-count basis exists in this codebase today);
nothing enforces the two stay consistent, so an operator could set them to disagree — confirmed
acceptable as a two-knob design rather than inventing a new worker-count setting shared by both
guards, but worth Tin's explicit sign-off since it is a genuine two-number-must-agree operational
risk, not a technical unknown.
(Previously top-flagged per-worker-accounting concern is RESOLVED — Redis cross-worker accounting
per Tin's freeze-review decision, §0 issue #2/Framings weighed. Previously-open default-split and
permission-reuse assumptions are RESOLVED — DECIDED 20/20/60 + KEYS_MANAGE/superadmin split, §1 M6/M13.)

DECIDED at freeze review (2026-07-12, Tin): (1) Redis cross-worker accounting CONFIRMED as amended
(ZSET pools, live-ZCARD occupancy, fail-open degradation with tier_served="standard" +
tier_capacity_degraded discriminator). (2) `tier_capacity_hold_ttl_s` seeds at **600s** (not
the drafted 300s) — realtime WS sessions routinely exceed 5 minutes and expiry errs toward
soft over-admission; settings-tunable. (3) Dual capacity knobs ACCEPTED **plus a required
STARTUP WARNING** (new build requirement): log a warning at boot when
tier_capacity_cluster_cap < max_concurrent_requests (the one per-process-detectable certain
misconfiguration); ops guidance (cluster_cap ≈ workers × max_concurrent_requests) in settings
docstrings + runbook. (4) 20/20/60 seed; KEYS_MANAGE + superadmin platform route; overflow
bills tier SERVED per milestone rule 4.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 54 tests across 4 files, one per §2 scenario (+ dual-copy-governance and
byte-identical-regression bonus tests, mirroring credits-ledger/region-pricing precedent).
100% of the §2 gherkin scenarios have at least one asserting test; `src/gateway` §3-touched
modules verified via `uv run pyright src` (0 errors) + full-project `ruff check`/`format`.

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_key_level_tier_override_wins_over_tenant_default: Given a key with tier="priority" + tenant default_tier="standard" / When AuthzUseCase resolves it / Then tier="priority", tier_source="key"; a sibling key with no override resolves "standard" · covers: M1
  - test_absent_key_override_falls_back_to_tenant_default: Given tenant default_tier="priority", no key override / When resolved / Then tier="priority", tier_source="tenant" · covers: M1
  - test_tier_patch_takes_effect_on_next_request: Given a key with no tier / When PATCHed {tier:"priority"} / Then the VERY NEXT resolution reflects it · covers: M2
  - test_clearing_key_override_reverts_to_tenant_default: Given tier="priority" override + tenant default="standard" / When PATCHed {tier:null} / Then resolves "standard"; tenant default_tier itself unchanged · covers: M2
  - test_invalid_tier_on_create_key_rejected / test_invalid_tier_on_patch_key_rejected / test_invalid_default_tier_on_admin_route_rejected: Given tier="gold"/"vip" / When POST|PATCH key or PUT default-tier / Then 422, no row changed · covers: R1
  - test_invalid_markup_pct_rejected[bad_markup]: Given markup_pct=-5|"abc" / When PUT priority-markup / Then 422, zero override rows written · covers: R2
  - test_member_cannot_manage_tenant_service_tiers / test_admin_role_can_manage_tenant_service_tiers: Given KEYS_MANAGE held by OWNER+ADMIN+OPERATOR, not MEMBER (confirmed via authz.py ROLE_PERMISSIONS) / When MEMBER vs ADMIN PUT/PATCH / Then 403 vs 200 · covers: R3
  - test_duplicate_priority_markup_put_idempotent_upsert: Given two PUTs with different pct / When applied / Then exactly one row survives, latest value · covers: R6
  - test_get_service_tiers_returns_effective_seed_when_no_override / test_get_service_tiers_reflects_override: Given no/with override / When GET /admin/service-tiers / Then effective (seed-or-override) values returned · covers: M11 read path
  - test_tenant_owner_403_on_platform_route / test_superadmin_can_read_and_write_platform_route: Given a tenant OWNER vs a platform SUPERADMIN / When GET/PUT /admin/platform/service-tiers / Then 403 vs 200 · covers: R3 (platform variant)
  - test_platform_route_rejects_pct_sum_over_one / test_platform_route_rejects_merged_sum_over_one_against_stale_partner: Given pcts summing >1.0 (direct or merged-against-a-prior-PUT) / When PUT / Then 422, unchanged · covers: R5 (live-write half)
  - test_env_boot_coerces_invalid_pct_sum_and_warns / test_env_boot_coerces_negative_pct_and_warns: Given Settings(...) constructed with an invalid pct / When booted / Then coerced to 0.20/0.20 + WARNING logged (never raises) · covers: R5 (env-boot half)
  - test_startup_warns_when_cluster_cap_below_max_concurrent_requests / test_startup_does_not_warn_when_cluster_cap_sufficient / test_startup_does_not_warn_when_tiering_disabled: Given cluster_cap</>=/==0 vs max_concurrent_requests / When create_app() boots / Then the REQUIRED startup WARNING fires exactly when cluster_cap>0 AND cluster_cap<max_concurrent · covers: DECIDED-at-freeze-review startup warning
  - test_platform_put_reconfigures_live_guard_without_restart: Given a REAL RedisTierCapacityGuard wired at boot / When PUT changes the split / Then the SAME instance's pool caps mutate in place, no restart · covers: M6, M13
  - test_admission_places_hold_via_pool_zset / test_release_is_idempotent_never_double_decrements / test_release_of_ttl_expired_member_is_a_noop / test_cross_worker_race_for_last_slot_exactly_one_wins / test_redis_unavailable_at_admission_fails_open_degraded / test_redis_unavailable_at_release_swallowed (+ 10 more in test_capacity_guard.py): direct-port Lua-script mechanics (atomicity, idempotent release, passive TTL reclaim, cross-worker race, fail-open degrade) · covers: M3, M5, M7, M8a, coordinator's cross-worker/double-release requirements
  - test_tier_hold_wired_at_chat_choke_point / test_tier_hold_wired_at_nonchat_choke_point: Given a real guard with cluster_cap=1 / When /v1/chat/completions vs /v1/embeddings is called against a pre-filled pool / Then BOTH choke points shed 503 — dual-copy governance proven, not just one pipeline copy · covers: M3, M7
  - test_all_pools_exhausted_sheds_with_tier_specific_code: Given every applicable pool full / When a request arrives / Then 503 ERR_TIER_CAPACITY_EXHAUSTED + Retry-After, upstream never invoked, no usage record written · covers: M8, R4
  - test_tier_hold_released_on_later_rpm_rejection: Given a tier hold succeeded / When a LATER RPM rejection fires / Then release() ZREMs it immediately, pool occupancy returns to 0, the RPM error is what the caller sees · covers: M3 (later-rejection reversal)
  - test_slot_held_for_whole_response_then_released: Given a gated slow upstream holding the sole slot / When a second request arrives mid-flight / Then it is shed (slot genuinely occupied); once the first drains, the slot releases · covers: M9
  - test_disabled_tiering_is_byte_identical: Given cluster_cap=0 (default) / When a request is made / Then PassthroughTierCapacityGuard never touches Redis, response unchanged · covers: M4, M6 (disabled-tiering scenario)
  - test_standard_tier_is_always_the_identity_multiplier / test_priority_tier_with_no_override_resolves_seed_25pct / test_priority_tier_with_override_resolves_override_not_seed: pure-unit resolve_tier_multiplier resolution rules · covers: M11
  - test_recorder_composes_tier_multiplier_for_priority_served / test_recorder_standard_served_stays_byte_identical: Given tier_served="priority"/"standard" / When recorder.record() + flusher flush / Then cost_usd = base × markup × region × tier_multiplier (identity for standard) · covers: M12
  - test_tenant_priority_markup_override_wins_others_unaffected: Given tenant A overrides to 15%, tenant B has none / When both bill priority-served requests / Then A bills 1.15x, B still bills the 1.25x seed unchanged · covers: M11, M13
  - test_catalog_price_matches_billed_price_zero_drift: Given no override, seed=25% / When ListModelsForTenantUseCase.execute(tier="priority") is called directly (see the in-file NOTE on why not via HTTP — no per-key-tier catalog route exists in the frozen contract) AND a real billed request is recorded / Then catalog multiplier == billing multiplier, zero drift · covers: M12, milestone exit criterion
  - test_cost_recovery_matches_priority_served_rate: Given an anchor row with tier_served="priority" / When cost_recovery.py recovers the delta / Then target composes markup × region × tier_multiplier, keyed by the STORED tier_served, matching what a fresh record() would produce · covers: M12
</test_plan>

Tests live in: `./tests/service_tiers` (†54, 4 files: test_capacity_guard.py, test_tier_resolution_and_admin.py, test_platform_admin_and_settings.py, test_governance_wiring.py, test_billing_composition.py) · MUST run red (missing implementation) before Build.

**PROCESS DEVIATION — disclosed honestly (not silently absorbed):** a session compaction
mid-build meant the bulk of the `src/gateway` implementation was already substantially
complete BEFORE this §4 suite was written — the strict RED-before-implementation TDD
sequencing the persona/CLAUDE.md mandate was broken by session-continuity constraints, not
by a deliberate skip. Each test in this session was nonetheless independently reasoned
about for its RED failure mode before being trusted (see each test file's module
docstring for the specific RED reason: AttributeError/NotImplementedError/404/wrong-
Decimal, never a broken harness) — several genuinely surfaced RED on first run against the
already-mostly-built tree (wrong TTL config in one earlier defect, structlog test-capture
plumbing, pool-pre-fill score bugs in the test code itself) confirming the suite exercises
real behavior, not tautologies. Flagged in the final report's `open_questions`.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `./src/gateway/proxy/infrastructure/tier_capacity_guard.py` (new),
`./src/gateway/proxy/domain/tier_capacity.py` (new: ServiceTier, TierHold), `./src/gateway/proxy/application/use_cases.py`,
`./src/gateway/proxy/application/governance.py`, `./src/gateway/proxy/api/deps.py`,
`./src/gateway/proxy/api/images_deps.py`, `./src/gateway/proxy/api/embeddings_deps.py`,
`./src/gateway/proxy/api/audio_deps.py`, `./src/gateway/memory/api/router.py`,
`./src/gateway/proxy/api/realtime_relay_ws.py`, `./src/gateway/proxy/api/realtime_ws.py`,
`./src/gateway/keys/domain/ports.py`, `./src/gateway/keys/infrastructure/repository.py`,
`./src/gateway/keys/application/use_cases.py`, `./src/gateway/usage/application/rate_card_resolver.py`,
`./src/gateway/usage/application/recorder.py`, `./src/gateway/usage/application/flusher.py`,
`./src/gateway/usage/application/cost_recovery.py`, `./src/gateway/usage/infrastructure/orm.py`,
`./src/gateway/catalog/infrastructure/repository.py`, `./src/gateway/catalog/domain/ports.py`,
`./src/gateway/catalog/application/use_cases.py`, `./src/gateway/tenants/infrastructure/tier_markup_orm.py` (new),
`./src/gateway/tenants/api/service_tier_router.py` (new), `./src/gateway/tenants/api/platform_service_tier_router.py` (new),
`./src/gateway/core/config.py`, `./src/gateway/core/error_catalog.py`, `./src/gateway/main.py`,
`./migrations/versions/4583689a7b8b_service_tiers.py` (new)

Strategy (ordered batches):
1. Domain + Redis infra: `ServiceTier`/`TierHold` entities, `PassthroughTierCapacityGuard`/`RedisTierCapacityGuard`
   (register_script-once, prune-then-count-then-admit Lua, mirrors `RedisLuaRateLimiter`).
2. Migration: `api_keys.tier`, `tenants.default_tier`, `tenant_priority_markup_overrides`,
   `usage_records.tier_served`/`tier_capacity_degraded` — additive-only, CHECK constraints.
3. Config: `tier_capacity_cluster_cap`/`tier_priority_reserved_pct`/`tier_standard_reserved_pct`/
   `tier_capacity_hold_ttl_s` fields + range/sum validators + the REQUIRED startup warning.
4. Dual-copy governance wiring: `CompletionUseCase._enforce_governance` AND
   `NonChatGovernance.authorize` — tier hold BEFORE credit hold, both released on a later
   RPM/TPM/credit rejection, `_tier_hold_ctx`/`_tier_served_ctx` ContextVars mirroring
   `_credit_hold_ctx`'s publish/consume shape across all 9 construction choke points.
5. Billing composition: `resolve_tier_multiplier` (fills region-pricing's reserved 4-arg
   signature) + recorder/flusher/orm/cost_recovery/catalog composition, one shared resolver.
6. Admin surface: tenant-scoped `service_tier_router.py` (KEYS_MANAGE) + superadmin
   `platform_service_tier_router.py` (live `.reconfigure()`, no restart).
7. Tests: one test per §2 scenario across 4 suite files (§4), run full/touched-suite regression.

Persona (required): `backend-architect` — Redis ZSET pool accounting, atomic Lua, idempotent
release, fail-open degradation; domain stance atop SOUL.md, advisory only.
Spawn isolation (default): worktree (`ai-proxy-builds/build-service-tiers`, branch `wt/build-service-tiers`) — dedicated per this dispatch.
Known-problem fixes:
  - HEAL finding 2 precedent (a governance change wired at only ONE of the two choke
    points) → wired BOTH `_enforce_governance` and `NonChatGovernance.authorize` from
    the start, verified via `grep -c tier_capacity_guard=` across all 9 construction sites.
  - Drafted-vs-DECIDED value drift (config default silently shipping a superseded
    number) → caught the `tier_capacity_hold_ttl_s` 300→600 freeze-review amendment by
    re-reading the FULL §1 SPECIFY text, not just the terser §3 symbol table; fixed.
  - Redis outage silently billing a promise it couldn't keep → `tier_capacity_degraded`
    discriminator forces `tier_served="standard"` on ANY fail-open, never priority.
Strategy actually used: as planned (1→7 above), except step 7 (Tests) ran AFTER most of
steps 1-6 had already landed due to a session compaction mid-build — an honest sequencing
deviation from the intended RED-first order, disclosed in full in §4 and the final report,
not silently absorbed. Each test was still independently reasoned about for its RED
failure mode (see §4) before being trusted as meaningful coverage.
Safety rule (feature-specific): admission and release are ALWAYS atomic at the Redis layer
(one Lua script per decision, occupancy always derived live from ZCARD, never a
separately-mutated counter — release of an absent/already-expired member is a structural
no-op, not a distinct "double release" failure mode) — Redis is treated as the single
source of truth for pool occupancy, mirrors `PostgresCreditGuard`'s row-lock-is-truth
discipline but via Lua atomicity instead of a DB transaction.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

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
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-13

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned (1→7 above), except step 7 (Tests) ran AFTER most of steps 1-6 had already landed due to a session compaction mid-build — an honest sequencing deviation from the intended RED-first order, disclosed in full in §4 and the final report, not silently absorbed. Each test was still independently reasoned about for its RED failure mode (see §4) before being trusted as meaningful coverage.
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

