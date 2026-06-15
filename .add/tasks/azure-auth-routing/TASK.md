# TASK: Azure config + deployment URL routing + api-key auth seam

slug: azure-auth-routing · created: 2026-06-15 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it. -->
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
- `apps/gateway/src/gateway/core/config.py:Settings` (class @ L121, `env_prefix="GATEWAY_"`) — ADD a `# ── Azure OpenAI ──` block of fields (mirrors the `bedrock_*` block @ L225-240): `azure_api_key: str = ""`, `azure_endpoint: str = ""` (e.g. `https://{resource}.openai.azure.com`), `azure_api_version: str = "2024-10-21"` (GA stable default), `azure_deployment_map: ...` (model→deployment; default identity), plus the AAD fields are DEFERRED to azure-aad-auth (declared here as empty-default placeholders only if cheap; else added later). api_key is a SECRET (never logged/echoed/metric-labelled — same comment banner as bedrock).
- `apps/gateway/src/gateway/core/config.py:_UPSTREAM_KEY_ENV_VARS` (tuple @ L29-36) + `validate_upstream_keys` (@ L39) + `EmptyUpstreamKeyError` (@ L20) — ADD `"GATEWAY_AZURE_API_KEY"` to the guarded tuple (set-but-empty → boot fail; absent → provider cleanly disabled). The guard names ONLY the var, never a value.
- NEW `apps/gateway/src/gateway/proxy/infrastructure/azure_config.py` — `resolve_azure_config(settings) -> AzureConfig | None` mirrors `bedrock_sigv4.resolve_aws_credentials` (returns None when any required field falsy → opt-in). `AzureConfig` is a frozen dataclass with `api_key: str = field(repr=False)` (SECRET, mirrors `AwsCredentials.secret_access_key` @ bedrock_sigv4.py:51), `endpoint: str`, `api_version: str`, `deployment_map: Mapping[str,str]`. Plus `AzureEndpoint`-style URL builder `build_url(deployment, op) -> str` = `{endpoint}/openai/deployments/{quoted-deployment}/{op}?api-version={ver}` and `resolve_deployment(model) -> str` (map.get(model, model) = identity default). This URL shape is the one GENUINELY NEW sub-system (no other provider routes by deployment-in-path + api-version query).
- `apps/gateway/src/gateway/main.py` (@ L425-436 chat-adapter guard; @ L552-558 provider guard) — task 1 does NOT wire an adapter yet (no upstream until azure-chat); it only lands config + resolver + URL builder + boot-guard. Registration wiring is azure-chat/azure-embeddings. (Grounded here so §3 names the exact insertion seam.)

Context (working folder): no DB/migration touch. Pure config + a stateless infra helper module + a boot-guard tuple entry. e2e overlay env wiring is deferred to azure-verify.

Honors (patterns / conventions):
- opt-in & byte-identical when absent — `resolve_*() -> X | None`, register only when non-None (PROJECT.md §Key Decisions, bedrock/google/openai precedent).
- SECRET class — `field(repr=False)`, never logged/echoed/in metric labels/span attrs/URLs/cache keys (CONVENTIONS.md security; AwsCredentials precedent).
- design-for-failure — config is pure/total; no IO in task 1 (the IO-bearing upstreams are later tasks with breaker+retry reuse).

