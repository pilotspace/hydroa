# TASK: Auto-assign MEMBER into the verified-domain tenant on SSO/first login + thread joined_existing_tenant through the BFF

slug: domain-auto-assign-login · created: 2026-07-18 · stage: production
milestone: enterprise-domain-onboarding
component: gateway, dashboard
autonomy: conservative
sensitivity: security
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `tenants/infrastructure/repository.py:SqlAlchemyIdentityRepository._get_or_provision_sso_user` — the shared JIT-provision helper both OIDC+SAML delegate to. **ALREADY auto-joins MEMBER on SSO first-login**: SELECT by email → if absent, `assert_seat_available` + INSERT `UserRow(role=MEMBER, tenant_id=<caller's>)` + `SeatMembershipEventRow(event_type="joined")`. Keyed on whatever tenant_id the caller passes; does NOT itself consult claims.
- `auth/application/use_cases.py:OidcLoginUseCase.execute` (Step 6-7) — resolves mapped_tenant_id (claim-first, then per-tenant-config/env fallback — task-1 CR-v2) → `get_or_provision_oidc_user`. Returns `(jwt, expires_in)` — NO new-vs-existing signal.
- `auth/application/saml_use_cases.py:SamlAcsUseCase.execute` — same shape via `get_or_provision_saml_user`; tenant pinned at `/login` via `resolve_verified_tenant_for_raw_domain`. Returns `(jwt, expires_in, redirect_path)` — same gap.
- `auth/api/oidc_router.py:oidc_callback` / `auth/api/saml_router.py:saml_acs` — return a bare `RedirectResponse(302)` + session cookie. **NO JSON body on the SSO completion path.**
- `core/config.py:Settings.oidc_post_login_redirect`/`saml_post_login_redirect` (default `"/"`) — the gateway fully controls the redirect URL → can append a query param (the precedented signal channel).
- `domain_capture/application/join_tenant_use_case.py:JoinTenantByDomainUseCase` + `repository.py:join_verified_tenant_domain` — the password-signup auto-join reference pattern (INSERT-only, MEMBER, verified-claim-gated).
- `tenants/api/router.py:signup` + `tenants/api/schemas.py:SignupResponse.joined_existing_tenant: bool` — password-signup emits the join signal.
- `apps/dashboard/app/api/auth/signup/route.ts` — parses the gateway body ONLY on error; on success reads only the login token → **`joined_existing_tenant` silently dropped**.
- `apps/dashboard/app/auth/oidc/callback/route.ts` — OIDC relay; forwards 3xx Location + Set-Cookie verbatim, already rewrites 4xx → `/login?sso_error=<code>` (**precedent for a query-param signal**; a success-path param on the gateway redirect forwards for free).
- `apps/dashboard/components/auth/SignupForm.tsx` — always sends tenant_name; on 201 always `router.push("/app/keys")`, no differentiated messaging.
- `domain_capture/application/verified_domain_resolution.py:resolve_verified_tenant_for_raw_domain` — task-1's shared predicate (the ONLY legit "does a verified claim own this domain" check).

Context (working folder): NO `apps/dashboard/app/auth/saml/` relay route exists — SAML has ZERO dashboard wiring (no login button, no callback relay); OIDC is the only live dashboard SSO surface. Frozen tests: `apps/dashboard/tests/sso-login.test.tsx` (OIDC login-init), `apps/dashboard/tests-bff/oidc-callback-relay.test.tsx` (relay 3xx/4xx/5xx rules), gateway `plan_seat_cap/`, `seat_billing/test_seat_membership_events.py`, `superadmin_audit_foundation/test_part_c_oidc_login_audit.py` (exercise `_get_or_provision_sso_user` — frozen return-tuple shapes). `.add/GLOSSARY.md` L58: `joined_existing_tenant` defined for password-signup only; `domain auto-join` term defined but not yet anchored to SSO code.

Honors: task-1's shared predicate is the ONLY way to check verified-claim ownership (never re-derive); milestone invariant auto-assign = MEMBER-only + verified-domain-only (never owner/admin, never zero-touch); SSO NEW users always role=MEMBER, an EXISTING user's role never downgraded on re-login; BFF relay never puts a token in a body / only forwards documented params / fails closed with a sanitized code; CLAUDE.md design-for-failure on any new gateway→BFF IO. SCIM-deactivated users are already rejected at SSO login (`OidcAccountDeactivatedError`) — do not disturb.

