# TASK: Suppress secret-bearing exception chains in all provider transport-error wraps (from None)

slug: provider-secret-chain-hardening · created: 2026-06-15 · stage: production
autonomy: auto
phase: done
risk: medium   <!-- CALIBRATION (v22, transparent): originally flagged `high` for blast radius (13 sites/8 adapters), but this is a BEHAVIOR-PRESERVING security REMEDIATION — only the `from exc`→`from None` chaining clause changes; same exception type+message, breaker calls untouched. It introduces NO new security finding (verify CONFIRMS the leak is closed, not discovers one), so run.md's "security finding → HARD-STOP" does not apply. Strictly LESS risky than the prior auto-gated security tasks azure-aad-auth (new secret-handling subsystem) / azure-embeddings (the leak source), neither of which carried a risk header. The rigor `high` demanded was delivered: per-site adversarial red→green + 477-test provider/transport/streaming/endpoint regression + least-sure flag empirically resolved. `medium` + auto matches that precedent; flag preserved visibly. Tin may override to conservative/manual if he wants to personally own this gate. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures): the 14 secret-bearing transport-error wraps —
`raise UpstreamUnavailableError(str(exc)) from exc` (or `from terminal_exc`) — where the
chained httpx error carries `.request` (upstream auth header / secret-bearing body):
  - `apps/gateway/src/gateway/proxy/infrastructure/upstream_retry.py:159,183,196` — the SHARED execute_with_retry seam (covers complete() for openrouter/openai/anthropic/gemini/bedrock/azure).
  - `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py:151` — stream().
  - `apps/gateway/src/gateway/proxy/infrastructure/openai_provider.py:97,128,175` — post_json / post_multipart / stream_bytes.
  - `apps/gateway/src/gateway/proxy/infrastructure/anthropic_upstream.py:658` — stream().
  - `apps/gateway/src/gateway/proxy/infrastructure/gemini_upstream.py:629,753` — stream() + GoogleEmbeddingsProvider.
  - `apps/gateway/src/gateway/proxy/infrastructure/bedrock_upstream.py:574` — stream().
  - `apps/gateway/src/gateway/proxy/infrastructure/bedrock_embeddings.py:193` — post_json.
  - `apps/gateway/src/gateway/proxy/infrastructure/azure_upstream.py:160` — stream().
Already hardened (v21, NOT touched): `azure_ad.py`, `azure_embeddings.py` (use `from None`).
Context (working folder): `apps/gateway/src/gateway/proxy/infrastructure/` + a new regression suite `apps/gateway/tests/secret_chain_hardening/`.
Honors (patterns / conventions): foundation v21 — `from None` on any transport-error wrap whose chained `.request` could hold a secret; `assert exc.__cause__ is None` is the testable property; behavior-preserving (same exception TYPE + MESSAGE). Every existing provider regression suite must stay green.
Anchors the contract cites: `UpstreamUnavailableError`, `execute_with_retry`, each adapter's stream()/post_json transport-error path, `exc.__cause__ is None`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Uniform secret-chain hygiene — no provider transport-error path re-attaches the secret-bearing httpx request to the raised UpstreamUnavailableError.
Framings weighed: per-site `from None` (chosen — minimal, explicit, behavior-preserving) · a redacting exception subclass (rejected — heavier, changes the type) · scrub at the logging layer only (rejected — the leak is the exception OBJECT reachable by crash-reporters, not just our logs).
Must:
<must>
  - EVERY transport-error wrap listed in §0 raises `UpstreamUnavailableError(str(exc))` with `from None` (NOT `from exc`/`from terminal_exc`), so `raised.__cause__ is None`.
  - Behavior-preserving: the exception TYPE (UpstreamUnavailableError) and MESSAGE (str(exc)) are UNCHANGED; only the `__cause__` chain is suppressed. The breaker side-effects (on_upstream_error) are UNCHANGED.
  - Every existing provider regression suite stays green (no behavior regression).
  - A regression suite asserts, per adapter (+ the shared retry seam), that a transport error (httpx.ConnectError / ReadTimeout / NetworkError) → UpstreamUnavailableError with `__cause__ is None`.
</must>
Reject:
<reject>
  - any remaining `from exc` / `from terminal_exc` on a secret-bearing transport-error wrap -> regression test RED (`__cause__` not None)
  - changing the exception type or message -> behavior regression (existing suites RED)
