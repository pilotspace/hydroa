# TASK: Collapse password/SSO/SAML into one routed Continue-with-email step

slug: unified-signin-entry · created: 2026-07-20 · stage: production
milestone: frontdoor-persona-routing
autonomy: auto
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/dashboard/components/auth/LoginForm.tsx:LoginForm` — **THIS TASK'S SURFACE.** As it
  stands NOW it renders, in one card, in this order: `login_email` (Email) → `login_password`
  (Password) → **Log in** → `sso_domain` ("Work email or domain") → **Sign in with SSO** →
  **Sign in with SAML**. That is TWO email-shaped fields and THREE submit affordances, with no
  guidance about which applies to you — the visitor must self-diagnose. There is **no link to
  /signup anywhere on this surface**: a visitor who clicks "Log in" and has no account is at a
  literal dead end. This is the milestone's stranded persona, on the screen they actually reach.
- `apps/dashboard/components/auth/LoginForm.tsx:resolveSsoDomain(raw: string): string` — pure,
  zero-IO: trim → lowercase → text after the LAST "@", bare domain as-is. Superseded in intent by
  `normalizeEmailDomain` (domain-aware §3), but still the live derivation feeding `handleSso` /
  `handleSamlSso`. Exported and directly unit-tested → NOT removed by this task.
- `apps/dashboard/components/auth/LoginForm.tsx:validateSsoDomain(domain: string): string | null`
  — lenient shape check `/^[^\s@]+\.[^\s@]+$/`. Looser than `isWellShapedDomain`; exported and
  directly tested → NOT tightened by this task (tightening it would change which domains reach
  the gateway, i.e. shipped SSO behavior).
- `apps/dashboard/components/auth/LoginForm.tsx:handleSso` + `SSO_NOT_CONFIGURED_MSG` +
  `SSO_PREFLIGHT_TIMEOUT_MS` — the SSO preflight: `fetch("/api/auth/oidc/login?domain=X",
  {redirect:"manual"})`; a 4xx renders one uniform message and blocks navigation, a non-4xx
  persists the domain and navigates. **Fires ONLY from an explicit click on "Sign in with SSO".**
  It is not on any typing path today, and this task does not put it on one (→ R2, and the
  login-residual ruling below).
- `apps/dashboard/components/auth/LoginForm.tsx:handleSamlSso` — the SAML sibling; no preflight at
  all, straight `window.location.assign`. Untouched.
- `apps/dashboard/components/auth/LoginForm.tsx` — the `useEffect([])` ONE-SHOT seed: a present
  `?domain=` query param wins, else a `localStorage` `sso_domain` value. Sets `ssoDomain` state.
  This task must not disturb its precedence (asserted green by `login-domain-query-seed.test.tsx`).
- `apps/dashboard/lib/email-domain-routing.ts` — the SHIPPED pure, zero-IO, **import-free** module
  from `domain-aware-auth-routing` §3 (FROZEN @ v1): `EmailDomainClass = "public" | "corporate" |
  "unknown"`, `PUBLIC_EMAIL_DOMAINS` (22-entry `ReadonlySet`, parity-guarded against the Python
  frozenset), `normalizeEmailDomain(raw)`, `isWellShapedDomain(domain)`, `classifyEmailDomain(raw)`.
  Read in full at Ground SHA. **This task CONSUMES it unchanged** — it adds no classifier, no
  second list, no new normalizer.
- `apps/dashboard/components/auth/SignupForm.tsx:SignupForm` — the precedent this task mirrors on
  the login side: `const domainClass = classifyEmailDomain(email)` as a **derived value on every
  render** (not state, not an effect), feeding `data-domain-class` on the
  `[data-slot="signup-alt-routes"]` panel, a per-class lead-in, and a per-class ORDER over three
  always-present routes. Also carries the ONE-SHOT `?account_type=business` seed
  (`homepage-cta-intent-split` §3 M4) — the exact seed pattern this task reuses for `?email=`.
- `apps/dashboard/app/(auth)/login/page.tsx:LoginPage` — server component; validates `?next=`
  through `loginNextTarget` and renders a generic alert on `?sso_error=`. Passes `nextPath` into
  `LoginForm`. Not modified by this task.
- `apps/dashboard/app/(auth)/signup/page.tsx:SignupPage` — renders `SignupForm`. Not modified.
- `apps/gateway/src/gateway/auth/api/oidc_router.py:oidc_login` — **re-read at Ground SHA to
  verify the sibling landed, not assumed.** It now documents and implements "ONE collapsed
  terminal (sso-login-oracle-closure TASK.md §3 — FROZEN @ v1, which AMENDS
  domain-routing-unification §3 M2's `403 | 404` alternation to 404 only for this route)". The
  claimed-but-unconfigured leg **falls through** (a load-bearing non-raise, commented as such)
  instead of raising `ERR_OIDC_DOMAIN_NOT_MAPPED`; that 403 now survives only in `oidc_callback`,
  which is post-authentication. Every unresolved case reaches the same
  `raise OIDC_NOT_CONFIGURED.exc()` with no detail/headers/extra. → the 403/404 defect flagged by
  domain-aware is **CLOSED in code**; see the login-residual ruling in §1.

Context (working folder): `apps/dashboard/components/auth/` (LoginForm — changed; SignupForm — one
additive seed) · `apps/dashboard/tests/` (new suite) · read-only for grounding:
`apps/dashboard/lib/email-domain-routing.ts`, `apps/dashboard/app/(auth)/`,
`apps/gateway/src/gateway/auth/api/oidc_router.py`. No gateway change, no BFF route, no migration,
no config knob is in this task's working folder.

Honors (patterns / conventions):
- `domain-aware-auth-routing` §3 (FROZEN @ v1) **M11, the anti-enumeration invariant** — the render
  is a pure function of (typed string, `PUBLIC_EMAIL_DOMAINS`); server state is not an input. This
  task INHERITS it onto a second surface rather than re-deriving it.
- `domain-aware-auth-routing` M6/R5 — classification changes **ORDER and EMPHASIS only, never
  PRESENCE**. Adopted verbatim here; it is also what keeps every shipped LoginForm test green.
- `signup-refusal-router` §3 (FROZEN @ v1) — routes render unconditionally; routing is CLIENT-SIDE
  STATIC; the server makes no routing decision. Preserved.
- `sso-login-oracle-closure` §3 (FROZEN @ v1) — one collapsed terminal on `oidc_login`, with a
  disclosed and Tin-accepted 302-vs-4xx residual. This task stays **inside** that accepted residual
  and does not widen it.
- The shipped ONE-SHOT seed pattern (`LoginForm`'s `?domain=`, `SignupForm`'s `?account_type=`):
  browser-only read in a `useEffect` with `[]` deps, SSR-safe, never re-fires, any unrecognized
  value is a no-op. Reused verbatim for `?email=`.
- ux-researcher persona: no finding without a named user and their job-to-be-done; accessibility is
  research, not decoration; state confidence honestly (this is a structured heuristic read of the
  real code, NOT a usability test with participants).

Seams consulted: none — no `.add/SEAMS.md` entry governs auth-entry routing.

Anchors the contract cites: `LoginForm`, `resolveSsoDomain`, `validateSsoDomain`, `handleSso`,
`handleSamlSso`, `SSO_NOT_CONFIGURED_MSG`, `SSO_PREFLIGHT_TIMEOUT_MS`, the `?domain=`/localStorage
one-shot seed, `classifyEmailDomain`, `normalizeEmailDomain`, `PUBLIC_EMAIL_DOMAINS`,
`EmailDomainClass`, `SignupForm`, its `?account_type=` seed, `LoginPage`, `loginNextTarget`,
`oidc_login` (cited as UNTOUCHED + already-collapsed).

Issues/Risks (→ feed §1):
- **R-a (SCOPE, decisive — validated against the real tests, not assumed).** A brand-new front-door
  route (`/start`, `/continue`) is NOT viable as the unified entry, because the marketing CTAs
  cannot be repointed to it without breaking green tests I may not weaken:
  `tests/landing-page.test.tsx:69` finds a link whose href is exactly `/login`;
  `tests/design-system/landing-fidelity.test.tsx:37` asserts
  `arrayContaining(["/signup", "/login", "/pricing", "/docs"])`;
  `tests/homepage-cta-intent-split.test.tsx:65` filters on `href === "/login"`;
  `tests/marketing-shell.test.tsx` covers the nav's `/login` + `/signup` pair.
  A new route the CTAs never point at is a door nobody walks through — the exact
  "payoff is invisible" failure domain-aware flagged. **Therefore the unified entry must land ON
  `/login`**, which is where the milestone's stranded persona (a member of an existing tenant,
  clicking "Log in") actually arrives.
- **R-b (DESIGN CONSTRAINT, decisive — validated against the real tests).** Progressive disclosure
  (stage 1 = email + Continue; stage 2 = reveal password/SSO/SAML) is NOT viable: shipped green
  tests reach those controls **immediately after render**, with no intervening click —
  `tests/login.test.tsx:30-32` types into `/^email$/i` + `/password/i` then clicks `/^log in$/i`;
  `tests/sso-login.test.tsx:77-79` types `/work email or domain/i` then clicks
  `/sign in with sso/i`; `tests/saml-login-affordance.test.tsx:56` asserts the SAML button
  `toBeInTheDocument()` on render; `tests/login-domain-query-seed.test.tsx:51` asserts the SSO
  field already HAS VALUE `"acme.com"` on render. Hiding any of these behind a Continue step turns
  those tests red, and weakening them is forbidden. → the "collapse" in this task's title must be
  delivered as **emphasis + ordering + a single source-of-truth email field**, never by removing or
  gating a control. This is the same shape as domain-aware's M6/R5, and it is a constraint
  discovered in the code, not a preference.
- **R-c (STRAND, the common case).** A corporate visitor at a NON-customer domain — by far the
  most likely corporate visitor — currently has **no path off /login at all**: no signup link, and
  "Sign in with SSO" answers with `SSO_NOT_CONFIGURED_MSG`, which is a refusal, not a next step.
  Fixing this is the single largest non-stranding win available on this surface and is purely
  additive.
- **R-d (SEED COLLISION, subtle — must not break a green test).** Making the ONE email field the
  source of truth means auto-seeding `ssoDomain` from `login_email`. But
  `tests/sso-login.test.tsx:119-134` types `"acme.com"` into the SSO field FIRST, then
  `"ada@acme.io"` into the email field, then asserts the SSO field STILL reads `"acme.com"` after a
  failed login. So the auto-seed must be **pristine-only**: it may never overwrite a value the
  visitor (or the `?domain=`/localStorage one-shot seed) already put there. This differs from
  `SignupForm`'s `requestEmail` sync, which DOES re-sync on every change — copying that pattern
  here would turn a green test red.
- **R-e (RESIDUAL, inherited and now materially reduced).** domain-aware's freeze flag left the
  login-surface oracle open "until `unified-signin-entry`". Verified at Ground SHA: the FIXABLE
  half is already CLOSED by `sso-login-oracle-closure` (see §0's `oidc_login` entry). The INHERENT
  half — 302-vs-4xx reveals whether a domain has SSO configured — is disclosed in `oidc_login`'s
  own docstring and Tin-accepted. The live risk for THIS task is therefore not the oracle itself
  but **amplification**: emphasizing SSO for `corporate` sends more visitors to the preflight, and
  any design that preflights on TYPING would convert a per-click signal into a per-keystroke one.
- **R-f.** `classifyEmailDomain` and `validateSsoDomain` disagree by construction
  (`isWellShapedDomain` is strictly tighter). A domain can be `corporate` yet rejected by
  `validateSsoDomain`, or accepted by `validateSsoDomain` yet `unknown`. Classification must drive
  ONLY ordering/emphasis, never gate an existing submit path, or the two predicates will fight.
- **R-g.** The `public` branch's most useful route is "create your own workspace" → `/signup`. But
  `SignupForm` has no `?email=` seed, so the visitor retypes the address they just typed —
  breaking the "type your email once" promise at exactly the handoff. A one-shot `?email=` seed
  mirroring the shipped `?account_type=` seed closes it; absent the param it is a strict no-op.
- **R-h (a11y).** LoginForm has two inputs whose accessible names both read as email-ish ("Email",
  "Work email or domain"). A screen-reader user cannot tell which one to fill. Reordering alone
  does not fix this; the lead-in must be an announced, programmatically-associated region, and the
  SSO field's help text must say it is optional and auto-filled.

Related intent: milestone `frontdoor-persona-routing` — "Every visitor who arrives at Hydroa's
front door reaches a live next step … routed to SSO, their invite link, or a request-access path
instead of a dead end." GLOSSARY: **Email-domain shape class** (domain-aware §3 — existence-blind
by construction), **Domain claim** (DNS-TXT-proven), **Access request** (unauthenticated lead).
The WHY: signup-refusal-router made three routes reachable on /signup; domain-aware made them
legible but ABOVE the field, on a page the stranded member never opens. `/login` is where that
member actually lands, and today it is the only auth surface with no escape hatch at all. This
task is where the milestone goal becomes visibly true.

Ground SHA: `9421827` — symbols cited by name; any line reference is "as of" this commit.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: One email field, typed once, drives the whole login surface — it classifies (purely,
client-side), auto-fills the SSO domain, orders the three existing sign-in affordances by what
actually fits the visitor, and — new — always offers a way OFF this page for someone who has no
account. `/login` becomes the unified entry; `/signup` and `/login` both remain reachable and
byte-compatible.

Framings weighed:
1. **Email-first emphasis ON `/login`, additive and presence-preserving (CHOSEN).** The existing
   `login_email` field becomes the single source of truth; a `[data-slot="login-entry-routes"]`
   region carries `data-domain-class` + a per-class lead-in + a per-class ORDER over affordances
   that are ALL always present, plus a new always-present "create a workspace" route.
   WHY CHOSEN: it lands the payoff on the door the stranded persona actually opens (R-a), it is
   the only shape compatible with the shipped green suite (R-b), and it inherits M11 by
   construction because it consumes the already-frozen pure classifier and adds no new input.
2. **A new `/start` front door ahead of /login and /signup.** REJECTED on evidence (R-a): the
   marketing CTAs are pinned to `/login` and `/signup` by four green tests, so `/start` would be a
   third door nobody is sent to — the payoff would again be invisible, which is the precise
   failure this task exists to end.
3. **True progressive disclosure — collapse to one field + Continue, reveal controls per class.**
   REJECTED on evidence (R-b): four shipped test files reach password/SSO/SAML immediately after
   render. Delivering it means weakening frozen tests. Recorded as a SPEC delta, not smuggled in.
4. **Server-assisted routing** (`GET /auth/entry?email=` → "password" | "sso" | "signup").
   REJECTED: this is the oracle the entire milestone exists to avoid. The adaptation itself is the
   observable — response-shaping cannot fix it, because the usefulness of the answer IS the leak.
   Re-stating domain-aware's framing 2 rejection so it is not re-litigated at build time.

Must:
<must>
  - M1 — `/login`'s `login_email` field is the ONE entry field. Its value is classified by the
    ALREADY-SHIPPED `classifyEmailDomain` from `@/lib/email-domain-routing`. This task adds no
    classifier, no second provider list, and no new normalizer.
  - M2 — The classification is a value DERIVED on every render from the typed string (mirroring
    `SignupForm`'s `const domainClass = classifyEmailDomain(email)`) — never React state, never a
    `useEffect`, never async. There must exist no seam at which a probe could later be added.
  - M3 — A `[data-slot="login-entry-routes"]` region carries `data-domain-class="public" |
    "corporate" | "unknown"` — the observable the tests assert on.
  - M4 — Exactly three static lead-in strings, one per class; `unknown` has NO lead-in and renders
    today's shipped neutral surface. The lead-in is inside the region, announced via
    `aria-live="polite"` as a PROPERTY (never `role="status"`, which PROJECT.md reserves for the
    transient loading spinner), and referenced by the region's `aria-describedby` (R-h).
  - M5 — `corporate` orders the affordances SSO → SAML → password → create-workspace, with the
    lead-in "If your team already uses Hydroa, sign in with your company account."
  - M6 — `public` orders them create-workspace → password → SSO → SAML, with the lead-in
    "Looks like a personal address — sign in, or create your own workspace."
  - M7 — `unknown` renders today's shipped order byte-identically: password → SSO → SAML, plus the
    always-present create-workspace route (M8), and no lead-in.
  - M8 — A NEW, ALWAYS-PRESENT route off this page: a plain `<a>` "Create a workspace" →
    `/signup?email=<typed>` (`&account_type=business` when the class is `corporate`; no
    `account_type` otherwise, leaving the shipped "personal" default untouched). It is a link, not
    a fetch. This is what stops the non-customer corporate visitor (R-c) and the account-less
    personal visitor from dead-ending.
  - M9 — PRESENCE IS INVARIANT: password field + "Log in", the SSO domain field + "Sign in with
    SSO", "Sign in with SAML", and the create-workspace link are ALL present in the DOM in EVERY
    class. Classification changes ORDER, EMPHASIS, and the lead-in — never presence, never copy of
    an existing control, never an existing href, never an existing handler. (Inherits domain-aware
    M6/R5; also what keeps the shipped suite green — R-b.)
  - M10 — PRISTINE-ONLY AUTO-SEED: when the visitor types in `login_email` and the SSO domain field
    has NOT been touched — neither edited by the visitor nor filled by the shipped
    `?domain=`/localStorage one-shot seed — the SSO field is filled with
    `normalizeEmailDomain(login_email)`. Once touched by either, the auto-seed NEVER overwrites it
    again (R-d). This is the actual "type your email once" collapse.
  - M11 — THE INHERITED ANTI-ENUMERATION INVARIANT, restated as a testable property for THIS
    surface: *the rendered `/login` surface is a pure function of (the typed strings, the constant
    `PUBLIC_EMAIL_DOMAINS`). Server state is NOT an input. For any two domains in the same shape
    class, the rendered surface is byte-identical regardless of whether either domain has a tenant,
    a verified claim, an SSO config, or any user.* No request is issued to compute or refresh the
    class, at any keystroke, for any domain.
  - M12 — NO NEW PREFLIGHT PRESSURE. The SSO preflight (`handleSso`) keeps firing ONLY from an
    explicit click on "Sign in with SSO", with `SSO_PREFLIGHT_TIMEOUT_MS` and
    `SSO_NOT_CONFIGURED_MSG` byte-unchanged. Typing, classifying, ordering, and the M10 auto-seed
    issue ZERO requests. The number of preflights a visitor can cause is unchanged: at most one
    per deliberate click.
  - M13 — `SignupForm` gains a ONE-SHOT `?email=` seed, mirroring its shipped `?account_type=`
    seed exactly: browser-only read, `useEffect` with `[]` deps, never re-fires, absent/empty value
    is a strict no-op leaving today's behavior byte-identical (R-g).
  - M14 — The build introduces NO gateway route, NO BFF route, NO schema change, NO migration, and
    NO config knob. The server surface after this task is byte-identical to before it. The 302-vs-4xx
    residual accepted at `sso-login-oracle-closure`'s freeze is neither widened nor narrowed here.
</must>

Reject:
<reject>
  - R1 — Any tenant / user / domain-claim / SSO-config lookup reachable from the entry-routing path
    -> "ENTRY_EXISTENCE_LOOKUP_FORBIDDEN" (contract-level; no such path may exist to return a
    runtime error).
  - R2 — Any network request issued to compute, refresh, or "confirm" the classification — expressly
    including reusing `handleSso`'s preflight as a routing or autocomplete signal, and expressly
    including firing it on typing/blur/debounce -> "ENTRY_NETWORK_PROBE_FORBIDDEN".
  - R3 — Empty input / no domain part -> class `"unknown"` -> "ENTRY_NEUTRAL_NO_DOMAIN": today's
    shipped neutral surface, no lead-in, no error shown to the visitor.
  - R4 — Malformed / non-hostname-shaped input (spaces, single label, IP literal, >253 chars)
    -> class `"unknown"` -> "ENTRY_NEUTRAL_MALFORMED_DOMAIN" — fail SAFE to neutral, never guess.
    Classification NEVER blocks a submit; `validateSsoDomain` remains the only gate on the SSO path
    (R-f).
  - R5 — Any classification that HIDES, removes, disables, or rewrites the copy/href/handler of the
    password field, "Log in", the SSO field, "Sign in with SSO", or "Sign in with SAML"
    -> "ENTRY_HIDES_AFFORDANCE" (contradicts M9 and turns the shipped suite red).
  - R6 — An auto-seed that OVERWRITES a visitor-typed or `?domain=`/localStorage-seeded SSO domain
    -> "ENTRY_SEED_CLOBBER" (R-d; a shipped green test asserts the opposite).
  - R7 — Any class in which no route off the page exists — i.e. the create-workspace route absent or
    conditional -> "ENTRY_STRANDS_VISITOR" (R-c).
  NOTE (deliberate, inherited from domain-aware §1 assumption 2): these are contract-level and
  build-time refusals, not HTTP codes, because M14 adds no server surface. Inventing an endpoint so
  there is something to return 4xx from would itself be the oracle. Each refusal is a real failing
  test.
</reject>

After:
<after>
  - A member of an existing tenant types their work email ONCE at `/login` and is led with their
    company sign-in — SSO domain already filled — instead of choosing between three buttons and two
    email fields.
  - A visitor with no account who lands on `/login` has a visible, always-present way forward
    ("Create a workspace"), carrying the email they already typed. Nobody dead-ends on this page.
  - The corporate visitor at a NON-customer domain sees the same surface as one at a customer
    domain, and reaches signup rather than a refusal message.
  - Neither the render nor any keystroke tells anyone — including the visitor — whether their
    domain is a Hydroa customer.
  - The milestone goal is visibly true at the door people actually open.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ 1. **That the "collapse" this task's title promises is acceptably delivered as emphasis +
     ordering + a single source-of-truth email field, rather than as a literal one-field/Continue
     step that hides password/SSO/SAML until a class is known.** Lowest confidence because it is a
     product-intent question, not a technical one, and my answer is forced by evidence rather than
     chosen: R-b shows four shipped green test files reach those controls immediately after render,
     so a literal collapse means weakening frozen tests — which is forbidden. If Tin wants the
     literal collapse, this is not a build-time tweak: it needs those suites re-specified as a
     change request, and it should be its own task. Cost if wrong: we ship a surface that is
     genuinely better but reads as "reordered", not "unified", against the title.
  - [ ] 2. That `/login` — not a new `/start` — is the right home for the unified entry. Grounded in
     R-a (four green tests pin the CTAs) plus the milestone's own persona (a member of an existing
     tenant clicks "Log in"). If wrong: a `/start` route plus repointing the CTAs is a follow-on
     task with its own contract, not a widening of this one.
  - [ ] 3. That the login-surface residual handoff is DISCHARGED by verification rather than by new
     code here. Verified at Ground SHA: `oidc_login` carries the one collapsed 404 terminal and no
     longer raises `ERR_OIDC_DOMAIN_NOT_MAPPED`; the fixable defect is closed. What remains is the
     inherent, Tin-accepted 302-vs-4xx signal. My obligation is M12 (no new preflight pressure) and
     R2 (never a routing signal). Recommend: accept and close the handoff. If wrong: the remedy is
     rate-limiting the preflight, which is a gateway task, not a dashboard one.
  - [ ] 4. That `corporate` should lead with SSO. Inherited unresearched from domain-aware §1
     assumption 5, and now slightly stronger evidence-wise: the collapsed terminal means an
     unconfigured domain gets one uniform refusal, and M8 guarantees a route off the page even then.
     Cheap to reorder; changes no invariant.
  - [ ] 5. That the pristine-only auto-seed (M10) is the right rule rather than always-sync or
     never-sync. Grounded directly in a shipped green assertion (R-d). Confirm; if wrong, only M10's
     predicate changes.
  - [ ] 6. That `?email=` on `/signup` is safe to seed. It is a non-secret the visitor just typed,
     already travels as `?domain=` on the SSO link today, and lands in a field they can edit. Note it
     WILL appear in referrer/history — same exposure class as the shipped `?domain=`.
  - [ ] 7. That this task is dashboard-only (M14). Confirmed by reading `oidc_router.py` — no gateway
     change is needed, because every destination this surface offers already exists and works.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Corporate work email leads with company sign-in and fills the SSO domain   # M1,M5,M10
  Given a visitor is on /login with an empty form
  When they type "dana@acme-corp.com" into the Email field
  Then the [data-slot="login-entry-routes"] region has data-domain-class="corporate"
  And the corporate lead-in "If your team already uses Hydroa, sign in with your company account."
      is announced in the region
  And the affordances are ordered SSO, SAML, password, create-workspace
  And the "Work email or domain" field now reads "acme-corp.com" without the visitor typing it
  And zero network requests were issued

Scenario: Personal address leads with create-a-workspace   # M1, M6, M8
  Given a visitor is on /login
  When they type "Bob@GMAIL.com" into the Email field
  Then the region has data-domain-class="public"
  And the lead-in "Looks like a personal address — sign in, or create your own workspace." shows
  And the affordances are ordered create-workspace, password, SSO, SAML
  And the create-workspace link points at "/signup?email=Bob%40GMAIL.com"
  And zero network requests were issued

Scenario: Corporate visitor at a NON-customer domain is not stranded   # M8, R7 — THE COMMON CASE
  Given the domain "nobody-here.example" has no tenant, no claim and no SSO config
  When a visitor types "dana@nobody-here.example" into the Email field
  Then the region has data-domain-class="corporate"
  And a "Create a workspace" link is present pointing at
      "/signup?email=dana%40nobody-here.example&account_type=business"
  And that link is reachable by keyboard from the email field without leaving the region
  And no request was issued that could have revealed the domain is a stranger

Scenario: A customer domain and a stranger domain render byte-identically   # M11 (the invariant)
  Given the tenant "Acme" holds a VERIFIED domain claim on "acme-corp.com" with OIDC configured
  And "nobody-here.example" has no tenant, no claim and no SSO config
  When a visitor types "dana@acme-corp.com" and, separately, "dana@nobody-here.example"
  Then the rendered [data-slot="login-entry-routes"] markup is BYTE-IDENTICAL for both,
       apart from the domain string echoed back from what the visitor themselves typed
  And no request carrying either domain was issued in either case

Scenario: Adversary probing a domain list learns nothing   # M11, R2 — THE ADVERSARY CASE
  Given an attacker holds a list of 10,000 candidate customer domains
  And some have verified claims, tenants and SSO configured, and the rest are strangers
  When the attacker scripts typing each domain into the /login Email field and records
       (a) every outbound request, (b) the rendered surface, and (c) the time to render
  Then no outbound request is issued for ANY domain
  And the rendered surface differs ONLY by shape class, which the attacker could compute offline
      from the public provider list alone
  And the attacker's posterior on "is this domain a Hydroa customer" is UNCHANGED from its prior
  And the SSO preflight was never invoked, because no "Sign in with SSO" click occurred

Scenario: Nothing typed yet renders the shipped neutral surface   # M7, R3
  Given a visitor opens /login
  When the page renders
  Then the region has data-domain-class="unknown"
  And no lead-in is present
  And the order is password, SSO, SAML, create-workspace
  And no error, hint or warning is shown to the visitor

Scenario: Malformed entry falls back to neutral and never blocks a submit   # R4, R-f
  Given a visitor is on /login
  When they type each of "bob@localhost", "bob@192.168.1.1", "bob@ acme.com", "bob@-acme.com",
       and "bob@" followed by a 300-character label
  Then every one classifies as "unknown" and renders the neutral surface
  And no error is shown to the visitor and no request is issued
  And the "Log in" button remains enabled and its handler is unchanged

Scenario: A visitor-typed SSO domain is never clobbered by the auto-seed   # M10, R6
  Given a visitor is on /login
  When they type "acme.com" into the "Work email or domain" field
  And THEN type "ada@acme.io" into the Email field
  Then the "Work email or domain" field still reads "acme.com"
  And the classification of the Email field is still applied to the region

Scenario: A ?domain= seeded SSO field is never clobbered by the auto-seed   # M10, R6
  Given a visitor opens "/login?domain=acme.com"
  When they then type "ada@other-co.com" into the Email field
  Then the "Work email or domain" field still reads "acme.com"
  And the shipped ?domain=-over-localStorage precedence is unchanged

Scenario: Every affordance stays present in every class   # M9, R5
  Given a visitor is on /login
  When the classification is "public", then "corporate", then "unknown" in turn
  Then in every case the password field, "Log in", the "Work email or domain" field,
       "Sign in with SSO", "Sign in with SAML" and the create-workspace link are ALL in the DOM
  And each one's copy, href and handler are byte-identical to what shipped

Scenario: Typing never fires the SSO preflight   # M12, R2
  Given fetch, XMLHttpRequest and navigator.sendBeacon are instrumented
  When a visitor types a full email address one character at a time into the Email field
  Then none of them is called
  And the SSO preflight fires only after an explicit click on "Sign in with SSO",
      with SSO_PREFLIGHT_TIMEOUT_MS and SSO_NOT_CONFIGURED_MSG byte-unchanged

Scenario: Subdomain of a public provider is corporate, not public   # M1 edge (inherited)
  Given the provider list contains "gmail.com"
  When a visitor types "bob@mail.gmail.com"
  Then the classification is "corporate" (exact match only)
  And no request is issued

Scenario: The signup email seed is one-shot and a no-op when absent   # M13
  Given a visitor opens "/signup?email=dana%40acme-corp.com"
  Then the signup Email field is pre-filled with "dana@acme-corp.com"
  And a later manual edit of that field is never re-overwritten
  And opening plain "/signup" leaves the Email field empty, byte-identical to today

Scenario: A screen-reader user completes the corporate path without guessing   # M4, R-h
  Given a screen-reader user is on /login
  When they type a corporate work email into the Email field
  Then the lead-in is announced via the region's aria-live="polite"
  And the region is programmatically associated with its lead-in via aria-describedby
  And the "Work email or domain" field is described as optional and auto-filled
  And the visitor reaches "Sign in with SSO" by keyboard without re-reading the whole form

Scenario: The server surface is unchanged   # M14
  Given the gateway and BFF route tables before this task
  When the task is complete
  Then the set of routes, their responses, the schema and the config knobs are identical
  And oidc_login still has exactly one collapsed 404 terminal and raises no
      ERR_OIDC_DOMAIN_NOT_MAPPED
  And no migration was added
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
SERVER SURFACE — INTENTIONALLY EMPTY (M14)
  No new gateway route. No new BFF route. No schema change. No migration. No config knob.
  The 302-vs-4xx residual accepted at sso-login-oracle-closure's freeze is neither widened nor
  narrowed. This is a contract term, not an omission: the security property of this task is
  delivered by the ABSENCE of a server surface, so adding one is a contract violation.

CONSUMED UNCHANGED — apps/dashboard/lib/email-domain-routing.ts   (domain-aware §3, FROZEN @ v1)
  classifyEmailDomain(raw) -> "public" | "corporate" | "unknown"
  normalizeEmailDomain(raw) -> string
  PUBLIC_EMAIL_DOMAINS : ReadonlySet<string>
  This task ADDS NOTHING to this module and MODIFIES NOTHING in it. No second classifier, no
  second provider list, no second normalizer. Its P3 (IO-free import graph) and P4 (list parity)
  guards remain the property of domain-aware and must stay green.

CHANGED COMPONENT — apps/dashboard/components/auth/LoginForm.tsx:LoginForm
  The surface gains, and ONLY gains:

  1. A derived classification, computed on every render, never state, never an effect:
       const entryClass: EmailDomainClass = classifyEmailDomain(email);
     (mirrors SignupForm's shipped `const domainClass = classifyEmailDomain(email)`)

  2. A region wrapping the sign-in affordances:
       <div data-slot="login-entry-routes" data-domain-class={entryClass}
            aria-describedby={leadInId | undefined}>
     data-domain-class is THE observable the tests assert on.

  3. A lead-in line — exactly three static strings, rendered inside the region with
     aria-live="polite" (a PROPERTY, not a role) and id={leadInId}. Deliberately NOT
     role="status": PROJECT.md (folded foundation-version 40, from model-catalog-paging-search)
     reserves role="status" for the transient loading spinner across this dashboard, and this
     lead-in is persistent. SignupForm's own role="status" usages are transient request-access
     confirmations, so they are not a precedent for this one:
       public    -> "Looks like a personal address — sign in, or create your own workspace."
       corporate -> "If your team already uses Hydroa, sign in with your company account."
       unknown   -> (no lead-in; today's shipped neutral surface, byte-identical)

  4. An ORDER over affordances that are ALL present in EVERY class (M9/R5):
       public    -> [create-workspace, password+Log in, SSO field+Sign in with SSO, SAML]
       corporate -> [SSO field+Sign in with SSO, SAML, password+Log in, create-workspace]
       unknown   -> [password+Log in, SSO field+Sign in with SSO, SAML, create-workspace]
                    (today's shipped order, with create-workspace appended)
     Reordering MUST move whole subtrees, never rewrite a control in place.

  5. A NEW always-present route off the page (M8/R7) — a plain anchor, never a fetch:
       <a href={createWorkspaceHref}>Create a workspace</a>
       createWorkspaceHref =
         "/signup?email=" + encodeURIComponent(email)
         + (entryClass === "corporate" ? "&account_type=business" : "")
       email === ""  ->  "/signup"   (no empty param)

  6. PRISTINE-ONLY SSO AUTO-SEED (M10/R6):
       ssoDomain is auto-filled with normalizeEmailDomain(email) on each Email-field change
       ONLY while ssoDomainTouched === false.
       ssoDomainTouched flips to true, permanently, on EITHER:
         (a) the SSO field's own onChange, or
         (b) the shipped one-shot ?domain= / localStorage seed effect assigning a value.
       Once true it is never reset. The auto-seed may NEVER overwrite a touched value.

  UNCHANGED — byte-identical, re-verified by reading, not modified:
    - handleSubmit: Zod validation, POST /api/auth/login credentials:"include",
      200 -> router.push(nextPath), 401/error -> inline problem+json title, no navigation
      (LoginForm §3 v2 behaviors 1-4)
    - handleSso: the preflight target, redirect:"manual", SSO_PREFLIGHT_TIMEOUT_MS,
      SSO_NOT_CONFIGURED_MSG, the persist-only-on-confirmed-good rule, the degrade-on-throw path,
      and the empty-field env-level fallback
    - handleSamlSso and SAML_LOGIN_PATH
    - resolveSsoDomain and validateSsoDomain — exported, directly unit-tested, NOT retired and NOT
      tightened (R-f: tightening changes which domains reach the gateway)
    - the ?domain=-over-localStorage one-shot seed precedence
    - every accessible name: "Email", "Password", "Work email or domain", "Log in",
      "Sign in with SSO", "Sign in with SAML"

CHANGED COMPONENT — apps/dashboard/components/auth/SignupForm.tsx:SignupForm   (M13, additive)
  ONE additional one-shot seed effect, mirroring the shipped ?account_type= seed EXACTLY:
    useEffect(() => { const e = searchParams.get("email"); if (e) setEmail(e); }, []);
  Browser-only, [] deps, never re-fires, absent/empty value is a strict no-op. Nothing else in
  SignupForm changes — the frozen [data-slot="signup-alt-routes"] panel, its data-domain-class,
  its lead-ins, its ORDER and all three routes are untouched.

UNCHANGED — apps/gateway/src/gateway/auth/api/oidc_router.py:oidc_login
  NOT touched by this task. Verified at Ground SHA to already carry sso-login-oracle-closure §3's
  ONE collapsed 404 terminal, with ERR_OIDC_DOMAIN_NOT_MAPPED no longer raised on this route.

THE INHERITED ANTI-ENUMERATION INVARIANT (M11) — restated as a testable property for /login:
  render(/login, typed) is a PURE FUNCTION of (the typed strings, PUBLIC_EMAIL_DOMAINS).
  Server state is NOT an input. Therefore, for any two domains in the same shape class, the
  rendered surface is byte-identical regardless of tenant / claim / user / SSO-config existence.
  VERIFIED BY, at minimum (the login-surface analogues of domain-aware's P1-P4):
    (Q1) a render test over a domain with a seeded verified claim + OIDC config and a domain with
         neither, asserting byte-identical [data-slot="login-entry-routes"] markup;
    (Q2) an instrumentation test asserting fetch / XMLHttpRequest / sendBeacon are called ZERO
         times across a full character-by-character type-in of an email address, AND that the
         preflight fires on an explicit "Sign in with SSO" click and only then;
    (Q3) a source assertion that LoginForm's entry-routing path reaches classification only via
         the import-free email-domain-routing module — no lookup, no probe, no debounce timer;
    (Q4) domain-aware's own P3 + P4 guards still green (this task must not weaken them).
  Non-vacuity, deliberately: Q1 and Q2 would BOTH pass against a LoginForm that does nothing at
  all — "identical markup" and "zero requests" are trivially true of an unbuilt feature. Every
  Q1/Q2 test MUST therefore also assert the expected data-domain-class and the expected order, so
  each is red on the missing implementation and probes BOTH failure directions (the leak AND the
  silent-but-useless classifier). This is the milestone's own recurring vacuous-test lesson.
  A failure of ANY of Q1-Q4 is a SECURITY HARD-STOP, never a flaky-test retry.

CONTRACT-LEVEL REFUSALS (no HTTP codes — M14 adds no server surface):
  ENTRY_EXISTENCE_LOOKUP_FORBIDDEN  -> Q3 source assertion fails the build
  ENTRY_NETWORK_PROBE_FORBIDDEN     -> Q2 instrumentation assertion fails the build
  ENTRY_HIDES_AFFORDANCE            -> presence-in-every-class test fails the build; the shipped
                                       login.test / sso-login.test / saml-login-affordance.test /
                                       login-domain-query-seed.test suites also turn red
  ENTRY_SEED_CLOBBER                -> the two auto-seed scenarios fail the build
  ENTRY_STRANDS_VISITOR             -> the create-workspace-present-in-every-class test fails
  ENTRY_NEUTRAL_NO_DOMAIN           -> class "unknown", neutral render, no visitor-facing error
  ENTRY_NEUTRAL_MALFORMED_DOMAIN    -> class "unknown", neutral render, no visitor-facing error
```

