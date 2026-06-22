# TASK: Catalog sync trigger — POST /admin/catalog/sync + /models re-sync button

slug: catalog-sync-trigger · created: 2026-06-23 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. -->
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
BACKEND (add a NEW `admin_catalog_router` to catalog/api/router.py → `POST /admin/catalog/sync`; a thin owner/admin wrapper delegating to the EXISTING sync use case — all upstream timeout/retry already inside):
- `apps/gateway/src/gateway/catalog/application/use_cases.py:SyncCatalogUseCase.execute(self) -> int` — fetches all models via `CatalogSource`, calls `repository.sync_catalog(models)`, returns count. Raises `CatalogSourceUnavailableError` (pre-write) if upstream unreachable. **The thing to wrap.**
- `apps/gateway/src/gateway/catalog/api/router.py:sync_catalog` (≈53, `POST /internal/catalog/sync` → `SyncResponse(synced:int)`, 200; NO auth — Envoy guards `/internal/*`) — the internal variant; also refreshes `app.state.provider_resolver` fail-safely after success. **Mirror the body, ADD owner/admin auth.**
- `apps/gateway/src/gateway/catalog/api/router.py:admin_models_router` (≈116, `GET /admin/models`; PUT `/admin/models/{model_id:path}` ≈157) — sibling admin surface; **DO NOT reuse this prefix — the `:path` converter collides with `/sync`.** Use a SEPARATE `admin_catalog_router = APIRouter(prefix="/admin/catalog", tags=["admin-catalog"])`.
- `apps/gateway/src/gateway/catalog/api/deps.py:require_owner_or_admin` (≈64) — catalog-module copy; `role==MEMBER → AUTH_FORBIDDEN.exc()` (403 ERR_AUTH_FORBIDDEN). Import THIS one (in-module). `get_sync_use_case` dep wiring also lives in catalog/api/deps.py.
- `apps/gateway/src/gateway/catalog/api/router.py:SyncResponse` — `{synced:int}`; EXTEND with `synced_at:str` (ISO, stamped at success) OR define a new admin response model. (Pick at §3.)
- `apps/gateway/src/gateway/core/error_catalog.py`: `CATALOG_UPSTREAM_UNAVAILABLE = ErrorSpec(502,"ERR_UPSTREAM_UNAVAILABLE",…)` (already used by the internal handler), `CATALOG_EMPTY` (409), `AUTH_FORBIDDEN` (403). **No new spec needed.**
- `apps/gateway/src/gateway/main.py` (≈724) — `app.include_router(admin_models_router)`; ADD `app.include_router(admin_catalog_router)`.
- Catalog is GLOBAL (no tenant_id on `models`) — sync = platform-wide idempotent upsert (`catalog/infrastructure/repository.py:SqlAlchemyCatalogRepository.sync_catalog`, one txn). Upstream = `catalog/infrastructure/openrouter_source.py:OpenRouterCatalogSource` (10s timeout, max-2 jittered retry; failure→`CatalogSourceUnavailableError`).

FRONTEND (mirror the ModelsPage mutation-button pattern):
- `apps/dashboard/components/models/ModelsPage.tsx` — owner/admin model surface; `useMutation`+`useQueryClient`, `bffPut`, `onSuccess`→`invalidateQueries(["admin-models"])`, error via `mutation.isError`+`<ErrorState title={getErrorTitle(...)}/>`, role gate via `useCurrentUser`. **ADD a "Re-sync catalog" button + last-sync display here.**
- `apps/dashboard/lib/bff-client.ts:bffPost(path,body)` (≈103) — POST via `/api/gw${path}`, credentials:include; `BffError{status,problem}`. **Use for the sync call.**
- `apps/dashboard/app/api/gw/[...path]/route.ts` — catch-all already exports POST → `POST /api/gw/admin/catalog/sync` passes through. **NO new BFF route.**
- `apps/dashboard/components/ui` — Button, ErrorState reuse. The button is owner/admin-only (ModelsPage already admin-gated by nav + gateway).

Context (working folder):
- `.add/milestones/v31/MILESTONE.md` exit criterion: "An owner forces a catalog re-sync from the dashboard and sees the new last-sync time." ← actor=owner (settles blast-radius), and "sees the new last-sync time" ← drives the synced_at response field.
- Tests to mirror: backend admin-mutation suites (e.g. `apps/gateway/tests/model_mgmt/` PUT /admin/models — auth/member-403/success); frontend `apps/dashboard/tests/` ModelsPage mutation tests (success + member-hidden + error). `scripts/` call `POST /internal/catalog/sync` today.

