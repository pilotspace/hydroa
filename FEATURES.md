# Features — Hydroa

A whole-product catalog of what Hydroa can do today, organized by domain rather than by
release. For *when* something shipped, see [`CHANGELOG.md`](CHANGELOG.md); for the release
ledger, see [`RELEASES.md`](RELEASES.md). Formerly "ai-proxy" — a metered, multi-tenant AI
proxy: a tenant sets up → logs in → calls any supported LLM through one OpenAI-compatible
gateway → sees accurate, billable cost tracking.

Current shipped line: **0.7.0** (2026-07-02). One more closed milestone is merged to `main`
and held back from release pending live verification (see
[Not yet in a release](#not-yet-in-a-release) below) — its code is live on `main` today; only
its release-notes credit is pending.

---

## Core proxy — one OpenAI-compatible endpoint, seven providers

- **Providers**: OpenRouter (default/core), OpenAI, Anthropic, Google Gemini, AWS Bedrock
  (SigV4, Converse API), Azure OpenAI (deployment-URL routing, Azure AD client-credentials),
  and MiniMax (OpenAI-wire-compatible). Provider is catalog metadata, never client-specified.
- **BYOK** — tenant-managed provider credentials, Fernet-encrypted at rest, resolved per
  request; no shared platform key for completions.
- **Endpoints**: chat completions (streaming + non-streaming), embeddings, images, audio
  (speech-to-text, text-to-speech, translations), turn-based realtime voice (WebSocket), and
  full-duplex realtime voice relay (bidirectional WS pump, provider-agnostic).
- **Batch API** — an OpenAI-compatible `/v1/batches` surface (durable job store, line-item
  validation, background processing), plus an admin batch-diversion policy that can divert
  eligible traffic into batch windows.
- **Model capabilities translated across every provider**: tool-use / function-calling
  (canonical OpenAI vocabulary, provider-native shapes normalized both ways, id-less providers
  get synthesized ids), JSON-mode / structured outputs, vision & multimodal content-parts
  (image/audio inline data), native web-search grounding with citation passthrough, reasoning /
  "thinking" token passthrough (Anthropic extended thinking, Gemini thinking budget, OpenRouter
  effort scale), and prompt-cache passthrough (Anthropic cache breakpoints).
- **Provider-adapter docs-faithfulness** — stop reasons map to the correct OpenAI
  `finish_reason` per provider (Gemini content-policy reasons, Bedrock context-window-exceeded,
  Anthropic refusal); a mid-stream provider error surfaces as a terminal SSE error frame instead
  of silently dropping.

## Model management & routing intelligence

- **Capability-aware model management** — every catalog model declares its accepted
  `input_modalities` (text/image/audio); a request whose input type the resolved model can't
  handle is rejected with a structured 4xx (`unsupported_input_modality`) *before* any upstream
  call and *before* billing — covering chat multimodal content-parts and the audio STT
  endpoint. Fail-closed guard, default-off rollout knob.
- **Per-tenant model presets** — a tenant admin defines named presets that remap a model name
  to a concrete target (`opus → gpt5-5`), selectable via a `preset:alias` colon-prefix
  (`cheap:opus`). Resolution happens at ingress, before the router, across all five request
  entry points (chat, images, embeddings, STT, TTS) plus realtime-WS; a bare/unknown name stays
  byte-identical to today. Reuses the capability guard so a remap to an incompatible model is
  caught with the same structured error, extended to a coarse per-endpoint modality-mismatch
  guard (images/embeddings/TTS/chat/realtime-WS-chat).
- **Routing & load-balancing** — model groups (deployments with weight / TPM / RPM limits),
  four routing strategies (ordered, weighted-shuffle, least-busy, latency-EWMA; the strategy is
  a single operator-wide setting, not per-alias), health-aware fallback across candidates.
- **Reliability primitives** — uniform retries, error-aware fallback, a per-model Redis
  cooldown gate (a cooling model is skipped in favor of the next candidate), an in-process
  per-provider circuit breaker whose open state triggers fallover to the next candidate
  instead of aborting the alias request, and a concurrency/load guard for agent-coding
  workloads.
- **Response caching** — exact-match and normalized near-duplicate ("semantic") caching as
  per-tenant/per-key opt-ins (default-off, TTL-capped, per-request `Cache-Control: max-age`
  override, tenant-facing cache admin API), plus an embedding-similarity vector cache
  (cosine-threshold, Redis) behind a global operator flag — no per-tenant vector toggle yet. A
  cache hit bills the served catalog candidate, never the requested alias.
- **Per-key bandwidth pacing** — a Redis token-bucket paces concurrent same-key throughput
  (bounded-wait → 429/503 + `Retry-After` or a terminal SSE frame); default-off, fail-open.

## Billing & cost accuracy

- **Exact metering** — every proxied request produces exactly one usage record; token counts
  and true per-tier cost are computed from the real provider usage payload, never estimated
  where a real count exists.
- **Catalog cost transparency** — both the client-facing (`GET /v1/models`) and admin
  (`GET /admin/catalog/models`) catalog endpoints expose per-1M-token cost fields
  (input/output/cached-input), normalized to the OpenAI-compatible display convention.
- **Cache-discount / dual-stream pricing** — real provider cache-hit discounts are persisted
  and actually applied to billed cost (not just displayed): MiniMax's cache-hit rate and
  OpenAI GPT-Realtime's independently-priced text + audio streams (with their own cached-input
  discount) both produce an accurate blended `cost_usd`, via an additive dual-stream schema
  (`pricing_snapshots` / `usage_records` gained audio-stream columns; every pre-existing
  single-stream model stays byte-identical).
- **Cache-hit billing correctness** — a response served from cache bills the *served* catalog
  candidate that produced it, never the requested alias/group name, across all three cache hit
  paths (exact, semantic, vector) and both the live-route and cache-hit code paths.
- **Disconnect-billing integrity** — a dropped streaming connection deterministically aborts
  the upstream generation, captures the real provider cost, and reconciles it via a
  signed-delta correction with idempotency + a periodic sweep backstop, so a disconnected
  stream's upstream charge is never silently unbilled.
- **Reconciliation & drift detection** — provider-cost-vs-billed reconciliation with a
  configurable drift-threshold alert, plus an operator-wide cross-tenant reconciliation view
  (`GET /ops/reconciliation`) gated behind mTLS + XFCC edge-trust (default-off, fail-closed).
- **Per-model / per-tier rate cards** — an admin-managed markup override per (tenant, model)
  on top of the tenant's flat markup percentage, resolved by ONE shared resolver so catalog
  display, recorder billing, and cost recovery can never drift from each other.
- **Durable usage recording** — a usage event survives a Redis blip via a bounded-timeout
  Postgres fallback, and a crash mid-flush via Redis-stream PEL reclaim; both paths converge
  on one idempotent insert (`ON CONFLICT DO NOTHING` on a deterministic id), so no event is
  double-billed or silently lost.
- **Budgets & rate limits** — per-tenant/team/key spend budgets, RPM/TPM rate limits, model
  allowlists.

## Identity, auth & multi-tenancy

- **Tenants & teams** — every row is `tenant_id`-scoped; cross-tenant references return `404`,
  never a leak. Teams attribute spend within a tenant.
- **Three credential classes**: password login (argon2id) → session JWT; per-tenant SSO — OIDC
  **and SAML 2.0** (SP-side assertion validation: signature, audience, replay, and the same
  tenant-confusion defenses; both issue the identical session JWT) with a per-tenant SSO domain
  field; and an RFC 8628 OAuth 2.0 Device Authorization Grant for headless coding agents
  (SHA-256-hashed tokens, fail-closed, server-enforced expiry, default-on 30-day refresh,
  default $100/mo per-token budget cap). All are accepted at both the in-process `/v1` handlers
  and the Envoy edge `ext_authz` gate with byte-identical 401s (anti-enumeration).
- **SCIM 2.0 provisioning** — a per-tenant SCIM bearer token drives `/scim/v2/Users`
  (create / update / deactivate) straight into the existing user lifecycle; a deactivated user's
  sessions and keys stop authenticating, and another tenant's token can never touch a user
  (cross-tenant writes rejected).
- **Domain capture** — a tenant admin proves ownership of an email domain via a DNS-TXT
  challenge (`_ai-proxy-challenge.<domain>`); a new signup on that verified domain is then routed
  into the tenant. Composes with the invite-only signup default (an explicit opt-in that relaxes
  it for that domain only), and a Postgres partial-unique index (`WHERE status='verified'`) is the
  structural cross-tenant collision guard — no app-level check-then-write race.
- **API keys** — SHA-256-hashed at rest (plaintext shown once at creation), per-key budgets and
  rate limits.
- **RBAC** — six-tier role matrix (owner → member) on a frozen permission allowlist, with an
  admin UI for role assignment and an escalation guard.
- **Invite-based onboarding** — email invites for tenant/team membership with a dedicated
  acceptance flow.
- **Platform (superadmin) console** — a cross-tenant operator surface: tenant directory and
  per-tenant config management, cross-tenant user/key administration, platform-wide audit
  read, a plan/tier catalog with per-tenant assignment (catalog-only today — no quota
  enforcement wired yet), and time-boxed, fully audited support-access impersonation
  sessions.

## Enterprise governance & security

- **Guardrails** — a tenant-configurable guardrail engine enforced pre- and post-call (including
  on cache hits): regex prompt-injection detection (7 pattern families, block or audit mode) and
  PII masking (8 built-in patterns — email / phone / credit-card / SSN / IP / IBAN / API-secret /
  passport — plus up to 8 tenant-supplied custom patterns, ReDoS-hardened behind a strict
  time/size budget), managed via `GET/PUT /admin/guardrails` and a dashboard Guardrails settings
  tab. Default-off until a tenant configures a policy.
- **ML moderation layer** — a provider-backed moderation check class alongside the regex checks
  (default-off, block or audit mode). It carries its own outbound-IO failure domain — an isolated
  circuit breaker and a third `unchecked` verdict state — so a moderation-provider outage degrades
  honestly per the configured failure mode and never silently passes as "checked".
- **Per-key guardrail policies** — a policy attached to an individual API key overrides the tenant
  policy wholesale (resolution order: key > tenant > default-off).
- **Output schema validation** — opt-in JSON-schema validation of a response with exactly one
  bounded retry on mismatch, then a structured error (a recorded, opt-in supersession of the
  earlier translate-don't-enforce pin; with it off the response path is byte-identical).
- **Guardrail analytics** — per-policy / per-pattern / per-key verdict hit counts over a time
  window (`GET /admin/guardrails/analytics` + a dashboard analytics view).
- **Request/response logs & Logs Explorer** — opt-in, per-tenant/per-key payload capture that is
  PII-scrubbed *through the guardrail engine before persist* (raw payloads never touch disk; a
  scrub failure drops to a metadata-only row), size-capped, retention-governed, and fail-open for
  the proxy path (a capture-store outage never fails or slows a proxied call). A tenant-admin
  query API (`GET /admin/logs` list + detail, filter by time/model/key/status/cost, keyset
  pagination, tenant-404-invisible) backs a console **Logs Explorer** page — filter table + detail
  drawer (request / response / metadata / guardrail verdicts) + **replay a logged call into the
  chat playground**. Never a source of billing truth (`usage_records` stays the only ledger).
- **Audit log & compliance export** — append-only, trigger-immutable audit events with an admin
  read surface (`GET /admin/audit`) and a read-only, cursor-paginated compliance export API
  (time/actor-filtered, deterministic ordering for external archival; the export access is itself
  audited).
- **Data retention & Zero-Data-Retention** — an active-by-default operator-wide retention sweeper
  (audit retained to a configurable floor), now with a **per-tenant** retention window over the
  same sweeper and a fail-closed **ZDR mode**: a ZDR tenant produces metadata-only usage records
  and zero payload rows across every inventoried store, while billing stays exact (token counts
  are metadata). Enabling ZDR is a destructive, confirm-gated, irreversible purge.
- **SLO & observability** — SLO metrics + dashboard (`GET /admin/slo`, `/app/slo`), live
  rate-limit/upstream-health views, alerts & events (including webhook delivery), OpenTelemetry
  tracing.
- **Edge security** — an Envoy edge terminates `ext_authz` for every `/v1/*` call, JWT
  validation for `/admin/*`, hard-blocks `/internal/*`, and is the sole authenticator of
  WebSocket handshakes (a browser can't set an `Authorization` header on a WS upgrade).
- **Public marketing site** — landing, pricing, docs, blog, legal, and a public (non-authed,
  cache-friendly) status page distinct from the gated admin health view.
- **Honest degradation, everywhere** — a design principle, not a single feature: video
  generation without a configured provider fails explicitly rather than faking a result; an
  object-store outage returns a typed `ERR_OBJECT_STORE_UNAVAILABLE` (+ circuit breaker) instead
  of a silent inline fallback that masks the outage; a realtime relay with no provider configured
  closes with an honest `4404`. Nothing in Hydroa fakes success.

## AI application platform (dashboard "playgrounds")

Console-grade surfaces (OpenAI-Playground / Anthropic-Console quality) built as pure
pass-through BFF layers over the gateway — the browser never sees a server-side `sk-` token:

- **Chat** — SSE streaming workspace with model/parameter controls, per-turn metadata (model,
  finish reason, tokens, latency, cost), tool/function-call turns, and conversation management
  (fork, export).
- **Voice** — live hold-to-record capture → STT → chat → TTS turn loop with autoplay and a
  running session cost total.
- **Memory** — semantic-memory library (search/sort, detail inspector, add/delete).
- **Artifacts** — typed file manager (XSS-safe inline preview, drag-drop upload, guarded
  delete), backed by a provider-agnostic `ObjectStore` port (S3/MinIO, default-off; inline
  bytea remains the zero-config path).
- **Vision** — multimodal inspector: media preview + multi-turn markdown Q&A over images/video.
- **Video** — generation studio (prompt/params composer, job gallery, inline preview), backed
  by a durable Redis-backed job queue with startup orphan recovery and a retry cap.
- **Aurora design system** — one visual language (tokens, elevation, motion, WCAG 2.2 AA,
  reduced-motion-aware) applied across admin, landing, and auth, on a ~18-page Next.js
  dashboard covering every backend capability (usage/cost, keys, budgets, routing config,
  presets, model catalog with capability badges, monitoring, governance).

## Platform & deployment

- **Kubernetes-native** — one env-parameterized Helm chart deploys the full stack (dashboard,
  gateway, Envoy edge, Postgres, Redis, MinIO) with migrate-before-boot init containers,
  fail-closed secret/TLS guards, default-on NetworkPolicies, and PSS-restricted pods. Validated
  on both a reproducible local `kind` harness and real GKE Autopilot/Cilium; an automated e2e
  suite drives the goal flow + dashboard UI + realtime relay + artifacts through the live edge.
- **Local dev** — `make edge` (Docker Compose) or `make kind-up` (local Kubernetes).

## Not yet in a release

`gpt-realtime-pricing` is **merged to `main`** and gated PASS, but held back from 0.7.0 by
deliberate choice (this is the `add.py release` housekeeping this document accompanies):

| Milestone | What it adds |
|---|---|
| `gpt-realtime-pricing` | GPT-Realtime dual-stream cache-discount billing — **known gap**: the
  billing math is unit/adversarially-tested but was never live-verified against real OpenAI
  Realtime infrastructure (no live credential was available in any task's environment). The code
  is merged and running (not feature-flagged), but Tin chose to hold its release-notes credit
  back until that live verification happens rather than crediting a not-fully-verified billing
  path to a named release. |

## In progress — not yet shipped

The **enterprise-hardening** milestone shipped in full (7/7 tasks, incl. realtime-relay
governance, edge input hardening, and signup/routing authorization — all described in their
domain sections above).

Two clusters are **complete, verified, and gated on the integration branch**, pending one merge
to `main`, and are documented above as shipped:

- **logs-explorer-guardrails-v2** — request/response payload capture + Logs Explorer, per-key
  guardrail policies, the ML moderation layer, output schema validation, and guardrail analytics.
- **enterprise-identity-compliance** — SCIM 2.0, SAML SSO, domain capture, per-tenant
  retention + ZDR mode, and the compliance export API.

All 14 tasks are gated PASS (five carried HARD-STOP security verifies this project's method
never auto-passes); they will land in the next numbered release cut.

---

*Full chronological detail (what shipped in each numbered release, with evidence) lives in
[`CHANGELOG.md`](CHANGELOG.md). The append-only release ledger lives in
[`RELEASES.md`](RELEASES.md).*
