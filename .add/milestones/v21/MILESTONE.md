# MILESTONE: Enterprise provider: Azure OpenAI

goal: A tenant can call Azure OpenAI on the OpenAI-compatible surface (chat/stream/tools/response_format/embeddings), routed by deployment with api-version, authenticated by api-key and/or Azure AD, billed exactly; opt-in and byte-identical when disabled.
rationale: new-major (project-lead/auto, 2026-06-15). Azure OpenAI is the #2 enterprise-cloud provider and the named sibling to v20 Bedrock — the next gap on the LiteLLM-parity arc. DEPTH on the v9 provider-dispatch + v10 tools + v11 response_format + v12 exact-billing + v19 reliability seams; the only genuinely NEW sub-systems are (1) deployment-based URL routing + the required `api-version`, and (2) Azure AD (client-credentials) token acquisition/caching. Unlike Bedrock there is NO SigV4/Converse/EventStream — Azure is OpenAI-shaped, so chat/stream/tools/response_format are largely passthrough; the rigor shifts to auth + routing + content-filter mapping + exact billing.

stage: production · status: active · created: 2026-06-15

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  An opt-in Azure OpenAI provider reachable on the existing OpenAI-compatible
     surface (no new client-facing API). Deployment-based URL routing with the
     required `api-version` query param. Two auth modes: `api-key` header
     (primary) and Azure AD client-credentials bearer token (enterprise, with
     in-memory token cache + refresh-before-expiry). Chat (non-stream + SSE),
     tools/function-calling, response_format/JSON-mode, embeddings — exact-usage
     billing on every path (v12). Azure content-filter errors mapped to the
     OpenAI error shape and composing with the v19 fallback router. Circuit
     breaker + opt-in retries reused from the existing seam. Default-off and
     byte-identical when Azure is not configured.
Out: Azure AI Studio / non-OpenAI Azure models (Phi, Llama-on-Azure via the
     serverless `models.ai.azure.com` surface) — Azure OpenAI only this milestone.
     Managed-identity (IMDS/workload-identity) token source — AAD this milestone
     is client-credentials (tenant+client+secret) only; IMDS is a carried delta.
     Azure-specific image/audio/batch endpoints. On-Your-Data / extensions.
     Per-deployment rate-limit headers surfacing (carried). Provisioned-throughput
     (PTU) routing nuances. Azure OpenAI Assistants API.

## Shared decisions & glossary deltas   (living — every task must honor these)
- "deployment" (Azure) is the routing unit: the client's `model` field maps to an
  Azure *deployment name* via a configured map (default identity: model == deployment).
  The wire URL is `{endpoint}/openai/deployments/{deployment}/{op}?api-version={ver}`.
- Auth precedence: if Azure AD is fully configured (tenant+client+secret), use the
  AAD bearer token (`Authorization: Bearer <aad>`); else use the `api-key: <key>`
  header. Exactly one auth header is sent. AAD token + client secret + api-key are
  a SECRET class — `field(repr=False)`, never logged/echoed/in metric labels/URLs.
- Opt-in & byte-identical: Azure registers `_chat_adapters["azure"]` / `_providers["azure"]`
  ONLY when `resolve_azure_config()` returns non-None. Absent config → zero behavior
  change anywhere (regression-guarded).
- Exact billing (v12): usage ints come from the upstream body (non-stream) or the
  terminal SSE frame (`extract_usage_from_sse`); never estimated.
- Reuse, don't fork: `execute_with_retry` + `CircuitBreaker` + `classify_fallback_trigger`
  are reused verbatim; Azure adds config + URL building + auth + error mapping only.

