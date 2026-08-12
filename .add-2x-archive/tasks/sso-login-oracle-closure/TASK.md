# TASK: Collapse the OIDC login-init 403/404 split so a domain probe cannot reveal a verified customer domain (SECURITY)

slug: sso-login-oracle-closure · created: 2026-07-21 · stage: production
milestone: frontdoor-persona-routing
component: gateway
autonomy: conservative
sensitivity: security
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/auth/api/oidc_router.py:oidc_login` — `GET /auth/oidc/login`, public (no auth deps), `(request: Request, session: AsyncSession) -> Response`. THE one file this task changes. Reads `?domain=`; resolves claim-first then falls back; terminates in one of three ways. Its module docstring (`oidc_router` module header) and the `oidc_login` docstring both describe the routing contract.
- `apps/gateway/src/gateway/auth/api/oidc_router.py:oidc_callback` — `GET /auth/oidc/callback`. READ-ONLY here. It raises `OIDC_DOMAIN_NOT_MAPPED.exc()` (post-authentication, verified-email-domain unmapped) and `OIDC_TENANT_NOT_CONFIGURED.exc()`. **Out of scope — not an enumeration surface** (reaching it requires an IdP-signed ID token).
- `apps/gateway/src/gateway/core/error_catalog.py:OIDC_NOT_CONFIGURED` — `ErrorSpec(404, "ERR_OIDC_NOT_CONFIGURED", "OIDC login is not configured on this platform")`. The collapse target. READ-ONLY (reused as-is, not edited).
- `apps/gateway/src/gateway/core/error_catalog.py:OIDC_DOMAIN_NOT_MAPPED` — `ErrorSpec(403, "ERR_OIDC_DOMAIN_NOT_MAPPED", "Email domain not permitted for SSO login")`. Stays in the catalog and stays imported by `oidc_router` — still used by `oidc_callback`. Only its *login-path* raise site is removed.
- `apps/gateway/src/gateway/core/error_catalog.py:OIDC_TENANT_NOT_CONFIGURED` — `ErrorSpec(404, "ERR_OIDC_NOT_CONFIGURED", "OIDC configuration not found or disabled for this tenant")`. Same `code`, DIFFERENT `title_template`. **Deliberately NOT used by the collapse** — a distinct title on the same code would re-open the oracle one layer down (see Issues/Risks).
- `apps/gateway/src/gateway/core/error_catalog.py:ErrorSpec.exc` — `(detail=None, headers=None, extra=None, **fmt) -> ProblemError`. Body is byte-identical by construction for the same spec when `detail`/`headers`/`extra` are all None.
- `apps/gateway/src/gateway/domain_capture/application/verified_domain_resolution.py:resolve_verified_tenant_for_raw_domain` — `(claim_repo, domain) -> uuid.UUID | None`. The single canonical email-domain→tenant router (domain-routing-unification §3 M1/M8). Unchanged.
- `apps/gateway/src/gateway/auth/api/deps.py:get_oidc_config_resolver` — returns the production `DbOidcConfigResolver` when the seam is None. Its `resolve_by_tenant_id(tenant_id)` / `resolve(domain)` are the two resolution legs. Unchanged.
- `apps/gateway/src/gateway/auth/infrastructure/settings_oidc_config_resolver.py:ENV_CONFIG_COOKIE_VALUE` — `"env-config"`, the tenant-cookie value on the env-Settings fallback leg. Unchanged.
- `apps/gateway/src/gateway/core/config.py:Settings.oidc_enabled` — `bool = False` (`GATEWAY_OIDC_ENABLED`). The deployment flag that makes this oracle deployment-dependent.
- `apps/gateway/src/gateway/auth/api/saml_router.py:saml_login` — the sibling path, READ-ONLY. Already fully collapsed: ONE terminal `SAML_NOT_CONFIGURED.exc()` (404) covers no-claim, claimed-but-unconfigured, AND no-domain. It has no env fallback. **This is the shape OIDC is being brought into line with.**
- `apps/gateway/tests/domain_routing_unification/conftest.py:assert_problem` — `(resp, status, code) -> dict`. Asserts `status_code` and `body["code"]` ONLY; it does NOT assert `title`. Relevant: a title-level oracle would pass every existing assertion silently.

Context (working folder):
- `.add/tasks/domain-routing-unification/TASK.md` §3 — FROZEN @ v2/CR-v2. The contract this task change-requests. NOT edited.
- `.add/tasks/domain-aware-auth-routing/TASK.md` §3 M12 — "SERVER SURFACE — INTENTIONALLY EMPTY". The sibling boundary this task must not cross.
- `apps/dashboard/app/api/auth/oidc/login/route.ts` — the pre-auth BFF relay. Forwards ANY 4xx status+body verbatim (`upstream.status >= 400 && < 500`); only its prose comments name `404 ERR_OIDC_NOT_CONFIGURED` as the example. **Requires NO code change** — the relay is already status-agnostic within 4xx. Confirms the change stays server-side-only.
- `apps/gateway/tests/domain_routing_unification/test_domain_routing_unification.py` — docstrings at the two `assert_problem(..., 404, "ERR_OIDC_NOT_CONFIGURED")` sites narrate "the two contracted fail-closed codes (403 … / 404 …)". That prose goes stale on this change. It is a FROZEN test file — prose is NOT edited (editing a frozen test, even a comment, trips `build_tampered`; see [[add-tamper-tripwire-ordering]]). Recorded as a §7 spec delta instead.
- No config, manifest, migration, fixture, or dependency change. No new package.

Honors (patterns / conventions):
- `CONVENTIONS.md` — every `raise` goes through a `gateway/core/error_catalog.py` `ErrorSpec`; no site constructs `ProblemError` with raw literals. This task raises an EXISTING spec; it adds no new catalog entry.
- domain-routing-unification §3 M1/M8 — `resolve_verified_tenant` is the sole email-domain→tenant router; a claimed domain is NEVER routed by an `email_domains` containment match. This task must preserve that even while making the claimed-but-unconfigured leg fall through (see Issues/Risks #2).
- ADD: never weaken a frozen test; retarget an expected code, never delete or loosen an assertion.

Seams consulted: none applicable.

Anchors the contract cites:
- `apps/gateway/src/gateway/auth/api/oidc_router.py:oidc_login`
- `apps/gateway/src/gateway/core/error_catalog.py:OIDC_NOT_CONFIGURED`
- `apps/gateway/src/gateway/core/error_catalog.py:OIDC_DOMAIN_NOT_MAPPED`
- `apps/gateway/src/gateway/auth/api/saml_router.py:saml_login`
- `apps/gateway/src/gateway/core/config.py:Settings.oidc_enabled`

Issues/Risks (→ feed §1):

1. **The oracle, confirmed against the live file.** `oidc_login` has three terminal outcomes for `?domain=X`:
   - verified claim + enabled config → 302 to IdP
   - verified claim + NO enabled config → `raise OIDC_DOMAIN_NOT_MAPPED.exc()` → **403** (`oidc_login`, the `if oidc_config is None:` guard inside the `mapped_tenant_id is not None` branch, l.170-173 as of Ground SHA)
   - no verified claim + legacy `resolver.resolve(domain)` hit → 302
   - no verified claim + `settings.oidc_enabled` TRUE → 302 via env fallback (l.194-199)
   - no verified claim + `settings.oidc_enabled` FALSE → `raise OIDC_NOT_CONFIGURED.exc()` → **404** (l.201)
   An unauthenticated prober walking a domain list distinguishes 403 ("X IS a verified customer domain whose tenant has not enabled SSO") from 404 ("X is not a claimed domain"). No auth, no rate-limit coupling, one GET per domain.

2. **⚠ THE NON-OBVIOUS PART — swapping the code alone does NOT close the oracle.** The 403 branch `raise`s *before* the env-Settings fallback. Change only the spec at l.173 and, with `GATEWAY_OIDC_ENABLED=true`, you get: claimed-unconfigured → 404 (early raise) vs unclaimed → **302** (env fallback). The oracle survives, merely inverted. The claimed-but-unconfigured leg must **fall through to the same terminal branch** as the unclaimed leg, not short-circuit. This is why the fix is a control-flow change, not a one-token edit.

3. **…but it must NOT fall through into `resolver.resolve(domain)`.** If a claimed-but-unconfigured domain were allowed to reach the domain-keyed fallback, another tenant's `oidc_provider_configs.email_domains` row containing that domain would route the claimant's users to a foreign IdP — precisely the cross-tenant hijack domain-routing-unification M1/M2 closed. The fall-through must skip the domain-keyed leg and land on the env/terminal branch only.

4. **A same-code/different-title collapse would be a silent oracle.** `OIDC_TENANT_NOT_CONFIGURED` shares `code="ERR_OIDC_NOT_CONFIGURED"` but carries a different `title_template`. `assert_problem` checks status+code only, so using it on one leg and `OIDC_NOT_CONFIGURED` on the other would pass every existing test while leaving the response bodies distinguishable to a prober reading `title`. The invariant must be asserted on the **whole body**, not on `code`.

5. **The docstring at `oidc_login` overclaims.** It states the 403 is "the same fail-closed rejection as an unclaimed domain (M2: no oracle between the two)" — demonstrably false against its own code (403 vs 404). The inline comment at the raise site repeats it. Correcting both is in scope; leaving a false security claim in shipped auth code is its own defect.

6. **The 403 login leg is UNTESTED — which is why this shipped.** Exhaustive grep of `ERR_OIDC_DOMAIN_NOT_MAPPED` across `apps/gateway/tests/` returns exactly one assertion: `apps/gateway/tests/sso_oidc/test_sso_oidc.py:528`, and it is the **callback** path (`build_callback_url`, S5 "unknown email domain"). **No test asserts the login-path 403 today.** Consequence for §4: the retarget surface is far smaller than assumed at intake — see §3 "Retarget register", which names the true set (and the reason it is nearly empty) rather than inventing churn.

7. **Residual, accepted, disclosed:** 302-vs-4xx still tells a prober whether a domain has SSO configured, and the `Location` header names the IdP. This is inherent to domain-based SSO discovery — the endpoint's *function* is to route a user to their IdP. Okta, Slack, and Google Workspace all expose the same signal. Closing it would require abandoning domain-based discovery (e.g. emailing a link instead), which is out of scope and would break every legitimate SSO entry. Accepted and stated in §3, not silently ignored.

8. **Residual, accepted:** timing. Claimed-unconfigured does claim-lookup + `resolve_by_tenant_id`; unclaimed does claim-lookup + `resolve(domain)`. Two DB round-trips either way — comparable, not equalized. Sub-millisecond query-shape variance under network jitter is not a practical enumeration channel at this asset value. Not addressed; recorded.

9. **Blast-radius risk (low, bounded).** The fall-through newly lets a claimed-but-unconfigured domain reach the env fallback when `oidc_enabled=True`. Grep of `oidc_enabled=True` fixtures: `domain_routing_unification:358` (callback test, no verified claim), `sso_oidc:170/972/1064`, `oidc_jwks:356/978`, `oidc_tenant_config:373`, `plan_seat_cap/conftest:288`. None seeds a verified claim WITHOUT an enabled config on the login path, so no existing green is expected to flip. Not certain without running the suites — this is the §1 ⚠ assumption.

Related intent:
- Milestone `frontdoor-persona-routing` m-goal: "Every visitor who arrives at Hydroa's front door reaches a live next step" — the front door is now an unauthenticated, domain-parameterized discovery surface, which is exactly what makes this leak reachable.
- `.add/PROJECT.md` — tenant isolation discipline; `InviteNotFoundError`'s own docstring names unknown-id and wrong-tenant as "deliberately indistinguishable". This task applies that same established project principle to SSO login-init.
- **Boundary (explicit):** `domain-aware-auth-routing` owns the CLIENT-side persona classification and its §3 M12 keeps its server surface INTENTIONALLY EMPTY — its security property is delivered BY the absence of a server surface. Folding this change there would violate M12. This task owns exactly ONE server-side error-code/control-flow change and touches no dashboard file.
- GLOSSARY: no new term. "verified claim", "unified domain resolver" are already defined (domain-routing-unification glossary deltas, folded at foundation v54).

Ground SHA: `9421827` (branch `feat/frontdoor-persona-routing`) — symbols are the durable anchors; the `l.NNN` refs above read "as of" this commit.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: OIDC login-init terminal-response collapse — every no-resolution outcome of `GET /auth/oidc/login` returns one indistinguishable response, so an unauthenticated domain probe cannot separate "verified customer domain, SSO not enabled" from "domain unknown to us".

Framings weighed:
- **(b) Collapse onto the EXISTING terminal `404 ERR_OIDC_NOT_CONFIGURED`, and make the claimed-but-unconfigured leg fall through to the shared terminal branch instead of short-circuiting (chosen).** Matches `saml_login`, which already collapses all three no-resolution cases onto one 404. Reuses a shipped `ErrorSpec` — no new catalog entry, no BFF change, no dashboard change. Because the two frozen login-path assertions already expect this exact code, the retarget surface is ~zero and no frozen test is put at risk. Semantically honest: "not configured" describes the observable truth of every collapsed case and asserts nothing about whether the domain is known.
- **(a) One NEW uniform code (e.g. `ERR_SSO_NOT_AVAILABLE`).** Cleanest naming, but it churns every already-green `ERR_OIDC_NOT_CONFIGURED` assertion across three suites (`domain_routing_unification`, `sso_oidc`, `oidc_tenant_config`), diverges from SAML's 404, and stales the BFF prose. Decisively: domain-routing-unification CR-v2 *already reverted* a proposed new code (`SAML_DOMAIN_NOT_MAPPED`) for exactly this cost. Repeating that mistake with the same contract in view would be indefensible. REJECTED.
- **(c) Collapse onto 403 for both.** Would force retargeting the two frozen 404 login assertions (more frozen-test risk, not less), diverges from SAML, and 403 "Forbidden" *implies the domain is known but denied* — a weaker, leakier semantic than 404. REJECTED.

Must:
<must>
  - M1 — `oidc_login`, when `?domain=` resolves a verified claim whose tenant has NO enabled OIDC config, MUST NOT raise `OIDC_DOMAIN_NOT_MAPPED`. It falls through to the shared terminal branch shared with every other no-resolution case.
  - M2 — The claimed-but-unconfigured fall-through MUST skip the domain-keyed `resolver.resolve(domain)` leg. A verified claim continues to be routed ONLY by `resolve_by_tenant_id` (domain-routing-unification M1/M8 preserved — no `email_domains` containment match may ever route a claimed domain).
  - M3 — ANTI-ORACLE INVARIANT: for one fixed `Settings`, the response to `GET /auth/oidc/login?domain=<claimed-but-unconfigured>` MUST be indistinguishable from the response to `GET /auth/oidc/login?domain=<unclaimed-and-unresolvable>` on: status code, the full response body, and the set of `Set-Cookie` names. This holds in BOTH deployments — `oidc_enabled=False` (both 404) and `oidc_enabled=True` (both 302 to the env IdP).
  - M4 — The single terminal rejection is the EXISTING `OIDC_NOT_CONFIGURED` spec → `404 ERR_OIDC_NOT_CONFIGURED`, raised with no `detail`, no `headers`, no `extra`, so the body is byte-identical by construction. `OIDC_TENANT_NOT_CONFIGURED` MUST NOT be used on the login path (its differing title would re-open the oracle beneath the `code` field).
  - M5 — Every legitimate SSO flow is unchanged: verified claim + enabled config → 302 with `oidc_state`/`oidc_nonce`/`oidc_tenant_id` cookies and `oidc_tenant_id` == the claim's tenant; no-claim + legacy `resolver.resolve(domain)` hit → 302; no-claim + `oidc_enabled=True` → 302 via env fallback with `oidc_tenant_id` == `ENV_CONFIG_COOKIE_VALUE`; no `?domain=` + env disabled → 404.
  - M6 — `oidc_callback` is untouched. Its `OIDC_DOMAIN_NOT_MAPPED` (403, post-authentication) and `OIDC_TENANT_NOT_CONFIGURED` raises remain exactly as shipped, and the `OIDC_DOMAIN_NOT_MAPPED` import stays.
  - M7 — `error_catalog.py` is NOT edited. No spec added, removed, or reworded. `OIDC_DOMAIN_NOT_MAPPED` remains in the catalog (still raised by `oidc_callback`).
  - M8 — The `oidc_login` docstring and the raise-site inline comment are corrected: the false "no oracle between 'unclaimed' and 'claimed but unconfigured'" gloss is replaced with an accurate statement of the collapsed behavior AND an explicit note of the accepted 302-vs-4xx residual.
  - M9 — SERVER-SIDE ONLY. No file under `apps/dashboard/` is touched (the BFF already relays any 4xx verbatim; `domain-aware-auth-routing` §3 M12 keeps its server surface intentionally empty and is not disturbed).
</must>

Reject:
<reject>
  - Verified claim exists, tenant has no enabled OIDC config, `oidc_enabled=False` -> "ERR_OIDC_NOT_CONFIGURED" (404)
  - No verified claim, no legacy config resolves, `oidc_enabled=False` -> "ERR_OIDC_NOT_CONFIGURED" (404)  [pre-existing, unchanged]
  - No `?domain=` supplied, `oidc_enabled=False` -> "ERR_OIDC_NOT_CONFIGURED" (404)  [pre-existing, unchanged]
</reject>

After:
<after>
  - `GET /auth/oidc/login` has exactly ONE terminal rejection code, `404 ERR_OIDC_NOT_CONFIGURED` — matching `saml_login`'s already-collapsed single-404 shape.
  - `raise OIDC_DOMAIN_NOT_MAPPED.exc()` no longer appears anywhere in `oidc_login`; it survives only in `oidc_callback`.
  - Probing an arbitrary domain list against `/auth/oidc/login` partitions domains into {has SSO configured} and {everything else} — never into {verified customer} and {stranger}.
  - The residual 302-vs-4xx signal is documented in code, in §3, and accepted at freeze rather than silently carried.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **No existing green test exercises "verified claim + no enabled OIDC config" on the LOGIN path.** Lowest confidence because it is established by exhaustive grep of both error codes across `apps/gateway/tests/` plus a fixture scan of `oidc_enabled=True`, but NOT by running the suites (this task is forbidden from running pytest — test runs regenerate `.coverage`/`.pytest_cache` and poison the ADD gate scope-walk). If wrong, the fall-through flips such a test from 403 to 404 (or, in an `oidc_enabled=True` fixture, from 403 to 302). Cost: bounded and cheap — the correct response is a RETARGET of the expected code to the newly contracted one at TESTS/BUILD (never a deletion or a loosening to "is 4xx"); a 403→302 flip would be the more serious signal and must be escalated, not retargeted, because it would mean a claimed-but-unconfigured tenant is being routed somewhere. §6 build-expectations require both suites to be named and run at BUILD.
  - [x] The claimed-but-unconfigured leg currently short-circuits BEFORE the env fallback — CONFIRMED by reading `oidc_login` at Ground SHA (the `raise` precedes the `elif settings.oidc_enabled:` branch).
  - [x] The BFF relay needs no change — CONFIRMED: `route.ts` forwards any `status >= 400 && < 500` verbatim; only prose names 404.
  - [x] `assert_problem` does not assert `title` — CONFIRMED at `domain_routing_unification/conftest.py:312-316`; hence M3 asserts the FULL body, not just `code`.
  - [x] `saml_login` already collapses all no-resolution cases onto one 404 — CONFIRMED by reading `saml_router.py:saml_login` (single `SAML_NOT_CONFIGURED` raise for no-claim, claimed-unconfigured, and no-domain).
  - [x] `sso_oidc:528`'s 403 assertion is the CALLBACK, not login — CONFIRMED (`build_callback_url`, S5). Out of scope, stays green untouched.
  - [ ] `oidc_enabled=True` in production is off (default `False` in `config.py:267`), so the real-world collapsed behavior is 404-for-both. Confirm with Tin at freeze — it determines whether the env-fallback leg of M3 is a live production path or a test-only one.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Claimed-but-unconfigured domain returns the collapsed terminal, not 403   # M1, M4, R1
  Given tenant A holds a VERIFIED domain claim for "claimed-nosso.com"
  And tenant A has NO enabled OIDC provider config
  And Settings.oidc_enabled is False
  When an unauthenticated client GETs /auth/oidc/login?domain=claimed-nosso.com
  Then the response is 404 with body code "ERR_OIDC_NOT_CONFIGURED"
  And the response is NOT 403 and the body code is never "ERR_OIDC_DOMAIN_NOT_MAPPED"
  And no oidc_state, oidc_nonce, or oidc_tenant_id cookie is set
  And tenant A's domain claim, config rows, and users are unchanged

Scenario: Anti-oracle — the two probe answers are byte-identical (env disabled)   # M3
  Given tenant A holds a VERIFIED claim for "claimed-nosso.com" with NO enabled OIDC config
  And no tenant claims or configures "ghost-unknown.com"
  And Settings.oidc_enabled is False
  When an unauthenticated client GETs /auth/oidc/login for each of the two domains
  Then both responses have the same status code
  And both response bodies are exactly equal, field for field, including "title"
  And both set exactly the same (empty) set of cookie names
  And no state is created or modified by either probe

Scenario: Anti-oracle holds under the env fallback deployment too   # M3
  Given tenant A holds a VERIFIED claim for "claimed-nosso.com" with NO enabled OIDC config
  And no tenant claims or configures "ghost-unknown.com"
  And Settings.oidc_enabled is True with an env issuer/client_id configured
  When an unauthenticated client GETs /auth/oidc/login for each of the two domains
  Then both responses are 302 to the SAME env authorize endpoint host and path
  And both set oidc_tenant_id to the env-config sentinel value
  And neither response reveals that one domain is claimed and the other is not
  And no domain claim or config row is modified by either probe

Scenario: A claimed domain is never routed by another tenant's email_domains   # M2
  Given tenant A holds a VERIFIED claim for "claimed-nosso.com" and has NO enabled OIDC config
  And tenant B has an enabled OIDC config whose email_domains contains "claimed-nosso.com"
  And Settings.oidc_enabled is False
  When an unauthenticated client GETs /auth/oidc/login?domain=claimed-nosso.com
  Then the response is 404 "ERR_OIDC_NOT_CONFIGURED"
  And the response is NOT a 302 to tenant B's IdP
  And no oidc_tenant_id cookie carrying tenant B is set

Scenario: The legitimate configured-SSO flow is unchanged   # M5
  Given tenant A holds a VERIFIED claim for "acme-sso.com"
  And tenant A has an ENABLED OIDC config
  When an unauthenticated client GETs /auth/oidc/login?domain=acme-sso.com
  Then the response is 302 to tenant A's IdP authorize endpoint
  And oidc_state, oidc_nonce, and oidc_tenant_id cookies are set
  And oidc_tenant_id equals tenant A's id

Scenario: Unclaimed domain with env fallback enabled still routes   # M5
  Given no tenant claims or configures "ghost-unknown.com"
  And Settings.oidc_enabled is True
  When an unauthenticated client GETs /auth/oidc/login?domain=ghost-unknown.com
  Then the response is 302 to the env authorize endpoint
  And oidc_tenant_id equals the env-config sentinel value

Scenario: No-domain call keeps its pre-existing shape   # M5, R3
  Given Settings.oidc_enabled is False
  When an unauthenticated client GETs /auth/oidc/login with no domain param
  Then the response is 404 with body code "ERR_OIDC_NOT_CONFIGURED"
  And no cookie is set

Scenario: The SAML sibling path is untouched and stays consistent   # M6, consistency
  Given no tenant claims "ghost-unknown.com"
  And tenant A holds a VERIFIED claim for "claimed-nosso.com" with no enabled SAML config
  When an unauthenticated client GETs /auth/saml/login for each domain
  Then both responses are 404 with body code "ERR_SAML_NOT_CONFIGURED"
  And the OIDC and SAML paths now agree: exactly one terminal rejection code each

Scenario: The callback's 403 is preserved — this task did not over-collapse   # M6, M7
  Given a callback arrives bearing a validly-signed ID token whose verified email domain maps to no tenant
  When the client GETs /auth/oidc/callback with matching state and nonce cookies
  Then the response is 403 with body code "ERR_OIDC_DOMAIN_NOT_MAPPED"
  And no ai_proxy_session cookie is set
  And the OIDC_DOMAIN_NOT_MAPPED ErrorSpec is unchanged in the catalog

Scenario: The login path no longer raises the domain-not-mapped code at all   # M1, M8
  Given the shipped oidc_router source at build completion
  When oidc_login is read end to end
  Then it contains no raise of OIDC_DOMAIN_NOT_MAPPED
  And its docstring no longer claims there is no oracle between unclaimed and claimed-but-unconfigured
  And its docstring states the accepted 302-vs-4xx residual explicitly
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# ── OIDC login-init: ONE collapsed terminal rejection (this task) ──
GET /auth/oidc/login?domain=<d>            # unauthenticated, public
  302 -> IdP redirect
         # (i)  verified claim for d whose tenant HAS an enabled config → config via resolve_by_tenant_id;
         #      oidc_tenant_id = that tenant_id
         # (ii) NO verified claim → existing deterministic DbOidcConfigResolver.resolve(d) fallback (M6 of
         #      domain-routing-unification, retained); oidc_tenant_id = that config's tenant_id
         # (iii) neither of the above AND Settings.oidc_enabled → env-Settings fallback;
         #      oidc_tenant_id = ENV_CONFIG_COOKIE_VALUE ("env-config")
  404 -> { code: "ERR_OIDC_NOT_CONFIGURED" }        # the SOLE terminal rejection — every no-resolution case
         # raised from the EXISTING error_catalog.OIDC_NOT_CONFIGURED spec with detail=None, headers=None,
         # extra=None → body byte-identical by construction across all collapsed cases.
         # ERR_OIDC_DOMAIN_NOT_MAPPED is NO LONGER reachable from this route.

# ── Control flow (normative — the code change, not an implementation hint) ──
oidc_login(request, session) -> Response
  1. claim = resolve_verified_tenant_for_raw_domain(claim_repo, d)      # unchanged
  2. if claim is not None:
       config = resolver.resolve_by_tenant_id(str(claim))               # unchanged
       # CHANGED: when config is None, DO NOT raise. Fall through to step 4.
       # MUST NOT fall into resolver.resolve(d) — a claimed domain is never routed by an
       # email_domains containment match (domain-routing-unification M1/M8 preserved).
     elif resolver is not None:
       config = resolver.resolve(d)                                     # unchanged
  3. no-domain shape                                                    # unchanged
  4. TERMINAL (shared by every unresolved case): if Settings.oidc_enabled → env fallback 302,
     else raise OIDC_NOT_CONFIGURED.exc() → 404.

# ── ANTI-ORACLE INVARIANT (testable property, M3) ──
for any fixed Settings S, and any domain pair (dc, du) where
    dc = a domain with a VERIFIED claim whose tenant has NO enabled OIDC config
    du = a domain with NO verified claim and NO resolvable config
  response(GET /auth/oidc/login?domain=dc)  ≡  response(GET /auth/oidc/login?domain=du)
  where ≡ compares: status_code · the FULL response body (every field, incl. "title") · the SET of
  Set-Cookie names. Asserted for S.oidc_enabled ∈ {False, True} — the invariant is deployment-independent.
  NOTE: ≡ on the full body is required, not merely on `code`: error_catalog holds two distinct specs
  sharing code "ERR_OIDC_NOT_CONFIGURED" with DIFFERENT titles (OIDC_NOT_CONFIGURED /
  OIDC_TENANT_NOT_CONFIGURED), and assert_problem checks status+code only — a title-level oracle
  would pass every code-level assertion silently. The login path uses OIDC_NOT_CONFIGURED only.

# ── UNCHANGED (explicitly out of scope — guarded, not merely omitted) ──
GET /auth/oidc/callback
  403 -> { code: "ERR_OIDC_DOMAIN_NOT_MAPPED" }   # post-authentication; requires an IdP-signed ID token,
                                                  # therefore NOT an unauthenticated enumeration surface. KEPT.
  404 -> { code: "ERR_OIDC_NOT_CONFIGURED" }      # from OIDC_TENANT_NOT_CONFIGURED. KEPT.
GET /auth/saml/login?domain=<d>
  404 -> { code: "ERR_SAML_NOT_CONFIGURED" }      # already collapsed (no-claim, claimed-unconfigured,
                                                  # and no-domain all terminate here). NOT TOUCHED — OIDC is
                                                  # being brought into line with this shape, not vice versa.
error_catalog.py                                  # NOT EDITED. No spec added, removed, or reworded.
                                                  # OIDC_DOMAIN_NOT_MAPPED remains (raised by oidc_callback).
apps/dashboard/**                                 # NOT TOUCHED. The BFF relay forwards any 4xx verbatim;
                                                  # domain-aware-auth-routing §3 M12 ("SERVER SURFACE —
                                                  # INTENTIONALLY EMPTY") is not disturbed by this task.

Schema: none. No table, column, index, migration, fixture, config key, or dependency is touched.
        Read-only use of tenant_domain_claims and oidc_provider_configs via existing resolvers.
```

