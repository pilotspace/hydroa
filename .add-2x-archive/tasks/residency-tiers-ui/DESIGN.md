# DESIGN: residency-tiers-ui — Data & residency, region badges, tier selector, pricing story

> UDD design-definition artifact. Companion to `TASK.md` §0–§3 (DRAFT — never freeze from this
> file). Wireframes are annotated ASCII structure trees, not pixel mocks — every element cites the
> real component/token it maps to, so BUILD has no guessing left. Confirm alongside the TASK.md
> freeze review. Signature element (milestone-named): the plain-language CONSEQUENCE LINE — every
> dangerous setting states its consequence in one sentence a lawyer and an engineer both accept.

---

## 1 · The consequence-line idiom (the one behavioral pattern this task introduces)

Two rules, applied consistently everywhere a residency pin can be set:

1. **Every tightening write is preceded by a written, verbatim consequence sentence** inside the
   EXISTING `ConfirmDialog` (`components/teams/ConfirmDialog.tsx`) — never a generic "Are you
   sure?". The sentence names BOTH the refusal behavior ("refused, not rerouted") and the
   realtime-voice loss (residency-policy's own DECIDED-at-freeze consequence, which this fieldset
   is the literal place that "states it"). No new dialog component — same primitive as ZDR.
2. **A loosening write (clearing a pin) is never gated.** Mirrors ZDR's own asymmetry exactly:
   destructive/tightening = confirm; safe/loosening = immediate. This is a BEHAVIORAL pattern
   applied to a NEW control, not a new visual pattern — the one new VISUAL element this task adds
   is `RegionBadge` (§5).

---

## 2 · Page — Settings → Data & residency (`/app/settings`, tab renamed)

```
┌─ PageHeader ────────────────────────────────────────────────────────────────┐
│ Settings                                                                    │
│ Manage cache, guardrails, SSO, and provider keys for your tenant.           │
└───────────────────────────────────────────────────────────────────────────┘

┌─ TabsList ───────────────────────────────────────────────────────────────────┐
│ Cache │ Guardrails │ SSO │ Provider Keys │ SCIM │ SAML SSO │ Data & residency │ ← renamed
└───────────────────────────────────────────────────────────────────────────┘   (was "Retention & ZDR")

┌─ TabsContent value="retention" (RetentionZdrSettings.tsx) ──────────────────┐
│                                                                              │
│ ┌─ fieldset: Retention window ───────────────────────────────┐  UNCHANGED   │
│ │ Window (days)  [___30___]           [Save window]           │             │
│ │ Inherits operator default. Operator ceiling: 90 days.        │             │
│ └───────────────────────────────────────────────────────────┘             │
│                                                                              │
│ ┌─ fieldset: Effective window by store ──────────────────────┐  UNCHANGED   │
│ │ (7-row table, unchanged)                                     │             │
│ └───────────────────────────────────────────────────────────┘             │
│                                                                              │
│ ┌─ fieldset: Zero-Data-Retention (ZDR) ───────────────────────┐  UNCHANGED   │
│ │ Enable Zero-Data-Retention                          [○──●]   │             │
│ └───────────────────────────────────────────────────────────┘             │
│                                                                              │
│ ┌─ fieldset: Data residency ─────────────────────────────────┐  NEW (M1-M5) │
│ │ legend: "Data residency"                                     │             │
│ │                                                                │             │
│ │  Pin inference region                                         │             │
│ │  ( ) No pin (unrestricted)          ← seededData.region==null │             │
│ │  ( ) US                                                       │             │
│ │  ( ) EU                                                       │             │
│ │  (⊘) AP  — disabled, greyed radio                             │             │
│ │      "Not available yet — Asia-Pacific residency pinning      │             │
│ │       is a tracked follow-up"                    (M2, R1)     │             │
│ │                                                                │             │
│ │  [Save]                                                       │             │
│ │  (pin currently: "EU", set Jul 10 2026 — echoes updated_at)   │             │
│ └───────────────────────────────────────────────────────────┘             │
└───────────────────────────────────────────────────────────────────────────┘

CONFIRM DIALOG (opens on Save when the new selection tightens/changes a pin — M3):
┌─ ConfirmDialog (existing component, unchanged shape) ───────────────────────┐
│ Pin residency to EU?                                                        │
│                                                                              │
│ "Pinning to EU means requests that cannot run in the EU will be refused,   │
│  not rerouted. This also blocks realtime voice for this tenant — no        │
│  realtime model is region-tagged yet."                                     │
│                                                                              │
│                                        [ Cancel ]   [ Pin to EU ]           │
└──────────────────────────────────────────────────────────────────────────┘
   (US pin: identical structure, "EU" -> "US" throughout, verbatim per TASK.md §3)

CLEARING A PIN (M4) — no dialog, PUT fires the instant Save is clicked:
  "No pin (unrestricted)" selected + Save -> PUT {region:null} immediately, mirrors ZDR-disable.

NON-OWNER (M5) — same picker, same Save button, no client-side hiding; a 403 on Save renders:
┌──────────────────────────────────────────────────────────────────────────┐
│ ⚠ You don't have permission to change the residency policy.  (role="alert")│
└──────────────────────────────────────────────────────────────────────────┘
```

