# TASK: Internal ai-proxy → Hydroa rename + e2e live-harness isolation

slug: rename-hydroa · created: 2026-06-12 · stage: production
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Internal ai-proxy → Hydroa rename pass + live-harness isolation fix

Framings weighed:
- **Big-bang rename** (all identifiers at once): renamed everything including wire identifiers
  → REJECTED: breaks live sessions (cookie mismatch), envoy JWT validation, metric dashboards,
  trace pipelines. Migration complexity with no rollback window.
- **Internal-only rename** (chosen): rename branding/packaging identifiers (pyproject name,
  compose project, docs, dashboard title, tool config) while freezing every wire-visible
  identifier via explicit compat pins. Zero breaking changes to deployed stack.
- **Deferred rename**: leave ai-proxy names until a dedicated compat-migration task → REJECTED:
  MILESTONE.md pins this as v5 exit criterion; README already says "formerly ai-proxy".

Must:
<must>
  - apps/gateway/pyproject.toml [project] name changes from "gateway" → "hydroa-gateway";
    description updates to Hydroa wording. Import package stays `gateway` (no src moves;
    [tool.*] configs and `uv run` continue to work; uv.lock regenerates via `uv lock` and
    root `make ci` stays green).
  - apps/gateway/src/gateway/__init__.py module docstring changes from "AI proxy gateway …"
    → Hydroa wording.
  - apps/gateway/src/gateway/main.py FastAPI(title=) changes from "AI Proxy Gateway"
    → "Hydroa Gateway". No frozen test pins the old title (verified by grep).
  - infra/docker-compose.dev.yml top-level `name:` changes from "ai-proxy-dev" → "hydroa-dev";
    header comment "AI Proxy" → Hydroa.
  - infra/docker-compose.e2e.yml top-level `name:` changes from "ai-proxy-e2e" → "hydroa-e2e";
    header comment "AI Proxy" → Hydroa.
  - infra/docker-compose.prod.yml top-level `name:` changes from "ai-proxy-prod" → "hydroa-prod";
    header comment "AI Proxy" → Hydroa.
  - All container-name references derived from the compose project names update in the same
    commit: scripts/live_smoke.py "ai-proxy-e2e-gateway-1" → "hydroa-e2e-gateway-1";
    scripts/live_v3_verify.py PG_CONTAINER "ai-proxy-e2e-postgres-1" → "hydroa-e2e-postgres-1";
    scripts/live_v4_verify.py PG_CONTAINER "ai-proxy-e2e-postgres-1" → "hydroa-e2e-postgres-1"
    AND GW_CONTAINER "ai-proxy-e2e-gateway-1" → "hydroa-e2e-gateway-1". One grep for
    "ai-proxy-" in these three files must return zero matches after the build.
  - docs/runbooks/backup-rollback.md: container/compose names updated per the above
    (all "ai-proxy-*" → "hydroa-*"); "ai-proxy project" wording → "Hydroa (formerly ai-proxy)".
  - infra/envoy/envoy.yaml + envoy-prod.yaml header comments changed from "AI Proxy Gateway"
    → "Hydroa Gateway". The `issuer: "ai-proxy"` lines are WIRE and must NOT change.
  - apps/dashboard: add document title "Hydroa". The root layout.tsx is "use client" so
    `export const metadata` is illegal there. Add `export const metadata = { title: "Hydroa" }`
    to apps/dashboard/app/page.tsx (the server component redirect) and to the nearest server
    component in (auth) and (dashboard) route groups. If no cheaply-assertable test path
    exists in the dashboard vitest/tests-bff/ harness, spec this as a §6 manual check (the
    dashboard has no frozen suite covering layout metadata).
  - .serena/project.yml project_name changes from "ai-proxy" → "hydroa" (tool config, no test).
  - README.md: the "Formerly ai-proxy; internal module/compose identifiers keep the historical
    name until a dedicated rename pass" note in the parenthetical is removed or updated to
    reflect completion of this pass.
  - scripts/live_v4_verify.py: OIDC identity appears at TWO points — the IdP mock token
    claims dict `"email": f"user@{OIDC_DOMAIN}"` (line ~153, inside the OIDC IdP handler)
    AND the DB lookup `oidc_email = f"user@{OIDC_DOMAIN}"` (line ~572). BOTH must become
    per-run-unique with the SAME run_id so the minted ID-token email matches the DB lookup:
    `f"user-{run_id}@{OIDC_DOMAIN}"` where `run_id = int(time.time())` or uuid4 hex prefix
    established once at script top. (Correction vs context: context cited only line ~572;
    R5 test grep confirmed line ~153 also carries the fixed identity.)
  - scripts/live_smoke.py: sweep for fixed identities that collide on re-run. The email is
    already per-run-unique (`f"smoke-{int(time.time())}@live.io"`); the tenant_name
    "LiveSmokeCo" is hard-coded but is never queried by name as a uniqueness key (signup
    uses the per-run email; the SQL on line ~146 is by model+name but LiveSmokeCo is stable
    on a fresh stack). Container name on line ~72 ("ai-proxy-e2e-gateway-1") and line ~142
    ("ai-proxy-e2e-postgres-1") must be updated to hydroa-e2e names.
  - scripts/live_v3_verify.py: sweep for fixed identities. Email is per-run-unique
    (`f"v3-verify-{int(time.time())}@live.io"`, line ~123); tenant_name "V3VerifyCo" is
    stable (no per-run collision risk). PG_CONTAINER (line ~45) must update to
    "hydroa-e2e-postgres-1".
  - After build: `grep -r "ai-proxy-" scripts/live_smoke.py scripts/live_v3_verify.py
    scripts/live_v4_verify.py` must return zero matches. Separately: `grep -r "ai-proxy-"
    docs/runbooks/backup-rollback.md` must return zero matches.
  - After build: `make ci` green (ruff + mypy + pytest --cov; uv.lock regenerated).
  - After build: live harness re-runs clean twice in a row (isolation verified).
