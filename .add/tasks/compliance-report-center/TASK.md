# TASK: Compliance report center console surface (generate/download/schedule the Art. 12 bundle)

slug: compliance-report-center · created: 2026-07-14 · stage: production
milestone: eu-ai-act-readiness
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: ground   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- content below is drafted through §3 CONTRACT (DRAFT, unfrozen) by the design agent; phase
     marker left at "ground" to match state.json (engine-tracked) — the orchestrator advances
     phase via add.py, this draft does not self-advance it. -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

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
- Backend scheduling/delivery infra actually grounded (Issue #2, the ⚠ flag — see §1): `apps/gateway/src/gateway/usage/application/retention_sweep.py:RetentionSweeper`/`should_start_retention_sweep` and `apps/gateway/src/gateway/billing/application/invoice_generator.py:InvoiceGenerator`/`should_start_invoice_generator` are the ONLY two existing "conditionally-started background loop" precedents in the gateway (both wired via `main.py`'s lifespan, both an in-process `asyncio` `run_forever()` — NOT a k8s CronJob; `grep` for `kind: CronJob` across `infra/` returned zero hits). Both are OPERATOR-scoped periodic sweeps across ALL tenants, not a per-tenant "generate my bundle monthly and deliver it to me" surface. Outbound delivery: `apps/gateway/src/gateway/alerting/application/dispatcher.py` + `apps/gateway/src/gateway/alerting/infrastructure/httpx_webhook_sink.py` is the ONLY existing outbound-delivery mechanism (webhook, built for alert events) — there is NO email/SMTP sending infrastructure anywhere in `apps/gateway/src/gateway` (`grep`ped, zero hits). No per-tenant schedule-preference table exists on `TenantRow` (`apps/gateway/src/gateway/tenants/infrastructure/orm.py:TenantRow`, l.91-131 read in full).
Context (working folder): `.add/milestones/eu-ai-act-readiness/MILESTONE.md` (this task's owning milestone, HARD DEADLINE 2026-08-02 on the marketing-page sibling task, this task shares the deadline pressure); `/Users/tindang/workspaces/tind-repo/ai-proxy/tmp/r1-design-context.md` (shared R1 wave-1 design rules: draft-only, ground in real code, security/legal copy floor, never touch another task's files).
Honors (patterns / conventions): BFF-only access (`lib/bff-client.ts`'s `bffGet`, no server `sk-` token ever reaches the browser); Aurora token layer + WCAG 2.2 AA + axe-clean (project-wide default, `apps/dashboard/tests/legal-pages.test.tsx` / `status-page.test.tsx` / `slo-page.test.tsx` / `billing-plan.test.tsx` are this codebase's own `jest-axe` precedent); the copy floor from `apps/dashboard/app/(marketing)/ai-act-readiness/page.tsx` (l.31-33: "record-keeping / audit-readiness support", NEVER "makes you compliant" / "GPAI compliance") and its own RED suite `apps/dashboard/tests/ai-act-readiness-page.test.tsx` (l.126-130: an EXISTING test already asserts the marketing page never links to `compliance-report-center` — confirms this surface does not exist yet and that the marketing page intentionally does not reference it pre-ship).
Seams consulted: none new — this task is the first design pass at `.add/tasks/compliance-report-center/`; no `.add/SEAMS.md` entry yet exists for a "client-side full-bundle cursor-assembly" seam (this task would be the one to seed it, at Build).
Anchors the contract cites: `gateway.compliance.api.router.Art12BundleResponse` / `CoverModel` / `SectionsModel` / `AuditSectionModel` / `LogSectionModel` / `UsageSectionModel` (all `apps/gateway/src/gateway/compliance/api/router.py`, FROZEN, read-only consumption); `apps/dashboard/components/platform/PlatformTenantDetail.tsx`'s controlled-tab-via-URL pattern; `apps/dashboard/components/invoices/InvoiceStatusSeal.tsx`'s seal idiom; `apps/dashboard/components/logs/LogsFilterBar.tsx`'s since/until picker idiom; `apps/dashboard/lib/format.ts:formatTimestamp/formatNumber`.
Issues/Risks (→ feed §1):
- Issue #1 (client-side full-bundle assembly is a NEW pattern): no existing component walks a `has_more`/`bundle_token` cursor to full completion and assembles ONE downloadable artifact — `LogsExplorerPage.tsx` only ever fetches one page at a time for on-screen display. This task must introduce the loop itself; risk: an unbounded tenant with a very large period could force an unbounded number of round-trips in-browser. Feeds §1 M2/M11/R7.
- Issue #2 (the ⚠ flag — "schedule monthly" has NO backend scheduling/delivery surface to build on): the milestone's own Scope explicitly says "Out: ... new export/audit engine capability (assembly only — a gap found in the export API becomes a change-request ..., not silent scope growth here)". A real server-side "generate monthly + deliver" feature needs (a) a new per-tenant schedule-preference store, (b) a new monthly background-loop mirroring `RetentionSweeper`/`InvoiceGenerator`, and (c) a delivery mechanism — no email infra exists at all, and the only existing outbound channel (`alerting`'s webhook dispatcher) is built for a different domain (alert events, not document delivery). Building all three by 2026-08-02 is a real, non-trivial backend capability addition this design task is explicitly told NOT to invent. Feeds §1 M6/⚠.
- Issue #3 (RBAC is READ-only, already correct): this route is gated on the EXISTING `Permission.AUDIT_READ` (owner/admin/operator/superadmin-own-tenant pass; billing_admin/viewer/member -> 403) — no new permission/plan-feature gate exists or is proposed by this task (mirrors the backend task's own FREEZE-Q2 decision: reuse `AUDIT_READ` only, no new gate for v1).
Related intent: MILESTONE.md goal — "An EU tenant can self-serve produce a dated, Art. 12-mapped record-keeping evidence bundle from the console before EU AI Act GPAI enforcement lands on Aug 2, 2026"; GLOSSARY delta "Art. 12 bundle" / "Bundle token" (both owned by `art12-record-keeping-preset`, cited not redefined here); this task's own exit criterion — "The bundle is generatable, downloadable, and monthly-schedulable from the console, axe-clean."
Ground SHA: `230921a`

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Compliance report center — a new "Compliance" tab in Settings that lets an AUDIT_READ-scoped tenant admin pick a period, generate the full Art. 12 bundle (walking every page to completion), view it rendered in the financial-document/evidence idiom, download it as one JSON file, and set a per-browser monthly reminder.

Framings weighed:
1. **New sibling "Compliance" tab in `SettingsPage`, reusing the Data & residency panel's fieldset/query/mutation idiom** (CHOSEN) — matches the milestone's own phrasing ("extends the Settings → Data & residency panel idiom") at the STYLE level without bloating an already-442-line file with a 4th unrelated concern; keeps each tab a single bounded concept, consistent with every other existing tab (Cache, Guardrails, SSO, Provider Keys, SCIM, SAML, Data & residency).
2. Add a 4th fieldset directly inside `RetentionZdrSettings.tsx` — rejected: the bundle preview (cover + 3 sections + evidence seal + download) is a materially larger, differently-shaped surface than a settings fieldset (it's a generated DOCUMENT, not a policy toggle); folding it in would mix two very different visual/interaction idioms (form-fieldset vs. document-preview) in one file and one query-cache key.
3. A standalone top-level nav page (`/compliance`) outside Settings entirely — rejected: contradicts the milestone's explicit instruction to extend Settings → Data & residency, and would need its own nav-link addition + RBAC-gated nav visibility work not otherwise justified by anything else in this task.

Must:
<must>
  - M1: The Compliance tab (new file `ComplianceReportCenter.tsx`, mounted as a new sibling `TabsTrigger`/`TabsContent` in `SettingsPage.tsx`) renders a period picker — two `Input type="datetime-local"` fields (From/Since, To/Until), both required, reusing `LogsFilterBar.tsx`'s labeled-pair idiom — and a primary "Generate bundle" action, disabled until both fields are filled.
  - M2: "Generate bundle" walks the full `bundle_token` continuation loop against `GET /admin/compliance/art12-bundle` (via `bffGet`) — calling again with the SAME since/until (M14-safe) and the previous response's `bundle_token` — until every section's `has_more` is `false`, accumulating each section's `items` client-side, BEFORE rendering any preview. A partially-assembled bundle is NEVER rendered or offered for download as if it were complete.
  - M3: On successful full assembly, render a financial-document-idiom preview: a dated document header showing the PINNED cover fields (`tenant_name`, `period.since`/`period.until`, `generated_at`, `residency_pin`, `zdr_state.enabled`/`enabled_at`, `retention_window_days`, `default_tier`) via `formatTimestamp`, a NEW `BundleEvidenceSeal` ("Generated & pinned" — modeled on `InvoiceStatusSeal`'s `Badge variant="success"` + `Lock` pattern, new component because the vocabulary differs: every returned bundle is already final, there is no draft state), and per-section row counts (`formatNumber`, `tabular-nums`) with any non-null `note` (the backend's own ZDR/plan-feature honest-degrade text, M8/M9 of the backend contract) rendered VERBATIM and visibly — never suppressed.
  - M4: A "Download bundle (JSON)" button downloads the fully-assembled bundle (cover + all 3 sections' accumulated items, in the shape of one `Art12BundleResponse`-like object with `bundle_token` omitted) as one `.json` file via client-side `Blob` + `URL.createObjectURL` + a synthetic `<a download>` click (no server export endpoint exists for this shape — confirmed at Ground). Filename: `art12-bundle-{tenant_id}-{since}-{until}.json` (ISO dates, colon-stripped) for evidence traceability.
  - M5: Every field/value rendered in the preview traces to a real `CoverModel`/`SectionsModel`/section-item field named in `apps/gateway/src/gateway/compliance/api/router.py` — no invented field, no silently-dropped field.
  - M6: A "Scheduled generation (preview)" fieldset lets the tenant enable a monthly day-of-month reminder, persisted ONLY in `localStorage` (key namespaced by `tenant_id`, mirrors `theme-provider.tsx`'s precedent) — this is a PER-BROWSER, best-effort local reminder, NEVER presented as a guaranteed, server-side, or cross-device schedule. Explicit disclosure copy states plainly that enabling it does not automatically generate or deliver anything — the tenant (or a teammate on a different browser) still has to open this tab and click "Generate bundle" themselves. A REAL server-side scheduled generation+delivery surface is recorded as an explicit follow-up change-request (§7-style note below), not built here.
  - M7: Copy floor — no instance of "compliant" / "makes you compliant" / "GPAI compliance" anywhere on this surface; uses "record-keeping" / "audit-readiness" language, mirroring the ALREADY-shipped `ai-act-readiness` marketing page's own M3/R2 floor and its RED suite (`tests/ai-act-readiness-page.test.tsx` l.85-93).
  - M8: This surface never independently quotes an Art. 101/99 penalty figure (it is a functional console surface, not marketing copy) — if any legal-figure reference is ever added here, it MUST cite Art. 101 (3% global turnover / €15M), never Art. 99 (€35M/7%). Recorded as a standing constraint even though v1's copy carries no figure at all.
  - M9: RBAC — a role lacking `AUDIT_READ` (billing_admin/viewer/member) sees an `ErrorState` (not a form) on this tab, mirroring `RetentionZdrSettings.tsx`'s own `isError -> ErrorState` pattern and `InvoiceDetailPage.tsx`'s 403-copy pattern ("You don't have access to ...").
  - M10: `since > until` in the picker is caught CLIENT-SIDE (mirrors the backend's own `since>until -> PAYLOAD_INVALID`) and blocks "Generate bundle" with an inline `role="alert"` field error — never fires a doomed request.
  - M11: The client-side cursor-assembly loop (M2) is bounded — a maximum page-count ceiling (e.g. 500 pages, i.e. up to 500×5000=2.5M rows per section at the backend's own max `limit`) aborts the walk with a visible, distinct error rather than hanging the tab indefinitely; no partial download is ever offered on this path.
  - M12: `SettingsPage.tsx`'s `Tabs` is lifted to CONTROLLED mode reading/writing a `?tab=` query param — reusing `PlatformTenantDetail.tsx`'s exact pattern (`useSearchParams`-seeded lazy `useState`, the "adjust state during render" re-sync block for browser back/forward, `handleTabChange` calling both `setActiveTab` and `router.replace`) — so `/settings?tab=compliance` opens directly on the Compliance tab. Additive only: any existing bare `/settings` link (no `?tab=` param) still resolves to the SAME default tab as today ("cache").
  - M13: WCAG 2.2 AA — every interactive element on this tab (period inputs, Generate button, Download button, schedule Switch + day-of-month select) has a visible `focus-visible` state, ≥44px hit target, and the panel's heading/landmark order is correct; axe-clean (project's own `jest-axe` convention, cited above).
  - M14: The continuation loop (M2) always resends the EXACT since/until captured at the moment "Generate bundle" was clicked — never re-reads the (possibly since-edited) picker inputs mid-walk — matching the backend's own bundle_token-pins-to-its-minting-period rule; a client bug here would otherwise self-trigger `ERR_CURSOR_INVALID`.
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
</reject>
After:
<after>
  - A tenant admin with `AUDIT_READ` can, from `/settings?tab=compliance`, pick a period, click "Generate bundle", see the FULL bundle (every page walked, never a partial view) rendered in the financial-document idiom with a visible "Generated & pinned" evidence seal and every section's honest-degrade `note` shown verbatim, download it as one JSON file, and optionally turn on a per-browser monthly reminder that is honestly disclosed as local-only — all without this task inventing any new backend endpoint, table, or write path, and without any interactive element failing an axe/WCAG 2.2 AA check.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The "schedule monthly" exit-criterion wording ("monthly-schedulable from the console") is satisfied by a client-only, per-browser, disclosed-as-local-only reminder (M6) rather than a real server-side scheduled generation+delivery pipeline — lowest confidence because Tin may read "schedulable" as implying the console genuinely triggers unattended monthly generation/delivery, which this design deliberately does NOT build (Issue #2: no schedule-preference table, no monthly background loop, no delivery channel exists or is proposed here — building all three by Aug 2, alongside the marketing-page deadline, is a real, multi-piece backend capability addition the milestone's own Scope explicitly rules out as "new export/audit engine capability"). If wrong: the exit criterion reads as unmet even though a literal, honestly-labeled "schedule" control exists — cost is a possible re-open of this task's Must-list plus a NEW dependent backend task (mirroring `RetentionSweeper`/`InvoiceGenerator`'s background-loop shape + a real delivery channel, since none exists today) that cannot land by 2026-08-02. RECORDED FOLLOW-UP CHANGE-REQUEST (not built here): a new task, tentatively `art12-scheduled-generation-delivery`, to add (a) a per-tenant schedule-preference row (mirrors `residency-policy`'s GET/PUT single-row idiom), (b) a monthly background loop mirroring `RetentionSweeper`/`InvoiceGenerator`'s `run_forever()` shape, and (c) a delivery decision (email infra does not exist; the existing `alerting` webhook dispatcher is the nearest reusable channel but is built for a different domain) — sequenced for a LATER milestone/release, after 2026-08-02.
  - [ ] The 500-page ceiling (M11) is a reasonable default for "large but real-world" EU tenants — confirm or tune against an actual EU tenant's expected audit/log/usage row volume once one exists; a wrong ceiling either aborts a legitimate large tenant too early or lets the browser tab hang too long before erroring. Low blast-radius (a client-only constant), easy to revise post-ship.
  - [ ] `localStorage` (not a small new backend preference row) is the right v1 store for the schedule reminder (M6) even though it means the reminder does not follow the tenant across browsers/devices/teammates — confirmed against the design brief's own explicit sanction of a "client-only... stub with disclosed limits" as an acceptable v1 fork; if Tin instead wants the reminder to be tenant-shared (visible to every admin, any browser), that is a small, contained additive backend change (one GET/PUT policy row, no scheduler/delivery) that could still ship by Aug 2 as a SEPARATE, narrowly-scoped change-request — not assumed here.
  - [ ] The new `BundleEvidenceSeal` component (rather than widening `InvoiceStatusSeal`'s `status` union) is the right call — confirmed reasoning: an Art. 12 bundle has no "draft" state (every response IS a generated, pinned snapshot), so a shared component would need a permanently-unused branch; kept as two small, single-purpose components instead of one component with a dead code path.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

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

Scenario: Scheduled generation preference is local-only and honestly disclosed   # M6
  Given the "Scheduled generation (preview)" fieldset is visible
  When the tenant enables the monthly reminder and picks day 1
  Then the preference is written to localStorage under a tenant_id-namespaced key only
  And disclosure copy states plainly that this does not automatically generate or deliver anything
  And no network call is made as a result of toggling this control

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
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

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
  (this task's FE NEVER introduces a new query param, a new response field, or a new error code —
   pure consumption of the frozen shape above)

Schema: NONE touched — this task is FE-only, zero new backend endpoint/table/column.

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
         art12-bundle-{tenant_id}-{since}-{until}.json) -> a "Scheduled generation (preview)"
         fieldset (Switch + day-of-month <select>, localStorage key
         `hydroa.compliance.art12-schedule.${tenant_id}.v1`, value shape
         `{ enabled: boolean; dayOfMonth: number }`, disclosure copy per M6) -> on error: ErrorState
         mapped per errorKind (R2/R3/R4/R5/R6/R7); on "cursor" a "Generate again" button clears
         ALL walk state and starts a fresh un-tokened call (R5)
      -- uses useCurrentUser() for tenant_id (schedule-preference key namespacing + download filename)

Modified existing components (no new exported prop surface beyond what's listed):
  components/settings/SettingsPage.tsx   -- Tabs uplifted from uncontrolled (defaultValue="cache")
    to CONTROLLED (value={activeTab} onValueChange={handleTabChange}), reusing
    PlatformTenantDetail.tsx's exact useSearchParams/useRouter + lazy-useState-seed +
    adjust-during-render re-sync + handleTabChange(router.replace(?tab=...)) pattern verbatim
    (M12); + one new TabsTrigger value="compliance" label "Compliance" + one new TabsContent
    mounting <ComplianceReportCenter />; default tab unchanged ("cache") when no ?tab= param.

Schema (client-only storage, NOT backend):
  localStorage key `hydroa.compliance.art12-schedule.${tenant_id}.v1`
    value: { enabled: boolean; dayOfMonth: number }   -- per-browser, per-tenant-namespaced,
    NEVER read/written by any backend call, NEVER used to gate any access or write (M6)
```

Glossary deltas:
- **Bundle evidence seal**: the `BundleEvidenceSeal` component's visual assertion that a rendered Art. 12 bundle is a fixed, generated snapshot (`Badge variant="success"` + lock icon + sr-only copy) — the Art. 12-bundle-specific counterpart to `InvoiceStatusSeal`'s "issued" state, introduced because a bundle has no draft/issued distinction (every response is already final).
- **Scheduled generation (preview)**: this task's per-browser, `localStorage`-only monthly reminder control — explicitly NOT a server-side scheduled generation or delivery mechanism; the honest v1 answer to the milestone's "schedule monthly" phrasing, with the real server-side capability recorded as a follow-up change-request (§1 ⚠), not built here.

Status: DRAFT

<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

Least-sure flag surfaced at freeze: [spec] "Schedule monthly" is delivered in v1 as a client-only, per-browser `localStorage` reminder with disclosed limits (M6/§1 ⚠) — NOT a real server-side scheduled-generation-and-delivery pipeline, because no schedule-preference table, no monthly background loop, and no email/document-delivery infrastructure exists anywhere in the gateway today (Ground Issue #2), and building all three is a genuinely new backend engine capability the milestone's own Scope explicitly rules out ("Out: ... new export/audit engine capability"). Recommendation: freeze this contract as drafted (ships something honest and real by 2026-08-02) and record the real server-side scheduler+delivery surface as a separate follow-up task (tentatively `art12-scheduled-generation-delivery`) sequenced for a later milestone — Tin should confirm this fork over reopening the milestone's own Scope/deadline now.

Reported: no — awaiting human freeze (this draft, plus the flag above, is the freeze report input)

### Scope (for whoever builds it — non-binding preferred plan, human freezes the shape above, not this list)
May touch:
- `apps/dashboard/lib/art12-bundle.ts` — NEW (pure assembly/cursor-loop function)
- `apps/dashboard/components/compliance/ComplianceReportCenter.tsx` — NEW
- `apps/dashboard/components/compliance/BundleEvidenceSeal.tsx` — NEW
- `apps/dashboard/components/settings/SettingsPage.tsx` — additive (controlled-tab uplift + new tab)
- `apps/dashboard/tests/art12-bundle-assembly.test.ts` — NEW (pure-function suite for `lib/art12-bundle.ts`)
- `apps/dashboard/tests/compliance-report-center.test.tsx` — NEW (component + axe suite)
- `apps/dashboard/tests/settings-page.test.tsx` — NEW (tab addition + `?tab=` deep-link suite; no prior file existed at Ground)
Must NOT touch: `apps/gateway/src/gateway/compliance/api/router.py` (frozen v1, backend-owned by `art12-record-keeping-preset`), any other file under `apps/gateway/` (this task is FE-only), `apps/dashboard/components/settings/RetentionZdrSettings.tsx` (its own 3 fieldsets stay untouched — this task adds a sibling tab, not a 4th fieldset), `apps/dashboard/components/invoices/InvoiceStatusSeal.tsx` (reused as a REFERENCE pattern, not modified — its `status` union is NOT widened), `apps/dashboard/app/(marketing)/ai-act-readiness/page.tsx` (its own RED suite already asserts it never links here pre-ship — do not add that link as part of THIS task without a separate, deliberate change).

Strategy (ordered batches, non-binding preferred plan):
1. `lib/art12-bundle.ts` first — pure `assembleArt12Bundle` + `BundleTooLargeError`, unit-tested against a hand-built mock `fetchPage` sequence (multi-page, error-mid-walk, ceiling-exceeded cases) with ZERO React/DOM — cheapest, highest-value-first slice per TDD discipline.
2. `BundleEvidenceSeal.tsx` — trivial, no state, straight port of `InvoiceStatusSeal.tsx`'s issued-branch shape.
3. `ComplianceReportCenter.tsx` — period picker (LogsFilterBar idiom) -> wire `assembleArt12Bundle` -> preview rendering -> download -> schedule fieldset, in that order; each sub-slice has its own scenario(s) above to drive red-then-green.
4. `SettingsPage.tsx` uplift — copy `PlatformTenantDetail.tsx`'s controlled-tab-via-URL block verbatim, adapted to this file's tab-value set; confirm EVERY existing tab still defaults correctly with no `?tab=` param (regression scenario, not just the new one).
5. Full `jest-axe` pass across every state (loading/error/empty-picker/populated-preview) before calling this done — mirrors the project's own `legal-pages.test.tsx`/`status-page.test.tsx` convention.
6. `pnpm lint`/`tsc`/vitest suite clean; re-run any existing `SettingsPage`-adjacent test to confirm no regression from the controlled-tab uplift. (Backend `ruff`/`pyright` N/A — this task is FE-only.)

Persona (required): ui-designer — Aurora-consistency + WCAG-AA lens; this task's highest-judgment call is visual/structural (financial-document idiom translation for a non-invoice document, the seal-vs-widen-InvoiceStatusSeal decision, the controlled-tab-uplift's default-behavior-preservation) rather than a backend architecture call — advisory, does not lower any gate. `frontend-engineer`'s BFF-trust-boundary and SSR-safety rules still apply verbatim (this task only ever reads via `bffGet`, no new BFF route, no `localStorage` read outside a `useEffect`-safe pattern for the schedule preference's initial read).
Spawn isolation (default): worktree — prefer an isolated worktree for the build/verify spawn per the project's own default, not only for explicit parallel mode.
Known-problem fixes: the lazy-`useState`-initializer-reads-`localStorage` SSR-breakage class (`frontend-engineer` persona's own named rule) applies directly to the schedule-preference read in `ComplianceReportCenter.tsx` — read it inside a `useEffect`, never a lazy `useState` initializer, mirroring `theme-provider.tsx`'s own settled pattern (NOT `PlatformTenantDetail.tsx`'s lazy-initializer-from-`useSearchParams` pattern, which is safe specifically because `useSearchParams` is itself SSR-safe/Suspense-boundary-aware in a way raw `localStorage` reads are not).

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
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0 -->

`apps/dashboard/tests/art12-bundle-assembly.test.ts` † · `apps/dashboard/tests/compliance-report-center.test.tsx` † · `apps/dashboard/tests/settings-page.test.tsx` †

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

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
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- Security is ALWAYS HARD-STOP; record exactly one outcome — no silent pass. The Advisor 3-lens and Refute-read verdicts are audit-measured (`advisor_verdict_unrecorded` · `refute_unrecorded`), never engine-blocked; a human spot-audit backstops anything unrecorded. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): client-side walk abort rate by errorKind (401/403/422-payload/422-cursor/504/too-large) — a spike in "too-large" signals the 500-page ceiling (§1 assumption) needs tuning against real EU-tenant volume; localStorage schedule-preference opt-in rate (signals whether the client-only stub is actually meeting the "schedule monthly" need or whether the real server-side follow-up (§1 ⚠) should be prioritized sooner).

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).
- [SPEC · open] Real server-side scheduled generation+delivery (email or webhook-based, mirroring RetentionSweeper/InvoiceGenerator's background-loop shape) is a recorded follow-up change-request, tentatively `art12-scheduled-generation-delivery`, NOT built in this task (evidence: §1 ⚠, §0 Issue #2 — no schedule-preference table, no monthly background loop, no email infra exists today).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
- [UDD · open] A financial-document idiom (InvoiceStatusSeal/InvoiceDetailPage) does not always transplant its exact vocabulary onto a structurally-similar-but-semantically-different document (an Art. 12 bundle has no draft state) — the lesson is to translate the IDIOM (dated header, tabular-nums, visible immutability marker) rather than force-reuse the exact component/prop union (evidence: BundleEvidenceSeal introduced as a sibling, not an InvoiceStatusSeal prop-union widening).