Component reuse: `Switch`→`radio group` (native `<input type="radio">` styled to match the
existing `Input`/`Switch` visual family — no new form-control primitive), `Button`, `ConfirmDialog`
(verbatim), the existing `mutError role="alert"` pattern. New: the "Data residency" `<fieldset>`
block (inline in `RetentionZdrSettings.tsx`, not a separate file — mirrors how the ZDR fieldset
itself is inline, not extracted).

---

## 3 · Page — Catalog (`/app/models`, `ModelsPage.tsx`)

```
┌─ header ─────────────────────────────────────────────────────────────────────┐
│ Models                                          [Re-sync catalog] (owner/admin)│
│ Enable or disable individual catalog models for your tenant.                  │
└───────────────────────────────────────────────────────────────────────────┘

┌─ DataTable ───────────────────────────────────────────────────────────────────────────────┐
│ Model              │ Context length │ Inputs         │ Region  │ Enabled                  │
├────────────────────┼────────────────┼────────────────┼─────────┼──────────────────────────┤
│ claude-opus-4       │ 200,000        │ [text][image]  │ [ EU ]  │  ○──●                    │  ← eligible, normal
│   eu.anthropic...   │                │                │         │                          │
├────────────────────┼────────────────┼────────────────┼─────────┼──────────────────────────┤
│ claude-opus-4-us     │ 200,000        │ [text][image]  │ [ US ]  │  ○──○  (disabled, muted) │  ← M7: tenant pinned EU,
│   us.anthropic...    │                │                │         │  [Ineligible in EU]      │     this row is us-only
├────────────────────┼────────────────┼────────────────┼─────────┼──────────────────────────┤
│ gpt-4o-mini          │ 128,000        │ [text]         │[GLOBAL] │  ○──○  (disabled, muted) │  ← global also ineligible
│                      │                │                │         │  [Ineligible in EU]      │     for a specific pin (M6 semantics)
└────────────────────┴────────────────┴────────────────┴─────────┴──────────────────────────┘
```

`RegionBadge` (`Badge variant="outline"`) renders `US`/`EU`/`AP`/`GLOBAL` — same visual weight as
the existing `input_modalities` badges (M6). The `[Ineligible in EU]` badge is a SECOND, distinct
`Badge variant="warning"` stacked below the Region badge — never replacing it (an admin still
needs to see the row's actual region, not just that it's excluded).

Degrade path (M8) — `GET /admin/residency-policy` fails while `GET /admin/models` succeeds:
```
│ claude-opus-4-us     │ 200,000        │ [text][image]  │ [ US ]  │  ○──●                    │  ← Region badge still
│   us.anthropic...    │                │                │         │  (no ineligibility badge) │     shows; no dimming
```
No page-level `ErrorState` — the table renders exactly as it would for an unpinned tenant.
Unpinned tenant (`residency_region == null`): zero rows dimmed, identical to pre-this-task table.

---

## 4 · Dialog — Create API Key (`CreateKeyDialog.tsx`)

```
┌─ dialog role="dialog" aria-modal="true" ──────────────────────────────────┐
│ Create API Key                                                            │
│                                                                            │
│ Key Name        [________________________]                               │
│                                                                            │
│ Service tier                                                              │
│  (●) Standard      (default)                                             │
│  ( ) Priority       +25% on requests using this key      ← M10, server-  │
│                                                              sourced, or   │
│                                                              "Pricing      │
│                                                              pending" (R4) │
│                                                                            │
│  "Priority requests get preference under contention and may fall back    │
│   to Standard when capacity is unavailable — Standard is never starved." │
│                                                                            │
│                                          [ Cancel ]     [ Create ]        │
└──────────────────────────────────────────────────────────────────────────┘

DEGRADE (service-tiers pricing endpoint absent/unshipped — M10, R4):
  (●) Standard
  ( ) Priority       Pricing pending          ← inert text, no fetch retried,
                                                 selector still fully usable
```

