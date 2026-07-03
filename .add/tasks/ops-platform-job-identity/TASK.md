# TASK: Ops-mTLS platform job identity

slug: ops-platform-job-identity · created: 2026-07-02 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
phase: done   <!-- ground -> specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
depends-on: platform-tenant-seed (done, gate=PASS, 2026-07-03)
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower the
     autonomy level to `manual` or `conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
- `apps/gateway/src/gateway/ops/api/deps.py` — `require_ops(request, verifier, tokens) -> OpsIdentity` (existing, UNCHANGED by this task). Denial split: valid tenant JWT → 403 `ERR_OPS_FORBIDDEN`; everything else → byte-identical 401 `ERR_OPS_UNAUTHORIZED`. Fail-closed default-OFF (empty fingerprint allow-list authorizes no one).
- `apps/gateway/src/gateway/tenants/infrastructure/ops_cert_verifier.py` — `OpsIdentity(fingerprint: str)`, frozen dataclass — UNCHANGED.
- `apps/gateway/src/gateway/ops/api/router.py` — `GET /ops/reconciliation`, the ONLY existing consumer of `require_ops`. This task's scope declares ZERO lines touched here.
- `apps/gateway/src/gateway/tenants/infrastructure/repository.py` — `get_platform_tenant(session: AsyncSession) -> TenantRow | None`, now BUILT by the sibling `platform-tenant-seed` task (VERIFY still pending as of this drafting). Per its contract: "returns None only if unmigrated (defensive, never raised)."
- `apps/gateway/src/gateway/proxy/application/use_cases.py` — `resolve_provider_credential(resolver, tenant_id, provider) -> object | None` (existing, UNCHANGED). "The single source of truth for the credential-resolution-seam §3 gate." Behavior: returns `None` if `resolver is None` or `provider not in BYOK_PROVIDERS`; calls `resolver.resolve(tenant_id, provider)`; on `ProviderKeyMissing` raises `ProblemError(402, "ERR_PROVIDER_KEY_MISSING", ...)`; on success calls `set_provider_credential(cred)` and returns the reset `Token`.
- `apps/gateway/src/gateway/proxy/domain/ports.py` — `TenantCredentialResolver` Protocol (`async def resolve(tenant_id, provider) -> ProviderCredential`, "ALWAYS raises `ProviderKeyMissing` on absent/disabled/None/timeout").
- `apps/gateway/src/gateway/proxy/domain/provider_credentials.py` — `BYOK_PROVIDERS` frozenset (7 providers, including "minimax"); `ProviderKeyMissing(provider)` domain error, `.code = "ERR_PROVIDER_KEY_MISSING"`.
- `apps/gateway/src/gateway/proxy/domain/credential_context.py` — `set_provider_credential`/`get_provider_credential`/`reset_provider_credential` contextvar helpers (existing, UNCHANGED) — the side effect this task inherits as-is.
- `apps/gateway/src/gateway/core/error_catalog.py` — `ErrorSpec(status, code, title_template)` + `.exc()`; `OPS_UNAUTHORIZED`/`OPS_FORBIDDEN` precedent. This task adds ONE new constant following the identical pattern.
- `apps/gateway/src/gateway/main.py` — `app.state.tenant_credential_resolver = CachedTenantCredentialResolver(store=app.state.tenant_provider_key_store, settings=settings)` — confirms the resolver a future caller passes in.
- `apps/gateway/tests/credential_stub.py` — `install_stub_resolver`/`StubCredentialResolver`, wired into the root `app` test fixture; resolves ANY (tenant, provider) to a fixed masked Bearer credential. This task's tests supply their OWN resolver fake, not the default stub, to exercise the `ProviderKeyMissing`/402 path.
- `apps/gateway/tests/operator_wide_reconciliation/conftest.py` — `OPS_RECON`, `recon_params()`, `xfcc()`, `enable_ops()` — reused directly (imported, not duplicated) for this task's one auth-precondition regression test.

Context (working folder):
- `.add/milestones/platform-identity/MILESTONE.md` — Scope In: "the existing ops-mTLS/XFCC mechanism extended so a cert-authenticated platform job can resolve the platform tenant's own stored `tenant_provider_keys`." Exit criterion: "An ops-mTLS-authenticated request resolves the platform tenant's stored provider credential; a request without a valid ops cert cannot."
- `.add/tasks/minimax-catalog-seed/TASK.md` — the FUTURE MiniMax refetch job motivating this task, explicitly split OUT as `[SPEC · open]`, not built here. No consumer of this task's output exists yet.
- `.add/CONVENTIONS.md` (folded v3, 2026-06-11): "test arranges call CANONICAL routes only — an arrange that invents an endpoint pushes builders into expanding product surface." Directly rules out inventing a placeholder HTTP endpoint to make this task's behavior "observable."
- `apps/gateway/tests/platform_tenant_seed/test_platform_tenant_seed.py` — style/harness anchor: its non-migration scenarios use the standard `db_session` fixture + raw-SQL insert, not the Alembic harness — this task follows the same shape since it tests neither seeding nor migration ordering.

Honors (patterns / conventions):
- CONVENTIONS.md CLEAN ARCHITECTURE, "Dependencies point INWARD only" — but `ops/api/` is ALREADY an established cross-module composition layer: `ops/api/router.py` imports `usage.application.reconciliation`; `ops/api/deps.py` already imports `tenants.infrastructure.ops_cert_verifier` and `keys.api.deps`. This task's new function fits the identical shape — reusing `tenants.infrastructure.repository.get_platform_tenant` + `proxy.application.use_cases.resolve_provider_credential`, owning zero new domain logic.
- CONVENTIONS.md: "Errors: machine-readable codes ERR_<DOMAIN>_<REASON>... never free text" — the one new Reject follows this via a new `ErrorSpec`.
- GLOSSARY.md `platform operator`/`ops-auth` — this task EXTENDS the existing mechanism; it is explicitly distinct from and has ZERO dependency on the sibling `superadmin` (JWT) role.

Anchors the contract cites: `require_ops` / `OpsIdentity` (unchanged) · `get_platform_tenant` (platform-tenant-seed, BUILT) · `resolve_provider_credential` / `TenantCredentialResolver` / `BYOK_PROVIDERS` / `ProviderKeyMissing` (unchanged, delegated verbatim) · `ErrorSpec` / `OPS_UNAUTHORIZED` / `OPS_FORBIDDEN` (pattern for the new constant).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Ops-authenticated platform-job credential resolution

Framings weighed:
A plain composition function `resolve_platform_credential(resolver, session, provider)` living in `ops/api/deps.py`, NOT itself a FastAPI dependency — a future endpoint declares its OWN `Depends(require_ops)`, then calls this function directly **(chosen)** · a FastAPI dependency-factory parallel to `require_permission(perm)` (e.g. `require_ops_platform_credential(provider: str)`) **(rejected** — presupposes how an undesigned future endpoint wants to bind `provider`, zero real consumers to validate against; forces tests to either fake FastAPI's DI-resolution chain or stand up a placeholder route, which CONVENTIONS.md's folded v3 lesson explicitly flags as rejected-at-review**)** · extending `require_ops`'s own return type (`OpsIdentity`) to eagerly carry a resolved `platform_tenant_id` **(rejected** — `require_ops`/`OpsIdentity` are a FROZEN §3 contract (v30) whose only consumer is `/ops/reconciliation`; adding a mandatory DB round-trip inside it changes that endpoint's behavior/cost for a need it doesn't have**)**

Must:
<must>
  - An ops-authenticated caller (one that already holds a valid `OpsIdentity` from the unchanged `require_ops`) can resolve the platform tenant's own credential for a BYOK provider by calling `resolve_platform_credential(resolver, session, provider)`.
  - `resolve_platform_credential` resolves "the platform tenant" via `get_platform_tenant(session)` only — never filters `tenants` by `kind='platform'` directly.
  - `resolve_platform_credential` delegates the actual credential resolution to the existing `resolve_provider_credential(resolver, platform_tenant.id, provider)` verbatim — inheriting its `BYOK_PROVIDERS` gate, TTL cache, bounded timeout, positive-only caching, `ProviderKeyMissing`→402 mapping, and contextvar side-effect unchanged. Zero new credential-resolution logic is written.
  - `GET /ops/reconciliation`, `require_ops`, `OpsIdentity`, and `get_ops_cert_verifier` are byte-identical after this task — zero lines changed in `ops/api/router.py`; `ops/api/deps.py` only gains a new, separate function.
  - This task reuses `ops-auth`/`platform operator` (existing, mTLS-based) exclusively. It does NOT introduce a new authentication mechanism, and does NOT depend on, reference, or require the new `superadmin` (JWT-based) role from the sibling `superadmin-role` task.
  - `resolve_platform_credential` performs no independent authentication/authorization check of its own — callers must already be behind `require_ops` (an architectural precondition, exactly mirroring how `_operator: Annotated[OpsIdentity, Depends(require_ops)]` is a mandatory-but-otherwise-unused gate parameter in `router.py` today).
  - When `provider` is not in `BYOK_PROVIDERS`, or no resolver is wired (`resolver is None`), `resolve_platform_credential` returns `None` — identical pass-through semantics to `resolve_provider_credential`, never raises.
</must>

Reject:
<reject>
  - The platform tenant row does not exist (`get_platform_tenant(session)` returns `None` — unmigrated/pre-seed state) -> `"ERR_PLATFORM_TENANT_MISSING"` (new `ErrorSpec(500, ...)`, follows the `OPS_UNAUTHORIZED`/`OPS_FORBIDDEN` pattern). Fails closed; never fabricates or substitutes a placeholder tenant_id.
  - The platform tenant has no enabled credential configured for `provider` (`TenantCredentialResolver.resolve` raises `ProviderKeyMissing`) -> the existing `ProblemError(402, "ERR_PROVIDER_KEY_MISSING")` mapping, reused unchanged — no new error code invented for "the platform tenant specifically."
</reject>
After:
<after>
  - Given ops-auth is valid and the platform tenant has an enabled `provider` credential, `resolve_platform_credential` returns the same token/contextvar-set result `resolve_provider_credential` would return for any other tenant.
  - `GET /ops/reconciliation`'s request count, response shape, auth behavior, and latency profile are unaffected — this task adds a new, additive, independently-callable function; it changes no existing call graph.
  - No new authentication mechanism exists; `ops-auth`/`platform operator` remains the only door a platform job walks through, exactly as before this task.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ `get_platform_tenant`'s exact signature/behavior is inherited from the sibling `platform-tenant-seed` task, which was DRAFT/unbuilt when this bundle was first drafted and is now BUILT (VERIFY still pending as of this integration). If platform-tenant-seed's VERIFY surfaces a change, this task needs a small mechanical follow-up edit, not a redesign (the delegation pattern — call it, handle `None` — is signature-shape-agnostic).
  - Choosing HTTP 500 (rather than 503, or an unhandled exception) for `ERR_PLATFORM_TENANT_MISSING` — medium confidence: 500 best matches "an operational/deployment precondition failed, not a caller error." Confirm or redirect if Tin prefers 503 (readiness-style).
  - [ ] No new HTTP endpoint is created in this task (pure plumbing) — medium-high confidence, backed by three independent signals: the milestone's Scope-In wording describes a capability extension not a route; `minimax-catalog-seed/TASK.md` explicitly splits the actual refetch job out as a future, unscoped task; CONVENTIONS.md's folded rule rejects inventing endpoints for tests.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: an ops-authenticated caller resolves the platform tenant's own credential
  Given the platform tenant row exists (resolvable via get_platform_tenant)
  And the platform tenant has an enabled "minimax" credential configured
  When resolve_platform_credential(resolver, session, "minimax") is called
  Then it returns a non-None result and the resolved credential becomes readable via get_provider_credential()
  And the resolver was consulted with the PLATFORM tenant's id, never a different tenant's

Scenario: a non-BYOK provider or an unwired resolver is a silent no-op
  Given the platform tenant row exists
  When resolve_platform_credential is called with a provider outside BYOK_PROVIDERS, and separately with resolver=None
  Then both calls return None
  And no ProblemError is raised and no contextvar is set

Scenario: the platform tenant has no configured credential for the requested provider
  Given the platform tenant row exists with no enabled "minimax" credential
  When resolve_platform_credential(resolver, session, "minimax") is called
  Then a ProblemError(402, "ERR_PROVIDER_KEY_MISSING") is raised — the existing, reused mapping
  And no contextvar is set

Scenario: the platform tenant row does not exist (unmigrated)
  Given get_platform_tenant(session) returns None (pre-platform-tenant-seed state)
  When resolve_platform_credential(resolver, session, "minimax") is called
  Then a ProblemError(500, "ERR_PLATFORM_TENANT_MISSING") is raised
  And the resolver is never consulted — no fabricated tenant_id is used

Scenario: a request without a valid ops cert is denied, and GET /ops/reconciliation is unaffected
  Given ops-auth is default-OFF (no fingerprint configured) and no XFCC header is sent
  When GET /ops/reconciliation (the canonical, pre-existing route) is called
  Then it is denied with byte-identical 401 ERR_OPS_UNAUTHORIZED, exactly as before this task
  And resolve_platform_credential is never reached (require_ops raises before any handler body runs)
```

