# TASK: BFF SSE streaming passthrough

slug: streaming-bff · created: 2026-06-25 · stage: production
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
  - `apps/dashboard/app/api/gw/[...path]/route.ts:proxyRequest(req, context)` — the BFF catch-all; attaches `Authorization: Bearer <ai_proxy_session cookie>` and forwards to `GATEWAY_URL`. DEFECT: buffers EVERY response via `await upstream.json()` (~line 104) → `NextResponse.json(...)`, so SSE never streams. Exports GET/POST/PUT/PATCH/DELETE → proxyRequest.
  - same file helpers `getTokenFromRequest` · `buildClearCookieValue` · `gatewayUrl()` — auth/cookie behavior to preserve BYTE-IDENTICAL.
  - upstream shape `apps/gateway/src/gateway/proxy/api/router.py:64` → `StreamingResponse(gen, media_type="text/event-stream")` for `stream:true` chat (terminal `data: [DONE]`); non-stream → `JSONResponse`. BFF must branch on the upstream response `Content-Type`.
  - FE seam `apps/dashboard/lib/bff-client.ts` + `lib/api-client.ts` — how the dashboard calls `/api/gw/*` (the streaming consumer is added later by chat-workspace-page; named here only as the downstream contract reader).
Context (working folder):
  - Tests: `apps/dashboard/tests-bff/` vitest project (NextRequest-driven route-handler tests: `route-handlers.test.ts` · `proxy.test.ts` · `patch-passthrough.test.ts` · `bff-client.test.tsx`). New red suite lives in `tests-bff/`.
  - Harness gotcha (CONVENTIONS.md): the MSW `/api/gw/:path*` wildcard handler can silently defeat `onUnhandledRequest:"error"` — per-test handlers must be explicit.
  - Config: `GATEWAY_URL` / `NEXT_PUBLIC_GATEWAY_URL` (default `http://localhost:8080`).
Honors (patterns / conventions):
  - CONVENTIONS.md: "stream/wire parsers test fragmentation as part of the input domain by default — split-at-midpoint AND byte-by-byte chunk cases" → the passthrough red suite must include chunk-fragmentation cases.
  - PROJECT.md IO invariant: no outbound IO without timeout + bounded handling; KEEP fail-closed auth byte-identical (no cookie → 401 `ERR_AUTH_NO_SESSION`; upstream 401 → clear cookie + `ERR_AUTH_SESSION_EXPIRED`; 204 → empty).
  - v35 disconnect-billing (foundation): client disconnect MUST propagate to the upstream fetch (AbortController / `req.signal`) so the gateway bills the partial stream — the streaming path cannot swallow the abort.
Anchors the contract cites:
  - `proxyRequest(req, context)` (the function changed)
  - response branch: upstream `Content-Type: text/event-stream` → `new NextResponse(upstream.body, { status, headers })` (pipe `ReadableStream`); else the existing `NextResponse.json` path unchanged.
  - `AbortController` / `req.signal` → upstream `fetch({ signal })` for disconnect propagation.
  - preserved auth/cookie invariants: `ERR_AUTH_NO_SESSION` · `ERR_AUTH_SESSION_EXPIRED`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: BFF streaming passthrough — the dashboard gateway-proxy (`proxyRequest`) forwards STREAMABLE responses (SSE + binary: audio/video/octet-stream) to the browser INCREMENTALLY (no buffering), forwards the upstream headers, and propagates client disconnect/cancel to the upstream via an explicit AbortController. Future-proofs voice (v42/v47) & video (v46/v48), not just chat.
Framings weighed: streamable-Content-Type allowlist + request `stream:true` fallback (chosen) · upstream-event-stream-only branch (too narrow — misses audio/video) · separate dedicated streaming route (forks the seam).
Must:
<must>
  - M1 — A response is STREAMABLE when its upstream Content-Type starts with one of {`text/event-stream`, `audio/`, `video/`, `application/octet-stream`}, OR the request body carried `stream: true` AND the upstream Content-Type is NOT `application/json`. A streamable response is piped UNBUFFERED (`new NextResponse(upstream.body, …)`) preserving the upstream status, so first bytes reach the client before the upstream completes — `upstream.json()` is NEVER called on it.
  - M2 — The BFF drives the upstream fetch with an explicit `AbortController`; it aborts when the inbound `req.signal` fires (client disconnect) OR the piped response stream is cancelled — so the upstream connection tears down and the gateway's v35 disconnect-billing fires.
  - M3 — A streamed response FORWARDS the upstream headers minus the hop-by-hop set {`content-encoding`, `content-length`, `transfer-encoding`, `connection`, `keep-alive`} and sets `Cache-Control: no-cache` + `X-Accel-Buffering: no`, so SSE survives intermediary proxy buffering.
  - M4 — A NON-streamable response stays BYTE-IDENTICAL to today: `application/json` (and any non-streamable, non-stream-requested body) buffered via `NextResponse.json` (parse-fail → null), upstream 204 → empty 204, upstream 401 → cleared cookie + `ERR_AUTH_SESSION_EXPIRED`. A pre-stream JSON error to a `stream:true` request (402/429) stays buffered JSON — NEVER piped.
  - M5 — Auth preconditions byte-identical: no `ai_proxy_session` cookie → 401 `ERR_AUTH_NO_SESSION` WITHOUT calling upstream; Bearer from the cookie; method, query, request body, Content-Type forwarded as today.
  - M6 — The stream branch is reached only AFTER the 401/204 guards; an `application/json` body is NEVER piped.
