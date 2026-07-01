# TASK: MiniMax chat model catalog seed

slug: minimax-catalog-seed · created: 2026-07-01 · stage: production
autonomy: auto   <!-- inherited from the project default (PROJECT.md); explicit level: manual < conservative < auto (visible · overridable) — lower below if a high-risk task needs it, or run `add.py autonomy set`. Multi-component repo (monorepo/multi-repo)? add a `component: <name>` line (declared in `.add/components.toml`) to ADD that component's root to your §5 Scope; omit for single-component projects (byte-identical default). -->
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
  - `apps/gateway/src/gateway/catalog/infrastructure/repository.py:195-222` —
    `SqlAlchemyCatalogRepository._upsert_model()` — CONFIRMED BUG (not just "out of scope" per its
    own docstring): the INSERT `.values(...)` never sets `provider`/`input_modalities` either, so
    EVERY row — including OpenRouter's own — gets them from the column `server_default`
    (`"openrouter"` / `"text"`), never from the in-memory `CatalogModel.provider`/`.input_modalities`.
    It "works" for OpenRouter purely because the default happens to match. Must extend both the
    INSERT `.values()` and the `on_conflict_do_update.set_={}` to write `provider` and
    `input_modalities` from `model.*` — required for `provider="minimax"` to ever persist.
  - `apps/gateway/src/gateway/catalog/infrastructure/repository.py:30-84` —
    `SqlAlchemyCatalogRepository.sync_catalog()` — the deactivation sweep
    (`ModelRow.id.notin_(incoming_ids)` -> `active=False`) is GLOBAL across the whole `models`
    table; `incoming_ids` is built ONLY from the `models`/`embedding_models` lists passed into
    *this* call. Any model id seeded outside this call's `incoming_ids` gets deactivated on the
    very next OpenRouter sync. -> MiniMax ids MUST be part of the same `models` list that reaches
    this method, not a side-channel insert.
  - `apps/gateway/src/gateway/catalog/application/use_cases.py:15-52` — `SyncCatalogUseCase` —
    `__init__(self, source: CatalogSource, repository: CatalogRepository)`; `execute()` calls
    `self._source.list_models()` (chat) + `self._source.list_embedding_models()` (embeddings), then
    `repository.sync_catalog(models, embedding_models=...)`. Left UNCHANGED — see Framings weighed.
  - `apps/gateway/src/gateway/catalog/domain/ports.py:12-30` — `CatalogSource` Protocol —
    `list_models() -> AsyncIterator[CatalogModel]` / `list_embedding_models() -> AsyncIterator[...]`.
    Any class implementing this shape is a legal `source` for `SyncCatalogUseCase`.
  - `apps/gateway/src/gateway/catalog/infrastructure/openrouter_source.py:27-112` —
    `OpenRouterCatalogSource` — the ONLY `CatalogSource` wired today (`main.py:627-628`,
    `app.state.catalog_source = OpenRouterCatalogSource(httpx.AsyncClient())`); its `_parse_item`
    always sets `provider="openrouter"` explicitly on each `CatalogModel` it yields (in-memory
    correct; only the repository fails to persist it, per the bug above).
  - `apps/gateway/src/gateway/catalog/infrastructure/openai_seed.py` — `OPENAI_SEED_MODELS` —
    CONFIRMED DEAD CODE (grepped: zero references outside its own file, in src/tests/migrations).
    Its shape (`list[CatalogModel]` module constant) is the template to mirror for
    `MINIMAX_SEED_MODELS`, but it is NOT wired anywhere and stays untouched/unwired by this task
    (out of scope — reviving `openai_seed.py` would change existing openai catalog behavior,
    unrequested).
  - `apps/gateway/src/gateway/catalog/domain/entities.py:127-146` — `CatalogModel` frozen dataclass
    — `id, name, context_length, prompt_usd_per_token, completion_usd_per_token,
    modality="chat", provider="openrouter", input_modalities="text"` — one flat price pair per
    model id (no context-length-tiered pricing field — see Assumptions ⚠).
  - `apps/gateway/src/gateway/catalog/infrastructure/orm.py:15-40` — `ModelRow` — `models` table;
    `provider`/`modality` server_default `"openrouter"`/`"chat"`; `input_modalities` server_default
    `"text"`.
  - `apps/gateway/src/gateway/main.py:627-628` — composition root wiring:
    `app.state.catalog_source = OpenRouterCatalogSource(httpx.AsyncClient())` — becomes a
    composite source (see Framings weighed) so `SyncCatalogUseCase` needs no change.
  - Test template: `apps/gateway/tests/catalog/test_model_catalog.py` — `FakeCatalogSource`
    installed via `app.state.catalog_source` override, POST `/internal/catalog/sync`, assert DB
    rows / `/v1/models` / `/admin/catalog/models`. Also
    `apps/gateway/tests/provider_seam/test_provider_seam.py::test_ps5_*` (entity/ORM field-default
    assertions) as a pattern for "assert CatalogModel/ModelRow carries provider/modality".

Context (working folder):
  - MiniMax pricing (fetched live, `https://platform.minimax.io/docs/guides/pricing-paygo.md`,
    2026-07-01): "Current" tier — MiniMax-M3 (context ≤512k) $0.30/$1.20 per-M-tokens in/out;
    MiniMax-M3 (context >512k) $0.60/$2.40; MiniMax-M2.7 $0.30/$1.20; MiniMax-M2.7-highspeed
    $0.60/$2.40. "Legacy" tier (M2.5/M2.1/M2 + highspeed variants) same $0.30/$1.20 or $0.60/$2.40
    pairing — NOT seeded this task (see Scope/Framings — legacy tier deferred, list is additive).
    Context windows (ctx7 `/websites/platform_minimax_io_api-reference`): MiniMax-M3 = 1,000,000
    tokens; MiniMax-M2.7(-highspeed) = 204,800 tokens.
  - `.add/tasks/minimax-adapter-registry/TASK.md` (prior task, done/gate=PASS) — MiniMax is
    OpenAI-wire-compatible chat-only; `provider="minimax"` already joins `PROVIDER_VALUE_SET` /
    `BYOK_PROVIDERS`; `_chat_adapters["minimax"]` already registered unconditionally in main.py.
    This task ONLY adds catalog rows — no adapter/BYOK changes.