</scenarios>

Note: two Musts (verbatim delegation to `resolve_provider_credential`; zero dependency on `superadmin`/JWT) are structural claims verified by code/import-diff review, not a runtime Gherkin scenario — `resolve_platform_credential`'s entire body is 4 lines with no independent credential logic and no Role/JWT import.

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
No new HTTP endpoint — a composition-function contract (ops/api/deps.py), reusing
require_ops (unchanged) + get_platform_tenant (platform-tenant-seed) +
resolve_provider_credential (credential-resolution-seam) verbatim.

async def resolve_platform_credential(
    resolver: TenantCredentialResolver | None,
    session: AsyncSession,
    provider: str,
) -> object | None:
    """Resolve the platform tenant's own credential for `provider`.

    PRECONDITION (not enforced by this signature — an architectural assumption,
    exactly like `_operator: Annotated[OpsIdentity, Depends(require_ops)]` in
    ops/api/router.py): the caller has already been authorized via
    `Depends(require_ops)`. This function performs no auth check of its own.

    Returns:
        None — resolver is None OR provider not in BYOK_PROVIDERS (identical
        pass-through to resolve_provider_credential; not an error).
        Otherwise the contextvar reset Token; the platform tenant's resolved
        ProviderCredential becomes readable via get_provider_credential() until
        the caller resets the token in a `finally`.

    Raises:
        ProblemError(500, "ERR_PLATFORM_TENANT_MISSING") — get_platform_tenant
            returned None (unmigrated/pre-seed). Fails closed; never fabricates
            a tenant_id.
        ProblemError(402, "ERR_PROVIDER_KEY_MISSING") — REUSED unchanged from
            resolve_provider_credential: no enabled credential for `provider`.
    """
    platform_tenant = await get_platform_tenant(session)
    if platform_tenant is None:
        raise PLATFORM_TENANT_MISSING.exc()
    return await resolve_provider_credential(resolver, platform_tenant.id, provider)


