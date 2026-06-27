# TASK: Automated e2e through the Envoy edge on live kind: signup → login → proxied completion (stub upstream) → accurate usage+cost row

slug: e2e-core-flow · created: 2026-06-27 · stage: production
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

Touches (files · symbols · signatures): a NET-NEW automated e2e that drives the GOAL FLOW through the LIVE kind Envoy edge (task 6) and asserts an accurate usage+cost row. Grounded in the existing compose-edge e2e it adapts + the real auth/proxy/usage surfaces —
  - **NEW kind e2e harness + test** — analog `scripts/e2e_edge.sh` brings up the COMPOSE edge stack (`docker compose -f infra/docker-compose.e2e.yml up --wait`) then runs `apps/gateway/tests/edge/test_e2e_edge.py` + `test_e2e_tls.py` via `uv run pytest -m e2e` against `E2E_TLS_URL`/`E2E_BASE_URL`. Task 7 RETARGETS this at the kind edge: `make kind-up` (task 6) → drive `https://127.0.0.1:8443` (the kind NodePort, self-signed TLS → verify off) → NEW assertions incl. the usage+cost row → `make kind-down`. Mechanism (script vs Makefile target vs pytest -m kind_e2e) is a §1/§3 fork.
  - **The goal-flow HTTP sequence (reuse the edge e2e helpers, `apps/gateway/tests/edge/test_e2e_edge.py`):** `_e2e_signup_and_login` = `POST /admin/auth/signup` → `POST /admin/auth/login` → JWT; `_e2e_create_key` = `POST /admin/keys` (JWT auth, 201, returns `{full key…}`); proxied completion = `POST /v1/chat/completions` with `Authorization: Bearer <api-key>` → routes through Envoy ext_authz → gateway → upstream. Envoy paths: `/admin/*` jwt_authn, `/v1/*` ext_authz (task 3).
  - **The in-cluster upstream stub (task 6, `infra/kind/upstream-stub.yaml`)** answers `POST …/chat/completions` → `model: "kind-stub-model"`, `usage: {prompt_tokens: 9, completion_tokens: 7, total_tokens: 16}` (non-stream + SSE). In kind ALL `gateway.upstreamBaseUrls.*` → the stub (values-kind.yaml), so a real completion succeeds with ZERO provider keys.
  - **The usage+cost READ surface** — `GET /admin/usage` (`apps/gateway/src/gateway/usage/api/router.py:102`, `usage_router` prefix `/admin`, JWT auth) → `UsageTotalsResponse` (`usage/api/schemas.py`): `total_cost_usd` + `records: [UsageRecordItem{id, model_id, prompt_tokens, completion_tokens, cost_usd (str Decimal — EXACT), status, created_at}]`. This is the row the e2e asserts.
  - **Pricing/recording reality (the central constraint):** `RecordingUsageRecorder` (`usage/application/recorder.py`) NEVER raises into the proxy path and records `cost_usd=0` when NO `PricingSnapshot` exists for the model. The catalog is **sync-only from a HARD-CODED `https://openrouter.ai/api/v1/models`** (`catalog/infrastructure/openrouter_source.py:19`, no config knob) — offline-broken in kind; the stub `/v1/models` returns only `{"status":"ok"}` (no pricing). `provider_for(model_id)` (`proxy/infrastructure/catalog_provider_resolver.py:57`) defaults UNKNOWN models → `"openrouter"` (→ stub in kind), so a completion always ROUTES, but an UNPRICED model bills $0. ⟹ to assert an ACCURATE NON-ZERO cost the e2e MUST first put a PricingSnapshot in the kind DB — the §1/§3 DESIGN FORK.