Honors (patterns / conventions):
- CONVENTIONS.md: Clean-Arch (api delegates to the application use case, never reimplements sync) · ErrorSpec.exc() only · design-for-failure IO — timeout+jittered-retry already inside `OpenRouterCatalogSource` (inherited; NO circuit breaker on the catalog source = known gap, spec delta not blocker) · admin mutations return 200 (not 202 — sync runs inline) · frontend: bffPost + useMutation with error surfaced, no client-side Authorization header, role-gated button.
- PROJECT.md: catalog is a SHARED/global platform resource; an owner/admin triggering re-sync is a platform-wide (idempotent) op — safe by design, but a rate-limit/debounce is a spec delta (any owner could hammer the upstream).

Anchors the contract cites: `SyncCatalogUseCase.execute` · `admin_catalog_router` (NEW, prefix `/admin/catalog`) · `require_owner_or_admin` (catalog/api/deps) · `get_sync_use_case` · `SyncResponse`/new admin response (`synced`+`synced_at`) · `CATALOG_UPSTREAM_UNAVAILABLE`/`AUTH_FORBIDDEN` · `bffPost` · `ModelsPage` mutation pattern.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Catalog sync trigger — an owner/admin forces a model-catalog re-sync from the dashboard via `POST /admin/catalog/sync` and sees how many models synced + when.
Framings weighed: thin owner/admin wrapper over the existing internal sync (chosen — reuses SyncCatalogUseCase verbatim, inherits its timeout/retry) · re-implement sync logic in the admin handler (rejected — duplicates the use case, violates Clean-Arch) · async/queued 202 + poll (rejected — sync runs inline in <~1s, 202 implies a job system that does not exist) · operator-only via the /ops mTLS surface (rejected — the milestone criterion says "an OWNER forces a re-sync from the dashboard"; the op is an idempotent platform refresh, no data exposure).
Must:
<must>
  - `POST /admin/catalog/sync` triggers the existing `SyncCatalogUseCase.execute()`; owner/admin only (member → 403).
  - On success return 200 `{ synced: <int count>, synced_at: <ISO-8601 UTC, gateway clock at completion> }`.
  - After a successful sync, refresh `app.state.provider_resolver` fail-safely (a refresh error never changes the response) — exactly as the internal handler does.
  - The operation is idempotent (repeat triggers converge to the same catalog state — inherited from the upsert).
  - Dashboard `/models` page: an owner/admin-visible "Re-sync catalog" button that calls the endpoint, shows the returned last-sync time on success, and refreshes the model list (invalidate `admin-models`); members never see the button.
Reject:
  - upstream catalog source unreachable -> "ERR_UPSTREAM_UNAVAILABLE" (502, inherited — CatalogSourceUnavailableError mapped before any write)
  - missing/invalid Bearer -> existing auth rejection (401); member role -> "ERR_AUTH_FORBIDDEN" (403)
After:
  - The global `models` catalog reflects the upstream model list (upserted, absent models deactivated, price changes snapshotted); the provider resolver map is refreshed; the caller sees the count + completion time.
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **Last-sync time may be returned EPHEMERALLY (in the POST response only), not persisted** — lowest confidence because the criterion says "sees the new last-sync time", which the response satisfies right after a trigger, but on a page RELOAD the dashboard has no stored last-sync to show (no `synced_at` column / meta row exists today). If wrong (a persisted, reload-survivable timestamp is required): add a `catalog_sync_meta` singleton row or a `models.synced_at` column via migration + expose it on GET — deferred as a SPEC delta to keep this slice small. Cost if wrong: a follow-up migration; the response shape (`synced_at`) is forward-compatible so no contract break.
  - [ ] Endpoint lives on a NEW `admin_catalog_router` (prefix `/admin/catalog`), NOT on `admin_models_router` — to dodge the `/admin/models/{model_id:path}` converter collision with `/sync`. Low risk; pure routing.
  - [ ] 200 (not 202) — sync runs inline and returns the count synchronously, matching every existing admin-mutation analog. Low risk.
  - [ ] No rate-limit/debounce on the trigger (any owner could repeatedly hammer the OpenRouter upstream) — accepted for this slice (idempotent, low-volume admin action); a debounce/rate-limit is a SPEC delta. Low risk operationally.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Owner triggers a successful re-sync
  Given an owner JWT and a catalog source that returns N models
  When POST /admin/catalog/sync
  Then 200 with { synced: N, synced_at: <ISO-8601 string> }
  And the models catalog reflects the source (upserted)

