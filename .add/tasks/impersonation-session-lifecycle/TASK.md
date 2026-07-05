# TASK: Impersonation session lifecycle

slug: impersonation-session-lifecycle · created: 2026-07-03 · stage: production
milestone: tenant-impersonation
sensitivity: security
autonomy: auto
phase: done

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `Identity` (`apps/gateway/src/gateway/tenants/domain/entities.py:30-37`) — the frozen 4-field
    shape (tenant_id/role/user_id/email) this task ADDITIVELY extends with a new optional field
    (illustratively `impersonation: ImpersonationContext | None`) — never overwrites the 4 existing
    fields' meaning.
  - `JwtTokenService.issue()`/`.decode()` (`tenants/infrastructure/jwt_service.py:20-56`) — extend to
    carry the new optional claim; must remain able to issue/decode an ORDINARY (non-impersonation)
    token byte-identically to today.
  - `ROLE_PERMISSIONS`/`Role.SUPERADMIN` (`tenants/domain/authz.py:96-108`) — confirmed by direct read
    this session: `Role.SUPERADMIN: frozenset(Permission)` (full parity with OWNER, no tenant-scoping
    within the matrix itself). THE central risk this task's contract must close: an impersonation
    identity's `role` field must be the TARGET user's own role, NEVER `Role.SUPERADMIN` — confirmed
    `require_superadmin` (`authz.py:210-233`) gates purely on `identity.role != Role.SUPERADMIN`
    (line 231), so this is both the trap AND the containment mechanism, verified from the same code.
  - `authorize_tenant_scope()` (`authz.py:134-...`, FROZEN @ v1) — reused verbatim for the mint
    endpoint's own superadmin+target-tenant check; NOT modified.
  - `emit_platform_audit` (`apps/gateway/src/gateway/audit/application/platform_audit.py:15-16,50-52`,
    FROZEN @ v1) — reused verbatim for session start/end audit emission; its documented contract
    already requires "identity: the SUPERADMIN caller — always supplies the REAL actor_user_id/
    actor_email," which this task's dual-identity design must satisfy without modifying the helper.
  - NEW session-store record (mirrors the existing nullable `revoked_at`-timestamp pattern —
    `keys/infrastructure/orm.py:65` `RevokeKeyUseCase`, and `agent_oauth/infrastructure/orm.py:105`
    `agent_oauth/domain/entities.py:42`) — NOT a bare stateless JWT `exp` claim, so an explicit "End"
    action can durably, immediately revoke a session rather than waiting out its TTL.
  - NEW route family `POST /admin/platform/tenants/{tenant_id}/users/{user_id}/impersonate` (+ an end
    route), mounted alongside the existing `platform_*_router.py` siblings
    (`tenants/api/platform_users_router.py` is the nearest sibling — same `require_superadmin` +
    `authorize_tenant_scope` gating shape).
  - `jwt_ttl_seconds` default (`core/config.py:89`, `86400`) — the impersonation session's TTL must
    be materially shorter than this; exact value is this task's own contract decision, not pre-set
    here.

Context (working folder): `.add/milestones/tenant-impersonation/MILESTONE.md` (Scope/Shared-
  decisions/Shared-contracts just drafted and independently verified this session — in particular
  the "dual-identity principle" and "actor-attribution invariant" sections, which this task's §3
  CONTRACT must satisfy); `.add/tasks/cross-tenant-keys-members/TASK.md` §0 (the M13 note that
  originally deferred the playground-token endpoint to this milestone).