Anchors the contract cites: `_get_or_provision_sso_user` · `OidcLoginUseCase.execute` · `SamlAcsUseCase.execute` · `oidc_callback`/`saml_acs` · `SignupResponse.joined_existing_tenant` · `apps/dashboard/app/api/auth/signup/route.ts` · `apps/dashboard/app/auth/oidc/callback/route.ts` · `Settings.oidc_post_login_redirect` · `resolve_verified_tenant_for_raw_domain`.

Issues/Risks (→ feed §1):
1. **Auto-join MEMBER on SSO login ALREADY EXISTS & fires** (`_get_or_provision_sso_user`) — NOT net-new backend. Net-new = (a) maybe re-gate to claims, (b) the join SIGNAL, (c) new-vs-existing bit.
2. **Invariant tension (needs a Tin call).** Task-1's CR-v2 KEPT a non-claim fallback (per-tenant config + operator env-mapping) for SSO login-init. `_get_or_provision_sso_user` auto-joins into whatever tenant_id it's handed with NO claim re-check → a login resolved via the ENV-mapping fallback still auto-joins a MEMBER on a domain no tenant DNS-verified. Config-fallback is claim-backed (task-1 write-gate+backfill), so only the ENV path is unverified — the SAME env-global exception Tin already accepted as a task-1 spec-delta. Resolvable by consistency (env = trusted-operator), but it's an interpretation of "verified-domain-only" across two Tin-approved artifacts.
3. **No signal vehicle on the SSO redirect-only path** — callback returns bare 302+cookie, no JSON, no BFF business-logic hop. Only channel: a query param on the gateway-authored redirect Location (mirrors shipped `?sso_error=`), or a 2nd short-lived readable cookie.
4. **SAML has ZERO dashboard wiring** — threading it through the BFF means building a relay route from scratch (scope call: OIDC-only vs OIDC+SAML).
5. **No new-vs-existing-user bit** in the provision return path — additive change with call-site fan-out (OIDC use case, SAML use case, + frozen tests asserting the 1/2/3-tuple return shapes).
6. **`SignupForm` "drop tenant_name when joining"** can't mean omit-from-request (single round-trip, client can't know the outcome first) — it's a UI-treatment decision (post-response messaging).
7. **Ownership boundary vs task-3** — the polished "you joined {tenant}" confirmation UI is `domain-claims-console`'s UDD deliverable; task-2 delivers the end-to-end DATA/signal.

