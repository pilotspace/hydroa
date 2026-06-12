# MILESTONE: LiteLLM parity slice 6 — router & load-balancing

goal: a model alias with multiple deployments distributes requests across them by a configured routing strategy, honoring per-deployment rate limits, with v6 fallback+cooldown and billing intact
rationale: sub-milestone of the production parity roadmap (Tin confirmed "Router / load-balancing" as the next slice, 2026-06-12). LiteLLM's flagship feature is the Router — multiple deployments behind one model alias with a routing strategy. v6 delivered ordered fallback + per-candidate circuit-breaker cooldown; v7 delivered the provider-selection seam. This slice upgrades the model-group from always-first-then-fallback to load-balanced selection across equivalent deployments, the single largest remaining parity surface that composes directly on v6+v7.
stage: production · status: active · created: 2026-06-12

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  A model alias (model group) resolves to MULTIPLE deployments; a configured
     routing strategy picks the PRIMARY deployment per request (no longer always
     the first). Strategies: simple-shuffle (weighted-random by deployment weight,
     the default), least-busy (fewest in-flight), latency-based (lowest recent
     EWMA latency). Per-deployment TPM/RPM limits skip a saturated deployment at
     selection time (usage-based routing). v6 fallback ordering + per-deployment
     cooldown still remove failed/unhealthy deployments after the strategy picks.
     Billing keys on the SERVED deployment's catalog model id (v6 invariant).
     Backward-compatible: a bare-string model group (the v6 shape) behaves as a
     single weight-1, no-limit deployment — chat stays byte-identical.
Out: cross-region/geo routing; cost-based routing; client-specified deployment
     pinning; provider breadth (Anthropic/Azure/Bedrock — a later slice); spend
     tags/reports (a later slice); admin UI for deployment config (config-driven
     only this slice); the v7 soft-budget-alert + empty-key-guard follow-ups
     (tracked open, not in this milestone).

## Shared decisions & glossary deltas   (living — every task must honor these)
- GLOSSARY: introduce **Deployment** — one concrete (model_id, provider, optional
  weight/tpm_limit/rpm_limit) member of a **Model group** (alias). A model group
  is now an ordered list of Deployments, not of bare model-id strings.
- GLOSSARY: **Routing strategy** — the policy that selects the PRIMARY deployment
  for a model-group request; orthogonal to v6 **fallback** (what happens after a
  failure) and v6 **cooldown** (removing an unhealthy deployment).
- Additive/back-compat is non-negotiable: a bare-string group member = a
  weight-1, no-limit deployment; the v6 ordered-fallback behavior is the default
  when no strategy is configured. Chat path stays byte-identical (v6 invariant).
- Per-deployment counters (in-flight, recent-latency, tpm/rpm usage) live in Redis
  keyed by deployment, reusing the v1 rate-limit + v6 cooldown infrastructure;
  no new datastore.
- Billing keys on the served deployment's catalog model id — the router's returned
  candidate id, never response_body["model"] (folded v6 §Key Decision).

## Shared / risky contracts (freeze these first)
- Deployment / model-group config shape (string-or-object union, weight/tpm/rpm
  fields, back-compat coercion) -> owning task `deployment-model` (FREEZE FIRST —
  every routing task inherits it)
- RoutingStrategy Protocol seam (select(deployments, context) -> ordered
  deployments) -> owning task `routing-strategy` (frozen before least-busy/latency
  strategies build against it)

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] deployment-model    depends-on: none              — extend model-group config from ordered string-list to a list of Deployment objects (model_id + weight + tpm_limit + rpm_limit); back-compat string coercion; GLOSSARY Deployment/Model-group/Routing-strategy
- [ ] routing-strategy    depends-on: deployment-model  — RoutingStrategy Protocol + simple-shuffle (weighted-random) default; selects the primary deployment, then defers to the v6 fallback chain on failure
- [ ] balance-strategies  depends-on: routing-strategy  — least-busy (Redis in-flight counter) + latency-based (Redis EWMA) strategies, config-selectable; default stays simple-shuffle
- [ ] deployment-limits   depends-on: routing-strategy  — per-deployment TPM/RPM limits skip a saturated deployment at selection (usage-based routing); all saturated → clean 429; reuses v1 rate-limit infra
- [ ] v8-live-verify      depends-on: balance-strategies, deployment-limits — e2e double-pass: a multi-deployment alias distributes by strategy, honors per-deployment limits, fallback+cooldown still remove failures, chat billing unaffected

## Exit criteria (observable; map each to the task that delivers it)
- [ ] A model alias with ≥2 deployments distributes requests across them per the configured strategy — not always the first deployment (← routing-strategy)
- [ ] simple-shuffle honors deployment weights; least-busy selects the fewest in-flight; latency-based selects the lowest recent-latency deployment (← balance-strategies)
- [ ] A deployment over its TPM or RPM limit is SKIPPED at selection (another deployment serves); when every deployment is saturated the request gets a clean 429, not a 500 (← deployment-limits)
- [ ] v6 fallback ordering and per-deployment cooldown still remove failed/unhealthy deployments after the strategy picks; the ledger bills the SERVED deployment's catalog model id (← deployment-model)
- [ ] Backward compatibility: a bare-string model group (the v6 shape) behaves as a single weight-1, no-limit deployment and the chat path stays byte-identical to v6 (← deployment-model)
- [ ] All of the above proven LIVE through the TLS edge with a multi-deployment stub overlay, two consecutive clean passes (← v8-live-verify)
