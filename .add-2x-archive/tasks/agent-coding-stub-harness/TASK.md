# TASK: Deterministic Helios-shaped stub harness (CI gate backbone)

slug: agent-coding-stub-harness · created: 2026-06-23 · stage: production
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

NOTE: the gateway package lives at `apps/gateway/` — all paths below are relative to that (its own `pyproject.toml` runs pytest with cwd `apps/gateway`).

THREE TEST SEAMS (the harness serves all three — SEAM C added at the §3 freeze, Tin 2026-06-23):
- SEAM A — pure translator unit tests: existing `tests/anthropic_tool_use/test_anthropic_tool_use.py` imports `_openai_to_anthropic_request`, `_anthropic_to_openai`, `_translate_anthropic_sse` and asserts the mapping DIRECTLY (no DB, no route, no transport). Fastest; isolates the wire mapping.
- SEAM B — route/billing/disconnect integration tests: drive `client.post("/v1/chat/completions")` with a fake on `app.state.completion_upstream` (post-translation); assert client-visible SSE + recorded usage row. Proves routing, billing, disconnect, provider-agnostically.
- SEAM C — REAL adapter against a mocked httpx transport: build e.g. `AnthropicCompletionUpstream`, swap its internally-built `self._client` (`anthropic_upstream.py:549`, NO injectable transport) for `httpx.AsyncClient(base_url=…, transport=httpx.MockTransport(handler))`, set the request-scoped credential contextvar, feed provider-NATIVE bytes. Exercises the real adapter end-to-end (request build · auth headers · SSE parse · circuit breaker · error mapping) with zero socket. The truest proof for surfaces 2–4.
  - Credential anchors (SEAM C): `domain/credential_context.py:43` `set_provider_credential(cred)->Token` / `:72` `reset_provider_credential(token)` (finally-block contract); `domain/provider_credentials.py:94` `BearerCredential(secret: SecretStr)` (rejects empty); adapters raise `ProviderKeyMissing` when unset.

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/proxy/api/router.py:35` — `async def completions(...)`: the `/v1/chat/completions` handler; `upstream: CompletionUpstream = Depends(get_completion_upstream)`, reads per-request state off `request.app.state.*`. SEAM B entry point.
- `apps/gateway/src/gateway/proxy/domain/ports.py:122` — `class CompletionUpstream(Protocol)`: `async def complete(payload) -> tuple[int, dict]` and `def stream(payload) -> AsyncIterator[bytes]` (byte-identical SSE pass-through; raises `UpstreamUnavailableError`). The seam the SEAM-B stub implements.
- `apps/gateway/src/gateway/proxy/api/deps.py:80` — `get_completion_upstream(request)` → `request.app.state.completion_upstream`. The swap point.
- `apps/gateway/tests/conftest.py` — root fixtures: `app` (real Postgres, fresh schema per test), `client` (`httpx.AsyncClient` over `ASGITransport`), autouse `_isolate_stores`, `settings` (redis db 9). The harness fixtures compose on these.
- `apps/gateway/tests/provider_seam/test_provider_seam.py` — precedent `FakeCompletionUpstream` injected via `app.state.completion_upstream`; the SEAM-B pattern to mirror.
- `apps/gateway/tests/anthropic_tool_use/test_anthropic_tool_use.py` — the SEAM-A pure-helper pattern to mirror.
- Pure translator helpers the fixture library feeds (SEAM A): `apps/gateway/src/gateway/proxy/infrastructure/anthropic_upstream.py` (`_openai_to_anthropic_request`, `_anthropic_to_openai`, `_translate_anthropic_sse`, `_AnthropicSSEStepper`), `gemini_upstream.py:513`, `openrouter_upstream.py:100`, `bedrock_upstream.py`, `azure_upstream.py`, `openai_provider.py`.

Context (working folder):
- `apps/gateway/pyproject.toml:164` `[tool.pytest.ini_options]` — `testpaths=["tests"]`, `addopts="--cov=gateway --cov-fail-under=80 -m 'not e2e'"`. The CI gate the harness suite MUST join (non-`e2e`, no live network). (No root Makefile; `make test-fast` is the gateway-dir target per memory — verify in §0 deepen.)
- Existing agent-coding-relevant suites to borrow fixtures/patterns from (all under `apps/gateway/tests/`): `incremental_sse_streaming/`, `tool_translation/`, `anthropic_tool_use/` · `gemini_tool_use/` · `bedrock_tool_use/`, `stream_disconnect_billing/`, `stream_usage_completeness/`, `provider_seam/`.
- ⚠ Naming caution: `apps/gateway/tests/{cache_controls,semantic_cache,vector_cache,response_caching}/` are the proxy's OWN response cache — NOT provider prompt-caching passthrough (the v34 `prompt-cache-passthrough` task is distinct; do not conflate).
- Helios request shapes (reference, read-only, `../helios-mono/crates/helios-providers/src/openai_completions/convert.rs`): OpenAI-wire JSON with `tools`/`tool_choice`, `stream:true`, `role:"tool"` turns, `reasoning_effort`/`reasoning:{effort}` (NOT provider-native `cache_control`/`thinking.budget_tokens` — Helios speaks OpenAI wire).

Honors (patterns / conventions):
- Domain ports are `typing.Protocol`s with fakes injected via `app.state` — zero real network in unit/CI tests (PROJECT.md, folded v1). The stub is a Protocol fake, never a live call.
- The default path stays BYTE-IDENTICAL when a surface is not engaged (PROJECT.md v9/v10 invariant) — the harness must assert this no-op-passthrough baseline.
- `-m 'not e2e'` deterministic gate; live calls live in `scripts/live_*_verify.py`, never in the CI suite (foundation live-verify rule). One pytest process at a time (memory: concurrent runs cross-wipe the test DB).

Anchors the contract cites: `CompletionUpstream` (ports.py:122) · `get_completion_upstream` / `app.state.completion_upstream` (deps.py:80) · `completions` route (router.py:35) · the `complete()->(int,dict)` and `stream()->AsyncIterator[bytes]` signatures · the `apps/gateway/tests/conftest.py` `app`/`client` fixtures · the pure translator helpers (`_openai_to_anthropic_request`, `_translate_anthropic_sse`, …) for SEAM A.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: agent-coding stub harness — a reusable test-support module + fixture library that lets every v34 task prove Helios's OpenAI-wire agent traffic deterministically, in CI, with zero live network.

Framings weighed: programmable stub + fixture library (chosen) · record/replay VCR cassettes · per-task ad-hoc fakes
  - chosen: a scriptable `StubCompletionUpstream` (Protocol fake) + a single shared fixture library serving BOTH test seams — deterministic, secret-free, one canonical source of truth.
  - VCR cassettes: rejected — captures provider secrets/PII, brittle to provider wire drift, and parallel-tool / mid-stream-disconnect determinism is hard to author from a recording.
  - per-task ad-hoc fakes: rejected — duplication + inconsistent fidelity; the confirmed "CI stub layer" deliverable would not exist as a real artifact.

Must:
<must>
  - Provide `StubCompletionUpstream` implementing the `CompletionUpstream` Protocol (SEAM B): scriptable to return a canned `(status, body)` from `complete()`, and to yield a canned, ordered list of SSE byte-frames from `stream()`; injectable via `app.state.completion_upstream`. It records the exact `payload` it was forwarded so a test can assert what the proxy sent upstream.
  - Provide a fixture library of Helios-REAL OpenAI-wire REQUEST bodies, one canonical copy each, covering: non-stream chat · streaming chat · single tool-call · ≥2 PARALLEL tool-calls in one turn · multi-turn `role:"tool"` follow-up · `reasoning_effort` and `reasoning:{effort}`.
  - Provide provider-NATIVE response/SSE fixtures (Anthropic · Gemini · Bedrock · OpenRouter/OpenAI-wire) for those same cases, to feed SEAM-A pure-translator tests AND SEAM-C transport handlers.
  - Provide SEAM C: a helper that wires a REAL adapter to an `httpx.MockTransport` (swapping its internal `_client`) plus a context manager that sets/resets the provider-credential contextvar — so a test exercises the real adapter end-to-end against native bytes with no socket.
  - Expose a helper to read back the recorded usage row for a SEAM-B request, so the billing/disconnect tasks reuse one assertion path.
  - Assert the BYTE-IDENTICAL no-op baseline: a plain chat request (no tools/reasoning/cache engaged) routed through the stub yields the same client-visible SSE/body as the documented default path (guards the v9/v10 invariant for every later task).
  - Join the existing `-m 'not e2e'` suite; fully deterministic (stable frame order; no wall-clock/random dependence); zero live network and zero real provider key at collect or run time.
</must>
Reject:
<reject>
  - a stub stream scripted with a frame that is not well-formed `data: …\n\n` SSE -> "invalid_sse_fixture" (the stub validates frames at construction so a typo fails loud, not as a mysterious downstream assert)
  - asking the stub for a behavior it was not scripted for (e.g. `stream()` when only `complete()` was scripted) -> "stub_unscripted" (never silently return empty — a missing script is a test author error)
  - a request fixture that drifts from what Helios actually emits on the wire -> "unfaithful_fixture" (each fixture is tagged with its `convert.rs` provenance; the live-smoke task is the runtime cross-check)
</reject>
After:
<after>
  - Any v34 task imports the harness (`from tests._helios_harness import …`) and writes a deterministic test in either seam against a shared fixture — no re-authoring of Helios request bodies.
  - The harness's own suite is green under the `-m 'not e2e'` CI gate and asserts the no-op baseline + all three reject guards.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The TWO-SEAM split is sufficient — SEAM B (stub at `app.state.completion_upstream`, post-translation) for route/billing/disconnect + SEAM A (pure translator helpers) for the wire mappings — and no v34 behavior falls between them needing a THIRD seam (real adapter against a mocked httpx transport). Lowest confidence because I confirmed the two existing patterns but did not exhaustively check that e.g. cache-token billing is observable without running the real adapter end-to-end. If wrong: the harness must also ship a transport-mock helper (medium added scope) — better to discover now than mid-build.
  - [ ] The Helios request shapes read statically from `convert.rs` match what Helios emits at runtime — confirm in `helios-live-smoke`; if wrong, fixtures need a refresh (low cost, isolated to fixture files).
  - [ ] `-m 'not e2e'` (the gateway pyproject gate) is the right CI hook for the harness suite, vs a new dedicated marker — trivial to change.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: SEAM-B stub serves a scripted non-stream completion and records the forwarded payload
  Given a StubCompletionUpstream scripted to return (200, {canned body+usage}) for complete()
  And it is injected at app.state.completion_upstream
  When a test posts a Helios non-stream chat fixture to /v1/chat/completions
  Then the client receives the canned body
  And the stub exposes the exact payload the proxy forwarded (asserted equal to the sent fixture, model-resolution aside)

Scenario: SEAM-B stub yields scripted SSE frames in order
  Given a StubCompletionUpstream scripted with an ordered list of well-formed data: frames ending in [DONE]
  When a test posts a streaming Helios fixture to /v1/chat/completions
  Then the client receives exactly those frames, in that order, terminated by [DONE]

Scenario: SEAM-A fixture drives a real translator helper for parallel tool-calls (non-stream response)
  Given the Anthropic-native non-stream response fixture with two tool_use content blocks
  When the test calls _anthropic_to_openai on the native body directly
  Then the OpenAI-wire message.tool_calls contains both tools (finish_reason "tool_calls")
  # NOTE: native fixtures are dict (non-stream body) for SEAM A · list[bytes] raw SSE for SEAM C — matches frozen ProviderFixture.native. Streaming parallel-tool translation is proven by SEAM C below (real adapter exercises _AnthropicSSEStepper end-to-end).

Scenario: SEAM-C real adapter streams native bytes through a mocked transport
  Given a real AnthropicCompletionUpstream wired to httpx.MockTransport returning the native two-tool_use SSE fixture
  And a fake provider credential set on the contextvar for the block
  When the test consumes the adapter's stream() for the parallel-tool request fixture
  Then the adapter yields OpenAI-wire SSE with two tool_calls (index 0 and 1)
  And no real socket is opened (the transport handler is the only I/O)

Scenario: provider-native response fixtures exist for every covered case and provider
  Given the fixture library
  When a test enumerates request cases × {anthropic, gemini, bedrock, openrouter} 
  Then a native response/SSE fixture is present for each, each tagged with its convert.rs/provider provenance

Scenario: usage-row readback helper returns the recorded row for a SEAM-B request
  Given a SEAM-B request completed through the stub with a usage-bearing body
  When the test calls the harness usage-readback helper
  Then it returns the single recorded usage row (tokens/cost fields) for that request

Scenario: byte-identical no-op baseline
  Given a plain chat request engaging no tools/reasoning/cache
  When it is routed through the stub (canned to mirror a default upstream reply)
  Then the client-visible body/SSE is byte-identical to the documented default-path output

Scenario: the harness suite runs in the non-e2e CI gate with no live network
  Given the harness suite
  When it is collected and run under `-m 'not e2e'`
  Then it passes with zero outbound network calls and no real provider key present

Scenario: REJECT a malformed SSE frame in a stub stream script
  Given a StubCompletionUpstream constructed with a frame that is not well-formed `data: …\n\n`
  When the stub is constructed
  Then it raises "invalid_sse_fixture"
  And no partially-built stub is returned (construction fails atomically; nothing injected)

Scenario: REJECT asking the stub for an unscripted behavior
  Given a StubCompletionUpstream scripted only for complete()
  When a test triggers its stream() path
  Then it raises "stub_unscripted"
  And it never silently yields an empty/zero-frame stream (no usage row is recorded for the unscripted call)

Scenario: REJECT a fixture lacking wire provenance
  Given a request fixture added without a convert.rs/provider provenance tag
  When the fixture-provenance test enumerates the library
  Then it fails with "unfaithful_fixture"
  And the existing tagged fixtures remain unchanged (the guard only flags the untagged addition)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Module (importable test support, non-e2e): apps/gateway/tests/_helios_harness/__init__.py

# ── SEAM B — programmable stub (implements domain.ports.CompletionUpstream) ──
class StubCompletionUpstream:
    def __init__(self, *, complete: tuple[int, dict[str, object]] | None = None,
                          stream: list[bytes] | None = None) -> None
        # validates each stream frame matches well-formed SSE (b"data: " … b"\n\n");
        #   a malformed frame -> raise HarnessError("invalid_sse_fixture") at construction (atomic: no instance returned)
    forwarded: list[dict[str, object]]                     # payloads received, in call order (assert what the proxy sent)
    async def complete(self, payload) -> tuple[int, dict]  # records payload; if complete not scripted -> HarnessError("stub_unscripted")
    def stream(self, payload) -> AsyncIterator[bytes]      # records payload; if stream not scripted -> HarnessError("stub_unscripted")

class HarnessError(AssertionError):
    code: Literal["invalid_sse_fixture", "stub_unscripted", "unfaithful_fixture"]

# ── Fixture library — one canonical copy per case ──
HeliosCase = Literal["chat", "chat_stream", "tool_call", "parallel_tool_calls",
                     "tool_result_followup", "reasoning_effort"]
Provider   = Literal["anthropic", "gemini", "bedrock", "openrouter"]

def helios_request(case: HeliosCase) -> dict[str, object]
    # the canonical Helios OpenAI-wire request body for `case` (faithful to convert.rs)
def provider_fixture(case: HeliosCase, provider: Provider) -> ProviderFixture
class ProviderFixture(TypedDict):
    native: dict[str, object] | list[bytes]   # non-stream native body OR ordered native SSE frames
    provenance: str                           # non-empty source tag (convert.rs ref / provider docs) — REQUIRED

# ── SEAM C — real adapter against a mocked httpx transport ──
TransportHandler = Callable[[httpx.Request], httpx.Response]
def wire_mock_transport(adapter: object, handler: TransportHandler) -> None
    # swaps adapter._client for httpx.AsyncClient(base_url=<existing>, transport=httpx.MockTransport(handler)).
    # Exercises the REAL adapter (request build · auth · SSE parse · breaker · error map); zero socket.
def sse_handler(frames: list[bytes], *, status: int = 200) -> TransportHandler
    # convenience: a handler streaming the given native SSE frames (text/event-stream)
@contextmanager
def fake_provider_credential(secret: str = "test-key") -> Iterator[None]
    # set_provider_credential(BearerCredential(secret=secret)) on enter; reset_provider_credential in finally

# ── pytest fixtures (registered in apps/gateway/tests/conftest.py) ──
stub_upstream  : factory(*, complete=None, stream=None) -> StubCompletionUpstream
                 # builds + installs on app.state.completion_upstream, returns it
recorded_usage : async (model: str | None = None) -> UsageRecordRow | None
                 # the single usage-ledger row recorded for the last SEAM-B request (read-only)

# ── provenance guard ──
def assert_fixtures_have_provenance() -> None
    # enumerates the library; any case×provider entry with empty/missing provenance -> HarnessError("unfaithful_fixture")

Reject -> response:
  invalid_sse_fixture -> HarnessError(code="invalid_sse_fixture")  raised in StubCompletionUpstream.__init__
  stub_unscripted     -> HarnessError(code="stub_unscripted")      raised on the unscripted complete()/stream() call
  unfaithful_fixture  -> HarnessError(code="unfaithful_fixture")   raised by assert_fixtures_have_provenance()

Schema: NONE — no migration. `recorded_usage` reads the existing usage-ledger ORM row
        (gateway.usage.infrastructure.orm.UsageRecordRow, table usage_records) read-only;
        the stub adds no table/column.
```

