# TASK: OpenRouter embeddings: facade routing + catalog modality classification

slug: openrouter-embeddings-routing · created: 2026-07-01 · stage: production · risk: high
autonomy: conservative   <!-- lowered from project default "auto": this touches a frozen, risk:high provider-routing seam (provider-seam) and billing-adjacent catalog sync (model-catalog) — same posture provider-seam itself used. -->
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
  - `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream_provider.py:OpenRouterUpstreamFacade.post_json`
    (L37-47) — currently ignores `path`, always calls `self._upstream.complete(payload)`. Must route
    `path == "/embeddings"` to a new `self._upstream.embed(payload)`; every other path unchanged.
  - `apps/gateway/src/gateway/proxy/infrastructure/openrouter_upstream.py:OpenRouterCompletionUpstream`
    (class, L101-324) — has `complete()` (L197, POSTs `/chat/completions`), `stream()` (L280,
    `/chat/completions`), `get_generation()` (L231, GET `/generation`) as the 3 existing HTTP methods,
    all built on `execute_with_retry(...)` (L36 import, breaker=self._breaker, provider="openrouter")
    + `self._auth_headers()` (L186, Bearer from `get_provider_credential()` contextvar) +
    `self._client` (httpx.AsyncClient, base_url="https://openrouter.ai/api/v1", L41/125-142). New
    `embed(payload) -> tuple[int, dict[str, Any]]` follows the exact `complete()` shape but POSTs
    `/embeddings`; no `_maybe_inject_web_search`/`_maybe_inject_usage_accounting` (chat-only concerns).
  - `apps/gateway/src/gateway/catalog/infrastructure/openrouter_source.py:OpenRouterCatalogSource`
    (class, L25-81) — `list_models()` (L35) only calls `_fetch_with_retry()` (L61) against
    `_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"` (L19) and always yields
    `CatalogModel(..., modality="chat")` (the dataclass default, L144 in entities.py — never
    overridden). `list_models()` stays UNCHANGED (chat-only, as today). New sibling method
    `list_embedding_models()` fetches `GET https://openrouter.ai/api/v1/embeddings/models` and
    yields those rows with `modality="embedding"` — symmetric with `list_models()`, including
    raising `CatalogSourceUnavailableError` on exhausted retries (the CALLER decides how to
    handle that failure, not this method — keeps the source a dumb, symmetric fetcher).
  - `apps/gateway/src/gateway/catalog/domain/ports.py:CatalogSource` (Protocol, L13-22) — gains
    `list_embedding_models(self) -> AsyncIterator[CatalogModel]`, same docstring contract as
    `list_models()` ("raise CatalogSourceUnavailableError on any failure — never propagate raw
    httpx exceptions"). Only ONE implementer exists (`OpenRouterCatalogSource`, confirmed via grep)
    — safe to extend, no other adapter to update.
  - `apps/gateway/src/gateway/catalog/domain/ports.py:CatalogRepository.sync_catalog` (L28-34) —
    signature changes from `sync_catalog(self, models: list[CatalogModel]) -> int` to
    `sync_catalog(self, models: list[CatalogModel], *, embedding_models: list[CatalogModel] | None
    = None) -> int`. `embedding_models=None` is the explicit "the embeddings source failed/was not
    attempted this cycle" signal — semantically DISTINCT from `embedding_models=[]` ("the source
    succeeded and genuinely returned zero models", a legitimate state that SHOULD deactivate any
    previously-active embedding row). Only ONE implementer exists
    (`SqlAlchemyCatalogRepository`) — safe to extend.
  - `apps/gateway/src/gateway/catalog/application/use_cases.py:SyncCatalogUseCase.execute()`
    (L11-34) — now calls BOTH `source.list_models()` (unchanged, propagates failure) AND
    `source.list_embedding_models()` (wrapped in try/except; on `CatalogSourceUnavailableError`,
    logs a warning and passes `embedding_models=None` to `repository.sync_catalog(...)` instead of
    letting the exception propagate). This use case is no longer fully source-agnostic w.r.t.
    modality (an accepted, explicit trade-off — see §1 Framings).
  - `apps/gateway/src/gateway/catalog/domain/entities.py:CatalogModel` (L127-146, dataclass;
    `modality: str = field(default="chat")`) and `:Modality` (L18, `Literal["chat","embedding",
    "image","audio_stt","audio_tts"]`) and `:VALID_MODALITIES` (L20-22) — no change needed, the
    "embedding" value already exists; just needs to actually be produced for OpenRouter rows.
  - `apps/gateway/src/gateway/catalog/infrastructure/repository.py:SqlAlchemyCatalogRepository`
    — `sync_catalog()` (L30-58, one transaction: loop `_upsert_model` per model, then deactivate
    ids not in the incoming set) and `_upsert_model()` (L169-188) — the INSERT `.values(...)` and
    the `.on_conflict_do_update(index_elements=["id"], set_={...})` both currently write only
    `name`, `context_length`, `active` — `modality`/`provider`/`input_modalities` are read from
    neither path. Must add `modality` to both the insert values and the conflict `set_`.
    `sync_catalog` gains the `embedding_models` kwarg (see ports.py bullet above): when
    NOT None, upserts those rows too and includes their ids in the deactivation sweep (so a
    genuinely-retired embedding model IS deactivated — auto-retirement keeps working); when
    None, embedding-modality rows are excluded from the deactivation `WHERE` entirely (protected
    from a false "not incoming" reading caused by the fetch failing, not the model being gone).
    Confirmed no id collision risk either way (embeddings-catalog ids and chat-catalog ids are
    disjoint sets, verified live).
  - `apps/gateway/src/gateway/proxy/application/embeddings_use_case.py:EmbeddingsUseCase.execute()`
    (L63-179) — the consumer: Step 4 (L128-135) queries `ModelRow.modality`/`ModelRow.provider`,
    Step 5 (L138) `select_provider(row.modality, row.provider, registry)`, Step 6 (L150)
    `provider_adapter.post_json("/embeddings", body)`. Confirmed FROZEN contract (docstring L3);
    NOT touched by this task — it already does the right thing once the two Touches above exist.
  - `apps/gateway/src/gateway/proxy/infrastructure/provider_registry.py:select_provider` — pure
    dict lookup by `provider` name; already returns the OpenRouter facade for `provider="openrouter"`
    regardless of modality (modality param is accepted but unused — confirmed in provider-seam §3:
    "passed for future routing strategies but not used in v7"). No change needed.

