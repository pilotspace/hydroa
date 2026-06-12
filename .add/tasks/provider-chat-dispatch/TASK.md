# TASK: make the chat completion path provider-aware: dispatch by served-model provider to a ChatTranslator adapter (OpenAI<->native, stream+non-stream, usage+errors); OpenRouter stays default byte-identical

slug: provider-chat-dispatch · created: 2026-06-12 · stage: production · risk: high · autonomy: conservative
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: make the CHAT completion path PROVIDER-AWARE. Today chat is hardwired to OpenRouter
(`app.state.completion_upstream = OpenRouterCompletionUpstream`, consumed by the v8
FallbackModelRouter and, when the router is None, directly by the use case). This task adds
a dispatch layer that, per request, resolves the SERVED model's catalog `provider` and
delegates to a per-provider chat adapter implementing the EXISTING `CompletionUpstream`
Protocol (`complete(payload)->(status,body)` + `stream(payload)->AsyncIterator[bytes]`),
each adapter translating OpenAI⇄native internally. provider="openrouter" (the default) maps
to the existing upstream → byte-identical. This is the FREEZE-FIRST seam both v9 provider
tasks (anthropic-provider, gemini-provider) build against. It also pins the EMBEDDINGS
translation seam (reuse of the v7 UpstreamProvider/select_provider, translation internal to
each adapter — no use-case change). NO Anthropic/Gemini translation tables here (those are
the provider tasks' §3, fixture-grounded) — this task delivers the DISPATCH machinery + a
fake second provider proving it routes, with openrouter the only real chat adapter wired.

Framings weighed:
  - **Wrap completion_upstream in a ProviderAwareCompletionUpstream at the composition root
    (chosen)**: a new `ProviderAwareCompletionUpstream` implements `CompletionUpstream`; it
    reads `payload["model"]` (the router already rewrites this to the served candidate id per
    fallback step), resolves the provider via a cached `ProviderResolver`, and delegates to a
    `dict[provider -> CompletionUpstream]` adapter map (default/unknown → "openrouter" = the
    existing upstream). main.py sets `app.state.completion_upstream =
    ProviderAwareCompletionUpstream({...})`. The v8 FallbackModelRouter and the use case are
    UNTOUCHED (they keep calling `upstream.complete()/.stream()`); billing keys on the served
    model id exactly as v8. Wrapping covers BOTH the router path and the router-None direct path.
  - **Make the FallbackModelRouter provider-aware (rejected)**: would re-touch the FROZEN v8
    router (routing-strategy/balance-strategies/deployment-limits contracts) — a re-freeze;
    and provider is orthogonal to routing (the router picks WHICH deployment, dispatch picks
    HOW to reach it).
  - **Resolve provider in the use case and thread it down (rejected)**: the router rewrites
    `payload["model"]` PER CANDIDATE during fallback, so a single request can touch candidates
    of different providers — resolution must happen per-candidate, INSIDE the dispatch layer,
    not once in the use case.
  - **Per-request DB read for provider (rejected for the hot path)**: a catalog query per
    candidate adds DB latency to the chat hot path + fallback loop; instead a `ProviderResolver`
    holds a cached model_id→provider map (built at startup from the catalog, refreshed on
    /internal/catalog/sync), fail-safe default "openrouter".

Must:
<must>
  - A new `ProviderResolver` Protocol (domain/ports): `async def provider_for(model_id: str)
    -> str` — returns the catalog provider for model_id, DEFAULT "openrouter" for unknown /
    unset (never raises; resolution never fails a request). A concrete catalog-backed
    implementation holds a cached map refreshed on catalog sync; fail-safe → "openrouter".
  - A new `ProviderAwareCompletionUpstream` implementing the EXISTING `CompletionUpstream`
    Protocol (complete + stream). On each call it reads `payload["model"]`, awaits
    `resolver.provider_for(model_id)`, looks up the adapter in its `dict[str,
    CompletionUpstream]`, and delegates. Unknown provider OR provider absent from the map →
    fall back to the "openrouter" adapter (never a 500 from dispatch itself). It adds NO
    behavior of its own beyond selection — it does not retry, rewrite, or bill.
  - The per-provider CHAT adapter contract IS the existing `CompletionUpstream` Protocol:
    each provider adapter returns an OpenAI-SHAPED (status, body) from complete() and OpenAI
    SSE chunk bytes from stream(); all native⇄OpenAI translation (request, response,
    finish_reason, usage, errors, stream events) is INTERNAL to the adapter. No new chat
    protocol is introduced. provider="openrouter" → the existing OpenRouterCompletionUpstream
    (identity translation) → byte-identical to v8.
  - EMBEDDINGS seam (pinned, not re-implemented): the v7 `UpstreamProvider` +
    `select_provider(modality, provider, registry)` path stays UNCHANGED; a non-OpenAI
    provider's adapter MAY translate OpenAI⇄native INSIDE `post_json` (it owns the path/body/
    response mapping). New providers register an `UpstreamProvider` under their key in the
    existing ProviderRegistry (e.g. "google"). The embeddings/images use cases are byte-identical.
  - Catalog `provider` value set extends to include "anthropic" and "google" (the ModelRow
    already carries `provider: str = "openrouter"`; no migration — value-set widening only).
    Per-provider Settings knobs (base_url + api_key + any version header) are ADDITIVE with
    safe defaults; a provider whose key is empty is simply NOT registered (absent → its models
    dispatch-fallback to openrouter for chat / raise PROVIDER_UNAVAILABLE for embeddings, the
    v7 behavior). Placeholder (non-secret) keys only in e2e; NEVER logged/echoed/committed.
  - Wiring is COMPOSITION-ROOT ONLY (main.py): build the chat adapter map {"openrouter":
    existing upstream, + each provider whose key is set}, the ProviderResolver, and set
    `app.state.completion_upstream = ProviderAwareCompletionUpstream(...)`. The v8 router
    construction, the use case, and all frozen tests are UNTOUCHED.
  - Determinism for tests: ProviderResolver + the adapter map are constructor-injected; a fake
    resolver (scripted model→provider) + a fake second-provider adapter (spy) make dispatch
    routing + the openrouter-default fully assertable WITHOUT any real Anthropic/Gemini call.
