# MILESTONE: Hardening — clear carried follow-up debt (v13/v14/v15)

goal: every carried follow-up from v13/v14/v15 is cleared with zero behavioral regression on the existing floor: tests-bff is tsc-clean with scoped msw handlers, the two react-hooks lint rules are restored to error with their violations fixed, role-based nav visibility + the pre-auth OIDC callback relay ship, the dev-toolchain advisories are cleared, and the real-browser a11y+viewport pass runs
rationale: Intake → `sub-milestone` (Tin-confirmed 2026-06-14). v13/v14/v15 each landed clean per-surface but DEFERRED named follow-ups (folded as open deltas, never lost). v14 (Next 16) surfaced two more (the react-hooks error→warn downgrade, the tests-bff tsc drift) and confirmed the recurring "shared real-browser a11y pass" residue. This milestone is a DEPTH/quality slice — no new product surface; it pays down the foundation-tracked debt so the dashboard is genuinely enterprise-grade (strict lint, strict test harness, clean dev supply-chain, role-correct nav, complete SSO loop, real-browser-verified a11y). All work is behavior-preserving against the existing floor unless a task's contract explicitly adds a small UX behavior (nav-role-filter, oidc-callback-relay).

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  The six carried follow-ups, each a tracked delta from a prior milestone's fold:
     - **bff-test-harness-strict-handlers** (from v14+v15): make `apps/dashboard/tests-bff/` tsc-clean
       (fix the Next 16 async-params `Promise<{path}>` route-handler test fixtures + the pre-existing
       msw `JsonBodyType`/`null→Request` cast looseness) AND scope the shared msw fallbacks so a
       `/api/gw/:path*` wildcard no longer defeats `onUnhandledRequest:"error"`. Enables a future
       tests-tree type gate. Behavior-preserving (the 236 tests keep their assertions).
     - **react-hooks-strict-lint** (from v14): restore `react-hooks/refs` + `react-hooks/set-state-in-effect`
       to `error` and FIX the ~60 flagged patterns (SpendPage last-good-ref read-in-render, OidcSettings
       sync-server-state-in-effect, use-focus-trap ref) via a behavior-preserving state-model refactor;
       the eslint baseline returns to 0 warnings / 0 errors.
     - **nav-role-filter** (from v15): role-based NAV visibility — a `member` no longer sees admin-only
       (`/models`) or owner-only (SSO settings) links they would 403 on (a UX gap, not a security hole;
       the gateway still enforces RBAC). The nav filters on the current user's role from `useCurrentUser`.
     - **oidc-callback-relay** (from v15): complete the SSO loop — the pre-auth OIDC CALLBACK relay
       route (the v15 `oidc-login-relay` shipped the login redirect; the callback exchange → session
       cookie is the missing half). client_secret stays WRITE-ONLY; no token in any response body.
     - **devtool-vitest4-upgrade** (from v14): clear the 7 dev-toolchain advisories — vitest 3→4 +
       @vitejs/plugin-react 4→6 majors + a vitest-axe 0.1.0 replacement; the full `npm audit` reaches
       0 critical/high; the 236-test floor stays green on vitest 4.
     - **realbrowser-a11y-pass** (shared v13/v15 residue): a real-browser (Playwright + viewport) axe
       pass that proves color-contrast + true-layout a11y that jsdom-axe CANNOT — the standing residue,
       finally discharged with a minimal headless-Chromium harness.
Out: any NEW product feature or surface beyond the two small declared UX behaviors (nav-role-filter,
     oidc-callback-relay); any GATEWAY/BFF data-contract change (oidc-callback-relay adds a BFF ROUTE,
     not a gateway contract change); a full E2E/Playwright SUITE beyond the single a11y pass (the a11y
     pass is the in-scope real-browser smoke, not a broad browser-test migration); LiteLLM-parity
     feature work (Bedrock/Azure, more routing/governance) — that is the SEPARATE next-feature track.

