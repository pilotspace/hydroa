# MILESTONE: Enterprise provider: AWS Bedrock (SigV4 · Converse chat/stream/tools · Titan embeddings)

goal: a tenant can call AWS Bedrock models through the proxy on the same OpenAI-compatible surface — chat, streaming, tool-use, embeddings — authenticated via SigV4, billed accurately, opt-in and byte-identical when disabled
rationale: Intake → `new-major` (project-lead decision, auto mode, 2026-06-15). The LiteLLM-parity arc's reliability pillar (v19) is done; the next-largest enterprise-grade gap is NATIVE ENTERPRISE-CLOUD providers. AWS Bedrock is the #1 enterprise demand (data residency in the customer's own cloud; Claude/Titan/Llama/Mistral via one API). The gateway already has the provider-dispatch seam (v9 ProviderAwareCompletionUpstream), tool-translation (v10), response_format (v11), and pre-first-byte streaming resilience (v19) — Bedrock is BREADTH on those seams, with ONE genuinely new sub-system: AWS SigV4 request signing (credential-based HMAC auth, unlike the bearer-token providers). Use the Bedrock **Converse / ConverseStream** API (unified across model families) so we map ONE request/response shape, not per-model bodies. Azure OpenAI is the sibling enterprise provider — deferred to its own milestone (v21).

stage: production · status: active · created: 2026-06-15

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  SIGV4 AUTH — an AWS Signature V4 signer for the bedrock-runtime service (HMAC-SHA256 canonical
     request → string-to-sign → signing key → `Authorization` header + `x-amz-date`; SHA256 payload
     hash; optional `x-amz-security-token` for STS/role creds). Credentials resolved from config/env
     (access key id + secret + region + optional session token); NEVER logged/echoed/in metric labels/URLs.
     CHAT — non-streaming Bedrock chat via the Converse API (`POST /model/{modelId}/converse`), mapped
     both ways to the OpenAI-compatible `/v1/chat/completions` surface (system/messages → Converse
     `system`+`messages` content blocks; `stopReason`+`usage` → choices+usage). Wired into the v9
     provider-dispatch seam (a new `bedrock/<modelId>` provider route).
     STREAMING — Bedrock `ConverseStream` (AWS event-stream framing) → OpenAI SSE chunks; reuses the v19
     pre-first-byte resilience boundary (a pre-first-event failure is replayable; once a chunk ships it's
     committed).
     TOOL-USE — Converse `toolConfig` ↔ the v10 canonical tool seam (OpenAI tools/tool_calls ↔ Bedrock
     `toolUse`/`toolResult` content blocks; reuse `tool_translation.py` canonical shapes).
     EMBEDDINGS — Amazon Titan Embeddings (`POST /model/{modelId}/invoke`) → OpenAI `/v1/embeddings`
     shape; reuses the v9 embeddings dispatch + exact token accounting.
     BILLING — Bedrock usage (`inputTokens`/`outputTokens` from Converse `usage`) recorded on the served
     attempt only; per-model Bedrock pricing in the catalog; cache/retry/fallback billing invariants
     (v12/v19) preserved.
     VERIFICATION — a live double-pass through the TLS edge against a Bedrock Converse stub that VERIFIES
     the SigV4 signature, + retry/fallback/cache composition + the behavioral floor stays green.
Out: NON-Converse model bodies (legacy per-model InvokeModel JSON for models the Converse API doesn't
     support — Converse covers Claude/Titan/Llama/Mistral/Cohere chat; anything outside is a later task,
     not this milestone); Azure OpenAI (sibling milestone v21); Bedrock image/multimodal-IN, Guardrails,
     Knowledge Bases, Agents, model-import, batch/async InvokeModel; AWS auth methods beyond static keys +
     STS session token (no IMDS/EC2 instance-profile, no SSO/web-identity, no AssumeRole chaining in v20 —
     env/config creds only, documented); changing the v4 routing strategies / circuit-breaker; any
     billing-formula change (v12 accuracy is an invariant to PRESERVE); any dashboard surface for Bedrock
     config (env/config-driven; a later UI milestone if wanted).

## Shared decisions & glossary deltas   (living — every task must honor these)
- OPT-IN / DEFAULT-OFF (foundation rule): with no Bedrock credentials/region configured, the provider is
  unregistered and the gateway is byte-identical to today. `GATEWAY_BEDROCK_*` knobs all default off/empty.
- SECRET DISCIPLINE is HARD (this milestone introduces a new secret class — the AWS secret access key +
  session token): the secret key NEVER appears in logs, metric labels, span attrs, exception messages,
  cache keys, or URLs. The signer takes the secret only to derive the HMAC signing key; it is never
  serialized. The `Authorization` signature header is a derived MAC, not the secret — but is still treated
  as sensitive (never logged). Re-uses the gateway's existing key-redaction discipline.