Status: FROZEN @ v1 — approved by Tin (2026-06-23). Changing this contract = change request back to SPECIFY.
Least-sure flag surfaced at freeze: [contract] two-seam sufficiency — a post-translation stub (SEAM B) + pure helpers (SEAM A) may miss a behavior only observable through the real adapter; if wrong a third seam is needed. RESOLVED at freeze by adding SEAM C (real adapter + httpx.MockTransport). [test] byte-identical no-op baseline rests on a reference output — if circular the test proves nothing; resolved via an explicit golden constant in the tests phase (_GOLDEN_NOOP_BODY).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥80% (the gateway `--cov-fail-under=80` gate); the `_helios_harness` module fully exercised by its own suite.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_stub_complete_records_payload: arrange StubCompletionUpstream(complete=(200, body)) installed via stub_upstream / act POST non-stream helios_request("chat") / assert client gets body AND stub.forwarded[0] == sent payload (model aside)
  - test_stub_streams_frames_in_order: arrange stub(stream=[f1,f2,[DONE]]) / act POST helios_request("chat_stream") / assert client SSE == [f1,f2,[DONE]] in order
  - test_seam_a_parallel_tool_translation: arrange provider_fixture("parallel_tool_calls","anthropic").native (dict non-stream body, 2 tool_use blocks) / act _anthropic_to_openai(native) / assert message.tool_calls has both tools AND finish_reason "tool_calls"
  - test_seam_c_real_adapter_via_mock_transport: arrange real AnthropicCompletionUpstream + wire_mock_transport(sse_handler(provider_fixture("parallel_tool_calls","anthropic").native as list[bytes])) + fake_provider_credential() / act consume adapter.stream(helios_request("parallel_tool_calls")) / assert OpenAI-wire SSE has tool_calls index 0 AND 1 AND no socket opened
  - test_fixture_matrix_present_and_tagged: act enumerate HeliosCase × Provider / assert provider_fixture exists for each AND provenance non-empty
  - test_recorded_usage_readback: arrange SEAM-B request with usage-bearing body / act await recorded_usage() / assert one UsageRecordRow with that request's tokens
  - test_byte_identical_noop_baseline: arrange plain chat (no tools/reasoning/cache), stub canned to mirror default reply / act POST / assert client body bytes == documented default-path bytes
  - test_harness_suite_non_e2e_no_network: assert suite collected under `-m 'not e2e'` AND a network-deny guard (e.g. patched transport) records zero outbound calls
  - test_reject_invalid_sse_fixture: act StubCompletionUpstream(stream=[b"oops no prefix"]) / assert raises HarnessError(code="invalid_sse_fixture") AND no instance/app.state mutation
  - test_reject_stub_unscripted: arrange stub(complete=...) only / act trigger stream() / assert raises HarnessError(code="stub_unscripted") AND no usage row recorded
  - test_reject_fixture_without_provenance: arrange a library entry with empty provenance / act assert_fixtures_have_provenance() / assert raises HarnessError(code="unfaithful_fixture") AND tagged entries untouched
