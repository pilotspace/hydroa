# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); this project uses semantic versioning.

## [0.7.0] — 2026-07-02

Seventh release. Rounds out model management (per-model input-capability guardrails, per-tenant
model presets), adds a 7th LLM provider (MiniMax), and makes catalog cost display and cache-hit
billing math trustworthy across every provider. Bundles 6 closed milestones since 0.6.0 (v55 · v56 ·
catalog-pricing-detail · minimax-provider · openrouter-embeddings · gateway-health) plus 1 loose
task (stream-alias-billing). No breaking changes — every change is additive or a disclosed
correctness fix; existing API-key, agent-OAuth, OIDC/SSO, session-JWT, billing, and routing behavior
is unchanged.

### Added

- **Capability-aware model management** (v55) — every catalog model now declares the input types
  (text/image/audio) it accepts; a request whose content-parts exceed what the resolved model
  supports is rejected with a structured `400 unsupported_input_modality` — before the upstream
  call and before billing — across chat multimodal content and the audio STT endpoint. Default-off
  rollout knob; exposed read-only on the admin catalog API and the `/app/models` dashboard.
- **Per-tenant model presets** (v56) — a tenant admin defines named presets that remap a model
  name to a concrete target (`opus → gpt5-5`), selectable via a `preset:alias` colon-prefix
  (`cheap:opus`) across all five request entry points (chat, images, embeddings, STT, TTS) plus
  realtime-WS. A bare/unknown name resolves byte-identical to today. Reuses v55's capability guard
  (extended to a coarse per-endpoint modality-mismatch check) so a remap to an incompatible model
  is caught with the same structured error.
- **MiniMax provider** — a 7th LLM provider joins the registry via its OpenAI-compatible
  `/v1/chat/completions` surface, with BYOK credential storage and 3 catalog models
  (MiniMax-M3/M2.7/M2.7-highspeed). Live-verified against `api.minimax.io/v1` with a real billed
  call and a matching independently-recomputed cost.
- **Catalog cost transparency** — both `GET /v1/models` and `GET /admin/catalog/models` now expose
  `prompt_usd_per_1m` / `completion_usd_per_1m` / `cached_input_usd_per_1m`, normalized to the
  familiar per-1M-token display convention (additive; existing fields byte-identical).

### Fixed

- **OpenRouter embeddings routing** — `POST /v1/embeddings` for an OpenRouter-hosted embedding
  model (e.g. `google/gemini-embedding-2`) was silently forwarded to `/chat/completions` instead
  of a real embeddings call; catalog sync also defaulted every OpenRouter row to `modality=chat`
  regardless of its real type. Both fixed; live-verified end-to-end (real embedding vector, billed
  usage row, chat path unaffected).
- **MiniMax cache-hit billing** — MiniMax's real $0.06/1M cache-hit discount was captured at sync
  time but never actually applied to a billed `cost_usd` (silent no-op). Now genuinely lowers the
  bill on a cache hit (~26% reduction on the live-verified example call).
- **Cache-hit alias billing** (`stream-alias-billing`, loose task) — a streaming request routed
  through a model-group alias now bills the served catalog candidate, never the alias name itself,
  closing a $0-billing gap on the streaming live-route path.
- **Gateway static-quality gates** (`gateway-health`, maintenance-only, no behavior change) —
  restored `make ci` (ruff lint/format + pyright + full pytest) to fully green after CI-billing
  drift let it slip red; 3 stale tests fixed by correcting their environment-coupling, not by
  loosening assertions.

### Known deltas (backlog)

- ~220 pre-existing, cross-milestone SPEC deltas ride into this release as documented backlog (the
  same accumulating-backlog pattern every release since 0.1.0 has carried; e.g. 0.6.0 shipped with
  35). None are security blockers — this release's own floor check reports 0 open HARD-STOPs.

### Security

- 0 open security HARD-STOPs · 0 RISK-ACCEPTED waivers (release-report clean).
- **gpt-realtime-pricing held back from this release** (code is merged to `main`, not reverted or
  flagged — see RELEASES.md) — its dual-stream billing math is unit- and adversarially-tested but
  was never live-verified against real OpenAI Realtime infrastructure; it will be credited to a
  release once that live verification happens.