New error_catalog.py constant (inserted after OPS_FORBIDDEN, its own section):
  PLATFORM_TENANT_MISSING = ErrorSpec(
      500, "ERR_PLATFORM_TENANT_MISSING", "Platform tenant not provisioned"
  )

Reject:
  get_platform_tenant(session) is None -> ProblemError(500, "ERR_PLATFORM_TENANT_MISSING")  (new)
  ProviderKeyMissing from the resolver -> ProblemError(402, "ERR_PROVIDER_KEY_MISSING")       (reused, zero new code)

Unchanged (explicitly frozen as NOT touched by this task):
  require_ops(request, verifier, tokens) -> OpsIdentity        (ops/api/deps.py)
  OpsIdentity(fingerprint: str)                                 (tenants/infrastructure/ops_cert_verifier.py)
  GET /ops/reconciliation                                       (ops/api/router.py) — zero lines changed
  resolve_provider_credential(resolver, tenant_id, provider)    (proxy/application/use_cases.py) — zero lines changed

Schema: no new tables/columns. Reads tenants (via get_platform_tenant, read-only) and
  tenant_provider_keys (via the existing TenantCredentialResolver chain, read-only).

IO note: no bespoke asyncio.timeout wraps get_platform_tenant's SELECT — it is a single
  point lookup via the platform-tenant-seed partial UNIQUE INDEX, not a cross-tenant
  aggregate scan (the risk class /ops/reconciliation's existing 30s timeout guards
  against). The actual upstream-credential fetch already has its own bounded timeout
  and no-retry-on-timeout policy, inherited unchanged from CachedTenantCredentialResolver.
