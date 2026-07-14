# DESIGN: compliance-report-center — Compliance report center (Settings extension, financial-document idiom, + REAL server-side monthly schedule)

> UDD design-definition artifact (MILESTONE.md eu-ai-act-readiness: "console Compliance report
> center...extending Settings → Data & residency; the generated bundle renders in the Billing
> console's financial-document idiom — it should look like evidence: dated, tabular-nums, visible
> immutability seal"). Companion to `TASK.md` §0–§3 (DRAFT). Wireframes are annotated ASCII
> structure trees, not pixel mocks — every element cites the real component/token it maps to, so
> BUILD has no guessing left. Confirm alongside the TASK.md freeze review.
>
> **REVISION 2026-07-14**: Tin chose real server-side monthly scheduling over the client-only
> reminder originally drafted here (§6 below, entirely replaced). Sections 1-5 and 7-11 describe
> the on-demand generate/download path, UNCHANGED by this revision. Section 6 now covers the REAL
> `ScheduleControl` (OWNER-gated write, any-AUDIT_READ-role read) and the new `GeneratedReportsList`
> inbox — the v1 delivery mechanism for scheduled runs.

---

## 1 · The evidence idiom (borrowed from billing-ui, adapted — not re-invented)

Three rules, directly inherited from `billing-ui/DESIGN.md §1` and applied here to a document
that has NO draft state (every `Art12BundleResponse` returned is already a generated, pinned
snapshot):

1. **Tabular-nums everywhere a count appears.** Every section row-count (`audit_events`,
   `request_log_metadata`, `usage_lineage`) uses `font-mono tabular-nums` via `formatNumber` —
   same token, same convention as `InvoiceLinesTable`'s `request_count`/token columns. Nothing new
   added to `globals.css`.
2. **Visibly-pinned, not visibly-issued.** An Art. 12 bundle has no draft/issued distinction — it
   is a snapshot, fixed the instant `bundle_id`/`generated_at` are minted server-side and echoed
   unchanged across every page of the walk. The seal therefore reads "Generated & pinned", not
   "Issued" — a new `BundleEvidenceSeal` (§3 below), modeled on `InvoiceStatusSeal`'s success-badge
   + lock-icon shape but with NO alternate/draft branch, because none exists.
3. **Honest degrade shown in place, never hidden.** When a section is empty because of ZDR or a
   missing plan feature (the backend's own M8/M9), the returned `note` string is rendered verbatim
   directly under that section's count — never silently presented as "0 rows, no further
   explanation." This is the Art. 12-specific counterpart to billing-ui's "evidence drill-down over
   cross-link" rule: the explanation is answered IN PLACE, from a field the backend already sends.

---

## 2 · Settings mount point + the deep-link resolution

```
apps/dashboard/components/settings/SettingsPage.tsx  (Tabs, now CONTROLLED via ?tab=)
├─ Cache            (unchanged)
├─ Guardrails        (unchanged)
├─ SSO               (unchanged)
├─ Provider Keys     (unchanged)
├─ SCIM              (unchanged)
├─ SAML SSO          (unchanged)
├─ Data & residency  (unchanged — RetentionZdrSettings.tsx, its 3 fieldsets untouched)
└─ Compliance        ◄── NEW sibling tab (this task) — mounts <ComplianceReportCenter />
```

`SettingsPage.tsx` today: `<Tabs defaultValue="cache">`, UNCONTROLLED (Ground SHA `230921a`,
l.29) — no deep-link possible as-is. RESOLVED, not left as a limitation: this task lifts `Tabs` to
the SAME controlled-URL pattern `PlatformTenantDetail.tsx` already proves in this codebase
(`useSearchParams`-seeded lazy `useState`, an "adjust state during render" re-sync block for
back/forward, `handleTabChange` calling `router.replace(?tab=...)`) — copied verbatim, adapted to
this file's 8-value tab set. Result: `/settings?tab=compliance` opens directly on this tab (useful
for a future docs/marketing CTA — NOT wired by this task, since `ai-act-readiness/page.tsx`'s own
frozen RED suite currently asserts it never links here). Every existing bare `/settings` link
(no `?tab=` param) is unaffected — still lands on "cache", exactly as today.

RBAC/nav visibility (this tab is always visible in the tab list — Settings has no per-tab hide
today; the DATA call itself is what gates):

| Role          | Sees "Compliance" tab | AUDIT_READ passes | Renders |
|---------------|:----------------------:|:--------------------:|---------|
| owner         | yes                     | yes                   | form |
| admin         | yes                     | yes                   | form |
| operator      | yes                     | yes                   | form |
| superadmin (own tenant) | yes           | yes                   | form |
| billing_admin | yes                     | **no → 403**          | `ErrorState` (M9) |
| viewer        | yes                     | **no → 403**          | `ErrorState` (M9) |
| member        | yes                     | **no → 403**          | `ErrorState` (M9) |

REVISION — a SECOND, narrower gate applies only to `ScheduleControl`'s WRITE action (the
enable/disable toggle + day-of-month select); every AUDIT_READ role above still SEES the current
schedule state and the Generated reports list read-only:

| Role          | GET schedule / GET reports list+download | PUT/DELETE schedule |
|---------------|:------------------------------------------:|:----------------------:|
| owner         | yes                                          | yes |
| admin         | yes                                          | **no → control disabled client-side; 403 if forced (R10)** |
| operator      | yes                                          | **no → same as admin** |
| superadmin (own tenant) | yes                              | **no → same as admin** |
| billing_admin / viewer / member | **no → 403 (M9, unchanged)** | **no → 403** |

---

## 3 · Compliance tab — idle / picker state

```
┌─ TabsContent "compliance" ───────────────────────────────────────────────────┐
│  Compliance report center                                                    │
│  Generate a dated, Art. 12-mapped record-keeping bundle for a period — audit-│
│  readiness support for your own compliance process, not a compliance claim.  │
│                                                                                │
│  ┌─ fieldset: "Period" ────────────────────────────────────────────────────┐│
│  │  From                          To                                       ││
│  │  [ 2026-07-01T00:00  ▾]        [ 2026-07-31T23:59  ▾]                   ││
│  │  (Input type="datetime-local" ×2 — LogsFilterBar.tsx idiom, verbatim)   ││
│  │                                                                          ││
│  │  [ Generate bundle ]  ← disabled until both dates set; "From must be    ││
│  │                          before To" inline role="alert" if inverted     ││
│  └──────────────────────────────────────────────────────────────────────┘│
└───────────────────────────────────────────────────────────────────────────┘
```

## 4 · Compliance tab — generating state (M2, the cursor-walk in flight)

```
│  [ Generating… ]  (disabled — R8: a second click is a no-op)                 │
│  Assembling your bundle — this may take a moment for a large period.         │
│  (no partial preview rendered — M2: nothing shows until every page is walked)│
```

## 5 · Compliance tab — populated preview (M3/M4/M5, the financial-document idiom)

```
┌─ Document header ─────────────────────────────────────────────────────────┐
│  Art. 12 record-keeping bundle                    [● Generated & pinned 🔒]│  ← BundleEvidenceSeal
│  Acme Corp · Jul 1 – Jul 31, 2026                                          │
│  Generated Aug 1, 2026, 09:14                                              │
│                                                                              │
│  Residency pin: EU        ZDR: Off        Retention: 90 days   Tier: standard│
│  (each traces to CoverModel.residency_pin / zdr_state / retention_window_  │
│   days / default_tier — M5, no invented field)                             │
└─────────────────────────────────────────────────────────────────────────┘

┌─ Sections (Card) ──────────────────────────────────────────────────────────┐
│  Audit events                                          1,204 rows          │  ← tabular-nums
│  Request log metadata                                    892 rows          │
│  Usage lineage                                          3,056 rows         │
└─────────────────────────────────────────────────────────────────────────┘

  WITH a ZDR/plan-feature honest degrade (M3, note shown verbatim, never hidden):
┌─ Sections (Card) ──────────────────────────────────────────────────────────┐
│  Audit events                                          1,204 rows          │
│  Request log metadata                                      0 rows          │
│    ⓘ Zero-Data-Retention has been enabled since 2026-06-01T00:00:00; no    │
│      request-log rows exist while ZDR is on.                               │
│  Usage lineage                                          3,056 rows         │
└─────────────────────────────────────────────────────────────────────────┘

  [ Download bundle (JSON) ]
  (Blob + createObjectURL, filename art12-bundle-{tenant_id}-{since}-{until}.json — M4)
```

