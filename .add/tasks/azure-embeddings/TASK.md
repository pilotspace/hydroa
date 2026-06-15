# TASK: Azure OpenAI embeddings provider (deployment-routed, api-key/AAD)

slug: azure-embeddings · created: 2026-06-15 · stage: production
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
  - `apps/gateway/src/gateway/proxy/infrastructure/azure_embeddings.py:AzureEmbeddingsProvider` — NEW; implements UpstreamProvider Protocol (post_json / post_multipart / stream_bytes). Mirrors `openai_provider.py:OpenAIDirectProvider` (the OpenAI-compatible sibling) but routes by Azure deployment + injects Azure auth.
  - `apps/gateway/src/gateway/proxy/infrastructure/azure_config.py:AzureConfig` — FROZEN @ v1 (task 1). Reused read-only: `resolve_deployment(model)` + `build_url(deployment, "embeddings")` + `api_key`.
  - `apps/gateway/src/gateway/proxy/infrastructure/azure_ad.py:AzureADTokenProvider` — task 4. Reused read-only: `get_token()` is the SINGLE AAD plug-in point (the shared-instance token cache).
  - `apps/gateway/src/gateway/proxy/domain/ports.py:UpstreamProvider` — Protocol (runtime_checkable) the new class must satisfy.
  - `apps/gateway/src/gateway/proxy/infrastructure/circuit_breaker.py:CircuitBreaker` — per-instance breaker (same as every sibling adapter).
  - `apps/gateway/src/gateway/main.py` create_app provider-registry block (~L575-597) — register `_providers["azure"]` iff `_azure_cfg` (the SAME guard the chat adapter uses), passing the SHARED `_azure_token_provider`.