```

Status: FROZEN @ v1 — approved by Tin Dang
Least-sure flag surfaced at freeze:
⚠ [contract] At drafting time, the top uncertainty was that `get_platform_tenant`'s exact
signature/behavior was inherited from the sibling `platform-tenant-seed` task while that task was
still DRAFT/unbuilt. UPDATE at this freeze: platform-tenant-seed is now BUILT + VERIFIED
(gate=PASS, 2026-07-03), with `get_platform_tenant`'s signature unchanged from what this task
assumed — including its refute-read-hardened `None`-on-unmigrated behavior, which this task
already treats as the sole "platform tenant missing" trigger. That risk is now CLOSED, kept here
for transparency about how this bundle was drafted (parallel, before its dependency landed).
The live lowest-confidence item is now: [contract] choosing HTTP 500 (rather than 503, or an
unhandled exception) for `ERR_PLATFORM_TENANT_MISSING` — medium confidence, 500 best matches "an
operational/deployment precondition failed, not a caller error." Cost if wrong: a one-line
`ErrorSpec` status-code change, no shape/call-site rework.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of new code (one new function, ~4 lines of logic; one new `ErrorSpec` constant).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_ops_authenticated_caller_resolves_platform_credential: arrange a seeded platform tenant + a fake resolver programmed with a "minimax" credential for the platform tenant's id AND a decoy credential for a different tenant id / act call resolve_platform_credential(resolver, session, "minimax") / assert non-None return, get_provider_credential() is the platform credential, resolver was called with the platform tenant's id and never the decoy's, reset clears it.
  - test_non_byok_provider_and_unwired_resolver_return_none: arrange a seeded platform tenant / act call with a non-BYOK provider, and separately with resolver=None / assert both return None, no exception, no contextvar set.
  - test_missing_platform_credential_raises_402: arrange a seeded platform tenant, resolver has nothing programmed for "minimax" / act call resolve_platform_credential / assert ProblemError status=402 code="ERR_PROVIDER_KEY_MISSING".
  - test_missing_platform_tenant_raises_500: arrange NO platform tenant row / act call resolve_platform_credential / assert ProblemError status=500 code="ERR_PLATFORM_TENANT_MISSING" + resolver never consulted.
  - test_require_ops_still_denies_without_valid_cert: arrange ops-auth default-OFF, no XFCC / act GET the canonical /ops/reconciliation route (imported helpers, no new route) / assert byte-identical 401 ERR_OPS_UNAUTHORIZED, unchanged from before this task.
</test_plan>

Tests live in: `apps/gateway/tests/ops_platform_job_identity/` · MUST run red (missing implementation) before Build. RED reason: `resolve_platform_credential` and `PLATFORM_TENANT_MISSING` do not exist yet (ImportError).

RED CONFIRMED (2026-07-03, isolated DB): `uv run pytest tests/ops_platform_job_identity/ -v` →
**4 failed, 1 passed**, zero errors, zero flakes. All 4 failures are the exact predicted reason —
`ImportError: cannot import name 'resolve_platform_credential' from 'gateway.ops.api.deps'`. The
1 pass (`test_require_ops_still_denies_without_valid_cert`) is the deliberate pre-existing-
behavior guard — it exercises the canonical, unmodified `/ops/reconciliation` route and its
existing `require_ops` denial, which this task's new function only composes with, never touches.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/ops/api/deps.py` · `apps/gateway/src/gateway/core/error_catalog.py` · `apps/gateway/tests/ops_platform_job_identity/`
Strategy (ordered batches):
  1. `error_catalog.py`: add `PLATFORM_TENANT_MISSING = ErrorSpec(500, "ERR_PLATFORM_TENANT_MISSING", "Platform tenant not provisioned")`, inserted right after `OPS_FORBIDDEN`.
  2. `ops/api/deps.py`: add `resolve_platform_credential(resolver, session, provider)`; new imports — `AsyncSession` (sqlalchemy.ext.asyncio), `get_platform_tenant` (tenants.infrastructure.repository), `TenantCredentialResolver` + `resolve_provider_credential` (proxy), `PLATFORM_TENANT_MISSING` (core.error_catalog).
  3. Tests: `conftest.py` fakes/fixtures + `test_ops_platform_job_identity.py` (5 functions); run red → green.
  4. Re-run the full, untouched `tests/operator_wide_reconciliation/` suite (OW1-OW9) — the regression proof that `GET /ops/reconciliation` is unaffected.
