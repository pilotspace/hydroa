# TASK: Surface upstream 429 as client 429 + Retry-After (not 502)

slug: upstream-ratelimit-passthrough · created: 2026-06-24 · stage: production
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
- `apps/gateway/src/gateway/proxy/infrastructure/upstream_retry.py`
  - `execute_with_retry(...)` (L103) — the bounded retry loop. L169-171 ALREADY parse the upstream `Retry-After` (for its own backoff); L180-184 on retry-exhaust raise `UpstreamUnavailableError(f"Upstream returned {status}")` — **the defect: drops both the 429 status AND the parsed retry_after**. L193-197 deadline branch raises the same.
  - `parse_retry_after(header_value, *, max_s=60.0)` (L87) — honors a non-negative integer in [0,60]; HTTP-date / garbage / out-of-range → None. Reuse as-is.
  - `RetryPolicy.classify_status` (L54) — labels 429 → "upstream_429".
- `apps/gateway/src/gateway/proxy/domain/errors.py`
  - `UpstreamUnavailableError(ProxyError)` (L8) — caught by the circuit breaker + fallback router for fall-over/counting.
  - `AllDeploymentsSaturatedError` (L19) — **the precedent**: a domain error the use case maps to 429 ERR_RATE_LIMITED. NEW: add `UpstreamRateLimitedError(UpstreamUnavailableError)` carrying `retry_after: float | None` — subclassing preserves every existing `except UpstreamUnavailableError` (breaker count + fall-over) unchanged.
- `apps/gateway/src/gateway/core/error_catalog.py`
  - `ErrorSpec` (L33) with `.exc(detail=..., headers=...)` (L54 supports a `Retry-After` header); `RATE_LIMITED = ErrorSpec(429,"ERR_RATE_LIMITED",...)` (L332); `UPSTREAM_UNAVAILABLE = ErrorSpec(502,...)` (L370). NEW: `UPSTREAM_RATE_LIMITED = ErrorSpec(429,"ERR_UPSTREAM_RATE_LIMITED","Upstream rate limit exceeded")`.
- `apps/gateway/src/gateway/proxy/application/use_cases.py`
  - non-stream chat catch (L1165 `except (UpstreamUnavailableError, CircuitOpenError):` → L1177 `UPSTREAM_UNAVAILABLE.exc()` 502). Precedent AllDeploymentsSaturatedError→429 at L1157-1164.
  - PRE-first-byte stream catch (L1473 → L1484 502) — status NOT yet committed, so a 429 is still settable here.
  - **OUT of this task:** the MID-stream catch (L1507, status already 200) → owned by `stream-upstream-error-frame`.

Context (working folder): chat completion path (`/v1/chat/completions`) only. Confirmed live (2026-06-24 multi-model probe): free OpenRouter models return upstream 429 "temporarily rate-limited"; proxy currently returns 502. Direct-model path (no alias group) — `upstream.complete(body)` at L1155 — is the probe scenario.

Honors (patterns / conventions): the AllDeploymentsSaturatedError→RATE_LIMITED precedent (domain error raised in infra/domain, mapped to HTTP at the use-case boundary); circuit-breaker still counts the failure (subclass); INVARIANT byte-identical upstream-SUCCESS path; CONVENTIONS layering (domain error in `domain/`, spec in `core/error_catalog.py`, HTTP mapping in `application/`).