- CONVERSE IS THE ONE SHAPE: every Bedrock chat/stream/tool task maps to/from the Converse request +
  response (and ConverseStream events) — NOT per-model InvokeModel bodies. One translation surface.
- DESIGN-FOR-FAILURE (CLAUDE.md): every Bedrock IO path declares a timeout, bounded retry (reuse the v19
  shared retry seam — Bedrock throttling = `429`/`ThrottlingException` is retryable; SigV4/4xx auth is
  terminal), and a fail-safe default. The signer is pure/total (no IO) and deterministic given inputs.
- TENANT ISOLATION + BILLING ACCURACY preserved verbatim (v8/v12/v19): a Bedrock response bills the served
  attempt once; cache hits bill $0; the SigV4 signing cost is internal (no extra upstream bill).
- PROVIDER-TEMPLATE REUSE: follow the repeatable 4-step provider pattern established v9→v11 (catalog/route
  → request map → response map → tests), adding the SigV4 auth seam as step 0.

## Shared / risky contracts (freeze these first)
- The SIGV4 SIGNER contract — signer signature (inputs: method, host, path, query, headers, body, service,
  region, credentials, timestamp) → headers to add; the canonicalization + signing-key derivation; the
  credential-resolution shape. Pure, total, deterministic, secret-safe. -> owning task bedrock-sigv4-auth
- The CONVERSE ⇄ OPENAI MAPPING contract — the both-ways field map for request (messages/system/
  inferenceConfig/toolConfig) and response (output.message/stopReason/usage), incl. the modelId routing
  key. Every chat/stream/tool task consumes it. -> owning task bedrock-chat
- The CONVERSESTREAM EVENT contract — the AWS event-stream → OpenAI SSE chunk mapping + the precise
  pre-first-event commit boundary (reuses v19's). -> owning task bedrock-streaming

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] bedrock-sigv4-auth   depends-on: none                 — pure SigV4 v4 signer (bedrock-runtime) + credential resolution; secret-safe, total, tested against AWS canonical vectors
- [x] bedrock-chat         depends-on: bedrock-sigv4-auth   — non-streaming Converse chat ⇄ OpenAI surface; new provider route in the v9 dispatch; Bedrock usage billing + pricing
- [x] bedrock-streaming    depends-on: bedrock-chat         — ConverseStream event-stream → OpenAI SSE; reuse v19 pre-first-byte resilience boundary
- [x] bedrock-tools        depends-on: bedrock-chat         — Converse toolConfig ⇄ v10 canonical tool seam (toolUse/toolResult ↔ tool_calls)  [non-streaming round-trip; streaming tool deltas = carried "parallel-tool streaming" open]
- [x] bedrock-embeddings   depends-on: bedrock-sigv4-auth   — Titan embeddings via InvokeModel ⇄ OpenAI /v1/embeddings; exact token accounting
- [x] bedrock-verify       depends-on: bedrock-sigv4-auth, bedrock-chat, bedrock-streaming, bedrock-tools, bedrock-embeddings — live double-pass ×2 vs a SigV4-verifying Converse stub + retry/fallback/cache composition + zero-regression floor

