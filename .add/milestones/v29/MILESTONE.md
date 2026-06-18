# MILESTONE: Billing reconciliation — provider cost vs billed, with drift alert

goal: every dollar an upstream provider charges us is reconciled against what we billed the tenant, and drift beyond a configured threshold raises an alert — an upstream charge with no matching user charge can never go unnoticed
rationale: new-major (Tin, 2026-06-18 via AskUserQuestion: "finish v28 t3, then scope reconciliation as v29" → "Open v29=reconciliation, UI↔BE→v30"). Directly answers Tin's question — "how do we make sure our upstream doesn't charge us while we don't charge the user's usage?" v27 stamped a per-row `provider_cost` + `cost_basis` and v28 closed the silent-$0 completeness gaps (every $0 now carries a `usage_source`), but NOTHING aggregates provider_cost against billed cost to DETECT drift. This milestone adds the reconciliation measurement + an alert so an upstream-charged-but-unbilled row can never go unnoticed. Took the v29 slot ahead of the UI↔BE coverage program (renumbered v29→v30). Serves the standing production goal's "accurate, billable cost tracking" half.
stage: production · status: active · created: 2026-06-18

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  a READ-ONLY reconciliation over the append-only `usage_records` ledger: (1) a pure, tested
     aggregation for a time window — Σ(provider_cost) vs Σ(billed cost) → drift, plus a count/breakdown
     of UNBILLED-UPSTREAM rows (provider_cost>0 ∧ billed=0) grouped by `usage_source`; (2) an
     admin/owner-scoped endpoint to observe that window's drift on demand; (3) a periodic drift check
     that fires ONE deduped alert through the existing `alert_events` + webhook seam when the window
     drift exceeds a configured threshold.
