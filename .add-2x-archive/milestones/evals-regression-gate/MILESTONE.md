# MILESTONE: Evals — regression gate on model swaps

goal: a tenant can run a named eval set against a candidate model and get a scored pass/fail verdict against a pinned baseline, so a model swap is proven safe BEFORE it ships
rationale: R7 lead, Tin-approved 2026-08-12 (AskUserQuestion ×2). The roadmap's M5 is prompt-registry + evals + shadow-A/B — three sizable features. Tin scoped R7 to the NARROWEST slice with real value: the regression gate. It answers the question a gateway operator is actually asked ("did changing the model break this tenant?") without touching the hot proxy path, which shadow-A/B would. Prompt registry and shadow-A/B stay on the roadmap, not in this milestone.
stage: production · status: active · created: 2026-08-12T05:37:13+00:00
relations: relates-to: managed-rag-finetune

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/PLAN.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  Tenant-scoped **eval sets** (a named collection of cases: an input request + a
     deterministic assertion). **Runs** of a set against a named model, executed through the
     existing governance path so a run is billed, budgeted and rate-limited exactly like the
     traffic it simulates. **Deterministic scorers** (exact · contains · regex · JSON-schema
     valid). A **pinned baseline** run per set, and a **verdict** comparing a candidate run to
     it against a tenant-set threshold. A console page to author sets, launch runs, and read
     the verdict with per-case drill-down.

Out: **LLM-as-judge scoring** — nondeterministic and it spends money to grade money; a
     regression gate whose own verdict flaps is not a gate. **Shadow A/B on live traffic** —
     mirrors real requests into the hot path and doubles upstream spend; it needs its own cost
     and failure design and is a separate milestone. **Prompt registry / `prompt:name@ver`** —
     roadmap M5, not this. **Auto-promotion or auto-rollback** on a verdict — this milestone
     REPORTS; acting on the report is a human decision until the gate has a track record.
     **Cross-provider comparison** in one run — one run targets one model.

> UI/UX in scope: the evals console is a user-facing surface, so it takes the UDD design loop
> (see [[ui-ux-polish-standing-bar]]), not a CRUD table. Name precisely at design time: IA
> (sets → runs → cases, three levels deep), the verdict as the page's primary object rather
> than a row count, per-case diff as the signature element, WCAG AA, keyboard-navigable diff.

## Ground   (shared real-code context — gathered ONCE; every task's specify projects from this)
Touches (shared files · symbols):
  - `proxy/application/governance.py` — `_check_model_catalog` / budget · tier · credit ·
    rate-limit guards. A run MUST enter through these, not around them.
  - `proxy/infrastructure/model_checker.py::SqlAlchemyModelChecker.check_for_tenant` — the
    tri-state ACTIVE/UNKNOWN/TENANT_DISABLED authority on whether a tenant may dial a model.
  - `proxy/application/use_cases.py` — the completion path a run reuses.
  - retention/ZDR policy surfaces — eval cases are STORED REQUEST PAYLOADS.
  - `catalog/infrastructure/repository.py` — model listing is scoped
    `tenant_id IS NULL OR = :tenant`; a candidate model must resolve under the SAME rule.
  - dashboard: a new console section.

Anchors: `ModelAccess` (tri-state, no distinguishable 403); the per-tenant breaker port; the
  governance guard order; `usage_records` as the billing evidence trail.

Honors (conventions): PROJECT.md `invariants:` bind under the BARE declared runtime. Clean
  architecture — dependencies point inward; the executor depends on a port, never on a
  provider SDK. Design-for-failure is mandatory on every IO path (CLAUDE.md).

