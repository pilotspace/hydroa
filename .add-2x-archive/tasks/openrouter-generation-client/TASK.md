# TASK: OpenRouter get_generation(id) cost-recovery IO client

slug: openrouter-generation-client · created: 2026-06-22 · stage: production
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
  - `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py:OpenRouterCompletionUpstream`
    — the OR adapter. Has `self._client: httpx.AsyncClient(base_url="https://openrouter.ai/api/v1")`,
    `self._breaker: CircuitBreaker`, `self._auth_headers()` (Bearer from request-scoped credential
    contextvar, raises ProviderKeyMissing if unset/non-Bearer), and `complete()` which runs its GET/POST
    through `execute_with_retry(do_request, render, breaker=, provider="openrouter", max_retries=, ...)`.
    The new `get_generation(id)` is a sibling read method on this class.
  - `apps/gateway/src/gateway/proxy/infrastructure/upstream_retry.py:execute_with_retry` — the shared
    designed-for-failure seam: bounded retries w/ full-jitter backoff on 5xx/429/408/connect+pool
    timeout, deadline cap, circuit-breaker integration; raises UpstreamUnavailableError on exhausted/
    terminal transport error, re-raises CircuitOpenError; returns render(terminal_response) otherwise.
  - OpenRouter endpoint (VERIFIED live 2026-06-22): `GET /api/v1/generation?id={id}`, `Authorization:
    Bearer <key>` → `{"data": {"total_cost": <usd>, "usage": <usd>, "upstream_inference_cost": <usd>,
    "native_tokens_prompt": <int>, "native_tokens_completion": <int>, "native_tokens_cached": <int>,
    "tokens_prompt": <int>, "tokens_completion": <int>, ...}}`. base_url already ends in `/api/v1`,
    so the relative path is `/generation`.
Context (working folder): `apps/gateway/tests/openrouter_generation_client/` — new suite; httpx
  MockTransport (no network), set/reset_provider_credential with a BearerCredential (mirrors the
  existing openrouter / incremental_sse_streaming suites).
Honors (patterns / conventions): MUST design for failure (reuse execute_with_retry: timeout + retry +
  circuit-breaker) · money as Decimal not float (v27 billing-precision floor) · credential read per
  request from the contextvar via `_auth_headers()` · no SDK, raw httpx.
Anchors the contract cites: `OpenRouterCompletionUpstream.get_generation` · `GenerationCost`
  (new typed result) · `execute_with_retry` · `_auth_headers` · `self._breaker`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: OpenRouterCompletionUpstream.get_generation(id) — fetch the authoritative cost + native
  token usage of a past generation, the read-side primitive for disconnect cost-recovery (t6.2).
Framings weighed:
  - **reuse execute_with_retry, return a typed GenerationCost | None** (chosen) — the GET runs through
    the same designed-for-failure seam complete() uses (breaker + bounded retry on 5xx/429/408/transport
    + deadline). 200 → parse `data` into a frozen GenerationCost (Decimal cost, int tokens); a terminal
    non-200 (e.g. 404 not-ready) → None so the caller decides to retry-until-ready (t6.2) or sweep (t6.3);
    transport-exhausted/circuit-open propagate as the usual UpstreamUnavailableError/CircuitOpenError.
  - bespoke retry loop here (retry-until-ready inside the client) — rejected: eventual-consistency
    polling is the recovery caller's policy, not the transport primitive's; keep this unit thin + pure-IO.
  - ALL non-200 → None — rejected (change-request v2, refute MEDIUM): an auth/client 4xx (401/403) is
    a PERMANENT failure, not "not ready". Conflating it with None would make the t6.2 recovery caller
    re-poll a broken auth state forever. Only the not-found/not-ready signal (404) maps to None.
