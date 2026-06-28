# Hydroa

Multi-tenant AI proxy platform over [OpenRouter](https://openrouter.ai) with
per-tenant cost tracking, key governance, rate limiting, spend analytics,
alerting, and an admin dashboard. Built with the
[ADD methodology](https://github.com/pilotspace/ADD).
(Formerly "ai-proxy"; internal branding and compose project names updated to Hydroa.)

**Goal:** tenant setup → login → use any LLM model → cost tracked.

> **📖 Usage runbooks → [`docs/runbooks/`](docs/runbooks/README.md)** — verified-live
> guides for running, administering, and calling Hydroa:
> [Getting started](docs/runbooks/01-getting-started.md) ·
> [Admin guide](docs/runbooks/02-admin-guide.md) ·
> [API client guide](docs/runbooks/03-api-client-guide.md) ·
> [Multi-tenant guide](docs/runbooks/04-multi-tenant-guide.md).

## Highlights

**One OpenAI-compatible endpoint, every major provider.** Point an OpenAI SDK at
the edge with an `sk-` key and reach **OpenRouter, OpenAI, Anthropic, Gemini,
Bedrock, and Azure** — chat (streaming + tools + vision/multimodal + JSON mode +
web search), embeddings, images, and audio (STT/TTS), plus turn-based and
full-duplex **realtime voice** over WebSockets.

**Multi-tenant by construction.** Every row is `tenant_id`-scoped; cross-tenant
references return `404`, never a leak. Six-role **RBAC** (owner → member) on a
frozen permission matrix. Per-tenant **BYOK** provider keys (Fernet-encrypted at
rest) resolved per request — no shared platform key for completions.

**Three ways to authenticate.** Password login (argon2id) → HS256 JWT; per-tenant
**SSO/OIDC** with tenant-confusion defenses; and an RFC 8628 **device flow** that
mints scoped, budget-capped tokens for headless agents.

**Accurate, billable cost tracking.** Every call is metered to the Postgres
ledger with provider-vs-billed **reconciliation** and disconnect-cost recovery, so
you always know what an upstream charged even when the client hung up.

**Governance & resilience built in.** Per-tenant/team/key **budgets**, **rate
limits** (RPM/TPM) and **bandwidth pacing**, model allowlists, prompt-injection /
PII **guardrails**, exact-match + semantic **response caching**, weighted
**routing** with health-aware fallback, circuit breakers, retries, and graceful
streaming degradation.

**Operable.** Append-only **audit log**, **alerts** (incl. webhook), **SLO** and
**spend** analytics, live rate-limit/health views, OpenTelemetry tracing, an
operator-only cross-tenant **reconciliation** surface (mTLS), data-retention
sweeps, and a Next.js **admin dashboard** (~18 pages).

**AI-application platform.** First-class **memory** (semantic search),
**conversations**, **artifacts** (S3/MinIO or inline), and async **video
generation** jobs.

**Production-shaped everywhere.** An **Envoy edge** (`ext_authz` for `/v1/*`, JWT
validation for `/admin/*`, `/internal/*` hard-blocked) fronts a FastAPI gateway.
Run it locally with `make edge` (Docker), `make kind-up` (Kubernetes), or deploy
the **Helm chart** to the cloud.

## Orientation

| Read | For |
|------|-----|
| `.add/PROJECT.md` | Domain, architecture, key decisions — read first |
| `SETUP-REVIEW.md` | Confidence-tagged setup decisions (human-locked 2026-06-10) |
| `.add/CONVENTIONS.md` | Layout, style, TDD and failure-design rules |
| `.add/GLOSSARY.md` | Canonical names used in contracts and code |
| `.claude/skills/add/phases/` | The ADD phase prompts (setup → observe) |

State-tracked workflow: `python3 .add/tooling/add.py status` shows the resume
point; in Claude Code, run `/add`.

## Develop

```sh
make install   # uv sync
make ci        # lint + typecheck + allowlist gate + tests
```

Stack: Python 3.12 / FastAPI gateway (`apps/gateway`), Envoy edge
(`infra/envoy`), Next.js dashboard (`apps/dashboard`), PostgreSQL + Redis.
