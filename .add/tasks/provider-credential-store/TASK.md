# TASK: Tenant provider-credential store (encrypted-at-rest schema + value-objects)

slug: provider-credential-store · created: 2026-06-16 · stage: production · risk: high
autonomy: conservative   <!-- LOWERED from auto: net-new secret-at-rest crypto (Fernet) handling upstream provider secrets — the BYOK security substrate. Per v21 fold, any auth/secret task's verify gate runs an INDEPENDENT adversarial security subagent + a human gate (risk:high+auto trips unguarded_high_risk_auto). -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/migrations/versions/c9e2f4a8b1d6_pricing_units_schema.py` — current Alembic **HEAD**; the new `tenant_provider_keys` migration sets `down_revision = "c9e2f4a8b1d6"`. Dir `apps/gateway/migrations/versions/` (naming `{8hex}_{slug}.py`); `alembic.ini` + `migrations/env.py`.
- `apps/gateway/src/gateway/auth/infrastructure/orm.py:23` — `OidcProviderConfigRow(Base)` — the **MIRROR**: per-tenant config row, PK=`tenant_id`, `client_secret_enc: Mapped[bytes] = mapped_column(BYTEA, nullable=False)`, `enabled`, `created_at/updated_at`; docstring SECURITY INVARIANTS (BYTEA never plaintext, never JSON-serialized). New `TenantProviderKeyRow` mirrors this, PK `(tenant_id, provider)`.
- `apps/gateway/src/gateway/auth/api/oidc_admin_router.py:245` — Fernet **WRITE** template: `Fernet(key).encrypt(plaintext.encode())` → BYTEA (lazy import).
- `apps/gateway/src/gateway/auth/infrastructure/db_oidc_config_resolver.py:79,135` — Fernet **READ** template: `Fernet(key).decrypt(row.*_enc).decode()` — new repo's decrypt must wrap errors `from None` (extends v22 secret-chain floor; OIDC path predates it).
- `apps/gateway/src/gateway/core/config.py:182` — `oidc_config_encryption_key: str = ""` (`GATEWAY_OIDC_CONFIG_ENCRYPTION_KEY`). New `provider_key_encryption_key` Setting (`GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY`) follows the identical field pattern (separate key for blast-radius separation).
- `apps/gateway/src/gateway/core/db.py` — declarative `Base` the ORM row extends.
- Decrypted credential value-objects the store must round-trip (later consumed by `credential-resolution-seam`):
  - `proxy/infrastructure/bedrock_sigv4.py:38` — `AwsCredentials` (frozen dataclass: `access_key_id`, `secret_access_key`=field(repr=False), `region`, `session_token: str|None=None`).
  - `proxy/infrastructure/azure_config.py:29` — `AzureConfig` (`api_key`=field(repr=False), `endpoint`, `api_version`, `deployment_map`).
  - `proxy/infrastructure/azure_ad.py:45` — `AzureADConfig` (`tenant_id`, `client_id`, `client_secret`=field(repr=False), `scope`, `authority`).
  - Bearer providers (openrouter/openai/anthropic/google) need only a single secret string → a new `BearerCredential`.
- `apps/gateway/src/gateway/proxy/domain/ports.py:307` — `ProviderResolver(Protocol)` (model_id→provider); provider value-set `{openrouter, openai, anthropic, google, bedrock, azure}` is catalog-driven **TEXT** (foundation v7/v9), NOT a DB ENUM. The store keys on this value-set. (`catalog_provider_resolver.py:22`, `provider_registry.py:16` are the dispatch — consumed by task 2, not here.)

Context (working folder):
- `GLOSSARY.md` — `provider`, `Upstream`, `Markup`, `API key`. ⚠ Name-collision to avoid: existing **API key** = a tenant→proxy credential (SHA-256, one-way); the NEW concept is a **Provider credential** = a tenant→**upstream** secret (Fernet, reversible). The §1/§3 glossary delta must name the new term distinctly.
- Migration DDL template: `a9b3c4d5e6f7_oidc_tenant_config.py` (BYTEA + tenant PK + index pattern). Chain HEAD = `c9e2f4a8b1d6`.
- `pyproject.toml` / `dependencies.allowlist` — `cryptography` (Fernet) **already a dep**; no new package (honors the allowlist gate).

Honors (patterns / conventions):
- Fernet reversible encryption for at-rest secrets; **BYTEA** column; plaintext decrypted only in-memory, **never serialized to JSON** (OIDC SECURITY INVARIANTS docstring).
- `raise ... from None` on every decrypt/transport-error wrap whose chained exception could carry ciphertext/key/secret (v22 project-wide secret-chain floor; test asserts `__cause__ is None`).
- Frozen dataclass + `field(repr=False)` on secret fields (AwsCredentials/AzureConfig/AzureADConfig convention) — new `BearerCredential`/`AzureCredential` keep secrets out of `repr`.
- Domain ports are `typing.Protocol` + fakes via `app.state` (foundation v1, locked) — the store gets a port.
- Provider as **TEXT + bounded value-set**, not a DB ENUM (foundation v7 — avoids `ALTER TYPE`).
- Multi-row writes in ONE transaction; row ids generated explicitly at the call site (foundation v1).
- Migration is **additive** — no destructive change to existing tables.

