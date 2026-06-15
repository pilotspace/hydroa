# TASK: Non-streaming Bedrock Converse chat <-> OpenAI surface + provider dispatch wiring + usage billing

slug: bedrock-chat · created: 2026-06-15 · stage: production
autonomy: auto
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> v20 task 2/6 — the Bedrock chat adapter: map the OpenAI /v1/chat/completions surface to AWS Bedrock's
> Converse API (non-streaming), sign each request with the v20 task-1 SigV4 signer, wire a new "bedrock"
> provider into the v9 dispatch, and return an OpenAI-shaped body with accurate usage for billing.
> Streaming is task 3 (stream() is a documented NotImplementedError stub here); tools are task 4.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/proxy/infrastructure/bedrock_upstream.py` (NEW) — `BedrockCompletionUpstream`
  implementing the `CompletionUpstream` Protocol (ports.py:104). Mirrors `AnthropicCompletionUpstream`
  (anthropic_upstream.py) — the closest template (Converse ≈ Anthropic Messages). Pure mapping fns
  `_openai_to_converse_request`, `_converse_to_openai`, `_map_finish_reason`, `_bedrock_error_to_openai`.
- `apps/gateway/src/gateway/main.py` — wire `_chat_adapters["bedrock"] = BedrockCompletionUpstream(...)`
  under `if resolve_aws_credentials(settings):` (mirror the anthropic block at main.py:392-402;
  `app.state.chat_adapters` seam at ~main.py:420).
- REUSE: `bedrock_sigv4.sign_request` / `resolve_aws_credentials` (v20 task 1); `execute_with_retry`
  (upstream_retry.py:103); `CircuitBreaker`; the billing path reads `response_body["usage"]`.

Context (working folder): apps/gateway. Tests: `cd apps/gateway && uv run pytest -p no:cacheprovider
--no-cov -q tests/bedrock_provider`. Adapter tests use `httpx.MockTransport` (no network); wiring tests use
the app factory.

Honors (patterns / conventions):
- The `CompletionUpstream` Protocol: `async complete(payload) -> tuple[int, dict]` + `stream(payload) ->
  AsyncIterator[bytes]`. complete() returns `(status, OPENAI-SHAPED body)`.
- BILLING CONTRACT (v12, use_cases.py:1165): the returned OpenAI body MUST carry
  `usage: {prompt_tokens:int, completion_tokens:int, total_tokens:int}` — billing + TPM read these.
- RETRY SEAM (v19): complete() maps the body ONCE, then calls `execute_with_retry` with `_do_request` +
  `_render` closures; 4xx → `(status, error_body)`; 5xx/429/408 retried then `UpstreamUnavailableError`.
- SECRET DISCIPLINE: credentials come from `AwsCredentials` (secret never logged); the adapter signs PER
  REQUEST via `sign_request` injecting `datetime.now(UTC)` (the signer stays pure; the ADAPTER reads the
  clock). The signed Authorization MAC is never logged.
- DEFAULT-OFF: no AWS creds → `resolve_aws_credentials` None → no "bedrock" adapter → byte-identical.
- CONVERSE IS THE ONE SHAPE (milestone decision): map to/from Converse, not per-model InvokeModel bodies.

Anchors the contract cites: `BedrockCompletionUpstream`, `_openai_to_converse_request`,
`_converse_to_openai`, `_map_finish_reason`; `sign_request`; the Converse request/response shape; the
OpenAI usage billing fields.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: a non-streaming AWS Bedrock chat adapter exposing Bedrock models on the OpenAI
/v1/chat/completions surface via the Converse API, signed with SigV4, billed by exact Bedrock usage.

Framings weighed: mirror the Anthropic adapter against the Converse API (chosen — Converse is the unified
cross-family shape, ≈ Anthropic Messages, so the proven adapter template applies; one mapping surface) ·
per-model InvokeModel bodies (rejected — N per-family translators; Converse subsumes them) · a generic
"AWS" adapter (rejected — premature; Bedrock-runtime Converse is the concrete need).

Must:
<must>
  - complete(payload) maps the OpenAI body to a Converse request: role:"system" messages → top-level
    `system:[{text}]`; user/assistant messages → `messages:[{role, content:[{text}]}]`; max_tokens/
    temperature/top_p/stop → `inferenceConfig:{maxTokens,temperature,topP,stopSequences}`; the OpenAI
    `model` becomes the Converse URL modelId (`POST {endpoint}/model/{modelId}/converse`).
  - each request is SIGV4-signed via sign_request (service "bedrock", the adapter's region, now=UTC); the
    request carries a valid `Authorization: AWS4-HMAC-SHA256 …` header (+ x-amz-date, x-amz-content-sha256,
    + x-amz-security-token when the creds have a session token).
  - a 200 Converse response maps to an OpenAI chat.completion body: concatenated `output.message.content[].text`
    → choices[0].message.content; `stopReason` → finish_reason (end_turn→stop, max_tokens→length,
    stop_sequence→stop, content_filtered/guardrail_intervened→content_filter, tool_use→tool_calls); and
    `usage.inputTokens/outputTokens` → `usage.prompt_tokens/completion_tokens/total_tokens`.
  - a 4xx Converse error returns `(status, openai_error_body)` (no retry, no raise); a 5xx/429/timeout is
    retried via execute_with_retry and raises UpstreamUnavailableError on exhaustion.
  - the adapter satisfies the CompletionUpstream Protocol (isinstance check); stream() is a DOCUMENTED
    NotImplementedError stub (Bedrock streaming = v20 task 3) — never silently wrong.
  - main.py registers "bedrock" in chat_adapters IFF resolve_aws_credentials(settings) is truthy; absent
    creds → not registered → byte-identical.
</must>

Reject:
<reject>
  - no AWS creds configured -> "bedrock" adapter absent (NOT an error); routing to a bedrock model with no
    creds is a normal PROVIDER_UNAVAILABLE path (unchanged).
  - a malformed Converse response (missing output/usage) -> defensive defaults (empty content, zero usage)
    rather than a raise; never crash the request path.
</reject>

After:
<after>
  - a chat completion to a Bedrock model returns an OpenAI-compatible body with accurate usage billed once
    on the served attempt; with no creds, behavior is byte-identical to today.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ CONVERSE RESPONSE SHAPE — lowest confidence: the exact Converse 200 JSON (output.message.content list,
    usage.inputTokens/outputTokens/totalTokens, stopReason enum values) must match AWS. Mitigation: map
    defensively (concat all text blocks; sum tokens if totalTokens absent) and pin the shape in tests from
    the AWS Converse API docs; the live double-pass (task 6) confirms against a real-shaped stub. If wrong:
    wrong content/usage — caught by the mapping tests + live verify. Confidence: 0.8.
  - [ ] the OpenAI `model` field equals the Bedrock modelId (no prefix) — ModelRow.provider is a separate
    column, no id prefix convention (grep-confirmed). Confidence: 0.9.
  - [ ] signing per-request with now=UTC inside the adapter is correct (signer stays pure) — Confidence 0.95.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: BC1 — request mapping (system lift + inferenceConfig)
  Given an OpenAI body with a system message, a user message, max_tokens, temperature, stop
  When complete() builds the Converse request
  Then system → top-level system[{text}], user → messages[{role,content:[{text}]}], and
       inferenceConfig has maxTokens/temperature/stopSequences

Scenario: BC2 — request is SigV4-signed to the converse path
  Given a Bedrock model id with a ':' version suffix
  When complete() issues the request
  Then the URL is POST {endpoint}/model/{modelId}/converse and the request carries an
       Authorization header starting "AWS4-HMAC-SHA256" plus x-amz-date and x-amz-content-sha256

Scenario: BC3 — response mapping (content + finish_reason + usage)
  Given a 200 Converse response with output.message.content[{text}], stopReason, usage
  When complete() maps it
  Then choices[0].message.content is the concatenated text, finish_reason is mapped, and
       usage = {prompt_tokens, completion_tokens, total_tokens} from inputTokens/outputTokens

Scenario: BC4 — finish_reason map
  Given Converse stopReason values end_turn|max_tokens|stop_sequence|content_filtered|unknown
  When mapped
  Then they become stop|length|stop|content_filter|stop respectively

Scenario: BC5 — 4xx passthrough, 5xx raises
  Given a 400 Converse error then (separately) a 503
  When complete() runs
  Then the 400 returns (400, openai_error_body) with no raise; the 503 raises UpstreamUnavailableError

Scenario: BC6 — session token signed
  Given AwsCredentials WITH a session_token
  When complete() signs the request
  Then the request carries x-amz-security-token

Scenario: BC7 — protocol + stream stub
  Given the adapter
  Then isinstance(adapter, CompletionUpstream) is True
  And calling/iterating stream() raises NotImplementedError (documented: task 3)

Scenario: BC8 — wiring present/absent
  Given AWS creds set (resolve returns creds) then unset
  When the app is built
  Then "bedrock" is in app.state.chat_adapters when creds set, and absent when unset (byte-identical)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Module: apps/gateway/src/gateway/proxy/infrastructure/bedrock_upstream.py  (raw httpx; reuses bedrock_sigv4)

class BedrockCompletionUpstream:   # implements ports.CompletionUpstream
    def __init__(self, *, credentials: AwsCredentials, region: str, endpoint_url: str | None = None,
                 default_max_tokens: int = 4096, max_retries: int = 0, backoff_base: float = 0.5,
                 retry_deadline_s: float = 0.0, metrics_registry: MetricsRegistry | None = None) -> None
        # endpoint = endpoint_url or f"https://bedrock-runtime.{region}.amazonaws.com"
        # self._client = httpx.AsyncClient(timeout=Timeout(connect=10, read=120, write=120, pool=10))
        # self._breaker = CircuitBreaker()
    async def complete(self, payload: dict) -> tuple[int, dict]    # OpenAI body in, (status, OpenAI body) out
    def stream(self, payload: dict) -> AsyncIterator[bytes]        # raises NotImplementedError (v20 task 3)

# Pure mapping helpers (module-level, unit-tested):
_openai_to_converse_request(payload, *, default_max_tokens) -> tuple[str, dict]
    # returns (model_id, converse_body); converse_body = {messages:[{role,content:[{text}]}],
    #   system:[{text}] (omitted if none), inferenceConfig:{maxTokens,temperature,topP,stopSequences}
    #   (only keys present in payload; maxTokens defaults)}
_converse_to_openai(resp_json, *, model_id) -> dict
    # {id, object:"chat.completion", created:int(time()), model:model_id,
    #  choices:[{index:0, message:{role:"assistant", content:<concat text blocks>}, finish_reason}],
    #  usage:{prompt_tokens:inputTokens, completion_tokens:outputTokens, total_tokens:sum-or-totalTokens}}
_map_finish_reason(stop_reason: str|None) -> str
    # end_turn->stop, max_tokens->length, stop_sequence->stop, tool_use->tool_calls,
    # content_filtered->content_filter, guardrail_intervened->content_filter, else->stop
_bedrock_error_to_openai(resp_json, status) -> dict   # OpenAI error envelope {error:{message,type,code}}

complete() flow (mirrors anthropic): map body ONCE -> _do_request closure (build absolute converse URL,
  json-encode body to bytes, sign via sign_request(method="POST", url, body=bytes, service="bedrock",
  region=self._region, credentials=self._credentials, timestamp=datetime.now(UTC)), POST with signed
  headers + content-type application/json) -> _render closure (>=400 -> (status, _bedrock_error_to_openai);
  else (200, _converse_to_openai)) -> execute_with_retry(provider="bedrock", ...).

Wiring (main.py create_app, after the google block):
  _aws_creds = resolve_aws_credentials(settings)
  if _aws_creds:
      _chat_adapters["bedrock"] = BedrockCompletionUpstream(credentials=_aws_creds,
          region=settings.bedrock_region, endpoint_url=settings.bedrock_endpoint_url or None,
          default_max_tokens=settings.anthropic_default_max_tokens, max_retries=settings.upstream_max_retries,
          backoff_base=settings.upstream_retry_backoff_base_s, retry_deadline_s=settings.upstream_retry_deadline_s,
          metrics_registry=app.state.metrics_registry)

Billing: returned body.usage = {prompt_tokens,completion_tokens,total_tokens} — single-bill preserved.
NO change to use_cases / ports / billing formula. Default-off byte-identical (no creds -> no adapter).
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, v20 fully-autonomous mandate 2026-06-15).

Least-sure flag surfaced at freeze:
  ⚠ [contract] CONVERSE RESPONSE SHAPE — the 200 Converse JSON (output.message.content list, usage
    inputTokens/outputTokens/totalTokens, stopReason enum) must match AWS; map defensively (concat all
    text, sum tokens if totalTokens absent) and pin in tests from AWS Converse docs; live verify (task 6)
    confirms against a real-shaped stub. Cost if wrong: wrong content/usage — caught by mapping tests.
  ⚠ [test] BILLING USAGE FIELDS — the returned OpenAI body MUST carry usage.{prompt,completion,total}_tokens
    or billing+TPM silently record nothing; a test asserts these three are present + correct. Cost if wrong:
    $0/under-billing on real upstream calls.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (adapter + pure mapping fns).
Plan (one test per scenario, MockTransport for the client; mirror tests/anthropic_provider):
<test_plan>
  - test_request_mapping_system_lift (BC1): _openai_to_converse_request lifts system, builds inferenceConfig.
  - test_complete_signs_converse_path (BC2): MockTransport captures the request → URL /model/{id}/converse,
    Authorization starts "AWS4-HMAC-SHA256", x-amz-date + x-amz-content-sha256 present; ':' in id → %3A path.
  - test_response_mapping (BC3): _converse_to_openai → content concat, finish_reason, usage 3 fields correct.
  - test_finish_reason_map (BC4): each stopReason → OpenAI finish_reason.
  - test_4xx_passthrough_5xx_raises (BC5): 400 → (400, error body); 503 → UpstreamUnavailableError.
  - test_session_token_signed (BC6): creds with token → x-amz-security-token on the request.
  - test_protocol_and_stream_stub (BC7): isinstance CompletionUpstream; stream() raises NotImplementedError.
  - test_wiring_present_absent (BC8): creds set → "bedrock" in chat_adapters; unset → absent.
</test_plan>

Tests live in: `apps/gateway/tests/bedrock_provider/` · MUST run red before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/bedrock_upstream.py` `apps/gateway/src/gateway/main.py`
Strategy: 1. pure mapping fns (request/response/finish_reason/error). 2. BedrockCompletionUpstream (ctor,
  complete via sign_request + execute_with_retry, stream NotImplementedError stub). 3. main.py wiring guard.
  4. green the suite; pyright + ruff; no-DB floor.
