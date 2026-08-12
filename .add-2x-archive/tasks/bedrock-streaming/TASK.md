# TASK: ConverseStream event-stream -> OpenAI SSE (reuse v19 pre-first-byte resilience)

slug: bedrock-streaming · created: 2026-06-15 · stage: production
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
- `apps/gateway/src/gateway/proxy/infrastructure/bedrock_eventstream.py` (NEW) — pure, total decoder for
  AWS `vnd.amazon.eventstream` binary framing. `decode_event_stream(data: bytes) -> Iterator[tuple[dict[str,str], bytes]]`
  yielding (string-headers, payload) per message; validates prelude CRC + message CRC (`binascii.crc32`);
  raises `EventStreamError` on CRC mismatch / truncation. Mirrors the bedrock_sigv4 "new pure sub-system"
  pattern (stdlib only, no boto3; pinnable to byte vectors).
- `apps/gateway/src/gateway/proxy/infrastructure/bedrock_upstream.py` (MODIFY) — replace the `stream()`
  NotImplementedError stub (bedrock_upstream.py:314) with the real impl; add pure translator
  `_converse_stream_to_openai_sse(events, *, model_id) -> Iterator[bytes]` (ConverseStream events → OpenAI
  `chat.completion.chunk` SSE frames). Reuses `_map_finish_reason` (already in this module).
- REUSE: `bedrock_sigv4.sign_request` (raw model_id in path, same url to sign+request — v20 task-2 %3A lesson);
  the per-instance `CircuitBreaker` (already a field on the adapter); `_endpoint`/`_credentials`/`_region`.

Context (working folder): apps/gateway. Tests: `cd apps/gateway && uv run pytest -p no:cacheprovider --no-cov
-q tests/bedrock_streaming`. Streaming tests use `httpx.MockTransport` returning a 200 with raw
event-stream BYTES (content-type application/vnd.amazon.eventstream) built by a test helper; drain via
`[c async for c in adapter.stream({...})]`. The binary frame layout was verified BYTE-FOR-BYTE against
botocore's `EventStreamBuffer` oracle (round-trip parse OK): 12-byte prelude (total_len u32, headers_len u32),
prelude_crc u32 = crc32(first 8B), headers, payload, message_crc u32 = crc32(all-but-last-4B); header =
name_len(1)+name+type(1); string type-code = 7 → value_len(2 BE)+value.

Honors (patterns / conventions):
- `CompletionUpstream.stream()` (ports.py:116) is a SYNC `def` returning `AsyncIterator[bytes]` via an inner
  `async def _gen()`; yields OpenAI `data: {json}\n\n` frames; terminal `data: [DONE]\n\n`. (anthropic/gemini
  template: anthropic_upstream.py:590, gemini_upstream.py:575.)
- BUFFER-THEN-TRANSLATE (matches anthropic/gemini): drain the whole upstream (`aiter_bytes`) into one buffer,
  then decode + translate synchronously, then yield. A pre-first-yield failure is therefore pre-first-byte →
  the v19 `open_resilient_stream` (streaming_resilience.py:24) fails it over; once a chunk ships it's committed.
- STREAM RETRY RULE (v19, upstream_retry.py:13): stream() is NEVER retried; only the circuit breaker guards it
  (raise `CircuitOpenError`/`UpstreamUnavailableError` BEFORE the first yield).
- STREAMING BILLING (use_cases.py:1380): usage is teed post-stream by `extract_usage_from_sse(collected)`
  (extractor.py:20) reading the LAST `data:` frame carrying a top-level `"usage"`. So the terminal pre-[DONE]
  frame MUST carry `usage:{prompt_tokens,completion_tokens,total_tokens}` (v12 billing contract).
- SECRET DISCIPLINE: secret_access_key never logged; Authorization MAC never logged; content=body_bytes so the
  signed x-amz-content-sha256 matches the wire body.

Anchors the contract cites: `decode_event_stream`, `EventStreamError`, `BedrockCompletionUpstream.stream`,
`_converse_stream_to_openai_sse`, `_map_finish_reason`; `sign_request`; the ConverseStream event types
(messageStart · contentBlockDelta · contentBlockStop · messageStop · metadata) and the OpenAI
chat.completion.chunk + usage SSE shape.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: streaming Bedrock chat — translate AWS ConverseStream (binary event-stream) into OpenAI-compatible
SSE `chat.completion.chunk` frames on the existing stream() seam, signed with SigV4, with usage on the
terminal frame for billing, and pre-first-byte failover preserved.

