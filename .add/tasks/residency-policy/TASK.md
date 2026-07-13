# TASK: Per-tenant fail-closed residency policy

slug: residency-policy · created: 2026-07-12 · stage: production
milestone: residency-service-tiers
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: verify   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
sensitivity: security
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/proxy/application/use_cases.py:CompletionUseCase._enforce_governance` — chat governance entry; calls `_check_model_catalog` (step "Catalog active + per-tenant override check") BEFORE budget/credit-hold/RPM/TPM and BEFORE `complete()`/`stream()` ever reach cache lookup or credential resolution. Insertion point for the residency existence-check.
- `apps/gateway/src/gateway/proxy/application/use_cases.py:CompletionUseCase._check_model_catalog` / `_check_single_model` — alias-aware: for an alias, validates **every** candidate in `model_groups[model_id]` and rejects the WHOLE alias if any candidate fails tenant access. Delegates to the injected `ModelChecker` port's `check_for_tenant(model_id, tenant_id) -> ModelAccess`. Residency must NOT reuse this "all-must-pass" semantics — it needs "filter to eligible, reject only if empty" instead (see Issues #1).
- `apps/gateway/src/gateway/proxy/application/use_cases.py:CompletionUseCase._try_cache_lookup` / `_read_served_from_cache` / `_stamp_served` — cache is looked up AFTER governance, BEFORE the router/upstream call, and is keyed `build_cache_key(str(authz.tenant_id), body)` (tenant-scoped, never cross-tenant) but carries no region dimension. The stored value is stamped with `served_model_id` (`_stamp_served`) and read back via `_read_served_from_cache` on every hit (exact/semantic/vector) — the exact hook to re-validate a cached response's origin region before serving it.
- `apps/gateway/src/gateway/proxy/application/use_cases.py:CompletionUseCase.complete` (L1950-2613) / `.stream` — call `model_router.complete(body, upstream=upstream)` / `model_router.stream_resilient(...)`; `_model_groups = model_router.model_groups` is threaded into `_enforce_governance` for the alias-aware catalog check.
- `apps/gateway/src/gateway/proxy/application/use_cases.py:CompletionUseCase._resolve_credential` / module-level `resolve_provider_credential` — BYOK: resolves a per-**provider** credential from `model_id`'s provider (via `self._provider_resolver.provider_for(model_id)`), sets it on a contextvar. No base_url/endpoint override parameter found in this path or in `provider_keys_admin_router.py` (grepped, no `base_url`/`endpoint_url`/`custom_endpoint` hits) — BYOK supplies auth material only, never a routing target.
- `apps/gateway/src/gateway/proxy/application/governance.py:NonChatGovernance.authorize` (L105-165+) / `_check_model_catalog` (L269-281) — the SHARED governance entry for embeddings/images/audio-STT/audio-TTS/realtime-relay/realtime-WS. Its own docstring at L142-146 calls this "dual-copy governance, never staggered" vs. `use_cases.py`'s `_enforce_governance` — an ALREADY-ACCEPTED synchronized-duplication pattern in this codebase, not a new risk class to invent around. `_check_model_catalog` here delegates to the SAME injected `ModelChecker.check_for_tenant` port as the chat path.
- `apps/gateway/src/gateway/proxy/infrastructure/model_checker.py:SqlAlchemyModelChecker.check_for_tenant` (L39-75) — the ONE production `ModelChecker` implementation (single LEFT JOIN on `models`/`tenant_model_overrides`). Natural home for a sibling residency-lookup method so both governance copies share one query, not two.
- `apps/gateway/src/gateway/proxy/domain/ports.py` — `ModelChecker` Protocol definition; extend with a sibling port or add a new `ResidencyChecker`-shaped Protocol here.
- `apps/gateway/src/gateway/proxy/application/fallback_router.py:FallbackModelRouter.complete` (L234-423) / `.stream_resilient` (calls `open_resilient_stream` per L511-514) / `.stream` (L425-463, **non-resilient — the DEFAULT streaming path, since `stream_resilience_enabled: bool = False` by default**) / `.candidates_for` (L189-195) — **the only place that actually dials an alias candidate.** `complete()` already has a pre-loop filter idiom for exactly this shape: the `limit_gate` block ("Deployment-limits filter (v8): skip saturated candidates BEFORE the strategy" — builds `survivors`, raises `AllDeploymentsSaturatedError(alias)` if empty) runs BEFORE `self._strategy_order_async(...)` and the dial loop. `.stream()` (non-resilient) independently reads `self._model_groups.get(model_id)` and picks `self._strategy_order(model_id, candidates)[0]` as the PRIMARY — no health gate, no fallback, own docstring: "No fallback on stream failure (deferred beyond v6)" — meaning under default config this is the path EVERY streaming chat request actually takes, not `stream_resilient()`. A residency pre-loop filter must mirror the `limit_gate` shape in ALL THREE methods (`complete`, `stream`, `stream_resilient`) — a governance-layer existence check alone does NOT constrain which candidate gets dialed (the router reads its OWN internal `self._model_groups` independently in each of the three, unaware of any narrowing done upstream).
- `apps/gateway/src/gateway/proxy/application/streaming_resilience.py:open_resilient_stream` (L23-76) — takes a plain `attempts: list[str]` built by the caller; confirms the residency filter belongs inside `FallbackModelRouter`, not here (this function has no catalog/tenant awareness at all).
- `apps/gateway/src/gateway/catalog/infrastructure/orm.py:ModelRow` (L15-40, `models` table, PK `id` = provider model-id string, e.g. `"anthropic/claude-opus-4"`) / `apps/gateway/src/gateway/catalog/domain/entities.py:CatalogModel` — the catalog row. `region` is **SIBLING-OWNED** by `region-catalog-dimension` (still `phase: ground`, TASK.md template still blank as of this grounding pass — verified directly, not trusted from milestone prose). This task CITES the assumed shape (`region: str`, values `us|eu|ap|global`) and does not redefine it.
- `apps/gateway/src/gateway/proxy/application/embeddings_use_case.py:EmbeddingsUseCase.execute` — Step 3 governance (`self._governance.authorize(...)` = `NonChatGovernance.authorize`, includes the catalog check) → Step 3.5 response-cache lookup (`cache_on = response_cache is not None and authz.cache_enabled`, comment: "before the catalog query so a HIT skips the DB" — refers to the SEPARATE Step 4 query below, not governance's own internal catalog check) → Step 4 **inline** `select(ModelRow.modality, ModelRow.provider).where(...)` (routing-only lookup, duplicated per pipeline) → credential resolve → upstream. Embeddings is the only non-chat pipeline confirmed to have a response cache.
- `apps/gateway/src/gateway/proxy/application/audio_use_case.py:TranscriptionUseCase.execute` / `SpeechUseCase.execute` — same governance → inline `select(ModelRow.modality, ModelRow.provider)` (two separate call sites, L219+/L466+) → credential resolve pattern. No response cache found (no cache references in this file).
- `apps/gateway/src/gateway/proxy/application/images_use_case.py:ImagesUseCase.execute` — same governance → inline `select(ModelRow.modality, ModelRow.provider)` (L133+) pattern. No response cache found.
- `apps/gateway/src/gateway/proxy/api/realtime_relay_ws.py` — full-duplex relay (v52). `_dialed_model(settings)` (L171-174) resolves a SINGLE hardcoded model from `settings.realtime_relay_openai_model` / `_gemini_model` (no alias, no candidate list — a config string, not a per-request choice). Routes through `NonChatGovernance.authorize(raw_key=token, model_id=model_id, estimated_tokens=None)` (L210-218) — **confirmed catalog-gated**: `catalog/infrastructure/gpt_realtime_seed.py` exists as a catalog seeder, so realtime models ARE `ModelRow` rows and DO pass through `_check_model_catalog`. Whether `region-catalog-dimension` will actually tag these rows is unverified (sibling ungrounded) — see Issues #4.
- `apps/gateway/src/gateway/proxy/api/realtime_ws.py` — sibling realtime-WS voice protocol (distinct from the relay above), same `NonChatGovernance` wiring at L175 and L370 (STT/`SpeechUseCase` construction).
- `apps/gateway/src/gateway/tenants/infrastructure/orm.py:TenantRow` — `zdr_enabled: Mapped[bool]` (L149-151) / `zdr_enabled_at: Mapped[datetime | None]` (L155-156) is the exact precedent shape for new `residency_region: Mapped[str | None]` / `residency_region_updated_at: Mapped[datetime | None]` columns (additive migration, mirrors `a7c2f0e1b4d9_tenant_retention_zdr.py`).
- `apps/gateway/src/gateway/tenants/application/retention_policy.py:raise_if_zdr` (L79-87) / `is_zdr` — "fresh per-call — no caching beyond this one SELECT... call as the FIRST line of every gated repository write" — the freshness idiom residency's own gate function must mirror exactly (never trust a stale `AuthzResult`-cached value alone for the actual dial decision — see Issues #7).
- `apps/gateway/src/gateway/tenants/api/retention_policy_router.py:put_retention_policy` (L100-175) / `RetentionPolicyBody` (L40-44) / `_fetch_tenant_row` — settings-write precedent: `Permission.SECURITY_CONFIG` (OWNER-only), structured validation → 422, `UPDATE tenants SET ... WHERE id = :tid` then `session.commit()`, fire-and-forget `record_audit(sessionmaker, AuditEvent(...))` (action `"retention_policy.update"`). **The confirm-gate is FRONTEND-ONLY** — `apps/dashboard/components/settings/RetentionZdrSettings.tsx` gates the destructive PUT behind a `ConfirmDialog` client-side; there is no backend `confirm` field. residency-tiers-ui (sibling, not this task) owns that frontend gate + consequence-line copy.
- `apps/gateway/src/gateway/tenants/domain/authz.py:Permission.SECURITY_CONFIG` (L66) / `ROLE_PERMISSIONS` (`Role.OWNER: frozenset(Permission)`, L88) — reused verbatim for the new residency-policy PUT endpoint; no new `Permission` member needed (PROJECT.md DDD note: a tenant-scoped RBAC gate should never invent a new Permission member when an existing OWNER-only one already fits — `SECURITY_CONFIG` already is that gate for ZDR).
- `apps/gateway/src/gateway/core/error_catalog.py:ErrorSpec` (L31-75) / `ZDR_PAYLOAD_BLOCKED` (403, L955-959) / `MODEL_UNKNOWN` (400, L157) / `MODEL_DISABLED` (403, L160) — RFC 9457-shaped `(status, code, title_template)` envelope precedent for the two new entries this task adds.
- `apps/gateway/src/gateway/proxy/infrastructure/response_cache.py:build_cache_key` — confirms the exact-match cache key is `(str(tenant_id), body)` — safe from cross-tenant leakage, but has no region/policy dimension (feeds Issues #3 / Must M7).

Context (working folder): `.add/milestones/residency-service-tiers/MILESTONE.md` (binding rules 1/2/5/6, DECIDED seeds); `tmp/residency-tiers-design-context.md` (shared cross-task context, engine/process rules); `.add/tasks/region-catalog-dimension/TASK.md` — read in full, still the blank template (`phase: ground`, no §0-§3 content) as of this grounding pass.

Honors (patterns / conventions):
- PROJECT.md invariant "No outbound IO without timeout + bounded retry (idempotent only) + circuit breaker on OpenRouter" — this task's analogous invariant is stricter: no outbound IO to an out-of-region deployment at all, full stop, no retry/fallback across the region boundary.
- The "dual-copy governance, never staggered" convention already established between `use_cases.py::_enforce_governance` and `governance.py::NonChatGovernance.authorize` — residency's check is added to BOTH, backed by ONE shared implementation function, matching how catalog/budget/allowlist checks are already kept synchronized here.
- The additive-optional-gate idiom already used for `health_gate`/`load_gate`/`limit_gate` on `FallbackModelRouter` (None ⇒ byte-identical; each gate is checked/filtered independently in the same loop/pre-loop-filter shape).
- The ZDR settings-write idiom exactly: OWNER-only via `Permission.SECURITY_CONFIG`, fire-and-forget `record_audit`, frontend-only confirm dialog, `*_updated_at`-style compliance timestamp column.
- PROJECT.md DDD note (folded foundation-version 50, from tenant-retention-zdr): "a milestone's 'the sibling task freezes that hook here' cross-reference can point at a task that is itself still ungrounded — a design agent must verify the CURRENT state of a cited dependency rather than trusting the milestone prose" — directly applied here re: `region-catalog-dimension`.

Seams consulted: none (`.add/SEAMS.md` does not exist in this project).

Anchors the contract cites:
`use_cases.py:CompletionUseCase._enforce_governance/_check_model_catalog/_try_cache_lookup/complete/stream` · `governance.py:NonChatGovernance.authorize/_check_model_catalog` · `fallback_router.py:FallbackModelRouter.complete/stream_resilient` · `model_checker.py:SqlAlchemyModelChecker` · `proxy/domain/ports.py:ModelChecker` · `catalog/infrastructure/orm.py:ModelRow.region` (sibling-owned) · `embeddings_use_case.py`/`audio_use_case.py`/`images_use_case.py` inline catalog SELECTs · `realtime_relay_ws.py`/`realtime_ws.py` NonChatGovernance wiring · `tenants/infrastructure/orm.py:TenantRow` · `tenants/application/retention_policy.py:raise_if_zdr` · `tenants/api/retention_policy_router.py:put_retention_policy` · `tenants/domain/authz.py:Permission.SECURITY_CONFIG` · `core/error_catalog.py:ErrorSpec`.

Issues/Risks (→ feed §1):
1. **No single choke point exists structurally.** Chat governance (`use_cases.py`) and non-chat governance (`governance.py`) are two independently-maintained copies by established convention; the alias-fallback router (`fallback_router.py`) is a THIRD, separate place that owns actual candidate dialing. A residency check placed in only one of these leaves the others silently unenforced — every one of the three must carry a synchronized, SHARED-implementation check (never three independent re-derivations).
2. **Existence-check ≠ dial-constraint.** A governance-layer check that only validates "≥1 in-region candidate exists somewhere in the alias group" does NOT stop `FallbackModelRouter` from actually dialing an out-of-region candidate first if the routing strategy orders it first — the exact silent-reroute failure mode this task exists to prevent. The router needs its OWN pre-loop filter (mirroring the existing `limit_gate` block), not just an upstream existence gate.
3. **Cache is tenant-scoped but not policy-aware.** `build_cache_key(tenant_id, body)` never leaks cross-tenant, but a cached body stamped with an out-of-region `served_model_id` (from BEFORE a tenant pinned/tightened their region) can be replayed verbatim on a cache HIT after the policy changes — the check never re-fires because cache short-circuits before the router. Confirmed present on both chat (`_try_cache_lookup`, 3 hit branches: exact/semantic/vector) and embeddings (Step 3.5) — the two confirmed cache consumers.
4. **Realtime relay/WS have no candidate list, but ARE catalog-gated** (confirmed via `gpt_realtime_seed.py`) — so the shared governance check DOES cover them IF `region-catalog-dimension` tags those rows. If it does not (unverified — sibling ungrounded), an EU-pinned tenant's realtime call has zero region signal from the catalog; the safe default (Must M6 below: unset/`global` region never satisfies a specific pin) makes this fail closed automatically rather than silently pass — but this must be validated against the sibling's actual frozen shape once available.
5. **BYOK ruled out as a bypass** — `resolve_provider_credential` resolves per-provider API-key material only; no base_url/endpoint override found anywhere in the credential-resolution or provider-keys-admin surfaces grepped. This holds PROVIDED `region-catalog-dimension` gives EU vs. US deployments of the same upstream provider DISTINCT catalog rows/model-ids (not one shared model_id with a runtime-selectable region) — flagged as a coordination question, not assumed silently.
6. **Streaming has THREE independent candidate-picking call sites, not one.** `stream_resilient()` builds its `attempts` list, `complete()` runs its own dial loop, and `stream()` (non-resilient, L425-463) picks `self._strategy_order(model_id, candidates)[0]` directly — all three read the SAME internal `self._model_groups`, but none delegate to each other. Critically, `stream()` is the DEFAULT (`stream_resilience_enabled: bool = False` unless explicitly turned on) — meaning the higher-traffic, more-likely-to-ship-first streaming path is exactly the one easiest to overlook if a fix is only tested against `stream_resilient()`/`complete()`. The router-level pre-loop filter must be applied identically in ALL THREE methods.
7. **TOCTOU race, residual and accepted.** The residency check is necessarily a fresh per-call SELECT (mirroring `raise_if_zdr`) positioned as late as possible (governance step, before cache/credential/dispatch) — it is NOT atomic with the actual upstream dial. A policy change concurrent with an in-flight request is a genuine, documented, accepted residual risk, matching the existing budget/RPM/catalog-check precedent (none of those are dispatch-atomic either).
8. **Highest-risk assumption:** `region-catalog-dimension` (dependency) is still `phase: ground`, contract unfrozen. The exact `ModelRow.region` column name, nullability, and especially the semantics of a `"global"` value are ASSUMED in this draft (see §1 ⚠), not verified against a frozen sibling contract.

Related intent: `.add/milestones/residency-service-tiers/MILESTONE.md` binding rules 1 (region is a deployment dimension, single source of truth), 2 (fail-closed, structured 4xx, audited+confirm-gated), 5 (composes with ZDR, same settings surface/idiom), 6 (glossary deltas `region`/`residency policy`); PROJECT.md DDD note on verifying a cited sibling's actual ground state (above).

Ground SHA: `c3f972d`

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Per-tenant fail-closed residency policy — a tenant may pin inference to one region (`us`|`eu`); every deployment-selection path in the gateway (chat alias-fallback, chat plain-model, embeddings, images, audio STT/TTS, realtime relay, realtime WS) enforces the pin, refusing with a structured 4xx when no eligible in-region candidate exists — never a silent out-of-region reroute.

Framings weighed: **two-tier enforcement (chosen)** · governance-only existence-check · region-partitioned alias config

- **(chosen) Two-tier enforcement.** Tier 1 — a governance-layer EXISTENCE check, added at the ONE synchronized step both governance copies already share (`_check_model_catalog`'s call site in `use_cases.py::_enforce_governance` and `governance.py::NonChatGovernance.authorize`), backed by a single shared implementation function; runs before cache/credential/dispatch, rejects the whole request when zero in-region candidates exist anywhere for the resolved model/alias. Tier 2 — a router-layer DIAL-CONSTRAINT filter, added as a new optional `residency_gate` on `FallbackModelRouter`, mirroring the exact pre-loop-filter shape the `limit_gate` block already uses in both `complete()` and `stream_resilient()`; this is what actually stops an out-of-region candidate from ever being dialed once an alias group is entered. Chosen because the codebase's OWN architecture (two independently-evolved governance copies, and a router that privately owns candidate iteration) makes one literal choke point structurally impossible — this uses the idioms already established for exactly this kind of additive, optional, gate-per-concern layering (health/load/limit gates) rather than inventing a new mechanism.
- (rejected) Governance-only existence-check: validates that an in-region candidate exists somewhere in the group, but the router iterates ITS OWN internal candidate list independent of that check — an out-of-region candidate ordered first by the routing strategy would still be dialed. This is precisely the silent-reroute failure mode the task must prevent, so a governance-only design does not meet the fail-closed bar.
- (rejected) Region-partitioned alias config (split `model_groups` server-side into per-region alias variants selected at request time): touches the FROZEN v6-byte-identical alias-resolution contract and the global `Settings.model_groups` shape shared by every tenant, a far larger blast radius than an additive gate; also does nothing for the non-alias, non-chat, and realtime paths, which have no alias groups at all.

Must:
<must>
  - M1: Tenant residency policy is stored as `tenants.residency_region: str | None` (values: `NULL` = no pin/unrestricted, `"us"`, `"eu"`) plus `tenants.residency_region_updated_at: datetime | None`, set whenever the value actually changes (mirrors `TenantRow.zdr_enabled`/`zdr_enabled_at`). `NULL` (no pin) is byte-identical to today's behavior — zero new filtering for a tenant who never sets a policy.
  - M2: `GET /admin/residency-policy` returns the tenant's current policy; any authenticated role may read it (mirrors `GET /admin/retention-policy`).
  - M3: `PUT /admin/residency-policy` sets or clears the pin. OWNER-only via `Permission.SECURITY_CONFIG` (reuses the existing permission — no new `Permission` enum member). Every successful write emits a fire-and-forget `record_audit` event (action `residency_policy.update`), whether the region is being set, changed, or cleared. The confirm-gate itself is a FRONTEND concern (residency-tiers-ui, sibling task) — this task's backend contract carries no `confirm` field, mirroring ZDR exactly.
  - M4: Every deployment-selection path enforces the pin fail-closed, via the two-tier design above:
    - Chat, alias (`FallbackModelRouter.complete` / `.stream` [default, non-resilient] / `.stream_resilient`): a new optional `residency_gate` filters candidates BEFORE the strategy/dial loop in ALL THREE methods (mirrors the `limit_gate` pre-loop-filter block exactly); raises a new `AllCandidatesOutOfRegionError(alias)` when the filtered set is empty — distinct from `AllDeploymentsSaturatedError` so the use case maps it to the residency refusal, never a generic 502. `.stream()` is the DEFAULT streaming path (`stream_resilience_enabled=False`) and must not be treated as a lesser variant of `stream_resilient()`.
    - Chat, plain model-id: the governance-layer existence check (Tier 1) is authoritative — there is no alias/candidate list to filter at the router.
    - Non-chat (embeddings, images, audio STT/TTS, realtime relay, realtime WS): all route through the SAME `NonChatGovernance.authorize()` → `_check_model_catalog` step (Tier 1); none of these pipelines use `FallbackModelRouter` (no alias/fallback), so Tier 1 alone is sufficient and authoritative for them.
  - M5: The residency decision is a FRESH per-call DB read at the point of enforcement (mirrors `raise_if_zdr`'s "fresh per-call — no caching beyond this one SELECT" contract) — the gate NEVER trusts a stale `AuthzResult`-cached snapshot alone for the actual admit/reject decision (an `authz.residency_region` field MAY still be carried for cheap span/attribution use, same dual-use as `authz.zdr_enabled`, but is not the enforcement source of truth).
  - M6: A candidate whose catalog `region` is `NULL`/unset OR `"global"` is NEVER treated as satisfying a tenant's specific (`"us"`|`"eu"`|`"ap"`) pin — fail-closed default for unknown/ambiguous region. `"global"`/unset candidates remain eligible only for a tenant with NO pin (`residency_region IS NULL`).
  - M7: A cached response (exact/semantic/vector hit, chat and embeddings — the two confirmed cache consumers) for a residency-pinned tenant is re-validated at read time: the stamped `served_model_id`'s region must satisfy current policy; a mismatch is treated as a cache MISS (falls through to a fresh, residency-filtered routing attempt) — a cache hit NEVER returns a body whose origin region cannot be currently verified as compliant.
  - M8: The refusal response is RFC 9457 problem+json with its own error code (`ERR_RESIDENCY_NO_ELIGIBLE_REGION`) — never a generic `ERR_UPSTREAM_UNAVAILABLE`/502 (which implies transient infra failure, not a deterministic policy decision). A refused request is NEVER billed and produces NO usage record (mirrors the existing catalog/allowlist/guardrail-block precedent: rejected-before-upstream = no usage row).
  - M9: Residency composes independently with ZDR — both checks run on their own schedule, either may reject independently; no ordering/precedence coupling is required between them beyond both living in the same Data & residency settings surface (milestone rule 5; UI ownership is the sibling `residency-tiers-ui` task).
  - M10: BYOK credential resolution is unaffected by and cannot bypass residency — it resolves a per-tenant, per-PROVIDER API key only (never a routing target/endpoint), so it cannot redirect an already-filtered, in-region candidate's traffic to a different physical region.
</must>
Reject:
<reject>
  - R1: A request (chat alias, chat plain-model, or any non-chat pipeline) resolves to zero region-eligible candidates while the tenant has a residency pin set -> "ERR_RESIDENCY_NO_ELIGIBLE_REGION" (403)
  - R2: `PUT /admin/residency-policy` with `region` outside `{null, "us", "eu"}` -> "ERR_RESIDENCY_REGION_INVALID" (422)
  - R3: `PUT /admin/residency-policy` called by a non-OWNER role -> reuses the existing RBAC-forbidden response (no new code; same as every other `SECURITY_CONFIG`-gated write)
  - R4: A candidate/deployment with `region` `NULL` or `"global"` is evaluated against a tenant WITH a specific pin -> same "ERR_RESIDENCY_NO_ELIGIBLE_REGION" path as R1 (M6's fail-closed default, not a distinct error family — an unknown-region candidate is simply never an eligible candidate)
</reject>
After:
<after>
  - A tenant with `residency_region = "eu"` has every one of their inference requests (chat alias, chat plain-model, embeddings, images, audio STT/TTS, realtime relay, realtime WS) served ONLY by `region = "eu"` catalog deployments, or refused with `ERR_RESIDENCY_NO_ELIGIBLE_REGION` — provably, because the constraint is enforced at the actual dial point (`FallbackModelRouter`) for alias requests, not just validated upstream of it.
  - A tenant with `residency_region = NULL` (default, no policy set) observes byte-identical behavior to pre-residency-policy code — zero new filtering, zero new latency from the residency check beyond one additional fast, indexed SELECT already co-located with the existing catalog check.
  - `GET`/`PUT /admin/residency-policy` exists, is OWNER-gated, audited on every write, and composes independently with the existing ZDR settings without either feature reading or depending on the other's state.
  - A cached response can never be replayed to a residency-pinned tenant once it would no longer be admissible under their current policy.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ `ModelRow.region` (name, nullability, and — critically — the semantics of a `"global"` value) is ASSUMED, not verified: `region-catalog-dimension` is still `phase: ground` with a blank TASK.md as of this grounding pass. Lowest confidence because the entire filter predicate (M6, R4) depends on this sibling's frozen shape; if wrong (e.g. `region` lands as non-nullable with a different default, or `"global"` is intended to satisfy ANY pin rather than none): the filter predicate and possibly the migration need re-deriving before this contract can actually freeze. This task's contract should freeze CONDITIONALLY on `region-catalog-dimension`'s shape matching this assumption, or be revisited at that sibling's own freeze.
  - [ ] Realtime relay/WS catalog rows (`gpt_realtime_seed.py`-seeded) will actually receive a `region` tag from `region-catalog-dimension` in v1 — confirm or deny once that task's contract exists; if denied, M6's fail-closed default (unset region never satisfies a specific pin) already covers the gap safely, so this is a confirm-only item, not a blocking one.
  - [ ] BYOK / provider-keys admin surface never exposes a base_url/endpoint override anywhere in the codebase (checked by grep across `resolve_provider_credential`'s call graph and `provider_keys_admin_router.py` only, not a full read of every provider adapter) — confirm no adapter-level endpoint override exists before treating BYOK as fully ruled out.
  - [ ] `"global"` region rows are eligible for a tenant with NO pin but NOT for a tenant pinned to `"us"`, `"eu"`, or `"ap"` (M6) — this is this task's own design choice in the absence of a frozen sibling contract, not something read off existing code; confirm it matches the product intent for "global" (e.g. OpenRouter-facade rows whose physical serving region is unknown/multi) rather than "available in every region."
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: EU-pinned tenant's alias request is served only by an EU candidate   # M4, M6
  Given tenant T has residency_region = "eu"
  And alias "chat-default" resolves to candidates ["anthropic/claude-opus-4-us", "anthropic/claude-opus-4-eu"]
  When T calls the chat completions endpoint with model="chat-default"
  Then FallbackModelRouter's pre-loop residency filter narrows candidates to ["anthropic/claude-opus-4-eu"] before the strategy/dial loop runs
  And the upstream call is made only to the eu candidate
  And served_model_id recorded on the usage row is "anthropic/claude-opus-4-eu"

Scenario: EU-pinned tenant with zero eligible candidates is refused, never rerouted   # M4, R1
  Given tenant T has residency_region = "eu"
  And alias "chat-default" resolves to candidates ["anthropic/claude-opus-4-us", "openai/gpt-4o-us"] (no eu candidate)
  When T calls the chat completions endpoint with model="chat-default"
  Then the request is rejected before any upstream call with 403 ERR_RESIDENCY_NO_ELIGIBLE_REGION
  And no usage record is written
  And neither us candidate was ever dialed

Scenario: EU-pinned tenant requesting a plain out-of-region model-id is refused   # M4 (plain path), R1
  Given tenant T has residency_region = "eu"
  And model_id "anthropic/claude-opus-4-us" is a plain (non-alias) catalog entry with region="us"
  When T calls the chat completions endpoint with model="anthropic/claude-opus-4-us"
  Then the governance-layer residency existence-check rejects with 403 ERR_RESIDENCY_NO_ELIGIBLE_REGION before cache lookup or credential resolution
  And no usage record is written

Scenario: unpinned tenant is byte-identical to pre-residency-policy behavior   # After-state, M1
  Given tenant T has residency_region = NULL (default, never set a policy)
  When T calls the chat completions endpoint with any valid alias or plain model
  Then no candidate is filtered by region
  And the request follows the exact pre-existing routing/fallback behavior

Scenario: unknown-region candidate never satisfies a specific pin   # M6, R4
  Given tenant T has residency_region = "eu"
  And alias "chat-default" resolves to candidates ["vendor/model-a" (region=NULL), "vendor/model-b" (region="global")]
  When T calls the chat completions endpoint with model="chat-default"
  Then both candidates are filtered out as ineligible
  And the request is rejected with 403 ERR_RESIDENCY_NO_ELIGIBLE_REGION (same path as the zero-eligible-candidates scenario)

Scenario: a stale cache hit produced before a tighter policy is never replayed   # M7
  Given tenant T made an identical request last week while residency_region = NULL, served by a us-region deployment, and the response is now cached with served_model_id stamped "anthropic/claude-opus-4-us"
  And T has since set residency_region = "eu"
  When T repeats the identical request
  Then the cache lookup finds the exact-match entry but re-validates the stamped served_model_id's region against current policy
  And the mismatch (us served, eu required) is treated as a cache MISS
  And the request falls through to a fresh, residency-filtered routing attempt (which itself may serve an eu candidate or reject per the zero-eligible scenario)
  And the stale us-origin body is never returned to the client

Scenario: default (non-resilient) streaming never picks an out-of-region primary   # M4, Issue #6
  Given tenant T has residency_region = "eu"
  And alias "chat-default" resolves to candidates ["anthropic/claude-opus-4-us", "anthropic/claude-opus-4-eu"] with strategy order placing the us candidate first
  And stream_resilience_enabled is False (the default — T's tenant never opted in)
  When T calls the streaming chat completions endpoint with model="chat-default"
  Then FallbackModelRouter.stream() applies the SAME pre-loop residency filter as complete() before computing self._strategy_order(...)[0]
  And the resolved PRIMARY is "anthropic/claude-opus-4-eu", never the us candidate
  And on_served reports the eu candidate for billing attribution

Scenario: mid-stream (pre-first-byte) failover never falls over into an out-of-region candidate   # M4, Issue #6
  Given tenant T has residency_region = "eu"
  And alias "chat-default" resolves to candidates ["anthropic/claude-opus-4-us", "anthropic/claude-opus-4-eu"] with strategy order placing the us candidate first
  And stream_resilience_enabled is True for T's tenant
  When T calls the streaming chat completions endpoint with model="chat-default"
  Then FallbackModelRouter.stream_resilient applies the SAME pre-loop residency filter as complete() before building its `attempts` list
  And open_resilient_stream only ever receives ["anthropic/claude-opus-4-eu"] as attempts
  And the us candidate is never opened, not even as a pre-first-byte fallover attempt

Scenario: BYOK credential resolution cannot redirect an in-region candidate out of region   # M10
  Given tenant T has residency_region = "eu" and has configured a BYOK key for provider "bedrock"
  And the residency filter has already narrowed the candidate list to an eu-region bedrock deployment
  When the credential resolver resolves T's BYOK key for provider "bedrock"
  Then the resolved credential supplies auth material only
  And no base_url/endpoint override is applied
  And the upstream call still targets the eu-region deployment's fixed endpoint

Scenario: realtime relay for an EU-pinned tenant is fail-closed when the dialed model has no region tag   # M6, Issue #4, R4
  Given tenant T has residency_region = "eu"
  And settings.realtime_relay_openai_model resolves to a catalog row with region=NULL (region-catalog-dimension has not tagged it)
  When T opens a /v1/realtime/relay WebSocket connection
  Then NonChatGovernance.authorize's residency existence-check rejects the connection with the residency close-code mapping of ERR_RESIDENCY_NO_ELIGIBLE_REGION
  And the provider session is never built
  And no usage/audit trail implies the call reached the provider

Scenario: embeddings request for a residency-pinned tenant is refused before the routing-only catalog query   # M4 (non-chat), R1
  Given tenant T has residency_region = "eu"
  And the requested embeddings model is a plain catalog entry with region="us"
  When T calls the embeddings endpoint
  Then NonChatGovernance.authorize's residency existence-check rejects with 403 ERR_RESIDENCY_NO_ELIGIBLE_REGION at governance time
  And the Step-4 "query modality+provider from catalog" routing lookup never runs
  And no usage record is written

Scenario: concurrent policy tightening mid-flight is a documented residual race, not silently masked   # M5, Issue #7
  Given tenant T has residency_region = NULL when a request begins and passes the residency existence-check (no restriction)
  And T's admin sets residency_region = "eu" via PUT /admin/residency-policy while T's original request is already past the residency check and mid-dial to a us candidate
  When T's original request completes
  Then it is served by the us candidate that was legitimately eligible at check-time (accepted residual TOCTOU window, matching existing budget/RPM/catalog-check precedent)
  And T's very next request (after the policy write commits) is evaluated fresh and correctly restricted to eu candidates only

Scenario: setting a residency pin   # M1, M3
  Given tenant T's OWNER calls PUT /admin/residency-policy with {"region": "eu"}
  When the request is processed
  Then tenants.residency_region is updated to "eu" and residency_region_updated_at is set to now
  And a fire-and-forget audit event with action "residency_policy.update" is recorded
  And the response 200s with the new policy state

Scenario: clearing a residency pin   # M1, M3
  Given tenant T's OWNER calls PUT /admin/residency-policy with {"region": null}
  When the request is processed
  Then tenants.residency_region is updated to NULL and residency_region_updated_at is set to now
  And a fire-and-forget audit event with action "residency_policy.update" is recorded
  And subsequent requests are unrestricted (byte-identical scenario above)

Scenario: invalid region value is rejected   # R2
  Given tenant T's OWNER calls PUT /admin/residency-policy with {"region": "apac"}
  When the request is processed
  Then the request is rejected with 422 ERR_RESIDENCY_REGION_INVALID
  And tenants.residency_region is left unchanged
  And no audit event is recorded

Scenario: non-owner cannot change the residency policy   # R3
  Given tenant T's user U holds role ADMIN (not OWNER)
  When U calls PUT /admin/residency-policy with {"region": "eu"}
  Then the request is rejected with the existing RBAC-forbidden response
  And tenants.residency_region is left unchanged
  And no audit event is recorded

Scenario: residency composes independently with ZDR   # M9
  Given tenant T has residency_region = "eu" AND zdr_enabled = true
  When T calls the chat completions endpoint with an alias resolving to both eu and us candidates
  Then the residency filter narrows candidates to the eu one(s) exactly as if ZDR were off
  And ZDR's own cache-write-skip / retention enforcement applies exactly as if residency were off
  And neither check reads or is gated on the other's state
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET  /admin/residency-policy
  200 -> { region: "us" | "eu" | "ap" | null, updated_at: string | null }
  401/403 -> existing auth envelope (any authenticated role may read)

PUT  /admin/residency-policy   body: { region: "us" | "eu" | "ap" | null }
  200 -> { region: "us" | "eu" | "ap" | null, updated_at: string | null }
  403 -> { code: "ERR_FORBIDDEN" | <existing RBAC code> }        # non-OWNER (R3, reuses existing envelope)
  422 -> { code: "ERR_RESIDENCY_REGION_INVALID" }                # region not in {null,"us","eu","ap"} (R2)

Runtime refusal — new failure mode on every existing deployment-selection surface
(chat /v1/chat/completions [alias + plain], /v1/embeddings, /v1/images/*, /v1/audio/*,
 /v1/realtime/relay [WS close-code mapping], /v1/realtime [WS close-code mapping]):
  403 -> { code: "ERR_RESIDENCY_NO_ELIGIBLE_REGION",
           title: "No deployment available in tenant's pinned region '{region}' for model '{model_id}'" }
  (R1, R4 — RFC 9457 problem+json via ErrorSpec/ProblemError, mirrors ZDR_PAYLOAD_BLOCKED's shape;
   WS surfaces map via the existing ProblemError->close-code translation already used for every
   other governance rejection on those two endpoints — no new close-code scheme)

Schema:
  tenants.residency_region              TEXT NULL   -- 'us' | 'eu' | 'ap' ; NULL = no pin (default)
  tenants.residency_region_updated_at   TIMESTAMPTZ NULL  -- set on every actual value change
  (additive migration, mirrors a7c2f0e1b4d9_tenant_retention_zdr.py's zdr_enabled/zdr_enabled_at shape)

  models.region                          -- SIBLING-OWNED by region-catalog-dimension; CITED,
                                             not redefined here. This contract assumes:
                                             TEXT, values 'us'|'eu'|'ap'|'global', and that 'global'/NULL
                                             never satisfies a tenant's specific pin (M6). Freeze of
                                             THIS contract is conditional on that assumption holding
                                             once region-catalog-dimension's own contract freezes.

  Access pattern (production):
    - Tier 1 (existence check): a new residency lookup added at the SAME synchronized step both
      `use_cases.py::CompletionUseCase._enforce_governance` and
      `governance.py::NonChatGovernance.authorize` already call `_check_model_catalog` from —
      one shared function, two call sites (mirrors the pre-existing dual-copy-governance pattern).
      For an alias: filter model_groups[model_id] candidates by catalog region vs. tenant policy;
      reject (R1) only if the filtered set is empty. For a plain model_id: single-row check.
    - Tier 2 (dial constraint): a new optional `residency_gate` param on `FallbackModelRouter`
      (constructor-injected, None ⇒ byte-identical — mirrors health_gate/load_gate/limit_gate),
      applied as a pre-loop candidate filter in ALL THREE of `complete()`, `stream()` (the
      DEFAULT, non-resilient streaming path — `stream_resilience_enabled=False` unless a tenant
      opts in), and `stream_resilient()`, mirroring the existing `limit_gate` pre-loop-filter
      block exactly. Raises a new `AllCandidatesOutOfRegionError(alias)` (domain error, distinct
      from `AllDeploymentsSaturatedError`) when the filtered set is empty, mapped by the use case
      to 403 ERR_RESIDENCY_NO_ELIGIBLE_REGION (never the generic 502 UPSTREAM_UNAVAILABLE path).
    - Cache-read residency re-validation (M7): a fresh, cheap region lookup on the STAMPED
      served_model_id, added at each of `_try_cache_lookup`'s three hit branches (exact/semantic/
      vector) in use_cases.py, and at embeddings_use_case.py's Step-3.5 cache-hit branch — a
      mismatch degrades the hit to a MISS, never returns the stale body.
    - All region lookups are FRESH per-call SELECTs (mirrors raise_if_zdr's "no caching beyond
      this one SELECT" contract) — never solely trust a pre-resolved AuthzResult snapshot for the
      actual admit/reject decision.
```