Anchors the contract cites (the symbols §3 will name):
- `tenant_provider_keys` table — `tenant_id` (FK `tenants.id` ON DELETE CASCADE, part of PK), `provider` (TEXT, part of PK), `secret_enc` (BYTEA), `extra_enc` (BYTEA | NULL — multi-field creds: SigV4 triple / Azure endpoint+version+deployment_map), `enabled` (bool), `created_at`, `updated_at`.
- `TenantProviderKeyRow(Base)` — the ORM (mirror of `OidcProviderConfigRow`).
- Credential value-objects the store encrypts/returns: `BearerCredential` (new) · reuse `AwsCredentials` · `AzureCredential` (new: api_key+endpoint+api_version+deployment_map) · reuse `AzureADConfig`. **[contract flag]** reuse-existing-dataclasses vs a unified `ProviderCredential` union — resolved at §3.
- `TenantProviderKeyStore(Protocol)` port — method set (`upsert` / `get` / `list` / `delete`) frozen at §3.
- `GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY` Setting.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Tenant provider-credential store — per-`(tenant, provider)` reversible-encrypted credential persistence: the Alembic schema + Fernet repository + typed credential value-objects + a `typing.Protocol` port. The BYOK substrate every later v25 task builds on. NO HTTP and NO per-request wiring here (tasks 4 and 2 consume the port) — §3 freezes a SCHEMA + PORT shape, not a METHOD/path (like v8 deployment-model / v6 cooldown state-table).

Framings weighed: one-table unified envelope — `tenant_provider_keys` PK `(tenant_id, provider)`, `secret_enc` BYTEA + `extra_enc` BYTEA|NULL (encrypted JSON for multi-field creds), `get` returns a typed credential union by provider **(chosen)** · per-provider columns/tables (explicit at-rest typing but multiplies migrations; a new provider needs DDL — violates the additive-provider / TEXT-not-ENUM ethos) · single opaque `credential_enc` JSON blob (simplest, but "is configured?" + masked `list` would require a decrypt).

Must:
<must>
  - Persist a tenant's credential for a provider keyed `(tenant_id, provider)`, the secret Fernet-encrypted at rest (BYTEA) via `GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY`; `upsert` REPLACES the existing row's credential in ONE transaction (rotate = re-upsert).
  - Cover all 6 provider credential shapes: Bearer single-secret (openrouter/openai/anthropic/google) · Bedrock SigV4 (`access_key_id`, `secret_access_key`, `region`, `session_token?`) · Azure (api-key OR AAD client-credentials `tenant_id`/`client_id`/`client_secret`/`scope`/`authority`; plus `endpoint`/`api_version`/`deployment_map`).
  - `get(tenant_id, provider)` decrypts in-memory and returns the typed credential value-object (union resolved by provider) or `None` when absent/disabled.
  - `list(tenant_id)` returns per-provider STATUS (`provider`, `configured`, `enabled`, `updated_at`) WITHOUT decrypting and WITHOUT any plaintext — the masked, observable view.
  - `delete(tenant_id, provider)` removes the row and reports whether one existed.
  - The store is a `typing.Protocol` port with an injectable fake via `app.state` (zero-DB tests).
  - `provider` is validated against the bounded value-set `{openrouter, openai, anthropic, google, bedrock, azure}`, stored as TEXT.
  - Credential value-objects are **Pydantic v2 models** with the secret as `SecretStr` (masked in `repr`/`str`/logs; `.get_secret_value()` to read) and a `@model_validator(mode="after")` enforcing per-shape completeness (these validators ARE the EMPTY/INCOMPLETE rejections). Each exposes a `.to_<adapter>()` converter (`to_aws_credentials()`/`to_azure_ad_config()`) returning the EXISTING frozen dataclass the signer/adapter consumes (no divergent copy). The ORM row is NEVER JSON-serialized.
</must>
Reject:
<reject>
  - provider not in the value-set -> "ERR_PROVIDER_UNKNOWN"
  - empty / whitespace-only primary secret on upsert -> "ERR_PROVIDER_CREDENTIAL_EMPTY"
  - required multi-field missing for the shape (SigV4 w/o region; Azure AAD w/o client_id) -> "ERR_PROVIDER_CREDENTIAL_INCOMPLETE"
  - upsert/get when GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY is unset/invalid -> "ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE"  (mirrors OIDC requiring its key)
  - decrypt failure on get (corrupt ciphertext / wrong key) -> "ERR_PROVIDER_CREDENTIAL_CORRUPT"  (raised `from None` — no ciphertext/key on __cause__)
