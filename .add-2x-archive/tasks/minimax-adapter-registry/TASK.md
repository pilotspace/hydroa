# TASK: MiniMax provider adapter, registry & BYOK wiring

slug: minimax-adapter-registry · created: 2026-07-01 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
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
  - `apps/gateway/src/gateway/proxy/domain/provider_credentials.py:36-50` — `ProviderName` Literal,
    `PROVIDER_VALUE_SET` frozenset, `BYOK_PROVIDERS` frozenset — all three currently list exactly
    `{openrouter, openai, anthropic, google, bedrock, azure}`; each needs `"minimax"` added.
  - `apps/gateway/src/gateway/proxy/infrastructure/tenant_provider_key_store.py:141-142` —
    `_parts_to_credential()`'s Bearer-credential dispatch: `if provider in ("openrouter","openai",
    "anthropic","google"): return BearerCredential(...)` — minimax is a plain Bearer-token API
    (confirmed via find-docs: `Authorization: Bearer <api_key>`, no extra fields), so it joins this
    tuple verbatim (no new `ProviderCredential` subtype, no `_credential_to_parts` branch needed).
  - `apps/gateway/src/gateway/proxy/api/provider_keys_admin_router.py:60` — `_BEARER_PROVIDERS:
    frozenset[str] = frozenset({"openrouter","openai","anthropic","google"})` — the admin BYOK
    endpoint's bearer-shape allow-list; needs `"minimax"`.
  - `apps/gateway/src/gateway/proxy/infrastructure/openai_provider.py` — `OpenAIDirectProvider`
    class (`_DEFAULT_BASE_URL`, `__init__(base_url=...)`, `_auth_headers()` raises
    `ProviderKeyMissing("openai")` HARDCODED, `_maybe_inject_web_search()` calls
    `native_web_search_tool("openai")` HARDCODED, `complete()`/`stream()` pass `provider="openai"`
    HARDCODED to `execute_with_retry` for metrics/logging). All four hardcoded spots need a
    `provider_name` constructor param (default `"openai"` — byte-identical) so a `"minimax"`
    instance labels correctly.
  - `apps/gateway/src/gateway/proxy/infrastructure/provider_registry.py` — `ProviderRegistry`,
    `select_provider()` — plain dict; no change needed beyond registering the new key.
  - `apps/gateway/src/gateway/main.py:663-740` — chat-adapter wiring block: every provider
    (`openrouter/anthropic/google/openai/bedrock/azure`) is registered UNCONDITIONALLY into
    `_chat_adapters` (credential resolved per-request from the BYOK contextvar — task-3
    dynamic-auth-byok; NO boot-time env-key check). `minimax` follows the exact `_openai_direct`
    pattern (lines 693-703): a new `OpenAIDirectProvider(base_url=settings.minimax_base_url,
    provider_name="minimax", ...)` instance registered as `_chat_adapters["minimax"]`.
  - `apps/gateway/src/gateway/core/config.py` — `Settings` — needs `minimax_base_url: str =
    "https://api.minimax.io/v1"` (mirrors `openai_base_url`/`anthropic_base_url`/`google_base_url`
    pattern — no operator-level API key field; BYOK-only per the dynamic-auth-byok precedent).
  - `apps/dashboard/components/settings/ProviderKeysSettings.tsx:25` — `const PROVIDERS =
    ["openrouter","openai","anthropic","google","bedrock","azure"] as const;` (fixed render order
    for the BYOK settings page) — needs `"minimax"`.
  - `apps/dashboard/components/settings/ConfigureProviderDialog.tsx:25` — `const BEARER_PROVIDERS
    = new Set(["openrouter","openai","anthropic","google"]);` (drives the single-bearer-secret
    form vs. the multi-field Azure/Bedrock form) — needs `"minimax"`.
  - Frozen/parametrized test suites asserting the exact provider set (widen, do not weaken):
    `apps/gateway/tests/dynamic_auth_byok/test_dynamic_auth_byok.py:150`,
    `apps/gateway/tests/credential_resolution_nonchat/test_credential_resolution_nonchat.py:69`,
    `apps/gateway/tests/credential_resolution_seam/test_credential_resolution_seam.py:483`.

Context (working folder):
  - MiniMax API reference (ctx7 `/websites/platform_minimax_io_api-reference`,
    https://platform.minimax.io/docs/api-reference/text-chat-openai): `POST /v1/chat/completions`
    is OpenAI-wire-compatible — `Authorization: Bearer <api_key>` (no group_id/extra header),
    request `{model, messages, stream, temperature, max_tokens, tools}`, response
    `{id, object:"chat.completion", choices[], usage:{prompt_tokens,completion_tokens,
    total_tokens}}` — byte-shape matches what `OpenAIDirectProvider.complete()`/`stream()` already
    forward. `GET /v1/models` also OpenAI-compatible (id/object/created/owned_by). Base URL:
    `https://api.minimax.io/v1`. Models seen in docs: MiniMax-M3, MiniMax-M2.x family (exact
    catalog id/pricing sourced in the follow-on `minimax-catalog-seed` task).
  - Live-verify credential: a real MiniMax API key was supplied by Tin in-chat for the
    `minimax-live-verify` task — treat as a live secret (BYOK-store only; never written to
    `.env`/committed config/logs/this TASK.md).

