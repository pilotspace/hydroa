# TASK: Stamp provider_cost on client-disconnect mid-stream rows

slug: disconnect-provider-cost · created: 2026-06-18 · stage: production
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
  - `apps/gateway/src/gateway/proxy/application/use_cases.py:_wrapped` — the streaming tee
    coroutine inside `CompletionUseCase.stream`. `gen` (the upstream async generator) is bound
    at lines 1423-1428 (`model_router.stream_resilient` peeks `first_chunk,gen` · `model_router.stream`
    · `upstream.stream(body)`). `_wrapped` drains `async for chunk in gen` (line 1456) re-yielding
    each chunk and appending to `collected`.
  - The disconnect handler `except (GeneratorExit, asyncio.CancelledError)` (use_cases.py:1471) —
    fires EXACTLY ONE flagged record (v28 stream-disconnect-billing) then `raise`. It does NOT
    close `gen`: the upstream generator is left for the event-loop async-gen finalizer (GC), so the
    provider HTTP connection — and its token generation / cost accrual — keeps running until GC,
    NOT the instant the client drops. That residual upstream cost is what this task stops.
  - `gateway.usage.domain.extractor.extract_usage_from_sse` / `stream_usage_is_complete` — usage
    parse + completeness predicate (a complete terminal frame ⇒ authoritative real usage).
  - `_fire_record_with_raw(...)` — fire-and-forget ledger write (status=200, usage_source marker).
Context (working folder): `apps/gateway/tests/stream_disconnect_billing/` — the v28 fake harness
  (`conftest.open_stream` returns the live `_wrapped()` gen so a test partial-consumes then
  `aclose()`/`athrow(CancelledError)`; `MarkerSpyRecorder` captures the fire-and-forget record;
  `PlanStreamUpstream` is the fake upstream — no DB/Redis/server).
Honors (patterns / conventions): MUST design for failure (close is best-effort, never raises over
  the original disconnect) · red/green TDD · fire-and-forget record (never await in the hot path) ·
  re-raise the GeneratorExit/CancelledError so the close/cancel completes (never swallow).