</reject>
After:
<after>
  - A `tenant_provider_keys` row exists for `(tenant, provider)`: `secret_enc` (and `extra_enc` when the shape needs it) hold Fernet ciphertext, `enabled=true`, timestamps set. The plaintext is recoverable ONLY via `get` in-memory; `list` reports it `configured=true` without decrypting; the ciphertext bytes ≠ the plaintext.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] The one-table unified envelope (`secret_enc` + `extra_enc` JSON, typed Pydantic union from `get`) cleanly covers all 6 shapes INCLUDING Azure's dual api-key-OR-AAD mode + endpoint/version/deployment_map — lowest confidence because Azure is the richest/most-divergent shape and a wrong envelope forces a schema + port re-freeze that ripples into tasks 2/3/4. If wrong: migration redo + port re-freeze + every consumer rebuilds. STILL LIVE to the freeze.
  - [x] DECIDED (Tin 2026-06-16): `get` returns store-owned **Pydantic v2 models** (`SecretStr` + `@model_validator`), each with a `.to_<adapter>()` converter to the EXISTING frozen `AwsCredentials`/`AzureADConfig` dataclasses — no drift, validation + secret-masking at construction. ("performance" rationale corrected: the task-2 TTL cache amortizes construction, so the real wins are SecretStr + validators + Pydantic-layer consistency.)
  - [x] DECIDED (Tin 2026-06-16): a SEPARATE `GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY` (blast-radius isolation from the OIDC key); cost = one more required env var.
  - [ ] `enabled=false` ≡ unconfigured for resolution (task 2 fail-closes on a disabled credential) — leaning yes; confirm at the freeze.
  - [ ] Azure non-secret fields (`endpoint`/`api_version`/`deployment_map`) stored encrypted in `extra_enc` for envelope uniformity (vs a plaintext config column) — harmless but confirm at the freeze.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
# --- Must: persist + encrypt-at-rest -------------------------------------------------
Scenario: Upsert encrypts a Bearer credential at rest
  Given tenant T and no row for (T, "openrouter")
  When upsert(T, "openrouter", BearerCredential(secret="sk-or-123"))
  Then a tenant_provider_keys row exists for (T, "openrouter") with enabled=true and timestamps set
  And the stored secret_enc is Fernet ciphertext whose bytes do NOT contain "sk-or-123"

# --- Must: get round-trips, decrypted, secret masked ---------------------------------
Scenario: Get round-trips a stored credential decrypted in memory
  Given (T, "openrouter") was upserted with BearerCredential(secret="sk-or-123")
  When get(T, "openrouter")
  Then it returns a BearerCredential whose .secret.get_secret_value() == "sk-or-123"
  And repr() of the returned model does NOT contain "sk-or-123"

# --- Must: absent -> None ------------------------------------------------------------
Scenario: Get returns None for an unconfigured provider
  Given tenant T has no row for (T, "anthropic")
  When get(T, "anthropic")
  Then it returns None

# --- Must: upsert replaces (rotate) in one transaction -------------------------------
Scenario: Upsert rotates an existing credential without duplicating the row
  Given (T, "openai") holds secret "sk-old"
  When upsert(T, "openai", BearerCredential(secret="sk-new"))
  Then get(T, "openai").secret.get_secret_value() == "sk-new"
  And exactly one row exists for (T, "openai")

# --- Must: SigV4 multi-field shape + converter --------------------------------------
Scenario: Bedrock SigV4 credential round-trips and converts to AwsCredentials
  Given tenant T
  When upsert(T, "bedrock", BedrockCredential(access_key_id="AKIAEX", secret_access_key="s3cr3t", region="us-east-1"))
  Then get(T, "bedrock").to_aws_credentials() == AwsCredentials(access_key_id="AKIAEX", secret_access_key="s3cr3t", region="us-east-1", session_token=None)
  And neither "s3cr3t" nor "AKIAEX" appears in plaintext in secret_enc or extra_enc

# --- Must: Azure dual-mode shape + converters (the ⚠ envelope stress test) -----------
Scenario: Azure AAD credential round-trips and converts to AzureADConfig + AzureConfig
  Given tenant T
  When upsert(T, "azure", AzureCredential(mode="aad", tenant_id="tid", client_id="cid", client_secret="csecret", endpoint="https://x.openai.azure.com", api_version="2024-10-21", deployment_map={"gpt-4o": "my-deploy"}))
  Then get(T, "azure").to_azure_ad_config() == AzureADConfig(tenant_id="tid", client_id="cid", client_secret="csecret")
  And get(T, "azure").to_azure_config().deployment_map == {"gpt-4o": "my-deploy"}

