# TASK: Zod fail-closed input validation at every BFF route boundary

slug: bff-input-validation · created: 2026-06-26 · stage: production
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
- `apps/dashboard/app/api/auth/login/route.ts` : `POST` — currently ad-hoc `typeof email/password` checks → `400 ERR_BFF_PAYLOAD_INVALID`; on valid, fetches `${gateway}/admin/auth/login`. Replace the ad-hoc checks with the shared zod validator (SAME 400 contract).
- `apps/dashboard/app/api/auth/signup/route.ts` : `POST` — same ad-hoc pattern for `{tenant_name,email,password}` → 400; signup then auto-login. Replace with zod.
- `apps/dashboard/app/api/auth/oidc/login/route.ts` : `GET` — already hardened (drops all params except `domain`, encodeURIComponent, timeout, fail-closed 502). Add a LIGHT bounded `domain` validation (defense-in-depth; already safe).
- `apps/dashboard/app/api/gw/[...path]/route.ts` : `proxyRequest` — generic passthrough; add a request BODY-SIZE guard (reject oversized → 413) BEFORE forwarding. Path SSRF already mitigated (App-Router normalization, confirmed task-1 refute-read). NOTE: this file was hardened in task-1 (resilientFetch) — touch only the body-read.
- (NEW) `apps/dashboard/lib/bff-validation.ts` : `loginSchema`/`signupSchema` (zod) + `parseJsonBody(req, schema)` helper returning `{ok,data}` | a `400 ERR_BFF_PAYLOAD_INVALID` NextResponse.

Context (working folder):
- Tests: `tests-bff/route-handlers.test.ts` PINS `test_bff_login_missing_fields_400` → `status 400` + `code "ERR_BFF_PAYLOAD_INVALID"` (a SHIPPED contract — must stay green). `tests/login.test.tsx`/`signup.test.tsx` are client-form tests.
- `zod@3.25.28` is ALREADY a dep and ALREADY used client-side in `LoginForm`/`SignupForm`/`CreateKeyDialog`/teams dialogs — server-side use is consistent, NO new dep.
- The gateway is the auth AUTHORITY (email format / password policy / uniqueness) — the BFF guard is a THIN presence+type+bounds gate, NOT a duplicate of gateway business validation.

Honors (patterns / conventions):
- Folded byte-identical-preservation: KEEP the `400 ERR_BFF_PAYLOAD_INVALID` status (a shipped test pins it) — do NOT change to 422; the milestone's "422" draft wording reconciles to "4xx fail-closed". Surface this at the freeze.
- BFF security model (v18): routes never expose the JWT; validation happens BEFORE any upstream fetch (fail-closed).
- IO-rule: the size guard + zod parse are pure/sync, no IO; they run before the bounded upstream call.

Anchors the contract cites: `parseJsonBody` (new), `loginSchema`/`signupSchema` (new), `POST` (login/signup), `GET` (oidc/login), `proxyRequest` (gw size guard), error codes `ERR_BFF_PAYLOAD_INVALID` (preserved) + `ERR_BFF_PAYLOAD_TOO_LARGE` (new, 413).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Shared zod fail-closed input validation at the BFF route boundary (+ gw body-size guard)
Framings weighed: shared zod helper + preserve the 400 contract (chosen) · change status to 422 (rejected — breaks a shipped test, not a Tin decision) · per-route ad-hoc checks (rejected — the status quo; inconsistent, no bounds)
Must:
<must>
  - M1 A shared `parseJsonBody(req, schema)` validates the JSON body BEFORE any upstream fetch; on failure it returns `400 ERR_BFF_PAYLOAD_INVALID` (preserving the shipped contract) and the upstream is NEVER called.
  - M2 `loginSchema` = `{ email: non-empty string ≤320, password: non-empty string ≤4096 }`; `signupSchema` = `{ tenant_name: non-empty ≤200, email: ≤320, password: ≤4096 }`. Presence + type + BOUNDS only — NOT email-format/password-policy (the gateway owns those).
  - M3 login + signup routes use the shared validator; their happy-path + gateway-error passthrough behavior is unchanged (cookie set, JWT never in body).
  - M4 The gw proxy (`proxyRequest`) rejects a request body larger than a max (default 1 MiB, env-overridable) with `413 ERR_BFF_PAYLOAD_TOO_LARGE` BEFORE forwarding; within-limit bodies forward unchanged.
  - M5 oidc/login adds a light bounded `domain` validation (≤253 chars, no control chars); an invalid domain is dropped (treated as absent) so the route stays fail-safe — never an upstream call with a smuggled value.
  - M6 Fail-closed everywhere: a non-object body, wrong-typed field, oversized field, or oversized stream is rejected with no upstream call; the validator never throws past its boundary.
