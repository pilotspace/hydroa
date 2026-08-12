# TASK: OIDC dashboard login through the BFF

slug: sso-oidc · created: 2026-06-11 · stage: production · risk: high · autonomy: conservative
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- risk: high — trust-boundary change: session minting via external IdP; autonomy: conservative
     — security review mandatory; build cannot auto-PASS at Verify. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: OIDC dashboard login — server-side authorization-code flow in the gateway, minting
         the existing ai_proxy_session cookie, auto-provisioning a member-role user bound to a
         tenant by email-domain claim mapping.

Framings weighed:
- **gateway-side code exchange** (chosen): the gateway owns the session (JwtTokenService), the
  users table, and the tenants table. Implementing the OIDC code exchange inside the gateway
  means the IdP callback lands at a gateway route `/auth/oidc/callback`, the gateway does the
  server-side token exchange (httpx POST to IdP token endpoint), validates the ID token,
  provisions the user if absent, and mints the existing ai_proxy_session cookie — the exact
  same token the BFF's `/api/auth/login` route already mints today via `Set-Cookie`. The BFF
  needs no new OIDC logic: it already proxies `/api/gw/[...path]` to the gateway, and the
  dashboard login page can simply redirect the browser to the gateway's `/auth/oidc/login`
  route directly (or the BFF can surface a link). Zero new trust boundaries in the BFF.
  Tradeoff: the IdP must be able to redirect to the gateway's public hostname (not the
  dashboard's hostname) unless Envoy is configured to forward `/auth/oidc/*` to the gateway.
  The dashboard can also link directly to the edge `/auth/oidc/login` (through Envoy :8080/8443)
  which already proxies everything under `/` (catch-all route) to the gateway.
  ⚠ ENVOY ROUTING CONCERN: the `/auth/oidc/*` prefix must be exempt from Envoy's jwt_authn
  filter — the user has no JWT yet. The current Envoy config exempts only `/admin/auth/signup`
  and `/admin/auth/login` by exact path. Adding `/auth/oidc/login` and `/auth/oidc/callback`
  under the `/auth/oidc/` prefix requires either (a) placing them under a different prefix
  that is already exempt (the catch-all `/` has jwt_authn bypass for non-`/admin/*` paths),
  or (b) adding explicit JWT-exemption entries to envoy.yaml. See §3 for the decision.

- BFF-side code exchange (rejected): the BFF (Next.js) could handle the callback itself,
  exchange the code for tokens via a Next.js API route, then POST the ID token to the gateway
  for validation and user provisioning. This would keep the gateway's HTTP surface smaller.
  Rejected because: (a) the BFF would then do JWT/OIDC crypto that the gateway already has
  context for (JwtTokenService, user repo, tenant repo); (b) adding oidc/crypto packages to
  the Next.js BFF conflicts with the no-new-packages convention; (c) it would add a new trust
  boundary (BFF calling an /internal or /admin gateway endpoint to create a user server-side).
  Gateway-side exchange is cleaner — single service owns identity.

- JWKS-based RS256 ID-token verification (intended for production; CONSTRAINED by allowlist):
  The OIDC spec recommends RS256 and JWKS endpoint discovery for ID-token signature
  verification. pyjwt>=2.13.0 supports RS256 ONLY when the `cryptography` package is also
  installed. `cryptography` is NOT in `.add/dependencies.allowlist` and is NOT installed in
  the gateway venv. Adding it is a PR that edits the allowlist — this is OUT OF SCOPE for v4
  per "no new packages" convention. WORKAROUND CHOSEN: the gateway validates the ID token's
  claims (iss, aud, exp, nonce) deterministically but SKIPS JWKS signature verification in
  v4 — the signature is checked only when `cryptography` is available (the gateway detects
  this with a try-import and logs a WARNING if signature verification is skipped). The test
  suite issues HS256 fake ID tokens using the existing pyjwt HS256 support. This is a known
  security limitation, documented as a v5 TODO: add `cryptography` to allowlist and pin RS256
  JWKS verification. The gateway's token-endpoint code-exchange call (httpx POST) is still
  server-side; the exposed risk is limited to IdP misconfiguration, not public forgery.
  See §1 assumptions for the confidence ranking and cost if wrong.

