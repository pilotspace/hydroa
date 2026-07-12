# TASK: Priority/standard service tiers — capacity preference, overflow, tier markup

slug: service-tiers · created: 2026-07-12 · stage: production
milestone: residency-service-tiers
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: ground   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
sensitivity: data
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

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
- `apps/gateway/src/gateway/proxy/infrastructure/redis_load_gate.py:RedisDeploymentLoadGate` (30-133) — the closest EXISTING precedent for "in-flight counting wrapped around a request/stream at the APPLICATION layer, not ASGI" (`acquire`/`release`/`in_flight`, Redis INCR/DECR, fail-open on error) — evaluated and NOT reused directly (it is per-DEPLOYMENT routing telemetry, Redis-backed, cross-worker; see §1 framings).
- `apps/gateway/src/gateway/usage/application/recorder.py:RecordingUsageRecorder` (65-674) — `record`/`record_with_outcome`/`_record_internal` stamp discriminator fields (`cost_basis`, `usage_source`, ~line 492/497) into an `event_fields: dict[str,str]` XADD'd to Redis; `cost_basis`/`usage_source` (ORM `Text, nullable=False, server_default=...`) are the exact template for a new `tier_served` column.
- `apps/gateway/src/gateway/proxy/domain/ports.py:UsageRecordExtras` (`TypedDict, total=False`, 33-85) + `recorder.py:RecordingUsageRecorder.supported_extras` (81-96, a `frozenset[str]` class attribute, NOT derived from the TypedDict) — the typed-extras seam (memory's summary location was imprecise; ground truth corrects it: the TypedDict lives in `proxy/domain/ports.py`, not `usage/`). Adding `tier_served` needs: (a) new TypedDict key, (b) added to `supported_extras`, (c) new kwarg threaded through `record`/`record_with_outcome`/`_record_internal`, (d) stamped into `event_fields`. Checked/filtered at `apps/gateway/src/gateway/proxy/application/use_cases.py:363`.
- `apps/gateway/src/gateway/usage/application/flusher.py:insert_usage_row` (58-203) — a THIRD site that must change: it hand-parses the Redis-stream fields dict and builds the raw `INSERT INTO usage_records (...)` text-SQL (columns ~155-169, params ~178-200); an old-event-safe fallback (`_event_field(fields, "tier_served") or "standard"`) is required, mirroring `cost_basis`'s own fallback idiom.
- `apps/gateway/src/gateway/usage/infrastructure/orm.py:UsageRecordRow` (36-126, `__tablename__ = "usage_records"`) — `cost_basis`/`usage_source` columns are the template for `tier_served: Mapped[str] = mapped_column(Text, nullable=False, server_default="standard")`.
- `apps/gateway/src/gateway/usage/application/rate_card_resolver.py:resolve_markup_pct` (FROZEN v1, untouched) and the NEW `resolve_region_multiplier` + the RESERVED `resolve_tier_multiplier(session, tenant_id, model_id, tier) -> Decimal` — region-pricing TASK.md §3 (**Status: FROZEN @ v1**, approved by Tin) reserves this EXACT 4-arg signature for this task to fill; `model_id` is accepted-but-UNUSED in the body (tier markup is not model-specific — a stated scope-cut, not an oversight) — implemented against the frozen signature verbatim, never renegotiated.
- `apps/gateway/src/gateway/usage/application/recorder.py::RecordingUsageRecorder.record` (~line 282 resolves `markup_pct`, ~line 415 applies `region_multiplier`, ~line 430 divides it back out for disconnect-provider-cost) · `cost_recovery.py::OpenRouterCostRecovery` (~161-223) · `catalog/infrastructure/repository.py::CatalogRepository.list_active_models_with_markup` (~87-186, single bulk `multiplier` scalar at ~153) — the THREE multiplication sites region-pricing's M9 already named as the tier-multiplier's future insertion points; this task fills them.
- `apps/gateway/src/gateway/tenants/domain/authz.py:Permission` (StrEnum, 55-80) and `apps/gateway/src/gateway/tenants/api/rate_card_router.py` / `region_pricing_router.py` — the OWNER-only-permission + dedicated-router-file precedent this task's admin surface mirrors (`Permission.KEYS_MANAGE` already gates key create/patch — reused here rather than minting a redundant permission for the key-level `tier` field; a NEW `service-tiers` admin router mirrors `region_pricing_router.py`'s file shape for the tenant-level default-tier / priority-markup routes).