Safety rule: sign per-request with now=UTC (signer stays pure); secret never logged; usage 3 fields always
  present in the returned body.
Code lives in: bedrock_upstream.py + main.py
Constraints: do NOT change any test/contract; reuse bedrock_sigv4 + execute_with_retry; allow-list only.
Test-format note (v19 lesson): ruff-format the new test files DURING the tests phase before the snapshot.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 19/19 bedrock_provider; 57 regression (anthropic/gemini/config/health); no-DB floor exit 0
- [x] coverage did not decrease — additive module + tests; floor green
- [x] no test or contract was altered during build — the §3 contract (raw model_id) was HONORED; a frozen-test DEFECT was corrected via the sanctioned change-request path (re-entered tests phase → re-snapshot), not edited mid-build
- [x] the green was EARNED, not gamed — adversarial refute-read FOUND a real bug: build double-encoded the model_id (`quote(quote(...))`) only to satisfy a wrong test assertion; that routes real Bedrock calls to a non-existent model `…v2%3A0` and breaks the SigV4 signature. Verified against the authoritative botocore SigV4Auth oracle (canonical `…v2%3A0`, wire `…v2:0`) → fixed build to raw model_id (contract-honoring) + strengthened the test to assert wire-routing + single-encode (now catches the double-encode regression)
- [x] concurrency / timing safe — per-instance CircuitBreaker; request mapping pure & once; SigV4 timestamp per-request; no shared mutable state
- [x] no exposed secrets, injection openings, or unexpected dependencies — secret_access_key stored privately + `field(repr=False)`; never logged/echoed/in metric labels/exceptions; Authorization MAC never logged; no new dependency (stdlib + existing httpx/signer/retry/breaker)
- [x] layering & dependencies follow CONVENTIONS.md — infrastructure adapter mirrors Anthropic/Gemini; reuses bedrock_sigv4 + execute_with_retry + CircuitBreaker
- [x] a person reviewed and approved the change — auto-resolved under autonomy:auto on complete evidence (not a security finding); the orchestrator's adversarial refute-read + botocore oracle stand in for the human read

