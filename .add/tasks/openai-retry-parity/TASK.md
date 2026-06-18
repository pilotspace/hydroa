# TASK: OpenAI direct-chat retry parity via shared execute_with_retry

slug: openai-retry-parity · created: 2026-06-17 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- low-risk: additive parity refactor onto an existing, well-tested shared seam; no new
     dependency, no security surface change (the fail-closed credential gate is preserved). -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/proxy/infrastructure/openai_provider.py:OpenAIDirectProvider` —
  `__init__(*, base_url, metrics_registry)` (NO retry knobs today) · `complete(payload)->(status,body)`
  hand-rolls its own breaker+try/except (single attempt; 5xx→raise) · `stream()` · the non-chat
  `post_json`/`post_multipart`/`stream_bytes` surface · `_auth_headers()` (fail-closed contextvar read).
- `apps/gateway/src/gateway/proxy/infrastructure/upstream_retry.py:execute_with_retry(do_request,
  render_response, *, breaker, provider, max_retries, backoff_base, deadline_s, policy, metrics_registry)`
  — the SHARED retry seam the other 5 chat adapters already call.
- `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py:OpenRouterCompletionUpstream`
  — the reference: `__init__(*, base_url, max_retries=0, backoff_base=0.5, retry_deadline_s=0.0,
  metrics_registry=None)`; `complete()` delegates to `execute_with_retry`; `stream()` = "zero retry machinery".
- `apps/gateway/src/gateway/main.py` ~L429 — `_openai_direct = OpenAIDirectProvider(base_url=…,
  metrics_registry=…)` is the ONLY adapter ctor missing `max_retries/backoff_base/retry_deadline_s`
  (the other 5 thread `settings.upstream_max_retries` / `…_retry_backoff_base_s` / `…_retry_deadline_s`).
- `apps/gateway/src/gateway/core/config.py:Settings` — fields already exist: `upstream_max_retries`,
  `upstream_retry_backoff_base_s`, `upstream_retry_deadline_s` (validated; max_retries∈[0..8]).

Context (working folder): apps/gateway — pyright-strict, RFC9457; `make test-fast` is the no-DB floor.
Honors (patterns / conventions): the per-provider retry-parity test convention
(`tests/retry_policy/test_anthropic_retry.py` + `make_anthropic_upstream`); the v6 production-wiring
rule (`tests/retry_policy_wiring/` asserts create_app threads Settings→adapter). Reuse the shared
`tests/retry_policy/conftest.py` doubles (CountingCircuitBreaker · SequencedMockTransport · make_json_response).
Anchors the contract cites: `OpenAIDirectProvider.__init__`, `OpenAIDirectProvider.complete`,
`execute_with_retry`, `Settings.upstream_max_retries`.

CROSS-TASK CONSTRAINT (the load-bearing fact): the FROZEN `tests/openai_chat_dispatch/` suite (task
`openai-chat-complete`, committed 7f9f7a2) builds the provider via `OpenAIDirectProvider.__new__(...)`
setting ONLY `_client`/`_breaker`/`_metrics_registry`. If `complete()` starts reading `self._max_retries`
without a fallback, OC1–OC6 break with AttributeError. → BUILD MUST give the three retry knobs
**class-level defaults** (`_max_retries=0`, `_backoff_base=0.5`, `_retry_deadline_s=0.0`) so `__new__`-built
instances resolve to single-attempt behavior. This frozen suite must NOT be edited.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: OpenAI direct-chat retry parity (route `complete()` through the shared `execute_with_retry`).
Framings weighed: reuse the shared `execute_with_retry` seam (chosen — identical policy, metrics,
breaker semantics as the other 5 adapters) · a bespoke `openai_max_retries` knob + inline loop (rejected:
divergent policy, duplicate code, new Settings field for no reason) · do nothing (rejected: OpenAI is the
only chat adapter with no transient-5xx resilience, a real gap the v25 live pass region touched).
Must:
<must>
  - `OpenAIDirectProvider.__init__` accepts `max_retries:int=0`, `backoff_base:float=0.5`,
    `retry_deadline_s:float=0.0`; stores them on `self`; the three are ALSO class-level defaults
    so `__new__`-built instances (no __init__) read 0 / 0.5 / 0.0.
  - `complete()` delegates to `execute_with_retry(provider="openai", breaker=self._breaker,
    max_retries=self._max_retries, backoff_base=self._backoff_base, deadline_s=self._retry_deadline_s,
    metrics_registry=self._metrics_registry)`; `do_request` POSTs `/chat/completions` json=payload with
    `_auth_headers()`; `render_response` = `(resp.status_code, resp.json())`.
  - Retryable (5xx · 408 · 429 · connect-timeout) → retried up to `max_retries`, full-jitter backoff,
    bounded by `retry_deadline_s`; 429 honors `Retry-After`. Exhaustion / deadline → UpstreamUnavailableError.
  - Non-retryable terminal (200 · 4xx≠{408,429}) → passthrough `(status, body)` after exactly 1 attempt;
    breaker.record_success. ReadTimeout/WriteTimeout/NetworkError → UpstreamUnavailableError (never retried).
  - `main.py` threads `settings.upstream_max_retries` / `upstream_retry_backoff_base_s` /
    `upstream_retry_deadline_s` into the `_openai_direct` ctor (parity with the other 5 adapters).
  - `stream()` and the non-chat surface (`post_json`/`post_multipart`/`stream_bytes`) are UNCHANGED.
Reject:
<reject>
  - unset / non-Bearer provider credential (contextvar) -> "ERR_PROVIDER_KEY_MISSING" (fail-closed,
    raised by `_auth_headers()` BEFORE any HTTP attempt — survives the retry refactor).
  - circuit open at guard -> CircuitOpenError (re-raised from `breaker.guard()` before each attempt).
</reject>
After:
<after>
  - With `max_retries=0` (default) `complete()` makes exactly 1 attempt and never sleeps. For 5xx this is
    byte-identical to today; for 408/429 it is an INTENTIONAL change — the old code passed 408/429 through as
    (status, body) (status<500), the seam classifies them retryable so with retries disabled they surface as
    UpstreamUnavailableError (exact parity with the other 5 adapters; see §3).
  - The frozen `tests/openai_chat_dispatch/` OC1–OC9 suite stays green, untouched.
  - `app.state.chat_adapters["openai"]._max_retries == settings.upstream_max_retries`.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ class-level defaults on the three retry knobs are sufficient to keep the FROZEN
    `openai_chat_dispatch` `__new__`-built providers green — lowest confidence because it depends on
    `complete()` reading ONLY those three (no other new required attr); if wrong: OC1–OC6 AttributeError
    and I'd be tempted to edit a frozen test (HARD-STOP — must instead fix the defaults). Mitigation:
    a dedicated test asserts `OpenAIDirectProvider.__new__(...)._max_retries == 0`.
  - [ ] `stream()` stays retry-free — confirmed: every sibling adapter's `stream()` is "zero retry machinery".
  - [ ] Settings fields already exist & are validated — confirmed by grep (no config.py change needed).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked, top ⚠-flagged with why + cost. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: transient 5xx is retried then succeeds
  Given an OpenAI adapter with max_retries=1 and upstream returning 503 then 200
  When complete() is called
  Then it returns (200, body) after 2 POSTs
  And the breaker saw exactly 1 error + 1 success and the retried counter incremented

Scenario: 408 timeout-status is retried
  Given max_retries=1 and upstream returning 408 then 200
  When complete() is called
  Then it returns (200, …) after 2 POSTs

Scenario: connect error is retried then succeeds
  Given max_retries=1 and a ConnectError then 200
  When complete() is called
  Then it returns 200 after 2 POSTs with breaker error=1, success=1

Scenario: default-off is byte-identical to today
  Given max_retries=0 and upstream returning 503
  When complete() is called
  Then it raises UpstreamUnavailableError after exactly 1 POST
  And asyncio.sleep is never awaited

Scenario: 4xx passthrough is never retried
  Given max_retries=3 and upstream returning 400
  When complete() is called
  Then it returns (400, body) after exactly 1 POST

Scenario: stream() has zero retry machinery
  Given max_retries=3 and a ConnectError on the stream
  When stream() is iterated
  Then it raises UpstreamUnavailableError after exactly 1 POST

Scenario: fail-closed survives the retry refactor
  Given retries configured but NO provider credential in the contextvar
  When complete() is called
  Then it raises ProviderKeyMissing (ERR_PROVIDER_KEY_MISSING) and makes 0 POSTs

Scenario: __new__-built provider keeps single-attempt behavior (frozen-suite guarantee)
  Given a provider built via OpenAIDirectProvider.__new__ with only _client/_breaker/_metrics_registry
  When its _max_retries / _backoff_base / _retry_deadline_s are read
  Then they resolve to the class defaults 0 / 0.5 / 0.0 (no AttributeError)

Scenario: production wiring threads Settings into the adapter
  Given create_app(settings with upstream_max_retries=N)
  When app.state.chat_adapters["openai"] is inspected
  Then its _max_retries == N (and backoff/deadline likewise)
  And with default settings _max_retries == 0

Scenario: zero regression on the dual-role surface
  Given the OpenAI adapter
  When type-checked
  Then it still satisfies CompletionUpstream AND UpstreamProvider
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# internal adapter seam (no HTTP surface change)

class OpenAIDirectProvider:
    _max_retries: int = 0          # class-level defaults — read by __new__-built instances
    _backoff_base: float = 0.5     #   (keeps the FROZEN openai_chat_dispatch suite green)
    _retry_deadline_s: float = 0.0

    __init__(*, base_url=_DEFAULT_BASE_URL, max_retries=0, backoff_base=0.5,
             retry_deadline_s=0.0, metrics_registry=None)
        -> stores self._max_retries / _backoff_base / _retry_deadline_s (+ _client/_breaker/_metrics_registry)

    async complete(payload) -> (status:int, body:dict)
        via execute_with_retry(do_request=POST /chat/completions json=payload headers=_auth_headers(),
                               render_response=lambda r:(r.status_code, r.json()),
                               breaker=self._breaker, provider="openai",
                               max_retries=self._max_retries, backoff_base=self._backoff_base,
                               deadline_s=self._retry_deadline_s, metrics_registry=self._metrics_registry)
        200 / 4xx(≠408,429) -> (status, body)            # terminal, record_success
        5xx / 408 / 429 / connect-timeout -> retried, then UpstreamUnavailableError on exhaustion/deadline
        ReadTimeout/WriteTimeout/NetworkError -> UpstreamUnavailableError (not retried)
        unset/non-Bearer credential -> ProviderKeyMissing("openai")  # ERR_PROVIDER_KEY_MISSING, pre-HTTP
        circuit open -> CircuitOpenError (from breaker.guard, before each attempt)

    stream(payload), post_json(...), post_multipart(...), stream_bytes(...)  -> UNCHANGED

main.py:
    _openai_direct = OpenAIDirectProvider(
        base_url=settings.openai_base_url,
        max_retries=settings.upstream_max_retries,
        backoff_base=settings.upstream_retry_backoff_base_s,
        retry_deadline_s=settings.upstream_retry_deadline_s,
        metrics_registry=app.state.metrics_registry,
    )
Schema: no DB/schema touched. Settings fields pre-exist (config.py unchanged).
```

