# TASK: Alembic migration Helm-hook Job (migrate-before-boot) + fail-fast Secret wiring

slug: migration-and-secrets · created: 2026-06-26 · stage: production
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

Touches (files · symbols · signatures): the migrate-before-boot Job + its image prerequisite + the secret/DSN reuse, grounded in the real alembic setup + the existing chart wiring —
  - `apps/gateway/Makefile:55` `migrate:` → `cd apps/gateway && uv run alembic upgrade head` — the canonical migration command. `apps/gateway/alembic.ini` (`script_location = migrations`, default `sqlalchemy.url` is the local test DSN) + `apps/gateway/migrations/env.py:89` reads `GATEWAY_DATABASE_URL` from env and `set_main_option("sqlalchemy.url", …)` — so a Job/container that sets `GATEWAY_DATABASE_URL` + runs `alembic upgrade head` from a dir holding `alembic.ini` + `migrations/` migrates to head. `alembic>=1.9` is a RUNTIME dep (`pyproject.toml:18`, not dev) → present in the gateway venv.
  - **GAP (image prerequisite):** `apps/gateway/Dockerfile` runtime stage copies ONLY `src/ ./src/` + `pyproject.toml`/`uv.lock` — it does NOT copy `migrations/` or `alembic.ini` (both live at `apps/gateway/` = the build context root). So `alembic upgrade head` CANNOT run in the current gateway image. THIS task must add `COPY migrations/ ./migrations/` + `COPY alembic.ini ./` to the runtime stage so the migration Job (gateway image) can run. WORKDIR is `/app`, venv bin on PATH → command is `["alembic","upgrade","head"]` from `/app`.
  - `charts/ai-proxy/templates/gateway-deployment.yaml:33-41` — the gateway resolves `GATEWAY_DATABASE_URL` from `gateway.env.databaseUrlSecretRef` (name set → `secretKeyRef`; else the literal `gateway.env.databaseUrl`). The migration Job MUST mirror this EXACT resolution so it migrates the same DB the gateway will serve. `charts/ai-proxy/templates/datastore-secret.yaml:27` mints key `url` = `postgresql+asyncpg://<user>:<pw>@<postgres.fullname>:5432/<db>` — the DSN both the gateway + the Job source (kind/e2e sets `databaseUrlSecretRef.name=ai-proxy-datastore-secrets`).
  - `charts/ai-proxy/values.yaml:29-39` (gateway.env: databaseUrl + databaseUrlSecretRef + drainTimeoutSeconds) · `:101-106` (datastores.secrets) · `:45-49` (gateway.jwtSecret) — the frozen inputs the Job reuses. `_helpers.tpl`: `ai-proxy.gateway.fullname` (Job name base), `ai-proxy.labels`, `ai-proxy.postgres.fullname` (wait-for-db target), `ai-proxy.datastores.secretName`.
  - **Already DONE by task 1 (do NOT re-do):** the gateway Deployment already has the three probes (`/health`), resources requests+limits, `terminationGracePeriodSeconds: drainTimeoutSeconds+5` (graceful drain), and `gateway-pdb.yaml` renders a PDB. So the milestone task's "probes/resources/PDB/graceful-shutdown wiring" is largely satisfied for the gateway — verify-only, not net-new (the net-new core is the migration Job + the image fix).
  - **Fail-fast secret posture already exists:** `ai-proxy.gateway.validateSecret` / `ai-proxy.datastores.validateSecret` / `ai-proxy.envoy.validateTLS` (`_helpers.tpl:44/89/113`) all `fail` for any env outside {dev,test} when a required secret ref is unset; `datastore-secret.yaml` fail-closes on empty creds when create=true. The Job inherits this (it sources the same Secret) — exit criterion "every secret Secret-sourced, fail-fast on unset" is met by reuse, not new guards.
