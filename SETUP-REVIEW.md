# SETUP-REVIEW.md

All setup decisions, tagged **evidence-based** or **guessed**, ordered
**lowest confidence first**. Human approval of this file locks the foundation
(ADD setup exit gate).

> Status: ⏳ AWAITING HUMAN LOCK

## Decisions (lowest confidence first)

### 1. ⚠ Envoy auth split — jwt_authn for dashboard, ext_authz for API keys — **guessed** (0.70)
You answered "envoy proxy" to the auth question; I interpreted that as Envoy
at the edge enforcing auth, with the gateway still issuing JWTs and owning
key hashes. Plausible alternatives you may have meant: Envoy purely as L7 load
balancer with auth in FastAPI, or Envoy Gateway (Kubernetes). **Cost if wrong:**
Envoy config and `/internal/authz` endpoint reworked — contained, but touches
the security boundary. Confirm before the auth contract freezes.

### 2. ⚠ Cost source of truth: local pricing snapshot, reconciled with OpenRouter generation cost — **guessed** (0.75)
OpenRouter returns usage tokens in responses and exposes a generation-cost
endpoint, but exact reconciliation semantics (streaming usage availability,
cache-discount accounting) need verification against the live API during the
POC slice of cost metering. **Cost if wrong:** billing inaccuracy — the worst
kind of bug for this product. Mitigation: ledger stores raw upstream payload
alongside computed cost, so recomputation is always possible.

### 3. Budget enforcement semantics (reject at limit, eventual consistency) — **guessed** (0.78)
Write-behind metering means budget checks lag actual spend by seconds. Assumed
acceptable for MVP (industry-standard: LiteLLM behaves the same). **Cost if
wrong:** small overage at budget boundaries; tighten with Redis-side counters
later.

### 4. Single service topology (proxy + control plane in one FastAPI app) — **guessed** (0.85)
Simplest deployable that meets MVP; stateless so it scales horizontally.
**Cost if wrong:** router split into two services later — boring, additive work.

### 5. Dashboard: Next.js + shadcn/ui + Tremor + TanStack Query — **evidence-based** (0.90)
Web research (June 2026): default stack for admin/analytics tools; gateway
competitors (LiteLLM, Portkey, Helicone) converge on this class of UI.

### 6. PostgreSQL + Redis, tenant_id scoping, append-only usage ledger — **evidence-based** (0.92)
Standard multi-tenant SaaS persistence; append-only ledger is the established
pattern for metering/billing correctness.

### 7. OpenAI-compatible API surface — **evidence-based** (0.95)
Industry lingua franca; OpenRouter itself is OpenAI-compatible, so the proxy
surface maps ~1:1 to the upstream.

### 8. Python 3.12 + FastAPI for the gateway — **evidence-based** (0.95)
Explicit user choice after tradeoff analysis. Workload is I/O-bound SSE
pass-through; proxy overhead is negligible vs upstream LLM latency. LiteLLM
proves the pattern in production. Documented escape hatch: Go data plane if
per-node concurrency ever becomes the bottleneck.

### 9. MVP stage, code kept — **evidence-based** (0.95)
Explicit user choice; matches the stated single-goal vertical slice.

## Setup exit checklist

- [x] Foundation and living docs drafted with confidence tags
- [x] SETUP-REVIEW.md orders decisions by confidence (lowest first)
- [x] Model pinned (`MODEL_REGISTRY.md`); allowlist exists; pipeline rejects unknown packages
- [x] Pipeline passes on empty skeleton (`make ci` green)
- [ ] **Human locked down** — approve this file (and resolve ⚠ #1, #2) before feature build opens
