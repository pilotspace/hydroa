# 2.x → 3.0 migration — recorded 2026-08-12 by Tin Dang (decided 2026-08-12 after reviewing that ADD 3.x refuses to translate 2.x state by design; queue exported to 78 GitHub issues first)

Nothing was deleted. This directory is the complete 2.x bundle, byte-identical,
renamed from `.add/`. The fresh 3.0 bundle beside it starts empty on purpose:
2.x state is not translated, because its markers (phase, autonomy, waivers) mean
things 3.0 deliberately refuses to mean. Re-author each task below against its
archived PLAN.md — the direction work transfers; the bypasses do not.

## 2.x tasks to re-author

- `breaker-4xx-classification` (2.x phase: done) — archived at `tasks/breaker-4xx-classification/PLAN.md`; re-author with `add new Task breaker-4xx-classification`
- `ci-flake-classification` (2.x phase: done) — archived at `tasks/ci-flake-classification/PLAN.md`; re-author with `add new Task ci-flake-classification`
- `ci-restoration` (2.x phase: done) — archived at `tasks/ci-restoration/PLAN.md`; re-author with `add new Task ci-restoration`
- `ci-timeout-and-e2e-scope` (2.x phase: done) — archived at `tasks/ci-timeout-and-e2e-scope/PLAN.md`; re-author with `add new Task ci-timeout-and-e2e-scope`
- `date-bomb-sweep` (2.x phase: done) — archived at `tasks/date-bomb-sweep/PLAN.md`; re-author with `add new Task date-bomb-sweep`
- `eval-set-store` (2.x phase: direction) — archived at `tasks/eval-set-store/PLAN.md`; re-author with `add new Task eval-set-store`
- `file-search-tool` (2.x phase: done) — archived at `tasks/file-search-tool/PLAN.md`; re-author with `add new Task file-search-tool`
- `files-uploads-api` (2.x phase: done) — archived at `tasks/files-uploads-api/PLAN.md`; re-author with `add new Task files-uploads-api`
- `finetune-broker` (2.x phase: done) — archived at `tasks/finetune-broker/PLAN.md`; re-author with `add new Task finetune-broker`
- `finetune-model-registry` (2.x phase: done) — archived at `tasks/finetune-model-registry/PLAN.md`; re-author with `add new Task finetune-model-registry`
- `flake-tail-burndown` (2.x phase: done) — archived at `tasks/flake-tail-burndown/PLAN.md`; re-author with `add new Task flake-tail-burndown`
- `image-edits-variations` (2.x phase: done) — archived at `tasks/image-edits-variations/PLAN.md`; re-author with `add new Task image-edits-variations`
- `lint-type-debt-sweep` (2.x phase: done) — archived at `tasks/lint-type-debt-sweep/PLAN.md`; re-author with `add new Task lint-type-debt-sweep`
- `login-global-error-position` (2.x phase: done) — archived at `tasks/login-global-error-position/PLAN.md`; re-author with `add new Task login-global-error-position`
- `moderations-endpoint` (2.x phase: done) — archived at `tasks/moderations-endpoint/PLAN.md`; re-author with `add new Task moderations-endpoint`
- `pgvector-deploy-runbook` (2.x phase: done) — archived at `tasks/pgvector-deploy-runbook/PLAN.md`; re-author with `add new Task pgvector-deploy-runbook`
- `release-provenance` (2.x phase: done) — archived at `tasks/release-provenance/PLAN.md`; re-author with `add new Task release-provenance`
- `responses-api-core` (2.x phase: done) — archived at `tasks/responses-api-core/PLAN.md`; re-author with `add new Task responses-api-core`
- `responses-state-store` (2.x phase: done) — archived at `tasks/responses-state-store/PLAN.md`; re-author with `add new Task responses-state-store`
- `suite-infra-tripwire` (2.x phase: done) — archived at `tasks/suite-infra-tripwire/PLAN.md`; re-author with `add new Task suite-infra-tripwire`
- `suite-stability` (2.x phase: done) — archived at `tasks/suite-stability/PLAN.md`; re-author with `add new Task suite-stability`
- `tenant-usage-costs-api` (2.x phase: done) — archived at `tasks/tenant-usage-costs-api/PLAN.md`; re-author with `add new Task tenant-usage-costs-api`
- `vector-extension-preflight` (2.x phase: done) — archived at `tasks/vector-extension-preflight/PLAN.md`; re-author with `add new Task vector-extension-preflight`
- `vector-store-core` (2.x phase: done) — archived at `tasks/vector-store-core/PLAN.md`; re-author with `add new Task vector-store-core`
- `vector-store-files` (2.x phase: done) — archived at `tasks/vector-store-files/PLAN.md`; re-author with `add new Task vector-store-files`
- `zdr-ingest-lock-heal` (2.x phase: done) — archived at `tasks/zdr-ingest-lock-heal/PLAN.md`; re-author with `add new Task zdr-ingest-lock-heal`

## Archive fidelity

This directory is a faithful, self-contained copy of the 2.x bundle, verified against the last
2.x commit: 980 files byte-identical, 0 missing. Two honest deviations, both recorded here:

- **The engine tooling was restored from git after the fact.** The npm update to 3.2.0
  replaced `.add/tooling/` in place (2.x `add.py` + `add_engine/*` → the ABF-1 library)
  *before* the archive copy ran, so the first copy captured the 3.0 engine by mistake. The 2.x
  engine (`tooling/add.py` + the 13 `add_engine/*` modules) was restored here from the 2.x
  commit, and `.add-version` corrected back to `2.5.0`.
- **A few task test files are stored as resolved content, not symlinks.** Under
  `tasks/plan-rate-enforcement/tests/`, 2.x kept symlinks into `apps/gateway/tests/plan_rate_enforcement/`;
  the archive stores the file *content* they pointed at, so the record stays self-contained and
  frozen even as the live suite evolves.

## Carried todos

The 2.x inline `todos` queue held 123 items. The **78 open** ones were exported to GitHub
issues #121–#198 before the update; the **45 done** ones stay in `state.json` as closed
history. The id → issue-URL map is `TODO-CARRIED.md` in this directory. Issue titles are
prefixed `[add-todo #N]` and each body records the original id, text, and creation date —
because GitHub issue numbers share a sequence with PRs, so an ADD todo id is never its issue
number.

## Next

1. `add status` — see the fresh bundle.
2. `add new milestone <slug>` — recreate the active milestone.
3. `add new Task <slug>` per task above, authoring RULES/ASSUMPTIONS/CHECKS
   from the archived PLAN.md's §1–§4.
4. Freeze, brief, build, gate — the 3.0 loop takes it from there.