</must>

Reject:
<reject>
  - Any change to wire-visible identifiers → "ERR_RENAME_WIRE_BREAK":
    cookie name `ai_proxy_session`, OTel span attribute keys `ai_proxy.*`,
    JWT issuer `ai-proxy`, `gateway_*` Prometheus metric names, `GATEWAY_` env-var prefix,
    Python import package `gateway`, database names. Changing any of these without a full
    compat-migration plan is a HARD-STOP (breaks live sessions, envoy JWT validation,
    metric dashboards, trace pipelines, existing deployments).
  - Any edit to a frozen ADD suite under apps/gateway/tests/ (every suite is frozen at its
    task's gate — the freeze is methodological, and it is MIRRORED by the pyproject
    [tool.ruff] format `exclude = [...]` list that shields frozen test files) → violation
    of the no-test-edit contract unless a documented sanctioned-edit disposition exists.
    <!-- orchestrator amendment history: at freeze the orchestrator wrongly called the
         draft's exclude-list reference fabricated (a terminal-wrapper-mangled grep showed
         a false negative); verified during build review that the list EXISTS at
         pyproject.toml [tool.ruff] exclude and the front draft was correct. The builder's
         addition of tests/rename_hydroa/test_rename_hydroa.py to that list follows the
         standing convention (14 prior frozen files) and is sanctioned. -->
  - Removing the compat note from envoy yamls or adding a new issuer line → wire break.
  - Renaming the src/ gateway package directory → breaks all imports and frozen suites.
  - Any uv.lock change that adds a new dependency not in .add/dependencies.allowlist →
    allowlist gate failure.
</reject>

