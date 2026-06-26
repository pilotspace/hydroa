# TASK: Security headers + CSP across all dashboard routes

slug: security-headers-csp · created: 2026-06-26 · stage: production
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
- `apps/dashboard/next.config.ts` : `nextConfig` (currently `{}`) — add an async `headers()` returning the static security headers for `source: "/:path*"` (all routes). TESTABLE by invoking `nextConfig.headers()` and asserting the returned array.
- `apps/dashboard/proxy.ts` : `proxy(req)` route guard, `config.matcher = ["/app","/app/:path*"]`, Node runtime — the cookie guard. IF the CSP is nonce-based, this is where a per-request nonce + CSP header is set (matcher widened to all routes); ELSE untouched. Frozen v38 route-split behavior (307 redirect) must be preserved.
- `apps/dashboard/app/layout.tsx` : root layout renders an INLINE `<script>{themeScript()}</script>` in <head> (no-flash theme) — THE CSP obstacle. Under a nonce CSP it needs `nonce={...}` read from `next/headers`. Inter via `next/font/google` is self-hosted (no runtime external font origin).
- `apps/dashboard/components/ui/theme-script.ts` : `themeScript()` returns the inline script source (relevant if a hash-based allowance is chosen).

Context (working folder):
- `app/globals.css` imports `tw-animate-css` (bundled, no external origin). No analytics/third-party script tags found. Self-hosted behind Envoy.
- Tests: `tests-bff/proxy.test.ts` pins the guard (307 redirect, cookie passthrough) — must stay green if proxy.ts changes. next.config `headers()` is unit-testable by importing `nextConfig` and calling it.
- `package.json`: no new dep needed (nonce via Node `crypto`; headers are plain config).

Honors (patterns / conventions):
- v38 marketing-shell route-split (frozen): `/` + `/(marketing)/*` + `/login` + `/signup` PUBLIC (proxy does NOT match); `/app/*` GATED. Any matcher widening for CSP must NOT change auth-guard semantics (only ADD a header).
- IO-rule / design-for-failure: header logic is pure/synchronous (no IO) — fail-open is trivial; the nonce generator must never throw.
- Milestone shared-decision: CSP is the riskiest cross-cutting contract — freeze its shape FIRST; record any relaxation (e.g. `'unsafe-inline'`) as an auditable freeze decision.

Anchors the contract cites: `nextConfig.headers()` (new), the header name set (Content-Security-Policy · Strict-Transport-Security · X-Frame-Options · X-Content-Type-Options · Referrer-Policy · Permissions-Policy), `proxy()` (+ nonce, only if nonce-CSP chosen), `themeScript()`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Static security headers + pragmatic CSP on every dashboard response (next.config headers())
Framings weighed: static next.config headers() pragmatic CSP (chosen — Tin 2026-06-26) · nonce+strict-dynamic via proxy.ts · report-only-first
Must:
<must>
  - M1 Every response on every route (`source: "/:path*"`) carries all 6 headers: Content-Security-Policy, Strict-Transport-Security, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy.
  - M2 CSP value = `default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'; upgrade-insecure-requests`.
  - M3 HSTS = `max-age=63072000; includeSubDomains; preload` (>=1 year).
  - M4 X-Frame-Options=`DENY` · X-Content-Type-Options=`nosniff` · Referrer-Policy=`strict-origin-when-cross-origin` · Permissions-Policy denies camera, microphone, geolocation (e.g. `camera=(), microphone=(), geolocation=()`).
  - M5 Implemented purely via `next.config.ts` `headers()` (no IO, synchronous shape) — proxy.ts is NOT touched; the v38 route-split + auth guard are unchanged.
  - M6 The header config is unit-testable: `await nextConfig.headers()` returns one entry with `source:"/:path*"` whose `headers` array contains the 6 by key/value.
</must>
Reject:
<reject>
  - a page framed by another origin (clickjacking) -> blocked by `frame-ancestors 'none'` + `X-Frame-Options: DENY`
  - a response body MIME-sniffed to execute -> blocked by `X-Content-Type-Options: nosniff`
  - an injected <object>/<embed>/<base> tag -> blocked by `object-src 'none'` / `base-uri 'self'`
  - a passive-mixed-content HTTP subresource -> upgraded by `upgrade-insecure-requests`
  - a cross-origin form post exfiltration -> blocked by `form-action 'self'`
