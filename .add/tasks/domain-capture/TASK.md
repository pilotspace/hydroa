# TASK: Verified email-domain claim routes signup/join (composes with S1 invite-only)

slug: domain-capture · created: 2026-07-10 · stage: production · sensitivity: security · risk: high · autonomy: conservative
milestone: enterprise-identity-compliance
<!-- risk: high — account-takeover surface (milestone shared decision: "verification via DNS TXT or
     equivalent proof, never email-match alone"). Mirrors saml-sso's own header shape. autonomy
     lowered to conservative so build cannot auto-PASS at Verify; HARD-STOP verify per the
     milestone's shared decision (identity surface), never auto-passed even under the project's
     default autonomy: auto. -->
phase: contract   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/tenants/api/router.py:39-59` `signup()` — CURRENT shape (post-S1):
  `POST /admin/auth/signup`, PUBLIC, first checks `request.app.state.settings.
  public_signup_enabled` (403 `ERR_SIGNUP_INVITE_ONLY` if False, zero DB IO), else delegates to
  `SignupUseCase.execute`. This is the ONE hook point this task extends with a new guard clause
  ABOVE the existing one — see §1 M8.
- `apps/gateway/src/gateway/tenants/api/schemas.py` `SignupRequest{tenant_name, email: EmailStr,
  password}` / `SignupResponse{tenant_id, user_id}` — `EmailStr` means `body.email` is ALREADY a
  validated address by the time the handler runs (422 on malformed email happens in FastAPI/
  pydantic before the handler body, unchanged) — domain extraction via `email.rsplit("@", 1)[-1]`
  is always safe, no new malformed-input edge case at the handler level.
- `apps/gateway/src/gateway/tenants/application/use_cases.py:20-34` `SignupUseCase.execute` —
  weak-password check (zero DB IO) THEN `IdentityRepository.create_tenant_with_owner`. Pattern
  this task's new `JoinTenantByDomainUseCase` mirrors (check-then-insert ordering).
- `apps/gateway/src/gateway/tenants/infrastructure/repository.py:67-85`
  `SqlAlchemyIdentityRepository.create_tenant_with_owner` — ONE-transaction INSERT of
  `TenantRow`+`UserRow(role=OWNER)`; `IntegrityError` (the `users.email` UNIQUE violation) ->
  `EmailAlreadyRegisteredError`. THE pattern this task's new join-path repository method mirrors
  (INSERT + catch-IntegrityError-reject), not the get-or-provision helper below — see Issues.
- `apps/gateway/src/gateway/tenants/infrastructure/repository.py:148-190`
  `_get_or_provision_sso_user` (private helper backing both `get_or_provision_oidc_user` and
  `get_or_provision_saml_user`) — email lookup; if the row exists AND `tenant_id` matches, RETURNS
  the existing user (submitted credential ignored); else provisions a NEW row with `role=Role.
  MEMBER` always. Built for repeat LOGIN (OIDC/SAML re-auth), not one-shot signup — see Issues for
  why this task does NOT reuse it for the join-existing-tenant path.
- `apps/gateway/src/gateway/tenants/domain/ports.py:6-49` `IdentityRepository` Protocol —
  `create_tenant_with_owner`, `get_user_by_email`, `get_or_provision_oidc_user`,
  `get_or_provision_saml_user` — this task adds ONE more ADDITIVE method (mirrors the saml-sso
  precedent's own framing: "existing method's signature is untouched").
- `apps/gateway/src/gateway/tenants/domain/entities.py:9-20` `Role(StrEnum)` — `MEMBER` is the
  least-privileged non-viewer role; both `_get_or_provision_sso_user`'s new-row branch and this
  task's new join-path use `Role.MEMBER` unconditionally.
- `apps/gateway/src/gateway/tenants/domain/errors.py:5-11` `EmailAlreadyRegisteredError`,
  `WeakPasswordError` — REUSED verbatim by the new join path (see M9); zero new error CLASS needed
  for that specific rejection.
- `apps/gateway/src/gateway/tenants/infrastructure/orm.py:69-89` `TenantRow` — `kind` `
  CheckConstraint` + `Index("tenants_platform_kind_uidx", "kind", unique=True, postgresql_where=
  text("kind = 'platform'"))` — a Postgres PARTIAL UNIQUE INDEX already live in this exact
  codebase. THE precedent this task's new domain-collision guard structurally mirrors (see M1).
  `:168-192` `UserRow` — `email` globally UNIQUE, `auth_method` `VARCHAR(32)` `server_default=
  'password'` (new password-signup rows keep this default untouched; SSO rows set `'oidc'`/`'saml'`
  explicitly at INSERT) — confirms a domain-capture-joined user is a REAL password user, not a
  sentinel-hash SSO user.
- `apps/gateway/src/gateway/core/config.py:83-84` `Settings` — `GATEWAY_<NAME>_ENABLED: bool =
  False` knob shape (`otel_enabled`, `oidc_enabled`, `public_signup_enabled` from S1); this task's
  new timeout/rate knobs (M13/M14) follow the same `Settings` field convention, not new shape.
- `apps/gateway/src/gateway/core/error_catalog.py:32-50` `ErrorSpec` dataclass (`status`, `code`,
  `title_template`, `.exc()`); `:~136` the "tenant identity" section (`AUTH_EMAIL_TAKEN`,
  `AUTH_PASSWORD_WEAK`, `SIGNUP_INVITE_ONLY`) — this task's new codes join that section.
  `:595-628` the SAML domain-error block, incl. `SAML_DOMAIN_ALREADY_CLAIMED` (409) — the naming
  convention this task's `ERR_DOMAIN_*` codes follow.
- `apps/gateway/src/gateway/auth/infrastructure/saml_orm.py:~31-56` `SamlProviderConfigRow.
  email_domains: list[str]` (Postgres `TEXT[]`, GIN `.contains()` index) — the ARRAY-COLUMN
  approach this task deliberately does NOT copy for its own domain→tenant mapping (see Framings —
  a dedicated one-row-per-domain table structurally prevents the exact defect below).
- `apps/gateway/src/gateway/auth/infrastructure/db_saml_config_resolver.py:59-95` `resolve()` — a
  defensive `ORDER BY created_at ASC, tenant_id ASC LIMIT 1` added so a cross-tenant `email_domains`
  collision returns ONE deterministic row instead of an unhandled `MultipleResultsFound`. Its own
  docstring says this is a mitigation for a TOCTOU, NOT a structural fix — see Issues.
- `apps/gateway/src/gateway/auth/api/saml_admin_router.py:252-277` — the PUT-time app-level
  collision PRE-CHECK (`SELECT ... WHERE tenant_id != :self AND email_domains.overlap(:new)`,
  raises `SAML_DOMAIN_ALREADY_CLAIMED` 409) added post-freeze in response to add-verify's Finding 3.
  This is the "collision guard" referenced in this task's brief — real, but explicitly documented
  as still race-prone under concurrent PUTs (see Issues); this task's own guard (M1) improves on it
  with a DB-level constraint rather than an app-level SELECT-then-INSERT.
- `apps/gateway/src/gateway/auth/api/oidc_admin_router.py:131-154` `_get_owner_identity` +
  `AUTH_FORBIDDEN_OWNER_REQUIRED` — a hard `Role.OWNER`-only gate (stricter than a tenant-scoped
  `Permission`), used identically by `saml_admin_router.py` for IdP-trust-anchor config. THE
  authorization precedent this task's own domain-claim admin endpoints reuse (see M2/M5/M6/M7 —
  Framings).
- `apps/gateway/src/gateway/scim/api/token_router.py:139` `require_permission(Permission.
  MEMBERS_MANAGE)` — the ALTERNATIVE gate considered (OWNER+ADMIN, broader) and rejected in favor
  of the stricter OIDC/SAML OWNER-only precedent — see Framings.
- `apps/gateway/src/gateway/tenants/domain/authz.py:54-69` `Permission(StrEnum)` (11 members, incl.
  `SECURITY_CONFIG`) — NOT extended by this task; no new `Permission` minted (matches S1's own
  "zero edit to the FROZEN Permission/ROLE_PERMISSIONS matrix" discipline).
- `apps/gateway/src/gateway/tenants/infrastructure/invite_repository.py:53-89`
  `InviteRepository.create_or_replace` — the upsert-on-reissue pattern (`ON CONFLICT` on a
  `(tenant_id, <target>)` unique pair, regenerate token+expiry) this task's claim-reissue path
  mirrors for a pending, not-yet-expired-or-expired re-claim of the same domain.
- `apps/gateway/src/gateway/tenants/application/invite_use_cases.py:85-87` `secrets.
  token_urlsafe(_TOKEN_BYTES)` + `_hasher.hash(token)` — the token-generation precedent. NOTE: this
  task's DNS-TXT token is deliberately NOT secret (it will be published in public DNS by design),
  so it is stored PLAINTEXT (not hashed) — a documented divergence from the invite/SCIM/agent-oauth
  token precedents, all of which protect a bearer credential; see Framings.
- `apps/gateway/src/gateway/tenants/api/invite_accept_router.py:125-129,168-172`
  `InvitePublicRateLimiter` / `InviteRateLimitedError` -> `RATE_LIMITED.exc(headers={"Retry-After":
  ...})` — the rate-limit pattern this task's claim-create/verify endpoints reuse (keyed by
  `tenant_id` since callers are authenticated OWNERs, not by client IP).
- `apps/gateway/uv.lock:576-589` `dnspython 2.8.0` — ALREADY present as a transitive dependency (of
  `email-validator`, used by pydantic's `EmailStr`), confirmed via `search_for_pattern("import
  dns")` returning zero hits anywhere in `apps/gateway/src` — this task is the FIRST direct use;
  must be promoted to an explicit direct dependency in `apps/gateway/pyproject.toml` (M16).
- `apps/dashboard/components/settings/OidcSettings.tsx` — the existing per-tenant settings-section
  component precedent (Aurora design system) the sibling `enterprise-identity-admin-ui` task (or a
  later UI-polish pass) extends for the domain-verification affordance; cited as the anchor ONLY —
  building that UI is explicitly out of THIS task's scope per the milestone brief.
- `.add/tasks/signup-and-routing-authz/TASK.md` (slug `signup-and-routing-authz`, **FROZEN @ v1**,
  `phase: done`, Tin-approved 2026-07-10) — the S1 contract this task composes with. Cites M1
  (`Settings.public_signup_enabled: bool = False`), M2 (the guard "checked FIRST... zero DB IO"),
  M3 (enabled-path byte-identical), R1 (403 `ERR_SIGNUP_INVITE_ONLY`). This task narrowly amends
  M2's "zero DB IO" property — see Issues and §1 ⚠.
- `.add/tasks/saml-sso/TASK.md` (slug `saml-sso`, **`phase: verify`, NOT done** — corrects this
  task's own brief, which called the collision guard "just shipped"; it IS shipped code but the
  task's OWN verify pass records `Verdict: HARD-STOP` with Finding 3 (domain-collision DoS)
  explicitly flagged as unresolved residue alongside the primary Finding 1 (SCD/InResponseTo).
  domain-capture depends-on saml-sso per the milestone DAG but does not need Finding 1/3 resolved
  first — the two tasks touch disjoint code; this task simply must not repeat Finding 3's app-level-
  only mistake in its OWN new table.
- `.add/milestones/enterprise-identity-compliance/MILESTONE.md` — shared decisions: "every identity
  surface is security-sensitive: HARD-STOP verify... domain capture (account-takeover surface:
  verification via DNS TXT or equivalent proof, never email-match alone)"; "domain capture composes
  with invite-only signup (S1)... a verified captured domain is an explicit tenant-admin opt-in that
  relaxes it for that domain only... Record as an S1-compatible extension, not a supersession, unless
  design proves otherwise" (this task proves no supersession is needed — see Framings); exit
  criterion: "A tenant admin proves domain ownership; a new signup on that verified domain lands in
  that tenant per the frozen precedence; an unverified domain changes nothing."
- `.add/GLOSSARY.md:25` `oidc_claim_mapping` — the existing, closest-analog glossary term ("the
  email-domain → tenant mapping that binds an SSO login to a tenant") this task's new terms are
  patterned against, while naming explicitly that domain-capture is a DISTINCT, fourth domain→tenant
  mapping surface (see Issues).

Context (working folder): `.add/milestones/enterprise-identity-compliance/MILESTONE.md` (shared
  decisions + exit criteria, fully read) · `.add/tasks/signup-and-routing-authz/TASK.md` (FROZEN,
  fully read) · `.add/tasks/saml-sso/TASK.md` (fully read, incl. its VERIFY section) ·
  `/private/tmp/claude-501/.../scratchpad/fe-design-context.md` (wave-2 shared design brief).

Honors (patterns / conventions):
  - REUSE the shared JIT-provisioning discipline's ROLE choice (`Role.MEMBER` for every
    auto-provisioned user, never higher) but NOT its get-or-return-existing SEMANTICS for a
    signup-shaped operation — see Issues (a real, not cosmetic, distinction).
  - Cheapest/no-IO check first is the DEFAULT S1 discipline, but this task's own M8 must
    deliberately INVERT that ordering for the ONE case where the feature's purpose requires it —
    named explicitly as an amendment, not silently violated (see Issues, §1 ⚠ TOP flag).
  - A Postgres PARTIAL UNIQUE INDEX (`tenants_platform_kind_uidx`) is ALREADY the codebase's own
    precedent for "at most one row may hold a given state" — reused here instead of SAML's
    app-level SELECT-then-INSERT pre-check, which its own author already flagged as TOCTOU-prone.
  - OWNER-only (`_get_owner_identity`), not a `Permission`, for identity-trust-anchor-shaped tenant
    config — matches OIDC/SAML's own precedent exactly (stricter than SCIM's `MEMBERS_MANAGE`).
  - `secrets.token_urlsafe(32)` for a fresh, unguessable, non-reused token per claim — matches the
    invite/SCIM/agent-oauth precedent's entropy choice, diverging only on hash-at-rest (see
    Touches — this token is not a secret).
  - `GATEWAY_<NAME>_ENABLED`/`GATEWAY_<NAME>_<UNIT>` Settings field naming for the new DNS-timeout
    and rate-limit knobs (M13/M14) — sibling to `otel_enabled`, `invite_preview_rpm`.

Anchors the contract cites: `signup()` (`tenants/api/router.py:39-59`) · `SignupRequest`/
  `SignupResponse` (`tenants/api/schemas.py`) · `IdentityRepository` (`tenants/domain/ports.py`) ·
  `create_tenant_with_owner` (`tenants/infrastructure/repository.py:67-85`) ·
  `EmailAlreadyRegisteredError`/`WeakPasswordError` (`tenants/domain/errors.py`) · `TenantRow`'s
  `tenants_platform_kind_uidx` partial-index pattern (`tenants/infrastructure/orm.py:69-89`) ·
  `_get_owner_identity`/`AUTH_FORBIDDEN_OWNER_REQUIRED` (`auth/api/oidc_admin_router.py:131-154`) ·
  `InvitePublicRateLimiter`/`RATE_LIMITED` (`tenants/api/invite_accept_router.py`) ·
  `Settings` (`core/config.py`) · `ErrorSpec`/tenant-identity section (`core/error_catalog.py`).

Issues/Risks (→ feed §1):
  - **Brief-correction (materially important)**: the task brief's "mirror the saml domain-collision
    guard just shipped" is half-right. Real, shipped app code exists (`saml_admin_router.py:252-
    277`'s PUT-time pre-check + the resolver's `ORDER BY...LIMIT 1` fallback) — but saml-sso is
    NOT `phase: done`, and that SAME guard's author (add-verify) explicitly documented it as still
    TOCTOU-prone under concurrent PUTs (residue under Finding 3, not separately re-verified). This
    task must not just copy an already-flagged-imperfect pattern; see §1 M1 for the structural
    upgrade (a partial unique index closes the race at the DB level, not just at the app level).
  - **S1 M2 "zero DB IO when disabled" cannot survive composition unchanged**: the milestone's own
    wording — "relaxes it for THAT domain only" — requires the domain-claim lookup to run even when
    `public_signup_enabled` is False (otherwise a verified-domain org can never benefit from this
    feature while otherwise invite-only, which is S1's OWN shipped prod default:
    `values-prod.yaml: publicSignupEnabled: false`). This forces the domain lookup to run BEFORE,
    not after, the (free, in-memory) `public_signup_enabled` check — inverting the "cheapest-first"
    heuristic S1 itself established. This is a deliberate, narrow, and disclosed amendment to a
    FROZEN sibling contract's stated property, not a silent violation — flagged at §1 ⚠ TOP.
  - **Reusing `_get_or_provision_sso_user` for the join path would be a real security bug, not a
    style choice**: that helper's existing-user branch returns the EXISTING user (ignoring
    whatever credential was just supplied) when `email` already exists in the TARGET tenant —
    correct for repeat SSO LOGIN, but WRONG for SIGNUP: an attacker could submit ANY password
    against a real victim's already-registered email and receive a misleading 201 success response
    (a user-enumeration + false-success oracle). This task's join path must instead REJECT on any
    existing email — see §1 M9, mirroring `create_tenant_with_owner`'s INSERT+IntegrityError shape.
  - **Domain→tenant mapping is now a FOUR-way-parallel concept**, none reconciled with each other:
    env `GATEWAY_OIDC_DOMAIN_MAPPING`, per-tenant `oidc_provider_configs.email_domains`, per-tenant
    `saml_provider_configs.email_domains`, and this task's NEW `tenant_domain_claims`. A tenant
    COULD misconfigure SAML's `email_domains` to point one way and hold a verified domain-capture
    claim pointing another. Reconciling all four is out of this task's declared scope (it changes
    only the plain-password `POST /admin/auth/signup` routing decision, never SSO login-initiation
    routing) — named here, flagged forward at §7 OBSERVE, not solved.
  - **No re-verification / domain-lapse handling in v1**: once a claim is `verified`, this task
    never re-checks the DNS TXT record again. If the underlying domain registration later lapses
    and is re-registered by an unrelated new owner, that new owner cannot self-claim it via this
    feature until the ORIGINAL tenant (or an operator) explicitly revokes the stale row — a named,
    accepted residual risk (favors availability of the first verifier over a later claimant), not
    solved here; flagged forward.
  - **Subdomain-takeover / dangling-DNS is structurally defended, not merely mitigated**: this
    task's TXT-record challenge requires DNS ZONE edit rights for the exact `_ai-proxy-challenge.
    <domain>` label — control of a dangling CNAME target (classic "subdomain takeover") does NOT
    grant zone-edit rights, so it cannot forge this record. The residual risk above (domain-registration
    lapse -> DNS zone control changes hands) is a DIFFERENT, narrower threat than subdomain
    takeover and is the one actually worth naming.
  - **Verification-token replay is structurally defended**: each claim's token is a fresh
    `secrets.token_urlsafe(32)` value bound to (claim_id, tenant_id, domain); `verify()` always
    re-queries DNS for THAT claim's OWN `domain` column (never a client-suppliable override), so
    publishing tenant A's token under domain Y cannot verify claim X (which targets domain X) —
    no cross-claim replay path exists as designed.

Related intent: `.add/PROJECT.md` (ADD methodology; SUPERSESSION vs EXTENSION pattern) ·
  `.add/milestones/enterprise-identity-compliance/MILESTONE.md` (exit criterion, shared decisions) ·
  `.add/tasks/signup-and-routing-authz/TASK.md` (FROZEN S1 contract, the invite-only default this
  task composes with, never supersedes) · `.add/tasks/saml-sso/TASK.md` (shared tenant-SSO config
  surface + the domain-collision defect class this task must not repeat).

Ground SHA: 443a33a (branch `feat/enterprise-hardening`)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: A tenant OWNER proves control of an email domain via a DNS TXT challenge; once verified,
  a NEW `POST /admin/auth/signup` for that domain auto-joins the claiming tenant (role=member) —
  REGARDLESS of the S1 `public_signup_enabled` flag's value — while every other domain continues to
  obey S1's frozen invite-only default exactly as today. An unverified (pending/expired/revoked)
  claim changes nothing.

Framings weighed:
  WHERE the domain→tenant mapping lives:
    a NEW dedicated table `tenant_domain_claims`, one row per (tenant_id, domain), with a
    PARTIAL UNIQUE INDEX on `domain WHERE status = 'verified'` (CHOSEN — mirrors the codebase's own
    `tenants_platform_kind_uidx` precedent; makes "at most one tenant may hold a verified claim on
    any domain" a DB-level invariant, closing the exact TOCTOU class saml-sso's own verify pass
    flagged as still-open residue in its array-column + app-level-pre-check design) · reuse/extend
    `saml_provider_configs.email_domains` (REJECTED — conflates SSO-IdP existence with domain
    ownership proof; a tenant may want a verified domain with NO SAML/OIDC configured at all; an
    array column cannot carry a DB-level cross-row uniqueness constraint the way a one-row-per-
    domain table can) · a 5th env-driven global mapping like `GATEWAY_OIDC_DOMAIN_MAPPING`
    (REJECTED — this is inherently a per-tenant, admin-self-service capability, not an operator
    config knob).
  HOW ownership is proven:
    DNS TXT record at a claim-scoped subdomain label, `_ai-proxy-challenge.<domain>` (CHOSEN —
    requires DNS ZONE edit rights, not merely web-server/CDN control; scoping to a subdomain label
    rather than the apex avoids clobbering existing SPF/DKIM/DMARC apex TXT records an admin may
    already have) · an apex-level TXT record, `<domain> TXT "ai-proxy-domain-verification=..."`
    (REJECTED as the CHOSEN default, kept as the documented, low-stakes, mechanical alternative if
    Tin prefers matching Google Search Console's more familiar apex convention — flagged at §1 ⚠) ·
    email-match / "your email ends in @acme.com so you must be from Acme" (REJECTED — explicitly
    ruled out by the milestone's own mandate: "never email-match alone"; proves nothing about DNS
    control, trivially spoofable by anyone with an @acme.com address who is NOT authorized to speak
    for the domain) · a one-time HTTP file upload to `https://<domain>/.well-known/ai-proxy-verify`
    (REJECTED — requires web-server write access, not DNS control; a domain can have working DNS
    with no web server at all, e.g. an email-only domain; DNS TXT is the milestone's own named
    default and needs no additional infrastructure assumption).
  WHO may claim/verify/revoke:
    `Role.OWNER` only, via the SAME `_get_owner_identity` gate OIDC/SAML admin config already use
    (CHOSEN — matches the existing precedent for identity-trust-anchor-shaped tenant config exactly;
    a wrong domain claim has tenant-wide, account-takeover-adjacent blast radius, at least as
    sensitive as SAML IdP config) · `require_permission(Permission.MEMBERS_MANAGE)` (REJECTED —
    the SCIM-token precedent, but broader: ADMIN would also qualify; domain-capture's blast radius
    (who can join the tenant AT ALL, for an entire domain, indefinitely) is a materially bigger
    decision than SCIM token issuance) · a NEW `Permission.DOMAIN_MANAGE` enum member (REJECTED —
    zero behavioral difference from reusing `_get_owner_identity` once OWNER already holds every
    Permission by the frozen completeness guard; adds a new enum member for no isolation gain,
    mirrors S1's own "mint a new Permission" rejection).
  WHETHER this composes with or supersedes S1:
    An EXPLICIT, NARROW composition/extension of the FROZEN S1 contract, not a supersession
    (CHOSEN — S1's own decision, "any owner/admin may write... invite-only by default," is left
    completely intact for every domain with no verified claim; this task adds exactly one new,
    narrow bypass scoped to a single proven domain, which is what the milestone's shared decision
    explicitly asks for: "record as an S1-compatible extension... unless design proves otherwise" —
    no proof that a supersession is needed was found) — but the composition DOES require one
    disclosed, narrow amendment to S1 M2's "zero DB IO when disabled" property (see ⚠ TOP below);
    this is recorded as an AMENDMENT, not a supersession, since M2's outward behavior (403
    `ERR_SIGNUP_INVITE_ONLY` for every non-matching domain) is unchanged — only its INTERNAL
    zero-IO implementation detail changes.
  HOW an existing email is handled on the join path:
    a NEW, INSERT-only `join_verified_tenant_domain` repository method that catches the `users.
    email` UNIQUE-constraint `IntegrityError` and re-raises the EXISTING `EmailAlreadyRegisteredError`
    (CHOSEN — matches signup's true "reject on any existing email" semantics; reuses S1's own
    `AUTH_EMAIL_TAKEN` 409 mapping verbatim, zero new error code for this rejection) · reuse
    `_get_or_provision_sso_user`'s get-or-return-existing semantics (REJECTED — see §0 Issues: a
    real account-enumeration + false-success bug for a SIGNUP-shaped operation, not a style
    preference).
  ORDERING vs the `public_signup_enabled` check:
    domain-claim lookup (indexed, O(1)) runs FIRST, `public_signup_enabled` (free, in-memory) check
    runs SECOND, only reached when no verified claim matches (CHOSEN — the ONLY ordering under which
    a verified domain ever relaxes an otherwise invite-only deployment, which is the feature's
    entire purpose and the majority real-world case since S1 shipped prod with
    `publicSignupEnabled: false`) · check `public_signup_enabled` FIRST, only consult domain claims
    when it is already True (REJECTED — makes the ENTIRE feature inert on every deployment that
    actually wants it, i.e. an invite-only org that wants ONE trusted corporate domain to self-serve
    — this is precisely the scenario the milestone names) — kept as the documented reversible
    alternative in ⚠ TOP below, since it is genuinely Tin's call on a FROZEN sibling's behavior.

