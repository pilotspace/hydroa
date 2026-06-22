# TASK: Incremental AWS EventStream decode for Bedrock streaming

slug: bedrock-incremental-stream · created: 2026-06-22 · stage: production
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
  - `bedrock_eventstream.py:decode_event_stream(data: bytes) -> Iterator[(dict,bytes)]` — FROZEN v20 single-pass decoder over the COMPLETE wire buffer (prelude+CRC+headers). Pure stdlib.
  - `bedrock_upstream.py:_converse_stream_to_openai_sse(events: list[(str,dict)], *, model_id) -> Iterator[bytes]` — buffered translator; per-event yields (messageStart→role, contentBlockDelta→content, messageStop→capture stop, metadata→capture usage), ALWAYS emits terminal frame + [DONE] after the loop.
  - `bedrock_upstream.py:BedrockCompletionUpstream.stream()._gen()` — the BUFFER: `buf = b"".join([c async for c in resp.aiter_bytes()])` reads the ENTIRE binary EventStream, decodes ALL via decode_event_stream, then translates. TTFB ≈ full generation.
Context (working folder): apps/gateway/src/gateway/proxy/infrastructure/{bedrock_eventstream,bedrock_upstream}.py · tests/{bedrock_streaming,bedrock_tool_use,bedrock_provider,bedrock_verify}.
Honors (patterns / conventions): SigV4 sign==wire URL lock-step (v20 %3A rule) UNCHANGED; fail-closed credential read before first byte; breaker guard/record_success/on_upstream_error BEFORE first yield; secret never logged; t3 stepper pattern (`step`/`finish` + thin buffered wrapper kept byte-identical) is the precedent.
Anchors the contract cites: `decode_event_stream`, new `aiter_event_stream`, `_converse_stream_to_openai_sse`, new `_BedrockSSEStepper`, `BedrockCompletionUpstream.stream`.

Key finding: two layers buffer — (1) the EventStream byte decode (`b"".join` then decode_event_stream), (2) the Converse→OpenAI translation (list in, terminal after loop). BOTH must go incremental. Plan: add `aiter_event_stream(AsyncIterator[bytes])` that emits (headers,payload) as each length-prefixed frame completes (peek prelude→wait for total_len→parse), refactor `decode_event_stream` to share a `_parse_frame` helper (byte-identical), add `_BedrockSSEStepper` (step/finish; terminal in finish), and drive both live in `_gen()` off `resp.aiter_bytes()`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Incremental AWS EventStream decode + Converse→OpenAI translation for Bedrock streaming
Framings weighed: incremental frame-decoder + stepper, share `_parse_frame` with frozen decoder (chosen) · keep buffering, do nothing (rejected: TTFB defect + blocks t5 cost-abort) · pull in botocore eventstream (rejected: adds heavy dep, breaks pure-stdlib v20 contract)
Must:
<must>
  - Complete-stream output is BYTE-IDENTICAL to today's (every frozen bedrock_streaming/tool_use/provider/verify test stays green).
  - `decode_event_stream(full_buffer)` keeps its exact signature + behavior + error messages (refactor only extracts a shared `_parse_frame`).
  - New `aiter_event_stream(AsyncIterator[bytes])` yields each (headers,payload) the instant its length-prefixed frame completes on the wire (peek 12-byte prelude → wait for total_len → CRC-validate → parse), buffering only the partial tail.
  - The adapter yields each OpenAI frame as its source ConverseStream event arrives (incremental); the terminal frame (finish_reason+usage) + [DONE] is emitted ONCE after the last event (via `_BedrockSSEStepper.finish()`), LAST.
  - SigV4 sign==wire URL lock-step, fail-closed credential read, and breaker guard/record_success/on_upstream_error-before-first-yield are all UNCHANGED.
Reject:
<reject>
  - upstream >=400 at stream open -> `UpstreamUnavailableError` (before first frame, breaker.on_upstream_error) — UNCHANGED
  - connect/timeout/network error -> `UpstreamUnavailableError` (breaker.on_upstream_error) — UNCHANGED (mid-stream: partial frames may precede it, as with any incremental stream)
  - prelude/message CRC mismatch -> `EventStreamError` (corrupt frame) — UNCHANGED semantics; incremental decoder distinguishes "need more bytes" (wait) from "corrupt" (raise)
  - stream ends with a partial trailing frame -> `EventStreamError` (truncation) — same as the buffered decoder