# --- Must: list = masked status view, no decrypt, no plaintext -----------------------
Scenario: List reports per-provider status without decrypting or exposing plaintext
  Given (T, "openrouter") configured+enabled and (T, "openai") configured+disabled, no other rows
  When list(T)
  Then the result contains {provider:"openrouter", configured:true, enabled:true} and {provider:"openai", configured:true, enabled:false}
  And the result carries no secret value (only provider/configured/enabled/updated_at fields)

# --- Must: delete removes + reports prior existence ----------------------------------
Scenario: Delete removes a credential and reports whether it existed
  Given (T, "google") is configured
  When delete(T, "google")
  Then it returns true and get(T, "google") returns None
  And a second delete(T, "google") returns false

# --- Must: enabled=false is unconfigured for resolution (assumption #3, freeze-confirm) -
Scenario: A disabled credential is not resolvable
  Given (T, "openai") is configured with enabled=false
  When get(T, "openai")
  Then it returns None
  And list(T) still reports {provider:"openai", configured:true, enabled:false}

# --- Must: Protocol port + injectable fake (zero-DB) ---------------------------------
Scenario: The store is a Protocol satisfied by a zero-DB fake
  Given a FakeTenantProviderKeyStore registered on app.state implementing TenantProviderKeyStore
  When a consumer reads the store from app.state and calls get/upsert
  Then it operates with no database connection (the fake satisfies the port structurally)

# --- Reject: unknown provider --------------------------------------------------------
Scenario: Unknown provider is rejected
  Given tenant T
  When upsert(T, "cohere", BearerCredential(secret="x"))
  Then it is rejected with "ERR_PROVIDER_UNKNOWN"
  And no row is written for (T, "cohere")

# --- Reject: empty secret ------------------------------------------------------------
Scenario: Empty or whitespace-only secret is rejected
  Given tenant T
  When constructing BearerCredential(secret="   ")
  Then it is rejected with "ERR_PROVIDER_CREDENTIAL_EMPTY" (at model construction)
  And no row is written

# --- Reject: incomplete multi-field --------------------------------------------------
Scenario: Incomplete multi-field credential is rejected
  Given tenant T
  When constructing BedrockCredential without region (or AzureCredential mode="aad" without client_id)
  Then it is rejected with "ERR_PROVIDER_CREDENTIAL_INCOMPLETE"
  And no row is written

# --- Reject: encryption key unavailable ----------------------------------------------
Scenario: Missing encryption key fails closed
  Given GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY is unset
  When upsert(T, "openrouter", BearerCredential(secret="sk"))
  Then it is rejected with "ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE"
  And no row is written and no plaintext is persisted

# --- Reject: corrupt ciphertext, fail closed, no secret on the chain -----------------
Scenario: Corrupt ciphertext fails closed without leaking the chain
  Given a (T, "openrouter") row whose secret_enc was tampered (not a valid Fernet token)
  When get(T, "openrouter")
  Then it is rejected with "ERR_PROVIDER_CREDENTIAL_CORRUPT"
  And the raised error's __cause__ is None
  And the stored row is left unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

> This is a **SCHEMA + PORT** contract (no HTTP — tasks 2/4 consume it). It freezes: the DB table,
> the credential value-objects (Pydantic v2), the store Protocol, the domain error, and the Setting.

### A. Persistent schema — `tenant_provider_keys`  (new, `proxy/infrastructure/orm.py` · migration `down_revision="c9e2f4a8b1d6"`)
```
tenant_provider_keys
  tenant_id   UUID    NOT NULL  FK tenants.id ON DELETE CASCADE   ┐ PK (tenant_id, provider)
  provider    TEXT    NOT NULL  (app-validated value-set)         ┘
  secret_enc  BYTEA   NOT NULL  -- Fernet ciphertext of the PRIMARY secret (never plaintext, never JSON-serialized)
  extra_enc   BYTEA   NULL      -- Fernet ciphertext of a JSON object of the remaining fields; NULL for Bearer
  auth_mode   TEXT    NULL      -- plaintext discriminator: "aad" | "api_key" for azure, else NULL (non-secret)
  enabled     BOOLEAN NOT NULL  server_default true
  created_at  TIMESTAMPTZ NOT NULL server_default now()
  updated_at  TIMESTAMPTZ NOT NULL server_default now() onupdate now()
Access: write = upsert ON CONFLICT (tenant_id, provider) DO UPDATE (one txn, replaces credential = rotate).
        read  = get by PK (decrypt in memory) · list by tenant_id (NO decrypt) · delete by PK.
        Primary-secret split per shape: Bearer→secret · Bedrock→secret_access_key · Azure→(client_secret | api_key).
        extra_enc JSON per shape: Bearer→absent · Bedrock→{access_key_id,region,session_token} · Azure→{endpoint,api_version,deployment_map,tenant_id,client_id,scope,authority}.
```