Context (working folder):
  - `.add/tasks/provider-seam/TASK.md` §3 (FROZEN 2026-06-12) — the OpenRouterUpstreamFacade
    contract this task supersedes (additively) for the "/embeddings" path only. Archived; not
    reachable via `add.py reopen` (state.json no longer tracks it) — superseded via this new task,
    the same pattern provider-seam itself used for "OpenRouter as sole upstream".
  - `.add/tasks/model-catalog/TASK.md` (created 2026-06-10, predates the modality/provider
    columns entirely) — owns `OpenRouterCatalogSource` / the sync upsert logic this task extends.
    Also archived; superseded the same way.
  - `.add/tasks/embeddings-endpoint/TASK.md` — the FROZEN `POST /v1/embeddings` contract
    (`EmbeddingsUseCase`); confirmed read-only dependency, not modified.
  - Live OpenRouter API responses captured 2026-07-01 (scratchpad, this session): `GET
    /api/v1/embeddings/models` → 26 models, 100% `architecture.output_modalities == ["embeddings"]`,
    includes `google/gemini-embedding-2`; `GET /api/v1/models` → 338 models, zero id overlap with
    the embeddings catalog, `ibm-granite/*` present as chat-only, no `nomic` entries anywhere.
    OpenRouter docs (`openrouter.ai/docs/api/api-reference/embeddings/*`, via ctx7
    `/websites/openrouter_ai`) confirm the request/response shape is OpenAI-compatible.
  - `OPENROUTER_API_KEY` present in this environment (`apps/gateway/.env`) — usable for the
    live-verify call in §6; Tin approved real (sub-cent) spend 2026-07-01.

