# MILESTONE: LiteLLM parity slice 8 — tool-use / function-calling across providers

goal: a tenant sends OpenAI tools + tool_choice to a chat model on any provider (OpenRouter, Anthropic, Gemini) and gets OpenAI-shaped tool_calls back — non-stream and streaming — with native translation, billing, and v8 routing intact
rationale: sub-milestone of the production LiteLLM-parity roadmap. Intake → `sub-milestone` (a slice of the live parity theme, too big for one task). Tool-use / function-calling is the single largest remaining LiteLLM-parity gap after v9 provider breadth and the explicit v9 deferral ("chat text + usage first; tools a follow-up"). It is depth, not breadth: it extends each v9 ChatTranslator (OpenRouter/Anthropic/Gemini) with the tools⇄native mapping rather than adding new adapters — proving the v9 seam generalizes to a richer request/response shape. Agentic workloads (the dominant modern LLM use) are impossible without it.
stage: production · status: active · created: 2026-06-13

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  The CHAT completion path carries TOOLS end-to-end on EVERY provider. A request
     may include OpenAI `tools` (function schemas) + `tool_choice`; each provider's v9
     ChatTranslator gains the tool mapping — request: OpenAI tools/tool_choice/prior
     `role:"tool"` messages → provider-native (Anthropic tools+tool_choice+tool_result
     content blocks; Gemini functionDeclarations+toolConfig+functionResponse parts);
     response: provider-native tool calls → OpenAI `tool_calls` (id + function.name +
     function.arguments-as-JSON-string) with finish_reason "tool_calls"; AND the
     streaming case (provider tool-call events → OpenAI `delta.tool_calls` fragments +
     the terminal usage chunk). A full multi-turn round-trip works: tools → tool_calls
     → caller runs the tool → follow-up turn with the tool result → final answer.
     OpenRouter/OpenAI stay BYTE-IDENTICAL passthrough (they already speak OpenAI tools);
     a request WITHOUT tools behaves exactly as v9. Billing keys on the served model id
     with the provider's NATIVE usage; v8 routing/governance compose unchanged.
Out: Parallel-tool-call STREAMING edge cases beyond fragment-per-index (correctness
     first; aggressive partial-arg interleaving a follow-up); provider-native
     "fine-grained"/computer-use/code-execution built-in tools (function-calling only);
     JSON-mode / structured-outputs `response_format` (its own later slice — distinct
     from tools); tool-use on the non-chat modalities (embeddings/images/audio have no
     tools); AWS Bedrock + Azure (separate provider slices); the v7/v8/v9 open
     follow-ups (incremental-SSE TTFB, exact Gemini-embed tokens, non-chat
     soft-budget-alert, empty-key boot guard — tracked open, not in this milestone).

## Shared decisions & glossary deltas   (living — every task must honor these)
- GLOSSARY: **Tool / function-call** — the canonical (OpenAI) shape the /v1 surface
  speaks: a request MAY carry `tools` (a list of `{type:"function", function:{name,
  description, parameters}}`) + `tool_choice` ("auto"|"none"|"required"|{type:"function",
  function:{name}}); a response MAY carry `message.tool_calls` (`{id, type:"function",
  function:{name, arguments:<JSON string>}}`) with finish_reason "tool_calls"; a
  follow-up turn carries `role:"tool"` messages keyed by `tool_call_id`. Every provider
  translates this canonical shape ⇄ its native form.
- GLOSSARY: **Tool-call id synthesis** — a provider with no native tool-call id (Gemini's
  `functionCall` is id-less) gets a gateway-SYNTHESIZED deterministic id outbound; the
  reverse translation matches the follow-up tool result back to native (Anthropic by
  `tool_use_id`; Gemini by function NAME, its only correlation key). The id is
  gateway-owned, deterministic, and never encodes a secret.
- `function.arguments` is a JSON STRING on the OpenAI wire but a JSON OBJECT natively
  (Anthropic `input`, Gemini `args`) → translate with json dumps/loads AT the boundary;
  malformed/garbage args never crash a translator (fail-safe: forward the raw string).
