# MILESTONE: Production-ready metered proxy

goal: the v1 MVP runs in production posture — TLS at the edge, migration-managed schema, cookie-based dashboard auth, full observability, and a live-verified OpenRouter billing path
rationale: intake 2026-06-10 classified the post-v1 work as new-major (next product theme: production hardening, named in the v1 roadmap note). Drafted from v1 residue (live-smoke composite evidence), the v1 freeze flags (localStorage-JWT BFF upgrade path), the open SDD question (streaming cost reconciliation), and the folded ADD delta (node-dep governance). Confirmed by Tin Dang ("Create v2 as drafted").
stage: production · status: active · created: 2026-06-10

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  Alembic migration baseline + CI parity gate · live OpenRouter smoke with streaming
     cost reconciliation (closes the v1 composite-evidence residue and the open SDD
     question) · TLS termination at Envoy + production compose topology · httpOnly-cookie
     BFF replacing localStorage JWT (the upgrade path named at the dashboard-shell
     freeze) · structured JSON logs with request_id/tenant_id + Prometheus metrics
     (breaker state, 402 rate, flusher lag) wired to the §7 Watch monitors · lifespan
     migration off deprecated on_event, graceful shutdown draining the usage flusher,
     readiness vs liveness probes, backup/rollback runbook, node-dep governance follow-up
Out: email verification · SSO/OIDC · per-key budgets · per-tenant model allowlists ·
     BYOK / hybrid credentials · invoicing & export · multi-region · hard-cap budget
     escrow · prompt logging/observability product features (all remain v3/enterprise)

## Shared decisions & glossary deltas   (living — every task must honor these)
- Schema changes are ADDITIVE Alembic migrations with documented rollback; create_all is
  dev/test-only and never runs when GATEWAY_ENVIRONMENT=production (CONVENTIONS: Architecture)
- The ledger stays the billing source of truth; live reconciliation compares ledger cost to
  OpenRouter generation cost × (1 + tenant Markup) — divergence is a defect, not a rounding note
- No token may be readable by page JavaScript after auth-bff lands (GLOSSARY: JWT — transport
  becomes httpOnly cookie; the gateway JWT contract itself is unchanged)
- Every log line carries request_id; tenant-scoped lines carry tenant_id; secrets and full
  prompt payloads never appear in logs
- Foundation v2 conventions apply to all new tests: red-for-the-right-reason check at freeze,
  within()-scoped UI assertions, byte-identical security failure responses

## Shared / risky contracts (freeze these first)
- Alembic baseline == ORM metadata parity (the schema contract every task builds on) -> owning task db-migrations
- BFF cookie/session surface (dashboard ↔ gateway auth flow change) -> owning task auth-bff
- /internal/metrics exposition format + metric names -> owning task observability

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] db-migrations        depends-on: none           — Alembic baseline + CI parity gate; prod stops using create_all
- [ ] live-upstream-smoke  depends-on: none           — real-key curl through Envoy streams a completion; ledger cost reconciled
- [ ] edge-tls             depends-on: none           — TLS listener + cert wiring at Envoy, HSTS, production compose topology
- [ ] auth-bff             depends-on: none           — httpOnly-cookie BFF via Next.js route handlers; no JWT in localStorage
- [ ] observability        depends-on: none           — structlog JSON + request/tenant IDs, /internal/metrics, §7 monitors wired
- [ ] ops-hardening        depends-on: db-migrations  — lifespan handlers, flusher-draining shutdown, probes, backup/rollback runbook, node-dep governance

## Exit criteria (observable; map each to the task that delivers it)
- [ ] `alembic upgrade head` from an empty DB produces a schema identical to ORM metadata, asserted in CI       (← db-migrations)
- [ ] A real-key curl through TLS-Envoy streams a live completion; the ledger row reconciles with OpenRouter generation cost × (1+markup)  (← live-upstream-smoke, edge-tls)
- [ ] No auth token is readable from page JavaScript; dashboard flows pass with httpOnly cookies               (← auth-bff)
- [ ] /internal/metrics exposes breaker state, 402 rate, and flusher lag; every log line carries request_id (tenant_id where scoped)  (← observability)
- [ ] Gateway shutdown under load loses zero buffered usage events (drain test)                                 (← ops-hardening)
- [ ] Stage flips to production via graduate.md only after all criteria above are checked                       (← milestone close)
