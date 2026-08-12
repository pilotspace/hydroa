# TASK: Personal-tier self-serve signup without re-opening account enumeration (SECURITY)

slug: scoped-self-serve-signup · created: 2026-07-20 · stage: production
milestone: frontdoor-persona-routing
component: gateway
sensitivity: security
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/tenants/api/router.py:signup` — the S1 gate (lines ~108-114, comment
  block "Invite-only gate (S1)"): `if not request.app.state.settings.public_signup_enabled: raise
  SIGNUP_INVITE_ONLY.exc()`, reached only AFTER the verified-domain-claim lookup
  (`resolve_verified_tenant`, lines ~68-78) falls through. This task inserts exactly ONE new branch
  between that domain-claim fallthrough and the S1 check — for `account_type=='personal'` with the
  NEW scoped flag ON. Every other branch (verified-domain join ~79-106, business/global-flag path
  ~115-131, the S1 fallback ~108-114, the member-verified-recognition issuance hook ~138-151) stays
  byte-unchanged.
- `apps/gateway/src/gateway/core/config.py:194-198` — `public_signup_enabled: bool = False`
  (`GATEWAY_PUBLIC_SIGNUP_ENABLED`), the existing GLOBAL flag. Human decision on record (frontdoor
  context): do NOT flip this globally — add a SEPARATE, narrower flag scoped to personal-tier only.
- `apps/gateway/src/gateway/core/error_catalog.py:135-161` — `AUTH_EMAIL_TAKEN` (409
  ERR_TENANT_EMAIL_TAKEN), `AUTH_PASSWORD_WEAK` (400 ERR_AUTH_PASSWORD_WEAK), `SIGNUP_INVITE_ONLY`
  (403 ERR_SIGNUP_INVITE_ONLY, "checked FIRST, before any body validation or DB IO"),
  `SIGNUP_PLAN_UNPROVISIONED` (500) — all REUSED unchanged; the S1 comment at line 144-146 is the
  exact anti-enumeration property this task must preserve for every scoped-personal request too.
- `apps/gateway/src/gateway/tenants/application/use_cases.py:SignupUseCase.execute` — password
  length checked BEFORE any DB IO (`if len(password) < MIN_PASSWORD_LENGTH: raise WeakPasswordError`);
  personal `account_type` resolves `get_plan_id_by_name("free")`, raising `IndividualPlanMissingError`
  (-> 500 SIGNUP_PLAN_UNPROVISIONED) if the seed is absent. Reused pattern, not reused code (the new
  scoped path needs a DEFERRED-creation shape this use-case doesn't have).
- `apps/gateway/src/gateway/tenants/infrastructure/repository.py:SqlAlchemyIdentityRepository.
  create_tenant_with_owner` — INSERT tenant+owner in one transaction, catches `IntegrityError` (the
  `users.email` UNIQUE constraint) -> `EmailAlreadyRegisteredError`. This is the exact mechanism the
  CONFIRM step reuses verbatim (with a pre-hashed password, no re-hash) — see M10.
- `apps/gateway/src/gateway/tenants/domain/ports.py:IdentityRepository` (Protocol) — additive
  methods only; `PasswordHasher.verify`'s own docstring ("MUST cost the same time either way — no
  user enumeration through timing") and `Argon2PasswordHasher`'s dummy-hash implementation
  (`tenants/infrastructure/argon2_hasher.py`) are the DIRECT precedent for this task's M6 (hash
  unconditionally, before branching on email existence) — same principle, applied to signup instead
  of login.
- `apps/gateway/src/gateway/tenants/infrastructure/invite_repository.py` — invite tokens are stored
  as `token_hash` (SHA-256, via `gateway.keys.infrastructure.sha256_hasher.Sha256SecretHasher`,
  reused cross-context already by `tenants/application/invite_use_cases.py:_hasher`) and looked up by
  `WHERE token_hash == :hash` — NO `hmac.compare_digest`, NO attempt-cap, NO HMAC pepper. This is the
  precedent this task's confirm-token follows (256-bit CSPRNG via `secrets.token_urlsafe(32)`,
  mirrors `domain_capture/application/create_claim_use_case.py`'s `_TOKEN_BYTES = 32` token
  generation) — DISTINCT from `domain_capture/domain/member_verify_code.py`'s HMAC-pepper +
  `hmac.compare_digest` + attempt-cap scheme, which exists ONLY because a 6-digit code is a
  brute-forceable 10^6 space. A 256-bit token needs no pepper and no attempt-cap; reaching for the
  heavier scheme here would defend against a threat this secret doesn't have.
- `apps/gateway/src/gateway/domain_capture/application/create_claim_use_case.py:
  CreateDomainClaimUseCase` — the "create-or-reissue" idiom (one row per key, a repeat call
  overwrites token+expiry) this task's pending-signup issuance mirrors for "one pending row per
  email."
- `apps/gateway/src/gateway/tenants/infrastructure/invite_public_rate_limiter.py:
  InvitePublicRateLimiter` — already fully generic (`check(action, key, limit)`), already
  instantiated once on `app.state.invite_public_limiter` (wired in `main.py:1358`), already used by
  `tenants/api/invite_accept_router.py:preview_invite` with `gateway.core.net.resolve_trusted_client_ip
  (request, settings.trusted_proxy_hops)` as the IP key (NEVER raw `request.client.host` — proxy-
  spoofing precedent). Reused AS-IS for this task with 3 new `action` labels — no new limiter class.
- `apps/gateway/src/gateway/email/domain/ports.py:EmailSender` +
  `email/application/email_dispatch.py:send_email` (fail-open, FROZEN @ v1) +
  `email/domain/entities.py:EmailMessage(to, subject, text_body, html_body)` — the seam every new
  template call reuses; `domain_capture/application/member_verify_use_cases.py`'s
  `IssueMemberVerifyCodeUseCase` is the closest sibling shape (constructor takes `jwt_secret`/
  `code_ttl_seconds`/`origin`; `execute` awaits `send_email` directly for deterministic test capture).
- `apps/gateway/tests/signup_routing_authz/test_signup_routing_authz.py` (FROZEN, S1) — 16 tests;
  #1/#2/#8 are the ones this task's new flag must never perturb: `test_signup_rejected_invite_only`,
  `test_signup_invite_only_checked_before_validation` (flag OFF + an ALREADY-registered email + a
  WEAK password still -> 403 ERR_SIGNUP_INVITE_ONLY, zero rows — the gate short-circuits before the
  use case, before ANY DB IO), `test_bootstrap_flip_on_then_off`. This suite's `ADA` fixture omits
  `account_type` (defaults `"business"`) and the suite's `settings` fixture never sets the NEW flag —
  so by construction every one of these 16 tests stays byte-identical.
- No forgot-password / password-reset route exists anywhere in this codebase (grep-confirmed,
  `apps/gateway/src/gateway` has zero `forgot.password|password.reset|reset_password` hits) — bounds
  what the out-of-band conflict-notice email (M9) can safely say (no reset link to offer).
- `alembic heads` (this branch, at Ground SHA) resolves to a SINGLE head:
  `a4f2d9c17b3e_domain_invite_links.py` — the new migration chains off this file.

Context (working folder): `apps/gateway/src/gateway/tenants/` (domain/application/infrastructure/api
— the new pending-signup entity/errors/repository-methods/use-cases/schemas/router-branch all land
here, alongside the EXISTING `SignupUseCase`/`IdentityRepository`) · `apps/gateway/src/gateway/email/
application/` (2 new pure templates) · `core/config.py` (4 new knobs) · `core/error_catalog.py` (2 new
ErrorSpecs) · `migrations/versions/` (1 additive migration). Out of scope for THIS task (backend/
gateway only, per the milestone DAG's wave-1 listing): the dashboard confirmation screen — see
Issues/Risks below.

Honors (patterns / conventions): hexagonal layering (domain Protocol ports; application use-cases;
infra adapters; api routers/schemas — CONVENTIONS.md, backend-architect discipline); additive-only
migrations (nullable/defaulted or a wholly new table, every existing row/consumer untouched);
create-or-reissue single-row-per-key idiom (`CreateDomainClaimUseCase`); fail-open outbound email
(`send_email`) + fail-open rate limiting (`InvitePublicRateLimiter`); secrets sized to their entropy
class — high-entropy tokens get a bare SHA-256 (`Sha256SecretHasher`, invite precedent), low-entropy
codes get an HMAC pepper + attempt-cap (`member_verify_code.py` precedent, NOT reused here — wrong
entropy class); timing-equalization for any branch keyed on account existence
(`Argon2PasswordHasher.verify`'s dummy-hash rationale, `LoginUseCase.execute`'s own comment "both
failure paths cost the same time"); anti-enumeration byte-identical responses across unknown-vs-
cross-tenant/unknown-vs-taken (`InviteNotFoundError`'s docstring, `SIGNUP_INVITE_ONLY`'s own S1
comment) — this task extends that SAME invariant to a NEW surface (email-taken-vs-available) instead
of inventing a new one.
Seams consulted: email seam (transactional-email, FROZEN @ v1) · the invite token/rate-limiter seam
(member-invite-acceptance, FROZEN) · the S1 gate (signup-and-routing-authz, FROZEN) · domain-capture's
create-or-reissue + member-verify-code precedents (both read for contrast, only the FIRST is reused).
Anchors the contract cites: `Settings.public_signup_personal_enabled` (new), `IdentityRepository`
(+3 methods), `SqlAlchemyIdentityRepository` (+3 impls), a new `PendingPersonalSignup` entity, 2 new
`IdentityError` subclasses, a new `personal_signup_confirm.py` domain module, 2 new use-cases
(`IssuePendingSignupUseCase`, `ConfirmPendingSignupUseCase`), `SignupPendingResponse` +
`ConfirmSignupRequest` schemas, the `signup` router's one new branch + the new `POST /admin/auth/
signup/confirm` route, `Sha256SecretHasher` (reused), `InvitePublicRateLimiter` + `resolve_trusted_
client_ip` (reused), `send_email` (reused), 2 new ErrorSpecs, `create_tenant_with_owner` (reused
unchanged at confirm-time), the `pending_personal_signups` table.
Issues/Risks (→ feed §1):
- **R-sec-1 (the whole point — do not reopen S1's oracle):** naively removing/relaxing the S1 gate
  for personal signups would run `SignupUseCase.execute`, which returns 409 (email taken) or 201
  (success) differently per submitted email — a classic account-enumeration oracle. MITIGATION: never
  create the tenant/user row synchronously; the initial POST always returns the SAME 202 shape
  regardless of email state (M7); conflict is handled ENTIRELY out-of-band (M9).
- **R-sec-2 (timing side-channel):** even with an identical response BODY, the "email new" branch
  does more DB work (an INSERT) than the "email taken" branch (a bare SELECT) — a measurable latency
  gap could still leak the signal. MITIGATION: the dominant cost (argon2 hash, ~50-200ms) runs
  UNCONDITIONALLY before the branch, on every request, mirroring `Argon2PasswordHasher`'s own
  dummy-hash equalization (M6) — masking the comparatively tiny INSERT-vs-SELECT delta.
- **R-sec-3 (a new anonymous email-send surface):** the initial POST is unauthenticated and
  attacker-controlled — an attacker could hammer it with real victim emails to either flood their
  inbox (confirm emails) or (if a conflict-notice is sent, M9) alert/annoy an unrelated real owner.
  MITIGATION: per-IP AND per-email fail-open rate limits (M3), both checked BEFORE any DB IO or email
  dispatch, with an IDENTICAL threshold/response regardless of which branch would fire.
- **R-sec-4 (token squatting):** unlike the domain-claim DNS token (published in DNS, a public
  channel — plaintext-at-rest is fine there), this confirm token, if read from a DB dump, lets the
  reader complete account creation with a password OF THEIR CHOOSING before the real submitter clicks
  the email — a real account-squatting risk for a not-yet-registered email. MITIGATION: store only a
  hash (M8); right-sized to 256-bit entropy (bare SHA-256, no pepper needed — R-sec-4 is closed by
  entropy alone, a DB-dump attacker cannot invert the hash regardless of pepper).
- **R-sec-5 (confirm-time race):** the target email could become registered through an UNRELATED path
  (business domain-join, SSO, another concurrent pending-signup for the literal same address) between
  issuance and confirm. MITIGATION: `create_tenant_with_owner`'s existing `IntegrityError` catch is
  reused unchanged at confirm-time (M12) — safe to surface as a real 409 there because the confirm
  caller is authenticated by token possession, not an anonymous prober (R-sec-6).
- **R-sec-6 (why confirm-time errors are allowed to be more expressive):** distinguishing
  invalid/expired/taken at `/signup/confirm` does NOT reopen an enumeration oracle, because this
  endpoint is reachable in a meaningful way ONLY by someone who already holds a 256-bit token
  delivered solely via a specific inbox — an anonymous prober working the INITIAL endpoint learns
  nothing from this distinction (M11).
- **R-scope-1 (no reset-flow exists):** the out-of-band conflict-notice email (M9) cannot offer a
  self-service recovery link because no forgot-password flow exists in this codebase yet — it can
  only be a generic "an account with this email already exists, log in or ignore this" notice.
- **R-scope-2 (frontend gap):** the milestone DAG (wave1: homepage-integration-proof ·
  homepage-price-anchor · scoped-self-serve-signup · signup-refusal-router; wave2:
  domain-aware-auth-routing · homepage-cta-intent-split; wave3: unified-signin-entry) names no
  dashboard task that renders the confirm-token screen this contract's `/signup/confirm` endpoint
  requires (mirrors the EXISTING `/join/[token]` + `apps/dashboard/app/api/auth/join/` precedent).
  This task's contract is backend-only and unusable end-to-end without that follow-on — flagged for
  the orchestrator, not resolved here.
Related intent: milestone `frontdoor-persona-routing` — "P4 Sam ... Actively failed" +
`homepage-cta-intent-split (deps scoped-self-serve-signup)`; the human decision on record ("self-serve
should work — but via scoped self-serve that preserves the anti-enumeration property... NOT by
flipping GATEWAY_PUBLIC_SIGNUP_ENABLED globally"). GLOSSARY: introduces "pending-personal-signup" (a
mailbox-proof-BEFORE-tenant-creation rung, distinct from "member-verified" which proves a mailbox
AFTER a business tenant already exists).
Ground SHA: 8daf22c (cite symbols, not bare line numbers; any line ref is "as of" this commit)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Scoped self-serve signup for personal-tier accounts — a new, narrow
`public_signup_personal_enabled` flag lets an unauthenticated visitor complete a PERSONAL signup
without an invite, WITHOUT reopening the account-enumeration oracle the existing invite-only S1 gate
was built to close. Business/enterprise signups are completely unaffected (still gated behind the
existing global flag / a verified-domain-claim / the S1 fallback, byte-identical to today).
Framings weighed:
- **Deferred creation: uniform 202 response now, real tenant/user rows created only at a token-gated
  confirm step (CHOSEN)** — the initial POST never branches observably on email-existence (no DB
  write differs in a way visible to the caller); the account-enumeration surface is closed by
  construction, not by careful error-message-matching after the fact. Mirrors the codebase's own
  established "authenticate by secret possession, not by synchronous DB signal" pattern (invite
  accept, member-verify code) applied one step earlier — before ANY account exists at all.
- Eager creation + login-gate (an inert, `email_verified_at IS NULL` row created synchronously,
  rejected LOGIN until verified) — REJECTED: it would require a NEW oracle-shaped decision inside
  `LoginUseCase` (must an unverified account's login attempt look identical to a wrong-password
  attempt? yes — but that duplicates work `LoginUseCase.execute` already solved for `deactivated_at`,
  for no benefit) and leaves abandoned, unverified rows in the PRIMARY `users`/`tenants` tables
  forever if never confirmed, rather than in an isolated, obviously-transient table.
- Reuse the EXISTING low-entropy 6-digit `member_verify_code` machinery for this too — REJECTED: that
  scheme's HMAC-pepper + `hmac.compare_digest` + attempt-cap machinery exists specifically to defend
  a 10^6-space code reachable via an AUTHENTICATED, rate-limited, owner-only endpoint; this is an
  UNAUTHENTICATED, pre-account flow where a 256-bit link-style token (the invite-token precedent) is
  both simpler and a better entropy match — see Ground R-sec-4.
- A dedicated `/signup/resend` endpoint — REJECTED (unnecessary): re-`POST`ing `/admin/auth/signup`
  for the same still-pending email already reissues (create-or-reissue, M8), bounded by the SAME
  per-email rate limit as the original issuance — a second endpoint would be a second attack surface
  for zero new capability.

Must:
<must>
  - M1 New, narrow, additive flag: `public_signup_personal_enabled` (default False) gates ONLY
    `account_type=='personal'` signups that already fell through the existing verified-domain-claim
    check. It never widens, aliases, or is read anywhere near the existing `public_signup_enabled`
    (business/global) check — business signups have zero coupling to this new flag.
  - M2 Byte-identical fallback when OFF (the default): a personal signup with no domain claim and
    `public_signup_personal_enabled=False` hits the EXISTING S1 `SIGNUP_INVITE_ONLY` gate exactly as
    today — checked BEFORE any body validation or DB IO, zero new rows, indistinguishable from a
    business signup hitting the same gate. `tests/signup_routing_authz/test_signup_routing_authz.py`
    passes completely unmodified.
  - M3 Rate-limit FIRST, uniform, fail-open: before any DB IO, check a per-client-IP limit AND a
    per-normalized-email limit (both via the EXISTING `InvitePublicRateLimiter` /
    `app.state.invite_public_limiter`, new `action` labels, `resolve_trusted_client_ip` for the IP
    key) — the SAME two checks, SAME thresholds, SAME 429 response, run regardless of whether the
    submitted email is already registered.
  - M4 Password strength checked early, uniformly: too-short password -> `ERR_AUTH_PASSWORD_WEAK`
    (400, reused unchanged), identical for every submitted email regardless of registration state —
    this signal is a pure function of the password, never of account existence, so it is not an
    enumeration oracle and MAY stay synchronous (matches `SignupUseCase.execute`'s existing ordering).
  - M5 Plan-availability checked uniformly: the seeded `free` plan absent -> `ERR_SIGNUP_PLAN_
    UNPROVISIONED` (500, reused unchanged), identical for every request regardless of the target
    email — a server-misconfiguration signal, never an email-specific one.
  - M6 Password hashed unconditionally, before the email-existence branch (timing mask): the
    submitted password is Argon2-hashed via the EXISTING `PasswordHasher.hash()` EXACTLY ONCE on
    every request that reaches this point — whether the branch below stores that hash (M8) or
    discards it (M9), the dominant cost is paid identically either way. Mirrors `Argon2PasswordHasher.
    verify`'s own dummy-hash timing-equalization rationale, applied to signup.
  - M7 Uniform response — the anti-enumeration invariant: after M3-M6, the HTTP response is
    IDENTICAL in status code (202) and JSON body shape (`{"status": "pending_verification", "email":
    <the submitted, lower-cased email>}`) whether the target email is already registered or brand
    new. No 409, no 201, no field, no header differs between the two branches.
  - M8 New pending signup (email NOT already registered): UPSERT-by-email (create-or-reissue,
    mirrors `CreateDomainClaimUseCase`) a `pending_personal_signups` row: normalized email,
    tenant_name, the argon2 hash from M6, a SHA-256 hash (via the EXISTING `Sha256SecretHasher` — no
    HMAC pepper; see Ground R-sec-4) of a freshly generated `secrets.token_urlsafe(32)` confirm token,
    and `expires_at = now + personal_signup_confirm_ttl_seconds`. Exactly ONE confirmation email
    (`render_signup_confirm_email`, carrying the RAW token) is sent to that address. A repeat
    not-yet-confirmed submission for the SAME email REISSUES (fresh token, hash, expiry — the
    previous token stops working).
  - M9 Conflict handled out-of-band (email ALREADY registered): no `pending_personal_signups` row is
    written and no existing tenant/user row is touched; exactly ONE generic notice email
    (`render_signup_conflict_notice_email` — states an account with this email exists, points to
    `/login`, offers no reset link since none exists in this codebase, names no other detail) is sent
    to that address. The HTTP response is UNCHANGED from M7.
  - M10 Confirm completes creation: `POST /admin/auth/signup/confirm` body `{token}` (public, no
    bearer auth — authenticated ONLY by possession of the emailed token). Hash the submitted token
    (same `Sha256SecretHasher`) and atomically consume the matching, unexpired row in ONE statement
    (`DELETE … WHERE confirm_token_hash = :hash AND expires_at > now() RETURNING *` — single-use by
    construction). On a hit: re-resolve the `free` plan id fresh, then call the EXISTING
    `create_tenant_with_owner(tenant_name=…, email=…, password_hash=<the STORED hash from M8 —
    NEVER re-hashed>, account_type="personal", plan_id=…)`, returning the EXISTING `SignupResponse`
    (201).
  - M11 Expired vs invalid token distinguished (safe here — see R-sec-6): if the atomic consume
    misses, a SEPARATE read checks whether a matching-but-expired row exists (and deletes it if so,
    for cleanup) -> 410 `ERR_SIGNUP_CONFIRM_EXPIRED` (must re-`POST /admin/auth/signup` for a fresh
    token — no separate resend route); otherwise -> 400 `ERR_SIGNUP_CONFIRM_INVALID` (unknown /
    already-consumed / never-issued).
  - M12 Confirm-time uniqueness race handled loud: if `create_tenant_with_owner` still raises
    `EmailAlreadyRegisteredError` (a race against an UNRELATED signup path for the same email between
    issuance and confirm), the confirm endpoint reuses the EXISTING `AUTH_EMAIL_TAKEN` (409)
    unchanged — safe to reveal here because the caller is token-possession-authenticated (R-sec-6).
  - M13 Confirm endpoint rate-limited too (defense-in-depth): reuse the SAME shared
    `InvitePublicRateLimiter`, a THIRD `action` label, keyed by client IP — bounds abuse of the
    endpoint itself, not an enumeration concern (the 256-bit token space is already brute-force-
    infeasible).
  - M14 No new signup failure mode leaks into the EXISTING branches: the verified-domain-claim join,
    the existing business/global-flag path, and the S1 fallback are all byte-unchanged — this task
    inserts exactly ONE new branch, ahead of the S1 check, gated by `account_type=='personal' AND no
    domain claim AND public_signup_personal_enabled`.
</must>
Reject:
<reject>
  - R1 A business signup, or a personal signup with the new flag OFF, with no domain claim and no
    global flag -> "ERR_SIGNUP_INVITE_ONLY" (403; byte-identical to the existing frozen S1 behavior;
    the new flag has ZERO effect on this path; zero rows created). [reuse, unchanged]
  - R2 Weak password on the scoped-personal path -> "ERR_AUTH_PASSWORD_WEAK" (400; identical
    regardless of the target email's registration state; no pending row created; no email sent).
    [reuse, unchanged]
  - R3 Free plan unprovisioned on the scoped-personal path -> "ERR_SIGNUP_PLAN_UNPROVISIONED" (500;
    identical regardless of the target email; no pending row created; no email sent). [reuse,
    unchanged]
  - R4 Per-IP or per-email rate limit exceeded on the scoped-personal path ->  "ERR_RATE_LIMITED"
    (429, Retry-After header; identical threshold/response regardless of the target email's
    registration state). [reuse]
  - R5 Confirm with an unknown / already-consumed / never-issued token -> "ERR_SIGNUP_CONFIRM_
    INVALID" (400; nothing created; nothing changed). [NEW ErrorSpec]
  - R6 Confirm with a token that existed but is past its TTL -> "ERR_SIGNUP_CONFIRM_EXPIRED" (410;
    the expired row is deleted as cleanup; nothing created; caller must re-submit signup for a fresh
    token). [NEW ErrorSpec]
  - R7 Confirm races an independently-created account for the same email -> "ERR_TENANT_EMAIL_TAKEN"
    (409; the pending row was already atomically consumed/deleted; nothing double-creates). [reuse]
  - R8 Confirm endpoint beyond its per-IP rate limit -> "ERR_RATE_LIMITED" (429, Retry-After).
    [reuse]
</reject>
After:
<after>
  - A visitor with a fresh, unregistered email signs up as personal -> receives the SAME 202 response
    as anyone else, gets exactly one confirmation email, and clicking through creates their tenant +
    owner (on the `free` plan) via the confirm endpoint.
  - A visitor who submits an already-registered email gets the IDENTICAL 202 response and no second
    account is ever created; the real owner (if different from the submitter) receives a generic,
    link-free notice pointing at `/login`; nothing about that email's registration status is EVER
    observable in the synchronous HTTP response — status, body, and dominant cost (the hash) match.
  - Business/enterprise signups are entirely unaffected — still gated behind the pre-existing global
    flag / verified-domain-claim / S1 invite-only fallback, byte-identical to before this task.
  - `tests/signup_routing_authz/test_signup_routing_authz.py` (all 16 tests, S1 FROZEN) passes
    unmodified.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Whether to send the out-of-band "you already have an account" conflict-notice email at all
    (M9) vs. a silent no-op — lowest confidence because it trades a helpful signal to a legitimate
    existing owner (useful if they mistype/reuse their own old email, or as an early warning of a
    targeted probing attempt) against a NEW anonymous-triggerable email-send to an arbitrary address.
    Bounded by the same per-email rate limit as issuance (M3), and link-free since no reset flow
    exists (R-scope-1) — so the worst case is one unwanted, non-actionable email. If wrong (should be
    silent instead): drop `render_signup_conflict_notice_email` + its M9 call site — the HTTP contract
    (already uniform either way) does not change; a same-day follow-up.
  - [ ] 202 + a NEW `SignupPendingResponse` shape (this draft's choice) vs. reusing 201
    `SignupResponse` with nullable `tenant_id`/`user_id` — ranked next. 202 is semantically honest
    (nothing was created yet) and avoids widening the EXISTING `SignupResponse`'s guarantee that a
    201 always carries a real tenant_id/user_id for every OTHER caller. Confirm at freeze.
  - [ ] `personal_signup_confirm_ttl_seconds` default 24h (an email a visitor may not click
    immediately, vs. the member-verify code's ~15 min which bounds an ONLINE brute-force this token
    doesn't have) — confirm the number at freeze, alongside the 3 rate-limit knobs (all defaulted in
    §3, none yet Tin-ratified).
  - [ ] No dashboard confirm screen exists yet in the milestone DAG (R-scope-2) — this contract is
    unusable end-to-end without a follow-on UI task; flagged for the orchestrator, not blocking this
    backend contract's freeze.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Flag OFF keeps a personal signup byte-identical to the frozen S1 gate   # M2,R1
  Given public_signup_personal_enabled is False (the default) and no domain claim exists
  When a personal-account signup is submitted with an already-registered email AND a weak password
  Then the response is 403 ERR_SIGNUP_INVITE_ONLY, exactly like a business signup on the same gate
  And zero rows are created — the gate is checked BEFORE any body validation or DB IO

Scenario: The new flag has zero effect on business signups   # M1,R1
  Given public_signup_personal_enabled is True and no domain claim exists
  When a BUSINESS-account signup is submitted with no global public_signup_enabled flag
  Then the response is 403 ERR_SIGNUP_INVITE_ONLY, unchanged from before this task existed

Scenario: [ATTACKER] Probing a known-registered vs an unknown personal email is indistinguishable   # M7,M9,R-sec-1,R-sec-2
  Given public_signup_personal_enabled is True, "taken@acme.io" is already a registered user, and
    "fresh@acme.io" has never been used, both submitted with the same valid password
  When two personal signups are POSTed, one per email
  Then both responses are 202 with the IDENTICAL JSON shape {"status":"pending_verification","email":<echoed>}
  And the password hasher's hash() was invoked EXACTLY ONCE for EACH request (call-count, not
    wall-clock — the dominant cost is paid identically on both branches, masking the DB-write delta)
  And no response field, header, or status code differs between the two — an observer of the HTTP
    exchange alone cannot determine which email was already registered

Scenario: A fresh email issues a pending signup and exactly one confirm email   # M8
  Given public_signup_personal_enabled is True and "new@acme.io" is not a registered user
  When a personal signup is submitted for new@acme.io with a valid password
  Then the response is 202 pending_verification
  And a pending_personal_signups row exists for new@acme.io with a hashed token and ~24h expiry
  And exactly one confirmation email is sent to new@acme.io carrying the raw token
  And zero tenants/users rows are created

Scenario: An already-registered email creates nothing and notifies out-of-band   # M9
  Given public_signup_personal_enabled is True and "owner@acme.io" is already a registered user
  When a personal signup is submitted for owner@acme.io
  Then the response is 202 pending_verification (identical to the fresh-email case)
  And no pending_personal_signups row is written and the existing user/tenant rows are untouched
  And exactly one generic conflict-notice email (no token, no reset link) is sent to owner@acme.io

Scenario: Weak password rejected identically regardless of target email   # M4,R2
  Given public_signup_personal_enabled is True
  When a personal signup is submitted with a too-short password, once for a fresh email and once
    for an already-registered email
  Then both responses are 400 ERR_AUTH_PASSWORD_WEAK, byte-identical
  And neither call creates a pending row, sends an email, or touches an existing account

Scenario: Free plan unprovisioned fails loud identically regardless of target email   # M5,R3
  Given public_signup_personal_enabled is True and the seeded `free` plan is absent
  When a personal signup is submitted with a valid body
  Then the response is 500 ERR_SIGNUP_PLAN_UNPROVISIONED
  And no pending row is written and no email is sent

Scenario: Rate limit exceeded identically regardless of target email   # M3,R4
  Given public_signup_personal_enabled is True and the per-IP (or per-email) limit is already spent
  When one more personal signup is submitted from that IP (or for that email)
  Then the response is 429 ERR_RATE_LIMITED with a Retry-After header
  And the threshold and response are the SAME whether or not the target email is registered
  And no pending row is written and no email is sent

Scenario: Re-submitting for a still-pending email reissues a fresh token   # M8 reissue
  Given a pending_personal_signups row for "waiting@acme.io" already exists, unconfirmed
  When a second personal signup is submitted for waiting@acme.io before confirming
  Then the row is overwritten with a fresh token hash and a fresh ~24h expiry
  And a second confirmation email is sent
  And the ORIGINAL (now-superseded) token no longer confirms anything

Scenario: Confirming with the correct token creates the tenant   # M10
  Given a valid, unexpired pending_personal_signups row for new@acme.io
  When POST /admin/auth/signup/confirm is called with the correct token
  Then the response is 201 with a fresh tenant_id/user_id and joined_existing_tenant=False
  And the new tenant's account_type is 'personal' and its plan is the seeded `free` plan
  And the pending_personal_signups row no longer exists (consumed)

Scenario: Confirming with an unknown or already-consumed token is rejected   # M11,R5
  Given a token that was never issued, or was already consumed by a prior confirm
  When POST /admin/auth/signup/confirm is called with that token
  Then the response is 400 ERR_SIGNUP_CONFIRM_INVALID
  And no tenant/user row is created

Scenario: Confirming with an expired token is rejected and cleaned up   # M11,R6
  Given a pending_personal_signups row whose expires_at has passed
  When POST /admin/auth/signup/confirm is called with that (expired) token
  Then the response is 410 ERR_SIGNUP_CONFIRM_EXPIRED
  And the expired row is deleted and no tenant/user row is created
  And re-submitting the SAME token afterward returns 400 ERR_SIGNUP_CONFIRM_INVALID (not 410)

Scenario: Replaying an already-consumed token fails closed   # M10 single-use
  Given a token that was just successfully confirmed (tenant already created)
  When POST /admin/auth/signup/confirm is called again with the SAME token
  Then the response is 400 ERR_SIGNUP_CONFIRM_INVALID
  And no second tenant/user row is created

Scenario: Concurrent confirms with the same token — only one succeeds   # M10 atomicity
  Given a valid, unexpired pending_personal_signups row
  When two confirm requests with the SAME token arrive concurrently
  Then exactly ONE receives 201 with a real tenant_id/user_id
  And the other receives 400 ERR_SIGNUP_CONFIRM_INVALID (the row was already atomically consumed)
  And exactly one tenant/user pair exists afterward, never two

Scenario: Confirm-time race against an independently-created account   # M12,R7
  Given a pending_personal_signups row for sam@acme.io, and — after issuance — sam@acme.io becomes
    a registered user through an UNRELATED path (e.g. a business domain-join)
  When POST /admin/auth/signup/confirm is called with sam's valid, unexpired token
  Then the response is 409 ERR_TENANT_EMAIL_TAKEN
  And no second tenant/user row for sam@acme.io is created
  And the pending_personal_signups row is gone (consumed by the atomic DELETE, not left dangling)

Scenario: Confirm endpoint rate-limited   # M13,R8
  Given the confirm endpoint's per-IP limit is already spent
  When one more confirm request arrives from that IP
  Then the response is 429 ERR_RATE_LIMITED with a Retry-After header
  And no tenant/user row is created regardless of whether the submitted token was valid

Scenario: The existing S1 gate and its whole frozen suite are unaffected   # M14
  Given the codebase after this task's build
  When tests/signup_routing_authz/test_signup_routing_authz.py runs in full
  Then all 16 tests pass unmodified, with no test file edited by this task
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /admin/auth/signup   body: { tenant_name, email, password, account_type: "personal"|"business" }

  # NEW scoped path — account_type=='personal', no verified domain claim,
  # public_signup_personal_enabled=true. Response is IDENTICAL for a fresh vs. already-registered
  # email (M7/M9) — the ONLY difference is which email template is dispatched, never in this body.
  202 -> { status: "pending_verification", email: <lower-cased submitted email> }
  400 -> { code: "ERR_AUTH_PASSWORD_WEAK" }          # too-short password [reuse, unchanged]
  429 -> { code: "ERR_RATE_LIMITED" }                # per-IP OR per-email limit [reuse]
  500 -> { code: "ERR_SIGNUP_PLAN_UNPROVISIONED" }   # free plan not seeded [reuse, unchanged]

  # personal + flag FALSE (default), OR business without an existing global flag / domain claim —
  # UNCHANGED existing S1 behavior, byte-identical, checked BEFORE any body validation/DB IO:
  403 -> { code: "ERR_SIGNUP_INVITE_ONLY" }          # [reuse, untouched]
  # (existing verified-domain-claim join / business+global-flag paths — UNCHANGED, not repeated here)

POST /admin/auth/signup/confirm   body: { token: string }
  # PUBLIC — no bearer auth. The token is the ONLY credential (delivered solely by email);
  # authenticated by possession, not by anything request-supplied about the target identity.
  201 -> SignupResponse   (tenant_id, user_id, joined_existing_tenant=False)   [reuse, unchanged shape]
  400 -> { code: "ERR_SIGNUP_CONFIRM_INVALID" }      # unknown / consumed / never-issued [NEW]
  410 -> { code: "ERR_SIGNUP_CONFIRM_EXPIRED" }      # existed, past TTL; re-signup for a fresh one [NEW]
  409 -> { code: "ERR_TENANT_EMAIL_TAKEN" }          # confirm-time race (R-sec-5/M12) [reuse]
  500 -> { code: "ERR_SIGNUP_PLAN_UNPROVISIONED" }   # free plan vanished between issuance+confirm [reuse]
  429 -> { code: "ERR_RATE_LIMITED" }                # per-IP limit on this endpoint [reuse]

Schema (additive only; tenants/users/the S1 gate/the frozen S1 suite are UNTOUCHED):
  NEW table `pending_personal_signups`
    id UUID PK
    email TEXT NOT NULL UNIQUE                (lower-cased)
    tenant_name TEXT NOT NULL
    password_hash TEXT NOT NULL               (argon2, via the EXISTING PasswordHasher; never re-hashed)
    confirm_token_hash TEXT NOT NULL UNIQUE   (SHA-256 hex via the EXISTING Sha256SecretHasher — no
                                                HMAC pepper: 256-bit CSPRNG, mirrors the invite-token
                                                precedent, NOT member-verify-code's low-entropy scheme)
    expires_at TIMESTAMPTZ NOT NULL
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    migration chains off the current single alembic head `a4f2d9c17b3e` (domain_invite_links.py).

  repository (additive, on the EXISTING IdentityRepository / SqlAlchemyIdentityRepository):
    issue_or_reissue_pending_signup(email, tenant_name, password_hash, confirm_token_hash,
      expires_at) -> None            (UPSERT by email — create-or-reissue idiom)
    consume_pending_signup(confirm_token_hash) -> PendingPersonalSignup | None
      (single-statement `DELETE … WHERE confirm_token_hash = :hash AND expires_at > now() RETURNING *`
       — single-use by construction; a concurrent double-confirm can never both return a row)
    pop_expired_pending_signup(confirm_token_hash) -> bool
      (`DELETE … WHERE confirm_token_hash = :hash RETURNING 1` — called ONLY when consume misses, to
       distinguish "expired" from "never existed/already consumed" and opportunistically clean up)

  domain (new, pure, IO-free):
    tenants/domain/personal_signup_confirm.py: generate_confirm_token() -> str
      (secrets.token_urlsafe(32) — mirrors create_claim_use_case.py's _TOKEN_BYTES=32)
    hashing REUSES gateway.keys.infrastructure.sha256_hasher.Sha256SecretHasher AS-IS (already
      imported cross-context by tenants/application/invite_use_cases.py — same precedent, no new
      hashing module)
    tenants/domain/errors.py + PendingSignupNotFoundError, PendingSignupExpiredError (IdentityError
      subclasses, mirror InviteNotFoundError/InviteExpiredError's plain-marker shape)
    tenants/domain/entities.py + PendingPersonalSignup(email, tenant_name, password_hash) — a
      repository-internal transfer object; NEVER rides any API schema (mirrors DomainClaim's
      code-fields-stay-internal convention)

  application (new use-cases, tenants/application/use_cases.py):
    IssuePendingSignupUseCase(repository, hasher, email_sender, *, confirm_ttl_seconds, origin)
      .execute(tenant_name, email, password) -> None
      — order: [password length -> WeakPasswordError] -> [resolve free plan_id -> IndividualPlan
      MissingError] -> [hash password UNCONDITIONALLY, M6] -> [get_user_by_email] -> branch M8/M9.
      NEVER raises for an email-exists conflict (that branch is silent-success, M9) — only
      WeakPasswordError / IndividualPlanMissingError propagate, translated exactly like today's
      SignupUseCase (AUTH_PASSWORD_WEAK / SIGNUP_PLAN_UNPROVISIONED).
    ConfirmPendingSignupUseCase(repository) .execute(token) -> tuple[tenant_id, user_id]
      — M10/M11/M12, calls the EXISTING create_tenant_with_owner unchanged.

  api: tenants/api/schemas.py + SignupPendingResponse(status: Literal["pending_verification"],
    email: EmailStr), ConfirmSignupRequest(token: str) · tenants/api/router.py: signup() gains ONE
    new branch (account_type=='personal' AND no domain claim AND public_signup_personal_enabled)
    positioned AHEAD of the existing SIGNUP_INVITE_ONLY check; + a NEW POST /admin/auth/signup/
    confirm route · tenants/api/deps.py + 2 use-case factories.

  rate limiting: REUSES the EXISTING request.app.state.invite_public_limiter
    (InvitePublicRateLimiter, fail-open) with 3 new `action` labels ("personal_signup_ip",
    "personal_signup_email", "personal_signup_confirm") — zero new limiter class. Client IP via the
    EXISTING gateway.core.net.resolve_trusted_client_ip (never raw request.client.host).

  email: 2 new pure templates (mirror email/application/domain_verified_email_template.py's shape):
    render_signup_confirm_email(*, to, tenant_name, token, origin) -> EmailMessage
    render_signup_conflict_notice_email(*, to, origin) -> EmailMessage   (no token, no reset link)
    both dispatched via the EXISTING fail-open gateway.email.application.email_dispatch.send_email.

  config (core/config.py, additive, mirrors invite_preview_rpm's positive-int validator pattern):
    public_signup_personal_enabled: bool = False        # GATEWAY_PUBLIC_SIGNUP_PERSONAL_ENABLED
    personal_signup_confirm_ttl_seconds: int = 86400     # GATEWAY_PERSONAL_SIGNUP_CONFIRM_TTL_SECONDS
    personal_signup_ip_rpm: int                          # GATEWAY_PERSONAL_SIGNUP_IP_RPM
    personal_signup_email_rpm: int                       # GATEWAY_PERSONAL_SIGNUP_EMAIL_RPM
    personal_signup_confirm_rpm: int                     # GATEWAY_PERSONAL_SIGNUP_CONFIRM_RPM
    (exact numeric defaults for the 3 RPM knobs + the TTL: Tin to ratify at freeze, see §1 Assumptions)
```