Glossary deltas: **Unified sign-in entry** (NEW term) — the `/login` surface after this task: a
single typed email drives an existence-blind shape classification, the SSO domain auto-fill, and
the ORDER of the sign-in affordances, all of which remain present in every class. It is an
EMPHASIS mechanism, never a gate: it never hides an affordance, never blocks a submit, and never
consults the server. Distinct from **Email-domain shape class** (the classification itself,
domain-aware §3) and emphatically not a statement that a domain is or is not a customer. [folded foundation-version 55]

Reported: no — the orchestrator brings this to Tin.

Least-sure flag surfaced at freeze: [scope] **this task delivers the milestone's "one routed
Continue-with-email step" as emphasis + ordering + a single source-of-truth email field, NOT as a
literal one-field/Continue step that hides password/SSO/SAML until a class is known — and that is
forced by evidence, not chosen.** Four shipped green test files reach those controls immediately
after render with no intervening click (`login.test.tsx:30-32`, `sso-login.test.tsx:77-79`,
`saml-login-affordance.test.tsx:56`, `login-domain-query-seed.test.tsx:51`), so the literal collapse
would require weakening frozen tests, which is forbidden. THE DECISION REQUIRED: (a) freeze as
drafted — the surface genuinely unifies (type once, SSO auto-fills, order fits the visitor, nobody
dead-ends) but reads as "reordered" against the task title; (b) re-specify those four suites as an
explicit change request and deliver the literal two-stage collapse as its own task; or (c) freeze as
drafted now and seed (b) as a follow-on. Cost if wrong: a surface that is better but under-delivers
against the title's promise. Least confident because this is product intent, not a technical
question, and it is not mine to settle. · [scope] the unified entry lands on `/login` rather than a
new `/start` front door, because the marketing CTAs are pinned to `/login` and `/signup` by four
green tests (§0 R-a) and a door nobody is sent to would repeat the invisible-payoff failure this
task exists to end. · [security — RESOLVED, recorded for the freeze] the login-surface residual
handoff domain-aware left open "until unified-signin-entry" is DISCHARGED: verified at Ground SHA
that `oidc_login` already carries sso-login-oracle-closure's ONE collapsed 404 terminal and no
longer raises `ERR_OIDC_DOMAIN_NOT_MAPPED`, so the fixable defect is closed; the remaining
302-vs-4xx signal is inherent to domain-based SSO discovery and already Tin-accepted. This task's
obligation is M12 (no new preflight pressure — the preflight stays bound to one deliberate click)
and R2 (never a routing signal), both contracted above. · [UX] `corporate` leads with SSO rather
than password — inherited unresearched from domain-aware §1 assumption 5; cheap to reorder, changes
no invariant. · [a11y] the lead-in uses `aria-live="polite"` as a property (NOT `role="status"`,
which PROJECT.md's folded v40 convention reserves for the transient loading spinner — caught at
CONTRACT by the freeze-flag cross-artifact check, not at build) and fires on every keystroke that
changes the class; if it proves chatty for screen-reader users under real use, the fix is to
announce on blur instead — a copy/attribute change, no invariant moved. Confidence: heuristic read
of the real components, not a screen-reader session with a participant.

