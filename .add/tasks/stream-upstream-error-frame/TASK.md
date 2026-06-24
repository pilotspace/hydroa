# TASK: Emit a terminal SSE error frame + [DONE] on mid-stream upstream failure

slug: stream-upstream-error-frame · created: 2026-06-24 · stage: production
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
- `apps/gateway/src/gateway/proxy/application/use_cases.py`
  - `_wrapped()` (L1587) — the streaming generator. The MID-stream catch `except (UpstreamUnavailableError, CircuitOpenError):` (L1608) fires a 502 usage record then a bare `return` → the SSE stream ENDS with NO error frame and NO `[DONE]`. **This is Finding B.** A `[DONE]`-waiting client (Helios) hangs/mis-parses.
  - The `except (GeneratorExit, asyncio.CancelledError):` catch (L1620) = CLIENT disconnect (client is gone) → OUT of scope: no frame, re-raise preserved (v34 disconnect billing).
  - `collected: list[bytes]` (L1588) accumulates yielded chunks for usage extraction; status already committed 200 at this point.
  - `UpstreamRateLimitedError` (task 1, subclass of UpstreamUnavailableError) is ALSO caught at L1608 mid-stream → the frame should carry its specific code.
- SSE wire format (mirror — no shared helper exists, adapters inline it):
  - `apps/gateway/src/gateway/proxy/infrastructure/anthropic_upstream.py:685-686` — `yield b"data: " + json.dumps(chunk).encode() + b"\n\n"` then `yield b"data: [DONE]\n\n"`.
  - `anthropic_upstream.py:600-610` — OpenAI error shape `{"error": {"message":<m>, "type":<t>, "code":<c>}}`.
- `apps/gateway/src/gateway/core/error_catalog.py` — code strings `ERR_UPSTREAM_UNAVAILABLE` + `ERR_UPSTREAM_RATE_LIMITED` (task 1) reused as the frame `code` (a string, NOT an HTTP status — status is already 200).

Context (working folder): chat streaming path only; the fix lives entirely in `_wrapped()`'s mid-stream upstream-failure catch. Confirmed live (2026-06-24 probe): rate-limited free models on a stream returned HTTP 200 + no [DONE].

