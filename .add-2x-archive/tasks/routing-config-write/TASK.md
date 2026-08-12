# TASK: Routing config write — editable model-groups / strategy / deployment limits + /routing editor

slug: routing-config-write · created: 2026-06-23 · stage: production
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
REFOCUS (v32 task 2 of 3 — write endpoint, 2026-06-23): the sub-milestone was opened (Tin-confirmed); this task is now ONLY the WRITE ENDPOINT + extended READ shape. Persistence + boot-apply already shipped in `routing-config-store` (gate PASS): `RoutingConfigRepository.get()/upsert(config)` (singleton `routing_config` JSONB row), `merge_routing_config`, and the lifespan boot-apply (restart-to-apply). So this task needs NO new table and NO runtime hot-path reload — write persists to the repo; it takes effect on next boot (restart-to-apply, Tin-decided). Build: `PUT /admin/routing` that validates (full Settings/Deployment validator parity) → `RoutingConfigRepository.upsert` → returns the extended GET shape; and ADDITIVELY extend `GET /admin/routing` model_groups/candidates to expose per-deployment weight/rpm/tpm.
SECURITY — AUTHZ FROZEN (Tin-approved 2026-06-23, AskUserQuestion): `PUT /admin/routing` uses `require_owner_or_admin`, **always-on** (no feature flag, no /ops boundary). The role model is tenant-scoped (Role MEMBER/ADMIN/OWNER, no operator role); Tin explicitly accepted that any owner/admin may write the operator-wide routing config — the deployment is treated as single-operator/trusted-owner, consistent with `GET /admin/routing` already exposing operator-wide config to owners. This was a HARD-STOP security freeze; Tin's selection IS the approval. (Rejected: default-OFF flag; /ops mTLS+XFCC boundary — the latter would block the dashboard editor in task 3.)
ORIGINAL RECON (v31 4-task breakdown — kept for provenance; tasks 1/2 of it now shipped/in-flight):
- SOURCE OF TRUTH = `core/config.py:Settings` (pydantic-settings, `GATEWAY_` env): `deployments`(dict[str,list[Deployment]] via GATEWAY_MODEL_GROUPS; Deployment={model_id,weight>0,tpm_limit?,rpm_limit?}), `routing_strategy`(ordered|simple-shuffle|least-busy|latency), `cooldown_failure_threshold/_ttl_s/_window_s` (circuit, GLOBAL not per-deployment), `upstream_max_retries(0..5)/_retry_backoff_base_s/_retry_deadline_s/_fallback_on_error/_stream_resilience_enabled`, `loadbal_ewma_alpha/_inflight_ttl_s`. NO ORM table, NO migration for deployments/model_groups/routing_config (latest head `f4a9b3c7e8d2_alert_events`; baseline = 6 tables, none routing).
- RUNTIME = `main.py:617` builds `app.state.model_router = FallbackModelRouter(...)` ONCE at create_app from Settings; stores `_model_groups/_deployments/_strategy/_load_gate/_limit_gate` privately, NEVER re-read per request. NO refresh()/reload() exists (contrast catalog's `provider_resolver.refresh()`). → a write needs a runtime reload of the LIVE routing singleton on the request HOT-PATH (the hard, risky part) OR restart-to-apply.
- READ side = `proxy/api/routing_admin_router.py:get_routing_admin` (`GET /admin/routing`, require_owner_or_admin, read-only FROZEN) returns {retry_policy{max_retries,backoff_base_s}, cooldown{enabled,threshold,ttl_s,window_s}, model_groups{alias:[model_id]}, candidates[{model_id,alias,state}]}; reads app.state.settings + app.state.model_router.model_groups + cooldown_gate.snapshot_state. NOTE: it does NOT currently expose deployment weight/tpm/rpm — the write side must extend the read shape.
- NO write/mutation of routing config exists anywhere today (no POST/PUT/PATCH on routing_admin_router).
- DASHBOARD = `app/(dashboard)/routing/page.tsx`→`components/routing/RoutingPage.tsx` is READ-ONLY (bffGet "/admin/routing", 4 cards: retry/cooldown/model-groups/candidates; ZERO edit UI, imports only bffGet).
- CLOSEST ANALOG for the build = tenant OIDC config (`a9b3c4d5e6f7_oidc_tenant_config` migration + ORM + encrypted store + TTL-cache resolver + GET/PUT editor) — similar scope MINUS routing's extra live-reload-of-a-singleton-on-the-hot-path complexity.
WHAT A WRITABLE ROUTING CONFIG NEEDS (sub-milestone task breakdown, est. 3–4 tasks):
1. persistence: new migration + ORM `routing_config` (singleton/operator-wide row: model_groups+deployments JSONB, strategy, retry/cooldown knobs) + repository.
2. runtime reload: a safe `FallbackModelRouter.reload(config)` (or atomic app.state.model_router swap) + strategy/gate reconstruction — design-for-failure on the hot path (a bad config must NOT break live routing; validate-then-swap, keep old on failure).
3. write endpoint: `PUT /admin/routing` (owner/admin) that round-trips ALL Settings validators (empty-candidate-list, duplicate-deployment, too-many-candidates, alias-collides-with-candidate, unknown-strategy, positive weight/limits) BEFORE persist+reload.
4. dashboard editor: forms for model-groups (alias/deployment CRUD + weight/limits), strategy dropdown, retry/cooldown knobs; optimistic invalidate ["admin-routing"].
Context (working folder):
- `.add/milestones/v31/MILESTONE.md` task "routing-config-write" + criterion "An owner edits model-groups / routing strategy / deployment limits from the dashboard." This task is the LAST of v31; its size (sub-milestone) is the open re-size decision surfaced to Tin 2026-06-23.
Honors (patterns / conventions):
- CONVENTIONS.md: Clean-Arch (api→application→infrastructure), migration in BOTH orm __table_args__ AND alembic, owner/admin gate, frozen Pydantic, design-for-failure on IO + (here) on the hot-path reload.
- PROJECT.md: routing decides the request hot-path; an operator-wide config write must never break live routing (validate-then-atomic-swap; restart-to-apply is the safe fallback).
Anchors the contract cites: `Settings`(config.py: deployments/routing_strategy/cooldown_*/upstream_*/loadbal_*) · `merge_routing_config` · `RoutingConfigRepository`(get/upsert) · `get_routing_admin`/`GET /admin/routing` (extend additively) · NEW `put_routing_admin`/`PUT /admin/routing` · `require_owner_or_admin` (authz, Tin-approved always-on) · `Deployment` validators.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Routing config write endpoint — `PUT /admin/routing` (owner/admin) persists an operator-wide routing config after full Settings/Deployment validator parity; `GET /admin/routing` is additively extended to expose per-deployment weight/rpm/tpm so the write round-trips. Restart-to-apply (persisted now, live router rebuilds at next boot via the shipped boot-merge).
Framings weighed: validate the incoming body by round-tripping it through `merge_routing_config(current_settings, body)` then `RoutingConfigRepository.upsert(body)` (chosen — reuses the EXACT shipped validator path, zero duplicated validation logic, persistence already exists) · hand-rolled per-field validation in the endpoint (rejected — duplicates Settings validators, drifts from boot parity) · validate-then-live-reload the router (rejected — Tin chose restart-to-apply; no hot-path mutation).
Must:
<must>
  - PUT /admin/routing accepts a routing config document {model_groups?, routing_strategy?, cooldown_*?, upstream_*?, loadbal_*?} and, when it passes the SAME Settings/Deployment validators that run at boot, persists it via RoutingConfigRepository.upsert (replacing the singleton).
  - The successful PUT returns 200 with the post-write routing config in the SAME (extended) shape GET returns — read-after-write parity.
  - GET /admin/routing additively gains a `deployments` block exposing, per alias, each candidate's {model_id, weight, rpm_limit, tpm_limit}; the existing keys (retry_policy, cooldown, model_groups, candidates) are unchanged.
  - PUT is gated by require_owner_or_admin (owner/admin only; member → 403; missing/invalid token → 401) — same dependency as GET. (AUTHZ FROZEN: always-on, Tin-approved.)
  - An invalid config is rejected with the EXISTING validator error code and NOTHING is persisted (the prior stored config, or no-row env state, is untouched).
</must>
Reject:
<reject>
  - unknown routing_strategy -> "UNKNOWN_ROUTING_STRATEGY"
  - duplicate deployment within an alias -> "DUPLICATE_DEPLOYMENT"
  - empty candidate list for an alias -> "EMPTY_CANDIDATE_LIST"
  - more than 5 candidates in an alias -> "TOO_MANY_CANDIDATES"
  - an alias equal to one of its candidate model_ids -> "ALIAS_COLLIDES_WITH_CANDIDATE"
  - non-positive deployment weight / limit, or out-of-range scalar knob -> the existing Settings/Deployment validator code (e.g. INVALID_DEPLOYMENT_WEIGHT, INVALID_LOADBAL_ALPHA)
  - caller is a member -> 403 AUTH_FORBIDDEN ; missing/invalid bearer -> 401
</reject>
After:
<after>
  - The routing_config singleton row holds the just-PUT document; a subsequent GET /admin/routing reflects it; on the next gateway boot merge_routing_config applies it over env (DB-wins).
  - On a rejected PUT, the stored row is unchanged (or still absent) and the running router/settings are untouched (restart-to-apply means the live router is never mutated by a write regardless).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The PUT body shape == the stored config doc shape (model_groups + scalar override keys) consumed by merge_routing_config — lowest confidence because the stored doc is an internal contract from routing-config-store, and clients (the task-3 editor) must serialize deployments either as bare strings or {model_id,weight,rpm_limit,tpm_limit}; if wrong, the editor and endpoint disagree on the wire shape. Cost: a follow-up shape change. Mitigation: accept BOTH bare-string and object deployment members (Settings._coerce_deployment already does), and round-trip through merge so the response shows exactly what was stored/validated.
  - [x] merge_routing_config raises a ValueError subclass (pydantic ValidationError) carrying the validator code — confirmed: routing-config-store's test_merge_invalid_* assert `pytest.raises(ValueError, match="<CODE>")` green, so the endpoint catches ValueError → maps to 4xx with the code.
  - [x] GET extension is purely additive (no frozen-key change) — confirmed: routing-admin §3 froze the four keys; adding a 5th `deployments` key does not alter them.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: owner writes a valid routing config
  Given an owner/admin bearer token
  When they PUT /admin/routing with {model_groups:{gpt:["a","b"]}, routing_strategy:"simple-shuffle"}
  Then the response is 200 and its body shows routing_strategy "simple-shuffle" and model_groups {gpt:["a","b"]}
  And a subsequent GET /admin/routing returns the same persisted config

Scenario: PUT round-trips deployment weight/limits in the extended shape
  Given an owner/admin bearer token
  When they PUT model_groups:{gpt:[{model_id:"a",weight:3,rpm_limit:60,tpm_limit:1000}]}
  Then the 200 body's deployments block shows gpt -> [{model_id:"a",weight:3,rpm_limit:60,tpm_limit:1000}]
  And GET /admin/routing also returns that deployments block

Scenario: GET is additively extended (existing keys unchanged)
  Given any persisted-or-env routing config
  When an owner/admin GETs /admin/routing
  Then the body contains retry_policy, cooldown, model_groups, candidates AND a new deployments block
  And the four pre-existing blocks have their frozen shape

Scenario: unknown strategy is rejected, nothing persists
  Given an owner/admin bearer token and a previously-stored valid config C
  When they PUT routing_strategy:"bogus"
  Then the response is 422 and the error detail names "UNKNOWN_ROUTING_STRATEGY"
  And GET /admin/routing still returns C (nothing was persisted)

Scenario: duplicate deployment is rejected, nothing persists
  Given an owner/admin bearer token and no stored config (env only)
  When they PUT model_groups:{gpt:["a","a"]}
  Then the response is 422 and the error detail names "DUPLICATE_DEPLOYMENT"
  And no routing_config row exists (env state unchanged)

Scenario: member is forbidden
  Given a member bearer token
  When they PUT /admin/routing with any body
  Then the response is 403 ERR_AUTH_FORBIDDEN
  And no routing_config row is written

Scenario: missing/invalid token is unauthorized
  Given no (or an invalid) bearer token
  When they PUT /admin/routing
  Then the response is 401
  And no routing_config row is written
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
PUT /admin/routing   (auth: require_owner_or_admin — always-on, Tin-approved)
  body: {                          # the routing config document (all keys optional; absent => env default)
    model_groups?: { <alias>: [ <str> | {model_id:str, weight?:int>0, rpm_limit?:int>0|null, tpm_limit?:int>0|null} ] },
    routing_strategy?: "ordered"|"simple-shuffle"|"least-busy"|"latency",
    cooldown_failure_threshold?, cooldown_ttl_s?, cooldown_window_s?,
    upstream_max_retries?, upstream_retry_backoff_base_s?, upstream_retry_deadline_s?,
    upstream_fallback_on_error?, upstream_stream_resilience_enabled?,
    loadbal_ewma_alpha?, loadbal_inflight_ttl_s?
  }
  200 -> <the extended GET shape below, reflecting the just-persisted config>
  422 -> { type, title, status:422, code:"ERR_ROUTING_CONFIG_INVALID", detail:"<VALIDATOR_CODE>: <msg>" }
         # detail carries the underlying Settings/Deployment validator code:
         # UNKNOWN_ROUTING_STRATEGY | DUPLICATE_DEPLOYMENT | EMPTY_CANDIDATE_LIST |
         # TOO_MANY_CANDIDATES | ALIAS_COLLIDES_WITH_CANDIDATE | INVALID_DEPLOYMENT_WEIGHT |
         # INVALID_DEPLOYMENT_LIMIT | DEPLOYMENT_MODEL_ID_REQUIRED | INVALID_LOADBAL_ALPHA | INVALID_LOADBAL_TTL
  403 -> { code:"ERR_AUTH_FORBIDDEN" }      # member role
  401 -> { code:"ERR_AUTH_INVALID_TOKEN" }  # missing/invalid bearer

GET /admin/routing   (extended — additive only)
  200 -> {
    retry_policy: {max_retries, backoff_base_s},     # FROZEN (routing-admin §3) — unchanged
    cooldown: {enabled, threshold, ttl_s, window_s},  # FROZEN — unchanged
    model_groups: { <alias>: [<model_id>, ...] },     # FROZEN bare-string view — unchanged
    candidates: [ {model_id, alias, state} ],         # FROZEN — model_ids from effective config; state from LIVE cooldown_gate
    routing_strategy: "ordered"|...,                  # NEW additive scalar (editor needs to read/edit strategy)
    deployments: { <alias>: [ {model_id, weight, rpm_limit, tpm_limit} ] }   # NEW additive block
  }
```
Schema: `routing_config` singleton row (already shipped) — `config JSONB` holds the document above.
- READ-AFTER-WRITE access pattern (clarifies the frozen scenarios; the JSON shape above is unchanged): GET and the PUT 200-response both render the EFFECTIVE-ON-NEXT-BOOT config = `merge_routing_config(app.state.settings, RoutingConfigRepository.get())`. No stored row → merge returns settings unchanged → the four frozen blocks are BYTE-IDENTICAL to today (backward compatible). A stored row → GET reflects the persisted config (so the editor shows what was saved, even though the live router only adopts it on restart). `candidates[].state` always comes from the LIVE `cooldown_gate.snapshot_state` (runtime health, not config).
- PUT: validate via `merge_routing_config(app.state.settings, body)` (raises pydantic ValidationError ⊂ ValueError on any invalid field → 422, NOTHING persisted), then `RoutingConfigRepository(app.state.sessionmaker).upsert(body)`, then render the response from `merge_routing_config(app.state.settings, body)`. The endpoint NEVER mutates app.state (restart-to-apply).
- `deployments` block reads the merged `Settings.deployments` (alias -> [Deployment]).

Status: FROZEN @ v1 — approved by Tin
Least-sure flag surfaced at freeze: [contract] the PUT body == the stored config-doc shape (model_groups + scalar override keys), with deployment members accepted as EITHER a bare string OR a {model_id,weight,rpm_limit,tpm_limit} object (Settings._coerce_deployment handles both). Why least sure: the task-3 dashboard editor must serialize to this exact shape; if it diverges the editor round-trip breaks. Cost if wrong: an additive body-shape change + editor fix (no data migration — JSONB). Mitigation: response is built from the validated probe so the client always sees the canonical normalized form. — SECURITY authz (always-on owner/admin) was the prior HARD-STOP, resolved by Tin via AskUserQuestion 2026-06-23.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (endpoint + extended GET; merge/validation parity already covered by routing-config-store)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_put_valid_persists_and_returns: owner PUT {model_groups, routing_strategy} → 200 body reflects it; GET returns same; routing_config row present.
  - test_put_roundtrips_deployment_detail: owner PUT object-form deployment {model_id,weight,rpm_limit,tpm_limit} → 200 deployments block matches; GET deployments block matches.
  - test_get_extended_additive: GET → has retry_policy/cooldown/model_groups/candidates (frozen shape) AND new deployments block.
  - test_put_unknown_strategy_rejected_nothing_persists: store valid C, PUT bogus strategy → 422 detail names UNKNOWN_ROUTING_STRATEGY; GET still C.
  - test_put_duplicate_deployment_rejected_no_row: no stored config, PUT {gpt:["a","a"]} → 422 detail names DUPLICATE_DEPLOYMENT; no row.
  - test_put_member_forbidden: member token PUT → 403 ERR_AUTH_FORBIDDEN; no row.
  - test_put_unauthenticated: no/invalid token PUT → 401; no row.
</test_plan>

Tests live in: `apps/gateway/tests/routing_config_write/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/api/routing_admin_router.py` `apps/gateway/src/gateway/core/error_catalog.py`
Strategy (ordered batches): 1. add ERR_ROUTING_CONFIG_INVALID ErrorSpec (422) to error_catalog. 2. extract a `_routing_response(settings, model_router, gate)` helper from get_routing_admin + add the `deployments` block; GET delegates to it (byte-identical for the 4 frozen blocks). 3. add `put_routing_admin` (require_owner_or_admin) → `merge_routing_config(app.state.settings, body)` in try/except ValueError → 422 with extracted validator code in detail; on success `RoutingConfigRepository(app.state.sessionmaker).upsert(body)` → return `_routing_response(merged, ...)`.
Safety rule (feature-specific): validate-before-persist — `merge_routing_config` MUST succeed before `upsert`; on ValueError nothing is written. Restart-to-apply: the endpoint NEVER mutates app.state.model_router/settings (no live reload).
Code lives in: `apps/gateway/src/gateway/proxy/api/routing_admin_router.py`
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

- [x] all tests pass — full gateway suite **1368 passed** (`--ignore=tests/edge`, single process); `tests/routing_config_write/` 9 passed; `tests/routing_admin/` 8 passed (frozen GET shape, additively extended).
- [x] coverage did not decrease — endpoint + extended GET fully exercised (valid write, deployment-detail round-trip, additive GET, two rejections, member-403, unauth-401, admin-role allowed, junk-key strip).
- [x] no test or contract was altered during build — §3 FROZEN untouched. Test edits: STRENGTHENED my own suite (F3/F4 from refute-read) + one DISPOSITION EDIT to the FROZEN routing-admin keyset test (additive 4→6 keys, documented, secrets-free intent preserved). Re-crossed tests→build to re-snapshot.
- [x] the green was EARNED — adversarial refute-read (sonnet) verdict **UPHOLD 0.88**, no CHEAT/REAL-BUG/SECURITY-HOLE. Actioned: F1 NIT (validator-code regex echoed a user-controlled alias → now filtered to the known-code set), F2 NIT (raw body persisted → now persists only recognized routing keys, blocking stray-key/secret smuggling into JSONB); F3/F4 earned-gaps closed with tests. All 8 attack vectors REFUTED.
- [x] concurrency / timing safe — validate-before-persist: `merge_routing_config` MUST succeed before `upsert` (422 persists nothing). RESTART-TO-APPLY: the endpoint NEVER mutates app.state.settings/model_router (no live hot-path reload). GET read is fail-open to env (design-for-failure).
- [x] no exposed secrets / injection / unexpected deps — JSONB upsert is fully parameterized (pg_insert.values); body persists ONLY whitelisted routing keys; 422 detail carries only a known validator code, never input values/secrets; no new dependencies.
- [x] layering & dependencies follow CONVENTIONS.md — api (`routing_admin_router`) → application (`merge_routing_config`) → infrastructure (`RoutingConfigRepository`); owner/admin gate via shared `require_owner_or_admin`; error via `error_catalog` ErrorSpec.
- [x] reviewed — **SECURITY authz was a HARD-STOP resolved by Tin** (AskUserQuestion 2026-06-23: owner/admin always-on). Remaining gate auto-resolved under `autonomy: auto` (the authz HARD-STOP cleared) on complete evidence + refute-read UPHOLD.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] A valid PUT persists + round-trips — confirmed by `test_put_valid_persists_and_returns` (200 body shows new model_groups/routing_strategy; GET returns same; row count 1).
- [x] Deployment weight/rpm/tpm round-trip in the additive `deployments` block — confirmed by `test_put_roundtrips_deployment_detail`.
- [x] GET is additively extended, four frozen blocks intact — confirmed by `test_get_extended_additive` + routing-admin RA8 (keyset 4→6, sub-shapes unchanged).
- [x] Invalid config rejected with the existing validator code, nothing persisted — confirmed by `test_put_unknown_strategy_rejected_nothing_persists` (422, detail UNKNOWN_ROUTING_STRATEGY, GET still C, row=1) + `test_put_duplicate_deployment_rejected_no_row` (422 DUPLICATE_DEPLOYMENT, row=0).
- [x] Authz enforced — `test_put_member_forbidden` (403, no row), `test_put_unauthenticated` (401, no row), `test_put_admin_role_allowed` (admin 200).
- [x] No stray-key persistence — `test_put_strips_unrecognized_keys` (jwt_secret/junk NOT in stored row).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `put_routing_admin` mounted on the existing `routing_admin_router` (already wired in main.py); `_effective_settings`/`_routing_response` used by both GET and PUT; `ROUTING_CONFIG_INVALID` raised in PUT; `ROUTING_OVERRIDE_KEYS` (made public) consumed for `_PERSISTABLE_KEYS`.
- [x] DEAD-CODE (code) — no orphaned symbols; the old inline GET body was replaced by the shared helper.
- [x] SEMANTIC — refute-read read all modules in full (UPHOLD 0.88).

### GATE RECORD
Outcome: PASS
Reviewed by: Tin (security authz HARD-STOP, AskUserQuestion 2026-06-23) + auto-resolved on evidence + sonnet refute-read UPHOLD 0.88 · date: 2026-06-23

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): 422 ERR_ROUTING_CONFIG_INVALID rate by validator code; PUT /admin/routing volume (no rate-limit yet).

### Spec delta
- [SPEC · open] add a rate-limit/debounce on PUT /admin/routing — any owner/admin can repeatedly rewrite operator-wide config (evidence: same accepted-risk shape as catalog-sync-trigger).  [routing-config-write]
- [SPEC · open] expose the global retry/cooldown/loadbal scalar knobs in the editor too (GET already returns retry_policy+cooldown; PUT accepts the knobs) — this task wired strategy + deployments only (evidence: milestone named those three; the rest are additive).  [routing-config-write]
- [SPEC · open] when there IS no operator role, consider a future operator/platform role so operator-wide routing write is not tenant-owner-scoped (evidence: Tin accepted owner/admin always-on for single-operator deployments; a multi-operator tenancy would want a higher bar).  [routing-config-write]

### Competency deltas
- [SDD · folded] read-after-write under a RESTART-TO-APPLY model must read the persisted store (merge over settings), NOT the live app.state (which is stale until restart) — and stay byte-identical when no row exists so the existing read contract is preserved (evidence: GET/PUT render merge_routing_config(settings, stored); routing-admin frozen blocks unchanged when no row).  [routing-config-write] [folded foundation-version 29]
- [ADD · folded] a refute-read BLOCK verdict can OVER-claim — evaluate each finding on its merits (F1 "member flash" was actually fail-closed-correct; F3 "re-seed" was by-design per the refetch contract), FIX the real ones (surface the validator `detail`), and REFUTE the false ones with reasoning; never just accept the headline verdict (evidence: editor refute BLOCK 0.82 → 2 fixed, 2 refuted, green earned).  [routing-config-write] [folded foundation-version 29]
- [ADD · folded] a privileged WRITE endpoint is a security contract freeze even when it reuses an existing auth dep — surface the authz model (who, always-on vs flag vs ops-boundary) to the human as a HARD-STOP before building, because the role SCOPE (tenant-owner vs operator) materially changes the blast radius (evidence: PUT /admin/routing authz → AskUserQuestion → Tin chose owner/admin always-on).  [routing-config-write] [folded foundation-version 29]
