# TASK: Pre-first-byte streaming retry/fallback (no mid-stream replay)

slug: streaming-resilience · created: 2026-06-15 · stage: production · risk: high
autonomy: conservative   <!-- HIGH-RISK (streaming commit boundary): lowered from auto so the verify gate REQUIRES Tin's sign-off (unguarded_high_risk_auto guard). Per Tin's v19 pacing: build to verify, then STOP. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Feature: pre-first-byte STREAMING resilience — when an upstream stream fails BEFORE the first
SSE byte reaches the client, transparently FALL OVER to the next deployment in the model-group
(the streaming analog of v6 non-streaming model fallback). Once a chunk reaches the client the
stream is COMMITTED — no replay (today's mid-stream behavior holds, verbatim). Opt-in/default-off.

Touches (files · symbols · signatures):
- `proxy/domain/ports.py:CompletionUpstream` (L104-121) — `complete()` → (status, body); `stream(payload) -> AsyncIterator[bytes]` is a SYNC def returning a generator; the HTTP connection + status are LAZY (opened on first `__anext__`). Docstring: "Raises UpstreamUnavailableError on 5xx / timeout / network error." **REUSE, no change.**
- `proxy/application/use_cases.py:CompletionUseCase.stream()` (L1132-1350) — auth→validate→governance→guardrails ALL pre-byte; then `gen = model_router.stream(body, upstream=upstream)` (sync, L1247-1251) wrapped in try/except (UpstreamUnavailableError|CircuitOpenError)→records 502, raises UPSTREAM_UNAVAILABLE; returns `_wrapped()` which `async for chunk in gen` (the FIRST `yield chunk` = pre-first-byte commit point), collects chunks, `extract_usage_from_sse`→`_fire_record_with_raw(status=200)`. Intricate span contract (`_stream_error_status` sentinel; `_wrapped` emits success span; finally emits error span). **CHANGE (flag-gated): when resilience enabled + model_router wired, `first_chunk, gen = await model_router.stream_resilient(...)` (the await drives fallover; total pre-first-byte failure raises UpstreamUnavailableError → caught by the SAME existing except → 502 before StreamingResponse); `_wrapped` prepends first_chunk. Flag OFF → OLD path verbatim (byte-identical; the 3 frozen stream tests stay green).**
- `proxy/application/fallback_router.py:FallbackModelRouter.stream()` (L392-418) — today: alias→primary candidate only, `return _upstream.stream(rewritten)`; "No fallback on stream failure (deferred beyond v6)". `complete()` (L221-390) is the candidate-iteration model to mirror (strategy_order, _inc_counter outcomes, last_fallen). **ADD: `stream_resilient(payload, upstream) -> tuple[bytes|None, AsyncIterator[bytes]]` (async) + ctor flag `stream_resilience_enabled: bool = False`; existing sync `stream()` UNCHANGED.**
- `proxy/application/streaming_resilience.py` (NEW) — pure-ish async helper `open_resilient_stream(*, attempts, open_stream, on_fallover=None)`: try each attempt's model id in order, open its stream, `await __anext__()` the FIRST chunk; pre-first-byte UpstreamUnavailableError|CircuitOpenError → on_fallover, try next; first chunk → COMMIT return (first_chunk, rest_gen); StopAsyncIteration (empty upstream) → return (None, empty); ALL fail → raise UpstreamUnavailableError. Rest generator yields remaining chunks (mid-stream errors propagate — NO replay).
- `core/config.py:Settings` — **ADD** `upstream_stream_resilience_enabled: bool = Field(default=False)` (env GATEWAY_STREAM_RESILIENCE_ENABLED).
- `main.py` (~L495 router ctor, ~L531 app.state) — **ADD** `stream_resilience_enabled=settings.upstream_stream_resilience_enabled` to FallbackModelRouter ctor + `app.state.stream_resilience_enabled`.
- `proxy/api/deps.py:get_completion_use_case` (L65-102) — **ADD** read settings flag, pass to CompletionUseCase ctor (additive kwarg `stream_resilience_enabled=False`).
- REUSE (no change): `usage/domain/extractor.py:extract_usage_from_sse(chunks)`; `proxy/domain/errors.py:{UpstreamUnavailableError,CircuitOpenError}`; `_inc_counter`/`fallback_*` metric on the router; strategy_order.

Context (working folder):
- `.add/milestones/v19/MILESTONE.md` — STREAMING IS THE HARD BOUNDARY (resilience ONLY pre-first-byte; the instant the first chunk reaches the client → committed, no retry/fallback/replay — the riskiest contract, every reviewer checks it). BILLING ACCURACY (only the SERVED attempt bills; discarded pre-first-byte attempts never bill — they raise before any chunk, so no usage is extracted). OPT-IN/DEFAULT-OFF byte-identical. DESIGN-FOR-FAILURE.
- Tests: `tests/proxy/` (SSE byte-identical, anti-tamper), `tests/retry_policy/` (3 tests assert stream NEVER retried — MUST stay green), `tests/model_fallbacks/test_f11_stream_resolves_to_first_candidate_no_fallback` + `tests/error_aware_fallback/test_stream_untouched` (assert NO stream fallback today — these will need the flag OFF to stay green, and new flag-ON tests prove fallover). New suite dir: `tests/streaming_resilience/`.

Honors (patterns / conventions):
- RETRY SEAM stays `complete()`-only (upstream_retry.py module invariant + 3 tests). Streaming resilience is FALLOVER across deployments, NOT same-target retry → providers UNTOUCHED, the "stream never retried" invariant preserved. Same-target streaming retry is OUT (documented boundary; a plain model id with no candidates keeps today's behavior).
- OPT-IN/DEFAULT-OFF (foundation rule): GATEWAY_STREAM_RESILIENCE_ENABLED default false → every streaming request byte-identical to today.
- BILLING ACCURACY (v12): single-bill preserved — the served stream extracts usage once; a fallen-over attempt raises pre-byte (no chunks, no usage, no record).
- STREAMING COMMIT BOUNDARY (v19): the await of the FIRST chunk is the line; after it, no replay. Mirrors v6 `complete()` fallover but stops at the first byte.
- DESIGN-FOR-FAILURE (CLAUDE.md): bounded by candidate count (no unbounded loop); each attempt's failure is contained; total failure degrades to the existing 502 path.

Anchors the contract cites:
- `open_resilient_stream(*, attempts, open_stream, on_fallover=None) -> tuple[bytes|None, AsyncIterator[bytes]]` (NEW helper).
- `FallbackModelRouter.stream_resilient(payload, upstream) -> tuple[bytes|None, AsyncIterator[bytes]]` (NEW async) + ctor `stream_resilience_enabled: bool=False`.
- `CompletionUseCase.stream(..., )` flag-gated peek path + ctor `stream_resilience_enabled: bool=False`.
- `Settings.upstream_stream_resilience_enabled` (NEW knob, env GATEWAY_STREAM_RESILIENCE_ENABLED).
- REUSE: `extract_usage_from_sse`, `UpstreamUnavailableError`, `CircuitOpenError`, strategy_order, `_inc_counter`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Pre-first-byte streaming resilience — transparent candidate FALLOVER before the first SSE
byte reaches the client (the streaming analog of v6 non-streaming model fallback). Opt-in/default-off.

Framings weighed: candidate FALLOVER pre-first-byte, peeked in the use case (chosen) — mirrors the
v6 `complete()` fallover, bounded by candidate count, leaves providers + the retry seam untouched ·
same-target streaming RETRY inside providers (rejected — breaks the tested "stream never retried"
invariant, touches every provider, re-opens SSE connections with backoff) · buffer-whole-stream-then-
decide (rejected — defeats streaming; we must never buffer the CLIENT-facing stream).

Must:
<must>
  - Flag GATEWAY_STREAM_RESILIENCE_ENABLED=true + model is an alias with ≥2 candidates + model_router
    wired: if opening a candidate's stream fails BEFORE its first chunk (UpstreamUnavailableError or
    CircuitOpenError), fall over to the next candidate in strategy order; serve the FIRST candidate
    whose first chunk is obtained.
  - Bytes are byte-identical pass-through (no parse / re-serialize) — the anti-tamper SSE invariant holds.
  - Bill exactly ONCE, for the SERVED attempt only (post-stream extract_usage_from_sse over the served
    chunks). A fallen-over attempt bills nothing (it raised before any chunk → no usage extracted).
  - After the first chunk reaches the client the stream is COMMITTED: a mid-stream failure keeps today's
    behavior verbatim (record status=502 internally, stop) — NO replay, NO fallover.
  - A pre-first-byte fallover increments the streaming-fallover metric (per-alias from→to, outcome label).
  - Flag OFF (default): every streaming request is byte-identical to today (the old sync `stream()` path).
</must>
Reject:
<reject>
  - ALL candidates fail pre-first-byte -> raise UPSTREAM_UNAVAILABLE (HTTP 502) BEFORE StreamingResponse
    commits (the use case awaits the first chunk pre-response) — never a silent empty-200.
  - Mid-stream failure AFTER the first byte -> today's behavior (record + stop); the 200 stream is already
    committed, so it is NOT an error response and NOT replayed.
  - Plain model id (no candidates) with flag ON -> byte-identical to today (no same-target streaming retry);
    a documented boundary, not an error.
</reject>
After:
<after>
  - The client received a complete SSE stream from a SINGLE served deployment; usage billed once for that
    deployment; the fallover metric reflects every skipped candidate; the committed stream was never replayed.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The pre-first-byte boundary == awaiting the FIRST `__anext__()` of a candidate's stream. For OpenRouter
    that is the true first network byte; for Anthropic/Gemini the adapter BUFFERS the whole upstream stream
    before yielding the first TRANSLATED chunk, so "pre-first-byte" there means "after the full upstream is
    consumed but before the CLIENT sees anything." Still correct (the client sees nothing until commit), but
    a slow/large Anthropic/Gemini upstream delays the commit/fallover decision up to its read timeout —
    lowest confidence because it couples fallover latency to provider buffering. If wrong: MEDIUM cost
    (added latency on the fallover path for buffering providers); mitigated by the existing upstream read
    timeout bounding the wait, and by default-off.
  - [ ] A total pre-first-byte failure must surface as HTTP 502 BEFORE StreamingResponse — requires the use
    case to AWAIT the first chunk before returning the generator. Feasible (the use case is async). If wrong
    (peek skipped): a total failure degrades to an empty-200 (today's 5xx-on-stream behavior) — worse, not
    catastrophic.
  - [ ] 4xx triggers (context-window / content-policy) do NOT apply to streaming — providers only guard
    ≥500 on the stream path, so a streaming 4xx is invisible today. OUT of scope; only transport failures
    (≥500 / timeout / network / circuit-open) trigger streaming fallover. If wrong: a follow-up adds
    streaming 4xx detection; non-streaming (task 2) already covers 4xx triggers.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Pre-first-byte fallover to the next candidate (the core Must)
  Given resilience is enabled and an alias maps to [cand-A, cand-B] in strategy order
  And cand-A's stream raises UpstreamUnavailableError before yielding any chunk
  And cand-B's stream yields a normal SSE sequence
  When a streaming completion is requested for the alias
  Then the client receives cand-B's full SSE byte sequence
  And cand-A produced zero bytes to the client

Scenario: Served stream is byte-identical pass-through
  Given resilience is enabled and the served candidate yields chunks [c0, c1, DONE]
  When the stream is consumed
  Then the client receives exactly b"".join([c0, c1, DONE]) with no re-framing or re-serialization

Scenario: Single-bill — only the served attempt bills, the fallen-over attempt bills nothing
  Given resilience is enabled, cand-A fails pre-first-byte, cand-B serves with usage total_tokens=42
  When the stream completes
  Then exactly one usage record is fired, status=200, for cand-B with total_tokens=42
  And no usage record is fired for cand-A

Scenario: After the first byte the stream is committed — no replay, no fallover (the hard boundary)
  Given resilience is enabled, cand-A yields c0 to the client THEN raises UpstreamUnavailableError mid-stream
  When the stream is consumed
  Then the client received c0 (the committed prefix) and the stream stops
  And cand-B is NEVER attempted (no fallover after commit)
  And a usage record with status=502 is fired internally (today's mid-stream behavior, verbatim)

Scenario: A pre-first-byte fallover increments the streaming-fallover metric
  Given resilience is enabled, cand-A fails pre-first-byte, cand-B serves
  When the stream completes
  Then the fallover counter is incremented once for (alias, from=cand-A, to=cand-B)

Scenario: Flag OFF is byte-identical to today (default)
  Given resilience is DISABLED (default) and an alias maps to [cand-A, cand-B]
  And cand-A's stream raises UpstreamUnavailableError before any chunk
  When a streaming completion is requested for the alias
  Then behavior is today's: cand-A only is attempted, NO fallover to cand-B
  And the existing "stream never retried" + "stream resolves to first candidate" tests stay green

Scenario: All candidates fail pre-first-byte -> 502 before the response commits (Reject)
  Given resilience is enabled and every candidate's stream fails before its first chunk
  When a streaming completion is requested
  Then the request fails with HTTP 502 ERR_UPSTREAM_UNAVAILABLE raised BEFORE StreamingResponse starts
  And the client never receives a 200 empty stream

Scenario: Plain model id (no candidates) with flag ON is byte-identical (Reject same-target retry)
  Given resilience is enabled and the request targets a plain model id with no model-group
  And its stream fails before the first chunk
  When a streaming completion is requested
  Then behavior is today's (no same-target streaming retry) — a documented boundary, not a new code path
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# ── (A) Pure-ish async helper — proxy/application/streaming_resilience.py (NEW) ──────────
async def open_resilient_stream(
    *,
    attempts: list[str],                                  # ordered model ids to try (≥1)
    open_stream: Callable[[str], AsyncIterator[bytes]],   # model_id -> a FRESH upstream stream
    on_fallover: Callable[[str, str], None] | None = None,  # (from_model, to_model) per skip
) -> tuple[bytes | None, AsyncIterator[bytes]]:
  Iterate `attempts` in order. For each model_id:
    gen = open_stream(model_id); ait = gen.__aiter__()
    try: first = await ait.__anext__()          # PRE-FIRST-BYTE boundary — the ONLY retriable point
    except (UpstreamUnavailableError, CircuitOpenError):
        if a NEXT attempt exists: on_fallover(this, next); continue   # nothing yielded → safe
        else: raise                              # last attempt failed pre-byte → propagate
    except StopAsyncIteration:                   # upstream yielded zero chunks, clean end
        return (None, _empty_aiter())            # committed-empty (no fallover; degenerate success)
    else:
        return (first, ait)                      # COMMITTED — caller yields `first` then drains `ait`
  # unreachable (loop either returns or the last attempt raises)
  PURE of business logic: no billing, no metrics except via on_fallover; never swallows a
  post-first-byte error (those live in `ait`, surfaced to the caller's drain loop → no replay).

# ── (B) FallbackModelRouter (proxy/application/fallback_router.py) ───────────────────────
__init__(..., stream_resilience_enabled: bool = False)        # NEW ctor kwarg (additive; default-off)
async def stream_resilient(
    payload: dict[str, Any], upstream: CompletionUpstream | None = None,
) -> tuple[bytes | None, AsyncIterator[bytes]]:
  # Only called by the use case when the flag is ON. Builds the attempt list:
  #   alias with candidates -> strategy_order(alias, candidates)         (fallover across deployments)
  #   plain model id        -> [model_id]                                (single attempt; no retry — boundary)
  # open_stream(mid) = _resolve_upstream(upstream).stream({**payload, "model": mid})
  # on_fallover(frm,to) = self._inc_counter(alias=alias, from_model=frm, to_model=to, outcome="stream_fallover")
  # returns open_resilient_stream(attempts=..., open_stream=..., on_fallover=...)
  # ALL attempts fail pre-byte -> the helper re-raises UpstreamUnavailableError (caller maps to 502).
def stream(payload, upstream=None) -> AsyncIterator[bytes]:   # UNCHANGED (byte-identical when flag off)

# ── (C) CompletionUseCase.stream (proxy/application/use_cases.py) ────────────────────────
__init__(..., stream_resilience_enabled: bool = False)        # NEW ctor kwarg (additive; default-off)
# In stream(), replace ONLY the gen-acquisition block (flag-gated; OLD path verbatim when off):
#   if model_router is not None and self._stream_resilience_enabled:
#       first_chunk, gen = await model_router.stream_resilient(body, upstream=upstream)   # may raise → 502
#   elif model_router is not None:
#       gen = model_router.stream(body, upstream=upstream); first_chunk = None            # OLD path
#   else:
#       gen = upstream.stream(body); first_chunk = None                                   # OLD path
#   <SAME existing except (UpstreamUnavailableError, CircuitOpenError): record 502 + raise UPSTREAM_UNAVAILABLE>
# _wrapped() yields `first_chunk` (when not None) BEFORE `async for chunk in gen`, and includes it
#   in `collected` so extract_usage_from_sse + billing + span are UNCHANGED (single bill, status 200).
# Post-first-byte failure inside `gen` keeps today's except (UpstreamUnavailableError|CircuitOpenError)
#   → record status=502, stop. NO replay, NO fallover (the commit boundary).

# ── (D) Config (core/config.py:Settings) ───────────────────────────────────────────────
upstream_stream_resilience_enabled: bool = Field(default=False)   # env GATEWAY_STREAM_RESILIENCE_ENABLED

# ── (E) Wiring ──────────────────────────────────────────────────────────────────────────
# main.py: FallbackModelRouter(..., stream_resilience_enabled=settings.upstream_stream_resilience_enabled)
#          app.state.stream_resilience_enabled = settings.upstream_stream_resilience_enabled
# deps.py:get_completion_use_case: pass stream_resilience_enabled=<settings flag> to CompletionUseCase

Schema: NONE — no DB. Reads: config flag + model-group candidates (in-memory) + circuit/health state.
        Network: opens a candidate stream per pre-first-byte attempt; a discarded attempt is fully
        consumed/closed by raising before any chunk. Billing: single-bill on the SERVED attempt only
        (post-stream extract_usage_from_sse); discarded attempts extract no usage. Metric: a new
        "stream_fallover" outcome on the existing fallback counter. Providers + retry seam UNCHANGED.
```

Status: FROZEN @ v1 — approved by Tin (auto mode delegated freeze, 2026-06-15)
Least-sure flag surfaced at freeze: [contract] the pre-first-byte boundary is "await the FIRST
  `__anext__()` of a candidate stream." For OpenRouter that is the true first network byte; for
  Anthropic/Gemini the adapter BUFFERS the entire upstream stream before yielding the first TRANSLATED
  chunk — so for those two providers the fallover decision is delayed until the whole upstream stream is
  read (bounded by the existing read timeout). This is SAFE for correctness (the client sees nothing
  until commit, so no replay risk and no double-serve) but couples fallover latency to provider
  buffering. Cost: MEDIUM (latency on the buffering-provider fallover path); mitigations: bounded by the
  upstream read timeout, default-off, and OpenRouter (the default provider) is truly incremental.
  Secondary [spec] streaming 4xx triggers (context-window/content-policy) are OUT — providers only guard
  ≥500 on the stream path so a streaming 4xx is invisible today; only transport failures
  (≥500/timeout/network/circuit-open) trigger streaming fallover (non-streaming task 2 covers 4xx). LOW risk.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 95% on the NEW helper (streaming_resilience.py); behavior-level on router + use case.
Plan (one test per scenario, asserting behavior not internals) — 17 tests, all RED for the right reason:
<test_plan>
  HELPER (test_open_resilient_stream.py — 8):
  - first_attempt_serves_no_fallover · fallover_on_pre_first_byte_unavailable · circuit_open_triggers_fallover
  - all_attempts_fail_raises · single_attempt_pre_byte_failure_raises · empty_upstream_stream_returns_none_first
  - committed_then_mid_stream_error_propagates_no_replay · fallover_callback_receives_each_skip_pair
  ROUTER (test_stream_resilient_router.py — 5):
  - alias_fallover_serves_second_candidate_and_increments_metric · alias_first_candidate_serves_no_fallover
  - plain_model_single_attempt_no_retry · all_candidates_fail_raises · sync_stream_unchanged_resolves_to_first_candidate
  USE CASE (test_stream_resilience_use_case.py — 4):
  - fallover_serves_second_candidate_single_bill · all_candidates_fail_raises_502_before_response
  - mid_stream_failure_after_first_byte_commits_no_replay · flag_off_is_byte_identical_no_fallover
</test_plan>
RED confirmed: 17 failed (ImportError on open_resilient_stream / missing stream_resilient + ctor kwargs).

Tests live in: `./tests/streaming_resilience/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/application/streaming_resilience.py` `apps/gateway/src/gateway/proxy/application/fallback_router.py` `apps/gateway/src/gateway/proxy/application/use_cases.py` `apps/gateway/src/gateway/proxy/api/deps.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/main.py` `apps/gateway/tests/streaming_resilience/`
Strategy (ordered batches): 1. NEW helper streaming_resilience.py (open_resilient_stream) → green the 8 helper tests · 2. FallbackModelRouter: ctor flag + stream_resilient → green the 5 router tests · 3. CompletionUseCase: ctor flag + flag-gated peek path in stream() + _wrapped prepends first_chunk → green the 4 use-case tests · 4. config flag + main.py + deps.py wiring (no new tests; preserves default-off byte-identical).
Safety rule (feature-specific): the FIRST `__anext__` is the ONLY retriable point — after it commits, never re-open/replay; bill only the served attempt; flag-off path is the OLD code verbatim.
Code lives in: `apps/gateway/src/gateway/`
Constraints: do NOT change any test or the contract; providers + upstream_retry.py UNTOUCHED; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — streaming_resilience 20/20 (8 helper + 6 router + 4 use-case + 2 sync-circuit added at refute-read); regression floor 151 green across proxy + retry_policy (incl. the 3 "stream never retried" invariants) + model_fallbacks(+wiring, incl. f11 "stream resolves to first candidate") + error_aware_fallback (incl. test_stream_untouched) + response_caching. `make test-fast` exit 0; typecheck 0 errors; lint clean.
- [x] coverage did not decrease — new helper streaming_resilience.py fully exercised (commit/fallover/sync-circuit/all-fail/empty/no-replay); router stream_resilient + use-case peek covered behaviorally.
- [x] no test or contract was altered to force green — §3 contract FROZEN @ v1 unchanged; the 2 sync-circuit tests + the helper fix were a refute-read GAP fix, added via the sanctioned re-cross (phase tests → advance → advance re-snapshots tamper+scope). No existing test weakened.
- [x] the green was EARNED, not gamed — refute-read (11 probes below) FOUND a real gap (synchronous circuit-open escaping fallover) and fixed it with a failing-first test. No overfit: tests assert real served bytes, real per-candidate stream-call order, real single-bill call-counts, real metric labels, real 502-before-response.
- [x] concurrency / timing safe — the FIRST `__anext__` is the only retriable point; bounded by candidate count (≤5); a discarded attempt's generator raises through its `async with`, closing the upstream connection (no leak); after commit the mid-stream error lives in the returned iterator and surfaces to the drain loop (no replay). Anthropic/Gemini buffering couples fallover latency to their read timeout (least-sure flag; bounded, default-off).
- [x] no exposed secrets / injection / unexpected deps — metric labels carry only model ids (public), never keys; helper handles bytes opaquely (no parse/re-serialize → anti-tamper SSE invariant holds); no new third-party deps (reuses domain errors + existing metric).
- [x] layering & dependencies follow CONVENTIONS.md — pure-ish helper in application/streaming_resilience.py; orchestration in fallback_router.stream_resilient; the peek + billing stay in the use case; config flag + main/deps wiring. Providers + upstream_retry.py UNTOUCHED (the "stream never retried" invariant preserved by construction).
- [x] a person reviewed and approved the change — **HIGH-RISK human gate SIGNED OFF by Tin (2026-06-15): "Sign off PASS + commit" (autonomy: conservative).**

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — open_resilient_stream ← fallback_router.stream_resilient; stream_resilient ← use_cases.stream (flag-gated peek); ctor flags wired main.py→router + deps.py→use case from settings.upstream_stream_resilience_enabled + app.state.stream_resilience_enabled. Verified by the passing suite + grep.
- [x] DEAD-CODE (code) — no orphaned symbol; _empty_aiter used on the committed-empty path (test_empty_upstream_stream_returns_none_first); every new flag has a live reader.
- [x] SEMANTIC — frozen §3 re-read in full; build matches intent (the open_stream-inside-try refinement is faithful to "pre-first-byte failure → fallover" since a synchronous circuit-open IS pre-first-byte; documented below).

### Refute-read (adversarial — 11 probes; 1 real gap found + fixed)
1. ⚠ FOUND+FIXED: BoundCircuitBreakerUpstream + provider guard() raise CircuitOpenError SYNCHRONOUSLY at the stream() call (Explore-confirmed), but the helper opened the stream OUTSIDE the try → a circuit-open would escape instead of falling over. Fixed: moved open_stream() inside the try; added 2 helper + 1 router sync-circuit tests. ✓
2. flag-off byte-identical (3 retry "never retried" + f11 + test_stream_untouched all green) ✓  3. single-bill: fallen-over attempt raises pre-byte → no chunks collected → never billed (rec.call_count==1) ✓  4. no replay after first byte: mid-stream error in the returned iterator, helper try covers only first __anext__ ✓  5. all-fail → ProblemError 502 BEFORE StreamingResponse (peek in use case, caught by existing except) ✓  6. empty upstream → committed-empty (None, empty), no spurious fallover ✓  7. successful first candidate → byte-identical output + billing (peek+prepend) ✓  8. discarded connection closed via provider async-with on raise (no leak) ✓  9. security: model-id-only metric labels, opaque bytes, no key surface ✓  10. retry-seam stays complete()-only (providers untouched; invariant tests green) ✓  11. ordering uses sync _strategy_order (consistent with existing stream(); load/health gates intentionally not on the stream path — v6 boundary) ✓

### GATE RECORD
Outcome: PASS
Reviewed by: Tin (HIGH-RISK human gate, explicit sign-off "Sign off PASS + commit") · date: 2026-06-15

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
