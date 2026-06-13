# MILESTONE: LiteLLM parity slice 9 — JSON-mode / structured outputs across providers

goal: a tenant sends response_format (json_object or json_schema) to a chat model on any provider (OpenRouter, Anthropic, Gemini) and gets back JSON-conformant output — non-stream and streaming — with native translation, billing, tool-use, and v8 routing intact
rationale: sub-milestone of the production LiteLLM-parity roadmap. Intake → `sub-milestone` (a slice of the live parity theme, too big for one task — contract + 2 provider tasks + live verify, same shape as v9/v10). `response_format` is the single largest remaining chat-completion parity gap after v10 tool-use, and v10 explicitly deferred it as "its own later slice — distinct from tools". It is DEPTH on the v9 ChatTranslator seam (like v10): it extends each provider's request/response/SSE helpers with the response_format mapping. It also COMPOSES with v10 — Anthropic has no native response_format field, so its json_schema path REUSES the v10 tool-coercion seam (forced single tool), making this the natural v10 follow-up. Structured outputs are table-stakes for agentic/data-extraction workloads.

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  The CHAT completion path carries `response_format` end-to-end on EVERY provider. A
     request MAY include OpenAI `response_format` ∈ {`{type:"text"}` (default, no-op),
     `{type:"json_object"}` (free-form JSON), `{type:"json_schema", json_schema:{name,
     schema, strict}}` (schema-constrained)}; each provider's v9 ChatTranslator gains the
     response_format mapping — request: OpenAI response_format → provider-native
     (OpenRouter/OpenAI byte-identical passthrough; Gemini
     `generationConfig.responseMimeType:"application/json"` + `responseSchema` for
     json_schema; Anthropic NO native field → json_schema COERCED to a single forced
     tool (v10 tool seam: a synthetic `json_tool_call` tool + tool_choice forcing it),
     json_object via a documented system-instruction/prefill strategy); response: the
     model's JSON output is returned as normal `message.content` (a JSON STRING) with
     finish_reason "stop" — for the Anthropic tool-coercion path the forced tool_use
     block is UNWRAPPED back into `message.content` (the caller asked for JSON content,
     not a tool_call). Streaming works: JSON content streams as normal
     `delta.content` fragments + the terminal usage chunk per provider; the Anthropic
     coercion path unwraps the streamed tool_use input_json_delta fragments into
     content. Billing keys on the served model id with native usage; v8 routing,
     governance, AND v10 tool-use compose unchanged (a request MAY carry BOTH tools and
     response_format — they must not collide).
Out: Client-side JSON re-validation / repair (the gateway translates the directive; it
     does NOT validate or retry the model's output against the schema — parity with
     LiteLLM's translate-don't-enforce stance); `strict` schema-subset normalization
     beyond pass-through (OpenAI's strict-mode schema restrictions are forwarded as-is,
     not rewritten); JSON-mode on non-chat modalities (embeddings/images/audio have no
     response_format); Pydantic/`response_model` SDK-side helpers (a client concern, not
     a wire feature); AWS Bedrock + Azure (separate provider slices); the carried-open
     v7/v9/v10 follow-ups (incremental-SSE TTFB, exact Gemini-embed tokens, non-chat
     soft-budget-alert, empty-key boot guard, parallel-tool-call streaming, same-name
     Gemini disambiguation — tracked open, not in this milestone).

## Shared decisions & glossary deltas   (living — every task must honor these)
- GLOSSARY: **response_format** — the canonical (OpenAI) directive the /v1 surface speaks:
  `{type:"text"|"json_object"|"json_schema"}`, with `json_schema:{name, schema, strict?}`
  for the constrained case. Every provider translates this ⇄ its native mechanism; the
  MODEL OUTPUT always comes back as OpenAI `message.content` (a JSON string), never a new
  response field.
- GLOSSARY: **JSON-schema tool coercion** — a provider with NO native structured-output
  field (Anthropic) satisfies `json_schema` by REUSING the v10 tool seam: emit one
  synthetic tool whose `input_schema` IS the requested schema + a forced `tool_choice`,
  then UNWRAP the returned tool_use block's `input` back into `message.content` as a JSON
  string. The coercion is gateway-owned and invisible to the caller (no tool_calls leak).
