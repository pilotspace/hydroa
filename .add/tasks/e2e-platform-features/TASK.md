# TASK: Extend the live kind e2e: realtime-relay WS round-trip + artifact object-store round-trip (MinIO) + key admin surfaces through the Envoy edge

slug: e2e-platform-features · created: 2026-06-27 · stage: production
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

Touches (files · symbols · signatures): EXTENDS the task-7 live kind e2e (`apps/gateway/tests/kind_e2e/`, marker `kind_e2e`, run via `make kind-e2e`→`scripts/e2e_kind.sh`) to also exercise THREE platform surfaces through the live Envoy edge — realtime-relay WS, artifacts/object-store (MinIO), and key admin reads. NO gateway `src/` change is required for the honest-degrade + admin + (object-store-enabled) artifact paths; all are reuse of shipped surfaces.
  - **A · REALTIME RELAY (v52)** — `proxy/api/realtime_relay_ws.py:150` `@realtime_relay_router.websocket("/v1/realtime/relay")` (registered `main.py:929`). AUTH-OVER-WS (v47-reuse `_authenticate_token`): server accepts → first frame MUST be `{"type":"auth","token":"sk-…"}` within `GATEWAY_REALTIME_AUTH_TIMEOUT_SECONDS` (10s) else close 4408; bad/again-shape→4401; **NO realtime provider configured → close 4404** (`_real_session_factory` returns None when provider∉{openai,gemini}). Envoy upgrades WS GLOBALLY on the TLS listener (`charts/ai-proxy/templates/envoy-configmap.yaml` `upgrade_configs:[{upgrade_type:websocket}]`), so `/v1/realtime/relay` upgrades with no path rule. **KIND REALITY: `values-kind.yaml` has NO `realtimeRelay` → provider="" → every relay WS closes 4404 (honest-degrade). The HTTP upstream-stub serves NO WS; relay adapters are openai/gemini ONLY — a media round-trip would need net-new gateway src (an echo provider) + a WS echo stub. §1 FORK.**
  - **B · ARTIFACTS / OBJECT STORE (v51)** — `artifacts/api/router.py:63` `artifacts_router` (registered `main.py:935`), API-key (Bearer `sk-`) auth: `POST /v1/artifacts` {name,content_type,content_base64}→201 {id,name,content_type,size_bytes,created_at}; `GET /v1/artifacts/{id}`→raw bytes + Content-Type/Disposition; `GET /v1/artifacts`→list; `DELETE`→204. Seam `objectstore/port.py:ObjectStore` (put/get/delete/health) ← `objectstore/s3.py:S3ObjectStore` (aioboto3, path-style, breaker+retry; key `artifacts/{tenant_id}/{artifact_id}`); `build_object_store()` returns None (→ honest-degrade `storage_backend="inline"` Postgres BYTEA) UNLESS enabled+endpoint+bucket+creds all set. **KIND REALITY: MinIO StatefulSet IS deployed (`ai-proxy-minio:9000`, creds in `ai-proxy-datastore-secrets` keys minio-root-user/minio-root-password) BUT `values-kind.yaml` has NO `gateway.objectStore` → enabled=false → artifacts go to inline BYTEA, NOT MinIO. A real MinIO round-trip needs object store ENABLED in values-kind (values-only change, the "external-ready" pattern) + a bucket (`minio-createbucket-job.yaml` exists, needs a bucket name). §1 FORK (recommend: enable it).**
  - **C · ADMIN SURFACES** — all JWT-Bearer reads, `/admin/*` (Envoy jwt_authn; ext_authz disabled for `/admin/`). Candidates: `usage/api/router.py` `/admin/usage`·`/admin/spend`·`/admin/reconciliation`·`/admin/alerts`·`/admin/audit`·`/admin/health/upstreams`·`/admin/ratelimits`·`/admin/bandwidth`·`/admin/slo`; `keys/api/router.py` `/admin/keys` (GET list); `proxy/api/provider_keys_admin_router.py` `/admin/provider-keys` (GET); `tenants/api/router.py` `/admin/auth/me`. Pick a representative read set in §1.
  - **D · HARNESS REUSE** — `apps/gateway/tests/kind_e2e/conftest.py`: `signup_and_login`·`create_key`·`register_provider_key`·`complete`·`poll_usage_record`·`psql_scalar`·`read_tenant_markup_pct`·`edge_client`(httpx verify=False)·`unique_suffix`. Edge `EDGE_URL=https://127.0.0.1:8443` (KIND_EDGE_URL). **WS CLIENT: httpx has NO WS; `websockets` is present TRANSITIVELY (uvicorn[standard]) — used by v52 src — but is NOT a declared dep (`pyproject.toml`); a kind WS test importing it may trip `make allowlist`. §1/§5 note.**
