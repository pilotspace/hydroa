# TASK: Agent-as-principal on device-OAuth: named identities, per-agent budgets/limits, universal kill switch

slug: agent-identity-governance · created: 2026-07-14 · stage: production · sensitivity: security
milestone: agent-gateway-v1
autonomy: auto   <!-- level: manual < conservative < auto — lower for a high-risk task (`add.py autonomy set`). Multi-component repo? add a `component: <name>` line (.add/components.toml) to join that root to §5 Scope. -->
phase: contract   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining? declare `risk: high` on the slug line + a lowered autonomy — the engine refuses an unguarded completion (`unguarded_high_risk_auto`). A comment is never a declaration. -->

> One file = one task — fill top-to-bottom; the phase marker above is the single source of truth (`add.py phase`); unclear phase → its book chapter.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/agent_oauth/domain/entities.py:AgentToken` — has a `revoked_at` field but NOTHING writes it today.
- `apps/gateway/src/gateway/agent_oauth/domain/ports.py:AgentOAuthRepository` — Protocol has `create_pending/get_by_device_code_hash/get_by_user_code_hash/approve/deny/mint_token/resolve_access_token`; **no revoke method exists at any layer**.
- `apps/gateway/src/gateway/agent_oauth/infrastructure/repository.py:SqlAlchemyAgentOAuthRepository.resolve_access_token` — the fail-closed read (`revoked_at is not None or access_expires_at <= now` → `None`); this task's kill switch works BY WRITING to the column this method already reads.
- `apps/gateway/src/gateway/agent_oauth/infrastructure/orm.py:AgentTokenRow` / `DeviceAuthorizationRow` — `agent_tokens` (unique `authorization_id`, `access_token_hash`) and `device_authorizations` tables; both timestamptz.
- `apps/gateway/src/gateway/proxy/infrastructure/composite_key_authenticator.py:CompositeKeyAuthenticator.authenticate` — Contract FROZEN @ v39 (`agent-token-authn-seam` TASK.md §3). Dispatches `sk-` → API-key path, else → SHA-256 hash → `resolve_access_token` → `AuthzResult(monthly_budget_usd=settings.agent_oauth_default_budget_usd)`. **This is the ONE shared credential-resolution call both authn seams use** — no duplicate logic to keep in sync.
- `apps/gateway/src/gateway/keys/api/router.py:authz` / `authz_subpath` (`POST /internal/authz`, `POST /internal/authz/{_subpath:path}`) — the Envoy ext_authz HTTP-check endpoint; byte-identical 401 (`ERR_AUTH_INVALID_KEY`/`AUTH_KEY_INVALID_AUTHZ`) for every failure mode.
- `apps/gateway/src/gateway/keys/api/deps.py:get_authz_authenticator` — builds the SAME `CompositeKeyAuthenticator` instance shape used by `/v1` in-process (`apps/gateway/src/gateway/proxy/api/deps.py` line ~124) — confirms both seams share one code path, not two independently-maintained ones.
- `infra/envoy/envoy.yaml:115-144` — `envoy.filters.http.ext_authz`, `failure_mode_allow: false`, **no `cache_duration`/TTL wired** — every `/v1/*` request triggers a fresh HTTP call to `/internal/authz`, which itself does a live, uncached DB read via `resolve_access_token`.
- `apps/gateway/src/gateway/keys/domain/entities.py:AuthzResult` — the ONE governance-fields carrier; already has `monthly_budget_usd`, `soft_budget_usd`, `rpm_limit`, `tpm_limit`, `model_allowlist`, `expires_at`, and — the load-bearing precedent — `team_id`/`team_budget_usd` as a SEPARATE additive aggregation dimension alongside the per-key one.
- `apps/gateway/src/gateway/proxy/application/governance.py:GovernanceService.authorize` / `_check_per_key_budget` / `_check_team_budget` (≈ lines 134-430) — the ONE choke point that reads `AuthzResult`'s governance fields. `_check_per_key_budget` keys its Redis spend counter on `usage:spend:key:{authz.key_id}:{YYYYMM}`; `_check_team_budget` keys on `usage:spend:team:{team_id}:{YYYYMM}` — a SECOND, independent counter checked in the SAME pass. RPM/TPM (`rate_limiter.check_rpm/check_tpm`) are keyed ONLY on `authz.key_id` — no team-level (or any aggregate) analog exists for those two.
- `apps/gateway/src/gateway/keys/infrastructure/repository.py:SqlAlchemyApiKeyRepository.revoke` — the existing single-key revoke pattern: `UPDATE ... WHERE id=key_id AND tenant_id=tenant_id .values(revoked_at=func.now()) RETURNING id`; unknown-id and cross-tenant-id both return `False` → identical 404. This is the bulk-write pattern the kill switch's principal-wide revoke mirrors (WHERE `principal_id = :id` instead of a single `id`).
- `apps/gateway/src/gateway/keys/application/use_cases.py:RevokeKeyUseCase.execute` — RBAC precedent for a destructive per-tenant admin action: forbids ONLY `Role.MEMBER` (every other role, including VIEWER/OPERATOR, may revoke a key today).
- `apps/gateway/src/gateway/tenants/domain/entities.py:Role` (StrEnum: OWNER, ADMIN, OPERATOR, BILLING_ADMIN, VIEWER, MEMBER, SUPERADMIN).
- `apps/gateway/src/gateway/tenants/infrastructure/impersonation_session_guard.py:DbImpersonationSessionGuard.ensure_live` — fail-**CLOSED** per-request live revocation check (`revoked_at IS NULL AND expires_at > now()`, explicitly diverging from the Redis-guard fail-open convention because "this is a credential-revocation decision, not an availability gate"). Confirms this codebase's established convention: credential/session revocation is ALWAYS re-checked live, per request, never cached — and there is NO existing precedent anywhere (including the realtime-relay WS session) for re-checking authorization mid-stream after admission.
- `apps/gateway/src/gateway/audit/domain/audit_event.py:AuditEvent` + `apps/gateway/src/gateway/audit/application/audit_writer.py:record_audit` — fire-and-forget, separate-session, fail-open audit write (a write failure never changes the HTTP outcome); `actor_key_id`/`actor_scim_token_id` are the precedent for widening `AuditEvent` to a non-human actor without breaking `audit_missing_actor`.
- `apps/gateway/src/gateway/core/error_catalog.py:ErrorSpec` / `AUTH_FORBIDDEN` (`403 ERR_AUTH_FORBIDDEN "Insufficient role for this operation"`) — the existing generic role-ceiling error this task reuses rather than minting a new code.
- `apps/gateway/src/gateway/agent_oauth/api/token_router.py` / `device_authorize_router.py` / `device_approval_router.py` — the 3 existing v39 endpoints (mint / start-device-flow / approve-deny); none are principal-aware; none of their frozen contracts are touched by this task.

Context (working folder): `.add/milestones/agent-gateway-v1/MILESTONE.md` (owning milestone; shared decisions + this task's owned contract: "agent principal model"); v39 tasks `agent-oauth-grant-store`, `device-authorization-endpoint`, `device-approval-flow`, `agent-token-authn-seam` (all FROZEN — this task extends additively, edits none of them); `/Users/tindang/workspaces/tind-repo/ai-proxy/tmp/r1-design-context.md` (shared wave-1 design rules).

Honors:
- Additive-nullable-column convention: every governance field added since v27+ (`team_id`, `cache_enabled`, `guardrail_configs`, `batch_grouping_enabled`, `policy_source`) is nullable/defaulted so every pre-existing row and call site stays byte-identical — the new `agent_tokens.principal_id` FK and `AuthzResult.agent_principal_*` fields follow the same rule.
- Two DELIBERATELY different fail modes coexist in this codebase: fail-**open** for availability gates (Redis budget/rate-limit counters, RFC 8628 slow_down probe) vs fail-**closed** for credential/session revocation (`resolve_access_token`, `DbImpersonationSessionGuard`). The kill switch is squarely the latter category.
- tenant_id-scoped WHERE clause in the SAME query as the existence check — never a separate lookup then a scope check (`RevokeKeyUseCase`/`SqlAlchemyApiKeyRepository.revoke`) — closes the enumeration oracle; unknown-id and cross-tenant-id are byte-identical.
- Plaintext secret/token discipline (device_code/user_code/access_token never logged) extends to any principal-admin surface that references a token by id only, never by raw value.
- All new error responses go through `ErrorSpec`/`ProblemError` in `core/error_catalog.py` — no ad hoc raises.

Seams consulted: none in `.add/SEAMS.md` apply (checked; file does not name an agent-identity or governance-composition seam yet).

Anchors the contract cites: `AgentTokenRow` (+ new `principal_id` FK), a new `agent_principals` ORM row, `AgentOAuthRepository` (+ new revoke methods), `AuthzResult` (+ new `agent_principal_id`/`agent_principal_budget_usd` fields), `CompositeKeyAuthenticator.authenticate`, `GovernanceService._check_team_budget` (the method the new `_check_agent_principal_budget` mirrors), `SqlAlchemyApiKeyRepository.revoke` (the bulk-UPDATE pattern), `AUTH_FORBIDDEN`, `record_audit`/`AuditEvent`.

Issues/Risks (→ feed §1):
1. **No revoke path exists today** — `AgentTokenRow.revoked_at` is read (fail-closed) but never written anywhere in the codebase. This task must add the write path from scratch; it cannot "wire up" an existing partial mechanism.
2. **Per-agent budgets are currently a single tenant-wide flat default** (`settings.agent_oauth_default_budget_usd`, identical for every agent token) — there is no per-principal override today. The milestone's "per-agent budgets" exit criterion is not achievable without this task's schema + wiring change.
3. **Budget-attachment bypass risk (named threat)**: `_check_per_key_budget`/RPM/TPM enforcement key on `authz.key_id` (= the individual token). A principal that owns N attached tokens could present each token separately and receive N× its intended ceiling unless an ADDITIONAL principal-level aggregate dimension is enforced — mirroring the one precedent that exists (`team_budget_usd`). RPM/TPM have NO existing aggregate-dimension precedent at all (only budget does), so a principal-level RPM/TPM aggregate would be new territory, not a mirror of proven code — flagged in §1 as a scoping decision, not silently built.
4. **No mid-flight re-authorization precedent anywhere in this codebase** (confirmed by reading the realtime-relay WS pump and the impersonation-session guard) — admission is checked once, at the start of a request/session, never again. This directly shapes the kill-switch's in-flight-stream semantics (§1 M10): a kill can only affect NEW admissions, not sessions already past the gate. This is a structural fact about the codebase, not a design shortcut invented for this task.
5. **No cache sits between either authn seam and the DB** — confirmed by reading `envoy.yaml`'s `ext_authz` block (no `cache_duration`) and `composite_key_authenticator.py`/`key_authenticator.py` (no Redis-fronted authn cache). This means revocation propagation has NO cache-TTL to reason about — it is bounded only by transaction-commit visibility (effectively immediate) plus the in-flight-admission caveat above.
6. **Device authorizations are not principal-aware** — a principal can only be attached to an ALREADY-MINTED `AgentToken` (post-hoc grouping via an explicit admin action), never to a pending/approved-but-unminted `DeviceAuthorization`. This closes off a race (attach-then-kill vs. an in-flight device-code poll) by construction: the token to attach doesn't exist yet during that race window.
7. **No RBAC precedent pins the exact ceiling for this new class of action** — the closest analog (`RevokeKeyUseCase`) forbids only `Role.MEMBER`. A kill switch's blast radius (every session of a named agent, tenant-wide) reads as materially more destructive than revoking one key; I have chosen a narrower {OWNER, ADMIN} ceiling below and flagged it ⚠ as the lowest-confidence call in this bundle.

Related intent: MILESTONE.md exit criterion — "A tenant admin kills a named agent and every one of its sessions/tokens stops authenticating at both authn seams" ← this task. MILESTONE.md "Shared / risky contracts" — "agent principal model (identity fields, budget/limit attachment, kill-switch semantics) -> owning task `agent-identity-governance`" ← this task's §3 IS that owning contract. MILESTONE.md Glossary delta "agent principal" ← defined below. Shared decision "Agent principal rides device-OAuth — extends the RFC 8628 grant store (v39), no new credential class; kill switch = revocation of all grants/sessions for the principal, fail-closed at both authn seams" ← the exact shape this task implements.

Ground SHA: `c948576` (branch `feat/agent-gateway-r1`)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Agent-as-principal identity on device-OAuth — named agent principals, per-agent budget/RPM/TPM attachment through the existing governance gates, and a universal kill switch across both authn seams.

Framings weighed: **Principal-as-a-grouping-layer** (chosen) — a new tenant-scoped `agent_principals` row that an admin explicitly attaches to one or more ALREADY-MINTED `agent_tokens` rows (nullable `principal_id` FK, purely additive); governance reads a principal-level aggregate (budget now, RPM/TPM explicitly deferred — see ⚠ below) alongside the untouched per-token checks, mirroring `team_budget_usd`'s existing dual-check idiom exactly. Chosen because it requires ZERO edits to the frozen v39 device-OAuth contract, is fully backward compatible (an unattached token behaves exactly as it does today), and reuses the ONE governance choke point instead of building a parallel one. · **Fold principal into the grant itself** (alternative, rejected — a token/authorization is inherently 1:1 with a single device-OAuth grant; the milestone explicitly requires "≥1 device-OAuth grants" per principal, which a 1:1 fold cannot express without editing the v39-frozen contract). · **A parallel authn/governance/billing path for agent principals** (alternative, rejected — violates this milestone's own shared decisions verbatim: "ONE billing path," "agent principal rides device-OAuth, no new credential class").

Must:
<must>
  - M1: An {OWNER, ADMIN} can create a named `agent_principal` (name, optional owner_user_id, optional monthly_budget_usd/rpm_limit/tpm_limit), tenant-scoped to the caller's own tenant.
  - M2: An {OWNER, ADMIN} can attach an EXISTING, already-minted `agent_tokens` row belonging to their OWN tenant to a principal (nullable `principal_id` FK write); a token may be attached to at most one principal at a time.
  - M3: An {OWNER, ADMIN} can detach a token from its principal (sets `principal_id` back to NULL; the token itself is untouched and keeps authenticating under the tenant-wide default, exactly as an unattached v39 token does today).
  - M4: A principal's configured `monthly_budget_usd` is enforced as an ADDITIONAL aggregate dimension in `GovernanceService.authorize`, summed across every token currently attached to it — mirroring `_check_team_budget`'s existing dual-check idiom (Redis counter key `usage:spend:agent_principal:{principal_id}:{YYYYMM}`) — never replacing the existing per-token `monthly_budget_usd` check.
  - M5: Any authenticated tenant role can list the tenant's agent principals (name, owner_user_id, created_at, last_seen_at, killed_at, monthly_budget_usd/rpm_limit/tpm_limit, attached-token count) — this is the read-API seam the sibling `agents-console` task's directory view consumes.
  - M6: A principal's `last_seen_at` updates on a successful `/oauth/token` mint tied to one of its attached tokens, via a fire-and-forget write — NEVER a synchronous write on the `/v1` data-plane hot path (mirrors `record_audit`'s fire-and-forget-separate-session shape).
  - M7: An {OWNER, ADMIN} can KILL a principal in ONE action: every attached `agent_tokens` row is revoked (`revoked_at = now()`) via a single atomic tenant-scoped bulk UPDATE, and the principal itself is marked `killed_at = now()` in the SAME transaction.
  - M8: Killing an already-killed principal is idempotent — a 200 no-op (see R9), never an error, and never re-revokes tokens attached AFTER the first kill (a token attached post-kill to an already-killed principal is rejected — see R10).
  - M9: A killed principal's every attached token stops authenticating at BOTH seams (in-process `/v1` `CompositeKeyAuthenticator` + Envoy `ext_authz` → `POST /internal/authz`) starting with the very next request evaluated at either seam — achieved BY CONSTRUCTION (both seams share the one uncached `resolve_access_token` call), not by a new propagation mechanism.
  - M10: A kill does NOT force-terminate a request/stream ALREADY past admission (e.g. a live SSE completion or an open realtime-relay WS session) — a disclosed limitation consistent with this codebase's existing admission-time-only governance convention, not a regression this task introduces.
  - M11: The kill action is audited — one `AuditEvent` (`action="agent_principal.killed"`, tenant_id, actor_user_id, target_type="agent_principal", target_id=principal id) via the existing fire-and-forget `record_audit`, scheduled AFTER the kill's own transaction commits and never blocking the HTTP response.
  - M12: create/attach/detach/kill are fail-closed and tenant-scoped: an unknown id, OR an id belonging to another tenant, returns the IDENTICAL 404 in the same query as the existence check (mirrors `SqlAlchemyApiKeyRepository.revoke`) — no enumeration signal.
</must>

Reject:
<reject>
  - Missing/invalid bearer session JWT on any admin-agents endpoint -> "unauthorized" (401, `AUTH_TOKEN_INVALID` shape)
  - Caller's role is outside {OWNER, ADMIN} on create/attach/detach/kill -> "forbidden" (403, reuses `AUTH_FORBIDDEN`)
  - Attach targeting an `agent_tokens` id that does not exist, or belongs to another tenant -> "agent_token_not_found" (404 — identical for both cases)
  - Attach targeting an `agent_tokens` row already attached to a DIFFERENT principal -> "agent_token_already_attached" (409)
  - Attach/detach/kill targeting a principal id that does not exist, or belongs to another tenant -> "agent_principal_not_found" (404 — identical for both cases)
  - Create with a name already used by another principal in the SAME tenant -> "agent_principal_name_conflict" (409)
  - Create/update with a negative or non-numeric budget/rpm_limit/tpm_limit -> "invalid_request" (422)
  - A killed principal's token presented at EITHER authn seam -> byte-identical 401 `ERR_AUTH_INVALID_TOKEN` / `ERR_AUTH_INVALID_KEY` — the SAME code as any other unknown/expired/revoked credential (no signal distinguishing "killed" from merely expired)
  - Re-killing an already-killed principal -> 200 idempotent no-op, NOT an error (a defined success path, listed here because it is a boundary the test suite must cover — see M8)
  - Attaching a NEW token to an already-killed principal -> "agent_principal_killed" (409) — a killed principal cannot be "resurrected" by attaching a fresh token to it; the tenant must create a new principal
</reject>

After:
<after>
  - A named `agent_principal` row exists, tenant-scoped, with 0..N attached `agent_tokens` rows, a monthly budget aggregate cap, and `created_at`/`last_seen_at`/`killed_at` timestamps.
  - `GovernanceService.authorize` enforces the principal's aggregate `monthly_budget_usd` as an ADDITIONAL dimension whenever the resolved `AuthzResult` carries a non-null `agent_principal_id` — the existing per-token budget check is untouched.
  - Every token attached to a killed principal fails `resolve_access_token` (its own `revoked_at` is set) — both the `/v1` in-process seam and the Envoy `ext_authz` seam reject it identically, on the very next request, with no separate propagation step to wait on.
  - One `AuditEvent` records the kill, written independently of the kill's own DB transaction (a fire-and-forget failure never rolls back or blocks the kill).
  - An unattached (pre-existing v39) agent token continues to authenticate and bill exactly as it does today — this task changes nothing about the default path.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ RBAC ceiling for principal admin actions ({OWNER, ADMIN} only — NOT the wider "anyone but MEMBER" ceiling `RevokeKeyUseCase` uses for single-key revocation) — lowest confidence because the only concrete precedent I found in the real code (`RevokeKeyUseCase.execute`) forbids ONLY `Role.MEMBER`, a materially wider allow-list than what I've proposed here, and I have no frozen RBAC matrix document that pins this EXACT new capability. I chose the narrower ceiling because a kill switch's blast radius (every session of a named agent, tenant-wide, in one action) reads as categorically more destructive than revoking one API key. If wrong: either every downstream build/verify re-litigates this as a change request back to SPECIFY, or — the worse direction — it ships too permissive and an OPERATOR/VIEWER can kill agents they should not be able to touch.
  - [ ] Principal-level RPM/TPM aggregate enforcement is explicitly DEFERRED out of this task (M4 covers only the aggregate USD budget, mirroring the one precedent that exists, `team_budget_usd`); RPM/TPM stay per-token-only, meaning a principal with N attached tokens still gets an effective N× RPM/TPM ceiling versus its single-token limit. This is a DISCLOSED, not a silently-dropped, gap — confirm whether this is acceptable for the v1 freeze or must be closed now before it ships as a known governance gap.
  - [ ] `last_seen_at` updates on token MINT only (an `/oauth/token` poll success), never on a `/v1` data-plane call — confirm this satisfies the agents-console directory's "last-seen" expectation, versus wanting true last-API-call recency (which would require a write on the hottest path in the gateway, deliberately avoided here).
  - [ ] A principal name is unique per tenant (R: `agent_principal_name_conflict`) — reasonable for a human-facing directory key but not explicitly stated in MILESTONE.md; confirm before freeze.
</assumptions>

<!-- EXIT: every rule + rejection stated; assumptions ranked lowest-confidence first, top 1–2 ⚠-flagged with why + cost (or an honest "none material" naming the biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Owner creates a named agent principal   # M1
  Given an authenticated OWNER with no existing principal named "billing-bot" in their tenant
  When they POST /admin/agents {"name": "billing-bot", "monthly_budget_usd": "50.00"}
  Then a 200 response returns the new principal with created_at set, last_seen_at null, killed_at null, attached_token_count 0

Scenario: Admin attaches an existing agent token to a principal   # M2
  Given an approved and minted agent_tokens row owned by the caller's tenant, and an unattached principal
  When an ADMIN POSTs /admin/agents/{principal_id}/tokens/{token_id}/attach
  Then a 200 response returns {principal_id, token_id, attached_at} and the token's principal_id is set

Scenario: Admin detaches a token, which reverts to tenant-wide default governance   # M3
  Given a token currently attached to a principal
  When an ADMIN DELETEs /admin/agents/{principal_id}/tokens/{token_id}
  Then a 200 response confirms detached_at, the token's principal_id is NULL, and its next request is governed only by the tenant-wide default budget (byte-identical to an unattached v39 token)

Scenario: Principal aggregate budget blocks a request once its combined spend crosses the cap   # M4
  Given a principal with monthly_budget_usd=10.00 and two attached tokens whose combined recorded spend for the month is >= 10.00
  When either attached token calls the data plane
  Then GovernanceService.authorize raises BUDGET_EXCEEDED (402) even though NEITHER token individually exceeds its own per-token budget
  And the existing per-token budget check result is unaffected (still evaluated, still passes on its own terms)

Scenario: Any tenant role lists agent principals   # M5
  Given a tenant with 2 agent principals, one killed
  When a VIEWER GETs /admin/agents
  Then a 200 response lists both, each carrying killed_at (null for the live one, a timestamp for the killed one) and attached_token_count

Scenario: last_seen_at updates on token mint, not on data-plane calls   # M6
  Given a principal with an attached, not-yet-polled device authorization
  When the agent successfully polls POST /oauth/token and mints its access token
  Then the principal's last_seen_at is set (fire-and-forget, eventually visible)
  And a subsequent /v1/chat/completions call using that token does NOT update last_seen_at again

Scenario: Owner kills a principal, revoking every attached token in one action   # M7
  Given a principal with 3 attached agent_tokens, none previously revoked
  When an OWNER POSTs /admin/agents/{principal_id}/kill
  Then a 200 response returns {id, killed_at}
  And all 3 attached agent_tokens rows now have revoked_at set (single atomic UPDATE)
  And the principal's own killed_at is set in the same transaction

Scenario: Re-killing an already-killed principal is an idempotent no-op   # M8, R9
  Given a principal already killed at time T1
  When an OWNER POSTs /admin/agents/{principal_id}/kill again at T2
  Then a 200 response returns the ORIGINAL killed_at (T1), not a new timestamp
  And no attached token's revoked_at changes (they were already revoked at T1)

Scenario: A killed principal's token is rejected identically at both authn seams   # M9
  Given a principal killed 1 second ago with one attached token
  When that token is presented as a Bearer credential to the in-process /v1 CompositeKeyAuthenticator
  And separately presented via Envoy's ext_authz call to POST /internal/authz
  Then BOTH seams return a byte-identical 401 (ERR_AUTH_INVALID_TOKEN / ERR_AUTH_INVALID_KEY) with no retry or cache-clearing step required

Scenario: Kill does not abort an already-admitted in-flight stream   # M10
  Given an agent token that was validated and admitted for a long-lived SSE chat completion BEFORE the kill
  When the owning principal is killed WHILE that SSE stream is still open
  Then the in-flight stream continues uninterrupted to its natural end
  And the SAME token's very NEXT NEW request (a fresh admission) is rejected 401

Scenario: A kill is audited independently of its own transaction   # M11
  Given a principal being killed by an ADMIN
  When the kill's DB transaction commits
  Then a fire-and-forget AuditEvent (action="agent_principal.killed") is recorded in a separate session
  And a simulated audit-write failure does NOT change the kill's own 200 response (fail-open audit, fail-closed kill)

Scenario: Cross-tenant kill attempt is indistinguishable from unknown-id   # M12, R:agent_principal_not_found
  Given a principal belonging to tenant A
  When an authenticated OWNER of tenant B attempts to kill it by id
  Then a 404 "agent_principal_not_found" is returned — the SAME response as killing a random nonexistent UUID

Scenario: Missing bearer token on any admin-agents call   # R:unauthorized
  Given no Authorization header
  When a request hits POST /admin/agents
  Then a 401 "unauthorized" is returned

Scenario: Insufficient role attempts create/attach/detach/kill   # R:forbidden
  Given an authenticated VIEWER
  When they POST /admin/agents or POST /admin/agents/{id}/kill
  Then a 403 "forbidden" is returned and no row is created/modified

Scenario: Attach targets an unknown or cross-tenant token id   # R:agent_token_not_found
  Given a token id that either does not exist or belongs to a different tenant
  When an ADMIN attempts to attach it to one of their own principals
  Then a 404 "agent_token_not_found" is returned — identical for both cases

Scenario: Attach targets a token already attached elsewhere   # R:agent_token_already_attached
  Given a token already attached to principal A
  When an ADMIN attempts to attach the SAME token to principal B
  Then a 409 "agent_token_already_attached" is returned and the token stays attached to A

Scenario: Create with a duplicate name in the same tenant   # R:agent_principal_name_conflict
  Given a tenant already has a principal named "billing-bot"
  When an OWNER attempts to create another principal named "billing-bot" in the SAME tenant
  Then a 409 "agent_principal_name_conflict" is returned and no new row is created

Scenario: Create/update with an invalid budget value   # R:invalid_request
  Given a create request with monthly_budget_usd = "-5.00"
  When it is submitted
  Then a 422 "invalid_request" is returned and no row is created

Scenario: Attaching a fresh token to an already-killed principal is refused   # R:agent_principal_killed
  Given a principal killed 1 hour ago
  When an ADMIN attempts to attach a brand-new, never-before-attached agent token to it
  Then a 409 "agent_principal_killed" is returned and the token's principal_id stays NULL

Scenario: Two concurrent kill requests for the same principal race safely   # edge case — concurrency
  Given a live principal with 2 attached tokens and two admins simultaneously POST /admin/agents/{id}/kill
  When both requests reach the conditional UPDATE at nearly the same instant
  Then exactly ONE request's UPDATE affects a row (wins the race), revokes both attached tokens, and schedules exactly one AuditEvent
  And the OTHER request's UPDATE affects zero rows, returns the SAME killed_at as the winner, and schedules no second audit event or token re-revoke

Scenario: A kill racing an in-flight attach cannot attach a token to a just-killed principal   # edge case — concurrency
  Given a principal mid-flight between "live" and "killed" (its kill transaction is committing)
  When a concurrent attach request for a brand-new token targets the SAME principal
  Then the attach's conditional UPDATE either succeeds (attach committed strictly before the kill) and the token is revoked a moment later by M9's next-request check, OR fails with "agent_principal_killed" (kill committed first) — in neither ordering does a token end up attached to a killed principal while still authenticating
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /admin/agents   body: { name: str, owner_user_id?: uuid, monthly_budget_usd?: decimal, rpm_limit?: int, tpm_limit?: int }
  200 -> { id, tenant_id, name, owner_user_id, monthly_budget_usd, rpm_limit, tpm_limit,
           created_at, last_seen_at: null, killed_at: null, attached_token_count: 0 }
  401 -> { error: "unauthorized" }
  403 -> { error: "forbidden" }
  409 -> { error: "agent_principal_name_conflict" }
  422 -> { error: "invalid_request" }

GET /admin/agents
  200 -> { agents: [ { id, tenant_id, name, owner_user_id, monthly_budget_usd, rpm_limit, tpm_limit,
                        created_at, last_seen_at, killed_at, attached_token_count }, ... ] }
  401 -> { error: "unauthorized" }

POST /admin/agents/{principal_id}/tokens/{token_id}/attach
  200 -> { principal_id, token_id, attached_at }
  401 -> { error: "unauthorized" }
  403 -> { error: "forbidden" }
  404 -> { error: "agent_principal_not_found" | "agent_token_not_found" }
  409 -> { error: "agent_token_already_attached" | "agent_principal_killed" }

DELETE /admin/agents/{principal_id}/tokens/{token_id}
  200 -> { principal_id, token_id, detached_at }
  401 -> { error: "unauthorized" }
  403 -> { error: "forbidden" }
  404 -> { error: "agent_principal_not_found" | "agent_token_not_found" }

POST /admin/agents/{principal_id}/kill
  200 -> { id, killed_at }          # idempotent — returns the ORIGINAL killed_at on a repeat call
  401 -> { error: "unauthorized" }
  403 -> { error: "forbidden" }
  404 -> { error: "agent_principal_not_found" }

Schema:
- NEW table `agent_principals`: id (uuid7 PK) · tenant_id (FK tenants.id, ondelete CASCADE) · name (str, UNIQUE per (tenant_id, name)) · owner_user_id (FK users.id, nullable) · monthly_budget_usd (Decimal, nullable) · rpm_limit (int, nullable) · tpm_limit (int, nullable) · created_at (timestamptz, server_default now()) · last_seen_at (timestamptz, nullable) · killed_at (timestamptz, nullable).
- ALTER `agent_tokens` ADD `principal_id` (uuid, FK agent_principals.id, ondelete SET NULL, nullable, indexed) — additive; every existing v39 row stays NULL (unattached, byte-identical behavior).
- `AgentOAuthRepository` (domain/ports.py) gains: `kill_principal(*, principal_id, tenant_id, now) -> bool` — a SINGLE conditional `UPDATE agent_principals SET killed_at=now() WHERE id=:id AND tenant_id=:tenant_id AND killed_at IS NULL RETURNING id` (mirrors `SqlAlchemyApiKeyRepository.revoke`'s conditional-UPDATE shape) decides — via its own row-count, not a prior SELECT — whether THIS call is the one that fires; only when it returns a row does the SAME transaction run the bulk `UPDATE agent_tokens SET revoked_at=now() WHERE principal_id=:id AND revoked_at IS NULL`. Two concurrent kill calls race on the principal row's conditional UPDATE itself (Postgres row-level locking, no explicit `with_for_update()` needed for a single-statement conditional UPDATE) — exactly one wins and returns True (schedules the audit event and the token revoke), the other observes 0 rows updated and returns False (200 idempotent no-op, no audit, no re-revoke). · `attach_token(*, principal_id, token_id, tenant_id) -> None` — a SINGLE conditional `UPDATE agent_tokens SET principal_id=:pid WHERE id=:token_id AND tenant_id=:tenant_id AND principal_id IS NULL RETURNING id` (never a separate SELECT-then-UPDATE, closing the same TOCTOU race `mint_token`'s `with_for_update()` closes for the device-authorization row) — 0 rows updated is disambiguated by one cheap follow-up read into the exact §1 reject (`agent_token_not_found` | `agent_token_already_attached` | `agent_principal_killed`); the killed-principal check reads `agent_principals.killed_at` in the SAME statement's WHERE clause so a kill racing an in-flight attach cannot attach a token to a principal that just became killed. · `detach_token(*, principal_id, token_id, tenant_id) -> None`.
- `AuthzResult` (keys/domain/entities.py) gains, mirroring `team_id`/`team_budget_usd` exactly: `agent_principal_id: uuid.UUID | None = None`, `agent_principal_budget_usd: Decimal | None = None`. Populated in `CompositeKeyAuthenticator.authenticate`'s agent-token branch via a LEFT JOIN `agent_principals` on `agent_tokens.principal_id` inside `resolve_access_token`'s existing query (mirrors the `tenants` LEFT JOIN already present in `SqlAlchemyApiKeyRepository.get_by_id`); both fields stay `None` for an unattached token — byte-identical to today.
- `GovernanceService.authorize` (proxy/application/governance.py) gains `_check_agent_principal_budget`, a structural mirror of `_check_team_budget`: Redis spend counter key `usage:spend:agent_principal:{agent_principal_id}:{YYYYMM}`, fail-open on Redis error, hard 402 `BUDGET_EXCEEDED` only when `agent_principal_budget_usd` is set and the aggregate spend meets/exceeds it. Called from `authorize()` alongside the existing per-key/per-team checks, gated on `authz.agent_principal_id is not None`.
- `core/error_catalog.py` gains: `AGENT_PRINCIPAL_NOT_FOUND = ErrorSpec(404, "ERR_AGENT_PRINCIPAL_NOT_FOUND", ...)`, `AGENT_TOKEN_NOT_FOUND = ErrorSpec(404, "ERR_AGENT_TOKEN_NOT_FOUND", ...)`, `AGENT_TOKEN_ALREADY_ATTACHED = ErrorSpec(409, "ERR_AGENT_TOKEN_ALREADY_ATTACHED", ...)`, `AGENT_PRINCIPAL_NAME_CONFLICT = ErrorSpec(409, "ERR_AGENT_PRINCIPAL_NAME_CONFLICT", ...)`, `AGENT_PRINCIPAL_KILLED = ErrorSpec(409, "ERR_AGENT_PRINCIPAL_KILLED", ...)` — role-ceiling reuses the EXISTING `AUTH_FORBIDDEN`; unauthorized reuses the EXISTING `AUTH_TOKEN_MISSING`/`AUTH_TOKEN_INVALID`.
- `audit/domain/audit_event.py:AuditEvent` — no schema change; the kill emits an existing-shape event with `action="agent_principal.killed"`, `target_type="agent_principal"`, `target_id=str(principal_id)`, `actor_user_id` from the caller's Identity.
- Access pattern: every read/write in this contract filters `tenant_id` in the SAME query as its existence/uniqueness check (mirrors `SqlAlchemyApiKeyRepository.revoke`) — no separate lookup-then-scope-check ever exists in this task's code.
```

Glossary deltas:
- **agent principal**: a named, tenant-scoped identity grouping ≥0 already-minted device-OAuth agent tokens under one admin-managed record carrying an aggregate budget cap, ownership, lifecycle timestamps (created/last-seen/killed), and a single kill switch that revokes every attached token at once. (Matches and formalizes the term MILESTONE.md already names but does not yet define.)
- **kill switch**: the one admin action that atomically revokes every `agent_tokens` row attached to an agent principal and marks the principal itself killed — fail-closed, idempotent, audited, and effective at both authn seams on the next request with no separate propagation step (no cache exists to invalidate).

Status: DRAFT
Reported: no

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

Scope (may touch): `./src/`   <fill before the §3 freeze — every file the build may write>
Strategy (ordered batches): <1. … 2. … — the planned build order; guidance, not enforced; preferred architecture/pattern strategies; advise solution/method to resolve issues/implement features; let the named Persona's domain stance (below) shape the approach, not just architecture patterns>

Persona (required): <name the persona file under `.add/personas/` this build embodies as a domain stance atop SOUL.md — advisory, never lowers a gate; name "generic" if no project persona fits yet>
Spawn isolation (default): <prefer isolation: "worktree" for any subagent build/verify spawn, not only explicit parallel mode; shared-tree needs a stated reason — see worktree-isolated-spawn-default>
Known-problem fixes: <trap → planned fix — the failure modes this build must dodge; guidance, not enforced>
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
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
