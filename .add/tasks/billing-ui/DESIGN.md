# DESIGN: billing-ui — Tenant Billing console (Aurora financial-document idiom)

> UDD design-definition artifact (MILESTONE.md: "UDD design-definition loop required for billing-ui
> before its build"). Companion to `TASK.md` §0–§3 (DRAFT). Wireframes are annotated ASCII structure
> trees, not pixel mocks — every element cites the real component/token it maps to, so BUILD has no
> guessing left. Confirm alongside the TASK.md freeze review.

---

## 1 · The financial-document idiom (the one new visual pattern this task introduces)

Three rules, applied consistently across all three pages + the evidence drawer:

1. **Tabular-nums money & tokens.** Every currency, token-count, and request-count column uses
   `font-mono tabular-nums` (the existing `--font-mono` token, already the v7 "spec-sheet" numerals
   convention — see `PageHeader`'s meta strip, `MemoryScoreBar`'s score). Digits align vertically
   column-over-column; nothing new is added to `globals.css`.
2. **Visibly-immutable issued documents.** An `issued` invoice never renders an edit affordance —
   not a disabled button, not a greyed input: the absence is total, because the frozen backend
   contract has no PUT/PATCH to back one. The ONLY marker of state is the `InvoiceStatusSeal` chip
   (success-tinted "Issued" + lock icon vs. secondary-tinted "Draft") plus one line of sr-only copy.
   Corrections are presented as new, separately-dated rows underneath — never as a diff against the
   original lines.
3. **Evidence drill-down over cross-link.** Every invoice line's provenance is answered IN PLACE (a
   drawer listing the underlying `usage_records` rows), never by navigating away to Logs Explorer —
   both because the frozen `UsageEvidenceItem` shape carries no `log_id` to link with, and because a
   `billing_admin` viewing an invoice may lack `LOGS_READ` (a cross-link could 403 for a legitimate
   invoice viewer).

---

## 2 · Nav — Billing group placement

```
Sidebar (apps/dashboard/components/ui/app-shell.tsx:NAV_GROUPS)
├─ Playground   (unchanged: Chat · Voice · Memory · Artifacts · Vision · Video)
├─ Insights     (unchanged: Usage · Spend)
├─ Billing      ◄── NEW GROUP (this task)
│   ├─ 🧾 Invoices        /app/invoices        minRole:"admin"  (icon: FileText)
│   ├─ 💳 Credits         /app/credits         (no minRole — any role)
│   └─ 🎫 Plan & seats    /app/plan            (no minRole — any role)
├─ Configure    (unchanged: API Keys · Model Presets · Models · Routing · Batches)
└─ Govern       (unchanged: Teams · Members · Alerts · Audit · Logs · Health · SLO · Guardrail Analytics)
   ── hairline ──
   Settings (pinned, unchanged)
   Platform (superadmin-only allowlist group, unchanged)
```

RBAC/nav visibility matrix (who sees the link vs. who actually passes the gateway check):

| Role          | Sees "Invoices" link | `INVOICES_READ` passes | Sees "Credits" / "Plan & seats" |
|---------------|:---------------------:|:------------------------:|:---------------------------------:|
| owner         | yes                   | yes                       | yes                               |
| admin         | yes                   | yes                       | yes                               |
| billing_admin | yes                   | yes                       | yes                               |
| superadmin    | yes                   | yes                       | yes                               |
| operator      | yes                   | **no → 403 (M9 inline)**  | yes                               |
| viewer        | yes                   | **no → 403 (M9 inline)**  | yes                               |
| member        | **no (hidden, M1)**   | no                        | yes                               |

---

## 3 · Page — Invoices list (`/app/invoices`)

