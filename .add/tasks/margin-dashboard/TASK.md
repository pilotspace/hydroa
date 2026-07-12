# TASK: Operator margin dashboard (provider cost vs billed vs invoiced)

slug: margin-dashboard · created: 2026-07-12 · stage: production
sensitivity: mechanical
milestone: monetization-core
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: ground   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/usage/application/reconciliation.py` — the EXISTING, pre-milestone READ-ONLY reconciliation substrate this task productizes, not replaces. `reconcile_window(session, window_from, window_to, tenant_id=None) -> ReconciliationSummary` and `reconcile_by_tenant(session, window_from, window_to) -> tuple[TenantReconciliation,...]` are the two primitives `/ops/reconciliation` already calls. Both split strictly on `cost_basis`: `provider_cost_total`/`billed_total`/`drift` are `SUM(...) FILTER (WHERE cost_basis='provider')` ONLY; `catalog_billed_total` is reported separately (`cost_basis='catalog'`) and is "never folded into drift" (module docstring, verbatim). `_money()` (line 99, `Decimal(str(value))`) is the project's float-avoidance idiom this task's every new aggregation reuses verbatim. This task ADDS two new functions to this SAME module (additive, byte-unchanged existing functions): `reconcile_by_tenant_model` (per-tenant-PER-MODEL grouping, mirrors `reconcile_by_tenant`'s SQL shape with `GROUP BY tenant_id, model_id` and an added `catalog_billed_total` column) and `reconcile_trend` (date-bucketed series, mirrors the `date_trunc` idiom below).
- `apps/gateway/src/gateway/usage/application/recorder.py:_safe_provider_cost` (line ~695) — confirms `provider_cost`/`cost_basis='provider'` is populated ONLY when the upstream response carries a `usage.cost` field, which — grep-confirmed across every adapter in this codebase (`grep -rn '"cost"'` under `gateway/`, single hit: this file) — is emitted by exactly ONE integration path (OpenRouter's pass-through `usage.cost`), consumed nowhere else. Every other provider (OpenAI, Anthropic, Bedrock, Azure, Gemini, Vertex, etc.) leaves `cost_basis='catalog'` and `provider_cost` NULL (`recorder.py` line 268-271: "default catalog basis; provider_cost stays NULL unless an upstream cost is consumed below"). This is DECISIVE for §1 M3 — the overwhelming majority of `usage_records` rows have no authoritative provider cost, by construction, not by a gap this task can close.
- `apps/gateway/src/gateway/usage/infrastructure/orm.py:UsageRecordRow` — confirmed fields this task aggregates: `cost_basis` (Text, server_default `'catalog'`), `provider_cost` (`Numeric(20,10)`, nullable), `cost_usd` (`Numeric(14,8)`), `tenant_id`, `model_id`, `created_at`. No column this task needs is absent.
- `apps/gateway/src/gateway/usage/api/router.py:_compute_window_bounds` (line 196) — the EXACT window/date validator `/ops/reconciliation` already reuses (`ops/api/router.py` line 31, with the identical import-and-reuse comment this task's own router copies verbatim). `_VALID_WINDOWS = {"day","week","month"}` (line 75); raises `PAYLOAD_WINDOW_INVALID`/`PAYLOAD_START_DATE_INVALID`/`PAYLOAD_END_DATE_INVALID` — all three map to the SAME wire code `ERR_PAYLOAD_INVALID` (422), confirmed in `core/error_catalog.py` lines 282-294 (only the `title_template` differs). Same function also DOUBLES as the bucket-granularity selector: the third tuple element it returns (`granularity`) is fed straight into the `date_trunc('{granularity}', created_at AT TIME ZONE 'UTC')` bucketing SQL at `usage/api/router.py` lines 355-369 (the `GET /admin/usage/spend` windowed-bucket endpoint) — this task's `reconcile_trend` reuses this EXACT idiom (same `date_trunc` call shape, same NUMERIC-not-float SUM discipline) rather than inventing a second bucketing scheme or a separate `granularity=` query param.
- `apps/gateway/src/gateway/ops/api/router.py:get_ops_reconciliation` + `apps/gateway/src/gateway/ops/api/deps.py:require_ops` — confirms `/ops/reconciliation` is gated by `require_ops` (mTLS client cert → Envoy → `x-forwarded-client-cert` → fingerprint allow-list; a valid tenant JWT gets 403 `ERR_OPS_FORBIDDEN`, everything else byte-identical 401 `ERR_OPS_UNAUTHORIZED`) — an auth surface the platform console (browser, tenant/superadmin JWT) CANNOT reach. This is decisive for §1 M1: margin-dashboard needs its OWN JWT-based superadmin gate, and MUST NOT proxy through or duplicate the mTLS path — but its numbers, for the same window, must be byte-identical to `/ops/reconciliation`'s (both call the same `reconcile_window`).
- `apps/gateway/src/gateway/tenants/api/platform_plans_router.py` (full file read) and `apps/gateway/src/gateway/credits/api/router.py:credits_platform_router` (prefix `/admin/platform/credits`, line 48) — the TWO existing precedents for a cross-tenant, superadmin-only, platform-console read surface: `require_superadmin` (role-only dependency, `tenants/domain/authz.py:require_superadmin` line 329 — "deliberately NOT a Permission" since there is no single `target_tenant_id` for a bulk cross-tenant read) as the FIRST FastAPI dependency on every route; router `prefix="/admin/platform/..."`; every route — including plain GETs like `list_platform_plans` — calls `emit_platform_audit(...)` on its success path with `target_tenant_id=None` for the targetless/bulk case (`platform_plans_router.py` lines 169-177, action `"platform.plan.list"`). This task's router (`/admin/platform/margin`) mirrors this shape exactly, including auditing every read (cross-tenant financial visibility is exactly the kind of access this precedent already treats as audit-worthy).
- `apps/gateway/src/gateway/audit/application/platform_audit.py:emit_platform_audit` (line 36) — signature confirmed: `(session_factory, *, identity, target_tenant_id, action, target_type, target_id, metadata)`; "Callers never construct AuditEvent or call record_audit/asyncio.ensure_future themselves — this is the ONLY place either happens for all cross-tenant call sites" (docstring, verbatim) — this task's 4 routes call it, never a hand-rolled audit write.
- `apps/gateway/src/gateway/billing/infrastructure/orm.py:InvoiceRow` / `apps/gateway/src/gateway/billing/infrastructure/invoice_repository.py:InvoiceRepository` (invoice-generation TASK.md §3, FROZEN @ v1, ALREADY BUILT — `phase: verify` as of this ground read) — confirmed real, shipped columns: `invoices.raw_total_usd` (`Numeric(14,8)`, "pre-rounding SUM, audit/reconciliation-drift only, never billed" — module docstring, verbatim — this is EXACTLY the field this task's tie-out compares against), `invoices.total_usd` (rounded, billed), `invoices.status` (`'draft'|'issued'`), `UniqueConstraint(tenant_id, period_start)` (one row per UTC calendar month per tenant, M1 of that task). `InvoiceRepository` exposes no "list all tenants for a period" method (its every method is tenant-scoped, `get_invoice`/`list_for_tenant_keyset`/etc.) — this task's tie-out needs a NEW, additive, cross-tenant read (`SELECT * FROM invoices WHERE period_start = :period_start`, superadmin-authorized), Build's choice whether as a new `InvoiceRepository` method or a standalone query in the margin module — non-load-bearing on this contract's shape.
- `apps/gateway/src/gateway/main.py` — `app.include_router(platform_plans_router)` (line 1341, alongside `platform_tenants_router`/`platform_users_router`/`platform_tenant_config_router`/`platform_impersonation_router`/`platform_audit_router`, lines 1338-1343) and `app.include_router(invoices_router)` (line 1373) — the registration block this task's new `margin_router` joins.
- `apps/dashboard/components/ui/app-shell.tsx:PlatformNavGroup` (lines 205-231) + `showPlatformNav` (line 192, `role === "superadmin"` EXACT allowlist, fail-CLOSED — deliberately NOT the fail-open default every other nav item uses, per its own comment: a platform-admin link disclosing a cross-tenant surface is a real trust cost) + `PLATFORM_TENANTS_HREF`/`PLATFORM_PLANS_HREF` (lines 196, 203) — the exact 2-entry precedent this task's THIRD entry (`PLATFORM_MARGIN_HREF = "/app/platform/margin"`) extends, zero changes to the existing two entries or `showPlatformNav`.
- `apps/dashboard/app/(app)/app/platform/plans/page.tsx` + `apps/dashboard/components/platform/PlatformPlanCatalog.tsx` (full file read) — the exact "thin Server Component page + a `"use client"` data component using `useQuery`/`bffGet`/`BffError`, `Card`/`CardHeader`/`CardContent`/`Loading`/`Empty`/`ErrorState`/`PageHeader` from `@/components/ui`" shape this task's `/app/platform/margin` page + `PlatformMarginView.tsx` component mirror. No client-side role gate — "the gateway's existing `require_superadmin` dependency is the sole enforcement point" (that page's own docstring, verbatim); a non-superadmin direct hit surfaces the standard `ErrorState`.

Context (working folder): MILESTONE.md `monetization-core` line 38 — `margin-dashboard depends-on: invoice-generation — operator margin view (provider cost vs billed vs invoiced, per tenant/model) productizing reconciliation, platform console`; line 47 exit criterion — "The platform operator sees per-tenant margin (billed − provider cost) that reconciles against the existing reconciliation view". `invoice-generation` is `phase: verify` as of this ground read (its §3 is FROZEN @ v1 and its ORM/migration are already merged on this branch) — this task's tie-out cites REAL, already-shipped columns, not a forward promise. Sibling wave-1/wave-2 tasks (`credits-ledger`, `plan-enforcement`, `seat-billing`, `billing-ui`) have no code-surface overlap with this task.

Honors (patterns / conventions):
- `.add/CONVENTIONS.md` clean-architecture layering — this task is additive-only within the EXISTING `usage/` context (`application/reconciliation.py`, `api/margin_router.py`) plus a cross-context read of `billing/infrastructure/orm.py:InvoiceRow` for tie-out (the same kind of cross-context read `invoice-generation` itself performs against `usage_records`, an established precedent, not a new layering violation).
- MILESTONE.md shared decisions: "usage_records is the only ledger of truth" (honored — zero new usage-truth tables; pure aggregation); "one resolver" (honored — this task NEVER calls `resolve_markup_pct` or any pricing function, only sums already-persisted `cost_usd`/`provider_cost`, exactly like invoice-generation's own M3); "append-only money... corrections are new signed-delta entries" (honored by construction — this task has NO write path at all, the strictest possible form of "never mutates money").
- CLAUDE.md IO design-for-failure rule — every read bounded by `asyncio.timeout`, mirrors `/ops/reconciliation`'s own 30s bound (`ops/api/router.py:_OPS_RECON_TIMEOUT_SECONDS`) and invoice-generation's identical idiom; no outbound network IO (internal Postgres only) so no retry/circuit-breaker wrapper is warranted (matches every existing DB-read admin route).
- Decimal/provenance discipline (project-wide, confirmed directly in `reconciliation.py`/`recorder.py` above): all money arithmetic stays `Decimal`, serialized as `str(Decimal)` on the wire, never `float` — reuses `_money()` verbatim.

Seams consulted: none (`.add/SEAMS.md` not present in this repo, per invoice-generation's own identical finding).

Anchors the contract cites: `usage/application/reconciliation.py:reconcile_window` / `:reconcile_by_tenant` / `:_money` (reused) · new `:reconcile_by_tenant_model` / `:reconcile_trend` (additive) · `usage/application/recorder.py:_safe_provider_cost` (provider-cost-honesty proof) · `usage/infrastructure/orm.py:UsageRecordRow` · `usage/api/router.py:_compute_window_bounds` (reused verbatim) · `ops/api/router.py:get_ops_reconciliation` (cross-check target, not called) · `ops/api/deps.py:require_ops` (the auth surface this task must NOT use) · `tenants/domain/authz.py:require_superadmin` (the auth surface this task DOES use) · `audit/application/platform_audit.py:emit_platform_audit` · `billing/infrastructure/orm.py:InvoiceRow` (`raw_total_usd`, `total_usd`, `status`, `period_start`) · `core/error_catalog.py:ErrorSpec` (`PAYLOAD_WINDOW_INVALID` family reused; new `MARGIN_QUERY_TIMEOUT`) · `main.py` include_router block · `app-shell.tsx:PlatformNavGroup` / `showPlatformNav`.

Issues/Risks (→ feed §1):
- R1 **NULL-provider-cost is the norm, not the exception** — only OpenRouter-routed usage carries an authoritative `provider_cost`; every other provider's rows are `cost_basis='catalog'` with `provider_cost` NULL, by construction (recorder.py, confirmed above). A margin dashboard that silently shows "$0 cost" or a guessed figure for these rows would be fiction, not analytics — this is the task's central honesty risk, flagged at §1 ⚠.
- R2 **no reliable way to back out a historical catalog-basis cost** — `cost_usd` for a `catalog`-basis row already has `markup_pct` baked in at record time (recorder.py, same as invoice-generation's own M12 finding), but the markup_pct VALUE used is not persisted per-row; re-deriving a "pre-markup" figure would require re-resolving the rate-card resolver against CURRENT config, which drifts from the historical truth and reopens the "one resolver, never a second price path" rule (MILESTONE.md binding rule #2) this task must not violate.
- R3 **`reconcile_by_tenant` is already unbounded** (returns ALL tenants with no pagination, an existing accepted risk in the pre-milestone codebase) — this task does NOT change that function, but its OWN new `reconcile_by_tenant_model` has strictly HIGHER cardinality (tenant × model, not just tenant) and is deliberately given pagination (§1 M10) rather than inheriting the unbounded precedent.
- R4 **tie-out's "period" concept is calendar-month-only** (matches invoice-generation M1's UTC calendar-month invariant exactly) — this is a DIFFERENT param shape (`period=YYYY-MM`) than the `window=day|week|month` + `start`/`end` used by the other 3 endpoints; conflating them would let a caller ask for a tie-out over a non-month window that no invoice could ever match, producing confusing permanent "pending_invoice" noise.
- R5 **a genuinely very-early period (e.g. yesterday) has no invoice yet by design** (invoice-generation's 72h stabilization window, not yet Tin-confirmed at that task's own freeze at time of this reading) — tie-out must not report a false "drift_detected" for a period that simply hasn't been invoiced yet.

Related intent: MILESTONE.md `monetization-core` (roadmap M1, Tin-confirmed 2026-07-12); Glossary delta `margin` already declared at the milestone level (MILESTONE.md line 24: "billed − provider cost, per tenant/model") — this task is the owning task that productizes it; exit criterion (MILESTONE.md line 47).

Ground SHA: 71641a9

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Operator margin dashboard — a superadmin-only platform-console view productizing the existing `usage_records` reconciliation substrate into per-tenant/per-model margin, a windowed trend, and a three-way tie-out against issued invoices.

Framings weighed: **Extend the existing `reconciliation.py` primitives + a new `require_superadmin` platform router (JWT), byte-identical to `/ops/reconciliation` for shared numbers** (chosen) · Reuse `/ops/reconciliation` directly from the dashboard BFF, tunneling the platform-console session through the mTLS ops path (rejected — `require_ops` is an operator-CERT surface, not a user-identity surface; the dashboard has no client cert to present, and manufacturing one to bridge a JWT session would blur the two auth models MILESTONE.md and the ops substrate keep deliberately separate) · Estimate a "shadow" provider cost for `cost_basis='catalog'` rows from the current catalog price (rejected — reopens a second price path forbidden by MILESTONE.md binding rule #2, and would drift from the historical markup actually applied at record time, R2).

Must:
<must>
  - M1 Auth seam: every route is gated by `require_superadmin` (role-only JWT dependency, `tenants/domain/authz.py`) — the platform-console surface, NEVER `require_ops` (mTLS). Router `margin_router = APIRouter(prefix="/admin/platform/margin")`, mirrors `platform_plans_router`/`credits_platform_router` exactly. For the SAME `window`/`start`/`end`, `GET /admin/platform/margin/summary`'s `provider_cost_total`/`billed_total`/`drift`/`unbilled_upstream_cost`/`unbilled_rows`/`catalog_billed_total` fields MUST be byte-identical to `/ops/reconciliation`'s corresponding fields — both call the SAME `reconcile_window`, never a second aggregation.
  - M2 No second aggregation path: `reconcile_window`/`reconcile_by_tenant` are reused UNMODIFIED (imported, not re-implemented). The two genuinely new aggregations this task needs — per-(tenant,model) grouping and date-bucketed trend — are added as NEW, additive functions in the SAME `usage/application/reconciliation.py` module (one query surface for this whole substrate), reusing `_money()` and the module's existing half-open-window / `cost_basis` FILTER idioms verbatim. This task never imports or calls `resolve_markup_pct` or any pricing function (mirrors invoice-generation's own M3 "no second price path").
  - M3 Provider-cost honesty (the central rule, R1/R2): margin is a real, computed number ONLY for `cost_basis='provider'` rows (`billed_total − provider_cost_total` for that bucket). `cost_basis='catalog'` rows NEVER receive a fabricated, estimated, or zeroed margin — their billed revenue is reported separately as `catalog_billed_total`, and every per-(tenant,model) and summary response carries an explicit `has_provider_cost_data: bool` (true iff the bucket has ≥1 `cost_basis='provider'` row). A bucket with `has_provider_cost_data=false` shows `margin: null`, never `margin: 0` (zero would falsely imply "billed at cost with no profit"; null truthfully means "unknown").
  - M4 Per-tenant-per-model margin: new `reconcile_by_tenant_model(session, window_from, window_to) -> tuple[TenantModelReconciliation,...]`, `GROUP BY tenant_id, model_id`, returning `(tenant_id, model_id, provider_cost_total, billed_total, catalog_billed_total, margin, unbilled_upstream_cost, unbilled_rows, has_provider_cost_data)` per bucket — the SQL/dataclass shape `reconcile_by_tenant` already establishes, extended with one more GROUP BY column and the `catalog_billed_total`/`has_provider_cost_data` additions from M3.
  - M5 Windowed trend: new `reconcile_trend(session, window_from, window_to, granularity, tenant_id=None) -> tuple[MarginTrendPoint,...]` buckets by `date_trunc('{granularity}', created_at AT TIME ZONE 'UTC')`, reusing the EXACT bucketing idiom `usage/api/router.py`'s spend-windows endpoint already uses (lines 355-369) — same NUMERIC-SUM discipline, no float. `granularity` is NOT a separate query param: it is the third element `_compute_window_bounds` already returns from the SAME `window=day|week|month` value the endpoint's `window`/`start`/`end` params carry (identical to how `/admin/usage/spend` derives its own bucket size) — this task invents no new param shape for it. `tenant_id` is an OPTIONAL filter (all tenants when omitted).
  - M6 Three-way tie-out (billed vs invoiced vs provider): `GET /admin/platform/margin/tie-out?period=YYYY-MM` computes, per tenant that has EITHER usage or an issued invoice in that UTC calendar month: `ledger_billed_total_usd` (`SUM(usage_records.cost_usd)` for the period, ALL cost_basis, cross-tenant), `provider_cost_total_usd` (`SUM(provider_cost)` FILTER `cost_basis='provider'`), and — if an `invoices` row exists for `(tenant_id, period_start)` — `invoiced_total_usd`/`invoiced_raw_total_usd` (`invoices.total_usd`/`raw_total_usd`, read-only, cited directly from invoice-generation's frozen §3 columns, never recomputed). `tie_out_status="matched"` when an invoice exists and `invoiced_raw_total_usd == ledger_billed_total_usd` exactly (the invariant invoice-generation's own M3/M4 already guarantees); `"drift_detected"` when an invoice exists and they differ (a genuine anomaly this task SURFACES, never silently fixes or writes).
  - M7 `tie_out_status="pending_invoice"` (not a discrepancy) when NO `invoices` row exists yet for `(tenant_id, period_start)` — covers both "period too recent, stabilization window hasn't elapsed" (R5) and "tenant has zero usage that period" (still listed, matching invoice-generation's own M10 "$0 invoice, never a silent gap" spirit — a period with usage but no invoice is `pending_invoice`, not silently omitted).
  - M8 Every one of the 4 reads is bounded by `asyncio.timeout` (mirrors `/ops/reconciliation`'s 30s bound) → `ERR_MARGIN_QUERY_TIMEOUT` (504) on expiry — new `ErrorSpec` in `core/error_catalog.py`, same shape as `INVOICE_QUERY_TIMEOUT`.
  - M9 Cross-tenant financial visibility is audited: every one of the 4 routes calls `emit_platform_audit(...)` on its success path (`target_tenant_id=None` for the operator-wide reads, matching `platform_plans_router`'s own bulk-list precedent; the optional `tenant_id=` filter, when given, is carried in `metadata`, not as `target_tenant_id`, since the READ still spans the query the caller chose, not one tenant's own record).
  - M10 `GET /admin/platform/margin/by-tenant-model` is the one paginated list endpoint (R3 — strictly higher cardinality than the existing unbounded `reconcile_by_tenant`): keyset over `(tenant_id, model_id)` ascending, `limit` 1..100 default 50, `fetch limit+1` to derive `has_more`, opaque base64 cursor — mirrors `AuditRepository.list_for_tenant_keyset`'s idiom (already reused verbatim by invoice-generation's own list endpoint).
  - M11 Every money field is `Decimal` end-to-end and serialized as `str(Decimal)` on the wire (never `float`) — reuses `_money()` verbatim; matches every existing convention in `reconciliation.py`/`ops/api/router.py`/invoice-generation's own DTOs.
  - M12 Platform-console UI: a new `/app/platform/margin` page + `PlatformMarginView.tsx` (mirrors `PlatformPlanCatalog.tsx`'s "thin Server Component page + `useQuery`/`bffGet` client component, `Card`/`PageHeader`/`Loading`/`Empty`/`ErrorState`" shape) — summary tiles (from `/summary`), a per-tenant/per-model table (from `/by-tenant-model`, `margin` cell renders "—" / "no cost data" when `has_provider_cost_data=false`, never "$0.00"), a trend chart (from `/trend`), and a tie-out section (from `/tie-out`). Added as the THIRD entry in the existing `PlatformNavGroup` allowlist (`PLATFORM_MARGIN_HREF`), same fail-closed `showPlatformNav` gate, zero changes to the two existing entries. No per-row evidence drill-down in this task (that idiom is `billing-ui`'s, per MILESTONE.md's UI/UX scope naming Billing nav group ≠ platform-console Margin page).
</must>

Reject:
<reject>
  - no/malformed bearer token on any margin endpoint -> "ERR_AUTH_INVALID_TOKEN"
  - caller authenticated but role is not superadmin -> "ERR_AUTH_FORBIDDEN"
  - `window=` not one of `day`/`week`/`month`, or `start=`/`end=` not a valid ISO date, on `/summary`, `/by-tenant-model`, or `/trend` -> "ERR_PAYLOAD_INVALID"
  - `/tie-out?period=` absent, not `YYYY-MM` shaped, or not a real calendar month -> "ERR_PAYLOAD_INVALID"
  - `/by-tenant-model?limit=` non-integer, `< 1`, or `> 100` -> "ERR_PAYLOAD_INVALID"
  - `/by-tenant-model?cursor=` undecodable / malformed / wrong shape -> "ERR_CURSOR_INVALID"
  - `?tenant_id=` present but not a valid UUID (any endpoint that accepts it) -> "ERR_PAYLOAD_INVALID"
  - any of the 4 reads exceeds its bounded query timeout -> "ERR_MARGIN_QUERY_TIMEOUT"
</reject>
After:
<after>
  - A superadmin opens `/app/platform/margin` and sees operator-wide margin tiles, a per-tenant/per-model table, a trend, and a tie-out check against issued invoices — every figure for a given window agreeing EXACTLY with `/ops/reconciliation`'s numbers for that same window, verifiable side-by-side.
  - Catalog-priced usage (the majority of all usage, R1) is always visibly labeled "no provider cost data" — never a fabricated $0 or estimated margin — so the dashboard is honest about what it does and does not know, by construction.
  - A tie-out mismatch (invoice total vs ledger sum) surfaces as `drift_detected` for a human to investigate — this task never auto-corrects, since it has no write path at all.
  - This task adds ZERO new tables and ZERO migrations — it is a pure read/aggregation layer over `usage_records` (existing) and `invoices`/`invoice_lines` (existing, frozen by invoice-generation).
  - A future provider that starts reporting `usage.cost` automatically gains real per-row margin visibility with zero code change here — the `cost_basis` split this task reuses is already generic.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **M3's "never estimate catalog-basis margin, show null instead" framing is the single lowest-confidence call in this draft** — lowest confidence because it is a genuine product-framing tradeoff, not a code-correctness question: the fintech-honest choice (never fabricate a number that could silently drift from reality, and never invent a second price path the milestone explicitly forbids) means the dashboard will show `margin: null`/"no cost data" for the OVERWHELMING MAJORITY of usage_records rows (every provider except OpenRouter), which could read as "the margin dashboard doesn't work for most of my traffic" to an operator on first look, even though that is the honest and correct state of the world today. If wrong: a future task adds a clearly-labeled, SEPARATE "estimated margin (catalog list-price basis)" column, sourced from a deliberate new re-resolution path — a real scope addition requiring its own spec, not a quick fix layered onto this contract. **Surfaced as the freeze flag.**
  - [ ] Whether `GET /admin/platform/margin/tie-out` without a `tenant_id` filter should include tenants with a `draft`-status invoice (there is none in practice today, since invoice-generation's auto-issue path inserts `status='issued'` directly — no draft→issued UPDATE exists, per that task's own §3 immutability note) — recommend treating `draft` identically to "no invoice row" (`pending_invoice`) since v1 never actually produces a `draft` row; confirm or deny at freeze (low-cost either way, would only matter if invoice-generation's lifecycle changes later).
  - [ ] Whether `/by-tenant-model` and `/trend` should default `tenant_id` to "all tenants" (current draft) vs REQUIRE it (forcing the operator to pick a tenant first) — recommend default-all with pagination as the honest MVP shape (matches `reconcile_by_tenant`'s own existing "all tenants" default); confirm or defer at freeze.
  - [ ] Whether the Margin page ships as part of THIS task (current draft, M12) vs is deferred entirely to `billing-ui` — MILESTONE.md line 38 explicitly names `margin-dashboard` (not `billing-ui`) as the owning task for "platform console" (Margin page), and line 16 names `billing-ui`'s UI/UX scope as the TENANT-facing "Billing nav group (Invoices · Credits · Plan & seats)" — a different surface entirely; recommend confirming this reading is correct (a scope-boundary confirmation, not a design ambiguity) rather than re-litigating it.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Margin summary matches /ops/reconciliation for the same window   # M1
  Given usage_records with a mix of cost_basis='provider' and 'catalog' rows in July 2026
  When a superadmin calls GET /admin/platform/margin/summary?window=month&start=2026-07-01&end=2026-08-01
  And the mTLS operator identically calls GET /ops/reconciliation?window=month&start=2026-07-01&end=2026-08-01
  Then provider_cost_total, billed_total, drift, unbilled_upstream_cost, unbilled_rows, and catalog_billed_total are byte-identical strings in both responses
  And no second SQL aggregation was written — both responses trace to the same reconcile_window call

Scenario: reconcile_by_tenant_model reuses the existing primitives, never resolve_markup_pct   # M2
  Given 3 usage_records rows across 2 tenants and 2 models in the window
  When the margin summary and by-tenant-model endpoints are generated with resolve_markup_pct monkeypatched to raise
  Then both responses return successfully with correct totals
  And resolve_markup_pct is never invoked

Scenario: Catalog-basis usage never gets a fabricated margin   # M3
  Given a tenant's July usage for model "gpt-4o" is entirely cost_basis='catalog' (provider_cost NULL on every row)
  When GET /admin/platform/margin/by-tenant-model?window=month is called
  Then that (tenant, "gpt-4o") item has has_provider_cost_data=false and margin=null
  And catalog_billed_total for that item equals the exact SUM(cost_usd) of those rows, never zero and never a guessed figure

Scenario: Provider-basis usage gets a real computed margin   # M3
  Given a tenant's July usage for model "some/openrouter-model" has cost_basis='provider' rows with cost_usd totaling 12.00 and provider_cost totaling 8.00
  When GET /admin/platform/margin/by-tenant-model?window=month is called
  Then that item has has_provider_cost_data=true and margin="4.00"

Scenario: Per-tenant-per-model grouping partitions correctly   # M4
  Given tenant A has usage on both "gpt-4o" and "claude-3" in July, tenant B has usage on "gpt-4o" only
  When GET /admin/platform/margin/by-tenant-model?window=month is called
  Then exactly 3 items are returned: (A, gpt-4o), (A, claude-3), (B, gpt-4o)
  And each item's billed_total equals the exact SUM(cost_usd) for that (tenant_id, model_id) pair alone

Scenario: Trend buckets by the window's own granularity   # M5
  Given a tenant has usage spread across 5 distinct days within window=day, start=2026-07-01, end=2026-07-06
  When GET /admin/platform/margin/trend?window=day&start=2026-07-01&end=2026-07-06 is called
  Then the response contains up to 5 points, one per calendar day with usage, each bucket_start at UTC midnight
  And each point's provider_cost_total/billed_total/catalog_billed_total is the exact per-day SUM, no float rounding drift

Scenario: Tie-out matches when the invoice reconciles to the ledger   # M6
  Given tenant T has an issued July invoice with raw_total_usd = 350.00000000, and usage_records for T in July sum to cost_usd = 350.00000000
  When GET /admin/platform/margin/tie-out?period=2026-07 is called
  Then tenant T's item has tie_out_status="matched", invoiced_raw_total_usd="350.00000000", ledger_billed_total_usd="350.00000000"

Scenario: Tie-out surfaces drift without correcting it   # M6
  Given tenant T has an issued July invoice with raw_total_usd = 350.00000000, but a data anomaly makes usage_records for T in July sum to cost_usd = 351.50000000
  When GET /admin/platform/margin/tie-out?period=2026-07 is called
  Then tenant T's item has tie_out_status="drift_detected"
  And neither the invoice row nor any usage_records row is modified by this read

Scenario: Tie-out reports pending_invoice, not drift, for an un-invoiced period   # M7
  Given tenant T has usage_records in August 2026 but no invoices row yet exists for (T, 2026-08-01)
  When GET /admin/platform/margin/tie-out?period=2026-08 is called
  Then tenant T's item has tie_out_status="pending_invoice", invoiced_total_usd=null, invoiced_raw_total_usd=null
  And ledger_billed_total_usd still reports the real SUM(cost_usd) for that tenant/period

Scenario: Bounded query timeout surfaces as a structured error   # M8
  Given the margin summary query exceeds its bounded asyncio.timeout
  When GET /admin/platform/margin/summary is called
  Then the response is 504 "ERR_MARGIN_QUERY_TIMEOUT"
  And no partial/inconsistent totals are returned

Scenario: Every margin read is audited   # M9
  Given a superadmin identity
  When they call GET /admin/platform/margin/summary?window=month
  Then exactly one audit_events row is written with action="platform.margin.view_summary", target_tenant_id=null
  And the row's metadata carries the resolved window bounds

Scenario: by-tenant-model list is keyset-paginated   # M10
  Given 120 distinct (tenant_id, model_id) buckets with usage in the window
  When GET /admin/platform/margin/by-tenant-model?window=month&limit=50 is called, then the returned next_cursor is used for a second call
  Then the first page returns 50 items with has_more=true, the second page returns the next 50 with no overlap
  And walking all pages to exhaustion yields exactly 120 items, none duplicated or dropped

Scenario: Money fields are exact decimal strings   # M11
  Given a usage_records row with cost_usd = 0.10000003
  When GET /admin/platform/margin/summary?window=month is called
  Then billed_total is the literal string "0.10000003", not a float-rounded "0.1"

Scenario: Margin page renders tiles, table, trend, and tie-out for a superadmin   # M12
  Given a superadmin is signed in
  When they navigate to /app/platform/margin
  Then the Platform nav group shows a third "Margin" entry alongside "Tenants" and "Plans"
  And the page renders summary tiles, a per-tenant/per-model table (catalog-only rows show "no cost data", never "$0.00"), a trend chart, and a tie-out section

Scenario: Margin page is invisible and unreachable for a non-superadmin   # M12
  Given a tenant admin (not superadmin) is signed in
  When they inspect the Platform nav group
  Then no "Margin" entry (nor "Tenants"/"Plans") is rendered
  When they navigate directly to /app/platform/margin by URL
  Then the page renders the standard ErrorState (the gateway's require_superadmin is the real, only enforcement point)

Scenario: no bearer token   # R1
  Given no Authorization header
  When calling GET /admin/platform/margin/summary
  Then the response is 401 "ERR_AUTH_INVALID_TOKEN"
  And no margin data is returned in the body

Scenario: authenticated but not superadmin   # R2
  Given a valid tenant JWT for a billing_admin/owner/admin (not superadmin)
  When calling any of the 4 margin endpoints
  Then the response is 403 "ERR_AUTH_FORBIDDEN"
  And no cross-tenant data is returned in the body

Scenario: invalid window/date is rejected   # R3
  Given an authenticated superadmin
  When calling GET /admin/platform/margin/summary?window=quarter (also: start=not-a-date)
  Then the response is 422 "ERR_PAYLOAD_INVALID"
  And no partial totals are returned

Scenario: malformed tie-out period is rejected   # R4
  Given an authenticated superadmin
  When calling GET /admin/platform/margin/tie-out?period=2026-13 (also: period=july-2026, also: period= absent)
  Then the response is 422 "ERR_PAYLOAD_INVALID"

Scenario: invalid limit on by-tenant-model is rejected   # R5
  Given an authenticated superadmin
  When calling GET /admin/platform/margin/by-tenant-model?limit=0 (also: limit=101, also: limit=abc)
  Then the response is 422 "ERR_PAYLOAD_INVALID"

Scenario: malformed cursor on by-tenant-model is rejected   # R6
  Given an authenticated superadmin
  When calling GET /admin/platform/margin/by-tenant-model?cursor=not-valid-base64
  Then the response is 422 "ERR_CURSOR_INVALID"
  And no partial page is returned

Scenario: invalid tenant_id filter is rejected   # R7
  Given an authenticated superadmin
  When calling GET /admin/platform/margin/by-tenant-model?tenant_id=not-a-uuid
  Then the response is 422 "ERR_PAYLOAD_INVALID"

Scenario: empty window returns explicit zeros, not an error   # edge/boundary
  Given no usage_records rows exist in the requested window at all
  When GET /admin/platform/margin/summary?window=month&start=2020-01-01&end=2020-02-01 is called
  Then the response is 200 with provider_cost_total="0", billed_total="0", catalog_billed_total="0", margin=null, has_provider_cost_data=false
  And by-tenant-model for the same window returns items=[], has_more=false
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Status: DRAFT
Least-sure flag surfaced at freeze: [spec] M3's "never estimate catalog-basis margin, render null + 'no cost data' instead of a fabricated figure" — the honest choice given only OpenRouter reports an authoritative provider_cost (R1/R2), but it means `margin` is `null` for the majority of usage_records rows today. Tin: confirm this framing, or direct a clearly-labeled, separately-scoped "estimated margin (catalog list-price basis)" column as a follow-on task instead.

```
GET /admin/platform/margin/summary?window=&start=&end=
  200 -> { window_from, window_to, provider_cost_total, billed_total, catalog_billed_total,
           margin, has_provider_cost_data, drift, unbilled_upstream_cost, unbilled_rows }
  401 -> { error: "ERR_AUTH_INVALID_TOKEN" }
  403 -> { error: "ERR_AUTH_FORBIDDEN" }
  422 -> { error: "ERR_PAYLOAD_INVALID" }
  504 -> { error: "ERR_MARGIN_QUERY_TIMEOUT" }