Context (working folder): sibling task `region-pricing` is **Status: FROZEN @ v1** (its `resolve_tier_multiplier` reservation is the load-bearing citation for this task) and `region-catalog-dimension` is **Status: FROZEN @ v1**; `concurrency-load-guard` is **Status: FROZEN @ v1** and shipped (2026-06-23, pre-dates this milestone entirely — NOT part of residency-service-tiers). `.add/milestones/residency-service-tiers/MILESTONE.md` binding rule #4 ("tier is a capacity PREFERENCE... priority admitted ahead... standard never starved... tier actually SERVED is billed") is DIRECTION, not ground truth about HOW — the milestone text says "capacity preference in the concurrency guard" (singular), but the real guard cannot see identity and is frozen; this task corrects that framing (§1 Framings weighed) rather than silently editing a frozen contract.

Honors (patterns / conventions): CONVENTIONS.md additive-migration/rollback discipline (same as region-pricing/region-catalog-dimension); the `check_and_hold`/`release` admission-hold shape (`credits/domain/ports.py:CreditGuard`) — "reserve-then-release, best-effort, never raises on infra failure" (ports.py:15-31); the `_credit_hold_ctx`/`_settle_or_release_hold`/`_dispatch_record` contextvar-plus-task-done-callback idiom for spanning a hold across a possibly-streaming request WITHOUT threading a new parameter through ~25 call sites; the "dual-copy governance, never staggered" discipline (both `CompletionUseCase._enforce_governance` AND `NonChatGovernance.authorize` get the SAME insertion — the credits-ledger build originally MISSED `governance.py` and had to heal it in, "HEAL (finding 2)" comment at governance.py:178 — this task wires both from the start to avoid repeating that trap); typed-extras declared-capability filtering (never `inspect.signature` dispatch); `Numeric(7,4)` percentage-additive columns for anything that composes like `markup_pct` (vs `Numeric(6,4)` raw-multiplier columns like region); PUT-idempotent-upsert / DELETE-always-204 / OWNER-only-via-Permission admin-router precedent (`rate_card_router.py`, `region_pricing_router.py`).

Anchors the contract cites: `keys/domain/entities.py:AuthzResult.tier` (NEW) · `keys/application/use_cases.py:AuthzUseCase.execute` (extension) · `keys/infrastructure/orm.py:ApiKeyRow.tier` (NEW) · `tenants/infrastructure/orm.py:TenantRow.default_tier` (NEW) · `keys/api/schemas.py:CreateKeyRequest.tier` / `PatchKeyRequest.tier` (NEW) · `proxy/domain/tier_capacity.py:TierCapacityGuard` (NEW Protocol) · `proxy/infrastructure/tier_capacity_guard.py:InProcessTierCapacityGuard` / `PassthroughTierCapacityGuard` (NEW) · `proxy/application/use_cases.py:CompletionUseCase._enforce_governance` (extension) · `proxy/application/governance.py:NonChatGovernance.authorize` (extension) · `usage/application/rate_card_resolver.py:resolve_tier_multiplier` (fills the RESERVED frozen signature) · `usage/application/recorder.py::RecordingUsageRecorder.record` (extension, 4th multiplication site correction) · `usage/application/cost_recovery.py::OpenRouterCostRecovery` (extension) · `catalog/infrastructure/repository.py::CatalogRepository.list_active_models_with_markup` (extension, new `tier` param) · `proxy/domain/ports.py:UsageRecordExtras.tier_served` (NEW) · `usage/infrastructure/orm.py:UsageRecordRow.tier_served` (NEW) · `usage/application/flusher.py:insert_usage_row` (extension) · `tenants/api/service_tier_router.py` (NEW).

