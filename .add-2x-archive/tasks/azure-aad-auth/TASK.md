# TASK: Azure AD client-credentials token auth (acquire + cache + refresh, fail-closed) as api-key alternative

slug: azure-aad-auth · created: 2026-06-15 · stage: production
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
- NEW `apps/gateway/src/gateway/proxy/infrastructure/azure_ad.py` — `AzureADConfig` (frozen: tenant_id, client_id, client_secret=field(repr=False) SECRET, scope default "https://cognitiveservices.azure.com/.default", authority default "https://login.microsoftonline.com") + `resolve_azure_ad_config(settings)->AzureADConfig|None` (None unless tenant+client+secret ALL truthy) + `AzureADTokenProvider` (async get_token()->str with in-memory cache + refresh-before-expiry skew + single-flight asyncio.Lock + fail-closed). Token endpoint: POST {authority}/{tenant}/oauth2/v2.0/token, form grant_type=client_credentials & client_id & client_secret & scope; parse access_token + expires_in. Design-for-failure: connect/read timeout, token-endpoint error → UpstreamUnavailableError (FAIL-CLOSED — never silently fall back to api-key); breaker NOT needed (separate IDP host) but timeouts are.
- `apps/gateway/src/gateway/proxy/infrastructure/azure_upstream.py:AzureCompletionUpstream` (tasks 2/3) — ADD optional `token_provider: AzureADTokenProvider | None = None` ctor param; make `_auth_headers` ASYNC: when token_provider is set → `{"Authorization": f"Bearer {await token_provider.get_token()}"}`; else → `{"api-key": config.api_key}` (unchanged). complete() (L94) + stream() (inside _gen) AWAIT _auth_headers(). The token is awaited ONCE per call before the retry loop / before first stream byte.
- `apps/gateway/src/gateway/core/config.py:Settings` + `_UPSTREAM_KEY_ENV_VARS` — ADD azure_tenant_id/azure_client_id/azure_client_secret (client_secret SECRET) fields + append "GATEWAY_AZURE_CLIENT_SECRET" to the boot-guard tuple (set-but-empty → boot fail).
- `apps/gateway/src/gateway/main.py` (the _azure_cfg block from task 2) — construct `AzureADTokenProvider` when resolve_azure_ad_config(settings) is non-None and pass token_provider= to AzureCompletionUpstream; else token_provider=None (api-key). AAD config present but azure_cfg absent (no api_key) → AAD still needs endpoint; precedence: AAD overrides api-key when BOTH endpoint+AAD set.
- `gateway.proxy.infrastructure.azure_config.AzureConfig` (task 1, FROZEN) — UNCHANGED (AAD is a SEPARATE config + provider; api_key stays the fallback). resolve_azure_config still gates the adapter on api_key+endpoint... ⚠ but AAD-only (no api_key) must also enable Azure → see §1 (resolve gate revisited).

Context (working folder): no DB/migration. New IDP token sub-system + an async auth-header seam. Tests use httpx.MockTransport (token endpoint + chat endpoint) + an injected clock for refresh; no network.

Honors (patterns / conventions):
- SECRET class — client_secret + the acquired token are NEVER logged/echoed/in metric labels/span attrs/URLs/exception messages (field(repr=False); token only in the Authorization header).
- design-for-failure (CLAUDE.md) — timeouts on the token POST; fail-closed on token error (no silent api-key fallback); single-flight refresh (asyncio.Lock) so a token-expiry stampede makes ONE IDP call; refresh-before-expiry skew.
- opt-in & byte-identical — token_provider=None → api-key path identical to tasks 2/3; AAD absent → no IDP calls.