Issues/Risks (shared) — the three defects this repo has shipped REPEATEDLY; each is a
  HARD-STOP if it reaches review, so each is named here BEFORE the first task:
  1. **ZDR is the sharp edge of this whole milestone.** An eval case is a persisted request
     payload — precisely what a ZDR tenant is promised will never be stored. [[zdr-toctou-async-write-paths]]
     records this as HARD-STOPPED TWICE: check-ZDR-at-entry + persist-after-await is a
     tenant-reachable bypass. The ZDR re-check must be ATOMIC with the write, and must be
     tested with a slow double that flips the flag mid-await. Decide EARLY whether a ZDR
     tenant is refused outright or restricted to assertion-only cases — do not discover this
     in build.
  2. **Per-tenant breaker.** [[per-tenant-breaker-recurring-defect]]: every new provider
     surface so far has shipped a GLOBAL breaker → cross-tenant DoS, HARD-STOPPED twice. An
     eval run is a BURST of upstream calls, so it is the most likely surface yet to trip a
     shared breaker and take out other tenants. Thread the tenant key through the port.
  3. **Spend.** A run costs real money. If it bypasses the budget/credit/tier guards it is a
     spend-control bypass dressed as a feature — a tenant at their credit limit could keep
     spending through evals. Runs are billed traffic, not a side channel.

Also live, and worth knowing while touching this area: eval runs make the catalog's
  finetune-registered models more reachable, and todos #59 + #64 COMPOUND there — a
  registration that loses the `ON CONFLICT (id) DO NOTHING` race leaves the job `succeeded`
  with no `models` row, and the unbounded 300s repair sweep then re-selects it forever.
  Assessed 2026-08-12: NOT a security issue (the dial path is tenant-scoped, nothing is
  overwritten, ids are unforgeable, the column is unbounded `Text`) — so it does not gate this
  milestone, but fix the pair together when touched.

## Shared decisions & glossary deltas   (living — every task must honor these)
- **eval set** — a tenant-owned, named collection of eval cases. **eval case** — one request
  body + one deterministic assertion. **run** — one execution of a set against one model.
  **baseline** — the run a set's verdicts are measured against. **verdict** — pass/fail of a
  candidate run vs the baseline at a tenant-set threshold.
- A run is BILLED TRAFFIC. It enters through `governance.py` and produces `usage_records` like
  any request. There is no unmetered execution path.
- A candidate model must pass `check_for_tenant` exactly as a live request would. Evals grant
  no visibility a normal request lacks — including for finetuned models.
- A verdict REPORTS. Nothing in this milestone promotes, routes, or rolls back automatically.

## Shared / risky contracts (freeze these first)
- eval-case persistence + ZDR disposition -> owning task `eval-set-store`   ← freeze FIRST;
  every other task's storage assumptions hang off it, and it is the HARD-STOP surface.
- run-execution port (tenant-keyed breaker, bounded concurrency, budget entry) -> `eval-run-executor`