Honors (patterns / conventions):
  - PROJECT.md invariant: provider is catalog metadata, never client-specified (unchanged) —
    `provider="minimax"` on the seeded rows IS this invariant in action.
  - PROJECT.md invariant: every proxied request produces exactly one usage record, billing keys on
    the SERVED model id with native usage tokens — pricing seeded here must be real, not placeholder
    (v55 already established input_modalities/capabilities discipline for the same reason).
  - v9 folded rule: "adding a provider NEVER changes the default path" — the composite source must
    leave OpenRouter's own sync behavior byte-identical when MiniMax's list is empty/unreachable.
  - openrouter-embeddings-routing (folded): `sync_catalog`'s embeddings-degrade-on-fetch-failure
    precedent — a MiniMax-list failure (impossible for a static list, but mirror the "never let one
    source's hiccup corrupt another modality's rows" spirit) must not affect OpenRouter rows.

Anchors the contract cites:
  - `SqlAlchemyCatalogRepository._upsert_model` (extended INSERT + conflict-update fields)
  - `CatalogSource` Protocol (composite source implements it unchanged)
  - `MINIMAX_SEED_MODELS` (new, mirrors `OPENAI_SEED_MODELS` shape)
  - `app.state.catalog_source` wiring in main.py

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: MiniMax chat models discoverable in the catalog (`provider=minimax`, `modality=chat`) with real pricing, surviving OpenRouter's own sync cycle.
Framings weighed:
  - (chosen) A new `CompositeCatalogSource` implementing the existing `CatalogSource` Protocol
    unchanged — its `list_models()` chains `OpenRouterCatalogSource.list_models()` with a static
    `MINIMAX_SEED_MODELS` iterator; `list_embedding_models()` delegates to OpenRouter only
    (MiniMax has no embeddings surface). Wired in main.py in place of the bare
    `OpenRouterCatalogSource`. `SyncCatalogUseCase` and its tests are untouched — it already
    accepts any `CatalogSource`-shaped object. This is what makes MiniMax ids part of
    `incoming_ids` in the SAME `sync_catalog()` call, so the deactivation sweep never treats them
    as absent-from-upstream. Chosen per Tin's explicit direction ("extend the shared sync/repository
    layer") — 2026-07-01.
  - Rejected: side-channel direct INSERT (migration or boot-time script) bypassing
    `SyncCatalogUseCase` entirely — cheaper, but MiniMax ids would never appear in any sync's
    `incoming_ids`, so the very next OpenRouter sync's deactivation sweep would flip them to
    `active=False` (a self-inflicted, silent regression an hour after seeding).
  - Rejected: reviving `openai_seed.py`'s dead `OPENAI_SEED_MODELS` in the same change — conflates
    two unrelated providers' catalog-seeding fixes into one contract, changes existing OpenAI
    catalog behavior unrequested, and isn't needed to satisfy this milestone's exit criterion.
Must:
<must>
  - `MINIMAX_SEED_MODELS: list[CatalogModel]` (new module, mirrors `OPENAI_SEED_MODELS` shape) lists
    MiniMax-M3, MiniMax-M2.7, MiniMax-M2.7-highspeed — each `modality="chat"`, `provider="minimax"`,
    `input_modalities="text"`, real per-token pricing converted from the confirmed $/M-token rates,
    real `context_length` (1,000,000 for M3; 204,800 for the M2.7 pair).
  - A new `CompositeCatalogSource` (catalog/infrastructure) implements the `CatalogSource` Protocol;
    `list_models()` yields every OpenRouter chat model THEN every `MINIMAX_SEED_MODELS` entry;
    `list_embedding_models()` yields OpenRouter's embeddings only (unchanged today).
  - `main.py`'s `app.state.catalog_source` wiring is replaced with the composite (OpenRouter
    instance + `MINIMAX_SEED_MODELS` injected) — no other boot wiring changes.
  - `SqlAlchemyCatalogRepository._upsert_model()` is extended to set `provider` and
    `input_modalities` from `model.provider`/`model.input_modalities` on BOTH the INSERT `.values()`
    and the `on_conflict_do_update.set_={}` — fixing the confirmed pre-existing bug so ANY
    non-default provider (not just minimax) persists correctly going forward.
  - After a `POST /internal/catalog/sync` (or `/admin/catalog/sync`), the `models` table contains
    exactly the 3 MiniMax rows above with `active=true`, alongside OpenRouter's own rows unaffected.
  - A subsequent sync run (simulating "OpenRouter's feed changed, MiniMax's static list didn't")
    leaves the MiniMax rows `active=true` — they are never swept by the deactivation logic since
    their ids are always part of `incoming_ids`.
  - `GET /v1/models` and `GET /admin/catalog/models` both list the 3 MiniMax models once synced,
    with the same provider/modality/input_modalities exposure rules already enforced for every other
    provider (admin sees `input_modalities`, public `/v1/models` does not — unchanged existing rule).
</must>
Reject:
<reject>
  - None of this task's changes introduce a new client-facing rejection path — no new endpoint,
    no new request shape. The only "reject"-shaped behavior is implicit: a MiniMax model id must
    never collide with an existing OpenRouter model id (defensive assumption, see ⚠ below) -> if it
    ever did, `_upsert_model`'s ON CONFLICT would silently overwrite the OpenRouter row's
    provider/pricing with MiniMax's, which would be a real, observable data-corruption bug, not a
    handled rejection — flagged as the top assumption below rather than a Reject-code, since there
    is no user input to validate against; it's a data hygiene property of the two source lists.
