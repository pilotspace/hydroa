# TASK: Member-verified via a 6-digit mailbox code (GATEWAY security core): confirm the admin's signup mailbox → set additive `member_verified_at` (status stays 'pending', auto-join UNTOUCHED), business-only + generic-domain excluded

slug: member-verified-recognition · created: 2026-07-20 · stage: production
milestone: domain-onboarding-softening
component: gateway
sensitivity: security
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/domain_capture/infrastructure/orm.py:TenantDomainClaimRow` — the
  `tenant_domain_claims` table. FROZEN untouched: `verification_token`/`expires_at` (both NOT NULL,
  DNS-challenge-shaped), the ClaimStatus CheckConstraint `status IN ('pending','verified')`
  (`ck_tenant_domain_claims_status`), and BOTH indexes `uq_domain_claims_tenant_domain` +
  `uq_domain_claims_domain_verified (WHERE status='verified')`. ADD 4 additive columns (mirrors the
  domain-verify-notify precedent that already added `notify_requested_at`/`notified_at` here):
  `member_verified_at TIMESTAMPTZ NULL`, `member_verify_code_hash TEXT NULL`,
  `member_verify_code_expires_at TIMESTAMPTZ NULL`, `member_verify_attempt_count INT NOT NULL DEFAULT 0`.
- `apps/gateway/src/gateway/domain_capture/infrastructure/repository.py:SqlAlchemyDomainClaimRepository`
  — atomic-write precedents to mirror: `mark_verified` (`UPDATE … WHERE status='pending' RETURNING`),
  `request_notify` (idempotent `COALESCE`), `mark_notified` (atomic conditional claim
  `WHERE notified_at IS NULL RETURNING`), `get_own` (tenant-scoped point lookup). ADD (additive ports):
  `issue_member_verify_code(...)` (set hash+expiry, reset attempt_count=0), `claim_row_for_member_verify`
  (`SELECT … FOR UPDATE` the row, tenant-scoped, to serialize concurrent guesses), and the flip/increment
  writes described in §3. **`resolve_verified_tenant` (the auto-join gate — matches `status=='verified'`
  ONLY) MUST stay byte-untouched.**
- `apps/gateway/src/gateway/domain_capture/domain/entities.py:DomainClaim` (frozen slotted dataclass)
  — ADD one additive optional field `member_verified_at: datetime | None = None` (for list-response
  mapping). The 3 in-flight code columns stay INTERNAL to the repository — the secret hash never rides
  the domain entity or any API schema.
- `apps/gateway/src/gateway/domain_capture/domain/ports.py:DomainClaimRepository` (Protocol) — ADD the
  additive method signatures above (mirrors how notify methods were appended).
- `apps/gateway/src/gateway/domain_capture/api/schemas.py:DomainClaimListItem` — ADD additive
  `member_verified_at: datetime | None`; `to_list_item` maps it. This is the ONLY field the following
  dashboard task reads to derive its rung-aware seal.
- `apps/gateway/src/gateway/domain_capture/api/domain_claims_router.py` — OWNER-only, per-tenant
  rate-limited router (prefix `/admin/domain-claims`), `_get_owner_identity` + `_rate_limit` helpers to
  reuse verbatim. ADD 2 routes: `POST /{claim_id}/member-verify` (body `{code}`) +
  `POST /{claim_id}/member-verify/resend` (body `{}`).
- `apps/gateway/src/gateway/domain_capture/application/create_claim_use_case.py:CreateDomainClaimUseCase`
  — the token+7-day-expiry generation + `create_or_reissue` shape the issuance path reuses to seed the
  DNS challenge alongside the code.
- `apps/gateway/src/gateway/tenants/api/router.py:signup` (the `POST /admin/auth/signup` handler) — the
  NEW-tenant business branch (line ~108–125, after `use_case.execute` returns `(tenant_id, user_id)`,
  before `return SignupResponse(...)`) is the issuance hook. The join-existing-verified-tenant branch
  (line ~72–99) NEVER issues a code (that user auto-joins an already-verified domain). Signup already
  crosses into domain_capture (`get_domain_claim_resolver`) here — issuance orchestration is consistent.
- `apps/gateway/src/gateway/tenants/application/use_cases.py:SignupUseCase.execute` — `account_type`
  discriminates `'personal'` vs `'business'`; `email` is lower-cased. Business + non-generic domain is
  the issuance gate.
- `apps/gateway/src/gateway/domain_capture/domain/domain_validation.py:normalize_domain` — reuse to
  extract/normalize the domain from the signup email (`email.rsplit("@",1)[-1]` then normalize).
- EMAIL seam (transactional-email FROZEN @ v1, extend additively): `email/domain/ports.py:EmailSender.send`
  · `email/application/email_dispatch.py:send_email(sender, message)` (fail-OPEN boundary — never raises)
  · `email/application/domain_verified_email_template.py:render_domain_verified_email` (pure-function
  template to mirror) · `email/domain/entities.py:EmailMessage(to, subject, text_body, html_body)`. ADD:
  `render_member_verify_code_email(*, to, domain, code, origin)`.
- RECIPIENT resolution: `tenants/infrastructure/users_repository.py:UserRoleRepository.get_by_id_and_tenant`
  — the owner's own account email, server-derived. At signup the recipient IS the just-created owner's
  own signup address (`email`); at resend it is resolved from `created_by_user_id`, tenant-scoped.
- RATE LIMIT: `domain_capture/infrastructure/rate_limiter.py:DomainClaimRateLimiter.check(action, tenant_id, limit)`
  (fail-OPEN on Redis outage, per-tenant fixed window) — reuse with new actions `member_verify` +
  `member_verify_resend`. Knobs in `core/config.py` near `domain_claim_verify_rpm` (line ~1405).
- ERROR CATALOG: `core/error_catalog.py:ErrorSpec` + the domain-capture block (line ~726). Reuse
  `DOMAIN_CLAIM_NOT_FOUND` (404), `DOMAIN_CLAIM_NOT_PENDING` (409), `AUTH_FORBIDDEN_OWNER_REQUIRED` (403),
  `RATE_LIMITED` (429). ADD the new specs in §3.
- Alembic head to chain off: `0f1648b174a2` (`0f1648b174a2_domain_verify_notify.py`, the domain_capture
  branch head). See Issues/Risks R-drift for the multi-head state.

Context (working folder): `apps/gateway/src/gateway/domain_capture/` (orm/entities/ports/repository,
api/router+schemas, a new `application/member_verify_use_cases.py` + a new
`domain/public_email_domains.py` block-list module) · `email/application/` (one new template) ·
`core/error_catalog.py` + `core/config.py` (new ErrorSpecs + knobs) · `tenants/api/router.py` (one
issuance hook on the new-tenant business branch) · `migrations/versions/` (one additive migration).
Backend only — the dashboard code-entry screen + rung-aware seal is a SEPARATE following task that
CONSUMES this frozen contract (reads only `member_verified_at`).
Honors (patterns / conventions): hexagonal (domain Protocol ports; application use-cases; infra
adapters); additive nullable migration columns (domain-verify-notify's `0f1648b174a2` precedent on
this exact table); atomic single-statement writes (`mark_verified`/`mark_notified`); `SELECT … FOR
UPDATE` row-lock to serialize a security-critical counter (backend-architect discipline); fail-OPEN
email dispatch (`send_email`) + fail-OPEN rate limiter (`DomainClaimRateLimiter`); owner-only,
tenant-scoped admin routes via `_get_owner_identity`; server-derived recipient (never request-supplied,
the domain-verify-notify R-sec-1 rule); anti-enumeration indistinguishable NOT_FOUND (`get_own` returns
None for both unknown and cross-tenant).
Seams consulted: email seam (transactional-email FROZEN @ v1) · domain-capture claim table + repository
(FROZEN @ v1, + domain-verify-notify additive precedent) · rate limiter (fail-open) · signup flow
(account-type-discriminator + domain-capture auto-join, FROZEN).
Anchors the contract cites: TenantDomainClaimRow (+4 additive cols), DomainClaim (+member_verified_at),
DomainClaimRepository (+issue/claim-for-update/flip/increment methods), DomainClaimListItem
(+member_verified_at) + to_list_item, domain_claims_router (+2 routes), a new
IssueMemberVerifyCodeUseCase + VerifyMemberCodeUseCase + ResendMemberVerifyCodeUseCase,
render_member_verify_code_email, a new public_email_domains block-list module, normalize_domain,
DomainClaimRateLimiter, UserRoleRepository.get_by_id_and_tenant, resolve_verified_tenant (UNTOUCHED
guard), new ErrorSpecs.
Issues/Risks (→ feed §1):
- **R-sec-1 (brute force — a 6-digit code is only a 10^6 space):** without controls, a code is
  guessable. MITIGATIONS (each a Must): store the code as a KEYED hash only, never plaintext
  (member_verify_code_hash); ~15-min expiry; hard attempt-cap ≤5 wrong tries then invalidate (clear the
  hash → must resend); per-tenant rate-limit BOTH the verify endpoint and the resend endpoint (reuse
  DomainClaimRateLimiter); constant-time compare (`hmac.compare_digest`). The attempt-cap counter is the
  concurrency-critical value (see R-sec-6).
- **R-sec-2 (hash-at-rest strategy — a 10^6 space is trivially reversible under a plain digest):** a bare
  `sha256(code)` is preimage-recoverable for a DB-dump attacker (only 10^6 candidates). RECOMMEND
  HMAC-SHA256(code, server-side pepper secret) — without the secret a DB-only compromise cannot reverse
  the code. FALLBACK: reuse the already-wired argon2 `PasswordHasher` (no new secret, but a costly-yet-
  finite 10^6 offline brute is still possible). This is the freeze least-sure flag (§3, tagged
  [contract]).
- **R-sec-3 (confused-deputy / anti-spam — recipient & domain must be server-derived):** the code goes
  ONLY to the owner's OWN account email (signup address at issuance / `created_by_user_id` at resend),
  and the member-verified domain is the domain OF that same address — NEVER a request-supplied email or
  domain. The verify request body carries ONLY the 6-digit `code`, never an email or domain. Mirrors
  domain-verify-notify's R-sec-1 verbatim.
- **R-sec-4 (must NOT leak into the DNS auto-join gate):** `member_verified_at` is a SEPARATE additive
  column; a member-verified row keeps `status='pending'`, so `resolve_verified_tenant` (matches
  `status='verified'` only) NEVER resolves it for auto-join. No member-verify path writes `status`,
  `verified_at`, or touches the partial-unique-index. Auto-join stays DNS-TXT-only.
- **R-sec-5 (generic/public-domain + personal-account exclusion):** member-verify @gmail.com would let
  one user invite-by-domain the entire gmail population. A curated static block-list module
  (`public_email_domains.py`, extensible) refuses generic providers, and personal accounts
  (account_type='personal') are refused — issuance is SKIPPED at signup (silent, since signup must 201)
  and the resend endpoint returns a TYPED error; never a silent grant.
- **R-sec-6 (attempt-cap under concurrent guesses):** N parallel wrong guesses must not each slip under
  the cap. MITIGATION: the verify use-case reads the row under `SELECT … FOR UPDATE` (row-lock,
  tenant-scoped) within one transaction, then compares + increments + (on cap) clears — so concurrent
  attempts on the same claim serialize and the cap holds EXACTLY. The success flip is likewise a guarded
  single write. This is the #1 thing the ≥2 adversarial verifies must probe.
- **R-fail-open (issuance must never break signup):** at signup the code issuance + email is best-effort
  and fail-OPEN — any failure (email down, another tenant already verified the domain, DB hiccup) is
  logged and swallowed; signup STILL returns 201. Issuance is a convenience unlock, not a signup gate.
- **R-drift (known casualties):** `apps/gateway/tests/migrations/test_migrations.py` carries a
  column/table manifest for `tenant_domain_claims`; +4 columns trips it → SANCTIONED additive manifest
  edit (per [[commercial-self-serve-milestone]]). Also `alembic heads` currently shows 6 heads
  (a pre-existing multi-head merge state) — the new migration chains off `0f1648b174a2`; do NOT
  attempt to merge unrelated heads. Sweep exact-shape consumers of DomainClaim / the claim list response
  for the new `member_verified_at` field.
Related intent: milestone `domain-onboarding-softening` rung-1 ("member-verified"), [[domain-onboarding-progressive-trust]].
WHY: signup does not confirm the mailbox today, so a 6-digit code emailed to the owner's own signup
address IS the mailbox proof. It grants ONLY rung-1 member-verified (unlocks invite-by-domain, a later
task) — never the DNS-only stranger auto-join. GLOSSARY: introduces "member-verified" as a trust rung
distinct from "verified" (DNS-proven).
Ground SHA: 04ed333 (cite symbols, not bare line numbers; any line ref is "as of" this commit)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Member-verified via a 6-digit mailbox code — a business admin proves control of their signup
MAILBOX by entering a 6-digit code emailed to their own signup address; on success the domain's claim row
gains `member_verified_at` (status stays 'pending', auto-join UNTOUCHED). Rung-1 trust; unlocks
invite-by-domain (a later task), NEVER stranger auto-join.
Framings weighed:
- **Additive code-columns on tenant_domain_claims + issue-at-signup + owner-only verify/resend endpoints
  (CHOSEN)** — mirrors the frozen domain-verify-notify precedent (which put notify columns on this exact
  table); one atomic write path; `member_verified_at` lives naturally beside `verified_at` and the list
  response reads it trivially; one-row-per-(tenant,domain) uniqueness already gives single-in-flight-code
  semantics; the 3 code columns are cleared to NULL on success/expiry so no long-lived secret persists.
- Separate `domain_member_verify_codes` table (rejected) — cleaner ephemeral/durable split, BUT
  `member_verified_at` STILL must live on the claim (durable rung state the list reads), so a table adds
  a second write surface, a second migration/manifest, an FK and a repository for marginal benefit;
  diverges from the just-set precedent. The code columns nulling on success already bounds the secret's life.
- Issue the code on-demand via an owner-authenticated endpoint instead of at signup (rejected — the
  decision is Tin-LOCKED to signup-issuance): signup is where the mailbox domain is unambiguously
  server-derived from the signup email; the "walk away, get the code" convenience is the point. (A resend
  endpoint still exists for a lost/expired code.)

Must:
<must>
  - M1 Issue-at-signup: on a NEW-tenant BUSINESS signup whose email domain is non-generic, after the
    owner is created, ensure a `tenant_domain_claims` row for that domain (with a fresh DNS challenge,
    reusing the CreateDomainClaimUseCase token+expiry shape), generate a 6-digit code, store its KEYED
    hash + a ~15-min `member_verify_code_expires_at`, reset `member_verify_attempt_count=0`, and email the
    code to the owner's OWN signup address via `render_member_verify_code_email` + fail-open `send_email`.
  - M2 Signup unaffected: issuance is best-effort and fail-OPEN — any failure (email down, DB hiccup, or
    another tenant already holds a verified claim on the domain) is logged and swallowed; signup STILL
    returns 201 with the same SignupResponse. No new signup failure mode is introduced (R-fail-open).
  - M3 Verify endpoint: `POST /admin/domain-claims/{claim_id}/member-verify` body `{code}` (OWNER-only,
    tenant-scoped, rate-limited per tenant). Reads the claim row under `SELECT … FOR UPDATE`; if a valid
    in-flight code exists, is not expired, is under the attempt-cap, and the submitted code matches (via
    constant-time `hmac.compare_digest` on the keyed hash), sets `member_verified_at=now()`, clears the 3
    code columns (single-use), and returns the updated claim (DomainClaimListItem). `status` stays 'pending'.
  - M4 Wrong-code attempt-cap: a mismatch atomically increments `member_verify_attempt_count`; on the 5th
    wrong try the code is INVALIDATED (hash cleared) — further attempts require a resend. Concurrent
    guesses on the same claim serialize under the row-lock so the cap holds exactly (R-sec-6).
  - M5 Resend endpoint: `POST /admin/domain-claims/{claim_id}/member-verify/resend` body `{}` (OWNER-only,
    tenant-scoped, rate-limited per tenant, distinct action) — for a pending, non-generic-domain,
    business claim, issues a FRESH code (new hash + expiry, attempt_count reset to 0) and re-emails it to
    the owner's server-derived account address. Never accepts a recipient/domain from the request.
  - M6 Server-derived recipient & domain (R-sec-3): the code recipient is ALWAYS the AUTHENTICATED CALLER's
    own account email (the signup address at M1; the authenticated owner's account email at M5) and the
    member-verified domain is the domain OF that address — never any request-supplied email or domain. The
    verify request body carries ONLY the 6-digit `code`.
  - M6b Mailbox-covers-the-domain gate (R-sec-3b — the code only proves the mailbox it was sent to):
    member-verify AND resend REQUIRE the claim's `domain` to equal the authenticated caller's OWN email
    domain (`normalize_domain(caller_email.split("@")[-1]) == claim.domain`). A mismatch is REFUSED — a code
    delivered to `owner@acme.com` proves control of `acme.com` ONLY, never of some other domain the tenant
    added a claim for; without this gate an owner could member-verify ANY domain by receiving a code in their
    own inbox. (At signup this holds by construction — the domain IS derived from the signup email.)
  - M7 Auto-join UNTOUCHED (R-sec-4): `member_verified_at` is a separate additive column; a member-
    verified row keeps `status='pending'`. `resolve_verified_tenant`, the ClaimStatus enum + its
    CheckConstraint, and both unique indexes are byte-unchanged — no member-verify path writes `status`,
    `verified_at`, or the partial-unique-index. Auto-join stays DNS-TXT-only.
  - M8 Keyed-hash single-use, brute-resistant (R-sec-1/R-sec-2): the code is stored ONLY as a keyed hash
    (never plaintext at rest), compared in constant time, expires ~15 min, capped at ≤5 attempts, and is
    single-use (cleared on success/expiry/cap so a verified/expired/exhausted code cannot be replayed).
  - M9 List response exposes rung: `GET /admin/domain-claims` additively exposes `member_verified_at` per
    item so the following dashboard task can derive the rung-aware seal. No other new field is exposed;
    the code hash/expiry/attempt-count NEVER appear in any API response.
</must>
Reject:
<reject>
  - R1 member-verify / resend on an unknown claim OR another tenant's claim -> "ERR_DOMAIN_CLAIM_NOT_FOUND"
    (404; claim unchanged; deliberately indistinguishable, mirrors verify — anti-enumeration).
  - R2 A non-owner calling member-verify / resend -> "ERR_AUTH_FORBIDDEN" (403; no state change) — server gate.
  - R3 Wrong 6-digit code (still under the cap) -> "ERR_MEMBER_VERIFY_CODE_INVALID" (400; attempt_count
    incremented; member_verified_at unchanged; status unchanged). [NEW ErrorSpec]
  - R4 A submitted code after the in-flight code has expired -> "ERR_MEMBER_VERIFY_CODE_EXPIRED" (410; the
    code is cleared; must resend; member_verified_at unchanged). [NEW ErrorSpec]
  - R5 A submitted code after the attempt-cap is reached (code already invalidated) ->
    "ERR_MEMBER_VERIFY_TOO_MANY_ATTEMPTS" (429; member_verified_at unchanged; must resend). [NEW ErrorSpec]
  - R6 Resend (or member-verify) on a generic/public email domain -> "ERR_DOMAIN_GENERIC" (422; no code
    issued; no state change). [NEW ErrorSpec]
  - R7 Resend on a personal account (account_type='personal') -> "ERR_MEMBER_VERIFY_NOT_ELIGIBLE" (403; no
    code issued; no state change). [NEW ErrorSpec]
  - R8 member-verify / resend on an already-DNS-verified claim (status='verified') ->
    "ERR_DOMAIN_CLAIM_NOT_PENDING" (409; the stronger rung already holds; no state change). [reuse]
  - R9 member-verify / resend beyond the per-tenant rate limit -> "ERR_RATE_LIMITED" (429; no state
    change). [reuse]
  - R10 member-verify / resend on a claim whose `domain` != the authenticated caller's own email domain
    (M6b) -> "ERR_MEMBER_VERIFY_DOMAIN_MISMATCH" (403; no code issued/accepted; no state change) — the
    caller's mailbox proof cannot cover a different domain. [NEW ErrorSpec]
</reject>
After:
<after>
  - A business admin who signs up with a company email receives a 6-digit code at that address; entering
    it within ~15 min sets `member_verified_at` on the domain's claim while `status` stays 'pending'; the
    claim appears in `GET /admin/domain-claims` with `member_verified_at` populated.
  - A wrong code up to 4 times returns 400 and increments the counter; the 5th invalidates the code; a
    resend issues a fresh code and resets the counter.
  - The DNS-TXT proof, `resolve_verified_tenant`, the ClaimStatus enum + CheckConstraint + both unique
    indexes, and AUTO-JOIN are all UNCHANGED — a grep shows only additive columns/methods/routes/template.
  - No code hash, expiry, or attempt-count ever appears in an API response; the code recipient/domain is
    never request-supplied.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The hash-at-rest strategy for a low-entropy (10^6) code — HMAC-SHA256(code, server-side pepper)
    (RECOMMENDED) vs reuse the argon2 PasswordHasher (no new secret). Lowest confidence because it trades
    a NEW required-secret config surface (and a fail-closed concern if the secret is absent) against
    at-rest brute-force strength: a bare digest is trivially reversible over 10^6, and even argon2 is a
    finite offline brute for a DB-dump attacker, whereas a pepper makes DB-only compromise useless. If
    wrong (pepper is operationally undesirable): cost = fall back to argon2 PasswordHasher — the online
    controls (expiry + attempt-cap + rate-limit) already bound the primary (online) threat, so the
    fallback is acceptable. Surfaced as the freeze flag [contract].
  - [ ] ~15-min expiry + ≤5-attempt cap are the right online-brute bounds — ranked next; 5 tries over
    10^6 with a 15-min window and per-tenant rate-limit is a ~negligible online-guess probability. Confirm
    the exact numbers with Tin at freeze (knobs, defaulted).
  - [ ] Issuing at the signup router's new-tenant business branch (not inside SignupUseCase) keeps the
    tenants use-case pure — confirmed: the router already crosses into domain_capture
    (get_domain_claim_resolver) on this exact handler, so an issuance orchestration there is consistent
    and keeps SignupUseCase free of email/domain-capture deps.
  - [ ] `member_verified_at` never feeds auto-join — confirmed by reading `resolve_verified_tenant`
    (matches status='verified' ONLY) and keeping status='pending' on every member-verify path (M7).
  - [ ] Recipient is always server-derived — confirmed: signup address at M1, `created_by_user_id` via
    UserRoleRepository.get_by_id_and_tenant at M5; the API surface has no email/domain field (R-sec-3).
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Business signup with a company domain issues a code to the signup address   # M1
  Given public signup is enabled and no tenant has verified example-co.com
  When a business account signs up as owner@example-co.com
  Then a pending tenant_domain_claims row for example-co.com exists with a fresh DNS challenge
  And member_verify_code_hash + member_verify_code_expires_at (~15 min) are set, attempt_count is 0
  And exactly one member-verify code email is sent to owner@example-co.com
  And the recipient address was derived from the signup email, never from any extra request field

Scenario: Signup still returns 201 when code issuance fails   # M2
  Given the email sender is unavailable (or the domain is already verified by another tenant)
  When a business account signs up as owner@example-co.com
  Then signup returns 201 with the normal SignupResponse
  And the issuance failure was swallowed and logged, introducing no new signup error

Scenario: Owner enters the correct code and becomes member-verified   # M3,M6,M8
  Given a pending claim for example-co.com with an in-flight, unexpired code for the caller
  When the owner POSTs member-verify with the correct 6-digit code
  Then member_verified_at is set to now() and the updated claim is returned
  And status stays 'pending' and verified_at stays null
  And the 3 code columns are cleared so the code cannot be replayed

Scenario: A wrong code is rejected and increments the attempt counter   # R3,M4
  Given a pending claim with an in-flight code and attempt_count 0
  When the owner POSTs member-verify with a wrong 6-digit code
  Then the response is 400 ERR_MEMBER_VERIFY_CODE_INVALID
  And member_verify_attempt_count is 1 and member_verified_at stays null and status stays 'pending'

Scenario: The 5th wrong code invalidates the code   # R5,M4
  Given a pending claim whose in-flight code has already had 4 wrong attempts
  When the owner POSTs member-verify with a 5th wrong code
  Then the response is 429 ERR_MEMBER_VERIFY_TOO_MANY_ATTEMPTS
  And the code hash is cleared (must resend) and member_verified_at stays null and status stays 'pending'

Scenario: An expired code is rejected   # R4
  Given a pending claim whose in-flight code's member_verify_code_expires_at has passed
  When the owner POSTs member-verify with that code
  Then the response is 410 ERR_MEMBER_VERIFY_CODE_EXPIRED
  And the code is cleared and member_verified_at stays null and status stays 'pending'

Scenario: Concurrent wrong guesses cannot exceed the attempt cap   # M4,R-sec-6
  Given a pending claim with an in-flight code and attempt_count 0
  When six wrong-code member-verify requests arrive concurrently for the same claim
  Then the row-lock serializes them, at most 5 increments are recorded, and the code is invalidated once
  And member_verified_at stays null and status stays 'pending' throughout

Scenario: Resend issues a fresh code and resets the counter   # M5
  Given a pending, business, non-generic claim whose code had 3 wrong attempts
  When the owner POSTs member-verify/resend
  Then a fresh code hash + expiry are stored, attempt_count is reset to 0
  And exactly one new code email is sent to the owner's server-derived account address

Scenario: A member-verified row is never resolved for auto-join   # M7,R-sec-4
  Given a claim for example-co.com with member_verified_at set and status='pending'
  When resolve_verified_tenant('example-co.com') runs
  Then it returns None (no verified claim) and stranger auto-join does not fire
  And the ClaimStatus enum, CheckConstraint, and both unique indexes are unchanged

Scenario: The list response exposes member_verified_at only   # M9
  Given a claim that is member-verified
  When the owner GETs /admin/domain-claims
  Then the item includes member_verified_at
  And no code hash, code expiry, or attempt-count field appears in the response

Scenario: member-verify on another tenant's claim is not found   # R1
  Given a claim owned by a different tenant
  When the caller POSTs member-verify for it
  Then the response is 404 ERR_DOMAIN_CLAIM_NOT_FOUND
  And that claim is unchanged

Scenario: A non-owner cannot member-verify or resend   # R2
  Given a caller who is not an owner
  When they POST member-verify
  Then the response is 403 ERR_AUTH_FORBIDDEN
  And nothing changes

Scenario: Resend on a generic email domain is refused   # R6
  Given a claim whose domain is gmail.com
  When the owner POSTs member-verify/resend
  Then the response is 422 ERR_DOMAIN_GENERIC
  And no code is issued and nothing changes

Scenario: Resend on a personal account is refused   # R7
  Given a personal (account_type='personal') account's owner
  When they POST member-verify/resend
  Then the response is 403 ERR_MEMBER_VERIFY_NOT_ELIGIBLE
  And no code is issued and nothing changes

Scenario: member-verify on an already-DNS-verified claim is rejected   # R8
  Given a claim with status='verified'
  When the owner POSTs member-verify or resend
  Then the response is 409 ERR_DOMAIN_CLAIM_NOT_PENDING
  And nothing changes

Scenario: member-verify beyond the per-tenant rate limit is rejected   # R9
  Given a tenant that has exceeded the member-verify rate limit
  When the owner POSTs member-verify
  Then the response is 429 ERR_RATE_LIMITED
  And nothing changes

Scenario: member-verify on a claim whose domain is not the caller's own email domain is refused   # R10,M6b
  Given owner sam@acme.com and a pending claim for a different domain acme.io
  When sam POSTs member-verify (or resend) for the acme.io claim
  Then the response is 403 ERR_MEMBER_VERIFY_DOMAIN_MISMATCH
  And no code is issued/accepted, member_verified_at stays null, and status stays 'pending'
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /admin/domain-claims/{claim_id}/member-verify   body: { code: string }   (OWNER-only; body is ONLY the 6-digit code — NO email/domain field)
  200 -> DomainClaimListItem (incl. member_verified_at set; status stays "pending")
  400 -> { code: "ERR_MEMBER_VERIFY_CODE_INVALID" }          # wrong code, still under cap [NEW ErrorSpec]
  410 -> { code: "ERR_MEMBER_VERIFY_CODE_EXPIRED" }          # in-flight code expired; resend [NEW ErrorSpec]
  429 -> { code: "ERR_MEMBER_VERIFY_TOO_MANY_ATTEMPTS" }     # attempt-cap reached; code invalidated [NEW ErrorSpec]
  404 -> { code: "ERR_DOMAIN_CLAIM_NOT_FOUND" }              # unknown / other tenant (indistinguishable) [reuse]
  409 -> { code: "ERR_DOMAIN_CLAIM_NOT_PENDING" }            # already DNS-verified [reuse]
  403 -> { code: "ERR_AUTH_FORBIDDEN" }                      # non-owner [reuse]
  403 -> { code: "ERR_MEMBER_VERIFY_DOMAIN_MISMATCH" }       # claim.domain != caller's own email domain (M6b) [NEW ErrorSpec]
  429 -> { code: "ERR_RATE_LIMITED" }                        # per-tenant limit (Retry-After) [reuse]

POST /admin/domain-claims/{claim_id}/member-verify/resend   body: {}   (OWNER-only; NO email/domain field)
  200 -> DomainClaimListItem (a fresh code issued + emailed to the CALLER's own account email; attempt_count reset to 0)
  404 -> { code: "ERR_DOMAIN_CLAIM_NOT_FOUND" }              # unknown / other tenant [reuse]
  409 -> { code: "ERR_DOMAIN_CLAIM_NOT_PENDING" }            # already DNS-verified [reuse]
  422 -> { code: "ERR_DOMAIN_GENERIC" }                      # generic/public email domain [NEW ErrorSpec]
  403 -> { code: "ERR_MEMBER_VERIFY_NOT_ELIGIBLE" }          # personal account [NEW ErrorSpec]
  403 -> { code: "ERR_MEMBER_VERIFY_DOMAIN_MISMATCH" }       # claim.domain != caller's own email domain (M6b) [NEW ErrorSpec]
  403 -> { code: "ERR_AUTH_FORBIDDEN" }                      # non-owner [reuse]
  429 -> { code: "ERR_RATE_LIMITED" }                        # per-tenant limit (Retry-After) [reuse]

GET /admin/domain-claims  (EXISTING list) — ADDITIVELY exposes per item:
  + member_verified_at: datetime|null      (the ONLY new field; code hash/expiry/attempt-count are NEVER exposed)

SIGNUP HOOK (no new endpoint) — POST /admin/auth/signup, NEW-tenant BUSINESS branch only:
  after the owner is created, IFF account_type=='business' AND the signup-email domain is non-generic:
    IssueMemberVerifyCodeUseCase.execute(tenant_id, domain, owner_user_id, owner_email)
      → create_or_reissue a pending claim (fresh DNS challenge) → generate 6-digit code → store keyed
        hash + ~15-min expiry, attempt_count=0 → send_email(owner_email, render_member_verify_code_email)
    WRAPPED best-effort/fail-OPEN: any exception is logged + swallowed; signup STILL returns 201 (M2).
  The join-existing-verified-tenant branch NEVER issues a code.

Schema (additive; FROZEN parts UNTOUCHED):
  tenant_domain_claims
    + member_verified_at TIMESTAMPTZ NULL
    + member_verify_code_hash TEXT NULL            (keyed hash ONLY — never plaintext)
    + member_verify_code_expires_at TIMESTAMPTZ NULL
    + member_verify_attempt_count INT NOT NULL DEFAULT 0
    (UNCHANGED: verification_token/expires_at NOT NULL · ClaimStatus CheckConstraint pending|verified
     · uq_domain_claims_tenant_domain · uq_domain_claims_domain_verified · resolve_verified_tenant)
    migration chains off head 0f1648b174a2 (additive only; nullable + defaulted so every existing row is
    byte-identical). SANCTIONED: reconcile the tenant_domain_claims manifest in tests/migrations/test_migrations.py.
  DomainClaim entity  + member_verified_at: datetime | None = None   (code fields stay repository-internal)
  DomainClaimListItem + member_verified_at: datetime | None ; to_list_item maps it
  repository (additive ports):
    issue_member_verify_code(claim_id, tenant_id, code_hash, expires_at) -> DomainClaim
      (set hash+expiry, attempt_count=0; tenant-scoped)
    load_member_verify_row_for_update(claim_id, tenant_id)  (SELECT … FOR UPDATE — serializes concurrent
      guesses so the cap holds exactly; returns hash/expiry/attempt_count/status/member_verified_at)
    mark_member_verified(claim_id) -> DomainClaim   (SET member_verified_at=now(), clear the 3 code
      columns; status/verified_at/indexes UNTOUCHED)
    bump_member_verify_attempt(claim_id, *, invalidate: bool) -> int   (atomic +1; when the new count
      reaches the cap, also clears the hash — single-use invalidation)
  email: render_member_verify_code_email(*, to, domain, code, origin) -> EmailMessage   (additive pure template)
  domain: public_email_domains.py — is_public_email_domain(domain) -> bool over a curated, extensible
    frozenset (gmail/googlemail/outlook/hotmail/live/yahoo/icloud/me/proton/protonmail/aol/gmx/mail/
    yandex/zoho/…); reused at signup issuance (skip) + resend (ERR_DOMAIN_GENERIC).
  config: member_verify_code_ttl_seconds (~900) · member_verify_max_attempts (~5) · member_verify_rpm ·
    member_verify_resend_rpm (positive-int validated, mirrors domain_claim_*_rpm) ·
    [recommended] member_verify_code_hmac_secret (the pepper — see SAFETY RULES / freeze flag)
```

