# TASK: Member invite issuance

slug: member-invite-issuance · created: 2026-07-03 · stage: production
milestone: team-member-invite
autonomy: auto
phase: done

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - NEW `InviteRow` ORM model (`apps/gateway/src/gateway/tenants/infrastructure/orm.py`) — a new
    `invites` table: `id`, `tenant_id` FK, `email` (NOT globally unique — scoped uniqueness is
    (tenant_id, email) WHERE status='pending', mirrors the re-invite-replaces-pending rule), `role`,
    `token_hash` (SHA-256, mirrors API-key precedent), `status` (pending/accepted/revoked CHECK),
    `expires_at`, `invited_by_user_id` FK, `created_at`.
  - NEW migration adding the `invites` table (mirrors `3fc2328e5e82_platform_tenant_seed.py`'s
    additive-migration shape — new table, no existing-table alteration needed for THIS task).
  - NEW `InviteRepository` (`tenants/infrastructure/repository.py` sibling or new
    `tenants/infrastructure/invite_repository.py`) — create (upsert-on-re-invite: expire any existing
    pending row for (tenant_id, email) then insert fresh), list-by-tenant, revoke-by-id.
  - NEW `CreateInviteUseCase`/`ListInvitesUseCase`/`RevokeInviteUseCase`
    (`tenants/application/invite_use_cases.py`, sibling to existing `users_use_cases.py`) — create
    MUST call the SAME escalation-ceiling check as `AssignUserRoleUseCase`
    (`tenants/application/users_use_cases.py:26-28` `_ADMIN_ASSIGNABLE` constant, `:64-91`
    `AssignUserRoleUseCase.execute`'s ceiling logic) rather than reimplementing it — extract/share the
    predicate, do not fork it.
  - NEW router `tenants/api/invites_router.py` (sibling to `users_router.py`) —
    `POST /admin/invites` (create, owner/admin only), `GET /admin/invites` (list pending, same
    guard), `DELETE /admin/invites/{invite_id}` (revoke, same guard). Mounted in `main.py` alongside
    the existing `users_router`/`platform_users_router` mounts.
  - NEW error-catalog entries (`core/error_catalog.py`, sibling to existing `AUTH_EMAIL_TAKEN`
    `:129-131` and the `users_router.py` 422 pre-check shape `:121-129`) — an invite-specific
    escalation-rejection code (mirroring the existing role-assignment one) and any invite-not-found/
    already-revoked codes list-side.

Context (working folder): `.add/milestones/team-member-invite/MILESTONE.md` (this task's owning
  milestone — Scope/Shared-decisions/Shared-contracts sections just drafted and reviewed this
  session); `.add/GLOSSARY.md` (existing API-key hash-at-rest precedent, `budget_usd_monthly` vs.
  `monthly_budget_usd` naming-collision precedent as a model for precise naming discipline here too).

Honors (patterns / conventions):
  - Escalation ceiling REUSE, not re-derivation — `users_use_cases.py:26-28,64-91`.
  - Superadmin hard-exclusion pre-check shape — `users_router.py:121-129`,
    `platform_users_router.py:174-179` (422 before any use-case runs).
  - Token-as-opaque-hashed-secret precedent (NOT a JWT) — GLOSSARY "API key" entry: SHA-256 hash at
    rest, 32-byte CSPRNG secret, plaintext shown/usable exactly once.
  - Anti-enumeration precedent — `.add/tasks/teams-add-by-email/TASK.md` §1 (rejected building any
    surface that discloses cross-tenant user existence to a tenant-scoped caller); this task's
    invite-CREATE path must not distinguish "email already registered elsewhere" from "brand new
    email" in its response.
  - Server-side-only identity resolution precedent — `.add/tasks/device-approval-flow/TASK.md` §3
    AUTHZ RULE ("binding always from the verified source, never client-supplied body fields") —
    informs the (separate, sibling) acceptance task, but the issuance task's create/list/revoke
    endpoints must equally never trust a client-supplied tenant_id/role override beyond what the
    caller's OWN identity + the escalation ceiling permit.

Anchors the contract cites: `AssignUserRoleUseCase`/`_ADMIN_ASSIGNABLE` (users_use_cases.py),
  `UserRow`/email-uniqueness (orm.py:79), the existing `AUTH_EMAIL_TAKEN` error shape (router.py:50-51,
  error_catalog.py:129-131), `require_permission`/`MEMBERS_MANAGE` gating pattern (authz.py).

Issues/Risks (→ feed §1):
  - Escalation-ceiling DRIFT risk: if invite-creation's role-check is hand-rolled instead of reusing
    `AssignUserRoleUseCase`'s predicate, a future change to one ceiling silently desyncs from the
    other — a privilege-escalation-shaped bug, not a style nit (per team-member-invite MILESTONE.md
    Shared decisions).
  - Cross-tenant email-enumeration tension: signup's PUBLIC `AUTH_EMAIL_TAKEN` 409 already leaks
    "is this email registered anywhere" to anyone unauthenticated; this task must NOT compound that
    with a second, tenant-owner-facing oracle at invite-CREATE time (identical response whether the
    email is brand-new or belongs to a user in a different tenant) — the milestone doc's resolution:
    defer any necessary disclosure to the (separate) acceptance task, where the audience is narrower
    (token holder only).
  - Token format: opaque random value hashed at rest (API-key precedent), NOT a JWT — a JWT-encoded
    invite could never be durably, instantly revoked, defeating the "revoke kills it immediately"
    exit criterion.
  - Re-invite-replaces-pending must be atomic (expire-old + insert-new in one transaction) to avoid a
    window with two simultaneously-valid tokens for the same (tenant, email).

Related intent: `.add/milestones/team-member-invite/MILESTONE.md` goal + Scope + Shared decisions
  (drafted and independently verified this session) — GLOSSARY term to add at this milestone's fold:
  "invite (pending invite)". Originating request: Tin identified, via a direct product question
  about personal-vs-enterprise-tenant users, that password-auth tenants have zero path to add a
  colleague today; confirmed via code trace (zero invite/join mechanism found anywhere).

Ground SHA: 9740f21

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Member invite issuance — a tenant owner/admin creates, lists, and revokes tenant-scoped
  invites so a new colleague can join their tenant by email + role, with no SSO/domain-mapping
  required. Issuance ONLY (create/list/revoke) — the public preview+accept flow that turns an invite
  into a real `UserRow` is the sibling `member-invite-acceptance` task; this task owns the invite
  record shape + token lifecycle + escalation-ceiling parity per MILESTONE.md's Shared/risky contracts.

Framings weighed: Extract `AssignUserRoleUseCase`'s escalation-ceiling check into ONE shared
  predicate both use cases call (chosen — verified behavior-preserving: `users_router.py:144-145`
  calls `AUTH_FORBIDDEN.exc()` with NO `detail`, so it never surfaces `EscalationForbiddenError`'s
  internal message, meaning the extraction changes zero observable HTTP responses for the existing
  role-reassignment endpoint; the only way "same ceiling" is a structural guarantee rather than a
  promise two copies can silently drift apart from) · re-derive an equivalent `_ADMIN_ASSIGNABLE`-
  alike check inline in the new use case (rejected — exactly the escalation-ceiling DRIFT risk §0
  GROUND names as a privilege-escalation-shaped bug, not a style nit) · route invite-creation THROUGH
  `AssignUserRoleUseCase` itself (rejected — its signature and self-guard are shaped around an
  EXISTING target user_id, which an invite, by definition, does not have) · re-invite as an atomic
  revoke-old-then-insert-new backed by a DB partial unique index on (tenant_id, email) WHERE
  status='pending' (chosen — mirrors the shipped `device_authorizations` precedent exactly, so "no
  two live tokens" is DB-enforced, not just application-promised) over an in-place token/expiry
  UPSERT (rejected — destroys the superseded-row audit trail and complicates the accept-flow's token-
  to-row matching) or an outright 409 forcing a manual revoke-then-create (rejected — MILESTONE.md is
  explicit: "no stacked pending rows, no separate resend surface") · escalation-ceiling-violation and
  superadmin-target rejections REUSE the existing `AUTH_FORBIDDEN` (403) / `PAYLOAD_INVALID` (422)
  codes verbatim (chosen — true byte-identical parity, the MILESTONE's own explicit bar) over minting
  a new invite-flavored escalation code as §0 GROUND's literal phrasing suggests (rejected — a
  differently-named code for the identical situation would make the two ceilings LOOK different to
  any client or observability dashboard, undermining the very parity this task exists to protect).
Must:
<must>
  - **[M1]** CREATE (`POST /admin/invites`, MEMBERS_MANAGE-gated): an owner may invite any of the 6
    self-service roles; an admin may invite ONLY {operator, billing_admin, viewer, member} — enforced
    by ONE shared predicate (`assert_role_within_ceiling`, extracted verbatim from
    `AssignUserRoleUseCase.execute`'s existing superadmin-guard + escalation-guard lines,
    `users_use_cases.py:71-82`) that BOTH use cases call — never a second, hand-rolled copy of
    `_ADMIN_ASSIGNABLE`.
  - **[M2]** `role == "superadmin"` is rejected BEFORE any use case runs, byte-identical in shape (422,
    "Unknown role: {role}") to `users_router.py:121-129`'s existing pre-check — for every caller,
    regardless of role.
  - **[M3]** The invite token is a 32-byte CSPRNG opaque value (`secrets.token_urlsafe(32)` — the
    codebase's existing, consistent pattern: agent_oauth device/access/refresh codes, API-key
    secrets, and OIDC state tokens all generate this way), hashed at rest with the EXISTING
    `Sha256SecretHasher` (`keys/infrastructure/sha256_hasher.py`, SHA-256 hex digest) — never logged,
    never stored in plaintext; returned in the 201 body exactly once and never retrievable again
    afterward (mirrors the API-key GLOSSARY precedent: "plaintext shown exactly once at creation").
  - **[M4]** The target email is stored lowercased (mirrors `UserRow`'s `.lower()` discipline,
    `orm.py:72`); `role` is persisted as one of the 6 self-service values — enforced BOTH by the M2
    pre-check AND by a DB CHECK constraint that excludes 'superadmin' entirely (stricter than
    `users_role_check`, which must permit it for real platform-tenant rows).
  - **[M5]** Re-inviting the same (tenant_id, email) while a pending invite exists ATOMICALLY
    supersedes it: the prior pending row flips to `revoked` and a fresh pending row (new token, new
    expiry) is inserted, in one transaction. At most one pending row per (tenant_id, email) ever
    exists — a DB partial unique index enforces this, not just application logic. A genuine
    concurrent double re-invite is resolved by one bounded retry (CLAUDE.md design-for-failure) —
    never a lost update, never two simultaneously-valid tokens for the same pair.
  - **[M6]** CREATE never distinguishes — in status code, response shape, or timing-sensitive
    behavior — whether the target email is brand-new or already belongs to a user in a DIFFERENT
    tenant (the `teams-add-by-email` anti-enumeration stance, reaffirmed by MILESTONE.md): both return
    an identical 201 and create the invite the same way; CREATE never runs a cross-tenant `UserRow`
    query.
  - **[M7]** CREATE rejects (409) ONLY when the email already belongs to an existing user in the
    CALLER's OWN tenant — a tenant-scoped check (`WHERE tenant_id = caller's AND email = :email`),
    safe to disclose because the caller already sees this roster via `GET /admin/users`.
  - **[M8]** LIST (`GET /admin/invites`, same gate): returns every invite currently `status='pending'`
    in the caller's own tenant (id, email, role, status, expires_at, created_at, invited_by_user_id) —
    NEVER the token or its hash; no pagination (a bounded, ephemeral set, mirrors
    `ListTenantUsersUseCase`).
  - **[M9]** REVOKE (`DELETE /admin/invites/{invite_id}`, same gate): flips a caller-tenant-scoped,
    currently-`pending` invite to `revoked` inside one row-locked (`SELECT ... FOR UPDATE`)
    transaction — 204 on success — immediately and durably invalidating its token for the (separate)
    accept flow. Any current owner/admin in the tenant may revoke ANY pending invite regardless of who
    created it (tenant-scoped, not creator-scoped — identical in spirit to how any owner/admin may
    reassign any user's role today).
  - **[M10]** The three endpoints share the IDENTICAL `require_permission(Permission.MEMBERS_MANAGE)`
    dependency `users_router.py` already uses — no new authorization primitive.
  - **[M11]** The new migration is PURELY additive: one new `invites` table; no existing table is
    altered.
  - **[M12]** CREATE and REVOKE each fire an audit event (`invite.create` / `invite.revoke`),
    fire-and-forget / fail-open, mirroring `users_router.py`'s existing
    `record_audit(...action="user.role_assign"...)` pattern (metadata: target email + role for create;
    invite_id for revoke). LIST is not audited — mirrors `list_users`, which is also unaudited (the
    heavier `platform.user.list` audit belongs only to the superadmin CROSS-tenant read, a materially
    different trust boundary this task does not touch).
</must>
Reject:
<reject>
  - **[R1]** Missing/invalid bearer JWT -> "ERR_AUTH_INVALID_TOKEN" (401, reused AUTH_TOKEN_MISSING/AUTH_TOKEN_INVALID)
  - **[R2]** Caller's role lacks MEMBERS_MANAGE (member/operator/billing_admin/viewer) -> "ERR_AUTH_FORBIDDEN" (403, reused)
  - **[R3]** `role` does not parse as a `Role` -> "ERR_PAYLOAD_INVALID" (422, reused, byte-identical detail to users_router.py)
  - **[R4]** `role == "superadmin"` -> "ERR_PAYLOAD_INVALID" (422, reused, same pre-check shape, BEFORE any use case runs)
  - **[R5]** Admin invites `owner` or `admin` (outside `_ADMIN_ASSIGNABLE`) -> "ERR_AUTH_FORBIDDEN" (403, reused — the shared escalation-ceiling predicate)
  - **[R6]** `email` fails `EmailStr` validation -> "ERR_PAYLOAD_INVALID" (422, reused, standard FastAPI/Pydantic path)
  - **[R7]** `email` already belongs to an existing user in the CALLER's own tenant -> "ERR_INVITE_EMAIL_ALREADY_MEMBER" (409, NEW)
  - **[R8]** REVOKE: `invite_id` does not resolve in the caller's tenant (unknown OR belongs to a different tenant — no distinguishing oracle) -> "ERR_INVITE_NOT_FOUND" (404, NEW)
  - **[R9]** REVOKE: the resolved invite's status is not `pending` (already accepted or already revoked) -> "ERR_INVITE_NOT_PENDING" (409, NEW)
</reject>
After:
<after>
  - CREATE success: exactly one `invites` row with status='pending' exists for (tenant_id, email); any
    PRIOR pending row for that pair is now 'revoked'; the plaintext token was returned exactly once
    and is unrecoverable from the API from this point on; no other tenant's rows were read or written;
    an `invite.create` audit event was fired.
  - REVOKE success: the targeted row's status='revoked', durably, before the 204 is returned; its
    token can never subsequently be accepted; an `invite.revoke` audit event was fired.
  - Any Reject: the `invites` table is completely unchanged — no partial row, no half-applied status
    flip, no token generated-but-undiscoverable, no audit event fired.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Invite expiry = 7 days, HARDCODED (not a new per-tenant Settings/env knob) — lowest confidence
    because MILESTONE.md itself flags this as an unconfirmed, deliberately-unfrozen product default
    ("propose 7 days... confirm or override... rather than treat 7d as frozen") and this drafting pass
    has no channel to put that question to Tin directly; if wrong: swapping the constant is a one-line
    Build change if only the number is wrong, or a small scope addition (a Settings knob + a nullable
    tenants column) if Tin instead wants it tenant-configurable — either way NOT a contract-shape
    change (no request field exposes a caller-supplied TTL either way), so nothing here blocks
    freezing the rest of the shape.
  ⚠ Extracting `assert_role_within_ceiling` out of `AssignUserRoleUseCase.execute` touches a
    previously-shipped, FROZEN@v1 file (`users_use_cases.py`) from within a DIFFERENT task — lowest
    confidence on whether that crosses this task's own change-scope even though I verified it is
    strictly behavior-preserving (traced `users_router.py:144-145`: `AUTH_FORBIDDEN.exc()` takes no
    `detail`, so the router never surfaces `EscalationForbiddenError`'s internal message — reordering/
    bundling the superadmin+escalation checks changes zero observable HTTP responses for the existing
    role-reassignment endpoint); if wrong (reviewer wants zero touches to `users_use_cases.py`): fall
    back to duplicating `_ADMIN_ASSIGNABLE` + the two guard lines into `invite_use_cases.py` with a
    prominent "MUST stay byte-identical to users_use_cases.py:26-28" comment — safe, just reintroduces
    the exact drift risk §0 GROUND warns about; cost = one file, an ongoing manual-sync burden rather
    than a one-time refactor.
  - [ ] No rate-limit is needed on any of the 3 endpoints — confirmed by precedent:
    `PUT /admin/users/{id}/role` (same gate, same privileged-caller-only shape) carries none anywhere
    in the codebase; device-approval-flow's per-user 429 exists specifically because ANY authenticated
    tenant member (not just owner/admin) can hit that surface to guess user_codes — a threat model
    that does not apply here (caller is already owner/admin-privileged; invite_id/email are not bearer
    secrets being guessed).
  - [ ] `EmailStr` (matching `SignupRequest`/`LoginRequest`, not a plain `str`) is the right validator
    for the invite's email — noting the known project gotcha (EmailStr rejects `.local` TLDs, memory:
    env-npx-shim-gotcha) purely as a Build/Tests heads-up, not a product ambiguity.
  - [ ] `invites.tenant_id` FK uses `ON DELETE CASCADE` (mirrors the closer `device_authorizations`
    ephemeral-record precedent) rather than `RESTRICT` (`UserRow`'s permanent-identity-record
    precedent) — low-stakes either way since no tenant-deletion feature exists in this codebase today.
  - [ ] A demoted former admin loses the ability to create/list/revoke invites only once their JWT is
    re-issued (role is a point-in-time JWT claim, never re-checked against the DB per request) — this
    is existing, tenant-scoped (not creator-scoped) permission behavior identical to `/admin/users`
    today, not a new question this task introduces.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Owner invites a co-owner (ceiling: owner may grant any of the 6 tiers)   # M1
  Given a logged-in OWNER of tenant T (holds MEMBERS_MANAGE)
  When they POST /admin/invites { email: "new@co.io", role: "owner" }
  Then the response is 201 with role "owner" and status "pending"
  And exactly one invites row exists for (T, "new@co.io") with status='pending'

Scenario: Admin invites an allowed tier (ceiling: admin may grant operator/billing_admin/viewer/member)   # M1
  Given a logged-in ADMIN of tenant T
  When they POST /admin/invites { email: "new@co.io", role: "viewer" }
  Then the response is 201 with role "viewer"

Scenario: Admin cannot invite owner or admin (escalation ceiling)   # M1, R5
  Given a logged-in ADMIN of tenant T
  When they POST /admin/invites { email: "x@co.io", role: "owner" }, and separately role: "admin"
  Then each response is 403 "ERR_AUTH_FORBIDDEN"
  And no invites row is created for either attempt

Scenario: Inviting role "superadmin" is always rejected, before any use case runs   # M2, R4
  Given a logged-in OWNER of tenant T (the highest self-service tier)
  When they POST /admin/invites { email: "x@co.io", role: "superadmin" }
  Then the response is 422 "ERR_PAYLOAD_INVALID" with the SAME "Unknown role: 'superadmin'" detail
    users_router.py's own pre-check produces
  And no invites row is created

Scenario: Unparseable role literal is rejected   # R3
  Given a logged-in OWNER of tenant T
  When they POST /admin/invites { email: "x@co.io", role: "wizard" }
  Then the response is 422 "ERR_PAYLOAD_INVALID"
  And no invites row is created

Scenario: Malformed email is rejected   # R6
  Given a logged-in OWNER of tenant T
  When they POST /admin/invites { email: "not-an-email", role: "member" }
  Then the response is 422 "ERR_PAYLOAD_INVALID"
  And no invites row is created

Scenario: Caller without MEMBERS_MANAGE cannot create an invite   # R2
  Given a logged-in MEMBER (or operator/billing_admin/viewer) of tenant T
  When they POST /admin/invites { email: "x@co.io", role: "member" }
  Then the response is 403 "ERR_AUTH_FORBIDDEN"
  And no invites row is created

Scenario: Missing or invalid bearer token is rejected   # R1
  Given no Authorization header (or an invalid/expired one)
  When a client POSTs /admin/invites { email: "x@co.io", role: "member" }
  Then the response is 401 "ERR_AUTH_INVALID_TOKEN"
  And no invites row is created

Scenario: Token is a CSPRNG secret, hashed at rest, shown exactly once   # M3
  Given a logged-in OWNER of tenant T
  When they POST /admin/invites { email: "new@co.io", role: "member" }
  Then the 201 body includes a `token` field (a 32-byte-derived opaque string)
  And the stored invites row's token_hash is the SHA-256 hex digest of that token, NOT the token itself
  And GET /admin/invites for the same tenant never includes a `token` or `token_hash` field for that row

Scenario: Email is stored lowercased; the DB itself excludes 'superadmin' as a persisted role   # M4
  Given a logged-in OWNER of tenant T
  When they POST /admin/invites { email: "MixedCase@Co.IO", role: "member" }
  Then the response is 201 and the stored invites row's email is "mixedcase@co.io" (lowercased)
  And re-inviting "mixedcase@CO.io" (different casing, same address) matches and supersedes THAT SAME
    row under M5's atomicity rule, not a second parallel row
  And the invites table's own CHECK constraint would reject an attempted INSERT with role='superadmin'
    even if application code were bypassed — a second, DB-level line of defense behind M2's pre-check

Scenario: Email already belongs to a member of the CALLER's own tenant   # R7
  Given a logged-in OWNER of tenant T, and an existing user u with email "u@t.io" already IN tenant T
  When the owner POSTs /admin/invites { email: "u@t.io", role: "member" }
  Then the response is 409 "ERR_INVITE_EMAIL_ALREADY_MEMBER"
  And no invites row is created

Scenario: Email belongs to a user in a DIFFERENT tenant — indistinguishable from brand-new (anti-enumeration)   # M6
  Given a logged-in OWNER of tenant A, and an existing user in a DIFFERENT tenant B with email "b@b.io"
  When the owner of A POSTs /admin/invites { email: "b@b.io", role: "member" }
  Then the response is 201 — IDENTICAL in shape to inviting a brand-new, never-seen-anywhere email
  And a pending invites row is created in tenant A for "b@b.io" (tenant B's user is untouched)
  And this endpoint never runs a query that looks outside tenant A's own users

Scenario: Re-inviting the same pending email issues a fresh token and kills the old one   # M5
  Given tenant T has a pending invite for "x@co.io" with token X1
  When the owner POSTs /admin/invites { email: "x@co.io", role: "member" } again
  Then the response is 201 with a NEW token X2 (X2 != X1)
  And exactly ONE invites row for (T, "x@co.io") has status='pending' (the new one)
  And the OLD row is now status='revoked' — X1 can never subsequently be accepted

Scenario: Concurrent double re-invite for the same pending email never produces two live tokens   # M5 (concurrency edge)
  Given tenant T has a pending invite for "x@co.io"
  When two re-invite requests for the SAME (T, "x@co.io") commit at nearly the same instant
  Then BOTH responses are 201 (each returns its own fresh token; the loser transparently retries once
    and succeeds against the winner's now-committed row)
  And after both complete, exactly ONE invites row for (T, "x@co.io") has status='pending' — never
    zero, never two

Scenario: Listing pending invites   # M8
  Given tenant T has two pending invites and one already-revoked invite
  When the owner GETs /admin/invites
  Then the response is 200 with exactly the two pending invites (id, email, role, status, expires_at,
    created_at, invited_by_user_id)
  And neither list item includes a `token` or `token_hash` field
  And the already-revoked invite does NOT appear

Scenario: Owner/admin revokes a pending invite   # M9
  Given tenant T has a pending invite I with token X
  When the owner sends DELETE /admin/invites/{I.id}
  Then the response is 204
  And I.status is now 'revoked', durably, before the response returns
  And token X can never subsequently be accepted

Scenario: Any current owner/admin may revoke an invite created by someone else (tenant-scoped, not creator-scoped)   # M9 (edge)
  Given admin A created a pending invite I in tenant T, and admin A has since been demoted to member
  When a DIFFERENT current owner/admin of tenant T sends DELETE /admin/invites/{I.id}
  Then the response is 204 and I.status is 'revoked'
  And demoted admin A's OWN attempt to revoke, once their JWT reflects the demotion, instead gets 403
    "ERR_AUTH_FORBIDDEN" — the gate is tenant+role-scoped, never creator-scoped

Scenario: Revoking an unknown invite_id   # R8
  Given tenant T has no invite with id "00000000-0000-0000-0000-000000000000"
  When the owner sends DELETE /admin/invites/00000000-0000-0000-0000-000000000000
  Then the response is 404 "ERR_INVITE_NOT_FOUND"
  And no row is modified

Scenario: Revoking an invite belonging to a different tenant is indistinguishable from unknown   # R8 (no oracle)
  Given tenant B has a pending invite I, and tenant A does not
  When an owner of tenant A sends DELETE /admin/invites/{I.id}
  Then the response is 404 "ERR_INVITE_NOT_FOUND" — the SAME code as an unknown id
  And I (in tenant B) is completely unchanged

Scenario: Revoking an already-revoked invite   # R9
  Given a pending invite I in tenant T that was already revoked
  When the owner sends DELETE /admin/invites/{I.id} again
  Then the response is 409 "ERR_INVITE_NOT_PENDING"
  And I's status remains 'revoked' (unchanged)

Scenario: Revoking an already-accepted invite (race with the sibling accept flow)   # R9 (concurrency edge)
  Given a pending invite I in tenant T whose token was JUST accepted (status is now 'accepted')
  When the owner sends DELETE /admin/invites/{I.id} moments later
  Then the response is 409 "ERR_INVITE_NOT_PENDING"
  And I's status remains 'accepted' — revoke can never un-accept a user who already joined
  And the row-level lock guarantees this resolves cleanly regardless of which operation's transaction
    reaches the row microseconds first — never a lost update, never both "succeeding"

Scenario: CREATE and REVOKE each fire an audit event   # M12
  Given a logged-in OWNER of tenant T
  When they POST /admin/invites (success), and later DELETE /admin/invites/{id} (success)
  Then an "invite.create" audit event was recorded for the first action
  And an "invite.revoke" audit event was recorded for the second
  And neither audit call blocked or altered the HTTP response (fire-and-forget, fail-open)

Scenario: Migration is purely additive   # M11
  Given the tenants and users tables and their existing rows, before this migration runs
  When the new migration is applied
  Then a new, empty `invites` table exists with its CHECK/unique constraints
  And every existing table and every existing row is byte-for-byte unchanged
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /admin/invites                                (AUTH: MEMBERS_MANAGE — owner/admin only, M10)
  body: InviteCreateRequest { email: EmailStr, role: string }
  201 -> InviteCreateResponse {
    id: uuid, email: string, role: string, status: "pending",
    expires_at: datetime (ISO-8601 UTC), created_at: datetime (ISO-8601 UTC),
    invited_by_user_id: uuid,
    token: string          # PLAINTEXT — shown exactly once, never retrievable again (M3)
  }
  401 -> { code: "ERR_AUTH_INVALID_TOKEN" }              # R1 — missing/invalid bearer JWT
  403 -> { code: "ERR_AUTH_FORBIDDEN" }                  # R2 (no MEMBERS_MANAGE) or R5 (escalation ceiling)
  422 -> { code: "ERR_PAYLOAD_INVALID" }                 # R3 (bad role) · R4 (role="superadmin") · R6 (bad email)
  409 -> { code: "ERR_INVITE_EMAIL_ALREADY_MEMBER" }     # R7 — email already a user in CALLER's own tenant

GET /admin/invites                                  (AUTH: MEMBERS_MANAGE — owner/admin only, M10)
  200 -> InviteListResponse { invites: InviteResponse[] }
    InviteResponse { id, email, role, status: "pending", expires_at, created_at, invited_by_user_id }
    # status is always "pending" (M8) — accepted/revoked rows never appear; NO token/token_hash field.
  401 -> { code: "ERR_AUTH_INVALID_TOKEN" }              # R1
  403 -> { code: "ERR_AUTH_FORBIDDEN" }                  # R2

DELETE /admin/invites/{invite_id}                   (AUTH: MEMBERS_MANAGE — owner/admin only, M10)
  204 -> (no body)
  401 -> { code: "ERR_AUTH_INVALID_TOKEN" }              # R1
  403 -> { code: "ERR_AUTH_FORBIDDEN" }                  # R2
  404 -> { code: "ERR_INVITE_NOT_FOUND" }                # R8 — unknown OR different tenant (no oracle)
  409 -> { code: "ERR_INVITE_NOT_PENDING" }              # R9 — already accepted or already revoked

Schema: NEW table `invites` (additive migration only — no existing table altered, M11):
  id                  UUID PK (default uuid7())
  tenant_id           UUID NOT NULL FK -> tenants.id ON DELETE CASCADE
  email               TEXT NOT NULL       -- stored lowercased; CHECK (email = lower(email)),
                                           --   mirrors users_email_lowercase_check (orm.py:72)
  role                TEXT NOT NULL       -- CHECK role IN ('owner','admin','operator','billing_admin',
                                           --   'viewer','member') — 'superadmin' EXCLUDED at the DB
                                           --   level (M4; stricter than users_role_check, which must
                                           --   allow it for real platform-tenant UserRow rows)
  token_hash          TEXT NOT NULL UNIQUE   -- SHA-256 hex digest via Sha256SecretHasher.hash(); the
                                              --   plaintext token is NEVER persisted or logged (M3)
  status              TEXT NOT NULL DEFAULT 'pending'   -- CHECK status IN ('pending','accepted','revoked')
  expires_at          TIMESTAMPTZ NOT NULL   -- now() + 7d at insert time (§1 ⚠ top flag — not yet
                                              --   Tin-confirmed; hardcoded constant, no Settings knob)
  invited_by_user_id  UUID NOT NULL FK -> users.id ON DELETE RESTRICT   -- mirrors UserRow's own FK style
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()

  Indexes:
    - UNIQUE PARTIAL (tenant_id, email) WHERE status = 'pending'   -- at most one live pending invite
      per (tenant, email); mirrors uq_device_authorizations_user_code_pending (M5)
    - INDEX (token_hash)   -- the (separate) sibling accept-flow's lookup path; also covered by the
      UNIQUE constraint above, but a plain index keeps that lookup planner-cheap regardless of status

Access pattern:
  CREATE — one transaction:
    1. SELECT 1 FROM users WHERE tenant_id=:caller_tenant AND email=:email   -- tenant-SCOPED ONLY,
       never cross-tenant (M6) — a hit -> 409 ERR_INVITE_EMAIL_ALREADY_MEMBER (R7), BEFORE any write
    2. SELECT * FROM invites WHERE tenant_id=:t AND email=:e AND status='pending' FOR UPDATE
    3. IF found: UPDATE that row SET status='revoked'
    4. INSERT the new pending row (fresh token_hash, fresh expires_at)
    5. On a rare concurrent-INSERT IntegrityError (the M5 partial unique index caught a true
       double-submit racing step 2-4), retry steps 2-4 ONCE in a fresh transaction — by construction
       the retry's step 2 now observes the winner's committed row (bounded retry, CLAUDE.md
       design-for-failure; never a lost update, never a scary 5xx for a legitimate double-click)
  LIST: SELECT * FROM invites WHERE tenant_id=:t AND status='pending' ORDER BY created_at DESC
    (no pagination — a bounded, ephemeral set, mirrors ListTenantUsersUseCase)
  REVOKE — one transaction:
    1. SELECT * FROM invites WHERE id=:invite_id AND tenant_id=:t FOR UPDATE
       -> no row (unknown OR cross-tenant) -> 404 ERR_INVITE_NOT_FOUND (R8, no distinguishing oracle)
    2. status != 'pending' -> 409 ERR_INVITE_NOT_PENDING (R9); ELSE UPDATE status='revoked' -> 204

NEW domain symbols (gateway.tenants.domain.entities, sibling to Role/User/Identity):
  InviteStatus(StrEnum): PENDING="pending" · ACCEPTED="accepted" · REVOKED="revoked"
  Invite(frozen dataclass): id, tenant_id, email, role: Role, status: InviteStatus, expires_at,
    invited_by_user_id, created_at   (token_hash intentionally NOT exposed past the infra boundary)

NEW domain errors (gateway.tenants.domain.errors, sibling to EscalationForbiddenError/UserNotFoundError):
  InviteNotFoundError · InviteNotPendingError · InviteEmailAlreadyMemberError

NEW shared symbol (escalation-ceiling parity — extracted, NOT forked, §1 Framings weighed):
  gateway.tenants.application.users_use_cases.assert_role_within_ceiling(
      *, caller_role: Role, target_role: Role) -> None
    Raises EscalationForbiddenError. Contains EXACTLY the superadmin-guard + admin-ceiling-guard logic
    currently inlined in AssignUserRoleUseCase.execute (users_use_cases.py:71-82) — extracted verbatim
    and called from BOTH AssignUserRoleUseCase.execute (replacing its own inline checks; the
    self-guard stays local to that use case, since it has no meaning for invite-creation) and the NEW
    CreateInviteUseCase.execute. Behavior-preserving for the existing endpoint (§1 ⚠ flag).

NEW application use cases (gateway.tenants.application.invite_use_cases.py, sibling to users_use_cases.py):
  CreateInviteUseCase.execute(*, caller_user_id, caller_role, tenant_id, email, role, now) -> Invite
    (raises EscalationForbiddenError, InviteEmailAlreadyMemberError)
  ListPendingInvitesUseCase.execute(*, tenant_id) -> list[Invite]
  RevokeInviteUseCase.execute(*, invite_id, tenant_id) -> Invite
    (raises InviteNotFoundError, InviteNotPendingError)

NEW infrastructure (gateway.tenants.infrastructure.invite_repository.py, sibling to users_repository.py):
  InviteRepository: create_or_replace(...) · list_pending_by_tenant(*, tenant_id) ·
    get_by_id_and_tenant(*, invite_id, tenant_id) · revoke(*, invite_id) · user_exists_in_tenant(*, tenant_id, email)

NEW router (gateway.tenants.api.invites_router.py, sibling to users_router.py):
  invites_router = APIRouter(prefix="/admin/invites", tags=["invites"])
  Mounted in main.py immediately after `app.include_router(users_router)` (main.py:978).

NEW error_catalog.py entries (sibling to AUTH_EMAIL_TAKEN / USER_NOT_FOUND):
  INVITE_EMAIL_ALREADY_MEMBER = ErrorSpec(409, "ERR_INVITE_EMAIL_ALREADY_MEMBER",
      "This email already belongs to a member of your tenant")
  INVITE_NOT_FOUND  = ErrorSpec(404, "ERR_INVITE_NOT_FOUND", "Invite not found")
  INVITE_NOT_PENDING = ErrorSpec(409, "ERR_INVITE_NOT_PENDING", "Invite is not in pending state")
  (REUSED, not new: AUTH_TOKEN_MISSING · AUTH_TOKEN_INVALID · AUTH_FORBIDDEN · PAYLOAD_INVALID —
   see §1 Framings weighed for why these are reused verbatim rather than invite-flavored duplicates.)

NEW audit actions (fire-and-forget via record_audit, mirroring user.role_assign — M12):
  "invite.create" (metadata: target email, role) · "invite.revoke" (metadata: invite_id)
```

Glossary deltas:
  - `invite (pending invite)`: a tenant-scoped, single-use, hashed-at-rest, explicitly-expiring offer
    for a NEW person to join a tenant AS a specific role — created by an owner/admin, listed/revoked by
    any CURRENT owner/admin in that tenant (not creator-scoped). States: pending -> accepted | revoked
    (both terminal; no further transition). Distinct from `Team`'s `lead`/`member` join-table tag,
    which groups EXISTING users for budget attribution and grants no tenant RBAC permission — never
    conflate the two (MILESTONE.md Shared decisions).
  - `escalation ceiling`: the per-caller-role limit on which roles they may grant to someone else, via
    role-reassignment OR invite — owner: any of the 6 self-service tiers; admin: {operator,
    billing_admin, viewer, member} only; superadmin target: always rejected, for every caller.
    Enforced by the single shared `assert_role_within_ceiling()` predicate — never re-derived.
Status: FROZEN @ v1 — approved by Tin Dang 2026-07-04 ("freeze."). Both flags below accepted
  as-drafted (7-day expiry hardcoded default stands; ceiling-predicate extraction from
  users_use_cases.py stands, pre-verified behavior-preserving) — neither changes §3's external shape.

**Least-sure flag surfaced at freeze:** (bundle lowest-confidence, ranked)
  ⚠ [spec] 7-day invite expiry is a HARDCODED, Tin-unconfirmed default (MILESTONE.md itself flags it
    as unfrozen) — see the §1 ⚠ assumption; if wrong, a one-line constant change or a small, non-
    contract-shape scope addition (a Settings knob), never a breaking change to what's frozen here.
  ⚠ [contract] Extracting `assert_role_within_ceiling` out of the previously-shipped, FROZEN@v1
    `users_use_cases.py` — verified behavior-preserving (the router never surfaces
    `EscalationForbiddenError`'s message), but it IS a touch to a file outside this task's own new
    files; if the reviewer wants zero touches there, the documented fallback (duplicate the ceiling
    logic with a "must stay byte-identical" comment) is a safe, same-shaped substitute — see the §1 ⚠
    assumption for the full trade-off.
  Both flags are scoped so that accepting either resolution leaves §3's external shape (routes, DTOs,
  schema, error codes) completely unchanged — neither is a reason to withhold freezing the contract
  itself, only to pick which of two Build-time paths to take.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: <e.g. 90%>
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_<scenario>: arrange <Given> / act <When> / assert <Then> + assert <unchanged> · covers: <M#, R:code — optional>
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
  `apps/gateway/src/gateway/tenants/domain/entities.py` (add InviteStatus, Invite)
  `apps/gateway/src/gateway/tenants/domain/errors.py` (add InviteNotFoundError, InviteNotPendingError, InviteEmailAlreadyMemberError)
  `apps/gateway/src/gateway/tenants/application/users_use_cases.py` (extract assert_role_within_ceiling)
  `apps/gateway/src/gateway/tenants/application/invite_use_cases.py` (NEW)
  `apps/gateway/src/gateway/tenants/infrastructure/invite_repository.py` (NEW)
  `apps/gateway/src/gateway/tenants/infrastructure/orm.py` (add InviteRow)
  `apps/gateway/src/gateway/tenants/api/invites_router.py` (NEW)
  `apps/gateway/src/gateway/core/error_catalog.py` (add 3 new ErrorSpec entries — SHARED file, other
    parallel builds also touch this; expect a merge reconciliation, not a conflict-free apply)
  `apps/gateway/src/gateway/main.py` (register invites_router after users_router — SHARED file, same
    merge caveat as error_catalog.py)
  `apps/gateway/migrations/versions/` (one new migration, revises current head — SHARED directory;
    the exact down_revision chain across the 3 parallel builds is reconciled by the orchestrator
    after all 3 land, not by this build)
  `apps/gateway/tests/member_invite_issuance/` (this task's own test directory)
Strategy (ordered batches): 1. domain (InviteStatus/Invite/errors) 2. assert_role_within_ceiling
  extraction (behavior-preserving, re-run existing rbac_roles/users tests to confirm byte-identical)
  3. infrastructure (InviteRow + migration + InviteRepository) 4. application (invite_use_cases.py)
  5. API (invites_router.py + main.py registration + error_catalog.py entries) 6. tests per scenario.

Persona (optional): <name the persona file under `.add/personas/` this build embodies as a domain stance atop SOUL.md — advisory, never lowers a gate; absent = generic>
Known-problem fixes: <trap → planned fix — the failure modes this build must dodge; guidance, not enforced>
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 30/30 `tests/member_invite_issuance/` + 8/8 `test_users_role.py` + 8/8 `tests/rbac_roles/` (regression), independently re-run by the verify agent, not taken on the build's claim
- [x] coverage did not decrease — all M1-M12/R1-R9 trace to a non-vacuous test (see refute-read below); repo-wide `[tool.coverage.run]` lacks `concurrency=greenlet` so raw async-file line-% is unreliable here (pre-existing repo gap, logged as an OBSERVE delta, not this task's defect)
- [x] no test or contract was altered during build — `git diff` shows only 2 pre-existing SHARED manifest files touched (`tests/migrations/test_migrations.py`, `tests/guardrails/test_guardrails_core.py`), both additive one-line "SANCTIONED EDIT" entries following 8+ prior tasks' identical precedent, not a weakening of any assertion
- [x] the green was EARNED, not gamed — adversarial refute-read by an independent add-verify subagent: EARNED (see verdict below)
- [x] concurrency / timing of the risky operation is safe — independently proven via a forced-race probe (see verdict below), not just reading the shipped test
- [x] no exposed secrets, injection openings, or unexpected dependencies — token/token_hash confirmed absent from every log line, exception, and audit metadata; all queries parameterized
- [x] layering & dependencies follow CONVENTIONS.md — router -> use-case -> repository, no violation, confirmed by direct read
- [x] a person reviewed and approved the change — Tin Dang, via the orchestrator's report following this gate record

### Build expectations — what "correct" looks like
- [x] `role: "superadmin"` is rejected 422 BEFORE any use case runs, for every caller (M2/R4) — confirmed at `invites_router.py:141-151` (explicit two-step pre-check, not reliant on enum-parse failure)
- [x] the SAME escalation ceiling used by role-reassignment is reused, never re-derived (M1/R5) — confirmed via object-identity test (`invite_use_cases.assert_role_within_ceiling is users_use_cases.assert_role_within_ceiling`) + repo-wide grep found exactly one `_ADMIN_ASSIGNABLE` definition
- [x] superadmin is excluded at the DB layer independent of the app (defense-in-depth) — confirmed via a raw bypass-INSERT test proving `invites_role_check` CHECK constraint fires even without going through the application
- [x] tenant isolation on REVOKE: unknown-id and cross-tenant-id return byte-identical responses (no oracle) — confirmed at `invite_repository.py:159-190`, single tenant-scoped query, same error path
- [x] re-invite atomically supersedes any existing pending row, no window with two live tokens (M5) — confirmed via an independent forced-race probe (explicit `asyncio.Event` barriers forcing genuinely overlapping transactions through the real, unmodified repository code)

### Deep checks
- [x] WIRING (code) — every new symbol referenced; `main.py:980` mounts `invites_router` immediately after `users_router:979`, matching the contract's anchor
- [x] DEAD-CODE (code) — one contract-mandated symbol (`InviteRepository.get_by_id_and_tenant`) has no production caller yet in this build; judged intentional forward-provisioning for the sibling `member-invite-acceptance` task, not orphaned — flagged as an OBSERVE delta to confirm with that task's design, not silently assumed
- [ ] SEMANTIC (prose / non-code) — n/a, no prose-only deliverable in this task

### Live-verify evidence
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by the independent verify agent reading each directly (not grep-only)
- [x] the one symbol that moved since Ground SHA `9740f21` is `assert_role_within_ceiling` itself — the deliberate, contract-mandated extraction, not silent drift; no other anchor moved

### Refute-read verdict — the earned-green check
Verdict: EARNED
By: independent add-verify subagent (appsec-engineer persona) · adversarially checked: re-ran all 30 tests firsthand; read all 1231 lines of the test file line-by-line; confirmed each M/R item traces to a test asserting real HTTP status/body AND real DB state (not mocks); specifically verified Test 9 recomputes the token hash independently (not a vacuous field-exists check), Test 10 proves the DB CHECK constraint via a raw bypass-insert, and Tests 25-27 prove the shared-predicate extraction by object identity — closing exactly the gap that sank the discarded first build attempt.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
Advisor: independent add-verify subagent (agent a7cf907c9e9eeb824, appsec-engineer persona)
1. Security: CLEAR — all 3 defense-in-depth layers (router 422 pre-check, unconditional-first shared-predicate guard, DB CHECK constraint) independently confirmed from code; tenant isolation and anti-enumeration confirmed structurally identical code paths, not just similar-looking responses
2. Concurrency: CLEAR — proven via an independent forced-race probe (not just reading the shipped test); 🟡 non-blocking note: the shipped `test_concurrent_double_reinvite_never_two_live_tokens` lacks an explicit sync barrier so it doesn't itself guarantee the collision path runs every execution — logged as an OBSERVE delta, does not block since the underlying mechanism was independently proven correct
3. Architecture: CLEAR — clean 3-layer separation, zero duplicate ceiling-table risk (repo-wide grep + object-identity test)
Verdict: PASS
Residue: none blocking — 2 non-blocking deltas recorded in §7 OBSERVE (test-hardening fast-follow; repo-wide coverage.py greenlet-tracking gap)
Binding: advisory — sensitivity not declared `security` on this task's header, though treated with security-HARD-STOP rigor throughout given the actual privilege-escalation-shaped risk

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (via orchestrator report) · date: 2026-07-04

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang 2026-07-04 ("freeze."). Both flags below accepted)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by Tin Dang (via orchestrator report))

### Spec delta
- [SPEC · open] Harden `test_concurrent_double_reinvite_never_two_live_tokens` with an explicit
  `asyncio.Event` synchronization barrier (pattern proven in the verify agent's `probe_race.py`
  diagnostic) so the IntegrityError-retry path is deterministically exercised every run rather than
  scheduler-timing-dependent (evidence: 0% line coverage on `invite_repository.py:78-84` across an
  otherwise-passing run; the underlying mechanism was independently proven atomic via a forced-race
  probe, so this is test-hardening, not a code defect).
- [SPEC · seeded] Confirm with the `member-invite-acceptance` task's own design whether
  `InviteRepository.get_by_id_and_tenant` (contract-mandated, currently unwired into any production
  path in this build) is the primitive it intends to reuse for token-holder lookups.

### Competency deltas
- [TDD · folded] This repo's `[tool.coverage.run]` config lacks `concurrency = greenlet`, making [folded foundation-version 48]
  per-line coverage on SQLAlchemy-async modules structurally unreliable for judging which branches
  actually executed (evidence: `invite_repository.py`'s core INSERT logic showed "uncovered" despite
  being required for 30 passing tests; corroborated against a known-solid pre-existing file showing
  the same "impossible" under-report). Worth fixing repo-wide so future verifies of async code can
  trust coverage numbers directly instead of hand-building a probe.
- [ADD · folded] Worktree isolation (`isolation: "worktree"`) branches from the last git COMMIT, not [folded foundation-version 48]
  the current working tree — incompatible with a task whose §0 GROUND anchors (or whose milestone's
  prerequisite work) exist only uncommitted. This task's first build attempt silently ran against a
  stale base missing `Role.SUPERADMIN` entirely and shipped an incomplete security guard before being
  caught and discarded (evidence: this task's own Verify history). Future dispatches onto a
  substantially-uncommitted tree should default to no isolation + strict sequential ordering when
  shared files are at stake, not isolation-for-safety by default.
