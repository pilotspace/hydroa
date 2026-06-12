# TASK: Deployment / model-group config — string-or-object union with weight + tpm/rpm limits

slug: deployment-model · created: 2026-06-12 · stage: production · risk: high · autonomy: conservative
phase: tests   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Deployment config shape — a model group's members become Deployment objects
(model_id + optional weight / tpm_limit / rpm_limit) instead of bare model-id strings,
with backward-compatible string coercion. This is the FREEZE-FIRST contract every v8
routing task inherits; it establishes the DATA shape only — it adds NO routing strategy
and NO limit enforcement (those are routing-strategy / balance-strategies /
deployment-limits). Behavior stays byte-identical to v6: ordered fallback over the
normalized deployment.model_id list.

Framings weighed:
  - **String-or-object union on the existing model_groups dict, normalized to an
    internal Deployment list (chosen)**: `model_groups: dict[str, list[str | DeploymentConfig]]`;
    a pydantic `model_validator(after)` coerces every member to a normalized internal
    `Deployment(model_id, weight=1, tpm_limit=None, rpm_limit=None)`. The
    FallbackModelRouter, routing_admin_router, and the alias-aware catalog check read
    `deployment.model_id` where they read the bare string today; iteration ORDER is
    preserved so an all-bare-string / weight-1 / no-limit group is byte-identical to v6.
    Minimal blast radius; the v6 GATEWAY_MODEL_GROUPS JSON keeps working unchanged.
  - **New parallel GATEWAY_DEPLOYMENTS setting (rejected)**: two overlapping config
    surfaces for the same concept; drift risk; forces every consumer to merge two maps.
  - **Per-deployment DB rows instead of config (rejected for this slice)**: model_groups
    is config-driven today (v6); moving to DB is a separate, larger migration — Out of
    scope (admin-UI/DB deployment management is explicitly deferred in MILESTONE.md).

Must:
<must>
  - `Settings.model_groups` MUST accept BOTH member shapes within a group's list:
    - a bare string `"vendor/model"` (the v6 shape), AND
    - an object `{"model_id": "vendor/model", "weight": 3, "tpm_limit": 100000, "rpm_limit": 600}`
      where only `model_id` is required.
  - A bare string MUST coerce to a Deployment with `weight=1`, `tpm_limit=None`,
    `rpm_limit=None` — so a v6 all-string group is behaviorally identical (ordered
    fallback, no weighting, no limits). Default `{}` stays feature-off / v6 byte-identical.
  - The normalized internal representation MUST preserve member ORDER (fallback order)
    and the exact `model_id` strings (billing + alias-aware catalog check depend on them).
  - Startup validation (extends the existing `_validate_model_groups`):
    - weight MUST be a number > 0 (default 1) — else reject.
    - tpm_limit / rpm_limit, when present, MUST be integers > 0 — else reject.
    - a deployment object MUST carry a non-empty `model_id` — else reject.
    - duplicate `model_id` within a single group MUST reject (ambiguous routing target).
    - empty group list MUST still reject as `EMPTY_CANDIDATE_LIST` (existing v6 rule).
  - `FallbackModelRouter`, `routing_admin_router` (GET /admin/routing), and the
    alias-aware catalog check (use_cases) MUST behave byte-identically to v6 for the
    all-string / weight-1 / no-limit case. The router keeps its `.model_groups` STRING
    view (which routing-admin + the alias-aware check read); the normalized Deployment
    objects are exposed via a SEPARATE `.deployments` view for the strategy layer.
    routing_admin_router.py is UNTOUCHED — RA8 freezes the EXACT top-level + per-candidate
    key sets, so deployment weight/limits are NOT surfaced on the admin API this task
    (resolved at freeze; see §3 least-sure flag). The v6 frozen routing-admin + fallback
    suites MUST stay green.
  - Billing MUST continue to key on the served deployment's catalog model id (the router's
    returned candidate id) — never `response_body["model"]` (folded v6 §Key Decision).
</must>
Reject:
<reject>
  - weight ≤ 0 or non-numeric -> "INVALID_DEPLOYMENT_WEIGHT" (startup ValidationError)
  - tpm_limit or rpm_limit ≤ 0 or non-integer -> "INVALID_DEPLOYMENT_LIMIT" (startup)
  - deployment object missing / empty model_id -> "DEPLOYMENT_MODEL_ID_REQUIRED" (startup)
  - duplicate model_id within one group -> "DUPLICATE_DEPLOYMENT" (startup)
  - empty group candidate list -> "EMPTY_CANDIDATE_LIST" (existing v6 rule, preserved)
  - any change that alters v6 ordered-fallback behavior for an all-string group, or edits
    a frozen v6 test / the INVIOLABLE chat path -> "ERR_FROZEN_VIOLATION"
