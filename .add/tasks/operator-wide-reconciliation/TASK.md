# TASK: Cross-tenant reconciliation view behind ops-auth

slug: operator-wide-reconciliation · created: 2026-06-18 · stage: production · risk: high
autonomy: manual   <!-- LOWERED for risk:high (cross-tenant tenant-scoping exception + new ops-auth authority). The engine refuses an unguarded completion (unguarded_high_risk_auto). Security contract freeze is a HARD-STOP for Tin's explicit approval before any code. -->
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
- `apps/gateway/src/gateway/usage/application/reconciliation.py:reconcile_window` — `async def reconcile_window(session, window_from, window_to, tenant_id: uuid.UUID | None = None) -> ReconciliationSummary`. **KEY: `tenant_id=None` ALREADY = all-tenants mode** (SQL `tenant_clause = " AND tenant_id = :tid" if tenant_id is not None else ""`). READ-ONLY, two SELECTs on `usage_records`; never writes the ledger. Reused as-is — the milestone OPEN (all-tenants mode vs sibling query) is RESOLVED: the mode exists.
- `apps/gateway/src/gateway/usage/application/reconciliation.py:ReconciliationSummary` — frozen dataclass; drift = provider_cost_total − billed_total over cost_basis='provider'.
- `apps/gateway/src/gateway/usage/api/router.py:get_reconciliation` — the v29/v30 TENANT-SCOPED `GET /admin/reconciliation` (auth `require_owner_or_admin`, passes `tenant_id=identity.tenant_id`; docstring: "never invoked operator-wide here"). The sibling to mirror — the new ops route is the all-tenants peer.
- `apps/gateway/src/gateway/usage/api/schemas.py:ReconciliationResponse` — frozen Pydantic; Decimal-as-str money fields. Reusable shape; operator response may add a per-tenant breakdown (decide §3).
- `apps/gateway/src/gateway/keys/api/deps.py:{get_bearer_token,get_identity,require_owner_or_admin}` — the TENANT auth chain. Ops surface needs a PEER chain, NOT these (tenant JWT must never grant cross-tenant).
- `apps/gateway/src/gateway/tenants/infrastructure/jwt_service.py:JwtTokenService.decode` — HS256, required claims `[sub, tenant_id, role, email, exp, iat, iss]`, issuer=`settings.jwt_issuer`, key=`settings.jwt_secret`. The model the ops verifier mirrors with a SEPARATE issuer + key.
- `apps/gateway/src/gateway/main.py:create_app` — composition root; binds `app.state.token_service`. New ops verifier bound here.

Context (working folder):
- `/internal/*` family (`internal_router`, `/internal/health|metrics`, `POST /internal/catalog/sync`) — **carries ZERO app-level auth today; "Envoy guards `/internal/*` at the edge (no auth in MVP)".** Edge restriction is infra-only; no app check confirms a caller came through a restricted path. Relevant precedent for "edge-restricted path" — but ops-reconciliation reads cross-tenant billing data, so it needs APP-LEVEL auth, not edge-only.
- `apps/gateway/src/gateway/core/config.py:Settings` (env prefix `GATEWAY_`) — `jwt_secret` (dev default forbidden outside dev by `_forbid_dev_secret_outside_dev`), `jwt_issuer="ai-proxy"`, `jwt_ttl_seconds`, `oidc_*`. No `ops_*`/`operator_*` fields exist.

Honors (patterns / conventions):
- Clean Architecture (CONVENTIONS.md): domain ← application ← infrastructure ← api, inward-only. New ops-auth dependency → `*/api/*deps.py`; new verifier → `tenants/infrastructure/`; route → new ops router; reuse `reconcile_window` in `application/`.
- #1 invariant (PROJECT.md): "every query is tenant-scoped." This task is the ONE named, audited exception — `tenant_id=None`. Must be conscious + tested (tenant JWT → 403; ops creds → all-tenants).
- IO design-for-failure (CONVENTIONS.md): read-only DB call needs a timeout; if ops-auth ever fetches a key/JWKS, it needs timeout/cache/fallback.
- Errors: `ERR_<DOMAIN>_<REASON>` RFC 9457 problem+json; security failure paths byte-identical across modes, dedicated tests; TDD red-before-green; 80% floor.