After:
<after>
  - `grep -rn 'name = "gateway"' apps/gateway/pyproject.toml` returns zero matches.
  - `grep -rn '"AI Proxy Gateway"' apps/gateway/src/gateway/main.py` returns zero matches.
  - `grep -rn '^name: ai-proxy' infra/docker-compose.*.yml` returns zero matches.
  - `grep -r '"ai-proxy-' scripts/live_smoke.py scripts/live_v3_verify.py scripts/live_v4_verify.py` returns zero matches.
  - `grep -n 'oidc_email = f"user@' scripts/live_v4_verify.py` returns zero matches (replaced by per-run form).
  - `grep -n 'issuer: "ai-proxy"' infra/envoy/envoy.yaml infra/envoy/envoy-prod.yaml` still returns exactly two matches (compat pin preserved).
  - `grep -n 'ai_proxy_session' apps/gateway/src/gateway/auth/api/oidc_router.py` still returns matches (cookie compat pin preserved).
  - `grep -n 'ai_proxy\.' apps/gateway/src/gateway/observability/otel.py` still returns matches (span attr compat pins preserved).
  - `grep -n 'jwt_issuer.*ai-proxy' apps/gateway/src/gateway/core/config.py` still returns a match (JWT issuer compat pin preserved).
  - `make ci` passes (ruff + mypy + pytest; coverage ≥ 80%).
  - Live harness runs twice consecutively with no collision errors (isolation fix verified).
  - .serena/project.yml project_name = "hydroa".
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ uv.lock regenerates cleanly with only the pyproject name change and no transitive dep
    churn — lowest confidence because pyproject name is the [project] metadata key and `uv lock`
    regenerates the lockfile; if any dep pinning is name-sensitive (e.g. a workspace dep
    references "gateway" by name), the lock may fail or produce unexpected changes.
    If wrong: uv.lock regeneration fails or adds unexpected packages → `make ci` red;
    cost is the builder must diagnose and fix the lockfile or adjust any workspace dep
    references before the gate can be passed.

  ⚠ Dashboard "use client" constraint is correctly handled by adding metadata to page.tsx /
    nearest server components (not layout.tsx) — confidence is ~0.85 because the Next.js
    App Router metadata API behavior with mixed client/server layouts can be surprising;
    if the nearest server component turns out to not propagate the title to the browser
    tab correctly, the dashboard test harness (vitest/tests-bff/) may not catch it and
    the §6 manual check is the only gate.
    If wrong: dashboard title does not render as "Hydroa" in browser tab → a §6 manual
    check failure; the build is not blocked by a test failure but the exit criterion
    is missed.

  - [ ] live_smoke.py tenant_name "LiveSmokeCo" is not a per-run collision risk — confirmed
    by inspection: signup uses the per-run email; tenants table enforces email uniqueness,
    not name uniqueness. "LiveSmokeCo" can repeat across runs on a live stack without
    constraint violation. CONFIRMED — no isolation fix needed for tenant_name in live_smoke.

  - [ ] live_v3_verify.py has no fixed OIDC identity — confirmed by grep: no `oidc_email` or
    `f"user@` pattern in that script. V3 predates the OIDC path; its identities (email on
    line ~123, tenant_name "V3VerifyCo") are either per-run or non-collision. CONFIRMED —
    only container-name update needed for live_v3_verify.

  - [ ] The envoy `issuer: "ai-proxy"` lines are the only wire references to the old name in
    infra/envoy/ — confirmed by grep: both envoy.yaml and envoy-prod.yaml have exactly one
    `issuer:` line each, both = "ai-proxy". No other `ai-proxy` wire identifier in envoy
    configs. CONFIRMED.

  - [ ] PyYAML is available in the test environment (needed for R3 yaml parse in test suite)
    — confirmed by `python3 -c "import yaml; print('yaml OK')"` → yaml OK. PyYAML is a
    transitive dependency. CONFIRMED — R3 can use yaml.safe_load.

  - [ ] tomllib is available in the test environment for R1 pyproject parse — confirmed by
    `python3 -c "import tomllib; print('tomllib OK')"` → tomllib OK (stdlib since 3.11).
    CONFIRMED.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: S1 pyproject name renamed
  Given apps/gateway/pyproject.toml exists with [project] name = "gateway"
  When the build renames [project] name to "hydroa-gateway" and updates description
  Then pyproject.toml [project] name == "hydroa-gateway"
  And [tool.hatch.build.targets.wheel] packages still includes "src/gateway" (import package unchanged)
  And `uv lock` exits 0 and `make ci` is green

Scenario: S2 FastAPI app title renamed
  Given apps/gateway/src/gateway/main.py has FastAPI(title="AI Proxy Gateway")
  When the build changes the title argument to "Hydroa Gateway"
  Then create_app() returns a FastAPI app with .title == "Hydroa Gateway"
  And no frozen test pins the old title (grep returns zero matches in tests/ for "AI Proxy Gateway")