Known-problem fixes:
  - Verify `get_platform_tenant` is actually exported (no restrictive `__all__` omission) from the sibling's shipped `repository.py` — CONFIRMED present (platform-tenant-seed BUILT).
  - Cross-module import-cycle risk (`ops/api/deps.py` → `proxy/application/use_cases.py`): verify no cycle before wiring; if one appears, import `resolve_provider_credential` lazily inside the function body (`# noqa: PLC0415`, an established late-import pattern already used elsewhere in this codebase).
Strategy actually used: exactly as planned, zero deviation. The named contingency (lazy in-function
  import to dodge an `ops/api/deps.py` ↔ `proxy/application/use_cases.py` cycle) was checked first
  (`grep -rn "gateway\.ops" src/gateway/proxy/` and the reverse — zero hits both ways) and confirmed
  unnecessary, so a plain top-level import was used. Built by a single background subagent
  (backend-expert, sonnet) under a shared-context prompt alongside the independent superadmin-role
  build; diff manually re-verified line-by-line against §3 CONTRACT afterward (byte-identical body
  + docstring).
Safety rule (feature-specific): fails closed — a missing platform tenant raises rather than fabricating a tenant_id.
Code lives in: `./src/`
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

- [x] all tests pass
- [x] coverage did not decrease
- [x] no test or contract was altered during build
- [x] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [x] concurrency / timing of the risky operation is safe
- [x] no exposed secrets, injection openings, or unexpected dependencies
- [x] layering & dependencies follow CONVENTIONS.md
- [x] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] all 5 new tests pass — confirmed by `uv run pytest tests/ops_platform_job_identity/ -v`:
      **5 passed, 0 failed** (1.39s, isolated DB).