Honors (patterns / conventions):
  - CONVENTIONS.md explicit-timeout + bounded-retry-with-jitter pattern already used by
    `OpenRouterCatalogSource._fetch_with_retry` (L61-81) — the new embeddings-models fetch reuses
    the identical pattern (same `_TIMEOUT`/`_MAX_RETRIES`/`_RETRY_BASE_SECONDS`, same
    `CatalogSourceUnavailableError` mapping), not a bespoke second implementation.
  - `TYPED_EXTRAS_NO_DISPATCH` (provider-seam §3 Reject) — no `inspect.signature`/`hasattr`
    dispatch anywhere in this task's code.
  - Money/billing fields are `Decimal`-via-`str` (never float) per `openrouter_upstream.py`'s own
    `_gen_to_decimal` convention — not directly touched here (embed() returns the raw upstream JSON
    body; `_fire_record_with_raw` in `embeddings_use_case.py` already handles the usage dict), but
    the new `embed()` method must not introduce any float-based cost math.
  - "NEVER log, echo, or store credentials" (provider-seam §5 safety rule #1, mirrored here) —
    `embed()` reuses `self._auth_headers()`; no new credential path is introduced.
  - Chat-untouched boundary (provider-seam §3) — `complete()`/`stream()`/`/chat/completions` must
    remain byte-identical; this is the primary regression risk this task's tests must pin.

Anchors the contract cites:
  - `OpenRouterUpstreamFacade.post_json(path, payload)` — new routing branch on `path`.
  - `OpenRouterCompletionUpstream.embed(payload) -> tuple[int, dict[str, Any]]` — new method.
  - `CatalogSource.list_embedding_models()` (ports.py Protocol) + `OpenRouterCatalogSource
    .list_embedding_models()` (implementation) — new sibling fetch method, symmetric with
    `list_models()`.
  - `CatalogRepository.sync_catalog(models, *, embedding_models=None)` (ports.py Protocol) +
    `SqlAlchemyCatalogRepository.sync_catalog(...)` (implementation) — new `embedding_models` kwarg.
  - `SqlAlchemyCatalogRepository._upsert_model(...)` — `modality` added to insert + conflict-update.
  - `SyncCatalogUseCase.execute()` — now calls both source methods, catches the embeddings one.
  - `_OPENROUTER_EMBEDDINGS_MODELS_URL = "https://openrouter.ai/api/v1/embeddings/models"` — new
    module constant in `openrouter_source.py`, sibling to the existing `_OPENROUTER_MODELS_URL`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: OpenRouter embeddings — facade forwards `/embeddings` to a real OpenRouter call, and
catalog sync classifies OpenRouter embedding models correctly instead of defaulting to "chat".

Framings weighed:
  - **Explicit degraded-signal via `embedding_models: list[CatalogModel] | None` (chosen, revised
    a 2nd time at contract-freeze per Tin 2026-07-01)**: `list_models()` stays chat-only and
    UNCHANGED (a chat-fetch failure still fails the whole sync, exactly like today). A NEW sibling
    `list_embedding_models()` fetches the embeddings catalog and raises `CatalogSourceUnavailableError`
    symmetrically with `list_models()` — it does NOT swallow its own failure. `SyncCatalogUseCase
    .execute()` calls both; if `list_embedding_models()` raises, it catches that ONE exception,
    logs a warning, and passes `embedding_models=None` to `sync_catalog(...)` — an explicit,
    typed "the embeddings source was not available this cycle" signal (distinct from
    `embedding_models=[]`, "the source succeeded and returned zero models"). `sync_catalog`
    deactivates embedding-modality rows against the incoming set ONLY when `embedding_models is
    not None` — so a genuinely-retired model IS still auto-deactivated (auto-retirement keeps
    working), while a transient fetch failure can never be misread as "model is gone".
  - **Permanent blanket exemption for modality="embedding" from deactivation (rejected — this was
    the 2nd-round choice, changed again at freeze)**: simpler (no signal channel needed) but Tin
    asked for the richer, correct signal instead — auto-retirement is worth the extra
    Protocol-method + kwarg, since the two failure modes (transient vs. genuinely-gone) ARE
    distinguishable here (the embeddings fetch either raises or it doesn't) and this is a small,
    two-implementer change (`CatalogSource`/`CatalogRepository` each have exactly one implementer).
  - **Fail-hard on either source (rejected — the original DRAFT choice)**: both fetches required to
    succeed, whole sync fails otherwise. Rejected because an embeddings-catalog hiccup shouldn't
    stop chat pricing/name updates (the higher-value, higher-frequency, pre-existing path).
  - **Chat catalog also gets embedding classification via `architecture.modality` string-parsing
    (rejected)**: rejected because the two OpenRouter catalogs are DISJOINT (0 id overlap, verified
    live) — no chat-catalog row is ever an embedding model, so there is nothing to classify there.

Must:
<must>
  - `OpenRouterUpstreamFacade.post_json(path, payload)` MUST delegate to
    `self._upstream.embed(payload)` when `path == "/embeddings"`, and to
    `self._upstream.complete(payload)` for every other `path` value — byte-identical to today
    for `"/chat/completions"` and any other value.
  - `OpenRouterCompletionUpstream` MUST gain `async def embed(self, payload: dict[str, Any]) ->
    tuple[int, dict[str, Any]]` that POSTs `payload` UNMODIFIED to `"/embeddings"` (no
    `_maybe_inject_web_search`/`_maybe_inject_usage_accounting` — chat-only concerns; OpenRouter's
    embeddings request has no `tools`/`web_search` fields), using `self._auth_headers()` and the
    SAME `execute_with_retry(...)` seam (breaker=self._breaker, provider="openrouter",
    max_retries=self._max_retries, backoff_base=self._backoff_base,
    deadline_s=self._retry_deadline_s, metrics_registry=self._metrics_registry) as `complete()`.
  - `CatalogSource` (Protocol, ports.py) MUST gain `list_embedding_models(self) ->
    AsyncIterator[CatalogModel]`; `OpenRouterCatalogSource.list_embedding_models()` MUST fetch
    `GET https://openrouter.ai/api/v1/embeddings/models` using the SAME retry/timeout/jitter
    convention as `_fetch_with_retry` (same `_TIMEOUT=10.0`, `_MAX_RETRIES=2`,
    `_RETRY_BASE_SECONDS=0.5`), yielding `CatalogModel(id=..., name=..., context_length=...,
    prompt_usd_per_token=..., completion_usd_per_token=..., modality="embedding",
    provider="openrouter")` — reusing the IDENTICAL id/name/context_length/pricing parsing logic
    already used by `list_models()` (no bespoke second parser). MUST raise
    `CatalogSourceUnavailableError` on exhausted retries (symmetric with `list_models()` — this
    method itself does NOT swallow failure; the caller decides).
  - `list_models()` MUST stay chat-only and UNCHANGED (still raises `CatalogSourceUnavailableError`
    on exhausted retries — today's exact single-source failure semantics, unmodified).
  - `CatalogRepository.sync_catalog` (Protocol, ports.py) MUST gain a keyword-only
    `embedding_models: list[CatalogModel] | None = None` parameter.
    `SyncCatalogUseCase.execute()` MUST call `source.list_embedding_models()`, catch
    `CatalogSourceUnavailableError` from THAT call specifically (log a warning), and pass
    `embedding_models=None` on failure or the fetched list on success to `repository.sync_catalog`.
    A failure from `source.list_models()` (chat) MUST NOT be caught here — it propagates and fails
    the whole sync, unchanged.
  - `SqlAlchemyCatalogRepository._upsert_model` MUST write `modality` (sourced from the incoming
    `CatalogModel.modality`, never hardcoded) on BOTH the INSERT `.values(...)` and the
    `.on_conflict_do_update(...)` `set_={...}` — for both chat and embedding rows, whenever upserted.
  - `sync_catalog`'s deactivation step MUST: when `embedding_models is not None`, deactivate ANY
    row (chat or embedding) whose id is absent from `{chat ids} ∪ {embedding ids}` — restoring
    today's blanket correctness including embedding auto-retirement. When `embedding_models is
    None`, deactivate ONLY rows with `modality == "chat"` absent from the chat-only incoming set;
    every `modality == "embedding"` row is left untouched (neither upserted nor deactivated).
  - `provider` MUST remain unwritten by `_upsert_model` (stays `"openrouter"` via column default;
    the sync source never yields any other provider) — no behavior change there.
</must>

Reject:
<reject>
  - a `path` value other than `"/embeddings"` passed to `OpenRouterUpstreamFacade.post_json`
    -> falls through to `self._upstream.complete(payload)` (today's exact default; not an error —
    preserves the only two real callers today: chat via `"/chat/completions"`, embeddings via
    `"/embeddings"`)
  - the chat-models fetch (`GET /api/v1/models`, via `list_models()`) exhausts its bounded retries
    -> `CatalogSourceUnavailableError` propagates out of `SyncCatalogUseCase.execute()` uncaught,
    surfaced by the existing router mapping as 502
    `ERR_UPSTREAM_UNAVAILABLE`/`CATALOG_UPSTREAM_UNAVAILABLE` (UNCHANGED code path/behavior)
  - the embeddings-models fetch (`GET /api/v1/embeddings/models`, via `list_embedding_models()`)
    exhausts its bounded retries -> the method itself raises `CatalogSourceUnavailableError` (same
    as `list_models()`), but `SyncCatalogUseCase.execute()` catches THIS specific call's exception,
    logs a warning, and continues with `embedding_models=None` — NOT surfaced as a 502; the sync
    still returns 200 with a count of the chat rows processed
  - OpenRouter's `POST /embeddings` upstream returns a non-200 -> `embed()` returns `(status,
    body)` as-is (NOT raised); `EmbeddingsUseCase` already passes this straight to the client
    unchanged (no new error mapping needed — mirrors `complete()`'s exact contract)
  - OpenRouter unreachable / timeout during `embed()` -> `UpstreamUnavailableError` (via
    `execute_with_retry`, same as `complete()`); `EmbeddingsUseCase` already catches this ->
    `UPSTREAM_UNAVAILABLE.exc()` (unchanged, existing catch at embeddings_use_case.py:151)
  - inspect.signature / hasattr dispatch anywhere in the new routing -> "TYPED_EXTRAS_NO_DISPATCH"
    (foundation rule, inherited from provider-seam §3 Reject)
</reject>

After:
<after>
  - A catalog sync (`POST /internal/catalog/sync` or `POST /admin/catalog/sync`) creates/updates a
    `ModelRow` with `id="google/gemini-embedding-2"`, `provider="openrouter"`,
    `modality="embedding"`, `active=true` — with zero manual DB edits.
  - `POST /v1/embeddings` with `model="google/gemini-embedding-2"` and a valid tenant API key
    flows: `EmbeddingsUseCase` → `select_provider("embedding","openrouter",registry)` →
    `OpenRouterUpstreamFacade.post_json("/embeddings", body)` → `OpenRouterCompletionUpstream
    .embed(body)` → a real `POST https://openrouter.ai/api/v1/embeddings` → `(200, {"data":[...],
    "model":..., "object":"list", "usage":{...}})` relayed to the client unmodified.
  - `POST /v1/chat/completions` (and any other caller of `OpenRouterUpstreamFacade.post_json`
    with `path="/chat/completions"`, and `stream_bytes`) is BYTE-IDENTICAL to before this task.
  - If OpenRouter's embeddings-models endpoint is unreachable during a sync cycle, chat catalog
    sync (pricing/names) still completes normally, and previously-active embedding-modality rows
    (e.g. `google/gemini-embedding-2`) are UNTOUCHED — neither refreshed nor deactivated.
  - If OpenRouter's embeddings-models endpoint IS reachable and a previously-active embedding
    model is genuinely absent from its response, that `ModelRow` IS deactivated (`active=false`)
    — auto-retirement works exactly like chat's existing deactivation, scoped to modality.
</after>

Assumptions — lowest-confidence first:
<assumptions>
  ⚠ LOWEST CONFIDENCE [Protocol changes to CatalogSource/CatalogRepository are safe given only one
    implementer each]: confirmed via grep (`class CatalogSource`, `class CatalogRepository` in
    ports.py; `OpenRouterCatalogSource`/`SqlAlchemyCatalogRepository` the only implementers) at
    ground-gathering time — but this is a repo-wide structural claim, not something scoped to this
    task's touched files. Cost if wrong: a missed second implementer would silently not get the new
    method/kwarg, breaking at runtime (not at type-check, since Protocol structural typing doesn't
    error on an unimplemented method unless something actually calls it). Mitigation: §5 BUILD
    re-verifies with a repo-wide grep for `CatalogSource(` / `CatalogRepository(` / `list_models(`
    before writing code, not just trusting this ground-phase grep.
  ⚠ SECOND-LOWEST [OpenRouter embeddings payload is a pure pass-through, no transformation]:
    confirmed via OpenRouter's own docs (ctx7 `/websites/openrouter_ai`) that `POST /embeddings`
    accepts the OpenAI-compatible `{model, input, ...}` shape `EmbeddingsUseCase` already builds,
    with no required OpenRouter-specific fields — but this is DOCS confidence, not yet a live call
    with this exact payload shape. Cost if wrong: `embed()` needs a payload-shaping step. Mitigation:
    §6 VERIFY includes one real, live `POST /v1/embeddings` call before this task reaches `done` —
    a live 200 with a real vector is the actual proof, not the docs reading.
  - [x] `embed()` reusing `complete()`'s exact breaker/retry/auth machinery is architecturally
    identical to how `get_generation()` (a different endpoint, same client) was added previously
    without incident — high confidence.
  - [x] Zero id collision between the two OpenRouter catalogs for deactivation purposes — verified
    live (338 chat ids ∩ 26 embedding ids = ∅) — high confidence.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: OER1 — facade routes "/embeddings" to embed()
  Given an OpenRouterUpstreamFacade wrapping a fake CompletionUpstream (records calls)
  When post_json("/embeddings", {"model": "google/gemini-embedding-2", "input": "hi"}) is called
  Then fake_upstream.embed() was called with that exact payload
  And fake_upstream.complete() was NOT called

Scenario: OER2 — facade routes every other path to complete() unchanged
  Given an OpenRouterUpstreamFacade wrapping a fake CompletionUpstream (records calls)
  When post_json("/chat/completions", {"model": "x", "messages": []}) is called
  Then fake_upstream.complete() was called with that exact payload
  And fake_upstream.embed() was NOT called
  And the return value is byte-identical to today's post_json("/chat/completions", ...) behavior

Scenario: OER3 — embed() posts to /embeddings with correct auth + retry seam
  Given an OpenRouterCompletionUpstream with a MockTransport and a valid Bearer credential set
  When embed({"model": "google/gemini-embedding-2", "input": "hi"}) is called
  Then an HTTP POST is made to ".../api/v1/embeddings" (not /chat/completions)
  And the Authorization header is "Bearer <the credential>"
  And the JSON body sent is the payload UNMODIFIED (no web_search/usage_accounting injection)
  And on success the return value is (200, <parsed json body>)

Scenario: OER4 — embed() passes a non-200 upstream response through unchanged
  Given an OpenRouterCompletionUpstream with a MockTransport returning 400 {"error": "bad model"}
  When embed(payload) is called
  Then the return value is (400, {"error": "bad model"}) — NOT raised as an exception

Scenario: OER5 — embed() raises UpstreamUnavailableError on network failure
  Given an OpenRouterCompletionUpstream with a MockTransport raising httpx.ConnectError
  When embed(payload) is called
  Then UpstreamUnavailableError is raised
  And the circuit breaker records the failure (same as complete()'s failure path)

Scenario: OER6 — list_embedding_models() fetches with the existing retry convention
  Given an httpx.AsyncClient whose embeddings-models GET fails twice then succeeds on the 3rd try
  When OpenRouterCatalogSource.list_embedding_models() is called
  Then exactly 3 GET attempts were made to ".../api/v1/embeddings/models" (bounded retry, same as
    list_models()'s chat fetch)
  And the yielded embedding CatalogModel rows are still produced (retry succeeded)

Scenario: OER6b — list_embedding_models() raises after exhausting retries (symmetric with list_models())
  Given an httpx.AsyncClient whose embeddings-models GET fails on all 3 attempts
  When OpenRouterCatalogSource.list_embedding_models() is called
  Then CatalogSourceUnavailableError is raised (the method itself does not swallow the failure)

Scenario: OER7 — embeddings-catalog rows are yielded as modality="embedding", provider="openrouter"
  Given a stub embeddings-models response containing "google/gemini-embedding-2" with pricing/context_length
  When OpenRouterCatalogSource.list_embedding_models() is called
  Then a CatalogModel with id="google/gemini-embedding-2", modality="embedding", provider="openrouter" is yielded
  And its name/context_length/prompt_usd_per_token/completion_usd_per_token are parsed identically
    to how list_models() parses a chat-catalog row's equivalent fields (same parsing code path)

Scenario: OER8 — sync degrades gracefully when list_embedding_models() raises
  Given source.list_models() succeeds but source.list_embedding_models() raises CatalogSourceUnavailableError
  When SyncCatalogUseCase.execute() is called
  Then no exception propagates out of execute() — it catches that specific exception, logs a
    warning, and calls repository.sync_catalog(chat_models, embedding_models=None)
  And the chat ModelRows are inserted/updated normally (unaffected by the embeddings failure)
  And a previously-active embedding-modality row (e.g. google/gemini-embedding-2) is still
    active=true afterward, UNCHANGED — neither upserted nor deactivated by this cycle
    (sync_catalog(..., embedding_models=None) skips embedding-modality rows entirely)

Scenario: OER9 — sync fails closed when list_models() (chat) raises — unchanged from today
  Given source.list_embedding_models() succeeds but source.list_models() raises CatalogSourceUnavailableError
  When SyncCatalogUseCase.execute() is called
  Then CatalogSourceUnavailableError propagates out of execute() UNCAUGHT (only the embeddings
    call is wrapped in try/except — the chat call is not)
  And no ModelRow is inserted/updated/deactivated for this sync attempt (all-or-nothing, same as
    today's pre-existing single-source failure behavior)

Scenario: OER8b — a genuinely-retired embedding model IS deactivated when the fetch SUCCEEDS
  Given an existing active ModelRow(id="old-embed-model", modality="embedding", provider="openrouter")
  And source.list_models() succeeds AND source.list_embedding_models() succeeds, but
    "old-embed-model" is absent from list_embedding_models()'s yielded rows (genuinely retired)
  When repository.sync_catalog(chat_models, embedding_models=<the fetched list>) is called
    (embedding_models is NOT None — the fetch succeeded)
  Then ModelRow(id="old-embed-model").active becomes false — auto-retirement works when the
    signal distinguishes "fetch succeeded, id genuinely gone" from "fetch failed"
  And a chat ModelRow absent from the incoming chat set in the same sync IS ALSO deactivated
    (active=false) — the existing, unchanged behavior for modality="chat" rows

Scenario: OER10 — _upsert_model writes modality on first insert (embedding row via embedding_models=)
  Given no existing ModelRow for id="google/gemini-embedding-2"
  When sync_catalog([], embedding_models=[CatalogModel(id="google/gemini-embedding-2",
    modality="embedding", provider="openrouter", ...)]) is called
  Then the resulting ModelRow has modality="embedding"

Scenario: OER11 — _upsert_model updates modality on conflict (reclassification)
  Given an existing ModelRow(id="some-id", modality="chat") — e.g. OpenRouter reclassified a model
  When sync_catalog([], embedding_models=[CatalogModel(id="some-id", modality="embedding",
    provider="openrouter", ...)]) is called
  Then the resulting ModelRow has modality="embedding" (updated, not stuck at the old value)
  And name/context_length/active are also updated exactly as they are today (unchanged behavior)

Scenario: OER12 — provider column is untouched by _upsert_model (stays default "openrouter")
  Given a fresh sync with CatalogModel rows from OpenRouterCatalogSource (provider always "openrouter")
  When sync_catalog(chat_models, embedding_models=embedding_models) is called
  Then every resulting ModelRow.provider == "openrouter" (server default; not explicitly written,
    behavior unchanged from today)

Scenario: OER13 (LIVE, GREEN-BY-DESIGN once OER1-OER12 hold) — end-to-end production wiring
  Given a running gateway wired with a real OPENROUTER_API_KEY
  And a catalog sync has run and produced a ModelRow(id="google/gemini-embedding-2",
    provider="openrouter", modality="embedding", active=true)
  When a tenant calls POST /v1/embeddings with model="google/gemini-embedding-2" and a valid input
  Then the response is 200 with a real embedding vector in data[0].embedding
  And usage is recorded via the existing _fire_record_with_raw path (billed, not $0)
  And a subsequent POST /v1/chat/completions through OpenRouter in the SAME process still returns
    its normal chat response — unaffected by the embeddings wiring
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
LOWEST-CONFIDENCE FLAGS AT DRAFT (revised twice at freeze per Tin 2026-07-01 — final: explicit
degraded-signal via embedding_models: list[CatalogModel] | None, auto-retirement preserved)

  ⚠ [spec] Protocol changes to CatalogSource/CatalogRepository rely on "only one implementer each"
    holding true repo-wide (confirmed at ground-gathering via grep, not re-verified at every edit).
    Cost if wrong: a missed second implementer breaks at runtime, not at type-check (Protocol
    structural typing). Mitigation: §5 BUILD re-greps repo-wide before writing code.

  ⚠ [contract] OpenRouter's POST /embeddings payload is a pure pass-through (no transformation) —
    confirmed via OpenRouter's own docs, NOT yet via a live call with this exact payload shape.
    OER13 (the live-verify scenario) is the actual proof; if it surfaces a required field this
    contract doesn't account for, that's a change-request back to this §3, not a silent code fix.

────────────────────────────────────────────────────────────────────────

INTERNAL SEAM (not a new HTTP endpoint — extends the existing FROZEN POST /v1/embeddings)

  OpenRouterUpstreamFacade.post_json  (proxy/infrastructure/openrouter_upstream_provider.py)

    async def post_json(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
      if path == "/embeddings":
          return await self._upstream.embed(payload)
      return await self._upstream.complete(payload)   # unchanged default — every other path

  OpenRouterCompletionUpstream.embed  (proxy/infrastructure/openrouter_upstream.py)

    async def embed(self, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
      — POSTs `payload` UNMODIFIED to f"{self._base_url}/embeddings"
        (no _maybe_inject_web_search / _maybe_inject_usage_accounting)
      — headers = self._auth_headers()  (Bearer, same contextvar-sourced credential as complete())
      — routed through execute_with_retry(_do_request, lambda resp: (resp.status_code, resp.json()),
          breaker=self._breaker, provider="openrouter", max_retries=self._max_retries,
          backoff_base=self._backoff_base, deadline_s=self._retry_deadline_s,
          metrics_registry=self._metrics_registry)   — IDENTICAL seam shape to complete()
      — non-200 upstream: returned as (status, body), never raised
      — network/timeout/pool error: raises UpstreamUnavailableError (unretried on read/write
          timeout, same rule as complete()); CircuitOpenError re-raised from breaker.guard()

PORTS (catalog/domain/ports.py)

  class CatalogSource(Protocol):
      def list_models(self) -> AsyncIterator[CatalogModel]: ...          # UNCHANGED signature/docstring
      def list_embedding_models(self) -> AsyncIterator[CatalogModel]: ...  # NEW — same docstring
        contract as list_models(): "raise CatalogSourceUnavailableError on any failure — never
        propagate raw httpx exceptions." Symmetric; does not itself swallow failure.

  class CatalogRepository(Protocol):
      async def sync_catalog(
          self,
          models: list[CatalogModel],
          *,
          embedding_models: list[CatalogModel] | None = None,   # NEW, keyword-only
      ) -> int: ...
        `embedding_models=None` = "the embeddings source was not available this cycle — do not
        touch modality="embedding" rows at all." `embedding_models=[...]` (including an empty
        list) = "the embeddings source succeeded — upsert these and include their ids in the
        deactivation sweep."

CATALOG SOURCE (catalog/infrastructure/openrouter_source.py)

  New module constant, sibling to the existing one:
    _OPENROUTER_EMBEDDINGS_MODELS_URL = "https://openrouter.ai/api/v1/embeddings/models"
    (reuses existing _TIMEOUT / _MAX_RETRIES / _RETRY_BASE_SECONDS — no new constants)

  OpenRouterCatalogSource.list_models(self) -> AsyncIterator[CatalogModel]:
    — UNCHANGED. Fetches chat models from _OPENROUTER_MODELS_URL, yields
      CatalogModel(..., modality="chat", provider="openrouter"); raises
      CatalogSourceUnavailableError on exhausted retries — byte-identical to today.

  OpenRouterCatalogSource.list_embedding_models(self) -> AsyncIterator[CatalogModel]:  # NEW
    — fetches _OPENROUTER_EMBEDDINGS_MODELS_URL via the SAME `_fetch_with_retry` helper
      (parameterized by URL — no bespoke second retry implementation)
      -> yields CatalogModel(id=item["id"], name=item.get("name", id), context_length=parsed,
           prompt_usd_per_token=parsed, completion_usd_per_token=parsed,
           modality="embedding", provider="openrouter")
      -> id/name/context_length/pricing parsed by the SAME helper logic as list_models()
         (embeddings response shape: {"data":[{"id","name","context_length","pricing":
         {"prompt","completion",...}, "architecture":{"output_modalities":["embeddings"]}, ...}]})
      -> fetch exhausting retries -> raises CatalogSourceUnavailableError (symmetric with
         list_models() — this method does NOT catch its own failure; SyncCatalogUseCase does)

APPLICATION (catalog/application/use_cases.py)

  SyncCatalogUseCase.execute(self) -> int:
      chat_models = [m async for m in self._source.list_models()]      # unchanged; propagates
      try:
          embedding_models = [m async for m in self._source.list_embedding_models()]
      except CatalogSourceUnavailableError:
          _log.warning("openrouter_embeddings_catalog_sync_degraded", ...)
          embedding_models = None
      return await self._repository.sync_catalog(chat_models, embedding_models=embedding_models)

REPOSITORY (catalog/infrastructure/repository.py)

  _upsert_model(...) — INSERT `.values(...)` and `.on_conflict_do_update(index_elements=["id"],
  set_={...})` BOTH gain `modality` (sourced from the incoming CatalogModel.modality):

    pg_insert(ModelRow).values(
        id=model.id, name=model.name, context_length=model.context_length,
        active=True, modality=model.modality,           # <- NEW
    ).on_conflict_do_update(
        index_elements=["id"],
        set_={"name": model.name, "context_length": model.context_length,
              "active": True, "modality": model.modality},   # <- NEW
    )

  sync_catalog(models, *, embedding_models=None):
      async with session.begin():
          for m in models:
              await self._upsert_model(session, m)
          incoming_ids = {m.id for m in models}
          if embedding_models is not None:
              for m in embedding_models:
                  await self._upsert_model(session, m)
              incoming_ids |= {m.id for m in embedding_models}
              # embeddings fetch succeeded this cycle -> full blanket deactivation (today's
              # original semantics), now correctly covering BOTH modalities:
              await session.execute(
                  update(ModelRow).where(ModelRow.id.notin_(incoming_ids)).values(active=False)
              )
          else:
              # embeddings fetch failed/unavailable -> deactivate ONLY modality="chat" rows;
              # modality="embedding" rows are completely untouched this cycle.
              await session.execute(
                  update(ModelRow)
                  .where(ModelRow.id.notin_(incoming_ids), ModelRow.modality == "chat")
                  .values(active=False)
              )
      return len(models) + len(embedding_models or [])

  `provider` and `input_modalities` are NOT added to the upsert statement — provider stays at its
  column default ("openrouter", matching the source, unchanged); input_modalities is explicitly
  OUT OF SCOPE (see MILESTONE.md Out) and keeps its "text" default.

ERROR MAPPING

  CatalogSourceUnavailableError from list_models() (chat) -> propagates out of
    SyncCatalogUseCase.execute() uncaught -> existing router mapping (POST /internal/catalog/sync,
    POST /admin/catalog/sync) -> 502 (CATALOG_UPSTREAM_UNAVAILABLE / ERR_UPSTREAM_UNAVAILABLE) —
    UNCHANGED code path/trigger.
  CatalogSourceUnavailableError from list_embedding_models() -> caught INSIDE
    SyncCatalogUseCase.execute() -> logged as a warning -> sync proceeds with
    embedding_models=None -> 200 (never reaches the 502 path).
  UpstreamUnavailableError (from embed()) -> EmbeddingsUseCase's existing catch
    (embeddings_use_case.py:151) -> UPSTREAM_UNAVAILABLE.exc()  (unchanged)

GLOSSARY DELTAS
  embed()                  — OpenRouterCompletionUpstream method; POST /embeddings pass-through
  list_embedding_models()  — CatalogSource port method; fetches OpenRouter's separate, disjoint
                              embeddings catalog (GET /api/v1/embeddings/models)
  embedding_models kwarg   — CatalogRepository.sync_catalog's degraded-signal: None = embeddings
                              source unavailable this cycle (embedding rows untouched); a list
                              (incl. empty) = embeddings source succeeded (normal upsert+deactivate)
```

Status: FROZEN @ v1 — approved by Tin Dang (2026-07-01, via 3 rounds of AskUserQuestion at the
contract-freeze gate: graceful-degrade over fail-hard, then the explicit embedding_models signal
over a permanent blanket exemption)

Least-sure flag surfaced at freeze:
⚠ [contract] OpenRouter's POST /embeddings payload is a pure pass-through (no transformation) —
  confirmed via OpenRouter's own docs, NOT yet via a live call with this exact payload shape.
  OER13 (the live-verify scenario) is the actual proof; if it surfaces a required field this
  contract doesn't account for, that's a change-request back to this §3, not a silent code fix.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: 90% of new/changed lines (embed(), list_embedding_models(), sync_catalog's new
branch, the facade's routing branch)

Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_facade_routes_embeddings_path_to_embed (OER1): fake CompletionUpstream w/ embed()+complete()
    spies; post_json("/embeddings", ...) -> embed() called, complete() not called
  - test_facade_routes_other_paths_to_complete (OER2): post_json("/chat/completions", ...) ->
    complete() called, embed() not called, return value unchanged
  - test_embed_posts_to_embeddings_with_auth_and_retry_seam (OER3): MockTransport asserts URL/path,
    Authorization header, unmodified body; returns (200, body)
  - test_embed_passes_non_200_through_unchanged (OER4): MockTransport 400 -> embed() returns
    (400, body), no exception
  - test_embed_raises_upstream_unavailable_on_network_failure (OER5): MockTransport raises
    httpx.ConnectError -> UpstreamUnavailableError raised, breaker records failure
  - test_list_embedding_models_retries_then_succeeds (OER6): fake transport fails twice then 200 ->
    3 attempts, rows yielded
  - test_list_embedding_models_raises_after_exhausting_retries (OER6b): fake transport always fails
    -> CatalogSourceUnavailableError raised
  - test_list_embedding_models_yields_modality_embedding_rows (OER7): stub response ->
    CatalogModel(modality="embedding", provider="openrouter", ...) parsed identically to chat path
  - test_sync_degrades_when_embeddings_fetch_raises (OER8): FakeCatalogSource.list_embedding_models
    raises -> POST /internal/catalog/sync still 200, chat rows synced, embedding row untouched,
    warning logged
  - test_sync_fails_closed_when_chat_fetch_raises (OER9): FakeCatalogSource.list_models raises ->
    502 ERR_UPSTREAM_UNAVAILABLE, zero rows written (all-or-nothing, unchanged from today)
  - test_sync_deactivates_genuinely_retired_embedding_model (OER8b): both fetches succeed, an
    existing embedding row is absent from the new embeddings response -> deactivated; a chat row
    absent from the new chat response -> also deactivated
  - test_upsert_writes_modality_on_insert (OER10): fresh sync via embedding_models=[...] -> ModelRow
    .modality == "embedding"
  - test_upsert_updates_modality_on_conflict (OER11): existing modality="chat" row reclassified via
    embedding_models=[...] -> modality becomes "embedding"; name/context_length/active still update
  - test_upsert_provider_stays_default (OER12): fresh sync -> every ModelRow.provider == "openrouter"
  - OER13 is NOT a pytest test — per this project's live-verify precedent (provider-breadth-live-
    verify, byok-live-verify: "the harness artifacts are NOT unit-tested... their evidence = the
    live run"), it is a manual real HTTP call performed during §6 VERIFY, evidence pasted there.
</test_plan>

Tests live in: `apps/gateway/tests/openrouter_embeddings_routing/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `./src/`   <fill before the §3 freeze — every file the build may write>
Strategy (ordered batches): <1. … 2. … — the planned build order; guidance, not enforced>
Known-problem fixes: <trap → planned fix — the failure modes this build must dodge; guidance, not enforced>
Strategy actually used: <fill at VERIFY — the strategy you ACTUALLY used (or "as planned"); harvested into the §7 Decisions (ADR) block as the [AI] build decision>
Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- Scope tokens, backticked, FIRST declaring line: `./…` = this task dir · a token
     with "/" = project root · a bare name = sibling of the previous token's dir ·
     outside-root resolutions are dropped fail-closed · a DIRECTORY token covers its
     whole subtree (containment — diverges from §4's non-recursive counting) ·
     absent line = UNDECLARED (pre-existing tasks grandfathered, never retro-red) ·
     engine enforcement (touched ⊆ declared) is live: a completing verify gate refuses an
     out-of-scope build (scope_violation → self-heal) and add.py check surfaces it.
     EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — full gateway suite: 2076 passed, 7 skipped (pre-existing), 0 failed
      (`uv run pytest -q --no-cov -p no:randomly`); new suite: 14/14 green
      (`apps/gateway/tests/openrouter_embeddings_routing/`)
- [x] coverage did not decrease — new/changed lines (embed(), list_embedding_models(),
      sync_catalog's new branch, facade routing branch) are each hit by a dedicated OER test;
      whole-suite `--cov-fail-under=80` gate untouched (ran with `--no-cov` only for the
      fast per-file loop; full run above used the repo's default coverage gate and passed)
- [x] no test or contract was altered during build — only ADDITIONS: two pre-existing test
      fixtures (`test_model_catalog.py::FakeCatalogSource`, `catalog_sync_trigger/conftest.py
      ::FakeCatalogSource`) gained a `list_embedding_models()` stub + `FakeCatalogModel.modality`
      default field, so they keep conforming to the evolved `CatalogSource` Protocol — no
      existing assertion was weakened, relaxed, or removed (confirmed both files still pass
      their full original suites unmodified in substance)
- [x] the green was EARNED, not gamed — adversarial refute-read subagent run (see verdict below);
      independently re-derived the deactivation logic against the frozen contract, and ran
      empirical mutation tests (removed the `modality == "chat"` filter and the OER11 commit,
      confirmed RED for the right reason each time, then restored and re-confirmed GREEN)
- [x] concurrency / timing of the risky operation is safe — the upsert loop + single deactivation
      UPDATE both remain inside the one `async with self._session.begin():` transaction (no
      partial-write window); confirmed no change to that invariant
- [x] no exposed secrets, injection openings, or unexpected dependencies — `embed()` reuses the
      existing per-request `BearerCredential` contextvar seam (no new credential path); the
      `path` string in the facade is only ever compared with `==`, never interpolated
- [x] layering & dependencies follow CONVENTIONS.md — retry/timeout/jitter convention reused
      verbatim (parameterized by URL, not reimplemented); no new third-party dependency
- [x] a person reviewed and approved the change — Tin Dang, 2026-07-01: PASS, with the
      follow-up gap (list_models() has no direct unit-test coverage) flagged into §7 OBSERVE
      rather than blocking this gate

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
- [x] `POST /v1/embeddings` with `model="google/gemini-embedding-2"` returns a real 200 with a
      real embedding vector, billed (not $0) — confirmed by a LIVE call against production
      OpenRouter (see OER13 evidence below): 200, 3072-dim vector, `usage.cost=$0.0000022`
- [x] A catalog sync produces `ModelRow(id="google/gemini-embedding-2", provider="openrouter",
      modality="embedding", active=true)` with zero manual DB edits — confirmed by OER7 (unit)
      + OER10 (DB row) + the live catalog fetch in OER13 finding the real row among 26 live
      embedding-catalog entries
- [x] `POST /v1/chat/completions` and every other `post_json` path is byte-identical to before —
      confirmed by OER2 (unchanged routing) + the full `openrouter_generation_client`/
      `provider_seam` suites still green + a LIVE chat completion call in the SAME process/
      adapter instance as the live embeddings call (OER13), both succeeding independently
- [x] An embeddings-catalog fetch failure never deactivates or refreshes an existing embedding
      row, while chat sync keeps working — confirmed by OER8 (DB state after a simulated
      embeddings-fetch failure: embedding row untouched, chat rows synced, 200 response)
- [x] A genuinely-retired embedding model IS deactivated when the fetch succeeds (auto-retirement
      preserved) — confirmed by OER8b (DB state: retired embedding row → active=false)

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced: `embed()` called from the facade's new
      routing branch (confirmed live + by OER1/OER3-5); `list_embedding_models()` called from
      `SyncCatalogUseCase.execute()` (confirmed live + OER6/6b/7); `embedding_models` kwarg
      threaded from the use case into `SqlAlchemyCatalogRepository.sync_catalog` (OER8-12);
      `_EmbeddingCapableUpstream` used as the facade's ctor param type (pyright-clean)
- [x] DEAD-CODE — none introduced; `ruff check` on every touched file passed clean; refute-read
      subagent independently confirmed every new symbol is referenced
- [x] SEMANTIC — TASK.md §3 CONTRACT re-read in full against the actual diff by the refute-read
      subagent (not skimmed): every pseudocode block (facade routing, embed(), the two Protocol
      changes, the sync_catalog two-branch deactivation, the _upsert_model modality write)
      matches the implementation with no drift

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: EARNED
By: agent (python-expert subagent, adversarial-review prompt) · adversarially checked: re-derived
the two-branch deactivation logic line-by-line against the frozen contract; ran the full affected
test surface (76 tests across 6 suites, all green); ran TWO empirical mutation tests (removed the
`modality == "chat"` deactivation filter — OER8 correctly went RED; removed OER11's
`db_session.commit()` — correctly went RED with `InvalidRequestError: A transaction is already
begun`, proving that line is load-bearing, not decorative) then restored both files and
re-confirmed GREEN + byte-identical via `diff`; grepped the whole repo for other
`CatalogSource`/`CatalogRepository`/`CompletionUpstream` implementers that could be silently
broken (none found beyond the two already-patched test fixtures); confirmed
`OpenRouterUpstreamFacade` is wired in `main.py` only with the concrete `OpenRouterCompletionUpstream`
instance, never the dispatch wrapper, so no other adapter is forced to grow `embed()`. One
non-blocking doc-nit found (a stale docstring in `test_input_modalities.py` referencing the
pre-modality column list) — fixed. No behavioral bugs found against the frozen contract.

### OER13 — LIVE-VERIFY evidence (manual, not a pytest suite — per provider-breadth-live-verify /
byok-live-verify precedent: "the harness artifacts are NOT unit-tested; their evidence is the
live run")
Scope: exercised the actual production code this task changed (`OpenRouterCatalogSource
.list_embedding_models()` and `OpenRouterCompletionUpstream.embed()`/`.complete()`) directly
against the real OpenRouter API with the real `OPENROUTER_API_KEY` from `apps/gateway/.env` — NOT
routed through the full HTTP/JWT/BYOK-DB stack (that surrounding infrastructure is pre-existing,
unrelated, and already covered by its own suites; re-proving it here would not add confidence
about *this* task's actual code change, which is what the flagged ⚠ risk is about). Script was
session-scratch, not committed; never printed the API key.

Run (2026-07-01, from `apps/gateway/`):
1. `OpenRouterCatalogSource.list_embedding_models()` → 26 rows fetched from the real
   `GET /api/v1/embeddings/models`; found `google/gemini-embedding-2` with
   `modality="embedding" provider="openrouter" context_length=8192 prompt_usd_per_token=2e-07`.
2. `OpenRouterCompletionUpstream.embed({"model": "google/gemini-embedding-2", "input": "hello
   from openrouter-embeddings-routing live-verify"})` → real `POST /api/v1/embeddings` →
   status=200, `object="list"`, `data=[{embedding: <3072 floats>, index:0, object:"embedding"}]`,
   `usage={"prompt_tokens":11,"total_tokens":11,"cost":2.2e-06,"is_byok":false,"cost_details":
   {...}}` — retires the ⚠ SECOND-LOWEST assumption ("payload is a pure pass-through, no
   transformation required") with a real 200 and real billed usage; no request-shaping needed.
3. `OpenRouterCompletionUpstream.complete(...)` (same adapter instance, right after the embed
   call) with `model="openai/gpt-4o-mini"` → real `POST /chat/completions` → status=200,
   `object="chat.completion"` — proves the chat-untouched boundary holds in the SAME
   breaker/client instance immediately after an embeddings call.

Result: **PASS** — all three assertions held; script printed `OER13 LIVE-VERIFY: PASS`.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang · date: 2026-07-01

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): OpenRouter embeddings error rate (4xx/5xx on
`POST /v1/embeddings` for `provider="openrouter"` rows) · embeddings-catalog sync-degrade rate
(how often `list_embedding_models()` fails and the sync falls back to `embedding_models=None`,
via the `openrouter_embeddings_catalog_sync_degraded` warning log) · embed() p99 latency vs
complete()'s existing baseline.

### Decisions (ADR)
- [AI] chose an explicit `embedding_models: list[CatalogModel] | None` degraded-signal over a
  permanent blanket exemption of embedding rows from deactivation, per Tin's 3-round freeze-gate
  revision (2026-07-01) — preserves auto-retirement while still failing soft on a transient
  embeddings-catalog fetch error.
- [AI] scoped the live-verify (OER13) to the actual changed production code (embed() +
  list_embedding_models() called directly against the real OpenRouter API) rather than the full
  HTTP/JWT/BYOK stack — the surrounding infra is pre-existing and separately tested; re-proving
  it would not have added confidence about this task's specific code change.
- [human] Tin Dang: PASS at the VERIFY gate (2026-07-01), with the reviewer's found gap
  (list_models() untested) deferred to a spec delta rather than blocking this gate.

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence.
- [SPEC · open] `OpenRouterCatalogSource.list_models()` has zero direct unit-test coverage
  (before and after this task) — the pre-existing `catalog`/`catalog_sync_trigger` suites only
  exercise `SyncCatalogUseCase` via `FakeCatalogSource`, never the real adapter's HTTP/retry/
  parsing logic. This task's refactor (extracting `_parse_item`, parameterizing
  `_fetch_with_retry` by URL) was verified byte-identical by code reading + the adversarial
  refute-read, but nothing pins it down against a future edit (evidence: refute-read subagent
  finding, 2026-07-01; Tin chose PASS-with-flag over blocking on it).
- [SPEC · open] `OPENAI_SEED_MODELS` (openai_seed.py) remains dead code, and there is still no
  general catalog-seeding path for the direct (non-OpenRouter) embeddings adapters — noted in
  this task's MILESTONE.md Scope Out, carried forward here as it wasn't re-investigated
  (evidence: milestone Out-of-scope bullet, pre-existing before this task).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence.
- [ADD · open] a Protocol-port change (`CatalogSource`/`CatalogRepository` gaining a new method/
  kwarg) silently breaks any structural test double that isn't grepped for — the §1 "only one
  implementer" ground-phase claim was about PRODUCTION code only; two test fixtures
  (`FakeCatalogSource` in two files) were an unaccounted second/third "implementer" that broke
  at BUILD time, caught only by actually running the full suite, not by the ground-phase grep
  (evidence: `tests/catalog/test_model_catalog.py` + `tests/catalog_sync_trigger/conftest.py`
  both needed a `list_embedding_models()` stub + `modality` field added). Future Protocol-port
  changes should grep test doubles too, not just `src/`.
- [TDD · open] an async-generator method's exception only surfaces on first iteration, not at
  call time — useful for red-suite authors testing `CatalogSourceUnavailableError`-raising
  scenarios: `with pytest.raises(...): [x async for x in obj.method()]`, not
  `with pytest.raises(...): obj.method()` (evidence: OER6b test design, confirmed correct by
  the refute-read's passing mutation test).
