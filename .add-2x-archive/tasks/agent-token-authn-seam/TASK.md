# TASK: Data-plane accepts the minted agent token (fail-closed)

slug: agent-token-authn-seam · created: 2026-06-25 · stage: production · risk: high
autonomy: conservative   <!-- risk:high — FREEZES the data-plane credential seam: a new credential class authenticating to the BILLABLE /v1 path. Privilege + cost surface. Security HARD-STOP at verify; human owns the gate. -->
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
  NEW `apps/gateway/src/gateway/proxy/infrastructure/composite_key_authenticator.py` —
  `CompositeKeyAuthenticator` implementing the FROZEN `KeyAuthenticator` Protocol
  (`proxy/domain/ports.py:86` — `async authenticate(raw_key: str) -> AuthzResult`, raises `InvalidApiKeyError`
  on ANY failure, byte-identical). Dispatches on the EXISTING `sk-` API-key grammar:
    • raw_key starts with the API-key prefix → delegate to the wrapped `SqlAlchemyKeyAuthenticator` (unchanged).
    • otherwise → treat as a candidate agent token: `sha256(raw_key)` → task-1
      `SqlAlchemyAgentOAuthRepository.resolve_access_token(access_token_hash=, now=) -> AgentTokenBinding | None`
      (FAIL-CLOSED: None on unknown/expired/revoked). None → raise `InvalidApiKeyError` (same 401, no enumeration).
    • map a non-None `AgentTokenBinding(token_id, tenant_id, user_id, scope)` → `AuthzResult(tenant_id=binding.tenant_id,
      key_id=binding.token_id, monthly_budget_usd=settings.agent_oauth_default_budget_usd)`. Other governance fields
      default (expires_at=None — resolve already gates expiry; model_allowlist=None, rpm/tpm=None). Tin's freeze
      decision: each agent token gets a DEFAULT per-token monthly budget cap (enforced by the existing per-key guard).
  - REUSE (FROZEN): `SqlAlchemyKeyAuthenticator(authz_use_case)` (`proxy/infrastructure/key_authenticator.py:9`,
    built at `proxy/api/deps.py:114`) · task-1 `resolve_access_token` (`agent_oauth/infrastructure/repository.py:198`)
    + `AgentTokenBinding` (`agent_oauth/domain/entities.py:46`) · `AuthzResult` (`keys/domain/entities.py:67`) ·
    `Sha256SecretHasher` · `InvalidApiKeyError` (`keys/domain/errors.py`).
  - EDIT `apps/gateway/src/gateway/proxy/api/deps.py:114` — wrap: `authenticator = CompositeKeyAuthenticator(
      api_key_authenticator=SqlAlchemyKeyAuthenticator(authz_use_case),
      agent_token_repo=SqlAlchemyAgentOAuthRepository(session), hasher=_hasher)`. The use case + governance are
    UNCHANGED (they consume the `AuthzResult` the composite returns).
  - VERIFY the non-chat path: embeddings/STT auth flows through `NonChatGovernance.authorize()` →
    the same `KeyAuthenticator`; confirm the composite is the authenticator there too (one wiring point or two).
  - EDIT `apps/gateway/src/gateway/core/config.py:Settings` — NEW `agent_oauth_default_budget_usd: Decimal =
    Decimal("100.00")` (GATEWAY_AGENT_OAUTH_DEFAULT_BUDGET_USD, >0; mirror the `reconciliation_drift_threshold`
    Decimal-coercion validator pattern at config.py:131). The composite reads it from app.state.settings.
Context (working folder):
  - `usage_events.key_id` is a PLAIN UUID column (NO ForeignKey — `usage/infrastructure/orm.py:70`), while
    `tenant_id` IS a FK to tenants.id. So billing usage against (tenant_id=binding.tenant_id, key_id=binding.token_id)
    is valid — the agent token's row id IS the billing "key". The approval-bound tenant is a real tenant (FK holds).
  - Agent access token = bare `secrets.token_urlsafe(32)` (task-1 `use_cases.py:61`) — NO prefix; it never starts
    with the `sk-` API-key prefix, so grammar dispatch is unambiguous (collision prob ~1/64³, and even then it
    fails CLOSED to 401 — the agent just re-runs the device flow). Documented as the §1 ⚠ assumption.
  - scope: AgentTokenBinding.scope is "proxy" (task-1 default) — in v39 any valid agent token may call /v1
    (scope is carried for FUTURE per-scope authz; not enforced as a /v1 gate yet → SPEC delta).