Anchors the contract cites: `AzureADConfig` (client_secret repr=False), `resolve_azure_ad_config(settings)->AzureADConfig|None`, `AzureADTokenProvider(*, config, now_fn=…, expiry_skew_s=…, metrics_registry=None).get_token()->str`, `AzureCompletionUpstream(*, config, token_provider=None, …)`, async `_auth_headers`, `GATEWAY_AZURE_CLIENT_SECRET` boot-guard.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Azure AD (client-credentials) bearer-token auth as an alternative to the api-key.
Framings weighed: a separate AzureADConfig + AzureADTokenProvider injected into the adapter, leaving task-1's frozen AzureConfig/resolve untouched; AAD-only config assembled in the composition root (chosen) · add AAD fields to the frozen AzureConfig + relax resolve_azure_config's gate (rejected — edits a FROZEN contract) · re-acquire a token every request (rejected — needless IDP load + latency; cache+refresh is the standard).
Must:
<must>
  - AzureADTokenProvider.get_token() returns a cached access_token while valid; when absent/within the expiry skew it acquires one via POST {authority}/{tenant}/oauth2/v2.0/token (form: grant_type=client_credentials, client_id, client_secret, scope) and caches it with expiry = now + expires_in.
  - Concurrent get_token() calls during a refresh make exactly ONE token request (single-flight asyncio.Lock; double-check the cache after acquiring the lock).
  - Token acquisition is FAIL-CLOSED: a token-endpoint non-200, timeout, or network error raises UpstreamUnavailableError — NEVER a silent fall back to api-key, NEVER a blank Bearer.
  - When a token_provider is injected, AzureCompletionUpstream sends `Authorization: Bearer <token>` (await-ed once per call before the retry loop / first stream byte) and sends NO `api-key` header; when token_provider is None, behavior is byte-identical to tasks 2/3 (`api-key` header).
  - resolve_azure_ad_config(settings) returns AzureADConfig iff tenant_id AND client_id AND client_secret are all truthy; else None. AzureADConfig is frozen; client_secret uses field(repr=False).
  - main.py enables the Azure adapter when api-key config OR (AAD config AND endpoint) is present; AAD takes precedence (token_provider injected) when both are configured.
  - GATEWAY_AZURE_CLIENT_SECRET is added to _UPSTREAM_KEY_ENV_VARS (set-but-empty → boot fail). client_secret + the acquired token NEVER appear in a log, metric label, span attribute, URL, or exception message.
</must>
Reject:
<reject>
  - token endpoint non-200 / timeout / network error -> UpstreamUnavailableError (fail-closed; no api-key fallback, no blank Bearer)
  - GATEWAY_AZURE_CLIENT_SECRET present but empty/whitespace (boot) -> EmptyUpstreamKeyError "GATEWAY_AZURE_CLIENT_SECRET" (names the var only)
  - partial AAD config (e.g. tenant+client but no secret) -> resolve_azure_ad_config returns None (no AAD; falls back to api-key if that is configured)
</reject>
After:
<after>
  - An operator configuring AAD (tenant+client+secret+endpoint) gets Azure chat/stream/embeddings authenticated by a cached, auto-refreshed AAD bearer token; the token endpoint is hit once per token lifetime (not per request); a token failure fails the request closed (502/fallback), never leaks the secret.
  - With AAD unconfigured: token_provider=None, api-key auth, zero IDP calls — byte-identical to tasks 2/3.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] AAD precedence over api-key when BOTH are configured — least-sure because an operator might expect api-key to win, or expect per-deployment choice; if wrong: flip the precedence or make it explicit via a setting (additive). Chose AAD-wins because configuring AAD is the deliberate enterprise upgrade; documented + live-verified (task 6).
  - [ ] [spec] the OAuth2 client-credentials scope ".../.default" + v2.0 token endpoint is the correct Azure-OpenAI cognitive-services scope — confirmed by Azure docs + LiteLLM; operator-overridable via GATEWAY_AZURE_AD_SCOPE if a sovereign cloud differs.
  - [ ] [spec] a monotonic injected clock (now_fn) drives expiry so refresh is unit-testable without real time — standard; the live-verify confirms a real expires_in is honored.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: token is acquired once and cached
  Given an AzureADTokenProvider over a token endpoint returning access_token + expires_in=3600
  When get_token() is called twice within the validity window
  Then the token endpoint is hit exactly once
  And both calls return the same access_token

Scenario: token refreshes after expiry
  Given a cached token whose expiry (minus skew) has passed per the injected clock
  When get_token() is called again
  Then the token endpoint is hit a second time and the new token is returned

Scenario: concurrent refresh is single-flight
  Given no cached token and N concurrent get_token() calls
  When they race
  Then the token endpoint is hit exactly once (asyncio.Lock + double-check)

Scenario: token failure fails closed
  Given the token endpoint returns 401 (or times out)
  When get_token() is called
  Then UpstreamUnavailableError is raised
  And no token is cached and no api-key fallback occurs