### CHANGE REQUEST against `domain-routing-unification` §3 M2 (FROZEN @ v2/CR-v2)

The frozen file is **NOT edited**. This clause is the disclosed amendment, recorded here, in this task's
§3, exactly as `pricing-tier-ladder` §3 amended `plan-tiers-and-base-fee` §3's render-count clause.

- **The frozen term.** domain-routing-unification §3 reads:
  `4xx -> { error: "OIDC_DOMAIN_NOT_MAPPED" (403) | "OIDC_NOT_CONFIGURED" (404) }   # existing codes; NEVER 500 (M2,R1)`
- **What it actually promises.** That terminal no-resolution reuses the EXISTING codes and is **NEVER a 500**.
  It permits either of the two codes; it does **not** promise the two states are indistinguishable.
- **Therefore this is NOT a contract violation to heal.** Nothing in the frozen text is broken by the current
  code. This is a **deliberate NARROWING of an over-permissive frozen term**: `403 | 404` becomes `404` only.
  A narrowing — every response this task produces was already permitted by the frozen alternation.
- **Why narrow rather than defer.** The permissiveness is the defect. The alternation is exactly what lets an
  unauthenticated prober separate verified customer domains from unknown ones. The frozen contract's own
  *intent* is recorded in the shipped docstring it authored ("no oracle between 'unclaimed' and 'claimed but
  unconfigured'"); the code never delivered it. This task makes the code match that stated intent.
- **Scope of the amendment.** OIDC **login-init only**. domain-routing-unification's M3 (SAML), M5 (admin
  write-gate 409/422), M6 (deterministic resolver), M7 (backfill), M8 (normalization), and the callback
  behavior are all untouched and remain frozen as written.