Anchors the contract cites: `resolve_azure_config(settings) -> AzureConfig | None`, `AzureConfig` (frozen, api_key repr=False), `AzureConfig.build_url(deployment, op)`, `AzureConfig.resolve_deployment(model)`, `GATEWAY_AZURE_API_KEY` in `_UPSTREAM_KEY_ENV_VARS`, the `GATEWAY_AZURE_*` Settings fields.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Azure OpenAI config resolution + deployment URL routing + api-key auth seam (pure, no IO).
Framings weighed: a pure config-resolution + URL-builder seam consumed by later adapters (chosen) · build the full AzureCompletionUpstream now (rejected — breadth-first: auth+routing is the shared foundation chat/embeddings/AAD all depend on; isolate it first) · inline URL building inside each adapter (rejected — duplicates deployment+api-version logic across chat/embeddings; centralize so ONE frozen contract governs routing).
Must:
<must>
  - resolve_azure_config(settings) returns an AzureConfig iff BOTH azure_api_key AND azure_endpoint are truthy; otherwise None (opt-in; absence is byte-identical to today — mirrors resolve_aws_credentials).
  - AzureConfig is a frozen dataclass; api_key uses field(repr=False) (SECRET — never in repr/str/logs/metrics/URLs/cache keys). Fields: api_key, endpoint, api_version, deployment_map.
  - api_version defaults to a GA-stable value ("2024-10-21") when GATEWAY_AZURE_API_VERSION is unset; operator-overridable.
  - deployment_map defaults to {} (empty) → identity routing; parsed from GATEWAY_AZURE_DEPLOYMENT_MAP as a JSON object (model→deployment).
  - AzureConfig.resolve_deployment(model) returns deployment_map[model] if mapped, else model (identity default).
  - AzureConfig.build_url(deployment, op) returns "{endpoint}/openai/deployments/{quoted-deployment}/{op}?api-version={api_version}", where endpoint has any trailing slash stripped (idempotent — a trailing slash in config must not double up), deployment is URL path-quoted, and op is the caller-supplied OpenAI-relative operation segment ("chat/completions", "embeddings").
  - GATEWAY_AZURE_API_KEY is added to _UPSTREAM_KEY_ENV_VARS so a set-but-empty value fails boot (EmptyUpstreamKeyError) and an absent value cleanly disables Azure.
</must>
Reject:
<reject>
  - GATEWAY_AZURE_API_KEY present but empty/whitespace (boot) -> EmptyUpstreamKeyError "GATEWAY_AZURE_API_KEY" (names ONLY the var, never a value)
  - build_url(deployment="" or whitespace) -> ValueError "AZURE_DEPLOYMENT_REQUIRED" (never emit a malformed deployment URL)
  - partial config (api_key set, endpoint empty — or vice-versa) -> resolve_azure_config returns None (silent disable, NOT an error — opt-in partial = disabled; see ⚠ below)
</reject>
After:
<after>
  - With api_key+endpoint set: app can resolve a non-None AzureConfig; build_url emits a correctly-shaped Azure URL carrying api-version; resolve_deployment maps the client model→deployment. No adapter is wired yet (azure-chat/azure-embeddings own that).
  - With Azure absent: zero new surface; every existing provider/route byte-identical (regression-guarded).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [spec] partial config (api_key set but endpoint empty) SILENTLY disables Azure (returns None) per the bedrock/google precedent, rather than failing boot — lowest confidence because an operator who sets the key but omits/mistypes the endpoint gets a confusing silent "no azure provider" (then a later model-not-found) instead of a clear boot error; if wrong: add an endpoint-required boot check (cheap additive follow-up). Chose None-on-partial for cross-provider consistency.
  - [ ] [contract] default api_version "2024-10-21" is a real GA-stable Azure version — Azure requires api-version on every call; if an operator's resource needs a different version they override via GATEWAY_AZURE_API_VERSION (overridable → low risk).
  - [ ] [spec] op is supplied by the caller as an OpenAI-relative path segment ("chat/completions", "embeddings") — confirmed by the existing surface (openai_provider posts "/embeddings", openrouter "/chat/completions"); task 1 owns only the wrapper, adapters pass op.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Config resolves when api_key + endpoint are both set
  Given settings with azure_api_key="sk-az" and azure_endpoint="https://r.openai.azure.com"
  When resolve_azure_config(settings) is called
  Then it returns an AzureConfig whose endpoint is "https://r.openai.azure.com"
  And api_version defaults to "2024-10-21" and deployment_map is {}

Scenario: Config is absent (opt-out) when neither field is set
  Given settings with azure_api_key="" and azure_endpoint=""
  When resolve_azure_config(settings) is called
  Then it returns None
  And the set of existing providers/adapters is unchanged (byte-identical)

Scenario: Partial config silently disables Azure
  Given settings with azure_api_key="sk-az" and azure_endpoint=""
  When resolve_azure_config(settings) is called
  Then it returns None
  And no error is raised

