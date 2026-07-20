# Releases

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