### B. Credential value-objects — Pydantic v2, secret as `SecretStr`  (`proxy/domain/provider_credentials.py`)
```python
ProviderName = Literal["openrouter","openai","anthropic","google","bedrock","azure"]

class BearerCredential(BaseModel):           # openrouter/openai/anthropic/google
    secret: SecretStr
    # @model_validator(after): secret.get_secret_value().strip() else ValueError("ERR_PROVIDER_CREDENTIAL_EMPTY")

class BedrockCredential(BaseModel):
    access_key_id: str;  secret_access_key: SecretStr;  region: str;  session_token: SecretStr | None = None
    # validator: all of {access_key_id, region} non-empty + secret non-empty, else EMPTY/INCOMPLETE
    def to_aws_credentials(self) -> AwsCredentials: ...          # → existing frozen dataclass (bedrock_sigv4.py:38)

class AzureCredential(BaseModel):
    mode: Literal["aad","api_key"];  endpoint: str;  api_version: str = DEFAULT_API_VERSION
    deployment_map: Mapping[str,str] = {}
    api_key: SecretStr | None = None                                       # mode="api_key"
    tenant_id: str|None=None; client_id: str|None=None; client_secret: SecretStr|None=None  # mode="aad"
    scope: str = DEFAULT_SCOPE;  authority: str = DEFAULT_AUTHORITY
    # validator: mode="api_key"→api_key required; mode="aad"→{tenant_id,client_id,client_secret} required, else INCOMPLETE
    def to_azure_config(self) -> AzureConfig: ...               # → existing (azure_config.py:29), api_key path
    def to_azure_ad_config(self) -> AzureADConfig: ...          # → existing (azure_ad.py:45), aad path

ProviderCredential = BearerCredential | BedrockCredential | AzureCredential

class ProviderKeyStatus(BaseModel):          # the masked list view — NO secret field ever
    provider: ProviderName; configured: bool; enabled: bool; auth_mode: str | None; updated_at: datetime
```

### C. Store port + domain error  (`proxy/domain/provider_credentials.py`)
```python
class TenantProviderKeyStore(Protocol):
    async def upsert(self, tenant_id: UUID, provider: ProviderName, credential: ProviderCredential, *, enabled: bool = True) -> None
    async def get(self,    tenant_id: UUID, provider: ProviderName) -> ProviderCredential | None   # None if absent OR enabled=false
    async def list(self,   tenant_id: UUID) -> list[ProviderKeyStatus]                              # no decrypt, no secret
    async def delete(self, tenant_id: UUID, provider: ProviderName) -> bool                         # True iff a row existed

class ProviderCredentialError(Exception):    # carries a stable .code; task 4 maps code→HTTP
    code: str   # one of the §1 Reject codes
```

### D. Reject → contracted response (every §1 code has a home)
```
ERR_PROVIDER_UNKNOWN                    -> upsert/get/delete raise ProviderCredentialError(code) before any row I/O
ERR_PROVIDER_CREDENTIAL_EMPTY          -> raised at Pydantic model construction (ValueError, message == code)
ERR_PROVIDER_CREDENTIAL_INCOMPLETE     -> raised at Pydantic model construction (ValueError, message == code)
ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE-> upsert/get raise ProviderCredentialError(code) before touching a row
ERR_PROVIDER_CREDENTIAL_CORRUPT        -> get raises ProviderCredentialError(code) FROM None (no ciphertext/key on __cause__)
```

### E. Config + glossary
```
Setting: provider_key_encryption_key: str = ""   (env GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY) — core/config.py
GLOSSARY +: "Provider credential" = a tenant's own upstream secret, Fernet-encrypted per (tenant, provider),
            resolved per request — DISTINCT from "API key" (tenant→proxy, SHA-256, one-way).
GLOSSARY ~: "Upstream" no longer "the platform's single LLM provider"; "Markup" decoupled from cost-plus (v25 supersession).
```

Mock + contract tests: a `FakeTenantProviderKeyStore` (in-memory dict, satisfies the Protocol, zero-DB) + contract
tests pinning the value-object shapes, the four method signatures, the `.to_<adapter>()` converters, and the
Reject codes — authored as the §4 red suite (they fail until the real models/repo/migration exist).

