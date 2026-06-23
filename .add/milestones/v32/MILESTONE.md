# MILESTONE: Writable routing configuration

goal: an owner edits model-groups, routing strategy, and per-deployment limits from the dashboard and the changes persist (apply on restart)
rationale: **sub-milestone** (re-sized from v31's `routing-config-write` task at its "re-size at open" point, Tin-confirmed 2026-06-23). Ground recon proved making routing config writable is net-new architecture, not a single task: today all routing config is BOOT-TIME env vars (`core/config.py:Settings`) with NO DB/ORM/migration and NO reload — `FallbackModelRouter` is a singleton built once at boot on the request hot-path. v31 shipped every READ-side control-plane surface; this milestone adds the one WRITE-side capability that was itself missing.
stage: production · status: active · created: 2026-06-23

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  an operator-wide, persisted routing configuration (model-groups + per-deployment weight/rpm/tpm limits, routing strategy, retry policy, cooldown/circuit thresholds) editable from the dashboard `/routing` page; a write endpoint that round-trips ALL existing `Settings` validators before persist; **persist + restart-to-apply** semantics (config saved to DB, applied when the gateway next boots/reloads from config).
Out: **LIVE hot-reload of the running `FallbackModelRouter`** (explicitly deferred — Tin chose persist+restart-to-apply to avoid mutating the routing hot-path under concurrent traffic; live reload may be a later milestone) · per-tenant routing overrides (this config is operator-wide, like today's env config) · any change to the routing ALGORITHMS / strategy implementations (only their configuration becomes writable) · the frozen `GET /admin/routing` read shape beyond additively exposing deployment weight/limits.

## Shared decisions & glossary deltas   (living — every task must honor these)
- **Apply-mechanism = persist + restart-to-apply** (DECIDED 2026-06-23, Tin). The editor MUST clearly label that changes take effect on restart; the running router is NOT mutated at write time. SAFE by construction — no live mutation of the request hot-path.
- **Operator-wide singleton config** — one routing config for the deployment (mirrors today's env-var model), NOT per-tenant. Persisted as a single authoritative row/document.
- **Validation parity** — the write path must enforce EXACTLY the `Settings`/`Deployment` validators that run today at pydantic-settings load (empty-candidate-list, duplicate-deployment, too-many-candidates, alias-collides-with-candidate, unknown-routing-strategy, weight>0, positive limits). A persisted config that boot can't load is the failure to prevent.
- **Boot precedence** — define + freeze whether the DB config OVERRIDES env (DB-wins) or only fills gaps; pick at the first task's contract. Lean DB-wins-when-present, else env fallback (no regression for env-only deployments).

## Shared / risky contracts (freeze these first)
- **`routing_config` persistence shape + boot precedence** -> owning task `routing-config-store`. The DB row/document schema (model_groups+deployments JSONB, strategy, retry/cooldown knobs) + how `create_app` merges it over `Settings` at boot. Freeze before the editor is built.
- **`PUT /admin/routing` validated write contract** -> owning task `routing-config-write-endpoint`. Round-trips all validators; owner/admin; returns the new persisted config in the (extended) `GET /admin/routing` shape.

## Tasks (breadth-first decomposition; detail lives in each TASK.md — written just-in-time)
- [x] routing-config-store          depends-on: none — NEW alembic migration `a2c4e6f8b0d1` + ORM `RoutingConfigRow` (operator-wide singleton row, boolean-PK CHECK) + `RoutingConfigRepository`; `merge_routing_config` (probe-validate + model_copy = validator parity) + extracted `build_model_router`; lifespan boot-apply rebuilds the router over merged Settings (DB-wins, env fallback; fail-closed to env on read/build error). Gate PASS 2026-06-23, refute-read UPHOLD 0.82. Persistence shape + boot precedence FROZEN.
- [x] routing-config-write-endpoint depends-on: routing-config-store — DELIVERED BY the `routing-config-write` task (repurposed from the v31 umbrella holder; gate PASS 2026-06-23, refute-read UPHOLD 0.88). `PUT /admin/routing` (owner/admin, always-on — Tin-approved authz) validates via merge_routing_config (full parity) → persists only whitelisted keys; `GET /admin/routing` additively gains `routing_strategy` + `deployments` (weight/rpm/tpm). Read-after-write via merge(settings, stored). New error ERR_ROUTING_CONFIG_INVALID.
- [ ] routing-config-editor         depends-on: routing-config-write-endpoint — dashboard `/routing` editor: model-group/deployment CRUD (alias, model_id, weight, rpm/tpm), strategy dropdown, retry + cooldown knobs; "changes apply on restart" notice; invalidate ["admin-routing"]. (re-size if the editor alone proves large.)
- [x] routing-config-write (the original v31 task) — REPURPOSED as routing-config-write-endpoint above (its §0 GROUND recon seeded all three tasks). Done.

## Exit criteria (observable; map each to the task that delivers it)
- [x] A persisted routing config exists in the DB and the gateway loads it over env at boot (DB-wins-when-present, env fallback).   (← routing-config-store; verified by `test_boot_applies_persisted_config` + migration parity, gate PASS)
- [x] An owner PUTs a new routing config (valid) and reads it back; an invalid config (e.g. duplicate deployment, unknown strategy) is rejected with the existing validator error, nothing persisted.   (← routing-config-write-endpoint; verified by test_put_valid_persists_and_returns + test_put_{unknown_strategy,duplicate_deployment}_rejected, gate PASS)
- [ ] An owner edits model-groups / routing strategy / deployment limits from the `/routing` dashboard and sees the saved config (with a clear "applies on restart" indication).   (← routing-config-editor)

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