### Deep checks
- [x] WIRING (code) — bedrock adapter registered in `_chat_adapters` iff `resolve_aws_credentials(settings)`; same dict feeds `ProviderAwareCompletionUpstream(adapters=_chat_adapters)` so `provider="bedrock"` is routable; partial/empty creds → None → absent
- [x] DEAD-CODE (code) — no orphaned symbol; removed the now-unused `quote` import after the raw_model_id fix
- [x] SEMANTIC (prose / non-code) — Converse mapping verified both ways: system-lift, messages content blocks, inferenceConfig (maxTokens default/temperature/topP/stopSequences), content concat, usage→prompt/completion/total (totalTokens fallback), finish_reason map, error envelope

### GATE RECORD
Outcome: PASS
Reviewed by: ADD auto-gate (orchestrator adversarial refute-read + botocore SigV4 oracle) · date: 2026-06-15

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch: Bedrock chat error rate; usage-billing correctness; finish_reason distribution.
Spec delta for the next loop: bedrock-streaming (task 3) replaces the stream() stub; bedrock-tools (task 4)
  extends the Converse mapping with toolConfig/toolUse/toolResult.

### Competency deltas
What did this loop teach the foundation? One line each, tagged (`DDD · SDD · UDD · TDD · ADD`), status `open`.

- [TDD] `open` — A MockTransport that only checks shape (not signature/routing) lets a green test hide a real wire bug; assert on the WIRE target (`req.url.raw_path`, decoded EXACTLY once) and pin canonicalization against an authoritative oracle (botocore SigV4Auth), not on `req.url.path` (httpx already decodes it once, so a second `unquote` masks double-encoding).
- [ADD] `open` — A frozen TEST can be wrong while the frozen CONTRACT is right: when build output and a test assertion conflict, verify against ground truth FIRST (here botocore), then correct the test via the change-request path (re-enter tests → re-snapshot) — never bend the build to a false assertion. The adversarial refute-read + external oracle is what turned "19/19 green" into a caught critical bug.
- [SDD] `open` — AWS SigV4 path handling for Bedrock: send the model_id RAW in the URL path (literal `:`); the SAME url string goes to both `sign_request` and `client.post`; AWS single-encodes `:`→`%3A` for the canonical URI exactly as `quote(path, safe="/~")` does. Double-encoding routes to a non-existent model and breaks signing.