Context (working folder): `.add/milestones/v53/MILESTONE.md` task line 36 (extend the e2e: realtime-relay WS round-trip · artifact object-store round-trip (MinIO) · key admin surfaces — all live) + exit criterion line 48. Predecessor `e2e-core-flow` (task 7, committed 4890f74) = the harness this extends. values-kind.yaml (kind overlay), upstream-stub.yaml (HTTP-only).
Honors (patterns / conventions): E2E-THROUGH-THE-EDGE (drive the Envoy NodePort, exercise WS-upgrade+TLS+ext_authz/jwt_authn) · ZERO-CLOUD-CREDS/STUB (no real provider keys; MinIO is in-cluster) · IN-CLUSTER-NOW/EXTERNAL-READY (enabling object store = a values change, never a template edit) · HONEST-DEGRADE (4404 relay / inline-vs-MinIO is a real, observable contract) · DESIGN-FOR-FAILURE (bounded waits, idempotent) · REUSE-THE-KIND-E2E-HARNESS (same marker, conftest helpers, script).
Anchors the contract cites: the relay WS endpoint `/v1/realtime/relay` + auth-frame + close codes {4401,4404,4408} · the artifacts round-trip `POST /v1/artifacts`→`GET /v1/artifacts/{id}` (bytes echo) + the object-store backend (MinIO vs inline) · the chosen admin read set · the kind edge `wss/https://127.0.0.1:8443` · the values-kind object-store enablement (if forked ON) · the WS client lib decision.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: e2e-platform-features — extend the task-7 live kind e2e to prove THREE platform surfaces work through the REAL Envoy edge against the live cluster: a realtime-relay WS handshake (honest-degrade), an artifact object-store round-trip on MinIO, and key admin reads.
v2 CHANGE-REQUEST (Tin 2026-06-27 AskUserQuestion "Extend task 8 + fix envoy"): the live red run CAUGHT a real, prod-relevant edge defect — `/v1/realtime/relay` sits under the `/v1/` ext_authz route, so the WS handshake is 401'd at the edge BEFORE the gateway's in-band auth-over-WS can run; but a browser CANNOT set an Authorization header on a WS handshake (v52 auth is purely in-band) → the relay is UNREACHABLE through the edge. v52's live-verify was SKIPPED so this was never caught (parallel to task-7's enc-key defect). Fix folded into this task: add an envoy route disabling ext_authz for prefix `/v1/realtime/` (the gateway stays the sole authenticator — closes 4401 on a bad in-band token). Re-froze §3 @ v2; security review (disabling edge auth on one path) escalates to Tin at the gate.
Framings weighed: realtime = HONEST-DEGRADE WS handshake (chosen, Tin 2026-06-27 AskUserQuestion — drive wss through Envoy → auth-over-WS → assert the honest close codes; zero gateway src, zero new stub, faithful to the stub/no-key posture) · full-media-echo-round-trip (rejected — needs a net-new gateway echo provider + a WS echo stub that exist only for the e2e). artifacts = ENABLE MinIO in values-kind (chosen, Tin — a values-only "external-ready" change → a real object-store round-trip) · inline-BYTEA-only (rejected — would not satisfy the "object-store round-trip (MinIO)" exit criterion).
Must:
<must>
  - M1 — REALTIME WS HONEST-DEGRADE THROUGH THE EDGE: a WS client connects `wss://127.0.0.1:8443/v1/realtime/relay` with NO Authorization header (self-signed TLS → verify off; Envoy upgrades WS globally AND the M5 carve-out clears ext_authz for `/v1/realtime/` so the header-less handshake upgrades) and sends the auth frame `{"type":"auth","token":"<a VALID sk- API key>"}`; the gateway authenticates the key over the WS, finds NO realtime provider configured in kind, and closes the WS with code **4404** (honest-degrade). Proves WS-upgrade + TLS + auth-over-WS reach the gateway and the relay degrades honestly — end to end through the edge.
  - M2 — ARTIFACT OBJECT-STORE (MinIO) ROUND-TRIP: with object store ENABLED in values-kind (in-cluster MinIO `ai-proxy-minio:9000`, bucket pre-created), `POST /v1/artifacts {name,content_type,content_base64}` (Bearer `sk-` key) → 201 `{id, size_bytes==len(bytes), …}`; `GET /v1/artifacts/{id}` (same key) → 200 with the EXACT original bytes and the stored `Content-Type`. PROVE it persisted to MinIO (not inline): the `artifacts` row for `{id}` has `storage_backend='s3'` AND `object_key='artifacts/{tenant_id}/{id}'` (read via psql).
  - M3 — ADMIN READS THROUGH THE EDGE: for the e2e tenant's JWT, a representative set of `/admin/*` reads each return 200 with the expected shape — `GET /admin/auth/me` (tenant/user identity), `GET /admin/keys` (lists ≥1 key incl. the created key_id), `GET /admin/usage` (UsageTotalsResponse). Proves jwt_authn + the admin read surfaces are live behind the edge (ext_authz disabled for `/admin/`).
  - M4 — REPRODUCIBLE + HARNESS-INTEGRATED: new tests live in `apps/gateway/tests/kind_e2e/`, carry the `kind_e2e` marker (EXCLUDED from `make test-fast`), reuse the conftest helpers + add minimal new ones (ws connect→close-code, artifact post/get, admin get); `values-kind.yaml` enables object store; the suite is re-runnable (unique tenant per run, idempotent, bounded waits). The default run stays cluster-free.
  - M5 — ENVOY EXT_AUTHZ CARVE-OUT FOR THE RELAY (v2 change-request): the edge config disables ext_authz for prefix `/v1/realtime/` via a route placed BEFORE the `/v1/` route (Envoy first-match-wins), so the header-less WS handshake UPGRADES at the edge and the gateway's in-band auth-over-WS becomes the SOLE authenticator on that path. WITHOUT it the edge ext_authz 401s the upgrade request (a browser CANNOT set an Authorization header on a WS handshake — v52 auth is purely in-band), so the relay is unreachable through the edge (M1/R1 fail at the handshake). Security posture: auth is STILL ENFORCED — the gateway closes **4401** on a bad/absent in-band token (R1) BEFORE any provider session is opened, and the 10s auth timeout (4408) bounds an unauthenticated socket; the edge's global `local_rate_limit` still applies. ext_authz stays ENABLED for every OTHER `/v1/` path (`/v1/realtime/` is the only carve-out; `/v1/realtime/relay` is the only route under it).