Status: FROZEN @ v1 — approved by Tin Dang, 2026-07-21.

FREEZE DECISION on the least-sure flag above: Tin chose **(c) — freeze as drafted AND seed the
follow-on**. The drafted surface ships now (unify via emphasis + ordering + one source-of-truth email
field: type your email once, SSO auto-fills while pristine, order fits the visitor's class, and
"Create a workspace" means nobody dead-ends). The LITERAL one-field "Continue with email" two-stage
collapse is seeded as its own follow-on task carrying its own explicit change request against the four
green suites that reach password/SSO/SAML immediately after render — it is NOT delivered here, and no
frozen test is weakened to fake it. Recorded as a §7 spec delta.

The other flags are accepted as drafted and are NOT open questions: the entry lands on `/login` (not a
new `/start`) because the marketing CTAs are pinned there; the login-surface security residual handed
over by domain-aware is DISCHARGED (sso-login-oracle-closure shipped the single collapsed 404 terminal,
gate=PASS 2026-07-21) leaving only the Tin-accepted 302-vs-4xx SSO-discovery signal; `corporate` leading
with SSO is an inherited unresearched assumption, cheap to reorder, moves no invariant; the lead-in's
`aria-live="polite"` is a deliberate property choice over `role="status"` (PROJECT.md v40 reserves that
role for the transient loading spinner) and may move to announce-on-blur if it proves chatty.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% of the LoginForm entry-routing delta; the repo floor
(`vitest.config.ts` coverage.thresholds.lines = 80 over `components/**` + `lib/**`) holds.

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  ENTRY ROUTING — `apps/dashboard/tests/unified-signin-entry.test.tsx`
  - test_corporate_email_classifies_corporate_and_orders_sso_first: data-domain-class="corporate",
    corporate lead-in present, order [sso, saml, password, create-workspace] · covers: M1,M3,M4,M5
  - test_corporate_email_autofills_the_sso_domain_field · covers: M10
  - test_public_email_classifies_public_and_leads_with_create_workspace: order
    [create-workspace, password, sso, saml] + href "/signup?email=Bob%40GMAIL.com" · covers: M6,M8
  - test_case_and_whitespace_do_not_change_the_class · covers: M1 (inherited M2)
  - test_non_customer_corporate_visitor_is_not_stranded: create-workspace link present with
    &account_type=business, keyboard-reachable · covers: M8, R7
  - test_nothing_typed_renders_the_shipped_neutral_surface: class "unknown", no lead-in, order
    [password, sso, saml, create-workspace] · covers: M7, R3
  - test_malformed_entry_falls_back_to_neutral_and_never_blocks_submit: the §2 malformed list;
    "Log in" stays enabled · covers: R4, R-f
  - test_subdomain_of_public_provider_is_corporate · covers: M1 edge
  - test_all_affordances_present_in_public_corporate_and_unknown: 4/4/4 recorded · covers: M9, R5
  - test_affordance_copy_href_and_handler_are_byte_identical: asserted in the class that REORDERS
    them, so reorder-by-rewrite fails · covers: M9
  - test_visitor_typed_sso_domain_is_never_clobbered: SSO field typed FIRST, then the Email field
    — mirrors sso-login.test.tsx:119-134's ordering exactly · covers: M10, R6
  - test_query_param_seeded_sso_domain_is_never_clobbered · covers: M10, R6
  - (Q1) test_q1_customer_and_stranger_domain_surfaces_are_byte_identical: seeded verified claim +
    OIDC config vs neither; region outerHTML compared literally, modulo the typed domain; ALSO
    asserts the expected data-domain-class (non-vacuity) · covers: M11
  - (Q2) test_q2_zero_requests_across_a_full_character_by_character_type_in · covers: M11, M12, R2
  - (Q2) test_q2_adversary_probing_many_domains_issues_no_request: THE ADVERSARY CASE · covers: M11
  - (Q2) test_q2_preflight_fires_only_on_an_explicit_sso_click_and_is_byte_unchanged · covers: M12
  - (Q3) test_q3_entry_routing_path_reaches_no_lookup_probe_or_debounce_timer · covers: R1, R2
  - (a11y) test_screen_reader_reaches_sso_without_guessing: lead-in announced via aria-live,
    region aria-describedby wired, SSO field described as optional/auto-filled · covers: M4, R-h

  SIGNUP EMAIL SEED — `apps/dashboard/tests/signup-email-seed.test.tsx`
  - test_email_query_param_prefills_the_signup_email_field · covers: M13
  - test_seed_is_one_shot_and_a_later_manual_edit_wins · covers: M13
  - test_absent_email_param_is_a_no_op_byte_identical_to_today · covers: M13

  REGRESSION (must stay green, NOT rewritten — the presence invariant's real proof):
    login.test.tsx · sso-login.test.tsx · saml-login-affordance.test.tsx ·
    login-domain-query-seed.test.tsx · signup-refusal-router.test.tsx ·
    signup-domain-aware-routing.test.tsx · email-domain-routing.test.ts (Q4) ·
    auth-hardening.test.tsx · a11y-coverage.test.tsx · design-system/auth-pages-redesign.test.tsx
</test_plan>

Tests live in: `apps/dashboard/tests/` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/dashboard/components/auth/LoginForm.tsx` `apps/dashboard/components/auth/SignupForm.tsx` `apps/dashboard/tests/`
Strategy (ordered batches):
  1. Write the red suite (§4) FIRST and confirm it is red for the RIGHT reason — missing
     `data-domain-class`, not a broken harness. Record the red output verbatim.
  2. LoginForm: extract the three affordances (password+Log in · SSO field+Sign in with SSO ·
     SAML) into named subtrees defined ONCE, exactly as SignupForm defines ssoRoute/inviteRoute/
     requestAccessRoute. Reorder by moving whole subtrees — never by rewriting one in place. This
     is what makes M9/R5 structurally true instead of asserted.
  3. Add the derived `entryClass`, the region wrapper, the lead-in, and the create-workspace link.
  4. Add the pristine-only auto-seed with its `ssoDomainTouched` flag; wire the flag from BOTH the
     SSO field's onChange AND the shipped `?domain=`/localStorage seed effect.
  5. SignupForm: the one-shot `?email=` seed, copying the `?account_type=` effect's shape verbatim
     (including its scoped `react-hooks/set-state-in-effect` disable).
  6. Run the REGRESSION list in §4 before declaring green — the presence invariant's real proof is
     that four shipped suites never needed touching.

Persona (required): `frontend-engineer` (`.add/personas/frontend-engineer.md` — "the dashboard is a
trust boundary before it is a UI"; BFF trust-boundary discipline, SSR-safety, design-token
fidelity), with `appsec-engineer` as the advisor lens for the M11/Q1-Q4 checks.
Spawn isolation (default): `isolation: "worktree"` — four verify agents are concurrently reading
this tree and several tasks' uncommitted work lives in it; a shared-tree build is not safe here.
Known-problem fixes:
  - trap: copying SignupForm's `requestEmail` always-resync sync → turns `sso-login.test.tsx:134`
    red. fix: the pristine-only `ssoDomainTouched` rule (M10/R6).
  - trap: hiding or gating an affordance to "collapse" the surface → turns four shipped suites red
    and trips ENTRY_HIDES_AFFORDANCE. fix: order and emphasis only (M9/R5).
  - trap: reordering by rewriting a control in place → silently drifts frozen copy/href. fix:
    define each affordance once, move whole subtrees (batch 2).
  - trap: adding a debounce/timer around classification "for performance" → creates the async seam
    where a probe can later be added. fix: derived-on-render, no timer (M2, Q3).
  - trap: tightening `validateSsoDomain` to match `isWellShapedDomain` → changes which domains
    reach the gateway, i.e. shipped SSO behavior. fix: leave both, classification never gates a
    submit (R-f/R4).
  - trap: SSR/hydration mismatch from reading searchParams outside an effect. fix: the shipped
    one-shot `[]`-deps effect pattern, verbatim.
  - trap: running the suite leaves `.coverage`/`.pytest_cache` and poisons the gate scope-walk.
    fix: clean them as the LAST pre-gate step.
Strategy actually used: <fill at VERIFY>
Safety rule (feature-specific): the entry-routing path must be computable with the network cable
unplugged — classification, ordering, the lead-in, the create-workspace href, and the auto-seed
are all pure functions of what the visitor typed plus a compile-time constant. If any of them ever
needs a request, the design is wrong, not the invariant.
Code lives in: `apps/dashboard/components/auth/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — re-run FIRST-HAND by the verifier, not taken from the build report: the 2 new
      files 21/21; the §4 regression list 10 files / 74 tests; the FULL legacy project 112 files /
      1054 tests, all green. Green-bar `vitest (ci.yml dashboard job, working-directory: apps/dashboard)`.
      `eslint` on both touched components clean; `tsc --noEmit -p .` exit 0.
- [x] coverage did not decrease — MEASURED, not inferred: LoginForm.tsx 95.45% stmts / 95.32% lines /
      100% funcs; SignupForm.tsx 94.39% / 94.28% / 93.33% funcs. Both clear the §4 90% target and the
      repo's 80% floor.
- [x] no test or contract was altered during build — `git diff` byte-EMPTY on all four frozen suites
      (`login`, `sso-login`, `saml-login-affordance`, `login-domain-query-seed`) and on the frozen
      `lib/email-domain-routing.ts`. Independently confirmed by BOTH the orchestrator and the verifier.
- [x] the green was EARNED, not gamed — refute-read EARNED; see the verdict block below, incl. the
      per-test classification of all 21 and the mutant-construction check on the two non-clobber tests.
- [x] concurrency / timing of the risky operation is safe — no new async/timing logic; the seed effect
      and the auto-seed handler are synchronous state updates, and `handleSso`'s preflight fetch is
      byte-unchanged. The mount-vs-first-keystroke race was probed directly and is not reachable
      (`[]`-deps effects fire on mount before the component is interactive).
- [x] no exposed secrets, injection openings, or unexpected dependencies — the whole derivation path
      (`entryClass` / `leadInText` / `leadInId` / `createWorkspaceHref` / the SSO auto-seed) is a pure
      function of the typed `email` plus the frozen IO-free module; zero IO, verified three ways.
- [x] layering & dependencies follow CONVENTIONS.md — M14 holds: the gateway diff on this tree contains
      ZERO mentions of this task (`grep -ic` = 0); it is 100% attributable to sibling in-flight tasks,
      confirmed by CONTENT attribution rather than assumed from git-status presence.
- [ ] a person reviewed and approved the change — gate path is AUTO (autonomy: auto, no security
      finding). Tin's approval at the §3 FREEZE is recorded there; he has not been asked to tick this
      verify checkbox and it is NOT claimed on his behalf.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] Typing `dana@acme-corp.com` into `/login`'s Email field makes `[data-slot="login-entry-routes"]` carry `data-domain-class="corporate"`, show the corporate lead-in, order the affordances SSO → SAML → password → create-workspace, and fill "Work email or domain" with `acme-corp.com` — with the visitor having typed the domain exactly once — confirmed by the green `unified-signin-entry.test.tsx` corporate cases under `vitest (ci.yml dashboard job, working-directory: apps/dashboard)`