## Exit criteria (observable; map each to the task that delivers it)
- [x] A request to a `bedrock/<modelId>` model is authenticated with a valid AWS SigV4 signature (verified against AWS's published canonical-request test vectors), and credentials never appear in any log/metric/URL; with no creds configured the provider is absent and behavior is byte-identical (← bedrock-sigv4-auth) (verify: signer vector tests + secret-absence test + default-off byte-identical test) ✓ DONE 2026-06-15 — 13/13 tests green incl. SV0 (AWS get-vanilla byte-exact 5fa00fa3…) + SV5 (secret-absence) + SV7 (default-off → None) + SV8 (%3A path-encoding bug FOUND by refute-read & fixed); pyright 0, ruff clean, no-DB floor green; pure stdlib signer (no boto3), secret excluded from repr + boot guard
- [x] A chat completion to a Bedrock Claude model returns an OpenAI-compatible response with accurate token usage billed once on the served attempt, at per-model Bedrock pricing (← bedrock-chat) (verify: Converse map both-ways tests + single-bill test + pricing test) ✓ DONE 2026-06-15 — 19/19 bedrock_provider tests green; Converse mapping both-ways (system-lift, inferenceConfig, content concat, usage→prompt/completion/total w/ totalTokens fallback, finish_reason map); usage{prompt,completion,total} ints on served body (v12 billing contract); routable via ProviderAwareCompletionUpstream(adapters=_chat_adapters); opt-in (None on partial creds → byte-identical). ADVERSARIAL CATCH: build double-encoded the model_id (wrong-model routing + broken SigV4) — found via botocore SigV4 oracle, fixed to raw model_id, test strengthened to assert wire-routing + single-encode. pyright 0, ruff clean, no-DB floor exit 0
- [x] A streaming chat completion to a Bedrock model streams OpenAI-compatible SSE chunks, with a pre-first-event failure transparently retried/failed-over and a post-first-chunk failure keeping committed-stream behavior (← bedrock-streaming) (verify: ConverseStream→SSE tests + pre/post-first-event boundary tests) ✓ DONE 2026-06-15 — 9/9 bedrock_streaming green; hand-rolled pure-stdlib AWS EventStream decoder (bedrock_eventstream.py, both CRCs via binascii.crc32, round-trip-verified against botocore EventStreamBuffer oracle); _converse_stream_to_openai_sse translates messageStart/contentBlockDelta/messageStop/metadata → OpenAI chat.completion.chunk with usage on the terminal frame (v12 billing via extract_usage_from_sse); stream() mirrors anthropic (breaker.guard pre-first-byte, status+buffer before first yield → 503 raises with zero chunks → v19 failover; never retried); raw model_id %3A wire-routing (BS8). Task-2 stub-guard test retired (declared in §5). pyright 0, ruff clean, no-DB floor exit 0
- [x] A tool-use request to a Bedrock model round-trips OpenAI tools↔Bedrock toolUse/toolResult correctly (← bedrock-tools) (verify: tool request+response translation tests reusing the v10 canonical shapes) ✓ DONE 2026-06-15 — 9/9 bedrock_tool_use green; request: tools→toolConfig.tools(toolSpec/inputSchema.json), tool_choice→toolChoice union (auto/any/tool; none omitted), assistant tool_calls→toolUse blocks (input dict via load_tool_arguments), consecutive role:"tool"→ONE user msg of toolResult blocks; response: toolUse→tool_calls (id=toolUseId, arguments JSON via dump_tool_arguments), content None when only tool_calls, finish_reason tool_calls. Shapes oracle-confirmed vs botocore service model; reuses v10 domain/tool_translation (Bedrock=Anthropic-shaped). Plain-text path byte-identical (BT8 + task-2 suite). NOTE: streaming tool-call deltas deferred to the carried "parallel-tool streaming" open. pyright 0, ruff clean, no-DB floor exit 0
- [x] An embeddings request to a Titan model returns an OpenAI-compatible embedding with exact token accounting (← bedrock-embeddings) (verify: Titan embed map + exact-token tests) ✓ DONE 2026-06-15 — 7/7 bedrock_embeddings green (BE1-BE7); BedrockEmbeddingsProvider implements UpstreamProvider (post_json loops Titan InvokeModel — one signed POST /model/{model_id}/invoke per inputText since Titan has no batch; sums per-call inputTextTokenCount → usage{prompt_tokens,total_tokens} EXACT, no estimation; returns OpenAI list shape via _titan_to_openai_embeddings); SigV4 service="bedrock" (oracle-confirmed signingName), raw model_id %3A wire-routing, content=body_bytes so signed hash matches wire; fail-fast on first 4xx (BE4 call_count==1, no partial list) via _bedrock_error_to_openai; ConnectError/Timeout→UpstreamUnavailableError + per-instance CircuitBreaker; registered _providers["bedrock"] iff resolve_aws_credentials (opt-in, byte-identical when absent — BE7). post_multipart/stream_bytes raise (embeddings don't stream/multipart). regression bedrock_provider+embeddings_endpoint+gemini_embed_tokens 39/39; pyright 0, ruff clean, no-DB floor exit 0
- [x] Live double-pass green ×2 against a SigV4-verifying Bedrock stub through the TLS edge, retry/fallback/cache compose with Bedrock, and zero behavioral regression on the committed floor (← bedrock-verify) (verify: the §6 evidence block + live double-pass log) ✓ DONE 2026-06-15 — TWO parts both green: (1) docker-free earned-green suite 9/9 (BV1-BV9) — an INDEPENDENT SigV4 stub (re-impl from AWS spec, NOT importing the gateway signer) pinned to the AWS get-vanilla vector (BV1) that REJECTS tampered/missing sigs (BV3→403) ACCEPTS the real adapter's chat (incl. ':'→%3A path)/stream/tools/exact-embed-tokens/retry/fallback (BV2/4/5/6/7/8) over real TCP; (2) LIVE double-pass ×2 through the Envoy edge — run_id 1781524638 + 1781524659, BOTH 10/10: C1 chat+billing, C2 SSE, C3 tools, C4 Titan exact tokens, C5 retry-to-success (stub saw 2 signed attempts), C6 error-aware fallback (fb-fail ctx-400→fb-ok 200), C7 X-Cache:hit. The 6 bedrock suites = 66 passed; `make test-fast` floor exit 0. No production source change (adapters frozen tasks 1-5)
