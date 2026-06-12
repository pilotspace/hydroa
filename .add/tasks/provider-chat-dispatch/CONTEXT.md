# Shared context — provider-chat-dispatch (v9 task 1/4, FREEZE-FIRST)

Frozen spec: `.add/tasks/provider-chat-dispatch/TASK.md` (§1–§4; §3 CONTRACT FROZEN @ v1).
Read it FIRST and in full — single source of truth. Do NOT edit it.

## Goal of this BUILD
Make the chat completion path PROVIDER-AWARE by wrapping `app.state.completion_upstream` in a
dispatch layer — WITHOUT touching the frozen v8 router or the chat use case. Turn the RED suite
`apps/gateway/tests/provider_chat_dispatch/test_provider_chat_dispatch.py` GREEN (11 tests).
NO real Anthropic/Gemini translation here — only the dispatch machinery + the openrouter
byte-identical default. The Anthropic/Gemini adapters are LATER tasks.

## Hard rules (NON-NEGOTIABLE — violation = ERR_FROZEN_VIOLATION / HARD-STOP)
- Do NOT edit the v8 FallbackModelRouter (`proxy/application/fallback_router.py`), the chat use
  case (`proxy/application/use_cases.py`), or ANY frozen test (model_fallbacks, routing_*,
  proxy, deployment_*, balance_strategies, cooldown_circuit, embeddings, etc.).
- provider="openrouter"/unset chat MUST stay byte-identical to v8 (same upstream, same fallback,
  same billing, same streaming). The dispatch adds only: one `await resolver.provider_for(model)`
  + one dict lookup, then delegates to the SAME OpenRouterCompletionUpstream.
- Resolution is FAIL-SAFE: provider_for NEVER raises; unknown/unset/error → "openrouter".
  Dispatch NEVER 500s on its own (unknown provider not in the adapter map → openrouter adapter).
- No provider api key is logged/echoed/committed. New config keys default to "" (empty).
- Do NOT change the §3 contract. Do NOT weaken/edit the red tests — make them pass as written.

## Files to CREATE
1. `apps/gateway/src/gateway/proxy/infrastructure/provider_aware_upstream.py`
   - `class ProviderAwareCompletionUpstream` implementing the EXISTING `CompletionUpstream`
     Protocol (see `proxy/domain/ports.py`: `async complete(payload)->tuple[int,dict]`,
     `def stream(payload)->AsyncIterator[bytes]`).
   - ctor: `__init__(self, *, adapters: dict[str, CompletionUpstream], resolver: ProviderResolver,
     default_provider: str = "openrouter")`.
   - `complete`: `model = str(payload.get("model","")); provider = await
     self._resolver.provider_for(model); adapter = self._adapters.get(provider) or
     self._adapters[self._default]; return await adapter.complete(payload)`. Nothing else.
   - `stream`: return an INNER async generator that, on first iteration, does the SAME selection
     (await resolver inside the generator — stream() itself is a sync def returning AsyncIterator,
     mirror `openrouter_upstream.py` stream() which does `return _gen()`), then
     `async for chunk in adapter.stream(payload): yield chunk`.
2. `apps/gateway/src/gateway/proxy/infrastructure/catalog_provider_resolver.py`
   - `class CatalogProviderResolver` implementing `ProviderResolver`.
   - ctor: `__init__(self, *, loader: Callable[[], Awaitable[dict[str, str]]])` — loader returns a
     fresh model_id→provider map. Hold an in-memory `self._map: dict[str,str] = {}`.
   - `async def refresh(self) -> None`: try `self._map = await self._loader()`; on ANY exception
     keep the last-good map (do NOT clear) and log a warning (deployment_id-free; no secrets).
     MUST NOT raise.
   - `async def provider_for(self, model_id: str) -> str`: `return self._map.get(model_id,
     "openrouter")`. Reads in-memory only — NO DB on this hot path. NEVER raises.

## Files to MODIFY
3. `apps/gateway/src/gateway/proxy/domain/ports.py` — ADD a `@runtime_checkable` `ProviderResolver`
   Protocol (`async def provider_for(self, model_id: str) -> str: ...`) and add "ProviderResolver"
   to `__all__`. Mirror the existing Protocol style in that file.
