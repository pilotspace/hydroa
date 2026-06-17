# TASK: OpenAI direct provider: implement CompletionUpstream (complete/stream) for chat dispatch

slug: openai-chat-complete · created: 2026-06-17 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/`.
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/proxy/infrastructure/openai_provider.py:OpenAIDirectProvider` — direct OpenAI-compatible HTTP adapter. TODAY implements the **UpstreamProvider** Protocol only (`post_json` / `post_multipart` / `stream_bytes`). It has `_auth_headers()` (reads the request-scoped `BearerCredential` from the contextvar; raises `ProviderKeyMissing("openai")` when unset/non-Bearer), `self._client` (httpx.AsyncClient with base_url + timeout), `self._breaker` (CircuitBreaker). It has **NO `complete()` / `stream()`** — the **CompletionUpstream** surface the chat dispatch calls.
- `apps/gateway/src/gateway/proxy/domain/ports.py:CompletionUpstream` — the chat Protocol: `async complete(payload)->tuple[int,dict]` (raises `UpstreamUnavailableError` on 5xx/timeout/network) · `stream(payload)->AsyncIterator[bytes]` (same).
- `apps/gateway/src/gateway/proxy/infrastructure/provider_aware_upstream.py:ProviderAwareCompletionUpstream.complete/stream` — dispatch: `adapter = self._adapters.get(provider) or self._adapters[default]; await adapter.complete(payload)`. Calling `.complete()` on an adapter without it → AttributeError → 500.
- `apps/gateway/src/gateway/main.py` — line 433 `_chat_adapters["openai"] = _openai_direct` (introduced v25 task-2 `e3b4f2a`); line 473-474 `ProviderAwareCompletionUpstream(adapters=_chat_adapters,  # type: ignore[arg-type]` — the `type: ignore` MASKS that OpenAIDirectProvider does not satisfy CompletionUpstream.
- REFERENCE impl to mirror: `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py:OpenRouterCompletionUpstream.complete/stream` — the canonical chat adapter (POST `/chat/completions`, breaker guard, 5xx→UpstreamUnavailableError).

Context (working folder): `apps/gateway` — provider adapters under `src/gateway/proxy/infrastructure/`; adapter tests under `tests/provider_seam/` (OpenAIDirectProvider home; reusable conftest: `SequencedMockTransport`, `make_json_response`, `CHAT_PAYLOAD`, `CHAT_RESPONSE_BODY`, `FAKE_API_KEY`, `FAKE_OPENAI_BASE_URL`).

Honors (patterns / conventions): contextvar credential seam (v25 BYOK — NO key ctor arg); CircuitBreaker per-instance; `raise ... from None` secret-chain floor; pyright-strict; RFC-9457 error mapping owned by the use-case layer (adapter raises domain errors only).

Anchors the contract cites: `OpenAIDirectProvider.complete`, `OpenAIDirectProvider.stream`, `CompletionUpstream`, `UpstreamUnavailableError`, `ProviderKeyMissing`, `_auth_headers`, `main.py` adapter-map wiring.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: OpenAI direct provider chat dispatch — `complete` + `stream` (CompletionUpstream parity)
Framings weighed: **add complete()/stream() to OpenAIDirectProvider** (chosen — makes provider="openai" a true first-class direct BYOK chat path, the milestone goal) · wrap the adapter in an adapter-shim (rejected — extra indirection, two objects to keep in sync) · drop the `_chat_adapters["openai"]` registration so it falls back to openrouter (rejected — sends the OpenAI key to the OpenRouter surface → 401; abandons direct-OpenAI BYOK).
Must:
<must>
  - `OpenAIDirectProvider.complete(payload)` POSTs `{base_url}/chat/completions` with `_auth_headers()` (per-tenant contextvar Bearer) and returns `(status, json_body)` for any non-5xx (2xx/4xx passthrough).
  - `OpenAIDirectProvider.stream(payload)` POSTs `{base_url}/chat/completions` streaming with `_auth_headers()` and yields raw upstream byte chunks unchanged.
  - Both guard with the circuit breaker: `breaker.guard()` before the call; `>=500`/timeout/network → `breaker.on_upstream_error()` + raise `UpstreamUnavailableError`; else `breaker.record_success()`.
  - The existing `post_json` / `post_multipart` / `stream_bytes` (UpstreamProvider surface) remain byte-identical — embeddings/images/audio unaffected.
  - `main.py`: the `# type: ignore[arg-type]` on the `ProviderAwareCompletionUpstream(adapters=_chat_adapters, …)` call is REMOVED — the map is now type-correct.
  - A served model with `provider="openai"` dispatches to `OpenAIDirectProvider.complete` (NOT the openrouter fallback).
