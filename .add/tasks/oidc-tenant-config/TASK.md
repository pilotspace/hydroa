# TASK: Per-tenant OIDC IdP configuration (DB-backed, env fallback)

slug: oidc-tenant-config · created: 2026-06-11 · stage: production · risk: high · autonomy: conservative
phase: build   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- risk: high — multi-tenant auth + secrets at rest (Fernet) + SSRF surface (tenant-supplied URLs) +
     per-tenant JWKS kid-collision fix; autonomy: conservative — security review mandatory;
     build cannot auto-PASS at Verify. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Per-tenant OIDC IdP configuration — DB-backed per-tenant IdP records (new table
         `oidc_provider_configs`) with Fernet-encrypted client_secret at rest. GET /auth/oidc/login
         selects the tenant's IdP by `?domain=` query param matched against per-tenant registered
         email domains. The env-var Settings config (GATEWAY_OIDC_*) is the authoritative FALLBACK
         when no DB row matches, preserving the FROZEN sso-oidc (v4) and oidc-jwks (v5) flows
         exactly. The admin API (owner-only GET/PUT /admin/oidc) exposes per-tenant IdP management
         with client_secret NEVER returned. Two tenants with different IdPs can simultaneously
         complete OIDC logins against their respective configurations in a single app instance.

Framings weighed:

- **?domain= query param + email-domain-scoped DB rows** (CHOSEN):
  GET /auth/oidc/login?domain=acme.com looks up `oidc_provider_configs` where
  `email_domains @> ARRAY['acme.com']`. Unknown or absent domain → fall back to Settings env config
  if configured and oidc_enabled=True, else 404 ERR_OIDC_NOT_CONFIGURED. This mirrors the existing
  email-domain → tenant concept (sso-oidc §3 domain_mapping) and requires zero cookie
  proliferation. The chosen tenant context is committed to an httpOnly `oidc_tenant_id` cookie at
  /login time (in addition to the existing `oidc_state` / `oidc_nonce` cookies). At /callback, the
  gateway reads this cookie to select the correct per-tenant config — it NEVER trusts a tenant id
  from query params or from the IdP redirect. Tenant-confusion defense is analyzed and pinned below.

- Per-tenant slug paths /auth/oidc/{slug}/login (rejected): requires URL-schema change; existing
  frozen tests and envoy config reference /auth/oidc/login and /auth/oidc/callback by exact path.
  A path change touches frozen routes. Rejected: frozen-test break risk + envoy config touch.

- Discovery page (rejected): a form that asks the user to enter their email and infers the tenant.
  Adds a BFF surface and UX complexity. Rejected: unnecessary complexity for a B2B platform where
  operators configure the IdP; the operator can include the ?domain= param in their login link.

**Tenant-confusion defense (PINNED — the main attack surface):**
The attack: a victim completes /auth/oidc/login?domain=victim.com, obtains an `oidc_state` cookie,
then an attacker swaps the browser's `oidc_tenant_id` cookie to point at attacker.com's config
before the /callback fires. Defense: the `oidc_tenant_id` cookie is httpOnly (JS-inaccessible,
cannot be swapped by JS injection). The cookie is also SameSite=Lax (matches the oidc_state pattern
exactly). At /callback: the use case resolves ONLY the config row referenced by the oidc_tenant_id
cookie — the tenant context was committed at /login and cannot be changed by the IdP redirect. The
issuer/aud/jwks_url validation runs against THAT specific config. A token with issuer=attacker.com
presented under a oidc_tenant_id cookie for victim.com → iss mismatch → 401 ERR_OIDC_TOKEN_INVALID.
Cross-tenant code reuse is also blocked because the nonce is bound to the oidc_nonce cookie (set at
the same /login call), so a code obtained by an attacker against their own IdP cannot be exchanged
under the victim's config (the nonce will not match the victim's cookie). Defense is SUFFICIENT for
the cookie-based architecture — no additional HMAC over the cookie value is required beyond httpOnly.

**Secret at rest (PINNED):**
Fernet symmetric encryption with key from new Settings field `oidc_config_encryption_key` (env:
GATEWAY_OIDC_CONFIG_ENCRYPTION_KEY). This field is REQUIRED for PUT /admin/oidc to accept a
client_secret (a write with an unencrypted key → 409 ERR_OIDC_CONFIG_ENCRYPTION_NOT_CONFIGURED).
Fernet is already available (cryptography>=48.0.1 is in the allowlist since oidc-jwks). A single
static key is used — key rotation is OUT OF SCOPE for this task (documented). Alternative:
write-only plaintext (rejected — DB dump leaks all client_secrets), KMS (rejected — no cloud
dependency). The key is stored as base64url-encoded bytes (Fernet.generate_key() format).
If the key is absent (env var not set), per-tenant OIDC reads (GET /admin/oidc) still work — the
stored ciphertext is returned as a fixed placeholder string `"<encrypted>"` to signal that a secret
is stored without leaking it. Writes (PUT /admin/oidc with client_secret) fail 409.
SECURITY INVARIANT: client_secret is NEVER returned in GET responses, NEVER logged, NEVER included
in any problem+json body. The literal client_secret string must be absent from any response.

**users.auth_method decision (PINNED):**
DECISION: additive `users.auth_method` VARCHAR column, NOT NULL DEFAULT 'password', backfilled
from the "!sso-no-password" sentinel in the migration. Rationale: the sentinel approach (v4/v5) is
cleaner than a boolean but relies on Argon2PasswordHasher.verify() internals; a dedicated column
is the canonical signal and allows future auth methods (SAML, magic-link) without sentinel explosion.
The column is added in the SAME additive migration as `oidc_provider_configs`. The FROZEN sso-oidc
suite provisions users with the sentinel hash — the migration backfills `auth_method='oidc'` WHERE
password_hash = '!sso-no-password', so existing rows are correctly classified. New SSO users going
forward get `auth_method='oidc'` at INSERT time (set by get_or_provision_oidc_user). New password
users keep `auth_method='password'` (DEFAULT handles existing users). The FROZEN tests pass because
they do not SELECT auth_method (the column is additive).

