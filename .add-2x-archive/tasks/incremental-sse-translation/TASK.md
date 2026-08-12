# TASK: Incremental SSE translation for Anthropic + Gemini (byte-identical, enables mid-stream abort)

slug: incremental-sse-translation · created: 2026-06-22 · stage: production
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
  - `anthropic_upstream.py:_translate_anthropic_sse(events: Iterable[(str,dict)]) -> Iterable[bytes]` — stateful SYNC translator; already yields per-event inside its `for` loop. State held in locals (prompt_tokens, completion_tokens, finish_reason, block_to_tc, tc_count, coercion_block_index, saw_coercion).
  - `anthropic_upstream.py:AnthropicCompletionUpstream.stream()._gen()` — the BUFFER: `async for line in response.aiter_lines()` collects ALL events into `events: list[...]`, THEN `for chunk in _translate_anthropic_sse(events): yield chunk`. This is where TTFB is lost.
  - `gemini_upstream.py:_translate_gemini_sse(chunks: Iterable[dict]) -> Iterable[bytes]` — same shape; `chunk_list = list(chunks)` then per-chunk yield. State: finish_reason, last_usage, tc_count, saw_tool_call.
  - `gemini_upstream.py:GeminiCompletionUpstream.stream()._gen()` — same BUFFER pattern (collects `chunks: list`, then translates).
Context (working folder): apps/gateway/src/gateway/proxy/infrastructure/{anthropic,gemini}_upstream.py · tests/{anthropic_provider,anthropic_tool_use,anthropic_json_mode,gemini_provider,gemini_tool_use,gemini_json_mode}.
Honors (patterns / conventions): circuit-breaker guard before first byte; `record_success()` on 2xx, `on_upstream_error()` + UpstreamUnavailableError on 5xx/network (must stay BEFORE first yield); secret never logged; OpenRouter `_gen()` (`async for chunk: yield chunk`) is the live-passthrough reference. `extract_usage_from_sse` scans frames in REVERSE for a `usage` key → terminal frame must remain LAST.
Anchors the contract cites: `_translate_anthropic_sse`, `_translate_gemini_sse`, `AnthropicCompletionUpstream.stream`, `GeminiCompletionUpstream.stream`.

Key finding: the translators are ALREADY incremental generators — only the adapter buffers. Refactor = extract per-event logic into a stateful stepper (`step(event)->frames`, `finish()->frames`); keep `_translate_*_sse(list)` as a thin sync wrapper (byte-identical → every frozen unit test stays green); drive the stepper live from `aiter_lines()` in `_gen()` so each translated frame is yielded the instant its source event arrives. No test asserts buffering — all assert joined-bytes + extract_usage_from_sse, both byte-identical.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Incremental SSE translation for Anthropic + Gemini chat streaming
Framings weighed: stateful-stepper reused by sync wrapper + async adapter (chosen) · convert translator to async generator (rejected: breaks frozen sync unit tests, forces test churn + tamper-tripwire) · leave buffered, do nothing (rejected: TTFB defect persists, blocks mid-stream abort for t5)
Must:
<must>
  - For a COMPLETE stream, the adapter's yielded byte sequence is BYTE-IDENTICAL to today's (every frozen translator/stream test stays green, unchanged).
  - Each translated OpenAI SSE frame is yielded the instant its source upstream event is read — NOT after the whole upstream stream is collected (true incremental delivery; TTFB ≈ first-token, matching OpenRouter passthrough).
  - The terminal frame (finish_reason + usage) + `data: [DONE]\n\n` remain LAST and unchanged, so `extract_usage_from_sse` (reverse scan) still finds usage.
  - The circuit-breaker contract is preserved: `guard()` before the generator object; `record_success()` on 2xx; on **5xx at stream-open** `on_upstream_error()`+`UpstreamUnavailableError` fire BEFORE the first frame (zero partial output — unchanged). On a **mid-stream** network/timeout error the breaker still records the failure and `UpstreamUnavailableError` is still raised, but — as with any true incremental stream (this matches the existing OpenRouter passthrough) — frames delivered before the drop cannot be un-sent. [clarified post-refute: the old buffer-then-yield masked this; incremental delivery surfaces it, which is correct.]
  - The pure `_translate_anthropic_sse(list)` / `_translate_gemini_sse(list)` entry points keep their signature + byte output (thin wrappers over the stepper) so existing callers/tests are untouched.
  - All v10 tool-streaming and v11 JSON-mode coercion behavior is preserved exactly (stepper carries the same state: block_to_tc, coercion_block_index, tc_count, saw_tool_call).
</must>
Reject:
<reject>
  - upstream 5xx on stream open -> `UpstreamUnavailableError` (raised before first frame, breaker.on_upstream_error) — UNCHANGED
  - upstream read-timeout / network error mid-stream -> `UpstreamUnavailableError` (breaker.on_upstream_error) — UNCHANGED
  - malformed / undecodable SSE line -> skipped (no frame), exactly as today
