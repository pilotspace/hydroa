# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/); this project uses semantic versioning.

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