Honors (patterns / conventions):
  - Reuse-over-invent (platform-admin-console's own precedent, carried forward): mint/end reuse
    `authorize_tenant_scope`/`emit_platform_audit`/`require_superadmin` verbatim — no parallel authz
    or audit primitive invented for impersonation.
  - Additive-only extension of a frozen contract — `Identity`'s 4 fields, `authorize_tenant_scope()`,
    and `emit_platform_audit()`'s existing signatures are NOT modified, only additively consumed/
    extended (mirrors how `OtelSpan` has grown optional fields over time without breaking existing
    callers, per the milestone doc's own citation).
  - Revocable-record-with-timestamp pattern already used twice in this codebase (API keys,
    agent_oauth grants) — the THIRD use of this pattern, not a new mechanism.

Anchors the contract cites: `Identity` (entities.py:30-37), `Role.SUPERADMIN`/`ROLE_PERMISSIONS`
  (authz.py:96-108), `require_superadmin` (authz.py:210-233), `JwtTokenService.issue/.decode`
  (jwt_service.py:20-56), `authorize_tenant_scope` (authz.py), `emit_platform_audit`
  (platform_audit.py:15-16,50-52).

Issues/Risks (→ feed §1):
  - **PRIVILEGE-ESCALATION TRAP, confirmed by direct code read (not assumed)**: keeping
    `role=Role.SUPERADMIN` on an impersonation identity while only swapping `tenant_id` would grant
    the session EVERY permission (`authz.py:108`) regardless of the target user's real, possibly far
    narrower role — silently defeating "act as a specific tenant user." The dual-identity design
    (role/tenant_id/user_id/email = target's real values; superadmin identity carried in a NEW
    additive field) is the confirmed-correct fix, and is itself the single highest-stakes decision
    this task's §3 CONTRACT freezes — MANDATORY HARD-STOP security review at that freeze, per this
    project's own non-negotiable rule ("a security finding is always HARD-STOP").
  - Nested-impersonation / confused-deputy risk: an impersonation identity's `role` is never
    `SUPERADMIN`, so it structurally cannot pass `require_superadmin` again — confirmed by the same
    code read, not merely asserted; §3 should still name this explicitly as a scenario to test, not
    just an incidental consequence.
  - Valid-target guardrails not yet decided at the freeze: only ordinary (non-superadmin) users of a
    non-platform tenant may be targeted (closes a superadmin-impersonating-superadmin laundering
    risk) — which of the 6 non-superadmin roles are valid targets is flagged OPEN by the milestone
    doc (default-leaning: any of the 6, since the dual-identity design already caps the session at
    that role's own permissions) — this task's own §1 SPECIFY must resolve it, not silently inherit
    the milestone doc's lean.
  - Session revocation mechanism is itself HARD-STOP-flagged (shared with `impersonation-live-
    session-guard`, which owns the per-request live re-check; THIS task owns the mint/end shape and
    the store record itself).

Related intent: `.add/milestones/tenant-impersonation/MILESTONE.md` goal + Scope + Shared decisions
  (drafted and independently verified this session, including direct confirmation of the SUPERADMIN-
  permission-grant and require_superadmin-gate claims). GLOSSARY term to add at this milestone's
  fold: "impersonation session." Originating intent: milestone 3 of Tin's confirmed "Full 5,
  admin-first" superadmin roadmap; also closes `cross-tenant-keys-members` TASK.md's own deferred M13
  gap (playground-token minting excluded from the cross-tenant console because it is "functionally
  'acting as' that tenant").

Ground SHA: 9740f21

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Impersonation session mint/end — dual-identity JWT + revocable session-store record
Framings weighed:
Dual-identity shape as the additive `Identity.impersonation: ImpersonationContext | None` field
(chosen — MILESTONE.md's own HARD-STOP-flagged Shared decision; independently re-verified this
session against real code that `ROLE_PERMISSIONS[Role.SUPERADMIN] == frozenset(Permission)`
(authz.py:108, full parity with OWNER) and `require_superadmin` gates purely on
`identity.role != Role.SUPERADMIN` (authz.py:231) — so the 4 primary `Identity` fields on an
impersonation session MUST be the target's own real values, never the superadmin's) · vs keeping
`role=Role.SUPERADMIN` and only swapping `tenant_id` (rejected — the CONFIRMED
privilege-escalation trap: grants the session literal ALL Permissions regardless of the target's
real, possibly far narrower role, defeating "act as a specific tenant user" entirely).
Valid impersonation-target roles — RESOLVED here (left open by MILESTONE.md for this task): all 6
non-superadmin roles (OWNER, ADMIN, OPERATOR, BILLING_ADMIN, VIEWER, MEMBER) are valid targets
(chosen) · vs a narrower support-safe subset, e.g. VIEWER/MEMBER only (rejected). Reasoning: (1)
the dual-identity design already caps a session's permissions at the target's own
`ROLE_PERMISSIONS` entry through the completely unmodified authz surface, so impersonating an
OWNER is mechanically no more powerful than that OWNER's own legitimate session — restricting
target roles doesn't shrink what the session can DO, only who it can be started as; (2) a
superadmin already has equivalent-or-greater, un-time-boxed reach into any tenant's OWNER-level
surface today via the shipped `cross-tenant admin surface` (`platform_keys_router.py`/
`platform_users_router.py`/`platform_tenant_config_router.py`, GLOSSARY.md:33) — a
role-eligibility restriction here closes no capability a malicious/careless superadmin lacks
another way to reach, while crippling the feature's actual support/diagnostic purpose (e.g.
reproducing an OWNER's own provider-key misconfiguration — the exact `cross-tenant-keys-members`
M13 gap this milestone exists to close); (3) this task's real, role-independent security levers —
hard TTL, durable explicit revocation, real-actor audit attribution — are where
privileged-access-management guidance (time-box + audit + revoke) says the posture belongs, not
in a second, ad-hoc role-allowlist parallel to `ROLE_PERMISSIONS`. Flagged as this task's #1
lowest-confidence item below.
Session-store keyed by the REAL actor (`actor_user_id`), one active row per actor at a time
(chosen) — "starting a second session while one is active" (§0 Issues) is naturally per-CALLING-
superadmin, matching the milestone's own singular "you are impersonating X as Y" UI framing · vs
a global cross-superadmin lock, or a per-TARGET lock blocking two different superadmins from
concurrently impersonating the same user (both rejected — neither is asked for anywhere in
MILESTONE.md or this task's own GROUND, and a per-target lock adds real complexity for no stated
security rationale; each session remains independently, correctly actor-attributed regardless).
End authenticated by the REAL superadmin's ORDINARY (non-impersonation) JWT, under
`/admin/platform/*`, behind the SAME `require_superadmin` gate as Mint and every sibling platform
router (chosen) · vs accepting the ACTIVE impersonation token itself to call End (rejected —
MILESTONE.md's own CONFIRMED containment property holds that an impersonation identity can never
reach `/admin/platform/*` because its `role` is never `SUPERADMIN`; carving one exception into
that property for End turns an airtight, zero-exception guarantee into a
guarantee-with-one-documented-hole for a marginal convenience `impersonation-ui` can absorb
instead, by retaining the superadmin's original token client-side purely to power its persistent
End control). Flagged as this task's #2 lowest-confidence item below.
End targeted by `session_id` (returned at Mint, embedded in the impersonation JWT's own claim), no
`authorize_tenant_scope` call (chosen — unambiguous across a target's possibly-multiple past
sessions, and mirrors `authz.py`'s own "list every tenant" precedent: "no single target_tenant_id,
so `authorize_tenant_scope` doesn't apply") · vs `DELETE .../tenants/{tenant_id}/users/{user_id}/
impersonate` (rejected — ambiguous which past session is meant, forces resupplying data already
in the token/response).
Any superadmin — not only the one who started it — may End any active session (chosen — matches
`ROLE_PERMISSIONS[SUPERADMIN]` having no per-superadmin ownership concept anywhere else in this
codebase, and is the STRONGER security property: an incident responder must be able to kill a
colleague's compromised or stuck session without waiting on that colleague) · vs restricting End
to the session's own starting superadmin (rejected — weakens "durably, immediately revoke," this
milestone's core promise, for a restriction this codebase's authz model doesn't otherwise
recognize).
End hard-rejects (409), rather than idempotently 204s, on an already-ended/expired `session_id`
(chosen — matches this draft's own Reject enumeration requiring a mapped error code here, and a
superadmin caller has no enumeration-risk reason to prefer a silent no-op) · vs idempotent 204 (a
legitimate, defensible alternative DELETE-idiom reading — noted, not chosen).
New route family in `tenants/api/platform_impersonation_router.py` (Mint + End, no shared
router-level path prefix so Mint's `/tenants/{tenant_id}/users/{user_id}/impersonate` and End's
`/impersonation/sessions/{session_id}` coexist) — mirrors "mounted alongside the existing
platform_*_router.py siblings" (§0); the session-store aggregate (domain/infra/application) lives
in a NEW `gateway/impersonation/` package, mirroring `agent_oauth`'s own dedicated-package
precedent for a conceptually similar revocable grant/session store. Container placement is
Build's discretion, not contract-binding.

Must:
<must>
  - M1: `Identity` (`tenants/domain/entities.py:31-38`) gains ONE new, additive, optional field:
    `impersonation: ImpersonationContext | None = None`. Every existing call site constructing an
    `Identity` without it is unaffected (default `None`). `ImpersonationContext` is a NEW frozen
    dataclass — `session_id: uuid.UUID`, `actor_user_id: uuid.UUID`, `actor_tenant_id: uuid.UUID`,
    `actor_email: str` — carrying the REAL superadmin's identity plus the session-store row's id.
    On an impersonation identity, the 4 EXISTING `Identity` fields (`user_id`/`tenant_id`/`email`/
    `role`) are ALWAYS the TARGET user's own real, current-at-mint-time values — never the
    superadmin's — so `ROLE_PERMISSIONS`, `authorize_tenant_scope`, and every repository's
    `WHERE tenant_id=...` filter behave exactly as for that real user, completely unmodified.
  - M2: `JwtTokenService.issue()` (`jwt_service.py:20-33`) and the `TokenService` Protocol
    (`tenants/domain/ports.py:43-48`) each gain TWO new optional kwargs:
    `impersonation: ImpersonationContext | None = None` and `ttl_seconds: int | None = None`
    (`None` ⇒ use `self._ttl`, i.e. today's `jwt_ttl_seconds` — byte-identical claims dict for
    every EXISTING caller, which passes neither). When `impersonation` is supplied, the encoded
    claims gain exactly one additional, NOT-required, `"impersonation"` key (nested `session_id`/
    `actor_user_id`/`actor_tenant_id`/`actor_email`, UUIDs as `str`) alongside the unchanged
    `sub`/`tenant_id`/`role`/`email`/`iat`/`exp`/`iss`.
  - M3: `JwtTokenService.decode()` (`jwt_service.py:35-56`) reconstructs `Identity.impersonation`
    from the optional `"impersonation"` claim when present; absent ⇒ `impersonation=None`,
    byte-identical to today for every ordinary token (the claim is never added to
    `options={"require": [...]}`). `decode()` additionally, defensively raises `InvalidTokenError`
    (same family as any other malformed-claim rejection) for any token claiming BOTH
    `role == Role.SUPERADMIN` AND a present `"impersonation"` claim — a combination the mint flow
    (M5-M6) never produces; checked here purely as defense-in-depth against a future minting bug,
    not against external forgery (the HS256 signature already prevents that).
  - M4: A NEW session-store table (`ImpersonationSession` domain entity + ORM row — the THIRD use
    of the shipped nullable-`revoked_at`-timestamp pattern after `api_keys`
    (`keys/infrastructure/orm.py:65`) and `agent_tokens` (`agent_oauth/infrastructure/orm.py:105`))
    persists, per session: `id`, `actor_user_id`/`actor_tenant_id`/`actor_email` (the REAL
    superadmin, snapshotted at mint), `target_user_id`/`target_tenant_id`/`target_role`/
    `target_email` (the target, snapshotted at mint — `target_role` is the target's `Role` AT
    MINT TIME, never re-derived by this task), `created_at`, `expires_at` (hard TTL = mint time +
    a NEW `impersonation_session_ttl_seconds` Settings field, default 900s/15min — materially
    shorter than `jwt_ttl_seconds`'s 86400s default — own fail-loud `> 0` validator mirroring
    `playground_token_ttl_seconds`'s), `revoked_at` (nullable — set only by explicit End),
    `revoked_reason` (nullable, descriptive-only: `"explicit_end"` | `"expired_lazy_cleanup"`).
  - M5: `POST /admin/platform/tenants/{tenant_id}/users/{user_id}/impersonate` mints a session —
    no request body. Gate order (mirrors `platform_users_router.py`'s own `_require_target_tenant`):
    `require_superadmin` (Depends) → `authorize_tenant_scope(identity, tenant_id)` →
    `get_tenant_by_id` (404 if missing) → target-eligibility checks (M6) → single-active-session
    precondition (M7) → persist the session-store row (M4) → issue the dual-identity JWT (M1-M3)
    → `emit_platform_audit(request.app.state.sessionmaker, identity=<the REAL calling
    superadmin>, target_tenant_id=tenant_id, action="platform.impersonation.start",
    target_type="user", target_id=str(user_id), metadata={"session_id": ..., "target_role": ...,
    "expires_at": ...})` — UNCHANGED, verbatim call shape — → 201
    `{token, expires_in, session_id, target: {user_id, tenant_id, email, role}}`.
  - M6: Valid-target enforcement, BOTH checked explicitly by this task's own code (never relying
    solely on the DB trigger): (a) the PATH `tenant_id`'s `TenantRow.kind`
    (`tenants/infrastructure/orm.py:61`, CHECK-constrained to `'customer'|'platform'`) must be
    `"customer"`, never `"platform"`; (b) the resolved target `User.role` must never be
    `Role.SUPERADMIN` — defense-in-depth alongside the existing DB trigger
    (`5b34ca5e1c4b_superadmin_platform_tenant_guard.py`) that already makes (b) structurally
    unreachable once (a) holds.
  - M7: A superadmin may hold at most ONE active (`revoked_at IS NULL AND expires_at > now()`)
    impersonation session at a time. Mint first lazily revokes (`revoked_reason=
    "expired_lazy_cleanup"`) any of the CALLING superadmin's own prior rows that are past
    `expires_at` but never explicitly ended, THEN checks for a remaining active row. A race
    between two concurrent Mint calls for the SAME superadmin is closed by a partial unique index
    (`actor_user_id` WHERE `revoked_at IS NULL`) — a resulting `IntegrityError` is translated to
    the same 409 as the ordinary precondition check (design-for-failure: the precondition read
    and the insert are not assumed atomic on their own).
  - M8: `DELETE /admin/platform/impersonation/sessions/{session_id}` ends a session — no request
    body. Gated `require_superadmin` ONLY — no `authorize_tenant_scope`, mirroring `authz.py`'s
    own "list every tenant" precedent (no single target `tenant_id` to ask about). May be called
    by ANY superadmin, not only the one who started the session. Resolve the row by `session_id`
    → if `revoked_at IS NOT NULL` OR `expires_at <= now()`, reject (see Reject) → else set
    `revoked_at=now()`, `revoked_reason="explicit_end"` → `emit_platform_audit(..., identity=<the
    REAL calling superadmin ending it>, target_tenant_id=<session.target_tenant_id>,
    action="platform.impersonation.end", target_type="user", target_id=str(session.target_user_id),
    metadata={"session_id": ..., "ended_by_original_actor": <bool>})` — UNCHANGED, verbatim call
    shape — → 204.
  - M9: Both Mint and End reuse `authorize_tenant_scope`, `require_superadmin`, and
    `emit_platform_audit` completely UNCHANGED — zero lines modified in `authz.py`,
    `platform_audit.py`, `audit_writer.py`, `audit_event.py`, or any audit migration. This task is
    purely an ADDITIVE consumer, plus one additive extension each of `Identity`, `JwtTokenService`,
    and the `TokenService` Protocol.
  - M10: This task's build stops at the session-store record and the Mint/End HTTP surface. It
    does NOT wire `revoked_at`/`expires_at` into the request-authentication path
    (`_resolve_identity`) or into any self-service endpoint's live per-request check — that
    consultation is `impersonation-live-session-guard`'s explicit, dependent responsibility. Nor
    does it re-derive the target's role live from the DB on any request after mint: the target's
    role is PINNED at mint-time, both in the JWT's own `role` claim and in the session-store row's
    `target_role` snapshot, for the entire session lifetime — a live diff of "current DB role vs.
    this snapshot" is `impersonation-live-session-guard`'s job, fed by the snapshot this task's
    schema exists to provide. This task's own obligation is that the session-store row is CORRECT
    and DURABLE the instant Mint/End run, so that dependent consumer can be built against a
    trustworthy record.
</must>

Reject:
<reject>
  - R1: missing/malformed Bearer token, on Mint or End -> "ERR_AUTH_TOKEN_MISSING" /
    "ERR_AUTH_TOKEN_INVALID" (401)
  - R2: valid token, caller role != SUPERADMIN, on Mint or End -> "ERR_AUTH_FORBIDDEN" (403) —
    this identically covers an ordinary tenant user AND an impersonation identity itself (its own
    `role` is the target's, never SUPERADMIN) attempting Mint, End, or any other
    `/admin/platform/*` route; same unchanged `require_superadmin` mechanism, no
    impersonation-specific carve-out
  - R3: Mint — target `tenant_id` does not resolve to a real tenant -> "ERR_TENANT_NOT_FOUND" (404)
  - R4: Mint — target tenant's `kind == "platform"` -> "ERR_IMPERSONATION_TARGET_INVALID" (403, NEW)
  - R5: Mint — target `user_id` does not resolve within the target tenant -> "ERR_USER_NOT_FOUND"
    (404)
  - R6: Mint — target user's role is (defense-in-depth) `Role.SUPERADMIN` ->
    "ERR_IMPERSONATION_TARGET_INVALID" (403, NEW — same code as R4, `detail` distinguishes the
    reason)
  - R7: Mint — the calling superadmin already has an active session ->
    "ERR_IMPERSONATION_SESSION_ALREADY_ACTIVE" (409, NEW)
  - R8: End — `session_id` never resolves to any minted row ->
    "ERR_IMPERSONATION_SESSION_NOT_FOUND" (404, NEW)
  - R9: End — `session_id` resolves, but `revoked_at IS NOT NULL` or `expires_at <= now()` already
    -> "ERR_IMPERSONATION_SESSION_ALREADY_ENDED" (409, NEW — covers BOTH a repeat explicit End and
    a never-explicitly-ended-but-naturally-expired session under one rule/code)
</reject>

After:
<after>
  - A superadmin holds at most one active session at a time, acting as a specific, eligible
    (non-superadmin-role, non-platform-tenant) target user; the resulting JWT's primary 4 claims
    are indistinguishable in shape from an ordinary token issued for that same user, plus an
    additive `impersonation` claim recovering the real actor.
  - The session-store row is the sole durable source of truth for "is this session still valid"
    (`revoked_at IS NULL AND expires_at > now()`); Mint and End are the only two writers of it.
  - Session start and session end each produce exactly one `audit_events` row via the UNCHANGED
    `emit_platform_audit`, with `actor_user_id`/`actor_email` always resolving to the REAL
    superadmin who called that specific route (Mint's caller and End's caller may be two different
    superadmins).
  - Zero lines change in `authorize_tenant_scope`, `require_superadmin`, `emit_platform_audit`,
    `ROLE_PERMISSIONS`, or any of the ~10 self-service `record_audit` call sites; an impersonation
    identity reaches the unmodified self-service `/admin/*` surface exactly as the target user
    would, and never `/admin/platform/*`.
  - Per-request enforcement of `revoked_at`/`expires_at` against the ordinary request path, live
    re-validation of the target's role/tenant against the mint-time snapshot, and
    mid-stream-proxy-at-expiry behavior are explicitly NOT delivered by this task —
    `impersonation-live-session-guard` is the dependent consumer of the schema frozen here.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Valid-target-role set = all 6 non-superadmin roles, not a narrower support-safe subset —
    lowest confidence because it is fundamentally a product-risk-appetite call dressed as a
    security-mechanism one: the reasoning (dual-identity already caps permissions per-role; a
    superadmin already has equivalent reach via the existing un-time-boxed cross-tenant admin
    surface; role-restriction wouldn't reduce actual blast radius) is sound, but Tin may simply
    want impersonation to feel narrower/support-only regardless of mechanical equivalence. If
    wrong: narrowing the allowed target-role set is a small, additive change to M6/R4 (one more
    condition on an existing check) — no JWT or session-store shape change, bounded to this task's
    own contract.
  ⚠ End requires the REAL superadmin's ORIGINAL (non-impersonation) JWT, never the active
    impersonation token — high confidence in the security reasoning (preserves an airtight,
    zero-exception `/admin/platform/*` containment property) but low confidence that the
    downstream `impersonation-ui` task has already assumed this shape: it implies the dashboard
    must retain the original superadmin token client-side, alongside the active impersonation
    token, specifically to power the persistent End control. If wrong: End would need a new
    dependency admitting an impersonation identity by its `impersonation` field rather than its
    `role`, reopening the "does this weaken containment" question this draft closes by not doing
    that — a bounded, this-task-only change-request, but one that would need to be resolved before
    `impersonation-ui` can proceed with confidence.
  - [x] any superadmin, not only the session's own starter, may End any active session —
    confirmed via `ROLE_PERMISSIONS[SUPERADMIN]` holding no per-superadmin ownership concept
    anywhere else in this codebase; treated as the stronger, not weaker, security property.
  - [x] `impersonation_session_ttl_seconds` default = 900s (15 min) — a policy knob, not an
    architectural one; cheaply changed via Settings/env var without any contract change.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Superadmin mints an impersonation session for an eligible target user   # M1, M5
  Given a SUPERADMIN identity, and a customer tenant T with an ADMIN-role user U (email u@x.test)
  When the SUPERADMIN calls POST /admin/platform/tenants/{T}/users/{U}/impersonate
  Then the response is 201 with a token, expires_in, session_id, and target={user_id: U, tenant_id: T, email: "u@x.test", role: "admin"}
  And a session-store row exists with actor_user_id = the superadmin's own id, target_user_id = U, target_role = "admin", revoked_at IS NULL

Scenario: The minted JWT's primary claims are the target's, never the superadmin's   # M1 (dual-identity)
  Given a SUPERADMIN (tenant T_super, id S) impersonates an ADMIN-role user U of tenant T_other
  When the returned token is decoded
  Then decode() returns an Identity with tenant_id=T_other, role="admin", user_id=U, email=U's email — NEVER T_super, "superadmin", or S
  And the Identity's impersonation field is present with actor_user_id=S, actor_tenant_id=T_super, session_id matching the minted session

Scenario: An ordinary (non-impersonation) token is issued and decoded byte-identically to before this task   # M2, M3
  Given an ordinary OWNER login via the existing /admin/auth/login flow
  When JwtTokenService.issue() is called exactly as today (no impersonation, no ttl_seconds kwarg)
  Then the encoded claims contain no "impersonation" key, and decode() returns an Identity with impersonation=None
  And every existing test asserting the ordinary token's claim shape still passes unchanged

Scenario: Mint rejects a target tenant that is the platform tenant   # M6, R4
  Given a SUPERADMIN identity and the platform tenant's own id P
  When the SUPERADMIN calls POST /admin/platform/tenants/{P}/users/{any-user-in-P}/impersonate
  Then the response is 403 ERR_IMPERSONATION_TARGET_INVALID
  And no session-store row is created and no JWT is issued

Scenario: Mint rejects a target user whose role is superadmin (defense-in-depth)   # M6, R6
  Given a User row directly fixture-provisioned with role=superadmin under a NON-platform tenant T (bypassing the DB trigger for test purposes only, to exercise the application-level check in isolation)
  When a SUPERADMIN calls POST /admin/platform/tenants/{T}/users/{that-user}/impersonate
  Then the response is 403 ERR_IMPERSONATION_TARGET_INVALID
  And no session-store row is created and no JWT is issued

Scenario: Mint rejects an unknown target tenant_id   # R3
  Given a SUPERADMIN identity and a tenant_id with no matching row
  When the SUPERADMIN calls POST /admin/platform/tenants/{tenant_id}/users/{any}/impersonate
  Then the response is 404 ERR_TENANT_NOT_FOUND
  And no session-store row is created

Scenario: Mint rejects an unknown target user_id within a real, eligible tenant   # R5
  Given a customer tenant T with no user matching some user_id
  When a SUPERADMIN calls POST /admin/platform/tenants/{T}/users/{user_id}/impersonate
  Then the response is 404 ERR_USER_NOT_FOUND
  And no session-store row is created

Scenario: A non-superadmin caller is rejected on Mint regardless of their own permissions   # R2
  Given an OWNER identity (holds every non-superadmin Permission) for tenant T_owner, and a target tenant T_other with user U
  When the OWNER calls POST /admin/platform/tenants/{T_other}/users/{U}/impersonate
  Then the response is 403 ERR_AUTH_FORBIDDEN
  And no session-store row is created, and T_owner's own tenant-scoped surface remains unaffected

Scenario: Mint rejects starting a second session while one is already active for the same superadmin   # M7, R7
  Given a SUPERADMIN who already holds one active session (against target U1 of tenant T1)
  When that SAME superadmin calls POST /admin/platform/tenants/{T2}/users/{U2}/impersonate for a different eligible target
  Then the response is 409 ERR_IMPERSONATION_SESSION_ALREADY_ACTIVE
  And no new session-store row is created, and the original active session against U1 is completely unaffected

Scenario: A naturally expired, never-explicitly-ended prior session does not block a new Mint   # M7 (lazy-expire)
  Given a SUPERADMIN whose only session-store row has expires_at in the past and revoked_at still NULL
  When that superadmin calls POST /admin/platform/tenants/{T}/users/{U}/impersonate for an eligible target
  Then the response is 201 with a new session_id
  And the prior row is now revoked_at NOT NULL, revoked_reason="expired_lazy_cleanup", distinct from the new row

Scenario: Two different superadmins may each hold their own independent active session concurrently   # M7 (boundary)
  Given superadmin S1 already holds an active session, and a different superadmin S2 holds none
  When S2 calls POST /admin/platform/tenants/{T}/users/{U}/impersonate for an eligible target
  Then the response is 201 for S2
  And S1's own active session is completely unaffected — the one-active-session rule is per-actor, not global

Scenario: Superadmin ends their own active session   # M8
  Given a SUPERADMIN holds an active session with session_id = SID, targeting user U of tenant T
  When that superadmin calls DELETE /admin/platform/impersonation/sessions/{SID}
  Then the response is 204
  And the session-store row now has revoked_at NOT NULL, revoked_reason="explicit_end"

Scenario: A different superadmin may end another superadmin's active session   # M8 (decision)
  Given superadmin S1 holds an active session with session_id = SID
  When a DIFFERENT superadmin S2 calls DELETE /admin/platform/impersonation/sessions/{SID}
  Then the response is 204
  And the session-store row now has revoked_at NOT NULL, revoked_reason="explicit_end"

Scenario: End rejects an unknown session_id   # R8
  Given a session_id that was never minted by this system
  When a SUPERADMIN calls DELETE /admin/platform/impersonation/sessions/{session_id}
  Then the response is 404 ERR_IMPERSONATION_SESSION_NOT_FOUND

Scenario: End rejects an already-ended session_id (double-End)   # R9
  Given a session that was already explicitly ended (revoked_at NOT NULL, revoked_reason="explicit_end")
  When a SUPERADMIN calls DELETE /admin/platform/impersonation/sessions/{that session_id} again
  Then the response is 409 ERR_IMPERSONATION_SESSION_ALREADY_ENDED
  And the row's revoked_at / revoked_reason are unchanged from the first End

Scenario: End rejects a naturally expired, never-explicitly-ended session_id   # R9 (TTL variant)
  Given a session whose expires_at is in the past and revoked_at is still NULL
  When a SUPERADMIN calls DELETE /admin/platform/impersonation/sessions/{that session_id}
  Then the response is 409 ERR_IMPERSONATION_SESSION_ALREADY_ENDED
  And revoked_at remains NULL — End does not itself perform the lazy-expire write; that is scoped to Mint's own precondition path (M7)

Scenario: An impersonation identity can never reach /admin/platform/*   # Containment (explicit)
  Given an active impersonation session's token (role = the target's own, e.g. "admin", never "superadmin")
  When that token is used to call GET /admin/platform/tenants (or any other /admin/platform/* route, including this task's own Mint/End)
  Then the response is 403 ERR_AUTH_FORBIDDEN from the completely UNCHANGED require_superadmin dependency
  And no platform-scoped data is returned and no session/audit state changes

Scenario: An impersonation identity can never mint a nested second impersonation session   # Containment (nested, explicit)
  Given an active impersonation session's token (role = the target's own)
  When that token is used to call POST /admin/platform/tenants/{any}/users/{any}/impersonate
  Then the response is 403 ERR_AUTH_FORBIDDEN (identical mechanism to the scenario above — require_superadmin rejects before any impersonation-specific logic runs)
  And no second session-store row is created, and the original active session is completely unaffected

Scenario: Session start and end both attribute the audit row to the REAL superadmin, never the target   # Actor-attribution invariant
  Given a SUPERADMIN (id S, email s@platform.test) starts, then ends, a session against target user U (id different from S, email u@x.test) of tenant T
  When both the start and the end audit rows are inspected
  Then both rows have actor_user_id = S and actor_email = "s@platform.test" — NEVER U or "u@x.test" — with target_tenant_id = T on both

Scenario: Ending a session durably invalidates its store state regardless of the JWT's own unexpired exp claim   # Replay-after-End design boundary
  Given an active session is minted with a hard TTL of 900s, and is explicitly ended after only 10s (its JWT's own exp claim is still ~890s in the future)
  When the session-store row is queried immediately after End
  Then revoked_at is NOT NULL — the row is durably, immediately invalid regardless of how much of the JWT's own exp window remains
  And any consumer that honors revoked_at (impersonation-live-session-guard's per-request check, out of this task's own build scope) has everything it needs, the instant End returns, to reject a replay of that JWT — this task guarantees the store's correctness and immediacy; the live per-request consultation itself is verified by that dependent task, not here
```

</scenarios>

Note: M1-M4, M9, and M10 are structural/non-runtime claims (mirroring how sibling tasks in this
repo record such Musts) verified by code/diff review rather than a behavioral Gherkin scenario:
(1) `Identity` gains exactly one new field with a `None` default, confirmed by diffing the
dataclass; (2)/(3) `JwtTokenService.issue`/`.decode` and the `TokenService` Protocol each gain
exactly the two/one new optional parameter(s) described, confirmed by diffing signatures — the
"ordinary token unaffected" half of this claim IS exercised by a runtime scenario above; (4) the
session-store schema matches M4's field list exactly, confirmed by reading the migration; (9)
`git diff --stat` shows no entries for `authz.py`, `platform_audit.py`, `audit_writer.py`,
`audit_event.py`, or any audit migration beyond this task's own new files; (10) confirmed by code
review that no new dependency is wired into `_resolve_identity` or any self-service router, and
that `target_role` is written once at Mint and never updated except by the lazy-expire/explicit-
End revocation path.

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
═══════════════════════════════════════════════════════════════════════════════
PART A — dual-identity shape (Identity / JwtTokenService / TokenService Protocol)
═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class ImpersonationContext:
    """Present on an Identity iff it is an impersonation session — the REAL superadmin's
    own identity + the session-store row's id. NEVER present together with role=SUPERADMIN
    (JwtTokenService.decode() defensively rejects that combination — see Part B)."""
    session_id: uuid.UUID
    actor_user_id: uuid.UUID
    actor_tenant_id: uuid.UUID
    actor_email: str

@dataclass(frozen=True, slots=True)
class Identity:                              # tenants/domain/entities.py:31-38 — ADDITIVE ONLY
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    role: Role
    impersonation: ImpersonationContext | None = None   # NEW field, default None

═══════════════════════════════════════════════════════════════════════════════
PART B — JWT issuance/decoding (jwt_service.py:20-56, ports.py:43-48 — ADDITIVE ONLY)
═══════════════════════════════════════════════════════════════════════════════

class TokenService(Protocol):
    def issue(
        self, *, user_id: uuid.UUID, tenant_id: uuid.UUID, role: Role, email: str,
        impersonation: ImpersonationContext | None = None,   # NEW, optional
        ttl_seconds: int | None = None,                      # NEW, optional — None = self._ttl
    ) -> tuple[str, int]: ...
    def decode(self, token: str) -> Identity: ...            # UNCHANGED signature

class JwtTokenService:
    def issue(
        self, *, user_id, tenant_id, role, email,
        impersonation: ImpersonationContext | None = None,
        ttl_seconds: int | None = None,
    ) -> tuple[str, int]:
        now = int(time.time())
        ttl = ttl_seconds if ttl_seconds is not None else self._ttl
        claims = {
            "sub": str(user_id), "tenant_id": str(tenant_id), "role": str(role),
            "email": email, "iat": now, "exp": now + ttl, "iss": self._issuer,
        }
        if impersonation is not None:            # byte-identical claims dict when None
            claims["impersonation"] = {
                "session_id": str(impersonation.session_id),
                "actor_user_id": str(impersonation.actor_user_id),
                "actor_tenant_id": str(impersonation.actor_tenant_id),
                "actor_email": impersonation.actor_email,
            }
        return jwt.encode(claims, self._secret, algorithm="HS256"), ttl

    def decode(self, token: str) -> Identity:
        claims = jwt.decode(..., options={"require": [...]})    # UNCHANGED required-claims set
        role = Role(claims["role"])
        imp_claim = claims.get("impersonation")                 # optional — never required
        impersonation = (
            ImpersonationContext(
                session_id=uuid.UUID(imp_claim["session_id"]),
                actor_user_id=uuid.UUID(imp_claim["actor_user_id"]),
                actor_tenant_id=uuid.UUID(imp_claim["actor_tenant_id"]),
                actor_email=imp_claim["actor_email"],
            ) if imp_claim is not None else None
        )
        if role == Role.SUPERADMIN and impersonation is not None:
            raise InvalidTokenError   # defense-in-depth — never produced by this task's own mint path
        return Identity(user_id=..., tenant_id=..., email=..., role=role, impersonation=impersonation)

═══════════════════════════════════════════════════════════════════════════════
PART C — routes
═══════════════════════════════════════════════════════════════════════════════

POST /admin/platform/tenants/{tenant_id}/users/{user_id}/impersonate   body: none
  201 -> { token: str, expires_in: int, session_id: uuid,
           target: { user_id: uuid, tenant_id: uuid, email: str, role: str } }
  401 -> { code: "ERR_AUTH_TOKEN_MISSING" | "ERR_AUTH_TOKEN_INVALID" }
  403 -> { code: "ERR_AUTH_FORBIDDEN" | "ERR_IMPERSONATION_TARGET_INVALID" }
  404 -> { code: "ERR_TENANT_NOT_FOUND" | "ERR_USER_NOT_FOUND" }
  409 -> { code: "ERR_IMPERSONATION_SESSION_ALREADY_ACTIVE" }

DELETE /admin/platform/impersonation/sessions/{session_id}   body: none
  204 -> (empty body)
  401 -> { code: "ERR_AUTH_TOKEN_MISSING" | "ERR_AUTH_TOKEN_INVALID" }
  403 -> { code: "ERR_AUTH_FORBIDDEN" }
  404 -> { code: "ERR_IMPERSONATION_SESSION_NOT_FOUND" }
  409 -> { code: "ERR_IMPERSONATION_SESSION_ALREADY_ENDED" }

Gate order, both routes: require_superadmin (Depends) FIRST — UNCHANGED — Mint additionally
runs authorize_tenant_scope(identity, tenant_id) THEN get_tenant_by_id (404) THEN M6's
target-eligibility checks THEN M7's active-session precondition, all BEFORE any write; End
has no authorize_tenant_scope call (no single target tenant_id — mirrors authz.py's own
"list every tenant" precedent) and resolves/validates the session row before any write.

New ErrorSpec entries (core/error_catalog.py — mirrors existing ErrorSpec(status, code, title) shape):
  IMPERSONATION_TARGET_INVALID       = ErrorSpec(403, "ERR_IMPERSONATION_TARGET_INVALID",
                                                  "Target is not eligible for impersonation")
  IMPERSONATION_SESSION_ALREADY_ACTIVE = ErrorSpec(409, "ERR_IMPERSONATION_SESSION_ALREADY_ACTIVE",
                                                  "An impersonation session is already active for this superadmin")
  IMPERSONATION_SESSION_NOT_FOUND    = ErrorSpec(404, "ERR_IMPERSONATION_SESSION_NOT_FOUND",
                                                  "Impersonation session not found")
  IMPERSONATION_SESSION_ALREADY_ENDED = ErrorSpec(409, "ERR_IMPERSONATION_SESSION_ALREADY_ENDED",
                                                  "Impersonation session already ended or expired")

═══════════════════════════════════════════════════════════════════════════════
PART D — session-store schema (NEW table impersonation_sessions)
═══════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True, slots=True)
class ImpersonationSession:
    id: uuid.UUID
    actor_user_id: uuid.UUID
    actor_tenant_id: uuid.UUID
    actor_email: str
    target_user_id: uuid.UUID
    target_tenant_id: uuid.UUID
    target_role: Role
    target_email: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None
    revoked_reason: str | None = None   # "explicit_end" | "expired_lazy_cleanup"

class ImpersonationSessionRow(Base):   # mirrors AgentTokenRow's style (agent_oauth/infrastructure/orm.py)
    __tablename__ = "impersonation_sessions"
    __table_args__ = (
        CheckConstraint(
            "revoked_reason IS NULL OR revoked_reason IN ('explicit_end', 'expired_lazy_cleanup')",
            name="impersonation_sessions_revoked_reason_check",
        ),
        Index(                                             # M7's race-safety backstop
            "uq_impersonation_sessions_actor_active", "actor_user_id",
            unique=True, postgresql_where=text("revoked_at IS NULL"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid7)
    actor_user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    actor_tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    actor_email: Mapped[str] = mapped_column(nullable=False)
    target_user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    target_tenant_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    target_role: Mapped[str] = mapped_column(nullable=False)
    target_email: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)
    revoked_reason: Mapped[str | None] = mapped_column(nullable=True, default=None)

New Settings field (core/config.py, mirrors playground_token_ttl_seconds's own style exactly):
  impersonation_session_ttl_seconds: int = 900   # GATEWAY_IMPERSONATION_SESSION_TTL_SECONDS
  @field_validator("impersonation_session_ttl_seconds")
  def _validate_impersonation_ttl(cls, v: int) -> int:
      if v <= 0: raise ValueError("INVALID_IMPERSONATION_SESSION_TTL: ... must be > 0")
      return v

Schema/access pattern: impersonation_sessions is written by Mint (INSERT, after a lazy-expire
  UPDATE scoped to actor_user_id) and by End (UPDATE revoked_at/revoked_reason by id) only.
  Reads: users/tenants tables via the EXISTING get_tenant_by_id / UserRoleRepository.get_by_id_and_tenant
  (both reused verbatim, zero change). No column is added to any EXISTING table; no change to
  api_keys, agent_tokens, audit_events, or any of their migrations.

IO note — design for failure: the lazy-expire-then-check-then-insert sequence in Mint (M7) is
  NOT assumed atomic across the read and the write on its own — the partial unique index
  (`actor_user_id` WHERE `revoked_at IS NULL`) is the actual race-safety backstop; a resulting
  IntegrityError on insert is caught and translated to the same 409 ERR_IMPERSONATION_SESSION_
  ALREADY_ACTIVE the ordinary precondition check returns, never surfaced as a raw 500. End is
  NOT a plain read-then-write-in-a-transaction — under READ COMMITTED (Postgres default) that
  shape lets two concurrent DELETEs on the same session_id both read revoked_at IS NULL before
  either commits, producing a double-204. End MUST use the same "push the race into a single
  statement" discipline as Mint's own insert: one conditional
  `UPDATE impersonation_sessions SET revoked_at = now(), revoked_reason = 'explicit_end'
  WHERE id = :session_id AND revoked_at IS NULL AND expires_at > now() RETURNING id`.
  A returned row -> 204. Zero rows -> a follow-up plain SELECT by id (safe to run only on this
  already-lost-the-race path) distinguishes 404 ERR_IMPERSONATION_SESSION_NOT_FOUND (no such row)
  from 409 ERR_IMPERSONATION_SESSION_ALREADY_ENDED (row exists but revoked_at was already set, or
  expires_at had already elapsed) — mirrored by a dedicated concurrency test (two simulated
  concurrent Ends on one session_id -> exactly one 204 and one 409, never two 204s).
  No new retry/timeout/circuit-breaker is added for emit_platform_audit's own call (M9) — its
  already-FROZEN fail-open contract is reused verbatim, unmodified, for the same reasons already
  adjudicated by superadmin-audit-foundation's own §3 (an audit-DB blip must never block a
  security-relevant primary action).
```

Glossary deltas (proposed here; pending this milestone's fold, mirrors how platform-tenant-directory's
own §3 claimed its terms ahead of fold):
- `impersonation session`: a time-boxed, revocable, superadmin-initiated session-store record + JWT
  pair granting a superadmin the ability to act through the ENTIRE existing self-service `/admin/*`
  surface exactly as one specific, eligible target user of a non-platform tenant would, while the
  JWT's additive `impersonation` claim and the session-store row's `actor_*` fields always keep the
  REAL superadmin recoverable; ends the instant its hard TTL elapses or an explicit End call
  durably revokes it.
- `impersonation identity`: an `Identity` whose 4 primary fields (`user_id`/`tenant_id`/`email`/
  `role`) are a target user's own real values and whose additive `impersonation` field is
  non-`None` — mutually exclusive by construction with both an ordinary identity (`impersonation`
  always `None`) and a superadmin's own identity (`role == Role.SUPERADMIN`, `impersonation` always
  `None`; `JwtTokenService.decode()` rejects any token claiming both).

Status: FROZEN @ v1 — approved by Tin Dang 2026-07-04 ("freeze."), gated on and cleared by the
  mandatory HARD-STOP security review MILESTONE.md requires (independent adversarial pass, opus,
  2026-07-04). Verdict: CLEAR-WITH-NOTES — no HARD-STOP finding; central privilege-escalation claim
  independently re-derived (traced the single `Identity(` construction call site in
  jwt_service.py:decode(), confirmed the SUPERADMIN+impersonation defense-in-depth check runs
  before it); End's conditional-UPDATE concurrency fix confirmed race-safe and shown to mirror an
  already-proven pattern (`RevokeKeyUseCase`'s own `UPDATE...RETURNING`, keys/infrastructure/
  repository.py:119-122); audit misattribution confirmed unreachable within this task's own scope.
  Three non-blocking notes carried forward (none change this contract's shape):
  - N1 [deploy-sequencing, needs Tin's explicit call before PRODUCTION DEPLOY, not before this
    freeze]: End is DB-only until `impersonation-live-session-guard` ships — an already-issued
    impersonation JWT keeps working against self-service routes until its own ≤900s exp elapses
    even after an explicit End, in the narrow window between this task deploying and that sibling
    guard task deploying. Blast radius is bounded (TTL-capped, requires an already-valid superadmin
    bearer token, no ordinary user reaches Mint/End without the UI which itself depends on the
    guard task). Tin should decide at deploy time: hold this router's production deploy until the
    guard ships alongside it, or knowingly accept the bounded, audited gap.
  - N2 [non-blocking hardening candidate]: no rate limit on sequential Mint→End→Mint cycles by one
    superadmin credential (concurrent sessions ARE capped at one by M7; sequential rate is not).
    Codebase already has the precedent knob shape (`playground_token_mint_rate_per_minute`) — good
    candidate for a cheap follow-up task, not required now.
  - N3 [Build-time robustness nit]: `decode()`'s new nested-claim parsing should add `TypeError` to
    its caught-exceptions tuple (alongside the already-caught `InvalidTokenError`/`ValueError`/
    `KeyError`) so a malformed non-dict `impersonation` claim 401s cleanly instead of 500ing. Not
    attacker-reachable (requires the HMAC secret to produce), but cheap to get right at Build.
Least-sure flag surfaced at freeze: the §1 assumptions surfaced at this pre-freeze point (superseded
by the security review above; retained for the Build phase's own reference):
⚠ [spec] Valid-target-role set = all 6 non-superadmin roles — a defensible, reasoned
product-risk-appetite call, not a pure security-mechanism fact; see §1 Assumptions for the full
reasoning. If Tin narrows it, the change is bounded to M6/R4, no shape ripple.
⚠ [spec/contract] End requires the superadmin's ORIGINAL (non-impersonation) JWT, never the
active impersonation token — sound containment reasoning, but has a real, not-yet-confirmed
`impersonation-ui` client-state cost (retaining two tokens). If reversed, End needs a new
impersonation-identity-admitting dependency, reopening the containment-exception question this
draft closes by avoiding it.

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
  `apps/gateway/src/gateway/tenants/domain/entities.py` (add ImpersonationContext, extend Identity
    additively with the new `impersonation` field — SHARED file, other parallel builds also touch
    this; expect a merge reconciliation, not a conflict-free apply)
  `apps/gateway/src/gateway/tenants/domain/ports.py` (extend TokenService Protocol, 2 new optional kwargs)
  `apps/gateway/src/gateway/tenants/infrastructure/jwt_service.py` (extend JwtTokenService.issue/.decode)
  `apps/gateway/src/gateway/tenants/infrastructure/orm.py` (add ImpersonationSessionRow — SHARED file,
    same merge caveat)
  `apps/gateway/src/gateway/tenants/api/platform_impersonation_router.py` (NEW — mint + end routes)
  `apps/gateway/src/gateway/core/config.py` (add impersonation_session_ttl_seconds + validator)
  `apps/gateway/src/gateway/core/error_catalog.py` (add 4 new ErrorSpec entries — SHARED file, same
    merge caveat)
  `apps/gateway/src/gateway/main.py` (register platform_impersonation_router — SHARED file, same
    merge caveat)
  `apps/gateway/migrations/versions/` (one new migration, revises current head — SHARED directory;
    down_revision chain across the 3 parallel builds is reconciled by the orchestrator after all 3
    land, not by this build)
  `apps/gateway/tests/impersonation_session_lifecycle/` (this task's own test directory)
Strategy (ordered batches): 1. domain (ImpersonationContext + additive Identity field) 2. JWT layer
  (issue/decode + Protocol, confirm ordinary-token byte-identical scenario passes) 3. session-store
  (ImpersonationSessionRow + migration + partial unique index) 4. Mint endpoint (full gate order:
  require_superadmin -> authorize_tenant_scope -> get_tenant_by_id -> target-eligibility ->
  active-session precondition -> insert -> emit_platform_audit) 5. End endpoint (the reviewed
  conditional-UPDATE concurrency-safe pattern — do NOT implement a plain read-then-write) 6. tests
  per scenario, including the concurrent-double-End test the security review named.
Known-problem fixes: End's revoke must be the single conditional
  `UPDATE ... WHERE id=:id AND revoked_at IS NULL AND expires_at > now() RETURNING id` pattern (never
  a separate SELECT-then-UPDATE) — confirmed race-safe by this task's own mandatory security review;
  a plain read-then-write reintroduces the double-204 race that review caught and this contract fixed.
  `decode()`'s new nested-claim parsing must add `TypeError` to its caught-exceptions tuple (alongside
  the existing InvalidTokenError/ValueError/KeyError) — security review N3, a Build-time robustness
  nit, not attacker-reachable but cheap to get right now.

Persona (optional): <name the persona file under `.add/personas/` this build embodies as a domain stance atop SOUL.md — advisory, never lowers a gate; absent = generic>
Known-problem fixes: <trap → planned fix — the failure modes this build must dodge; guidance, not enforced>
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass
- [x] coverage did not decrease
- [x] no test or contract was altered during build
- [x] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [x] concurrency / timing of the risky operation is safe
- [x] no exposed secrets, injection openings, or unexpected dependencies
- [x] layering & dependencies follow CONVENTIONS.md
- [x] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] a superadmin mints a 201 for an eligible target (non-platform tenant, non-superadmin role) carrying token/expires_in/session_id/target — confirmed by `test_mint_impersonation_session_for_eligible_target`, read in full
- [x] the minted token's primary claims (sub/tenant_id/role/email) are the TARGET's own real values, byte-identical to an ordinary token for that user, distinguishable only by the additive `impersonation` claim — confirmed by `test_minted_jwt_primary_claims_are_targets_never_superadmins` + `test_ordinary_token_issued_and_decoded_byte_identically`
- [x] an impersonation identity is structurally unable to reach any other `/admin/platform/*` route or mint a nested session — confirmed by `test_impersonation_identity_cannot_reach_platform_routes` (403 `ERR_AUTH_FORBIDDEN`) + `test_impersonation_identity_cannot_mint_nested_session`, both read in full
- [x] at most one active session per superadmin, enforced even under a concurrent-Mint race (DB partial unique index is the actual backstop, not just the app-level precondition read) — confirmed by `test_mint_rejects_second_session_while_one_active` + `test_concurrent_mint_calls_by_same_superadmin_exactly_one_201_one_409`, read in full: real `asyncio.gather` dual-dispatch, asserts exactly one 201 + one 409 (`ERR_IMPERSONATION_SESSION_ALREADY_ACTIVE`), re-verifies final active-count == 1
- [x] End is one conditional UPDATE, safe under a concurrent-double-End race (never two 204s) — confirmed by `test_concurrent_end_calls_exactly_one_204_one_409`, read in full: asserts exactly one 204 + one 409, re-verifies DB row state after
- [x] every Mint/End audit row attributes the REAL superadmin, never the target, even across two different superadmins (mint by A, end by B) — confirmed by `test_audit_rows_attribute_real_superadmin_never_target` + `test_end_by_different_superadmin`
- [x] a forged `role=superadmin` + non-None `impersonation` claim, or a malformed non-dict `impersonation` claim, is rejected as a clean 401 (never a 500) — confirmed by `test_jwt_decode_rejects_superadmin_role_with_impersonation_claim` + `test_jwt_decode_rejects_malformed_impersonation_claim_type_error`, both read in full (direct JWT-layer forge, unreachable via any HTTP scenario per their own docstrings)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced: `platform_impersonation_router` imported + `app.include_router`'d at `main.py:126,987`; `ImpersonationContext`/`Identity.impersonation` consumed by `jwt_service.py` issue()/decode(); `ImpersonationSessionRow` used by the router's Mint/End bodies; all 4 new `error_catalog.py` ErrorSpec entries (`IMPERSONATION_TARGET_INVALID`/`_SESSION_ALREADY_ACTIVE`/`_SESSION_NOT_FOUND`/`_SESSION_ALREADY_ENDED`) referenced at their respective raise sites; `impersonation_session_ttl_seconds` (`config.py:919`, validator `:921`) consumed by both Mint's `expires_at` calc and `issue()`'s `ttl_seconds` kwarg — all confirmed by direct grep + read, not assumed
- [x] DEAD-CODE (code) — no new unused/orphaned symbol; confirmed by a clean ruff pass (no unused-import/unused-variable diagnostics) and direct read of every new file
- [ ] SEMANTIC (prose / non-code) — N/A this task is pure code; the router's own module docstring (lines 1-43) was read in full and cross-checked against the actual implementation — no drift between the documented gate order/concurrency reasoning and the real code

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> §0's Ground SHA anchors the symbols cited at ground time to that commit — code moves during
> build. Before the gate, re-resolve every symbol §3 CONTRACT cites against the CURRENT tree
> (not the Ground SHA) so a stale anchor is caught here, not by a future reader chasing a moved
> line.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by direct grep against the real files: `entities.py:34` (`ImpersonationContext`), `entities.py:57` (`Identity.impersonation` field), `ports.py:53-54` (Protocol's new kwargs), `jwt_service.py:20-108` (issue/decode, read in full), `orm.py` (`ImpersonationSessionRow`), `config.py:919,921` (TTL field + validator), `error_catalog.py:660-682` (4 ErrorSpecs), `main.py:126,987` (router wiring), migration `1d563bf9b143` (read in full, `down_revision=1e66a2cb51a6` chains cleanly off plan-catalog, single alembic head)
- [x] any anchor that moved/renamed since Ground SHA is named here, not left silent — none found; all anchors resolved at their originally-cited locations (module line numbers shifted slightly from parallel sibling-build merges but every symbol itself is present, unrenamed, and correctly wired)

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: TWO independent, convergent passes — agent-id `a0b94814490d2569d` (add-verify, persona
appsec-engineer) delivered its full verdict after an unusually long run (~23 min, 38 tool_uses,
256,967 tokens — an earlier transient "API Error: Overloaded" and a slow background full-suite run
made it look stalled at two intermediate checkpoints, but it was still working, not stuck); AND self
(Claude, orchestrator), who independently verified in parallel before the agent's final report landed.
Both converged on PASS with no HARD-STOP.
Adversarially checked (union of both passes):
  1. Read `platform_impersonation_router.py` (301 lines, full), `jwt_service.py` `decode()` (lines
     60-108, full), and migration `1d563bf9b143` (full) directly — confirmed the defense-in-depth gate
     ordering matches the frozen contract exactly, no shortcut or stub anywhere. [self]
  2. Confirmed both concurrency tests use a genuine `asyncio.gather` dual-dispatch against the real
     ASGI app + real DB, assert exactly-one-success + exactly-one-conflict, and re-verify final DB
     state afterward. [self] THEN the agent went further: built 4 TEMPORARY forced-race probes (not
     part of the frozen suite, deleted after use) using `asyncio.Event` barriers to force worst-case
     interleaving deterministically for both Mint and End — proving BOTH that the naive
     read-then-write alternative really does double-succeed (the bug the contract names is real, not
     theoretical) AND that the actual implemented fix never double-succeeds across 21+ forced
     iterations, against a real Postgres with an independent AsyncSession per call (no
     shared-transaction illusion of concurrency). [agent — strictly stronger evidence]
  3. Confirmed containment tests fire a real minted token through the full HTTP stack against a live
     route. [self] The agent additionally ran 6 of its own temporary empirical HTTP-level security
     probes (no-auth, garbage-token, wrong-secret-signed token, valid-non-superadmin token on both
     routes, two forged-claim tokens) through the full stack. [agent]
  4. Confirmed the 2 direct JWT-layer unit tests forge genuinely adversarial claims and assert
     `InvalidTokenError`, directly exercising N3's `TypeError` catch and the SUPERADMIN+impersonation
     defense-in-depth line (`jwt_service.py:83-89`). [both]
  5. Ran the task's own 24-test suite directly: 24/24 passed [self]; agent ran it twice plus 5x on
     just the 2 race tests for stability [agent]. Full gateway regression suite: both independently
     got 2411 passed, 1 failed, 7 skipped — the 1 failure root-caused by both to the same
     pre-existing, unrelated `batch_jobs`/`batch_job_items` orphan-table guardrails issue (zero
     references anywhere in `src/gateway/`, stale shared-DB contamination, not caused by this task).
  6. Ran ruff + pyright directly — clean. [self]
  7. The coverage.py anomaly (48% router / 39.27% total when this suite runs in isolation) — [self]
     reasoned it matched the same greenlet-tracking gap logged twice before this session; [agent]
     went further and DIRECTLY FALSIFIED it empirically: temporarily instrumented a "missing" line
     (120, squarely inside the reported gap) with a print statement, confirmed it fires during a
     passing test, then reverted the instrumentation and reconfirmed the file clean (0 diff, 24/24
     still pass). Confirms this is a coverage-tooling artifact, not a real test gap — see Competency
     deltas.
  8. [agent, new] Found one genuine non-blocking architecture residue via grep/serena: the plain
     frozen domain dataclass `ImpersonationSession` (§3 Part D, `entities.py:112-133`) is dead
     code — never instantiated or imported anywhere in `src/` or `tests/`; the router works directly
     against `ImpersonationSessionRow` (the ORM class) instead. Contract-mandated (Build correctly
     implemented what §3 asked for) — a contract-shape gap, not a Build defect. Matches a
     previously-folded CONVENTIONS.md lesson from an earlier task (model-mgmt's
     ModelDisabledError/ModelNotFoundError were also born dead) — recurrence noted in Competency
     deltas below.
No overfit to fixtures, vacuous asserts, or stubbed-away logic found anywhere — every defense-in-depth
layer traced to a real, non-bypassable code path exercised by a real test.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Under autonomy: auto run the 3-lens checklist and record the verdict here. Lenses run in
> order; a Security HARD-STOP ends the checklist (leave remaining lenses blank). Binding for
> sensitivity: mechanical (advisor-gate-relax reads it); advisory for all other sensitivities.
> The engine MEASURES this block is filled (audit: advisor_verdict_unrecorded); it never blocks.
Advisor: agent-id `a0b94814490d2569d` (add-verify, persona appsec-engineer) — primary; self (Claude,
orchestrator) — independent corroborating pass in parallel. Both converged.
1. Security: CLEAR. `require_superadmin` gates both routes UNCHANGED (byte-for-byte, confirmed by
   direct read) — an impersonation identity's `role` is always the target's real, non-superadmin role
   (M1), making it structurally unable to reach any superadmin-gated route; proven by code trace
   (`jwt_service.py:83-89`'s unconditional defense-in-depth reject), by 2 direct HTTP-level containment
   tests + 2 direct JWT-forge unit tests, AND by the agent's own 6 empirical HTTP probes (no-auth →
   401, garbage token → 401, wrong-secret-signed token → 401, valid non-superadmin token → 403, on
   both routes; forged-claim tokens → clean 401 through the full stack, never a 500). Mint's full gate
   order (tenant-scope → tenant-404 → platform-tenant-kind-403 → target-404 →
   target-role-superadmin-403 defense-in-depth → active-session-409 → insert) matches the frozen
   contract exactly, all before any write — each rejection point confirmed by its own dedicated
   scenario. End's "any superadmin may end any session" (no ownership check) confirmed intentional
   per §1's own stated reasoning (stronger property for incident response), not an oversight. No
   secret exposure, no injection surface (parameterized queries throughout), no unlisted dependency.
2. Concurrency: CLEAR. Both risky operations use a real DB-level backstop, not just an
   application-level check-then-act: Mint's partial unique index
   (`uq_impersonation_sessions_actor_active`) turns a lost race into an `IntegrityError`, caught and
   translated to the same 409; End's single conditional `UPDATE ... WHERE id=:id AND revoked_at IS
   NULL AND expires_at > now() RETURNING ...` (mirroring `RevokeKeyUseCase`'s own precedent,
   `keys/infrastructure/repository.py:111-124`) makes a double-204 structurally impossible under READ
   COMMITTED. Confirmed by the frozen suite's own `asyncio.gather` races AND by the agent's own 4
   forced-barrier probes (`asyncio.Event`-synchronized worst-case interleaving, 21+ iterations) proving
   both that the naive alternative genuinely double-succeeds and that the actual fix never does,
   against a real Postgres with independent AsyncSessions (no shared-transaction illusion).
3. Architecture: RESIDUE (minor, non-blocking). Additive-only migration (`down_revision=1e66a2cb51a6`
   chains cleanly off plan-catalog — sequential landing confirmed, single alembic head). No existing
   table or file altered beyond declared-scope additive fields. Router mirrors sibling
   `platform_*_router.py` conventions (gate ordering, audit-emission shape, inline-SQL-in-router
   precedent checked against `platform_plans_router.py`/`platform_tenant_config_router.py`). No new
   dependency introduced. 🟡 Finding: `ImpersonationSession` (§3 Part D's plain frozen domain
   dataclass, `entities.py:112-133`) is dead code — never instantiated or imported anywhere in `src/`
   or `tests/`; the router works directly against `ImpersonationSessionRow` (the ORM class) instead.
   Contract-mandated, not a Build defect — recurrence of a previously-folded CONVENTIONS.md lesson
   (model-mgmt's ModelDisabledError/ModelNotFoundError were also born dead). Does not block PASS;
   carried to Spec delta below.
Verdict: PASS
Residue: minor architecture-only (unused `ImpersonationSession` dataclass — non-blocking, see Spec delta)
Binding: advisory — security (a human floor per ADD rules; this AI-recorded PASS is the auto-mode
evidence trail, not a substitute for Tin's own available spot-audit — flagged in the final report)

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: add-verify agent `a0b94814490d2569d` (appsec-engineer persona, full adversarial pass w/
forced-race probes) + Claude (orchestrator, independent corroborating pass), autonomy: auto ·
date: 2026-07-04

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): mint 409-rate (`ERR_IMPERSONATION_SESSION_ALREADY_ACTIVE`,
a contention/retry signal); active-session count vs. distinct-superadmin count (should track 1:1 at
steady state — a sustained gap flags lazy-expire or End not firing); audit-row pairing (every
`platform.impersonation.start` should eventually see a matching `.end` or a natural TTL expiry — an
unpaired start aging past its TTL flags a stuck session or a missed End call); mint/end p99 latency.

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang 2026-07-04 ("freeze."), gated on and cleared by the)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by add-verify agent `a0b94814490d2569d` (appsec-engineer persona, full adversarial pass w/)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] Delete the unused `ImpersonationSession` domain dataclass (`entities.py:112-133`) or
  add a one-line comment naming its intended future consumer — evidence: confirmed zero references
  anywhere in `src/` or `tests/` (add-verify agent finding, cross-checked).
- [SPEC · open] N1 carried forward, now at its last checkpoint before ship: Tin must decide at deploy
  time whether to hold this router's production deploy until `impersonation-live-session-guard` ships
  alongside it, or knowingly accept the bounded (TTL-capped, superadmin-bearer-token-gated) window
  where an ended session's JWT still works against self-service routes until its own ≤900s exp
  elapses — evidence: contract §3 Part D IO note, reconfirmed still open at verify.
- [SPEC · open] N2 carried forward, non-blocking: no rate limit on sequential Mint→End→Mint cycles by
  one superadmin credential (concurrent sessions ARE capped at one by M7; sequential rate is not) —
  precedent knob shape already exists (`playground_token_mint_rate_per_minute`); good cheap
  follow-up, not required now.
- [SPEC · open] The Least-sure-flag question from freeze (line 722-726: End requires the superadmin's
  ORIGINAL non-impersonation JWT, never the active impersonation token) has a real, not-yet-confirmed
  `impersonation-ui` client-state cost (retaining two tokens) — carry into that sibling task's own
  Specify step rather than resolve here.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [ADD · open] coverage.py under-reports statement coverage for async router code exercised through
  SQLAlchemy's greenlet bridge — 3rd confirmed occurrence this session (member-invite-issuance,
  plan-catalog, now this task: 48% router / 39.27% total reported when run in isolation, despite
  24/24 passing tests with real assertions). This time DIRECTLY FALSIFIED (not just reasoned about):
  the add-verify agent instrumented a "missing" line with a temporary print statement, confirmed it
  fires during a passing test, then reverted. Worth a repo-wide `.coveragerc`/pytest-cov config fix
  now that it has empirical proof behind it, not just pattern-matching across 3 builds.
- [ADD · open] The "contract-specified-but-unused domain type" failure mode recurred (this task's
  `ImpersonationSession` plain dataclass, §3 Part D) despite already being folded into
  CONVENTIONS.md from an earlier task (model-mgmt's ModelDisabledError/ModelNotFoundError). Worth a
  sharper trigger at Contract-freeze time: ask "which code path actually constructs this type?" for
  every domain entity named in §3, not just error classes — the existing lesson's phrasing may be too
  narrowly scoped to catch this class of recurrence.
- [ADD · open] `add.py advance` state silently lagged actual build completion across this
  long-running, compacted, parallel-build session: this task's own phase marker was still `tests`
  (not `verify`) despite its code already being fully built, tested, and independently investigated
  by 2 separate verify attempts — root cause: the orchestrator built directly against the tree in a
  fast-moving parallel-build sequence and never called the 2 required `add.py advance` transitions
  (tests→build, build→verify) immediately after the code was done, only discovered via `add.py
  status` at this gate. Lesson: advance state the instant a build completes, not deferred until the
  next check-in — the engine's phase marker is the only authoritative source of truth (TASK.md's own
  header is cosmetic) and desyncs silently otherwise, especially across compaction boundaries.
- [ADD · open] A background-suite-dependent `add-verify` dispatch can look stalled (a transient API
  error, then a long silent gap around a slow full-suite run) while actually still being alive and
  eventually delivering a complete, high-quality, more-rigorous-than-the-orchestrator's-own verdict
  (this instance: ~23 min wall-clock, 38 tool_uses, 4 self-built forced-race probes, 6 self-built HTTP
  security probes, one genuine new finding). This nuances the pattern logged twice before this
  session (plan-catalog's build agent ×2) that such agents "don't truly block" — sometimes they
  DO eventually deliver, just slower than the orchestrator's own patience budget. Lesson: when an
  orchestrator takes over independent verification after presuming a stall, treat a
  later-arriving agent verdict as additional evidence to merge, not discard — as done here — rather
  than assuming the takeover was necessarily the only path to a verdict.

