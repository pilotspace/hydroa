# TASK: Replace the invite-only dead end with three live routes (SSO / invite link / request access)

slug: signup-refusal-router · created: 2026-07-20 · stage: production
milestone: frontdoor-persona-routing
component: gateway, dashboard
autonomy: conservative
sensitivity: security
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/dashboard/components/auth/SignupForm.tsx:SignupForm` — client Zod validation → `POST
  /api/auth/signup` → 201 push `/app/keys`; the `handleSubmit` catch block special-cases ONLY
  `err.status === 409` (email-taken field error); every other status (incl. today's 403) falls
  through to `setGlobalError(err.problem.title)`, rendered as a single `role="alert"` paragraph
  after the password field — the literal dead end described in the milestone context. The parsed
  `problem: { title?: string; status?: number }` type (~L85-94) DROPS the `code` field even
  though it is present on the wire (see `ProblemDetail` below) — SignupForm cannot today
  distinguish `ERR_SIGNUP_INVITE_ONLY` from any other 403/500.
- `apps/dashboard/lib/resilient-fetch.ts:ProblemDetail` (`{type?, title, status, code?}`) +
  `BffError` (`{status, problem}`) — the shared typed-error shape. SignupForm constructs its OWN
  `BffError` by hand (`new BffError(res.status, { title, status })`) instead of using
  `bffAuthPost`/`handleBffResponse`, which is WHY `code` is silently lost — a real, existing gap,
  not a hypothetical one.
- `apps/dashboard/components/auth/JoinByDomainForm.tsx:bffCode` (~L85-91) — the PRECEDENT for
  reading `problem.code` off a `BffError` to branch UI on a specific error code rather than a bare
  HTTP status; the fix SignupForm needs mirrors this exactly.
- `apps/dashboard/app/(auth)/signup/page.tsx:SignupPage` — thin wrapper, `<AuthShell><SignupForm
  /></AuthShell>`, no routing affordances today.
- `apps/dashboard/app/api/auth/signup/route.ts` — BFF proxy: on `!signupRes.ok` forwards the
  gateway's problem+json body AND status verbatim (`NextResponse.json(errorBody, {status})`) — the
  `code` field already survives this hop untouched; only the dashboard-side parsing drops it.
- `apps/gateway/src/gateway/tenants/api/router.py:signup` — the verified-domain auto-join lookup
  (`resolve_verified_tenant`) runs FIRST; only when it returns `None` does the invite-only gate
  fire: `if not request.app.state.settings.public_signup_enabled: raise
  SIGNUP_INVITE_ONLY.exc()` — checked BEFORE any further DB IO (frozen S1 M2 property, restated
  verbatim in its own comment). This task changes NOTHING about this function.
- `apps/gateway/src/gateway/core/error_catalog.py:SIGNUP_INVITE_ONLY` = `ErrorSpec(403,
  "ERR_SIGNUP_INVITE_ONLY", "Public signup is disabled; ask an existing member for an invite")` —
  the exact frozen error this task's UI must key off of (via `code`), never off bare `status`.
- `apps/dashboard/app/(auth)/join/[token]/page.tsx` + `components/auth/JoinByDomainForm.tsx` — the
  EXISTING, working, token-scoped invite-by-domain redeem flow (`email` → `code+password` → 201/
  session). No token-less `/join` landing route exists anywhere in `app/(auth)/` — confirmed by
  directory listing; there is nothing for a generic "have a link?" screen to link to except telling
  the visitor to open the link they already have.
- `apps/dashboard/components/auth/LoginForm.tsx:resolveSsoDomain`/`validateSsoDomain`/`handleSso`/
  `handleSamlSso` + the always-visible "Work email or domain" field — the EXISTING SSO entry point
  route (a) targets. `useEffect` seeds `ssoDomain` from `localStorage` only today; no query-param
  seed exists yet, but the seed pattern (`readSsoDomain`/one-shot effect) is a direct precedent to
  extend additively with a `?domain=` search-param seed.
- `apps/dashboard/app/(auth)/login/page.tsx:LoginPage` — already reads `searchParams` (`sso_error`,
  `next`) and passes a validated value down — precedent for accepting one more optional param.
- `apps/gateway/src/gateway/tenants/infrastructure/invite_public_rate_limiter.py:
  InvitePublicRateLimiter` (+ sibling `agent_oauth/infrastructure/ip_rate_limiter.py:
  AgentOAuthIpRateLimiter`) — the fixed-60s-window, per-client-IP, Redis INCR+EXPIRE, FAIL-OPEN
  (RedisError/OSError → allow) rate limiter shape every existing public/unauthenticated write
  endpoint in this codebase uses. `core/net.py:resolve_trusted_client_ip` is the IP-resolution
  helper both already call.
- `apps/gateway/src/gateway/core/error_catalog.py:RATE_LIMITED` = `ErrorSpec(429,
  "ERR_RATE_LIMITED", "Rate limit exceeded")` — generic, already-shipped, reusable verbatim; no new
  ErrorSpec needed for the rate-limit case.
- `apps/gateway/src/gateway/tenants/api/schemas.py:SignupRequest` — the `pydantic.EmailStr` +
  `Field(min_length=..., max_length=...)` idiom to mirror for the new capture body.
- `apps/gateway/src/gateway/domain_capture/domain/public_email_domains.py:PUBLIC_EMAIL_DOMAINS` —
  a static, frozen, gateway-only (Python) block-list of generic providers (gmail.com etc.).
  Considered and DECLINED as a client-side "hide the SSO route for personal email" heuristic (see
  Assumptions) — duplicating it into the dashboard risks silent drift from the frozen source.
- `apps/gateway/src/gateway/main.py` (~L1629 `app.include_router(domain_claims_router)`) — the
  router-registration site a new `access_requests_router` joins.
- `apps/gateway/src/gateway/core/config.py:invite_accept_rpm`/`invite_preview_rpm` (+ their
  `field_validator` fail-fast-at-boot pattern, ~L1310-1325) — the rpm-knob convention to mirror for
  a new `access_request_rpm`.

Context (working folder): frontend — `apps/dashboard/app/(auth)/signup/`,
`components/auth/SignupForm.tsx`, `app/api/auth/signup/route.ts`, a NEW
`app/api/auth/access-requests/route.ts`, and an additive touch to `components/auth/LoginForm.tsx`
(query-param SSO-domain seed only). Backend — `apps/gateway/src/gateway/tenants/api/router.py`
(no change to `signup`, only registering the new router alongside it), `core/error_catalog.py`
(reuse only, no new ErrorSpec expected), `core/config.py` (one new rpm knob), a NEW small module
(proposed `apps/gateway/src/gateway/access_requests/` mirroring `domain_capture`'s
domain/application/infrastructure/api split at a much smaller scale — one entity, one use case,
one repository, one rate limiter, one router), and one new additive migration.
Honors (patterns / conventions): hexagonal per-bounded-context layout (domain/application/
infrastructure/api); public-endpoint rate limiting is ALWAYS per-IP + fail-open (never per-tenant,
since no tenant exists yet); a public capture endpoint returns a UNIFORM response regardless of
what the server does or doesn't already know (the exact discipline `resolve_verified_tenant`
already protects for signup itself); RFC 9457 problem+json envelope (`code`/`title`/`status`)
everywhere; `role="alert"`/`role="status"` + `aria-live="polite"` for all inline messaging
(SignupForm/LoginForm/JoinByDomainForm precedent).
Anchors the contract cites: `SignupForm`, `ProblemDetail`/`BffError`, `LoginForm` (SSO domain seed),
`JoinByDomainForm`/`join/[token]` (unmodified, linked-to), `tenants/api/router.py:signup` (frozen,
unmodified), `SIGNUP_INVITE_ONLY`, `RATE_LIMITED`, `InvitePublicRateLimiter` (pattern to mirror), a
NEW `access_requests` bounded context (entity/use-case/router/rate-limiter/migration), a NEW BFF
route.
Issues/Risks (→ feed §1):
- **R-sec-1 (the routing panel itself becomes an oracle):** if any of the three routes' visibility,
  copy, enabled state, or the request-access response varied by whether the typed email/domain is
  already a customer, already invited, or unknown, that variance IS the enumeration signal the
  milestone explicitly forbids. MITIGATION (a Must): all three routes are static and identical for
  every visitor; the smart "is this domain a customer" check is explicitly OUT of scope here — it
  is `domain-aware-auth-routing`'s owned deliverable, next in this task's own DAG chain.
- **R-sec-2 (request-access as a new public write surface):** an unauthenticated endpoint that
  accepts an arbitrary email is spam/abuse surface even without a notification loop yet.
  MITIGATION: per-IP fail-open rate limiting (mirror `InvitePublicRateLimiter` exactly), capture
  the MINIMUM fields (email + derived domain + timestamp), and — critically — do NOT resolve or
  persist which tenant/owner the domain might belong to (that resolution-and-branch is itself the
  oracle in embryo; see Assumptions).
- **R-drift (SignupForm's dropped `code` field):** an existing, real gap (not introduced by this
  task) — `problem.code` is parsed by the wire but discarded by `SignupForm`'s hand-rolled
  `BffError` construction. Must be fixed for M6 to key the routing panel off
  `ERR_SIGNUP_INVITE_ONLY` specifically rather than "any 403", which would incorrectly also fire on
  a future, unrelated 403.
- **Risk (route (b) has no generic landing target):** `/join/[token]` requires a real token; there
  is no token-less entry point anywhere in the app. A "have an invite link?" affordance can only be
  informational copy pointing the visitor at the link they already have — building a generic
  `/join` page is out of scope (it would need new backend semantics no task here owns).
Related intent: FRONTDOOR-CONTEXT.md (milestone `frontdoor-persona-routing`) — persona **P4 Sam**,
"engineer at an existing customer, trying to get into a workspace their employer already pays for.
Actively failed." This task is P4 Sam's fix. Decision 1 (self-serve via a SCOPED mechanism, not a
flag flip) is owned by the sibling `scoped-self-serve-signup` task, not this one — this task never
proposes flipping `public_signup_enabled`. GLOSSARY: no existing term for "access request"; adds one
(§3).
Ground SHA: `8daf22c` (branch `feat/frontdoor-persona-routing`, cut from main @ 8daf22c; every cited
symbol opened directly, no serena cache reused from a prior session)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Replace the current terminal, single-message invite-only refusal with three static,
always-available next steps — sign in via SSO, open an existing invite link, or ask to be let in —
shown BOTH before any submission (on the bare `/signup` screen) AND in place of the current dead-end
text when the 403 actually fires. None of the three routes perform any new lookup or branch any
response on whether a given email/domain is already a customer — that stays `domain-aware-auth-
routing`'s job, next on this task's own critical path.
Framings weighed:
- **Static, always-visible 3-route panel + a minimal capture-only request-access endpoint (CHOSEN)**
  — zero new lookups, nothing about visibility/copy/response depends on server state; satisfies
  "surface routes before password entry" by making the panel unconditional rather than gated on the
  existing form's validation/submission state.
- Smart pre-check ("does this email's domain look like a customer?") before deciding what to show
  (REJECTED) — that is precisely the naive oracle the milestone context calls out by name, and it
  is `domain-aware-auth-routing`'s explicitly-owned deliverable (this task precedes it in the DAG on
  purpose; building it here would pre-empt and likely diverge from that task's real contract).
- Fix only the POST-submission 403 state, leave the bare page unchanged (REJECTED) — contradicts
  the explicit steer to surface routes before a password is typed; a P4-Sam-shaped visitor who never
  gets that far would still hit the same wall, just with nicer words after wasting a submit.
- "Request access" as a `mailto:` link with no server record (REJECTED) — the milestone decision is
  explicit that the email must be CAPTURED so a tenant owner CAN approve; a `mailto:` creates no
  record at all and is strictly worse than the affordance not existing (implies action was taken).
Must:
<must>
  - M1 A persistent "Already have access another way?" panel renders on `/signup`
    UNCONDITIONALLY — before any field is filled or submitted, not gated on any error state —
    containing exactly the three routes below, positioned so it is visible before the password
    field (satisfies "surface before a password they can't set" for every visitor, not only those
    who submit and fail).
  - M2 Route (a) "Sign in with SSO" is a static link to `/login`; IF the visitor has already typed
    something into the email field containing `@`, the link additionally carries
    `?domain=<the part after @>` and `LoginForm` reads that param ONCE (mirroring its existing
    one-shot `localStorage` seed effect) to pre-fill "Work email or domain" — additive, client-side
    only, zero new backend IO, the existing SSO preflight/handleSso/handleSamlSso logic is
    untouched.
  - M3 Route (b) "Have an invite link?" is static informational copy directing the visitor to open
    the link they received in their browser — no new route, no new form; `/join/[token]` and
    `JoinByDomainForm` are consumed exactly as they exist today, byte-unchanged.
  - M4 Route (c) "Request access" is a small inline form (one email input, pre-filled from the
    signup email field's current value if any) that `POST`s to a NEW BFF route
    `/api/auth/access-requests`, proxying verbatim to a NEW gateway endpoint `POST
    /admin/auth/access-requests`.
  - M5 The request-access endpoint ALWAYS returns the SAME success shape/status for ANY
    syntactically-valid email — regardless of whether that email or its domain is already a
    customer, already has a pending or verified domain claim, already has a pending invite, or is
    entirely unknown to the system. No branch in the request-access code path may read
    `resolve_verified_tenant`, any user/tenant table, or any domain-claim state.
  - M6 The request-access endpoint captures ONLY `{email, domain (derived, lower-cased apex),
    created_at}` into a new durable store — no password, no tenant_name, no attempted tenant/owner
    resolution at write time (that resolution-and-branch would itself become the oracle R-sec-2
    warns about).
  - M7 The endpoint is public/unauthenticated and rate-limited per-client-IP, fail-open on Redis
    outage — mirroring `InvitePublicRateLimiter` exactly (fixed 60s window, `INCR`+`EXPIRE`, a new
    `access_request_rpm` config knob with the same fail-fast-at-boot validator pattern as
    `invite_accept_rpm`).
  - M8 When the 403 `ERR_SIGNUP_INVITE_ONLY` actually fires on submit, `SignupForm` renders the
    SAME M1 panel IN PLACE of the current dead-end `role="alert"` text — never both, never the old
    copy. The form's already-typed `tenant_name`/`email`/`password`/`account_type` values are
    preserved (not cleared) so the visitor can still reconsider without retyping.
  - M9 `SignupForm` is fixed to read `problem.code` off the parsed error body (not just `status`)
    and gates M8 specifically on `code === "ERR_SIGNUP_INVITE_ONLY"` — mirroring
    `JoinByDomainForm`'s `bffCode()` precedent — so a future, unrelated 403/500 does NOT
    incorrectly trigger the routing panel.
  - M10 The S1 gate (`public_signup_enabled` check in `tenants/api/router.py:signup`), its
    pre-gate DB-IO-ordering property (verified-domain lookup first, zero further IO before the
    gate), and the `SIGNUP_INVITE_ONLY` ErrorSpec's status/code/title are BYTE-IDENTICAL after this
    task — this task changes only what the client does with the 403, never whether/when it fires.
</must>
Reject:
<reject>
  - R1 A syntactically malformed email to `POST /admin/auth/access-requests` -> standard FastAPI/
    pydantic `EmailStr` 422 (no new ErrorSpec; nothing is captured).
  - R2 A caller over the per-IP `access_request_rpm` limit -> "ERR_RATE_LIMITED" (429, reusing the
    existing generic `RATE_LIMITED` ErrorSpec verbatim; nothing new captured for that call).
  - R3 (anti-enumeration invariant, not a rejection but the property every "bad" case above must
    still hold) — a request-access call for an email that is already a full account, already
    domain-claimed, already invited, or entirely unknown NEVER differs in status, body, or observable
    timing-class from any other valid call -> always the same 202 shape (M5). Any code path that
    would make this differ is itself the defect this task exists to avoid re-introducing.
</reject>
After:
<after>
  - A visitor who lands on `/signup` with no invite sees three concrete next steps before typing a
    password, and the identical three steps again if they submit anyway and hit the 403 — never a
    dead end, for P4 Sam or anyone else.
  - The S1 invite-only gate, its trigger condition, its response timing/shape, and the existing
    verified-domain auto-join path (`resolve_verified_tenant`) are byte-identical to before this
    task — grep confirms only additive files/routes/columns.
  - A submitted "request access" is durably captured with a uniform, calm confirmation shown to
    every visitor regardless of what the gateway does or doesn't already know about that email.
  - No response anywhere in this change (status, body shape, copy, or observable timing) varies
    based on whether an email/domain is already a customer, already invited, or unknown.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Whether "request access" notifying a tenant owner IN-PRODUCT is required for THIS task's done
    bar, vs. this task shipping capture-only (durable row, no notification, no admin-visible queue
    yet) with owner-visibility deferred to a follow-up — lowest confidence because
    FRONTDOOR-CONTEXT.md names this explicitly as an OPEN question, not a decided one. I am
    assuming capture-only clears this task's bar: from the VISITOR's side, submitting the request
    IS the live next step the milestone promises ("every visitor... reaches a live next step"),
    even if the owner-side surface ships later. If wrong (a reviewer expects an actual
    notification or admin-visible triage list at freeze): cost = meaningful scope growth — either
    (a) an email to a resolved tenant owner (only even possible when the domain already has a
    verified claim, i.e. an existing tenant to notify — silently impossible for a genuinely-unknown
    domain, which is itself a branch M5 forbids at write time) or (b) a superadmin-visible queue —
    likely large enough to warrant splitting into its own follow-on task rather than folding in
    here. This is the flag to surface at the freeze decision.
  - [ ] Not persisting any resolved tenant/owner at write time (M6) is safe and sufficient — a
    human (support/ops) can grep the new table by domain later; resolving-and-storing a `tenant_id`
    FK now would look free but silently encodes exactly the signal (present for a real customer
    domain, null for a stranger) this task exists to avoid. Confidence: fairly high (directly
    follows from R-sec-2), but flagged because it does mean today's `access_requests` table is
    "dumber" than a reviewer might expect at first read.
  - [ ] "Have an invite link?" needs no new UI beyond static copy, because no token-less `/join`
    entry point exists anywhere in `app/(auth)/` (confirmed by directory listing + reading
    `JoinByDomainForm`) — if wrong (product actually wants a "paste your invite link" input that
    extracts the token and redirects), cost = one small additive input+redirect component; still
    zero new backend surface, since it would just parse+navigate to the existing route.
  - [x] SignupForm's `problem.code` gap (R-drift) is real and must be fixed for M9 — confirmed by
    reading both the hand-rolled `BffError` construction in `SignupForm.handleSubmit` and the
    `ProblemDetail`/`BffError` types in `resilient-fetch.ts`, which DO carry `code` on the wire.
  - [x] Deliberately declined to reuse gateway's `PUBLIC_EMAIL_DOMAINS` to hide/soften the SSO
    route for generic providers (gmail.com etc.) — always showing it costs nothing functionally,
    and duplicating a Python-side frozen list into the dashboard risks silent drift; confirmed by
    reading the module's own docstring (curated, "extend the frozenset as new providers appear" —
    a list that changes independently of any dashboard build).
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

> User: **P4 Sam** — an engineer at an existing Hydroa customer, arriving at `/signup` with a work
> email at a domain their employer already pays for, no invite in hand. Job-to-be-done: get into
> the workspace their employer already has, in one of the three ways that actually exist, without
> being told to "ask an existing member" with no way to find out who that is.

<scenarios>

```gherkin
Scenario: The three routes are visible before Sam types anything   # M1
  Given Sam has just landed on /signup and touched no field
  When the page renders
  Then the "Already have access another way?" panel with all three routes is already visible
  And it is positioned before the password field