</must>
Reject:
<reject>
  - request without an `ai_proxy_session` cookie -> "ERR_AUTH_NO_SESSION" (401, upstream NOT called)
  - upstream responds 401 -> "ERR_AUTH_SESSION_EXPIRED" (401, clears the session cookie)
</reject>
After:
<after>
  - A streaming request (SSE or binary): the browser receives bytes incrementally with the upstream headers; on client disconnect/cancel the upstream fetch is aborted; nothing is buffered in BFF memory.
  - A non-streamable request: identical status + bytes + Set-Cookie as before this task (the buffered JSON path is untouched).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] The streamable-vs-buffer decision must NEVER pipe an `application/json` body — pre-stream errors (402/429) to a `stream:true` request come back JSON and MUST stay buffered; only non-JSON / allowlisted bodies pipe. Lowest confidence because a mislabeled body could route wrong. Grounded: gateway emits `text/event-stream` for SSE (`router.py:64`) and JSON for pre-stream errors. If wrong: a JSON error streams as raw bytes (FE error-handling miss) or audio buffers (broken) — pinned by the M1 + "402-stays-buffered" tests.
  - [ ] [contract] An explicit `AbortController` wired to `req.signal` + the stream's `cancel()` aborts the upstream on disconnect (Next 16). If unwired → "billed full" degrade, not a crash. Pinned by the M2 abort-propagation test.
  - [ ] [contract] Forwarding upstream headers MINUS hop-by-hop is correct — `content-encoding`/`content-length` must be dropped (fetch already decoded the body) or the piped response is corrupt. Pinned by the M3 header-forward test.
  - [ ] [scenario] The `tests-bff` vitest harness can mock an upstream `ReadableStream` body + assert response `.body` is a stream, headers forwarded, and signal/abort propagation — else assert content-type + body-is-stream + signal rather than wall-clock chunk timing.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: SSE response streams incrementally            # M1
  Given an authenticated request to /api/gw/v1/chat/completions
  And the upstream responds 200 Content-Type "text/event-stream" with a chunked body
  When proxyRequest forwards it
  Then the BFF response is 200 with Content-Type "text/event-stream"
  And the body is the piped upstream ReadableStream (upstream.json() was never called)

Scenario: binary (audio/video) response streams         # M1 (generalized)
  Given an authenticated request whose upstream responds 200 Content-Type "audio/mpeg" with a byte stream
  When proxyRequest forwards it
  Then the BFF pipes the upstream body unbuffered with Content-Type "audio/mpeg"
  And upstream.json() is never called on it

Scenario: stream:true with non-JSON upstream is piped    # M1 (belt-and-suspenders)
  Given an authenticated POST whose body has "stream": true
  And the upstream responds 200 with a NON-application/json Content-Type
  When proxyRequest forwards it
  Then the response is piped unbuffered
  And the buffered JSON branch is NOT taken

Scenario: JSON error to a stream:true request stays buffered  # M4/M6 (critical guard)
  Given an authenticated POST whose body has "stream": true
  And the upstream responds 402 Content-Type "application/json" {"code":"ERR_BUDGET_EXCEEDED"}
  When proxyRequest forwards it
  Then the BFF returns 402 with that JSON body via NextResponse.json
  And the stream branch is NOT taken (the JSON error is never piped as raw bytes)

Scenario: client disconnect aborts the upstream         # M2
  Given an authenticated streaming request in flight
  When the inbound req.signal fires (client disconnects)
  Then the AbortController driving the upstream fetch is aborted
  And the upstream connection is torn down (no further reads)

