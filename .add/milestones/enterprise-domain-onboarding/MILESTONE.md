# MILESTONE: Enterprise domain onboarding: unified email-domain routing + auto-assign + domain-claims console

goal: A business's users are automatically routed into their company tenant by verified email domain across both signup and SSO login from one source of truth, with self-service domain management in the dashboard, while self-signup into a tenant stays invite-or-verified-domain only
rationale: sub-milestone (intake 2026-07-16, QUEUED behind `account-tiers-billing`) — identity/onboarding
  half of Tin's business-model spec. Recon found the domain primitive already exists (`tenant_domain_claims`,
  DNS-TXT verified, DB-invariant collision guard) and signup-time auto-join works, but the model is
  FRAGMENTED (4 unreconciled domain→tenant surfaces: env oidc_domain_mapping, oidc/saml `email_domains`,
  and `tenant_domain_claims`), SSO login routes by a DIFFERENT surface than signup, there is NO dashboard
  UI (API-only), and the signup response's `joined_existing_tenant` is silently dropped by the BFF. Tin
  decisions (2026-07-16): (a) TWO milestones — this is the onboarding half; (b) trust model = ADMIN
  PRE-VERIFIES the domain (DNS-TXT), NOT zero-touch first-employee provisioning (email domain alone is
  not ownership proof); (c) invite-only remains the enforced default.
stage: production · status: queued · created: 2026-07-16T03:12:06+00:00
release: 0.11.0

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
- Make `tenant_domain_claims` (DNS-TXT verified) the SINGLE source of truth for email-domain→tenant
  routing across BOTH password signup AND OIDC/SAML SSO login; reconcile the 3 other mapping surfaces
  (env `oidc_domain_mapping`, `oidc_provider_configs.email_domains`, `saml_provider_configs.email_domains`)
  so they defer to / are validated against the verified claim (no conflicting mappings).
- Auto-assign a user whose verified email domain owns a tenant into that tenant as `MEMBER` on SSO/first
  login (mirroring the existing signup-time auto-join); wire the `joined_existing_tenant` outcome through
  the dashboard BFF (currently dropped) and adapt the signup form (drop `tenant_name` when joining).
- A dashboard domain-claims console (UDD design loop): create / list / verify (show the DNS-TXT challenge
  + live status) / revoke a domain claim — today API-only — plus the onboarding UX that tells a user
  they joined an existing workspace vs created a new one.
- Preserve invite-only: self-signup into a tenant remains impossible except via an invite or a verified-
  domain match (no regression of `public_signup_enabled=false` default).
Out:
- Account tiers / individual plan / enterprise base fee / payer-of-record — owned by the sibling
  milestone `account-tiers-billing`.
- Zero-touch first-employee auto-provisioning (Tin decided AGAINST it — admin must pre-verify the domain).
- New SSO protocol work (OIDC/SAML/SCIM mechanics themselves are shipped and unchanged) — this milestone
  only unifies the DOMAIN-ROUTING layer on top of them.
- Making `public_signup_enabled` per-tenant (noted as a residual gap; out unless a task surfaces it as
  load-bearing).

> UI/UX in scope? YES — `domain-claims-console` runs the full UDD design-definition loop (design.md):
> IA for a settings surface listing claimed domains + verification state; the interaction pattern for the
> DNS-TXT challenge (copyable record, "verify now", pending/verified/failed states); component states +
> WCAG AA; the onboarding "you joined {tenant}" confirmation. Signature element: a domain-verification
> STATUS chip/seal (pending → verified) reusing the compliance/invoice immutability-marker idiom rather
> than a generic badge — named deliberately, not a default.

## Shared decisions & glossary deltas   (living — every task must honor these)
- **ONE source of truth for domain→tenant.** A verified `tenant_domain_claims` row is authoritative; the
  OIDC/SAML `email_domains` config and env mapping MUST NOT contradict it — they defer to or are validated
  against it. No new fourth mechanism.
- **Auto-assign is MEMBER-only, verified-domain-only.** Login-time auto-join grants `Role.MEMBER`, never
  owner/admin, and fires ONLY on a verified-domain match — never on an unverified domain, never zero-touch.
- **Invite-only is preserved.** Nothing here reopens self-service NEW-tenant creation; the only join paths
  stay invite OR verified-domain match.
- New GLOSSARY terms — **domain claim**: a DNS-TXT-verified assertion that a tenant owns an email domain ·
  **domain auto-join**: MEMBER assignment into the domain-owning tenant on signup/SSO login.

## Shared / risky contracts (freeze these first)
- The unified domain-resolution contract (given an email → which tenant, precedence across the 4 surfaces)
  -> owning task `domain-routing-unification` (consumed by signup, SSO login, and the console).
- The `joined_existing_tenant` end-to-end shape (gateway response → BFF → signup UX)
  -> owning task `domain-auto-assign-login` (consumed by the dashboard).

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] domain-routing-unification   depends-on: none                     — SECURITY (dual-verify — auth
  routing / tenant-confusion). Make verified `tenant_domain_claims` the single source of truth; reconcile
  the 3 other domain-mapping surfaces to defer to it; one precedence-defined resolver for email→tenant.
- [ ] domain-auto-assign-login     depends-on: domain-routing-unification — Auto-assign MEMBER into the
  verified-domain tenant on SSO/first login (mirror signup auto-join); thread `joined_existing_tenant`
  through the BFF; adapt the signup form. Invite-only preserved.