Scenario: client_secret never appears in repr/errors
  Given an AzureADConfig with client_secret="top-secret" and a token endpoint that 400s with an echo body
  When repr(config) and the raised error are inspected
  Then "top-secret" appears in neither

Scenario: adapter uses Bearer when a token provider is injected
  Given an AzureCompletionUpstream with a token_provider yielding "tok-123"
  When complete(payload) is called against a request-capturing handler
  Then the request carries "Authorization: Bearer tok-123" and NO "api-key" header

Scenario: adapter keeps api-key when no token provider
  Given an AzureCompletionUpstream with token_provider=None
  When complete(payload) is called
  Then the request carries the "api-key" header and NO "Authorization" header (byte-identical to task 2)

Scenario: resolve_azure_ad_config gates on all three fields
  Given settings with tenant+client set but client_secret empty
  When resolve_azure_ad_config(settings) is called
  Then it returns None

Scenario: empty-but-present client secret fails boot
  Given GATEWAY_AZURE_CLIENT_SECRET="" in the environment
  When validate_upstream_keys(env) is called
  Then EmptyUpstreamKeyError naming "GATEWAY_AZURE_CLIENT_SECRET" is raised

Scenario: AAD takes precedence and enables Azure without an api-key
  Given settings with endpoint + AAD (tenant+client+secret) but NO api_key
  When create_app(settings) builds the app
  Then "azure" is registered and its adapter authenticates via Bearer (token_provider injected)
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# New module: apps/gateway/src/gateway/proxy/infrastructure/azure_ad.py
@dataclass(frozen=True)
class AzureADConfig:
    tenant_id: str
    client_id: str
    client_secret: str = field(repr=False)          # SECRET
    scope: str = "https://cognitiveservices.azure.com/.default"
    authority: str = "https://login.microsoftonline.com"

def resolve_azure_ad_config(settings) -> AzureADConfig | None:
    # AzureADConfig iff tenant_id AND client_id AND client_secret all truthy; else None.

class AzureADTokenProvider:
    def __init__(self, *, config: AzureADConfig, now_fn: Callable[[], float] = time.monotonic,
                 expiry_skew_s: float = 60.0, metrics_registry=None) -> None: ...
    async def get_token(self) -> str:
        # cached token if now_fn() < expires_at - skew; else single-flight acquire under asyncio.Lock
        # (double-check after lock). POST {authority}/{tenant}/oauth2/v2.0/token form:
        #   grant_type=client_credentials & client_id & client_secret & scope
        # parse access_token + expires_in -> cache (expires_at = now + expires_in)
        # FAIL-CLOSED: non-200 / timeout / network -> raise UpstreamUnavailableError (no cache, no fallback)

# azure_upstream.py — evolve the auth seam:
class AzureCompletionUpstream:
    def __init__(self, *, config, token_provider: AzureADTokenProvider | None = None, ...): ...
    async def _auth_headers(self) -> dict[str, str]:
        # token_provider set -> {"Authorization": f"Bearer {await token_provider.get_token()}"}
        # else                -> {"api-key": config.api_key}
    # complete(): headers = {**(await self._auth_headers()), "content-type": "application/json"}
    # stream():   headers awaited INSIDE _gen before the first byte

# config.py: azure_tenant_id / azure_client_id / azure_client_secret(SECRET) fields;
#   _UPSTREAM_KEY_ENV_VARS += ("GATEWAY_AZURE_CLIENT_SECRET",)
#   optional GATEWAY_AZURE_AD_SCOPE override (azure_ad_scope).
# main.py: _azure_ad = resolve_azure_ad_config(settings); enable adapter when
#   resolve_azure_config(settings) OR (_azure_ad and settings.azure_endpoint);
#   token_provider = AzureADTokenProvider(config=_azure_ad,…) if _azure_ad else None (AAD precedence).