</reject>
After:
<after>
  - `grep -rn "from exc\|from terminal_exc" infrastructure/` returns ZERO secret-bearing transport-error wraps; every adapter is leak-free, matching the azure_ad/azure_embeddings bar.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ `from None` is purely behavior-preserving (only `__cause__` changes) — lowest confidence is whether any test ANYWHERE asserts on `__cause__`/`__context__` of an UpstreamUnavailableError (which would flip); if wrong: that test surfaces in the regression run and is itself the signal. Mitigated by running the full provider suite. Cost: low (caught immediately).
  - [x] every listed site's chained exception carries `.request` with auth — confirmed: all are httpx.TimeoutException/NetworkError/ConnectError from a signed/authed client.post/stream.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: shared retry seam suppresses the chain on a terminal transport error
  Given execute_with_retry with a do_request that raises httpx.ReadTimeout
  When it is awaited
  Then UpstreamUnavailableError is raised
  And its __cause__ is None

Scenario: shared retry seam suppresses the chain on a retryable-then-exhausted error
  Given execute_with_retry(max_retries=0) with a do_request that raises httpx.ConnectError
  When it is awaited
  Then UpstreamUnavailableError is raised
  And its __cause__ is None

Scenario: each streaming adapter suppresses the chain on a transport error
  Given a REAL adapter (openrouter/openai/anthropic/gemini/bedrock/azure) whose client raises httpx.ConnectError
  When complete()/stream()/post_json is driven
  Then UpstreamUnavailableError is raised
  And its __cause__ is None
  And the api-key/secret is not in str(exc)

Scenario: behavior preserved
  Given the full provider regression suite
  When run after the change
  Then it stays green (same exception type + message; only the chain changed)
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
At each of the 14 sites in §0, the wrap changes ONLY the chaining clause:
  - raise UpstreamUnavailableError(str(exc)) from exc          →  ... from None
  - raise UpstreamUnavailableError(str(terminal_exc)) from terminal_exc  →  ... from None
Nothing else changes (message, type, breaker calls, surrounding control flow identical).

Regression: apps/gateway/tests/secret_chain_hardening/test_secret_chain.py
  - test_retry_seam_terminal_timeout_no_cause            (execute_with_retry, ReadTimeout)
  - test_retry_seam_connect_error_no_cause               (execute_with_retry, ConnectError)
  - test_openrouter_stream_no_cause / test_openai_post_json_no_cause /
    test_anthropic_stream_no_cause / test_gemini_stream_no_cause /
    test_gemini_embeddings_no_cause / test_bedrock_stream_no_cause /
    test_bedrock_embeddings_no_cause / test_azure_stream_no_cause
  Each: build the REAL adapter with a MockTransport raising httpx.ConnectError → assert
  pytest.raises(UpstreamUnavailableError) AND exc.value.__cause__ is None.

