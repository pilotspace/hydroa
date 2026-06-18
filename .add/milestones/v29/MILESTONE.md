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
- [ ] reconciliation-aggregate   depends-on: none — a pure, tested aggregation over `usage_records` for a [from,to] window: Σ(provider_cost where cost_basis='provider'), Σ(billed cost), drift = provider_cost − billed, and the UNBILLED-UPSTREAM breakdown (provider_cost>0 ∧ billed=0, grouped by `usage_source`). Read-only, no schema change. Owns the shared metric contract.
- [ ] reconciliation-endpoint    depends-on: reconciliation-aggregate — `GET /admin/reconciliation?from=&to=` (owner/admin-scoped; tenant-scoped + an operator-wide view) returning the window's drift summary, so ops can observe leak on demand. Thin handler over the aggregate.
- [ ] drift-alert                depends-on: reconciliation-aggregate — a periodic check (scheduled/flusher-adjacent) that computes the window drift and, when it exceeds `GATEWAY_RECONCILIATION_DRIFT_THRESHOLD` (abs-$ or %), fires ONE deduped alert via the existing `alert_events` + webhook seam. The "can never go unnoticed" guarantee.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] An operator queries a time window and sees Σ(provider_cost) vs Σ(billed) and the drift, broken down by `usage_source`.   (verify: pytest apps/gateway/tests/reconciliation_aggregate + reconciliation_endpoint)   (← reconciliation-aggregate, reconciliation-endpoint)
- [ ] A row where the upstream charged us (provider_cost>0) but we billed $0 is COUNTED and surfaced, never silently absorbed.   (verify: pytest apps/gateway/tests/reconciliation_aggregate)   (← reconciliation-aggregate)
- [ ] When the window drift exceeds the configured threshold, EXACTLY ONE deduped alert fires through the existing alert_events/webhook seam (and none below it).   (verify: pytest apps/gateway/tests/drift_alert)   (← drift-alert)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : <add.py / state.json / templates — what shipped, or "untouched">
- skill   : <SKILL.md / phases/* / guides — what shipped, or "untouched">
- book    : <docs/* — what shipped, or "untouched">

### Cross-task evidence   (one row per task)
- <slug> : gate=<PASS|RISK-ACCEPTED> · tests=<n green> · residue=<none|note>

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [ ] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: <restate the milestone goal — and the one evidence line that proves the ship meets it>

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] <step — e.g. open a PR from the Close ship-review above; the human reviews + merges>
- [ ] <step — e.g. export the ship-review to a hand-off doc, e.g. `pandoc CLOSE.md -o close.docx`>
- [ ] <step — e.g. tag / publish / deploy  (human-run, per release.md)>