**SSRF posture for tenant-supplied URLs (PINNED):**
PUT /admin/oidc accepts `issuer`, `authorize_url` (optional), `token_url`, `jwks_url`, `redirect_uri`
from the tenant admin. These are tenant-admin-supplied and represent an SSRF surface. Mitigation:
all URL fields in PUT /admin/oidc are validated to start with `https://` in production semantics.
Test fixtures may use `http://` URLs (the test seam: a Settings field `oidc_allow_http_urls: bool
= False` that tests override). In production oidc_allow_http_urls is never True. The validation runs
in the use case (not just the router) so it is never bypassed. `localhost` and RFC-1918 ranges are
BANNED in production URLs regardless of scheme. This prevents server-side metadata endpoint abuse.
SSRF analysis: the gateway issues outbound HTTP to IdP token_url (code exchange) and jwks_url (JWKS
fetch). Both endpoints originate from operator-trusted DB config (not from request input). The same
TLS-never-disabled invariant from v4/v5 applies: httpx verify=False is a HARD-STOP. The SSRF
posture for these operator-configured endpoints is: trusted-operator-configured ≈ Settings level.
The difference from v4/v5 is that tenant admins (not platform operators) can set these URLs. This
lowers the trust bar. The https-only + no-private-IP validation is the mandatory compensating control.

**OidcConfigResolver port (PINNED — the clean-architecture seam):**
A new protocol `OidcConfigResolver` in `auth/domain/ports.py`:
```
class OidcConfigResolver(Protocol):
    async def resolve(self, domain: str | None) -> OidcProviderConfig | None:
        """Return the IdP config for the given email domain, or None if not found.
        None signals: try the env Settings fallback."""
        ...
```
A `DbOidcConfigResolver` adapter in `auth/infrastructure/` queries `oidc_provider_configs`.
A `SettingsOidcConfigResolver` adapter wraps the env-var Settings config (used as the FALLBACK).
At /login, the resolver is called; the result is the config used to build the authorize URL and
stored (by config_id) in the `oidc_tenant_id` cookie. This port is injectable via
`app.state.oidc_config_resolver` — tests inject a fake to avoid DB dependencies.

**Resolution order (PINNED):**
  1. DB row: `oidc_provider_configs` WHERE `email_domains @> ARRAY[domain]` AND `enabled=TRUE`
  2. Settings fallback: if `settings.oidc_enabled=True` (the existing env config)
  3. None of the above: 404 ERR_OIDC_NOT_CONFIGURED

**Per-tenant JwksKeyCache kid-collision fix (PINNED — SECURITY):**
Investigation: `JwksKeyCache` keys by bare `kid`. In a multi-tenant deployment, tenant A's IdP
(jwks_url=A) and tenant B's IdP (jwks_url=B) may both issue tokens with `kid="key-1"`. The current
cache stores `kid → key` without namespace. A token from tenant A with `kid="key-1"` would be
verified with tenant B's key if tenant B's key was cached first.
Fix: cache key is a **(jwks_url, kid)** tuple — `(str, str | None) → (key, fetched_at)`.
The `JwksKeyCache.resolve` signature changes to include `jwks_url`:
```
async def resolve(self, jwks_url: str, kid: str | None, jwks_client: JwksClient) -> Any
```
This change is ADDITIVE to the JwksKeyCache (new required arg). The `OidcLoginUseCase` now passes
`jwks_url` from the resolved per-tenant config. The env-fallback path passes
`settings.oidc_jwks_url`. The FROZEN oidc-jwks test suite: investigation shows that the suite
injects `FakeJwksClient` via `app.state.jwks_client` and `JwksKeyCache` via `app.state.jwks_key_cache`.
Test J7 asserts `fake_jwks.calls == 1` (cache hit on second callback) — this test passes a
consistent jwks_url to both callbacks, so the `(jwks_url, kid)` tuple is the same on both calls:
the cache hit still fires. J5 asserts 2 calls on unknown kid — also unaffected by the new param.
CONCLUSION: the frozen oidc-jwks suite stays GREEN after the cache-key change, because
`oidc_jwks_url` from settings is the same for both callbacks in every J-test.
The `JwksKeyCache` in `create_app` stays; its `resolve` signature change is the only breakage risk,
and it is additive.

