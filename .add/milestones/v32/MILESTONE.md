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
- [x] routing-config-editor         depends-on: routing-config-write-endpoint — NEW `RoutingEditor.tsx` on the /routing page (owner/admin gated): strategy dropdown + per-alias deployment CRUD (model_id/weight/rpm/tpm, object-form), Save→bffPut→invalidate ["admin-routing"], "applies on restart" notice, inline 422 error with validator code. Gate PASS 2026-06-23, refute-read findings actioned. Dashboard suite 384 green. (strategy + deployments cover the milestone's named editables; global retry/cooldown knobs deferred — see SPEC delta.)
- [x] routing-config-write (the original v31 task) — REPURPOSED as routing-config-write-endpoint above (its §0 GROUND recon seeded all three tasks). Done.

## Exit criteria (observable; map each to the task that delivers it)
- [x] A persisted routing config exists in the DB and the gateway loads it over env at boot (DB-wins-when-present, env fallback).   (← routing-config-store; verified by `test_boot_applies_persisted_config` + migration parity, gate PASS)
- [x] An owner PUTs a new routing config (valid) and reads it back; an invalid config (e.g. duplicate deployment, unknown strategy) is rejected with the existing validator error, nothing persisted.   (← routing-config-write-endpoint; verified by test_put_valid_persists_and_returns + test_put_{unknown_strategy,duplicate_deployment}_rejected, gate PASS)
- [x] An owner edits model-groups / routing strategy / deployment limits from the `/routing` dashboard and sees the saved config (with a clear "applies on restart" indication).   (← routing-config-editor; verified by test_owner_edit_and_save + test_restart_notice_visible + test_member_no_editor, gate PASS)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway (backend) : NEW `routing_config` singleton table (migration `a2c4e6f8b0d1`) + ORM + `RoutingConfigRepository`; `merge_routing_config` (validator-parity probe) + extracted `build_model_router`; lifespan boot-apply (DB-wins/env-fallback, fail-closed). `PUT /admin/routing` write + additive `GET /admin/routing` (routing_strategy + deployments); new `ERR_ROUTING_CONFIG_INVALID`. `ROUTING_OVERRIDE_KEYS` made public.
- dashboard (frontend) : NEW `RoutingEditor.tsx` on /routing (owner/admin gated) — strategy + deployment CRUD, Save→PUT, "applies on restart" notice, inline validator-code error.
- tooling/skill/book : untouched.

### Cross-task evidence   (one row per task)
- routing-config-store : gate=PASS · tests=full gateway 1359 green (12 suite) · refute-read UPHOLD 0.82 · residue=none
- routing-config-write (write-endpoint) : gate=PASS · tests=full gateway 1368 green (9 suite + routing_admin 8) · refute-read UPHOLD 0.88 · residue=none
- routing-config-editor : gate=PASS · tests=dashboard 384 green (6 suite) · refute-read findings actioned (2 fixed, 2 refuted) · residue=none

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: "an owner edits model-groups, routing strategy, and per-deployment limits from the dashboard and the changes persist (apply on restart)" — MET: the /routing editor (routing-config-editor) PUTs to /admin/routing (routing-config-write), which validates + persists to the routing_config singleton (routing-config-store), and the gateway boot-merge adopts it on the next restart. End-to-end proven by the three suites above (BE round-trip + boot-apply + FE editor round-trip).

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
- [ ] open a PR for branch `feat/v31` → main (it carries v31 + the v32 sub-milestone commits) — Tin reviews + merges (per [[git-push-https-gotcha]]: push via gh HTTPS token, account TinDang97).
- [ ] note for ops/deploy: routing config is RESTART-TO-APPLY — a saved config takes effect only after the gateway process restarts (document in the deploy runbook).
- [ ] tag / publish / deploy — human-run, per release.md (v32 is a candidate to bundle into the next release alongside v30/v31).
