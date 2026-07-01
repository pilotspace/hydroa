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
- [ ] tenant-preset-store          depends-on: none                     — per-tenant preset table (tenant_id · preset_name · alias_key · target_model) + migration + domain/store + catalog-target validation.
- [ ] preset-resolution-ingress    depends-on: tenant-preset-store      — parse `preset:alias`, look up caller's tenant presets, rewrite `model` → target before the router; unknown preset → structured error.
- [ ] preset-admin-surface         depends-on: tenant-preset-store      — tenant-scoped API + dashboard editor to manage multiple presets.
- [ ] preset-capability-validation depends-on: preset-resolution-ingress — on resolve, apply v55's input-capability guard so a remap to an incompatible model is rejected.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] A tenant admin can create/list/delete named presets that remap a model name to a target model   (← preset-admin-surface, tenant-preset-store)
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