Honors (patterns / conventions):
  - PROJECT.md invariant: provider is catalog metadata, never client-specified (unchanged).
  - v9 folded rule: "adding a provider NEVER changes the default path" — registering `minimax`
    must not alter `_chat_adapters["openrouter"|"openai"|...]` behavior (byte-identical).
  - dynamic-auth-byok (task-3, folded): EVERY provider is registered unconditionally at boot;
    `ProviderKeyMissing` fires at per-request resolve time (402), never a boot-time env check —
    minimax has NO operator-level API-key Settings field, BYOK-only.
  - v25 BYOK: secrets Fernet-at-rest via `DbTenantProviderKeyStore`; guard order is
    unknown-provider (`ERR_PROVIDER_UNKNOWN`) before the encryption-key guard.

Anchors the contract cites:
  - `ProviderName` / `PROVIDER_VALUE_SET` / `BYOK_PROVIDERS` (provider_credentials.py)
  - `_parts_to_credential()` Bearer tuple (tenant_provider_key_store.py:141)
  - `_BEARER_PROVIDERS` (provider_keys_admin_router.py:60)
  - `OpenAIDirectProvider.__init__` / `_auth_headers` / `_maybe_inject_web_search` / `complete` /
    `stream` (openai_provider.py)
  - `_chat_adapters["openai"] = OpenAIDirectProvider(...)` wiring block (main.py:693-703)
  - `Settings.openai_base_url`-sibling field (core/config.py)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Register "minimax" as a chat-only, BYOK-authenticated provider — adapter, registry, admin API, dashboard picker.
Framings weighed:
  - (chosen) Generalize `OpenAIDirectProvider` with a `provider_name: str = "openai"` constructor
    param (replacing its 4 hardcoded `"openai"` literals) and register a `"minimax"` instance the
    same way `"openai"` is registered in `main.py` — reuses the proven v9 "one adapter, base_url
    swap" shape; default arg keeps existing openai behavior byte-identical.
  - Bespoke `MiniMaxProvider` subclass/copy of openai_provider.py — rejected: ~300 lines duplicated
    for a 3-string difference, drifts from the original on the next shared fix.
  - A generic provider-config-table + single factory covering ALL OpenAI-wire providers at once —
    rejected: over-scoped for this task and fights the stated TYPED_EXTRAS_NO_DISPATCH /
    explicit-dict-lookup convention (provider_registry.py docstring); each provider is its own
    explicit registration line by design.
