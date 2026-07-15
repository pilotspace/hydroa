# MILESTONE: Team Member Invite

goal: A tenant owner/admin can invite a new colleague by email into their own tenant with a chosen role, and that colleague can accept the invite and set their own password — without requiring SSO/domain-mapping to be configured
rationale: new-major — a confirmed, previously-unaddressed gap: password-auth tenants have ZERO path to add a colleague short of configuring OIDC domain-mapping (`POST /admin/auth/signup` always mints a brand-new tenant; the Teams "add member" dialog only resolves an email ALREADY in the caller's tenant). Sized as its own milestone — not a sub-part of the "Full 5, admin-first" superadmin roadmap, since it is core tenant self-service, not a platform/superadmin capability — per Tin's 2026-07-03 instruction to build it in parallel with `tenant-impersonation`/`platform-access-plan`.
stage: production · status: active · created: 2026-07-03T16:21:50+00:00
release: pending

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - Owner/admin creates a tenant-scoped invite (email + role) for someone with no existing account,
    honoring the IDENTICAL escalation ceiling already frozen for role-reassignment
    (`AssignUserRoleUseCase`/`_ADMIN_ASSIGNABLE`, `users_use_cases.py:26-28,64-91`): owner may invite
    any of the 6 self-service tiers (including a co-owner); admin may invite only
    `{operator, billing_admin, viewer, member}`; superadmin is never invitable, rejected before any
    use-case runs (mirrors the existing 422 pre-check, `users_router.py:121-129`).
  - A pending invite is a single-use, hashed-at-rest, explicitly-expiring record; owner/admin can list
    and revoke it (revoke durably kills the token). Re-inviting the same (tenant, email) while a
    pending invite exists replaces it — no stacked pending rows, no separate "resend" surface.
  - A public (unauthenticated), token-keyed lookup so the dashboard can preview {tenant name, invited
    email, role} before the invitee commits to a password, plus a public accept action that sets a
    password and provisions a new `UserRow` bound to the invite's tenant_id + role
    (`auth_method='password'`) — rate-limited, failing closed (no partial row) on any
    unknown/expired/revoked/already-accepted token.
  - Dashboard: an "Invite member" action + a "Pending invites" list/revoke surface added to the
    existing `/app/members` page (one surface, two states of the same roster — not a separate page),
    reusing `AddMemberDialog.tsx`'s modal/focus-trap/validation shape for the invite form. A new public
    accept-invite page lives beside `(auth)/login`/`(auth)/signup` (same unauthenticated shell) with a
    BFF route that auto-logs the new user in on success, mirroring the EXISTING signup chain
    byte-for-byte (`app/api/auth/signup/route.ts:28-67`: create → `POST /admin/auth/login` → httpOnly
    `ai_proxy_session` cookie) — no new session mechanism. Signature element: a live "expires in Nd"
    countdown chip on each pending-invite row (the one deliberate distinguishing touch, not badge soup).

Out:
  - Real transactional email delivery (SMTP/SendGrid/SES/Mailgun/Postmark/etc.) — CONFIRMED absent: an
    exhaustive grep across `apps/gateway/src` and `pyproject.toml` found zero email-provider
    infrastructure anywhere in the codebase. v1 ships a copy/paste invite LINK the owner/admin relays
    out-of-band (Slack/email/etc. of their own choosing).
    ⚠ FLAGGED — genuine product-scope call, not a technical default: confirm with Tin that a
    link-only v1 is acceptable before `member-invite-issuance` freezes its contract; wiring a real
    outbound-email provider is a distinct, later capability decision.
  - ⚠ FLAGGED — invite token expiry window is not yet decided: proposing 7 days (common industry
    default for org-invite links) as the working assumption; `member-invite-issuance` should confirm
    or override this at its own SPECIFY step rather than treat 7d as frozen.
  - Any seat-count / user-count cap enforcement — explicitly belongs to `platform-access-plan` per the
    roadmap's own cross-milestone dependency note. This milestone leaves ONE clean provisioning choke
    point (see Shared decisions) but must NOT build the cap itself.
  - Payment/checkout/plan-tier gating on who can be invited — no plan/tier column or enforcement exists
    anywhere in application code today (confirmed; the marketing pricing page is explicitly disclaimed
    placeholder copy, `app/(marketing)/pricing/page.tsx`).
  - Bulk/CSV invite of multiple people in one action — v1 is one-at-a-time, mirroring
    `AddMemberDialog.tsx`'s existing one-at-a-time precedent.
  - Editing an invite's role/email after creation — revoke + re-invite instead; avoids a stale-token
    edge case for a rarely-needed action.
  - Any change to the Teams `team_members` add-by-email flow or its `lead`/`member` tag — a DIFFERENT,
    untouched concept (Teams group API keys for budget attribution and optionally tag EXISTING users
    `lead`/`member`; grants zero tenant RBAC permission — GLOSSARY `Team`/`TeamMember`).
  - Any change to OIDC/domain-mapping auto-provisioning (`OidcLoginUseCase`,
    `get_or_provision_oidc_user`) — unchanged; remains the other (pre-existing) path into a tenant.

## Shared decisions & glossary deltas   (living — every task must honor these)
- **New GLOSSARY concept — "invite" (pending invite)**: a tenant-scoped, single-use, expiring,
  hashed-at-rest offer for a NEW person to join AS a specific role — distinct from `Team`'s
  `lead`/`member` join-table tag, which groups EXISTING users for budget attribution and grants no
  tenant RBAC permission. Do not conflate the two in code, copy, or tests.
- **Escalation-ceiling parity is a hard requirement, not a re-derivation**: invite-creation MUST reuse
  (call, not reimplement) the exact ceiling already frozen for role-reassignment — owner: any of the 6
  self-service tiers; admin: `{operator, billing_admin, viewer, member}` only; superadmin: never,
  rejected before any use-case runs, byte-identical in shape to `users_router.py:121-129` /
  `platform_users_router.py:174-179`'s existing pre-check. A drift between "who can be invited" and
  "who can be role-reassigned" is a privilege-escalation bug, not a style nit.
- **Invite tokens follow the API-key precedent, not the JWT precedent**: per GLOSSARY's existing
  API-key design ("stored as SHA-256 hash... 32-byte CSPRNG secrets make offline brute force
  infeasible"), an invite token is a random opaque value, hashed at rest, single-use, with an explicit
  `expires_at` and a pending/accepted/revoked status — NOT a stateless JWT, because a JWT-encoded
  invite could never be durably, instantly revoked (the "revoked invite" case demands a DB-side flip).
- **Server-side-only identity resolution on accept**: the accept endpoint resolves email, tenant_id,
  and role EXCLUSIVELY from the invite row keyed by the token — never as client-supplied body fields.
  Mirrors two existing precedents: `get_or_provision_oidc_user`'s "role is ALWAYS member, never from
  claims" (`repository.py:132`) and device-approval-flow's "binding always from the verified source,
  never the body" (`.add/tasks/device-approval-flow/TASK.md` §3 AUTHZ RULE).
- **Cross-tenant email-enumeration stance** (security-relevant — surfacing the tradeoff, not deciding
  silently): inviting an email already belonging to a user in the CALLER's OWN tenant → 409 (safe to
  disclose; the caller already sees their own roster via `GET /admin/users`). Inviting an email
  belonging to a user in a DIFFERENT tenant must NOT be distinguishable from a brand-new email at
  invite-CREATE time (identical response either way) — this follows the `teams-add-by-email` precedent
  of refusing to build any surface that discloses cross-tenant user existence to a tenant-scoped caller
  (its own rejected framing: "add a `GET /admin/users` picker — rejected, enumerates users",
  `.add/tasks/teams-add-by-email/TASK.md` §1), deliberately NOT the older, narrower, already-existing
  `AUTH_EMAIL_TAKEN` public-signup oracle (`router.py:50-51` — today, anyone unauthenticated can already
  learn "is this email registered" via `POST /admin/auth/signup`'s 409). This milestone should not
  compound that pre-existing leak with a second, tenant-owner-facing one. The one place the fact CAN
  surface is at ACCEPT time, where the global `UserRow.email` uniqueness constraint (`orm.py:79`) makes
  a collision unavoidable — reusing `AUTH_EMAIL_TAKEN`'s exact 409 shape there is acceptable (narrower
  audience: whoever holds that one unguessable token, not an anonymous prober) — owning task:
  `member-invite-acceptance`. Residual risk, named not hidden: a tenant owner/admin could generate an
  invite for a guessed email and open the accept-link themselves to test for that 409 — bounded by
  already needing MEMBERS_MANAGE on some tenant, materially narrower than the anonymous signup oracle,
  but not zero; worth a one-line note in that task's own risk assessment, not a blocker here.
- **One clean provisioning choke point for the future seat-cap**: `platform-access-plan`'s eventual
  seat/user-count cap needs "a new user is being added to tenant X" as a hook point; today that exists
  only in `get_or_provision_oidc_user`. This milestone's accept-flow becomes the SECOND entry point —
  structure it as one clearly named use-case call (not scattered inline inserts) so that future task
  gets exactly two call sites to wire, not a codebase hunt. This milestone does not build the cap
  itself (Out, above).
- **Dashboard accept-invite reuses the existing signup auto-login chain verbatim**: `SignupResponse`
  carries no token (`schemas.py:12-14`); the BFF's `app/api/auth/signup/route.ts:28-67` gets the JWT by
  calling `POST /admin/auth/login` immediately after signup succeeds, then sets the httpOnly
  `ai_proxy_session` cookie. The new accept-invite BFF route runs the SAME two-step chain
  (accept → login → cookie) — no new session/auto-login mechanism invented.
- **Token transport minimizes log exposure where it can**: the token travels in the dashboard URL
  (unavoidable — that is what makes it a clickable link) but the gateway's lookup/accept calls take it
  as a path/body value, never repeated as a URL query string on the API itself, keeping it out of
  typical access logs; browser-history/referrer exposure is an accepted, inherent tradeoff of
  link-based invites (the same one every password-reset-link design makes).
- **`auth_method` needs no new conflict handling**: an invite-accepted user always gets
  `auth_method='password'` (existing column, `orm.py:86-88`) regardless of whether the tenant
  separately has OIDC configured — the two paths already co-exist per-user today (OIDC-provisioned rows
  get `'oidc'`), and email's global uniqueness means there is nothing new to reconcile.
- **A11y + component reuse by construction**: the invite dialog reuses `AddMemberDialog.tsx`'s
  focus-trap + `role="alert"`/`aria-live` error pattern verbatim; the accept-invite page reuses the
  `(auth)` route group's existing unauthenticated shell; WCAG AA floor throughout — no bespoke a11y
  pattern invented for this milestone.

## Shared / risky contracts (freeze these first)
- Invite record shape + token lifecycle (columns, hash-at-rest, `expires_at`, single-use
  pending/accepted/revoked state machine) -> owning task `member-invite-issuance`
- Escalation-ceiling parity with `AssignUserRoleUseCase` (exact invitable-role sets per caller role,
  superadmin hard-excluded) -> owning task `member-invite-issuance`
- Accept-time identity resolution + the cross-tenant/global-email-collision handling (server-side-only
  email/tenant/role; 409 reuse of `AUTH_EMAIL_TAKEN` on a global-uniqueness collision) -> owning task
  `member-invite-acceptance`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] member-invite-issuance      depends-on: none                                          — Owner/admin create/list/revoke tenant-scoped invites (hashed single-use token, explicit expiry, escalation ceiling mirrors AssignUserRoleUseCase).
