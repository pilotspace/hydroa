# TASK: Stream-path pacing with bounded-wait backpressure

slug: stream-bandwidth-pacing · created: 2026-06-24 · stage: production
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
  CONSUMES (frozen by task 1 bandwidth-token-bucket §3):
  - `rate_limits/domain/ports.py:BandwidthBucket` — acquire(key_id, estimated_tokens, max_wait_s)
    bounded-wait pace-or-raise · reconcile · level. `BandwidthGrant`/`ConsumeResult` value objects.
  - `rate_limits/domain/errors.py:BandwidthExhaustedError(key_id, requested, retry_after_s)` — raised
    when the wait budget is spent; carries retry_after_s for the 503/Retry-After mapping.
  - `rate_limits/infrastructure/redis_token_bucket.py:RedisTokenBucket` /
    `rate_limits/application/passthrough.py:PassthroughBandwidthBucket` — wire one of these into app.state.
  - config knobs `bandwidth_tokens_per_sec` / `bandwidth_burst_tokens` / `bandwidth_max_wait_seconds`.

  CHANGES (this task owns):
  - `proxy/application/use_cases.py:CompletionUseCase.__init__` (line ~465) — add a
    `bandwidth_bucket: BandwidthBucket | None = None` param → `self._bandwidth_bucket` (default
    PassthroughBandwidthBucket), MIRRORING the `rate_limiter` injection at 470/483-484.
  - `proxy/application/use_cases.py:CompletionUseCase.stream._wrapped` (gen at 1599) — the PACING SEAM:
    the `async for chunk in gen:` loop (1617-1619) is where each outbound SSE chunk is gated by
    `await self._bandwidth_bucket.acquire(key_id, estimate, max_wait_s)` BEFORE `yield chunk`. The
    peeked `first_chunk` (1614-1616, the TTFB commit) is yielded UNPACED (never delay first byte).
  - mid-stream BandwidthExhaustedError → COMPOSE with the v35 error-frame seam (1631-1640): emit
    `_sse_error_frame("ERR_BANDWIDTH_EXHAUSTED", ...)` + `data: [DONE]` then return — a 503 status is
    impossible after headers are sent (same constraint as the UpstreamUnavailable branch at 1620).
  - `proxy/application/use_cases.py:CompletionUseCase.complete` (non-stream path) — acquire BEFORE
    returning the body; BandwidthExhaustedError → a real `ProblemError`/503 + Retry-After (headers not
    yet sent). (Reconcile estimate→real is task 3; this task charges the estimate + sheds.)
  - `proxy/api/deps.py:get_completion_use_case` (~117) — read `app.state.bandwidth_bucket` via
    getattr(...,None) and pass it into CompletionUseCase, MIRRORING the rate_limiter wiring at 117/178.
  - `main.py` (~653, next to `app.state.rate_limiter = RedisLuaRateLimiter(...)`) — construct
    `app.state.bandwidth_bucket = RedisTokenBucket(redis=..., rate=settings.bandwidth_tokens_per_sec,
    burst=settings.bandwidth_burst_tokens)` when rate>0 else `PassthroughBandwidthBucket()`.

  READ (mirror — NOT changed):
  - `_sse_error_frame(code, message) -> bytes` (use_cases.py:101) — the v35 error-frame builder to reuse.
  - `_fire_record_tpm` / the post-stream usage extraction at 1773 — where task 3 will later reconcile.

Context (working folder):
  - Token ESTIMATE per chunk: the milestone's chars/4 convention. An SSE chunk is `data: {json}\n\n`;
    the cheap, robust estimate is `max(1, len(chunk_bytes)//4)` (framing overhead is small + constant).
    Exact per-chunk token counts are unknowable mid-stream (only the terminal usage frame is authoritative
    — that is the v27/v35 finding) → estimate now, reconcile at close (task 3). DECISION for §1.
  - No new migration. No new docs/fixtures yet; tests in `apps/gateway/tests/stream_bandwidth_pacing/`.