Scenario: Admin can also trigger
  Given an admin JWT and a catalog source that returns models
  When POST /admin/catalog/sync
  Then 200 with synced == the source count

Scenario: Member is denied
  Given a member-role JWT
  When POST /admin/catalog/sync
  Then 403 "ERR_AUTH_FORBIDDEN"
  And no sync ran (the catalog is unchanged)

Scenario: Missing Bearer is denied
  Given no Authorization header
  When POST /admin/catalog/sync
  Then 401
  And no sync ran

Scenario: Upstream source unavailable
  Given an owner JWT and a catalog source that is unreachable
  When POST /admin/catalog/sync
  Then 502 "ERR_UPSTREAM_UNAVAILABLE"
  And the catalog is unchanged (failure is raised before any write)

Scenario: Re-sync is idempotent
  Given an owner JWT and a fixed catalog source
  When POST /admin/catalog/sync is called twice
  Then both return 200 with the same synced count
  And the final catalog state equals a single sync (no duplicate/divergent rows)

Scenario: synced_at advances between syncs
  Given an owner JWT
  When POST /admin/catalog/sync is called, then called again later
  Then the second synced_at is >= the first (monotonic, gateway clock)

Scenario: Dashboard owner re-syncs and sees the time
  Given an owner on /models and the BFF returns { synced: 5, synced_at: T }
  When the owner clicks "Re-sync catalog"
  Then POST /admin/catalog/sync is called once
  And the last-sync time T is shown
  And the model list query is invalidated (refetched)

Scenario: Dashboard surfaces a sync error
  Given an owner on /models and the BFF returns 502 ERR_UPSTREAM_UNAVAILABLE
  When the owner clicks "Re-sync catalog"
  Then an error is shown (the problem title)
  And no last-sync time is set

Scenario: Member does not see the re-sync button
  Given a member-role session on /models
  When the page renders
  And the "Re-sync catalog" button is absent
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /admin/catalog/sync   body: {} (none)
  auth: Bearer JWT -> require_owner_or_admin (owner OR admin); member -> 403
  200 -> { "synced": <int>, "synced_at": "<ISO-8601 UTC>" }   # synced_at stamped at completion (gateway clock)
  401 -> existing auth rejection (missing/invalid Bearer)
  403 -> { "code": "ERR_AUTH_FORBIDDEN", … }   # member role
  502 -> { "code": "ERR_UPSTREAM_UNAVAILABLE", … }   # CatalogSourceUnavailableError, mapped before any write

Behavior: delegates to SyncCatalogUseCase.execute() (idempotent upsert over the GLOBAL `models`
catalog; absent models deactivated; price changes -> pricing_snapshots). On success, refreshes
app.state.provider_resolver FAIL-SAFELY (a refresh error never alters the response). READ-then-write
is one transaction inside the repository; the endpoint adds no new write.

Response model: NEW `CatalogSyncResponse(synced:int, synced_at:str)` (frozen, ConfigDict) — does NOT
mutate the existing internal `SyncResponse` (keeps the internal endpoint byte-identical).

Router: NEW admin_catalog_router = APIRouter(prefix="/admin/catalog", tags=["admin-catalog"]) in
catalog/api/router.py; registered in main.py. (NOT on admin_models_router — avoids the
/admin/models/{model_id:path} converter collision.)

Frontend: POST /admin/catalog/sync via bffPost (BFF catch-all, NO new route). /models page gains an
owner/admin-only "Re-sync catalog" button -> on success show synced_at + invalidate ["admin-models"];
on error show the problem title; members never see the button.