</must>
Reject:
<reject>
  - contextvar unset / non-Bearer credential -> `ProviderKeyMissing("openai")` (code "ERR_PROVIDER_KEY_MISSING"), and NO HTTP request is made.
  - upstream returns >=500 (or timeout / network error) -> `UpstreamUnavailableError` (breaker tripped).
</reject>
After:
<after>
  - `isinstance(OpenAIDirectProvider_instance, CompletionUpstream)` holds AND it still satisfies UpstreamProvider; provider="openai" chat returns 200 `chat.completion` authenticating with the per-tenant key (the task-6 live BV:openai step goes green, no AttributeError 500).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ stream() error shape — that a >=500 status must raise `UpstreamUnavailableError` BEFORE yielding any byte (not passthrough). Lowest confidence because the dispatch wraps stream lazily; if wrong, a 5xx stream leaks raw bytes instead of a clean 502. Mitigation: mirror the EXISTING `stream_bytes` exactly (it already does `on_upstream_error()` + raise on `>=500` before the first yield). Cost if wrong: a streaming 5xx surfaces malformed instead of 502.
  - [x] OpenAIDirectProvider's circuit breaker is per-call-safe to reuse for chat (same as post_json) — confirmed: `post_json` already uses the identical guard/record pattern.
  - [x] No retry is required for parity — confirmed: the adapter documents "no retries (conservative default)"; retry remains a separate carried follow-up (`openai_max_retries` knob), out of scope here.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: OC1 complete posts /chat/completions with the per-tenant Bearer and returns 200
  Given a BearerCredential is set in the contextvar and a MockTransport returns 200 chat.completion
  When complete(CHAT_PAYLOAD) is awaited
  Then it POSTs base_url + "/chat/completions" with header Authorization: Bearer <secret>
  And it returns (200, body) with body["object"]/choices intact
  And post_json/post_multipart/stream_bytes remain present and unchanged

Scenario: OC2 complete passes a 4xx through without raising
  Given the contextvar holds a BearerCredential and the transport returns 400
  When complete(CHAT_PAYLOAD) is awaited
  Then it returns (400, body) and does NOT raise
  And the breaker recorded a success (4xx is not an upstream-availability failure)

Scenario: OC3 complete raises UpstreamUnavailableError on 5xx
  Given the transport returns 503
  When complete(CHAT_PAYLOAD) is awaited
  Then UpstreamUnavailableError is raised
  And breaker.on_upstream_error() was called (no body leaked)

Scenario: OC4 stream yields raw SSE bytes from /chat/completions with the Bearer
  Given the contextvar holds a BearerCredential and the transport streams SSE bytes
  When stream(CHAT_PAYLOAD) is iterated
  Then it POSTs "/chat/completions" with Authorization: Bearer <secret> and yields the bytes unchanged
  And nothing else on the response is rewritten

Scenario: OC5 stream raises UpstreamUnavailableError on 5xx before yielding
  Given the transport returns 503 on the stream
  When stream(CHAT_PAYLOAD) is iterated
  Then UpstreamUnavailableError is raised before any byte is yielded
  And breaker.on_upstream_error() was called

Scenario: OC6 fail-closed when the contextvar is unset
  Given NO credential (or non-Bearer) is set in the contextvar
  When complete(CHAT_PAYLOAD) (and stream) is invoked
  Then ProviderKeyMissing("openai") is raised with code ERR_PROVIDER_KEY_MISSING
  And NO HTTP request is made (transport call_count == 0)