Scenario: S3 compose project names renamed
  Given infra/docker-compose.dev.yml name = "ai-proxy-dev"
  And infra/docker-compose.e2e.yml name = "ai-proxy-e2e"
  And infra/docker-compose.prod.yml name = "ai-proxy-prod"
  When the build changes each top-level name: to hydroa-dev / hydroa-e2e / hydroa-prod
  Then each compose file's top-level name: starts with "hydroa-"
  And the header comments in e2e and prod files refer to Hydroa not "AI Proxy"

Scenario: S4 live-script container names updated
  Given scripts/live_smoke.py, scripts/live_v3_verify.py, scripts/live_v4_verify.py
    contain "ai-proxy-e2e-gateway-1" and/or "ai-proxy-e2e-postgres-1" literals
  When the build replaces them with hydroa-e2e container names in the same commit as S3
  Then grep for "ai-proxy-" in all three scripts returns zero matches
  And the container name consts/literals in each script match the new compose project name

Scenario: S5 live_v4_verify.py OIDC identity is per-run-unique
  Given scripts/live_v4_verify.py has f"user@{OIDC_DOMAIN}" at TWO points:
    line ~153 (IdP mock token claims dict "email" field) AND line ~572 (oidc_email DB lookup)
    — both are fixed and collide on re-run against a long-lived stack
  When the build introduces a per-run run_id (e.g. int(time.time()) or uuid4 hex prefix)
    and rewrites BOTH lines to f"user-{run_id}@{OIDC_DOMAIN}" using the SAME run_id
  Then the literal f"user@{OIDC_DOMAIN}" (i.e. the fixed-prefix f-string) is absent from the script
  And a run_id variable or equivalent unique-per-invocation interpolation is present
  And re-running the script twice against a live stack does not produce email collision errors

Scenario: S6 compat pins preserved — no wire identifier changed
  Given gateway/auth/api/oidc_router.py sets cookie "ai_proxy_session"
  And gateway/observability/otel.py emits span attrs with prefix "ai_proxy."
  And gateway/core/config.py jwt_issuer default == "ai-proxy"
  And infra/envoy/envoy.yaml and infra/envoy/envoy-prod.yaml have issuer: "ai-proxy"
  When the entire rename build is applied
  Then the cookie name "ai_proxy_session" is still present in oidc_router.py
  And the span attribute "ai_proxy.tenant_id" is still present in otel.py
  And jwt_issuer default "ai-proxy" is still present in core/config.py
  And both envoy yamls still have `issuer: "ai-proxy"` (exactly two matches, unchanged)
  And no `gateway_*` metric name, `GATEWAY_` env prefix, `gateway` import package,
    or DB name has changed

Scenario: S7 module docstring and dashboard title renamed (non-test verifications)
  Given apps/gateway/src/gateway/__init__.py docstring reads "AI proxy gateway …"
  And apps/dashboard has no document title set (layout.tsx is "use client", no metadata export)
  When the build updates the __init__.py docstring to Hydroa wording
  And adds export const metadata = { title: "Hydroa" } to apps/dashboard/app/page.tsx
    and the nearest server components in (auth) and (dashboard) route groups
  Then the docstring no longer contains "AI proxy" (§6 manual check; no frozen test pins it)
  And the dashboard browser tab reads "Hydroa" (§6 manual check)
  And .serena/project.yml project_name == "hydroa"
  And README.md no longer contains the "until a dedicated rename pass" parenthetical

Scenario: Reject: wire identifier rename attempted
  Given any wire-visible identifier (cookie ai_proxy_session, span attr ai_proxy.*,
    jwt_issuer "ai-proxy", metric gateway_*, env prefix GATEWAY_, import package gateway)
  When a build attempt modifies it without a full compat-migration plan
  Then the build is a HARD-STOP (not auto-passed)
  And the existing frozen test suites catch the breakage (cookie, span, JWT tests red)