Must:
<must>
  - **[M1]** NEW table `tenant_domain_claims` (id uuid7 PK, tenant_id FK->tenants.id RESTRICT,
    domain text NOT NULL lowercase-normalized, verification_token text NOT NULL, status text
    CHECK IN ('pending','verified') NOT NULL DEFAULT 'pending', created_at, verified_at nullable,
    expires_at NOT NULL, created_by_user_id FK->users.id). TWO indexes: (a) plain UNIQUE
    (tenant_id, domain) — one claim row per tenant per domain, ever; (b) PARTIAL UNIQUE INDEX on
    `domain` `WHERE status = 'verified'` — the STRUCTURAL cross-tenant collision guard (mirrors
    `tenants_platform_kind_uidx`), enforced by Postgres itself, not by an app-level SELECT-then-
    INSERT race.
  - **[M2]** `POST /admin/domain-claims` (OWNER-only, `_get_owner_identity`): validates+normalizes
    `body.domain` (M3), then either (a) creates a NEW `pending` row with a freshly generated
    `secrets.token_urlsafe(32)` token and `expires_at = now + 7 days`, or (b) if a `pending`,
    NOT-YET-verified row already exists for (tenant_id, domain) (mirrors `InviteRepository.
    create_or_replace`'s upsert-on-reissue shape — a re-POST regenerates the token+expiry, does
    not create a second row). Returns the DNS record the caller must publish (name, type, value,
    expiry) — 201.
  - **[M3]** Domain string is validated BEFORE any DB write: lowercased, trimmed, matches a
    hostname shape (labels of `[a-z0-9-]`, 1-63 chars each, no leading/trailing hyphen, >= 2 labels,
    total <= 253 chars), not a bare IP literal. Invalid -> `ERR_DOMAIN_INVALID` (400), zero DB IO.
  - **[M4]** `POST /admin/domain-claims` fails FAST (before insert) if a DIFFERENT tenant already
    holds a `verified` row for that exact domain -> `ERR_DOMAIN_ALREADY_VERIFIED` (409) — a UX-level
    pre-check; M1's partial unique index is the actual structural backstop, not this check alone.
  - **[M5]** `GET /admin/domain-claims` (OWNER-only): lists ONLY the caller's own tenant's claim
    rows (pending + verified), tenant-scoped query — 200.
  - **[M6]** `POST /admin/domain-claims/{claim_id}/verify` (OWNER-only, claim tenant-scoped —
    unknown id OR another tenant's id -> `ERR_DOMAIN_CLAIM_NOT_FOUND` 404, deliberately
    indistinguishable, matching the existing `InviteNotFoundError` "unknown-id and wrong-tenant are
    deliberately indistinguishable" precedent): performs ONE bounded-timeout DNS TXT lookup for
    `_ai-proxy-challenge.<domain>`; on an EXACT match to this claim's own stored
    `verification_token` value, atomically flips `status: pending -> verified` via an `UPDATE ...
    WHERE id = :id AND status = 'pending'` guarded by M1's partial unique index — if a DIFFERENT
    tenant's claim on the SAME domain verified first (a genuine race), this UPDATE raises an
    `IntegrityError` -> `ERR_DOMAIN_ALREADY_VERIFIED` (409), the loser's row stays `pending`.
  - **[M7]** `DELETE /admin/domain-claims/{claim_id}` (OWNER-only, same tenant-scoped 404 shape as
    M6) revokes a claim in ANY status. A revoked `verified` claim stops influencing FUTURE signups
    immediately; it NEVER retroactively removes or demotes any user already joined through it — 204.
  - **[M8]** `signup()` (`tenants/api/router.py`) gains exactly ONE new guard clause, inserted
    ABOVE the existing S1 `public_signup_enabled` check (§0 Issues — the ordering IS the feature):
    resolve `body.email`'s domain against `tenant_domain_claims` rows with `status = 'verified'`
    (indexed point lookup). A match -> the NEW join-existing-tenant path (M9-M12), completely
    bypassing the `public_signup_enabled` check for THIS request only. No match -> fall through to
    the EXISTING, byte-identical S1 path (frozen M1-M11 of `signup-and-routing-authz` TASK.md,
    UNCHANGED) — a pending, expired, or revoked claim is indistinguishable from no claim at all.
  - **[M9]** The join-existing-tenant path uses a NEW, ADDITIVE `IdentityRepository.
    join_verified_tenant_domain(tenant_id, email, password_hash) -> uuid.UUID` method: ONE INSERT
    of `UserRow(tenant_id=<claimed tenant>, role=Role.MEMBER, auth_method='password' (default,
    unchanged column))`; catches the `users.email` UNIQUE `IntegrityError` and re-raises the
    EXISTING `EmailAlreadyRegisteredError` — see §0 Issues for why this does NOT reuse
    `_get_or_provision_sso_user`'s get-or-return-existing shape.
  - **[M10]** A user provisioned via domain-capture ALWAYS gets `role = Role.MEMBER` (never OWNER,
    never any role from request input — `SignupRequest` has no role field, so this is not
    attacker-controllable either way) and their OWN submitted `password` (hashed via the existing
    `PasswordHasher`, never a sentinel) — matches the least-privilege JIT-provisioning default
    already established for OIDC/SAML.
  - **[M11]** `SignupRequest.tenant_name` remains present and required (schema BYTE-IDENTICAL to
    S1's frozen shape) but is IGNORED and never persisted when routed via a verified domain match —
    the target tenant already exists. Named explicitly, not silently dropped.
  - **[M12]** `SignupResponse` gains ONE new, ADDITIVE, default-`false` field:
    `joined_existing_tenant: bool` — `true` on the domain-routed join path, `false` on the existing
    create-new-tenant path. Backward-compatible: every field S1 froze (`tenant_id`, `user_id`) is
    present, unchanged, in both cases; S1's own scenarios assert a STRICT SUBSET of this shape, not
    violated.
  - **[M13]** Every DNS TXT lookup (M6) uses a SINGLE attempt with a bounded timeout —
    `Settings.domain_verification_dns_timeout_seconds: float = 5.0`
    (`GATEWAY_DOMAIN_VERIFICATION_DNS_TIMEOUT_SECONDS`) — and fails CLOSED: any resolver error,
    NXDOMAIN, empty answer, or timeout -> `ERR_DNS_LOOKUP_FAILED` (503, retryable), NEVER treated as
    a verified match. No internal retry loop — the human re-clicking "Verify" IS the retry
    mechanism (design-for-failure via bounded timeout + fail-closed + human-visible retry, not a
    background job or circuit breaker, appropriate for a low-volume, human-triggered action).
  - **[M14]** `POST /admin/domain-claims` and `POST /admin/domain-claims/{id}/verify` are BOTH
    rate-limited per `tenant_id` (reusing the `InvitePublicRateLimiter`/`RATE_LIMITED` pattern, new
    `Settings.domain_claim_create_rpm`/`domain_claim_verify_rpm` knobs, sane defaults e.g. 10/hour
    and 30/hour) -> `RATE_LIMITED` (429, `Retry-After` header) when exceeded.
  - **[M15]** Only `status = 'verified'` rows are ever consulted by `signup()`'s M8 lookup — a
    `pending`, `expired` (past `expires_at` with no successful verify), or revoked (deleted) row
    NEVER influences signup routing, matching "an unverified domain changes nothing" verbatim.
  - **[M16]** `dnspython` (already present transitively per `uv.lock`) is added as an explicit
    direct dependency in `apps/gateway/pyproject.toml` — this task is the first direct import.
</must>
Reject:
<reject>
  - **[R1]** `POST /admin/domain-claims` with a malformed/single-label/IP-literal domain ->
    "ERR_DOMAIN_INVALID" (400, zero DB IO) — M3
  - **[R2]** `POST /admin/domain-claims` when a DIFFERENT tenant already holds a `verified` row for
    that domain -> "ERR_DOMAIN_ALREADY_VERIFIED" (409) — M4
  - **[R3]** Any `/admin/domain-claims*` call by a non-OWNER (ADMIN/OPERATOR/BILLING_ADMIN/VIEWER/
    MEMBER of any tenant) -> "ERR_AUTH_FORBIDDEN_OWNER_REQUIRED" (403, reused verbatim) — Framings
  - **[R4]** Any `/admin/domain-claims*` call with a missing/invalid/expired bearer token ->
    "ERR_AUTH_TOKEN_INVALID" (401, reused verbatim)
  - **[R5]** `POST .../verify` when the DNS TXT record is missing or does not match this claim's
    own stored token -> "ERR_DOMAIN_VERIFICATION_FAILED" (400) — M6
  - **[R6]** `POST .../verify` when `now > expires_at` -> "ERR_DOMAIN_CLAIM_EXPIRED" (410) — the
    caller must re-`POST /admin/domain-claims` to reissue (M2)
  - **[R7]** `POST .../verify` that loses a genuine cross-tenant race (M1's partial unique index
    rejects the UPDATE) -> "ERR_DOMAIN_ALREADY_VERIFIED" (409) — the loser's row stays `pending`,
    unchanged — M6
  - **[R8]** `POST .../verify` when the DNS resolver errors out or times out ->
    "ERR_DNS_LOOKUP_FAILED" (503, retryable) — fail CLOSED, never marks verified — M13
  - **[R9]** `GET`/`POST .../verify`/`DELETE` with a `claim_id` that does not exist OR belongs to a
    DIFFERENT tenant -> "ERR_DOMAIN_CLAIM_NOT_FOUND" (404, deliberately indistinguishable) — M6/M7
  - **[R10]** `POST /admin/domain-claims` or `.../verify` beyond the configured rate limit ->
    "ERR_RATE_LIMITED" (429, `Retry-After` header, reused verbatim) — M14
  - **[R11]** `POST /admin/auth/signup` with an email whose domain has NO claim, or only a
    `pending`/`expired`/revoked one, while `public_signup_enabled == False` ->
    "ERR_SIGNUP_INVITE_ONLY" (403, reused verbatim, BYTE-IDENTICAL to S1's own R1) — "an unverified
    domain changes nothing" — M8, M15
  - **[R12]** `POST /admin/auth/signup` with an email whose domain HAS a `verified` claim, but that
    exact email is already registered ANYWHERE (this tenant or any other) ->
    "ERR_AUTH_EMAIL_TAKEN" (409, reused verbatim) — M9
  - **[R13]** `POST /admin/auth/signup` with an email whose domain HAS a `verified` claim, but
    `password` is weaker than `MIN_PASSWORD_LENGTH` -> "ERR_AUTH_PASSWORD_WEAK" (400, reused
    verbatim) — M9, M10
</reject>
After:
<after>
  - No claim, or only a pending/expired/revoked one: `POST /admin/auth/signup` is BYTE-IDENTICAL to
    the frozen S1 contract in every observable respect (M8, M15) — S1's own scenarios continue to
    pass unmodified.
  - A verified-domain signup succeeds: exactly ONE new `users` row exists (tenant_id = the claiming
    tenant, role=member, auth_method='password', password_hash = the caller's own), ZERO new
    `tenants` rows, `SignupResponse.joined_existing_tenant == true`. The claiming tenant's OTHER
    data (existing users, plan, budgets, keys, other claims) is completely untouched.
  - Two tenants race to verify the SAME domain: exactly one claim ends `verified`; the loser's claim
    row is unchanged (`pending`, its `expires_at` untouched) and its OWNER receives 409 — no manual
    reconciliation runs automatically; the loser may claim a DIFFERENT, legitimately-owned domain,
    or ask the winner's tenant / an operator to release it.
  - A claim is revoked: `signup()` immediately stops routing NEW signups on that domain to the
    formerly-claiming tenant (falls back to the S1 default for that domain going forward); every
    user who already joined through it keeps their existing tenant membership, role, and
    credentials — completely unaffected by the revoke.
  - DNS lookup times out or errors: the claim's `status` is UNCHANGED (`pending`), no partial state,
    caller sees a retryable 503 and may click "Verify" again once DNS propagates/is fixed.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **The domain-claim lookup runs BEFORE (not after) the frozen S1 `public_signup_enabled` check —
    a narrow, disclosed AMENDMENT to S1 M2's "zero DB IO when disabled" property.** Lowest
    confidence because it is the one place this task's contract reaches back into a FROZEN sibling
    task's own stated invariant, and S1 itself did not anticipate this composition. The alternative
    (check `public_signup_enabled` first, only consult domain claims when already True) is a
    one-line reversal, fully reversible, and was seriously weighed — but it would make the ENTIRE
    domain-capture feature inert on every deployment that actually wants it (an otherwise
    invite-only org that wants exactly one trusted corporate domain to self-serve — S1's OWN prod
    default ships `publicSignupEnabled: false`). If wrong: revert the guard ordering (tiny router
    diff, no schema change) — but then this whole task ships functionally dead in the majority
    real-world deployment shape. This needs Tin's explicit confirmation at freeze, same as S1's own
    three flags were.
  ⚠ **DNS TXT record scoped to a subdomain label (`_ai-proxy-challenge.<domain>`) rather than the
    domain apex.** Lower confidence because it is a user-facing detail the sibling admin-ui task (or
    a UX-polish pass) will render directly to tenant admins, and "apex TXT, Google-style" is the
    more familiar convention for some admins even though subdomain-scoping is objectively safer
    (no collision with existing SPF/DKIM/DMARC apex records). If wrong: swap the DNS record name
    template (one string constant), zero schema/API-shape change — low stakes, easy to revise
    even after build, but surfaced because it is externally visible the moment the first real
    tenant admin sees it.
  - [ ] Partial-unique-index-on-VERIFIED-only (not a blanket `UNIQUE(domain)` across all statuses) —
    chosen so a losing/expired `pending` claim never needs manual cleanup and never blocks a
    legitimate different-domain retry by the same or another tenant; low stakes, reversible via a
    follow-up migration if Tin prefers a stricter always-on uniqueness.
  - [ ] No periodic re-verification / domain-registration-lapse handling in v1 (§0 Issues) — a
    named, accepted gap, not silently omitted; candidate for an OBSERVE-seeded spec delta
    (e.g. a scheduled re-check job that auto-revokes a claim whose TXT record disappears).
  - [ ] OWNER-only (not ADMIN too) may claim/verify/revoke a domain — matches the OIDC/SAML
    precedent exactly; flagged only because it may be more friction than some larger orgs want
    (an ADMIN cannot self-serve this), but I lean CHOSEN with high confidence given the blast
    radius (see Framings) — not top-flagged.
  - [ ] The four-way domain→tenant mapping fragmentation (§0 Issues: env OIDC mapping, per-tenant
    OIDC config, per-tenant SAML config, this task's new claims table) is named but not reconciled —
    out of THIS task's declared scope; flagged forward as an OBSERVE spec-delta candidate.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: OWNER claims a fresh domain, gets a DNS TXT challenge to publish            # M1, M2
  Given tenant "acme" has no existing claim for "acme.io", caller is acme's OWNER
  When they POST /admin/domain-claims {domain: "acme.io"}
  Then the response is 201 {claim_id, domain: "acme.io", status: "pending",
    dns_record_type: "TXT", dns_record_name: "_ai-proxy-challenge.acme.io",
    dns_record_value: "ai-proxy-domain-verification=<43-char-token>", expires_at}
  And exactly one tenant_domain_claims row exists, status='pending'

Scenario: Re-claiming an already-pending domain reissues, does not duplicate           # M2
  Given tenant "acme" already has a pending claim for "acme.io" (not yet expired)
  When acme's OWNER POSTs /admin/domain-claims {domain: "acme.io"} again
  Then the response is 201 with a NEW dns_record_value (freshly regenerated token) and a
    NEW expires_at
  And exactly ONE tenant_domain_claims row still exists for (acme, "acme.io") — no duplicate

Scenario: Malformed domain is rejected before any DB write                             # M3, R1
  Given acme's OWNER is authenticated
  When they POST /admin/domain-claims {domain: "not a domain!!"}   (also tried: "com",
    "192.168.1.1", "")
  Then the response is 400 "ERR_DOMAIN_INVALID"
  And no tenant_domain_claims row was created for any of these inputs

Scenario: Claiming a domain another tenant has already verified is rejected up front   # M4, R2
  Given tenant "globex" holds a VERIFIED claim on "shared-corp.com"
  When acme's OWNER POSTs /admin/domain-claims {domain: "shared-corp.com"}
  Then the response is 409 "ERR_DOMAIN_ALREADY_VERIFIED"
  And no tenant_domain_claims row was created for acme on that domain
  And globex's verified claim is completely unchanged

Scenario: OWNER lists only their own tenant's claims                                   # M5
  Given tenant "acme" has 2 claims (one pending, one verified) and tenant "globex" has 1
  When acme's OWNER GETs /admin/domain-claims
  Then the response is 200 with exactly acme's 2 claims — globex's claim is NOT present

Scenario: Verification succeeds when the DNS TXT record matches exactly                # M6
  Given acme has a pending claim on "acme.io" with token "T1", and the DNS resolver
    returns TXT "_ai-proxy-challenge.acme.io" = "ai-proxy-domain-verification=T1"
  When acme's OWNER POSTs /admin/domain-claims/{claim_id}/verify
  Then the response is 200 {claim_id, domain: "acme.io", status: "verified", verified_at}
  And the claim row's status flips to 'verified' in exactly one UPDATE

Scenario: Verification fails when the TXT record is missing or wrong                   # M6, R5
  Given acme has a pending claim on "acme.io" with token "T1", and the DNS resolver
    returns NO TXT record (or a TXT record with a different value) at
    "_ai-proxy-challenge.acme.io"
  When acme's OWNER POSTs /admin/domain-claims/{claim_id}/verify
  Then the response is 400 "ERR_DOMAIN_VERIFICATION_FAILED"
  And the claim row's status stays 'pending' — unchanged

Scenario: Verification fails closed on a DNS resolver timeout                          # M13, R8
  Given acme has a pending claim on "acme.io", and the DNS resolver call exceeds
    domain_verification_dns_timeout_seconds
  When acme's OWNER POSTs /admin/domain-claims/{claim_id}/verify
  Then the response is 503 "ERR_DNS_LOOKUP_FAILED"
  And the claim row's status stays 'pending' — NEVER marked verified on a timeout
  And the caller may retry the same POST once DNS is reachable (no internal auto-retry)

Scenario: Verification is rejected once the challenge has expired                      # R6
  Given acme has a pending claim on "acme.io" with expires_at in the past
  When acme's OWNER POSTs /admin/domain-claims/{claim_id}/verify (even with a
    now-correctly-published TXT record)
  Then the response is 410 "ERR_DOMAIN_CLAIM_EXPIRED"
  And the claim row's status stays 'pending' — the caller must re-POST /admin/domain-claims
    to reissue a fresh token before retrying verify

Scenario: Two tenants race to verify the same domain — exactly one wins                # M1, M6, R7
  Given acme AND globex each hold a separate pending claim on "shared-corp.com", and both
    publish (or attempt to publish) a matching TXT record for their own token concurrently
  When both OWNERs POST .../verify at nearly the same time
  Then exactly ONE of the two responses is 200 "verified" and the OTHER is 409
    "ERR_DOMAIN_ALREADY_VERIFIED" — enforced by the partial unique index, not a pre-check race
  And the loser's claim row remains 'pending', completely unchanged
  And only ONE tenant_domain_claims row for "shared-corp.com" is ever status='verified'

Scenario: A non-OWNER cannot claim, verify, list, or revoke a domain                    # R3
  Given a logged-in ADMIN of tenant "acme" (not OWNER)
  When they attempt POST /admin/domain-claims, GET /admin/domain-claims,
    POST .../verify, and DELETE .../{claim_id} in turn
  Then every response is 403 "ERR_AUTH_FORBIDDEN_OWNER_REQUIRED"
  And no tenant_domain_claims row is created, modified, or deleted by any of the 4 attempts

Scenario: Missing or invalid bearer token is rejected for every domain-claims endpoint  # R4
  Given no Authorization header (or an invalid/expired one)
  When a client calls any /admin/domain-claims* endpoint
  Then the response is 401 "ERR_AUTH_TOKEN_INVALID"

Scenario: A claim_id from a different tenant is indistinguishable from unknown          # R9
  Given globex holds claim_id "G1", and acme's OWNER does not know it belongs to globex
  When acme's OWNER POSTs /admin/domain-claims/G1/verify or DELETEs /admin/domain-claims/G1
  Then the response is 404 "ERR_DOMAIN_CLAIM_NOT_FOUND" — identical to a genuinely unknown id
  And globex's claim G1 is completely unchanged

Scenario: Claim-creation and verify are rate-limited per tenant                        # M14, R10
  Given acme's OWNER has already made domain_claim_create_rpm create-attempts this window
  When they POST /admin/domain-claims one more time
  Then the response is 429 "ERR_RATE_LIMITED" with a Retry-After header
  And no new tenant_domain_claims row is created by the rate-limited attempt

Scenario: Revoking a verified claim stops future signups but not existing members       # M7
  Given acme holds a verified claim on "acme.io", and "alice@acme.io" already joined acme
    through it yesterday
  When acme's OWNER DELETEs /admin/domain-claims/{claim_id}
  Then the response is 204 and the claim row no longer exists (or is marked revoked)
  And alice's users row (tenant_id=acme, role=member) is completely unchanged — she is not
    removed, demoted, or logged out
  And a NEW signup for "bob@acme.io" immediately AFTER the revoke falls back to the S1
    default (403 ERR_SIGNUP_INVITE_ONLY if public_signup_enabled=false, else creates a
    brand-new tenant per S1's unchanged path)

Scenario: A verified-domain signup joins the EXISTING tenant, even while invite-only    # M8, M9, M10
  Given GATEWAY_PUBLIC_SIGNUP_ENABLED=false (S1's shipped prod default), and acme holds a
    VERIFIED claim on "acme.io"
  When a client POSTs /admin/auth/signup {tenant_name: "ignored-value", email:
    "newhire@acme.io", password: "correct horse battery staple"}
  Then the response is 201 SignupResponse {tenant_id: <acme's existing id>, user_id: <new>,
    joined_existing_tenant: true}
  And exactly one NEW users row exists: tenant_id=acme, role=member, auth_method='password'
  And ZERO new tenants rows were created — acme's tenant row is otherwise untouched
  And SignupRequest.tenant_name ("ignored-value") was never read or persisted

Scenario: An unverified (pending) domain changes nothing — S1's default still applies   # M8, M15, R11
  Given GATEWAY_PUBLIC_SIGNUP_ENABLED=false, and acme holds only a PENDING (not yet
    verified) claim on "acme.io"
  When a client POSTs /admin/auth/signup {tenant_name: "X", email: "x@acme.io", password:
    "correct horse battery staple"}
  Then the response is 403 "ERR_SIGNUP_INVITE_ONLY" — byte-identical to S1's own R1
  And no tenants row and no users row was created
  And the pending claim on "acme.io" is completely unchanged

Scenario: A domain with no claim at all still obeys S1 exactly, both flag values        # M8 (regression)
  Given "unclaimed.example" has never had any tenant_domain_claims row
  When a client POSTs /admin/auth/signup {tenant_name: "New Co", email: "x@unclaimed.example",
    password: "correct horse battery staple"} first with
    GATEWAY_PUBLIC_SIGNUP_ENABLED=false, then again with it =true
  Then the false case is 403 "ERR_SIGNUP_INVITE_ONLY" and the true case is 201 with a BRAND
    NEW tenant + owner, byte-identical to signup-and-routing-authz's own frozen scenarios
  And joined_existing_tenant is absent-or-false in the true case's response

Scenario: A verified-domain signup still rejects an already-registered email            # M9, R12
  Given acme holds a VERIFIED claim on "acme.io", and "taken@acme.io" is already registered
    (in ANY tenant, including acme itself)
  When a client POSTs /admin/auth/signup {tenant_name: "X", email: "taken@acme.io",
    password: "correct horse battery staple"}
  Then the response is 409 "ERR_AUTH_EMAIL_TAKEN" — the SAME code S1's R3 uses
  And no NEW users row was created, and the existing user's row (whichever tenant it
    belongs to) is completely unchanged — no silent "log them in" behavior

Scenario: A verified-domain signup still rejects a weak password                       # M9, M10, R13
  Given acme holds a VERIFIED claim on "acme.io"
  When a client POSTs /admin/auth/signup {tenant_name: "X", email: "new@acme.io",
    password: "short"}
  Then the response is 400 "ERR_AUTH_PASSWORD_WEAK"
  And no users row was created

Scenario: The existing S1 bootstrap/regression suite is unaffected                     # M8 (integration regression)
  Given every scenario in signup-and-routing-authz TASK.md §2 (bootstrap flip-on/off,
    superadmin routing reads/writes, non-superadmin routing rejections, weak-password/
    duplicate-email regressions)
  When each is re-run unmodified against the tree AFTER this task's build
  Then every one still passes byte-for-byte as originally specified — this task adds a
    guard clause ABOVE the existing checks, it does not alter their internals
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /admin/domain-claims                             (AUTH: _get_owner_identity — OWNER only)
  body: { domain: str }
  201 -> { claim_id: uuid, domain: str, status: "pending", dns_record_type: "TXT",
           dns_record_name: str,   # "_ai-proxy-challenge.<domain>"
           dns_record_value: str,  # "ai-proxy-domain-verification=<43-char urlsafe token>"
           expires_at: datetime }  # now + 7 days; re-POST before/after expiry reissues (M2)
  400 -> { code: "ERR_DOMAIN_INVALID" }             # R1 — malformed/single-label/IP domain
  401 -> { code: "ERR_AUTH_TOKEN_INVALID" }         # R4 — reused verbatim
  403 -> { code: "ERR_AUTH_FORBIDDEN_OWNER_REQUIRED" }  # R3 — reused verbatim (oidc/saml precedent)
  409 -> { code: "ERR_DOMAIN_ALREADY_VERIFIED" }    # R2 — a DIFFERENT tenant already verified it
  429 -> { code: "ERR_RATE_LIMITED" }               # R10 — reused, Retry-After header

GET /admin/domain-claims                              (AUTH: _get_owner_identity — OWNER only)
  200 -> { claims: [ { claim_id, domain, status, dns_record_name, dns_record_value,
                        expires_at, verified_at: datetime|null } ] }   # tenant-scoped, own only
  401 -> { code: "ERR_AUTH_TOKEN_INVALID" }
  403 -> { code: "ERR_AUTH_FORBIDDEN_OWNER_REQUIRED" }

POST /admin/domain-claims/{claim_id}/verify           (AUTH: _get_owner_identity — OWNER only)
  200 -> { claim_id: uuid, domain: str, status: "verified", verified_at: datetime }
  400 -> { code: "ERR_DOMAIN_VERIFICATION_FAILED" }  # R5 — TXT record missing or value mismatch
  401 -> { code: "ERR_AUTH_TOKEN_INVALID" }
  403 -> { code: "ERR_AUTH_FORBIDDEN_OWNER_REQUIRED" }
  404 -> { code: "ERR_DOMAIN_CLAIM_NOT_FOUND" }      # R9 — unknown id OR another tenant's claim
  409 -> { code: "ERR_DOMAIN_ALREADY_VERIFIED" }     # R7 — lost a genuine cross-tenant race
  410 -> { code: "ERR_DOMAIN_CLAIM_EXPIRED" }        # R6 — re-POST /admin/domain-claims to reissue
  429 -> { code: "ERR_RATE_LIMITED" }                # R10
  503 -> { code: "ERR_DNS_LOOKUP_FAILED" }           # R8 — resolver error/timeout, fail-closed

DELETE /admin/domain-claims/{claim_id}                (AUTH: _get_owner_identity — OWNER only)
  204 -> (no body)                                    # M7 — revoke, any status; never retroactive
  401 -> { code: "ERR_AUTH_TOKEN_INVALID" }
  403 -> { code: "ERR_AUTH_FORBIDDEN_OWNER_REQUIRED" }
  404 -> { code: "ERR_DOMAIN_CLAIM_NOT_FOUND" }      # R9

POST /admin/auth/signup                               (PUBLIC — no auth; MODIFIED, one new guard
                                                         inserted ABOVE the frozen S1 guard, M8)
  body: SignupRequest { tenant_name: str, email: EmailStr, password: str }   # UNCHANGED shape
  201 -> SignupResponse { tenant_id: uuid, user_id: uuid,
                           joined_existing_tenant: bool }  # NEW additive field, default false (M12)
  400 -> { code: "ERR_AUTH_PASSWORD_WEAK" }     # R13/S1-R2 — reused, both paths
  403 -> { code: "ERR_SIGNUP_INVITE_ONLY" }     # R11/S1-R1 — reused, unmatched-domain path only
  409 -> { code: "ERR_AUTH_EMAIL_TAKEN" }       # R12/S1-R3 — reused, both paths

Schema:
  NEW table `tenant_domain_claims` (additive migration, no edit to any existing table):
    id              uuid PRIMARY KEY (uuid7)
    tenant_id       uuid NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT
    domain          text NOT NULL     -- lowercased, validated hostname shape (M3)
    verification_token  text NOT NULL -- secrets.token_urlsafe(32); PLAINTEXT (not a secret — see
                                       -- §0 Honors; it is published in public DNS by design)
    status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','verified'))
    created_at      timestamptz NOT NULL DEFAULT now()
    verified_at     timestamptz NULL
    expires_at      timestamptz NOT NULL
    created_by_user_id  uuid NOT NULL REFERENCES users(id)
  Indexes:
    UNIQUE (tenant_id, domain)                                    -- one row per tenant per domain
    UNIQUE (domain) WHERE status = 'verified'                     -- M1's structural collision guard,
                                                                   -- mirrors tenants_platform_kind_uidx
  Access pattern:
    CLAIM CREATE (domain_capture/api/domain_claims_router.py, NEW file):
      1. _get_owner_identity(request, session) -> Identity | 401 | 403
      2. rate_limiter.check(action="create", key=identity.tenant_id, limit=settings.
         domain_claim_create_rpm) -> 429 on exceed (M14)
      3. validate+normalize body.domain (M3) -> 400 ERR_DOMAIN_INVALID on failure, zero DB IO
      4. SELECT 1 FROM tenant_domain_claims WHERE domain = :d AND status = 'verified' AND
         tenant_id != :self -> 409 ERR_DOMAIN_ALREADY_VERIFIED if found (M4, UX pre-check)
      5. INSERT ... ON CONFLICT (tenant_id, domain) DO UPDATE SET verification_token = :new,
         expires_at = :new, status = CASE WHEN status='verified' THEN 'verified' ELSE 'pending' END
         -- mirrors InviteRepository.create_or_replace's upsert-on-reissue shape (M2); a claim
         -- that is ALREADY verified is never regressed back to pending by a reissue attempt
      6. 201 -> render dns_record_name/value from domain+token
    CLAIM VERIFY (domain_capture/application/verify_use_case.py, NEW file):
      1. _get_owner_identity -> 401 | 403
      2. rate_limiter.check(action="verify", ...) -> 429 (M14)
      3. SELECT * FROM tenant_domain_claims WHERE id = :id AND tenant_id = :self ->
         404 ERR_DOMAIN_CLAIM_NOT_FOUND if none (R9, tenant-scoped, indistinguishable)
      4. now > expires_at -> 410 ERR_DOMAIN_CLAIM_EXPIRED (R6)
      5. dns_resolver.lookup_txt(f"_ai-proxy-challenge.{domain}", timeout=settings.
         domain_verification_dns_timeout_seconds) -> resolver error/timeout -> 503
         ERR_DNS_LOOKUP_FAILED (R8, M13); success but no exact "ai-proxy-domain-verification=
         <token>" match -> 400 ERR_DOMAIN_VERIFICATION_FAILED (R5)
      6. exact match -> UPDATE tenant_domain_claims SET status='verified', verified_at=now()
         WHERE id = :id AND status = 'pending' -- guarded by the partial unique index; a
         concurrent winner elsewhere -> IntegrityError -> 409 ERR_DOMAIN_ALREADY_VERIFIED (R7, M6)
      7. 200 -> render updated claim
    CLAIM REVOKE: DELETE FROM tenant_domain_claims WHERE id = :id AND tenant_id = :self ->
      0 rows affected -> 404 ERR_DOMAIN_CLAIM_NOT_FOUND; else 204 (M7) — no cascade, no effect on
      any existing `users` row.
    SIGNUP (tenants/api/router.py:signup, MODIFIED — one new guard clause + one new branch, the
      existing S1 guard and SignupUseCase.execute call are otherwise untouched):
      1. domain = body.email.rsplit("@", 1)[-1].lower()   # safe: EmailStr already validated
      2. tenant_id = await domain_claim_resolver.resolve_verified_tenant(domain)   # NEW, indexed
         SELECT tenant_id FROM tenant_domain_claims WHERE domain = :d AND status = 'verified'
         -- runs BEFORE the public_signup_enabled check, by design (M8, §1 ⚠ TOP)
      3. IF tenant_id is not None:
           user_id = await join_use_case.execute(tenant_id, body.email, body.password)
             -- WeakPasswordError -> 400 AUTH_PASSWORD_WEAK (R13)
             -- EmailAlreadyRegisteredError -> 409 AUTH_EMAIL_TAKEN (R12)
           return 201 SignupResponse(tenant_id, user_id, joined_existing_tenant=True)   # M9-M12
      4. (UNCHANGED, S1) IF NOT settings.public_signup_enabled: raise SIGNUP_INVITE_ONLY.exc()  # R11
      5. (UNCHANGED, S1) SignupUseCase.execute(...) -> byte-identical to today             # M8

NEW error_catalog.py entries (sibling to SIGNUP_INVITE_ONLY / SAML_DOMAIN_ALREADY_CLAIMED, "tenant
  identity" / new "domain capture" section):
  DOMAIN_INVALID = ErrorSpec(400, "ERR_DOMAIN_INVALID", "Domain is not a valid claimable hostname")
  DOMAIN_ALREADY_VERIFIED = ErrorSpec(409, "ERR_DOMAIN_ALREADY_VERIFIED",
      "This domain is already verified by another tenant")
  DOMAIN_VERIFICATION_FAILED = ErrorSpec(400, "ERR_DOMAIN_VERIFICATION_FAILED",
      "The expected DNS TXT record was not found or did not match")
  DOMAIN_CLAIM_EXPIRED = ErrorSpec(410, "ERR_DOMAIN_CLAIM_EXPIRED",
      "This verification challenge has expired; request a new one")
  DOMAIN_CLAIM_NOT_FOUND = ErrorSpec(404, "ERR_DOMAIN_CLAIM_NOT_FOUND", "Domain claim not found")
  DNS_LOOKUP_FAILED = ErrorSpec(503, "ERR_DNS_LOOKUP_FAILED",
      "DNS lookup failed or timed out; try again")
  (REUSED, not new: AUTH_TOKEN_INVALID · AUTH_FORBIDDEN_OWNER_REQUIRED · AUTH_PASSWORD_WEAK ·
   AUTH_EMAIL_TAKEN · SIGNUP_INVITE_ONLY · RATE_LIMITED — every other rejection is verbatim reuse.)

NEW Settings fields (core/config.py, sibling to public_signup_enabled/invite_preview_rpm):
  domain_verification_dns_timeout_seconds: float = 5.0   # GATEWAY_DOMAIN_VERIFICATION_DNS_TIMEOUT_SECONDS
  domain_claim_create_rpm: int = 10                       # GATEWAY_DOMAIN_CLAIM_CREATE_RPM
  domain_claim_verify_rpm: int = 30                       # GATEWAY_DOMAIN_CLAIM_VERIFY_RPM

NEW dependency: `dnspython` promoted from transitive to an explicit direct dependency in
  `apps/gateway/pyproject.toml` (M16) — no version pin change (already resolved at 2.8.0 in
  uv.lock).

NO edit to any FROZEN file: `signup-and-routing-authz` TASK.md's own contract is untouched (its
  M1-M11 remain byte-identical); `Permission`/`ROLE_PERMISSIONS` (authz.py) is untouched (no new
  Permission minted); `saml_provider_configs`/`oidc_provider_configs` schemas are untouched (this
  task's domain mapping is a separate table, not a repurposing of `email_domains`).
```

Glossary deltas:
  - `domain claim` (NEW term): a tenant's assertion of ownership over an email domain, recorded as a
    `tenant_domain_claims` row; `status: pending` until DNS-TXT-verified, `status: verified`
    thereafter. Distinct from `oidc_claim_mapping` (existing GLOSSARY term, env/SAML-config-driven,
    governs SSO login-initiation routing) — domain claim governs plain password `POST /admin/auth/
    signup` routing only; the two are independent, currently-unreconciled mapping surfaces (§0
    Issues, flagged forward).
  - `verified domain` (NEW term): a `domain claim` whose DNS TXT ownership challenge has been
    successfully checked; the ONLY status that ever influences signup routing (M15). A verified
    domain is unique across the WHOLE gateway (at most one tenant, enforced by a Postgres partial
    unique index) — an unverified (pending/expired/revoked) claim carries no such uniqueness and
    never blocks another tenant's own claim attempt on the same domain.
  - `domain-capture join` (NEW term): the signup path where `POST /admin/auth/signup`'s email
    matches a verified domain and the caller is added to that EXISTING tenant (role=member) instead
    of a brand-new tenant being created — signaled by `SignupResponse.joined_existing_tenant ==
    true`. An explicit, S1-compatible EXTENSION of the invite-only default (frozen
    `signup-and-routing-authz` TASK.md), scoped to exactly the domains a tenant OWNER has proven
    control of — never a supersession of S1's own default.

Status: FROZEN @ v1 — approved by Tin Dang
Reported: yes — presented for freeze 2026-07-10.
Decided at freeze (Tin, 2026-07-10): domain-claim lookup runs BEFORE the S1 `public_signup_enabled`
check (option A). Recorded as a disclosed, S1-compatible AMENDMENT to S1 M2's zero-IO-when-disabled
detail (outward S1 behavior unchanged for every non-matching domain); NOT a supersession. Secondary
flag (DNS TXT subdomain-label vs apex): confirmed subdomain label `_ai-proxy-challenge.<domain>`.

Least-sure flag surfaced at freeze: ⚠ [contract] the domain-claim lookup in `signup()` runs BEFORE
the frozen S1 `public_signup_enabled` check — the ONE place this task's contract touches a FROZEN
sibling's own stated property ("checked FIRST... zero DB IO," S1 M2). This is recorded as a narrow,
disclosed AMENDMENT (S1's OUTWARD behavior for every non-matching domain is unchanged; only the
internal zero-IO detail changes for the specific new domain-claim lookup), not a supersession — but
it is genuinely Tin's call, the same way S1's own three flags were. Alternative: check
`public_signup_enabled` first, only consult domain claims when already `True` — a one-line reversal,
but makes this entire feature inert on S1's own shipped prod default (`publicSignupEnabled: false`).
Cost if wrong: either (a) an unreviewed amendment to a frozen contract ships, or (b) the feature
ships functionally dead in the majority real deployment shape. Second-highest: [spec] DNS TXT record
scoped to a subdomain label (`_ai-proxy-challenge.<domain>`) rather than the domain apex — low
stakes/reversible, but user-facing the moment a real tenant admin sees it (§1 ⚠ #2).

SECURITY task: the VERIFY gate is Tin's HARD-STOP regardless of autonomy (this freeze, once
granted, authorizes BUILD only — matches saml-sso's and S1's own header discipline).

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: <e.g. 90%>
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_<scenario>: arrange <Given> / act <When> / assert <Then> + assert <unchanged> · covers: <M#, R:code — optional>
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/domain_capture/` (NEW module — domain/application/
  infrastructure/api, clean-architecture-per-module) · `apps/gateway/src/gateway/tenants/api/
  router.py` (signup() — one new guard clause + one new branch only) · `apps/gateway/src/gateway/
  tenants/api/schemas.py` (SignupResponse — one new additive field) · `apps/gateway/src/gateway/
  tenants/domain/ports.py` (IdentityRepository — one new additive method) · `apps/gateway/src/
  gateway/tenants/infrastructure/repository.py` (join_verified_tenant_domain — new method only) ·
  `apps/gateway/src/gateway/core/config.py` (3 new Settings fields) · `apps/gateway/src/gateway/
  core/error_catalog.py` (6 new ErrorSpec entries) · `apps/gateway/src/gateway/main.py` (wire the
  new router + app.state.domain_claim_resolver/rate_limiter/dns_resolver, mirrors saml/oidc wiring)
  · `apps/gateway/migrations/versions/` (one new additive migration: tenant_domain_claims) ·
  `apps/gateway/pyproject.toml` (promote dnspython to a direct dependency) · `apps/gateway/tests/`
Strategy (ordered batches):
  1. Schema first: the `tenant_domain_claims` migration (both indexes — plain UNIQUE(tenant_id,
     domain) and the partial UNIQUE(domain) WHERE status='verified') — get this reviewable and
     tested against a real Postgres (SQLite/aiosqlite test doubles do not enforce partial indexes
     identically; verify the collision-race scenario against the REAL migration, not create_all).
  2. `domain_capture/domain/` — entities (DomainClaim), ports (DnsTxtResolver Protocol,
     DomainClaimRepository Protocol), errors — zero framework imports (CONVENTIONS.md discipline).
  3. `domain_capture/infrastructure/` — SqlAlchemyDomainClaimRepository (the ON CONFLICT
     upsert-on-reissue + the atomic UPDATE...WHERE status='pending' verify-flip) and
     DnsPythonTxtResolver (dns.asyncresolver, bounded timeout, single attempt, fail-closed).
  4. `domain_capture/application/` — CreateDomainClaimUseCase, VerifyDomainClaimUseCase,
     RevokeDomainClaimUseCase, JoinTenantByDomainUseCase — one execute() per use case, mirrors
     SignupUseCase's check-then-insert shape.
  5. `domain_capture/api/domain_claims_router.py` — the 4 new endpoints, `_get_owner_identity`
     reused verbatim (import from oidc_admin_router or hoist to a shared module if that's cleaner —
     builder's call, ask if genuinely ambiguous), rate limiter reused from the invite pattern.
  6. `tenants/api/router.py::signup()` — the ONE new guard clause + branch, inserted ABOVE the
     existing S1 check; run the FULL signup-and-routing-authz test file unmodified afterward as a
     regression gate before touching anything else.
  7. Wire `main.py` (domain_claim_resolver, rate limiter, DNS resolver into app.state) + Settings +
     error_catalog entries + pyproject.toml dnspython promotion.

Persona (required): generic (backend-architect's clean-architecture-per-module discipline + the
  appsec-engineer's escalation/tenant-isolation lens both apply here per this project's OWN
  conventions — see §0 Honors — but neither persona file is `flow: build`-tagged specifically for a
  brand-new bounded-context module; the builder should read both `.add/personas/backend-
  architect.md` and `.add/personas/appsec-engineer.md` as domain stance even though this line names
  "generic").
Spawn isolation (default): worktree — this is a security-sensitive, HARD-STOP-verify task; isolate
  the build from any concurrent wave-2 sibling work.
Known-problem fixes:
  - trap: reusing `_get_or_provision_sso_user`'s get-or-return-existing branch for the join path ->
    fix: use the NEW INSERT-only `join_verified_tenant_domain` method instead (§0 Issues, M9) —
    do NOT take the shortcut of parameterizing the existing helper.
  - trap: an app-level SELECT-then-INSERT as the ONLY collision defense (saml-sso's own documented
    residue) -> fix: the partial unique index IS the defense; the SELECT pre-check (M4) is UX only,
    never remove the DB constraint in favor of "the pre-check already handles it."
  - trap: testing the collision race against SQLite/`create_all` only -> fix: run at least the
    race scenario against a real Postgres test DB (this repo already runs Postgres-backed tests
    elsewhere — mirror that harness, do not skip to a SQLite shortcut for this one scenario).
  - trap: DNS lookup without a bounded timeout hanging a request -> fix: pass an explicit `lifetime`/
    `timeout` to dnspython's resolver call; a slow/non-responding nameserver must 503, never hang.
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): the claim-creation upsert (step in access pattern, "INSERT ... ON
  CONFLICT DO UPDATE") and the verify-flip UPDATE (M6) each run as ONE atomic statement — no
  read-modify-write window between checking current status and writing the new one; the collision
  guard's correctness depends entirely on this being a single statement, not application-level
  check-then-write.
Code lives in: `apps/gateway/src/gateway/domain_capture/`
Constraints: do NOT change any test or the contract; allow-list packages only (dnspython already
  vendored, no new external service); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token with "/" = project root · a bare name = sibling of the previous token's dir · a DIRECTORY token covers its whole subtree (diverges from §4's non-recursive counting) · outside-root resolutions drop fail-closed · absent line = UNDECLARED (grandfathered, never retro-red) · enforcement live: a completing verify gate refuses an out-of-scope build (scope_violation → self-heal); check surfaces it. EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 42/42 (`tests/domain_capture/` 20 + `tests/signup_routing_authz/` 22), real Postgres (`gateway_test_vdc`), `uv run pytest tests/domain_capture/ tests/signup_routing_authz/ -q --no-cov` → `42 passed in 113.52s`
- [x] coverage did not decrease — new `domain_capture/` module: 90-100% per file except the real DNS adapter (see Residual risks); `tenants/api/router.py` new guard clause fully covered by the combined suite
- [x] no test or contract was altered during build — `git diff` of `tests/domain_capture/`, `tests/signup_routing_authz/`, and TASK.md §3 shows zero build-phase edits (only my throwaway probe file, created+deleted this pass, never committed)
- [x] the green was EARNED, not gamed — adversarial refute-read below; 4 live-executed attacks, all held
- [x] concurrency / timing of the risky operation is safe — TRUE `asyncio.gather` concurrent verify race executed against real Postgres (not the suite's own sequential race test) — exactly 1 winner, 1 loser, structurally enforced by the partial unique index
- [x] no exposed secrets, injection openings, or unexpected dependencies — `verification_token` is deliberately plaintext (not a secret, disclosed); all SQL is parameterized (SQLAlchemy Core, no string interpolation); `dnspython` promoted to a pinned direct dependency, no new external service
- [x] layering & dependencies follow CONVENTIONS.md — `domain/` has zero framework imports (confirmed by reading `domain_validation.py`, `entities.py`, `ports.py`); one repository class serves two Protocol ports via structural typing (documented, mirrors `SqlAlchemyIdentityRepository`)
- [ ] a person reviewed and approved the change — pending Tin's HARD-STOP gate (this task's `sensitivity: security` requires human sign-off regardless of this verdict)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [x] An unmatched/pending/expired/revoked domain leaves `signup()` byte-identical to frozen S1 — confirmed by re-running `tests/signup_routing_authz/` unmodified (22/22 green) AND a live throwaway probe: unclaimed-domain signup with flag ON created a new tenant, `tenant_domain_claims` row count unchanged (zero write from the read-only lookup)
- [x] A verified-domain signup joins the EXISTING tenant even while invite-only, `joined_existing_tenant=true`, zero new `tenants` row — confirmed by `test_verified_domain_signup_joins_existing_tenant_while_invite_only` (green) and by DB row-count assertions in the suite
- [x] Exactly one tenant may hold a `verified` claim on any domain, enforced at the DB layer under TRUE concurrency, not just app-level ordering — confirmed by my own `asyncio.gather` throwaway probe (real overlapping HTTP requests, real Postgres): statuses `[200, 409]`, `verified_count == 1`
- [x] A revoked claim stops future signups immediately but never touches already-joined users — confirmed by `test_revoke_verified_claim_stops_future_signups_not_existing_members` (green)
- [x] A DNS failure (NXDOMAIN/timeout/resolver error) never marks a claim verified — confirmed by suite (`FakeDnsResolver`) AND a live hand-run of the REAL `DnsPythonTxtResolver` against real DNS: NXDOMAIN → `DnsLookupFailedError`, 0.0001s timeout → `DnsLookupFailedError`, real TXT lookup (google.com) → 14 records correctly parsed
- [x] The join path rejects (never silently logs in) an already-registered email, existing user's credentials untouched — confirmed by suite AND my own throwaway "hijack" probe: attacker-submitted signup against a victim's already-verified-domain email → 409, victim's `password_hash` unchanged before/after

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new repository/use-case method traced to a live caller: `list_for_tenant`→router GET, `get_own`→verify use case, `revoke`→revoke use case, `resolve_verified_tenant`→`tenants/api/router.py:signup()`, `mark_verified`→verify use case, `create_or_reissue`→create use case (grep-confirmed, zero orphans); `main.py` includes `domain_claims_router`, wires `domain_claim_rate_limiter`/`dns_resolver`/`domain_claim_repository`(None sentinel)/`domain_claim_resolver`(None sentinel) on `app.state`, imports `orm.py` to register `TenantDomainClaimRow` on `Base.metadata`
- [x] DEAD-CODE (code) — no new unused symbol found; pyright clean (`uv run pyright src/gateway/domain_capture/ src/gateway/tenants/api/router.py` → `0 errors, 0 warnings, 0 informations`)
- [x] SEMANTIC (prose) — §0-§5 read in full; the ⚠-flagged S1 M2 amendment (domain lookup runs BEFORE `public_signup_enabled`) is exactly what's shipped (`tenants/api/router.py` — the guard clause + comment matches §3's access-pattern prose verbatim, incl. the documented `session.rollback()` autobegin workaround)

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
- [x] every symbol §3 CONTRACT cites still resolves in the current tree — confirmed via `mcp__serena`/grep: `signup()` (`tenants/api/router.py:43`), `IdentityRepository.join_verified_tenant_domain` (`tenants/infrastructure/repository.py:88`), `_get_owner_identity` (duplicated verbatim in `domain_claims_router.py:69`, matching the SAML/OIDC precedent's own documented duplication convention), `tenants_platform_kind_uidx`-style partial index → `uq_domain_claims_domain_verified` (migration `b3d8e1f4a7c2`), single alembic head confirmed (`uv run alembic heads` → `69cfdc584129 (head)`, `tenant_domain_claims` correctly parented at `a55ddcebaac6`)
- [x] no anchor moved/renamed since Ground SHA — one cosmetic-only discrepancy named below (migration docstring vs `down_revision` field), not a functional break

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: self (add-verify, appsec-engineer persona) · adversarially checked, 4 attacks LIVE-EXECUTED (not argued on paper) via a throwaway pytest file (`tests/domain_capture/test_zz_adversarial_throwaway.py`, deleted after this pass):
  1. TRUE `asyncio.gather` concurrent verify race (real overlapping requests, real Postgres, both tenants' claims seeded pending beforehand) — HELD: `[200, 409]`, exactly 1 verified row, structural DB-level guard confirmed independent of app-level statement ordering.
  2. Domain-normalization bypass attempt: `EvilCorp.com` → normalizes to `evilcorp.com`; `evilcorp.com.` (trailing dot / FQDN form) → rejected outright 400 `ERR_DOMAIN_INVALID` (the trailing empty label fails the 1-63-char label check) — HELD, no distinct-domain bypass of the uniqueness guard.
  3. Unclaimed-domain signup write-side probe: confirmed the new M8 domain-resolver SELECT never writes `tenant_domain_claims` (row count unchanged pre/post), and the S1 fallback path still creates a normal new tenant — HELD, matches "an unverified domain changes nothing" and S1's zero-observable-behavior-change property.
  4. Account-hijack-via-domain-capture probe: attacker submits a "signup" for a victim's email that's already registered under an already-verified domain, with an attacker-chosen password — 409 `AUTH_EMAIL_TAKEN` (actual wire code `ERR_TENANT_EMAIL_TAKEN`, see Residual risks), victim's `password_hash` byte-identical before/after — HELD, the `_get_or_provision_sso_user` get-or-return-existing bug class this task explicitly designed around is confirmed absent.
Also live-verified (not stubbed): the REAL `DnsPythonTxtResolver` adapter against real DNS (real TXT lookup succeeds + parses multi-string records; forced NXDOMAIN and forced 0.0001s timeout both fail CLOSED via `DnsLookupFailedError`) — closes a real coverage gap the merged suite itself leaves at 27% (suite only exercises the `FakeDnsResolver` stub, never the real adapter) — see Residual risks.

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
Advisor: self (add-verify, appsec-engineer persona)
1. Security: CLEAR — OWNER-only gate reused verbatim from the OIDC/SAML precedent; tenant-scoped queries filter in the SAME statement as existence checks (`get_own`, `revoke`) with the mandated indistinguishable 404; `verification_token` plaintext-at-rest is a disclosed, reasoned exception (not a secret, published in public DNS by design) not a violation of the Fernet-at-rest floor (that floor applies to actual secrets); no SQL string interpolation anywhere in the new module; the account-hijack/enumeration attack this task's own §0 Issues names as the reason to avoid `_get_or_provision_sso_user` is confirmed closed by live probe.
2. Concurrency: CLEAR — the verify-flip race is closed at the DB layer (partial unique index), confirmed under TRUE concurrency, not just sequential test ordering; the create-or-reissue upsert is one atomic statement; one named, accepted, LOW-severity timing residue (see Residual risks: a revoke racing a concurrently in-flight join).
3. Architecture: CLEAR — clean-architecture-per-module discipline held (`domain/` zero framework imports, confirmed by direct read); one repository class serving two Protocol ports via structural typing is a deliberate, documented choice mirroring an existing precedent, not an accidental God-object; `_get_owner_identity` duplication (not import) matches the project's own stated SAML/OIDC precedent for avoiding a hard dependency on a sibling admin-router file.
Verdict: PASS (recommended — HARD-STOPs to Tin regardless per this task's `sensitivity: security` header)
Residue: none BLOCKER-class; 3 named residual risks below (all 💭 note / 🟡 concern severity, none 🔴)
Binding: advisory — sensitivity: security (Tin's HARD-STOP gate is the binding decision, matches this task's own header discipline)

### GATE RECORD
Reported: yes — this §6 fill is the gate report; verdict below is a RECOMMENDATION to the human HARD-STOP gate, not a self-issued PASS
Outcome: HARD-STOP (procedural — every security task in this milestone HARD-STOPs to Tin regardless of evidence quality; recommend PASS on the merits above)
Reviewed by: <Tin Dang — pending> · date: <pending>

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