Framings weighed: hand-rolled stdlib event-stream decoder + buffer-then-translate (chosen — matches the
no-SDK rule and the anthropic/gemini streaming template; the binary framing is verified against the botocore
oracle; incremental decode is the carried TTFB open, not this task) · depend on botocore.eventstream (rejected
— violates the no-boto3 foundation rule) · raw pass-through of event-stream bytes (rejected — clients speak
OpenAI SSE, not vnd.amazon.eventstream).

Must:
<must>
  - decode_event_stream(data) decodes concatenated AWS event-stream messages: it validates the prelude CRC and
    the message CRC (binascii.crc32), parses string-typed headers, and yields (headers, payload_bytes) per
    message in order.
  - stream(payload) opens `POST {endpoint}/model/{modelId}/converse-stream`, SigV4-signed (raw modelId in the
    path, the IDENTICAL url string handed to sign_request and client.stream; content=body_bytes so the signed
    payload hash matches), drains the upstream bytes, decodes them, and yields OpenAI SSE frames.
  - the translated SSE sequence is: a role chunk (delta.role="assistant") on messageStart; a content chunk
    (delta.content=<text>) per contentBlockDelta with delta.text; a TERMINAL chunk carrying
    finish_reason (_map_finish_reason of messageStop.stopReason) AND top-level
    usage:{prompt_tokens,completion_tokens,total_tokens} (from the metadata event's usage); then
    `data: [DONE]\n\n`. Each frame is `data: {json}\n\n`; object="chat.completion.chunk".
  - usage maps inputTokens→prompt_tokens, outputTokens→completion_tokens, totalTokens→total_tokens (fallback
    input+output when totalTokens absent); zero when the metadata event is absent (never crash).
  - a 5xx / connect-error / timeout opening the stream raises UpstreamUnavailableError BEFORE the first yield
    (pre-first-byte → v19 failover); an open circuit raises before any upstream call. stream() is NEVER retried.
  - the adapter still satisfies the CompletionUpstream Protocol; with no AWS creds the adapter is absent
    (byte-identical) — unchanged from task 2.
</must>
Reject:
<reject>
  - a frame whose prelude CRC or message CRC does not match -> raise "EventStreamError" (corruption is never
    silently yielded as content).
  - a truncated buffer (fewer bytes than the declared total_len) -> raise "EventStreamError" (no partial frame).
  - an upstream `exception`/`error` event (`:message-type` != "event") mid-stream -> surface as
    UpstreamUnavailableError if seen before the first content frame; never emit it as assistant content.
</reject>
After:
<after>
  - a streaming chat completion to a Bedrock model yields OpenAI-compatible SSE chunks ending in [DONE], with
    usage on the terminal frame billed once post-stream; a pre-first-event failure fails over; no regression
    on the committed floor.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ CONVERSESTREAM EVENT SHAPE — lowest confidence: the exact event-type names + payload shapes
    (messageStart{role}, contentBlockDelta{delta:{text}}, messageStop{stopReason}, metadata{usage:{inputTokens,
    outputTokens,totalTokens}}) must match AWS. Mitigation: decode defensively (skip unknown event types;
    concat all contentBlockDelta text; usage from metadata only) and pin fixtures built via the
    botocore-verified framing; the live double-pass (task 6) confirms against a real-shaped stub. If wrong:
    wrong/empty content or usage — caught by the translation tests + live verify. Confidence: 0.8.
  - [x] binary frame layout + CRC algorithm — VERIFIED byte-for-byte against botocore EventStreamBuffer
    (round-trip parse OK; crc32 IEEE; string type-code 7). Confidence: 0.97.
  - [x] usage must ride the terminal pre-[DONE] frame for extract_usage_from_sse — confirmed at extractor.py:20
    (reverse scan for the last `data:` with `"usage"`). Confidence: 0.95.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: BS1 — decode a valid multi-message event-stream
  Given a buffer of concatenated AWS event-stream frames (messageStart, contentBlockDelta×2, messageStop, metadata)
   built with the botocore-verified framing
  When decode_event_stream(buffer) runs
  Then it yields one (headers, payload) per message in order, with :event-type readable and JSON payload intact

Scenario: BS2 — CRC mismatch is rejected
  Given an event-stream frame whose message CRC byte is corrupted
  When decode_event_stream runs
  Then it raises EventStreamError
  And no (headers, payload) tuple for the corrupted frame is yielded as if valid

Scenario: BS3 — truncated buffer is rejected
  Given a buffer shorter than its declared total_len
  When decode_event_stream runs
  Then it raises EventStreamError
  And no partial frame is yielded

Scenario: BS4 — stream() translates ConverseStream → OpenAI SSE end-to-end
  Given a MockTransport returning 200 with a full ConverseStream byte body (role + "Hello"+" world" + end_turn + usage)
  When stream() is drained
  Then the frames are chat.completion.chunk: a role delta, content deltas "Hello"/" world", a terminal frame with
   finish_reason "stop" and usage{prompt,completion,total}, then data:[DONE]
  And the request URL path is /model/<modelId>/converse-stream, SigV4-signed (Authorization AWS4-HMAC-SHA256)

Scenario: BS5 — usage rides the terminal frame for billing
  Given the drained SSE chunk list from BS4
  When extract_usage_from_sse(chunks) runs
  Then it returns {prompt_tokens, completion_tokens, total_tokens} matching the metadata event

Scenario: BS6 — finish_reason mapping in stream
  Given a ConverseStream whose messageStop.stopReason is "max_tokens"
  When stream() is drained
  Then the terminal frame finish_reason is "length"

Scenario: BS7 — pre-first-byte 5xx fails over (raises before first yield)
  Given a MockTransport returning 503
  When stream() is drained
  Then UpstreamUnavailableError is raised before any chunk is yielded
  And no partial SSE is emitted

Scenario: BS8 — protocol + signing path still hold
  Given a Bedrock model id with a ':' suffix
  When stream() issues the request
  Then the wire target routes to the exact model id (decoded once) and ':' single-encodes to %3A in the canonical URI
  And isinstance(adapter, CompletionUpstream) is True
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# NEW pure module — apps/gateway/src/gateway/proxy/infrastructure/bedrock_eventstream.py
class EventStreamError(Exception): ...
def decode_event_stream(data: bytes) -> Iterator[tuple[dict[str, str], bytes]]
    # Single-pass over the full buffer. Per message:
    #   prelude = data[off:off+12]; total_len, headers_len = struct.unpack(">II", prelude[:8])
    #   validate: binascii.crc32(data[off:off+8]) == u32 at data[off+8:off+12]   else EventStreamError
    #   validate: off+total_len <= len(data)                                     else EventStreamError (truncated)
    #   validate: binascii.crc32(data[off:off+total_len-4]) == u32 tail          else EventStreamError
    #   headers parsed from data[off+12 : off+12+headers_len]; ONLY string type (code 7):
    #     name_len(1) name type(1); if type==7: val_len(2 BE) val  (other types: skip per their width or raise)
    #   payload = data[off+12+headers_len : off+total_len-4]
    #   yield ({name: value}, payload); off += total_len
    # Empty data -> yields nothing. Raises EventStreamError on any CRC/truncation fault.

# MODIFY apps/gateway/src/gateway/proxy/infrastructure/bedrock_upstream.py
def _converse_stream_to_openai_sse(events: list[tuple[str, dict]], *, model_id: str) -> Iterator[bytes]
    # events = [(event_type, payload_dict)] in upstream order. Emits OpenAI SSE byte frames:
    #   messageStart            -> data: {chunk, choices:[{index:0, delta:{role:"assistant"}, finish_reason:null}]}\n\n
    #   contentBlockDelta(.delta.text) -> data: {chunk, choices:[{index:0, delta:{content:text}, finish_reason:null}]}\n\n
    #   (accumulate stopReason from messageStop; usage from metadata.usage)
    #   TERMINAL                -> data: {chunk, choices:[{index:0, delta:{}, finish_reason:<mapped>}],
    #                                     usage:{prompt_tokens,completion_tokens,total_tokens}}\n\n
    #   then                    -> data: [DONE]\n\n
    # chunk = {id:"", object:"chat.completion.chunk", created:int(time.time()), model:model_id}
    # finish_reason via _map_finish_reason(stopReason); usage zero-filled if metadata absent.

def stream(self, payload: dict[str, Any]) -> AsyncIterator[bytes]   # SYNC def, inner async def _gen()
    # model_id, converse_body = _openai_to_converse_request(payload, default_max_tokens=self._default_max_tokens)
    # url = f"{self._endpoint}/model/{model_id}/converse-stream"   # RAW model_id; same url to sign + stream
    # body_bytes = json.dumps(converse_body, separators=(",",":")).encode()
    # circuit breaker guard BEFORE the request (raise CircuitOpenError if open; record success/failure)
    # sign_request(method="POST", url=url, body=body_bytes, service="bedrock", region, credentials, now=UTC)
    #   headers = {**sig, "content-type":"application/json"}
    # async with self._client.stream("POST", url, content=body_bytes, headers=headers) as resp:
    #     if resp.status_code >= 500: raise UpstreamUnavailableError(...)        # pre-first-byte → failover
    #     if resp.status_code >= 400: raise UpstreamUnavailableError(...)        # streaming has no 4xx body path
    #     buf = b"".join([c async for c in resp.aiter_bytes()])
    # events = [(_event_type(h), json.loads(p)) for h,p in decode_event_stream(buf)]   # h[":event-type"]
    # for frame in _converse_stream_to_openai_sse(events, model_id=model_id): yield frame
    # 5xx/ConnectError/Timeout → UpstreamUnavailableError (record breaker failure); NEVER retried here
```
Schema: no DB/schema change. Billing reads the terminal frame usage via extract_usage_from_sse (unchanged seam).

Least-sure flag surfaced at freeze: [scenario] the ConverseStream event-type names + payload field paths
(messageStart.role / contentBlockDelta.delta.text / messageStop.stopReason / metadata.usage.*) — most likely
to drift from AWS; cost if wrong = empty content or zero usage. Mitigated by defensive decode + botocore-verified
framing + the task-6 live double-pass. (The binary framing + CRC itself is already oracle-verified → not the risk.)

Status: FROZEN @ v1 — approved by ADD auto-gate (autonomy:auto; non-security; framing oracle-verified) · 2026-06-15
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥90% on the two touched modules.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - A test helper `_es_message(event_type, payload_dict)` builds one authentic frame (the botocore-verified
    layout); `_es_stream(*messages)` concatenates. Fixtures are built, not recorded.
  - test_decode_multi_message (BS1): decode a 5-message stream → 5 tuples, :event-type + JSON intact, in order.
  - test_decode_crc_mismatch (BS2): flip a payload byte (post-CRC) → EventStreamError.
  - test_decode_truncated (BS3): slice the buffer short → EventStreamError.
  - test_stream_translation_end_to_end (BS4): MockTransport 200 + ConverseStream body → drained frames are
    chat.completion.chunk: role delta, content "Hello"/" world", terminal finish_reason "stop" + usage; last
    frame == b"data: [DONE]\n\n"; captured request path endswith /converse-stream; Authorization starts AWS4-HMAC-SHA256.
  - test_stream_usage_extractable (BS5): extract_usage_from_sse(drained) == {prompt_tokens:11,completion_tokens:5,total_tokens:16}.
  - test_stream_finish_reason_length (BS6): stopReason "max_tokens" → terminal finish_reason "length".
  - test_stream_5xx_pre_first_byte_raises (BS7): MockTransport 503 → drain raises UpstreamUnavailableError; zero chunks emitted before the raise.
  - test_stream_signs_converse_stream_path + test_protocol (BS8): wire raw_path decoded once == /model/<modelId>/converse-stream;
    quote(unquote(raw_path),safe="/~") single-encodes ':'→%3A (no %253A); isinstance(adapter, CompletionUpstream).
</test_plan>

Tests live in: `apps/gateway/tests/bedrock_streaming/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/bedrock_eventstream.py` `apps/gateway/src/gateway/proxy/infrastructure/bedrock_upstream.py` `apps/gateway/tests/bedrock_provider/test_bedrock_provider.py`
Strategy (ordered batches): 1. bedrock_eventstream.py (pure decoder + EventStreamError) green BS1-BS3. 2. bedrock_upstream.py: _converse_stream_to_openai_sse translator + replace stream() stub; green BS4-BS8. No main.py change (wiring done in task 2).
Safety rule (feature-specific): stream() never retries; circuit breaker guards the open; a CRC/truncation fault raises EventStreamError (never yields corrupt bytes as content); secret/Authorization never logged; content=body_bytes so the signed payload hash matches the wire.
Code lives in: the two declared §5 files.
Constraints: do NOT change any test or the contract; stdlib + existing httpx/signer/breaker only (no boto3); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 9/9 bedrock_streaming (BS1-BS8 + protocol); 63 combined with task-2/anthropic/gemini; no-DB floor exit 0
- [x] coverage did not decrease — additive pure decoder module + stream() impl + tests
- [x] no test or contract was altered to GAME a pass — the §3 contract was HONORED. ONE prior-task test was legitimately updated (task-2 `test_protocol_and_stream_stub`): the NotImplementedError stub-guard was RETIRED because task-3 implements stream() for real (the task-2 test's own docstring anticipated "implemented in v20 task 3"); the isinstance Protocol assertion was preserved and is now redundantly covered by bedrock_streaming::test_protocol. Declared in §5 scope + re-snapshotted. Not a weakening — a superseded stub guard.
- [x] the green was EARNED — adversarial review of the decoder + translator + stream(): decoder validates BOTH CRCs + truncation + unknown header types (robust header walk past non-string types); fixtures are authentic (CRCs computed, byte-flip lands in the body region) and were ROUND-TRIP verified against the botocore EventStreamBuffer oracle; BS7 enforces the pre-first-byte invariant (zero chunks before the raise); BS8 carries the task-2 %3A wire-routing assertion forward.
- [x] concurrency / timing safe — stream() mirrors AnthropicCompletionUpstream EXACTLY: breaker.guard() synchronously at the stream() call (pre-first-byte failover), on_upstream_error()/record_success() at the same points; status-check + full buffering happen BEFORE the first yield; stream() is NEVER retried (v19 rule).
- [x] no exposed secrets, injection openings, or unexpected dependencies — secret_access_key never logged; UpstreamUnavailableError messages carry only the status code or str(httpx_exc) (no secret/MAC); decoder is pure stdlib (struct + binascii.crc32), no boto3.
- [x] layering & dependencies follow CONVENTIONS.md — new pure sub-system module mirrors the bedrock_sigv4 pattern; stream() mirrors the anthropic/gemini template; usage on the terminal frame honors the v12 billing contract (extract_usage_from_sse seam unchanged).
- [x] a person reviewed and approved — auto-resolved under autonomy:auto on complete evidence (non-security); orchestrator adversarial read + botocore round-trip oracle stand in for the human read.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — decode_event_stream + EventStreamError consumed by stream(); _converse_stream_to_openai_sse consumed by stream(); stream() routable via ProviderAwareCompletionUpstream (wiring unchanged from task 2). No main.py change needed.
- [x] DEAD-CODE (code) — no orphaned symbol; _FIXED_WIDTHS table fully used in the header walk; `_drain`/`pytest` still referenced in the edited task-2 test (ruff clean).
- [x] SEMANTIC (prose / non-code) — read the decoder + translator + stream() in full vs the AWS EventStream spec + ConverseStream event shapes; confirmed frame layout + CRC algorithm + event→chunk mapping. NOTE (observe item): a corrupt 200 body raises EventStreamError (not UpstreamUnavailableError) before the first yield → propagates as 500, not failed-over. Within the frozen contract (decode faults raise EventStreamError); hardening to wrap it as UpstreamUnavailableError is a §7 follow-up.

### GATE RECORD
Outcome: PASS
Reviewed by: ADD auto-gate (orchestrator adversarial read + botocore EventStreamBuffer round-trip oracle) · date: 2026-06-15

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): Bedrock stream error rate; pre-first-event failover rate; EventStreamError
rate (decode faults on a 200 body); streaming usage-billing correctness; finish_reason distribution.
Spec delta for the next loop: bedrock-tools (task 4) extends the Converse mapping (toolConfig/toolUse) and will
add a streaming tool-call branch to the translator; a hardening follow-up: wrap a decode-time EventStreamError
on a 200 body as UpstreamUnavailableError so the resilience boundary fails it over (currently → 500).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.

- [SDD] `open` — AWS EventStream (vnd.amazon.eventstream) is hand-rollable in pure stdlib: 12-byte prelude
  (total_len u32, headers_len u32) + prelude_crc + headers + payload + message_crc, both CRCs = binascii.crc32
  (IEEE, NOT crc32c); headers = name_len(1)+name+type(1) with string type-code 7 → val_len(2 BE)+value. Decode
  by computing payload offset from headers_len (so unknown header types never block payload extraction); walk
  headers only to lift the string-typed ones. Verified by round-tripping a hand-built frame through botocore's
  EventStreamBuffer (oracle reused, no boto3 dep) — evidence: BS1-BS3 + the round-trip probe.
- [TDD] `open` — build streaming fixtures with an AUTHENTIC frame builder (real CRCs), then validate the builder
  against the vendor's own parser as an oracle BEFORE writing the decoder; a recorded-blob fixture would have
  hidden a framing bug. Evidence: the botocore round-trip gave 0.97 confidence on the binary layout pre-build.
- [ADD] `open` — when task N's implementation necessarily supersedes a prior task's stub-guard test, that is
  legitimate cross-task evolution, not test-weakening: retire only the stub assertion (keep the real ones),
  DECLARE the prior test file in task N's §5 scope, and re-snapshot so the scope-gate accepts the touch.
  Evidence: task-2 test_protocol_and_stream_stub NotImplementedError guard retired here.