## Shared / risky contracts (freeze these first)
- `AzureEndpoint` URL builder (deployment + api-version → path) -> owning task `azure-auth-routing`
- `resolve_azure_config()` + `GATEWAY_AZURE_*` Settings + boot-guard -> owning task `azure-auth-routing`
- Azure AD token acquisition + cache/refresh contract -> owning task `azure-aad-auth`
- Azure content-filter → OpenAI error mapping (composes w/ v19 fallback) -> owning task `azure-chat`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] azure-auth-routing       depends-on: none              — GATEWAY_AZURE_* Settings, resolve_azure_config()→config|None, AzureEndpoint URL builder (deployment→/openai/deployments/{d}/{op}?api-version=), api-key header injection, empty-key boot-guard; opt-in & byte-identical. Foundation seam (no chat yet). [gate PASS 1ca2893]
- [x] azure-chat               depends-on: azure-auth-routing — AzureCompletionUpstream (mirrors OpenRouterCompletionUpstream; OpenAI-shaped passthrough) impl CompletionUpstream.complete; deployment routing; exact usage (v12); content-filter 400→OpenAI shape + composes w/ classify_fallback_trigger; wired _chat_adapters["azure"] iff config; retry+breaker reused. stream() stub. [gate PASS cb61e3e]
- [x] azure-streaming-passthrough depends-on: azure-chat     — SSE stream() (breaker pre-first-byte → v19 failover; billing via extract_usage_from_sse on terminal frame) + tools passthrough + response_format passthrough; OpenAI-shaped so NO translation — DEPTH = prove passthrough byte-identity + billing on each path (regression guards). [gate PASS a727a6c]
- [x] azure-aad-auth           depends-on: azure-auth-routing — Azure AD client-credentials token acquisition (POST login.microsoftonline.com/{tenant}/oauth2/v2.0/token, scope .../.default), in-memory cache w/ refresh-before-expiry, fail-closed on token failure (timeout/retry/circuit); Authorization: Bearer <aad> replaces api-key when AAD configured. The one genuinely-new auth sub-system. [gate PASS 0aaa2a5 — 3 security findings remediated pre-gate]
- [x] azure-embeddings         depends-on: azure-auth-routing — AzureEmbeddingsProvider (mirrors OpenAIDirectProvider) impl UpstreamProvider; deployment routing for /embeddings; exact usage; api-key/AAD reuse; registered _providers["azure"] iff config. [gate PASS 7c62b42 — security finding (api-key in exception chain) remediated pre-gate]
- [x] azure-verify             depends-on: all                — two-layer: docker-free earned-green pytest suite driving REAL adapters over real TCP to an INDEPENDENT Azure stub (verifies api-key header / AAD bearer / deployment-URL+api-version; pinned to a known token vector, rejects tampered/missing) + live double-pass ×2 through the Envoy edge. [gate PASS 6c27553 — 9/9 earned-green + live double-pass ×2 9/9]

## Exit criteria (observable; map each to the task that delivers it)
- [x] A tenant calls Azure chat (non-stream) routed by deployment with api-version, api-key auth, usage billed exactly; byte-identical when Azure unconfigured.   (← azure-chat / azure-auth-routing · verifier: azure_chat suite + azure_verify AV2 + live C1 [usage 11/5/16])
- [x] SSE streaming + tools + response_format work over Azure (passthrough, billed on the terminal frame).   (← azure-streaming-passthrough · verifier: azure_streaming suite + azure_verify AV4 + live C2 [chat.completion.chunk + [DONE]])
- [x] Embeddings via an Azure deployment return OpenAI-shaped vectors with exact usage.   (← azure-embeddings · verifier: azure_embeddings suite + azure_verify AV5 + live C3 [exact summed tokens])
- [x] Azure AD (client-credentials) auth works as an alternative to api-key; token is cached + refreshed before expiry; secrets never logged.   (← azure-aad-auth · verifier: azure_aad suite [12/12 incl. 2 security regressions] + azure_verify AV3 [minted-token end-to-end])
- [x] Azure content-filter errors map to the OpenAI error shape and compose with the v19 fallback router.   (← azure-chat · verifier: azure_verify AV7 + live C5 [az-cf 400 → az-ok 200])
- [x] Live double-pass ×2 GREEN through the Envoy edge (chat/SSE/tools/response_format/embeddings/content-filter→fallback/cache-hit); no residue.   (← azure-verify · verifier: scripts/live_v21_verify.py ×2 = 9/9 ×2 [run_id 1781531130 + 1781531148])