Must:
<must>
  - "minimax" added to `ProviderName` Literal + `PROVIDER_VALUE_SET` + `BYOK_PROVIDERS`
    (provider_credentials.py) — pure value-set widening, no shape change.
  - `OpenAIDirectProvider.__init__` gains `provider_name: str = "openai"`; `_auth_headers()`,
    `_maybe_inject_web_search()`, `complete()`, `stream()` read `self._provider_name` instead of
    the literal `"openai"`. A default-arg instance (existing `_openai_direct`) stays
    byte-identical.
  - `Settings` gains `minimax_base_url: str = "https://api.minimax.io/v1"` — BYOK-only, no
    operator-level API-key Settings field (mirrors the dynamic-auth-byok precedent for
    openai/anthropic/google/bedrock/azure).
  - `main.py` registers `_chat_adapters["minimax"] = OpenAIDirectProvider(base_url=
    settings.minimax_base_url, provider_name="minimax", max_retries=settings.upstream_max_retries,
    ...)` UNCONDITIONALLY (credential resolved per-request from the BYOK contextvar; no boot-time
    check) — mirrors every other provider's v9/task-3 registration.
  - "minimax" is NOT added to the non-chat `_providers` dict — mirrors "anthropic" (chat-only,
    no non-chat modality registered) since embeddings/image/audio are explicitly Out of scope
    for this milestone. Avoids an unreachable/orphaned registration (§6 DEAD-CODE check).
  - `_parts_to_credential()`'s Bearer tuple (tenant_provider_key_store.py:141) gains `"minimax"`
    — reconstructs a plain `BearerCredential`, no new `ProviderCredential` subtype.
  - `_BEARER_PROVIDERS` (provider_keys_admin_router.py:60) gains `"minimax"` so `PUT
    /admin/provider-keys/minimax {secret: "..."}` builds a `BearerCredential` via the existing
    `_build_credential()` dispatch — the admin router's frozen route/body SHAPE is unchanged
    (pure value-set widening, same as `PROVIDER_VALUE_SET`).
  - Dashboard `PROVIDERS` (ProviderKeysSettings.tsx:25) and `BEARER_PROVIDERS`
    (ConfigureProviderDialog.tsx:25) both gain `"minimax"` so a tenant can configure/see it from
    the settings UI with the single-secret form (not the multi-field Azure/Bedrock form).
  - `select_provider(modality="chat", provider="minimax", registry)` resolves to the new adapter.
  - Existing openrouter/openai/anthropic/google/bedrock/azure chat + non-chat behavior is
    byte-identical (v9 "adding a provider never changes the default path" invariant).
