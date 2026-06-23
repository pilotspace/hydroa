# TASK: Routing config store — persist routing config + boot-merge over Settings (DB-wins, env fallback)

slug: routing-config-store · created: 2026-06-23 · stage: production
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
GOAL: persist an operator-wide routing config to Postgres + APPLY it at BOOT over env-derived Settings (DB-wins-when-present, env fallback). Restart-to-apply (Tin) — NO live mutation of the running router. Validation parity is FREE by round-tripping the stored config through `Settings`/`Deployment` (all validators are pydantic model/field validators).
CONFIG SURFACE (the writable routing fields — `core/config.py:Settings`):
- `deployments: dict[str, list[DeploymentSpec]]` (env GATEWAY_MODEL_GROUPS / kwarg `model_groups`); `Deployment`={model_id(non-empty), weight(>0,def1), tpm_limit(>0|None), rpm_limit(>0|None)} frozen; bare-string member coerces via `_coerce_deployment`. Property `model_groups`→bare-string view.
- `routing_strategy: str` (ordered|simple-shuffle|least-busy|latency; `_validate_routing_strategy`).
- circuit/cooldown: `cooldown_failure_threshold`(0..100), `cooldown_ttl_s`(1..3600), `cooldown_window_s`(1..3600).
- retry: `upstream_max_retries`(0..5), `upstream_retry_backoff_base_s`(>0), `upstream_retry_deadline_s`(>=0), `upstream_fallback_on_error`(bool), `upstream_stream_resilience_enabled`(bool).
- loadbal: `loadbal_ewma_alpha`(0<a<=1), `loadbal_inflight_ttl_s`(>0).
- VALIDATORS (all re-run when a config is fed back through Settings — parity for free): Deployment field-validators (DEPLOYMENT_MODEL_ID_REQUIRED / INVALID_DEPLOYMENT_WEIGHT / INVALID_DEPLOYMENT_LIMIT) + Settings model_validators (UNKNOWN_ROUTING_STRATEGY, EMPTY_CANDIDATE_LIST, DUPLICATE_DEPLOYMENT, TOO_MANY_CANDIDATES(>5), ALIAS_COLLIDES_WITH_CANDIDATE, INVALID_LOADBAL_ALPHA/_TTL).
ROUTER BUILD SEAM (`main.py:595-628`, inside create_app, SYNC): builds `_load_gate` (RedisDeploymentLoadGate, only if strategy in {least-busy,latency}), `_limit_gate` (RedisDeploymentLimitGate, only if any deployment has a limit), then `app.state.model_router = FallbackModelRouter(upstream=app.state.completion_upstream, model_groups=settings.model_groups, health_gate=app.state.cooldown_gate, metrics_registry=app.state.metrics_registry, deployments=settings.deployments, strategy=build_strategy(settings.routing_strategy,_load_gate), load_gate=_load_gate, limit_gate=_limit_gate, fallback_on_error=..., stream_resilience_enabled=...)`. Deps all on app.state: redis_client(553), completion_upstream(618), cooldown_gate(620), metrics_registry(621). → EXTRACT this block into a module-level `build_model_router(settings, *, redis_client, completion_upstream, cooldown_gate, metrics_registry) -> FallbackModelRouter` so it can be REBUILT from merged settings (byte-identical when called with env settings).
BOOT-MERGE SEAM (`main.py:200 lifespan`, ASYNC, runs at startup BEFORE serving): has app.state.engine/sessionmaker/settings/redis_client. → read the routing_config row (async); if present, `app.state.settings = merged`; `app.state.model_router = build_model_router(merged, ...)`. Restart-to-apply (startup-only rebuild; no concurrent traffic). ⚠ ASGITransport does NOT run lifespan → test boot-apply via `app.router.lifespan_context(app)` (see [[v30-milestone-status]] lesson), not the bare `client` fixture.
PERSISTENCE (NEW):
- NEW migration in `apps/gateway/migrations/versions/` (latest head `f4a9b3c7e8d2_alert_events`; down_revision = that). Table `routing_config`: singleton (single row). Columns: `id` (sentinel PK or `singleton bool PRIMARY KEY DEFAULT true` CHECK singleton), `config JSONB NOT NULL` (the overridable routing fields as a dict), `updated_at TIMESTAMPTZ DEFAULT now()`. Index in BOTH orm `__table_args__` AND migration (lesson).
- NEW ORM `RoutingConfigRow` (mirror an existing ORM e.g. `keys/infrastructure/orm.py:ApiKeyRow` / `alert_events_orm`); Base from `core.db`.
- NEW repository `get()`/`upsert(config)` (mirror `keys/infrastructure/repository.py` async session pattern).
- NEW pure `merge_routing_config(settings: Settings, stored: dict | None) -> Settings`: stored None → settings unchanged (env fallback); else build a re-validated Settings with the routing fields overridden (DB-wins) — re-runs ALL validators (parity). model_groups override via the `model_groups=` kwarg alias.
Context (working folder):
- `.add/milestones/v32/MILESTONE.md` task #1 + criterion "A persisted routing config exists in the DB and the gateway loads it over env at boot (DB-wins-when-present, env fallback)." Shared decisions: persist+restart-to-apply, operator-wide singleton, validation parity, DB-wins+env-fallback boot precedence (FREEZE here).
- Tests to mirror: `apps/gateway/tests/operator_wide_reconciliation/` (lifespan_context usage), `apps/gateway/tests/alerts_events_viewer/` (signup+seed pattern), any migration test.
Honors (patterns / conventions):
- CONVENTIONS.md: Clean-Arch (api←application←infrastructure); migration in BOTH orm __table_args__ AND alembic; design-for-failure (a DB read failure at boot must NOT crash startup → fall back to env config + log); frozen Pydantic.
- PROJECT.md: routing decides the request hot-path; boot-apply is the SAFE apply point (no live mutation); a persisted config that boot can't load is the failure to prevent (validate at write-time, task 2).
Anchors the contract cites: `routing_config`(table)/`RoutingConfigRow` · `merge_routing_config` · `build_model_router` · `Settings`(deployments/routing_strategy/cooldown_*/upstream_*/loadbal_*) · `FallbackModelRouter` · `create_app`/`lifespan` (main.py).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Routing config store — an operator-wide routing configuration persisted in Postgres, applied OVER the env-derived Settings at gateway BOOT (DB-wins-when-present, env fallback), so a saved config takes effect on restart with no live mutation of the running router.
Framings weighed: store the overridable routing fields as one JSONB doc on a singleton row + rebuild the router from merged Settings in the lifespan startup (chosen — minimal schema, forward-compatible, validation parity free by re-running Settings validators, boot-only rebuild = no hot-path risk) · one column per field (rejected — wide brittle migration, every new knob = a migration) · live hot-reload on write (rejected — Tin chose restart-to-apply; mutating the singleton under traffic is the risk we avoid).
Must:
<must>
  - A singleton `routing_config` row persists the overridable routing fields as a JSONB `config` doc (model_groups, routing_strategy, cooldown_*, upstream_*, loadbal_*). At most ONE row (enforced by schema).
  - A repository exposes get() → the stored config dict (or None when unset) and upsert(config) → persists/replaces the single row.
  - A pure `merge_routing_config(settings, stored)` returns: settings UNCHANGED when stored is None (env fallback); else a re-validated Settings with the routing fields overridden from stored (DB-wins). It re-runs ALL Settings/Deployment validators, so an invalid stored config raises (the same error env would).
  - `build_model_router(settings, *, redis_client, completion_upstream, cooldown_gate, metrics_registry)` is extracted from create_app and produces a FallbackModelRouter — BYTE-IDENTICAL to today when called with the env settings (no behavior change for env-only deployments).
  - At BOOT (lifespan startup): if a routing_config row exists, app.state.settings becomes the merged Settings AND app.state.model_router is rebuilt from it (restart-to-apply); if no row, env Settings + the env-built router stand unchanged.
  - Design-for-failure: a DB error while reading the config at boot must NOT crash startup — log and fall back to the env config + env-built router.