</must>
Reject:
<reject>
  - R1 — RELAY AUTH REJECTS A BAD TOKEN: the relay WS receives an auth frame with an INVALID/garbage token → the gateway closes with **4401** (NOT 4404) and never opens a provider session. Proves auth-over-WS validates the token BEFORE the provider step (the 4404 path is reachable only with a VALID key) -> close `4401`.
  - R2 — OBJECT-STORE SURFACE GUARDED AT THE EDGE: `GET /v1/artifacts/{id}` with NO / an invalid API key → the EDGE (ext_authz, `/v1/*`) rejects; the request never reaches the artifacts handler and no bytes are read. Proves the artifact surface is auth-guarded at the edge like the completion path -> `401|403`.
</reject>
After:
<after>
  - The kind state holds: the created artifact row (`storage_backend='s3'`) + its object in MinIO at `artifacts/{tenant_id}/{id}`; relay closes create no billable usage rows. The cluster stays Ready. `make test-fast` is unchanged (kind_e2e not collected). `values-kind.yaml` now enables object store, so the kind deploy itself exercises MinIO.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Envoy passes the WS CLOSE CODE (4404 / 4401) THROUGH to the client UNCHANGED — LOWEST confidence because Envoy proxies the upgraded WS, but a close-code rewrite/normalization (or a half-open TCP teardown surfacing as 1006 instead of the app code) at the edge would make M1/R1 assert the wrong code. If wrong: relax to assert "the WS closed without relaying" + capture the actual arriving code, and pin the exact 4404/4401 against the gateway path. Mitigation: the §5 live run READS the real close code arriving through the edge before M1/R1 are trusted.
  - [ ] The object-store bucket EXISTS before the first PUT — `S3ObjectStore.put` does NOT create the bucket; the bucket (`gateway.objectStore.bucket`, createbucket-job default `ai-proxy-artifacts`) is made by the post-install hook Job. If it raced/didn't run, the first POST → 503. Confirm the bucket exists on the live run.
  - [ ] `storage_backend='s3'` + `object_key='artifacts/{tenant_id}/{id}'` are the literals written on the MinIO path (confirmed `artifacts/api/router.py:244-245`) and the row is readable via psql for the e2e tenant.
  - [ ] `websockets` is importable in the test venv (transitive via `uvicorn[standard]`, already used by v52 src) — if `make allowlist` flags an undeclared import in the test, add `websockets` to the gateway dev/test deps (NOT a new runtime dep).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: realtime relay honest-degrades over WS through the edge (M1)
  Given the kind stack is Ready with NO realtime provider configured (values-kind provider="")
  And a fresh tenant has signed up, logged in, and minted a VALID sk- API key through the edge
  When a WS client connects wss://127.0.0.1:8443/v1/realtime/relay and sends {"type":"auth","token":"<that key>"}
  Then the gateway authenticates the key and closes the WS with code 4404 (no provider — honest-degrade)
  And the WS was upgraded + TLS-terminated at the Envoy NodePort (not the gateway ClusterIP)

