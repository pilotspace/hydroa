# TASK: Art. 12 record-keeping export preset (one-click evidence bundle over compliance-export-api)

slug: art12-record-keeping-preset · created: 2026-07-14 · stage: production
milestone: eu-ai-act-readiness
sensitivity: data
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/audit/api/router.py:audit_export_router / export_audit` — `GET /admin/audit/export` (compliance-export-api TASK.md §3, FROZEN @ v1). This task's `audit_events` section calls `AuditRepository.list_for_tenant_keyset` (`audit/infrastructure/audit_repository.py:109`) IN-PROCESS, the same way the frozen route does — never an HTTP hop back into `export_audit` itself. The frozen route/contract is a reuse target, not a dependency to widen.
- `apps/gateway/src/gateway/audit/infrastructure/audit_repository.py:109 AuditRepository.list_for_tenant_keyset` — keyset page over `(created_at, id) DESC`, decomposed OR/AND predicate (not `tuple_()`, pyright-stub reason documented inline), `limit+1`-fetch has_more derivation. Reused verbatim for the `audit_events` section.
- `apps/gateway/src/gateway/logs/infrastructure/logs_repository.py:LogsRepository.list_for_tenant_keyset` (logs-explorer-api TASK.md §3, FROZEN @ v1) — the SAME keyset shape, one module over, for `request_logs`. Confirms this pattern is now a proven, twice-shipped precedent (audit, logs) — the natural third instance for `usage_records` (see Issues #1).
- `apps/gateway/src/gateway/logs/api/logs_query_router.py:LogListItem` (metadata-only: no `request_body`/`response_body`/`guardrail_verdict`) — the exact item shape this task's `request_log_metadata` section reuses field-for-field; `LogDetailItem`'s payload-bearing fields are deliberately NOT reused (this bundle is metadata-only for logs by construction, independent of ZDR).
- `apps/gateway/src/gateway/tenants/application/entitlements.py:check_plan_feature` — the plan-feature gate `GET /admin/logs` already applies (`"logs_explorer"`). Since this task reads `request_logs` data directly via `LogsRepository` rather than calling the gated HTTP route, it must independently honor the SAME entitlement or it becomes a silent bypass of an existing paid-feature gate (see Issues #5 / Must M9).
- `apps/gateway/src/gateway/logs/infrastructure/sqlalchemy_capture.py:SqlAlchemyPayloadCapture.capture` (L108-124) — confirmed: `if is_zdr: return` fires BEFORE any INSERT. Under `tenants.zdr_enabled=true`, `request_logs` receives ZERO new rows (not merely scrubbed/metadata-only rows — no row at all). This is the concrete mechanism behind MILESTONE.md's "a ZDR tenant's bundle is metadata-only" framing, and it must be surfaced in the bundle honestly (Issues #4 / Must M8), not left to read as "no traffic occurred."
- `apps/gateway/src/gateway/tenants/application/retention_policy.py:is_zdr` (L64) — fresh per-call `SELECT zdr_enabled FROM tenants` read, never cached; the pattern this task's own cover-snapshot read mirrors (read ONCE, at mint time, then pinned — see Must M4/M7).
- `apps/gateway/src/gateway/tenants/infrastructure/orm.py:TenantRow` — cover-report source columns: `name`; `residency_region` / `residency_region_updated_at` (residency-policy TASK.md §3, FROZEN @ v2 — NULL = unrestricted); `zdr_enabled` / `zdr_enabled_at` (tenant-retention-zdr TASK.md §3, FROZEN @ v1 — timestamp set only on false→true, never cleared); `retention_window_days` (tenant-retention-zdr, nullable, NO companion "updated_at" column exists — a real gap, see Issues #2); `guardrail_configs` (JSONB, current value only, no version/hash column anywhere); `default_tier` (service-tiers TASK.md §3, FROZEN @ v1).
- `apps/gateway/src/gateway/usage/infrastructure/orm.py:UsageRecordRow` — `id` is a Redis-stream-derived, millisecond-timestamp-based UUID (`redis_stream.py:stream_id_to_uuid`), roughly time-ordered — a MILDER version of `audit_events.id`'s pure-`uuid4()` limitation, but this is not a documented monotonic guarantee anywhere in this codebase; the contract must not overclaim it. Billing-lineage columns confirmed present and directly relevant to Art. 12 evidence: `cost_basis` ('provider'|'catalog'), `usage_source` ('frame'|'stream_fallback'), `tier_served` (service-tiers), `pricing_unit`, `key_id`, `model_id`, `prompt_tokens`/`completion_tokens`, `cost_usd`, `status`, `created_at`. Existing indexes: `ix_usage_records_created_at` (single-column) and `ix_usage_records_tenant_team` (tenant_id, team_id) — NO composite `(tenant_id, created_at, id)` index exists (see Issues #1/#7).
- `apps/gateway/src/gateway/usage/api/router.py:get_usage` — the ONLY existing read over `usage_records` besides aggregate-window endpoints (`get_spend`, `get_cost_by_tag`, `get_reconciliation`); it is raw `session.execute(text(...))` directly in the router (no repository class), returns totals + the 50 newest rows only, no `since`/`until`, no cursor. Confirms: **no bounded, paginated, period-filterable read exists over `usage_records` today** (Issues #1).
- `apps/gateway/src/gateway/tenants/domain/authz.py:Permission.AUDIT_READ` (L67) / `Permission.LOGS_READ` (L74, comment: "role set mirrors AUDIT_READ exactly: owner/admin/operator/superadmin") — confirmed identical role sets. `Permission.USAGE_READ` (used by `_require_usage_read` in `usage/api/router.py`) is a strictly BROADER set (adds billing_admin/viewer). Gating the whole bundle on `AUDIT_READ` alone is therefore sufficient and correct — no caller who legitimately needs `usage_records` access via this bundle is excluded, since anyone who clears `AUDIT_READ` also always clears the broader `USAGE_READ`.
- `apps/gateway/src/gateway/core/error_catalog.py:CURSOR_INVALID` (L1031, `ERR_CURSOR_INVALID` 422), `EXPORT_QUERY_TIMEOUT` (L1034, `ERR_EXPORT_TIMEOUT` 504), `PAYLOAD_INVALID` (L188, `ERR_PAYLOAD_INVALID` 422), `AUTH_FORBIDDEN` (L89, `ERR_AUTH_FORBIDDEN` 403), `AUTH_TOKEN_INVALID`/`AUTH_TOKEN_MISSING` — all reused verbatim; no new `ErrorSpec` needed.
- `apps/gateway/src/gateway/logs/api/logs_query_router.py:_encode_cursor` / `_decode_cursor` and `audit/api/router.py`'s equivalent opaque base64url `(created_at, id)` token — the precedent this task's own `bundle_token` (cover snapshot + 3 section cursors, still nothing persisted server-side) directly extends to a THIRD field: a pinned cover snapshot alongside the cursor state.
- `apps/gateway/src/gateway/audit/application/audit_writer.py:record_audit` (fire-and-forget, fail-open, separate session) — reused verbatim for this task's own "bundle generation is itself audited" Must (mirrors compliance-export-api M11 exactly).

Context (working folder): `.add/milestones/eu-ai-act-readiness/MILESTONE.md` (owning milestone — Scope, Shared decisions, "Shared/risky contracts" line naming THIS task as the owner of "Art. 12 bundle manifest shape"). Sibling `.add/tasks/compliance-report-center/TASK.md` is still the blank template (`phase: ground`, confirmed by reading it directly, not trusted from milestone prose) and `depends-on: art12-record-keeping-preset` — it consumes this contract for its "generate/download/schedule" console surface; no UI work happens in this task. `.add/tasks/ai-act-marketing-page` is an independent sibling (not read in depth — no dependency either direction). `tmp/r1-design-context.md` (shared wave-1 design-agent context, engine/process rules, verified market facts).

Honors (patterns / conventions):
- `.add/CONVENTIONS.md` clean-architecture layering (`domain/`←`application/`←`infrastructure/`←`api/`); this task's own composition role doesn't own state, so it is a legitimate candidate for a thin `api/`-only bounded context calling INTO existing repositories directly (mirrors `audit/api/router.py`'s own precedent of a router calling its repository directly for a simple read, no intervening use-case class, per that task's own build-persona note).
- MILESTONE.md shared decision: "Bundle is read-only assembly... it never grows a new write path; export access is itself audited (existing invariant)" — both pinned as Musts below.
- MILESTONE.md shared decision: "Evidence, not compliance" — this task's own copy surface is limited to structured field names (no marketing prose lives in an API contract), but the naming itself avoids implying legal compliance (e.g. `retention_window_days`, not `compliance_status`).
- PROJECT.md invariant "Every tenant-owned row carries `tenant_id`; every query is tenant-scoped" — every one of the 3 underlying reads is already tenant-scoped in its WHERE clause; this task adds no new cross-tenant surface.
- The established "opaque, self-describing continuation token, never a new store" idiom (audit-export cursor, logs cursor) — extended here to also carry a pinned cover snapshot (Issues #3).
- The established "honest degrade, never a silent gap" idiom (compliance-export-api M13 purge-mid-export; this codebase's repeated preference for a labeled partial result over an indistinguishable empty one).

Seams consulted: none (`.add/SEAMS.md` does not exist in this project, confirmed by the sibling residency-policy task's own grounding pass).

Anchors the contract cites: `audit/infrastructure/audit_repository.py:AuditRepository.list_for_tenant_keyset` (reused) · `logs/infrastructure/logs_repository.py:LogsRepository.list_for_tenant_keyset` (reused) · `logs/api/logs_query_router.py:LogListItem` (item shape reused) · `audit/api/router.py`'s `AuditEventItem` (item shape reused) · `tenants/application/entitlements.py:check_plan_feature` (section-level, not whole-bundle) · `tenants/infrastructure/orm.py:TenantRow` (cover snapshot source) · `tenants/domain/authz.py:Permission.AUDIT_READ` · `audit/application/audit_writer.py:record_audit` (reused) · `core/error_catalog.py` (5 codes reused, none new) · `usage/infrastructure/orm.py:UsageRecordRow` (NEW repository method target).

Issues/Risks (→ feed §1):
1. **GAP — no bounded/paginated read exists over `usage_records` for an arbitrary period.** `get_usage` (50-newest, no filters), `get_spend`/`get_cost_by_tag`/`get_reconciliation` (aggregates, not row-level lineage) are the only existing surfaces. Per the milestone's own "assembly only... a gap becomes a change-request, not silent scope growth" rule, this is flagged as a proposed follow-up: a standalone `usage-lineage-export` task (mirroring `audit/export`'s own shape) for a future SIEM-style pull. THIS task adds only the MINIMAL internal `UsageRepository.list_for_tenant_keyset` method the bundle itself needs — it is NOT exposed as its own public endpoint, keeping this task's own public surface additive-only over existing routes (see §1 assumption).
2. **GAP — no policy-version history exists anywhere.** `zdr_enabled_at` and `residency_region_updated_at` are the ONLY "since when" timestamps on `TenantRow`; `retention_window_days` and `guardrail_configs` have no companion timestamp or version field at all. The cover report's "policy versions" can therefore only ever be a CURRENT-STATE-AS-OF-GENERATION snapshot — never a true point-in-time reconstruction for a past period if a policy changed mid-period. Must be labeled honestly (§3 cover fields), not silently presented as if it were period-accurate.
3. **Determinism-across-pages risk.** No new write path is allowed (milestone Out-of-scope), so nothing server-side can remember a bundle's mint-time snapshot across HTTP calls. Without a deliberate design, a policy change between page 1 and page 2 of the SAME bundle walk would silently produce an internally-inconsistent artifact (cover claims one ZDR/residency state, later pages were actually generated under a different one) — exactly the "determinism and dating matter" risk this task's own persona was asked to weigh. Resolved via a `bundle_token` that carries the PINNED cover snapshot itself (extends the existing opaque-cursor idiom, no new storage — Must M4/M7).
4. **ZDR honesty.** Under `zdr_enabled=true`, `request_log_metadata` is legitimately `[]` (confirmed mechanism, §0 Touches) for as long as ZDR has been on — this must be labeled in the section's own `note` field, not left indistinguishable from "no matching traffic."
5. **Plan-feature honesty, section-scoped not bundle-scoped.** `check_plan_feature(..., "logs_explorer")` already gates the interactive `GET /admin/logs`. If this task re-applied that gate at the WHOLE-BUNDLE level, a tenant without the `logs_explorer` plan feature would lose the `audit_events` and `usage_lineage` sections too, even though neither depends on that feature — decided: gate ONLY the `request_log_metadata` section (Must M9), never the whole response.
6. **No existing Permission or plan-feature exists for "the bundle" itself.** Inventing one now is a monetization/packaging call (which plan tier gets Art. 12 evidence) that sits outside "assembly, not new engine capability" — flagged as an open freeze-question rather than decided unilaterally (§3).
7. `usage_records.id`'s rough time-ordering (§0 Touches) must not be worded in the contract as a stronger ordering guarantee than the audit/logs precedent already accepts (deterministic-total-order on `(created_at, id) DESC`, not guaranteed-chronological-on-tie).
8. A tenant's own `retention_window_days` may have already purged rows for part of a requested `since..until` period BEFORE this bundle is even requested (ordinary retention, not a mid-walk race) — the bundle can only ever reflect rows CURRENTLY present; this is the same class of honest limitation as Issue #2 and is folded into the same cover-level documentation rather than treated as a separate mechanism.

Related intent: `.add/milestones/eu-ai-act-readiness/MILESTONE.md` goal ("An EU tenant can self-serve produce a dated, Art. 12-mapped record-keeping evidence bundle... before EU AI Act GPAI enforcement lands on Aug 2, 2026") and rationale (obligations sit on upstream GPAI providers; Hydroa sells deployer-side Art. 12 record-keeping support only — "audit-readiness support," never "compliance," per the milestone's own accuracy-floor decision and `tmp/r1-design-context.md`'s verified Art. 101 figures). `.add/GLOSSARY.md` "Compliance export" / "Export cursor" / "Audit-of-export" (existing terms this task extends via new deltas below, never redefines).
Ground SHA: `c948576`

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: `GET /admin/compliance/art12-bundle` — a one-click, cursor-continuable, dated Art. 12 record-keeping evidence bundle assembled read-only over the audit-export, logs, and (new, internal-only) usage-lineage reads.
Framings weighed:
- **(chosen) One new read-only endpoint in a NEW `compliance/` bounded context** that composes `AuditRepository.list_for_tenant_keyset` + `LogsRepository.list_for_tenant_keyset` + a new internal `UsageRepository.list_for_tenant_keyset`, under ONE deterministic manifest envelope (a pinned `cover` + 3 independently-paginated `sections`), continuable via one opaque `bundle_token` (cover snapshot + 3 section cursors, no new storage). A new bounded context (not folded into `audit/` or `usage/`) because this task owns no domain state of its own — it is pure cross-context read composition, and folding it into an existing context would bloat that context's own frozen contract surface with unrelated composition logic.
- (rejected) Expose the 3 reads as 3 separate calls the console client must stitch together itself — rejected because the cross-cutting guarantee that actually matters here (a pinned cover consistent across every page of the SAME walk) cannot be provided by 3 independently-called, independently-timed endpoints without the CLIENT re-deriving the same pinning logic this task exists to centralize; MILESTONE.md explicitly names ONE owning task for exactly this "risky shared contract."
- (rejected) Persist a `compliance_bundles` row at mint time, read it back on continuation calls — rejected: violates the milestone's explicit "never grows a new write path" scope boundary, and the opaque-token idiom already proven twice in this codebase (audit/export cursor, logs cursor) solves the identical determinism need with zero new storage.

Must:
<must>
  - M1 AUDIT_READ-gated: reuse `Permission.AUDIT_READ` unchanged (owner/admin/operator/superadmin → 200; billing_admin/viewer/member → 403) — no new Permission enum member. A superadmin identity stays scoped to its own (platform) tenant only, mirroring `logs_query_router`'s own documented convention verbatim — no cross-tenant reach is wired here.
  - M2 Tenant-scoped everywhere: every row in every section carries the caller's own `tenant_id`; no filter combination widens visibility to another tenant's rows.
  - M3 Period is REQUIRED: `since` and `until` (ISO-8601, both inclusive) MUST both be present — unlike `audit/export`'s optional filters, a dated "period" is a first-class Art. 12 cover-report field, not an optional narrowing.
  - M4 Deterministic, pinned cover: on the FIRST call (no `bundle_token`), mint `bundle_id` (uuid7), `generated_at` (now, naive-UTC-normalized per the `_as_naive_utc` convention), and read the tenant's CURRENT `name`, `residency_region`, `zdr_enabled`/`zdr_enabled_at`, `retention_window_days`, `guardrail_configs`, `default_tier` exactly ONCE. These 8 cover fields are then PINNED — every subsequent page of the SAME bundle walk echoes them verbatim, never re-queried, regardless of any tenant-state change that occurs mid-walk (an accepted, documented residual — mirrors the existing budget/RPM/catalog-check "not dispatch-atomic" precedent).
  - M5 Three independently cursor-paginated sections, one shared continuation token: `audit_events` (item shape = `AuditEventItem`, reused field-for-field), `request_log_metadata` (item shape = `LogListItem`, reused field-for-field, metadata-only), `usage_lineage` (NEW item shape: `id, key_id, model_id, prompt_tokens, completion_tokens, cost_usd, cost_basis, usage_source, tier_served, status, created_at`). A single `limit` query param (1..5000, default 1000) applies uniformly to all 3 sections per page — a deliberate divergence from `GET /admin/logs`'s own interactive 100-row cap, justified because this bundle is an archival/evidence surface like `audit/export`, never an interactive UI page (see ⚠ assumption below).
  - M6 First-page mint: returns page 1 of all 3 sections (each via its own `limit+1`-fetch has_more derivation) plus the pinned cover; if ANY section `has_more=true`, an encoded `bundle_token` is returned (else `bundle_token: null`).
  - M7 Continuation: a call WITH `bundle_token` decodes the pinned cover snapshot + the 3 section cursors from the token, echoes the cover verbatim (never re-reads current tenant state), and advances only the sections that still have `has_more=true` (a section already exhausted on a prior page stays `items: [], has_more: false` for the remainder of this bundle walk).
  - M8 ZDR honesty: when the PINNED `zdr_state.enabled` is `true`, `request_log_metadata.items` is `[]` with `has_more=false` on every page, and its `note` field reads e.g. `"Zero-Data-Retention has been enabled since <zdr_enabled_at>; no request-log rows exist while ZDR is on."` — never a bare, unexplained empty array.
  - M9 Plan-feature honesty, section-scoped: read `check_plan_feature`'s underlying entitlement for `"logs_explorer"` ONCE at mint time (pinned, same as M4); if the tenant's plan lacks it, `request_log_metadata` is `[]`/`has_more=false` with `note="tenant plan does not include logs_explorer; audit_events and usage_lineage are unaffected"` — the OTHER two sections are still returned normally; the endpoint itself never 403s for this reason.
  - M10 Read-only: zero writes to `audit_events`, `request_logs`, or `usage_records`; the ONLY write is the fire-and-forget audit-of-generation row (M11).
  - M11 Bundle generation is itself audited: on EVERY successful page (mint or continuation), fire-and-forget `record_audit` (existing writer, fail-open, unchanged), `action="compliance.art12_bundle"`, `result="success"`, `metadata={since, until, bundle_id, page_token_used: bool, limit, row_counts: {audit_events, request_log_metadata, usage_lineage}}`. An audit-write failure MUST NOT fail or delay the bundle response (reuses the writer's existing fail-open contract verbatim).
  - M12 Bounded query time: the whole page's 3 underlying reads (plus the single tenant-row cover read on a mint call) are wrapped in one `asyncio.timeout`; on expiry, raise the EXISTING `ERR_EXPORT_TIMEOUT` (504) — no new timeout code minted for a 4th near-duplicate of `EXPORT_QUERY_TIMEOUT`/`LOGS_QUERY_TIMEOUT`.
  - M13 Purge-mid-walk honesty: a row purged by the retention sweeper between two pages of the SAME bundle walk simply stops matching its section's keyset predicate on the next page — no error, no gap marker (mirrors compliance-export-api M13 verbatim; this is NOT communicated via the `note` field, which is reserved for the two bundle-wide conditions in M8/M9, to avoid conflating two different honesty mechanisms under one field).
  - M14 `bundle_token` binds to its minting period: a continuation call's `since`/`until` query params MUST match the values encoded inside the token; a mismatch is rejected (R8) rather than silently either re-pinning to the new period or silently ignoring the new params — protects the "one bundle = one period" determinism guarantee.
</must>
Reject:
<reject>
  - missing/invalid bearer token -> "ERR_AUTH_INVALID_TOKEN" (401, unchanged auth dependency)
  - caller's role lacks AUDIT_READ (billing_admin/viewer/member) -> "ERR_AUTH_FORBIDDEN" (403)
  - `since` or `until` missing (either one) -> "ERR_PAYLOAD_INVALID" (422) — period is REQUIRED here, unlike audit/export
  - `since`/`until` not valid ISO-8601 -> "ERR_PAYLOAD_INVALID" (422)
  - `since` > `until` (inverted range) -> "ERR_PAYLOAD_INVALID" (422)
  - `limit` non-integer, < 1, or > 5000 -> "ERR_PAYLOAD_INVALID" (422)
  - `bundle_token` present but undecodable / malformed / wrong shape -> "ERR_CURSOR_INVALID" (422)
  - `bundle_token` present but its encoded `since`/`until` do not match the current call's `since`/`until` query params -> "ERR_CURSOR_INVALID" (422)
  - DB read exceeds the bounded query timeout -> "ERR_EXPORT_TIMEOUT" (504)
</reject>
After:
<after>
  - A compliance officer (owner/admin/operator, or superadmin scoped to their own tenant) pulls one dated, internally-consistent Art. 12 evidence bundle for an explicit period — in one call for a small tenant, or several cursor-continued calls for a large one — with zero possibility of a mixed/stale cover across pages of the same walk, and with ZDR/plan-feature gaps labeled honestly rather than silently empty.
  - Every page pulled is itself a new `audit_events` row (`action="compliance.art12_bundle"`), fail-open, visible to a subsequent `GET /admin/audit`/`GET /admin/audit/export` call.
  - `compliance-report-center` can implement "generate / download / schedule" purely by looping THIS one contract client-side to assemble a downloadable artifact — no other new backend surface is needed for that sibling task.
  - None of `compliance-export-api`'s, `logs-explorer-api`'s, or `usage`'s existing FROZEN contracts are touched, widened, or reinterpreted.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **Uniform 1..5000/default-1000 page-size ceiling applied identically to all 3 sections**, including `request_log_metadata` (whose own interactive surface, `GET /admin/logs`, caps at 100) — lowest confidence because it diverges from that surface's established ceiling with only ONE direct archival precedent to mirror (`audit/export`), not a per-surface one; if wrong, a follow-up must split into 3 distinct limits or lower the shared one. See FREEZE-QUESTION 1.
  - [ ] No new Permission or plan-feature gate on the bundle itself beyond reusing `AUDIT_READ` — confirm this is acceptable for v1, or that a monetization/packaging decision (e.g. a `"compliance_export"` plan-feature flag) should gate it instead; out of THIS task's additive scope unless decided at freeze.
  - [ ] New bounded context `gateway/compliance/api/` (vs. folding this route into `audit/api/` or `usage/api/`) — confirm the new-context call is architecturally sound; recommended because this is pure cross-context composition with no domain state of its own.
  - [ ] `usage_lineage`'s new repository method is added as an INTERNAL-only read (no new standalone public `/admin/usage/export` endpoint) — recorded as a proposed follow-up change-request (Issue #1) rather than built now; confirm this scoping call, or decide the standalone endpoint should ship as part of THIS task instead.
  - [ ] M14's reject-on-period-mismatch (vs. silently trusting the token's embedded period and ignoring the call's own since/until query params on a continuation call) — confirm this is the right failure mode; the alternative is also defensible and arguably simpler for a client to implement.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: owner/admin/operator/superadmin-own-tenant can generate a bundle   # M1
  Given a tenant with role owner (also: admin, also: operator, also: a superadmin acting on their own platform tenant)
  When they call GET /admin/compliance/art12-bundle?since=<iso>&until=<iso>
  Then the response is 200
  And it contains a cover object and 3 sections (audit_events, request_log_metadata, usage_lineage)

Scenario: billing_admin/viewer/member cannot generate a bundle   # M1, R2
  Given a tenant with role billing_admin (also: viewer, also: member)
  When they call GET /admin/compliance/art12-bundle?since=<iso>&until=<iso>
  Then the response is 403 "ERR_AUTH_FORBIDDEN"
  And no audit_events row is written for this attempt (denied before any DB read)

Scenario: no bearer token   # R1
  Given no Authorization header
  When calling GET /admin/compliance/art12-bundle
  Then the response is 401 "ERR_AUTH_INVALID_TOKEN"
  And no bundle-generation audit row is written

Scenario: tenant isolation across all 3 sections   # M2
  Given tenant A has audit/log/usage rows and tenant B has audit/log/usage rows in the same period
  When tenant A's owner requests a bundle for that period
  Then only tenant A's rows appear in every one of the 3 sections
  And tenant B's rows never appear, in any section, under any bundle_token continuation

Scenario: period is required   # M3, R3
  Given a tenant owner
  When calling GET /admin/compliance/art12-bundle with since present but until missing (also: until present, since missing; also: both missing)
  Then the response is 422 "ERR_PAYLOAD_INVALID"
  And no rows are read in any section

Scenario: malformed or inverted period   # R4, R5
  Given a tenant owner
  When calling with since=not-a-date (also: since=<later iso>&until=<earlier iso>)
  Then the response is 422 "ERR_PAYLOAD_INVALID"
  And no rows are read

Scenario: cover is pinned across pages of the same bundle walk   # M4, M7
  Given a tenant with zdr_enabled=false and residency_region="eu" at mint time, and 3 pages worth of audit_events
  When the owner requests page 1 (limit=1, forcing has_more), THEN the tenant flips zdr_enabled to true, THEN page 2 is requested with the returned bundle_token
  Then page 2's cover.zdr_state.enabled is still false and cover.residency_pin is still "eu" (the MINT-TIME values, not the current ones)
  And cover.bundle_id and cover.generated_at are byte-identical between page 1 and page 2

Scenario: bundle walks all 3 sections to completion with no gaps or dupes   # M5, M6, M7
  Given a tenant with 7 audit rows, 5 request_logs rows, and 9 usage_records rows all in-period
  When the owner pages through with limit=3, following bundle_token, until every section has_more=false
  Then the concatenation of each section's items equals its full seeded set exactly once, in (created_at, id) DESC order
  And a section that finishes before the others stays empty with has_more=false on every later page

Scenario: default and max page size, applied uniformly   # M5
  Given a tenant with 1200 audit rows, 1200 request_logs rows, and 1200 usage_records rows in-period
  When the owner calls with no limit
  Then each of the 3 sections' first page returns exactly 1000 items with has_more=true

Scenario: limit out of bounds   # R6
  Given a tenant owner
  When calling with limit=0 (also: limit=5001, also: limit=abc)
  Then the response is 422 "ERR_PAYLOAD_INVALID"
  And no rows are read in any section

Scenario: ZDR tenant's log section is honestly empty   # M8
  Given a tenant with zdr_enabled=true (enabled_at=<ts>) and audit/usage rows in-period
  When the owner requests a bundle
  Then request_log_metadata.items is [] and has_more is false
  And request_log_metadata.note names the ZDR enabled_at timestamp as the reason
  And audit_events and usage_lineage are populated normally

Scenario: tenant without the logs_explorer plan feature   # M9
  Given a tenant whose assigned plan's feature_flags does not include "logs_explorer", with audit/usage rows in-period
  When the owner requests a bundle
  Then the response is 200 (not 403)
  And request_log_metadata.items is [] with a note explaining the plan does not include logs_explorer
  And audit_events and usage_lineage are populated normally

Scenario: bundle generation never mutates the underlying stores   # M10
  Given a tenant with rows in all 3 stores, snapshot each row's fields
  When the owner generates a bundle (any page)
  Then re-snapshotting shows zero mutation in audit_events, request_logs, or usage_records

Scenario: successful bundle generation is itself audited   # M11
  Given a tenant owner generates page 1 and then page 2 of a bundle
  When a subsequent GET /admin/audit call is made
  Then it includes 2 new rows, action="compliance.art12_bundle", one per page, each metadata containing since/until/bundle_id/page_token_used/limit/row_counts

Scenario: audit-of-generation failure does not fail the bundle   # M11 (fail-open)
  Given the audit writer's session factory raises on write (simulated)
  When the owner requests a bundle
  Then the response is still 200 with the correct bundle body
  And the audit-write failure is only logged, never surfaced to the caller

Scenario: bounded query timeout surfaces honestly   # M12, R9
  Given a query that exceeds the bundle's time budget (simulated slow session)
  When the owner requests a bundle
  Then the response is 504 "ERR_EXPORT_TIMEOUT"
  And no partial bundle body is sent as if it were complete

Scenario: purge mid-walk is a silent, honest gap   # M13
  Given a tenant with usage_records rows past the retention floor eligible for the next sweep
  When the owner holds a bundle_token from page 1, THEN the retention sweeper purges one remaining row, THEN page 2 is requested
  Then page 2's usage_lineage section returns the remaining un-purged rows with no error and no gap marker
  And this is not surfaced via the note field (reserved for M8/M9 only)

Scenario: malformed bundle_token   # R7
  Given a tenant owner
  When calling with bundle_token=not-valid-base64-or-wrong-shape
  Then the response is 422 "ERR_CURSOR_INVALID"
  And no rows are read

Scenario: bundle_token period mismatch is rejected   # M14, R8
  Given a tenant owner holds a bundle_token minted for since=2026-06-01&until=2026-06-30
  When they call again with the SAME bundle_token but since=2026-07-01&until=2026-07-31
  Then the response is 422 "ERR_CURSOR_INVALID"
  And no rows are read

Scenario: empty bundle   # boundary
  Given a tenant with zero rows in all 3 stores for the requested period
  When the owner requests a bundle
  Then the response is 200 with all 3 sections {items: [], next section-level has_more: false} and bundle_token: null
  And cover is still fully populated (tenant/period/residency/ZDR/policy fields)

Scenario: last-page boundary — exact multiple of limit   # boundary
  Given a tenant with exactly 1000 audit rows (default limit) and 0 rows in the other two stores, in-period
  When the owner requests page 1
  Then audit_events returns all 1000 rows with has_more=false and no next_cursor (never inferred from row_count == limit alone)
  And bundle_token is null (no section has_more=true)

Scenario: duplicate bundle_token request is idempotent   # concurrency/retry-safety
  Given the owner holds a valid bundle_token from a prior page
  When the SAME bundle_token + same since/until/limit request is issued twice (simulating a client-side retry)
  Then both responses return byte-identical section content and cover (absent an intervening purge/insert)
  And two separate audit-of-generation rows are written (M11 fires per successful read, retries are not deduped)
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Least-sure flag surfaced at freeze: [spec] Uniform 1..5000/default-1000 page-size ceiling applied identically to all 3 sections, including request_log_metadata (whose interactive surface caps at 100) — see FREEZE-QUESTION 1.

```
GET /admin/compliance/art12-bundle
  query: since=<ISO-8601, REQUIRED> · until=<ISO-8601, REQUIRED>
       · limit?=1000 (1..5000, applies uniformly to all 3 sections) · bundle_token?=<opaque base64url>
  auth: Bearer JWT, Permission.AUDIT_READ (owner/admin/operator/superadmin-own-tenant; billing_admin/viewer/member -> 403)

  200 ->
    {
      "cover": {
        "bundle_id": str,                 # uuid7, minted on page 1, PINNED across the whole bundle_token walk
        "generated_at": str,              # ISO-8601, naive-UTC-normalized, PINNED
        "tenant_id": str,
        "tenant_name": str,
        "period": {"since": str, "until": str},
        "residency_pin": str | null,      # tenants.residency_region, PINNED at mint ("us"|"eu"|"ap"|null=unrestricted)
        "zdr_state": {"enabled": bool, "enabled_at": str | null},   # tenants.zdr_enabled/_at, PINNED at mint
        "retention_window_days": int | null,   # tenants.retention_window_days, PINNED at mint
                                                # NOTE: no "as of" timestamp exists in the schema for this field (documented limitation)
        "guardrail_configs_snapshot": object,  # tenants.guardrail_configs, PINNED at mint, raw current value
                                                # NOTE: no version/hash concept exists for this field (documented limitation)
        "default_tier": str,               # tenants.default_tier, PINNED at mint
        "format_version": "1"
      },
      "sections": {
        "audit_events":         {"items": [<AuditEventItem...>],  "next_cursor": str|null, "has_more": bool, "note": str|null},
        "request_log_metadata": {"items": [<LogListItem...>],     "next_cursor": str|null, "has_more": bool, "note": str|null},
        "usage_lineage":        {"items": [<UsageLineageItem...>],"next_cursor": str|null, "has_more": bool, "note": str|null}
      },
      "bundle_token": str | null   # present iff ANY section has_more=true; opaque, encodes
                                    # {bundle_id, generated_at, cover_snapshot, since, until,
                                    #  per-section (created_at,id) cursors}
    }

  UsageLineageItem (NEW — item shape for the usage_lineage section):
    {"id": str, "key_id": str, "model_id": str, "prompt_tokens": int, "completion_tokens": int,
     "cost_usd": str, "cost_basis": str, "usage_source": str, "tier_served": str,
     "status": int, "created_at": str}

  401 -> { code: "ERR_AUTH_INVALID_TOKEN" }   # missing/invalid bearer
  403 -> { code: "ERR_AUTH_FORBIDDEN" }        # role lacks AUDIT_READ
  422 -> { code: "ERR_PAYLOAD_INVALID" }       # missing/malformed since|until, inverted range, bad limit
  422 -> { code: "ERR_CURSOR_INVALID" }        # malformed bundle_token OR since/until mismatch vs the token's pinned period
  504 -> { code: "ERR_EXPORT_TIMEOUT" }        # bounded query time exceeded (reused, no new code)