Scenario: response-stream cancel aborts the upstream     # M2 (edge)
  Given an authenticated streaming response being consumed
  When the client cancels the response stream (reader.cancel)
  Then the same AbortController is aborted
  And no further upstream reads occur

Scenario: streamed response forwards upstream headers     # M3
  Given an authenticated streaming request whose upstream sets X-Request-Id + content-length + content-encoding
  When proxyRequest pipes it
  Then the BFF response carries X-Request-Id and Cache-Control "no-cache" and X-Accel-Buffering "no"
  And the hop-by-hop headers (content-length, content-encoding, transfer-encoding, connection) are dropped

Scenario: non-streamable JSON is byte-identical          # M4
  Given an authenticated request whose upstream responds 200 application/json {"a":1}
  When proxyRequest forwards it
  Then the BFF returns 200 with the same JSON body via NextResponse.json
  And the stream branch is NOT taken (buffered exactly as before)

Scenario: no session cookie is rejected without upstream # M5 / Reject
  Given a request with no ai_proxy_session cookie
  When proxyRequest runs
  Then the BFF returns 401 { code: "ERR_AUTH_NO_SESSION" }
  And the upstream fetch is never called

Scenario: upstream 401 clears the cookie                 # M4/M6 / Reject
  Given an authenticated request whose upstream responds 401
  When proxyRequest forwards it
  Then the BFF returns 401 { code: "ERR_AUTH_SESSION_EXPIRED" } with a Set-Cookie clearing ai_proxy_session
  And the stream branch is NOT taken

Scenario: 204 No Content stays empty                     # M4 (edge)
  Given an authenticated DELETE whose upstream responds 204
  When proxyRequest forwards it
  Then the BFF returns an empty 204
  And nothing is streamed or buffered

Scenario: event-stream with chunk fragmentation          # M1 (edge: split-at-midpoint + byte-by-byte)
  Given an authenticated streaming request whose upstream body arrives at arbitrary chunk boundaries
  When proxyRequest pipes it
  Then every upstream byte reaches the client in order with no reassembly/buffering by the BFF
  And the BFF adds no framing of its own (transparent passthrough)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
proxyRequest(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) -> Response
  (exported verbatim as GET/POST/PUT/PATCH/DELETE → proxyRequest; signatures unchanged)

Constants:
  STREAMABLE_CT = ["text/event-stream", "audio/", "video/", "application/octet-stream"]   # startsWith match
  HOP_BY_HOP    = ["content-encoding", "content-length", "transfer-encoding", "connection", "keep-alive"]

Ordered behavior:
  1. token = ai_proxy_session cookie
        absent  -> 401 { code: "ERR_AUTH_NO_SESSION" }                    (NO upstream call)
  2. reqBody = (method ∉ {GET,HEAD,DELETE}) ? await req.text() : undefined
     requestedStream = best-effort JSON.parse(reqBody)?.stream === true   # try/catch → false
     controller = new AbortController()
     req.signal.addEventListener("abort", () => controller.abort())       # NEW (M2)
     upstream = fetch(`${GATEWAY_URL}/${path}${query}`, {
         method, headers: { Authorization: `Bearer ${token}`, ...(Content-Type if present) },
         body: reqBody, signal: controller.signal,
       })
  3. upstream.status === 401 -> 401 { code: "ERR_AUTH_SESSION_EXPIRED" } + Set-Cookie clears ai_proxy_session
  4. upstream.status === 204 -> 204 (empty)
  5. ct = (upstream Content-Type ?? "").toLowerCase()
     isJson      = ct.startsWith("application/json")
     shouldStream = STREAMABLE_CT.some(p => ct.startsWith(p)) || (requestedStream && !isJson)   # M1/M6
     if shouldStream:                                                     # NEW — pipe, UNBUFFERED
        headers = clone(upstream.headers) minus HOP_BY_HOP                # M3
        headers.set("Cache-Control","no-cache"); headers.set("X-Accel-Buffering","no")
        body = upstream.body wrapped so its cancel() → controller.abort() # M2 (cancel propagation)
        -> new NextResponse(body, { status: upstream.status, headers })
  6. else -> NextResponse.json((await upstream.json()) ?? null, { status: upstream.status })     # UNCHANGED (M4)

Reject responses (preserved):
  ERR_AUTH_NO_SESSION       -> 401        (no cookie; upstream never called)
  ERR_AUTH_SESSION_EXPIRED  -> 401 + clear-cookie   (upstream 401)

Schema: none — stateless proxy (no DB / Redis / persistent state).
Anchors: apps/dashboard/app/api/gw/[...path]/route.ts:proxyRequest
         (+ getTokenFromRequest · buildClearCookieValue · gatewayUrl — unchanged)