</reject>
After:
<after>
  - The `models` table has >=3 active rows with `provider="minimax"`, `modality="chat"`, correct
    `context_length` and real per-token pricing, discoverable via `/v1/models` and
    `/admin/catalog/models`.
  - OpenRouter's existing sync behavior (upsert/snapshot/deactivate for its own models) is
    byte-identical to before this task — verified by the full existing `test_model_catalog.py` suite
    passing unmodified.
  - `_upsert_model`'s provider/input_modalities bug is fixed for every provider, not just minimax —
    a regression-guard test pins OpenRouter rows still get `provider="openrouter"` explicitly
    written (not just defaulted) after this fix.
  - Task 3 (`minimax-live-verify`) can route a real chat completion to `MiniMax-M3` (or another
    seeded id) because the catalog now has a real, priced, active row for it.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ MiniMax-M3's pricing is CONTEXT-TIERED ($0.30/$1.20 per-M ≤512k vs. $0.60/$2.40 per-M >512k)
    but `CatalogModel`/`pricing_snapshots` only support ONE flat price pair per model id — lowest
    confidence because there is no existing tiered-pricing mechanism anywhere in this catalog schema
    to extend cleanly within this task's scope. Resolution: seed the ≤512k (cheaper) tier as the flat
    rate — matches the common case, but a genuine >512k-context MiniMax-M3 request will be
    UNDER-billed relative to MiniMax's real invoice to us. Cost if wrong: a real (if likely small,
    given >512k-context requests are rare) billing under-recovery — logged here as a known
    limitation, not silently swallowed; worth a `[SPEC · open]` delta for a future tiered-pricing
    task if MiniMax usage volume justifies it.
  - [ ] MiniMax model ids (`MiniMax-M3`, `MiniMax-M2.7`, `MiniMax-M2.7-highspeed`) never collide with
    any current or future OpenRouter catalog id — confirmed true TODAY (grepped current OpenRouter
    naming convention uses `vendor/model` slugs, e.g. `anthropic/claude-opus-4`; MiniMax's bare
    `MiniMax-*` ids don't match that shape) but not contractually guaranteed by OpenRouter — if it
    ever collided, `_upsert_model`'s ON CONFLICT would let whichever source's sync ran LAST silently
    overwrite the other's provider/pricing on that one row.
  - [ ] Seeding only the 3 "Current" models (M3, M2.7, M2.7-highspeed) and deferring the 5 "Legacy"
    variants (M2.5/M2.1/M2 + highspeed) satisfies the milestone's ">=1 MiniMax chat model" exit
    criterion — confirmed sufficient by the milestone text itself, but a narrower choice than "seed
    everything MiniMax documents"; the seed list is a plain Python list, trivially extensible later.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: MINIMAX_SEED_MODELS lists 3 correctly-priced chat entries
  Given the new MINIMAX_SEED_MODELS module constant
  When it is inspected
  Then it contains exactly 3 CatalogModel entries: "MiniMax-M3", "MiniMax-M2.7", "MiniMax-M2.7-highspeed"
  And each has modality="chat", provider="minimax", input_modalities="text"
  And MiniMax-M3 has context_length=1000000, prompt_usd_per_token=0.0000003, completion_usd_per_token=0.0000012
  And MiniMax-M2.7 has context_length=204800, prompt_usd_per_token=0.0000003, completion_usd_per_token=0.0000012
  And MiniMax-M2.7-highspeed has context_length=204800, prompt_usd_per_token=0.0000006, completion_usd_per_token=0.0000024

Scenario: CompositeCatalogSource chains OpenRouter chat models with the MiniMax seed
  Given a CompositeCatalogSource wrapping a fake OpenRouter-shaped source yielding 2 chat models
  When list_models() is consumed to completion
  Then all 2 OpenRouter models are yielded first, followed by all 3 MINIMAX_SEED_MODELS entries
  And the total yielded count is 5

Scenario: CompositeCatalogSource delegates embeddings to OpenRouter only
  Given a CompositeCatalogSource wrapping a fake OpenRouter-shaped source yielding 1 embedding model
  When list_embedding_models() is consumed to completion
  Then exactly the 1 OpenRouter embedding model is yielded
  And no MiniMax entry appears (MiniMax has no embeddings modality)

Scenario: main.py wires the composite source in place of the bare OpenRouter source
  Given the app is booted via create_app()
  When app.state.catalog_source is inspected
  Then it is a CompositeCatalogSource instance
  And its wrapped OpenRouter client / MINIMAX_SEED_MODELS are the same objects/values used elsewhere in boot

Scenario: a full sync persists the MiniMax rows as active with correct provider/modality
  Given the composite source is installed and the repository is empty
  When POST /internal/catalog/sync is called
  Then the models table contains 3 rows with id in ("MiniMax-M3","MiniMax-M2.7","MiniMax-M2.7-highspeed")
  And each row has active=true, provider="minimax", modality="chat", input_modalities="text"
  And each row's pricing_snapshots latest entry matches its seeded prompt/completion price

Scenario: _upsert_model persists provider and input_modalities on first insert
  Given the models table has no row for a given CatalogModel.id
  When _upsert_model(model) is called directly with provider="minimax", input_modalities="text"
  Then the inserted row's provider column equals "minimax" (not the "openrouter" server_default)
  And the inserted row's input_modalities column equals "text"

Scenario: _upsert_model persists provider and input_modalities on conflict-update
  Given an existing row for a model.id with provider="openrouter"
  When _upsert_model(model) is called again with the same id but provider="minimax"
  Then the row's provider column is updated to "minimax"
  And the row's input_modalities column is updated to match the new model's value

Scenario: OpenRouter rows keep explicit provider="openrouter" after the _upsert_model fix
  Given a CatalogModel from OpenRouterCatalogSource with provider="openrouter" explicitly set
  When _upsert_model(model) is called
  Then the row's provider column equals "openrouter" (written explicitly, not merely defaulted)
  And this is unchanged behavior from before the fix (same observable value, now via explicit write)

Scenario: a second sync run does not deactivate MiniMax rows
  Given the MiniMax rows are active from a prior sync via the composite source
  When a second POST /internal/catalog/sync runs (OpenRouter's fake source returns a different model set)
  Then the MiniMax rows remain active=true
  And they are not present in any "notin_(incoming_ids)" deactivation update

Scenario: synced MiniMax models are visible on public and admin catalog endpoints
  Given the MiniMax rows are active and priced from a prior sync
  When GET /v1/models and GET /admin/catalog/models are called
  Then both list all 3 MiniMax model ids with provider="minimax"
  And GET /admin/catalog/models includes input_modalities=["text"] for each
  And GET /v1/models does NOT include an input_modalities field (existing public-surface rule, unchanged)

Scenario: existing OpenRouter sync behavior is byte-identical after this task
  Given the full pre-existing tests/catalog/test_model_catalog.py suite
  When it is run unmodified against the post-build code
  Then every test passes exactly as before (no assertions changed)
  And this proves OpenRouter's upsert/snapshot/deactivate cycle is untouched by the composite source
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

No new HTTP endpoint — this task is pure catalog-data + wiring. The observable shape is: new module
`catalog/infrastructure/minimax_seed.py`, new class `CompositeCatalogSource`, an extended
`_upsert_model`, and one `main.py` wiring-line swap. Existing `/internal/catalog/sync`,
`/admin/catalog/sync`, `/v1/models`, `/admin/catalog/models` REQUEST/RESPONSE shapes are REUSED
UNCHANGED — this contract only adds rows those endpoints already know how to surface.

```python
# apps/gateway/src/gateway/catalog/infrastructure/minimax_seed.py  (NEW FILE)
"""Fixed seed list of known MiniMax chat model entries — minimax-catalog-seed TASK.md §3.

Mirrors OPENAI_SEED_MODELS' shape (openai_seed.py). MiniMax has no OpenRouter-style discovery
API wired into this proxy, so these 3 "Current"-tier models are hand-seeded with real pay-as-you-go
pricing (https://platform.minimax.io/docs/guides/pricing-paygo.md, fetched 2026-07-01). The 5
"Legacy" MiniMax models (M2.5/M2.1/M2 + highspeed variants) are deliberately NOT seeded here — see
TASK.md §1 Assumptions; add them to this list later if needed, additive-only.

MiniMax-M3 pricing is CONTEXT-TIERED upstream ($0.30/$1.20 per-M-tokens in/out for <=512k context,
$0.60/$2.40 for >512k) but CatalogModel supports only one flat price pair per id — the <=512k
(cheaper) tier is used as the flat rate; a >512k-context request is under-billed relative to
MiniMax's real invoice (documented limitation, TASK.md §1 top ⚠).
"""

from __future__ import annotations

from gateway.catalog.domain.entities import CatalogModel

MINIMAX_SEED_MODELS: list[CatalogModel] = [
    CatalogModel(
        id="MiniMax-M3",
        name="MiniMax-M3",
        context_length=1_000_000,
        prompt_usd_per_token=0.0000003,
        completion_usd_per_token=0.0000012,
        modality="chat",
        provider="minimax",
        input_modalities="text",
    ),
    CatalogModel(
        id="MiniMax-M2.7",
        name="MiniMax-M2.7",
        context_length=204_800,
        prompt_usd_per_token=0.0000003,
        completion_usd_per_token=0.0000012,
        modality="chat",
        provider="minimax",
        input_modalities="text",
    ),
    CatalogModel(
        id="MiniMax-M2.7-highspeed",
        name="MiniMax-M2.7-highspeed",
        context_length=204_800,
        prompt_usd_per_token=0.0000006,
        completion_usd_per_token=0.0000024,
        modality="chat",
        provider="minimax",
        input_modalities="text",
    ),
]

__all__ = ["MINIMAX_SEED_MODELS"]
```

```python
# apps/gateway/src/gateway/catalog/infrastructure/composite_source.py  (NEW FILE)
"""CompositeCatalogSource — chains a dynamic CatalogSource with a static seed list.

minimax-catalog-seed TASK.md §3. Implements the CatalogSource Protocol unchanged (structural,
no inheritance needed — Protocol is runtime_checkable-compatible by shape). Lets SyncCatalogUseCase
and SqlAlchemyCatalogRepository.sync_catalog() stay byte-identical: MiniMax's static rows simply
become part of the SAME `models` list passed into one sync_catalog() call, which is what keeps
them out of the deactivation sweep's `notin_(incoming_ids)` blast radius (TASK.md §0 Touches).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from gateway.catalog.domain.entities import CatalogModel
from gateway.catalog.domain.ports import CatalogSource


class CompositeCatalogSource:
    """Chains `primary` (dynamic, e.g. OpenRouter) chat models with a static `static_models` list.

    list_models(): yields every model from `primary.list_models()`, THEN every entry in
    `static_models`, in that order (order asserted by TASK.md §2 scenario, not load-bearing
    elsewhere — sync_catalog()'s upsert loop is order-independent).
    list_embedding_models(): delegates to `primary` ONLY — `static_models` never contributes
    embedding rows (MiniMax has no embeddings surface; out of milestone scope).
    """

    def __init__(self, primary: CatalogSource, static_models: list[CatalogModel]) -> None:
        self._primary = primary
        self._static_models = static_models

    async def list_models(self) -> AsyncIterator[CatalogModel]:
        async for model in self._primary.list_models():
            yield model
        for model in self._static_models:
            yield model

    async def list_embedding_models(self) -> AsyncIterator[CatalogModel]:
        async for model in self._primary.list_embedding_models():
            yield model


__all__ = ["CompositeCatalogSource"]
```

```python
# apps/gateway/src/gateway/catalog/infrastructure/repository.py  — _upsert_model EXTENDED
# (diff shape; exact surrounding code at repository.py:195-222 is REUSED, only .values()/set_ grow)
# AMENDED post-freeze (v2, during BUILD, 2026-07-01 — Tin approved via AskUserQuestion): the v1
# diff below wrote input_modalities on BOTH insert and conflict-update. Running the full existing
# regression suite surfaced that this clobbers model-input-capabilities TASK.md §2 SC5's frozen
# invariant ("_upsert_model never clobbers a seeded/admin-set input_modalities value on re-sync") —
# `tests/catalog_input_modalities/test_input_modalities.py::test_sc5_seed_sets_sync_never_clobbers`
# failed for real (not an environment flake). Fix: input_modalities moves to INSERT-only; provider
# is UNAFFECTED (no equivalent no-clobber invariant exists for it, and this task's own scenario
# explicitly requires provider to update on conflict) and stays in both.
    async def _upsert_model(self, model: CatalogModel) -> None:
        """Insert or update (on conflict) the model row, setting active=true.

        minimax-catalog-seed TASK.md §3: now writes `provider` on BOTH the insert and the
        conflict-update, fixing the pre-existing bug where it silently fell back to the column
        server_default regardless of the in-memory CatalogModel's value. `input_modalities` is
        written on INSERT only — model-input-capabilities TASK.md §2 SC5 froze the invariant that
        sync must never clobber a seeded/admin-set input_modalities value on re-sync, so it is
        deliberately absent from the conflict-update `set_`.
        """
        stmt = (
            pg_insert(ModelRow)
            .values(
                id=model.id,
                name=model.name,
                context_length=model.context_length,
                active=True,
                modality=model.modality,
                provider=model.provider,                      # NEW
                input_modalities=model.input_modalities,       # NEW (insert-only)
            )
            .on_conflict_do_update(
                index_elements=["id"],
                set_={
                    "name": model.name,
                    "context_length": model.context_length,
                    "active": True,
                    "modality": model.modality,
                    "provider": model.provider,                     # NEW
                    # input_modalities deliberately OMITTED here — SC5 no-clobber invariant.
                },
            )
        )
        await self._session.execute(stmt)
```

```python
# apps/gateway/src/gateway/main.py:627-628 — wiring REPLACED
# BEFORE:
#     app.state.catalog_source = OpenRouterCatalogSource(httpx.AsyncClient())
# AFTER:
    app.state.catalog_source = CompositeCatalogSource(
        primary=OpenRouterCatalogSource(httpx.AsyncClient()),
        static_models=MINIMAX_SEED_MODELS,
    )
```

Schema: no migration — `models`/`pricing_snapshots` tables and their columns already exist
(provider/modality/input_modalities added by prior tasks). This task only WRITES correctly to
already-existing columns via the extended `_upsert_model`; no DDL change.

REUSED UNCHANGED (explicitly, so no test may claim these as "this task's contract"):
  - `POST /internal/catalog/sync`, `POST /admin/catalog/sync` request/response shapes
    (`catalog/api/router.py`, `catalog/api/schemas.py`)
  - `GET /v1/models`, `GET /admin/catalog/models` request/response shapes
  - `SyncCatalogUseCase.__init__`/`.execute()` signature (untouched — receives whatever
    `CatalogSource` app.state wires in, structurally, no code change to this class)
  - `CatalogSource` Protocol definition (untouched — `CompositeCatalogSource` merely implements it)

Status: FROZEN @ v2 — approved by Tin Dang (2026-07-01); v1 → v2 amendment (2026-07-01, same day,
  during BUILD) approved by Tin Dang via AskUserQuestion: `_upsert_model`'s `input_modalities` write
  scoped to INSERT-only (see amendment note above the code block) after the full regression suite
  surfaced a real conflict with model-input-capabilities TASK.md §2 SC5's frozen no-clobber
  invariant. No scenario, test, or REUSED-UNCHANGED item changed — only the `_upsert_model` code
  shape and this Status line.
Least-sure flag surfaced at freeze:
⚠ [spec] MiniMax-M3's context-tiered pricing ($0.30/$1.20 <=512k vs $0.60/$2.40 >512k) is flattened
to the cheaper <=512k rate because CatalogModel has no tiered-pricing field — lowest confidence
because this is a real, if likely small, billing under-recovery for genuine >512k-context MiniMax-M3
requests, not a hypothetical; cost if wrong: MiniMax's real invoice to us exceeds what we charge the
tenant for those specific requests. Carried forward as a `[SPEC · open]` delta for a future
tiered-pricing task, not silently fixed here (out of this task's scope — no tiered-pricing mechanism
exists anywhere in the catalog schema to extend safely within one task).
⚠ [contract] `_upsert_model`'s fix changes behavior for EVERY existing provider's re-sync (not just
minimax) — lowest confidence because the full pre-existing `tests/catalog/test_model_catalog.py`
suite is the only safety net proving OpenRouter rows still resolve to `provider="openrouter"`
byte-identically after the fix; if that suite has a gap, a live OpenRouter re-sync in production
could persist a wrong provider value for some row. Mitigated by TASK.md §2's explicit
"OpenRouter rows keep explicit provider=openrouter" scenario + running the full existing suite
unmodified at VERIFY.
CONFIRMED MATERIAL (2026-07-01, during BUILD): this flag's risk was real, but the actual gap was in
`tests/catalog_input_modalities/` (SC5's no-clobber invariant), not `test_model_catalog.py` — see
the v1→v2 contract amendment above. Running the FULL suite (not just the directly-touched one) at
BUILD, before VERIFY, is what caught it — narrower re-runs of only `tests/catalog/` would have
stayed green while shipping a real clobber bug.
<!-- The freeze IS the one approval — lead it with the bundle's lowest-confidence flag: the 1–2
     points most likely wrong across the whole bundle, tagged [spec|scenario|contract|test], each
     with why + cost (the §1 ⚠ assumptions feed it; a flag may point at a scenario or the contract
     too — see run.md). Approved -> Status: FROZEN @ vN — approved by <name>. Changing a frozen
     contract = change request back to SPECIFY.
     EXIT: frozen + every spec rejection has a contracted response + names match GLOSSARY + the
     bundle's lowest-confidence flag was surfaced at the freeze (or an honest "none material"). -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: no decrease vs. current baseline (additive module + a repository bug fix; no new
  uncovered branches introduced by BUILD).
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_minimax_seed_models_has_three_correctly_priced_chat_entries: MINIMAX_SEED_MODELS ==
    exactly {MiniMax-M3, MiniMax-M2.7, MiniMax-M2.7-highspeed}, each modality=chat/provider=minimax/
    input_modalities=text, with the real pay-as-you-go per-token prices + context lengths
  - test_composite_source_chains_primary_then_minimax_seed: list_models() yields primary's models
    first, then MINIMAX_SEED_MODELS, in order
  - test_composite_source_embeddings_delegate_to_primary_only: list_embedding_models() yields only
    primary's models — MiniMax never contributes an embedding row
  - test_main_wires_composite_catalog_source: app.state.catalog_source is a CompositeCatalogSource
    wrapping OpenRouterCatalogSource as primary and MINIMAX_SEED_MODELS as static_models
  - test_upsert_model_persists_provider_on_insert: sync_catalog() with provider="minimax" persists
    provider="minimax" on first INSERT (not the "openrouter" column default)
  - test_upsert_model_persists_provider_on_conflict_update: re-sync with a changed provider updates
    the stored provider value on conflict
  - test_openrouter_rows_keep_explicit_provider_after_fix: sync_catalog() with provider="openrouter"
    still persists "openrouter" (regression guard — must stay green pre- and post-fix)
  - test_full_sync_persists_minimax_rows_active_with_correct_fields: a full
    POST /internal/catalog/sync with the composite source installed persists all 3 MiniMax rows
    active=true with correct provider/modality/input_modalities + a pricing snapshot each
  - test_second_sync_does_not_deactivate_minimax_rows: a second sync with a DIFFERENT primary feed
    leaves the MiniMax rows active (proves they're outside the deactivation sweep's blast radius)
  - test_v1_models_and_admin_catalog_models_list_minimax_with_correct_shape: synced MiniMax models
    appear on both GET /v1/models (no input_modalities field) and GET /admin/catalog/models
    (input_modalities=["text"])
  - (evidence, not a new test) full existing `tests/catalog/test_model_catalog.py` suite run at
    BUILD/VERIFY — proves OpenRouter sync behavior is unaffected after the fix. Its local
    `FakeCatalogModel` test double needed a 2-field WIDENING (`provider: str = "openrouter"`,
    `input_modalities: str = "text"`, mirroring the exact precedent already in that file for
    `modality`) once `_upsert_model` started reading those fields directly — no assertion in the
    suite was touched or weakened, only the fake's default fields grew to match the real
    `CatalogModel`'s defaults.
</test_plan>

RED CONFIRMED (2026-07-01): `apps/gateway/tests/minimax_catalog_seed/` — 9 failed / 1 correctly-green
regression-guard (`test_openrouter_rows_keep_explicit_provider_after_fix`, which must and does pass
today since `OpenRouterCatalogSource` already sets provider="openrouter" explicitly, coincidentally
matching the column default — this is the RIGHT pre-build state per TASK.md §0's documented bug).
Of the 9 failures: 7 fail on `ModuleNotFoundError` for `composite_source`/`minimax_seed` (not yet
built) and 2 fail on the exact predicted bug (`test_upsert_model_persists_provider_on_insert` /
`..._on_conflict_update`, both asserting `got 'openrouter'` instead of `'minimax'` — the column
server_default masking the unwritten field, exactly as documented in TASK.md §0 Touches). A
pre-existing, unrelated environment issue (an orphaned `tenant_model_presets` table left behind by
the parked `model-preset` sibling worktree — no live connections held it, confirmed via
`pg_stat_activity`) was found blocking all DB-bootstrap tests and cleared (`DROP TABLE ... CASCADE`)
before this red confirmation, mirroring the same issue and fix already documented in
`minimax-adapter-registry`'s TASK.md §4.

Tests live in: `apps/gateway/tests/minimax_catalog_seed/`, `apps/gateway/tests/catalog/`,
`apps/gateway/tests/catalog_sync_trigger/`, `apps/gateway/tests/catalog_input_modalities/`
(the latter three: widened fake / contract-corrected only, no assertion changed) · MUST run red
(missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/`, `apps/gateway/tests/`
Strategy (ordered batches):
  1. `catalog/infrastructure/minimax_seed.py` (new) — the static `MINIMAX_SEED_MODELS` list, no
     dependencies on anything else in this build.
  2. `catalog/infrastructure/composite_source.py` (new) — `CompositeCatalogSource`, depends only on
     the existing `CatalogModel`/`CatalogSource` (both untouched).
  3. `catalog/infrastructure/repository.py` — extend `_upsert_model`'s `.values()` and
     `on_conflict_do_update.set_={}` to write `provider`/`input_modalities` (the pre-existing bug fix).
  4. `main.py:627-628` — swap the bare `OpenRouterCatalogSource(...)` wiring for
     `CompositeCatalogSource(primary=OpenRouterCatalogSource(...), static_models=MINIMAX_SEED_MODELS)`.
Known-problem fixes: `_upsert_model` silently relying on `ModelRow` column server_defaults for
  `provider`/`input_modalities` (only "worked" for OpenRouter by coincidence) → planned fix: pass
  both fields explicitly in both the INSERT `.values()` and the conflict `set_={}` dict.
Strategy actually used: as planned (all 4 batches executed in order, all 10 new tests green); two
  unplanned fixups surfaced only by running progressively wider regression scopes:
  1. `tests/catalog/test_model_catalog.py`'s local `FakeCatalogModel` (a test double, not the real
     `CatalogModel`) had no `provider`/`input_modalities` fields, so the fix's direct
     `model.provider`/`model.input_modalities` reads raised `AttributeError`. Widened the fake with
     matching defaults (mirroring its own pre-existing `modality` widening from
     openrouter-embeddings-routing) — zero assertions touched. Same gap, same fix, found
     independently in `tests/catalog_sync_trigger/conftest.py`'s `FakeCatalogModel` once the FULL
     suite was run.
  2. Running the FULL `tests/` suite (not just `tests/catalog/`) surfaced a real conflict:
     `tests/catalog_input_modalities/`'s SC5 froze "sync never clobbers input_modalities" — see the
     v1→v2 contract amendment in §3. This was a genuine contract defect (not an environment flake
     or a test needing widening), corrected with Tin's approval via AskUserQuestion mid-build.
  Full-suite result after both fixes: `uv run pytest tests/ -q --no-cov -p no:cacheprovider` ->
  2100 passed / 0 failed / 0 errors / 7 skipped (pre-build baseline was 2090; +10 = exactly this
  task's new suite, no other count drift). An earlier full-suite run (before the SC5 fix, and
  concurrent with a since-corrected foreground/background pytest race against the shared dev
  Postgres) had shown 3 `DeadlockDetectedError` errors (pytest-xdist workers racing on concurrent
  schema DDL) — confirmed pre-existing/unrelated by re-running those 3 tests in isolation (all
  pass) and by their absence in this final clean 2100-passed run.
Safety rule (feature-specific): the deactivation sweep (`ModelRow.id.notin_(incoming_ids)`) must
  never fire for MiniMax rows — achieved structurally by making MiniMax ids part of the SAME
  `incoming_ids` set as OpenRouter's (via `CompositeCatalogSource`), not a separate sync call.
Code lives in: `apps/gateway/src/gateway/catalog/infrastructure/`, `apps/gateway/src/gateway/main.py`
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

- [x] all tests pass — 2100 passed / 0 failed / 0 errors / 7 skipped, full `tests/` suite
- [x] coverage did not decrease — additive-only code paths + a bug fix on an already-covered method;
      no branch introduced without a covering test
- [x] no test was WEAKENED and no frozen contract was silently edited — 2 pre-existing test doubles
      were WIDENED (new default-valued fields added, zero assertions touched: `test_model_catalog.py`
      + `catalog_sync_trigger/conftest.py` `FakeCatalogModel`); this task's OWN §3 CONTRACT was
      AMENDED v1→v2 mid-build with Tin's explicit re-approval (via AskUserQuestion) after a genuine
      defect was found by testing, not to force a pass — see §3/§5 for the full record
- [x] the green was EARNED, not gamed — adversarial refute-read completed (see below); VERDICT: EARNED
- [x] concurrency / timing of the risky operation is safe — `_upsert_model` is a single
      `pg_insert...on_conflict_do_update` statement per model, executed inside the existing
      `sync_catalog()` transaction/session; no new concurrency surface introduced. Confirmed the
      deactivation sweep cannot race MiniMax rows out: both OpenRouter's and MiniMax's ids are
      combined into ONE `incoming_ids` set before the sweep query runs (traced in
      `sync_catalog()`, not assumed) — refute-read independently confirmed this by reading the code.
- [x] no exposed secrets, injection openings, or unexpected dependencies — all DB writes remain
      parameter-bound via SQLAlchemy `pg_insert().values()`/`on_conflict_do_update(set_=...)`; no
      new secret, no new third-party dependency; refute-read checked and found nothing.
- [x] layering & dependencies follow CONVENTIONS.md — `minimax_seed.py`/`composite_source.py` sit in
      `catalog/infrastructure/` alongside their siblings (`openai_seed.py`, `openrouter_source.py`);
      `CompositeCatalogSource` depends only on the domain `CatalogModel`/`CatalogSource` port, no
      infrastructure-to-infrastructure coupling beyond what `SyncCatalogUseCase` already expects.
- [x] a person reviewed and approved the change — Tin Dang approved the §3 CONTRACT freeze
      (2026-07-01) and the v1→v2 amendment mid-build (2026-07-01, via AskUserQuestion); this is the
      AI-driven-build / human-freeze-approval pattern, matching `minimax-adapter-registry`'s
      precedent GATE RECORD.

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] `MINIMAX_SEED_MODELS` has exactly 3 entries with real pay-as-you-go pricing/context — confirmed
      by `test_minimax_seed_models_has_three_correctly_priced_chat_entries` passing AND by the
      refute-read agent independently re-fetching MiniMax's live pricing page and diffing the values
      (exact match, no transcription error).
- [x] A real `POST /internal/catalog/sync` persists all 3 MiniMax rows active, correct
      provider/modality/input_modalities, each with a pricing snapshot — confirmed by
      `test_full_sync_persists_minimax_rows_active_with_correct_fields` passing against the real
      DB-backed `app`/`client`/`db_session` fixtures (not mocked).
- [x] MiniMax rows survive a second sync where OpenRouter's feed changes entirely (never
      deactivated) — confirmed by `test_second_sync_does_not_deactivate_minimax_rows` passing, AND
      by the refute-read agent tracing `sync_catalog()`'s `incoming_ids` construction by hand to
      confirm this holds structurally, not just for this test's specific inputs.
- [x] Existing OpenRouter sync/catalog/admin behavior is unaffected by the `_upsert_model` fix —
      confirmed by the FULL pre-existing suite passing unmodified in assertions (2100/2100), plus the
      one genuine gap found (`catalog_input_modalities` SC5) was a real defect in this task's OWN
      draft contract, now corrected (v1→v2), not evidence of OpenRouter-specific breakage.
- [x] `app.state.catalog_source` is wired to a `CompositeCatalogSource` in the real `create_app()`
      boot path (not just in tests) — confirmed by `test_main_wires_composite_catalog_source` passing
      against the real `app` fixture (full app boot, not a stub).

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol is referenced: `MINIMAX_SEED_MODELS` is consumed exactly once,
      by `main.py`'s `CompositeCatalogSource(...)` construction; `CompositeCatalogSource` is
      constructed exactly once (same call site) and stored on `app.state.catalog_source`, the same
      attribute every catalog endpoint already reads through `SyncCatalogUseCase` — confirmed via
      `grep -n "MINIMAX_SEED_MODELS\|CompositeCatalogSource" src/gateway/main.py` (single wiring
      site) and by the refute-read agent's independent trace of the same call graph.
- [x] DEAD-CODE (code) — no new unused symbol: both new modules' only public export
      (`MINIMAX_SEED_MODELS`, `CompositeCatalogSource`) is imported and used in `main.py`; no
      orphaned helper left behind. `openai_seed.py`'s `OPENAI_SEED_MODELS` remains deliberately
      unwired (pre-existing, out of this task's scope, unchanged).
- [x] SEMANTIC (prose) — §0-§5 of this TASK.md re-read in full at refute-read time by an independent
      subagent (isolated context); no drift found between the frozen (and properly amended) §3
      CONTRACT and what §5 BUILD actually produced, beyond the one documented v1→v2 correction.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: agent (independent adversarial subagent, isolated context, instructed to try to REFUTE) ·
adversarially checked: whether MiniMax ids genuinely hit the ON-CONFLICT branch in practice (traced
`sync_catalog()`'s `incoming_ids` construction and `CompositeCatalogSource.list_models()`'s chaining
by hand, confirmed structural, re-derived independently rather than trusting TASK.md's narrative);
whether ANY other pre-existing test suite has an undiscovered local `FakeCatalogModel`/
`FakeCatalogSource` double that could also break (repo-wide grep for `class Fake.*Catalog`, found
exactly the 2 already-fixed doubles, no third); whether `CompositeCatalogSource.list_embedding_models`
is structurally immune to ever yielding a MiniMax row (confirmed — delegates to `primary` only, never
touches `static_models`); whether the SC5 no-clobber fix is genuinely exercised (not coincidentally
green) by re-reading `test_sc5_seed_sets_sync_never_clobbers` line-by-line; whether `CatalogModel`'s
real dataclass defaults actually match what the widened fakes assume (`entities.py:144-146`,
confirmed exact); whether the 3 seeded prices/context-lengths match MiniMax's real pricing page (live
re-fetched independently, exact match — no transcription error); injection/secret exposure (none —
all writes parameter-bound via `pg_insert`/`on_conflict_do_update`). Ran the 4 targeted suites itself
from raw output (not trusting my summary): `tests/minimax_catalog_seed/ tests/catalog/
tests/catalog_sync_trigger/ tests/catalog_input_modalities/` -> 46 passed, 1 warning, no skips.

Non-blocking findings from the refute-read (neither is a code defect; both left open, not fixed here):
  1. [low, test-strength] `test_second_sync_does_not_deactivate_minimax_rows` re-asserts only
     `active=True` after the second sync, not `provider=="minimax"` too — logically safe given the
     conflict-update path, but a stronger assertion would close residual doubt. Candidate for a
     follow-up hardening, not required for this gate.
  2. [low, unverified] MiniMax-M2.7/-M2.7-highspeed's `context_length=204_800` was sourced via ctx7
     MCP (per TASK.md §0) and could not be independently re-confirmed via a public web fetch this
     session (M3's 1,000,000 context WAS independently re-confirmed). Unrefuted, not confirmed wrong.
  3. [environment, unrelated] The working tree also carries `minimax-adapter-registry` (task 1)'s
     uncommitted changes (BYOK/provider-keys files) — expected, since no git commit has happened this
     session yet per Tin's standing instruction to ask before committing; not this task's scope.

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang (AI-driven build; human freeze-approval at §3 contract + the v1→v2 amendment,
both 2026-07-01) · date: 2026-07-01

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): `/admin/catalog/sync` success rate + the 3 MiniMax rows'
`active` status after every production sync (a regression here would silently deactivate MiniMax
from `/v1/models` without any error); catalog-sync latency (CompositeCatalogSource adds 3
in-memory-only yields after OpenRouter's network fetch — negligible, but worth a baseline).

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v2 (approved by Tin Dang (2026-07-01); v1 → v2 amendment (2026-07-01, same day,)
- [AI] build — strategy used: as planned (all 4 batches executed in order, all 10 new tests green); two
- [AI] verify — gate PASS (reviewed by Tin Dang (AI-driven build; human freeze-approval at §3 contract + the v1→v2 amendment,)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
  - [SPEC · open] Introduce a platform-wide superadmin role / system-or-platform tenant concept —
    today every `Role` value (owner/admin/operator/billing_admin/viewer/member) is strictly
    per-tenant (evidence: `tenants/domain/entities.py` Role enum + `authz.py` ROLE_PERMISSIONS, all
    tenant-scoped; confirmed via research agent, 2026-07-01) and the only existing tenant-agnostic
    mechanism is `/ops/*`'s mTLS-cert-based `require_ops` (one read-only endpoint,
    `GET /ops/reconciliation`, explicitly documented as "the ONE named, audited exception" to
    tenant-scoping — not a general-purpose admin identity). Motivating need: the
    `minimax-catalog-seed` follow-on (system-tenant-credential-based live model-id refetch +
    deprecated flag) has no natural "whose BYOK key authenticates this platform-wide action"
    answer without this. Tin confirmed wanting this tracked (2026-07-01) rather than inventing an
    ad hoc convention inside a single task.
  - [SPEC · open] System/ops-tenant-BYOK-credential-based MiniMax model-id refetch + deprecated-flag
    marking — deliberately split OUT of this task (Tin's decision, 2026-07-01, via AskUserQuestion:
    "Split it: ship minimax-catalog-seed now, refetch/deprecated as its own next task") once it
    became clear MiniMax's own `GET /v1/models` needs a Bearer key (no operator-level key exists)
    AND returns no pricing/context_length anyway (confirmed via ctx7 OpenAPI spec, 2026-07-01) — so
    a real refetch design needs a designated system/ops tenant's stored BYOK key (Tin's preferred
    direction) AND depends on the superadmin/system-tenant delta above for "whose identity runs it".
  - [SPEC · open] MiniMax-M3's context-tiered pricing ($0.30/$1.20 ≤512k vs $0.60/$2.40 >512k) is
    flattened to the cheaper ≤512k rate in `MINIMAX_SEED_MODELS` because `CatalogModel` has no
    tiered-pricing field (evidence: `catalog/domain/entities.py` — one flat `prompt_usd_per_token`/
    `completion_usd_per_token` pair per model id) — genuine, if likely small, billing
    under-recovery for real >512k-context MiniMax-M3 requests. Needs a tiered-pricing mechanism in
    the catalog schema before it can be fixed; flagged at contract freeze (2026-07-01), not
    silently fixed in this task (out of scope — no such mechanism exists to extend safely here).

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
<!-- e.g.  - [DDD · open] the model missed multi-tenancy (evidence: scenario_x failed) -->
  - [ADD · open] A contract's blast-radius risk flag (here: "`_upsert_model`'s fix changes behavior
    for EVERY existing provider's re-sync") should trigger running the FULL test suite before
    VERIFY, not just the directly-touched test directory — evidence: `tests/catalog/` alone stayed
    green while `tests/catalog_input_modalities/`'s SC5 (a different, already-shipped task's frozen
    no-clobber invariant) was silently broken by this task's first-draft `_upsert_model` diff;
    only a full-suite run surfaced it (minimax-catalog-seed TASK.md §5, 2026-07-01).