Scenario: Sam picks SSO and his typed email domain comes along   # M2
  Given Sam has typed "sam@acme.com" into the signup email field
  When Sam clicks "Sign in with SSO"
  Then he is navigated to /login?domain=acme.com
  And the login page's "Work email or domain" field is pre-filled with "acme.com"
  And no new backend call was made to check whether acme.com is configured for SSO

Scenario: Sam clicks SSO with no email typed yet   # M2
  Given Sam has typed nothing into the signup email field
  When Sam clicks "Sign in with SSO"
  Then he is navigated to plain /login with no domain param
  And the existing SSO field seeds from localStorage as it already does today

Scenario: Sam looks for his invite link   # M3
  Given Sam does not remember getting an invite link but wants to check
  When Sam reads the "Have an invite link?" copy
  Then it tells him to open the link from his invite email in this browser
  And no new form or route was rendered for it

Scenario: Sam requests access and gets one calm confirmation   # M4,M5,M6
  Given Sam has typed "sam@acme.com" and clicks "Request access"
  When the request-access form submits
  Then the BFF returns 202 with a uniform success body
  And Sam sees exactly one calm confirmation message
  And a new access_requests row exists with email="sam@acme.com", domain="acme.com", a created_at

Scenario: The 403 dead end is replaced, not merely reworded   # M8,M9,M10
  Given Sam fills in tenant_name/email/password and submits, and public signup is disabled
  When the gateway returns 403 ERR_SIGNUP_INVITE_ONLY
  Then SignupForm renders the SAME three-route panel in place of the old dead-end text
  And Sam's typed tenant_name/email/password/account_type are still in the fields
  And the gate fired with the SAME timing/DB-IO-ordering as before this task (unchanged)

