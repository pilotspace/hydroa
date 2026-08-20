---
type: Task
title: Login rate-limit + lockout, password reset, session revocation at both seams
status: done
depth: deep
sensitivity: security
milestone: release-hardening-p0
scope:
  - apps/gateway/src/gateway/tenants
  - apps/gateway/src/gateway/email
  - apps/gateway/src/gateway/core
  - apps/gateway/src/gateway/main.py
  - apps/gateway/migrations
  - apps/gateway/tests
gives:
  - S1 hardened POST /admin/auth/login — per-IP + per-email rate limit ahead of the credential check
  - S2 password-reset flow — POST /admin/auth/password-reset (uniform 202) + /password-reset/confirm (token → new password, revokes prior sessions)
  - S3 session revocation — jti claim on issued JWTs, POST /admin/auth/logout, revocation enforced at the shared identity seam
generated: { by: add/3.2.0, at: 2026-08-18 }
verified:
  - { by: "Tin Dang", at: 2026-08-18, act: freeze, authority: human, direction: "sha256:52aa5b859938afee" }
  - { by: "cli", at: 2026-08-18, act: brief, authority: process, brief: "sha256:0d669988c56902b4" }
  - { by: "process:run", at: 2026-08-18, act: run, authority: process, outcome: PASS, receipt: /tasks/auth-hardening-login-sessions.d/runs/1.md }
  - { by: "process:run", at: 2026-08-18, act: run, authority: process, outcome: PASS, receipt: /tasks/auth-hardening-login-sessions.d/runs/2.md }
  - { by: "Tin Dang", at: 2026-08-18, act: refreeze, authority: human, direction: "sha256:42ff21decf0d7ec1" }
  - { by: "process:run", at: 2026-08-18, act: run, authority: process, outcome: PASS, receipt: /tasks/auth-hardening-login-sessions.d/runs/3.md }
  - { by: "cli", at: 2026-08-18, act: brief, authority: process, brief: "sha256:55b77edcec12caac" }
  - { by: "process:run", at: 2026-08-18, act: run, authority: process, outcome: PASS, receipt: /tasks/auth-hardening-login-sessions.d/runs/4.md }
  - { by: "Tin Dang", at: 2026-08-18, act: gate, authority: human, outcome: PASS, receipt: /tasks/auth-hardening-login-sessions.d/runs/4.md, brief: "sha256:55b77edcec12caac" }
advised_by: appsec-engineer
---
## CARD
goal: the product's front door stops being its weakest seam — brute force on /admin/auth/login is bounded before any credential work, a locked-out-of-account user has a real self-serve reset path, and a stolen/ended session can actually be killed server-side
why: P0 #1 of the 2026-08-18 deep review — /admin/auth/login is the ONLY unprotected public auth endpoint (every sibling is limiter-gated in the same file), the codebase self-acknowledges "no forgot-password/reset flow exists", and the HS256 session JWT has no jti and no revocation, so a compromised token lives until exp. All three close with in-tree patterns: the InvitePublicRateLimiter + resolve_trusted_client_ip pair, the pending-signup token→email→confirm flow, and the DbImpersonationSessionGuard per-request liveness precedent.
beat: done · next: add status