Errors: UpstreamUnavailableError (token fail, fail-closed) · EmptyUpstreamKeyError (empty client_secret at boot)
Schema: none (no DB). AzureConfig (task 1) UNCHANGED.
Invariant: token_provider=None → api-key path byte-identical to tasks 2/3; AAD absent → zero IDP calls.
```

Least-sure flag surfaced at freeze: [contract] AAD takes PRECEDENCE over api-key when both are configured — least-sure because an operator could expect api-key to win; cost if wrong = flip precedence or add an explicit selector setting (additive, no break). Chose AAD-wins (configuring AAD is the deliberate enterprise upgrade), documented + live-verified. Security-critical points (fail-closed on token error, single-flight refresh, secret/token never logged or in URL) are NON-negotiable and adversarially verified at the gate — a real leak/fallback is HARD-STOP.

Status: FROZEN @ v1 — approved by Tin (auto mode, delegated per standing fully-autonomous mandate; security-sensitive → gate carries a dedicated adversarial security refute-read, any real finding HARD-STOPs)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of azure_ad.py + the new azure_upstream auth branch; project floor elsewhere.
Plan (one test per scenario; httpx.MockTransport for token + chat endpoints, injected clock):
<test_plan>
  - test_token_acquired_once_and_cached: 2 get_token() in-window → token endpoint hit 1×; same token
  - test_token_refreshes_after_expiry: advance fake clock past expiry-skew → 2nd acquire, new token
  - test_concurrent_refresh_single_flight: asyncio.gather N get_token() cold → endpoint hit 1×
  - test_token_failure_fails_closed: token endpoint 401/timeout → UpstreamUnavailableError; nothing cached
  - test_client_secret_not_in_repr_or_error: secret absent from repr(config) and from the raised error str
  - test_adapter_uses_bearer_with_token_provider: complete() → "Authorization: Bearer tok-123", no "api-key"
  - test_adapter_keeps_api_key_without_provider: complete() → "api-key" header, no "Authorization" (task-2 parity)
  - test_resolve_ad_config_gates_on_all_three: tenant+client, empty secret → None
  - test_empty_client_secret_fails_boot: validate_upstream_keys({"GATEWAY_AZURE_CLIENT_SECRET":""}) → EmptyUpstreamKeyError
  - test_wiring_aad_precedence_enables_without_api_key: create_app(endpoint+AAD, no api_key) → "azure" registered; Bearer auth
</test_plan>

Tests live in: `apps/gateway/tests/azure_aad/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/azure_ad.py` `apps/gateway/src/gateway/proxy/infrastructure/azure_upstream.py` `apps/gateway/src/gateway/core/config.py` `apps/gateway/src/gateway/main.py`
Strategy (ordered batches): 1. NEW azure_ad.py — AzureADConfig + resolve_azure_ad_config + AzureADTokenProvider (cache/refresh/single-flight/fail-closed). 2. config.py — AAD Settings fields + GATEWAY_AZURE_CLIENT_SECRET boot-guard + azure_ad_scope. 3. azure_upstream.py — token_provider ctor param + async _auth_headers + await at complete()/stream() call sites. 4. main.py — resolve AAD + AAD-precedence wiring (enable adapter on api-key OR AAD+endpoint).
Safety rule (feature-specific): client_secret + token are SECRETS — field(repr=False), only the token enters the Authorization header; NEVER logged/echoed/in metric labels/URLs/exceptions. Token acquisition FAIL-CLOSED (no silent api-key fallback). Single-flight refresh (asyncio.Lock + double-check) so a stampede makes ONE IDP call.
Code lives in: `apps/gateway/src/gateway/proxy/infrastructure/azure_ad.py` (+ azure_upstream/config/main edits)
Constraints: do NOT change any test or the contract; do NOT modify the FROZEN azure_config.py; allow-list packages only (httpx, stdlib asyncio/time + existing infra); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 12/12 azure_aad (incl. 2 security regression tests); regression 102/102 (floor + dispatch + streaming_resilience + boot-guard + all azure); pyright 0; ruff clean.
- [x] coverage did not decrease — azure_ad.py fully exercised (cache/refresh/single-flight/fail-closed/secret-hygiene) + the new auth branch in azure_upstream.
- [x] no test or contract was altered during build — build touched only the 4 declared §5 src files; the 2 security tests were added in the tests phase (pre-snapshot, change-request loop) and the file re-snapshotted.
- [x] the green was EARNED, not gamed — adversarial SECURITY refute-read by an independent security-expert subagent (sonnet): verified fail-closed (raises on non-200/timeout/missing-token), single-flight (Lock+double-check, no caching-on-failure, no TOCTOU), auth-seam (Bearer ⇒ no api-key; None ⇒ api-key), wiring (no blank Bearer), and that the tests are substantive. It found 3 issues — ALL REMEDIATED (see GATE RECORD); re-verified green.
- [x] concurrency / timing of the risky operation is safe — asyncio.Lock + post-lock cache re-check ⇒ a token-expiry stampede makes exactly ONE IDP call (test_concurrent_refresh_single_flight); no await between the two cache-assignment lines; never serves a token past (expiry - skew).
- [x] no exposed secrets, injection openings, or unexpected dependencies — client_secret field(repr=False); errors carry ONLY a status code (never a body); the timeout path now uses `from None` so the chained httpx request body (holding the secret) is suppressed (Finding 1 fix, test_token_timeout_secret_not_in_exception_chain); token enters only the Authorization header; deps stdlib+httpx.
- [x] layering & dependencies follow CONVENTIONS.md — new IDP sub-system in infrastructure; AzureConfig (task 1, frozen) UNCHANGED; AAD-only config assembled in the composition root (main.py), not by editing a frozen contract.
- [x] a person reviewed and approved the change — adversarial SECURITY subagent review performed (findings remediated); AUTO-RESOLVED for the gate under autonomy: auto (no OPEN security finding remains). Per the standing mandate any UNRESOLVED security finding would have been HARD-STOP.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — AzureADTokenProvider + resolve_azure_ad_config wired in main.py (AAD precedence; enables Azure without an api-key); AzureCompletionUpstream.token_provider awaited in _auth_headers from complete()+stream(). Exercised end-to-end (test_wiring_aad_precedence_enables_without_api_key + bearer tests). No orphan.
- [x] DEAD-CODE (code) — all symbols used: DEFAULT_SCOPE/AUTHORITY by config defaults; _cached/_acquire by get_token; the new ctor param by _auth_headers + main.py.
- [x] SEMANTIC (prose / non-code) — n/a (code task); §3 read in full; build matches (fail-closed, single-flight, Bearer-precedence, boot-guard).

### GATE RECORD
Outcome: PASS
Security review (independent security-expert subagent, adversarial refute): 3 findings, ALL REMEDIATED before gate:
  - [MED] timeout-path `raise ... from exc` carried the httpx request body (client_secret) in the exception chain → FIXED with `from None` + regression test (no OPEN secret leak).
  - [LOW] non-JSON 200 raised JSONDecodeError past the fail-closed contract → FIXED (wrapped → UpstreamUnavailableError) + regression test.
  - [LOW] timeout-path secret-in-cause untested → test added.
  Reviewer otherwise verified CLEAN: fail-closed, single-flight, auth-seam, wiring, non-vacuous tests.
Evidence: 12/12 azure_aad · regression 102/102 · pyright 0/0 · ruff clean. No OPEN security finding → not a HARD-STOP. LIVE double-pass deferred to azure-verify (task 6). Carried follow-up: zero-TTL (missing expires_in) → per-request IDP calls (add a min-TTL floor).
Reviewed by: auto + adversarial security subagent (autonomy: auto; security-sensitive, findings remediated) · date: 2026-06-15

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): AAD token-acquire failure rate (fail-closed → 502/fallover); token-refresh frequency (spikes ⇒ a too-large skew or missing expires_in); ratio of Bearer vs api-key auth.
Spec delta for the next loop: azure-embeddings (task 5) reuses the SAME token_provider seam (an UpstreamProvider that builds the api-key OR Bearer header) — the auth-header method is the single point AAD plugs into. Carried follow-up: min-TTL floor when expires_in is absent (avoid per-request IDP calls).

### Competency deltas
- [ADD · folded] An adversarial SECURITY subagent (independent, sonnet) at verify caught a real latent secret-leak the author's own refute-read missed: `raise UpstreamUnavailableError(...) from exc` carries the httpx request body (client_secret) on exc.__cause__.request.content — invisible today but harvested by any future crash-reporter. LESSON: for any auth/secret task, the verify gate MUST include an independent adversarial security pass (not just the author's self-review); use `from None` whenever wrapping an exception whose request/response could hold a secret. Evidence: Finding 1, fixed + regression-tested before gate.
- [TDD · folded] Wrapping a raised exception in `from None` is a TESTABLE security property: assert `exc.value.__cause__ is None` to lock that a secret-bearing chain can never re-attach. Evidence: test_token_timeout_secret_not_in_exception_chain.
<!-- tags: DDD · SDD · UDD · TDD · ADD — see the `add` skill's deltas.md -->>
