# TASK: Shared audit-event primitive for platform-level actions

slug: superadmin-audit-foundation · created: 2026-07-03 · stage: production
autonomy: auto
phase: done

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/ops/api/deps.py:64-93` — `resolve_platform_credential(resolver, session, provider) -> object | None` (ops-platform-job-identity, FROZEN @ v1, gate=PASS). **RETROFIT TARGET.** Confirmed by direct read: zero audit writes today (no `record_audit`/`AuditEvent` import anywhere in this file); zero production callers today (sibling task's own §6 VERIFY: "no HTTP endpoint exists in this task, by design... intended for a future job/endpoint to wire in"). Body: `get_platform_tenant(session)` → `None` raises `PLATFORM_TENANT_MISSING.exc()` (500); else delegates verbatim to `resolve_provider_credential(resolver, platform_tenant.id, provider)`.
- `apps/gateway/src/gateway/proxy/application/use_cases.py:446-474` — `resolve_provider_credential(resolver, tenant_id, provider) -> object | None` (unchanged, delegated-to): returns `None` (pure no-op skip — `platform_tenant.id` never used) when `resolver is None` or `provider not in BYOK_PROVIDERS`; raises `ProblemError(402, "ERR_PROVIDER_KEY_MISSING", ...)` on `ProviderKeyMissing` (the ONLY exception this function raises); else returns a non-None contextvar `Token` via `set_provider_credential(cred)`. This distinguishes "real resolution attempted" (result is non-None-Token-or-raises) from "skipped, never touched the platform tenant" (returns `None` without raising) purely by return value — load-bearing for §3's audit-outcome mapping.
- `apps/gateway/src/gateway/core/error_catalog.py` — `PLATFORM_TENANT_MISSING = ErrorSpec(500, "ERR_PLATFORM_TENANT_MISSING", ...)`; `ErrorSpec` class body (lines 30-68) — `.exc()` returns a `gateway.core.errors.ProblemError`.
- `apps/gateway/src/gateway/core/errors.py:10` — `class ProblemError(Exception)` — `.status`, `.code`, `.title`, `.detail`, `.headers`.
- `apps/gateway/src/gateway/audit/domain/audit_event.py:21-46` — `AuditEvent` frozen dataclass (`id, tenant_id, actor_user_id, actor_email, action, target_type, target_id, result, metadata, created_at`). `__post_init__` INVARIANT (load-bearing for this task's field design): `tenant_id is not None` ⟹ `actor_user_id` MUST be non-None, else raises `ValueError("audit_missing_actor: ...")` **at construction time** — before `record_audit`'s own try/except can protect it. System events (`tenant_id=None`) may have `actor_user_id=None`.
- `apps/gateway/src/gateway/audit/application/audit_writer.py:29-57` — `record_audit(session_factory: async_sessionmaker[AsyncSession], event: AuditEvent) -> None` (audit-log-store TASK.md §3, FROZEN @ v1). FAIL-OPEN: own separate session (isolated from the caller's transaction), swallows ALL exceptions, logs `_log.warning(...)` with `audit_action`/`audit_event_id`/`audit_tenant_id`/`audit_actor_user_id`. MUST be scheduled via `asyncio.ensure_future()`/`asyncio.create_task()` in a request-handling context; the one exception, `retention_sweep.py:270`, `await`s it directly but that call already sits inside its OWN try/except in a background-job loop, not a request path.
- `apps/gateway/src/gateway/audit/infrastructure/audit_repository.py` — `AuditRepository(session)`: `.record(event)` / `.list_for_tenant(tenant_id, limit)` only — no update/delete (enforced by `test_no_mutate_method_app`).
- `apps/gateway/src/gateway/audit/infrastructure/audit_events_orm.py:42-84` — `AuditEventRow` ORM / `audit_events` table (already exists; zero schema change needed).
- `apps/gateway/migrations/versions/e3f5a7c9b1d2_audit_events.py` + `.../f2a4c6e8b0d3_audit_retention_trigger.py` — table + immutability-trigger migrations (already shipped, fully generic, apply to any row this task inserts; zero new migration).
- 8 confirmed EXISTING `record_audit` call sites — the established wiring convention this task's two new sites must match: `proxy/api/provider_keys_admin_router.py:218-234` (read in full — exact shape: `asyncio.ensure_future(record_audit(request.app.state.sessionmaker, AuditEvent(id=uuid4(), tenant_id=tenant_id, actor_user_id=identity.user_id, actor_email=identity.email, action="provider_key.put", target_type="provider", target_id=provider, result="success", metadata={...no secret...}, created_at=datetime.now(UTC))))`), plus `routing_admin_router.py:194+`, `auth/api/oidc_admin_router.py:306+`, `tenants/api/users_router.py:149+`, `budgets/api/router.py:136+`, `teams/api/router.py:213+`, `keys/api/router.py` (4 sites), `usage/application/retention_sweep.py:19-21,268-272` (the one background-job/non-router variant). ALL 8 use `result="success"` only — none exercises `"denied"`/`"error"`, which this task's ops-side retrofit is the first to exercise.
- `apps/gateway/src/gateway/main.py:632-634` — `app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)`; `app.state.token_service = JwtTokenService(settings)`. Both confirmed live globals.
- `apps/gateway/src/gateway/tenants/api/router.py:36,55-64` — `router = APIRouter(prefix="/admin/auth", ...)`; `POST /admin/auth/login` → `LoginUseCase.execute(email, password)` via `Depends(get_login_use_case)`. THE literal "/admin/auth JWT flow" the milestone scopes superadmin login to. The router receives back only `tuple[str, int]` (token, expires_in) — no `role`, and the use case has no `request`/`app.state` access.
- `apps/gateway/src/gateway/tenants/application/use_cases.py:25-41` — `LoginUseCase.execute()`: constant-time credential check (verifies against a dummy hash when `user is None` — "so both failure paths cost the same time," a deliberate timing-attack mitigation this task must not disturb), then `self._tokens.issue(user_id=user.id, tenant_id=user.tenant_id, role=user.role, email=user.email)`. APPLICATION layer — no `session_factory`/`request` dependency today, architecturally unlike all 8 router-layer call sites above.
- `apps/gateway/src/gateway/tenants/infrastructure/jwt_service.py:20-33` — `JwtTokenService.issue()`/`.decode()` — confirmed (directly, not just trusted from the brief) fully generic over `Role`, zero role-specific branching.
- `apps/gateway/src/gateway/auth/application/use_cases.py` — `class OidcLoginUseCase.__init__(self, exchanger, repository, tokens, settings, jwks_client=None, jwks_key_cache=None, domain_mappings=...)` (no `session_factory` today). `.execute()` (167-339): the OIDC/SSO login flow — exchanges code, validates the ID token (RS256/JWKS or v4 TLS-channel mode), resolves `mapped_tenant_id` (per-tenant `OidcProviderConfig` or env `GATEWAY_OIDC_DOMAIN_MAPPING`), then `self._repository.get_or_provision_oidc_user(email, tenant_id=mapped_tenant_id, password_hash=SSO_PASSWORD_HASH_SENTINEL)`, then `self._tokens.issue(user_id=user.id, tenant_id=user.tenant_id, role=user.role, email=user.email)` (line 332-337) — a SECOND, separate JWT-issuance call site. **Confirmed boundary (own inline comment, lines 327-331):** `get_or_provision_oidc_user` always provisions a BRAND-NEW user as `role=member` — "SSO never auto-grants owner/admin" — so this path can NEVER auto-CREATE a superadmin; `user.role` is the user's STORED role specifically so an EXISTING elevated user isn't silently downgraded on SSO login. This task's OIDC coverage therefore only ever audits an EXISTING superadmin's login via SSO, never expands who can become one.
- `apps/gateway/src/gateway/auth/api/deps.py:101-134` — `get_oidc_use_case`/`get_oidc_use_case_with_config(request, session, *, oidc_config)`: the FastAPI DI provider constructing `OidcLoginUseCase(...)`. Full `request` access, so `request.app.state.sessionmaker` (confirmed live global, same as the other 8 call sites and Part A/B) is trivially threadable into a new `session_factory` constructor param — mechanically identical in shape to Part B's candidate (b), and unlike Part B, NOT blocked on any sibling task — `OidcLoginUseCase` already exists in full today. Per Tin's freeze decision (2026-07-03): this call site is IN scope — see Part C below.
- `.add/tasks/superadmin-login/TASK.md` — read in full; CONFIRMED still completely blank (`phase: ground`, every section the unfilled skeleton placeholder) as of this drafting, independently corroborated by `add.py status`'s live task list (`superadmin-login phase=ground gate=none`). This is the direct evidence behind this task's primary lowest-confidence flag.

Context (working folder):
- `.add/milestones/platform-identity/MILESTONE.md` — Scope In (the audit-primitive line, quoted in Touches above); Shared decisions: "Audit rows for platform-level actions reuse the shipped AuditEvent nullable-tenant_id precedent's SHAPE... as prior art... scoped to platform/superadmin actions specifically, not a general port change" — the single most load-bearing sentence for this task's design (drives: reuse verbatim, zero new port/schema, `tenant_id=None` for ops-side rows). Exit criterion (verbatim): "Every superadmin JWT issuance and every ops-authenticated platform-job credential resolution writes a distinguishable audit row."
- `/private/tmp/claude-501/-Users-tindang-workspaces-tind-repo-ai-proxy/767ca570-b0da-476d-a66b-510b7decf24a/scratchpad/draft-shared-context-login-audit.md` — the orchestrator's shared-context brief for this drafting wave; scopes this task as (a) the shared primitive [reuse, not rebuild] (b) the `resolve_platform_credential` retrofit (c) the login-side wiring; flags the build-sequencing dependency on `superadmin-login` and explicitly invites keeping §3 "slightly more abstract about the exact call-site signature" if warranted.
- `.add/tasks/ops-platform-job-identity/TASK.md` — read in full; the retrofit target's own FROZEN @ v1 contract (§3) + VERIFY evidence, cited verbatim above.
- `apps/gateway/tests/audit/test_audit_store.py` — read in full; the test-pattern prior art this task's own future §4 will mirror (`test_missing_actor_rejected`, `test_audit_write_fail_open`, `test_no_secret_in_metadata`, `test_tenant_scoped_newest_first`, the `db_session` fixture convention).

Honors (patterns / conventions):
- CONVENTIONS.md "Errors: machine-readable codes ERR_<DOMAIN>_<REASON>" — this task introduces ZERO new error codes; both existing rejections (`ERR_PLATFORM_TENANT_MISSING` 500, `ERR_PROVIDER_KEY_MISSING` 402) stay byte-identical, gaining only a side effect.
- The established 8-call-site `record_audit` convention (dotted `noun.verb` action names; `asyncio.ensure_future(...)`, never a bare `await` in a request path; `metadata` never carries secret material, mirrored by `test_no_secret_in_metadata`'s field-name denylist) — this task's two new call sites follow it in spirit, with one deliberate, justified deviation: embedding the ops-side audit call INSIDE the shared `resolve_platform_credential` function body itself (not at an external router call site), because zero router/endpoint consumer of that function exists yet — embedding it in the shared seam is the only way the exit criterion's "every... resolution" guarantee can actually hold for whatever consumer eventually calls it, rather than depending on that future, unscoped consumer to remember to add it.
- MILESTONE.md's "reuse... as prior art... not a general port change" — honored by touching zero files under `audit/domain/`, `audit/infrastructure/`, or `audit/application/`, and by adding no migration.

Anchors the contract cites: `resolve_platform_credential` (ops/api/deps.py, retrofit) · `resolve_provider_credential` / `ProblemError` (unchanged, delegated-to) · `PLATFORM_TENANT_MISSING` (unchanged) · `AuditEvent` / `record_audit` / `AuditRepository` (audit-log-store, reused verbatim, zero change) · `app.state.sessionmaker` (existing global) · `LoginUseCase.execute` / `tenants/api/router.py::login` (existing, pre-superadmin-login-build state) · `OidcLoginUseCase` / `get_oidc_use_case_with_config` (auth/application/use_cases.py, auth/api/deps.py — Part C retrofit).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Shared platform/superadmin audit-event primitive, wired into two call sites (ops credential resolution retrofit + superadmin JWT-issuance forward integration)

Framings weighed:
- Primitive shape: reuse the shipped `AuditEvent`/`record_audit`/`AuditRepository` verbatim, zero new port/schema/table **(chosen — MILESTONE.md's own shared decision names this SHAPE as prior art and explicitly forecloses "a general port change")** · a new dedicated `PlatformAuditEvent` table/port scoped only to platform actions **(rejected — MILESTONE.md forecloses inventing a parallel mechanism just as much as it forecloses generalizing the existing one; would also fragment one audit trail across two tables for no queryable benefit)** · extending `AuditEvent`'s frozen dataclass with a new `is_platform_action: bool` field **(rejected — would touch audit-log-store's own FROZEN, cross-milestone, differently-owned §3 contract for a need already satisfiable via existing fields: `tenant_id=None` + a distinct `action` verb prefix is 100% distinguishing on its own)**
- Ops-side retrofit placement: instrument INSIDE `resolve_platform_credential`'s own body, covering all 3 outcomes (success / tenant-missing / key-missing), with a new required `session_factory` param **(chosen)** · wait for a future consumer endpoint to add its own audit call at its own call site, mirroring the 8 existing router-layer sites **(rejected — no such endpoint exists or is scoped by any current task; this would leave the exit criterion unmet indefinitely, exactly the "aspirational, half-true done" failure mode this task must not fall into. Embedding it in the shared seam is a strictly stronger guarantee — impossible for a future caller to forget)** · only audit the success outcome, mirroring how all 8 existing sites only ever use `result="success"` **(rejected — the exit criterion literally says "every... resolution," and `AuditEvent.result`'s own three-valued docstring `("success" | "denied" | "error")` was clearly designed for exactly this; auditing only success would silently miss the highest-signal case — an unconfigured-provider or missing-platform-tenant attempt)**
- Login-side integration (`/admin/auth` password path): audit fires only when the issued `role == Role.SUPERADMIN`, on a successful `tokens.issue()`, reusing `record_audit` verbatim **(chosen, event-shape only — see the flag below on call-site placement)** · audit every login regardless of role, with role captured in metadata **(rejected — MILESTONE.md scopes this primitive to "platform/superadmin actions specifically, not a general... change"; auditing every tenant owner/member login is materially larger blast radius against a hot path, outside this milestone's charter)** · also audit failed superadmin login attempts (wrong password against a superadmin email) **(rejected — (a) the exit criterion says "issuance," and a failed attempt never reaches issuance; (b) distinguishing "this failed attempt targeted a superadmin account" would require a role lookup BEFORE the password check, risking reintroduction of the exact timing side-channel `LoginUseCase.execute()` deliberately avoids today via its dummy-hash-on-`None`-user comparison)**
- OIDC/SSO coverage: widen this task to ALSO audit `OidcLoginUseCase.execute()`'s superadmin JWT issuance, as a third, fully concrete contract part alongside `/admin/auth` **(chosen — Tin's explicit freeze decision, 2026-07-03: "freeze, but widen Part B to also cover the OIDC/SSO path"; the milestone's own exit-criterion wording — "every superadmin JWT issuance" — is literally broader than "/admin/auth" alone, and `OidcLoginUseCase` is confirmed mechanically capable of issuing a superadmin JWT for an EXISTING superadmin user matched by email)** · defer OIDC coverage to a separate, later follow-up task **(rejected — superseded by Tin's decision; was this contract's drafted default before freeze)** · fold both paths into one shared helper function called from both use cases **(rejected — `LoginUseCase` and `OidcLoginUseCase` live in different modules with different constructor shapes and different existing DI patterns; a shared helper would be a new abstraction neither use case currently needs, when duplicating a ~10-line `AuditEvent(...)` construction — already the established pattern at 8 existing call sites, none of which share a helper either — is simpler and stays consistent with the existing convention)**

Must:
<must>
  - `resolve_platform_credential` gains a new required parameter `session_factory: async_sessionmaker[AsyncSession]`, used ONLY to schedule the audit write; the existing `resolver`/`session`/`provider` parameters and their use for actual credential resolution are UNCHANGED.
  - `resolve_platform_credential` emits exactly one audit row per invocation that reaches "platform tenant resolved" — covering all three real outcomes: (1) success (a non-None Token returned by `resolve_provider_credential`), (2) platform tenant missing (`PLATFORM_TENANT_MISSING`, 500), (3) provider key missing (`ProblemError` 402 from `resolve_provider_credential`). The pre-existing pass-through-`None` skip (resolver unwired, or provider not in `BYOK_PROVIDERS`) remains UNAUDITED — unchanged, still a pure no-op that never reaches platform-tenant-specific logic, per `resolve_provider_credential`'s own unchanged contract.
  - Every ops-side audit row uses `tenant_id=None` and `actor_user_id=None` (a "system-level event" per `AuditEvent`'s own docstring) — this is not a style preference, it is FORCED by `AuditEvent.__post_init__`'s invariant: ops-mTLS auth (`OpsIdentity(fingerprint: str)`) carries no `user_id` at all, so `tenant_id` cannot be set non-None (e.g. to the platform tenant's own id) without also fabricating a non-existent `actor_user_id`, which this task will never do. The platform tenant's id (when resolved) and the ops cert fingerprint are carried in `metadata` instead, as the closest honest analogue to "who."
  - Ops-side `action="ops.platform_credential_resolve"` for all three outcomes, `target_type="provider"`, `target_id=<provider>`, `result` ∈ {`"success"`, `"error"` (tenant missing), `"denied"` (key missing)}, `metadata` never contains secret material (mirrors `test_no_secret_in_metadata`'s denylist) — only `provider` and, when resolvable, `platform_tenant_id`.
  - On the `PLATFORM_TENANT_MISSING` and `ProblemError`(402) paths, the audit write is scheduled BEFORE re-raising, and the original exception is re-raised completely UNCHANGED (same type, status, code, title) — this task adds a side effect, never alters an existing error contract.
  - Both new call sites reuse `record_audit(session_factory, event)` UNCHANGED — fire-and-forget via `asyncio.ensure_future`, own separate session, swallow-all-exceptions-log-a-warning FAIL-OPEN semantics inherited verbatim. Zero new audit-writing logic, zero new failure-handling logic is written by this task.
  - An audit-write failure (e.g. the audit DB session raising) NEVER blocks, delays, or fails the superadmin login response, and NEVER blocks, delays, or fails `resolve_platform_credential`'s own return/raise — the primary action's outcome is byte-identical whether or not the fire-and-forget audit write ultimately succeeds.
  - The `/admin/auth` login-side audit row fires if and only if the just-issued JWT's `role == Role.SUPERADMIN`, only after `tokens.issue()` has actually returned (never on a failed/rejected login attempt, which raises `InvalidCredentialsError` before issuance and therefore before this hook is ever reached). Fields: `tenant_id=<issued tenant_id>` (always the platform tenant's, per the milestone's byte-identical JWT invariant — never None here), `actor_user_id=<user.id>`, `actor_email=<user.email>`, `action="auth.superadmin_login"`, `target_type="user"`, `target_id=str(user.id)`, `result="success"`, `metadata={"role": "superadmin", "auth_method": "password"}`.
  - `OidcLoginUseCase` gains a new required constructor parameter `session_factory: async_sessionmaker[AsyncSession]`, wired at its one construction site (`auth/api/deps.py::get_oidc_use_case_with_config`, from the same `request.app.state.sessionmaker` global as every other call site) — used ONLY to schedule the audit write; the existing OIDC exchange/validation/provisioning logic is UNCHANGED.
  - The OIDC-side audit row fires under the IDENTICAL rule as the `/admin/auth` row above (`role == Role.SUPERADMIN`, only after `self._tokens.issue()` returns), with the SAME field shape except `metadata={"role": "superadmin", "auth_method": "oidc"}` — the one intentional difference, so a security review of `audit_events` can tell the two issuance mechanisms apart without inferring it from context. This can only ever fire for an EXISTING superadmin user matched by email under the correctly-mapped platform tenant: `get_or_provision_oidc_user` provisions any BRAND-NEW user as `role=member` unconditionally (never from claims), so OIDC can never auto-create or auto-promote a superadmin — this task audits an existing privilege, it does not introduce a new way to acquire one.
  - Zero lines change in `AuditEvent`, `record_audit`, `AuditRepository`, `AuditEventRow`, or any audit migration — this task is a pure consumer of the existing primitive, not a modifier of it.
  - `GET /ops/reconciliation`, `require_ops`, `OpsIdentity`, `get_platform_tenant`, `resolve_provider_credential`, `JwtTokenService.issue`/`.decode`, `get_or_provision_oidc_user`'s member-only provisioning rule, `OidcTenantConflictError`'s raise condition, and the JWT required-claims set are byte-identical after this task.
</must>

Reject:
<reject>
  - (unchanged from `resolve_platform_credential`'s existing FROZEN contract) the platform tenant row does not exist -> `"ERR_PLATFORM_TENANT_MISSING"` (500) — now ALSO produces a `result="error"` audit row before the raise; the code/status/title are unchanged.
  - (unchanged from `resolve_platform_credential`'s existing FROZEN contract) the platform tenant has no enabled credential for `provider` -> `"ERR_PROVIDER_KEY_MISSING"` (402) — now ALSO produces a `result="denied"` audit row before the re-raise; the code/status/title are unchanged.
  - (unchanged) `resolver is None`, or `provider not in BYOK_PROVIDERS` -> returns `None`, no exception, and (new, explicit) NO audit row — this situation never reached platform-tenant-specific logic.
  - A non-superadmin login (any of the other 6 `Role` values) -> JWT issued exactly as today, and (new, explicit) NO audit row — this task's primitive is scoped to platform/superadmin actions only, per MILESTONE.md.
  - A failed login attempt (wrong password / unknown email, for any role including a superadmin email) -> `InvalidCredentialsError` exactly as today, and (new, explicit) NO audit row, NO role lookup performed ahead of the password check — preserves the existing constant-time-failure mitigation unchanged.
  - A brand-new OIDC login (email never seen before) -> `get_or_provision_oidc_user` provisions it as `role=member` exactly as today (unchanged — never from claims), and (new, explicit) NO audit row — correctly not a superadmin event, by construction not by a role check.
  - An OIDC login for an existing, NON-superadmin user under the correctly-mapped tenant -> logged in with their stored role exactly as today, and (new, explicit) NO audit row.
  - An OIDC login where the email exists but under a DIFFERENT tenant than the domain-mapping resolved -> `OidcTenantConflictError` raised exactly as today (unchanged), and (new, explicit) NO audit row — issuance is never reached, and this task changes nothing about how that conflict is surfaced to the caller.
  - An OIDC exchange or ID-token/claims validation failure (bad code, invalid/expired token, JWKS mismatch, unmapped domain) -> the existing rejection is raised exactly as today, and (new, explicit) NO audit row — issuance is never reached; this task adds no logic upstream of `get_or_provision_oidc_user`.
  - The audit write itself fails for any reason (DB unreachable, constraint violation, etc.) -> NOT surfaced to the caller of any of the three call sites in any form — swallowed by `record_audit`'s existing fail-open contract, logged as a warning only.
</reject>

After:
<after>
  - Given ops-auth is valid, the platform tenant exists, and its `provider` credential is enabled: `resolve_platform_credential` returns the resolved credential exactly as before, AND exactly one `audit_events` row exists with `action="ops.platform_credential_resolve"`, `result="success"`, `tenant_id=NULL`, `actor_user_id=NULL`.
  - Given the platform tenant row is missing: `PLATFORM_TENANT_MISSING` (500) is raised exactly as before, AND exactly one `audit_events` row exists with `result="error"`.
  - Given the platform tenant has no enabled `provider` credential: `ProblemError`(402) is raised exactly as before, AND exactly one `audit_events` row exists with `result="denied"`.
  - Given a superadmin successfully logs in via `/admin/auth/login`: the returned JWT is byte-identical to today's shape (still carries the platform tenant's real `tenant_id`), AND exactly one `audit_events` row exists with `action="auth.superadmin_login"`, `result="success"`, `tenant_id=<platform tenant id>`, `actor_user_id=<the superadmin's user id>`, `metadata.auth_method="password"`.
  - Given any non-superadmin logs in successfully via `/admin/auth/login`: the returned JWT is byte-identical to today's shape, AND zero new `audit_events` rows exist (row count for `action="auth.superadmin_login"` unchanged).
  - Given an existing superadmin logs in successfully via OIDC/SSO under the correctly-mapped platform tenant: the returned JWT is byte-identical to today's shape (role=superadmin preserved from the stored user row), AND exactly one `audit_events` row exists with `action="auth.superadmin_login"`, `result="success"`, `tenant_id=<platform tenant id>`, `actor_user_id=<the superadmin's user id>`, `metadata.auth_method="oidc"`.
  - Given a brand-new email logs in via OIDC/SSO: the user is provisioned with `role=member` exactly as today, AND zero new `audit_events` rows exist for `action="auth.superadmin_login"`.
  - Given the audit DB write fails: the login response and the credential-resolution outcome are completely unaffected on all three call sites — a warning is logged, no exception propagates from the audit side effect.
  - No new HTTP endpoint, error code, migration, or audit port/schema exists after this task — only three existing call sites gained a side effect (two retrofitted now, one — the `/admin/auth` login-side — to be wired once `superadmin-login` ships its own frozen shape).
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The exact call-site signature for wiring the login-side audit call cannot be pinned yet: `superadmin-login`'s own TASK.md is confirmed still completely blank (`phase: ground`, every section unfilled — verified by direct read AND by `add.py status`'s live task list) as of this drafting. Two architecturally different, both mechanically-viable integration shapes exist today: (a) router-layer wiring in `tenants/api/router.py::login`, mirroring the 8 existing call sites, requiring either a small additive change to `LoginUseCase.execute()`'s return shape (to surface `role`) or a post-issuance `tokens.decode()` round-trip at the router (both `app.state.token_service` and a `get_token_service` DI provider already exist, so this is mechanically free); (b) application-layer wiring inside `LoginUseCase.execute()` itself, mirroring how this task instruments `resolve_platform_credential` internally, requiring `LoginUseCase` to gain a new `session_factory` constructor dependency — a genuinely new pattern for this use-case class (no existing use case in this codebase depends on a raw `session_factory` today). Lowest confidence because it depends entirely on a sibling task's still-undrafted contract; if wrong: a bounded change-request back to SPECIFY for the login-half of this contract only — the ops-side retrofit half is fully specified, independently buildable, and unaffected either way. §3 CONTRACT freezes the login-side EVENT SHAPE and FIRING RULE concretely but leaves the exact call-site/function signature abstract, to be pinned against `superadmin-login`'s own frozen §3 before this task's own Build.
  - [x] RESOLVED at freeze (2026-07-03, Tin's explicit decision): OIDC/SSO coverage is IN scope, not deferred — this contract's Part C (§3) audits `OidcLoginUseCase.execute()`'s superadmin JWT issuance too, closing the gap between the milestone's exit-criterion wording ("every superadmin JWT issuance") and the narrower `/admin/auth`-only reading this task originally drafted.
  - [ ] Adding a required `session_factory` parameter to `resolve_platform_credential` is a signature change to an already-FROZEN (ops-platform-job-identity, gate=PASS) function. Treated here as a normal, expected "retrofit an existing shipped function" change — not an edit to that task's own TASK.md record, which stays untouched — since that function is documented by its own owning task as a deliberately-unconsumed seam awaiting a future caller. Confirm or deny this reading of what "retrofit" licenses. (The same reading now also applies to Part C's `OidcLoginUseCase` constructor — a signature change to already-shipped code, its one call site confirmed via `mcp__serena` to be the sole construction site in the whole `gateway/` tree, so no cascading test breakage from direct constructor calls.)
  - [ ] The `result` classification for the two ops-side rejections (`tenant-missing` → `"error"`, `key-missing` → `"denied"`) is an observability-only labeling choice with zero behavioral impact on any caller — lowest-stakes item in this contract, a one-line swap if Tin prefers the reverse mapping or a single uniform value.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: an ops-authenticated resolution of a configured platform credential is audited
  Given the platform tenant row exists and has an enabled "minimax" credential configured
  When resolve_platform_credential(resolver, session, "minimax", session_factory) is called
  Then it returns the resolved credential exactly as before this task
  And exactly one audit_events row is written with action="ops.platform_credential_resolve", result="success", tenant_id IS NULL, actor_user_id IS NULL, metadata containing "minimax" and the platform tenant's id

Scenario: a non-BYOK provider or an unwired resolver remains a silent, unaudited no-op
  Given the platform tenant row exists
  When resolve_platform_credential is called with a provider outside BYOK_PROVIDERS, and separately with resolver=None
  Then both calls return None exactly as before this task
  And no audit_events row is written for either call — platform-tenant-specific logic was never reached, unchanged from before this task

Scenario: a missing platform tenant is audited as an error before the 500 is raised
  Given get_platform_tenant(session) returns None (pre-seed/unmigrated state)
  When resolve_platform_credential(resolver, session, "minimax", session_factory) is called
  Then ProblemError(500, "ERR_PLATFORM_TENANT_MISSING") is raised, byte-identical to before this task
  And exactly one audit_events row is written with action="ops.platform_credential_resolve", result="error", tenant_id IS NULL, actor_user_id IS NULL
  And the resolver is never consulted — no fabricated tenant_id is used, unchanged from before this task

Scenario: a missing provider credential is audited as denied before the 402 is raised
  Given the platform tenant row exists with no enabled "minimax" credential
  When resolve_platform_credential(resolver, session, "minimax", session_factory) is called
  Then ProblemError(402, "ERR_PROVIDER_KEY_MISSING") is raised, byte-identical to before this task
  And exactly one audit_events row is written with action="ops.platform_credential_resolve", result="denied", tenant_id IS NULL, actor_user_id IS NULL

Scenario: an ops-side audit write failure never blocks credential resolution
  Given the platform tenant row exists and has an enabled "minimax" credential configured
  And the supplied session_factory raises on every session open (simulating an audit DB outage)
  When resolve_platform_credential(resolver, session, "minimax", session_factory) is called
  Then it still returns the resolved credential exactly as in the healthy-audit-DB case
  And no exception from the audit write propagates to the caller — the credential-resolution outcome is unchanged by an audit-subsystem failure
  And a warning is logged (record_audit's existing, unchanged fail-open behavior)

Scenario: a superadmin's successful login is audited
  Given a User row exists with role=superadmin under the platform tenant (provisioned directly via fixture, mirroring how other role-scoped suites provision test users)
  When that user logs in successfully via POST /admin/auth/login
  Then the returned JWT is byte-identical in shape to today's — decodes with role=superadmin and tenant_id=<the platform tenant's id>
  And exactly one audit_events row is written with action="auth.superadmin_login", result="success", tenant_id=<the platform tenant id>, actor_user_id=<the superadmin's user id>, metadata={"role": "superadmin"}

Scenario: a non-superadmin's successful login is not audited by this primitive
  Given a User row exists with role=owner (or any non-superadmin role) under an ordinary tenant
  When that user logs in successfully via POST /admin/auth/login
  Then the returned JWT is issued exactly as today, unchanged by this task
  And no new audit_events row with action="auth.superadmin_login" exists — the row count for that action is unchanged

Scenario: a failed login attempt against a superadmin email is not audited and performs no role lookup
  Given a User row exists with role=superadmin and a known email
  When a login attempt is made against that email with the wrong password
  Then InvalidCredentialsError is raised exactly as today — no JWT is issued, unchanged from before this task
  And no audit_events row is written — issuance was never reached
  And the rejection path's timing is unaffected by this task — no role lookup was added ahead of the existing constant-time password check

Scenario: a login-side audit write failure never blocks a superadmin's login response
  Given a User row exists with role=superadmin under the platform tenant
  And the audit write's session_factory raises on every session open (simulating an audit DB outage)
  When that user logs in successfully
  Then the login response (JWT + expires_in) is returned exactly as in the healthy-audit-DB case
  And no exception from the audit write propagates to the caller — the login outcome is unchanged by an audit-subsystem failure

Scenario: an existing superadmin's successful OIDC login is audited
  Given a User row exists with role=superadmin, tenant_id=<the platform tenant's id>, and a known email
  And the OIDC provider's domain mapping resolves that email's tenant to the platform tenant
  When OidcLoginUseCase.execute(...) completes a successful exchange and claims validation for that email
  Then the returned JWT is byte-identical in shape to today's — decodes with role=superadmin and tenant_id=<the platform tenant's id>
  And exactly one audit_events row is written with action="auth.superadmin_login", result="success", tenant_id=<the platform tenant id>, actor_user_id=<the superadmin's user id>, metadata={"role": "superadmin", "auth_method": "oidc"}

Scenario: a non-superadmin OIDC login, new or existing, is not audited by this primitive
  Given (a) an email never seen before completes a successful OIDC exchange mapped to some tenant, and separately (b) a User row with role=owner (or any non-superadmin role) completes a successful OIDC exchange mapped to its own correct tenant
  When OidcLoginUseCase.execute(...) completes for each
  Then (a) is provisioned with role=member exactly as today, and (b) is logged in with its stored role exactly as today
  And neither produces a new audit_events row for action="auth.superadmin_login" — the row count for that action is unchanged by either call

Scenario: OIDC rejections never reach the audit hook
  Given (a) a User row exists with a known email under tenant X, and a separate OIDC domain mapping resolves that same email to a different tenant Y, and separately (b) an OIDC exchange or ID-token/claims validation fails (bad code, invalid/expired token, JWKS mismatch, or an unmapped domain)
  When OidcLoginUseCase.execute(...) is invoked for each
  Then (a) raises OidcTenantConflictError exactly as today, and (b) raises its existing corresponding rejection exactly as today — in both cases, unchanged from before this task
  And no audit_events row is written for either — get_or_provision_oidc_user / tokens.issue() were never reached

Scenario: an OIDC-side audit write failure never blocks a superadmin's OIDC login response
  Given a User row exists with role=superadmin under the platform tenant, reachable via a correctly-mapped OIDC domain
  And the audit write's session_factory raises on every session open (simulating an audit DB outage)
  When that user completes a successful OIDC login
  Then the login response (JWT + expires_in) is returned exactly as in the healthy-audit-DB case
  And no exception from the audit write propagates to the caller — the login outcome is unchanged by an audit-subsystem failure
```

