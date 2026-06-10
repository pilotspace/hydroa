# GLOSSARY  (one name per concept — used everywhere: specs, contracts, code)

Tenant: a customer organization; owns users, API keys, budgets, and a usage ledger — every tenant-owned row carries `tenant_id`.
User: a human belonging to one tenant; logs into the dashboard via JWT. Roles: owner, admin, member.
API key: secret credential (`sk-<key_id>.<secret>`) issued per tenant for proxy access; stored as SHA-256 hash (amended 2026-06-10 from argon2: 32-byte CSPRNG secrets make offline brute force infeasible and /internal/authz sits on the proxy hot path — passwords stay argon2); plaintext shown exactly once at creation.
Proxy request: an OpenAI-compatible call to `/v1/*` forwarded to OpenRouter, streaming or non-streaming.
Upstream: OpenRouter (`https://openrouter.ai/api/v1`) — the platform's single LLM provider.
Model: an LLM identifier from the OpenRouter catalog (e.g. `anthropic/claude-fable-5`).
Model catalog: cached, priced list of models synced from OpenRouter; tenants see marked-up prices.
Usage record: append-only ledger row per proxy request — tenant, key, model, prompt/completion tokens, cost (USD), latency, status, raw upstream payload.
Cost: USD amount per request = upstream cost × (1 + tenant markup), computed from the pricing snapshot effective at request time.
Markup: per-tenant margin percentage applied over upstream cost; the platform's revenue.
Pricing snapshot: immutable copy of a model's per-token prices captured at catalog sync.
Budget: monthly spend ceiling per tenant; exceeding it rejects proxy requests with `ERR_BUDGET_EXCEEDED` (near-real-time check, small in-flight overage tolerated).
Gateway: the FastAPI service (`apps/gateway`) — proxy data plane + admin control plane.
Edge: Envoy front proxy — TLS, jwt_authn (dashboard JWTs), ext_authz (API keys), rate limiting.
ext_authz: Envoy external-authorization call to the gateway's `/internal/authz` to validate API keys.
Dashboard: the Next.js admin UI (`apps/dashboard`).
Write-behind: usage records buffered in Redis and flushed asynchronously to Postgres, keeping metering off the streaming hot path.

# ADD method vocabulary (domain-standard names; bridges to legacy terms)
GOAL: the one durable outcome a project (and each milestone) runs toward — the loop's orientation anchor, declared as the lowercase `goal:` line in PROJECT.md / MILESTONE.md and surfaced by status/guide every session; distinct from a task's §1 Must (a single required behavior, not the whole-project outcome).
deep verify: the deepened Verify evidence (v20) required beyond passing tests — for a task that produced code, that every new symbol is referenced (wiring) and no new dead/unused code exists; for prose/non-code, a recorded no-skim semantic read; which path applies is resolver-judged and the engine never classifies (a rubric, not add.py).
onboarding: the install -> first-milestone path (formerly "on-ramp").
primary flow: the solid forward path of the flow diagram — a phase starts only when its input exists (formerly "forward spine").
cross-cutting concern: a concern running through every step rather than being one step — security, testing, observability, cost (formerly "spine / continuous concern").
working state: everything an agent loads each session — skill router, active phase, PROJECT/MILESTONE/TASK, state.json (formerly "state surface").
audit trail: the reference record read by people, never auto-loaded into agent context (formerly "story surface").
method rationale: the why behind every rule — the AIDD book, loaded on demand, never duplicated (formerly "trust layer").
failing-first suite: the test suite written before code, confirmed red for the right reason — a missing implementation (formerly "red safety net").
non-functional review: the deliberate post-evidence check of what tests rarely catch — concurrency, security, architecture (formerly "blind-spot checks").
change scope: the files a locked run may and may not touch (formerly "touch-boundary"; the <touch_boundary> XML prompt tag keeps its name).
automated quality gate: the evidence-based Verify resolver under autonomy auto — may auto-PASS on complete evidence; security always escalates (formerly "evidence auto-gate").
autonomy level: the per-task Verify resolver setting — auto (default) or conservative; declared in the TASK.md header, human-reviewed at the freeze (formerly "autonomy dial").
living documentation: the durable project artifacts — conventions, glossary, frozen contracts — that outlive any particular code (formerly "survivor layer").
scope level: the granularity a decision lives at — intake level (request -> versioned scope), milestone level, setup/foundation level, task level (formerly "altitude").
baseline approval: the one human gate that freezes the AI-drafted foundation, first scope, and first contract together — runs as `add.py lock` (formerly "the lock-down").
lesson learned: a single learning a loop produces, tagged by the competency it improves — the `- [DDD · open]` grammar and deltas.md/`add.py deltas` machine names stay (formerly "competency delta").
lowest-confidence flag: the AI's ranked declaration of the 1–2 points most likely to be wrong in what a human is asked to approve — each with why + cost-if-wrong; the ⚠ glyph keeps its name as the machine marker (formerly "least-sure flag").
decision point: a stop for human judgment — the contract-freeze approval, an escalated verify gate, intake confirmation, milestone close; the machine names seam (--json owner enum, decide key) and seam-audit (CI job) keep their names (formerly "seam").
retrospective consolidation: gathering confirmed lessons learned at milestone close and writing them append-only into the versioned foundation — human-confirmed, never self-approved; the machine names fold.md, the folded status, and add.py deltas keep their names (formerly "the fold / fold ritual").
specification bundle: a task's spec, scenarios, contract, and failing tests drafted as one piece and approved by a person once at the contract freeze (formerly "the one-approval front").