4. `apps/gateway/src/gateway/core/config.py` — ADD additive Settings fields (defaults exactly):
   `anthropic_api_key: str = ""`, `anthropic_base_url: str = "https://api.anthropic.com/v1"`,
   `anthropic_version: str = "2023-06-01"`, `google_api_key: str = ""`,
   `google_base_url: str = "https://generativelanguage.googleapis.com/v1beta"`. Place them near
   the existing `openai_api_key`/`openai_base_url` fields; match their env-var/validation_alias
   convention (GATEWAY_ANTHROPIC_API_KEY etc. if AliasChoices is used). No validators needed.
5. `apps/gateway/src/gateway/main.py` — COMPOSITION-ROOT wiring ONLY:
   - Keep the existing OpenRouter upstream but bind it to a LOCAL name first:
     `_openrouter_upstream = OpenRouterCompletionUpstream(...)` (the current
     `app.state.completion_upstream = OpenRouterCompletionUpstream(...)` block).
   - Build the catalog loader closure over `app.state.sessionmaker`:
     `async def _load_provider_map() -> dict[str,str]:` open a session, `select(ModelRow.id,
     ModelRow.provider)` (import ModelRow from `gateway.catalog.infrastructure.orm`), return
     `{row.id: row.provider for row in rows}`. Build
     `app.state.provider_resolver = CatalogProviderResolver(loader=_load_provider_map)`.
   - Build the chat adapter map and the dispatch wrapper:
     `_chat_adapters: dict[str, CompletionUpstream] = {"openrouter": _openrouter_upstream}`
     `app.state.completion_upstream = ProviderAwareCompletionUpstream(adapters=_chat_adapters,
       resolver=app.state.provider_resolver)`.
   - CRITICAL: the `OpenRouterUpstreamFacade(upstream=...)` used to build the "openrouter" entry of
     `provider_registry` MUST wrap the RAW `_openrouter_upstream`, NOT the dispatch wrapper. (Find
     the existing `_openrouter_facade = OpenRouterUpstreamFacade(upstream=app.state.completion_upstream)`
     line and change it to `upstream=_openrouter_upstream`.)
   - The `FallbackModelRouter(upstream=app.state.completion_upstream)` construction is UNCHANGED
     (it now receives the dispatch wrapper — intended).
   - In the `lifespan` startup (after the dev/test schema bootstrap block), add
     `await app.state.provider_resolver.refresh()` (guard: it never raises).
6. `apps/gateway/src/gateway/catalog/api/router.py` — in `sync_catalog`, add `request: Request`
   param and, AFTER a successful `synced = await use_case.execute()`, call a FAIL-SAFE refresh:
   `_r = getattr(request.app.state, "provider_resolver", None)` then `if _r is not None: await
   _r.refresh()` wrapped in try/except (never let a refresh error change the sync response).
   Import `Request` from fastapi. This keeps the model→provider map fresh after an operator sync.
   (Do NOT change the response shape — frozen catalog tests assert {"synced": N}.)

## Verification (the subagent runs these; orchestrator re-runs authoritative)
- Target suite GREEN: `cd apps/gateway && uv run pytest tests/provider_chat_dispatch -o addopts="" -q`
- Regression (byte-identical guard): `cd apps/gateway && uv run pytest tests/model_fallbacks
  tests/routing_strategy tests/balance_strategies tests/deployment_limits tests/deployment_model
  tests/cooldown_circuit tests/proxy tests/embeddings tests/catalog -o addopts="" -q`
- Lint the WHOLE tree (gate scope): `cd apps/gateway && uv run ruff check . && uv run ruff format --check .`
- Typecheck (from repo ROOT): `make typecheck`. Allowlist (from ROOT): `make allowlist`.
- Settings test kwargs if you construct Settings in a test/helper:
  database_url="postgresql+asyncpg://gateway:gateway@localhost:5433/gateway_test",
  jwt_secret="test-secret-not-for-production-0123456789", redis_url="redis://localhost:6380/9",
  environment="test".

## Deliverables
- The 2 new files + 4 modified files; the 11-test suite GREEN; full regression GREEN; lint/types/
  allowlist clean. Do NOT git commit. Do NOT edit TASK.md. Report: files touched, test counts,
  any deviation/risk, and confidence scores. Confirm the OpenRouterUpstreamFacade now wraps the RAW
  upstream (not the dispatch wrapper) and that no frozen router/use-case/test was edited.