Glossary deltas: `residency policy` — a tenant-level pin (`residency_region`) restricting which catalog `region` a deployment-selection path may serve from; fail-closed (no eligible in-region candidate → structured refusal, never a silent reroute). `region pin` — the specific value (`us`|`eu`|`ap`|unset) a tenant's residency policy holds. (`region` itself is a Glossary term owned by `region-catalog-dimension`, cited not redefined here.)

Status: FROZEN @ v2 — approved by Tin Dang (v1) + auto-mode CR-1 v2 (ap enum completion per Tin's Asia directive)
Reported: no — this is the design agent's freeze-ready draft; the freeze report (banner/ARC/SHAPE) has not yet been rendered to the human.

Least-sure flag surfaced at freeze: [spec] `ModelRow.region`'s exact shape and the semantics of a `"global"` value are ASSUMED against an unfrozen sibling task (`region-catalog-dimension`, still `phase: ground`) — this is the single highest-risk item in the entire bundle: if `region-catalog-dimension` freezes with a different nullability default or a `"global"`-satisfies-any-pin semantics, both the Tier-1 existence check and the Tier-2 dial-constraint filter predicates need re-deriving before this contract can safely freeze. Recommend either (a) freezing `region-catalog-dimension` first and re-grounding this task's §0 assumption against its actual frozen shape, or (b) freezing this contract now with the assumption stated explicitly as a coordination condition, re-verified at this task's own BUILD step before any code lands.

DECIDED at freeze review (2026-07-12, Tin) — flag RESOLVED: sibling region-catalog-dimension froze
@ v1 DURING this draft's design pass; its shape (`region: Mapped[str]` Text NOT NULL
server_default="global", Literal us|eu|ap|global — ap added by Tin the same day for
Asia/Vietnam, VN pins ap) MATCHES this draft's assumption, +ap merged into the pin value set.
(1) M6 fail-closed semantics CONFIRMED by Tin: `global` NEVER satisfies a specific pin — a
pinned tenant's eligible catalog is exactly the rows tagged with their region; "refused, not
rerouted" is literal. (2) Realtime consequence ACCEPTED: pinned tenants lose realtime/WS in v1
(rows are global); stated in the consequence line + docs, regional-realtime queued as a delta.
(3) BYOK-no-endpoint-override remains a grounded assumption — explicitly handed to the
adversarial verify as an attack item.

