# MILESTONE: Enterprise readiness

goal: A prospective customer can discover and evaluate the product on a public marketing site, and an enterprise operator can run it with the audit trail, role-based access, compliance/SLA surfaces, and observability an enterprise deployment requires.
rationale: new-major (intake-confirmed 2026-06-24; Tin chose full marketing site + all four
  hardening dimensions + ONE combined milestone). Relationship to map: EXTENDS the whole v1–v37 arc
  (which built the metered multi-tenant proxy + admin dashboard) by adding the two things it has
  never had — a PUBLIC front-of-house (no marketing surface exists; `/` currently redirects straight
  to `/login`, see apps/dashboard/app/page.tsx) and ENTERPRISE governance/trust/observability (audit,
  RBAC tiers, compliance/SLA, SLO are NET-NEW mechanisms, unlike v37's render-the-existing-surface work).
  ⚠ LOWEST-CONFIDENCE: the goal carries an "and" (front-of-house AND governance) — by ADD's one-outcome
  rule that reads as two milestones; Tin chose to combine under the single theme "enterprise readiness".
  If wrong: v38 runs long / releases late — mitigated by closing+folding in WAVES (marketing → audit →
  RBAC → compliance → SLO) and cutting releases incrementally.

stage: production · status: active · created: 2026-06-24

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - Public marketing site (Next.js, in-app, no CMS): landing, pricing, legal (terms/privacy),
    docs/blog scaffold, public trust/status page.
  - Audit logging: append-only `audit_events` + write seam at admin/security actions + query API
    + dashboard viewer.
  - RBAC tiers: roles beyond owner/admin (viewer · billing-admin · operator) with per-surface
    authorization + assignment UI.
  - Compliance & SLA: public trust/status page (component status + SLA statement) + tenant
    data-retention controls (enforced sweep).
  - Observability/SLO: per-tenant SLO/error-budget metrics exposed + dashboard SLO view.
Out:
  - CMS / external marketing platform — content stays in-repo; no headless CMS.
  - SOC2 CERTIFICATION itself — we build evidence hooks/surfaces, not the audit engagement.
  - Billing/payment checkout from pricing — pricing is presentational; the signup funnel is unchanged.
  - Full distributed-tracing/APM backend — SLO = metrics + error budgets, not an APM rollout.
  - Any new LLM provider or data-plane routing change.

## Shared decisions & glossary deltas   (living — every task must honor these)
- PUBLIC/GATED ROUTE SPLIT (NEW glossary: *public route* vs *gated app*): `/` becomes the public
  landing; the authenticated app relocates under a gated segment. Auth stays enforced on the gateway —
  this is a UX/routing boundary ONLY, no auth weakening (proxy.ts cookie gate + gateway JWT unchanged).
  Riskiest contract — freeze first.
- AUDIT EVENT (NEW glossary): append-only `audit_events(tenant_id, actor, action, target, metadata, ts)`
  — same append-only invariant as the usage ledger; every tenant-owned row carries `tenant_id`.
- ROLE (EXTEND glossary): owner/admin preserved byte-for-byte; new tiers are ADDITIVE; authorization
  is a per-surface matrix, enforced on the gateway (FE never re-implements authz).
- SLO / ERROR BUDGET (NEW glossary): per-tenant latency + success-rate targets; metrics contract
  frozen by its owning task.
- All FE honors WCAG-AA + design tokens (v23/v24 bar); all BE honors timeout + bounded retry +
  circuit-breaker and the tenant-scoping / append-only-ledger invariants.

## Shared / risky contracts (freeze these first)
- Public/gated route split                         -> owning task `marketing-shell`
- `audit_events` shape + write-seam signature      -> owning task `audit-log-store`
- Role tiers + per-surface authorization matrix    -> owning task `rbac-roles`
- SLO metrics format (Prometheus `/metrics` vs admin JSON) -> owning task `slo-metrics`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] marketing-shell          depends-on: none             — Public marketing layout (nav/footer/theme) + root-route split so anon `/` serves public content; app relocated to a gated segment. FREEZES the public/gated route split.
- [ ] landing-page             depends-on: marketing-shell  — Hero, value-prop, feature grid, social proof, CTA → signup/login.
- [ ] pricing-page             depends-on: marketing-shell  — Presentational pricing tiers (no checkout).
- [ ] legal-pages              depends-on: marketing-shell  — Terms + Privacy pages.
- [ ] docs-blog-scaffold       depends-on: marketing-shell  — Docs/blog index structure (scaffold, not full content).
- [ ] audit-log-store          depends-on: none             — Append-only `audit_events` migration + write seam at key/routing/login/role actions. FREEZES the event shape.
- [ ] audit-log-surface        depends-on: audit-log-store  — `GET /admin/audit` (tenant-scoped, paginated, filterable) + dashboard Audit page.
- [ ] rbac-roles               depends-on: none             — Role tiers (viewer/billing-admin/operator) + per-surface authz matrix; owner/admin preserved. FREEZES authz.
- [ ] rbac-admin-ui            depends-on: rbac-roles        — View/assign member roles on the Teams page.
- [ ] trust-status-page        depends-on: marketing-shell  — Public status/trust page: component status + SLA statement, wired to real health signals.
- [ ] data-retention-controls  depends-on: audit-log-store  — Tenant data-retention window setting + enforcing sweep (ledger + audit).
- [ ] slo-metrics              depends-on: none             — Per-tenant SLO/error-budget metrics exposed. FREEZES the metrics contract.
- [ ] slo-dashboard            depends-on: slo-metrics       — Dashboard SLO view (latency / error budget per tenant).