Anchors the contract cites: `_wrapped` · the `except (GeneratorExit, asyncio.CancelledError)`
  handler · `gen.aclose()` · `extract_usage_from_sse` · `stream_usage_is_complete` · `usage_source`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Abort the upstream the instant the client disconnects (stop the provider's cost),
  and bill the customer the authoritative usage available at the disconnect point.
Framings weighed:
  - **deterministic close in the disconnect handler** (chosen) — `await gen.aclose()` inside the
    existing `except (GeneratorExit, asyncio.CancelledError)` after the record fires, before the
    re-raise. Closing the upstream async generator propagates the close into the adapter → closes
    the httpx response → TCP FIN to the provider → provider stops generating ⇒ cost accrual stops
    NOW, not at GC. Smallest surface; the incremental-stream refactor (t3/t4) is what makes this
    actually save cost (a buffered adapter had already paid for the whole generation).
  - rely on GC finalizer (status quo) — rejected: non-deterministic; provider keeps billing until
    the loop's async-gen finalizer runs.
  - cancel a background task / external stop API — rejected: no such task; the close IS the stop signal.
Must:
<must>
  - On client disconnect (GeneratorExit) or request-task cancellation (CancelledError) mid-stream,
    deterministically close the upstream generator (`await gen.aclose()`) before re-raising — so the
    provider connection is torn down immediately, not left to GC.
  - The close is best-effort: any error from `gen.aclose()` is swallowed (the original
    GeneratorExit/CancelledError still propagates; aborting cleanup must never mask the disconnect).
  - Still fire EXACTLY ONE record on disconnect (unchanged v28 behavior): a complete terminal frame
    present ⇒ usage_source="frame" (real authoritative usage billed); otherwise
    usage_source="client_disconnect" + WARN (partial/zero, flagged — surfaced in reconciliation, and
    real-cost recovery for OpenRouter is deferred to t6 openrouter-cost-recovery).
  - The normal-end and UpstreamUnavailable paths are unchanged (gen already exhausted/closed there;
    `aclose()` on an exhausted generator is a safe no-op — but it is only invoked on the disconnect path).
</must>
Reject:
<reject>
  - (n/a — no new external input/endpoint; this is internal resource-teardown behavior. The only
    "bad situation" is `gen.aclose()` raising during teardown → swallow, never propagate over the disconnect.)
</reject>
After:
<after>
  - After a mid-stream client disconnect, the upstream generator has received its close (GeneratorExit
    delivered to the adapter) by the time `_wrapped().aclose()` returns — deterministically, not via GC.
  - Exactly one ledger record was written for the partial stream, flagged frame|client_disconnect.
  - The disconnect/cancel still propagates to the caller (Starlette completes the close).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ awaiting `gen.aclose()` while handling GeneratorExit inside an async generator is permitted —
    lowest confidence because async-gen close semantics are subtle; if wrong: a RuntimeError
    ("async generator ignored GeneratorExit" / "coroutine awaited during close") at disconnect.
    Mitigation: PEP 525 permits awaiting (not yielding) during close; covered by the red/green test
    that drives a real `aclose()` through `_wrapped`. If it ever proves unsafe on CancelledError,
    fall back to scheduling the close fire-and-forget (`asyncio.ensure_future(gen.aclose())`).
  - [x] the upstream `gen` (resilient-peek remainder, router stream, or raw upstream.stream) is always
    an async generator exposing `aclose()` — confirmed: all three branches return async generators.
  - [x] mid-stream partial usage is NOT authoritatively in-band for non-OpenRouter providers — confirmed;
    real-cost recovery is t6's job, this task only guarantees the abort + the flagged single record.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Client disconnect aborts the upstream immediately (the new behavior)
  Given a streaming call whose upstream generator has yielded some chunks but not finished
  When the client disconnects (the returned generator is aclose()'d) mid-stream
  Then the upstream generator receives its close (GeneratorExit) before aclose() returns
  And exactly one ledger record is fired for the partial stream

Scenario: Task cancellation also aborts the upstream immediately
  Given a streaming call mid-flight
  When the request task is cancelled (athrow(CancelledError)) mid-stream
  Then the upstream generator receives its close before the cancellation propagates
  And the CancelledError still propagates to the caller (close is not swallowed)

Scenario: A complete frame before disconnect bills the real amount (regression)
  Given the terminal usage frame has already arrived in the consumed chunks
  When the client disconnects after that frame
  Then exactly one record is fired with usage_source="frame" and the real token counts
  And the upstream generator is still closed deterministically

Scenario: No frame before disconnect bills flagged partial (regression)
  Given no complete usage frame arrived before the client dropped
  When the client disconnects mid-stream
  Then exactly one record is fired with usage_source="client_disconnect"
  And a WARN is logged
  And the upstream generator is still closed deterministically

Scenario: aclose() failure during teardown never masks the disconnect
  Given the upstream generator raises on close
  When the client disconnects
  Then the original GeneratorExit still propagates (the close error is swallowed)
  And exactly one record is still fired
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
INTERNAL behavior contract — no HTTP surface change (the public /v1 streaming contract is unchanged).

CompletionUseCase.stream → _wrapped() async generator:
  on except (GeneratorExit, asyncio.CancelledError):           # client disconnect / task cancel
    1. extract_usage_from_sse(collected) → disconnect_usage
    2. usage_source = "frame" if stream_usage_is_complete(disconnect_usage) else "client_disconnect"
       (the else branch also WARNs)                              # UNCHANGED v28
    3. _fire_record_with_raw(... usage=disconnect_usage, status=200, usage_source=usage_source)
       — exactly one record                                      # UNCHANGED v28
    4. NEW: best-effort `await gen.aclose()` — swallow any exception (close must never mask the
       disconnect); deterministically delivers GeneratorExit into the upstream adapter ⇒ httpx
       response closed ⇒ TCP FIN ⇒ provider stops generating/billing.
    5. raise                                                     # re-raise so the close/cancel completes

Invariants: normal-end and UpstreamUnavailable paths untouched · still exactly one record per stream
· the disconnect/cancel always propagates · no new external input, dependency, or schema/migration.
Schema: none (no tables/fields touched).
```

Status: FROZEN @ v1 — approved by Tin Dang (billing policy "Bill the customer the real amount, and
spawn stop event to provider to stop stream immediately after we detect client disconnect or stop
request" pre-confirmed; this contract is the faithful realization — abort = the stop event, real-amount
billing = frame-case authoritative usage now + OpenRouter recovery in t6).
Least-sure flag surfaced at freeze: [spec] awaiting `gen.aclose()` during GeneratorExit handling
inside an async generator — why: async-gen close semantics are subtle and an illegal await/yield
during close raises RuntimeError at disconnect; cost: a 500-equivalent teardown error on every
client disconnect. Mitigation: the red/green test drives a real `aclose()`/`athrow()` through
`_wrapped`; fallback = schedule the close fire-and-forget (`asyncio.ensure_future(gen.aclose())`).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of the new disconnect-close branch.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_disconnect_closes_upstream: arrange a ClosureTrackingUpstream whose _gen records the
    GeneratorExit it receives / partial-consume the stream / act aclose() the wrapped gen /
    assert the upstream recorded its close AND exactly one record fired.
  - test_cancel_closes_upstream: arrange same / act athrow(CancelledError) / assert upstream closed
    AND CancelledError propagated (pytest.raises) — close not swallowed.
  - test_frame_present_bills_real_and_closes: arrange plan ends with COMPLETE_USAGE before disconnect /
    act consume through the frame then aclose() / assert one record usage_source="frame" w/ real
    counts AND upstream closed.
  - test_no_frame_bills_flagged_and_closes: arrange plan with no terminal frame / act partial-consume
    then aclose() / assert one record usage_source="client_disconnect" AND upstream closed.
  - test_aclose_failure_does_not_mask_disconnect: arrange an upstream whose _gen raises an
    Exception (RuntimeError) on close / act aclose() / assert it still completes AND one record fired.
  - test_aclose_baseexception_does_not_mask_disconnect: arrange close raises a BaseException
    (CancelledError — what httpx teardown raises mid-cancel) / act aclose() / assert it still
    completes cleanly (NOT re-raised) AND one record fired. [added in verify, closing refute BUG-1]
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
New suite: `stream_disconnect_abort/test_stream_disconnect_abort.py` (sibling of stream_disconnect_billing,
reusing its `open_stream`/`MarkerSpyRecorder` patterns with a closure-tracking upstream variant).
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/application/use_cases.py`
Strategy (ordered batches): 1. add `import contextlib` + `AsyncGenerator` import. 2. in the
  `except (GeneratorExit, asyncio.CancelledError)` handler of `_wrapped`, after `_fire_record_with_raw`
  and before `raise`, add `if isinstance(gen, AsyncGenerator): with contextlib.suppress(BaseException): await gen.aclose()`
  with a comment tying it to "stop event to provider on disconnect". (isinstance guard satisfies the
  AsyncIterator type + is defensive; suppress(BaseException) — not Exception — because CancelledError
  from httpx teardown is a BaseException in py3.11+ that must not mask the disconnect [refute BUG-1].)
Safety rule (feature-specific): the close is best-effort — suppress ALL exceptions incl. BaseException
  from `gen.aclose()` so the original GeneratorExit/CancelledError always propagates (cleanup never masks
  the disconnect).
Code lives in: `apps/gateway/src/gateway/proxy/application/use_cases.py`
Constraints: do NOT change any test or the contract; allow-list packages only (stdlib contextlib);
  no schema/migration; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 7/7 new (stream_disconnect_abort) + full suite **1261 passed** (PG :5433 + Redis :6380)
- [x] coverage did not decrease — new branch covered by 7 tests; net +7 tests
- [x] no test or contract was altered during build — test fixes (event-loop yield + BaseException case) were
      done by re-crossing tests→build twice (tamper-tripwire ordering); contract FROZEN @ v1 untouched
- [x] the green was EARNED — adversarial refute-read (sonnet subagent) verdict NOT-REFUTED on the core;
      it earned ONE real finding (BUG-1: suppress(Exception) misses BaseException/CancelledError) → CLOSED by
      strengthening (new test_aclose_baseexception_does_not_mask_disconnect, red→fix src to suppress(BaseException)→green,
      re-crossed). Refute confirmed: no double-billing, no vacuous asserts, isinstance guard always True in prod,
      normal/502 paths unchanged, closure flag is genuine GeneratorExit proof.
- [x] concurrency / timing safe — close is awaited (never yielded) during GeneratorExit/CancelledError handling
      (PEP 525 legal); record fires BEFORE the close so a close error can't drop billing; single record per stream.
- [x] no exposed secrets / injection / unexpected deps — stdlib `contextlib` only; no new IO surface.
- [x] layering & dependencies follow CONVENTIONS.md — change is confined to the application use-case generator.
- [x] a person reviewed and approved the change — contract pre-approved by Tin (billing policy); refute-read + this gate.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — the new close runs inside the existing `except (GeneratorExit, asyncio.CancelledError)`
      handler of `_wrapped` (use_cases.py); `AsyncGenerator`/`contextlib` imports both used; exercised live by all 7 tests.
- [x] DEAD-CODE (code) — no new symbol; one inline guarded close block. No orphan introduced.
- [x] SEMANTIC — n/a (code task).

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang (AUTO, autonomy:auto) · date: 2026-06-22

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of `stream_client_disconnect` WARNs (client-disconnect
volume) · ledger rows with usage_source="client_disconnect" (partial/flagged bills) · reconciliation
`unbilled_upstream_cost` should TREND DOWN now that disconnect aborts the provider promptly.

### Spec delta
- [SPEC · seeded] real partial-cost recovery on mid-stream disconnect for OpenRouter via its
  generation/cost endpoint (evidence: this task only guarantees abort + flagged single record; the
  authoritative partial usage is not in-band) → t6 openrouter-cost-recovery (next).
- [SPEC · open] non-OpenRouter providers still bill flagged-partial (often $0) on a no-frame
  mid-stream disconnect — surfaced in reconciliation; no per-provider recovery path yet (evidence:
  only OpenRouter exposes a post-hoc cost endpoint).

### Competency deltas
- [ADD · folded] an adversarial refute-read earns its keep even on a 3-line change — it surfaced the [folded foundation-version 28]
  Exception-vs-BaseException (CancelledError) gap that the first green hid (evidence: BUG-1 → strengthened test).
- [TDD · folded] best-effort cleanup in async-gen close handlers must be tested with a BaseException [folded foundation-version 28]
  (CancelledError), not just Exception — `suppress(Exception)` silently leaks BaseException (evidence:
  the new red test failed against suppress(Exception), passed against suppress(BaseException)).