- [x] Typing `Bob@GMAIL.com` yields `data-domain-class="public"`, the public lead-in, order create-workspace → password → SSO → SAML, and a create-workspace anchor whose href is exactly `/signup?email=Bob%40GMAIL.com` — confirmed by the same suite
- [x] A visitor at a NON-customer corporate domain still sees a present, keyboard-reachable "Create a workspace" link carrying `&account_type=business` — nobody dead-ends on `/login` in any class — confirmed by the not-stranded and presence-in-every-class cases
- [x] Instrumented `fetch` / `XMLHttpRequest.open` / `navigator.sendBeacon` all report ZERO calls across a full character-by-character type-in and across an adversary loop over many domains; the SSO preflight fires only after an explicit "Sign in with SSO" click — confirmed by the Q2 cases
- [x] The `[data-slot="login-entry-routes"]` outerHTML for a domain with a seeded verified claim + OIDC config is byte-identical to one with neither, modulo the echoed typed domain, AND the expected `data-domain-class` is asserted in the same test so the assertion cannot pass vacuously — confirmed by the Q1 case
- [x] `login.test.tsx`, `sso-login.test.tsx`, `saml-login-affordance.test.tsx` and `login-domain-query-seed.test.tsx` are green WITHOUT having been edited — this is the real proof that no affordance was hidden, gated or rewritten (M9/R5) — confirmed by `git diff --stat` showing zero changes under those paths plus the green bar
- [x] `email-domain-routing.ts` shows zero changes in `git diff` and domain-aware's P3/P4 guards are green — this task consumed the frozen module rather than extending it (Q4)
- [x] Opening plain `/signup` leaves the Email field empty and behaves byte-identically to today, while `/signup?email=…` pre-fills it and a later manual edit is never re-overwritten — confirmed by `signup-email-seed.test.tsx`
- [x] The gateway diff is empty: no route, schema, migration or config knob added, and `oidc_router.py:oidc_login` still has exactly one collapsed 404 terminal with no `ERR_OIDC_DOMAIN_NOT_MAPPED` raise (M14) — confirmed by `git diff apps/gateway` being empty

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced in the render tree, confirmed by reading the FULL
      478-line file rather than sampling: `entryClass`, `leadInText`, `leadInId`, `createWorkspaceHref`,
      `ssoDomainTouched`, `SSO_DOMAIN_HELP_ID`, and the four route consts.