## Exit criteria (observable; map each to the task that delivers it)
- [x] An anonymous visitor at `/` sees a landing page (no login redirect) and can navigate to pricing, legal, and docs   (← marketing-shell, landing-page, pricing-page, legal-pages, docs-blog-scaffold)
- [x] Admin/security actions write append-only audit events; an admin can query and filter them in the dashboard   (← audit-log-store, audit-log-surface)
- [x] Roles beyond owner/admin exist, are enforced per surface, and are assignable from the dashboard   (← rbac-roles, rbac-admin-ui)
- [x] A public trust/status page shows component status + SLA, and a tenant can set a data-retention window that is enforced   (← trust-status-page, data-retention-controls)
- [x] Per-tenant SLO/error-budget metrics are exposed and rendered in the dashboard   (← slo-metrics, slo-dashboard)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- dashboard : Public marketing site (route-split `/`→landing, app→`/app`; hero/features/pricing/legal/docs/blog/status) + read viewers (audit `/app/audit`, members/role-assign `/app/members`, SLO `/app/slo`). dashboard vitest 501 green, tsc 0, next build exit 0.
- gateway   : RBAC allowlist Permission matrix (6 role tiers) + append-only `audit_events` store (trigger-immutable, fail-open, 8 emit sites) + `GET /admin/audit` + `GET /admin/users`+`PUT /admin/users/{id}/role` (escalation guard) + RetentionSweeper (active-by-default purge, audit-floor) + `GET /admin/slo`. Migrations b2d4f6a8c0e1·e3f5a7c9b1d2·f2a4c6e8b0d3. Gateway suite 1646 green.
- tooling / skill / book : untouched.

### Cross-task evidence   (one row per task)
- marketing-shell · landing-page · pricing-page · legal-pages · docs-blog-scaffold · trust-status-page : gate=PASS · dashboard vitest grew to 480 green · residue=none (SPEC deltas: real pricing/legal/docs content, public health-summary endpoint)
- rbac-roles : gate=PASS · gateway 1584 green · residue=none (Tin-approved matrix; back-compat byte-identical)
- audit-log-store : gate=PASS · gateway 1599 green · residue=none (Tin-approved A=DB-enforced/B=fail-open/C=all-6)
- data-retention-controls : gate=PASS · gateway 1607 green · residue=⚠ active-by-default destructive purge (release note) + RULE→trigger change-request to audit-log-store (Tin-approved)
- audit-log-surface : gate=PASS · gateway 1622 + dashboard 486 green · residue=none (repo-seam refactor)
- rbac-admin-ui : gate=PASS · gateway 1631 + dashboard 499 green · residue=422-vs-400 on invalid role (codebase convention)
- slo-metrics : gate=PASS · gateway 1646 green · residue=latency_ms null (no stored latency — spec delta)
- slo-dashboard : gate=PASS · dashboard 501 green · residue=none (honest latency placeholder)

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which): EC1←marketing wave (dashboard 501) · EC2←audit-log-store+surface · EC3←rbac-roles+admin-ui · EC4←trust-status-page+data-retention-controls · EC5←slo-metrics+dashboard
- goal: "A prospective customer can discover and evaluate the product on a public marketing site, and an enterprise operator can run it with the audit trail, role-based access, compliance/SLA surfaces, and observability an enterprise deployment requires." PROVEN: public site live at `/` + the four governance surfaces (audit/RBAC/retention+SLA/SLO) all shipped, gate-passed, and independently verified. All 3 BE security contracts Tin-approved.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] commit each wave + fold on a `feat/v38-*` branch (FE marketing + BE governance + .add bookkeeping)
- [ ] open PR(s) to main; Tin reviews + merges (HTTPS push per [[git-push-https-gotcha]])
- [ ] v38 joins the releasable set; bundle into the next release cut when Tin calls it (release.md)