Context (working folder): `.add/milestones/v53/MILESTONE.md` task line 35 (e2e through the Envoy edge on the live kind cluster: signup→login→proxied completion (stubbed upstream)→accurate usage+cost row) + exit criterion line 47. Analog harness `scripts/e2e_edge.sh` + `scripts/edge_smoke.sh` (compose edge). Task-6 kind harness: `make kind-up`/`kind-down`/`kind-smoke`, edge at `https://127.0.0.1:8443`, the upstream stub, values-kind.yaml (jwtSecret `kind-local-insecure-jwt-secret`, NP disabled). Existing e2e markers: pytest `-m e2e` (`apps/gateway`).
Honors (patterns / conventions): E2E-THROUGH-THE-EDGE (drive the real Envoy NodePort, not the gateway directly — exercises ext_authz + jwt_authn + TLS) · ZERO-CLOUD-CREDS/OFFLINE (the stub stands in for every provider; no real keys; the OpenRouter catalog sync is NOT reachable/used) · DESIGN-FOR-FAILURE (bounded waits, kind-up diag-on-fail already exists; the e2e must clean up / be idempotent) · REUSE-THE-EDGE-E2E-PATTERNS (signup/login/key helpers already proven against the compose edge) · ACCURATE-BILLABLE-COST (the milestone GOAL — the cost row must be exact, str(Decimal), not a $0 degenerate).
Anchors the contract cites: NEW kind-e2e harness (script/target) + the e2e test(s) · the goal-flow sequence `POST /admin/auth/signup`→`/admin/auth/login`→`/admin/keys`→`/v1/chat/completions` · the stub usage block `{9, 7}` · `GET /admin/usage` → `UsageTotalsResponse.records[].{model_id,prompt_tokens,completion_tokens,cost_usd}` · the PRICED-MODEL-SEEDING decision (how the kind DB gets a PricingSnapshot so cost is accurate+non-zero) · the kind edge `https://127.0.0.1:8443` + `make kind-up`/`kind-down`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: e2e-core-flow — an automated, reproducible test that drives the FULL money path through the LIVE kind Envoy edge (task 6) and proves the platform records an ACCURATE, NON-ZERO usage+cost row. The compose edge e2e (`test_e2e_edge.py`) already proves ext_authz *passes* a keyed `/v1` call; this task adds what it never asserts — the priced usage row.
Framings weighed: drive-the-edge-NodePort-and-assert-the-priced-row (chosen — the milestone's "accurate usage+cost row" exit criterion) · drive-the-gateway-ClusterIP-directly (rejected — skips Envoy ext_authz/jwt_authn/TLS, the whole point of an *edge* e2e) · assert-only-that-a-row-exists-at-$0 (rejected at the design fork — proves usage tracking, not BILLABLE cost).
Priced-model decision (Tin, 2026-06-27, AskUserQuestion): the e2e SEEDS pricing directly in the kind DB (no gateway code change, fully offline) — NOT catalog-sync (OpenRouter-only, unreachable) nor a $0 degenerate.
v2 CHANGE-REQUEST (Tin, 2026-06-27, AskUserQuestion "Extend task 7"): the live run CAUGHT a real, prod-relevant chart defect — the Helm chart never wires `GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY`, so the BYOK credential store raises `ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE` → every `/v1/chat/completions` 500s. The gateway is BYOK-only (resolver wired unconditionally, NO system-key fallback), so a completion also needs a per-tenant provider key (else 402). Task-6 smoke (health + `/v1/models 401`) and the compose e2e ("not 401/403") never asserted a real 200 → the gap was invisible until this task. Fix folded into task 7: (a) wire the enc-key SECRET into the chart; (b) the e2e registers a tenant BYOK key via the admin API before completing. Proven live by a spike: enc-key + BYOK → 200, usage{9,7}, cost 0.00010560 = (9·P+7·C)·1.2 EXACT, model_id==requested (served==requested confirmed).
Must:
<must>
  - M1 — GOAL FLOW through the live edge: against the kind edge `https://127.0.0.1:8443` (self-signed → TLS verify off), `POST /admin/auth/signup` → `POST /admin/auth/login` (JWT) → `POST /admin/keys` (api key, field `key`) → `POST /v1/chat/completions` with `Authorization: Bearer <key>` and `{"model": "<E2E_MODEL>", "messages":[…]}` returns 200 carrying the stub's completion + usage `{prompt_tokens:9, completion_tokens:7}`. Drives ONLY the Envoy NodePort (exercises TLS + jwt_authn + ext_authz), never the gateway ClusterIP.
  - M2 — SEED pricing BEFORE the completion: insert a catalog `models` row (id=`<E2E_MODEL>`, active) AND a `pricing_snapshots` row (model_id=`<E2E_MODEL>`, `prompt_usd_per_token`=P, `completion_usd_per_token`=C, known constants) into the kind Postgres via `kubectl exec ai-proxy-postgres-0 -- psql -U gateway -d gateway`. The `models` row double-serves as the FK parent (`pricing_snapshots.model_id`→`models.id` RESTRICT) and the catalog existence row. Idempotent (ON CONFLICT DO NOTHING).
  - M3 — ASSERT the accurate row: after the completion, `GET /admin/usage` (JWT) within a BOUNDED poll (≤ 30 s, 1 s interval — the flusher is a 1 s background task) returns a record for `<E2E_MODEL>` with `prompt_tokens==9`, `completion_tokens==7`, `status==200`, and `cost_usd` EXACTLY `(9·P + 7·C) × (1 + markup/100)`, strictly `> 0`, where `markup` is the e2e tenant's `markup_pct` READ from the DB (not hardcoded). `total_cost_usd > 0`.
  - M4 — REPRODUCIBLE + ISOLATED FROM the default suite: a thin harness (`scripts/e2e_kind.sh` + `make kind-e2e`) ensures the cluster is up, seeds, runs the live test, tears down on demand; the live test carries a `kind_e2e` marker EXCLUDED from `make test-fast` (so the default run never needs a cluster). Re-running is safe (re-seed idempotent; each run signs up a unique tenant).
  - M5 (v2) — DEPLOYMENT CAN SERVE THE MONEY PATH: the Helm chart wires `GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY` from the gateway Secret (mirrors `jwtSecret`: values knob + `gateway-secret.yaml` stringData + `gateway-deployment.yaml` env), and the kind overlay sets a FAKE test Fernet key (SECRETS-NEVER-IN-CHART preserved). The e2e, being BYOK-only, registers a tenant provider key BEFORE completing: `PUT /admin/provider-keys/openrouter` body `{"secret": "<dummy>"}` (owner JWT) → 200. Without (a) the enc-key wiring the completion 500s; without (b) the BYOK key it 402s.
</must>
Reject:
<reject>
  - R1 — `POST /v1/chat/completions` with NO / an invalid API key → the EDGE (ext_authz) rejects with 401/403; the request never reaches the upstream and NO usage row is created for it. (proves ext_authz is live at the kind edge, not just "a 200 came back")
  - R2 — a completion for an UNPRICED model (no seed) → a usage row with `cost_usd == 0` (honest-degrade). This is the CONTRAST that proves M3's non-zero cost comes from the seed, not from the pipeline defaulting non-zero — it makes the green un-gameable.
</reject>
After:
<after>
  - The kind DB holds: the seeded `models` + `pricing_snapshots` rows; ≥1 `usage_records` row for the e2e tenant with the exact non-zero `cost_usd`. The cluster stays Ready (the e2e is non-destructive beyond its own seed + signup). The default `make test-fast` is unchanged (kind_e2e not collected).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The recorder bills against the REQUESTED model id (for a plain, non-alias model `served_model_id == model_id`, confirmed `use_cases.py:1243-1257`) — LOWEST confidence because if any alias/router rewrites the served id in the kind wiring, pricing is looked up under a different key, the seeded snapshot misses, and `cost_usd` degrades to 0 → M3 fails. If wrong: the seed must target `served_model_id`, not the requested id. Mitigation: use a plain id with no alias config (kind ships none) + the §5 build CONFIRMS served==requested on the live run before trusting M3.
  - [ ] The in-cluster gateway runs the usage flusher (background lifespan task, 1 s — `flusher.py:330`) so the row lands in `usage_records` within the poll window — if the chart disabled it, M3 times out. Confirm on the live run.
  - [ ] `kubectl exec` + `psql` into `ai-proxy-postgres-0` works under the kind overlay's local-socket trust (no password). If trust is off, the seed passes `PGPASSWORD` from the `ai-proxy-datastore-secrets` value (`kind-local-pg-pass`).
  - [ ] The default tenant `markup_pct` is 20.0 (`tenants` server_default) and the e2e READS it for the e2e tenant (join via the created key) rather than hardcoding — keeps M3 exact if signup ever sets a different markup.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: priced completion records an accurate non-zero cost row (M1+M2+M3+M5)
  Given the kind stack is Ready (chart wires GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY) and pricing for "<E2E_MODEL>" is seeded (P prompt, C completion per token)
  And a fresh tenant has signed up, logged in (JWT), REGISTERED a BYOK key (PUT /admin/provider-keys/openrouter {secret}), and minted an API key — all through the edge
  When the tenant POSTs /v1/chat/completions {model:"<E2E_MODEL>"} with Authorization: Bearer <key> to https://127.0.0.1:8443
  Then the edge returns 200 with usage {prompt_tokens:9, completion_tokens:7}
  And GET /admin/usage (within a bounded 30s poll) shows a record for "<E2E_MODEL>" with prompt_tokens=9, completion_tokens=7, status=200
  And that record's cost_usd == (9·P + 7·C) × (1 + markup/100) and is strictly > 0

Scenario: the call traverses the edge, not the gateway directly (M1)
  Given the seeded, keyed happy path above
  When the completion is issued
  Then it was accepted by Envoy ext_authz (status is not an Envoy 401/403) and TLS terminated at the NodePort
  And the gateway ClusterIP was never addressed directly by the test

Scenario: the live e2e is isolated from the default suite (M4)
  Given the kind_e2e marker on the live test and addopts "-m 'not e2e and not kind_e2e'"
  When `make test-fast` runs with no cluster present
  Then the live kind e2e is not collected and the default suite passes
  And `make kind-e2e` (cluster up) collects and runs exactly the kind_e2e test

Scenario: unauthenticated /v1 is rejected at the edge (R1)
  Given the kind edge is up
  When POST /v1/chat/completions is issued with no/invalid Authorization to https://127.0.0.1:8443
  Then the edge (ext_authz) returns 401 or 403
  And no usage_records row is created for that rejected request

Scenario: an unpriced model bills $0 — the honest-degrade contrast (R2)
  Given NO pricing snapshot exists for model "<UNPRICED_MODEL>"
  When the tenant completes against "<UNPRICED_MODEL>" through the edge
  Then GET /admin/usage shows that record with cost_usd == 0
  And the seeded-model record's cost_usd remains the non-zero accurate value (the seed, not the pipeline, produced it)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This task ships no new HTTP endpoint — it freezes the OBSERVABLE shape of an e2e HARNESS that
drives EXISTING surfaces. Constants are frozen; prices/markup are read, not invented.

```
CONSTANTS (frozen)
  E2E_MODEL       = "e2e-kind-priced"      # dedicated catalog id — obviously test data
  UNPRICED_MODEL  = "e2e-kind-unpriced"    # R2 contrast — seeded as a models row, NO pricing row
  P (prompt_usd_per_token)     = 0.000002  # distinct from C so a prompt/completion swap is caught
  C (completion_usd_per_token) = 0.000010
  EDGE_URL        = "https://127.0.0.1:8443"   # kind NodePort (self-signed → verify=False)
  POLL            = ≤ 30 s, 1 s interval       # bounded wait for the 1 s flusher

HARNESS SURFACE (frozen entrypoints)
  scripts/e2e_kind.sh            # bring cluster up if needed · seed · run `uv run pytest -m kind_e2e --no-cov` · trap-teardown opt-in
  make kind-e2e                  # → scripts/e2e_kind.sh
  pytest marker: kind_e2e        # registered in apps/gateway/pyproject.toml; default addopts excludes it
  tests/kind_e2e/test_e2e_kind_core_flow.py   # the live test (M1–M4 + R1 + R2)
  tests/kind_e2e/conftest.py                  # edge client + seed/teardown fixture (kubectl exec psql)

SEED (kubectl exec ai-proxy-postgres-0 -- psql -U gateway -d gateway), idempotent:
  INSERT INTO models (id, name, active, provider, modality)
    VALUES (:model, :name, true, 'openrouter', 'chat') ON CONFLICT (id) DO NOTHING;   # for E2E_MODEL and UNPRICED_MODEL
  INSERT INTO pricing_snapshots (id, model_id, prompt_usd_per_token, completion_usd_per_token)
    VALUES (gen_random_uuid(), :model, :P, :C);                                       # ONLY for E2E_MODEL

CHART ENC-KEY WIRING (v2 — the prod-relevant fix; mirrors jwtSecret, render-safe so existing helm tests stay green):
  values.yaml          gateway.providerKeyEncryption: { existingKey: "provider-key-encryption-key", value: "" }
  gateway-secret.yaml  stringData adds  {{ .Values.gateway.providerKeyEncryption.existingKey }}: <value>  (createSecret path; "" when unset — NO `required`)
  gateway-deployment   env GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY ← secretKeyRef(jwtSecretName, providerKeyEncryption.existingKey)
  values-kind.yaml     gateway.providerKeyEncryption.value = "<FAKE public test Fernet key>"   (SECRETS-NEVER-IN-CHART: throwaway, never real)
  PROD RUNBOOK (delta): operators whose existingSecret is external MUST add the provider-key-encryption-key key, else completions 500.

GOAL FLOW (all via EDGE_URL, reusing tests/edge helpers' shapes):
  POST /admin/auth/signup  {tenant_name,email,password}            -> 201
  POST /admin/auth/login   {email,password}                        -> 200 { access_token }
  PUT  /admin/provider-keys/openrouter {secret:"<dummy>"} (JWT)    -> 200   (v2 — BYOK precondition; bearer providers use field `secret`)
  POST /admin/keys         {name}  (Bearer JWT)                    -> 201 { key, key_id }
  POST /v1/chat/completions {model,messages} (Bearer <key>)        -> 200 { …, usage:{prompt_tokens:9,completion_tokens:7} }
       no/invalid Bearer                                            -> 401|403 at the edge (R1)

READ + ASSERT:
  GET /admin/usage (Bearer JWT) -> UsageTotalsResponse {
        total_cost_usd, records: [ UsageRecordItem{model_id, prompt_tokens, completion_tokens, cost_usd(str Decimal), status, …} ] }
  markup read: SELECT t.markup_pct FROM tenants t JOIN api_keys k ON k.tenant_id = t.id WHERE k.id = :key_id
  expected  = (Decimal(9)·P + Decimal(7)·C) · (1 + markup/100)
  assert Decimal(record.cost_usd) == expected > 0   (E2E_MODEL);   == 0   (UNPRICED_MODEL)

Schema touched (READ + seed only — NO migration, NO gateway src):
  models             — INSERT seed rows (id PK, active, provider, modality)
  pricing_snapshots  — INSERT seed row (model_id FK→models.id RESTRICT; prompt/completion_usd_per_token NUMERIC(20,10))
  tenants, api_keys  — READ markup_pct via join
  usage_records      — READ (written by the in-cluster flusher; never touched by the test)
```

Status: FROZEN @ v2 — approved by Tin · 2026-06-27 (extend-task-7 change-request: chart enc-key wiring + BYOK precondition)
Least-sure flag surfaced at freeze: [contract] the chart's GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY env must read from the SAME Secret the jwtSecret env reads (jwtSecretName) under a NEW key `provider-key-encryption-key`; if the createSecret path omits that key (or uses `required` and renders empty), helm template fails OR the env resolves empty → completion 500s exactly as the live run caught. Cost: a broken chart re-introduces the defect this change-request exists to fix. Mitigation: gateway-secret.yaml writes the key WITHOUT `required` (renders "" when unset, so external-secret installs stay green) + the existing tests/helm + tests/kind suites stay green + the §5 live run RE-CONFIRMS a 200 completion end-to-end. (Carried v1 flag still holds: served_model_id == model_id for a plain non-alias model — use_cases.py:1243-1257; kind ships no alias config + the live M3 record matches model_id==e2e-kind-priced.)
<!-- Bundle freeze: the lowest-confidence flag surfaced at the freeze was [contract] served_model_id ==
     model_id for a plain non-alias model (use_cases.py:1243-1257) — carried into §5 build for a live-run
     confirmation before M3 is trusted. Priced-model mechanism = seed-via-psql (Tin, AskUserQuestion). -->
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: n/a — this is an OUT-OF-PROCESS e2e against a live cluster (runs under `make kind-e2e`, `--no-cov`); the default coverage gate is unaffected (kind_e2e excluded from `make test-fast`).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_priced_completion_records_accurate_cost (M1+M2+M3): arrange signup→login→key→[pricing seeded for E2E_MODEL] / act POST /v1 {model:E2E_MODEL} through the edge / assert 200 + usage{9,7} AND GET /admin/usage row {model,9,7,status200} with cost_usd == (9·P+7·C)×(1+markup/100) > 0 (markup read from DB).
  - test_keyed_completion_accepted_at_edge (M1): arrange a valid key / act POST /v1 over TLS / assert status not in (401,403) and == 200 (ext_authz passed a real key).
  - test_unauthenticated_v1_rejected_at_edge (R1): arrange no key / act POST /v1 / assert edge returns 401|403 (request never reaches upstream).
  - test_unpriced_model_bills_zero (R2): arrange UNPRICED_MODEL (models row, NO pricing) / act complete / assert usage row cost_usd == 0 (the contrast proving M3's non-zero comes from the seed).
  RED reason (behaviour-grounded): without the §5 seed, an unpriced E2E_MODEL bills $0 → M3 fails on `cost_usd > 0`; the seed is the implementation that turns it green. (Supporting tests exercise already-working edge behaviour.)
</test_plan>

Tests live in: `apps/gateway/tests/kind_e2e/` · MUST run red (missing seed/harness) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `scripts/e2e_kind.sh` `Makefile` `apps/gateway/pyproject.toml` `apps/gateway/tests/kind_e2e/` `charts/ai-proxy/values.yaml` `charts/ai-proxy/values-kind.yaml` `charts/ai-proxy/templates/gateway-secret.yaml` `charts/ai-proxy/templates/gateway-deployment.yaml` `tests/kind/test_kind_bootstrap.py`   <!-- dir token = whole subtree: __init__.py · conftest.py · test_e2e_kind_core_flow.py · v2: 4 chart files for the enc-key wiring + task-6's overlay-guard (authorize the 2 new gateway-template edits) -->
Reuse note: the §3 surface lists the two test files; the dir token covers them plus the package __init__.py (test infra). v2 adds the 4 chart files named in §3 CHART ENC-KEY WIRING — they MIRROR the existing jwtSecret wiring (values knob → secret stringData → deployment env), no new mechanism. v2 also updates task-6's `test_kind_overlay_only_authorized_template_edit` allow-list to RECORD the two now-authorized gateway-template edits (Tin's "Extend task 7" decision) — the guard keeps its teeth (any OTHER template change still fails); this is recording an authorized expansion, NOT weakening a test. SURFACED at the verify gate.
Strategy (ordered batches): 1. register the `kind_e2e` marker + widen default addopts exclusion (pyproject) 2. the live test + conftest (seed/teardown fixture via kubectl-exec-psql; reuse tests/edge helper shapes; v2: register_provider_key helper + tests register BYOK before completing) 3. `scripts/e2e_kind.sh` + `make kind-e2e` orchestration 4. (v2) wire GATEWAY_PROVIDER_KEY_ENCRYPTION_KEY into the chart (values + secret stringData + deployment env, render-safe no `required`) + fake Fernet key in values-kind.yaml; run tests/helm + tests/kind to confirm no chart regression 5. live-run on the kind cluster, confirm a 200 completion end-to-end + served==requested + the accurate row.
Safety rule (feature-specific): the seed is idempotent (ON CONFLICT) and the test NEVER mutates `usage_records` or gateway src; the default `make test-fast` must stay cluster-free (kind_e2e excluded). Bounded polls everywhere (design-for-failure); no unbounded waits.
Code lives in: `scripts/`, `apps/gateway/tests/kind_e2e/` (this is a TEST/harness task — no `src/` changes).
Constraints: do NOT change any test or the contract; allow-list packages only (httpx, pytest already present); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — live `tests/kind_e2e` 4 passed (against the chart-reconciled stack); `tests/helm`+`tests/kind` 85 passed.
- [x] coverage did not decrease — NO `src/` change; kind_e2e excluded from the coverage gate (out-of-process, `--no-cov`); the coverage-measured suite is untouched.
- [x] no test or contract was altered during build — my OWN snapshotted kind_e2e tests were unchanged during build (build→verify advanced with no `build_tampered`). The ONE test edit (task-6's `test_kind_overlay_only_authorized_template_edit` allow-list) is the v2 change-request's AUTHORIZED expansion, declared in §5, NOT a build-time weakening — the guard still fails on any OTHER template change. SURFACED to Tin (gate FLAG).
- [x] the green was EARNED — adversarial refute-read (general-purpose subagent) VERDICT=CONCERNS, NO confirmed cheat / NO HARD-STOP. It could NOT break: exact-Decimal cost assertion + separate `>0` guard; behaviour-grounded red (no-seed → pricing None → cost stays _ZERO); R2 `==0` contrast non-vacuous; tenant-scoped `GET /admin/usage` (`WHERE tenant_id=:tid`) → deterministic; UUID-validated markup read (no injection); marker truly excluded. 4 findings (1 MED + 3 LOW) → gate FLAGS + observe-deltas, none a cheat.
- [x] concurrency / timing safe — bounded polls everywhere (≤30s/1s for the 1s write-behind flusher; kubectl/http timeouts); idempotent seed (`ON CONFLICT` + `WHERE NOT EXISTS`); each run a unique tenant. No unbounded waits.
- [~] no exposed secrets, injection openings, or unexpected dependencies — INJECTION: clean (UUID-validated `read_tenant_markup_pct`; fixed-argv kubectl; script-const model ids). DEPS: none new (httpx/pytest present). SECRETS: only a PUBLICLY-KNOWN throwaway Fernet key + a dummy BYOK secret in the kind overlay (SECRETS-NEVER-IN-CHART holds); BUT this adds a NEW secret KEY to the chart → SECURITY HARD-STOP rule ⟹ escalates to Tin (never auto-PASS). See FLAGS.
- [x] layering & dependencies follow CONVENTIONS.md — the chart enc-key wiring MIRRORS the existing jwtSecret path (values knob → secret stringData → deployment secretKeyRef env); no new mechanism. Test-only/harness task, no `src/`.
- [ ] a person reviewed and approved the change — PENDING Tin (this gate; security + sibling-test + ops-tradeoff sign-off).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] LIVE green: `./scripts/e2e_kind.sh --no-up` seeded + ran all 4 kind_e2e tests GREEN against the real Envoy edge — `4 passed in 2.81s`.
- [x] (v2) CHART SERVES THE MONEY PATH: `helm upgrade` (chart-alone, REVISION 3) reconciled the enc-key env to `secretKeyRef=ai-proxy-gateway-secrets/provider-key-encryption-key` (literal `value` GONE — the imperative spike was removed first); the gateway Secret now carries `provider-key-encryption-key` (decoded == values-kind.yaml). The 4 green completions returned 200, NOT 500. Server-side-apply even FAILED first while the imperative literal coexisted with the chart's `valueFrom` — proving the chart is now the sole source.
- [x] (v2) CHART REGRESSION-FREE: `tests/helm`+`tests/kind` 85 passed after the wiring; `helm lint` clean. Render-safe verified (no `required`; renders "" when unset).
- [x] (v2) BYOK precondition enforced: `register_provider_key` PUTs `/admin/provider-keys/openrouter {secret}` → asserts 200 before each completion; all completions resolved the credential (no 402).
- [x] Behaviour-grounded red→green: DELETED the pricing snapshot → `test_priced…` FAILED (`assert 0 == 9` on prompt_tokens) → re-seeded → GREEN. NOTE: the red surfaces on the TOKEN assertion FIRST (not `cost_usd>0` as §4 predicted) because the recorder reads token counts ONLY inside `if pricing is not None` (`recorder.py` `_record_internal`) — so no-seed → {0,0} tokens AND $0. An EVEN STRONGER red; both assertions are seed-gated. (→ observe-delta.)
- [x] served == requested: the green M3 only matches a record whose `model_id == e2e-kind-priced` (the REQUESTED id, not the stub's `kind-stub-model`) and asserts the exact cost on it — the ⚠ v1 freeze flag (served==requested) is upheld live.
- [x] Default suite stays cluster-free: `pytest --collect-only -q tests/kind_e2e` → `no tests collected (4 deselected)`; marker registered in pyproject; no PytestUnknownMarkWarning.
- [x] Honest-degrade contrast holds: R2 (`test_unpriced_model_bills_zero`, `cost_usd==0`) and M3 (non-zero exact) both pass in the SAME run — the non-zero cost comes from the seed, not a pipeline default.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `make kind-e2e`→`scripts/e2e_kind.sh`→ seeds + runs `pytest -m kind_e2e`; marker registered+excluded in pyproject; every conftest helper (`signup_and_login`/`register_provider_key`/`create_key`/`complete`/`poll_usage_record`/`read_tenant_markup_pct`) is referenced by a test. Confirmed by the live run + default collect.
- [x] DEAD-CODE (code) — gateway-scoped `tests/kind_e2e` ruff CLEAN; refute-read found no unused helper/constant; every seeded id/price is asserted.
- [x] SEMANTIC (prose / non-code) — seed SQL idempotent (`ON CONFLICT DO NOTHING` + `WHERE NOT EXISTS`); bounded waits; teardown opt-in (`--down`, default LEAVE up); no REAL secret (throwaway Fernet + dummy BYOK in the kind overlay only); default suite untouched.

### GATE RECORD
Outcome: PASS
FLAGS for Tin's decision:
  F1 (SECURITY · escalate) — the v2 chart change introduces a NEW gateway Secret KEY (`provider-key-encryption-key`). Committed values are a PUBLICLY-KNOWN throwaway Fernet key + a dummy BYOK secret, kind-overlay ONLY (SECRETS-NEVER-IN-CHART holds). Per the security HARD-STOP rule this never auto-PASSes → Tin sign-off.
  F2 (OPS-TRADEOFF · MED, refute-read F1) — the enc-key env is `optional: true`: a PROD deploy whose EXTERNAL Secret lacks the key BOOTS but every /v1 completion 500s (`ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE`). Chosen for BACKWARD-COMPAT (optional:false would break pod-startup for every existing prod deploy on upgrade) + honest-degrade. Mitigations: PROD RUNBOOK delta in §3 + the task-7 e2e now asserts a real 200 (catches the misconfig pre-prod). Follow-up (→SPEC delta): fail-fast at boot when environment=production AND key empty (mirror jwt `secret_ref_missing`).
  F3 (SIBLING-TEST · surfaced) — updated task-6's `test_kind_overlay_only_authorized_template_edit` allow-list to authorize the 2 new gateway-template edits (Tin's "Extend task 7"). Guard keeps teeth (any OTHER template change still fails). In §5 scope.
Outcome: PASS — Tin signed (AskUserQuestion 2026-06-27): green earned (refute-read=CONCERNS, no cheat/no HARD-STOP), only fake/throwaway test secrets committed, F2's prod fail-fast guard accepted as the [SPEC·open] follow-up (separate task), F3 sibling-test edit acknowledged as the authorized "Extend task 7" expansion.
Reviewed by: Tin Dang · date: 2026-06-27

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): /v1 5xx rate (esp. ERR_PROVIDER_KEY_ENCRYPTION_UNAVAILABLE) · usage-row cost_usd==0 rate for PRICED models (recording regression) · e2e green in CI.