</must>
Reject:
<reject>
  - non-JSON / non-object body, or a missing/empty/wrong-typed required field -> "ERR_BFF_PAYLOAD_INVALID" (400)
  - a field exceeding its max length -> "ERR_BFF_PAYLOAD_INVALID" (400)
  - a gw request body exceeding the size cap -> "ERR_BFF_PAYLOAD_TOO_LARGE" (413)
  - an oidc `domain` param with control chars / over-long -> dropped (param treated as absent; request proceeds without it)
</reject>
After:
<after>
  - Every typed BFF route rejects a malformed/oversized body with a 4xx BEFORE any upstream call; no unvalidated body reaches the gateway; the generic gw proxy caps body size. The login/signup happy paths, the shipped 400 contract, the oidc relay, and all existing tests are unchanged.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ KEEP `400 ERR_BFF_PAYLOAD_INVALID` (not 422) — lowest confidence because the milestone exit criterion #2 drafted "422"; preserving 400 honors the shipped `test_bff_login_missing_fields_400` and the no-weaken-a-test rule. The criterion's INTENT (reject-before-upstream, fail-closed) is met. If Tin actually wants 422: a follow-up change-request updates the routes + that test together. Reconcile the milestone wording to "4xx".
  ⚠ Field max-lengths (email≤320 per RFC5321, password≤4096, tenant≤200) + gw cap 1 MiB are guard bounds, not policy — if a legitimate payload exceeds them the guard rejects it. Mitigated: generous + env-overridable for the gw cap.
  - [ ] `.strict()` (reject unknown keys) NOT used — clients send exactly the documented fields, but a non-strict schema avoids breaking a future additive field; the gateway ignores extras anyway. Confirm.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: M1/M3 valid login still works
  Given a valid {email,password} body and a gateway returning a token
  When POST /api/auth/login runs
  Then it sets the ai_proxy_session cookie and returns ok (200), JWT not in body
  And the shipped happy-path test stays green

Scenario: M1 missing fields rejected before upstream (shipped contract)
  Given an empty {} body
  When POST /api/auth/login runs
  Then it returns 400 ERR_BFF_PAYLOAD_INVALID
  And the gateway /admin/auth/login is NEVER called

Scenario: M2 oversized field rejected
  Given a login body whose password is 5000 chars
  When POST /api/auth/login runs
  Then it returns 400 ERR_BFF_PAYLOAD_INVALID
  And no upstream call is made

Scenario: M2 wrong-typed field rejected
  Given a signup body where tenant_name is a number
  When POST /api/auth/signup runs
  Then it returns 400 ERR_BFF_PAYLOAD_INVALID
  And no upstream signup call is made

Scenario: M4 gw oversized body rejected with 413
  Given a POST /api/gw/admin/keys with a body over the size cap
  When proxyRequest runs
  Then it returns 413 ERR_BFF_PAYLOAD_TOO_LARGE
  And the gateway is NEVER called

