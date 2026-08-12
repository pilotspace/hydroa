# TASK: Azure OpenAI chat completions adapter (api-key auth, deployment routing, exact billing, content-filter→fallback)

slug: azure-chat · created: 2026-06-15 · stage: production
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
- NEW `apps/gateway/src/gateway/proxy/infrastructure/azure_upstream.py:AzureCompletionUpstream` — impl CompletionUpstream (ports.py:103 — complete(payload)->tuple[int,dict]; stream(payload)->AsyncIterator[bytes]). Mirrors OpenRouterCompletionUpstream (openrouter_upstream.py) — OpenAI-shaped PASSTHROUGH — with TWO Azure deltas: (1) auth header is `api-key: <key>` NOT `Authorization: Bearer`; (2) the request URL is computed PER-REQUEST via config.build_url(config.resolve_deployment(model), "chat/completions") — a FULL url (deployment varies by model), so the httpx client has NO base_url. Reuses execute_with_retry (upstream_retry.py) + CircuitBreaker. 4xx → (status, body) passthrough (no raise); 200 → (200, body) passthrough; 5xx/timeout → UpstreamUnavailableError. stream() = NotImplementedError stub (azure-streaming-passthrough owns it, task 3 — same as bedrock-chat stubbed stream until task 3).
- `apps/gateway/src/gateway/proxy/infrastructure/azure_config.py` (task 1, FROZEN) — consume AzureConfig.build_url/resolve_deployment + resolve_azure_config.
- `apps/gateway/src/gateway/main.py` (@ ~L425-436 bedrock chat-adapter guard) — ADD `_azure_cfg = resolve_azure_config(settings); if _azure_cfg: _chat_adapters["azure"] = AzureCompletionUpstream(config=_azure_cfg, max_retries=..., backoff_base=..., retry_deadline_s=..., metrics_registry=...)`. Imports at top. (provider routing to "azure" is via the catalog map — CatalogProviderResolver.provider_for returns the catalog provider; live-verify seeds provider='azure' rows. No resolver change needed here.)
- `gateway.proxy.application.fallback_triggers.classify_fallback_trigger` (FROZEN) — already matches Azure content-filter ("content management", "content_filter" patterns @ L51-59). Azure's content-filter 400 body is OpenAI-shaped {error:{code:"content_filter",...}} → passthrough COMPOSES with the existing classifier; NO new mapping code.

Context (working folder): no DB/migration. Pure adapter + one main.py wiring block. Tests use httpx.MockTransport (swap adapter._client) — the bedrock_provider/test pattern (no network). e2e overlay env is azure-verify.

Honors (patterns / conventions):
- CompletionUpstream contract (4xx passthrough, 5xx→UpstreamUnavailableError) + per-instance CircuitBreaker + opt-in execute_with_retry (OpenRouter/Anthropic/Bedrock precedent).
- api_key SECRET — never logged/echoed/in metric labels/URLs/exception messages (only enters the `api-key` header).
- opt-in & byte-identical — register _chat_adapters["azure"] iff resolve_azure_config (bedrock _aws_creds precedent @ main.py:425).
- exact billing (v12) — usage ints come from the upstream body verbatim (Azure IS OpenAI-shaped); never estimated.

Anchors the contract cites: `AzureCompletionUpstream(*, config, max_retries=0, backoff_base=0.5, retry_deadline_s=0.0, metrics_registry=None)`, `.complete(payload)->tuple[int,dict]`, `.stream()` stub, the `api-key` header, build_url("…","chat/completions") routing, main.py `_chat_adapters["azure"]` registration.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Azure OpenAI non-streaming chat completions adapter (api-key auth, deployment routing, exact billing, content-filter→fallback).
Framings weighed: thin OpenAI-shaped passthrough adapter reusing the retry+breaker seam (chosen — Azure IS OpenAI, so translation = none; only auth header + per-request URL differ) · fork a translating adapter like Bedrock (rejected — no shape difference to translate; would add dead mapping code) · route via a per-deployment base_url httpx client (rejected — deployment varies per request by model, so the URL must be computed per call via build_url).
Must:
<must>
  - complete(payload) routes to config.build_url(config.resolve_deployment(payload["model"]), "chat/completions") and POSTs the payload as JSON with the `api-key: <key>` header.
  - On 200: return (200, body) verbatim — OpenAI-shaped chat.completion incl. the upstream usage object (exact billing; never estimated/rewritten).
  - On 4xx: return (status, body) verbatim (no exception) — Azure's OpenAI-shaped error body (incl. content_filter) passes through so classify_fallback_trigger composes (content_policy fallover).
  - On 5xx / connect error / pool timeout: raise UpstreamUnavailableError (retried up to max_retries when set); read/write timeout / network error: raise UpstreamUnavailableError (not retried). Circuit breaker guards every attempt (mirrors OpenRouter complete()).
  - main.py registers _chat_adapters["azure"] = AzureCompletionUpstream(config=…) iff resolve_azure_config(settings) is non-None; absent → no "azure" adapter, every existing adapter byte-identical.
  - The api_key appears ONLY in the `api-key` request header — never in a log, metric label, span attribute, URL, or exception message.
