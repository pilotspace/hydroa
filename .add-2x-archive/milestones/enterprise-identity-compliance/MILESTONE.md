# MILESTONE: Enterprise Identity & Compliance Pack

goal: An enterprise tenant can provision users via SCIM, sign in via SAML or OIDC, capture its email domain, set its own retention policy including a Zero-Data-Retention mode, and export its audit trail through a compliance API.
rationale: new-major — Track A of the Tin-approved 2026-07-10 Claude-vs-Hydroa competitive gap analysis (all four tracks approved; A recommended first as procurement-checkbox items). No active milestone covers enterprise identity lifecycle or per-tenant compliance posture: today Hydroa has OIDC SSO + invites but NO SCIM, NO SAML, NO domain capture; retention is one operator-wide sweeper (no per-tenant policy, no ZDR); the immutable audit store has an admin read view but no compliance-grade export.
stage: production · status: active · created: 2026-07-10T12:17:07+00:00
release: 0.8.0

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  (1) SCIM 2.0 provisioning — per-tenant SCIM bearer token, /scim/v2 Users (create/update/deactivate; Groups→teams mapping decided at design) driving the existing tenant-user lifecycle; (2) SAML 2.0 SSO alongside the existing OIDC (per-tenant IdP metadata config, same session-JWT issuance, the existing tenant-confusion defenses extended to SAML); (3) domain capture — a VERIFIED email domain claims signups/joins for a tenant (must compose with the S1 invite-only-by-default signup: design decides the precedence and freezes it); (4) per-tenant retention policy over the existing operator-wide sweeper, incl. a ZDR mode (metadata-only metering: no payload persistence anywhere — overrides the sibling milestone's capture opt-in, fail-closed); (5) a compliance export API over the immutable audit store (time/actor-filtered, cursor-paginated, deterministic ordering for external archival).
Out: HIPAA/SOC2 certification work itself (process, not code) · billing/invoicing (Track B) · data-residency routing (Track C) · MCP/agent governance (Track D) · SCIM Groups beyond a basic team mapping if design finds it heavy (defer as a delta) · IdP-initiated SAML flows if security review prefers SP-initiated-only (design decides).

UI/UX in scope (admin settings surfaces only — SCIM token management, SAML IdP config, domain verification, retention policy editor): extend the existing dashboard settings IA and Aurora components; no new page archetype. WCAG 2.2 AA floor.

## Shared decisions & glossary deltas   (living — every task must honor these)
- **All five surfaces are tenant-scoped config on EXISTING primitives** — SCIM drives the existing user lifecycle, SAML issues the existing session JWT, retention extends the existing sweeper, export reads the existing audit store. No parallel identity or audit stores.
- **Every identity surface is security-sensitive: HARD-STOP verify** — SCIM (unattended write path into user lifecycle), SAML (assertion validation — signature, audience, replay, tenant confusion), domain capture (account-takeover surface: verification via DNS TXT or equivalent proof, never email-match alone). Never auto-passed even under autonomy: auto.
- **ZDR is fail-closed and global within the tenant** — a ZDR tenant produces metadata-only usage records and zero payload rows across ALL stores (request logs, artifacts inline previews excluded? design maps every payload-bearing store and freezes the list). Billing exactness is preserved (token counts are metadata).
- **Domain capture composes with invite-only signup (S1)** — the frozen S1 default (invite-only) stays the default; a verified captured domain is an explicit tenant-admin opt-in that relaxes it for that domain only. Record as an S1-compatible extension, not a supersession, unless design proves otherwise.
- **Compliance export never mutates** — read-only over the append-only audit store; export access is itself audited.

## Shared / risky contracts (freeze these first)
- SCIM resource mapping (SCIM User/Group ⇄ tenant user/team + deactivate semantics) -> owning task `scim-provisioning`
- SAML assertion-validation + tenant-resolution contract -> owning task `saml-sso`
- domain-verification proof + signup-precedence contract (vs S1 invite-only) -> owning task `domain-capture`
- per-tenant retention/ZDR policy shape + the payload-bearing-store inventory -> owning task `tenant-retention-zdr`
- compliance export envelope (filters, cursor, ordering, audit-of-export) -> owning task `compliance-export-api`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] scim-provisioning       depends-on: none               — SCIM 2.0 Users endpoints + per-tenant SCIM token; create/update/deactivate drives existing user lifecycle. (security; HARD-STOP verify)
- [ ] saml-sso                depends-on: none               — SAML 2.0 SP alongside OIDC: per-tenant IdP config, assertion validation, session-JWT issuance, tenant-confusion defenses. (security; HARD-STOP verify)
- [ ] domain-capture          depends-on: saml-sso           — verified email-domain claim routes signup/join to the tenant; composes with S1 invite-only default. (security; HARD-STOP verify; serialized after saml-sso — shared tenant-SSO config surface)
- [ ] tenant-retention-zdr    depends-on: none               — per-tenant retention policy + ZDR mode over the existing sweeper; payload-store inventory + fail-closed override hooks. (data)
- [ ] compliance-export-api   depends-on: none               — read-only, cursor-paginated, filtered export over the immutable audit store; export access audited. (data)