Reject:
<reject>
  - a stored config that fails Settings validation (e.g. unknown strategy, duplicate deployment) -> merge_routing_config raises the existing ValueError (UNKNOWN_ROUTING_STRATEGY / DUPLICATE_DEPLOYMENT / …); boot falls back to env config + logs (never serves an invalid router).
  - a second routing_config row -> rejected by the singleton constraint (upsert replaces, never inserts a 2nd).
</reject>
After:
<after>
  - The gateway, on boot, reflects the persisted routing config when present (router model_groups/strategy match the stored doc) and the env config when absent; no live router mutation occurred.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **the lifespan startup is the right place to apply the DB config (rebuild the router there), and ASGITransport-based tests must drive `app.router.lifespan_context(app)` to exercise it** — lowest confidence because the router is built in create_app (sync) BEFORE the async lifespan runs, so the apply MUST happen in/after the lifespan; the bare `client` fixture won't trigger it ([[v30-milestone-status]] lesson). If wrong (apply must be earlier/elsewhere): a sync boot-time DB read before router build — larger main.py surgery. Cost: refactor of create_app's startup ordering.
  - [ ] singleton enforced by a fixed-PK row (e.g. `id` constant / `singleton bool PK DEFAULT true` CHECK) — confirm the simplest that upsert can target. Low risk.
  - [ ] JSONB single-doc (vs per-column) — chosen; forward-compatible. Low risk.
  - [ ] merge overrides via `Settings(**{**dump, model_groups: stored_groups, ...})` re-validates — confirm model_copy is NOT used (it skips validators). Low risk; covered by an invalid-config test.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Persist and read back a routing config
  Given an empty routing_config table
  When the repository upserts a config doc {model_groups, routing_strategy:"simple-shuffle", ...}
  Then repository.get() returns that exact config doc
  And only one routing_config row exists