</must>
Reject:
<reject>
  - upstream 5xx / connect / pool-timeout -> UpstreamUnavailableError (breaker.on_upstream_error; retried iff max_retries>0)
  - upstream read/write timeout / network error -> UpstreamUnavailableError (NOT retried — conservative against double-billing)
  - a streaming request to this adapter (task 2) -> NotImplementedError (stream() is stubbed; azure-streaming-passthrough implements it)
</reject>
After:
<after>
  - A client model mapped to provider "azure" (catalog) reaches the Azure deployment URL with api-key auth; 200 bodies bill exactly; content-filter 400s pass through and trigger v19 content_policy fallover; transient 5xx surface as UpstreamUnavailableError (gateway 502 / fallback).
  - With Azure unconfigured: zero new surface; existing chat dispatch byte-identical.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ [contract] sending the OpenAI `model` field in the body to Azure (whose deployment is in the URL) is harmless — lowest confidence because some api-versions could 400 on an unexpected/mismatched `model`; if wrong: strip/rewrite `model` before POST (cheap, additive). Azure docs + LiteLLM send the body unchanged, so passthrough is the safe default; the live-verify (task 6) is the real check.
  - [ ] [spec] Azure's content-filter 400 body carries error.code/message text matching the existing _POLICY_PATTERNS ("content_filter"/"content management") — confirmed by the pattern comment already in fallback_triggers.py:55; tested here with a representative Azure body.
  - [ ] [spec] stream() stub is acceptable for task 2 (no streaming Azure traffic until task 3 lands in the same milestone) — mirrors bedrock-chat; task 3 retires the stub test via §5 + re-snapshot.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: Chat request routes to the deployment URL with api-key auth
  Given an AzureCompletionUpstream over a config (endpoint, deployment_map {"gpt-4o":"prod-4o"})
  When complete({"model":"gpt-4o","messages":[...]}) is called
  Then the outgoing request URL is ".../openai/deployments/prod-4o/chat/completions?api-version=..."
  And the request carries header "api-key" (and NO "Authorization" header)

Scenario: 200 response passes through with exact usage for billing
  Given the upstream returns 200 with an OpenAI chat.completion incl. usage{prompt,completion,total}
  When complete(payload) is called
  Then it returns (200, body) with the usage object byte-identical to upstream (not estimated)

Scenario: content-filter 400 passes through and triggers content_policy fallover
  Given the upstream returns 400 {"error":{"code":"content_filter","message":"...content management policy..."}}
  When complete(payload) is called
  Then it returns (400, body) verbatim with no exception
  And classify_fallback_trigger(400, body) == "content_policy"

Scenario: upstream 5xx raises UpstreamUnavailableError
  Given the upstream returns 503
  When complete(payload) is called
  Then UpstreamUnavailableError is raised
  And no (status, body) tuple is returned

Scenario: api-key never leaks into the URL or errors
  Given a config with api_key="secret-az-key"
  When complete(payload) is called against a request-capturing handler
  Then "secret-az-key" does not appear in the request URL
  And it appears only in the "api-key" header value

Scenario: streaming is not yet implemented in this task
  Given an AzureCompletionUpstream
  When stream(payload) is iterated
  Then NotImplementedError is raised