GET /admin/platform/margin/by-tenant-model?window=&start=&end=&limit=&cursor=&tenant_id=
  200 -> { items: TenantModelMarginItem[], next_cursor: str|null, has_more: bool }
  401 / 403 -> same as above
  422 -> { error: "ERR_PAYLOAD_INVALID" | "ERR_CURSOR_INVALID" }
  504 -> { error: "ERR_MARGIN_QUERY_TIMEOUT" }

GET /admin/platform/margin/trend?window=&start=&end=&tenant_id=
  200 -> { granularity: str, points: MarginTrendPoint[] }
  401 / 403 -> same as above
  422 -> { error: "ERR_PAYLOAD_INVALID" }
  504 -> { error: "ERR_MARGIN_QUERY_TIMEOUT" }

GET /admin/platform/margin/tie-out?period=YYYY-MM&tenant_id=
  200 -> { period_start, period_end, items: TieOutItem[] }
  401 / 403 -> same as above
  422 -> { error: "ERR_PAYLOAD_INVALID" }
  504 -> { error: "ERR_MARGIN_QUERY_TIMEOUT" }

TenantModelMarginItem: { tenant_id, model_id, provider_cost_total, billed_total, catalog_billed_total,
                          margin: str|null, has_provider_cost_data: bool, unbilled_upstream_cost, unbilled_rows }
