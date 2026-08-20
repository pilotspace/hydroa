---
type: Milestone
title: Release hardening — close the 2026-08-18 deep-review P0s + Keycloak external IdP
status: direction
depth: deep
generated: { by: add/3.2.0, at: 2026-08-18 }
verified:
  - { by: "Tin Dang", at: 2026-08-18, act: freeze, authority: human, direction: "sha256:75a11da44c802486" }
---
## CARD
goal: every P0 security/correctness finding from the 2026-08-18 whole-product deep review is closed with evidence, and Keycloak is adopted as the documented external IdP through the EXISTING per-tenant OIDC seam — no auth rearchitecture, no de-tenanting
why: R9 lead, Tin-directed 2026-08-18 (AskUserQuestion: "proactive to fix all P0 issues and make Keycloak as external IdP now"; the full-Keycloak-migration and drop-multi-tenancy options were explicitly REJECTED — tenant_id spans 299/532 source files and 436/459 test suites, and the per-tenant billing spine IS the product). The seven-agent depth review (artifact 6816985f) found the engine deep but eight P0 holes that are auditor- or customer-visible: an unprotected login endpoint, a ZDR promise breached on three data-bearing stores, the twice-HARD-STOPPED global-breaker defect class alive in the core router, a fail-open money gate framed as fail-closed, an unbounded upload, silent vision corruption, and unaudited mutation surfaces. All are depth-completion of existing features — exactly the "wide not deep" remediation Tin asked for. Runs alongside R8 soc2-groundwork (whose frozen scope these findings do NOT edit); everything here strengthens the same audit posture.
next: add freeze release-hardening-p0

## SCOPE
In:  (1) **Auth hardening** on Hydroa's own credential path (CC6): rate-limit + lockout on
     `/admin/auth/login` (the only unprotected public auth endpoint), a password-reset flow over the
     existing email module, and session revocation (jti + denylist checked at both the in-process
     and Envoy ext_authz seams) — these stay necessary regardless of IdP because Hydroa mints its
     own session JWT even for SSO users. (2) **Keycloak as external IdP**: an optional, documented
     Keycloak deployment (compose + Helm subchart, off by default) wired through the EXISTING
     per-tenant OIDC SSO seam; a walked runbook + e2e proof (login via Keycloak → Hydroa session);
     guidance steering tenants to IdP-managed MFA/reset/lockout. Explicitly NOT a migration: Hydroa
     session JWTs, Envoy config, API keys, RBAC, SCIM, device-OAuth all unchanged. (3) **ZDR/retention
     inventory extension**: vector_store_*, eval-run payloads, finetune job/event data join the
     sweeper + ZDR purge, plus a structural guard so a future payload-bearing table cannot ship
     outside the inventory. (4) **Tenant-scoped breaker + cooldown** in the core router (the
     per-tenant registry pattern already proven in moderation/finetune). (5) **Credits gate
     fail-closed** — or an explicit, alarmed, console-visible fail-open with honest labeling; Tin
     decides at task freeze. (6) **Upload bounds**: audio STT/translation size cap before read, plus
     a sweep for any other unbounded multipart read. (7) **Vision fidelity**: Bedrock multimodal
     honest handling (Converse supports image blocks — translate, or cleanly reject + catalog-correct)
     and `/v1/messages` Anthropic image-block passthrough. (8) **Audit-coverage structural guard**:
     a route-walking test asserting every mutating endpoint emits an audit event (explicit exemption
     list), and retrofit of the silent modules (evals, vector_stores, finetune, memory, conversations).
Out: **Full Keycloak migration** (replacing Hydroa password/session/SCIM with Keycloak realms) —
     rejected for now; revisit post-attestation. **Dropping multi-tenancy** — rejected outright.
     **MFA built into Hydroa** — delegated to the IdP path this milestone documents. **The P1s**
     (Stripe webhooks, audit-export console wiring, checkout expiry) and **P2 surface completion**
     (finetune/vector-store/files consoles, batch cancel, video provider) — next milestone. **R8's
     frozen scope** (independent-review recruit, pentest, SLO runbooks, security-debt #27/#31/#51,
     readiness assessment, SOC2 tool live sources) — stays in R8, unedited.

## GROUND
touches:
  - `apps/gateway/src/gateway/tenants/api/router.py` (login), `tenants/infrastructure/jwt_service.py`, `email/` — auth hardening.
  - `apps/gateway/src/gateway/auth/` (OIDC seam — read-mostly), `infra/` + `charts/ai-proxy/` (optional Keycloak component), `docs/runbooks/` — Keycloak IdP.
  - `apps/gateway/src/gateway/usage/application/retention_sweep.py` + vector_stores/evals/finetune ORM — ZDR inventory (⚠ four-manifest lesson: [[gateway-new-table-four-manifests]]).
  - `apps/gateway/src/gateway/proxy/api/deps.py`, `proxy/infrastructure/redis_cooldown_gate.py` — tenant-scoped breaker ([[per-tenant-breaker-recurring-defect]]).
  - `apps/gateway/src/gateway/credits/infrastructure/postgres_guard.py` + console — credits gate.
  - `apps/gateway/src/gateway/proxy/application/audio_use_case.py` — upload bounds.
  - `proxy/infrastructure/bedrock_upstream.py`, `anthropic_ingress.py`, catalog seed — vision fidelity.
  - `apps/gateway/src/gateway/audit/` + the five silent modules — audit guard.