Related intent: MILESTONE.md (identity/onboarding half; Tin trust = admin-pre-verifies-DNS-TXT, never zero-touch; `domain-auto-assign-login` owns the `joined_existing_tenant` end-to-end shape, consumed by dashboard + task-3's confirmation UI). Task-1 §7 spec-delta (env-GLOBAL OIDC bypass, Tin-accepted) is the direct background for Risk #2. GLOSSARY `domain auto-join`.

Ground SHA: `4fd7ff5`   (grounded via serena; subagent a0c2f409c2e824383 — every symbol opened)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Surface the "you joined an existing workspace" outcome end-to-end across password signup AND OIDC/SAML SSO login, plus net-new SAML dashboard login+callback wiring — WITHOUT re-gating the (accepted-as-is) SSO auto-join.
Framings weighed: **additive join-SIGNAL (query-param on the SSO redirect + un-drop the password BFF field) + a new-vs-existing bit out of provisioning + net-new SAML dashboard relay** (chosen) · re-gate provisioning to claims-only (rejected — Tin accepted the operator-env exception 2026-07-18) · cookie-based signal (rejected — a query-param has the shipped `?sso_error=` precedent, no new cookie).
Must:
<must>
  - M1 — the SSO provisioning path signals whether THIS login just created the user (joined) vs matched an existing one (`_get_or_provision_sso_user` + its OIDC/SAML wrappers expose a `newly_provisioned` bit). Existing role/tenant/seat behavior is byte-unchanged; a returning user's stored role is never touched.
  - M2 — OIDC callback appends `?joined=1` to its post-login redirect Location IFF the user was newly provisioned (SSO never creates a tenant, so `joined` always means "joined an existing tenant"). The bare-302 + cookie shape is otherwise unchanged.
  - M3 — SAML ACS appends the same `?joined=1` to its post-login redirect on newly-provisioned.
  - M4 — the password-signup BFF (`app/api/auth/signup/route.ts`) reads `joined_existing_tenant` from the gateway SUCCESS body (currently only parsed on error) and forwards it to the client.
  - M5 — `SignupForm.tsx` renders a differentiated success outcome when joined (a "you joined {tenant}" message/route) vs the workspace-created path — minimal treatment; the polished confirmation surface is `domain-claims-console` (task-3).
  - M6 — NET-NEW SAML dashboard wiring: a SAML login affordance + a dashboard SAML callback relay route that forwards the gateway redirect + Set-Cookie verbatim and surfaces `?joined=1`/`?sso_error=`, mirroring the OIDC relay conventions (no token in a body, sanitized error code, timeout-bound fetch, fail-closed).
  - M7 — the OIDC callback relay (`app/auth/oidc/callback/route.ts`) carries the `?joined=1` success param through to the landing (it forwards Location verbatim today; a test asserts it survives).
</must>
Reject:
<reject>
  - R1 — a returning (already-existing) SSO user's login does NOT get `?joined=1` (signal only on first provision) -> param absent.
  - R2 — the SSO callback never leaks a token in a body, and never emits `?joined` on an auth-failure path -> a 4xx is `?sso_error=<sanitized>`, never `?joined`.
  - R3 — an SSO login can never create a brand-new TENANT (SSO only joins/logs-in) -> `?joined` only ever means joined-an-existing-tenant; unchanged.
</reject>
After:
<after>
  - A user who joins an existing workspace via password signup OR OIDC OR SAML SSO sees that outcome in the dashboard; SAML has first-class dashboard login+callback wiring; the SSO auto-join gate itself is unchanged (operator-env exception accepted, task-1 consistency).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] (M2/M7 redirect signal transport) that a query param appended to the gateway's post-login redirect Location survives the OIDC relay intact — lowest confidence because a relay that RECONSTRUCTS (vs forwards) the URL would drop it; if wrong: the OIDC join signal is silently lost. Mitigation: M7 pins it with a test; the frozen `oidc-callback-relay.test.tsx` shows Location is forwarded verbatim today.
  - [ ] that `newly_provisioned` threads through the frozen provisioning return-shapes without breaking frozen seat/audit tests — the arity change is additive but `plan_seat_cap`/`seat_billing`/`superadmin_audit_foundation` assert current shapes → SANCTIONED-EDIT reconciliation (task-1 pattern) OR carry the bit as a transient User attribute (no arity change). Build picks; confirm no assertion weakened.
  - [ ] that the SAML ACS response is a redirect the dashboard can relay the same way as OIDC — confirm the ACS returns a 302 the new relay can forward (vs an HTML-POST-binding form that needs different handling).
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: first OIDC login into a verified-domain tenant signals joined   # M1,M2
  Given tenant A holds a verified claim for acme.com and a user owner-less acme.com email logs in via OIDC for the first time
  When the OIDC callback completes and provisions a new MEMBER of A
  Then the post-login redirect Location carries ?joined=1
  And the user's role is MEMBER and no new tenant was created

Scenario: returning OIDC user is not signalled as joined   # R1
  Given the same acme.com user already exists in tenant A
  When they log in via OIDC again
  Then the redirect Location has NO ?joined param
  And their stored role is unchanged

Scenario: first SAML login signals joined   # M3
  Given tenant A (verified acme.com + SAML config) and a first-time acme.com user via SAML
  When the ACS provisions a new MEMBER
  Then the ACS post-login redirect carries ?joined=1

Scenario: password signup that joins an existing tenant surfaces to the client   # M4,M5
  Given tenant A holds a verified claim for acme.com
  When a new acme.com user signs up via password and the gateway returns joined_existing_tenant=true
  Then the signup BFF forwards that outcome (no longer dropped) and SignupForm shows a "joined {tenant}" outcome
  And a non-joining signup still shows the workspace-created outcome