- **Amended text (this task's §3 governs the OIDC login-init line from freeze onward):**
  `4xx -> { code: "ERR_OIDC_NOT_CONFIGURED" (404) }   # sole terminal; NEVER 500; NEVER 403 on this route`
- **Consequence to disclose:** `403 ERR_OIDC_DOMAIN_NOT_MAPPED` becomes unreachable from `/auth/oidc/login`.
  Any operator runbook, dashboard copy, or alert keyed to that code *on the login path* is now dead. Grep
  found no such consumer (the BFF is 4xx-generic; no dashboard file names the code).

### Accepted residual — stated, not silently carried

- **302-vs-4xx remains observable.** A prober still learns whether a domain has SSO configured, and the
  `Location` header names the IdP. This is **largely unavoidable for domain-based SSO discovery** — routing
  the user to their IdP IS the endpoint's function. Okta, Slack, and Google Workspace all reveal the same
  signal. Removing it means abandoning domain-based discovery entirely, which is out of scope and would
  break every legitimate SSO entry. **Accepted.** What this task removes is the strictly worse signal: the
  ability to identify a *verified customer domain that has not yet enabled SSO* — commercially sensitive
  (it names prospects/customers) and useless to any legitimate caller.
- **Timing.** The two collapsed legs issue comparable but not identical DB work (claim-lookup +
  `resolve_by_tenant_id` vs claim-lookup + `resolve(domain)`). Not equalized; sub-ms query-shape variance
  under network jitter is not a practical channel at this asset value. **Accepted, recorded, not addressed.**
- **A legitimate user hitting a genuinely misconfigured tenant** now sees the uniform 404 "OIDC login is not
  configured on this platform" instead of "Email domain not permitted for SSO login". Both are terminal
  dead-ends with the identical real remedy (contact your admin / use password login), and the pre-existing
  no-domain and unclaimed legs already produced this exact message. Deliberate: uniformity is the security
  property, and per-case actionable detail is precisely the oracle. No regression in actionability.

### Retarget register — every already-green assertion, checked

Retarget = update the expected code to the newly contracted one. **Never** delete an assertion, never
loosen one to a bare "is 4xx". Established by exhaustive grep of `ERR_OIDC_NOT_CONFIGURED` and
`ERR_OIDC_DOMAIN_NOT_MAPPED` across `apps/gateway/tests/` at Ground SHA `9421827`:

| file:line | asserts | path | verdict |
|---|---|---|---|
| `apps/gateway/tests/domain_routing_unification/test_domain_routing_unification.py:170` | `assert_problem(resp, 404, "ERR_OIDC_NOT_CONFIGURED")` | login, unclaimed, env off | **NO retarget** — already the contracted code; stays green verbatim |
| `apps/gateway/tests/domain_routing_unification/test_domain_routing_unification.py:644` | `assert_problem(subdomain, 404, "ERR_OIDC_NOT_CONFIGURED")` | login, subdomain (never claims), env off | **NO retarget** — already the contracted code; stays green verbatim |
| `apps/gateway/tests/sso_oidc/test_sso_oidc.py:305` | `assert_problem(resp, 404, "ERR_OIDC_NOT_CONFIGURED")` | login, env-disabled | **NO retarget** — unchanged leg |
| `apps/gateway/tests/sso_oidc/test_sso_oidc.py:336` | `assert_problem(resp, 404, "ERR_OIDC_NOT_CONFIGURED")` | login, env-disabled | **NO retarget** — unchanged leg |
| `apps/gateway/tests/oidc_tenant_config/test_oidc_tenant_config.py:1074` | `assert_problem(resp, 404, "ERR_OIDC_NOT_CONFIGURED")` | login, unknown domain (T5) | **NO retarget** — unchanged leg |
| `apps/gateway/tests/sso_oidc/test_sso_oidc.py:528` | `assert_problem(resp, 403, "ERR_OIDC_DOMAIN_NOT_MAPPED")` | **CALLBACK** (`build_callback_url`, S5) | **NO retarget — OUT OF SCOPE.** Post-auth; guarded by M6 and its own scenario. Must stay green and untouched. |

**Finding, stated plainly rather than papered over:** the retarget set is **EMPTY**. Intake expected
`:170` and `:644` to need retargeting; they do not — both already assert the code this task collapses onto,
which is independent corroboration that 404 is the right target. The reason no assertion needs changing is
that **the login-path 403 leg was never tested at all** — that untested branch is why this oracle shipped
through a security-sensitive frozen review. Frozen-test *prose* at `:162-165` and `:614-627` narrates "the
two contracted fail-closed codes (403/404)" and goes stale; it is **deliberately NOT edited** (editing a
frozen test file, even a comment, trips `build_tampered` — [[add-tamper-tripwire-ordering]]). Recorded as a
§7 spec delta for the milestone-close fold instead.