## Exit criteria (observable; map each to the task that delivers it)
- [x] An IdP can create, update, and deactivate a tenant user via SCIM with the tenant's SCIM token; a deactivated user's sessions/keys stop authenticating; another tenant's SCIM token cannot touch it   (← scim-provisioning)
- [x] A user of a SAML-configured tenant signs in via their IdP and receives the same session JWT as OIDC/password users; a forged/replayed/cross-tenant assertion is rejected   (← saml-sso)
- [x] A tenant admin proves domain ownership; a new signup on that verified domain lands in that tenant per the frozen precedence; an unverified domain changes nothing   (← domain-capture)
- [x] A tenant admin sets a retention window and the sweeper honors it per-tenant; a ZDR tenant produces zero payload rows in every inventoried store while billing stays exact   (← tenant-retention-zdr)
- [x] A compliance officer exports the tenant's audit trail filtered by time/actor with stable cursor pagination; the export itself appears in the audit log   (← compliance-export-api)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway (identity/tenants/retention/audit) : NEW SCIM 2.0 `/scim/v2/Users` (per-tenant bearer token, create/update/deactivate → existing user lifecycle; cross-tenant token rejected); NEW SAML 2.0 SP alongside OIDC (per-tenant IdP config, assertion signature/audience/replay/tenant-confusion validation, same session-JWT issuance); NEW `tenant_domain_claims` (DNS-TXT proof at `_ai-proxy-challenge.<domain>`, partial-unique `WHERE status='verified'` structural collision guard, signup routing composes with S1 invite-only default); per-tenant retention window + ZDR fail-closed mode over the existing sweeper (payload-store inventory frozen); read-only cursor-paginated compliance export over the append-only audit store (export itself audited).
- dashboard : 3 NEW /settings tabs — SCIM token mgmt, SAML IdP config, Retention & ZDR editor; Aurora + WCAG 2.2 AA (ZDR destructive-confirm hardened at verify).
- tooling : untouched. skill : untouched. book : untouched.

### Cross-task evidence   (one row per task)
- scim-provisioning       : gate=PASS (security HARD-STOP) · deactivate stops auth, cross-tenant token rejected · residue=none
- saml-sso                : gate=PASS (security HARD-STOP) · forged/replayed/cross-tenant assertion rejected · residue=none
- domain-capture          : gate=PASS (security HARD-STOP, Tin-gated) · concurrent-verify race + normalization-bypass + account-hijack-via-join all held live · residue=note (real-DNS-adapter suite coverage gap — hand-verified correct, deferred test)
- tenant-retention-zdr    : gate=PASS · per-tenant window honored, ZDR zero-payload + exact billing · residue=none
- compliance-export-api   : gate=PASS · stable cursor, time/actor filter, export-of-export audited · residue=none
- enterprise-identity-admin-ui : gate=PASS · 44 suite · residue=none (verify MAJOR — ZDR switch-race — fixed + red-verified, reveal-once/enumeration held)

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: enterprise identity + compliance on existing primitives — SCIM drives the user lifecycle, SAML issues the existing session JWT, domain-capture routes signups (S1-composed), per-tenant retention/ZDR extends the sweeper fail-closed, compliance export reads the immutable audit store; all 6 tasks gate=PASS (3 security HARD-STOPs Tin-approved), no parallel identity/audit stores introduced.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] Open one integrated PR (or a stacked series) from the Close ship-review; Tin reviews + merges (HTTPS push via gh; org-billing CI block => local-suite evidence + admin-merge)
- [ ] Full local suite green on the integrated branch (nohup + Monitor for the full run)
- [ ] FEATURES.md + docs/runbooks updated (SCIM/SAML/domain-capture/retention/compliance-export) before close
- [ ] Bundle into the next release cut (release.md) with milestone attribution; Tin tags / deploys