- Additive / byte-identical is non-negotiable (v9+v10 invariant extended): a request
  WITHOUT `response_format` (or `{type:"text"}`) engages ZERO json plumbing and is
  byte-identical to v10; OpenRouter/OpenAI response_format requests stay byte-identical
  passthrough. Billing still keys on the SERVED model id with native usage.
- response_format and tools COMPOSE: a request carrying BOTH a real `tools` list AND
  `response_format` must keep them separate on every provider — on Anthropic the JSON
  coercion tool is ADDED alongside the caller's tools (distinct synthetic name), never
  replacing them; the freeze must pin this collision rule.

## Shared / risky contracts (freeze these first)
- The response-format-translation contract — the canonical OpenAI⇄native mapping for
  `response_format` (text/json_object/json_schema) across request + response + streaming,
  INCLUDING the riskiest point: the Anthropic JSON-schema tool-coercion + UNWRAP strategy
  (synthetic forced tool → tool_use block → message.content JSON string, on both the
  non-stream and stream paths) and the tools+response_format COMPOSITION rule (no
  collision). Proves response_format flows through the chat use-case unstripped (raw-dict
  passthrough, like v10 tools); OpenRouter/OpenAI passthrough + no-response_format
  byte-identical must be proven here → owning task `response-format-contract` (FREEZE
  FIRST — both provider tasks build against it).

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] response-format-contract   depends-on: none                       — freeze the canonical OpenAI⇄native response_format mapping (text/json_object/json_schema, request+response+streaming), the Anthropic tool-coercion+unwrap strategy, and the tools+response_format composition rule; prove response_format flows unstripped through the chat use-case; OpenRouter/OpenAI passthrough byte-identical; no-response_format byte-identical to v10. FREEZE FIRST.
- [x] gemini-json-mode           depends-on: response-format-contract    — extend GeminiCompletionUpstream: response_format → generationConfig.responseMimeType "application/json" (json_object) + responseSchema (json_schema); response JSON returned as message.content; streaming content fragments + terminal usage; no-response_format byte-identical.
- [x] anthropic-json-mode        depends-on: response-format-contract    — extend AnthropicCompletionUpstream: json_schema → synthetic forced json_output (v10 tool seam) → UNWRAP tool_use.input into message.content JSON string; json_object via system-instruction strategy; streaming input_json_delta unwrapped into delta.content; composes with caller-supplied tools (no collision); no-response_format byte-identical.
- [x] json-mode-live-verify      depends-on: gemini-json-mode, anthropic-json-mode — e2e double-pass: json_object + json_schema round-trips through Anthropic + Gemini stubs (JSON content back, finish_reason stop), streaming JSON content per provider, billing on the served id, tools+response_format composition intact, governance intact, OpenRouter + no-response_format byte-identical.

## Exit criteria (observable; map each to the task that delivers it)
- [x] A chat request with `response_format:{type:"json_schema",...}` to provider=google returns JSON-conformant `message.content` (finish_reason "stop") via responseMimeType+responseSchema, non-stream and streaming (← gemini-json-mode; LIVE C3/C4)
- [x] The same works for provider=anthropic — json_schema is satisfied by the synthetic forced-tool coercion and UNWRAPPED back into `message.content` as a JSON string (no tool_calls leak), non-stream and streaming (← anthropic-json-mode; LIVE C1/C2, no tool_calls leak confirmed)
- [x] `response_format:{type:"json_object"}` (free-form JSON) works on both Gemini and Anthropic; `{type:"text"}` / absent is a no-op (← gemini-json-mode, anthropic-json-mode; LIVE C3 json_object, unit-suite no-op)
- [x] A request carrying BOTH `tools` and `response_format` keeps them separate on every provider (the JSON coercion tool is added alongside caller tools, never replacing) (← response-format-contract, exercised by anthropic-json-mode unit suite)
- [x] OpenRouter/OpenAI response_format requests stay byte-identical passthrough, and a request WITHOUT response_format behaves exactly as v10; billing keys on the served model id with native usage (← response-format-contract; LIVE C5 ok-openrouter 5/3 byte-identical)
- [x] All of the above proven LIVE through the TLS edge with per-provider stubs, two consecutive clean passes (← json-mode-live-verify; 13/13 ×2, both exit 0)