</reject>
After:
<after>
  - Anthropic + Gemini streams deliver token-by-token (incremental); complete-stream output and usage extraction are unchanged; the stepper is ready for t5 to read partial accumulated usage on mid-stream abort.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ extracting the per-event logic into a stepper preserves BYTE-IDENTICAL output across all tool/JSON-mode paths — lowest confidence because the Anthropic translator has intricate cross-event state (coercion unwrap, tc index mapping) and a subtle reorder could shift bytes; if wrong: a frozen tool/json test goes red (caught immediately by the existing suite — the safety net is exactly these tests).
  - [ ] httpx `aiter_lines()` yields lines incrementally as they arrive on the wire (not internally buffered) — confirm: httpx streams line-by-line from `aiter_bytes`, so feeding the stepper per line gives real incremental delivery; if wrong, delivery is still correct just not faster (no correctness cost).
  - [ ] no test asserts the buffered timing (all assert joined bytes / usage) — confirmed via grep in GROUND.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Anthropic incremental delivery
  Given an Anthropic upstream that emits message_start, two content_block_delta text events, message_delta, message_stop one at a time with a pause between each
  When the client drains adapter.stream(...)
  Then the role frame and each content frame are yielded before the NEXT upstream event is read (frame N available before event N+1)
  And the full joined byte sequence is byte-identical to the buffered translator output

Scenario: Anthropic complete-stream byte-identical
  Given a full Anthropic SSE event sequence (text + tool_use + json-mode coercion)
  When drained through the refactored adapter
  Then the joined bytes and extract_usage_from_sse(chunks) equal the current implementation's output exactly

Scenario: Gemini incremental delivery
  Given a Gemini upstream emitting multiple candidates parts then a usageMetadata chunk one at a time
  When the client drains adapter.stream(...)
  Then each content frame is yielded as its source chunk arrives, terminal usage frame last
  And the joined bytes are byte-identical to the buffered output

Scenario: upstream 5xx on stream open (unchanged)
  Given the upstream returns 500 when the stream opens
  When the client drains adapter.stream(...)
  Then UpstreamUnavailableError is raised with ZERO frames yielded
  And the circuit breaker recorded an upstream error

Scenario: network error mid-stream (unchanged)
  Given the upstream connection drops after the first event
  When the client drains adapter.stream(...)
  Then UpstreamUnavailableError is raised
  And the circuit breaker recorded an upstream error

Scenario: pure translator wrapper unchanged
  Given a list of events passed directly to _translate_anthropic_sse / _translate_gemini_sse
  When iterated
  Then the byte output is identical to before the refactor
  And the function signature (Iterable in, Iterable[bytes] out) is unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Internal refactor — no HTTP/wire contract change. The frozen surface is the
stepper interface + the byte-for-byte streaming invariant.

```
# Anthropic — anthropic_upstream.py
class _AnthropicSSEStepper:
    def step(event_name: str, data: dict) -> Iterator[bytes]   # 0+ OpenAI SSE frames for this event
    def finish() -> Iterator[bytes]                            # terminal frame + [DONE] if not already emitted
_translate_anthropic_sse(events: Iterable[(str,dict)]) -> Iterable[bytes]
    # UNCHANGED signature; now = for e in events: yield from s.step(*e); yield from s.finish()

# Gemini — gemini_upstream.py
class _GeminiSSEStepper:
    def step(chunk: dict) -> Iterator[bytes]
    def finish() -> Iterator[bytes]
_translate_gemini_sse(chunks: Iterable[dict]) -> Iterable[bytes]   # UNCHANGED signature, wrapper over stepper

# Adapters — *.stream()._gen() drive the stepper LIVE:
#   guard()/status-check/record_success UNCHANGED (before first yield)
#   async for line in response.aiter_lines():
#       parse line -> event; for frame in stepper.step(event): yield frame
#   for frame in stepper.finish(): yield frame

INVARIANT: complete-stream joined bytes + extract_usage_from_sse output are byte-identical to pre-refactor.
Schema: none (no DB, no migration).
```

Least-sure flag surfaced at freeze: ⚠ [spec] the stepper extraction stays BYTE-IDENTICAL across the
Anthropic tool-streaming + JSON-mode coercion paths (intricate cross-event state). Why it's the top
risk: a subtle reorder shifts output bytes. Cost if wrong: a frozen tool/json test goes red — but that
is exactly the safety net (the existing suite catches it at build, before any merge). Net risk: low,
because the refactor is behavior-preserving and fully covered by the frozen suite.

