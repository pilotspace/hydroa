---
type: Spec
title: Domain
lens: domain
project: ai-proxy (Hydroa) — the multi-tenant LLM gateway
generated: { by: add/3.2.0, at: 2026-08-12 }
---
## Now

A multi-tenant LLM gateway: one OpenAI-compatible wire in front of many providers
(OpenRouter · OpenAI · Anthropic · Google · Azure · Bedrock), with metering and billing as
first-class domain concepts rather than instrumentation.

Core concepts: **Tenant** · **User** · **API key** · **Proxy request** · **Model catalog** ·
**Pricing snapshot** · **Usage record** · **Budget** · **Markup** · **Model group** (an
ordered list of Deployments, not bare id strings) · **ZDR** (zero data retention) ·
**Eval set / case / run / baseline / verdict** (R7, in direction).

Bounded contexts, as modules: `proxy/` (the data plane) · `tenants/` (signup, users, JWT,
retention policy) · `auth/` (API keys, ext_authz) · `usage/` (metering, ledger, budgets,
retention sweep) · `catalog/` (models, pricing) · `vector_stores/`, `files/`, `batches/`,
`video/`, `finetune*/`, `responses_store/` (payload-bearing surfaces) · `core/` (config, db,
errors). Full vocabulary: `.add-2x-archive/GLOSSARY.md`.

## Decisions that bind

- **Every tenant-owned row carries `tenant_id`, and every query is tenant-scoped.** A
  cross-tenant id must be indistinguishable from an absent one — uniform 404, never an
  enumeration oracle. (carried invariant; a live OIDC enumeration oracle was closed in #84)
- **The usage ledger is append-only**, and the raw upstream payload is retained so cost is
  always recomputable. (carried invariant)
- **Every proxied request produces exactly one usage record** — no unmetered execution path,
  and no double-bill. Eval runs are billed traffic, not a side channel. (carried invariant)
- **API key secrets exist only as SHA-256 hashes at rest**; plaintext is shown once at
  creation. Argon2 is for user passwords only — it broke hot-path authz latency for keys.
  (amended at the api-keys freeze; the freeze flag caught the spec/GLOSSARY conflict)
- **ZDR means no payload at rest, and the check is atomic with the write.** Eight payload
  stores refuse a ZDR tenant outright (403 `ERR_ZDR_PAYLOAD_BLOCKED`) as the first line of
  the repository create. Check-at-entry plus persist-after-await is a tenant-reachable
  bypass — HARD-STOPPED three times; `raise_if_zdr_locked` (SELECT … FOR UPDATE) is the
  shared primitive wherever an await sits between the check and the commit.
- **A model's availability is tri-state** (`ACTIVE` / `UNKNOWN` / `TENANT_DISABLED`) and
  catalog visibility is scoped `tenant_id IS NULL OR = :tenant`. No surface grants
  visibility a normal request lacks — including finetuned models.
- **Modality is TEXT + a `Literal` alias, never a DB ENUM**; the value set is bounded but
  grows, and `ALTER TYPE` per addition is a worse trade than a compile-time exhaustive alias.
- **Billing keys on the SERVED model id** returned by the router, never `body["model"]` —
  the request string drifts from the catalog id (`:free` variants).
- **A verdict reports; it never acts.** Nothing promotes, routes, or rolls back on a score.

## Deltas
<!-- the inbox: `- [open · <date>] <lesson>` — fold upward into the sections above, then retag [folded] -->