</scenarios>

Note: three Musts are structural claims verified by code/import-diff review, not a runtime Gherkin scenario, mirroring how the sibling `ops-platform-job-identity` task recorded its own non-runtime Musts: (1) `resolve_platform_credential` gains exactly one new parameter (`session_factory`), used only for the audit side effect — confirmed by diffing its signature, not by a behavioral test; (2) `OidcLoginUseCase.__init__` gains exactly one new parameter (`session_factory`), and its one construction site (`auth/api/deps.py`) is updated to supply it — confirmed by diffing the signature and by `mcp__serena` confirming no other construction site exists; (3) `AuditEvent`, `record_audit`, `AuditRepository`, `AuditEventRow`, every audit migration, `GET /ops/reconciliation`, `require_ops`, `OpsIdentity`, `get_platform_tenant`, `resolve_provider_credential`, `get_or_provision_oidc_user`'s member-only rule, `OidcTenantConflictError`'s raise condition, and `JwtTokenService.issue`/`.decode` are byte-identical after this task — confirmed by `git diff --stat` showing no entries for those files/symbols beyond the three call sites this task legitimately touches.

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
No new HTTP endpoint, port, schema, or migration. Two consumers of the EXISTING,
UNCHANGED audit-log-store primitive (AuditEvent / record_audit / AuditRepository).