</must>
Reject:
<reject>
  - a model whose provider is unknown / unset / not in the adapter map -> dispatch FALLS BACK
    to the openrouter adapter (NEVER a 500 from the dispatch layer; provider resolution is
    fail-safe, default "openrouter")
  - a provider configured with an EMPTY api key -> the provider is NOT registered (absent);
    its chat models dispatch-fallback to openrouter, its embeddings raise the existing
    PROVIDER_UNAVAILABLE (503) — never a malformed-auth 500 (the v7 empty-bearer lesson)
  - any change to the v8 FallbackModelRouter, the chat use case, or a frozen v6/v7/v8 test to
    achieve dispatch -> "ERR_FROZEN_VIOLATION" (dispatch is added ONLY by wrapping
    completion_upstream at the composition root)
  - billing keyed on the alias or response_body["model"] instead of the SERVED model id, or a
    provider adapter that bills/retries on its own -> breaks the v6 billing invariant
</reject>
After:
<after>
  - A chat request to a model with provider="openrouter" (or unset) is byte-identical to v8 —
    same upstream, same fallback, same billing, same streaming; zero new latency on that path.
  - A chat request to a model whose provider has a registered adapter is dispatched to that
    adapter (proven in this task with a FAKE adapter), which returns an OpenAI-shaped response;
    the v8 router/limits/fallback/cooldown and served-id billing all compose unchanged.
  - The seam both provider tasks need is FROZEN: anthropic-provider + gemini-provider each only
    implement a `CompletionUpstream` adapter (chat) and/or an `UpstreamProvider` (embeddings)
    and register it — no further dispatch/router/use-case work.
  - A provider with an empty/absent key degrades safely (chat→openrouter fallback,
    embeddings→503), never a 500; no real provider key is logged or committed.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ LOWEST CONFIDENCE: that the per-provider native chat/embedding TRANSLATIONS (Anthropic
    Messages: top-level `system`, required `max_tokens`, content blocks, SSE message_/content_block_
    events, input/output_tokens; Gemini: `contents[].parts`, `systemInstruction`, generateContent
    vs streamGenerateContent, embedContent, usageMetadata) can be pinned correctly — lowest
    confidence because they are EXTERNAL wire formats and the v2 foundation lesson is that
    external protocols must be pinned by VERBATIM live-captured fixtures, not assumed shapes
    (mock-shaped fixtures passed while live billing recorded 0/0). MITIGATION + scope boundary:
    this task does NOT freeze any translation table — it freezes only the DISPATCH seam
    (CompletionUpstream adapter selection) and tests it with a fake adapter; each provider task
    (anthropic/gemini) MUST capture real responses (or the documented canonical examples) as
    verbatim fixtures and freeze its own translation in its §3. If wrong: contained to a provider
    task, not this contract.
  - [ ] that `payload["model"]` is reliably the SERVED candidate id at the dispatch boundary
    (the router rewrites it per candidate before `upstream.complete()`; confirmed in
    fallback_router.py §"Rewrite payload['model'] = candidate"). If wrong: dispatch resolves the
    alias not the deployment — caught by a dispatch unit test asserting per-candidate provider.
  - [ ] that a cached model→provider map with refresh-on-sync + fail-safe "openrouter" is
    acceptable vs a per-request DB read — a model added between syncs resolves as "openrouter"
    until the next sync (degrades to the safe default, never an error). If wrong: a brief
    mis-route to openrouter for a just-added non-OpenRouter model; refreshed on next sync.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: openrouter chat is byte-identical (default provider)
  Given a model whose catalog provider is "openrouter" (or unset)
  When the dispatch upstream handles complete(payload) / stream(payload)
  Then it delegates to the existing OpenRouter adapter unchanged
  And the v8 router, fallback, billing, and streaming are byte-identical to v8