- [x] full `tests/operator_wide_reconciliation/` suite (OW1-OW9) unaffected — confirmed by re-run:
      **9 passed, 0 failed** (3.10s) — byte-identical to before this task.
- [x] `resolve_platform_credential` never filters `tenants` by `kind='platform'` directly —
      confirmed by manual `git diff` review: the only tenant lookup is `await
      get_platform_tenant(session)`; no raw `kind=` filter anywhere in the diff.
- [x] zero lines changed in `ops/api/router.py` — confirmed by `git diff --stat` showing no entry
      for that file at all.
- [x] `ruff check` / `ruff format --check` / `uv run pyright` (strict) all clean — 0 errors, 0
      warnings on both changed files.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `resolve_platform_credential` is imported and called by 4 of the 5 new
      tests (all but the `require_ops` regression test, which deliberately never reaches it);
      `PLATFORM_TENANT_MISSING` is referenced by both `deps.py` (raised) and the test asserting
      `exc_info.value.code == "ERR_PLATFORM_TENANT_MISSING"`. Confirmed via
      `grep -rn "resolve_platform_credential\|PLATFORM_TENANT_MISSING" apps/gateway/src
      apps/gateway/tests` — no orphaned symbol.
- [x] DEAD-CODE (code) — `resolve_platform_credential` has NO production caller yet (no HTTP
      endpoint exists in this task, by design — §1 assumption). Recorded as a deliberate, flagged
      exception, not an oversight: it is the same designed seam pattern as
      `get_platform_tenant` before it, intended for a future job/endpoint to wire in.
