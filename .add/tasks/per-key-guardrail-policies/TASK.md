# TASK: Per-key guardrail policy resolution (key > tenant)

slug: per-key-guardrail-policies · created: 2026-07-10 · stage: production
milestone: logs-explorer-guardrails-v2
sensitivity: data   <!-- per-key policy CRUD + resolution touches tenant/key data isolation; no new outbound IO seam (reuses the existing auth-time LEFT JOIN) -->
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: tests   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures): <path:symbol — what it is / how it is keyed>
  - `apps/gateway/src/gateway/keys/infrastructure/orm.py:ApiKeyRow` — the `api_keys` ORM row. Already has a NULLABLE JSONB column (`model_allowlist`, same class body) — the exact precedent shape for the new `guardrail_policy` column; no new pattern introduced.
  - `apps/gateway/src/gateway/keys/infrastructure/repository.py:SqlAlchemyKeyRepository.get_by_id` (lines 126-201) — the 3-table LEFT JOIN (`api_keys` → `teams` → `tenants`) that resolves `tenants.guardrail_configs` into `ApiKey.guardrail_configs` TODAY. This is where key>tenant resolution belongs — `ApiKeyRow` is already selected wholesale in the `select(ApiKeyRow, ...)` projection, so adding `guardrail_policy` costs zero extra columns/JOINs/IO.
  - `apps/gateway/src/gateway/keys/domain/entities.py:ApiKey` (field `guardrail_configs`, line 37) and `:AuthzResult` (field `guardrail_configs`, line 97) — both frozen additive dataclasses; the RESOLVED (already-merged) config flows through unchanged in shape — no field rename, no new dataclass member needed on either.
  - `apps/gateway/src/gateway/keys/application/use_cases.py:AuthenticateKeyUseCase.execute` (~line 303) — builds `AuthzResult(..., guardrail_configs=getattr(row, "guardrail_configs", {}), ...)`. Zero change needed once the repository resolves the merged value into `row.guardrail_configs`.
  - `apps/gateway/src/gateway/proxy/application/use_cases.py:1233` — `guardrail_configs = getattr(authz, "guardrail_configs", {}) or {}` (pre-call), re-read UNCHANGED at the 3 cache-hit branches (exact-hit :1336, semantic-hit :1393, vector-hit :1455) and the streaming branch (:1858/:1862). Confirms resolve-once-at-auth already threads through EVERY enforcement point including all cache hits — zero proxy-layer code needs to change for this task's core Musts.
  - `apps/gateway/src/gateway/proxy/infrastructure/guardrail_evaluator.py:RegexGuardrailEvaluator` — evaluator is config-agnostic (`evaluate_pre`/`evaluate_post` just consume whatever `guardrail_configs` dict is handed in); no change needed here either.
  - `apps/gateway/src/gateway/tenants/api/guardrail_router.py` — the FROZEN tenant-level `/admin/guardrails` GET/PUT: `GuardrailConfigRequest`, `GuardrailConfigResponse`, `PromptInjectionConfig`, `PiiMaskConfig`, `CustomPatternItem`, `_validate_custom_patterns` (V1-V7), `_NAME_RE`/`_MAX_CUSTOM_PATTERNS`/`_MAX_PATTERN_BYTES`. This task's per-key endpoints REUSE these models/validators verbatim (import, not duplicate) — identical shape, identical validation.
  - `apps/gateway/src/gateway/keys/api/router.py:patch_key` (~line 269) — sibling key-governance PATCH; shows the 404-on-cross-tenant-or-revoked pattern (`KeyNotFoundError` → `KEY_NOT_FOUND.exc()`) this task's GET/PUT/DELETE mirror. `admin_router.delete("/{key_id}", status_code=204)` (line 464, key REVOKE) is this codebase's own DELETE-with-204 precedent (7 other `status_code=204` DELETE endpoints exist repo-wide — artifacts, conversations, memory, teams, invites, provider-keys, rate-cards).
  - `apps/gateway/src/gateway/keys/api/deps.py:require_owner_or_admin` (~line 74) — checks `Permission.KEYS_MANAGE`; already the SAME gate used by both key-governance writes and the tenant-level guardrails PUT (`guardrail_router.py` imports it too) — no new `Permission` enum member needed (mirrors the PROJECT.md v49 DDD note: an operator-wide/role gate reuses the existing allowlist, never invents a bespoke permission).
  - `apps/gateway/src/gateway/audit/application/audit_writer.py:record_audit` + `apps/gateway/src/gateway/audit/domain/audit_event.py:AuditEvent` — fire-and-forget, fail-open audit seam (own session, separate from the action's transaction). Used by `routing_admin_router.py` (`routing.update`), `provider_keys_admin_router.py` (`provider_key.put`), `oidc_admin_router.py` (`oidc.put`), `budgets/api/router.py` (`budget.update`), `teams/api/router.py` (`member.role_assign`), and `keys/api/router.py` itself (`key.create`/`key.rotate`/`key.revoke`) — this task's PUT/DELETE call it the same way.
  - `apps/gateway/migrations/versions/d4e7f1a2b3c5_guardrails_core.py` — precedent migration (additive `tenants.guardrail_configs JSONB NOT NULL DEFAULT '{}'`). This task's migration mirrors its shape but on `api_keys`, NULLABLE with NO default (NULL is semantically "no override"; `{}` is a deliberate "all guardrails off for this key" override, a DIFFERENT state).
  - `apps/dashboard/components/settings/GuardrailSettings.tsx` — tenant-level Guardrails tab (GET/PUT `/admin/guardrails`; always sends BOTH `prompt_injection` and `pii_mask` on save — confirmed by reading the `mutationFn` body, so the tenant PUT's partial-merge-of-absent-keys feature is never actually exercised by this UI). The shape this task's per-key panel mirrors.
  - `apps/dashboard/components/keys/KeyGovernanceEditor.tsx`, `BandwidthPanel.tsx`, `RatelimitsPanel.tsx` (siblings under `apps/dashboard/components/keys/`, used from `KeysPage.tsx`) — existing per-key settings-panel pattern (PATCH `/api/gw/admin/keys/{key_id}` through the BFF catch-all proxy, no client-side Authorization header) this task's new panel slots into.

Context (working folder): <docs · todos · config · data the task touches — task-delta only>
  - No task-specific docs/config exist yet; `.add/milestones/logs-explorer-guardrails-v2/MILESTONE.md` is the only prior context (read in full).

Honors (patterns / conventions): <PROJECT.md / CONVENTIONS.md anchors — task-delta only, never a re-scan>
  - CONVENTIONS.md clean-architecture layering (`domain/` ← `application/` ← `infrastructure/` ← `api/`, per module) — resolution logic belongs in `keys/infrastructure/repository.py` (infra already does the tenant-merge today), never smuggled into the `api/` layer.
  - CONVENTIONS.md precedent: cross-module DTO/deps reuse is already established (`keys/api/router.py` imports `gateway.teams.api.deps`) — this task reuses `gateway.tenants.api.guardrail_router`'s Pydantic models + `_validate_custom_patterns` rather than duplicating the V1-V7 rules (duplication would let the two rule sets drift).
  - PROJECT.md invariant "every tenant-owned row carries tenant_id; every query is tenant-scoped" — a key row already carries `tenant_id`; the new column inherits that scoping for free, no new cross-tenant surface.
  - PROJECT.md folded pattern (v9/v29/v57: `cache_enabled`/`semantic_cache_enabled`/`batch_grouping_enabled`) — additive column, resolved ONCE at auth time via the existing LEFT JOIN, zero proxy-layer touch, byte-identical default. This task's `guardrail_policy` is the SAME shape, one level deeper (JSONB, not bool).
  - CLAUDE.md "design for failure on every new IO seam" — there is deliberately NO new IO seam (reuses the single existing LEFT JOIN); flagged explicitly as a Must below because a naive design (a second per-request lookup, or a caching layer) WOULD introduce one and require its own timeout/retry/breaker story.

Anchors the contract cites: <the symbols §3 will name>
  ApiKeyRow · SqlAlchemyKeyRepository.get_by_id · ApiKey.guardrail_configs · AuthzResult.guardrail_configs · GuardrailConfigRequest · PromptInjectionConfig · PiiMaskConfig · CustomPatternItem · _validate_custom_patterns · require_owner_or_admin · get_identity · record_audit · AuditEvent · KEY_NOT_FOUND · AUTH_FORBIDDEN · PAYLOAD_CUSTOM_PATTERN_INVALID

Issues/Risks (→ feed §1): <problems · traps · untestable risks found in the real code — task-delta; §1 builds on these>
  - The agent-OAuth token path (`gateway/proxy/infrastructure/composite_key_authenticator.py:88`) builds its own `AuthzResult` with NO `guardrail_configs` kwarg at all (defaults to `{}` — agent tokens get ZERO guardrails today, not even tenant-level). This task's policy lives on `api_keys` rows only (agent tokens are NOT `api_keys` rows) — confirmed OUT OF SCOPE, flagged so the freeze doesn't imply agent-token coverage.
  - The tenant-level `/admin/guardrails` PUT (`guardrail_router.py`, read in full) has NO `record_audit` call today — a pre-existing gap. This task's OWN writes ARE audited (the milestone's "Security floor" shared decision + this task's explicit scope line) but the asymmetry (key-level audited, tenant-level not) is worth flagging, not silently fixed (retrofitting the sibling endpoint is out of this task's scope).
  - `guardrail_configs`/`guardrail_policy` are `dict[str, Any]` with no schema enforced below the Pydantic boundary — reusing the SAME validated request/response models for the new per-key surface (rather than a hand-rolled JSON path) keeps write and read on one validated path, closing the drift risk this would otherwise open.
  - `GuardrailEvent` (`proxy/domain/entities.py`) carries no source discriminator (`guardrail: "prompt_injection"|"pii_mask"` only, no key-vs-tenant tag). The sibling `guardrail-analytics` task (milestone-listed, `depends-on: per-key-guardrail-policies`) will likely want to attribute hit-counts to "key policy" vs "tenant policy" — this task COULD thread a cheap `policy_source` field forward now; ranked as a freeze question below rather than decided unilaterally (widens this task's frozen surface for a sibling task's future need).
  - Validated the milestone's binding shared decision ("a per-key policy overrides the tenant policy WHOLESALE — no field merge") against the real config shape: CONFIRMED correct, no change-request needed. `guardrail_configs` is an opaque `dict[str, "prompt_injection"|"pii_mask" → config]`; there is no natural per-field merge unit finer than the whole dict without inventing one, and every existing additive-config precedent in this codebase (cache/semantic-cache/batch-grouping) resolves as a single flat value, never merges. Wholesale-override is the only shape consistent with the rest of the module.

Related intent: <PROJECT.md § · GLOSSARY term(s) · originating request/milestone rationale — the WHY; task-delta>
  - MILESTONE.md "Shared decisions": "Guardrail policy resolution order: key > tenant > default-off — a per-key policy overrides the tenant policy wholesale (no field merge — design decides and freezes this)" [validated above — CONFIRMED, no change-request] · "Security floor: ... ML moderation egress is a new outbound IO seam (timeout + retry + breaker per CLAUDE.md)" — this task introduces NO new outbound IO seam, so that clause does not apply here (noted so the freeze doesn't ask for a breaker that has nothing to guard).
  - MILESTONE.md Exit criterion: "A key with its own guardrail policy enforces it (overriding the tenant policy); a key without one inherits the tenant policy" (← this task).
  - GLOSSARY.md today defines "Guardrail" as tenant-scoped only ("a per-tenant pre/post-call content control ... configured via `tenants.guardrail_configs`") — GLOSSARY delta required (below).

Ground SHA: <`git rev-parse --short HEAD` at ground time — cite symbols, not bare line numbers; any line ref is "as of" this commit>
  2071046 (branch `chore/add-housekeeping-clusters`)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Per-key guardrail policy CRUD + resolution (key > tenant > default-off)
Framings weighed: **A — additive nullable JSONB column on `api_keys`, resolved once at auth time inside the existing `SqlAlchemyKeyRepository.get_by_id()` LEFT JOIN** (chosen) · B — a dedicated relational `key_guardrail_policies` table keyed by `key_id` (rejected: adds a 4th JOIN to the already-3-table hot auth-path query for no benefit — the milestone's own wholesale-override decision means there is no per-field relational structure to model; `api_keys.model_allowlist` is already a nullable-JSONB column on this SAME table, so JSONB is the established local precedent, not a new one) · C — resolve key-vs-tenant precedence at evaluation time inside `guardrail_evaluator.py`/`proxy/application/use_cases.py`, passing both dicts down (rejected: would touch the evaluator plus 6 call sites — pre-call, evaluate_post ×3 cache-hit branches, the streaming branch — breaking the "resolve-once-at-auth into one flat `AuthzResult` field" invariant every other additive config field already follows; A gets identical behavior with ZERO proxy-layer edits).
Must:
<must>
  - M1: A key with a non-NULL `guardrail_policy` (including an explicit `{}`) uses that value WHOLESALE as its effective `guardrail_configs` — the tenant's `guardrail_configs` is NOT read/merged at all when the key has an override (no per-field merge, per the milestone's binding decision).
  - M2: A key with NULL `guardrail_policy` (never configured, or reverted via DELETE) inherits the tenant's `guardrail_configs` BYTE-IDENTICALLY to pre-task behavior — same dict reaches `evaluate_pre`/`evaluate_post`, same masking/blocking/audit outcomes, with ZERO code changes to `proxy/application/use_cases.py` or `guardrail_evaluator.py`.
  - M3: Resolution happens EXACTLY ONCE, at key-authentication time, inside `SqlAlchemyKeyRepository.get_by_id()`'s existing 3-table LEFT JOIN — no new DB read, no new outbound IO seam, no cache layer (mirrors `cache_enabled`/`semantic_cache_enabled`/`batch_grouping_enabled`'s resolve-once-at-auth precedent exactly).
  - M4: `GET /admin/keys/{key_id}/guardrails` returns the key's CURRENT EFFECTIVE config plus a `source: "key" | "tenant"` discriminator, so the dashboard can render "inheriting tenant policy" vs "custom policy set" from one call.
  - M5: `PUT /admin/keys/{key_id}/guardrails` sets/replaces the key's override. Reuses `GuardrailConfigRequest`/`PromptInjectionConfig`/`PiiMaskConfig`/`CustomPatternItem` and the V1-V7 custom-pattern validation VERBATIM (imported, not duplicated) — identical shape and rules to the tenant-level PUT, including its top-level partial-merge-within-the-key's-own-override semantics (absent key = preserve that guardrail's existing key-level value; present key = replace; explicit null = remove that one guardrail from the key override).
  - M6: `DELETE /admin/keys/{key_id}/guardrails` clears the override (`guardrail_policy` → NULL), reverting the key to tenant inheritance. Idempotent: 204 whether or not an override existed.
  - M7: PUT/DELETE require owner or admin role (`require_owner_or_admin`, i.e. `Permission.KEYS_MANAGE`) — mirrors both the tenant guardrails PUT and every other key-governance write (`patch_key`, rotate, revoke). GET is open to any authenticated tenant role — mirrors the tenant-level `GET /admin/guardrails` precedent (member can view, not edit).
  - M8: Cross-tenant, unknown, or REVOKED key_id on GET/PUT/DELETE → 404 `ERR_KEY_NOT_FOUND` (no existence leak) — mirrors `patch_key`'s existing "active (non-revoked) key owned by tenant_id" framing exactly.
  - M9: Every PUT/DELETE emits a fire-and-forget, fail-open `record_audit()` (action `key_guardrail_policy.put` / `key_guardrail_policy.delete`, target_type `api_key`, metadata = {key_id, which guardrails are enabled+mode} — NEVER pattern regex text or message content) — closes the milestone's "Security floor" + "audit trail for policy changes" scope line; this task's OWN writes are audited even though the sibling tenant-level PUT is not (§0 Issues/Risks).
  - M10: Enforcement on ALL THREE cache-hit paths (exact-match, semantic-cache, vector-cache) plus the streaming path is automatically correct with ZERO code changes to `proxy/application/use_cases.py`, because every one of those branches already reads the SAME `guardrail_configs` local variable resolved once from `authz.guardrail_configs` before any cache-lookup branch runs (confirmed at `:1233`, reused at `:1336`, `:1393`, `:1455`, `:1858`).
</must>
Reject:
<reject>
  - PUT body fails the SAME V1-V7 custom-pattern validation the tenant PUT already enforces (>8 patterns, bad name format, pattern >256 bytes, invalid regex syntax, pattern matches empty string, backreferences, nested quantifiers) -> "ERR_PAYLOAD_INVALID" (422; reuses `_validate_custom_patterns` verbatim — R1)
  - PUT body with an invalid `mode` (prompt_injection mode="mask", or pii_mask mode="block") -> "ERR_PAYLOAD_INVALID" (422; reuses `PromptInjectionConfig`/`PiiMaskConfig`'s existing field_validator unchanged — R2)
  - PUT/DELETE attempted by a member (or any role lacking `Permission.KEYS_MANAGE`) -> "ERR_AUTH_FORBIDDEN" (403 — R3)
  - GET/PUT/DELETE on a key_id belonging to another tenant, an unknown key_id, or a revoked key -> "ERR_KEY_NOT_FOUND" (404, identical for all three — no existence leak — R4)
</reject>
After:
<after>
  - `api_keys.guardrail_policy` holds the key's explicit override (nullable JSONB); proxy guardrail enforcement (pre-call block/audit, post-call mask, all 3 cache-hit paths, streaming) is driven by the RESOLVED value with zero proxy-layer code change.
  - A key without an override is verified byte-identical to pre-task behavior (identical `evaluate_pre`/`evaluate_post` inputs, identical events, identical masking/blocking outcomes as today).
  - Every per-key policy write (PUT or DELETE) has a corresponding `audit_events` row.
  - The dashboard Keys page can show, edit, and clear a per-key guardrail override from the same panel family as budget/rate-limit governance.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ #1 PUT's partial-merge-WITHIN-the-key's-own-override semantics (mirroring the tenant PUT: absent top-level key preserves, present replaces, null removes) vs. a simpler FULL-REPLACE PUT (always overwrite the entire key override) — lowest confidence because it's genuinely a coin-flip: the dashboard's own `GuardrailSettings.tsx` precedent always sends BOTH fields on save (confirmed by reading the mutation body), so the partial-merge feature is never exercised by any known caller today, on EITHER endpoint. If wrong: a future programmatic API caller relying on partial-merge silently clobbers a sibling guardrail it didn't intend to touch (or, if full-replace was expected but partial-merge shipped, a caller's "clear pii_mask only" PUT unexpectedly preserves a stale prompt_injection value) — a spec-conformance bug, not a security one, cheap to fix post-freeze via a contract amendment. RECOMMEND: mirror the tenant endpoint (partial-merge) for API symmetry, since it costs nothing extra to implement (same reused code path) and keeps the two `/admin/guardrails` surfaces behaviorally twinned.
  - [ ] #2 DELETE as the sole revert-to-inherit mechanism (vs. a `PUT {"inherit": true}` sentinel field, or accepting a PUT with an explicitly-empty body meaning "clear") — medium confidence. RECOMMEND: DELETE — REST-idiomatic, matches 7+ existing `status_code=204` DELETE precedents in this codebase, needs no new sentinel field to define/validate/document.
  - [ ] #3 Whether to add a `policy_source: "key" | "tenant" | "none"` field to `AuthzResult` NOW (cheap, additive, mirrors every other additive field on that dataclass) so the sibling `guardrail-analytics` task (depends-on this task) can attribute hit-counts to key vs. tenant policy without re-deriving the comparison itself — vs. leaving that entirely to the analytics task to add when it lands. RECOMMEND: add it now (low cost, avoids a second read/compare in the analytics task, and `AuthzResult` is exactly where every other "resolved-once" governance fact already lives) — flagged as a freeze question since it modestly widens this task's own frozen surface for a sibling task's benefit.
  - [ ] #4 Exact GET response envelope naming (`source` field name/values) — low-stakes, cosmetic; default to `"key"`/`"tenant"` unless the freeze prefers different literals.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: key with an explicit non-empty override enforces it, tenant ignored   # M1
  Given a tenant with prompt_injection {enabled:true, mode:block} and a key with its own
    guardrail_policy {pii_mask: {enabled:true, mode:mask}} (no prompt_injection block set)
  When a completion request through that key contains a prompt-injection pattern
  Then the request is NOT blocked (the key's override has no prompt_injection entry at all —
    the tenant's block-mode prompt_injection config is never consulted)
  And PII in the request is masked per the key's own pii_mask config

Scenario: key override explicitly empty ({}) disables all guardrails for that key   # M1 edge
  Given a tenant with pii_mask {enabled:true, mode:mask} and a key whose guardrail_policy
    was PUT as {} (both blocks explicitly absent)
  When a completion request through that key contains PII
  Then the PII is NOT masked (the key's wholesale-empty override wins; tenant is not consulted)
  And this is DISTINCT from a key with guardrail_policy = NULL (see next scenario)

Scenario: key without an override inherits the tenant policy byte-identically   # M2
  Given a tenant with pii_mask {enabled:true, mode:mask} and a key with guardrail_policy = NULL
  When a completion request through that key contains PII
  Then the response matches EXACTLY what pre-task tenant-only resolution would have produced
    (same masked content, same GuardrailEvent list, same audit/metric emission)

Scenario: resolution costs zero extra IO   # M3
  Given a key with a non-NULL guardrail_policy
  When SqlAlchemyKeyRepository.get_by_id() authenticates that key
  Then exactly one SQL query executes (the existing 3-table LEFT JOIN) — no second query for
    the key's guardrail policy, no cache read/write

Scenario: GET reports source=key when an override exists   # M4
  Given a key with a non-NULL guardrail_policy {prompt_injection: {enabled:true, mode:audit}}
  When an owner calls GET /admin/keys/{key_id}/guardrails
  Then 200 is returned with prompt_injection matching the override and source="key"

Scenario: GET reports source=tenant when no override exists   # M4
  Given a key with guardrail_policy = NULL and a tenant with pii_mask {enabled:true, mode:mask}
  When an owner calls GET /admin/keys/{key_id}/guardrails
  Then 200 is returned with pii_mask matching the TENANT config and source="tenant"

Scenario: PUT sets a key-level override, replacing only the guardrail present in the body   # M5
  Given a key with guardrail_policy = {pii_mask: {enabled:true, mode:mask}}
  When an admin PUTs {prompt_injection: {enabled:true, mode:block}} (pii_mask key absent)
  Then 200 is returned with BOTH prompt_injection (new) and pii_mask (preserved) in the
    stored key override, source="key"

Scenario: PUT with pii_mask explicitly null removes just that guardrail from the override   # M5
  Given a key with guardrail_policy = {prompt_injection: {...}, pii_mask: {...}}
  When an admin PUTs {pii_mask: null} (prompt_injection key absent)
  Then 200 is returned; the stored key override retains prompt_injection and drops pii_mask
    (the key override is now non-NULL and non-empty — still a "key" source, not "tenant")

Scenario: DELETE reverts a key to tenant inheritance   # M6
  Given a key with a non-NULL guardrail_policy
  When an admin calls DELETE /admin/keys/{key_id}/guardrails
  Then 204 is returned; a subsequent GET on the same key returns source="tenant"
  And a subsequent completion request through that key is enforced per the TENANT policy

Scenario: DELETE on a key with no existing override is idempotent   # M6 edge
  Given a key with guardrail_policy = NULL
  When an admin calls DELETE /admin/keys/{key_id}/guardrails
  Then 204 is returned (no error, no audit-worthy state change beyond the no-op)

Scenario: member cannot write a key's guardrail policy   # M7 / R3
  Given a key belonging to the caller's tenant
  When a member calls PUT /admin/keys/{key_id}/guardrails with a valid body
  Then 403 "ERR_AUTH_FORBIDDEN" is returned
  And the key's guardrail_policy column is unchanged

Scenario: member CAN read a key's guardrail policy   # M7
  Given a key belonging to the caller's tenant
  When a member calls GET /admin/keys/{key_id}/guardrails
  Then 200 is returned with the current effective config + source

Scenario: audit event recorded on a successful PUT   # M9
  Given an admin PUTs a valid guardrail override on a key they own
  When the PUT completes 200
  Then exactly one audit_events row is written with action="key_guardrail_policy.put",
    target_type="api_key", actor_user_id/actor_email set, and metadata containing NO
    pattern regex text or message content — only enabled/mode flags + key_id

Scenario: audit event recorded on a successful DELETE   # M9
  Given an admin DELETEs an existing override on a key they own
  When the DELETE completes 204
  Then exactly one audit_events row is written with action="key_guardrail_policy.delete"

Scenario: key override is enforced on an exact-match cache hit   # M10
  Given a key with guardrail_policy = {pii_mask: {enabled:true, mode:mask}} and a cached
    response for an identical prior request containing PII
  When a request through that key hits the exact-match cache
  Then the cached response body returned to the caller has the PII masked per the KEY's
    override (not the tenant's, if different)

Scenario: key override is enforced on a semantic-cache hit   # M10
  Given the same key-level override as above and a semantically-similar cached response
    containing PII
  When a request through that key hits the semantic cache
  Then the returned body has PII masked per the key's override

Scenario: key override is enforced on a vector-cache hit   # M10
  Given the same key-level override as above and a vector-cache hit containing PII
  When a request through that key hits the vector cache
  Then the returned body has PII masked per the key's override

Scenario: PUT rejects an invalid custom PII pattern   # R1
  Given a valid session as owner
  When PUT /admin/keys/{key_id}/guardrails carries pii_mask.pii_custom_patterns with a
    pattern containing a nested quantifier (ReDoS heuristic)
  Then 422 "ERR_PAYLOAD_INVALID" is returned
  And the key's guardrail_policy column is unchanged (atomic reject, same as tenant PUT)

Scenario: PUT rejects an invalid mode value   # R2
  Given a valid session as owner
  When PUT /admin/keys/{key_id}/guardrails carries {prompt_injection: {enabled:true, mode:"mask"}}
  Then 422 "ERR_PAYLOAD_INVALID" is returned
  And the key's guardrail_policy column is unchanged

Scenario: PUT/DELETE on a cross-tenant key returns 404, not 403   # R4
  Given key_id belongs to a DIFFERENT tenant than the caller's
  When an owner calls PUT /admin/keys/{key_id}/guardrails with a valid body
  Then 404 "ERR_KEY_NOT_FOUND" is returned (not 403 — no existence leak)
  And no row in any tenant's api_keys table is modified

Scenario: PUT/DELETE on a revoked key returns 404   # R4
  Given key_id belongs to the caller's tenant but is revoked (revoked_at IS NOT NULL)
  When an owner calls DELETE /admin/keys/{key_id}/guardrails
  Then 404 "ERR_KEY_NOT_FOUND" is returned
  And the revoked key's guardrail_policy column is unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Status: FROZEN @ v1 — approved by Tin Dang
```
GET /admin/keys/{key_id}/guardrails
  200 -> { prompt_injection: {enabled,mode}|null, pii_mask: {enabled,mode,pii_custom_patterns?}|null,
           source: "key" | "tenant" }
  404 -> { error: "ERR_KEY_NOT_FOUND" }

PUT /admin/keys/{key_id}/guardrails
  body: { prompt_injection?: {enabled,mode}|null, pii_mask?: {enabled,mode,pii_custom_patterns?}|null }
        (GuardrailConfigRequest, reused verbatim from tenants/api/guardrail_router.py —
         absent top-level key = preserve key's existing sub-value, null = remove, present = replace)
  200 -> { prompt_injection, pii_mask, source: "key" }   (source is ALWAYS "key" after a successful PUT)
  403 -> { error: "ERR_AUTH_FORBIDDEN" }
  404 -> { error: "ERR_KEY_NOT_FOUND" }
  422 -> { error: "ERR_PAYLOAD_INVALID" }

DELETE /admin/keys/{key_id}/guardrails
  204 -> (no body; idempotent whether or not an override existed)
  403 -> { error: "ERR_AUTH_FORBIDDEN" }
  404 -> { error: "ERR_KEY_NOT_FOUND" }

Schema:
  ALTER TABLE api_keys ADD COLUMN guardrail_policy JSONB NULL DEFAULT NULL;
    -- NULL = no override, inherit tenant (default for all existing + new keys — byte-identical).
    -- Non-NULL (including {}) = explicit key-level override, wholesale, no field merge with tenant.
    -- Mirrors the EXISTING api_keys.model_allowlist nullable-JSONB column on this same table.

  Access pattern (read):
    SqlAlchemyKeyRepository.get_by_id() — add ApiKeyRow.guardrail_policy to the already-selected
    columns in the existing 3-table LEFT JOIN (api_keys already selected wholesale — zero new
    JOIN, zero new query). Resolve:
      effective = key.guardrail_policy if key.guardrail_policy is not None else tenant.guardrail_configs
    and populate ApiKey.guardrail_configs / AuthzResult.guardrail_configs with the RESOLVED value,
    exactly as today (no downstream signature change).

  Access pattern (write):
    UPDATE api_keys SET guardrail_policy = :val::jsonb
      WHERE id = :key_id AND tenant_id = :tenant_id AND revoked_at IS NULL
      RETURNING id                                    -- PUT; 0 rows -> 404 ERR_KEY_NOT_FOUND
    UPDATE api_keys SET guardrail_policy = NULL
      WHERE id = :key_id AND tenant_id = :tenant_id AND revoked_at IS NULL
      RETURNING id                                    -- DELETE; 0 rows -> 404 ERR_KEY_NOT_FOUND
    (PUT's :val is the MERGED key-override dict — current key override, partial-merged per the
     GuardrailConfigRequest fields_set semantics, mirroring guardrail_router.put_guardrails)

Illustrative shapes (syntax-checked, ast.parse-clean):

  # keys/infrastructure/orm.py — ApiKeyRow (additive column)
  guardrail_policy: Mapped[dict[str, Any] | None] = mapped_column(
      JSONB, nullable=True, default=None
  )

  # keys/infrastructure/repository.py — resolution helper
  def _resolve_effective_guardrails(
      key_policy: dict[str, Any] | None,
      tenant_configs: dict[str, Any],
  ) -> dict[str, Any]:
      """Key > tenant > default-off. Wholesale override -- no field merge."""
      if key_policy is not None:
          return key_policy
      return tenant_configs

  # keys/api/<new router module> — GET/PUT/DELETE
  class KeyGuardrailPolicyResponse(BaseModel):
      prompt_injection: dict[str, Any] | None = None
      pii_mask: dict[str, Any] | None = None
      source: Literal["key", "tenant"]

  key_guardrail_router = APIRouter(prefix="/admin/keys/{key_id}/guardrails", tags=["api-keys"])

  @key_guardrail_router.get("", response_model=KeyGuardrailPolicyResponse)
  async def get_key_guardrails(
      key_id: uuid.UUID,
      identity: Annotated[Identity, Depends(get_identity)],
      session: Annotated[AsyncSession, Depends(get_session)],
  ) -> KeyGuardrailPolicyResponse: ...

  @key_guardrail_router.put("", response_model=KeyGuardrailPolicyResponse)
  async def put_key_guardrails(
      key_id: uuid.UUID,
      body: GuardrailConfigRequest,
      request: Request,
      identity: Annotated[Identity, Depends(require_owner_or_admin)],
      session: Annotated[AsyncSession, Depends(get_session)],
  ) -> KeyGuardrailPolicyResponse: ...

  @key_guardrail_router.delete("", status_code=204)
  async def delete_key_guardrails(
      key_id: uuid.UUID,
      request: Request,
      identity: Annotated[Identity, Depends(require_owner_or_admin)],
      session: Annotated[AsyncSession, Depends(get_session)],
  ) -> Response: ...

  # migration sketch (parent = actual alembic head at build time — this branch currently has
  # multiple open heads from sibling in-flight design tasks; NOT pinned here)
  def upgrade() -> None:
      op.add_column(
          "api_keys",
          sa.Column("guardrail_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
      )

  def downgrade() -> None:
      op.drop_column("api_keys", "guardrail_policy")
```

FREEZE QUESTIONS (Tin rules on these at the freeze — see §1 Assumptions for full reasoning):
  1. PUT semantics: partial-merge-within-key-override (mirrors tenant PUT) vs full-replace. RECOMMEND: partial-merge (API symmetry, zero extra cost).
  2. Revert mechanism: DELETE endpoint (recommended, REST-idiomatic, 7+ local precedents) vs a PUT sentinel field.
  3. Add `policy_source: "key"|"tenant"|"none"` to `AuthzResult` now (for the sibling guardrail-analytics task) vs defer to that task. RECOMMEND: add now — cheap, additive, avoids a second derivation later.
  4. GET response `source` field naming/values — cosmetic, default `"key"`/`"tenant"` unless overridden.

Glossary deltas: <new domain term(s) this task introduces, `Term: definition` — or "none">
  - AMENDS "Guardrail" (added v4, guardrails-core): guardrail policy resolution is now TWO-LEVEL —
    `Guardrail: a per-tenant OR per-key pre/post-call content control on proxy requests —
    prompt-injection detection (pre) and PII masking (post); tenant-level via
    `tenants.guardrail_configs` (GET/PUT /admin/guardrails, v4), key-level via
    `api_keys.guardrail_policy` (GET/PUT/DELETE /admin/keys/{key_id}/guardrails, added
    per-key-guardrail-policies). Resolution order: key > tenant > default-off; a non-NULL
    key policy overrides the tenant policy WHOLESALE (no field merge).`
  - NEW: `guardrail_policy: the key-scoped override column (api_keys.guardrail_policy, nullable
    JSONB). NULL = inherit tenant; non-NULL (including {}) = this key's own wholesale policy,
    tenant ignored entirely for this key.`
Status: DRAFT — awaiting human freeze
Reported: <yes — the freeze report (banner/ARC/SHAPE) rendered before this froze | no>
  no — this is the design-team draft; the orchestrator renders the freeze report when Tin reviews.

Least-sure flag surfaced at freeze: [contract] PUT semantics = partial-merge within the key's override (API symmetry with the tenant endpoint) — a coin-flip not yet empirically observable from the dashboard caller. Decided at freeze (Tin, 2026-07-10 batch): all 4 agent recommendations accepted (merge; DELETE revert-to-inherit; add policy_source to AuthzResult now; source field naming as drafted).

## Design self-score

- Completeness: 0.93 — every Must has a matching Reject/Scenario, every Reject has a contracted
  error response, migration+ORM+router+resolution all sketched and syntax-checked; the one
  deliberate gap (agent-OAuth tokens getting no guardrails at all) is a pre-existing condition
  flagged, not silently left unaddressed.
- Clarity: 0.93 — NULL-vs-{} is the one subtle bit and it's called out three times (Schema
  comment, M1 edge scenario, After state) so it can't be missed at freeze or build.
- Practicality: 0.95 — zero new IO seams, zero proxy-layer code changes, reuses an
  already-proven column shape (model_allowlist) and an already-proven validation module
  (guardrail_router's V1-V7) — the build is almost entirely additive plumbing.
- Optimization: 0.92 — confirmed zero extra DB reads (same LEFT JOIN); the one added JSONB
  column costs nothing on the hot auth path since the row is already selected wholesale.
- Edge cases: 0.9 — NULL vs {} override, idempotent DELETE, cross-tenant/revoked 404, all 3
  cache-hit paths, custom-pattern validation reuse, and concurrent-PUT last-write-wins (single
  UPDATE statement, no read-modify-write race beyond what the tenant PUT already accepts) are
  all covered; genuinely open items are pushed to FREEZE QUESTIONS rather than guessed.
- Self-evaluation: 0.9 — the two real judgment calls (PUT partial-merge vs full-replace;
  whether to widen AuthzResult now for the analytics task) are surfaced as ranked, reasoned
  freeze questions with a recommendation each, not resolved by fiat.
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