SAFETY RULES (security task — binding):
- KEYED-HASH-AT-REST: the code is stored ONLY as a keyed hash (RECOMMENDED HMAC-SHA256(code, server pepper);
  FALLBACK argon2 PasswordHasher) — never plaintext. Compared with constant-time `hmac.compare_digest`.
  Single-use: the 3 code columns are cleared on success, expiry, and attempt-cap, so a verified/expired/
  exhausted code cannot be replayed.
- BRUTE-FORCE BOUND: ~15-min expiry + ≤5-attempt hard cap (then invalidate) + per-tenant rate-limit on
  BOTH member-verify and resend (fail-open DomainClaimRateLimiter). The attempt counter is read/incremented
  under `SELECT … FOR UPDATE`, so concurrent guesses on one claim serialize and the cap is exact.
- SERVER-DERIVED RECIPIENT & DOMAIN: the code recipient is ALWAYS the AUTHENTICATED CALLER's own account
  email (signup address at issuance / the authenticated owner's account email at resend) and the member-
  verified domain is the domain OF that address — the API carries no email/domain field; the verify body is
  only the 6-digit code.
- MAILBOX-COVERS-THE-DOMAIN (M6b): member-verify/resend REQUIRE `claim.domain == normalize_domain(caller's
  own email domain)`. A code proves control of the mailbox it was sent to and NOTHING else — so it can only
  member-verify the domain of that mailbox. A mismatch → 403 ERR_MEMBER_VERIFY_DOMAIN_MISMATCH. (Without this,
  an owner could member-verify any domain the tenant added a claim for by receiving a code in their own inbox.)
- AUTO-JOIN UNTOUCHED: member_verified_at is a separate additive column; a member-verified row keeps
  status='pending'. No member-verify path writes status/verified_at or the partial-unique-index;
  resolve_verified_tenant is byte-unchanged, so auto-join stays DNS-TXT-only.
- GENERIC/PERSONAL EXCLUSION: generic/public domains and personal accounts NEVER get member-verified —
  issuance is skipped at signup (silent, signup must 201) and refused with a typed error at resend; never
  a silent grant.
- FAIL-OPEN ISSUANCE: signup-time code issuance is best-effort — any failure is logged + swallowed; signup
  still returns 201.

Glossary deltas: member-verified: a rung-1 trust marker (`member_verified_at` on a domain claim) proving
the admin controls their signup MAILBOX (6-digit code), distinct from "verified" (DNS-TXT-proven domain
control, status='verified'). member-verified unlocks invite-by-domain; it NEVER enables stranger auto-join.
Status: FROZEN @ v1 — approved by Tin Dang, 2026-07-20. DECIDED at freeze: hash-at-rest = Option A —
HMAC-SHA256(code, key = HMAC(jwt_secret, "member-verify-code")), constant-time `hmac.compare_digest`; NO new
config secret (reuses the existing required, dev-default-guarded `jwt_secret` with domain separation).
Numbers ratified: code TTL ~900s · attempt-cap ≤5 · per-tenant rate-limit on both member-verify + resend.
Scope ratified: GATEWAY backend security core ONLY; the dashboard code-entry screen + rung-aware climb seal
is a SEPARATE following task (its own UDD pass — the 6-digit code adds a screen the original wireframe lacked),
taking the milestone 5→6 tasks; invite-by-domain stays last. My review ADDED M6b (mailbox-covers-the-domain
gate) + R10/ERR_MEMBER_VERIFY_DOMAIN_MISMATCH before freeze — a code proves only the mailbox it was sent to.
Reported: yes — freeze report rendered 2026-07-20 (banner/summary/flag/scope), Tin picked hash Option A.
Least-sure flag surfaced at freeze: [contract] the hash-at-rest strategy for a low-entropy (10^6) 6-digit
code — RESOLVED to Option A (above). KEY INSIGHT (orchestrator review): the code is EPHEMERAL — ~15-min expiry, single-use, cleared on
success — so a DB-dump attacker who reverses a hash almost always recovers an ALREADY-EXPIRED code. The
online controls (expiry + ≤5-attempt cap + per-tenant rate-limit + SELECT…FOR UPDATE serialization) bound
the real threat; at-rest strength is near-moot. Three options, in the orchestrator's recommended order:
  (A · RECOMMENDED) HMAC-SHA256(code, existing `jwt_secret` with domain-separation, e.g. key =
     HMAC(jwt_secret,"member-verify-code")) — pepper-strength with NO new config surface (jwt_secret is
     already a required, dev-default-guarded secret). Mild key-reuse, mitigated by domain separation.
  (B) HMAC-SHA256(code, a NEW dedicated `member_verify_code_hmac_secret`) — cleanest key hygiene, but a new
     required secret + a fail-closed-if-absent decision (ops burden).
  (C) argon2 `Argon2PasswordHasher` (already wired) — no secret at all; simplest; the ephemeral-code insight
     makes its "finite offline brute" practically irrelevant.
Cost if the recommendation is wrong: swap the one hashing helper (an internal, testable seam) — no API/schema
change. This — plus the attempt-cap-under-concurrency serialization (R-sec-6, SELECT … FOR UPDATE) and the
M6b mailbox-covers-the-domain gate — is what the ≥2 adversarial verifies must probe hardest. Tin to ratify
the hash choice (A/B/C) + the TTL(~900s)/attempt(≤5)/rate numbers at freeze.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (member_verify_use_cases + the new repository methods + router routes)
Plan (one test per scenario, asserting behavior not internals — mirrors `test_domain_verify_notify.py`):
<test_plan>
  - test_business_signup_issues_code_to_signup_address: arrange business signup owner@example-co.com, no domain verified / act POST /admin/auth/signup / assert 201 + a pending claim row exists (fresh DNS challenge) + member_verify_code_hash & _expires_at (~15min) set & attempt_count==0 + EXACTLY ONE member-verify email captured to owner@example-co.com + recipient came from signup email (no request field) · covers M1,M6
  - test_signup_returns_201_when_issuance_fails: arrange email sender raises (or domain already verified by another tenant) / act signup / assert 201 normal SignupResponse + issuance failure swallowed (no new signup error) · covers M2,R-fail-open
  - test_correct_code_sets_member_verified: arrange pending claim + in-flight unexpired code for the caller's own domain / act POST member-verify {correct code} / assert 200 DomainClaimListItem with member_verified_at set + status stays 'pending' + verified_at null + all 3 code columns cleared (replay of same code → 410/400) · covers M3,M6,M8
  - test_wrong_code_increments_attempt: arrange pending claim, attempt_count 0 / act POST member-verify {wrong code} / assert 400 ERR_MEMBER_VERIFY_CODE_INVALID + attempt_count==1 + member_verified_at null + status 'pending' · covers R3,M4
  - test_fifth_wrong_code_invalidates: arrange in-flight code already 4 wrong attempts / act 5th wrong POST / assert 429 ERR_MEMBER_VERIFY_TOO_MANY_ATTEMPTS + hash cleared (subsequent correct-code attempt also fails, must resend) + member_verified_at null + status 'pending' · covers R5,M4
  - test_expired_code_rejected: arrange in-flight code whose _expires_at has passed / act POST member-verify {that code} / assert 410 ERR_MEMBER_VERIFY_CODE_EXPIRED + code cleared + member_verified_at null + status 'pending' · covers R4,M8
  - test_concurrent_wrong_guesses_cap_holds: arrange pending claim, attempt_count 0 / act 6 concurrent wrong POSTs for the same claim (asyncio.gather) / assert final attempt_count<=5, code invalidated exactly once, no more than 5 increments recorded, member_verified_at null + status 'pending' throughout (SELECT…FOR UPDATE serialization) · covers M4,R-sec-6
  - test_resend_issues_fresh_code_resets_counter: arrange pending business non-generic claim with 3 wrong attempts / act POST member-verify/resend / assert 200 + fresh hash+expiry stored + attempt_count==0 + EXACTLY ONE new email to the owner's server-derived account address · covers M5,M6
  - test_member_verified_row_never_auto_joins: arrange claim with member_verified_at set + status='pending' / act resolve_verified_tenant('example-co.com') / assert returns None (no verified claim) + auto-join does not fire + ClaimStatus enum/CheckConstraint/both unique indexes unchanged · covers M7,R-sec-4
  - test_list_exposes_member_verified_at_only: arrange a member-verified claim / act GET /admin/domain-claims / assert item includes member_verified_at + NO member_verify_code_hash / _expires_at / _attempt_count key anywhere in the response body · covers M9
  - test_member_verify_other_tenant_not_found: arrange claim owned by a different tenant / act caller POSTs member-verify for it / assert 404 ERR_DOMAIN_CLAIM_NOT_FOUND (indistinguishable from unknown) + that claim unchanged · covers R1
  - test_non_owner_forbidden: arrange caller who is not an owner / act POST member-verify (and resend) / assert 403 ERR_AUTH_FORBIDDEN + no state change · covers R2
  - test_resend_generic_domain_refused: arrange claim whose domain is gmail.com / act POST member-verify/resend / assert 422 ERR_DOMAIN_GENERIC + no code issued + nothing changes · covers R6,R-sec-5
  - test_resend_personal_account_refused: arrange personal (account_type='personal') owner / act POST member-verify/resend / assert 403 ERR_MEMBER_VERIFY_NOT_ELIGIBLE + no code issued + nothing changes · covers R7,R-sec-5
  - test_already_dns_verified_rejected: arrange claim status='verified' / act POST member-verify (and resend) / assert 409 ERR_DOMAIN_CLAIM_NOT_PENDING + nothing changes · covers R8
  - test_rate_limited: arrange tenant over the member-verify limit (stub DomainClaimRateLimiter.check → False) / act POST member-verify / assert 429 ERR_RATE_LIMITED + no state change · covers R9
  - test_domain_mismatch_refused: arrange owner sam@acme.com + pending claim for a DIFFERENT domain acme.io / act sam POSTs member-verify (and resend) for the acme.io claim / assert 403 ERR_MEMBER_VERIFY_DOMAIN_MISMATCH + no code issued/accepted + member_verified_at null + status 'pending' · covers R10,M6b
  - test_code_stored_only_as_keyed_hash: arrange issue a code / assert the persisted member_verify_code_hash != the plaintext code AND == HMAC-SHA256(code, HMAC(jwt_secret,'member-verify-code')) (keyed-hash-at-rest, constant-time seam) · covers M8,R-sec-2
</test_plan>

Tests live in: `apps/gateway/tests/domain_capture/test_member_verified_recognition.py` (base DB MUST be gateway_test; run with `env -u GATEWAY_TEST_DATABASE_URL`) · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/domain_capture/` `apps/gateway/src/gateway/email/` `apps/gateway/src/gateway/core/error_catalog.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/tenants/api/router.py` `apps/gateway/migrations/versions/` `apps/gateway/tests/domain_capture/` `apps/gateway/tests/migrations/test_migrations.py`
Strategy (ordered batches): 1. Additive migration off head `0f1648b174a2` (+4 nullable/defaulted cols on tenant_domain_claims) + reconcile the SANCTIONED manifest in `tests/migrations/test_migrations.py`. 2. `domain/public_email_domains.py` block-list (`is_public_email_domain`) + `domain/entities.py` (+member_verified_at) + `domain/ports.py` (+4 repo method sigs). 3. ORM (+4 cols) + repository adapters (issue_member_verify_code · load_member_verify_row_for_update via `SELECT … FOR UPDATE` · mark_member_verified · bump_member_verify_attempt) — mirror mark_verified/mark_notified atomic-write shapes; `resolve_verified_tenant` byte-untouched. 4. `core/config.py` knobs (ttl 900 / max_attempts 5 / member_verify_rpm / member_verify_resend_rpm) + `core/error_catalog.py` 5 NEW ErrorSpecs. 5. `email/application/member_verify_code_email_template.py:render_member_verify_code_email` (mirror domain_verified template) + the keyed-hash helper (HMAC-SHA256(code, HMAC(jwt_secret,'member-verify-code')) — Option A, constant-time `hmac.compare_digest`). 6. `application/member_verify_use_cases.py` — Issue / Verify / Resend use-cases (Verify reads under FOR-UPDATE, compares constant-time, flips or bumps; Resend re-issues; both enforce owner + M6b domain-match + generic/personal exclusion). 7. `api/schemas.py` (+member_verified_at + to_list_item) + `api/domain_claims_router.py` (2 routes, reuse `_get_owner_identity`+`_rate_limit`). 8. `tenants/api/router.py` signup hook on the NEW-tenant BUSINESS branch — fail-OPEN wrap. 9. Sweep exact-shape consumers of DomainClaim / the list response for the new field.
Persona (required): generic (no domain persona under `.add/personas/`; backend-security discipline carried by the §3 SAFETY RULES + the ≥2 adversarial verifies)
Spawn isolation: shared-tree (in-place on `feat/domain-onboarding-softening`). REASON: sequential single-stream task that shares domain_capture files with the already-committed DNS-softener work on this same branch; a worktree would branch from session-start main lacking those commits and force a net-diff merge (see worktree-agent-stale-base) — no parallel sibling to isolate from.
Known-problem fixes: (a) `test_migrations.py` tenant_domain_claims manifest → sanctioned additive edit. (b) `GATEWAY_TEST_DATABASE_URL` must be UNSET not empty (`env -u`), one pytest process at a time on :5433. (c) 6 pre-existing alembic heads — chain ONLY off `0f1648b174a2`, never merge unrelated heads. (d) argon2/passlib import cost — reuse the wired hasher only if Option A helper insufficient (it is not; Option A stands). (e) `jwt_secret` domain-separation key derived once (module-level or cached), never per-call re-HMAC in a hot loop.
Strategy actually used: <fill at VERIFY>
Safety rule (feature-specific): the attempt-cap read+compare+increment (or flip-on-success) executes inside ONE transaction under `SELECT … FOR UPDATE` on the claim row — concurrent guesses serialize so the ≤5 cap is exact and a success flip is a single guarded write; the code is stored ONLY as the keyed hash and cleared on success/expiry/cap (single-use); status/verified_at/indexes are never written by any member-verify path.
Code lives in: `apps/gateway/src/gateway/`
Constraints: do NOT change any test or the contract; allow-list packages only (no new deps — hmac/hashlib are stdlib); ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — target suite 18/18 (`test_member_verified_recognition.py`); affected+sibling suites 183/183 (domain_capture, migrations, saml_sso, oidc_tenant_config, transactional_email, account_type_discriminator, signup_routing_authz) at `-n6`.
- [x] coverage did not decrease — additive new module + tests; the new use-cases/repo methods/routes are all exercised by the 18-test suite (target 90%).
- [x] no test or contract was altered during build — §0–§3 untouched; the ONE test change was a change-request re-entered via `phase tests` (isolated the signup-time email in `test_resend` so `== 1` measures the RESEND email — non-weakening, then re-crossed tests→build).
- [x] the green was EARNED, not gamed — refute-read EARNED (below); tests assert observable DB/HTTP/email state, single-use invalidation retried with the ORIGINAL correct code, exactly-5 under real concurrency, a keyed-hash oracle, and no-secret-in-body.
- [x] concurrency / timing of the risky operation is safe — `FOR UPDATE` lock spans read→write→commit; the increment commits BEFORE the use-case raises (persists), a 2nd concurrent correct-code reads `code_hash=None`→400 (no double-verify). Independently code-read + verified by the concurrency-lens adversary.
- [x] no exposed secrets, injection openings, or unexpected dependencies — code stored only as the keyed hash (never plaintext/logged); stdlib hmac/hashlib/secrets only (no new dep); parameterized SQL; the hash/expiry/attempt-count never ride the entity or any API schema.
- [x] layering & dependencies follow CONVENTIONS.md — hexagonal: domain (pure hash/block-list) · application (3 use-cases) · infra (repo adapters) · api (routes/schemas); secret stays repository-internal.
- [ ] a person reviewed and approved the change — PENDING: security human floor (this gate, presented to Tin).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
Component green-bar (gateway): `pytest (Makefile:test / ci.yml 'Tests' step)` — verify `cd apps/gateway && env -u GATEWAY_TEST_DATABASE_URL uv run pytest tests/domain_capture/test_member_verified_recognition.py` (+ the domain_capture + migrations suites for drift).
- [x] Business signup persists a pending claim AND sends exactly one 6-digit-code email to the signup address — `test_business_signup_issues_code_to_signup_address`: DB row non-null hash + ~15-min expiry + attempt_count 0, `fake.sent[0].to == owner@example-co.com`. Hook is `account_type=="business"` only; `domain = body.email.rsplit("@")[-1]` (server-derived, router.py:68).
- [x] `member_verify_code_hash` is NOT plaintext and equals HMAC-SHA256(code, HMAC(jwt_secret,'member-verify-code')) — `test_code_stored_only_as_keyed_hash` recomputes the oracle from the emailed code; `member_verify_code.py` implements exactly Option A + `hmac.compare_digest` + `secrets.randbelow`.
- [x] Correct code sets `member_verified_at`, `status` stays 'pending', `verified_at` null, 3 code cols cleared — `test_correct_code_sets_member_verified` + replay→400/410; `mark_member_verified` writes only member_verified_at + NULLs the 3 code cols.
- [x] 6 concurrent wrong guesses → exactly 5 increments, invalidated once — `test_concurrent_wrong_guesses_cap_holds`; code-read confirms `load…for_update` holds the lock across `bump` (commit) so serialization is exact; a 2nd concurrent CORRECT code reads `code_hash=None`→400 (no double-verify).
- [x] `resolve_verified_tenant` returns None for a member-verified-but-pending domain — `test_member_verified_row_never_auto_joins` calls it directly; `resolve_verified_tenant` byte-unchanged (empty repo.py diff for it).
- [x] No API body contains `member_verify_code_hash`/`_expires_at`/`_attempt_count` — `test_list_exposes_member_verified_at_only` asserts absence in raw text; entity/schema carry only `member_verified_at`, the 3 code cols stay repo-internal.
- [x] A code for `acme.com` cannot member-verify a different domain — `test_domain_mismatch_refused` (403 DOMAIN_MISMATCH on verify+resend); M6b compares the locked-row domain to `normalize_domain(caller JWT email domain)`; subdomain/unicode/trailing-dot all fail-closed.
- [x] `git diff` additive-only — resolve_verified_tenant + `ck_tenant_domain_claims_status` + both unique indexes byte-unchanged (grep-confirmed); the 4 "deletions" are import-line extensions; no `test_migrations.py` manifest edit needed (table-level manifest, column parity via ORM+migration).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — the 3 use-cases wired via `api/deps.py` factories (get_member_verify_use_case / _resend / get_issue_member_verify_code_use_case); routes registered on domain_claims_router; signup hook calls the issue factory; all reached by the 18-test suite.
- [x] DEAD-CODE (code) — no orphan: every new symbol (MemberVerifyState, the 6 errors, 5 ErrorSpecs, 4 config knobs, template, block-list, hash helper) is referenced by a use-case/route/test.
- [x] SEMANTIC — read the 6 new/changed src files IN FULL (hash helper, use-cases, repository methods, router routes, signup hook, schemas) + the migration; confirmed Option A, FOR-UPDATE boundary, server-derived identity, business-only issuance, additive migration off `0f1648b174a2`.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every symbol §3 CONTRACT cites still resolves — TenantDomainClaimRow (+4 cols), DomainClaim (+member_verified_at), DomainClaimRepository (+4 methods), DomainClaimListItem (+member_verified_at)/to_list_item, domain_claims_router (+2 routes), the 3 new use-cases, render_member_verify_code_email, public_email_domains, normalize_domain, DomainClaimRateLimiter, resolve_verified_tenant (untouched), the 5 new ErrorSpecs — all present in the current tree.
- [x] no anchor moved/renamed since Ground SHA `04ed333` — all resolved at their §0 paths.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: self (orchestrator full code-read) + add-verify lens-1 (a7fd17f) · adversarially checked: no vacuous asserts (mutation tests assert unchanged-invariants + single-use replay with the ORIGINAL correct code); the concurrency test drives 6 real gathered requests against independent per-request sessions (harness commits, not a shared rolled-back txn); the keyed-hash test recomputes an independent oracle; no logic stubbed away (rate-limit double is the only stub, and it only exercises the 429 path).

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: TWO independent adversarial add-verify subagents (opus) + orchestrator full code-read — a7fd17f (authz/confused-deputy lens) · a5761cb (crypto/brute-force/concurrency lens)
1. Security: CLEAR — server-derived recipient/domain (signed-JWT identity, never request-supplied; `domain=body.email.rsplit("@")[-1]`); M6b enforced on verify+resend against the locked-row domain vs normalized caller domain (subdomain/unicode/trailing-dot/IP fail-closed); owner-gate before any DB work; cross-tenant → None → indistinguishable NOT_FOUND; keyed-hash-at-rest (Option A) never plaintext/logged; generic/personal excluded at issuance (business-only hook) + resend (typed errors). Both lenses CLEAR; my own per-attack read agrees.
2. Concurrency: CLEAR — `load_member_verify_row_for_update` takes `SELECT … FOR UPDATE` and does NOT commit; `bump`/`mark` write-then-commit, so the lock spans read→write→commit and the increment persists BEFORE the use-case raises; a 2nd concurrent CORRECT code reads `code_hash=None`→400 (no double-verify). Exactly-5 under 6 real gathered requests is guaranteed, not timing-lucky (`test_concurrent_wrong_guesses_cap_holds`).
3. Architecture: CLEAR — additive-only (resolve_verified_tenant + ClaimStatus CheckConstraint + both unique indexes byte-unchanged; migration chains off `0f1648b174a2`, working downgrade); hexagonal layering; the secret stays repository-internal (never on the entity/schema); no new dependency (stdlib hmac/hashlib/secrets).
Verdict: PASS (advisory — security)
Residue: none. Minor non-blocking hardening for §7 observe: `MemberVerifyRequest` could add `extra="forbid"` (extra body fields are currently ignored, not read — zero effect); `code` has no length/pattern bound (a non-6-digit string simply never matches — no injection).
Binding: advisory — security (a HUMAN floor: this gate escalates to Tin; ≥2 independent adversarial verifies done as required)

### GATE RECORD
Reported: yes — security gate report rendered to Tin (banner/ARC/evidence/approve) 2026-07-20
Outcome: PASS — security human floor cleared: ≥2 independent adversarial verifies (authz + crypto/concurrency lenses) + orchestrator full code-read, all CLEAR; 18/18 target + 183/183 affected suites green; contract untouched.
component: gateway · expected green-bar: pytest (Makefile:test / ci.yml 'Tests' step) · verify: cd apps/gateway && uv run pytest
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-20

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang, 2026-07-20. DECIDED at freeze: hash-at-rest = Option A —)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