- [x] member-invite-acceptance    depends-on: member-invite-issuance                          — Public, unauthenticated token lookup (preview) + accept (set password) that provisions the User row; rate-limited, fails closed on expired/revoked/consumed/unknown tokens.
- [x] member-invite-ui            depends-on: member-invite-issuance, member-invite-acceptance — Dashboard "Invite member" dialog + pending-invites list/revoke on the Members page; new public accept-invite page + BFF route mirroring signup's auto-login chain.

## Exit criteria (observable; map each to the task that delivers it)
- [x] An owner can invite a new colleague by email into any of the 6 self-service roles; an admin can
      only invite `{operator, billing_admin, viewer, member}` — attempting owner/admin/superadmin is
      rejected with the same shape as the existing role-reassignment ceiling   (← member-invite-issuance)
- [x] A pending invite appears in a list and can be revoked, immediately and durably invalidating its
      token   (← member-invite-issuance)
- [x] Re-inviting the same email while a pending invite exists issues a fresh token and kills the old
      one — no stacked/duplicate pending rows   (← member-invite-issuance)
- [x] The invited person can open the link with no login, see which tenant and role they are joining,
      set a password, and land in the dashboard already signed in — with zero OIDC/domain-mapping
      configuration required   (← member-invite-acceptance, member-invite-ui)