</must>
Reject:
<reject>
  - `PUT /admin/provider-keys/minimax` with an empty/missing `secret` -> "ERR_PROVIDER_CREDENTIAL_INCOMPLETE" (422, existing `BearerCredential` validator — no new code)
  - `PUT /admin/provider-keys/<still-unknown-provider>` -> "ERR_PROVIDER_UNKNOWN" (existing guard, unaffected by this widening)
  - A chat request routed to a MiniMax-provider model with no tenant MiniMax key stored -> 402 `ERR_PROVIDER_KEY_MISSING` (existing per-request resolve-time guard, unaffected — proven generically by the credential-resolution-seam suite; this task's tests parametrize "minimax" into it, not invent a new path)
</reject>
After:
<after>
  - A tenant (OWNER role) can PUT a MiniMax API key via the existing BYOK admin endpoint and it
    round-trips (upsert -> get -> shows configured, no secret echoed) exactly like the other
    Bearer providers.
  - `app.state.chat_adapters["minimax"]` exists and is a `OpenAIDirectProvider` instance pointed
    at `https://api.minimax.io/v1`.
  - The dashboard BYOK settings page lists MiniMax with the single-secret configure form.
  - No behavior change for any of the other 6 providers (regression suite green, unchanged).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ MiniMax's STREAMING `/v1/chat/completions` response (stream:true) emits a terminal SSE chunk
    carrying `usage:{prompt_tokens,completion_tokens,total_tokens}` in the shape the frozen
    `extract_usage_from_sse` helper scans for (last usage frame before `data: [DONE]`) — confirmed
    via find-docs for the NON-streaming response shape only; the raw streaming SSE transcript was
    not fetched. Lowest confidence because streaming billing correctness hinges on it; if wrong:
    a MiniMax streaming call could under-bill ($0 usage) or the extractor could silently miss the
    frame. NOT material to THIS task (which makes no live network call — pure wiring/BYOK unit
    tests); carried forward as the #1 assumption for `minimax-live-verify` to confirm or deny live.
  - [ ] MiniMax's 429/rate-limit response shape is standard enough for the existing generic
    `execute_with_retry` Retry-After handling to work unchanged — confirm or deny at
    `minimax-live-verify` (not this task).
  - [ ] Whether to also live-check `GET /v1/models` as a catalog cross-reference — deferred
    entirely to `minimax-catalog-seed`, out of scope here.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: minimax joins the provider value set
  Given the gateway process starts
  When ProviderName / PROVIDER_VALUE_SET / BYOK_PROVIDERS are inspected
  Then "minimax" is a member of all three
  And openrouter/openai/anthropic/google/bedrock/azure remain members, unchanged

Scenario: minimax chat adapter is registered unconditionally at boot
  Given the gateway boots with no MiniMax operator-level env var set (BYOK-only, no such var exists)
  When app.state.chat_adapters is inspected
  Then chat_adapters["minimax"] exists and is an OpenAIDirectProvider bound to base_url
       "https://api.minimax.io/v1"
  And chat_adapters["openai"/"anthropic"/"google"/"bedrock"/"azure"/"openrouter"] are unchanged

Scenario: minimax is chat-only — no non-chat registration
  Given the gateway has booted
  When app.state.provider_registry (the non-chat _providers dict) is inspected
  Then it has no "minimax" entry (mirrors "anthropic", which also has none)
  And its "openai"/"google"/"bedrock"/"azure"/"openrouter" entries are unchanged

Scenario: OpenAIDirectProvider generalizes without changing openai's behavior
  Given an OpenAIDirectProvider constructed with the default provider_name
  When _auth_headers() runs with no credential set in the contextvar
  Then it raises ProviderKeyMissing("openai") exactly as before
  And a second instance constructed with provider_name="minimax" raises ProviderKeyMissing("minimax")
      instead, under the same no-credential condition

Scenario: a tenant stores a MiniMax key via the existing admin API
  Given an authenticated OWNER-role tenant with no existing minimax row
  When they PUT /admin/provider-keys/minimax {"secret": "<a-key>"}
  Then the response carries ProviderKeyStatus{provider:"minimax", configured:true} with no secret field
  And a subsequent GET /admin/provider-keys/minimax also returns configured:true
  And the persisted row round-trips through _parts_to_credential's new "minimax" branch as a
      BearerCredential (get() after put() returns a BearerCredential with the same secret)

Scenario: minimax appears in the dashboard BYOK settings picker
  Given the ProviderKeysSettings page renders its PROVIDERS list
  When the list is read
  Then "minimax" is included and renders the single-secret ConfigureProviderDialog form
      (BEARER_PROVIDERS membership), not the multi-field Azure/Bedrock form
  And the six pre-existing providers keep their original relative order

Scenario: select_provider resolves "minimax" once wired
  Given a ProviderRegistry containing the registered minimax chat adapter
  When select_provider(modality="chat", provider="minimax", registry) is called
  Then it returns that OpenAIDirectProvider instance
  And select_provider for every pre-existing provider name is unaffected

Scenario: PUT minimax key with an empty secret is rejected
  Given an authenticated OWNER-role tenant
  When they PUT /admin/provider-keys/minimax {"secret": ""}
  Then the response is 422 "ERR_PROVIDER_CREDENTIAL_INCOMPLETE"
  And no row is written/updated for (tenant_id, "minimax") — prior state (absent, or a previously
      configured key) is unchanged

Scenario: PUT to a still-unknown provider is rejected
  Given an authenticated OWNER-role tenant
  When they PUT /admin/provider-keys/not-a-real-provider {"secret": "x"}
  Then the response is 4xx "ERR_PROVIDER_UNKNOWN"
  And PROVIDER_VALUE_SET is unchanged (still exactly the 7 known providers, minimax included)

Scenario: a chat request to minimax with no stored tenant key is rejected
  Given a tenant with no minimax row in tenant_provider_key_store
  When a chat completion request routes to a catalog model with provider="minimax"
  Then the response is 402 "ERR_PROVIDER_KEY_MISSING"
  And no upstream HTTP call is made to api.minimax.io and no usage_records row is written

Scenario: re-PUT (upsert) replaces an existing minimax key
  Given a tenant already has a minimax key configured
  When they PUT /admin/provider-keys/minimax with a new secret
  Then the stored row updates in place (ON CONFLICT DO UPDATE) and configured stays true
  And the previously stored secret is overwritten, not retained or versioned

Scenario: DELETE removes a configured minimax key
  Given a tenant has a minimax key configured
  When they DELETE /admin/provider-keys/minimax
  Then the response is 204, and a subsequent GET /admin/provider-keys/minimax returns 404
  And a subsequent chat request routed to a minimax-provider model now gets 402
      "ERR_PROVIDER_KEY_MISSING"
```

Edge case ruled out on purpose: concurrent PUTs to the same (tenant_id, "minimax") row — already
handled generically by the existing `ON CONFLICT DO UPDATE` upsert (task-1 BYOK store, unchanged
by this task); not re-tested per-provider.

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
Module widenings (value-set only, no shape change; GLOSSARY "provider" unchanged):
  provider_credentials.py
    ProviderName = Literal["openrouter","openai","anthropic","google","bedrock","azure","minimax"]
    PROVIDER_VALUE_SET: frozenset[str] = frozenset({...6 existing..., "minimax"})
    BYOK_PROVIDERS:     frozenset[str] = frozenset({...6 existing..., "minimax"})
  tenant_provider_key_store.py
    _parts_to_credential(): Bearer branch tuple gains "minimax"
      if provider in ("openrouter","openai","anthropic","google","minimax"):
          return BearerCredential(secret=SecretStr(secret_plain))
  provider_keys_admin_router.py
    _BEARER_PROVIDERS: frozenset[str] = frozenset({"openrouter","openai","anthropic","google","minimax"})
  apps/dashboard/components/settings/ProviderKeysSettings.tsx
    PROVIDERS = ["openrouter","openai","anthropic","google","bedrock","azure","minimax"] as const
  apps/dashboard/components/settings/ConfigureProviderDialog.tsx
    BEARER_PROVIDERS = new Set(["openrouter","openai","anthropic","google","minimax"])

New adapter constructor signature (openai_provider.py — additive, default-preserving):
  OpenAIDirectProvider.__init__(
      *, base_url: str = _DEFAULT_BASE_URL, provider_name: str = "openai",
      max_retries: int = 0, backoff_base: float = 0.5, retry_deadline_s: float = 0.0,
      metrics_registry: MetricsRegistry | None = None,
  ) -> None
    - stores self._provider_name = provider_name
    - _auth_headers() raises ProviderKeyMissing(self._provider_name) (was hardcoded "openai")
    - _maybe_inject_web_search() calls native_web_search_tool(self._provider_name)
    - complete() / stream() pass provider=self._provider_name to execute_with_retry(...)
    - class-level default `_provider_name = "openai"` alongside the existing `_max_retries` etc.
      class defaults, so __new__-built test doubles that skip __init__ still resolve it.

New Settings field (core/config.py):
  minimax_base_url: str = "https://api.minimax.io/v1"   # env GATEWAY_MINIMAX_BASE_URL

New wiring (main.py, inside the existing UNCONDITIONAL _chat_adapters block, after azure):
  _chat_adapters["minimax"] = OpenAIDirectProvider(
      base_url=settings.minimax_base_url,
      provider_name="minimax",
      max_retries=settings.upstream_max_retries,
      backoff_base=settings.upstream_retry_backoff_base_s,
      retry_deadline_s=settings.upstream_retry_deadline_s,
      metrics_registry=app.state.metrics_registry,
  )
  # Deliberately NOT added to the non-chat `_providers` dict (chat-only; mirrors "anthropic").

Reused, UNCHANGED (frozen elsewhere by prior tasks — this task widens their value-set inputs only,
never reopens their shape):
  PUT/GET/DELETE /admin/provider-keys/{provider}  (§3 FROZEN @ v1, provider-config-admin-api)
    PUT    body: { secret?, access_key_id?, secret_access_key?, region?, session_token?, mode?,
                   endpoint?, api_version?, deployment_map?, api_key?, tenant_id?, client_id?,
                   client_secret?, scope?, authority?, enabled? }
      200/201 -> ProviderKeyStatus{ provider, configured, ... }   (no secret field, ever)
      422     -> { error: "ERR_PROVIDER_CREDENTIAL_INCOMPLETE" }
      4xx     -> { error: "ERR_PROVIDER_UNKNOWN" }   (provider not in PROVIDER_VALUE_SET)
    GET    /admin/provider-keys/{provider} -> 200 ProviderKeyStatus | 404 "ERR_PROVIDER_KEY_NOT_FOUND"
    DELETE /admin/provider-keys/{provider} -> 204 | 404 "ERR_PROVIDER_KEY_NOT_FOUND"
  select_provider(modality, provider, registry) -> UpstreamProvider | raises 503
    "ERR_PROVIDER_UNAVAILABLE" — unchanged shape; now additionally resolves "minimax".
  Chat credential-resolution seam (`CompletionUseCase._resolve_credential`) -> 402
    "ERR_PROVIDER_KEY_MISSING" on an absent tenant key for the request's provider — unchanged shape.

Schema: no migration. `tenant_provider_key_store` rows key on (tenant_id, provider TEXT); the
app-level PROVIDER_VALUE_SET guard is the only admission gate (value-set widening only, no DDL).
```

Status: FROZEN @ v1 — approved by Tin Dang (2026-07-01)
Least-sure flag surfaced at freeze:
⚠ [spec] MiniMax's STREAMING `/v1/chat/completions` usage-frame shape (billing correctness on the
  stream path) was confirmed via find-docs only for the NON-streaming response body, not a raw SSE
  transcript — because the doc tool returned schema tables, not a live stream capture. If wrong: a
  streaming MiniMax call could under-bill or the frozen `extract_usage_from_sse` scanner could miss
  the frame. NOT a risk to THIS task (its build/tests make no live network call) — carried forward
  as the #1 open item for `minimax-live-verify` to confirm or deny against the real endpoint.