Must:
<must>
  - New Settings fields in `core/config.py` (additive, all GATEWAY_OIDC_CONFIG_* prefixed):
      oidc_config_encryption_key: str = ""   # GATEWAY_OIDC_CONFIG_ENCRYPTION_KEY (Fernet key, base64url)
      oidc_allow_http_urls: bool = False      # GATEWAY_OIDC_ALLOW_HTTP_URLS (dev/test only, never True in prod)
    Both fields are optional at Settings level (not added to _validate_oidc_config — they are
    independently governed). oidc_config_encryption_key absence → PUT 409 for writes; GET still works.

  - New table `oidc_provider_configs` (additive Alembic migration after f1b2c3d4e5a6 with rollback):
      tenant_id         UUID PRIMARY KEY NOT NULL REFERENCES tenants(id) ON DELETE CASCADE
      issuer            VARCHAR NOT NULL        (e.g. "https://accounts.google.com")
      client_id         VARCHAR NOT NULL
      client_secret_enc BYTEA NOT NULL          (Fernet-encrypted ciphertext; never NULL)
      authorize_url     VARCHAR NOT NULL DEFAULT '' (empty = derive from issuer + "/authorize")
      token_url         VARCHAR NOT NULL        (e.g. "{issuer}/token")
      jwks_url          VARCHAR NOT NULL        (e.g. "{issuer}/.well-known/jwks.json")
      email_domains     TEXT[]  NOT NULL DEFAULT '{}'  (GIN index for @> containment queries)
      enabled           BOOLEAN NOT NULL DEFAULT TRUE
      created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
      updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
    Additive migration also adds `users.auth_method VARCHAR(32) NOT NULL DEFAULT 'password'`,
    with backfill: `UPDATE users SET auth_method = 'oidc' WHERE password_hash = '!sso-no-password'`.
    EXPECTED_TABLES: must add "oidc_provider_configs" to the manifest in test_migrations.py.
    This is a SANCTIONED EDIT per the established pattern (teams-core, model-mgmt did the same).
    The manifest comment must read: "SANCTIONED EDIT — oidc-tenant-config TASK.md §3 manifest
    maintenance; disposition: additive migration <revision_id> adds this table".
    users table: `auth_method` column is additive, NOT in EXPECTED_TABLES (it is a column, not a
    table). The `test_upgrade_from_empty_parity` scenario checks ORM column coverage, so the
    `users` ORM model must gain the `auth_method` column to stay aligned.

  - New admin API routes in `auth/api/oidc_admin_router.py` (NEW file), prefix `/admin/oidc`:
      GET  /admin/oidc      (owner only) → returns per-tenant IdP config WITHOUT client_secret
      PUT  /admin/oidc      (owner only) → upsert per-tenant IdP config; requires encryption key

  - OidcProviderConfig dataclass (NEW, in `auth/domain/entities.py`, additive):
      @dataclass(frozen=True)
      class OidcProviderConfig:
          tenant_id:     uuid.UUID
          issuer:        str
          client_id:     str
          client_secret: str            # PLAINTEXT at domain layer ONLY; never persisted; encrypt before INSERT
          authorize_url: str            # empty = derive from issuer
          token_url:     str
          jwks_url:      str
          email_domains: list[str]
          enabled:       bool
    `OidcProviderConfigRow` (SQLAlchemy ORM model in `auth/infrastructure/orm.py` NEW file):
      stores `client_secret_enc` as BYTEA; NEVER exposes plaintext. Fernet decrypt only in
      the `DbOidcConfigResolver.resolve()` call — the plaintext config crosses domain layers
      only in memory, never serialized.

  - OidcConfigResolver protocol (NEW, additive to `auth/domain/ports.py`):
      class OidcConfigResolver(Protocol):
          async def resolve(self, domain: str | None) -> OidcProviderConfig | None: ...
      Implementations:
        DbOidcConfigResolver (auth/infrastructure/db_oidc_config_resolver.py): queries DB,
          decrypts secret in-memory using Fernet key from Settings.
        SettingsOidcConfigResolver (auth/infrastructure/settings_oidc_config_resolver.py):
          wraps the existing env-var OIDC fields.
      Test seam: app.state.oidc_config_resolver (injected by tests; deps.py reads it).

  - GET /auth/oidc/login changes:
      Accepts optional `?domain=<email_domain>` query param.
      Calls OidcConfigResolver.resolve(domain) → OidcProviderConfig | None.
      If None and settings.oidc_enabled: falls back to Settings env config (backwards compat).
      If None and not settings.oidc_enabled: 404 ERR_OIDC_NOT_CONFIGURED.
      Sets new `oidc_tenant_id` cookie: httpOnly, SameSite=Lax, Max-Age=300, value=tenant_id hex.
      For env-fallback path: oidc_tenant_id cookie value is the sentinel string "env-config".
      Existing `oidc_state` + `oidc_nonce` cookies unchanged.

  - GET /auth/oidc/callback changes:
      Reads `oidc_tenant_id` cookie.
      Resolves config: if value is "env-config" → use Settings env config; else resolve by tenant_id.
      Config used at callback = config pinned at login (from cookie). Never trusts query params for
      tenant selection. Validates iss/aud/jwks_url against THAT config only.
      Clears `oidc_tenant_id` cookie (Max-Age=0) alongside oidc_state/oidc_nonce.

  - JwksKeyCache.resolve signature change (additive param — SECURITY FIX):
      async def resolve(self, jwks_url: str, kid: str | None, jwks_client: JwksClient) -> Any
      Cache key is (jwks_url, kid) tuple — prevents cross-tenant kid collisions.
      The change is additive. All callers (use_cases.py) must pass jwks_url. Frozen oidc-jwks
      tests still pass because each test's two callbacks share the same jwks_url.

  - GET /admin/oidc (owner only):
      Returns { tenant_id, issuer, client_id, client_secret: "<stored>", authorize_url, token_url,
        jwks_url, email_domains, enabled, created_at, updated_at }.
      client_secret field is ALWAYS the literal string "<stored>" — never the plaintext or ciphertext.
      404 ERR_OIDC_CONFIG_NOT_FOUND if no row for the caller's tenant.
      401/403 per standard tenants auth (existing auth deps, unchanged).

  - PUT /admin/oidc (owner only):
      Body: { issuer, client_id, client_secret, authorize_url?, token_url, jwks_url, email_domains,
              enabled? }.
      If oidc_config_encryption_key is empty → 409 ERR_OIDC_CONFIG_ENCRYPTION_NOT_CONFIGURED.
      URL validation: issuer, token_url, jwks_url must start with https:// (unless
        oidc_allow_http_urls=True for tests). localhost / RFC-1918 IPs rejected in production.
      Encrypts client_secret with Fernet before INSERT/UPDATE.
      UPSERT: INSERT ON CONFLICT (tenant_id) DO UPDATE.
      Returns 200 { tenant_id, issuer, client_id, client_secret: "<stored>", ... } (same as GET).
      200 on create and update (no 201 — tenant may not know if row existed).

  - get_or_provision_oidc_user in SqlAlchemyIdentityRepository: adds `auth_method='oidc'` to the
      INSERT (new column). The protocol gains no new method — auth_method is set internally by
      the provision logic when password_hash == SSO_PASSWORD_HASH_SENTINEL.

  - Two-tenant scenario must be observable in the suite: tenant A (IdP A) and tenant B (IdP B)
      complete logins in ONE app instance with DIFFERENT issuers and the users land in their
      respective tenants. This is the core multi-tenant correctness invariant.

  - httpx timeout on all outbound calls to per-tenant IdP endpoints: 10 seconds (unchanged from v4).
      Per-tenant token_url replaces settings.oidc_issuer + "/token" when using DB config.
      Per-tenant jwks_url replaces settings.oidc_jwks_url when using DB config.

  - Error codes (reuse ERR_OIDC_* family; new codes only as unavoidable):
      NEW: ERR_OIDC_CONFIG_ENCRYPTION_NOT_CONFIGURED (409) — PUT without encryption key
      NEW: ERR_OIDC_CONFIG_NOT_FOUND (404) — GET /admin/oidc with no row for tenant
      NEW: ERR_OIDC_TENANT_COOKIE_MISSING (400) — /callback without oidc_tenant_id cookie
           (separate from ERR_OIDC_SESSION_EXPIRED which covers oidc_state cookie)
      EXISTING codes reused for all other rejections (URL validation → 422 with per-field errors).

  - Module boundary (clean architecture, additive):
      NEW:
        auth/domain/entities.py          (add OidcProviderConfig dataclass — additive)
        auth/domain/ports.py             (add OidcConfigResolver protocol — additive)
        auth/infrastructure/orm.py                   (OidcProviderConfigRow ORM model)
        auth/infrastructure/db_oidc_config_resolver.py
        auth/infrastructure/settings_oidc_config_resolver.py
        auth/api/oidc_admin_router.py    (GET + PUT /admin/oidc)
      MODIFIED:
        auth/application/use_cases.py    (accept OidcConfigResolver, jwks_url param to cache)
        auth/application/jwks_key_cache.py (resolve gains jwks_url param; cache key is tuple)
        auth/api/oidc_router.py          (read oidc_tenant_id cookie, call resolver)
        auth/api/deps.py                 (wire OidcConfigResolver from app.state or DB adapter)
        core/config.py                   (add 2 new Settings fields)
        main.py                          (register oidc_admin_router, wire resolver seam)
      MODIFIED (migration side):
        migrations/versions/<new>.py     (new additive migration)
        tenants/infrastructure/orm.py    (add auth_method column to UserRow)
      MIGRATION MANIFEST (SANCTIONED EDIT):
        tests/migrations/test_migrations.py  (add "oidc_provider_configs" to EXPECTED_TABLES)
      UNCHANGED (confirmed):
        auth/domain/errors.py            (new error classes added as standalone file additions)
        tests/sso_oidc/test_sso_oidc.py  (FROZEN — must not be touched)
        tests/oidc_jwks/test_oidc_jwks.py (FROZEN — must not be touched)
        infra/envoy/                     (no envoy changes)
        apps/dashboard/                  (no BFF changes)
