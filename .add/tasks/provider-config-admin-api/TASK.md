# TASK: Provider config admin API — BYOK write path (tenant CRUD into the credential store)

slug: provider-config-admin-api · created: 2026-06-16 · stage: production · risk: high
autonomy: conservative   <!-- LOWERED from auto: the BYOK WRITE path — an admin/tenant API that ACCEPTS and PERSISTS provider secrets (Bearer tokens, AWS secret keys, Azure client secrets) into the Fernet-at-rest store. Secret material on the request+persist path — same security class as provider-credential-store (task 1) + dynamic-auth-byok (task 3). Per the v21 fold, any auth/secret task's verify gate runs an INDEPENDENT adversarial security subagent + a human gate (risk:high+auto trips unguarded_high_risk_auto). -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Task: add the BYOK **WRITE path** — a tenant-owner admin API to create/replace, list, inspect, and
delete a tenant's OWN provider credentials, persisting them into the task-1 Fernet-at-rest store.
This is the PRODUCER for the store that tasks 1-3 read; the store API is ALREADY complete (no store
change needed). Pure API-layer task: a new router + provider-specific request models + tests.

Touches (files · symbols · signatures):
- **NEW router** — `proxy/api/provider_keys_admin_router.py` (to create) — `APIRouter(prefix="/admin/provider-keys", tags=["provider-keys-admin"])`; mirrors `auth/api/oidc_admin_router.py`.
- **Store (task 1, EXISTS — NO change; this task only CALLS it)** — `proxy/infrastructure/tenant_provider_key_store.py`: `DbTenantProviderKeyStore.upsert(tenant_id: UUID, provider: str, credential: ProviderCredential, *, enabled: bool=True) -> None` :212 · `.get(tenant_id, provider) -> ProviderCredential|None` :272 · `.list(tenant_id) -> list[ProviderKeyStatus]` :314 (NEVER decrypts — reads provider/enabled/auth_mode/updated_at only) · `.delete(tenant_id, provider) -> bool` :352 (True iff a row was removed). Wired at `app.state.tenant_provider_key_store`.
- **Value objects (task 1, EXISTS — construct from the request body)** — `proxy/domain/provider_credentials.py`: `BearerCredential`(secret) · `BedrockCredential`(access_key_id, secret_access_key, region, session_token?) · `AzureCredential`(mode=api_key|aad; api_key | tenant_id/client_id/client_secret/endpoint/api_version/deployment_map/scope/authority) · `ProviderCredential` union; `@model_validator` raises `ValueError("ERR_PROVIDER_CREDENTIAL_INCOMPLETE")` for an incomplete body; `BYOK_PROVIDERS` frozenset (all 6); `ProviderKeyStatus` :254-265 BaseModel (provider, configured, enabled, auth_mode, updated_at — NO secret); `ProviderCredentialError.code`.
- **Auth/authz to MIRROR** — `auth/api/oidc_admin_router.py:127` `async def _get_owner_tenant_id(request, session) -> str` → `tenants/api/deps.get_bearer_token(request) -> str` (raises `AUTH_TOKEN_MISSING`) + `GetIdentityUseCase(request.app.state.token_service).execute(token) -> Identity`; `tenants/domain/entities.py:22` `Identity(user_id, tenant_id: UUID, email, role: Role)`, `Role` StrEnum {owner,admin,member}; OWNER-only via `AUTH_FORBIDDEN_OWNER_REQUIRED.exc()` (403). Tenant-scoping is JWT-intrinsic: the caller's `tenant_id` comes from the verified token → cross-tenant access architecturally impossible.
- **DB session DI** — `core/db.py:72` `get_session(request) -> AsyncIterator[AsyncSession]` (`Annotated[AsyncSession, Depends(get_session)]`); the store opens its own sessions, so the router can simply use `app.state.tenant_provider_key_store`.
- **Registration** — `main.py` `app.include_router(...)` block (~629-650); ORM side-effect import for `TenantProviderKeyRow` ALREADY present at `main.py:60`.
- **Errors** — `core/errors.py` / error catalog: RFC 9457 `ProblemError` (code+status+title); existing `ERR_PROVIDER_CREDENTIAL_INCOMPLETE`; reuse/extend for unknown-provider (422) + not-found (404).