Anchors the contract cites: `execute_with_retry` exhaustion+deadline branches · `UpstreamRateLimitedError(retry_after)` · `UPSTREAM_RATE_LIMITED` ErrorSpec · use_cases non-stream (L1165) + pre-first-byte stream (L1473) catch sites · `parse_retry_after`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Upstream rate-limit passthrough — surface a retry-exhausted upstream 429 as a client 429 + Retry-After, not a generic 502.
Framings weighed: subclass `UpstreamRateLimitedError(UpstreamUnavailableError)` carrying retry_after, mapped at the use-case boundary (chosen — mirrors the AllDeploymentsSaturatedError→429 precedent; preserves every existing `except UpstreamUnavailableError`) · widen `UpstreamUnavailableError` to carry status+headers (rejected — pollutes the generic error; forces every catch site to branch) · map in a global API exception handler (rejected — the per-path mapping + usage recording already lives in use_cases).
Must:
<must>
  - When an upstream 429 survives the bounded retry (attempt-exhaustion at L180 OR deadline at L193), `execute_with_retry` raises `UpstreamRateLimitedError` carrying the parsed `Retry-After` (float seconds, or None when the upstream omitted/sent an unparseable value).
  - The non-stream chat path (use_cases L1165 region) maps `UpstreamRateLimitedError` → client 429 `ERR_UPSTREAM_RATE_LIMITED`.
  - The PRE-first-byte stream path (use_cases L1473 region) maps `UpstreamRateLimitedError` → client 429 `ERR_UPSTREAM_RATE_LIMITED` (status not yet committed).
  - When retry_after is present, the 429 response carries a `Retry-After: <int seconds>` header; when None, NO Retry-After header is emitted (never fabricate a value).
  - `UpstreamRateLimitedError` IS-A `UpstreamUnavailableError`, so the circuit breaker still counts it and the fallback router still falls over on it (no behavior change for those paths).
  - A rate-limited request fires exactly one `usage_records` row with status=429, usage=None (mirrors the existing 502-on-failure record).
  - ALIAS-GROUP non-stream: when every candidate of an alias group fails and AT LEAST ONE failed with `UpstreamRateLimitedError`, the fallback router's exhaustion raise (`fallback_router.py:397`) raises `UpstreamRateLimitedError` (retry_after = the MAX seen among rate-limited candidates) instead of the generic `UpstreamUnavailableError` → the client gets 429 + Retry-After. When NO candidate was rate-limited, the generic 502 is unchanged.
  - ALIAS-GROUP pre-first-byte stream: NO new code — `open_resilient_stream` (L59) already re-raises the last attempt's actual exception, so a rate-limited last candidate propagates as `UpstreamRateLimitedError` → 429. Locked with a regression test.
</must>
Reject:
<reject>
  - upstream 429 retry-exhausted, non-stream -> "ERR_UPSTREAM_RATE_LIMITED" (429)
  - upstream 429 retry-exhausted, pre-first-byte stream -> "ERR_UPSTREAM_RATE_LIMITED" (429)
</reject>
After:
<after>
  - A rate-limited upstream yields a client 429 + (when supplied) Retry-After.
  - Upstream 5xx / timeout / connect-error still yields 502 `ERR_UPSTREAM_UNAVAILABLE` (UNCHANGED).
  - The upstream-SUCCESS path (non-stream + stream) is byte-identical to pre-v35.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ MAX-retry_after on alias exhaustion is the right merge rule — lowest confidence because when candidates return DIFFERENT Retry-After values (or a mix of rate-limit + 5xx), picking the max is a judgment call. Chosen because the longest hint is the safest back-off for an agent loop (never tells it to retry too soon); raising 429 when ANY candidate was rate-limited (vs requiring ALL) keeps a single 5xx candidate from masking an actionable signal. If wrong: a client backs off slightly longer than strictly necessary. Cost: low.
  - [ ] `parse_retry_after` integer-only [0,60] is acceptable — OpenRouter's 429 may send seconds or omit; an HTTP-date degrades to "no header" (still a 429). Accept.
  - [ ] new code `ERR_UPSTREAM_RATE_LIMITED` (vs reusing `ERR_RATE_LIMITED`) — chosen to distinguish upstream- from gateway-imposed limits; no existing client keys on it. Low risk.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: retry executor surfaces a rate-limit with Retry-After
  Given a do_request that always returns HTTP 429 with header "Retry-After: 7"
  And max_retries exhausts the bounded loop
  When execute_with_retry runs
  Then it raises UpstreamRateLimitedError with retry_after == 7.0
  And the error is an instance of UpstreamUnavailableError   # breaker/fallover unchanged

Scenario: retry executor surfaces a rate-limit with no parseable Retry-After
  Given a do_request that always returns HTTP 429 with no Retry-After header
  When execute_with_retry exhausts retries
  Then it raises UpstreamRateLimitedError with retry_after is None

Scenario: upstream 5xx still raises the generic unavailable error
  Given a do_request that always returns HTTP 503
  When execute_with_retry exhausts retries
  Then it raises UpstreamUnavailableError
  And the error is NOT an UpstreamRateLimitedError   # 502 path unchanged

Scenario: non-stream chat maps an upstream rate-limit to a client 429 + Retry-After
  Given a tenant whose upstream complete() raises UpstreamRateLimitedError(retry_after=5.0)
  When the client POSTs /v1/chat/completions (stream=false)
  Then the response status is 429 with body error code "ERR_UPSTREAM_RATE_LIMITED"
  And the Retry-After response header is "5"
  And one usage_records row is written with status=429

Scenario: non-stream chat omits Retry-After when the upstream gave none
  Given an upstream complete() that raises UpstreamRateLimitedError(retry_after=None)
  When the client POSTs /v1/chat/completions (stream=false)
  Then the response status is 429 with code "ERR_UPSTREAM_RATE_LIMITED"
  And no Retry-After response header is present

