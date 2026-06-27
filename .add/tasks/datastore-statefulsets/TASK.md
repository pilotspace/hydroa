# TASK: In-cluster Postgres/Redis/MinIO StatefulSets (external-ready)

slug: datastore-statefulsets · created: 2026-06-26 · stage: production
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

Touches (files · symbols · signatures): NEW datastore templates extending the FROZEN chart; grounded in the real datastore configs they translate —
  - `infra/docker-compose.dev.yml` — the real in-cluster datastore configs: postgres `postgres:16-alpine` (POSTGRES_USER/PASSWORD/DB=gateway), redis `redis:7-alpine`, minio `minio/minio:latest` (`command: server /data --console-address ":9001"`, MINIO_ROOT_USER/PASSWORD, :9000 API + :9001 console) + a `minio/mc` one-shot `minio-createbucket` that loops until MinIO answers then makes the artifacts bucket.
  - `infra/docker-compose.prod.yml` — prod posture for PG/Redis: same images, internal-only (no host port), healthchecks `pg_isready -U $USER -d $DB` and `redis-cli ping`, `restart: unless-stopped`. (MinIO is not in prod compose — v51 added it dev-only; this task brings it in-cluster.)
  - `charts/ai-proxy/values.yaml` — the FROZEN `datastores: {}` placeholder THIS task fills (sibling-owned). Gateway conn-string defaults PIN the Service names: `databaseUrl=…@ai-proxy-postgres:5432/gateway`, `redisUrl=redis://ai-proxy-redis:6379/0`, objectStore.endpoint→`ai-proxy-minio:9000`. The datastore Services MUST be named `ai-proxy-postgres`/`ai-proxy-redis`/`ai-proxy-minio` (= `<release>-<name>` via the helper).
  - `charts/ai-proxy/templates/_helpers.tpl` — REUSE `ai-proxy.fullname`/`.labels`; add per-datastore name + selectorLabels following the gateway pattern (do NOT edit frozen helpers).
  - `apps/gateway/src/gateway/core/config.py:Settings` — the consumers the datastores must satisfy: `database_url`/`redis_url` hosts + object-store (`object_store_endpoint/bucket/access_key_id/secret_access_key`) — the MinIO root creds + bucket must match what the gateway expects (gateway objectStore.secretRef → same Secret the MinIO StatefulSet uses).
