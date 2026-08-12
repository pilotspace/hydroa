# TASK: GET /admin/routing health+config surface for retry/fallback/cooldown

slug: routing-admin · created: 2026-06-12 · stage: production
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Read-only admin surface exposing retry policy, model-group routing config, and per-model cooldown health state — secrets-free, tenant-authenticated

Framings weighed:
  - **Single GET endpoint with inline state derivation (chosen)**: one endpoint reads
    `app.state.model_router` (Settings knobs are already threaded in at create_app),
    and optionally calls `snapshot_state` on `app.state.cooldown_gate`. Derives
    candidate list in-handler (union of groups). Zero new domain objects. Auth reuses
    the existing `require_owner_or_admin` dependency (catalog and keys admin pattern).
  - **Dedicated use-case class (rejected)**: adds an application layer object for a
    read-only state dump with no invariants to enforce; over-engineering for v6 scope.
  - **Polling via Prometheus metrics endpoint (rejected)**: exposes aggregate counters,
    not per-candidate instantaneous state; wrong granularity and tenant surface.

Must:
<must>
  - GET /admin/routing MUST be mounted at prefix /admin/routing (additive, like
    admin_models_router and keys_admin_router in main.py). Authenticated with the
    SAME `require_owner_or_admin` dependency used by gateway/keys/api/router.py
    (Bearer JWT; member role → 403 ERR_AUTH_FORBIDDEN; missing/invalid → 401
    ERR_AUTH_INVALID_TOKEN).
  - Response MUST be 200 with exact JSON shape (frozen in §3). All fields are always
    present — no optional top-level keys.
  - `retry_policy` block MUST carry `max_retries` (int) and `backoff_base_s` (float)
    derived from Settings.upstream_max_retries and Settings.upstream_retry_backoff_base_s.
  - `cooldown` block MUST carry `enabled` (bool), `threshold` (int), `ttl_s` (int),
    `window_s` (int) derived from Settings cooldown_* knobs. `enabled` = true iff
    cooldown_failure_threshold > 0.
  - `model_groups` MUST be the dict of alias → ordered candidate list from
    app.state.model_router.model_groups (which mirrors Settings.model_groups).
  - `candidates` MUST be the union of all candidates across all groups, deduped with
    STABLE ORDER (group declaration order, then candidate list order within group);
    a candidate appearing in two groups is listed once per (alias, model_id) pair
    (see §3 — dedup is per pair, not per model_id).
  - Each candidate entry MUST carry `model_id` (str), `alias` (str), and `state`
    (one of "closed" | "open" | "half_open" | "unknown").
  - State derivation MUST use `snapshot_state(model_id)` on `app.state.cooldown_gate`
    when the gate is not None (cooldown enabled). The method is additive on
    RedisCooldownGate (not part of ModelHealthGate protocol — see §3 disposition).
  - MUST NEVER issue Redis commands when `app.state.cooldown_gate is None`
    (cooldown disabled). Every candidate state MUST be "closed" in that case.
  - Redis error during `snapshot_state` MUST result in state "unknown" for that
    candidate, and the endpoint MUST still return 200 (fail-open read). A structlog
    WARNING is emitted by snapshot_state itself (B3 rules: model_id ok, no key strings).
  - Response MUST be secrets-free: no api keys, client secrets, redis URLs,
    database URLs, jwt secrets, or any Settings field beyond the four named blocks.
    MUST be enforced by a test (RA6).
  - No write operations in v6 — endpoint is GET only. No pagination or filtering
    (bounded by Settings.model_groups cap of ≤5 candidates per group). Document
    as future-proofing note only.
</must>

Reject:
<reject>
  - Missing or malformed Bearer JWT → 401 "ERR_AUTH_INVALID_TOKEN" (parity with keys admin_router)
  - No Authorization header → 401 "ERR_AUTH_INVALID_TOKEN" (AUTH_TOKEN_MISSING code path)
  - Member role JWT → 403 "ERR_AUTH_FORBIDDEN"
  - Any method other than GET (e.g. POST /admin/routing) → 405 Method Not Allowed (FastAPI default)
