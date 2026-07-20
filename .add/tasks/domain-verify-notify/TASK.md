# TASK: Email-me-when-it's-live: opt-in + background scheduler auto-verify + transactional email (backend, SECURITY)

slug: domain-verify-notify · created: 2026-07-19 · stage: production
milestone: domain-onboarding-softening
component: gateway
sensitivity: security   <!-- a BACKGROUND job auto-verifies domain claims with NO human present + sends outbound email (PII). Security floor: HARD-STOP at verify + ≥2 independent adversarial verifies (standing bar). The DNS-TXT proof is REUSED verbatim (never weakened) — that is the core trust argument. -->
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: tests   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- REUSE (do NOT modify — frozen): `apps/gateway/src/gateway/domain_capture/application/verify_claim_use_case.py:
  VerifyDomainClaimUseCase.execute(claim_id, tenant_id)` — the DNS-TXT proof: get_own → expiry check →
  `DnsTxtResolver.lookup_txt(record_name, timeout)` (fail-CLOSED: DnsLookupFailedError propagates, status
  untouched) → `expected not in values` → DomainVerificationFailedError → else `repository.mark_verified`
  (atomic `UPDATE ... WHERE status='pending'`). The background job REUSES this exact logic — the proof is
  never re-implemented or weakened. `_CHALLENGE_LABEL_PREFIX="_ai-proxy-challenge"`, value
  `ai-proxy-domain-verification={token}`.
- `apps/gateway/src/gateway/domain_capture/domain/ports.py:DnsTxtResolver` (Protocol, frozen) +
  `:DomainClaimRepository` (Protocol) — existing methods create_or_reissue/list_for_tenant/get_own/
  mark_verified/revoke/has_verified_claim_by_other_tenant. ADD (additive): `request_notify(claim_id, tenant_id)`
  (set opt-in ts), `mark_notified(claim_id)` (set notified_at, idempotent), `list_notify_candidates(now)`
  (opted-in AND status='pending' AND notified_at IS NULL AND expires_at > now — the bounded scheduler input).
- `apps/gateway/src/gateway/domain_capture/infrastructure/orm.py:TenantDomainClaimRow` — the
  `tenant_domain_claims` table. ADD 2 additive NULLABLE cols: `notify_requested_at` (opt-in ts; NULL=not
  opted in) + `notified_at` (email-sent ts; NULL=not yet). FROZEN untouched: the ClaimStatus CheckConstraint
  `status IN ('pending','verified')`, `uq_domain_claims_tenant_domain`, `uq_domain_claims_domain_verified`.
- `apps/gateway/src/gateway/domain_capture/domain/entities.py:DomainClaim` (frozen dataclass) — ADD 2
  additive optional fields mirroring the cols (for list-response mapping).
- SCHEDULER pattern to MIRROR: `apps/gateway/src/gateway/catalog/application/refresh_scheduler.py:
  CatalogRefreshScheduler` — `refresh_once()` NEVER raises (swallows → 0, self-heals next tick);
  `run_forever(interval_seconds, _sleep=asyncio.sleep)` work-then-sleep, propagates CancelledError only.
  Wired in `main.py` lifespan (mirror `app.state.*` wiring + the lifespan create_task/cancel).
- EMAIL seam (frozen @ v1, extend additively): `email/domain/ports.py:EmailSender.send(EmailMessage)` ·
  `email/application/email_dispatch.py:send_email(sender, message)` (fail-OPEN boundary — never raises) ·
  `email/application/invite_email_template.py:render_invite_email(...)` (template pattern to mirror) ·
  `email/domain/entities.py:EmailMessage(to, subject, text_body, html_body)` · wired at `main.py:
  build_email_sender` → `app.state.email_sender` (Console unless `email_smtp_enabled`). ADD:
  `render_domain_verified_email(*, to, domain, origin)`.
- RECIPIENT lookup: the notify email goes to the CLAIM OWNER's account email, resolved server-side from
  `TenantDomainClaimRow.created_by_user_id` → users.email (via the existing user repository — pin the exact
  symbol at build). NEVER a request-supplied address.
- RATE/CONFIG precedent: `core/config.py:~1405 domain_claim_verify_rpm=30` + `DomainClaimRateLimiter`. ADD:
  a scheduler cadence knob `domain_verify_notify_interval_seconds` + the DNS timeout reuse.