Issues/Risks (→ feed §1):
1. **The frozen guard cannot see tier — architecturally, not incidentally.** `GlobalBackPressureMiddleware` runs before auth exists in this codebase (no ASGI `AuthenticationMiddleware` anywhere — confirmed by exhaustive search). Any design that tries to make the ASGI guard itself tier-aware would require either (a) editing a FROZEN contract (disallowed) or (b) duplicating auth inside the middleware (a second, drift-prone identity-resolution path, and a new external dependency on the hot ASGI path — which the guard's own frozen contract explicitly rejected for the base cap: "no new external dependency on the hot path"). This is the central finding driving the whole design: tier admission preference must be a SEPARATE, additive mechanism at the governance choke point, not an edit to the guard. See §1 Framings weighed.
2. **Per-worker in-process accounting is a KNOWN, already-accepted approximation in this codebase** (the base guard's own contract states "total cap = workers × per-worker cap (documented)" as an accepted tradeoff, explicitly rejecting a Redis cross-worker counter "for now... adds a per-request INCR/DECR on hot path + fail-open complexity"). A tier-capacity gate faces the identical choice. Mirroring the base guard's choice (in-process, per-worker) is the lower-risk, most-consistent option but means "priority admitted ahead" is a per-WORKER guarantee, not a cluster-wide one — a worker whose priority-reserved pool is briefly full can still shed a priority request even if a DIFFERENT worker has spare capacity. Flagged at §3 freeze (top ⚠) — this is a genuine, consequential tradeoff a human should confirm, not a default I get to silently pick.
3. **Non-blocking, non-queued admission (the base guard's own chosen semantics — "shed-now, no waiter queue") means "priority admitted ahead of standard" cannot be a literal queue-reordering guarantee** — there is no queue to reorder. The only deterministic mechanism compatible with non-blocking semaphores is RESERVED CAPACITY (a floor only one tier may draw from), which is why the milestone's own language is "preference... not a guarantee" (MILESTONE.md shared decision #4) — this task's mechanism (§1) is built to match that literal framing, not to over-promise a hard ordering guarantee it cannot deliver without inventing a real priority queue (out of scope — would change the base guard's shed-now posture, which is frozen).
4. **`tier_served` can legitimately differ from the tier selected on the key** — MILESTONE.md rule #4's own wording ("tier actually served... is what gets billed") is a stronger claim than "served == requested-or-shed"; if served always equaled requested-or-503, the rule would be a trivial echo of the key's own `tier` field, not worth stating as its own binding rule. This task's mechanism (§1 M-items) makes a genuine downgrade path real: a priority request that exhausts BOTH its own reserved capacity AND the shared pool may still be admitted through standard's reserved floor as a last resort, billed and recorded as `tier_served="standard"` — this is the actual "priority→standard overflow" the milestone names, not a synonym for "priority always bills priority."
5. **Cost-recovery must re-resolve `tier_multiplier` FRESH against the CURRENT rate-card state** (not replay a stored multiplier value), mirroring region-pricing's own accepted precedent for `resolve_region_multiplier` in `cost_recovery.py` — but keyed by the ORIGINAL request's `tier_served` (read from the `usage_records` row being recovered), never re-derived from current admission state (admission is a point-in-time capacity fact; billing rate is a point-in-time rate-card fact — conflating them would let a stale recovery silently re-run admission logic that no longer applies).

Related intent: MILESTONE.md shared decision #4 ("tier is a capacity preference, not a guarantee") and the DECIDED seed "+25% priority markup, tenant-overridable" (MILESTONE.md, 2026-07-12 intake); GLOSSARY.md `Markup`/`Cost` (extended by region-pricing's "region multiplier" delta, extended again here by "tier markup"); region-pricing TASK.md §1 assumption #3 (the deliberate multiplicative-region / additive-percentage-tier asymmetry) — this task's storage follows the SAME percentage-additive convention as `markup_pct`, not region's raw-multiplier convention.
Ground SHA: 853afa8

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Priority/standard service tiers — per-tenant/key capacity preference, overflow, and tier-differentiated markup, composed with (not edited into) the frozen global back-pressure guard.

Framings weighed:
  (chosen) A NEW, additive `TierCapacityGuard` admission-hold — mirroring `CreditGuard`'s exact `check_and_hold`/`release` shape and insertion point (immediately after the credit hold, inside `_enforce_governance`/`NonChatGovernance.authorize`, wrapped so a later RPM/TPM/credit rejection reverses it) — backed by an in-process, per-worker partition of THREE `asyncio.BoundedSemaphore` pools (`priority_floor`, `standard_floor`, `shared_pool`) sized as fractions of the SAME `settings.max_concurrent_requests` (K) the frozen guard already tracks (one source of truth for "the cap," never a second independently-configured number). Release spans the whole (possibly-streaming) request via the SAME `_credit_hold_ctx`/`_settle_or_release_hold`/`_dispatch_record` contextvar-plus-task-done-callback mechanism credits already use, extended (not duplicated) to also release the tier hold.
  · Edit `GlobalBackPressureMiddleware` itself to read tier from the request — REJECTED: the middleware runs before auth exists anywhere in this codebase (§0 issue #1), and the guard is a FROZEN v1 contract (boundary: "MUST NOT edit a frozen contract"); making it tier-aware would require either duplicating auth on the ASGI hot path (a second identity-resolution source of drift) or a new external dependency on that path, which the guard's own frozen contract explicitly rejected.
  · A Redis cross-worker capacity gate (mirrors `RedisDeploymentLoadGate`'s INCR/DECR/fail-open shape) — REJECTED for v1: would give more ACCURATE cluster-wide tier fairness than the in-process choice, but (a) the base guard itself already explicitly rejected the equivalent Redis cross-worker design for the SAME reason ("adds a per-request INCR/DECR on hot path + fail-open complexity") and this task should not silently reverse that project-level precedent for a lower-stakes soft-preference feature, (b) it introduces a NEW cross-cutting infra dependency for a feature explicitly scoped as "preference, not guarantee" where a per-worker approximation is an honest, cheaper fit. Named as the natural v2 upgrade if operators report the per-worker approximation is materially unfair (§1 ⚠ #1).
  · A real priority queue (waiters ordered, priority dequeued first) — REJECTED: the frozen base guard's admission semantics are explicitly non-blocking/shed-now ("chosen so saturation sheds immediately rather than building an unbounded waiter queue" — concurrency-load-guard TASK.md §3). A queue at the tier layer would contradict that posture (a shed-now system feeding a queued system is an inconsistent mix) and reintroduce unbounded-waiter memory risk the base guard was specifically designed to avoid.

Must:
<must>
  - M1: `api_keys.tier: Text | NULL` (CHECK IN ('priority','standard') OR NULL) is an OPTIONAL per-key override; `tenants.default_tier: Text NOT NULL DEFAULT 'standard'` (CHECK IN ('priority','standard')) is the tenant-wide fallback. `AuthzUseCase.execute` resolves `AuthzResult.tier = api_keys.tier if not NULL else tenants.default_tier` via the EXISTING LEFT JOIN in `ApiKeyRepository.get_by_id` (zero extra DB reads), stamping `AuthzResult.tier_source: Literal["key","tenant"]` (mirrors `policy_source`).
  - M2: `CreateKeyRequest.tier: Literal["priority","standard"] | None = None` (omit = inherit tenant default at creation time, resolved fresh on every request via M1 — NOT frozen at create time) and `PatchKeyRequest.tier: str | None = None` follow the EXISTING three-state PATCH convention (absent=no-change; present+null=clear the key-level override, reverting to tenant default; present+value=set) — the "tier change mid-flight" capability, wired through `patch_key`/`UpdateKeyUseCase.execute` exactly like `team_id`/`cache_enabled`.
  - M3: A NEW `TierCapacityGuard` Protocol (`check_and_hold(tenant_id, tier, request_id) -> tier_served`; `release(tenant_id, request_id) -> None`) is called from BOTH `CompletionUseCase._enforce_governance` AND `NonChatGovernance.authorize` (dual-copy, §0 Honors), inserted immediately BEFORE the credit hold (capacity is a gateway-wide scarce-resource concern, closer to the base back-pressure guard's job than to per-tenant affordability); a later credit-hold OR RPM/TPM rejection releases the tier hold too (extend the existing `except Exception: ... raise` blocks in both call sites).
  - M4: Default wiring is `PassthroughTierCapacityGuard` (`check_and_hold` always returns the REQUESTED tier unchanged, `release` is a no-op) — mirrors `PassthroughCreditGuard` exactly. Byte-identical to today until an operator wires `InProcessTierCapacityGuard` AND sets non-zero reservation fractions (M6) — matches this codebase's pervasive "new gate defaults to a no-op" convention.
  - M5: `InProcessTierCapacityGuard` partitions `settings.max_concurrent_requests` (K) into THREE per-worker `asyncio.BoundedSemaphore` pools: `priority_floor` (size = round(K × `tier_priority_reserved_pct`), priority-EXCLUSIVE), `standard_floor` (size = round(K × `tier_standard_reserved_pct`), standard-EXCLUSIVE — the starvation bound), `shared_pool` (size = K − both floors, open to both). When K = 0 (base guard disabled), `InProcessTierCapacityGuard` is ALSO a pass-through (no scarcity to prefer against).
  - M6: NEW `Settings` knobs `tier_priority_reserved_pct: float = Field(default=0.0)` (env `GATEWAY_TIER_PRIORITY_RESERVED_PCT`) and `tier_standard_reserved_pct: float = Field(default=0.0)` (env `GATEWAY_TIER_STANDARD_RESERVED_PCT`) — DEFAULT 0/0 means `shared_pool` = 100% of K and priority/standard behave IDENTICALLY (byte-identical admission behavior) until an operator opts in, mirroring the base guard's own K=0-disabled convention. A sum > 1.0, or either value negative, is coerced to 0/0 + a startup WARN (mirrors the base guard's negative-knob-coerced-to-0 precedent) — boot never crashes on a bad knob.
  - M7: Admission order — PRIORITY: try `priority_floor` → else `shared_pool` → else `standard_floor` (the true overflow-into-standard's-own-lane path, LAST resort before shedding) → else shed. STANDARD: try `standard_floor` → else `shared_pool` → else shed (standard NEVER draws from `priority_floor`). `tier_served` = "priority" when admitted via `priority_floor` or `shared_pool`; = "standard" when a priority request is admitted via `standard_floor` (the overflow case) or for any standard-tier admission.
  - M8: SHED ON EXHAUSTION: `check_and_hold` raises `ProblemError(503, "ERR_TIER_CAPACITY_EXHAUSTED")` + `Retry-After` header (mirrors the base guard's `ERR_OVERLOADED` shape exactly, distinct code so callers can tell "gateway globally overloaded" apart from "this tier's reserved capacity is exhausted even though the base guard admitted you") when all applicable pools are exhausted for that tier.
  - M9: The hold spans the WHOLE (possibly-streaming) request via a NEW `_tier_hold_ctx` contextvar, set alongside `_credit_hold_ctx` right after M3's hold succeeds, released by extending the EXISTING `_settle_or_release_hold` task-done-callback (fires once `_dispatch_record`'s Task completes, i.e., after full stream drain) — NOT the ASGI "hold across `await self.app(...)`" pattern, which is unavailable at this layer.
  - M10: `tier_served` (the value M7 resolved, NEVER the requested tier) is stamped onto the usage record via the typed-extras seam: NEW `UsageRecordExtras.tier_served: Literal["priority","standard"]` key, added to `RecordingUsageRecorder.supported_extras`, threaded through `record`/`record_with_outcome`/`_record_internal` into `event_fields`, persisted as `usage_records.tier_served: Text NOT NULL DEFAULT 'standard'` (mirrors `cost_basis`/`usage_source`), with an old-event-safe fallback in `flusher.py::insert_usage_row`.
  - M11: `resolve_tier_multiplier(session, tenant_id, model_id, tier) -> Decimal` fills region-pricing's RESERVED frozen signature in `rate_card_resolver.py` (SAME module, `model_id` accepted-but-unused): `tier != "priority"` → `Decimal("1")` (standard is definitionally the zero-markup baseline, never overridable); else a tenant override in NEW table `tenant_priority_markup_overrides` (tenant_id UNIQUE, `markup_pct Numeric(7,4)`) wins, ELSE the DECIDED seed `Decimal("25")` (MILESTONE.md, +25%) — returns `Decimal("1") + pct/100` so callers compose it by straight multiplication, matching region-pricing's own M9 formula `cost_usd = cost_usd * region_multiplier * tier_multiplier`.
  - M12: The tier multiplier is applied at the SAME 3 sites region-pricing named as its own reserved insertion points, using `tier_served` (never the requested tier — M10's after-the-fact value, resolved ONCE per request alongside `region_multiplier`): (a) `recorder.py::record` — `cost_usd = cost_usd * region_multiplier * tier_multiplier`, and the disconnect-provider-cost back-derivation divides `tier_multiplier` back out too (mirrors region-pricing's own M3 correction); (b) `cost_recovery.py` — `target = cost.total_cost * (1+markup/100) * region_multiplier * tier_multiplier`, resolved FRESH against current rate-card state but keyed by the ORIGINAL row's stored `tier_served` (§0 issue #5); (c) `catalog/infrastructure/repository.py::list_active_models_with_markup` gains a NEW `tier: Literal["priority","standard"] | None = None` parameter — tier is CALLER-specific, not model-row-keyed like region, so this is a single extra scalar query resolved ONCE (not a per-row JOIN), folded into the existing bulk `multiplier` expression uniformly across every row.
  - M13: `PUT /admin/service-tiers/priority-markup` (idempotent upsert), `PUT /admin/service-tiers/default-tier`, `GET /admin/service-tiers` (effective default_tier + priority_markup_pct) — gated by `Permission.KEYS_MANAGE` (reused — tier is key/tenant governance, not a rate-card money-config surface; §1 assumption #2), scoped to the caller's own tenant, mirrors `region_pricing_router.py`'s file shape.
  - M14: Invoice generation and the margin dashboard require ZERO code changes (same free-inheritance guarantee region-pricing already established — both sum `usage_records.cost_usd` without recomputing).
</must>
Reject:
<reject>
  - R1: `tier` value outside {priority, standard, null} on `CreateKeyRequest`/`PatchKeyRequest`/admin routes -> "422 problem+json"
  - R2: negative or non-numeric `markup_pct` on `PUT /admin/service-tiers/priority-markup` -> "422 problem+json" (mirrors `Field(ge=0, max_digits=7, decimal_places=4)`)
  - R3: non-OWNER/non-ADMIN caller on any `/admin/service-tiers/*` route or `PATCH /admin/keys/{id}` tier field -> "ERR_AUTH_FORBIDDEN" (403)
  - R4: both `priority_floor` and `shared_pool` (and, for a priority request, `standard_floor`) exhausted at admission time -> "ERR_TIER_CAPACITY_EXHAUSTED" (503) + Retry-After, app/upstream NEVER invoked, NO usage record written (never billed for a shed request)
  - R5: `tier_priority_reserved_pct + tier_standard_reserved_pct > 1.0`, or either negative, at boot -> coerced to 0/0 + startup WARN, boot succeeds (never crashes)
  - R6: duplicate `PUT /admin/service-tiers/priority-markup` -> NOT an error — idempotent UPSERT (mirrors RC/region-pricing precedent)
</reject>
After:
<after>
  - A tenant/key's resolved tier is known with zero extra DB reads at auth time; changing it takes effect on the VERY NEXT request (never cached/frozen at key-creation time).
  - Under contention (K reached), a priority request is admitted via a strictly wider set of pools than a standard request (priority_floor ∪ shared_pool ∪ standard_floor-as-last-resort vs standard_floor ∪ shared_pool) — a genuine, deterministic structural edge, not a race-dependent one.
  - `standard_floor` is a hard, priority-proof guarantee: a standard request that arrives while `standard_floor` has room is NEVER blocked by priority load (priority only reaches `standard_floor` as its OWN last resort, after both its pools are exhausted).
  - The tier ACTUALLY SERVED (which may be "standard" for an overflowed priority request) — never the tier selected on the key — lands on `usage_records.tier_served` and is the ONLY input to `resolve_tier_multiplier`'s billing composition.
  - The frozen `GlobalBackPressureMiddleware` v1 contract is byte-identical, untouched, and still the outermost safety net (a request must clear ITS cap before the tier gate is ever consulted).
  - Catalog price display (via `list_active_models_with_markup`), `usage_records.cost_usd`, and invoice lines for a priority-served request all reflect the SAME `region_multiplier × tier_multiplier` composition, through the one shared resolver — zero drift, provable the same way region-pricing already proved it.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ #1 In-process, per-worker tier-pool accounting (M5) gives "priority admitted ahead" and "standard never starved" as PER-WORKER guarantees, not cluster-wide ones — a request's fate can depend on which worker the load balancer happened to route it to. This mirrors an ALREADY-ACCEPTED tradeoff in the frozen base guard ("total cap = workers × per-worker cap"), but that guard's job is coarse overload protection (any worker's cap is fine), whereas THIS feature is sold as a differentiated SLA a paying customer notices — the per-worker approximation is weaker for a monetized promise than for a safety net. Lowest confidence because it's a genuine product/engineering tradeoff, not a technical fact I can verify by reading code. If wrong (operators report visible unfairness across workers): a v2 Redis-backed cross-worker gate (mirrors `RedisDeploymentLoadGate`) is the named upgrade path, isolated to `InProcessTierCapacityGuard`'s implementation — the `TierCapacityGuard` Protocol and every call site stay unchanged.
  - [ ] #2 Default reservation fractions are 0/0 (M6, byte-identical until an operator opts in) rather than a nonzero DECIDED seed shipped from day one — chosen for consistency with this codebase's pervasive "new gate defaults to a no-op" convention and because I (the design agent) cannot unilaterally set a business-facing capacity split. If Tin wants priority customers to see a real edge from the FIRST deployment (not after an ops follow-up config change), confirm a nonzero seed (e.g. 0.2/0.1) at freeze instead.
  - [ ] #3 Reusing `Permission.KEYS_MANAGE` (rather than minting a new `SERVICE_TIERS_MANAGE`) for both the key-level `tier` field AND the tenant-level default-tier/priority-markup admin routes — chosen because tier is fundamentally key/tenant GOVERNANCE (who can call how), not a rate-card money-config surface like `RATE_CARDS_MANAGE`. Confirm at freeze; cheap to rename before any caller depends on it.
  - [ ] #4 `tier_served="standard"` for an overflowed priority request is billed at the STANDARD rate (no priority markup) — read directly from MILESTONE.md rule #4's literal wording ("tier actually served... is what's billed"), not independently re-derived. If this reading is wrong (intent was "priority always bills priority, overflow is only about admission-order, never about price"), M12's billing composition needs no structural change — just gate `tier_multiplier` on `tier_requested` instead of `tier_served`; low cost if caught before Build, since M7/M10 (admission + recording) are unaffected either way.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: <short name>   # <Must/Reject item this covers, e.g. M1 or R1>
  Given <starting situation>
  When <action>
  Then <expected result>
  And <what must remain unchanged>   # required for every rejection
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
<METHOD> <path>   body: { <fields> }
  200 -> { <success fields> }
  4xx -> { error: "<code>" | "<code>" }
Schema: <tables/fields touched, and access pattern>
```

Glossary deltas: <new domain term(s) this task introduces, `Term: definition` — or "none">
Status: DRAFT
Reported: <yes — the freeze report (banner/ARC/SHAPE) rendered before this froze | no>
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