</reject>
After:
<after>
  - `GATEWAY_MODEL_GROUPS` accepts string members, object members, or a mix; every member
    is normalized to an internal Deployment with order + model_id preserved.
  - The chat path and the v6 model-group fallback behavior are byte-identical for the
    all-bare-string case (v6 frozen suites green; v6-alias live check unaffected).
  - The frozen Deployment shape (model_id · weight · tpm_limit · rpm_limit) is the stable
    interface that routing-strategy / balance-strategies / deployment-limits build on.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ LOWEST CONFIDENCE: the GET /admin/routing response shape is pinned by v6 frozen tests
    (`model_groups: dict[str, list[str]]` + a `candidates` block). Adding per-deployment
    metadata (weight/limits) MUST be purely ADDITIVE — the existing keys and their string
    shapes cannot change or the frozen v6 routing-admin tests break (and frozen tests are
    never edited). Lowest confidence because the exact frozen response shape must be
    re-read from the v6 routing-admin tests before §3 freezes the additive fields. If wrong
    cost: a frozen-test break that forces a contract redesign mid-build.
  - [ ] pydantic v2 `str | DeploymentConfig` smart-union coerces a JSON object to
    DeploymentConfig and leaves a bare string as str, with our `model_validator(after)`
    normalizing both — confirm the union does not mis-coerce a string into a model.
  - [ ] every site that today reads a group member as a string (FallbackModelRouter
    .candidates_for / .complete / the alias-aware check in use_cases, routing_admin_router)
    is enumerated and switched to `.model_id` with identical strings — confirm the full
    consumer list so no string read is missed.
  - [ ] weight is the only field the strategy layer needs from this task; tpm/rpm are
    carried now but ENFORCED later (deployment-limits) — confirm carrying-not-enforcing
    is acceptable so this task stays a pure data-shape freeze.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: DM1 — bare-string group coerces to a weight-1 no-limit deployment
  Given GATEWAY_MODEL_GROUPS='{"fast": ["vendor/a", "vendor/b"]}'
  When the gateway boots and normalizes the config
  Then settings.deployments["fast"] == [Deployment("vendor/a",1,None,None), Deployment("vendor/b",1,None,None)]
  And settings.model_groups == {"fast": ["vendor/a","vendor/b"]} stays a bare-string view (v6 byte-identical)

Scenario: DM2 — object members parse weight + tpm/rpm limits, order preserved
  Given GATEWAY_MODEL_GROUPS='{"fast":[{"model_id":"vendor/a","weight":3,"tpm_limit":100000,"rpm_limit":600},"vendor/b"]}'
  When the gateway boots
  Then settings.deployments["fast"][0] == Deployment("vendor/a",3,100000,600)
  And settings.deployments["fast"][1] == Deployment("vendor/b",1,None,None)
  And settings.model_groups["fast"] == ["vendor/a","vendor/b"]   # string view, original order

Scenario: DM3 — mixed group routes byte-identically to v6 (ordered fallback over model_id)
  Given a model group "fast" with one object member and one string member, all weight 1, no limits
  When a chat completion targets alias "fast" and the first deployment fails
  Then the FallbackModelRouter attempts deployments in declared order by model_id, falling back exactly as v6
  And billing keys on the SERVED deployment's catalog model id (the router's returned candidate id)

Scenario: DM4 — /admin/routing response stays byte-identical (frozen RA1/RA8)
  Given GATEWAY_MODEL_GROUPS='{"fast":[{"model_id":"vendor/a","weight":3}]}'
  When GET /admin/routing is called by an owner
  Then body["model_groups"] == {"fast": ["vendor/a"]}   # bare strings, no weight/limit keys
  And top-level keys == {retry_policy,cooldown,model_groups,candidates} and each candidate == {model_id,alias,state}

Scenario: DM5 — weight <= 0 rejected at startup
  Given GATEWAY_MODEL_GROUPS='{"g":[{"model_id":"m","weight":0}]}'
  When the gateway boots
  Then startup raises ValidationError containing "INVALID_DEPLOYMENT_WEIGHT"
  And no app starts (fail-closed; no partial config takes effect)