═══════════════════════════════════════════════════════════════════════════════
PART A — ops-side retrofit (FROZEN shape, concrete, ready to build)
═══════════════════════════════════════════════════════════════════════════════

async def resolve_platform_credential(
    resolver: TenantCredentialResolver | None,
    session: AsyncSession,
    provider: str,
    session_factory: async_sessionmaker[AsyncSession],   # NEW parameter, audit-only
) -> object | None:
    """Resolve the platform tenant's own credential for `provider` — now audited.

    Unchanged from the existing FROZEN contract: PRECONDITION (caller already
    authorized via Depends(require_ops)); returns None when resolver is None or
    provider not in BYOK_PROVIDERS (pass-through skip, UNAUDITED — never reaches
    platform-tenant-specific logic); raises ProblemError(500,
    ERR_PLATFORM_TENANT_MISSING) or re-raises the 402 ERR_PROVIDER_KEY_MISSING
    from resolve_provider_credential, both byte-identical to before this task.

    NEW (this task): every invocation that reaches platform-tenant-specific logic
    (get_platform_tenant succeeded) emits exactly one fire-and-forget, fail-open
    AuditEvent via record_audit(session_factory, ...) — covering all three real
    outcomes below. tenant_id=None / actor_user_id=None on every row: FORCED by
    AuditEvent.__post_init__'s actor invariant, since ops-mTLS auth carries no
    user identity to satisfy a non-None tenant_id. The platform tenant's id (once
    resolved) and `provider` are carried in metadata instead.
    """
    platform_tenant = await get_platform_tenant(session)
    if platform_tenant is None:
        asyncio.ensure_future(  # noqa: RUF006
            record_audit(
                session_factory,
                AuditEvent(
                    id=uuid.uuid4(), tenant_id=None, actor_user_id=None, actor_email=None,
                    action="ops.platform_credential_resolve", target_type="provider",
                    target_id=provider, result="error",
                    metadata={"provider": provider}, created_at=datetime.now(UTC),
                ),
            )
        )
        raise PLATFORM_TENANT_MISSING.exc()
    try:
        result = await resolve_provider_credential(resolver, platform_tenant.id, provider)
    except ProblemError:
        asyncio.ensure_future(  # noqa: RUF006
            record_audit(
                session_factory,
                AuditEvent(
                    id=uuid.uuid4(), tenant_id=None, actor_user_id=None, actor_email=None,
                    action="ops.platform_credential_resolve", target_type="provider",
                    target_id=provider, result="denied",
                    metadata={"provider": provider, "platform_tenant_id": str(platform_tenant.id)},
                    created_at=datetime.now(UTC),
                ),
            )
        )
        raise
    if result is not None:
        asyncio.ensure_future(  # noqa: RUF006
            record_audit(
                session_factory,
                AuditEvent(
                    id=uuid.uuid4(), tenant_id=None, actor_user_id=None, actor_email=None,
                    action="ops.platform_credential_resolve", target_type="provider",
                    target_id=provider, result="success",
                    metadata={"provider": provider, "platform_tenant_id": str(platform_tenant.id)},
                    created_at=datetime.now(UTC),
                ),
            )
        )
    return result