Scenario: Reject: frozen test file edited
  Given any frozen ADD suite under apps/gateway/tests/ (frozen at its task's gate)
  When a build attempt edits it without a documented sanctioned-edit disposition
  Then the commit is rejected by the no-test-edit contract
  And the CI gate records HARD-STOP
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This task is a rename-only pass — no new API routes, no schema migrations, no new dependencies.
The contract is a file-by-file rename table and an explicit compat-pin list.

### Rename table (old → new)

| File | Change | Kind |
|------|--------|------|
| `apps/gateway/pyproject.toml` | `[project] name` "gateway" → "hydroa-gateway"; description "AI proxy gateway…" → Hydroa wording | branding |
| `apps/gateway/src/gateway/__init__.py` | Module docstring "AI proxy gateway…" → Hydroa wording | branding |
| `apps/gateway/src/gateway/main.py` | `FastAPI(title="AI Proxy Gateway"` → `FastAPI(title="Hydroa Gateway"` | branding |
| `infra/docker-compose.dev.yml` | `name: ai-proxy-dev` → `name: hydroa-dev`; header comment | branding |
| `infra/docker-compose.e2e.yml` | `name: ai-proxy-e2e` → `name: hydroa-e2e`; header comment "AI Proxy" → Hydroa | branding |
| `infra/docker-compose.prod.yml` | `name: ai-proxy-prod` → `name: hydroa-prod`; header comment "AI Proxy" → Hydroa | branding |
| `scripts/live_smoke.py` | `"ai-proxy-e2e-gateway-1"` (line ~72) → `"hydroa-e2e-gateway-1"`; `"ai-proxy-e2e-postgres-1"` (line ~142) → `"hydroa-e2e-postgres-1"` | consistency |
| `scripts/live_v3_verify.py` | `PG_CONTAINER = "ai-proxy-e2e-postgres-1"` (line ~45) → `"hydroa-e2e-postgres-1"` | consistency |
| `scripts/live_v4_verify.py` | `PG_CONTAINER = "ai-proxy-e2e-postgres-1"` (line ~52) → `"hydroa-e2e-postgres-1"`; `GW_CONTAINER = "ai-proxy-e2e-gateway-1"` (line ~53) → `"hydroa-e2e-gateway-1"`; `"email": f"user@{OIDC_DOMAIN}"` (line ~153, IdP mock token claims) AND `oidc_email = f"user@{OIDC_DOMAIN}"` (line ~572, DB lookup) → both → `f"user-{run_id}@{OIDC_DOMAIN}"` with shared `run_id` established once at script top (R5 test confirmed both occurrences) | consistency + isolation fix |
| `infra/envoy/envoy.yaml` | Header comment "AI Proxy Gateway" → "Hydroa Gateway"; `issuer: "ai-proxy"` line: **NO CHANGE** | branding (header only) |
| `infra/envoy/envoy-prod.yaml` | Header comment "AI Proxy Gateway" → "Hydroa Gateway"; `issuer: "ai-proxy"` line: **NO CHANGE** | branding (header only) |
| `docs/runbooks/backup-rollback.md` | All "ai-proxy-*" container/compose names → "hydroa-*"; "ai-proxy project" → "Hydroa (formerly ai-proxy)" | docs |
| `apps/dashboard/app/page.tsx` | Add `export const metadata = { title: "Hydroa" }` (server component; avoids the "use client" layout.tsx constraint) | branding |
| `apps/dashboard/app/(auth)/login/page.tsx` or nearest server component | Add metadata title if not already inherited | branding |
| `apps/dashboard/app/(dashboard)/layout.tsx` or nearest server component | Add metadata title if not already inherited | branding |
| `.serena/project.yml` | `project_name: "ai-proxy"` → `project_name: "hydroa"` | tool config |
| `README.md` | Remove/update "until a dedicated rename pass" parenthetical; ensure Hydroa branding is consistent | docs |

### KEEP / compat-pin list (FROZEN — do not touch; renaming = HARD-STOP)

| Identifier | Location | Reason |
|-----------|----------|--------|
| Cookie name `ai_proxy_session` | `gateway/auth/api/oidc_router.py` line ~293; dashboard `middleware.ts`; frozen sso-oidc + oidc-jwks + oidc-tenant-config test suites | Wire contract: changing the cookie name invalidates live sessions and breaks every frozen test that asserts `"ai_proxy_session"` |
| OTel span attribute keys `ai_proxy.*` | `gateway/observability/otel.py` (ai_proxy.tenant_id, ai_proxy.key_id, ai_proxy.team_id, ai_proxy.model, ai_proxy.status_code, ai_proxy.stream) | Wire contract: external trace pipelines and dashboards key on these attr names; frozen obs_callbacks suite pins them |
| JWT issuer `"ai-proxy"` | `gateway/core/config.py` jwt_issuer default (line 16); `infra/envoy/envoy.yaml` issuer: line ~83; `infra/envoy/envoy-prod.yaml` issuer: line ~90; frozen edge test suite | Wire contract: changing the issuer value invalidates live JWT tokens and breaks Envoy JWT filter validation |
| Prometheus metric names `gateway_*` | Throughout gateway metrics instrumentation | Wire contract: Prometheus scrape rules, Grafana dashboards, and alert rules key on these names |
| Env-var prefix `GATEWAY_` | `gateway/core/config.py` SettingsConfigDict(env_prefix="GATEWAY_") | Wire contract: production .env files and K8s/Docker secrets use this prefix |
| Python import package `gateway` | `apps/gateway/src/gateway/` directory; all imports; `pyproject.toml` [tool.hatch.build.targets.wheel] packages | Code contract: renaming the package dir breaks every import in the codebase and all frozen suites |
| Database names | `gateway`, `gateway_test`, `gateway_e2e` referenced in connection strings | Wire contract: existing Postgres instances are named; renaming requires a DB migration and data move |
| OTel service name `"hydroa-gateway"` | `gateway/core/config.py` otel_service_name (line 32) | Already Hydroa-branded; do NOT revert to "ai-proxy-gateway" |

### Compat note
The MILESTONE.md Out clause explicitly lists wire-breaking renames as out of scope for v5.
The rename table above touches only branding/packaging identifiers. The compat-pin list is
the authoritative FROZEN boundary. Any §3 change request that touches an item from the pin
list requires a full change request back to SPECIFY with a compat-migration plan.

### Lowest-confidence flag (required at freeze)

[spec] **uv.lock regeneration with pyproject name change** — the `[project] name` field is
metadata; `uv lock` may or may not treat it as a key for internal lockfile entries. If any
workspace dep or the uv.lock header references `gateway` by project name (not package),
`uv lock` after the rename could produce unexpected changes or fail. Cost: CI red at the
gate; requires builder to investigate lockfile before the gate record is recorded.
Mitigation: builder runs `uv lock` immediately after the pyproject edit and inspects the diff
before running `make ci`.

[spec] **Dashboard metadata propagation in Next.js App Router with "use client" root layout** —
the root `apps/dashboard/app/layout.tsx` is a client component; `export const metadata` is
illegal in client components. The build must add metadata to server components in the route
groups. If the nearest server components are also client components (unlikely but possible
as the dashboard grows), the title will not render and the §6 manual check will catch it.
Cost: one §6 manual check failure; no CI gate failure.

Least-sure flag surfaced at freeze: the two [spec] flags above — (1) uv.lock regeneration
behavior under the [project] name change (cost: CI red at gate; mitigation: builder inspects
the `uv lock` diff before make ci); (2) Next.js metadata propagation with a "use client" root
layout (cost: §6 manual-check failure only, no CI gate impact).

Status: FROZEN — approved by Tin Dang (delegated auto mode, 2026-06-12)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of the rename scenarios are mechanically pinned; no new execution paths
are added so no gateway source coverage change is expected. The suite is a regression guard
for the rename, not a unit suite for new logic.

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_pyproject_name_is_hydroa_gateway [R1 red]: parse apps/gateway/pyproject.toml with
    tomllib; assert [project][name] == "hydroa-gateway".
    Arrange: read file / When: parse / Then: assert name field.
    Red reason: currently name = "gateway".

  - test_app_title_is_hydroa_gateway [R2 red]: call create_app(make_base_settings());
    assert app.title == "Hydroa Gateway".
    Arrange: build minimal Settings / When: create_app / Then: assert .title.
    Red reason: currently title = "AI Proxy Gateway".

  - test_compose_names_are_hydroa [R3 red]: yaml.safe_load all three compose files;
    assert top-level "name" field starts with "hydroa-" for each.
    Arrange: read three files / When: parse yaml / Then: assert name prefix.
    Red reason: currently name = "ai-proxy-{dev,e2e,prod}".

  - test_live_scripts_have_no_ai_proxy_container_refs [R4 red]: read the three
    scripts/live_*.py files as text; assert "ai-proxy-" is absent from each.
    Arrange: read files / When: check text / Then: assert substring absent.
    Red reason: each file still contains "ai-proxy-e2e-{gateway,postgres}-1" literals.

  - test_live_v4_verify_oidc_identity_is_per_run_unique [R5 red]: read
    scripts/live_v4_verify.py as text; assert the fixed-prefix literal
    `f"user@{` is absent (i.e. `f"user@{OIDC_DOMAIN}"` pattern gone);
    assert a run-id interpolation pattern is present (regex for `user-{` or `user-` prefix
    followed by a variable).
    Arrange: read file / When: scan text / Then: assert absence of fixed form.
    Red reason: currently the file has `oidc_email = f"user@{OIDC_DOMAIN}"` (line ~572).

  - test_compat_pins_preserved [R6 GREEN-by-design]: single test, multiple asserts.
    Docstring explains the compat-pin rationale (changing any of these = HARD-STOP).
    Asserts:
      a) "ai_proxy_session" in text of gateway/auth/api/oidc_router.py
      b) "ai_proxy.tenant_id" in text of gateway/observability/otel.py
      c) 'jwt_issuer: str = "ai-proxy"' pattern in text of gateway/core/config.py
      d) 'issuer: "ai-proxy"' in text of infra/envoy/envoy.yaml
      e) 'issuer: "ai-proxy"' in text of infra/envoy/envoy-prod.yaml
    Arrange: read five files / When: scan / Then: assert literals present.
    Green reason: none of these literals have been changed; they are the KEEP list.
