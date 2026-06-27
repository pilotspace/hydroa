# TASK: Helm chart scaffold + values-schema contract

slug: helm-chart-scaffold · created: 2026-06-26 · stage: production
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

Touches (files · symbols · signatures): NEW chart, grounded in the real prod topology + gateway workload contract it translates —
  - `infra/docker-compose.prod.yml` — the canonical prod topology this chart re-expresses (gateway service: `image:${GATEWAY_IMAGE}`, env GATEWAY_DATABASE_URL/REDIS_URL/JWT_SECRET/ENVIRONMENT=production, `stop_grace_period:15s`, healthcheck GET `http://localhost:8000/health`, `depends_on` postgres+redis healthy). Header documents the "no dev defaults — fail-fast if unset" secret posture.
  - `apps/gateway/Dockerfile` — the gateway image contract: non-root `gateway` uid 1000, `EXPOSE 8000`, CMD `uvicorn gateway.main:create_app --factory --host 0.0.0.0 --port 8000`, `ENV GATEWAY_ENVIRONMENT=production`. (Deployment podSpec must match: runAsUser 1000, containerPort 8000.)
  - `apps/gateway/src/gateway/core/config.py:Settings` — `SettingsConfigDict(env_prefix="GATEWAY_")`. REQUIRED-in-prod: `database_url` · `redis_url` · `jwt_secret` (validator `_forbid_dev_secret_outside_dev` → must be set when environment≠dev/test) · `environment`. Operational groups the e2e/overlay flips: object-store (`object_store_enabled/endpoint/bucket/region/access_key_id/secret_access_key`), realtime relay (`realtime_relay_provider/_openai_model/_gemini_model`), upstream base-urls (`openrouter_base_url/openai_base_url/...` → point at in-cluster stub), `shutdown_drain_timeout_seconds`. ~70 more opt-in knobs with safe defaults → reachable via an `extraEnv`/secret escape hatch, NOT individually templated.
  - `Makefile:migrate` — `cd apps/gateway && uv run alembic upgrade head` (+ `migrate-check` = `alembic check`). The shape the migration-Job (sibling task) runs; scaffold's values schema must expose the image+DB-URL it needs.