Context (working folder): `apps/gateway/src/gateway/domain_capture/` (+ a new template in `email/application/`,
a migration in `apps/gateway/migrations/versions/`, wiring in `main.py`, a knob in `core/config.py`). Backend
only — the dashboard `dns-verify-softeners` CONSUMES the opt-in endpoint shape frozen here.
Honors (patterns / conventions): hexagonal (domain Protocol ports; application use-cases; infra adapters);
fail-open scheduler (CatalogRefreshScheduler); fail-open email dispatch (send_email); fail-CLOSED DNS
(VerifyDomainClaimUseCase); atomic status flip via repository.mark_verified; additive nullable migration
columns (many precedents, e.g. a7c2f0e1b4d9 tenant_retention_zdr); owner-only admin routes via
`_get_owner_identity`.
Seams consulted: email seam (transactional-email FROZEN @ v1) · DnsTxtResolver (domain-capture FROZEN @ v1) ·
scheduler lifecycle (catalog refresh / RetentionSweeper).
Anchors the contract cites: VerifyDomainClaimUseCase, DomainClaimRepository(+request_notify/mark_notified/
list_notify_candidates), TenantDomainClaimRow(+notify_requested_at/notified_at), render_domain_verified_email,
send_email/EmailSender, a new NotifyOptIn endpoint on the domain-claims router, new ErrorSpec(s).
Issues/Risks (→ feed §1):
- **R-sec-1 (email-injection / spam vector):** if the opt-in let the caller name the recipient, an attacker
  could opt-in a pending claim to spam a victim's inbox. MITIGATION (a Must): recipient is ALWAYS the claim
  owner's own account email, server-derived from created_by_user_id — the request body carries NO email.
- **R-sec-2 (unattended auto-verify must not weaken the proof):** the background flip must run the SAME
  fail-closed DNS-TXT check as the human path (reuse VerifyDomainClaimUseCase) — never a shortcut, never
  trusting anything but a live matching TXT record. A DnsLookupFailed/VerificationFailed leaves status
  untouched (self-heals next tick). This is the whole trust argument for automating verification.
- **R-sec-3 (double-email / at-least-once ticks):** overlapping ticks or a retry must email at most ONCE.
  MITIGATION: `notified_at` guard — send only if NULL, then set it; the send+mark ordering must not double-send
  (mark_notified BEFORE dispatch, or an atomic claim-of-work) — decide at contract; email_dispatch is fail-open
  so a send failure after mark is acceptable (better under- than over-send), but must be logged + is bounded
  (claim expiry stops re-attempts).
- **R-sec-4 (runaway / unbounded scheduler):** the candidate set is naturally bounded (opted-in ∧ pending ∧
  not-notified ∧ not-expired); each DNS lookup has a timeout; the loop is fail-open. No per-claim infinite
  retry — claim expiry is the ceiling.
- **R-drift (known casualty):** `apps/gateway/tests/migrations/test_migrations.py` carries a COLUMN/table
  manifest for `tenant_domain_claims`; adding columns will trip it → SANCTIONED additive manifest edit
  (per [[commercial-self-serve-milestone]] — sweep table-manifest consumers). Also sweep exact-shape consumers
  of the DomainClaim entity / the claim list response for the 2 new fields.
Related intent: milestone D4 + Exit criterion "email me when it's live" ([[domain-onboarding-progressive-trust]]).
WHY: long DNS propagation means the admin shouldn't have to sit on the tab; opt in, walk away, get one email
when it's live. Softens the STRONG rung without touching auto-join.
Ground SHA: 9ec92b4 (cite symbols, not bare line numbers; any line ref is "as of" this commit)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: "Email me when it's live" — an owner can opt a pending domain claim into a background watch; a
scheduler re-runs the SAME fail-closed DNS-TXT proof on a cadence and, on the first match, auto-verifies the
claim and sends ONE email to the owner's own account address. Reuses the frozen verify proof verbatim (the
DNS-TXT requirement is unchanged); adds only opt-in state + a loop + a template.
Framings weighed:
- **Reuse VerifyDomainClaimUseCase in a fail-open scheduler (CHOSEN)** — the background flip runs the exact
  frozen proof; no duplicated/weakened verification logic; the scheduler owns only the loop + email.