Schema (no new table — reads 3 existing stores + 1 single-row cover read):
  audit_events    -- via AuditRepository.list_for_tenant_keyset (reused, unchanged)
  request_logs    -- via LogsRepository.list_for_tenant_keyset (reused, unchanged)
  usage_records   -- via NEW UsageRepository.list_for_tenant_keyset (additive; see below)
  tenants         -- single-row SELECT (name, residency_region, residency_region_updated_at,
                     zdr_enabled, zdr_enabled_at, retention_window_days, guardrail_configs,
                     default_tier) — read ONCE per bundle (mint-time only), never on a
                     continuation call (M4/M7)
  Write (side effect, fire-and-forget): one INSERT into audit_events per successful page,
    via the existing record_audit() writer — action="compliance.art12_bundle".

New repository (usage/infrastructure/usage_repository.py — NEW file; usage_records currently
has no repository class, only inline SQL in usage/api/router.py):
  class UsageRepository:
      def __init__(self, session: AsyncSession) -> None: ...
      async def list_for_tenant_keyset(
          self,
          tenant_id: uuid.UUID,
          *,
          limit: int,
          cursor: tuple[datetime, uuid.UUID] | None = None,
          since: datetime | None = None,
          until: datetime | None = None,
      ) -> list[UsageLineageRow]:
          """Keyset page over (created_at, id) DESC; caller fetches limit+1 to derive
          has_more. Mirrors AuditRepository.list_for_tenant_keyset /
          LogsRepository.list_for_tenant_keyset byte-for-shape (same decomposed OR/AND
          keyset predicate, same ORDER BY). This is an INTERNAL read for THIS bundle only
          — no standalone public /admin/usage/export route is added (see §1 assumption /
          Issue #1 change-request)."""
          ...