## 6 · Compliance tab — REAL scheduled generation control + Generated reports inbox (REVISION — M6, M15-M23)

> Entirely replaces the original client-only "Scheduled generation (preview)" fieldback above.
> No `localStorage`, no "preview" framing — this control writes a real backend row and drives a
> real background loop.

### 6a · `ScheduleControl` — owner can write, any AUDIT_READ role can read

```
┌─ fieldset: "Scheduled generation" ─────────────────────────────────────────┐
│  Generate automatically every month             [○──●]  (Switch)           │
│  Day of month   [ 1 ▾]  (1–28, own <select>)                               │
│                                                                              │
│  ⓘ Generates on day 1 of each month, UTC. The bundle is generated and      │
│    stored automatically — you'll find it in "Generated reports" below.     │
│    No email or notification is sent yet; check back here after the        │
│    scheduled date.                                                        │
│                                                                              │
│  Last run: Jul 1, 2026, 00:04 UTC — success        (last_run_at/status)   │
└─────────────────────────────────────────────────────────────────────────┘

  NON-OWNER (admin/operator/superadmin — AUDIT_READ but not SECURITY_CONFIG):
┌─ fieldset: "Scheduled generation" ─────────────────────────────────────────┐
│  Generate automatically every month             [○──●]  (Switch, disabled) │
│  Day of month   [ 1 ▾]  (disabled)                                         │
│                                                                              │
│  ⓘ Only the tenant owner can change this. Current state shown above is    │
│    live — you can still view it and download from Generated reports.      │
└─────────────────────────────────────────────────────────────────────────┘

  ZDR-SKIPPED last run (last_run_status='skipped_zdr' — honest, not hidden):
│  Last run: Jul 1, 2026, 00:04 UTC — skipped (Zero-Data-Retention is on for │
│  this tenant; scheduled generation does not persist a bundle while ZDR is │
│  on — use "Generate bundle" above for an on-demand copy instead)          │
```

Deliberately NOT a `ConfirmDialog`-gated action (unlike ZDR-enable in `RetentionZdrSettings.tsx`)
— toggling this control only starts/stops FUTURE ticks, never deletes an already-generated report;
a confirm gate would overstate the consequence of a single toggle.

### 6b · `GeneratedReportsList` — the v1 delivery mechanism (in-app inbox, Framing #4)

```
┌─ "Generated reports" ───────────────────────────────────────────────────────┐
│  Period                    Generated              Size        Download     │
│  ─────────────────────────────────────────────────────────────────────────│
│  Jun 1 – Jun 30, 2026       Jul 1, 2026, 00:04 UTC  842 KB     [Download]  │
│  May 1 – May 31, 2026       Jun 1, 2026, 00:03 UTC  790 KB     [Download]  │
│  Apr 1 – Apr 30, 2026       May 1, 2026, 00:05 UTC  ⚠ unavailable (R13)    │
│                                                                              │
│  [ Load more ]  (keyset cursor, mirrors LogsExplorerPage's has_more idiom) │
└─────────────────────────────────────────────────────────────────────────┘

  EMPTY STATE (no schedule ever run yet):
┌─ "Generated reports" ───────────────────────────────────────────────────────┐
│  No reports generated yet — enable Scheduled generation above, or check    │
│  back after the next monthly run.                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

Each row's [Download] is a plain `<a href="/api/gw/admin/compliance/reports/{id}">` — the BFF
catch-all already forwards this path (no new BFF route file, same confirmed-at-Ground precedent
as the on-demand `art12-bundle` route); `Content-Disposition: attachment` on the backend means the
browser downloads natively, no client-side Blob assembly needed here (unlike M4's on-demand path,
which has no server export endpoint to hit). A row whose bytes are unreachable (R13, object store
down) shows an inline "⚠ unavailable" state on THAT row only — the rest of the list stays usable.

## 7 · Compliance tab — error states (R2/R3/R4/R5/R6/R7)

```
403 (M9, R3):
┌─ ErrorState role="alert" ─────────────────────────────────────────────────┐
│ ⚠ You don't have access to the Compliance report center                    │
└─────────────────────────────────────────────────────────────────────────┘