anchors: the existing per-tenant OIDC SSO path is hardened and tested (~6,100 lines of SSO tests) — Keycloak rides it, never forks it; the per-tenant breaker registry pattern (bounded LRU) exists in ml_moderation_evaluator.py and finetune/openai_client.py — reuse, don't reinvent; every guard added here must be RED against its motivating defect ([[guard-must-be-red-against-its-motivating-tree]]).
risks:
  - **The Keycloak task drifts into a migration.** The moment a change touches Hydroa's session JWT shape, Envoy jwt_authn, or the password store, it has left scope — that is the rejected Option C. The task's Reject rules must fence this.
  - **ZDR extension meets the TOCTOU class again** ([[zdr-toctou-async-write-paths]] — HARD-STOPPED twice): the eval-run executor and vector-store workers persist after awaits; the purge/deny check must be atomic with the write, tested with a slow double.
  - **Fail-closed credits could brick paying tenants on a Postgres blip.** The fail-closed decision needs a bounded degrade story (grace window, alert, breaker) — not a naive hard gate; Tin owns this call at freeze.
  - **The audit route-walker becomes ceremony.** It must fail RED on today's tree (evals/vector_stores unaudited) before the retrofit lands, or it proves nothing.
  - **Tenant-scoping the breaker changes availability semantics under load** — needs the delay-injection repro discipline ([[reproduce-load-flakes-by-delay-injection]]), not re-run-until-green.
security: every task here is sensitivity security or data — the freeze floor is human/plan throughout; findings are HARD-STOP, never waived to hit the milestone.

## EXIT
- [ ] `/admin/auth/login` is rate-limited + lockout-protected, a password-reset flow works end-to-end, and a revoked session stops authenticating at BOTH auth seams   (← auth-hardening-login-sessions)   (verify: brute-force probe refused with 429 before credential check; reset e2e via the email store; a revoked jti is refused in-process AND at ext_authz; anti-enumeration byte-identity preserved)
- [ ] A tenant can authenticate to Hydroa through Keycloak via the existing OIDC seam, from a documented, walked runbook, with MFA enforced IdP-side — and zero diffs to jwt_service, Envoy config, or the password store   (← keycloak-external-idp)   (verify: e2e login through a real Keycloak container → Hydroa session JWT; runbook walked from scratch; `git diff` proves the fence held)
- [ ] A ZDR tenant's vector-store, eval-run, and finetune payloads are purged by the sweeper, and a structural guard fails on any payload-bearing table absent from the inventory   (← zdr-retention-inventory-extension)   (verify: seed → enable ZDR → sweep → zero payload rows across all three stores; the guard is RED on the pre-fix tree)
- [ ] The core router's breaker and cooldown are tenant-scoped: tenant A's failures never open tenant B's path   (← tenant-scoped-breaker-cooldown)   (verify: adversarial two-tenant probe — A trips, B dials; bounded registry; existing provider-isolation tests stay green)
- [ ] The credits spend gate's failure posture matches its documentation and is observable: fail-closed with a bounded degrade, or explicitly-labeled fail-open with a firing alert + console surface   (← credits-gate-fail-closed)   (verify: Postgres-down drill produces the decided behavior + a visible alert; docs and code agree)
- [ ] No proxy entry point reads an unbounded upload: audio is capped pre-read with a structured 413, and a sweep shows every multipart read is bounded   (← upload-bounds-audio)   (verify: oversized STT upload refused before governance/billing; grep-audit of every `.read(` on request bodies recorded in the task)
- [ ] A vision request to a Bedrock catalog-vision model and an image block on `/v1/messages` are either honestly translated or cleanly refused — never silently mangled or dropped   (← vision-fidelity-bedrock-messages)   (verify: image round-trip test per path, or a structured 4xx + corrected catalog modalities; no `str(content)` stringification survives)
- [ ] Every mutating gateway route emits an audit event or sits on an explicit exemption list enforced by a route-walking guard; the five silent modules are retrofitted   (← audit-coverage-structural-guard)   (verify: the walker is RED on the pre-retrofit tree, names its victims, and goes green only after evals/vector_stores/finetune/memory/conversations audit)

## CLOSE
evidence: <one row per task at ship — gate · tests/evidence · residue>
sequencing: security-first, breadth-first. `auth-hardening-login-sessions` and `upload-bounds-audio` lead (smallest blast radius, highest exposure). `zdr-retention-inventory-extension` ∥ `audit-coverage-structural-guard` (both are inventory+guard work over disjoint modules). `tenant-scoped-breaker-cooldown` and `vision-fidelity-bedrock-messages` run mid (router/adapter seams, need the suite stable). `credits-gate-fail-closed` waits for Tin's posture decision at its freeze. `keycloak-external-idp` LAST of the eight — it documents the auth story the hardening task finishes, and its runbook should describe the hardened endpoint, not race it. Rejected: Keycloak-first (would document an unhardened login as the fallback path); one mega-task (eight seams, eight blast radii — atomicity would be theater).