Anchors the contract cites: `reconcile_window` (tenant_id=None) · `ReconciliationSummary` · `ReconciliationResponse` (or an operator variant) · a NEW ops-auth dependency (peer to `require_owner_or_admin`) · a NEW ops JWT verifier (peer to `JwtTokenService`, separate issuer+key) · NEW `Settings` ops knobs (`ops_jwt_secret`/`ops_jwt_issuer` per the `jwt_*` convention) · a NEW route `GET /ops/reconciliation` (or `/internal/...`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Operator-wide (cross-tenant) reconciliation read behind a dedicated ops-auth surface — a platform operator reads drift / unbilled-upstream across ALL tenants; no tenant credential can reach it.

Framings weighed: **mTLS client certificate** (CHOSEN — Tin 2026-06-22; operator presents a client cert, validated by Envoy at the TLS layer, identity forwarded to the app via the `x-forwarded-client-cert` / XFCC header; no app JWT) · separate ops-JWT own issuer+signing key (rejected — shared secret lets the gateway both sign and verify, wider custody blast-radius) · `role=operator` claim on the tenant JWT (rejected — cross-tenant power on a tenant-mintable token; one signup-path bug = full escalation) · edge-only `/internal/*` with no app check (rejected — no app-level proof at all)

Must:
<must>
  - Given a request carrying a VALID operator client cert (mTLS-validated by Envoy, forwarded as a trusted XFCC header with the expected operator identity — CN/SAN/fingerprint), GET the cross-tenant reconciliation: invoke `reconcile_window(session, from, to, tenant_id=None)` for the global aggregate AND a new read-only per-tenant aggregate (GROUP BY tenant_id) over the window.
  - The read is READ-ONLY — no `usage_records` write, no ledger mutation (mirrors the v29 tenant endpoint).
  - The DB aggregates run under a bounded timeout (IO design-for-failure; read-only ⇒ no retry needed).
  - The app trusts the XFCC header ONLY on the edge-restricted `/ops/*` path AND only because Envoy is configured to STRIP/OVERWRITE any client-supplied XFCC (forward_only/sanitize) — a tenant cannot spoof it. This Envoy control is a FROZEN security requirement of this contract (documented in release/edge config).
  - Ops-auth is DEFAULT-OFF and fail-closed: when no operator cert identity is configured (no expected CN/fingerprint set), the surface authenticates no one (every call denied — see Reject).
  - Security failure paths return byte-identical problem+json across all auth-failure modes (no oracle: missing cert vs unrecognised cert vs not-configured are indistinguishable).
</must>
Reject:
<reject>
  - A valid TENANT JWT (ANY role, incl. owner/admin) presented to the ops endpoint with NO operator cert -> "ERR_OPS_FORBIDDEN" (403) — explicit "wrong credential type"; the dep recognises a valid tenant token and denies it distinctly.
  - Missing XFCC / unrecognised operator identity / malformed XFCC -> "ERR_OPS_UNAUTHORIZED" (401), byte-identical (no leak of which check failed).
  - Ops-auth not configured (no expected operator identity set) -> every call -> "ERR_OPS_UNAUTHORIZED" (401) (fail-closed; grants no one until provisioned).
  - Invalid window params (bad `window`/`start`/`end`) -> reuse the existing reconciliation param validation -> "ERR_USAGE_INVALID_WINDOW" (400).
</reject>
After:
<after>
  - An authorized operator has read cross-tenant + per-tenant drift / unbilled_upstream_cost for the window; the tenant-isolation invariant for every tenant-facing endpoint is unchanged; zero ledger rows written. NOTE (edge-trust caveat inherent to mTLS-behind-Envoy): the cryptographic auth is at the TLS layer (Envoy); the app verifies the forwarded XFCC identity and trusts it ONLY because Envoy strips client-supplied XFCC on the edge-restricted path.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The XFCC trust boundary holds operationally — i.e. Envoy is/will-be configured to STRIP inbound client-supplied `x-forwarded-client-cert` and inject only its own validated value, and `/ops/*` is reachable ONLY through that Envoy listener. Lowest confidence because it is an INFRA control outside this repo's test reach (ASGI tests simulate XFCC by setting the header); if wrong: a tenant who can reach the app directly (bypassing Envoy) or inject XFCC could forge operator identity → full cross-tenant read. Mitigation frozen into the contract + release/edge steps; app stays fail-closed when identity unconfigured.
  - [x] Auth model = mTLS client cert (Tin 2026-06-22). · [x] Path = `/ops/reconciliation`. · [x] Denial = tenant-JWT→403, else→401. · [x] Response = global + per-tenant breakdown.
  - [ ] Operator identity match key: cert CN vs SAN vs SHA-256 fingerprint (allow-list). Leaning a configurable allow-list of fingerprints (rotation-friendly, exact). Confirm at build.
  - [ ] Cert ISSUANCE/ROTATION + Envoy mTLS listener config are OUT OF SCOPE for this task (provisioned out-of-band / release-step). This task VERIFIES the forwarded identity + ships the app surface only.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: operator reads cross-tenant + per-tenant drift
  Given usage_records exist for two different tenants in the window
  And a request carrying a recognised operator cert identity via XFCC
  When the operator GETs /ops/reconciliation for that window
  Then the response 200s with global totals equal to reconcile_window(tenant_id=None)
  And by_tenant lists a drift row for each of the two tenants

Scenario: operator read writes nothing
  Given usage_records exist across tenants
  And a recognised operator cert identity via XFCC
  When the operator GETs /ops/reconciliation
  Then the response 200s
  And the usage_records row count and every cost_usd/provider_cost is unchanged   # read-only

Scenario: tenant owner JWT is denied distinctly
  Given a valid TENANT JWT with role=owner and NO operator cert
  When it is presented to /ops/reconciliation
  Then the response is 403 "ERR_OPS_FORBIDDEN"
  And no cross-tenant data is returned   # tenant power never reaches ops

Scenario: missing or unrecognised or malformed operator identity
  Given (a) no XFCC, (b) XFCC with an unrecognised cert identity, (c) a malformed XFCC (three cases)
  When each is presented to /ops/reconciliation
  Then each response is 401 "ERR_OPS_UNAUTHORIZED" with a byte-identical body
  And no cross-tenant data is returned   # no oracle on which check failed

Scenario: ops-auth not configured (default-OFF, fail-closed)
  Given no expected operator identity is configured
  And a request carrying an otherwise-well-formed operator XFCC
  When it is presented to /ops/reconciliation
  Then the response is 401 "ERR_OPS_UNAUTHORIZED"
  And no cross-tenant data is returned   # grants no one until provisioned

Scenario: invalid window params
  Given a recognised operator cert identity via XFCC
  When the operator GETs /ops/reconciliation with an unparseable window/start/end
  Then the response is 400 "ERR_USAGE_INVALID_WINDOW"
  And no cross-tenant data is returned
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
GET /ops/reconciliation   query: { window?: "day"|"week"|"month" (default "month"), start?: ISO8601, end?: ISO8601 }
  Auth: mTLS operator client cert -> Envoy validates -> forwarded as `x-forwarded-client-cert` (XFCC). NOT a tenant JWT.
  200 -> OperatorReconciliationResponse {     # global aggregate + per-tenant breakdown
           window_from: str, window_to: str,
           provider_cost_total: str, billed_total: str, drift: str,
           unbilled_upstream_cost: str, unbilled_rows: int,
           catalog_billed_total: str,
           by_source: [ { usage_source: str, provider_cost: str, rows: int } ],
           by_tenant: [ { tenant_id: str, provider_cost_total: str, billed_total: str,
                          drift: str, unbilled_upstream_cost: str, unbilled_rows: int } ]
         }
  422 -> problem+json { type, title, status:422, code:"ERR_PAYLOAD_INVALID" }   # reuse existing _compute_window_bounds validation (corrected from the illustrative ERR_USAGE_INVALID_WINDOW pre-test 2026-06-22 to match the sibling /admin/reconciliation exactly — the binding §1 instruction is "reuse existing")
  401 -> problem+json { type, title, status:401, code:"ERR_OPS_UNAUTHORIZED" }   # missing|unrecognised|malformed XFCC | not-configured — BYTE-IDENTICAL
  403 -> problem+json { type, title, status:403, code:"ERR_OPS_FORBIDDEN" }       # a valid TENANT JWT (no operator cert) presented to the ops surface

Auth model (the frozen security shape — Tin-approved 2026-06-22):
  - mTLS client cert: Envoy terminates TLS, validates the operator client cert against the ops CA, and forwards the verified identity via XFCC. The app does NOT terminate TLS itself.
  - App-side verification: OpsCertVerifier.identify(xfcc_header) -> OpsIdentity(subject) | None, in tenants/infrastructure/ (peer to JwtTokenService). Matches the cert identity against a configured allow-list (Settings.ops_cert_fingerprints, default "" = OFF). Parses the XFCC field (Hash=/Subject=/SAN per Envoy XFCC grammar).
  - TRUST BOUNDARY (frozen security requirement): the app accepts XFCC ONLY on the edge-restricted /ops/* path, trusting it ONLY because Envoy is configured to STRIP/OVERWRITE any client-supplied XFCC (sanitize) and /ops/* is reachable solely via that Envoy listener. -> release/edge steps + verify SEMANTIC check.
  - DEFAULT-OFF / fail-closed: ops_cert_fingerprints == "" => verifier returns None for everyone => 401.
  - New dependency require_ops in a new ops/api/deps.py (peer to require_owner_or_admin): (1) read XFCC -> if it identifies a configured operator -> allow; (2) else if a valid tenant JWT is present (decode via existing token_service succeeds) -> 403 ERR_OPS_FORBIDDEN; (3) else -> 401 ERR_OPS_UNAUTHORIZED (byte-identical).
  - Route GET /ops/reconciliation on a NEW ops_router (prefix "/ops"), registered in main. Calls reconcile_window(tenant_id=None) for global totals + a NEW read-only reconcile_by_tenant(session, from, to) (GROUP BY tenant_id) for by_tenant.
  - DB reads under a bounded timeout (asyncio.timeout — mirror existing read pattern); read-only, no retry.

Schema: usage_records — READ-ONLY. Global = the two existing SELECTs in reconcile_window (tenant_id clause omitted). by_tenant = a NEW SELECT ... GROUP BY tenant_id (read-only). No new tables, NO migration. New Settings field only: ops_cert_fingerprints (CSV allow-list of SHA-256 cert fingerprints; "" = OFF).
```

Status: FROZEN @ v1 — approved by Tin (2026-06-22). Security model: Envoy mTLS + XFCC (edge-trust, Envoy strips client-supplied XFCC) · /ops/reconciliation · tenant-JWT→403 / else→byte-identical 401 · global + per-tenant breakdown · fingerprint allow-list default-OFF fail-closed. Changing this = change request back to SPECIFY.

Least-sure flag surfaced at freeze: [contract] the XFCC TRUST BOUNDARY is the load-bearing security assumption and lives OUTSIDE this repo's test reach — Envoy MUST strip any client-supplied `x-forwarded-client-cert` and `/ops/*` must be reachable ONLY through that Envoy listener. ASGI tests can only simulate XFCC by setting the header, so they CANNOT prove the strip; if the infra control is wrong (a tenant reaches the app directly or injects XFCC), the app would trust a forged operator identity → full cross-tenant read. Cost if wrong: complete cross-tenant billing-data disclosure. Mitigation frozen here + carried into the release/edge steps + the verify SEMANTIC check; app stays fail-closed (empty allow-list ⇒ nobody authorized) regardless.

Frozen decisions (from Tin 2026-06-22):
  1. AUTH MODEL = mTLS client cert (Envoy-validated, XFCC-forwarded). ✅
  2. DENIAL = valid tenant JWT → 403; everything else → byte-identical 401. ✅
  3. PATH = /ops/reconciliation (new app-authed /ops family, edge-restricted). ✅
  4. RESPONSE = global aggregate + per-tenant breakdown (by_tenant). ✅
Engineer-set (non-decisions, frozen into the contract):
  - XFCC trust boundary: Envoy MUST strip client-supplied XFCC; /ops/* reachable only via that listener (release/edge requirement + verify SEMANTIC).
  - Operator match = allow-list of SHA-256 cert fingerprints (Settings.ops_cert_fingerprints; "" = OFF, fail-closed).
  - Cert issuance/rotation + Envoy mTLS listener config = OUT OF SCOPE (out-of-band / release step); this task verifies the forwarded identity + ships the app surface.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (security-critical surface)
Plan (one test per scenario + focused unit tests on the new aggregate & verifier):
<test_plan>
  - test_ow1_operator_reads_cross_tenant: seed 2 tenants / GET /ops/reconciliation w/ valid XFCC fp / assert 200, global totals == reconcile_window(None), by_tenant has 2 rows
  - test_ow2_read_writes_nothing: seed rows / valid XFCC GET / assert 200 + usage_records count & every cost unchanged (read-only)
  - test_ow3_tenant_jwt_forbidden: valid OWNER tenant JWT, NO XFCC / GET / assert 403 ERR_OPS_FORBIDDEN + no data
  - test_ow4_unauthorized_byte_identical: (a) no XFCC (b) unrecognised fp (c) malformed XFCC / GET each / assert 401 ERR_OPS_UNAUTHORIZED, all three bodies byte-identical
  - test_ow5_not_configured_fail_closed: ops_cert_fingerprints="" + well-formed XFCC / GET / assert 401 ERR_OPS_UNAUTHORIZED
  - test_ow6_bad_window_422: configured + valid XFCC + window="century" / GET / assert 422 ERR_PAYLOAD_INVALID (reuse existing)
  - test_ow7_reconcile_by_tenant_groups: unit — seed 2 tenants / reconcile_by_tenant(session,from,to) / assert one row per tenant w/ correct drift (GROUP BY)
  - test_ow8_verifier_matches_fingerprint: unit — OpsCertVerifier({fp}).identify("Hash=fp;...")→OpsIdentity; identify(None)/unknown/garbage→None; empty allow-list→None (fail-closed)
</test_plan>

Tests live in: `apps/gateway/tests/operator_wide_reconciliation/`  ·  MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/ops/` `apps/gateway/src/gateway/usage/application/reconciliation.py` `apps/gateway/src/gateway/tenants/infrastructure/ops_cert_verifier.py` `apps/gateway/src/gateway/usage/api/schemas.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/core/error_catalog.py` `apps/gateway/src/gateway/main.py`
Strategy (ordered batches): 1. config knob `ops_cert_fingerprints` (default "" = OFF) + error specs OPS_UNAUTHORIZED/OPS_FORBIDDEN. 2. `OpsCertVerifier`/`OpsIdentity` (parse XFCC Hash field, match SHA-256 fingerprint allow-list, fail-closed). 3. `reconcile_by_tenant` aggregate (read-only GROUP BY tenant_id). 4. ops API package: `require_ops` dep (XFCC→allow / valid-tenant-JWT→403 / else→401) + `ops_router` GET /ops/reconciliation (bounded-timeout reads) + ops response schema. 5. register ops_router in main.
Safety rule (feature-specific): the cross-tenant read is the ONE audited tenant-scope exception — it MUST sit behind require_ops (no tenant JWT path reaches reconcile_window(tenant_id=None)); reads are READ-ONLY under a bounded timeout; verifier fail-closed when allow-list empty.
Code lives in: `apps/gateway/src/gateway/`
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

- [x] all tests pass — 9/9 ops suite green; FULL suite 1303 passed (single-process, --ignore=tests/edge), no regression
- [x] coverage did not decrease — new code is test-covered (endpoint scenarios + 2 unit tests); 90% target met for the new surface
- [x] no test or contract was altered during build — tests STRENGTHENED post-refute (added bad-Bearer→401, OW5 byte-identity, per-tenant unbilled + sum-to-global, OW9 catalog), then RE-CROSSED tests→build to re-baseline (no weakening; contract untouched)
- [x] the green was EARNED — adversarial refute-read (sonnet) verdict UPHELD 0.87, ZERO blockers, no implementation bugs; explicitly verified: no auth bypass (chain/empty-hash/case), fail-closed holds, byte-identical 401, 403 only for a genuinely valid tenant JWT (expired/garbage→401), read-only. The 3 EARNED-GAPs it raised were test-coverage gaps — all closed by strengthening + re-cross
- [x] concurrency / timing safe — endpoint is READ-ONLY (two SELECT-only aggregates), wrapped in `asyncio.timeout(30s)` (IO design-for-failure; read-only ⇒ no retry); no shared mutable state
- [x] no exposed secrets / injection — SQL is parameterized text() (bound :from/:to, no value interpolation); fingerprints are PUBLIC cert hashes (not secrets); ops_cert_fingerprints default "" = OFF; no new dependencies
- [x] layering follows CONVENTIONS.md — domain-free verifier in infrastructure/, dep+router+schema in ops/api/, reuse of application/reconcile_window+reconcile_by_tenant; the one cross-module reuse (`_compute_window_bounds`) is contract-mandated and annotated
- [ ] a person reviewed and approved the change — **PENDING Tin (risk:high human gate)**

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `ops_router` registered in `main.create_app` (include_router); `require_ops`→`get_ops_cert_verifier`→`OpsCertVerifier.from_settings` chain wired; route calls `reconcile_window(tenant_id=None)` + new `reconcile_by_tenant`; all referenced by the green suite over real HTTP
- [x] DEAD-CODE (code) — no orphans: every new symbol (OpsIdentity, OpsCertVerifier, require_ops, get_ops_cert_verifier, ops_router, OperatorReconciliationResponse, TenantReconciliationItem, reconcile_by_tenant, TenantReconciliation, OPS_UNAUTHORIZED, OPS_FORBIDDEN, ops_cert_fingerprints) is referenced (ruff/pyright clean)
- [x] SEMANTIC (security) — read the auth path in full: the cross-tenant `tenant_id=None` read is reachable ONLY behind `require_ops`; no tenant-JWT path reaches it. The XFCC TRUST BOUNDARY (Envoy strips client-supplied XFCC; /ops/* only via Envoy) is the load-bearing infra control OUTSIDE app test reach — carried into the Release steps as a hard requirement; app stays fail-closed regardless. NIT (observe): XFCC parsing reads leaf-only Hash; a Subject-before-Hash ordering with a comma'd DN would fail CLOSED (deny legit operator), never open.

### GATE RECORD
Outcome: PASS   (pending Tin's human approval — risk:high)
If RISK-ACCEPTED -> owner: — · ticket: — · expires: —   (N/A; no risk accepted — no security gap, the XFCC boundary is a release requirement not an accepted gap)
Reviewed by: Tin Dang · date: 2026-06-22

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of 401 ERR_OPS_UNAUTHORIZED vs 403 ERR_OPS_FORBIDDEN on /ops/* (a spike in either = probing or misconfig); /ops/reconciliation latency (cross-tenant aggregate over the whole ledger — watch for slow scans as data grows); count of 200s (should be ~0 until an operator is provisioned).

### Spec delta
- [SPEC · open] Envoy EDGE CONFIG is the load-bearing security control: strip any client-supplied `x-forwarded-client-cert` + restrict `/ops/*` to the Envoy mTLS listener only (the frozen XFCC trust boundary — RELEASE/infra requirement, NOT app code; the app is verify-only and stays fail-closed regardless). MUST land before any operator key is provisioned.
- [SPEC · open] operator-VIEW dashboard UI for /ops/reconciliation (the milestone's eventual surface; deferred — needs ops-auth/mTLS in a browser/operator context, a separate design).
- [SPEC · open] cert ISSUANCE/ROTATION tooling + ops CA provisioning (out-of-band; out of this task's scope by contract).
- [SPEC · open] XFCC parser robustness: currently reads leaf-only `Hash` and fails CLOSED on Subject-before-Hash ordering or a comma'd DN (refute NIT) — harden to quote-aware parsing if real Envoy output ever orders Subject before Hash (today: deny-not-bypass, acceptable).
- [SPEC · open] operator-wide periodic drift EXPORT/alert (cross-tenant analogue of the v29 tenant drift-alert) — likely a future v31+ task.
- [SPEC · open] index for the cross-tenant aggregate: as the ledger grows, `reconcile_by_tenant`'s GROUP BY over `created_at ∈ window AND cost_basis='provider'` may want a supporting index (watch latency first).

### Competency deltas
- [SDD · open] mTLS behind a reverse proxy = an XFCC EDGE-TRUST model: the app can only be the verify-half (match the forwarded fingerprint); the cryptographic check + the anti-spoof strip live in Envoy. Capture this as the standard shape for any future operator/edge-authed surface — freeze the strip+path-restriction as a release requirement, keep the app fail-closed (evidence: this task's §3 trust boundary + Least-sure flag).
- [TDD · open] for a security surface, the refute-read's EARNED-GAPs were COVERAGE not bugs; each security invariant needs its OWN explicit guard test — byte-identical 401 (incl. the invalid-Bearer→401 oracle case), the 403/401 denial split, and fail-closed default-OFF (evidence: refute UPHELD 0.87 → 4 strengthening asserts added → re-cross).
- [ADD · open] a frozen contract's ILLUSTRATIVE literal (ERR_USAGE_INVALID_WINDOW/400) was corrected PRE-TEST to the binding "reuse existing" reality (422 ERR_PAYLOAD_INVALID) and annotated in §3 — a clarification caught at test-writing is legitimate (not a contract-weakening), as long as it moves toward the contract's own stated intent (evidence: §3 correction note 2026-06-22).