</must>

Reject:
<reject>
  - GET /admin/oidc returns client_secret in plaintext → "ERR_OIDC_SECRET_LEAKED" (HARD-STOP — not an HTTP error, a security violation; the build must never produce this)
  - PUT /admin/oidc with oidc_config_encryption_key absent → "ERR_OIDC_CONFIG_ENCRYPTION_NOT_CONFIGURED" (409)
  - PUT /admin/oidc with issuer/token_url/jwks_url using http:// scheme in production (oidc_allow_http_urls=False) → 422 per-field validation error
  - PUT /admin/oidc with localhost or RFC-1918 IP in any URL field (production) → 422
  - GET /auth/oidc/login?domain= with no matching DB config and oidc_enabled=False → "ERR_OIDC_NOT_CONFIGURED" (404)
  - GET /auth/oidc/login?domain=unknown.com (no DB row, no env fallback configured) → "ERR_OIDC_NOT_CONFIGURED" (404)
  - GET /auth/oidc/callback without oidc_tenant_id cookie → "ERR_OIDC_TENANT_COOKIE_MISSING" (400)
  - GET /auth/oidc/callback where oidc_tenant_id cookie references a non-existent or disabled config → "ERR_OIDC_NOT_CONFIGURED" (404)
  - id_token iss ≠ resolved config's issuer → "ERR_OIDC_TOKEN_INVALID" (401) — tenant-confusion defense
  - id_token aud does not contain resolved config's client_id → "ERR_OIDC_TOKEN_INVALID" (401)
  - GET /admin/oidc for a tenant with no configured IdP → "ERR_OIDC_CONFIG_NOT_FOUND" (404)
  - Two tenants completing OIDC with swapped state cookies (tenant-confusion attack via cross-site JS injection of httpOnly cookie) → blocked by httpOnly; the oidc_tenant_id cookie is httpOnly and cannot be overwritten by JS
  - Existing sso-oidc §3 + oidc-jwks §3 rejections all remain in force (state mismatch, session expired, domain not mapped, tenant conflict, upstream error, etc.)
</reject>

After:
<after>
  - GET /admin/oidc returns per-tenant config with client_secret always as literal "<stored>".
  - PUT /admin/oidc upserts the per-tenant config; client_secret is Fernet-encrypted at rest.
  - GET /auth/oidc/login?domain=acme.com resolves the DB row for acme.com, sets oidc_tenant_id cookie.
  - GET /auth/oidc/login with no domain= and oidc_enabled=True → env Settings fallback (unchanged v4 behavior).
  - GET /auth/oidc/callback resolves the config from oidc_tenant_id cookie, NOT from query params.
  - Two-tenant happy path: tenant A and tenant B both complete OIDC logins, users land in correct tenants.
  - client_secret never appears in any GET response body, any log line, or any error body.
  - Tenant-confusion: callback with oidc_tenant_id for tenant A + token from tenant B's IdP → 401 ERR_OIDC_TOKEN_INVALID (iss mismatch).
  - Unknown domain (no DB row + env not configured) → 404 ERR_OIDC_NOT_CONFIGURED.
  - Env fallback: when no DB row for domain and oidc_enabled=True → env Settings config used (sso-oidc/oidc-jwks frozen tests remain GREEN by design).
  - users table gains auth_method column; SSO users have auth_method='oidc'; password users have auth_method='password'.
  - JwksKeyCache caches keys by (jwks_url, kid) tuple — no cross-tenant kid collisions.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ JWKS_KEY_CACHE SIGNATURE CHANGE BREAKS FROZEN OIDC-JWKS SUITE [contract]: The frozen
    oidc-jwks suite (11 tests) was built against the current JwksKeyCache.resolve(kid, jwks_client)
    signature. Adding `jwks_url` as a first param changes the call site in use_cases.py only —
    the frozen suite injects FakeJwksClient via app.state.jwks_client and never calls
    JwksKeyCache.resolve directly. The frozen tests do NOT import or instantiate JwksKeyCache;
    they assert on HTTP response codes and on FakeJwksClient.calls. The use_cases.py change
    (pass jwks_url to resolve) is invisible to the frozen suite. CONFIDENCE: 0.82.
    Why lowest: the interaction between a modified use_cases.py and the frozen test's
    FakeJwksClient is subtle; a bug in the jwks_url threading could flip J10 (compat pin).
    Cost if wrong: frozen oidc-jwks suite goes red — must never happen; the build is blocked.
    Mitigation: the orchestrator verifies the frozen suite stays green at build time (§6).

  ⚠ EXPECTED_TABLES SANCTIONED EDIT [contract]: The EXPECTED_TABLES manifest in
    tests/migrations/test_migrations.py is a FROZEN test file — it has pre-existing format
    exclusions in pyproject.toml and the "never edit frozen tests" contract. HOWEVER: investigation
    confirms that teams-core and model-mgmt both performed SANCTIONED EDITS to add their tables to
    this manifest (comments: "SANCTIONED EDIT — teams-core TASK.md §3 manifest maintenance" etc.).
    The pattern is established: adding a table to EXPECTED_TABLES in the frozen migrations test is
    the correct procedure, with a SANCTIONED EDIT comment citing the task. This is the ONLY allowed
    edit to test_migrations.py for this task. CONFIDENCE: 0.90.
    Why second-lowest: editing even a single line of a frozen test is high-friction; if the SANCTIONED
    EDIT convention is misapplied (e.g., wrong TASK.md citation, no build evidence), the Verify gate
    will catch it. Cost if wrong: migration test fails with unexpected table; or the Verify gate
    rejects an unsanctioned edit.

  ⚠ AUTH_METHOD COLUMN BACKFILL [spec]: The migration backfills auth_method='oidc' for rows with
    password_hash='!sso-no-password'. This is accurate because all SSO users provisioned in v4/v5
    have that exact sentinel. If any future task changes the sentinel value, the backfill condition
    misses those rows. Current confidence: 0.88 (the sentinel is frozen by sso-oidc §3 contract;
    no task can change it without a change request). Cost if wrong: SSO users have auth_method=
    'password' after migration; the column is an informational field (auth logic still works via
    sentinel); the bug is cosmetic but misleading.

  - FERNET KEY ROTATION OUT OF SCOPE [spec]: key rotation (replacing GATEWAY_OIDC_CONFIG_ENCRYPTION_KEY
    with a new value and re-encrypting existing rows) is not implemented in this task. If the key
    is compromised, all stored client_secrets must be manually re-entered via PUT /admin/oidc.
    CONFIDENCE: 0.95. This is a documented limitation, not an oversight.

  - URL VALIDATION FOR RFC-1918 RANGES [contract]: SSRF validation rejects localhost and private-IP
    ranges by hostname/IP parsing (ipaddress module). This covers 127.x, 10.x, 172.16-31.x, 192.168.x.
    DNS rebinding attacks (a hostname that resolves to a private IP at query time) are OUT OF SCOPE
    — they require a separate DNS-validation layer (resolver-at-validation-time check). CONFIDENCE: 0.93.
    Cost if wrong: a sophisticated DNS-rebinding SSRF is possible; acceptable for v6 hardening.

  - OidcConfigResolver port wiring via app.state.oidc_config_resolver [test]: mirrors the established
    app.state.oidc_exchanger + app.state.jwks_client seam pattern exactly. Tests inject a
    FakeOidcConfigResolver instead of constructing a DB connection. CONFIDENCE: 0.95.

  - USERS.AUTH_METHOD IN ORM ALIGNMENT [contract]: The test_upgrade_from_empty_parity scenario
    checks that ORM column names are a subset of DB column names. The users ORM model (UserRow)
    must gain the auth_method column. If missed, the parity test fails. CONFIDENCE: 0.93.
    Cost if wrong: test_upgrade_from_empty_parity fails after build.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: T1 — two-tenant happy path: tenant A (IdP A) and tenant B (IdP B) complete logins
  Given a running app with DB containing:
    - tenant A with oidc_provider_configs row: issuer=idp-a.test, client_id=client-a,
      email_domains=['a.com'], jwks_url=jwks-a.test
    - tenant B with oidc_provider_configs row: issuer=idp-b.test, client_id=client-b,
      email_domains=['b.com'], jwks_url=jwks-b.test
    - FakeOidcConfigResolver returning these configs by domain
    - FakeOidcExchanger (per-config) returning valid id_tokens
    - FakeJwksClient (per-config) returning the correct signing key
  When GET /auth/oidc/login?domain=a.com is called → oidc_tenant_id=tenant_A cookie set
  And  GET /auth/oidc/callback is called with tenant_A's state/nonce/tenant cookies + valid token
  And  GET /auth/oidc/login?domain=b.com is called → oidc_tenant_id=tenant_B cookie set
  And  GET /auth/oidc/callback is called with tenant_B's state/nonce/tenant cookies + valid token
  Then first callback: 302, user alice@a.com created in tenant_A with role=member, auth_method=oidc
  And  second callback: 302, user bob@b.com created in tenant_B with role=member, auth_method=oidc
  And  each user is in their respective tenant (no cross-contamination)
  And  what must remain unchanged: no user from tenant A is in tenant B's rows; issuers are distinct