- The **enterprise-hardening** milestone (branch `feat/enterprise-hardening`) is explicitly **not**
  part of this release — 4 of 7 tasks are done but unmerged, and the remaining 3 include 2
  security-governance tasks not yet started. It ships in a future release once complete.

## [0.6.0] — 2026-06-30

Sixth release. Turns the dashboard's thin AI-feature surfaces into **Console-grade playgrounds** an
operator can run real work on, and makes the gateway's provider adapters **faithful to the providers'
published API docs**. Bundles 8 closed milestones since 0.5.0 (chat · voice · memory · artifacts ·
vision · video playgrounds + v54 UI refinement + proxy-correctness). No breaking changes — every
change is additive; the playgrounds are pure pass-through over existing gateway endpoints (no backend
change), and the adapter fixes only add mappings/passthrough. Existing API-key, agent-OAuth,
OIDC/SSO, session-JWT, billing, and routing behavior is unchanged.

### Added

- **Console-grade AI-feature playgrounds** — the chat, voice, memory, artifacts, vision, and video
  surfaces rebuilt to OpenAI-Playground / Anthropic-Console quality, reusing one shared Console
  design language (four UI states, WCAG 2.2 AA, XSS-safe rendering). All are **pure pass-through**
  over existing gateway endpoints via the Next.js BFF, which mints the server-side `sk-` token
  (never exposed to the browser), caps bodies at 32 MiB, and streams SSE/audio/video unbuffered:
  - **Voice** — live hold-to-record microphone capture (getUserMedia/MediaRecorder) → STT → chat →
    TTS turn loop, TTS-reply autoplay, and a per-turn transcript with models · tokens · latency ·
    cost plus a running session total.
  - **Memory** — a two-pane semantic-memory library with search/sort, a detail inspector, and
    add/delete (relevance scores are shown only when real — never fabricated).
  - **Artifacts** — a file-manager with typed list + search/sort, XSS-safe inline image/text/JSON
    preview (blob object URLs, escaped text), drag-drop + picker upload, download, guarded delete.
  - **Vision** — a multimodal inspector: media preview + a multi-turn markdown Q&A thread over
    images/video with a model picker and per-response metadata.
  - **Video** — a generation studio: prompt + params composer, a job gallery with rich status,
    inline blob-only video preview on success + download, and an honest no-provider-configured
    degrade.
- **Polished, responsive app pages** (v54) — UI refinement across the admin/app surfaces for
  scalable, responsive layouts.

### Fixed

- **Provider-adapter docs-faithfulness** (proxy-correctness) — provider stop reasons now map to the
  correct OpenAI `finish_reason`: Gemini content-policy finish reasons (`BLOCKLIST`,
  `PROHIBITED_CONTENT`, `SPII`, `IMAGE_SAFETY`) → `content_filter`; Bedrock
  `model_context_window_exceeded` → `length`; Anthropic `refusal` → `content_filter`. An Anthropic
  **mid-stream `error` event** is now surfaced as a terminal SSE error frame instead of being
  silently dropped. OpenAI speech-to-text now forwards `timestamp_granularities` and
  `chunking_strategy`. (An audit's "critical" Bedrock SigV4 finding was investigated and **refuted**
  — the signing name is correctly `bedrock`, pinned against AWS's published test vectors — so the
  signer was left unchanged.)

### Known deltas (backlog)

35 documented SPEC deltas ride into this release as backlog (e.g. realtime-WS voice modes, OpenRouter
attribution headers / usage-accounting staleness, Anthropic thinking-token passthrough, OpenAI
`stream_options.include_usage`, a real video-generation provider adapter). All are additive
follow-ups; none are security blockers (the release floor reported 0 blockers / 0 waivers).

## [0.5.0] — 2026-06-27

Fifth release. Makes three previously-deferred capabilities **real** and puts the whole stack on
**Kubernetes**: artifact bytes now persist to a real object store, realtime voice gains a
provider-agnostic full-duplex relay, and the entire platform deploys from one env-parameterized
Helm chart proven by an automated end-to-end suite. Bundles 3 closed milestones (v51–v53) since
0.4.0. No breaking changes — every capability is additive and gated; existing API-key, agent-OAuth,
OIDC/SSO, session-JWT, billing, and routing behavior is unchanged.

### Added