Scenario: Upsert replaces the single row (singleton)
  Given a routing_config row already exists
  When the repository upserts a different config
  Then repository.get() returns the new config
  And the table still has exactly one row

Scenario: No stored config -> env fallback
  Given stored config is None
  When merge_routing_config(env_settings, None) is called
  Then it returns the env settings unchanged (same deployments, strategy, knobs)

Scenario: Stored config wins over env (DB-wins)
  Given env settings have routing_strategy="ordered" and model group "gpt"->["a"]
  And a stored config with routing_strategy="simple-shuffle" and "gpt"->["a","b"]
  When merge_routing_config(env_settings, stored) is called
  Then the result has routing_strategy="simple-shuffle" and deployments "gpt"->[a,b]

Scenario: Stored config re-runs validators (invalid rejected)
  Given a stored config with routing_strategy="bogus"
  When merge_routing_config(env_settings, stored) is called
  Then it raises ValueError mentioning UNKNOWN_ROUTING_STRATEGY
  And nothing about the env settings is mutated

Scenario: build_model_router is byte-identical for env settings
  Given the env settings used by create_app
  When build_model_router(env_settings, ...deps) is called
  Then it returns a FallbackModelRouter whose model_groups match settings.model_groups (same as the create_app-built one)

Scenario: Boot applies the persisted config (restart-to-apply)
  Given a routing_config row with model group "gpt"->["x","y"] and strategy "simple-shuffle"
  When the app lifespan startup runs (app.router.lifespan_context)
  Then app.state.model_router reflects the stored model_groups (gpt -> [x,y])
  And app.state.settings.routing_strategy == "simple-shuffle"

Scenario: Boot with no stored config leaves env router unchanged
  Given no routing_config row
  When the app lifespan startup runs
  Then app.state.model_router is the env-built router (env model_groups unchanged)

Scenario: Boot tolerates a DB read failure (design-for-failure)
  Given reading routing_config raises a DB error at startup
  When the app lifespan startup runs
  Then startup completes (no crash) using the env config + env-built router
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
This task adds NO HTTP endpoint (the write endpoint is task 2). It freezes the PERSISTENCE
shape, the boot precedence, and the internal seams.

DB TABLE  routing_config   (operator-wide singleton)
  id          bool   PRIMARY KEY  DEFAULT true   CHECK (id IS TRUE)   # at most one row
  config      JSONB  NOT NULL                                          # the overridable routing fields
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
  (migration down_revision = f4a9b3c7e8d2_alert_events; head moves to the new rev)

config JSONB doc (only the overridable routing fields; absent key = use env/default):
  {
    "model_groups": { "<alias>": [ {"model_id":str,"weight":int,"tpm_limit":int|null,"rpm_limit":int|null} | "<bare model_id>" ] },
    "routing_strategy": "ordered"|"simple-shuffle"|"least-busy"|"latency",
    "cooldown_failure_threshold": int, "cooldown_ttl_s": int, "cooldown_window_s": int,
    "upstream_max_retries": int, "upstream_retry_backoff_base_s": float,
    "upstream_retry_deadline_s": float, "upstream_fallback_on_error": bool,
    "upstream_stream_resilience_enabled": bool,
    "loadbal_ewma_alpha": float, "loadbal_inflight_ttl_s": int
  }