**Standing instruction to TESTS/BUILD:** if running the suites reveals an assertion this static analysis
missed, RETARGET the expected code — never delete, never loosen. One exception: a 403→**302** flip is not a
retarget candidate; it would mean a claimed-but-unconfigured tenant is being *routed* somewhere. Escalate
that as a HARD-STOP finding, do not "fix" the test.

Glossary deltas: none — this task introduces no new domain term. It narrows the observable surface of terms
(`verified claim`, `unified domain resolver`) already defined by domain-routing-unification and folded at
foundation v54.

Least-sure flag surfaced at freeze: [spec] That NO existing green test exercises the "verified claim + no enabled OIDC config" LOGIN leg — established by exhaustive grep of both error codes across `apps/gateway/tests/` plus a fixture scan of every `oidc_enabled=True` site, but NOT by executing the suites (this task is barred from running pytest: test runs regenerate `.coverage`/`.pytest_cache` and poison the ADD gate scope-walk, which cost real attempts on this branch today). If wrong, the fall-through flips a currently-green assertion from 403 to 404 — cheap and bounded, handled by RETARGETING the expected code at TESTS/BUILD, never by weakening it. The materially worse variant: a 403→302 flip under an `oidc_enabled=True` fixture would mean a claimed-but-unconfigured tenant now routes to the env IdP; that is a HARD-STOP escalation, not a retarget. Secondary, for Tin: confirm `GATEWAY_OIDC_ENABLED` is FALSE in production — it decides whether M3's env-fallback leg is a live production path or test-only.

