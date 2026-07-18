# TASK: Compliance report center console surface (generate/download + REAL server-side monthly schedule+delivery of the Art. 12 bundle)

slug: compliance-report-center · created: 2026-07-14 · revised: 2026-07-14 (scheduling scope expansion, Tin-authorized) · stage: production
milestone: eu-ai-act-readiness
autonomy: conservative
risk: high
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/compliance/api/router.py:get_art12_bundle` (mounted `GET /admin/compliance/art12-bundle`) — the backend dependency, ALREADY SHIPPED + integrated (art12-record-keeping-preset, FROZEN @ v1). Response shapes read directly from this file: `Art12BundleResponse` (l.172-177: `cover: CoverModel`, `sections: SectionsModel`, `bundle_token: str | null`), `CoverModel` (l.121-134: `bundle_id, generated_at, tenant_id, tenant_name, period: PeriodModel, residency_pin, zdr_state: ZdrStateModel, retention_window_days, guardrail_configs_snapshot, default_tier, format_version`), `SectionsModel` (l.164-169: `audit_events: AuditSectionModel`, `request_log_metadata: LogSectionModel`, `usage_lineage: UsageSectionModel`), each section model (l.137-161: `items, next_cursor, has_more, note`). `since`/`until` are BOTH REQUIRED query params (`_parse_required_period`, l.215-223); `since>until` -> `PAYLOAD_INVALID` (422). `bundle_token` PINS the cover snapshot across the whole continuation walk (`_mint_cover`/`_cover_from_token`, l.322-377) and MUST echo the SAME since/until on every continuation call or the route rejects with `ERR_CURSOR_INVALID` (M14, l.459-463). This task's frontend is a pure consumer of this route — it introduces NO new backend endpoint, NO new table, NO new write path beyond what the route itself already does (its own fire-and-forget `compliance.art12_bundle` audit write, l.643-670, unchanged).
- `.add/tasks/art12-record-keeping-preset/TASK.md §3` (FROZEN @ v1) — the authoritative contract this task consumes verbatim; error codes reused unchanged: `ERR_AUTH_INVALID_TOKEN` (401), `ERR_AUTH_FORBIDDEN` (403), `ERR_PAYLOAD_INVALID` (422), `ERR_CURSOR_INVALID` (422), `ERR_EXPORT_TIMEOUT` (504).
- `apps/dashboard/components/settings/SettingsPage.tsx:SettingsPage` — the `/settings` tabbed hub. Currently `<Tabs defaultValue="cache">` (l.29), UNCONTROLLED — no `value`/`onValueChange` passed, so today there is genuinely no deep-link into a specific tab. RESOLVED (not a hard limitation): `apps/dashboard/components/platform/PlatformTenantDetail.tsx:PlatformTenantDetail` (l.66-107) already proves the exact reusable pattern in THIS codebase — `useRouter`/`useSearchParams` from `next/navigation`, a `TabValue`/`isTabValue` guard, a lazy-`useState` initializer seeded from `searchParams.get("tab")`, an "adjust state during render" re-sync block (l.82-87, explicitly NOT a `useEffect`, citing the same seed/reseed convention `CacheSettings.tsx`/`GuardrailSettings.tsx` already use) for browser back/forward, and `handleTabChange` (l.101-107) that both `setActiveTab` and `router.replace(?tab=...)`. This task lifts `SettingsPage.tsx`'s `Tabs` to the SAME controlled-URL idiom, additively — every existing bare `/settings` link still resolves to `defaultValue`-equivalent "cache" behavior (§1 M12/R9).
- `apps/dashboard/components/settings/RetentionZdrSettings.tsx:RetentionZdrSettings` — the "Data & residency" tab's own panel idiom this task extends the STYLE of (not the file): `fieldset`+`legend` grouping (l.262-343), `useQuery`/`useMutation` + `BffError`-typed `getErrorTitle` (l.103-107), `ConfirmDialog` for a destructive/irreversible action (l.423-439, ZDR-enable), the seed-from-query-cache re-sync pattern for a mutation's error path (l.244-248). This task's Compliance tab is a NEW sibling tab (own file, own panel), reusing this idiom's shape rather than adding a 4th fieldset to an already-dense 442-line file.
- `apps/dashboard/components/invoices/InvoiceStatusSeal.tsx:InvoiceStatusSeal` — the ONE existing "this document is final" idiom (`Badge variant="success"` + `Lock` icon + sr-only copy, l.18-26) — the direct precedent for this task's evidence seal. Its own vocabulary (`"draft" | "issued"`) does not map onto an Art. 12 bundle (which has no draft state — every returned bundle is already a pinned, generated snapshot), so this task introduces a new, narrowly-scoped sibling component rather than widening `InvoiceStatusSeal`'s prop union (§1 Issue, resolved below).
- `apps/dashboard/components/invoices/InvoiceDetailPage.tsx:InvoiceDetailPage` (l.65-130) — the financial-document idiom this task's preview borrows: `PageHeader` with `actions` carrying the seal (l.79-84), `tabular-nums`+`font-mono` for every numeric value (l.96, l.106), `formatUsd`/`formatTimestamp` from `apps/dashboard/lib/format.ts` (l.17-28 `formatTimestamp`, l.35 `formatNumber`, l.46 `formatUsd`), plain `<a download>` export links (l.114-127) — but INVOICES download via a dedicated server-side export endpoint (`/admin/invoices/{id}/export?format=...`); the Art. 12 bundle has NO such endpoint (by design — it is a paginated JSON read, not a document-export route), so this task's download is a genuinely NEW client pattern (see Issues below).
- `apps/dashboard/components/logs/LogsFilterBar.tsx:LogsFilterBar` (l.55-104) — the reused since/until period-picker idiom: paired `Input type="datetime-local"` fields, `htmlFor`/`label`, `aria-invalid`/`aria-describedby` wired to an inline `role="alert"` field error. This task's period picker reuses this shape verbatim (own local state, not `LogsFilters`).
- `apps/dashboard/components/logs/LogsExplorerPage.tsx` (l.147-266) — the nearest existing cursor-pagination UI (`cursorStack`, `has_more`/`next_cursor` driving a "Load more"/"Previous" affordance) — confirmed this is UI-driven single-page-at-a-time pagination, NOT a full-assembly-then-download loop. No existing component in this codebase walks a `has_more` cursor to completion client-side and assembles one artifact; this task's full-bundle assembly loop is a genuinely NEW pattern (Issue #1 below).
- `apps/dashboard/app/api/gw/[...path]/route.ts` — the BFF catch-all (`bffGet` in `apps/dashboard/lib/bff-client.ts` l.93-101) already forwards `GET /admin/compliance/art12-bundle` with its full query string untouched (no new BFF route file needed) — confirmed against its own doc comment (l.1-23) and `STREAMABLE_CONTENT_TYPES`/`HOP_BY_HOP_HEADERS` (l.31-47): a JSON response (this route's `response_model=Art12BundleResponse`) is buffered/parsed normally, not streamed.
- `apps/dashboard/components/ui/theme-provider.tsx` (l.36-99) — the ONE existing precedent for a non-sensitive, per-browser `localStorage` preference in this codebase (dark/light theme), cited as the precedent for this task's schedule-preference storage. Contrast with `apps/dashboard/lib/auth.ts` (l.1-12: "localStorage helpers REMOVED per v2 BFF contract") — that removal is scoped to SESSION/AUTH tokens (a trust-boundary class of data), not general non-sensitive UI preferences; `theme-provider.tsx` proves the latter class is an accepted, live pattern.
- `apps/dashboard/lib/hooks/use-current-user.ts:useCurrentUser` (l.15, l.45) — exposes `tenant_id`, used to namespace the schedule-preference `localStorage` key per tenant (avoids one superadmin browser session bleeding one tenant's local reminder into another's view while impersonating).
- Backend scheduling infra REUSED (REVISION 2026-07-14 — Tin chose real server-side scheduling over the client-only reminder; re-grounded against current tree): `apps/gateway/src/gateway/usage/application/retention_sweep.py:RetentionSweeper`/`should_start_retention_sweep` and `apps/gateway/src/gateway/billing/application/invoice_generator.py:InvoiceGenerator`/`should_start_invoice_generator` are the ONLY two existing "conditionally-started background loop" precedents in the gateway — both an in-process `asyncio` `run_forever(interval_seconds)` task, gated by a `should_start_X(settings)` predicate, registered via `asyncio.create_task(...)` inside `main.py`'s single `lifespan()` context manager (`apps/gateway/src/gateway/main.py` l.628-645 RetentionSweeper, l.709-721 InvoiceGenerator — read in full) and cancelled on shutdown; NOT a k8s CronJob (`grep` for `kind: CronJob` across `infra/` returns zero hits). `InvoiceGenerator` (`apps/gateway/src/gateway/billing/application/invoice_generator.py`, read l.1-90) is the CLOSER precedent of the two: it computes a per-tenant "most recently completed calendar month" period (`_month_start`/`_next_month`) and writes exactly-once via `INSERT ... ON CONFLICT (tenant_id, period_start) DO NOTHING` — this task's `ReportScheduleGenerator` mirrors BOTH shapes (the conditionally-started loop AND the idempotent per-tenant-period insert). Object persistence REUSED, not invented: `gateway.objectstore.port.ObjectStore` (Protocol: `put(key, data, content_type)` / `get(key)` / `delete(key)` / `health()`, `apps/gateway/src/gateway/objectstore/port.py` read in full) is the SAME seam `ArtifactRepository` (`apps/gateway/src/gateway/artifacts/infrastructure/repository.py`) and `RetentionSweeper._purge_artifacts_batch` already depend on for tenant file bytes (`ArtifactRow.object_key`/`storage_backend`, `apps/gateway/src/gateway/artifacts/infrastructure/orm.py` read in full) — this task's generated bundles reuse this SAME port, no new storage technology.
- ZDR write-gate REUSED (the load-bearing grounding for the ZDR/retention interaction, §1 M17): `gateway.tenants.application.retention_policy.is_zdr(session, tenant_id)` / `raise_if_zdr(session, tenant_id)` (`apps/gateway/src/gateway/tenants/application/retention_policy.py` l.64-88 read in full) is called as the FIRST line of every gated repository write across this codebase — confirmed by grep: `artifacts/infrastructure/repository.py`, `video/infrastructure/repository.py`, `memory/infrastructure/repository.py`, `conversations/infrastructure/repository.py`, `batches/infrastructure/repository.py` ALL call `raise_if_zdr` before persisting a NEW payload for a ZDR-enabled tenant (403 `ERR_ZDR_PAYLOAD_BLOCKED` for an HTTP caller). A persisted Art. 12 bundle (containing audit/log/usage-lineage content) is squarely this class of write — this task's background loop is the FIRST place this same fail-closed rule is applied to a non-HTTP, scheduler-driven write (there is no caller to 403; the loop instead SKIPS the tenant's tick, see M17).
- Delivery channel grounded and found NOT cleanly reusable: `apps/gateway/src/gateway/alerting/application/dispatcher.py:AlertDispatcher` polls `alert_events` and POSTs to a SINGLE webhook URL — confirmed at `apps/gateway/src/gateway/main.py:523`, `webhook_url=_settings.alert_webhook_url` is ONE OPERATOR-WIDE setting, not a per-tenant delivery target; `alert_events` has no tenant-scoped destination column. Reusing it to notify "this tenant your report is ready" would require adding a per-tenant webhook-target column/table FIRST — itself an undesigned, un-grounded addition. `grep`ped again: still zero email/SMTP infrastructure anywhere in `apps/gateway/src/gateway`. Decision (§1 M6/Issue #2 revised): the in-app "Generated reports" list (new `GET /admin/compliance/reports` + `GET /admin/compliance/reports/{id}`) is the v1 delivery mechanism; webhook-notify-on-ready is recorded as an explicit follow-up (§7 Spec delta), not built here.
- RBAC precedent for the NEW write endpoints: `apps/gateway/src/gateway/tenants/api/retention_policy_router.py` (read in full) is the nearest real precedent for a per-tenant POLICY TOGGLE living in the SAME Data & residency/Compliance settings surface — `GET /admin/retention-policy` is open to any authenticated role, `PUT /admin/retention-policy` requires `Permission.SECURITY_CONFIG` (OWNER only, `apps/gateway/src/gateway/tenants/domain/authz.py` l.55-87 `Permission` enum read in full — confirmed `SECURITY_CONFIG` exists, distinct from `AUDIT_READ`), and BOTH operate ONLY on `identity.tenant_id` with no tenant_id path/query param — structurally impossible to cross-tenant target. This task's `PUT`/`DELETE /admin/compliance/report-schedule` mirror this shape exactly (§1 M19).
- Tenant-scoped download precedent: `apps/gateway/src/gateway/artifacts/api/router.py:download_artifact` (l.334-376, read in full) — tenant-scoped row lookup (`repo.get_active(tenant_id=authz.tenant_id, artifact_id=...)`) BEFORE any object-store call, s3-backed content fetched via `ObjectStore.get()`, `Content-Disposition: attachment` ALWAYS (XSS guard), 404 for unknown/cross-tenant/vanished, 503 `OBJECT_STORE_UNAVAILABLE` when the store itself is unreachable — this task's `GET /admin/compliance/reports/{report_id}` mirrors this exactly (§1 M18).
- Audit-write precedent for a background job with no human actor: `RetentionSweeper._emit_zdr_purge_audit` (`apps/gateway/src/gateway/usage/application/retention_sweep.py` l.480-534, read in full, docstring l.487-501) resolves the `audit_missing_actor` invariant (any `AuditEvent` with `tenant_id` set must carry an actor) by setting `tenant_id=None` (a "system-level" event) while naming the real tenant in `metadata` — this task's scheduler-driven audit writes (§1 M20) reuse this EXACT resolution, not a fabricated actor.
- Migration base state (re-grounded, current tree): `apps/gateway/migrations/versions/` currently has FOUR concurrent heads (`e2f4a6b8c0d1`, `1193bc6178f3`, `1d563bf9b143`, `e5a7c9b1d3f6` — computed by revision/down_revision graph walk, not just `alembic heads`, since this worktree cannot run a live DB) — expected, given wave-1 R1 tasks are drafting migrations in parallel on separate worktrees off the same `feat/agent-gateway-r1` base. This task's new migration's `down_revision` CANNOT be pinned correctly at design time; §3 flags it for orchestrator re-parenting at integration.
- `Permission.AUDIT_READ` still the correct gate for every READ surface this task adds (list/download reports, GET schedule) — unchanged reasoning from the original draft's Issue #3.
Context (working folder): `.add/milestones/eu-ai-act-readiness/MILESTONE.md` (this task's owning milestone, HARD DEADLINE 2026-08-02 on the marketing-page sibling task, this task shares the deadline pressure); `/Users/tindang/workspaces/tind-repo/ai-proxy/tmp/r1-design-context.md` (shared R1 wave-1 design rules: draft-only, ground in real code, security/legal copy floor, never touch another task's files).
Honors (patterns / conventions): BFF-only access (`lib/bff-client.ts`'s `bffGet`, no server `sk-` token ever reaches the browser); Aurora token layer + WCAG 2.2 AA + axe-clean (project-wide default, `apps/dashboard/tests/legal-pages.test.tsx` / `status-page.test.tsx` / `slo-page.test.tsx` / `billing-plan.test.tsx` are this codebase's own `jest-axe` precedent); the copy floor from `apps/dashboard/app/(marketing)/ai-act-readiness/page.tsx` (l.31-33: "record-keeping / audit-readiness support", NEVER "makes you compliant" / "GPAI compliance") and its own RED suite `apps/dashboard/tests/ai-act-readiness-page.test.tsx` (l.126-130: an EXISTING test already asserts the marketing page never links to `compliance-report-center` — confirms this surface does not exist yet and that the marketing page intentionally does not reference it pre-ship); design-for-failure (design-for-failure is a standing project rule — every new IO path below states its timeout/retry/fail-open or fail-closed behavior explicitly, mirroring `RetentionSweeper`'s per-pass isolation and `AlertDispatcher`'s bounded exponential retry).
Seams consulted: none new for the FE cursor-assembly loop (unchanged from the original draft — still no `.add/SEAMS.md` entry for "client-side full-bundle cursor-assembly," this task would seed it at Build); the NEW backend seam this revision introduces — "a scheduler-driven write checks `is_zdr` before persisting, mirroring but distinct from `raise_if_zdr`'s HTTP-caller 403 path" — has no prior `.add/SEAMS.md` entry either; this task seeds BOTH at Build.
Anchors the contract cites: `gateway.compliance.api.router.Art12BundleResponse` / `CoverModel` / `SectionsModel` / `AuditSectionModel` / `LogSectionModel` / `UsageSectionModel` (all `apps/gateway/src/gateway/compliance/api/router.py`, FROZEN, read-only consumption, UNCHANGED by this revision — the on-demand path stays a pure consumer); `apps/dashboard/components/platform/PlatformTenantDetail.tsx`'s controlled-tab-via-URL pattern; `apps/dashboard/components/invoices/InvoiceStatusSeal.tsx`'s seal idiom; `apps/dashboard/components/logs/LogsFilterBar.tsx`'s since/until picker idiom; `apps/dashboard/lib/format.ts:formatTimestamp/formatNumber`; NEW this revision — `gateway.usage.application.retention_sweep.RetentionSweeper` (loop shape + additive-pass extension seam + `_purge_artifacts_batch`/ZDR-tenant-iteration idiom); `gateway.billing.application.invoice_generator.InvoiceGenerator` (month-close period computation + idempotent insert idiom); `gateway.objectstore.port.ObjectStore`; `gateway.tenants.application.retention_policy.is_zdr`/`raise_if_zdr`; `gateway.tenants.api.retention_policy_router` (GET/PUT single-row-per-tenant idiom); `gateway.artifacts.api.router.download_artifact` (tenant-scoped download idiom); `gateway.tenants.domain.authz.Permission.SECURITY_CONFIG`.
Issues/Risks (→ feed §1):
- Issue #1 (client-side full-bundle assembly is a NEW pattern, UNCHANGED by this revision): no existing component walks a `has_more`/`bundle_token` cursor to full completion and assembles ONE downloadable artifact — `LogsExplorerPage.tsx` only ever fetches one page at a time for on-screen display. This task must introduce the loop itself; risk: an unbounded tenant with a very large period could force an unbounded number of round-trips in-browser. Feeds §1 M2/M11/R7. The on-demand path this Issue describes is UNCHANGED by this revision — it stays ephemeral and never persisted server-side.
- Issue #2 REVISED (was the ⚠ flag; RESOLVED this revision, Tin's explicit decision 2026-07-14: build real scheduling): the original draft's fork — "no schedule-preference table, no monthly loop, no delivery channel exists" — is now BUILT, not deferred: a NEW `tenant_report_schedules` table + a NEW `ReportScheduleGenerator` background loop (mirrors `RetentionSweeper`/`InvoiceGenerator` verbatim) + a NEW `compliance_report_runs` table persisted via the EXISTING `ObjectStore` port + an in-app "Generated reports" list as the v1 delivery channel (webhook-notify-on-ready remains a follow-up — no per-tenant webhook target exists to reuse `AlertDispatcher` cleanly, see above). This closes the milestone's own exit criterion literally ("monthly-schedulable") rather than the honestly-labeled-but-inert local reminder the original draft shipped. New open threads this creates: the RBAC tier for the write endpoints (§1 new ⚠), and RetentionSweeper needing an additive extension so generated reports do not accumulate forever with no purge path (resolved as a Must, §1 M21, not left open).
- Issue #3 (RBAC precedent for the EXISTING read route, unchanged): `GET /admin/compliance/art12-bundle` is gated on `Permission.AUDIT_READ` (owner/admin/operator/superadmin-own-tenant pass; billing_admin/viewer/member -> 403) — reused unchanged for every new READ endpoint this revision adds (schedule GET, reports list, report download). The NEW WRITE endpoints (schedule PUT/DELETE) use `Permission.SECURITY_CONFIG` instead (Issue "RBAC precedent" above) — see §1 ⚠, the top lowest-confidence flag this revision.
Related intent: MILESTONE.md goal — "An EU tenant can self-serve produce a dated, Art. 12-mapped record-keeping evidence bundle from the console before EU AI Act GPAI enforcement lands on Aug 2, 2026"; GLOSSARY delta "Art. 12 bundle" / "Bundle token" (both owned by `art12-record-keeping-preset`, cited not redefined here); this task's own exit criterion — "The bundle is generatable, downloadable, and monthly-schedulable from the console, axe-clean" — now read LITERALLY (a real server-side monthly schedule), per Tin's 2026-07-14 decision.
Ground SHA: `9abbfe5b` (revision re-ground; original draft's Ground SHA `230921a` covers §0 content carried forward unchanged — the art12-bundle route, FE components, and Settings/RetentionZdrSettings/InvoiceStatusSeal anchors were NOT re-walked line-by-line this revision since they are unchanged; every NEW anchor above was read fresh at `9abbfe5b`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Compliance report center — a new "Compliance" tab in Settings that lets an AUDIT_READ-scoped tenant admin pick a period, generate the full Art. 12 bundle on demand (walking every page to completion), view it rendered in the financial-document/evidence idiom, and download it as one JSON file; a tenant OWNER can additionally enable REAL server-side monthly generation that runs unattended, persists each month's bundle, and lists every past run in an in-app "Generated reports" inbox for download.

Framings weighed:
1. **New sibling "Compliance" tab in `SettingsPage`, reusing the Data & residency panel's fieldset/query/mutation idiom** (CHOSEN) — matches the milestone's own phrasing ("extends the Settings → Data & residency panel idiom") at the STYLE level without bloating an already-442-line file with a 4th unrelated concern; keeps each tab a single bounded concept, consistent with every other existing tab (Cache, Guardrails, SSO, Provider Keys, SCIM, SAML, Data & residency).
2. Add a 4th fieldset directly inside `RetentionZdrSettings.tsx` — rejected: the bundle preview (cover + 3 sections + evidence seal + download) is a materially larger, differently-shaped surface than a settings fieldset (it's a generated DOCUMENT, not a policy toggle); folding it in would mix two very different visual/interaction idioms (form-fieldset vs. document-preview) in one file and one query-cache key.
3. A standalone top-level nav page (`/compliance`) outside Settings entirely — rejected: contradicts the milestone's explicit instruction to extend Settings → Data & residency, and would need its own nav-link addition + RBAC-gated nav visibility work not otherwise justified by anything else in this task.
4. REVISION (scheduling delivery mechanism) — reuse the EXISTING `alerting` webhook dispatcher (`AlertDispatcher`/`alert_events`) as the delivery channel for "your report is ready" — rejected (Ground): `alert_webhook_url` is confirmed a SINGLE operator-wide setting (`main.py:523`), not a per-tenant destination; there is no tenant-scoped webhook-target column anywhere. Making it work would require designing and building that column/table FIRST — an undesigned dependency this task does not introduce speculatively. CHOSEN instead: an in-app "Generated reports" list is the v1 delivery mechanism (the tenant/teammate checks the Compliance tab; no push notification in v1); webhook-notify-on-ready is recorded as a follow-up (§7 Spec delta).
5. REVISION (persistence technology) — invent a NEW storage path for generated bundles (e.g. a dedicated bucket/service) — rejected: the EXISTING `gateway.objectstore.port.ObjectStore` port already backs `artifacts` (tenant file bytes) and is already consumed by `RetentionSweeper`'s own purge pass; reusing it needs zero new infrastructure, zero new dependency, and inherits its existing design-for-failure behavior (`ObjectStoreUnavailableError` — deferred, not lost).

Must:
<must>
  - M1: The Compliance tab (new file `ComplianceReportCenter.tsx`, mounted as a new sibling `TabsTrigger`/`TabsContent` in `SettingsPage.tsx`) renders a period picker — two `Input type="datetime-local"` fields (From/Since, To/Until), both required, reusing `LogsFilterBar.tsx`'s labeled-pair idiom — and a primary "Generate bundle" action, disabled until both fields are filled.
  - M2: "Generate bundle" walks the full `bundle_token` continuation loop against `GET /admin/compliance/art12-bundle` (via `bffGet`) — calling again with the SAME since/until (M14-safe) and the previous response's `bundle_token` — until every section's `has_more` is `false`, accumulating each section's `items` client-side, BEFORE rendering any preview. A partially-assembled bundle is NEVER rendered or offered for download as if it were complete.
  - M3: On successful full assembly, render a financial-document-idiom preview: a dated document header showing the PINNED cover fields (`tenant_name`, `period.since`/`period.until`, `generated_at`, `residency_pin`, `zdr_state.enabled`/`enabled_at`, `retention_window_days`, `default_tier`) via `formatTimestamp`, a NEW `BundleEvidenceSeal` ("Generated & pinned" — modeled on `InvoiceStatusSeal`'s `Badge variant="success"` + `Lock` pattern, new component because the vocabulary differs: every returned bundle is already final, there is no draft state), and per-section row counts (`formatNumber`, `tabular-nums`) with any non-null `note` (the backend's own ZDR/plan-feature honest-degrade text, M8/M9 of the backend contract) rendered VERBATIM and visibly — never suppressed.
  - M4: A "Download bundle (JSON)" button downloads the fully-assembled bundle (cover + all 3 sections' accumulated items, in the shape of one `Art12BundleResponse`-like object with `bundle_token` omitted) as one `.json` file via client-side `Blob` + `URL.createObjectURL` + a synthetic `<a download>` click (no server export endpoint exists for this shape — confirmed at Ground). Filename: `art12-bundle-{tenant_id}-{since}-{until}.json` (ISO dates, colon-stripped) for evidence traceability.
  - M5: Every field/value rendered in the preview traces to a real `CoverModel`/`SectionsModel`/section-item field named in `apps/gateway/src/gateway/compliance/api/router.py` — no invented field, no silently-dropped field.
  - M6 (REVISED 2026-07-14 — Tin's decision: real scheduling, not a local reminder): A "Scheduled generation" fieldset lets a tenant OWNER (gated `Permission.SECURITY_CONFIG`, §1 M19) enable REAL server-side monthly generation via `PUT /admin/compliance/report-schedule` — writes a per-tenant `tenant_report_schedules` row (`enabled`, `day_of_month` 1-28, `window_policy` fixed to "previous calendar month" in v1). A non-owner (any other AUDIT_READ-capable role) sees the CURRENT schedule state read-only via `GET /admin/compliance/report-schedule` — the toggle itself is disabled with an inline explanation ("Only the tenant owner can change this"), never silently hidden. This REPLACES the client-only `localStorage` reminder from the original draft outright — no `localStorage` schedule state, no per-browser reminder, ships nowhere in the final build. See M15-M23 for the generation loop, persistence, ZDR interaction, delivery, RBAC, and audit detail this control drives.
  - M7: Copy floor — no instance of "compliant" / "makes you compliant" / "GPAI compliance" anywhere on this surface; uses "record-keeping" / "audit-readiness" language, mirroring the ALREADY-shipped `ai-act-readiness` marketing page's own M3/R2 floor and its RED suite (`tests/ai-act-readiness-page.test.tsx` l.85-93).
  - M8: This surface never independently quotes an Art. 101/99 penalty figure (it is a functional console surface, not marketing copy) — if any legal-figure reference is ever added here, it MUST cite Art. 101 (3% global turnover / €15M), never Art. 99 (€35M/7%). Recorded as a standing constraint even though v1's copy carries no figure at all.
  - M9: RBAC — a role lacking `AUDIT_READ` (billing_admin/viewer/member) sees an `ErrorState` (not a form) on this tab, mirroring `RetentionZdrSettings.tsx`'s own `isError -> ErrorState` pattern and `InvoiceDetailPage.tsx`'s 403-copy pattern ("You don't have access to ...").
  - M10: `since > until` in the picker is caught CLIENT-SIDE (mirrors the backend's own `since>until -> PAYLOAD_INVALID`) and blocks "Generate bundle" with an inline `role="alert"` field error — never fires a doomed request.
  - M11: The client-side cursor-assembly loop (M2) is bounded — a maximum page-count ceiling (e.g. 500 pages, i.e. up to 500×5000=2.5M rows per section at the backend's own max `limit`) aborts the walk with a visible, distinct error rather than hanging the tab indefinitely; no partial download is ever offered on this path.
  - M12: `SettingsPage.tsx`'s `Tabs` is lifted to CONTROLLED mode reading/writing a `?tab=` query param — reusing `PlatformTenantDetail.tsx`'s exact pattern (`useSearchParams`-seeded lazy `useState`, the "adjust state during render" re-sync block for browser back/forward, `handleTabChange` calling both `setActiveTab` and `router.replace`) — so `/settings?tab=compliance` opens directly on the Compliance tab. Additive only: any existing bare `/settings` link (no `?tab=` param) still resolves to the SAME default tab as today ("cache").
  - M13: WCAG 2.2 AA — every interactive element on this tab (period inputs, Generate button, Download button, schedule Switch + day-of-month select) has a visible `focus-visible` state, ≥44px hit target, and the panel's heading/landmark order is correct; axe-clean (project's own `jest-axe` convention, cited above).
  - M14: The continuation loop (M2) always resends the EXACT since/until captured at the moment "Generate bundle" was clicked — never re-reads the (possibly since-edited) picker inputs mid-walk — matching the backend's own bundle_token-pins-to-its-minting-period rule; a client bug here would otherwise self-trigger `ERR_CURSOR_INVALID`.
  - M15 (NEW — real scheduling backend): A new server-side background loop, `ReportScheduleGenerator` (`apps/gateway/src/gateway/compliance/application/report_schedule_generator.py`), mirrors `RetentionSweeper`/`InvoiceGenerator`'s conditionally-started `run_forever(interval_seconds)` + `should_start_report_schedule_generator(settings)` shape verbatim, registered in `main.py`'s lifespan exactly like those two (a new `compliance_report_schedule_interval_seconds` setting, default disabled-safe like the others — interval<=0 means "don't start"). Each tick selects every ENABLED `tenant_report_schedules` row whose `next_run_at <= now()` and, per tenant, computes the previous COMPLETED calendar month (mirrors `InvoiceGenerator._month_start`/`_next_month` exactly) and generates that tenant's bundle using the SAME 3 repositories the frozen `GET /admin/compliance/art12-bundle` route reads — in-process, NEVER an HTTP self-call to its own API.
  - M16: Each generated run is recorded EXACTLY ONCE via a NEW `compliance_report_runs` row under a `UNIQUE (tenant_id, period_start)` constraint, inserted with `ON CONFLICT (tenant_id, period_start) DO NOTHING` — mirrors `InvoiceGenerator`'s own idempotent-insert idiom verbatim. A restart-during-tick, an overlapping tick, or a re-run never double-generates, double-charges object-store space, or double-lists the same tenant+period (R14).
  - M17 (the ZDR/persistence interaction — the security_note's own required threat-model item): before generating OR persisting anything for a due schedule, the loop reads that tenant's CURRENT `zdr_enabled` via the EXISTING `gateway.tenants.application.retention_policy.is_zdr` read (the same read `raise_if_zdr` wraps — already the fail-closed gate every other content-persisting repository in this codebase calls before a new payload write). If `zdr_enabled=true`, the tick is SKIPPED for that tenant: NO bundle is assembled, NO object is written, NO `compliance_report_runs` row is inserted; `tenant_report_schedules.last_run_status` is set to `'skipped_zdr'`, `last_run_at`/`next_run_at` still advance (self-healing, mirrors `RetentionSweeper`'s "re-runs every tick" idiom — if ZDR is later disabled, the VERY NEXT due tick generates normally, no manual re-trigger). This is DELIBERATELY stricter than the UNCHANGED on-demand path (M3's honest-degrade note, still available to a ZDR tenant exactly as before) — persisting a bundle server-side is a NEW STORED PAYLOAD, the exact class of write `raise_if_zdr` exists to block; skip, never silently downgrade-and-persist.
    - M17 **v2 — POST-FREEZE CR (Tin-approved 2026-07-14, AskUserQuestion "Close now via CR")**: the single up-front `is_zdr` check leaves a TOCTOU window — bundle assembly (two DB round-trips) then a network object PUT separate the check from the first persistence, so a tenant flipping `zdr_enabled=true` mid-tick could still get a bundle persisted (adversarial verify a854c67d, rated 🟡, self-healing via the RetentionSweeper ZDR pass but a real ZDR-promise window on the EU-compliance surface). CR: re-read `is_zdr` on a FRESH session IMMEDIATELY before the first persistence (before the object PUT); if it flipped, take the SAME fail-closed skip path (`_record_zdr_skip` → `skipped_zdr`, nothing written). Closes the window entirely rather than relying on next-tick self-heal. Red/green: `test_zdr_flip_mid_tick_persists_nothing_toctou` (flips the flag inside `_assemble_bundle`, the exact window) proves leak→close. Does NOT change any endpoint/table/wire shape — a strictly-stricter internal gate.
  - M18 (in-app delivery — the v1 delivery mechanism, Framing #4): `GET /admin/compliance/reports` lists a tenant's own generated runs (keyset-paginated, `generated_at DESC`), gated `Permission.AUDIT_READ` (same role set as the frozen art12-bundle route). `GET /admin/compliance/reports/{report_id}` downloads one run's bytes, gated the same way, with a tenant-scoped row lookup BEFORE any object-store call — mirrors `download_artifact`'s exact discipline (tenant-scoped lookup first, `Content-Disposition: attachment` always, 503 `ERR_OBJECT_STORE_UNAVAILABLE` if the store itself is unreachable). A cross-tenant `report_id` guess is a 404, NEVER a 403 (a 403 would itself leak existence) and never a cross-tenant byte (R11).
  - M19 (RBAC split — read vs. write, the ⚠-flagged part): `GET /admin/compliance/report-schedule` is gated `Permission.AUDIT_READ` (any read-capable role can SEE the schedule state). `PUT`/`DELETE /admin/compliance/report-schedule` are gated `Permission.SECURITY_CONFIG` (OWNER only) — mirrors the EXISTING `PUT /admin/retention-policy` precedent verbatim, the nearest real precedent for a per-tenant policy toggle inside the SAME Data & residency/Compliance settings surface. All three operate ONLY on `identity.tenant_id` — no tenant_id path/query param exists, structurally impossible to target another tenant (mirrors `retention_policy_router`'s own documented invariant).
  - M20 (audit — every write, human or system): Every schedule mutation (`PUT`/`DELETE /admin/compliance/report-schedule`) fires an actor-attributed audit event (`record_audit`, action `compliance.schedule_updated`/`compliance.schedule_deleted`, `tenant_id=identity.tenant_id`, the real caller as actor) via the EXISTING, unchanged audit writer. Every scheduler-driven generation (success OR ZDR-skip) fires a SYSTEM-scoped audit event (action `compliance.report_generated`/`compliance.report_generation_skipped`, `tenant_id=None`, actor=None, the real tenant named in `metadata`) — mirrors `RetentionSweeper._emit_zdr_purge_audit`'s EXACT resolution of the `audit_missing_actor` invariant (a background job has no human actor; the codebase's own settled convention is `tenant_id=None` + tenant named in metadata, never a fabricated actor).
  - M21 (retention completeness — closes a gap this expansion would otherwise leave open): `RetentionSweeper` (`usage/application/retention_sweep.py`) is EXTENDED, additively (mirrors the file's own documented extension seam for `tenant-retention-zdr`/`payload-capture-store`), to sweep `compliance_report_runs` exactly like it already sweeps `artifacts` — a per-tenant window-cutoff pass (only tenants with `retention_window_days IS NOT NULL`) and a per-tenant ZDR purge pass (every row, every tick, for `zdr_enabled=true` tenants), each calling `ObjectStore.delete()` on the row's `object_key` before dropping the DB row (mirrors `_purge_artifacts_batch` verbatim — an object-delete failure DEFERS the row to the next tick, never orphans bytes). WITHOUT this Must, a generated report would have NO deletion path at all once written — this task closes that gap rather than shipping it open.
  - M22: WCAG 2.2 AA / axe-clean extends to the new schedule control (Switch + day-of-month select, now wired to a REAL `PUT` mutation with its own loading/error states) and the new "Generated reports" list (each row's download link, empty state, loading state) — same bar as M13.
  - M23: The schedule's `day_of_month` (1-28) fires relative to UTC midnight on that day of the SERVER'S clock — there is no per-tenant timezone stored anywhere in this codebase (`TenantRow` carries none). Disclosure copy states this plainly ("Generates on day N of each month, UTC") rather than implying a tenant-local time.
</must>
Reject:
<reject>
  - R1: since/until missing, or since > until, at "Generate bundle" click -> client blocks submission, inline error "Both dates are required" / "From must be before To" — request is NEVER sent (mirrors `ERR_PAYLOAD_INVALID`, caught pre-flight client-side, no code round-trips to the network for this case).
  - R2: Backend 401 `ERR_AUTH_INVALID_TOKEN` on any page of the walk -> abort immediately, `ErrorState` (mirrors the existing BFF-401 session-expiry handling in `bff-client.ts`), discard any partially-assembled bundle in memory — never render or offer a stale/partial preview.
  - R3: Backend 403 `ERR_AUTH_FORBIDDEN` (role lacks AUDIT_READ; can arrive even after the tab itself already rendered, e.g. a role change mid-session) -> `ErrorState` "You don't have access to the Compliance report center" — no form, no partial preview.
  - R4: Backend 422 `ERR_PAYLOAD_INVALID` mid-walk (defensive — R1 should make this unreachable from THIS client, but a future caller/replay could still hit it) -> abort, `ErrorState`, discard partial state, offer "Try again".
  - R5: Backend 422 `ERR_CURSOR_INVALID` (token/period mismatch — e.g. a long-idle tab replaying a stale token, or a client bug) -> abort the walk, `ErrorState` "The bundle could not be completed — please generate it again", discard ALL partial state, offer "Generate again" which starts a completely FRESH (un-tokened) call rather than retrying the stale continuation.
  - R6: Backend 504 `ERR_EXPORT_TIMEOUT` on any page -> abort, `ErrorState` "Generation timed out", offer "Try again" — never silently download a truncated bundle as if it were complete.
  - R7: The client-side page-count ceiling (M11) is exceeded -> abort locally with a DISTINCT message ("This period is too large to assemble in the browser — narrow the date range and try again") rather than a generic network-error message; no partial download offered; the underlying picker/period state is unchanged.
  - R8: A second "Generate bundle" click while a walk is already in flight -> ignored (button is `disabled` + shows "Generating…"); the in-flight walk is unaffected, no duplicate concurrent walk is started, and no state outside the in-flight walk's own local variables is mutated.
  - R9 (NEW): `PUT /admin/compliance/report-schedule` with `day_of_month` outside 1-28 -> 422 `ERR_PAYLOAD_INVALID`, inline field error client-side, the stored row unchanged (mirrors R1's pre-flight-block discipline applied to the new write).
  - R10 (NEW): A non-owner reaches the schedule write path -> the toggle/day-of-month control is `disabled` client-side (never fires the network call) AND, defensively, the backend 403s `ERR_AUTH_FORBIDDEN` if reached directly (e.g. a stale tab, a role downgrade mid-session) — mirrors R3's shape, applied to the new write surface.
  - R11 (NEW): `GET /admin/compliance/reports/{report_id}` for a `report_id` belonging to another tenant -> 404, NEVER 403 (a 403 would itself leak existence), never a cross-tenant byte — mirrors `download_artifact`'s exact behavior (M18).
  - R12 (NEW — restates M17 as an explicit non-persistence guarantee): a scheduled tick fires for a tenant with `zdr_enabled=true` -> no object is written, no `compliance_report_runs` row is inserted, `last_run_status='skipped_zdr'`, `next_run_at` still advances to the following month.
  - R13 (NEW — design-for-failure on the object store): the object store is unreachable/unconfigured at generate time -> the scheduled loop logs+swallows and retries next tick (fail-open per-tenant, mirrors `RetentionSweeper`'s own per-pass isolation; `last_run_status` is NOT set to `'success'`, the row stays un-generated, self-healing on the next tick); a download call in this state -> 503 `ERR_OBJECT_STORE_UNAVAILABLE` (mirrors `download_artifact` verbatim), NEVER a silent empty/truncated download.
  - R14 (NEW — restates M16 as an explicit Reject): two ticks race or overlap for the same tenant+period (a slow tick + interval overlap, or a restart mid-tick) -> the `UNIQUE (tenant_id, period_start)` constraint + `ON CONFLICT DO NOTHING` insert ensures exactly ONE `compliance_report_runs` row survives — no duplicate object-store bytes, no double "report ready" state in the list.
</reject>
After:
<after>
  - A tenant admin with `AUDIT_READ` can, from `/settings?tab=compliance`, pick a period, click "Generate bundle", see the FULL bundle (every page walked, never a partial view) rendered in the financial-document idiom with a visible "Generated & pinned" evidence seal and every section's honest-degrade `note` shown verbatim, and download it as one JSON file — this on-demand path is UNCHANGED and still never persisted server-side. A tenant OWNER can additionally enable REAL server-side monthly generation (M6/M15-M23): an unattended background loop generates and persists each due tenant's bundle to the SAME object-store seam `artifacts` already uses, skips honestly (never degrades-and-persists) for a ZDR tenant, lists every past run in a "Generated reports" inbox any AUDIT_READ role can see and download, is swept by the SAME retention system as every other persisted payload, and is gated + tenant-scoped + audited on both the read and write surfaces — all without inventing an email channel, a k8s CronJob, or a new storage technology, and without any interactive element failing an axe/WCAG 2.2 AA check.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ RBAC tier for `PUT`/`DELETE /admin/compliance/report-schedule` — this design chose `Permission.SECURITY_CONFIG` (OWNER only), mirroring `PUT /admin/retention-policy`'s exact precedent (the nearest real sibling: a policy toggle in the SAME Data & residency/Compliance settings surface). LOWEST CONFIDENCE because the security_note's own phrasing ("schedule writes... are admin-gated") could also read as "the SAME role set as `AUDIT_READ`" (owner/admin/operator/superadmin), not OWNER-only. Cost if wrong: too tight — an admin who should reasonably be able to turn on monthly evidence generation can't, and has to escalate to the owner; too loose (if AUDIT_READ were used instead) — an operator role, which today cannot touch retention/ZDR policy at all, would gain the ability to stand up an unattended job that persists tenant compliance data indefinitely, a materially bigger capability than any operator-held permission today. Recommendation: keep `SECURITY_CONFIG`/OWNER-only at freeze — it errs toward the stricter, already-precedented gate for a genuinely NEW automated persistent-write capability — unless Tin corrects it.
  - [ ] `RetentionSweeper`'s extension to sweep `compliance_report_runs` (M21) reuses the artifacts precedent's semantics verbatim — a generated report is deleted on the SAME `retention_window_days`/ZDR schedule as any other tenant payload. Not yet confirmed against whether Tin wants generated COMPLIANCE EVIDENCE specifically to be retained LONGER than a tenant's (possibly short) operational log-retention window — an Art. 12 bundle is itself the evidence a tenant may want to keep past its own log retention. Low blast radius (a tenant with a short window simply accumulates fewer historical reports — no surprise beyond what they already opted into for every other swept table); easy to carve out a separate `retention_report_runs_days` knob later if this proves wrong.
  - [ ] The monthly tick's poll interval (`compliance_report_schedule_interval_seconds`, mirrors `invoice_generation_interval_seconds`) is assumed to run hourly-or-tighter (ticks often, a given schedule only ever GENERATES when its own `next_run_at` is actually due) — confirmed reasoning (matches `InvoiceGenerator`'s own "tick often, no-op until due" idiom exactly), not yet tuned to a specific default value; low blast radius, a config-only follow-up, does not change any Must's behavior.
  - [ ] The 500-page ceiling (M11) is a reasonable default for "large but real-world" EU tenants — confirm or tune against an actual EU tenant's expected audit/log/usage row volume once one exists; a wrong ceiling either aborts a legitimate large tenant too early or lets the browser tab hang too long before erroring. Low blast-radius (a client-only constant), easy to revise post-ship. (Unchanged from the original draft — applies only to the on-demand path, M2/M11.)
  - [ ] The new `BundleEvidenceSeal` component (rather than widening `InvoiceStatusSeal`'s `status` union) is the right call — confirmed reasoning: an Art. 12 bundle has no "draft" state (every response IS a generated, pinned snapshot), so a shared component would need a permanently-unused branch; kept as two small, single-purpose components instead of one component with a dead code path. (Unchanged from the original draft.)
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Period picker renders and Generate is disabled until both dates are set   # M1
  Given the Compliance tab is open and the tenant has AUDIT_READ
  When the tab first renders
  Then two labeled datetime-local inputs (From, To) are visible
  And the "Generate bundle" button is disabled

Scenario: Generate walks every page to completion before rendering a preview   # M2
  Given a valid since/until period is entered
  And the backend returns 3 pages for the walk (has_more true, true, then false with a bundle_token on the first two)
  When "Generate bundle" is clicked
  Then the client calls GET /admin/compliance/art12-bundle 3 times, each continuation call echoing the SAME since/until plus the prior bundle_token
  And no preview is rendered after page 1 or page 2
  And the preview renders only after the 3rd (has_more=false) response

Scenario: Successful assembly renders the financial-document preview with the evidence seal   # M3, M5
  Given a full bundle has been assembled (2 audit_events rows, 1 request_log_metadata row, 0 usage_lineage rows)
  When the preview renders
  Then the document header shows tenant_name, the period, generated_at, residency_pin, zdr_state, retention_window_days, and default_tier — each traced to a real CoverModel field
  And a "Generated & pinned" BundleEvidenceSeal (Badge variant="success" + Lock icon) is visible
  And each section shows its row count in tabular-nums via formatNumber
  And no field is rendered that is not a named CoverModel/SectionsModel field

Scenario: A section's honest-degrade note is shown verbatim, never suppressed   # M3
  Given the backend returns request_log_metadata with items=[], has_more=false, note="Zero-Data-Retention has been enabled since 2026-06-01T00:00:00; no request-log rows exist while ZDR is on."
  When the preview renders
  Then the request_log_metadata section shows the note text verbatim
  And the section is NOT rendered as if it silently had zero real rows for an unstated reason

Scenario: Download produces one JSON file assembled from every page   # M4
  Given a full bundle has been assembled across 2 pages
  When "Download bundle (JSON)" is clicked
  Then a single .json file is created via Blob + createObjectURL, named art12-bundle-{tenant_id}-{since}-{until}.json
  And its contents include the pinned cover plus ALL accumulated items from every section across both pages, not just the last page's items

Scenario: Scheduled generation is REAL, owner-writable, and honestly discloses UTC timing   # M6, M23
  Given the "Scheduled generation" fieldset is visible and the caller is the tenant OWNER
  When the owner enables monthly generation and picks day 1
  Then PUT /admin/compliance/report-schedule is called with { enabled: true, day_of_month: 1 }
  And the response's enabled/day_of_month reflect the new server-side state (never localStorage)
  And disclosure copy states plainly that generation is real, runs unattended, is stored server-side, and fires at UTC midnight on day 1
  Given a non-owner (e.g. "admin") views the SAME fieldset
  When the tab renders
  Then the toggle/day-of-month control are disabled with an inline "Only the tenant owner can change this" explanation
  And the CURRENT schedule state (from GET) is still shown, not hidden

Scenario: Copy floor holds — no compliance-claim overreach   # M7
  Given the Compliance tab's full rendered text (loading, empty, error, and populated states)
  When the text is scanned
  Then it contains no case-insensitive match for "compliant" used as a claim, "makes you compliant", or "GPAI compliance"
  And any mention of Art. 12 uses "record-keeping" / "audit-readiness" language

Scenario: A role lacking AUDIT_READ sees ErrorState, not a form   # M9, R3
  Given the tenant's role is "viewer"
  When the Compliance tab is opened
  And the backend returns 403 ERR_AUTH_FORBIDDEN
  Then an ErrorState "You don't have access to the Compliance report center" is rendered
  And no period picker or Generate button is rendered

Scenario: since > until is blocked client-side, never sent   # M10, R1
  Given From is set to a later date than To
  When "Generate bundle" is clicked
  Then an inline field error "From must be before To" is shown
  And no network request is made
  And the picker's own values remain unchanged

Scenario: Backend 401 mid-walk aborts and discards partial state   # R2
  Given page 1 of the walk succeeded (has_more=true) and page 2 returns 401 ERR_AUTH_INVALID_TOKEN
  When the 401 is received
  Then the walk aborts immediately
  And an ErrorState is shown
  And no partial bundle (page 1's already-accumulated items) is rendered or offered for download
  And the period picker's own values remain unchanged

Scenario: Backend 422 ERR_CURSOR_INVALID mid-walk offers a clean restart, not a stale retry   # R5
  Given a continuation call returns 422 ERR_CURSOR_INVALID
  When the error is received
  Then the walk aborts and all partial state is discarded
  And an ErrorState "The bundle could not be completed — please generate it again" is shown
  And clicking "Generate again" starts a completely FRESH call with no bundle_token attached
  And the period picker's own values remain unchanged

Scenario: Backend 504 timeout mid-walk never yields a silent truncated download   # R6
  Given a continuation call returns 504 ERR_EXPORT_TIMEOUT
  When the error is received
  Then the walk aborts, an ErrorState "Generation timed out" is shown
  And no "Download bundle" affordance is offered for the incomplete data
  And the period picker's own values remain unchanged

Scenario: Client-side page-count ceiling protects the browser tab   # M11, R7
  Given the backend keeps returning has_more=true past the 500th page
  When the 501st page would be requested
  Then the walk aborts locally without making that request
  And a distinct message "This period is too large to assemble in the browser — narrow the date range and try again" is shown
  And no partial download is offered
  And the period picker's own values remain unchanged

Scenario: A second Generate click during an in-flight walk is a no-op   # R8
  Given "Generate bundle" was clicked and the walk is still in flight (page 2 of 3)
  When "Generate bundle" is clicked again
  Then the button is disabled and shows "Generating…"
  And no second concurrent walk is started
  And the in-flight walk completes normally, unaffected

Scenario: Deep link opens the Compliance tab directly, existing links unaffected   # M12
  Given a user navigates to /settings?tab=compliance
  When the page renders
  Then the Compliance tab is active without any click
  Given a user navigates to /settings with no query param
  When the page renders
  Then the SAME default tab as today ("cache") is active

Scenario: Compliance tab is axe-clean in every state   # M13
  Given the Compliance tab's loading, error, empty-picker, and populated-preview states
  When each is checked with jest-axe
  Then zero violations are reported in every state

Scenario: An edited period mid-walk does not leak into the continuation call   # M14
  Given "Generate bundle" was clicked with since=2026-01-01/until=2026-01-31, and the walk is on page 2
  When the walk's next continuation call is made
  Then it sends the EXACT since/until captured at click time, never a value re-derived from the (disabled, but theoretically stale) picker inputs

Scenario: A due schedule generates and persists exactly once   # M15, M16
  Given a tenant has an enabled tenant_report_schedules row with next_run_at <= now(), zdr_enabled=false
  When ReportScheduleGenerator's tick runs
  Then it computes the previous completed calendar month as the period
  And it assembles the bundle in-process from the same 3 repositories the frozen art12-bundle route reads
  And it writes exactly one object to the ObjectStore and exactly one compliance_report_runs row (tenant_id, period_start unique)
  And tenant_report_schedules.last_run_at/last_run_status='success'/next_run_at all advance

Scenario: A due schedule for a ZDR tenant is skipped, never degraded-and-persisted   # M17, R12
  Given a tenant has an enabled schedule with next_run_at <= now() and zdr_enabled=true
  When the tick runs
  Then is_zdr(tenant_id) is checked BEFORE any assembly or object-store write
  And no bundle is assembled, no object is written, no compliance_report_runs row is inserted
  And last_run_status is set to 'skipped_zdr', last_run_at/next_run_at still advance
  And the tenant's on-demand generate/download path (M2-M5) is completely unaffected — still available with its existing honest-degrade note

Scenario: Generated reports list and download are tenant-scoped   # M18, R11
  Given tenant A has 2 compliance_report_runs rows and tenant B has 1
  When tenant A's admin calls GET /admin/compliance/reports
  Then only tenant A's 2 rows are returned, keyset-paginated by generated_at DESC
  Given tenant A's admin then calls GET /admin/compliance/reports/{tenant_B's report_id}
  Then the response is 404, never 403, never tenant B's bytes

Scenario: Schedule GET is open to any AUDIT_READ role; PUT/DELETE require OWNER   # M19, R9, R10
  Given the caller's role is "admin" (AUDIT_READ-capable, not OWNER)
  When they call GET /admin/compliance/report-schedule
  Then the current schedule state is returned 200
  When they call PUT /admin/compliance/report-schedule
  Then the response is 403 ERR_AUTH_FORBIDDEN
  Given the caller's role is "owner" and submits day_of_month=32
  When they call PUT /admin/compliance/report-schedule
  Then the response is 422 ERR_PAYLOAD_INVALID and the stored row is unchanged

Scenario: Schedule writes are actor-audited; scheduler-driven generation is system-audited   # M20
  Given an owner calls PUT /admin/compliance/report-schedule with enabled=true
  When the mutation succeeds
  Then an audit event action="compliance.schedule_updated" is recorded with tenant_id=identity.tenant_id and the real actor
  Given the background loop generates a report for that tenant on the next due tick
  When generation succeeds
  Then an audit event action="compliance.report_generated" is recorded with tenant_id=None, actor=None, and the real tenant_id named in metadata

Scenario: RetentionSweeper purges aged generated reports and their object-store bytes   # M21
  Given a tenant has retention_window_days set and a compliance_report_runs row older than that window
  When RetentionSweeper's window-cutoff pass runs
  Then ObjectStore.delete() is called on that row's object_key BEFORE the DB row is dropped
  And an object-delete failure defers the row to the next tick rather than dropping it

Scenario: A schedule race/restart never double-generates the same tenant+period   # M16, R14
  Given two overlapping ticks both attempt to generate the same tenant's same period_start
  When both INSERT attempts run against compliance_report_runs
  Then the UNIQUE (tenant_id, period_start) constraint + ON CONFLICT DO NOTHING leaves exactly one row
  And no duplicate object-store bytes are orphaned

Scenario: Object store unavailability fails open on generate, fails closed (503) on download   # R13
  Given the object store is unreachable
  When a scheduled tick attempts to generate
  Then the tick logs and swallows the error, last_run_status is NOT set to 'success', the next tick retries
  Given a caller then requests GET /admin/compliance/reports/{id} for an existing row whose bytes are now unreachable
  Then the response is 503 ERR_OBJECT_STORE_UNAVAILABLE, never a silent empty or truncated download

Scenario: Schedule control and Generated reports list are axe-clean and disclose UTC timing   # M22, M23
  Given the schedule control's enabled/disabled/loading/error states and the Generated reports list's empty/loading/populated states
  When each is checked with jest-axe
  Then zero violations are reported in every state
  And the schedule's disclosure copy states plainly that generation fires on day N of each month, UTC
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
BFF pass-through route REUSED verbatim (zero new BFF route file — the existing
apps/dashboard/app/api/gw/[...path]/route.ts catch-all already forwards this path with its
full query string, confirmed at Ground):

GET /admin/compliance/art12-bundle   -- art12-record-keeping-preset §3, FROZEN @ v1, cited verbatim
  query: since=<ISO-8601, REQUIRED> · until=<ISO-8601, REQUIRED> · bundle_token?=<opaque>
  200 -> Art12BundleResponse { cover: CoverModel, sections: SectionsModel, bundle_token: str|null }
  401 -> { code: "ERR_AUTH_INVALID_TOKEN" }
  403 -> { code: "ERR_AUTH_FORBIDDEN" }
  422 -> { code: "ERR_PAYLOAD_INVALID" }        -- missing/malformed since|until, inverted range
  422 -> { code: "ERR_CURSOR_INVALID" }         -- malformed/mismatched bundle_token vs since/until
  504 -> { code: "ERR_EXPORT_TIMEOUT" }
  (this task's on-demand FE path NEVER introduces a new query param, a new response field, or a new
   error code on THIS route — pure consumption of the frozen shape above, unchanged by this revision)

NEW backend endpoints (apps/gateway/src/gateway/compliance/api/report_schedule_router.py — a NEW
sibling file, mounted alongside the existing FROZEN compliance router in main.py; the frozen
art12-bundle route/file is NEVER edited by this task):

GET /admin/compliance/report-schedule
  auth: Permission.AUDIT_READ · scope: identity.tenant_id only, no path/query tenant param
  200 -> ReportScheduleResponse { enabled: bool, cadence: "monthly", day_of_month: int(1-28),
         window_policy: "previous_calendar_month", created_by: str|null, created_at: str|null,
         updated_at: str|null, last_run_at: str|null, last_run_status: "success"|"skipped_zdr"|
         "failed"|null, next_run_at: str|null }
  (no row yet for this tenant -> 200 with enabled=false and every other field null/default —
   absence of a schedule is a normal state, never a 404)

PUT /admin/compliance/report-schedule
  auth: Permission.SECURITY_CONFIG (OWNER only, mirrors PUT /admin/retention-policy verbatim)
  body: { enabled: bool, day_of_month?: int(1-28, default 1) }   -- partial-merge, mirrors
        RetentionPolicyBody's fields_set convention
  -> upserts tenant_report_schedules; false->true transition computes next_run_at fresh;
     true->false clears next_run_at (no further ticks); fires audit compliance.schedule_updated
  200 -> ReportScheduleResponse (same shape as GET)
  401 -> ERR_AUTH_INVALID_TOKEN · 403 -> ERR_AUTH_FORBIDDEN (non-owner)
  422 -> ERR_PAYLOAD_INVALID (day_of_month outside 1-28)

DELETE /admin/compliance/report-schedule
  auth: Permission.SECURITY_CONFIG (OWNER only)
  -> hard-deletes the tenant_report_schedules row (re-enable is a fresh PUT); fires audit
     compliance.schedule_deleted
  204 · 401/403 as above

GET /admin/compliance/reports
  auth: Permission.AUDIT_READ · scope: identity.tenant_id only
  query: limit?(default 20, max 100) · cursor?(opaque, keyset on generated_at DESC, id)
  200 -> { items: [{ id, period_start, period_end, generated_at, size_bytes, format_version }],
           next_cursor: str|null, has_more: bool }

GET /admin/compliance/reports/{report_id}
  auth: Permission.AUDIT_READ · scope: tenant-scoped row lookup BEFORE any object-store call
        (mirrors download_artifact verbatim)
  200 -> raw bytes, Content-Type: application/json,
         Content-Disposition: attachment; filename="art12-bundle-{tenant_id}-{period_start}-{period_end}.json"
  404 -> unknown / cross-tenant / vanished-object report_id (NEVER 403 — a 403 would leak existence)
  503 -> ERR_OBJECT_STORE_UNAVAILABLE (store unreachable/unconfigured — mirrors download_artifact)

Schema (2 NEW tables, additive-only migration — see "Migration" note below):

  tenant_report_schedules
    tenant_id UUID PRIMARY KEY REFERENCES tenants(id)   -- one row per tenant, mirrors the
                                                          -- retention_policy single-row-per-tenant idiom
    enabled BOOLEAN NOT NULL DEFAULT false
    cadence TEXT NOT NULL DEFAULT 'monthly' CHECK (cadence = 'monthly')          -- v1 fixed value
    day_of_month INTEGER NOT NULL DEFAULT 1 CHECK (day_of_month BETWEEN 1 AND 28)
    window_policy TEXT NOT NULL DEFAULT 'previous_calendar_month'
      CHECK (window_policy = 'previous_calendar_month')                          -- v1 fixed value
    delivery_target TEXT NOT NULL DEFAULT 'in_app' CHECK (delivery_target = 'in_app') -- v1 fixed;
      column present for a later webhook/email addition (§7 Spec delta), not read by v1 code
    created_by UUID NULL                                 -- the user who last enabled it
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    last_run_at TIMESTAMPTZ NULL
    last_run_status TEXT NULL CHECK (last_run_status IN ('success','skipped_zdr','failed'))
    next_run_at TIMESTAMPTZ NULL

  compliance_report_runs
    id UUID PRIMARY KEY DEFAULT gen_random_uuid()        -- uuid7 at the Python layer, mirrors
                                                          -- InvoiceRow/ArtifactRow's own id idiom
    tenant_id UUID NOT NULL REFERENCES tenants(id)
    period_start TIMESTAMPTZ NOT NULL
    period_end TIMESTAMPTZ NOT NULL
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    object_key TEXT NOT NULL                             -- gateway.objectstore.port.ObjectStore key,
                                                          -- s3-backed only (no inline BYTEA path —
                                                          -- bundles can be large; mirrors artifacts'
                                                          -- storage_backend='s3' branch, without the
                                                          -- 'inline' branch this task never needs)
    size_bytes INTEGER NOT NULL
    format_version TEXT NOT NULL                          -- mirrors CoverModel.format_version
    source TEXT NOT NULL DEFAULT 'scheduled' CHECK (source = 'scheduled')  -- v1: ONLY the scheduler
      -- writes this table; the on-demand path (M2-M5) stays ephemeral/unpersisted, UNCHANGED —
      -- this is a deliberate boundary, not an oversight (keeps the frozen on-demand contract
      -- untouched and avoids retrofitting persistence onto a path that never needed it)
    UNIQUE (tenant_id, period_start)                       -- idempotency, mirrors InvoiceRow's
                                                          -- ON CONFLICT (tenant_id, period_start)
    INDEX (tenant_id, generated_at DESC)                   -- mirrors ix_artifacts_tenant_created

  RetentionSweeper extension (M21, additive to usage/application/retention_sweep.py, the file's
  own documented extension seam — the 3 original unconditional passes stay byte-identical):
    - new SQL templates _SELECT_COMPLIANCE_REPORTS_WINDOW / _SELECT_COMPLIANCE_REPORTS_ZDR_TENANT,
      mirroring _SELECT_ARTIFACTS_WINDOW / _SELECT_ARTIFACTS_ZDR_TENANT exactly (object_key-aware:
      ObjectStore.delete() before the DB row drops, deferred on ObjectStoreUnavailableError)
    - wired into _sweep_new_payload_window_pass and _sweep_zdr_purge_pass alongside artifacts

Migration: NEW alembic revision under apps/gateway/migrations/versions/ creating both tables above.
  down_revision NEEDS ORCHESTRATOR RE-PARENTING AT INTEGRATION — four concurrent heads exist as of
  Ground SHA 9abbfe5b (e2f4a6b8c0d1 / 1193bc6178f3 / 1d563bf9b143 / e5a7c9b1d3f6, other wave-1 R1
  tasks drafting migrations in parallel off the same feat/agent-gateway-r1 base); this migration
  must be rebased onto whichever becomes canonical before `alembic upgrade head` runs at Build.

New backend module (apps/gateway/src/gateway/compliance/application/report_schedule_generator.py):

  class ReportScheduleGenerator:
    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession], object_store: ObjectStore,
                 settings: _Settings) -> None: ...
    async def generate_due_schedules(self) -> dict[str, int]:
      -- SELECT tenant_report_schedules WHERE enabled=true AND next_run_at <= now()
      -- per tenant, in isolation (mirrors RetentionSweeper's per-tenant ZDR-pass iteration,
         never a batched cross-tenant query, never a shared buffer — the cross-tenant-generation
         guard the security_note requires): is_zdr(tenant_id) check FIRST (M17) -> skip-and-advance
         if true; else compute previous-month period (mirrors InvoiceGenerator._month_start/
         _next_month) -> assemble via the 3 existing repositories (AuditRepository,
         LogSectionModel's source, UsageRepository) in-process -> ObjectStore.put() at key
         `compliance-reports/{tenant_id}/{report_id}.json` (tenant_id embedded in the key itself —
         this task's OWN new requirement, mirrors how artifacts' S3ObjectStore already scopes byte
         paths per caller) -> INSERT compliance_report_runs ON CONFLICT (tenant_id, period_start)
         DO NOTHING (M16) -> UPDATE tenant_report_schedules SET last_run_at/last_run_status/
         next_run_at -> fire-and-forget record_audit (M20)
      -- bounded per-tenant asyncio.timeout; each tenant's failure is isolated (try/except,
         mirrors RetentionSweeper's per-pass isolation) — one tenant's failure never blocks
         another tenant's tick or crashes the loop
    async def run_forever(self, *, interval_seconds: float,
                           _sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None: ...
      -- mirrors RetentionSweeper.run_forever verbatim: swallow all non-CancelledError exceptions,
         propagate CancelledError for clean shutdown

  def should_start_report_schedule_generator(settings: _Settings) -> bool:
    return settings.compliance_report_schedule_interval_seconds > 0   -- new settings field,
      mirrors invoice_generation_interval_seconds's default-disabled-safe convention

  Wired in main.py's lifespan exactly like InvoiceGenerator/RetentionSweeper (a new
  app.state.report_schedule_generator_task = asyncio.create_task(...) block, cancelled on
  shutdown identically).

New FE modules (all under apps/dashboard/, TypeScript):

  lib/art12-bundle.ts (NEW — pure, no React/DOM dependency, unit-testable in isolation)
    export interface Art12AssembledBundle {
      cover: CoverModel;   // re-exported/mirrored shape, NOT re-derived — same field names as
                            // gateway.compliance.api.router.CoverModel, since/until as ISO strings
      sections: {
        audit_events: { items: AuditEventItem[]; note: string | null };
        request_log_metadata: { items: LogListItem[]; note: string | null };
        usage_lineage: { items: UsageLineageItem[]; note: string | null };
      };
    }
    export class BundleTooLargeError extends Error {}   -- thrown at the M11 page-count ceiling
    export async function assembleArt12Bundle(
      fetchPage: (since: string, until: string, bundleToken?: string) => Promise<Art12BundleResponse>,
      since: string,
      until: string,
      opts?: { maxPages?: number },   // default 500 (M11)
    ): Promise<Art12AssembledBundle>
      -- walks bundle_token until every section's has_more=false (M2/M14); the SAME since/until
         string captured at call time is echoed on every continuation call, never re-read from a
         live source; throws BundleTooLargeError at the ceiling (R7); any non-2xx from fetchPage
         propagates unchanged for the caller to map to an ErrorState (R2/R4/R5/R6) — this module
         does its OWN retry-free single pass, no internal retry logic.

  components/compliance/BundleEvidenceSeal.tsx (NEW)
    export function BundleEvidenceSeal(): JSX.Element
      -- Badge variant="success" + <Lock className="size-3" aria-hidden /> + "Generated & pinned"
         + sr-only " — this bundle snapshot is fixed and will not change" (modeled on
         InvoiceStatusSeal.tsx's issued-state branch; no props — every rendered bundle is by
         definition already generated+pinned, there is no draft variant to branch on)

  components/compliance/ComplianceReportCenter.tsx (NEW — the Compliance tab's content)
    export function ComplianceReportCenter(): JSX.Element
      -- state: since/until (local, controlled inputs), fieldErrors, walkState
         ("idle" | "generating" | "done" | "error"), assembledBundle (Art12AssembledBundle | null),
         errorKind ("auth" | "forbidden" | "payload" | "cursor" | "timeout" | "too-large" | "unknown")
      -- on Generate: client-validates (R1) -> calls assembleArt12Bundle(fetchPage, since, until)
         where fetchPage wraps bffGet<Art12BundleResponse>(`/admin/compliance/art12-bundle?...`)
      -- renders: LogsFilterBar-idiom period picker -> Generate button (disabled while
         walkState==="generating", label "Generating…") -> on done: PageHeader-style document
         header (tenant_name/period/generated_at/residency_pin/zdr_state/retention_window_days/
         default_tier via formatTimestamp) + BundleEvidenceSeal + 3 section-count rows
         (formatNumber, tabular-nums) each showing its note when non-null + "Download bundle
         (JSON)" button (Blob+createObjectURL, filename
         art12-bundle-{tenant_id}-{since}-{until}.json) -> mounts <ScheduleControl /> (below,
         REPLACES the old localStorage fieldset outright) -> mounts <GeneratedReportsList />
         (below) -> on error: ErrorState mapped per errorKind (R2/R3/R4/R5/R6/R7); on "cursor" a
         "Generate again" button clears ALL walk state and starts a fresh un-tokened call (R5)
      -- uses useCurrentUser() for tenant_id/role (download filename + ScheduleControl's
         owner-only gate)

  components/compliance/ScheduleControl.tsx (NEW — REVISION, replaces the old localStorage
    fieldset described in the original draft; that fieldset and its localStorage key ship
    NOWHERE in the final build)
    export function ScheduleControl(): JSX.Element
      -- useQuery(getReportSchedule) via bffGet(`/admin/compliance/report-schedule`) (M19 GET,
         any AUDIT_READ role) + useMutation(putReportSchedule) via bffPut (M19 PUT, OWNER only)
      -- role-gates the Switch/day-of-month <select> to `disabled` for a non-owner (read from
         useCurrentUser()), with inline copy "Only the tenant owner can change this" — the GET
         still succeeds and renders the CURRENT state for a non-owner (M6)
      -- disclosure copy states plainly: generation is REAL, runs unattended monthly, is stored
         server-side, appears in "Generated reports" below, fires at UTC midnight on day N (M23)
         — the opposite disclosure of the old draft's "this does nothing automatically" copy
      -- on a 403 from a defensive backend reject (R10) -> inline error, control stays disabled
      -- NOT ConfirmDialog-gated (unlike ZDR-enable) — toggling starts/stops FUTURE ticks only,
         never deletes existing generated reports (a materially reversible action)

  components/compliance/GeneratedReportsList.tsx (NEW)
    export function GeneratedReportsList(): JSX.Element
      -- useInfiniteQuery(listGeneratedReports) via bffGet(`/admin/compliance/reports?...`)
         (M18, keyset cursor idiom mirrors LogsExplorerPage's cursorStack/has_more shape)
      -- each row: period_start-period_end (formatTimestamp), generated_at (formatTimestamp),
         size_bytes (formatNumber) + a download <a> pointing at
         `/api/gw/admin/compliance/reports/{id}` (BFF pass-through, browser-native download —
         the BFF forwards the Content-Disposition/Content-Type unchanged, same buffering path
         as every other non-streamable BFF response)
      -- empty state: "No reports generated yet — enable Scheduled generation above, or check
         back after the next monthly run."
      -- a 503 on a specific row's download (R13) shows an inline row-level error, does not
         blank the whole list

  lib/compliance-reports.ts (NEW — pure bffGet/bffPut wrappers, no React/DOM dependency)
    export interface ReportSchedule { enabled: boolean; cadence: "monthly"; dayOfMonth: number;
      windowPolicy: "previous_calendar_month"; createdBy: string | null; createdAt: string | null;
      updatedAt: string | null; lastRunAt: string | null;
      lastRunStatus: "success" | "skipped_zdr" | "failed" | null; nextRunAt: string | null; }
    export interface GeneratedReportSummary { id: string; periodStart: string; periodEnd: string;
      generatedAt: string; sizeBytes: number; formatVersion: string; }
    export async function getReportSchedule(): Promise<ReportSchedule>
    export async function putReportSchedule(body: { enabled: boolean; dayOfMonth?: number }):
      Promise<ReportSchedule>
    export async function listGeneratedReports(cursor?: string):
      Promise<{ items: GeneratedReportSummary[]; nextCursor: string | null; hasMore: boolean }>
      -- all three are thin bffGet/bffPut wrappers over the M18/M19 endpoints; field names
         mapped snake_case (wire) <-> camelCase (TS), mirrors every other lib/*.ts wrapper's
         own convention in this codebase (no new pattern introduced)

Modified existing components (no new exported prop surface beyond what's listed):
  components/settings/SettingsPage.tsx   -- Tabs uplifted from uncontrolled (defaultValue="cache")
    to CONTROLLED (value={activeTab} onValueChange={handleTabChange}), reusing
    PlatformTenantDetail.tsx's exact useSearchParams/useRouter + lazy-useState-seed +
    adjust-during-render re-sync + handleTabChange(router.replace(?tab=...)) pattern verbatim
    (M12); + one new TabsTrigger value="compliance" label "Compliance" + one new TabsContent
    mounting <ComplianceReportCenter />; default tab unchanged ("cache") when no ?tab= param.

Schema (client-only storage): NONE — REVISION removes the old localStorage schedule-preference
  key entirely (M6 is now a real server-side row, not a client preference); no client-only
  storage of any kind remains in this task's scope.
```