- **Artifacts on real object storage** (v51) — artifact bytes persist to an S3-compatible object
  store (MinIO) behind a provider-agnostic `ObjectStore` port, with **honest degradation**
  (explicit `ERR_OBJECT_STORE_UNAVAILABLE` + circuit breaker, never a silent inline fallback that
  masks an outage). Default-off; inline-BYTEA remains the zero-config path.
- **Full-duplex realtime voice relay** (v52) — a provider-agnostic relay seam
  (`/v1/realtime/relay`) bridges a client WebSocket to a provider realtime session bidirectionally
  (dict frames = control, binary = audio) through a breaker + timeout pump, with in-band
  auth-over-WS (token never in the URL or a header; honest `4404` close when no provider is
  configured). Extends v47's turn-based realtime.
- **Kubernetes deployment + full e2e validation** (v53) — one env-parameterized **Helm chart**
  (`charts/ai-proxy/`) stands up the entire stack (Next.js dashboard · gateway · Envoy edge ·
  Postgres · Redis · MinIO) with external-ready datastores, migrate-before-boot init containers,
  fail-closed secret/TLS guards, default-on NetworkPolicies, and PSS-restricted pods. A
  reproducible **kind** harness (`make kind-up`) plus an automated **e2e suite** drive the goal
  flow + dashboard UI + realtime relay + artifacts + admin surfaces through the live Envoy edge; a
  kind-in-CI workflow and a `make ci-e2e` local mirror run the whole pipeline on demand.

### Changed

- **Realtime edge auth** (v53) — the Envoy edge gains a `/v1/realtime/` ext_authz carve-out so the
  gateway is the sole authenticator of WebSocket handshakes (a browser cannot set an `Authorization`
  header on a WS upgrade); every other `/v1/` path keeps edge ext_authz unchanged.
- **Container images** (v53) — the gateway image now carries `migrations/` + `alembic.ini` so an
  init container runs `alembic upgrade head` before the gateway serves an unmigrated DB; the
  dashboard ships as an `output:'standalone'` Next.js server image. The dashboard BFF resolves the
  gateway via a server-side in-cluster URL only (the `NEXT_PUBLIC_GATEWAY_URL` fallback was removed
  so the in-cluster address can never be inlined into the client bundle).

### Notes

- **No breaking changes.** All three milestones are additive and default-off / zero-config-safe.
- **Deployment is kind-validated; the real-cloud apply is a documented HARD-STOP runbook**
  (`docs/runbooks/cloud-deploy.md`) — human-run, never executed by CI. Two pre-apply gates must
  pass first: validate the production NetworkPolicies under a real enforcing CNI (the kind overlay
  disables them because kindnet enforces NP in a way that blocks the edge path), and confirm the
  provider-key encryption key is set (boot-time fail-fast is a tracked follow-up).
- **CI**: the new `kind-e2e` workflow is committed but cannot run on a hosted runner until the org's
  GitHub Actions billing is restored; the contemporaneous proof is the locally-green `make ci-e2e`
  (kind stack Ready → API e2e + UI e2e through the live edge).
- 0 open security HARD-STOPs · 0 RISK-ACCEPTED waivers. Foundation consolidated to version 39.

## [0.4.0] — 2026-06-26

Fourth release. Builds a full **AI Application Platform** on top of the hardened proxy core —
chat, web search, voice, conversations, memory, files, vision, realtime voice, and video
generation — then elevates the web UI to a single visual language and hardens the landing &
admin surfaces for production. Bundles 12 closed milestones (v40–v50 + ui-fidelity) since 0.3.0.
No breaking changes — existing API-key, agent-OAuth, OIDC/SSO, session-JWT, billing, and routing
behavior are unchanged; every new capability is additive and gated.

### Added

**AI Application Platform** (v40–v49)
- **Chat workspace + streaming** (v40) — server-sent-event chat through the BFF with a frozen
  `useChatStream` client; model and cost controls in the dashboard.
- **Web search augmentation** (v41) — native provider grounding with citation passthrough;
  default-off, dashboard toggle.
- **Voice breadth** (v42) — multi-provider STT/TTS (Azure audio), `/v1/audio/translations`,
  TTS input cap (413), and a `/app/voice` playground; BFF binary-body passthrough.