Out: retroactive RE-BILLING or any mutation of historical ledger rows (the ledger is append-only —
     reconciliation only MEASURES + ALERTS); pulling the provider's real INVOICE via an external API
     (provider_cost is v27's per-row upstream-reported cost; invoice-level true-up is a later milestone);
     any new token-estimate heuristic for unbilled streams (v27/v28 rejected heuristic token math in the
     money path); the UI↔BE dashboard-coverage program (now v30); a full billing/cost dashboard surface
     (a reconciliation VIEW endpoint is in; rich dashboards are v30).

## Shared decisions & glossary deltas   (living — every task must honor these)
- **Drift is computed only over `cost_basis='provider'` rows** — only those carry an authoritative
  upstream `provider_cost` to reconcile against; `catalog`-priced rows have no independent provider truth,
  so they are surfaced SEPARATELY, never folded into drift.
- **A $0-billed row is not automatically a leak.** A legitimately free/derived-$0 row (e.g. an
  unavailable-duration STT) differs from an upstream-CHARGED-but-unbilled row; the metric distinguishes
  strictly via `provider_cost > 0 ∧ billed = 0` (and reports the `usage_source` that explains it —
  `client_disconnect`, `stream_fallback`, …).
- **Read-only over the append-only ledger** — reconciliation NEVER re-bills or edits historical rows
  (consistent with v28's "no retroactive re-billing").
- **Reuse the existing alerting seam** (`alert_events` + the webhook dispatcher), deduped by window+type —
  no new alert infrastructure (mirrors the soft-budget-alert pattern).
- Glossary deltas (new terms): **reconciliation drift** · **unbilled-upstream row** · **reconciliation window**.

## Shared / risky contracts (freeze these first)
- **The reconciliation metric definition** — what "drift" is, and what precisely counts as an
  "unbilled-upstream row" (the `cost_basis`/`provider_cost`/billed/`usage_source` semantics). The endpoint
  and the alert BOTH consume it, so it is the one genuine cross-cutting decision. -> owning task
  `reconciliation-aggregate`.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] reconciliation-aggregate   depends-on: none — a pure, tested aggregation over `usage_records` for a [from,to] window: Σ(provider_cost where cost_basis='provider'), Σ(billed cost), drift = provider_cost − billed, and the UNBILLED-UPSTREAM breakdown (provider_cost>0 ∧ billed=0, grouped by `usage_source`). Read-only, no schema change. Owns the shared metric contract.   **DONE 2026-06-18 · gate PASS · 8 RA tests green (1194 full suite).**
- [x] reconciliation-endpoint    depends-on: reconciliation-aggregate — `GET /admin/reconciliation?window=&start=&end=` (owner/admin-scoped; **TENANT-SCOPED** — see the operator-view note below) returning the window's drift summary, so an admin can observe their tenant's leak on demand. Thin handler over the aggregate (reuses `_compute_window_bounds` + `require_owner_or_admin`). Contract FROZEN @ v1 (2026-06-18).   **DONE 2026-06-18 · gate PASS · 10 RE tests green (1204 full suite) · refute-read 0.88, tenant-isolation CONFIRMED.**
  - **operator-wide-reconciliation-view** (deferred follow-up, scoped by Tin 2026-06-18): grounding revealed the auth model has NO cross-tenant platform-operator role, so the endpoint above is tenant-scoped only (an all-tenants view via a tenant JWT would breach tenant isolation). A cross-tenant operator-wide endpoint view needs a NEW platform-operator authority (super-admin role/claim or ops-auth) — its own security-sensitive task; placement (v29 extra vs a later milestone) TBD. The all-tenants leak monitor is meanwhile served by the server-side drift-alert (t3, no per-request caller to authorize).
- [x] drift-alert                depends-on: reconciliation-aggregate — a periodic check (in-process `run_forever`, lifespan-wired) that computes the operator-wide current-UTC-day drift and, when `unbilled_upstream_cost` exceeds `GATEWAY_RECONCILIATION_DRIFT_THRESHOLD` (absolute $, `Decimal`), fires ONE per-day deduped system alert via the existing `alert_events` + webhook seam. Default-OFF (both knobs 0). The "can never go unnoticed" guarantee.   **DONE 2026-06-18 · gate PASS · 10 DA tests green (1214 full suite) · refute-read BLOCK→4 coverage gaps closed (DA5 drift-field, DA8 boundary, DA9 run_forever, DA10 wiring-predicate), impl sound on every §3 clause.**

## Exit criteria (observable; map each to the task that delivers it)
- [x] An operator queries a time window and sees Σ(provider_cost) vs Σ(billed) and the drift, broken down by `usage_source`.   (verify: pytest apps/gateway/tests/reconciliation_aggregate + reconciliation_endpoint)   (← reconciliation-aggregate, reconciliation-endpoint)
- [x] A row where the upstream charged us (provider_cost>0) but we billed $0 is COUNTED and surfaced, never silently absorbed.   (verify: pytest apps/gateway/tests/reconciliation_aggregate)   (← reconciliation-aggregate)
- [x] When the window drift exceeds the configured threshold, EXACTLY ONE deduped alert fires through the existing alert_events/webhook seam (and none below it).   (verify: pytest apps/gateway/tests/drift_alert)   (← drift-alert)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway/usage : NEW `usage/application/reconciliation.py` (`reconcile_window` → `ReconciliationSummary`, the shared READ-ONLY metric — Σ(provider_cost) vs Σ(billed) over `cost_basis='provider'`, drift, unbilled-upstream count/breakdown by `usage_source`); NEW `usage/application/drift_checker.py` (`ReconciliationDriftChecker` periodic monitor + `should_start_drift_checker` start-guard predicate); `usage/api/router.py` + `schemas.py` (`GET /admin/reconciliation`, tenant-scoped, `ReconciliationResponse`).
- gateway/core : `core/config.py` — two default-OFF knobs (`reconciliation_drift_threshold: Decimal = 0`, `reconciliation_check_interval_seconds: int = 0`).
- gateway/main : lifespan wires the drift checker behind the start-guard (gated on both knobs > 0), cancels + final-drains it on shutdown alongside dispatcher/health_checker; `drift_checker_task` pre-initialised in `create_app`.
- schema/deps : NO migration · NO new table/column · NO new dependency (reuses the existing `alert_events` + `AlertDispatcher` webhook seam).
- tooling / skill / book : untouched by the milestone's feature work (the working-tree `.add/` engine + `.claude/skills` edits are pre-existing, unrelated to v29).

### Cross-task evidence   (one row per task)
- reconciliation-aggregate : gate=PASS · tests=8 RA green (1194 full suite) · residue=none (owns the frozen metric contract; 5 cosmetic ruff findings noted as a chore-lint follow-up).
- reconciliation-endpoint  : gate=PASS · tests=10 RE green (1204 full suite) · residue=none · refute-read 0.88, tenant-isolation CONFIRMED (operator-wide view deferred — needs a new platform-operator authority).
- drift-alert              : gate=PASS · tests=10 DA green (1214 full suite) · residue=none · refute-read BLOCK→4 test-coverage gaps closed, impl sound on every §3 clause.

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
  - EC1 (query a window → provider vs billed + drift by source): `reconciliation-aggregate` (`reconcile_window`) + `reconciliation-endpoint` (`GET /admin/reconciliation`) — 8 RA + 10 RE green.
  - EC2 (provider_cost>0 ∧ billed=$0 row COUNTED + surfaced): `reconciliation-aggregate` `unbilled_upstream_cost`/`unbilled_rows`/`by_source` — RA tests assert the count + per-source breakdown.
  - EC3 (drift over threshold → EXACTLY ONE deduped alert, none below): `drift-alert` `ReconciliationDriftChecker` — DA1 (fires one), DA2/DA8 (none at/below threshold), DA3 (per-day dedup), DA4 (operator-wide).
- goal: every dollar an upstream provider charges us is reconciled against what we billed the tenant, and drift beyond a configured threshold raises an alert. Proof: the operator-wide `ReconciliationDriftChecker` reads `unbilled_upstream_cost` (Σ provider_cost where billed=$0) every interval and fires one deduped `reconciliation_drift` alert through the existing webhook seam the moment it exceeds the threshold — an upstream charge with no matching user charge can never go unnoticed (1214-test suite green; default-OFF until the operator sets a threshold + interval).

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] commit each task individually (done: reconciliation-aggregate, reconciliation-endpoint; drift-alert pending) + a `chore(add)` fold commit on `feat/v29-billing-reconciliation`.
- [ ] open a PR from `feat/v29-billing-reconciliation` → `main` under the `TinDang97` gh account (HTTPS push per the git-push gotcha); human reviews + merges. **Outward-facing — ASK Tin before pushing/opening.**
- [ ] operator runbook note: enabling the monitor is opt-in — set `GATEWAY_RECONCILIATION_DRIFT_THRESHOLD` (absolute USD) **and** `GATEWAY_RECONCILIATION_CHECK_INTERVAL_SECONDS` (both > 0) plus a configured alert webhook; both 0 (default) = monitor off, behavior byte-identical.
- [ ] CI note: the org-billing pre-existing red (0-step jobs) is unrelated to this milestone; the gateway suite is green locally (1214, ex live-stack edge).
