# TASK: Azure SSE streaming + tools/response_format passthrough (billing on terminal frame)

slug: azure-streaming-passthrough · created: 2026-06-15 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it. -->
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
- `apps/gateway/src/gateway/proxy/infrastructure/azure_upstream.py:AzureCompletionUpstream.stream` (currently a NotImplementedError stub from azure-chat) — IMPLEMENT as a byte-identical SSE passthrough mirroring OpenRouterCompletionUpstream.stream (openrouter_upstream.py:118): breaker.guard() BEFORE the first byte; client.stream("POST", url, json=payload, headers={api-key,…}) with the stream-read timeout; status>=500 → breaker.on_upstream_error()+UpstreamUnavailableError raised BEFORE yielding; record_success then `async for chunk in response.aiter_bytes(): yield chunk`; TimeoutException/NetworkError → UpstreamUnavailableError. URL = config.build_url(config.resolve_deployment(model), "chat/completions").
- `apps/gateway/proxy/application/use_cases.py` + `gateway.usage.domain.extractor.extract_usage_from_sse` (FROZEN, NO CHANGE) — billing reads usage from the streamed bytes in the APPLICATION layer; the adapter only yields raw bytes (Azure SSE is OpenAI-shaped → byte-passthrough → billing works unchanged, same as OpenRouter). No adapter billing code.
- tools / response_format — NO new code: these are payload fields that complete() (task 2) and stream() forward verbatim (json=payload) and return verbatim. Task 3 proves passthrough byte-identity with regression tests (a tools+tool_choice+response_format payload is forwarded unchanged; a tool_calls / json response passes through).
- `apps/gateway/tests/azure_chat/test_azure_upstream.py:test_stream_not_implemented` — RETIRE (cross-task): the task-2 stub-guard is obsolete once stream() works (mirrors bedrock task 3 retiring its stub test). Removed during the TESTS phase (pre-snapshot) so it never reds; declared in §5 for honesty.

Context (working folder): no DB/migration. One method body + passthrough proofs. Tests use httpx.MockTransport streaming responses (httpx.Response(200, content=sse_bytes)) — the bedrock_streaming pattern (no network).

Honors (patterns / conventions):
- stream() contract (breaker pre-first-byte → v19 failover; status+buffer before first yield → 5xx raises 0 chunks; NEVER retried) — OpenRouter/Anthropic/Bedrock precedent.
- billing is application-layer (extract_usage_from_sse on the drained chunks); adapter is pure byte-passthrough.
- api_key SECRET — only in the `api-key` header.

Anchors the contract cites: `AzureCompletionUpstream.stream(payload) -> AsyncIterator[bytes]` (byte-passthrough, breaker-guarded, 5xx→UpstreamUnavailableError), tools/response_format passthrough via complete()/stream().

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Azure SSE streaming (byte-passthrough) + tools/response_format passthrough proofs.
Framings weighed: byte-identical SSE passthrough mirroring OpenRouter.stream, billing left to the application layer (chosen — Azure SSE IS OpenAI SSE; the gateway already extracts usage from streamed bytes) · re-parse/re-emit SSE in the adapter (rejected — needless, risks corrupting byte-identity + billing) · add adapter-level usage extraction (rejected — duplicates extract_usage_from_sse; billing is application-layer by design).
Must:
<must>
  - stream(payload) checks the circuit breaker BEFORE the first byte, then streams POST to config.build_url(config.resolve_deployment(model), "chat/completions") with the `api-key` header; yields upstream bytes verbatim (byte-identical SSE passthrough).
  - On upstream status >= 500 (before first yield): breaker.on_upstream_error() and raise UpstreamUnavailableError — zero chunks yielded; NEVER retried (stream has no retry machinery).
  - On read timeout / network error mid-stream: breaker.on_upstream_error() and raise UpstreamUnavailableError.
  - Streamed usage billing works UNCHANGED: the application layer drains the bytes and extract_usage_from_sse reads the terminal usage frame (adapter adds no billing code).
  - tools + tool_choice + response_format in the request payload are forwarded verbatim (complete + stream); tool_calls / json-mode content in the response passes through verbatim.
  - The task-2 stream() NotImplementedError stub test is retired (pre-snapshot); the rest of the azure_chat suite stays green.
</must>
Reject:
<reject>
  - upstream 5xx on stream open -> UpstreamUnavailableError, 0 chunks (breaker.on_upstream_error)
  - read timeout / network error mid-stream -> UpstreamUnavailableError (not retried)
