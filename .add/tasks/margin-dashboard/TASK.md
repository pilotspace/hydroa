# TASK: Operator margin dashboard (provider cost vs billed vs invoiced)

slug: margin-dashboard · created: 2026-07-12 · stage: production
sensitivity: mechanical
milestone: monetization-core
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: verify   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
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

Status: FROZEN @ v1 — approved by Tin Dang
Least-sure flag surfaced at freeze: [spec] M3's "never estimate catalog-basis margin, render null + 'no cost data' instead of a fabricated figure" — the honest choice given only OpenRouter reports an authoritative provider_cost (R1/R2), but it means `margin` is `null` for the majority of usage_records rows today. Tin: confirm this framing, or direct a clearly-labeled, separately-scoped "estimated margin (catalog list-price basis)" column as a follow-on task instead.

DECIDED at freeze review (2026-07-12, Tin): honest-null CONFIRMED for this task, AND the clearly-labeled
"estimated margin (catalog list-price basis)" column is QUEUED as a separate P2 follow-on task
(recorded via add.py todo). Open questions resolved (orchestrator as project lead, Tin offered
override): (1) draft invoices count under `pending_invoice` status; (2) default view = all tenants;
(3) margin-dashboard owns the platform Margin page.

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

Coverage target: 90% (backend `margin_router.py` + `reconciliation.py` additions); dashboard
`PlatformMarginView.tsx` covered by scenario-mapped Vitest cases, no numeric coverage gate
locally enforced for the dashboard app.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_m1_summary_matches_ops_reconciliation: Given mixed provider/catalog usage / When GET
    /admin/platform/margin/summary / Then its 6 shared fields byte-match a direct
    reconcile_window call for the same window · covers: M1
  - test_m1_summary_via_ops_router_matches_margin_summary: Given seeded usage / When summary
    is fetched with reconcile_window monkeypatch-traced / Then exactly one reconcile_window
    call fires (no second aggregation) · covers: M1, M2
  - test_m2_never_calls_resolve_markup_pct: Given mixed-basis usage / When summary +
    by-tenant-model are called with resolve_markup_pct monkeypatched to raise / Then both
    succeed and it is never invoked · covers: M2
  - test_m3_catalog_basis_never_fabricated_margin: Given a (tenant, model) with only
    cost_basis='catalog' rows / When by-tenant-model is called / Then has_provider_cost_data
    is false, margin is null, catalog_billed_total is the exact SUM · covers: M3
  - test_m3_provider_basis_real_computed_margin: Given cost_basis='provider' rows with known
    billed/provider sums / When by-tenant-model is called / Then has_provider_cost_data is
    true and margin equals billed-provider exactly · covers: M3
  - test_m4_per_tenant_per_model_grouping: Given 2 tenants x 2 models mixed / When
    by-tenant-model is called / Then exactly the 3 real (tenant,model) pairs are returned,
    each with its own isolated totals · covers: M4
  - test_m5_trend_buckets_by_day_granularity: Given usage spread over 5 days / When trend is
    called with window=day / Then up to 5 UTC-midnight buckets are returned, each an exact
    per-day SUM · covers: M5
  - test_m6_tie_out_matched: Given an issued invoice whose raw_total_usd equals the ledger sum
    / When tie-out is called / Then tie_out_status="matched" · covers: M6
  - test_m6_tie_out_drift_detected_never_corrects: Given an issued invoice that does NOT match
    the ledger sum / When tie-out is called / Then tie_out_status="drift_detected" and neither
    the invoice nor usage_records rows are modified · covers: M6
  - test_m7_tie_out_pending_invoice_for_uninvoiced_period: Given usage with no invoices row for
    that period / When tie-out is called / Then tie_out_status="pending_invoice", invoiced
    fields null, ledger_billed_total_usd still real · covers: M7
  - test_m8_query_timeout_maps_to_504: Given a usage_records SELECT forced to raise
    TimeoutError / When summary is called / Then 504 ERR_MARGIN_QUERY_TIMEOUT · covers: M8
  - test_m9_summary_read_is_audited: Given a superadmin / When summary is called / Then
    exactly one new audit_events row (action=platform.margin.view_summary,
    target_tenant_id=null) with resolved window metadata · covers: M9
  - test_m10_by_tenant_model_keyset_pagination: Given 120 (tenant,model) buckets / When pages
    are walked via next_cursor to exhaustion / Then exactly 120 items, no dupes/drops, page 1
    has_more=true at limit=50 · covers: M10
  - test_m11_money_fields_are_exact_decimal_strings: Given cost_usd=0.10000003 / When summary
    is called / Then billed_total is the literal string "0.10000003" · covers: M11
  - test_r1_no_bearer_token: Given no Authorization header / When summary is called / Then 401
    ERR_AUTH_INVALID_TOKEN, no margin fields leaked · covers: R:no-token
  - test_r2_non_superadmin_forbidden (parametrized x4 endpoints): Given a valid owner JWT /
    When each of the 4 endpoints is called / Then 403 ERR_AUTH_FORBIDDEN, no cross-tenant data
    leaked · covers: R:not-superadmin
  - test_r3_invalid_window_rejected / test_r3_invalid_start_date_rejected: Given
    window=quarter or start=not-a-date / When summary is called / Then 422
    ERR_PAYLOAD_INVALID · covers: R:invalid-window
  - test_r4_malformed_tie_out_period_rejected (parametrized): Given period=2026-13 /
    july-2026 / absent / When tie-out is called / Then 422 ERR_PAYLOAD_INVALID · covers:
    R:invalid-period
  - test_r5_invalid_limit_rejected (parametrized 0/101/abc): Given a bad limit / When
    by-tenant-model is called / Then 422 ERR_PAYLOAD_INVALID · covers: R:invalid-limit
  - test_r6_malformed_cursor_rejected: Given cursor=not-valid-base64!! / When by-tenant-model
    is called / Then 422 ERR_CURSOR_INVALID, no partial page · covers: R:invalid-cursor
  - test_r7_invalid_tenant_id_filter_rejected: Given tenant_id=not-a-uuid / When
    by-tenant-model is called / Then 422 ERR_PAYLOAD_INVALID · covers: R:invalid-tenant-id
  - test_edge_empty_window_explicit_zeros: Given no usage_records at all / When summary +
    by-tenant-model are called / Then 200 with explicit "0"/null/false fields and items=[] ·
    covers: edge
  - test_reconcile_by_tenant_model_shape / test_reconcile_trend_shape: direct pure-aggregate
    coverage of the two new reconciliation.py primitives (mirrors
    reconciliation_aggregate's own direct-call convention) · covers: M4, M5
  - test_renders_summary_tiles_table_trend_and_tie_out (dashboard): Given all 4 endpoints
    mocked / When PlatformMarginView renders / Then tiles + table rows + trend figure +
    tie-out statuses all appear · covers: M12
  - test_catalog_only_row_shows_no_cost_data_badge_never_dollar_zero (dashboard): Given
    has_provider_cost_data=false / When rendered / Then the margin value renders the "No cost
    data" badge, never a dollar figure · covers: M3, M12
  - test_summary_no_cost_data_renders_no_cost_data_not_zero (dashboard): same rule at the
    summary-tile level · covers: M3, M12
  - test_shows_standard_error_state_on_403_non_superadmin (dashboard): Given all 4 endpoints
    403 / When rendered / Then the standard ErrorState (role=alert) appears once, no data
    leak · covers: R:not-superadmin, M12
  - test_margin_nav_visible_for_superadmin_desktop_and_mobile /
    test_margin_nav_hidden_for_non_superadmin_roles (dashboard): the third PlatformNavGroup
    entry, same allowlist-gated pattern as Tenants/Plans · covers: M12
</test_plan>

Tests live in: `apps/gateway/tests/margin_dashboard/` (32 tests, backend) ·
`apps/dashboard/tests/platform-margin.test.tsx` (8 tests, dashboard) · MUST run red (missing
implementation) before Build — confirmed: backend suite failed at collection with
`ImportError: cannot import name 'MarginTrendPoint' from
'gateway.usage.application.reconciliation'`; dashboard suite failed to resolve
`@/components/platform/PlatformMarginView` — both the honest missing-implementation red, not
a broken harness.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
`apps/gateway/src/gateway/usage/application/reconciliation.py` (additive: 3 new dataclasses +
3 new functions, existing functions byte-unchanged) ·
`apps/gateway/src/gateway/usage/api/margin_router.py` (new file) ·
`apps/gateway/src/gateway/core/error_catalog.py` (additive: 1 new ErrorSpec) ·
`apps/gateway/src/gateway/main.py` (2-line additive: import + include_router) ·
`apps/gateway/tests/margin_dashboard/` (new dir) ·
`apps/dashboard/components/platform/PlatformMarginView.tsx` (new file) ·
`apps/dashboard/app/(app)/app/platform/margin/page.tsx` (new file) ·
`apps/dashboard/components/ui/app-shell.tsx` (additive: 1 new nav entry, existing 2 untouched)
· `apps/dashboard/tests/platform-margin.test.tsx` (new file).

Strategy (ordered batches): 1. Read the reconciliation/ops/platform-router/invoice-repository
ground files directly (confirm every §0 anchor resolves as claimed). 2. Write the full §4 red
suite (backend HTTP + pure-aggregate + dashboard) against the frozen §3 shapes; confirm RED
for the right reason (ImportError / unresolved-import, not a broken harness). 3. Add the 3
additive reconciliation.py primitives (`reconcile_by_tenant_model`, `reconcile_trend`,
`tie_out_ledger_by_tenant`) — same Decimal/`_money()`/half-open-window idioms as the existing
functions, zero changes to them. 4. Add `MARGIN_QUERY_TIMEOUT` to error_catalog.py. 5. Write
`margin_router.py` (4 routes, all `require_superadmin`-first + `emit_platform_audit`-on-success)
and register it in main.py. 6. Green the backend suite; fix any Decimal-scale test-assertion
mismatches uncovered by real Postgres NUMERIC behavior (never weaken an assertion — correct a
wrong precision assumption against the real column scale). 7. Build the dashboard
`PlatformMarginView` (summary tiles / table / trend / tie-out) + page + nav entry; green the
dashboard suite, scoping ambiguous multi-match text queries precisely rather than loosening
what they verify. 8. Lint/typecheck (ruff, pyright, tsc, eslint) on every touched file.

Persona (required): Billing Precision Engineer (`.add/personas/billing-precision-engineer.md`)
— every money field routes through `_money()`/`Decimal`, never `float`; every cost figure
carries its `has_provider_cost_data` provenance flag so a null margin is always an EXPLAINED
"unknown", never a silent/fabricated zero.
Spawn isolation (default): n/a — no subagent spawned this build (single-agent execution in the
dedicated `build-margin-dashboard` worktree).
Known-problem fixes: Postgres NUMERIC SUM preserves the summed column's declared scale
(`cost_usd` Numeric(14,8) -> 8-decimal strings, `provider_cost` Numeric(20,10) -> 10-decimal
after Decimal subtraction) — test assertions must expect the REAL scale, not a hand-typed
2-decimal guess → fixed by running the suite and correcting expectations against actual
Postgres output, never by truncating/rounding in the response layer (M11 forbids it). ·
`invoices.period_start`/`period_end` are TIMESTAMPTZ in the real migration but a NAIVE
`DateTime` in the fast `create_all()` test schema (SQLAlchemy infers no explicit type from
`InvoiceRow`'s bare `Mapped[datetime]` annotation) — `invoice_generator.py`'s own
`_as_naive_utc` write convention is mirrored in both the test seed helper and
`_fetch_invoices_for_period`'s read side so the equality match holds under either schema.
Strategy actually used: as planned (all 8 batches executed in order, no deviation).
Safety rule (feature-specific): this router has NO write path at all — every one of the 4
routes is a pure aggregate read (2 SELECT-only Postgres aggregates + a superadmin-audited
side-effect-free response mapping); the tie-out's `drift_detected` case is a read-time
comparison that NEVER writes to `invoices` or `usage_records` (proven directly by
`test_m6_tie_out_drift_detected_never_corrects`, which re-reads the invoice row after the
call and asserts it is byte-unchanged).
Code lives in: `apps/gateway/src/gateway/usage/` (backend) · `apps/dashboard/components/platform/`
+ `apps/dashboard/app/(app)/app/platform/margin/` (dashboard).
Constraints: do NOT change any test or the contract; allow-list packages only (none added —
`recharts`/`@tanstack/react-query`/FastAPI/SQLAlchemy already in use); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token with "/" = project root · a bare name = sibling of the previous token's dir · a DIRECTORY token covers its whole subtree (diverges from §4's non-recursive counting) · outside-root resolutions drop fail-closed · absent line = UNDECLARED (grandfathered, never retro-red) · enforcement live: a completing verify gate refuses an out-of-scope build (scope_violation → self-heal); check surfaces it. EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

Persona: Billing Precision Engineer (`.add/personas/billing-precision-engineer.md`, flow:
build+advisor) — refute-the-green stance; NEVER a fabricated margin, superadmin-only airtight.

- [x] all tests pass — 32/32 frozen backend + 18/18 NEW adversarial backend probes
  (`tests/margin_dashboard/test_verify_adversarial.py`) = 50/50, re-run twice clean (no
  flakes); 6/6 frozen dashboard (`vitest run tests/platform-margin.test.tsx`)
- [x] coverage did not decrease — `margin_router.py` 95%→98% (adversarial probes added
  branch coverage on the 3 previously-unexercised timeout paths); `reconciliation.py` held at
  76% (uncovered lines are pre-existing `audit_cost_basis_breaches`/
  `audit_unrecovered_disconnects`/legacy `reconcile_by_tenant`, out of this task's scope)
- [x] no test or contract was altered during build — `git diff d586348 HEAD -- tests/margin_dashboard/test_margin_dashboard.py tests/platform-margin.test.tsx` = empty (both files created once, never touched again); `git log -p .add/tasks/margin-dashboard/TASK.md` shows only additive `+` lines for the §3 contract block, no `-` removals/edits post-freeze
- [x] the green was EARNED, not gamed — see Refute-read verdict below (EARNED)
- [x] concurrency / timing of the risky operation is safe — pure read-only aggregation, no
  write path at all; `asyncio.timeout` wiring independently confirmed REAL on all 4 routes
  (not just `/summary`, which is all the frozen suite covers) via low-level
  `AsyncSession.execute` fault injection
- [x] no exposed secrets, injection openings, or unexpected dependencies — all SQL params
  bound via `text()` placeholders except the `granularity` string, which is interpolated only
  after a hard whitelist check (`{"day","week","month"}`); zero new pip/npm dependencies;
  grep for stray `float(` in `margin_router.py`/`reconciliation.py` = 0 hits (Decimal
  discipline held end-to-end)
- [x] layering & dependencies follow CONVENTIONS.md — additive-only within `usage/`
  (application+api), one cross-context read into `billing/infrastructure/orm.py:InvoiceRow`
  (precedented by invoice-generation's own symmetric read), `main.py`/`error_catalog.py`
  touched only additively (2-line + 1-ErrorSpec)
- [ ] a person reviewed and approved the change — pending Tin's review of this record

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] `/admin/platform/margin/summary`'s 6 shared fields are byte-identical to a direct
  `reconcile_window` call for the same window — confirmed by
  `test_m1_summary_matches_ops_reconciliation` (asserts string equality field-by-field
  against a live `reconcile_window` call, not a fixture).
- [x] `resolve_markup_pct` is never imported or called by any of the 4 margin routes —
  confirmed by `test_m2_never_calls_resolve_markup_pct` (monkeypatches it to raise
  `AssertionError`; both endpoints still succeed).
- [x] A `cost_basis='catalog'`-only (tenant, model) bucket renders `has_provider_cost_data:
  false` and `margin: null` — NEVER `margin: 0` or a guessed figure — confirmed by
  `test_m3_catalog_basis_never_fabricated_margin` (backend) and
  `test_catalog_only_row_shows_no_cost_data_badge_never_dollar_zero` /
  `test_summary_no_cost_data_renders_no_cost_data_not_zero` (dashboard — the "No cost data"
  badge, never a dollar figure, at both the summary tile and table-cell call sites).
- [x] A tie-out mismatch surfaces as `drift_detected` without writing to `invoices` or
  `usage_records` — confirmed by `test_m6_tie_out_drift_detected_never_corrects` (re-reads
  `invoices.raw_total_usd` before/after the call and asserts byte-equality).
- [x] Every one of the 4 routes is gated by `require_superadmin` (never `require_ops`) and a
  non-superadmin gets 403 with zero data leaked — confirmed by
  `test_r2_non_superadmin_forbidden` (parametrized across all 4 endpoints, asserts
  `"items"`/`"provider_cost_total"` absent from the response body).
- [x] Every margin read is audited exactly once with `target_tenant_id=None` — confirmed by
  `test_m9_summary_read_is_audited` (counts `audit_events` before/after, asserts the delta is
  exactly 1 and the row's `tenant_id` is NULL).
- [x] `by-tenant-model` pagination walks to exhaustion with no duplicate/dropped item across
  120 seeded buckets — confirmed by `test_m10_by_tenant_model_keyset_pagination` (accumulates
  a `set` of `(tenant_id, model_id)` across pages, asserts no overlap and a final count of
  exactly 120).
- [x] A bounded query timeout surfaces as 504 `ERR_MARGIN_QUERY_TIMEOUT`, never a 500 or a
  partial body — confirmed by `test_m8_query_timeout_maps_to_504` (forces a real `TimeoutError`
  from a monkeypatched `AsyncSession.execute` on the `usage_records` query).
- [x] The Margin page is the third `PlatformNavGroup` entry, visible only for `role="superadmin"`
  — confirmed by `test_margin_nav_visible_for_superadmin_desktop_and_mobile` /
  `test_margin_nav_hidden_for_non_superadmin_roles` (desktop + mobile nav, 5 non-superadmin
  role values including `owner`), with the existing "Tenants"/"Plans" entries asserted
  unchanged in the same test.
- [x] Zero new tables, zero migrations — confirmed by `grep -rl "invoices\|usage_records" apps/gateway/migrations/versions/` showing no new revision file added by this build; the git diff for this task touches no `migrations/` path.
- [x] **NEW (verify-added) — mixed-provenance (tenant, model) bucket never blends catalog
  revenue into the provider-basis margin**: `test_verify_mixed_provenance_bucket_no_blend`
  seeds 2 `cost_basis='provider'` rows (billed 10.00, provider_cost 6.00) AND 2
  `cost_basis='catalog'` rows (14.00) on the SAME (tenant, model) bucket in the SAME window —
  `by-tenant-model` returns `billed_total="10.00000000"`, `margin="4.00000000"` (provider-only),
  `catalog_billed_total="14.00000000"` reported separately, never folded in. This is the
  scenario the frozen suite's M3 tests never exercise (they use catalog-ONLY or provider-ONLY
  buckets, never a genuinely mixed one) — the strongest possible refutation attempt on the
  task's central honesty rule, and it held.
- [x] **NEW (verify-added) — Decimal fidelity at real column scale (8dp), no float drift**:
  `test_verify_decimal_fidelity_no_float_drift` sums 3 rows whose total is not exactly
  representable in IEEE754 float at this precision; `billed_total`/`provider_cost_total`/
  `margin` all match the Decimal-exact expected value byte-for-byte on the wire.
  `test_verify_tie_out_decimal_exactness` confirms the same at the tie-out layer
  (116.66666667, `tie_out_status="matched"` exactly, not within a tolerance).
- [x] **NEW (verify-added) — half-open window boundary is exact**:
  `test_verify_window_boundary_half_open_exact` seeds one row exactly at `window_from` and one
  exactly at `window_to`; only the `window_from` row is counted (`billed_total="1.00000000"`),
  confirming `[from, to)` semantics hold at the literal instant, not just within the interior.
- [x] **NEW (verify-added) — timeout wiring is real on ALL 4 routes**, not only `/summary`
  (the frozen suite's only timeout test): `test_verify_timeout_wiring_by_tenant_model` /
  `_trend` / `_tie_out` each force a real `TimeoutError` from `AsyncSession.execute` on the
  `usage_records` query and confirm 504 `ERR_MARGIN_QUERY_TIMEOUT` on all three previously
  UNTESTED routes.
- [x] **NEW (verify-added) — `?tenant_id=` filter isolation, no cross-tenant leak**:
  `test_verify_tenant_id_filter_isolates_by_tenant_model` /
  `test_verify_tenant_id_filter_isolates_tie_out` seed 2 tenants, filter to one, confirm the
  other's figures never appear in the response — untested by the frozen suite.
- [x] **NEW (verify-added) — authz byte-shape across all 4 routes** (401 no-token, 403
  non-superadmin): `test_verify_no_token_401_byte_shape` /
  `test_verify_non_superadmin_403_byte_shape` (parametrized ×4 routes each) assert zero
  financial keys (`items`/`points`/`provider_cost_total`/`billed_total`/`margin`/
  `catalog_billed_total`/`ledger_billed_total_usd`) leak into the error body on either path.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `margin_router` imported + `app.include_router(margin_router)` in
  `main.py:225,1377`; `MARGIN_QUERY_TIMEOUT` ErrorSpec present in `error_catalog.py:1007-1008`
  and imported/used in `margin_router.py`; `reconcile_by_tenant_model`/`reconcile_trend`/
  `tie_out_ledger_by_tenant` all imported and called from their respective routes (grep-
  confirmed, no orphaned import). Frontend: `PLATFORM_MARGIN_HREF` wired into
  `PlatformNavGroup` (`app-shell.tsx:219,246-247`), gated by the existing `showPlatformNav`.
- [x] DEAD-CODE (code) — no unused new symbol found: every new dataclass
  (`TenantModelReconciliation`/`MarginTrendPoint`/`TenantLedgerPeriod`) and every new function
  is referenced from `margin_router.py`; no orphaned schema class in either file.
- [ ] SEMANTIC — n/a (this is a code task, WIRING+DEAD-CODE path applies, not prose)

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by direct
  grep/read at HEAD (`06682f1`): `_compute_window_bounds` (`usage/api/router.py:196`),
  `require_superadmin` (`tenants/domain/authz.py:329`), `emit_platform_audit`
  (`audit/application/platform_audit.py:36`), `InvoiceRow.period_start/period_end/status/
  total_usd/raw_total_usd` (`billing/infrastructure/orm.py:78-101`), `PlatformNavGroup` /
  `showPlatformNav` / new `PLATFORM_MARGIN_HREF` (`app-shell.tsx:200-247`) — all resolve
  exactly as §0/§3 describe.
- [x] no anchor moved/renamed since Ground SHA (`71641a9`) — all cited symbols are at the
  same relative_path; only additive lines were introduced around them.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: **EARNED**
By: self (add-verify, billing-precision-engineer persona) · adversarially checked: mixed-
provenance same-bucket blending (M3's strongest case, untested by the frozen suite), Decimal
fidelity at real 8dp column scale under a value not exactly representable in float, half-open
window-boundary exactness at the literal instant, timeout wiring on the 3 routes the frozen
suite never exercises, cross-tenant `tenant_id=` filter isolation, and 401/403 byte-shape
non-leakage across all 4 routes. 18 new tests, independently authored (not copied from the
build's own suite), all reproduce the SAME invariants the build claims — no overfit to the
build's own fixtures found. One genuine spec-vs-implementation gap found (Finding 🟡1 below)
via a deliberately adversarial edge case (all-zero provider row) the frozen suite's fixtures
never construct — this is exactly the kind of gap a refute-read is supposed to surface, and it
does NOT indicate a gamed/vacuous suite (the frozen tests assert real live-DB values, not
mocks). Not a cheat; a real, narrow, safe-direction (never-fabricates) inconsistency.

### Findings
🟡 **Finding 1 — `/summary`'s `has_provider_cost_data` is a heuristic, not the honest
`COUNT(cost_basis='provider')` the other 3 endpoints use.** `get_margin_summary` derives it as
`provider_cost_total != 0 OR billed_total != 0 OR unbilled_rows > 0` because
`ReconciliationSummary` (the reused, unmodified `reconcile_window` primitive) has no
provider-row-count field to draw from. `by-tenant-model`/`trend` instead COUNT actual
`cost_basis='provider'` rows (honest, matches M3's literal text: "true iff the bucket has ≥1
cost_basis='provider' row"). Edge case: a window whose ONLY provider-basis row(s) have
`provider_cost=0 AND cost_usd=0` (a genuinely free upstream call) makes `/summary` report
`has_provider_cost_data=false`/`margin=null` even though a real provider row — and a real,
computed margin of exactly 0 — exists. Direction of error is SAFE (under-claims certainty
rather than fabricating a wrong number, consistent with M3's honesty spirit), so this is NOT
an M3 violation, but it IS a genuine inconsistency between `/summary` and the other 3
endpoints' definition of the same field name. Confirmed by
`test_verify_summary_has_provider_cost_data_zero_cost_provider_row` (passes, documenting
current behavior). Recommend: either add a provider-row COUNT to `reconcile_window`/
`ReconciliationSummary` (matches the other 3 endpoints' honest definition) or explicitly
document `/summary`'s field as a distinct, coarser proxy — non-blocking, narrow edge case
(free-tier OpenRouter usage), does not affect any currently-observed cost value.

🟡 **Finding 2 — the `_as_naive_utc` idiom's docstring claim ("asyncpg expects NAIVE UTC
datetimes") is factually wrong, and this task's new tie-out code extends the same pattern
into a fresh money-critical path.** Direct probe against a REAL alembic-migrated schema
(`gateway_migrations_test_verify_margin`, genuine `TIMESTAMPTZ` columns — confirmed
`usage_records.created_at`, `invoices.period_start/period_end/issued_at` are ALL
`timestamp with time zone` in the real migration, NOT the naive type the `create_all()` test
schema infers) shows asyncpg interprets a naive Python datetime bound to a TIMESTAMPTZ column
as being in the **process's local OS timezone**, not UTC, before storing the converted
instant: binding `datetime(2026,8,1,0,0,0)` (no tzinfo) under a UTC+7 host clock stored
`2026-07-31T17:00:00+00:00` — a real 7-hour corruption of the intended UTC instant
(reproduced directly via asyncpg, bypassing the app entirely — script available on request).
Because `invoice_generator.py`'s write path and `margin_router.py`'s/`reconciliation.py`'s new
read paths (`_fetch_invoices_for_period`, `tie_out_ledger_by_tenant`, `reconcile_trend`, etc.)
ALL apply the identical naive-strip conversion within the SAME process, the round-trip is
SELF-CONSISTENT as long as the gateway container's OS-level timezone is UTC — which is the
Debian-bookworm-slim default (this repo's Dockerfile sets no `TZ`, so the base image's default
almost certainly applies) — but this is an **unpinned, undocumented environmental assumption**
for a financial-reconciliation code path, and it is INVISIBLE to the entire test suite, which
runs exclusively against `create_all()`'s inferred NAIVE (non-tz) columns — no test in this
repo, frozen or adversarial, can currently detect a regression here regardless of host clock.
This is INHERITED pre-existing debt (the `usage_records.created_at` TIMESTAMPTZ + naive-strip
pattern predates this task, already used by `reconcile_window`/`reconcile_by_tenant`) — this
task did not introduce the pattern, but its 3 new functions (`reconcile_by_tenant_model`,
`reconcile_trend`, `tie_out_ledger_by_tenant`) and `_fetch_invoices_for_period` all extend it
into the tie-out feature, whose entire purpose is catching financial drift — a path where a
silent timezone-dependent instant-shift could itself mask or fabricate a `drift_detected`.
Non-blocking for THIS task (does not fail under the expected UTC-default deployment, and is
not a regression this task caused), but worth escalating as a repo-wide hardening item:
(a) pin `TZ=UTC` explicitly in the Dockerfile/process startup with a runtime assertion, or
(b) the more robust fix — bind timezone-AWARE UTC datetimes to every TIMESTAMPTZ query
parameter instead of stripping tzinfo (asyncpg handles aware datetimes correctly regardless of
host clock; the naive-strip was based on an incorrect assumption about asyncpg's semantics).

💭 **Note 3** — monkeypatching `gateway.usage.application.reconciliation.<fn>` does NOT affect
`margin_router.py`'s already-bound `from ... import <fn>` reference (a Python name-binding
trap, not a code defect) — my first attempt at cross-route timeout probes silently no-opped
this way before I switched to the same low-level `AsyncSession.execute` fault-injection idiom
the frozen suite's `test_m8` already uses correctly. Noted for future test authors on this
router, not a build finding.

💭 **Note 4** — TASK.md §4 describes "8 tests, dashboard" for `platform-margin.test.tsx`; the
actual file has 6 `it()` blocks (`vitest run` reports "6 passed"). All M12-listed assertions
ARE present (e.g. the 5-role non-superadmin check appears to be looped inside one `it()`
rather than 5 separate blocks) — a bookkeeping mismatch in the test_plan count, not a coverage
gap.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
Advisor: self (add-verify)
1. Security: **CLEAR** — all SQL bound via `text()` placeholders except `granularity`, which
   is whitelist-validated (`{"day","week","month"}`) before interpolation; every route is
   `require_superadmin`-gated with confirmed zero-leak 401/403 byte shape; every read is
   audited; no secrets, no new dependencies.
2. Concurrency: **CLEAR** — pure read-only aggregation, zero write path; bounded
   `asyncio.timeout` confirmed real on all 4 routes (adversarially re-verified beyond the
   frozen suite's `/summary`-only coverage).
3. Architecture: **CLEAR, with RESIDUE** — layering is correct (additive-only, one resolver,
   no second price/aggregation path, thin router). Residue = Finding 🟡2 (inherited
   TIMESTAMPTZ/naive-datetime coupling, extended by this task's tie-out code into a
   money-critical path, invisible to the current test suite) — a real architectural fragility
   worth a dedicated hardening task, not a defect this task introduced or is positioned to fix
   within its own additive-only scope.
Verdict: **PASS**
Residue: Finding 🟡2 (TIMESTAMPTZ/naive-datetime environmental coupling, inherited +
extended, non-blocking under expected UTC-default deployment) · Finding 🟡1 (`/summary`
has_provider_cost_data heuristic vs honest-count inconsistency, narrow safe-direction edge
case)
Binding: yes — mechanical

### GATE RECORD
Reported: no — this record is the verify agent's evidence; the orchestrator renders the gate
report and records the final outcome (per dispatch instruction: "Do NOT gate — orchestrator
gates").
Outcome: **RECOMMENDED — PASS** (no security or concurrency finding; 2 non-blocking 🟡
findings recorded above, both safe-direction/inherited, neither meets HARD-STOP or
RISK-ACCEPTED criteria) — orchestrator to record the binding outcome.
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