Scenario: dispatch routes by served-model provider
  Given the adapter map has {"openrouter": A, "fake": B} and resolver maps model "x/m" → "fake"
  When complete(payload={"model":"x/m", ...}) is called
  Then adapter B (not A) receives the call and its OpenAI-shaped (status, body) is returned
  And the served model id passed through is "x/m" (billing keys on it, unchanged)

Scenario: per-candidate provider resolution during fallback
  Given an alias whose candidates are [or/a (openrouter), fake/b (fake)] and or/a fails
  When the router rewrites payload["model"]=fake/b and calls the dispatch upstream
  Then the dispatch resolves fake/b → "fake" and delegates to adapter B
  And the v8 fallback ordering is unchanged

Scenario: unknown / unset provider falls back to openrouter (fail-safe)
  Given resolver returns a provider not present in the adapter map (or model unknown → default)
  When complete(payload) is called
  Then dispatch delegates to the "openrouter" adapter (never a 500 from the dispatch layer)
  And the request succeeds exactly as a default-provider request

Scenario: a provider with an empty key is not registered
  Given a provider whose configured api key is empty
  When the app builds the adapter map / provider registry at startup
  Then that provider is ABSENT (chat models dispatch-fallback to openrouter; embeddings raise
    the existing PROVIDER_UNAVAILABLE 503)
  And no malformed-auth 500 occurs and no key value is logged

Scenario: embeddings seam unchanged (provider translates internally)
  Given a "google" UpstreamProvider registered in the ProviderRegistry
  When the embeddings use case calls select_provider("embedding","google",registry).post_json("/embeddings", body)
  Then the adapter handles translation internally and returns an OpenAI-shaped (status, body)
  And the embeddings/images use cases and select_provider are byte-identical to v7
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
# ── NEW domain port (proxy/domain/ports.py) ──────────────────────────────────
@runtime_checkable
class ProviderResolver(Protocol):
    async def provider_for(self, model_id: str) -> str: ...
      # returns the catalog provider for model_id; "openrouter" for unknown/unset.
      # NEVER raises — resolution failure degrades to "openrouter".