RESOLVED at freeze (orchestrator, 2026-07-21, evidence-based): `GATEWAY_OIDC_ENABLED` is FALSE in
production. Evidence: the key appears in NO chart values file (`charts/ai-proxy/values.yaml`,
`values-prod.yaml`, `values-kind.yaml`) nor any template — the only occurrence repo-wide is
`infra/docker-compose.e2e.v4.yml:27` (the local e2e stack) — so the `Settings.oidc_enabled: bool = False`
default (config.py:267) governs production. CONSEQUENCES, both favorable: (1) the oracle IS live in
production today — claimed-but-unconfigured 403 vs unclaimed 404 are both reachable and
distinguishable — so this task closes a REAL leak, not a theoretical one; (2) M3's env-fallback 302
leg is TEST-ONLY, so the "403→302 flip" HARD-STOP variant above cannot occur in production, only
under an e2e/test fixture. RESIDUAL UNCERTAINTY (stated, not hidden): this is repo-config evidence
only; an externally-injected env var in Tin's live deployment would override it. If production ever
sets it TRUE, re-verify the fall-through terminal before trusting the invariant.
Status: FROZEN @ v1 — approved by Tin Dang
Reported: no

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 95% on the changed `oidc_login` branch set (the file's terminal-resolution paths); no
decrease anywhere else.

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_claimed_but_unconfigured_returns_collapsed_404: arrange verified claim for tenant A + NO enabled OIDC config + oidc_enabled=False / act GET /auth/oidc/login?domain=claimed-nosso.com / assert 404 + code ERR_OIDC_NOT_CONFIGURED + NOT 403 + code never ERR_OIDC_DOMAIN_NOT_MAPPED / assert no oidc_* cookie set + claim/config/user rows unchanged · covers: M1, M4, R1
  - test_anti_oracle_bodies_identical_env_disabled: arrange claimed-unconfigured domain + unclaimed domain + oidc_enabled=False / act GET login for both / assert status_code equal AND full response .json() dicts equal (field for field, incl. title) AND identical Set-Cookie name sets · covers: M3
  - test_anti_oracle_bodies_identical_env_enabled: arrange same two domains + oidc_enabled=True with env issuer/client_id / act GET login for both / assert both 302 to the same authorize host+path AND both oidc_tenant_id == ENV_CONFIG_COOKIE_VALUE AND no claim/config row modified · covers: M3
  - test_claimed_domain_never_routed_by_foreign_email_domains: arrange tenant A verified claim + no config; tenant B enabled config whose email_domains contains that domain; oidc_enabled=False / act GET login?domain=<A's domain> / assert 404 ERR_OIDC_NOT_CONFIGURED + NOT 302 + no oidc_tenant_id cookie carrying tenant B · covers: M2
  - test_configured_sso_flow_unchanged: arrange verified claim + enabled config for tenant A / act GET login?domain=acme-sso.com / assert 302 to tenant A IdP + oidc_state/oidc_nonce/oidc_tenant_id set + oidc_tenant_id == tenant A id · covers: M5
  - test_unclaimed_with_env_fallback_still_routes: arrange no claim, no config, oidc_enabled=True / act GET login?domain=ghost-unknown.com / assert 302 to env authorize + oidc_tenant_id == ENV_CONFIG_COOKIE_VALUE · covers: M5
  - test_no_domain_shape_unchanged: arrange oidc_enabled=False / act GET login with no domain param / assert 404 ERR_OIDC_NOT_CONFIGURED + no cookie set · covers: M5, R3
  - test_saml_sibling_stays_collapsed_and_consistent: arrange unclaimed domain + claimed-domain-without-SAML-config / act GET /auth/saml/login for both / assert both 404 ERR_SAML_NOT_CONFIGURED · covers: M6, cross-path consistency
  - test_callback_403_preserved: arrange callback with signed ID token whose verified email domain maps to no tenant + matching state/nonce cookies / act GET /auth/oidc/callback / assert 403 ERR_OIDC_DOMAIN_NOT_MAPPED + no ai_proxy_session cookie · covers: M6, M7
  - test_login_source_has_no_domain_not_mapped_raise: arrange read the shipped oidc_router source / act inspect oidc_login's source + docstring / assert no OIDC_DOMAIN_NOT_MAPPED raise within oidc_login, docstring no longer claims "no oracle", docstring states the 302-vs-4xx residual · covers: M1, M8
</test_plan>

Tests live in: `apps/gateway/tests/sso_login_oracle_closure/` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/auth/api/oidc_router.py` `apps/gateway/tests/sso_login_oracle_closure/`

Strategy (ordered batches):
1. Write the red suite in `apps/gateway/tests/sso_login_oracle_closure/` (its own conftest; reuse the
   `domain_routing_unification/conftest.py` fixture shapes — `claim_and_verify_domain`, `insert_oidc_config_row`,
   `signup_and_login`, `get_cookies_from_response` — by copying the pattern, NOT by importing across suites).
   Confirm red for the RIGHT reason: the claimed-unconfigured probe returns 403 today.
2. `oidc_login`: delete the `raise OIDC_DOMAIN_NOT_MAPPED.exc()` inside the `mapped_tenant_id is not None`
   branch. Let `oidc_config` stay None and fall through to the shared `if oidc_config is not None: … elif
   settings.oidc_enabled: … else: raise OIDC_NOT_CONFIGURED.exc()` terminal. Structure the branch so a claim
   hit CANNOT reach `resolver.resolve(domain)` — an `elif` on `mapped_tenant_id is None`, not a bare fall-out
   that lets the domain-keyed leg run. This is the one place a plausible-looking edit silently re-opens the
   M1/M8 cross-tenant hijack; write it so the structure makes that impossible, not so a comment forbids it.
3. Keep the `OIDC_DOMAIN_NOT_MAPPED` import — `oidc_callback` still raises it. Pyright/ruff will not flag it;
   if a linter does, the import is still correct and the linter is wrong about the file.
4. Rewrite the `oidc_login` docstring + the raise-site comment: state the collapsed single-404 terminal, cite
   this task's §3 as the amending contract over domain-routing-unification §3 M2, and name the accepted
   302-vs-4xx residual. Delete the false "no oracle between 'unclaimed' and 'claimed but unconfigured'" gloss.
5. Run `apps/gateway/tests/sso_login_oracle_closure/`, then the three neighbours named in the retarget
   register — `domain_routing_unification`, `sso_oidc`, `oidc_tenant_config` — as FOREGROUND chunks at `-n 5/6`
   (never one `-n 12`; this 12-core host load-saturates). Any newly-red assertion: RETARGET the expected code
   per §3's standing instruction; a 403→302 flip is a HARD-STOP escalation, not a retarget.
6. LAST step before the gate: delete `.coverage` and `.pytest_cache` — build artifacts poison the ADD gate
   scope-walk ([[add-scope-snapshot-poisoning]]).

Persona (required): `.add/personas/appsec-engineer.md` — "Assume breach, verify both failure directions,
escalate to HARD-STOP." Its stance is the right one here: this codebase's own precedent is
`InviteNotFoundError`, whose docstring names unknown-id and wrong-tenant as "deliberately indistinguishable".
This task applies that exact established discipline to SSO login-init. `flow: build, advisor` — the correct
persona for the BUILD span; this design span was drafted under a generic domain-analyst/interface-architect
stance since no `flow: design` persona in `.add/personas/` covers backend security (the three that exist —
accessibility-auditor, ui-designer, ux-researcher — are all UI). Recorded as a §7 competency delta.

Spawn isolation (default): `isolation: "worktree"` — mandatory here, not preferential. Build agents are
concurrently editing `apps/dashboard/components/auth/SignupForm.tsx`, `apps/dashboard/app/(marketing)/page.tsx`,
and `apps/dashboard/lib/` on this branch. This task's scope is disjoint from theirs (gateway only), but a
shared tree would still cross-contaminate the gate scope-walk.

Known-problem fixes:
- trap: swapping the ErrorSpec at the raise site and calling it done → the oracle survives inverted as 404-vs-302
  under `oidc_enabled=True`. fix: the branch must FALL THROUGH to the shared terminal, never short-circuit (§3 step 2).
- trap: letting the claimed-but-unconfigured leg fall into `resolver.resolve(domain)` → re-opens the cross-tenant
  hijack that domain-routing-unification M1/M8 closed. fix: structural `elif`, plus `test_claimed_domain_never_routed_by_foreign_email_domains` as the guard.
- trap: collapsing onto `OIDC_TENANT_NOT_CONFIGURED` (same code, different title) → title-level oracle that passes
  every `assert_problem` silently. fix: use `OIDC_NOT_CONFIGURED` only; assert FULL body equality, not `code`.
- trap: editing frozen test prose in `domain_routing_unification` to fix the stale 403/404 narration → `build_tampered`,
  burns a heal attempt. fix: leave it; it is a §7 spec delta.
- trap: touching `error_catalog.py` "for tidiness" (e.g. removing the now-login-unused 403 spec) → breaks
  `oidc_callback` and blows scope. fix: `error_catalog.py` is not in §5 scope. It cannot be written.
- trap: running the full suite as one `-n 12` → load-saturates this 12-core host. fix: 4-5 foreground chunks at `-n 5/6`.

Strategy actually used: <fill at VERIFY>

Safety rule (feature-specific): every terminal no-resolution exit of `oidc_login` must be reachable ONLY through
the single shared terminal branch — there must be exactly one `raise OIDC_NOT_CONFIGURED.exc()` statement in
`oidc_login`, and no other `raise` of any kind. A second exit is how the oracle comes back.

Code lives in: `apps/gateway/src/gateway/auth/api/oidc_router.py`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 54 passed / 0 failed in 59.5s across the four suites, on a DEDICATED database.
      ⚠ An earlier run on the SHARED `:5433` gateway_test DB read 5-failed/49-passed; root-caused as
      phantom, NOT red-for-cause: all 5 failed with FK/schema errors ("Key (tenant_id)=… is not
      present in table tenants") and never an error-code assertion, all 5 passed in isolation, and
      `ps aux` showed 7 concurrent pytest processes drop_all/create_all-ing the same schema. This is
      the documented shared-test-postgres signature. Operational note for the pre-merge full suite:
      on this branch a shared-DB run WILL read false red — use a dedicated `GATEWAY_TEST_DATABASE_URL`.
- [x] coverage did not decrease — `oidc_login` (l.121-260) has ZERO missing lines from this suite
      alone: 100%, above the 95% target. (The file-level 52% is `oidc_callback`, covered by
      `tests/sso_oidc`.)
- [x] no test or contract was altered during build — `git diff --stat` EMPTY across
      `tests/domain_routing_unification`, `tests/sso_oidc`, `tests/oidc_tenant_config`,
      `apps/dashboard/app/api/auth/oidc/`, and `.add/tasks/domain-routing-unification/`. Retargeted
      assertions: **0** — and honestly so: the five pre-existing 404 login assertions already expected
      the collapse target, and the 403 assertion at `sso_oidc:528` is the CALLBACK, which is
      deliberately preserved. The retarget set is empty because the claimed-but-unconfigured leg was
      never tested before, not because anything was loosened.
- [x] the green was EARNED, not gamed — proven EMPIRICALLY, not argued: the verifier built a detached
      worktree at Ground SHA `9421827` (pre-fix router, 403 still at l.173), copied the new suite in,
      ran it on a dedicated DB → **7 failed, 4 passed**. The 7 red are exactly the tests that pin the
      change; the 4 green are by design the M5/M6 regression guards that must pass on both sides.
      This also refutes the main vacuity hypothesis: had `claim_and_verify_domain` silently failed to
      establish a VERIFIED claim, `test_claimed_but_unconfigured` would have taken the unclaimed path
      and passed pre-fix. It FAILED pre-fix, so the seeding is real.
- [x] concurrency / timing of the risky operation is safe — the route is read-only (two SELECTs),
      holds no lock, mutates no shared state, both legs order-independent. Timing remains the §3
      disclosed accepted residual (PK-keyed lookup vs GIN-indexed containment: comparable, not
      equalized) — unchanged by this task, not newly introduced.
- [x] no exposed secrets, injection openings, or unexpected dependencies — the 404 legs set no
      cookies; the 302 legs set the same three cookies via the same helper with the same flags;
      `exc.headers` is `{}` so no differential response header; no new dependency.
- [x] layering & dependencies follow CONVENTIONS.md — raised via an existing `ErrorSpec`, no new
      catalog entry, no raw `ProblemError`; `error_catalog.py` untouched by this task; BFF correctly
      needed no change. Typecheck: `uv run pyright src/gateway/auth/api/oidc_router.py` → 0 errors,
      0 warnings, and it does NOT flag the retained `OIDC_DOMAIN_NOT_MAPPED` import (genuinely
      referenced by the callback).
- [x] a person reviewed and approved the change — **Tin Dang, 2026-07-21.** `autonomy: conservative` +
      `sensitivity: security` ⇒ this gate is a human decision; two independent verifiers proposed PASS
      and neither could record it. Tin was shown the evidence summary (control-flow fall-through is
      real, bodies identical incl. title/cookies/headers in both deployment modes, the Ground-SHA
      revert-proof, zero honest retargets) together with the two open questions below, and called
      **PASS**. He additionally declined to fold the operability logging delta into this task, keeping
      the security diff inside its frozen §5 scope — recorded as a §7 spec delta instead.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract.

- [x] A probe of a claimed-but-unconfigured domain and a probe of an unclaimed domain return responses whose
      `.json()` bodies compare **equal**, not merely same-`code` — confirmed by
      `test_anti_oracle_bodies_identical_env_disabled`. Equality is by CONSTRUCTION, not convention:
      `core/errors.py:32-49` `problem_response` builds `{type, title, status, code}` purely from the
      spec, omits `detail` when None and `extra` when falsy, and `exc.headers` is `{}` — so no
      request-derived field can reach the body or the headers.
- [x] The same equality holds with `oidc_enabled=True` (both 302, same authorize host+path, same
      `oidc_tenant_id` sentinel) — confirmed by `test_anti_oracle_bodies_identical_env_enabled`. The
      cookie VALUE is checked too: the claimed leg pins the `"env-config"` sentinel and never leaks
      tenant A's id.
- [x] `grep -n "OIDC_DOMAIN_NOT_MAPPED" oidc_router.py` shows exactly TWO hits: the import, and the
      single raise inside `oidc_callback` (l.368). Zero hits inside `oidc_login` — AST-verified, not
      grep-guessed.
- [x] `oidc_login` contains exactly ONE `raise` statement (`OIDC_NOT_CONFIGURED.exc()`) — confirmed by
      an end-to-end read of l.184-235 AND by AST. The fall-through is REAL CONTROL FLOW, not a swapped
      constant: the `if oidc_config is None: raise OIDC_DOMAIN_NOT_MAPPED.exc()` guard is GONE;
      `oidc_config` simply stays None and control reaches the shared terminal. Further, the
      `elif resolver is not None:` at l.208 hangs off `if mapped_tenant_id is not None:` (l.189), so a
      claimed domain is STRUCTURALLY unable to reach `resolver.resolve(domain)` at l.214 — M2 is
      preserved by construction, not by comment. The §0 Issues-#2 inverted-oracle trap is closed in
      the only way that closes it.
- [x] A claimed domain whose ONLY config match is another tenant's `email_domains` returns 404, never a
      302 to that tenant's IdP — confirmed by `test_claimed_domain_never_routed_by_foreign_email_domains`
      (one of the 7 that go red at Ground SHA).
- [x] `git diff --stat` touches exactly `oidc_router.py` (docstring + raise-site comment + the
      control-flow deletion) plus the new `apps/gateway/tests/sso_login_oracle_closure/` files. Zero
      `apps/dashboard/**`. `error_catalog.py` IS modified on this tree but its `git diff` contains ZERO
      OIDC lines — the delta is `SIGNUP_CONFIRM_INVALID/EXPIRED` from the sibling
      `scoped-self-serve-signup`. M9/M7 boundaries seen, not assumed.
- [x] `.add/tasks/domain-routing-unification/TASK.md` is byte-unchanged — `git diff --exit-code` clean.
- [x] The three neighbour suites are green, retargeted-assertion count stated explicitly: **0**.
- [x] The `oidc_login` docstring (l.131-170) no longer claims "no oracle" about unclaimed-vs-claimed and
      DOES name both accepted residuals — read in full at the gate.
- [x] Gateway green-bar citation recorded: `pytest (Makefile:test / ci.yml 'Tests' step)`.
- [x] Typecheck clean: `uv run pyright src/gateway/auth/api/oidc_router.py` → 0 errors, 0 warnings. It
      does NOT flag the retained `OIDC_DOMAIN_NOT_MAPPED` import — genuinely referenced by
      `oidc_callback`. No suppression was needed and the import was NOT deleted.

**TITLE-LEVEL ORACLE — the trap §0 flagged, confirmed closed AND asserted.** The sole raise uses
`OIDC_NOT_CONFIGURED` (`error_catalog.py:591`, title "OIDC login is not configured on this platform"),
never the same-code-different-title `OIDC_TENANT_NOT_CONFIGURED` (`:596`, "OIDC configuration not found
or disabled for this tenant"). Called with no detail/headers/extra/`**fmt`. The new tests assert TITLE
explicitly at three sites (test file l.149-151, l.181, l.316) against `OIDC_NOT_CONFIGURED.title_template`
and negatively against both sibling specs — closing exactly the gap where `assert_problem`
(status + code only, never title) would have passed a title-level oracle silently.

**MOVED-ORACLE SWEEP — two extra channels found already closed.** (a)
`DbOidcConfigResolver.resolve_by_tenant_id` (`db_oidc_config_resolver.py:118-158`) fails CLOSED to None
on every path — bad uuid, no row, missing encryption key, Fernet decrypt failure (caught broadly,
logged, not re-raised) — so even a claimed tenant with corrupt ciphertext collapses to the shared
terminal instead of 500ing distinguishably. (b) `scalar_one_or_none()` could raise
`MultipleResultsFound`, but `tenant_id` is the PRIMARY KEY of `oidc_provider_configs` (`orm.py:39-44`),
so at most one row per tenant — structurally impossible. Content-Length is equal because the body is
equal. 302-vs-4xx remains observable exactly as §3 disclosed and Tin accepted; nothing WORSE remains.

### Deep checks — do not skim (fill the path that applies)
- [x] WIRING (code) — the retained `OIDC_DOMAIN_NOT_MAPPED` import is still referenced at
      `oidc_router.py:368`, inside `oidc_callback`'s exception-translate block; confirmed by AST
      within the test itself, not by eye.
- [x] DEAD-CODE (code) — no orphaned symbol; the removed raise left no unreachable branch, proven by
      100% line coverage of `oidc_login`.
- [x] SEMANTIC (prose) — the rewritten `oidc_login` docstring (l.131-170) read IN FULL: it describes
      the shipped behavior, states the collapse as a CONTROL-FLOW property, names the accepted
      302-vs-4xx residual and the timing residual, and records that the 403 is no longer raised here.
      No overclaim — which is itself the defect class this task exists to fix.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
- [x] all §3-cited anchors resolve in the CURRENT tree — `oidc_login` (`oidc_router.py:121`),
      `oidc_callback` (`:264`), `OIDC_NOT_CONFIGURED` (`error_catalog.py:591`),
      `OIDC_DOMAIN_NOT_MAPPED` (`:644`), `OIDC_TENANT_NOT_CONFIGURED` (`:596`),
      `saml_login` (`saml_router.py:62`), `Settings.oidc_enabled` (`config.py:267`),
      `ENV_CONFIG_COOKIE_VALUE` (`settings_oidc_config_resolver.py:26`).
- [x] any anchor that moved/renamed since Ground SHA `9421827` is named here — NONE moved or renamed.

### Refute-read verdict — the earned-green check
Verdict: EARNED
By: agent a7ad391 (independent add-verify, appsec-engineer persona) · adversarially checked:
(a) **can the two collapsed responses be told apart by ANY observable?** Probed status, full `.json()`
body incl. `title`, `Set-Cookie` names AND values, response headers, and Content-Length across BOTH
deployment modes — no distinguisher found; (b) whether the fix is a cosmetic constant swap rather than
control flow — read l.184-235 end to end plus AST, confirmed a genuine structural fall-through with the
claimed leg unable to reach the resolver at all; (c) whether the oracle merely MOVED — swept for
500-vs-4xx channels and found two potential ones both already structurally closed; (d) whether the
green was vacuous seeding — disproved empirically by the Ground-SHA worktree revert-proof
(7 failed / 4 passed pre-fix, and specifically `test_claimed_but_unconfigured` failing pre-fix proves
the VERIFIED claim is genuinely established, via a real DNS-TXT round trip through `FakeDnsResolver`);
(e) whether the zero-retarget claim was honest — `git diff --stat` EMPTY across all four neighbour
paths, no assertion touched, loosened, or deleted.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
Advisor: agent a7ad391
1. Security: CLEAR — no finding. The oracle is closed in both deployment modes on status, full body
   incl. title, cookie names, cookie values, and response headers; two additional 500-shaped
   distinguishers are structurally closed; the callback's post-auth 403 is preserved and out of
   enumeration reach.
2. Concurrency: CLEAR — read-only route (two SELECTs), no lock, no shared mutable state, both legs
   order-independent. Timing is the disclosed accepted residual, not new.
3. Architecture: CLEAR — CONVENTIONS.md honored (existing ErrorSpec, no new catalog entry, no raw
   `ProblemError`), `error_catalog.py` untouched by this task, layering unchanged, BFF correctly
   needed no change.
Verdict: PASS (recommended — a verifier proposes; only Tin records a conservative+security gate)
Residue: none blocking. Three 💭 notes: (1) §4 planned a behavioral `test_callback_403_preserved`; it
shipped as an AST assertion with the behavioral proof delegated to `tests/sso_oidc:528` (green,
untouched) — sound delegation, but the scenario clause "no `ai_proxy_session` cookie is set" is
asserted nowhere in this suite; cheap to add later. (2) §3 says the frozen prose at
`domain_routing_unification:614-627` goes stale — on reading, it does NOT (it describes an earlier CR-v2
narrowing and remains accurate); only `:164-165` is genuinely stale, so the §7 spec delta is narrowed
to those two lines at fold. (3) The suite added one test beyond plan
(`test_no_domain_probe_is_indistinguishable_too`) pinning a THIRD terminal leg the plan's pairwise ≡
would have let drift — good instinct, kept.
Binding: yes — mechanical (sensitivity: security)

### SECOND INDEPENDENT VERIFY — blast-radius / legitimate-user-regression lens
> Project standing rule: a `sensitivity: security` task gets ≥2 INDEPENDENT adversarial verifies.
> Verifier #1 (a7ad391) asked "is the oracle really closed?". Verifier #2 asks the opposite question:
> "what did closing it BREAK, and did the oracle just move somewhere legitimate users pay for?"
Advisor: agent aa44e53 (appsec-engineer persona) · Verdict: **CLEAR / EARNED**, PASS-recommended.

- **M1/M8 cross-tenant routing PRESERVED — the highest-value check.** The claimed leg falls through
  INSIDE `if mapped_tenant_id is not None:`, and its sibling is `elif resolver is not None:` (l.208),
  NOT a bare `if`. A claimed domain is therefore structurally unable to reach `resolver.resolve(domain)`.
  Proven as a MUTATION-KILLER, not just asserted: `test_...:234` seeds tenant A with a verified claim +
  no config while tenant B holds an ENABLED config with `email_domains=[CLAIMED_NO_SSO]` — the exact
  hijack setup — and asserts 404 + `str(tenant_b) not in resp.text` + no `oidc_tenant_id` cookie.
  Flatten the `elif` to `if` and the test fails with a 302 to tenant B's IdP.
- **All four legitimate flows intact** — (a) verified claim + enabled config → 302 with the claim's own
  tenant id; (b) no claim + legacy resolver hit → 302; (c) env-Settings fallback → 302 "env-config";
  (d) no-`?domain=` → 404. The diff removed ONLY the raise + docstring prose; every resolution leg is
  byte-unchanged. Nothing was swallowed into the 404 terminal.
- **The one behavior change probed for privilege escalation and CLEARED:** under `oidc_enabled=True` a
  claimed-but-unconfigured domain now reaches the env IdP instead of 403. This grants NO new reach —
  the env leg is already reachable by any caller sending no `?domain=` at all — and it is dead in
  production. It is precisely the control-flow property M3 requires.
- **Suites:** `95 passed` on the prescribed serial command, reproduced TWICE consecutively (87s, 88s):
  84 neighbours + 11 new.
- ⚠ **Flake attribution, decisively established:** two earlier runs each failed ONE different SAML test
  (`test_saml_login_routes_via_verified_claim`, then `test_clock_skew_boundary_honored`). Both are
  PRE-EXISTING. The proof is a differential, not an isolation re-run: removing the new
  `sso_login_oracle_closure` suite entirely and re-running the SAME command STILL failed. `tests/saml_sso`
  alone is 30/30. An OIDC-only diff cannot touch a SAML clock-window path. → separate flake ticket.
- **`oidc_callback` untouched and correctly left alone** — diff hunks confined to l.131-199; the callback
  starts at l.263; its two raises and the import survive. That path requires an IdP-signed ID token, so
  it is not an unauthenticated enumeration surface.
- **Docstring audited for the OPPOSITE failure (under-claiming) and found accurate on every axis** — it
  correctly QUALIFIES the terminal ("when env OIDC is disabled"; without that qualifier it would be
  false), discloses the 302-vs-4xx residual rather than burying it, explicitly declines to claim timing
  safety ("comparable but not equalized"), and names the `elif` as load-bearing WITH the reason.

**🟡 CONCERN (operability, non-blocking, no security impact):** the claimed-vs-unknown distinction is now
lost from LOGS too, not just the wire — `oidc_login` contains zero logging. An operator previously
separated "verified customer, SSO not yet set up" from "unknown domain" via access-log status codes.
Real cost: a tenant admin mid-OIDC-setup files "SSO says not configured" and support cannot triage from
gateway logs; they must hand-query `tenant_domain_claims` + `oidc_provider_configs`. **Wire-uniformity is
required; log-uniformity is NOT.** → recorded as a spec delta in §7 (structured `oidc_login_unresolved`
log line with `reason=claimed_unconfigured|no_claim|no_domain`). Deliberately NOT folded into this task:
it is additive, it is outside the frozen §5 scope, and doing it here would widen a security task's diff.
**💭 Coverage nicety (not a hole):** the M2 hijack test covers `oidc_enabled=False` only; the `elif` makes
the `True` case structurally safe, so this is optional hardening.

### OPEN QUESTIONS FOR TIN — must be answered before this gate is recorded
1. Confirm `GATEWAY_OIDC_ENABLED` is not set externally in the live deployment. Repo-config evidence
   says FALSE (absent from every chart values file; only `infra/docker-compose.e2e.v4.yml:27` sets it
   true; `Settings.oidc_enabled` defaults False). An externally-injected env var would flip the live
   leg to the 302 branch — which is ALSO verified green, so the invariant holds either way; the
   uncertainty is about WHICH leg is live, not about whether it leaks.
2. Product-copy decision you own, not a defect: the uniform 404 now tells a genuinely misconfigured
   tenant's user "OIDC login is not configured on this platform" instead of the older, more specific
   message. The contract accepts this deliberately — uniformity IS the security property here.
3. The §6 "a person reviewed and approved" checkbox and the GATE RECORD outcome are yours to tick.

### GATE RECORD
Reported: yes — both independent verify reports (a7ad391 oracle-closed lens, aa44e53 blast-radius lens) were rendered to Tin, together with the two open questions, before he called PASS.
Outcome: PASS
component: gateway · expected green-bar: pytest (Makefile:test / ci.yml 'Tests' step) · verify: cd apps/gateway && uv run pytest
Reviewed by: Tin Dang · date: 2026-07-21

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors):
- rate of `404 ERR_OIDC_NOT_CONFIGURED` on `/auth/oidc/login` grouped by source IP — a sudden fan-out across
  many distinct `?domain=` values is the enumeration attempt this task defends against. It is now uninformative
  to the attacker, but it is still the signal that someone is trying, and it argues for rate-limiting the route.
- count of `403 ERR_OIDC_DOMAIN_NOT_MAPPED` originating from `/auth/oidc/login` — must be ZERO post-deploy.
  Any non-zero value means the collapse regressed.
- ratio of 302 to 404 on `/auth/oidc/login` — a sustained shift toward 404 may mean tenants are losing their
  OIDC config, not that the oracle changed.

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: as planned
- [human] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
- [SPEC · open] `domain_routing_unification/test_domain_routing_unification.py` docstrings at `:162-165` and
  `:614-627` narrate "the two contracted fail-closed codes (403 OIDC_DOMAIN_NOT_MAPPED / 404
  OIDC_NOT_CONFIGURED)" for the OIDC login path. After this task there is one code (404). Deliberately NOT
  edited during build (frozen test file — editing even a comment trips `build_tampered`). Fold the correction
  at milestone close. (evidence: §3 Retarget register, Ground SHA `9421827`)
- [SPEC · open] `/auth/oidc/login` and `/auth/saml/login` are unauthenticated and, as far as this grounding
  found, un-rate-limited. The collapse removes the *information* from a domain probe but not the *ability* to
  probe. A per-IP rate limit on both login-init routes is the natural follow-on. (evidence: §0 Issues #1)

- [SPEC · open] **NARROWING of the delta above** — verifier a7ad391 re-read the frozen prose and found
  `:614-627` is NOT stale (it describes an earlier CR-v2 narrowing and remains accurate). Only `:164-165`
  is genuinely stale. Narrow the fold to those two lines; do not rewrite the `:614-627` block.
- [SPEC · open] **Restore operator triage without restoring the wire oracle.** Closing the oracle also
  erased the claimed-vs-unknown distinction from LOGS — `oidc_login` has zero logging, so support can no
  longer separate "verified customer, SSO not yet set up" from "unknown domain" and must hand-query
  `tenant_domain_claims` + `oidc_provider_configs`. Add a structured server-side log line
  (`oidc_login_unresolved`, `reason=claimed_unconfigured|no_claim|no_domain`). Wire-uniformity is the
  security property; log-uniformity is not required. NOT folded into this task: additive, outside the
  frozen §5 scope, and it would widen a security task's diff. (evidence: verifier aa44e53 🟡)
- [SPEC · open] **Catalog invariant test: one `ERR_*` code ⇒ one title.** `OIDC_NOT_CONFIGURED` (:591) and
  `OIDC_TENANT_NOT_CONFIGURED` (:596) share a code but carry different titles, and the shared
  `assert_problem` helper checks status + code ONLY — so a title-level oracle would pass every existing
  assertion in the repo silently. HIGH value: this is a latent bug CLASS, not a one-off.
- [SPEC · open] Behavioral coverage gaps, both cheap and both non-blocking: (a) the scenario clause "no
  `ai_proxy_session` cookie is set" on the callback-403 path is asserted nowhere in this suite (the
  behavioral proof is delegated to `tests/sso_oidc:528`); (b) the M2 cross-tenant hijack test covers
  `oidc_enabled=False` only — the `elif` makes the `True` case structurally safe, so this is hardening.
- [SPEC · new] **Test-infra standing rule.** A shared `:5433` database plus concurrent agents yields
  FK-violation phantom failures that look EXACTLY like cross-task drift — verifier a7ad391 hit 5 such
  failures, and `ps aux` showed 7 concurrent pytest processes drop_all/create_all-ing the same schema.
  Make a per-run dedicated database (or a documented pre-gate check for concurrent pytest processes) the
  standing rule on multi-agent branches.
- [BUG · open] Pre-existing SAML flake pair, NOT caused by this task:
  `test_saml_login_routes_via_verified_claim` and `test_clock_skew_boundary_honored`. Established by
  DIFFERENTIAL, not isolation: removing the new suite entirely and re-running the same command still
  failed. Deserves its own ticket.
- [TDD · folded · persona:appsec-engineer · ability] **"When a behavior change retargets ZERO assertions, [folded foundation-version 55]
  run the new suite against the pre-fix code before believing it."** The Ground-SHA worktree revert-proof
  turned an *argued* non-vacuity claim into *evidence* in ~10 minutes, and it is the only check that could
  have caught vacuous claim-seeding.
- [ADD · folded · persona:appsec-engineer · anti-pattern] **"Treat a prose security claim in shipped code as [folded foundation-version 55]
  an assertion requiring a test."** The false "no oracle" docstring is plausibly the reason nobody
  re-checked this route for months. Highest-value competency delta from this task.
- [SPEC · open] `error_catalog.OIDC_TENANT_NOT_CONFIGURED` and `OIDC_NOT_CONFIGURED` share code
  `ERR_OIDC_NOT_CONFIGURED` with DIFFERENT titles, while `assert_problem` asserts status+code only. Any future
  same-code/different-title pair is an oracle no existing test would catch. Consider a catalog invariant test:
  one code ⇒ one title. (evidence: §0 Issues #4)

### Competency deltas
- [SDD · folded] A frozen contract that permits an ALTERNATION of error codes (`403 | 404`) on an unauthenticated [folded foundation-version 55]
  route silently licenses an enumeration oracle. The alternation reads as flexibility at freeze and as a leak in
  production. Prefer a single contracted terminal code on any unauthenticated discovery surface. (evidence:
  domain-routing-unification §3 M2's `403 | 404` line produced this task)
- [ADD · folded] A docstring authored by a frozen contract asserted a security property ("no oracle between [folded foundation-version 55]
  unclaimed and claimed-but-unconfigured") that the code beneath it never delivered, and it survived a
  security-sensitive review. The overclaim was load-bearing: it is plausibly *why* nobody re-checked. Treat a
  prose security claim in shipped code as an assertion requiring a test, not as documentation. (evidence: §0
  Issues #5; the leg was entirely untested — §3 Retarget register)
- [TDD · folded] The retarget set for this task is EMPTY because the 403 login leg had no test at all. "No test [folded foundation-version 55]
  needs changing" was, here, evidence of a coverage hole rather than of a safe change. When a behavior change
  touches zero assertions, ask why the old behavior was untested before concluding the change is low-risk.
  (evidence: exhaustive grep of both codes across `apps/gateway/tests/`)
- [ADD · folded] No `flow: design` persona in `.add/personas/` covers backend/security design — the three that [folded foundation-version 55]
  exist (accessibility-auditor, ui-designer, ux-researcher) are all UI-facing, while `appsec-engineer` and
  `backend-architect` are `flow: build, advisor`. Security design spans currently fall back to generic. Consider
  adding `flow: design` to `appsec-engineer`, or seeding a design-flow security-architect persona.
  (evidence: `grep -l "flow: design" .add/personas/*.md` at Ground SHA `9421827` returns only
  accessibility-auditor, ui-designer, ux-researcher; appsec-engineer frontmatter reads `flow: build, advisor`)