```
┌─ PageHeader ────────────────────────────────────────────────────────────────┐
│ Invoices                                                                    │
│ Monthly statements derived from this tenant's usage — immutable once issued.│
└───────────────────────────────────────────────────────────────────────────┘

┌─ Table (Card) ────────────────────────────────────────────────────────────┐
│ Period          │ Status          │ Total          │                      │
├─────────────────┼─────────────────┼────────────────┼──────────────────────┤
│ Jun 2026         │ [● Issued]      │      $1,204.55 │  →  row click        │
│ May 2026         │ [● Issued]      │        $988.10 │  →  navigates to     │
│ Jul 2026 (draft) │ [○ Draft]       │        $312.40 │     /app/invoices/{id}│
└─────────────────┴─────────────────┴────────────────┴──────────────────────┘
            ◀ Previous            Next ▶        (cursorStack idiom, LogsExplorerPage-shaped)

EMPTY (zero invoices):
┌─ Empty ──────────────────────────────────────────────────┐
│         (Inbox icon)                                      │
│   No invoices yet                                         │
│   A monthly statement appears after your first billable   │
│   usage.                                                   │
└─────────────────────────────────────────────────────────┘

ERROR (member direct-URL or operator/viewer 403):
┌─ ErrorState role="alert" ───────────────────────────────┐
│ ⚠ You don't have access to Invoices                      │
└──────────────────────────────────────────────────────────┘
```

