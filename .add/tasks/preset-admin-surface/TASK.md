# TASK: Tenant-scoped API + dashboard editor to create/list/delete named presets

slug: preset-admin-surface · created: 2026-07-01 · stage: production
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
- `apps/gateway/src/gateway/proxy/api/provider_keys_admin_router.py` — the v25 BYOK admin-API
  precedent to mirror: `APIRouter(prefix="/admin/provider-keys", tags=[...])` (L57). Auth is a
  session-JWT/RBAC dependency chain, NOT the proxy's raw `sk-` key path: `get_bearer_token(request)`
  (`tenants/api/deps.py:48-53`) extracts the header → `GetIdentityUseCase(app.state.token_service)
  .execute(token)` (`tenants/application/use_cases.py:44-48`) decodes the JWT into `Identity
  (user_id, tenant_id, email, role)` (`tenants/domain/entities.py:26-33`) → `_require_owner_identity`
  /`_require_owner_tenant_id` (`provider_keys_admin_router.py:96-120`) enforce `role == OWNER` (403
  `AUTH_FORBIDDEN_OWNER_REQUIRED` else) and return `identity.tenant_id: UUID` — the ONLY source of
  tenant_id; never a client-supplied body/query/path value (stated as a security invariant in the
  router's own docstring L12-13). New preset endpoints reuse this same dependency, not a new one.
- `list_provider_keys`/`delete_provider_key` (`provider_keys_admin_router.py:238-244`, `:259-269`)
  are the template shape:
  ```python
  @provider_keys_admin_router.get("")
  async def list_provider_keys(request: Request) -> dict[str, list[ProviderKeyStatus]]:
      tenant_id = _require_owner_tenant_id(request)
      store = request.app.state.tenant_provider_key_store
      return {"keys": await store.list(tenant_id)}
  ```
- Errors: a global `ProblemError` exception handler (`core/errors.py:39-45`, registered via
  `register_error_handlers(app)` at `main.py:923`) auto-converts any raised `ErrorSpec.exc()` into
  an RFC-9457 problem+json response — no manual mapping needed for THOSE. But
  `ModelPresetError` (raised by `DbTenantModelPresetStore.upsert`, `proxy/domain/model_presets.py:
  46-56`) is a plain `Exception` with only `.code`, NOT a `ProblemError` — no handler exists for it.
  Precedent (`put_provider_key`, `provider_keys_admin_router.py:198-210`): catch it, manually
  `if/elif exc.code == "ERR_..."` map to the matching `ErrorSpec.exc() from None` (fallback
  `INTERNAL_ERROR.exc() from None`). New preset endpoints must do the same for
  `ERR_PRESET_SELECTOR_INVALID`/`ERR_PRESET_TARGET_UNKNOWN` — zero existing call sites do this
  mapping today (grep-confirmed).
- `DELETE` semantics precedent CONFLICT (needs a decision, see §1 ⚠): `DbTenantModelPresetStore
  .delete(tenant_id, preset_name, alias_key) -> bool` (`tenant_model_preset_store.py:180-200`) is
  documented + implemented as idempotent — "return True iff a row existed", no exception on a miss.
  But the existing BYOK admin HTTP layer (`delete_provider_key`, `provider_keys_admin_router.py:
  259-269`) chose to surface a 404 (`PROVIDER_KEY_NOT_FOUND.exc()`) when the store returns `False`,
  even though nothing forced that choice — and the DASHBOARD already special-cases this as
  idempotent client-side (`ProviderKeysSettings.tsx` `deleteMutation.onError`, comment: "Delete is
  idempotent server-side (404 → already gone)", swallows the error). Reusing `PRESET_NOT_FOUND`
  (`core/error_catalog.py:595-597`) for this would be WRONG regardless of the idempotency choice —
  it's explicitly scoped (own comment L590-594) to request-ingress selector resolution (400,
  13 call sites all in the proxy use cases) — a different failure semantic and status code than
  "no such admin row" would need.
- Frontend: Next.js App Router page `apps/dashboard/app/(app)/app/settings/page.tsx` → thin wrapper
  importing `SettingsPage` (`components/settings/SettingsPage.tsx:1-52`), a `"use client"` tabbed
  hub (`Tabs/TabsList/TabsTrigger/TabsContent` from `@/components/ui`) hosting
  `<ProviderKeysSettings />` in one tab. API-client: `bffGet/bffPost/bffPut/bffDelete`
  (`lib/bff-client.ts:93-143`), same-origin `/api/gw/<path>`, `credentials:"include"` — no client
  Authorization header; the server-side proxy (`app/api/gw/[...path]/route.ts:1-23`) reads the
  `ai_proxy_session` cookie and attaches the JWT server-side (ties to the backend's
  `_require_owner_identity` decode above). TanStack Query: `useQuery`/`useMutation` +
  `queryClient.invalidateQueries` on success (`ProviderKeysSettings.tsx:57-88`).
- Better structural UI analog than provider-keys (since presets ARE a dynamic, user-named,
  creatable/deletable list, unlike provider-keys' fixed 6-provider enum):
  `components/keys/KeysPage.tsx` + `components/keys/KeyRow.tsx` — `useQuery(["admin-keys"], () =>
  bffGet<ApiKey[]>("/admin/keys"))` (L112-114), `createKeyMutation`/`revokeKeyMutation`
  (`bffPost`/`bffDelete` + invalidate, L118-131), rendered via `Table/TableHeader/TableBody/TableRow`
  with one `<KeyRow>` per item (a `TableRow` with cells + an `onRevoke` callback prop).

Context (working folder): no client ever supplies `tenant_id` directly — always derived from the
decoded session JWT (`identity.tenant_id`), matching this task's Must (tenant-scoped, no client-
trusted tenant value). `preset-resolution-ingress` (done) already consumes
`app.state.tenant_model_preset_store` at ingress; this task is the FIRST thing that can ever
populate a real preset row in production (no admin-write path existed before).

Honors (patterns / conventions): mirror the BYOK admin-API auth/URL/error-mapping shape exactly
(`/admin/<resource>` prefix, `_require_owner_tenant_id`-equivalent, manual `ModelPresetError` →
`ErrorSpec` mapping) rather than inventing a new admin-API idiom. Frontend: `KeysPage`/`KeyRow`'s
dynamic-list-with-Table pattern is the closer analog than `ProviderKeysSettings`'s fixed-enum
pattern — mirror that shape, not the tab-hosted fixed list.

Anchors the contract cites: `provider_keys_admin_router.py`'s auth-dependency chain (reused
verbatim), `TenantModelPresetStore.upsert/list/delete` (existing port, no changes), new Pydantic
request/response schemas, new router file + `main.py` registration, new `ModelPresetError` →
`ErrorSpec` mapping, new frontend page/component mirroring `KeysPage`/`KeyRow`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Tenant-scoped admin API (list/upsert/delete named presets) + a new top-level dashboard
page (`/app/presets`) to manage them — the FIRST thing that can ever populate a real preset row.
Framings weighed:
- New top-level page mirroring `KeysPage`/`KeyRow` (chosen, Tin 2026-07-01 via AskUserQuestion) —
  presets are a dynamic, user-named, creatable/deletable list, same shape as API keys.