Status: FROZEN @ v1 — approved by Tin 2026-06-16. Baked-in calls approved: enabled=false ≡ unconfigured-for-resolution; Azure non-secret fields encrypted in extra_enc. Changing this frozen shape = a change request back to SPECIFY.
Least-sure flag surfaced at freeze: [contract] the one-table envelope must hold all 6 credential shapes in `secret_enc` + `extra_enc` JSON + `auth_mode` — Azure is the stress test (dual api_key / AAD mode + endpoint/version/deployment_map). Why lowest confidence: Azure is the most divergent shape and the envelope is shared. If wrong: schema migration redo + port re-freeze + tasks 2/3/4 rebuild.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥90% of the new module (project floor is 80%, `--cov-fail-under=80`; raised here — security-critical crypto).
Plan (one test per §2 scenario, asserting behavior not internals). RED target each = `ModuleNotFoundError: gateway.proxy.domain.provider_credentials` / missing `tenant_provider_key_store` (no impl yet). Harness mirrors `tests/oidc_tenant_config` (real PG `:5433/gateway_test`, `bootstrap_fresh_db`, `Fernet.generate_key()`, `create_app(settings)`):
<test_plan>
  value-objects (no DB) — test_provider_credentials.py
  - test_bearer_empty_secret_rejected:        construct BearerCredential(secret="   ") -> ValueError msg=="ERR_PROVIDER_CREDENTIAL_EMPTY"
  - test_bedrock_missing_region_incomplete:    BedrockCredential(...no region) -> "ERR_PROVIDER_CREDENTIAL_INCOMPLETE"
  - test_azure_aad_missing_client_id_incomplete: AzureCredential(mode="aad", no client_id) -> "ERR_PROVIDER_CREDENTIAL_INCOMPLETE"
  - test_secretstr_masks_in_repr:              repr(cred) excludes the secret; .get_secret_value() returns it
  - test_bedrock_to_aws_credentials:           .to_aws_credentials() == AwsCredentials(...) (existing dataclass)
  - test_azure_to_aad_and_azure_config:        .to_azure_ad_config()==AzureADConfig(...) AND .to_azure_config().deployment_map=={...}
  - test_protocol_fake_zero_db:                FakeTenantProviderKeyStore satisfies TenantProviderKeyStore; get/upsert with no DB conn
  store (DB) — test_tenant_provider_key_store_db.py
  - test_bearer_upsert_encrypts_at_rest:       upsert -> row exists, enabled=true; secret_enc bytes NOT containing "sk-or-123"
  - test_get_roundtrips_decrypted_masked:      get -> BearerCredential; .get_secret_value()=="sk-or-123"; repr excludes it
  - test_get_absent_returns_none:              get(unconfigured) -> None
  - test_upsert_rotates_single_row:            upsert sk-old then sk-new -> get==sk-new AND exactly one row
  - test_bedrock_roundtrip_to_aws_credentials: upsert+get(bedrock).to_aws_credentials() round-trips; no plaintext in secret_enc/extra_enc
  - test_azure_aad_roundtrip_converters:       upsert+get(azure aad) converters round-trip; deployment_map preserved
  - test_list_masked_status_no_decrypt:        list(T) -> [{provider,configured,enabled,auth_mode,updated_at}] with NO secret value present
  - test_delete_removes_and_reports:           delete -> True + get None; second delete -> False
  - test_disabled_not_resolvable:              enabled=false -> get None; list still shows configured=true,enabled=false
  - test_unknown_provider_rejected:            upsert(provider="cohere") -> ProviderCredentialError.code=="ERR_PROVIDER_UNKNOWN"; no row
  - test_missing_encryption_key_fails_closed:  key unset -> upsert -> ProviderCredentialError.code=="ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE"; no row
  - test_corrupt_ciphertext_from_none:         tamper secret_enc; get -> ProviderCredentialError.code=="ERR_PROVIDER_CREDENTIAL_CORRUPT"; __cause__ is None; row unchanged
</test_plan>