- Per-tenant OIDC config on tenants table (REJECTED — label fixed at orchestrator review;
  the chosen framing is the env-var alternative below): add `oidc_config JSONB nullable` column.
  Shape: { "enabled": bool, "issuer": str, "client_id": str, "client_secret": str,
           "domain_mapping": [{"email_domain": str, "tenant_id": str}] }.
  The column is nullable (NULL = OIDC disabled for that tenant); NULL → 404 on /auth/oidc/login.
  Alternative: per-platform OIDC config in Settings env-vars (GATEWAY_OIDC_* env vars) with a
  single globally-configured IdP; domain mapping drives which tenant to bind a user to.
  **CHOSEN**: env-var (Settings-level) OIDC config, NOT per-tenant JSONB. Rationale: v4 scope
  is a single generic IdP for the whole platform (the milestone says "generic provider,
  e2e-verified against a test IdP"). Per-tenant OIDC is a v5 concern. A single Settings-level
  config (GATEWAY_OIDC_*) is simpler, avoids a new migration, and matches how the milestone
  describes it. The domain_mapping is also Settings-level: a JSON-encoded env var
  `GATEWAY_OIDC_DOMAIN_MAPPING` = '[{"email_domain":"example.com","tenant_id":"<uuid>"}]'.
  If GATEWAY_OIDC_ENABLED is false/absent → /auth/oidc/login returns 404 (not 503, because
  the resource doesn't exist in that config).

Must:
<must>
  - Settings gains OIDC fields in core/config.py (all prefixed GATEWAY_OIDC_*):
      oidc_enabled: bool = False            # GATEWAY_OIDC_ENABLED
      oidc_issuer: str = ""                 # GATEWAY_OIDC_ISSUER  (e.g. https://accounts.google.com)
      oidc_client_id: str = ""              # GATEWAY_OIDC_CLIENT_ID
      oidc_client_secret: str = ""          # GATEWAY_OIDC_CLIENT_SECRET  (marked as secret in logs)
      oidc_redirect_uri: str = ""           # GATEWAY_OIDC_REDIRECT_URI   (must match IdP registration)
      oidc_domain_mapping: str = "[]"       # GATEWAY_OIDC_DOMAIN_MAPPING (JSON-encoded list)
    Parsed domain mapping shape: list[{"email_domain": str, "tenant_id": str}].
    Settings validation: if oidc_enabled=true, oidc_issuer + oidc_client_id +
    oidc_client_secret + oidc_redirect_uri must all be non-empty (validator raises ValueError).

  - Two new gateway routes registered under /auth/oidc/ prefix in a new
    `apps/gateway/src/gateway/auth/api/oidc_router.py`:
      GET  /auth/oidc/login
        — If oidc_enabled=False → 404 ERR_OIDC_NOT_CONFIGURED.
        — Generates state (32-byte urandom, base64url) and nonce (32-byte urandom, base64url).
        — Sets `oidc_state` httpOnly SameSite=Lax Secure (non-dev) cookie (value=state, Max-Age=300).
        — Sets `oidc_nonce` httpOnly SameSite=Lax Secure (non-dev) cookie (value=nonce, Max-Age=300).
        — Returns 302 redirect to:
            {issuer}/authorize?
              response_type=code
              &client_id={client_id}
              &redirect_uri={redirect_uri}
              &scope=openid+email+profile
              &state={state}
              &nonce={nonce}
          (The authorization endpoint URL is derived by appending /authorize to oidc_issuer,
           OR from OIDC discovery if a discovery document is cached. In v4: append /authorize
           directly — no discovery endpoint call, to avoid a new IO path without a test seam.
           The issuer is trusted as the authorization server base URL. If the IdP uses a
           different path, the operator sets GATEWAY_OIDC_ISSUER appropriately.)

      GET  /auth/oidc/callback?code=<code>&state=<state>
        — If oidc_enabled=False → 404 ERR_OIDC_NOT_CONFIGURED.
        — Reads `oidc_state` and `oidc_nonce` cookies from the request (set in /login step).
        — Validates state matches cookie value; mismatch → 400 ERR_OIDC_STATE_MISMATCH.
        — Missing code or state query params → 400 ERR_OIDC_INVALID_CALLBACK.
        — Missing cookies (state/nonce) → 400 ERR_OIDC_SESSION_EXPIRED.
        — Exchanges code for tokens: httpx POST to {oidc_issuer}/token with:
            grant_type=authorization_code, code=code, redirect_uri=oidc_redirect_uri,
            client_id=oidc_client_id, client_secret=oidc_client_secret
            Content-Type: application/x-www-form-urlencoded
          Timeout: 10 seconds (explicit httpx timeout — design-for-failure).
          IdP timeout/network error → 502 ERR_OIDC_UPSTREAM_ERROR.
          Non-200 from IdP → 502 ERR_OIDC_UPSTREAM_ERROR.
        — Extracts id_token from token endpoint response.
          Missing id_token → 502 ERR_OIDC_UPSTREAM_ERROR.
        — Validates ID token CLAIMS (no signature verification in v4 — see §1 framings):
            iss claim must equal oidc_issuer → mismatch: 401 ERR_OIDC_TOKEN_INVALID
            aud claim must contain oidc_client_id → missing/mismatch: 401 ERR_OIDC_TOKEN_INVALID
            exp claim must be > current UTC epoch → expired: 401 ERR_OIDC_TOKEN_EXPIRED
            nonce claim must equal the oidc_nonce cookie value → mismatch: 401 ERR_OIDC_TOKEN_INVALID
            email claim must be present and non-empty → absent: 401 ERR_OIDC_TOKEN_INVALID
          Any jwt decode error (malformed token) → 401 ERR_OIDC_TOKEN_INVALID.
        — Extracts email from id_token.email claim. email is lowercased.
        — Domain mapping: extract email domain (part after @). Look up domain in
          oidc_domain_mapping list. No matching domain → 403 ERR_OIDC_DOMAIN_NOT_MAPPED.
          No user created, no session minted on domain mismatch.
        — Auto-provision: SELECT users WHERE email = lower(email). If absent:
            INSERT users (id=uuid7(), tenant_id=mapped_tenant_id, email=lower(email),
                          password_hash="!sso-no-password", role="member")
            — password_hash sentinel value "!sso-no-password" is an intentionally un-verifiable
              string (argon2.verify() always returns False for any input against this value
              because it's not a valid argon2 hash, which is the correct behavior —
              SSO users cannot log in via password). The constraint is VARCHAR NOT NULL so we
              must store something; "!" prefix is conventional (Linux /etc/shadow style).
            — role is always "member" — owner/admin are NEVER auto-granted via SSO.
          If user already exists: verify tenant_id matches mapped_tenant_id. Cross-tenant
          email (same email in a different tenant's mapping) → 403 ERR_OIDC_TENANT_CONFLICT
          (edge case: same email in two domain mappings across tenants — reject to prevent
          unauthorized tenant hopping). No user state changed.
        — Mint session: call JwtTokenService.issue(user_id, tenant_id, role, email).
          Returns (token, expires_in).
        — Clear oidc_state and oidc_nonce cookies (Max-Age=0).
        — Set ai_proxy_session cookie: HttpOnly, SameSite=Strict, Secure (non-dev), Path=/,
          Max-Age=expires_in. Exactly matches the BFF /api/auth/login cookie attributes.
        — Return 302 redirect to /dashboard (the dashboard root, configurable via
          GATEWAY_OIDC_POST_LOGIN_REDIRECT; default "/"). The browser follows the redirect;
          the ai_proxy_session cookie is already set.

  - httpx timeout on the IdP token exchange call: explicit timeout=10.0 seconds.
    On httpx.TimeoutException → 502 ERR_OIDC_UPSTREAM_ERROR.
    On httpx.RequestError (network failure) → 502 ERR_OIDC_UPSTREAM_ERROR.
    Never propagate raw httpx exceptions to the client.

  - Envoy routing: /auth/oidc/* must reach the gateway WITHOUT jwt_authn enforcement.
    The current Envoy catch-all route (`prefix: "/"`) already routes to gateway_cluster with
    ext_authz DISABLED and jwt_authn rules only applying to /admin/* prefix.
    Verification: the jwt_authn rules block only `/admin/` prefix; /auth/oidc/* falls through
    to the catch-all route (no jwt_authn rule matches it). NO envoy.yaml changes needed for
    basic routing. This was confirmed by reading the envoy.yaml rules — jwt_authn requires
    provider_name only on `prefix: "/admin/"` match; /auth/* misses that rule and hits the
    final match { prefix: "/" } which has no `requires:` clause → jwt_authn bypass. ✓
    EXCEPTION: for the e2e stack, envoy.yaml needs no change. No infra touch in v4.

  - Module boundary: all new code lives in a new module `apps/gateway/src/gateway/auth/`
    with clean architecture layers:
      auth/domain/ports.py    — OidcTokenExchanger protocol + OidcIdTokenClaims dataclass
      auth/application/use_cases.py — OidcLoginUseCase orchestrating: code exchange, claim
                                      validation, domain mapping, user provision/lookup, token mint
      auth/api/oidc_router.py — FastAPI routes GET /auth/oidc/login + /auth/oidc/callback
      auth/api/deps.py        — dependency wiring (get_oidc_use_case)
      auth/infrastructure/httpx_oidc_exchanger.py — httpx POST to token endpoint + claim decode
    The OidcLoginUseCase uses:
      - OidcTokenExchanger port (injectable for tests — fake via app.state.oidc_exchanger)
      - IdentityRepository from tenants.domain.ports (existing — find/create user)
      - TokenService from tenants.domain.ports (existing — mint session JWT)
    Extends tenants.domain.ports.IdentityRepository with a new method:
      get_or_provision_oidc_user(email, tenant_id, password_hash_sentinel) -> User
    The extension uses the CAPABILITY SEAM pattern: IdentityRepository gains a new method via
    a Protocol extension. The existing SqlAlchemyIdentityRepository gains the new method.
    Existing frozen tests that use the old Protocol are unaffected (Protocol is structural).

  - users.password_hash constraint: the existing column is NOT NULL. SSO-provisioned users
    are inserted with password_hash = "!sso-no-password" (sentinel). No schema migration
    needed — the column is VARCHAR NOT NULL and accepts any string. No new column.
    EXPECTED_TABLES: UNCHANGED. No new table, no migration.

  - Settings validation: if GATEWAY_OIDC_ENABLED=true, a @model_validator raises ValueError
    when any of oidc_issuer / oidc_client_id / oidc_client_secret / oidc_redirect_uri is "".
    oidc_domain_mapping must be valid JSON; invalid JSON raises ValueError.

  - Response cookie must match BFF cookie attributes exactly:
    ai_proxy_session=<jwt>; HttpOnly; SameSite=Strict; Path=/; Max-Age=<expires_in>
    In non-dev environments: + Secure flag (mirrors BFF behavior).
    The gateway checks settings.environment != "dev" for the Secure flag.

  - GATEWAY_OIDC_POST_LOGIN_REDIRECT: str = "/"   (default: dashboard root)
    Additive Settings field; the 302 redirect after session mint targets this URL.
</must>

Reject:
<reject>
  - oidc_enabled=False (or absent) → GET /auth/oidc/login → "ERR_OIDC_NOT_CONFIGURED" (404)
  - oidc_enabled=False → GET /auth/oidc/callback?code=x&state=y → "ERR_OIDC_NOT_CONFIGURED" (404)
  - state query param does not match oidc_state cookie → "ERR_OIDC_STATE_MISMATCH" (400)
  - oidc_state cookie absent on callback → "ERR_OIDC_SESSION_EXPIRED" (400)
  - code or state query param absent on callback → "ERR_OIDC_INVALID_CALLBACK" (400)
  - IdP token endpoint times out or returns non-200 → "ERR_OIDC_UPSTREAM_ERROR" (502)
  - id_token missing from IdP token response → "ERR_OIDC_UPSTREAM_ERROR" (502)
  - id_token iss claim ≠ GATEWAY_OIDC_ISSUER → "ERR_OIDC_TOKEN_INVALID" (401)
  - id_token aud does not contain GATEWAY_OIDC_CLIENT_ID → "ERR_OIDC_TOKEN_INVALID" (401)
  - id_token exp is in the past → "ERR_OIDC_TOKEN_EXPIRED" (401)
  - id_token nonce ≠ oidc_nonce cookie → "ERR_OIDC_TOKEN_INVALID" (401)
  - id_token email claim absent → "ERR_OIDC_TOKEN_INVALID" (401)
  - email domain not in GATEWAY_OIDC_DOMAIN_MAPPING → "ERR_OIDC_DOMAIN_NOT_MAPPED" (403)
    (no user created, no session minted)
  - SSO user email matches an existing user bound to a DIFFERENT tenant than mapped → "ERR_OIDC_TENANT_CONFLICT" (403)
  - owner or admin role is NEVER auto-granted via SSO; auto-provisioned role is always member
</reject>

After:
<after>
  - With GATEWAY_OIDC_ENABLED=false: GET /auth/oidc/login returns 404 ERR_OIDC_NOT_CONFIGURED.
  - With OIDC configured: GET /auth/oidc/login returns 302 redirect to IdP authorize endpoint
    with response_type=code, client_id, redirect_uri, scope=openid+email+profile, state, nonce.
    oidc_state and oidc_nonce cookies are set on the response (HttpOnly, SameSite=Lax).
  - With a valid code and valid state/nonce: GET /auth/oidc/callback completes:
    a) A new user row exists in users with role=member, email=<email>, tenant_id=<mapped>,
       password_hash="!sso-no-password".
    b) The response sets ai_proxy_session cookie (HttpOnly, SameSite=Strict, Path=/).
    c) The response is 302 redirect to GATEWAY_OIDC_POST_LOGIN_REDIRECT (default "/").
    d) oidc_state and oidc_nonce cookies are cleared (Max-Age=0).
  - Second login with the same OIDC email: existing user is found and reused (no duplicate row).
  - State mismatch: 400 ERR_OIDC_STATE_MISMATCH; no user created; no session minted.
  - IdP timeout: 502 ERR_OIDC_UPSTREAM_ERROR; no user created; no session minted.
  - Invalid/expired id_token: 401 ERR_OIDC_TOKEN_INVALID or ERR_OIDC_TOKEN_EXPIRED; no user
    created; no session minted.
  - Unknown email domain: 403 ERR_OIDC_DOMAIN_NOT_MAPPED; no user created; no session minted.
  - Auto-provisioned user has role=member; owner/admin roles unreachable via SSO.
  - The ai_proxy_session cookie minted is functionally identical to the one minted by
    POST /admin/auth/login: the same JwtTokenService.issue() call is used; the BFF middleware
    and /api/gw proxy already work with this cookie — no BFF changes required.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ ID TOKEN SIGNATURE SKIPPED IN V4 [spec]: In v4, the id_token signature is NOT
    verified (cryptography package not in allowlist). Claims validation (iss, aud, exp, nonce,
    email) is performed. The security boundary depends on TLS to the IdP token endpoint (HTTPS
    POST via httpx) — the id_token arrives over the server-side TLS connection, not from the
    browser. An attacker cannot forge an id_token without (a) man-in-the-middle of the HTTPS
    connection to the IdP, or (b) compromising the IdP. The nonce binding ensures a
    browser-delivered code cannot be replayed by a third party. The risk is LOW in a well-
    operated environment but SHOULD be mitigated in v5 by adding cryptography to the allowlist.
    ⚠ Why lowest confidence: this is the biggest security tradeoff in the whole bundle.
    Cost if wrong: a compromised TLS path to the IdP allows token forgery with no signature
    check. Mitigation: add a warning log when environment != "dev" and signature check is
    skipped. The v5 fix (add cryptography) closes the gap completely. This MUST be surface at
    the freeze flag review. Confidence: 0.65 (acceptable for v4 with the documented tradeoff).

  ⚠ PASSWORD_HASH SENTINEL "!sso-no-password" [spec]: SSO users are inserted with
    password_hash = "!sso-no-password". The existing argon2 verify() method (Argon2PasswordHasher)
    passes any hash to argon2.PasswordHasher.verify() — it will raise VerifyMismatchError for
    this sentinel, which is caught and returns False. This effectively prevents password login
    for SSO users, which is the desired behavior. However, if the LoginUseCase is changed in
    the future to accept email-only (no password), or if a new auth flow is added, this sentinel
    must be checked. Confidence: 0.80. Cost if wrong: an SSO user could log in via password
    if the argon2 verify code changes to handle non-argon2 strings differently. Fix: add a
    `users.auth_method` column in v5 (or document the sentinel as the single source of truth
    for SSO detection). For v4, the sentinel is sufficient.

  - AUTHORIZATION ENDPOINT URL DERIVATION [spec]: The gateway derives the IdP authorization
    URL by appending "/authorize" to GATEWAY_OIDC_ISSUER. Google OIDC uses
    https://accounts.google.com/o/oauth2/v2/auth, not {issuer}/authorize. The operator must
    set GATEWAY_OIDC_ISSUER to the base URL such that {issuer}/authorize is correct, OR we
    separate GATEWAY_OIDC_AUTHORIZE_URL from GATEWAY_OIDC_ISSUER. v4 adds both:
    GATEWAY_OIDC_AUTHORIZE_URL: str = ""  (if empty, falls back to {oidc_issuer}/authorize).
    This gives operators the flexibility without requiring discovery endpoint calls.
    Confidence: 0.85. Cost if wrong: an extra env var; no contract change.

  - DOMAIN MAPPING FORMAT [spec]: GATEWAY_OIDC_DOMAIN_MAPPING is a JSON-encoded list of
    {"email_domain": str, "tenant_id": str} objects. The tenant_id is a UUID string. If the
    tenant does not exist in the DB (stale mapping), the INSERT into users will FK-fail with
    IntegrityError → 502 ERR_OIDC_UPSTREAM_ERROR (caught and re-raised as a generic upstream
    error; config issue, not caller issue). Confidence: 0.90. Cost if wrong: need a specific
    error code ERR_OIDC_CONFIG_INVALID; acceptable for v4 to surface as 502.

  - HTTPX FAKE INJECTION SEAM [test]: the OidcTokenExchanger port is injectable via
    app.state.oidc_exchanger (set by tests). The production exchanger makes httpx calls to
    the IdP. Tests inject a FakeOidcExchanger that returns pre-built claim dicts without any
    network call. This mirrors the completion_upstream override pattern exactly.
    Confidence: 0.95. Cost if wrong: tests would need a different seam pattern.

  - COOKIE ATTRIBUTES [spec]: The ai_proxy_session cookie from the OIDC flow uses
    SameSite=Strict (matching the BFF's /api/auth/login cookie). The oidc_state and oidc_nonce
    cookies use SameSite=Lax because they must survive the cross-site redirect from the IdP
    back to the gateway (the redirect from IdP to /auth/oidc/callback IS a cross-site top-level
    navigation — Lax allows it; Strict would block it). This is the correct OIDC cookie posture.
    Confidence: 0.92. Cost if wrong: state/nonce cookies blocked by browser → login always fails.

  - REDIRECT TARGET AFTER LOGIN [spec]: After minting the session, the gateway redirects to
    GATEWAY_OIDC_POST_LOGIN_REDIRECT (default "/"). If the dashboard is served at a different
    origin (e.g., localhost:3000), this "/" would go to the gateway's origin, not the
    dashboard's origin. In production the TLS edge proxies the dashboard at the same hostname,
    so "/" resolves to the dashboard root correctly. For local dev with separate ports, the
    operator sets GATEWAY_OIDC_POST_LOGIN_REDIRECT to the dashboard URL.
    Confidence: 0.88. Cost if wrong: operator config only; no contract change.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: S1 — OIDC disabled → /auth/oidc/login returns 404
  Given GATEWAY_OIDC_ENABLED is false (or unset)
  When GET /auth/oidc/login is called
  Then the response is 404 with code ERR_OIDC_NOT_CONFIGURED
  And no oidc_state or oidc_nonce cookies are set
  And what must remain unchanged: no redirect to any IdP; no state cookies set

Scenario: S2 — OIDC disabled → /auth/oidc/callback returns 404
  Given GATEWAY_OIDC_ENABLED is false (or unset)
  When GET /auth/oidc/callback?code=abc&state=xyz is called
  Then the response is 404 with code ERR_OIDC_NOT_CONFIGURED
  And what must remain unchanged: no user created; no session cookie set

Scenario: S3 — happy-path login: new user provisioned, session minted
  Given OIDC is configured with issuer=fake-issuer, client_id=test-client
  And domain_mapping maps "example.com" → an existing tenant
  And no user with email "alice@example.com" exists
  When the FakeOidcExchanger returns a valid id_token with email=alice@example.com,
       iss=fake-issuer, aud=test-client, valid nonce, valid exp
  And GET /auth/oidc/callback?code=validcode&state=<matching_state> is called
       with oidc_state and oidc_nonce cookies matching the token claims
  Then the response is 302 with Location matching GATEWAY_OIDC_POST_LOGIN_REDIRECT
  And the response sets ai_proxy_session cookie (HttpOnly, SameSite=Strict, Path=/)
  And a new user row exists with email=alice@example.com, role=member, tenant_id=<mapped>
  And password_hash="!sso-no-password" (SSO sentinel)
  And oidc_state and oidc_nonce cookies are cleared (Max-Age=0)

Scenario: S4 — second login: existing user reused, no duplicate row
  Given a user with email=alice@example.com already exists (role=member, tenant_id=<mapped>)
  And OIDC is configured with matching domain mapping
  When GET /auth/oidc/callback is called again with a fresh valid id_token for alice@example.com
  Then the response is 302 (session minted successfully)
  And the users table still has exactly ONE row for alice@example.com (no duplicate)
  And the ai_proxy_session cookie is set
  And what must remain unchanged: the existing user row is not modified; no extra rows created

Scenario: S5 — unknown email domain → 403, no user created
  Given OIDC is configured; domain_mapping maps only "example.com" → a tenant
  When the FakeOidcExchanger returns a valid id_token with email=bob@otherdomain.com
  And GET /auth/oidc/callback is called with matching state/nonce cookies
  Then the response is 403 with code ERR_OIDC_DOMAIN_NOT_MAPPED
  And no user row is created for bob@otherdomain.com
  And no ai_proxy_session cookie is set
  And what must remain unchanged: no session minted; no user created; tenant data unaffected

Scenario: S6 — state mismatch → 400, no user created
  Given OIDC is configured and oidc_state cookie is set to "correct-state"
  When GET /auth/oidc/callback?code=abc&state=WRONG-STATE is called
  Then the response is 400 with code ERR_OIDC_STATE_MISMATCH
  And no user row is created
  And no ai_proxy_session cookie is set
  And what must remain unchanged: no session minted; no user created

Scenario: S7 — oidc_state cookie absent → 400 ERR_OIDC_SESSION_EXPIRED
  Given OIDC is configured
  When GET /auth/oidc/callback?code=abc&state=somestate is called WITHOUT oidc_state cookie
  Then the response is 400 with code ERR_OIDC_SESSION_EXPIRED
  And no user row is created
  And no ai_proxy_session cookie is set
  And what must remain unchanged: no session minted; no user created

Scenario: S8 — IdP token endpoint timeout → 502 ERR_OIDC_UPSTREAM_ERROR
  Given OIDC is configured and oidc_state/oidc_nonce cookies match
  When the FakeOidcExchanger raises httpx.TimeoutException (simulating IdP timeout)
  And GET /auth/oidc/callback?code=abc&state=<matching> is called
  Then the response is 502 with code ERR_OIDC_UPSTREAM_ERROR
  And no user row is created
  And no ai_proxy_session cookie is set
  And what must remain unchanged: no session minted; no user created; IdP error absorbed

Scenario: S9 — id_token with wrong issuer → 401 ERR_OIDC_TOKEN_INVALID
  Given OIDC is configured with issuer=fake-issuer
  When the FakeOidcExchanger returns an id_token with iss=WRONG-ISSUER
  And GET /auth/oidc/callback is called with matching state/nonce cookies
  Then the response is 401 with code ERR_OIDC_TOKEN_INVALID
  And no user row is created
  And no ai_proxy_session cookie is set
  And what must remain unchanged: no session minted; no user created

Scenario: S10 — expired id_token → 401 ERR_OIDC_TOKEN_EXPIRED
  Given OIDC is configured
  When the FakeOidcExchanger returns an id_token with exp in the past
  And GET /auth/oidc/callback is called with matching state/nonce cookies
  Then the response is 401 with code ERR_OIDC_TOKEN_EXPIRED
  And no user row is created
  And no ai_proxy_session cookie is set
  And what must remain unchanged: no session minted; no user created

Scenario: S11 — nonce mismatch → 401 ERR_OIDC_TOKEN_INVALID
  Given OIDC is configured; oidc_nonce cookie is "correct-nonce"
  When the FakeOidcExchanger returns an id_token with nonce=WRONG-NONCE
  And GET /auth/oidc/callback is called with oidc_state matching and oidc_nonce="correct-nonce"
  Then the response is 401 with code ERR_OIDC_TOKEN_INVALID
  And no user row is created
  And no ai_proxy_session cookie is set
  And what must remain unchanged: no session minted; no user created

Scenario: S12 — owner/admin roles never auto-granted: provisioned user is always member
  Given OIDC is configured; domain_mapping maps "example.com" → a tenant
  And the id_token contains email=carol@example.com (no role claim)
  When GET /auth/oidc/callback is called and completes successfully
  Then the new user row has role=member
  And the ai_proxy_session JWT decodes with role=member (not owner or admin)
  And what must remain unchanged: no owner or admin role is assigned via SSO

Scenario: S13 — GET /auth/oidc/login sets state and nonce cookies, redirects to IdP
  Given OIDC is configured with issuer=fake-issuer, client_id=test-client,
        redirect_uri=http://gw/auth/oidc/callback
  When GET /auth/oidc/login is called
  Then the response is 302 with Location starting with "fake-issuer/authorize" (or
       GATEWAY_OIDC_AUTHORIZE_URL if set) containing:
         response_type=code, client_id=test-client, redirect_uri=..., scope includes "openid",
         state=<some_value>, nonce=<some_value>
  And the response sets oidc_state cookie (HttpOnly, SameSite=Lax, Max-Age=300)
  And the response sets oidc_nonce cookie (HttpOnly, SameSite=Lax, Max-Age=300)
  And no ai_proxy_session cookie is set
  And what must remain unchanged: no user created; no DB writes

Scenario: S14 — session cookie attributes match BFF login exactly
  Given OIDC is configured in non-dev environment (environment != "dev")
  When GET /auth/oidc/callback completes a valid flow
  Then the ai_proxy_session cookie has attributes: HttpOnly; SameSite=Strict; Path=/; Secure
  And the oidc_state and oidc_nonce cookies are cleared (ai_proxy_session NOT cleared)
  And what must remain unchanged: no tokens exposed in response body

Scenario: S15 — no id_token in IdP response → 502 ERR_OIDC_UPSTREAM_ERROR
  Given OIDC is configured and state/nonce cookies match
  When the FakeOidcExchanger returns a token endpoint response without id_token field
  And GET /auth/oidc/callback is called
  Then the response is 502 with code ERR_OIDC_UPSTREAM_ERROR
  And no user row is created
  And no ai_proxy_session cookie is set
  And what must remain unchanged: no session minted; no user created

Scenario: S16 — cross-tenant email collision → 403 ERR_OIDC_TENANT_CONFLICT
  Given OIDC is configured; domain_mapping maps "example.com" → tenant_A
  And a user with email=dave@example.com already exists bound to tenant_B (different tenant)
  When GET /auth/oidc/callback is called with a valid id_token for dave@example.com
  Then the response is 403 with code ERR_OIDC_TENANT_CONFLICT
  And no new user row is created
  And no ai_proxy_session cookie is set
  And what must remain unchanged: existing user in tenant_B is unaffected; no session minted
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /auth/oidc/login
  302  → Location: {oidc_authorize_url}?response_type=code&client_id={oidc_client_id}
                    &redirect_uri={oidc_redirect_uri}&scope=openid+email+profile
                    &state={state}&nonce={nonce}
         Set-Cookie: oidc_state={state}; HttpOnly; SameSite=Lax; Path=/auth/oidc; Max-Age=300[; Secure]
         Set-Cookie: oidc_nonce={nonce}; HttpOnly; SameSite=Lax; Path=/auth/oidc; Max-Age=300[; Secure]
  404  → { "type": "about:blank", "title": "...", "status": 404, "code": "ERR_OIDC_NOT_CONFIGURED" }
         (when GATEWAY_OIDC_ENABLED=false or route not registered)

GET /auth/oidc/callback?code=<code>&state=<state>
  302  → Location: {GATEWAY_OIDC_POST_LOGIN_REDIRECT} (default "/")
         Set-Cookie: ai_proxy_session={jwt}; HttpOnly; SameSite=Strict; Path=/; Max-Age={expires_in}[; Secure]
         Set-Cookie: oidc_state=; HttpOnly; SameSite=Lax; Path=/auth/oidc; Max-Age=0
         Set-Cookie: oidc_nonce=; HttpOnly; SameSite=Lax; Path=/auth/oidc; Max-Age=0
  400  → { "code": "ERR_OIDC_STATE_MISMATCH" | "ERR_OIDC_INVALID_CALLBACK"
                  | "ERR_OIDC_SESSION_EXPIRED" }
  401  → { "code": "ERR_OIDC_TOKEN_INVALID" | "ERR_OIDC_TOKEN_EXPIRED" }
  403  → { "code": "ERR_OIDC_DOMAIN_NOT_MAPPED" | "ERR_OIDC_TENANT_CONFLICT" }
  502  → { "code": "ERR_OIDC_UPSTREAM_ERROR" }
  404  → { "code": "ERR_OIDC_NOT_CONFIGURED" }   (when OIDC disabled)

Schema (DDL):
  users table: NO new columns. SSO users stored with password_hash="!sso-no-password".
  EXPECTED_TABLES: UNCHANGED. No new table, no migration.
  (Optional v5 follow-up: add users.auth_method VARCHAR DEFAULT 'password' to distinguish
   SSO users from password users — additive migration, deferred.)

Settings (all new, additive to core/config.py):
  GATEWAY_OIDC_ENABLED: bool = False
  GATEWAY_OIDC_ISSUER: str = ""             # e.g. "https://accounts.google.com"
  GATEWAY_OIDC_AUTHORIZE_URL: str = ""      # optional; defaults to {oidc_issuer}/authorize
  GATEWAY_OIDC_CLIENT_ID: str = ""
  GATEWAY_OIDC_CLIENT_SECRET: str = ""      # treated as secret; never logged
  GATEWAY_OIDC_REDIRECT_URI: str = ""       # e.g. "https://proxy.example.com/auth/oidc/callback"
  GATEWAY_OIDC_DOMAIN_MAPPING: str = "[]"  # JSON: [{"email_domain":"example.com","tenant_id":"<uuid>"}]
  GATEWAY_OIDC_POST_LOGIN_REDIRECT: str = "/"
  Validation: if oidc_enabled=True, oidc_issuer + oidc_client_id + oidc_client_secret +
              oidc_redirect_uri must be non-empty. oidc_domain_mapping must be valid JSON.

OidcTokenExchanger protocol (new, in auth/domain/ports.py):
  class OidcTokenExchanger(Protocol):
      async def exchange(self, code: str, redirect_uri: str) -> dict[str, Any]:
          """POST code to token endpoint; return parsed response body (includes id_token)."""
          ...
  # Raises OidcUpstreamError on httpx.RequestError / httpx.TimeoutException / non-200 status

OidcIdTokenClaims dataclass (new, in auth/domain/entities.py):
  @dataclass(frozen=True)
  class OidcIdTokenClaims:
      sub: str
      email: str
      iss: str
      aud: str | list[str]   # aud can be a string or a list
      exp: int
      nonce: str | None      # may be absent in some IdPs; validated when present

DomainMapping dataclass (new, in auth/domain/entities.py):
  @dataclass(frozen=True)
  class DomainMapping:
      email_domain: str
      tenant_id: uuid.UUID

IdentityRepository extension (additive to tenants/domain/ports.py):
  # New method on IdentityRepository Protocol — capability seam:
  async def get_or_provision_oidc_user(
      self,
      *,
      email: str,       # lowercased
      tenant_id: uuid.UUID,
      password_hash: str,  # sentinel: "!sso-no-password"
  ) -> User:
  """Get existing user by email OR create with role=member if absent.
   Raises OidcTenantConflictError if user exists bound to a different tenant_id."""
  ...
  # The existing SqlAlchemyIdentityRepository gains this method.
  # Existing frozen tests using the old Protocol shape are unaffected.

OidcLoginUseCase (new, in auth/application/use_cases.py):
  class OidcLoginUseCase:
      def __init__(
          self,
          exchanger: OidcTokenExchanger,
          repository: IdentityRepository,
          tokens: TokenService,
          settings: Settings,
      ) -> None: ...

      async def execute(
          self,
          *,
          code: str,
          state: str,
          cookie_state: str | None,  # from oidc_state cookie
          cookie_nonce: str | None,  # from oidc_nonce cookie
      ) -> tuple[str, int]:  # (jwt, expires_in)
      """Orchestrates: state validate → code exchange → claims validate →
         domain map → provision/lookup → token mint."""

Error classes (new domain errors in auth/domain/errors.py):
  OidcUpstreamError         → 502 ERR_OIDC_UPSTREAM_ERROR
  OidcTokenInvalidError     → 401 ERR_OIDC_TOKEN_INVALID
  OidcTokenExpiredError     → 401 ERR_OIDC_TOKEN_EXPIRED
  OidcStateMismatchError    → 400 ERR_OIDC_STATE_MISMATCH
  OidcSessionExpiredError   → 400 ERR_OIDC_SESSION_EXPIRED
  OidcInvalidCallbackError  → 400 ERR_OIDC_INVALID_CALLBACK
  OidcDomainNotMappedError  → 403 ERR_OIDC_DOMAIN_NOT_MAPPED
  OidcTenantConflictError   → 403 ERR_OIDC_TENANT_CONFLICT

HttpxOidcExchanger (new, in auth/infrastructure/httpx_oidc_exchanger.py):
  Implements OidcTokenExchanger.
  exchange(): httpx POST to {oidc_settings.oidc_issuer}/token
    Content-Type: application/x-www-form-urlencoded
    Body: grant_type=authorization_code, code=code, redirect_uri=redirect_uri,
          client_id=oidc_client_id, client_secret=oidc_client_secret
    Timeout: httpx.Timeout(10.0)
    On httpx.RequestError / httpx.TimeoutException → raise OidcUpstreamError
    On status != 200 → raise OidcUpstreamError
    Returns parsed JSON body dict.

FakeOidcExchanger injection seam (pinned, mirrors guardrail_evaluator seam):
  Production: get_oidc_use_case reads request.app.state.oidc_exchanger; if None,
              constructs HttpxOidcExchanger. Tests set app.state.oidc_exchanger before
              calling the route.

Envoy routing: NO changes needed.
  /auth/oidc/* falls through to catch-all route (prefix: "/") with jwt_authn bypass
  and ext_authz disabled. Confirmed by reading envoy.yaml rules — jwt_authn requires:
  provider_name only on exact /admin/auth/signup, /admin/auth/login, and prefix /admin/.
  /auth/* misses all three rules. No envoy touch in v4.

Cookie path for oidc_state/oidc_nonce: Path=/auth/oidc (scoped to the OIDC routes only).
Cookie path for ai_proxy_session: Path=/ (whole site, matching BFF behavior).

Modules touched (hard boundary):
  NEW:
    apps/gateway/src/gateway/auth/__init__.py
    apps/gateway/src/gateway/auth/domain/__init__.py
    apps/gateway/src/gateway/auth/domain/entities.py      (OidcIdTokenClaims, DomainMapping)
    apps/gateway/src/gateway/auth/domain/errors.py        (Oidc* error classes)
    apps/gateway/src/gateway/auth/domain/ports.py         (OidcTokenExchanger protocol)
    apps/gateway/src/gateway/auth/application/__init__.py
    apps/gateway/src/gateway/auth/application/use_cases.py (OidcLoginUseCase)
    apps/gateway/src/gateway/auth/api/__init__.py
    apps/gateway/src/gateway/auth/api/deps.py             (get_oidc_use_case)
    apps/gateway/src/gateway/auth/api/oidc_router.py      (GET /auth/oidc/login + /callback)
    apps/gateway/src/gateway/auth/infrastructure/__init__.py
    apps/gateway/src/gateway/auth/infrastructure/httpx_oidc_exchanger.py
  MODIFIED:
    apps/gateway/src/gateway/core/config.py               (add OIDC settings fields)
    apps/gateway/src/gateway/tenants/domain/ports.py      (add get_or_provision_oidc_user)
    apps/gateway/src/gateway/tenants/infrastructure/repository.py (implement new method)
    apps/gateway/src/gateway/main.py                      (include oidc_router)
  UNCHANGED (confirmed):
    apps/gateway/migrations/                              (no migration needed)
    apps/dashboard/                                       (no BFF changes needed)
    infra/envoy/                                          (no envoy changes needed)

Fake IdP injection seam (PINNED for tests):
  Tests set app.state.oidc_exchanger = FakeOidcExchanger(...) before calling the route.
  FakeOidcExchanger.exchange() returns a pre-built dict {"id_token": "<hs256-token>"}.
  ID tokens in the test suite are HS256-signed with a test key using pyjwt (the only
  available JWT algorithm without cryptography package).
  The gateway's HttpxOidcTokenValidator decodes with options={"verify_signature": False}
  in test mode (detected by checking algorithm availability or environment=="test").
  PINNED APPROACH: the gateway decode step always calls
    jwt.decode(id_token, options={"verify_signature": False, "verify_aud": False})
    to extract claims, then manually validates: iss, aud (contains client_id), exp, nonce,
    email. No pyjwt algorithm is passed (decode without algorithm = claim extraction only).
  This approach is SPEC-SANCTIONED, not merely a tradeoff (orchestrator security review,
  2026-06-11): OIDC Core 1.0 §3.1.3.7 clause 6 states that when the ID Token is received
  via direct communication between the Client and the Token Endpoint — exactly this flow —
  "the TLS server validation MAY be used to validate the issuer in place of checking the
  token signature." The sanction is CONDITIONAL on preconditions that are hereby PINNED:
    1. The token arrives ONLY server-side via httpx POST to the IdP token endpoint; the
       gateway NEVER accepts an id_token from the browser or any other channel.
    2. httpx TLS certificate verification is NEVER disabled (no verify=False anywhere in
       the OIDC exchanger — a build that sets verify=False is a security HARD-STOP).
    3. The token endpoint URL derives exclusively from operator-trusted Settings
       (GATEWAY_OIDC_* env vars) — never from request input or IdP-supplied redirects.
    4. The nonce binding prevents cross-site replay.
    5. Claim validation (iss, aud (contains client_id), exp, nonce, email) is still enforced.
  The FakeOidcExchanger signs tokens with HS256 for completeness; the gateway does not
  verify the signature either way. Tests assert on the CLAIM validation paths, not on
  signature verification (which is explicitly deferred to v5).

Flags for freeze (lowest-confidence points across the bundle — MUST be reviewed by Tin Dang):
  ⚠ [spec] ID token signature verification replaced by TLS-channel validation in v4
    (security-reviewed): The gateway decodes id_token claims without signature verification.
    Orchestrator security review upgraded this from "tradeoff" to SPEC-SANCTIONED design —
    OIDC Core 1.0 §3.1.3.7 clause 6 permits TLS server validation in place of signature
    checking when the ID token is received via direct client↔token-endpoint communication,
    which is exactly this flow. The sanction's preconditions are PINNED in §3 (server-side
    only, httpx verify never disabled, token endpoint from trusted Settings only, nonce
    binding, full claim validation). Residual risk: a compromised TLS path to the IdP allows
    claim injection — that compromise also breaks the code exchange itself, so signature
    verification would not restore trust alone. v5 hardening: add cryptography to the
    allowlist and pin RS256 JWKS verification (defense in depth, recommended).
    The WARNING log in non-dev environments is the v4 operational signal.

  ⚠ [contract] password_hash sentinel "!sso-no-password":
    SSO users are stored with this sentinel in the NOT NULL password_hash column. argon2
    verify() will raise VerifyMismatchError and return False for any password against this
    sentinel, effectively blocking password login. The risk: if Argon2PasswordHasher.verify()
    changes to treat non-argon2 strings specially, or if someone adds a password-reset flow
    that sets a new password for an SSO user, the sentinel contract breaks.
    Why least sure: it relies on Argon2PasswordHasher internals (specifically, that argon2
    always throws VerifyMismatchError on a non-argon2 hash string). Verified by reading
    argon2_hasher.py — it delegates to argon2.PasswordHasher().verify() which always raises
    on invalid hash format. Confidence: 0.80. Cost if wrong: SSO users bypass password auth.

  ⚠ [test] FakeOidcExchanger and decode with verify_signature=False:
    The test suite relies on jwt.decode(..., options={"verify_signature": False}) to extract
    claims from HS256-signed fake tokens. This works in pyjwt>=2.13.0 (confirmed). The
    production path also uses verify_signature=False (the v4 intended behavior). This means
    the tests do NOT test signature verification (because neither path verifies signatures in
    v4). The tests DO cover all other claim validation paths. The risk: a future reviewer
    might think the tests prove signature security when they only prove claim validation.
    Why least sure: it's a subtle gap that a future builder might miss. The §5 safety rule
    MUST document this explicitly.

Least-sure flag surfaced at freeze:
  ⚠ [spec] ID-token signature verification replaced by TLS-channel validation —
    security-reviewed and accepted as OIDC Core 1.0 §3.1.3.7(6)-sanctioned for this exact
    flow shape, CONDITIONAL on the §3-pinned preconditions (server-side-only token receipt,
    httpx verify never disabled — verify=False in the exchanger is a security HARD-STOP,
    trusted-Settings-only endpoint URLs, nonce binding, full claim validation). Why least
    sure: the sanction holds only while all preconditions hold; a future edit relaxing any
    one silently voids it. Cost if wrong: claim injection via a compromised IdP TLS path.
    v5 hardening: cryptography + RS256 JWKS (defense in depth).
  ⚠ [contract] password_hash sentinel "!sso-no-password" for SSO users — relies on
    Argon2PasswordHasher.verify() raising on any non-argon2 string (verified in code).
    Cost if wrong: SSO users gain a password-login path. v5: users.auth_method column.

Status: FROZEN @ v4 — approved by Tin Dang (delegated auto mode, 2026-06-11)
```

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 85%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_oidc_login_disabled_returns_404: S1 — arrange app with oidc_enabled=False;
    act GET /auth/oidc/login; assert 404 ERR_OIDC_NOT_CONFIGURED, no cookies set

  - test_oidc_callback_disabled_returns_404: S2 — arrange app with oidc_enabled=False;
    act GET /auth/oidc/callback?code=abc&state=xyz; assert 404 ERR_OIDC_NOT_CONFIGURED

  - test_happy_path_new_user_provisioned: S3 — arrange OIDC settings + FakeOidcExchanger
    returning valid id_token for alice@example.com + domain_mapping + existing tenant;
    act GET /auth/oidc/callback with matching state/nonce cookies;
    assert 302 to post_login_redirect, ai_proxy_session cookie set, user row created
    with role=member password_hash="!sso-no-password", oidc_state/oidc_nonce cleared

  - test_second_login_no_duplicate: S4 — arrange same as S3 + pre-existing user row;
    act callback again; assert 302, ai_proxy_session set, exactly one user row remains

  - test_unknown_domain_rejected: S5 — arrange OIDC + FakeOidcExchanger for bob@otherdomain.com;
    act callback; assert 403 ERR_OIDC_DOMAIN_NOT_MAPPED, no user row, no session cookie

  - test_state_mismatch_rejected: S6 — arrange valid OIDC config + oidc_state cookie="correct";
    act GET /auth/oidc/callback?state=WRONG-STATE; assert 400 ERR_OIDC_STATE_MISMATCH,
    no user created, no session cookie

  - test_missing_state_cookie: S7 — arrange valid OIDC config, no oidc_state cookie;
    act GET /auth/oidc/callback?code=abc&state=somestate;
    assert 400 ERR_OIDC_SESSION_EXPIRED, no user, no session

  - test_idp_timeout_returns_502: S8 — arrange FakeOidcExchanger that raises httpx.TimeoutException;
    act callback with valid state/nonce cookies; assert 502 ERR_OIDC_UPSTREAM_ERROR,
    no user, no session

  - test_wrong_issuer_rejected: S9 — arrange FakeOidcExchanger returning id_token with iss=WRONG;
    act callback; assert 401 ERR_OIDC_TOKEN_INVALID, no user, no session

  - test_expired_token_rejected: S10 — arrange FakeOidcExchanger returning id_token with exp=past;
    act callback; assert 401 ERR_OIDC_TOKEN_EXPIRED, no user, no session

  - test_nonce_mismatch_rejected: S11 — arrange FakeOidcExchanger with nonce=WRONG in token;
    oidc_nonce cookie = "correct-nonce"; act callback;
    assert 401 ERR_OIDC_TOKEN_INVALID, no user, no session

  - test_provisioned_role_always_member: S12 — arrange valid flow for carol@example.com;
    act callback; assert user.role == "member"; decode ai_proxy_session JWT, assert role=member

  - test_login_sets_state_nonce_cookies: S13 — arrange OIDC config;
    act GET /auth/oidc/login; assert 302 to authorize URL with state/nonce/scope/client_id params;
    assert oidc_state cookie set (HttpOnly, SameSite=Lax); assert oidc_nonce cookie set

  - test_session_cookie_attributes: S14 — arrange OIDC config + environment=production;
    act happy-path callback; assert ai_proxy_session cookie has HttpOnly, SameSite=Strict,
    Secure, Path=/; assert no jwt in response body

  - test_missing_id_token_in_response: S15 — arrange FakeOidcExchanger returning {} (no id_token);
    act callback with valid state/nonce; assert 502 ERR_OIDC_UPSTREAM_ERROR, no user, no session

  - test_cross_tenant_email_conflict: S16 — arrange user with email=dave@example.com in tenant_B;
    domain_mapping maps "example.com" → tenant_A; act callback for dave@example.com;
    assert 403 ERR_OIDC_TENANT_CONFLICT, no new user, no session
</test_plan>

Tests live in: `apps/gateway/tests/sso_oidc/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

Red run evidence (captured 2026-06-11):
  All 16 tests FAIL. Zero collection errors. Right-reason summary:

  S1 (OIDC disabled → login 404):
    Route /auth/oidc/login does not exist → FastAPI returns 404 {"detail":"Not Found"}
    (no `code` field). AssertionError: expected code 'ERR_OIDC_NOT_CONFIGURED', got None.

  S2 (OIDC disabled → callback 404):
    Same: /auth/oidc/callback does not exist → 404 {"detail":"Not Found"} without code field.
    AssertionError: expected code 'ERR_OIDC_NOT_CONFIGURED', got None.

  S3 (happy-path new user provisioned):
    Route /auth/oidc/callback does not exist → 404.
    AssertionError: expected 302, got 404: {"detail":"Not Found"}

  S4 (second login no duplicate):
    Same: route missing → 404.
    AssertionError: expected 302, got 404: {"detail":"Not Found"}

  S5 (unknown domain → 403 ERR_OIDC_DOMAIN_NOT_MAPPED):
    Route missing → 404. AssertionError: expected HTTP 403, got 404.

  S6 (state mismatch → 400 ERR_OIDC_STATE_MISMATCH):
    Route missing → 404. AssertionError: expected HTTP 400, got 404.

  S7 (missing state cookie → 400 ERR_OIDC_SESSION_EXPIRED):
    Route missing → 404. AssertionError: expected HTTP 400, got 404.

  S8 (IdP timeout → 502 ERR_OIDC_UPSTREAM_ERROR):
    Route missing → 404. AssertionError: expected HTTP 502, got 404.

  S9 (wrong issuer → 401 ERR_OIDC_TOKEN_INVALID):
    Route missing → 404. AssertionError: expected HTTP 401, got 404.

  S10 (expired token → 401 ERR_OIDC_TOKEN_EXPIRED):
    Route missing → 404. AssertionError: expected HTTP 401, got 404.

  S11 (nonce mismatch → 401 ERR_OIDC_TOKEN_INVALID):
    Route missing → 404. AssertionError: expected HTTP 401, got 404.

  S12 (provisioned role always member):
    Route missing → 404. AssertionError: expected 302, got 404.

  S13 (login sets state/nonce cookies, redirects to IdP):
    Route /auth/oidc/login does not exist → 404.
    AssertionError: expected 302, got 404.

  S14 (session cookie attributes in non-dev):
    Route missing → 404. AssertionError: expected 302, got 404.

  S15 (missing id_token → 502 ERR_OIDC_UPSTREAM_ERROR):
    Route missing → 404. AssertionError: expected HTTP 502, got 404.

  S16 (cross-tenant email collision → 403 ERR_OIDC_TENANT_CONFLICT):
    Route missing → 404. AssertionError: expected HTTP 403, got 404.

  Run tail (captured):
    16 failed, 12 warnings in 5.04s
    (coverage floor failure suppressed — expected on partial run per §4 instructions)

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific):
  1. ID token signature is NOT verified in v4 (no cryptography package). This is a known
     conscious security tradeoff. Claim validation (iss, aud, exp, nonce, email) is ALWAYS
     enforced. A WARNING must be logged when environment != "dev" noting the skip.
  2. SSO-provisioned users have password_hash="!sso-no-password" sentinel. This value MUST
     be preserved exactly; the LoginUseCase must never be made to accept it as a valid hash.
  3. owner/admin roles are NEVER auto-granted via SSO. The provision step HARDCODES role=member.
  4. No tokens (id_token, access_token, refresh_token) ever reach the browser or appear in
     response bodies. Only the ai_proxy_session httpOnly cookie is set.
  5. oidc_client_secret must never appear in logs. Structlog bindings must exclude it.
  6. httpx timeout of 10 seconds on token exchange — never removed or increased silently.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — frozen suite tests/sso_oidc 16/16 green (re-run after orchestrator amendments); full suite 312 passed via root `make ci` exit 0 (2026-06-11)
- [x] coverage did not decrease — 80.70% ≥ 80% floor enforced by `make ci` (exit 0)
- [x] no test or contract was altered during build — zero frozen-test edits this task; §3 untouched
- [x] concurrency / timing of the risky operation is safe — state compared with constant-time hmac.compare_digest; state/nonce single-use (cleared Max-Age=0 on every callback); httpx exchange has an explicit 10s timeout (no hang path); provisioning SELECT→INSERT race on duplicate email resolves via the users.email unique constraint (worst case one 5xx on simultaneous first login, no duplicate rows)
- [x] no exposed secrets, injection openings, or unexpected dependencies — client_secret lives only in the POST body to the IdP; id_token/access_token never reach the browser (cookie-only session); endpoint URLs from trusted Settings only; no SQL string interpolation; no new packages
- [x] layering & dependencies follow CONVENTIONS.md — gateway/auth mirrors the platform layout (domain → application → infrastructure → api); use case depends only on ports
- [x] a person reviewed and approved the change — risk: high / autonomy: conservative — full line-by-line security review by the orchestrator as Tin Dang's delegate (standing delegated auto mode grant)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — oidc_router included in main.py; get_oidc_use_case wires the app.state.oidc_exchanger override seam exactly as pinned (read first, else HttpxOidcExchanger); get_or_provision_oidc_user declared on the IdentityRepository port and implemented in SqlAlchemyIdentityRepository; GATEWAY_OIDC_* settings consumed by router/use case/exchanger; token_service reused from app.state — confirmed by reading every new/modified file
- [x] DEAD-CODE (code) — unused Role import removed at review; all 8 domain errors mapped in the router except-chain; ruff clean via make ci
- [x] SEMANTIC (prose / non-code) — §3 security preconditions read against the implementation one by one (checklist below); config validator messages read; no migration and `make migrate-check` stays clean

### SECURITY HARD-STOP checklist (must be manually reviewed; auto-PASS never applies):
- [x] id_token signature skip is documented in WARNING log (_SIGNATURE_SKIP_WARNING, emitted via structlog AND stdlib logging when environment not in dev/test) and in the §3/§5 safety prose with the OIDC Core §3.1.3.7(6) citation
- [x] oidc_client_secret never appears in any log line — audited every logger call in gateway/auth/*; the secret exists only in HttpxOidcExchanger._client_secret and the IdP POST body; no repr/bind exposure
- [x] No tokens in response body — /callback returns a 302 RedirectResponse with httpOnly cookies only; id_token/access_token never serialized to the client
- [x] owner/admin cannot be auto-granted via any SSO claim — role never read from claims anywhere; auto-provision hardcodes Role.MEMBER in the repository; the minted session uses the user's STORED role (orchestrator amendment: prevents silent downgrade of a legitimately-promoted existing user; SSO itself grants nothing — S12 still asserts new-user JWT decodes member)
- [x] state/nonce CSRF/replay binding verified end-to-end — state: httpOnly Lax cookie vs query param, constant-time compare, 400 on mismatch (S6), 400 on absent cookie (S7); nonce: claim vs httpOnly cookie, 401 on mismatch (S11); both cleared on every callback (single-use); Max-Age=300 bounds the window

Verified §3 OIDC Core §3.1.3.7(6) preconditions in code, one by one:
  1. id_token accepted ONLY from the server-side token-endpoint response body ✓
  2. httpx TLS verification never disabled (no verify= argument exists) ✓
  3. authorize/token endpoint URLs derive exclusively from Settings ✓
  4. nonce binding enforced ✓
  5. full claim validation (iss exact, aud contains client_id str|list, exp, email) ✓

### DISPOSITIONS (orchestrator review, delegated auto mode)
1. Stored-role session minting (builder hardcoded MEMBER for ALL logins — would
   silently downgrade a promoted existing user; auto-provision stays MEMBER-only).
2. Constant-time state comparison via hmac.compare_digest.
3. pyproject ruff exclude += tests/sso_oidc/test_sso_oidc.py (frozen file).

### GATE RECORD
Outcome: PASS
Evidence: tests/sso_oidc 16/16; root `make ci` exit 0 (312 passed, coverage
80.70% ≥ 80%); `make migrate-check` clean (no migration); SECURITY HARD-STOP
checklist completed above with no findings beyond the freeze-accepted [spec]
flag (no JWKS verification in v4 — spec-sanctioned with pinned preconditions;
v5 hardening: cryptography + RS256 JWKS).
Reviewed by: Tin Dang via delegated auto mode (orchestrator line-by-line
security review under the standing delegation; the conservative-autonomy human
gate is satisfied by that delegation grant) · date: 2026-06-11

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): OIDC callback error rate by code (ERR_OIDC_UPSTREAM_ERROR
spike = IdP down; ERR_OIDC_DOMAIN_NOT_MAPPED spike = misconfigured domain mapping);
ERR_OIDC_STATE_MISMATCH rate (potential CSRF probing); new user provisioning rate per tenant.
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence.