</test_plan>

Tests live in: `apps/gateway/tests/agent_coding_harness/` · MUST run red (missing implementation) before Build.
RED confirmed (2026-06-23): `ImportError: cannot import name 'HarnessError' from 'tests._helios_harness'` (1 error) — red for the right reason (missing module, not a broken harness). GREEN after build: 43 passed; adjacent suites (proxy, anthropic_tool_use, anthropic_provider, tool_translation, response_format_translation) 114 passed — no regression. Independent re-run by orchestrator: 43 passed.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/tests/_helios_harness/` `apps/gateway/tests/conftest.py`
  — the harness module + fixtures (new package), and the conftest registration of the
  `stub_upstream` / `recorded_usage` fixtures. (Deliverable is TEST infrastructure → code lives
  under tests/, not src/. The harness's OWN red suite at `apps/gateway/tests/agent_coding_harness/`
  is written in §4, not here.) NO src/ change, NO migration.
Strategy (ordered batches): 1. HarnessError + StubCompletionUpstream (SSE validation, payload recording, scripted complete/stream + unscripted guards). 2. Fixture library: helios_request cases + provider_fixture matrix with provenance tags. 3. assert_fixtures_have_provenance guard. 4. SEAM C: wire_mock_transport + sse_handler + fake_provider_credential. 5. conftest fixtures stub_upstream + recorded_usage. 6. green the §4 suite.
Safety rule (feature-specific): the stub NEVER opens a socket — it is a pure in-memory Protocol fake; fixtures are static literals (no network, no real keys); construction validation is atomic (raise before any partial state).
Code lives in: `apps/gateway/tests/_helios_harness/`
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

- [x] all tests pass — 43 passed (independent orchestrator re-run); 114 passed incl. adjacent suites
- [x] coverage did not decrease — harness is new test-support code; subset-run cov-fail is an artifact (the 80% gate is whole-suite); no src/ touched so prod coverage unchanged
- [x] no test or contract was altered during build — §3 FROZEN @ v1 unchanged; only tests/_helios_harness + conftest added (no src/, no migration)
- [x] the green was EARNED — orchestrator refute-read of the harness module + the 4 riskiest tests: SEAM-C runs the REAL AnthropicCompletionUpstream+_AnthropicSSEStepper (two distinct tool ids from streamed native bytes), byte-identical baseline asserts against an explicit _GOLDEN_NOOP_BODY constant (not circular), reject tests assert exact .code, fixtures faithful to convert.rs wire. No vacuous asserts found.
- [x] concurrency / timing — stub is pure in-memory (socket-deny test proves no socket); deterministic frame order; honors the one-pytest-process rule
- [x] no exposed secrets / injection / unexpected deps — fixtures are static literals, no real keys; BearerCredential uses SecretStr; allow-list packages only (pytest, httpx)
- [x] layering & dependencies follow CONVENTIONS.md — test-only module; imports existing domain ports/credential helpers; no layering violation
- [x] a person reviewed and approved the change — Tin approved the frozen contract (the decision point); verify auto-gates on evidence under autonomy:auto; orchestrator performed the adversarial review

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] A test author can script the stub and assert the forwarded payload + client SSE — confirmed by test_stub_complete_records_payload / test_stub_stream_records_payload (stub.forwarded == [payload]; collected == frames)
- [x] The real Anthropic adapter, fed native two-tool_use streaming bytes via MockTransport, emits OpenAI-wire SSE with two distinct tool_calls — confirmed by test_seam_c_real_adapter_via_mock_transport (get_weather + get_time, 2 unique ids)
- [x] The default path is byte-identical when no surface is engaged — confirmed by test_byte_identical_noop_baseline (resp.json() == _GOLDEN_NOOP_BODY)
- [x] All three reject codes fire loud — confirmed by the three reject tests asserting exc.code

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every harness symbol (StubCompletionUpstream, helios_request, provider_fixture, assert_fixtures_have_provenance, wire_mock_transport, sse_handler, fake_provider_credential, stub_upstream, recorded_usage) is referenced by ≥1 test; conftest fixtures resolve in the suite run
- [x] DEAD-CODE (code) — no orphaned symbol; the §3 API surface is fully exercised; no unused exports introduced
- [x] SEMANTIC (prose / non-code) — read the harness module + 4 riskiest tests in full (not skimmed): confirmed fixtures match real provider shapes and the SEAM-C native SSE is genuine

### GATE RECORD
Outcome: PASS
Reviewed by: Claude (orchestrator, adversarial refute-read) · approved-contract: Tin · date: 2026-06-23

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): harness suite stays green in CI (`-m 'not e2e'`); fixture-provenance guard stays green as cases are added; SEAM-C tests stay green as adapters evolve (catches translator regressions).

### Spec delta
- [SPEC · seeded] parallel-tool STREAMING already works in the real Anthropic adapter (evidence: test_seam_c_real_adapter_via_mock_transport — 2 distinct tool ids from native two-block SSE). The v34 `parallel-tool-streaming-verify` task is now mostly a coverage-extension to Gemini+Bedrock, not a fix. [parallel-tool-streaming-verify]
- [SPEC · open] add Gemini + Bedrock provider_fixture rows for tool/parallel/reasoning cases (today the matrix is Anthropic-deep + chat-only for the OpenAI-wire providers) — the streaming verify + cache + reasoning tasks will need them. [agent-coding-stub-harness]
- [SPEC · open] `recorded_usage` reads a synchronous harness recorder, NOT the real Redis→Postgres flush path — the disconnect/billing tasks that assert cost must confirm the real recorder computes the same fields, or add a flush-aware readback. [disconnect-billing-all-providers]
- [SPEC · open] no SEAM-C helper for non-Anthropic adapters yet (wire_mock_transport is generic via `_client` swap, but only Anthropic has a native streaming fixture); add when prompt-cache/reasoning tasks touch Gemini/Bedrock. [agent-coding-stub-harness]

### Competency deltas
- [TDD · folded] discovering the real test-injection seam (pure helpers vs post-translation stub vs real-adapter+MockTransport) BEFORE specifying prevented a wrong contract — the §3 freeze flag (two-seam sufficiency) was resolved by adding SEAM C, not discovered mid-build (evidence: SEAM C added at freeze on Tin's call). [folded foundation-version 31]
- [ADD · folded] a frozen `native: dict | list[bytes]` type was kept intact by SPLITTING coverage (SEAM A non-stream dict, SEAM C stream bytes) instead of widening the contract — a scenario/test refinement, not a change-request (evidence: §2/§4 edits pre-build, §3 untouched). See the `add` skill's `deltas.md`. [folded foundation-version 31]
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
