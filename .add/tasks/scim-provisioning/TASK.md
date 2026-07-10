# TASK: SCIM 2.0 user provisioning (per-tenant token)

slug: scim-provisioning · created: 2026-07-10 · stage: production
milestone: enterprise-identity-compliance
sensitivity: security   <!-- unattended machine write-path into tenant identity lifecycle (create/update/deactivate users) — milestone Shared decisions: "every identity surface is security-sensitive: HARD-STOP verify"; never auto-passed even under autonomy:auto -->
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: tests   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/tenants/domain/entities.py:User` — frozen dataclass (id, tenant_id, email, password_hash, role); **no active/inactive flag exists today**.
- `apps/gateway/src/gateway/tenants/infrastructure/orm.py:UserRow` — `users` table; `email` has a GLOBAL unique constraint (NOT per-tenant); `auth_method: Mapped[str]` (VARCHAR(32), no CHECK constraint, values today `"password"`/`"oidc"` — a third value is a pure data addition, no migration needed for the column itself); no `deactivated_at`/`is_active` column.
- `apps/gateway/src/gateway/tenants/infrastructure/repository.py:SqlAlchemyIdentityRepository.get_or_provision_oidc_user` (l.100-155) — the closest existing precedent for "provision a User row into an EXISTING tenant": role is **ALWAYS** coerced to `Role.MEMBER`, never taken from the external identity provider's claims; raises `OidcTenantConflictError` when the (globally-unique) email already exists under a different `tenant_id`. SCIM Create-User mirrors this shape exactly.
- `apps/gateway/src/gateway/tenants/application/use_cases.py:LoginUseCase.execute` (l.38-98) — loads the user by email, verifies password, issues a JWT via `TokenService.issue`; this is the ONE enforcement point for password-login deactivation (add one `deactivated_at is None` check, same style as the existing post-fetch `role == Role.SUPERADMIN` audit branch).
- `apps/gateway/src/gateway/auth/application/use_cases.py:OidcLoginUseCase` — the SSO-login sibling call site; needs the SAME deactivation check (two call sites total, both must be covered or SSO becomes a deactivation bypass).
- `apps/gateway/src/gateway/tenants/infrastructure/jwt_service.py:JwtTokenService.decode` (l.11-107) and `apps/gateway/src/gateway/tenants/domain/authz.py:_resolve_identity` (l.191-221) — session JWT verification is **stateless HMAC-only**: no per-request DB lookup of the user row (the only DB-backed liveness check that exists is `ensure_impersonation_session_live`, scoped narrowly to superadmin impersonation, not ordinary sessions). `Settings.jwt_ttl_seconds` default `86400` (`core/config.py:89`).
- `apps/gateway/src/gateway/keys/infrastructure/orm.py:ApiKeyRow` / `apps/gateway/src/gateway/keys/domain/entities.py:ApiKey` — the `revoked_at: datetime | None` nullable-timestamp soft-revoke pattern to mirror for the new SCIM token. **`api_keys` carries `tenant_id` and an optional `team_id`, but NO user-attribution column** (no `created_by_user_id`/`owner_user_id`) — a key is not owned by an individual user in this schema.
- `apps/gateway/src/gateway/keys/infrastructure/sha256_hasher.py:Sha256SecretHasher` — generic `hash()`/`verify()` (SHA-256 + `hmac.compare_digest`), zero API-key-specific state; directly reusable, unmodified, for the SCIM token secret.
- `apps/gateway/src/gateway/keys/application/use_cases.py:_KEY_PREFIX`/`_KEY_SEPARATOR` (l.16-17) and `RotateKeyUseCase.execute` (l.191-231) — the `sk-<key_id.hex>.<secret>` bearer-token format and the atomic revoke-old+issue-new rotation transaction to mirror.
- `apps/gateway/src/gateway/keys/api/deps.py:get_bearer_token` (l.40-46) — the `Authorization: Bearer <token>` extraction dependency shape to mirror for a new `get_scim_identity` dependency.
- `apps/gateway/src/gateway/teams/infrastructure/orm.py:TeamMemberRow` (`team_members` table, real `user_id` FK) — team membership genuinely is user-attributed and cascade-able (unlike API keys); `apps/gateway/src/gateway/teams/infrastructure/repository.py` (l.278-285) has the existing delete-by-`team_id`+`user_id` pattern to mirror for "remove from every team on deactivation."
- `apps/gateway/src/gateway/audit/domain/audit_event.py:AuditEvent` (l.24-60) — `__post_init__` enforces: a tenant-scoped event MUST carry `actor_user_id` OR `actor_key_id`. `actor_key_id` itself is a prior ADDITIVE field (realtime-relay-governance task) for exactly this situation ("a key-authenticated caller with no user identity"). SCIM's machine actor needs the SAME treatment: a new additive `actor_scim_token_id`.
- `apps/gateway/src/gateway/audit/application/audit_writer.py:record_audit` — fire-and-forget, own DB session, swallow-all-exceptions, scheduled via `asyncio.ensure_future`; the exact anchor every SCIM mutation's audit write reuses verbatim.
- `apps/gateway/src/gateway/tenants/domain/authz.py:Permission.MEMBERS_MANAGE`, `ROLE_PERMISSIONS` (l.76-121, OWNER+ADMIN hold `MEMBERS_MANAGE`), `require_permission` (l.229-252) — the existing permission to reuse (unmodified) for the SCIM-token-management admin API; no new `Permission` enum member needed.
- `infra/envoy/envoy.yaml` jwt_authn rules (l.93-105) and route table (l.159-225): the catch-all `prefix: "/"` route has **both** `ext_authz` and the `jwt_authn` requirement **already inactive** (only `/admin/*` requires the session JWT; only `/v1/*` requires ext_authz). A router mounted at `/scim/v2/*` falls into this catch-all today with zero Envoy edit.
- `apps/gateway/src/gateway/tenants/infrastructure/invite_public_rate_limiter.py:InvitePublicRateLimiter` (l.42-98) — fixed 60s-window Redis `INCR`+`EXPIRE` limiter, fail-open on Redis error, action-discriminated key (`{action}:{key}:{bucket}`); the pattern to mirror (keyed by `scim_token_id` instead of client IP, since SCIM callers are authenticated).
- `apps/gateway/src/gateway/tenants/domain/entities.py:Role` (l.9-20) — `MEMBER` is the only role SCIM may ever assign; SUPERADMIN is DB-trigger-restricted to the platform tenant and is structurally unreachable from a tenant-scoped SCIM token.

Context (working folder): `apps/gateway/migrations/versions/` (Alembic, single linear chain, current head confirmed via `alembic heads` = `511ad8a7b65e`, additive-only convention); `.add/GLOSSARY.md` (no prior SCIM term — naming is open; existing `ops-auth` term is the precedent for "a third bearer-credential surface distinct from the tenant JWT and the API-key ext_authz surface"); `apps/gateway/pyproject.toml` dependencies — no SCIM library present or needed (RFC 7644 shapes are simple enough to hand-roll with pydantic, consistent with this codebase's existing hand-rolled translators); `.add/tasks/scim-provisioning/{src,tests}/` are empty scaffolds.

Honors (patterns / conventions): CLEAN ARCHITECTURE per module (`domain/` ports+entities ← `application/` use cases ← `infrastructure/` adapters ← `api/` routers) — a new `gateway/scim/` module follows this shape; every tenant-owned row stays `tenant_id`-scoped (PROJECT.md invariant); additive-only migrations with documented rollback; `ERR_<DOMAIN>_<REASON>` RFC 9457 problem+json is the project-wide error convention — SCIM is a DELIBERATE, scoped exception (RFC 7644 mandates its own SCIM error envelope for `/scim/v2/*` so real IdPs can parse `scimType`), the same kind of accepted, documented inconsistency as the S4 edge-input-hardening Envoy-native-413-body carve-out (`.add/tasks/edge-input-hardening/TASK.md §3 Part C`); anti-enumeration convention — a security-sensitive failure path returns byte-identical responses across failure modes (CONVENTIONS.md "Folded from v1"), applied to deactivated-vs-wrong-password login; multi-row sync/lifecycle operations commit in ONE transaction (PROJECT.md "Settled" — deactivation's DB-side effects: `users.deactivated_at` + `team_members` delete-by-user commit atomically).

Anchors the contract cites: `UserRow` (+ new `deactivated_at` column), `AuditEvent` (+ new `actor_scim_token_id` field), `Sha256SecretHasher`, `RotateKeyUseCase`-style atomic rotation, `TeamMemberRow`, `require_permission(Permission.MEMBERS_MANAGE)`, `record_audit`, `InvitePublicRateLimiter`-style limiter, the Envoy catch-all route.

Issues/Risks (→ feed §1):
- **No user-active-flag exists** — `UserRow` has no `deactivated_at`/`is_active`. SCIM `active:false` needs a genuinely NEW additive column; this is real schema surface, not a data-only change.
- **API keys are not user-attributed** — the milestone/dispatch language ("what happens to API keys" on deactivation) cannot be honored literally: there is no `user_id` column on `api_keys` to cascade from. Faking a cascade (e.g. guessing "keys created around the same time") would violate the project's honest-degradation invariant. §1 must state this as an explicit, named scope boundary, not silently drop it.
- **Session JWTs are stateless and NOT revocable today** — deactivating a user blocks NEW login/token issuance but cannot invalidate an ALREADY-ISSUED JWT (no session store for ordinary logins, unlike the narrow impersonation-session mechanism). Residual exposure window = up to `jwt_ttl_seconds` (86400s / 24h default). Building a full session-revocation store is a large, separate architectural change — out of this task's size; must be a named, bounded residual risk, not a silent gap.
- **`users.email` is GLOBALLY unique, not per-tenant** — a SCIM Create-User whose email already exists (in this tenant OR another) must be rejected (SCIM 409 uniqueness), never silently merged/attached — mirrors `OidcTenantConflictError`'s existing precedent but SCIM Create is spec-non-idempotent (unlike OIDC's get-or-create), so ANY existing-email match on POST rejects.
- **Groups→teams mapping is explicitly deferrable** per MILESTONE.md scope note ("SCIM Groups beyond a basic team mapping if design finds it heavy (defer as a delta)") — the milestone's own escape hatch; still needs one explicit, freeze-worthy call (see §1 ⚠).
- **AuditEvent's actor invariant has no "machine SCIM token" actor kind** — without an additive field, every SCIM mutation would either raise `ValueError` (the `__post_init__` guard) or require misusing `actor_key_id` (semantically wrong — an API key and a SCIM token are different credential kinds with different blast radii, mirrors the project's own "audit event distinct from alert event" / "SUPERADMIN role-only, not a Permission" precedent of not conflating adjacent-but-distinct concepts).
- **This task's dispatch scope names only the backend surface**; the MILESTONE.md's "UI/UX in scope" line (SCIM token management admin surface) applies milestone-wide but no sibling task owns a SCIM dashboard screen — flagged as a freeze question (§3), not silently built or silently dropped.

Related intent: PROJECT.md "Invariants" (tenant scoping, no parallel identity store) and the "role assignment is a security surface distinct from team membership" DDD fold (mirrors why SCIM role is never client-controlled); GLOSSARY `ops-auth` (precedent for a distinct machine-credential surface) and `API key` (SHA-256-at-rest precedent); MILESTONE.md `enterprise-identity-compliance` Shared decisions ("all five surfaces are tenant-scoped config on EXISTING primitives — no parallel identity or audit stores"; "every identity surface is security-sensitive: HARD-STOP verify").

Ground SHA: `2071046`

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: SCIM 2.0 user provisioning (per-tenant token)
Framings weighed:
  A. (chosen) SCIM as a THIRD bearer-credential surface (mirrors the `ops-auth` glossary precedent — its own token kind, its own hash, its own auth dependency), driving the EXISTING `UserRow`/`TeamMemberRow` via two small additive fields (`users.deactivated_at`, `audit_events.actor_scim_token_id`) and one new value on the existing `auth_method` column. No parallel identity store.
  B. Reuse `api_keys` with a `kind` discriminator column for SCIM tokens — rejected: conflates a proxy-billing credential (tenant/team-scoped, low blast radius) with an unattended identity-write credential (can create/deactivate USERS — high blast radius); breaks the project's own established precedent of keeping adjacent-but-distinct concepts in separate tables (mirrors "audit event distinct from alert event", "SUPERADMIN role-only, never a `Permission`").
  C. Model SCIM Groups as a first-class parallel resource with its own membership store — rejected for v1 per MILESTONE.md's explicit deferral allowance; a read-only, spec-compliant empty `Groups` collection plus a truthful `ServiceProviderConfig` capability flag is chosen instead (see ⚠ below).
Must:
<must>
  - M1: an OWNER/ADMIN (via `require_permission(Permission.MEMBERS_MANAGE)` — reused unmodified, no new `Permission`) manages SCIM tokens for their own tenant: create, list, rotate, revoke. A token's secret is shown exactly once at creation/rotation, stored only as a SHA-256 hash (`Sha256SecretHasher`, reused unmodified). Format `scim-<token_id.hex>.<secret>` (mirrors `sk-<key_id.hex>.<secret>`). A tenant may hold multiple live tokens (supports zero-downtime rotation: mint new, cut IdP over, revoke old).
  - M2: every `/scim/v2/*` request authenticates via `Authorization: Bearer scim-...`, resolved in-app (new dependency mirroring `keys/api/deps.py:get_bearer_token` + hash lookup) to a `(tenant_id, scim_token_id)` pair. No Envoy edit is required for this to work (catch-all route already has ext_authz/jwt_authn inactive) — an explicit, additive `/scim/` route block is added to `infra/envoy/envoy.yaml` + `envoy-prod.yaml` + `charts/ai-proxy/templates/envoy-configmap.yaml` anyway, as documentation-as-config insurance against future catch-all drift (byte-identical behavior to today; belt-and-suspenders, not a behavior change).
  - M3: `POST /scim/v2/Users` creates a `UserRow` scoped to the token's OWN `tenant_id`, `role` hard-coded to `Role.MEMBER` (never read from the SCIM payload — mirrors `get_or_provision_oidc_user`'s "role ALWAYS member, never from claims"), `auth_method="scim"`, a non-authenticatable password-hash sentinel (mirrors the existing `SSO_PASSWORD_HASH_SENTINEL` pattern with a SCIM-distinct sentinel value so audit/debugging can tell provisioning origin apart from the hash alone).
  - M4: `GET /scim/v2/Users` supports `filter=userName eq "<value>"` (the one filter IdPs require for pre-create existence checks) plus `startIndex`/`count` pagination (RFC 7644 §3.4.2); `GET /scim/v2/Users/{id}` reads one user. Every read is scoped `WHERE tenant_id = :token_tenant_id` in addition to the token's own construction-time tenant binding (defense-in-depth, mirrors the teams add-by-email "filter AND check independently" fold) — a cross-tenant `{id}` returns 404, never 403 or a data leak.
  - M5: `PUT`/`PATCH /scim/v2/Users/{id}` updates the SAME `UserRow` (email, name attributes); a `PATCH` with `active:false` deactivates; `active:true` reactivates. Both are idempotent — repeating the same PATCH on an already-deactivated/-active user returns 200, not an error (IdP retry-safety).
  - M6: deactivation sets `users.deactivated_at = now()` (mirrors `api_keys.revoked_at`'s nullable-timestamp soft pattern) in the SAME transaction as deleting the user's `team_members` rows (real `user_id` FK exists — genuinely cascadable, unlike API keys). `DELETE /scim/v2/Users/{id}` is an ALIAS for the same soft-deactivation (SCIM "delete" never issues a hard SQL DELETE — `users.tenant_id` is `ON DELETE RESTRICT` and usage/audit rows FK to `users`, so a literal delete is both unsafe and structurally blocked anyway).
  - M7: deactivation blocks all FUTURE authentication: `LoginUseCase.execute` and `OidcLoginUseCase` both gain a `deactivated_at IS NULL` check (two call sites, both required); the failure is BYTE-IDENTICAL to `InvalidCredentialsError` (same error, same timing shape as a bad password — anti-enumeration, mirrors CONVENTIONS.md's existing fold). Deactivation does NOT retroactively revoke an already-issued session JWT (stateless, no session store) — documented residual risk bounded by `jwt_ttl_seconds` (24h default), not silently unaddressed.
  - M8: SCIM MUST NOT attempt to touch `api_keys` on deactivation — there is no user-attribution column to cascade from in this schema; this is a stated, honest scope boundary (not a silently dropped requirement).
  - M9: `GET /scim/v2/ServiceProviderConfig`, `GET /scim/v2/ResourceTypes`, `GET /scim/v2/Schemas` are served (static per-deployment content, RFC 7644 §4), authenticated the same as every other `/scim/v2/*` route (see ⚠ below); `ServiceProviderConfig` truthfully advertises `"patch":{"supported":true}`, `"filter":{"supported":true, "maxResults": 200}`, `"group":{"supported":false}` (Part C above).
  - M10: `GET /scim/v2/Groups` returns a spec-shaped empty collection (`Resources: []`, `totalResults: 0`, HTTP 200) rather than 404/501 — many IdP setup wizards probe this even when `ServiceProviderConfig.group.supported=false`; any Groups WRITE (`POST`/`PATCH`/`PUT`/`DELETE`) returns the SCIM 501 error shape.
  - M11: every SCIM mutation (token create/rotate/revoke; user create/update/deactivate/reactivate) writes one `AuditEvent` via `record_audit` (fire-and-forget, own session — reused verbatim), `action` namespaced `scim.token_*`/`scim.user_*`, `actor_scim_token_id` set (new additive `AuditEvent` field, mirrors `actor_key_id`'s precedent exactly, including relaxing `__post_init__` to accept it as a third valid actor kind), `result` recorded on both success and rejection.
  - M12: SCIM writes are rate-limited per `scim_token_id` (new limiter mirroring `InvitePublicRateLimiter`'s fixed-60s-window Redis `INCR`+`EXPIRE`, fail-open on Redis error — same availability-over-strict-limiting posture already accepted for the sibling public-invite surface).
</must>
Reject:
<reject>
  - missing/malformed/unknown/revoked SCIM bearer token -> 401 SCIM error (`detail: "invalid_token"`, no `scimType` — RFC 7644 doesn't define one for auth failures)
  - `{id}` resolves in a DIFFERENT tenant than the bearer token's own tenant -> 404 (`urn:ietf:params:scim:api:messages:2.0:Error`, `detail: "Resource not found"`) — never 403 (tenant-confusion defense, matches the project's existing "cross-tenant access returns 404, never a leak" invariant)
  - `POST /Users` with an email that already exists (same tenant OR a different tenant) -> 409 `scimType: "uniqueness"`
  - SCIM payload attempts to set `role`/any privilege-shaped attribute (core or enterprise-extension schema) -> silently ignored, never a 400 — role is never SCIM-controlled, full stop (Must M3); this is a deliberate non-error, not an omission
  - malformed SCIM payload (missing `schemas`, missing required `userName`, invalid PATCH `op`) -> 400 `scimType: "invalidValue"`
  - PATCH targets an immutable path (e.g. `id`, `meta`) -> 400 `scimType: "mutability"`
  - SCIM token rate limit exceeded -> 429 SCIM error + `Retry-After` header (mirrors `InviteRateLimitedError`'s `retry_after` shape)
  - `POST`/`PATCH`/`PUT`/`DELETE /scim/v2/Groups` (any Groups write) -> 501 SCIM error, `detail: "Group resource management is not supported"`
  - a revoked/unknown SCIM token id passed to rotate/revoke -> 404 `ERR_SCIM_TOKEN_NOT_FOUND` (admin-API side — ordinary RFC 9457 problem+json, NOT the SCIM error envelope, since token management is an `/admin/*` surface, not `/scim/v2/*`)
</reject>
After:
<after>
  - a tenant OWNER/ADMIN holds a live, hashed-at-rest SCIM bearer token scoped to their own tenant
  - an IdP can create, read (filtered + paginated), update, deactivate, and reactivate that tenant's users through `/scim/v2/Users`, never reaching another tenant's data
  - a deactivated user cannot log in (password or OIDC) from that moment forward, is removed from every team, and the deactivation is independently auditable — while an already-issued session JWT remains valid for up to its original TTL (documented, not silently true)
  - every SCIM mutation leaves exactly one audit row attributing the SCIM token as actor
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Groups→teams mapping is DEFERRED to a v2 delta (M10/C above: read-only empty collection + truthful `group.supported=false`, no write path) rather than built now — lowest confidence because it's the one explicit ranked freeze question the milestone calls out by name, and real IdP behavior on `group.supported=false` varies (some stop probing Groups entirely, some still poll it periodically and may surface a benign "0 groups synced" state in their admin UI, not a hard error); if wrong (Tin wants basic Group→Team CRUD now): adds a full second resource type (its own PATCH `members` semantics, its own audit actions) to this same freeze — better decided before build than mid-build.
  - [ ] session-JWT non-revocation on deactivation (M7's residual risk, bounded by `jwt_ttl_seconds`=24h) is accepted as documented residual risk rather than in-scope work to build a session-revocation store — confirm or deny; if a shorter bound is required, the cheapest lever is lowering `jwt_ttl_seconds` globally (affects ALL sessions, not SCIM-specific) or a follow-up task adding a per-user token-generation counter checked at `_resolve_identity` (a real architecture change to the currently-stateless JWT design — out of this task's size).
  - [ ] SCIM discovery endpoints (`ServiceProviderConfig`/`Schemas`/`ResourceTypes`, M9) require the SAME bearer token as every other `/scim/v2/*` route, rather than being left unauthenticated per some IdPs' pre-connectivity-test conventions — confirm or deny; if wrong, a real IdP's initial "test connection" step (which typically DOES send the token together with the base URL in one form submission, per Okta/Entra SCIM app setup flows) could still fail and needs a live-IdP-verify follow-up either way (flagged as a §6 build expectation regardless of which way this is decided).
  - [ ] SCIM-provisioned password_hash sentinel is a NEW distinct value (e.g. `!scim-no-password`) rather than reusing the existing `SSO_PASSWORD_HASH_SENTINEL` verbatim — low material risk either way (both block password login identically; `auth_method="scim"` already disambiguates provisioning origin) but stated as a naming decision, not silently picked.
  - [ ] this task's dispatch scope names only the backend `/scim/v2/*` + `/admin/scim/tokens` surfaces; no sibling task in MILESTONE.md's task list owns a dashboard SCIM-token-management SCREEN even though the milestone's "UI/UX in scope" line covers it — confirmed as OUT of this task's Build scope (backend-only), to be either folded into this task's own follow-up delta or a new sibling task, not silently built here without a UI design pass.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: OWNER creates a SCIM token   # M1
  Given an authenticated OWNER of tenant T
  When they POST /admin/scim/tokens {"name": "Okta"}
  Then a 201 response returns the plaintext token exactly once, format "scim-<hex>.<secret>"
  And the stored row holds only the SHA-256 hash, tenant_id=T, revoked_at=NULL

Scenario: MEMBER cannot create a SCIM token   # M1
  Given an authenticated MEMBER of tenant T (holds no MEMBERS_MANAGE permission)
  When they POST /admin/scim/tokens
  Then a 403 ERR_AUTH_FORBIDDEN response
  And no scim_tokens row is created

Scenario: rotate a SCIM token atomically   # M1
  Given tenant T holds a live SCIM token S1
  When the OWNER POSTs /admin/scim/tokens/{S1.id}/rotate
  Then a 200 response returns a NEW plaintext token S2 exactly once
  And S1.revoked_at is set AND S2 is live, in the same transaction (S1 immediately stops authenticating)

Scenario: IdP creates a user via SCIM   # M3
  Given a live SCIM token for tenant T, no existing user with email "new@corp.example"
  When POST /scim/v2/Users {"userName": "new@corp.example", "active": true}
  Then a 201 response returns the SCIM User resource with a "member" role never echoed as settable
  And the new UserRow has tenant_id=T, role=MEMBER, auth_method="scim"
  And one audit_events row is written with actor_scim_token_id set, action="scim.user_create"

Scenario: SCIM payload attempts a role attribute   # Reject (silently ignored)
  Given a live SCIM token for tenant T
  When POST /scim/v2/Users carries an enterprise-extension "role":"owner"-shaped attribute
  Then the created user's role is MEMBER regardless (the attribute is ignored, not a 400)
  And no privilege escalation occurs

Scenario: duplicate email on create   # R (uniqueness)
  Given a user with email "dup@corp.example" already exists (in tenant T or a different tenant U)
  When a tenant-T SCIM token POSTs /scim/v2/Users {"userName": "dup@corp.example"}
  Then a 409 response with scimType="uniqueness"
  And no new UserRow is created; the existing row (in whichever tenant it belongs to) is unchanged

Scenario: cross-tenant SCIM token cannot reach another tenant's user   # M4 / R (isolation)
  Given tenant T's SCIM token and a user U2 that belongs to tenant X (X != T)
  When GET /scim/v2/Users/{U2.id} using tenant T's token
  Then a 404 response (never 403, never the user's data)
  And U2's row is completely unchanged and unread

Scenario: filter by userName   # M4
  Given tenant T has users "a@corp.example" and "b@corp.example"
  When GET /scim/v2/Users?filter=userName eq "a@corp.example"
  Then the response contains exactly one Resource (a@corp.example) with totalResults=1
  And "b@corp.example" is not included

Scenario: PATCH active:false deactivates and cascades team removal   # M6, M7
  Given user U in tenant T is an active MEMBER of team G, deactivated_at IS NULL
  When PATCH /scim/v2/Users/{U.id} {"Operations":[{"op":"replace","path":"active","value":false}]}
  Then a 200 response; U.deactivated_at is now set
  And U's team_members row for G is deleted in the same transaction
  And one audit_events row is written, action="scim.user_deactivate", actor_scim_token_id set

Scenario: deactivated user cannot log in with password   # M7
  Given user U has deactivated_at set (from the prior scenario)
  When U attempts POST /admin/auth/login with their correct password
  Then a 401 response byte-identical in shape/timing to InvalidCredentialsError (a wrong-password attempt)
  And no distinguishing signal reveals the account is deactivated vs the password is simply wrong

Scenario: deactivated user cannot log in via OIDC either   # M7
  Given user U has deactivated_at set
  When U completes a valid OIDC assertion that would otherwise resolve to U via get_or_provision_oidc_user
  Then the login is rejected (same denial family as the password path)
  And no session JWT is issued

Scenario: an already-issued session JWT survives deactivation until natural expiry   # M7 residual (documented, not a defect)
  Given user U held a valid session JWT issued 1 hour before being deactivated (TTL 24h)
  When U calls GET /admin/usage with that pre-existing JWT
  Then the request STILL succeeds (stateless JWT — no revocation store)
  And this is the documented residual window, bounded by jwt_ttl_seconds, not a silent gap

Scenario: repeated PATCH active:false is idempotent   # M5
  Given user U already has deactivated_at set
  When PATCH /scim/v2/Users/{U.id} {"Operations":[{"op":"replace","path":"active","value":false}]} is repeated
  Then a 200 response (no error) with the SAME deactivated_at value (no-op)
  And no duplicate audit_events row misrepresents a second deactivation event as new state

Scenario: reactivation clears deactivated_at   # M5
  Given user U has deactivated_at set
  When PATCH /scim/v2/Users/{U.id} {"Operations":[{"op":"replace","path":"active","value":true}]}
  Then a 200 response; U.deactivated_at is now NULL
  And U can log in with password again (subject to their still-known password hash)

Scenario: deactivation does not touch api_keys   # M8 (scope boundary, not silently dropped)
  Given tenant T has an api_keys row unrelated to any specific user (no user-attribution column exists)
  When a SCIM PATCH deactivates a user in tenant T
  Then the api_keys row's revoked_at is unchanged
  And this is documented behavior (§1 M8), not an unnoticed gap

Scenario: DELETE is an alias for deactivate, never a hard delete   # M6
  Given an active user U in tenant T
  When DELETE /scim/v2/Users/{U.id}
  Then a 204 response; U's row still exists with deactivated_at now set
  And no usage_records/audit_events FK referencing U.id is broken

Scenario: SCIM rate limit exceeded   # R (429)
  Given a SCIM token has already made the configured per-window request ceiling
  When one more request arrives inside the same window
  Then a 429 response with Retry-After
  And the request performs NO mutation (rejected before any DB write)
  And a concurrent Redis outage instead fails OPEN (request proceeds, logged WARNING) — availability preserved, matching the sibling invite-limiter posture

Scenario: Groups probe returns an honest empty collection   # M10
  Given a live SCIM token for tenant T
  When GET /scim/v2/Groups
  Then a 200 response with Resources=[] and totalResults=0
  And ServiceProviderConfig.group.supported is reported as false

Scenario: a Groups write is rejected as unsupported   # M10 / R (501)
  Given a live SCIM token for tenant T
  When POST /scim/v2/Groups {"displayName": "Engineering"}
  Then a 501 response, SCIM error envelope
  And no groups-shaped resource is created anywhere (no parallel store exists to write to)

Scenario: revoked SCIM token cannot authenticate   # R (401)
  Given a SCIM token was revoked (via rotate or explicit revoke)
  When any /scim/v2/* request is made bearing the revoked token's secret
  Then a 401 response
  And the request never reaches any user-mutating code path
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

Status: FROZEN @ v1 — approved by Tin Dang
### Part A — SCIM token management (`/admin/scim/tokens`, RFC 9457 problem+json, session-JWT auth via `require_permission(Permission.MEMBERS_MANAGE)`)

```
POST   /admin/scim/tokens        body: { name: str }
  201 -> { id: uuid, name: str, token: "scim-<hex>.<secret>", created_at: datetime }
         # token is returned ONLY in this response body — never again, never in GET/list
  403 -> { error: "ERR_AUTH_FORBIDDEN" }                    # caller lacks MEMBERS_MANAGE

GET    /admin/scim/tokens
  200 -> { tokens: [ { id, name, created_at, revoked_at: datetime|null } ] }
         # no token_hash, no secret, ever

POST   /admin/scim/tokens/{id}/rotate
  200 -> { id: uuid, name: str, token: "scim-<hex>.<secret>", created_at: datetime }
         # atomically: old {id}.revoked_at = now(); a NEW row is created and returned
  404 -> { error: "ERR_SCIM_TOKEN_NOT_FOUND" }               # unknown id, already revoked, or cross-tenant

DELETE /admin/scim/tokens/{id}
  204 -> (empty)                                             # sets revoked_at = now()
  404 -> { error: "ERR_SCIM_TOKEN_NOT_FOUND" }
```

### Part B — SCIM 2.0 resource surface (`/scim/v2/*`, RFC 7644 SCIM error envelope, SCIM-bearer auth — see §0 Envoy note)

```
GET    /scim/v2/ServiceProviderConfig
  200 -> { patch: {supported: true}, filter: {supported: true, maxResults: 200},
           bulk: {supported: false}, sort: {supported: false},
           changePassword: {supported: false}, etag: {supported: false},
           group: {supported: false}, authenticationSchemes: [...] }

GET    /scim/v2/ResourceTypes
  200 -> [ { id: "User", schema: "urn:ietf:params:scim:schemas:core:2.0:User", ... } ]
         # Groups intentionally omitted from the discoverable resource-type list

GET    /scim/v2/Schemas
  200 -> [ <core User schema> ]                              # static, per-deployment

GET    /scim/v2/Users?filter=userName+eq+"<value>"&startIndex=&count=
  200 -> { schemas: ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
           totalResults: int, startIndex: int, itemsPerPage: int,
           Resources: [ <SCIM User> ] }
         # ONLY "userName eq" is supported; any other filter expression -> 400 invalidFilter

GET    /scim/v2/Users/{id}
  200 -> <SCIM User>
  404 -> <SCIM Error> { status: "404", detail: "Resource not found" }   # wrong-tenant id too

POST   /scim/v2/Users        body: <SCIM User> (userName required)
  201 -> <SCIM User>   # role is never read from the body; auth_method="scim" internally
  409 -> <SCIM Error> { status: "409", scimType: "uniqueness", detail: "userName already in use" }
  400 -> <SCIM Error> { status: "400", scimType: "invalidValue", detail: "..." }

PUT    /scim/v2/Users/{id}   body: <full SCIM User>
PATCH  /scim/v2/Users/{id}   body: { schemas: [...PatchOp], Operations: [ {op, path, value} ] }
  200 -> <SCIM User>          # active:false -> deactivate; active:true -> reactivate; idempotent both ways
  404 -> <SCIM Error>          # wrong-tenant or unknown id
  400 -> <SCIM Error> { scimType: "mutability" }   # op targets id/meta or any privilege-shaped attribute path

DELETE /scim/v2/Users/{id}
  204 -> (empty)               # ALIAS for PATCH active:false — never a hard SQL DELETE
  404 -> <SCIM Error>

GET    /scim/v2/Groups
  200 -> { schemas: [...ListResponse], totalResults: 0, Resources: [] }   # always empty (v1)

POST|PUT|PATCH|DELETE /scim/v2/Groups[/*]
  501 -> <SCIM Error> { status: "501", detail: "Group resource management is not supported" }

# Every /scim/v2/* route, any method:
  401 -> <SCIM Error> { status: "401", detail: "invalid_token" }        # missing/invalid/revoked bearer
  429 -> <SCIM Error> { status: "429", detail: "rate_limited" }  + Retry-After header
```

<SCIM Error> envelope (RFC 7644 §3.12, used ONLY under `/scim/v2/*` — a deliberate, documented exception to the project-wide RFC 9457 convention, same class of accepted inconsistency as S4's Envoy-native-413 body):
```
{ "schemas": ["urn:ietf:params:scim:api:messages:2.0:Error"],
  "status": "<http status as string>",
  "scimType": "<uniqueness|invalidValue|mutability|...>",   # omitted when not applicable
  "detail": "<human-readable, never leaks cross-tenant existence beyond the uniqueness code itself>" }
```

### Schema (additive-only, one Alembic revision, parents current head `511ad8a7b65e`)

- `ALTER TABLE users ADD COLUMN deactivated_at TIMESTAMPTZ NULL` — mirrors `api_keys.revoked_at`; `NULL` = active (default, all existing rows unaffected).
- `CREATE TABLE scim_tokens ( id UUID PK, tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT, name TEXT NOT NULL CHECK(length(name) BETWEEN 1 AND 200), token_hash TEXT NOT NULL, created_by_user_id UUID NULL REFERENCES users(id) ON DELETE SET NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), revoked_at TIMESTAMPTZ NULL )` — shape mirrors `api_keys` minus the governance/budget columns that don't apply to a provisioning credential.
- `ALTER TABLE audit_events ADD COLUMN actor_scim_token_id UUID NULL REFERENCES scim_tokens(id) ON DELETE SET NULL` — additive, mirrors the existing `actor_key_id` precedent exactly; `AuditEvent.__post_init__`'s actor-required guard is widened to accept `actor_scim_token_id` as a third valid actor alongside `actor_user_id`/`actor_key_id` (every existing call site is unaffected — the field defaults to `None`).
- Access pattern: every `scim_tokens`/`users`(via SCIM)/`team_members`(delete) query is `WHERE tenant_id = :token_tenant_id`, mirroring the codebase-wide tenant-scoping invariant; the deactivation write (`users.deactivated_at` + `team_members` delete) commits in ONE transaction (mirrors the project's "multi-row lifecycle operations" settled convention).
- `infra/envoy/envoy.yaml` + `infra/envoy/envoy-prod.yaml` + `charts/ai-proxy/templates/envoy-configmap.yaml`: additive explicit route block for `prefix: "/scim/"` (ext_authz disabled, no jwt_authn requirement) — behavior-preserving relative to today's catch-all (see §0), added for drift-insurance only.

### Named error codes (every §1 Reject covered)

| Reject | Surface | Code / shape |
|---|---|---|
| missing/invalid/revoked SCIM token | `/scim/v2/*` | 401 SCIM Error `detail:"invalid_token"` |
| cross-tenant `{id}` | `/scim/v2/Users/{id}` | 404 SCIM Error |
| duplicate email | `POST /scim/v2/Users` | 409 SCIM Error `scimType:"uniqueness"` |
| role/privilege attribute in payload | `POST\|PATCH /scim/v2/Users` | silently ignored (201/200, no error — Must M3) |
| malformed payload | `POST\|PATCH /scim/v2/Users` | 400 SCIM Error `scimType:"invalidValue"` |
| PATCH immutable path | `PATCH /scim/v2/Users/{id}` | 400 SCIM Error `scimType:"mutability"` |
| rate limit exceeded | `/scim/v2/*` | 429 SCIM Error + `Retry-After` |
| Groups write | `/scim/v2/Groups[/*]` | 501 SCIM Error |
| unknown/revoked token id | `/admin/scim/tokens/{id}/rotate\|DELETE` | 404 `ERR_SCIM_TOKEN_NOT_FOUND` (RFC 9457) |
| caller lacks `MEMBERS_MANAGE` | `/admin/scim/tokens*` | 403 `ERR_AUTH_FORBIDDEN` (existing, reused) |

Glossary deltas:
- `SCIM token`: a per-tenant bearer credential (`scim-<id>.<secret>`, SHA-256-hashed at rest) authorizing unattended write access to that tenant's user lifecycle via `/scim/v2/*` — a third bearer-credential surface distinct from the tenant JWT (`/admin`) and API-key ext_authz (`/v1`), mirroring the `ops-auth` precedent of "its own issuer/signing key, its own edge treatment."
- `SCIM-provisioned user`: a `UserRow` with `auth_method="scim"` — created by an IdP via `/scim/v2/Users`, always `role=MEMBER`, never authenticatable by password (sentinel hash), lifecycle-managed (create/update/deactivate/reactivate) entirely by the owning tenant's SCIM token.
- `deactivated_at`: a nullable timestamp on `users` (mirrors `api_keys.revoked_at`) — `NULL` = active; once set, blocks all FUTURE password/OIDC login and removes the user from every team, but does NOT retroactively revoke an already-issued session JWT (documented residual, bounded by `jwt_ttl_seconds`).

Status: DRAFT — awaiting human freeze
Reported: no — freeze report renders when Tin reviews this draft

Least-sure flag surfaced at freeze: [contract] deactivated user's session JWT stays valid up to jwt_ttl (24h default) — accepted at freeze as a DOCUMENTED residual risk (no revocation store this task); Groups→teams DEFERRED (empty-read + 501-write) pending real-IdP behavior. Decided at freeze (Tin, 2026-07-10 batch): all 5 agent recommendations accepted.

## Design self-score

- Completeness: 0.92 — every §1 Must has a §2 scenario and a §3 response shape; every Reject has a named code + scenario; the milestone's five cross-cutting concerns (tenant isolation, rate limiting, audit, deactivation semantics, edge routing) are all pinned with concrete anchors, not deferred. Held back from higher: the dashboard SCIM-token-management UI is explicitly named out-of-scope rather than delivered, per the ⚠ freeze question.
- Clarity: 0.93 — every contract line cites a real anchor (`path:symbol`, `l.NNN-NNN` as-of Ground SHA `2071046`); naming pulled from RFC 7644 canon (SCIM User/Group/PatchOp) with no invented vocabulary; the SCIM-error-vs-RFC9457 split is explicitly justified rather than left implicit.
- Practicality: 0.93 — reuses `Sha256SecretHasher`, `RotateKeyUseCase`'s atomic-rotation shape, `InvitePublicRateLimiter`'s limiter shape, `get_or_provision_oidc_user`'s role-coercion precedent, and the EXISTING `MEMBERS_MANAGE` permission verbatim; zero new dependencies; the Envoy change is additive/behavior-preserving, not a rework of a frozen edge component.
- Optimization: 0.90 — avoids over-building (Groups deferred per the milestone's own escape hatch rather than a speculative full resource type); avoids under-building (does not skip audit/rate-limit/isolation to hit a smaller diff). The one place this could be leaner — building nothing for session-JWT revocation — is a real architecture boundary, not a shortcut, and is named as such rather than silently assumed away.
- Edge cases: 0.90 — idempotent PATCH, reactivation, cross-tenant 404, global-email-uniqueness conflict, revoked-token-401, Groups-probe-vs-Groups-write split, Redis-outage fail-open, and the stateless-JWT residual window are all scenario-covered. Not covered (deliberately, named as a build expectation for VERIFY, not silently absent): live behavior against a real Okta/Entra tenant — SCIM interoperability quirks are empirically discovered, not guessable from the RFC text alone.
- Self-evaluation: 0.91 — the three ⚠/open assumptions (Groups deferral, discovery-endpoint auth, sentinel-hash naming, UI-scope boundary) are ranked lowest-confidence-first with named costs if wrong, per the co-specify contract; nothing was resolved by silent guess where the milestone or the grounded code left it genuinely open.

All six ≥ 0.90 — no refinement pass required before reporting.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag (§1 ⚠ feeds it; a flag may point at any part — run.md). Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen contract = change request back to SPECIFY. EXIT: frozen · every §1 rejection has a contracted response · names match GLOSSARY (new terms = Glossary delta) · flag surfaced. -->

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

Scope (may touch): `./src/` `apps/gateway/src/gateway/scim/` `apps/gateway/src/gateway/tenants/domain/entities.py` `apps/gateway/src/gateway/tenants/infrastructure/orm.py` `apps/gateway/src/gateway/tenants/application/use_cases.py` `apps/gateway/src/gateway/auth/application/use_cases.py` `apps/gateway/src/gateway/audit/domain/audit_event.py` `apps/gateway/src/gateway/audit/infrastructure/audit_events_orm.py` `apps/gateway/src/gateway/teams/infrastructure/repository.py` `apps/gateway/migrations/versions/` `apps/gateway/src/gateway/main.py` `apps/gateway/src/gateway/core/config.py` `infra/envoy/envoy.yaml` `infra/envoy/envoy-prod.yaml` `charts/ai-proxy/templates/envoy-configmap.yaml` `./tests/`

Strategy (ordered batches):
  1. Schema first: one additive Alembic migration (`users.deactivated_at`, `scim_tokens` table, `audit_events.actor_scim_token_id`) parented on head `511ad8a7b65e`; widen `AuditEvent.__post_init__`.
  2. `gateway/scim/` module, clean-architecture shaped (`domain/` entities+ports+errors ← `application/` use cases ← `infrastructure/` SQLAlchemy repo + `Sha256SecretHasher` reuse ← `api/`): token CRUD use cases first (smallest, no SCIM-wire-format concerns), proven against `RotateKeyUseCase`'s atomic-rotation shape.
  3. SCIM wire-translation layer (`api/scim_schemas.py`: SCIM User ⇄ `UserRow`, PATCH-op parser, SCIM Error envelope) — kept OUT of the domain/application layers per the `ChatTranslator` precedent (translation lives at the boundary, business rules don't know about SCIM's wire shape).
  4. `/scim/v2/Users` CRUD use cases wired to the translator; deactivation reuses the teams repository's delete-by-`team_id`+`user_id` pattern in the SAME transaction as `deactivated_at`.
  5. `LoginUseCase`/`OidcLoginUseCase` deactivation-check additions (byte-identical failure shape to existing `InvalidCredentialsError`/OIDC denial).
  6. Discovery endpoints (`ServiceProviderConfig`/`ResourceTypes`/`Schemas`) + `Groups` read-only/501 stub — static, last, lowest-risk.
  7. Per-token rate limiter (mirrors `InvitePublicRateLimiter` 1:1) + audit wiring on every mutation use case.
  8. Envoy route-block insertion (additive, behavior-preserving — verified via the existing "diff the two rendered filter+route blocks" check).

Persona (required): generic — no seeded `.add/personas/` file carries `flow: design`; this draft applied the dispatch's identity-platform-security-engineer stance directly (SCIM 2.0 / RFC 7642-7644 domain knowledge, unattended-write-path hardening). For BUILD, `.add/personas/appsec-engineer.md` (tenant-isolation/RBAC/escalation-ceiling lens) and `.add/personas/backend-architect.md` (clean-architecture layering) are both `flow: build`-fit and should be loaded together — appsec-engineer's stance is primary given `sensitivity: security`.
Spawn isolation (default): worktree — this task edits shared files (`main.py`, `config.py`, `audit_event.py`) also touched by sibling milestone tasks (`saml-sso`, `tenant-retention-zdr`, `compliance-export-api`); isolate to avoid cross-task collision, net-diff merge back per the `worktree-isolated-spawn-default` convention.
Known-problem fixes: bare `require_permission`/`require_superadmin` without `Depends()` silently no-ops the gate (the exact S1 bug class caught last milestone) → every new dependency wiring MUST use `Annotated[Identity, fastapi.Depends(...)]`, verified by a dedicated test per new gated route, not assumed from the pattern alone; a raw SQLAlchemy UPDATE (deactivation) does not fire ORM `onupdate` hooks → explicit `deactivated_at=func.now()`/`updated_at=func.now()` in every VALUES clause (mirrors the `rename_title`/v40 fold).
Strategy actually used: <fill at VERIFY>
Safety rule (feature-specific): the deactivation write (`users.deactivated_at` SET) and the `team_members` delete-by-`user_id` commit in ONE atomic transaction — a partial failure must never leave a user deactivated-but-still-team-attributed or vice versa.
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