- [x] SEMANTIC (prose / non-code) — read in full, not skimmed: the full diff (43 lines across 2
      files) was compared side-by-side against §3 CONTRACT's literal function body and docstring —
      byte-identical. Confirmed zero independent auth/timeout/retry logic was added beyond what
      §3 specifies (the single biggest risk this task's design flagged).

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: self (orchestrator manual review, following the build subagent's own report)
Adversarially checked: (1) whether the 5 tests are vacuous — specifically whether
`test_missing_platform_tenant_raises_500` actually proves the resolver is never consulted when
the platform tenant is missing (it does: `assert platform_resolver.calls == []`, which would fail
if the implementation fabricated a tenant_id and called the resolver anyway); (2) whether
`test_ops_authenticated_caller_resolves_platform_credential`'s decoy-tenant assertion
(`assert (decoy_tenant_id, "minimax") not in platform_resolver.calls`) actually proves
`get_platform_tenant`'s result is threaded through, not hardcoded — it does, since the fixture
programs BOTH the decoy and the real platform tenant id with valid credentials, so only a correct
implementation distinguishes them; (3) whether the build subagent's report of file scope was
accurate — independently re-ran `git diff --stat` myself rather than trusting the report alone,
confirmed identical. No overfit, no stubbed-away logic found.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (contract freeze, §3, "Freeze as drafted") + AI self-review (orchestrator,
following a backend-expert subagent's build + this record's independent diff re-verification) ·
date: 2026-07-03

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): ERR_PLATFORM_TENANT_MISSING rate (should be zero post-seed) · ERR_PROVIDER_KEY_MISSING rate on platform-tenant credential resolution

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang)
- [AI] build — strategy used: exactly as planned, zero deviation. The named contingency (lazy in-function
- [AI] verify — gate PASS (reviewed by Tin Dang (contract freeze, §3, "Freeze as drafted") + AI self-review (orchestrator,)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
- [ADD · open] this task's own `gate PASS` was the one that actually hit the tree-wide §5
  scope-lock cross-contamination and consumed 1/3 heal attempts — full analysis and recovery
  pattern recorded in sibling task `superadmin-role`'s §7 (same milestone, same root cause: both
  tasks' Build phases ran concurrently, non-worktree-isolated, in the shared tree) (evidence: this
  task's `gate PASS` failed with `scope_violation` naming files from `superadmin-role`'s build,
  before the sibling)