⚠ [contract] `provider_name` as the new constructor param name on `OpenAIDirectProvider` was chosen
  for clarity, not cross-checked against every other adapter's constructor for a pre-existing naming
  convention — because a full grep of all 6 adapter constructors wasn't run. If wrong: a same-PR
  rename during review, no functional impact (the param is internal, never serialized/logged).
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: no decrease vs. current baseline (widening + additive adapter code; no new
  uncovered branches introduced by BUILD).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_minimax_joins_the_provider_value_set: ProviderName/PROVIDER_VALUE_SET/BYOK_PROVIDERS
    contain "minimax" + the 6 existing members unchanged
  - test_minimax_chat_adapter_registered_unconditionally: chat_adapters["minimax"] exists,
    OpenAIDirectProvider bound to api.minimax.io/v1 + 6 existing adapters unchanged
  - test_minimax_not_registered_for_non_chat_modalities: non-chat provider_registry has no
    "minimax" entry (mirrors "anthropic") + existing non-chat entries unchanged
  - test_openai_direct_provider_default_provider_name_unchanged: default-arg instance still
    fails ProviderKeyMissing("openai")
  - test_openai_direct_provider_custom_provider_name_minimax: provider_name="minimax" instance
    fails ProviderKeyMissing("minimax")
  - test_parts_to_credential_minimax_returns_bearer: _parts_to_credential("minimax", ...) ->
    BearerCredential
  - test_select_provider_resolves_minimax: select_provider("chat","minimax",registry) resolves
  - test_bearer_providers_admin_allowlist_includes_minimax: _BEARER_PROVIDERS contains "minimax"
  - test_admin_put_minimax_key_roundtrip: PUT+GET round-trip, no secret echoed, store returns
    BearerCredential
  - test_admin_put_minimax_empty_secret_rejected: empty secret -> 422
    ERR_PROVIDER_CREDENTIAL_INCOMPLETE + assert nothing persisted
  - test_admin_put_unknown_provider_still_rejected: still-unknown provider -> 422
    ERR_PROVIDER_UNKNOWN + assert PROVIDER_VALUE_SET == exactly the 7 known providers
  - test_admin_put_minimax_key_upsert_replaces: re-PUT replaces the stored secret in place
  - test_admin_delete_minimax_key: DELETE -> 204, subsequent GET -> 404, store returns None
  - (widened, not new) test_byok_providers_includes_bedrock_and_azure
    (tests/dynamic_auth_byok) / test_bearer_provider_resolves_and_sets_contextvar[minimax]
    (tests/credential_resolution_nonchat) / test_bearer_env_removed_boots_clean
    (tests/credential_resolution_seam) / test_M1_lists_seven_with_status
    (apps/dashboard/tests/provider-keys.test.tsx) — each pre-existing frozen assertion widened
    to include "minimax", never weakened