Context (working folder): `apps/gateway/src/gateway/proxy/infrastructure/` (adapter) + `apps/gateway/tests/azure_embeddings/` (red suite). EmbeddingsUseCase (application/embeddings_use_case.py) calls `provider_adapter.post_json("/embeddings", body)` — the `path` arg is IGNORED by deployment-routed providers (Bedrock does the same).
Honors (patterns / conventions):
  - PROJECT.md TYPED_EXTRAS_NO_DISPATCH — provider selected by deterministic dict lookup (`select_provider`); NO inspect.signature/hasattr.
  - CONVENTIONS.md byte-passthrough — Azure is OpenAI-compatible, so the embeddings body AND response need ZERO translation (unlike BedrockEmbeddingsProvider's Titan translation). post_json forwards `body` as-is and returns `resp.json()` as-is; billing reads `usage` from the OpenAI-shaped response (use_case layer).
  - design-for-failure (CLAUDE.md) — connect/non-stream timeouts; per-instance CircuitBreaker; 5xx + Timeout/Network → UpstreamUnavailableError; AAD token failure fails CLOSED (propagates, never blank auth).
  - Secret hygiene — `api_key` / bearer token NEVER logged, echoed, in metric labels, span attrs, exception messages, or URLs.
Anchors the contract cites: `AzureEmbeddingsProvider`, `UpstreamProvider`, `AzureConfig.build_url`, `AzureADTokenProvider.get_token`, `_providers["azure"]`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Azure OpenAI embeddings adapter — POST /v1/embeddings routed to an Azure deployment, authenticated by api-key OR Azure AD bearer, OpenAI-compatible body/response (no translation).
Framings weighed: thin OpenAI-compatible passthrough with Azure URL+auth (chosen) · full request/response translation like Bedrock/Titan (rejected — Azure speaks the OpenAI embeddings wire shape natively, translation would be dead code) · subclass OpenAIDirectProvider (rejected — auth seam + deployment routing differ enough that composition-by-mirroring is clearer than inheritance).
Must:
<must>
  - Implement the UpstreamProvider Protocol: `post_json(path, payload) -> (status, json)`, `post_multipart(...)`, `stream_bytes(...)`.
  - On post_json: resolve the Azure deployment from `payload["model"]` (config.resolve_deployment), build the URL via config.build_url(deployment, "embeddings") (deployment-in-path + api-version query), POST the payload UNCHANGED as JSON, return (status, resp.json()) UNCHANGED.
  - Auth header seam: when a token_provider is injected → `Authorization: Bearer <token>` (await get_token, NO api-key header); otherwise → `api-key: <config.api_key>` (NO Authorization header). Identical seam semantics to AzureCompletionUpstream._auth_headers.
  - Reuse the SHARED AzureADTokenProvider instance (one token cache across chat + embeddings) — wired in main.py.
  - Resilience: per-instance CircuitBreaker.guard() before the call; 5xx → on_upstream_error() + UpstreamUnavailableError; success/4xx → record_success(); Timeout/Network → on_upstream_error() + UpstreamUnavailableError.
  - 4xx (incl. Azure content_filter, which is OpenAI-shaped) pass THROUGH as (status, body) — never raised (mirrors OpenAIDirectProvider).
  - post_multipart / stream_bytes raise UpstreamUnavailableError("azure-embeddings: unsupported modality") — embeddings models never reach them.
  - Register `_providers["azure"] = AzureEmbeddingsProvider(...)` in create_app iff Azure config is present, passing the shared token_provider; opt-in + byte-identical when absent.
</must>
Reject:
<reject>
  - empty/missing deployment (build_url on empty) -> raises ValueError("AZURE_DEPLOYMENT_REQUIRED") (frozen task-1 invariant; never emit a malformed URL)
  - upstream 5xx / Timeout / Network -> "UpstreamUnavailableError" (use_case maps to ERR_UPSTREAM_UNAVAILABLE 503)
  - AAD token acquisition failure -> "UpstreamUnavailableError" (fail-closed; never POST with a blank/absent Authorization)
</reject>
After:
<after>
  - A catalog model with provider="azure" + modality="embedding" routes POST /v1/embeddings to the Azure deployment URL, authed per config, billed exactly from the OpenAI-shaped usage in the response.
  - With Azure unconfigured, the registry has no "azure" key → select_provider raises ERR_PROVIDER_UNAVAILABLE (byte-identical to pre-task state).
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The Azure embeddings response body is OpenAI-shaped (object/data/model/usage) so NO response translation is needed and `usage` bills directly — lowest confidence because Azure echoes the *deployment* (not the client model) in some fields; if wrong: billing reads usage fine regardless (usage shape is stable), but the echoed `model` may differ — acceptable (passthrough returns what Azure sent, same as the chat adapter). Verified at the live double-pass (task 6).
  - [x] post_json's `path` arg ("/embeddings") is ignored by deployment-routed providers — confirmed: BedrockEmbeddingsProvider ignores it identically; the op segment is hard-coded "embeddings".
  - [x] The shared token_provider seam is the single AAD plug-in point — confirmed against §7 spec delta from task 4 and AzureCompletionUpstream._auth_headers.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: api-key embeddings call routes to the deployment URL
  Given an AzureEmbeddingsProvider with config (no token_provider) and deployment_map {"text-embedding-3-small": "prod-embed"}
  When post_json("/embeddings", {"model": "text-embedding-3-small", "input": "hello"}) is called
  Then the request hits {endpoint}/openai/deployments/prod-embed/embeddings?api-version=... with header api-key:<key>
  And NO Authorization header is sent
  And it returns (200, the OpenAI-shaped body) unchanged

Scenario: AAD embeddings call sends a bearer token
  Given an AzureEmbeddingsProvider with a token_provider returning "tok-xyz"
  When post_json("/embeddings", {"model": "m", "input": ["a", "b"]}) is called
  Then the request carries Authorization: Bearer tok-xyz
  And NO api-key header is sent

Scenario: identity deployment routing for an unmapped model
  Given an AzureEmbeddingsProvider with deployment_map {} (empty)
  When post_json("/embeddings", {"model": "my-embed", "input": "x"}) is called
  Then the URL deployment segment is "my-embed" (identity)
  And the body is forwarded unchanged

Scenario: upstream 500 fails closed
  Given an AzureEmbeddingsProvider whose endpoint returns 500
  When post_json is called
  Then UpstreamUnavailableError is raised
  And the circuit breaker recorded the failure (state unchanged otherwise)

Scenario: 4xx content_filter passes through
  Given an AzureEmbeddingsProvider whose endpoint returns 400 {"error": {"code": "content_filter"}}
  When post_json is called
  Then it returns (400, the error body) — not raised
  And the breaker recorded success (4xx is not an upstream outage)

Scenario: AAD token failure fails closed before any POST
  Given an AzureEmbeddingsProvider whose token_provider.get_token raises UpstreamUnavailableError
  When post_json is called
  Then UpstreamUnavailableError propagates
  And NO request was sent to the embeddings endpoint

Scenario: wiring registers the azure embeddings provider with shared auth
  Given Settings with azure_endpoint + azure_api_key set
  When create_app builds the provider registry
  Then provider_registry.get("azure") is an AzureEmbeddingsProvider
  And with Azure unconfigured, provider_registry.get("azure") is None
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
class AzureEmbeddingsProvider:                                  # proxy/infrastructure/azure_embeddings.py
    def __init__(self, *, config: AzureConfig,
                 token_provider: AzureADTokenProvider | None = None,
                 metrics_registry: MetricsRegistry | None = None) -> None
    async def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]
        # deployment = config.resolve_deployment(payload["model"])
        # url        = config.build_url(deployment, "embeddings")
        # headers    = {**(await self._auth_headers()), "content-type": "application/json"}
        # POST url json=payload  → 5xx/Timeout/Network: UpstreamUnavailableError ; else (status, resp.json())
    async def post_multipart(self, path, files, data) -> tuple[int, dict[str, Any]]   # raise UpstreamUnavailableError("azure-embeddings: unsupported modality")
    def stream_bytes(self, path, payload) -> AsyncIterator[bytes]                      # async-gen that raises same
    async def _auth_headers(self) -> dict[str, str]
        # token_provider → {"Authorization": f"Bearer {await get_token()}"} ; else {"api-key": config.api_key}