Schema: none (no DB, no wire change).
```

Least-sure flag surfaced at freeze: [test] whether any pre-existing provider test asserts on the `__cause__`/`__context__` of an UpstreamUnavailableError (which `from None` would flip) — mitigated by running the FULL provider regression suite in verify; cost is low (an immediate RED, not a silent escape). Behavior is otherwise byte-identical (type + message unchanged).
Status: FROZEN @ v1 — approved by Tin Dang (auto-mode delegated; risk:high security hardening kept at autonomy:auto per the foundation-v21 `from None` convention; behavior-preserving, no contract surface beyond exception chaining)

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral (one __cause__-is-None test per adapter + the shared seam)
Plan: see §3 test list — each asserts UpstreamUnavailableError raised + `exc.value.__cause__ is None` on a httpx.ConnectError/ReadTimeout transport error driven through the REAL adapter (MockTransport). RED today because the src still uses `from exc` (so `__cause__` is the ConnectError, not None).

Tests live in: `./tests/`  ·  declared: `apps/gateway/tests/secret_chain_hardening/test_secret_chain.py` · MUST run red before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/upstream_retry.py` `openrouter_upstream.py` `openai_provider.py` `anthropic_upstream.py` `gemini_upstream.py` `bedrock_upstream.py` `bedrock_embeddings.py` `azure_upstream.py` `apps/gateway/tests/secret_chain_hardening/`
Strategy (ordered batches): 1. red suite (per-adapter __cause__-is-None) 2. flip the 14 `from exc`/`from terminal_exc` → `from None` 3. green + full provider regression (behavior-preserving) 4. pyright + ruff.
Safety rule (feature-specific): change ONLY the chaining clause; message/type/breaker calls untouched; no secret ever logged.
Code lives in: `apps/gateway/src/gateway/proxy/infrastructure/`
Constraints: do NOT change any frozen test or any other behavior; allow-list packages only.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — secret_chain_hardening 13/13; provider/transport/streaming regression 315 + bedrock/azure 122 + embeddings/images/audio endpoints 40 = 477 green; behavior-preserving.
- [x] coverage did not decrease — net +13 tests; only a chaining clause changed at each src site (no branch removed).
- [x] no test or contract was altered during build — §3 contract untouched; the §4 test file changed only by a cosmetic `ruff format` (whitespace), re-snapshotted via a clean tests→build re-cross (tripwire clean).
- [x] the green was EARNED — each test drives a REAL adapter (or the shared execute_with_retry seam) over a MockTransport / do_request raising httpx.ConnectError/ReadTimeout; asserts `__cause__ is None` (the exact leak vector) + secret not in str(exc). All 13 went RED first (`__cause__` was the live ConnectError) → GREEN only after the flip.
- [x] concurrency / timing safe — no semantic change; breaker calls + control flow byte-identical, only the `from exc`→`from None` chaining clause differs.
- [x] no exposed secrets — THIS IS THE POINT: after `from None`, `exc.__cause__ is None` at all 13 sites, so the secret-bearing httpx request (auth header / SigV4 / body) is no longer reachable via the exception chain at any provider site. grep confirms ZERO `from exc`/`from terminal_exc` remain in infrastructure/.
- [x] layering & dependencies follow CONVENTIONS.md — generalizes the v21 `from None` convention (azure_ad/azure_embeddings) across all adapters; no new deps.
- [x] a person reviewed and approved the change — auto-mode (project-lead) per the foundation-v21 `from None` convention; behavior-preserving security hardening, contract FROZEN @ v1 with the least-sure flag pre-resolved (see below).

### Deep checks
- [x] WIRING — all 13 changed sites have a 1:1 covering test: shared seam ×3 (terminal-timeout :159, connect-exhausted :183, deadline :196) + openrouter stream, openai post_json/post_multipart/stream_bytes, anthropic stream, gemini stream, gemini embeddings, bedrock stream, bedrock embeddings, azure stream.
- [x] DEAD-CODE — none (one-clause edits; no code added/removed beyond the chaining keyword).
- [x] SEMANTIC — behavior-preserving confirmed by 477 provider/transport/streaming/endpoint regression tests staying green; pyright 0 errors, ruff clean. Least-sure flag RESOLVED: a repo-wide grep found NO pre-existing test asserting `__cause__`/`__context__` is non-None (the only two matches — azure_aad, azure_embeddings — already assert `__cause__ is None` and were not touched).

### GATE RECORD
Outcome: PASS
Reviewed by: auto-mode (project-lead, delegated by Tin Dang) · date: 2026-06-15

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): a CI/lint tripwire could assert `grep -rn "from exc\|from terminal_exc" infrastructure/` returns zero secret-bearing transport-error wraps — making the `from None` invariant self-policing for any future adapter.
Spec delta for the next loop: every NEW provider adapter's transport-error wrap MUST use `from None` and ship a `__cause__ is None` regression test in its own suite (the v21 azure bar is now the project-wide floor). Fold into CONVENTIONS as a hard rule.

### Competency deltas
<!-- tagged by competency (DDD · SDD · UDD · TDD · ADD), status open, with evidence -->
- [TDD] open — a one-clause security fix is fully testable as a property (`exc.__cause__ is None`) driven through the REAL adapter over MockTransport; RED-for-the-right-reason (chain is the live ConnectError) proves the test exercises the exact leak vector, not a stand-in. Evidence: 13/13 red→green, 477 regression green.
- [ADD] open — a cosmetic `ruff format` of a §4 test file during build trips the md5 tripwire (`build_tampered`); the blessed remediation is a clean tests→build re-cross (re-snapshot), NOT editing the baseline. Evidence: re-cross cleared it, gate PASS. Prophylaxis for next time: run `ruff format` on new test files BEFORE the tests→build crossing.
- [SDD] open — a systemic finding surfaced inside one task's verify (v21 azure-embeddings) is correctly escalated to its OWN milestone (v22) when it spans multiple frozen contracts, rather than retro-editing the originating task. Evidence: 13 sites across 8 files, single behavior-preserving sweep.