- **Remote conversations** (v43) — tenant-scoped `/v1/conversations` CRUD with chat history UI.
- **Remote memory** (v44) — tenant-scoped semantic memory store (`/v1/memories`, cosine search).
- **Artifacts / files** (v45) — tenant-scoped `/v1/artifacts` store with XSS-attachment guard
  and size cap.
- **Video & image understanding** (v46) — native Gemini multimodal: OpenAI content-part arrays
  translate to `inlineData`, riding `/v1/chat/completions` (byte-identical back-compat).
- **Realtime voice** (v47) — turn-based WebSocket `/v1/realtime` (auth on the first frame, token
  never in the URL, bounded in-flight buffer as a DoS guard).
- **Video generation** (v48) — async job lifecycle (`/v1/video/generations`) storing the result
  as an artifact; **honest degradation** — with no provider configured a job fails explicitly
  rather than returning a fake video.
- **Durable video-job processing** (v49) — Redis-backed queue with startup orphan recovery and a
  retry cap; default-off, fail-open to inline on Redis-down.

**Web UI elevation & production hardening** (ui-fidelity, v50)
- **Aurora design language** (ui-fidelity) — one elevated visual language (indigo/slate/Inter,
  elevation, display type, brand gradient, motion) applied across admin, landing, and auth via a
  token graph and shared primitives.
- **Production hardening** (v50) — resilient BFF fetch (timeout → typed 504, client-disconnect
  abort preserving disconnect-billing, body-size cap), static CSP + security headers, fail-closed
  input validation, scoped failure-state segments (digest-only, no leak), reduced-motion-aware
  entrance motion, and an accessibility regression net.

### Notes

- **Honest residue (no fake success):** real video-generation / realtime provider adapters
  (external API keys), an S3 object store (new infra), and a full-duplex realtime relay remain
  un-shipped by design and degrade explicitly rather than faking a result.

## [0.3.0] — 2026-06-25

Third release. Adds headless agent authentication (OAuth 2.0 Device Authorization Grant,
RFC 8628), hardens billing/metering across disconnects and streaming, completes the Helios
agent-coding path, and ships enterprise governance (RBAC, audit, retention, SLO) plus a public
marketing site. Bundles 7 closed milestones (v33–v39) since 0.2.0. No breaking changes — existing
API-key, OIDC/SSO, session-JWT, billing, and routing behavior are unchanged.

### Added

**Headless agent authentication** (v39) — a new credential class
- OAuth 2.0 Device Authorization Grant (RFC 8628): public `POST /oauth/device/authorize` +
  `POST /oauth/token` (full §3.5 polling), authed `POST /oauth/device/{approve,deny}` (any tenant
  member, bound to the verified JWT — never the request body).
- Agent tokens are SHA-256-hashed at rest, plaintext returned once, fail-closed, expiry
  server-enforced; refresh default-on 30d; default $100/mo per-token budget cap.
- Accepted at both the in-process `/v1` handlers and the edge `/internal/authz` ext_authz gate;
  byte-identical 401 for both credential classes (anti-enumeration). A coding agent can now
  self-authenticate and make billable LLM requests with no browser.

**Helios agent-coding integration readiness** (v34)
- OpenAI-wire → native Helios path: reasoning, prompt-cache, parallel tool streaming,
  disconnect-billing across all providers, and a concurrency load guard.

**Enterprise governance & observability** (v38, v37)
- RBAC roles (allowlist permission matrix, 6 tiers) + RBAC admin UI (`PUT /admin/users/{id}/role`,
  escalation guard).
- Append-only, trigger-immutable audit log + `GET /admin/audit`.
- Active-by-default data-retention sweeper (audit retained to a floor).
- SLO metrics + dashboard (`GET /admin/slo`, `/app/slo`).
- Public marketing site (landing, pricing, legal, docs, blog, status).
- Dashboard observability parity (v37): bandwidth panel, routing-editor weight guard + restart
  affordance, SSO domain-seed with pre-flight messaging.

**Per-key bandwidth pacing** (v36)
- Per-key aggregate Redis token-bucket paces concurrent same-key throughput; bounded-wait →
  503 + Retry-After / terminal SSE error frame. Default-off, fail-open.

### Changed / Hardened

**Billing trustworthiness** (v33)
- Drift-threshold validation, non-finite passthrough sanitization, cost-basis-filtered
  reconciliation, and provider-cost stamping on client disconnect (gated against double-counting).