401 mid-walk (R2) / 422 payload mid-walk (R4) / 504 timeout (R6):
┌─ ErrorState role="alert" ─────────────────────────────────────────────────┐
│ ⚠ Generation timed out   /   Something went wrong generating this bundle   │
│   [ Try again ]                                                            │
└─────────────────────────────────────────────────────────────────────────┘
  (period picker's own values are UNCHANGED underneath — never cleared on error)

422 cursor-invalid (R5) — the one path with distinct recovery copy:
┌─ ErrorState role="alert" ─────────────────────────────────────────────────┐
│ ⚠ The bundle could not be completed — please generate it again            │
│   [ Generate again ]  ← starts a completely FRESH, un-tokened call         │
└─────────────────────────────────────────────────────────────────────────┘

Client-side page-count ceiling exceeded (M11, R7):
┌─ ErrorState role="alert" ─────────────────────────────────────────────────┐
│ ⚠ This period is too large to assemble in the browser — narrow the date   │
│   range and try again                                                      │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 8 · Component reuse inventory

| New component | Reuses (verbatim or structural) | New from scratch |
|---|---|---|
| `ComplianceReportCenter.tsx` | `LogsFilterBar`'s since/until `Input type="datetime-local"` pair, `RetentionZdrSettings.tsx`'s fieldset/`useMutation`/`getErrorTitle` shape, `PageHeader`-style document header, `Card`+section rows, `Switch` | the client cursor-walk wiring (via `lib/art12-bundle.ts`), the error-kind → ErrorState-copy map |
| `BundleEvidenceSeal.tsx` | `Badge variant="success"` + `Lock` icon (`InvoiceStatusSeal.tsx`'s issued-branch shape) | no-prop, single-state variant (no draft branch) |
| `lib/art12-bundle.ts:assembleArt12Bundle` | the backend's own `bundle_token`/`has_more` continuation idiom (consumed, not re-implemented) | the client-side full-walk-then-assemble loop itself (Ground Issue #1 — no prior client-side precedent) |
| `SettingsPage.tsx` (modified) | `PlatformTenantDetail.tsx`'s controlled-tab-via-`?tab=` pattern, verbatim | one new `TabsTrigger`/`TabsContent` pair |
| `ScheduleControl.tsx` (REVISION, NEW) | `RetentionZdrSettings.tsx`'s `useQuery`/`useMutation` + role-gated-disabled-control shape (mirrors its own OWNER-only ZDR-toggle gating pattern), `Switch` + `<select>` primitives | the owner-vs-non-owner disabled-with-explanation dual rendering, the `last_run_status='skipped_zdr'` honest-degrade copy |
| `GeneratedReportsList.tsx` (REVISION, NEW) | `LogsExplorerPage.tsx`'s keyset `cursorStack`/`has_more`/"Load more" idiom, plain `<a>` download links (mirrors `InvoiceDetailPage.tsx`'s export-link pattern, but hitting a REAL server endpoint this time, not a client Blob) | the per-row (not whole-list) 503-unavailable degrade state |

Zero new base primitives; zero new `globals.css` tokens; the on-demand path's ONE genuinely new
interaction pattern remains the client-side full-bundle cursor-assembly loop (Ground Issue #1),
isolated in a pure, unit-testable module (`lib/art12-bundle.ts`) precisely because it has no prior
in-repo shape to copy. REVISION adds real backend routes (5 new endpoints) and 2 new tables — see
`TASK.md` §3 for the backend contract; this DESIGN.md file covers FE presentation only.

---

## 9 · Tokens used (all existing — cite, never invent)

| Token | Used for |
|---|---|
| `--font-mono` | tabular-nums section row-counts (evidence idiom rule 1) |
| `--success` / `--success-text` | `BundleEvidenceSeal`'s "Generated & pinned" badge |
| `--muted-foreground` | period sub-label, cover metadata row, disclosure copy |
| `--border`, `--radius-md`, `--radius-lg` | fieldset/card chrome (unchanged from every sibling settings tab) |
| `--destructive` | inline field error text (`role="alert"`, matches `RetentionZdrSettings.tsx`'s own `windowError`/`mutError` styling) |

---

## 10 · Empty / zero-state matrix

| Surface | Zero-data condition | Rendered state |
|---|---|---|
| Period picker | initial load, no period chosen | Generate disabled, no error shown (not yet attempted) |
| A section, ZDR on | `request_log_metadata` empty because ZDR is enabled | count "0 rows" + verbatim `note` (never silently zero) |
| A section, no plan feature | `request_log_metadata` empty because `logs_explorer` not entitled | count "0 rows" + verbatim `note` |
| A section, genuinely empty | e.g. `usage_lineage` empty because the tenant made zero requests that period | count "0 rows", no `note` (backend sends `note: null` — rendered as no explanatory line, which is itself honest: there is nothing to explain) |
| Whole bundle | tenant made zero activity of any kind in the period | preview still renders (cover is always minted); all 3 sections read "0 rows" |

---

## 11 · Responsive & a11y notes

- No new breakpoint: this tab lives inside the existing `/settings` shell layout, unchanged.
- The document header + section rows never require horizontal scroll at any width (no wide table —
  row-count list, not a data grid); if a future iteration adds a full itemized table view, it must
  scroll inside its own bordered container per `LogsTable`/`AuditTable`'s existing convention (not
  introduced by v1 of this task).
- Every interactive element (period inputs, Generate, Download, schedule Switch + day `<select>`)
  has a visible `focus-visible` ring and a ≥44px hit target — mirrors every other Settings tab's
  existing `Input`/`Button`/`Switch` primitives (no new primitive styling introduced).
- The honest-degrade `note` and every `ErrorState` use `role="alert"`/`aria-live="polite"` —
  mirrors `RetentionZdrSettings.tsx`'s own `windowError`/`mutError` announcement pattern — so a
  screen-reader user is told about a degrade or failure without having to re-poll the panel.
- Exactly one `h1` for the whole `/settings` page (via the existing `SettingsPage.tsx` `PageHeader`,
  unchanged by this task); this tab's own "Compliance report center" heading is an `h2`, consistent
  with every other tab's internal heading level.
- Color is never the only state cue: the evidence seal pairs a tint (`success`) with an ICON (lock)
  and TEXT ("Generated & pinned"); the schedule Switch's on/off state is legible from its own
  accessible name + visual track position, not tint alone.
- REVISION: a `disabled` `ScheduleControl` (non-owner) still exposes an accessible explanation
  (`aria-describedby` pointing at the "Only the tenant owner can change this" text), never a bare
  disabled control with no stated reason. `GeneratedReportsList`'s "Load more" button and each row's
  download `<a>` follow the same ≥44px/focus-visible bar as every other interactive element on this
  tab (M22); a row-level "⚠ unavailable" (R13) uses `role="status"` (not `alert` — it's informational
  per-row, not a page-level failure) so it doesn't steal focus from the rest of the list.