Context (working folder):
- **PRIMARY analog to mirror** — `auth/api/oidc_admin_router.py` (`/admin/oidc` GET+PUT): Fernet-at-rest, `client_secret`→`"<stored>"` sentinel in responses, OWNER-only inline helper, `pg_insert(...).on_conflict_do_update(...)` upsert, lazy Fernet import. Same security invariants as this task.
- **SECONDARY analog** — `keys/api/router.py` + `keys/api/schemas.py` (full CRUD + `Depends(require_owner_or_admin)` + thin use-case + `ConfigDict(frozen=True)` response models).
- **TEST analog** — `tests/oidc_tenant_config/test_oidc_tenant_config.py` (self-contained: per-test `create_app` + `bootstrap_fresh_db` + `Fernet.generate_key()`; `signup_tenant()`→JWT; `assert_problem(resp,status,code)`; secret-never-returned + secret-never-logged + cross-tenant-denial assertions). New suite → `tests/provider_config_admin_api/`.

Honors (patterns / conventions):
- Clean arch layers (domain ← application ← infrastructure ← api); all data `tenant_id`-scoped (CONVENTIONS.md §Architecture).
- RFC 9457 problem+json, `ERR_<DOMAIN>_<REASON>` codes — never free text (§Errors).
- **OWNER-only** for secret-writing admin (mirror OIDC's bar — same risk class).
- **Secret hygiene** (v22 fold): secrets are plain `str` in REQUEST bodies (like `OidcConfigPutBody.client_secret`), `SecretStr` only inside domain value-objects, **NEVER** returned (mask/`<stored>`/omit) and never logged; `raise … from None` on any wrap.
- TDD mandatory — RED before build; `pytest` + `httpx.ASGITransport`; assert observable behavior only.
- risk:high → human gate + an INDEPENDENT adversarial security subagent; a security finding is HARD-STOP (run.md + v22 fold).
- Every `app.state` seam carries a production-wiring regression test (v5 fold).

Anchors the contract cites:
- `DbTenantProviderKeyStore.{upsert,get,list,delete}` · `ProviderKeyStatus`
- `BearerCredential` · `BedrockCredential` · `AzureCredential` · `ProviderCredential` · `BYOK_PROVIDERS` · `ERR_PROVIDER_CREDENTIAL_INCOMPLETE`
- `_get_owner_tenant_id` pattern · `get_bearer_token` · `GetIdentityUseCase` · `Identity` · `Role.OWNER` · `AUTH_TOKEN_MISSING` · `AUTH_FORBIDDEN_OWNER_REQUIRED`
- `get_session` · `app.state.tenant_provider_key_store` · `ProblemError`
- NEW: `provider_keys_admin_router` @ `/admin/provider-keys`

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Tenant-owner provider-credential admin API — the BYOK WRITE path. An authenticated
tenant OWNER manages their OWN per-provider credentials (create/replace · list · inspect-status ·
delete), persisted into the task-1 Fernet store; secrets are write-only (never returned/logged).

Framings weighed:
  - **Thin per-resource REST router mirroring `oidc_admin_router`** (chosen) — `/admin/provider-keys`,
    OWNER-only, one provider-discriminated PUT body validated by constructing the frozen value-object,
    no use-case layer (the store IS the application seam). Smallest native-looking surface.
  - Use-case layer like `keys/` (rejected — over-engineered for a thin CRUD over an existing store).
  - One generic "credential blob" endpoint (rejected — loses per-provider typed validation + the
    value-object's ERR_PROVIDER_CREDENTIAL_INCOMPLETE guard).

Must:
<must>
  - PUT /admin/provider-keys/{provider} — an authenticated OWNER creates-or-replaces (upsert) the
    credential for `provider`. The body is provider-discriminated: bearer→{secret}; bedrock→
    {access_key_id, secret_access_key, region, session_token?}; azure→{mode: api_key|aad, + api_key
    OR aad fields tenant_id/client_id/client_secret/endpoint/api_version/deployment_map?/scope?/
    authority?}. Optional `enabled` (default true). The router builds the matching ProviderCredential
    value-object and calls store.upsert(tenant_id, provider, credential, enabled=...). 200/201, status body (NO secret).
  - GET /admin/provider-keys — list the caller-tenant's configured providers as ProviderKeyStatus[]
    (provider, configured, enabled, auth_mode, updated_at) — NEVER any secret. Empty list when none.
  - GET /admin/provider-keys/{provider} — that provider's ProviderKeyStatus (NO secret); 404 if absent.
  - DELETE /admin/provider-keys/{provider} — remove the credential; 204 on delete; 404 if absent.
  - Every operation is scoped to the caller's JWT tenant_id and requires Role.OWNER.
  - Secrets are accepted as plaintext in the REQUEST only; persisted Fernet-encrypted; NEVER returned
    in any response and NEVER logged (write-only, mirror OIDC `<stored>` discipline — here: omit entirely).
  - All errors are RFC 9457 problem+json with stable ERR_/AUTH_ codes.
</must>
Reject:
<reject>
  - unknown / unsupported provider (path param not in BYOK_PROVIDERS) -> 422 "ERR_PROVIDER_UNKNOWN"
  - incomplete / invalid credential body for the provider+mode -> 422 "ERR_PROVIDER_CREDENTIAL_INCOMPLETE"
  - missing / malformed bearer token -> 401 "ERR_AUTH_INVALID_TOKEN" (reuse AUTH_TOKEN_MISSING spec)
  - authenticated non-OWNER (admin/member) -> 403 "ERR_AUTH_FORBIDDEN" (reuse AUTH_FORBIDDEN_OWNER_REQUIRED spec)
  - GET-status / DELETE of a provider with no stored credential for this tenant -> 404 "ERR_PROVIDER_KEY_NOT_FOUND"
</reject>
After:
<after>
  - After a successful PUT: store.get(tenant_id, provider) returns the value-object; list() shows it
    configured+enabled; a subsequent LLM call for that tenant+provider authenticates with it (closes
    the BYOK loop with tasks 1-3). The plaintext secret exists ONLY Fernet-encrypted at rest.
  - After DELETE: store.get returns None; list() omits the provider; an LLM call for it -> 402
    ERR_PROVIDER_KEY_MISSING (the task-2/3 fail-closed path).
  - No response body or log line in any path contains a plaintext secret.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The provider-discriminated REQUEST-body shape (ONE `PUT /{provider}` whose required fields depend
    on provider+mode, validated by constructing the frozen value-object) is the genuinely NEW design
    surface — the value-objects are frozen but their API ingress is not. Lowest confidence because the
    Azure aad/api_key dual-mode body is the trickiest to model cleanly (a flat optional-fields body
    coerced into the value-object, leaning on its validator for ERR_PROVIDER_CREDENTIAL_INCOMPLETE, vs
    a nested per-mode schema). If wrong: re-shape the request models + their tests (NOT the store/value-
    objects). Recommend: flat per-provider Pydantic bodies that construct the value-object and surface
    its ValueError as 422.
  - [ ] Enable/disable folds into the PUT `enabled` field (default true); NO separate PATCH in v25 —
    keeps the surface minimal. If wrong: add a PATCH later (additive). Recommend: fold in.
  - [ ] DELETE of an absent provider -> 404 (explicit), NOT 204-idempotent. Recommend 404 (mirror the
    not-found discipline; the UI wants the distinction).
  - [ ] OWNER-only (not OWNER+ADMIN) — secret-writing is the highest bar; mirror OIDC. Recommend OWNER-only.
  - [ ] Reuse the existing tenants/auth bearer+identity seam (get_bearer_token + GetIdentityUseCase)
    rather than a new dependency. Recommend reuse (no new auth surface for a secret endpoint).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Owner creates a bearer credential                                  # M1
  Given tenant T's owner is authenticated and T has no openrouter credential
  When the owner PUTs /admin/provider-keys/openrouter with {"secret": "sk-or-123"}
  Then the response is 200 and its body is a status with provider=openrouter, configured=true, enabled=true
  And the response body contains no "sk-or-123" anywhere (secret is write-only)
  And store.get(T, "openrouter") now returns a BearerCredential whose secret is "sk-or-123"

Scenario: Owner creates a bedrock credential with a session token            # M2
  Given tenant T's owner is authenticated
  When the owner PUTs /admin/provider-keys/bedrock with {access_key_id, secret_access_key, region, session_token}
  Then the response is 200 with a status (auth_mode null/na, no secret) for bedrock
  And store.get(T, "bedrock") returns a BedrockCredential carrying all four fields

Scenario: Owner creates an azure aad credential                              # M3
  Given tenant T's owner is authenticated
  When the owner PUTs /admin/provider-keys/azure with {mode:"aad", tenant_id, client_id, client_secret, endpoint, api_version}
  Then the response is 200 with a status for azure showing auth_mode="aad"
  And store.get(T, "azure") returns an AzureCredential in aad mode
  And the response body contains no client_secret value

Scenario: Owner creates an azure api_key credential                          # M4
  Given tenant T's owner is authenticated
  When the owner PUTs /admin/provider-keys/azure with {mode:"api_key", api_key, endpoint, api_version}
  Then the response is 200 with a status for azure showing auth_mode="api_key"
  And store.get(T, "azure") returns an AzureCredential in api_key mode

Scenario: Owner lists configured providers without secrets                   # M5
  Given tenant T has openrouter + bedrock credentials configured
  When the owner GETs /admin/provider-keys
  Then the response is 200 with a list of ProviderKeyStatus for openrouter and bedrock
  And no entry contains any secret field

Scenario: Owner inspects one provider's status                              # M6
  Given tenant T has an azure aad credential configured
  When the owner GETs /admin/provider-keys/azure
  Then the response is 200 with provider=azure, configured=true, auth_mode="aad", no secret

Scenario: Owner deletes a credential and the BYOK loop closes               # M7
  Given tenant T has an openrouter credential configured
  When the owner DELETEs /admin/provider-keys/openrouter
  Then the response is 204
  And store.get(T, "openrouter") returns None and the list no longer includes openrouter

Scenario: Owner disables a credential via enabled=false                     # M8
  Given tenant T's owner is authenticated
  When the owner PUTs /admin/provider-keys/openrouter with {"secret": "sk-or-9", "enabled": false}
  Then the response is 200 and the provider's status shows enabled=false
  And store.get(T, "openrouter") still returns the credential (configured but disabled)

Scenario: Re-PUT replaces (rotates) the stored secret                       # M9
  Given tenant T has an openrouter credential with secret "sk-or-old"
  When the owner PUTs /admin/provider-keys/openrouter with {"secret": "sk-or-new"}
  Then the response is 200 and store.get(T, "openrouter") returns a BearerCredential with secret "sk-or-new"
  And exactly one openrouter row exists for T (upsert, not insert)

Scenario: Unknown provider is rejected                                      # R1
  Given tenant T's owner is authenticated
  When the owner PUTs /admin/provider-keys/notaprovider with any body
  Then the response is 422 with code "ERR_PROVIDER_UNKNOWN"
  And no credential row is created for T

Scenario: Incomplete azure aad body is rejected                            # R2
  Given tenant T's owner is authenticated
  When the owner PUTs /admin/provider-keys/azure with {mode:"aad", tenant_id, client_id} (no client_secret)
  Then the response is 422 with code "ERR_PROVIDER_CREDENTIAL_INCOMPLETE"
  And store.get(T, "azure") still returns None (nothing was persisted)

Scenario: Missing bearer token is rejected                                 # R3
  Given no Authorization header is sent
  When a client PUTs /admin/provider-keys/openrouter with {"secret": "x"}
  Then the response is 401 with code "ERR_AUTH_INVALID_TOKEN"
  And no credential row is created

Scenario: A non-owner is forbidden                                         # R4
  Given a tenant member (role=member) is authenticated
  When the member PUTs /admin/provider-keys/openrouter with {"secret": "x"}
  Then the response is 403 with code "ERR_AUTH_FORBIDDEN"
  And no credential row is created

Scenario: Status/delete of an absent provider is not found                 # R5
  Given tenant T has no azure credential
  When the owner GETs (or DELETEs) /admin/provider-keys/azure
  Then the response is 404 with code "ERR_PROVIDER_KEY_NOT_FOUND"
  And no row is created or changed

Scenario: A tenant cannot touch another tenant's credentials               # SEC-tenant
  Given tenant A has an openrouter credential and tenant B's owner is authenticated with B's token
  When B GETs /admin/provider-keys (and DELETEs /admin/provider-keys/openrouter)
  Then B's list does not include A's credential and the DELETE is 404 for B
  And tenant A's openrouter credential remains intact

Scenario: The plaintext secret is never logged                            # SEC-log
  Given log capture is active and tenant T's owner is authenticated
  When the owner PUTs /admin/provider-keys/openrouter with {"secret": "sk-or-LOGCHECK"}
  Then no emitted log record contains "sk-or-LOGCHECK"
  And the credential is still persisted (store.get returns it)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

All routes are under `provider_keys_admin_router = APIRouter(prefix="/admin/provider-keys",
tags=["provider-keys-admin"])`, OWNER-only. Auth (every route): `get_bearer_token(request)` →
`GetIdentityUseCase(app.state.token_service).execute(token)` → `Identity`; `identity.role != Role.OWNER`
→ 403. `tenant_id = identity.tenant_id` (from the verified JWT — never a body/query param). The router
calls `app.state.tenant_provider_key_store`. `{provider}` MUST be in `BYOK_PROVIDERS` else 422.

```
PUT /admin/provider-keys/{provider}
  body (provider-discriminated; secrets are plaintext str on the WAY IN only):
    bearer  : { "secret": str, "enabled"?: bool = true }
    bedrock : { "access_key_id": str, "secret_access_key": str, "region": str,
                "session_token"?: str|null, "enabled"?: bool = true }
    azure   : { "mode": "api_key", "api_key": str, "endpoint": str, "api_version": str,
                "deployment_map"?: {str:str}, "enabled"?: bool = true }
            | { "mode": "aad", "tenant_id": str, "client_id": str, "client_secret": str,
                "endpoint": str, "api_version": str, "deployment_map"?: {str:str},
                "scope"?: str, "authority"?: str, "enabled"?: bool = true }
  flow: build the matching ProviderCredential value-object from the body (its @model_validator is the
        completeness gate) → store.upsert(tenant_id, provider, credential, enabled=body.enabled)
  200 -> ProviderKeyStatus { provider, configured: true, enabled, auth_mode, updated_at }   # NO secret
  401 -> { code: "ERR_AUTH_INVALID_TOKEN" }      422 -> { code: "ERR_PROVIDER_UNKNOWN" }
  403 -> { code: "ERR_AUTH_FORBIDDEN" }          422 -> { code: "ERR_PROVIDER_CREDENTIAL_INCOMPLETE" }

GET /admin/provider-keys
  200 -> { "keys": ProviderKeyStatus[] }         # store.list(tenant_id); NO secret; [] when none
  401 -> ERR_AUTH_INVALID_TOKEN   403 -> ERR_AUTH_FORBIDDEN

GET /admin/provider-keys/{provider}
  200 -> ProviderKeyStatus                        # derived from store.list(tenant_id) entry; NO secret
  404 -> { code: "ERR_PROVIDER_KEY_NOT_FOUND" }   # no credential for this tenant+provider
  401/403 as above   422 -> ERR_PROVIDER_UNKNOWN (provider not in BYOK_PROVIDERS)

DELETE /admin/provider-keys/{provider}
  flow: store.delete(tenant_id, provider) -> bool
  204 -> (no body)                                # a row was removed
  404 -> { code: "ERR_PROVIDER_KEY_NOT_FOUND" }   # delete() returned False (nothing to remove)
  401/403 as above   422 -> ERR_PROVIDER_UNKNOWN

Schema: NO new DB tables/columns. Reuses tenant_provider_keys via DbTenantProviderKeyStore
  {upsert, get, list, delete} on app.state.tenant_provider_key_store. ProviderKeyStatus is the existing
  domain model (proxy/domain/provider_credentials.py) — response models mirror it, secrets NEVER serialized.
New ErrorSpecs in core/error_catalog.py: PROVIDER_KEY_NOT_FOUND(404,"ERR_PROVIDER_KEY_NOT_FOUND"),
  PROVIDER_UNKNOWN(422,"ERR_PROVIDER_UNKNOWN"), PROVIDER_CREDENTIAL_INCOMPLETE(422,
  "ERR_PROVIDER_CREDENTIAL_INCOMPLETE"). Auth reuses AUTH_TOKEN_MISSING(401,"ERR_AUTH_INVALID_TOKEN") +
  AUTH_FORBIDDEN_OWNER_REQUIRED(403,"ERR_AUTH_FORBIDDEN"). Router registered in main.py.
```

Least-sure flag surfaced at freeze: [contract] the provider-discriminated PUT body — flat per-provider
Pydantic request models that CONSTRUCT the frozen value-object and surface its
`ValueError("ERR_PROVIDER_CREDENTIAL_INCOMPLETE")` as 422, vs a nested per-mode schema. The Azure
api_key|aad dual-mode body is the trickiest single point (one flat optional-field model + `mode`
discriminator, coerced into AzureCredential). If wrong: re-shape the request models + their tests ONLY
(the store + value-objects are frozen and untouched). [scenario] secondary: GET-one status is derived
from `store.list` (no per-provider getter exists that avoids decryption) — if list grows costly a
dedicated status getter is a later additive change.

Status: FROZEN @ v1 — approved by Tin Dang 2026-06-16
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90%
Plan (one test per scenario, asserting behavior not internals — mirror tests/oidc_tenant_config:
self-contained create_app + bootstrap_fresh_db + Fernet.generate_key() per test; signup_tenant()→JWT;
assert_problem(resp,status,code)):
<test_plan>
  - test_M1_owner_creates_bearer: PUT openrouter {secret} → 200 status configured/enabled; body has no secret; store.get returns BearerCredential
  - test_M2_owner_creates_bedrock_with_session_token: PUT bedrock {4 fields} → 200; store.get returns BedrockCredential with all four
  - test_M3_owner_creates_azure_aad: PUT azure {mode:aad,…} → 200 auth_mode=aad; store.get AzureCredential aad; body has no client_secret
  - test_M4_owner_creates_azure_api_key: PUT azure {mode:api_key,…} → 200 auth_mode=api_key; store.get AzureCredential api_key
  - test_M5_list_no_secrets: two configured → GET list → 200 both ProviderKeyStatus; assert no secret substring anywhere
  - test_M6_get_one_status: configured azure → GET /azure → 200 status auth_mode=aad, no secret
  - test_M7_delete_closes_loop: configured → DELETE → 204; store.get None; list omits it
  - test_M8_disable_via_enabled_false: PUT {…,enabled:false} → 200 enabled=false; store.get still returns it
  - test_M9_reput_rotates: PUT old then PUT new → store.get has new secret; exactly one row (upsert)
  - test_R1_unknown_provider_422: PUT /notaprovider → 422 ERR_PROVIDER_UNKNOWN; no row
  - test_R2_incomplete_azure_aad_422: PUT azure aad missing client_secret → 422 ERR_PROVIDER_CREDENTIAL_INCOMPLETE; store.get None
  - test_R3_missing_token_401: no Authorization → 401 ERR_AUTH_INVALID_TOKEN; no row
  - test_R4_non_owner_403: member token → 403 ERR_AUTH_FORBIDDEN; no row
  - test_R5_absent_provider_404: GET and DELETE absent azure → 404 ERR_PROVIDER_KEY_NOT_FOUND; no change
  - test_SEC_tenant_isolation: A configures openrouter; B's list excludes it; B DELETE /openrouter → 404; A's row intact
  - test_SEC_secret_never_logged: caplog active; PUT {secret:"sk-or-LOGCHECK"} → no log record contains it; store.get returns it
  - test_WIRING_router_registered: create_app(settings) mounts the router (a real PUT/GET reaches it, not 404-route) — the app.state seam regression (v5 fold)
</test_plan>

Tests live in: `apps/gateway/tests/provider_config_admin_api/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/api/provider_keys_admin_router.py` · `apps/gateway/src/gateway/main.py` · `apps/gateway/src/gateway/core/error_catalog.py`
Strategy (ordered batches): 1. error_catalog.py — add PROVIDER_KEY_NOT_FOUND / PROVIDER_UNKNOWN / PROVIDER_CREDENTIAL_INCOMPLETE ErrorSpecs. 2. provider_keys_admin_router.py — inline per-provider request models + ProviderKeyStatus response model + the OWNER auth helper (mirror oidc_admin_router) + PUT/GET-list/GET-one/DELETE handlers calling app.state.tenant_provider_key_store. 3. main.py — import + app.include_router(provider_keys_admin_router).
Safety rule (feature-specific): SECRET-WRITE-ONLY + TENANT-FROM-JWT. (1) No response model or log statement may ever serialize a plaintext secret — the ONLY response body is ProviderKeyStatus (which has no secret field); request models carry secrets as plain str and are never echoed. (2) tenant_id is ALWAYS taken from the verified JWT Identity, NEVER from a path/query/body param — cross-tenant write is structurally impossible. (3) An incomplete/invalid body fails CLOSED via the value-object validator → 422, persisting nothing.
Code lives in: `apps/gateway/src/gateway/proxy/api/` (+ main.py, core/error_catalog.py)
Constraints: do NOT change any test or the contract; do NOT modify the store or value-objects (frozen, tasks 1/3); allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

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

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### GATE RECORD
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