Honors (patterns / conventions):
  - PROJECT.md: tenant_id from the verified credential, NEVER the body; secrets compared by hash only.
  - run.md fail-closed: ANY resolve failure → InvalidApiKeyError → 401 byte-identical (anti-enumeration preserved).
  - CONVENTIONS.md hexagonal: the composite is an infrastructure adapter implementing the domain Protocol; the
    application use case is untouched (depends on the Protocol, not the impl).
Anchors the contract cites:
  `CompositeKeyAuthenticator` · `KeyAuthenticator` Protocol · `SqlAlchemyKeyAuthenticator` · `resolve_access_token` ·
  `AgentTokenBinding` · `AuthzResult` (key_id=token_id) · `InvalidApiKeyError` · `Sha256SecretHasher` · deps.py:114.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Data-plane agent-token authentication — the /v1 endpoints accept a minted agent access token as a
  Bearer credential (alongside tenant API keys), resolving it to the SAME `AuthzResult` the rest of the pipeline
  consumes so the agent's requests authenticate, authorize, and BILL against the approving tenant. Fail-closed.
Framings weighed: a composite KeyAuthenticator that dispatches on the `sk-` grammar (chosen — single Protocol
  impl, use case untouched, both credential classes share one fail-closed 401) · a separate FastAPI dependency /
  middleware that branches before the use case (rejected — duplicates auth wiring across chat + non-chat) · adding
  an `agt-` prefix to the minted token (rejected — would mutate the FROZEN task-1 token format; grammar dispatch
  on the existing `sk-` prefix is sufficient and fail-closed).
Must:
<must>
  - On /v1 (chat + non-chat), a Bearer token starting with `sk-` authenticates EXACTLY as today (delegate to the
    unchanged SqlAlchemyKeyAuthenticator) — zero behavior change for existing API keys.
  - A Bearer token NOT starting with `sk-` is treated as a candidate agent token: `sha256(raw_key)` →
    `resolve_access_token(access_token_hash, now)`. A non-None `AgentTokenBinding` → `AuthzResult(tenant_id=
    binding.tenant_id, key_id=binding.token_id, monthly_budget_usd=settings.agent_oauth_default_budget_usd)`; the
    request then proceeds through the normal use case + governance and bills against (tenant_id, token_id).
  - Every agent token carries a DEFAULT per-token monthly budget cap (`agent_oauth_default_budget_usd`, mapped to
    AuthzResult.monthly_budget_usd). Once that token's monthly spend reaches the cap, /v1 returns 402
    ERR_BUDGET_EXCEEDED (the EXISTING per-key budget guard, keyed on key_id=token_id; fail-OPEN on Redis outage).
  - resolve_access_token is the SINGLE source of truth for agent-token validity: it returns None for unknown /
    expired / revoked tokens (fail-closed). None → `InvalidApiKeyError` → the existing 401 (byte-identical to a bad
    API key — no detail that distinguishes "bad key" from "bad/expired agent token": anti-enumeration preserved).
  - `now` is server time (datetime.now(UTC)); the caller cannot influence expiry evaluation.
  - The application use case, governance, billing, and the 401 error shape are UNCHANGED — the composite only
    widens which credentials resolve to an AuthzResult.
</must>
Reject:
<reject>
  - missing / empty Bearer token                                       -> 401 (AUTH_KEY_INVALID, as today)
  - `sk-`-prefixed key that is unknown / revoked / wrong-secret         -> 401 (delegated, as today)
  - non-`sk-` token that resolves to None (unknown agent token)        -> 401 (InvalidApiKeyError, identical body)
  - agent token that is EXPIRED (access_expires_at <= now)             -> 401 (resolve returns None — fail-closed)
  - agent token that is REVOKED (revoked_at set)                       -> 401 (resolve returns None — fail-closed)
