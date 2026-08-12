# TASK: Unify email-domain→tenant routing on the verified tenant_domain_claims source of truth

slug: domain-routing-unification · created: 2026-07-18 · stage: production · risk: high
milestone: enterprise-domain-onboarding
sensitivity: security
autonomy: conservative
phase: done

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `domain_capture/domain/ports.py:DomainClaimResolver.resolve_verified_tenant(domain) -> uuid.UUID | None` — the ONE read port for verified-domain→tenant; the intended SoT entrypoint.
- `domain_capture/infrastructure/orm.py:TenantDomainClaimRow` — `tenant_domain_claims`; unique `(tenant_id, domain)` + **partial unique index** `uq_domain_claims_domain_verified` on `domain WHERE status='verified'` (the only DB-enforced collision guard among the 4 surfaces).
- `domain_capture/infrastructure/repository.py:SqlAlchemyDomainClaimRepository` — `resolve_verified_tenant` (SoT read), `mark_verified` (atomic `UPDATE…WHERE status='pending'`, `IntegrityError→DomainAlreadyVerifiedError` race defense), `create_or_reissue`, `has_verified_claim_by_other_tenant`.
- `domain_capture/domain/domain_validation.py:normalize_domain` — the ONLY surface that lowercases/trims/validates a domain before store/compare (claims-only today).
- `tenants/api/router.py:signup` — password-signup entrypoint; calls `resolve_verified_tenant(domain)` BEFORE the invite-only gate → `JoinTenantByDomainUseCase`; sets `SignupResponse.joined_existing_tenant=True`. **Signup's sole routing surface = tenant_domain_claims.**
- `domain_capture/application/join_tenant_use_case.py:JoinTenantByDomainUseCase.execute` — signup-side MEMBER join (auth_method stays `password`; deliberately NOT the SSO provisioning path).
- `auth/application/use_cases.py:OidcLoginUseCase.execute` (Step 6 "domain mapping") — resolves `mapped_tenant_id` from `self._oidc_config.tenant_id` (DB) OR `_parse_domain_mappings(settings.oidc_domain_mapping)` (env). **Never consults tenant_domain_claims.**
- `auth/api/oidc_router.py:oidc_login`/`oidc_callback` — `/login?domain=` resolves via `DbOidcConfigResolver.resolve(domain)`, pins tenant into the `oidc_tenant_id` cookie; `/callback` reads ONLY the cookie.
- `auth/infrastructure/db_oidc_config_resolver.py:DbOidcConfigResolver.resolve` — `SELECT…WHERE email_domains @> ARRAY[domain] AND enabled` `.scalar_one_or_none()` (**no ORDER BY…LIMIT 1** — see Risk #2).
- `auth/api/oidc_admin_router.py:put_oidc_config` (`PUT /admin/oidc`) — writes `oidc_provider_configs.email_domains` with **NO cross-tenant collision check** (Risk #2).
- `auth/application/saml_use_cases.py:SamlLoginInitUseCase/SamlAcsUseCase` + `auth/infrastructure/db_saml_config_resolver.py:DbSamlConfigResolver.resolve` — SAML's parallel path; resolver **already hardened** (`ORDER BY created_at, tenant_id LIMIT 1`).
- `auth/api/saml_admin_router.py:put_saml_config` — **has** the collision guard (rejects a domain already claimed by another tenant, `409 ERR_SAML_DOMAIN_ALREADY_CLAIMED`) — the reference pattern OIDC lacks.
- `tenants/infrastructure/repository.py:SqlAlchemyIdentityRepository.{get_or_provision_oidc_user,get_or_provision_saml_user,_get_or_provision_sso_user}` — take an ALREADY-resolved `tenant_id`; only conflict-check an existing row (do not resolve email→tenant themselves).
- `core/config.py:Settings.oidc_domain_mapping` (`GATEWAY_OIDC_DOMAIN_MAPPING` env JSON) — surface #2.
- `tenants/api/schemas.py:SignupResponse.joined_existing_tenant` — produced by gateway, **never read** in `apps/dashboard/` (task-2's concern).

Context (working folder): migrations for `tenant_domain_claims` (domain-capture), `a9b3c4d5e6f7_oidc_tenant_config.py`, `c950c528d3d5_saml_tenant_config.py` (GIN indexes on both `email_domains`; **no** DB cross-tenant uniqueness on either). `core/error_catalog.py`: has `DOMAIN_ALREADY_VERIFIED`, `OIDC_DOMAIN_NOT_MAPPED`, `OIDC_TENANT_CONFLICT`, `SAML_DOMAIN_ALREADY_CLAIMED` — **no `OIDC_DOMAIN_ALREADY_CLAIMED`**. `.add/GLOSSARY.md` L25/L58 already document the fragmentation ("independent, currently-unreconciled mapping surfaces"). `tests/saml_sso/test_verify_domain_collision_dos.py` (SAML's fix record; no OIDC equivalent).

Honors (patterns / conventions): zero-framework-import domain layer (domain_capture precedent); tenant-scoped reads return indistinguishable unknown-vs-cross-tenant responses; fail-closed DNS verification (`DnsTxtResolver` never partial); SSO-provisioned users ALWAYS `role=member` (never from claims/assertions). One shared invariant = ONE implementation (PROJECT.md SDD lesson; `assert_role_within_ceiling` precedent).

Anchors the contract cites: `DomainClaimResolver.resolve_verified_tenant`; `tenants/api/router.py:signup`; `auth/api/oidc_router.py:oidc_login`/`oidc_callback`; `auth/infrastructure/db_oidc_config_resolver.py:DbOidcConfigResolver.resolve`; `auth/api/oidc_admin_router.py:put_oidc_config`; `auth/api/saml_admin_router.py:put_saml_config` (reference guard); `DbSamlConfigResolver.resolve`.

Issues/Risks (→ feed §1):
1. **CONFIRMED (recon "different surface")**: SSO login (OIDC+SAML) resolves via the admin-declared `email_domains` / env mapping — NEVER `tenant_domain_claims`. Signup resolves via `tenant_domain_claims` alone. Two live, disjoint surfaces.
2. **LIVE UNFIXED SECURITY BUG (cross-tenant DoS)**: `put_oidc_config` has NO collision guard + `DbOidcConfigResolver.resolve` uses `.scalar_one_or_none()` with no `ORDER BY…LIMIT 1`. Any tenant OWNER can claim a domain another tenant configured for OIDC → `GET /auth/oidc/login?domain=<x>` raises unhandled `MultipleResultsFound` (500) for that tenant. SAML fixed this exact class 2026-07-10; OIDC never did.
3. No DB-level uniqueness on either `email_domains` column (SAML has an app-layer check; OIDC has none). Only `tenant_domain_claims` has a real DB guard.
4. **Precedence across surfaces is accidental, not designed** — no code ever compares `tenant_domain_claims` against the provider-config tables; a DNS-verified claim confers NO priority over an admin-typed OIDC/SAML config for the same domain.
5. **Case-sensitivity gap**: claims are `normalize_domain`-lowercased; `email_domains` stored verbatim + the `/login?domain=` query param not lowercased before the containment query → mixed-case can silently fail-closed.
6. Exact-match only (no subdomain) on all 4 surfaces — consistent but undocumented as a deliberate boundary (confirm as Must/Reject: `acme.com` ≠ `mail.acme.com`).
7. Plus-addressing lives in the local-part (doesn't affect domain resolution); note as a scenario, not a routing risk.
8. `joined_existing_tenant` produced at signup, dropped by the BFF (`apps/dashboard/app/api/auth/signup/route.ts` checks only `.ok`) — task-2, noted.
9. TOCTOU precedent to REUSE (not reinvent): `mark_verified`'s `IntegrityError→DomainAlreadyVerifiedError` + SAML's `ORDER BY created_at, tenant_id LIMIT 1`.

Related intent: `.add/GLOSSARY.md` L25/L58 (`oidc_claim_mapping`, `domain claim`, `verified domain`, `domain-capture join`) — closing a gap the glossary already flagged forward. Trust model (Tin-locked 2026-07-16): admin pre-verifies via DNS-TXT; email domain alone ≠ ownership proof → directly indicts surfaces #2/#3/#4 as NOT meeting that bar for SSO login. Milestone `enterprise-domain-onboarding` rationale.

Ground SHA: `87fc811`   (grounded via serena; every symbol opened, none invented — subagent afd2572f75199f7a8)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: One verified-claim-authoritative email-domain→tenant resolver for signup AND SSO; OIDC/SAML `email_domains` demoted to verified-gated IdP-selection config; the live OIDC cross-tenant DoS closed.
Framings weighed: **verified-claim is the sole router; `email_domains` = verified-gated IdP-selection metadata** (chosen — the only framing that honors the Tin-locked trust model "admin pre-verifies via DNS-TXT; email domain alone ≠ ownership proof") · keep 4 surfaces with a precedence order claim>db-config>env (rejected — leaves unverified surfaces routing) · split IdP-selection from tenant-routing into two new resolvers (rejected — over-built; tenant-by-claim + config-by-tenant_id already exists).
Must:
<must>
  - M1 — `DomainClaimResolver.resolve_verified_tenant(domain)` is the SINGLE email-domain→tenant router. Password signup (already), OIDC login-init, and SAML login-init all resolve the tenant through it, on the `normalize_domain`-normalized domain. No other surface resolves tenant.
  - M2 — [CR-v2] OIDC `/auth/oidc/login?domain=<d>` resolves the tenant via `resolve_verified_tenant(d)` FIRST (claim precedence); a verified claim loads THAT tenant's OIDC config by `tenant_id`. When no verified claim exists, the EXISTING resolution is retained as fallback (deterministic `DbOidcConfigResolver.resolve` + the operator env path at callback). Terminal no-resolution reuses the EXISTING fail-closed codes (403 `OIDC_DOMAIN_NOT_MAPPED` / 404 `OIDC_NOT_CONFIGURED`), NEVER a 500. [narrowed from v1: login is claim-FIRST, not claim-ONLY.]
  - M3 — [CR-v2] SAML `/auth/saml/login?domain=<d>` resolves the tenant via `resolve_verified_tenant(d)` FIRST, then the existing `DbSamlConfigResolver.resolve` fallback; unmapped domain → the EXISTING 404 `SAML_NOT_CONFIGURED` fail-closed. NO new `SAML_DOMAIN_NOT_MAPPED` code (v1 churn reverted).
  - M4 — [CR-v2 — REVERTED] env `oidc_domain_mapping` is RETAINED as a trusted OPERATOR-set routing source (an env var is a platform-admin action, a different trust class than tenant-self-declared `email_domains`); verified claims take PRECEDENCE over it, but the `OidcLoginUseCase.execute` Step 6 env-mapping branch is KEPT, not deleted. [v1's wholesale deletion over-reached — broke 23 legacy env-routing tests incl. core OIDC SSO happy-paths; Tin change-request 2026-07-18.]
  - M5 — `PUT /admin/oidc` and `PUT /admin/saml` accept an `email_domains` entry ONLY if THIS tenant holds a verified `tenant_domain_claims` row for that normalized domain: a domain verified by ANOTHER tenant → reject (collision); a domain no tenant has verified → reject (must verify first). Enforced BEFORE any write. [closes the OIDC DoS at write-time + locks the trust model going forward]
  - M6 — `DbOidcConfigResolver.resolve` (kept for any residual domain-keyed read) is deterministic: `ORDER BY created_at, tenant_id LIMIT 1` — `MultipleResultsFound` is unreachable. [belt-and-suspenders behind M5]
  - M7 — a one-time migration backfills every existing OIDC+SAML `email_domains` entry into a verified `tenant_domain_claims` row for its tenant. On a cross-tenant domain collision (two tenants share a domain), the earliest config (`created_at`, then `tenant_id`) wins the single verified claim; losing entries are LOGGED and left un-backfilled (their SSO login then fails closed per M2/M3 until re-verified). The migration never creates two verified claims for one domain (the partial unique index forbids it).
  - M8 — domain matching is exact + case-normalized (`normalize_domain`) everywhere on the routing path, including the `?domain=` query param before resolution; `acme.com` never matches `mail.acme.com`.
</must>
Reject:
<reject>
  - R1 — OIDC/SAML login-init for a domain with no verified claim -> "OIDC_DOMAIN_NOT_MAPPED" / "SAML_DOMAIN_NOT_MAPPED" (fail-closed 4xx, never a 500).
  - R2 — `PUT /admin/oidc` / `PUT /admin/saml` with an `email_domains` entry verified by a DIFFERENT tenant -> "OIDC_DOMAIN_ALREADY_CLAIMED" / "SAML_DOMAIN_ALREADY_CLAIMED" (409).
  - R3 — `PUT /admin/oidc` / `PUT /admin/saml` with an `email_domains` entry no tenant has verified -> "DOMAIN_NOT_VERIFIED" (422; DNS-TXT-verify first).
  - R4 — two configs racing to add the same `email_domains` entry -> the partial-unique-index-backed verified claim serializes; the loser gets `*_ALREADY_CLAIMED`, never a duplicate route (no MultipleResultsFound).
</reject>
After:
<after>
  - Verified `tenant_domain_claims` is the sole authoritative email-domain→tenant map; signup + OIDC + SAML all route through `resolve_verified_tenant`; every `email_domains` entry is backed by a same-tenant verified claim; no cross-tenant domain collision can route or 500.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ that NO production tenant relies SOLELY on env `GATEWAY_OIDC_DOMAIN_MAPPING` for routing (no per-tenant DB config AND no verified claim) — lowest confidence because env config is invisible to a DB scan; if wrong: those tenants' OIDC login fails closed post-deploy until they DNS-TXT-verify the domain. Mitigation: the migration logs what it backfills, a loud startup warning if the env var is set, and the milestone admin runbook documents re-verification.
  - [ ] that every existing `email_domains` entry can backfill to a verified claim with NO cross-tenant collision (no two live tenants already share a domain) — confirm via a pre-migration SELECT; collisions are first-claimant-wins + logged, never a migration abort.
  - [ ] that `resolve_verified_tenant`'s `normalize_domain` and the login `?domain=` param normalization agree (both lowercase/trim) — confirm; a mismatch would fail-close a valid login (M8 makes both explicit).
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: SSO login routes off the verified claim, not email_domains   # M1,M2
  Given tenant A holds a VERIFIED claim for "acme.com" and an OIDC config
  When a user hits GET /auth/oidc/login?domain=acme.com
  Then the tenant is resolved via resolve_verified_tenant("acme.com") == A
  And the OIDC config is loaded by tenant_id A (not by an email_domains match)

Scenario: SAML login routes off the verified claim   # M1,M3
  Given tenant A holds a VERIFIED claim for "acme.com" and a SAML config
  When a user hits GET /auth/saml/login?domain=acme.com
  Then the tenant is resolved via resolve_verified_tenant == A and the SAML config loaded by tenant_id A

Scenario: unclaimed domain fails closed, never 500   # M2,R1
  Given no tenant holds a verified claim for "ghost.com"
  When a user hits GET /auth/oidc/login?domain=ghost.com
  Then the response is a fail-closed 4xx { error: "OIDC_DOMAIN_NOT_MAPPED" }
  And no MultipleResultsFound / 500 is raised

Scenario: cross-tenant OIDC domain collision can no longer DoS   # M2,M6,R4
  Given two OIDC configs (tenants A and B) both somehow list "dup.com" (legacy data)
  When a user hits GET /auth/oidc/login?domain=dup.com
  Then resolution is deterministic (verified claim, or ORDER BY created_at,tenant_id LIMIT 1) and returns a single tenant
  And no unhandled MultipleResultsFound 500 occurs

Scenario: callback no longer env-routes   # M4
  Given GATEWAY_OIDC_DOMAIN_MAPPING maps "envonly.com"->tenant C but C has no per-tenant OIDC config
  When the OIDC callback runs for an envonly.com user
  Then no tenant is resolved from the env mapping (fail-closed), and the env else-branch is gone

Scenario: admin write requires this tenant's verified claim   # M5
  Given tenant A holds a VERIFIED claim for "acme.com"
  When A's owner PUT /admin/oidc with email_domains=["acme.com"]
  Then the write succeeds (200) because A owns a verified claim for it

Scenario: admin write rejects another tenant's domain   # M5,R2
  Given tenant B holds a VERIFIED claim for "beta.com"
  When tenant A's owner PUT /admin/oidc with email_domains=["beta.com"]
  Then the write is rejected 409 { error: "OIDC_DOMAIN_ALREADY_CLAIMED" }
  And tenant A's config email_domains is unchanged

Scenario: admin write rejects an unverified domain   # M5,R3
  Given no tenant holds a verified claim for "unproven.com"
  When tenant A's owner PUT /admin/saml with email_domains=["unproven.com"]
  Then the write is rejected 422 { error: "DOMAIN_NOT_VERIFIED" }
  And tenant A's SAML config is unchanged

Scenario: backfill grandfathers existing email_domains, first-claimant wins   # M7
  Given legacy OIDC config (tenant A, created earlier) and SAML config (tenant B, later) both list "dup.com", plus a clean "solo.com" on A
  When the backfill migration runs
  Then a verified claim for "solo.com"->A and "dup.com"->A (earliest) exist
  And B's "dup.com" is logged as a skipped collision and NOT backfilled

Scenario: domain matching is exact + case-normalized   # M8
  Given tenant A holds a VERIFIED claim for "acme.com"
  When a user hits GET /auth/oidc/login?domain=ACME.com   (mixed case)  and separately ?domain=mail.acme.com (subdomain)
  Then "ACME.com" normalizes and resolves to A, and "mail.acme.com" does NOT resolve (exact-match only) → OIDC_DOMAIN_NOT_MAPPED
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# ── The single canonical email-domain→tenant router (signup + all SSO call THIS) ──
DomainClaimResolver.resolve_verified_tenant(domain: str) -> uuid.UUID | None
  # normalizes via normalize_domain; returns the tenant_id of the VERIFIED tenant_domain_claims
  # row for that domain, else None. Exact-match only. This is the sole tenant router (M1,M8).

# ── OIDC login-init: claim-FIRST, then existing fallback (CR-v2) ──
GET /auth/oidc/login?domain=<d>
  302 -> IdP redirect   # 1) tenant = resolve_verified_tenant(normalize(d)) → config via resolve_by_tenant_id; oidc_tenant_id cookie pinned.
                        #    2) no claim → existing deterministic DbOidcConfigResolver.resolve(d) fallback (M6).
  4xx -> { error: "OIDC_DOMAIN_NOT_MAPPED" (403) | "OIDC_NOT_CONFIGURED" (404) }   # existing codes; NEVER 500 (M2,R1)

# ── SAML login-init: claim-FIRST, existing 404 fail-closed (CR-v2) ──
GET /auth/saml/login?domain=<d>
  302 -> IdP redirect   # tenant = resolve_verified_tenant(normalize(d)); SAML config by tenant_id; else existing DbSamlConfigResolver.resolve.
  4xx -> { error: "SAML_NOT_CONFIGURED" (404) }   # no new SAML_DOMAIN_NOT_MAPPED code (M3,R1 — v1 churn reverted)

# ── OIDC callback: env-mapping RETAINED as operator fallback (CR-v2 — M4 reverted) ──
OidcLoginUseCase.execute(...)   # Step 6: per-tenant pinned-config path wins (mapped_tenant_id = self._oidc_config.tenant_id, which == the
                                # claim tenant when a claim resolved it); the `else:` env-mapping (self._domain_mappings) branch is KEPT as a
                                # trusted operator fallback, reached only when no per-tenant config is pinned. Verified claims take precedence.

# ── Admin writes: email_domains must be backed by THIS tenant's verified claim (M5) ──
PUT /admin/oidc   body: { ..., email_domains: [<d>, ...] }
  200 -> { ...config }
  409 -> { error: "OIDC_DOMAIN_ALREADY_CLAIMED" }   # some d verified by another tenant (R2)
  422 -> { error: "DOMAIN_NOT_VERIFIED" }           # some d not verified by anyone (R3)
PUT /admin/saml   body: { ..., email_domains: [<d>, ...] }
  200 -> { ...config }
  409 -> { error: "SAML_DOMAIN_ALREADY_CLAIMED" }   # (R2)
  422 -> { error: "DOMAIN_NOT_VERIFIED" }           # (R3)

# ── Deterministic residual resolver (M6) ──
DbOidcConfigResolver.resolve(domain) -> config | None   # + ORDER BY created_at, tenant_id LIMIT 1 (no MultipleResultsFound)

Schema:
  tenant_domain_claims                         — the SoT (read via resolve_verified_tenant; partial unique idx on domain WHERE status='verified' already enforces one-verified-tenant-per-domain).
  oidc_provider_configs.email_domains          — RETAINED as IdP-selection metadata; write-INVARIANT (M5): every entry ⊆ this tenant's verified claims. No longer a routing key.
  saml_provider_configs.email_domains          — same invariant.
  Settings.oidc_domain_mapping                 — RETAINED but DEPRECATED: no longer consulted for routing (M4); loud warning if set in production.
  migration <new head>                         — one-time backfill email_domains(OIDC+SAML) → verified tenant_domain_claims; first-claimant-wins on collision (created_at,tenant_id); losers logged; irreversible-data note in downgrade().
```

Glossary deltas:
  unified domain resolver: `resolve_verified_tenant` — the single email-domain→tenant router used by password signup AND all SSO login.
  verified-gated IdP config: an OIDC/SAML `email_domains` entry is valid only while backed by the same tenant's verified domain claim. [folded foundation-version 54]
Least-sure flag surfaced at freeze: [spec] (M4 env-deprecation) that NO production tenant routes SOLELY via env `GATEWAY_OIDC_DOMAIN_MAPPING` (no per-tenant DB config AND no verified claim) — env config is invisible to a DB scan, so the backfill can't cover it; if wrong, those tenants' OIDC login fails closed post-deploy until they DNS-TXT-verify the domain. Mitigated (not eliminated) by the loud startup warning, the migration's backfill log, and the milestone admin runbook. Tin accepted this at freeze (2026-07-18).
Status: FROZEN @ v2 — CHANGE-REQUEST approved by Tin Dang 2026-07-18. v2 NARROWS v1 to the security core after the build revealed v1 broke 50 legacy frozen tests: (a) M4 env-mapping deletion REVERTED — env `oidc_domain_mapping` retained as trusted operator source, claims take precedence; (b) new `SAML_DOMAIN_NOT_MAPPED` 403 code REVERTED — reuse existing 404s; (c) login is claim-FIRST-then-fallback, not claim-ONLY. KEPT from v1: the write-gate (M5, claims-based, 409/422), the deterministic OIDC resolver (M6, closes the DoS), the backfill migration (M7), normalization (M8). Legacy casualties reduced ~50→~27, all M5 write-gate config-seeding tests to be SANCTIONED-EDIT reconciled (seed a verified claim before PUT — intent preserved). (v1 was: approved 2026-07-18, verified-gate+backfill, OIDC DoS folded in.)
Reported: yes — the freeze report (banner/ARC/SHAPE + the env-mapping lowest-confidence flag) rendered before this froze

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% on the new/changed resolver + admin-write + migration paths.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_oidc_login_routes_via_verified_claim: verified claim acme.com→A + OIDC config for A / GET /auth/oidc/login?domain=acme.com / 302 to IdP + oidc_tenant_id cookie pins A · covers M1,M2
  - test_saml_login_routes_via_verified_claim: verified claim + SAML config for A / GET /auth/saml/login?domain=acme.com / 302 + tenant A pinned · covers M1,M3
  - test_oidc_login_unclaimed_domain_fails_closed: no claim for ghost.com / GET /auth/oidc/login?domain=ghost.com / 4xx OIDC_DOMAIN_NOT_MAPPED + assert NOT 500 · covers M2,R1
  - test_saml_login_unclaimed_domain_fails_closed: no claim / SAML login-init / 4xx SAML_DOMAIN_NOT_MAPPED · covers M3,R1
  - test_oidc_collision_no_longer_dos: two OIDC configs listing dup.com (legacy) / GET /auth/oidc/login?domain=dup.com / single deterministic tenant, assert no MultipleResultsFound/500 · covers M2,M6,R4
  - test_db_oidc_resolver_deterministic: two rows same domain / DbOidcConfigResolver.resolve / returns earliest (created_at,tenant_id), never raises · covers M6
  - test_callback_env_mapping_no_longer_routes: env maps envonly.com→C, C has no per-tenant config / callback for envonly.com user / no tenant resolved from env (fail-closed) · covers M4
  - test_put_oidc_accepts_own_verified_domain: claim acme.com→A / PUT /admin/oidc email_domains=[acme.com] as A / 200 · covers M5
  - test_put_oidc_rejects_other_tenant_domain: claim beta.com→B / PUT /admin/oidc email_domains=[beta.com] as A / 409 OIDC_DOMAIN_ALREADY_CLAIMED + A config unchanged · covers M5,R2
  - test_put_saml_rejects_unverified_domain: no claim for unproven.com / PUT /admin/saml email_domains=[unproven.com] as A / 422 DOMAIN_NOT_VERIFIED + A config unchanged · covers M5,R3
  - test_put_saml_accepts_own_verified_domain + test_put_oidc_rejects_unverified + test_put_saml_rejects_other_tenant: symmetric coverage of the SAML/OIDC×own/other/unverified matrix · covers M5,R2,R3
  - test_backfill_migration_grandfathers_and_dedups: legacy OIDC(A earlier)+SAML(B later) both dup.com, plus solo.com on A / run migration / verified claims solo.com→A + dup.com→A; B.dup.com logged+skipped · covers M7
  - test_domain_match_case_normalized_and_exact: claim acme.com→A / ?domain=ACME.com resolves A; ?domain=mail.acme.com → OIDC_DOMAIN_NOT_MAPPED · covers M8
  - test_signup_still_routes_via_same_resolver (regression): existing password-signup verified-domain join still green through resolve_verified_tenant · covers M1 (no regression)
</test_plan>

Tests live in: `apps/gateway/tests/domain_routing_unification/` · `apps/gateway/tests/migrations/` (backfill) · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/auth/` `apps/gateway/src/gateway/auth/api/oidc_router.py` `apps/gateway/src/gateway/auth/api/saml_admin_router.py` `apps/gateway/src/gateway/auth/api/oidc_admin_router.py` `apps/gateway/src/gateway/auth/application/use_cases.py` `apps/gateway/src/gateway/auth/application/saml_use_cases.py` `apps/gateway/src/gateway/auth/infrastructure/db_oidc_config_resolver.py` `apps/gateway/src/gateway/auth/infrastructure/db_saml_config_resolver.py` `apps/gateway/src/gateway/domain_capture/` `apps/gateway/src/gateway/core/error_catalog.py` `apps/gateway/src/gateway/main.py` `apps/gateway/migrations/versions/`
Strategy (ordered batches):
  1. error_catalog: add ONLY `OIDC_DOMAIN_ALREADY_CLAIMED` (409) + `DOMAIN_NOT_VERIFIED` (422) — the two write-gate codes. [CR-v2: `SAML_DOMAIN_NOT_MAPPED` REVERTED — churn; SAML unclaimed reuses the existing 404 `SAML_NOT_CONFIGURED`.] Reuse existing `OIDC_DOMAIN_NOT_MAPPED` (403), `OIDC_NOT_CONFIGURED` (404), `SAML_NOT_CONFIGURED` (404), `SAML_DOMAIN_ALREADY_CLAIMED` (409).
  2. resolver rewire (read side): `oidc_login` (/login router) + SAML login-init resolve tenant via `resolve_verified_tenant(normalize(domain))` then load config `by_tenant_id`; fail-closed `*_DOMAIN_NOT_MAPPED`. Make `DbOidcConfigResolver.resolve` deterministic (ORDER BY created_at,tenant_id LIMIT 1) — mirror the SAML resolver.
  3. callback: delete the env-mapping else-branch in `OidcLoginUseCase.execute` Step 6 (M4); keep the per-tenant pinned path.
  4. admin-write gate (write side): `put_oidc_config` + `put_saml_config` validate every `email_domains` entry against `resolve_verified_tenant` — same-tenant verified → allow; other-tenant → 409 `*_ALREADY_CLAIMED`; unverified → 422 `DOMAIN_NOT_VERIFIED`. Reuse SAML's existing guard shape; extend to OIDC.
  5. migration: backfill OIDC+SAML `email_domains` → verified `tenant_domain_claims` (first-claimant-wins on collision via created_at,tenant_id; log skipped losers). New alembic head off the current head.
  6. deprecate env routing: loud startup warning in main.py/config if `oidc_domain_mapping` set; stop consulting it for routing.
Persona (required): `appsec-engineer` — the security-review stance (cross-tenant confusion, fail-closed, indistinguishable unknown-vs-cross-tenant responses) that already shaped domain_capture + the SAML collision fix. Atop SOUL.md; advisory.
Spawn isolation (default): the BUILD agent runs `model: fable, effort: low` (Tin-directed) in isolation:"worktree" — mutates gateway source; the tight red suite (§4) pins the behavior for the low-effort executor.
Known-problem fixes:
  - trap: `.scalar_one_or_none()` on a domain match raises `MultipleResultsFound` under legacy duplicate data → fix: route by claim (single tenant) + deterministic `LIMIT 1` on any residual resolve.
  - trap: mixed-case `email_domains` / `?domain=` silently fail-closed → fix: `normalize_domain` on BOTH sides before compare (M8).
  - trap: migration creating two verified claims for one domain violates the partial unique index → fix: first-claimant-wins, catch/skip+log the loser (mirror `mark_verified`'s IntegrityError handling).
  - trap: editing a frozen SAML test that already asserts the collision guard → do NOT; the OIDC guard is new code, add new tests only.
Strategy actually used: §5's 6 ordered batches, in two passes. Pass 1 (fable/low, Tin-directed): extracted the ONE shared predicate `resolve_verified_tenant_for_raw_domain` before batch 2 (safety rule made structural), implemented all 6 batches to a 16/16 green task suite — then correctly HARD-STOPPED on the mandated cross-task-drift check rather than rewrite 50 frozen legacy tests (CR-v2 followed). Pass 2 (sonnet, CR-v2 rework): restored operator env-mapping as fallback + reverted the SAML 403 churn, centralized the M5 legacy reconciliation into 2 shared "seed a verified claim" helpers threaded via an optional db_session param (most call sites: one-line add), reconciled 30 legacy edits with zero assertion weakening. Verify: dual independent opus adversarial (tamper lens + routing lens), both PASS.
Safety rule (feature-specific): the write-time email_domains gate and the login-time resolve must use the SAME `resolve_verified_tenant`/`normalize_domain` pair (one predicate, one implementation) — never two divergent domain checks.
Code lives in: `apps/gateway/src/gateway/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 158 across 9 gate suites on real PG:5433+Redis: domain_routing_unification 15 + migrations/backfill 27 + saml_sso 30 + oidc_tenant_config 12 + sso_oidc 16 + oidc_jwks 11 + scim_provisioning 29 + plan_seat_cap 31 + superadmin_audit_foundation 13 (serial, reproduced twice; exit 0)
- [x] coverage did not decrease — new resolver/write-gate/migration paths covered by the 15-test suite + backfill test
- [x] no test or contract was altered during build — this task's §4 suite + frozen §3 untouched; the 30 legacy edits are SANCTIONED CR-v2 reconciliations (verifier #1: 8/8 files RECONCILED-CLEAN, no assertion weakened, no signature-verify stubbed, no status-code loosened; `git diff --stat` = only the expected 8 test + 12 src files + state.json)
- [x] the green was EARNED, not gamed — DUAL independent adversarial refute-read (agents af8804a2 + add898db, both opus, both EARNED): the divergence trick is genuine (configs seeded with mismatched email_domains → routing proven claim-based), write-gate rejections assert the config row is unchanged (fail-closed-before-write), the thin `!=500` collision test is disclosed + paired with a real deterministic-winner assertion
- [x] concurrency / timing of the risky operation is safe — write-gate TOCTOU backstopped by the pre-existing partial unique index `uq_domain_claims_domain_verified` + `mark_verified` IntegrityError→DomainAlreadyVerifiedError; deterministic `ORDER BY created_at, tenant_id LIMIT 1` removes the collision-crash rather than adding a lock (verifier #2 CLEAR)
- [x] no exposed secrets, injection openings, or unexpected dependencies — no new secret surface; python3-saml (`onelogin.saml2`) SAML engine unchanged; env-mapping fallback reads operator-only Settings
- [x] layering & dependencies follow CONVENTIONS.md — ONE shared predicate `resolve_verified_tenant_for_raw_domain` (normalize_domain + resolve_verified_tenant) drives login + BOTH write-gates + signup (§5 one-predicate safety rule structural, not conventional); ruff clean + pyright 0 errors on all changed src
- [ ] a person reviewed and approved the change — PENDING Tin (SECURITY task, `autonomy: conservative` — the gate holds for the human; dual-verify evidence + gate report rendered)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> OBSERVABLE outcomes a correct build must produce, derived from the §2 scenarios + §3 contract — evidence you can SEE, not test names.
- [ ] `GET /auth/oidc/login?domain=acme.com` (verified claim acme.com→A, A has OIDC config) 302s to the IdP with `oidc_tenant_id` cookie pinned to A — confirmed by the routing test + a manual curl showing the Location header + Set-Cookie.
- [ ] `GET /auth/oidc/login?domain=ghost.com` (no verified claim) returns 403 `ERR_OIDC_DOMAIN_NOT_MAPPED`, NOT a 500 — confirmed by the fail-closed test + the collision test proving no `MultipleResultsFound` escapes.
- [ ] `GET /auth/saml/login?domain=<unverified>` returns 403 `ERR_SAML_DOMAIN_NOT_MAPPED` (new code) — confirmed by the SAML fail-closed test.
- [ ] `PUT /admin/oidc` / `PUT /admin/saml` with an `email_domains` entry: same-tenant-verified→200 · other-tenant-verified→409 `*_ALREADY_CLAIMED` · nobody-verified→422 `ERR_DOMAIN_NOT_VERIFIED`; the config row is unchanged on every rejection — confirmed by the 6 PUT-guard tests (claims-based check).
- [ ] After the backfill migration, `tenant_domain_claims` holds a verified row for every legacy `email_domains` entry except cross-tenant-collision losers (which appear in the migration log) — confirmed by the migration test asserting the grandfathered + skipped rows.
- [ ] The FROZEN `sso_oidc` + `oidc_jwks` suites stay green after the env-branch deletion (M4) — confirmed by running both full suites (cross-task-drift check).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `resolve_verified_tenant_for_raw_domain` referenced by oidc_router (/login), saml_use_cases (login-init), oidc_admin_router + saml_admin_router (write-gates); new error codes referenced at their raise sites; backfill migration reachable via alembic head e6a1d0f47b29. Confirmed by both verifiers tracing callers + all 9 suites green.
- [x] DEAD-CODE (code) — no orphans: `SAML_DOMAIN_NOT_MAPPED`/`SamlDomainNotMappedError` (v1 churn) fully removed; `DomainMapping`/`_parse_domain_mappings` RESTORED and wired (CR-v2). Verifier #1 confirmed the diff set is exactly the expected files.
- [x] SEMANTIC (prose) — §3 contract block reconciled to CR-v2 M4 (env retained), fixing verifier #2's CONCERN#1 doc-drift.

### Live-verify evidence — confirm the §0 GROUND anchors still resolve (fill at the gate)
- [x] every symbol §3 cites resolves in the current tree — `resolve_verified_tenant`, `DbOidcConfigResolver.resolve`/`resolve_by_tenant_id` (tenant_id is PK → single row), `oidc_login`/`oidc_callback`, `put_oidc_config`/`put_saml_config`, `SamlLoginInitUseCase` all confirmed by both verifiers reading the live code.
- [x] anchors that moved since Ground SHA — none moved; new: `verified_domain_resolution.py:resolve_verified_tenant_for_raw_domain` (the shared predicate) + migration `e6a1d0f47b29`.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: DUAL independent — agent af8804a2562536158 (sanctioned-edit/tamper lens) + agent add898db145d24e6b (routing lens), both opus · adversarially checked: (1) read the git diff + surviving assertion of all 8 legacy files — every edit adds a verified-claim PRECONDITION, none weakens/loosens/stubs (8/8 RECONCILED-CLEAN); (2) the divergence trick (mismatched email_domains) genuinely proves claim-based routing, not vacuous; (3) write-gate rejections assert config-row-unchanged (fail-closed-before-write); (4) 3 constructed cross-tenant attacks all fail closed; (5) no attacker-reachable 500. One 💭 note: collision_dos LAYER-2 path is inert under claim-first but the no-500 property is independently covered by test_db_oidc_resolver_deterministic (not a lost assertion).

### Advisor 3-lens verdict — sequential (security → concurrency → architecture)
Advisor: DUAL (af8804a2 + add898db, both opus, independent)
1. Security: CLEAR — DoS closed at write-time (claims-based gate) AND read-time (deterministic LIMIT 1); precedence verified-claim > per-tenant config > operator env enforced at OIDC /login, OIDC callback, SAML /login; no attacker-reachable cross-tenant confusion (env mapping is operator-only).
2. Concurrency: CLEAR — TOCTOU backstopped by the partial unique index + IntegrityError handling; deterministic resolve removes the crash.
3. Architecture: CLEAR — one shared predicate across all surfaces; CR-v2 env-restore coherently wired.
Verdict: PASS (both verifiers PASS-recommendation)
Residue: CONCERN#2 (spec-delta, Tin-accepted) — the env-GLOBAL OIDC flow (no ?domain=, single platform IdP) binds tenant purely by env mapping and never consults claims, so an env entry contradicting a verified claim wins IN THAT FLOW; operator-only, not attacker-reachable, covered by the §1 ⚠ freeze assumption. Recorded in §7 as a spec-delta.
Binding: advisory — security (a human floor; the gate is Tin's to record)

### GATE RECORD
Reported: yes — the dual-verify evidence + gate ARC rendered to Tin before this outcome
Outcome: PASS — recorded by Tin Dang 2026-07-18 (SECURITY task, `autonomy: conservative`; dual-adversarial verify both CLEAR, 158/158 green, earned-green, ruff+pyright clean)
Reviewed by: Tin Dang · date: 2026-07-18

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose **verified-claim is the sole router; `email_domains` = verified-gated IdP-selection metadata**; rejected keep 4 surfaces with a precedence order claim>db-config>env (rejected — leaves unverified surfaces routing) · split IdP-selection from tenant-routing into two new resolvers (rejected — over-built; tenant-by-claim + config-by-tenant_id already exists).
- [human] freeze — froze §3 @ v2 (approved by Tin Dang 2026-07-18. v2 NARROWS v1 to the security core after the build revealed v1 broke 50 legacy frozen tests: (a) M4 env-mapping deletion REVERTED — env `oidc_domain_mapping` retained as trusted operator source, claims take precedence; (b) new `SAML_DOMAIN_NOT_MAPPED` 403 code REVERTED — reuse existing 404s; (c) login is claim-FIRST-then-fallback, not claim-ONLY. KEPT from v1: the write-gate (M5, claims-based, 409/422), the deterministic OIDC resolver (M6, closes the DoS), the backfill migration (M7), normalization (M8). Legacy casualties reduced ~50→~27, all M5 write-gate config-seeding tests to be SANCTIONED-EDIT reconciled (seed a verified claim before PUT — intent preserved). (v1 was: approved 2026-07-18, verified-gate+backfill, OIDC DoS folded in.))
- [AI] build — strategy used: §5's 6 ordered batches, in two passes. Pass 1 (fable/low, Tin-directed): extracted the ONE shared predicate `resolve_verified_tenant_for_raw_domain` before batch 2 (safety rule made structural), implemented all 6 batches to a 16/16 green task suite — then correctly HARD-STOPPED on the mandated cross-task-drift check rather than rewrite 50 frozen legacy tests (CR-v2 followed). Pass 2 (sonnet, CR-v2 rework): restored operator env-mapping as fallback + reverted the SAML 403 churn, centralized the M5 legacy reconciliation into 2 shared "seed a verified claim" helpers threaded via an optional db_session param (most call sites: one-line add), reconciled 30 legacy edits with zero assertion weakening. Verify: dual independent opus adversarial (tamper lens + routing lens), both PASS.
- [human] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
- [SPEC · open] the env-GLOBAL OIDC flow (no `?domain=`, single platform IdP) binds tenant purely by `GATEWAY_OIDC_DOMAIN_MAPPING` and never consults `tenant_domain_claims` — an env entry contradicting a verified claim wins IN THAT FLOW (operator-only, not attacker-reachable; the §1 ⚠ freeze assumption). Add a test pinning "verified claim overrides a contradicting env mapping" in the env-global callback, or explicitly document the bypass (evidence: verifier #2 CONCERN#2).
- [SPEC · seeded] legacy SSO test suites lack a DNS-TXT resolver seam; the M5 reconciliation used direct verified-row inserts per suite — extract a shared verified-claim test factory (evidence: 30 sanctioned edits re-implemented the seed across saml_sso/oidc_tenant_config/plan_seat_cap).

### Competency deltas
- [TDD · folded] when a redesign short-circuits the code path a legacy regression test targeted (claim-first bypassing the resolver-collision path), the assertion can stay green while going INERT — verify the net property coverage MOVED (here: no-500 now proven by test_db_oidc_resolver_deterministic), don't trust the green (evidence: collision_dos LAYER-2, verifier #1). [folded foundation-version 54]
- [ADD · folded] a change-request that NARROWS a frozen contract mid-build must reconcile the §3 contract PROSE too, not just the §1 Must rules — the §3 code-block drifted (said env "DELETED" while M4 said retained) until a verifier caught it (evidence: verifier #2 CONCERN#1). [folded foundation-version 54]
- [ADD · folded] a low-effort executor (fable/low) can implement a well-pinned red suite AND correctly HARD-STOP on a 50-test cross-task-drift casualty rather than weaken tests — the tight red suite + explicit "do not edit tests" constraint carried the safety, not the model tier (evidence: v1 build STOP, then sonnet handled the delicate legacy reconciliation). [folded foundation-version 54]