Scenario: An unrelated 403 does NOT trigger the routing panel   # M9
  Given some other, unrelated endpoint returns a 403 with a different code
  When SignupForm's error handling runs
  Then the routing panel is NOT shown for that unrelated code
  And the generic error message path is used instead

Scenario: A visitor whose domain is already a customer gets the identical response   # R3, anti-enumeration
  Given tenant Acme already holds a verified claim for acme.com
  When two different visitors POST /admin/auth/access-requests — one with an acme.com email, one
    with an entirely unknown, never-seen domain
  Then both responses are byte-identical in status and body shape (202, uniform success)
  And neither response, nor its timing class, reveals which (if either) domain is already a customer

Scenario: A visitor whose email already has a full account gets the identical response   # R3, anti-enumeration
  Given "existing@acme.com" already has a registered account
  When that email is POSTed to /admin/auth/access-requests
  Then the response is the SAME 202 uniform success as for a never-seen email
  And no distinguishing field, code, or message is present anywhere in the body

Scenario: Malformed email is rejected before any capture   # R1
  Given the request-access body contains "not-an-email"
  When the request is validated
  Then the response is 422 (standard pydantic validation shape)
  And no access_requests row is written

Scenario: Per-IP rate limit blocks a burst without leaking anything about the target email   # R2
  Given one client IP has already made access_request_rpm requests within the current window
  When it makes one more, for any email
  Then the response is 429 "ERR_RATE_LIMITED"
  And the response does not vary by which email was attempted
  And no access_requests row is written for the blocked call

