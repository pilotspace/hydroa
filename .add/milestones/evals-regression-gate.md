---
type: Milestone
title: Evals — regression gate on model swaps
status: archived
generated: { by: add/3.2.0, at: 2026-08-12 }
verified: []
---
## CARD
goal: a tenant runs a named eval set against a candidate model and gets a scored pass/fail verdict against a pinned baseline, so a model swap is proven safe BEFORE it ships
why: R7 lead, Tin-approved 2026-08-12. The roadmap's M5 is prompt-registry + evals + shadow-A/B — three sizable features; Tin scoped R7 to the narrowest slice with real value, the regression gate. It answers the question a gateway operator is actually asked ("did changing the model break this tenant?") without touching the hot proxy path that shadow-A/B would. Prompt registry and shadow-A/B stay on the roadmap, not here.
next: add freeze eval-set-store

## SCOPE
In:  Tenant-scoped **eval sets** (a named collection of cases: an input request + a
     deterministic assertion). **Runs** of a set against a named model, executed through the
     existing governance path so a run is billed, budgeted and rate-limited exactly like the
     traffic it simulates. **Deterministic scorers** (exact · contains · regex · JSON-schema
     valid). A pinned **baseline** run per set and a **verdict** comparing a candidate run to
     it at a tenant-set threshold. A console to author sets, launch runs, and read the verdict
     with per-case drill-down (UDD design loop — verdict-first IA, per-case diff as the
     signature element, WCAG AA, keyboard-navigable diff).
Out: **LLM-as-judge scoring** — nondeterministic, and a gate whose own verdict flaps is not a
     gate. **Shadow A/B on live traffic** — doubles upstream spend in the hot path; its own
     milestone. **Prompt registry / `prompt:name@ver`** — roadmap M5. **Auto-promotion or
     auto-rollback** on a verdict — this milestone REPORTS; acting is a human decision until
     the gate has a track record. **Cross-provider comparison in one run** — one run, one model.

## GROUND
touches:
  - `proxy/application/governance.py` — budget · tier · credit · rate-limit guards; a run MUST enter through these, not around them.
  - `proxy/infrastructure/model_checker.py::SqlAlchemyModelChecker.check_for_tenant` — tri-state ACTIVE/UNKNOWN/TENANT_DISABLED authority on whether a tenant may dial a model.
  - `proxy/application/use_cases.py` — the completion path a run reuses.
  - retention/ZDR policy surfaces — eval cases are STORED REQUEST PAYLOADS.
  - `catalog/infrastructure/repository.py` — model listing scoped `tenant_id IS NULL OR = :tenant`; a candidate model resolves under the SAME rule.
  - dashboard — a new console section.
anchors: `ModelAccess` (tri-state, no distinguishable 403); the per-tenant breaker port; the governance guard order; `usage_records` as the billing evidence trail. A run is BILLED TRAFFIC — it enters through governance and produces `usage_records` like any request; no unmetered path. Evals grant no visibility a normal request lacks, including finetuned models. A verdict REPORTS — nothing promotes, routes, or rolls back automatically.
risks:
  - **ZDR is the sharp edge of the whole milestone.** An eval case is a persisted request payload — exactly what a ZDR tenant is promised will never be stored. [[zdr-toctou-async-write-paths]] is HARD-STOPPED three times: check-at-entry + persist-after-await is a tenant-reachable bypass. The ZDR re-check must be ATOMIC with the write (`raise_if_zdr_locked`, SELECT … FOR UPDATE) and tested with a slow double that flips the flag mid-await. **Decide EARLY — refuse a ZDR tenant outright vs. assertion-only cases — do not discover this in build.**
  - **Per-tenant breaker.** [[per-tenant-breaker-recurring-defect]]: every new provider surface has shipped a GLOBAL breaker first → cross-tenant DoS, HARD-STOPPED twice. An eval run is a BURST of upstream calls — the most likely surface yet to trip a shared breaker and take out other tenants. Thread the tenant key through the port.
  - **Spend.** A run costs real money. Bypassing budget/credit/tier guards is a spend-control bypass dressed as a feature — a tenant at their credit limit must not spend through evals. Runs are billed traffic, not a side channel.

## EXIT
- [x] User can create a named eval set and add cases, and a ZDR tenant gets the documented, tested disposition rather than silent payload persistence   (← eval-set-store)   (verify: a ZDR tenant's case write is refused/redacted INCLUDING when a slow double flips the flag mid-await; assert on the persisted row)
- [x] User can run a set against a model and see per-case results; the run appears in usage/billing like ordinary traffic   (← eval-run-executor)   (verify: a completed run produces one usage_record per case with the same shape as an equivalent live request)
- [x] User can see each case scored by a deterministic scorer, identically on a re-run   (← deterministic-scorers)   (verify: same case + same response scores identically across 2 runs; each scorer red against a case it must fail)
- [x] User can pin a baseline and get an explicit pass/fail verdict for a candidate against it   (← baseline-and-verdict)   (verify: candidate strictly worse → FAIL, strictly better → PASS, equal-at-threshold decided explicitly not by float luck)
- [x] User can do all of the above from the console, verdict-first, keyboard-navigable, WCAG AA   (← evals-console)   (verify: `next build` + authed capture harness `apps/dashboard/e2e-review/capture.spec.ts`, plus an axe pass with zero serious/critical)
- [x] A tenant at their credit/budget limit CANNOT spend through an eval run   (← eval-run-executor)   (verify: a tenant over budget/credit gets the run refused at the governance guard; assert NO upstream call was made)
- [x] One tenant's eval burst CANNOT open a breaker that degrades another tenant   (← eval-run-executor)   (verify: tenant A's run drives its breaker open; tenant B's request in the same process still succeeds)

## CLOSE
evidence: <one row per task at ship — gate · tests green · residue>
sequencing: risk-first. `eval-set-store` leads because the ZDR disposition is a product decision with a HARD-STOP attached and every other task assumes an answer. After it freezes, `eval-run-executor` ∥ `deterministic-scorers` run concurrently (share only the frozen case shape); `baseline-and-verdict` joins them; `evals-console` is last. Rejected: scorers-first / executor-first (both fix the case shape implicitly → re-freeze); one big task (hides the ZDR seam that deserves its own frozen contract + adversarial verify).
release: one PR per task through the four-eyes gate (`required_approving_review_count: 1` since 2026-08-12); full gateway suite green on live infra proven up at both ends; milestone-done + archive-milestone once every Exit is checked; cut + tag the release — tagging PUBLISHES images via `.github/workflows/publish-images.yml`, do not deploy a tag whose publish job is not green.