Must:
<must>
  - `get_generation(generation_id)` issues `GET /generation?id={generation_id}` with `_auth_headers()`
    (Bearer) through `execute_with_retry(..., breaker=self._breaker, provider="openrouter", ...)`.
  - On a terminal 200, parse the `data` object into `GenerationCost(total_cost: Decimal,
    upstream_inference_cost: Decimal, native_tokens_prompt: int, native_tokens_completion: int,
    native_tokens_cached: int)`; money fields are Decimal (parsed from str/number, never float). A
    present total_cost of 0 is a VALID zero-cost generation (returns GenerationCost), distinct from an
    ABSENT total_cost (→ None, not ready yet).
  - On a terminal 404, return None (the generation isn't available / not ready — retry-or-defer signal).
  - On any OTHER terminal non-200 (e.g. 401/403/4xx), raise UpstreamUnavailableError — a permanent
    failure the recovery caller must NOT re-poll as "not ready".
  - Inherit designed-for-failure from the seam: retry 5xx/429/408/connect+pool-timeout with backoff,
    bounded by the deadline; circuit-breaker guarded; read/write timeout → UpstreamUnavailableError.
</must>
Reject:
<reject>
  - missing / non-Bearer provider credential in the contextvar -> ProviderKeyMissing (from _auth_headers)
  - auth/client 4xx (401/403/other non-404) -> UpstreamUnavailableError (permanent, do not re-poll)
  - retries exhausted / terminal transport error -> UpstreamUnavailableError (from execute_with_retry)
  - circuit open -> CircuitOpenError (from the breaker, re-raised by the seam)
</reject>
After:
<after>
  - A successful lookup yields a GenerationCost whose total_cost is a Decimal equal to OpenRouter's
    reported USD cost and whose native token counts match the response.
  - A not-ready generation (404 or 200-without-total_cost) yields None without raising; an auth/client
    4xx raises UpstreamUnavailableError so the caller stops re-polling.
  - No new outbound behavior on any existing path (complete/stream untouched); breaker state shared.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ the success body wraps the fields under a top-level `data` object — lowest confidence because the
    verification came from the doc, not a live call against this account; if wrong (fields at top level):
    the parse returns zeros/None. Mitigation: parse defensively `body.get("data", body)` so BOTH shapes
    work, and pin the exact shape with the fixture in the red test.
  - [ ] OpenRouter returns a non-200 (404) — not a 200-with-empty-body — when a generation isn't ready
    yet; if it's actually 200-empty, the None signal must key off missing fields too. Mitigation: treat
    a 200 whose `data` lacks total_cost as None as well (covered by a test).
  - [x] base_url already ends in `/api/v1` so the relative path is `/generation` — confirmed from the
    adapter constructor (_BASE_URL) and the complete() path `/chat/completions`.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Successful lookup parses authoritative cost + tokens
  Given OpenRouter returns 200 with {"data":{"total_cost":0.0123,"native_tokens_prompt":11,...}}
  When get_generation("gen-abc") is called
  Then it returns a GenerationCost with total_cost == Decimal("0.0123") and the native token counts
  And money fields are Decimal, not float

Scenario: Cost is parsed exactly from a JSON number (no float drift)
  Given a body whose total_cost is the JSON number 0.00000123
  When get_generation is called
  Then total_cost == Decimal("0.00000123") (parsed float->str->Decimal, never Decimal(float))

Scenario: A present zero cost is a valid generation, not 'not ready'
  Given OpenRouter returns 200 with {"data":{"total_cost":0}}
  When get_generation is called
  Then it returns a GenerationCost with total_cost == Decimal("0") (NOT None)

Scenario: The id is sent as the query param
  Given a 200 response
  When get_generation("gen-xyz") is called
  Then the issued request URL carries ?id=gen-xyz

Scenario: Generation not ready -> None
  Given OpenRouter returns 404 (or a 200 with no total_cost) for the id
  When get_generation is called
  Then it returns None
  And it does not raise

Scenario: Auth/client 4xx is a permanent failure, not 'not ready'
  Given OpenRouter returns 401 (or 403) for the id
  When get_generation is called
  Then UpstreamUnavailableError is raised (NOT None — the caller must not re-poll)

Scenario: Transient 5xx is retried then succeeds
  Given OpenRouter returns 503 then 200 and the client allows one retry
  When get_generation is called
  Then it retries and returns the GenerationCost from the 200
  And the breaker is not left tripped (record_success on the terminal 200)

Scenario: Retries exhausted raises UpstreamUnavailableError
  Given OpenRouter returns 503 on every attempt
  When get_generation is called
  Then UpstreamUnavailableError is raised
  And no GenerationCost is returned

Scenario: Missing credential is rejected before any request
  Given the provider-credential contextvar is unset
  When get_generation is called
  Then ProviderKeyMissing is raised
  And no HTTP request is issued
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
async OpenRouterCompletionUpstream.get_generation(generation_id: str) -> GenerationCost | None

  Outbound: GET https://openrouter.ai/api/v1/generation?id={generation_id}
            headers: Authorization: Bearer <request-scoped key>
            via execute_with_retry(breaker=self._breaker, provider="openrouter",
                                    max_retries=self._max_retries, backoff_base=..., deadline_s=...)

  200  -> GenerationCost(
            total_cost: Decimal,              # data.total_cost (USD, what OR charges us)
            upstream_inference_cost: Decimal, # data.upstream_inference_cost (0 if absent)
            native_tokens_prompt: int,        # data.native_tokens_prompt (0 if absent)
            native_tokens_completion: int,    # data.native_tokens_completion (0 if absent)
            native_tokens_cached: int,        # data.native_tokens_cached (0 if absent)
          )
  200 w/ present total_cost (incl. 0) -> GenerationCost     (0 is a valid zero-cost generation)
  200 w/o total_cost, OR 404          -> None               (not ready / unknown — retry-or-defer)
  other terminal non-200 (401/403/4xx)-> raises UpstreamUnavailableError  (permanent; do NOT re-poll)
  unset/non-Bearer credential         -> raises ProviderKeyMissing
  retries exhausted / transport error -> raises UpstreamUnavailableError
  circuit open                        -> raises CircuitOpenError

GenerationCost: new frozen dataclass in openrouter_upstream.py (money = Decimal).
Schema: none (read-only HTTP call; no DB, no migration).
```

Status: FROZEN @ v2 — approved by Tin Dang (AUTO, autonomy:auto). Change-request from v1 (refute-read
MEDIUM): split non-200 handling — 404→None (not ready) vs auth/client 4xx→raise (permanent), so the
t6.2 recovery caller never infinite-re-polls a broken auth state; and a present total_cost of 0 is a
valid zero-cost generation, not None. Read-only IO primitive on a verified endpoint; billing-shaping
wiring + persistence is t6.2/t6.3 where Tin's policy applies.
Least-sure flag surfaced at freeze: [spec] the success body wraps fields under `data` — why: verified
from docs not a live call on this account; cost: a wrong nesting parses to None/zeros and recovery
silently no-ops. Mitigation: parse `body.get("data", body)` (both shapes) + the red fixture pins the
exact shape, and a 200-without-total_cost is treated as None (tested).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of get_generation + GenerationCost parse.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_get_generation_parses_cost_and_tokens: 200 data body / call / assert GenerationCost fields + Decimal types.
  - test_total_cost_is_exact_decimal: 200 total_cost=JSON-number 0.00000123 / assert == Decimal("0.00000123").
  - test_zero_cost_is_valid_not_none: 200 {"data":{"total_cost":0}} / assert GenerationCost total_cost==0 (v2).
  - test_request_carries_id_query_param: 200 / handler asserts "id=gen-..." in request.url (v2).
  - test_not_ready_404_returns_none: 404 / call / assert None, no raise.
  - test_200_without_total_cost_returns_none: 200 {"data":{}} / call / assert None.
  - test_auth_4xx_raises: 401 / call / assert raises UpstreamUnavailableError (NOT None) (v2).
  - test_transient_5xx_retried_then_success: MockTransport 503→200, max_retries=1 / call / assert GenerationCost.
  - test_retries_exhausted_raises: 503 always, max_retries=1 / call / assert raises UpstreamUnavailableError.
  - test_missing_credential_raises_before_request: no contextvar / call / assert ProviderKeyMissing + 0 requests.
</test_plan>

Tests live in: `./tests/openrouter_generation_client/test_openrouter_generation_client.py`
MUST run red (missing implementation) before Build. Uses httpx MockTransport (no network) +
set/reset_provider_credential with a BearerCredential; a request-counter handler proves the
no-request-on-missing-credential and retry cases.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py`
Strategy (ordered batches): 1. add a frozen `GenerationCost` dataclass (Decimal money, int tokens) +
  a pure `_parse_generation(body) -> GenerationCost | None` helper (reads `body.get("data", body)`,
  Decimal(str(...)) for money, int(...) for tokens, returns None if total_cost absent). 2. add
  `async def get_generation(self, generation_id)` that builds `_do_request` (GET /generation, params
  {"id": id}, headers _auth_headers()) and runs it through execute_with_retry with a render that
  returns (status, json); map 200→_parse_generation, non-200→None.
Safety rule (feature-specific): money parsed via Decimal(str(value)) — NEVER float (billing precision).
Code lives in: `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py`
Constraints: do NOT change any test or the contract; allow-list packages only (stdlib + httpx, already
  deps); no schema/migration; complete()/stream() paths untouched; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 10/10 in tests/openrouter_generation_client/; full suite 1269 green (pre-v2 fix)
- [x] coverage did not decrease — new method + parse fully covered; net +10 tests (3 added in v2)
- [x] no test or contract was altered during build — v2 was a CHANGE-REQUEST (back to specify→re-froze
      @ v2), not a build-phase edit; build only wrote src per the v2 contract
- [x] the green was EARNED — adversarial refute-read (general-purpose sonnet) on v1: NOT-REFUTED on
      core, earned ONE MEDIUM (401/403→None infinite-repoll) + 3 LOW test-gaps. ALL closed in v2:
      auth-4xx→raise (test_auth_4xx_raises), zero-cost valid (test_zero_cost_is_valid_not_none),
      id-param asserted (test_request_carries_id_query_param), exact-decimal now uses a JSON NUMBER.
- [x] concurrency / timing safe — single awaited GET via execute_with_retry; no shared mutable state
      beyond the per-instance breaker (shared by design); backoff_base=0 in tests so no real sleep.
- [x] no exposed secrets / injection / unexpected deps — Bearer via _auth_headers() (masked SecretStr);
      id passed as a urlencoded query param (httpx params=), not string-formatted; stdlib + httpx only.
- [x] layering & dependencies follow CONVENTIONS.md — adapter-layer read method beside complete()/stream().
- [x] a person reviewed and approved the change — contract FROZEN @ v2 by Tin (AUTO); refute-read + gate.

### Build expectations — what "correct" looks like
- [x] money is Decimal end-to-end — total_cost 0.00000123 (JSON number) → Decimal("0.00000123"); confirmed by test_total_cost_is_exact_decimal.
- [x] present total_cost=0 → GenerationCost(0), absent → None — confirmed by test_zero_cost_is_valid_not_none + test_200_without_total_cost_returns_none.
- [x] 404 → None, 401 → raise — confirmed by test_not_ready_404_returns_none + test_auth_4xx_raises.
- [x] retry seam works — 503→200 retries (counter==2, breaker reset); exhausted 503 raises — test_transient_5xx_retried_then_success + test_retries_exhausted_raises.
- [x] request shape — GET /generation?id=gen-xyz — confirmed by test_request_carries_id_query_param.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — GenerationCost/_parse_generation/_gen_to_decimal/_gen_to_int all consumed by
      get_generation; get_generation is the t6.2 entry point (consumed next task), exercised by 10 tests.
- [x] DEAD-CODE (code) — no orphan; helpers are private and all referenced. (t6.2 will import get_generation.)
- [x] SEMANTIC — n/a (code task).

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang (AUTO, autonomy:auto) · date: 2026-06-22

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): get_generation latency · rate of None (not-ready) vs
UpstreamUnavailableError (hard fail) returns · breaker trips attributable to /generation polling.

