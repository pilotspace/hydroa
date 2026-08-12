# TASK: Map graceful mid-stream peer-close (httpx.RemoteProtocolError) to UpstreamUnavailableError across provider adapters

slug: stream-graceful-close-mapping · created: 2026-06-24 · stage: production
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
- Each provider adapter's streaming `_gen()` maps upstream transport failure mid-stream to `UpstreamUnavailableError` via `except (httpx.TimeoutException, httpx.NetworkError) as exc: self._breaker.on_upstream_error(); raise UpstreamUnavailableError(...) from None`. The 5 streaming sites:
  - `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py:288` (`_gen` @266)
  - `apps/gateway/src/gateway/proxy/infrastructure/anthropic_upstream.py:968` (`_gen` @909)
  - `apps/gateway/src/gateway/proxy/infrastructure/azure_upstream.py:206` (`_gen` @180)
  - `apps/gateway/src/gateway/proxy/infrastructure/gemini_upstream.py:778` (`_gen` @730)
  - `apps/gateway/src/gateway/proxy/infrastructure/bedrock_upstream.py:700` (`_gen` @655; already lists `httpx.ConnectError` too)
- THE GAP (Finding C, proven empirically 2026-06-24): `httpx.RemoteProtocolError` (raised when the peer GRACEFULLY closes the connection mid-body — "peer closed connection without sending complete message body") is a `ProtocolError`, NOT a `NetworkError` → it is NOT caught by these clauses → it escapes `_gen()` as a raw httpx error → use_cases `_wrapped()` mid-stream catch (`except (UpstreamUnavailableError, CircuitOpenError)`) does NOT see it → the v35 task-2 SSE error frame + [DONE] never fires → the client gets a truncated stream with no terminal frame (the exact Finding-B symptom, for the MOST COMMON real drop). Only a connection RESET (RST → `httpx.ReadError`, a NetworkError) is mapped today.
- OUT OF SCOPE: the non-stream `complete()`/embed/`countTokens` except sites (gemini @853 @902); gemini `_gen` @943 is a pure stub (raises directly, no httpx). The use_cases mid-stream catch + `_sse_error_frame` (task 2) are UNCHANGED — once the adapter maps RemoteProtocolError → UpstreamUnavailableError, the existing task-2 path fires automatically.

Context (working folder): `apps/gateway/src/gateway/proxy/infrastructure/` adapters only. Empirical proof: a stdlib socket server that sends 200 + 1 SSE chunk then FIN-closes → httpx raises `RemoteProtocolError` (NetworkError=False); RST-closes → `ReadError` (NetworkError=True). `/tmp/probe_midstream_drop.py`.

Honors (patterns / conventions): mirror each adapter's existing mid-stream mapping EXACTLY (breaker.on_upstream_error then `raise UpstreamUnavailableError(str(exc)) from None`); the v35 milestone goal = faithful agent-loop error signals; design-for-failure (a graceful upstream close is an upstream failure, not a client success).

