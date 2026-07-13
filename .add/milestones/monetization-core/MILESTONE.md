# MILESTONE: Monetization core — bill your tenants

goal: A gateway operator can bill their downstream tenants end-to-end — an immutable monthly invoice with row-level usage evidence, a prepaid-credits spend gate, an enforced plan (seats · budgets · allowlists · features), and a per-tenant margin view — with every dollar traceable from usage_record to invoice line.
rationale: new-major (roadmap M1 of 3, Tin-confirmed 2026-07-12 after the July-2026 Claude-cloud + gateway-market analysis) — "commercial platform that bills its own tenants" is a theme no active milestone covers, and downstream tenant billing/invoicing/margin is the uncontested market whitespace (none of the 12 scanned gateway vendors productize it). Strategy artifact: claude.ai/code/artifact/714afaab-749b-4518-9167-1e240924ad8a. Salvages plan-enforcement + cost-attribution-tags from the demoted cluster 4.
stage: production · status: active · created: 2026-07-12T07:38:27+00:00
release: 0.8.0

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  invoice generation from usage_records (immutable, evidence-linked, PDF/CSV + admin API) · prepaid credits/committed-spend ledger acting as a spend gate at the existing budget enforcement point · plan enforcement wiring the existing plans catalog (budget defaults, model allowlists, feature flags; seat CAPS live in the sibling `plan-seat-cap` task under platform-access-plan — driven to done in this wave, never duplicated here) · seat-based plan pricing with proration invoice lines · request metadata tags + cost-by-tag breakdowns · operator margin dashboard (provider cost vs billed vs invoiced) · tenant console Billing surfaces + platform-console Margin page.
Out: payment-processor integration (Stripe/etc. — invoices are documents + API, collection is external) · CCU-style consumption units (P2) · promotional/time-boxed rate windows (P2) · dunning/collections workflows · tax/VAT computation (invoice carries a configurable tax line only) · any change to the "exactly one usage record per request" invariant beyond the recorded v29/schema-validation exceptions.

UI/UX in scope — named precisely: a new **Billing** nav group in the tenant console (Invoices · Credits · Plan & seats) plus a platform-console **Margin** page, all in Aurora. Signature element: a **financial-document idiom** — statement-style invoice detail with `tabular-nums` currency columns, an issued-invoice surface that is *visibly* immutable (no edit affordances, an "issued" seal chip), and per-line **evidence drill-down** reusing the Logs-Explorer drawer pattern down to the underlying usage rows. Accessibility floor WCAG 2.2 AA; responsive to the existing dashboard breakpoints; journey: tenant admin reviews month → drills a disputed line to evidence → tops up credits → sees plan meters. UDD design-definition loop required for billing-ui before its build.

## Shared decisions & glossary deltas   (living — every task must honor these)
- **usage_records is the only ledger of usage truth** — invoices/credits/margin are *derived, append-only projections*; nothing ever mutates a usage record (same rule Logs Explorer honors).
- **One resolver** — all monetary derivation goes through the existing shared rate-card resolver (catalog display, recorder billing, cost recovery, and now invoicing can never drift).
- **Append-only money** — invoice rows and credit-ledger entries are immutable once written; corrections are new signed-delta entries (the v33 reconciliation precedent), never updates.
- **Fail-closed spend gate** — credits at zero blocks (with configurable grace), and the gate composes with existing budget enforcement at the SAME choke point; a gate-store outage degrades per the documented failure mode, never silently free.
- **Shared-seam discipline** — this milestone touches `usage_records`, the budget enforcement point, and the plans catalog: the most shared seams in the codebase. Parallel builders MUST re-check reused Pydantic/table shapes at BUILD time (the PR #66 `GuardrailConfigRequest` lesson), and the full BE suite is the pre-merge gate.
- Glossary deltas: **invoice** (immutable monthly statement derived from usage_records), **credit ledger** (append-only prepaid balance), **margin** (billed − provider cost, per tenant/model), **evidence link** (invoice line → usage-row lineage).

## Shared / risky contracts (freeze these first)
- invoice data model + `GET /admin/invoices` list/detail shape -> owning task invoice-generation
- credit-ledger table + spend-gate verdict port -> owning task credits-ledger
- plan-enforcement resolution order (plan defaults vs explicit tenant overrides) -> owning task plan-enforcement
- usage-record `tags` field (additive column — consumed by invoice line grouping + analytics) -> owning task cost-attribution-tags

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] cost-attribution-tags   depends-on: none                              — additive `tags` metadata on usage records + cost-by-tag breakdown API (foundation: invoice grouping consumes it)
- [ ] invoice-generation      depends-on: cost-attribution-tags             — immutable monthly invoices from usage_records via the shared rate-card resolver; line items per model/team/key/tag; PDF/CSV; `GET /admin/invoices`; per-line usage-row evidence links
- [ ] credits-ledger          depends-on: none                              — prepaid/committed-spend append-only ledger + fail-closed spend gate at the budget enforcement point [sensitivity: security — HARD-STOP verify expected]
- [ ] plan-enforcement        depends-on: none  (sibling: plan-seat-cap under platform-access-plan finishes in this wave) — plans catalog actually enforced: budget defaults, model allowlists, feature flags
- [ ] seat-billing            depends-on: plan-enforcement, invoice-generation — per-seat plan pricing, seat counting, proration lines on the invoice
- [ ] margin-dashboard        depends-on: invoice-generation                — operator margin view (provider cost vs billed vs invoiced, per tenant/model) productizing reconciliation, platform console
- [ ] billing-ui              depends-on: invoice-generation, credits-ledger, plan-enforcement — tenant Billing nav group (Invoices/Credits/Plan & seats) + evidence drill-down drawer, Aurora financial-document idiom [UDD design loop before build]