</test_plan>

RED CONFIRMED (2026-07-01): `tests/minimax_adapter_registry/` 12 failed / 1 correctly-green
negative-invariant (minimax absent from the non-chat registry pre-build, which is also the
correct post-build state) · the 3 widened backend suites each show exactly 1 new failure
(`[minimax]` parametrize case / the new set member) with all pre-existing provider cases still
green · the widened dashboard suite: 1 failed / 13 passed (unaffected). A pre-existing, unrelated
environment issue (an orphaned `tenant_model_presets` table from a parked worktree, blocking ALL
DB-bootstrap tests including the already-shipped `provider_config_admin_api` suite) was found and
cleared (Tin approved) before this red confirmation.

Tests live in: `./tests/`, `apps/gateway/tests/minimax_adapter_registry/`,
`apps/gateway/tests/dynamic_auth_byok/`, `apps/gateway/tests/credential_resolution_nonchat/`,
`apps/gateway/tests/credential_resolution_seam/`, `apps/dashboard/tests/provider-keys.test.tsx` ·
MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/`, `apps/dashboard/components/`, `apps/gateway/tests/`, `apps/dashboard/tests/`
Strategy (ordered batches):
  1. Widen the 3 provider-collection literals (provider_credentials.py) — the root value-set
     everything else keys off.
  2. Widen the 2 dispatch allow-lists (tenant_provider_key_store.py Bearer tuple,
     provider_keys_admin_router.py `_BEARER_PROVIDERS`).
  3. Generalize `OpenAIDirectProvider` with `provider_name` (4 call-sites: `_auth_headers`,
     `_maybe_inject_web_search`, `complete`'s `execute_with_retry` call — default arg keeps
     "openai" byte-identical).
  4. Add `Settings.minimax_base_url`; wire `_chat_adapters["minimax"]` in main.py (chat-only,
     mirrors the `_openai_direct` block, NOT added to the non-chat `_providers` dict).
  5. Dashboard: add "minimax" to `ProviderKeysSettings.PROVIDERS`/`DISPLAY` and
     `ConfigureProviderDialog.BEARER_PROVIDERS`/`LABELS`.
Known-problem fixes: hardcoded `"openai"` literal scattered across 4 call-sites in
  openai_provider.py → planned fix: single `self._provider_name` constructor field threaded
  through all 4, default `"openai"` for zero behavior change to the existing provider.
Strategy actually used: as planned (all 5 batches executed in order); one unplanned fixup
  during verification — 2 tests in test_minimax_adapter_registry.py built `OpenAIDirectProvider`
  via `__new__` (skipping `__init__`) and needed `_client`/`_breaker`/`_metrics_registry`
  manually stubbed to reach the real `complete()`/`_auth_headers()` control flow (matching the
  established PS8 `__new__` test-double pattern already used in test_openai_chat_dispatch.py);
  this was a test-scaffolding gap in this task's own RED-phase authoring, not a contract or
  production-code change.
Safety rule (feature-specific): registering "minimax" must not alter the default dispatch path
  for any of the other 6 providers — verified via full-suite regression run (2090 passed).
Code lives in: `apps/gateway/src/`, `apps/dashboard/components/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) is live: a completing verify gate refuses an
     out-of-scope build (scope_violation → self-heal) and add.py check surfaces it.
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

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] `PROVIDER_VALUE_SET`/`BYOK_PROVIDERS`/`ProviderName` all equal the same 7-member set — confirmed
      by reading provider_credentials.py:36-51 directly (all three literal/frozensets updated together).