Status: FROZEN @ v1 — approved by Tin (project-lead, auto 2026-06-17)
Least-sure flag surfaced at freeze: [contract] the **class-level-default** mechanism is the one load-bearing
choice — it is what lets `complete()` read `self._max_retries` while the FROZEN `openai_chat_dispatch`
`__new__`-built providers (which never ran `__init__`) stay green untouched. Why most likely wrong: if any
frozen OC test depended on the ABSENCE of these attrs, or if `complete()` needs another new required attr,
the defaults wouldn't save it. Cost if wrong: OC1–OC6 AttributeError → re-open SPECIFY (never edit the
frozen suite). Guarded by a dedicated `__new__` default-resolution test in §4.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of the changed `complete()` / `__init__` branches.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_openai_retries_503_then_200: 503→200, max_retries=1 / assert (200,body), 2 POSTs, breaker 1+1, retried counter=1
  - test_openai_408_is_retried: 408→200 / assert 200, 2 POSTs
  - test_openai_connect_error_then_200: ConnectError→200 / assert 200, 2 POSTs, breaker err=1 success=1
  - test_openai_default_off_byte_identical: max_retries=0, 503 / assert raise, 1 POST, sleep not awaited
  - test_openai_400_passthrough_not_retried: max_retries=3, 400 / assert (400,body), 1 POST
  - test_openai_stream_never_retried: max_retries=3, ConnectError on stream / assert raise, 1 POST
  - test_openai_failclosed_no_credential_no_http: retries set, no contextvar cred / assert ProviderKeyMissing, 0 POSTs
  - test_new_built_provider_uses_class_default_retries: __new__ instance / assert _max_retries==0, _backoff_base==0.5, _retry_deadline_s==0.0
  - test_wiring_default_max_retries_zero: create_app(default) / assert chat_adapters["openai"]._max_retries==0
  - test_wiring_custom_retry_settings_threaded: create_app(max_retries=3, backoff=1.5, deadline=2.0) / assert all three threaded
  - test_openai_still_satisfies_both_protocols: isinstance CompletionUpstream AND UpstreamProvider
