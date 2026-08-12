# MILESTONE: LiteLLM parity slice 7 — provider breadth (Anthropic + Google Gemini, chat + embeddings)

goal: a tenant calls Anthropic (chat) and Google Gemini (chat + embeddings) models through the same OpenAI-compatible /v1 surface, with native-API translation, billing, governance, and v8 routing intact
rationale: sub-milestone of the production parity roadmap (Tin confirmed "Provider breadth", then "Anthropic + Google Gemini, chat + embeddings", 2026-06-12). LiteLLM's other flagship is multi-provider reach behind one OpenAI-compatible surface. v7 added a provider-selection seam for NON-chat modalities (embeddings/images/audio via ProviderRegistry); v8 added the multi-deployment router. This slice makes the CHAT path provider-aware (today it is hardwired to OpenRouter) and adds two native upstreams that speak NON-OpenAI wire formats — the first real schema-translation surface.
stage: production · status: active · created: 2026-06-12

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  The CHAT completion path becomes PROVIDER-AWARE — it dispatches by the SERVED
     model's catalog `provider` to a per-provider chat adapter that TRANSLATES an
     OpenAI chat-completions request → the provider-native request and the native
     response → an OpenAI-shaped response (non-stream AND streaming SSE), with usage
     and error mapping. Two native upstreams: **Anthropic** (Messages API — chat only;
     Anthropic has no first-party embeddings) and **Google Gemini** (generateContent
     for chat + embedContent for embeddings). They register under new ProviderRegistry
     keys ("anthropic", "google") and new catalog `provider` values; v8 routing
     (strategy/limits/fallback/cooldown) + per-deployment billing on the SERVED model
     id all compose unchanged. The OpenRouter + OpenAI paths stay BYTE-IDENTICAL
     (OpenRouter remains the default provider).
Out: Anthropic embeddings (no native API — defer to a Voyage/3rd-party slice);
     Gemini images/audio; AWS Bedrock + Azure (their own later slices — SigV4 / Azure
     deployment URLs are distinct surfaces); tool-use/function-calling translation
     (chat text + usage first; tools a follow-up); provider breadth in the dashboard UI
     (config-driven only); the v7/v8 open follow-ups (non-chat soft-budget-alert seam,
     empty-upstream-key boot guard — tracked open, not in this milestone).

## Shared decisions & glossary deltas   (living — every task must honor these)
- GLOSSARY: **Provider** graduates from non-chat-only catalog metadata to a
  first-class routing dimension on EVERY modality including chat. Provider ∈
  {openrouter (default), openai, anthropic, google}; the catalog model row's
  `provider` selects the upstream adapter.
- GLOSSARY: **Chat translator** — the per-provider seam that maps an OpenAI
  chat-completions request ⇄ a provider-native request/response (+ SSE stream).
  Distinct from the v7 `UpstreamProvider` (post_json/post_multipart/stream_bytes,
  used by non-chat modalities) — chat needs request/response SHAPE translation, not
  just transport.
- Additive / byte-identical is non-negotiable: provider=openrouter chat is the
  default and stays byte-identical to v8; a model with no provider override behaves
  exactly as today. Billing still keys on the SERVED model id (v6 invariant).
- No new datastore. Per-provider auth (x-api-key/anthropic-version for Anthropic;
  API-key for Gemini) comes from new Settings knobs (non-secret placeholders in e2e),
  never logged/echoed (foundation security rule).

## Shared / risky contracts (freeze these first)
- The provider-aware chat-dispatch + translator seam (how the completion path selects
  a provider adapter by served-model provider and translates OpenAI⇄native, stream +
  non-stream, usage + errors) -> owning task `provider-chat-dispatch` (FREEZE FIRST —
  both provider tasks build against it; the v8 router + billing must stay unchanged)

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] provider-chat-dispatch   depends-on: none                     — make the chat completion path provider-aware: dispatch by served-model catalog `provider` to a ChatTranslator adapter (OpenAI⇄native request/response/SSE/usage/error); register new provider keys; OpenRouter stays default + byte-identical; define the embeddings-translation seam too. FREEZE FIRST.
- [ ] anthropic-provider       depends-on: provider-chat-dispatch   — Anthropic Messages API chat adapter (system+messages+content-blocks translation, x-api-key + anthropic-version headers, SSE event→OpenAI-chunk translation, input/output-token usage mapping). Chat only.
- [ ] gemini-provider          depends-on: provider-chat-dispatch   — Google Gemini adapter: generateContent (chat translation, role/parts mapping, usageMetadata) + embedContent (/v1/embeddings translation); API-key auth.
- [ ] provider-breadth-live-verify  depends-on: anthropic-provider, gemini-provider — e2e double-pass: route a chat through Anthropic + Gemini stubs and an embedding through Gemini; billing rows on the served model with correct usage; governance (401/402) intact; OpenRouter + OpenAI paths byte-identical; streaming verified per chat provider.

## Exit criteria (observable; map each to the task that delivers it)
- [x] A catalog model with provider=anthropic routes /v1/chat/completions through the Anthropic Messages translation and returns an OpenAI-shaped response (stream + non-stream) (← anthropic-provider) — live C1/C2 (7/4 billed)
- [x] A catalog model with provider=google routes chat (generateContent) AND /v1/embeddings (embedContent) with correct OpenAI⇄native translation (← gemini-provider) — live C3/C4 (9/6) + C5 (order-preserved embeddings)
- [x] Billing keys on the served model id with correct usage tokens for every provider; governance (401 auth / 402 budget) unchanged across providers (← provider-chat-dispatch) — live C1/C3 served-id billing + C6 (401+402, 0 rows)
- [x] The OpenRouter + OpenAI paths stay byte-identical (default provider unchanged); a model with no provider override behaves exactly as v8 (← provider-chat-dispatch) — live C7 (5/3/8) + 628 unit suite green, openrouter path untouched
- [x] All of the above proven LIVE through the TLS edge with per-provider stubs, two consecutive clean passes (← provider-breadth-live-verify) — live double-pass 35/35 ×2 through https://localhost:8443