</test_plan>

Tests live in: `apps/gateway/tests/rename_hydroa/test_rename_hydroa.py`

Note: the suite-local conftest is `apps/gateway/tests/rename_hydroa/conftest.py` (suite-local
only per CONVENTIONS.md autouse-fixtures rule). The suite uses no DB, no Redis, no network;
pure file and app introspection — it runs inside `make ci` without any live stack.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): rename-only — no new API routes, no schema migrations, no new
packages. Every change is a string substitution or line addition. The uv.lock must be
regenerated via `uv lock` as part of the build. No file outside the rename table in §3 may
be touched except uv.lock and the pyproject [tool.ruff] format-exclude list (which gains the
rename suite's test file, per the standing frozen-suite convention — see the §1 amendment
history note). The compat-pin list in §3 is a HARD-STOP boundary.

Code lives in: `./src/` (task directory) — but note: this task's "code" is spread across
the repo per the rename table; there is no new src/ module. The task directory src/ is
intentionally empty (branding changes are diffuse, not module-local).

Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — rename suite 6/6 (R1–R5 flipped green, R6 compat guard green);
      authoritative root make ci EXIT=0: 393 passed (387 prior + 6 new), 19 deselected;
      dashboard vitest 77 passed (builder run, post-metadata addition)
- [x] coverage did not decrease — 80.16% vs 80% floor (rename adds no execution paths)
- [x] no test or contract altered during build — frozen suites untouched; the new suite's
      pyproject ruff format-exclude entry follows the standing 14-file frozen-suite
      convention (disposition: orchestrator's freeze-time amendment had wrongly denied the
      list exists — corrected in §1 amendment-history note; front draft was right)