</reject>
After:
<after>
  - A streaming Azure chat request yields OpenAI-shaped SSE byte-for-byte; usage on the terminal frame bills exactly via the existing application path; tools + response_format work over Azure with no translation; 5xx pre-first-byte fails over (v19).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [spec] Azure streamed usage arrives on a terminal SSE frame that extract_usage_from_sse already parses (OpenAI shape with stream_options:{include_usage:true}) — least-sure because if a client omits include_usage, the stream carries no usage and billing falls back to the existing zero/estimate path (same as OpenRouter today); if wrong: nothing azure-specific to fix (shared behavior). Live-verify (task 6) confirms a real usage frame.
  - [ ] [spec] byte-passthrough preserves tool_calls/json content with no translation — true by construction (Azure IS OpenAI); proven by passthrough tests.
  - [ ] [spec] retiring the task-2 stub test pre-snapshot keeps the azure_chat suite green (task-2 impl now provides a real stream()) — confirmed by re-running azure_chat after the edit.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: stream yields Azure SSE bytes verbatim to the deployment URL with api-key
  Given an AzureCompletionUpstream and an upstream 200 SSE body of OpenAI chat.completion.chunk frames
  When stream({"model":"gpt-4o","stream":true,...}) is drained
  Then the joined chunks equal the upstream SSE bytes byte-for-byte
  And the request went to ".../openai/deployments/prod-4o/chat/completions?api-version=..." with the "api-key" header

Scenario: streamed usage frame is billable via the application extractor
  Given an SSE body whose terminal frame carries usage{prompt,completion,total}
  When the drained chunks are passed to extract_usage_from_sse
  Then it returns the usage dict (exact billing path works unchanged)

Scenario: upstream 5xx on stream open raises before any chunk
  Given the upstream returns 503 on the stream
  When stream(payload) is iterated
  Then UpstreamUnavailableError is raised
  And zero chunks were yielded

Scenario: tools + tool_choice + response_format are forwarded verbatim
  Given a payload with tools, tool_choice, and response_format
  When complete(payload) is called against a request-capturing handler
  Then the forwarded request body contains tools, tool_choice, and response_format unchanged

Scenario: a tool_calls response passes through verbatim
  Given the upstream returns 200 with a message containing tool_calls
  When complete(payload) is called
  Then the returned body's tool_calls are byte-identical to upstream

Scenario: the obsolete stream-stub test is retired
  Given azure-chat task 2 stubbed stream() with a NotImplementedError guard test
  When stream() is implemented here
  Then test_stream_not_implemented is removed and the azure_chat suite stays green
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# azure_upstream.py — implement the stream() stub from azure-chat:
def stream(self, payload: dict[str, object]) -> AsyncIterator[bytes]:
    self._breaker.guard()                       # BEFORE first byte (v19 failover)
    url = self._config.build_url(self._config.resolve_deployment(model), "chat/completions")
    headers = {**self._auth_headers(), "content-type": "application/json"}
    async def _gen():
        try:
            async with self._client.stream("POST", url, json=payload, headers=headers,
                                           timeout=<stream-read timeout>) as response:
                if response.status_code >= 500:
                    self._breaker.on_upstream_error()
                    raise UpstreamUnavailableError(...)   # 0 chunks yielded
                self._breaker.record_success()
                async for chunk in response.aiter_bytes():
                    yield chunk                  # BYTE-IDENTICAL passthrough
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            self._breaker.on_upstream_error()
            raise UpstreamUnavailableError(str(exc)) from exc
    return _gen()