New API layer (compliance/api/router.py — NEW bounded context, compliance/'s first module):
  compliance_router = APIRouter(prefix="/admin/compliance", tags=["compliance"])

  @compliance_router.get("/art12-bundle")
  async def get_art12_bundle(
      request: Request,
      identity: Annotated[Identity, require_permission(Permission.AUDIT_READ)],
      session: Annotated[AsyncSession, Depends(get_session)],
      since: Annotated[str | None, Query()] = None,
      until: Annotated[str | None, Query()] = None,
      limit: Annotated[str | None, Query()] = None,
      bundle_token: Annotated[str | None, Query()] = None,
  ) -> Art12BundleResponse:
      """Mounted in main.py as a new router (mirrors audit_export_router's 2-line
      mount pattern). build-phase fills this in against the frozen shape above."""
      raise NotImplementedError

Mount (main.py, additive import + include_router):
  from gateway.compliance.api.router import compliance_router
  app.include_router(compliance_router)

Migration (additive, parents on current alembic head at build time):
  op.create_index(
      "usage_records_tenant_created_id_idx",
      "usage_records",
      ["tenant_id", sa.text("created_at DESC"), sa.text("id DESC")],
  )
  # Confirm-at-BUILD per the compliance-export-api / residency-policy precedent:
  # EXPLAIN ANALYZE against a realistic row count before committing to this index shape.

No new error_catalog.py entries — all 5 response codes (ERR_AUTH_INVALID_TOKEN,
ERR_AUTH_FORBIDDEN, ERR_PAYLOAD_INVALID, ERR_CURSOR_INVALID, ERR_EXPORT_TIMEOUT) are reused
verbatim from existing ErrorSpec constants.
```

Glossary deltas:
- **Art. 12 bundle**: the dated, deterministic record-keeping evidence manifest `GET /admin/compliance/art12-bundle` produces — a pinned cover snapshot (tenant, period, residency pin, ZDR state, policy-as-of-generation fields) plus 3 cursor-paginated sections (`audit_events`, `request_log_metadata`, `usage_lineage`) assembled read-only over 3 existing stores; continuable across HTTP calls via a `bundle_token` (no new storage). Named in MILESTONE.md; this delta gives it its concrete shape.
- **Bundle token**: the opaque, base64url-encoded continuation marker minted on an Art. 12 bundle's first page, carrying the PINNED cover snapshot plus each section's own `(created_at, id)` keyset cursor — extends the existing Export cursor idiom to also guarantee the cover stays internally consistent across every page of the same bundle walk, with nothing persisted server-side.
- **Usage lineage**: the per-request `usage_records` rows (model, tokens, cost, `cost_basis`, `usage_source`, `tier_served`) underlying a tenant's billed activity for a bundle's period — the audit-trail counterpart to Compliance export's audit events; this task adds the first bounded/paginated read over `usage_records` for an arbitrary period (previously only 50-newest + aggregate-window reads existed).

**Freeze decisions (Tin, 2026-07-14 — recorded at freeze, resolve the open questions above):**
- New compliance/ bounded context accepted (FREEZE-Q3).
- Gate on existing Permission.AUDIT_READ, no new plan-feature/permission now (FREEZE-Q2).
- Uniform 1..5000/default-1000 page ceiling across all 3 sections accepted (FREEZE-Q1).
- Usage-lineage stays an internal-only read; the standalone public export endpoint is the recorded follow-up change-request (FREEZE-Q4).
- bundle_token period-mismatch -> REJECT (deterministic evidence beats silent trust) (FREEZE-Q5).

Status: FROZEN @ v1 — approved by Tin Dang
Reported: no — awaiting human freeze (this draft, plus FREEZE-QUESTIONS below, is the freeze report input)

### FREEZE-QUESTIONS (Tin decides at freeze — each: options + recommendation)
1. **Page-size ceiling: uniform vs. per-section** (the ⚠ least-sure flag). Options: (a) one uniform 1..5000/default-1000 limit across all 3 sections [recommended — simplest surface, matches the archival/evidence framing of the whole endpoint, not an interactive UI page]; (b) 3 distinct ceilings, mirroring each section's OWN underlying surface (audit/usage: 1..5000/1000; logs: 1..100/50) [more conservative, avoids ever pulling more log-metadata per call than the interactive surface itself allows]. Recommendation: (a) — a compliance bundle is inherently a bulk/archival read, and a lower log cap would only mean MORE continuation round-trips for that one section with no corresponding safety benefit.
2. **No new plan-feature/Permission gate on the whole bundle** vs. reserving one now for future packaging. Options: (a) reuse `AUDIT_READ` only, no new gate [recommended — matches "assembly only," avoids a monetization decision this task isn't positioned to make]; (b) add a `check_plan_feature(..., "compliance_export")` gate now, seeded onto whichever plan tiers Tin designates. Recommendation: (a) for v1, given the Aug 2 deadline; (b) is a small, contained additive follow-up once packaging/pricing for the readiness pack is decided.
3. **New `compliance/` bounded context** vs. folding this route into `audit/api/` or `usage/api/`. Recommendation: new context — this task composes 3 existing contexts and owns no domain state; folding it into any ONE of them would make that context's own frozen surface own logic it doesn't otherwise need.
4. **Usage-lineage as an internal-only method** (no new standalone public endpoint) vs. shipping a full standalone `/admin/usage/export` as part of THIS task instead of deferring it. Recommendation: internal-only for v1 (matches "assembly, not new engine capability" and the tighter Aug 2 deadline); the standalone endpoint is recorded as a follow-up change-request (Issue #1) for whenever a SIEM-style usage pull is separately requested.
5. **`bundle_token`/since-until mismatch → reject (M14)** vs. silently trusting the token's embedded period and ignoring the call's own since/until params. Recommendation: reject — a silent-trust design means a client that forgets to keep echoing the SAME since/until on every continuation call gets no error signal that it's still walking the ORIGINAL period, which is a worse failure mode for evidence-grade output than an explicit 422.

### Scope (for whoever builds it — non-binding preferred plan, human freezes the shape above, not this list)
May touch:
- `apps/gateway/src/gateway/compliance/__init__.py`, `apps/gateway/src/gateway/compliance/api/__init__.py`, `apps/gateway/src/gateway/compliance/api/router.py` — NEW (compliance's first bounded context + api layer)
- `apps/gateway/src/gateway/usage/infrastructure/usage_repository.py` — NEW (additive, sibling of `AuditRepository`/`LogsRepository`)
- `apps/gateway/src/gateway/main.py` — additive import + `include_router` (mirrors `audit_export_router`'s existing two-line pattern)
- `apps/gateway/migrations/versions/<new>_usage_records_export_index.py` — NEW, additive index only
- `apps/gateway/tests/compliance_bundle/` — NEW suite (mirrors `tests/audit_export/` fixture pattern)
Must NOT touch: `audit/api/router.py` (frozen v1), `logs/api/logs_query_router.py` (frozen v1), `usage/api/router.py` (frozen v1 / spend-windows / cost-by-tag / reconciliation — all separately frozen), any existing ORM table's column definitions beyond the one additive index.

Strategy (ordered batches, non-binding preferred plan):
1. Ground-read + confirm every §0 anchor still resolves (esp. `AuditRepository`/`LogsRepository` method shapes, `TenantRow` columns, `check_plan_feature` signature, `record_audit`/`_as_naive_utc` conventions) — code may have moved since Ground SHA `c948576`.
2. New `usage/infrastructure/usage_repository.py:UsageRepository.list_for_tenant_keyset` — mirror `AuditRepository.list_for_tenant_keyset` / `LogsRepository.list_for_tenant_keyset` verbatim in shape (decomposed OR/AND keyset predicate, `limit+1` has_more derivation, `_as_naive_utc` bound normalization).
3. New `compliance/` bounded context: decide at build time whether a thin `application/`-layer use-case class is warranted per CONVENTIONS.md, or whether the router calls the 3 repositories directly (mirrors `audit/api/router.py`'s own accepted precedent for a simple, non-mutating, no-cross-aggregate-invariant read) — this is the single highest-judgment call this build owns.
4. `bundle_token` encode/decode helpers — mirror `_encode_cursor`/`_decode_cursor` exactly, extended to also carry the pinned cover snapshot + since/until (for the M14 mismatch check) alongside the 3 section cursors.
5. Cover-snapshot mint logic: ONE single-row `tenants` SELECT, executed only when `bundle_token` is absent; on a continuation call, decode the token's embedded cover instead of touching `tenants` again.
6. Additive migration + `usage_records_tenant_created_id_idx`; confirm via `EXPLAIN ANALYZE` against a realistic row count before finalizing (same deferred-verification convention as compliance-export-api / residency-policy).
7. Failing-first suite in `tests/compliance_bundle/`; green; re-run `tests/audit_export/`, `tests/audit_read/`, and the logs-explorer suite alongside (all frozen, must stay green); `ruff check` + `pyright` clean.

Persona (required): backend-architect — clean-architecture composition-layer lens (`domain/`←`application/`←`infrastructure/`←`api/`); this is the FIRST cross-context read-composition router in the codebase (composing `audit/`, `logs/`, and `usage/` from a NEW `compliance/` context), so the layering call at Strategy step 3 is the single highest-judgment decision this persona owns — advisory, does not lower any gate.
Spawn isolation (default): worktree — prefer an isolated worktree for the build/verify spawn per the project's own default, not only for explicit parallel mode.
Known-problem fixes: naive/aware `created_at` bind mismatch (test schema naive TIMESTAMP vs prod TIMESTAMPTZ) → reuse the local `_as_naive_utc` copy convention (verbatim-local-copy, not a cross-module private import, per this codebase's own established convention). `git stash` is a GLOBALLY SHARED stack across every linked worktree of this repo (hit and recovered from during compliance-export-api's own build) → never use `git stash` mid-build; prove RED via a temporary branch or a diff-based isolation instead.

---

## Design self-score

Illustrative shapes (repository method signature, router signature, migration sketch, UsageLineageItem field list) were checked against the exact real signatures of `AuditRepository.list_for_tenant_keyset` / `LogsRepository.list_for_tenant_keyset` / `_encode_cursor`/`_decode_cursor` read during grounding — every field name and every reused symbol traces to a file/line actually opened this session, not assumed from the milestone's prose alone.

- Completeness: 0.93 — every Must and Reject has a scenario and a contracted response; the new repository method, API layer, mount, and migration are all named with exact file targets; both genuine gaps found (usage-lineage bounded read, policy-version history) are recorded as explicit change-requests/limitations rather than silently absorbed. Held below 0.95 because the exact `application/`-layer-or-not call (Strategy step 3) is deliberately left as a build-time judgment rather than pre-decided — a real, named open point, not an oversight.
- Clarity: 0.94 — cover/section/bundle_token shapes are each stated once, unambiguously, with the exact precedent each reuses or the exact way each deliberately diverges (uniform limit, required period, section-scoped honesty) named explicitly rather than left implicit.
- Practicality: 0.92 — every new piece (repository method, router, migration, bundle_token codec) is additive-only against real, currently-existing symbols (§0 anchors), buildable without touching any of the 3 frozen sibling contracts it composes over. Held below 0.95 because the composite index (like its 2 predecessors) is explicitly unverified pending an `EXPLAIN ANALYZE` at build time.
- Optimization: 0.91 — the page-size ceiling and the internal-only-vs-standalone usage-lineage-endpoint choices are reversible, low-blast-radius judgment calls, each flagged rather than silently asserted; the `bundle_token`-carries-cover design avoids a persisted store while still solving the determinism requirement, the one place a wrong call here would have been expensive to unwind post-freeze.
- Edge cases: 0.93 — covers empty bundle, exact-multiple-of-limit boundary, ZDR toggling mid-walk, plan-feature absence, concurrent purge, malformed/mismatched token, timeout, and duplicate-request idempotency; the interaction between M8 (ZDR) and M9 (plan-feature) on the SAME section is scenario'd as two separate cases rather than merged, since either could independently apply.
- Self-evaluation: 0.92 — the one genuinely open judgment call (page-size ceiling) is surfaced as the ⚠ least-sure flag and echoed as FREEZE-QUESTION 1 with both options argued; 4 further judgment calls (plan-feature gate, bounded context placement, internal-vs-standalone usage read, token-mismatch behavior) are also surfaced as freeze-questions rather than folded silently into the draft.

All six ≥ 0.9 — no refinement pass required before reporting.

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

Scope (may touch): see "Scope" under §3 CONTRACT above (drafted at contract time, per the design-span convention of fixing the allowlist alongside the shape it builds).
Strategy (ordered batches): see "Strategy" under §3 CONTRACT above.

Persona (required): backend-architect (see §3 CONTRACT above)
Spawn isolation (default): worktree (see §3 CONTRACT above)
Known-problem fixes: see §3 CONTRACT above.
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): the cover snapshot MUST be read/pinned exactly once per bundle (mint call only) and never re-queried on a continuation call — a build that accidentally re-reads `tenants` on every page reintroduces the exact determinism risk (Issue #3) this contract exists to close.
Code lives in: `apps/gateway/src/`
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
Reviewed by: Tin Dang · date: 2026-07-14

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