Scenario: SAML has a dashboard login + callback relay   # M6
  Given a user on the dashboard login page choosing SAML SSO
  When they complete SAML and the gateway ACS redirects
  Then the new dashboard SAML callback relay forwards the redirect + Set-Cookie verbatim and surfaces ?joined=1 / ?sso_error
  And no token appears in any response body

Scenario: OIDC relay carries the joined param through   # M7
  Given the gateway OIDC callback redirects with ?joined=1
  When the dashboard OIDC callback relay forwards it
  Then the landing receives ?joined=1 intact

Scenario: an auth-failure never emits joined   # R2
  Given an OIDC/SAML login that fails validation
  When the callback returns a 4xx
  Then the relay bounces to /login?sso_error=<sanitized> and never ?joined, and no token is leaked
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# ── SSO provisioning signal (gateway internal) ──
_get_or_provision_sso_user(...) -> (User, newly_provisioned: bool)   # + get_or_provision_oidc_user / _saml_user wrappers
  # newly_provisioned=True IFF this call INSERTed the user; existing-user lookup → False. Role/tenant/seat behavior unchanged.
  # (Build MAY instead carry the bit as a transient User attribute to avoid arity churn — observable contract is the ?joined param, not the tuple.)

# ── OIDC login completion (gateway) ──
GET /auth/oidc/callback ...
  302 -> Location: <oidc_post_login_redirect>[?joined=1]   # ?joined=1 appended IFF newly_provisioned; + session cookie (unchanged)
  4xx -> (relay maps to) /login?sso_error=<sanitized>      # never ?joined (R2)

# ── SAML login completion (gateway) ──
POST /auth/saml/acs ...
  302 -> Location: <saml_post_login_redirect>[?joined=1]   # symmetric (M3)

# ── Password signup BFF (dashboard) ──
POST /api/auth/signup  (BFF)
  -> reads gateway SUCCESS body { ..., joined_existing_tenant: bool } and forwards it to the client (M4; was dropped)

# ── NET-NEW SAML dashboard wiring ──
GET|POST /auth/saml/callback  (dashboard relay, NEW)   # forwards gateway redirect Location + every Set-Cookie verbatim;
  surfaces ?joined=1 / ?sso_error=<sanitized>; no token in a body; timeout-bound; fail-closed (mirror app/auth/oidc/callback/route.ts)
  + a SAML login affordance on the dashboard login surface (M6)

# ── OIDC dashboard relay (existing) ──
app/auth/oidc/callback/route.ts   # forwards Location verbatim → ?joined=1 survives; assert it (M7)

