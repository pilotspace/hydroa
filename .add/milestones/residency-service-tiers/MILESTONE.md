# MILESTONE: Data-residency & service-tier routing

goal: A tenant can pin inference to a region (EU via Bedrock/Vertex EU deployments) with a fail-closed residency policy, and buy priority-vs-standard service tiers with tier- and region-differentiated pricing — selling what Anthropic verifiably lacks (no first-party EU; US-pin monetized at 1.1x).
rationale: roadmap M2 of 3 (Tin-confirmed 2026-07-12) — Track C of the approved roadmap; durable competitive whitespace validated by the July-2026 research (only Requesty stakes an EU claim). Detail drafted at activation.
stage: mvp · status: active · created: 2026-07-12T07:38:33+00:00
release: pending

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  region as a first-class deployment dimension (us/eu/ap/global) with Bedrock EU + APAC
     inference-profile entries AND a real Vertex AI adapter (Tin, 2026-07-12: scope GROWN — no
     Vertex adapter existed; second EU/Asia provider wanted) with EU + asia-southeast1 entries; per-tenant fail-closed residency policy; priority-vs-standard service
     tiers (capacity preference + overflow + tier-differentiated markup); region multiplier on
     rate cards (EU-pin premium, mirroring Anthropic's 1.1× US-pin posture); the console +
     marketing surfaces for all of it.
Out: physical EU/Asia deployment of Hydroa itself (infra, not product); a `vn` region value —
     no hyperscaler operates a Vietnam region today, so Vietnamese tenants pin `ap` (served
     from SEA endpoints: Singapore/Thailand); per-city region pinning (coarse us/eu/ap only); per-REQUEST region override (tenant-level policy only, v1);
     latency-based geo-routing (residency is compliance, not performance); CCU/consumption
     units (P2 backlog); prompt registry (P2).

UI/UX in scope (signature: the plain-language consequence line): Settings → Data & residency
extends the EXISTING ZDR panel idiom — region picker, confirm-gated like ZDR, with a
consequence line ("requests that cannot run in the EU will be refused, not rerouted");
catalog gets region badges per deployment; key creation gets a Priority/Standard tier
selector with the price delta inline; marketing pricing page gains the residency + priority
story. WCAG 2.2 AA (axe) floor, Aurora tokens, no new design idiom beyond the badges.

> UI/UX in scope? Name it precisely, not "make it nice" — information architecture ·
> interaction pattern · visual hierarchy · design tokens · component states ·
> accessibility floor (WCAG AA) · responsive breakpoints · user journey
> (`.add/personas-teacher/design/`). Precise ≠ distinctive: skip generic AI-design
> defaults (cream+serif+terracotta · near-black+neon · broadsheet-hairline) and name ONE
> deliberate signature element instead (Claude Code's `frontend-design` skill). A UI
> feature also triggers DESIGN.md via the `add` skill's design.md.

## Shared decisions & glossary deltas   (living — every task must honor these)
1. **Region is a deployment dimension** — one `region` value (us | eu | global) on the
   deployment/catalog row is the single source of truth; policy filters by it, pricing
   multiplies by it; NEVER inferred from a provider URL at request time.
2. **Residency is fail-closed** — no eligible in-region candidate → structured 4xx refusal
   (problem+json, own error code), never a silent out-of-region reroute; mirrors the credits
   spend-gate and ZDR posture. Residency policy changes are audited + confirm-gated.
3. **ONE rate-card resolver** (M1 binding rule carried forward) — region multiplier and tier
   markup resolve through the existing shared resolver; catalog display, recorder billing, and
   invoice lines can never drift. No second pricing path.
4. **Tier is a capacity preference, not a guarantee** — priority gets preference in the
   concurrency guard and may overflow to standard; standard is never starved (bounded share);
   the tier actually served is what's billed and recorded.
5. **Composes with ZDR** — residency and ZDR live in the same Data & residency settings
   surface, same confirm-gate idiom; a ZDR+EU tenant gets both enforced independently.
6. Glossary deltas: `region`, `residency policy`, `service tier`, `region multiplier`,
   `tier markup`.

DECIDED additions (2026-07-12, Tin, at wave-1 freeze): region set = us|eu|**ap**|global
(Asia + Vietnam support; ap seeds 1.0× multiplier, overridable); vertex-adapter task ADDED;
coarse-region granularity; symmetric us./eu./apac. Bedrock seeds.

DECIDED at intake (2026-07-12, Tin): EU region multiplier seeds at **1.1×** (mirrors
Anthropic's US-pin compliance-multiplier posture); Priority tier markup seeds at **+25%**
on top of the tenant's base markup. Both tenant-overridable via the shared rate card.

## Shared / risky contracts (freeze these first)
- `region` column semantics + catalog descriptor shape -> region-catalog-dimension (everything
  else reads it)
- residency refusal error envelope + policy storage shape -> residency-policy (security verify)
- rate-card resolver extension signature (region multiplier · tier markup) -> region-pricing
  (service-tiers consumes it)

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] vertex-adapter   depends-on: region-catalog-dimension — REAL Vertex AI adapter
      (service-account auth, {region}-aiplatform endpoints) + EU (europe-west*) and Asia
      (asia-southeast1) catalog entries citing the frozen region shape   [sensitivity: data]
- [ ] region-catalog-dimension   depends-on: none     — `region` on deployments/catalog
      (us/eu/global) + Bedrock EU (Frankfurt/Ireland/Paris/Stockholm) + Vertex EU entries +
      admin/catalog surfaces expose it   [sensitivity: data]
- [ ] residency-policy   depends-on: region-catalog-dimension — per-tenant residency policy,
      fail-closed structured 4xx when no eligible regional candidate, composes with ZDR,
      audited + confirm-gated   [sensitivity: security — HARD-STOP verify]
- [ ] region-pricing   depends-on: region-catalog-dimension — region multiplier on rate cards
      via the shared resolver (EU-pin premium); flows to recorder + invoices   [sensitivity: data]
- [ ] service-tiers   depends-on: region-pricing — priority/standard per tenant+key: capacity
      preference in the concurrency guard, priority→standard overflow, tier-differentiated
      markup via the same resolver; tier served lands on the usage record   [sensitivity: data]
- [ ] residency-tiers-ui   depends-on: residency-policy, service-tiers — Data & residency
      settings extension, catalog region badges, key-creation tier selector w/ inline price
      delta, marketing pricing page story   [sensitivity: mechanical]

## Exit criteria (observable; map each to the task that delivers it)
- [ ] The catalog shows a region per deployment, including live Bedrock eu./apac. and Vertex
      EU/Asia entries        (← region-catalog-dimension, vertex-adapter)
- [ ] An EU-pinned tenant's request is served ONLY by eu-region deployments; with zero eligible
      EU candidates it gets a structured 4xx refusal — provably never rerouted out-of-region
      (← residency-policy)
- [ ] An EU-pinned tenant's usage bills with the region multiplier applied, visible from
      catalog price display through usage record to invoice line, all via the one resolver
      (← region-pricing)
- [ ] Under real contention a priority key is admitted ahead of standard, overflow works, the
      served tier is on the usage record, and priority bills its differentiated markup
      (← service-tiers)
- [ ] A tenant admin can pin a region behind a typed confirm gate with the consequence line,
      see region badges in the catalog, and pick Priority/Standard at key creation with the
      price delta shown — all axe-clean        (← residency-tiers-ui)
- [ ] The marketing pricing page tells the residency + priority story        (← residency-tiers-ui)

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