</reject>
After:
<after>
  - Bedrock streams deliver token-by-token; complete-stream bytes + usage extraction unchanged; the stepper exposes accumulated partial usage for t5's mid-stream abort billing.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ httpx `aiter_bytes()` chunk boundaries do NOT align with EventStream frame boundaries — lowest confidence because TCP/httpx chunking is arbitrary; the incremental decoder MUST handle a frame split across chunks AND multiple frames in one chunk; if wrong (naive per-chunk decode): corrupt/dropped frames. Mitigated by a running tail buffer + total_len gating, covered by a split-mid-frame test.
  - [ ] refactoring `decode_event_stream` to share `_parse_frame` stays byte-identical — confirm: same CRC checks, same error strings; frozen bedrock tests are the net.
  - [ ] AWS sends only complete frames before EOF on normal completion (so the tail buffer is empty at end) — true per the EventStream spec; a non-empty tail = truncation → raise.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: incremental frame delivery
  Given a Bedrock ConverseStream whose binary frames arrive in separate byte chunks (messageStart, contentBlockDelta, ..., messageStop, metadata)
  When the client drains adapter.stream(...)
  Then the role frame is yielded after pulling only the first frame's bytes (not the whole response)
  And the full joined output is byte-identical to the buffered translator

Scenario: frame split across chunk boundaries
  Given a single EventStream frame whose bytes are delivered split across two aiter_bytes chunks
  When aiter_event_stream consumes the chunks
  Then exactly one (headers,payload) is yielded once the second chunk completes the frame
  And no bytes are dropped or duplicated

Scenario: multiple frames in one chunk
  Given two complete frames concatenated in a single byte chunk
  When aiter_event_stream consumes it
  Then both frames are yielded in order from that one chunk

Scenario: complete-stream byte-identical
  Given a full ConverseStream event sequence (text + tool_use)
  When drained through the refactored adapter
  Then the joined bytes equal the current implementation's output exactly

Scenario: CRC mismatch surfaces as corruption (not truncation)
  Given a complete frame whose message CRC is wrong
  When aiter_event_stream consumes it
  Then EventStreamError is raised

Scenario: upstream >=400 at open (unchanged)
  Given Bedrock returns 500 at stream open
  When the client drains adapter.stream(...)
  Then UpstreamUnavailableError is raised with zero frames
  And the breaker recorded an upstream error

Scenario: decode_event_stream unchanged
  Given a full multi-frame buffer passed to decode_event_stream
  When iterated
  Then the (headers,payload) tuples are identical to before the refactor
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Internal refactor — no HTTP/wire contract change. Frozen surface = the new
incremental decoder + stepper interfaces and the byte-identical invariant.

```
# bedrock_eventstream.py
def _parse_frame(msg: bytes, off: int) -> tuple[dict[str,str], bytes]   # one complete frame → headers+payload (CRC+headers parse, shared)
def decode_event_stream(data: bytes) -> Iterator[tuple[dict[str,str], bytes]]   # UNCHANGED signature/behavior; now uses _parse_frame
async def aiter_event_stream(chunks: AsyncIterator[bytes]) -> AsyncIterator[tuple[dict[str,str], bytes]]
    # running bytearray tail; peek 12B prelude → prelude CRC → wait for total_len → _parse_frame → yield → drop frame
    # EOF with non-empty tail → EventStreamError (truncation)

# bedrock_upstream.py
class _BedrockSSEStepper:
    def __init__(self, *, model_id: str)
    def step(event_type: str, payload: dict) -> Iterator[bytes]   # role/content/tool frames; capture stop+usage
    def finish() -> Iterator[bytes]                               # terminal finish_reason+usage frame + [DONE] (always once)
_converse_stream_to_openai_sse(events, *, model_id) -> Iterator[bytes]   # UNCHANGED signature, now wrapper over stepper

# BedrockCompletionUpstream.stream()._gen() drives BOTH live:
#   guard()/cred/status/record_success UNCHANGED (before first yield)
#   async for (headers,payload) in aiter_event_stream(resp.aiter_bytes()):
#       for frame in stepper.step(headers.get(":event-type",""), json.loads(payload)): yield frame
#   for frame in stepper.finish(): yield frame

INVARIANT: complete-stream joined bytes byte-identical to pre-refactor; decode_event_stream output unchanged.
Schema: none (no DB, no migration).
```

Least-sure flag surfaced at freeze: ⚠ [spec] httpx chunk boundaries don't align with EventStream frame
boundaries → the incremental decoder must correctly reassemble frames split across chunks and split
multiple frames in one chunk. Cost if wrong: corrupt/dropped Bedrock frames (billing + content). Mitigated
by the tail-buffer + total_len gating design and dedicated split/concat tests; frozen bedrock suite guards
byte-identity.

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