New imports in ops/api/deps.py: asyncio · uuid · datetime.now/UTC ·
  record_audit (gateway.audit.application.audit_writer) ·
  AuditEvent (gateway.audit.domain.audit_event) ·
  ProblemError (gateway.core.errors) · async_sessionmaker (sqlalchemy.ext.asyncio)

Reject (unchanged codes — this task adds a side effect, never a new caller-facing error):
  get_platform_tenant(session) is None -> ProblemError(500, "ERR_PLATFORM_TENANT_MISSING")
    (unchanged code/status/title; NEW: preceded by a result="error" audit write)
  ProblemError from resolve_provider_credential -> re-raised bare, unchanged
    (unchanged code/status/title; NEW: preceded by a result="denied" audit write)

═══════════════════════════════════════════════════════════════════════════════
PART B — login-side integration (event shape + firing rule FROZEN; exact
call-site signature DEFERRED — see "Least-sure flag" below)
═══════════════════════════════════════════════════════════════════════════════

Fires if and only if the just-issued JWT's role == Role.SUPERADMIN, immediately
after tokens.issue() returns successfully (never on a rejected login attempt,
which raises InvalidCredentialsError before issuance is ever reached).

  AuditEvent(
      id=uuid.uuid4(),
      tenant_id=<the issued token's tenant_id>,  # always the platform tenant's,
                                                  # per the milestone's byte-identical JWT invariant
      actor_user_id=<user.id>,
      actor_email=<user.email>,
      action="auth.superadmin_login",
      target_type="user",
      target_id=str(user.id),
      result="success",
      metadata={"role": "superadmin"},
      created_at=datetime.now(UTC),
  )
  scheduled via record_audit(session_factory, event) — fire-and-forget, fail-open,
  unchanged, identical in kind to Part A's calls.