Schema: NO DB schema change. Reads/writes are the existing UserRow / SeatMembershipEventRow provisioning (unchanged); the signal is transport-only (query param + BFF body field).
```

Glossary deltas: domain auto-join (SSO): a MEMBER auto-provisioned into the verified/operator-mapped tenant on SSO first-login; the `?joined=1` redirect param is its client signal (mirrors password-signup `joined_existing_tenant`).
Least-sure flag surfaced at freeze: [contract] (M2/M7 redirect signal transport) — that a `?joined=1` query param appended to the gateway's post-login redirect Location survives the OIDC dashboard relay intact. Lowest confidence because a relay that RECONSTRUCTS (rather than forwards) the URL would silently drop it → the OIDC join signal is lost. Mitigation: M7 pins it with a dedicated test; the frozen `oidc-callback-relay.test.tsx` shows Location is forwarded verbatim today, so the risk is regression, not first-build.
Status: FROZEN @ v1 — approved by Tin Dang 2026-07-19
Reported: yes — freeze report (banner/ARC/SHAPE/FLAGS) rendered before this froze

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% on the new signal/relay paths.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
GATEWAY (pytest — apps/gateway/tests/domain_auto_assign_login/):
  - test_oidc_first_login_redirect_has_joined_param: new acme.com OIDC user / callback / Location contains joined=1 + role MEMBER + no new tenant · M1,M2
  - test_oidc_returning_user_no_joined_param: existing acme.com user / OIDC login / Location has NO joined param + role unchanged · R1
  - test_saml_first_login_redirect_has_joined_param: new SAML user / ACS / redirect has joined=1 · M3
  - test_sso_auth_failure_no_joined_param: invalid OIDC/SAML callback / 4xx (no joined) · R2
  - test_newly_provisioned_signal (unit): _get_or_provision_sso_user returns newly_provisioned True on insert, False on existing · M1
  - regression: existing plan_seat_cap/seat_billing/superadmin_audit provisioning tests stay green (SANCTIONED-EDIT only if arity threading chosen) · M1
DASHBOARD (vitest — apps/dashboard/tests-bff/ + tests/):
  - signup-bff-forwards-joined.test: gateway body joined_existing_tenant=true / signup BFF / client receives the flag (not dropped) · M4
  - signup-form-joined-outcome.test.tsx: joined vs created → differentiated messaging · M5
  - saml-callback-relay.test.tsx (NEW): gateway 3xx w/ joined=1 → forwarded Location+cookies verbatim; 4xx → /login?sso_error; no token in body · M6
  - saml-login-affordance.test.tsx: dashboard login surface offers SAML SSO · M6
  - oidc-callback-relay joined passthrough: extend the frozen relay test to assert ?joined=1 survives the forward · M7
</test_plan>

Tests live in: `apps/gateway/tests/domain_auto_assign_login/` (gateway) · `apps/dashboard/tests-bff/` + `apps/dashboard/tests/` (dashboard) · MUST run red before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/auth/api/oidc_router.py` · `apps/gateway/src/gateway/auth/api/saml_router.py` · `apps/gateway/src/gateway/auth/application/use_cases.py` · `apps/gateway/src/gateway/auth/application/saml_use_cases.py` · `apps/gateway/src/gateway/tenants/infrastructure/repository.py` · `apps/gateway/src/gateway/tenants/domain/ports.py` · `apps/gateway/tests/domain_auto_assign_login/` · `apps/dashboard/app/api/auth/signup/route.ts` · `apps/dashboard/app/auth/saml/` · `apps/dashboard/app/auth/oidc/callback/route.ts` · `apps/dashboard/components/auth/` · `apps/dashboard/tests-bff/` · `apps/dashboard/tests/`
(Prose note — the `_get_or_provision_sso_user` seam + both wrappers live in repository.py; the NEW SAML dashboard relay = `app/auth/saml/callback/route.ts` + login-init `app/auth/saml/login/route.ts`, mirroring `app/auth/oidc/`, NOT under `app/api/`; components/auth covers SignupForm + LoginForm.)
Strategy (ordered batches):
  1. GATEWAY M1 — thread a `newly_provisioned: bool` out of `_get_or_provision_sso_user` (return-tuple or a small result object); pure-refactor its existing callers first so their frozen tests stay green, THEN read the bit. Design-for-failure: the bit defaults False on any ambiguity (existing-user path, lookup race) → fail-safe toward "returning user", never a false "joined".
  2. GATEWAY M2/M3 — at OIDC callback + SAML ACS, append `?joined=1` to the success redirect Location ONLY when newly_provisioned AND auth succeeded. Never on the error/failure branch (R2). Reuse the existing `?sso_error=` query-param precedent — same transport, no new cookie.
  3. DASHBOARD M6 (NET-NEW, the widest gap) — add a SAML callback relay route mirroring the OIDC relay: forward gateway Location + Set-Cookie verbatim on 3xx; on 4xx redirect `/login?sso_error=`; NEVER place a token in the response body. Add the SAML SSO affordance on the login surface.
  4. DASHBOARD M4/M7 — signup BFF forwards `joined_existing_tenant` (stop dropping it); OIDC relay passes `?joined=1` through.
  5. DASHBOARD M5 — SignupForm differentiates "joined {tenant}" vs "created" outcome (plain copy; the polished chip belongs to task-3 domain-claims-console).