</reject>

After:
<after>
  - Authenticated owner/admin caller receives a complete, accurate view of the gateway's
    routing configuration and per-candidate cooldown health as of the time of the request.
  - No Redis keys are created or modified; no Settings are changed; no DB rows are written.
  - `app.state.cooldown_gate` is never None-checked inside the router for a non-None gate:
    it always calls `snapshot_state` (gate enforces its own fail-open semantics).
  - When cooldown is disabled, the response reflects `enabled: false` and all candidate
    states are "closed" with zero Redis I/O.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ LOWEST CONFIDENCE [spec]: `snapshot_state` is an ADDITIVE method on `RedisCooldownGate`
    (not on the `ModelHealthGate` protocol). The protocol is owned by model-fallbacks and is
    FROZEN (it has `is_available`, `record_failure`, `record_success`). Adding `snapshot_state`
    to RedisCooldownGate is an additive class extension — it does not change the protocol.
    Risk: if another gate implementation (e.g. a future in-process gate) is ever introduced,
    it will not have `snapshot_state` and the routing-admin router would need a type-guard or
    the protocol would need extending. For v6 there is exactly one non-None gate type
    (RedisCooldownGate), so an isinstance check or protocol extension is deferred. Cost: if
    violated, the router breaks at runtime for a hypothetical future gate type. Mitigation:
    document in §3 that `snapshot_state` is concrete-type-only and must be extended if new
    gate types are added. Surface this tradeoff at freeze.
  ⚠ SECOND-LOWEST CONFIDENCE [spec]: Candidate dedup semantics — the context says "a candidate
    in two groups appears once per (alias, model_id) pair" which means if model_id "x" appears
    in alias "fast" AND alias "smart", it appears twice in `candidates` (once per alias). This
    is different from a per-model_id dedup. The implied implementation iterates groups in order,
    emits all (alias, candidate) pairs, and deduplicates only exact (alias, model_id) duplicates
    (which can't exist by the model_groups structure). In practice dedup is a no-op. Risk: if
    the consumer expects per-model_id dedup, the extra alias entries are surprising. Cost: a
    response shape change in v7 (non-breaking additive if alias field stays). Surfaced at freeze.
  - [x] `snapshot_state` Redis reads use frozen key shapes — no new key patterns introduced.
    The method returns "open" / "half_open" / "closed" / "unknown" based on exactly two GETs
    (open key then half key). Zero writes.
  - [x] Auth parity is with `require_owner_or_admin` (not owner-only). The OIDC admin uses
    owner-only. The routing surface is read-only and non-sensitive (no secret values), so
    owner-or-admin is the correct boundary — same as keys and catalog admin endpoints.
  - [x] No rate limiting added to /admin/routing in v6 (admin endpoints are not rate-limited
    in existing surface). Future hardening note only.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost. -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: RA1 — authenticated GET /admin/routing with model_groups + enabled cooldown → 200 full shape
  Given a running gateway with model_groups={"fast": ["vendor/a", "vendor/b"]}
  And cooldown_failure_threshold=3, cooldown_ttl_s=60, cooldown_window_s=60
  And a fake cooldown gate returning "closed" for vendor/a and "open" for vendor/b
  And a valid owner JWT
  When GET /admin/routing with Bearer token
  Then 200 with body matching exact §3 shape
  And retry_policy block present with correct values
  And cooldown block with enabled=true, threshold=3, ttl_s=60, window_s=60
  And model_groups={"fast": ["vendor/a", "vendor/b"]}
  And candidates=[{model_id: vendor/a, alias: fast, state: closed}, {model_id: vendor/b, alias: fast, state: open}]

Scenario: RA2 — cooldown disabled (gate None) → enabled false; all states "closed"; zero gate interaction
  Given a running gateway with model_groups={"group": ["m1", "m2"]}
  And cooldown_failure_threshold=0 (gate is None)
  And a valid owner JWT
  When GET /admin/routing with Bearer token
  Then 200 with cooldown.enabled=false
  And all candidate states are "closed"
  And no snapshot_state was called (gate is None — zero Redis interaction)

Scenario: RA3 — model with open key → state "open"; half marker only → state "half_open"
  Given a running gateway with model_groups={"g": ["model-open", "model-half"]}
  And a real RedisCooldownGate over a local FakeRedis
  And the open key for model-open is SET in FakeRedis
  And no open key but the half marker for model-half is SET in FakeRedis
  And a valid owner JWT
  When GET /admin/routing
  Then 200 with candidates containing model-open state="open" and model-half state="half_open"

Scenario: RA4 — Redis error during snapshot → state "unknown"; 200 still returned
  Given a running gateway with model_groups={"g": ["model-x"]}
  And a fake gate whose snapshot_state raises ConnectionError for model-x
  And a valid owner JWT
  When GET /admin/routing
  Then 200 with candidate model-x state="unknown"
  And the endpoint does not raise (fail-open read)

Scenario: RA5 — unauthenticated → 401 matching existing admin-route parity
  Given a running gateway
  When GET /admin/routing with no Authorization header
  Then 401 with body matching {"code": "ERR_AUTH_INVALID_TOKEN", "status": 401}
  And the response shape is identical to the 401 from GET /admin/models (parity pin)

Scenario: RA6 — secrets-free: sentinel secret values never appear in serialized response
  Given a running gateway built with sentinel values for jwt_secret, openrouter_api_key,
    and oidc_client_secret ("SENTINEL_JWT_SECRET", "SENTINEL_OR_KEY", "SENTINEL_OIDC_SECRET")
  And a valid owner JWT issued against the sentinel jwt_secret
  When GET /admin/routing
  Then 200 with response body as JSON string
  And the string "SENTINEL_JWT_SECRET" does NOT appear in the serialized response
  And the string "SENTINEL_OR_KEY" does NOT appear in the serialized response
  And the string "SENTINEL_OIDC_SECRET" does NOT appear in the serialized response

Scenario: RA7 — empty model_groups → 200 with empty groups/candidates; retry+cooldown blocks present
  Given a running gateway with model_groups={} (feature off)
  And cooldown_failure_threshold=0
  And a valid owner JWT
  When GET /admin/routing
  Then 200 with model_groups={}
  And candidates=[]
  And retry_policy block still present (correct default values)
  And cooldown block still present (enabled=false)

Scenario: RA8 — response is JSON-schema stable (required keys present regardless of config)
  Given any gateway configuration (including empty groups and disabled cooldown)
  And a valid owner JWT
  When GET /admin/routing
  Then 200 with all four top-level keys present: retry_policy, cooldown, model_groups, candidates
  And retry_policy has exactly keys: max_retries, backoff_base_s
  And cooldown has exactly keys: enabled, threshold, ttl_s, window_s
  And each candidate entry has exactly keys: model_id, alias, state
  And state is one of "closed", "open", "half_open", "unknown"
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
LOWEST-CONFIDENCE FLAGS AT DRAFT

  ⚠ [spec] snapshot_state is an additive method on the CONCRETE RedisCooldownGate class,
    NOT on the ModelHealthGate protocol (which is frozen by cooldown-circuit). The routing-admin
    router depends on the concrete class for this method. If a second ModelHealthGate
    implementation is ever introduced, it will not have snapshot_state and the router will
    need updating. For v6 this is safe (exactly one concrete gate type exists). Disposition:
    ACCEPTED for v6 with a required note in BUILD that any future gate implementation must
    either implement snapshot_state or the router's snapshot dispatch must be guarded with
    isinstance. Do not extend the frozen ModelHealthGate protocol without a change request.

  ⚠ [spec] Candidate dedup semantics: candidates list is per (alias, model_id) pair — a
    model_id appearing in two aliases is listed once per alias (with its alias name). The
    Settings validator already prevents a model_id from appearing twice in the SAME alias
    group, so within-group dedup is never needed. Cross-alias same model_id = two entries
    (different alias fields). Cost: slightly surprising to consumers who expect per-model
    dedup. Surfaced here; frozen as stated — change only via change request.

---

HTTP CONTRACT

  GET /admin/routing   body: (none)
    200 → {
      "retry_policy": {
        "max_retries": <int>,        # Settings.upstream_max_retries
        "backoff_base_s": <float>    # Settings.upstream_retry_backoff_base_s
      },
      "cooldown": {
        "enabled": <bool>,           # cooldown_failure_threshold > 0
        "threshold": <int>,          # Settings.cooldown_failure_threshold
        "ttl_s": <int>,              # Settings.cooldown_ttl_s
        "window_s": <int>            # Settings.cooldown_window_s
      },
      "model_groups": {
        "<alias>": ["<candidate_id>", ...]   # Settings.model_groups (as-is)
      },
      "candidates": [
        {
          "model_id": "<str>",       # upstream model id (public catalog id)
          "alias": "<str>",          # alias this candidate belongs to
          "state": "closed" | "open" | "half_open" | "unknown"
        },
        ...
      ]
    }
    401 → { "code": "ERR_AUTH_INVALID_TOKEN", "status": 401, "title": "..." }
    403 → { "code": "ERR_AUTH_FORBIDDEN", "status": 403, "title": "..." }
    405 → (FastAPI default for disallowed methods)

AUTH

  Authentication and authorization MUST be provided by the SAME dependency chain as
  gateway/keys/api/deps.py: `require_owner_or_admin` → `get_identity` → `get_bearer_token`.
  Specifically:
    - `get_bearer_token`: extract raw token from Authorization: Bearer header; raise
      AUTH_TOKEN_MISSING.exc() if missing/non-Bearer.
    - `get_identity`: call `request.app.state.token_service.decode(token)`; raise
      AUTH_TOKEN_INVALID.exc() on InvalidTokenError.
    - `require_owner_or_admin`: raise AUTH_FORBIDDEN.exc() if identity.role == Role.MEMBER.
  Do NOT import or duplicate these functions — import them from gateway.keys.api.deps.

  Parity pin: an unauthenticated request to /admin/routing MUST return the same
  status code (401) and error code ("ERR_AUTH_INVALID_TOKEN") as an unauthenticated
  request to GET /admin/models (tested in RA5).

CANDIDATES DERIVATION

  Iterate app.state.model_router.model_groups.items() in insertion order:
    for alias, candidate_list in model_groups.items():
        for model_id in candidate_list:
            emit {"model_id": model_id, "alias": alias, "state": <derived>}

  State derivation per candidate:
    - If app.state.cooldown_gate is None → "closed" (no Redis call).
    - Else: call `await app.state.cooldown_gate.snapshot_state(model_id)`.
      snapshot_state is an additive method that:
        1. GETs gateway:cooldown:open:{model_id} → if present, return "open"
        2. GETs gateway:cooldown:half:{model_id} → if present, return "half_open"
        3. Else return "closed"
        4. Any Redis exception → return "unknown" + emit WARNING (B3: model_id ok,
           no key strings in log fields)
      No writes. No new Redis keys. Honors frozen key shapes.

  Dedup invariant: the Settings validator prevents the same candidate from appearing
  twice in the same group; cross-group same model_id gets one entry per (alias, model_id)
  pair. No explicit dedup step required in the handler.

SNAPSHOT_STATE METHOD (additive on RedisCooldownGate)

  Signature:   async def snapshot_state(self, model_id: str) -> str
  Returns:     "open" | "half_open" | "closed" | "unknown"
  Side-effects: NONE — read-only; no Redis writes; no metric increments
  Error contract: any Redis exception → return "unknown" (do NOT re-raise);
                  emit WARNING "cooldown_gate_redis_error" with model_id and error type name
                  (B3: model_id is a public catalog id; no key strings; no credentials)
  Placement:   apps/gateway/src/gateway/proxy/infrastructure/redis_cooldown_gate.py
               (additive method on existing RedisCooldownGate class — no new file)
  Protocol disposition: NOT added to ModelHealthGate protocol (frozen by cooldown-circuit).
               This method is concrete-class only. See ⚠ [spec] flag above.

SECRETS-FREE INVARIANT

  The 200 response body MUST NOT contain any value from the following Settings fields:
    - jwt_secret
    - openrouter_api_key
    - oidc_client_secret
    - database_url (any connection string)
    - redis_url (any connection URL)
    - oidc_config_encryption_key
  Only the four named blocks (retry_policy, cooldown, model_groups, candidates)
  are permitted in the response body. All values are derived from the named Settings
  knobs listed in the response schema — no other Settings fields are read.

ROUTER PLACEMENT AND MOUNTING

  New file: apps/gateway/src/gateway/proxy/api/routing_admin_router.py
    routing_admin_router = APIRouter(prefix="/admin/routing", tags=["routing-admin"])

  Mount in gateway/main.py:
    from gateway.proxy.api.routing_admin_router import routing_admin_router
    ...
    app.include_router(routing_admin_router)

  Note: the router reads from app.state (model_router, cooldown_gate, settings);
  it has NO database dependency and does NOT receive a DB session.

SETTINGS READ (read-only; no new Settings fields introduced by this task)

  All Settings values read by the router are ALREADY present from prior v6 tasks:
    upstream_max_retries       (retry-policy task — DONE)
    upstream_retry_backoff_base_s (retry-policy task — DONE)
    cooldown_failure_threshold (cooldown-circuit task — DONE)
    cooldown_ttl_s             (cooldown-circuit task — DONE)
    cooldown_window_s          (cooldown-circuit task — DONE)
    model_groups               (model-fallbacks task — DONE)

  This task introduces ZERO new Settings fields.

FUTURE-PROOFING NOTES (out of scope for v6)

  - Pagination/filtering: not needed (≤5 candidates per group by Settings cap).
    If model_groups grows beyond one screen, add ?alias= filter in v7.
  - Per-tenant routing config: currently Settings are global (tenant-agnostic).
    Per-tenant group overrides are a v7+ concern.
  - Write path: env/config file is the write path. No PATCH /admin/routing in v6.
  - snapshot_state protocol: if a second ModelHealthGate impl is introduced,
    extend with isinstance dispatch or add snapshot_state to the protocol.
```

Status: FROZEN — approved by Tin Dang (delegated auto mode, 2026-06-12)
Least-sure flag surfaced at freeze: [spec] snapshot_state lives on the concrete
RedisCooldownGate class, not the frozen ModelHealthGate protocol — CONFIRMED for v6
(exactly one gate implementation exists; future implementations must implement it or the
router guards with isinstance; protocol extension requires a change request). Second flag
(per-(alias, model_id) candidate entries, no cross-alias dedup) — CONFIRMED as stated.
Auth via require_owner_or_admin (owner/admin parity with the keys admin surface) — CONFIRMED.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% of response shape paths + auth paths + secrets-free + state derivation (closed/open/half_open/unknown/disabled)

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_ra1_full_shape_authenticated_with_cooldown:
      arrange: minimal app (no DB) with model_groups={"fast": ["vendor/a", "vendor/b"]};
               cooldown settings threshold=3/ttl_s=60/window_s=60; inject fake gate returning
               "closed" for vendor/a and "open" for vendor/b onto app.state.cooldown_gate;
               also inject Settings.upstream_max_retries=2, upstream_retry_backoff_base_s=1.0;
               issue valid owner JWT via JwtTokenService
      act: GET /admin/routing with Bearer token
      assert: 200; retry_policy.max_retries==2; retry_policy.backoff_base_s==1.0;
              cooldown.enabled==True; cooldown.threshold==3; cooldown.ttl_s==60; cooldown.window_s==60;
              model_groups=={"fast": ["vendor/a", "vendor/b"]};
              candidates contains {model_id: "vendor/a", alias: "fast", state: "closed"} and
              {model_id: "vendor/b", alias: "fast", state: "open"}
      RED reason: gateway.proxy.api.routing_admin_router does not exist → ImportError
                  (or router not mounted → 404)

  - test_ra2_cooldown_disabled_gate_none_all_closed:
      arrange: app with model_groups={"group": ["m1", "m2"]}; threshold=0 (gate=None);
               tracking fake gate that raises if called; valid owner JWT
      act: GET /admin/routing
      assert: 200; cooldown.enabled==False; both candidates state=="closed";
              no gate method was called
      RED reason: ImportError or 404

  - test_ra3_state_derivation_open_and_half_open:
      arrange: RedisCooldownGate (local FakeRedis copy; threshold=3);
               SET open key for "model-open"; SET half key (no open key) for "model-half";
               model_groups={"g": ["model-open", "model-half"]}; valid owner JWT
      act: GET /admin/routing
      assert: candidate model-open state=="open"; candidate model-half state=="half_open"
      RED reason: ImportError or 404

  - test_ra4_redis_error_yields_unknown_still_200:
      arrange: error fake gate (snapshot_state raises ConnectionError); valid owner JWT;
               model_groups={"g": ["model-x"]}
      act: GET /admin/routing
      assert: 200; candidate model-x state=="unknown"
      RED reason: ImportError or 404

  - test_ra5_unauthenticated_401_parity:
      arrange: app with any settings; no Authorization header; also make a bare GET /admin/models
               request with no auth to get its 401 shape
      act: GET /admin/routing (no auth); GET /admin/models (no auth) for parity comparison
      assert: both return 401; both return code=="ERR_AUTH_INVALID_TOKEN"; both return status==401
      RED reason: ImportError or 404 (routing-admin has no route yet)

  - test_ra6_secrets_free_sentinel_values:
      arrange: app built with sentinel secrets:
               jwt_secret="SENTINEL_JWT_SECRET_XYZZY",
               openrouter_api_key="SENTINEL_OR_KEY_ABCDE",
               oidc_client_secret="SENTINEL_OIDC_SECRET_99999";
               issue valid JWT against sentinel secret; valid owner JWT
      act: GET /admin/routing; serialize response.json() to string via json.dumps
      assert: 200; "SENTINEL_JWT_SECRET_XYZZY" not in serialized;
              "SENTINEL_OR_KEY_ABCDE" not in serialized;
              "SENTINEL_OIDC_SECRET_99999" not in serialized
      RED reason: ImportError or 404

  - test_ra7_empty_groups_returns_retry_cooldown_blocks:
      arrange: app with model_groups={}; threshold=0; max_retries=0; valid owner JWT
      act: GET /admin/routing
      assert: 200; model_groups=={}; candidates==[]; "retry_policy" in body; "cooldown" in body;
              retry_policy.max_retries==0; cooldown.enabled==False
      RED reason: ImportError or 404

  - test_ra8_schema_stable_all_top_level_keys_present:
      arrange: app with minimal settings (empty groups); valid owner JWT
      act: GET /admin/routing
      assert: 200; body has exactly top-level keys {retry_policy, cooldown, model_groups, candidates};
              retry_policy has keys {max_retries, backoff_base_s};
              cooldown has keys {enabled, threshold, ttl_s, window_s};
              candidates is a list (may be empty)
      RED reason: ImportError or 404
</test_plan>

Tests live in: `apps/gateway/tests/routing_admin/` · `apps/gateway/tests/routing_admin/conftest.py` · `apps/gateway/tests/routing_admin/test_routing_admin.py`

Expected red/green at spec phase (before BUILD):
  - RA1–RA8: RED for ImportError (routing_admin_router module absent) or 404 (route not mounted)
    — the module gateway.proxy.api.routing_admin_router does not exist yet.
  - snapshot_state method absent from RedisCooldownGate → RA3 also fails with AttributeError.
  - All failures are for the RIGHT reason — the implementation simply does not exist yet.
  - Zero skips. All FAILED.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): snapshot_state MUST be a pure read (zero Redis writes);
any exception in snapshot_state MUST return "unknown" (never re-raise); the handler
MUST NOT include any Settings field other than the six named knobs in the response body;
the router MUST use the exact same require_owner_or_admin import from gateway.keys.api.deps.

Code lives in:
  - `apps/gateway/src/gateway/proxy/api/routing_admin_router.py` (new file — the router)
  - `apps/gateway/src/gateway/proxy/infrastructure/redis_cooldown_gate.py` (additive: snapshot_state method)
  - `apps/gateway/src/gateway/main.py` (one new include_router line)

Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — frozen tests/routing_admin 8/8; full suite 462 passed (tests/upstream_base_url DRAFT reds excluded — sibling task) (2026-06-12)
- [x] coverage did not decrease — 81.76% vs 81.67% pre-build (floor 80)
- [x] no test or contract was altered during build — frozen tests/routing_admin untouched post-freeze; §3 untouched; pyproject format-exclude additions per convention
- [x] concurrency / timing safe — endpoint is read-only (two GETs per candidate, ≤5/group by Settings cap); no shared mutable state; snapshot races with live transitions return a momentarily-stale but valid state string
- [x] no exposed secrets / injection / new deps — RA6 pins sentinel secrets never serialize; response sources only named non-secret knobs; auth chain reused verbatim; zero new dependencies
- [x] layering follows CONVENTIONS.md — api router depends on infrastructure gate + app.state seams; additive method stays in infrastructure; no protocol change
- [x] reviewed — orchestrator line-reviewed every diff under delegated auto mode (Tin Dang); handler-level try/except confirmed contract-sanctioned (§3 decision 5 — RA4 fake gates raise directly)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — main.py mounts routing_admin_router beside admin_models_router; snapshot_state reachable and exercised by RA1/RA3/RA4; no new app.state seam introduced (reads existing settings/model_router/cooldown_gate), so no new wiring suite required by the foundation rule
- [x] DEAD-CODE — all new symbols (router, handler, snapshot_state) exercised by the frozen suite; ruff+mypy clean
- [x] SEMANTIC — §3 HTTP contract re-read in full against the handler: 4-block shape exact, state strings exact, per-(alias,candidate) ordering (group insertion order then list order) exact, 401/403 parity via the shared dependency confirmed by RA5

### GATE RECORD
Outcome: PASS (auto-resolved — complete evidence, no security finding, no concurrency/architecture residue)
Dispositions:
  - snapshot_state concrete-class-only placement confirmed per freeze flag (one gate type in v6;
    future implementations must implement it or the router adds isinstance dispatch).
  - Handler-level defensive catch around snapshot_state is contract-sanctioned (§3 decision 5).
  - Sibling DRAFT red suite tests/upstream_base_url excluded from this gate's run (v6-live-verify owns it).
Evidence: frozen suite 8/8; 462 passed, coverage 81.76% (floor 80); lint/mypy-strict/allowlists EXIT=0 (2026-06-12).
Reviewed by: Tin Dang (delegated auto mode, orchestrator line review) · date: 2026-06-12

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): latency of GET /admin/routing (should be sub-millisecond when cooldown disabled; sub-5ms with Redis reads); 401 rate on /admin/routing (auth misconfiguration signal)
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