Tests live in: `apps/gateway/tests/provider_credential_store/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/domain/provider_credentials.py` `apps/gateway/src/gateway/proxy/infrastructure/orm.py` `apps/gateway/src/gateway/proxy/infrastructure/tenant_provider_key_store.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/migrations/versions/` `apps/gateway/src/gateway/main.py` `apps/gateway/migrations/env.py` `apps/gateway/src/gateway/tenants/infrastructure/orm.py` `apps/gateway/tests/migrations/test_migrations.py` `apps/gateway/tests/guardrails/test_guardrails_core.py` `apps/gateway/tests/provider_credential_store/test_tenant_provider_key_store_db.py`
<!-- SCOPE AMENDED post-freeze (2026-06-16, pre-build re-snapshot): added main.py + migrations/env.py — the new TenantProviderKeyRow must be SIDE-EFFECT IMPORTED in both (the established ORM-registration pattern, main.py:20-86) so Base.metadata.create_all (bootstrap_fresh_db) builds the table and Alembic autogenerate stays consistent. §3 untouched (scope correction, not a contract change). Re-snapshotted via `phase tests` → `advance`. -->
<!-- SCOPE AMENDED #2 post-freeze (2026-06-16, build): added tenants/infrastructure/orm.py + tests/migrations/test_migrations.py — both FORCED by the FROZEN tests, not by design drift: (a) the frozen DB test inserts `tenants.updated_at` (test_tenant_provider_key_store_db.py:171), but the baseline (ad14442336db) created tenants with created_at only; honoring the un-editable frozen test requires adding the column (ORM + migration e2b7f4c9a1d8) — additive, no production tenants (clean cutover). (b) TWO table-name manifests assert the exact public-table set and must list every contracted table: tests/migrations/test_migrations.py EXPECTED_TABLES (exact-equality) and tests/guardrails/test_guardrails_core.py's `NOT IN (...)` inventory (guardrails-adds-no-tables assertion). Adding the in-scope `tenant_provider_keys` table forces a one-line append to BOTH — the established SANCTIONED-EDIT maintenance pattern explicitly documented in each manifest (oidc/teams precedent). §3 contract untouched. Re-snapshotted via `phase tests` → `advance`. -->
<!-- SCOPE AMENDED #3 post-freeze (2026-06-16, VERIFY gate, Tin-approved): added tests/provider_credential_store/test_tenant_provider_key_store_db.py — a STRENGTHENING test addition (not a weakening/edit): the independent security review found the Azure `mode="api_key"` encrypt→decrypt DB round-trip was unit/converter-tested but never DB-tested (only `aad` was). Tin chose "close gap, then PASS" at the conservative-autonomy human gate. New test `test_azure_api_key_roundtrip_converters` mirrors the existing aad test + asserts at-rest secrecy of the api_key. §3 contract untouched; no existing assert changed. Re-snapshotted via `phase tests` → `advance`. -->
Wiring imports ONLY in main.py/env.py (one side-effect `import ... as _X  # noqa: F401` line each, mirroring the existing 7); NO behavioral change to main.py.
Strategy (ordered batches): 1. value-objects (ProviderName · BearerCredential · BedrockCredential · AzureCredential + validators · ProviderKeyStatus · ProviderCredentialError · TenantProviderKeyStore Protocol) in provider_credentials.py — pure domain, no IO. 2. the `.to_aws_credentials()/.to_azure_config()/.to_azure_ad_config()` converters → existing frozen dataclasses. 3. `provider_key_encryption_key` Setting in config.py. 4. TenantProviderKeyRow ORM (orm.py) + Alembic migration (down_revision="c9e2f4a8b1d6"). 5. DB store impl (Fernet encrypt-on-upsert, decrypt-on-get `from None`, masked list, delete) in tenant_provider_key_store.py.
Safety rule (feature-specific): upsert is ONE `ON CONFLICT (tenant_id, provider)` transaction; plaintext lives only in-memory inside encrypt()/decrypt(); decrypt failure raises `ProviderCredentialError("ERR_PROVIDER_CREDENTIAL_CORRUPT") from None` (assert `__cause__ is None`); `list` NEVER decrypts; the ORM row is NEVER serialized to JSON; secrets are `SecretStr` (masked repr).
Code lives in: `apps/gateway/src/gateway/proxy/` + the migration + config.
Constraints: do NOT change any test or the contract; allow-list packages only (cryptography already allowed; no new dep); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full backend suite **1053 passed**, 19 deselected (live/billing) @ 2026-06-16, orchestrator-run, PG :5433 up; task suite 27/27 (incl. the gate-added Azure api_key DB round-trip); migrations 6/6
- [x] coverage did not decrease — INCREASED: additive feature (5 new src + 2 migrations); 22 frozen tests + 1 gate-added strengthening test, all green; no existing test removed/skipped
- [x] no test or contract was altered during build — §3 CONTRACT untouched; no existing assert weakened. Test-file changes are all ADDITIVE/SANCTIONED: two table-name **manifest** appends (test_migrations.py EXPECTED_TABLES + test_guardrails_core.py NOT-IN inventory, the oidc/teams maintenance pattern documented in each) + ONE new strengthening test `test_azure_api_key_roundtrip_converters` (§5 SCOPE-AMENDED #3, Tin-approved at this gate to close the api_key DB-coverage gap)
- [x] the green was EARNED, not gamed — independent adversarial security subagent refute-read (2026-06-16): "SECURE + EARNED, no HARD-STOP"; all asserts substantive (real asyncpg round-trips, `raw_secret not in secret_enc`, `__cause__ is None`, exact reject-code equality); no vacuous/overfit/stubbed green found
- [x] concurrency / timing of the risky operation is safe — upsert is ONE `ON CONFLICT (tenant_id, provider)` transaction (atomic); plaintext exists only in-memory inside `_encrypt`/`_decrypt`; no shared mutable state across requests
- [x] no exposed secrets, injection openings, or unexpected dependencies — all secrets `SecretStr` (masked); Fernet ciphertext BYTEA at rest; decrypt failure raises `from None`; SQL fully ORM-parameterized (zero f-string/%/format); provider validated vs bounded value-set before I/O; no new dependency (cryptography already allow-listed)
- [x] layering & dependencies follow CONVENTIONS.md — domain `provider_credentials.py` is pure (no IO); infra `tenant_provider_key_store.py`/`orm.py` depend inward on the domain port; converters return the existing frozen `AwsCredentials`/`AzureConfig`/`AzureADConfig` (no divergent copy); mirrors the OIDC `oidc_provider_configs` layering
- [x] a person reviewed and approved the change — **Tin approved at the conservative-autonomy human gate (2026-06-16)**; chose "close gap, then PASS" (Azure api_key DB round-trip added + green)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `TenantProviderKeyRow` side-effect-imported in `main.py` + `migrations/env.py` (registers on `Base.metadata`; proven by create_all building the table in DB tests + migrations 6/6); `DbTenantProviderKeyStore` + value-objects + `ProviderCredentialError` + `TenantProviderKeyStore` Protocol all referenced by the 22 frozen tests; whole-suite green confirms reachability
- [x] DEAD-CODE (code) — ruff clean (would flag unused); the sole intentional "unused" is the side-effect ORM import (carrying `# noqa: F401` + pyright-ignore, the established pattern of the other 7); no orphaned symbol introduced
- [x] SEMANTIC (prose / non-code) — read the §1 reject-code catalog + §3 CONTRACT in full; confirmed every reject code, value-object shape, and the port signature match the implementation and the frozen tests

### Adversarial review findings (independent security subagent, 2026-06-16) — VERDICT: SECURE + EARNED
- Coverage gap — **CLOSED** (2026-06-16, Tin-approved): Azure `mode="api_key"` lacked a DB encrypt→decrypt round-trip test (only `aad` was DB-tested). Added `test_azure_api_key_roundtrip_converters` (at-rest secrecy + `auth_mode` persistence + `to_azure_config()` round-trip) — green; task suite 26→27.
- Residual risks for the milestone (all pre-existing or by-design, milestone-scoped-OUT): (1) single static Fernet key + (2) no per-tenant key derivation + (3) `provider_key_encryption_key` is plain `str` like every other secret Setting → the inherited single-static-key limitation the v25 MILESTONE explicitly lists OUT; (4) `auth_mode` plaintext is by §3 design; (5) `get`/`delete` don't pre-validate provider vs the value-set (harmless — parameterized, returns None/False; contract only mandates the guard on `upsert`).

### GATE RECORD
Outcome: PASS
Basis: independent adversarial security review = SECURE + EARNED (no HARD-STOP); full suite 1053 green; the one coverage gap (Azure api_key DB round-trip) CLOSED at the gate, not accepted. Residual risks (single static Fernet key / no rotation / no per-tenant key derivation) are the v25-MILESTONE-scoped-OUT single-key limitation — carried as observe-deltas, not gate blockers.
Reviewed by: Tin Dang · date: 2026-06-16

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of `ERR_PROVIDER_CREDENTIAL_CORRUPT` on `get` (a decrypt-failure spike = key misconfig / rotation / tampering signal); `upsert` latency (Fernet + one ON-CONFLICT txn); once task 2 lands, the `ERR_PROVIDER_KEY_MISSING` fail-closed rejection rate.
Spec delta for the next loop: the single static Fernet key (no rotation, no per-tenant derivation) is the inherited limitation — production will say whether rotation/KMS must be pulled forward from "later milestone" into the v25 line.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [TDD · open] a subagent's per-task test run (tests/provider_credential_store + tests/migrations) declared green but MISSED a second hardcoded table-manifest in tests/guardrails/test_guardrails_core.py — only the FULL-suite blast-radius run caught the regression. Lesson: never accept a delegated "green" on a schema-touching change without the full suite. (evidence: test_guardrails_core_migration_column_exists failed on the 1052-run, passed after the sanctioned manifest append.)
- [ADD · open] the §5 scope-anchor freezes from a SINGLE physical line and a build that legitimately must touch files beyond it needs an explicit amend + re-snapshot (`phase tests` → `advance`) — hit 4× here (main.py/env.py · tenants ORM + migrations manifest · guardrails manifest · gate-added test). The pattern holds; the friction is real. (evidence: 4 SCOPE-AMENDED notes in §5.)
- [ADD · open] the mandated adversarial security refute-read found a real DB-coverage gap (Azure api_key encrypt→decrypt never DB-tested) that the all-green suite hid — the independent skeptic earns its keep on risk:high secret tasks; the human gate then chose to close it rather than accept it. (evidence: §6 Adversarial review findings → gap CLOSED.)
