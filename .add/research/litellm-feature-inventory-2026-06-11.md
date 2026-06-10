# LiteLLM Feature Inventory — intake input for the production-grade goal

source: /Volumes/Games/tindang-repo/litellm (local clone), surveyed 2026-06-11 by Explore agent
purpose: feeds the `new-major` intake for "production grade to full main features of LiteLLM"
(goal set by Tin Dang, 2026-06-10). ai-proxy status assessed against the v2 (5/6) state.

## Feature areas (what · where in litellm · complexity · ai-proxy status)

| # | Area | Complexity | ai-proxy status |
|---|------|-----------|-----------------|
| 1 | Multi-provider routing — Router class, deployment groups, strategies (least-busy, latency, usage, cost, provider-budget, shuffle), generic/context-window/content-policy fallbacks, cooldowns (Redis dual cache), per-exception retry policies, adaptive routing (`llmproxy/router.py` 441KB, `router_strategy/`, `router_utils/`) | XL | partial — OpenRouter pass-through only; no groups/strategies/fallbacks/cooldowns |
| 2 | Virtual keys — budgets (max/soft/duration), per-model sub-budgets, TPM/RPM/parallel limits, model allowlist w/ wildcards, teams/orgs scoping, allowed_ips, blocked flag, service-account type, auto-rotation (`key_management_endpoints.py` 275KB) | L | partial — SHA-256 key auth exists; missing budget richness, limits, rotation, scoping |
| 3 | Teams/Orgs/Users hierarchy — Org > Team > User > End-User, roles, per-tier budgets+limits+model lists, SCIM v2, per-team aliases/callbacks (`team_endpoints.py` 189KB etc.) | XL | partial — tenant+JWT roles only |
| 4 | Spend tracking & budgets — cost calculator, SpendUpdateQueue batch writes, pre-call budget hooks, soft alerts, rolling windows, spend query API, daily Slack reports, S3/GCS cold storage (`proxy/spend_tracking/`) | L | partial — write-behind ledger + markup exists; missing windows, soft alerts, query API |
| 5 | Caching — Redis/cluster/in-memory/S3/GCS/disk + semantic (Redis embedding, Qdrant); per-request cache controls; DualCache (`llmproxy/caching/`) | L | missing |
| 6 | Rate limiting — ParallelRequestLimiterV3 (112KB): TPM/RPM/parallel at key/user/team/org/model via Redis Lua sliding windows; dynamic + batch variants | L | partial — Envoy edge 50/s only; no app-level TPM/RPM hierarchy |
| 7 | Guardrails/hooks — CustomLogger lifecycle hooks (pre/post/streaming/moderation), 30+ vendor guardrails (Presidio PII, Lakera, Bedrock, …), prompt-injection detection (`proxy/guardrails/`) | XL | missing |
| 8 | Observability callbacks — Langfuse, Datadog, OTel (112KB), Prometheus (149KB, 40+ metrics), 30+ more; team/key-level callback overrides (`integrations/`) | XL | missing (ai-proxy now has own structlog+Prometheus core from v2 observability) |
| 9 | Alerting — SlackAlerting (72KB): budget/outage/hanging/cooldown/daily-report alert types, per-type webhook routing; email via SMTP/Resend/SendGrid | M | missing |
| 10 | Health checks — live LLM endpoint checks, background polling cached to DB, /health/services, /health/readiness+liveliness | M | missing (probes arrive in v2 ops-hardening) |
| 11 | SSO/JWT admin auth — OIDC (Google/Microsoft/Okta), JWT role mapping + RBAC, custom SSO, SCIM, JWK rotation (`ui_sso.py` 165KB) | L | partial — HS256 JWT only |
| 12 | Admin UI — keys/teams/orgs/users/models/router/guardrails/usage analytics/spend logs/playground etc. (Next.js, 50+ components) | XL | partial — dashboard w/ BFF auth, few screens |
| 13 | Pass-through endpoints — provider passthroughs (Azure/Anthropic/Bedrock/Vertex/…) + user-defined authenticated tunnels + passthrough guardrails | L | missing |
| 14 | Unified API beyond chat — completions, embeddings, images, audio (TTS/STT), moderations, assistants/threads, files, batches, responses API, fine-tuning, rerank, OCR, realtime WS, video, search, RAG | L | partial — chat completions only |
| 15 | Secret managers — AWS SM, GCP SM/KMS, Vault, Azure KV, Conjur; os.environ/ indirection (`secret_managers/`) | M | missing |
| 16 | Dynamic model management — runtime add/remove via API + DB, access groups, model_group_alias, tag-based routing, per-team aliases | M | partial — catalog sync + pricing snapshots only |
| 17 | Priority queue/scheduler — per-model heap, FlowItem priority admission control (`scheduler.py`) | S | missing |
| 18 | Credential store (BYOK) — encrypted per-deployment credentials, per-team scoping (`credential_endpoints/`) | M | missing |
| 19 | MCP server gateway — aggregates upstream MCP servers, tool filtering, A2A protocol (`mcp_management_endpoints.py` 100KB) | XL | missing |
| 20 | Policy engine — PolicyRegistry/Matcher/Resolver, OPA sidecar (`proxy/policy_engine/`) | L | missing |
| 21 | Prompt management — versioned registry, templates, DotPrompt (`proxy/prompts/`) | M | missing |
| 22 | Audit logging — management-plane write audit w/ before/after (enterprise) | M | missing |
| 23 | Provider budget routing — rolling spend window per provider filters deployments (`router_strategy/budget_limiter.py`) | M | missing |
| 24 | Vector store / RAG — ingest/query endpoints, vector store CRUD, file table (`proxy/rag_endpoints/`) | L | missing |
| 25 | Compression/agentic loop control — context pruning + max-iterations hooks | M | missing |

## Suggested parity tiers (Explore agent, confidence 0.93/0.95/0.94)

- **Tier 1 — core production parity**: (2) key budgets/limits/rotation · (3) teams/users hierarchy · (6) app-level TPM/RPM rate limiting · (4) spend windows + soft budgets + query API · (10) health checks · (9) alerting · (16) dynamic model management
- **Tier 2 — differentiating**: (1) multi-strategy router + fallbacks/cooldowns · (5) response caching · (7) guardrails framework core (Presidio PII + prompt-injection first) · (11) SSO/OIDC · (8) Langfuse + OTel callbacks · (13) passthrough endpoints · (15) secret managers (AWS SM + Vault)
- **Tier 3 — long tail**: MCP gateway, policy engine/OPA, prompt mgmt, scheduler, BYOK credential store, SCIM, audit logging, RAG/vector stores, provider-budget routing, batches/responses/realtime, agentic compression

## Orchestrator note
ai-proxy is OpenRouter-fronted (single upstream), so "multi-provider routing" parity means routing
across MODELS/upstream variants + fallbacks/cooldowns at the gateway, or adding direct provider
upstreams (passthroughs) — a v3 intake decision to surface explicitly.