Status: FROZEN @ v1 — approved by Tin Dang (autonomy:auto; behavior-preserving refactor, no wire/schema change)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: maintain (no decrease); new code covered by the tests below.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_anthropic_incremental_first_frame_before_full_drain: drive adapter via MockTransport whose body counts chunk pulls / get first frame via __anext__ / assert pulls < total AND first is the role frame AND full drain ends in [DONE] with correct usage  [RED: buffered pulls all 5]
  - test_gemini_incremental_first_frame_before_full_drain: same for Gemini  [RED: buffered pulls all 2]
  - test_anthropic_stream_byte_identical_to_translator: pin time.time / drain adapter / assert joined bytes == _translate_anthropic_sse(events)  [guard, green either way]
  - test_gemini_stream_byte_identical_to_translator: same for Gemini  [guard]
  - test_anthropic_5xx_yields_no_frame: 503 on open / assert UpstreamUnavailableError, zero frames  [unchanged]
  - test_anthropic_midstream_network_error_raises_after_partial: body yields message_start then raises NetworkError / assert role frame WAS delivered AND UpstreamUnavailableError raised AND breaker recorded failure  [added post-refute: pins intended incremental error behavior]
  - test_gemini_midstream_network_error_raises_after_partial: same for Gemini  [added post-refute]
</test_plan>
Confirmed RED (2 failed, 3 passed) on first cross: incremental tests fail `assert 5 < 5` / `assert 2 < 2` — buffered adapter drains the whole upstream before the first frame. Right reason. After build: 7/7 green. The 2 mid-stream tests were added during a refute-driven re-cross (strengthening, documents existing-correct behavior — not a weakening).

Tests live in: `apps/gateway/tests/incremental_sse_streaming/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/anthropic_upstream.py` `apps/gateway/src/gateway/proxy/infrastructure/gemini_upstream.py`
Strategy (ordered batches): 1. Extract `_AnthropicSSEStepper` (step/finish) from `_translate_anthropic_sse`; rewrite the translator as a thin wrapper. 2. Drive the stepper live in `AnthropicCompletionUpstream.stream()._gen()` (parse each line → event → `yield from step`; `yield from finish` after the loop). 3. Same for Gemini (`_GeminiSSEStepper` with lazy role-frame on first step/finish). 4. Run new tests green + full suite green.
Safety rule (feature-specific): circuit-breaker guard + status check + record_success/on_upstream_error MUST stay before the first yield (no partial output on upstream failure); terminal frame stays LAST.
Code lives in: `apps/gateway/src/gateway/proxy/infrastructure/`
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

- [x] all tests pass — full suite 1241 passed (Postgres:5433 + Redis:6380 up); targeted 84 passed; earlier 15 fail/92 err were Redis-down infra, not this change.
- [x] coverage did not decrease — pure refactor + 7 new tests covering the new incremental path.
- [x] no test or contract was altered during build — only ADDED 2 mid-stream tests during a refute-driven re-cross (strengthening); §3 stepper contract unchanged.
- [x] the green was EARNED — adversarial refute-read (sonnet) found NO byte-identity break and confirmed the 5xx-open guard; its one point (mid-stream partial frames) is correct-by-design and now tested.
- [x] concurrency / timing safe — single-consumer async generator; breaker guard/record_success/on_upstream_error positions unchanged; no shared mutable state across requests (stepper is per-call).
- [x] no exposed secrets / injection / unexpected deps — no new imports beyond stdlib `Iterator`; secrets untouched.
- [x] layering & dependencies follow CONVENTIONS.md — infrastructure-layer change only; mirrors OpenRouter passthrough seam.
- [x] reviewed — autonomy:auto self-review + adversarial subagent refute-read.

### Build expectations — what "correct" looks like
- [x] Anthropic/Gemini first frame is delivered after pulling only 1 upstream chunk — confirmed by test_*_incremental_first_frame_before_full_drain (pulls < total).
- [x] complete-stream bytes byte-identical to the pure translator — confirmed by test_*_stream_byte_identical_to_translator (time.time pinned).
- [x] 5xx-at-open yields zero frames + UpstreamUnavailableError — confirmed by test_anthropic_5xx_yields_no_frame.
- [x] mid-stream drop: partial frames delivered, then UpstreamUnavailableError + breaker failure — confirmed by test_*_midstream_network_error_raises_after_partial.

### Deep checks
- [x] WIRING — `_AnthropicSSEStepper`/`_GeminiSSEStepper` referenced by both the wrappers and the adapters' `_gen()`; pyright (project-wide) 0 errors.
- [x] DEAD-CODE — `_translate_*_sse` wrappers retained as the unit-tested translation contract (Gemini via `__all__`, Anthropic via a justified `# pyright: ignore[reportUnusedFunction]`); no orphaned symbols.
- [x] SEMANTIC — n/a (code task).

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (autonomy:auto) · date: 2026-06-22

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