</reject>
After:
<after>
  - Every dashboard HTTP response carries the 6 security headers; clickjacking, MIME-sniffing, object/embed injection, base-uri hijack, and cross-origin form posts are blocked at the browser; HTTP subresources upgrade to HTTPS. The proxy guard, route-split, and all 514 existing tests are unchanged.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ `script-src 'self' 'unsafe-inline'` is an ACCEPTED relaxation (Tin's freeze choice) — weaker inline-script-XSS posture than nonce; mitigated: the dashboard renders no untrusted HTML, and a SPEC delta will upgrade to nonce+strict-dynamic once task 6's browser harness can verify it. If wrong (a real XSS sink exists): the relaxation is the gap.
  ⚠ `connect-src 'self'` suffices — the BFF is same-origin (`/api/*`) so all fetches are 'self'. If a future direct-to-gateway browser call is added it must extend connect-src. Confirm at freeze.
  - [ ] Referrer-Policy `strict-origin-when-cross-origin` (browser default-ish, leaks only origin cross-site) vs stricter `no-referrer` — chosen for analytics-friendliness; deny harder if required.
  - [ ] HSTS only takes effect over HTTPS (Envoy terminates TLS); harmless header over HTTP in local dev.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: M1 all six headers present on every route
  Given the dashboard next.config
  When nextConfig.headers() is evaluated
  Then it returns a rule for source "/:path*" whose header keys include all six
  And the proxy guard config is unchanged

Scenario: M2 CSP directive value is the agreed pragmatic policy
  Given the headers rule
  When the Content-Security-Policy value is read
  Then it equals the agreed directive string (default-src 'self'; script-src 'self' 'unsafe-inline'; … frame-ancestors 'none'; object-src 'none'; base-uri 'self'; form-action 'self'; upgrade-insecure-requests)
  And it contains NO 'unsafe-eval' and NO wildcard '*' in default-src

Scenario: M3 HSTS is at least one year + includeSubDomains
  Given the headers rule
  When Strict-Transport-Security is read
  Then max-age >= 31536000 and it includes "includeSubDomains"

Scenario: M4 anti-clickjacking + nosniff + referrer + permissions
  Given the headers rule
  When the four supporting headers are read
  Then X-Frame-Options=DENY, X-Content-Type-Options=nosniff, Referrer-Policy=strict-origin-when-cross-origin, and Permissions-Policy denies camera/microphone/geolocation

Scenario: M5/M6 proxy + existing suite untouched
  Given the change is confined to next.config.ts
  When the full test suite runs
  Then tests-bff/proxy.test.ts (307 guard) and all prior tests stay green
  And proxy.ts is byte-identical

Scenario: REJECT framing is forbidden
  Given a response carrying the CSP + X-Frame-Options
  When another origin attempts to frame the page
  Then frame-ancestors 'none' (and X-Frame-Options DENY) forbid it
  And no header is missing that would otherwise allow framing

Scenario: REJECT object/base/form injection forbidden
  Given the CSP value
  When the policy is parsed
  Then object-src 'none', base-uri 'self', form-action 'self' are all present
  And default-src 'self' bounds every unlisted fetch directive
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
// next.config.ts — additive async headers() (NO other config change)
nextConfig.headers(): Promise<Array<{
  source: string,
  headers: Array<{ key: string, value: string }>
}>>
// returns EXACTLY one rule:
{
  source: "/:path*",
  headers: [
    { key: "Content-Security-Policy",   value: CSP },          // see below — single-spaced, "; "-joined
    { key: "Strict-Transport-Security", value: "max-age=63072000; includeSubDomains; preload" },
    { key: "X-Frame-Options",           value: "DENY" },
    { key: "X-Content-Type-Options",    value: "nosniff" },
    { key: "Referrer-Policy",           value: "strict-origin-when-cross-origin" },
    { key: "Permissions-Policy",        value: "camera=(), microphone=(), geolocation=(), interest-cohort=()" },
  ],
}

CSP = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    + "img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
    + "object-src 'none'; base-uri 'self'; form-action 'self'; upgrade-insecure-requests"
```

Schema: none — pure static config, no DB, no migration, no new dependency. proxy.ts UNTOUCHED.

Least-sure flag surfaced at freeze: [contract] `script-src 'self' 'unsafe-inline'` relaxation — Tin chose the pragmatic static CSP (over nonce+strict-dynamic) at the milestone freeze; weaker inline-script-XSS posture, accepted because the dashboard chrome renders no untrusted HTML and a SPEC delta will upgrade to nonce once the task-6 browser harness can verify enforcement; if wrong (a real XSS sink): the relaxation is the gap. · [contract] `connect-src 'self'` assumes the BFF stays same-origin; if a direct-to-gateway browser call is ever added it must extend connect-src.
Status: FROZEN @ v1 — approved by Tin 2026-06-26 (pragmatic static CSP via next.config; the 'unsafe-inline' relaxation explicitly accepted, nonce-upgrade deferred to a SPEC delta)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral (config object asserted; no % threshold — next.config is excluded from coverage include globs)
Plan (one test per scenario, asserting behavior not internals — import `nextConfig` from `@/next.config` and call `headers()`):
<test_plan>
  - test_all_six_headers_present: act await nextConfig.headers() / assert one rule source==="/:path*" with all 6 header keys present
  - test_csp_value_is_pragmatic_policy: assert CSP === agreed string; assert it contains object-src 'none', base-uri 'self', frame-ancestors 'none', form-action 'self', upgrade-insecure-requests; assert NO 'unsafe-eval' and NO "default-src *"
  - test_hsts_one_year_subdomains: assert STS max-age>=31536000 && includes "includeSubDomains"
  - test_supporting_headers: assert XFO===DENY, XCTO===nosniff, Referrer-Policy===strict-origin-when-cross-origin, Permissions-Policy denies camera/microphone/geolocation
  - test_proxy_and_suite_unchanged: proxy.ts byte-identical (tests-bff/proxy.test.ts stays green); full suite green
</test_plan>

Tests live in: `apps/dashboard/tests-bff/security-headers.test.ts` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/next.config.ts` `apps/dashboard/tests-bff/security-headers.test.ts`
Strategy (ordered batches): 1. add the agreed `headers()` to next.config.ts (CSP constant + 6-header rule for "/:path*") 2. confirm red→green + full suite + proxy untouched
Safety rule (feature-specific): proxy.ts and app/layout.tsx are NOT touched (pragmatic CSP needs no nonce); headers() is pure/sync (no IO).
Code lives in: `apps/dashboard/`
Constraints: do NOT change any test or the contract; NO new npm dependency; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 518 green (63 files); +4 new security-headers tests
- [x] coverage did not decrease — next.config.ts is config (excluded from coverage globs); no source coverage change
- [x] no test or contract was altered during build — only next.config.ts written; §4 tests + contract untouched; proxy.ts byte-identical
- [x] the green was EARNED — tests assert the EXACT directive strings (not vacuous); confirmed beyond the unit test by a RUNNING `next start` server emitting all 6 headers on `/` and `/login` (real curl evidence). No logic to overfit/stub — pure static config
- [x] concurrency / timing safe — N/A: pure synchronous static config, no IO, no shared state
- [x] no exposed secrets, injection openings, or unexpected dependencies — no secret; ZERO new deps; the change ADDS defense-in-depth (CSP/HSTS/anti-clickjack). The accepted `'unsafe-inline'` script relaxation is the one auditable freeze decision (Tin-approved), tracked as a SPEC delta
- [x] layering & dependencies follow CONVENTIONS.md — config-only; proxy/layout/route-split untouched (M5)
- [x] a person reviewed and approved the change — Tin approved the CSP strictness + freeze; auto-gate under autonomy:auto (no residue), accountable owner: Tin Dang

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] Every response carries all 6 security headers — confirmed by `curl -I` against a live `next start` on `/` AND `/login` (all 6 present, CSP value byte-matches the contract)
- [x] CSP blocks framing/object/base/mixed-content — confirmed: served CSP contains `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`, `upgrade-insecure-requests` (+ X-Frame-Options DENY)
- [x] No regression to the auth guard or any surface — confirmed: proxy.ts byte-identical, full 518-green suite, `next build` exit 0

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING — `headers()` is invoked by Next at build/runtime (proven by live curl); `CONTENT_SECURITY_POLICY`/`SECURITY_HEADERS` consumed by the returned rule.
- [x] DEAD-CODE — no orphan symbols; both constants referenced.
- [x] SEMANTIC — read the served CSP in full against the frozen §3 directive string: exact match; no stray `'unsafe-eval'`, no `default-src *`.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (CSP strictness + freeze approval) · auto-resolved under autonomy:auto (no residue) · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