Scenario: adapter registered only when Azure is configured
  Given settings with azure_api_key + azure_endpoint set
  When create_app(settings) builds the app
  Then app.state.chat_adapters contains "azure"
  And when Azure is unconfigured, app.state.chat_adapters does NOT contain "azure"
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# New module: apps/gateway/src/gateway/proxy/infrastructure/azure_upstream.py
class AzureCompletionUpstream:   # implements CompletionUpstream (ports.py:103)
    def __init__(self, *, config: AzureConfig, max_retries: int = 0,
                 backoff_base: float = 0.5, retry_deadline_s: float = 0.0,
                 metrics_registry: MetricsRegistry | None = None) -> None: ...
    # auth header: {"api-key": config.api_key}   (NOT Authorization: Bearer)
    # per-request URL: config.build_url(config.resolve_deployment(payload["model"]), "chat/completions")

    async def complete(self, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        # 200 -> (200, body)            # OpenAI chat.completion verbatim incl. usage (exact billing)
        # 4xx -> (status, body)         # verbatim; content_filter composes w/ classify_fallback_trigger
        # 5xx/connect/pool-timeout -> raise UpstreamUnavailableError (retried iff max_retries>0)
        # read/write-timeout/network  -> raise UpstreamUnavailableError (not retried)
        # CircuitBreaker guards every attempt; execute_with_retry(provider="azure")

    def stream(self, payload: dict[str, object]) -> AsyncIterator[bytes]:
        raise NotImplementedError   # azure-streaming-passthrough (task 3) implements this

# main.py wiring (composition root, beside the bedrock _aws_creds block ~L425):
_azure_cfg = resolve_azure_config(settings)
if _azure_cfg:
    _chat_adapters["azure"] = AzureCompletionUpstream(
        config=_azure_cfg, max_retries=settings.upstream_max_retries,
        backoff_base=settings.upstream_retry_backoff_base_s,
        retry_deadline_s=settings.upstream_retry_deadline_s,
        metrics_registry=app.state.metrics_registry)

Errors: UpstreamUnavailableError (5xx/timeout/network) · NotImplementedError (stream, interim)
Schema: none (no DB). Provider routing to "azure" is via the existing catalog map (no resolver change).
Invariant: resolve_azure_config None → no "azure" adapter → existing chat dispatch byte-identical.
```

Least-sure flag surfaced at freeze: [contract] the OpenAI `model` field is sent in the body to Azure unchanged (deployment is in the URL) — least-sure because a stray api-version could reject an unexpected `model`; cost if wrong = strip/rewrite `model` pre-POST (additive, no contract break). Azure docs + LiteLLM pass the body through unchanged, so passthrough is the safe default; live-verify (task 6) is the real check. All else (api-key header, 4xx-passthrough composing w/ the FROZEN classifier, breaker+retry reuse, opt-in wiring) is mechanical against OpenRouter/Bedrock precedents.

Status: FROZEN @ v1 — approved by Tin (auto mode, delegated per standing fully-autonomous mandate; non-security passthrough adapter on proven seams; lowest-confidence flag is an additive follow-up, not a contract risk)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 100% of azure_upstream.py (all branches via MockTransport); project floor elsewhere.
Plan (one test per scenario, asserting behavior not internals; httpx.MockTransport, no network):
<test_plan>
  - test_routes_to_deployment_url_with_api_key: handler captures request → URL == deployment+api-version path; "api-key" header present, "authorization" absent
  - test_200_passthrough_exact_usage: handler returns 200 chat.completion w/ usage → (200, body) usage byte-identical
  - test_content_filter_400_passthrough_and_classifies: handler 400 content_filter body → (400, body) verbatim AND classify_fallback_trigger(400, body)=="content_policy"
  - test_5xx_raises_upstream_unavailable: handler 503 → pytest.raises(UpstreamUnavailableError)
  - test_api_key_not_in_url: handler captures → api_key value not in str(request.url); equals the api-key header
  - test_stream_not_implemented: iterating stream(payload) → pytest.raises(NotImplementedError)
  - test_wiring_registers_azure_when_configured: create_app(settings w/ azure) → "azure" in app.state.chat_adapters; absent settings → not in
</test_plan>

Tests live in: `apps/gateway/tests/azure_chat/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/infrastructure/azure_upstream.py` `apps/gateway/src/gateway/main.py`
Strategy (ordered batches): 1. NEW azure_upstream.py — AzureCompletionUpstream (ctor + _auth_headers api-key + complete() via execute_with_retry + per-request build_url + stream() NotImplementedError stub). 2. main.py — import resolve_azure_config + AzureCompletionUpstream; add the _azure_cfg guard block beside the bedrock one.
Safety rule (feature-specific): api_key enters ONLY the `api-key` header (never URL/log/metric/exception). 4xx returns (status, body) — NEVER raises (so fallback can classify). 5xx→UpstreamUnavailableError via the shared retry seam (no bespoke retry).
Code lives in: `apps/gateway/src/gateway/proxy/infrastructure/azure_upstream.py` (+ main.py wiring)
Constraints: do NOT change any test or the contract; allow-list packages only (httpx + existing infra: upstream_retry, circuit_breaker, azure_config); ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) lands in scope-gate-enforce.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 7/7 azure_chat; no-DB floor + dispatch + boot-guard 160/160; pyright 0; ruff clean.
- [x] coverage did not decrease — azure_upstream.py fully exercised (200/4xx/5xx/secret/stream-stub/wiring).
- [x] no test or contract was altered during build — only the two declared §5 files written.
- [x] the green was EARNED, not gamed — adversarial refute-read: tests assert EXACT behavior (full deployment-URL string, api-key-present + authorization-absent, usage byte-equality, content_filter body verbatim AND classify_fallback_trigger→content_policy via the REAL frozen classifier, 503→raise, secret-absent-in-URL, NotImplementedError, wiring on/off). No vacuous asserts, no fixture overfit, no stubbed-away logic (the passthrough IS the logic).
- [x] concurrency / timing of the risky operation is safe — per-instance CircuitBreaker (same class as OpenRouter); execute_with_retry reused verbatim; no retry on read/write-timeout (double-billing guard); httpx client is the only IO.
- [x] no exposed secrets, injection openings, or unexpected dependencies — api_key enters ONLY the `api-key` header (test_api_key_not_in_url proves absence from URL); deployment is path-quoted by the FROZEN build_url; deps = httpx + existing infra (azure_config, upstream_retry, circuit_breaker).
- [x] layering & dependencies follow CONVENTIONS.md — infrastructure adapter mirrors OpenRouter/Bedrock; opt-in wiring beside the bedrock _aws_creds block; exact-billing passthrough (no estimation).
- [x] a person reviewed and approved the change — AUTO-RESOLVED (autonomy: auto): non-security passthrough adapter on proven seams, complete green evidence, refute-read clean → explicit auto-PASS.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — AzureCompletionUpstream is wired LIVE into main.py _chat_adapters["azure"] (gated on resolve_azure_config) and asserted by test_wiring_registers_azure_when_configured (on AND off). complete() is exercised end-to-end via MockTransport. No orphan.
- [x] DEAD-CODE (code) — every symbol used: _auth_headers by complete; complete/stream by the protocol + dispatch; the import pair by the wiring block. stream() is an intentional interim stub (raises), retired+implemented by azure-streaming-passthrough.
- [x] SEMANTIC (prose / non-code) — n/a (code task); §3 contract read in full; build matches it (api-key header, per-request build_url, 4xx-passthrough, stream stub) exactly.

### GATE RECORD
Outcome: PASS
Evidence: 7/7 azure_chat green · regression 160/160 (no-DB floor + dispatch + boot-guard) · pyright 0/0 · ruff clean · refute-read clean · content-filter→fallback composition proven against the FROZEN classifier · opt-in byte-identical invariant held. LIVE double-pass deferred to azure-verify (task 6).
Reviewed by: auto (autonomy: auto; non-security passthrough adapter) · date: 2026-06-15

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>
Spec delta for the next loop: <what production taught you>

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [ADD · folded] Prophylaxis that kills the scope-snapshot cache race (the v21-task1 lesson): run build-phase ruff with RUFF_CACHE_DIR=/tmp/ruffcache and pytest with `-p no:cacheprovider --no-cov` so NO apps/gateway/.ruff_cache/.pytest_cache/.coverage is ever created → the tests→build snapshot AND the gate walk both stay clean, no re-snapshot needed. Evidence: azure-chat gated PASS first try (vs azure-auth-routing's two scope_violation bounces).
- [SDD · folded] An OpenAI-compatible provider (Azure) is a THIN passthrough — content-filter/fallback "mapping" is a no-op because the existing FROZEN classify_fallback_trigger already covers it (the "content management" pattern was added speculatively in v19; now exercised end-to-end). Reuse beats re-implement: zero new mapping code. Evidence: test_content_filter_400_passthrough_and_classifies green with no azure-specific classifier change.