- Duplicate a lighter DNS check in the scheduler (rejected) — would fork the proof, risking divergence/weakening.
- Client-only long-poll instead of a backend watch (rejected — that's task `dns-verify-softeners`) — cannot
  survive the tab closing, which is exactly the "walk away and get emailed" need.

Must:
<must>
  - M1 Opt-in: `POST /admin/domain-claims/{id}/notify` (OWNER-only, body `{}` — NO email field) sets the claim's
    `notify_requested_at`; idempotent (opting in twice is a no-op success). Returns the updated claim.
  - M2 Opt-out: `DELETE /admin/domain-claims/{id}/notify` (OWNER-only) clears `notify_requested_at` (revocable).
  - M3 Recipient is server-derived: the notify email is sent ONLY to the claim owner's account email, resolved
    from `created_by_user_id` — never from any request input (R-sec-1).
  - M4 Background re-check: a `DomainVerifyNotifyScheduler` (mirrors CatalogRefreshScheduler) periodically loads
    `list_notify_candidates(now)` = claims where `notify_requested_at IS NOT NULL AND status='pending' AND
    notified_at IS NULL AND expires_at > now`, and for each runs the FROZEN DNS-TXT proof (reusing
    VerifyDomainClaimUseCase's check — same resolver, same expected value, fail-CLOSED).
  - M5 Auto-verify + notify on match: on a matching TXT record the claim is flipped verified via the atomic
    `repository.mark_verified` (the SAME proof-gated flip the human path uses), then `mark_notified` atomically
    claims the send (`UPDATE ... SET notified_at=now() WHERE id=:id AND notified_at IS NULL RETURNING id` — only
    the winner proceeds), then ONE `render_domain_verified_email` is dispatched via the fail-open `send_email`.
  - M6 Fail-open loop: `refresh_once()` NEVER raises (a DNS/DB/SMTP failure is logged, no crash, self-heals next
    tick); `run_forever` swallows non-CancelledError, propagates CancelledError for clean shutdown. Bounded: per
    claim a DNS timeout; the candidate set is finite; claim expiry is the retry ceiling (no infinite re-attempt).
  - M7 Proof unchanged: a non-matching or failed DNS lookup leaves the claim `status='pending'` and
    `notified_at NULL` — untouched (self-heals). No path flips a claim verified without a live matching TXT record.
</must>
Reject:
<reject>
  - R1 Opt-in/out on an unknown claim OR another tenant's claim -> "ERR_DOMAIN_CLAIM_NOT_FOUND" (404; claim
    stays unchanged; deliberately indistinguishable, mirrors verify).
  - R2 Opt-in on an already-verified claim -> "ERR_DOMAIN_CLAIM_NOT_PENDING" (409; nothing to watch; no state
    change). [NEW ErrorSpec]
  - R3 Opt-in on an expired claim -> "ERR_DOMAIN_CLAIM_EXPIRED" (410; must reissue first; no state change).
  - R4 A non-owner calling notify/opt-out -> "ERR_AUTH_FORBIDDEN" (403; no state change) — server gate.
  - R5 Opt-in beyond the per-tenant rate limit -> "ERR_DOMAIN_CLAIM_RATE_LIMITED" (429; no state change).
</reject>
After:
<after>
  - An opted-in claim whose TXT record goes live is verified by the scheduler within ~one interval AND the owner
    receives exactly one "acme.com is verified" email at their account address; `notified_at` is set.
  - Opting out before it goes live stops the watch (no email); re-opting in resumes it.
  - The DNS-TXT proof, the ClaimStatus enum + constraints + indexes, `resolve_verified_tenant*`, and AUTO-JOIN
    are all UNCHANGED — grep shows only additive columns/methods/template/endpoint/scheduler.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Multiple gateway replicas each running the scheduler could double-verify/double-email — lowest confidence
    because deployment replica count isn't pinned here. MITIGATION makes it safe REGARDLESS: `mark_verified` is
    idempotent (WHERE status='pending' — the 2nd flip is a no-op) and `mark_notified` is an atomic conditional
    claim (WHERE notified_at IS NULL RETURNING — only one replica wins the send). So even N replicas send ONCE.
    If wrong (the atomic claim is insufficient): cost = add an advisory lock / dedicated leader — but the
    conditional-UPDATE claim is the standard, sufficient guard.
  - [x] Recipient = owner account email (created_by_user_id) — confirmed the column exists; the user-email repo
    lookup is a build detail. NEVER request-supplied (R-sec-1 closed by design).
  - [x] mark_notified BEFORE dispatch — confirmed the right ordering: under-send on SMTP failure (rare, logged,
    fail-open) is safer than over-send (spam); the claim is verified regardless, so the UI still reflects truth.
  - [x] Reusing VerifyDomainClaimUseCase keeps the proof identical — confirmed by reading it (fail-closed,
    atomic flip); the scheduler calls the same use-case per candidate (tenant_id from the claim row).
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Owner opts in to be emailed when live   # M1
  Given a pending claim for acme.com owned by the caller
  When the owner POSTs /admin/domain-claims/{id}/notify with an empty body
  Then notify_requested_at is set and the updated claim is returned
  And no email address was read from the request

Scenario: Opting in twice is an idempotent no-op success   # M1
  Given a claim already opted in
  When the owner POSTs notify again
  Then it succeeds and nothing changes beyond the existing opt-in

Scenario: Owner opts out to stop the watch   # M2
  Given a claim opted in for notification
  When the owner DELETEs /admin/domain-claims/{id}/notify
  Then notify_requested_at is cleared
  And the scheduler will not email for it

Scenario: Scheduler auto-verifies and emails the OWNER when the record goes live   # M4,M5,M3
  Given an opted-in, pending, non-expired claim whose DNS TXT record now matches
  When the scheduler runs a tick
  Then the claim is flipped to verified via the atomic proof-gated update
  And exactly one verification email is sent to the claim owner's account email
  And notified_at is set
  And the email recipient was derived from created_by_user_id, never from any request

Scenario: Not-yet-live record leaves the claim untouched (proof unchanged)   # M7
  Given an opted-in pending claim whose TXT record does NOT match yet
  When the scheduler runs a tick
  Then the claim stays status=pending with notified_at NULL
  And no email is sent
  And it is retried on the next tick

Scenario: A DNS lookup failure never crashes the loop or flips the claim   # M6,M7
  Given an opted-in pending claim and a failing DNS resolver (timeout/NXDOMAIN)
  When the scheduler runs a tick
  Then refresh_once does not raise and the claim stays pending untouched
  And the loop continues to the next tick

Scenario: Exactly one email even across overlapping ticks / replicas   # M5
  Given an opted-in claim whose record just went live
  When two scheduler passes claim it concurrently
  Then only the pass that wins the atomic notified_at claim sends the email
  And the other sends nothing (mark_verified's WHERE status='pending' also no-ops the 2nd flip)

Scenario: Notify on another tenant's claim is not found   # R1
  Given a claim owned by a different tenant
  When the caller POSTs notify for it
  Then the response is 404 ERR_DOMAIN_CLAIM_NOT_FOUND
  And that claim is unchanged

Scenario: Notify on an already-verified claim is rejected   # R2
  Given a verified claim
  When the owner POSTs notify
  Then the response is 409 ERR_DOMAIN_CLAIM_NOT_PENDING
  And nothing changes

Scenario: Notify on an expired claim is rejected   # R3
  Given a pending claim past expires_at
  When the owner POSTs notify
  Then the response is 410 ERR_DOMAIN_CLAIM_EXPIRED
  And nothing changes

Scenario: A non-owner cannot opt a claim in   # R4
  Given a caller who is not an owner
  When they POST notify
  Then the response is 403 ERR_AUTH_FORBIDDEN
  And nothing changes

Scenario: Opt-in beyond the rate limit is rejected   # R5
  Given a tenant that has exceeded the notify rate limit
  When the owner POSTs notify
  Then the response is 429 ERR_DOMAIN_CLAIM_RATE_LIMITED
  And nothing changes
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /admin/domain-claims/{claim_id}/notify   body: {}          (OWNER-only; NO email field)
  200 -> DomainClaimListItem (incl. notify_requested_at, notified_at)   # idempotent opt-in
  404 -> { code: "ERR_DOMAIN_CLAIM_NOT_FOUND" }        # unknown / other tenant
  409 -> { code: "ERR_DOMAIN_CLAIM_NOT_PENDING" }      # already verified   [NEW ErrorSpec, 409]
  410 -> { code: "ERR_DOMAIN_CLAIM_EXPIRED" }          # must reissue
  403 -> { code: "ERR_AUTH_FORBIDDEN" }                # non-owner
  429 -> { code: "ERR_DOMAIN_CLAIM_RATE_LIMITED" }     # per-tenant limit

DELETE /admin/domain-claims/{claim_id}/notify         (OWNER-only)
  204 -> (no content)   # clears notify_requested_at; idempotent
  404 -> { code: "ERR_DOMAIN_CLAIM_NOT_FOUND" }
  403 -> { code: "ERR_AUTH_FORBIDDEN" }

GET /admin/domain-claims  (EXISTING list) — ADDITIVELY exposes per item:
  + notify_requested_at: datetime|null     + notified_at: datetime|null

BACKGROUND  DomainVerifyNotifyScheduler (no HTTP) — mirrors CatalogRefreshScheduler:
  refresh_once():  for c in repo.list_notify_candidates(now):        # opted-in ∧ pending ∧ !notified ∧ !expired
                     try: verify c via the FROZEN DNS-TXT proof (reuse VerifyDomainClaimUseCase)
                          on match: mark_verified(c) → mark_notified(c) [atomic claim] → send_email(owner_email, render_domain_verified_email)
                     except (DnsLookupFailed|VerificationFailed): leave untouched (pending), retry next tick
                     NEVER raises (log + continue)
  run_forever(interval_seconds): work-then-sleep; propagate CancelledError only.

Schema (additive; FROZEN parts UNTOUCHED):
  tenant_domain_claims  + notify_requested_at TIMESTAMPTZ NULL,  + notified_at TIMESTAMPTZ NULL
    (ClaimStatus CheckConstraint pending|verified, uq_domain_claims_tenant_domain,
     uq_domain_claims_domain_verified — all UNCHANGED)
  repository (additive ports): request_notify(claim_id, tenant_id) · clear_notify(claim_id, tenant_id) ·
    mark_notified(claim_id)->bool  (atomic: UPDATE … SET notified_at=now() WHERE id=:id AND notified_at IS NULL
    RETURNING id) · list_notify_candidates(now)->list[DomainClaim]
  email: render_domain_verified_email(*, to, domain, origin) -> EmailMessage   (additive template)
  config: domain_verify_notify_interval_seconds (knob) ; notify rate-limit reuses DomainClaimRateLimiter action
  main.py lifespan: construct DomainVerifyNotifyScheduler + create_task(run_forever) + cancel on shutdown
```

SAFETY RULES (security task — binding):
- The proof is REUSED verbatim (VerifyDomainClaimUseCase): no path flips a claim verified without a live
  matching TXT record; a DNS failure fails CLOSED (status untouched).
- Recipient is ALWAYS the owner's account email (from created_by_user_id); the API carries no email field.
- Exactly-once email: `mark_notified` is an atomic conditional claim (WHERE notified_at IS NULL RETURNING);
  only the winner dispatches. `mark_notified` runs BEFORE dispatch (under-send on SMTP failure > over-send).
- The scheduler is fail-open + bounded (candidate set finite, per-lookup timeout, claim-expiry ceiling).

Glossary deltas: none new (uses domain-claim / verified). "notify-when-live" is a feature name, not a domain term.
Status: FROZEN @ v1 — approved by Tin Dang, 2026-07-20 (consolidated backend-first freeze; security bar: ≥2 adversarial verifies + HARD-STOP floor)
Reported: yes — consolidated freeze report (banner/ARC/SHAPE + the 3 security invariants) rendered 2026-07-20
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 95% (security task — the scheduler + opt-in + proof-reuse paths fully covered)
Plan (one test per scenario, asserting behavior not internals; gateway pytest, base `gateway_test` on :5433,
a fake DnsTxtResolver + a capturing EmailSender injected via app.state):
<test_plan>
  - test_optin_sets_flag: POST notify / notify_requested_at set, claim returned, no email field read · M1
  - test_optin_idempotent: POST notify twice / second is a no-op success · M1
  - test_optout_clears_flag: DELETE notify / notify_requested_at cleared · M2
  - test_scheduler_verifies_and_emails_owner: opted-in pending + matching TXT / tick / verified + ONE email to owner account addr + notified_at set · M3,M4,M5
  - test_recipient_is_owner_never_request: assert the captured email .to == owner account email, independent of any input · R-sec-1,M3
  - test_scheduler_leaves_unmatched_pending: non-matching TXT / tick / still pending, no email, notified_at NULL · M7
  - test_scheduler_failopen_on_dns_error: failing resolver / refresh_once returns without raising, claim untouched · M6,M7
  - test_exactly_once_email_on_double_claim: two mark_notified attempts / only the atomic winner sends · M5
  - test_notify_other_tenant_404: cross-tenant claim / 404 NOT_FOUND, unchanged · R1
  - test_notify_already_verified_409: verified claim / 409 NOT_PENDING, unchanged · R2
  - test_notify_expired_410: expired claim / 410 EXPIRED, unchanged · R3
  - test_notify_non_owner_403: non-owner / 403 FORBIDDEN, unchanged · R4
  - test_notify_rate_limited_429: over limit / 429 RATE_LIMITED, unchanged · R5
  - test_migration_manifest_reconciled: the tenant_domain_claims column manifest includes the 2 new cols (SANCTIONED additive) · R-drift
</test_plan>

Tests live in: `apps/gateway/tests/domain_capture/` (new sibling suite files, e.g. `test_domain_verify_notify.py`
+ scheduler test) — NOT editing any FROZEN domain-capture suite. MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
`apps/gateway/src/gateway/domain_capture/`   (DIRECTORY — orm.py +2 cols, entities.py +2 fields, ports.py +methods, repository.py, api/domain_claims_router.py +2 routes, api/schemas.py +fields, a new application/notify_scheduler.py + application/notify use-case)
`apps/gateway/src/gateway/email/application/`   (new render_domain_verified_email template)
`apps/gateway/src/gateway/core/error_catalog.py`   (new ERR_DOMAIN_CLAIM_NOT_PENDING additively)
`apps/gateway/src/gateway/core/config.py`   (new domain_verify_notify_interval_seconds knob)
`apps/gateway/src/gateway/main.py`   (lifespan wiring: construct + create_task + cancel the scheduler)
`apps/gateway/migrations/`   (new additive migration: 2 nullable cols on tenant_domain_claims; chain off current head)
`apps/gateway/tests/domain_capture/`   (NEW red suites only)
`apps/gateway/tests/migrations/test_migrations.py`   (SANCTIONED additive column-manifest reconciliation only)
Strategy (ordered batches): 1. Migration + orm/entity cols (additive). 2. Repository ports (request_notify /
clear_notify / mark_notified atomic-claim / list_notify_candidates). 3. Opt-in/out router endpoints + schemas +
new ErrorSpec + rate-limit. 4. Email template. 5. NotifyScheduler reusing VerifyDomainClaimUseCase + owner-email
resolve + fail-open loop. 6. main.py lifespan wiring + config knob. Proof reuse is the spine — never fork it.
Persona (required): generic (no gateway persona file fits; SOUL.md governs voice; backend-architect discipline in Strategy).
Spawn isolation (default): worktree for any build/verify subagent spawn.
Known-problem fixes: celery/redis downgrade trap → NO new async infra deps (pure asyncio scheduler, mirror
CatalogRefreshScheduler — do NOT add celery); double-email → atomic mark_notified claim; migration head drift →
chain off `alembic heads` at build; manifest test red → SANCTIONED additive reconciliation (sweep first);
scope-artifact poisoning → clean .coverage/.pytest_cache as the LAST pre-gate step (per [[add-scope-snapshot-poisoning]]);
frozen proof → REUSE VerifyDomainClaimUseCase, never re-implement.
Strategy actually used: <fill at VERIFY>
Safety rule (feature-specific): no claim is ever flipped verified without a live matching DNS-TXT record (proof
reused fail-closed); recipient is always the owner's account email (never request-supplied); email sent
exactly once via the atomic notified_at claim, marked before dispatch.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token with "/" = project root · a bare name = sibling of the previous token's dir · a DIRECTORY token covers its whole subtree (diverges from §4's non-recursive counting) · outside-root resolutions drop fail-closed · absent line = UNDECLARED (grandfathered, never retro-red) · enforcement live: a completing verify gate refuses an out-of-scope build (scope_violation → self-heal); check surfaces it. EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

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
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [ ] <observable outcome a correct build must produce> — confirmed by <how / where>
- [ ] <another observable outcome> — confirmed by <evidence seen>

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [ ] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed by <how / where>
- [ ] any anchor that moved/renamed since Ground SHA is named here, not left silent

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
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

<!-- Security is ALWAYS HARD-STOP; record exactly one outcome — no silent pass. The Advisor 3-lens and Refute-read verdicts are audit-measured (`advisor_verdict_unrecorded` · `refute_unrecorded`), never engine-blocked; a human spot-audit backstops anything unrecorded. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
