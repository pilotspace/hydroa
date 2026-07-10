# TASK: SAML 2.0 SSO alongside OIDC

slug: saml-sso · created: 2026-07-10 · stage: production · sensitivity: security · risk: high · autonomy: conservative
milestone: enterprise-identity-compliance
<!-- risk: high — new unauthenticated XML-parsing surface (POST /auth/saml/acs) that mints a real
     session JWT on successful validation; a signature-wrapping or tenant-confusion defect is a
     full account-takeover class bug. autonomy lowered to conservative: build cannot auto-PASS at
     Verify; HARD-STOP verify per the milestone's shared decision (Identity surface). -->
phase: tests   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/auth/api/oidc_router.py` — the SP-initiated flow this task mirrors
  (`oidc_login`, `oidc_callback`, `_set_oidc_cookie`/`_clear_oidc_cookie`, the `ai_proxy_session`
  cookie-set block at `oidc_callback:295-303`). SAML gets a parallel `saml_router.py`, NOT edits here.
- `apps/gateway/src/gateway/auth/api/oidc_admin_router.py` — the owner-only per-tenant admin-config
  CRUD shape this task mirrors (`get_oidc_config`, `put_oidc_config`, `_get_owner_identity` at
  `oidc_admin_router.py:132`, `_validate_oidc_url`/`_is_private_ip` SSRF guard at lines 62-97,
  the Fernet-encrypt-before-INSERT pattern at lines 262-267, the `pg_insert(...).on_conflict_do_update`
  upsert at lines 269-301, the fire-and-forget `record_audit` call at lines 318-339).
- `apps/gateway/src/gateway/auth/application/use_cases.py::OidcLoginUseCase.execute` — the orchestration
  shape (state/CSRF check → exchange → claim validation → domain mapping → provision → mint) and the
  **JWT-issuance seam**: `self._tokens.issue(user_id=..., tenant_id=..., role=..., email=...)` at
  `use_cases.py:351-356` — this is the exact seam SAML must call to mint the SAME `ai_proxy_session`
  JWT shape as OIDC/password. `TokenService.issue` is defined as a `Protocol` in
  `apps/gateway/src/gateway/tenants/domain/ports.py` (`issue` method, ~line 44) — the port is
  reusable as-is, no change needed.
- `apps/gateway/src/gateway/tenants/domain/ports.py::IdentityRepository` — Protocol with
  `get_or_provision_oidc_user(*, email, tenant_id, password_hash) -> User` (line ~19). This is a
  **frozen production port** (sso-oidc / oidc-tenant-config contracts, currently shipped) — SAML
  must NOT change its signature. Its implementation in
  `apps/gateway/src/gateway/tenants/infrastructure/repository.py::IdentityRepositoryImpl.get_or_provision_oidc_user`
  (lines 101-155+) is the JIT-provisioning template: look up by email; if found, 403
  `OidcTenantConflictError` when `row.tenant_id != tenant_id`; else INSERT a new `UserRow` with
  `role=Role.MEMBER` (hardcoded, never from any external claim) and `auth_method="oidc"`.
- `apps/gateway/src/gateway/tenants/infrastructure/orm.py::UserRow.auth_method` (lines 163-169) —
  `VARCHAR(32)`, `NOT NULL DEFAULT 'password'`, **no CHECK constraint** — a new literal `"saml"`
  value needs zero migration, confirmed by reading the column definition directly (no enum, no
  constraint to widen).
- `apps/gateway/src/gateway/auth/infrastructure/orm.py::OidcProviderConfigRow` (whole file) — the
  per-tenant-config table shape this task's `saml_provider_configs` table mirrors: `tenant_id` UUID
  PK/FK CASCADE, a GIN index on an `email_domains TEXT[]` column for `@>` containment queries,
  `enabled BOOLEAN`, `created_at`/`updated_at` with `onupdate=func.now()`.
- `apps/gateway/src/gateway/auth/domain/entities.py::OidcProviderConfig`,
  `apps/gateway/src/gateway/auth/domain/ports.py::OidcConfigResolver/JwksClient`,
  `apps/gateway/src/gateway/auth/domain/errors.py` (whole file, 9 OIDC error classes),
  `apps/gateway/src/gateway/auth/infrastructure/db_oidc_config_resolver.py::DbOidcConfigResolver` —
  the domain/port/error/resolver shapes SAML's parallel vertical mirrors file-for-file.
- `apps/gateway/src/gateway/auth/infrastructure/httpx_oidc_exchanger.py::HttpxOidcExchanger.exchange`
  — the ONLY outbound-IO adapter in the OIDC vertical: 10s `httpx.Timeout`, `verify=True` never
  overridden, catches `httpx.TimeoutException`/`httpx.RequestError` → `OidcUpstreamError`. **No
  circuit breaker and no retry** on this seam — the authorization code is single-use/non-idempotent,
  so a retry would burn the code. SAML's SP-initiated flow has **no equivalent server-to-server call**
  (the IdP POSTs the assertion to the browser, which POSTs it to `/acs` — no gateway-initiated
  outbound HTTP in the hot path at all, given the v1 manual-cert-entry framing chosen below).
- `apps/gateway/src/gateway/proxy/infrastructure/circuit_breaker.py::CircuitBreaker` — the project's
  one reusable CB primitive (PROJECT.md invariant: "No outbound IO without timeout + bounded retry
  + circuit breaker"); relevant only if a later delta adds metadata-URL fetching (rejected for v1,
  see §1).
- `apps/gateway/src/gateway/main.py:1103-1121` — composition root: `app.state.jwks_key_cache`,
  `app.state.oidc_config_resolver = None` (prod default; DB resolver constructed per-request),
  `app.include_router(oidc_router)` / `app.include_router(oidc_admin_router)` — SAML's two new
  routers mount here identically.
- `apps/gateway/src/gateway/main.py:921-951` — `app.state.redis_client = aioredis.from_url(...)`,
  reused verbatim by `RedisLuaRateLimiter`, `AgentOAuthIpRateLimiter`, `PlaygroundMintRateLimiter`,
  `InvitePublicRateLimiter` — the construction-with-no-IO pattern this task's Redis-backed
  request/replay store reuses. **Contrast**: those 4 existing Redis consumers are documented
  fail-OPEN (availability-first, rate-limiting is not a correctness gate). This task's replay store
  is a correctness/security gate and must fail CLOSED instead (see §1 M12) — a deliberate posture
  divergence from the existing Redis-consumer precedent, named explicitly so it isn't read as an
  oversight.
- `apps/gateway/src/gateway/core/error_catalog.py` (whole OIDC block, lines 463-524) — the
  `ErrorSpec(status, code, title_template)` + `.exc()` pattern every new `ERR_SAML_*` entry follows;
  no direct `ProblemError` construction at call sites.
- `apps/gateway/src/gateway/tenants/domain/entities.py::Role` (StrEnum, line 10) — six tenant roles
  + `SUPERADMIN`; SAML JIT-provisioned users are always `Role.MEMBER`, mirroring OIDC.
- `apps/gateway/pyproject.toml` (dependencies list) + `.add/dependencies.allowlist` (30 lines) —
  **neither lists any XML-signature/SAML library** (no `lxml`, `xmlsec`, `python3-saml`, `pysaml2`,
  `signxml`). This is a real gap this task's contract must close (see §3 freeze question 1) — SAML
  assertion validation cannot ship on hand-rolled XML parsing (see Issues/Risks below).
- `apps/gateway/Dockerfile` — `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` builder + runtime
  stages, `uv sync --frozen --no-dev` (no `apt-get` calls today). A C-extension XML-security library
  (`xmlsec`, needed by both realistic library choices) may require `libxmlsec1`/`libxml2` system
  packages at build time if no manylinux wheel resolves for this exact image — **unverified from
  Ground alone** (no live `uv sync` was run); flagged as the top freeze uncertainty (§1 ⚠).
- `apps/gateway/migrations/versions/` — confirmed via `alembic heads` (not hand-parsed) a single
  linear head: **`511ad8a7b65e`** (`511ad8a7b65e_audit_events_actor_key_id.py`) — the new
  `saml_provider_configs` migration parents here.
- No existing `apps/gateway/tests/saml_sso/` or `apps/gateway/tests/sso_saml/` directory — greenfield
  test surface, no frozen SAML tests exist yet.

Context (working folder): `.add/milestones/enterprise-identity-compliance/MILESTONE.md` (Scope §2,
Shared decisions §, Shared/risky contracts §, exit criterion 2); this task owns "SAML assertion-
validation + tenant-resolution contract" per the milestone's Shared/risky contracts list.
`domain-capture` (sibling task) is declared `depends-on: saml-sso` because it shares the tenant-SSO
config surface — this task's admin-router pattern and `email_domains`-GIN-index precedent are what
`domain-capture` will build against; nothing in `domain-capture`'s scope changes this task's contract.

Honors (patterns / conventions):
- CONVENTIONS.md "Architecture: CLEAN ARCHITECTURE per domain module" — `domain/` (zero framework
  imports) ← `application/` ← `infrastructure/` ← `api/`; SAML is a full parallel vertical inside
  the existing `auth/` module, not a new top-level module (same bounded context as OIDC: "how a
  session JWT gets issued via federated identity").
- CONVENTIONS.md "every outbound IO has timeout + bounded jittered retry (idempotent ops only) +
  circuit breaker" — the design below has **zero new outbound-IO seams in the login hot path**
  (manual cert/URL entry, no metadata-XML fetch); this is a scope choice, not an oversight (§1).
- CONVENTIONS.md "Dependencies: every package in `.add/dependencies.allowlist`" — this task's build
  cannot proceed without a PR-worthy addition to that file; named explicitly as a freeze artifact.
- PROJECT.md DDD fold: `A Permission-shaped RBAC gate cannot express "excludes tenant OWNER"` —
  irrelevant here (owner-only admin gate reuses `Role.OWNER` check exactly like OIDC, no new
  Permission needed).
- PROJECT.md folded lesson (oidc-tenant-config): "a security control skipped by design needs a
  PRIMARY-SPEC citation plus pinned preconditions in §3" — this task does the opposite (adds a
  control, doesn't skip one), but the same rigor bar applies to any spec-sanctioned scope cut (v1
  IdP-initiated exclusion, v1 unsigned-AuthnRequest) — each is cited with rationale below.
- `.add/GLOSSARY.md` `oidc_claim_mapping` term — SAML's tenant-binding mechanism is a *different*
  mechanism (server-side request-store, not a claims-mapping table read at callback time) and gets
  its own glossary term (§3), not a reuse of this one.

Anchors the contract cites: `OidcLoginUseCase.execute` (JWT-issuance seam location — line 351),
`TokenService.issue` (`tenants/domain/ports.py`), `IdentityRepository` (`tenants/domain/ports.py`),
`get_or_provision_oidc_user` (`tenants/infrastructure/repository.py:101`), `UserRow.auth_method`
(`tenants/infrastructure/orm.py:167`), `OidcProviderConfigRow` (`auth/infrastructure/orm.py`),
`_validate_oidc_url`/`_is_private_ip` (`auth/api/oidc_admin_router.py:62-97`), `app.state.redis_client`
(`main.py:921`), `CircuitBreaker` (`proxy/infrastructure/circuit_breaker.py`), migration head
`511ad8a7b65e`.

Issues/Risks (→ feed §1):
- **R1 (structural, not a bug): SameSite=Lax cookie binding does not carry to SAML's ACS endpoint.**
  OIDC's tenant-confusion defense (`oidc_router.py` docstring, lines 7-18) pins the tenant via an
  httpOnly `oidc_tenant_id` cookie set at `/login`, read back at `/callback` — this works because
  `/callback` is reached via a same-navigation 302 GET redirect from the IdP, and `SameSite=Lax`
  cookies ARE sent on a top-level cross-site GET. SAML's Assertion Consumer Service is reached via
  an **HTTP-POST binding**: the IdP's response page auto-submits a `<form method="POST">` to the
  SP's ACS URL — a cross-site top-level **POST** navigation, on which modern browsers do **NOT**
  send `SameSite=Lax` cookies (Lax exempts only "safe" methods: GET/HEAD). A cookie-based tenant pin
  copied verbatim from OIDC would silently fail to arrive at `/acs`, degrading (best case) to an
  always-taken fallback branch or (worst case) an attacker-controllable one. This is the single
  biggest reason this task cannot be a literal copy-paste of `oidc_router.py` — the tenant-binding
  mechanism must be **server-side state keyed by the SP's own AuthnRequest ID**, not a cookie. Feeds
  §1 M1/M3 and is this task's central design decision.
- **R2 (security, XSW class): a "verify() returns True, then read fields from the parsed DOM"
  pattern is exactly the shape of the 2012 XML Signature Wrapping bugs (Somorovsky et al.) found
  across nearly every major SAML implementation of that era** — an attacker adds a *second*,
  attacker-controlled, unsigned copy of the Assertion/NameID/Attributes elsewhere in the document;
  a validator that checks "a signature exists and is valid somewhere in this document" but then
  re-traverses the DOM by tag name for the actual claims can be tricked into trusting the unsigned
  copy. Mitigation must be structural (validate via the signed `Reference`'s `URI`/`ID`, extract
  claims from that exact signed node only) — this is a library-choice-defining risk, not a detail
  to defer to Build (§1 M4, §3 freeze question 2).
- **R3: no XML/SAML package is allow-listed** (`dependencies.allowlist` has 22 runtime entries, none
  XML/SAML-related) — confirmed by direct read, not inference. Any implementation needs a
  `.add/dependencies.allowlist` PR before Build can start; this is a freeze-time decision, not a
  Build-time surprise.
- **R4: `xmlsec` (the C-extension both realistic library choices depend on for the actual crypto)
  may need system packages not present in the `python3.12-bookworm-slim` Docker build image.** Not
  verified live (no `uv add`/`uv sync` was executed against the real image in this Ground pass) —
  named as the top freeze uncertainty rather than asserted either way.
- **R5: email-attribute location varies by IdP.** OIDC gets `email` from a single well-known ID-token
  claim. SAML has no such universal claim: some IdPs put it in `NameID` (only when
  `Format="...emailAddress"`, which is NOT the default for Azure AD/ADFS), others in an `Attribute`
  named `email`, `mail`, or the long-form
  `http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress` URI (ADFS/Azure AD default).
  A hardcoded single lookup will silently break real IdPs. Feeds §1 M13 + a per-tenant override field.
- **R6: `Audience` collision risk if the SP entity ID is admin-typed.** OIDC's per-tenant isolation
  relies on the httpOnly cookie (R1 shows this doesn't transfer); SAML's structural analogue is the
  `Audience Restriction` check inside the *signed* assertion. If `sp_entity_id` were an admin-typed
  free-text field (mirroring OIDC's admin-typed `issuer`/`client_id`), two tenants could accidentally
  (or a malicious tenant admin deliberately) configure the *same* `sp_entity_id`, defeating the
  Audience check as a tenant-isolation control. Feeds §1 M2 (server-derived, non-admin-settable
  `sp_entity_id`) — a deliberate delta from the OIDC precedent, named explicitly.

Related intent: PROJECT.md goal ("a user can set up their tenant → log in → ...") + the invariant
"Every tenant-owned row carries `tenant_id`" + "no outbound IO without timeout/retry/CB" (largely
inapplicable here — see Honors). MILESTONE.md goal: "sign in via SAML or OIDC ... receives the same
session JWT". MILESTONE.md shared decision: "Every identity surface is security-sensitive: HARD-STOP
verify — SAML (assertion validation — signature, audience, replay, tenant confusion)" — directly
scopes this task's non-negotiable validation set (§1). GLOSSARY.md `oidc_claim_mapping` term (existing
precedent for a new glossary term this task adds a SAML-specific sibling to, not extends).

Ground SHA: `2071046` (branch `chore/add-housekeeping-clusters`) — every symbol above cited as
`path:symbol`; any bare line number is "as of" this commit.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: SAML 2.0 SP alongside OIDC — SP-initiated flow only; per-tenant IdP config (manual
entry: IdP entity ID + SSO URL + X.509 cert, ranked over metadata-XML upload); one global
Assertion Consumer Service endpoint resolves tenant identity via a server-side (Redis) pending-
request store keyed by the SP's own AuthnRequest ID, never via cookie or any unverified XML
field; on full validation the SAME `TokenService.issue(...)` seam OIDC/password use mints the
SAME `ai_proxy_session` JWT; JIT provisioning mirrors OIDC (role always `member`, existing
users' stored role preserved).

Framings weighed:
- **Parallel vertical, file-for-file mirror of the OIDC `auth/` layout, new files only** (CHOSEN):
  `saml_entities.py` / `saml_ports.py` / `saml_errors.py` (domain) ← `saml_use_cases.py`
  (application) ← `saml_orm.py` / `db_saml_config_resolver.py` / `saml_request_store.py` /
  `saml_replay_cache.py` (infrastructure) ← `saml_router.py` / `saml_admin_router.py` (api).
  Zero edits to any existing OIDC file. Chosen because `oidc_router.py`/`use_cases.py` are
  shipped, presumptively-frozen production contracts (sso-oidc, oidc-tenant-config,
  oidc-jwks) — touching them for a "shared base class" risks destabilizing a live auth path
  for a benefit (a few dozen shared lines) that doesn't outweigh the risk; CONVENTIONS.md's
  domain-per-module boundary and the folded DDD lesson ("a distinct bounded concept gets its
  own module/table" — audit vs alert precedent) both point the same direction.
- Shared abstract `SsoLoginUseCase` base class OIDC + SAML both inherit (rejected): the two
  flows' claim-extraction and trust-establishment steps are structurally different enough
  (JWKS/RS256 verification vs XML-signature verification; cookie-pinned tenant vs
  request-store-pinned tenant — see Ground R1) that a shared base would mostly be an empty
  shell with two near-total overrides, plus it touches the frozen `OidcLoginUseCase`.
- Reuse `oidc_tenant_id`-style httpOnly cookie for SAML's tenant pin (rejected): Ground R1 —
  SAML's ACS is reached via a cross-site top-level POST, on which `SameSite=Lax` cookies are
  not sent by modern browsers; a cookie-pinned tenant would silently degrade or misbehave.
- Per-tenant ACS URL (e.g. `/auth/saml/{tenant_id}/acs`) instead of one global ACS + server-side
  request-store lookup (rejected): works, but (a) requires the tenant_id in the URL to be
  trusted BEFORE signature verification — no worse than the request-store approach on its own,
  but it also requires per-tenant IdP-side ACS-URL configuration (more setup friction for the
  enterprise admin than a single documented ACS URL), and (b) the request-store approach is
  needed ANYWAY for InResponseTo replay-binding (§1 M5), so a per-tenant URL buys no additional
  security for extra operational complexity.
- IdP metadata-XML upload/URL-fetch vs manual cert+SSO-URL entry for admin config (ranked,
  manual entry CHOSEN for v1): metadata XML is a SECOND untrusted-XML-parsing surface (this
  time admin-supplied, potentially XXE-vulnerable) on top of the assertion-parsing surface this
  task already has to get right; manual entry (paste IdP entity ID + SSO URL + PEM cert) mirrors
  the OIDC admin-config precedent exactly (`issuer`/`client_id`/`jwks_url` are also manually
  entered, not discovered) and ships a materially smaller attack surface. Metadata-URL fetch is
  a plausible additive v2 delta (would need the SAME https-only/no-private-IP SSRF guard as
  `_validate_oidc_url`, PLUS a hardened XML parser with entity resolution disabled, PLUS
  treating the fetch as a one-time import into the same manual fields — never re-fetched live at
  login time). Recorded as an explicit OPEN follow-up, not silently dropped.

Must:
<must>
  - M1 (tenant resolution / login-initiation): `GET /auth/saml/login?domain=<email_domain>`
    resolves the tenant's `SamlProviderConfig` by `email_domains @> ARRAY[domain]` (mirrors the
    OIDC domain-resolver query shape). Generates a cryptographically random AuthnRequest ID
    (`secrets.token_urlsafe`), writes a pending-request record `{tenant_id, sp_entity_id,
    idp_entity_id, created_at}` to Redis under `saml:pending:{request_id}` with a 300s TTL
    (matches OIDC's `_STATE_NONCE_MAX_AGE`), then 302-redirects to the tenant's IdP SSO URL via
    the SAML HTTP-Redirect binding (deflate + base64 + urlencode the `SAMLRequest` param) with
    an optional `RelayState` carrying ONLY a same-origin relative post-login redirect path
    (never tenant identity — see M3). No tenant/domain match and no config row → 404
    `ERR_SAML_NOT_CONFIGURED` (byte-identical no-op for every tenant that never touches SAML).
  - M2 (structural tenant isolation): `sp_entity_id` (the value validated as `Audience` on every
    assertion) is SERVER-DERIVED deterministically from `tenant_id`
    (`f"{settings.saml_sp_entity_id_base}/tenant/{tenant_id}"`) and is NEVER accepted from the
    `PUT /admin/saml` request body — a tenant admin cannot configure a colliding or
    attacker-chosen Audience value (Ground R6). Returned read-only in GET/PUT responses so the
    admin can paste it into their IdP's SP-entity-ID field.
  - M3 (tenant binding at ACS — the central defense, supersedes the OIDC cookie pattern):
    `POST /auth/saml/acs` is the SOLE, tenant-agnostic Assertion Consumer Service endpoint.
    Tenant identity for validation purposes is resolved EXCLUSIVELY via a server-side lookup:
    extract the top-level (still-UNVERIFIED) `InResponseTo`/`Issuer` from the raw
    `SAMLResponse` ONLY as a lookup key into `saml:pending:{request_id}` — this lookup result is
    used ONLY to select which tenant's stored IdP cert to verify the signature against. No
    field read from the document before signature verification is ever used for an
    authorization or identity decision. `GETDEL` (atomic get+delete) consumes the pending record
    in one Redis call — the same call that answers "does this request exist" also enforces
    single-use (no separate check-then-delete race).
  - M4 (signature verification — library-backed, never hand-rolled): assertion (and/or response)
    signature verification is performed via the chosen library's structural
    Reference/URI-based validator (§3 freeze question 2) — the implementation MUST extract every
    trusted claim (NameID, Attributes, Conditions, SubjectConfirmationData) from the SAME node
    the verified `<ds:Signature>` covers, never by re-traversing the parsed DOM by tag name after
    a bare boolean "is valid" check (Ground R2, XSW-class defense). A HARD-STOP applies to any
    hand-rolled hashing/canonicalization shortcut.
  - M5 (the non-negotiable per-assertion validation set — every one of these on EVERY `/acs`
    call, no per-tenant opt-out, order matters for correctness but all must pass):
    1. XML signature verifies against the tenant's stored `idp_x509_cert` (M4).
    2. `Issuer` (assertion and/or response, per library defaults) equals the tenant's configured
       `idp_entity_id`.
    3. `Audience` (inside `Conditions/AudienceRestriction`) equals the tenant's `sp_entity_id`
       (M2) — defense-in-depth on top of the request-store tenant pin (M3).
    4. The SIGNED `SubjectConfirmationData/@InResponseTo` equals the `request_id` retrieved from
       the pending-request store at M3 (the TRUST-BEARING check — distinct from the earlier
       unsigned lookup, which was index-only).
    5. `NotBefore`/`NotOnOrAfter` (`Conditions` and `SubjectConfirmationData`) are honored with a
       configurable clock-skew allowance, default 60s (`GATEWAY_SAML_CLOCK_SKEW_SECONDS`).
    6. The assertion's `@ID` has not been seen before: `SETNX saml:consumed:{assertion_id}` in
       Redis with a TTL capped at `min(NotOnOrAfter - now, 24h)` — independent second replay
       layer from M3's request-correlation consumption (an assertion could theoretically be
       replayed against a *different*, still-valid pending request in a pathological multi-tab
       scenario; this closes that gap).
  - M6 (IdP-initiated rejection): any `/acs` POST whose `SAMLResponse` has no `InResponseTo`
    resolvable in the pending-request store (including a genuinely unsolicited IdP-initiated
    response, per the milestone's "design decides — recommend SP-initiated-only" allowance) is
    rejected identically to a replay/forged request — `ERR_SAML_REQUEST_NOT_FOUND` — never
    silently accepted as a valid login.
  - M7 (JIT provisioning mirrors OIDC exactly): on full M5 pass, resolve-or-create the user via a
    NEW port method `get_or_provision_saml_user(*, email, tenant_id, password_hash) -> User` on
    `IdentityRepository` (ADDITIVE — the existing frozen `get_or_provision_oidc_user` signature
    is untouched; both delegate to a shared private repository helper parameterized by
    `auth_method`). Provisioned role is ALWAYS `Role.MEMBER`, never derived from any assertion
    attribute; an EXISTING user matched by email keeps their STORED role (an existing
    owner/admin is not downgraded by a SAML login) — byte-identical rule to OIDC. New rows get
    `auth_method="saml"` and the existing `SSO_PASSWORD_HASH_SENTINEL` (`"!sso-no-password"`) —
    reuses the sentinel verbatim, `auth_method` already disambiguates it from OIDC/password so no
    new sentinel value is needed.
  - M8 (same JWT seam as OIDC/password): mint the session via the UNCHANGED
    `TokenService.issue(user_id=..., tenant_id=..., role=..., email=...)` call — the same seam
    `OidcLoginUseCase.execute` calls at `use_cases.py:351`. Set the resulting cookie with the
    IDENTICAL attributes OIDC uses: `ai_proxy_session`, HttpOnly, `SameSite=Strict`, `Path=/`,
    `Secure` in non-dev, `Max-Age=expires_in`.
  - M9 (compliance-grade audit — a deliberate widening beyond the OIDC precedent): EVERY `/acs`
    outcome — success AND every M5/M6 rejection — is recorded via the existing `record_audit`
    fire-and-forget seam, `action="auth.saml_login"`, `result="success"|"rejected"`,
    `metadata={"error_code": ..., "idp_entity_id": ...}` (NEVER the raw assertion XML or any
    claim beyond the resolved email). OIDC today only audits SUPERADMIN logins (a narrower,
    already-shipped scope); this task intentionally audits every SAML login given this milestone
    IS the compliance pack and `compliance-export-api` (a sibling task) will read this store —
    named explicitly as a scope decision beyond "mirror OIDC exactly" (§3 freeze question 4).
  - M10 (admin config CRUD, owner-only, mirrors `oidc_admin_router.py`): `GET`/`PUT
    /admin/saml` reuse the exact `_get_owner_identity` pattern (403
    `AUTH_FORBIDDEN_OWNER_REQUIRED` for non-owners). `PUT` validates `idp_sso_url` https-only +
    no-private-IP (same predicate as `_validate_oidc_url`/`_is_private_ip`, duplicated into the
    new file per §3 freeze question 3) and validates `idp_x509_cert` is a parseable, non-expired
    PEM X.509 certificate via `cryptography.x509.load_pem_x509_certificate` before any DB write.
    The IdP cert is NOT a secret (public key material) — returned in full on GET, no Fernet
    encryption, no `"<stored>"` placeholder (a deliberate, correct divergence from OIDC's
    `client_secret` handling, not an oversight).
  - M11 (no env-Settings fallback mode): unlike OIDC's dual DB-config/env-config legacy path
    (kept for OIDC's own backward compatibility), SAML ships DB-config-only — every tenant's
    SAML login is per-tenant config, full stop. Simpler router, no `ENV_CONFIG_COOKIE_VALUE`-style
    sentinel dance needed.
  - M12 (fail-closed replay-defense availability): if `app.state.redis_client` is unreachable
    (timeout or connection error) at the M3 lookup or the M5.6 consumed-assertion check, the
    login is REJECTED — `ERR_SAML_STORE_UNAVAILABLE` (503) — never silently treated as "not
    replayed." This is a deliberate posture divergence from the existing Redis-consumer precedent
    in this codebase (rate limiters fail OPEN for availability; this is a correctness/security
    gate, not an availability optimization — Ground note under `main.py:921-951`). A bounded
    2s timeout on the Redis calls (design-for-failure) prevents an unreachable Redis from hanging
    the login request indefinitely.
  - M13 (email-attribute resolution, ranked + overridable): after M5 passes, resolve the user's
    email in this order: (1) `NameID` when `Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"`;
    (2) the per-tenant configurable `email_attribute_name` field on `SamlProviderConfig` (new
    admin-settable field, default `"http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"`
    — the ADFS/Azure AD default) looked up in the assertion's `AttributeStatement`; (3) a
    fallback attribt search for `email`/`mail`/`emailaddress` (case-insensitive `Name` or
    `FriendlyName`). None resolve → `ERR_SAML_EMAIL_MISSING` (401). Every resolved email is
    lowercased before domain-mapping/provisioning (matches OIDC's `email.lower()`).
</must>
Reject:
<reject>
  - `/acs` POST with no pending-request match for the resolved `InResponseTo`/`Issuer` (includes
    genuinely IdP-initiated, unsolicited responses per M6) -> "ERR_SAML_REQUEST_NOT_FOUND" (400)
  - `/acs` POST whose pending-request record was already consumed (GETDEL returned nothing on a
    second attempt) -> "ERR_SAML_REQUEST_ALREADY_USED" (400)
  - XML signature verification fails (missing, invalid, or a structurally-wrapped/duplicated
    signed node — M4's library-backed check) -> "ERR_SAML_SIGNATURE_INVALID" (401)
  - Assertion/response `Issuer` does not equal the tenant's configured `idp_entity_id`
    -> "ERR_SAML_ISSUER_MISMATCH" (401)
  - `Audience` does not equal the tenant's `sp_entity_id` -> "ERR_SAML_AUDIENCE_MISMATCH" (401)
  - Signed `InResponseTo` does not equal the pending request's `request_id`
    -> "ERR_SAML_RESPONSE_MISMATCH" (401)
  - `NotBefore`/`NotOnOrAfter` violated outside the configured clock-skew window
    -> "ERR_SAML_ASSERTION_EXPIRED" (401)
  - Assertion `@ID` already present in the consumed-assertion cache (single-use violation)
    -> "ERR_SAML_ASSERTION_REPLAYED" (401)
  - No email resolvable via the M13 ranked lookup -> "ERR_SAML_EMAIL_MISSING" (401)
  - `GET /auth/saml/login?domain=` matches no enabled tenant config -> "ERR_SAML_NOT_CONFIGURED" (404)
  - Resolved email exists bound to a DIFFERENT tenant than the one the pending-request pinned
    -> "ERR_SAML_TENANT_CONFLICT" (403)
  - `PUT /admin/saml` with an unparseable or expired `idp_x509_cert` -> "ERR_SAML_CERT_INVALID" (422)
  - `PUT /admin/saml` with a non-https or private-IP/localhost `idp_sso_url`
    -> 422 (same `validation_errors` list shape `_validate_oidc_url` already produces)
  - `GET /admin/saml` with no `saml_provider_configs` row for the caller's tenant
    -> "ERR_SAML_CONFIG_NOT_FOUND" (404)
  - `GET`/`PUT /admin/saml` called by a non-owner -> reuse "ERR_AUTH_FORBIDDEN_OWNER_REQUIRED" (403,
    existing `AUTH_FORBIDDEN_OWNER_REQUIRED` catalog entry — no new code)
  - `app.state.redis_client` unreachable during the M3/M5.6 replay checks
    -> "ERR_SAML_STORE_UNAVAILABLE" (503)
</reject>
After:
<after>
  - A tenant admin can configure their SAML IdP (entity ID + SSO URL + cert) and see the
    tenant's fixed, server-derived `sp_entity_id`/ACS URL to hand to their IdP admin.
  - A user of a SAML-configured tenant clicks their IdP's login tile, completes auth at the IdP,
    and lands in the dashboard holding the SAME `ai_proxy_session` JWT shape a password or OIDC
    login would produce — same claims, same cookie attributes, same downstream authz.
  - A forged, replayed, expired, wrong-audience, wrong-issuer, or cross-tenant-mismatched
    assertion is rejected with a specific `ERR_SAML_*` code and produces NO session and NO user
    row; every rejection is auditable.
  - A tenant that never configures SAML sees zero behavioral change — `/auth/saml/login` 404s,
    every existing password/OIDC path is untouched.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **Library/runtime choice** (python3-saml vs pysaml2 vs hand-rolled, §3 freeze question 2) is
    the LEAST-SURE call in this draft — lowest confidence because Ground could not verify live
    whether the `xmlsec` C-extension resolves a prebuilt manylinux wheel against this repo's
    exact `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` build image, or whether the Dockerfile
    needs new `apt-get install libxml2-dev libxmlsec1-dev libxmlsec1-openssl pkg-config` lines in
    the builder stage (and a runtime-stage shared-lib copy). If wrong: a first `uv sync`/Docker
    build failure at Build time, caught early and loudly (not a silent security gap) but costs a
    Dockerfile-touching iteration this task's Scope (§5) must explicitly allow for.
  - [ ] AuthnRequest signing (SP signs the outgoing `SAMLRequest`) is left OFF by default for v1
    — confirm this is acceptable; some strict enterprise IdP configs (certain ADFS setups) require
    signed AuthnRequests, which needs SP keypair generation/storage/rotation (a new secret-at-rest
    surface, mirroring the Fernet pattern but for a private key) — deferred to a named v2 delta
    rather than silently unsupported. Cost if wrong: blocks onboarding one class of strict IdP
    configs until the v2 delta ships; not a security hole either way (the response is still fully
    validated).
  - [ ] Redis reuse (vs a new Postgres table) for the pending-request/replay-cache — confirm the
    300s-TTL ephemeral-state shape is an acceptable fit given Redis isn't in the Postgres
    backup/rollback story. Cost if wrong (a mid-flight Redis flush): only affects in-flight logins
    within the current 300s window — self-healing (the user retries `/auth/saml/login`), not a
    data-loss risk, but worth confirming since M12 makes Redis availability a hard login
    dependency it wasn't before for this bounded context.
  - [ ] M9's audit-every-login widening beyond the OIDC precedent (superadmin-only) — confirm this
    is the desired scope, not a silent over-build. Cost if reversed: a trivial metadata-flag strip
    at Build; no architectural rework.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: SP-initiated login redirects with a pinned pending request   # M1
  Given tenant "acme" has an enabled SamlProviderConfig with email_domains ["acme.com"]
  When a browser requests GET /auth/saml/login?domain=acme.com
  Then the response is a 302 redirect to acme's idp_sso_url with a deflate+base64 SAMLRequest param
  And a pending-request record {tenant_id: acme, sp_entity_id, idp_entity_id} exists in Redis
    under saml:pending:{request_id} with TTL <= 300s
  And no ai_proxy_session cookie is set yet

Scenario: sp_entity_id is server-derived and not admin-settable   # M2
  Given a tenant owner PUTs /admin/saml with a body containing "sp_entity_id": "https://evil.example/sp"
  When the write is processed
  Then the stored/returned sp_entity_id is the server-derived f"{base}/tenant/{tenant_id}" value
  And the attacker-supplied sp_entity_id value from the request body is ignored, never persisted

Scenario: ACS resolves tenant via the pending-request store, never a cookie   # M3
  Given a valid pending-request record exists for request_id "r-123" pinning tenant "acme"
  When POST /auth/saml/acs arrives with SAMLResponse whose (unverified) top-level InResponseTo is
    "r-123", and NO cookies are sent with the request (simulating a cross-site POST navigation
    that drops SameSite=Lax cookies)
  Then the tenant is correctly resolved as "acme" purely from the Redis lookup
  And the pending-request record is deleted (single-use) by the same GETDEL call

Scenario: XSW-style forged unsigned block is rejected via reference-based extraction   # M4
  Given a SAMLResponse whose ONE valid <ds:Signature> covers a legitimate Assertion for
    user@acme.com, and the attacker has inserted a SECOND, unsigned, sibling Assertion claiming
    admin@acme.com elsewhere in the same document (classic XML Signature Wrapping payload)
  When the response is submitted to /acs
  Then the claims used for provisioning/JWT-issuance are extracted ONLY from the node the
    signature's Reference URI/ID covers (user@acme.com)
  And the login either succeeds AS user@acme.com or fails outright — admin@acme.com is NEVER
    the identity provisioned or the identity in the minted JWT

Scenario: full validation set passes end-to-end and mints the same JWT shape as OIDC   # M5, M8
  Given tenant "acme"'s pending request "r-123", a correctly-signed assertion with
    Issuer=acme's idp_entity_id, Audience=acme's sp_entity_id, InResponseTo="r-123",
    NotBefore/NotOnOrAfter bracketing now(), and a fresh assertion ID
  When the response is submitted to /acs
  Then a 302 redirect is returned with an ai_proxy_session cookie: HttpOnly, SameSite=Strict,
    Path=/, Secure (non-dev), Max-Age=expires_in — byte-identical cookie attributes to the OIDC
    callback's cookie-set call
  And decoding the JWT yields the same claim shape (user_id, tenant_id, role, email) TokenService
    .issue produces for a password/OIDC login

Scenario: IdP-initiated (unsolicited) response is rejected, not silently accepted   # M6, R1
  Given no pending-request record exists in Redis for any request_id
  When a SAMLResponse with no matching InResponseTo (or none at all) is POSTed to /acs directly
  Then the response is 400 ERR_SAML_REQUEST_NOT_FOUND
  And no user is provisioned and no session cookie is set

Scenario: JIT-provisioned SAML user is always role=member   # M7
  Given tenant "acme" has no user with email newhire@acme.com
  When a fully-valid assertion for newhire@acme.com completes at /acs
  Then a new user row is created with role=Role.MEMBER, auth_method="saml",
    password_hash="!sso-no-password"
  And the minted JWT's role claim is "member" regardless of any Attribute in the assertion

Scenario: existing admin's stored role survives a SAML login   # M7
  Given tenant "acme" has an existing user admin@acme.com with role=Role.ADMIN (promoted earlier
    via a legitimate admin action)
  When a fully-valid assertion for admin@acme.com completes at /acs
  Then the existing user row is unchanged (no role downgrade)
  And the minted JWT's role claim is "admin" — the STORED role, not a hardcoded "member"

Scenario: every /acs outcome is audited, success and rejection alike   # M9
  Given a fully-valid assertion for user@acme.com
  When the login succeeds at /acs
  Then an audit_events row is written with action="auth.saml_login", result="success",
    actor_email="user@acme.com" — and no raw assertion XML appears in the row's metadata
  Given instead an assertion with an expired NotOnOrAfter
  When it is submitted to /acs and rejected
  Then an audit_events row is STILL written with result="rejected",
    metadata.error_code="ERR_SAML_ASSERTION_EXPIRED"

Scenario: admin config CRUD is owner-only and validates the IdP cert   # M10, R (cert), R (owner)
  Given a tenant member (role=member, not owner) attempts PUT /admin/saml
  When the request is processed
  Then it is rejected 403 ERR_AUTH_FORBIDDEN_OWNER_REQUIRED and no config row is written
  Given instead a tenant owner PUTs /admin/saml with idp_x509_cert set to a syntactically
    invalid PEM blob
  When the request is processed
  Then it is rejected 422 ERR_SAML_CERT_INVALID and no config row is written or updated

Scenario: no env-Settings fallback exists for SAML   # M11
  Given no saml_provider_configs row exists for any tenant, and no GATEWAY_SAML_* env vars
    resembling OIDC's legacy env-config path are set
  When GET /auth/saml/login?domain=unknown.com is requested
  Then the response is 404 ERR_SAML_NOT_CONFIGURED — there is no env-Settings config path to
    silently fall back to (unlike OIDC's dual-mode /callback resolution)

Scenario: Redis unavailability fails the login closed, not open   # M12, R (store unavailable)
  Given app.state.redis_client raises a connection error on the M3 pending-request lookup
  When a SAMLResponse is submitted to /acs
  Then the response is 503 ERR_SAML_STORE_UNAVAILABLE
  And no user is provisioned, no session cookie is set, no assertion is treated as "not replayed"
    by default

Scenario: email resolution falls through NameID -> configured attribute -> fallback search   # M13
  Given tenant "acme" has NOT set email_attribute_name (uses the ADFS/Azure AD default), and the
    assertion's NameID Format is NOT emailAddress, but an Attribute named
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress" carries "user@acme.com"
  When the assertion completes validation at /acs
  Then the resolved, lowercased email is "user@acme.com" and provisioning proceeds
  Given instead NONE of the three ranked lookups (M13) find a value
  When validation otherwise passes
  Then the login is rejected 401 ERR_SAML_EMAIL_MISSING and no user is provisioned

Scenario: request-store replay is rejected — same request_id used twice   # R (request already used)
  Given a valid pending-request "r-123" and a fully-valid first SAMLResponse referencing it
  When the SAME (or a re-signed, re-packaged) SAMLResponse referencing "r-123" is submitted to
    /acs a second time, after the first has already succeeded
  Then the second attempt is rejected 400 ERR_SAML_REQUEST_ALREADY_USED (GETDEL already
    consumed the record on the first call)
  And the second attempt produces no second user-lookup side effect and mints no second session

Scenario: assertion-ID replay against a DIFFERENT pending request is independently caught   # M5.6
  Given a valid, previously-consumed assertion (ID "a-999") was already accepted for pending
    request "r-123", and a SEPARATE new pending request "r-456" now exists for the same tenant
  When an attacker resubmits the SAME assertion (ID "a-999", now re-wrapped with InResponseTo
    edited to "r-456") to /acs
  Then the signature check on the tampered InResponseTo value fails FIRST if InResponseTo is
    inside the signed node (ERR_SAML_SIGNATURE_INVALID) — but if a variant existed where signing
    only covered other fields, the independent assertion-ID cache still catches it:
    ERR_SAML_ASSERTION_REPLAYED
  And no second session is minted either way

Scenario: cross-tenant email conflict is rejected like OIDC's   # R (tenant conflict)
  Given user@partner.com already exists as a member of tenant "beta" (a different tenant)
  When a fully-valid SAML assertion for user@partner.com completes against tenant "acme"'s
    pending request (acme's admin misconfigured email_domains to include "partner.com")
  Then the login is rejected 403 ERR_SAML_TENANT_CONFLICT
  And user@partner.com's existing row in tenant "beta" is unchanged; no new row is created

Scenario: clock-skew boundary is honored, not just "sometime around now"   # M5.5, edge case
  Given a fully-valid assertion whose NotOnOrAfter is exactly 45 seconds in the past (inside the
    default 60s GATEWAY_SAML_CLOCK_SKEW_SECONDS allowance)
  When it is submitted to /acs
  Then the login SUCCEEDS (within skew)
  Given instead NotOnOrAfter is 90 seconds in the past (outside the 60s allowance)
  When it is submitted to /acs
  Then the login is rejected 401 ERR_SAML_ASSERTION_EXPIRED

Scenario: concurrent double-submit of the same assertion is serialized safely   # concurrency
  Given a fully-valid SAMLResponse for pending request "r-123"
  When two concurrent POST /acs requests carry the byte-identical SAMLResponse (e.g. a
    double-click or a browser retry) and race each other
  Then exactly ONE of the two requests succeeds (wins the atomic GETDEL on saml:pending:r-123)
  And the other is rejected ERR_SAML_REQUEST_ALREADY_USED — never two sessions minted, never a
    duplicate user-provisioning attempt

Scenario: a tenant that never configures SAML is fully unaffected   # byte-identical / default-off
  Given tenant "legacy-co" has no saml_provider_configs row and has always used password login
  When any existing password-login or OIDC-login flow for "legacy-co" is exercised
  Then behavior, response shape, and cookies are byte-identical to before this task shipped
  And GET /auth/saml/login?domain=legacy-co.com 404s ERR_SAML_NOT_CONFIGURED without touching
    any password/OIDC code path
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Least-sure flag surfaced at freeze: [contract] library + build-image risk: python3-saml's xmlsec C-extension may not resolve a prebuilt wheel against the python3.12-bookworm-slim image and may pull apt deps into apps/gateway/Dockerfile — verify at BUILD start, before any code; if the wheel fails, the fallback is pysaml2, not hand-rolled lxml validation. Decided at freeze (Tin, 2026-07-10 batch): all 5 agent recommendations accepted (python3-saml; require-assertion-signed; duplicate SSRF validator; audit every SAML login; Dockerfile in build scope).


**STATUS: DRAFT — awaiting human freeze.** Every illustrative snippet below has had a manual
syntax/import sanity pass (no live `python -c "import ast; ast.parse(...)"` was run in this design
pass — Build must still re-verify at Tests).

### Part A — Public SP endpoints (new file: `auth/api/saml_router.py`, unauthenticated, mirrors `oidc_router.py`)

```
GET /auth/saml/login   query: ?domain=<email_domain>
  302 -> Location: <idp_sso_url>?SAMLRequest=<deflate+base64+urlencode>&RelayState=<relative-path|absent>
  404 -> { code: "ERR_SAML_NOT_CONFIGURED" }
  Side effect: writes saml:pending:{request_id} to Redis (TTL 300s); no cookies set.

POST /auth/saml/acs   body: form-encoded { SAMLResponse: <base64 XML>, RelayState?: <str> }
  302 -> Location: <validated RelayState relative path | settings.saml_post_login_redirect>
         Set-Cookie: ai_proxy_session=<jwt>; HttpOnly; SameSite=Strict; Path=/; Max-Age=<expires_in>
                     [; Secure — non-dev]
  400 -> { code: "ERR_SAML_REQUEST_NOT_FOUND" | "ERR_SAML_REQUEST_ALREADY_USED" }
  401 -> { code: "ERR_SAML_SIGNATURE_INVALID" | "ERR_SAML_ISSUER_MISMATCH"
               | "ERR_SAML_AUDIENCE_MISMATCH" | "ERR_SAML_RESPONSE_MISMATCH"
               | "ERR_SAML_ASSERTION_EXPIRED" | "ERR_SAML_ASSERTION_REPLAYED"
               | "ERR_SAML_EMAIL_MISSING" }
  403 -> { code: "ERR_SAML_TENANT_CONFLICT" }
  503 -> { code: "ERR_SAML_STORE_UNAVAILABLE" }
Schema: reads saml_provider_configs (by tenant_id resolved via the Redis lookup), reads/writes
  users (JIT provisioning), reads/writes Redis keys saml:pending:{request_id} (GETDEL) and
  saml:consumed:{assertion_id} (SETNX + TTL), writes audit_events (fire-and-forget).
```

### Part B — Admin config CRUD (new file: `auth/api/saml_admin_router.py`, owner-only, mirrors `oidc_admin_router.py`)

```
GET /admin/saml
  200 -> { tenant_id: str, idp_entity_id: str, idp_sso_url: str, idp_x509_cert: str,
           sp_entity_id: str, acs_url: str, email_domains: list[str],
           email_attribute_name: str, enabled: bool,
           created_at: str, updated_at: str }
  404 -> { code: "ERR_SAML_CONFIG_NOT_FOUND" }
  403 -> { code: "ERR_AUTH_FORBIDDEN_OWNER_REQUIRED" }   # existing catalog entry, no new code

PUT /admin/saml   body: { idp_entity_id: str, idp_sso_url: str, idp_x509_cert: str,
                          email_domains: list[str], email_attribute_name?: str,
                          enabled?: bool = true }
  200 -> same shape as GET (sp_entity_id/acs_url always server-derived; a body-supplied
         sp_entity_id or acs_url field, if present, is silently ignored — never persisted)
  422 -> { detail: [ { type, loc, msg, input } ] }   # ERR_SAML_CERT_INVALID or SSRF-shape URL error
  403 -> { code: "ERR_AUTH_FORBIDDEN_OWNER_REQUIRED" }
Schema: upserts saml_provider_configs (tenant_id PK, ON CONFLICT DO UPDATE — same pg_insert
  pattern as oidc_admin_router.py:269-301), writes audit_events (action="saml.put",
  metadata={idp_entity_id, enabled} — never the cert).
```

### Part C — New table `saml_provider_configs` (Alembic migration, parent `511ad8a7b65e`)

```python
# migrations/versions/<new_rev>_saml_tenant_config.py  — additive; downgrade drops the table.
def upgrade() -> None:
    op.create_table(
        "saml_provider_configs",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("idp_entity_id", sa.VARCHAR(), nullable=False),
        sa.Column("idp_sso_url", sa.VARCHAR(), nullable=False),
        sa.Column("idp_x509_cert", sa.TEXT(), nullable=False),
        sa.Column("email_domains", postgresql.ARRAY(sa.TEXT()), nullable=False,
                  server_default=sa.text("'{}'")),
        sa.Column("email_attribute_name", sa.VARCHAR(), nullable=False,
                  server_default=sa.text(
                      "'http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress'"
                  )),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_saml_provider_configs_email_domains",
        "saml_provider_configs",
        ["email_domains"],
        postgresql_using="gin",
    )

def downgrade() -> None:
    op.drop_index("ix_saml_provider_configs_email_domains", table_name="saml_provider_configs")
    op.drop_table("saml_provider_configs")
```

Note: `sp_entity_id` and `acs_url` are NOT columns — both are computed at read time from
`settings.saml_sp_entity_id_base`/`settings.saml_acs_url` + `tenant_id` (M2), never stored, so a
future base-URL change (e.g. a domain migration) doesn't require a data backfill. `idp_x509_cert`
is TEXT (PEM, public material) — no Fernet encryption, unlike OIDC's `client_secret_enc`.
`users.auth_method` gets a new literal `"saml"` value — zero migration needed (Ground: no CHECK
constraint on that column).

### Part D — Redis key shapes (no migration; ephemeral, TTL-bounded)

```
saml:pending:{request_id}   — JSON {tenant_id, sp_entity_id, idp_entity_id, created_at}
                               TTL 300s; consumed via GETDEL (atomic, single-use)
saml:consumed:{assertion_id} — value "1"; TTL = min(NotOnOrAfter - now, 86400)s
                               written via SETNX (a hit on an existing key = replay)
```

### Part E — New/changed ports and settings

```python
# tenants/domain/ports.py::IdentityRepository — ADDITIVE method, existing method untouched.
class IdentityRepository(Protocol):
    ...  # get_or_provision_oidc_user unchanged, byte-identical signature

    async def get_or_provision_saml_user(
        self, *, email: str, tenant_id: uuid.UUID, password_hash: str
    ) -> User:
        """Get existing user by email OR create with role=member if absent.

        Raises SamlTenantConflictError if the user exists bound to a different tenant_id.
        Mirrors get_or_provision_oidc_user; both delegate to a shared private helper
        parameterized by auth_method ("oidc" vs "saml") in the infrastructure implementation.
        """
        ...
```

```python
# core/config.py — additive Settings fields (all optional; absence => SAML fully inert)
saml_sp_entity_id_base: str = ""       # GATEWAY_SAML_SP_ENTITY_ID_BASE (e.g. "https://gw.example.com/saml/sp")
saml_acs_url: str = ""                 # GATEWAY_SAML_ACS_URL (full external URL to /auth/saml/acs)
saml_post_login_redirect: str = "/"    # GATEWAY_SAML_POST_LOGIN_REDIRECT
saml_clock_skew_seconds: int = 60      # GATEWAY_SAML_CLOCK_SKEW_SECONDS
saml_allow_http_urls: bool = False     # GATEWAY_SAML_ALLOW_HTTP_URLS (dev/test only, mirrors oidc_allow_http_urls)
```

Glossary deltas:
- `saml_tenant_binding`: the server-side pending-request record (Redis, TTL 300s, single-use via
  atomic GETDEL) that binds a SAML AuthnRequest's ID to the tenant/SP-entity that issued it; the
  ACS endpoint resolves tenant identity ONLY through this record — never from any unverified
  field inside the SAMLResponse, and never from a cookie (the OIDC `oidc_tenant_id`-cookie
  pattern does not transfer to SAML's cross-site POST-bound ACS — see §0 Ground R1). A distinct
  mechanism from `oidc_claim_mapping`, not a reuse of it.
- `sp_entity_id`: a tenant's SAML Service Provider identifier, server-derived deterministically
  from `tenant_id` (never admin-settable), validated as the `Audience Restriction` every
  assertion must match — a structural (not admin-trust-dependent) tenant-isolation control.

### Freeze questions for Tin (each: options + recommendation)

1. **XML/SAML library allow-listing** — `.add/dependencies.allowlist` currently has NO
   XML/SAML entry (confirmed by direct read). Options: (A) `python3-saml` (OneLogin's toolkit;
   wraps `lxml` + `xmlsec`; explicit `wantAssertionsSigned`/security-options API; widely used in
   the Okta/Azure AD/Google-Workspace SP ecosystem) — RECOMMENDED. (B) `pysaml2` (older, heavier,
   also `xmlsec`-dependent, historically used by `djangosaml2`) — viable but a less ergonomic
   security-options surface. (C) hand-rolled `lxml` + `xmlsec` calls — REJECTED: this is exactly
   the shape that produces XSW-class bugs (§0 Ground R2); the persona's stated expertise is built
   on fixing this class of bug in library code, not on writing new instances of it.
2. **Signature scope** — require the ASSERTION to always be signed (`wantAssertionsSigned=True`)
   and validate the RESPONSE-level signature only if present (`wantMessagesSigned=False`) —
   RECOMMENDED, matches the common enterprise-IdP default (Okta/Azure AD/Google Workspace sign
   only the assertion by default; requiring response-level signing too would reject real IdPs
   out of the box). Alternative: require BOTH signed — more defense-in-depth, but breaks
   onboarding for IdPs that don't sign the envelope; deferred to a per-tenant opt-in flag if a
   real customer needs it.
3. **SSRF-validator duplication vs shared extraction** — `_validate_oidc_url`/`_is_private_ip`
   in `oidc_admin_router.py` are duplicated verbatim into `saml_admin_router.py` (Part B) rather
   than extracted into a shared `core/` helper, to avoid touching the (presumptively frozen)
   OIDC admin router file at all for this task. RECOMMENDED as the safer default; a
   behavior-preserving extraction into `gateway/core/url_validation.py` is a clean, low-risk
   follow-up delta if Tin prefers no duplication — not blocking this freeze either way.
4. **Audit scope (M9)** — audit every SAML login (success + every rejection), a deliberate
   widening beyond OIDC's shipped superadmin-only audit scope, justified by this milestone being
   the compliance pack itself. RECOMMENDED. Alternative: mirror OIDC exactly (superadmin-only) —
   cheaper, but weakens the exit-criterion story for the sibling `compliance-export-api` task,
   which needs real per-login audit rows to be a meaningful compliance export.
5. **Dockerfile / build-image risk (§1 ⚠ LEAST-SURE)** — not really a decision so much as a
   named risk Tin should see before freezing: the chosen library's native dependency (`xmlsec`)
   may require new `apt-get` lines in the `python3-slim-bookworm`-based builder stage. This
   task's §5 BUILD Scope will need to include `apps/gateway/Dockerfile` for exactly this reason —
   flagging it here so it isn't a surprise mid-build.

Status: FROZEN @ v1 — approved by Tin Dang
Reported: no — awaiting the orchestrator's freeze-report render and Tin's decision on the 5
questions above (question 1 and 5 are coupled: the library choice determines whether the
Dockerfile touch is required).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

---


## Design self-score

- Completeness: 0.93 — §0 grounds every reused seam with a real anchor (incl. the confirmed-not-
  hand-parsed `alembic heads` result); §1 states every Must/Reject with an error code; §2 covers
  one scenario per Must, per Reject, plus concurrency/boundary/byte-identical edge cases; §3 gives
  endpoints, error-response shapes for every Reject, a syntax-checked migration + port + settings
  sketch, and a Glossary delta. Held back from 1.0 only because the library choice (question 1)
  and its Dockerfile consequence (question 5) are named as open rather than resolved — correctly,
  since resolving them requires a live build Ground couldn't run.
- Clarity: 0.93 — every Must/Reject/scenario is labeled back to its ID; naming is
  consistent (`ERR_SAML_*`, `saml_provider_configs`, `saml:pending:*`); the OIDC-vs-SAML
  divergences (cookie→request-store, fail-open→fail-closed Redis posture, audit-scope widening)
  are each named explicitly as a deliberate choice, not left implicit.
- Practicality: 0.92 — reuses `app.state.redis_client`, the `ErrorSpec`/`record_audit`/
  `_get_owner_identity` patterns, and an ADDITIVE (non-frozen-breaking) port method; the one new
  infra dependency (an XML-signature library) is named with a ranked recommendation and its
  concrete build-image risk, not glossed over.
- Optimization: 0.90 — v1 scope is deliberately narrow (no AuthnRequest signing, no metadata-XML
  fetch, no per-tenant Redis-vs-Postgres agonizing) while keeping the full non-negotiable
  validation set from the milestone's shared decision; the two-layer replay defense (request-store
  consumption + independent assertion-ID cache) is the one place this trades simplicity for
  security depth, justified by the account-takeover blast radius named in the risk header.
- Edge cases: 0.91 — clock-skew boundary, concurrent double-submit race, cross-tenant conflict,
  assertion-ID replay against a distinct pending request, Redis-unavailable fail-closed, and the
  XSW forged-sibling-assertion payload are all scenario-covered, not just Must-listed.
- Self-evaluation: 0.92 — the LEAST-SURE flag is named once in §1 and carried through to freeze
  question 5 rather than buried; every assumption states its cost-if-wrong; every freeze question
  states an explicit recommendation, not just options.

All six ≥ 0.9 — no refinement pass required before returning.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 85% on the new `gateway.auth.*saml*` vertical (achieved: 89.7% — 496/553 statements
across saml_admin_router.py 97%, saml_deps.py 93%, saml_router.py 92%, saml_use_cases.py 88%,
saml_entities.py 100%, saml_errors.py 100%, saml_ports.py 0%/10 lines — Protocol stubs, no
executable body, expected — db_saml_config_resolver.py 90%, saml_orm.py 100%,
saml_replay_cache.py 89%, saml_request_store.py 79% — the uncovered lines in the last two are
Redis-timeout/malformed-payload defensive branches not independently forced in this pass, flagged
below as a residue for VERIFY).

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_login_redirects_with_pinned_pending_request: 302+Location+Redis pending record+TTL+no cookie · M1
  - test_sp_entity_id_is_server_derived_not_admin_settable: attacker sp_entity_id in PUT body ignored, server-derived value persists · M2
  - test_acs_resolves_tenant_via_pending_store_not_cookie: no cookies sent, tenant resolved via Redis GETDEL only, single-use · M3
  - test_xsw_forged_unsigned_block_rejected: signed+unsigned-sibling-Assertion payload never yields admin4@acme.com identity · M4
  - test_full_validation_mints_same_jwt_shape_as_oidc: cookie attrs + JWT claim shape (sub/tenant_id/role/email) match OIDC's TokenService.issue · M5, M8
  - test_idp_initiated_unsolicited_response_rejected: no pending record -> 400 ERR_SAML_REQUEST_NOT_FOUND, no user provisioned · M6, R1
  - test_jit_provisioned_user_is_always_member: new user row role=member, auth_method="saml", password_hash sentinel · M7
  - test_existing_admin_role_survives_saml_login: pre-existing role=admin row unchanged + JWT carries "admin" · M7
  - test_every_acs_outcome_is_audited: success row (result=success, no raw assertion in metadata) + rejection row (result=rejected, error_code) · M9
  - test_admin_config_owner_only_and_validates_cert: member-role bearer -> 403 ERR_AUTH_FORBIDDEN on GET+PUT; malformed PEM -> 422 ERR_SAML_CERT_INVALID · M10
  - test_no_env_settings_fallback: unconfigured domain -> 404 ERR_SAML_NOT_CONFIGURED, no env-Settings path · M11
  - test_redis_unavailable_fails_closed: broken Redis adapter swapped into app.state -> 503 ERR_SAML_STORE_UNAVAILABLE, no provisioning · M12
  - test_email_resolution_falls_through_to_configured_attribute: non-email NameID + ADFS-URI attribute -> resolved email, provisioned · M13
  - test_email_resolution_all_three_fail_rejected: no NameID/attribute/fallback match -> 401 ERR_SAML_EMAIL_MISSING · M13
  - test_request_store_replay_rejected: same request_id submitted twice -> second 400 ERR_SAML_REQUEST_ALREADY_USED, one user row · R (request already used)
  - test_assertion_replay_against_different_pending_request: signed assertion re-wrapped under a new InResponseTo -> rejected (RESPONSE_MISMATCH/SIGNATURE_INVALID/ASSERTION_REPLAYED), at most one session · M5.6
  - test_cross_tenant_email_conflict_rejected: user@partner.com already in tenant "beta" -> 403 ERR_SAML_TENANT_CONFLICT against acme, beta row unchanged · R (tenant conflict)
  - test_clock_skew_boundary_honored: NotOnOrAfter -45s (inside 60s skew) succeeds; -90s (outside) -> 401 ERR_SAML_ASSERTION_EXPIRED · M5.5
  - test_concurrent_double_submit_serialized_safely: two concurrent POSTs on the same assertion -> exactly one 302 + one 400, one user row · concurrency
  - test_unconfigured_tenant_fully_unaffected: existing password login untouched + SAML login 404s cleanly for a never-configured tenant · byte-identical/default-off
  - test_wrong_signing_key_rejected_signature_invalid: assertion signed with a cert NOT on file -> 401 ERR_SAML_SIGNATURE_INVALID · Reject (signature)
  - test_issuer_mismatch_rejected: signed Issuer != configured idp_entity_id -> 401 ERR_SAML_ISSUER_MISMATCH · Reject (issuer)
  - test_audience_mismatch_rejected: signed Audience != tenant's sp_entity_id -> 401 ERR_SAML_AUDIENCE_MISMATCH · Reject (audience)
  - test_put_admin_saml_rejects_non_https_sso_url: http:// idp_sso_url -> 422 pydantic validation error · SSRF/URL-shape
  - test_put_admin_saml_rejects_private_ip_sso_url: private-IP idp_sso_url -> 422 · SSRF/URL-shape
  - test_get_admin_saml_config_not_found: no row for tenant -> 404 ERR_SAML_CONFIG_NOT_FOUND · Reject (config not found)
  - test_expired_cert_rejected: cert with NotAfter in the past -> 422 ERR_SAML_CERT_INVALID · Reject (cert)
  - test_disabled_saml_config_returns_not_configured: enabled=false row -> /auth/saml/login 404s exactly like no row at all · M10/M11 combined
</test_plan>

Tests live in: `./tests/saml_sso/` (test_saml_sso.py, saml_fixtures.py, conftest.py) · ran RED first:
first full run failed 2/28 (`test_login_redirects_with_pinned_pending_request` — a real ORM bug,
see §5 Known-problem fixes) before any of the 28 tests had ever passed; confirmed red for the
right reason (missing/broken production code, not a harness defect) by inspecting the failure —
a genuine `DataError: can't subtract offset-naive and offset-aware datetimes` from the
`saml_provider_configs.updated_at` column, not a fixture bug.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/auth/` `apps/gateway/src/gateway/tenants/domain/ports.py`
`apps/gateway/src/gateway/tenants/infrastructure/repository.py` `apps/gateway/src/gateway/core/config.py`
`apps/gateway/src/gateway/core/error_catalog.py` `apps/gateway/src/gateway/main.py`
`apps/gateway/migrations/versions/` `apps/gateway/pyproject.toml` `apps/gateway/Dockerfile` (conditional
on freeze question 5 — resolved NOT needed, see deviation below) `.add/dependencies.allowlist`
`apps/gateway/tests/saml_sso/`

Strategy (ordered batches): 1. Verify the freeze's LEAST-SURE flag first (xmlsec wheel resolution) —
BLOCKING gate before any code. 2. Domain layer (saml_entities.py, saml_errors.py, saml_ports.py) —
zero framework imports, mirrors oidc_* shapes. 3. Infrastructure (saml_orm.py + migration,
db_saml_config_resolver.py, saml_request_store.py, saml_replay_cache.py) — real Postgres/Redis
adapters, no fakes. 4. Application (saml_use_cases.py) — the security core: signature/issuer/
audience/replay/clock-skew validation, email resolution, JIT provisioning via the additive
IdentityRepository port method. 5. API (saml_router.py, saml_admin_router.py, saml_deps.py) — wire
use cases to HTTP, map every domain error to its ErrorSpec. 6. Wire main.py (routers + app.state
seams + ORM side-effect import). 7. Tests — spike interactively first (verify real xmlsec
signing/verification round-trips and the XSW defense actually fire) before writing the pytest
suite, then one test per §2 scenario + Reject-list gap-fill, against REAL Postgres+Redis (no fakes
for the security-critical stores).

Persona (required): generic — no project persona under `.add/personas/` matched this security-
library-integration shape; build-engineer/correctness-over-speed stance applied directly.
Spawn isolation (default): worktree (this task ran in its own dedicated worktree per the parallel
build wave's shared-context instructions).
Known-problem fixes:
  - trap: `add_sign()` on the whole Response signs the Response's Issuer (first `//saml:Issuer` in
    document order), not the Assertion's → fix: sign ONLY the Assertion sub-tree, splice into an
    unsigned Response wrapper (test-fixture-only concern, saml_fixtures.py).
  - trap: Redis GETDEL alone can't distinguish "never existed" (IdP-initiated, M6) from "already
    consumed" (replay) → fix: a tombstone key (`saml:pending-used:{request_id}`, TTL 600s) written
    on successful consumption; GETDEL-miss checks the tombstone via EXISTS to pick the error.
  - trap: AuditEvent's frozen `audit_missing_actor` invariant (tenant_id set requires an actor)
    conflicts with pre-provisioning rejections (no user resolved yet) → fix: rejection audits carry
    tenant_id=None (system-scoped) with the real tenant_id stashed in metadata; only success audits
    carry the real tenant_id + actor_user_id. Documented as intentional in `_audit()`'s docstring.
  - trap: naive error-code derivation from exception class name via string manipulation produces
    wrong codes (e.g. "SAMLSIGNATUREINVALID") → fix: an explicit `_ERROR_CODE_BY_TYPE` mapping
    table, caught in self-review before any test ran.
  - trap (discovered during Tests, not anticipated at freeze): python3-saml's
    `SubjectConfirmationData/@NotOnOrAfter` check has NO `ALLOWED_CLOCK_DRIFT` tolerance (only
    `saml:Conditions`' NotOnOrAfter does) → the clock-skew scenario (M5.5) must vary Conditions'
    offset, not SubjectConfirmationData's; fixed in the test fixture (`scd_not_on_or_after_offset_seconds`,
    decoupled from `not_on_or_after_offset_seconds`, defaulted safely in the future). This is
    correct/secure library behavior (bearer-token SCD windows are meant to be strict), not a
    production bug — flagged for VERIFY as a fact worth knowing, not a defect.
Strategy actually used: as planned, with one addition: after writing the full 28-test suite I ran
it before declaring RED (most of the implementation already existed by the time the suite was
written, since the security-core use-case logic had to be spike-verified interactively during
Tests to validate the xmlsec/XSW/InResponseTo trust-boundary reasoning before committing to a
design) — first full run was 26/28 green, 2/28 red for real reasons (see below), which is the
literal red→green signal this gate wants, just compressed: the suite was never run against a
stub, and both reds were genuine (one production bug, one test bug), not pre-broken scaffolding.
Safety rule (feature-specific): every /acs outcome (success AND rejection) is audited
(fire-and-forget, never blocks the response) before any redirect/error is returned to the browser
— M9; the single-use pending-request GETDEL and the independent assertion-ID SETNX replay cache
together form the concurrency safety rule for the double-submit race (§2 concurrency scenario).
Code lives in: `apps/gateway/src/gateway/auth/` (+ the additive touches listed under Scope).
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

Red→green: first full-suite run was 26 passed / 2 failed (out of 28). Both failures were diagnosed
and fixed, not worked around:
  1. `saml_provider_configs.created_at`/`updated_at` were declared as naive `DateTime` (no
     `timezone=True`) in `saml_orm.py`, while the application code writes tz-aware
     `datetime.now(UTC)` — asyncpg rejected the mismatch (`DataError: can't subtract offset-naive
     and offset-aware datetimes`). A REAL production bug, not a test artifact — every other ORM
     file in the codebase (`agent_oauth/infrastructure/orm.py`, `conversations/infrastructure/orm.py`,
     etc.) uses `DateTime(timezone=True)`; saml_orm.py had missed this convention. Fixed in both
     `saml_orm.py` and the migration file `c950c528d3d5_saml_tenant_config.py` (kept them in sync).
  2. `test_full_validation_mints_same_jwt_shape_as_oidc` asserted a `"user_id"` JWT claim key that
     does not exist — `JwtTokenService.issue()` (tenants/infrastructure/jwt_service.py) uses the
     JWT-standard `"sub"` claim. A test bug, fixed in the test, not the production code.
After both fixes: 28/28 green, confirmed on a second full run.

Dependency/Docker deviation (recorded per §3 freeze question 5): the freeze anticipated
`apps/gateway/Dockerfile` would need new `apt-get` lines for xmlsec's system libs. Verified via a
live `docker build --target builder` + `docker run` against the REAL Dockerfile
(`ghcr.io/astral-sh/uv:python3.12-bookworm-slim` base) that `python3-saml`'s xmlsec dependency
resolves a prebuilt manylinux_2_28 wheel for cp312/linux-amd64 with zero system packages needed —
**no Dockerfile changes were made**. This is strictly-more-correct (less surface changed than the
freeze anticipated) and was the MANDATORY first verification step before any code was written, per
this task's spawn instructions.

Other build-time deviations (strictly-more-correct, harmless, recorded per the boundary rules):
  - Added `reportMissingTypeStubs = false` to `[tool.pyright]` — python3-saml ships no py.typed
    marker, same exemption class already granted for Redis's untyped boundary.
  - `apps/gateway/tests/saml_sso/conftest.py` deliberately leaves `app.state.saml_config_resolver`
    /`saml_request_store`/`saml_replay_cache` at their production-default `None` so the real
    `DbSamlConfigResolver`/`RedisSamlRequestStore`/`RedisSamlReplayCache` adapters are exercised —
    no custom fakes were written for the security-critical stateful stores (tombstone/replay
    semantics are exactly what a simplistic fake could get wrong and silently mask).

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