Scenario: T2 — secret never returned (GET /admin/oidc response body)
  Given a tenant with an oidc_provider_configs row (client_secret stored encrypted)
  When  GET /admin/oidc is called (owner auth)
  Then  the response body does NOT contain the plaintext client_secret string
  And   the response body's client_secret field is exactly the string "<stored>"
  And   the raw response body as a string does NOT contain the plaintext secret
  And   what must remain unchanged: no other fields leak the secret

Scenario: T3 — secret never logged during PUT /admin/oidc
  Given a running app with encryption key configured
  When  PUT /admin/oidc is called with a plaintext client_secret in the body
  Then  the response is 200 with client_secret: "<stored>"
  And   no captured log line contains the plaintext client_secret string
  And   what must remain unchanged: the secret is encrypted before DB insert

Scenario: T4 — env fallback: existing sso-oidc/oidc-jwks behavior preserved when no DB row
  Given GATEWAY_OIDC_ENABLED=true (env config, existing v4/v5 Settings)
  And   NO oidc_provider_configs row for any domain
  And   GET /auth/oidc/login is called WITHOUT ?domain= param
  When  the callback completes with a valid v4-style HS256 token (no jwks_url configured)
  Then  the response is 302 with ai_proxy_session cookie (env fallback path works)
  And   the frozen sso-oidc behavior is preserved (green-by-design regression pin)
  And   what must remain unchanged: env-config flow is identical to pre-task behavior

Scenario: T5 — unknown domain → 404 ERR_OIDC_NOT_CONFIGURED
  Given GATEWAY_OIDC_ENABLED=false and NO oidc_provider_configs row for domain "unknown.com"
  When  GET /auth/oidc/login?domain=unknown.com is called
  Then  the response is 404 with code ERR_OIDC_NOT_CONFIGURED
  And   no oidc_tenant_id, oidc_state, oidc_nonce cookies are set
  And   what must remain unchanged: no session minted; no user created

Scenario: T6 — callback without oidc_tenant_id cookie → 400 ERR_OIDC_TENANT_COOKIE_MISSING
  Given OIDC is configured (DB or env)
  When  GET /auth/oidc/callback is called without the oidc_tenant_id cookie
        (but oidc_state and oidc_nonce cookies are present)
  Then  the response is 400 with code ERR_OIDC_TENANT_COOKIE_MISSING
  And   no user created, no session cookie set
  And   what must remain unchanged: no session minted; no user created