## Shared decisions & glossary deltas   (living — every task must honor these)
- Behavior-preserving is the default: every task holds the existing behavioral floor green; only
  nav-role-filter + oidc-callback-relay add a SMALL, contracted UX behavior (each asserts both the
  new behavior AND that the prior floor is untouched). A real behavior regression is a HARD-STOP.
- Strict-gate ratchet: bff-test-harness-strict-handlers + react-hooks-strict-lint TIGHTEN gates
  (test-tree tsc, lint error-class) — once tightened they must STAY tightened (a later task may not
  silently re-loosen them); the convention folded in foundation v16 governs how (error→warn is only
  for a DECLARED+TICKETED transition, never a permanent escape hatch).
- Security (carried, non-negotiable): oidc-callback-relay keeps client_secret WRITE-ONLY (GET returns
  the `<stored>` sentinel; never prefilled/logged/echoed); no JWT/bearer in any response body; tenant
  isolation never leaks; the callback exchanges the code server-side (BFF), the browser only gets the
  httpOnly session cookie. A security finding here is a HARD-STOP.
- devtool-vitest4-upgrade is risk:high (a test-runner major bump changes the gate's own engine) →
  autonomy:conservative, human-gated verify (mirrors the v14 next16-upgrade posture); it runs AFTER
  bff-test-harness-strict-handlers so the harness it migrates is already strict + tsc-clean.

## Shared / risky contracts (freeze these first)
- The **strict test-harness invariant** (tests-bff tsc-clean + scoped msw handlers) → owning task
  `bff-test-harness-strict-handlers` (everything downstream runs on it).
- The **OIDC callback BFF contract** (callback route: code→token exchange server-side → httpOnly
  session cookie; error paths; secret write-only) → owning task `oidc-callback-relay`.
- The **vitest-4 toolchain invariant** (vitest 4 + plugin-react 6 + axe replacement; audit 0/0) →
  owning task `devtool-vitest4-upgrade`.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] bff-test-harness-strict-handlers   depends-on: none                              — make tests-bff/ tsc-clean (Next 16 async-params fixtures + msw cast looseness) + scope shared msw fallbacks so the gw wildcard no longer defeats onUnhandledRequest:error; behavior-preserving (236 tests keep assertions). — DONE, gate PASS 2026-06-14 (tsc 18→0, wildcards removed, 0 leaks, 238 green @ 94.03%).
- [x] react-hooks-strict-lint            depends-on: bff-test-harness-strict-handlers  — restore react-hooks/refs + set-state-in-effect to error; fix ~60 flagged patterns (SpendPage ref-in-render, OidcSettings setState-in-effect, use-focus-trap) via behavior-preserving state refactor; eslint back to 0/0. — DONE, gate PASS 2026-06-14 (57 refs + 3 set-state-in-effect → 0; eslint 0/0; 240 tests green @ 94.03%; adversarial-EARNED; ratchet guard tests-bff/lint-rules-strict.test.ts).
- [x] nav-role-filter                    depends-on: none                              — role-based nav visibility from useCurrentUser (member hides admin/owner-only links); gateway RBAC unchanged; assert new behavior + floor intact. — DONE, gate PASS 2026-06-14 (member hides {models,teams,routing}; fail-open; AppShell role prop + DashboardShell wrapper; 245 tests green @ 94.05%; adversarial EARNED-WITH-GAPS→gaps closed).
- [x] oidc-callback-relay                depends-on: none                              — pre-auth OIDC CALLBACK BFF relay (code→token exchange server-side → httpOnly session cookie); client_secret write-only; no token in response body; error paths covered. — DONE, gate PASS 2026-06-14 (GET /auth/oidc/callback forwards code+state+3 oidc_* cookies; gateway 3xx→Location+Set-Cookie verbatim/empty body; 4xx/5xx/unreachable→302 /login?sso_error=<sanitized hint>; login page generic ErrorState alert; redirect:manual+5s timeout; 253 tests green @ 94.05%; adversarial AppSec refute-read EARNED-WITH-GAPS, no security finding, 6/6 inviolables upheld).
- [ ] devtool-vitest4-upgrade            depends-on: bff-test-harness-strict-handlers  — vitest 3→4 + @vitejs/plugin-react 4→6 + vitest-axe replacement; full npm audit 0 critical/high; 236-test floor green on vitest 4. risk:high → conservative/human-gated.
- [ ] realbrowser-a11y-pass              depends-on: none                              — minimal Playwright + viewport axe pass proving color-contrast + true-layout a11y jsdom can't; discharges the standing v13/v15 residue.