SAFETY RULES (security task — binding):
- WHAT AN UNAUTHENTICATED CALLER CAN LEARN FROM `POST /admin/auth/signup` (personal, flag ON):
  NOTHING about whether any given email address is already registered. Status code (202), JSON body
  shape, and the dominant cost (one unconditional argon2 hash, M6) are IDENTICAL whether the
  submitted email is brand-new or already belongs to an existing account. The only behavior that
  differs (which email template is dispatched) happens entirely OUT OF BAND, asynchronously, to an
  inbox the caller does not control — never reflected in the synchronous HTTP response. Password-
  strength (400) and plan-unprovisioned (500) rejections stay safe to keep DISTINCT because they are
  functions of the submitted password / server state ONLY — identical for every target email, never a
  function of whether THAT email is registered.
- NOT A GLOBAL FLIP: `public_signup_personal_enabled` is scoped to `account_type=='personal'` ONLY;
  it never touches, widens, or is read anywhere near `public_signup_enabled` (business/enterprise
  stays gated exactly as before) and never touches the verified-domain-claim auto-join path.
- DEFERRED CREATION: no tenant/user row is EVER created by the initial POST — only a short-lived,
  single-use `pending_personal_signups` row (or nothing, on conflict). An abandoned, never-confirmed
  signup leaves no loggable-in identity behind, only a ~24h row lazily purged on next access to that
  email/token (a periodic sweep job is a Should for a later task, not required for this contract).
