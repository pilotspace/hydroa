# MILESTONE: Dashboard observability parity

goal: The dashboard renders the operator read-side surfaces recent backend milestones shipped without UI — per-key bandwidth levels, routing-config save feedback + validation, and SSO repeat-login polish — so operators see and act on existing endpoints without curl.
rationale: sub-milestone (intake-confirmed 2026-06-24, Tin chose "Dashboard observability parity" over
  the single-panel option). NOT a net-new mechanism — it extends the EXISTING dashboard to render
  operator read-side endpoints that recent BACKEND milestones shipped without a UI (the recurring
  v31/v32/v36 BE-then-FE split). Each task renders/uses an endpoint that ALREADY EXISTS (mostly
  FE-only; at most one additive `updated_at` field on an existing GET). Grounded in the logged
  deferred SPEC deltas — not invented work.

stage: production · status: active · created: 2026-06-24

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - Per-key BANDWIDTH panel on /keys rendering GET /admin/bandwidth (v36 readout) — mirrors the
    existing RatelimitsPanel. [delta: bandwidth-counter-view → "dashboard /bandwidth UI"]
  - Routing-editor SAVE FEEDBACK + VALIDATION on /routing (RoutingEditor.tsx): a "restart required to
    apply" affordance after a successful PUT /admin/routing (v32 is restart-to-apply); client-side
    weight>0 guard (no 422 round-trip); surface the global retry/cooldown/loadbal scalar knobs the PUT
    already accepts. [deltas: routing-config-editor ×2 + routing-config-write]
  - SSO repeat-login POLISH on /login (LoginForm.tsx): persist the last-used SSO domain (localStorage)
    + a clear message when the backend 404s an unconfigured domain. [deltas: sso-login-button ×2]
Out:
  - Operator-wide /ops/reconciliation dashboard UI — needs ops-auth/mTLS in a browser context, a
    SEPARATE security-gated design (its own milestone). [delta: operator-wide-reconciliation]
  - Catalog last-sync-time PERSISTENCE + alerts payload-summary column — different surfaces
    (catalog/alerts) and the catalog one needs real BE (a `catalog_sync_meta` row); not this cluster.
  - Any NEW backend endpoint or mechanism (this milestone is render-the-existing-surface only).

## Shared decisions & glossary deltas   (living — every task must honor these)
- READ/RENDER-ONLY parity: every task renders an EXISTING gateway endpoint via the `/api/gw/[...path]`
  BFF proxy + `bffGet` (lib/bff-client) — no new gateway route, no new auth surface. The single
  allowed BE touch is an ADDITIVE `updated_at` field on GET /admin/routing (no contract break).
- Honest read-only states: mirror the established four-state render (loading / error / empty / success)
  and the null→"—" (unknown, never 0) convention from RatelimitsPanel.
- Owner/admin enforcement stays on the gateway (the endpoints are already `require_owner_or_admin`);
  panels render inside admin-reachable pages, the FE never re-implements authz.
- A11y + token parity: WCAG-AA, design tokens, headingLevel discipline — same bar as v23/v24 UI work.

## Shared / risky contracts (freeze these first)
- (none net-new) — all three tasks consume FROZEN endpoints. The only contract touch is the additive
  GET /admin/routing `updated_at` field -> owning task `routing-editor-feedback` (freeze that GET shape).

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] bandwidth-panel          depends-on: none  — BandwidthPanel.tsx on /keys rendering GET /admin/bandwidth (level vs capacity, null→"—"), mounted beside RatelimitsPanel; mirrors the ratelimits viewer + test. GATE PASS.
- [x] routing-editor-feedback  depends-on: none  — pending-restart "Saved — restart to apply" affordance after a successful routing save + client-side weight>0/blank-model validation (no 422 round-trip). Editable retry/cooldown/loadbal knobs + `updated_at` DESCOPED → SPEC deltas (Tin-approved narrow freeze; loadbal needs a BE GET field). GATE PASS.
- [x] sso-login-polish         depends-on: none  — persist last-used SSO domain (localStorage) + pre-flight unconfigured-domain (404) inline message on /login, degrade-to-nav on probe error. GATE PASS.

## Exit criteria (observable; map each to the task that delivers it)
- [x] /keys shows a per-key bandwidth panel: each key's current level vs capacity from GET /admin/bandwidth, null→"—", with loading/error/empty/success states   (← bandwidth-panel)
- [x] After saving routing config, the editor shows a clear "restart required to apply" affordance (restart-to-apply is visible, not silent)   (← routing-editor-feedback)
- [x] The routing editor rejects weight≤0 (and blank model) client-side (no 422 round-trip)   (← routing-editor-feedback)
      [NARROWED 2026-06-24: the "expose global retry/cooldown/loadbal knobs" clause was DESCOPED at the
       task freeze (Tin-approved) — loadbal is not in the GET response (needs BE) and editable knobs are
       an additive feature, not polish → moved to SPEC deltas. The client-guard half ships here.]
- [x] The /login SSO flow pre-fills the last-used domain and shows a clear message on an unconfigured-domain 404   (← sso-login-polish)
- [x] All changes are FE-only against existing endpoints (no `updated_at` field was needed); dashboard vitest + a11y + build all green   (← all)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- dashboard : NEW BandwidthPanel.tsx (+ mounted in KeysPage) rendering GET /admin/bandwidth; RoutingEditor.tsx
  gained a client weight/blank-model guard + "Saved — restart to apply" affordance; LoginForm.tsx gained a
  localStorage domain seed + a pre-flight 4xx "domain not configured" inline message (degrade-to-nav on error).
- gateway : untouched (all three consume FROZEN endpoints; no `updated_at` field was needed after all).
- tooling / skill / book : untouched.

### Cross-task evidence   (one row per task)
- bandwidth-panel         : gate=PASS · tests=7 green (incl. zero-level≠unknown) · residue=none
- routing-editor-feedback : gate=PASS · tests=12 green (6 v32 + 6 new incl. recovery) · residue=none (editable knobs → SPEC deltas)
- sso-login-polish        : gate=PASS · tests=9 green (5 v31 async-aware + 4 polish) · residue=none (callback sso_error mapping → SPEC delta)
- whole suite : dashboard vitest 401 green (52 files, incl. |bff| project) · tsc clean · eslint clean · real `next build` exit 0.

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row (bandwidth-panel → crit 1; routing-editor-feedback → crit 2+3 (3 narrowed); sso-login-polish → crit 4; whole-suite green → crit 5)
- goal: operators now SEE and ACT on the read-side surfaces recent BE milestones shipped without UI — per-key bandwidth levels (v36), routing save feedback + weight validation (v32), and SSO repeat-login polish (v31) — all FE-only against existing endpoints, full dashboard suite 401 green.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] commit the 3 tasks + fold on a `feat/v37-*` branch (FE-only dashboard diff + .add bookkeeping)
- [ ] open a PR from the Close ship-review above to main; Tin reviews + merges (HTTPS push per [[git-push-https-gotcha]])
- [ ] v37 joins the releasable set (v33+v34+v35+v36+v37 closed since 0.2.0) — bundle into the next release cut when Tin calls it (release.md); no separate deploy step (dashboard ships with the app)