Persona (required): appsec-engineer
Spawn isolation (default): isolation: "worktree" for any build/verify subagent spawn (two roots mutated in parallel).
Known-problem fixes:
  - Arity change to `_get_or_provision_sso_user` silently breaks every caller's frozen provisioning test → refactor all callers in the same batch; if any frozen assertion would change, STOP and surface as a change-request (never rewrite a frozen test — the task-1 CR lesson).
  - A relay that copies the response BODY leaks a session token to JS → forward only Location + Set-Cookie headers, mirror the audited OIDC relay exactly.
  - `?joined=1` on a returning-user or auth-failure redirect = false signal (R1/R2) → gate strictly on newly_provisioned AND success.
Strategy actually used: decided tuple-return everywhere (`_get_or_provision_sso_user` + both wrappers + ports Protocol + `SamlAcsUseCase.execute` 4-tuple) EXCEPT `OidcLoginUseCase.execute` — frozen `superadmin_audit_foundation/test_part_c_oidc_login_audit.py` unpacks it as a 2-tuple at 4 sites, so the bit rides the §3-sanctioned transient attribute `self.newly_provisioned` (class default False), read by `oidc_callback`. RATIFIED by orchestrator: the use case is constructed fresh per request in `get_oidc_use_case_with_config` (per-request session/repo, no cache) → instance-scoped, no cross-request race; set exactly once in execute() before the single read. Zero tests edited. `?joined=1` appended query-safely only on the success redirect, never on an error branch (those raise before the redirect).
Safety rule (feature-specific): the join signal is advisory UI-only — it MUST NOT influence which tenant a user lands in; routing/precedence is task-1's frozen contract and stays untouched.
Code lives in: `/apps/gateway/src/gateway/auth/` + `/apps/dashboard/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — gateway 189 passed (9 suites, exit 0, lens-1); dashboard 35 passed (9 files, exit 0, lens-2)
- [x] coverage did not decrease — every touched path has a dedicated new test (both lenses confirmed); the 46% line on a subset run is `--cov-fail-under` global noise, exit 0
- [x] no test or contract was altered during build — 0 files under tests/ or tests-bff/ changed; §3 FROZEN @ v1 untouched (lens-2 git-status audit)
- [x] the green was EARNED, not gamed — dual independent opus refute-read, both EARNED; every new assert fails if the feature is removed (no overfit/vacuous/stub)
- [x] concurrency / timing of the risky operation is safe — `OidcLoginUseCase` constructed fresh per request (deps.py:129, no cache); `self.newly_provisioned` set-once-before-read, class default False; both lenses CLEAR
- [x] no exposed secrets, injection openings, or unexpected dependencies — SAML relays empty-body + Set-Cookie only, sanitized `?sso_error` (`^[A-Za-z0-9_]+$` ≤64), `?joined` from operator config not user input; no new deps
- [x] layering & dependencies follow CONVENTIONS.md — Protocol↔impl↔wrappers↔use-case↔router tuple shapes consistent; additive only
- [ ] a person reviewed and approved the change — **← Tin, this gate (conservative + security = human floor)**

### Component green bars (both must be green — cross-component task)
- [x] GATEWAY — `pytest` (Makefile:test / ci.yml 'Tests' step) — 189 passed across domain_auto_assign_login + domain_routing_unification + plan_seat_cap + seat_billing + superadmin_audit_foundation + saml_sso + oidc_tenant_config + scim_provisioning (lens-1, real PG:5433+Redis, exit 0)
- [x] DASHBOARD — `vitest` (ci.yml dashboard job) — 35 passed incl. new saml-callback-relay/signup-joined-forward/saml-login-affordance/signup-form-joined + frozen oidc-callback-relay/sso-login/signup/signup-account-type (lens-2, exit 0)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] A brand-new SSO user (OIDC or SAML) whose email domain has a verified claim lands on a redirect whose Location carries `?joined=1`, and is a MEMBER of that tenant — confirmed: gateway tests assert Location `?joined=1` + role=member + tenant-count unchanged
- [x] A returning SSO user's redirect carries NO `?joined=1`; an auth-failure redirect carries NO `?joined=1` — confirmed: R1 (2nd login no param, role unchanged) + R2 (4xx, no location, no "joined"/token in body) both green
- [x] The dashboard SAML callback relay forwards Location + Set-Cookie verbatim on 3xx and never emits a token in the body — confirmed: relay test asserts exact Location, Set-Cookie present, empty body, no FAKE_JWT in any body
- [x] Password signup that joins an existing verified-domain tenant shows a "joined {tenant}" outcome, not the generic "created" — confirmed: BFF forwards true AND false; SignupForm renders /joined/i on join, preserves frozen created-path push

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced: `newly_provisioned` threads repository→both wrappers→SAML 4-tuple / OIDC transient attr→routers→`?joined=1`; new SAML callback+login routes reachable; BFF field emitted+consumed by SignupForm (lens-2 confirmed)
- [x] DEAD-CODE (code) — no orphaned symbol; SAML_LOGIN_PATH used, affordance wired to handleSamlSso (lens-2)
- [x] SEMANTIC (prose / non-code) — SAML relay read line-for-line vs audited OIDC relay: empty-body fail-closed, redirect:"manual", AbortSignal.timeout(5000), sanitized error code (lens-1)

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
- [x] every symbol §3 CONTRACT cites still resolves — confirmed via serena + the 189-pass gateway run: `_get_or_provision_sso_user`, both wrappers, `OidcLoginUseCase.execute`, `SamlAcsUseCase.execute`, `oidc_callback`/`saml_acs`, `SignupResponse.joined_existing_tenant`, `resolve_verified_tenant_for_raw_domain` all present
- [x] any anchor that moved/renamed since Ground SHA — none moved; build was additive on the anchored seams (Ground SHA 4fd7ff5 still the base)

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: agent a3e8a933fb60a64b0 (lens-1) + agent a4d1b3613815da0b8 (lens-2) — two independent opus refute-reads · adversarially checked: overfit/vacuous/stubbed asserts, insert-vs-existing distinction, no-token-in-body both directions, R1/R2/R3, affordance-collision both directions, per-request instance race

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
Advisor: dual independent — a3e8a933fb60a64b0 (routing/trust) + a4d1b3613815da0b8 (earned-green/tamper)
1. Security: CLEAR — role=MEMBER-only, no token leak, env-mapping not widened, signal is UI-advisory (cannot influence tenant routing); 8/8 attacks CONFIRMED-SAFE
2. Concurrency: CLEAR — per-request use-case instance, set-once-before-read, no cross-request race
3. Architecture: CLEAR — additive, type-consistent Protocol↔impl↔router; SAML relay mirrors audited OIDC relay
Verdict: PASS
Residue: none blocking. One §7 open spec-delta (both lenses, non-blocking): monitor `?joined` firing on an operator-ENV-mapped (non-claim) tenant — the Tin-accepted task-1 env-global exception.
Binding: advisory — security (a human floor gate holds regardless; recorded, not engine-relaxed)

### GATE RECORD
Reported: yes — dual-verify gate report (banner/ARC/SHAPE/EVIDENCE) rendered before this outcome
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-19

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose **additive join-SIGNAL (query-param on the SSO redirect + un-drop the password BFF field) + a new-vs-existing bit out of provisioning + net-new SAML dashboard relay**; rejected re-gate provisioning to claims-only (rejected — Tin accepted the operator-env exception 2026-07-18) · cookie-based signal (rejected — a query-param has the shipped `?sso_error=` precedent, no new cookie).
- [human] freeze — froze §3 @ v1 (approved by Tin Dang 2026-07-19)
- [AI] build — strategy used: decided tuple-return everywhere (`_get_or_provision_sso_user` + both wrappers + ports Protocol + `SamlAcsUseCase.execute` 4-tuple) EXCEPT `OidcLoginUseCase.execute` — frozen `superadmin_audit_foundation/test_part_c_oidc_login_audit.py` unpacks it as a 2-tuple at 4 sites, so the bit rides the §3-sanctioned transient attribute `self.newly_provisioned` (class default False), read by `oidc_callback`. RATIFIED by orchestrator: the use case is constructed fresh per request in `get_oidc_use_case_with_config` (per-request session/repo, no cache) → instance-scoped, no cross-request race; set exactly once in execute() before the single read. Zero tests edited. `?joined=1` appended query-safely only on the success redirect, never on an error branch (those raise before the redirect).
- [human] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.