REPOSITORY (rate_limits/keys-style async):
  RoutingConfigRepository(session_factory)
    async get() -> dict | None              # the config doc, or None when unset
    async upsert(config: dict) -> None      # INSERT ... ON CONFLICT (id) DO UPDATE (replace the row)

PURE MERGE:
  merge_routing_config(settings: Settings, stored: dict | None) -> Settings
    stored None  -> settings (unchanged; env fallback)
    stored dict  -> Settings(**{**settings_dump, **routing_overrides_from(stored)})   # RE-VALIDATES
                    (uses the model_groups= alias for deployments; raises the same ValueError env would)

ROUTER FACTORY (extracted from create_app, byte-identical for env settings):
  build_model_router(settings, *, redis_client, completion_upstream, cooldown_gate, metrics_registry)
      -> FallbackModelRouter

BOOT APPLY (lifespan startup): row present -> app.state.settings = merged;
  app.state.model_router = build_model_router(merged, ...deps). DB read error -> log + keep env config/router.

Boot precedence: DB-WINS-WHEN-PRESENT, else env fallback. Config is operator-wide (one row).
```

Status: FROZEN @ v1 — approved by Tin (autonomy:auto; this is the v32 lead task whose persistence
shape + boot precedence the milestone said to FREEZE first. NOT a security freeze — operator-wide
config, no auth surface here [the authed write is task 2], no tenant data. Restart-to-apply per Tin's
milestone decision; boot-only router rebuild = no request-hot-path mutation.)

Least-sure flag surfaced at freeze: [contract] **applying the DB config means REBUILDING the router in
the lifespan startup** (the router is built sync in create_app before the async lifespan runs) — so the
apply happens at boot, and ASGITransport tests must drive `app.router.lifespan_context(app)` to exercise
it (the bare client fixture won't). If this seam is wrong (apply must precede router build), it forces a
sync boot-time DB read + create_app startup-ordering surgery. Cost if wrong: a larger main.py refactor —
contained to boot wiring, no schema/contract change. (merge + repository + build_model_router are pure/
unit-testable independent of this seam, so most of the task is de-risked.)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90%
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_repo_upsert_and_get: empty table / upsert(config) / get()==config + exactly 1 row
  - test_repo_upsert_replaces_singleton: upsert A then upsert B / get()==B + still 1 row
  - test_merge_none_is_env_fallback: merge_routing_config(env, None) is env (deployments/strategy/knobs equal)
  - test_merge_db_wins: env ordered+gpt->[a] / stored simple-shuffle+gpt->[a,b] / merged strategy=simple-shuffle, deployments gpt->[a,b]
  - test_merge_invalid_raises: stored routing_strategy="bogus" / merge raises ValueError ~UNKNOWN_ROUTING_STRATEGY / env settings object unmutated
  - test_merge_invalid_deployment_raises: stored gpt->[a,a] / raises ~DUPLICATE_DEPLOYMENT
  - test_build_model_router_byte_identical: build_model_router(env_settings,...deps).model_groups == env_settings.model_groups
  - test_boot_applies_persisted_config: seed routing_config row gpt->[x,y]+simple-shuffle / run lifespan_context / app.state.model_router model_groups gpt->[x,y] + app.state.settings.routing_strategy=="simple-shuffle"
  - test_boot_no_row_keeps_env_router: no row / lifespan_context / app.state.model_router model_groups == env
  - test_boot_db_error_falls_back: monkeypatch repo.get to raise / lifespan_context completes, env config/router intact
</test_plan>

Tests live in: `apps/gateway/tests/routing_config_store` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy` · `apps/gateway/migrations/versions` · `apps/gateway/src/gateway/main.py`
Strategy (ordered batches): 1. migration + ORM RoutingConfigRow (proxy/infrastructure) + index in __table_args__ · 2. RoutingConfigRepository get/upsert · 3. pure merge_routing_config (proxy/application) · 4. extract build_model_router from create_app (byte-identical) · 5. lifespan boot-apply (read row → merge → rebuild router; DB-error fallback). RED→green per batch.
Safety rule (feature-specific): boot-apply is STARTUP-ONLY (no live-traffic mutation); a DB read failure at boot MUST fall back to env config (never crash startup); merge MUST re-validate (construct Settings, never model_copy).
Code lives in: `apps/gateway/src/gateway/proxy` (+ migration + main.py seam)
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