- [x] `app.state.chat_adapters["minimax"]` exists at boot, is an `OpenAIDirectProvider` pointed at
      `https://api.minimax.io/v1` — confirmed by `test_minimax_chat_adapter_registered_unconditionally`
      passing against the real `create_app()` wiring (not a mock), and by reading main.py:705-713.
- [x] `minimax` is chat-only — absent from `app.state.provider_registry` (the non-chat map) — confirmed
      by `test_minimax_not_registered_for_non_chat_modalities` passing and by reading main.py (minimax
      wiring block is NOT added to the `_providers = {...}` dict near the bottom of create_app()).
- [x] `OpenAIDirectProvider("openai")`'s existing behavior is byte-identical (default arg) — confirmed
      by the full gateway regression suite (2090 passed, 0 failed) including openai_chat_dispatch,
      openai_retry_parity, web_search, provider_seam suites that exercise the default provider_name.
- [x] Admin PUT/GET/DELETE `/admin/provider-keys/minimax` round-trips a Bearer secret through real
      Fernet encrypt/decrypt — confirmed by `test_admin_put_minimax_key_roundtrip` /
      `test_admin_put_minimax_key_upsert_replaces` / `test_admin_delete_minimax_key` passing against
      the DB-backed `bootstrap_fresh_db` fixture (real Postgres, not mocked).