Coverage target: maintain (no decrease); new decoder + stepper covered below.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_aiter_frame_split_across_chunks: one frame split into 2 chunks → exactly 1 (headers,payload)
  - test_aiter_multiple_frames_one_chunk: 2 frames in 1 chunk → both, in order
  - test_aiter_matches_buffered_decode[one-chunk|per-frame|7-byte]: parametrized chunkings == decode_event_stream(full)
  - test_aiter_crc_mismatch_raises: corrupt message CRC → EventStreamError
  - test_aiter_truncated_tail_raises: stream ends mid-frame → EventStreamError
  - test_bedrock_incremental_first_frame_before_full_drain: counting body → first frame after pulls < total  [RED: buffered pulls all 6]
  - test_bedrock_stream_byte_identical: time pinned → adapter drain == _converse_stream_to_openai_sse(events)
  - test_bedrock_5xx_yields_no_frame: 500 at open → UpstreamUnavailableError, zero frames
  - test_bedrock_midstream_network_error_after_partial: role frame delivered, then NetworkError → UpstreamUnavailableError + breaker failure
  - test_bedrock_stepper_finish_idempotent: [added post-refute] second finish() yields nothing (no duplicate [DONE])
</test_plan>
Confirmed RED on first cross (ImportError: aiter_event_stream missing); after build 56 green (incl frozen bedrock_streaming/tool_use/provider/verify). Refute-read (sonnet) found NO frame-decode/byte-identity break across all chunkings; idempotency test added post-refute.

Tests live in: `apps/gateway/tests/bedrock_incremental_stream/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/bedrock_eventstream.py` `apps/gateway/src/gateway/proxy/infrastructure/bedrock_upstream.py`
Strategy (ordered batches): 1. Extract `_read_prelude` + `_parse_frame`; refactor `decode_event_stream` (byte-identical). 2. Add `aiter_event_stream` (tail buffer + total_len gating). 3. Extract `_BedrockSSEStepper`; wrapper byte-identical. 4. Drive both live in `_gen()`. 5. Green + refute + idempotency.
Safety rule (feature-specific): CRC validation unchanged; "need more bytes" never mistaken for "corrupt"; SigV4 sign==wire URL lock-step preserved; terminal frame emitted once (idempotent finish).
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

- [x] all tests pass — full suite 1254 passed (PG:5433 + Redis:6380); 56 bedrock-focused green.
- [x] coverage did not decrease — refactor + new decoder/stepper covered by 10 new tests.
- [x] no test or contract was altered during build — added 1 idempotency test post-refute (strengthening); §3 contract unchanged.
- [x] the green was EARNED — adversarial refute-read (sonnet) found NO frame-decode/byte-identity break across 8 chunkings (1-byte, 7-byte, per-frame, mid-prelude, byte-8/12 boundaries, multi-frame); confirmed _read_prelude/_parse_frame byte-identical.
- [x] concurrency / timing safe — per-call stepper + per-call tail buffer (no shared state); finish() now idempotent.
- [x] no exposed secrets / injection / unexpected deps — pure stdlib decoder; SigV4 path untouched; secret never logged.
- [x] layering & dependencies follow CONVENTIONS.md — infrastructure-layer; mirrors the t3 stepper precedent + sibling adapters' record_success-at-open.
- [x] reviewed — autonomy:auto + adversarial subagent refute-read.

### Build expectations — what "correct" looks like
- [x] aiter_event_stream output == decode_event_stream(full) for ALL chunkings — confirmed by test_aiter_matches_buffered_decode (parametrized) + refute-read's 8-way check.
- [x] Bedrock first frame after pulling < all frames — confirmed by test_bedrock_incremental_first_frame_before_full_drain.
- [x] complete-stream bytes byte-identical — confirmed by test_bedrock_stream_byte_identical + frozen bedrock_streaming BS4/5/6 (independent hard-coded assertions).
- [x] 5xx-at-open: zero frames + UpstreamUnavailableError — confirmed by test_bedrock_5xx_yields_no_frame.
- [x] corrupt frame → EventStreamError (not silent) — confirmed by test_aiter_crc_mismatch_raises.

### Deep checks
- [x] WIRING — `aiter_event_stream`/`_read_prelude`/`_parse_frame`/`_BedrockSSEStepper` all referenced (adapter + decode_event_stream + wrapper); pyright 0 errors.
- [x] DEAD-CODE — `_converse_stream_to_openai_sse` wrapper retained as the unit-tested contract (justified `# pyright: ignore[reportUnusedFunction]`); no orphans.
- [x] SEMANTIC — n/a (code task).

### Refute-read dispositions
- BUG-2 (finish() not idempotent) → FIXED (added `_terminal_emitted` guard + test).
- BUG-1 (record_success at stream-open resets failure_count before a mid-stream body-drop) → ACCEPTED as consistent: Anthropic/Gemini/OpenRouter/Azure all record_success at open for streams. Special-casing Bedrock would be the inconsistency. Logged as a system-wide SPEC delta (streaming breaker success-timing) for a future cross-adapter decision — NOT a t4 regression (no frozen test asserted the old timing).

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
