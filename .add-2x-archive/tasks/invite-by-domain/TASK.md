# TASK: Domain-restricted shareable invite link (rung-1 unlock)

slug: invite-by-domain · created: 2026-07-20 · stage: production
milestone: domain-onboarding-softening
component: gateway
sensitivity: security
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `apps/gateway/src/gateway/tenants/api/invites_router.py` — the AUTHENTICATED `/admin/invites` router (MEMBERS_MANAGE-gated: `create_invite`/`list_invites`/`revoke_invite`). This task adds a SIBLING authenticated surface for domain LINKS: `POST/GET/DELETE /admin/domain-invite-links` (create-if-eligible / list-active / revoke), same `require_permission(MEMBERS_MANAGE)` gate + the member-verified-on-that-domain eligibility gate (NEW). REUSE its router/schema/`_get_repo` idiom; do NOT touch its per-email endpoints.
  - `apps/gateway/src/gateway/tenants/api/invite_accept_router.py` — the PUBLIC, unauthenticated `/invites/{token}` preview + `/invites/{token}/accept` provision router. This task adds a SIBLING public surface for the two-step DOMAIN redeem: `POST /domain-invite-links/{token}/redeem` (start: server derives nothing from a stored email — the redeemer SUPPLIES their @domain email → issue a 6-digit mailbox code) + `POST /domain-invite-links/{token}/redeem/verify` (finish: code + password → provision ONE MEMBER). REUSE verbatim: `resolve_trusted_client_ip(request, trusted_proxy_hops)`, the per-IP `InvitePublicRateLimiter` (`request.app.state.invite_public_limiter`) fail-open-on-Redis-outage pattern, the fire-and-forget fail-open `record_audit`, and the `extra="ignore"`/`extra="forbid"` schema-boundary identity guard.
  - `apps/gateway/src/gateway/tenants/infrastructure/invite_repository.py:InviteRepository.accept` (lines 238–338 as of ground SHA) — the CANONICAL one-transaction provisioning choke point: `SELECT … FOR UPDATE` → capture attrs BEFORE commit (MissingGreenlet trap) → pending/not-expired checks → `assert_seat_available(session, tenant_id)` (seat cap, raises `SeatCapExceededError`) → `UserRow(auth_method="password")` + `flush` (IntegrityError→`EmailAlreadyRegisteredError` on GLOBAL users.email) → `SeatMembershipEventRow(event_type="joined", occurred_at=now)` → flip status → single `commit`. The domain-redeem provisioning MUST mirror this shape (role is FIXED = MEMBER, tenant_id from the link, email from the verified code's redemption row).
  - `apps/gateway/src/gateway/domain_capture/domain/member_verify_code.py` — task-4's FROZEN reusable code primitives, IMPORTED verbatim: `generate_member_verify_code()` (`secrets.randbelow`, zero-padded 6-digit), `hash_member_verify_code(code, jwt_secret)` (Option-A keyed HMAC-SHA256 at rest, no new secret), `verify_member_verify_code(code, code_hash, jwt_secret)` (constant-time `hmac.compare_digest`). The redeem code reuses THIS scheme (not a second crypto design).
  - `apps/gateway/src/gateway/tenants/domain/entities.py:Invite`/`Role`/`InviteStatus` — the existing per-email entities. This task adds a NEW `DomainInviteLink` entity (tenant_id, domain, token_hash INFRA-ONLY, status active|revoked, expires_at, created_by_user_id, created_at) — the plaintext token returned once at creation, NEVER persisted (mirrors `Invite`'s token_hash discipline). Redemptions are ephemeral per-(link,email) code state, not a domain entity.
  - `tenant_domain_claims` (task-4 surface: `member_verified_at` additive col, `resolve_verified_tenant` matches status='verified' ONLY) — the CREATE-eligibility gate reads it: a link may be minted for domain D only if the caller's tenant holds a claim on D that is member-verified (`member_verified_at IS NOT NULL`) OR owner-verified (`status='verified'`). `resolve_verified_tenant` and the ClaimStatus enum/constraint/indexes stay BYTE-UNTOUCHED.
Context (working folder):
  - `apps/gateway/src/gateway/core/error_catalog.py` — REUSE `INVITE_NOT_FOUND`(404)/`INVITE_EXPIRED`(410)/`INVITE_NOT_PENDING`(409 → here "revoked"), `AUTH_EMAIL_TAKEN`(409), `AUTH_PASSWORD_WEAK`(400), `PLAN_SEAT_CAP_EXCEEDED`(403), `RATE_LIMITED`(429), and the task-4 `MEMBER_VERIFY_CODE_INVALID`(400)/`_EXPIRED`(410)/`_TOO_MANY_ATTEMPTS`(429)/`_DOMAIN_MISMATCH`(403) family. NEW specs likely: a create-side "not eligible / not member-verified for this domain" (403) + a redeem-side "email domain ≠ link domain" (403; may reuse `MEMBER_VERIFY_DOMAIN_MISMATCH` semantics or a link-specific spec — decide at CONTRACT).
  - `apps/gateway/src/gateway/tenants/infrastructure/invite_public_rate_limiter.py:InvitePublicRateLimiter` + settings `invite_preview_rpm`/`invite_accept_rpm` — the per-IP token-bucket (Redis, fail-open). The two redeem endpoints get their own rpm knobs (mirror the pattern; both fail-open).
  - `apps/gateway/migrations/versions/` — a new Alembic revision off head `c2e5a9d1b7f4` (task-4's member-verify migration is HEAD): `domain_invite_links` table + the per-redemption code state (a `domain_invite_redemptions` table keyed (link_id, email) OR columns — decide at CONTRACT). Additive; working downgrade.
Honors (patterns / conventions):
  - Presentation-free BACKEND: every trust/authorization/domain/rate-limit/seat decision is server-side + tenant-scoped (member-invite-issuance, member-verified-recognition 6a75579). The redeemer supplies ONLY their email (step 1) then code+password (step 2); the link's domain/tenant/role are NEVER client-supplied — role is FIXED MEMBER (never SUPERADMIN, D3 "→ MEMBER").
  - Two secret-keyed public resources → distinguishable 404/409/410 is NOT a new enumeration oracle (the identifying key IS the unguessable token) — the invite-accept precedent.
  - Design-for-failure (CLAUDE.md): rate-limit BEFORE DB IO fail-open; audit fire-and-forget fail-open; the code issue→verify→provision path atomic under `FOR UPDATE`; a redeem race caps at exactly the attempt ceiling (task-4 concurrency shape — increment persists BEFORE the use-case raises).
  - NEVER auto-join: a domain link grants ADMIN-INITIATED membership only (the admin minted+shared it AND the redeemer proved their own mailbox). `resolve_verified_tenant` (the DNS-only stranger-auto-join gate) is untouched and never consulted here.
Seams consulted: FE→gateway = the dashboard `/api/gw/[...path]` catch-all BFF (task 6b consumes these endpoints); no `.add/SEAMS.md` entry needed. Public redeem endpoints are reached UNAUTHENTICATED by the redeem page (no session yet — the redeemer has no account until provisioned).
Anchors the contract cites: `POST/GET/DELETE /admin/domain-invite-links`, `POST /domain-invite-links/{token}/redeem`, `POST /domain-invite-links/{token}/redeem/verify`, the `DomainInviteLink` entity, `domain_invite_links` (+redemption code state) tables, the member-verified create-eligibility gate over `tenant_domain_claims.member_verified_at`, the reused `member_verify_code.py` primitives + `InviteRepository.accept` provisioning shape — all in `apps/gateway`.
Issues/Risks (→ feed §1):
  - CREATE ELIGIBILITY is the create-side security boundary: only a member-verified (or owner-verified) domain may mint a link. A missing/pending claim → 403, never a link. Must be tenant-scoped (a caller can only mint for a domain THEIR tenant is verified on) — an anti-confused-deputy check (cf. residency confused-deputy lesson).
  - REDEEM DOMAIN ENFORCEMENT is the redeem-side boundary: the supplied email's domain MUST equal the link's domain, normalized identically to task-4's `_caller_email_domain` (subdomain/unicode/IP fail-closed) — else a code emailed to `attacker@evil.com` under an acme.com link. Enforce at step-1 (issue) so a non-@domain email never even receives a code.
  - TWO-STEP EPHEMERAL STATE: step-1 issues a code bound to (link, email); step-2 verifies+provisions. The per-(link,email) code row needs its own hash/expiry/attempt-cap/single-use lifecycle (task-4 columns shape) AND must compose with the link's own active/expiry/revoked state (a revoked link mid-redeem → all in-flight codes dead). Decide the table vs columns shape at CONTRACT.
  - PROVISION ATOMICITY + SEAT CAP: redeem/verify provisions exactly one MEMBER under `FOR UPDATE`, consults `assert_seat_available` BEFORE the INSERT (a domain link can flood seats — the cap is the backstop), appends the "joined" seat event, all one transaction; global users.email collision → `AUTH_EMAIL_TAKEN`, link+code left intact (idempotent retry-safe: the already-registered user simply can't double-provision).
  - RATE-LIMIT SURFACE: an unauthenticated reusable link is a spam/enumeration target → per-IP limit on BOTH redeem steps (fail-open) + the per-(link,email) attempt cap bounds code brute force. A single link's total redemptions are unbounded by design (D3 "unlimited until revoke/expiry") — the seat cap + domain gate are the real limits.
  - REVOKE vs in-flight: revoking a link must stop new redemptions immediately; a `FOR UPDATE` read of the link row at verify-time sees the revoked status (mirrors invite accept-vs-revoke race).
Related intent: milestone `domain-onboarding-softening` — the CORE goal ("invite their team by verified email domain the moment they sign up"). Rung-1 (member-verified, task 4/5) UNLOCKS this: D3 (Tin-confirmed 2026-07-19, re-confirmed axes 2026-07-20) = a domain-restricted shareable LINK, admin-initiated, @domain-scoped, revocable, only @domain email redeems → MEMBER, restriction SERVER-SIDE, never auto-join, extends member-invite-issuance. The WHY: give a whole team a low-friction rung-1 join path without per-person email invites or the DNS-owner burden, while keeping stranger auto-join strictly DNS-gated. See [[domain-onboarding-progressive-trust]].
Ground SHA: b1e4608 (member-verified-code-entry is HEAD; symbols cited above, not bare lines — any line ref is "as of" this commit).
Split: this is task 6a (GATEWAY security core). Task 6b (dashboard/UDD: admin create-manage UI + the two-step redeem page) is a SEPARATE task with its own wireframe confirmation, held until this contract freezes (FE-holds-until-BE-freezes).
Redeem-proof model CONFIRMED by Tin 2026-07-20 (AskUserQuestion): (1) 6-digit mailbox code on redeem (reuse task-4 infra); (2) reusable link, revocable, ~30d expiry, unlimited redeems; (3) member-verified-on-domain + MEMBERS_MANAGE unlocks create; (4) BE-core/FE split.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Domain-restricted shareable invite link — gateway security core (create/list/revoke for an eligible admin + a two-step public mailbox-code redeem that provisions a MEMBER). Consumes the FROZEN member-verified backend (task 4, 6a75579) + reuses its code primitives; UI is task 6b.
Framings weighed: two-step mailbox-code redeem (chosen — a shared link can't prove an individual mailbox, so the redeemer proves theirs with a task-4 6-digit code before provisioning) · bearer-link + domain-check only (rejected at UDD — a leaked link would let any known @domain address join with no mailbox proof) · SSO-authenticated redeem (rejected — needs a pre-existing @domain identity; doesn't fit self-serve signup)

Must:
<must>
  - M1 · CREATE ELIGIBILITY — POST /admin/domain-invite-links requires MEMBERS_MANAGE AND the caller's tenant holds a `tenant_domain_claims` row for `domain` that is member-verified (`member_verified_at IS NOT NULL`) OR owner-verified (`status='verified'`); otherwise 403 ERR_DOMAIN_INVITE_NOT_ELIGIBLE and NO link is created. Tenant-scoped: eligibility is only ever read for the caller's OWN tenant (anti-confused-deputy).
  - M2 · CREATE MINTS + SUPERSEDES — on success: 32-byte CSPRNG token, store ONLY its SHA256 hash (plaintext returned exactly ONCE in the 201 body, NEVER persisted), status='active', expires_at = now + 30d, created_by = caller. If an active link already exists for (tenant, domain) it is atomically superseded (revoked) so at most ONE active link per (tenant, domain) exists (partial-unique index WHERE status='active'). [contract]
  - M3 · CREATE SERVER-DERIVED — `domain` normalized lowercase; tenant_id + created_by come from the authenticated caller, never the body; the future member's role is FIXED MEMBER (no role field on create).
  - M4 · LIST — GET /admin/domain-invite-links returns ACTIVE links in the caller's tenant ONLY ({id, domain, status, expires_at, created_at}); the token NEVER appears in the list. No cross-tenant rows.
  - M5 · REVOKE — DELETE /admin/domain-invite-links/{id} flips a link in the caller's tenant to status='revoked' (idempotent target state); a revoked link can never again be redeemed.
  - M6 · REDEEM STEP-1 (issue) — POST /domain-invite-links/{token}/redeem {email}: resolve the link by token_hash (404 if none), require active (409 if revoked) + not-expired (410 if past); require email's domain == link.domain, normalized identically to task-4 `_caller_email_domain` (subdomain/unicode/IP fail-closed) else 403 ERR_DOMAIN_INVITE_DOMAIN_MISMATCH with NO code emailed; on pass, issue a 6-digit code (`generate_member_verify_code`) bound to (link, email), persist its Option-A keyed hash (`hash_member_verify_code`) + ~15-min expiry + attempt_count=0 (UPSERT — re-issue supersedes & resets), then email it FAIL-OPEN (still 202).
  - M7 · REDEEM STEP-2 (verify+provision) — POST /domain-invite-links/{token}/redeem/verify {email, code, password}: in ONE transaction with the link row + redemption row under `SELECT … FOR UPDATE` — re-check link active+not-expired; require a redemption row for (link, email) with a non-expired code and attempt_count < cap(5); constant-time compare (`verify_member_verify_code`); on mismatch increment attempt_count and PERSIST BEFORE raising (at cap → invalidate); check password strength BEFORE hashing; then provision exactly ONE UserRow(role=MEMBER, tenant_id=link.tenant_id, email, auth_method='password') mirroring `InviteRepository.accept` — `assert_seat_available` first, global users.email collision → rollback (link+code intact), append SeatMembershipEventRow 'joined', CONSUME (delete) the redemption row, single commit; return 201 {tenant_id, user_id, email}.
  - M8 · NO ENUMERATION AT STEP-1 — step-1 NEVER reveals whether `email` is already a registered user; account-existence surfaces only at step-2 provision (after the code proves mailbox control). No account-existence oracle on the public surface.
  - M9 · CODE AT REST + SINGLE USE — the code is stored ONLY as the Option-A keyed hash; never plaintext, never logged, never in any response body; the redemption row is consumed (deleted) on successful provision; a replay of the same correct code after success → ERR_MEMBER_VERIFY_CODE_INVALID.
  - M10 · NEVER AUTO-JOIN / NEVER SUPERADMIN — redeem provisions role=MEMBER only; `resolve_verified_tenant` + the ClaimStatus enum/CheckConstraint/indexes are BYTE-untouched; a domain link is never read by the DNS auto-join path.
  - M11 · RATE-LIMIT BOTH PUBLIC STEPS — per-IP token-bucket on redeem AND redeem/verify (own rpm knobs), checked BEFORE any DB IO, fail-open on Redis outage → ERR_RATE_LIMITED (429, Retry-After). The per-(link,email) attempt cap bounds code brute-force independently.
  - M12 · AUDIT — successful create, revoke, and redeem/verify (member joined) each emit a fire-and-forget FAIL-OPEN audit event; step-1 issue and list are unaudited (mirrors preview-unaudited).
</must>
Reject:
<reject>
  - R1 · create without MEMBERS_MANAGE -> "ERR_FORBIDDEN" (router permission gate)
  - R2 · create for a domain the tenant is NOT member/owner-verified on -> "ERR_DOMAIN_INVITE_NOT_ELIGIBLE"
  - R3 · redeem with email domain ≠ link domain -> "ERR_DOMAIN_INVITE_DOMAIN_MISMATCH" (no code emailed)
  - R4 · redeem/verify wrong code -> "ERR_MEMBER_VERIFY_CODE_INVALID" (attempt_count++, no user)
  - R5 · redeem/verify after cap(5) failed attempts -> "ERR_MEMBER_VERIFY_TOO_MANY_ATTEMPTS" (code invalidated, no user)
  - R6 · redeem/verify expired code -> "ERR_MEMBER_VERIFY_CODE_EXPIRED" (no user)
  - R7 · redeem/verify weak password -> "ERR_AUTH_PASSWORD_WEAK" (no user, code NOT consumed)
  - R8 · redeem/verify email already a GLOBAL user -> "ERR_TENANT_EMAIL_TAKEN" (no user, link+code intact)
  - R9 · redeem against a revoked link -> "ERR_DOMAIN_INVITE_LINK_INACTIVE" (no code/user)
  - R10 · redeem against an expired link -> "ERR_INVITE_EXPIRED"
  - R11 · redeem against an unknown token -> "ERR_INVITE_NOT_FOUND" (no oracle)
  - R12 · redeem/verify would exceed the seat cap -> "ERR_PLAN_SEAT_CAP_EXCEEDED" (no user)
  - R13 · revoke a link id NOT in the caller's tenant -> "ERR_INVITE_NOT_FOUND" (no cross-tenant oracle)
  - R14 · concurrent redeem/verify with the SAME correct code -> provisions the user EXACTLY once; the loser sees the consumed row -> "ERR_MEMBER_VERIFY_CODE_INVALID"
  - R15 · body injects tenant_id/role/domain at redeem/verify -> silently DROPPED (extra="ignore"), never read
</reject>
After:
<after>
  - an eligible admin (member/owner-verified on D + MEMBERS_MANAGE) holds exactly ONE active, revocable, 30-day invite link for D;
  - a teammate holding an @D mailbox can redeem it (email → 6-digit code → password) to become a MEMBER, exactly once per mailbox;
  - no code is ever stored in plaintext and no link enables stranger auto-join; `resolve_verified_tenant` (the DNS-only auto-join gate) is unchanged.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] POST re-create for an already-active (tenant, domain) SUPERSEDES the old link (rotates the token; the previously-shared link dies) rather than returning the existing one or 409 — lowest confidence because it is a product-UX call: an admin re-opening the create dialog would silently invalidate a link they already distributed. If wrong: a distributed link breaks unexpectedly. Cheap to flip to "return existing link (no new token) + an explicit Regenerate action" at contract — no schema change.
  - [ ] 30-day link expiry · ~15-min code expiry · attempt-cap 5 are HARDCODED constants (mirror task-4 + invite defaults); no caller-supplied TTL — a one-line change, never a shape change.
  - [ ] redeem/verify re-supplies `email` in the body (the code is bound to (link, email)); binding the code to the link alone is rejected (a single link's codes would be interchangeable across redeemers).
  - [ ] the seat cap is consulted at step-2 provision, not step-1 issue — issuing a code consumes no seat; only provisioning does.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: eligible admin mints a link   # M1,M2,M3
  Given a MEMBERS_MANAGE admin in a tenant with a member-verified claim on "acme.com"
  When they POST /admin/domain-invite-links {domain:"Acme.com"}
  Then 201 returns {id, domain:"acme.com", token:<32B plaintext, once>, status:"active", expires_at:~now+30d, created_at}
  And exactly one active domain_invite_links row exists for (tenant, "acme.com"), storing only the token's SHA256 hash (no plaintext)

Scenario: re-create supersedes the active link   # M2 [contract]
  Given an active link already exists for (tenant, "acme.com") with token T1
  When the admin POSTs create for "acme.com" again
  Then 201 returns a NEW token T2, the old link is status='revoked', and only one active row remains for (tenant,"acme.com")
  And redeeming with T1 now fails ERR_DOMAIN_INVITE_LINK_INACTIVE

Scenario: create for an unverified domain is refused   # R2
  Given the tenant has NO member/owner-verified claim on "acme.com" (or only a pending claim)
  When a MEMBERS_MANAGE admin POSTs create {domain:"acme.com"}
  Then 403 ERR_DOMAIN_INVITE_NOT_ELIGIBLE
  And no domain_invite_links row is created

Scenario: create requires MEMBERS_MANAGE   # R1
  Given a caller WITHOUT MEMBERS_MANAGE
  When they POST create
  Then 403 ERR_FORBIDDEN
  And no link is created

Scenario: list is active + tenant-scoped, token-free   # M4
  Given tenant A has one active and one revoked link, tenant B has an active link
  When an admin of A GETs /admin/domain-invite-links
  Then only A's ACTIVE link is returned, with no `token` field, and B's link is absent

Scenario: revoke stops redemption   # M5
  Given an active link with token T in the caller's tenant
  When the admin DELETEs /admin/domain-invite-links/{id}
  Then the link is status='revoked'
  And a subsequent redeem with T fails ERR_DOMAIN_INVITE_LINK_INACTIVE

Scenario: revoke of another tenant's link is not found   # R13
  Given a link id that belongs to a DIFFERENT tenant
  When the caller DELETEs it
  Then 404 ERR_INVITE_NOT_FOUND
  And the other tenant's link remains active (no cross-tenant mutation)

Scenario: redeem step-1 issues a code to an @domain mailbox   # M6
  Given an active link for "acme.com" with token T
  When someone POSTs /domain-invite-links/T/redeem {email:"Sam@Acme.com"}
  Then 202, and a domain_invite_redemptions row for (link,"sam@acme.com") holds a keyed code hash + ~15-min expiry + attempt_count=0
  And exactly one code email was sent to sam@acme.com

Scenario: redeem step-1 rejects a non-@domain email with no email sent   # M6,R3
  Given an active link for "acme.com"
  When someone POSTs redeem {email:"eve@evil.com"} (or "sam@sub.acme.com", or an IP/unicode host)
  Then 403 ERR_DOMAIN_INVITE_DOMAIN_MISMATCH
  And NO code row is created and NO email is sent

Scenario: re-issue supersedes and resets the code   # M6
  Given a redemption row for (link,"sam@acme.com") with attempt_count=3
  When redeem step-1 is called again for the same (link, email)
  Then the code hash is replaced, expiry refreshed, and attempt_count reset to 0

Scenario: step-1 does not reveal an existing account   # M8
  Given "sam@acme.com" is ALREADY a registered global user
  When redeem step-1 is called for "sam@acme.com" under an acme.com link
  Then the response is the SAME 202 as for a new email (a code is issued); account existence is NOT disclosed

Scenario: verify with the correct code provisions a MEMBER   # M7,M9,M10
  Given a redemption row for (link,"sam@acme.com") with a valid unexpired code C
  When someone POSTs /domain-invite-links/T/redeem/verify {email:"sam@acme.com", code:C, password:<strong>}
  Then 201 {tenant_id:link.tenant, user_id, email:"sam@acme.com"}
  And exactly one users row exists (role=MEMBER, auth_method='password'), one seat 'joined' event is appended, the redemption row is deleted, and the claim's status/resolve_verified_tenant are unchanged

Scenario: wrong code increments and does not provision   # R4
  Given a redemption row with attempt_count=0 and code C
  When verify is called with a WRONG code
  Then 400 ERR_MEMBER_VERIFY_CODE_INVALID, attempt_count is now 1, and no users row is created

Scenario: attempt cap invalidates the code   # R5
  Given a redemption row at attempt_count=4 (one below cap)
  When verify is called with a wrong code
  Then 429 ERR_MEMBER_VERIFY_TOO_MANY_ATTEMPTS, the code is invalidated, and no users row exists

Scenario: expired code is refused   # R6
  Given a redemption row whose code_expires_at is in the past
  When verify is called with that (formerly-correct) code
  Then 410 ERR_MEMBER_VERIFY_CODE_EXPIRED and no user is provisioned

Scenario: weak password is refused before consuming the code   # R7
  Given a valid unexpired code C for (link,"sam@acme.com")
  When verify is called with code C and a password shorter than the minimum
  Then 400 ERR_AUTH_PASSWORD_WEAK, no users row, and the redemption row (code C) is NOT consumed

Scenario: an already-registered email cannot double-provision   # R8
  Given "sam@acme.com" is already a global user, holding a valid code C
  When verify is called with code C and a strong password
  Then 409 ERR_TENANT_EMAIL_TAKEN, no second users row, and the link + code remain intact

Scenario: seat cap blocks the join   # R12
  Given the tenant is at its seat cap and a valid code C for a new email
  When verify is called with code C
  Then 403 ERR_PLAN_SEAT_CAP_EXCEEDED and no users row is created

Scenario: redeem against a revoked link   # R9
  Given a revoked link with (formerly-valid) token T
  When redeem step-1 OR verify is called with T
  Then 409 ERR_DOMAIN_INVITE_LINK_INACTIVE and no code/user results

Scenario: redeem against an expired link   # R10
  Given an active-status link whose expires_at has passed
  When redeem step-1 is called with its token
  Then 410 ERR_INVITE_EXPIRED

Scenario: redeem against an unknown token   # R11
  Given a token that resolves to no link
  When redeem step-1 is called
  Then 404 ERR_INVITE_NOT_FOUND (indistinguishable — no oracle)

Scenario: concurrent verify with the same correct code provisions once   # R14,M7
  Given a valid unexpired code C for (link,"sam@acme.com")
  When two verify requests with code C arrive concurrently
  Then exactly ONE returns 201 and provisions the user; the other returns 400 ERR_MEMBER_VERIFY_CODE_INVALID (consumed row); exactly one users row exists

Scenario: injected identity fields are ignored   # R15
  Given a valid code C for (link,"sam@acme.com") under a link owned by tenant A
  When verify is called with body {email:"sam@acme.com", code:C, password:<strong>, tenant_id:<tenant B>, role:"SUPERADMIN"}
  Then the provisioned user is a MEMBER of tenant A (link.tenant); the injected tenant_id/role are never read

Scenario: rate limit both public steps, fail-open on Redis outage   # M11
  Given the per-IP limit for redeem is exhausted
  When another redeem arrives from that IP
  Then 429 ERR_RATE_LIMITED with Retry-After; AND when Redis is unavailable the limiter fails OPEN (the request proceeds)

Scenario: create/revoke/join are audited fail-open   # M12
  Given audit writing is degraded (raises)
  When create, revoke, and a successful verify each run
  Then each still returns its normal success response (audit is fire-and-forget, never blocks)
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# ── AUTHENTICATED · /admin/domain-invite-links · require_permission(MEMBERS_MANAGE) ──
POST /admin/domain-invite-links        body: { domain: str }              # extra="forbid"
  201 -> { id: uuid, domain: str, token: str, status: "active",
           expires_at: datetime, created_at: datetime }                   # token = plaintext, ONCE
  403 -> { error: "ERR_FORBIDDEN" | "ERR_DOMAIN_INVITE_NOT_ELIGIBLE" }
  422 -> validation (missing/blank domain)

GET  /admin/domain-invite-links
  200 -> { links: [ { id: uuid, domain: str, status: "active",
                      expires_at: datetime, created_at: datetime } ] }    # NO token field; ACTIVE only; tenant-scoped
  403 -> { error: "ERR_FORBIDDEN" }

DELETE /admin/domain-invite-links/{id}
  200 -> { id: uuid, status: "revoked" }
  403 -> { error: "ERR_FORBIDDEN" }
  404 -> { error: "ERR_INVITE_NOT_FOUND" }                                # not in caller's tenant (no oracle)

# ── PUBLIC · /domain-invite-links/{token}/… · unauthenticated · per-IP rate-limited FIRST ──
POST /domain-invite-links/{token}/redeem         body: { email: str }     # extra="ignore"
  202 -> { email: str }                                                   # code issued (or would-be); no account-existence disclosure (M8)
  403 -> { error: "ERR_DOMAIN_INVITE_DOMAIN_MISMATCH" }                   # email domain ≠ link domain; no email sent
  404 -> { error: "ERR_INVITE_NOT_FOUND" }                                # unknown token
  409 -> { error: "ERR_DOMAIN_INVITE_LINK_INACTIVE" }                     # revoked
  410 -> { error: "ERR_INVITE_EXPIRED" }                                  # link past expires_at
  429 -> { error: "ERR_RATE_LIMITED" }  (Retry-After)

POST /domain-invite-links/{token}/redeem/verify  body: { email, code, password }  # extra="ignore"
  201 -> { tenant_id: uuid, user_id: uuid, email: str }                   # provisions ONE MEMBER
  400 -> { error: "ERR_MEMBER_VERIFY_CODE_INVALID" | "ERR_AUTH_PASSWORD_WEAK" }
  403 -> { error: "ERR_DOMAIN_INVITE_DOMAIN_MISMATCH" | "ERR_PLAN_SEAT_CAP_EXCEEDED" }
  404 -> { error: "ERR_INVITE_NOT_FOUND" }
  409 -> { error: "ERR_DOMAIN_INVITE_LINK_INACTIVE" | "ERR_TENANT_EMAIL_TAKEN" }
  410 -> { error: "ERR_MEMBER_VERIFY_CODE_EXPIRED" | "ERR_INVITE_EXPIRED" }
  429 -> { error: "ERR_MEMBER_VERIFY_TOO_MANY_ATTEMPTS" | "ERR_RATE_LIMITED" }  (Retry-After)

Schema (additive; migration off head c2e5a9d1b7f4):
  TABLE domain_invite_links:
    id uuid PK · tenant_id uuid (FK tenants) · domain text (lowercased) ·
    token_hash text NOT NULL (SHA256, INFRA-ONLY) · status text CHECK (status IN ('active','revoked')) ·
    expires_at timestamptz NOT NULL · created_by_user_id uuid · created_at timestamptz NOT NULL
    INDEX uq_domain_invite_links_token_hash UNIQUE (token_hash)
    INDEX uq_domain_invite_links_active_domain UNIQUE (tenant_id, domain) WHERE status='active'
  TABLE domain_invite_redemptions:
    id uuid PK · link_id uuid (FK domain_invite_links ON DELETE CASCADE) · email text (lowercased) ·
    code_hash text NOT NULL (Option-A keyed HMAC, member_verify_code.py) · code_expires_at timestamptz NOT NULL ·
    attempt_count int NOT NULL DEFAULT 0 · created_at timestamptz · updated_at timestamptz
    INDEX uq_domain_invite_redemptions_link_email UNIQUE (link_id, email)   # UPSERT target (re-issue)
  Access pattern:
    - create: SELECT eligibility (tenant_domain_claims WHERE tenant_id=caller AND domain=D AND (member_verified_at IS NOT NULL OR status='verified')); on pass, in ONE tx revoke any active (tenant,D) link then INSERT the new one.
    - redeem step-1: rate-limit → resolve link by token_hash → active/expiry gate → domain match → UPSERT redemption(code_hash,expiry,attempt_count=0) commit → send_email fail-open.
    - redeem step-2: rate-limit → SELECT link FOR UPDATE (+ redemption FOR UPDATE) → active/expiry + code checks (constant-time; increment persists before raise) → assert_seat_available → INSERT UserRow(MEMBER)+flush (IntegrityError→EMAIL_TAKEN, rollback) → INSERT seat 'joined' → DELETE redemption row → single commit.
  Config knobs (settings): domain_invite_link_ttl_days=30 · domain_invite_code_ttl_minutes≈15 · domain_invite_code_max_attempts=5 · domain_invite_redeem_rpm · domain_invite_verify_rpm.
  New ErrorSpecs: ERR_DOMAIN_INVITE_NOT_ELIGIBLE(403) · ERR_DOMAIN_INVITE_DOMAIN_MISMATCH(403) · ERR_DOMAIN_INVITE_LINK_INACTIVE(409).
  Reused verbatim: member_verify_code.py (generate/hash/verify) · InviteRepository.accept provisioning shape · InvitePublicRateLimiter · record_audit · resolve_trusted_client_ip · error specs INVITE_NOT_FOUND/INVITE_EXPIRED/AUTH_EMAIL_TAKEN/AUTH_PASSWORD_WEAK/PLAN_SEAT_CAP_EXCEEDED/RATE_LIMITED/MEMBER_VERIFY_CODE_*.
  UNTOUCHED (byte-identical): resolve_verified_tenant · ClaimStatus enum + ck_tenant_domain_claims_status + the two domain-claims unique indexes · invites_router / invite_accept_router per-email endpoints.
```

Glossary deltas:
  - Domain invite link: a tenant-scoped, reusable, revocable, 30-day shareable secret (SHA256-hashed at rest) that an admin — member/owner-verified on that domain — mints so any holder of an @domain mailbox may redeem it (after proving that mailbox via a 6-digit code) to join the tenant as a MEMBER. Never enables stranger auto-join.
  - Redemption: the ephemeral per-(link, email) 6-digit-code challenge that proves an individual mailbox before a domain-link join is provisioned; consumed on success, capped and expiring like the member-verify code. [folded foundation-version 54]
Least-sure flag surfaced at freeze: [contract] POST re-create for an already-active (tenant, domain) SUPERSEDES the old link (rotates the token; the previously-shared link stops working) rather than returning the existing link or 409 — see §1 ⚠. Tin CONFIRMED supersede/rotate at freeze (2026-07-20); the dashboard (6b) must show a "this replaces the old link" hint.
Status: FROZEN @ v1 — approved by Tin 2026-07-20
Reported: yes — the freeze report (banner/ARC/SHAPE/FLAG/DECIDED/EVIDENCE) rendered before this froze

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (security-critical; every reject path + the concurrency cap exercised)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_eligible_admin_mints_link: member-verified claim on acme.com / POST create / 201 + plaintext token once + one active row storing only the hash · M1,M2,M3
  - test_recreate_supersedes_active_link: active link T1 / POST create again / new T2, T1 row revoked, one active row, T1 redeem→INACTIVE · M2 [contract]
  - test_create_unverified_domain_forbidden: no/pending claim / POST create / 403 NOT_ELIGIBLE, no row · R2
  - test_create_requires_members_manage: non-MEMBERS_MANAGE / POST create / 403 FORBIDDEN, no row · R1
  - test_list_active_tenant_scoped_no_token: A active+revoked, B active / A GET / only A's active, no token field, B absent · M4
  - test_revoke_stops_redemption: active link / DELETE / status revoked + redeem→INACTIVE · M5
  - test_revoke_other_tenant_not_found: foreign link id / DELETE / 404 + foreign link untouched · R13
  - test_redeem_issues_code_to_domain_mailbox: active acme link / redeem {Sam@Acme.com} / 202 + redemption row (hash,~15m,attempts=0) + one email to sam@acme.com · M6
  - test_redeem_rejects_non_domain_email_no_email: redeem {eve@evil.com|sam@sub.acme.com|IP|unicode} / 403 MISMATCH, no row, no email · M6,R3
  - test_reissue_supersedes_and_resets: row at attempts=3 / redeem again / hash replaced, expiry refreshed, attempts=0 · M6
  - test_step1_no_account_existence_oracle: sam@acme.com already registered / redeem / SAME 202 as a new email · M8
  - test_verify_correct_code_provisions_member: valid code / verify / 201 + one MEMBER user + one 'joined' event + redemption deleted + claim/resolve_verified_tenant unchanged · M7,M9,M10
  - test_verify_wrong_code_increments: attempts=0, wrong code / verify / 400 INVALID, attempts=1, no user · R4
  - test_verify_cap_invalidates: attempts=4, wrong code / verify / 429 TOO_MANY, code invalidated, no user · R5
  - test_verify_expired_code: code_expires_at past / verify / 410 CODE_EXPIRED, no user · R6
  - test_verify_weak_password_keeps_code: valid code + short password / verify / 400 PASSWORD_WEAK, no user, code NOT consumed · R7
  - test_verify_email_taken_no_double_provision: email already global user + valid code / verify / 409 EMAIL_TAKEN, no 2nd user, link+code intact · R8
  - test_verify_seat_cap_blocks: tenant at cap + valid code / verify / 403 SEAT_CAP, no user · R12
  - test_redeem_revoked_link: revoked link / redeem+verify / 409 INACTIVE · R9
  - test_redeem_expired_link: expires_at past / redeem / 410 INVITE_EXPIRED · R10
  - test_redeem_unknown_token: bogus token / redeem / 404 NOT_FOUND · R11
  - test_concurrent_verify_provisions_once: valid code / 2 concurrent verify (asyncio.gather) / exactly one 201 + one user; other 400 INVALID · R14,M7
  - test_verify_ignores_injected_identity: body injects tenant_id(B)/role SUPERADMIN / verify / user is MEMBER of link.tenant(A), injected fields unread · R15
  - test_rate_limit_both_steps_fail_open: exhausted limiter→429 Retry-After; Redis down→proceeds (fail-open) · M11
  - test_create_revoke_join_audited_fail_open: audit writer raises / create+revoke+verify still return success · M12
  - test_code_never_in_response_or_plaintext: no endpoint body contains the code; DB stores only the keyed hash · M9
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
Green-bar (component gateway): `pytest (Makefile:test / ci.yml 'Tests' step)` — verify `cd apps/gateway && uv run pytest`. New suite: `apps/gateway/tests/invite_by_domain/test_invite_by_domain.py`.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/tenants/` `apps/gateway/src/gateway/core/error_catalog.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/migrations/versions/` `apps/gateway/src/gateway/main.py` `apps/gateway/tests/invite_by_domain/` `apps/gateway/tests/migrations/test_migrations.py`
Scope note: settings live in `core/config.py` (not settings.py) and router wiring in `main.py` (not app.py) — corrected from the pre-freeze §0 guess against the real tree at build. `tests/migrations/test_migrations.py` is the SANCTIONED additive-table manifest (EXPECTED_TABLES) — the same maintenance the invites/tenant_domain_claims/seat_membership_events tables already carry; a manifest append for the 2 new tables, no assertion relaxed.
Strategy (ordered batches): 1. Migration (domain_invite_links + domain_invite_redemptions, +indexes) off head c2e5a9d1b7f4 + working downgrade. 2. `DomainInviteLink` entity + 3 new ErrorSpecs + settings knobs. 3. `DomainInviteLinkRepository` (create-supersede tx · list-active · revoke tenant-scoped · issue/upsert redemption · load-link+redemption FOR UPDATE · provision mirroring InviteRepository.accept). 4. Use-cases: CreateDomainInviteLink (eligibility gate), List, Revoke, IssueRedemptionCode (fail-open email), VerifyRedemptionAndProvision. 5. Two routers (authenticated admin + public redeem) mounted in app.py + redeem rate-limiter wiring. 6. Reuse member_verify_code.py + email template. Keep every new use-case importing the concrete repo (sibling-consistency with invite_use_cases.py — no new Protocol port).
Persona (required): generic (no `.add/personas/` file fits a tenants-security task yet; SOUL.md + the member-invite-issuance/member-verified-recognition security stance govern).
Spawn isolation (default): isolation "worktree" for any subagent build/verify spawn.
Known-problem fixes: MissingGreenlet (capture ORM attrs BEFORE commit/rollback — InviteRepository.accept trap) → capture first · celery/redis lock trap (n/a) · email domain normalization drift → reuse task-4 `_caller_email_domain` verbatim, never re-derive · increment-before-raise ordering (task-4 concurrency) → bump+commit in the same locked tx before the use-case raises · scope snapshot poisoning → clean `.coverage`/`.pytest_cache` before the gate.
Strategy actually used: <fill at VERIFY>
Safety rule (feature-specific): the read→code-compare→increment/provision path runs in ONE transaction under SELECT … FOR UPDATE on the link + redemption rows, so concurrent redeems serialize and the code is single-use (exactly-once provision).
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 38 passed (orchestrator-rerun independently, post-fix: 29 original + 9 hardening = 6 malformed-addr-spec params + 1 step-2 re-check + cross-tenant-eligibility + owner-verified-branch); affected-suite sweep green pre-fix (56 migrations+invite_by_domain, 147 domain_capture+member_invite_*+plan_seat_cap, 11 tenants); the fix is isolated to `_redeemer_email_domain` (siblings don't import it)
- [x] coverage did not decrease — 95% on the 4 new modules (>90% target); uncovered = defensive-only (create IntegrityError retry, invalid-domain-on-create)
- [x] no test or contract was altered during build — §3 FROZEN untouched; the only test edit is the sanctioned additive EXPECTED_TABLES manifest append (no assertion relaxed)
- [x] the green was EARNED, not gamed — refute-read EARNED (self + 2 adversarial agents); DB-effect asserts, real race, hostile-input parametrization, no vacuous/stubbed logic
- [x] concurrency / timing of the risky operation is safe — link-row FOR UPDATE (by token_hash) serializes racing verifies (exactly-once; loser reads the deleted redemption → 400 INVALID, proven by a real asyncio.gather race asserting exactly one users row); atomic-SQL `attempt_count+1` committed-before-raise → exact cap of 5; lens B CLEAR (no lock inversion, per-request committing sessions confirmed)
- [x] no exposed secrets, injection openings, or unexpected dependencies — code at rest keyed-HMAC only, never plaintext/logged/in-body/audit; constant-time compare; eligibility SQL parameterized; no `uv add`; lens A + lens B CLEAR. ONE lens-B finding (unvalidated redeemer addr-spec → off-domain code email) FOUND → FIXED at source (`_redeemer_email_domain` fails closed on multi-`@`/empty-local/whitespace-control) → re-verified (7 red cases now green), NOT risk-accepted
- [x] layering & dependencies follow CONVENTIONS.md — router→use-case→repo→ORM; concrete-repo import (sibling-consistent with invite_use_cases.py); no new Protocol port
- [ ] a person reviewed and approved the change — security HARD-STOP: presenting the gate to Tin (finding found+fixed+re-verified)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [ ] a member-verified admin POSTing create gets a 201 with a 32-byte plaintext token exactly once, and the DB row stores ONLY its SHA256 hash (never plaintext) — confirmed by test_eligible_admin_mints_link + a code-read of the create use-case/repo
- [ ] an admin whose tenant is NOT member/owner-verified on the domain gets 403 ERR_DOMAIN_INVITE_NOT_ELIGIBLE and NO row is written — test_create_unverified_domain_forbidden
- [ ] redeem step-1 with a non-@domain email returns 403 DOMAIN_INVITE_DOMAIN_MISMATCH and sends NO email / writes NO redemption row — test_redeem_rejects_non_domain_email_no_email
- [ ] redeem step-1 issues the code to the @domain mailbox, storing only the Option-A keyed hash + ~15m expiry + attempt_count=0 (upsert resets) — test_redeem_issues_code_to_domain_mailbox + test_reissue_supersedes_and_resets
- [ ] redeem/verify with the correct code provisions exactly ONE MEMBER (role=MEMBER, auth_method='password'), appends one seat 'joined' event, deletes the redemption row, leaves claim/resolve_verified_tenant unchanged — test_verify_correct_code_provisions_member
- [ ] two concurrent correct-code verifies provision the user EXACTLY once (one 201, one 400 INVALID) — test_concurrent_verify_provisions_once genuinely races via asyncio.gather under FOR UPDATE
- [ ] wrong code increments attempt_count (persisted) and at cap(5) invalidates → 429 TOO_MANY; expired code → 410; weak password → 400 without consuming the code; seat cap → 403; already-registered email → 409 with link+code intact — the R4/R5/R6/R7/R8/R12 tests
- [ ] injected tenant_id/role in the verify body are dropped (extra="ignore") — provisioned user is a MEMBER of link.tenant — test_verify_ignores_injected_identity
- [ ] no endpoint response body ever contains the code; step-1 gives the SAME 202 whether or not the email is already a user (no account-existence oracle) — test_code_never_in_response_or_plaintext + test_step1_no_account_existence_oracle
- [ ] rate-limit fires BEFORE DB IO and fails OPEN on Redis outage on both public steps — test_rate_limit_both_steps_fail_open
- [x] resolve_verified_tenant + ClaimStatus enum/constraint/indexes + the per-email invite endpoints are byte-identical (git diff empty) — confirmed by the orchestrator diff check
- [x] GREEN-BAR (component gateway) met: `pytest (Makefile:test / ci.yml 'Tests' step)` — `cd apps/gateway && env -u GATEWAY_TEST_DATABASE_URL uv run pytest tests/invite_by_domain/test_invite_by_domain.py` → 38 passed (orchestrator-run 2026-07-20, post-fix); affected-suite sweep green pre-fix

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced: both routers mounted in `main.py` (import + include_router, orchestrator-diffed); the 5 use-cases each constructed in a router handler; the repository is the sole `_get_repo` return on both routers; the 3 ErrorSpecs each `.exc()`-raised in a router except-arm; the 5 config knobs each read at a call-site (`domain_invite_link_ttl_days`, `_code_ttl_minutes`, `_code_max_attempts`, `_redeem_rpm`, `_verify_rpm`); the 2 ORM rows used by the repo + migration. Confirmed by full read of the 4 new src files + routers.
- [x] DEAD-CODE (code) — no orphaned symbol: `RedeemState`, `is_domain_eligible`, `resolve_active_link_by_token_hash`, `load_link_and_redemption_for_update`, `bump_redemption_attempt`, `invalidate_redemption`, `provision_member_and_consume`, `_redeemer_email_domain` all referenced on a live path (traced each).
- [x] SEMANTIC (prose / non-code) — the frozen §3 contract + §2 scenarios read in full against the impl: every endpoint/status/error-code maps 1:1 (one contract-prose gap surfaced — R1 `ERR_FORBIDDEN` → the reused gate's real `ERR_AUTH_FORBIDDEN`; SHAPE=403 forbidden honored; recorded in §7).

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every §3 anchor resolves in the current tree: `InviteRepository.accept` provisioning shape (mirrored, not touched), `member_verify_code.py` generate/hash/verify (imported verbatim), `InvitePublicRateLimiter` + `resolve_trusted_client_ip` + `record_audit` (reused), `require_permission(MEMBERS_MANAGE)` (reused), `assert_seat_available`/`SeatMembershipEventRow` (reused), the reused error specs — all confirmed present at build via serena reads + the green suite exercising each path.
- [x] anchors that MOVED since the pre-freeze §0 guess, named not silent: settings live in `core/config.py` (not `settings.py`); router wiring in `main.py` (not `app.py`) — §5 scope corrected accordingly. `resolve_verified_tenant` + ClaimStatus enum/indexes + per-email invite endpoints confirmed byte-identical (git diff empty).

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: self (orchestrator) + 2 independent adversarial add-verify agents (lens A authz/isolation, lens B crypto/concurrency) · adversarially checked: the suite asserts DB effects not just status (token_hash=sha256≠plaintext, role='member', seat 'joined' event, redemption consumed, claim status/verified_at UNCHANGED), a real `asyncio.gather` two-request race asserting exactly-one users row + loser 400, 4 parametrized hostile domain-mismatch emails (subdomain/IP/unicode/other-domain) asserting no-row+no-email, weak-password-keeps-code, cap-invalidates, replay-after-success fails, injected tenant_id+role=superadmin dropped, rate-limit both steps + Redis fail-open, audit fail-open, code-never-in-response/plaintext. No vacuous asserts, no stubbed domain/seat/code logic, independent hash oracle. A broken impl (wrong tenant, non-MEMBER role, leaked/plaintext code, double-provision, cross-domain join) would fail a specific assertion.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: 2 independent add-verify agents (lens A authz/isolation · lens B crypto/concurrency) + orchestrator synthesis
1. Security: CLEAR (after fix) — lens A found NO authz/tenant-isolation/domain-enforcement/enumeration hole (tenant_id from identity never body; role hardcoded 'member'; domain re-checked both steps; domain_capture byte-untouched). Lens B found ONE finding — unvalidated redeemer addr-spec (`x@evil.com@acme.com` passed the last-`@`-segment gate → code emailed off-domain, relying only on incidental MTA routing). HARD-STOP honored: FIXED at source (`_redeemer_email_domain` fails closed) + 7 red regression cases now green; re-verified CLEAR, not auto-passed.
2. Concurrency: CLEAR — lens B confirmed link-row FOR UPDATE serialization = exactly-once, atomic-SQL increment committed-before-raise = exact cap, no lock inversion, password-first is not a code oracle.
3. Architecture: CLEAR — router→use-case→repo→ORM layering; concrete-repo sibling-consistency; additive-only; reused provisioning/rate-limit/audit/crypto primitives verbatim; no auto-join path touched.
Verdict: PASS (security HARD-STOP resolved by fix, not acceptance)
Residue: none blocking. Observe deltas: (a) [SPEC] reconcile §3/R1 ERR_FORBIDDEN naming ↔ reused gate's real ERR_AUTH_FORBIDDEN (impl uses the real code; no ERR_FORBIDDEN spec exists — a prose typo, SHAPE honored). (b) [SPEC/monitor] step-1 re-issue resets attempt_count — bounded (rotates code to a value only the real mailbox sees + per-IP limit + seat cap); monitor per-(link,email) re-issue rate. (c) MemberVerify/DomainRedeem request models could add extra="forbid" on create (already extra="forbid"); redeem stays extra="ignore" by contract.
Binding: advisory — security (HARD-STOP floor: human approval required at the gate)

### GATE RECORD
Reported: yes — the security gate report (banner/ARC/lenses/finding-fix/evidence) rendered before this outcome
Outcome: PASS — security HARD-STOP cleared: the one adversarial finding (off-domain code email via unvalidated addr-spec) was FIXED at source + re-verified (38/38 green), never risk-accepted; 2 independent adversarial lenses CLEAR + orchestrator read + earned-green EARNED.
component: gateway · expected green-bar: pytest (Makefile:test / ci.yml 'Tests' step) · verify: cd apps/gateway && uv run pytest
Reviewed by: Tin Dang · date: 2026-07-20

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose two-step mailbox-code redeem; rejected bearer-link + domain-check only (rejected at UDD — a leaked link would let any known @domain address join with no mailbox proof) · SSO-authenticated redeem (rejected — needs a pre-existing @domain identity; doesn't fit self-serve signup)
- [human] freeze — froze §3 @ v1 (approved by Tin 2026-07-20)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