Schema: NO migration, NO new table/column. Last-sync time is RETURNED in the response (ephemeral —
not persisted; see the freeze flag). Reads/writes only via the existing SyncCatalogUseCase.
```

Status: FROZEN @ v1 — approved by AI under autonomy:auto (2026-06-23)
Decided at freeze: actor = owner/admin via /admin/catalog/sync (milestone criterion "an OWNER forces a re-sync"; the op is an idempotent global platform refresh, no data exposure — NOT a security freeze, so auto-approved like [[sso-login-button]]). Success = 200 inline (not 202). NEW response model (don't touch internal SyncResponse).
Least-sure flag surfaced at freeze: [contract] `synced_at` is EPHEMERAL — returned in the POST response only, NOT persisted; on a page reload the dashboard shows no last-sync. Low confidence this matters because the criterion ("sees the new last-sync time") is satisfied right after the trigger, and the response field is forward-compatible (a later migration can persist + expose it on GET with no contract break). Cost if wrong: a follow-up migration (`catalog_sync_meta` row or `models.synced_at`), already filed as a SPEC delta — no rework of this contract.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% on new code (handler + frontend button/last-sync)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  BACKEND — `apps/gateway/tests/catalog_sync_trigger/test_catalog_sync_trigger.py` (FakeCatalogSource via app.state.catalog_source; owner via signup_and_login; member via direct users insert + token_service.issue):
  - test_owner_sync_success: fake source 2 models / owner POST / 200 {synced:2, synced_at:ISO} + 2 active model rows
  - test_admin_sync_success: admin JWT / POST / 200 synced==count
  - test_member_denied: member JWT / POST / 403 ERR_AUTH_FORBIDDEN + catalog unchanged (0 models)
  - test_missing_bearer_denied: no header / POST / 401 + catalog unchanged
  - test_upstream_unavailable: fake source raise_unavailable / owner POST / 502 ERR_UPSTREAM_UNAVAILABLE + catalog unchanged
  - test_idempotent: owner POST twice (same source) / both 200, same synced / single snapshot per model
  - test_synced_at_monotonic: two syncs / second synced_at >= first (ISO parse + compare)
  - test_synced_at_is_iso: synced_at parses as datetime.fromisoformat
  FRONTEND — `apps/dashboard/tests/catalog-sync.test.tsx` (mirror ModelsPage mutation tests; msw POST /api/gw/admin/catalog/sync; role from /api/auth/me):
  - test_owner_resync_success_shows_time: owner / click "Re-sync catalog" / POST called once + last-sync time shown + admin-models refetched
  - test_resync_error_surfaces: 502 ERR_UPSTREAM_UNAVAILABLE / click / error title shown, no time
  - test_member_no_resync_button: member role / button absent
</test_plan>

Tests live in: `apps/gateway/tests/catalog_sync_trigger/` `apps/dashboard/tests/catalog-sync.test.tsx` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/catalog/api/router.py` `apps/gateway/src/gateway/catalog/api/schemas.py` `apps/gateway/src/gateway/main.py` `apps/dashboard/components/models/ModelsPage.tsx`
Strategy (ordered batches): 1. backend: add `CatalogSyncResponse` to schemas.py; add `admin_catalog_router` + `POST /admin/catalog/sync` handler in router.py (delegate to SyncCatalogUseCase, stamp synced_at, fail-safe provider_resolver refresh, map CatalogSourceUnavailableError→502); register in main.py → red→green backend. 2. frontend: add owner/admin "Re-sync catalog" button + last-sync display to ModelsPage (useMutation→bffPost, onSuccess set time + invalidate admin-models, error via isError) → red→green frontend.
Safety rule (feature-specific): delegate to the existing use case (NO re-implemented sync, NO new write path); upstream timeout/retry inherited; CatalogSourceUnavailableError mapped to 502 BEFORE any write; provider_resolver refresh is fail-safe (never alters the response). Owner/admin enforced server-side (member→403), not just hidden in UI.
Code lives in: backend `catalog/api/router.py` + `catalog/api/schemas.py` + `main.py` · frontend `components/models/ModelsPage.tsx`
Constraints: do NOT change any test or the contract; do NOT mutate the internal `SyncResponse` (keep /internal byte-identical); allow-list packages only (no new deps); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — gateway full 1326 green; dashboard full 368 green; catalog_sync_trigger 9/9
- [x] coverage did not decrease — 9 backend + 3 frontend new tests; net new coverage
- [x] no test or contract was altered during build — tests only STRENGTHENED post-refute (re-crossed); contract FROZEN @ v1 untouched; internal SyncResponse byte-identical
- [x] the green was EARNED — adversarial refute-read (sonnet) UPHELD 0.87, ZERO blockers (auth gates at the Depends layer before the handler body; CatalogSourceUnavailableError raised before any write; provider_resolver refresh fail-safe; internal endpoint unchanged). Closed EG-2 by strengthening (member-denied on a NON-EMPTY catalog leaves it intact). EG-1 (port doesn't enforce raise-before-first-yield for a hypothetical paginated source) filed as a SPEC delta.
- [x] concurrency / timing — sync collects the full model list before the single-transaction upsert (no partial write); idempotent; synced_at = gateway clock; refresh wrapped in fail-safe try/except
- [x] no exposed secrets, injection openings, or unexpected dependencies — delegates to the existing use case; no new deps; CATALOG_UPSTREAM_UNAVAILABLE.exc(detail=str(exc)) (the catalog source detail carries no secret); credentials never logged
- [x] layering & dependencies follow CONVENTIONS.md — api delegates to SyncCatalogUseCase (no reimplemented sync); ErrorSpec.exc(); new admin_catalog_router separate prefix; 200 inline
- [x] a person reviewed and approved the change — auto-gated under autonomy:auto (not risk:high; no security freeze — the actor=owner decision was settled by the milestone criterion, mirrors [[sso-login-button]])

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [ ] POST /admin/catalog/sync (owner/admin) returns 200 `{synced:int, synced_at:ISO}` and the global models catalog reflects the source — confirmed by test_owner_sync_success (count + active rows)
- [ ] member → 403 ERR_AUTH_FORBIDDEN, missing bearer → 401, upstream-down → 502 ERR_UPSTREAM_UNAVAILABLE, and in each the catalog is UNCHANGED — confirmed by member/missing/unavailable tests
- [ ] re-sync is idempotent (twice = single sync state) and synced_at is monotonic ISO — confirmed by test_idempotent + test_synced_at_monotonic
- [ ] internal `SyncResponse` + `POST /internal/catalog/sync` unchanged — confirmed by the existing catalog suite staying green
- [ ] /models owner sees a "Re-sync catalog" button → click shows last-sync time + refetches models; member never sees it; error surfaces the problem title — confirmed by catalog-sync.test.tsx

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `admin_sync_catalog` on `admin_catalog_router`, registered in main.py (app.include_router); `CatalogSyncResponse` used as response_model; tests exercise the live route (200/403/401/502). Frontend: re-sync button + lastSync + resyncCatalog mutation all referenced in ModelsPage JSX.
- [x] DEAD-CODE (code) — no orphans: CatalogSyncResponse used; admin_catalog_router imported+registered; ruff (unused-import/var) + pyright clean; eslint clean.
- [x] SEMANTIC (prose / non-code) — n/a (code task).

### GATE RECORD
Outcome: PASS
Reviewed by: AI auto-resolved under autonomy:auto (refute UPHELD 0.87, no blockers; not risk:high, no security freeze — actor=owner settled by the milestone criterion) · date: 2026-06-23

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): POST /admin/catalog/sync 502-rate (OpenRouter health) · 403-rate (member probing) · trigger frequency per tenant (abuse/hammering signal) · p95 sync latency.