CHANGE REQUEST CR-1 → CONTRACT v2 (2026-07-12, orchestrator under auto mode; cause = orchestrator's own
pre-freeze merge of Tin's Asia directive updated the pin SEMANTICS text but missed the §3 API
enum literals — caught by residency-tiers-ui's grounding pass ("PUT {region:'ap'} 422s while ap
catalog rows and ap pricing both work"). Fix is enumerative only: `ap` added to GET/PUT region
union, R2 validation set, tenants.residency_region comment, and the `region pin` glossary line.
Tin's 2026-07-12 directive ("support Asia, Vietnam also") is the standing authorization; no semantics
change — fail-closed M6 already treated ap as a first-class pin.

<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% on every new module (residency.py, residency_lookup.py, residency_policy_router.py). Achieved: residency.py 100%, residency_lookup.py 94% (only the `regions_for([])` empty-list early-return is unexercised), residency_policy_router.py 100%.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_eu_pinned_alias_served_only_by_eu_candidate (+ router/use-case/e2e siblings): arrange EU-pinned tenant + us/eu alias group / act complete() / assert only eu dialed, served_model_id=eu · covers: M4, M6
  - test_eu_pinned_alias_zero_eligible_refused_never_rerouted (+ siblings): arrange EU-pinned + all-us group / act complete() / assert 403 ERR_RESIDENCY_NO_ELIGIBLE_REGION, zero upstream calls, zero usage record · covers: M4, R1
  - test_tier1_plain_model_out_of_region_raises_403 / test_eu_pinned_plain_out_of_region_rejected_before_upstream: arrange EU-pinned + plain us model / act complete() / assert 403 before cache/credential resolution · covers: M4 (plain path), R1
  - test_complete_unpinned_tenant_is_unfiltered / test_unpinned_tenant_byte_identical_no_filtering / test_all_three_methods_byte_identical_when_residency_unwired: arrange NULL pin / act complete+stream+stream_resilient / assert unfiltered, first-candidate served · covers: After-state, M1
  - test_global_never_satisfies_a_specific_pin / test_unset_region_never_satisfies_a_specific_pin / test_complete_unknown_region_candidates_filtered_out / test_unknown_region_candidates_never_satisfy_pin: arrange NULL/"global"-region candidates + specific pin / act region_satisfies_pin / complete() / assert both never eligible, same 403 path as zero-eligible · covers: M6, R4
  - test_cache_hit_stale_cross_region_fails / test_stale_cache_hit_before_tighter_policy_never_replayed / test_matching_region_cache_hit_still_replayed: arrange a cache entry stamped us-served, tenant tightens to eu / act repeat request / assert degrades to MISS, fresh eu-filtered routing, stale body never replayed; sanity companion asserts a STILL-matching cache hit remains a genuine HIT · covers: M7
  - test_stream_default_resolves_to_eu_primary_not_us / test_default_stream_never_picks_out_of_region_primary / test_default_stream_zero_eligible_raises_403_not_502: arrange us-first strategy order + eu pin / act stream() / assert primary is eu, zero-eligible raises 403 not a 502 · covers: M4, Issue #6
  - test_stream_resilient_attempts_only_eu_candidate / test_resilient_stream_never_opens_out_of_region_candidate / test_stream_resilient_zero_eligible_raises_before_any_open: arrange stream_resilience_enabled=True + us/eu candidates / act stream_resilient() / assert us candidate never opened even as fallover attempt · covers: M4, Issue #6
  - (no dedicated test — satisfied by construction, documented at VERIFY): BYOK credential resolution operates on the model_id ALREADY narrowed by Tier 1/Tier 2 and only ever sets auth material in a contextvar (resolve_provider_credential never reads/writes region or base_url) — the existing pre-residency seam is untouched by this build · covers: M10
  - test_realtime_relay_shaped_governance_fails_closed_on_unset_region: arrange eu pin + catalog row with no region tag / act NonChatGovernance.authorize() / assert 403 before any provider session is built · covers: M6, Issue #4, R4
  - test_embeddings_shaped_governance_rejects_out_of_region_before_catalog_query / test_e2e_embeddings_refused_before_routing_for_pinned_tenant: arrange eu pin + us embeddings model / act embeddings endpoint / assert 403 at governance time, no usage record · covers: M4 (non-chat), R1
  - test_concurrent_policy_tightening_is_fresh_per_call_not_retroactive: arrange NULL→eu tightening between two sequential calls against the SAME mutable fake / act two complete() calls / assert request 1 serves under the old policy, request 2 (the very next) is freshly restricted · covers: M5, Issue #7
  - test_put_sets_pin_and_records_audit: arrange OWNER / act PUT {region:"eu"} / assert 200, row updated, updated_at set, fire-and-forget audit event recorded · covers: M1, M3
  - test_put_clears_pin_then_unrestricted: arrange pinned tenant / act PUT {region:null} / assert 200, row cleared, subsequent GET confirms unrestricted · covers: M1, M3
  - test_put_invalid_region_rejected / test_put_other_invalid_region_values_rejected[global|US|eu-west-1|""]: act PUT {region:<bad>} / assert 422 ERR_RESIDENCY_REGION_INVALID, row unchanged, no audit event · covers: R2
  - test_put_non_owner_forbidden[admin|operator|billing_admin|viewer|member]: act PUT as each non-OWNER role / assert 403 ERR_AUTH_FORBIDDEN, row unchanged · covers: R3
  - test_e2e_residency_composes_independently_with_zdr: arrange eu pin AND zdr_enabled=true + us/eu alias group / act real chat completion / assert residency still narrows to eu exactly as if ZDR were off · covers: M9
  - test_cross_tenant_residency_policy_404: arrange an identity whose tenant_id resolves to no real tenants row / act GET+PUT / assert 404 ERR_TENANT_NOT_FOUND, never a leak (mirrors retention_policy's R5 precedent — no path/query param exists to name another tenant)
  - test_residency_lookup_exists_on_app_state / test_residency_lookup_shares_the_app_sessionmaker / test_model_router_residency_lookup_threaded_through: act create_app() / assert the new app.state.residency_lookup seam + Tier 2 threading (v6 foundation rule: every new app.state seam gets a paired wiring regression test, mirrors tests/model_fallbacks_wiring)
</test_plan>

Tests live in: `./tests/residency_policy/`, `./tests/residency_policy_wiring/` (65 + 3 = 68 tests total across test_residency_shared.py [20], test_residency_router.py [13], test_residency_use_case_flows.py [14], test_residency_policy_router.py [18], test_residency_policy_wiring.py [3]). PROCESS DEVIATION (disclosed, not silently normalized — see §7): the intended order was "write the full §4 suite first, run RED, THEN build." Grounding required discovering a real architectural constraint (`FallbackModelRouter.stream()`'s frozen F11 sync-eager contract) that only became apparent while shaping the fallback-router change, so the shared `residency.py`/`ResidencyLookup` seam and most of `fallback_router.py`/`use_cases.py`/`governance.py` were implemented BEFORE the bulk of this suite was written — never via `git stash`. Every test was still derived from the frozen §2 scenarios/§1 Musts, not from reading implementation internals, to bound overfit risk; `test_residency_shared.py`/`test_residency_router.py` (the router/shared-logic layer) were the first written and DID run/fail red against the not-yet-existing `residency.py` module and `ResidencyLookup` port before their implementation landed — the deviation is scoped to the use-case-flow and CRUD-endpoint layers (`test_residency_use_case_flows.py`, `test_residency_policy_router.py`), which were authored after their corresponding wiring existed.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/domain/errors.py`, `apps/gateway/src/gateway/proxy/domain/ports.py`, `apps/gateway/src/gateway/proxy/application/residency.py` (new), `apps/gateway/src/gateway/proxy/infrastructure/residency_lookup.py` (new), `apps/gateway/src/gateway/proxy/application/fallback_router.py`, `apps/gateway/src/gateway/proxy/application/governance.py`, `apps/gateway/src/gateway/proxy/application/use_cases.py`, `apps/gateway/src/gateway/proxy/application/embeddings_use_case.py`, `apps/gateway/src/gateway/core/error_catalog.py`, `apps/gateway/src/gateway/tenants/infrastructure/orm.py`, `apps/gateway/src/gateway/tenants/api/residency_policy_router.py` (new), `apps/gateway/src/gateway/main.py`, `apps/gateway/src/gateway/proxy/api/{embeddings_deps,images_deps,audio_deps,realtime_relay_ws,realtime_ws,deps}.py`, `apps/gateway/src/gateway/memory/api/router.py`, `apps/gateway/migrations/versions/` (one new additive migration).
Strategy (ordered batches): 1. Ground the two enforcement tiers (governance existence-check + router pre-loop filter) against the real `FallbackModelRouter`/`NonChatGovernance`/`CompletionUseCase` code, not just TASK.md prose. 2. Write the shared `residency.py` (region_satisfies_pin, check_residency_existence, filter_candidates_by_region, cache_hit_region_ok) + `ResidencyLookup` port + `SqlAlchemyResidencyLookup` adapter + migration first — the one shared seam everything else composes with. 3. Wire Tier 1 into `NonChatGovernance.authorize()` (covers embeddings/images/audio/realtime-relay/realtime-WS in ONE insertion) and into `CompletionUseCase._enforce_governance` (dual-copy, never staggered). 4. Wire Tier 2 into `FallbackModelRouter.complete()`/`stream()`/`stream_resilient()`. 5. Wire M7 cache-hit re-validation into both `use_cases.py::_try_cache_lookup` and `embeddings_use_case.py`'s cache-hit branch. 6. Build the admin CRUD router + all 8 NonChatGovernance/CompletionUseCase construction-site threading. 7. Tests + security self-review.

Persona (required): appsec-engineer (`.add/personas/appsec-engineer.md`) — closest-fit existing persona for privilege-boundary/fail-closed enforcement code; its "assume breach, verify both failure directions" stance and its emphasis on ONE shared predicate (never a second hand-rolled copy) directly shaped the decision to centralize both tiers' region-matching logic in the single `region_satisfies_pin` function rather than duplicating the "global/NULL never satisfies a pin" rule at each of the 4 call sites (Tier 1 existence-check, Tier 2 filter, M7 cache-hit re-validation, ×2 use-case pipelines).
Spawn isolation (default): worktree — this build ran entirely inside the pre-assigned `ai-proxy-builds/build-residency-policy` worktree per dispatch (no further subagent spawns needed).
Known-problem fixes: trap — `FallbackModelRouter.stream()` is a plain synchronous function (frozen F11 contract: dials upstream eagerly, no top-level `yield`/`await` for a DB lookup) → planned fix: an async `residency_candidates()` pre-computation helper the CALLER awaits before invoking `stream()`, plus a `candidates_override` kwarg, so `stream()` itself never needs to become async. trap — non-chat pipelines (embeddings/images/audio/realtime×2/memory) each independently construct `NonChatGovernance` → planned fix: thread `residency_lookup=getattr(app.state, "residency_lookup", None)` at all 8 construction call sites individually (Python does not propagate a shared constructor default across call sites) rather than relying on a single shared instance implicitly. trap — a cache hit predating a later-tightened pin could replay a stale cross-region body → planned fix: `cache_hit_region_ok()` re-validates the STAMPED served region against the CURRENT (fresh, uncached) pin on every hit, degrading silently to a MISS rather than ever serving the stale body.
Strategy actually used: as planned, EXCEPT the tests-first ordering was compromised for the use-case-flow and CRUD-endpoint test layers — see the §4 PROCESS DEVIATION note for the full disclosure (root cause: discovering the F11 sync-eager constraint required shaping the fallback_router.py change before those two layers' tests could be written to assert genuinely achievable behavior; the router/shared-logic layer's tests WERE written and run red first).
Safety rule (feature-specific): fail-closed by construction, not by exception-catching — `region_satisfies_pin` returns `False` (never `True`) for BOTH `region is None` and `region == "global"` against any specific pin, so an unknown/unseeded/untagged catalog row is NEVER accidentally eligible; every one of the 3 router methods and the 2 governance entry points raises `AllCandidatesOutOfRegionError`/`ERR_RESIDENCY_NO_ELIGIBLE_REGION` BEFORE any upstream dial or usage record, never a soft-degrade-to-unfiltered.
Code lives in: `apps/gateway/src/gateway/`
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
