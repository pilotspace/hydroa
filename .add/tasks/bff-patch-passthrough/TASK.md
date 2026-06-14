# TASK: BFF PATCH passthrough fix

slug: bff-patch-passthrough · created: 2026-06-14 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures): a discovered v13 LATENT BUG fix — the BFF authenticated proxy catch-all is missing its PATCH verb. Verified:
- `apps/dashboard/app/api/gw/[...path]/route.ts` defines `proxyRequest(req, context)` (forwards the cookie JWT as `Authorization: Bearer`, reconstructs the path via `pathSegments.join("/")`, forwards the body for every method EXCEPT GET/HEAD/DELETE — line 72) and exports thin wrappers `GET` (`:112`), `POST` (`:116`), `PUT` (`:120`), `DELETE` (`:124`). There is NO `export async function PATCH` → a client `PATCH /api/gw/*` hits the Next route with no matching handler → 405 in production.
- The bug bites SHIPPED code: `apps/dashboard/components/keys/KeyGovernanceEditor.tsx:159` calls `bffPatch("/admin/keys/{id}", ...)` (governance updates); `lib/bff-client.ts:125` `bffPatch` issues a real `PATCH /api/gw{path}`. v13's govern suite passed only because msw intercepts the CLIENT fetch directly (the Next route never runs in jsdom), masking the 405. v15 team-budget (`PATCH /admin/teams/{id}`) needs the same verb.
- The fix is a pure additive wrapper: `export async function PATCH(req, context) { return proxyRequest(req, context); }` — identical to the PUT wrapper (`:120-122`). `proxyRequest` ALREADY forwards the body for PATCH (the `method !== GET/HEAD/DELETE` branch at `:72`), so NO handler-body change — only the export.
- Test seam (`tests-bff/route-handlers.test.ts`): handlers imported directly + called as `handler(req, { params: { path: [...] } })`; `requestWithCookie(url, method, jwt)` (`:55`) builds a NextRequest with the `ai_proxy_session` cookie; `server.use(http.<verb>("http://gateway.test/...", ...))` stubs upstream; `VALID_SESSION_JWT` from `mocks/handlers`. The GET tests (`:318,356`) are the exact mirror.