```

Status: FROZEN @ v1 — approved by Tin 2026-06-25
Least-sure flag surfaced at freeze:
  - [contract] The streamable-vs-buffer decision must NEVER pipe an `application/json` body — a pre-stream error (402/429) to a `stream:true` request returns JSON and MUST stay buffered; only allowlisted / non-JSON bodies pipe. If wrong: a JSON error streams as raw bytes (FE error-handling miss) or audio buffers. Pinned by `test_json_error_to_stream_request_stays_buffered` + the M1 tests. Cost if wrong = a follow-up contract fix, no data risk.
  - [contract] The AbortController (req.signal + stream cancel) must tear the upstream down so v35 disconnect-billing fires; if unwired the degrade is "billed full," not a crash. Pinned by the M2 disconnect + cancel tests.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: behavioral — one test per scenario; no coverage regression on the BFF route.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_sse_streams_incrementally (M1): mock upstream 200 `text/event-stream` ReadableStream → assert response CT is event-stream AND response.body is a stream (upstream.json never called).
  - test_binary_audio_streams (M1): mock upstream 200 `audio/mpeg` byte stream → response is piped, CT audio/mpeg, json() never called.
  - test_stream_true_nonjson_is_piped (M1): POST body {stream:true} + upstream non-application/json CT → piped (buffered branch not taken).
  - test_json_error_to_stream_request_stays_buffered (M4/M6): POST {stream:true} + upstream 402 application/json → NextResponse.json 402 with body (NOT piped) — the critical guard.
  - test_client_disconnect_aborts_upstream (M2): spy fetch → called with the controller's signal; firing req.signal aborts it.
  - test_response_cancel_aborts_upstream (M2 edge): cancel the response stream → same controller aborts; no further upstream reads.
  - test_streamed_response_forwards_headers (M3): upstream sets X-Request-Id + content-length + content-encoding → response carries X-Request-Id + Cache-Control no-cache + X-Accel-Buffering no; hop-by-hop dropped.
  - test_nonstreamable_json_byte_identical (M4): upstream 200 application/json → NextResponse.json same body + status (buffered path unchanged).
  - test_no_cookie_rejects_no_upstream (M5): no cookie → 401 `ERR_AUTH_NO_SESSION` AND fetch never called.
  - test_upstream_401_clears_cookie (M4/M6): upstream 401 → 401 `ERR_AUTH_SESSION_EXPIRED` + Set-Cookie clears cookie AND stream branch not taken.
  - test_204_empty (M4 edge): upstream 204 → empty 204, nothing streamed/buffered.
  - test_event_stream_chunk_fragmentation (M1 edge): upstream body split at arbitrary boundaries → all bytes pass through in order, BFF adds no framing.
</test_plan>

Tests live in: `apps/dashboard/tests-bff/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/app/api/gw/[...path]/route.ts` · `apps/dashboard/tests-bff/streaming-bff.test.ts` (the §4 red suite — declared here so the scope-gate counts it)
Strategy (ordered batches): 1. parse `requestedStream` (best-effort, try/catch) + create an `AbortController` wired to `req.signal`, drive the upstream fetch with `controller.signal` (M2). 2. compute `shouldStream` = STREAMABLE_CT allowlist startsWith OR (requestedStream && !isJson) (M1/M6). 3. stream branch BEFORE the JSON fallback (after 401/204): clone upstream headers minus HOP_BY_HOP, set Cache-Control/X-Accel-Buffering, pipe `upstream.body` wrapped so `cancel()` → `controller.abort()` (M1/M2/M3). 4. leave the 401/204/JSON-fallback steps + helpers byte-identical (M4/M5).
Safety rule (feature-specific): NEVER call `upstream.json()` on a streamable/streamed body; NEVER pipe an `application/json` body (pre-stream JSON errors stay buffered); DROP hop-by-hop headers on the piped response (fetch already decoded the body — keeping content-length/content-encoding corrupts it).
Code lives in: `apps/dashboard/`
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

- [x] all tests pass — streaming-bff 13/13 green; full BFF project 29 files/222 green; full dashboard suite 62 files/513 green (pre-fix run); tsc --noEmit clean; eslint clean on the route.
- [x] coverage did not decrease — net +13 behavioral tests on the BFF route (was buffered-only); no test removed.
- [x] no test or contract was altered during build — §3 FROZEN unchanged; the only test edit was ADDING `test_pre_aborted_signal_returns_gracefully_no_crash` (red→green for a verify-found defect), never weakening one.
- [x] the green was EARNED — adversarial refute-read (sonnet) returned SHIP-WITH-FIXES @ 0.85; M1/M4/M5/M6 confirmed correct+tested; the one MAJOR (F1 unhandled AbortError) was reproduced by a failing test then fixed. No overfit/vacuous asserts found.
- [x] concurrency / timing safe — single AbortController wired to req.signal AND the response-stream `cancel()`; `cancelled` flag guards every stream-controller op against the cancel/pull race (no close-after-cancel unhandled rejection); fetch wrapped so a disconnect-abort returns 499, not a 500.
- [x] no exposed secrets / injection / unexpected deps — zero new dependencies (Web Streams + fetch only); Bearer still server-side only; `set-cookie` STRIPPED from streamed responses so the BFF stays the sole session-cookie authority (closes the buffered-vs-streamed asymmetry F6).
- [x] layering & dependencies follow CONVENTIONS.md — single-file route handler; fragmentation tested (split + reassembled-in-order) per the stream-parser convention; fail-closed auth preserved.
- [x] a person reviewed — Tin froze §3 (the bundle approval); change is BFF-only, non-security-blocking. Refute-read stood in for line-review; residue (F2/F4/F5) is documented below, none security.

### Build expectations — what "correct" looks like (confirmed at the gate)
- [x] An upstream `text/event-stream` (and `audio/*`) response reaches the client UNBUFFERED with Content-Type preserved — seen: `test_sse_streams_incrementally` / `test_binary_audio_streams` assert the streamed body + content-type; `upstream.json()` never runs on it.
- [x] A 402 `application/json` error to a `stream:true` request stays BUFFERED JSON (M6) — seen: `test_json_error_to_stream_request_stays_buffered` returns 402 application/json, not raw-piped.
- [x] Client disconnect AND response cancel both abort the upstream (so v35 disconnect-billing fires) — seen: `test_client_disconnect_aborts_upstream` + `test_response_cancel_aborts_upstream` assert the upstream signal aborts; `test_pre_aborted_signal_...` proves no crash on pre-abort.
- [x] Streamed responses forward upstream headers minus hop-by-hop + add no-buffering hints — seen: `test_streamed_response_forwards_headers` (x-request-id forwarded; content-length/connection dropped; cache-control no-cache + x-accel-buffering no set).
- [x] Auth/204/buffered-JSON paths byte-identical — seen: `test_nonstreamable_json_byte_identical`, `test_no_cookie_rejects_no_upstream`, `test_upstream_401_clears_cookie`, `test_204_empty` all green; the 401/204/json-fallback code is untouched.

### Deep checks
- [x] WIRING — `STREAMABLE_CONTENT_TYPES` + `HOP_BY_HOP_HEADERS` referenced in `proxyRequest`; `proxyRequest` exported as GET/POST/PUT/PATCH/DELETE (all 5 still wired); the piped `ReadableStream` returned via `NextResponse`.
- [x] DEAD-CODE — no orphaned symbol; the old `upstreamBody: BodyInit` was renamed to `bodyText: string` (reused for both fetch body + stream-flag parse).
- [x] SEMANTIC — refute-read read the impl + tests + frozen contract in full; verdict + per-Must confidence recorded above.

### Accepted residue (non-blocking, → OBSERVE)
- F2 [MINOR]: a streamable Content-Type with a `null` upstream body falls through to the buffered branch (returns 200 json `null`) — degenerate upstream, graceful non-crash; seeded as a spec delta.
- F4/F5 [NIT]: M3 test omits an explicit content-encoding/transfer-encoding drop assertion (impl lists both correctly; undici also pre-strips); the req.signal abort listener isn't removeEventListener'd (request-scoped, GC-bounded).

### GATE RECORD
Outcome: PASS
Reviewed by: AI auto-gate (autonomy:auto) on complete evidence + sonnet refute-read; §3 human-frozen by Tin · date: 2026-06-25

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] streamable Content-Type + `null` upstream body falls to the buffered branch (returns 200 json `null`) — harden to an empty streamed 200 (evidence: refute-read F2; degenerate upstream, non-crash today).
- [SPEC · open] M3 header test omits an explicit content-encoding/transfer-encoding drop assertion — add one if undici stops pre-stripping (evidence: refute-read F4; impl lists both correctly).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
- [ADD · folded] §5 Scope must declare the §4 red-test file too, not just src — the scope-gate reads `anchor.declared` (frozen at the tests→build crossing from the live §5 line), so a test-file touch during a verify→build heal loop reads as a scope_violation until you re-cross tests→build to rebirth the anchor (evidence: streaming-bff gate, 2 heal attempts spent). [folded foundation-version 36]