## RULES
<must>
- M1 /admin/auth/login is limiter-gated BEFORE any credential work: per trusted-client-IP (`login_ip_rpm`) AND per normalized email (`login_email_rpm`), both via the EXISTING `InvitePublicRateLimiter` on `app.state.invite_public_limiter` with new action labels, fail-open on Redis outage, refusal = the EXISTING `RATE_LIMITED` problem + `Retry-After`. A limited attempt never reaches the password hasher. -> "UNTHROTTLED_LOGIN"
- M2 Anti-enumeration byte-identity holds everywhere this task touches: the login 429 and the reset-request 202 are byte-identical for registered vs unknown emails; the existing constant-time dummy-hash mask and `AUTH_CREDENTIALS_INVALID` shape are unchanged. -> "ENUMERATION_ORACLE"
- M3 Reset tokens are 256-bit, stored ONLY as SHA-256 hashes, single-use (marked used atomically with the password write), TTL-bounded (`password_reset_ttl_seconds`), and delivered ONLY by email through the existing dispatch seam (fire-and-forget, never blocks or shapes the 202). -> "PLAINTEXT_RESET_TOKEN"
- M4 A successful reset-confirm sets the argon2 hash AND revokes every pre-existing session for that user (users.sessions_not_before watermark) in the same transaction — a stolen session does not survive the victim's reset.
- M5 Every newly issued session JWT carries a `jti` (NOT added to decode's required-claims set — legacy tokens still decode). POST /admin/auth/logout persists the presented jti to `revoked_auth_sessions` server-side; the shared identity seam (`GetIdentityUseCase.execute`) refuses a revoked jti and any token with iat < the user watermark. A cookie deleted client-side is not a logout. -> "STATELESS_REVOCATION_THEATER"
- M6 The revocation/watermark read is bounded (`session_revocation_check_timeout_seconds`, DbImpersonationSessionGuard precedent) and fails CLOSED on store failure with a typed 503-class problem — never a silent allow, never an unbounded await. Reset-confirm and logout each emit an audit event whose metadata never contains the token or password.
- M7 Client IP is only ever `resolve_trusted_client_ip(request, settings.trusted_proxy_hops)`; all new knobs get the fail-fast positive-value boot validators the sibling `_rpm` knobs use; the new table lands on all FOUR manifests (migration · EXPECTED_TABLES · alembic env.py ORM import · guardrails allow-list). -> "RAW_CLIENT_IP"
</must>
<reject>
- R:UNTHROTTLED_LOGIN any code path where a login attempt reaches credential verification without both limiter checks having run -> "UNTHROTTLED_LOGIN"
- R:ENUMERATION_ORACLE any status/body/header difference on login-429 or reset-202 that distinguishes a registered from an unknown email -> "ENUMERATION_ORACLE"
- R:PLAINTEXT_RESET_TOKEN a reset token at rest un-hashed, in a log line, in an audit row, or in any API response body -> "PLAINTEXT_RESET_TOKEN"
- R:STATELESS_REVOCATION_THEATER a logout/revocation whose only effect is client-side; the server keeps honoring the token -> "STATELESS_REVOCATION_THEATER"
- R:LOCKOUT_DOS a lockout mechanism an anonymous attacker can use to PERMANENTLY lock a victim out (per-email throttling must decay with the limiter window and must never gate the reset flow) -> "LOCKOUT_DOS"
- R:RAW_CLIENT_IP reading request.client.host (or trusting arbitrary XFF depth) for any limiter key -> "RAW_CLIENT_IP"
- R:WEAKENED_SIBLING any behavioral diff to the signup/confirm/invite/join limiter paths, the timing-mask call-count contract, or decode's required-claims set -> "WEAKENED_SIBLING"
</reject>

## ASSUMPTIONS
- A1 [who] covers: S1, S2 · the request does not say who calls these endpoints; taking: anonymous public callers, no CAPTCHA/proof-of-work at this depth — the limiter pair is the only bot bound -> if wrong, sustained distributed brute force is bounded per-email but not globally; acceptable residue, revisit with edge WAF
- A2 [who] covers: S3 · the request does not say whose sessions logout kills; taking: ONLY the presented token's jti — "log out everywhere" is reset-confirm's watermark, not logout's job · probe: after logout of token T1, a second token T2 for the same user still authenticates -> if wrong, one device's logout would nuke every device silently
- A3 [which] covers: S2 · the request does not say which users may reset; taking: any user row with a matching email, including SSO/SCIM-provisioned users without a prior password (mirrors invite-accept, which also sets a first password) -> if wrong, an SSO-only workspace gains an unexpected password credential class; flagged for Tin at freeze
- A4 [when] covers: S1 · the request does not say the lockout window; taking: lockout IS the per-email limiter's sliding decay — no persistent lock state, a legit user retries after Retry-After · probe: at the threshold the N+1th attempt 429s; after the window it succeeds -> if wrong (a real persistent lockout wanted), R:LOCKOUT_DOS territory opens and needs an unlock path
- A5 [when] covers: S2 · the request does not say the token-expiry boundary; taking: now >= expires_at refuses (expiry instant is expired, fail closed), TTL knob default 1800s · probe: a token at exactly expires_at is refused -> if wrong, a lapsed token works on its expiry second
- A6 [absent] covers: S2 · the request does not say what an unknown email does; taking: uniform 202, no row written, no email dispatched · probe: FakeEmailSender call count is 0 for unknown, 1 for registered; bodies byte-identical -> if wrong, the reset endpoint becomes the enumeration oracle login refuses to be
- A7 [absent] covers: S3 · the request does not say what a legacy token (no jti) means; taking: it still decodes and authenticates — revocable only via the user watermark — so the deploy forces no mass re-login · probe: a hand-minted token without jti passes /me until the user's watermark moves -> if wrong, every live session breaks at deploy
- A8 [order] covers: S1 · the request does not say check order; taking: IP check then email check, both consumed pre-credential; a limited attempt performs ZERO hasher calls · probe: hasher spy records no call on a 429'd attempt -> if wrong, the limiter is decoration around the expensive path it exists to protect
- A9 [order] covers: S3 · the request does not say the watermark comparison; taking: refuse iff iat < sessions_not_before (strict — a token minted in the same second as the reset survives; sub-second takeover window accepted over refusing the victim's own fresh post-reset login) -> if wrong, the victim's first post-reset session bounces and the flow reads broken
- A10 [experience] covers: S2 · the request does not say where the emailed link lands; taking: the email carries `{dashboard_base_url}/reset?token=...` — THIS task ships the gateway API + email only; the console page is recorded residue for the next dashboard task -> if wrong (page expected here), scope must grow to apps/dashboard at freeze
- A11 [experience] covers: S1, S3 · the request does not say error surfaces; taking: reuse the problem+json catalog — new codes AUTH_RESET_INVALID / AUTH_RESET_EXPIRED (post-token-possession, R-sec-6 precedent makes distinct codes safe) and AUTH_UNAVAILABLE (503-class, revocation store down) -> if wrong, clients get novel shapes their handlers don't parse
- A13 [which] covers: S1 · the request does not say which login attempts consume limiter budget; taking: EVERY attempt consumes (success and failure alike — the fixed-window INCR runs pre-credential, before outcome is knowable) -> if wrong, counting only failures would require a post-credential write path and reopen the hasher-before-limiter ordering M1 forbids
- A14 [when] covers: S3 · the request does not say when revocation takes effect; taking: immediately at the logout 204 / reset commit — the guard reads the store per request, no cache, no propagation window · probe: the very next request with the revoked jti is refused (E9) -> if wrong, a "revoked" token that lingers for a TTL is exactly the theater R:STATELESS_REVOCATION_THEATER names
- A15 [absent] covers: S1 · the request does not say what a missing/blank email or password in the login body means; taking: pydantic schema validation refuses (422) before any limiter or credential work and consumes no budget -> if wrong, malformed bodies could burn a victim's per-email budget
- A16 [order] covers: S2 · the request does not say the confirm-validation order; taking: token validity first (invalid, then expired), password strength LAST and only on a live token — a weak password never consumes the token · probe: E7 (weak refused, same token then succeeds) -> if wrong, a typo'd password would burn the emailed token and strand the user
- A12 [which] covers: S3 · the request does not say which seam enforces revocation; taking: GetIdentityUseCase.execute — the shared identity resolution behind the admin auth deps — so every JWT-authenticated route inherits the check without per-router edits; Envoy jwt_authn stays stateless by design (edge diff = out of scope) · probe: a revoked token is refused on /me AND on one representative data route -> if wrong, some router resolves identity around the seam and revocation is partial

## PLAN
contract: >
  S1: login() in tenants/api/router.py gains the two limiter checks (actions "login_ip",
  "login_email"; knobs login_ip_rpm=20, login_email_rpm=10) ahead of LoginUseCase.execute —
  same shape as the personal-signup block in the same file. S2: new public router endpoints
  POST /admin/auth/password-reset {email} -> 202 {ok:true} (limiter actions
  "password_reset_ip" rpm=10, "password_reset_email" rpm=3) and POST
  /admin/auth/password-reset/confirm {token, new_password} -> 200 {ok:true}; new table
  password_reset_tokens(token_hash pk, user_id fk, expires_at, used_at) via the four
  manifests; email/application/password_reset_email_template.py through the existing
  dispatch seam. S3: jwt_service.issue() adds jti=uuid4 claim (decode required-set
  UNCHANGED); new table revoked_auth_sessions(jti pk, user_id, expires_at) + users column
  sessions_not_before (nullable timestamptz, additive migration); POST /admin/auth/logout
  (bearer) -> 204; GetIdentityUseCase.execute gains a bounded revocation guard
  (session_revocation_check_timeout_seconds=2.0) refusing revoked jti / stale iat,
  fail-closed AUTH_UNAVAILABLE on store failure. New error-catalog entries
  AUTH_RESET_INVALID, AUTH_RESET_EXPIRED, AUTH_UNAVAILABLE. All knobs get boot validators.
scope: tenants/api/router.py · tenants/api/deps.py · tenants/application/use_cases.py · tenants/infrastructure/{jwt_service.py, repository.py, + new reset/revocation stores} · email/application/ · core/{config.py, error_catalog.py} · main.py wiring · migrations · tests/auth_hardening/

## EDGES
- E1 a rate-limited login attempt performs zero hasher calls and zero DB credential reads (M1/A8)
- E2 login 429 body+headers byte-identical for registered vs unknown email at the per-email threshold (M2)
- E3 limiter Redis outage → login proceeds to the credential check (fail-open house rule)
- E4 reset-request for unknown email → 202, no token row, no email; for registered → 202 + exactly one email (A6)
- E5 reset token reused after a successful confirm → AUTH_RESET_INVALID (single-use, atomic mark)
- E6 reset token at/after expires_at → AUTH_RESET_EXPIRED (A5)
- E7 weak new_password at confirm → AUTH_PASSWORD_WEAK, token NOT consumed (user may retry with a strong one)
- E8 successful reset-confirm → the victim's pre-reset session token is refused at the identity seam (M4)
- E9 logout → that jti is refused server-side on the next request; a different live session of the same user is untouched (M5/A2)
- E10 legacy token without jti authenticates until the user watermark moves (A7)
- E11 revocation-store outage → bounded, typed AUTH_UNAVAILABLE refusal within the timeout — never a hang, never a silent allow (M6)
- E12 audit rows for reset-confirm and logout exist and contain no token/password substring (M6)

## CHECKS
- test_login_rate_limited_per_ip_before_hasher · covers: M1, A8, E1, R:UNTHROTTLED_LOGIN · N+1th same-IP attempt 429s with Retry-After; hasher spy shows zero calls on the limited attempt
- test_login_per_email_cap_is_uniform · covers: M1, M2, A4, E2, R:ENUMERATION_ORACLE, R:LOCKOUT_DOS · at the per-email threshold, registered and unknown emails get byte-identical 429s
- test_login_limiter_redis_down_fails_open · covers: E3 · limiter backend raising → login still reaches the credential check and succeeds with valid creds
- test_login_limiter_keys_use_trusted_ip · covers: M7, R:RAW_CLIENT_IP · a spoofed X-Forwarded-For beyond trusted_proxy_hops does not escape the per-IP bucket
- test_reset_request_uniform_202_no_oracle · covers: M2, A6, E4, R:ENUMERATION_ORACLE · unknown vs registered email → byte-identical 202; email dispatched only for registered
- test_reset_token_hashed_and_single_use · covers: M3, E5, R:PLAINTEXT_RESET_TOKEN · stored row holds sha256(token) not the token; a second confirm with the same token is AUTH_RESET_INVALID
- test_reset_token_expiry_boundary_refused · covers: A5, E6 · a token at exactly expires_at → AUTH_RESET_EXPIRED
- test_reset_weak_password_keeps_token_live · covers: A16, E7 · weak password → AUTH_PASSWORD_WEAK and the same token then succeeds with a strong password
- test_reset_confirm_revokes_prior_sessions · covers: M4, E8 · a session minted pre-reset is refused post-reset; login with the new password succeeds
- test_logout_is_server_side_revocation · covers: M5, A2, A14, E9, R:STATELESS_REVOCATION_THEATER · after logout the same bearer 401s on /me; a second session of the same user still works
- test_legacy_token_without_jti_still_authenticates · covers: A7, E10, R:WEAKENED_SIBLING · a token minted without jti (legacy shape) passes /me; decode required-claims set unchanged
- test_revoked_token_refused_on_data_route_too · covers: A12 · the revoked jti is refused on a representative non-/me admin route (seam, not per-router, enforcement)
- test_revocation_store_outage_fails_closed_bounded · covers: M6, E11 · a store double that hangs/raises → typed AUTH_UNAVAILABLE within session_revocation_check_timeout_seconds, never 200
- test_reset_and_logout_audited_without_secrets · covers: M6, E12 · audit events exist for both; metadata contains neither the token nor any password substring
red-first: every check MUST fail first.

## EVIDENCE
receipt: <runs/<n>.md>
gate: <PASS | RISK-ACCEPTED | HARD-STOP>

## LESSONS
- <lesson> -> add learn <lens>
