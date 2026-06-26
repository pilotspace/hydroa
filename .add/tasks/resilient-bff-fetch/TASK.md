# TASK: Hardened BFF client: timeout + bounded retry + circuit-breaker

slug: resilient-bff-fetch · created: 2026-06-26 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/dashboard/lib/bff-client.ts` : `bffGet/bffPost/bffPut/bffPatch/bffDelete/bffAuthPost` + `handleBffResponse` + `BffError`/`ProblemDetail` — client-side adapter; plain `fetch` w/ `credentials:"include"`, NO timeout/retry/circuit-breaker. Imported by ~20 component files; 401(non-auth)→`window.location.href="/login"`.
- `apps/dashboard/lib/api-client.ts` : `apiGet/apiPost/apiPut/apiDelete` + `handleResponse` + `ApiError`/`ProblemDetail` + `isAuthPath` — near-identical DUPLICATE; difference: `isAuthPath` routes `/api/auth/*` direct (no `/api/gw` prefix, suppresses 401-redirect). Imported by ~15 component/page files (incl. `LoginForm`/`SignupForm`/`MembersPage`).
- `apps/dashboard/app/api/gw/[...path]/route.ts` : `proxyRequest(req, ctx)` + `GET/POST/PUT/PATCH/DELETE` — server-side authed proxy; bare `await fetch(upstreamUrl, …)` with NO `AbortController`/timeout/retry; reads `ai_proxy_session` cookie→`Authorization: Bearer`; upstream 401→clear-cookie+`ERR_AUTH_SESSION_EXPIRED`; 204 passthrough; else verbatim `NextResponse.json(body, status)`. An upstream network failure/timeout currently throws → unhandled 500.
- (NEW) `apps/dashboard/lib/resilient-fetch.ts` : the hardened core to add — `resilientFetch(...)` wrapping `fetch` with timeout (AbortController) + bounded retry (idempotent-only) + circuit-breaker.

Context (working folder):
- Tests: `tests-bff/` (BFF tier — `proxy.test.ts` guard, `bff-client.test.tsx`, `bff-forms.test.tsx`) using `tests-bff/mocks/server` (msw). `vitest.config.ts` has two projects (`legacy`, `bff`); coverage `lines: 80` over `lib/**/*.ts` + `components/**` — the new lib file MUST be covered.
- `package.json`: `zod@3.25.28`, `msw@2.7.5` (supports `delay()`), `vitest@4.1.8`, `next@^16.2.9` already present — NO new deps needed.
- Next 16 note: the route guard is `apps/dashboard/proxy.ts` (export `proxy`), NOT `middleware.ts` (renamed in next16-upgrade) — relevant to the sibling `security-headers-csp` task, not this one.

Honors (patterns / conventions):
- PROJECT.md core invariant: "No outbound IO without timeout + bounded retry (idempotent only) + circuit breaker" — this task RAISES that rule from the gateway `core/` tier to the dashboard BFF tier.
- Folded byte-identical-preservation lever (v8): keep the public API of `bff-client.ts` byte-identical so the ~20 consumers + frozen `bff-client.test.tsx`/`bff-forms.test.tsx` stay green; resilience is added BEHIND the existing function signatures.
- BFF security model (v18): no client-side `Authorization` header; cookie via `credentials:"include"`; 401→/login redirect except on auth routes. Unchanged by this task.

Anchors the contract cites: `resilientFetch` (new), `bffGet`/`bffPost`/`bffPut`/`bffPatch`/`bffDelete`/`bffAuthPost`, `BffError`/`ProblemDetail`, `proxyRequest` (gw route).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Resilient BFF fetch core — timeout + bounded idempotent retry + circuit-breaker behind the existing client APIs
Framings weighed: shared hardened core behind byte-identical public APIs (chosen) · full consolidation + migrate all 15 api-client consumers now · per-call-site hardening (rejected — scatters the IO rule, no single source)
Must:
<must>
  - M1 Timeout: every BFF→gateway call is made with an AbortController-backed timeout (a sane default, per-call override allowed). On expiry the request is aborted and surfaces a typed timeout error — never a hang.
  - M2 Bounded idempotent retry: GET (idempotent) is retried up to a bounded cap with exponential backoff + jitter on a TRANSIENT failure only (network error, timeout, or upstream 502/503/504). POST/PUT/PATCH/DELETE are NEVER retried.
  - M3 Circuit-breaker: per-target (keyed by origin) consecutive failures past a threshold OPEN the circuit; while open, calls fail fast with a typed error for a cooldown window, then HALF-OPEN to probe recovery (one trial), closing on success. Mirrors the gateway v6 cooldown state machine at the BFF tier.
  - M4 Byte-identical public surface: the signatures + success/HTTP-error behavior of `bffGet/bffPost/bffPut/bffPatch/bffDelete/bffAuthPost` AND `apiGet/apiPost/apiPut/apiDelete` are unchanged; resilience is added BEHIND them; both adapters delegate to ONE core (`resilientFetch`) — a single resilience implementation. The 401→/login redirect and auth-route suppression are preserved exactly.
  - M5 Server proxy hardening: `proxyRequest` (gw route) wraps its upstream `fetch` in an AbortController timeout and maps an upstream network failure / timeout to a typed problem+json response (502/504), NOT an unhandled 500. Cookie/Bearer/401-clear behavior unchanged.
  - M6 Fail-open: if breaker state or config is unavailable, default to closed + sane defaults — resilience degrades to a single direct attempt, never becomes a correctness or availability gate (no try/except past the core boundary leaks).
  - M7 No thundering herd: an upstream 429 is SURFACED to the caller (typed error), never auto-retried — the gateway owns rate-limit/Retry-After semantics.
</must>
Reject:
<reject>
  - request exceeds the timeout window -> "ERR_BFF_TIMEOUT" (504)
  - target circuit is OPEN (cooldown active) -> "ERR_BFF_CIRCUIT_OPEN" (503)
  - transport/network failure persists after the bounded retry cap is exhausted -> "ERR_BFF_NETWORK" (502)
  - a non-idempotent method (POST/PUT/PATCH/DELETE) fails transiently -> single attempt only, surfaced verbatim (NOT retried) -> existing "BffError"(status)
  - upstream returns 4xx/5xx (incl. 401, 429) -> existing "BffError"(status) preserved byte-identical (no new code)
</reject>
After:
<after>
  - Every outbound BFF→gateway call has a bounded worst-case latency (timeout) and a bounded attempt count; repeated failures to one target stop hammering it (breaker opens); every consumer receives either data or a typed `BffError`/problem+json — never a hang, an unhandled rejection, or a raw 500. The ~20 bff-client + ~15 api-client consumers and their frozen tests are unaffected.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Default thresholds (timeout ≈10s, retry cap ≈2 w/ backoff, breaker ≈5 consecutive fails / ≈30s cooldown) — lowest confidence because these are tuning values not derivable from code; if wrong: too-short timeout cuts slow-but-valid admin calls, too-eager retry risks herd. Mitigated: `/api/gw` BUFFERS JSON (`upstream.json()`), no SSE flows through it, so a fixed timeout is safe; values are config-overridable so the CONTRACT shape is unaffected by re-tuning.
  ⚠ api-client.ts stays a thin DELEGATING shim (keeps its `isAuthPath` routing but calls `resilientFetch`) rather than being deleted with its 15 consumers migrated now — lowest confidence because the milestone shared-decision says "collapse into a single client"; reading "single" as one resilience CORE (both adapters delegate) keeps this task one-file-sized and byte-identical, leaving public-name retirement as residue for the apply tasks. If wrong (Tin wants deletion now): +15-file migration folds into this task. → surface at the freeze.
  - [ ] Circuit-breaker state is in-memory per-runtime (module-level Map; per-tab in browser, per-process on server) — acceptable for advisory protection; a shared store is out of FE scope.
  - [ ] Retryable status set = {502,503,504}; 429 deliberately excluded (M7). Confirm at freeze.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: M1 timeout aborts a hanging call
  Given a target that never responds within the timeout window
  When a caller invokes resilientFetch (or bffGet) against it
  Then the request is aborted at the timeout and a BffError code "ERR_BFF_TIMEOUT" (status 504) is thrown
  And no promise is left pending (no hang)

Scenario: M2 idempotent GET retries a transient failure then succeeds
  Given a GET target that fails with a 503 once, then returns 200
  When bffGet is called
  Then the core retries within the bounded cap and returns the 200 body
  And the total attempt count never exceeds the configured cap

Scenario: M2b non-idempotent POST is never retried
  Given a POST target that fails transiently (network error)
  When bffPost is called
  Then exactly ONE upstream attempt is made and the error is surfaced
  And no second POST is sent (no double-apply)

Scenario: M3 circuit opens after consecutive failures and fails fast
  Given a target that has failed N consecutive times (threshold reached)
  When the next call is made within the cooldown window
  Then it fails fast with BffError "ERR_BFF_CIRCUIT_OPEN" (503) WITHOUT a network attempt
  And the underlying fetch is not invoked while the circuit is open

Scenario: M3b circuit half-opens and closes on recovery
  Given an open circuit whose cooldown has elapsed
  When the next call is made and the target now returns 200
  Then one trial request is allowed, it succeeds, and the circuit closes
  And subsequent calls flow normally

Scenario: M4 public API success path is byte-identical
  Given a healthy gateway returning 200 JSON
  When any existing bffGet/bffPost/apiGet/apiPost is called as before
  Then the returned value and the 401→/login + auth-route-suppression behavior are unchanged
  And the frozen bff-client.test.tsx / bff-forms.test.tsx suites stay green

Scenario: M5 server proxy maps upstream timeout to typed 504
  Given the gateway upstream that hangs past the server timeout
  When proxyRequest forwards a GET via /api/gw
  Then it returns a problem+json response with status 504 (not an unhandled 500)
  And the cookie/Bearer/401-clear behavior is unchanged

Scenario: M6 fail-open when breaker config absent
  Given breaker state/config is unavailable
  When a call is made
  Then it degrades to a single direct attempt (closed default), not an error
  And resilience never blocks an otherwise-valid request

Scenario: M7 429 is surfaced, not retried
  Given a GET target returning 429
  When bffGet is called
  Then exactly one attempt is made and BffError(429) is surfaced verbatim
  And no retry is issued (no herd)

Scenario: REJECT network failure exhausts retries
  Given a GET target that always throws a network error
  When bffGet is called
  Then after the bounded retry cap it throws BffError "ERR_BFF_NETWORK" (502)
  And the attempt count equals cap+1 exactly (no unbounded loop)

Scenario: EDGE per-target isolation
  Given target A's circuit is OPEN
  When a call is made to a DIFFERENT target B (healthy)
  Then B's call proceeds normally (breaker is keyed per origin)
  And A's open state does not affect B
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
// ── lib/resilient-fetch.ts (NEW) ───────────────────────────────────────────
// Runtime-agnostic (NO window/document refs) so both the browser adapters and
// the server gw route can call it.

interface ResilientFetchOptions {
  timeoutMs?: number;          // default DEFAULT_TIMEOUT_MS (10_000)
  maxRetries?: number;         // default DEFAULT_MAX_RETRIES (2) — idempotent methods only
  retryableStatuses?: number[];// default [502, 503, 504]   (429 deliberately excluded — M7)
  circuitKey?: string;         // default = new URL(input).origin
}

// Core. Applies timeout (AbortController) + bounded retry (GET only) + per-key breaker.
// RETURNS the raw Response (incl. non-retried 4xx/5xx — adapter's handler interprets it).
// THROWS BffError for transport-level failure:
//   timeout (abort)              -> BffError(504, { code: "ERR_BFF_TIMEOUT",     ... })
//   open circuit (no attempt)    -> BffError(503, { code: "ERR_BFF_CIRCUIT_OPEN", ... })
//   network throw, retries spent  -> BffError(502, { code: "ERR_BFF_NETWORK",     ... })
function resilientFetch(
  input: string | URL,
  init?: RequestInit,
  opts?: ResilientFetchOptions,
): Promise<Response>

// Breaker config (module constants, env-overridable; fail-open if unreadable — M6)
const DEFAULT_TIMEOUT_MS = 10_000
const DEFAULT_MAX_RETRIES = 2
const BREAKER_FAILURE_THRESHOLD = 5     // consecutive failures -> OPEN
const BREAKER_COOLDOWN_MS = 30_000      // OPEN -> HALF_OPEN after this

// Test seam ONLY (behavior asserted via fetch-call counts; this just isolates state):
function __resetBreakers(): void

// ── BffError / ProblemDetail (EXISTING, lib/bff-client.ts) — REUSED, not forked ──
// interface ProblemDetail { type?; title; status; code? }   // .code carries the new codes
// class BffError extends Error { status: number; problem: ProblemDetail }

// ── Public adapters — signatures BYTE-IDENTICAL (M4); bodies now delegate to core ──
// lib/bff-client.ts : bffGet/bffPost/bffPut/bffPatch/bffDelete/bffAuthPost
//    each: resilientFetch(`${appBase()}/api/gw${path}` | auth-url, {method, credentials, headers, body})
//          then handleBffResponse(res, isAuth?)   // 401→/login + auth-suppress UNCHANGED
// lib/api-client.ts : apiGet/apiPost/apiPut/apiDelete  (keeps isAuthPath routing)
//    each: resilientFetch(url, {...}) then handleResponse(res, isAuthPath(path))
//    ApiError/handleResponse preserved; the only change is fetch -> resilientFetch.

// ── Server gw proxy (app/api/gw/[...path]/route.ts) — proxyRequest ──
//    upstream call becomes: resilientFetch(upstreamUrl, {method, headers, body},
//                                           { timeoutMs: SERVER_TIMEOUT_MS })
//    catch (BffError e) -> NextResponse.json({ code: e.problem.code }, { status: e.status })
//    All existing behavior (no-cookie 401, upstream-401 clear-cookie, 204, verbatim) UNCHANGED.
```

Schema: none — no DB. Breaker state is in-memory (module-level `Map<string, BreakerEntry>`, per-runtime). No migration.

// ── FREEZE ADDENDUM (Tin chose "fully delete api-client now") ────────────────
// api-client.ts is DELETED this task; all ~15 consumers migrate to bff-client:
//   • ApiError -> BffError  (IDENTICAL shape {status, problem}; pure rename — 8 files:
//       LoginForm, SignupForm, UsageStatsCards, BudgetWidget, BudgetEditForm,
//       ModelCatalogTable, CreateKeyDialog, MembersPage)
//   • apiGet/apiPut (gw paths) -> bffGet/bffPut
//   • apiGet("/api/auth/me")  (the ONE auth-path GET, in app/(app)/app/members/page.tsx)
//       -> NEW additive `bffAuthGet<T>(path): Promise<T>` on bff-client, mirroring
//          bffAuthPost (direct /api/auth/*, suppress 401-redirect). resilientFetch-backed.
//   • test refs updated (import path only, NO assertion weakened):
//       tests/setup.ts, tests-bff/bff-client.test.tsx,
//       tests/{legal-pages,status-page,docs-blog,pricing-page}.test.tsx
// Net: ONE client module (bff-client) over ONE resilience core (resilient-fetch);
//      api-client.ts removed. The migration's safety net = the existing component +
//      marketing suites staying GREEN after the import swap.

Least-sure flag surfaced at freeze: [contract] full api-client deletion + ~15-consumer migration is folded into THIS task — because Tin chose deletion over a delegating shim; if wrong: task scope balloons and a consumer's error-handling could drift during the ApiError→BffError rename (mitigated: identical shape + existing component/marketing suites are the green safety net). · [contract] default thresholds 10s timeout / 2 retries / breaker 5-fail·30s — because they are tuning values not derivable from code; if wrong: re-tune via env (config-overridable, frozen shape unaffected).
Status: FROZEN @ v1 — approved by Tin 2026-06-26 (chose full api-client deletion + migration; default thresholds 10s/2/5·30s accepted as config-overridable)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% lines on `resilient-fetch.ts` (repo gate is 80% over lib/**)
Plan (one test per scenario, asserting behavior via fetch-call counts / thrown BffError codes — never internals):
<test_plan>
  - test_timeout_aborts: stub a never-resolving fetch / msw delay > timeout / act bffGet w/ short timeoutMs / assert throws BffError code ERR_BFF_TIMEOUT status 504 / assert no pending hang (fake timers)
  - test_get_retries_then_succeeds: fetch 503 once then 200 / act bffGet / assert returns body / assert fetch called == 2 (≤ cap+1)
  - test_post_never_retried: fetch rejects network / act bffPost / assert throws / assert fetch called == 1
  - test_circuit_opens_fail_fast: drive N consecutive failures / act next call / assert throws ERR_BFF_CIRCUIT_OPEN 503 / assert fetch NOT called while open
  - test_circuit_halfopen_recovers: open then advance cooldown (fake timers) / next call returns 200 / assert closes + returns body
  - test_public_api_byte_identical: healthy 200 / assert bffGet/apiGet return value unchanged + 401→/login still fires (existing suites stay green)
  - test_proxy_maps_timeout_504: msw upstream hang / act proxyRequest GET / assert NextResponse status 504 problem+json (not 500) / assert cookie behavior unchanged
  - test_failopen_when_no_config: breaker disabled/unreadable / assert single direct attempt, no error
  - test_429_surfaced_not_retried: fetch 429 / act bffGet / assert BffError(429) + fetch called == 1
  - test_network_exhausts_to_502: fetch always network-throws / act bffGet / assert ERR_BFF_NETWORK 502 + fetch called == cap+1
  - test_per_target_isolation: open circuit for origin A / call origin B healthy / assert B succeeds
</test_plan>

Tests live in: `apps/dashboard/tests-bff/resilient-fetch.test.ts` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/lib/resilient-fetch.ts` `apps/dashboard/lib/bff-client.ts` `apps/dashboard/lib/api-client.ts`(DELETE) `apps/dashboard/app/api/gw/[...path]/route.ts` `apps/dashboard/components/` `apps/dashboard/app/(app)/app/members/page.tsx` `apps/dashboard/tests/setup.ts` `apps/dashboard/tests-bff/`
Strategy (ordered batches): 1. write `resilient-fetch.ts` core (timeout→retry→breaker, BffError mapping) 2. delegate bff-client fetch→resilientFetch + add `bffAuthGet` (signatures untouched) 3. harden `proxyRequest` (resilientFetch + BffError→problem+json) 4. MIGRATE all api-client consumers→bff-client (ApiError→BffError, apiGet/apiPut→bff*, auth/me→bffAuthGet) + update test import refs 5. DELETE api-client.ts
Safety rule (feature-specific): the breaker/retry path NEVER changes a request's method or body; non-idempotent methods get exactly one attempt; resilientFetch holds NO window/document reference (runtime-agnostic).
Code lives in: `apps/dashboard/lib/` + `apps/dashboard/app/api/gw/[...path]/`
Constraints: do NOT change any test or the contract; NO new npm dependency (timeout via AbortController, breaker hand-rolled); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 514 green (62 files), stable across 3 runs incl. coverage
- [x] coverage did not decrease — resilient-fetch.ts 93.9% lines (target 90%); bff-client.ts 90.6%; lib aggregate 91.2%
- [x] no test or contract was altered during build — contract untouched; §4 red assertions unchanged. Test-INFRA additions only (tests/setup.ts + tests-bff/setup.ts: per-test `__resetBreakers()` + instant backoff env) — both declared in §5 scope; +1 STRENGTHENING test (single-probe) added post-refute, no assertion weakened
- [x] the green was EARNED — adversarial security-expert refute-read: VERDICT EARNED. Verified retry proves a hard cap (attempts==cap+1), breaker open proves fetch NOT called (call-count==0), timeout proves real abort (200ms delay vs 10ms timeout). No vacuous/overfit/stubbed asserts found
- [x] concurrency / timing safe — refute-read found a half-open race (>1 concurrent trial); FIXED with a single-probe `probing` sentinel + new `test_circuit_halfopen_single_probe` locks it. No residue remaining
- [x] no exposed secrets, injection openings, or unexpected dependencies — refute-read cleared SSRF (App-Router path normalization), header-injection (undici CRLF reject), token/cookie leakage (BffError carries only code/title, no upstream body); ZERO new npm deps
- [x] layering & dependencies follow CONVENTIONS.md — core is runtime-agnostic (no window ref); BffError single-sourced in the lowest layer, re-exported (instanceof holds); IO-rule raised to BFF tier per PROJECT.md
- [x] a person reviewed and approved the change — Tin approved the §3 freeze + the full-deletion scope; auto-gate under `autonomy: auto` (no residue), accountable owner: Tin Dang

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] A slow/hanging gateway no longer hangs the UI — the proxy returns a 504 `ERR_BFF_TIMEOUT` problem+json — confirmed by test_proxy_maps_timeout_504 (NextResponse.status===504) + the gw route catch mapping
- [x] A flapping gateway is not hammered — after N failures calls fail fast with 503 `ERR_BFF_CIRCUIT_OPEN` and NO network attempt, recovering on a half-open probe — confirmed by test_circuit_opens_fail_fast (probe count frozen) + test_circuit_halfopen_recovers
- [x] No double-apply on writes — POST/PUT/PATCH/DELETE make exactly one attempt — confirmed by test_post_never_retried (attempts===1)
- [x] Existing consumers unchanged — 401→/login still fires, ~20 bff-client + 15 migrated consumers green — confirmed by the unchanged bff-client.test.tsx/bff-forms.test.tsx + full 514-green suite + `next build` exit 0

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `resilientFetch` referenced by all 7 bff-client adapters + the gw route; `BffError`/`ProblemDetail` re-exported from bff-client and consumed app-wide; `bffAuthGet` consumed by members/page.tsx; `__resetBreakers` by both test setups. Confirmed via grep + tsc(0) + 514-green.
- [x] DEAD-CODE — `lib/api-client.ts` DELETED (zero importers verified pre-delete); no orphan left (eslint 0 errors). `__setBreakerConfigForTest` used by the breaker tests.
- [x] SEMANTIC — read the full migration diff (15 consumers): every `apiGet/apiPut→bff*`, `ApiError→BffError` (identical shape), and the one auth-path `apiGet("/api/auth/me")→bffAuthGet("me")` confirmed correct; no path mis-routed, no error message dropped.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (freeze + scope approval) · auto-resolved under autonomy:auto (no residue) · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): BFF error-rate by code (ERR_BFF_TIMEOUT/_CIRCUIT_OPEN/_NETWORK), circuit-open frequency per origin, retry count, p95 BFF latency (retry adds bounded latency to error surfacing).

### Spec delta
- [SPEC · open] Breaker state is in-memory per-runtime (per-tab in browser); a multi-tab user gets independent circuits and the server proxy circuit is per-process — a shared store (Redis) would unify, but is out of FE scope (evidence: §3 schema note).
- [SPEC · open] Default thresholds (10s/2-retry/5-fail·30s) are guesses; tune from the `Watch` metrics once real traffic exists (evidence: §1 ⚠ flag).
- [SPEC · seeded] api-client.ts public-name retirement is fully DONE here (Tin chose deletion); the apply-tasks (harden-admin/auth) inherit no migration residue (evidence: gate diff = dashboard-only, api-client deleted).
- [SPEC · open] `fetchWithTimeout` discards a caller-supplied `init.signal` (timeout owns the signal); compose the two if external cancellation is ever needed (evidence: refute-read MINOR).

### Competency deltas
- [ADD · folded] `_scope_walk` descended into `.claude/worktrees/*` (nested git worktrees from a PARALLEL program) and counted their uncommitted files as this task's touch → false `scope_violation` at the gate. FIXED by adding `.claude` to `_SCOPE_EXCLUDE_DIRS` (same regenerated/foreign-artifact class as `.next`/`node_modules`); required a re-cross (`phase tests`→advance→advance) to re-snapshot the baseline. Engine fix is LOCAL to this repo's `.add/tooling/add.py` — should be upstreamed (evidence: gate attempt 1 listed 14 `.claude/worktrees/dashboard/apps/gateway/...` files). [folded foundation-version 37]
- [TDD · folded] A module-global circuit-breaker poisons cross-test state — error-path tests trip it for later tests (saw "Service temporarily unavailable" in keys.test). FIX = reset per-test in the SHARED setup (mirrors msw/localStorage reset), and force instant retry backoff via env so error-path tests don't incur real latency that flakes `waitFor` under coverage (evidence: 2 keys tests failed pre-reset; intermittent coverage flake pre-backoff-env). [folded foundation-version 37]
- [SDD · folded] A single error class must live in the LOWEST layer and be re-exported upward (BffError defined in resilient-fetch.ts, re-exported by bff-client.ts) — defining it in the consumer would force a circular import and break `instanceof` across the app (evidence: refute-read confirmed instanceof holds). [folded foundation-version 37]
- [ADD · folded] An adversarial refute-read caught a real half-open concurrency gap (>1 parallel trial) that 12 green tests missed; closed by STRENGTHENING (single-probe `probing` sentinel + a 13th test), never by weakening — the refute-read is worth its cost on concurrency primitives (evidence: VERDICT EARNED after fix). [folded foundation-version 37]