## Tasks (breadth-first decomposition; detail lives in each PLAN.md)
- [ ] eval-set-store        depends-on: none                — tenant-scoped sets + cases; ZDR disposition decided and enforced atomically with the write.
- [ ] eval-run-executor     depends-on: eval-set-store      — run a set against a model through the governance path; per-tenant breaker, bounded concurrency, timeouts, partial-run resumability.
- [ ] deterministic-scorers depends-on: eval-set-store      — exact · contains · regex · JSON-schema; one scorer port, no LLM judge.
- [ ] baseline-and-verdict  depends-on: eval-run-executor, deterministic-scorers — pin a baseline, compare a candidate, emit a thresholded verdict.
- [ ] evals-console         depends-on: baseline-and-verdict — UDD design loop; verdict-first IA, per-case diff as the signature element.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] User can create a named eval set and add cases to it, and a ZDR tenant gets the documented, tested disposition rather than silent payload persistence   (← eval-set-store)   (verify: test — a ZDR tenant's case write is refused/redacted, INCLUDING when a slow double flips the ZDR flag mid-await; assert on the persisted row, not the response)
- [ ] User can run a set against a model and see per-case results; the run appears in usage/billing like ordinary traffic   (← eval-run-executor)   (verify: test — a completed run produces one usage_record per case with the same shape as an equivalent live request)
- [ ] User can see each case scored by a deterministic scorer, with the same input scoring identically on a re-run   (← deterministic-scorers)   (verify: test — same case + same response scores identically across 2 runs; each scorer red against a case it must fail)
- [ ] User can pin a baseline and get an explicit pass/fail verdict for a candidate model against it   (← baseline-and-verdict)   (verify: test — a candidate strictly worse than baseline yields FAIL, strictly better yields PASS, and equal-at-threshold is decided explicitly rather than by float luck)
- [ ] User can do all of the above from the console, verdict-first, keyboard-navigable, WCAG AA   (← evals-console)   (verify: command — `next build` + authed capture harness `apps/dashboard/e2e-review/capture.spec.ts`, plus an axe pass with zero serious/critical)
- [ ] A tenant at their credit/budget limit CANNOT spend through an eval run   (← eval-run-executor)   (verify: test — a tenant over budget/credit gets the run refused at the governance guard; assert NO upstream call was made)
- [ ] One tenant's eval burst CANNOT open a breaker that degrades another tenant   (← eval-run-executor)   (verify: test — tenant A's run drives its breaker open; tenant B's request in the same process still succeeds)

## Strategy   (AI-drafted WITH the human — the optimized task plan)
- Approach (sequencing): **risk-first**. The two things that can sink this milestone are ZDR
  and the shared breaker, and both are cheapest to settle before code exists. `eval-set-store`
  leads because the ZDR disposition is a product decision with a HARD-STOP attached, and every
  other task assumes an answer to it. Building scorers first would feel faster and would be
  building on an unfrozen foundation.
- Freeze-first: eval-case persistence + ZDR disposition. Nothing else starts until it is frozen.
- Waves (parallel): after `eval-set-store` freezes, `eval-run-executor` and
  `deterministic-scorers` can run CONCURRENTLY — they share only the frozen case shape.
  `baseline-and-verdict` then joins them; `evals-console` is last and sequential.
- Tradeoffs weighed: (a) *scorers-first* — tempting because it is pure and testable, rejected
  because it fixes the case shape implicitly and would force a re-freeze. (b) *executor-first*
  — rejected for the same reason plus it would need a stub store. (c) *one big task* — rejected;
  the ZDR surface deserves its own frozen contract and its own adversarial verify, and bundling
  it hides that seam. (d) *include the residue drain here* — rejected, Tin sequenced the #59
  assessment ahead of evals and it came back clear; the residue is real but unrelated work and
  folding it in would blur this milestone's exit criteria.

## Close — ship review   (AI fills when every task is done)
> Cross-task review the AI fills — the evidence behind the EXISTING milestone-done gate, NOT a new approval.

### Ship by domain   (what changed, per bounded context)
- tooling : <add.py / state.json / templates — what shipped, or "untouched">
- skill   : <SKILL.md / phases/* / guides — what shipped, or "untouched">
- book    : <docs/* — what shipped, or "untouched">

### Cross-task evidence   (one row per task)
- <slug> : gate=<PASS|RISK-ACCEPTED> · tests=<n green> · residue=<none|note>

### Goal met?   (map the evidence back to this milestone's Exit criteria)
- [ ] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: <restate the milestone goal — and the one evidence line that proves the ship meets it>

## Release steps   (AI-DEFINED — engine records, human gate)
- [ ] open a PR per task through the four-eyes gate (`required_approving_review_count: 1` since 2026-08-12 — every PR needs an approval from a second human)
- [ ] full gateway suite green on live infra, with the stack proven up before the first test and after the last
- [ ] milestone-done + archive-milestone once all Exit criteria are checked
- [ ] cut the release and tag it — tagging now PUBLISHES the images via `.github/workflows/publish-images.yml`; do not deploy a tag whose publish job is not green