Scenario: an artifact round-trips through MinIO (M2)
  Given object store is ENABLED in values-kind (in-cluster MinIO, bucket pre-created) and a valid sk- key
  When the tenant POSTs /v1/artifacts {name,content_type,content_base64:<bytes>} and then GETs /v1/artifacts/{id}
  Then POST returns 201 with size_bytes == len(bytes) and GET returns 200 with the EXACT original bytes + Content-Type
  And the artifacts row for {id} has storage_backend='s3' and object_key='artifacts/{tenant_id}/{id}' (persisted to MinIO, not inline)

Scenario: key admin reads answer behind the edge (M3)
  Given the e2e tenant's JWT and a minted key
  When it GETs /admin/auth/me, /admin/keys, and /admin/usage through the edge
  Then each returns 200 with the expected shape (me=identity, keys lists the created key_id, usage=UsageTotalsResponse)
  And jwt_authn accepted the JWT at the edge (ext_authz is disabled for /admin/)

Scenario: the platform e2e is isolated from the default suite (M4)
  Given the kind_e2e marker on the new tests and addopts "-m 'not e2e and not kind_e2e'"
  When `make test-fast` runs with no cluster present
  Then the new platform tests are not collected and the default suite passes
  And `make kind-e2e` (cluster up, object store enabled) collects and runs them

Scenario: the edge carves ext_authz out of the relay path so the header-less WS upgrades (M5)
  Given the envoy config disables ext_authz for prefix /v1/realtime/ via a route BEFORE the /v1/ route (first-match-wins)
  When a WS client connects wss://127.0.0.1:8443/v1/realtime/relay with NO Authorization header
  Then the edge upgrades the handshake (it does NOT 401 it) and the gateway runs in-band auth-over-WS
  And ext_authz stays enabled for every other /v1/ path (an unauthenticated GET /v1/artifacts/{id} is still 401|403 at the edge — R2)
  And WITHOUT the carve-out the same header-less handshake is rejected 401 at the edge (the defect this v2 fixes)

Scenario: relay auth rejects a bad token (R1)
  Given the relay WS endpoint is reachable through the edge (M5 carve-out in place)
  When a client connects and sends {"type":"auth","token":"not-a-real-key"}
  Then the gateway closes the WS with code 4401 (NOT 4404) and opens no provider session
  And no relay/usage row is created for the rejected connection

Scenario: the artifact surface is guarded at the edge (R2)
  Given the kind edge is up
  When GET /v1/artifacts/{any-id} is issued with no/invalid Authorization to https://127.0.0.1:8443
  Then the edge (ext_authz) returns 401 or 403
  And the request never reaches the artifacts handler and no bytes are read
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This task ships no new gateway endpoint — it freezes the OBSERVABLE shape of e2e tests that drive
EXISTING surfaces through the live kind edge, plus a values-kind object-store enablement.