Glossary deltas:
- **Bundle evidence seal**: the `BundleEvidenceSeal` component's visual assertion that a rendered Art. 12 bundle is a fixed, generated snapshot (`Badge variant="success"` + lock icon + sr-only copy) — the Art. 12-bundle-specific counterpart to `InvoiceStatusSeal`'s "issued" state, introduced because a bundle has no draft/issued distinction (every response is already final).
- **Scheduled generation** (REVISED — was "Scheduled generation (preview)"): a REAL, per-tenant, server-side monthly background job (`ReportScheduleGenerator`) that generates and persists the Art. 12 bundle unattended, gated OWNER-only to enable/disable, self-skipping (never degrading) for a ZDR tenant. NOT a client-side reminder of any kind — the old `localStorage`-only meaning is retired, ships nowhere in the final build.
- **Generated reports (inbox)**: the in-app list (`GET /admin/compliance/reports`) of every `compliance_report_runs` row a tenant's scheduler has produced, each individually downloadable (`GET /admin/compliance/reports/{id}`) — the v1 delivery mechanism for scheduled generation (Framing #4); no push/email/webhook notification exists yet (§7 Spec delta).
- **`tenant_report_schedules`** / **`compliance_report_runs`**: the two new tables this task owns (§3 Schema) — the former is the per-tenant schedule POLICY (one row, OWNER-writable), the latter is the per-run EVIDENCE RECORD (many rows, system-inserted only, `source='scheduled'`). [folded foundation-version 53]