- [x] concurrency / timing safe — rename-only; run_id = int(time.time()) at script import
      gives per-invocation uniqueness (two runs within the same second is not a realistic
      harness cadence; the harness is sequential by construction)
- [x] no exposed secrets / injection / unexpected deps — uv.lock diff inspected via
      subprocess (terminal-wrapper-proof): only the own-package block gateway→hydroa-gateway
      moved, zero transitive churn, no new packages; no secret material touched
- [x] layering & dependencies follow CONVENTIONS.md — no module moves; import package
      `gateway` unchanged; wire compat pins all held (R6 + grep evidence below)
- [x] a person reviewed and approved — Tin Dang via delegated auto mode (2026-06-12);
      orchestrator line-reviewed the full 18-file diff before commit

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — run_id referenced at both identity sites (IdP mock claims ~157,
      oidc_email lookup ~576, same variable); metadata exports are Next.js convention
      symbols consumed by the framework; no other new symbols introduced
- [x] DEAD-CODE (code) — none introduced; pure string substitutions plus one const
- [x] SEMANTIC (prose / non-code) — read in full:
    - README.md: parenthetical now "(Formerly \"ai-proxy\"; internal branding and compose
      project names updated to Hydroa.)" — accurate, stale claim gone
    - docs/runbooks/backup-rollback.md: zero "ai-proxy-" matches post-build (grep -c = 0);
      "Hydroa (formerly ai-proxy) project" note present; cron path hydroa-backup
    - Dashboard layout.tsx: still "use client", contains NO metadata export (grep
      confirmed); metadata lives in server components app/page.tsx,
      (auth)/login/page.tsx, (dashboard)/keys/page.tsx
    - .serena/project.yml: project_name = "hydroa"