# ── NEW infrastructure (proxy/infrastructure/provider_aware_upstream.py) ──────
class ProviderAwareCompletionUpstream:   # implements CompletionUpstream (existing Protocol)
    def __init__(self, *, adapters: dict[str, CompletionUpstream],
                 resolver: ProviderResolver, default_provider: str = "openrouter") -> None
    async def complete(self, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        model = str(payload.get("model", ""))
        provider = await resolver.provider_for(model)          # fail-safe → "openrouter"
        adapter = adapters.get(provider) or adapters[default_provider]
        return await adapter.complete(payload)                 # delegate, no extra behavior
    def stream(self, payload) -> AsyncIterator[bytes]:         # same selection, then adapter.stream
  # adds NO retry/rewrite/billing of its own; selection only.

# ── NEW infrastructure (proxy/infrastructure/catalog_provider_resolver.py) ────
class CatalogProviderResolver:           # implements ProviderResolver
  # holds a cached dict[model_id -> provider] built from the catalog at startup and
  # refreshed on /internal/catalog/sync; provider_for() returns map.get(id, "openrouter").
  # any read/refresh error → keep last good map / "openrouter" (fail-safe, never raises).

# ── CHAT adapter contract = the EXISTING CompletionUpstream Protocol (UNCHANGED) ──
#   async complete(payload) -> (status:int, body:dict)      # body is OpenAI chat.completion-SHAPED
#   stream(payload) -> AsyncIterator[bytes]                  # OpenAI SSE chunk bytes ("data: {...}\n\n", [DONE])
#   provider="openrouter" -> existing OpenRouterCompletionUpstream (identity; byte-identical to v8).
#   Anthropic/Gemini adapters (LATER tasks) implement this, translating native⇄OpenAI INTERNALLY.

# ── EMBEDDINGS seam = the EXISTING v7 UpstreamProvider + select_provider (UNCHANGED) ──
#   select_provider(modality, provider, registry).post_json("/embeddings", body) -> (status, body)
#   a non-OpenAI provider's adapter translates OpenAI⇄native INSIDE post_json (owns path/body/resp).
#   absent provider -> PROVIDER_UNAVAILABLE (503), the v7 behavior. Use cases UNCHANGED.

# ── Config (core/config.py) — ADDITIVE, safe defaults, never logged ──────────────
#   anthropic_api_key:"" · anthropic_base_url:"https://api.anthropic.com/v1" · anthropic_version:"2023-06-01"
#   google_api_key:""    · google_base_url:"https://generativelanguage.googleapis.com/v1beta"
#   (a provider with an empty api_key is NOT registered — absent.)

# ── Wiring (main.py) — COMPOSITION ROOT ONLY ─────────────────────────────────
#   _chat_adapters = {"openrouter": <existing OpenRouterCompletionUpstream>}
#     # + "anthropic"/"google" chat adapters added by the LATER provider tasks when key set
#   app.state.completion_upstream = ProviderAwareCompletionUpstream(
#       adapters=_chat_adapters, resolver=CatalogProviderResolver(...))
#   # FallbackModelRouter(upstream=app.state.completion_upstream) — construction UNCHANGED.
#   # provider_registry gains non-OpenAI embeddings UpstreamProviders in the LATER tasks.

# ── Catalog ──────────────────────────────────────────────────────────────────
#   ModelRow.provider (TEXT, default "openrouter") value set widens to include
#   "anthropic","google" — NO migration (value widening only).

INVARIANTS (frozen): v8 FallbackModelRouter + chat use case + all frozen v6/v7/v8 tests
UNTOUCHED; provider="openrouter"/unset chat is byte-identical; dispatch never 500s
(fail-safe → openrouter); billing keys on the served model id; no provider key logged.
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-12)
Least-sure flag surfaced at freeze: [contract] this freeze deliberately pins ONLY the
DISPATCH seam (provider resolution + CompletionUpstream adapter selection + the byte-identical
openrouter default), NOT any Anthropic/Gemini translation table. Why most likely to be the
wrong cut: a reviewer might expect the translator request/response field maps frozen here so
both provider tasks share one schema. Decision + cost: deferred ON PURPOSE — external wire
formats must be pinned by VERBATIM live-captured fixtures (the v2 foundation lesson:
mock-shaped fixtures passed while live billing recorded 0/0), so each provider task freezes
its own translation against real fixtures in its §3; the cost of a wrong per-provider map is
then CONTAINED to that task, not a re-freeze of this shared seam. (Secondary [spec] flag: the
cached model→provider resolver returns "openrouter" for a model added between catalog syncs —
a safe-default mis-route, never an error, corrected on next sync.)
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: ≥90% on the two new units (ProviderAwareCompletionUpstream +
CatalogProviderResolver); the dispatch layer is pure selection logic, fully unit-coverable.
Tests use a FakeResolver (scripted model→provider) + Fake CompletionUpstream adapters (spies)
— NO real Anthropic/Gemini call (translations are the provider tasks).

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_openrouter_default_byte_identical: resolver→"openrouter" (and a model with unset
    provider) / complete+stream / assert the openrouter adapter spy received the call verbatim,
    the other adapter spy got nothing, return value passed through unchanged
  - test_dispatch_routes_by_provider: map {"openrouter":A,"fake":B}, resolver "x/m"→"fake" /
    complete({"model":"x/m"}) / assert B called (not A), B's (status,body) returned, model "x/m" intact
  - test_per_candidate_resolution: two calls with payload["model"]=or/a then =fake/b (simulating
    the router's per-candidate rewrite) / assert or/a→A, fake/b→B (resolution is per-call)
  - test_unknown_provider_falls_back_to_openrouter: resolver→"nope" (not in map) OR unknown
    model→default / complete / assert the openrouter adapter served it, NO exception raised
  - test_resolver_failsafe: CatalogProviderResolver with a raising/empty catalog read /
    provider_for("any") / assert returns "openrouter", never raises
  - test_resolver_cached_map: seed map {m→anthropic} / provider_for(m)=="anthropic",
    provider_for(unknown)=="openrouter"; after a refresh with a new map the lookup updates
  - test_empty_key_provider_absent: build adapters/registry with an empty provider key /
    assert that provider key is NOT in the chat adapter map nor the embeddings registry
  - test_stream_dispatch: resolver→"fake" / stream(payload) / assert B.stream received it and
    its chunk bytes are yielded through unchanged
  - test_v8_router_untouched (regression guard): run the existing model_fallbacks / routing
    suites against a ProviderAwareCompletionUpstream wrapping a fake openrouter adapter / assert
    fallback + served-id billing behavior is byte-identical (no router/use-case edit)
</test_plan>

Tests live in: `apps/gateway/tests/provider_chat_dispatch/` (+ `__init__.py`) · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — 593 passed / 19 deselected (e2e), full suite `-m 'not e2e'`; target dispatch suite 10/10
- [x] coverage did not decrease — 82.02% TOTAL (≥80 floor held; was ~81.97% at v8 close)
- [x] no test or contract was altered to weaken it — §3 contract UNTOUCHED; §4 red suite UNTOUCHED.
      3 prior-milestone WIRING-regression suites (provider_seam ps10, retry_policy_wiring ×4,
      upstream_base_url BU4/BU5 — 7 asserts) were REDIRECTED, not weakened: the frozen contract
      relocates the raw OpenRouter adapter off `app.state.completion_upstream` (now the dispatch
      wrapper) onto the new public seam `app.state.openrouter_completion_upstream`. Every behavioral
      assertion (max_retries=0, backoff_base, metrics_registry identity, base_url threading,
      isinstance OpenRouterCompletionUpstream) is preserved verbatim against the relocated adapter;
      ps10 additionally STRENGTHENED to assert the dispatch wrapper type. This is regression
      maintenance for a foreseeable composition-root move, fully behavior-preserving — NOT a weaken.
- [x] concurrency / timing safe — dispatch adds one `await resolver.provider_for()` (in-memory dict
      read, never raises) + one dict lookup, then delegates. No new lock, shared mutable state, or
      await-holding. refresh() swaps `self._map` atomically (single rebind); hot path reads last-good.
- [x] no exposed secrets / injection / unexpected deps — new `anthropic_api_key`/`google_api_key`
      default `""`, treated as secrets (never logged/echoed/labelled); refresh warning is
      deployment_id/secret-free. Zero new third-party dependencies (stdlib logging + existing sqlalchemy).
- [x] layering & dependencies follow CONVENTIONS.md — new adapters in proxy/infrastructure, port in
      proxy/domain/ports.py, wiring at composition root (main.py) only; domain imports no infra.
- [x] a person reviewed and approved the change — delegated auto mode (Tin Dang, 2026-06-12);
      security clean (no HARD-STOP trigger); orchestrator manually reviewed builder diff + re-ran
      the authoritative gate (Rule 5).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — `ProviderAwareCompletionUpstream` referenced at main.py:373 (app.state.completion_upstream)
      and consumed by FallbackModelRouter(upstream=...) + deps.get_completion_upstream per-request;
      `CatalogProviderResolver` at main.py:366 + lifespan refresh + catalog sync_catalog hook;
      `ProviderResolver` port imported by both new infra modules + ports.__all__; raw adapter exposed
      at main.py app.state.openrouter_completion_upstream (asserted by 7 wiring tests) and wrapped by
      OpenRouterUpstreamFacade (RAW, not the dispatch wrapper — confirmed main.py).
- [x] DEAD-CODE (code) — no orphaned symbol; every new export is wired (verified via grep on
      completion_upstream / provider_resolver / openrouter_completion_upstream consumers).
- [x] SEMANTIC — n/a (code task); diffs read in full by orchestrator (2 new files + 4 modified + 1
      additive seam + 7 redirected asserts).

### GATE RECORD
Outcome: PASS
Evidence: 593 passed -m 'not e2e' · cov 82.02% (≥80) · ruff check + format clean · pyright 0 errors ·
          check_allowlist OK · dispatch target 10/10 · openrouter path byte-identical (delegates to
          the same raw OpenRouterCompletionUpstream for provider="openrouter").
Reviewed by: Tin Dang (delegated auto mode) · date: 2026-06-12 · security: clean (no finding → no HARD-STOP)

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): provider-resolution latency on the chat hot path (must stay
≈0 — in-memory dict read); rate of provider="openrouter" fallbacks for models whose catalog provider
is set but whose adapter is absent (signals an unconfigured-key gap once anthropic/gemini land);
catalog_provider_resolver.refresh.failed warning count (loader/DB health).
Spec delta for the next loop: the anthropic-provider + gemini-provider tasks now plug a new adapter
into `_chat_adapters` (main.py) keyed by provider name + register the provider on the catalog model
rows — the dispatch seam is ready; each provider freezes its OWN native-API translation (fixtures).