Context (working folder): the v15 MILESTONE.md "bff-patch-passthrough" task (prerequisite for teams-governance-ui's team-budget PATCH; also fixes the v13 key-governance latent 405). Discovered during teams grounding 2026-06-14.

Honors (patterns / conventions): CONVENTIONS.md (data-identical BFF seam — the proxy is a transparent passthrough; adding a verb changes NO data contract); the existing route.ts wrapper pattern (one thin `export async function <VERB>` delegating to `proxyRequest`); the route-handler test idiom (direct handler call + msw gateway stub).

Anchors the contract cites: the NEW `export async function PATCH` in `app/api/gw/[...path]/route.ts` (delegates to the EXISTING `proxyRequest`) · the existing `proxyRequest` body-forwarding branch · the `tests-bff/patch-passthrough.test.ts` route-handler test.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: BFF PATCH passthrough — add the missing `PATCH` verb to the authenticated proxy catch-all so client `bffPatch` calls reach the gateway (fixes the v13 key-governance 405; enables v15 team-budget). Transparent passthrough, no data-contract change.
Framings weighed: Add a thin `export async function PATCH` delegating to the existing `proxyRequest` (chosen — mirrors PUT exactly; `proxyRequest` already body-forwards PATCH; smallest correct fix) · Change `bffPatch` to use PUT instead (rejected — would diverge from the gateway's actual PATCH routes for keys/teams; the gateway expects PATCH) · Add the polyfills/middleware to translate verbs (rejected — over-engineering a one-line export).
Must:
<must>
  - The catch-all `app/api/gw/[...path]/route.ts` exports `PATCH` which delegates to `proxyRequest`, so a `PATCH /api/gw/<path>` with a valid `ai_proxy_session` cookie forwards `Authorization: Bearer <jwt>` + the request body to `PATCH <gateway>/<path>` and proxies the upstream response/status verbatim.
  - A `PATCH /api/gw/<path>` with NO session cookie returns `401 ERR_AUTH_NO_SESSION` and makes NO upstream call (same guard as the other verbs, inherited from `proxyRequest`).
  - The JWT never appears in the proxied response body (inherited passthrough guarantee).
  - No change to `proxyRequest`, to any other verb wrapper, to `bffPatch`, or to any data contract; the full suite stays green; coverage ≥ 80%.
</must>
Reject:
<reject>
  - A `PATCH /api/gw/*` with no cookie reaching the upstream gateway (auth bypass) -> "unauth_passthrough"
  - The PATCH wrapper NOT forwarding the request body (a no-op governance/budget update) -> "body_dropped"
  - Editing `proxyRequest` semantics or any other verb wrapper while adding PATCH -> "scope_creep"
</reject>
After:
<after>
  - `PATCH` is a first-class verb on the BFF proxy; `bffPatch("/admin/keys/{id}")` and `bffPatch("/admin/teams/{id}")` round-trip to the gateway in production; the route-handler suite proves bearer-forward + body-forward + no-cookie-401; full suite green, coverage ≥ 80%, lint clean.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ `proxyRequest` already forwards the body for PATCH — lowest confidence because it is the one behavior that makes the export sufficient; CONFIRMED by reading route.ts:72 (`method !== "GET" && method !== "HEAD" && method !== "DELETE"` → PATCH falls into the body-forwarding branch). If wrong: the fix would also need a body-forward tweak, caught by the body-forward test. Cost: trivial.
  - [ ] no existing test asserts PATCH is ABSENT/405 (which this fix would flip) — CONFIRMED: the only PATCH usage is the client `bffPatch` (govern), tested via msw client interception, not via the Next route; no route-handler test exercises PATCH today.
  - [ ] adding a verb export needs no allowlist/config change — CONFIRMED (it is application code, no new dependency).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: PATCH forwards bearer + body to the gateway
  Given a PATCH /api/gw/admin/keys/kid-1 with a valid ai_proxy_session cookie and a JSON body
  When the PATCH handler runs
  Then the upstream gateway receives PATCH /admin/keys/kid-1 with Authorization: Bearer <jwt> and the exact body
  And the upstream status + body are proxied verbatim and the JWT is NOT in the response body

Scenario: PATCH with no cookie is rejected before upstream
  Given a PATCH /api/gw/admin/keys/kid-1 with NO session cookie
  When the PATCH handler runs
  Then it returns 401 { code: "ERR_AUTH_NO_SESSION" }
  And the upstream gateway is NOT called -> else "unauth_passthrough"

Scenario: the body is not dropped
  Given a PATCH with body { monthly_budget_usd: "5.00" }
  When forwarded
  Then the gateway receives exactly { monthly_budget_usd: "5.00" } -> else "body_dropped"
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# ROUTE-HANDLER contract (transparent proxy verb). No data shape is defined here — the
# proxy forwards verbatim. This freezes the addition of the PATCH verb to the catch-all.

app/api/gw/[...path]/route.ts
  export async function PATCH(req: NextRequest, context: RouteContext): Promise<NextResponse>
    -> return proxyRequest(req, context)   // IDENTICAL to the existing PUT wrapper (:120-122)

GUARANTEE (inherited from proxyRequest, unchanged):
  - cookie present  -> forwards `Authorization: Bearer <jwt>` + the JSON body to PATCH <gateway>/<path>;
                       proxies upstream status + body verbatim; JWT never in the response body
  - cookie absent   -> 401 { code: "ERR_AUTH_NO_SESSION" }, NO upstream call
  - upstream 401    -> clears cookie, 401 { code: "ERR_AUTH_SESSION_EXPIRED" }

Reject codes: unauth_passthrough · body_dropped · scope_creep
Schema: NONE — transparent proxy; no DB/route/field/data change; no other verb or proxyRequest edit.
```

Status: FROZEN @ v1 — approved by Tin (delegated auto mode, v15 prerequisite bug-fix; additive verb on a transparent proxy)

**Least-sure flag surfaced at freeze:** `[contract]` — that a bare `export async function PATCH = proxyRequest` is SUFFICIENT (no body-forward tweak). *Why it's the riskiest call:* the whole fix rests on `proxyRequest` already body-forwarding PATCH; if its method guard excluded PATCH, the export alone would silently drop the body. *Cost if wrong:* a one-line guard tweak in `proxyRequest`, caught by the §4 body-forward test — no contract change. Mitigation: read confirms route.ts:72 forwards body for all non-GET/HEAD/DELETE methods (PATCH included), and the §4 test asserts the gateway received the exact body.
<!-- EXIT: frozen + every spec rejection has a contracted response + the lowest-confidence flag surfaced. -->
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥ 80% line (held). TRUE-RED reason: `import { PATCH as proxyPatch } from "@/app/api/gw/[...path]/route"` resolves to `undefined` (the export does not exist) → Vite throws "does not provide an export named 'PATCH'" at collect → the whole file is red until Build adds the export.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  In `apps/dashboard/tests-bff/patch-passthrough.test.ts` (bff project, route-handler idiom):
  - test_bff_proxy_patch_forwards_bearer_and_body: server.use http.patch("http://gateway.test/admin/keys/kid-1", capture Authorization + request.json()) → call proxyPatch(requestWithCookie+body, { params: { path: ["admin","keys","kid-1"] } }) → assert upstream got `Bearer <jwt>` + the exact body {monthly_budget_usd:"5.00"} + response 200 proxied + JWT NOT in body
  - test_bff_proxy_patch_absent_cookie_401: call proxyPatch(NextRequest no cookie) → assert 401 code ERR_AUTH_NO_SESSION + upstream NOT called
</test_plan>

Tests live in: `patch-passthrough.test.ts` · MUST run red (PATCH export absent) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/app/api/gw/` `apps/dashboard/tests-bff/` `apps/dashboard/.next/` `apps/dashboard/coverage/` `apps/dashboard/tsconfig.tsbuildinfo` `.add/tasks/bff-patch-passthrough/`
<!-- ONE additive export in app/api/gw/[...path]/route.ts + the NEW tests-bff/patch-passthrough.test.ts.
     NO change to proxyRequest, to any other verb wrapper, to bff-client.ts, to handlers.ts, or to any
     other file — touching those is scope_creep. .next/coverage/tsbuildinfo are gitignored artifacts. -->
Strategy (ordered batches): 1. RED test `tests-bff/patch-passthrough.test.ts` (imports the absent PATCH export). 2. add `export async function PATCH(req, context) { return proxyRequest(req, context); }` to route.ts. 3. run the route-handler suite green, then full-suite + coverage + lint.
Safety rule (feature-specific): transparent passthrough only — delegate to the EXISTING `proxyRequest` (its auth + body-forward guards are inherited, not re-implemented); change nothing else.
Code lives in: `apps/dashboard/app/api/gw/[...path]/route.ts`
Constraints: do NOT change any test or the contract or proxyRequest; allow-list packages only (none added); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 144/144 (142 prior + 2 new in `tests-bff/patch-passthrough.test.ts`); PATCH suite 2/2 green.
- [x] coverage did not decrease — 91.65% line (≥ 80% gate held, exit=0; unchanged — the one-line wrapper is fully covered by the 2 new tests).
- [x] no test or contract was altered during build — the §3 contract is UNCHANGED; the only new test file is `patch-passthrough.test.ts`; `proxyRequest` and the other verb wrappers are byte-for-byte unchanged (only the additive PATCH export).
- [x] the green was EARNED, not gamed — careful MANUAL review (a one-line isomorphic fix, not large-change territory): the PATCH wrapper is identical to the PUT wrapper, and the tests are DISCRIMINATING — `test_..._forwards_bearer_and_body` asserts the upstream gateway actually received `Bearer <jwt>` + the EXACT body (`toEqual {monthly_budget_usd:"5.00"}`) + a proxied 200 + no JWT in the response (would fail if the body were dropped or the wrapper missing); `test_..._absent_cookie_401` asserts the no-cookie guard returns ERR_AUTH_NO_SESSION AND `upstreamCalled === false` (would fail on an auth bypass). Neither passes against a no-op/stub.
- [x] concurrency / timing of the risky operation is safe — N/A; a stateless transparent proxy delegating to the existing `proxyRequest` (no shared state, no new IO path).
- [x] no exposed secrets, injection openings, or unexpected dependencies — the JWT is forwarded only as the upstream `Authorization` header (never in the body — asserted) and never logged; no cookie → no upstream call (no auth-bypass); ZERO new dependency (no package.json/allowlist change).
- [x] layering & dependencies follow CONVENTIONS.md — the BFF proxy stays a transparent passthrough (no data-contract change); the fix mirrors the existing verb-wrapper pattern exactly.
- [x] a person reviewed and approved the change — Tin (delegated auto mode) + manual discriminating-test review.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `PATCH` is exported from `app/api/gw/[...path]/route.ts` and imported/exercised by `patch-passthrough.test.ts`; in production it is the handler Next.js invokes for `PATCH /api/gw/*` (the consumers are `bffPatch` in KeyGovernanceEditor + the upcoming team-budget).
- [x] DEAD-CODE (code) — no orphaned symbol; the export is a live Next.js route handler (framework-invoked) and is test-exercised.
- [x] SEMANTIC (prose / non-code) — read in full: `route.ts` (`proxyRequest` body-forward branch at the `method !== GET/HEAD/DELETE` guard confirms PATCH bodies forward) + the frozen §3. Confirmed the export alone is sufficient — no `proxyRequest` change needed.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin (delegated auto) + manual discriminating-test review · date: 2026-06-14

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