**Agent-loop error fidelity** (v35)
- Upstream 429 surfaces as client 429 + Retry-After; any mid-stream failure (including graceful
  peer close) emits a terminal SSE error frame + `[DONE]`.

### Operator notes
- v38 data-retention purge is **active-by-default on deploy** (audit retained to a floor).
- v39 device-authorization + token endpoints are **public/pre-auth** (rate-limited and bounded);
  monitor the `/oauth/*` and `/internal/authz` 401-rate split for probing.
- The `operator` role holds `KEYS_MANAGE`.

### Quality
- Full gateway suite 1730 passed @ 88.14% at v39 close; all milestones gated PASS.
- v39's three backend security HARD-STOPs reviewed + approved; v39 live double-pass 13/13 ×2.

## [0.2.0] — 2026-06-23

Second release of the metered multi-tenant AI proxy. Hardens billing trustworthiness under
client disconnects, completes control-plane dashboard coverage (every operator/admin read has a
backend), and makes routing configuration writable from the dashboard. Bundles 3 closed
milestones (v30, v31, v32) since 0.1.0. No breaking changes.

### Added

**Control-plane dashboard coverage** (v31) — every operator/admin surface now has a backend
- Alerts & events viewer — `GET /admin/alerts` + dashboard Alerts page (tenant-scoped; platform
  system rows visible to all tenants).
- Catalog re-sync trigger — `POST /admin/catalog/sync` (owner/admin) for on-demand model-catalog refresh.
- Upstream health view — `GET /admin/health/upstreams` (honest, monitored-providers-only status).
- Rate-limit counter view — `GET /admin/ratelimits` (live per-key counters, fail-open).
- Per-tenant SSO domain field on the login page — drives the OIDC relay `?domain=` so a tenant
  with per-tenant SSO can start login.

**Operator-wide reconciliation** (v31)
- `GET /ops/reconciliation` — cross-tenant reconciliation for operators, behind mTLS + XFCC
  edge-trust ops-auth. Default-OFF and fail-closed (no operator certificate ⇒ all requests 401).

**Writable routing configuration** (v32)
- Persisted operator-wide routing config (singleton store) applied over env at gateway boot
  (DB-wins-when-present, env fallback).
- `PUT /admin/routing` (owner/admin) — edit model-groups, routing strategy, and per-deployment
  weight/rpm/tpm limits with full validator parity; `GET /admin/routing` additively exposes
  `routing_strategy` + `deployments`.
- Dashboard routing editor on `/routing` with a clear "applies on next restart" notice.

### Changed

- Streaming responses for Anthropic, Google Gemini, and AWS Bedrock are now forwarded
  incrementally instead of buffered, improving time-to-first-token (v30).

### Fixed

- Client-disconnect billing integrity (v30): a dropped streaming connection now deterministically
  aborts the upstream generation, captures the real provider cost, and reconciles it via a
  signed-delta correction with idempotency and a periodic sweep backstop — so an upstream charge
  on a disconnected stream is billed accurately and can never silently go unrecovered.

### Security & operations

- The `/ops/*` operator surface is the app-side verify-half of an mTLS edge-trust model. **Release
  requirement:** the reverse proxy (Envoy) must strip any client-supplied `x-forwarded-client-cert`
  header and restrict `/ops/*` before any operator certificate is provisioned. The endpoint ships
  default-OFF and fail-closed; this requirement gates enabling it, not the release.

## [0.1.0] — 2026-06-18

First public beta release of the metered multi-tenant AI proxy. A tenant can be set up, log
in via SSO, call any supported LLM through one OpenAI-compatible gateway, and see accurate,
billable cost tracking — the project's stage goal, now met end-to-end. Bundles 28 closed
milestones (v1–v29).

### Added

**Core platform**
- Multi-tenant gateway with per-tenant metered usage and billable cost tracking (v1, v2).
- SSO / OIDC login with hardened auth sessions — BFF JWT relay, fail-closed verification (v18).
- Tenant identity, teams, and team attribution of spend.

