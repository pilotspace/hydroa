# MILESTONE: Per-tenant model presets

goal: A tenant admin defines named presets that remap model names (opus to gpt5-5), selects among multiple presets via a name: prefix (cheap:opus), and requests resolve the preset to a concrete model before the router while the existing fallback seam stays byte-identical.
rationale: new-major (intake), second of the confirmed two-milestone roadmap (v55 capabilities → v56 presets). Queued — promote with `add.py activate v56` after v55 closes. Per-tenant model-name presets are net-new (no "preset" concept exists today; the existing `model_groups` aliases are operator-global fallback groups, a different layer). Tin confirmed via AskUserQuestion 2026-06-30: per-tenant scope · colon prefix `cheap:opus`. THIN sketch only — full decomposition written just-in-time at activation.
stage: production · status: queued · created: 2026-06-30T12:11:18+00:00

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  Per-tenant named presets that remap a model name to a concrete target model (`opus → gpt5-5`);
     multiple presets selected by a colon prefix in the `model` field (`cheap:opus`, `quality:opus`);
     ingress resolution that rewrites `model` to the target BEFORE `FallbackModelRouter` (downstream
     byte-identical); tenant-scoped admin API + dashboard editor to manage presets; and preset-time
     reuse of v55's input-capability guard so a remap to an incompatible model is caught.