Scenario: Redis outage fails open, same as every existing public rate limiter   # M7
  Given the rate limiter's Redis call raises RedisError/OSError
  When a request-access call arrives
  Then the call proceeds (fail-open, mirroring InvitePublicRateLimiter) and is captured normally
  And a WARNING is logged, never a 5xx surfaced to the caller

Scenario: All three routes are reachable and operable keyboard-only   # accessibility-as-research
  Given Sam navigates /signup using only Tab/Shift+Tab/Enter, no mouse
  When he tabs through the page
  Then the SSO link, the invite-link copy's focusable element (if any), and the request-access
    email input + submit button are all reachable in a logical order and activatable via keyboard
  And the request-access confirmation is announced via role="status" aria-live="polite" (screen
    reader hears it without moving focus), matching the existing JoinByDomainForm pattern
  # confidence: HEURISTIC — a structured keyboard walkthrough of the intended DOM order, not a
  # validated screen-reader user test; call out at freeze as unvalidated.
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
DASHBOARD — /signup (SignupForm.tsx + signup/page.tsx):
  A persistent panel (proposed data-slot="signup-alt-routes"), rendered UNCONDITIONALLY above the
  password field, containing:
    (a) <a href="/login"> or href="/login?domain=<derived>"> "Sign in with SSO instead"
        — domain derived client-side only (text after "@" in the current email field value, when
        present); LoginForm reads searchParams.domain ONCE (mirrors its existing localStorage
        one-shot seed) to pre-fill "Work email or domain". No new backend call.
    (b) static copy: "Have an invite link? Open it in this browser to join your team." — no new
        route; /join/[token] is consumed exactly as it exists today.
    (c) a request-access mini-form: one email <Input> (pre-filled from the signup email field) +
        submit -> POST /api/auth/access-requests {email} -> renders exactly one of:
          2xx -> role="status" aria-live="polite" calm confirmation (e.g. "We'll pass this along.")
          429 -> role="status" "You're going a little fast — try again in a moment."
          422 -> role="alert" inline "Enter a valid email" under the request-access input
        NEVER a message that names or implies account/domain state.

  On 403 ERR_SIGNUP_INVITE_ONLY from an actual /signup submit: SignupForm renders the SAME panel
  IN PLACE of today's dead-end role="alert" text (that copy is retired). Field values
  (tenant_name/email/password/account_type) are preserved. SignupForm's error handling reads
  `problem.code` (fixing the R-drift gap) and gates this specifically on
  code === "ERR_SIGNUP_INVITE_ONLY" — any other 403/5xx keeps today's generic globalError path,
  byte-unchanged.

