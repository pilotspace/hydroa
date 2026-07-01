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
- [ ] preset-capability-validation depends-on: preset-resolution-ingress — on resolve, apply v55's input-capability guard so a remap to an incompatible model is rejected.

## Exit criteria (observable; map each to the task that delivers it)
- [x] A tenant admin can create/list/delete named presets that remap a model name to a target model   (← preset-admin-surface, tenant-preset-store)
- [ ] A request with `cheap:opus` resolves to the tenant's preset target before routing; a bare/unknown name is byte-identical to today   (← preset-resolution-ingress)
- [ ] A preset target that cannot accept the request's input type is rejected with v55's structured 4xx   (← preset-capability-validation)

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