- Settings tab mirroring `ProviderKeysSettings` — rejected: that pattern fits a fixed small enum
  (6 hardcoded providers), not an arbitrary tenant-defined list.
Must:
<must>
  - Tenant identity comes ONLY from the decoded session JWT (the SAME `_require_owner_identity`/
    `_require_owner_tenant_id` dependency chain `provider_keys_admin_router.py` already uses) —
    never a client-supplied body/query/path `tenant_id`. Role gate: OWNER only, matching the
    identical BYOK precedent (403 `AUTH_FORBIDDEN_OWNER_REQUIRED` otherwise).
  - `GET /admin/presets` — list all presets for the calling tenant (`store.list(tenant_id)`,
    unchanged port). Response: `{"presets": [TenantModelPreset, ...]}` (the existing dataclass,
    returned directly as the response type — matches the `ProviderKeyStatus` precedent).
  - `PUT /admin/presets/{preset_name}/{alias_key}` — upsert (create-or-update) a preset, path
    params mirroring `provider_keys_admin_router.py`'s `PUT /admin/provider-keys/{provider}`
    style (Tin's choice via AskUserQuestion 2026-07-01, overriding the v1 JSON-body draft). Body:
    `{target_model}` only. Calls the existing `store.upsert(tenant_id, preset_name, alias_key,
    target_model)` unchanged. Response: the resulting `TenantModelPreset`. A new router-local
    guard rejects `preset_name`/`alias_key` containing "/" with the existing
    `ERR_PRESET_SELECTOR_INVALID` (400) BEFORE the path is used to address a row — see ⚠ below.
  - `DELETE /admin/presets/{preset_name}/{alias_key}` — idempotent delete, no body. Calls the
    existing `store.delete(tenant_id, preset_name, alias_key) -> bool` unchanged; ALWAYS returns
    204 No Content regardless of whether a row existed (Tin's choice via AskUserQuestion — matches
    the store's own idempotent contract, no new 404 error needed). Same "/" guard as PUT.
  - `ModelPresetError` (raised by `store.upsert` for `ERR_PRESET_SELECTOR_INVALID`/
    `ERR_PRESET_TARGET_UNKNOWN`) is caught and manually mapped to its matching `ErrorSpec.exc()`
    (both already exist in `error_catalog.py`) — same pattern as `put_provider_key`'s existing
    `ModelPresetError`→`ErrorSpec` mapping; fallback `INTERNAL_ERROR.exc() from None` for any
    unmapped code.
  - Dashboard page `/app/presets`: a create form (preset_name/alias_key/target_model text inputs)
    + a table of the tenant's existing presets (columns: preset_name, alias_key, target_model,
    updated_at, delete action) — mirrors `KeysPage`/`KeyRow`'s `Table`+row-component shape and
    `useQuery`/`useMutation`+`invalidateQueries` data-flow, via the existing `bffGet/bffPut/
    bffDelete` client (no new API-client idiom).
</must>
Reject:
<reject>
  - preset_name/alias_key empty, >64 chars, or containing ":" -> "ERR_PRESET_SELECTOR_INVALID" (400)
    (existing store validation, unchanged — this task adds no new validation rule)
  - target_model not an active catalog model -> "ERR_PRESET_TARGET_UNKNOWN" (400) (existing store
    validation, unchanged)
  - caller's session role != OWNER -> "ERR_AUTH_FORBIDDEN_OWNER_REQUIRED" (403) (existing dependency,
    unchanged)
  - missing/invalid/expired session token -> "ERR_AUTH_TOKEN_MISSING" / "ERR_AUTH_TOKEN_INVALID"
    (401) (existing dependency, unchanged)