**Providers** — one OpenAI-compatible surface, many upstreams
- OpenAI / OpenRouter (core), Anthropic chat (v9), Google Gemini chat + embeddings (v9).
- AWS Bedrock — SigV4 auth, Converse chat / streaming / tools, Titan embeddings (v20).
- Azure OpenAI — deployment-URL routing, api-version, Azure AD client-credentials auth (v21).
- BYOK: tenant-managed provider credentials, Fernet-encrypted at rest (v25).

**Endpoints & model capabilities**
- Chat completions (streaming + non-streaming), embeddings, audio / speech-to-text, images.
- Tool-use / function-calling translated across all providers (v10).
- JSON-mode / structured outputs across all providers (v11).

**Routing & reliability**
- Router and load-balancing: deployments, routing strategies, per-deployment limits (v8).
- Uniform retries, error-aware fallback, cooldown circuit-breaker, response + semantic
  caching (v19).

**Governance & dashboard**
- Usage/cost views, key & budget governance, model management, spend windows, rate limits.
- Full dashboard feature-coverage — a surface for every backend capability (v13, v15).
- Enterprise UI overhaul with WCAG 2.2 AA accessibility (v23, v24).

**Billing accuracy**
- Exact token counting and true per-tier cost on every call (v12, v27).
- Provider-cost reconciliation: Σ provider cost vs Σ billed, drift detection, and a periodic
  unbilled-upstream drift alert so an upstream charge against a $0-billed call can never go
  unnoticed (v29).

### Changed
- Upgraded to Next.js 16 with dependency / advisory remediation (v14).
- Provider security & config hardening — project-wide secret-chain `from None`, configurable
  Azure AD authority (v22); provider-config cleanup (v26).

### Fixed
- Billing & passthrough robustness — no silent $0 on stream disconnect, no non-finite values
  in the ledger or in passthrough responses, STT duration capping (v28).
- Cleared carried follow-up debt across UI, dependency, and billing tracks (v17).

### Security
- No open security HARD-STOP rides into this release (0 blockers).
- No RISK-ACCEPTED waivers shipped (0 waivers).

### Known follow-ups (tracked, non-blocking)
- 10 open SPEC deltas from v29 reconciliation: non-finite drift-threshold guard at startup,
  `cost_basis='provider'` filter on the unbilled aggregate, and provider_cost stamping on
  client-disconnect / mid-stream `GeneratorExit`.

<details>
<summary>Milestone attribution (28 milestones · carried deltas)</summary>

- MVP: metered multi-tenant AI proxy (v1) — 12 carried
- Production-ready metered proxy (v2) — 7 carried
- V3 — 6 carried
- V4 — 0 carried
- LiteLLM parity slice 3 — intelligence & hardening (v5) — 5 carried
- Routing & resilience (v6) — 9 carried
- LiteLLM parity slice 5 — multi-modal & multi-provider (v7) — 10 carried
- LiteLLM parity slice 6 — router & load-balancing (v8) — 17 carried
- LiteLLM parity slice 7 — provider breadth: Anthropic + Gemini, chat + embeddings (v9) — 16 carried
- LiteLLM parity slice 8 — tool-use / function-calling across providers (v10) — 15 carried
- LiteLLM parity slice 9 — JSON-mode / structured outputs across providers (v11) — 13 carried
- Billing accuracy + ops hardening (v12) — 5 carried
- UI/UX refresh — usage/cost + key/budget governance (v13) — 10 carried
- Dashboard feature-coverage (v15) — 23 carried
- Dependency hardening — Next.js 16 upgrade + advisory remediation (v14) — 5 carried
- Hardening — clear carried follow-up debt (v17) — 9 carried
- Auth session hardening (v18) — 5 carried
- Reliability — retries, error-aware fallback, response & semantic caching (v19) — 6 carried
- Enterprise provider: AWS Bedrock (v20) — 5 carried
- Enterprise provider: Azure OpenAI (v21) — 13 carried
- Provider security & config hardening (v22) — 2 carried
- Enterprise UI Overhaul (v23) — 9 carried
- UI polish & a11y follow-ups (v24) — 4 carried
- Tenant-managed provider credentials / BYOK (v25) — 5 carried
- Provider config cleanup — v25 follow-ups (v26) — 2 carried
- Billing precision — true per-tier cost on every call (v27) — 12 carried
- Billing & passthrough robustness (v28) — 8 carried
- Billing reconciliation — provider cost vs billed, with drift alert (v29) — 9 carried

</details>