MarginTrendPoint: { bucket_start, provider_cost_total, billed_total, catalog_billed_total,
                     margin: str|null, has_provider_cost_data: bool }
TieOutItem: { tenant_id, invoice_id: str|null, invoice_status: "issued"|"pending_invoice",
              invoiced_total_usd: str|null, invoiced_raw_total_usd: str|null,
              ledger_billed_total_usd, provider_cost_total_usd,
              drift_invoiced_vs_ledger: str|null, tie_out_status: "matched"|"pending_invoice"|"drift_detected" }
```

Schema: NO new tables, NO migration. Pure read/aggregation over existing `usage_records` (`tenant_id, model_id, cost_usd, provider_cost, cost_basis, created_at` — `usage/infrastructure/orm.py:UsageRecordRow`) and existing `invoices` (`tenant_id, period_start, period_end, status, total_usd, raw_total_usd` — `billing/infrastructure/orm.py:InvoiceRow`, frozen by invoice-generation §3, already merged on this branch).

Access pattern:
- `/summary` — `usage/application/reconciliation.py:reconcile_window(session, window_from, window_to, tenant_id=None)`, REUSED UNCHANGED (byte-identical call `/ops/reconciliation` already makes); `margin`/`has_provider_cost_data` are derived in the response-mapping layer from the existing `provider_cost_total`/`billed_total`/`unbilled_rows` fields, no new SQL.
- `/by-tenant-model` — new `reconcile_by_tenant_model(session, window_from, window_to) -> tuple[TenantModelReconciliation,...]` in the SAME module, `GROUP BY tenant_id, model_id`, filtered to `cost_basis='provider'` for `provider_cost_total`/`billed_total` and `cost_basis='catalog'` for `catalog_billed_total` (mirrors `reconcile_by_tenant`'s existing FILTER-clause shape); application-layer keyset slice over the aggregate ordered `(tenant_id, model_id)` ASC, `fetch limit+1`.
- `/trend` — new `reconcile_trend(session, window_from, window_to, granularity, tenant_id=None) -> tuple[MarginTrendPoint,...]`, `GROUP BY date_trunc(:granularity, created_at AT TIME ZONE 'UTC')` (granularity from `_compute_window_bounds`'s third return value — reused, not a new param), same `cost_basis` FILTER split as above, optional `WHERE tenant_id = :tid`.
- `/tie-out` — cross-tenant `SELECT * FROM invoices WHERE period_start = :period_start` (new, additive read — a new `InvoiceRepository` method or a standalone query, Build's discretion) LEFT-JOINed in the application layer against a `reconcile_window`-shaped per-tenant ledger aggregate for the same `[period_start, period_end)` (`period_end` = `period_start` + 1 calendar month, matching invoice-generation's own M1 convention exactly); a tenant present in the ledger aggregate but absent from the `invoices` result is `pending_invoice`.
- All 4 routes: `require_superadmin` first (`tenants/domain/authz.py`), then `emit_platform_audit(...)` (`audit/application/platform_audit.py`) on the success path before returning, `target_tenant_id=None`.

RBAC: role-only via `require_superadmin` — no new `Permission` enum member (mirrors `platform_plans_router`'s bulk-list routes, which are role-gated for the identical "no single target tenant" reason).

Error-catalog delta: `apps/gateway/src/gateway/core/error_catalog.py` gains one new `ErrorSpec`:
```
MARGIN_QUERY_TIMEOUT = ErrorSpec(504, "ERR_MARGIN_QUERY_TIMEOUT", "Margin query exceeded its time budget")
```
`PAYLOAD_INVALID` / `PAYLOAD_WINDOW_INVALID` / `PAYLOAD_START_DATE_INVALID` / `PAYLOAD_END_DATE_INVALID` / `CURSOR_INVALID` / `AUTH_TOKEN_MISSING` / `AUTH_FORBIDDEN` are reused verbatim (no new constants; all already wired to `ERR_PAYLOAD_INVALID`/`ERR_CURSOR_INVALID`/`ERR_AUTH_INVALID_TOKEN`/`ERR_AUTH_FORBIDDEN` respectively).

Registration: `apps/gateway/src/gateway/main.py` gains one `app.include_router(margin_router)` alongside the existing `platform_*` block (lines 1338-1343).

Frontend: `apps/dashboard/app/(app)/app/platform/margin/page.tsx` (thin Server Component, mirrors `platform/plans/page.tsx`) + `apps/dashboard/components/platform/PlatformMarginView.tsx` (mirrors `PlatformPlanCatalog.tsx`'s `useQuery`/`bffGet` shape) + `apps/dashboard/components/ui/app-shell.tsx` gains `PLATFORM_MARGIN_HREF = "/app/platform/margin"` as a third entry in the existing `PlatformNavGroup`, same `showPlatformNav` gate, zero changes to the two existing entries.

Glossary deltas: **has_provider_cost_data** — a boolean carried on every margin figure, true iff the underlying bucket contains at least one `cost_basis='provider'` usage row; false means `margin` is `null` by contract, never a fabricated or zeroed figure. **tie-out** — the read-only three-way comparison of `usage_records`-ledger billed total, `invoices.raw_total_usd`, and `usage_records`-ledger provider cost for one UTC calendar month, surfacing `matched`/`pending_invoice`/`drift_detected` without ever writing.

Reported: no — pending Tin's freeze review.
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