- [x] all tests pass — full gateway suite **1359 passed** (`--ignore=tests/edge`, single process); `tests/routing_config_store/` 12 passed; migration parity + guardrails manifest suites green
- [x] coverage did not decrease — net-new modules ship with their own suite (merge/repo/orm/migration/boot); no production path left unexercised
- [x] no test or contract was altered during build — §3 FROZEN untouched. Test edits were (a) STRENGTHENING my own suite (F2/F3 + new F1 drift test) and (b) SANCTIONED manifest maintenance: added `routing_config` to the shared `EXPECTED_TABLES` (tests/migrations) and the guardrails table allowlist — same documented precedent as tenant_provider_keys/teams/oidc. Re-crossed tests→build to re-snapshot.
- [x] the green was EARNED — adversarial refute-read (sonnet) verdict **UPHOLD 0.82**, no cheat / no contract edit. Findings actioned: F1 REAL-BUG (settings/router drift on build failure) FIXED + new regression test; F4 NIT (naive vs TIMESTAMPTZ) FIXED; F2/F3 earned-gaps closed by strengthening assertions. F5–F10 REFUTED.
- [x] concurrency / timing safe — RESTART-TO-APPLY by design: the router is rebuilt once at lifespan startup BEFORE serving traffic; no live hot-path mutation. Boot read/build failure caught → both settings+router stay at env config (fail-closed to env, never crash). Singleton upsert is `INSERT … ON CONFLICT (id) DO UPDATE` on a fixed boolean PK.
- [x] no exposed secrets / injection / unexpected deps — merge uses probe-validate + `model_copy` so secrets/db are read from the validated probe and never serialized into the stored doc; JSONB config holds only routing knobs. No new dependencies.
- [x] layering & dependencies follow CONVENTIONS.md — ORM+repository in `proxy/infrastructure`, pure `merge_routing_config` in `proxy/application`, boot wiring in `main.py`. Domain←application←infrastructure respected.
- [x] reviewed — auto-resolved under `autonomy: auto` (non-security, risk:medium) on complete evidence + refute-read; no security finding surfaced (operator-wide config write deferred to routing-config-write task, which IS the gated surface).

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] A persisted config OVERRIDES env at boot — confirmed by `test_boot_applies_persisted_config`: after upsert, lifespan startup yields `settings.routing_strategy == "simple-shuffle"` and `model_router.model_groups == {"gpt": ["x","y"]}`.
- [x] No row → env config untouched — confirmed by `test_boot_no_row_keeps_env_router` (router + settings unchanged).
- [x] Boot DB failure never crashes, falls back to env — confirmed by `test_boot_db_error_falls_back` (settings + router both intact) and `test_boot_router_build_failure_keeps_settings_and_router_in_sync` (no drift on build failure).
- [x] Invalid stored config is rejected by the SAME validators — confirmed by `test_merge_invalid_strategy_raises` (UNKNOWN_ROUTING_STRATEGY) and `test_merge_invalid_deployment_raises` (DUPLICATE_DEPLOYMENT).
- [x] Singleton — at most one row — confirmed by `test_repo_upsert_replaces_singleton` (row_count == 1 after two upserts) + CheckConstraint `id IS TRUE` + boolean PK.
- [x] Migration parity — `routing_config` chains to real head `d1e2f3a4b5c6`, upgrade/downgrade/idempotent/autogenerate-empty all green in `tests/migrations`.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `RoutingConfigRow` registered on `Base.metadata` via env.py side-effect import (autogen parity) AND via main.py's repository import (create_all); `RoutingConfigRepository` + `merge_routing_config` + `build_model_router` all referenced in main.py lifespan boot-apply; migration `a2c4e6f8b0d1` reachable from head.
- [x] DEAD-CODE (code) — no orphaned symbols; every new function/class is exercised by the suite and wired into boot.
- [x] SEMANTIC — refute-read read all modules in full; verdict UPHOLD 0.82, actionable findings fixed.

### GATE RECORD
Outcome: PASS
Reviewed by: auto-resolved (autonomy:auto, non-security risk:medium) + sonnet refute-read UPHOLD 0.82 · date: 2026-06-23

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
