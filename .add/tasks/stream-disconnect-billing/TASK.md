# TASK: Flag a mid-stream client disconnect so it is never an unexplained silent $0

slug: stream-disconnect-billing · created: 2026-06-18 · stage: production
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
- `apps/gateway/src/gateway/proxy/application/use_cases.py:CompletionUseCase.stream._wrapped` (method
  `stream` lines 1297-1569; the nested `_wrapped()` async generator ~1432-1530) — drains the upstream `gen`,
  TEEs every chunk into `collected: list[bytes]`, yields it. Structure:
  `try: (yield first_chunk?) ; async for chunk in gen: collected.append(chunk); yield chunk`
  `except (UpstreamUnavailableError, CircuitOpenError): _fire_record(status=502); return`
  `finally: reset_provider_credential(...)`  — then AFTER the try/finally, the POST-STREAM RECORD BLOCK:
  `extracted_usage = extract_usage_from_sse(collected); usage_source = frame|stream_fallback;`
  `_fire_record_with_raw(usage=extracted_usage, status=200, usage_source=usage_source)` + TPM + span.
  **THE LEAK:** a client disconnect throws `GeneratorExit` at `yield chunk`. It is NOT an
  `UpstreamUnavailableError/CircuitOpenError` (it's a BaseException), so the `except` does not catch it;
  the `finally` runs (credential reset ONLY — its comment already names "closed early (GeneratorExit on
  client disconnect)"); then GeneratorExit propagates OUT of the generator, so the post-finally record
  block NEVER runs → **ZERO usage rows written** for a partially-streamed (paid-for) response. Distinct
  from v27's missing-frame case (which DOES write one flagged $0 row).
- `apps/gateway/src/gateway/proxy/application/use_cases.py:_fire_record_with_raw` (294-342) +
  `_dispatch_record` (154-188) — the fire-and-forget seam. `_dispatch_record` does
  `asyncio.ensure_future(usage_recorder.record(**kwargs))` (an INDEPENDENT task — survives the request
  task's teardown), filtering extras against the recorder's `supported_extras`. `_fire_record_with_raw`
  ALREADY forwards `usage_source` (v27). A disconnect record reuses this verbatim — no new kwarg/seam.
- `apps/gateway/src/gateway/usage/domain/extractor.py:extract_usage_from_sse(collected)` — on disconnect,
  `collected` holds the partial chunks; the usage frame is terminal so it is normally ABSENT → returns
  None → $0. If a frame did arrive pre-disconnect (rare) it bills it. `stream_usage_is_complete` (v27)
  classifies it.
- `apps/gateway/src/gateway/usage/infrastructure/orm.py:UsageRecordRow.usage_source` (TEXT NOT NULL
  DEFAULT 'frame', v27 migration b8e4f1a7c2d5) — values today 'frame' | 'stream_fallback'. A new value
  'client_disconnect' fits the existing TEXT column with NO new migration (no enum/check constraint).

Context (working folder): the stream generator is drained by Starlette's StreamingResponse; on client
disconnect uvicorn cancels the response task → the async generator's `aclose()` is called → `GeneratorExit`
is raised at the suspended `yield`. (`asyncio.CancelledError` is the sibling teardown signal to also weigh
at specify.) No DB schema change. New test suite `apps/gateway/tests/stream_disconnect_billing/`. The
fast no-DB streaming harness `tests/streaming_resilience/conftest.py` + the v27
`tests/stream_usage_completeness/conftest.py` (MarkerSpyRecorder declaring usage_source) are the reuse base;
a disconnect is driven by consuming the generator partially then calling `await gen.aclose()`.

Honors (patterns / conventions):
- **Every $0 is EXPLAINED** (v27, MILESTONE.md): a $0 stream row always names why via `usage_source`; this
  task extends the same marker to the disconnect path so EVERY stream outcome writes exactly one named row.
- **Accuracy is never an availability gate** (v12/v27): the disconnect record must never block, delay, or
  resurrect the (already-gone) response; it is one fire-and-forget record, errors swallowed.
- **Single fire-and-forget record per stream** (the streaming seam invariant): exactly one record on every
  path — the disconnect path currently writes ZERO; the fix makes it exactly one, never two.
- **Additive marker via the usage_source typed seam** (v27 `stream-usage-completeness`): no new migration,
  no new kwarg — reuse `_fire_record_with_raw(..., usage_source=...)`.
- **The byte-identical floor**: a normally-completing or upstream-erroring stream is UNCHANGED — the new
  handler only fires on GeneratorExit/early-close.

Anchors the contract cites: `CompletionUseCase.stream._wrapped` GeneratorExit path + the single
`_fire_record_with_raw`, `extract_usage_from_sse(collected)` → None on a pre-frame disconnect,
`usage_source='client_disconnect'` on the v27 TEXT column, and the must-re-raise-GeneratorExit rule (the
generator may catch it to record, but MUST re-raise so the close completes — never swallow it).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: stream-disconnect-billing — when a streaming chat client disconnects before the stream completes
(GeneratorExit / cancellation thrown into `_wrapped` mid-drain), the gateway writes EXACTLY ONE usage
record — flagged `usage_source='client_disconnect'` (or `'frame'` if a complete usage frame had already
arrived) — instead of today's ZERO rows, so a paid-for truncated stream is never an unrecorded silent $0.

Framings weighed:
- **flag one record on early-close, billing `extract_usage_from_sse(collected)`** (chosen) — add a
  GeneratorExit/CancelledError handler to `_wrapped` that fires the SAME v27 post-stream record (reusing the
  `usage_source` seam), marking genuinely-truncated streams `'client_disconnect'`. No content estimate, no
  migration, no HTTP change.
- count the partial streamed content tokens from `collected` (rejected — milestone Out-scope; it is the
  v27-rejected heuristic-token-math-in-the-money-path).
- leave the zero-row gap (rejected — it is the milestone's headline revenue leak: the platform paid the
  upstream for the truncated tokens, the tenant is billed nothing).

Must:
<must>
  - A streaming client disconnect (GeneratorExit raised into `_wrapped` during the drain, before normal
    completion) fires EXACTLY ONE usage record: status 200, usage = `extract_usage_from_sse(collected)`
    (normally None → $0), team_id + pii_masked preserved; `usage_source` = `'frame'` if
    `stream_usage_is_complete(extracted)` else `'client_disconnect'`; a `stream_client_disconnect` WARN is
    logged on the `'client_disconnect'` branch.
  - GeneratorExit MUST be RE-RAISED after the record is scheduled — the generator close completes; the
    response is never resurrected, delayed, or turned into an error. No `await` happens during the handler
    (the record is sync-scheduled fire-and-forget).
  - `asyncio.CancelledError` raised into the drain (the sibling teardown signal) is treated IDENTICALLY to
    GeneratorExit (one record, same marker rule, then re-raise).
  - Exactly ONE record on EVERY path is preserved: normal completion (frame / stream_fallback, v27) is
    unchanged; the upstream-error 502 path is unchanged; the disconnect path now writes one (was zero) —
    never two (a disconnect must not also reach the normal post-stream block).
  - The credential-reset `finally` still runs exactly once (unchanged). The disconnect record is scheduled
    via the existing fire-and-forget seam and never blocks the close.
  - A normally-completing stream and an upstream-erroring stream are BYTE-IDENTICAL to v27 — the new handler
    runs ONLY on early-close.
</must>
Reject:
<reject>
  - client disconnect with NO complete frame in `collected` -> one record, $0, usage_source=`client_disconnect`, WARN, status 200.
  - client disconnect AFTER a complete frame already arrived (rare) -> one record billing that frame, usage_source=`frame`, NO client_disconnect WARN.
  - a non-disconnect drain failure (UpstreamUnavailableError / CircuitOpenError) -> UNCHANGED v27 502 record, never a client_disconnect.
</reject>
After:
<after>
  - Every streaming outcome — complete, missing-frame, partial-frame, upstream-error, AND client-disconnect
    — writes EXACTLY ONE ledger row whose `usage_source` explains it; a truncated stream is queryable as
    `usage_source='client_disconnect'` and reconcilable against the upstream invoice.
  - No HTTP-contract change, no schema change (the v27 TEXT column absorbs the new value), no new dependency.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ a fire-and-forget `asyncio.ensure_future` scheduled from INSIDE the GeneratorExit handler runs to
    completion (pushes the event to Redis) before the loop discards the closing generator — lowest
    confidence because the record task is scheduled at the moment the response task is being torn down; if
    wrong: the record is scheduled but never flushed → still a $0 gap (no worse than today, the fix is just
    ineffective). Mitigated: `_dispatch_record` uses `ensure_future` → an INDEPENDENT task on the running
    loop (NOT a child of the cancelled request task), and the build proves it with a test that drives
    `gen.aclose()` and asserts the spy recorded exactly one row.
  - [ ] GeneratorExit AND asyncio.CancelledError are the disconnect signals (not some other BaseException);
    confirm at build by triggering `aclose()` / task-cancel in a test.
  - [ ] marking `'frame'` when a complete frame preceded the disconnect (vs always `'client_disconnect'`) is
    the desired provenance — near-zero cost either way (the billed COST is identical; only the marker differs).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: DC1 disconnect mid-stream with no usage frame is flagged, never silent
  Given a chat stream yielding content deltas with NO terminal usage frame
  When the client disconnects mid-drain (gen.aclose() raises GeneratorExit) before completion
  Then exactly one usage record fires: status 200, cost 0, usage_source == "client_disconnect"
  And a "stream_client_disconnect" WARNING is logged

Scenario: DC2 GeneratorExit is re-raised so the generator actually closes
  Given a chat stream being consumed
  When the client disconnects mid-drain
  Then GeneratorExit propagates out of _wrapped (the close completes; no hang, no swallow)
  And the response is not resurrected or turned into an error

Scenario: DC3 disconnect after a complete frame bills the frame as usage_source frame
  Given a chat stream whose terminal usage frame {prompt_tokens:10,completion_tokens:5,total_tokens:15} arrived
  When the client disconnects right after that frame (before the generator finishes)
  Then exactly one record fires billing those tokens (cost > 0), usage_source == "frame"
  And NO stream_client_disconnect warning is logged

Scenario: DC4 single-bill on disconnect (exactly one, not zero, not two)
  Given a chat stream that the client disconnects mid-drain
  When the stream is torn down
  Then the usage recorder is invoked EXACTLY once (today it is zero)
  And the normal post-stream record block does NOT also fire

Scenario: DC5 normal completion is byte-identical to v27
  Given a chat stream that completes normally with a complete usage frame
  When the stream drains to the end
  Then exactly one record fires with usage_source == "frame" and no client_disconnect warning
  And the billed usage is unchanged from v27

Scenario: DC6 upstream-error 502 path is unchanged (not a disconnect)
  Given a chat stream whose upstream raises UpstreamUnavailableError mid-drain
  When the error propagates
  Then exactly one record fires with status 502 (the v27 path), usage_source is NOT "client_disconnect"
  And no client_disconnect record is written

Scenario: DC7 cancellation mid-drain is treated like a disconnect
  Given a chat stream being consumed
  When asyncio.CancelledError is raised into the drain (task cancellation) before completion
  Then exactly one record fires, usage_source == "client_disconnect", cost 0, status 200
  And CancelledError is re-raised (the cancellation completes)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# Internal billing seam on the existing streaming chat path. NO HTTP-contract change,
# NO change to the streamed bytes, NO schema change, NO new upstream request.

## CompletionUseCase.stream._wrapped — add an early-close billing handler (use_cases.py)
async def _wrapped():
    collected: list[bytes] = []
    try:
        if first_chunk is not None: collected.append(first_chunk); yield first_chunk
        async for chunk in gen:
            collected.append(chunk); yield chunk
    except (UpstreamUnavailableError, CircuitOpenError):
        _fire_record(..., status=502, ...); return            # UNCHANGED v27 path (not a disconnect)
    except (GeneratorExit, asyncio.CancelledError):            # NEW — client disconnect / cancellation
        extracted = extract_usage_from_sse(collected)
        if stream_usage_is_complete(extracted):
            usage_source = "frame"                            # a complete frame arrived pre-close → real bill
        else:
            usage_source = "client_disconnect"                # genuinely truncated → flagged $0
            _log.warning("stream_client_disconnect",
                         extra={"model": model_id, "tenant_id": str(tenant_id)})
        _fire_record_with_raw(usage_recorder, tenant_id=tenant_id, key_id=key_id, model=model_id,
                              usage=extracted, status=200, team_id=team_id,
                              pii_masked=_stream_pii_masked, usage_source=usage_source)  # NO await
        raise                                                 # MUST re-raise so the close/cancel completes
    finally:
        if _stream_cred_token is not None:                    # UNCHANGED credential reset
            try: reset_provider_credential(_stream_cred_token)
            except ValueError: pass
    # POST-STREAM RECORD BLOCK — reached ONLY on normal completion (both error paths exit early). UNCHANGED v27:
    extracted_usage = extract_usage_from_sse(collected)
    usage_source = "frame" if stream_usage_is_complete(extracted_usage) else "stream_fallback"
    (warn if stream_fallback) ; _fire_record_with_raw(..., status=200, usage_source=usage_source) ; TPM ; span

## Marker value (usage_source TEXT column, v27 — NO migration)
  'frame' | 'stream_fallback' (v27) | 'client_disconnect' (NEW value, same TEXT column, no enum/check)

## Invariants (frozen)
  - Exactly ONE usage record on EVERY stream path; the disconnect path goes 0 → 1 record, never 2.
  - The disconnect handler does NO await and ALWAYS re-raises (GeneratorExit/CancelledError must finish the close).
  - usage_source on the disconnect path = 'frame' iff a complete frame is in `collected`, else 'client_disconnect'.
  - Normal-completion + upstream-error paths are BYTE-IDENTICAL to v27 (the new except only runs on early-close).
  - The fire-and-forget record is an INDEPENDENT ensure_future task (survives the request-task teardown).
```

Status: FROZEN @ v1 — approved by Tin Dang 2026-06-18 (approve & freeze as drafted, via AskUserQuestion).
Lowest-confidence flag surfaced at the freeze: [spec/build] a fire-and-forget `ensure_future` scheduled from
INSIDE the GeneratorExit handler flushes to Redis before the closing generator is discarded — proven by the
DC1/DC4 `gen.aclose()` tests (if they cannot go green, the timing assumption is falsified → escalate, do not
ship). Marker rule kept as drafted ('frame' if a complete frame arrived, else 'client_disconnect'). Changing
this frozen contract = change request back to SPECIFY.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 95% on the new disconnect branch in `_wrapped`.
Plan (one test per scenario; reuse the v27 streaming harness — drive a partial drain then `gen.aclose()`):
<test_plan>
  - DC1 test_disconnect_no_frame_flagged: stream [A0] (no usage frame) via PlanStreamUpstream + MarkerSpyRecorder;
    consume 1 chunk, `await gen.aclose()`; settle / assert one record, status 200, usage None, usage_source==
    "client_disconnect", caplog has "stream_client_disconnect".
  - DC2 test_generatorexit_reraised: assert `gen.aclose()` returns without raising and the generator is closed
    (a second `aclose()` is a no-op; no hang) — the handler re-raised GeneratorExit, not swallowed.
  - DC3 test_disconnect_after_complete_frame_bills_frame: stream [A0, COMPLETE_USAGE]; consume both chunks,
    `aclose()`; assert one record, usage_source=="frame", usage total_tokens==15 (cost>0), NO warn.
  - DC4 test_single_bill_on_disconnect: disconnect mid-drain; assert rec.call_count==1 (not 0, not 2).
  - DC5 test_normal_completion_unchanged: full drain [A0, COMPLETE_USAGE, DONE]; assert usage_source=="frame",
    one record, no client_disconnect warn (v27 regression).
  - DC6 test_upstream_error_502_not_disconnect: PlanStreamUpstream raises UpstreamUnavailableError mid-drain;
    assert one record status 502, usage_source != "client_disconnect".
  - DC7 test_cancellation_treated_as_disconnect: wrap the drain in a task, cancel it mid-drain; assert one
    record usage_source=="client_disconnect", and CancelledError propagated.
</test_plan>

Tests live in: `apps/gateway/tests/stream_disconnect_billing/` · MUST run red (no disconnect handler) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/application/use_cases.py` `apps/gateway/tests/stream_disconnect_billing/`
Strategy (ordered batches): 1. add the `except (GeneratorExit, asyncio.CancelledError)` handler to `_wrapped`
  (fire one record via the existing `_fire_record_with_raw` + usage_source seam, then re-raise); 2. confirm the
  post-stream block is reached only on normal completion (both error paths exit early). NO migration, NO new module.
Safety rule (feature-specific): the handler does NO `await` (sync fire-and-forget schedule) and ALWAYS re-raises;
  exactly one record per stream on every path; normal-completion + 502 paths stay byte-identical.
Code lives in: `apps/gateway/src/gateway/proxy/application/`
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

- [x] all tests pass — DC1–DC7 7/7 (DC1/DC3/DC4/DC7 were RED for the right reason pre-build [zero records],
      green after the handler; DC2/DC5/DC6 green-by-design guards). Streaming regression 45/45
      (streaming_resilience + v27 stream_usage_completeness + this suite). Full gateway suite 1188 passed
      (was 1181 + 7 new); the ONLY failures are 16 `tests/edge/*` live Docker+Envoy+TLS e2e (stack down,
      env-only, unrelated).
- [x] coverage did not decrease — the new disconnect branch in `_wrapped` is exercised by DC1 (no-frame),
      DC3 (frame-before-close), DC7 (cancellation); both marker branches + the WARN covered.
- [x] no test or contract was altered during build — §3 FROZEN @ v1 untouched. ONE test edit during VERIFY
      (DC2 += `_settle()` to close refute-read NIT-1) → tamper tripwire legitimately re-crossed via
      `phase tests` → `advance` ×2, clean, no heal burned.
- [x] the green was EARNED — independent adversarial refute-read (sonnet) = **EARNED-GREEN-WITH-NITS @ 0.93,
      0 blockers**; all 6 frozen invariants CONFIRMED by control-flow trace (proved single-bill via live
      simulation that the post-stream block is unreachable after the except `raise`). DC1's assertion is
      non-vacuous (requires the except to fire, the client_disconnect branch, AND supported_extras to carry
      usage_source). 3 NITs: NIT-1 (DC2 `_settle`) CLOSED; NIT-2 (var-name readability) harmless — the
      post-stream `usage_source` and the new `disconnect_source` are distinct names on distinct paths, no
      shadow; NIT-3 (real uvicorn loop-teardown untestable in unit) = documented residue → §7.
- [x] concurrency / timing safe — THE risky operation, and the freeze's lowest-confidence flag: a
      fire-and-forget record fired from INSIDE GeneratorExit/CancelledError handling. PROVEN it flushes:
      DC1/DC4 go green only because `_dispatch_record`'s `asyncio.ensure_future` schedules an INDEPENDENT
      task (not a child of the torn-down request task). The handler does NO `await` (cannot, during
      GeneratorExit) and ALWAYS re-raises. The 502 except is ordered FIRST (GeneratorExit/CancelledError are
      BaseException, never caught there) → no double-bill, no cross-contamination.
- [x] no exposed secrets / injection / unexpected deps — no new dependency, no new IO; the WARN logs only
      model_id + tenant_id (already logged on the v27 path); no payload/secret.
- [x] layering & dependencies follow CONVENTIONS.md — reuses the existing `_fire_record_with_raw` →
      `UsageRecordExtras` (usage_source) → recorder → flusher → ORM seam (v27 `stream-usage-completeness`);
      no new symbol, no new migration, no new cross-layer edge. The marker is a new TEXT value, not a schema change.
- [x] reviewed — auto-resolved under `autonomy: auto` on complete evidence (no security finding, no
      architecture residue; autonomy not lowered). The freeze flag was proven, not merely asserted.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — the new `except (GeneratorExit, asyncio.CancelledError)` branch in
      `CompletionUseCase.stream._wrapped` calls the EXISTING `extract_usage_from_sse` / `stream_usage_is_complete`
      / `_fire_record_with_raw` (all already imported + used on the v27 path). No new symbol introduced;
      confirmed via serena read of `_wrapped` + the refute-read's line-by-line trace.
- [x] DEAD-CODE (code) — no orphan: the branch is on the live disconnect path, proven reached by DC1/DC3/DC7;
      ruff clean (no unused import/var). pyright 0 errors (the IDE Pylance `structlog.contextvars` note is a
      pre-existing line-31 import + an IDE-venv artifact, NOT in the project pyright gate).
- [x] SEMANTIC — n/a (code task).

### GATE RECORD
Outcome: PASS
Reviewed by: auto-resolved (autonomy: auto — complete evidence; freeze's timing flag PROVEN by DC1/DC4; refute-read EARNED 0.93, NITs closed/documented) · date: 2026-06-18

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): the rate of `usage_records.usage_source='client_disconnect'` rows per
model/tenant — a rising rate means clients are dropping mid-stream and the platform is eating the upstream
cost (DC1 as the live monitor). The `stream_client_disconnect` WARN rate is the leading signal. A normal
baseline is non-zero (users navigate away); a SPIKE for one model suggests a slow/hanging upstream.

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] a `client_disconnect` row bills $0 even though partial tokens were streamed (and paid for
  upstream) — if production shows a material client_disconnect rate, weigh counting the partial `collected`
  content for that marker only (the v27-rejected content-estimate, scoped to truncated streams), or a
  post-hoc reconciliation from the provider invoice. Evidence: §1 framing (chosen flag-$0 over count-partial).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [TDD · folded] `gen.athrow(asyncio.CancelledError)` / `gen.aclose()` are the DETERMINISTIC way to unit-test an [folded foundation-version 26]
  async generator's disconnect/cancellation billing — they inject GeneratorExit/CancelledError at the exact
  suspended yield with no real-task race, far more reliable than create_task+cancel+sleep. Evidence: DC1/DC7
  deterministic single-shot; the spy records during the injected teardown.
- [ADD · folded] the freeze's lowest-confidence flag (fire-and-forget flushing from INSIDE GeneratorExit [folded foundation-version 26]
  handling) was PROVEN by making the test itself the falsifier (DC1/DC4 can only go green if the record
  fires) — a "the test is the proof of the risky assumption" pattern, not a hand-wave. Evidence: DC1/DC4
  red→green is exactly the timing proof.
- [ADD · folded] CARRIED RESIDUE (refute-read NIT-3, untestable in unit): the real uvicorn loop-teardown on a [folded foundation-version 26]
  production client disconnect is not proven by the unit suite — the independent-task architecture mitigates
  it, but an e2e/live check (disconnect a real stream, assert a client_disconnect ledger row) would close it.
  Evidence: refute-read 0.93 discount was entirely this scenario.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