- HIGH-ENTROPY TOKEN, RIGHT-SIZED CRYPTO: the confirm token is 256 bits of CSPRNG
  (`secrets.token_urlsafe(32)`), hashed at rest with a BARE SHA-256 (`Sha256SecretHasher`, already
  used for invite tokens of the same entropy class) — NOT the HMAC-pepper scheme used for the
  LOW-entropy (10^6) member-verify code, because a 256-bit preimage is already computationally
  infeasible to reverse from a bare digest; the heavier scheme would defend against a threat this
  token doesn't have.
- SINGLE-USE, ATOMIC: `consume_pending_signup` is one `DELETE … RETURNING` statement — a concurrent
  double-confirm of the SAME token can never both succeed (the second sees no row, M10 scenario).
- CONFIRM-TIME DISCLOSURE IS SAFE, BY DESIGN: 400 vs. 410 vs. 409 at `/signup/confirm` reveal
  token-state information ONLY to whoever already possesses the emailed token — never to an anonymous
  prober working the INITIAL signup endpoint (R-sec-6) — so distinguishing them here does not reopen
  the S1 property this task exists to preserve.
- PASSWORD NEVER RE-VALIDATED OR RE-HASHED AT CONFIRM: the argon2 hash computed at issuance (M6) is
  stored once and passed through to `create_tenant_with_owner` UNCHANGED — the confirm endpoint never
  sees or handles the plaintext password again.