### Spec delta
- [SPEC · open] Fail-fast at gateway boot when `environment=production` AND `provider_key_encryption_key` is empty (mirror the jwt `secret_ref_missing` guard) — converts F2's silent runtime 500 into a LOUD prod-only boot failure without breaking kind/dev. (evidence: refute-read F1 + the `optional:true` tradeoff; the live run proved a missing key = every completion 500s.)
- [SPEC · open] Recorder couples token recording to pricing presence — token counts are read ONLY inside `if pricing is not None` (`recorder.py:_record_internal`), so an UNPRICED model records {0,0} tokens (not just $0 cost) even though the upstream returned {9,7}. Usage analytics under-report tokens for any unpriced model. Decouple token capture from pricing. (evidence: the red-demo row {0,0}@200 for e2e-kind-priced with no snapshot.)
- [SPEC · open] `test_no_real_secret_literal` (tests/kind) only flags PEM headers — it would not catch a real Fernet/urlsafe-base64 key literal. Extend it to flag Fernet-shaped secrets, allow-listing the known kind throwaway. (evidence: refute-read F2.)
- [SPEC · open] M3 is fragile to the stub: if `infra/kind/upstream-stub.yaml` ever emits `usage.cost: 0`, the recorder's provider-cost branch sets cost_usd=0 → M3 red for the WRONG reason. Pin/guard the stub's usage shape. (evidence: refute-read F4; `_safe_provider_cost` returns Decimal(0) not None for cost==0.)