- [x] DEAD-CODE (code) — no new unused or orphaned symbol. M9/R5 whole-subtree-move discipline confirmed
      STRUCTURALLY: each of `passwordRoute` / `ssoRoute` / `samlRoute` / `createWorkspaceRoute` is a
      `const` defined exactly ONCE and referenced 3× across the three class branches — reorder-by-move,
      not reorder-by-rewrite. Nothing became conditionally absent for any class.
- [ ] SEMANTIC (prose / non-code) — n/a, code task.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
> Re-resolve every symbol §3 cites against the CURRENT tree (code moved since Ground SHA) — catch a stale anchor here, not later.
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — re-resolved against the CURRENT
      tree by direct read of the full files (not diff-only): `LoginForm`, `resolveSsoDomain`,
      `validateSsoDomain`, `handleSso`, `handleSamlSso`, `SSO_NOT_CONFIGURED_MSG`,
      `SSO_PREFLIGHT_TIMEOUT_MS`, the `?domain=`/localStorage seed effect, `classifyEmailDomain`,
      `normalizeEmailDomain`, `PUBLIC_EMAIL_DOMAINS`, `EmailDomainClass`, `SignupForm`, `oidc_login`.
- [x] any anchor that moved/renamed since Ground SHA is named here — NONE moved, renamed, or went stale.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under auto, record the earned-green refute-read (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). Audit-measured (`refute_unrecorded`), never blocked; a human spot-audit is the backstop.
Verdict: EARNED
By: agent acf34d7 (independent add-verify) · adversarially checked:
(a) **Attribution, not git-status presence** — this working tree is shared by 8 uncommitted sibling
tasks, so `git diff --stat` alone is MISLEADING: `SignupForm.tsx` shows a ~250-line diff of which ~95%
belongs to the already-gate=PASS `signup-refusal-router` / `domain-aware-auth-routing`. This task's real
SignupForm delta is exactly the one `?email=` `useEffect([])` block, established by content attribution.
(b) **All 21 tests individually classified** — 20 genuine new-behavior asserts, 1 legitimate regression
guard, **0 vacuous remaining**. All 18 in `unified-signin-entry.test.tsx` require
`[data-slot="login-entry-routes"]` to exist (`getRegion()` throws without it), so none can pass against
unbuilt code. For BOTH M10/R6 non-clobber tests the verifier constructed a mutant (skip
`setSsoDomainTouched(true)` on the query-param branch; copy SignupForm's always-resync pattern) and
confirmed each mutant flips its test red — non-vacuity by construction, not by reading the comment.
⚠ HONEST GAP, recorded not smoothed: `signup-email-seed.test.tsx`'s
`test_absent_email_param_is_a_no_op_byte_identical_to_today` was already true pre-build (an empty field
is the pre-existing default), so it never ran RED for the right reason — technically at odds with §4's
"MUST run red" read literally. It is NOT a cheat: it directly implements the contracted no-op
requirement (R-g / M13 / the scenario's third clause) and guards a real future regression (wiring the
effect to fire on ANY searchParams change). Flagged rather than silently counted as genuine.
(c) **Pristine-seed ordering edge cases EXERCISED, not reasoned about** — the verifier wrote and ran
three scratch adversarial tests (deleted after, never committed). Mount-vs-first-keystroke race: with
`?domain=acme.com`, the SSO field already reads `acme.com` immediately after `render()`, before any
typing — not reachable. Clear-to-empty: typed `acme.com` into the SSO field, `user.clear()`'d it, then
typed a full corporate email — the SSO field STAYS empty, confirming `onChange` flips `touched`
regardless of resulting value. That is literally what the contract says; recorded as a 💭 UX nit (a
visitor who clears the field intending "let auto-fill take over again" does not get that), not a defect.
(d) eslint + `tsc --noEmit` clean on both touched files.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
> Lenses run in order; a Security HARD-STOP ends the checklist (leave the rest blank). Binding for sensitivity: mechanical (advisor-gate-relax); advisory otherwise. Audit-measured (`advisor_verdict_unrecorded`), never blocked.
Advisor: agent acf34d7
1. Security: CLEAR — the M11 anti-enumeration invariant verified THREE ways: code read (the whole
   derivation path is a pure function of `email` + the frozen import-free module, zero IO), Q1/Q2 tests
   re-run green, and independent scratch probes against `classifyEmailDomain`. `email-domain-routing.ts`
   zero-diff by BOTH `git diff` and content. M14 holds — gateway diff has zero mentions of this task.
2. Concurrency: CLEAR — no new async/timing surface; seed effect and auto-seed handler are synchronous;
   `handleSso`'s preflight is byte-unchanged.
3. Architecture: **RESIDUE (non-blocking)** — `globalError` (the login-401 message) now renders ABOVE the
   reordered `[data-slot="login-entry-routes"]` region rather than adjacent to "Log in", because the
   password field moved into the region. In `corporate` order (SSO → SAML → password → create-workspace)
   a 401 lands at the TOP of the card while the password field and Log-in button — where a sighted
   user's attention already is after clicking submit — sit at the very bottom, separated by two
   unrelated affordances. `aria-live="polite"` means screen-reader users still get the announcement, so
   this is a SIGHTED-user visual regression specifically, not an AT failure. No test in either new file
   or the regression list asserts globalError's position, and the frozen UNCHANGED list protects
   `handleSubmit`'s BEHAVIOR (validation / POST / navigation), not its error's DOM placement — so this
   is not a contract violation. → §7 delta.
Verdict: PASS
Residue: two non-blocking items, neither security, neither tracing to a Must/Reject violation — the
globalError position above, and the Q3 coverage narrowing below.
Binding: advisory — no `sensitivity` override in state.json; the architecture residue is recommend-only.

### Q3 FALSIFIABILITY — recorded because the build agent flagged it and the verifier narrowed it further
Q3 ("reaches no lookup/probe/debounce timer") is a SOURCE-LEVEL structural check and is **narrower than
its own name implies**. It greps the whole file for `setTimeout`/`debounce`, checks the import site and
the render-body derivation, and brace-matches `handleSso`/`handleSamlSso`/`handleSubmit` to assert none
contains `classifyEmailDomain`/`normalizeEmailDomain`. It does NOT grep for
`fetch`/`XMLHttpRequest`/`sendBeacon` anywhere, and it does NOT check `handleEmailChange` — the actual
auto-seed handler — at all. A mutant adding a bare `fetch(...)` inside `handleEmailChange` would sail
through Q3 untouched. **That exact mutant IS caught by Q2's runtime instrumentation**
(`fetchSpy`/`xhrOpenSpy`/`sendBeacon` counters across character-by-character typing, re-run and confirmed
to exercise `handleEmailChange` on every keystroke). Net: Q3 alone is a weaker proof than its name
suggests and must not be relied on in isolation; Q2+Q3 together give real, non-circumventable coverage of
R2. Hardening Q3 is a §7 delta, not a gate blocker.

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: PASS
Reviewed by: Tin Dang · date: 2026-07-21

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of create-workspace clicks from `/login` (the
non-stranding route actually being used) · rate of SSO-preflight 4xx renders (should not rise —
M12) · share of `/login` visits reaching any submit vs. abandoning · `/signup?email=` arrivals
whose email field is edited before submit (a proxy for a bad auto-seed).

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang, 2026-07-21.)
- [AI] build — strategy used: as planned
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).
- [SPEC · seeded] **TIN-CONFIRMED AT FREEZE 2026-07-21 (option c).** The literal two-stage collapse (one field + Continue, then reveal the affordances that fit) is deferred, not dropped — it requires re-specifying `login.test.tsx`, `sso-login.test.tsx`, `saml-login-affordance.test.tsx` and `login-domain-query-seed.test.tsx` as an explicit change request (evidence: §0 R-b, four shipped suites reach those controls immediately after render). Promote to a task at milestone close via `add.py new-task --from-delta`; it is the thing that makes the surface match this task's own title, so do not let it silently age out.
- [SPEC · open] A true single front door (`/start`) with the marketing CTAs repointed remains possible as a follow-on; it needs `landing-page.test.tsx`, `landing-fidelity.test.tsx`, `homepage-cta-intent-split.test.tsx` and `marketing-shell.test.tsx` re-specified (evidence: §0 R-a).
- [SPEC · open] `resolveSsoDomain` / `validateSsoDomain` remain live alongside `normalizeEmailDomain` / `isWellShapedDomain` — two near-duplicate pairs. Retiring the LoginForm pair is a standalone cleanup with real behavior risk (evidence: §0 R-f, both are exported and directly unit-tested).