Scenario: OC7 dispatch routes provider="openai" to OpenAIDirectProvider.complete (not openrouter)
  Given ProviderAwareCompletionUpstream with adapters {openrouter: fake, openai: OpenAIDirectProvider} and a resolver returning "openai"
  When complete(CHAT_PAYLOAD) is awaited
  Then the OpenAIDirectProvider was invoked (its transport saw the call) and the openrouter fake was NOT

Scenario: OC8 OpenAIDirectProvider satisfies CompletionUpstream AND UpstreamProvider (zero regression)
  Given an OpenAIDirectProvider instance
  Then isinstance(it, CompletionUpstream) is True (hasattr complete + stream)
  And isinstance(it, UpstreamProvider) remains True (post_json/post_multipart/stream_bytes intact)

Scenario: OC9 production wiring is type-correct (no masking type-ignore)
  Given create_app() built app.state.chat_adapters
  Then chat_adapters["openai"] is an OpenAIDirectProvider
  And main.py contains NO "type: ignore[arg-type]" on the ProviderAwareCompletionUpstream(adapters=…) call
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
OpenAIDirectProvider (gateway.proxy.infrastructure.openai_provider) — ADD the CompletionUpstream surface,
leave the UpstreamProvider surface (post_json/post_multipart/stream_bytes) byte-identical:

  async def complete(self, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
      breaker.guard()
      POST {base_url}/chat/completions  json=payload  headers=_auth_headers()   # Authorization: Bearer <contextvar BearerCredential>
      except (httpx.TimeoutException, httpx.NetworkError): breaker.on_upstream_error(); raise UpstreamUnavailableError(str(exc)) from None
      status >= 500: breaker.on_upstream_error(); raise UpstreamUnavailableError(f"Upstream returned {status}")
      else: breaker.record_success(); return (status, resp.json())
      # contextvar unset/non-Bearer -> _auth_headers() raises ProviderKeyMissing("openai")  [code ERR_PROVIDER_KEY_MISSING] BEFORE any HTTP call

  def stream(self, payload: dict[str, object]) -> AsyncIterator[bytes]:
      breaker.guard()   # before the first byte
      inner gen: async with client.stream("POST", "/chat/completions", json=payload, headers=_auth_headers(), timeout=<stream timeout>):
          status >= 500: breaker.on_upstream_error(); raise UpstreamUnavailableError(...)   # before any yield
          else: breaker.record_success(); async for chunk in resp.aiter_bytes(): yield chunk
          except (Timeout, NetworkError): breaker.on_upstream_error(); raise UpstreamUnavailableError(str(exc)) from None

main.py: remove `# type: ignore[arg-type]` on `ProviderAwareCompletionUpstream(adapters=_chat_adapters, …)`.

No DB / schema / public-API change. No new dependency. Error mapping (ProviderKeyMissing->402, UpstreamUnavailableError->502)
already owned by the use-case layer (unchanged).
```

Status: FROZEN @ v1 — auto-approved (auto mode; low-risk additive protocol impl mirroring OpenRouterCompletionUpstream).
Least-sure flag surfaced at freeze: **[contract] stream() 5xx error shape** — raise `UpstreamUnavailableError` BEFORE the first yield (mirrors existing `stream_bytes`); why it could be wrong: dispatch wraps stream lazily so a late raise would already have leaked bytes; cost: a malformed 5xx stream instead of a clean 502. Mitigated by copying the proven `stream_bytes` guard exactly.

<!-- Changing a frozen contract = change request back to SPECIFY. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (of the new/changed lines in openai_provider.py)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_oc1_complete_posts_chat_completions_with_bearer: arrange BearerCredential+200 transport / act await complete(CHAT_PAYLOAD) / assert URL=/chat/completions + Bearer header + (200, body) + post_json still present
  - test_oc2_complete_4xx_passthrough_no_raise: 400 transport / await complete / assert returns (400, body), no raise, breaker.record_success
  - test_oc3_complete_5xx_raises_upstream_unavailable: 503 transport / await complete / assert UpstreamUnavailableError + breaker tripped
  - test_oc4_stream_yields_sse_bytes_with_bearer: SSE transport / iterate stream / assert /chat/completions + Bearer + bytes unchanged
  - test_oc5_stream_5xx_raises_before_yield: 503 stream transport / iterate / assert UpstreamUnavailableError before any byte
  - test_oc6_failclosed_unset_contextvar_no_http: no contextvar / await complete (and stream) / assert ProviderKeyMissing(ERR_PROVIDER_KEY_MISSING) + transport.call_count==0
  - test_oc7_dispatch_routes_openai_to_direct_provider: ProviderAwareCompletionUpstream{openrouter:fake, openai:real} resolver->openai / await complete / assert real adapter saw the call, fake did not
  - test_oc8_satisfies_completionupstream_and_upstreamprovider: assert isinstance CompletionUpstream + UpstreamProvider (hasattr complete/stream/post_json)
  - test_oc9_main_wiring_no_type_ignore: assert app.state.chat_adapters["openai"] is OpenAIDirectProvider AND main.py source has no 'type: ignore[arg-type]' on the dispatch wiring line
</test_plan>

Tests live in: `apps/gateway/tests/openai_chat_dispatch/` · MUST run red (missing complete/stream) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/openai_provider.py` `apps/gateway/src/gateway/main.py`
Strategy (ordered batches): 1. add `complete()` to OpenAIDirectProvider (mirror post_json + openrouter complete) 2. add `stream()` (mirror stream_bytes) 3. remove the `# type: ignore[arg-type]` in main.py 4. green the suite + re-run task-6 live.
Safety rule (feature-specific): the UpstreamProvider surface (post_json/post_multipart/stream_bytes) stays byte-identical — embeddings/audio/images must not regress; secret never logged.
Code lives in: `apps/gateway/src/gateway/`
Constraints: do NOT change any test or the contract; allow-list packages only (no new deps); ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — openai_chat_dispatch 10/10 GREEN · provider_seam 16/16 (UpstreamProvider unchanged) · no-DB floor 121/121 · **task-6 LIVE double-pass 17/17 ×2** (openai now 200, was 500)
- [x] coverage did not decrease — additive methods + their 10 tests; no lines removed
- [x] no test or contract was altered during build — only `openai_provider.py` + `main.py` (the §5 scope) changed
- [x] the green was EARNED, not gamed — independent sonnet refute-read verdict **EARNED-GREEN, no defects** (asserts SpyBreaker successes/errors; a no-op `(200,{})` would fail OC1/OC3/OC7; OC9 source-text pins the type-ignore removal)
- [x] concurrency / timing of the risky operation is safe — breaker.guard() before the call, identical to post_json/stream_bytes
- [x] no exposed secrets, injection openings, or unexpected dependencies — secret only in the Authorization header; `raise … from None` floor honored; no new deps
- [x] layering & dependencies follow CONVENTIONS.md — adapter raises domain errors (ProviderKeyMissing/UpstreamUnavailableError); use-case maps to HTTP (unchanged)
- [x] a person reviewed and approved the change — Tin authorized the fix (AskUserQuestion: "Fix now as TDD task-7"); pending final commit review

### Deep checks
- [x] WIRING (code) — `complete`/`stream` are invoked by `ProviderAwareCompletionUpstream.complete/stream` (OC7 proves dispatch routes provider="openai" → OpenAIDirectProvider); `_chat_adapters: dict[str, CompletionUpstream]` is type-correct (pyright 0 errors)
- [x] DEAD-CODE (code) — no new unused symbol; both methods reachable via dispatch + tests

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (auto-driven; refute-read EARNED-GREEN; live double-pass 17/17 ×2) · date: 2026-06-17

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): provider="openai" chat 5xx rate / ERR_PROVIDER_KEY_MISSING rate.
Spec delta for the next loop: an adapter registered in a dispatch map MUST be protocol-verified at wiring time (a `type: ignore[arg-type]` on an adapter map is a smell — the live pass caught what the type-ignore hid).

### Competency deltas
- [TDD · open] earned-green tested the adapter's transport (post_json) but not the dispatch contract (complete) — the live pass caught the gap; protocol-surface tests must assert isinstance against the Protocol the caller uses.
- [ADD · open] a `# type: ignore` that masks a Protocol mismatch is a latent 500; the verify task is what surfaced it end-to-end.