### Competency deltas
- [ADD · folded] A live e2e is the FIRST gate that asserts a real 200 on the money path — it caught a prod-relevant chart defect (missing enc-key wiring) that task-6 smoke (health + /v1/models 401) and the compose e2e ("not 401/403") both passed straight over. Lesson: an exit-criterion e2e must assert the SUCCESS body, not just "not rejected". (evidence: the v2 change-request existed only because this task asserted 200.) [folded foundation-version 39]
- [TDD · folded] The red surfaced one assertion EARLIER than §4 predicted (tokens, not cost) because of a hidden upstream coupling (recorder token↔pricing). Red-for-the-right-reason held, but the PREDICTED failing assertion was wrong — pre-declared red mechanisms should be verified against the real recording path, not assumed. (evidence: §4 said "fails on cost_usd>0"; actual `assert 0 == 9`.) [folded foundation-version 39]
- [ADD · folded] Mixing an imperative `kubectl set env` spike with declarative helm caused a server-side-apply conflict (`valueFrom` + `value` on one env) — the spike MUST be removed before `helm upgrade` reconciles. Lesson: prove fixes via the declarative path, not an imperative patch that later collides. (evidence: the first helm upgrade UPGRADE FAILED until the imperative env was deleted.) [folded foundation-version 39]