- [SPEC · open] **`globalError` position regression — the highest-value follow-on here.** The login-401
  message now renders above the reordered entry region instead of adjacent to "Log in"; in `corporate`
  order the error lands at the top of the card while the password field and submit button sit at the
  bottom, two unrelated affordances away. AT users are unaffected (`aria-live="polite"` still announces);
  this is a sighted-user visual regression. Fix: render globalError adjacent to (or duplicated near) the
  password/Log-in subtree, or float it as a toast. NOTE the reason nothing caught it: no test — frozen or
  new — asserts globalError's POSITION, only its presence and text. Add a positional assertion with the
  fix. (evidence: verifier acf34d7 §3 friction point c.2)
- [SPEC · open] **Harden Q3 to match its own name.** It does not grep for
  `fetch(`/`XMLHttpRequest`/`sendBeacon`, and it never checks `handleEmailChange` — the actual auto-seed
  handler. A bare `fetch(...)` added inside `handleEmailChange` passes Q3 untouched (Q2's runtime
  instrumentation does catch it, so R2 is genuinely covered TODAY — but by Q2, not by the test named for
  it). Extend Q3 to grep the network primitives and to extract+check `handleEmailChange`'s own source.
  (evidence: verifier acf34d7 §4)
- [SPEC · open] Give `signup-email-seed.test.tsx`'s `test_absent_email_param_is_a_no_op…` a genuinely RED
  form, or annotate it in-file as a deliberate regression guard that cannot run red. Today it passed
  pre-build because an empty field is the pre-existing default. (evidence: verifier acf34d7 §2)
- [SPEC · open] 💭 UX nit, low priority: clearing the SSO field to empty counts as *touched*, so
  auto-fill stops permanently. Contract-correct, but a visitor who clears the field intending "let
  auto-fill take over again" does not get that. Treating empty-after-clear as pristine is a one-line
  change if it ever surfaces in real use. (evidence: verifier acf34d7 §5, exercised directly)

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
- [UDD · folded] The shipped test suite is a DESIGN CONSTRAINT, not just a safety net — reading four auth test files decided the shape of this task (emphasis-not-disclosure, /login-not-/start) before any code existed, and both alternatives would have looked reasonable on a whiteboard (evidence: §0 R-a and R-b, each grounded in a cited line of a green test). [folded foundation-version 55]
- [UDD · folded] A frozen a11y placement can flip from liability to asset once an upstream surface seeds the input — domain-aware's panel-above-the-field is a problem when the visitor types below it, and exactly right when the email arrives pre-classified from another door (evidence: the `?email=` seed, M13). [folded foundation-version 55]