Context (working folder): `infra/docker-compose.dev.yml` minio-createbucket one-shot (→ becomes a k8s bucket-create Job/init) · `.add/milestones/v53/MILESTONE.md` Shared decisions (IN-CLUSTER-NOW/EXTERNAL-READY, SECRETS-NEVER-IN-CHART, DESIGN-FOR-FAILURE-IN-CLUSTER) · v51 object-store-port (the MinIO consumer). NO datastore manifests exist yet.
Honors (patterns / conventions): EXTERNAL-READY → each datastore is `datastores.<x>.enabled` (default true for e2e); disabling it + pointing the gateway conn-string at a managed endpoint needs ONLY values (StatefulSet simply doesn't render). SECRETS-NEVER-IN-CHART → PG + MinIO credentials via a k8s Secret (createSecret for kind, existingSecret for prod), never a values default. DESIGN-FOR-FAILURE → StatefulSet (stable identity) + PVC (durability) + readiness/liveness probes (pg_isready · redis-cli ping · MinIO `/minio/health/ready`). FROZEN-SCHEMA → extend `datastores{}` ONLY; never touch the frozen gateway/image/etc. keys.
Anchors the contract cites: NEW templates `charts/ai-proxy/templates/postgres-statefulset.yaml` + `postgres-service.yaml` (headless, :5432) · `redis-statefulset.yaml` + `redis-service.yaml` (:6379) · `minio-statefulset.yaml` + `minio-service.yaml` (:9000/:9001) · `minio-createbucket-job.yaml` · the EXTENDED `datastores{}` values sub-schema (`postgres`/`redis`/`minio` each `{enabled, image, storage, resources, probes}` + credential Secret refs).

DECIDED (Tin, pre-specify 2026-06-26): PG auth = **password via Secret (prod-like)** — Postgres reads POSTGRES_PASSWORD from a k8s Secret; the gateway sources its FULL DSN from the same Secret via the task-1 `databaseUrlSecretRef` path (NOT trust-auth). Redis stays no-auth in-cluster (redisUrlSecretRef available for managed). MinIO root creds via Secret; gateway objectStore.secretRef → same Secret; bucket created by a post-install `mc` Job (mirrors the proven dev-compose one-shot).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: In-cluster Postgres/Redis/MinIO StatefulSets (external-ready) — fills the frozen `datastores{}` block with three persistent datastores + a MinIO bucket-create Job + a datastore-credential Secret, each toggle-gated.
Framings weighed: three per-datastore StatefulSets in the umbrella chart, each `enabled`-gated (chosen) · one generic templated datastore loop (rejected — PG/Redis/MinIO differ too much; a loop obscures more than it saves) · Bitnami subchart dependencies (rejected — external dep + fragments the one values contract; milestone chose umbrella)
Must:
<must>
  - M1 — `datastores.postgres.enabled` (default true) renders a Postgres StatefulSet (`postgres:16-alpine`) + a headless Service `ai-proxy-postgres:5432` + a PVC (volumeClaimTemplates); POSTGRES_USER/DB from values, POSTGRES_PASSWORD from a Secret; readiness+liveness via `pg_isready`.
  - M2 — `datastores.redis.enabled` (default true) renders a Redis StatefulSet (`redis:7-alpine`) + Service `ai-proxy-redis:6379` + a PVC; readiness+liveness via `redis-cli ping`. No-auth in-cluster (gateway redisUrl is password-free).
  - M3 — `datastores.minio.enabled` (default true) renders a MinIO StatefulSet (`minio/minio`, `server /data --console-address :9001`) + Service `ai-proxy-minio` (:9000 API, :9001 console) + a PVC; root creds from a Secret; readiness via `GET /minio/health/ready`.
  - M4 — a MinIO bucket-create Job (post-install/upgrade Helm hook, `minio/mc`) creates the artifacts bucket IDEMPOTENTLY (re-run on an existing bucket is a no-op success), gated on `datastores.minio.enabled`.
  - M5 — credentials via a k8s Secret: `datastores.secrets.create=true` (e2e/kind sets it; DEFAULT is false — secure-by-default, mirrors the gateway `jwtSecret.createSecret`) renders one `ai-proxy-datastore-secrets` Secret carrying the PG password, the assembled gateway DSN (`url` key), and the MinIO root user/password; default `create=false` REFERENCES an operator-provided Secret named `datastores.secrets.name` (non-empty default → default render succeeds, mirroring the gateway). NO credential value in a values default; a bare `helm install` ships no Secret material.
  - M6 — external-ready: any `datastores.<x>.enabled=false` renders NOTHING for that datastore (operator points the gateway conn-string at a managed endpoint via values alone); the FROZEN gateway/scaffold templates + values keys are untouched.
  - M7 — every datastore is values-driven (image, storage size, storageClass, resources, probe timings) with NO hardcoded literal; Service names exactly match the scaffold's pinned `ai-proxy-{postgres,redis,minio}`.
</must>
Reject:
<reject>
  - a credential value inlined in a template or a values default -> "secret_literal_forbidden"
  - `helm lint` / `helm template` non-zero on the extended chart (default values) -> "chart_invalid"
  - a datastore StatefulSet missing its PVC (volumeClaimTemplates) or its readiness probe -> "durability_incomplete"
  - the MinIO bucket Job failing when the bucket already exists -> "bucket_create_not_idempotent"
  - a rendered datastore Service name not matching the scaffold-pinned host -> "service_name_mismatch" (would break the gateway's default conn-strings)
</reject>
After:
<after>
  - `helm template` renders 3 StatefulSets + 3 Services + the bucket Job + the datastore Secret from values; any `enabled=false` removes that datastore cleanly; the gateway's pinned conn-strings resolve to the rendered Services; `helm lint` exits 0; the frozen scaffold output is unchanged when `datastores` is left `{}`-equivalent (all disabled).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] PVCs use `volumeClaimTemplates` with NO `storageClassName` by default (relies on the cluster's default StorageClass — kind ships a `standard` rancher.io/local-path provisioner) + small sizes (PG 1Gi · Redis 256Mi · MinIO 2Gi) — lowest confidence because a cluster with no default StorageClass leaves PVCs Pending forever; if wrong: set `datastores.<x>.storageClass` (already in the schema) — a values-only fix, documented for the kind-bootstrap task.
  ⚠ [spec] Redis stays NO-AUTH in-cluster (matches the gateway's password-free redisUrl) — lowest confidence because a security pass may require Redis AUTH; if wrong: add a requirepass Secret + the gateway `redisUrlSecretRef` (the path already exists from task 1) — additive.
  - [ ] ONE combined `ai-proxy-datastore-secrets` Secret (pg-password · url · minio-root-user · minio-root-password) vs per-datastore Secrets — leaning one combined for the createSecret/e2e path; confirm.
  - [ ] MinIO single-node (`replicas:1`, `server /data`) for e2e; distributed/HA MinIO is OUT (managed/R2 for prod) — confirm the e2e-single-node scope.
  - [ ] gateway↔datastore WIRING (set `gateway.env.databaseUrlSecretRef.name` + `objectStore.secretRef.name` to this Secret) is done at INSTALL by kind-bootstrap (task 5), not by editing the frozen gateway values here — this task only PRODUCES the Secret with the right keys.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
# --- one per Must (rendered-object assertions over `helm template`) ---

Scenario: Postgres StatefulSet renders durably (M1)
  Given default values (datastores.postgres.enabled=true)
  When the chart is rendered with `helm template`
  Then a StatefulSet "ai-proxy-postgres" runs image postgres:16-alpine
  And a headless Service "ai-proxy-postgres" exposes port 5432
  And the StatefulSet has a volumeClaimTemplate and a readiness probe running pg_isready
  And POSTGRES_PASSWORD is sourced via secretKeyRef (never a literal)

Scenario: Redis StatefulSet renders durably, no-auth (M2)
  Given default values (datastores.redis.enabled=true)
  When the chart is rendered
  Then a StatefulSet "ai-proxy-redis" runs image redis:7-alpine
  And a Service "ai-proxy-redis" exposes port 6379
  And the StatefulSet has a volumeClaimTemplate and a readiness probe running redis-cli ping
  And no requirepass/AUTH env is set (matches the gateway's password-free redisUrl)

Scenario: MinIO StatefulSet renders durably (M3)
  Given default values (datastores.minio.enabled=true)
  When the chart is rendered
  Then a StatefulSet "ai-proxy-minio" runs image minio/minio with args "server /data --console-address :9001"
  And a Service "ai-proxy-minio" exposes port 9000 (api) and 9001 (console)
  And the StatefulSet has a volumeClaimTemplate and a readiness probe hitting /minio/health/ready
  And MINIO_ROOT_USER and MINIO_ROOT_PASSWORD are sourced via secretKeyRef

Scenario: MinIO bucket-create Job is a post-install hook (M4)
  Given default values (datastores.minio.enabled=true)
  When the chart is rendered
  Then a Job "ai-proxy-minio-createbucket" runs image minio/mc
  And it carries helm.sh/hook: post-install,post-upgrade
  And its command makes the bucket idempotently (mc mb --ignore-existing, or mb;true)
  And the bucket name equals gateway.objectStore.bucket

Scenario: Datastore credentials come from a Secret, not values (M5)
  Given datastores.secrets.create=true with operator-supplied creds (default create=false)
  When the chart is rendered
  Then a Secret "ai-proxy-datastore-secrets" carries pg-password, url, minio-root-user, minio-root-password
  And a bare default render (create=false) ships NO populated datastore Secret
  And every datastore credential env uses secretKeyRef into this Secret

Scenario: External managed datastore — render nothing in-cluster (M6)
  Given datastores.postgres.enabled=false
  When the chart is rendered
  Then no Postgres StatefulSet, Service, or PVC is rendered
  And the gateway Deployment and its conn-string wiring are unchanged
  And the frozen gateway/scaffold values keys are untouched

Scenario: Service names match the scaffold-pinned hosts (M7)
  Given default values
  When the chart is rendered
  Then the datastore Service names are exactly ai-proxy-postgres, ai-proxy-redis, ai-proxy-minio
  And the gateway's default databaseUrl/redisUrl/objectStore.endpoint hosts resolve to those Services

# --- one per Reject ---

Scenario: A credential literal is forbidden (secret_literal_forbidden)
  Given a template or values default containing a plaintext password
  When the chart's default render is scanned
  Then no datastore credential value appears outside a Secret manifest
  And the gateway scaffold's password-free defaults remain password-free

Scenario: An invalid extended chart fails fast (chart_invalid)
  Given a misconfiguration (datastores.secrets.name="" while a datastore is enabled, env=production)
  When the chart is rendered
  Then `helm template` exits non-zero with a clear datastore_secret_ref_missing message
  And a valid default render still exits 0 and lints clean

Scenario: A datastore without a PVC or probe is rejected (durability_incomplete)
  Given a rendered datastore StatefulSet
  When its spec is inspected
  Then it has both a volumeClaimTemplate AND a readiness probe
  And a StatefulSet missing either is treated as a failing render

Scenario: The bucket Job is idempotent (bucket_create_not_idempotent)
  Given the artifacts bucket already exists
  When the bucket-create Job re-runs (post-upgrade)
  Then the mc command treats an existing bucket as success (no non-zero exit)
  And re-running the Job leaves exactly one bucket

Scenario: A mismatched Service name is rejected (service_name_mismatch)
  Given a rendered datastore Service
  When its name is compared to the gateway's pinned conn-string host
  Then they are identical
  And a name that would break the gateway's default DSN fails the render check
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

CONTRACT = the `datastores{}` values sub-schema (input) + the rendered k8s objects (output).
This EXTENDS the frozen `datastores: {}` placeholder; it touches NO other frozen key.

```
# --- INPUT: values.yaml `datastores{}` sub-schema (defaults shown) ---
datastores:
  secrets:
    create: false                # DEFAULT false (secure-by-default; mirrors gateway jwtSecret.createSecret). e2e/kind sets true. true -> render the Secret <name> w/ creds; false -> REFERENCE an operator-provided Secret <name>
    name: "ai-proxy-datastore-secrets"   # Secret name — rendered when create=true, referenced otherwise. Non-empty default => default render references it and SUCCEEDS (like gateway existingSecret="ai-proxy-gateway-secrets")
    postgresPassword: ""         # operator-supplied; EMPTY default (no literal) — only consumed when create=true
    minioRootUser: ""            #   "
    minioRootPassword: ""        #   "
  postgres:
    enabled: true
    image: postgres:16-alpine
    user: gateway                # POSTGRES_USER / db name (mirror dev-compose)
    database: gateway
    storage: 1Gi
    storageClass: ""             # "" -> cluster default StorageClass (kind: standard)
    resources: {}                # values-driven; no hardcoded request/limit
    probes: { ... }              # pg_isready timings, values-driven
  redis:
    enabled: true
    image: redis:7-alpine
    storage: 256Mi
    storageClass: ""
    resources: {}
    probes: { ... }              # redis-cli ping
  minio:
    enabled: true
    image: minio/minio
    mcImage: minio/mc
    apiPort: 9000
    consolePort: 9001
    storage: 2Gi
    storageClass: ""
    resources: {}
    probes: { ... }              # GET /minio/health/ready

# --- OUTPUT: objects `helm template` MUST render (default values) ---
StatefulSet  ai-proxy-postgres   image=postgres:16-alpine  port 5432  volumeClaimTemplate  probe=pg_isready      POSTGRES_PASSWORD<-secretKeyRef
Service      ai-proxy-postgres   headless (clusterIP: None)  port 5432
StatefulSet  ai-proxy-redis      image=redis:7-alpine       port 6379  volumeClaimTemplate  probe=redis-cli ping  (no auth)
Service      ai-proxy-redis      port 6379
StatefulSet  ai-proxy-minio      image=minio/minio  args="server /data --console-address :9001"  ports 9000/9001  volumeClaimTemplate  probe=/minio/health/ready  MINIO_ROOT_*<-secretKeyRef
Service      ai-proxy-minio      ports 9000 (api) + 9001 (console)
Job          ai-proxy-minio-createbucket  image=minio/mc  hook=post-install,post-upgrade  idempotent mb  bucket=gateway.objectStore.bucket
Secret       ai-proxy-datastore-secrets   keys: pg-password · url · minio-root-user · minio-root-password   (only when datastores.secrets.create)

Rejections -> render-time failures (asserted in tests over `helm template` exit + parsed YAML):
  secret_literal_forbidden        -> no credential value outside a Secret manifest in the default render
  chart_invalid                   -> `helm template`/`helm lint` non-zero on a misconfig (e.g. datastores.secrets.name="" while a datastore is enabled & env not dev/test -> datastore_secret_ref_missing; default render still exits 0)
  durability_incomplete           -> a datastore StatefulSet lacking volumeClaimTemplate OR readiness probe
  bucket_create_not_idempotent    -> the mc command is not --ignore-existing / not no-op on an existing bucket
  service_name_mismatch           -> a datastore Service name != the gateway's pinned conn-string host

Invariants:
  - enabled=false for any datastore renders NONE of its objects (StatefulSet/Service/PVC/[Job for minio]).
  - The `url` Secret key holds the full asyncpg DSN the gateway sources via the task-1 databaseUrlSecretRef path.
  - The gateway↔datastore Secret-name wiring is set at INSTALL (task 5 kind-bootstrap), not by editing frozen gateway values here.
  - NO frozen scaffold key is modified; only the `datastores{}` sub-tree is added.
  - Secret shape = ONE combined `ai-proxy-datastore-secrets` (Tin, freeze 2026-06-26), not per-datastore.
  - datastores.secrets.create DEFAULT = false (CR-1) — secure-by-default; a bare `helm install` ships no Secret material; e2e/kind sets create=true + creds.
```

Least-sure flag surfaced at freeze: [contract] PVC `storageClass: ""` relies on a cluster default StorageClass (true on kind; a no-default cluster leaves PVCs Pending) — accepted as a values-only fix documented for kind-bootstrap. Secondary: [spec] Redis no-auth in-cluster — accepted (additive AUTH path exists via `redisUrlSecretRef`).

CR-1 (tests-phase change request, approved by Tin 2026-06-26): `datastores.secrets.create` default flipped true→false. Reason: the v1 default collided with task-1's frozen `test_secrets_reference_only` ("no populated Secret by default") and was inconsistent with the frozen gateway `jwtSecret.createSecret=false` pattern. §1/§2 M5 reworded "default"→"when create=true". No other shape change.

Status: FROZEN @ v2 — approved by Tin (2026-06-26, CR-1 applied)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: every Must + every Reject has ≥1 test; assertions are over rendered `helm template` YAML (behavior), never template text.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_postgres_statefulset_renders_durably (M1): render defaults / assert StatefulSet ai-proxy-postgres image postgres:16-alpine, headless Service :5432, volumeClaimTemplate present, readiness probe execs pg_isready, POSTGRES_PASSWORD via secretKeyRef (no literal).
  - test_redis_statefulset_renders_durably (M2): assert StatefulSet ai-proxy-redis image redis:7-alpine, Service :6379, volumeClaimTemplate, readiness probe execs redis-cli ping, NO requirepass/AUTH env.
  - test_minio_statefulset_renders_durably (M3): assert StatefulSet ai-proxy-minio image minio/minio, args "server /data --console-address :9001", Service ports 9000+9001, volumeClaimTemplate, readiness httpGet /minio/health/ready, MINIO_ROOT_USER+PASSWORD via secretKeyRef.
  - test_minio_bucket_job_is_post_install_hook (M4): assert Job ai-proxy-minio-createbucket image minio/mc, helm.sh/hook post-install,post-upgrade, command makes bucket idempotently, bucket name == gateway.objectStore.bucket.
  - test_datastore_credentials_from_secret (M5): assert Secret ai-proxy-datastore-secrets has keys pg-password/url/minio-root-user/minio-root-password, each NON-empty when create=true; sentinel password flows into both pg-password and the assembled url DSN; default render ships no populated datastore Secret.
  - test_create_true_requires_nonempty_creds (M5/F1 fail-closed): create=true with an empty postgresPassword OR minioRootPassword → helm template non-zero (datastore_secret_value_missing); all creds supplied → exits 0. Prevents a silently-passwordless datastore.
  - test_external_managed_renders_nothing (M6): postgres.enabled=false → no ai-proxy-postgres StatefulSet/Service/PVC; redis+minio+gateway unchanged. Also all-disabled → zero datastore StatefulSets, gateway Deployment still renders.
  - test_service_names_match_gateway_pins (M7): datastore Service names == hosts parsed from the FROZEN gateway databaseUrl/redisUrl/objectStore.endpoint.
  - test_no_credential_literal (secret_literal_forbidden): default render — POSTGRES_PASSWORD/MINIO_ROOT_PASSWORD only ever via secretKeyRef; no `value:` literal; Secret data empty by default.
  - test_chart_invalid_fails (chart_invalid): datastores.secrets.create=false & existingSecret="" → helm template non-zero; default render exits 0 + helm lint clean.
  - test_durability_complete (durability_incomplete): each datastore StatefulSet has volumeClaimTemplates AND a readiness probe.
  - test_bucket_job_idempotent (bucket_create_not_idempotent): the mc command uses --ignore-existing (or mb;||true) — re-run is a no-op success.
  - test_service_name_mismatch_guarded (service_name_mismatch): rendered datastore Service names are byte-identical to the gateway-pinned hosts (a mismatch would break the default DSN).
  - test_datastore_resources_complete (design-for-failure): each datastore container has resources.requests AND limits for both cpu and memory (parity with the gateway; Tin-requested at the verify gate).
</test_plan>

Tests live in: `tests/helm/test_datastore_statefulsets.py` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `charts/ai-proxy/values.yaml` (extend `datastores{}` ONLY) · `charts/ai-proxy/templates/_helpers.tpl` (ADD datastore name/label helpers; do NOT edit frozen ones) · `charts/ai-proxy/templates/postgres-statefulset.yaml` · `postgres-service.yaml` · `redis-statefulset.yaml` · `redis-service.yaml` · `minio-statefulset.yaml` · `minio-service.yaml` · `minio-createbucket-job.yaml` · `datastore-secret.yaml`
Strategy (ordered batches): 1. extend `datastores{}` values sub-schema + add helpers (per-datastore name/selectorLabels + a `datastore.secretName`/guard helper). 2. Postgres SS+Service. 3. Redis SS+Service. 4. MinIO SS+Service. 5. bucket-create Job (post-install hook, idempotent mc). 6. datastore Secret (create-gated) + the `datastore_secret_ref_missing` guard. Re-run the FULL tests/helm suite after each batch.
Safety rule (feature-specific): touch ONLY the `datastores{}` values sub-tree + NEW template files; never modify a frozen scaffold key or the gateway/PDB/Service templates. Credentials are secretKeyRef-only — no literal in any template or values default.
Code lives in: `charts/ai-proxy/`
Constraints: do NOT change any test or the contract; allow-list packages only (no new deps — pure Helm/YAML); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 29/29 in `tests/helm/` (16 frozen task-1 scaffold + 13 datastore), via `uv run pytest -p no:cacheprovider --no-cov`.
- [x] coverage did not decrease — chart TDD is render-assertion based; +13 datastore tests, 0 existing tests touched.
- [x] no test or contract was altered during build — §3 FROZEN @ v2 (CR-1 approved at tests phase, before the build snapshot); test edits were STRENGTHENINGS done in the tests phase + re-snapshotted (heal cycle), never to pass code.
- [x] the green was EARNED — adversarial refute-read (security-expert subagent) ran; it found F1 (HIGH, passwordless datastore), F2 (invalid YAML), F6 (unbounded loop) + coverage gaps. ALL fixed and independently re-rendered. No overfit/vacuous asserts remain.
- [x] concurrency / timing safe — the only timing path is the bucket Job wait loop; now bounded (max=60, exit 1 → backoffLimit), idempotent mc mb --ignore-existing.
- [x] no exposed secrets — POSTGRES_PASSWORD/MINIO_ROOT_* are secretKeyRef-only in BOTH StatefulSets and the Job; default render ships no Secret material; create=true fails closed on empty creds. No new dependency (pure Helm/YAML).
- [x] layering & dependencies — extends only the `datastores{}` sub-tree + new template files; frozen scaffold (gateway/image/helpers) byte-unchanged (refute-read confirmed).
- [ ] a person reviewed and approved the change — ESCALATED to Tin (security gate, HARD-STOP discipline): the refute-read surfaced a HIGH security finding, so this gate does not auto-PASS.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] `helm template` (defaults) renders 3 StatefulSets (ai-proxy-postgres/redis/minio) + 3 Services + 1 minio-createbucket Job — confirmed by `helm template` output (3 StatefulSet / 4 Service / 1 Job / 1 Deployment / 1 PDB).
- [x] Each datastore StatefulSet has a volumeClaimTemplate AND a readiness probe (pg_isready · redis-cli ping · GET /minio/health/ready) — confirmed by parsed YAML in test_durability_complete.
- [x] Each datastore container sets resources.requests AND limits for cpu+memory (design-for-failure parity with the gateway; Tin-requested at gate) — confirmed by test_datastore_resources_complete + render.
- [x] POSTGRES_PASSWORD + MINIO_ROOT_USER/PASSWORD are secretKeyRef-only (no `value:` literal) in BOTH the StatefulSets and the bucket Job; the default render ships NO populated datastore Secret (create=false) — confirmed by grep + test_no_credential_literal + test_datastore_credentials_from_secret.
- [x] FAIL-CLOSED (F1): create=true with an empty postgresPassword/minioRootPassword fails the render (datastore_secret_value_missing) — never a silently-passwordless datastore — confirmed by render + test_create_true_requires_nonempty_creds.
- [x] The bucket Job is bounded (timeout on the wait loop, F6: max=60→exit 1) and its pod labels are valid YAML (no duplicate key, F2) — confirmed by render inspection.
- [x] Service names are byte-identical to the gateway's pinned hosts (ai-proxy-postgres/redis from the frozen DSNs; ai-proxy-minio from the release helper) — confirmed by test_service_name*.
- [x] disabling a datastore (enabled=false) removes that datastore's objects cleanly; all-disabled → zero datastore StatefulSets, gateway untouched — confirmed by test_external_managed_renders_nothing.
- [x] `helm lint` exits 0 and the existing tests/helm task-1 suite stays green (frozen scaffold unchanged) — confirmed by running the FULL tests/helm dir (29/29).
- [x] Misconfig (datastores.secrets.name="" + non-dev env) fails with `datastore_secret_ref_missing`; default render still exits 0 — confirmed by test_chart_invalid_fails.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new helper (postgres/redis/minio fullname+selectorLabels, datastores.secretName, datastores.validateSecret) is referenced by a template; every new values key is consumed; confirmed by `helm template` rendering all objects.
- [x] DEAD-CODE (code) — no orphaned helper/template; `helm lint` clean; all 9 new template files render (or are correctly enabled-gated to empty).
- [x] SEMANTIC (yaml/templates) — read every rendered StatefulSet/Service/Job/Secret in full: probes exec the right commands, ports correct, PVCs present, secretKeyRefs resolve to the datastore Secret, hook + delete-policy correct, fail-closed guards fire.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin · date: 2026-06-26   (security gate — Tin signed off after inspecting the templates, renders, and the refute-read findings + fixes; resources added at Tin's request before sign-off)
Refute-read: security-expert subagent (read-only) — F1 HIGH (passwordless datastore on create=true+empty creds) FIXED via fail-closed guard; F2 MEDIUM (duplicate label key → invalid YAML) FIXED; F6 LOW (unbounded wait loop) FIXED; F4/F5/F8 test gaps CLOSED; F3 (datastore pod runAsNonRoot) → §7 delta (safe seccomp default ships); F7 (dev/test guard exemption) → KEPT for gateway parity. Frozen task-1 contract confirmed respected.

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): bucket-Job failure rate (timeout exits) · PVC Pending count (StorageClass missing) · datastore pod restart loops (probe failures).

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] Datastore pod hardening: runAsNonRoot/runAsUser + fsGroup + drop ALL caps for Redis/MinIO (Postgres needs fsGroup, inits as root) (evidence: refute-read F3 — only a safe seccomp default ships now; production hardening deferred per kind-validated-MVP posture).
- [SPEC · open] kind-bootstrap (task 5) MUST wire gateway.objectStore.bucket == the datastore bucket name, and gateway.env.databaseUrlSecretRef.name/objectStore.secretRef.name == datastores.secrets.name (evidence: refute-read F5 — Job falls back to "ai-proxy-artifacts" when gateway.objectStore.bucket is empty; the gateway↔datastore Secret wiring is install-time).
- [SPEC · open] Redis AUTH for managed/exposed deployments (requirepass Secret + gateway redisUrlSecretRef) (evidence: freeze flag — no-auth is in-cluster-only; additive path already exists).
- [SPEC · seeded] PVC storageClass left "" (cluster default) — set datastores.<x>.storageClass on clusters with no default (evidence: freeze ⚠; values-only, documented for kind-bootstrap).
- [SPEC · dropped] Removing the dev/test exemption from the datastore secret-name guard (refute-read F7) — KEPT for deliberate parity with the frozen gateway validateSecret; name defaults non-empty so an empty ref needs an explicit blank.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [TDD · folded] A passing render-test that only asserts a key's PRESENCE (not its non-empty VALUE) waved through a passwordless-datastore defect (evidence: refute-read F1 — create=true+empty-creds; closed by a fail-closed guard + value-non-empty assertions). [folded foundation-version 39]
- [SDD · folded] A frozen default can collide with a SIBLING task's frozen invariant — task-2's create=true default broke task-1's "no populated Secret by default"; caught at tests phase, fixed via CR-1 (evidence: the secure-by-default flip mirroring gateway jwtSecret). [folded foundation-version 39]
- [ADD · folded] An adversarial refute-read at verify earned its keep: 1 HIGH security defect + 1 invalid-YAML correctness bug + a design-for-failure timeout gap, none caught by green tests (evidence: F1/F2/F6 → heal cycle tests→build→verify). [folded foundation-version 39]