Candidate call-site shapes (NOT frozen by this contract — pin against
superadmin-login's own §3 once it exists, before this task's Build):
  (a) router-layer, in tenants/api/router.py::login, mirroring the 8 existing
      call sites — requires LoginUseCase.execute() to additionally surface
      `role` in its return (a small additive shape change), OR a post-issuance
      tokens.decode(token) round-trip at the router using the already-existing
      app.state.token_service / get_token_service dependency.
  (b) application-layer, inside LoginUseCase.execute() itself, mirroring how
      Part A instruments resolve_platform_credential — requires LoginUseCase to
      gain a new session_factory constructor dependency (a new pattern for this
      use-case class).
Whichever shape superadmin-login's real build lands on, this task's own Build
re-confirms the exact insertion point against that frozen §3 before writing any
code — the event shape and firing rule above are unaffected either way.

Reject (unchanged codes):
  InvalidCredentialsError (wrong password / unknown email) -> unchanged,
    no audit reached — issuance never happens, no role lookup added ahead of it.
  role != Role.SUPERADMIN on a successful issuance -> unchanged JWT issuance,
    no audit row (this primitive is scoped to platform/superadmin actions only).

═══════════════════════════════════════════════════════════════════════════════
PART C — OIDC/SSO login-side integration (FROZEN shape, concrete, ready to
build — unlike Part B, NOT blocked on any sibling task: OidcLoginUseCase
already exists in full today. Added per Tin's freeze decision, 2026-07-03.)
═══════════════════════════════════════════════════════════════════════════════

class OidcLoginUseCase:
    def __init__(
        self,
        exchanger: ...,
        repository: IdentityRepository,
        tokens: TokenService,
        settings: Settings,
        jwks_client: JwksClient | None = None,
        jwks_key_cache: JwksKeyCache | None = None,
        domain_mappings: ... = ...,
        session_factory: async_sessionmaker[AsyncSession],   # NEW parameter, audit-only, required
    ) -> None:
        ...
        self._session_factory = session_factory

    async def execute(self, ...) -> tuple[str, int]:
        # ... unchanged: code exchange, ID-token/claims validation, domain-mapping
        # resolution, get_or_provision_oidc_user(...) (raises OidcTenantConflictError
        # on a cross-tenant email match; provisions role=member unconditionally for a
        # brand-new email — neither path is touched by this task) ...

        jwt_token, expires_in = self._tokens.issue(
            user_id=user.id, tenant_id=user.tenant_id, role=user.role, email=user.email,
        )

        # NEW (this task): fires iff the STORED role being preserved into this JWT
        # is superadmin — identical firing rule to Part B, evaluated post-issuance.
        if user.role == Role.SUPERADMIN:
            asyncio.ensure_future(  # noqa: RUF006
                record_audit(
                    self._session_factory,
                    AuditEvent(
                        id=uuid.uuid4(), tenant_id=user.tenant_id, actor_user_id=user.id,
                        actor_email=user.email, action="auth.superadmin_login",
                        target_type="user", target_id=str(user.id), result="success",
                        metadata={"role": "superadmin", "auth_method": "oidc"},
                        created_at=datetime.now(UTC),
                    ),
                )
            )

        return jwt_token, expires_in

New imports in auth/application/use_cases.py: asyncio · uuid · datetime.now/UTC ·
  record_audit (gateway.audit.application.audit_writer) ·
  AuditEvent (gateway.audit.domain.audit_event) ·
  async_sessionmaker (sqlalchemy.ext.asyncio) · Role (already imported)
DI wiring: auth/api/deps.py::get_oidc_use_case_with_config — one additional
  keyword arg, `session_factory=request.app.state.sessionmaker`, at the sole
  existing OidcLoginUseCase(...) construction call (confirmed via mcp__serena:
  the only construction site in the whole gateway/ tree).

Reject (unchanged codes/behavior — this task adds a side effect only on the
already-successful-issuance path, never on a rejection path):
  OidcTenantConflictError (email exists under a different tenant than the
    domain mapping resolved) -> re-raised bare, unchanged; no audit reached.
  Any OIDC exchange / ID-token / claims / JWKS rejection -> unchanged; no audit
    reached — this task adds no logic upstream of get_or_provision_oidc_user.
  role != Role.SUPERADMIN on a successful issuance (including a brand-new,
    always-member-provisioned user) -> unchanged JWT issuance / provisioning,
    no audit row.

Schema: no new tables/columns/migrations. All three parts write to the
  EXISTING audit_events table (audit-log-store TASK.md §3, FROZEN @ v1) via the
  EXISTING AuditRepository.record / record_audit — this task adds zero lines
  under gateway/audit/**. Reads: unchanged (tenants / tenant_provider_keys via
  the existing chain for Part A; users via the existing IdentityRepository for
  Parts B and C).

IO note — design for failure (all three parts): reuse record_audit's ALREADY-FROZEN
  fail-open contract verbatim — its own separate DB session (isolated from the
  caller's transaction: an audit failure can never roll back or block a login or
  a credential resolution, and a caller-side rollback can never lose a committed
  audit row), swallow-all-exceptions-log-a-warning, fire-and-forget via
  asyncio.ensure_future (never awaited on the request path). No new retry,
  timeout, or circuit-breaker is added for the audit write itself:
    1. it would be a bespoke, inconsistent deviation from an already-adjudicated,
       repo-wide, FROZEN convention applied uniformly at 8 other call sites plus
       the retention sweeper;
    2. fail-CLOSED here would mean an audit-DB blip blocks superadmin login or
       platform-job credential resolution — exactly the paths most needed to
       keep working during an incident, including one caused by the audit
       subsystem itself;
    3. the fail-open path is not silent — record_audit logs a structured warning
       (action / event_id / tenant_id / actor_user_id) that still feeds existing
       log-based observability.
  AuditEvent(...) construction is not defensively wrapped at any of the three
  call sites (matching the 8 existing sites' own convention) because all three
  call sites' field choices are proven, by the §0/§1 invariant analysis, to
  always satisfy AuditEvent.__post_init__'s actor invariant — construction
  cannot raise here.
```

Status: FROZEN @ v1 — approved by Tin Dang
Least-sure flag surfaced at freeze:
⚠ [contract] PRIMARY: Part B's exact call-site signature (which of the two candidate shapes —
router-layer post-issuance wiring, or an application-layer session_factory dependency on
LoginUseCase) is deliberately left unpinned, because `superadmin-login`'s own TASK.md is confirmed
still completely blank (phase=ground, every section unfilled) as of this drafting — it is being
drafted in parallel by a sibling agent and has not reached its own §3 yet. The event shape and
firing rule (role==SUPERADMIN, success-only, exact AuditEvent fields) ARE frozen and do not depend
on which shape wins. Cost if this task's Build guesses wrong before re-checking against
superadmin-login's real frozen §3: a bounded change-request back to SPECIFY for Part B only — Part
A (the ops-side retrofit) and Part C (the OIDC-side integration) are both fully specified,
independently buildable, and completely unaffected by how Part B's call site resolves.
Mitigation already built into this contract: Build is sequenced login-first by the orchestrator
specifically so this re-check happens against real code, not a guess.
RESOLVED at freeze (was SECONDARY): whether this contract should also wire the OIDC/SSO JWT-issuance
call site (`auth/application/use_cases.py::OidcLoginUseCase`) was flagged as an open scope question
in the draft. Tin's explicit decision, 2026-07-03: widen to cover it — now Part C above, frozen at
the same concreteness as Part A (OidcLoginUseCase already exists in full; unlike Part B it is not
blocked on any sibling task). Residual, lowest remaining stakes: Part C's audit hook is reachable in
practice only when the OIDC provider's domain/tenant mapping is operator-configured to resolve a
superadmin's email to the platform tenant — confirmed by direct read of
`get_or_provision_oidc_user` (global email lookup, `OidcTenantConflictError` on a tenant mismatch,
never a silent cross-tenant login). That configuration correctness is outside this task's code
surface entirely (no code path in this contract can make a misconfigured mapping resolve correctly),
so it is not treated as a contract gap — noted here only so a later security review has the context.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: scenario coverage — one test per §2 scenario. ALL THREE PARTS DONE: 13/13
  (5 Part-A + 4 Part-B + 4 Part-C — Part A's count is 5, not the 4 originally estimated in
  §5's pre-declared Strategy; the extra is audit-write-fail-open, a distinct, separately-
  load-bearing Must — see §5 "Strategy actually used").
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  Part A (`apps/gateway/tests/superadmin_audit_foundation/test_part_a_platform_credential_audit.py`, DONE):
  - test_successful_resolution_is_audited: arrange platform tenant + programmed credential /
    act resolve_platform_credential(...) / assert token returned unchanged + exactly 1
    result="success" row with the full expected shape
  - test_non_byok_provider_and_unwired_resolver_remain_unaudited: arrange 2 no-op-skip
    inputs / act both / assert both return None unchanged + 0 audit rows
  - test_missing_platform_tenant_audited_as_error: arrange no platform tenant / act call /
    assert ProblemError(500) unchanged + exactly 1 result="error" row
  - test_missing_provider_credential_audited_as_denied: arrange tenant w/ no credential /
    act call / assert ProblemError(402) unchanged + exactly 1 result="denied" row
  - test_audit_write_failure_never_blocks_credential_resolution: arrange a REAL failing
    session_factory (raises on open, not an unexercised AsyncMock) / act call / assert
    resolution succeeds unchanged + 0 rows persisted + fail-open warning IS logged (caplog)

  Part C (`apps/gateway/tests/superadmin_audit_foundation/test_part_c_oidc_login_audit.py`, DONE):
  - test_existing_superadmin_oidc_login_is_audited: arrange a real superadmin row + correct
    domain mapping / act OidcLoginUseCase.execute / assert JWT unchanged + exactly 1
    result="success" row, metadata=={"role":"superadmin","auth_method":"oidc"}
  - test_non_superadmin_oidc_login_not_audited: arrange (a) brand-new email, (b) existing
    owner / act both / assert both issue unchanged + 0 audit rows
  - test_oidc_rejections_never_reach_audit_hook: arrange (a) a REAL superadmin hit by a
    tenant-mapping conflict, (b) a claims-validation failure / act both / assert both raise
    unchanged + 0 audit rows — proves even an audit-worthy user produces nothing when
    rejected pre-issuance
  - test_audit_write_failure_never_blocks_oidc_login: arrange a REAL failing session_factory
    / act call / assert login succeeds unchanged + 0 rows persisted + warning IS logged

  Part B (`apps/gateway/tests/superadmin_audit_foundation/test_part_b_password_login_audit.py`, DONE):
  - test_superadmin_login_is_audited: arrange a real superadmin (direct-SQL + real Argon2
    hash, mirrors superadmin-login's own fixture) / act POST /admin/auth/login / assert 200
    + JWT unchanged + exactly 1 result="success" row, metadata=={"role":"superadmin",
    "auth_method":"password"}
  - test_non_superadmin_login_not_audited: POSITIVE CONTROL first (a superadmin login in the
    same test/schema must itself be audited, else the negative assertion below is vacuous) /
    act an ordinary owner signup+login / assert 200 unchanged + row count still exactly 1
    (only the control's row)
  - test_wrong_password_against_superadmin_email_not_audited: POSITIVE CONTROL first (a real
    successful login by the same superadmin email) / act a wrong-password attempt against
    that same email / assert 401 ERR_AUTH_INVALID_CREDENTIALS + row count still exactly 1 —
    proves the rejection path added no row (issuance never reached)
  - test_audit_write_failure_never_blocks_login: arrange a REAL failing session_factory via
    an `app.dependency_overrides[get_login_use_case]` override (real repository/hasher/
    tokens, only session_factory swapped — swapping app.state.sessionmaker wholesale would
    also break the login's own DB session, proving nothing about fail-open specifically) /
    act login / assert 200 unchanged + 0 rows persisted + fail-open warning IS logged
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/ops/api/deps.py` (Part A retrofit) ·
  `apps/gateway/src/gateway/tenants/application/use_cases.py` and/or
  `apps/gateway/src/gateway/tenants/api/router.py` (Part B — exact file(s) pinned against
  `superadmin-login`'s real frozen §3 at Build time, not guessed now) ·
  `apps/gateway/src/gateway/auth/application/use_cases.py` (Part C retrofit —
  `OidcLoginUseCase`) · `apps/gateway/src/gateway/auth/api/deps.py` (Part C DI wiring — one
  new kwarg at the sole `OidcLoginUseCase(...)` construction site) · `./tests/`
Strategy (ordered batches): 1. Part A first (fully specified, independently buildable): add
  `session_factory` param + the 3 audit-write branches to `resolve_platform_credential`, verbatim
  per §3's Python. 2. write + confirm red the 4 Part-A scenario tests. 3. Part C next (also fully
  specified, independently buildable, no sibling-task dependency): add `session_factory` param to
  `OidcLoginUseCase.__init__`, wire it at its one DI call site, add the post-issuance audit branch,
  verbatim per §3's Python. 4. write + confirm red the 4 Part-C scenario tests. 5. Part B last:
  re-read `superadmin-login`'s shipped code (by then built — orchestrator sequences login-first) to
  pin the exact call-site shape (candidate (a) or (b) per §3), wire the audit call. 6. write +
  confirm red the 4 Part-B scenario tests. 7. green all three parts; run the named regression
  suites (`test_audit_store.py`, `ops_platform_job_identity/`, `superadmin_login/`,
  `test_users_role.py`, plus the existing OIDC login suite covering `OidcLoginUseCase`).
Known-problem fixes: `AuditEvent.__post_init__`'s actor invariant (`tenant_id is not None` requires
  non-None `actor_user_id`) — all three new call sites' field choices are pre-verified in §1/§3 to
  satisfy it, but re-confirm at construction, not just at review. `record_audit` must be scheduled
  via `asyncio.ensure_future`, never bare-awaited on the request path (matches the 8 existing sites).
  Part C's `session_factory` must be threaded through as a REQUIRED kwarg at
  `get_oidc_use_case_with_config`, not defaulted to `None` — mirrors Part A's precedent of a
  required (not optional) parameter, so no future caller can silently skip the audit wiring.
Strategy actually used: [AI] ALL THREE PARTS DONE. Part A + Part C built first this pass
  (Part A fully implemented+green before Part C was started, not interleaved); Part B built
  last, in a separate pass, once `superadmin-login` shipped and confirmed `LoginUseCase`'s
  shape unchanged (that task made zero `src/` changes). Deviations found and handled, all
  flagged rather than silently resolved:
  (1) §2 has FIVE Part-A scenarios, not the "4 Part-A" this §5 originally estimated when the
  contract was widened — the extra is the audit-write-fail-open case, its own separately-
  load-bearing Must; built all 5, since §2 (not this paraphrase) is the authoritative source.
  (2) §3's Part C Python claimed `Role` was "already imported" in `use_cases.py` — false on
  direct read; added a real (non-TYPE_CHECKING) import, since it's used in a runtime `==`.
  (3) §3's Part C Python is syntactically invalid as literally transcribed — a required
  `session_factory` param cannot follow already-defaulted params without a bare `*` separator.
  Fixed by making it keyword-only-required (`*, session_factory: ...`) — preserves "required,
  not defaulted" and "last position" exactly as specified, valid Python, invisible to the sole
  call site (already all-keyword). Not a change-request: this corrects a transcription bug in
  the contract's illustrative code, not any Must/Reject/decision.
  (4) `tests/ops_platform_job_identity/`'s existing suite (4 test functions, 5 call sites)
  broke on Part A's new required parameter — expected fallout of retrofitting a shipped
  function's signature (per §1's own Must), fixed by threading a `session_factory` fixture
  through; zero assertions removed or weakened, confirmed by the orchestrator's own diff read.
  (5) Deliberately did NOT reuse `tests/audit/test_audit_store.py`'s `AsyncMock(spec=
  AsyncSession)`-with-`.execute()`-raising pattern for the two new fail-open tests: independently
  confirmed (by both the build agent and the orchestrator, reading `audit_repository.py` and
  `audit_writer.py` directly) that `record_audit`'s real path calls only `session.add()` (sync)
  and `await session.commit()` — never `.execute()` — so that existing pattern's configured
  failure is never actually triggered by the code under test; it is a LATENT, pre-existing
  vacuous-test risk in `audit-log-store`'s own already-shipped, already-gated suite, not
  something either of today's two tasks own or should silently patch. This task's own
  `failing_session_factory` instead makes the factory CALL ITSELF raise — directly inside
  `record_audit`'s `try` block — and both new tests assert via `caplog` that the fail-open
  warning is genuinely logged, a real, verified failure injection. See §7 Competency delta.
  (6) Part B's call-site shape (§3's Least-sure flag, PRIMARY): candidate (b) chosen —
  application-layer `session_factory` constructor dependency on `LoginUseCase` itself,
  mirroring Part A/C verbatim — over candidate (a) (router-layer post-issuance decode).
  Reasoning re-confirmed independently, not just accepted from the pre-declared lean: (i) now
  the established pattern at BOTH other call sites in this exact task; (ii) `get_login_use_case`
  only lacked a `request: Request` param to reach `request.app.state.sessionmaker` — the
  dominant, ubiquitous idiom already used by `get_oidc_use_case_with_config` and 5+ other
  DI providers in this codebase; (iii) `LoginUseCase.__init__` had zero defaulted params before
  this change, so `*, session_factory: ...` is a style choice for cross-call-site consistency
  with Part C, not a syntax necessity (unlike Part C's own analogous fix in deviation (3)).
  `execute()`'s direct `return self._tokens.issue(...)` was restructured into a captured-then-
  audit-checked-then-return shape, identical to Part C's own retrofit. Confirmed via
  `mcp__serena`: exactly 2 `LoginUseCase(` construction sites repo-wide (the DI provider + this
  task's own dependency-override test) — same single-call-site safety property as Part C.
  (7) A genuine cross-section drift inside the FROZEN bundle itself, found reading §1 against
  §2/§3 side by side: §1's own Must (line 71) requires Part B's audit row to carry
  `metadata={"role": "superadmin", "auth_method": "password"}`, but §2's Part-B scenario (line
  159) and §3's Part-B illustrative Python (line 324) both still show the pre-OIDC-widening
  `metadata={"role": "superadmin"}` — missing `auth_method`. Root cause: when Part C was added
  (widening this contract for OIDC coverage), §1's Must was proactively updated for both Part B
  and Part C to add the `auth_method` distinction (Part C's own Must, line 73, explicitly
  reasons why: "so a security review... can tell the two issuance mechanisms apart"), but §2/§3's
  Part-B text — not being actively rebuilt at that freeze moment — was never backfilled to match.
  Built to match §1's Must (the more specific, more recently-updated, and semantically-reasoned
  section), which is also symmetric with Part C's own fully-updated, already-shipped fields —
  confirmed correct by the pre-existing precedent, not a coin-flip. Not a change-request: no
  Must/Reject/decision changed, this is a within-freeze drafting inconsistency, same category as
  deviations (2)-(3) but a cross-section (not single-snippet) instance — see §7 Competency delta.
Safety rule (feature-specific): the audit write is fire-and-forget + fail-open, its own isolated DB
  session — reuses `record_audit`'s already-frozen contract verbatim (see §3 IO note); the primary
  action (login / credential resolution) is never gated on the audit write's outcome.
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 13/13 this task's own scenario tests green; all 5 named regression
  suites green (47/47, self-run); full-suite run: 2242 passed, 5 failed, 11 errored, 7
  skipped (635.69s) — every failure/error independently triaged and confirmed UNRELATED to
  this task (see note below), zero touch this task's files/symbols.
- [x] coverage did not decrease — full-suite run: 88.39% (repo gate is 80%); this task's own
  3 touched `src/` files individually inspected, no untested branch introduced.
- [x] no test or contract was altered during build — confirmed by `git status`: only new test
  files added under `tests/superadmin_audit_foundation/` and `tests/ops_platform_job_identity/`
  gained a fixture (Part A regression fix, not a weakened assertion); §1-§4 text unchanged
  since freeze (deviations recorded in §5, not silently folded into frozen text).
- [x] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP). See Refute-read verdict below: EARNED.
- [x] concurrency / timing of the risky operation is safe — all 3 audit branches fire strictly
  AFTER their primary action succeeds (`tokens.issue()` / `resolve_provider_credential`
  returning non-None), via `asyncio.ensure_future` (never blocking the request); the
  constant-time wrong-password check in `LoginUseCase.execute()` is unchanged — no role lookup
  was added ahead of it, confirmed by direct line-by-line read.
- [x] no exposed secrets, injection openings, or unexpected dependencies — all `metadata` dicts
  contain only role/auth_method/provider/tenant_id (no password/token material); zero new
  third-party dependencies; SQL in test fixtures uses parameterized `text()` queries only.
- [x] layering & dependencies follow CONVENTIONS.md — audit writes stay inside the
  application-layer use cases / the ops API dep function, never in `domain/` or `infrastructure/`;
  DI wiring stays in each module's own `api/deps.py`, matching the existing 8-call-site convention.
- [x] a person reviewed and approved the change — Tin Dang approved the §3 contract freeze
  (2026-07-03, "Freeze, but widen Part B to also cover the OIDC/SSO path"); build itself run
  under `autonomy: auto` with independent AI self-review substituting for a second human pass
  on the build diff specifically, consistent with this task's declared autonomy level.

Full-suite triage note (the 5 failed + 11 errored, all confirmed unrelated by direct re-run):
  `test_signup_creates_tenant_and_owner_atomically`, `test_signup_taken_email_rejected`,
  `test_retention_sweep.py::test_batched_delete`, `test_usage_metering.py::
  test_non_streaming_ledger_row_correct_decimal_cost` — all 4 PASS in isolation (re-run
  directly); full-suite-only failure is cross-test DB-state flakiness, a pre-existing pattern
  in this repo (see `gateway-health` milestone, which fixed a similar cross-suite Redis
  contamination issue). `test_guardrails_core_migration_column_exists` — genuinely fails, but
  for an unrelated reason: a hardcoded table allow-list not updated for `batch_jobs`/
  `batch_job_items`, tables from an unrelated in-flight migration; captured log output in that
  same test run shows `/admin/auth/login` itself returning 200 cleanly. All 11 `ERROR`s
  (`test_migrations.py` ×5, `platform_tenant_seed/` ×5, `superadmin_role/` ×1) are the SAME
  known, already-documented gotcha (see memory `shared-test-postgres-no-timeouts`):
  `tests/migrations/conftest.py`'s naive DB-name string-replace expects a DB named
  `gateway_migrations_test_<suffix>` to already exist for a custom-suffixed
  `GATEWAY_TEST_DATABASE_URL` — confirmed directly via `asyncpg.exceptions.InvalidCatalogNameError:
  database "gateway_migrations_test_audit_foundation_verify" does not exist` on isolated re-run.
  Zero relation to this task's 3 touched files.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] Every `resolve_platform_credential` call reaching platform-tenant logic writes exactly
  one `audit_events` row (success/error/denied) — confirmed by direct Postgres row-shape+count
  assertions in `test_part_a_platform_credential_audit.py`'s 5 tests.
- [x] A superadmin's successful `/admin/auth/login` writes exactly one `auth.superadmin_login`
  row with `auth_method="password"`; a non-superadmin or rejected attempt writes none —
  confirmed by `test_part_b_password_login_audit.py`'s 4 HTTP-level tests against real Postgres,
  each using a positive-control row to rule out a vacuously-passing negative assertion.
- [x] An existing superadmin's OIDC/SSO login writes an identical row with `auth_method="oidc"`;
  a brand-new/non-superadmin/rejected OIDC login writes none — confirmed by
  `test_part_c_oidc_login_audit.py`'s 4 tests, including a rejection test using a REAL
  superadmin (the strongest version — proves even an audit-worthy user produces nothing when
  rejected pre-issuance).
- [x] An audit-DB failure never blocks any of the 3 primary actions — confirmed by 3 dedicated
  fail-open tests (one per part), each using a REAL raising `session_factory` (not an
  unexercised mock) + a `caplog` assertion that `record_audit`'s existing warning fires.
- [x] Zero lines changed under `gateway/audit/**` (`AuditEvent`/`record_audit`/`AuditRepository`/
  `AuditEventRow`/migrations) — confirmed by `git status`/`git diff` scope: only
  `ops/api/deps.py`, `tenants/application/use_cases.py`, `tenants/api/deps.py`,
  `auth/application/use_cases.py`, `auth/api/deps.py`, and 4 new/modified test files touched.
- [x] All pre-existing consumers stay byte-identical (JWT shape, error codes 500/402/401, OIDC
  rejections) — confirmed by re-running `superadmin_login/`, `test_users_role.py`,
  `ops_platform_job_identity/`, `test_audit_store.py` (47/47 green) plus a full-suite run.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced: `session_factory` is read at its one
  respective call site in all 3 parts; `get_login_use_case`/`get_oidc_use_case_with_config`
  both now pass `request.app.state.sessionmaker`. Confirmed via `mcp__serena`/`grep`: exactly 2
  `LoginUseCase(` construction sites repo-wide (the DI provider + this task's own
  dependency-override test), exactly 1 `OidcLoginUseCase(` site, exactly 1
  `resolve_platform_credential` audit-branch set — no orphaned call path.
- [x] DEAD-CODE (code) — no orphaned symbol: all 3 audit branches sit on primary
  success/error/denied paths already exercised by existing callers (the router, the
  not-yet-wired ops job seam); `ruff check` clean (0 findings) on all touched `src/` files,
  confirming no unused import survived the 3 retrofits.
- [x] SEMANTIC (prose / non-code) — read in full, not skimmed: all 3 source diffs
  (`ops/api/deps.py`; `tenants/application/use_cases.py` + `tenants/api/deps.py`;
  `auth/application/use_cases.py` + `auth/api/deps.py`) read directly by the orchestrator
  against the actual current file content (not just the diff/build-agent's self-report); all 3
  test files (13 tests) read in full; §1/§2/§3's frozen text cross-checked line-by-line against
  the built code, which surfaced the genuine §1-vs-§2/§3 metadata drift recorded as §5
  deviation (7) rather than assumed consistent.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: self (orchestrator manual review, independent of both build agents' self-reports) ·
  adversarially checked: (1) re-read all 3 source diffs directly against current file content,
  not the agents' diff summaries — caught the reformatted-diff hunk-header ambiguity for Part B
  and resolved it by reading the real file; (2) re-ran the 5 named regression suites myself
  (47/47 green) rather than trust either build agent's own unfinished/partial suite run — Part
  B's own background full-suite job never reported back before the agent exited; (3) grepped
  `LoginUseCase(`/`OidcLoginUseCase(` repo-wide myself to independently confirm the
  single-call-site safety claim, not just accept it; (4) directly re-verified the
  AsyncMock-vacuous-test claim about `test_audit_store.py` by reading `audit_repository.py`/
  `audit_writer.py` myself in the prior build pass; (5) manually traced `LoginUseCase.execute()`
  line-by-line to confirm the audit branch is structurally unreachable from the
  `InvalidCredentialsError` raise path (no role lookup precedes the password check); (6) checked
  the Part-B test file for the vacuous-negative-assertion trap specifically — confirmed both
  negative tests use a positive control, not a bare "0 rows" assertion; (7) cross-read §1 Must
  against §2/§3's literal text for all three parts, not just Part B — found and recorded the
  metadata drift (deviation (7)) rather than assuming the frozen bundle was internally
  consistent. No cheat found; the green is earned.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (contract freeze, §3, "Freeze, but widen Part B to also cover the
  OIDC/SSO path") + AI self-review (orchestrator, this Verify — independent of both build
  agents' self-reports) · date: 2026-07-03

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: [AI] ALL THREE PARTS DONE. Part A + Part C built first this pass
- [AI] verify — gate PASS (reviewed by Tin Dang (contract freeze, §3, "Freeze, but widen Part B to also cover the)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] `apps/gateway/tests/audit/test_audit_store.py::test_audit_write_fail_open`
  (a DIFFERENT, already-closed `audit-log-store` milestone's own suite, not owned by this
  task or `platform-identity`) configures `AsyncMock(spec=AsyncSession).execute` to raise,
  but `record_audit`'s real write path never calls `.execute()` (only `session.add()` +
  `session.commit()`) — the test currently passes regardless of whether the fail-open
  try/except exists at all. A future task touching `audit-log-store` should strengthen it
  to match this task's own `failing_session_factory` pattern (evidence: independently
  confirmed by reading `audit_repository.py`/`audit_writer.py` directly, not just the build
  agent's claim; see §5 "Strategy actually used" (5)).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [TDD · open] an `AsyncMock(spec=SomeClass)` with only ONE method configured to raise is a
  silent trap if the code under test doesn't actually call that specific method on that
  specific failure path — it looks like a real failure-injection test and passes, but proves
  nothing. Prefer making the FIRST thing the code under test calls raise directly (here: the
  `session_factory()` call itself, matching the scenario's own wording) over mocking deep
  inside an object whose exact call pattern you have to keep re-verifying (evidence: found by
  independently re-deriving `record_audit`'s real call sequence before trusting the existing
  suite's pattern — see the Spec delta above).
- [SDD · open] a §3 CONTRACT's illustrative Python is not automatically valid Python — this
  task's own Part C snippet had a required param placed after already-defaulted ones with no
  `*` separator (a straightforward syntax error), and a false "already imported" note for
  `Role`. Neither was semantic (no Must/Reject changed), both were still worth a real syntax/
  import sanity pass before freezing a contract's code block, not just a shape/decision review
  (evidence: §5 "Strategy actually used" (2)-(3); mirrors `superadmin-login`'s own SDD delta
  about a prose path string drifting from its §0 anchor — the same underlying lesson, contract
  prose/code needs the same rigor as contract decisions).
- [TDD · open] when a scenario can only be driven through an HTTP/router round-trip (not direct
  use-case construction), a negative assertion like "0 audit rows" is vacuous pre-build for a
  different reason than the AsyncMock trap above: the feature being entirely absent ALSO
  produces 0 rows, so the test can pass for the wrong reason at every stage, not just RED. Fix:
  a POSITIVE CONTROL inside the same test — a genuine audit-worthy action in the same
  test/schema that must itself produce a row before the negative assertion is trusted. Applied
  in `test_part_b_password_login_audit.py`'s two negative tests (evidence: the build agent's
  own module docstring names this explicitly; independently confirmed by the orchestrator
  reading the test file — both tests open with a control block that itself asserts count==1
  before proceeding to the real scenario).
- [SDD · open] a contract-widening pass (adding Part C mid-freeze-cycle) can update one section's
  rule (§1 Must) for consistency across ALL affected parts, while leaving a SIBLING section's
  illustrative text (§2 scenario prose, §3 Python) stale for the part NOT being actively
  rebuilt in that pass — a distinct failure mode from deviation (2)-(3)'s single-snippet syntax
  error above: this is cross-section drift within one freeze, invisible unless §1 is read
  against §2/§3 side-by-side for every part, not just the part being changed. Found here: §1's
  Must for Part B silently gained an `auth_method` field when Part C was added, but §2/§3's own
  Part-B text did not (evidence: §5 "Strategy actually used" (6)). Suggests a freeze-time
  checklist item: when widening a contract for one part, diff-check whether the widening
  implies a change to any OTHER already-drafted part's text too, not just the part being added.