- RATE-LIMIT KEYS ARE UNIFORM: the per-IP AND per-email checks run BEFORE the email-existence branch,
  with the SAME threshold/response for either outcome — a 429 never correlates with the target
  email's registration status.

Glossary deltas: pending-personal-signup: a short-lived (~24h), single-use row proving a
personal-tier signup's email + tenant_name + password intent BEFORE the tenant/user rows exist;
distinct from "member-verified" (mailbox-proof issued AFTER a business tenant already exists, member-
verified-recognition TASK.md) and from "verified" (DNS-TXT domain proof, domain-capture TASK.md) —
this is mailbox-proof-BEFORE-creation, the personal-tier analog of the invite/join flow's own
already-existing token pattern. [folded foundation-version 55]

Status: FROZEN @ v1 — approved by Tin Dang

Reported: yes — presented to Tin 2026-07-20, leading with the ⚠ timing-mask flag below.

Least-sure flag surfaced at freeze: [contract] ⚠ the M6 Argon2 timing mask may be INCOMPLETE.
Orchestrator review (post-draft, verified against `tenants/api/router.py:139-152`): the existing
member-verify issuance is `await`ed INLINE in the request handler, so both new branches will also
send their email inline — M8 renders/sends a confirm email, M9 renders/sends a conflict notice.
Both branches send exactly ONE email, which makes them symmetric in COUNT but NOT necessarily in
LATENCY (different templates, potentially different send paths). A measurable send-latency delta
between the two branches reintroduces the very enumeration oracle M6/M7 exist to close — the Argon2
hash masks the DB branch, not the mail branch. Least confident because the draft's timing analysis
stops at Argon2 and never reasons about the inline send. RESOLUTION REQUIRED AT BUILD: either
(a) move BOTH sends off the request path (fire-and-forget / queued) so neither contributes to
response latency, or (b) prove equal-latency and pin it with a timing test. Cost if wrong: the
headline security property of this task is silently false while every test still passes — exactly
the failure mode the HARD-STOP adversarial verify must hunt for.

