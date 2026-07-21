# TASK: Route on work-email domain before the password field, without leaking customer identity (SECURITY)

slug: domain-aware-auth-routing · created: 2026-07-20 · stage: production
milestone: frontdoor-persona-routing
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/dashboard/components/auth/SignupForm.tsx:SignupForm` — carries the SHIPPED
  `[data-slot="signup-alt-routes"]` panel (signup-refusal-router §3 FROZEN @ v1, M1): the three
  routes (a) SSO link, (b) static invite-link copy, (c) request-access mini-form, rendered
  UNCONDITIONALLY above the password field. Its header comment states the frozen property
  verbatim: "None of the three routes' visibility, copy, or behavior varies by server state
  (R-sec-1 anti-enumeration)." THIS TASK'S SURFACE — the panel exists; what is missing is any
  awareness of WHICH of the three routes fits the visitor.
- `apps/dashboard/components/auth/SignupForm.tsx:deriveSsoDomain(rawEmail: string): string` —
  already-shipped PURE, client-side, zero-IO derivation: text after the LAST "@", trimmed; ""
  when no "@". The existing seam this task classifies on top of. No network call anywhere in it.
- `apps/dashboard/components/auth/LoginForm.tsx:resolveSsoDomain(raw: string): string` — the
  sibling derivation (lowercases in addition to trimming; accepts a bare domain as-is). Two
  near-duplicate derivations exist today — a real, existing drift seam (→ Risks).
- `apps/dashboard/components/auth/LoginForm.tsx:validateSsoDomain(domain: string): string | null`
  — pure shape check (`/^[^\s@]+\.[^\s@]+$/`); returns null when plausible. Client-side only.
- `apps/dashboard/components/auth/LoginForm.tsx:handleSso` + `SSO_NOT_CONFIGURED_MSG` +
  `SSO_PREFLIGHT_TIMEOUT_MS` — the SHIPPED SSO preflight: `fetch("/api/auth/oidc/login?domain=X",
  {redirect:"manual"})`, where a 4xx renders "That domain isn't set up for single sign-on." and a
  non-4xx navigates. **This is a live, unauthenticated, per-domain existence oracle** (→ Risks,
  HARD-STOP escalation). This task does NOT consume it and does NOT modify it.
- `apps/dashboard/app/api/auth/oidc/login/route.ts:GET` — the pre-auth BFF relay; forwards ONLY
  the `domain` param (via `sanitizeDomain`) and relays 4xx "verbatim … e.g. 404
  ERR_OIDC_NOT_CONFIGURED" (its own docstring). The transport of the oracle above.
- `apps/gateway/src/gateway/auth/api/oidc_router.py:oidc_login` — resolves the tenant CLAIM-FIRST
  via `resolve_verified_tenant` (domain-routing-unification §3 FROZEN @ v2/CR-v2, M1/M2/M8) and
  raises `OIDC_NOT_CONFIGURED` (404) when nothing resolves. FROZEN, untouched by this task.
- `apps/gateway/src/gateway/core/error_catalog.py:OIDC_NOT_CONFIGURED` /
  `OIDC_TENANT_NOT_CONFIGURED` — both `ErrorSpec(404, "ERR_OIDC_NOT_CONFIGURED", …)`. The two
  share a code, so the 404 does not itself distinguish platform-vs-tenant — but 302-vs-404 still
  separates "verified claim + OIDC configured" from everything else.
- `apps/gateway/src/gateway/domain_capture/domain/public_email_domains.py:PUBLIC_EMAIL_DOMAINS`
  (frozenset, 22 entries) + `is_public_email_domain(domain: str) -> bool` — a PURE, zero-IO,
  compile-time-constant classifier over a curated public-provider list (member-verified-recognition
  §3 FROZEN @ v1, R-sec-5). **The existence-free signal this task routes on.** No DB, no network,
  no tenant state — its answer is computable offline by anyone.
- `apps/gateway/src/gateway/domain_capture/domain/domain_validation.py:normalize_domain(raw) -> str`
  — pure, zero-IO hostname normalization (lowercase/trim, ≥2 labels, ≤253 chars, no IP literal),
  raises `DomainInvalidError`. The canonical shape-validity predicate; the dashboard has only the
  looser `validateSsoDomain` regex (→ Risks).
- `apps/gateway/src/gateway/domain_capture/application/verified_domain_resolution.py:
  resolve_verified_tenant_for_raw_domain` — the ONE existence predicate. Named here **so §3 can
  forbid it by name**: nothing this task builds may reach it.
- `apps/dashboard/lib/resilient-fetch.ts:ProblemDetail` (`{type?, title, status, code?}`) +
  `BffError` — the shared typed-error shape; `code` now survives backend→BFF→render
  (signup-refusal-router fixed the drop). This task adds no new error path over it.
- `apps/dashboard/lib/bff-validation.ts:sanitizeDomain(raw: string | null): string | null` —
  length + forbidden-char bound on a domain param; already applied on the `?domain=` hop.

Context (working folder): `apps/dashboard/components/auth/` (SignupForm, LoginForm,
JoinByDomainForm) · `apps/dashboard/lib/` (bff-validation, resilient-fetch) · read-only for
grounding: `apps/gateway/src/gateway/domain_capture/domain/` and `auth/api/oidc_router.py`.
No config knob, no migration, no new table is in this task's working folder.

Honors (patterns / conventions):
- The anti-enumeration floor set by scoped-self-serve-signup §3 (FROZEN @ v1): an unauthenticated
  caller learns NOTHING about whether a given email/domain is already registered — identical
  status, body shape, and dominant cost regardless of server knowledge.
- signup-refusal-router §3 (FROZEN @ v1): the three routes render unconditionally; the S1 gate and
  its response are never touched; routing today is CLIENT-SIDE STATIC — the server makes no
  routing decision. This task PRESERVES that server-silence rather than ending it.
- appsec-engineer persona: "an unknown id and a cross-tenant id return the IDENTICAL response …
  this is what closes the enumeration oracle, not an after-the-fact check" — applied here to
  domains rather than ids.
- Pure-domain-predicate discipline (backend-architect): classification lives in a zero-IO module,
  not inline in a component.

Seams consulted: none — no `.add/SEAMS.md` entry governs email-domain classification.

Anchors the contract cites: `SignupForm`, `deriveSsoDomain`, the `[data-slot="signup-alt-routes"]`
panel, `resolveSsoDomain`, `validateSsoDomain`, `PUBLIC_EMAIL_DOMAINS`, `is_public_email_domain`,
`normalize_domain`, `resolve_verified_tenant_for_raw_domain` (cited as FORBIDDEN),
`handleSso`/`SSO_NOT_CONFIGURED_MSG` (cited as UNTOUCHED), `ProblemDetail`, `sanitizeDomain`.

Issues/Risks (→ feed §1):
- **R-a (SECURITY, pre-existing, HARD-STOP escalation — not introduced by this task).** CORRECTED
  after an adversarial advisor review caught a stale anchor in this section's first draft; the
  precise behavior of `GET /auth/oidc/login?domain=X` (`oidc_router.py:oidc_login`, read at
  Ground SHA) is:
      verified claim + enabled OIDC config          -> 302 to the IdP
      verified claim + NO enabled config            -> 403 ERR_OIDC_DOMAIN_NOT_MAPPED   (line 173)
      no verified claim + legacy resolver hit       -> 302
      no verified claim + env `oidc_enabled` TRUE   -> 302 via the env fallback         (line 194)
      no verified claim + env `oidc_enabled` FALSE  -> 404 ERR_OIDC_NOT_CONFIGURED      (line 201)
  Two consequences, both material to this task:
  (i) **The residual oracle is deployment-dependent.** When env-level `oidc_enabled` is TRUE,
      nearly every domain returns 302 and the preflight leaks nothing. The oracle bites only when
      env OIDC is disabled — so its severity is a function of deployment config, not code alone.
      My first draft asserted a flat "302-vs-404 = customer" and was WRONG to state it unqualified.
  (ii) **`OIDC_DOMAIN_NOT_MAPPED`'s own stated property does not appear to hold.** The comment at
      lines 171-172 says the claimed-but-unconfigured 403 is "the same fail-closed rejection as an
      unclaimed domain (M2: no oracle between the two)", and the docstring repeats it. But an
      UNCLAIMED domain never reaches line 173 — it falls through to the env check and yields 404
      (or 302). **403 and 404 are distinguishable**, so a verified-domain-claim existence oracle
      remains open on the raw endpoint in exactly the deployment shape where it matters. The
      dashboard preflight happens to mask this (it collapses all 4xx to one message), but the
      endpoint is directly reachable without the dashboard.
  This is a defect in a FROZEN contract's delivered property (domain-routing-unification §3
  v2/CR-v2 M2), not in this task's diff. This task must (i) not consume the preflight as a routing
  signal and (ii) not silently amplify it. Escalated for a human decision — never resolved by
  guessing, and NOT fixed here (fixing it means editing a frozen contract's surface).
  CONFIDENCE NOTE: (ii) is a code-read conclusion, NOT test-confirmed — I did not run the suite
  (test runs poison the ADD gate scope-walk). It must be confirmed by an actual request before
  anyone acts on it.
- **R-b:** the tempting design — ask the server "does this domain have a tenant?" to pick a route
  — IS R-a generalized to every visitor, on the front door, by construction. Any server-side
  adaptation whose output depends on tenant/claim/user state is an oracle no matter how the
  response is shaped, because the ADAPTATION ITSELF is the observable.
- **R-c:** `PUBLIC_EMAIL_DOMAINS` lives in Python; the dashboard has no TS equivalent. A
  client-side classifier needs the list on the client — a SECOND copy that can silently drift from
  the Python one (the exact two-copies failure mode appsec-engineer's ceiling-predicate rule
  forbids). Needs an explicit parity guard, not good intentions.
- **R-d:** two near-duplicate domain derivations already exist (`deriveSsoDomain` trims only;
  `resolveSsoDomain` trims + lowercases + accepts a bare domain). Classifying on the un-lowercased
  one would mis-classify `Bob@GMAIL.com` as corporate. Normalization must be pinned.
- **R-e:** signup-refusal-router froze the three routes as rendered UNCONDITIONALLY. Any design
  that HIDES a route on a classification contradicts a frozen contract. Emphasis/ordering may
  vary; presence may not.
- **R-f:** the curated list is inherently incomplete (22 entries) — a public provider not on it
  classifies as corporate. This must be a graceful mis-route (visitor still sees every route), not
  a failure, and must never be "fixed" by asking the server.
- **R-g:** `validateSsoDomain`'s regex is looser than `normalize_domain` (accepts single-label-ish
  and over-long values the gateway rejects). Classification must fail SAFE (neutral) on anything
  it cannot confidently shape-check, never guess.

Related intent: milestone `frontdoor-persona-routing` — "a member of an existing tenant is routed
to SSO, their invite link, or a request-access path instead of a dead end." GLOSSARY: **Domain
claim** (DNS-TXT-proven tenant ownership), **Access request** (unauthenticated lead, proves
nothing), **pending-personal-signup** (mailbox-proof BEFORE creation). The WHY: signup-refusal-
router made all three routes REACHABLE; every visitor now sees all three with equal weight and
must self-diagnose. This task makes the panel *legible* — matching route to visitor — while
keeping the server silent. Its dependent task `unified-signin-entry` inherits this posture.

Ground SHA: `9421827` — symbols cited by name; any line reference is "as of" this commit.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Shape-aware, existence-blind route emphasis — the signup panel leads with the route that
fits the SHAPE of the typed email domain (public provider vs corporate), computed entirely on the
client from a static list, with ZERO network calls and ZERO server knowledge of who is a customer.

Framings weighed:
1. **Shape-aware / existence-blind, client-only (CHOSEN).** Classify the typed domain as
   `public` (on the curated provider list) / `corporate` (well-shaped, not on the list) /
   `unknown` (nothing typed, malformed, no "@"). Emphasis follows the class. The decision is a
   pure function of the typed string plus a compile-time constant — nothing else. An adversary
   learns nothing because *the product knows nothing and asks nothing*.
   WHY CHOSEN: it makes the anti-enumeration invariant TRIVIALLY provable — "the routing decision
   issues zero network requests" is a property you can assert directly, and it cannot silently rot
   the way "this endpoint doesn't branch on DB state" can.
2. **Existence-aware server routing** (`GET /auth/routing?domain=` → "sso" | "invite" | "signup").
   REJECTED: this is R-a generalized to every visitor. The adaptation itself is the observable —
   no response-shaping fixes it, because the *usefulness* of the feature is exactly the leak.
3. **Verified-DNS-claim-aware routing** — route on a claim the visitor could already observe by
   querying DNS themselves, arguing zero marginal disclosure. REJECTED for v1: (i) it still
   requires a server call whose answer varies by customer state, so the invariant becomes
   "argue about marginal cost" instead of "no call was made"; (ii) it turns an O(DNS) probe into a
   cheap O(HTTP) one at our expense; (iii) a *pending* claim has no published TXT record at all, so
   the "already observable" premise is false for exactly the tenants most worth protecting.
4. **Timing/uniform-response server routing** (respond identically, vary only out-of-band).
   REJECTED: it cannot deliver the feature at all — the visitor must SEE the adaptation, so it
   cannot be out-of-band. Noted to show the uniform-response trick that saved
   scoped-self-serve-signup does not transfer to a routing UI.

Must:
<must>
  - M1 — A pure, zero-IO client module classifies a raw email/domain string into exactly one of
    `"public" | "corporate" | "unknown"`. It performs no fetch, no XHR, no dynamic import, and
    reads no cookie, storage, or server-rendered state.
  - M2 — Classification normalizes before deciding: trim, lowercase, take the text after the LAST
    "@" (a bare domain with no "@" is accepted as-is, matching `resolveSsoDomain`). `Bob@GMAIL.com`
    and `bob@gmail.com` classify identically.
  - M3 — `public` iff the normalized domain is a member of the client-side public-provider list,
    which is value-for-value IDENTICAL to the gateway's `PUBLIC_EMAIL_DOMAINS`. A guard asserts
    the two lists match, so they cannot silently drift (R-c).
  - M4 — `corporate` iff the normalized domain is well-shaped (the `normalize_domain` rules:
    ≥2 labels, each 1–63 chars of [a-z0-9-] without leading/trailing hyphen, total ≤253, not a
    bare IP literal) AND not on the public list.
  - M5 — `unknown` for everything else: empty, no domain part, or any value failing M4's shape
    rules. `unknown` is the SAFE DEFAULT and renders exactly today's shipped neutral panel.
  - M6 — All three routes (SSO link, invite-link copy, request-access form) remain present in the
    DOM in EVERY classification. Classification changes only ORDER and EMPHASIS — never presence
    (this is what keeps the frozen signup-refusal-router M1 true).
  - M7 — `public` leads with self-serve signup ("this looks like a personal address — create your
    own workspace") and de-emphasizes SSO, which is meaningless on a public provider.
  - M8 — `corporate` leads with the three team routes in the order SSO → invite link → request
    access ("if your team already uses Hydroa, here's how to get in").
  - M9 — The three routes' own copy, hrefs, and behavior stay BYTE-IDENTICAL to the shipped
    signup-refusal-router contract. This task adds only a classification-derived lead-in line and
    an ordering — strictly additive, so it cannot contradict a frozen contract.
  - M10 — Classification is recomputed as the visitor types, purely locally, with no debounce
    needed and no request issued at any keystroke.
  - M11 — THE ANTI-ENUMERATION INVARIANT (testable property): *for any two domains D1 and D2 that
    fall in the same shape class, the rendered panel is byte-identical regardless of whether
    either domain has a tenant, a verified claim, an SSO config, or any user.* Equivalently: the
    rendered output is a pure function of (typed string, static list) — server state is not an
    input. This is the property the adversary scenario probes.
  - M12 — The build introduces NO gateway route, NO BFF route, NO schema change, and NO config
    knob. The server surface after this task is byte-identical to before it.
</must>

Reject:
<reject>
  - R1 — Any call to `resolve_verified_tenant_for_raw_domain`, `resolve_verified_tenant`, or any
    tenant/user/claim lookup from a routing path -> "ROUTE_EXISTENCE_LOOKUP_FORBIDDEN"
    (build-time/contract-level refusal; no such path may exist to return an error at runtime).
  - R2 — Any network request issued to compute or refresh the classification (including reusing
    the `handleSso` preflight as a routing signal) -> "ROUTE_NETWORK_PROBE_FORBIDDEN".
  - R3 — Empty input / no domain part -> class `"unknown"` -> "ROUTE_NEUTRAL_NO_DOMAIN"
    (a neutral render, never an error shown to the visitor).
  - R4 — Malformed or non-hostname-shaped domain (spaces, single label, IP literal, >253 chars)
    -> class `"unknown"` -> "ROUTE_NEUTRAL_MALFORMED_DOMAIN" — fail safe to neutral, never guess.
  - R5 — A classification that HIDES or removes any of the three routes -> "ROUTE_HIDES_ROUTE"
    (contradicts frozen signup-refusal-router M1; refused at contract level).
  - R6 — Client and gateway public-provider lists differing by any entry ->
    "ROUTE_PROVIDER_LIST_DRIFT" (a failing parity guard, surfaced at build time, not runtime).
  NOTE (deliberate, flagged): these are contract-level and build-time refusals, not HTTP error
  responses, because M12 adds no server surface. There is no new endpoint for a 4xx to come from
  — the correct "rejection" for a security property of this shape is a path that CANNOT be built,
  enforced by a test that fails if it appears. See §1 assumption 2.
</reject>

After:
<after>
  - A visitor typing a personal address sees "create your own workspace" led first; a visitor
    typing a work address sees the three team routes led first — and neither render tells anyone,
    including the visitor, whether that domain is already a Hydroa customer.
  - The gateway's observable surface is unchanged: the same routes, the same responses, the same
    number of endpoints as before this task.
  - `unified-signin-entry` inherits a named, tested, zero-IO classification seam and the M11
    invariant, instead of re-deriving domain routing at a second entry point.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ 1. **That shipping shape-aware emphasis is acceptable while the PRE-EXISTING `handleSso`
     preflight oracle (R-a) stays open — and in fact routes MORE visitors toward it.** Lowest
     confidence because it is not a technical question but a risk-acceptance one that is not mine
     to make: this task's headline is "without leaking customer identity," yet the SSO button it
     will emphasize sits directly on top of a live 302-vs-404 existence oracle protected by a
     different frozen contract. If wrong: we ship a task whose stated security property is true of
     its OWN code and false of the screen as a whole — the exact "green tests, false headline"
     failure mode. Cost of the fix is NOT in this task (it would change shipped SSO UX and touch a
     frozen contract) → REQUIRES A HUMAN DECISION: (a) accept + track as a separate task,
     (b) split a preflight-hardening task into this milestone BEFORE this one, or (c) de-emphasize
     SSO in the corporate branch until it is fixed.
  - [ ] 2. That contract-level/build-time refusals (R1/R2/R5/R6) are an acceptable substitute for
     HTTP error codes on a task that deliberately adds no server surface — confirm or deny; the
     alternative is inventing an endpoint purely to have something to return 4xx from, which would
     itself be the oracle. Recommend: accept, and make each refusal a real failing test.
  - [ ] 3. That duplicating `PUBLIC_EMAIL_DOMAINS` into TypeScript (guarded by a parity test that
     reads the Python file) beats the alternatives — a build-time codegen step, or a zero-IO
     gateway endpoint that returns the static list. Recommend: TS copy + parity test (simplest,
     keeps the zero-network property absolute). If wrong: swap to codegen, no contract change.
  - [ ] 4. That the 22-entry list is good enough for v1 and a missing provider (e.g. a regional
     ISP) mis-classifying as `corporate` is an acceptable, graceful mis-route — the visitor still
     sees every route, just ordered oddly. If wrong: extend the frozenset, additive, no reshape.
  - [ ] 5. That `corporate` should lead with SSO rather than "request access". Ordering is a UX
     judgement made without research here; it is cheap to reorder and changes no invariant.
  - [ ] 6. That this task is dashboard-only. Confirmed against the code (M12) — no gateway change
     is needed to deliver the milestone's routing goal, because all three destinations already
     exist and work.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Personal address leads with self-serve signup   # M1, M2, M7
  Given a visitor is on /signup with an empty form
  When they type "Bob@GMAIL.com" into the email field
  Then the [data-slot="signup-alt-routes"] panel classifies the domain as "public"
  And the self-serve-signup path is presented first
  And the SSO route is de-emphasized (still present, ordered last)
  And zero network requests were issued by the classification

Scenario: Case and whitespace do not change the class   # M2
  Given a visitor is on /signup
  When they type "  bob@GmAiL.CoM  " into the email field
  Then the classification is "public"
  And it is identical to the classification of "bob@gmail.com"

Scenario: Corporate address leads with the three team routes   # M4, M8
  Given a visitor is on /signup
  When they type "dana@acme-corp.com" into the email field
  Then the classification is "corporate"
  And the panel presents SSO, then the invite-link copy, then request-access, in that order
  And zero network requests were issued by the classification

Scenario: A corporate domain WITH a verified claim renders identically   # M11 (the invariant)
  Given the tenant "Acme" holds a VERIFIED domain claim on "acme-corp.com"
  And a second domain "nobody-here.example" has no tenant, no claim, and no SSO config
  When a visitor types "dana@acme-corp.com" and, separately, "dana@nobody-here.example"
  Then the rendered [data-slot="signup-alt-routes"] panel markup is BYTE-IDENTICAL for both,
       apart from the domain string echoed back from what the visitor themselves typed
  And no request carrying either domain was issued in either case

Scenario: Adversary probing a domain list learns nothing   # M11, R2 — THE ADVERSARY CASE
  Given an attacker holds a list of 10,000 candidate customer domains
  And some of those domains have verified claims, tenants, and SSO configured
  And the rest are strangers to the platform
  When the attacker scripts typing each domain into the signup email field and records
       (a) every outbound request, (b) the rendered panel, and (c) the time to render
  Then no outbound request is issued for ANY domain
  And the rendered panel differs ONLY by shape class (public vs corporate vs unknown),
       which the attacker could compute offline from the public provider list alone
  And the attacker's posterior on "is this domain a Hydroa customer" is UNCHANGED from its prior
  And no tenant, claim, user, or SSO record was read on the server for any of the 10,000 domains

Scenario: All three routes stay present in every class   # M6, R:ROUTE_HIDES_ROUTE
  Given a visitor is on /signup
  When the classification is "public", then "corporate", then "unknown" in turn
  Then in every case the SSO link, the invite-link copy, and the request-access form are all
       present in the DOM
  And the frozen signup-refusal-router M1 property (unconditional render) remains true

Scenario: The three routes' own copy and behavior are unchanged   # M9
  Given the shipped signup-refusal-router routes
  When any classification is applied
  Then each route's copy, href, and submit behavior are byte-identical to the frozen contract
  And only a classification-derived lead-in line and the ordering differ

Scenario: Nothing typed yet renders the neutral panel   # M5, R:ROUTE_NEUTRAL_NO_DOMAIN
  Given a visitor is on /signup with an empty email field
  When the panel renders
  Then the classification is "unknown"
  And the panel is byte-identical to today's shipped neutral panel
  And no error, hint, or warning is shown to the visitor
  And the three routes remain present and unchanged

Scenario: Malformed domain falls back to neutral   # M5, R4 (ROUTE_NEUTRAL_MALFORMED_DOMAIN)
  Given a visitor is on /signup
  When they type each of "bob@localhost", "bob@192.168.1.1", "bob@ acme.com",
       "bob@" + a 300-character label, and "bob@-acme.com"
  Then every one classifies as "unknown"
  And the neutral panel renders for each
  And no error is shown to the visitor and no request is issued

Scenario: Subdomain of a public provider is NOT public   # M3, M4 edge case
  Given the provider list contains "gmail.com"
  When a visitor types "bob@mail.gmail.com"
  Then the classification is "corporate" (exact-match only, mirroring normalize_domain's
       no-subdomain-matches-parent rule)
  And no request is issued

Scenario: Multiple "@" resolves on the LAST one   # M2 edge case
  Given a visitor types "weird@name@acme-corp.com"
  When the classification runs
  Then the domain resolved is "acme-corp.com"
  And the classification is "corporate"

Scenario: Provider lists cannot silently drift   # M3, R6 (ROUTE_PROVIDER_LIST_DRIFT)
  Given the gateway's PUBLIC_EMAIL_DOMAINS frozenset
  And the dashboard's client-side provider list
  When the parity guard runs
  Then the two sets are equal value-for-value
  And adding an entry to only one of them fails the guard

Scenario: No existence lookup can be reached from a routing path   # R1
  Given the classification module and the panel that consumes it
  When the build is inspected for reachability
  Then no path reaches resolve_verified_tenant_for_raw_domain, resolve_verified_tenant,
       or any tenant/user/claim lookup
  And the classification module imports nothing that performs IO

Scenario: The classification never issues a network probe   # R2 (ROUTE_NETWORK_PROBE_FORBIDDEN)
  Given fetch, XMLHttpRequest, and navigator.sendBeacon are all instrumented
  When a visitor types a full email address one character at a time
  Then none of them is called by the classification or the panel render
  And the existing handleSso preflight is NOT invoked (it fires only on an explicit SSO click,
       unchanged)

Scenario: The server surface is unchanged   # M12
  Given the gateway and BFF route tables before this task
  When the task is complete
  Then the set of routes, their responses, the schema, and the config knobs are identical
  And no migration was added
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
SERVER SURFACE — INTENTIONALLY EMPTY (M12)
  No new gateway route. No new BFF route. No schema change. No migration. No config knob.
  The set of endpoints, their responses, and their DB access after this task is BYTE-IDENTICAL
  to before it. This is a contract term, not an omission: the security property of this task is
  delivered by the ABSENCE of a server surface, so adding one is a contract violation.

NEW CLIENT MODULE — apps/dashboard/lib/email-domain-routing.ts   (pure, zero-IO)

  export type EmailDomainClass = "public" | "corporate" | "unknown";

  export const PUBLIC_EMAIL_DOMAINS: ReadonlySet<string>;
    # value-for-value mirror of
    # apps/gateway/src/gateway/domain_capture/domain/public_email_domains.py:PUBLIC_EMAIL_DOMAINS
    # (22 entries as of Ground SHA 9421827). Lower-cased apex domains only.

  export function normalizeEmailDomain(raw: string): string;
    # trim -> lowercase -> text after the LAST "@" (a value with no "@" is taken as-is).
    # Returns "" when nothing usable. Same derivation as LoginForm.resolveSsoDomain, which it
    # supersedes as the ONE normalizer (R-d: two near-duplicates exist today).

  export function isWellShapedDomain(domain: string): boolean;
    # TS mirror of domain_validation.normalize_domain's ACCEPT rules: >= 2 labels, each 1-63
    # chars matching /^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$/, total <= 253, not a bare IP literal.
    # Deliberately STRICTER than the shipped LoginForm.validateSsoDomain regex (R-g).

  export function classifyEmailDomain(raw: string): EmailDomainClass;
    # "public"    iff PUBLIC_EMAIL_DOMAINS.has(normalizeEmailDomain(raw))   [exact match only —
    #             a subdomain never matches its parent, mirroring normalize_domain's M8 rule]
    # "corporate" iff isWellShapedDomain(d) && !PUBLIC_EMAIL_DOMAINS.has(d)
    # "unknown"   otherwise  (empty / no domain part / malformed)  <- SAFE DEFAULT

  FORBIDDEN BY CONTRACT in this module and in every caller of it:
    - any import that performs IO (fetch, XMLHttpRequest, sendBeacon, next/navigation data
      fetching, dynamic import, cookie/storage reads)
    - any reference, direct or transitive, to resolve_verified_tenant_for_raw_domain,
      resolve_verified_tenant, or any tenant / user / domain-claim / SSO-config lookup
    - any reuse of LoginForm.handleSso's preflight as a classification signal

CHANGED COMPONENT — apps/dashboard/components/auth/SignupForm.tsx:SignupForm
  The SHIPPED [data-slot="signup-alt-routes"] panel gains, and ONLY gains:
    1. data-domain-class="public" | "corporate" | "unknown"  on the panel element
       (the observable the tests assert on)
    2. a classification-derived lead-in line, one of exactly three static strings:
         public    -> "Looks like a personal address — you can create your own workspace."
         corporate -> "If your team already uses Hydroa, here's how to get in."
         unknown   -> (no lead-in; today's shipped neutral panel, byte-identical)
    3. an ORDER over the three existing routes:
         public    -> [self-serve signup emphasis, invite-link copy, request-access, SSO link]
         corporate -> [SSO link, invite-link copy, request-access]
         unknown   -> today's shipped order, byte-identical
  UNCHANGED (frozen by signup-refusal-router §3 v1 — re-verified by reading, not modified):
    - all three routes remain PRESENT in the DOM in every class (M6/R5)
    - each route's own copy, href, and submit behavior are byte-identical
    - the SSO link's href derivation (?domain=<typed domain>), the request-access
      POST /api/auth/access-requests call and its 2xx/429/422 renders, the invite-link copy
    - deriveSsoDomain stays as-is or delegates to normalizeEmailDomain with identical output
    - the 403 ERR_SIGNUP_INVITE_ONLY handling via problem.code
  UNCHANGED — apps/dashboard/components/auth/LoginForm.tsx: handleSso, handleSamlSso,
    SSO_NOT_CONFIGURED_MSG, and the preflight are NOT touched by this task (see the ⚠ flag).

THE ANTI-ENUMERATION INVARIANT (M11) — stated as a testable property:
  render(panel, typed) is a PURE FUNCTION of (typed string, PUBLIC_EMAIL_DOMAINS).
  Server state is NOT an input. Therefore, for any two domains in the same shape class, the
  rendered panel is byte-identical regardless of tenant / claim / user / SSO-config existence.
  VERIFIED BY, at minimum:
    (P1) a render test over a domain with a seeded verified claim and a domain with none,
         asserting byte-identical panel markup;
    (P2) an instrumentation test asserting fetch / XMLHttpRequest / sendBeacon are called ZERO
         times across a full character-by-character type-in of an email address;
    (P3) a static-reachability assertion that email-domain-routing.ts's import graph is IO-free;
    (P4) the list-parity guard (below).
  A failure of ANY of P1-P4 is a SECURITY HARD-STOP, never a flaky-test retry.

LIST-PARITY GUARD (R6 / ROUTE_PROVIDER_LIST_DRIFT):
  A node-environment test reads
  apps/gateway/src/gateway/domain_capture/domain/public_email_domains.py, extracts the
  frozenset literal, and asserts set-equality with the TS PUBLIC_EMAIL_DOMAINS. Adding an entry
  to either side alone FAILS the build. (Chosen over codegen for simplicity; see §1 assumption 3.)

CONTRACT-LEVEL REFUSALS (no HTTP codes — M12 adds no server surface; see §1 assumption 2):
  ROUTE_EXISTENCE_LOOKUP_FORBIDDEN   -> P3 reachability assertion fails the build
  ROUTE_NETWORK_PROBE_FORBIDDEN      -> P2 instrumentation assertion fails the build
  ROUTE_HIDES_ROUTE                  -> presence-in-every-class test fails the build
  ROUTE_PROVIDER_LIST_DRIFT          -> parity guard fails the build
  ROUTE_NEUTRAL_NO_DOMAIN            -> class "unknown", neutral render, no visitor-facing error
  ROUTE_NEUTRAL_MALFORMED_DOMAIN     -> class "unknown", neutral render, no visitor-facing error
```