```
CONSTANTS (frozen)
  EDGE_URL    = "https://127.0.0.1:8443"             # kind NodePort (self-signed → verify=False)
  WS_URL      = "wss://127.0.0.1:8443/v1/realtime/relay"
  OBJ_BUCKET  = "ai-proxy-artifacts"                 # createbucket-job default; values-kind.objectStore.bucket
  ART_BYTES   = b"e2e-kind-artifact-bytes\n"         # the round-trip payload (content_type "text/plain")
  WS_RECV_TIMEOUT = 15 s                             # bounded wait for the relay close frame

HARNESS SURFACE (frozen entrypoints — slot into the task-7 harness)
  tests/kind_e2e/test_e2e_kind_platform.py     # the live tests (M1–M4 + R1 + R2)
  tests/kind_e2e/conftest.py                   # ADD helpers: ws_relay_close_code · create_artifact · get_artifact · admin_get
  pytest marker: kind_e2e                      # already registered + default-excluded (task 7)
  make kind-e2e → scripts/e2e_kind.sh          # unchanged runner (seeds pricing; these tests don't need the seed)

A · REALTIME RELAY (honest-degrade WS through the edge), reuse v52 wire:
  connect WS_URL (TLS verify off, NO Authorization header) → send TEXT frame {"type":"auth","token":"<sk- key>"}
    VALID key,   no provider configured  -> server CLOSE code 4404   (M1 honest-degrade)
    INVALID/garbage token                -> server CLOSE code 4401   (R1; never 4404, no provider session)
  (close code read from the WS close frame arriving THROUGH Envoy)
  REQUIRES the M5 envoy carve-out (below): /v1/realtime/relay is under the /v1/ ext_authz route, and a
  browser cannot set an Authorization header on a WS handshake, so without ext_authz disabled for
  /v1/realtime/ the edge 401s the upgrade and the relay is unreachable. The gateway's in-band
  auth-over-WS is the SOLE authenticator on this path (closes 4401 on bad token BEFORE any provider step).

B · ARTIFACTS object-store round-trip (MinIO ENABLED), reuse v51 endpoints:
  POST /v1/artifacts  {name, content_type:"text/plain", content_base64: b64(ART_BYTES)} (Bearer sk-)
     -> 201 { id, name, content_type, size_bytes==len(ART_BYTES), created_at }
  GET  /v1/artifacts/{id} (Bearer sk-)  -> 200 raw == ART_BYTES, Content-Type "text/plain"
  GET  /v1/artifacts/{id} (NO key)      -> 401|403 AT THE EDGE (R2, ext_authz on /v1/*)
  MinIO PROOF (psql): SELECT storage_backend, object_key FROM artifacts WHERE id='{id}'
     -> storage_backend == 's3'  AND  object_key == 'artifacts/{tenant_id}/{id}'

C · ADMIN reads through the edge (Bearer JWT):
  GET /admin/auth/me   -> 200 { tenant/user identity }
  GET /admin/keys      -> 200 { … } listing the created key_id
  GET /admin/usage     -> 200 UsageTotalsResponse { total_cost_usd, records:[…] }

CHART · values-kind ONLY — external-ready, no template edit (M2 object store):
  gateway.objectStore: { enabled: true, endpoint: "http://ai-proxy-minio:9000", bucket: OBJ_BUCKET,
                         region: "us-east-1", accessKeyId: "kind-local-minio",
                         secretRef: { name: "ai-proxy-datastore-secrets", key: "minio-root-password" } }
  (the deployment template already wires the objectStore env block on enabled=true; the createbucket
   post-install Job makes OBJ_BUCKET; S3ObjectStore.put does NOT create the bucket.)

ENVOY TEMPLATE · envoy-configmap.yaml (M5 carve-out — v2 change-request; authorized template edit):
  add a route BEFORE the `/v1/` route (Envoy first-match-wins) on virtual_host "local":
    - match: { prefix: "/v1/realtime/" }
      route:  { cluster: gateway_cluster }                 # WS upgrade is global on the TLS listener
      typed_per_filter_config:
        envoy.filters.http.ext_authz:
          "@type": type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthzPerRoute
          disabled: true                                   # gateway in-band auth-over-WS is the sole authenticator
  INVARIANT: ext_authz stays ENABLED on `/v1/` (and every other path) — `/v1/realtime/` is the ONLY
  carve-out. The overlay guard `test_kind_overlay_only_authorized_template_edit` already allows
  envoy-configmap.yaml (v2 FQDN); its comment records this task-8 authorization.

Schema touched (READ + values + 1 authorized template edit — NO migration, NO gateway src):
  artifacts          — READ storage_backend, object_key for the created id (written by the gateway)
  tenants, api_keys  — READ via the existing helpers (signup/key-create)
  usage_records      — READ via GET /admin/usage
  envoy-configmap.yaml — EDIT: add the /v1/realtime/ ext_authz carve-out (M5, v2 change-request)
```