Further ranked flags (see §1 Assumptions for the full reasoning):
  1. [contract] ⚠ Whether to send the out-of-band conflict-notice email at all (M9) vs. a silent
     no-op. RECOMMEND sending it (generic, link-free; bounded by the per-email rate limit). Cost if
     wrong: drop one call site, zero contract-shape change.
  2. [contract] 202 + a new `SignupPendingResponse` (this draft's choice) vs. reusing 201
     `SignupResponse` with nullable fields (rejected in this draft — would weaken every EXISTING 201
     consumer's guarantee of a real tenant_id/user_id).
  3. [numbers] `personal_signup_confirm_ttl_seconds` (~24h proposed) + the 3 RPM knobs — all
     defaulted above, none yet Tin-ratified.
  4. [scope] R-scope-2 — no dashboard confirm-screen task exists in the current milestone DAG; this
     contract is backend-complete but not end-to-end usable without one.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (the new `pending_personal_signups` domain/application/infrastructure/api
surface: `tenants/domain/personal_signup_confirm.py`, `IssuePendingSignupUseCase` +
`ConfirmPendingSignupUseCase`, the 3 new `IdentityRepository` methods, the signup router's new
branch + the new `/admin/auth/signup/confirm` route, the 2 new email templates). The pre-existing
`tests/signup_routing_authz/` suite (17 collected items / 16 scenarios) is run UNMODIFIED as
regression evidence, not counted toward this task's own coverage delta.

Plan (one test per scenario, asserting behavior not internals — mirrors
`test_member_verified_recognition.py`'s arrange/act/assert/covers shape):
<test_plan>
  - test_flag_off_personal_signup_byte_identical_to_s1: arrange an already-registered email
    (seeded via the primary flag-ON `client`) + a SEPARATE app with BOTH
    `public_signup_personal_enabled` and `public_signup_enabled` at their False default / act
    POST personal signup for that email with a weak password / assert 403
    ERR_SIGNUP_INVITE_ONLY (never 400/409) + zero new rows + zero emails sent · covers M2,R1
  - test_new_flag_zero_effect_on_business_signups: arrange `public_signup_personal_enabled=True`,
    `public_signup_enabled` left at its False default / act POST a BUSINESS signup, no domain
    claim / assert 403 ERR_SIGNUP_INVITE_ONLY, unchanged, zero rows, zero emails · covers M1,R1
  - test_probe_registered_vs_unknown_indistinguishable: arrange taken@acme.io registered via the
    primary app (same DB), fresh@acme.io never used, both same-length emails, a counting spy on
    `app.state.password_hasher.hash` / act POST personal signup for each / assert both 202,
    IDENTICAL JSON key-set/values-shape, IDENTICAL header-key-set, IDENTICAL Content-Length, and
    hash() invoked EXACTLY ONCE per request (call-count, not wall-clock) · covers
    M7,M9,R-sec-1,R-sec-2
  - test_email_dispatch_never_blocks_the_response: arrange a FakeEmailSender with a 1.5s
    injected send delay on a flag-ON app, one fresh + one already-registered email / act POST
    each personal signup / assert BOTH responses return in <0.5s (well under the injected
    delay), proving email dispatch never sits on the request path on EITHER branch — the
    STRUCTURAL closure of the ⚠ M6 timing-mask flag (see rationale below) · covers M6,R-sec-2
  - test_fresh_email_issues_pending_signup_and_one_confirm_email: arrange flag ON + free plan
    seeded / act POST personal signup for a fresh email / assert 202 pending_verification + a
    `pending_personal_signups` row (hashed token, ~24h expiry) + EXACTLY ONE confirm email
    carrying a raw token-shaped string + zero tenants/users rows · covers M8
  - test_already_registered_creates_nothing_and_notifies_out_of_band: arrange an already
    -registered email (seeded via the primary app) + free plan seeded / act POST personal
    signup for that email / assert 202 pending_verification (identical shape) + zero pending
    rows + the existing tenant/user row byte-unchanged + EXACTLY ONE conflict-notice email
    (mentions /login, carries NO token-shaped substring) · covers M9
  - test_weak_password_rejected_identically_regardless_of_target_email: arrange flag ON, one
    fresh + one already-registered email / act POST personal signup with a too-short password
    for each / assert both 400 ERR_AUTH_PASSWORD_WEAK, byte-identical bodies, zero rows, zero
    emails · covers M4,R2
  - test_plan_unprovisioned_fails_loud: arrange flag ON, the `free` plan deliberately NOT seeded
    / act POST a valid personal signup / assert 500 ERR_SIGNUP_PLAN_UNPROVISIONED + zero pending
    rows + zero emails · covers M5,R3
  - test_rate_limit_exceeded_identically_regardless_of_target_email: arrange
    `personal_signup_ip_rpm=2`, one pre-registered email / act 2 admitted personal signups (2
    distinct fresh emails) then a 3rd (a brand-new fresh email) and a 4th (the registered email)
    / assert both the 3rd and 4th are 429 ERR_RATE_LIMITED with Retry-After, byte-identical
    regardless of the target's registration state + only the 2 admitted calls dispatched an
    email · covers M3,R4
  - test_resubmission_reissues_a_fresh_token: arrange flag ON / act POST the SAME still-pending
    email twice / assert 2 distinct confirm emails/tokens, exactly ONE pending row (UPSERT), the
    stale first token now 400 ERR_SIGNUP_CONFIRM_INVALID, the fresh second token confirms 201 ·
    covers M8 (reissue)
  - test_confirm_with_correct_token_creates_the_tenant: arrange an issued, unexpired pending
    signup / act POST /admin/auth/signup/confirm {token} / assert 201 SignupResponse
    (joined_existing_tenant=False), account_type='personal', plan_id=the seeded free plan, the
    pending row consumed, +1 tenant/+1 user · covers M10
  - test_confirm_with_unknown_token_rejected: arrange no issuance at all / act POST confirm with
    a never-issued token / assert 400 ERR_SIGNUP_CONFIRM_INVALID + zero rows · covers M11,R5
  - test_confirm_replay_of_consumed_token_fails_closed: arrange an issued token, confirmed once
    (201) / act POST confirm again with the SAME token / assert 400 ERR_SIGNUP_CONFIRM_INVALID +
    still exactly ONE tenant/user pair for that email · covers M10 (single-use)
  - test_confirm_with_expired_token_rejected_and_cleaned_up: arrange
    `personal_signup_confirm_ttl_seconds=1`, issue then sleep past expiry / act POST confirm
    (expired) then POST confirm again with the SAME token / assert 410
    ERR_SIGNUP_CONFIRM_EXPIRED then 400 ERR_SIGNUP_CONFIRM_INVALID (not 410 again — proves
    cleanup) + zero tenant/user rows ever created · covers M11,R6
  - test_confirm_concurrent_same_token_only_one_succeeds: arrange an issued, unexpired token /
    act 2 concurrent POST confirm calls with the SAME token (asyncio.gather) / assert exactly
    one 201 and one 400 ERR_SIGNUP_CONFIRM_INVALID + exactly one tenant/user pair afterward ·
    covers M10 (atomicity)
  - test_confirm_race_against_independently_created_account: arrange an issued token for
    sam@acme.io, then sam@acme.io registers via an UNRELATED business signup on the primary app
    (same DB) BEFORE confirm / act POST confirm with sam's valid token / assert 409
    ERR_TENANT_EMAIL_TAKEN + still exactly ONE tenant/user pair (the unrelated one) + the pending
    row consumed (not dangling) · covers M12,R7
  - test_confirm_endpoint_rate_limited: arrange `personal_signup_confirm_rpm=2` / act 3 POST
    confirm calls with garbage tokens from one IP / assert the 3rd is 429 ERR_RATE_LIMITED with
    Retry-After, regardless of token validity + zero tenant/user rows · covers M13,R8
</test_plan>

M14 ("the frozen S1 suite is unaffected") is deliberately NOT re-encoded as a pytest test inside
this suite — shelling out to pytest from inside a pytest test has no precedent in this codebase.
Instead it is proven directly as RED-phase evidence below (a real `pytest` run of the frozen
file, plus a `git diff` proving the file itself was never touched).

⚠ M6 timing-mask flag — resolution taken for TESTS (design decision, not Build's to relitigate):
comparing branch-A-vs-branch-B wall-clock deltas directly would be flaky (send times are
naturally close; CI jitter dwarfs the signal). `test_email_dispatch_never_blocks_the_response`
instead pins the STRUCTURAL property that makes any such delta irrelevant: neither branch's
response may depend on email-send completion at all (a 1.5s injected send delay must never
surface in the response latency, on EITHER branch). This is deliberately a stronger, build-
agnostic requirement than "make M8 and M9 equally slow" — it forces email dispatch off the
request path entirely (`asyncio.ensure_future`, the shape `LoginUseCase.execute` already uses
for its own fire-and-forget superadmin-login audit write — `tenants/application/use_cases.py`),
closing the oracle by construction rather than by an achieved-but-fragile timing balance.

RED evidence (2026-07-20, `GATEWAY_TEST_DATABASE_URL=postgresql+asyncpg://gateway:gateway@
localhost:5433/gateway_test_scopedssv`, real Postgres :5433 / Redis :6380, `--no-cov`):
```
uv run pytest tests/scoped_self_serve_signup/ -q --no-cov
...
15 failed, 2 passed in 25.05s
```
15/17 fail for the intended reason — either the untouched S1 gate (`403 ERR_SIGNUP_INVITE_ONLY`,
the new `account_type=='personal' AND public_signup_personal_enabled` branch does not exist yet)
or a plain FastAPI `404 {"detail":"Not Found"}` on `/admin/auth/signup/confirm` (the route does
not exist yet). Confirmed individually (no wrong-reason failures — e.g. no fixture/harness
error masquerading as a real assertion failure). The other 2
(`test_flag_off_personal_signup_byte_identical_to_s1`,
`test_new_flag_zero_effect_on_business_signups`) pass ALREADY, pre-Build — both assert a
property that is BYTE-IDENTICAL to today's shipped S1 behavior (M2/M1's own "zero coupling to
the new flag" requirement), so they are regression guards for Build, not new-capability proofs;
this is the same shape as the frozen S1 suite's own tests 6/7/8 (invite issuance/acceptance
"unaffected by the signup flag").

Frozen S1 suite, run in full, unmodified (same DB/session):
```
uv run pytest tests/signup_routing_authz/ -q --no-cov
17 passed in 13.65s
```
`git status --porcelain -- apps/gateway/tests/signup_routing_authz/` and
`git diff --stat -- apps/gateway/tests/signup_routing_authz/` both empty — the frozen file was
never touched.

Tests live in: `apps/gateway/tests/scoped_self_serve_signup/` (conftest.py + one test module, 17
collected test items) · base test DB MUST be `gateway_test` (a unique `GATEWAY_TEST_DATABASE_URL`
suffix was used for this run per the shared-:5433-Postgres project convention) · MUST run red
(missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `./src/`   <fill before the §3 freeze — every file the build may write>
Strategy (ordered batches): <1. … 2. … — the planned build order; guidance, not enforced; preferred architecture/pattern strategies; advise solution/method to resolve issues/implement features; let the named Persona's domain stance (below) shape the approach, not just architecture patterns>

Persona (required): <name the persona file under `.add/personas/` this build embodies as a domain stance atop SOUL.md — advisory, never lowers a gate; name "generic" if no project persona fits yet>
Spawn isolation (default): <prefer isolation: "worktree" for any subagent build/verify spawn, not only explicit parallel mode; shared-tree needs a stated reason — see worktree-isolated-spawn-default>
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
- [x] the green was EARNED, not gamed — refute-read by 2 independent add-verify agents (anti-enum + token-lifecycle), both EARNED — see below
- [x] concurrency / timing of the risky operation is safe — single-use token consume shown race-safe (see 3-lens Concurrency)
- [x] no exposed secrets, injection openings, or unexpected dependencies — confirm token: 256-bit CSPRNG, emailed once, only SHA256 hash persisted; password_hash argon2 pass-through
- [x] layering & dependencies follow CONVENTIONS.md — deferred-creation flow in tenants/ context, additive
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] a personal-tier signup for a NEW email and for an ALREADY-EXISTING email are indistinguishable to the requester — identical 202 SignupPendingResponse body, status, headers; creation deferred (nothing written to tenants/users on first POST); conflict signalled only out-of-band by email — confirmed by add-verify anti-enumeration lens + green-bar `pytest (Makefile:test / ci.yml 'Tests' step)`: full gateway suite green in 5 fg chunks @-n6 (910+ / ✓ / ✓ / 738 / 756), tests/scoped_self_serve_signup/ green.
- [x] the mailbox-proof confirm token is single-use, expiring, and unforgeable — atomic consume (no double-tenant under concurrent replay), expires_at enforced at confirm, hash-only at rest — confirmed by add-verify token-lifecycle lens.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — new symbols (PendingPersonalSignupRow, the scoped branch in tenants/api/router.py, the 5 config knobs + 2 validators) all referenced and reachable; confirmed by both verify agents + full-suite green.
- [x] DEAD-CODE (code) — no new unused/orphaned symbol; scoped branch gated on account_type=="personal" AND public_signup_personal_enabled (default-OFF).
- [ ] SEMANTIC (prose / non-code) — n/a (code task)

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — SIGNUP_INVITE_ONLY, the S1 gate, resolve_verified_tenant, PendingPersonalSignupRow all resolve; confirmed by both verify agents.
- [x] no anchor moved/renamed since Ground SHA — the scoped branch is inserted AHEAD of the unchanged S1 gate; verified-domain-lookup-first ordering intact.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: 2 independent add-verify agents (opus) — a32096 (anti-enumeration lens) + a05675 (token-lifecycle lens) · adversarially checked: (1) the 5 distinguishers {response-body, status, headers, timing, side-effect} for an existence oracle — none found; deferred creation + uniform 202 + Argon2 timing mask confirmed; anti-enum tests refute-read as would-fail-if-violated. (2) token entropy/at-rest (hash-only), the double-confirm TOCTOU race (shown safe), expiry-at-confirm, UPSERT-reissue invalidation; single-use + expiry tests refute-read. Both verdicts CLEAR / no HARD-STOP.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: 2 independent add-verify agents (a32096 anti-enum + a05675 token-lifecycle)
1. Security: CLEAR — no anti-enumeration leak (5 distinguishers all closed); no token forge/guess/replay; ≥2 independent adversarial verifies, both CLEAR, no HARD-STOP finding.
2. Concurrency: CLEAR — single-use confirm consume is atomic (unique(confirm_token_hash) backstop + row-lock); concurrent double-confirm cannot mint two tenants.
3. Architecture: CLEAR — additive deferred-creation flow in tenants/ context; frozen S1 gate + domain-capture tables untouched.
Verdict: PASS
Residue: none
Binding: yes — sensitivity: security (HARD-STOP floor satisfied — no finding to stop on; ≥2 adversarial verifies recorded)

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: PASS
component: gateway · expected green-bar: pytest (Makefile:test / ci.yml 'Tests' step) · verify: cd apps/gateway && uv run pytest
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-21

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