Scenario: M4 gw within-limit body forwards unchanged
  Given a normal small POST body via /api/gw
  When proxyRequest runs
  Then it forwards to the gateway as before and proxies the response
  And the task-1 timeout/breaker behavior is unchanged

Scenario: M5 oidc invalid domain dropped
  Given GET /api/auth/oidc/login?domain=<512 chars with control chars>
  When the route runs
  Then the upstream is called WITHOUT a domain param (the bad value is dropped)
  And the route still fails safe / relays the gateway redirect

Scenario: M6 non-object body rejected
  Given a request whose JSON body is a bare string "hi"
  When any typed route validates it
  Then it returns 400 ERR_BFF_PAYLOAD_INVALID
  And no upstream call is made
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
// lib/bff-validation.ts (NEW)
import { z } from "zod";
const loginSchema  = z.object({ email: z.string().min(1).max(320), password: z.string().min(1).max(4096) })
const signupSchema = z.object({ tenant_name: z.string().min(1).max(200),
                                email: z.string().min(1).max(320), password: z.string().min(1).max(4096) })
// parse JSON body, fail-closed. Returns the data OR a ready 400 NextResponse.
function parseJsonBody<T>(req: NextRequest, schema: z.ZodSchema<T>):
    Promise<{ ok: true; data: T } | { ok: false; response: NextResponse }>
// on invalid JSON / non-object / schema failure -> { ok:false, response: 400 {code:"ERR_BFF_PAYLOAD_INVALID"} }
function sanitizeDomain(raw: string | null): string | null   // <=253, no control chars, else null

// login/signup route POST:  const r = await parseJsonBody(req, loginSchema)
//                           if (!r.ok) return r.response   // BEFORE any upstream fetch
//                           ...existing happy path with r.data...  // 400 contract preserved

// gw proxy proxyRequest (additive guard, before forwarding a mutating body):
//   MAX = env GW_MAX_BODY_BYTES || 1_048_576
//   if Content-Length > MAX  OR  the read body byte length > MAX
//      -> NextResponse.json({ code: "ERR_BFF_PAYLOAD_TOO_LARGE" }, { status: 413 })   // no upstream call

// oidc/login GET:  const domain = sanitizeDomain(searchParams.get("domain"))  // bad -> null -> dropped

Responses:
  400 -> { code: "ERR_BFF_PAYLOAD_INVALID" }        (login/signup malformed/oversized/typed body)
  413 -> { code: "ERR_BFF_PAYLOAD_TOO_LARGE" }       (gw body over cap)
  200/201 -> unchanged (cookie set, JWT never in body)