Status: FROZEN @ v2 — approved by Tin · 2026-06-27 (v2 change-request "Extend task 8 + fix envoy": add the M5 envoy /v1/realtime/ ext_authz carve-out so the header-less WS handshake reaches the gateway's in-band auth; re-froze §3; security review of the carve-out escalates to Tin at the gate). v1 freeze (forks: realtime=honest-degrade · object-store=enable-MinIO) approved 2026-06-27.
Least-sure flag surfaced at freeze: [contract] disabling edge ext_authz for `/v1/realtime/` shifts the ENTIRE authentication of that path onto the gateway's in-band auth-over-WS — if the gateway did NOT close 4401 on a bad/absent token before opening a provider session, the carve-out would expose an unauthenticated relay. Cost: an auth bypass on the relay path. Mitigation: R1 (bad token → 4401, no provider session) + the 4408 auth-timeout are the live proof the gateway is the real guard; the §6 gate runs an adversarial refute-read of the carve-out and escalates the disable-edge-auth decision to Tin (security HARD-STOP). Secondary [contract]: Envoy passes the WS app CLOSE CODE (4404/4401) THROUGH unchanged — if the edge rewrites the close (or a half-open teardown surfaces as 1006), M1/R1 assert the wrong code; the live run reads the actual arriving code before M1/R1 are trusted.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: n/a — OUT-OF-PROCESS e2e against a live cluster (runs under `make kind-e2e`, `--no-cov`); the default coverage gate is unaffected (kind_e2e excluded from `make test-fast`).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_relay_honest_degrades_over_ws (M1): arrange fresh tenant + valid sk- key / act connect WS_URL, send {type:auth,token:key} / assert the WS close code == 4404 (read through the edge).
  - test_artifact_round_trips_through_minio (M2): arrange object store enabled + valid key / act POST /v1/artifacts(b64 ART_BYTES) then GET /v1/artifacts/{id} / assert 201 size_bytes==len, GET 200 bytes==ART_BYTES + Content-Type, AND psql artifacts row storage_backend=='s3' + object_key=='artifacts/{tenant}/{id}'.
  - test_admin_reads_answer_behind_edge (M3): arrange tenant JWT + a key / act GET /admin/auth/me, /admin/keys, /admin/usage / assert each 200 + shape (me identity, keys lists key_id, usage has records/total).
  - test_relay_rejects_bad_token (R1): arrange the relay endpoint / act connect + send {type:auth,token:"not-a-real-key"} / assert close code == 4401 (NOT 4404).
  - test_unauthenticated_artifact_rejected_at_edge (R2): arrange no key / act GET /v1/artifacts/{rand-uuid} / assert edge returns 401|403 (ext_authz; never reaches the handler).
  - test_only_websocket_routes_under_realtime_carveout + test_relay_ws_is_under_the_carveout (M5 GUARD, DEFAULT suite — gate-harden per Tin "harden Q2"): `tests/realtime_relay/test_carveout_invariant.py` introspects `create_app().routes` and asserts EVERY route under `/v1/realtime/` is a Starlette `WebSocketRoute` (+ an anchor that the relay WS IS under the prefix, so the guard can't pass vacuously). RED proof: injecting a fake HTTP `/v1/realtime/leak` route makes the guard flag it. This enforces the §3 carve-out invariant at CI (no cluster) — a future non-WS route under the prefix would be unauthenticated at the edge.
  RED reason (behaviour-grounded): TWO real reasons, both proven on the live red run. (1) M1/R1 FAIL at the WS handshake — on the current edge `/v1/realtime/relay` is under the `/v1/` ext_authz route, so the header-less handshake is 401'd at the edge before in-band auth runs (observed: HTTP 401 ERR_AUTH_INVALID_KEY); the §5 envoy `/v1/realtime/` carve-out (M5) is the implementation that lets the handshake upgrade → 4404/4401. (2) M2 FAILS until values-kind ENABLES object store — with object store off the row is storage_backend=='inline' (not 's3'), so the MinIO-proof assertion fails; enabling object store in §5 turns it green. (M3/R2 exercise already-shipped edge behaviour; the §5 envoy carve-out + chart enablement are what make the suite collectively green on the live cluster.)
  M4 (isolation) is asserted via the marker + a default collect, not a live test (mirrors task 7).
</test_plan>

Tests live in: `apps/gateway/tests/kind_e2e/` · MUST run red (object store not yet enabled) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/tests/kind_e2e/` `charts/ai-proxy/values-kind.yaml` `charts/ai-proxy/templates/envoy-configmap.yaml` `apps/gateway/pyproject.toml` `tests/kind/test_kind_bootstrap.py` `apps/gateway/tests/realtime_relay/test_carveout_invariant.py`   <!-- dir token = kind_e2e subtree (new test_e2e_kind_platform.py + conftest.py helpers); values-kind = object-store enablement; envoy-configmap = M5 /v1/realtime/ ext_authz carve-out (v2); pyproject only IF `make allowlist` needs `websockets` declared; test_kind_bootstrap = the overlay guard comment records the task-8 authorization (file already allow-listed); test_carveout_invariant = the DEFAULT-suite M5 route guard (gate-harden, Tin "harden Q2") -->
Strategy (ordered batches): 1. conftest helpers (ws_relay_close_code via websockets+unverified SSL · create_artifact · get_artifact · admin_get) 2. test_e2e_kind_platform.py (M1–M4 + R1 + R2) 3. M5 envoy carve-out: add the `/v1/realtime/` ext_authz-disabled route BEFORE the `/v1/` route in envoy-configmap.yaml + record the task-8 authorization in the overlay-guard comment 4. enable gateway.objectStore in values-kind.yaml (endpoint/bucket/creds-from-datastore-secret) 5. helm-upgrade reconcile + ensure the bucket + live-run; confirm the relay handshake now UPGRADES (no edge 401) and the close code (4404/4401) passes through the edge unchanged before trusting M1/R1; if `make allowlist` flags the test's websockets import, add it to pyproject dev deps.
Safety rule (feature-specific): the e2e NEVER mutates artifacts/usage rows it didn't create; bounded WS recv + http timeouts (design-for-failure); the seed/harness stays idempotent; default `make test-fast` stays cluster-free (kind_e2e excluded). values-kind enablement is the "external-ready" pattern — NO chart template edit.
Code lives in: `apps/gateway/tests/kind_e2e/`, `charts/ai-proxy/` (this is a TEST/harness + values task — no gateway `src/` changes).
Constraints: do NOT change any test or the contract; allow-list packages only (httpx/pytest present; websockets transitive — declare only if allowlist requires); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — `make kind-e2e` → 9 passed (4 core-flow + 5 platform) live; helm lint clean; tests/helm + tests/kind 85 passed.
- [x] coverage did not decrease — kind_e2e runs `--no-cov` (out-of-process e2e); the default coverage gate is untouched (kind_e2e excluded from `make test-fast`).
- [x] no test or contract was altered during build — §3 FROZEN @ v2 unchanged during build; the only test-tree edit is the new task-8 conftest helper (a non-deprecated close-code read; behaviour identical) — no assertion weakened.
- [x] the green was EARNED, not gamed — independent adversarial refute-read (security-expert subagent) = CONCERNS, no HARD-STOP / no confirmed cheat (0.87): handshake-401 raises InvalidStatusCode → loud fail (not a silent pass); 1006 fails the exact-code assert; 4404-vs-4401 cross-validated; MinIO holds real 24-byte objects at `artifacts/{tenant}/{id}`.
- [x] concurrency / timing of the risky operation is safe — relay in-band auth closes 4401 BEFORE any provider session; 10s auth-timeout (4408) bounds an idle socket; edge global local_rate_limit (100%) applies to the upgrade. Residual: no per-IP concurrent-WS cap (→ [SPEC] delta, not a blocker).
- [x] no exposed secrets, injection openings, or unexpected dependencies — SECRETS-NEVER-IN-CHART held (objectStore secret from the chart-minted datastore Secret; only FAKE kind values); `websockets` is transitive (NOT a new declared dep); `make allowlist`'s only failure is PRE-EXISTING `aioboto3` (v51 residue, fails with my changes stashed → [SPEC] delta).
- [x] layering & dependencies follow CONVENTIONS.md — no `apps/gateway/src/` change; values-kind = the "external-ready" pattern; the ONE template edit (envoy carve-out) is authorized by the v2 change-request + recorded in the overlay guard's allow-list.
- [ ] a person reviewed and approved the change — PENDING Tin (security HARD-STOP escalation: disabling edge ext_authz on `/v1/realtime/`).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] LIVE green: `make kind-e2e` (object store enabled) → 9 passed against the real Envoy edge — confirmed by the pytest summary `9 passed in 5.80s`.
- [x] Relay reachable through the edge (M5 carve-out works): the envoy `/v1/realtime/` route (rendered line 226, BEFORE `/v1/` line 232, `disabled: true`) lets the header-less WS handshake UPGRADE — M1 closes 4404, R1 closes 4401 through the edge; BEFORE the carve-out the same handshake was 401'd `ERR_AUTH_INVALID_KEY` (the red→green for M1/R1, proven on the prior run).
- [x] Carve-out is SCOPED + auth still enforced: ext_authz disabled ONLY for `/v1/realtime/` (v47 `/v1/realtime` no-slash + every other `/v1/` path keep it — R2 unauthenticated artifact GET still 401|403 at the edge); gateway closes 4401 on a bad in-band token BEFORE any provider session (refute-read Q1/Q5 PASS). SECURITY: disabling edge auth on one path → escalated to Tin at the gate with the refute-read.
- [x] WS close-code passes the edge: the relay WS closes with the EXACT app code (4404 valid / 4401 bad) — the tests assert `== 4404` / `== 4401` and passed, so Envoy relays the app code unchanged (no 1006/normalization). The freeze flag is resolved GREEN.
- [x] MinIO round-trip is REAL: M2's artifact row is `storage_backend='s3'` + `object_key='artifacts/{tenant}/{id}'` (psql read) AND MinIO holds the real 24-byte objects at `artifacts/{tenant_id}/{artifact_id}` (spot `mc ls` — 24 = len(ART_BYTES)).
- [x] Behaviour-grounded red→green: BEFORE the §5 changes M1/R1 failed at the handshake (edge 401) and M2 was `inline` (`3 failed, 2 passed`); AFTER the envoy carve-out + object-store enablement + reconcile → 9 passed (`s3`, 4404/4401) — pre/post on the live cluster.
- [x] Default suite stays cluster-free: a default `--collect-only` collects 0 kind_e2e (9 deselected); `-m kind_e2e` collects 9 — confirmed by the collect output.
- [x] No gateway src touched: `git diff --stat` = conftest.py, envoy-configmap.yaml (+13), values-kind.yaml (+13), test_kind_bootstrap.py (+9/-3) + new test_e2e_kind_platform.py + new test_carveout_invariant.py; NO `apps/gateway/src/` change.
- [x] M5 carve-out invariant is CI-ENFORCED (gate-harden, Tin "harden Q2"): `tests/realtime_relay/test_carveout_invariant.py` (DEFAULT suite, no cluster) asserts every route under `/v1/realtime/` is a WebSocketRoute + anchors the relay WS is under the prefix — 2 passed; RED-proven by injecting a fake `/v1/realtime/leak` HTTP route (guard flags it); full realtime_relay suite 33 passed + 1 skipped.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (config + helpers) — the envoy `/v1/realtime/` route is loaded by Envoy at runtime (rendered, envoy rolled-restarted, live M1/R1 traverse it → 4404/4401); the gateway objectStore env block is live (`printenv` shows GATEWAY_OBJECT_STORE_ENABLED=true + endpoint/bucket/creds); every new conftest helper (ws_relay_close_code · create_artifact · get_artifact · admin_get · read_artifact_storage · read_tenant_id) is imported + used by test_e2e_kind_platform.py.
- [x] DEAD-CODE — no orphaned symbol: each helper backs a test; the values-kind objectStore block is consumed by gateway-deployment.yaml; the carve-out route is matched by live traffic.
- [x] SEMANTIC (the security-sensitive change) — read in full: the frozen §3 contract, the envoy carve-out + its comment, and the relay handler `realtime_relay_ws.py` auth ordering (accept → first-frame → close 4401 BEFORE `_authenticate`/`_build_session`/`RelayPump`; `_authenticate`→v47 `_authenticate_token` which `except Exception: return None`, never raising to 1011). Confirmed: the carve-out is correctly scoped (trailing-slash prefix; only the relay WS under it) and auth is enforced at the gateway. Independent refute-read agreed (CONCERNS, no HARD-STOP).

### GATE RECORD
Outcome: PASS — Tin signed off (AskUserQuestion gate: "PASS, but harden a residual now" → "harden Q2").
  Security escalation RESOLVED: disabling edge ext_authz for `/v1/realtime/` is approved because the
  gateway's in-band auth-over-WS is the proven sole guard (R1 bad-token→4401 BEFORE any provider
  session; refute-read Q1/Q4/Q5 PASS, no HARD-STOP). The Q2 residual was HARDENED IN THIS TASK: a
  default-suite CI guard (`test_carveout_invariant.py`) now fails if any non-WS route appears under the
  carve-out prefix. Q3 (concurrent-WS cap) + aioboto3-allowlist + envoy-configmap-checksum ride as
  open SPEC deltas. Live: `make kind-e2e` 9 passed; default guard 2 passed (RED-proven); helm/kind 85
  passed; no `apps/gateway/src/` change.
Reviewed by: Tin Dang · date: 2026-06-27

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): relay close-code mix (4404 honest-degrade vs 4401 reject) · artifact storage_backend distribution (s3 vs inline fallback) · count of anonymous WS sockets that hit the 4408 auth-timeout (DoS signal).

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · done] guard the `/v1/realtime/` ext_authz carve-out with a CI assertion that ONLY WebSocket routes may register under that prefix — RESOLVED IN THIS TASK (Tin gate-harden "harden Q2"): `tests/realtime_relay/test_carveout_invariant.py` introspects `create_app().routes` + fails CI if a non-WS route appears under the prefix (RED-proven by injecting `/v1/realtime/leak`). (evidence: refute-read Q2.)
- [SPEC · open] add a per-source-IP concurrent-WS cap (Envoy `max_connections` circuit breaker on gateway_cluster, or in-process guard) — today the rate limiter bounds new-connection RATE but allows N×auth_timeout idle anonymous sockets on the carved-out relay path (evidence: refute-read Q3).
- [SPEC · open] add `aioboto3` (v51 S3 dep) to `dependencies.allowlist` — `make allowlist` is RED on HEAD independent of this task (fails with task-8 changes stashed); a pre-existing v51 residue blocking the import-allowlist gate.
- [SPEC · open] envoy-deployment should carry a configmap checksum annotation so a config-only chart change rolls envoy on an EXISTING cluster — today an envoy-config change needs a manual `kubectl rollout restart` (or a fresh kind cluster); the gateway already auto-rolls because its env changed (evidence: this task's reconcile required an explicit envoy restart).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [ADD · folded] a live e2e that drives the REAL edge catches edge-vs-app auth-seam defects unit/render tests can't — task-8's header-less-WS-blocked-by-ext_authz is the second such catch after task-7's enc-key (evidence: v52 live-verify was SKIPPED, so the relay-unreachable-through-edge defect shipped undetected until this task drove it live). [folded foundation-version 39]
- [ADD · folded] WS endpoints behind an ext_authz edge need an explicit auth-model decision (header-at-edge vs in-band-at-gateway) at CONTRACT time — browsers can't set WS handshake headers, so any header-based edge auth makes a relay unreachable (evidence: the §3 v2 change-request was forced by exactly this, mid-build). [folded foundation-version 39]
- [TDD · folded] cross-validated close-code asserts (== 4404 for valid, == 4401 for bad, never-1006) make a WS honest-degrade test un-gameable — a wrong code or a dropped connection fails loudly instead of passing vacuously (evidence: refute-read Q4 PASS). [folded foundation-version 39]