Component reuse: `Input`, `Button` (unchanged from today's dialog), a new small `TierSelector`
(radio-group, same native-input styling as the residency picker in §2 — ONE shared visual
treatment for "pick one of a few options," not two competing patterns). Price-delta line is plain
text, `text-sm text-muted-foreground`, matching `PlanSeatsPage`'s "Seat pricing coming soon" tone
exactly when in the pending state.

---

## 5 · Page — Marketing pricing (`(marketing)/pricing/page.tsx`)

```
┌─ existing hero (unchanged): "Pricing" / "Usage-based pricing" badge ──────┐
└────────────────────────────────────────────────────────────────────────┘

┌─ Card: Team ────────────────────┐  ┌─ Card: Enterprise ──────────────────┐
│ $99/mo + usage                  │  │ Contact us                          │
│ • Unlimited users                │  │ • SSO/OIDC + role-based access      │
│ • BYOK + key governance          │  │ • Audit-ready logs & data retention │
│ • Rate limiting & bandwidth      │  │ • Per-tenant SLOs & observability   │
│ • Spend analytics & alerting     │  │ • Dedicated support & SLA           │
│ • Priority service tier          │  │ • Data residency: pin inference     │  ← NEW bullets
│   (optional, usage-priced)  NEW  │  │   to US or EU                  NEW  │
│ • Email support                  │  │                                      │
└──────────────────────────────────┘  └──────────────────────────────────────┘

┌─ NEW static callout section (below the 3-card grid, above the fold-end) ──┐
│  Data residency & priority routing                                        │
│                                                                            │
│  Pin inference to a region — EU or US — with a fail-closed policy: a      │
│  request that cannot run in your pinned region is refused, never silently │
│  rerouted. Need priority throughput? Priority-tier keys get preference    │
│  under contention, with Standard traffic never starved.                   │
└──────────────────────────────────────────────────────────────────────────┘
```

Zero fetch — Server Component, matches the page's own frozen "representative placeholders, not a
commitment" posture (M11). `Badge`/`Card` reused verbatim from the existing page.

---

## 6 · RBAC matrix

| Surface / action                                    | owner | admin | member | Backend gate (ground truth) |
|-------------------------------------------------------|:-----:|:-----:|:------:|------------------------------|
| View "Data & residency" tab + read the pin             | yes   | yes   | yes    | `GET /admin/residency-policy` — any authenticated role (residency-policy M2) |
| Change/clear the residency pin                         | yes   | **403 inline** | **403 inline** | `PUT /admin/residency-policy` — `Permission.SECURITY_CONFIG`, OWNER-only |
| View catalog Region badges + ineligibility badges       | yes   | yes   | yes    | `GET /admin/models` — no role restriction found in `get_admin_models_session` |
| Trigger "Re-sync catalog"                               | yes   | yes   | hidden (existing `canManage`, unchanged) | pre-existing, not touched by this task |
| Open Create API Key dialog / pick a tier                | yes   | yes   | hidden (existing convention — button always renders; backend 403s) | `POST /admin/keys` — `require_owner_or_admin` |
| Read the marketing `/pricing` page                      | n/a — public, unauthenticated | | | none (public route) |

No new RBAC surface is introduced anywhere (§0 Issue #5) — every gate above is either an existing
gate (catalog GET, key creation) or a directly-cited frozen sibling gate (residency PUT).

---

## 7 · Empty / error / degrade states matrix

| Surface | Condition | Rendered state |
|---|---|---|
| Data residency fieldset | `GET /admin/residency-policy` loading | shares the SAME `Loading` treatment already used by Retention/ZDR (or its own inline skeleton row if independently loading — Build decides, does not change observable behavior) |
| Data residency fieldset | `GET /admin/residency-policy` errors | inline `ErrorState`-equivalent scoped to JUST this fieldset — Retention/ZDR fieldsets still render (independent-read degrade, mirrors M8's read-degrade applied here to a write-surface's own read) |
| Data residency fieldset | `PUT` 403 (non-owner) | inline `mutError`, value reconciles from cache (M5, R3) |
| Data residency fieldset | `PUT` 422 (defensive, R1 should prevent) | inline error, value reconciles (R2) |
| AP option | always (v1) | rendered, disabled, helper text — never hidden (M2) |
| Catalog table | `GET /admin/residency-policy` fails, `GET /admin/models` succeeds | Region badges render; no ineligibility treatment; NO page-level error (M8) |
| Catalog table | both reads fail | existing `ErrorState` (unchanged pre-existing behavior for `GET /admin/models` failure) |
| Catalog table | tenant has no pin | zero rows dimmed — byte-identical to pre-this-task table |
| Tier selector price-delta | `GET /admin/service-tier-pricing` succeeds | server-computed "+N% on requests using this key" |
| Tier selector price-delta | endpoint 404/5xx/unavailable | "Pricing pending" placeholder, zero retry (M10, R4) |
| Tier selector | any state | selector itself always rendered + submittable — pricing availability never blocks the FEATURE |
| Create API Key dialog | `tier` rejected by backend | existing 422-field / else-global error branching (R5), no new path |
| Marketing pricing page | any | always static — no loading/error state possible (Server Component, no fetch) |

---

## 8 · Component reuse inventory

| New component | Reuses (verbatim or structural) | New from scratch |
|---|---|---|
| `RegionBadge` | `Badge variant="outline"` | uppercase-region text mapping only |
| `TierSelector` | native radio-input styling (matches the residency picker), `text-sm text-muted-foreground` price-delta line (matches `PlanSeatsPage`'s "coming soon" tone) | the priority/standard copy + price-delta formatting |
| Data residency fieldset | `ConfirmDialog`, `Button`, existing `mutError`/`windowError role="alert"` pattern, `RetentionZdrSettings`'s `handleZdrToggle`/`handleZdrConfirmClose` control-flow shape | the 3-option radio group + per-region consequence copy |
| `ModelsPage` Region column | existing `input_modalities` column's position/cell shape | the ineligibility cross-reference read + warning badge |
| `CreateKeyDialog` extension | existing `Input`/`Button`/Zod-schema/error-branching shape | `tier` field + price-delta read |
| Marketing pricing page | existing `Card`/`Badge`/bullet-list structure | 2 bullets + 1 static callout section |

Zero new base primitives (Badge, Button, ConfirmDialog, Input all pre-exist); zero new
`globals.css` tokens; zero new BFF route files (existing catch-all proxy covers everything).

---

## 9 · Tokens used (all existing — cite, never invent)

| Token / variant | Used for |
|---|---|
| `Badge variant="outline"` | `RegionBadge` — neutral descriptor, not a status |
| `Badge variant="warning"` | "Ineligible in {REGION}" badge on a catalog row |
| `--destructive` (via `ConfirmDialog`'s existing confirm-button styling) | the Pin-to-{REGION} confirm action, matching ZDR's own destructive-confirm button tone |
| `--muted-foreground` | disabled/ineligible row dimming, "Pricing pending" text, AP helper text |
| `text-sm`, `role="alert"`, `aria-live="polite"` | every new inline error message (residency PUT failures) — same idiom as `windowError`/`mutError` |
| `--border`, `--radius-md` | fieldset/dialog chrome — unchanged from every sibling surface |

---

## 10 · Responsive & a11y notes

- No new breakpoint — the residency fieldset, tier selector, and catalog column all live inside
  existing responsive containers (`Tabs`, `DataTable`, dialog overlay) that already handle narrow
  widths.
- The catalog table's new "Region" column follows the SAME horizontal-scroll-inside-its-own-
  container convention as every other `DataTable`/`Table` in this codebase — the page body itself
  never scrolls horizontally.
- Region/ineligibility state is conveyed through THREE redundant cues, never color alone: (1) the
  `RegionBadge` text itself (US/EU/AP/GLOBAL — legible independent of color), (2) the separate
  "Ineligible in {REGION}" text badge, (3) the disabled `Switch`'s own reduced-opacity + inert
  `aria-disabled` state (WCAG 1.4.1).
- The residency radio group and the tier radio group share ONE interaction pattern (native
  `<input type="radio">`, arrow-key navigable, visible `focus-visible` ring) — computed contrast
  ≥3:1 for the focus ring against both the light and dark `--border`/`--background` pairs (to be
  confirmed with an actual contrast computation at Build, not eyeballed, per the ui-designer
  persona's Default Requirement).
- `ConfirmDialog`'s existing focus-trap (Escape closes, Tab wraps, focus returns to the
  triggering Save button) is reused verbatim — no new focus-management code.
- Every new interactive control (radio options, Save, Confirm/Cancel, tier radios) targets a
  ≥44×44px hit area, matching the existing `Switch`/`Button` sizing already shipped.
- `axe(container, { rules: { "color-contrast": { enabled: false } } })` runs against every new
  surface via the EXISTING `tenant-settings.test.tsx` / `model-mgmt.test.tsx` sweeps (M12) — zero
  new serious/critical violations is the bar, not just "no regressions on old surfaces."

---

## 11 · Open items carried into the TASK.md §3 freeze flag

1. `ap` cannot be pinned today (residency-policy's frozen enum excludes it) — this design offers
   it disabled with an explanatory note rather than silently omitting or silently allowing a
   guaranteed-422 submission. Needs a Tin decision: accept the v1 scope-cut, or reopen
   residency-policy first.
2. service-tiers (this task's own stated dependency) is still blank — the `tier` field name and
   `GET /admin/service-tier-pricing` shape are FORWARD-CITED ASSUMPTIONS, degraded gracefully if
   wrong, but a real risk to this contract's stability until service-tiers freezes.
3. "Typed confirm gate" (milestone/slug wording) is interpreted as written consequence copy inside
   the existing `ConfirmDialog`, not a literal type-to-confirm text field — flagged in case that
   reading is wrong.