```

Schema: none — no DB. No new dependency (zod already present). proxy.ts UNTOUCHED.

Least-sure flag surfaced at freeze: [contract] PRESERVE `400 ERR_BFF_PAYLOAD_INVALID` (not the milestone's drafted 422) — because a shipped test (`test_bff_login_missing_fields_400`) pins 400 and the no-weaken-a-test rule forbids changing it for convenience; the criterion's intent (reject-before-upstream) is fully met. If wrong (Tin wants 422): a separate change-request updates routes + that test together. · [contract] `ERR_BFF_PAYLOAD_TOO_LARGE` (413) is a NEW code for the gw size guard — additive, no existing consumer.
Status: FROZEN @ v1 — approved by Tin 2026-06-26 (milestone approval; 400-preservation is the conservative contract-preserving call, surfaced as the freeze flag)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% lines on `lib/bff-validation.ts` (the new module); route changes covered by existing + new route-handler tests.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_login_valid_passes_through: valid body → parseJsonBody ok → upstream called, cookie set (covered by existing happy-path test staying green; assert via the new validation module unit test that a valid body returns {ok:true,data}).
  - test_parse_rejects_missing_field: parseJsonBody(loginSchema) on {} → {ok:false} 400 ERR_BFF_PAYLOAD_INVALID; assert NO upstream (msw spy never hit) via the login route returning 400.
  - test_parse_rejects_oversized_field: password length 5000 → 400 ERR_BFF_PAYLOAD_INVALID; route makes no upstream call.
  - test_parse_rejects_wrong_type: signupSchema with tenant_name as a number → 400 ERR_BFF_PAYLOAD_INVALID.
  - test_parse_rejects_non_object_body: body is JSON string "hi" or array → 400 ERR_BFF_PAYLOAD_INVALID.
  - test_parse_rejects_invalid_json: req.json() throws → 400 ERR_BFF_PAYLOAD_INVALID (no upstream).
  - test_gw_oversized_body_413: POST /api/gw/... with Content-Length over cap (and with an over-cap body, no header) → 413 ERR_BFF_PAYLOAD_TOO_LARGE; gateway never called.
  - test_gw_small_body_forwards: a normal small POST forwards to the gateway and proxies the response unchanged (breaker/timeout from task-1 intact).
  - test_sanitize_domain: valid "acme.com" → returned; ">253 chars" / "ctrlchar" → null (dropped).
  - test_oidc_invalid_domain_dropped: GET oidc/login?domain=<bad> → upstream URL has NO domain param.
  - test_existing_400_contract_unchanged: the shipped test_bff_login_missing_fields_400 still asserts 400 + ERR_BFF_PAYLOAD_INVALID.
</test_plan>

Tests live in: `./tests/` · `apps/dashboard/tests-bff/bff-validation.test.ts` `apps/dashboard/tests-bff/gw-body-guard.test.ts` `apps/dashboard/tests-bff/route-handlers.test.ts` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/lib/bff-validation.ts` `apps/dashboard/app/api/auth/login/route.ts` `apps/dashboard/app/api/auth/signup/route.ts` `apps/dashboard/app/api/auth/oidc/login/route.ts` `apps/dashboard/app/api/gw/[...path]/route.ts` `apps/dashboard/tests-bff/bff-validation.test.ts` `apps/dashboard/tests-bff/gw-body-guard.test.ts` `apps/dashboard/tests-bff/route-handlers.test.ts`
Strategy (ordered batches): 1. lib/bff-validation.ts (schemas + parseJsonBody + sanitizeDomain). 2. wire login + signup to parseJsonBody (preserve 400). 3. gw proxy body-size guard (413). 4. oidc/login sanitizeDomain. 5. green + coverage.
Safety rule (feature-specific): validation/size-guard run BEFORE any upstream fetch (fail-closed); the validator never throws past its boundary; gw guard reads body once (no double-consume of the request stream).
Code lives in: `apps/dashboard/lib/` + `apps/dashboard/app/api/`
Constraints: do NOT change any test or the contract; allow-list packages only (zod already present, NO new dep); proxy.ts untouched; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 536 green (65 files); +18 new (bff-validation 12, gw-body-guard 3, +3 hardening)
- [x] coverage did not decrease — new lib/bff-validation.ts fully branch-exercised by unit tests (valid/missing/oversized/wrong-type/non-object/invalid-json + domain valid/over-long/control/C1/non-ASCII/null); route + gw changes covered by integration tests
- [x] no test or contract was altered during build — shipped `test_bff_login_missing_fields_400` UNCHANGED + green; the existing oidc smuggle test UNCHANGED + green (the refactor is additive)
- [x] the green was EARNED — adversarial security refute-read (security-expert subagent, agent a23664b26ea86d2dc) = VERDICT EARNED across all 6 probes: parseJsonBody runs before every upstream call; size guard authoritative via TextEncoder byte-length (Content-Length lie/absence can't bypass); domain encode-not-smuggle guarantee intact; safeParse never throws (fail-closed); tests assert upstream-NOT-called on reject (not vacuous); proxy.ts untouched. One finding (C1 control gap) FIXED faithful to the "no control chars" contract + locked by a new test
- [x] concurrency / timing safe — validator + size guard are pure/synchronous, run before the bounded upstream fetch; body read exactly once (no double-consume)
- [x] no exposed secrets, injection openings, or unexpected dependencies — security refute-read cleared SSRF/smuggle/injection; ZERO new deps (zod already present); JWT never in body (unchanged)
- [x] layering & dependencies follow CONVENTIONS.md — shared validator in lib/, consumed by routes; proxy.ts untouched; gateway remains the auth authority
- [x] a person reviewed — Tin approved the freeze (400-preservation surfaced); auto-gate under autonomy:auto on complete evidence; security escalation cleared (no HARD-STOP). Owner: Tin Dang

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] A malformed/oversized/wrong-typed login or signup body returns 400 ERR_BFF_PAYLOAD_INVALID and NO upstream call — confirmed by `test_login_oversized_no_upstream_400` / `test_signup_wrong_type_no_upstream_400` asserting `upstreamCalled === false`
- [x] A gw body over the cap returns 413 ERR_BFF_PAYLOAD_TOO_LARGE before forwarding — confirmed by `test_gw_oversized_body_413_no_upstream` + PATCH variant (gateway handler never flips its spy); a small body forwards unchanged
- [x] A hostile/over-long/control-char oidc `domain` is dropped, never smuggled — confirmed by the new drop test + the UNCHANGED shipped encode-not-smuggle test both green
- [x] No regression — full 536-green suite, tsc 0, eslint 0, next build exit 0

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING — `parseJsonBody`/`loginSchema`/`signupSchema` consumed by login+signup routes; `sanitizeDomain` by oidc/login; `maxBodyBytes` by the gw guard. All referenced (tsc + eslint clean, no unused).
- [x] DEAD-CODE — the old ad-hoc typeof blocks were DELETED (replaced, not orphaned); no unused exports.
- [x] SEMANTIC — re-read the security refute-read verdict in full: EARNED, 3 non-blocking deltas recorded below.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (freeze) + security-expert refute-read (EARNED) · auto-resolved under autonomy:auto · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of 400 ERR_BFF_PAYLOAD_INVALID + 413 ERR_BFF_PAYLOAD_TOO_LARGE (a spike = a real client bug OR an abuse attempt); dropped-domain rate on oidc/login.