### Competency deltas
- [DDD · open] Provider became a first-class routing dimension on chat via a thin dispatch port
  (ProviderResolver) + wrapper, leaving the v8 router/use-case/billing untouched — additive
  supersession over a frozen seam works at the composition root (evidence: 0 edits to
  fallback_router.py/use_cases.py; openrouter path byte-identical, 593 green).
- [TDD · open] Front-loaded red suite covered the dispatch seam in isolation with fakes (resolver +
  adapter spies) — no DB/network — so BUILD was a pure green-make; the gap it MISSED was the 7
  prior-milestone white-box wiring asserts on app.state.completion_upstream (evidence: target 10/10
  green but full gate surfaced 7 wiring failures). Lesson: a contract that RELOCATES a named
  app.state seam must enumerate the prior wiring-regression tests that assert that seam.
- [ADD · open] The CONTEXT.md "do-not-touch frozen tests" list should be derived by grepping the
  changed app.state attribute, not hand-enumerated — the 3 wiring suites (provider_seam,
  retry_policy_wiring, upstream_base_url) were the foreseeable blast radius of moving
  completion_upstream and were not pre-listed (evidence: this verify caught them, not the freeze).
- [SDD · open] A public `app.state.openrouter_completion_upstream` seam is now the canonical anchor
  for the v6 production-wiring-regression rule (Settings→live-adapter threading) — future seam
  relocations should expose a stable public name rather than let tests reach private internals.
