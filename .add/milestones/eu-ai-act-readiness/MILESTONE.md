# MILESTONE: EU AI Act readiness pack

goal: An EU tenant can self-serve produce a dated, Art. 12-mapped record-keeping evidence bundle from the console before EU AI Act GPAI enforcement lands on Aug 2, 2026.
rationale: sub-milestone (quick strike) of the Tin-approved 2026-07-14 enterprise roadmap R1 (docs/roadmap/2026-07-14-enterprise-roadmap.html). EU AI Act Art. 101 GPAI penalty powers apply Aug 2, 2026 (3% global turnover / €15M — NEVER quote the Art. 99 €35M/7% figure; that is the general ceiling, a known competitor-marketing error). Obligations sit on upstream GPAI *providers*; the honest sellable is deployer-side Art. 12 record-keeping + fail-closed EU residency — Hydroa shipped ALL the machinery in 0.8.0 (compliance-export-api, audit store, Logs Explorer, residency, ZDR); this milestone is packaging/assembly + UI + marketing, not new engine surface. Extends: enterprise-identity-compliance (compliance-export-api is the anchor), residency-service-tiers (the residency story being sold), v38 marketing site. HARD DEADLINE: ai-act-marketing-page live before 2026-08-02. Runs parallel to agent-gateway-v1 wave 1; both feed release 0.9.0.
stage: mvp · status: active · created: 2026-07-14T03:32:21+00:00
release: pending

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  a one-click Art. 12-mapped record-keeping export preset (audit events + request logs metadata + usage lineage, with a generated dated cover report) assembled over the existing compliance-export-api; a console Compliance report center (generate / download / schedule monthly) extending Settings → Data & residency; a marketing page + docs telling the residency (refuse-not-reroute) + ZDR + audit story with legally accurate figures and the Fable-5 export-suspension single-vendor-risk narrative.
Out: any claim of "GPAI provider compliance" (obligations sit upstream — we sell deployer-side record-keeping support only); legal advice or DPIA generation; new export/audit engine capability (assembly only — a gap found in the export API becomes a change-request to enterprise-identity-compliance, not silent scope growth here); Annex XI/XII upstream-disclosure aggregation (P2); non-EU regulatory packs (UK/US state law).

UI/UX in scope (compliance-report-center): extends the Settings → Data & residency panel idiom; the generated bundle renders in the Billing console's financial-document idiom (this is evidence — it should look like evidence: dated, tabular-nums, visible immutability seal); scheduled-generation control; WCAG 2.2 AA, axe-checked. Marketing page follows the existing pricing-page voice (plain-language consequence lines).

## Shared decisions & glossary deltas   (living — every task must honor these)
- **Accuracy floor on legal copy**: Art. 101 = 3% global turnover / €15M for GPAI providers; enforcement date 2026-08-02; Digital Omnibus delayed only high-risk-system timelines, NOT GPAI. Every figure on the marketing page cites its article.
- **Evidence, not compliance**: all product/marketing copy says "record-keeping / audit-readiness support", never "makes you AI Act compliant".
- **Bundle is read-only assembly**: the preset composes EXISTING export surfaces (compliance export, audit read, logs metadata, usage lineage) — it never grows a new write path; export access is itself audited (existing invariant).
- Glossary deltas: **Art. 12 bundle** (the dated record-keeping evidence export), **readiness pack** (the residency+ZDR+audit+bundle commercial package).

## Shared / risky contracts (freeze these first)
- Art. 12 bundle manifest shape (sections, cover-report fields, determinism/order guarantee) -> owning task `art12-record-keeping-preset`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] art12-record-keeping-preset   depends-on: none — one-click compliance-export bundle mapped to Art. 12 (audit + logs metadata + usage lineage) with a generated dated cover report; assembly over compliance-export-api.
- [ ] compliance-report-center      depends-on: art12-record-keeping-preset — console surface: generate/download/schedule the bundle (Settings → Data & residency extension; financial-document idiom).
- [ ] ai-act-marketing-page         depends-on: none — marketing page + docs: fail-closed residency, ZDR, audit story; accurate Art. 101 figures; Fable-5 export-suspension vendor-risk narrative. DEADLINE 2026-08-02.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] An EU tenant downloads a dated, deterministic Art. 12 bundle in one action via the API        (← art12-record-keeping-preset)
- [ ] The bundle is generatable, downloadable, and monthly-schedulable from the console, axe-clean        (← compliance-report-center)
- [ ] The AI-Act readiness marketing page is live with accurate Art. 101 figures before 2026-08-02        (← ai-act-marketing-page)

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
- [ ] full BE + dashboard suites green pre-merge on the milestone branch
- [ ] open PR from the Close ship-review; admin-merge on local evidence if org-billing CI blocks
- [ ] marketing page deployed before 2026-08-02; feeds release 0.9.0 "Agent gateway" alongside agent-gateway-v1