Honors (patterns / conventions): mirror the adapter SSE format; INVARIANT — the upstream-SUCCESS stream stays byte-identical (exactly one [DONE], the upstream's); the client-disconnect path is untouched (v34); never emit a second [DONE] if the upstream already sent one.

Anchors the contract cites: `_wrapped()` mid-stream `except (UpstreamUnavailableError, CircuitOpenError)` catch · the OpenAI error-frame shape · the `[DONE]` sentinel · `UpstreamRateLimitedError` discrimination.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Terminal SSE error frame + [DONE] when an upstream failure hits an already-committed stream — so a [DONE]-waiting agent client never hangs.
Framings weighed: emit one OpenAI error chunk + a terminal [DONE] in the mid-stream catch, then return (chosen — minimal, mirrors the adapter SSE convention, no status change) · raise after the stream (rejected — status is already 200, can't change it; the client still hangs) · buffer the whole stream to detect failure before committing (rejected — kills incremental streaming / TTFB, contradicts v30/v34).
Must:
<must>
  - When an upstream failure (`UpstreamUnavailableError` / `CircuitOpenError`) is raised mid-stream (after the 200 committed, ≥0 chunks delivered), `_wrapped()` yields exactly ONE OpenAI-shaped error chunk (`data: {"error": {...}}\n\n`) then a terminal `data: [DONE]\n\n`, then returns.
  - The error chunk's `code` is `ERR_UPSTREAM_RATE_LIMITED` when the failure is an `UpstreamRateLimitedError`, else `ERR_UPSTREAM_UNAVAILABLE`.
  - The existing 502 usage record still fires (status=502) — unchanged.
  - If the upstream already emitted a terminal `[DONE]` in `collected` before failing, do NOT emit a second one (still emit the error chunk so the failure is visible).
  - CLIENT-disconnect (`GeneratorExit` / `asyncio.CancelledError`) path is UNCHANGED — no frame (the client is gone), re-raise preserved, v34 disconnect billing intact.
  - The upstream-SUCCESS stream stays byte-identical — exactly one [DONE] (the upstream's), no injected frame on the clean path.
</must>
Reject:
<reject>
  - mid-stream upstream failure -> emit `{"error":{code}}` chunk + `[DONE]` (never a silent truncation) -> code "ERR_UPSTREAM_UNAVAILABLE" | "ERR_UPSTREAM_RATE_LIMITED"
</reject>
After:
<after>
  - A client streaming through a mid-stream upstream failure receives a parseable error chunk + [DONE] and terminates cleanly — never hangs waiting for [DONE].
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The upstream stream does NOT already emit `[DONE]` before raising — lowest confidence because if an adapter yielded `[DONE]` then raised, a naive injection would send a SECOND `[DONE]` (some clients treat the first as end-of-stream and choke on trailing bytes). Mitigation: guard the terminal `[DONE]` on `b"[DONE]" not in the last collected frame`; always emit the error chunk. Cost: a double-[DONE] confuses strict clients.
  - [ ] Emitting an error chunk AFTER real content chunks is acceptable to OpenAI-wire clients (they read `data:` lines until `[DONE]`; an `{"error":...}` line is a recognized shape). Accept — matches how OpenRouter/Anthropic surface mid-stream errors.
  - [ ] The error chunk need not carry an HTTP status (status is already 200); the `code` string is the signal. Accept.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: mid-stream upstream-unavailable emits an error frame + [DONE]
  Given a stream that yields 2 chunks then raises UpstreamUnavailableError
  When the client drains the stream
  Then it receives the 2 chunks, then a data: {"error":{"code":"ERR_UPSTREAM_UNAVAILABLE"}} frame, then data: [DONE]
  And one usage_records row with status=502 is fired   # unchanged

Scenario: mid-stream rate-limit carries the rate-limit code
  Given a stream that yields 1 chunk then raises UpstreamRateLimitedError
  When the client drains the stream
  Then the error frame code is "ERR_UPSTREAM_RATE_LIMITED" followed by data: [DONE]

Scenario: mid-stream circuit-open emits the unavailable frame
  Given a stream that raises CircuitOpenError mid-flight
  When the client drains the stream
  Then it receives an error frame code "ERR_UPSTREAM_UNAVAILABLE" + data: [DONE]

Scenario: client disconnect emits NO frame (unchanged)
  Given a stream that raises GeneratorExit (client dropped) mid-flight
  When the generator is closed
  Then no error frame and no injected [DONE] are emitted
  And the v34 disconnect usage record path runs unchanged

Scenario: clean success stream is byte-identical
  Given an upstream stream that completes with its own data: [DONE]
  When the client drains the stream
  Then exactly one [DONE] is delivered and no error frame is injected

Scenario: no double [DONE] when upstream already terminated then raised
  Given a stream that yields data: [DONE] then raises UpstreamUnavailableError
  When the client drains the stream
  Then the error chunk is emitted but a second [DONE] is NOT appended
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# use_cases.py  _wrapped()  — MID-stream catch (status already 200; bytes, not HTTP)
except (UpstreamUnavailableError, CircuitOpenError) as exc:
    fire 502 usage record                                  # UNCHANGED
    code = "ERR_UPSTREAM_RATE_LIMITED" if isinstance(exc, UpstreamRateLimitedError)
           else "ERR_UPSTREAM_UNAVAILABLE"
    err = {"error": {"message": <safe text>, "type": "upstream_error", "code": code}}
    yield b"data: " + json.dumps(err).encode() + b"\n\n"   # ONE error chunk
    if b"[DONE]" not in (collected[-1] if collected else b""):
        yield b"data: [DONE]\n\n"                          # terminal sentinel (no dup)
    return

# small inlined helper (this file): _sse_error_frame(code: str, message: str) -> bytes
#   returns b'data: {"error":{...}}\n\n'  (mirrors anthropic_upstream stepper format)

# UNCHANGED: the (GeneratorExit, asyncio.CancelledError) catch (client disconnect) — no frame, re-raise.
# UNCHANGED: the clean-success path (upstream emits its own [DONE]).
Schema: none. No DB change, no new ErrorSpec (frame carries a code STRING; HTTP status stays 200).
```

Least-sure flag surfaced at freeze: [scenario] Whether to inject a terminal `[DONE]` at all after a mid-stream failure is the one point most likely to be reconsidered — OpenAI's own API often closes the socket on a mid-stream error WITHOUT a `[DONE]`, so some SDKs treat the error chunk itself as terminal. We emit `[DONE]` anyway (guarded against duplication) because the goal is that a `[DONE]`-waiting agent loop never hangs; if a specific client chokes on a post-error `[DONE]` we'd drop it. Cost: low (the error chunk is always emitted; only the trailing sentinel is in question). [contract] secondary: the error-frame `code` reuses the catalog code strings rather than a stream-specific schema.
Status: FROZEN @ v1 — approved by Tin Dang 2026-06-24 (AskUserQuestion: "Freeze — build it")
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% on the changed mid-stream catch branch.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_midstream_unavailable_emits_error_frame_and_done: fake stream yields 2 chunks then raises UpstreamUnavailableError / drain via HTTP / assert body contains the 2 chunks + a data:{"error":{"code":"ERR_UPSTREAM_UNAVAILABLE"}} line + a trailing data: [DONE] + 1 usage row status=502
  - test_midstream_ratelimit_frame_code: stream raises UpstreamRateLimitedError mid-flight / assert error frame code == ERR_UPSTREAM_RATE_LIMITED + [DONE]
  - test_midstream_circuit_open_frame: stream raises CircuitOpenError / assert ERR_UPSTREAM_UNAVAILABLE frame + [DONE]
  - test_client_disconnect_emits_no_frame: drive GeneratorExit into _wrapped (aclose) / assert NO error frame, NO injected [DONE], disconnect record path runs (mirror v34 disconnect test harness)
  - test_success_stream_byte_identical: upstream completes with its own [DONE] / assert exactly one [DONE], no error frame injected
  - test_no_double_done_when_upstream_already_done: stream yields data: [DONE] then raises UpstreamUnavailableError / assert error chunk present AND only one [DONE] total
</test_plan>

Tests live in: `apps/gateway/tests/stream_upstream_error_frame/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/application/use_cases.py`
Strategy (ordered batches): 1. add a small inlined `_sse_error_frame(code, message) -> bytes` helper (module-level in use_cases). 2. in `_wrapped()`'s mid-stream `except (UpstreamUnavailableError, CircuitOpenError) as exc:` — after the existing 502 record, yield the error frame (code by isinstance) then a guarded terminal [DONE], then return.
Safety rule (feature-specific): emit the terminal [DONE] ONLY if the last collected chunk isn't already a [DONE] (no double terminal); NEVER touch the GeneratorExit/CancelledError (disconnect) branch or the success path.
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

- [x] all tests pass — full suite **1530 passed @ 87.43%** (`uv run pytest -m 'not e2e'`, DB up). New `tests/stream_upstream_error_frame` 6/6 green; the 3 cross-suite CORRECTED tests green. (A 73f/503e run was purely the dev Postgres being down in this resumed session — `docker compose -f infra/docker-compose.dev.yml up -d --wait` fixed it.)
- [x] coverage did not decrease — 87.43% ≥ 87.40% task-1 baseline (+6 tests).
- [x] no test or contract was altered during build — §3 FROZEN @ v1 untouched. 3 pre-existing tests (test_dc6, two streaming_resilience) were CORRECTED in the TESTS phase (re-snapshotted at tests→build) — only the stale `chunks ==` equality relaxed for the INTENDED behavior change; every real invariant kept (no-fallover `up.stream_calls==[CAND_A]`, status=502, not-client_disconnect). v27 `test_sd8` precedent. Verified by refute-read H1.
- [x] the green was EARNED — adversarial refute-read (backend-expert sonnet) **SOUND 0.93**: 7 hypotheses (green-gamed / success-byte-identical / v34-disconnect-regression / double-[DONE]-guard / rate-limit-code / malformed-SSE / yield-after-record double-record) all REFUTED with probes; 0 BLOCKER, 3 NITs (stale comment FIXED; 2 documented trade-offs).
- [x] concurrency / timing safe — disconnect-during-injected-yield probed: GeneratorExit does NOT route to the sibling `except`, single 502 record, `finally` resets the partial-usage ContextVar. v34 disconnect billing intact.
- [x] no exposed secrets / injection / new deps — `_sse_error_frame` uses stdlib `json` only; fixed message; `json.dumps` escapes the payload.
- [x] layering follows CONVENTIONS.md — confined to the use-case streaming generator; mirrors the adapter SSE byte convention.
- [x] a person reviewed and approved — Tin Dang froze §3 ("Freeze — build it"); behavior-change test corrections per v27 precedent.

### Build expectations — what "correct" looks like
- [x] mid-stream UpstreamUnavailableError → prior chunks + `data: {"error":{"code":"ERR_UPSTREAM_UNAVAILABLE"}}` + `data: [DONE]` — SEF-1 (error_idx < done_idx) + test_dc6 body.
- [x] mid-stream UpstreamRateLimitedError → frame code `ERR_UPSTREAM_RATE_LIMITED` + `[DONE]` — SEF-2 (isinstance subclass).
- [x] 502 usage record still fires on mid-stream failure, not a client_disconnect — SEF-1 / test_dc6 (`status==502`).
- [x] clean-success byte-identical (exactly one [DONE], no injected frame) — SEF-5 (`_count_done==1`, `not _has_error_frame`).
- [x] client-disconnect emits NO frame + re-raises — SEF-4 (`client_disconnect` source).
- [x] no double [DONE] when upstream already sent one — SEF-6 (`done_count<=1`) + guard `if not (collected and b"[DONE]" in collected[-1])`.

### Deep checks
- [x] WIRING — new `_sse_error_frame` referenced from the mid-stream catch in `_wrapped()`; `import json` now used. Diff + green tests exercise the path.
- [x] DEAD-CODE — `_sse_error_frame` is the only new symbol and is called; no orphan.
- [x] SEMANTIC — refute-read read the full mid-stream / disconnect / success branches; except branches are siblings (no cross-routing); success never enters the modified branch.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (via Claude orchestration; refute-read SOUND 0.93) · date: 2026-06-24

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