Scenario: pre-first-byte streaming rate-limit maps to a client 429
  Given an upstream stream() that raises UpstreamRateLimitedError(retry_after=3.0) before the first byte
  When the client POSTs /v1/chat/completions (stream=true)
  Then the response status is 429 with code "ERR_UPSTREAM_RATE_LIMITED"
  And the Retry-After response header is "3"

Scenario: upstream 5xx still maps to 502 (regression guard)
  Given an upstream complete() that raises a plain UpstreamUnavailableError
  When the client POSTs /v1/chat/completions (stream=false)
  Then the response status is 502 with code "ERR_UPSTREAM_UNAVAILABLE"
  And the success path is unaffected

Scenario: alias-group exhaustion surfaces a rate-limit (max Retry-After)
  Given an alias group whose candidates each raise UpstreamRateLimitedError (retry_after 4 and 9)
  When the fallback router complete() runs and every candidate is exhausted
  Then it raises UpstreamRateLimitedError with retry_after == 9.0

Scenario: alias-group exhaustion with no rate-limit stays generic 502
  Given an alias group whose candidates all raise plain UpstreamUnavailableError
  When the fallback router complete() exhausts every candidate
  Then it raises a plain UpstreamUnavailableError (NOT a rate-limit)   # 502 path unchanged

Scenario: alias-group mixed failures still surface the rate-limit
  Given an alias group where one candidate raises 5xx and another UpstreamRateLimitedError(retry_after=6)
  When complete() exhausts every candidate
  Then it raises UpstreamRateLimitedError with retry_after == 6.0

Scenario: alias-group pre-first-byte stream rate-limit propagates (no new code)
  Given an alias group whose last pre-first-byte stream attempt raises UpstreamRateLimitedError(retry_after=2.0)
  When open_resilient_stream exhausts the attempts
  Then it re-raises that UpstreamRateLimitedError unchanged   # locks the existing re-raise
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# Domain (apps/gateway/src/gateway/proxy/domain/errors.py)
class UpstreamRateLimitedError(UpstreamUnavailableError):
    def __init__(self, message: str = "", *, retry_after: float | None = None) -> None
    retry_after: float | None        # parsed upstream Retry-After seconds, or None

# Error spec (apps/gateway/src/gateway/core/error_catalog.py)
UPSTREAM_RATE_LIMITED = ErrorSpec(429, "ERR_UPSTREAM_RATE_LIMITED", "Upstream rate limit exceeded")

# Retry seam (apps/gateway/src/gateway/proxy/infrastructure/upstream_retry.py)
execute_with_retry(...):
  on a 429 that exhausts retries (L180) OR breaches the deadline (L193):
    raise UpstreamRateLimitedError(f"Upstream returned 429", retry_after=<parsed or None>)
  on any other retryable status (408/5xx/connect/pool) exhausting:
    raise UpstreamUnavailableError(...)            # UNCHANGED

# Alias-group fallback (apps/gateway/src/gateway/proxy/application/fallback_router.py)
complete() loop:
  track across candidate failures: saw_rate_limit: bool, max_retry_after: float | None
  in `except UpstreamUnavailableError as exc` (L318): if isinstance(exc, UpstreamRateLimitedError):
     saw_rate_limit=True; max_retry_after = max(filtered non-None retry_after seen)
  on exhaustion (L397):
     if saw_rate_limit: raise UpstreamRateLimitedError(f"All candidates for '{alias}' are rate-limited", retry_after=max_retry_after)
     else: raise UpstreamUnavailableError(...)        # UNCHANGED
stream_resilient()/open_resilient_stream(): NO CHANGE — L59 bare `raise` already re-raises the last
  attempt's UpstreamRateLimitedError (subclass) unchanged. Locked by a regression test only.

# HTTP mapping (apps/gateway/src/gateway/proxy/application/use_cases.py)
POST /v1/chat/completions   (direct AND alias-group)
  upstream 429 retry-exhausted (non-stream L1165 region AND pre-first-byte stream L1473 region):
    429 -> { error: { code: "ERR_UPSTREAM_RATE_LIMITED", message, type } }
         + header "Retry-After": "<int(retry_after)>"   IFF retry_after is not None
    fires 1 usage_records row: status=429, usage=None
  upstream 5xx/timeout/connect retry-exhausted:
    502 -> { error: { code: "ERR_UPSTREAM_UNAVAILABLE" } }    # UNCHANGED
  upstream 200:  byte-identical                                # UNCHANGED