Anchors the contract cites: the 5 streaming `_gen()` except clauses · `httpx.RemoteProtocolError` · `UpstreamUnavailableError` · the circuit breaker `on_upstream_error()` call.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Map a graceful mid-stream upstream peer-close (`httpx.RemoteProtocolError`) to `UpstreamUnavailableError` in every provider streaming adapter, so the v35 task-2 terminal SSE error frame + [DONE] fires for the common real drop (not only for connection resets).
Framings weighed: add `httpx.RemoteProtocolError` to each existing `except (TimeoutException, NetworkError)` tuple (chosen — minimal, mirrors the existing mapping line-for-line, narrow & intentional) · catch the broader `httpx.RequestError`/`httpx.HTTPError` (rejected — too broad, would swallow `LocalProtocolError` which is OUR bug, and other request errors that should surface differently) · fix it once at the use_cases `_wrapped()` layer (rejected — `_wrapped` is provider-agnostic and shouldn't know httpx; the breaker accounting lives in the adapter).
Must:
<must>
  - When a provider's streaming `_gen()` raises `httpx.RemoteProtocolError` mid-stream (after ≥0 chunks), the adapter maps it to `UpstreamUnavailableError` (and calls `self._breaker.on_upstream_error()`), exactly as it already does for `TimeoutException`/`NetworkError`.
  - This holds for all 5 streaming adapters: openrouter, anthropic, azure, gemini, bedrock.
  - The existing `TimeoutException`/`NetworkError`/`ConnectError`(bedrock) mappings are UNCHANGED (RemoteProtocolError is ADDED to the tuple, nothing removed).
  - The clean-success stream path and the non-stream `complete()` paths are UNCHANGED.
  - NO change to use_cases / the task-2 error-frame code — once the adapter maps the error, the existing mid-stream catch fires the frame.
</must>
Reject:
<reject>
  - graceful mid-stream peer-close (RemoteProtocolError) -> map to UpstreamUnavailableError (was: escaped unmapped -> truncated stream, no frame)
  - a `LocalProtocolError` (our own malformed request) -> NOT mapped here (stays a distinct error; only RemoteProtocolError is added)
</reject>
After:
<after>
  - A graceful upstream mid-stream close on any provider surfaces, downstream, as the v35 terminal SSE error frame (ERR_UPSTREAM_UNAVAILABLE) + [DONE] — the agent client never sees a silent truncation.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ `httpx.RemoteProtocolError` is the exception a real graceful mid-stream close surfaces under the adapters' httpx config — confidence HIGH (empirically proven via loopback FIN probe), but the real OpenRouter drop could also be an SSE error-chunk + clean close (no exception, passthrough) in some cases; if so this fix is still correct for the close-without-terminator case and harmless otherwise. Cost if wrong: the live FIN-close check would not trigger (SKIP), but the deterministic stub (task 4) FIN-closes to prove it.
  - [ ] `RemoteProtocolError` is not already caught by some broader handler upstream of `_gen()` — confirmed: grep shows no `RemoteProtocolError`/`ProtocolError`/`httpx.HTTPError` handling anywhere in `proxy/`.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: openrouter graceful mid-stream close maps to UpstreamUnavailableError
  Given an OpenRouter stream that yields 1 chunk then raises httpx.RemoteProtocolError
  When the adapter's stream() is drained
  Then UpstreamUnavailableError is raised (not a raw httpx error)
  And the circuit breaker recorded an upstream error

Scenario: anthropic graceful mid-stream close maps to UpstreamUnavailableError
  Given an Anthropic stream that yields 1 chunk then raises httpx.RemoteProtocolError
  When the adapter's stream() is drained
  Then UpstreamUnavailableError is raised

Scenario: azure graceful mid-stream close maps to UpstreamUnavailableError
  Given an Azure stream that yields 1 chunk then raises httpx.RemoteProtocolError
  When the adapter's stream() is drained
  Then UpstreamUnavailableError is raised

Scenario: gemini graceful mid-stream close maps to UpstreamUnavailableError
  Given a Gemini stream that yields 1 chunk then raises httpx.RemoteProtocolError
  When the adapter's stream() is drained
  Then UpstreamUnavailableError is raised

Scenario: bedrock graceful mid-stream close maps to UpstreamUnavailableError
  Given a Bedrock stream that yields 1 chunk then raises httpx.RemoteProtocolError
  When the adapter's stream() is drained
  Then UpstreamUnavailableError is raised

Scenario: existing network/timeout mappings remain (regression guard)
  Given an OpenRouter stream that raises httpx.ReadError (NetworkError) mid-stream
  When the adapter's stream() is drained
  Then UpstreamUnavailableError is still raised   # unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Adapter streaming _gen() error mapping (all 5 providers):
  BEFORE: except (httpx.TimeoutException, httpx.NetworkError[, httpx.ConnectError]) as exc:
              self._breaker.on_upstream_error()
              raise UpstreamUnavailableError(str(exc)) from None
  AFTER:  except (httpx.TimeoutException, httpx.NetworkError[, httpx.ConnectError],
                  httpx.RemoteProtocolError) as exc:
              self._breaker.on_upstream_error()         # unchanged body
              raise UpstreamUnavailableError(str(exc)) from None
  Effect: graceful mid-stream peer-close -> UpstreamUnavailableError -> (use_cases, task 2)
          terminal SSE error frame {code: ERR_UPSTREAM_UNAVAILABLE} + data: [DONE].
  No new symbols, no signature change, no migration. RemoteProtocolError ADDED to the tuple only.
Schema: none (no DB).
```

Status: FROZEN @ v1 — approved by Tin Dang 2026-06-24 (AskUserQuestion: "Add Finding-C task first (all adapters)")
Least-sure flag surfaced at freeze: [spec] whether a real graceful OpenRouter mid-stream drop actually surfaces as `httpx.RemoteProtocolError` (vs an SSE error-chunk + clean close = passthrough, no exception). Why it might be wrong: the 2026-06-24 probe proved FIN-close → RemoteProtocolError on a loopback socket, but the real provider's exact wire behavior on a mid-stream rate-limit was not directly observed. Cost if wrong: the live FIN-close check SKIPs; the fix is still correct/harmless for the close-without-terminator case and the deterministic stub (next task) FIN-closes to prove it. [test] the per-adapter MockTransport construction differs (contextvar credentials, __new__ shims) — a mis-built adapter could fail for the wrong reason; mitigated by mirroring each adapter's existing stream test + the ReadError regression guard.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% on the 5 changed except clauses (one test exercises each).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - Mechanism: a shared `_RemoteProtocolMidStream` httpx.AsyncByteStream that yields one valid SSE chunk then `raise httpx.RemoteProtocolError("peer closed connection without sending complete message body", request=...)`; wire it into each adapter via httpx.MockTransport returning `httpx.Response(200, stream=..., headers={"content-type":"text/event-stream"})` — mirrors each adapter's existing MockTransport stream tests.
  - test_openrouter_graceful_close_maps_to_unavailable: drain adapter.stream() / assert pytest.raises(UpstreamUnavailableError)
  - test_anthropic_graceful_close_maps_to_unavailable: same for AnthropicCompletionUpstream
  - test_azure_graceful_close_maps_to_unavailable: same for AzureOpenAICompletionUpstream
  - test_gemini_graceful_close_maps_to_unavailable: same for GeminiCompletionUpstream
  - test_bedrock_graceful_close_maps_to_unavailable: same for the Bedrock streaming adapter
  - test_openrouter_readerror_still_maps (regression guard): a stream raising httpx.ReadError still → UpstreamUnavailableError (proves the additive change didn't drop the existing mapping)
</test_plan>

Tests live in: `apps/gateway/tests/stream_graceful_close_mapping/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py` `anthropic_upstream.py` `azure_upstream.py` `gemini_upstream.py` `bedrock_upstream.py`
Strategy (ordered batches): 1. add `httpx.RemoteProtocolError` to each streaming `_gen()` except tuple (5 sites, body unchanged). 2. nothing else — the use_cases task-2 path fires automatically.
Safety rule (feature-specific): ADD to the tuple only — never remove an existing exception type; never touch the non-stream `complete()` except clauses or the success path.
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

- [x] all tests pass — full suite **1536 passed @ 87.42%** (DB up); new `tests/stream_graceful_close_mapping` 6/6 green.
- [x] coverage did not decrease — 87.42% (held; +6 tests, no src logic added beyond the tuple entry).
- [x] no test or contract was altered during build — §3 FROZEN @ v1 untouched; only the 5 src adapters changed.
- [x] the green was EARNED — adversarial refute-read (backend-expert sonnet) **SOUND 0.95**: 6 attacks (green-gamed / type-correctness incl. LocalProtocolError leak / body-drift / wrong-site / end-to-end flow / success-path) all NONE; tests yield a real chunk before raising; ReadError regression guard non-vacuous.
- [x] concurrency / timing safe — no concurrency change; same breaker.on_upstream_error + raise...from None body, only the except tuple widened.
- [x] no exposed secrets / injection / new deps — `httpx.RemoteProtocolError` is already-imported stdlib-dep httpx; no new import.
- [x] layering follows CONVENTIONS.md — change confined to the infrastructure adapters, mirrors each adapter's existing mid-stream mapping line-for-line.
- [x] a person reviewed and approved — Tin Dang chose this task ("Add Finding-C task first (all adapters)") and the approach via AskUserQuestion; §3 frozen on that approval.

### Build expectations — what "correct" looks like
- [x] each adapter's stream() drains → UpstreamUnavailableError (not raw httpx.RemoteProtocolError) on a graceful mid-stream close — 5 per-adapter tests green.
- [x] httpx.RemoteProtocolError is genuinely NEW coverage (was uncaught: not a NetworkError/TimeoutException) and LocalProtocolError stays UNmapped — confirmed by refute-read attack 2 (issubclass(LocalProtocolError, RemoteProtocolError) == False).
- [x] existing NetworkError/ReadError mapping unchanged — ReadError regression test green; bedrock ConnectError retained.
- [x] end-to-end: the mapped UpstreamUnavailableError now hits use_cases `_wrapped()` mid-stream catch → task-2 error frame + [DONE] — confirmed by refute-read attack 5 (use_cases.py:1620).

### Deep checks
- [x] WIRING — `httpx.RemoteProtocolError` added to all 5 streaming `_gen()` except tuples; exercised by the 5 tests.
- [x] DEAD-CODE — no new symbol; additive tuple entry only.
- [x] SEMANTIC — refute-read read all 5 diffs: additive-only, body byte-identical, correct site (gemini non-stream @860/@909 untouched).

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (via Claude orchestration; refute-read SOUND 0.95) · date: 2026-06-24

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