Scenario: api_key never appears in the AzureConfig repr
  Given an AzureConfig with api_key="super-secret-value"
  When repr(config) is evaluated
  Then the string "super-secret-value" does not appear in it

Scenario: deployment routing is identity when the model is unmapped
  Given an AzureConfig with deployment_map={}
  When resolve_deployment("gpt-4o") is called
  Then it returns "gpt-4o"

Scenario: deployment routing follows the configured map
  Given an AzureConfig with deployment_map={"gpt-4o": "prod-4o"}
  When resolve_deployment("gpt-4o") is called
  Then it returns "prod-4o"

Scenario: build_url emits the Azure deployment URL with api-version
  Given an AzureConfig with endpoint="https://r.openai.azure.com" and api_version="2024-10-21"
  When build_url("prod-4o", "chat/completions") is called
  Then it returns "https://r.openai.azure.com/openai/deployments/prod-4o/chat/completions?api-version=2024-10-21"

Scenario: build_url is idempotent against a trailing slash in the endpoint
  Given an AzureConfig with endpoint="https://r.openai.azure.com/"
  When build_url("prod-4o", "embeddings") is called
  Then the path contains "/openai/deployments/prod-4o/embeddings" with no doubled slash

Scenario: build_url path-quotes a deployment name with reserved characters
  Given an AzureConfig with endpoint="https://r.openai.azure.com"
  When build_url("my deploy", "chat/completions") is called
  Then the deployment segment is percent-encoded ("my%20deploy") in the URL

Scenario: empty-but-present Azure key fails fast at boot
  Given the environment has GATEWAY_AZURE_API_KEY="" (set but empty)
  When validate_upstream_keys(env) is called
  Then it raises EmptyUpstreamKeyError whose message names "GATEWAY_AZURE_API_KEY"
  And the message contains no key value

Scenario: build_url rejects an empty deployment
  Given any AzureConfig
  When build_url("", "chat/completions") is called
  Then it raises ValueError "AZURE_DEPLOYMENT_REQUIRED"
  And no URL is produced
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# Module contract (no HTTP surface — a pure config/routing seam consumed by later adapters).
# New module: apps/gateway/src/gateway/proxy/infrastructure/azure_config.py

@dataclass(frozen=True)
class AzureConfig:
    api_key: str = field(repr=False)        # SECRET — excluded from repr/str
    endpoint: str                            # e.g. "https://r.openai.azure.com" (trailing slash tolerated)
    api_version: str                         # e.g. "2024-10-21"
    deployment_map: Mapping[str, str]        # model -> deployment; {} = identity routing

    def resolve_deployment(self, model: str) -> str:
        # deployment_map.get(model, model)  — identity default
        ...

    def build_url(self, deployment: str, op: str) -> str:
        # returns f"{endpoint.rstrip('/')}/openai/deployments/{quote(deployment, safe='')}/{op}?api-version={api_version}"
        # raises ValueError("AZURE_DEPLOYMENT_REQUIRED") if deployment is empty/whitespace
        ...

def resolve_azure_config(settings: object) -> AzureConfig | None:
    # AzureConfig iff (azure_api_key AND azure_endpoint) both truthy; else None.
    # api_version falls back to settings.azure_api_version (default "2024-10-21").
    # deployment_map from settings.azure_deployment_map (default {}).

# config.py additions (gateway.core.config):
Settings.azure_api_key: str = ""            # GATEWAY_AZURE_API_KEY     (SECRET)
Settings.azure_endpoint: str = ""           # GATEWAY_AZURE_ENDPOINT
Settings.azure_api_version: str = "2024-10-21"   # GATEWAY_AZURE_API_VERSION
Settings.azure_deployment_map: dict[str,str] = {}  # GATEWAY_AZURE_DEPLOYMENT_MAP (JSON object)
_UPSTREAM_KEY_ENV_VARS += ("GATEWAY_AZURE_API_KEY",)   # boot-guard: set-but-empty -> EmptyUpstreamKeyError