Context (working folder): `.add/milestones/v53/MILESTONE.md` line 19 (MIGRATIONS-BEFORE-BOOT: Helm-hook Job pre-install/pre-upgrade, alembic to head BEFORE the gateway is ready) + line 20 (SECRETS NEVER IN THE CHART, fail-fast) + line 33 (task scope) + exit criterion line 45. `infra/docker-compose.prod.yml` (today migration = host-run `make migrate`, never inside the gateway container — why the image lacks migrations/).
Honors (patterns / conventions): MIGRATIONS-BEFORE-BOOT (gateway never serves an unmigrated DB) · SECRETS-NEVER-IN-CHART (DSN sourced from the datastore Secret, no literal) · DESIGN-FOR-FAILURE (the Job: a bounded wait-for-DB, restartPolicy, backoffLimit, resources, non-root) · external-ready (the same databaseUrlSecretRef the gateway uses → managed DB by values alone).
Anchors the contract cites: NEW `charts/ai-proxy/templates/migration-job.yaml` (the migrate-before-boot Job — image=gateway, GATEWAY_DATABASE_URL mirrored from the gateway's resolution, `alembic upgrade head`, wait-for-db) · the EDITED `apps/gateway/Dockerfile` (COPY migrations/ + alembic.ini into the runtime image) · the reused `databaseUrlSecretRef` + `ai-proxy.datastores.secretName` + `ai-proxy.postgres.fullname`.

⚠ DESIGN FORK for §1/§3 (surface to Tin): the milestone names a "Helm-hook Job (pre-install/pre-upgrade)", but on a FRESH `helm install` with in-cluster Postgres, pre-install hooks run BEFORE the (non-hook) Postgres StatefulSet is created → the hook Job cannot reach the DB. Two reconciliations: (A) a gateway-pod **initContainer** running alembic (robust, naturally ordered after the Postgres StatefulSet via a wait loop, works install+upgrade, literal migrate-before-boot; trades: runs per-replica, departs from "Job" wording) vs (B) a Helm **hook Job** (matches the wording; correct when the DB pre-exists = the external/managed cloud posture; on fresh in-cluster install needs the DB up first). Recommend (A) for the kind-validated in-cluster goal, document (B) as the cloud/external-DB pattern — Tin decides at the freeze.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Migrate-before-boot for the gateway — `alembic upgrade head` runs as a gateway-pod initContainer (after a bounded wait for Postgres) so the gateway container never starts against an unmigrated DB, the gateway image is taught to carry the migration assets, and the migration sources its DB DSN from the SAME Secret the gateway uses (no literal, fail-fast on unset).
Framings weighed: gateway-pod **initContainer** running alembic after a bounded wait-for-Postgres (chosen — Tin 2026-06-27; robust on fresh kind install + upgrade + external/managed DB, literal "never serves an unmigrated DB"; naturally ordered after the StatefulSet via the wait loop) · Helm pre-install/pre-upgrade **hook Job** (rejected — on a fresh in-cluster install pre-install hooks run before the non-hook Postgres StatefulSet → no DB to reach; correct only when the DB pre-exists) · hybrid hook-Job + init-guard (rejected — two migration paths, more surface for no kind benefit).
Must:
<must>
  - M1 — the gateway IMAGE carries the migration assets: `apps/gateway/Dockerfile` runtime stage COPYs `migrations/ ./migrations/` + `alembic.ini ./` (alongside `src/`), so `alembic upgrade head` resolves `alembic.ini` (`script_location = migrations`) from WORKDIR `/app` with the venv (`alembic` runtime dep) on PATH.
  - M2 — the gateway Deployment renders, BEFORE the gateway container, two ordered initContainers using the gateway image: (1) `wait-for-db` — a BOUNDED loop (max attempts → non-zero exit) that blocks until the Postgres host:port is reachable, using a tool guaranteed in the image (python `socket`, not nc/pg_isready); (2) `migrate` — `alembic upgrade head`. initContainer semantics guarantee the gateway container starts only after both succeed.
  - M3 — the `migrate` initContainer's `GATEWAY_DATABASE_URL` is resolved IDENTICALLY to the gateway container — from `gateway.env.databaseUrlSecretRef` (secretKeyRef) when its name is set, else the literal `gateway.env.databaseUrl` — so it always migrates the exact DB the gateway will serve; NO separate or hardcoded DSN, NO secret literal in the chart.
  - M4 — design-for-failure on the init path: the wait is bounded (configurable max, default ~60 attempts/timeout) so a real DB outage fails the pod (CrashLoopBackOff) rather than hanging silently; both initContainers run non-root (inherit the pod securityContext) with resource requests+limits; the gateway's already-wired probes / PDB / `terminationGracePeriodSeconds` graceful-shutdown remain intact (verify-only, not re-authored).
  - M5 — a default-ON toggle `gateway.migrate.enabled` (default true) gates the migrate initContainer so an operator who runs migrations out-of-band (external CD) can disable the in-pod migrate; the `wait-for-db` initContainer is independently useful and stays.
</must>
Reject:
<reject>
  - the gateway image/Dockerfile does not copy migrations/ or alembic.ini (the initContainer would fail at runtime) -> "migration_assets_absent"
  - the migrate initContainer's GATEWAY_DATABASE_URL differs from the gateway container's resolution (would migrate a different DB than served) -> "migrate_dsn_mismatch"
  - the wait-for-db loop is unbounded (an infinite hang masks a real DB outage) -> "wait_unbounded"
  - a DB password / DSN literal is shipped in the chart for the migration -> "secret_literal_in_chart"
  - the migration runs as a sidecar / post-start / non-init step (so the gateway could serve before migration) -> "migrate_after_boot"
</reject>
After:
<after>
  - `helm template` renders the gateway Deployment with `initContainers: [wait-for-db, migrate]` ahead of the gateway container, both using the gateway image with the mirrored `GATEWAY_DATABASE_URL`; the gateway container + its probes/resources/PDB are otherwise unchanged; `apps/gateway/Dockerfile` copies `migrations/` + `alembic.ini`; `gateway.migrate.enabled=false` drops the migrate initContainer but keeps wait-for-db; the full tests/helm suite (incl. the sibling task-1..4 suites) stays green + `helm lint` 0.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ the gateway image runs alembic correctly from WORKDIR `/app` once `alembic.ini` + `migrations/` are COPYd there — lowest confidence because the COPY must land them at `/app` (not `/app/src`) and `script_location = migrations` is relative; if wrong: the initContainer dies "No config file 'alembic.ini' found" or "Path doesn't exist: 'migrations'". Mitigate: a source-parse test asserts the Dockerfile COPYs both to the workdir; the runtime truth proves at kind-bootstrap (task 6).
  - [ ] the bookworm-slim gateway image lacks `nc`/`pg_isready` but HAS python (it IS a python image) → the wait-for-db loop uses a python `socket.create_connection`, not a shell netcat — confirm the wait command is python-based.
  - [ ] multi-replica: every gateway pod runs the migrate initContainer; alembic is idempotent (version table + transactional DDL), so concurrent first-migration across replicas is at worst one-wins/others-noop — acceptable for the kind-validated MVP (a Postgres advisory lock is a later hardening delta).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
# --- one per Must ---

Scenario: Gateway image carries the migration assets (M1)
  Given apps/gateway/Dockerfile runtime stage
  When the COPY lines are read
  Then it copies migrations/ to the workdir AND alembic.ini to the workdir (alongside src/)
  And the gateway container CMD (uvicorn) is unchanged

Scenario: Migrate-before-boot initContainers render in order (M2)
  Given the default render with a datastore Secret referenced
  When the gateway Deployment pod spec is parsed
  Then initContainers = [wait-for-db, migrate] BEFORE the gateway container
  And both use the gateway image; migrate runs `alembic upgrade head`; wait-for-db blocks on the Postgres host:port via python (no nc/pg_isready)

Scenario: Migrate sources the same DSN as the gateway (M3)
  Given gateway.env.databaseUrlSecretRef.name is set
  When the migrate initContainer env is parsed
  Then GATEWAY_DATABASE_URL is a secretKeyRef to the SAME (name,key) the gateway container uses
  And when databaseUrlSecretRef.name is empty it falls back to the SAME literal gateway.env.databaseUrl — never a separate DSN

Scenario: The init path is design-for-failure (M4)
  Given the rendered initContainers
  When their command + securityContext + resources are read
  Then the wait loop is bounded (a max attempts/timeout, exits non-zero on exhaustion — no infinite loop)
  And both initContainers run non-root with resources requests+limits
  And the gateway container's probes, PDB, and terminationGracePeriodSeconds are unchanged

Scenario: Migrate is toggleable, wait-for-db stays (M5)
  Given gateway.migrate.enabled=false
  When the gateway Deployment is parsed
  Then the migrate initContainer is absent but wait-for-db remains
  And gateway.migrate.enabled defaults to true (migrate-before-boot on by default)

Scenario: Wait-for-db targets the ACTUAL DB at runtime (v2 F2)
  Given the wait-for-db initContainer
  When its env + command are parsed
  Then it carries the SAME GATEWAY_DATABASE_URL resolution as migrate AND its python parses host:port from that env (urlparse), falling back to the in-cluster helper
  And it renders even on the external-DB path (datastores.postgres.enabled=false) so that path also gets a bounded wait

Scenario: The gateway pod is container-hardened (v2 F3)
  Given the default render
  When the gateway container + both initContainers securityContexts are read
  Then each has allowPrivilegeEscalation:false and capabilities.drop=[ALL]
  And runAsUser/runAsNonRoot remain pod-level (the task-1 gateway suite stays green)

# --- one per Reject ---

Scenario: Missing migration assets is rejected (migration_assets_absent)
  Given a Dockerfile that does not copy migrations/ or alembic.ini
  When inspected
  Then the absence is treated as a failing build (the runtime initContainer would error)

Scenario: DSN mismatch is rejected (migrate_dsn_mismatch)
  Given the migrate initContainer
  When its GATEWAY_DATABASE_URL resolution is compared to the gateway container's
  Then a different secretRef/key or a divergent literal fails the check

Scenario: Unbounded wait is rejected (wait_unbounded)
  Given the wait-for-db command
  When parsed
  Then a loop with no attempt/time bound (would hang forever) fails the check

Scenario: A secret literal in the chart is rejected (secret_literal_in_chart)
  Given the rendered migration/init env + values
  When scanned
  Then no DB password or DSN-with-password literal is shipped (sourced from the Secret only)

Scenario: Post-boot migration is rejected (migrate_after_boot)
  Given the migration placed as a sidecar / post-start / non-init step
  When the pod spec is parsed
  Then it fails — migration MUST be an initContainer so the gateway never serves first
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

CONTRACT = the gateway image carrying migration assets + the gateway Deployment's two
migrate-before-boot initContainers + one new values toggle. EXTENDS the frozen gateway
artifacts ADDITIVELY (the gateway container, its probes/PDB/resources are untouched);
adds NO new template file — the init path lives in the existing gateway-deployment.yaml.

```
# --- INPUT: apps/gateway/Dockerfile (runtime stage) ---
COPY src/ ./src/                      # (existing)
COPY migrations/ ./migrations/        # NEW — alembic revision scripts
COPY alembic.ini ./                   # NEW — alembic config (script_location = migrations)
# WORKDIR /app, venv on PATH → `alembic upgrade head` resolves /app/alembic.ini + /app/migrations

# --- INPUT: values.yaml gateway sub-schema (additive keys) ---
gateway:
  # NOTE: replicas:2 default — both pods run `alembic upgrade head`; alembic serializes via a
  # lock on alembic_version, so concurrent runs are safe (loser sees head, exits 0). For a LARGE
  # initial migration set --set gateway.replicas=1 to avoid a lock-wait timeout on the loser. (F5)
  migrate:
    enabled: true                     # migrate-before-boot ON by default; false = operator migrates out-of-band
    waitForDb:
      maxAttempts: 60                  # bounded wait (×2s ≈ 120s); exhaustion → non-zero exit (no infinite hang)
  containerSecurityContext:           # NEW (v2, F3) — applied to the gateway container + BOTH initContainers
    allowPrivilegeEscalation: false
    capabilities: { drop: ["ALL"] }
  # (databaseUrl / databaseUrlSecretRef / podSecurityContext / image — REUSED, unchanged)

# --- OUTPUT: gateway-deployment.yaml pod spec (ADDITIVE — gateway container behavior unchanged) ---
spec.template.spec.initContainers:           # rendered BEFORE the gateway container
  - name: wait-for-db                        # renders whenever the init block does (postgres.enabled OR migrate.enabled)
      image: <image.repository>:<tag>        # the gateway image
      securityContext: <gateway.containerSecurityContext>   # v2 F3
      resources: <gateway.resources>
      env: [ GATEWAY_DATABASE_URL — SAME resolution block as migrate/gateway ]   # v2 F2: so the wait can read the DSN
      command: [python, -c, "<parse host:port from os.environ[GATEWAY_DATABASE_URL] via urlparse
                 (fallback ai-proxy.postgres.fullname:5432); bounded socket loop over (maxAttempts|int); sys.exit(1) on exhaustion>"]
  - name: migrate                            # only when gateway.migrate.enabled
      image: <image.repository>:<tag>
      securityContext: <gateway.containerSecurityContext>   # v2 F3
      resources: <gateway.resources>
      command: [alembic, upgrade, head]      # cwd /app
      env:
        - name: GATEWAY_DATABASE_URL         # IDENTICAL resolution to the gateway container:
          {{ if databaseUrlSecretRef.name }} valueFrom.secretKeyRef(name,key) {{ else }} value: <gateway.env.databaseUrl literal> {{ end }}
        - name: GATEWAY_ENVIRONMENT          # so alembic env.py sees the same environment
          value: <gateway.env.environment>
# the gateway CONTAINER also gains securityContext: <gateway.containerSecurityContext> (v2 F3) — runAsUser/runAsNonRoot still pod-level.

Rejections -> render/inspection-time failures:
  migration_assets_absent   -> Dockerfile runtime stage lacks `COPY migrations/` or `COPY alembic.ini`
  migrate_dsn_mismatch      -> migrate initContainer GATEWAY_DATABASE_URL != the gateway container's (name/key/literal)
  wait_unbounded            -> the wait-for-db command has no maxAttempts/time bound (or maxAttempts not `| int`-coerced)
  secret_literal_in_chart   -> a DB password / DSN-with-password literal in values or the rendered init env
  migrate_after_boot        -> migration placed anywhere but initContainers (sidecar/post-start/extra container)

Invariants:
  - initContainers render in order [wait-for-db, migrate] BEFORE the single gateway container; k8s guarantees the
    gateway container starts only after both exit 0 → the gateway never serves an unmigrated DB.
  - the migrate env GATEWAY_DATABASE_URL is byte-identical in shape to the gateway container's resolution
    (same secretKeyRef name+key when set; same literal otherwise) — one DSN source of truth.
  - (v2 F2) wait-for-db parses host:port from GATEWAY_DATABASE_URL at RUNTIME (urlparse, fallback to ai-proxy-postgres:5432),
    so it waits on the ACTUAL target — correct for in-cluster, external, AND mixed (secretRef→external) DSNs; renders whenever
    the init block does, so the external-DB path also gets a bounded wait.
  - the wait is BOUNDED (`maxAttempts | int`-coerced — no unquoted interpolation) and uses python (in the image), never nc/pg_isready.
  - (v2 F3) the gateway container + BOTH initContainers carry securityContext gateway.containerSecurityContext
    (allowPrivilegeEscalation:false, drop ALL); runAsUser/runAsNonRoot stay pod-level (task-1 test reads them via fallback → unbroken).
  - gateway.migrate.enabled=false drops ONLY the migrate initContainer; wait-for-db + the gateway container + its
    probes/PDB/resources/terminationGracePeriodSeconds are unchanged.
  - NO secret value added to the chart; the DSN is Secret-sourced via the existing databaseUrlSecretRef; the existing
    validateSecret/validateTLS fail-fast guards are untouched.
  - additive ONLY: no new template file; sibling task-1..4 suites + helm lint stay green.
```

Status: FROZEN @ v2 — approved by Tin (2026-06-27). v2 = gate-driven hardening from the adversarial refute-read (security-expert, NO HIGH; Tin picked all): F1 `maxAttempts | int` (kill the unquoted-interpolation injection surface); F2/F4 wait-for-db parses host:port from the DSN at RUNTIME (urlparse, helper fallback) + renders on the external-DB path too — RESOLVES the v1 lowest-confidence flag (the wait now targets the actual DB, in-cluster/external/mixed); F3 `gateway.containerSecurityContext` (allowPrivilegeEscalation:false, drop ALL) on the gateway container + both initContainers; F5 a values comment on multi-replica alembic safety. v1 (2026-06-27) = gateway initContainers (wait-for-db→migrate) per the mechanism decision; the wait-target flag surfaced + accepted, now closed by v2 F2.
Least-sure flag surfaced at freeze: [spec] the in-image alembic run only PROVES at container start (kind-bootstrap task 6), not at `helm template`/source-parse — if the COPY lands assets at the wrong path or alembic needs a missing write dir, only the live pod reveals it; cost = a red kind-bootstrap, not a silent prod bug. (The v1 [contract] wait-for-db-target flag is now CLOSED by v2-F2: wait-for-db parses the DSN host at runtime.)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: every Must + every Reject ≥1 test. Helm tests shell out to real `helm template`
+ parse rendered YAML (behavior); the image check parses the real Dockerfile.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_image_carries_migrations (M1 / migration_assets_absent): apps/gateway/Dockerfile COPYs migrations/ + alembic.ini to the workdir.
  - test_initcontainers_before_gateway (M2): render → initContainers == [wait-for-db, migrate] BEFORE the gateway container; both use the gateway image; migrate cmd = alembic upgrade head; wait uses python (not nc/pg_isready); gateway container remains.
  - test_migrate_dsn_matches_gateway_secretref (M3): with databaseUrlSecretRef.name set → migrate GATEWAY_DATABASE_URL == gateway's secretKeyRef (same name+key).
  - test_migrate_dsn_matches_gateway_literal (M3): default (no secretRef) → migrate GATEWAY_DATABASE_URL == the gateway's literal value.
  - test_init_design_for_failure (M4): two initContainers; each has resources req+limits; pod runs non-root; gateway terminationGracePeriodSeconds + 3 probes unchanged.
  - test_wait_is_bounded (wait_unbounded): the configured waitForDb.maxAttempts appears in the rendered wait command (no infinite loop).
  - test_migrate_toggle (M5): migrate.enabled=false → no migrate initContainer, wait-for-db stays; default → migrate present.
  - test_no_secret_literal_in_init (secret_literal_in_chart): no secret marker in values; the default migrate DSN is passwordless (no creds literal).
  - test_migrate_is_initcontainer_only (migrate_after_boot): no long-lived container runs alembic (migration is init-only).
  - test_wait_parses_dsn (v2 F2): wait-for-db carries the SAME GATEWAY_DATABASE_URL env as migrate AND its command parses host from that env (urlparse / os.environ); with datastores.postgres.enabled=false + a secretRef set, wait-for-db STILL renders (external path bounded).
  - test_gateway_pod_container_hardened (v2 F3): the gateway container + both initContainers each have securityContext allowPrivilegeEscalation:false + capabilities.drop=[ALL]; pod-level runAsUser/runAsNonRoot unchanged (task-1 suite green).
  - test_chart_valid: default `helm template` 0 + `helm lint` 0 (sibling suites stay green).
</test_plan>

Tests live in: `tests/helm/test_migration_and_secrets.py` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/Dockerfile` (ADD COPY migrations/ + alembic.ini to the runtime stage) · `charts/ai-proxy/values.yaml` (ADD `gateway.migrate{enabled,waitForDb.maxAttempts}` + (v2) `gateway.containerSecurityContext` + the multi-replica comment) · `charts/ai-proxy/templates/gateway-deployment.yaml` (ADD `initContainers: [wait-for-db, migrate]` before the gateway container + (v2) `securityContext` on the gateway container + both initContainers; (v2 F2) wait-for-db parses the DSN at runtime — the gateway container probes/PDB/resources behavior untouched)
Strategy (ordered batches): 1. Dockerfile: COPY migrations/ + alembic.ini. 2. values: add gateway.migrate sub-schema. 3. gateway-deployment.yaml: render initContainers (wait-for-db python socket loop bounded by maxAttempts targeting ai-proxy.postgres.fullname:5432; migrate alembic upgrade head with the SAME GATEWAY_DATABASE_URL block as the gateway container + GATEWAY_ENVIRONMENT). Re-run tests/helm after each batch.
Safety rule (feature-specific): the init path is ADDITIVE — the gateway container, its env resolution, probes, PDB, resources, and terminationGracePeriodSeconds MUST be unchanged; the migrate DSN block is the SAME shape as the gateway container's (one source of truth); no secret literal; the wait is bounded; sibling task-1..4 suites MUST stay green.
Code lives in: `apps/gateway/` + `charts/ai-proxy/`
Constraints: do NOT change any test or the contract; allow-list only (pure Helm/YAML + a Dockerfile COPY — no new dep); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `tests/helm/` 72 passed in 4.61s (task-1..4 suites + 12 task-5 tests incl. v2 test_wait_parses_dsn + test_gateway_pod_container_hardened); `helm lint charts/ai-proxy` → 0 failed (1 INFO icon-recommended).
- [x] coverage did not decrease — Helm tests assert rendered YAML via real `helm template`; no gateway-app source touched except Dockerfile (build-only, no runtime line). No Python module coverage to move.
- [x] no test or contract was altered during build — §3 FROZEN @ v2 since the contract phase; build edited only `Dockerfile`, `values.yaml`, `gateway-deployment.yaml`. The 2 v2 tests were authored in the tests phase before crossing to build.
- [x] the green was EARNED, not gamed — adversarial refute-read (sonnet subagent) at v1 returned NO HIGH findings; its 5 findings (F1 maxAttempts|int, F2/F4 wait-for-db wrong-host in mixed/external mode, F3 container securityContext, F5 multi-replica doc) were ALL addressed in the v2 contract+build. Tests parse the real render (init order, DSN equality, urlparse-in-command), not fixtures — independently re-confirmed by `tmp/v53_t5_render_check.py`.
- [x] concurrency / timing of the risky operation is safe — migrate runs init-only (no sidecar; test_migrate_is_initcontainer_only), ORDERED after a bounded `wait-for-db` (range(maxAttempts), 2s socket timeout + 2s sleep, sys.exit(1) on exhaustion = fail-closed → pod restarts, never an unmigrated boot). alembic `upgrade head` is idempotent across N replicas' init (each waits, applies-or-noops). The gateway container starts ONLY after both inits succeed (initContainer semantics).
- [x] no exposed secrets, injection openings, or unexpected dependencies — test_no_secret_literal_in_init green (default DSN passwordless; no cert/key/password literal in values); DSN sourced via the SAME secretKeyRef as the gateway (parity asserted). `maxAttempts | int` closes the `-c` script interpolation surface (no unquoted string into python). No new chart/runtime dependency — `alembic` already a runtime dep; `python`/`socket`/`urllib` are stdlib in the image.
- [x] layering & dependencies follow CONVENTIONS.md — migrate-before-boot lives in the chart (initContainers), not app code; the image only gains the migration ASSETS it already owns (`migrations/`, `alembic.ini`). MILESTONE decision + exit criterion updated to the gateway-initContainer mechanism (Tin 2026-06-27).
- [x] a person reviewed and approved the change — Tin signed off the verify gate 2026-06-27 (security-adjacent HARD-STOP: secrets handling + container hardening + migration mechanism).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] `helm template` renders the gateway Deployment with `initContainers: [wait-for-db, migrate]` BEFORE the single gateway container; both use the gateway image — CONFIRMED: render check prints `init order: ['wait-for-db', 'migrate']`, migrate cmd `['alembic','upgrade','head']`; test_initcontainers_before_gateway green.
- [x] the `migrate` initContainer runs `alembic upgrade head` and its `GATEWAY_DATABASE_URL` is byte-identical in shape to the gateway container's (secretKeyRef name+key when set; same literal otherwise) — CONFIRMED: render check `DSN parity gw==migrate: True`; test_migrate_dsn_matches_gateway_secretref + _literal green.
- [x] the `wait-for-db` initContainer uses a python socket loop bounded by `gateway.migrate.waitForDb.maxAttempts` (the value appears in the rendered command; no nc/pg_isready, no infinite loop) — CONFIRMED: render `wait reads env+urlparse: True`; test_wait_is_bounded (maxAttempts=7 appears) + test_wait_parses_dsn green.
- [x] `apps/gateway/Dockerfile` runtime stage COPYs `migrations/` + `alembic.ini` to the workdir (alongside `src/`); the uvicorn CMD is unchanged — CONFIRMED: Dockerfile:34-37 COPY migrations/ + alembic.ini; CMD:60 uvicorn unchanged; test_image_carries_migrations green.
- [x] `gateway.migrate.enabled=false` drops ONLY the migrate initContainer (wait-for-db + the gateway container + its probes/PDB/resources/terminationGracePeriodSeconds remain) — CONFIRMED: test_migrate_toggle + test_init_design_for_failure green (probes intact, terminationGracePeriodSeconds present).
- [x] no secret literal anywhere in the init env or values; the default migrate DSN is passwordless; migration runs init-only (no sidecar) — CONFIRMED: test_no_secret_literal_in_init + test_migrate_is_initcontainer_only green.
- [x] full tests/helm suite green incl. the task-1..4 suites + `helm lint` 0 — CONFIRMED: 72 passed; `helm lint` 0 failed.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (chart/image) — every new artifact is reachable: the `migrate`+`wait-for-db` initContainers are emitted under the `or postgres.enabled migrate.enabled` guard and rendered in BOTH in-cluster and external-DB paths (render check: external-DB init order `['wait-for-db','migrate']`); `gateway.migrate.{enabled,waitForDb.maxAttempts}` + `gateway.containerSecurityContext` in values.yaml are consumed by gateway-deployment.yaml; the Dockerfile-COPY'd `migrations/`+`alembic.ini` are what `alembic upgrade head` reads from WORKDIR /app (env.py:89 reads GATEWAY_DATABASE_URL). No orphan.
- [x] DEAD-CODE (chart/image) — no unused value or template branch introduced. `migrate.enabled=false` cleanly drops only the migrate container (test_migrate_toggle); `containerSecurityContext` applies to all 3 containers (render check, all APE=False dropALL=['ALL']). The Dockerfile COPY lines are exercised by the migrate init at runtime.
- [x] SEMANTIC (prose) — read MILESTONE.md MIGRATIONS-BEFORE-BOOT decision (line ~19) + the matching exit criterion (line ~45): both now state the gateway-initContainer mechanism (Tin-decided 2026-06-27), replacing the earlier Helm-hook-Job wording. Confirmed consistent with the rendered chart.

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin · date: 2026-06-27

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