Status: FROZEN @ v1 — approved by Tin Dang

Least-sure flag surfaced at freeze: [contract] RBAC tier for `PUT`/`DELETE /admin/compliance/report-schedule` — this design chose `Permission.SECURITY_CONFIG` (OWNER only), mirroring `PUT /admin/retention-policy`'s exact precedent, the nearest real sibling policy-toggle in the SAME Data & residency/Compliance settings surface (§1 ⚠, full reasoning there). Recommendation: freeze OWNER-only as drafted — it is the stricter, already-precedented gate for a genuinely NEW automated persistent-write capability, and is easy to WIDEN later (a role-set relaxation) but hard to safely NARROW after tenants have already delegated it to non-owner admins. Tin should confirm or correct this tier at freeze; every other design point in this revision (the ZDR-skip-not-degrade resolution, M17; the RetentionSweeper extension closing the no-purge-path gap, M21; the in-app-inbox-over-webhook delivery decision, Framing #4) is HIGH confidence and resolved, not flagged.

Reported: no — awaiting human freeze (this draft, plus the flag above, is the freeze report input)

### Scope (for whoever builds it — non-binding preferred plan, human freezes the shape above, not this list)
May touch:
- `apps/dashboard/lib/art12-bundle.ts` — NEW (pure assembly/cursor-loop function, on-demand path, UNCHANGED by this revision)
- `apps/dashboard/lib/compliance-reports.ts` — NEW (schedule + generated-reports-list bffGet/bffPut wrappers)
- `apps/dashboard/components/compliance/ComplianceReportCenter.tsx` — NEW
- `apps/dashboard/components/compliance/BundleEvidenceSeal.tsx` — NEW
- `apps/dashboard/components/compliance/ScheduleControl.tsx` — NEW
- `apps/dashboard/components/compliance/GeneratedReportsList.tsx` — NEW
- `apps/dashboard/components/settings/SettingsPage.tsx` — additive (controlled-tab uplift + new tab)
- `apps/dashboard/tests/art12-bundle-assembly.test.ts` · `compliance-report-center.test.tsx` · `settings-page.test.tsx` · `schedule-control.test.tsx` · `generated-reports-list.test.tsx` — NEW
- `apps/gateway/src/gateway/compliance/api/report_schedule_router.py` — NEW (schedule CRUD + reports list/download endpoints; the FROZEN `router.py` art12-bundle file is untouched)
- `apps/gateway/src/gateway/compliance/application/report_schedule_generator.py` — NEW (`ReportScheduleGenerator`)
- `apps/gateway/src/gateway/compliance/infrastructure/` — NEW (ORM rows + repository for the 2 new tables)
- `apps/gateway/src/gateway/usage/application/retention_sweep.py` — additive extension ONLY (M21: new SQL templates + 2 new calls inside `_sweep_new_payload_window_pass`/`_sweep_zdr_purge_pass`; every existing line stays byte-identical)
- `apps/gateway/src/gateway/main.py` — additive lifespan wiring for `ReportScheduleGenerator`, mirrors the `InvoiceGenerator` block exactly; additive router mount for `report_schedule_router`
- `apps/gateway/src/gateway/config.py` (or wherever `_Settings` lives) — additive: `compliance_report_schedule_interval_seconds`
- `apps/gateway/migrations/versions/` — ONE new migration (2 tables; down_revision needs re-parenting, see §3 Migration note)
- `apps/gateway/tests/` — NEW backend test files for the generator, the 2 new endpoints' RBAC/tenant-scoping/idempotency/ZDR-skip, and the RetentionSweeper extension
Must NOT touch: `apps/gateway/src/gateway/compliance/api/router.py` (frozen v1, backend-owned by `art12-record-keeping-preset` — this revision adds a SIBLING file, never edits this one); `RetentionSweeper`'s 3 ORIGINAL unconditional passes (usage_records/alert_events/audit_events) or its existing artifacts/conversations/memories/batch_job_items/video_generation_jobs passes (M21 is additive-only); `apps/dashboard/components/settings/RetentionZdrSettings.tsx` (its own 3 fieldsets stay untouched — this task adds a sibling tab, not a 4th fieldset); `apps/dashboard/components/invoices/InvoiceStatusSeal.tsx` (reused as a REFERENCE pattern, not modified — its `status` union is NOT widened); `apps/dashboard/app/(marketing)/ai-act-readiness/page.tsx` (its own RED suite already asserts it never links here pre-ship); any OTHER wave-1 R1 task's migration file (coordinate rebasing via the orchestrator, never hand-edit another task's revision).

Strategy (ordered batches, non-binding preferred plan):
1. Backend first (the riskier, higher-judgment half): migration (2 tables, rebase down_revision at integration) -> `report_schedule_generator.py` (`ReportScheduleGenerator`, `should_start_report_schedule_generator`, unit-tested against a hand-built mock repository/session-factory sequence covering due/not-due/ZDR-skip/idempotent-conflict/object-store-unavailable cases, ZERO real DB) -> `report_schedule_router.py` (5 endpoints, RBAC/tenant-scoping tests per endpoint) -> `retention_sweep.py` additive extension (M21, mirrors the artifacts pass exactly) -> `main.py` wiring (lifespan task + router mount) -> `ruff`/`pyright` clean.
2. `lib/art12-bundle.ts` (on-demand path, UNCHANGED) — if not already built from the prior draft, same as originally planned: pure `assembleArt12Bundle` + `BundleTooLargeError`, unit-tested, ZERO React/DOM.
3. `lib/compliance-reports.ts` — pure bffGet/bffPut wrappers, unit-tested against a mocked `bffGet`/`bffPut`.
4. `BundleEvidenceSeal.tsx` — trivial, no state, straight port of `InvoiceStatusSeal.tsx`'s issued-branch shape.
5. `ComplianceReportCenter.tsx` — period picker -> wire `assembleArt12Bundle` -> preview rendering -> download, in that order (each sub-slice has its own scenario(s) above).
6. `ScheduleControl.tsx` — GET-then-render -> owner-gated PUT mutation -> disclosure copy -> error/403 handling.
7. `GeneratedReportsList.tsx` — keyset list -> per-row download link -> empty/loading/row-level-503 states.
8. `SettingsPage.tsx` uplift — copy `PlatformTenantDetail.tsx`'s controlled-tab-via-URL block verbatim; confirm EVERY existing tab still defaults correctly with no `?tab=` param (regression scenario).
9. Full `jest-axe` pass across every state before calling this done — mirrors the project's own `legal-pages.test.tsx`/`status-page.test.tsx` convention.
10. `pnpm lint`/`tsc`/vitest suite clean AND `ruff`/`pyright`/pytest suite clean (this revision is no longer FE-only); re-run any existing `SettingsPage`-adjacent test and any existing `retention_sweep` test to confirm zero regression from the additive extension.

Persona (required): TWO personas this revision, sequenced — `backend-engineer` (or this repo's equivalent, e.g. `python-backend-expert`) for the batch-1 backend work (background-loop lifecycle, idempotent insert, ZDR-gate placement, RetentionSweeper extension, migration) — this is now the highest-judgment, highest-risk half of the task, NOT an advisory-only lens; then `ui-designer` — Aurora-consistency + WCAG-AA lens — for batches 2-9 (financial-document idiom translation, the seal-vs-widen-InvoiceStatusSeal decision, the controlled-tab-uplift's default-behavior-preservation, ScheduleControl/GeneratedReportsList's own new states). `frontend-engineer`'s BFF-trust-boundary and SSR-safety rules still apply verbatim to every FE batch (only ever reads/writes via `bffGet`/`bffPut`, no new BFF route file needed — the existing catch-all forwards the new paths too).
Spawn isolation (default): worktree — prefer an isolated worktree for the build/verify spawn per the project's own default, not only for explicit parallel mode. Given the risk:high declaration, this task's VERIFY should get the milestone's own standing bar for security-sensitive work — TWO independent adversarial passes (mirrors `residency-service-tiers`'s own lesson: a recorded PASS is reversible, a 2nd verify caught what a 1st rated CLEAR).
Known-problem fixes: the OLD localStorage/lazy-`useState`-initializer known-problem-fix from the original draft is MOOT (no `localStorage` remains in this task's scope — retired with M6). NEW known-problem to watch at Build: a background-loop write path (`ReportScheduleGenerator`) has NO HTTP caller to receive a 403 — every fail-closed decision it makes (ZDR-skip, object-store-unavailable) must be expressed as a SKIP-and-record, never a raised exception that would crash `run_forever`'s loop (mirrors `RetentionSweeper`'s own "NEVER raises, only logs+swallows" discipline, cited and read in full at Ground).

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: >=80% lines on every new/touched module (project-wide vitest.config.ts threshold, cited from `residency-tiers-ui` TASK.md §4's own precedent), no regression on any pre-existing file's coverage.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_period_picker_renders_and_generate_disabled: arrange render ComplianceReportCenter with AUDIT_READ / act nothing / assert both inputs present + Generate disabled · covers: M1
  - test_generate_walks_every_page_before_preview: arrange 3-page mock fetchPage sequence / act click Generate / assert 3 calls made, same since/until each call, no preview after call 1/2, preview after call 3 · covers: M2, M14
  - test_preview_renders_cover_and_seal: arrange assembled bundle fixture / act render / assert every CoverModel field shown + BundleEvidenceSeal present + no invented field · covers: M3, M5
  - test_section_note_shown_verbatim: arrange section with note set / act render / assert note text present verbatim · covers: M3
  - test_download_assembles_all_pages: arrange 2-page assembled bundle / act click Download / assert Blob content includes items from BOTH pages, correct filename · covers: M4
  - test_schedule_preference_local_only: arrange fieldset / act toggle + pick day / assert localStorage write, tenant-namespaced key, disclosure copy present, zero network calls · covers: M6
  - test_copy_floor_no_overreach: arrange full rendered text across all states / act scan / assert no "compliant"/"makes you compliant"/"GPAI compliance" match · covers: M7
  - test_forbidden_role_shows_error_state: arrange mock 403 ERR_AUTH_FORBIDDEN / act render / assert ErrorState shown, no form · covers: M9, R3
  - test_since_after_until_blocked_client_side: arrange From > To / act click Generate / assert inline error, zero fetch calls, picker unchanged · covers: M10, R1
  - test_401_mid_walk_aborts_discards_partial: arrange page1 ok page2 401 / act run walk / assert abort, ErrorState, no partial preview, picker unchanged · covers: R2
  - test_cursor_invalid_offers_clean_restart: arrange page2 422 ERR_CURSOR_INVALID / act run walk then click "Generate again" / assert fresh un-tokened call, all prior state discarded · covers: R5
  - test_timeout_mid_walk_no_silent_download: arrange page2 504 / act run walk / assert ErrorState, no Download affordance rendered · covers: R6
  - test_page_ceiling_aborts_locally: arrange fetchPage always has_more=true / act run assembleArt12Bundle with maxPages=500 / assert BundleTooLargeError thrown at page 501, no 501st call made · covers: M11, R7
  - test_double_generate_click_is_noop: arrange walk in flight / act click Generate twice / assert exactly one walk's worth of calls, button shows "Generating…" · covers: R8
  - test_settings_deep_link_opens_compliance_tab: arrange navigate to /settings?tab=compliance / act render SettingsPage / assert Compliance tab active · covers: M12
  - test_settings_default_tab_unchanged_without_param: arrange navigate to /settings (no query) / act render SettingsPage / assert same default tab as before this task ("cache") · covers: M12 (regression)
  - test_compliance_tab_axe_clean_all_states: arrange loading/error/empty-picker/populated-preview / act run jest-axe on each / assert zero violations each · covers: M13
  - test_assemble_bundle_pure_function_multipage: arrange hand-built mock fetchPage / act call assembleArt12Bundle directly (no React) / assert correct accumulation, same since/until propagated · covers: M2, M14 (unit-level, lib/art12-bundle.ts)
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.

`apps/dashboard/tests/art12-bundle-assembly.test.ts` † · `apps/dashboard/tests/compliance-report-center.test.tsx` † · `apps/dashboard/tests/settings-page.test.tsx` †

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/lib/art12-bundle.ts` · `apps/dashboard/components/compliance/` · `apps/dashboard/components/settings/SettingsPage.tsx` · `apps/dashboard/tests/art12-bundle-assembly.test.ts` · `apps/dashboard/tests/compliance-report-center.test.tsx` · `apps/dashboard/tests/settings-page.test.tsx`
Strategy (ordered batches): see §3 Strategy above (1. pure assembly fn -> 2. seal -> 3. tab component -> 4. SettingsPage uplift -> 5. axe pass -> 6. lint/typecheck/full suite).

Persona (required): ui-designer — see §3.
Spawn isolation (default): worktree.
Known-problem fixes: see §3 Known-problem fixes (localStorage read must live in a useEffect, not a lazy useState initializer).
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): the client-side cursor-assembly loop (`assembleArt12Bundle`) NEVER retries a failed page and NEVER re-derives since/until from a live source mid-walk — one captured period, one pass, fail closed (abort + discard partial state) on ANY non-2xx, matching M2/M14/R2/R4/R5/R6 exactly; no "best-effort partial download" fallback is ever offered.
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the contract; allow-list packages only (no new npm dependency — `Blob`/`URL.createObjectURL` are browser built-ins, `localStorage` is a browser built-in, no new package needed for either); ask if unclear.

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
- [ ] Opening `/settings?tab=compliance` as an AUDIT_READ tenant admin lands directly on the Compliance tab — confirmed by manual/e2e navigation, no click needed.
- [ ] A real multi-page bundle (mocked or live against a seeded tenant with >1 page of audit/log/usage rows) renders a preview only after EVERY page is walked, never a partial one — confirmed by network-call count + preview-timing assertion.
- [ ] The downloaded JSON file's item counts equal the SUM across all walked pages, not just the last page — confirmed by inspecting the downloaded Blob content in a test.
- [ ] Every error path (401/403/422 payload/422 cursor/504/local ceiling) renders a DISTINCT, correctly-worded ErrorState and leaves no partial-download affordance — confirmed by one test per code.
- [ ] The schedule-preference toggle never fires a network request and never blocks Generate — confirmed by asserting zero `bffGet`/`bffPost` calls on toggle.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — `ComplianceReportCenter` is actually mounted from `SettingsPage.tsx`'s new `TabsContent`; `assembleArt12Bundle` is actually called (not a dead export); record where confirmed.
- [ ] DEAD-CODE (code) — no unused export left in `lib/art12-bundle.ts` or `BundleEvidenceSeal.tsx`.
- [ ] SEMANTIC (prose / non-code) — the schedule-preference disclosure copy is read in full and confirmed to say, plainly, that nothing is auto-generated or auto-delivered.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [ ] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by <how / where>
- [ ] any anchor that moved/renamed since Ground SHA `230921a` is named here, not left silent

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

Watch (reuse scenarios as monitors): client-side walk abort rate by errorKind (401/403/422-payload/422-cursor/504/too-large) — a spike in "too-large" signals the 500-page ceiling (§1 assumption) needs tuning against real EU-tenant volume; schedule enable-rate (`tenant_report_schedules.enabled=true` count) and `last_run_status` distribution (a rising `skipped_zdr` share signals ZDR-tenant demand for a distinct evidence-retention story; a rising `failed`/never-`success` share signals the object store or generator loop itself needs attention) — both feed whether the webhook-notify-on-ready follow-up (§7 Spec delta) should be prioritized sooner.

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned
- [human] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).
- [SPEC · dropped] (2026-07-14 revision) Real server-side scheduled generation+delivery is NO LONGER a follow-up — Tin chose to build it in THIS task (M15-M23). The prior line recorded here (`art12-scheduled-generation-delivery` as a separate future task) is superseded; struck, not carried forward.
- [SPEC · open] (2026-07-14 revision) Webhook-notify-on-report-ready — reusing/extending the `alerting` dispatcher to push a notification when a scheduled report finishes, instead of the tenant having to check the "Generated reports" inbox — remains a genuine follow-up, NOT built here (evidence: Framing #4, §0 — `alert_webhook_url` is a single operator-wide setting, not a per-tenant target; adding that shape is undesigned work out of this task's grounded scope).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

- [UDD · folded] A financial-document idiom (InvoiceStatusSeal/InvoiceDetailPage) does not always transplant its exact vocabulary onto a structurally-similar-but-semantically-different document (an Art. 12 bundle has no draft state) — the lesson is to translate the IDIOM (dated header, tabular-nums, visible immutability marker) rather than force-reuse the exact component/prop union (evidence: BundleEvidenceSeal introduced as a sibling, not an InvoiceStatusSeal prop-union widening). [folded foundation-version 53]