### Spec delta
- [SPEC · open] DELETE-with-body bypasses the 413 size cap — the gw guard excludes DELETE (body silently nulled, not forwarded). No upstream DoS today, but the cap is inconsistently applied; decide whether DELETE bodies are forwarded+capped or explicitly rejected (evidence: refute-read finding #1, gw/[...path]/route.ts mutating-method exclusion list).
- [SPEC · open] Reconcile the milestone exit-criterion #2 wording "422" → "4xx fail-closed (400 ERR_BFF_PAYLOAD_INVALID)" to match the preserved shipped contract (evidence: §3 freeze flag; shipped test pins 400). Apply at milestone close.
- [SPEC · seeded] Centralize the size guard into the shared validator (parseJsonBody could take a maxBytes) so future BFF routes inherit it instead of re-implementing (evidence: only the gw proxy has it today).

### Competency deltas
- [TDD · open] Special/control characters in a test STRING literal get normalized away by the editor (U+0085/NBSP silently became plain ASCII → a green-looking but vacuous assert); build them with `String.fromCharCode(0x85)` so the bytes survive (evidence: test_sanitize_domain_c1_and_non_ascii failed-then-fixed).
- [ADD · open] A security refute-read pays off on input-validation tasks even when tests are green — it found a contract-fidelity gap (C0-only vs the "no control chars" contract includes C1) that the happy tests missed; reserve it for logic/security tasks, skip it for pure static config (evidence: v50 task-2 skipped it, task-3 caught a real gap).
- [SDD · open] When a task's drafted status code (422) collides with a shipped test (400), PRESERVE the shipped contract and reconcile the spec wording — never weaken the test for a cosmetic code (evidence: 400-preservation freeze flag honored).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