## Exit criteria (observable; map each to the task that delivers it)
- [x] `apps/dashboard` `tests-bff/` is tsc-clean (a bare `tsc --noEmit` over the tree reports 0 errors) and shared msw fallbacks are path-scoped (no `/api/gw/:path*` wildcard defeating `onUnhandledRequest:"error"`) (← bff-test-harness-strict-handlers) (verify: `tsc --noEmit` over tests-bff 0 errors + msw handler review) — MET: tsc 18→0; the 4 `:path*` wildcards removed (strict-harness.test.ts guard green); onUnhandledRequest:"error" preserved; 0 unhandled-request leaks; 238 tests green @ 94.03%.
- [x] `eslint .` is back to 0 errors AND 0 warnings with `react-hooks/refs` + `react-hooks/set-state-in-effect` restored to `error`; the suite stays green (← react-hooks-strict-lint) (verify: `eslint .` EXIT 0 + 0 warnings; `vitest run --coverage` EXIT 0) — MET: both rules pinned at "error"; `eslint .` EXIT 0 (0/0, was 60 warnings); 240 tests green @ 94.03%; ratchet guarded by tests-bff/lint-rules-strict.test.ts.
- [x] a `member` user does not see admin-only/owner-only nav links (they are absent from the DOM, not merely disabled), while an admin/owner does; gateway RBAC behavior is unchanged (← nav-role-filter) (verify: role-scoped render tests — member hides, admin shows) — MET: member nav = {usage,spend,keys,settings} (4); admin/owner = all 7; links ABSENT from DOM (queryByRole null + toHaveLength(4)); fail-open on unknown role; gateway RBAC untouched (UX-only). Note: no owner-ONLY top-level nav link exists — SSO is a server-authoritative tab in /settings, left as-is.
- [x] the SSO loop completes: the OIDC callback route exchanges the code server-side and sets the httpOnly session cookie; no token appears in any response body; client_secret stays write-only (← oidc-callback-relay) (verify: callback route tests — success sets cookie, error paths, secret never echoed) — MET: GET /auth/oidc/callback forwards the gateway 3xx Location + every Set-Cookie (ai_proxy_session httpOnly + cleared oidc_*) VERBATIM with an empty body; only the 3 oidc_* cookies go upstream; 4xx→/login?sso_error=<gateway code>, 5xx/unreachable→/login?sso_error=upstream (no upstream body); login page shows a GENERIC ErrorState alert (raw hint never rendered); token exchange + client_secret stay gateway-side (relay never touches either); adversarial AppSec refute-read EARNED-WITH-GAPS (no HARD-STOP); 8 route tests + 253-test floor green @ 94.05%.
- [ ] the FULL `npm audit` reports 0 critical and 0 high (dev-toolchain advisories cleared); the 236-test floor stays green on vitest 4 (← devtool-vitest4-upgrade) (verify: `npm audit --json` critical==0 && high==0 + `vitest run --coverage` EXIT 0)
- [ ] a real-browser (Playwright + viewport) axe pass runs green over the primary surfaces, proving color-contrast + true-layout a11y (← realbrowser-a11y-pass) (verify: the headless-Chromium axe pass EXIT 0)