- [ ] domain-claims-console        depends-on: domain-routing-unification — UDD DESIGN LOOP. Dashboard
  settings UI to create/list/verify/revoke domain claims (DNS-TXT challenge + status) + the "joined
  existing workspace" onboarding confirmation.

## Exit criteria (observable; map each to the task that delivers it)
- [x] A verified-domain email auto-joins the owning tenant as MEMBER on BOTH password signup AND SSO login   (← domain-auto-assign-login) — task-1 unified the resolver across signup+OIDC+SAML (158/158, dual opus verify CLEAR); task-2 wired login-time auto-join + the joined signal (189 gw green, dual verify CLEAR)
- [x] Email-domain→tenant routing resolves from ONE source of truth (verified `tenant_domain_claims`); the other 3 surfaces defer to it with no conflicting mapping   (← domain-routing-unification) — `resolve_verified_tenant_for_raw_domain` shared predicate + claims write-gate on PUT /admin/{oidc,saml} (409/422) + backfill migration `e6a1d0f47b29`; precedence verified-claim > per-tenant config > env-mapping
- [x] An unverified / unclaimed domain never auto-joins any tenant (still invite-or-blocked)   (← domain-routing-unification) — pending/expired/revoked/absent claim falls through byte-identically to the invite-only S1 path (fail-closed; dual adversarial verify)
- [x] An admin can create, verify (via the shown DNS-TXT challenge), and revoke a domain claim entirely from the dashboard   (← domain-claims-console) — DomainClaimsSettings console over the frozen /admin/domain-claims API (create/list/verify-DNS-TXT/revoke, owner-403→ErrorState); 94 dash green; UDD wireframe confirmed by Tin (artifact 655ae92f)
- [x] A user who joined an existing workspace sees that outcome in the UI (the BFF no longer drops `joined_existing_tenant`)   (← domain-auto-assign-login + domain-claims-console) — task-2 un-drops `joined_existing_tenant` + `?joined=1` redirect; task-3 JoinedWorkspaceCallout names the tenant from the M6 `/me` `tenant_name` field (gateway→BFF→useCurrentUser→callout, end-to-end verified)
- [x] Self-signup into a tenant remains impossible except via invite or verified-domain match (invite-only preserved)   (← domain-auto-assign-login) — the `public_signup_enabled=false` gate is unchanged and checked before any further IO for every unclaimed domain (task-1 M8 amendment, Tin-confirmed at freeze)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : untouched — no add.py / state.json / template change (product-only milestone)
- skill   : untouched
- book    : untouched
- gateway : `tenants/api` `MeResponse.tenant_name` (additive) + `me()` loads the caller's own tenant name; task-1's `domain_capture` resolver unification + claims write-gate on OIDC/SAML PUT + backfill migration `e6a1d0f47b29`; task-2's `(User, newly_provisioned)` SSO-provision signal
- dashboard : NEW domain-claims console (DomainClaimsSettings + DomainStatusSeal, InvoiceStatusSeal idiom) under a new Settings "Domains" tab; NEW JoinedWorkspaceCallout on the keys landing; SAML dashboard relay (auth/saml/callback|login); signup/login form joined-workspace affordances; `/api/auth/me` relays tenant_name → useCurrentUser

### Cross-task evidence   (one row per task)
- domain-routing-unification : gate=PASS (Tin 2026-07-18, commit `4fd7ff5`) · tests=158/158 across 9 suites · residue=none — SECURITY, dual opus adversarial verify (tamper + routing lenses) both CLEAR; closed a live cross-tenant DoS (OIDC collision + non-deterministic resolver)
- domain-auto-assign-login : gate=PASS (Tin 2026-07-19, commit `086b903`) · tests=189 gw + 35 dash green · residue=none — cross-component; DUAL opus verify (routing + earned-green) both CLEAR; signal is transport-only, cannot influence routing; zero tests/contract weakened
- domain-claims-console : gate=PASS (Tin 2026-07-19) · tests=19 gw (/me-adjacent + tenants + superadmin) + 94 dash green, tsc clean · residue=none — cross-component after the M6 CR; opus adversarial verify: 6/7 attack surfaces CONFIRMED-SAFE, 1 cross-task-drift HARD-STOP (a frozen /me consumer test) found → healed additively → independently re-verified CLEAR; two sanctioned additive-only frozen-test reconciliations (tab list + /me shape), neither weakening

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which) — all 6 exit criteria checked above, each citing its delivering task's PASS row
- goal: *business users auto-routed into their company tenant by verified email domain across signup AND SSO login from ONE source of truth, with self-serve dashboard domain management, invite-or-verified-domain-only preserved.* Proof: task-1 makes verified `tenant_domain_claims` the single authoritative resolver across all entry paths (158/158, dual-verify), task-2 lands login-time MEMBER auto-join + the joined signal (189 gw green), task-3 ships the create/verify/revoke console + names the joined workspace (94 dash green) — the invite-only default is provably unchanged (fail-closed fall-through). All three gate=PASS, no open residue.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] open a PR from the Close ship-review above; the human reviews + merges (domain-routing-unification is security — dual adversarial verify recorded before merge)
- [ ] document the domain-claim admin runbook (how to claim + DNS-TXT verify a domain; how auto-join behaves)
- [ ] capture the domain-claims-console design artifact (the UDD confirmed screen) into the design hand-off
- [ ] tag / publish / deploy (human-run, per release.md) — bundle with `account-tiers-billing` into the next release cut
