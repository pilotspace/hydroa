# TASK: End-to-end agent OAuth harness: stubbed + live double-pass

slug: agent-oauth-harness-e2e · created: 2026-06-25 · stage: production · risk: high
autonomy: conservative   <!-- risk:high — closes the headless-agent loop by widening the EDGE authz path (/internal/authz, the ext_authz gate for /v1 in production) to accept agent tokens; a billable-path credential change + the live double-pass. Security HARD-STOP at verify; human owns the gate. -->
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
  THE GAP (key finding): in PRODUCTION behind Envoy, /v1 auth happens at the EDGE via ext_authz →
  `POST /internal/authz/{path}` (`keys/api/router.py:441,471`), which resolves the credential with
  `AuthzUseCase.execute(raw_key)` (sk- ONLY, via `keys/api/deps.py:106 get_authz_use_case`). Task-5 wired the
  composite into the IN-PROCESS /v1 deps but NOT this edge gate — so an agent token is rejected at ext_authz
  BEFORE /v1 is reached. The live double-pass exists to catch exactly this.
  - EDIT `apps/gateway/src/gateway/keys/api/router.py:441-491` (`authz` + `authz_subpath`) — authenticate via the
    task-5 `CompositeKeyAuthenticator` (grammar dispatch: sk- → AuthzUseCase as today; else → resolve_access_token →
    AuthzResult(key_id=token_id, monthly_budget_usd=cap)) instead of bare `AuthzUseCase.execute`. The composite
    already raises `InvalidApiKeyError` → existing `AUTH_KEY_INVALID_AUTHZ` 401 (byte-identical). x-tenant-id /
    x-key-id response headers then carry the agent token's (tenant_id, token_id) for Envoy upstream forwarding.
  - EDIT `apps/gateway/src/gateway/keys/api/deps.py` — NEW `get_authz_authenticator` building a
    `CompositeKeyAuthenticator(api_key_authenticator=SqlAlchemyKeyAuthenticator(AuthzUseCase(repo,_hasher)),
    agent_token_repo=SqlAlchemyAgentOAuthRepository(session), hasher=_hasher, settings=app.state.settings)`
    (mirror proxy/api/deps.py wiring). authz handlers depend on it.
  - NEW `apps/gateway/tests/agent_oauth_e2e/` — STUBBED end-to-end (in-process, httpx ASGITransport): signup →
    POST /oauth/device/authorize → (JWT) /oauth/device/approve → POST /oauth/token (mint) → use the agent token on
    /v1/chat/completions (stub upstream) → assert billed (usage_events tenant_id, key_id=token_id). PLUS an authz-path
    test: POST /internal/authz/v1/chat/completions with the agent token → 200 + x-tenant-id / x-key-id headers; a bad
    token → 401 AUTH_KEY_INVALID_AUTHZ.
  - NEW `infra/docker-compose.e2e.v39.yml` + `scripts/live_v39_verify.py` — the LIVE double-pass: bring up the full
    Postgres+Redis+gateway+Envoy(TLS) stack with a STUB upstream provider; the script runs the WHOLE agent journey
    through Envoy :8443 (TLS) end-to-end and is run TWICE (foundation live-verify rule). Mirror
    `scripts/live_v25_verify.py` + `docker-compose.e2e.v25.yml` + `scripts/v25_bearer_stub.py`.
  - REUSE (FROZEN, call only): all v39 t1–t5 surfaces (device/authorize, device/approve, /oauth/token,
    CompositeKeyAuthenticator) + `AuthzUseCase` + `SqlAlchemyKeyAuthenticator` + `SqlAlchemyAgentOAuthRepository`.