Scenario: T7 — tenant-confusion defense: callback with wrong tenant cookie
  Given tenant A's oidc_provider_configs has issuer=idp-a.test
  And   a valid token was issued by IdP B (issuer=idp-b.test, client_id=client-b)
  When  GET /auth/oidc/callback is called with:
        - oidc_tenant_id cookie pointing to tenant A
        - oidc_state/oidc_nonce cookies matching tenant A's login session
        - id_token with iss=idp-b.test (wrong issuer for tenant A's config)
  Then  the response is 401 with code ERR_OIDC_TOKEN_INVALID (iss mismatch)
  And   no user created, no session cookie set
  And   what must remain unchanged: tenant A's config is used; iss mismatch is the defense

Scenario: T8 — PUT /admin/oidc without encryption key → 409 ERR_OIDC_CONFIG_ENCRYPTION_NOT_CONFIGURED
  Given GATEWAY_OIDC_CONFIG_ENCRYPTION_KEY is not set (empty)
  When  PUT /admin/oidc is called (owner auth) with a valid body including client_secret
  Then  the response is 409 with code ERR_OIDC_CONFIG_ENCRYPTION_NOT_CONFIGURED
  And   no row is written to oidc_provider_configs
  And   what must remain unchanged: no DB write; no config change

Scenario: T9 — PUT /admin/oidc with http:// URL in production → 422 validation error
  Given oidc_allow_http_urls=False (production mode)
  When  PUT /admin/oidc is called with token_url="http://insecure.example.com/token"
  Then  the response is 422 with a per-field validation error for token_url
  And   no row is written to oidc_provider_configs
  And   what must remain unchanged: SSRF protection; no DB write

Scenario: T10 — GET /admin/oidc for tenant with no row → 404 ERR_OIDC_CONFIG_NOT_FOUND
  Given a tenant has no oidc_provider_configs row
  When  GET /admin/oidc is called (owner auth)
  Then  the response is 404 with code ERR_OIDC_CONFIG_NOT_FOUND
  And   what must remain unchanged: no config data leaked; DB unchanged

Scenario: T11 — cross-tenant kid collision: tenant A's key does not verify tenant B's token
  Given tenant A has jwks_url=jwks-a.test with kid="shared-kid" → key_A
  And   tenant B has jwks_url=jwks-b.test with kid="shared-kid" → key_B (different key)
  And   the JwksKeyCache is shared (one app instance)
  When  tenant A completes a callback (kid="shared-kid", signed with key_A) → cached as (jwks-a.test, shared-kid)
  And   tenant B's callback arrives with kid="shared-kid" (same kid, signed with key_B)
  Then  tenant B's callback fetches key_B from jwks-b.test (cache key = (jwks-b.test, shared-kid))
  And   tenant B's token is verified with key_B (NOT key_A)
  And   what must remain unchanged: cache keyed by (jwks_url, kid) — cross-tenant collision impossible

Scenario: T12 — PUT /admin/oidc round-trip: upsert, then GET returns "<stored>" not plaintext
  Given tenant T has no existing oidc_provider_configs row
  And   GATEWAY_OIDC_CONFIG_ENCRYPTION_KEY is a valid Fernet key
  When  PUT /admin/oidc is called with client_secret="my-real-secret"
  Then  the response is 200 with body.client_secret == "<stored>"
  And   subsequent GET /admin/oidc returns body.client_secret == "<stored>"
  And   the DB row's client_secret_enc is NOT the plaintext "my-real-secret"
  And   what must remain unchanged: secret is always encrypted at rest; never returned by API
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /admin/oidc
  Authorization: Bearer <owner-jwt>
  200 -> {
    "tenant_id": "<uuid>",
    "issuer": "<str>",
    "client_id": "<str>",
    "client_secret": "<stored>",          # ALWAYS this literal string; never plaintext
    "authorize_url": "<str>",             # empty string if derived
    "token_url": "<str>",
    "jwks_url": "<str>",
    "email_domains": ["<str>", ...],
    "enabled": <bool>,
    "created_at": "<iso8601>",
    "updated_at": "<iso8601>"
  }
  404 -> { "code": "ERR_OIDC_CONFIG_NOT_FOUND" }
  401/403 per standard tenants auth (unchanged)

PUT /admin/oidc
  Authorization: Bearer <owner-jwt>
  Body: {
    "issuer": "<str>",                    # required; https:// only in production
    "client_id": "<str>",                 # required
    "client_secret": "<str>",             # required
    "authorize_url": "<str>",             # optional; defaults to issuer + "/authorize"
    "token_url": "<str>",                 # required; https:// only in production
    "jwks_url": "<str>",                  # required; https:// only in production
    "email_domains": ["<str>", ...],      # required; list of email domains for this tenant
    "enabled": <bool>                     # optional; defaults to true
  }
  200 -> { same shape as GET 200 with client_secret: "<stored>" }
  409 -> { "code": "ERR_OIDC_CONFIG_ENCRYPTION_NOT_CONFIGURED" }
       (oidc_config_encryption_key absent from Settings)
  422 -> per-field validation errors (URL scheme, private IP, missing required fields)
  401/403 per standard tenants auth (unchanged)

GET /auth/oidc/login?domain=<email_domain>   [domain= is optional]
  302 -> Location: {resolved_authorize_url}?response_type=code&client_id={...}&...
         Set-Cookie: oidc_tenant_id={tenant_id_hex|"env-config"}; HttpOnly; SameSite=Lax;
                     Path=/auth/oidc; Max-Age=300[; Secure]
         Set-Cookie: oidc_state={state}; HttpOnly; SameSite=Lax; Path=/auth/oidc; Max-Age=300
         Set-Cookie: oidc_nonce={nonce}; HttpOnly; SameSite=Lax; Path=/auth/oidc; Max-Age=300
  404 -> { "code": "ERR_OIDC_NOT_CONFIGURED" }

GET /auth/oidc/callback?code=<code>&state=<state>
  302 -> Location: {GATEWAY_OIDC_POST_LOGIN_REDIRECT}
         Set-Cookie: ai_proxy_session={jwt}; HttpOnly; SameSite=Strict; Path=/; Max-Age={...}
         Set-Cookie: oidc_tenant_id=; ...; Max-Age=0       (cleared)
         Set-Cookie: oidc_state=; ...; Max-Age=0           (cleared)
         Set-Cookie: oidc_nonce=; ...; Max-Age=0           (cleared)
  400 -> { "code": "ERR_OIDC_TENANT_COOKIE_MISSING" | "ERR_OIDC_STATE_MISMATCH"
                  | "ERR_OIDC_SESSION_EXPIRED" | "ERR_OIDC_INVALID_CALLBACK" }
  401 -> { "code": "ERR_OIDC_TOKEN_INVALID" | "ERR_OIDC_TOKEN_EXPIRED" }
  403 -> { "code": "ERR_OIDC_DOMAIN_NOT_MAPPED" | "ERR_OIDC_TENANT_CONFLICT" }
  404 -> { "code": "ERR_OIDC_NOT_CONFIGURED" }
  502 -> { "code": "ERR_OIDC_UPSTREAM_ERROR" }

Schema DDL (new table — additive migration after f1b2c3d4e5a6):
  CREATE TABLE oidc_provider_configs (
    tenant_id         UUID PRIMARY KEY NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    issuer            VARCHAR NOT NULL,
    client_id         VARCHAR NOT NULL,
    client_secret_enc BYTEA NOT NULL,
    authorize_url     VARCHAR NOT NULL DEFAULT '',
    token_url         VARCHAR NOT NULL,
    jwks_url          VARCHAR NOT NULL,
    email_domains     TEXT[] NOT NULL DEFAULT '{}',
    enabled           BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
  );
  CREATE INDEX ix_oidc_provider_configs_email_domains
    ON oidc_provider_configs USING GIN (email_domains);

  ALTER TABLE users ADD COLUMN IF NOT EXISTS auth_method VARCHAR(32) NOT NULL DEFAULT 'password';
  UPDATE users SET auth_method = 'oidc' WHERE password_hash = '!sso-no-password';

Rollback (downgrade):
  DROP TABLE IF EXISTS oidc_provider_configs;
  ALTER TABLE users DROP COLUMN IF EXISTS auth_method;

Migration chain: f1b2c3d4e5a6 (semantic-caching) → <new_rev_id> (oidc-tenant-config)
Migration revision: to be assigned by Alembic at build time (placeholder: a9b3c4d5e6f7).

EXPECTED_TABLES SANCTIONED EDIT:
  tests/migrations/test_migrations.py: add "oidc_provider_configs" to EXPECTED_TABLES frozenset.
  Comment: "SANCTIONED EDIT — oidc-tenant-config TASK.md §3 manifest maintenance;
            disposition: additive migration a9b3c4d5e6f7 adds this table"
  users.auth_method is a column addition — NOT added to EXPECTED_TABLES (tables-only manifest).
  ORM alignment: UserRow in tenants/infrastructure/orm.py must gain auth_method column so that
  test_upgrade_from_empty_parity's ORM-vs-DB column coverage check stays green.

OidcConfigResolver port (NEW, in auth/domain/ports.py):
  class OidcConfigResolver(Protocol):
      async def resolve(self, domain: str | None) -> OidcProviderConfig | None:
          """Return per-tenant config for domain, or None to signal env-fallback."""
          ...

OidcProviderConfig (NEW, in auth/domain/entities.py):
  @dataclass(frozen=True)
  class OidcProviderConfig:
      tenant_id:     uuid.UUID
      issuer:        str
      client_id:     str
      client_secret: str        # PLAINTEXT; in-memory only; NEVER serialized
      authorize_url: str        # empty = derive from issuer + "/authorize"
      token_url:     str
      jwks_url:      str
      email_domains: list[str]
      enabled:       bool

New error classes (in auth/domain/errors.py, additive):
  OidcConfigEncryptionNotConfiguredError   → 409 ERR_OIDC_CONFIG_ENCRYPTION_NOT_CONFIGURED
  OidcConfigNotFoundError                  → 404 ERR_OIDC_CONFIG_NOT_FOUND
  OidcTenantCookieMissingError             → 400 ERR_OIDC_TENANT_COOKIE_MISSING

JwksKeyCache.resolve signature (BREAKING CHANGE — security fix):
  async def resolve(self, jwks_url: str, kid: str | None, jwks_client: JwksClient) -> Any
  Cache dict type: dict[tuple[str, str | None], tuple[Any, float]]
  Breaking change to use_cases.py call site only; frozen tests unaffected (they do not
  call JwksKeyCache.resolve directly; they inject FakeJwksClient + observe HTTP responses).

New Settings fields (additive):
  oidc_config_encryption_key: str = ""   # GATEWAY_OIDC_CONFIG_ENCRYPTION_KEY
  oidc_allow_http_urls: bool = False      # GATEWAY_OIDC_ALLOW_HTTP_URLS (test seam)

SSRF validation rule (PINNED):
  URL fields (issuer, token_url, jwks_url, authorize_url) must satisfy:
    (a) scheme is https:// OR (oidc_allow_http_urls is True AND scheme is http://)
    (b) hostname is NOT localhost, 127.x, ::1
    (c) if hostname is an IP: NOT in RFC-1918 (10.x, 172.16-31.x, 192.168.x)
  Validation runs in the use case / PUT handler before any DB write.
  Failure → 422 with field-level error (not a domain error class).

Auth pattern for /admin/oidc:
  Reuses the existing owner-only pattern from /admin/budget, /admin/guardrails:
  - Dependency: get_current_user_owner (or equivalent from tenants/api/deps.py)
  - 403 if role != owner (standard, unchanged)

Fake injection seams for tests (PINNED):
  app.state.oidc_config_resolver: OidcConfigResolver | None
    → tests inject FakeOidcConfigResolver; production resolves via DbOidcConfigResolver
  app.state.oidc_exchanger: OidcTokenExchanger | None      (unchanged from v4/v5)
  app.state.jwks_client: JwksClient | None                 (unchanged from v5)
  app.state.jwks_key_cache: JwksKeyCache                   (unchanged from v5)

Modules touched (complete list):
  NEW:
    auth/domain/entities.py      (OidcProviderConfig — additive to existing file)
    auth/domain/ports.py         (OidcConfigResolver — additive to existing file)
    auth/domain/errors.py        (3 new error classes — additive to existing file)
    auth/infrastructure/orm.py
    auth/infrastructure/db_oidc_config_resolver.py
    auth/infrastructure/settings_oidc_config_resolver.py
    auth/api/oidc_admin_router.py
    migrations/versions/a9b3c4d5e6f7_oidc_tenant_config.py
  MODIFIED:
    auth/application/use_cases.py
    auth/application/jwks_key_cache.py
    auth/api/oidc_router.py
    auth/api/deps.py
    core/config.py
    main.py
    tenants/infrastructure/orm.py   (auth_method column on UserRow)
  SANCTIONED EDIT:
    tests/migrations/test_migrations.py  (EXPECTED_TABLES manifest only)
  UNCHANGED (confirmed):
    tests/sso_oidc/test_sso_oidc.py  (FROZEN)
    tests/oidc_jwks/test_oidc_jwks.py (FROZEN)

Least-sure flag surfaced at freeze:
  ⚠ [contract] JwksKeyCache.resolve signature change (jwks_url param): touches the v5-frozen
    application-layer cache. The change is invisible to the frozen oidc-jwks test suite
    (tests do not call resolve directly), but a subtle bug in the jwks_url threading through
    use_cases.py could break J10 (the v4-compat pin). Cost if wrong: frozen suite red; build
    blocked. Mitigation: the orchestrator must verify frozen suite green after this change.
  ⚠ [contract] EXPECTED_TABLES SANCTIONED EDIT: editing test_migrations.py is bounded to
    adding one string to the frozenset. The established pattern (teams-core, model-mgmt) provides
    precedent, but it is still an edit to a frozen test file. Cost if wrong: migration test
    unexpectedly fails or the edit is broader than allowed, violating the frozen-test contract.
```

Status: FROZEN — approved by Tin Dang (delegated auto mode, 2026-06-11).
  Orchestrator review notes: (1) CROSS-CONTRACT SUPERSESSION recorded — the frozen
  oidc-jwks §3 pinned JwksKeyCache.resolve(kid, jwks_client); this contract supersedes
  it to resolve(jwks_url, kid, jwks_client) with cache key (jwks_url, kid) because
  bare-kid keying is a cross-tenant key-confusion vector once jwks_urls are per-tenant
  (T11 proves it). The frozen oidc-jwks artifact is NOT edited; this §3 + this note are
  the documented disposition, and the frozen oidc-jwks suite must stay green post-build.
  (2) EXPECTED_TABLES manifest extension in tests/migrations/test_migrations.py is a
  SANCTIONED contracted edit (teams-core v4 precedent — the manifest names every
  contracted table; adding oidc_provider_configs at this freeze is the manifest doing
  its job). (3) Tenant-confusion defense (oidc_tenant_id httpOnly cookie pins the config;
  iss/aud validated against THAT config) + Fernet-at-rest + auth_method column approved.
  Red re-run by orchestrator: 12/12 failed for the right reasons (404 routes, missing
  resolver seam, missing error codes, TypeError on new resolve signature) — the front
  agent's DB-unavailability worry did NOT reproduce; frozen sso-oidc + oidc-jwks 27/27
  green — authoritative.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 85%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_two_tenant_happy_path: T1 — arrange two tenant DB rows with distinct issuer/jwks_url;
    FakeOidcConfigResolver per domain; two complete login flows in one app instance;
    assert users land in correct tenants, both 302, auth_method=oidc

  - test_secret_never_returned_in_get_response: T2 — arrange tenant with stored config;
    act GET /admin/oidc; assert response body does NOT contain plaintext secret;
    assert client_secret field == "<stored>"

  - test_secret_never_logged_during_put: T3 — capture logs; act PUT /admin/oidc with secret;
    assert no log line contains the plaintext secret; assert response 200 + client_secret "<stored>"

  - test_env_fallback_preserves_v4_behavior: T4 — arrange oidc_enabled=True (env only), no DB row;
    FakeOidcExchanger with HS256 token, no jwks_url; act callback; assert 302 (env fallback)

  - test_unknown_domain_returns_404: T5 — arrange oidc_enabled=False, no DB row for unknown.com;
    act GET /auth/oidc/login?domain=unknown.com; assert 404 ERR_OIDC_NOT_CONFIGURED

  - test_callback_without_tenant_cookie_returns_400: T6 — arrange valid OIDC config;
    act callback without oidc_tenant_id cookie; assert 400 ERR_OIDC_TENANT_COOKIE_MISSING

  - test_tenant_confusion_defense: T7 — arrange tenant A config (issuer=idp-a.test);
    act callback with oidc_tenant_id=tenantA cookie but token with iss=idp-b.test;
    assert 401 ERR_OIDC_TOKEN_INVALID (iss mismatch defense)

  - test_put_without_encryption_key_returns_409: T8 — arrange oidc_config_encryption_key="";
    act PUT /admin/oidc with client_secret; assert 409 ERR_OIDC_CONFIG_ENCRYPTION_NOT_CONFIGURED

  - test_put_with_http_url_in_production_returns_422: T9 — arrange oidc_allow_http_urls=False;
    act PUT /admin/oidc with token_url="http://..."; assert 422 per-field error

  - test_get_no_config_returns_404: T10 — arrange tenant with no DB row;
    act GET /admin/oidc; assert 404 ERR_OIDC_CONFIG_NOT_FOUND

  - test_jwks_cache_no_cross_tenant_collision: T11 — arrange two FakeJwksClients with same kid
    but different keys; both tenants complete callback; assert each verifies against own key
    (assert (jwks_url_A, kid) and (jwks_url_B, kid) are separate cache entries; no cross-verification)

  - test_put_get_round_trip_secret_never_returned: T12 — arrange valid encryption key;
    act PUT /admin/oidc with client_secret="my-real-secret"; assert 200 + client_secret "<stored>";
    act GET /admin/oidc; assert client_secret "<stored>"; query DB directly, assert ciphertext != plaintext
</test_plan>

Tests live in: `apps/gateway/tests/oidc_tenant_config/test_oidc_tenant_config.py`

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific):
  1. client_secret NEVER returned in GET /admin/oidc response — HARD-STOP if violated.
  2. client_secret NEVER logged — HARD-STOP. grep for oidc_client_secret in any logger call.
  3. Fernet encrypt BEFORE any DB INSERT/UPDATE of client_secret; never store plaintext.
  4. oidc_tenant_id cookie is httpOnly — HARD-STOP if missing.
  5. At /callback, config is resolved ONLY from oidc_tenant_id cookie — NEVER from query params.
  6. JwksKeyCache.resolve cache key is (jwks_url, kid) tuple — bare kid is a SECURITY BUG.
  7. URL validation (https-only + no-private-IP) runs in the use case, not only in the router.
  8. SSRF: httpx verify=False is a HARD-STOP anywhere in new code.
  9. Frozen tests (sso_oidc, oidc_jwks) must stay green — verify after every JwksKeyCache change.
  10. SANCTIONED EDIT to test_migrations.py is the ONLY allowed test file change.
Code lives in: `./src/`
Constraints: do NOT change any frozen test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build (except SANCTIONED EDIT to EXPECTED_TABLES)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — OidcConfigResolver seam wired in deps.py + main.py; oidc_tenant_id cookie
      set in oidc_router.py; JwksKeyCache.resolve called with jwks_url; admin router registered
- [ ] DEAD-CODE (code) — no orphaned symbols introduced
- [ ] SEMANTIC (prose / non-code) — §3 read in full against the diff

### GATE RECORD
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): ERR_OIDC_NOT_CONFIGURED rate by domain (unknown domain spike);
  ERR_OIDC_TOKEN_INVALID rate per tenant (tenant-confusion probes?); GET /admin/oidc 404 rate
  (tenants without configured IdP); PUT /admin/oidc 409 rate (operators missing encryption key).
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence.