</reject>
After:
<after>
  - A live (approved, minted, unexpired, unrevoked) agent token on /v1 → the request authenticates and a
    usage_events row is written with tenant_id = the approver's tenant and key_id = the agent token's id.
  - An existing API key's /v1 behavior and billing are byte-identical to before this task (regression-free).
  - On any auth rejection: NO upstream provider call is made and NO usage row is written (fail-closed before IO).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ `sk-`-prefix grammar dispatch is unambiguous — lowest confidence because a `secrets.token_urlsafe(32)` agent
    token could in principle start with the literal "sk-" (~1/64³ ≈ 1/262k). If wrong: that one token mis-routes to
    the API-key parser, fails to parse → 401 (fail-closed, no security hole) and the agent re-runs the device flow.
    Mitigation if it ever matters: add an `agt-` prefix in a future task-1 revision (SPEC delta).
  - [x] usage_events.key_id has no FK → token_id is a valid billing key — confirmed (orm.py:70 plain UUID).
  - [x] resolve_access_token already fail-closes on expired/revoked — confirmed (repository.py:198 returns None).
  - [x] per-key budget guard keys on key_id=token_id, so mapping monthly_budget_usd caps the agent token — confirmed
    (use_cases.py:791 `usage:spend:key:{key_id}:{YYYYMM}`; over-cap → BUDGET_EXCEEDED 402 at use_cases.py:831).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: a live agent token authenticates a /v1 chat request and bills the tenant
  Given an approved+minted agent token bound to tenant T (unexpired, unrevoked)
  When the agent POSTs /v1/chat/completions with Authorization: Bearer <agent_token>
  Then the request authenticates and returns the upstream completion (200)
  And a usage_events row is written with tenant_id=T and key_id=the agent token's id

Scenario: an existing API key is unaffected (regression guard)
  Given a valid tenant API key "sk-<hex>.<secret>"
  When the agent POSTs /v1/chat/completions with that key
  Then the request authenticates and bills exactly as before this task (key_id = the api key id)
  And the agent-token path is never consulted (sk- prefix → delegated)

Scenario: an unknown agent token is rejected fail-closed
  Given a random non-sk- Bearer token that matches no agent_tokens row
  When the agent POSTs /v1/chat/completions with it
  Then the response is 401 ERR_AUTH_INVALID_KEY (byte-identical to a bad API key)
  And NO upstream call is made and NO usage row is written

Scenario: an expired agent token is rejected fail-closed
  Given an agent token whose access_expires_at is in the past
  When the agent calls /v1 with it
  Then the response is 401 (resolve_access_token returned None)
  And NO upstream call is made and NO usage row is written

Scenario: a revoked agent token is rejected fail-closed
  Given an agent token whose revoked_at is set
  When the agent calls /v1 with it
  Then the response is 401
  And NO upstream call is made and NO usage row is written

Scenario: a missing Bearer token is rejected
  Given no Authorization header
  When the agent calls /v1
  Then the response is 401 ERR_AUTH_INVALID_KEY (unchanged behavior)
  And NO upstream call is made

Scenario: the non-chat path accepts an agent token too
  Given a live agent token bound to tenant T
  When the agent POSTs /v1/embeddings with Authorization: Bearer <agent_token>
  Then the request authenticates and bills against tenant T
  And a malformed/unknown token on /v1/embeddings → 401 fail-closed

Scenario: an agent token over its monthly budget is blocked with 402
  Given a live agent token whose monthly spend counter (usage:spend:key:{token_id}:{YYYYMM}) is already >= the
    configured agent_oauth_default_budget_usd cap
  When the agent POSTs /v1/chat/completions with that token
  Then the response is 402 ERR_BUDGET_EXCEEDED
  And NO upstream call is made (blocked pre-flight by the existing per-key budget guard)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