Component reuse: `PageHeader`, `Card`+`Table`/`TableHeader`/`TableRow`/`TableCell`, `Badge`
(status seal), `Loading`, `Empty`, `ErrorState` — all existing. New: `InvoicesListPage.tsx`
(orchestrator, mirrors `LogsExplorerPage.tsx`'s shape), `InvoiceStatusSeal.tsx`.

---

## 4 · Page — Invoice detail (`/app/invoices/[invoiceId]`)

```
┌─ PageHeader ────────────────────────────────────────────────────────────────┐
│ Statement — June 2026                              [● Issued 🔒]            │
│ Issued Jul 3, 2026                                                          │
│ "This document is final and cannot be edited." (sr-only, redundant w/ seal) │
└───────────────────────────────────────────────────────────────────────────┘

┌─ InvoiceLinesTable (Card) ──────────────────────────────────────────────────┐
│ Model         │Team │Key    │Tags        │Reqs │Tokens(p/c)  │Amount │Evid.│
├───────────────┼─────┼───────┼────────────┼─────┼─────────────┼───────┼─────┤
│ gpt-4o-mini   │ Eng │ k_a1  │ {}          │ 812 │ 40k / 12k   │$18.40 │ 🔍  │
│ claude-opus-4 │ Ops │ k_b2  │ {env:prod}  │  56 │  8k /  3k   │$41.02 │ 🔍  │
│ …                                                                          │
├───────────────┴─────┴───────┴────────────┴─────┴─────────────┴───────┴─────┤
│                                                            Total  $1,204.55 │  ← tabular-nums,
└──────────────────────────────────────────────────────────────────────────┘    formatUsd(total_usd)

┌─ InvoiceCorrectionsTable (Card) ─────────────────────────────────────────────┐
│ Corrections                                                                 │
│  ▼ -$12.50   "duplicate line"      by ops@tenant.io   Jun 14, 2026          │
│                                                                              │
│  (zero corrections) → "No corrections" (plain text, section stays visible) │
├──────────────────────────────────────────────────────────────────────────┤
│                                                 Corrected total  $1,192.05  │
└──────────────────────────────────────────────────────────────────────────┘

  [ Download PDF ]   [ Download CSV ]     ← <a href="/api/gw/admin/invoices/{id}/export?format=…"
                                              download>, zero new BFF code

  ┌─ InvoiceEvidenceDrawer (opens on "🔍 View evidence") ───────────────────┐
  │  ✕                                                    Evidence          │
  │  gpt-4o-mini · Eng team · key k_a1 · {} tags                            │
  │ ──────────────────────────────────────────────────────────────────────│
  │  Time            Model         Tokens(p/c)   Cost      Request ID      │
  │  10:02:14        gpt-4o-mini   512 / 128     $0.021    req_9f3c…       │
  │  10:04:51        gpt-4o-mini   488 / 140     $0.019    req_a01e…       │
  │  …                                                                      │
  │              ◀ Previous            Next ▶                              │
  │  (Radix Dialog: focus-trapped, Escape closes, focus returns to the      │
  │   "🔍" that opened it — mirrors LogDetailDrawer.tsx exactly)            │
  └──────────────────────────────────────────────────────────────────────┘
```

Component reuse: `PageHeader`, `Card`+`Table`, `Badge` (correction delta tone, mirrors `StatCard`'s
up/down convention), `Dialog`/`DrawerContent`/`DialogTitle`/`DialogDescription`, `Loading`,
`ErrorState`. New: `InvoiceDetailPage.tsx`, `InvoiceLinesTable.tsx`, `InvoiceCorrectionsTable.tsx`,
`InvoiceEvidenceDrawer.tsx`, `InvoiceStatusSeal.tsx` (shared with the list page).

---

## 5 · Page — Credits (`/app/credits`)

```
┌─ PageHeader ────────────────────────────────────────────────────────────────┐
│ Credits                                                                    │
│ Prepaid balance and top-up history for this tenant.                        │
└───────────────────────────────────────────────────────────────────────────┘

┌─ StatCard (hero) ───────────────┐
│ Balance                         │
│        $42.50                   │  ← tabular-nums, accent-soft tint
│ + $5.00 grace                   │  (only rendered when grace_usd > 0)
└──────────────────────────────────┘

┌─ CreditsHistoryTable (Card) ────────────────────────────────────────────────┐
│ Date            │ Type          │ Amount    │ Balance after                │
├─────────────────┼───────────────┼───────────┼──────────────────────────────┤
│ Jul 10, 14:02    │ [topup]        │ +$50.00  │ $92.50                       │
│ Jul 10, 09:11    │ [hold]         │ -$0.50   │ $42.50                       │
│ Jul 10, 09:11    │ [settle]       │ +$0.29   │ $42.79                       │
└─────────────────┴───────────────┴───────────┴──────────────────────────────┘
            ◀ Previous            Next ▶

EMPTY — covers BOTH "genuinely unused" AND "credits_gate_enabled=False platform-wide"
(no API signal distinguishes them; see TASK.md §1 Framings, ⚠2):
┌─ Empty ──────────────────────────────────────────────────┐
│         (Inbox icon)                                      │
│   No credit activity yet                                  │
│   Your platform operator manages credit top-ups — contact │
│   them to add credits to this account.                    │
└─────────────────────────────────────────────────────────┘
       (section is NEVER hidden — always reachable via nav, per M6)
```

Component reuse: `PageHeader`, `StatCard`, `Card`+`Table`, `Badge` (entry_type — topup=success,
hold=warning, settle=default, release=default, correction=warning), `Empty`, `Loading`,
`ErrorState`. New: `CreditsPage.tsx`, `CreditsHistoryTable.tsx`.

---

## 6 · Page — Plan & seats (`/app/plan`)

```
┌─ PageHeader ────────────────────────────────────────────────────────────────┐
│ Plan & seats                                                                │
│ Your tenant's assigned plan and the limits it enforces.                     │
└───────────────────────────────────────────────────────────────────────────┘

┌─ Card: Current plan ─────────────────────────────────────────────────────────┐
│ Team                                                          [seat_cap:25] │
│                                                                              │
│ Budget                                                                      │
│  ┌────────────────────────────────────────────────────────┐               │
│  │████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│  $120 / $500  │  ← EntitlementMeter
│  └────────────────────────────────────────────────────────┘               │     role="progressbar"
│                                                                              │
│ Models            All models                                               │
│ Features           [logs_explorer]  [batch]                                │
│ Seats              6 of 25                                                 │
│ Requests/min (plan reference)     600                                      │
│ Tokens/min (plan reference)       120,000                                  │
│                                                                              │
│ ┌─ Per-seat pricing ───────────────────────────────────────┐               │
│ │ Seat pricing coming soon.                                 │  ← inert    │
│ └───────────────────────────────────────────────────────────┘    placeholder
└──────────────────────────────────────────────────────────────────────────┘

UNPLANNED TENANT variant (plan === null, R6):
┌─ Card: Current plan ─────────────────────────────────────────────────────────┐
│ No plan assigned — usage governed by tenant-level defaults only.            │
│                                                                              │
│ Budget                                                                      │
│  ┌────────────────────────────────────────────────────────┐               │
│  │██████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░│  $30 / $100   │  (tenant's own
│  └────────────────────────────────────────────────────────┘                explicit budget,
│                                                                              still meaningful)
│ Models             All models    Features    None                          │
└──────────────────────────────────────────────────────────────────────────┘
   (or Budget: "$30 · Unlimited" with NO progressbar if the tenant also has no explicit budget)
```

Component reuse: `PageHeader`, `Card`+`CardHeader`+`CardContent`, `Badge` (seat_cap chip, feature
flags), `Loading`, `ErrorState`. New: `PlanSeatsPage.tsx`, `EntitlementMeter.tsx` (generalizes
`MemoryScoreBar`'s `role="progressbar"` idiom for a $-ceiling instead of a 0–1 score).

---

## 7 · Component reuse inventory

| New component                  | Reuses (verbatim or structural)                                  | New from scratch |
|---------------------------------|--------------------------------------------------------------------|-------------------|
| `InvoicesListPage`               | `LogsExplorerPage`'s cursorStack/Next/Previous shape                | orchestration only |
| `InvoiceDetailPage`              | `PageHeader`, `Card`, `Table`                                       | layout composition |
| `InvoiceStatusSeal`              | `Badge` variants (success/secondary)                                 | the seal wrapper |
| `InvoiceLinesTable`              | `Table`, `formatUsd`/`formatNumber`                                  | grouping columns |
| `InvoiceCorrectionsTable`        | `Table`, `StatCard`'s delta tone convention                          | signed-delta row |
| `InvoiceEvidenceDrawer`          | `LogDetailDrawer`'s Dialog/DrawerContent + focus-return idiom         | paginated-list body |
| `CreditsPage`                    | `StatCard`, `Empty`                                                   | hero + grace note |
| `CreditsHistoryTable`            | `Table`, `Badge` (entry_type tone)                                    | — |
| `PlanSeatsPage`                  | `Card`, `Badge`                                                       | no-plan variant |
| `EntitlementMeter`               | `MemoryScoreBar`'s `role="progressbar"` null-branch idiom              | $-ceiling variant |

Zero new base primitives; zero new `globals.css` tokens; one new backend route file
(`plan_router.py`, M8).

---

## 8 · Tokens used (all existing — cite, never invent)

| Token | Used for |
|---|---|
| `--font-mono` | tabular-nums money/token/count columns (financial-document idiom rule 1) |
| `--success` / `--success-text` | "Issued" seal, "topup" entry-type badge |
| `--warning` / `--warning-foreground` | "hold"/"correction" entry-type badges |
| `--destructive` / `--destructive-text` | over-budget `EntitlementMeter` tone |
| `--accent-soft` / `--accent-soft-border` | Credits hero `StatCard` tint |
| `--border`, `--radius-md`, `--radius-lg` | table/card chrome (unchanged from every sibling surface) |
| `--muted-foreground` | secondary/meta text (period sub-label, evidence timestamps) |

---

## 9 · Empty / zero-state matrix

| Surface | Zero-data condition | Rendered state |
|---|---|---|
| Invoices list | tenant has 0 invoices | `Empty` "No invoices yet" |
| Invoice detail | n/a (only reached via a real id) | 404 → `ErrorState` "Invoice not found" |
| Corrections section | invoice has 0 corrections | inline "No corrections" text (section stays) |
| Evidence drawer | line has 0 evidence rows (should not occur if the line has any amount, but handled) | `Empty` "No usage rows found for this line" |
| Credits | 0 ledger entries (unused OR kill-switch off — indistinguishable) | `Empty` "No credit activity yet" + contact-operator copy |
| Plan & seats | `plan_id IS NULL` | "No plan assigned…" line, budget meter still renders from tenant-level data |
| Plan & seats | seat-billing unfrozen | inert "Seat pricing coming soon" placeholder |

---

## 10 · Responsive & a11y notes

- No new breakpoint: reuses the shell's existing `lg:` cutover (`lg:h-full`/`lg:overflow-y-auto`
  main region).
- Every table (`InvoiceLinesTable`, `CreditsHistoryTable`, the evidence drawer's table) scrolls
  horizontally inside its own bordered container at narrow widths — mirrors `LogsTable`/
  `AuditTable`'s existing convention; the page body itself never scrolls horizontally (M12).
- `EntitlementMeter` conveys state via TWO redundant cues (fill width + the numeric label), never
  color alone — mirrors `StatCard`'s own delta-arrow + sr-only-word discipline (WCAG 1.4.1).
  Over-budget tone flips the fill to `--destructive`, but the numeric "$X / $Y" text also makes the
  overage legible without color.
- Exactly one `h1` per page via `PageHeader` (frozen dashboard-wide contract); the evidence drawer's
  `DialogTitle` is its own accessible name, never a second page `h1`.
- Focus order: skip-link → nav → page `h1` → page content → (if opened) drawer content → Escape
  returns focus to the exact triggering control (M4).