</reject>
After:
<after>
  - A tenant OWNER can create a preset via `PUT /admin/presets` and see it immediately in
    `GET /admin/presets` and in the `/app/presets` table.
  - A tenant OWNER can delete a preset via `DELETE /admin/presets`; deleting it twice in a row both
    return 204 (idempotent — no error the second time).
  - Tenant B's session can never see, create, or delete Tenant A's presets (tenant_id is derived
    server-side from the session JWT only).
  - `preset-resolution-ingress` (done, shipped) picks up newly-created presets immediately on the
    next request — no cache/restart needed (the store has no caching layer).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ preset_name/alias_key are arbitrary tenant-chosen strings up to 64 chars that the store's
    write-time validator permits to contain "/" (only ":" is forbidden, per the frozen
    tenant-preset-store contract) — putting them in the URL PATH as `{preset_name}/{alias_key}`
    (mirroring provider-keys' `PUT /admin/provider-keys/{provider}`, Tin's explicit choice via
    AskUserQuestion 2026-07-01, overriding my v1 JSON-body draft) is genuinely ambiguous/unsafe for
    a value containing "/" (a well-known encoded-slash hazard — whether a percent-encoded "/" is
    decoded before or after route-segment matching is server/version-dependent, not something to
    rely on). RESOLVED: this task adds a NEW, router-local guard — reject `preset_name`/`alias_key`
    containing "/" with the EXISTING `ERR_PRESET_SELECTOR_INVALID` (400), enforced before the path
    is ever used to address a row. Since this task is the FIRST-EVER writer for this table (§0
    GROUND — no admin-write path existed before), no preset can ever be created with "/" in it
    going forward, making the path-param route safe by construction without touching the
    already-shipped tenant-preset-store validator. Tin's own AskUserQuestion answer explicitly
    named this as an acceptable resolution ("additionally forbid '/' going forward"). Residual
    risk: a future SECOND writer that bypasses this router (e.g. a bulk-import script calling
    `store.upsert` directly) could still write a "/"-containing row, silently reintroducing the
    hazard for that one row's PUT/DELETE addressability — out of this task's scope to guard against
    a writer that doesn't exist yet.
  - [ ] a plain free-text `target_model` input (vs. a catalog-backed autocomplete/dropdown) is
    sufficient for this task's MVP scope — confirm or deny; if a tenant wants better UX (avoiding a
    guess-and-check `ERR_PRESET_TARGET_UNKNOWN` loop), that is a real, separate frontend enhancement
    (cost: a §7 Spec delta, not a blocker for this task, per "don't gold-plate beyond what's asked").
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: create then list round-trip
  Given tenant T's OWNER session, no presets yet
  When PUT /admin/presets/cheap/opus {target_model: "gpt5-5-mini"}
  Then the response is 200 with the created TenantModelPreset
  And GET /admin/presets returns exactly that one preset for tenant T

Scenario: upsert updates an existing preset in place
  Given tenant T has preset (cheap, opus) -> "gpt5-5-mini"
  When PUT /admin/presets/cheap/opus {target_model: "gpt5-5"}
  Then the response is 200 with target_model "gpt5-5"
  And GET /admin/presets shows exactly one row for (cheap, opus), now pointing at "gpt5-5"

Scenario: delete removes a preset
  Given tenant T has preset (cheap, opus) -> "gpt5-5-mini"
  When DELETE /admin/presets/cheap/opus
  Then the response is 204
  And GET /admin/presets no longer includes that preset

Scenario: delete is idempotent
  Given tenant T has no preset named (cheap, opus)
  When DELETE /admin/presets/cheap/opus
  Then the response is 204 (not an error)
  And GET /admin/presets is unaffected (still empty for that pair)

Scenario: tenant isolation on list
  Given tenant A has preset (cheap, opus) -> "model-a"; tenant B has no presets
  When tenant B's OWNER session calls GET /admin/presets
  Then the response is 200 with an empty presets list
  And tenant A's preset is never returned to tenant B

