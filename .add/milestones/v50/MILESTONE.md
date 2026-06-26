# MILESTONE: Production hardening — landing & admin UI

goal: every landing and admin/dashboard page stays usable, secure, and accessible when the backend is slow or failing — a production-grade frontend
rationale: new-major — a frontend production-hardening pillar no active milestone's goal covers. EXTENDS the visual work of ui-fidelity (Aurora restyle) and the dashboard feature-coverage milestones (v13/v15/v23/v24) by making those same surfaces resilient/secure/accessible rather than merely styled; DEPENDS-ON the existing BFF surfaces (`app/api/*`) and gateway endpoints they call; DISCHARGES the carried browser-axe coverage + a11y-in-CI residue left open by `realbrowser-a11y-pass` (only 5/25 routes, not wired to CI). No goal overlap — resilience/security/perf are net-new for the frontend.
stage: production · status: active · created: 2026-06-26

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  Production-harden every landing/marketing (`/`, pricing, docs, blog, status, legal/*), admin (`(app)/app/*` — 13 routes), and auth (login, signup, oidc-callback) surface across five dimensions — (1) resilience/failure-UX (timeout+bounded-retry+circuit-breaker on every BFF→gateway call per the IO rule; loading/error/empty/not-found states on every data fetch; no infinite spinners or unhandled rejections), (2) security (CSP+HSTS+anti-clickjacking headers; zod input validation fail-closed at every BFF boundary; no secret leakage; SSRF-safe gw proxy), (3) accessibility (real-browser axe pass — color-contrast + keyboard + focus — across ALL routes, wired into CI), (4) performance/SEO (per-page metadata/OG, ISR/caching on static marketing pages, bundle/code-split discipline), (5) UX polish + progressive animation (motion token system that respects `prefers-reduced-motion`; entrance/transition affordances).
Out: net-new product features or pages (this milestone hardens what exists, adds none); backend/gateway behavior changes (the gateway is a fixed dependency — we harden the BFF and UI only); auth/RBAC model changes (already shipped v17/v18/v38); the marketing CMS/content itself (copy stays as-is); native mobile; i18n/localization; visual redesign beyond motion polish (ui-fidelity owns the look).

## Shared decisions & glossary deltas   (living — every task must honor these)
- (ADD/IO-rule) Every outbound BFF→gateway call MUST design for failure: explicit timeout, bounded retry (idempotent GET only — never retry POST/PUT/PATCH/DELETE), and a circuit-breaker on repeated upstream failure. A failure renders a typed error state, never a hang or a stack trace. This is the core invariant — `core/` IO rule raised to the BFF tier.
- (DDD) "failure-state segment" — a Next.js route-level `error.tsx`/`loading.tsx`/`not-found.tsx` file (distinct from the in-component `states.tsx` primitives, which render *inside* a loaded page). Segment files catch render/fetch throws and Suspense; component states render known empty/error data. Both are required; neither replaces the other.
- (DDD) "progressive motion" — animation is an enhancement layered on a fully-functional static baseline; under `prefers-reduced-motion: reduce` every page MUST remain complete and usable with motion suppressed. Motion never gates content.
- (security) CSP is the milestone's riskiest cross-cutting contract: it can silently break inline scripts/styles, the gw proxy, and 3rd-party embeds. Freeze the policy shape FIRST and treat any required relaxation as an auditable decision recorded at the contract freeze.
- (SDD) One consolidated BFF client only — the duplicate `lib/bff-client.ts` + `lib/api-client.ts` collapse into a single hardened client; the `ProblemDetail`/`BffError` envelope is preserved and extended, never forked.

## Shared / risky contracts (freeze these first)
- Hardened BFF client public API + error envelope (timeout/retry/CB semantics) -> owning task `resilient-bff-fetch`  (every apply task + the gw proxy consume it)
- Content-Security-Policy + security-headers policy shape -> owning task `security-headers-csp`  (constrains what every page may load/inline)
- Motion token API (durations, easings, reduced-motion contract) -> owning task `motion-primitives`  (every apply task consumes)
- Failure-state segment pattern (shared ErrorBoundary + global error/not-found + per-group loading) -> owning task `failure-state-segments`  (every apply task follows the pattern)

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
### Foundations — shared primitives (freeze, then apply)
- [ ] resilient-bff-fetch     depends-on: none                 — collapse the two fetch wrappers into one BFF client with timeout + bounded-retry(idempotent-only) + circuit-breaker + preserved ProblemDetail envelope; add AbortController/timeout + error mapping to the `gw/[...path]` proxy.
- [ ] security-headers-csp    depends-on: none                 — `next.config` `headers()` + root `middleware.ts`: CSP (nonce strategy), HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy across all routes.
- [ ] bff-input-validation    depends-on: resilient-bff-fetch  — zod schemas fail-closed (422) at every BFF route boundary (login/signup/oidc/gw guards); reject malformed bodies before any upstream call.
- [ ] failure-state-segments  depends-on: none                 — shared ErrorBoundary + global `error.tsx`/`not-found.tsx` + per-group `loading.tsx`, built on the existing `states.tsx` primitives; the segment pattern apply-tasks reuse.
- [ ] motion-primitives       depends-on: none                 — motion token system (durations/easings) + reduced-motion-respecting entrance/transition primitives, layered over a static baseline.
- [ ] a11y-ci-coverage        depends-on: none                 — expand the playwright/axe spec from 5 → all ~25 routes (marketing+admin+auth) and wire `test:a11y` into CI as a gating check.

### Apply — per surface-group (consume the frozen primitives)
- [ ] harden-marketing        depends-on: resilient-bff-fetch, security-headers-csp, failure-state-segments, motion-primitives  — apply failure states + per-page SEO metadata/OG + ISR/caching + motion polish + a11y fixes to `/`, pricing, docs, blog, status, legal/*.
- [ ] harden-admin            depends-on: resilient-bff-fetch, security-headers-csp, failure-state-segments, motion-primitives  — apply loading/error/empty + retry-aware data fetching + a11y fixes + motion polish to the 13 `(app)/app/*` admin routes.
- [ ] harden-auth             depends-on: resilient-bff-fetch, bff-input-validation, failure-state-segments, motion-primitives   — apply resilient submit + validation feedback + failure states + a11y + motion to login, signup, oidc-callback.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] Every BFF→gateway call has an enforced timeout, retries only idempotent GETs within a bounded cap, and trips a circuit-breaker on repeated failure — verifiable by tests simulating slow/failing upstream  (← resilient-bff-fetch)
- [ ] A malformed request body to any BFF route returns 422 before any upstream call; no unvalidated body reaches the gateway  (← bff-input-validation)
- [ ] Every response from the dashboard carries CSP, HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy and Permissions-Policy headers; clickjacking + MIME-sniff are blocked  (← security-headers-csp)
- [ ] Every route group renders a typed loading state during fetch, a recoverable error state on throw, and a 404 page for unknown paths — no infinite spinner, no raw stack trace reaches the user  (← failure-state-segments, harden-marketing, harden-admin, harden-auth)
- [ ] A real-browser axe pass (color-contrast enabled) across ALL ~25 routes reports zero serious/critical violations, and the pass runs as a gating CI check  (← a11y-ci-coverage)
- [ ] Every marketing page emits unique title/description/OG metadata and static pages are statically rendered/ISR-cached (verifiable in build output)  (← harden-marketing)
- [ ] Under `prefers-reduced-motion: reduce` every page is complete and usable with animation suppressed; otherwise entrance/transition motion is present  (← motion-primitives, harden-marketing, harden-admin, harden-auth)
- [ ] Auth forms surface field-level validation errors and a resilient submit (timeout/error feedback, no silent failure) on login, signup, and oidc-callback  (← harden-auth)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : <add.py / state.json / templates — what shipped, or "untouched">
- skill   : <SKILL.md / phases/* / guides — what shipped, or "untouched">
- book    : <docs/* — what shipped, or "untouched">

### Cross-task evidence   (one row per task)
- <slug> : gate=<PASS|RISK-ACCEPTED> · tests=<n green> · residue=<none|note>

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [ ] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: <restate the milestone goal — and the one evidence line that proves the ship meets it>

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] open a PR from `feat/v50-ui-hardening` → main using the Close ship-review above; the human reviews + merges
- [ ] confirm dashboard CI (vitest + the newly-gating a11y playwright job) is green on the PR
- [ ] bundle into the next release cut (release.md) — attribute v50 in CHANGELOG/RELEASES ledger
- [ ] tag / publish / deploy  (human-run, per release.md)