### Spec delta
- [SPEC · open] PERSIST last-sync time (the freeze flag) — a `catalog_sync_meta` singleton row or `models.synced_at` column + expose on GET, so the dashboard shows last-sync after a reload (today it's ephemeral, response-only). Evidence: §3 least-sure flag.
- [SPEC · open] rate-limit / debounce the trigger — any owner/admin can repeatedly POST and hammer the OpenRouter upstream (cost + rate-limit risk). Evidence: §1 accepted-risk assumption.
- [SPEC · open] enforce "raise BEFORE first yield" as a CatalogSource port invariant (or accumulate-then-commit-or-rollback guard) — a future paginated source that yields some models then fails mid-stream would commit a PARTIAL catalog (silently deactivating un-received models) without a 502. Evidence: refute EG-1 (current sources raise pre-yield, so safe today; the port doesn't enforce it).
- [SPEC · open] add a circuit breaker on the catalog source (today only the completion upstream has one). Evidence: §0 CONVENTIONS design-for-failure gap.

### Competency deltas
- [TDD · open] a "denied/failed → state unchanged" assertion is VACUOUS against a fresh-DB fixture (count==0 is trivially true) — seed a prior SUCCESS first, then assert the count is unchanged at N (not 0/N+1) so the guard actually proves the denied path never wrote. Evidence: refute EG-2 → test_member_denied_leaves_existing_catalog_intact.
- [SDD · open] exposing a previously-internal (Envoy-guarded, no-auth) operation as an authed external endpoint is a thin, safe move when the op is idempotent + delegates to the same use case — give it a SEPARATE response model so the internal contract stays byte-identical (don't extend the shared DTO). Evidence: CatalogSyncResponse vs SyncResponse.
- [ADD · open] "reuse the existing mechanism" tasks inherit the upstream's design-for-failure (timeout/retry) for free — ground should explicitly confirm WHERE that handling lives and note what's still missing (circuit breaker) as a delta rather than re-implementing. Evidence: §0 + spec deltas.
