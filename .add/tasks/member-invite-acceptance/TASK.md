# TASK: Member invite acceptance

slug: member-invite-acceptance · created: 2026-07-05 · stage: production
milestone: team-member-invite
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
phase: ground   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - EXTEND `InviteRepository` (`apps/gateway/src/gateway/tenants/infrastructure/invite_repository.py`,
    SHIPPED/frozen class from member-invite-issuance — additive methods only, no existing method
    touched): NEW `get_preview_by_token_hash(*, token_hash: str, now: datetime) -> InvitePreview`
    (plain `SELECT` + `JOIN tenants` for `name`, no lock — read-only) and NEW `accept(*, token_hash:
    str, password_hash: str, now: datetime) -> tuple[uuid.UUID, uuid.UUID]` (the one atomic accept
    transaction: `SELECT ... FOR UPDATE` by `token_hash`, validate pending+not-expired, `INSERT
    users`, flip `invites.status`, commit). Both keyed PURELY by `token_hash` — no `tenant_id`/
    `invite_id` input, unlike the existing `get_by_id_and_tenant(*, invite_id, tenant_id)` (resolved
    open question, see §3).
  - NEW `apps/gateway/src/gateway/tenants/api/invite_accept_router.py` — a SEPARATE router instance
    (`invite_accept_router`, prefix `/invites`), NOT added to the existing `invites_router.py`
    (`/admin/invites`, MEMBERS_MANAGE-gated) — mirrors how `agent_oauth` already splits its PUBLIC
    `device_authorize_router.py` (`_resolve_client_ip`, `:62-73`) from its AUTHENTICATED
    `device_approval_router.py` despite both operating on the one `device_authorizations` table.
    `GET /invites/{token}` (preview) + `POST /invites/{token}/accept` — NEITHER endpoint carries any
    auth dependency; both take a per-client-IP rate-limit check first.
  - NEW `apps/gateway/src/gateway/tenants/application/invite_accept_use_cases.py` (sibling to
    `invite_use_cases.py`) — `PreviewInviteUseCase.execute(*, token, now)` and
    `AcceptInviteUseCase.execute(*, token, password, now)`; the latter is MILESTONE.md's own named
    "one clean provisioning choke point" call site (Shared decisions — the SECOND, after
    `get_or_provision_oidc_user`, `tenants/infrastructure/repository.py:101-141`, for a future
    seat-cap hook).
  - NEW `apps/gateway/src/gateway/tenants/infrastructure/invite_public_rate_limiter.py` — mirrors
    `keys/infrastructure/mint_rate_limiter.py`'s `PlaygroundMintRateLimiter`/`MintRateLimitedError`
    shape EXACTLY (fixed 60s window, Redis `INCR`+`EXPIRE`, fail-open on `RedisError`/`OSError`)
    rather than reusing `agent_oauth/infrastructure/ip_rate_limiter.py:AgentOAuthIpRateLimiter`
    cross-module — every existing bounded context that needed this shape (agent_oauth's own
    `RateLimitedError`, keys/playground's `MintRateLimitedError`) wrote its OWN small class; no
    shared generic version exists to reuse today (see §1 Framings weighed).
  - EXTEND `apps/gateway/src/gateway/tenants/domain/entities.py` — NEW frozen dataclass
    `InvitePreview` (`tenant_name: str, email: str, role: Role, expires_at: datetime`; sibling to
    `Invite`, entities.py:93-109). `Invite`/`InviteStatus` themselves are UNCHANGED — "expired" is
    always a COMPUTED check (`status == 'pending' AND expires_at < now`), never a 4th persisted
    status value (`invites_status_check` stays `IN ('pending','accepted','revoked')` — confirmed by
    reading the shipped migration directly, see Schema in §3).
  - EXTEND `apps/gateway/src/gateway/tenants/domain/errors.py` — NEW `InviteExpiredError` (sibling to
    `InviteNotFoundError`/`InviteNotPendingError`, errors.py:33-43). REUSES (does not touch)
    `EmailAlreadyRegisteredError` (errors.py:5-6, already raised on the signup path) and
    `WeakPasswordError` (errors.py:9-10).
  - EXTEND `apps/gateway/src/gateway/core/error_catalog.py` — ONE new entry,
    `INVITE_EXPIRED = ErrorSpec(410, "ERR_INVITE_EXPIRED", "Invite has expired")` (sibling to
    `INVITE_NOT_FOUND`/`INVITE_NOT_PENDING`, error_catalog.py:321,345). Every OTHER rejection reuses
    an EXISTING `ErrorSpec` verbatim: `INVITE_NOT_FOUND` (:321) · `INVITE_NOT_PENDING` (:345) ·
    `AUTH_PASSWORD_WEAK` (:134-136) · `AUTH_EMAIL_TAKEN` (:129-131) · `RATE_LIMITED` (:364).
  - EXTEND `apps/gateway/src/gateway/core/config.py:Settings` — NEW `invite_preview_rpm: int = 30`
    (`GATEWAY_INVITE_PREVIEW_RPM`) and `invite_accept_rpm: int = 10` (`GATEWAY_INVITE_ACCEPT_RPM`),
    joining the existing positive-knob `@field_validator` pattern (config.py:849-873's
    `_validate_agent_oauth_positive_knobs` is the style to mirror — new validator or extend that
    one's field list is Build's call).
  - EDIT `apps/gateway/src/gateway/main.py` — `app.include_router(invite_accept_router)` immediately
    after `app.include_router(invites_router)` (main.py:1101); `app.state.invite_public_limiter =
    InvitePublicRateLimiter(redis=redis_client)` beside `agent_oauth_ip_limiter`/
    `playground_mint_limiter` (main.py:919-924, same `redis_client`, no new connection).
  - NO migration — the shipped `invites` table (migration `1193bc6178f3_member_invite_issuance.py`)
    already has everything this task needs: `token_hash` carries `sa.UniqueConstraint(...,
    name="invites_token_hash_key")` (migration :67), which Postgres auto-indexes — already exactly
    what a by-token-hash lookup needs. GROUND CORRECTION: member-invite-issuance's OWN §3 CONTRACT
    prose additionally describes "INDEX (token_hash) ... keeps that lookup planner-cheap" as if a
    SEPARATE plain index exists — read directly, the shipped migration contains ONLY the
    `UniqueConstraint`, no second index (a UNIQUE constraint IS already a full B-tree index, so the
    described second index would have been redundant; not a gap, a stale/aspirational line in that
    task's own contract prose — verified against the migration file itself, not that doc's summary).

Context (working folder): `.add/milestones/team-member-invite/MILESTONE.md` (Shared decisions +
  Shared/risky contracts name this task as owner of "accept-time identity resolution + the
  cross-tenant/global-email-collision handling"); `.add/tasks/member-invite-issuance/TASK.md` (the
  shipped sibling — §0/§1/§3 establish the schema, token/hash scheme, and the escalation-ceiling
  precedent this task must NOT re-litigate); `.add/tasks/device-approval-flow/TASK.md` (the closest
  structural precedent: a secret-keyed resource with an analogous 404/409/410 triad, and the
  server-side-only-identity-resolution AUTHZ RULE this task's own Must list quotes directly).

Honors (patterns / conventions):
  - Token-as-opaque-hashed-secret + SHA-256-at-rest (NOT a JWT) — GLOSSARY "API key" precedent,
    already used verbatim by `invites.token_hash` (`keys/infrastructure/sha256_hasher.py:
    Sha256SecretHasher`, whose own `.verify()` uses `hmac.compare_digest` — this task's lookup goes
    through a DB equality index instead, see §3 for why that is the right substitute here).
  - Server-side-only identity resolution — MILESTONE.md quotes BOTH `get_or_provision_oidc_user`'s
    "role is ALWAYS member, never from claims" (`repository.py:132`) and device-approval-flow's
    "binding always from the verified source, never client-supplied body fields"
    (`.add/tasks/device-approval-flow/TASK.md` §3 AUTHZ RULE) as the two precedents THIS task must
    follow: tenant_id/email/role come EXCLUSIVELY from the invite row the token resolves to, never
    from any accept-request body field.
  - Distinguishable 404/409/410 for a secret-keyed resource — `device-approval-flow`'s
    `AGENT_OAUTH_AUTHORIZATION_NOT_FOUND`/`_NOT_PENDING`/`_EXPIRED` triad (error_catalog.py:598-608)
    is the DIRECT precedent this task's own three rejections mirror.
  - Design-for-failure (CLAUDE.md) — fixed-window Redis rate limiter, fail-open on Redis outage
    (mirrors `PlaygroundMintRateLimiter`/`AgentOAuthIpRateLimiter` exactly); one atomic, row-locked
    accept transaction that fails closed (no partial row) on every reject.
  - Auto-login BFF chain to mirror (NOT this task's own code — informs the accept RESPONSE shape
    only) — `apps/dashboard/app/api/auth/signup/route.ts`'s existing 2-step `signup -> POST
    /admin/auth/login -> httpOnly cookie` chain; the sibling `member-invite-ui` task will build the
    SAME chain for accept, so this task's `InviteAcceptResponse` must carry what that chain needs.

Anchors the contract cites: `InviteRepository.get_preview_by_token_hash`/`.accept` (NEW) ·
  `InviteRepository.get_by_id_and_tenant` (EXISTING, resolved-NOT-reused, see §3) · `InvitePreview`
  (NEW entity) · `InviteExpiredError` (NEW) · `EmailAlreadyRegisteredError`/`WeakPasswordError`
  (EXISTING, reused) · `INVITE_EXPIRED`/`INVITE_NOT_FOUND`/`INVITE_NOT_PENDING`/`AUTH_PASSWORD_WEAK`/
  `AUTH_EMAIL_TAKEN`/`RATE_LIMITED` (error_catalog.py) · `Sha256SecretHasher` · `MIN_PASSWORD_LENGTH`
  (entities.py:7) · `UserRow` / global email uniqueness (orm.py:145-170) ·
  `PlaygroundMintRateLimiter` (the infra shape to mirror, NOT to import).

Issues/Risks (→ feed §1):
  - Route-vs-logic 404 ambiguity: pre-Build, EVERY new path 404s because the route itself doesn't
    exist yet — a naive test asserting only `status_code == 404` would pass RED for the WRONG reason
    (route-not-mounted, not "token not found"). Every test in §4 MUST additionally assert the
    specific `code` field (FastAPI's own default not-found body has no `code` key at all), so a
    route-missing 404 is never conflated with a logic-driven 404.
  - Global email-uniqueness collision (MILESTONE.md's own named residual risk): the invited email can
    become registered in ANOTHER tenant between invite-CREATE and accept time (issuance's own M6
    deliberately allows creating an invite for an email that already exists in a different tenant).
    Accept's provisioning INSERT then hits `users`' global unique constraint — must roll back
    EVERYTHING (invite stays unflipped) and reuse `AUTH_EMAIL_TAKEN` verbatim (MILESTONE.md's own
    explicit resolution), never invent a new code for this.
  - Concurrent double-accept of the SAME token: two accept requests racing the SAME row must never
    both succeed (never two `users` rows for one invite) — the existing `SELECT ... FOR UPDATE` +
    one-transaction pattern (`InviteRepository.revoke`'s own shape) is the proven mechanism to reuse;
    needs its own forced-concurrency test, mirroring issuance's own M5 race test.
  - Preview/accept validity-gating symmetry is a genuine, UNRESOLVED product question (not silently
    assumed) — MILESTONE.md only explicitly describes preview's HAPPY path; whether an expired/
    revoked/accepted invite's preview should 404/409/410 (this task's choice) or instead 200 with a
    status field for the frontend to render a friendly message (a legitimate alternative the
    NOT-YET-drafted `member-invite-ui` sibling might prefer) is this task's own top ⚠ assumption —
    see §1.

Related intent: `.add/milestones/team-member-invite/MILESTONE.md` goal (a colleague can accept an
  invite and land in the dashboard signed in, with zero OIDC/domain-mapping) + "Shared / risky
  contracts" (explicitly names THIS task as owner of "accept-time identity resolution + the
  cross-tenant/global-email-collision handling") + Exit criteria bullet 4 ("the invited person can
  open the link... set a password, and land in the dashboard already signed in") and bullet 5 ("an
  expired, revoked, already-accepted, or unknown token is rejected with a specific, non-500 error").
  Tin named this task "currently the highest-priority" gap: an issued invite today has no way to be
  accepted at all.

Ground SHA: cfbb464

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Member invite acceptance — the public, unauthenticated other half of team-member-invite.
  Anyone holding a still-valid invite link can preview which tenant and role they are joining (GET,
  no side effect), then set a password to atomically provision a real `UserRow` bound to that
  tenant+role and mark the invite `accepted` (POST .../accept) — zero OIDC/domain-mapping
  configuration, zero prior account. This is the ONLY way a password-auth tenant gains a colleague
  today: the shipped sibling `member-invite-issuance` can only CREATE the offer; without this task
  every issued invite is a dead end. Non-goals: the dashboard accept-invite PAGE and its BFF
  auto-login route are the NOT-YET-drafted sibling `member-invite-ui` task — this task's surface
  ends at the gateway JSON API; no frontend/React/Next.js code, no session cookie, no JWT minted
  here (the response carries what the BFF's own login call will need, nothing more).
Framings weighed: token as a PATH parameter on BOTH endpoints, preview and accept sharing IDENTICAL
  validity gating — unknown/not-pending/expired all 404/409/410 on both (chosen — least-disclosure
  default, no wasted round-trip, one shared check; MILESTONE.md explicitly allows "path/body", never
  a query string, on the gateway's own API) · preview always 200s with a `status` field so the
  frontend renders its own "this invite expired" copy (rejected as the primary design but NOT
  dismissed — a legitimate alternative the UI task might still prefer; this task's top ⚠ assumption,
  reversible without touching accept) · token in the request body for both, mirroring a POST-only
  "lookup" action (rejected — GET is the more RESTful shape for a side-effect-free preview and the
  milestone's own phrasing already anticipates a "lookup" GET) · a single collapsed error code for
  every invalid-token reason (rejected — MILESTONE.md's exit criteria explicitly wants "a SPECIFIC,
  non-500 error", and unlike issuance's revoke-by-id anti-enumeration concern, the identifying key
  HERE is the unguessable secret itself, so distinguishing WHY an already-known token fails creates
  no new oracle — device-approval-flow's own frozen 404/409/410 triad is the direct, already-accepted
  precedent for this exact reasoning) · extending the EXISTING `InviteRepository` with 2 new methods
  (chosen — one repository authority per table; `user_exists_in_tenant` already established that
  this repository may read `UserRow` when directly relevant) over a second, parallel repository class
  for the same `invites` table (rejected — two authorities over one table is a layering smell) ·
  rate-limiting BOTH endpoints, not just accept (chosen, MY OWN reasoned extension beyond
  MILESTONE.md's literal text, which names only accept — flagged as this task's #2 ⚠ assumption,
  not silently assumed) over accept-only (rejected as an inconsistent half-measure: preview is an
  equally public, equally attacker-reachable surface that hashes and looks up a caller-supplied
  token) · a NEW small `InvitePublicRateLimiter` class mirroring `PlaygroundMintRateLimiter`'s exact
  shape (chosen — matches the established one-class-per-bounded-context convention; agent_oauth,
  keys/playground each wrote their own rather than sharing) over reusing `AgentOAuthIpRateLimiter`
  cross-module (rejected — agent_oauth is a distinct bounded context) or extracting a shared generic
  limiter into `core/` (rejected as OUT OF SCOPE — a valuable but separate refactor of 3 existing
  call sites, not a silent side effect of this task; noted as a spec-delta candidate, not acted on).
Must:
<must>
  - **[M1]** `GET /invites/{token}` (PUBLIC, no auth) resolves the plaintext `token` (SHA-256 hashed,
    matched via the EXISTING unique `token_hash` index — no migration) to
    `{tenant_name, email, role, expires_at}` — ONLY when the invite is currently `pending` AND not
    time-expired; no other invite field (id, tenant_id, invited_by_user_id, token/token_hash) is ever
    exposed. Read-only: never takes a row lock, never mutates anything.
  - **[M2]** `POST /invites/{token}/accept` (PUBLIC, no auth) accepts body `{password}`; on success,
    provisions exactly ONE new `UserRow` bound to the invite's `tenant_id` + `role`
    (`auth_method='password'`, password validated by the SAME `MIN_PASSWORD_LENGTH`/
    `WeakPasswordError` signup already uses), flips that SAME invite's status to `'accepted'`, and
    returns `{tenant_id, user_id, email}` — `SignupResponse`'s own shape plus `email` (accept's
    caller, unlike signup's, never typed the email in, so the sibling BFF's auto-login chain needs
    it echoed back).
  - **[M3]** Both endpoints resolve email/tenant_id/role EXCLUSIVELY from the invite row the token
    hash resolves to — NEVER from any client-supplied body field (server-side-only identity
    resolution, MILESTONE.md's named shared decision, mirrors `get_or_provision_oidc_user` +
    device-approval-flow's own AUTHZ RULE). An accept body carrying extra fields (e.g. an injected
    `tenant_id`/`role`/`email`) has zero effect on the provisioned user.
  - **[M4]** Both endpoints reject, with a SPECIFIC non-500 code, an unknown token (404), a token
    whose invite is not pending — already accepted OR already revoked — (409), or a pending-but-
    time-expired token (410) — mirroring device-approval-flow's exact 404/409/410 triad for an
    analogous secret-keyed resource. Preview and accept apply the IDENTICAL gate (§1 Framings
    weighed's top ⚠ assumption).
  - **[M5]** Accept is atomic and fails closed: ANY rejection (404/409/410/400-weak-password/
    409-email-taken/429-rate-limited) leaves the invites row's status COMPLETELY unchanged from
    immediately before the call and creates NO users row, partial or otherwise. The
    `SELECT ... FOR UPDATE` row lock + one transaction (mirrors `InviteRepository.revoke`'s own
    shape) guarantees no window where a concurrent accept and a concurrent revoke (from the shipped
    issuance surface) race to two different, both-"successful" outcomes.
  - **[M6]** If the invited email now belongs to an existing user ANYWHERE — the GLOBAL `users.email`
    uniqueness constraint, discovered via the DB catching an `IntegrityError` at INSERT time, never a
    proactive cross-tenant lookup — accept rejects 409 reusing `AUTH_EMAIL_TAKEN`'s exact shape
    byte-for-byte (MILESTONE.md's own already-decided resolution to the named cross-tenant-collision
    residual risk), rolling back the WHOLE transaction (the invite stays `pending`, per M5).
  - **[M7]** Both endpoints are rate-limited per-client-IP (fail-open on Redis outage, mirrors every
    other public gateway endpoint's design-for-failure posture): accept per
    `GATEWAY_INVITE_ACCEPT_RPM` (default 10/60s), preview per `GATEWAY_INVITE_PREVIEW_RPM` (default
    30/60s). Exceeding either returns 429 + `Retry-After`, and a rate-limited call never touches the
    invites/users tables.
  - **[M8]** A successful accept fires an `invite.accept` audit event (fire-and-forget, fail-open),
    metadata `{tenant_id, user_id, email}` — mirrors `invite.create`/`invite.revoke`'s M12 precedent
    from the shipped sibling. Preview is UNAUDITED (mirrors LIST being unaudited too — a read with no
    state change to record).
  - **[M9]** No migration: the shipped `invites` table's existing `token_hash` UNIQUE constraint
    (already a de-facto lookup index) and `status`/`expires_at` columns are exactly what this task
    needs — zero schema change.
  - **[M10]** The existing `/admin/invites` CRUD surface (member-invite-issuance, shipped/frozen) is
    completely untouched: no existing router, use case, or router behavior changes; `InviteRepository`
    gains ONLY new, additive methods.
</must>
Reject:
<reject>
  - **[R1]** Unknown token (hash matches no row) -> "ERR_INVITE_NOT_FOUND" (404, reused verbatim)
  - **[R2]** Token's invite status is not pending — already accepted OR already revoked ->
    "ERR_INVITE_NOT_PENDING" (409, reused verbatim)
  - **[R3]** Token's invite is pending but `expires_at` has passed -> "ERR_INVITE_EXPIRED" (410, NEW)
  - **[R4]** Accept: `password` shorter than `MIN_PASSWORD_LENGTH` (10) -> "ERR_AUTH_PASSWORD_WEAK"
    (400, reused verbatim) — checked only AFTER R1-R3 pass, before any DB write
  - **[R5]** Accept: provisioning collides with the GLOBAL `users.email` uniqueness constraint ->
    "ERR_TENANT_EMAIL_TAKEN" (409, reused verbatim via `AUTH_EMAIL_TAKEN`) — discovered last, at
    INSERT time
  - **[R6]** Either endpoint exceeds its per-IP rate limit -> "ERR_RATE_LIMITED" (429, reused
    verbatim) + `Retry-After` header
</reject>
After:
<after>
  - Preview success: zero side effect — the invites/users tables are byte-for-byte unchanged; the
    response reveals only `{tenant_name, email, role, expires_at}`.
  - Accept success: exactly one NEW users row exists (`tenant_id=invite.tenant_id`,
    `email=invite.email`, `role=invite.role`, `auth_method='password'`); the SAME invites row is now
    `status='accepted'`; the response is `{tenant_id, user_id, email}`; an `invite.accept` audit
    event was fired; the token can never be accepted again (a repeat POST now 409s via R2).
  - Any reject (accept): the invites row's status is UNCHANGED from immediately before the call; no
    users row was created, partial or otherwise; no audit event fired.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Preview should enforce the SAME 404/409/410 validity gate as accept — MY OWN reasoned extension,
    not stated verbatim anywhere in MILESTONE.md (which only explicitly describes preview's HAPPY
    path: "so the dashboard can preview {tenant name, invited email, role}"). Lowest confidence
    because a reasonable alternative reading is "preview should always succeed and show a status so
    the dashboard can render a friendly 'this invite expired' message inline" — a UX call that
    belongs to the NOT-YET-drafted `member-invite-ui` sibling, which might actually want to
    distinguish these states for better copy. If wrong: `member-invite-ui` needs preview to return
    200 with a `status` field instead of 404/409/410 — a contract-shape change to THIS task's frozen
    GET response only, a change request back to Specify; accept's own shape is unaffected either way.
  ⚠ Rate-limiting PREVIEW (not just accept) is MY OWN reasoned addition beyond MILESTONE.md's literal
    text. Lowest confidence because it is doing more than was explicitly asked; if wrong (Tin
    considers this unnecessary), it is a one-line deletion (drop the preview-side check call) with
    zero contract-shape impact — both response shapes are unaffected either way.
  - [ ] Accept response `{tenant_id, user_id, email}` (SignupResponse's shape + email) is the right
    shape for the sibling `member-invite-ui`/BFF task's auto-login chain — confirmed by direct trace
    of `app/api/auth/signup/route.ts`'s existing 2-step chain (needs email+password for
    `POST /admin/auth/login`); NOT yet confirmed by that sibling task itself, since it hasn't been
    drafted (depends-on this task per MILESTONE.md). If wrong: an additive field, non-breaking.
  - [ ] `GATEWAY_INVITE_ACCEPT_RPM=10` / `GATEWAY_INVITE_PREVIEW_RPM=30` (defaults) — reasonable
    starting numbers by analogy to `agent_oauth_approve_rpm=30`/`agent_oauth_authorize_rpm`, not
    independently derived from any traffic model; low-stakes either way since both are named,
    positive-knob Settings fields any operator can override post-deploy without a code change.
  - [ ] `INVITE_EXPIRED` (410) is confirmed a genuinely NEW code, not a reuse — no existing `(410, *)`
    ErrorSpec exists for any 'expired' concept other than agent_oauth's OWN, differently-scoped
    `AGENT_OAUTH_EXPIRED` — a fresh, invite-scoped code mirrors the codebase's per-feature-scoped
    error-naming convention (e.g. `INVITE_NOT_FOUND` vs `AGENT_OAUTH_AUTHORIZATION_NOT_FOUND` are
    already two separate codes for analogous-but-distinct resources).
  - [ ] No migration needed — confirmed by reading the actual shipped migration
    (`1193bc6178f3_member_invite_issuance.py`) directly: `token_hash` already carries a
    `UniqueConstraint` (auto-indexed), `status`/`expires_at` already exist; nothing this task needs
    is missing from the already-shipped schema.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Preview a valid pending invite   # M1
  Given a pending invite in tenant "InviteCo" for "new@co.io" as role "member", token X
  When a client GETs /invites/X (no Authorization header)
  Then the response is 200 with EXACTLY {tenant_name: "InviteCo", email: "new@co.io", role: "member",
    expires_at: <iso datetime>} — no id, tenant_id, invited_by_user_id, token, or token_hash field
  And the invites row is completely unchanged (still status='pending')

Scenario: Accept a valid pending invite provisions a user and marks it accepted   # M2, M8
  Given a pending invite in tenant T for "new@co.io" as role "member", token X
  When a client POSTs /invites/X/accept {password: "correct horse battery staple"}
  Then the response is 200 with EXACTLY {tenant_id: T, user_id: <new uuid>, email: "new@co.io"}
  And exactly one NEW users row exists with tenant_id=T, email="new@co.io", role="member",
    auth_method="password", and a real argon2 password_hash (never the plaintext)
  And the SAME invites row is now status="accepted"
  And an "invite.accept" audit event was recorded (metadata: tenant_id, user_id, email)

Scenario: Accept ignores client-supplied identity fields in the body   # M3
  Given a pending invite in tenant T for "new@co.io" as role "member", token X
  When a client POSTs /invites/X/accept {password: "correct horse battery staple",
    tenant_id: "<other-uuid>", role: "owner", email: "attacker@evil.io"}
  Then the response is 200 and the provisioned user's tenant_id/role/email are EXACTLY T/"member"/
    "new@co.io" — the injected body values have zero effect

Scenario: Preview an unknown token   # M4, R1
  Given no invite anywhere has a token hashing to X
  When a client GETs /invites/X
  Then the response is 404 "ERR_INVITE_NOT_FOUND"
  And no invites/users row is read or modified

Scenario: Preview a revoked invite   # M4, R2
  Given an invite with token X was revoked (via the shipped DELETE /admin/invites/{id})
  When a client GETs /invites/X
  Then the response is 409 "ERR_INVITE_NOT_PENDING"
  And the invite's status remains "revoked" (unchanged)

Scenario: Preview an already-accepted invite   # M4, R2
  Given an invite with token X was already accepted (via this task's own POST .../accept)
  When a client GETs /invites/X again
  Then the response is 409 "ERR_INVITE_NOT_PENDING"
  And the invite's status remains "accepted" (unchanged)

Scenario: Preview an expired invite   # M4, R3
  Given a pending invite with token X whose expires_at is in the past
  When a client GETs /invites/X
  Then the response is 410 "ERR_INVITE_EXPIRED"
  And the invite's status remains "pending" (unchanged — expiry is a computed check, never a write)

Scenario: Accept an unknown token   # R1
  Given no invite anywhere has a token hashing to X
  When a client POSTs /invites/X/accept {password: "correct horse battery staple"}
  Then the response is 404 "ERR_INVITE_NOT_FOUND"
  And no users row is created

Scenario: Accept a revoked invite   # R2
  Given an invite with token X was revoked (via the shipped DELETE /admin/invites/{id})
  When a client POSTs /invites/X/accept {password: "correct horse battery staple"}
  Then the response is 409 "ERR_INVITE_NOT_PENDING"
  And the invite's status remains "revoked" and no users row is created

Scenario: Accept an already-accepted invite (repeat accept)   # R2
  Given a pending invite with token X
  When a client POSTs /invites/X/accept {password: "correct horse battery staple"} (succeeds, 200)
  And the SAME client POSTs /invites/X/accept {password: "another-valid-password"} again
  Then the second response is 409 "ERR_INVITE_NOT_PENDING"
  And still exactly ONE users row exists for that email — the second attempt created none

Scenario: Accept an expired invite   # R3
  Given a pending invite with token X whose expires_at is in the past
  When a client POSTs /invites/X/accept {password: "correct horse battery staple"}
  Then the response is 410 "ERR_INVITE_EXPIRED"
  And the invite's status remains "pending" and no users row is created

Scenario: Accept with a weak password   # R4
  Given a pending invite with token X
  When a client POSTs /invites/X/accept {password: "short"}
  Then the response is 400 "ERR_AUTH_PASSWORD_WEAK"
  And the invite's status remains "pending" and no users row is created

Scenario: Accept when the email now belongs to a user in a different tenant   # M6, R5
  Given a pending invite in tenant A for "collide@co.io", token X
  And a REAL user with email "collide@co.io" already exists in a DIFFERENT tenant B (e.g. via signup,
    AFTER the invite was created — issuance's own M6 allows creating that invite in the first place)
  When a client POSTs /invites/X/accept {password: "correct horse battery staple"}
  Then the response is 409 "ERR_TENANT_EMAIL_TAKEN" (byte-identical shape to signup's own collision)
  And tenant A's invite remains status="pending" (unchanged) and NO new users row was created
  And tenant B's existing user row is completely untouched

Scenario: Preview rate limit exceeded   # M7, R6
  Given GATEWAY_INVITE_PREVIEW_RPM=2 for one client IP
  When that IP GETs /invites/{any-token} a 3rd time within the same 60s window
  Then the 3rd response is 429 "ERR_RATE_LIMITED" with a Retry-After header
  And the rejected call touched no invites row

Scenario: Accept rate limit exceeded   # M7, R6
  Given GATEWAY_INVITE_ACCEPT_RPM=2 for one client IP, and 2 distinct pending invites already used
  When that IP POSTs /invites/{a-3rd-distinct-token}/accept a 3rd time within the same 60s window
  Then the 3rd response is 429 "ERR_RATE_LIMITED" with a Retry-After header
  And the 3rd invite's status remains "pending" (unchanged) and no users row is created for it

Scenario: Concurrent double-accept of the same token never both succeed   # M5 (concurrency edge)
  Given a pending invite with token X
  When two accept requests for the SAME token X fire concurrently (forced overlap)
  Then EXACTLY ONE response is 200 and the OTHER is 409 "ERR_INVITE_NOT_PENDING" — never two 200s
  And exactly ONE users row exists for that invite's email — never zero, never two
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /invites/{token}                                (PUBLIC — no auth; rate-limited per-IP, M7)
  200 -> InvitePreviewResponse { tenant_name: string, email: string, role: string,
                                 expires_at: datetime (ISO-8601 UTC) }
  404 -> { code: "ERR_INVITE_NOT_FOUND" }            # R1 — unknown token
  409 -> { code: "ERR_INVITE_NOT_PENDING" }          # R2 — already accepted or already revoked
  410 -> { code: "ERR_INVITE_EXPIRED" }              # R3 — pending but expires_at has passed
  429 -> { code: "ERR_RATE_LIMITED" } + Retry-After   # R6 — GATEWAY_INVITE_PREVIEW_RPM exceeded

POST /invites/{token}/accept                        (PUBLIC — no auth; rate-limited per-IP, M7)
  body: InviteAcceptRequest { password: string }
  200 -> InviteAcceptResponse { tenant_id: uuid, user_id: uuid, email: string }
  400 -> { code: "ERR_AUTH_PASSWORD_WEAK" }          # R4 — password < MIN_PASSWORD_LENGTH
  404 -> { code: "ERR_INVITE_NOT_FOUND" }            # R1
  409 -> { code: "ERR_INVITE_NOT_PENDING" }          # R2
  409 -> { code: "ERR_TENANT_EMAIL_TAKEN" }          # R5 — global users.email collision (M6)
  410 -> { code: "ERR_INVITE_EXPIRED" }              # R3
  429 -> { code: "ERR_RATE_LIMITED" } + Retry-After   # R6 — GATEWAY_INVITE_ACCEPT_RPM exceeded

Schema: NO migration (M9) — reuses the EXISTING `invites` table byte-for-byte (shipped migration
  `1193bc6178f3_member_invite_issuance.py`): `token_hash` already carries
  `sa.UniqueConstraint(..., name="invites_token_hash_key")` (migration :67), Postgres-auto-indexed —
  already exactly what a by-token-hash lookup needs; `status`/`expires_at` already exist. GROUND
  CORRECTION: issuance's own §3 CONTRACT prose additionally describes "INDEX (token_hash) ... keeps
  that lookup planner-cheap" as if a separate plain index exists — the actual shipped migration
  contains ONLY the UniqueConstraint; not a gap (a UNIQUE constraint IS already a full B-tree index),
  just a stale line in that task's own contract prose. Reuses the EXISTING `users` table
  byte-for-byte too — accept's provisioning is a plain INSERT (mirrors `get_or_provision_oidc_user`'s
  own new-row shape): tenant_id=invite.tenant_id, email=invite.email, role=invite.role,
  password_hash=hash(password), auth_method='password' (column DEFAULT — no schema change).

Access pattern:
  PREVIEW (PreviewInviteUseCase -> InviteRepository.get_preview_by_token_hash, ONE read, no lock):
    SELECT invites.*, tenants.name AS tenant_name FROM invites JOIN tenants
      ON tenants.id = invites.tenant_id WHERE invites.token_hash = :hash
    -> no row: raise InviteNotFoundError (404) · status != 'pending': raise InviteNotPendingError
      (409) · status == 'pending' AND expires_at < :now: raise InviteExpiredError (410) ·
      else: return InvitePreview(tenant_name, email, role, expires_at)
  ACCEPT (AcceptInviteUseCase -> InviteRepository.accept, ONE transaction):
    1. SELECT * FROM invites WHERE token_hash = :hash FOR UPDATE
       -> no row: rollback, raise InviteNotFoundError (404)
    2. status != 'pending' -> rollback, raise InviteNotPendingError (409)
    3. status == 'pending' AND expires_at < :now -> rollback, raise InviteExpiredError (410)
    4. INSERT INTO users (tenant_id=invite.tenant_id, email=invite.email, role=invite.role,
       password_hash=:hash, auth_method='password')
       -> IntegrityError (global users.email unique) -> rollback EVERYTHING (invite stays
          'pending', unflipped) -> raise EmailAlreadyRegisteredError (409 ERR_TENANT_EMAIL_TAKEN)
    5. UPDATE invites SET status = 'accepted' WHERE id = invite.id
    6. COMMIT -> return (invite.tenant_id, new_user.id)
  Password-strength check (len(password) < MIN_PASSWORD_LENGTH -> WeakPasswordError, 400) runs in
    AcceptInviteUseCase BEFORE step 1's lock is taken — cheap, no-DB-IO checks first; the global-
    email-collision (step 4) is necessarily the LAST possible rejection, discoverable only at INSERT
    time. Rate-limit check (per-client-IP, fail-open) runs FIRST of all, before any DB IO, on BOTH
    endpoints.

RESOLVED open question (member-invite-issuance TASK.md §7 OBSERVE): `InviteRepository.
  get_by_id_and_tenant` is NOT the primitive this task reuses. It requires BOTH an invite_id AND a
  tenant_id as inputs, but this task's caller is unauthenticated and possesses NEITHER — only the
  plaintext token from the out-of-band link (never the row's id, never any tenant_id;
  MILESTONE.md's own server-side-only-resolution decision forbids trusting a client-supplied
  tenant_id regardless of the reason). The correct primitive is a lookup keyed PURELY by
  token_hash — `get_preview_by_token_hash`/`accept` above, with NO tenant_id parameter at all.
  `get_by_id_and_tenant` therefore remains genuinely unwired even after this task ships — flagged
  forward as a §7 OBSERVE spec-delta candidate (a future superadmin cross-tenant invite-inspection
  endpoint, or removal as dead code); not this task's call either way, since it did not introduce
  that symbol.

NEW domain symbol (gateway.tenants.domain.entities, sibling to Invite):
  InvitePreview(frozen dataclass): tenant_name: str, email: str, role: Role, expires_at: datetime
    (deliberately excludes id/tenant_id/token_hash/invited_by_user_id/status — an unauthenticated
    caller needs nothing beyond what they are about to join; status is implicitly "pending", the
    ONLY status this type is ever constructed for — see Access pattern above)

NEW domain error (gateway.tenants.domain.errors, sibling to InviteNotFoundError/InviteNotPendingError):
  InviteExpiredError: invite resolved and status IS 'pending', but expires_at has passed.
  (REUSED, not new: EmailAlreadyRegisteredError — already exists, raised on the signup path;
   WeakPasswordError — ditto.)

NEW infrastructure (gateway.tenants.infrastructure.invite_repository.InviteRepository — EXTENDS the
  EXISTING, shipped class; no existing method touched):
  get_preview_by_token_hash(*, token_hash: str, now: datetime) -> InvitePreview
    (raises InviteNotFoundError | InviteNotPendingError | InviteExpiredError)
  accept(*, token_hash: str, password_hash: str, now: datetime) -> tuple[uuid.UUID, uuid.UUID]
    (returns (tenant_id, new_user_id); raises InviteNotFoundError | InviteNotPendingError |
    InviteExpiredError | EmailAlreadyRegisteredError; ONE locked transaction, per Access pattern)

NEW infrastructure (gateway.tenants.infrastructure.invite_public_rate_limiter.py — mirrors
  keys/infrastructure/mint_rate_limiter.py's PlaygroundMintRateLimiter shape exactly):
  InviteRateLimitedError(Exception): __init__(self, retry_after: int)
  InvitePublicRateLimiter: fixed 60s window, Redis INCR+EXPIRE, fail-open on RedisError/OSError.
    check(*, key: str, limit: int) -> None   # key format: f"invite:public:rl:{key}:{bucket}"

NEW application use cases (gateway.tenants.application.invite_accept_use_cases.py, sibling to
  invite_use_cases.py):
  PreviewInviteUseCase.execute(*, token: str, now: datetime) -> InvitePreview
    (hashes token via Sha256SecretHasher, delegates to repo.get_preview_by_token_hash)
  AcceptInviteUseCase.execute(*, token: str, password: str, now: datetime)
    -> tuple[uuid.UUID, uuid.UUID, str]
    (returns (tenant_id, user_id, email); raises WeakPasswordError (len check, BEFORE hashing/repo
    call) then delegates to repo.accept; THIS is MILESTONE.md's named "one clean provisioning choke
    point" call site — the SECOND, alongside get_or_provision_oidc_user — for a future seat-cap hook)

NEW router (gateway.tenants.api.invite_accept_router.py, sibling to invites_router.py — a SEPARATE
  file/router instance, NOT added to invites_router.py, mirroring how agent_oauth splits its PUBLIC
  device_authorize_router.py from its AUTHENTICATED device_approval_router.py despite sharing one
  underlying table):
  invite_accept_router = APIRouter(prefix="/invites", tags=["invites-public"])
  GET  /invites/{token}            (no auth dependency at all — PUBLIC)
  POST /invites/{token}/accept     (no auth dependency at all — PUBLIC)
  Mounted in main.py immediately after `app.include_router(invites_router)` (main.py:1101).

NEW error_catalog.py entry (sibling to INVITE_NOT_FOUND/INVITE_NOT_PENDING):
  INVITE_EXPIRED = ErrorSpec(410, "ERR_INVITE_EXPIRED", "Invite has expired")
  (REUSED, not new: INVITE_NOT_FOUND · INVITE_NOT_PENDING · AUTH_PASSWORD_WEAK · AUTH_EMAIL_TAKEN ·
   RATE_LIMITED — every one of this task's OTHER rejections reuses an existing ErrorSpec verbatim.)

NEW config.py Settings fields (join the existing positive-knob field_validator style):
  invite_preview_rpm: int = 30   # GATEWAY_INVITE_PREVIEW_RPM
  invite_accept_rpm: int = 10    # GATEWAY_INVITE_ACCEPT_RPM

NEW main.py wiring:
  app.state.invite_public_limiter = InvitePublicRateLimiter(redis=redis_client)   # beside
    agent_oauth_ip_limiter/playground_mint_limiter, ~main.py:920-925; same redis_client, no new
    connection.

NEW audit action (fire-and-forget via record_audit, mirroring invite.create/invite.revoke — M8):
  "invite.accept" (metadata: tenant_id, user_id, email)
```

Glossary deltas: none new — this task fulfills the state transition ALREADY named in the existing
  "invite (pending invite)" glossary entry (`pending -> accepted | revoked`, both terminal); it does
  not introduce a new business concept. `InvitePreview` is an internal DTO, not a glossary-worthy term.

Status: DRAFT
Reported: no

**Least-sure flag surfaced at freeze (drafted, not yet presented):** (bundle lowest-confidence, ranked)
  ⚠ [spec] Preview enforces the SAME 404/409/410 gate as accept — MY OWN reasoned extension, not
    verbatim in MILESTONE.md (which only describes preview's happy path). If wrong, the NOT-YET-
    drafted `member-invite-ui` sibling may instead want preview to always 200 with a `status` field
    — a contract-shape change to THIS GET response only (a change request back to Specify); accept's
    shape and every other decision here are unaffected.
  ⚠ [spec] Rate-limiting PREVIEW (not just accept, which is all MILESTONE.md's text literally names)
    is my own defense-in-depth addition. If Tin considers this unnecessary, it is a one-line deletion
    with zero contract-shape impact.
  ⚠ [contract] `InviteRepository.get_by_id_and_tenant` (left "contract-mandated, currently unwired"
    by issuance's own §7 OBSERVE) is RESOLVED here as NOT the right primitive (see the RESOLVED open
    question above) — it remains unwired even after this task ships. Flagged forward as a spec-delta,
    not a blocker: nothing in THIS contract depends on that symbol ever gaining a caller.
  None of these three flags block freezing the rest of the shape — each is scoped so that either
  resolution leaves the OTHER decisions in this contract (routes, DTOs, error codes, schema) unchanged.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY (new
     terms declared as a Glossary delta) + the bundle's lowest-confidence flag was surfaced at
     the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (new `invite_accept_router.py` / `invite_accept_use_cases.py` /
  `invite_public_rate_limiter.py` / the 2 new `InviteRepository` methods)

RED-FOR-THE-RIGHT-REASON NOTE: pre-Build, `/invites/{token}` and `/invites/{token}/accept` do not
  exist, so FastAPI's own unmatched-route handler already returns 404 `{"detail": "Not Found"}` for
  EVERY request regardless of path. A test that asserted only `status_code == 404` for the
  unknown-token scenarios would therefore pass RED for the WRONG reason. Every test below asserts the
  SPECIFIC `code` field (or, for 200/expected-success cases, the exact response body/DB state) —
  FastAPI's default not-found body has no `code` key, so it can never satisfy
  `resp.json().get("code") == "ERR_..."`; only the real, built endpoint can. All tests are HTTP-level
  (via the shared `client`/`db_session` fixtures) — no test imports a not-yet-existing module at
  collection time, so a Build that is 0% complete still yields a clean pytest collection (all tests
  found, all FAIL on assertion mismatches — 404-wrong-body or 404-instead-of-200 — never
  ImportError/ModuleNotFoundError).

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_preview_happy_path: arrange a pending invite (via the shipped POST /admin/invites) / act GET
    /invites/{token} / assert 200 + EXACT body key set {tenant_name, email, role, expires_at} + assert
    invite row unchanged · covers: M1
  - test_accept_happy_path: arrange a pending invite / act POST /invites/{token}/accept {password} /
    assert 200 + EXACT body {tenant_id, user_id, email} + assert a new users row (tenant_id, email,
    role, auth_method='password', real hash) + assert invite row now 'accepted' + assert an
    invite.accept audit row exists · covers: M2, M8
  - test_accept_ignores_client_supplied_identity_fields: arrange a pending invite / act POST
    .../accept with extra {tenant_id, role, email} injected in the body / assert the provisioned
    user's tenant_id/role/email match the INVITE, not the injected values · covers: M3
  - test_preview_unknown_token_404: arrange nothing (a random token) / act GET /invites/{random} /
    assert 404 + code == ERR_INVITE_NOT_FOUND · covers: M4, R1
  - test_preview_revoked_token_409: arrange a pending invite then DELETE it via the shipped
    /admin/invites/{id} / act GET /invites/{token} / assert 409 + code == ERR_INVITE_NOT_PENDING +
    assert status still 'revoked' · covers: M4, R2
  - test_preview_already_accepted_token_409: arrange + accept an invite (via this task's own POST
    .../accept) / act GET /invites/{token} again / assert 409 + code == ERR_INVITE_NOT_PENDING ·
    covers: M4, R2
  - test_preview_expired_token_410: arrange a pending invite then backdate expires_at via raw SQL /
    act GET /invites/{token} / assert 410 + code == ERR_INVITE_EXPIRED + assert status still
    'pending' · covers: M4, R3
  - test_accept_unknown_token_404: act POST /invites/{random}/accept {password} / assert 404 + code
    == ERR_INVITE_NOT_FOUND + assert no users row created · covers: R1
  - test_accept_revoked_token_409: arrange + revoke (shipped DELETE) / act POST .../accept / assert
    409 + code == ERR_INVITE_NOT_PENDING + assert no users row created · covers: R2
  - test_accept_already_accepted_token_409: arrange + accept once (200) / act accept AGAIN with the
    same token / assert 2nd response 409 + code == ERR_INVITE_NOT_PENDING + assert still exactly ONE
    users row for that email · covers: R2
  - test_accept_expired_token_410: arrange a pending invite then backdate expires_at / act POST
    .../accept / assert 410 + code == ERR_INVITE_EXPIRED + assert no users row created · covers: R3
  - test_accept_weak_password_400: arrange a pending invite / act POST .../accept
    {password: "short"} / assert 400 + code == ERR_AUTH_PASSWORD_WEAK + assert invite still pending +
    assert no users row created · covers: R4
  - test_accept_global_email_collision_409: arrange a pending invite in tenant A for
    "collide@co.io", then signup a REAL user with that same email in a DIFFERENT tenant B / act POST
    tenant A's .../accept / assert 409 + code == ERR_TENANT_EMAIL_TAKEN + assert tenant A's invite
    still 'pending' + assert tenant B's user row untouched + assert no second users row created ·
    covers: M6, R5
  - test_preview_rate_limited_429: arrange the low-rpm app/client fixture
    (GATEWAY_INVITE_PREVIEW_RPM=2) + 3 GET calls from one IP / assert the 3rd is 429 + code ==
    ERR_RATE_LIMITED + Retry-After header present · covers: M7, R6
  - test_accept_rate_limited_429: arrange the low-rpm app/client fixture
    (GATEWAY_INVITE_ACCEPT_RPM=2) + 3 distinct pending invites + 3 accept calls from one IP / assert
    the 3rd is 429 + code == ERR_RATE_LIMITED + Retry-After header + assert the 3rd invite still
    'pending' and no users row created for it · covers: M7, R6
  - test_concurrent_double_accept_never_two_successes: arrange one pending invite / act two
    concurrent POST .../accept calls via asyncio.gather / assert EXACTLY one 200 and one 409 (never
    two 200s) + assert exactly ONE users row exists for that email · covers: M5 (concurrency edge)
</test_plan>

M9 (no migration) and M10 (existing /admin/invites surface untouched) are BUILD-TIME constraints, not
  behaviors a red-then-green test can observe (both are already vacuously true pre-Build, so no test
  on them would ever meaningfully redden) — enforced instead via this task's own declared §5 Scope
  (no `migrations/` path listed; no existing issuance file listed as touchable except additively via
  `invite_repository.py`) and confirmed by the shipped issuance suite's own 30 tests staying green
  after this task's Build (a regression check, not a new test).

Tests live in: `apps/gateway/tests/member_invite_acceptance/` · MUST run red (missing implementation)
  before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
  `apps/gateway/src/gateway/tenants/domain/entities.py` (add `InvitePreview` only)
  `apps/gateway/src/gateway/tenants/domain/errors.py` (add `InviteExpiredError` only)
  `apps/gateway/src/gateway/tenants/infrastructure/invite_repository.py` (ADD
    `get_preview_by_token_hash`/`accept` — do not touch any existing method)
  `apps/gateway/src/gateway/tenants/infrastructure/invite_public_rate_limiter.py` (NEW)
  `apps/gateway/src/gateway/tenants/application/invite_accept_use_cases.py` (NEW)
  `apps/gateway/src/gateway/tenants/api/invite_accept_router.py` (NEW)
  `apps/gateway/src/gateway/core/error_catalog.py` (add `INVITE_EXPIRED` only — SHARED file, other
    parallel builds in this milestone also touch this; expect a merge reconciliation)
  `apps/gateway/src/gateway/core/config.py` (add `invite_preview_rpm`/`invite_accept_rpm` + validator)
  `apps/gateway/src/gateway/main.py` (mount `invite_accept_router` + wire `invite_public_limiter` —
    SHARED file, same merge caveat)
  `apps/gateway/tests/member_invite_acceptance/` (this task's own test directory)
  Explicitly OUT of scope: `apps/gateway/migrations/` (M9 — no migration) ·
    `apps/gateway/src/gateway/tenants/api/invites_router.py` ·
    `apps/gateway/src/gateway/tenants/application/invite_use_cases.py` (M10 — issuance's shipped
    surface stays untouched) · anything under `apps/dashboard/` (member-invite-ui's job).
Strategy (ordered batches): 1. domain (`InvitePreview`, `InviteExpiredError`) 2. infrastructure
  (`InviteRepository.get_preview_by_token_hash`/`.accept`, re-running the shipped issuance suite
  after each to confirm zero regression) 3. `invite_public_rate_limiter.py` (mirror
  `PlaygroundMintRateLimiter` line-for-shape) 4. application (`invite_accept_use_cases.py`)
  5. API (`invite_accept_router.py` + `main.py` registration + `error_catalog.py`/`config.py`
  entries) 6. tests per scenario, confirm the concurrency test actually forces the race (explicit
  `asyncio.Event`/barrier, not scheduler-timing luck — issuance's own OBSERVE flagged exactly this
  gap on its analogous re-invite race test; do not repeat it here).

Persona (optional): `backend-architect.md` (`flow: build, advisor` — the closest repo persona; ports-
  and-adapters/hexagonal-layering lens for this exact FastAPI gateway). ONE of its own Critical Rules
  is in TENSION with this task's own established precedent, named here rather than left for Build to
  discover mid-flight: its "Default Requirement" wants every new use-case capability wired as a
  `typing.Protocol` port, yet the EXISTING, shipped `invite_use_cases.py` imports the CONCRETE
  `InviteRepository` class directly (`application/invite_use_cases.py:34`) — `domain/ports.py` has
  ZERO `Invite*` port today (confirmed by direct read, not assumed). RECOMMENDATION (a PREFERRED call,
  not a rule): mirror the EXISTING sibling's concrete-import style in the NEW
  `invite_accept_use_cases.py` for bounded-context consistency — two near-identical sibling use-case
  files should not diverge on dependency style — rather than introduce a Protocol port unilaterally
  for only the new half of one bounded context; reversible at negligible cost if Build judges
  otherwise. The REST of backend-architect.md applies cleanly and IS worth following as-is: inward-only
  dependency direction for `domain/entities.py`/`errors.py` (already satisfied, §0) · name the
  concurrency-safety primitive explicitly (already done — `SELECT ... FOR UPDATE`, §3 Access pattern)
  · a mutating repository method's test must re-fetch after commit, not trust the HTTP response alone
  (already satisfied — every §4 test queries `db_session` directly for invite/user row state).
Known-problem fixes: route-vs-logic 404 ambiguity (§4's own RED-FOR-THE-RIGHT-REASON note) → every
  test asserts the specific `code` field, never status_code alone · MissingGreenlet on a post-
  rollback attribute read (the SAME trap `InviteRepository.revoke` already dodges by capturing
  `row.status` BEFORE calling `rollback()`) → capture whatever's needed from the locked row BEFORE
  any `rollback()` call in the new `accept()` method too.
Safety rule (feature-specific): the accept transaction (lock -> validate -> INSERT users -> UPDATE
  invites -> commit) is ONE atomic unit with no partial-success path — any exception at any step
  (not-pending, expired, IntegrityError on the global email collision) rolls back the WHOLE
  transaction, never just the failing step, so the invite is never left half-flipped and no orphaned
  users row is ever left behind (M5's fail-closed requirement, CLAUDE.md design-for-failure).
Code lives in: `apps/gateway/src/gateway/tenants/`
Constraints: do NOT change any test or the contract; allow-list packages only (none new needed — no
  new third-party dependency this task requires); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) is live: a completing verify gate refuses an
     out-of-scope build (scope_violation → self-heal) and add.py check surfaces it.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [ ] <observable outcome a correct build must produce> — confirmed by <how / where>
- [ ] <another observable outcome> — confirmed by <evidence seen>

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> §0's Ground SHA anchors the symbols cited at ground time to that commit — code moves during
> build. Before the gate, re-resolve every symbol §3 CONTRACT cites against the CURRENT tree
> (not the Ground SHA) so a stale anchor is caught here, not by a future reader chasing a moved
> line.
- [ ] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by <how / where>
- [ ] any anchor that moved/renamed since Ground SHA is named here, not left silent

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Under autonomy: auto run the 3-lens checklist and record the verdict here. Lenses run in
> order; a Security HARD-STOP ends the checklist (leave remaining lenses blank). Binding for
> sensitivity: mechanical (advisor-gate-relax reads it); advisory for all other sensitivities.
> The engine MEASURES this block is filled (audit: advisor_verdict_unrecorded); it never blocks.
Advisor: <agent-id | self>
1. Security: <CLEAR | HARD-STOP: finding>
2. Concurrency: <CLEAR | RESIDUE: finding>
3. Architecture: <CLEAR | RESIDUE: finding>
Verdict: <PASS | HARD-STOP>
Residue: <none | summary>
Binding: <yes — mechanical | advisory — <sensitivity>>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. The Advisor 3-lens verdict and the Refute-read verdict are both measured by `add.py audit` (`advisor_verdict_unrecorded` · `refute_unrecorded`) — neither is engine-blocked; a human spot-audit is the backstop for any finding the AI did not surface or record. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