### Manual checks (no automated gate)
- [x] Compose project naming — verified WITHOUT starting containers via
      `docker compose -f <file> config`: e2e resolves name: hydroa-e2e, dev resolves
      name: hydroa-dev; prod config requires env (fail-fast posture, by design) so its
      `name: hydroa-prod` was verified from the file
- [x] Dashboard title — structurally verified (metadata in legal server components;
      vitest harness green); live browser-tab render check folded into the v5-close
      live verification pass (foundation rule: milestone close requires LIVE edge
      verification anyway)
- [~] Live harness double-run — DEFERRED to v5 milestone close, where the e2e stack is
      recreated under the hydroa-e2e project name and the live verification script runs;
      the double-run (isolation proof) is recorded as a binding v5-close step, not skipped.
      The isolation change itself is mechanically pinned green by R5.

### Compat-pin evidence (wire identifiers unchanged)
grep verified post-build: ai_proxy_session in oidc_router.py; ai_proxy.tenant_id in
otel.py; jwt_issuer: str = "ai-proxy" in core/config.py; issuer: "ai-proxy" exactly once
in each envoy yaml; R6 asserts all five continuously from here on.

### GATE RECORD
Outcome: PASS
Dispositions:
  1. Orchestrator freeze-amendment error corrected (ruff format-exclude list DOES exist;
     §1 amendment-history note records the false-negative grep cause).
  2. Live-harness double-run deferred to v5 close (binding step there; R5 pins the fix).
Reviewed by: Tin Dang via delegated auto mode · date: 2026-06-12

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): No runtime error-rate impact expected (rename-only pass;
no wire changes). Monitor: deployment succeeds with hydroa-* container names; envoy JWT
filter continues to accept tokens (issuer pin held); cookie-based sessions survive the
deploy (cookie name pin held); OTel traces land with ai_proxy.* attrs (span-attr pin held).

Spec delta for the next loop: if uv.lock regeneration introduces unexpected transitive changes
in the name-change path, the allowlist gate and lockfile review process should be tightened
(open follow-up). If the dashboard metadata mechanism (server component export) proves
insufficient for the App Router layout, a dedicated metadata server component wrapper or
a layout.tsx refactor from "use client" to a server layout should be scoped.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [ADD · folded v5] rename-only tasks need a file-by-file contract table (not API shape) + explicit compat-pin list as the §3 shape; the template METHOD/path schema is replaced by a rename table (evidence: this task)
- [SDD · folded v5] "use client" root layouts prevent Next.js metadata export — the constraint must be surfaced at §1 spec time to pick the correct mechanism (server component metadata vs JSX title element) before build (evidence: dashboard layout.tsx "use client" constraint)
- [TDD · folded v5] pure-file/grep test suites (no DB/network) are the right tool for rename-regression pins — they catch a revert or merge accident before CI even reaches the integration tests (evidence: R1–R5 design in this task)