Scenario: DM6 — non-positive tpm/rpm limit rejected at startup
  Given GATEWAY_MODEL_GROUPS='{"g":[{"model_id":"m","tpm_limit":0}]}'
  When the gateway boots
  Then startup raises ValidationError containing "INVALID_DEPLOYMENT_LIMIT"
  And no app starts

Scenario: DM7 — deployment object missing model_id rejected at startup
  Given GATEWAY_MODEL_GROUPS='{"g":[{"weight":2}]}'
  When the gateway boots
  Then startup raises ValidationError containing "DEPLOYMENT_MODEL_ID_REQUIRED"
  And no app starts

Scenario: DM8 — duplicate model_id within one group rejected at startup
  Given GATEWAY_MODEL_GROUPS='{"g":["m","m"]}'
  When the gateway boots
  Then startup raises ValidationError containing "DUPLICATE_DEPLOYMENT"
  And no app starts

Scenario: DM9 — existing v6 validators preserved (empty / collision / >5)
  Given GATEWAY_MODEL_GROUPS with an empty list, or an alias used as a candidate id, or a 6-member list
  When the gateway boots
  Then startup raises ValidationError containing "EMPTY_CANDIDATE_LIST" / "ALIAS_COLLIDES_WITH_CANDIDATE" / "TOO_MANY_CANDIDATES" respectively
  And these v6 rules apply over the normalized model_id view exactly as before

Scenario: DM10 — empty config stays feature-off, v6 byte-identical
  Given GATEWAY_MODEL_GROUPS unset (default {})
  When the gateway boots and serves a plain (non-alias) chat completion
  Then settings.deployments == {} and settings.model_groups == {}
  And the chat path is byte-identical to v6 (no deployment normalization on the plain path)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

This task freezes a CONFIG + INTERNAL DATA shape (no HTTP endpoint of its own). No
route is added or changed; /admin/routing stays byte-identical (frozen RA1/RA8).

```
TYPES (new — gateway/core/config.py or a sibling module)
  Deployment            # immutable normalized value object (frozen dataclass / pydantic model)
    model_id : str               # required, non-empty
    weight   : int = 1           # > 0
    tpm_limit: int | None = None # > 0 when present (CARRIED, not enforced this task)
    rpm_limit: int | None = None # > 0 when present (CARRIED, not enforced this task)

CONFIG (gateway/core/config.py — Settings)
  env GATEWAY_MODEL_GROUPS : JSON dict[str, list[str | DeploymentInput]]
      DeploymentInput = str  |  {"model_id": str, "weight"?: int, "tpm_limit"?: int, "rpm_limit"?: int}
      # a bare string s  ==  {"model_id": s}  (weight 1, no limits)
  settings.deployments   : dict[str, list[Deployment]]   # NEW canonical normalized view, order-preserved
  settings.model_groups  : dict[str, list[str]]          # PRESERVED bare-string view = [d.model_id ...]
                                                          # (byte-identical to v6; RA1/RA8 + all consumers read this)

VALIDATION (startup model_validator, mode="after"; fail-closed — raises ValueError):
  weight <= 0 / non-int            -> "INVALID_DEPLOYMENT_WEIGHT"
  tpm_limit|rpm_limit present <= 0 -> "INVALID_DEPLOYMENT_LIMIT"
  object member missing/empty id   -> "DEPLOYMENT_MODEL_ID_REQUIRED"
  duplicate model_id in one group  -> "DUPLICATE_DEPLOYMENT"
  empty candidate list             -> "EMPTY_CANDIDATE_LIST"        (v6, preserved)
  alias used as a candidate id     -> "ALIAS_COLLIDES_WITH_CANDIDATE"(v6, preserved — over model_id view)
  > 5 members in a group           -> "TOO_MANY_CANDIDATES"         (v6, preserved)

ROUTER (gateway/proxy/application/fallback_router.py — FallbackModelRouter)
  __init__(... , model_groups: dict[str,list[str]], deployments: dict[str,list[Deployment]] | None = None, ...)
  .model_groups   -> dict[str,list[str]]            # UNCHANGED property (RA1/RA8 + alias-aware check read this)
  .deployments    -> dict[str,list[Deployment]]     # NEW read-only view for the strategy layer (v8 routing-strategy)
  .candidates_for(model_id) -> list[str] | None     # UNCHANGED (bare model_id strings, declared order)
  complete(...)   # UNCHANGED behavior: ordered fallback over model_id; bills served candidate id

CONSUMERS that MUST stay byte-identical (read the string view, never the objects):
  - main.create_app: passes settings.model_groups (strings) AND settings.deployments (new) to the router
  - routing_admin_router.get_routing_admin: reads model_router.model_groups (strings) — FILE UNTOUCHED
  - use_cases._check_model_catalog: alias-aware check iterates model_router.model_groups (strings) — UNTOUCHED
```