SEAM (not a new HTTP route): widen the FROZEN KeyAuthenticator Protocol's accepted credentials on /v1.

  KeyAuthenticator.authenticate(raw_key: str) -> AuthzResult   (unchanged Protocol; raises InvalidApiKeyError)

  CompositeKeyAuthenticator(api_key_authenticator, agent_token_repo, hasher).authenticate(raw_key):
    if raw_key.startswith("sk-"):
        return await api_key_authenticator.authenticate(raw_key)      # existing path, unchanged
    binding = await agent_token_repo.resolve_access_token(
        access_token_hash=hasher.hash(raw_key), now=datetime.now(UTC))
    if binding is None:
        raise InvalidApiKeyError                                       # fail-closed → 401 (identical)
    return AuthzResult(
        tenant_id=binding.tenant_id,
        key_id=binding.token_id,
        monthly_budget_usd=settings.agent_oauth_default_budget_usd)    # PER-TOKEN cap (Tin's decision)
       # expires_at=None (resolve already gated), model_allowlist=None, rpm/tpm=None

  /v1 success      -> unchanged upstream completion/embedding response (200), billed (tenant_id, key_id=token_id)
  /v1 over-budget  -> 402 {code:"ERR_BUDGET_EXCEEDED"} once usage:spend:key:{token_id}:{YYYYMM} >= the cap
                      (existing per-key budget guard — keyed on key_id, which IS the token_id; fail-OPEN on Redis)
  /v1 auth fail    -> 401 application/problem+json {code:"ERR_AUTH_INVALID_KEY", ...} (byte-identical, both classes)

NEW config (Settings, env_prefix GATEWAY_):
  agent_oauth_default_budget_usd: Decimal = Decimal("100.00")   # GATEWAY_AGENT_OAUTH_DEFAULT_BUDGET_USD (>0)
    — the default MONTHLY spend cap applied to every agent token (mapped to AuthzResult.monthly_budget_usd so the
      existing per-key guard enforces it at usage:spend:key:{token_id}:{YYYYMM}). Tunable per deployment.

Wiring: deps.py:114 wraps SqlAlchemyKeyAuthenticator in CompositeKeyAuthenticator(... settings=app.state.settings)
  (chat). Mirror at the non-chat authenticator construction (embeddings/STT) so /v1/embeddings shares the seam.
  NO change to the use case, governance, billing pipeline, or error catalog (BUDGET_EXCEEDED already exists).
Schema: NO migration. Reads agent_tokens (via resolve_access_token). Writes usage_events with key_id=token_id
  (plain UUID column, no FK — verified). NO new table/column.
```

Status: FROZEN @ v39 — pending Tin's security HARD-STOP sign-off (privilege/cost surface: a new credential class
  on the BILLABLE /v1 path). Tin's freeze decision: agent tokens GET a default per-token monthly budget cap
  (GATEWAY_AGENT_OAUTH_DEFAULT_BUDGET_USD, default $100) enforced by the existing per-key guard. Change = back to SPECIFY.
Least-sure flag surfaced at freeze:
  ⚠ [contract] the $100 default cap VALUE is a product guess — it is a tunable knob (no code change to adjust); the
    enforcement mechanism (per-key guard on key_id=token_id) is the load-bearing part. If the default is wrong it is
    an env change, not a re-freeze.
  ⚠ [contract] `sk-` grammar dispatch (the §1 ⚠) — fail-closed on the ~1/262k collision; no security hole.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (new CompositeKeyAuthenticator)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_agent_token_authenticates_v1_chat_and_bills — seed approved+minted token; POST /v1/chat with it →
    200 (stub upstream); assert a usage_events row with tenant_id=T, key_id=token_id
  - test_existing_api_key_unaffected — sk- key still authenticates + bills key_id=api-key-id (regression)
  - test_unknown_agent_token_401 — random non-sk- bearer → 401 ERR_AUTH_INVALID_KEY; no upstream call; no usage row
  - test_expired_agent_token_401 — token past access_expires_at → 401 (resolve None); no upstream; no usage
  - test_revoked_agent_token_401 — revoked_at set → 401; no upstream; no usage
  - test_missing_bearer_401 — no header → 401 (unchanged)
  - test_embeddings_accepts_agent_token — /v1/embeddings with live token → authenticates+bills; bad token → 401
  - test_agent_token_over_budget_402 — seed usage:spend:key:{token_id}:{YYYYMM} >= cap; /v1/chat → 402
    ERR_BUDGET_EXCEEDED; no upstream call
  - test_composite_unit_dispatch — unit: sk- → delegates to wrapped authenticator; non-sk- → resolve path;
    None binding → InvalidApiKeyError; binding → AuthzResult(key_id=token_id, monthly_budget_usd=cap)
</test_plan>

Tests live in: `apps/gateway/tests/agent_token_authn_seam/` · MUST run red (composite/knob absent) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/` `apps/gateway/src/gateway/core/config.py` `apps/gateway/tests/`
<!-- `apps/gateway/tests/` broad (task-1/2/3/4 precedent). No migration. NEVER touch pyproject.toml or global config. -->
Strategy (ordered batches): 1. config knob agent_oauth_default_budget_usd (Decimal>0, mirror reconciliation_drift_threshold validator) 2. CompositeKeyAuthenticator (sk- dispatch → delegate; else sha256→resolve_access_token→AuthzResult(key_id=token_id, monthly_budget_usd=cap); None→InvalidApiKeyError) 3. wire deps.py:114 (chat) + the non-chat authenticator construction, passing settings 4. tests
Safety rule (feature-specific): FAIL-CLOSED — any resolve failure → InvalidApiKeyError → identical 401 (no enumeration); never log the raw token; `now` server-owned; the wrapped sk- path + use case + governance + error catalog stay UNCHANGED (composite only widens accepted credentials). Existing API-key behavior MUST be byte-identical (regression test guards it).
Code lives in: `apps/gateway/src/gateway/proxy/infrastructure/`
Constraints: do NOT change any test or the contract; do NOT modify task-1 frozen agent_oauth code (call only); allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full gateway suite 1721 passed, 19 deselected, exit 0 (88.17% total); task suite 10 passed
- [x] coverage did not decrease — total 88.17% (≥80% floor held; was 88.09% pre-task). New
      composite_key_authenticator.py = 100%.