Honors (patterns / conventions):
  - Default-OFF byte-identical (v36 shared decision): bandwidth_tokens_per_sec==0 ⇒ Passthrough wired ⇒
    the `async for` loop is byte-identical to today (acquire is an immediate no-op grant, zero Redis).
  - Fail-open is a floor: a Redis error inside acquire admits (task 1 guarantees this) — pacing NEVER
    becomes an availability gate on the stream.
  - Mid-stream errors can only be SSE frames, never status changes — the v35 precedent (1620-1640) is the
    exact pattern; ERR_BANDWIDTH_EXHAUSTED is a NEW error code joining ERR_UPSTREAM_* (error_catalog.py).
  - Injection seam: optional ctor param defaulting to a Passthrough, read from app.state via getattr —
    the SAME pattern as rate_limiter / budget_guard / cost_recovery (deps.py).
  - PROJECT.md IO invariant: bounded-wait budget = timeout (task 1); this task just passes max_wait_s.

Anchors the contract cites (§3 may name ONLY these):
  - CompletionUseCase.__init__ `bandwidth_bucket` param + `self._bandwidth_bucket` · the `_wrapped`
    pacing seam (acquire per chunk, first_chunk unpaced) · the ERR_BANDWIDTH_EXHAUSTED SSE error frame +
    [DONE] mid-stream · the non-stream 503 + Retry-After · per-chunk estimate `max(1, len(chunk)//4)` ·
    deps.py getattr wiring · main.py RedisTokenBucket/Passthrough construction · the 3 config knobs.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Wire the per-key bandwidth bucket into the completion path — pace streaming output, shed on budget exhaustion
Framings weighed:
  - Pace per OUTBOUND SSE chunk inside _wrapped's `async for` loop (CHOSEN) — meters what actually
    reaches the client; composes with the v35 error-frame seam; the only honest mid-stream pacing point.
  - Pace at the upstream READ side (before collecting) — rejected: paces what we fetch, not what the
    client receives; doesn't bound the client-visible throughput.
  - One up-front acquire(full-response) — rejected: response size is unknown before streaming; per-chunk
    is the only mechanism that works mid-stream.
Must:
<must>
  - INJECTION: CompletionUseCase.__init__ gains `bandwidth_bucket: BandwidthBucket | None = None` →
    `self._bandwidth_bucket = bandwidth_bucket or PassthroughBandwidthBucket()` (mirrors `rate_limiter`).
    deps.py reads `app.state.bandwidth_bucket` (getattr→None) and passes it; main.py constructs
    RedisTokenBucket when `bandwidth_tokens_per_sec > 0` else PassthroughBandwidthBucket.
  - STREAM PACING: in `_wrapped`, for each chunk drained from `gen` (NOT the peeked first_chunk), call
    `await self._bandwidth_bucket.acquire(authz.key_id, estimate, max_wait_s)` BEFORE `yield chunk`,
    where `estimate = max(1, len(chunk)//4)` and `max_wait_s = settings.bandwidth_max_wait_seconds`.
    The peeked `first_chunk` (TTFB commit) is yielded UNPACED (never delay the first byte).
  - MID-STREAM SHED: a BandwidthExhaustedError from acquire (the per-chunk wait budget is spent) →
    record usage for what already streamed (the bytes reached the client — truncation billing, same as
    the v35 upstream-error branch), emit `_sse_error_frame("ERR_BANDWIDTH_EXHAUSTED", ...)` then
    `data: [DONE]` (if not already present), then `return`. A 200 was already sent — no status change.
  - NON-STREAM PRE-FLIGHT SHED (Tin-chosen 2026-06-24): CompletionUseCase.complete acquires BEFORE the
    upstream call, AFTER _enforce_governance — `acquire(key_id, request_estimate, max_wait_s)` where
    `request_estimate = max(1, prompt_chars//4 + (body.get("max_tokens") or 0))`. On
    BandwidthExhaustedError → `raise ProblemError(503, "ERR_BANDWIDTH_EXHAUSTED", ...,
    headers={"Retry-After": str(exc.retry_after_s)})` — a real 503 (headers not yet sent), MIRRORING the
    existing 429 rate-limit raise (use_cases.py:634-644). The request is shed WITHOUT paying the upstream.
    (Reconcile estimate→real total at response close is task 3.)
  - MAX-WAIT INJECTION: CompletionUseCase.__init__ also gains `bandwidth_max_wait_s: float = 0.0` (from
    settings.bandwidth_max_wait_seconds via deps/main) so both stream + complete read self._bandwidth_max_wait_s
    (the use-case does not hold a Settings object — mirror how authz carries tpm_limit).
  - DEFAULT-OFF byte-identical: bandwidth_tokens_per_sec==0 ⇒ Passthrough ⇒ acquire is an immediate
    no-op grant ⇒ the `async for` loop + complete() are byte-identical to today (zero Redis, zero pacing).
  - FAIL-OPEN: a Redis error inside acquire admits (guaranteed by task 1) — pacing never fails a stream.
  - ERR_BANDWIDTH_EXHAUSTED is a NEW error code registered in error_catalog.py (503 for the non-shed
    pre-flight cases; mid-stream it is only an SSE frame body code).