- [x] An expired, revoked, already-accepted, or unknown token is rejected with a specific, non-500 error
      and creates no user row under any of those conditions   (← member-invite-acceptance)
- [x] The full gateway test suite stays green with new coverage for the happy path and every reject
      branch (mirrors the `device-approval-flow` suite's breadth of 401/404/409/410/422/429-shaped
      cases as applicable)   (← member-invite-issuance, member-invite-acceptance)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- backend (apps/gateway) : invites_router (create/list/revoke), invite_accept_router (public preview/accept),
  invite_use_cases + invite_repository (hashed single-use token, 7d expiry, atomic create-or-replace,
  SELECT…FOR UPDATE revoke/accept), shared assert_role_within_ceiling reused by CreateInviteUseCase.
  Independently re-verified 2026-07-15: issuance 30/30 + acceptance 17/17 + rbac 16/16 on fresh DBs;
  all 4 security shared-decisions HOLD.
- dashboard (apps/dashboard) : InviteMemberDialog (role-filtered, copy-link one-time token) + PendingInvites
  (live "expires in Nd" chip + revoke) on /app/members; public /invite/[token] accept page in the (auth)
  shell; /api/auth/invite/[token] BFF (preview relay + accept→login→httpOnly-cookie, mirrors signup).
  Shared roles.ts (assignableRoles lifted, no drift). 24 new vitest tests (red-first) + full suite 1438 green;
  tsc + eslint clean.
- tooling / skill / book : untouched.

### Cross-task evidence   (one row per task — all gate=PASS)
- member-invite-issuance   : gate=PASS · 30/30 (re-verified fresh DB) · residue=none
- member-invite-acceptance : gate=PASS · 17/17 (incl. forced-race + global-email-collision) · residue=none
- member-invite-ui         : gate=PASS · 24 new vitest + 1438 dashboard suite green · tsc/eslint clean · residue=none

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: A tenant owner/admin can invite a colleague by email into a chosen role and that colleague can
  accept + set their own password WITHOUT SSO — proven end-to-end: issuance/acceptance backend (independently
  re-verified, security decisions hold) + the dashboard invite dialog, pending list, and public accept page
  with BFF auto-login (built 2026-07-15 via the UDD design loop, Tin-confirmed: copy-link v1, 7d expiry,
  pending on /app/members). Full gateway suite green (my detached -n12: 3924 passed) + dashboard 1438 green.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] <step — e.g. open a PR from the Close ship-review above; the human reviews + merges>
- [ ] <step — e.g. export the ship-review to a hand-off doc, e.g. `pandoc CLOSE.md -o close.docx`>
- [ ] <step — e.g. tag / publish / deploy  (human-run, per release.md)>