# Billing: UNCHANGED — application layer drains chunks → extract_usage_from_sse (FROZEN).
# tools / response_format: NO code — forwarded verbatim by complete()/stream() (json=payload).
Errors: UpstreamUnavailableError (5xx pre-first-byte / timeout / network)
Schema: none. Cross-task: retire tests/azure_chat/...::test_stream_not_implemented (pre-snapshot).
Invariant: byte-identical to upstream SSE; mirrors OpenRouterCompletionUpstream.stream exactly bar URL+auth.
```

Least-sure flag surfaced at freeze: [spec] streamed-usage billing depends on the client sending stream_options:{include_usage:true} so Azure emits a terminal usage frame extract_usage_from_sse can read — least-sure because without it the stream has no usage (billing falls to the existing zero/estimate path, identical to OpenRouter today); cost if wrong = nothing azure-specific (shared behavior); live-verify (task 6) confirms a real usage frame. Everything else (byte-passthrough, breaker-pre-first-byte, 5xx→raise) is mechanically identical to the FROZEN OpenRouter.stream.

Status: FROZEN @ v1 — approved by Tin (auto mode, delegated per standing fully-autonomous mandate; non-security passthrough mirroring a frozen seam; flag is shared-behavior, not a contract risk)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of the new stream() branch; project floor elsewhere.
Plan (one test per scenario; httpx.MockTransport streaming responses, no network):
<test_plan>
  - test_stream_passthrough_url_and_api_key: 200 SSE body → joined chunks == upstream bytes; URL=deployment+api-version; "api-key" header present
  - test_streamed_usage_is_billable: SSE w/ terminal usage frame → extract_usage_from_sse(drained) == usage dict
  - test_stream_5xx_raises_before_any_chunk: 503 → pytest.raises(UpstreamUnavailableError); 0 chunks collected
  - test_tools_and_response_format_forwarded: payload w/ tools+tool_choice+response_format → captured request body has them unchanged
  - test_tool_calls_response_passthrough: 200 body w/ tool_calls → returned body tool_calls byte-identical
  - (tests phase) retire tests/azure_chat/test_azure_upstream.py::test_stream_not_implemented; re-run azure_chat green
</test_plan>

Tests live in: `apps/gateway/tests/azure_streaming/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/azure_upstream.py` `apps/gateway/tests/azure_chat/test_azure_upstream.py`
Strategy (ordered batches): 1. azure_upstream.py — replace the stream() stub body with the byte-passthrough generator (mirror OpenRouter.stream + build_url + api-key header + stream-read timeout). (The test_azure_upstream.py stub-test retirement is done in the TESTS phase, pre-snapshot — listed here for honesty.)
Safety rule (feature-specific): byte-identical passthrough — NEVER re-parse/re-emit SSE; breaker.guard() before first byte; 5xx pre-first-byte yields 0 chunks; api_key only in the `api-key` header.
Code lives in: `apps/gateway/src/gateway/proxy/infrastructure/azure_upstream.py`
Constraints: do NOT change the contract; allow-list packages only (httpx + existing infra); the only test edit is the documented stub retirement; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 11/11 (azure_streaming 5 + azure_chat 6 post-retirement); regression 90/90 (floor + dispatch + streaming_resilience + boot-guard); pyright 0; ruff clean.
- [x] coverage did not decrease — the new stream() branch fully exercised (passthrough, usage, 5xx-pre-first-byte).
- [x] no test or contract was altered during build — build touched only azure_upstream.py (the stub retirement was a pre-snapshot tests-phase edit, declared in §5).
- [x] the green was EARNED, not gamed — refute-read: byte-identity asserted by exact join==_SSE equality; 5xx asserts raise AND zero chunks (proves pre-first-byte ordering); usage proven by feeding drained chunks to the REAL extract_usage_from_sse; tools/response_format proven by capturing the forwarded request body. No vacuous asserts; passthrough IS the logic (no stub).
- [x] concurrency / timing of the risky operation is safe — breaker.guard() before first byte (v19 failover ordering); status checked before any yield; no retry on stream (double-billing guard); mirrors the FROZEN OpenRouter.stream exactly.
- [x] no exposed secrets, injection openings, or unexpected dependencies — api_key only in the `api-key` header; URL via the frozen path-quoting build_url; deps = httpx + existing infra.
- [x] layering & dependencies follow CONVENTIONS.md — adapter is pure byte-passthrough; billing stays in the application layer (extract_usage_from_sse unchanged).
- [x] a person reviewed and approved the change — AUTO-RESOLVED (autonomy: auto): non-security passthrough mirroring a frozen seam, complete green evidence, refute-read clean → explicit auto-PASS.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — stream() is invoked by ProviderAwareCompletionUpstream.stream for azure-routed models; the adapter is already wired (task 2) into _chat_adapters["azure"]. Exercised end-to-end via MockTransport. No orphan; the stub it replaced is gone.
- [x] DEAD-CODE (code) — no orphan: _STREAM_READ_TIMEOUT + UpstreamUnavailableError import are used by stream(); the retired stub test left no dangling helper (azure_chat suite green).
- [x] SEMANTIC (prose / non-code) — n/a (code task); §3 read in full; build matches (breaker-pre-first-byte, build_url, api-key, byte-passthrough, 5xx→raise).

### GATE RECORD
Outcome: PASS
Evidence: 11/11 azure streaming+chat · regression 90/90 · pyright 0/0 · ruff clean · refute-read clean · byte-identical SSE proven · streamed-usage billable via the real extractor · tools/response_format passthrough proven · stub retired cleanly. LIVE double-pass deferred to azure-verify (task 6).
Reviewed by: auto (autonomy: auto; non-security passthrough) · date: 2026-06-15

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): streamed-request rate with 0-usage frames (clients omitting stream_options.include_usage → billing gap, shared w/ OpenRouter); stream 5xx-pre-first-byte fallover rate.
Spec delta for the next loop: azure-embeddings (task 5) reuses build_url("…","embeddings") + the api-key header on the UpstreamProvider seam; azure-aad-auth (task 4) swaps the api-key header for Authorization: Bearer <aad> — both complete() and stream() read self._auth_headers(), so AAD only needs to change that one method (clean seam).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