Out: Operator-global routing/alias changes (existing `model_groups` untouched). Per-user / per-API-key
     scope (Tin chose per-tenant). Cross-tenant preset sharing. Capability authoring (that's v55).

## Shared decisions & glossary deltas   (living — every task must honor these)
- PRESET ≠ ALIAS: a preset is a PER-TENANT name remap resolved at ingress, above the operator-global
  `model_groups` alias/fallback layer. Presets feed the router a concrete model id; the router is unchanged.
- Selector grammar: `<preset>:<alias>` with a single colon delimiter (avoids `/` and `.` that appear in
  real model ids). A bare model id with no matching preset resolves unchanged (byte-identical).
- Reuse v55's frozen `unsupported_input_modality` error verbatim when a preset target rejects the input.

## Shared / risky contracts (freeze these first)
- per-tenant preset table shape + ingress resolution grammar (`preset:alias`) -> preset-resolution-ingress

## Tasks (breadth-first decomposition; detail lives in each TASK.md — THIN sketch, finalize at activation)
- [x] tenant-preset-store          depends-on: none                     — per-tenant preset table (tenant_id · preset_name · alias_key · target_model) + migration + domain/store + catalog-target validation. DONE gate=PASS 2026-07-01 (13 green; migration b5f8a1d4c7e0 chains from single head c2e4a6f8b0d3; full gateway suite 2075/0 after 2 sanctioned test-manifest additions). Contract went through 3 freezes: v1→v2 fixed a wrong ground-truth claim about branched Alembic heads; v2→v3 fixed a build-discovered concurrency/type-safety defect (dropped an injected `model_checker` param that led to a shared-singleton private-attribute-mutation anti-pattern — replaced with a fresh per-call checker matching the other 8 call sites in the codebase).
- [x] preset-resolution-ingress    depends-on: tenant-preset-store      — parse `preset:alias`, look up caller's tenant presets, rewrite `model` → target before the router; unknown preset → structured error. DONE gate=PASS 2026-07-01 (20 green; full gateway suite 2095/0). Scope chosen by Tin via AskUserQuestion: ALL 5 entry points (chat, images, embeddings, STT, TTS, realtime-WS chat), always-on (no feature flag). Contract v1 approved with authenticator threaded via constructor into the 4 non-chat use cases (not a new NonChatGovernance property — governance.py stays untouched). An independent adversarial refute-read subagent (run before gating) found a real gap: realtime_ws.py's `_real_stt`/`_real_tts` omitted the new ctor params entirely, leaving realtime-WS STT/TTS silently unresolved while chat correctly resolved — fixed (mirrored `_real_chat`'s wiring) + 2 regression tests added, full suite re-confirmed green before PASS was recorded.
- [x] preset-admin-surface         depends-on: tenant-preset-store      — tenant-scoped API + dashboard editor to manage multiple presets. DONE gate=PASS 2026-07-01 (15 backend + 4 frontend new tests; full gateway suite 2110/0, dashboard 910/0). Contract v2: Tin overrode the v1 JSON-body PUT/DELETE draft, choosing path params (`/admin/presets/{preset_name}/{alias_key}`) to mirror provider-keys — resolved the resulting encoded-slash hazard with a new router-local "/" rejection guard (reuses `ERR_PRESET_SELECTOR_INVALID`), safe by construction since this task is the first-ever writer for the table. An adversarial refute-read (first pass NOT-EARNED) found two real defects, both fixed: (1) the new nav link was reachable by any role but the backend is strict OWNER-only (stricter than `/app/keys`) — added a new `minRole:"owner"` nav tier so non-owners never see a permanently-broken 403 page; (2) an independently-reproduced, ~25-30%-flaky Postgres deadlock in `bootstrap_fresh_db`'s per-test schema reset (confirmed to ALSO affect the already-shipped `tenant_model_presets` suite, a pre-existing repo-wide test-infra characteristic, not new) — fixed with a bounded retry-on-deadlock wrapper in `DbTenantModelPresetStore.upsert`/`.delete` (production hardening) plus the same retry around both suites' bootstrap DDL; 8x stress-verified, 0 failures after (was ~25-30%). A third finding (TOCTOU in upsert-then-fetch, inherited from the `put_provider_key` precedent) was accepted as-is (low severity, no cross-tenant/data risk) and forward-carried as a SPEC delta.
- [x] preset-capability-validation depends-on: preset-resolution-ingress — on resolve, apply v55's input-capability guard so a remap to an incompatible model is rejected. DONE gate=PASS 2026-07-01 (12 green; full gateway suite 2136/0 post-merge). Contract added a NEW coarse `Modality`-axis guard (`MODEL_MODALITY_MISMATCH`, 400) on images/embeddings/TTS — a materially different axis from v55's fine-grained `InputModality` content guard, which is chat/STT-only and untouched. An adversarial refute-read found two real findings: (1) `OpenAIDirectProvider.post_multipart` doesn't raise as loudly as OpenRouter's facade, so the "STT is safe by construction" doc claim is provider-specific — forward-carried as a SPEC delta, out of this task's frozen scope; (2) HARD-STOP-class — the new unconditional guard's safety assumption (catalog `modality` is always real data) was FALSE in this stale worktree, because catalog sync never wrote `modality` on upsert; the fix was already shipped on `origin/main` (commit 3469a1e, PR #50, the `openrouter-embeddings` milestone) but predated this branch's fork point. Resolved by committing all 4 v56 tasks as 4 separate commits, then merging `origin/main` in (verified byte-identical file reconstruction against a pristine backup before merging), confirming the fix landed (`repository.py:210` now writes `modality=model.modality`), and re-running the full suite (2136 passed, 0 failed).

## Exit criteria (observable; map each to the task that delivers it)
- [x] A tenant admin can create/list/delete named presets that remap a model name to a target model   (← preset-admin-surface, tenant-preset-store)
- [x] A request with `cheap:opus` resolves to the tenant's preset target before routing; a bare/unknown name is byte-identical to today   (← preset-resolution-ingress)
- [x] A preset target that cannot accept the request's input type is rejected with v55's structured 4xx   (← preset-capability-validation)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : untouched (this milestone touched no `add.py`/templates; `.add/state.json` was updated only as normal task-lifecycle bookkeeping, plus a union-merge resolving the concurrent `openrouter-embeddings` milestone's own state.json entries)
- skill   : untouched
- book    : untouched
- gateway (backend) : new `tenant_model_presets` table + migration `b5f8a1d4c7e0`; `TenantModelPresetStore` (+ deadlock-retry hardening); preset:alias resolution wired into all 5 entry points (chat, images, embeddings, STT, TTS) + realtime-WS; tenant-scoped admin API (`/admin/presets/...`); new coarse `MODEL_MODALITY_MISMATCH` guard on images/embeddings/TTS
- dashboard (frontend) : new Presets admin page + editor components; nav gained an owner-only Presets link

### Cross-task evidence   (one row per task)
- tenant-preset-store : gate=PASS · tests=13 green · residue=none
- preset-resolution-ingress : gate=PASS · tests=20 green (+5 fixtures corrected during preset-capability-validation, Tin-authorized) · residue=none
- preset-admin-surface : gate=PASS · tests=15 backend + 4 frontend green · residue=1 accepted TOCTOU (SPEC delta, low severity)
- preset-capability-validation : gate=PASS · tests=12 green, full suite 2136/0 post-merge · residue=1 SPEC delta (OpenAI STT post_multipart safety claim, provider-specific not general)

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which) — criterion 1 ← tenant-preset-store + preset-admin-surface rows; criterion 2 ← preset-resolution-ingress row; criterion 3 ← preset-capability-validation row
- goal: A tenant admin defines named presets that remap model names, selects among multiple via a `name:` prefix, and requests resolve the preset to a concrete model before the router while the fallback seam stays byte-identical — proven end-to-end by the 4 tasks above (store → ingress resolution → admin surface → capability guard), all gate=PASS, full gateway suite green (2136/0) after merging in the one external prerequisite fix this milestone's safety property depended on.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] Tin reviews the 4-commit diff on `feat/v56-model-presets` (line-by-line human review — the one
      outstanding item from preset-capability-validation's gate record)
- [ ] open a PR from `feat/v56-model-presets` → `main`, human reviews + merges
- [ ] run ADD housekeeping (`archive-milestone v56` + `fold`) after merge, per the established pattern
- [ ] bundle into the next release alongside the already-releasable `gateway-health`/`openrouter-embeddings` milestones (human-run, per release.md)