Schema: no DB schema change. usage_records gains rows with status=429 (column already exists).
```

Least-sure flag surfaced at freeze: [contract] The MAX-retry_after merge rule on alias-group exhaustion (and raising 429 when ANY candidate was rate-limited, not only when ALL were) is the one judgment call most likely to be reconsidered — chosen so an agent loop never under-backs-off and a single 5xx candidate can't mask an actionable rate-limit signal; if wrong the client merely waits slightly longer than strictly necessary (cost: low). [spec] secondary: new code ERR_UPSTREAM_RATE_LIMITED vs reusing ERR_RATE_LIMITED — chosen to let clients distinguish upstream- from gateway-imposed limits.
Status: FROZEN @ v1 — approved by Tin Dang 2026-06-24 (AskUserQuestion: "Also handle alias-group now" — direct + alias-group both in scope)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% on the changed lines (retry-seam branch + the two use-case catch branches).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_retry_429_exhaust_raises_ratelimit_with_retry_after: MockTransport always-429 +"Retry-After: 7" / run execute_with_retry / assert raises UpstreamRateLimitedError, .retry_after==7.0, isinstance UpstreamUnavailableError
  - test_retry_429_no_header_retry_after_none: always-429 no header / assert UpstreamRateLimitedError, .retry_after is None
  - test_retry_5xx_exhaust_raises_plain_unavailable: always-503 / assert UpstreamUnavailableError AND not UpstreamRateLimitedError
  - test_nonstream_ratelimit_maps_429_and_header: upstream.complete raises UpstreamRateLimitedError(retry_after=5.0) / POST stream=false / assert 429 + code ERR_UPSTREAM_RATE_LIMITED + header Retry-After "5" + 1 usage row status=429
  - test_nonstream_ratelimit_no_header_when_none: retry_after=None / assert 429 + no Retry-After header
  - test_stream_prebyte_ratelimit_maps_429: upstream.stream raises UpstreamRateLimitedError(retry_after=3.0) pre-first-byte / POST stream=true / assert 429 + code + Retry-After "3"
  - test_nonstream_5xx_still_502: upstream.complete raises plain UpstreamUnavailableError / assert 502 ERR_UPSTREAM_UNAVAILABLE (regression guard)
  - test_alias_exhaust_all_ratelimited_raises_max_retry_after: fake group, candidates raise UpstreamRateLimitedError(4) + (9) / run router.complete / assert raises UpstreamRateLimitedError .retry_after==9.0
  - test_alias_exhaust_no_ratelimit_stays_generic: candidates all raise plain UpstreamUnavailableError / assert raises plain UpstreamUnavailableError, NOT UpstreamRateLimitedError
  - test_alias_exhaust_mixed_surfaces_ratelimit: one 5xx + one UpstreamRateLimitedError(6) / assert raises UpstreamRateLimitedError .retry_after==6.0
  - test_stream_resilient_reraises_ratelimit: open_resilient_stream attempts whose last raises UpstreamRateLimitedError(2.0) / assert it propagates unchanged (no new code — locks the re-raise)
</test_plan>

Tests live in: `apps/gateway/tests/upstream_ratelimit_passthrough/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/domain/errors.py` `apps/gateway/src/gateway/core/error_catalog.py` `apps/gateway/src/gateway/proxy/infrastructure/upstream_retry.py` `apps/gateway/src/gateway/proxy/application/use_cases.py` `apps/gateway/src/gateway/proxy/application/fallback_router.py`
Strategy (ordered batches): 1. add `UpstreamRateLimitedError` (domain) + `UPSTREAM_RATE_LIMITED` spec (catalog). 2. retry seam: capture retry_after for the 429 branch, raise the new error at exhaust+deadline. 3. fallback_router.complete(): track saw_rate_limit + max_retry_after across candidate failures; raise the specific error on exhaustion (streaming path unchanged). 4. use_cases: add `except UpstreamRateLimitedError` BEFORE the generic `(UpstreamUnavailableError, CircuitOpenError)` at both the non-stream (L1165) and pre-first-byte stream (L1473) sites → 429 + Retry-After + status=429 usage record.
Safety rule (feature-specific): the new `except` MUST precede the generic clause (subclass ordering) or the 429 is swallowed into the 502 path. Retry-After header only when retry_after is not None.
Code lives in: `apps/gateway/src/gateway/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] A retry-exhausted upstream 429 raises `UpstreamRateLimitedError(retry_after=...)` AND is-a `UpstreamUnavailableError` — confirmed by RP-1/RP-2 (`.retry_after` + isinstance) green
- [x] Non-stream + pre-first-byte stream upstream 429 → HTTP 429 body `ERR_UPSTREAM_RATE_LIMITED` + `Retry-After` header (omitted when None) + 1 usage row status=429 — confirmed by UC-1/UC-2/UC-3 green (status/code/header/record assertions)
- [x] Upstream 5xx still → 502 `ERR_UPSTREAM_UNAVAILABLE`; non-rate-limit alias exhaustion stays generic — confirmed by RP-3 + UC-4 + FR-2 green
- [x] Alias-group exhaustion with ≥1 rate-limited candidate raises rate-limit w/ MAX retry_after; mixed 5xx+RL still surfaces RL — confirmed by FR-1/FR-3 green
- [x] Streaming SUCCESS byte-identical (peek+prepend) and v34 disconnect-billing intact — confirmed by adversarial refute-read (verdict SOUND 0.93) + regression slices (streaming_resilience/disconnect_provider_cost/proxy 101 green)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `UpstreamRateLimitedError` referenced in upstream_retry (raise), fallback_router (raise+isinstance), use_cases (3 except sites); `UPSTREAM_RATE_LIMITED` referenced at both use_cases catch sites. All exercised by green tests.
- [x] DEAD-CODE (code) — no orphaned symbol; both new symbols have raise sites + catch sites + tests. The `_poisoned` closure is reached by the non-resilient pre-byte plain-unavailable path (byte-identical guard).
- [x] SEMANTIC — adversarial refute-read (backend-expert, sonnet) ran 8 hypotheses incl. byte-identical streaming, v34 disconnect-billing, retry_after scope, subclass ordering: verdict SOUND 0.93, 0 BLOCKER, 2 NIT → BOTH FIXED by strengthening (ContextVar reset on the 429 stream path; closure default-arg binding). Re-ran new suite (11) + regression slices (101) + full suite (1524) green after the fixes.