## Exit criteria (observable; map each to the task that delivers it)
- [x] A tenant admin can download last month's invoice (PDF/CSV) whose total equals the sum of its usage-derived lines, and every line drills down to the usage rows that produced it        (← invoice-generation, billing-ui — verifier: invoice adversarial suite (live JSONB tag-SET partition, never double-counts) + billing-ui verify fidelity walk; BFF binary passthrough confirmed zero-new-code)
- [x] A tenant with zero credit balance gets a structured 4xx spend-gate refusal on the next billable call, and succeeds again after a top-up — with the ledger showing both entries append-only        (← credits-ledger — verifier: independent adversarial verify + heal round; 402 ERR_CREDITS_EXHAUSTED scenario + append-only UPDATE/DELETE no-op probe green)
- [x] A tenant on a plan with a model allowlist/feature flag/budget default sees it actually enforced at request time (structured refusal, not catalog-only)        (← plan-enforcement — verifier: gate PASS on frozen suite incl. enforcement-at-request-time scenarios; fail-open flake ruled a shared-tree collision, clean re-run green)
- [x] An invoice for a plan-priced tenant carries correct seat lines including mid-month proration        (← seat-billing — verifier: 21-probe adversarial pass; proration exact at month boundaries incl. the corrected 16-day reactivation case; all 5 event-write sites atomic under injected failure)
- [x] Requests tagged via metadata produce cost-by-tag breakdowns that reconcile to the invoice totals        (← cost-attribution-tags — verifier: tags verify (RISK-ACCEPTED, signed) + invoice's canonical tag-SET partition probe: multi-tag rows partition, never double-count)
- [x] The platform operator sees per-tenant margin (billed − provider cost) that reconciles against the existing reconciliation view        (← margin-dashboard — verifier: 18-probe adversarial pass; tie-out route reconciles vs ledger exactly; honest-null never fabricates)
- [x] All Billing surfaces pass axe (WCAG 2.2 AA) and the issued invoice is visibly immutable in the UI        (← billing-ui — verifier: axe assertions in all three billing test files (12 total) + InvoiceStatusSeal immutability idiom confirmed in the DESIGN.md fidelity walk)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : untouched (engine used as-is; one CR — seat-billing re-frozen @ v2 via the §3
  Status→DRAFT + freeze flow)
- skill   : untouched
- book    : untouched (FEATURES.md gained the Monetization domain section; stale clusters
  "pending merge" text corrected — repo docs, not the ADD book)
- gateway : invoices (generator+PDF/CSV+evidence route) · credits (ledger, hold/settle/release
  at both choke points, global-idempotency index) · plans enforcement · seat cap at 4 seams ·
  seat_membership_events ledger + seat/proration lines · usage tags (JSONB) · margin endpoints ·
  GET /admin/plan; alembic chain fddae7074590→f70309062df0→d3f7a9c1b5e8→0b5527920450→
  1891020e487c→f1ef6b05a732 (single head)
- dashboard : Billing nav group (Invoices + evidence drawer, Credits, Plan & seats) · platform
  Margin page — Aurora financial-document idiom, axe-checked

### Cross-task evidence   (one row per task)
- cost-attribution-tags : gate=RISK-ACCEPTED (signed, expires 2026-08-15) · tests=green incl.
  retry-threading pins · residue=diverted-fallback closure bills tags={} (waivered)
- plan-enforcement : gate=PASS · tests=green (incl. migration suite) · residue=none
- invoice-generation : gate=PASS · tests=green + adversarial pins · residue=none
- credits-ledger : gate=PASS after 1 heal round (3 security findings fixed, repros red→green) ·
  tests=34 green, module cov 94.5% vs 90% bar · residue=none security; sweeper loop uncovered (💭)
- plan-seat-cap : gate=PASS (adversarial verify EARNED, 31 green incl. cross-seam races) ·
  residue=SCIM reactivation bypass — disclosed boundary, seeded delta (todo #9)
- margin-dashboard : gate=PASS (50 green incl. 18 adversarial) · residue=2 🟡 todos (#10 #11:
  summary heuristic; repo TZ assumption)
- billing-ui : gate=PASS (EARNED; probes adopted) · residue=F1 roster-count 403-degrade for 4/7
  roles (todo #7), F2 drawer subtitle (todo #8)
- seat-billing : gate=PASS @ contract v2/CR-1 (61 green: 38 builder + 21+2 adversarial) ·
  residue=4 seeded deltas (todos #15-#17 + prose fix #14)

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which — see per-criterion citations above)
- goal: a gateway operator can bill downstream tenants end-to-end — proven by the invoice
  determinism + evidence-drilldown adversarial probes (seat-billing/invoice verifies) on top of
  the credits spend-gate concurrency proofs: every dollar traceable usage_record → invoice line,
  seat lines included, margin honestly reported.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] full BE suite green on the integration branch (nohup + Monitor — background Bash dies at the 10-min cap) + dashboard suite + pyright/tsc 0
- [ ] open PR from feat/monetization-core; expect the org-billing 0-step CI block → admin-merge on local-suite evidence per standing practice
- [ ] update FEATURES.md (Billing domain section) + docs/pricing marketing page for the new packaging story
- [ ] cut feeds release 0.8.0 together with residency-service-tiers (human-run tag/publish/deploy per release.md)