main.py create_app provider-registry block:
    if _azure_cfg:
        _providers["azure"] = AzureEmbeddingsProvider(
            config=_azure_cfg, token_provider=_azure_token_provider,
            metrics_registry=app.state.metrics_registry)
    # opt-in: absent _azure_cfg → no "azure" key → byte-identical pre-task behavior

Schema: none (no DB writes; reads catalog ModelRow.provider/modality via the existing use_case).
```

Least-sure flag surfaced at freeze: [contract] the Azure embeddings response body is OpenAI-shaped (object/data/model/usage), so post_json returns resp.json() UNCHANGED and `usage` bills directly — why uncertain: Azure may echo the deployment in `model` rather than the client model; cost: none material (usage shape is stable; passthrough returns exactly what Azure sent, identical to the chat adapter). Confirmed at the task-6 live double-pass.
Status: FROZEN @ v1 — approved by Tin Dang (auto-mode delegated; OpenAI-compatible passthrough, no new contract surface beyond the existing UpstreamProvider Protocol)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% (new adapter file)
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_api_key_routes_to_deployment_url: arrange provider(no token_provider) + MockTransport capturing url+headers / act post_json / assert url has /deployments/prod-embed/embeddings?api-version + api-key header + NO authorization + (200, body) unchanged
  - test_aad_sends_bearer_token: arrange provider(_FakeProvider "tok-xyz") / act post_json / assert Authorization: Bearer tok-xyz + NO api-key header
  - test_identity_deployment_for_unmapped_model: arrange provider(deployment_map {}) / act post_json model="my-embed" / assert url segment "my-embed" + body forwarded unchanged
  - test_upstream_500_fails_closed: arrange MockTransport → 500 / act+assert pytest.raises(UpstreamUnavailableError)
  - test_4xx_content_filter_passes_through: arrange MockTransport → 400 content_filter / act post_json / assert returns (400, body), not raised
  - test_token_failure_fails_closed_no_post: arrange token_provider.get_token raises + handler sets a hit flag / act+assert raises AND handler never called
  - test_post_multipart_unsupported / test_stream_bytes_unsupported: assert UpstreamUnavailableError
  - test_wiring_registers_azure_provider: arrange Settings(azure_endpoint+azure_api_key) / act create_app / assert isinstance(provider_registry.get("azure"), AzureEmbeddingsProvider)
  - test_wiring_absent_when_unconfigured: arrange Settings without azure → assert provider_registry.get("azure") is None
</test_plan>

Tests live in: `./tests/`  ·  declared: `apps/gateway/tests/azure_embeddings/test_azure_embeddings.py` · MUST run red (missing implementation) before Build.

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/azure_embeddings.py` `apps/gateway/src/gateway/main.py` `apps/gateway/tests/azure_embeddings/`
Strategy (ordered batches): 1. write red suite (tests dir + __init__.py) 2. AzureEmbeddingsProvider (mirror OpenAIDirectProvider shape + Azure URL/auth) 3. wire _providers["azure"] in create_app 4. green + pyright + ruff.
Safety rule (feature-specific): AAD token acquisition happens BEFORE the breaker guard / POST so a token failure fails closed without a blank-auth request; api_key/token never logged or in URL.
Code lives in: `apps/gateway/src/gateway/proxy/infrastructure/azure_embeddings.py`
Constraints: do NOT change any test or the contract; do NOT touch frozen azure_config.py; allow-list packages only (httpx, stdlib); ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 13/13 azure_embeddings; 74/74 across azure_* + provider_seam + embeddings_endpoint; 169-test no-DB blast-radius gate green.
- [x] coverage did not decrease — new adapter exercised by 13 tests (auth seam both branches, routing, passthrough, breaker transitions, fail-closed, secret hygiene, wiring identity).
- [x] no test or contract was altered during build — security remediation went through the change-request loop (phase tests → RED → re-snapshot → build fix), NOT a build-time test edit. §3 FROZEN @ v1 unchanged.
- [x] the green was EARNED — adversarial refute-read by an independent security-expert subagent (sonnet). It classified every test MEANINGFUL/WEAK and found 6 issues; the WEAK-test gaps it flagged (breaker state never asserted, network path untested, shared-instance identity untested) were CLOSED by new tests with breaker spies + identity assertions. No overfit/vacuous asserts remain.
- [x] concurrency / timing safe — no new shared state; the one AAD token cache lives in the SHARED AzureADTokenProvider instance (single-flight lock, verified by identity test). post_json is stateless bar the per-instance breaker.
- [x] no exposed secrets — SECURITY FINDING (review #1, MED) FOUND + REMEDIATED: the network-error path chained the httpx request (api-key in headers) via `from exc`; fixed to `from None` (mirrors azure_ad.py) + regression test `test_network_error_fails_closed_no_secret_in_chain` asserts `__cause__ is None`. api_key/token never logged, in URLs, metric labels, or exception messages. No OPEN security finding.
- [x] layering & dependencies — infrastructure adapter implements the domain UpstreamProvider Protocol; selected by deterministic registry lookup (TYPED_EXTRAS_NO_DISPATCH honored); only httpx + stdlib added.
- [x] a person reviewed and approved the change — auto-mode (autonomy:auto): gate auto-resolved on complete evidence. The one security finding was remediated (not auto-passed) before this PASS; the systemic finding is recorded as an open delta (§7), not silently passed.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `AzureEmbeddingsProvider` imported (main.py:48) + registered `_providers["azure"]` under the `if _azure_cfg:` guard (main.py provider-registry block), reusing the hoisted shared `_azure_token_provider`. Referenced by select_provider via the catalog provider="azure" map. Confirmed by `test_wiring_registers_azure_provider` + `test_wiring_aad_only_shares_token_provider_instance` (identity) + `test_wiring_absent_when_unconfigured`.
- [x] DEAD-CODE (code) — no orphaned symbols; post_multipart/stream_bytes are required Protocol members (raise unsupported), exercised by tests. `_azure_token_provider` hoist removed a conditional-binding pattern, no dead branch.
- [x] SEMANTIC — N/A (code task); the adversarial security review served as the deep read.

### Security review (independent subagent — adversarial refute-read)
6 findings; triaged:
- #1 MED (api-key in exception chain via `from exc`) — REMEDIATED in azure_embeddings.py (`from None` + regression test). The new adapter is now leak-free (it does NOT use the shared retry seam — post_json is its only network path).
- #2/#3 MED (network path + breaker state untested) — CLOSED via new tests (network-error test + breaker spies on 5xx/4xx/200).
- #4/#5 LOW (shared-instance identity + AAD-only wiring untested) — CLOSED via `test_wiring_aad_only_shares_token_provider_instance` (asserts `embed._token_provider is chat._token_provider`).
- #6 LOW (empty-model ValueError → 500) — NON-ISSUE: EmbeddingsUseCase pre-validates `model` (PAYLOAD_MODEL_REQUIRED rejects empty/whitespace) BEFORE post_json, so build_url's AZURE_DEPLOYMENT_REQUIRED guard is unreachable from the embeddings endpoint (defense-in-depth only).
- SYSTEMIC (recorded as open delta §7): the shared `execute_with_retry` seam (upstream_retry.py:159,183) + every adapter's `complete()` + azure_upstream stream (azure_upstream.py:160) + openai/bedrock/gemini/anthropic providers all chain the secret-bearing request via `from exc`. This predates v21 and spans multiple frozen contracts — surfaced (not auto-passed) as a dedicated cross-cutting hardening task, NOT folded into task 5.

### GATE RECORD
Outcome: PASS
Reviewed by: auto-mode (autonomy:auto) — evidence complete; security finding #1 remediated via change-request loop before PASS; systemic finding recorded as open delta · date: 2026-06-15

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): ERR_UPSTREAM_UNAVAILABLE rate for provider="azure" embeddings; ERR_PROVIDER_UNAVAILABLE (azure absent when expected = misconfig); AAD token-acquisition failure rate (shared with chat); per-deployment embeddings latency.
Spec delta for the next loop: Azure embeddings completed the OpenAI-compatible passthrough thesis — a sibling provider that speaks the OpenAI wire shape needs only (URL routing + auth seam), ZERO body/response translation. The shared AzureADTokenProvider seam now serves chat + embeddings from one cache; any future Azure modality (images/audio) plugs into the same two seams. Task 6 (azure-verify) must live-confirm the [contract] freeze flag: embeddings `usage` bills exactly and the OpenAI-shaped response passes through.