BFF
POST /api/auth/access-requests   body: { email: string }
  -> proxies status + body verbatim to gateway POST /admin/auth/access-requests (mirrors
     app/api/auth/signup/route.ts's error-forwarding shape); never sets a session cookie; never
     calls /admin/auth/login.

GATEWAY
POST /admin/auth/access-requests   body: { email: EmailStr }        (public, unauthenticated)
  202 -> { ok: true }                                    # ALWAYS this exact shape (M5, R3)
  422 -> (default FastAPI/pydantic validation error)      # malformed email; nothing captured
  429 -> { code: "ERR_RATE_LIMITED" }                     # reuses the existing RATE_LIMITED ErrorSpec verbatim

Schema (additive, NEW table — no existing table touched):
  access_requests
    id          UUID PK
    email       TEXT NOT NULL
    domain      TEXT NOT NULL     -- derived server-side, lower-cased apex (text after '@')
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
  No FK to tenants/users/domain claims (deliberate — see §1 assumption: resolving+storing an owner
  at write time would itself become the signal this task exists to avoid). No status/handled_at
  column — owner-visible triage is an explicit OPEN follow-on, not silently assumed to exist.

Config (new knob, mirrors invite_accept_rpm's field_validator fail-fast-at-boot pattern):
  access_request_rpm: int   # GATEWAY_ACCESS_REQUEST_RPM

Rate limiter (new, mirrors InvitePublicRateLimiter EXACTLY):
  AccessRequestIpRateLimiter — fixed 60s window, per-client-IP (resolve_trusted_client_ip), Redis
  INCR+EXPIRE, FAIL-OPEN on RedisError/OSError (log WARNING, allow through).
  Key format: access_requests:rl:{ip}:{window_epoch_minute}

UNCHANGED (frozen; re-verified by reading, not modified by this task):
  tenants/api/router.py:signup — public_signup_enabled gate + its pre-gate DB-IO ordering
  (verified-domain lookup -> session.rollback() -> gate) — byte-identical.
  core/error_catalog.py:SIGNUP_INVITE_ONLY — status/code/title unchanged.
  domain_capture/* (resolve_verified_tenant, DNS-TXT proof, claims) — untouched, not read by the
  new endpoint.
  app/(auth)/join/[token]/page.tsx + JoinByDomainForm.tsx — untouched, only linked to.
  LoginForm.tsx's SSO preflight / handleSso / handleSamlSso bodies — untouched; only the
  query-param seed is additive to the existing one-shot useEffect seed.
```

SAFETY RULES (security task — binding):
- No code path in the request-access endpoint reads `resolve_verified_tenant`, any user/tenant/
  domain-claim table, or any account-existence signal — enforced by construction (M5, M6): the
  handler only validates the email shape and INSERTs.
- Every response from `/admin/auth/access-requests` for a syntactically-valid email is byte-
  identical (status + body) regardless of what the server does or doesn't know about that email or
  its domain (R3) — this is the property ≥1 adversarial verify at BUILD must specifically probe,
  including a timing-class check (no branch that does extra IO only for a "known" domain).
- The S1 gate (`public_signup_enabled`) and its response are never touched, never bypassed, never
  weakened — this task changes only what the CLIENT does after a 403, never the gate itself.
- The new endpoint is public/unauthenticated by design (a visitor has no session yet) — rate
  limiting is the only anti-abuse control, and it fails open (never fails closed into a DoS of the
  legitimate onboarding path).

Glossary deltas: **Access request** (NEW term) — a durable, unauthenticated capture of
`{email, domain, created_at}` recorded when a visitor without invite-only access asks to be let
in; distinct from a **Domain claim** (tenant-proven DNS-TXT ownership, `domain_capture`) and an
**Invite** (tenant-issued, token-scoped, `tenants`) — an access request proves nothing and grants
nothing by itself; it is a lead, not a credential. Whether/how it surfaces to a tenant owner is an
explicit open follow-on (§1 ⚠), not part of this term's definition yet.

Least-sure flag surfaced at freeze: [spec] whether "request access" must NOTIFY a tenant owner
in-product, or may ship as a stored lead only. Least confident because no owner-facing inbox or
notification surface for unauthenticated access requests was found in the dashboard during GROUND —
so the honest v1 is: persist the request and confirm receipt to the visitor, WITHOUT promising the
visitor that a human will see it. If in-product notification is required, this task grows a backend
seam (owner-facing queue + a notification path) and should be split rather than stretched. Cost if
wrong: the screen tells a visitor "we've passed this on" when nothing is routed anywhere — a worse
trust break than the dead end it replaces, so the COPY must not over-promise until the seam exists.
· [contract] the /pricing CTAs (`href: "/signup"`, both cards) are now in scope per the human
decision — they are a SECOND entry point into the same wall, and the contract must cover them, not
just the homepage.

Status: FROZEN @ v1 — approved by Tin Dang
Reported: no

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

**SCOPE OF THIS PASS (escalated, not silently decided):** this test-author dispatch was
briefed with dashboard-only tooling (Vitest + Testing Library + axe; "dashboard tests run
from apps/dashboard"; no pytest/uv/gateway convention named) and a Frontend Engineer persona.
The suite below therefore covers the **dashboard half** of the frozen §3 contract in full
(the SignupForm routing panel M1-M3/M8-M10, the request-access mini-form's client behavior
M4/R1/R2, the LoginForm domain-seed M2, and the /pricing second-entry-point note) plus the
**BFF proxy shape** of the new `/api/auth/access-requests` route (M4's proxy contract, R1/R2
passthrough, no-session-mutation). It does **NOT** cover the **gateway** bounded context
(`access_requests` entity/use-case/repository/router, `AccessRequestIpRateLimiter`,
`access_request_rpm` config validator, the additive migration) or the TRUE anti-enumeration
invariant (R3's "no branch may read `resolve_verified_tenant`/any tenant/user/domain-claim
table" — provable only where that logic could exist, i.e. pytest against the real handler).
**Open item for the orchestrator:** a separate gateway-scoped test-author pass is needed for
M4-M7, R1-R2 (server-side), and the binding R3 SAFETY RULE before this task can go to BUILD —
flagging this now rather than stretching a dashboard persona over backend security-invariant
testing it has no tooling access to verify.

Coverage target: ~85% statement coverage on the new/touched dashboard surface (SignupForm's
added panel branches, LoginForm's domain-seed effect, the new `access-requests` BFF route) —
mirrors this repo's typical component/route coverage bar on comparably-sized auth surfaces
(no repo-wide vitest coverage floor found to cite verbatim; declared, not measured, at RED).

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_panel_visible_before_password_unconditional: arrange render bare SignupForm / act none (no typing) / assert panel (data-slot="signup-alt-routes") present, precedes the password field in DOM order, and all three routes (SSO link, invite-link copy, request-access button) resolve inside it · covers: M1
  - test_sso_link_carries_typed_email_domain: arrange render SignupForm / act type "sam@acme.com" into the main email field / assert the SSO link's href === "/login?domain=acme.com" · covers: M2
  - test_sso_link_plain_when_no_email_typed: arrange render SignupForm / act none / assert the SSO link's href === "/login" (no domain param) · covers: M2
  - test_invite_link_copy_no_new_route: arrange render SignupForm / act none / assert the exact invite-link copy renders and exactly one textbox exists in the panel (no second form/input introduced by route (b)) · covers: M3
  - test_request_access_prefilled_from_signup_email: arrange render SignupForm / act type "sam@acme.com" into the main email field / assert the request-access mini-form's own input mirrors the same value · covers: M4 (contract detail)
  - test_request_access_success_shows_calm_uniform_confirmation: arrange mock POST /api/auth/access-requests -> 202 {ok:true} / act type email, click "Request access" / assert role="status" aria-live="polite" confirmation renders, names no account/domain state, and the BFF body sent was exactly {email} · covers: M4, M5, M6
  - test_request_access_malformed_email_422_inline_alert: arrange mock the route -> 422 pydantic shape / act type "not-an-email", submit / assert role="alert" "Enter a valid email" under the request-access input · covers: R1
  - test_request_access_rate_limited_429_status_message: arrange mock the route -> 429 {code:"ERR_RATE_LIMITED"} / act submit / assert role="status" "You're going a little fast — try again in a moment." (exact copy, contract has no "e.g." hedge here) · covers: R2
  - test_403_invite_only_replaces_dead_end_preserves_fields: arrange mock POST /api/auth/signup -> 403 {code:"ERR_SIGNUP_INVITE_ONLY", title:"Public signup is disabled; ask an existing member for an invite"} / act fill + submit / assert the old dead-end text is RETIRED (not in document), the panel is present, and tenant_name/email/password field values are preserved · covers: M8, M9, M10
  - test_403_unrelated_code_keeps_generic_error_path: arrange mock POST /api/auth/signup -> 403 with a DIFFERENT code / act fill + submit / assert the generic role="alert" globalError text renders (byte-unchanged path) AND the M1 panel remains present (per M1's "UNCONDITIONALLY" — flags and resolves a wording tension against §2's "panel is NOT shown" shorthand; see file-level KNOWN TENSION comment) · covers: M9 (negative)
  - test_panel_a11y_axe_no_serious_violations: arrange render bare SignupForm / act none / assert axe finds zero serious/critical violations (color-contrast disabled, matching landing-page.test.tsx's own precedent) · covers: accessibility bar (currently GREEN — a non-regression guard on the pre-existing form; becomes a real gate once the panel's markup lands)
  - test_panel_keyboard_reachable_in_order: arrange render bare SignupForm / act Tab-walk (bounded, 40 iterations) / assert the SSO link, request-access input, and its submit button are all reached · covers: accessibility-as-research scenario (HEURISTIC per §2's own confidence flag)
  - test_login_prefills_from_domain_query_param: arrange mock useSearchParams() -> {domain:"acme.com"}, no localStorage value / act render LoginForm / assert "Work email or domain" input has value "acme.com" · covers: M2 (LoginForm half)
  - test_login_no_domain_param_no_crash_falls_back_to_existing_seed: arrange mock useSearchParams() -> {} / act render LoginForm / assert the field stays empty, no throw (currently GREEN — degrade-safety guard; the existing localStorage-seed path itself is pinned byte-unchanged by tests/sso-login.test.tsx, untouched by this task) · covers: M2 (guard)
  - test_bff_access_requests_forwards_email_verbatim: arrange mock gateway POST /admin/auth/access-requests / act call the BFF handler with {email} / assert the captured upstream body === {email} · covers: M4
  - test_bff_access_requests_forwards_202_uniform_success: arrange mock gateway -> 202 {ok:true} / act call handler / assert BFF response is 202 {ok:true} verbatim · covers: M4, M5
  - test_bff_access_requests_forwards_422_validation_error: arrange mock gateway -> 422 pydantic shape / act call handler with a malformed email / assert status + body forwarded verbatim · covers: R1 (BFF passthrough half)
  - test_bff_access_requests_forwards_429_rate_limited: arrange mock gateway -> 429 {code:"ERR_RATE_LIMITED"} / act call handler / assert status 429, code forwarded · covers: R2 (BFF passthrough half)
  - test_bff_access_requests_never_mutates_session: arrange mock gateway 202 + a login-call spy / act call handler / assert no Set-Cookie header and /admin/auth/login was never called · covers: M4 (safety)
  - test_bff_access_requests_proxy_neutral_across_emails: arrange mock gateway -> identical 202 body for any email / act call handler twice with different emails / assert both BFF responses are byte-identical · covers: R3 (BFF-layer boundary only — see SCOPE OF THIS PASS note; the true invariant is gateway-side, out of scope here)
  - test_pricing_cta_starter_and_team_point_to_signup: arrange render the pricing page / act none / assert both "Get started" CTAs (Starter, Team) have href="/signup" · covers: contract's "Least-sure flag" pricing note (currently GREEN — a regression pin, not new Build work; the fix lands transitively via SignupForm since both CTAs already target /signup)
</test_plan>

**RED evidence** (`node_modules/.bin/vitest run tests/signup-refusal-router.test.tsx
tests/login-domain-query-seed.test.tsx tests-bff/access-requests-route.test.ts
tests/pricing-cta-signup-entry.test.tsx`, run from `apps/dashboard/`, real local binary not npx):
```
 RUN  v4.1.8 /Users/tindang/workspaces/tind-repo/ai-proxy/apps/dashboard

 FAIL  |bff| tests-bff/access-requests-route.test.ts [ tests-bff/access-requests-route.test.ts ]
Error: Failed to resolve import "@/app/api/auth/access-requests/route" from
"tests-bff/access-requests-route.test.ts". Does the file exist?
  35 |  import { POST as accessRequestHandler } from "@/app/api/auth/access-requests/route";
     |                                                ^
 ❯ tests-bff/access-requests-route.test.ts:37:45
 → whole file RED via MODULE_NOT_FOUND (route not built yet) — established true-red convention
   also used by tests-bff/join-by-domain-route.test.ts.

 FAIL tests/signup-refusal-router.test.tsx > SignupForm — M1 > test_panel_visible_before_password_unconditional
Error: Panel [data-slot="signup-alt-routes"] not found — M1 "Already have access another way?" panel is missing
 FAIL tests/signup-refusal-router.test.tsx > M2 > test_sso_link_carries_typed_email_domain          (same: panel missing)
 FAIL tests/signup-refusal-router.test.tsx > M2 > test_sso_link_plain_when_no_email_typed            (same: panel missing)
 FAIL tests/signup-refusal-router.test.tsx > M3 > test_invite_link_copy_no_new_route                 (same: panel missing)
 FAIL tests/signup-refusal-router.test.tsx > request-access > test_request_access_prefilled_from_signup_email   (same: panel missing)
 FAIL tests/signup-refusal-router.test.tsx > request-access > test_request_access_success_shows_calm_uniform_confirmation (same: panel missing)
 FAIL tests/signup-refusal-router.test.tsx > request-access > test_request_access_malformed_email_422_inline_alert       (same: panel missing)
 FAIL tests/signup-refusal-router.test.tsx > request-access > test_request_access_rate_limited_429_status_message        (same: panel missing)
 FAIL tests/signup-refusal-router.test.tsx > 403 > test_403_invite_only_replaces_dead_end_preserves_fields
Error: expect(element).not.toBeInTheDocument()
expected document not to contain element, found <p role="alert" aria-live="polite" ...>
  Public signup is disabled; ask an existing member for an invite
</p> instead
 → proves today's dead-end text is STILL rendered on a real 403 round trip (M8/M9/M10 not built)
 FAIL tests/signup-refusal-router.test.tsx > 403 > test_403_unrelated_code_keeps_generic_error_path  (panel missing)
 FAIL tests/signup-refusal-router.test.tsx > a11y > test_panel_keyboard_reachable_in_order            (panel missing)

 FAIL tests/login-domain-query-seed.test.tsx > test_login_prefills_from_domain_query_param
Error: expect(element).toHaveValue("acme.com")  →  Received: ""
 → proves LoginForm never reads the ?domain= search param today (only localStorage)

 Test Files  3 failed | 1 passed (4)
      Tests  12 failed | 3 passed (15)   [access-requests-route.test.ts's 6 tests: 0 ran, whole-file RED]
```
The 3 GREEN tests today are explicitly documented pre-existing-green (non-red-by-design):
`test_panel_a11y_axe_no_serious_violations` (axe on the current, already-accessible form —
becomes a real gate once the panel lands), `test_login_no_domain_param_no_crash_falls_back_to_existing_seed`
(degrade-safety guard), and `test_pricing_cta_starter_and_team_point_to_signup` (regression pin,
documented in its own file header). Every other test is RED for the stated, correct reason —
missing implementation, not a broken harness. **Regression check:** the 8 pre-existing sibling
suites this task must not disturb (`tests/signup.test.tsx`, `tests/signup-form-joined-outcome.test.tsx`,
`tests/signup-account-type.test.tsx`, `tests/sso-login.test.tsx`, `tests/login.test.tsx`,
`tests/saml-login-affordance.test.tsx`, `tests-bff/signup-joined-forward.test.ts`,
`tests-bff/signup-account-type-route.test.ts`) — 25/25 still green after adding this suite.

**Cross-task-drift risk flagged for BUILD:** `tests/signup.test.tsx` and
`tests/signup-form-joined-outcome.test.tsx` both call `screen.getByLabelText(/email/i)` for the
MAIN signup email field — a bare partial-regex match. Once the request-access mini-form's own
email input exists on the same page (M4), ANY accessible name containing "email" (a near-certain
choice for an email field) makes that query ambiguous and breaks those FROZEN tests. BUILD must
give the two inputs non-colliding accessible names without touching those tests — this suite's
own queries were written to route around the collision (`document.getElementById("signup_email")`
for the main field, `within(panel-by-data-slot).getByRole("textbox")` for the mini-form's), so
BUILD has a working existence proof but still owns solving the disambiguation for the two
pre-existing frozen suites.

Tests live in: `apps/dashboard/tests/signup-refusal-router.test.tsx`,
`apps/dashboard/tests/login-domain-query-seed.test.tsx`,
`apps/dashboard/tests/pricing-cta-signup-entry.test.tsx`,
`apps/dashboard/tests-bff/access-requests-route.test.ts` · MUST run red (missing implementation)
before Build — confirmed above. Gateway-side tests: NOT YET WRITTEN (see SCOPE OF THIS PASS).

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
- [x] the green was EARNED, not gamed — refute-read by 2 independent add-verify agents (no-leak + earned-green), both CLEAR — see below
- [x] concurrency / timing of the risky operation is safe — rate-limiter INCR+EXPIRE race bounded to ≤1 double-count (documented, mirrors InvitePublicRateLimiter)
- [x] no exposed secrets, injection openings, or unexpected dependencies — store-only endpoint; input validated
- [x] layering & dependencies follow CONVENTIONS.md — clean hexagonal split in the new access_requests/ context mirroring domain_capture
- [x] a person reviewed and approved the change — HUMAN GATE (autonomy: conservative): Tin Dang approved PASS on 2026-07-21, after being shown both adversarial verify verdicts (CLEAR/CLEAR) and the 2 non-blocking flags (fail-open rate-limiter M7 trade-off; M10 shared-branch wording drift). Tin accepted the fail-open limiter as contracted, not as a logged waiver.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] a would-be signer who is a member of an existing tenant is routed to a live next step (SSO / invite link / request-access), never a dead end — confirmed by dashboard SignupForm [data-slot="signup-alt-routes"] panel + green-bar `vitest (ci.yml dashboard job)`: full suite 1681 passed + tests-bff/access-requests-route.test.ts 6/6.
- [x] the refusal reveals NOTHING about tenant/domain existence: routing is client-side static, and the one server endpoint returns uniform 202 {"ok":true} for any valid email (zero SELECT, zero branch in use-case + repo) — confirmed by add-verify no-leak lens, proven both ways (existing account + verified domain claim) + green-bar `pytest (Makefile:test / ci.yml 'Tests' step)`: full gateway suite green in 5 fg chunks, tests/access_requests/ 6/6.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — the new access_requests/ context (domain/application/infra/api), the SignupForm alt-routes panel, and the problem.code plumbing are all referenced and reachable; confirmed by both verify agents + full-suite green.
- [x] DEAD-CODE (code) — no new unused/orphaned symbol.
- [x] SEMANTIC (prose / non-code) — honest store-only copy read in full: the request-access path stores the request and says so, promising no account.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every symbol §3 CONTRACT cites still resolves — ProblemDetail/BffError (resilient-fetch.ts), the refusal codes, resolve_verified_tenant all resolve; confirmed by both verify agents.
- [x] anchor that moved since Ground SHA, named not silent: §3 M10 ("signup() byte-identical") is literally false on the shared branch because the SIBLING task scoped-self-serve-signup inserted a personal branch AHEAD of the S1 gate — but M10's OBSERVABLE holds (branch gated on account_type=="personal" AND default-OFF; verified-domain-lookup-first ordering intact; invite-only 403 unchanged). NOT this task's change; reconcile M10 wording at merge. See [[domain-routing-unification-cr-and-tamper]].

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: 2 independent add-verify agents (opus) — ac3ac0 (no-leak lens) + a7d6a3 (earned-green lens) · adversarially checked: (1) refusal existence-oracle across all observables (none — uniform 202 by construction), access_requests store-only (only add+commit, no tenant/user/session, no read of resolve_verified_tenant), rate-limiter present/per-IP/fail-open-contracted. (2) vacuous-test hunt: the problem.code tests would FAIL if code were dropped again (both fall to generic path), the a11y test asserts the alt-routes panel EXISTS before auditing (not audit-an-empty-form); R3 tests use a real registered account + a real verified TenantDomainClaimRow, not stubs. Both CLEAR / no HARD-STOP.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: 2 independent add-verify agents (ac3ac0 no-leak + a7d6a3 earned-green)
1. Security: CLEAR — no existence-oracle (uniform 202, zero SELECT/branch); store-only reveals nothing; no injection; ≥2 independent adversarial verifies, both CLEAR, no HARD-STOP finding.
2. Concurrency: CLEAR — rate-limiter INCR+EXPIRE race bounded to ≤1 double-count (documented, mirrors InvitePublicRateLimiter); FAIL-OPEN on Redis/OS error is deliberate & contracted (M7 — fail-closed would DoS onboarding).
3. Architecture: CLEAR — clean hexagonal split in access_requests/ mirroring domain_capture; all symbols wired; no dead code.
Verdict: PASS (HUMAN gate: Tin Dang approved 2026-07-21)
Residue: none blocking. 2 non-blocking flags carried forward: (a) M10 wording reconciliation at merge (observable holds); (b) rate-limiter deliberately fail-open — Tin accepted as the contracted M7 trade-off (fail-closed would DoS onboarding), recorded as clean, not as a waiver.
Binding: <yes — mechanical | advisory — <sensitivity>>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-21

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned
- [human] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