Context (working folder):
  - Envoy routing (`infra/envoy/envoy.yaml`): /oauth/* falls under the catch-all `/` route → jwt_authn has NO
    `requires` there (passes through; the gateway validates the approve-JWT itself) AND ext_authz is DISABLED on
    non-/v1 routes → /oauth/authorize|approve|token reach the gateway directly through the edge. /v1/* → ext_authz →
    /internal/authz (the gate this task widens). /internal/* is a 403 direct-response from outside (never reachable).
  - Edge auth model (memory e2e-edge-stack-ops): JWT for /admin, ext_authz for /v1, /internal blocked externally.
  - The live stack uses a stub upstream so the pass needs NO real provider key / external network (deterministic).
Honors (patterns / conventions):
  - run.md fail-closed + anti-enumeration: the edge authz path keeps the byte-identical 401 for both credential classes.
  - Foundation rule (memory v5/v6): live-verify is a DOUBLE pass — run the live script twice, both GREEN, 0 src change between.
  - PROJECT.md tenant-scoping: x-tenant-id/x-key-id from the verified credential only.
Anchors the contract cites:
  `authz` / `authz_subpath` (/internal/authz) · `get_authz_authenticator` · `CompositeKeyAuthenticator` ·
  `AuthzUseCase` · `AUTH_KEY_INVALID_AUTHZ` · x-tenant-id/x-key-id headers · the v39 t1–t5 endpoints ·
  `docker-compose.e2e.v39.yml` · `scripts/live_v39_verify.py`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Close the headless-agent loop end-to-end — widen the EDGE authz gate (/internal/authz) to accept agent
  tokens (so /v1 works through Envoy, not just in-process), then prove the WHOLE journey both stubbed (red/green,
  in-process) and LIVE (double-pass through Envoy/TLS): signup → device authorize → human approve → poll/mint →
  the coding agent makes a billable /v1 request with its minted token.
Framings weighed: reuse the task-5 CompositeKeyAuthenticator at the edge authz path (chosen — one credential-logic
  source, byte-identical 401, no duplicated dispatch) · re-implement agent-token resolution inside the authz handler
  (rejected — duplicates the seam) · skip the live pass and trust the stubbed e2e (rejected — the edge gap proves
  stubbed-only misses production-path defects; live double-pass is a foundation rule).
Must:
<must>
  - The edge authz path `POST /internal/authz/{path}` authenticates via the CompositeKeyAuthenticator: an `sk-` key
    resolves EXACTLY as today; a valid agent token resolves to AuthzResult(tenant_id, key_id=token_id, budget cap),
    and the 200 response sets x-tenant-id + x-key-id for Envoy upstream forwarding. ZERO change for sk- keys.
  - Any invalid/expired/revoked credential at /internal/authz → 401 AUTH_KEY_INVALID_AUTHZ, byte-identical for both
    credential classes (anti-enumeration preserved; raw token never logged).
  - STUBBED e2e (in-process): the full flow completes — a freshly signed-up tenant's user approves a device code, the
    agent mints a token and makes a /v1/chat/completions call that returns 200 and writes a usage_events row billed to
    (tenant_id, key_id=token_id). The same agent token authenticates at /internal/authz (the edge simulation).
  - LIVE double-pass: the full stack (Postgres+Redis+gateway+Envoy/TLS + stub upstream) runs the entire journey
    through Envoy :8443; `scripts/live_v39_verify.py` exits GREEN on TWO consecutive runs with no source change between.
  - No regression: existing /internal/authz API-key behavior + all prior suites stay green.
</must>
Reject:
<reject>
  - invalid / expired / revoked / missing credential at /internal/authz   -> 401 AUTH_KEY_INVALID_AUTHZ (unchanged)
  - an agent token that is over its monthly budget cap, on /v1 via edge    -> 402 ERR_BUDGET_EXCEEDED (task-5 guard)
  - the live script failing on EITHER of the two passes                    -> task NOT done (live-verify is double-pass)
</reject>
After:
<after>
  - A minted agent token authenticates at BOTH the in-process /v1 handler (task-5) AND the edge /internal/authz gate
    (this task) — the production Envoy path works end-to-end.
  - The stubbed e2e test asserts the billed usage_events row (tenant_id, key_id=token_id) from the full journey.
  - The live script is GREEN ×2; the milestone goal (a coding agent self-authenticates headlessly + makes a billable
    request) is demonstrably met through the real edge.
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ the LIVE Docker/Envoy double-pass runs cleanly in this environment — lowest confidence because live stacks have
    hit env issues before (ports, TLS certs, stub wiring, compose). If it can't run here: deliver the stubbed e2e +
    the compose/script artifacts GREEN, and hand the live double-pass to Tin to run (record as residue), never fake it.
  - [x] /oauth/* + /internal/authz are reachable/!reachable through Envoy as required — confirmed (envoy.yaml routes:
    /oauth under catch-all, ext_authz off; /internal external = 403; /v1 → ext_authz → /internal/authz).
  - [x] the composite at the edge needs app.state.settings for the budget cap — confirmed (mirror proxy deps wiring).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: full headless-agent journey bills the tenant (stubbed, in-process)
  Given a freshly signed-up tenant T with user U (JWT)
  When the agent POSTs /oauth/device/authorize, U approves the user_code, the agent polls /oauth/token and mints
    an access token, then calls /v1/chat/completions with Authorization: Bearer <agent_token>
  Then the chat call returns 200 (stub upstream)
  And a usage_events row exists with tenant_id=T and key_id=the agent token's id

Scenario: edge authz accepts the agent token
  Given a live minted agent token bound to tenant T
  When ext_authz calls POST /internal/authz/v1/chat/completions with Authorization: Bearer <agent_token>
  Then the response is 200 with x-tenant-id=T and x-key-id=token_id headers set

Scenario: edge authz still accepts an existing API key (regression)
  Given a valid sk- API key
  When ext_authz calls POST /internal/authz/... with it
  Then the response is 200 with x-tenant-id + x-key-id (the api-key id) — byte-identical to before

Scenario: edge authz rejects a bad/expired/revoked credential fail-closed
  Given an unknown / expired / revoked token (agent or sk-)
  When ext_authz calls /internal/authz with it
  Then the response is 401 AUTH_KEY_INVALID_AUTHZ (identical body for all failure modes)
  And no x-tenant-id / x-key-id header is set

Scenario: over-budget agent token blocked at /v1
  Given an agent token whose monthly spend >= the cap
  When the agent calls /v1/chat/completions
  Then the response is 402 ERR_BUDGET_EXCEEDED
  And no upstream call is made

Scenario: live double-pass through Envoy/TLS
  Given the full e2e stack (Postgres+Redis+gateway+Envoy TLS + stub upstream) is up
  When scripts/live_v39_verify.py runs the whole journey through Envoy :8443 TWICE
  Then both runs exit GREEN with no source change between them
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
SEAM widen (the only production code change): /internal/authz now authenticates via CompositeKeyAuthenticator.

  POST /internal/authz  (+ /internal/authz/{path})   headers: Authorization: Bearer <cred> | X-Api-Key: <cred>
    200 -> AuthzResponse{ tenant_id, key_id }  + response headers x-tenant-id, x-key-id
           (cred = sk- key → key_id = api-key id; cred = agent token → key_id = token_id, budget cap applied)
    401 -> AUTH_KEY_INVALID_AUTHZ (problem+json, byte-identical for every failure mode; never reachable externally)

  get_authz_authenticator(request, session) -> CompositeKeyAuthenticator   # NEW dep in keys/api/deps.py
  authz / authz_subpath handlers: `result = await authenticator.authenticate(raw_key)` (was AuthzUseCase.execute)

Harness artifacts (NO production behavior — test/ops only):
  tests/agent_oauth_e2e/test_agent_oauth_e2e.py   — stubbed full-journey + edge-authz tests
  infra/docker-compose.e2e.v39.yml                — full stack + stub upstream
  scripts/live_v39_verify.py                      — runs the journey through Envoy :8443; exit 0 = GREEN
  scripts/v39_upstream_stub.py (if needed)        — deterministic stub provider (mirror v25 bearer stub)

Schema: NO migration, NO new table/column. Reads agent_tokens (resolve) + api_keys; the /internal/authz response
  shape (AuthzResponse + x-tenant-id/x-key-id) is UNCHANGED — only which credentials resolve is widened.
```

Status: FROZEN @ v39 — lead-frozen (no new product decision; Tin's budget-cap decision from task-5 carries through).
  Reuses the task-5 composite at the edge; the only production change is the authz authenticator. Security review of
  this billable-path edge change is the VERIFY HARD-STOP for Tin. Change = back to SPECIFY.
Least-sure flag surfaced at freeze:
  ⚠ [contract] the LIVE double-pass may not run in this environment (Docker/Envoy/TLS) — if so, ship the stubbed e2e
    + compose/script artifacts GREEN and hand the live runs to Tin (residue), NEVER fake a live pass. (§1 ⚠)
  ⚠ [test] the stubbed e2e must exercise the REAL /internal/authz handler (not just in-process /v1), else it repeats
    task-5's blind spot — the edge-authz test is the load-bearing assertion here.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% on the edited authz wiring (the e2e flow tests are integration, not coverage-driven)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_full_journey_bills_tenant — signup→authorize→approve→token→/v1 chat (stub upstream) → 200 + usage_events row
    (tenant_id=T, key_id=token_id)
  - test_edge_authz_accepts_agent_token — POST /internal/authz/v1/chat/completions w/ agent token → 200 +
    x-tenant-id=T + x-key-id=token_id headers
  - test_edge_authz_api_key_regression — sk- key at /internal/authz → 200 + x-key-id=api-key-id (unchanged)
  - test_edge_authz_rejects_bad_credential — unknown/expired/revoked → 401 AUTH_KEY_INVALID_AUTHZ; no headers set
  - test_over_budget_blocked_402 — agent token over cap on /v1 → 402 (reuses task-5 budget guard seam)
  - (live) scripts/live_v39_verify.py — full journey through Envoy :8443 TLS; asserted by exit 0 ×2 (not a pytest)
</test_plan>

Tests live in: `apps/gateway/tests/agent_oauth_e2e/` · MUST run red (edge composite/test absent) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/keys/` `apps/gateway/tests/` `infra/` `scripts/`
<!-- `apps/gateway/tests/` broad (t1–t5 precedent). NO migration. NEVER touch pyproject.toml or other global config. -->
Strategy (ordered batches): 1. keys/api/deps.py get_authz_authenticator (build the composite, mirror proxy deps) 2. keys/api/router.py authz+authz_subpath → authenticate via the composite (was AuthzUseCase.execute) 3. stubbed e2e tests (full journey + edge-authz + regression + over-budget) 4. infra/docker-compose.e2e.v39.yml + scripts/live_v39_verify.py (+ stub upstream) 5. run the live double-pass (or hand to Tin if the env can't)
Safety rule (feature-specific): the edge authz change is FAIL-CLOSED + byte-identical 401 (reuse the composite's behavior); sk- path unchanged (regression test guards it); raw token never logged; the response shape (AuthzResponse + x-tenant-id/x-key-id) is unchanged. Live pass must be REAL — never fake exit 0.
Code lives in: `apps/gateway/src/gateway/keys/api/`
Constraints: do NOT change any test or the contract; do NOT modify task-1..5 frozen code (call only); do NOT run git; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — task e2e suite 9 passed; clean full-suite re-run GREEN (see GATE RECORD count). A first
      full run showed 3 failures in UNRELATED modules (semantic_cache ×2, slo_metrics slo_aggregates, "sqlalchemy"
      error) — re-ran ALL 3 in isolation → 3 passed; the known :5433 DB cross-wipe flake from the live e2e Docker
      stack overlapping the suite (memory v30/v35), NOT a regression (authz change cannot touch those modules).
- [x] coverage did not decrease — ≥80% floor held; edited keys/api authz wiring covered by the 9 e2e tests.
- [x] no test or contract was altered during build — §3 FROZEN unchanged; tests only ADDED + ONE strengthened
      (the headline billing assertion, refute NB-1 — made the vacuous key_id==token_id check a real DB-backed assert).
- [x] the green was EARNED — adversarial refute-read (sonnet) VERDICT=UPHELD @0.83; its NB-1 (vacuous billing assert)
      FIXED; it audited the LIVE script and confirmed it genuinely performs the journey + asserts (no fake exit 0);
      probed edge sk- regression (delegates byte-identical), fail-closed (None→401), missing-header (""→agent path→
      401 not 500), enumeration (identical AUTH_KEY_INVALID_AUTHZ). NB-2/NOTE-3/NOTE-4 → §7 deltas.
- [x] concurrency / timing safe — per-request authenticator at the edge; the live double-pass ran the real
      concurrent stack twice with no state bleed (clean teardown).
- [x] no exposed secrets / injection / unexpected deps — raw token never logged at the authz path or composite;
      byte-identical 401; the live stack uses a STUB upstream (no real provider key); no new prod deps.
- [x] layering & dependencies follow CONVENTIONS.md — the edge authz handler depends on the composite (domain
      Protocol impl); only the authenticator widened, response shape (AuthzResponse + headers) unchanged.
- [x] a person reviewed and approved the change — PENDING Tin's security HARD-STOP sign-off (this gate).

### Build expectations — what "correct" looks like
- [x] agent token at /internal/authz/v1/... → 200 + x-tenant-id=T + x-key-id=token_id — test_edge_authz_accepts_agent_token
- [x] sk- key at /internal/authz → 200 + x-key-id=api-key-id, byte-identical — test_edge_authz_api_key_regression
- [x] bad/unknown/revoked/missing credential at edge → 401 AUTH_KEY_INVALID_AUTHZ, no x-* headers — 4 rejection tests
- [x] full journey signup→authorize→approve→token→/v1 chat → 200 + usage_events(tenant_id=T, key_id=token_id from DB)
      — test_full_journey_bills_tenant (strengthened, real DB assert)
- [x] over-budget agent token → 402 — test_over_budget_blocked_402
- [x] LIVE double-pass through Envoy/TLS — scripts/live_v39_verify.py 13/13 GREEN ×2 (run_id 1782400117 + 1782400128),
      no source change between, stack torn down cleanly (the foundation live-verify rule met)

### Deep checks
- [x] WIRING — authz + authz_subpath depend on get_authz_authenticator → CompositeKeyAuthenticator (router.py:445/484);
      grep confirms no other /v1-path uses bare SqlAlchemyKeyAuthenticator; live double-pass proves the real edge path.
- [~] DEAD-CODE — get_authz_use_case (keys/api/deps.py) is now unused (only a stale .pyc references it) → §7 cleanup
      delta (left in place to keep the in-flight full suite authoritative; provably inert — no live caller).
- [x] SEMANTIC — refute-read read contract + the authz change + all 9 tests + the live script in full; UPHELD @0.83.

### GATE RECORD
Outcome: PASS (security HARD-STOP resolved — Tin approved the edge ext_authz widening for /v1)
Rationale: clean full suite 1730 passed, 0 failed, 88.14% (3 earlier failures = confirmed :5433 cross-wipe flake,
  all green in isolation + clean re-run); refute-read UPHELD@0.83, NB-1 vacuous-billing-assert FIXED (real DB
  assert), live script audited as genuine; edge authz fail-closed + byte-identical 401 + sk- regression-safe +
  missing-header→401-not-500; raw token never logged. LIVE double-pass 13/13 ×2 through Envoy/TLS (foundation rule
  met) — proved the production edge path the in-process stubbed tests missed. Build subagent left HEAD unchanged.
  Human security sign-off given. v39 milestone goal MET: a coding agent self-authenticates headlessly + makes a
  billable /v1 request end-to-end.
If RISK-ACCEPTED -> n/a — security gate, never risk-accepted
Reviewed by: Tin · date: 2026-06-25

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): /internal/authz 200-vs-401 rate split by credential class, agent-token /v1
  success rate end-to-end through the edge, live-verify exit status on each deploy (re-run scripts/live_v39_verify.py).

### Spec delta
- [SPEC · open] remove the now-dead `get_authz_use_case` in keys/api/deps.py (superseded by get_authz_authenticator;
  no live caller — only a stale .pyc references it). Trivial cleanup; left in-place to keep the in-flight full-suite
  authoritative for production code (evidence: refute NOTE-3).
- [SPEC · open] tighten live_v39_verify.py A7 — the x-key-id check is conditional on a DB lookup that A5b already
  hard-exits on, but A7 could structurally pass green without the header check; make it unconditional (evidence: refute NB-2).
- [SPEC · open] /oauth/token deliberately omits token_id from the body — the e2e billing invariant now reads it from
  the DB; if an introspection endpoint is added later, expose token_id there (evidence: refute NB-1 fix).
- [SPEC · seeded] the v39 edge gap (task-5 wired in-process /v1 but not /internal/authz) → ALWAYS wire a new
  credential class into BOTH the in-process handler AND the edge ext_authz path; a checklist item for future auth work.

### Competency deltas
- [ADD · confirmed] the LIVE double-pass earned its keep — it forced discovery of the /internal/authz edge gap that
  the in-process stubbed tests (task-5, all green) completely missed; a feature can be "green" yet broken behind the
  real edge. Live-verify is non-negotiable for auth/edge work (evidence: t5 passed but agent token 401'd at ext_authz).
- [TDD · folded] refute-read caught a VACUOUS assertion in the headline e2e test (key_id==token_id skipped because the [folded foundation-version 36]
  guard was always None) — strengthened to a real DB-backed assert. A skipped-but-green assert reads as coverage it
  isn't; adversarial review is the backstop (evidence: refute NB-1).
- [ADD · confirmed] the hard "do NOT run git" block in the build prompt held again (HEAD unchanged) — two consecutive
  delegated builds now clean after the task-4 incident. Keep it standard.