Errors:
  EmptyUpstreamKeyError("GATEWAY_AZURE_API_KEY ...")   # boot; names var only, never a value
  ValueError("AZURE_DEPLOYMENT_REQUIRED")              # build_url with empty deployment

Schema: none (no DB). No HTTP route. No adapter wiring yet (azure-chat/azure-embeddings own registration).
Invariant: when resolve_azure_config returns None, behavior is byte-identical to pre-Azure.
```

Least-sure flag surfaced at freeze: [spec] partial Azure config (api_key set, endpoint empty) returns None = SILENT disable rather than a boot error — chosen for cross-provider consistency with resolve_aws_credentials/google. Why it's least-sure: an operator who sets the key but omits the endpoint gets a silent "no azure provider" then a confusing later model-not-found, instead of a clear boot failure. Cost if wrong: a cheap additive endpoint-required boot check (no contract break — strengthens a reject). All other points (URL shape, api-version default, secret-repr, identity routing, boot-guard) are mechanical and confirmed against the bedrock/openai precedents in §0.

Status: FROZEN @ v1 — approved by Tin (auto mode, delegated per standing fully-autonomous mandate; non-security config seam; lowest-confidence flag is a strengthen-only follow-up, not a contract risk)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of azure_config.py (pure, fully exercisable); project floor elsewhere.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_resolve_config_present: settings(api_key+endpoint) → AzureConfig w/ endpoint, default api_version "2024-10-21", deployment_map {}
  - test_resolve_config_absent_returns_none: settings(both empty) → None
  - test_partial_config_returns_none: settings(api_key set, endpoint "") → None, no raise
  - test_api_key_not_in_repr: AzureConfig(api_key="super-secret-value") → "super-secret-value" not in repr(config)
  - test_resolve_deployment_identity: deployment_map {} → resolve_deployment("gpt-4o") == "gpt-4o"
  - test_resolve_deployment_mapped: deployment_map {"gpt-4o":"prod-4o"} → resolve_deployment("gpt-4o") == "prod-4o"
  - test_build_url_shape: endpoint+api_version → build_url("prod-4o","chat/completions") == ".../openai/deployments/prod-4o/chat/completions?api-version=2024-10-21"
  - test_build_url_idempotent_trailing_slash: endpoint "https://r.../" → no doubled slash in path
  - test_build_url_quotes_deployment: build_url("my deploy",...) → "my%20deploy" segment
  - test_empty_azure_key_boot_fail: validate_upstream_keys({"GATEWAY_AZURE_API_KEY":""}) → EmptyUpstreamKeyError, msg names var, contains no value
  - test_build_url_empty_deployment_raises: build_url("","chat/completions") → ValueError("AZURE_DEPLOYMENT_REQUIRED")
</test_plan>

Tests live in: `apps/gateway/tests/azure_auth_routing/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/azure_config.py` `apps/gateway/src/gateway/core/config.py`
Strategy (ordered batches): 1. NEW azure_config.py — AzureConfig dataclass (frozen, api_key repr=False) + resolve_deployment + build_url + resolve_azure_config. 2. config.py — add GATEWAY_AZURE_* Settings fields + append "GATEWAY_AZURE_API_KEY" to _UPSTREAM_KEY_ENV_VARS.
Safety rule (feature-specific): api_key MUST be field(repr=False) and MUST NOT appear in build_url output, any log, or any exception message (the boot-guard names only the var). build_url is total — never emit a URL with an empty deployment.
Code lives in: `apps/gateway/src/gateway/proxy/infrastructure/azure_config.py` (+ config.py edit)
Constraints: do NOT change any test or the contract; allow-list packages only (stdlib urllib.parse.quote + existing pydantic-settings); no IO; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 11/11 azure_auth_routing; no-DB floor 146/146; pyright 0; ruff clean.
- [x] coverage did not decrease — azure_config.py fully exercised (every branch + both reject paths).
- [x] no test or contract was altered during build — only the two declared §5 src files written.
- [x] the green was EARNED, not gamed — adversarial refute-read: tests assert EXACT behavior (full-URL string equality, exact secret-absence in repr, exact error types/messages), not substrings or truthiness; build_url/resolve_azure_config are general (any endpoint/deployment/op/model), not fixture-shaped; no stubbed-away logic. No cheat found.
- [x] concurrency / timing of the risky operation is safe — pure, IO-free, stateless; frozen dataclass is immutable/thread-safe. No IO until the later adapter tasks (which reuse breaker+retry).
- [x] no exposed secrets, injection openings, or unexpected dependencies — api_key is field(repr=False), never in URL output (only deployment/op/api_version enter the URL) or any error message (boot-guard names the var only); deployment is quote(safe="")-encoded → no path injection; deps are stdlib only (urllib.parse.quote, dataclasses, collections.abc.Mapping).
- [x] layering & dependencies follow CONVENTIONS.md — infrastructure module imports no domain/application; config additions sit in core.config beside the bedrock block; opt-in resolve_*()->X|None + SECRET-class repr=False precedents honored.
- [x] a person reviewed and approved the change — AUTO-RESOLVED (autonomy: auto): non-security, IO-free config seam on a frozen contract, complete green evidence, refute-read clean → explicit auto-PASS (not a skip).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — AzureConfig + resolve_azure_config + build_url + resolve_deployment are exercised by the 11-test suite (proves they work end-to-end). config.py fields + the "GATEWAY_AZURE_API_KEY" boot-guard entry are LIVE at boot (Settings instantiated + validate_upstream_keys runs in create_app). The azure_config functions are intentionally NOT yet wired into a main.py adapter — the deliberate breadth-first foundation seam consumed by azure-chat/azure-embeddings (next tasks), mirroring bedrock-sigv4 (v20 task 1) which existed before bedrock-chat wired it.
- [x] DEAD-CODE (code) — no orphan: DEFAULT_API_VERSION is used by resolve_azure_config; all four AzureConfig fields + both methods are test-covered; config fields feed the (next-task) registration + the live boot-guard. Nothing unused.
- [x] SEMANTIC (prose / non-code) — n/a (code task); the §3 contract was read in full and the build matches it field-for-field.

### GATE RECORD
Outcome: PASS
Evidence: 11/11 azure_auth_routing green · no-DB floor 146/146 · pyright 0/0 · ruff clean · refute-read clean · opt-in byte-identical invariant held (resolve→None path). LIVE double-pass is deferred to azure-verify (this task is a pure offline seam — no wire surface to exercise live yet).
Reviewed by: auto (autonomy: auto; non-security config seam) · date: 2026-06-15

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of resolve_azure_config→None when api_key set (signals the partial-config ⚠ assumption biting in the field); per-deployment build_url shape errors.
Spec delta for the next loop: azure-chat consumes AzureConfig.build_url("…","chat/completions") + the api-key header (api-key: <key>, NOT Authorization: Bearer); azure-aad-auth will swap the auth header when AAD is configured. The endpoint-required boot check (the ⚠ assumption) is a candidate strengthen-only follow-up if field telemetry shows confused operators.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [ADD · folded] The scope-snapshot manifest includes ANY file present at the tests→build snapshot, incl. apps/gateway/.ruff_cache (created when ruff formats the test file DURING the tests phase). At gate, `touched = changed ∪ added ∪ DELETED` (add.py:2617) — so deleting a snapshotted cache COUNTS as an out-of-scope touch. The v20 "clean before gate" lesson is INCOMPLETE: cleaning a cache that was already in the snapshot is itself the violation. Correct fix = ensure transient dirs are ABSENT before the tests→build snapshot (point RUFF_CACHE_DIR outside the repo, or clean before re-snapshotting), then RE-SNAPSHOT clean (phase tests→advance→advance) and gate WITHOUT regenerating. Evidence: this task's gate failed twice on apps/gateway/.ruff_cache (deleted-since-snapshot) until a clean re-snapshot → PASS.
- [TDD · folded] A pure, IO-free config/routing seam landed FIRST (before any adapter) is a high-leverage breadth-first pattern: the URL builder + secret-class + opt-in resolver get a frozen contract + full unit coverage offline, so azure-chat/embeddings/aad inherit a proven routing primitive. Evidence: 11/11 offline tests fully exercise routing+secrets with zero docker/network.