Context (working folder): `infra/docker-compose.prod.yml` header (required-env list + fail-fast posture) · `.add/milestones/v53/MILESTONE.md` Shared decisions (HELM VALUES = THE CONTRACT, in-cluster-now/external-ready, migrations-before-boot, secrets-never-in-chart, design-for-failure-in-cluster) · `apps/gateway/alembic.ini` (migration root). NO Helm/k8s assets exist yet — this is the first chart.
Honors (patterns / conventions): PROJECT.md invariant "No outbound IO without timeout + bounded retry + circuit breaker on OpenRouter" (the workload is resilient; the chart must not undermine it) · CLAUDE.md "design for failure" → liveness/readiness/startup probes + resource requests/limits + PDB on the gateway Deployment · prod-compose "no dev defaults — fail-fast if unset" → every secret is a required values input surfaced as a k8s Secret, never a chart default · gateway runs non-root uid 1000 (podSecurityContext must agree).
Anchors the contract cites: the NEW chart files §3 freezes — `charts/ai-proxy/Chart.yaml` · `charts/ai-proxy/values.yaml` (the FROZEN schema: `image{repository,tag,pullPolicy}` · `gateway{replicas,resources,env{databaseUrl,redisUrl,environment,drainTimeoutSeconds},secrets{jwtSecretRef,...},extraEnv,objectStore{...},realtimeRelay{...},upstreamBaseUrls{...}}` · placeholder blocks `datastores{}`/`envoy{}`/`dashboard{}` owned by sibling tasks) · `charts/ai-proxy/values-prod.yaml` (overlay) · `charts/ai-proxy/templates/_helpers.tpl` (fullname/labels/in-cluster Service DNS) · `charts/ai-proxy/templates/gateway-deployment.yaml` · `charts/ai-proxy/templates/gateway-service.yaml`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Helm chart scaffold + frozen values-schema contract — `charts/ai-proxy/` skeleton, the gateway Deployment+Service, and the `values.yaml` schema every sibling template (datastores · envoy · dashboard · migration) consumes.
Framings weighed: single umbrella chart with typed placeholder blocks for sibling components (chosen) · subchart-per-component umbrella (deferred — fragments the one values contract; revisit only if a component needs independent release) · raw-manifests+kustomize (rejected at milestone level — Helm chosen)
Must:
<must>
  - M1 — `helm lint charts/ai-proxy` exits 0 and `helm template charts/ai-proxy` renders without error from `values.yaml` defaults alone.
  - M2 — every environment-specific gateway input is sourced from `.Values` — the rendered gateway Deployment carries NO hardcoded image, env value, host, or secret literal (image = `{{.Values.image.repository}}:{{.Values.image.tag}}`; env from `.Values.gateway.*`).
  - M3 — the gateway Deployment matches the image contract: containerPort 8000 · `securityContext.runAsNonRoot:true`+`runAsUser:1000` · no command override (the image's `--factory` uvicorn CMD stands) · GATEWAY_ENVIRONMENT from values (default "production").
  - M4 — design-for-failure on the gateway Deployment: liveness + readiness + startup probes hit `GET /health` :8000 · resource requests AND limits from values · a PodDisruptionBudget rendered · `terminationGracePeriodSeconds` ≥ `gateway.env.drainTimeoutSeconds`.
  - M5 — secrets are Secret-sourced: GATEWAY_JWT_SECRET (+ the secret-class long-tail) injected via `secretKeyRef`; the chart ships NO secret value; default = reference an existing Secret, opt-in `createSecret` from values.
  - M6 — the values schema is COMPLETE + external-ready: `gateway.env.databaseUrl`/`redisUrl` are single values-driven connection strings (in-cluster OR managed, no template change) · `gateway.extraEnv`/`extraSecretEnv` escape hatch reaches the ~70 long-tail GATEWAY_* knobs without per-knob templating · typed placeholder blocks `datastores{}`·`envoy{}`·`dashboard{}` exist so the schema is the single contract.
  - M7 — `values-prod.yaml` overlay, when layered (`-f values.yaml -f values-prod.yaml`), swaps image tag + edge host + secret ref + datastore endpoints with NO template edit.
  - M8 — `_helpers.tpl` defines fullname/labels/selectorLabels + an in-cluster Service-DNS helper that sibling templates (envoy→gateway, gateway→datastores, dashboard→gateway) consume for stable names.
</must>
Reject:
<reject>
  - a template that inlines a secret literal (jwt/object-store/encryption key value) -> "secret_literal_forbidden"
  - `helm lint` failing or `helm template` erroring on default values -> "chart_invalid"
  - the gateway Deployment missing any of {liveness, readiness, startup} probe, resource limits, or the PDB -> "resilience_incomplete"
  - an env/image/host rendered as a literal instead of from `.Values` -> "hardcoded_env_value"
  - rendering with environment=production but no jwtSecret ref/createSecret configured -> "secret_ref_missing" (mirrors the gateway's own `_forbid_dev_secret_outside_dev`)
</reject>
After:
<after>
  - `helm lint charts/ai-proxy` exits 0; `helm template` renders a gateway Deployment+Service from values alone; `values.yaml` is FROZEN as the contract; sibling tasks fill `datastores{}`/`envoy{}`/`dashboard{}` without changing the schema shape.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [test/tooling] The chart TDD harness is a **pytest suite that shells out to `helm template`/`helm lint` and asserts on parsed YAML** — lowest confidence because `helm` AND `kind` are NOT installed on this box (verified: only kubectl+docker+pyyaml present), so the red→green cannot run first-hand until `helm` is installed; if wrong (you'd rather not add the tool, or prefer the `helm-unittest` plugin / pure golden-file diffs): the §4 runner is reworked (assertions transfer; only the renderer changes). RESOLUTION OPTIONS in the freeze DECISION below.
  ⚠ [contract] One umbrella chart with typed placeholder blocks for siblings (not subcharts) — lowest confidence because a later component (dashboard/envoy) might warrant its own subchart; if wrong: refactor placeholder blocks into subchart values (additive key move).
  - [ ] secret strategy defaults to "reference an existing Secret" with opt-in `createSecret` from values — confirm vs always-create (1-line default flip).
  - [ ] baseline defaults: gateway `replicas:2`, PDB `minAvailable:1`, modest CPU/mem requests+limits — confirm the starting numbers (trivially tunable, not frozen behavior).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: chart lints and renders from defaults            # M1
  Given the charts/ai-proxy chart
  When I run `helm lint` and `helm template` with default values.yaml
  Then lint exits 0 and template emits valid YAML for a gateway Deployment + Service

Scenario: gateway inputs come only from values             # M2 / R4 hardcoded_env_value
  When I render the chart
  Then the gateway container image is "{repo}:{tag}" from .Values.image and every gateway env value traces to a .Values.gateway.* key
  And no image tag, host, or env value appears as a template literal

Scenario: gateway pod matches the image contract            # M3
  When I render the gateway Deployment
  Then it sets containerPort 8000, runAsNonRoot true + runAsUser 1000, no command override, and GATEWAY_ENVIRONMENT from values

Scenario: design-for-failure is complete                    # M4 / R3 resilience_incomplete
  When I render the gateway Deployment + PDB
  Then liveness, readiness, and startup probes target GET /health :8000, resource requests AND limits are set, a PodDisruptionBudget renders, and terminationGracePeriodSeconds >= gateway.env.drainTimeoutSeconds

Scenario: secrets are reference-only                        # M5 / R1 secret_literal_forbidden
  When I render the chart
  Then GATEWAY_JWT_SECRET (and secret-class env) come from a secretKeyRef and no Secret value is templated inline
  And the chart contains no plaintext secret default

Scenario: values schema is complete and external-ready      # M6
  When I inspect values.yaml
  Then databaseUrl and redisUrl are single connection-string values, an extraEnv/extraSecretEnv escape hatch exists, and typed datastores/envoy/dashboard placeholder blocks are present

Scenario: prod overlay swaps env without template edits     # M7
  Given values.yaml and values-prod.yaml
  When I render with both layered
  Then the image tag, edge host, secret ref, and datastore endpoints change and no template file is modified

Scenario: helpers expose stable names                       # M8
  When a sibling template calls the fullname / labels / in-cluster-DNS helpers
  Then it receives a deterministic name + selector labels + the gateway in-cluster Service DNS

Scenario: a broken chart fails its gate                     # R2 chart_invalid
  Given a chart with an invalid template or missing required value
  When I run `helm lint` / `helm template`
  Then it exits non-zero (chart_invalid)
  And a valid default render is unaffected (the failure is isolated to the broken input)

Scenario: production without a jwt secret ref is rejected   # R5 secret_ref_missing
  Given environment=production and neither jwtSecret.existingSecret nor createSecret set
  When I render the chart
  Then the render fails fast with secret_ref_missing
  And rendering with a configured jwtSecret ref succeeds unchanged
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

INTERFACE = the Helm chart `charts/ai-proxy/` + its FROZEN `values.yaml` schema (the contract every sibling template consumes). "Inputs" = values keys; "outputs" = rendered k8s objects.

```
charts/ai-proxy/
  Chart.yaml            apiVersion v2 · name "ai-proxy" · type application · version 0.1.0 · appVersion "0.4.0"
  values.yaml           # FROZEN SCHEMA (keys + types + defaults):
    image:        { repository: str, tag: str, pullPolicy: "IfNotPresent" }
    gateway:
      replicas:   int = 2
      resources:  { requests:{cpu,memory}, limits:{cpu,memory} }   # all four required
      env:
        environment:        str = "production"
        databaseUrl:        str            # GATEWAY_DATABASE_URL — single conn string (in-cluster|managed)
        redisUrl:           str            # GATEWAY_REDIS_URL    — single conn string (in-cluster|managed)
        drainTimeoutSeconds: int = 10      # GATEWAY_SHUTDOWN_DRAIN_TIMEOUT_SECONDS
      jwtSecret:  { existingSecret: str="", existingKey: str="jwt-secret", createSecret: bool=false, value: str="" }
      objectStore: { enabled: bool=false, endpoint, bucket, region, accessKeyId, secretRef:{name,key} }  # GATEWAY_OBJECT_STORE_*
      realtimeRelay: { provider: str="", openaiModel, geminiModel }   # GATEWAY_REALTIME_RELAY_*
      upstreamBaseUrls: { openrouter, openai, anthropic, google }     # GATEWAY_*_BASE_URL (stub override in e2e)
      extraEnv:       [ {name,value} ]      # long-tail GATEWAY_* plain knobs (escape hatch)
      extraSecretEnv: [ {name,secretRef:{name,key}} ]   # long-tail secret-class knobs
      probes:     { liveness, readiness, startup }  # tunable timings; all three always rendered
      pdb:        { minAvailable: 1 }
      podSecurityContext: { runAsNonRoot: true, runAsUser: 1000 }
    datastores: {}     # PLACEHOLDER — owned by datastore-statefulsets (typed in its task)
    envoy:      {}     # PLACEHOLDER — owned by envoy-edge-manifests
    dashboard:  {}     # PLACEHOLDER — owned by dashboard-chart
  values-prod.yaml      # overlay: image.tag, envoy.host, gateway.jwtSecret.existingSecret, gateway.env.databaseUrl/redisUrl, datastores endpoints
  templates/
    _helpers.tpl                 # define "ai-proxy.fullname" · ".labels" · ".selectorLabels" · ".gateway.serviceDNS"
    gateway-deployment.yaml      # renders all of M2/M3/M4
    gateway-service.yaml         # ClusterIP :8000 (selectorLabels)
    gateway-pdb.yaml             # PodDisruptionBudget (gateway.pdb.minAvailable)
    gateway-secret.yaml          # rendered ONLY when gateway.jwtSecret.createSecret=true

RENDER OUTCOMES (the "responses" for every §1 Reject):
  secret_literal_forbidden  -> contract-test FAILS if any Secret data value is templated from a non-ref literal
  chart_invalid             -> `helm lint`/`helm template` non-zero exit
  resilience_incomplete     -> contract-test FAILS if any of {liveness,readiness,startup,limits,PDB} absent
  hardcoded_env_value       -> contract-test FAILS if image/host/env rendered as a literal (not from .Values)
  secret_ref_missing        -> `fail` in template when env=production AND no jwtSecret.existingSecret/createSecret
Schema (k8s objects rendered by THIS task): Deployment/ai-proxy-gateway · Service/ai-proxy-gateway · PodDisruptionBudget/ai-proxy-gateway · (optional) Secret/ai-proxy-gateway-jwt. Sibling objects (Postgres/Redis/MinIO StatefulSets, Envoy, dashboard, migration Job) are rendered by their own tasks against the placeholder blocks.
```

Least-sure flag surfaced at freeze:
  ⚠ [test] the chart TDD shells out to `helm template`/`helm lint` (helm+kind were missing) — because a chart can only be truly validated by rendering it; if wrong (prefer helm-unittest/golden files): the §4 runner is reworked, assertions transfer. RESOLVED at freeze → helm v4.2.2 + kind v0.32.0 installed locally; red suite ran 10/10 red for the right reason.
  ⚠ [contract] one umbrella chart with typed placeholder blocks (datastores/envoy/dashboard), not subcharts — because the milestone wants ONE values contract; if wrong (a component needs independent release): refactor placeholder block → subchart values (additive key move).

Status: FROZEN @ v1 — approved by Tin 2026-06-26 (freeze decision: values.yaml schema + gateway Deployment/Service/PDB shape + 10 scenarios; ⚠ helm/kind-missing resolved → install locally; ⚠ umbrella-not-subcharts accepted; secret default=existing-ref, replicas:2/pdb.minAvailable:1 accepted)
DEVIATION (recorded, strictly-more-correct): the frozen §3 sketched `jwtSecret.existingSecret: str=""`; the build defaults it to a non-empty conventional name ("ai-proxy-gateway-secrets") so the default render is valid (M1) while R5 secret_ref_missing still fires when the operator explicitly blanks it. Per PROJECT.md "fix-if-strictly-more-correct, record the deviation".
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: not line-coverage (templates) → EVERY §2 scenario carries an assertion (10 scenarios → 10 tests). Harness: pytest shelling out to `helm template`/`helm lint`, parsing rendered YAML with pyyaml (already installed).
Plan (one test per scenario, asserting rendered behavior not template internals):
<test_plan>
  - test_lint_and_render_defaults: act `helm lint` + `helm template` / assert exit 0 + a gateway Deployment & Service object parse out
  - test_gateway_inputs_from_values: render with a sentinel image tag/db-url / assert they appear in output AND grep templates for no literal image:/host
  - test_gateway_pod_matches_image_contract: assert containerPort 8000 + runAsUser 1000 + runAsNonRoot + no `command:` + env GATEWAY_ENVIRONMENT from values
  - test_design_for_failure_complete: assert liveness+readiness+startup probes on /health:8000, resources.requests+limits all set, a PDB object, grace >= drainTimeoutSeconds
  - test_secrets_reference_only: assert GATEWAY_JWT_SECRET via valueFrom.secretKeyRef + no inline Secret data value
  - test_values_schema_complete: load values.yaml / assert databaseUrl+redisUrl scalars, extraEnv+extraSecretEnv keys, datastores/envoy/dashboard placeholder keys
  - test_prod_overlay_swaps_without_template_edit: render `-f values.yaml -f values-prod.yaml` / assert image tag+host+secretRef+datastore endpoints changed (templates untouched)
  - test_helpers_emit_stable_names: render a probe that calls the helpers / assert deterministic fullname+selectorLabels+gateway service DNS
  - test_chart_invalid_fails: render a deliberately broken value / assert non-zero exit; default render still 0
  - test_production_requires_secret_ref: render env=production w/o jwtSecret ref / assert fail "secret_ref_missing"; with ref → 0
</test_plan>

Tests live in: `tests/helm/` · MUST run red (chart absent) before Build.  ⚠ requires `helm` on PATH — see the freeze DECISION (tooling).
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `charts/ai-proxy/` `tests/helm/`
Strategy (ordered batches): 1. Chart.yaml + values.yaml (the frozen schema) + `_helpers.tpl`  2. gateway-deployment/service/pdb/secret templates (M2/M3/M4/M5)  3. values-prod.yaml overlay (M7)  4. the 10-test pytest harness (green)
Safety rule (feature-specific): NO secret value in any template or values default; secret-class env ONLY via `secretKeyRef`; placeholder blocks `datastores`/`envoy`/`dashboard` stay `{}` (sibling-owned) — this task never fills them.
Code lives in: `charts/ai-proxy/`  (tests in `tests/helm/`)
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 16/16 green (`uv run pytest tests/helm`) + `helm lint` exit 0
- [x] coverage did not decrease — every §2 scenario asserted; +6 tests added after the refute-read (secret-from-Secret, no-plaintext-creds, extraEnv loop, serviceDNS, non-dev-env guard, whitespace guard)
- [x] no test or contract was altered during build — test strengthenings were made in the TESTS phase (stepped back, re-crossed); the §3 frozen block is untouched (the 2 secret-ref keys are an ADDITIVE, recorded deviation)
- [x] the green was EARNED, not gamed — adversarial refute-read (security-expert subagent) returned BLOCK with 9 findings; ALL addressed: F1/F2 (HIGH, security) FIXED + independently re-verified via raw `helm template` repros; F3/F5/F7/F8/F9 (overfit) closed by stronger tests; F4/F6 fixed
- [x] concurrency / timing — N/A: a static Helm chart has no runtime concurrency in THIS task (the workload's own resilience is unchanged; design-for-failure surfaced via probes/PDB/grace)
- [x] no exposed secrets, injection openings, or unexpected dependencies — grep found NO secret literal; default DSNs are password-free; GATEWAY_JWT_SECRET + secret-class env are secretKeyRef-only; no new Python dep (pyyaml already present)
- [x] layering & dependencies follow CONVENTIONS.md — chart self-contained; sibling placeholder blocks stay `{}`; helpers are the shared seam
- [x] a person reviewed and approved the change — **Tin signed off 2026-06-26** after a guided walkthrough of the security (secret guard, no-plaintext posture) + non-functional (probes/PDB/grace, naming, overlay) mechanics

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [ ] `helm lint charts/ai-proxy` exits 0 and `helm template` renders a gateway Deployment + Service from defaults — confirmed by `helm lint` + test_lint_and_render_defaults
- [ ] gateway image + env trace to `.Values` (sentinel values appear when set, absent by default) — confirmed by test_gateway_inputs_from_values
- [ ] gateway pod: containerPort 8000, runAsUser 1000 + runAsNonRoot, no command override, GATEWAY_ENVIRONMENT=production — confirmed by test_gateway_pod_matches_image_contract
- [ ] design-for-failure: liveness+readiness+startup probes on /health:8000, resources requests+limits, a PDB, grace ≥ drain — confirmed by test_design_for_failure_complete
- [ ] GATEWAY_JWT_SECRET via secretKeyRef + no populated Secret rendered by default — confirmed by test_secrets_reference_only
- [ ] values.yaml: databaseUrl/redisUrl scalars + extraEnv/extraSecretEnv + datastores/envoy/dashboard placeholders — confirmed by test_values_schema_complete
- [ ] prod overlay swaps image tag + db url with NO template edit — confirmed by test_prod_overlay_swaps_without_template_edit
- [ ] helpers yield `ai-proxy-gateway` names + selector ⊆ pod labels — confirmed by test_helpers_emit_stable_names
- [ ] a broken value → non-zero render; default render still 0 — confirmed by test_chart_invalid_fails
- [ ] production without a jwt secret ref → fail "secret_ref_missing"; with ref → 0 — confirmed by test_production_requires_secret_ref

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every helper is referenced: fullname/labels/selectorLabels by deployment+service+pdb; jwtSecretName by deployment+secret; validateSecret by deployment; serviceDNS by the Service annotation (now consumed + tested). Every values key is read by a template (verified via render).
- [x] DEAD-CODE — no orphaned template/helper. serviceDNS (M8 contract deliverable) was defined-ahead-of-consumer; now surfaced as a Service annotation so it is rendered + asserted (no dead symbol remains).
- [x] SEMANTIC (chart YAML, read in full) — read every template: no secret literal; the secret guard mirrors the app validator's {dev,test} exemption; probes/resources/PDB/grace all values-driven; placeholder blocks untouched. `helm lint` + 16 tests + raw repros confirm.

### GATE RECORD
Outcome: PASS — 16/16 green, helm lint 0; 2 HIGH security findings FIXED + independently re-verified (not risk-accepted); all overfit findings closed by stronger tests.
If RISK-ACCEPTED -> owner: n/a · ticket: n/a · expires: n/a   (no risk accepted; security findings fixed, not waived)
Reviewed by: refute-read = security-expert subagent (BLOCK→all findings resolved) · human = Tin (signed off) · date: 2026-06-26

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): `helm lint` exit code in CI · the 16-test render suite green · (deploy-time) gateway pods reach Ready behind the Service.

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · seeded] ADDITIVE schema extension during build (refute-read F1): `gateway.env.databaseUrlSecretRef{name,key}` + `redisUrlSecretRef{name,key}` source the DSN from a Secret when `name` set; default DSNs are now password-free (evidence: test_no_plaintext_credentials_in_default_values + test_connection_string_can_come_from_secret).
- [SPEC · seeded] secret_ref_missing guard broadened to fire for ANY env outside {dev,test} (refute-read F2), mirroring `_forbid_dev_secret_outside_dev`; `trim` rejects whitespace refs (F6) (evidence: test_secret_guard_fires_for_any_non_dev_env + test_whitespace_secret_ref_is_rejected).
- [SPEC · open] sibling tasks fill the `datastores{}`/`envoy{}`/`dashboard{}` placeholder blocks + consume the `serviceDNS` helper (this task surfaced it as a Service annotation; envoy/dashboard will dial it).
- [SPEC · open] the ~70 long-tail GATEWAY_* knobs ride the `extraEnv`/`extraSecretEnv` escape hatch; if a knob graduates to first-class, add it to the typed schema (evidence: schema completeness decision at freeze).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [TDD · folded] chart TDD works by shelling out to real `helm template`/`helm lint` and asserting on PARSED rendered YAML (not template text) — the only way the green proves rendering; pyyaml + subprocess, no new dep (evidence: 16 tests, red-for-right-reason when chart absent). [folded foundation-version 39]
- [SDD · folded] a Helm chart guard that claims to mirror an app-side validator MUST mirror its exact predicate — exact-string `=="production"` silently under-guarded vs the app's `not in {dev,test}` (evidence: refute-read F2; fixed + test_secret_guard_fires_for_any_non_dev_env). [folded foundation-version 39]
- [ADD · folded] post-freeze deviation records belong in §7 OBSERVE, NOT appended into the frozen §3 region — editing §3 after the tests→build snapshot trips `contract_tampered` (evidence: this loop's tripwire on attempt 1; reverted §3, recorded here). [folded foundation-version 39]