Glossary deltas: **Email-domain shape class** (NEW term) — the classification of a typed email's
domain as `public` (a known public/generic provider), `corporate` (a well-shaped domain that is
not a known public provider), or `unknown` (absent or malformed), computed purely from the typed
string and a static list. It is deliberately NOT a statement about whether that domain belongs to
a tenant: it is EXISTENCE-BLIND by construction, and is distinct from a **Domain claim** (DNS-TXT-
proven tenant ownership) and from **member-verified** (mailbox proof). Naming matters here — the
term must never drift into meaning "we know this domain." [folded foundation-version 55]

Status: DRAFT — ready to freeze, NOT frozen (the freeze is the human's decision).
Reported: no — the orchestrator brings this to Tin.

Least-sure flag surfaced at freeze (lead with this): [security] ⚠ **This task is built on top of a
pre-existing SSO-preflight surface that appears to carry an OPEN claim-existence oracle, and this
task's corporate branch would send more traffic to it.** See §0 R-a for the exact branch table.
Two distinct things, which must not be conflated:
  1. **An INHERENT residual signal.** Any domain-keyed SSO entry point must eventually reveal
     whether an SSO flow can start for that domain — you cannot redirect a user to their IdP
     without disclosing that an IdP exists. This is not fixable while offering domain-based SSO;
     it is mitigable (rate-limit the preflight, do not render it as a crisp yes/no sentence).
  2. **A LIKELY DEFECT, which IS fixable.** `oidc_login` returns 403 ERR_OIDC_DOMAIN_NOT_MAPPED
     for a verified-claim-but-unconfigured domain and 404 ERR_OIDC_NOT_CONFIGURED for an
     unclaimed one. The code's own comment asserts these are indistinguishable ("no oracle between
     the two", M2 of the FROZEN domain-routing-unification §3 v2/CR-v2) — but two different status
     codes are trivially distinguishable, so the frozen property looks undelivered. Severity is
     deployment-dependent: with env `oidc_enabled` TRUE the fallback returns 302 broadly and the
     signal largely disappears; with it FALSE the oracle is live.
Least confident because (2) is a CODE-READ conclusion I did not execute a request to confirm, and
because the remedy touches a frozen contract owned by another task — neither the confirmation nor
the fix is mine to perform. THE DECISION REQUIRED: (a) ship as drafted, accept, and open a
separate hardening task against domain-routing-unification's M2; (b) confirm + fix the 403/404
split FIRST, before this task lands; or (c) ship with the corporate branch de-emphasizing SSO
until (2) is resolved. Cost if wrong: we land a task whose headline security property is true of
its own diff and false of the screen a visitor actually sees — green tests, false headline.
PROVENANCE: this flag's first draft named the wrong error codes and asserted a flat
"302-vs-404 = customer". An adversarial advisor review caught it; the branch table in §0 R-a is
the corrected, code-read version. Treat the correction itself as evidence that this surface is
subtle enough to deserve a real test, not another read.

Further ranked flags:
  1. [contract] Contract-level/build-time refusals instead of HTTP error codes (§1 assumption 2).
     The alternative — inventing an endpoint so there is something to return 4xx from — would
     itself be the oracle. Recommend accept.
  2. [contract] The TS mirror of PUBLIC_EMAIL_DOMAINS is a second copy of a security-relevant
     list, guarded by a parity test rather than codegen (§1 assumption 3). Recommend accept; swap
     to codegen later without a contract change.
  3. [UX] `corporate` leads with SSO rather than request-access (§1 assumption 5) — an unresearched
     ordering judgement; cheap to reorder, changes no invariant.
  4. [scope] The 22-entry provider list is incomplete; an unlisted provider mis-classifies as
     `corporate` and the visitor sees an oddly-ordered but complete panel (§1 assumption 4).
  5. [UX — found at CONTRACT, worth Tin's eye] The shipped panel renders **above** the email
     field, not below it: signup-refusal-router deliberately placed it FIRST ("so the routing
     panel is the very first thing a keyboard/screen-reader user reaches, ahead of the
     account-type/tenant-name/email fields that lead nowhere for a visitor without an invite" —
     SignupForm.tsx's own comment). So this task's adaptation happens ABOVE where the visitor is
     typing, and a visitor may never look back up to see it. Moving the panel would overturn a
     frozen, deliberately-reasoned a11y placement, so this contract does NOT move it. The full
     payoff of domain-aware routing therefore lands in the DEPENDENT task `unified-signin-entry`,
     which owns the single email-first entry field; this task's job is to establish the
     classification seam and the M11 invariant that task inherits. If Tin wants the visible
     payoff sooner, that is a scope decision to make at this freeze, not a build-time discovery.
Least-sure flag surfaced at freeze: [scope] the shipped alt-routes panel renders ABOVE the email
field (a deliberate, frozen a11y placement from signup-refusal-router — panel first so a keyboard/
screen-reader user reaches the live routes before fields that lead nowhere without an invite). So
this task's domain-aware adaptation happens above where the visitor is typing and many visitors will
never look back up to see it. This contract deliberately does NOT move the panel — doing so would
overturn a reasoned frozen a11y decision — which means THIS task's visible payoff is limited by
design: its job is to establish the classification seam + the M11 anti-enumeration invariant, and
the dependent task `unified-signin-entry` (same milestone, already planned) delivers the payoff via
its single email-first entry field. Accepted at freeze on that basis. · [test] the M11 invariant is
enforced by P1-P4, of which P2 (zero fetch/XHR/sendBeacon across a full type-in) and P3 (static
IO-free import graph) are the load-bearing ones — if either proves unenforceable in jsdom, the
invariant degrades from structural to merely-asserted and that is a SECURITY HARD-STOP, not a
retry. · [scope] LoginForm's handleSso preflight is deliberately NOT touched here, so any residual
oracle on the LOGIN surface is out of this task's scope and remains open until `unified-signin-entry`.

Status: FROZEN @ v1 — approved by Tin Dang

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% of the new `lib/email-domain-routing.ts` (a pure module — every branch is
reachable without a fixture); the SignupForm delta is covered by the render suite below. The repo
floor (`vitest.config.ts` coverage.thresholds.lines = 80 over `components/**` + `lib/**`) holds.

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  PURE MODULE — `apps/dashboard/tests/email-domain-routing.test.ts` (14 tests)
  - test_normalize_trims_lowercases_and_takes_text_after_last_at: arrange "Bob@GMAIL.com" /
    act normalizeEmailDomain / assert "gmail.com" · covers: M2
  - test_normalize_multiple_at_resolves_on_the_last_one: "weird@name@acme-corp.com" ->
    "acme-corp.com" · covers: M2 edge
  - test_normalize_bare_domain_without_at_is_taken_as_is: matches resolveSsoDomain · covers: M2
  - test_normalize_returns_empty_when_nothing_usable: "" / "   " / "bob@" -> "" · covers: M2, R3
  - test_well_shaped_accepts_multi_label_hostnames · covers: M4
  - test_well_shaped_rejects_single_label_ip_literal_and_bad_labels: the normalize_domain reject
    set (single label, IPv4 literal, leading/trailing hyphen, whitespace, underscore, >63-char
    label, >253 total) · covers: M4, R4, R-g
  - test_public_provider_classifies_public_case_insensitively · covers: M2, M3
  - test_every_listed_provider_classifies_public: the WHOLE list, not one demo entry · covers: M3
  - test_corporate_domain_classifies_corporate · covers: M4
  - test_subdomain_of_public_provider_is_not_public: exact-match only · covers: M3/M4 edge
  - test_multiple_at_classifies_on_the_last_domain · covers: M2 edge
  - test_empty_or_no_domain_part_classifies_unknown · covers: M5, R3
  - test_malformed_domain_classifies_unknown: the §2 malformed list · covers: M5, R4
  - test_classification_is_total_and_deterministic: same input -> same output over 50 calls (a
    lazily-initialised cache or memoized lookup would diverge) · covers: M1

  (P3) STATIC REACHABILITY — same file, 3 tests · covers: R1, R2
  - test_p3_module_has_zero_imports: no import / require / dynamic import / re-export barrel
  - test_p3_module_names_no_io_or_existence_lookup_identifier: §3's FORBIDDEN list verbatim +
    resolve_verified_tenant[_for_raw_domain] + handleSso, comments stripped first
  - test_p3_guard_itself_is_wired_to_the_real_file: proves the guard reads the module the other
    tests import (a guard on the wrong path passes vacuously forever)

  (P4) LIST PARITY — same file, 3 tests · covers: M3, R6
  - test_p4_lists_are_set_equal: reads the REAL public_email_domains.py off disk, extracts the
    frozenset literal, asserts set-equality with the TS PUBLIC_EMAIL_DOMAINS
  - test_p4_guard_fails_in_the_failing_direction: a one-sided edit IS rejected (§6: "a guard never
    exercised in the failing direction is not evidence")
  - test_p4_list_is_non_trivial_and_lowercase_apex_only: parity over two EMPTY sets would pass
    vacuously — pin size >= 22, lowercase, apex shape

  COMPONENT — `apps/dashboard/tests/signup-domain-aware-routing.test.tsx` (12 tests)
  - test_public_address_classifies_public_and_leads_with_self_serve: data-domain-class="public",
    lead-in precedes all routes, order [invite, request-access, sso] · covers: M1, M2, M7
  - test_case_and_whitespace_do_not_change_the_class · covers: M2
  - test_corporate_address_leads_with_the_three_team_routes: order [sso, invite, request-access]
    + the corporate lead-in · covers: M4, M8
  - test_nothing_typed_renders_the_neutral_panel_with_no_lead_in: class "unknown", no lead-in, no
    role="alert", shipped order · covers: M5, R3
  - test_malformed_domain_falls_back_to_neutral_unknown · covers: M5, R4
  - test_subdomain_of_public_provider_renders_corporate · covers: M3/M4 edge
  - test_all_three_routes_present_in_public_corporate_and_unknown: 3/3/3 recorded · covers: M6, R5
  - test_route_copy_href_and_behavior_stay_byte_identical: the frozen signup-refusal-router copy +
    href asserted in the class that REORDERS them, so reorder-by-rewrite fails · covers: M9
  - (P1) test_p1_claimed_and_unclaimed_domain_panels_are_byte_identical: server SEEDED to answer
    302 vs 404 for the two domains; both panels' outerHTML compared literally, modulo the typed
    domain · covers: M11
  - (P2) test_p2_zero_requests_across_a_full_character_by_character_type_in: fetch /
    XMLHttpRequest.open / navigator.sendBeacon counted, all 0 · covers: M10, R2
  - (P2) test_p2_adversary_probing_many_domains_issues_no_request: THE ADVERSARY CASE · covers:
    M11, R2
  - (P2) test_p2_sso_preflight_is_not_invoked_by_classification: the SSO route is a plain <a
    href>, not a probing click-handler · covers: R2

  NON-VACUITY NOTE (deliberate, this milestone already caught one vacuous a11y test): P1 and P2
  would both PASS today against a SignupForm that does nothing at all — "identical markup" and
  "zero requests" are trivially true of an unbuilt feature. Every P1/P2 test therefore ALSO
  asserts the expected data-domain-class, so each is red on the missing implementation and each
  probes BOTH failure directions (the leak AND the useless-but-silent classifier) — the
  appsec-engineer persona's Default Requirement applied to this task.
</test_plan>

Tests live in: `apps/dashboard/tests/` · MUST run red (missing implementation) before Build.

RED CONFIRMED (before any implementation): 12 failed / 12 in the component suite; the pure-module
suite fails at import resolution — `Failed to resolve import "@/lib/email-domain-routing"`. Every
component failure is `element.getAttribute("data-domain-class") === null`, i.e. missing
implementation, not a broken harness. One test was initially red for a HARNESS reason
(`toMatch(regex)` on a null attribute throws a TypeError) and was corrected to an array-contains
assert at TESTS, before build opened — recorded rather than silently fixed.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/lib/email-domain-routing.ts` `apps/dashboard/components/auth/SignupForm.tsx` `apps/dashboard/tests/` `apps/dashboard/tests-bff/`

Strategy (ordered batches — PREFERRED plan, not a hard rule):
  1. Write the red suite first (TDD): the pure-function tests over `classifyEmailDomain` (every §2
     scenario), then the parity guard, then the panel render + instrumentation tests.
  2. Build `apps/dashboard/lib/email-domain-routing.ts` as a leaf module with ZERO imports —
     no framework, no util barrel (a barrel import is how IO sneaks into an "IO-free" module).
     Port `normalize_domain`'s accept-rules literally; do not re-invent the regex.
  3. Wire `SignupForm` to the classifier: derive the class from the existing email state on each
     render (a plain `const cls = classifyEmailDomain(email)` — NOT a useEffect, NOT state; a
     derived value cannot desync and cannot introduce an async seam where a fetch could later be
     added).
  4. Add `data-domain-class` + the lead-in line + the ordering. Keep each route's own JSX subtree
     byte-identical — reorder by moving whole subtrees, never by rewriting them.
  5. Refute-read the diff specifically for: a new import in the classifier, a `useEffect` that
     could host a probe, and any place the panel reads something other than the typed string.

Persona (required): `appsec-engineer` — loaded as the domain stance. Its rule "an unknown id and a
cross-tenant id return the IDENTICAL response … this is what closes the enumeration oracle, not an
after-the-fact check" is the direct ancestor of M11: the identical-render property must hold BY
CONSTRUCTION (a pure function with no server input), never by a downstream check. Its "verify BOTH
failure directions by default" applies as: probe for the leak AND for the feature silently not
working (a classifier that always returns "unknown" leaks nothing and is also useless).
NOTE: no `flow: design` persona in `.add/personas/` is an identity/auth architect (the design-flow
set is accessibility-auditor / ui-designer / ux-researcher); the design span therefore ran as a
generic domain-analyst/interface-architect with appsec-engineer as the security overlay. A
`flow: design` identity-architect persona is worth seeding — see §7 competency delta.

Spawn isolation (default): `isolation: "worktree"` for any build/verify subagent spawn — two
sibling design agents are working concurrently in this milestone; a shared tree would cross them.

Known-problem fixes (traps this build must dodge):
  - R-c list drift -> the parity guard is written in batch 1, BEFORE the TS list exists, so the
    list is born guarded rather than guarded later.
  - R-d two derivations -> `normalizeEmailDomain` is the ONE normalizer; `deriveSsoDomain`
    delegates to it or is proven output-identical. Never a third copy.
  - R-e frozen unconditional render -> the presence-in-every-class test is written before the
    ordering code, so a hidden route fails immediately.
  - R-g looser client regex -> port `normalize_domain`'s rules, not `validateSsoDomain`'s.
  - The "helpful" regression -> any future "but we could just check if the domain exists" is
    exactly R1/R2; the contract names the forbidden symbols so a reviewer can grep for them.
  - Build artifacts poison the ADD gate scope-walk -> clean `.coverage` / `.pytest_cache` as the
    LAST pre-gate step (recurring project lesson).

Strategy actually used: <fill at VERIFY>

Safety rule (feature-specific): **The routing decision must be computable with the network cable
unplugged.** If any code path needs a server answer to decide what to render, the design is wrong —
stop and escalate rather than shaping the response to look uniform.

Code lives in: `apps/dashboard/lib/` · `apps/dashboard/components/auth/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 32/32 new (email-domain-routing.test.ts + signup-domain-aware-routing.test.tsx);
      full dashboard suite green across BOTH vitest projects; green-bar
      `vitest (ci.yml dashboard job, working-directory: apps/dashboard)`
- [x] coverage did not decrease — new module 100% lines/statements, 93.3% branch (target 90%)
- [x] no test or contract was altered during build
- [x] the green was EARNED, not gamed — 2 independent adversarial refute-reads, both EARNED; P1/P2
      specifically defended against the vacuity trap (they assert the expected CLASS too, so a
      component that classified nothing would fail) — see the refute-read verdict below
- [x] concurrency / timing of the risky operation is safe — no async seam exists; render-time const
- [x] no exposed secrets, injection openings, or unexpected dependencies — leaf module, zero imports
- [x] layering & dependencies follow CONVENTIONS.md — pure domain predicate in lib/, one importer
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
> ⚠ EVIDENCE-METHOD CORRECTION (orchestrator, at gate): two expectations below were authored at
> design time assuming a CLEAN single-task tree and named `git diff` as their confirmation method.
> That method is UNSOUND here — this branch carries 4+ sibling tasks' uncommitted work (gateway
> tenants/oidc/migrations, the access-requests BFF route, LoginForm's `?domain=` seed from
> signup-refusal-router). A raw `git diff` would show sibling files and CANNOT serve as this task's
> proof. The OBSERVABLE being confirmed is unchanged; only the evidence METHOD is replaced with
> symbol-level attribution by reading. Recorded openly rather than checking a box whose stated
> method would be a false claim.
- [x] Typing a gmail.com address into /signup makes the panel carry `data-domain-class="public"`
      and lead with the create-your-own-workspace line — confirmed: rendered DOM asserts the
      attribute + lead-in string (signup-domain-aware-routing.test.tsx), 32/32 green.
- [x] Typing a corporate address makes the panel carry `data-domain-class="corporate"` and order
      the routes SSO → invite → request-access — confirmed by rendered node order, SignupForm.tsx:389-406.
- [x] A domain WITH a seeded verified claim and a domain with NO claim produce byte-identical
      panel markup — confirmed (P1, test :267-296): asserts `data-domain-class="corporate"` on BOTH
      renders BEFORE comparing, with MSW seeding a genuine 302-vs-404 asymmetry — so a component
      that consulted the server would fail. Verified NON-VACUOUS by an independent verify agent.
- [x] Zero network calls across a full character-by-character type-in — confirmed (P2, test
      :328-393): instrumented `fetch` / `XMLHttpRequest.open` / `navigator.sendBeacon` counts
      recorded as **{0, 0, 0}**, asserted after EVERY character plus the final class.
- [x] `email-domain-routing.ts` has an empty import list — confirmed by reading the file: **ZERO
      imports**, so the "IO-free import graph" is trivially TOTAL, not a sampling claim.
- [x] The TS and Python provider lists are set-equal (22/22) — confirmed by the parity guard AND
      **exercised in the FAILING direction** by an independent verify agent, which ran the guard's
      exact extraction regex against a MUTATED copy of the .py: pinned → `{[],[]}`; drifted (+1 py
      entry) → `{missingFromClient:["bogus-drift-provider.example"]}`, which the guard rejects.
      (Run read-only on a scratchpad copy — the tree was never mutated.)
- [x] All three routes are present in the DOM for all three classes — confirmed, counts recorded
      **{unknown: 3, public: 3, corporate: 3}** (test :204-231). Structural, not merely tested: each
      route is defined ONCE as a named subtree (ssoRoute:308, inviteRoute:317, requestAccessRoute:323)
      and BOTH ordering branches render all three, so no class can hide a route.
- [x] The gateway/BFF route table, schema, and config are unchanged BY THIS TASK — METHOD REPLACED
      (see correction above): confirmed by symbol-level attribution from reading, by two independent
      verify agents. This task's attributable delta is exactly: 1 new dashboard lib file, 2 new test
      files, and a comment-tagged SignupForm delta adding ONLY `data-domain-class` + the lead-in +
      the ordering ternary. Zero gateway/BFF files are attributable to this task. M12 holds.
- [x] `LoginForm.handleSso` and `SSO_NOT_CONFIGURED_MSG` are untouched — METHOD REPLACED: the file's
      `git diff` is NOT empty (18 insertions), but every one of them is signup-refusal-router's M2
      `?domain=` seed, NOT this task. The §3 UNCHANGED list (`handleSso`, `handleSamlSso`,
      `SSO_NOT_CONFIGURED_MSG`, the preflight) verified untouched by reading each symbol.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `classifyEmailDomain` / `normalizeEmailDomain` / `isWellShapedDomain` /
      `PUBLIC_EMAIL_DOMAINS` all referenced; exactly ONE production importer repo-wide
      (SignupForm.tsx:38); `domainClass` consumed at the panel. Confirmed by both verify agents.
- [x] DEAD-CODE (code) — no new unused or orphaned symbol; leaf module, zero deps.
- [x] SEMANTIC (prose / non-code) — the three static lead-in strings read in full; the corporate
      line is conditionally phrased ("If your team already uses Hydroa…") so it asserts nothing
      about whether the visitor's domain is a customer.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every symbol §3 CONTRACT cites still resolves — `deriveSsoDomain`, the
      `[data-slot="signup-alt-routes"]` panel, `resolveSsoDomain`/`validateSsoDomain`,
      `PUBLIC_EMAIL_DOMAINS`/`is_public_email_domain`, `normalize_domain`,
      `resolve_verified_tenant_for_raw_domain` (cited as FORBIDDEN — confirmed unreferenced by this
      task, directly or transitively). Confirmed by both verify agents.
- [x] ANCHOR THAT MOVED, named not silent: §0 R-a described the login-surface oracle as LIVE. It is
      now CLOSED by the sibling task `sso-login-oracle-closure` (same branch, same PR). Verified by
      the orchestrator reading control flow, not docstrings: `oidc_login` no longer raises
      `OIDC_DOMAIN_NOT_MAPPED` at all (that 403 survives ONLY in `oidc_callback`, oidc_router.py:368,
      which requires an IdP-signed token and is not an enumeration surface); the claimed-but-
      unconfigured leg leaves `oidc_config = None` and falls through to the single shared terminal
      `raise OIDC_NOT_CONFIGURED.exc()` (:235). Both legs land identically in BOTH deployment shapes.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: 2 independent add-verify agents (opus) — ab047ee (M11-purity lens) + a0fc731 (screen-level-leak /
route-integrity lens) · adversarially checked: (1) purity — transitive import graph (ZERO imports, so
IO-freedom is total); determinism proven EMPIRICALLY (48 inputs × 200 interleaved calls against the real
module → stable); the panel's other inputs (requestStatusMessage/requestFieldError) traced to a gateway
endpoint that is unconditionally 202 with no branch on existence; P1-P4 each judged non-vacuous with
file:line, P4 proven in the FAILING direction against a mutated copy; malformed fail-safe probed well
beyond the fixed case (zero-width space, trailing/leading/double dot, non-ASCII, IPv6 literal, 64-char
label, >253 total, bare "@") — ALL fail safe to `unknown`. (2) route integrity — all three routes present
in all three classes, structurally. Both CLEAR / no HARD-STOP.
⚠ ORCHESTRATOR CORRECTION — the two agents CONTRADICTED each other twice; I resolved BOTH by reading the
code myself rather than adjudicating between them, and in both cases the pessimistic claim was WRONG:
 (a) ab047ee's residue said this task "routes MORE traffic" to the SSO surface. FALSE — SignupForm.tsx:
     389-406 shows `corporate` shares today's shipped order byte-identically (SSO was ALREADY first);
     only `public` reorders, DEMOTING SSO to last. This task routes strictly FEWER visitors to SSO than
     before it, which also refutes §1 assumption 1's own premise (recorded as a delta).
 (b) ab047ee's residue said the 403/404 login oracle is "still live". FALSE as of this tree — closed by
     the sibling task; verified by reading `oidc_router.py` control flow (see Live-verify above).
     ab047ee was reading the §3 freeze-flag TEXT (authored pre-sibling-build), not the current code.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: 2 independent add-verify agents (ab047ee purity + a0fc731 screen-level), persona appsec-engineer
1. Security: CLEAR — M11 purity upheld absolutely for the typed-string path; no IO reachable; no
   existence oracle in this task's surface; malformed input fails safe; ≥2 independent adversarial
   verifies, both CLEAR, no HARD-STOP finding.
2. Concurrency: CLEAR — n/a by construction: `const domainClass = classifyEmailDomain(email)` is a
   render-time derived const (no useState, no useEffect, no async seam), so there is no race to have.
3. Architecture: CLEAR — leaf module, zero dependencies, exactly one importer, correct layering; routes
   reordered by moving whole subtrees so frozen copy is never rewritten (M9 holds).
Verdict: PASS
Residue: none blocking. 4 non-blocking deltas recorded for follow-on: (i) extend P3's IO-free assertion
to CALLERS, not just the module — §3's FORBIDDEN list binds every caller, and `unified-signin-entry` is
about to become a second caller with no structural guard; (ii) replace P4's decorative failing-direction
sub-test (it re-implements set algebra instead of exercising the real guard) with a fixture-based one;
(iii) `PUBLIC_EMAIL_DOMAINS` is `ReadonlySet` = compile-time only (runtime `.add()` works, unlike the
Python frozenset) → keep the Set module-private and export an `isPublicEmailDomain` predicate;
(iv) pin the SSO route as a plain `<a>` — if it were "modernized" to `next/link`, hover-prefetch would
emit one request per typed domain and P2 (which counts only during typing) would NOT catch it.
Binding: yes — sensitivity: security (HARD-STOP floor satisfied — no finding to stop on; 2 adversarial
verifies recorded, both CLEAR, with the two contradictions independently resolved by code-read)

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
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

- [ADD · folded] no `flow: design` persona covers identity/auth architecture — the design-flow set is [folded foundation-version 55]
  accessibility-auditor / ui-designer / ux-researcher, all UI lenses. A SECURITY design span on an
  auth surface had to run generic with `appsec-engineer` (flow: build, advisor) as an overlay
  (evidence: this task's §5 Persona note). Seed an identity-architect persona with `flow: design`,
  or add `design` to appsec-engineer's flow.
- [SDD · folded] the anti-enumeration reasoning that protects the BACKEND (uniform status/body/cost, [folded foundation-version 55]
  scoped-self-serve-signup §3) does NOT transfer to a routing UI, where the visible adaptation IS
  the observable — the only safe posture is to make the decision a pure client-side function of
  input + a static constant (evidence: §1 framings 2 and 4, both rejected for this reason).
- [SDD · folded] a task can inherit a live oracle from a NEIGHBOURING frozen contract without its own [folded foundation-version 55]
  diff being at fault; grounding must sweep the surfaces the task ROUTES TO, not just the ones it
  edits (evidence: §0 R-a — LoginForm.handleSso's 302-vs-404 preflight, found only because ground
  read the SSO destination this task emphasizes).