</must>
Reject:
<reject>
  - mid-stream: a chunk's pace wait exceeds bandwidth_max_wait_seconds -> SSE frame
    `ERR_BANDWIDTH_EXHAUSTED` + [DONE] (status already 200; cannot 503 mid-stream).
  - (non-stream: NO reject — debit-only; a completed response is never shed. See ⚠ flag.)
</reject>
After:
<after>
  - The per-key bucket level reflects the streamed/returned token estimate; concurrent streams of one
    key share the single bucket (aggregate cap holds — milestone exit criterion 1).
  - On shed: the client received a parseable terminal error frame + [DONE] (never a silent truncation —
    composes with v35); a usage row exists for the streamed prefix.
  - Default-OFF / fail-open ⇒ behavior byte-identical to today.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ NON-STREAM is DEBIT-ONLY (counts toward the cap, never 503s a completed response) while Tin's
    intake answer was "queue with bounded wait → 503" generically. Lowest confidence because a strict
    reading wants non-stream to ALSO shed. But sheddding a non-stream response is only sensible
    PRE-UPSTREAM (estimate from request max_tokens, acquire before the upstream call) — post-upstream the
    money is already spent. If Tin wants pre-flight non-stream shedding: add a request-side estimate +
    an acquire before the upstream call. Cost: a second acquire site + a request-token estimator.
  - [ ] per-chunk estimate = max(1, len(chunk_bytes)//4) (bytes/4). If framing overhead skews it, parse
    the delta content instead. One-line change; reconcile-at-close (task 3) corrects drift anyway.
  - [ ] first_chunk (TTFB) is yielded UNPACED — confirm we never pace the first byte (latency win).
  - [ ] mid-stream shed fires a usage record for the streamed prefix (truncation billing) — confirm vs
    skip-billing-on-shed.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: default-OFF stream is byte-identical
  Given bandwidth_tokens_per_sec=0 (PassthroughBandwidthBucket wired)
  When a client streams a chat completion
  Then every upstream SSE chunk is forwarded unchanged and in order
  And no acquire pacing/delay and no Redis call occur

Scenario: stream paces under a configured ceiling
  Given a per-key bandwidth bucket with a low tokens/sec and a multi-chunk stream
  When the client streams faster than the ceiling
  Then chunks after the first are paced (acquire awaits) so aggregate output stays within the ceiling
  And the first chunk (TTFB) is yielded without pacing

Scenario: bounded-wait exhausted mid-stream sheds with a terminal frame   # REJECTION
  Given a bucket whose wait budget is exceeded by the next chunk's estimate
  When acquire raises BandwidthExhaustedError mid-stream
  Then the stream emits data: {... "code":"ERR_BANDWIDTH_EXHAUSTED" ...} then data: [DONE] and stops
  And a usage record is written for the bytes already streamed (no silent truncation)

Scenario: aggregate cap across two concurrent streams of one key
  Given two simultaneous streams using the SAME api key
  When both drain chunks
  Then their combined throughput is paced against the one shared bandwidth:bucket:{key_id}
  And neither stream alone can exceed the per-key ceiling

Scenario: Redis error fails open (stream not interrupted)
  Given the bandwidth bucket's Redis backend errors on acquire
  When a client streams
  Then every chunk is admitted (no pacing, no shed) and the stream completes normally
  And a warning is logged with key_id only

Scenario: non-stream pre-flight acquire admits under budget
  Given a per-key bandwidth bucket with headroom and a non-streaming completion
  When the request's estimate (prompt//4 + max_tokens) is acquired before the upstream call
  Then the acquire grants and the upstream is called and a 200 is returned
  And the bucket is debited by the request estimate

Scenario: non-stream pre-flight shed returns 503 without paying upstream   # REJECTION
  Given a per-key bucket whose wait budget is exceeded by the request estimate
  When complete() acquires before calling the upstream
  Then it raises ProblemError 503 ERR_BANDWIDTH_EXHAUSTED with a Retry-After header
  And the upstream provider is NEVER called (no spend)

Scenario: disabled non-stream is byte-identical
  Given bandwidth_tokens_per_sec=0
  When a non-streaming completion runs
  Then no acquire/debit and no Redis call occur, the upstream is called once, and the response is byte-identical to today
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# Internal wiring of task-1's frozen BandwidthBucket into the completion path. No NEW public HTTP
# endpoint; the observable contract is the stream's terminal error frame + the non-stream debit.

# --- CompletionUseCase (proxy/application/use_cases.py) ---
__init__(..., bandwidth_bucket: BandwidthBucket | None = None, bandwidth_max_wait_s: float = 0.0)
    self._bandwidth_bucket = bandwidth_bucket or PassthroughBandwidthBucket()
    self._bandwidth_max_wait_s = bandwidth_max_wait_s   # from settings.bandwidth_max_wait_seconds

stream()._wrapped():  # the pacing seam
    if first_chunk is not None: yield first_chunk            # UNPACED (TTFB)
    async for chunk in gen:
        try:
            await self._bandwidth_bucket.acquire(
                authz.key_id, max(1, len(chunk)//4), settings.bandwidth_max_wait_seconds)
        except BandwidthExhaustedError:
            <record streamed-prefix usage>                    # truncation billing
            yield _sse_error_frame("ERR_BANDWIDTH_EXHAUSTED", "bandwidth limit exceeded")
            if not (collected and b"[DONE]" in collected[-1]): yield b"data: [DONE]\n\n"
            return
        collected.append(chunk); yield chunk

complete():  # non-stream PRE-FLIGHT shed (Tin-chosen)
    <after _enforce_governance, BEFORE the upstream/router call>
    request_estimate = max(1, prompt_chars//4 + (body.get("max_tokens") or 0))
    try:
        await self._bandwidth_bucket.acquire(authz.key_id, request_estimate, self._bandwidth_max_wait_s)
    except BandwidthExhaustedError as exc:
        raise ProblemError(503, "ERR_BANDWIDTH_EXHAUSTED", "bandwidth limit exceeded",
                           headers={"Retry-After": str(exc.retry_after_s)}) from None
    # ... then call upstream; task 3 reconciles request_estimate -> real total at close.

# --- error_catalog.py ---
ERR_BANDWIDTH_EXHAUSTED  # new code; 503 + Retry-After for any pre-flight/non-stream HTTP shed path,
                         # mid-stream it is only the SSE frame body's "code"

# --- HTTP shape (mid-stream shed; status already 200) ---
data: {"error": {"message": "bandwidth limit exceeded", "code": "ERR_BANDWIDTH_EXHAUSTED", "type": "..."}}
data: [DONE]

# --- wiring ---
deps.py get_completion_use_case: bandwidth_bucket = getattr(app.state, "bandwidth_bucket", None)
main.py: app.state.bandwidth_bucket = (RedisTokenBucket(redis, rate=cfg.bandwidth_tokens_per_sec,
         burst=cfg.bandwidth_burst_tokens) if cfg.bandwidth_tokens_per_sec > 0 else PassthroughBandwidthBucket())

Schema: NO DB migration. State is the task-1 Redis bucket only.
```

Status: FROZEN @ v1 — approved by Tin Dang (2026-06-24). Resolution: NON-STREAM uses PRE-FLIGHT shedding
(estimate from prompt//4 + max_tokens, acquire BEFORE the upstream call, 503 + Retry-After on exhaustion,
upstream never paid) — chosen over debit-only. Stream paces per-chunk (first byte unpaced), sheds via the
v35 ERR_BANDWIDTH_EXHAUSTED SSE frame + [DONE]. Default-OFF byte-identical; fail-open. Changing this
contract = change request back to SPECIFY.

Least-sure flag surfaced at freeze:
  [contract] NON-STREAM pacing semantics — pre-flight estimate-then-503 (Tin-chosen) vs debit-only. The
    pre-flight estimate (prompt//4 + max_tokens) is COARSE: max_tokens is an upper bound, so a request may
    be shed on a budget it wouldn't actually have consumed. Cost if too aggressive: lower max_wait or rely
    on task-3 reconcile to refund the unused estimate immediately after. Resolved: Tin chose pre-flight 503.
  [scenario] mid-stream shed bills the streamed prefix (truncation billing). Cost if wrong: one record
    call moves; the bytes already reached the client so billing them matches the v35/TTS precedent.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (gateway floor). Harness mirrors tests/stream_upstream_error_frame (the v35
analog): a controllable FakeBandwidthBucket (records acquire calls; raise_on_call=N raises
BandwidthExhaustedError on the Nth) + the streaming_resilience fakes (FakeAuthenticator/ModelChecker/
UsageRecorder/PlanStreamUpstream). Drives stream() (async-drain bytes) + complete() (status,body,x).
<test_plan>
  - test_default_off_stream_byte_identical: no bucket → chunks == upstream chunks (regression guard, green now)
  - test_stream_paces_each_chunk_first_unpaced: 3-chunk stream → acquire called 2× (A0 peeked unpaced), key_id+estimate+max_wait asserted
  - test_midstream_shed_emits_bandwidth_frame_and_done: raise_on_call=1 → A0, then ERR_BANDWIDTH_EXHAUSTED frame, then DONE, A1 absent, ≥1 usage record
  - test_stream_admits_when_bucket_grants: granting bucket → chunks unchanged (fail-open shape)
  - test_nonstream_preflight_admits_and_calls_upstream: complete() → 1 acquire (estimate≥max_tokens), upstream.complete called, 200
  - test_nonstream_preflight_shed_503_no_upstream: raise_on_call=1 → ProblemError status=503 code=ERR_BANDWIDTH_EXHAUSTED Retry-After=7, upstream NEVER called
  - test_default_off_nonstream_no_acquire: no bucket → upstream called once, 200 (regression guard, green now)
</test_plan>

Tests live in: `apps/gateway/tests/stream_bandwidth_pacing/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/application/use_cases.py` `apps/gateway/src/gateway/proxy/api/deps.py` `apps/gateway/src/gateway/main.py` `apps/gateway/src/gateway/core/error_catalog.py`
Strategy (ordered batches):
  1. error_catalog: add BANDWIDTH_EXHAUSTED ErrorSpec (503).
  2. use_cases imports + __init__: BandwidthBucket/PassthroughBandwidthBucket/BandwidthExhaustedError; bandwidth_bucket + bandwidth_max_wait_s params (default Passthrough/0.0).
  3. stream._wrapped: acquire per async-for chunk (first_chunk unpaced) + BandwidthExhaustedError → record prefix + ERR_BANDWIDTH_EXHAUSTED frame + [DONE] + return.
  4. complete(): pre-flight acquire after _enforce_governance + BANDWIDTH_EXHAUSTED.exc(Retry-After) on shed.
  5. deps.py: getattr app.state.bandwidth_bucket + settings.bandwidth_max_wait_seconds → pass to CompletionUseCase.
  6. main.py: construct RedisTokenBucket when rate>0 else PassthroughBandwidthBucket on app.state.
Safety rule (feature-specific): acquire ONLY catches BandwidthExhaustedError (task-1 guarantees fail-open swallows Redis errors inside acquire → admit). First byte (peeked first_chunk) NEVER paced. Default-OFF (Passthrough) ⇒ byte-identical. raise via the error_catalog (.exc()), never a raw ProblemError literal.
Code lives in: `./src/`
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

- [x] all tests pass — pacing suite 8/8; streaming-path regression 31/31; full suite (run below)
- [x] coverage did not decrease — gateway floor 87%+ held (full-suite run)
- [x] no test or contract was altered during build — §3 frozen untouched; tests only ADDED/STRENGTHENED (== 1 tightening + new double-bill guard test), then re-crossed tests→build to re-baseline
- [x] the green was EARNED, not gamed — adversarial refute-read (sonnet) ran; it found a real MAJOR double-bill on the disconnect-during-shed race → FIXED (`_bw_shed_handled` gate) + proved non-vacuous (neutralize guard → test fails call_count==2). max_tokens ValueError MINOR → hardened. No overfit/vacuous asserts remain.
- [x] concurrency / timing safe — pacing await added BEFORE yield; GeneratorExit/CancelledError still propagate (guarded handler re-raises); aggregate cap is task-1's atomic Lua; fail-open is task-1's guarantee (acquire only raises BandwidthExhaustedError)
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new deps; logs key_id only (task-1); error frame body carries a fixed code, no payload echo
- [x] layering & dependencies follow CONVENTIONS.md — raise via error_catalog `.exc()` (no raw ProblemError literal); bucket injected via app.state (mirrors rate_limiter); domain Protocol port
- [ ] a person reviewed and approved the change — PENDING Tin (commit/PR held per instruction)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] STREAM under ceiling: first chunk (TTFB) yielded UNPACED, every later chunk metered via acquire(key_id, max(1,len//4), max_wait) — confirmed by test_stream_paces_each_chunk_first_unpaced (2 acquire calls for a 3-chunk stream, key_id==KEY_ID)
- [x] STREAM shed: mid-stream BandwidthExhaustedError → ERR_BANDWIDTH_EXHAUSTED SSE frame + [DONE] + EXACTLY ONE prefix record, even on disconnect-during-shed — confirmed by test_midstream_shed_* (==1) + neutralize-guard proof (==2 fails)
- [x] NON-STREAM pre-flight: acquire AFTER governance / BEFORE upstream; shed → 503 ERR_BANDWIDTH_EXHAUSTED + Retry-After, upstream.complete NEVER called — confirmed by test_nonstream_preflight_shed_503_no_upstream (upstream.complete_calls == [])
- [x] DEFAULT-OFF byte-identical: no bucket → Passthrough → no acquire/pacing, output unchanged — confirmed by the two test_default_off_* guards

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — bandwidth_bucket referenced: __init__ stores it; stream._wrapped + complete() call acquire; deps.py reads app.state.bandwidth_bucket + settings.bandwidth_max_wait_seconds → CompletionUseCase; main.py builds RedisTokenBucket(rate>0)/Passthrough on app.state; BANDWIDTH_EXHAUSTED used in complete(). import smoke OK.
- [x] DEAD-CODE (code) — no orphaned symbol; every new name (BANDWIDTH_EXHAUSTED, params, _bw_shed_handled flag) is read on a live path
- [x] SEMANTIC — n/a (code task)

### GATE RECORD
Outcome: PASS
Evidence: pacing suite 8/8 · full gateway suite **1558 passed**, 19 deselected (exit 0, ~3m49s) ·
ruff clean · pyright clean (only the 1 pre-existing provider_generation_id error, unrelated, shifted
by my line additions) · refute-read (sonnet) MAJOR double-bill FIXED + proven non-vacuous · MINOR
max_tokens hardened. No test/contract weakened; tests only added/strengthened then re-crossed.
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a   (never for a security gap)
Reviewed by: AI auto-gate (autonomy:auto) · human approval (Tin) PENDING for commit/PR · date: 2026-06-24

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

- [SPEC · seeded] reconcile the pacing/pre-flight ESTIMATE → real usage at close so the bucket carries true debt, not chars//4 (evidence: stream debits max(1,len//4) per chunk + non-stream debits prompt//4+max_tokens, both unreconciled — task-3 `bandwidth-usage-reconcile` owns this; bucket.reconcile() already exists from task-1)
- [SPEC · seeded] emit a bandwidth-shed counter/metric (stream-frame sheds + non-stream 503s) for observability (evidence: shed events are invisible today — wave2 `bandwidth-counter-view` owns this)
- [SPEC · open] estimate formula chars//4 is coarse vs a real tokenizer; over-estimates pace healthy clients, under-estimates leak budget (evidence: no tokenizer in the pacing path — acceptable for v36, revisit if pacing is too aggressive)
- [SPEC · open] non-stream pre-flight debits even on a CACHE HIT (acquire runs before cache lookup) — a free cached response still consumes bandwidth/can 503 (evidence: acquire placed after governance, before credential/cache; intentional for now since cache hit still returns bytes, revisit if it sheds cheap hits)

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