- Additive / byte-identical is non-negotiable (v9 invariant extended to tools): a request
  WITHOUT `tools` engages ZERO tool plumbing and is byte-identical to v9; OpenRouter +
  OpenAI tool requests stay byte-identical passthrough. Billing still keys on the SERVED
  model id with native usage — tool-call tokens are counted by the provider, no separate
  tool billing.

## Shared / risky contracts (freeze these first)
- The tool-translation contract — the canonical OpenAI⇄native mapping for tools,
  tool_choice, tool_calls, and `role:"tool"` messages (request + response + streaming
  `delta.tool_calls` shape), INCLUDING the id-synthesis + reverse-correlation strategy
  (the riskiest point: Gemini's id-less functionCall round-trip — synthesize outbound,
  match by name inbound) and the request-side plumbing proving `tools`/`tool_choice`
  flow through the chat use-case unstripped → owning task `tool-use-contract`
  (FREEZE FIRST — both provider tasks build against it; OpenRouter passthrough +
  no-tools byte-identical must be proven here).

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] tool-use-contract     depends-on: none                 — freeze the canonical OpenAI⇄native tool mapping (tools/tool_choice/tool_calls/role:"tool" + streaming delta shape + id-synthesis/reverse-correlation); prove tools/tool_choice flow through the chat use-case unstripped; OpenRouter/OpenAI passthrough byte-identical; no-tools request byte-identical to v9. FREEZE FIRST.
- [x] anthropic-tool-use     depends-on: tool-use-contract    — extend AnthropicCompletionUpstream: OpenAI tools→Anthropic tools(input_schema), tool_choice→{auto|any|tool}, tool_calls⇄tool_use blocks, role:"tool"⇄tool_result blocks (request+response), tool_use stop_reason→tool_calls, streaming input_json_delta→OpenAI delta.tool_calls.
- [x] gemini-tool-use        depends-on: tool-use-contract    — extend GeminiCompletionUpstream: OpenAI tools→functionDeclarations, tool_choice→toolConfig.functionCallingConfig, functionCall⇄tool_calls (synthesized id), role:"tool"⇄functionResponse parts (matched by name), finishReason→tool_calls on functionCall presence, streaming functionCall part→OpenAI delta.tool_calls.
- [x] tool-use-live-verify   depends-on: anthropic-tool-use, gemini-tool-use — e2e double-pass: a full tool round-trip (tools → tool_calls → tool-result follow-up → final answer) through Anthropic + Gemini stubs, streaming tool-call deltas verified per provider, billing on the served id with native usage, governance intact, OpenRouter + no-tools byte-identical.

## Exit criteria (observable; map each to the task that delivers it)
- [x] A chat request with `tools` + `tool_choice:"auto"` to provider=anthropic returns an OpenAI-shaped `tool_calls` response (id + function.name + arguments-as-JSON-string, finish_reason "tool_calls"), and a follow-up turn carrying the `role:"tool"` result yields a final assistant answer (← anthropic-tool-use) — LIVE C1 7/4 ×2
- [x] The same full round-trip works for provider=google — Gemini `functionCall`⇄`tool_calls` with a synthesized id, `functionResponse` follow-up matched by name, finish_reason "tool_calls" (← gemini-tool-use) — LIVE C3 9/6 synth-id ×2
- [x] Streaming tool calls: a streamed response emits OpenAI `delta.tool_calls` fragments per provider plus the terminal usage chunk; billing keys on the served model id with the provider's native tool-call usage (← tool-use-contract, exercised by both providers) — LIVE C2+C4 ×2
- [x] OpenRouter/OpenAI tool requests stay byte-identical passthrough, and a request WITHOUT tools behaves exactly as v9 (← tool-use-contract) — LIVE C5 5/3 byte-identical ×2
- [x] All of the above proven LIVE through the TLS edge with per-provider tool-stubs, two consecutive clean passes (← tool-use-live-verify) — 18/18 ×2 (run_id=1781291241, 1781291262)
