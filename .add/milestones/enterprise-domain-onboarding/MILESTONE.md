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
release: pending

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
- [ ] A verified-domain email auto-joins the owning tenant as MEMBER on BOTH password signup AND SSO login   (← domain-auto-assign-login)
- [ ] Email-domain→tenant routing resolves from ONE source of truth (verified `tenant_domain_claims`); the other 3 surfaces defer to it with no conflicting mapping   (← domain-routing-unification)
- [ ] An unverified / unclaimed domain never auto-joins any tenant (still invite-or-blocked)   (← domain-routing-unification)
- [ ] An admin can create, verify (via the shown DNS-TXT challenge), and revoke a domain claim entirely from the dashboard   (← domain-claims-console)
- [ ] A user who joined an existing workspace sees that outcome in the UI (the BFF no longer drops `joined_existing_tenant`)   (← domain-auto-assign-login + domain-claims-console)
- [ ] Self-signup into a tenant remains impossible except via invite or verified-domain match (invite-only preserved)   (← domain-auto-assign-login)

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
- [ ] open a PR from the Close ship-review above; the human reviews + merges (domain-routing-unification is security — dual adversarial verify recorded before merge)
- [ ] document the domain-claim admin runbook (how to claim + DNS-TXT verify a domain; how auto-join behaves)
- [ ] capture the domain-claims-console design artifact (the UDD confirmed screen) into the design hand-off
- [ ] tag / publish / deploy (human-run, per release.md) — bundle with `account-tiers-billing` into the next release cut
