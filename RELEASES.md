# Releases

<!-- RELEASE CHECKLIST (added 0.14.0 — todo #110). Nothing enforces this list; it exists because
     the Helm chart drifted NINE releases and two tags went missing entirely.
       1. apps/gateway/pyproject.toml  version
       1b. apps/gateway/src/gateway/__init__.py  _FALLBACK_VERSION  (the no-metadata fallback;
           `tests/release_provenance` FAILS if it disagrees with pyproject — it caught this
           very step being missed during the 0.14.0 cut, which is the guard working)
       2. charts/ai-proxy/Chart.yaml   version + appVersion
       3. charts/ai-proxy/values.yaml  image.tag AND dashboard.image.tag
       4. charts/ai-proxy/values-prod.yaml  both `-prod` overrides
       5. CHANGELOG.md row   (0.13.0 shipped without one — do not repeat)
       6. RELEASES.md row    (0.13.0's was added retroactively — do not repeat)
       7. git tag -a vX.Y.Z on the release commit   (v0.9.0 and v0.10.0 were missing until 0.14.0)
       8. build + push images FROM THE TAG, per docs/runbooks/cloud-deploy.md -->

## 0.14.1 — 2026-08-11 — Reproducible release artifacts
milestones: none (patch)
loose tasks: todo #113 (digest-pin production images) + todo #98 (pin the deployed Python), PRs #108 and #109
waivers: none new. 0.14.0's three carry forward unchanged — ci-restoration (DISCHARGED 2026-08-11), suite-stability (expires 2026-09-30), pgvector-deploy-runbook (expires 2026-09-30; the M3 operator walkthrough on a REAL target is still not done).
actor: Tin Dang <tindang.ht97@gmail.com> (git)
evidence: Patch cut because **`v0.14.0` was already published at `b194b24` and predates all three pin commits** (`834b0c9`, `8bd3f3c`, `292e70c`). `docs/runbooks/cloud-deploy.md` builds FROM THE TAG, so deploying `v0.14.0` would have shipped the unpinned Postgres image and the unpinned interpreter — the exact defects this work closes, in the release themed "release integrity". Moving a published tag was rejected: it silently changes what a fetched ref means. 0.14.0 made the substrate attestable; it did not make the artifact reproducible.
  Production images are digest-pinned. The **Postgres pin is a data-loss control**: `pg16` is a floating MINOR, an unattended `docker compose pull` can swap the libc base under a live volume, and a glibc↔musl change alters the cluster's collation version. Both halves were confirmed empirically on 2026-08-10 — indexes corrupt SILENTLY, and the documented same-volume remedy CANNOT finish (`REFRESH COLLATION VERSION` errors `invalid collation version change`), so recovery is dump/restore. Envoy's `v1.29-latest` floats BY NAME on the component terminating TLS and enforcing `ext_authz`. `${GATEWAY_IMAGE}` stays parameterised deliberately — it is supplied from the release tag at deploy time.
  The gateway image ran Python **3.12.12** while CI and dev ran **3.12.13**, so production executed an interpreter no gate ever had. It lands on a security control: `is_reserved("::ffff:10.20.30.40")` is True on 3.12.3 and False on 3.12.4+ (CVE-2024-4032 / gh-113171), and `core/egress_policy.py` reads it. **NOT a live hole** — both interpreters were run directly and agree on all five egress-relevant predicates; this was reproducibility, fixed before it became security. Verified by a real `docker build`, plus a runtime assertion that the shipped image runs as uid 1000 on `sys.version` 3.12.13.
  ⚠ Durable gotcha found here: **uv CANNOT install 3.12.13.** It provisions only what python-build-standalone publishes (newest 3.12 = 3.12.12), so `uv python install` fails outright — while the official `python:3.12.13-slim-bookworm` exists. The image now bases on that, digest-pinned, with `uv` copied in as a static binary. Check `uv python list --all-versions` before assuming a CPython patch is uv-reachable.
  Both pins carry standing guards, each red-checked against the tree that motivated it. The pre-existing CI parity guard was also found to be **half-blind** — it indexed one hard-coded step and so checked one of the two `python-version` pins `ci.yml` carries; it now sweeps every step, keyed on the input rather than the action name (the pin has already moved once, from `actions/setup-python` to `astral-sh/setup-uv`). Its vacuity assertion caught that very mistake mid-change.
  ⚠ Unchanged from 0.14.0 and still open: `required_approving_review_count` is **0**. Both PRs in this cut landed on genuinely green required checks with no admin bypass, but neither had four-eyes review. Carried to R8 `soc2-groundwork`.

## 0.14.0 — 2026-08-11 — Release integrity
milestones: release-integrity
loose tasks: dashboard-lint-gate + masked-gate sweep (PR #103, unscaffolded)
waivers: ci-restoration — RISK-ACCEPTED, owner Tin Dang, expires 2026-08-15 and DISCHARGED 2026-08-11 ahead of it: all three conditions met — todo #68 (the four #89 deps allowlisted WITH written justification; the justification text was verified, not just the green gate) and todo #69 (lint-type-debt-sweep promoted, gated PASS) are closed, and exit #1 is met (branch protection enforces required checks with `enforce_admins: true`) · suite-stability — RISK-ACCEPTED, owner Tin Dang, expires 2026-09-30 (todo #81 unreproduced catalog_refresh_scheduler stall, todo #80 azure egress DNS) · pgvector-deploy-runbook — RISK-ACCEPTED, owner Tin Dang, expires 2026-09-30 (the M3 operator walkthrough on a REAL target is still not done; the 2026-08-10 rehearsal was Docker-on-a-musl-volume, which is not a production dry-run)
actor: Tin Dang <tindang.ht97@gmail.com> (git)
evidence: R6 "release integrity" — 12/12 tasks gated (9 PASS, 3 RISK-ACCEPTED), 6/6 exit criteria. Delivers NO product features by design; it makes the delivery substrate attestable.
  THE headline: **main is protected and admin-merge is over.** Required `ci` + `dashboard` checks with `enforce_admins: true` (`ci` is a single aggregating job — a shard matrix renames `gateway` to `gateway (1)`…`gateway (N)`, and a required-context list re-edited on every shard-count change is one that will eventually be wrong in the direction where a shard's failure blocks nothing), ending an era that ran 7+ releases in which every merge since 0.8.0 landed by admin-merge on locally-run pytest — the single hardest thing to defend under SOC 2 CC8.1, because you cannot evidence that tests ran on the merged artifact. The blocker had been misdiagnosed as org billing for months; it was actually PLAN + VISIBILITY (protection on a private repo needs a paid plan), resolved by making the repo public after a full-history secret scan of all 1221 commits came back clean. Enforcement is evidenced by a REFUSED admin merge (`HTTP 405` on `PUT /pulls/102/merge`), not by reading config back — and `git push --dry-run` was explicitly rejected as evidence, since it never sends the ref update.
  Attestation firsts: the gateway CI job reached a verdict for the first time ever, main went green in CI for the first time ever (run 31457920121, gateway 15 steps / 1h06m44s), and PR #103 was the first merge in 7+ releases to land on genuinely green required checks with no admin bypass. Five consecutive merges then landed through the gate with no bypass (#103, #104, #102, #105, #106).
  Speed, because a gate nobody waits for is a gate that gets bypassed: **65-82 min -> 13m41s**, measured (run 31468024262). The suite is a 4-way shard matrix, each shard on its own runner with its own Postgres and Redis. More xdist workers on ONE box was a measured dead end (4 vCPUs shared with the service containers, a ~1.92x coverage multiplier, one contended database); more BOXES was the lever. The shard count is then set by the CONCURRENCY CAP and not the suite size — the Free plan allows 5 concurrent jobs and `dashboard` holds one, so past 4 shards a job queues behind a shard and the pipeline gets SLOWER. Free is not unlimited. The 80% coverage gate moved to a job that combines the per-shard data (91% on 31348 statements) rather than being weakened, and `timeout-minutes` was re-derived from 120 to 20 against a measured 10m29s shard.
  Determinism: the flake tail closed with 3 consecutive green full-suite runs at `-n 12 --dist loadscope`, no `--reruns` (824s/694s/332s, 4570 passed each), backed by 8 standing AST guards. The migration parity gate moved into `make ci`, which immediately exposed 16 unregistered ORM modules covering 24 tables that `alembic check` wanted to DROP — invisible for months because the gateway job died at the test step and never reached the gate behind it.
  Deploy: a fail-closed pgvector boot preflight, and the collation runbook WALKED end to end — which proved the documented §4a (`REINDEX` + `REFRESH COLLATION VERSION`) *cannot finish* on the musl case, so dump/restore is required. The chart's `appVersion` and image tags, drifted nine releases behind at `0.4.0`, are bumped here with a written checklist (todo #110), and `cloud-deploy.md` no longer tells operators to tag the image to match the file.
  Also shipped: lint/type debt cleared; a masked-gate sweep that found the dashboard ESLint gate had been dark since the Next 16 upgrade (red with 5 errors when first run, 4 of them one real ref-during-render defect), the 1128-line Envoy edge suite reachable from no make target, and `kind-e2e` at 0 green in 30 attempts (todo #109). Backfilled the missing `v0.9.0` and `v0.10.0` tags and the missing 0.13.0 CHANGELOG entry.
  ⚠ OPEN and deliberately not waived: `required_approving_review_count` is **0**, because GitHub forbids self-approval and any higher value deadlocks a solo-maintainer repo. This release evidences that tests ran green on the merged artifact; it does NOT evidence four-eyes review. That needs a second human with write access — carried to R8 `soc2-groundwork`. Residual flake population in todos #105/#111.

## 0.13.0 — 2026-07-25
milestones: managed-rag-finetune
loose tasks: none
waivers: pgvector-deploy-runbook — RISK-ACCEPTED @ v3, owner Tin Dang, expires 2026-09-30 (nobody has WALKED the runbook on a real target; 0.13.0 stays undeployable to an existing volume until someone does)
actor: Tin Dang <tindang.ht97@gmail.com> (git)
evidence: R5 "managed RAG + fine-tune BYOK brokering" (6/6 tasks gated, PR #89 merged to main `4a351bd`). pgvector-backed managed RAG (vector stores, files, chunking, embeddings cache) + fine-tune job brokering on tenant-supplied provider keys. PR review found a THIRD ZDR TOCTOU (HARD-STOP) — healed in-branch `3e041e0`, dual adversarial refute NOT-REFUTED, and all three `FOR UPDATE` copies collapsed onto one shared primitive. 6 medium findings deferred to R7 (todos #59–#65). Recorded retroactively by task `release-provenance` on 2026-08-07: this release shipped without a RELEASES.md row, which is the gap that task exists to close.

## 0.12.0 — 2026-07-24
milestones: api-surface-parity
loose tasks: none
waivers: none
actor: Tin Dang <tindang.ht97@gmail.com> (git)
evidence: R4 "OpenAI API-surface parity" (6/6 tasks gated PASS, PR #87 merged to main `71e55c3`). New surfaces: /v1/responses (+ stored responses/chaining), /v1/files + batches input_file_id, /v1/moderations, /v1/images/edits+variations, tenant usage/costs read API. Independent adversarial refute-reads caught+healed 4 real defects (split-usage-frame billing, files body-cap 413, timestamp-overflow 422, moderations shared-breaker→CR-1 per-tenant); responses-state-store dual security verify CLEAR. Pre-merge suite: 6 new suites 122/122 + adjacent regressions 201/201 + guardrails 36/36 serial (xdist collision parallel-only). Admin-merged past org-billing 0-step CI on local evidence (precedent: 0.10.0/0.11.0). Tag/publish/deploy human-run.

## 0.11.0 — 2026-07-20
milestones: account-tiers-billing, enterprise-domain-onboarding, domain-onboarding-softening
loose tasks: none
waivers: none
actor: Tin Dang <tindang.ht97@gmail.com> (git)
evidence: recorded by add.py release

## 0.10.0 — 2026-07-18
milestones: platform-admin-console, tenant-impersonation, platform-access-plan, team-member-invite, platform-key-default, model-catalog-db, dashboard-hallmark-restyle, commercial-self-serve
loose tasks: none
waivers: none
actor: Tin Dang <tindang.ht97@gmail.com> (git)
evidence: 8 milestones/~30 tasks gated PASS, all merged to main (PRs #76 #77 #79 + earlier); full bundle suite green: gateway ~4031, dashboard 1505 (13 pre-existing Airier design-token failures ride as disclosed backlog); 2 security milestones dual/triple adversarially verified (device x2, checkout x3) all EARNED/CLEAR; 0 HARD-STOP blockers, 0 waivers; 20 open SPEC deltas ride as disclosed backlog (precedent: 0.8.0 rode 15, 0.7.0 ~220); disclosed fail-closed follow-up: checkout idempotency-namespace M3 contract-CR. Admin-merged past org-billing 0-step CI on local evidence.

## 0.9.0 — 2026-07-14
milestones: agent-gateway-v1, eu-ai-act-readiness
loose tasks: none
waivers: none
actor: Tin Dang <tindang.ht97@gmail.com> (git)
evidence: 9 tasks/2 milestones (agent-gateway-v1 + eu-ai-act-readiness); BE 3802 pass (5 R1-drift fixed + routing_admin xdist flakes green in isolation), dashboard 147 files/1394 pass; 2 security tasks dual-verified + all reproduced defects healed (MCP PII 3-round output-invariant close, kill-switch fail-closed, ZDR TOCTOU CR); 0 HARD-STOP blockers; forceable spec-deltas ride as disclosed follow-ups

## 0.8.0 — 2026-07-13 — Commercial platform
milestones: gpt-realtime-pricing, platform-identity, v57, platform-console-flat-redesign, enterprise-hardening, logs-explorer-guardrails-v2, enterprise-identity-compliance, monetization-core, residency-service-tiers
loose tasks: role-update-persistence-fix, batch-observability-scaffolding, declare-components-registry
waivers: none
notes: gpt-realtime-pricing credited WITH caveat — billing math unit/adversarially-tested but NOT live-verified against real OpenAI Realtime infra (no live cred available); code merged/running, not feature-flagged. 15 bundle SPEC-deltas rode in via --force (forceable floor; 0 security/blockers). Merge evidence: full BE 3556 passed @ 90.94% cov + dashboard 1276/1276; residency-service-tiers via PR #69 (merge 14d7bd9), admin-merged past org-billing 0-step CI. Also folds in a dev-experience chore (PR #70, merge da9551f): `make test-parallel` — pytest-xdist per-worker Postgres/Redis isolation, ~26 min → ~5.5 min (~4.6×), 3566 passed @ 90.95% cov, serial `make test` unchanged.
actor: Tin Dang <tindang.ht97@gmail.com> (git)
evidence: recorded by add.py release

## 0.7.0 — 2026-07-02
milestones: v55, gateway-health, catalog-pricing-detail, minimax-provider, openrouter-embeddings, v56
loose tasks: stream-alias-billing
waivers: none
actor: Tin Dang <tindang.ht97@gmail.com> (git)
evidence: 6 milestones + 1 loose task gated PASS, all merged to main; 0 blockers/0 waivers; gpt-realtime-pricing held back by request (billing math not live-verified against real OpenAI infra — code merged but not credited to this release); ~220 pre-existing SPEC deltas ride as documented backlog (precedented: 0.6.0 shipped with 35)

## 0.6.0 — 2026-06-30
milestones: v54, voice-playground, memory-playground, artifacts-playground, vision-playground, video-playground, proxy-correctness, chat-playground
loose tasks: none
waivers: none
actor: Tin Dang <tindang.ht97@gmail.com> (git)
evidence: Console-grade AI-feature playgrounds (voice·memory·artifacts·vision·video) + chat-playground + v54 UI refinement + proxy adapter docs-faithfulness; dashboard 902/0, gateway +17 adapter tests, 0 blockers/0 waivers; 35 SPEC deltas ride as documented backlog

## 0.5.0 — 2026-06-27
milestones: v51, v52, v53
loose tasks: none
waivers: none
actor: Tin Dang <tindang.ht97@gmail.com> (git)
evidence: 3 milestones (v51-v53) gated PASS; v53 live make ci-e2e green (kind 10/10 Ready -> API e2e 9 + UI e2e 3 through the edge), 85 helm+kind + 7 ci_pipeline tests green; pre-merge security review MERGE-WITH-NITS no-blockers (0.96); 0 blockers / 0 waivers; foundation v39; pre-publish on branch chore/release-0.5.0

## 0.4.0 — 2026-06-26
milestones: v40, v41, v42, v43, v44, v45, v46, v47, v48, v49, ui-fidelity, v50
waivers: none
evidence: gateway: migration 6/6 on real DB, test-fast 228, merged-module routers 174; dashboard vitest 688/688 + tsc 0 + eslint 0; 0 blockers / 0 waivers

## 0.3.0 — 2026-06-25
milestones: v33, v34, v35, v36, v37, v38, v39
waivers: none
evidence: 7 milestones (v33-v39) gated PASS; gateway suite 1730 green @88.14% at v39 close; v39 live double-pass 13/13 x2 (run_id 1782402015/1782402020); 0 blockers/waivers; main HEAD 0ce2f8a.

## 0.2.0 — 2026-06-23
milestones: v31, v30, v32
waivers: none
evidence: 3 milestones (v30, v31, v32) gated PASS; suite green (gateway 1368 / dashboard 384); independent pre-merge review MERGE no-blockers; main HEAD 1f7ba73.

## 0.1.0 — 2026-06-18
milestones: v1, v2, v3, v4, v5, v6, v7, v8, v9, v10, v11, v12, v13, v15, v14, v17, v18, v19, v20, v21, v22, v23, v24, v25, v26, v27, v28, v29
waivers: none
evidence: 28 milestones gated PASS; suite 1214 green at v29 close; pure release cut, no new code. main HEAD 76fb207d.