</test_plan>

Tests live in: `apps/gateway/tests/openai_retry_parity/` · MUST run red (no retry loop / knobs unwired) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/openai_provider.py` `apps/gateway/src/gateway/main.py` `apps/gateway/tests/openai_retry_parity/`
Strategy (ordered batches): 1. add class-level retry defaults + ctor params + `execute_with_retry` import to openai_provider.py · 2. refactor `complete()` onto `execute_with_retry` (leave `stream()`/non-chat untouched) · 3. thread the 3 settings into the `_openai_direct` ctor in main.py.
Safety rule (feature-specific): the fail-closed credential check (`_auth_headers()` → ProviderKeyMissing) MUST run before any HTTP; never weaken it. Retry deadline + breaker guard are the timeout/circuit-breaker controls (design-for-failure).
Code lives in: `apps/gateway/src/gateway/`
Constraints: do NOT change any test or the contract; do NOT edit the frozen `tests/openai_chat_dispatch/` suite; allow-list packages only (no new dependency); ask if unclear.

<!-- Scope tokens declared on the FIRST line of "Scope (may touch)" above; each contains "/" so each
     resolves from project root; the test-dir token covers its whole subtree. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — openai_retry_parity 15/15 + frozen openai_chat_dispatch 10/10 green; retry_policy/
      retry_policy_wiring/provider_seam/dynamic_auth_byok 95/95; no-DB floor (test-fast) 154/154; ruff clean;
      `uv run pyright` (src/gateway) 0 errors.
- [x] coverage did not decrease — net-new suite + behavior-preserving src refactor.
- [x] no test or contract was altered during build — frozen openai_chat_dispatch UNTOUCHED (git: only 7f9f7a2).
      The §4 test edits (strengthen default-off + add 429-retry) were done back-in-tests-phase with a re-snapshot,
      never during build; they ADD coverage (strengthen, not weaken).
- [x] the green was EARNED — adversarial refute-read (sonnet) confirmed frozen suite intact, fail-closed preserved,
      wiring correct, no leak. It caught ONE real earned-green gap: the old "byte-identical" claim was false for
      408/429 (the seam classifies them retryable). RESOLVED: strengthened `test_..._single_attempt_retryable_raises`
      (now parametrized 503/408/429) + added `test_openai_429_retried_and_honors_retry_after`, and corrected §1 After.
      The 408/429 behavior change is INTENTIONAL parity (the frozen §3 already specified it), now explicitly pinned.
- [x] concurrency / timing safe — retry deadline + breaker guard bound it (shared seam, unchanged semantics).
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new package; `_auth_headers` masks the
      secret; secret-free chaining inside the seam (`from None`); metric label is `provider="openai"` only.
- [x] layering & dependencies follow CONVENTIONS.md — adapter → shared `execute_with_retry`, identical to siblings.
- [x] a person reviewed and approved the change — Tin (project-lead, auto 2026-06-17); refute-read = the adversarial reviewer.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `_openai_direct` ctor threads the 3 settings (main.py L430-432, confirmed by wiring tests);
      `complete()` calls `execute_with_retry`; the SAME instance is reused for non-chat `_providers["openai"]`
      and its post_json/stream_bytes surface is unaffected (no retry machinery there).
- [x] DEAD-CODE (code) — old hand-rolled breaker/try block fully replaced; `UpstreamUnavailableError` import still
      used by `stream()`; no orphaned import (ruff clean).
- [x] SEMANTIC (prose / non-code) — n/a (code task).

### GATE RECORD
Outcome: PASS
Reviewed by: Tin (project-lead, auto) · date: 2026-06-17

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): `gateway_upstream_retries_total{provider="openai"}` retried/exhausted rate.
Spec delta for the next loop: OpenAI now has transient-5xx resilience parity; all 6 chat adapters share one retry policy.

### Competency deltas
- [TDD · folded] class-level attribute defaults are the clean seam to extend an adapter's ctor without
  breaking a sibling task's `__new__`-built test doubles (evidence: kept frozen openai_chat_dispatch green).