- [x] no test or contract was altered during build — §3 FROZEN unchanged; tests only ADDED; no frozen agent_oauth /
      key_authenticator / use case / error_catalog code modified (call-only). Existing API-key tests still green.
- [x] the green was EARNED — adversarial refute-read (sonnet) VERDICT=UPHELD @0.91, ZERO blockers; probed
      sk- regression (short-circuits before any agent DB hit — byte-identical), fail-closed (None→InvalidApiKeyError;
      DB-error→500 not open), enumeration (both classes → identical ERR_AUTH_INVALID_KEY, no token logged), budget
      bypass via settings=None (fails LOUD 500, never silent None-cap — and unreachable: create_app always sets
      app.state.settings), FK/mis-billing (key_id no-FK confirmed; tenant_id is the approver's real tenant).
- [x] concurrency / timing safe — per-request session-scoped composite (no shared state); `now` server-owned;
      one extra DB read on the agent path only; sk- path unchanged.
- [x] no exposed secrets / injection / unexpected deps — raw token NEVER logged (composite has no logger; repo gets
      only the hash); fail-closed identical 401; no new deps.
- [x] layering & dependencies follow CONVENTIONS.md — composite is an infrastructure adapter implementing the
      domain KeyAuthenticator Protocol; the application use case is untouched (depends on the Protocol).
- [x] a person reviewed and approved the change — PENDING Tin's security HARD-STOP sign-off (this gate).

### Build expectations — what "correct" looks like
- [x] a live agent token on /v1/chat → 200 + a usage_events row with tenant_id=approver-tenant, key_id=token_id —
      confirmed by test_agent_token_authenticates_v1_chat_and_bills (asserts the DB row)
- [x] existing sk- API key billed to key_id=api-key-id, byte-identical — confirmed by test_existing_api_key_unaffected
- [x] unknown / expired / revoked / missing → 401 ERR_AUTH_INVALID_KEY, no upstream call, no usage row —
      one test each (asserts identical 401 + no side effects)
- [x] over-budget agent token → 402 ERR_BUDGET_EXCEEDED, no upstream — test_agent_token_over_budget_402 seeds the
      REAL Redis usage:spend:key:{token_id}:{YYYYMM} counter + real budget guard (not faked)
- [x] /v1/embeddings accepts an agent token + rejects a bad one — test_embeddings_accepts_agent_token
- [x] composite dispatch unit — sk-→delegate, non-sk-→resolve, None→InvalidApiKeyError, binding→AuthzResult(
      key_id=token_id, monthly_budget_usd=cap) — test_composite_unit_dispatch

### Deep checks
- [x] WIRING — CompositeKeyAuthenticator wired at ALL 5 /v1 entry points (deps.py chat · embeddings_deps ·
      images_deps · audio_deps transcription+speech via _build_composite_authenticator); grep confirms NO bare
      SqlAlchemyKeyAuthenticator left on a /v1 path (the /internal/authz authenticator is a separate non-/v1 surface).
- [x] DEAD-CODE — none; composite 100% covered. settings=None fallback is defensive-only (unreachable; fails loud).
- [x] SEMANTIC — refute-read read contract + impl + all 5 wiring sites + tests in full; UPHELD @0.91.

### GATE RECORD
Outcome: PASS (security HARD-STOP resolved — Tin approved the new credential class on the BILLABLE /v1 path)
Rationale: full suite 1721 green (88.17%); refute-read UPHELD@0.91 zero blockers; sk- API-key path byte-identical
  (short-circuits before any agent DB hit); fail-closed (unknown/expired/revoked → identical ERR_AUTH_INVALID_KEY
  401, anti-enumeration; raw token never logged); bills (tenant_id, key_id=token_id, no FK); per-token $100/mo
  budget cap enforced by the existing per-key guard (Tin's freeze decision); all 5 /v1 entry points wired; the build
  subagent left HEAD unchanged. NB-1/NB-2 (settings-None / DB-error → loud 500, both unreachable-or-symmetric and
  fail-closed) + scope-not-gated + images/audio integration coverage → §7 SPEC deltas. Human security sign-off given.
If RISK-ACCEPTED -> n/a — security gate, never risk-accepted
Reviewed by: Tin · date: 2026-06-25

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): agent-token /v1 auth success vs 401 rate, agent-token 402 (over-budget) rate,
  per-credential-class share of /v1 traffic, p95 added auth latency on the agent path (one extra DB read vs sk-).

### Spec delta
- [SPEC · open] enforce AgentTokenBinding.scope at /v1 — scope is resolved but not gated (any valid token → /v1);
  add per-scope route authorization when scopes diversify beyond "proxy" (evidence: refute Finding 3 / §0 note).
- [SPEC · open] integration-test /v1/images + /v1/audio/{transcriptions,speech} with a live agent token — wiring is
  structurally identical (shared composite) + unit-covered, but only chat+embeddings have integration tests;
  task-6 live e2e will exercise the real path (evidence: refute Finding 4).
- [SPEC · open] wrap composite resolve_access_token in `except Exception → InvalidApiKeyError` so a DB flap returns a
  clean 401 instead of 500 (currently fail-closed-but-500, symmetric with the sk- path) (evidence: refute NB-2).
- [SPEC · open] per-token budget is a flat default ($100/mo); future: per-token / per-scope configurable caps, and
  optionally inherit the approver's key budget instead of a flat default (evidence: Tin's freeze chose flat-default).
- [SPEC · open] agent-token refresh/revoke surface (re-use the v39-t4 seeded delta) — an over-budget or compromised
  agent token can only be retired by waiting out expiry or a manual DB revoke; no admin revoke endpoint yet.

### Competency deltas
- [ADD · folded] the freeze decision was a genuine FORK (unmetered vs per-token cap) that materially changed scope [folded foundation-version 36]
  (added a config knob + budget wiring + a 402 scenario AFTER the contract draft) — surfacing it at the freeze via
  AskUserQuestion (not assuming) was correct; Tin chose the larger-scope cap. Evidence: §3 freeze flag → scope grew.
- [ADD · confirmed] reinforcing "do NOT run git" in the delegated build prompt WORKED — this subagent left HEAD
  unchanged (vs the task-4 subagent that committed to main). Keep the hard-prohibition block in every build prompt.
- [TDD · folded] the per-key budget guard transparently caps the agent token because key_id=token_id reuses the [folded foundation-version 36]
  existing `usage:spend:key:{key_id}` envelope — composing a new credential class onto an existing governance seam
  beat adding a parallel budget path (evidence: zero changes to the budget guard; 402 test green).