Watch (reuse scenarios as monitors): CSP violation reports (once a report-uri is added), any blocked-resource console errors in the browser, header presence in prod responses.

### Spec delta
- [SPEC · open] UPGRADE the CSP from `script-src 'self' 'unsafe-inline'` to nonce + `'strict-dynamic'` once task 6's real-browser harness can verify enforcement without breaking Next hydration (evidence: Tin's freeze accepted the relaxation as interim; the inline themeScript + Next bootstrap scripts are the blockers).
- [SPEC · open] Add a CSP `report-uri`/`report-to` + a BFF report-collector endpoint to OBSERVE violations before tightening (evidence: no telemetry today on what the policy would break).
- [SPEC · open] `connect-src 'self'` must be extended if any direct-to-gateway browser call is ever added (today all fetches are same-origin via /api/*) (evidence: §1 ⚠ flag).

### Competency deltas
- [SDD · open] A static `next.config.ts` `headers()` is fully UNIT-testable by importing `nextConfig` and calling `headers()` (no running server needed for the red/green), while the RUNTIME emission is confirmed separately via a live `next start` + curl — the two together prove both the config shape AND that Next actually serves it (evidence: 4 unit tests + live curl on / and /login).
- [ADD · open] For a pure-static-config task with no logic/concurrency/secret, a full security-expert refute-read is overkill — the earned-green is proven by the running server emitting the exact contracted values; reserve the heavy refute-read for tasks with real logic (evidence: this gate auto-resolved on live evidence).
- [SDD · open] CSP relaxations must be recorded at the freeze as an auditable decision with a named upgrade path (here: `'unsafe-inline'`→nonce SPEC delta) — never a silent permanent allowance (evidence: §3 Least-sure flag + the SPEC delta above).