GLOSSARY deltas (add at freeze): **Deployment** (one normalized member of a model
group: model_id + weight + optional tpm/rpm limit) · **Model group** amended (an
ordered list of Deployments; the bare-string form is sugar for weight-1/no-limit) ·
**Routing strategy** (defined now, implemented in routing-strategy: selects the
primary deployment; orthogonal to fallback + cooldown).

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-12)
Least-sure flag surfaced at freeze: [contract] /admin/routing is NOT extended with
deployment weight/limits — RA8 freezes the EXACT top-level key set and the EXACT
per-candidate key set {model_id,alias,state}, and RA1 asserts model_groups equals
bare-string lists (re-read from tests/routing_admin/test_routing_admin.py this
session). So deployment metadata stays INTERNAL (settings.deployments / router
.deployments) this task; surfacing it on the admin API is deferred to a later
additive task with its own contract. Why this is the risk: if a future slice needs
weights visible via the API, it pays a new frozen-safe admin task rather than
editing RA8. Cost if wrong: a deferred admin-surfacing task (low — no rework of the
data shape, which is the load-bearing freeze here).
Secondary flag: [contract] tpm_limit/rpm_limit are CARRIED but NOT enforced in this
task (enforcement is deployment-limits) — a reviewer must accept a parsed-but-inert
field at this freeze; the alternative (parse+enforce in one task) would break the
breadth-first split and couple the data shape to the limiter.

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% on the new normalization + validation code paths.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_dm1_bare_string_coerces_weight1_nolimit: build Settings(model_groups={"fast":["vendor/a","vendor/b"]}); assert settings.deployments["fast"] == [Deployment("vendor/a",1,None,None), Deployment("vendor/b",1,None,None)]; assert settings.model_groups == {"fast":["vendor/a","vendor/b"]}
  - test_dm2_object_members_parse_and_preserve_order: Settings with one object (weight 3, tpm 100000, rpm 600) + one string; assert deployments[0]==Deployment("vendor/a",3,100000,600), deployments[1]==Deployment("vendor/b",1,None,None); assert model_groups string view order
  - test_dm3_mixed_group_routes_like_v6: FallbackModelRouter built with model_groups(strings)+deployments; force first candidate fail; assert it attempts deployments in declared model_id order and returns served candidate id (reuse v6 fallback test fakes)
  - test_dm4_admin_routing_byte_identical: create_app with an object-member group; GET /admin/routing as owner; assert body["model_groups"]=={"fast":["vendor/a"]}; assert set(body)=={retry_policy,cooldown,model_groups,candidates}; assert every candidate key set=={model_id,alias,state} (RA1/RA8 regression — GREEN-BY-DESIGN, frozen v6 tests must also stay green)
  - test_dm5_weight_nonpositive_rejected: Settings(model_groups={"g":[{"model_id":"m","weight":0}]}) raises ValidationError matching "INVALID_DEPLOYMENT_WEIGHT"
  - test_dm6_limit_nonpositive_rejected: tpm_limit=0 (and a rpm_limit=0 case) raises "INVALID_DEPLOYMENT_LIMIT"
  - test_dm7_missing_model_id_rejected: {"weight":2} raises "DEPLOYMENT_MODEL_ID_REQUIRED"
  - test_dm8_duplicate_model_id_rejected: {"g":["m","m"]} raises "DUPLICATE_DEPLOYMENT"
  - test_dm9_v6_validators_preserved: parametrize empty-list / alias-collision / 6-member cases → "EMPTY_CANDIDATE_LIST" / "ALIAS_COLLIDES_WITH_CANDIDATE" / "TOO_MANY_CANDIDATES" (over the model_id view)
  - test_dm10_empty_config_feature_off: Settings() default → settings.deployments=={} and settings.model_groups=={}; a create_app smoke asserts the plain chat path needs no normalization
</test_plan>

Tests live in: `./tests/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [ ] WIRING (code) — every new symbol is referenced; record where / how confirmed
- [ ] DEAD-CODE (code) — no new unused or orphaned symbol introduced
- [ ] SEMANTIC (prose / non-code) — read in full, not skimmed: <what read · what confirmed>

### GATE RECORD
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