Scenario: tenant isolation on delete
  Given tenant A has preset (cheap, opus) -> "model-a"; tenant B has no such preset
  When tenant B's OWNER session calls DELETE /admin/presets/cheap/opus
  Then the response is 204 (idempotent — looks like "already gone" from B's perspective)
  And tenant A's preset (cheap, opus) -> "model-a" still exists, untouched

Scenario: reject invalid selector (colon)
  Given tenant T's OWNER session
  When PUT /admin/presets/cheap%3A/opus {target_model: "gpt5-5"}
  Then the response is 400 ERR_PRESET_SELECTOR_INVALID
  And no preset row is created

Scenario: reject preset_name or alias_key containing a slash
  Given tenant T's OWNER session
  When PUT /admin/presets/cheap%2Fteam/opus {target_model: "gpt5-5"}
  Then the response is 400 ERR_PRESET_SELECTOR_INVALID (router-local "/" guard, NEW this task)
  And no preset row is created
  And the same guard applies to DELETE /admin/presets/{preset_name}/{alias_key}

Scenario: reject unknown target model
  Given tenant T's OWNER session
  When PUT /admin/presets/cheap/opus {target_model: "not-a-real-model"}
  Then the response is 400 ERR_PRESET_TARGET_UNKNOWN
  And no preset row is created

Scenario: reject non-owner role
  Given tenant T's session with role != OWNER
  When PUT /admin/presets/cheap/opus {target_model: "gpt5-5"}
  Then the response is 403 ERR_AUTH_FORBIDDEN_OWNER_REQUIRED
  And no preset row is created

Scenario: reject missing/invalid session token
  Given no Authorization header (or an undecodable one)
  When GET /admin/presets is called
  Then the response is 401 ERR_AUTH_TOKEN_MISSING (or ERR_AUTH_TOKEN_INVALID)
  And no tenant's presets are ever considered

Scenario: newly-created preset is immediately usable at ingress
  Given tenant T creates preset (cheap, opus) -> "gpt5-5-mini" via PUT /admin/presets/cheap/opus
  When tenant T immediately sends a chat completion with model: "cheap:opus"
  Then the request resolves to "gpt5-5-mini" (preset-resolution-ingress, already shipped) with no
    restart or cache-invalidation step needed

Scenario: dashboard page renders and manages presets end to end
  Given tenant T's OWNER is on /app/presets with zero presets
  When they submit the create form (preset_name, alias_key, target_model)
  Then the new preset appears in the table without a full page reload (query invalidation)
  And clicking delete on a row removes it from the table the same way
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
New router (gateway/proxy/api/presets_admin_router.py) — mirrors
provider_keys_admin_router.py's auth/error shape exactly:
  presets_admin_router = APIRouter(prefix="/admin/presets", tags=["presets-admin"])
  # reuses provider_keys_admin_router.py's _require_owner_identity/_require_owner_tenant_id
  # (imported, not re-implemented) — same OWNER-only, session-JWT-derived tenant_id.

GET /admin/presets
  200 -> { "presets": [TenantModelPreset, ...] }   # existing dataclass, returned directly
  401 -> { error: "ERR_AUTH_TOKEN_MISSING" | "ERR_AUTH_TOKEN_INVALID" }

PUT /admin/presets/{preset_name}/{alias_key}   body: { target_model: str }
  200 -> TenantModelPreset   # the resulting row
  400 -> { error: "ERR_PRESET_SELECTOR_INVALID" | "ERR_PRESET_TARGET_UNKNOWN" }
  401 -> { error: "ERR_AUTH_TOKEN_MISSING" | "ERR_AUTH_TOKEN_INVALID" }
  403 -> { error: "ERR_AUTH_FORBIDDEN_OWNER_REQUIRED" }

DELETE /admin/presets/{preset_name}/{alias_key}   (no body)
  204 -> (no body; ALWAYS 204, idempotent — Tin's freeze decision, no new 404 error)
  400 -> { error: "ERR_PRESET_SELECTOR_INVALID" }   # NEW: same "/" guard as PUT, see below
  401 -> { error: "ERR_AUTH_TOKEN_MISSING" | "ERR_AUTH_TOKEN_INVALID" }
  403 -> { error: "ERR_AUTH_FORBIDDEN_OWNER_REQUIRED" }

Path-segment safety (NEW, router-local, additive — does NOT touch the already-shipped
tenant-preset-store validator): before calling store.upsert/delete, reject a preset_name or
alias_key containing "/" with the EXISTING ERR_PRESET_SELECTOR_INVALID (400) — the same error
the store already raises for empty/>64-char/":"-containing tokens, just enforced one layer
higher, at the only admin-write entry point that can ever create a row (§0 GROUND: this task is
the first-ever writer for this table). This makes the {preset_name}/{alias_key} path-param route
safe by construction — no preset can ever be created with "/" in it, so no encoded-slash
ambiguity is ever reachable in practice:
  def _reject_slash(value: str) -> None:
      if "/" in value:
          raise PRESET_SELECTOR_INVALID.exc() from None

Pydantic request model (new, in the router file):
  class PresetUpsertBody(BaseModel):
      target_model: str
  # preset_name/alias_key now come from the path, not the body — DELETE has no body at all.

Error mapping (new, in the router file — mirrors put_provider_key's ModelPresetError handling):
  @presets_admin_router.put("/{preset_name}/{alias_key}")
  async def upsert_preset(
      preset_name: str, alias_key: str, body: PresetUpsertBody, request: Request
  ) -> TenantModelPreset:
      tenant_id = _require_owner_tenant_id(request)
      _reject_slash(preset_name)
      _reject_slash(alias_key)
      store = request.app.state.tenant_model_preset_store
      try:
          return await store.upsert(tenant_id, preset_name, alias_key, body.target_model)
      except ModelPresetError as exc:
          if exc.code == "ERR_PRESET_SELECTOR_INVALID":
              raise PRESET_SELECTOR_INVALID.exc() from None
          if exc.code == "ERR_PRESET_TARGET_UNKNOWN":
              raise PRESET_TARGET_UNKNOWN.exc() from None
          raise INTERNAL_ERROR.exc() from None

  @presets_admin_router.delete("/{preset_name}/{alias_key}", status_code=204)
  async def delete_preset(preset_name: str, alias_key: str, request: Request) -> None:
      tenant_id = _require_owner_tenant_id(request)
      _reject_slash(preset_name)
      _reject_slash(alias_key)
      store = request.app.state.tenant_model_preset_store
      await store.delete(tenant_id, preset_name, alias_key)
      # always 204 — idempotent, no existence check surfaced

main.py registration (ADDITIVE, alongside every other admin router):
  from gateway.proxy.api.presets_admin_router import presets_admin_router
  app.include_router(presets_admin_router)

Frontend (ADDITIVE, mirrors KeysPage.tsx/KeyRow.tsx exactly):
  apps/dashboard/app/(app)/app/presets/page.tsx        — thin wrapper, imports PresetsPage
  apps/dashboard/components/presets/PresetsPage.tsx    — useQuery(["admin-presets"], () =>
    bffGet<{presets: Preset[]}>("/admin/presets")); create form; upsertMutation (bffPut to
    `/admin/presets/${encodeURIComponent(preset_name)}/${encodeURIComponent(alias_key)}` with
    body {target_model}) + deleteMutation (bffDelete, same path-templated URL, no body) both
    invalidateQueries(["admin-presets"]) on success; renders Table/TableHeader/TableBody + one
    <PresetRow> per item
  apps/dashboard/components/presets/PresetRow.tsx      — TableRow: preset_name, alias_key,
    target_model, updated_at cells + a delete button (onDelete callback prop, mirrors KeyRow's
    onRevoke), free-text inputs only (no catalog-backed model picker — §1 ⚠, deferred); the
    create form rejects a "/" in preset_name/alias_key client-side too (mirrors the server-side
    guard — cheap UX win, server remains the enforced source of truth)

Schema: none (reuses tenant_model_presets table + upsert/list/delete from tenant-preset-store;
no new table/column/migration).
```

Least-sure flag surfaced at freeze: [spec] the new router-local "/" rejection guard on
preset_name/alias_key — the point most likely to surprise a future reader, since it's validation
that exists ONLY at this HTTP boundary, not in the already-shipped tenant-preset-store domain
layer (that layer still technically permits "/" at the store/DB level; only this router's write
path forbids it, to keep path-param routing unambiguous). Why riskiest: a future SECOND writer
that calls `store.upsert` directly (bypassing this router — e.g. a bulk-import script or a future
internal tool) could still write a "/"-containing row, reintroducing the encoded-slash hazard for
that one row's PUT/DELETE addressability via this API (GET/list would still return it fine). Cost
if wrong: low today — this task is the first-ever writer for this table (§0 GROUND), so no such
row can exist yet; the residual risk only materializes if a future task adds a second writer
without also adding this same guard, which is that future task's responsibility, not retroactive
scope here. Secondary [spec] confirmed at freeze (AskUserQuestion): DELETE is unconditionally
idempotent (204 always), matching the store's own contract rather than provider-keys' 404-on-miss
choice — deliberately NOT reusing PRESET_NOT_FOUND (wrong status/semantic, see §0 GROUND).

Status: FROZEN @ v2 — v1 (JSON-body PUT/DELETE) was presented via AskUserQuestion and Tin
REJECTED it 2026-07-01, choosing path params to mirror provider-keys' `PUT /admin/provider-keys/
{provider}` precedent instead, explicitly accepting the tradeoff of needing the "/" handled
("additionally forbid '/' going forward"). v2 (this version) resolves that via the new
router-local guard above. Approved by Tin Dang 2026-07-01: idempotent DELETE; new top-level
/app/presets page mirroring KeysPage; path-param PUT/DELETE with a "/" guard. Error-mapping shape
(which ModelPresetError code maps to which ErrorSpec) remains my own engineering call, unchanged
from v1 and not separately asked, since Tin's feedback was scoped to the path-vs-body transport
shape only.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: every Must/Reject row in §1 + every §2 scenario has one asserting test.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_create_then_list_round_trip: PUT /admin/presets/cheap/opus {target_model} -> 200 row;
    GET /admin/presets -> that one row for tenant T
  - test_upsert_updates_existing_preset_in_place: PUT twice, same path, different target_model ->
    200 both times; GET shows exactly one row with the second target_model
  - test_delete_removes_a_preset: PUT then DELETE /admin/presets/cheap/opus -> 204; GET no longer
    includes it
  - test_delete_is_idempotent: DELETE on a never-created (preset_name, alias_key) -> 204, not 404;
    calling it twice in a row both 204
  - test_tenant_isolation_on_list: tenant A creates a preset; tenant B's GET /admin/presets -> 200
    empty list, A's row never appears
  - test_tenant_isolation_on_delete: tenant A has a preset; tenant B's DELETE on the same
    (preset_name, alias_key) -> 204 (idempotent from B's view); A's row still exists unchanged
  - test_reject_invalid_selector_colon: PUT with a preset_name/alias_key containing ":" -> 400
    ERR_PRESET_SELECTOR_INVALID, no row created
  - test_reject_slash_in_preset_name: PUT /admin/presets/cheap%2Fteam/opus -> 400
    ERR_PRESET_SELECTOR_INVALID (router-local guard), no row created
  - test_reject_slash_in_alias_key: same guard, slash in the alias_key segment instead
  - test_reject_slash_on_delete: DELETE with a "/"-containing segment -> 400
    ERR_PRESET_SELECTOR_INVALID (guard applies to both verbs)
  - test_reject_unknown_target_model: PUT target_model not in the active catalog -> 400
    ERR_PRESET_TARGET_UNKNOWN, no row created
  - test_reject_non_owner_role: session role != OWNER -> 403 ERR_AUTH_FORBIDDEN_OWNER_REQUIRED on
    PUT/DELETE/GET, no row created/returned
  - test_reject_missing_or_invalid_session_token: no/garbage Authorization header -> 401
    ERR_AUTH_TOKEN_MISSING / ERR_AUTH_TOKEN_INVALID before any tenant is considered
  - test_ingress_resolves_newly_created_preset_immediately: create via the store (same path the
    router uses) then resolve via preset-resolution-ingress's own resolver in the same test run —
    no cache/restart step; proves the two already-shipped/this-task layers compose
  - frontend presets.test.tsx (RTL, mirrors keys.test.tsx): renders empty state; submitting the
    create form calls bffPut with the path-templated URL and shows the new row without reload;
    clicking delete on a row calls bffDelete and removes the row; a client-side "/" in an input
    is rejected before any network call (mirrors the server-side guard)
</test_plan>

Tests live in: `apps/gateway/tests/presets_admin_surface/` (backend, new dir) and
`apps/dashboard/tests/presets.test.tsx` (frontend, new file) · MUST run red (missing
implementation) before Build.

SCOPE ADDENDUM 1 (found during review, fixed before advancing past tests): the original §3/§5
Frontend file list never mentioned `apps/dashboard/components/ui/app-shell.tsx` — but a "new
top-level page" that isn't registered in `NAV_ITEMS` is unreachable from the sidebar, which fails
this project's own "genuinely usable product surface" bar. Initially added a plain (no-`minRole`)
entry mirroring `/app/keys`'s nav shape — but an adversarial refute-read (verdict NOT-EARNED)
correctly caught that this was a WRONG precedent match: `/app/keys`'s own backend (`list_keys`,
`keys/api/router.py:238-244`) uses unrestricted `get_identity` (any role), while presets requires
strict `role == OWNER` (`provider_keys_admin_router.py:109`) — stricter than every existing
`minRole:"admin"` link (which is owner-OR-admin). Fixed (Tin's decision via AskUserQuestion): added
a NEW `minRole: "owner"` tier to `NavItem` + `visibleItems()`'s filter (`app-shell.tsx`), hiding the
link from member AND admin, visible only to owner (fails open for unknown/loading roles, matching
the existing convention). Updated `apps/dashboard/tests-bff/nav-role-filter.test.tsx` (a
cross-cutting nav-count regression test from the v17 `nav-role-filter` task, not one of this task's
own declared test files) to reflect the corrected gating: member stays 10 (unchanged — presets was
never actually visible to them), admin stays 18 (unchanged — presets correctly stays hidden, unlike
every prior admin-tier addition), owner/unknown 18→19. Done while still at phase `tests` (no
snapshot taken yet), so no tamper-tripwire risk. Full dashboard suite re-confirmed green (910/0)
and `tsc --noEmit` clean after this fix.

SCOPE ADDENDUM 2 (found by the same refute-read, fixed before advancing past tests): the refute-read
also reproduced a genuine, intermittent Postgres `DeadlockDetectedError` (~25-30% of standalone
runs) in `tests/presets_admin_surface/`, and I independently confirmed the IDENTICAL deadlock also
occurs in the sibling `tests/tenant_model_presets/` suite (the already-shipped `tenant-preset-store`
task) — a pre-existing, repo-wide characteristic of the `bootstrap_fresh_db` per-test full
drop_all/create_all pattern against the shared `gateway_test` database, not a new regression from
this build. Tin chose to fix it now (not defer), even though it also touches already-shipped code.
Fix: (1) added a bounded retry-on-deadlock (SQLSTATE 40P01, 3 attempts, jittered backoff) around
`DbTenantModelPresetStore.upsert`/`.delete`'s DB write in `tenant_model_preset_store.py` (production
code — a legitimate design-for-failure hardening, not just a test workaround: any transient deadlock
under concurrent writes to the same table would hit this in production too, and retrying is
PostgreSQL's own documented recommendation); (2) the same retry pattern wrapped around the DDL
bootstrap step in both `test_presets_admin_surface.py` and the sibling (already-shipped)
`test_tenant_model_preset_store_db.py`. No method signature, validation order, or error semantic
changed — only added resilience around the existing write. Verified: 8 consecutive back-to-back
runs of both suites together, 0 failures (previously ~25-30% failure rate); full gateway suite
re-confirmed green (2110 passed, 0 failed) after this fix; pyright/ruff clean on all 3 touched files.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
  `apps/gateway/src/gateway/proxy/api/presets_admin_router.py` (NEW)
  `apps/gateway/src/gateway/main.py` (router registration only, additive)
  `apps/gateway/tests/presets_admin_surface/` (NEW dir)
  `apps/dashboard/app/(app)/app/presets/page.tsx` (NEW)
  `apps/dashboard/components/presets/PresetsPage.tsx` (NEW)
  `apps/dashboard/components/presets/PresetRow.tsx` (NEW)
  `apps/dashboard/tests/presets.test.tsx` (NEW)
  `apps/dashboard/components/ui/app-shell.tsx` (SCOPE ADDENDUM 1 — new `minRole:"owner"` nav tier)
  `apps/dashboard/tests-bff/nav-role-filter.test.tsx` (SCOPE ADDENDUM 1 — count/tier updates)
  `apps/gateway/src/gateway/proxy/infrastructure/tenant_model_preset_store.py` (SCOPE ADDENDUM 2 —
    deadlock-retry hardening; already-shipped `tenant-preset-store` file, Tin-authorized)
  `apps/gateway/tests/tenant_model_presets/test_tenant_model_preset_store_db.py` (SCOPE ADDENDUM 2 —
    same deadlock-retry, sibling already-shipped test file, Tin-authorized)
Strategy (ordered batches):
  1. Backend: new router file (GET/PUT/DELETE + `_reject_slash` guard + `ModelPresetError`
     mapping), wired in `main.py`, red tests first in `apps/gateway/tests/presets_admin_surface/`.
  2. Frontend: `PresetsPage`/`PresetRow` mirroring `KeysPage`/`KeyRow` + `presets.test.tsx`
     mirroring `keys.test.tsx`, red first.
  Independent surfaces (no shared files) — safe to build in parallel.
Known-problem fixes:
  - trap: forgetting the "/" guard on DELETE too (only remembering PUT) -> planned fix: apply
    `_reject_slash` identically in both handlers, one test per verb (§4).
  - trap: `ModelPresetError` escaping unmapped (no global handler for it, per §0 GROUND) -> planned
    fix: explicit try/except around every `store.upsert`/`store.delete` call, `INTERNAL_ERROR`
    fallback for any unmapped code, mirroring `put_provider_key`.
  - trap: reusing `PRESET_NOT_FOUND` for the admin DELETE-miss case -> planned fix: DELETE never
    raises on a miss at all (always 204), so this error is never reachable from this router.
Strategy actually used: as planned (backend + frontend built in parallel by two subagents against
  the frozen v2 contract, independent surfaces, no file collisions), PLUS an unplanned review/fix
  round: personal manual diff review of both surfaces (not just trusting the build agents' self-
  reports) found the nav-registration gap (SCOPE ADDENDUM 1) before any adversarial pass even ran;
  an adversarial refute-read subagent then returned NOT-EARNED, surfacing the corrected (owner-tier,
  not no-`minRole`) fix for that same gap plus the independently-reproduced deadlock flake (SCOPE
  ADDENDUM 2); both fixed and re-verified (8x stress runs + full suite) before this gate.
Safety rule (feature-specific): tenant_id is derived ONLY from `_require_owner_tenant_id(request)`
  (decoded session JWT) — never accepted from a path/query/body value, on every handler.
Code lives in: `apps/gateway/src/gateway/proxy/api/` (backend) · `apps/dashboard/components/presets/`
  + `apps/dashboard/app/(app)/app/presets/` (frontend)
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

- [x] all tests pass — gateway 2110 passed/7 skipped/0 failed (independently re-run by me, not just
  the build agents' self-report); dashboard 910 passed/0 failed; both re-confirmed after every fix
- [x] coverage did not decrease — gateway 87.89% (>= 80% floor); dashboard suite unchanged shape
- [x] no test or contract was altered during build — the `nav-role-filter.test.tsx` and
  `test_tenant_model_preset_store_db.py` edits are pre-existing tests outside THIS task's own §4
  plan; both are legitimate count/resilience updates (real new nav link; real deadlock hardening),
  not weakened assertions — done while still at phase `tests`, before any snapshot, so no
  tamper-tripwire applies
- [x] the green was EARNED, not gamed — adversarial refute-read run (see verdict below); returned
  NOT-EARNED on the first pass with 2 real findings, both fixed and re-verified
- [x] concurrency / timing of the risky operation is safe — the refute-read's TOCTOU finding (see
  below) is accepted as a known, low-severity, precedent-inherited pattern, not a blocker; the
  independently-reproduced deadlock flake WAS fixed (retry-on-deadlock, 8x stress-verified)
- [x] no exposed secrets, injection openings, or unexpected dependencies — tenant_id derivation,
  "/" guard, and error mapping all independently re-verified by the refute-read and by me
- [x] layering & dependencies follow CONVENTIONS.md — router mirrors the BYOK admin-API precedent;
  no new architectural layer introduced
- [x] a person reviewed and approved the change — Tin approved the v2 contract (path params + "/"
  guard) and both post-refute-read fix decisions (owner nav tier; fix deadlock now) via
  AskUserQuestion; the AI performed the line-level code review per this repo's standing instruction
  (manual diff read of both surfaces + an independent adversarial refute-read before this gate)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] a tenant OWNER can PUT/GET/DELETE their own presets by path, never another tenant's —
  confirmed by the tenant-isolation tests passing AND a manual read of every handler confirming
  `tenant_id` comes only from `_require_owner_tenant_id(request)` (also independently re-traced by
  the refute-read agent, which tried and failed to find a bypass)
- [x] no preset row can ever be created with "/" in its name/alias (both PUT and DELETE reject it
  identically) — confirmed by the two slash-guard tests + a manual read of both handlers; the
  refute-read additionally verified the percent-encoded-slash ASGI-routing edge case empirically
- [x] DELETE is unconditionally 204, never 404, on a miss — confirmed by the idempotent-delete test
- [x] `ModelPresetError` never escapes the router unmapped — confirmed by a manual read showing
  every `store.upsert` call site wrapped in try/except with an `INTERNAL_ERROR` fallback; refute-read
  confirmed the store only ever raises the two mapped codes today (fallback currently unreachable,
  not a leak path)
- [x] a newly created preset is usable at ingress with zero extra steps — confirmed by the
  ingress-composition test calling the real store + the real resolver in one test
- [x] the dashboard page creates/lists/deletes without a full reload — confirmed by
  `presets.test.tsx` asserting query invalidation, not a page navigation
- [x] a non-owner tenant member never lands on a broken nav destination — REGRESSION FOUND by the
  refute-read (nav link visible+broken for non-owners) and fixed (owner-only nav tier); confirmed by
  the updated `nav-role-filter.test.tsx` assertions (member/admin never see the link)
- [x] the admin API's own write path never intermittently fails on a transient DB condition —
  REGRESSION FOUND by the refute-read (reproducible Postgres deadlock) and fixed (retry-on-deadlock
  in the store); confirmed by 8x back-to-back stress runs with 0 failures (was ~25-30%)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `presets_admin_router` referenced in `main.py`'s `app.include_router`; the new
  `NavItem.minRole:"owner"` tier referenced by `visibleItems()`'s filter and by the `/app/presets`
  entry; `_retry_on_deadlock` referenced by both `upsert`/`delete`; every new symbol traced to a
  caller, no orphans
- [x] DEAD-CODE (code) — none introduced; the `INTERNAL_ERROR` fallback branch in `upsert_preset` is
  intentionally defensive/currently-unreachable (documented, not dead — mirrors precedent's own
  `# pragma: no cover` style for the same reason)
- [ ] SEMANTIC (prose / non-code) — n/a, no prose-only artifact in this task's scope

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED (after one round-trip: first pass was NOT-EARNED — 2 real findings [nav/role gap,
reproducible Postgres deadlock] plus 1 accepted-as-is finding [TOCTOU in upsert-then-fetch, inherited
from precedent, low severity, no data corruption/cross-tenant risk — documented, not fixed, per
Tin's implicit scope on the 2 questions actually asked]; both real findings fixed by me directly and
re-verified independently — not just re-trusting a second agent pass)
By: agent-id affb580a9fa5a2545 (adversarial refute-read) + self (independent reproduction of both
findings, fix, and re-verification) · adversarially checked: tenant isolation (list/upsert/delete),
the "/" guard on both verbs incl. percent-encoded-slash ASGI behavior, error-code mapping
completeness, idempotent-DELETE semantics, dashboard key-collision/client-guard-bypass, TOCTOU in
the upsert-then-fetch pattern, nav/role-gate consistency with backend auth strictness, and
test-suite determinism (ran the new+sibling suites 6-8x back-to-back, not just once)

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (contract + both fix-decision approvals via AskUserQuestion) / self (code-level
review + independent verification) · date: 2026-07-01

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of `ERR_PRESET_SELECTOR_INVALID` / `ERR_PRESET_TARGET_UNKNOWN`
on PUT (a spike suggests confusing free-text `target_model` UX, per §1 ⚠); the retry-on-deadlock
path's "retried"/"exhausted" outcome, if ever instrumented, would confirm the fix holds under real
concurrent admin writes.

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ <unrecorded> (approved by <unrecorded>)
- [AI] build — strategy used: as planned (backend + frontend built in parallel by two subagents against
- [AI] verify — gate PASS (reviewed by Tin Dang (contract + both fix-decision approvals via AskUserQuestion) / self (code-level)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] the `bootstrap_fresh_db` per-test full drop_all/create_all pattern is duplicated
  (copy-pasted) across at least 5 test files (`presets_admin_surface`, `tenant_model_presets`,
  `provider_credential_store`, `provider_config_admin_api`, `oidc_tenant_config`) — this task's
  refute-read reproduced a genuine intermittent Postgres deadlock in the first two and fixed both
  with a scoped retry wrapper, but the other 3 were NOT touched (no reproduced failure, out of the
  scope Tin authorized for this specific fix) and likely carry the same latent flake risk (evidence:
  identical helper function, identical shared-DB-per-test design). A future task should either
  centralize this helper with the retry built in once, or audit the other 3 suites directly.
- [SPEC · open] `upsert_preset`'s upsert-then-separate-session-list-fetch (`_preset_for`) has a
  narrow TOCTOU window where a concurrent DELETE between the write and the read-back could surface
  a misleading `INTERNAL_ERROR` (500) for what was actually a successful write — inherited verbatim
  from the `put_provider_key`/`_status_for` precedent (not new to this task), accepted as-is (no
  cross-tenant leak, no data corruption, self-healing on client retry) per Tin's scope on this gate's
  2 fix questions (evidence: refute-read agent affb580a9fa5a2545, finding (f); the store's own
  `upsert` could instead use `RETURNING` to return the row atomically, eliminating the window
  entirely, but that changes the already-shipped `tenant-preset-store` port's `upsert() -> None`
  signature — a cross-task contract change, not done here).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [ADD · open] a contract's own §1 ⚠ "mirrors X precedent" claim needs the SAME precedent checked on
  BOTH axes (here: frontend nav shape AND backend auth strictness) before freeze — this task's v1/v2
  SCOPE ADDENDUM 1 asserted "mirrors /app/keys exactly" for nav visibility while silently carrying a
  STRICTER backend gate (OWNER-only vs. keys' any-role `get_identity`) than the precedent it named;
  only the adversarial refute-read caught the mismatch, not the contract-freeze review itself
  (evidence: refute-read agent affb580a9fa5a2545, finding (g1)).
- [TDD · open] a single clean local test run is not sufficient evidence of a green suite's
  determinism when the harness has known shared-resource characteristics (one Postgres instance
  across all tests) — this task's build agent's "15/15 green" self-report rested on exactly one run;
  repeating it 6-8x surfaced a ~25-30% failure rate the single run entirely missed (evidence: my own
  independent repeated runs, §5 SCOPE ADDENDUM 2).
