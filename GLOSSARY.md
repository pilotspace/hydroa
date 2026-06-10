# GLOSSARY.md

Canonical names. Contracts, schemas, and code use these terms exactly.

| Term | Definition |
|------|------------|
| **Tenant** | A customer organization. Owns users, API keys, budgets, and a usage ledger. Every tenant-owned row carries `tenant_id`. |
| **User** | A human belonging to one tenant; logs into the dashboard via JWT. Roles: `owner`, `admin`, `member`. |
| **API key** | Secret credential (`sk-...`) issued per tenant for proxy access. Stored as argon2 hash; shown in full exactly once at creation. |
| **Proxy request** | An OpenAI-compatible call to `/v1/*` forwarded to OpenRouter, streaming or non-streaming. |
| **Upstream** | OpenRouter (`https://openrouter.ai/api/v1`). |
| **Model** | An LLM identifier from the OpenRouter catalog (e.g. `anthropic/claude-fable-5`). |
| **Model catalog** | Cached, priced list of models synced from OpenRouter. |
| **Usage record** | Append-only ledger row per proxy request: tenant, key, model, prompt/completion tokens, cost (USD), latency, status. |
| **Cost** | USD amount per request, computed from the pricing snapshot effective at request time (and reconciled with OpenRouter generation cost when available). |
| **Pricing snapshot** | Immutable copy of a model's per-token prices captured when the catalog syncs. |
| **Budget** | Optional spend ceiling per tenant or key; exceeding it rejects proxy requests with `ERR_BUDGET_EXCEEDED`. |
| **Gateway** | The FastAPI service (`apps/gateway`): proxy data plane + admin control plane. |
| **Edge** | Envoy front proxy: TLS, JWT validation, ext_authz, rate limiting. |
| **ext_authz** | Envoy external authorization call to the gateway's `/internal/authz` to validate API keys. |
| **Dashboard** | Next.js admin UI (`apps/dashboard`). |
| **Write-behind** | Usage records buffered in Redis and flushed asynchronously to Postgres, keeping metering off the streaming hot path. |