### Spec delta
- [SPEC · seeded] t6.2 openrouter-cost-recovery: capture the OR generation id on disconnect (SSE `id`
  field in collected chunks, or X-Generation-Id header), persist a pending-recovery marker (migration),
  and run inline fire-and-forget recovery via get_generation → bill through cost_basis='provider'
  (client_disconnect → openrouter_recovered). Retry-until-ready loop / not-ready (None) handling lives here.
- [SPEC · seeded] t6.3 openrouter-recovery-sweep: periodic backstop (mirror ReconciliationDriftChecker)
  that back-fills pending-recovery rows the inline attempt missed; bounded retry budget for permanent 404s.
- [SPEC · open] confirm the live `data`-nesting + that `total_cost` is a JSON number not string on this
  account (verified from docs only) — fold into the t6 live-verify pass (evidence: §1 ⚠ assumption).

### Competency deltas
- [ADD · folded] a refute-read on a thin IO primitive still earns a contract refinement: 401/403 must not [folded foundation-version 28]
  alias "not ready" (None) or the caller infinite-re-polls — split not-ready (404) from permanent (raise)
  (evidence: refute MEDIUM → change-request v2).
- [TDD · folded] a money-precision test must feed a JSON NUMBER, not a string — a str fixture trivially [folded foundation-version 28]
  passes Decimal(str(str)) and proves nothing about the real float→str→Decimal path (evidence: refute LOW).