- [x] Dashboard settings page renders a 7th "MiniMax" row with a single-bearer-secret form (not the
      multi-field Azure/Bedrock form) — confirmed by `provider-keys.test.tsx` (14/14 passed, including
      the widened `test_M1_lists_seven_with_status`).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced: `Settings.minimax_base_url` is read exactly once
      at main.py:707 (`base_url=settings.minimax_base_url`); `OpenAIDirectProvider.provider_name` param
      is consumed by all 4 previously-hardcoded call sites (`_auth_headers`, `_maybe_inject_web_search`,
      `complete`'s `execute_with_retry(provider=...)`) — grepped openai_provider.py post-build for any
      remaining literal `"openai"` inside those 3 methods; none found (confirmed via
      `grep -n '"openai"' src/gateway/proxy/infrastructure/openai_provider.py` → only the
      `_DEFAULT_BASE_URL`/docstring/class-level-default lines remain, all intentional).
- [x] DEAD-CODE (code) — no new unused symbol: `_chat_adapters["minimax"]` is read by
      `ProviderAwareCompletionUpstream`/`select_provider` the same way every other adapter key is (no
      new dispatch path, reuses the existing dict-lookup seam) — confirmed by
      `test_select_provider_resolves_minimax` passing against the real `select_provider` function.
- [x] SEMANTIC (prose) — §0-§4 of this TASK.md and MILESTONE.md re-read in full at refute-read time;
      no drift found between the frozen §3 CONTRACT shape and what §5 BUILD actually produced.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: agent (independent adversarial subagent, isolated context, instructed to try to REFUTE) ·
adversarially checked: WIRING reachability of every new symbol from a real request path (traced
`_chat_adapters["minimax"]` → `app.state.chat_adapters` → `ProviderAwareCompletionUpstream.complete()`'s
`self._adapters.get(provider)` lookup — not orphaned); whether each hardcoded "openai" literal was
actually replaced (grep-verified post-build, none missed — only the intentional default-arg/class-attr
occurrences remain); whether the `__new__`-built test doubles exercise the real
`complete()`/`_auth_headers()` control flow rather than bypassing it (confirmed — `_auth_headers()` is
evaluated as a kwarg before any network call, so `ProviderKeyMissing` propagates unmodified, and its
message genuinely differs "openai" vs "minimax", not coincidentally); whether
`_parts_to_credential`+admin-router round-trip minimax through a REAL Fernet encrypt/decrypt against a
real Postgres DB (confirmed, not mocked); whether the dashboard vitest assertion targets the new row
specifically via `getByText(/minimax/i)` (not just a count bump); an exhaustive repo-wide grep (full
key + 20-char prefix) for the live MiniMax API key across every touched file plus
TASK.md/MILESTONE.md (absent everywhere); re-running every affected suite from raw output (51 backend +
14 dashboard, all green) rather than trusting a summary.

Non-blocking findings from the refute-read (neither is a code defect; both left open, not fixed here):
  1. [medium, environment] The orphaned `tenant_model_presets` table (documented at RED time, dropped
     with Tin's explicit approval) had recurred by VERIFY time and was dropped again by the refute-read
     agent on the local test DB only (no source/test file touched). Recommend a durable fix (e.g. a
     test-session schema reset) so this stops recurring across tasks — tracked as a follow-up, not a
     blocker for this task.
  2. [low, contract-text nit] §3 CONTRACT prose says the minimax wiring block goes "after azure"; the
     actual placement (main.py, immediately after the `openai` block) is functionally inert (plain dict,
     no order dependency, both adapters registered before any read) — a documentation wording mismatch
     only, not a functional defect.

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang (AI-driven build; human freeze-approval at §3 contract 2026-07-01) · date: 2026-07-01

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang (2026-07-01))
- [AI] build — strategy used: as planned (all 5 batches executed in order); one unplanned fixup
- [AI] verify — gate PASS (reviewed by Tin Dang (AI-driven build; human freeze-approval at §3 contract 2026-07-01))

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