### Competency deltas
- [ADD · folded] The adversarial security refute-read (independent subagent) again caught what self-review missed — here the `from exc` api-key-in-chain leak (review #1) AND three WEAK-test gaps (breaker state / network path / shared-instance identity never asserted). Evidence: 6 findings on a "thin passthrough" task; 4 closed by new tests, 1 code fix, 1 systemic delta. Reinforces: run the adversarial verify even on tasks that look trivial.
- [TDD · folded] A breaker SPY (subclass counting on_upstream_error/record_success) turns resilience semantics from "assumed" into "asserted" — a 5xx test that only checks `pytest.raises` would pass an implementation that never trips the breaker. Evidence: `_SpyBreaker` closed review #3. Adopt the spy pattern for all adapter resilience tests.
- [SECURITY · folded] SYSTEMIC: the shared `execute_with_retry` seam (upstream_retry.py:159,183) and every provider adapter's transport-error path (azure_upstream.py:160, openai_provider.py:97, bedrock, gemini, anthropic) chain the secret-bearing httpx request via `raise ... from exc` — reachable by a crash-reporter walking `__cause__.request.headers/content`. Only azure_ad.py + (now) azure_embeddings.py use `from None`. Evidence: review #1 + grep of upstream_retry.py. PROPOSED FOLLOW-UP TASK: cross-cutting `provider-secret-chain-hardening` — sweep all `from exc` → `from None` at secret-bearing transport sites (+ regression tests), spans multiple frozen contracts so it is its own task, NOT folded into v21.
- [SDD · folded] The "single point AAD plugs in" spec delta from task 4 held exactly: embeddings reused the shared token_provider instance with zero new auth code. Evidence: `test_wiring_aad_only_shares_token_provider_instance` asserts object identity across chat + embeddings adapters.
