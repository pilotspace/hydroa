# MILESTONE: Reconciliation hardening — make leak-detection trustworthy

goal: a platform operator can trust the billing reconciliation signal — no nonsense config silently disables it, no false leak from catalog rows, no unexplained $0 on disconnect, and drift is visible across all tenants
rationale: new-major (v30). Closes the open reconciliation hardening deltas from the v29 §7 observe — file-cited in `drift-alert`, `reconciliation-aggregate`, `reconciliation-endpoint`. Relationship to the milestone map: *extends* the billing-accuracy theme (v27 precision → v28 robustness → v29 reconciliation → **v30 hardening**); *depends-on* the v29 reconciliation primitives (`reconcile_window`, `/admin/reconciliation`, `drift-alert`) it hardens. Bumped the UI↔BE coverage stub v30 → v31 (5th renumber, same documented pattern). Scope "Core 3 + operator-wide view" + operator-auth model "separate ops-auth surface" both confirmed by Tin 2026-06-18.
stage: production · status: active · created: 2026-06-18

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  config hardening (reject a nonsense drift threshold at startup) · filter correctness (`cost_basis`-scoped `unbilled_upstream_cost`) · disconnect billing completeness — the milestone GREW from the original 4-task sketch into a streaming-refactor + disconnect-cost-recovery program (Tin "do both together in v30"): incremental SSE translation for the 3 previously-buffered providers (Anthropic/Gemini/Bedrock), deterministic upstream-abort on client disconnect, `provider_cost` stamped on disconnect rows, and a full OpenRouter authoritative-cost recovery chain (generation client → gen-id capture → partial-floor+signed-delta correction → inline fire-and-forget wiring → periodic backstop sweep).
Out: operator-wide cross-tenant reconciliation **endpoint** behind the new ops-auth surface — **DEFERRED to v31** (Tin 2026-06-22: "defer to v31, close v30 now"; the task is drafted at phase=ground and detached from v30, awaiting the risk:high security contract freeze + Tin's explicit security approval) · the RA9 belt-and-suspenders read-only COUNT test + the 6-money-field str-type assertion sweep (low-value nits — deferred) · a **dashboard UI** for the operator view · any change to markup semantics or the drift-sign convention (frozen at v29) · alert delivery-channel changes (the `drift-alert` seam stays as-is).

## Shared decisions & glossary deltas   (living — every task must honor these)
- **NEW glossary term — `platform operator`**: an authority that reads *across* tenants. The #1 invariant ("every tenant-owned query is tenant-scoped") gets exactly ONE named, audited exception — the cross-tenant reconciliation read — and it lives behind a **separate ops-auth surface**, never on a tenant JWT.
- **NEW glossary term — `ops-auth`**: the separate operator credential surface — its own issuer/signing key (NOT mintable via tenant signup), enforced on an edge-restricted path. Designed-for-failure per the IO rule (verification timeout/cache/fallback where a key fetch is involved).
- **`unbilled_upstream_cost` is provider-basis-only**: a counted row satisfies `cost_basis='provider' ∧ cost_usd=0`. The recorder invariant (catalog rows have NULL `provider_cost`) becomes an EXPLICIT filter clause, no longer relied upon implicitly.
- **Design-for-failure floor**: a nonsense monitor config (`inf`/`nan`/≤0 threshold) FAILS LOUD at startup — never silent-disables. (global IO/design-for-failure rule)

## Shared / risky contracts (freeze these first)
- **Platform-operator authority model = separate ops-auth surface** (DECIDED 2026-06-18, Tin) -> owning task `operator-wide-reconciliation`. A dedicated operator credential with its own issuer/signing key, NOT issuable through tenant signup, enforced on an edge-restricted path (`/ops/...` or the existing edge-blocked `/internal` family). Cross-tenant power NEVER rides a tenant JWT; the tenant-isolation invariant stays pure. Freeze the exact wire shape (issuer/claims/path/verification + failure modes) in this task's §3, human-approved, before any code.
- **`reconcile_window` §3 supersession** -> owning task `reconcile-cost-basis-filter`. The `cost_basis='provider'` filter clause re-freezes the frozen v29 aggregate via the supersession pattern (behavior-preserving on today's data — catalog rows already excluded by the NULL-provider_cost invariant).
- **OPEN at t4 specify**: whether the cross-tenant aggregation needs an all-tenants *mode* on the (now re-frozen) `reconcile_window` — a second supersession — vs a sibling query. Decide at `operator-wide-reconciliation` §1/§3.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] drift-threshold-validation     gate=PASS  — reject non-finite / ≤0 `GATEWAY_RECONCILIATION_DRIFT_THRESHOLD` at config-load (fail loud). (risk:low)
- [x] reconcile-cost-basis-filter    gate=PASS  — `AND cost_basis='provider'` on the `unbilled_upstream_cost` filter in `reconcile_window` (supersession). (risk:low)
- [x] incremental-sse-translation    gate=PASS  — Anthropic+Gemini stateful SSE steppers; adapters stream incrementally off `aiter_lines` (byte-identical wrappers retained). Prereq for cost-saving abort. (risk:medium)
- [x] bedrock-incremental-stream     gate=PASS  — `aiter_event_stream` tail-buffer frame reassembly + `_BedrockSSEStepper`; Bedrock now incremental. (risk:medium)
- [x] disconnect-provider-cost       gate=PASS  — deterministic upstream `gen.aclose()` (stop event to provider) + `provider_cost` stamped on client-disconnect rows; `suppress(BaseException)` so CancelledError never masks the disconnect. (risk:medium — billing path)
- [x] openrouter-generation-client   gate=PASS  — `OpenRouterCompletionUpstream.get_generation(id) -> GenerationCost | None` (authoritative `total_cost`); permanent 4xx raises, not-ready→None. (risk:medium)
- [x] provider-generation-id-capture gate=PASS  — additive nullable `provider_generation_id` column (mig `c9f2a4d7e1b8`) stamped on disconnect rows. (risk:low)
- [x] openrouter-cost-recovery (t6.2b) gate=PASS — out-of-band recovery core: partial-floor + SIGNED delta-top-up correction row; gid-global uuid5 idempotency + SET-NX counter guard. (risk:medium — billing path)
- [x] openrouter-cost-recovery-wiring (t6.2c) gate=PASS — fire-and-forget inline recover() from the disconnect handler when provider==openrouter; default-OFF knob. (risk:medium)
- [x] openrouter-recovery-sweep (t6.3) gate=PASS — periodic backstop (mirror drift_checker): recover() unrecovered in-window OpenRouter disconnect rows; partial index; default-OFF knob. (risk:medium)
- [→] operator-wide-reconciliation   **DEFERRED to v31** (Tin 2026-06-22) — cross-tenant reconciliation endpoint behind the separate ops-auth surface; tenant admin/member denied (403). Detached from v30 at phase=ground; risk:high security contract freeze awaits Tin's explicit approval. depends-on: reconcile-cost-basis-filter (done).

## Exit criteria (observable; map each to the task that delivers it)
- [x] A nonsense drift threshold (`inf` / `nan` / ≤0) fails fast at startup with a clear error; the monitor never runs silently useless.        (← drift-threshold-validation)
- [x] The unbilled-upstream filter counts only provider-basis rows; a catalog row carrying a `provider_cost` is never counted as a leak.        (← reconcile-cost-basis-filter)
- [x] A request whose client disconnects mid-stream records its `provider_cost`, so a real upstream charge billed $0 surfaces in reconciliation drift.   (← disconnect-provider-cost)
- [x] A client-disconnected OpenRouter stream's REAL upstream cost is recovered and billed (partial-floor + signed delta), inline and via a periodic backstop — no silent $0.   (← openrouter-generation-client + provider-generation-id-capture + openrouter-cost-recovery + -wiring + -sweep)
- [→] A platform operator reads cross-tenant reconciliation drift through the authorized ops-auth endpoint; a tenant admin/member is denied (403).   (← operator-wide-reconciliation — **DEFERRED to v31**, Tin 2026-06-22)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway/usage : NEW `cost_recovery.py` (recovery core), `recovery_sweep.py` (periodic backstop); recorder `record_correction` (signed delta + explicit id); flusher explicit-id branch; reconciliation `cost_basis='provider'` filter; orm partial index `ix_usage_records_gen_recovery`; drift-threshold validator. Migrations `c9f2a4d7e1b8` (provider_generation_id), `d1e2f3a4b5c6` (recovery index).
- gateway/proxy : incremental SSE steppers (Anthropic/Gemini/Bedrock) + `aiter_event_stream`; disconnect handler deterministic `gen.aclose()` + provider_cost stamp + inline fire-and-forget recovery wiring (`_InlineCostRecovery` Protocol); `OpenRouterCompletionUpstream.get_generation`.
- gateway/core  : config knobs `GATEWAY_OPENROUTER_COST_RECOVERY_ENABLED`, `GATEWAY_OPENROUTER_RECOVERY_SWEEP_INTERVAL_SECONDS`; drift-threshold validation.
- gateway/main  : lifespan wiring for the cost-recovery service + recovery-sweep task (default-OFF, cancelled on shutdown).
- tooling / skill / book : untouched (engine bookkeeping only — state.json).

### Cross-task evidence   (one row per task)
- drift-threshold-validation       : gate=PASS · residue=none
- reconcile-cost-basis-filter      : gate=PASS · residue=none
- incremental-sse-translation      : gate=PASS · residue=none
- bedrock-incremental-stream       : gate=PASS · residue=none
- disconnect-provider-cost         : gate=PASS · residue=none (BUG-1 suppress(BaseException) closed by strengthening)
- openrouter-generation-client     : gate=PASS · residue=none (contract frozen @ v2 post-refute)
- provider-generation-id-capture   : gate=PASS · residue=none
- openrouter-cost-recovery (t6.2b) : gate=PASS · residue=none (counter double-increment closed via SET-NX)
- openrouter-cost-recovery-wiring  : gate=PASS · residue=none
- openrouter-recovery-sweep (t6.3) : gate=PASS · tests=13 · residue=none (refute 0.84, no cheat)
- FULL SUITE: 1294 green (excl tests/edge live-stack), single-process.

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row (criteria 1–4 PASS; criterion 5 operator-wide DEFERRED to v31 by Tin's explicit decision — not a gap in the shipped scope)
- goal: a platform operator can trust the billing reconciliation signal — MET for the single-operator surface: nonsense config fails loud (drift-threshold-validation), no false catalog leak (reconcile-cost-basis-filter), disconnect rows carry provider_cost AND their real OpenRouter cost is recovered/billed inline + via backstop (disconnect-provider-cost + t6.1/t6.2/t6.2b/t6.2c/t6.3). The cross-tenant *operator-wide* view is the one consciously-deferred slice → v31.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [x] PR #17 (`feat/v30-reconciliation-hardening`, off main `96e5a11`) carries t1–t6.3; merge to main (Tin approved 2026-06-22 "merge now"; via gh API over HTTPS).
- [ ] open v31 (UI↔BE coverage stub) AND seed `operator-wide-reconciliation` (risk:high, drafted at ground) as its first non-UI task — the deferred slice.
- [ ] tag / publish / deploy — human-run per release.md (infra deploy still gated on Tin's pipeline; CI red = org-billing 0-step, unrelated).