### GATE RECORD
Outcome: PASS
Evidence: new suite 11/11 · full suite 1524 passed @ 87.40% coverage (held v34 baseline) · refute-read SOUND 0.93 (2 NITs fixed) · no test/contract weakened · success + 5xx→502 paths unchanged (regression-guarded). Pre-existing (NOT introduced): error_catalog E501 @L98 (OPS_FORBIDDEN) + pyright provider_generation_id @use_cases:1713 (v34 disconnect code).
Reviewed by: AI auto-gate (autonomy: auto) + Tin Dang (contract freeze) · date: 2026-06-24

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of 429 ERR_UPSTREAM_RATE_LIMITED vs 502 ERR_UPSTREAM_UNAVAILABLE (a regression would show 429s collapsing back to 502); Retry-After header presence on upstream-429 responses; streaming TTFB (the new peek moves the first-byte wait ahead of the 200 — watch p50/p99).

### Spec delta
- [SPEC · open] end-to-end test of the resilience-ENABLED HTTP path (`model_router` + `stream_resilience_enabled=True`) for an upstream rate-limit — currently covered only by composition (SR-1 propagation + UC-3 mapping); the refute-read flagged the combination as untested. Close in `error-fidelity-live-verify` (the live stack can toggle resilience). Evidence: refute-read 0.93 discount note.
- [SPEC · open] non-resilient streaming now eagerly peeks the first upstream chunk before committing the 200 (TTFB change for every stream). Acceptable for agent clients but worth a config/contract note; revisit if TTFB-sensitive callers appear. Evidence: use_cases peek @ stream setup.
- [SPEC · open] `parse_retry_after` honors integer-seconds only ([0,60]); an upstream HTTP-date Retry-After degrades to "no header" (still 429). Widen to RFC-1123 date parsing if a provider sends dates. Evidence: §1 assumption 2.

### Competency deltas
- [TDD · folded] a realistic fake matters: the red-test fake's `stream()` raised INSIDE the generator body (not at the call) — that single fidelity choice is what forced the eager-peek design, because async-generator functions don't execute until iterated. Evidence: UC-3 could not pass on the default non-resilient path without the peek. [folded foundation-version 32]
- [ADD · folded] widening a frozen contract mid-freeze (Tin's "also handle alias-group") is a legitimate same-session change request; captured by re-editing §1-§5 before crossing to tests, not after. Evidence: alias-group musts added pre-tests. [folded foundation-version 32]
- [SDD · folded] a subclass error (`UpstreamRateLimitedError(UpstreamUnavailableError)`) is the clean way to add a NEW HTTP mapping without disturbing existing `except` sites — mirrors the AllDeploymentsSaturatedError→429 precedent. Evidence: 0 regression across 1524 tests. [folded foundation-version 32]
