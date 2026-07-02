# TASK: Coarse operation-type guard for chat/realtime-WS-chat

slug: chat-modality-guard · created: 2026-07-01 · stage: production
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
- `gateway/proxy/application/use_cases.py:984,1621` `CompletionUseCase._resolve_credential`/`stream()` — both call `self._provider_resolver.provider_for(model_id)`, the ONLY per-request model-keyed lookup chat's request-handling seam performs. Confirmed (this session, preset-capability-validation GROUND): zero `ModelRow` queries anywhere in this file.
- `gateway/proxy/application/use_cases.py:965` `_resolve_credential`'s own docstring: "This avoids adding a direct DB dependency to CompletionUseCase" — chat's architecture DELIBERATELY avoids a per-request DB round-trip for provider/model metadata; this is a real constraint the new guard must respect, not a coincidence to route around.
- `gateway/proxy/infrastructure/catalog_provider_resolver.py` (whole file, FROZEN @ v1 per `provider-chat-dispatch` TASK.md §3) `CatalogProviderResolver` — holds an in-memory `dict[str, str]` (`model_id -> provider`), populated at startup + refreshed on `/internal/catalog/sync`; docstring: "All reads are in-memory; the DB is only touched during refresh() — NEVER on the chat hot path." `provider_for()` "NEVER raises. Unknown/unset model_id returns 'openrouter'." THIS is the natural, zero-new-per-request-I/O extension point: growing the cached map to also carry `modality` (refreshed the same way) preserves the "never touches DB on the hot path" contract that a naive new `ModelRow.modality` SELECT per chat request would violate.
- `gateway/proxy/infrastructure/model_checker.py` `SqlAlchemyModelChecker.is_active`/`.check_for_tenant` — both queries select ONLY `ModelRow.active` (+ `TenantModelOverrideRow.enabled` for the tenant variant), never `.modality`. `is_active`'s docstring: "FROZEN — signature must not change; frozen proxy-completions fakes depend on it." Confirmed by preset-capability-validation's own regression test (`test_model_checker_activation_check_still_modality_agnostic`) that this checker is INTENTIONALLY modality-agnostic — it is NOT a viable piggyback point for this task; do not touch it.
- `gateway/proxy/api/deps.py:180,223` `get_completion_use_case` — reads `request.app.state.provider_resolver` and passes it into `CompletionUseCase(..., provider_resolver=provider_resolver)`.
- `gateway/proxy/api/realtime_ws.py:253,276` `_real_chat` — reads the SAME `app.state.provider_resolver` and passes it the SAME way. Unlike preset-resolution-ingress/preset-capability-validation (which each needed NEW explicit kwargs threaded into `_real_chat`'s construction), extending `provider_resolver`'s cached data is wired for BOTH the HTTP and realtime-WS chat paths through this ONE existing composition point — no new `_real_chat` wiring should be needed if the guard reads through `provider_resolver` (or a sibling object built the same way).
- `gateway/core/error_catalog.py` `MODEL_MODALITY_MISMATCH` (already exists, added by `preset-capability-validation`, this session) — 400, `"Model '{model_id}' does not support this operation"`. REUSE verbatim; do not redefine.
- `gateway/proxy/application/images_use_case.py:147-153` (precedent, for the SHAPE of the check only, not the I/O strategy — images/embeddings/TTS already had a live per-request `ModelRow` SELECT to piggyback on; chat does not, per above) `if row.modality != "image": raise MODEL_MODALITY_MISMATCH.exc(...)`.
- `gateway/catalog/domain/entities.py:18-22` `Modality = Literal["chat","embedding","image","audio_stt","audio_tts"]`.
- `gateway/main.py:657-666` — `_load_provider_map()` (a closure, ModelRow imported locally to dodge a circular import) already runs `select(ModelRow.id, ModelRow.provider)` and builds the exact `dict[str, str]` `CatalogProviderResolver(loader=_load_provider_map)` consumes. Extending this ONE SELECT to also fetch `.modality` (still one query, one row per model, same refresh cadence — at startup + `/internal/catalog/sync`) is the whole "new I/O" cost of this task: literally zero added per-request cost, one added column to an already-scheduled periodic refresh query.

Context (working folder): `.add/tasks/preset-capability-validation/TASK.md` §7 Spec delta (the seed source for this task — the severity analysis there is authoritative: upstream rejects a wrong-type chat model cleanly today, this is a DX/error-shape gap, not a silent-misbilling one).
Honors (patterns / conventions): the `CatalogProviderResolver` "never touches DB on the hot path" contract is a load-bearing convention for the highest-QPS endpoint in this gateway — any design that adds a per-request DB read for chat must be explicitly justified, not defaulted into.
Anchors the contract cites: `CatalogProviderResolver`, `MODEL_MODALITY_MISMATCH`, `CompletionUseCase.complete`/`.stream`, `_real_chat`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Chat (HTTP `/v1/chat/completions` + realtime-WS `_real_chat`) gains a coarse operation-type
guard symmetric to images/embeddings/TTS's `MODEL_MODALITY_MISMATCH` — a preset (or directly-named)
model whose catalog `modality` is known and isn't `"chat"` is rejected with a clean gateway 400,
instead of reaching the real upstream and failing there with a provider-specific error shape.

Framings weighed:
  (chosen) Extend `CatalogProviderResolver`'s existing cached `model_id -> provider` map (built by
  `main.py`'s `_load_provider_map()`, refreshed at startup + `/internal/catalog/sync`) to ALSO carry
  `modality`, and add a new `modality_for(model_id) -> str | None` method alongside the existing
  `provider_for()` — zero new per-request I/O (one extra column on an already-scheduled periodic
  query), zero new wiring at either `_real_chat` or `deps.py` (both already inject the SAME
  `provider_resolver` singleton). `CompletionUseCase.complete()`/`.stream()` call `modality_for()`
  once, after preset-resolve, and raise `MODEL_MODALITY_MISMATCH` (reused verbatim) if it returns a
  known value that isn't `"chat"`.
  · A brand-new per-request `ModelRow.modality` SELECT in `CompletionUseCase` (mirroring images/
    embeddings/TTS's literal insertion shape) — REJECTED: violates the explicit, documented
    "avoids adding a direct DB dependency to CompletionUseCase" design principle for the
    highest-QPS endpoint in the gateway (`use_cases.py:965`); the cached-map approach gets the
    identical safety property for zero added hot-path cost.
  · Piggyback on `SqlAlchemyModelChecker.is_active`/`.check_for_tenant` (already queries `ModelRow`
    per chat request) — REJECTED: both are FROZEN ("signature must not change; frozen
    proxy-completions fakes depend on it") and confirmed, by preset-capability-validation's own
    regression test, to be intentionally modality-agnostic; extending either would violate an
    existing frozen contract and a passing regression test.
Must:
<must>
  - A chat request (HTTP or realtime-WS) whose resolved model_id (preset-resolved or named
    directly) has a KNOWN cached `modality` that is NOT `"chat"` is rejected with
    `MODEL_MODALITY_MISMATCH` (400), fired before any upstream call, credential resolution, or
    usage record (single-bill invariant, matching the images/embeddings/TTS precedent).
  - The check reads `modality` from the SAME cached provider-resolver map `provider_for()` already
    reads — zero new per-request I/O; the ONE new query is `main.py`'s existing periodic
    `_load_provider_map()` refresh, extended by one column.
  - realtime-WS chat (`_real_chat`) gets the SAME guard automatically via the SAME
    `app.state.provider_resolver` instance — no new construction-site kwargs.
  - A chat model (the overwhelming common case) is byte-identical to today: zero new latency, zero
    new query, zero new error path taken.
  - A model whose modality is UNKNOWN to the cache (e.g. added to the catalog after the last sync,
    a stale-cache window) is treated as compatible — NOT rejected. Consistent with `provider_for()`'s
    own existing "unknown model_id -> 'openrouter'" fail-open default; the model's mere
    existence/active-state was already authoritatively checked earlier in the call order by
    `ModelChecker.is_active`/`.check_for_tenant` (via `_enforce_governance` -> `_check_model_catalog`).
</must>
Reject:
<reject>
  - A preset (or directly-named) model whose CACHED modality is known and != "chat" ->
    "ERR_MODEL_MODALITY_MISMATCH" (400)
</reject>
After:
<after>
  - A tenant preset (or direct model name) that maps a chat request to a non-chat model (e.g. an
    embedding or image model) gets a clean, gateway-level 400 with the existing error taxonomy,
    instead of reaching the real upstream and surfacing a provider-specific, inconsistent error.
  - `CatalogProviderResolver` carries `modality` alongside `provider` for every cached model, kept
    in sync by the SAME refresh mechanism (startup + `/internal/catalog/sync`) — no new schedule,
    no new trigger.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ Fail-open on a cache-miss (unknown modality -> treat as compatible, don't reject) is the RIGHT
  default vs. fail-closed (reject on unknown) — lowest confidence because it's a judgment call, not
  a fact I can verify in code: fail-open risks a genuinely-mismatched model slipping through during
  a stale-cache window (small, self-healing on the next sync); fail-closed risks a false-positive
  rejection of a legitimately-good, just-not-yet-synced new model. If wrong (should be fail-closed):
  cost is a narrow, time-boxed under-enforcement window, not a security hole — the ORIGINAL
  images/embeddings/TTS bug this guard family exists for was about ACTIVE, SYNCED models being
  misrouted, not about brand-new unsynced ones.
  - [x] Extending `CatalogProviderResolver`'s internal map value type (str -> a richer shape) breaks
    no other caller — CONFIRMED safe: grepped every access; the only other consumer
    (`ProviderAwareCompletionUpstream`) calls the public `provider_for()` method only, never
    touches the internal map directly, and `provider_for()`'s own signature/behavior is unchanged
    by this task (a new `modality_for()` method is added alongside it, nothing is replaced).
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: chat request to a compatible chat model is byte-identical to today
  Given a tenant sends /v1/chat/completions (or opens realtime-WS chat) resolving (directly or via
    preset) to a model_id whose cached modality is "chat"
  When the request is handled
  Then it proceeds exactly as before this task — no new latency, no new query, no new error path
  And the response/upstream call/usage record are unaffected by this change

Scenario: chat request to a known-incompatible model is rejected before any I/O
  Given a tenant sends a chat request resolving (directly or via preset) to a model_id whose cached
    modality is known and is NOT "chat" (e.g. an embedding or image model)
  When the request is handled
  Then it is rejected with MODEL_MODALITY_MISMATCH (400) before credential resolution, the upstream
    call, or any usage record
  And zero upstream calls and zero usage rows are produced for this request

Scenario: realtime-WS chat gets the same guard with no new wiring
  Given a tenant opens a realtime-WS chat session resolving to a model_id whose cached modality is
    known and is NOT "chat"
  When the first chat turn is handled by `_real_chat`
  Then it is rejected with MODEL_MODALITY_MISMATCH (400) via the SAME `app.state.provider_resolver`
    instance HTTP chat uses — no new constructor kwargs added to `_real_chat`
  And zero upstream calls and zero usage rows are produced for this request

Scenario: chat request to a model with unknown (not-yet-synced) modality is treated as compatible
  Given a tenant sends a chat request resolving to a model_id present in the resolver's provider map
    but with no cached modality entry (e.g. added after the last catalog sync)
  When the request is handled
  Then it proceeds (fail-open) — MODEL_MODALITY_MISMATCH is NOT raised on this check alone
  And the pre-existing `ModelChecker.is_active`/`.check_for_tenant` active/existence gate still runs
    unchanged earlier in the call order

Scenario: catalog sync/startup refresh populates modality alongside provider with one query
  Given the gateway starts up or `/internal/catalog/sync` runs
  When `_load_provider_map()` executes
  Then the SAME single query now also selects `ModelRow.modality`, and `CatalogProviderResolver`'s
    cache carries both `provider` and `modality` per model_id after the refresh
  And no second query or new schedule/trigger is introduced
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
POST /v1/chat/completions   body: { model, messages, ... }     (unchanged fields)
WS   /v1/realtime            first chat-turn frame: { model, ... }   (unchanged fields)
  200 / streamed -> unchanged success shape (no new fields)
  400 -> { error: "MODEL_MODALITY_MISMATCH" }   # model_id resolves (direct or preset) to a
                                                  # cached, KNOWN, non-"chat" modality
Schema:
  - CatalogProviderResolver internal cache: value type grows from `str` (provider) to a small
    struct/tuple carrying `{provider, modality}`; new method `modality_for(model_id) -> str | None`
    added alongside the existing `provider_for(model_id) -> str` (unchanged signature/behavior).
  - gateway/main.py `_load_provider_map()`: SAME single query, extended to also SELECT
    `ModelRow.modality`; no new query, no new refresh trigger.
  - CompletionUseCase.complete()/.stream(): one new call to `modality_for(model_id)` after
    preset-resolution, before `_resolve_credential`/upstream dispatch/usage recording; raises
    `MODEL_MODALITY_MISMATCH.exc(model_id)` (error already defined by preset-capability-validation,
    reused verbatim, NOT redefined) only when the returned value is known and != "chat"; a `None`
    (unknown/not-yet-cached) result is treated as compatible (fail-open), matching `provider_for()`'s
    own existing "unknown -> default" precedent.
  - realtime_ws.py `_real_chat`: NO changes — it already shares the same `app.state.provider_resolver`
    instance injected via the same composition point as HTTP chat, so it inherits the guard for free.
```

Status: FROZEN @ v1 — approved by Tin Dang (via AskUserQuestion, 2026-07-01): "Freeze v1 as drafted".
Least-sure flag surfaced at freeze: [spec] fail-open-on-unknown-modality (§1 ⚠) — treating a
not-yet-cache-synced model as chat-compatible rather than rejecting it. Confidence this is the right
default: moderate-high (mirrors `provider_for()`'s own existing fail-open precedent; the existence/
active-state gate already runs earlier via `ModelChecker`). If wrong: a narrow, self-healing
under-enforcement window (closes on the next catalog sync), not a security hole — this guard's
origin bug (preset-capability-validation) was about ACTIVE, SYNCED models being misrouted, not
brand-new unsynced ones. Tin approved this default explicitly via AskUserQuestion at freeze time.
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
  - test_resolver_modality_for_returns_cached_value_else_none: arrange a CatalogProviderResolver
    with both loader + modality_loader wired / act refresh() then modality_for() for a known and
    an unknown model_id / assert cached value returned, None for unknown
  - test_resolver_modality_for_none_when_no_modality_loader_wired: arrange a CatalogProviderResolver
    constructed the OLD way (loader only, no modality_loader — mirrors the frozen
    provider_chat_dispatch suite's construction) / act refresh() then modality_for() / assert
    None always + provider_for() still works unchanged (backward compat, byte-identical)
  - test_chat_to_compatible_model_byte_identical: arrange a chat model seeded + cache refreshed with
    modality="chat" / act POST /v1/chat/completions / assert 200 + upstream called exactly once
    (zero new latency/query/error path)
  - test_chat_to_known_incompatible_model_rejected: arrange an embedding-modality model seeded +
    cache refreshed / act POST /v1/chat/completions naming it / assert 400
    ERR_MODEL_MODALITY_MISMATCH + zero upstream calls + zero usage rows
  - test_chat_to_unknown_modality_model_is_fail_open: arrange a chat-eligible model seeded but the
    resolver NOT refreshed after seeding (cache miss) / act POST /v1/chat/completions / assert 200
    + upstream called (fail-open, ModelChecker's own active-check already gates existence)
  - test_realtime_ws_real_chat_wires_chat_modality_lookup: arrange a fake WS app.state mirroring
    preset-resolution-ingress's wiring test, monkeypatching CompletionUseCase with a recording fake
    / act realtime_ws._real_chat(...) / assert the SAME app.state.provider_resolver object reaches
    the constructor as chat_modality_lookup= (no new app.state attribute, no new instance)
</test_plan>

Tests live in: `apps/gateway/tests/chat_modality_guard/` · MUST run red (missing implementation) before Build.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch):
  `apps/gateway/src/gateway/proxy/infrastructure/catalog_provider_resolver.py` (additive modality_loader/modality_for)
  `apps/gateway/src/gateway/proxy/domain/ports.py` (add new ChatModalityLookup Protocol)
  `apps/gateway/src/gateway/proxy/application/use_cases.py` (add chat_modality_lookup + guard method + 2 call sites)
  `apps/gateway/src/gateway/proxy/api/deps.py` (wire chat_modality_lookup=provider_resolver)
  `apps/gateway/src/gateway/proxy/api/realtime_ws.py` (wire chat_modality_lookup into _real_chat)
  `apps/gateway/src/gateway/main.py` (extend _load_provider_map query + add _load_modality_map)
  `apps/gateway/tests/chat_modality_guard/` (NEW test dir, all 7 tests)
Strategy (ordered batches):
  1. Write all 6 tests RED first (missing `modality_for`/`modality_loader`/`chat_modality_lookup` ->
     TypeError/AttributeError, or assertion failures for the right reason).
  2. Extend `CatalogProviderResolver` (additive `modality_loader`/`_modality_map`/`modality_for()`).
  3. Add the new `ChatModalityLookup` Protocol to `ports.py`.
  4. Add `chat_modality_lookup` ctor param + `_check_chat_modality()` to `CompletionUseCase`; insert
     the one call site in `complete()` and the one in `stream()`, immediately after
     `_check_input_modalities` (mirrors its placement: after governance, before credential
     resolution/upstream/usage — single-bill invariant preserved).
  5. Wire `deps.py` + `realtime_ws.py` (one new kwarg line each, reusing the existing
     `provider_resolver` local var — no new app.state attribute, no new instance).
  6. Extend `main.py`'s `_load_provider_map()` query + add `_load_modality_map()` + pass
     `modality_loader=` into the constructor call.
  7. Run the new suite green, then the full gateway suite; confirm the byte-identical scenario is
     unaffected and no other suite's chat tests regress (fail-open makes this safe by construction:
     no other suite refreshes the resolver after seeding a model, so modality stays uncached there).
Known-problem fixes:
  trap: repurposing `CatalogProviderResolver`'s existing `loader`/`_map` to also carry modality
    (e.g. richer tuple values) -> BREAKS the frozen `provider_chat_dispatch` suite, whose fakes
    construct `CatalogProviderResolver(loader=...)` with a loader returning a plain `dict[str, str]`
    -> planned fix: keep `loader`/`_map` byte-identical; add a wholly separate optional
    `modality_loader`/`_modality_map` pair instead.
  trap: adding `modality_for` directly to the frozen `ProviderResolver` Protocol -> breaks
    structural typing for the ~14 test files whose fake resolvers implement only `provider_for`
    -> planned fix: a NEW, separate `ChatModalityLookup` Protocol; `CatalogProviderResolver`
    structurally satisfies both without inheriting either nominally.
  trap: an extra live DB query at refresh time (one for provider, one for modality) doubling
    refresh-time I/O -> planned fix: `_load_provider_map()` does the ONE real SELECT (3 columns:
    id/provider/modality) and stashes modality in a closure-scoped cache; `_load_modality_map()` is
    a zero-I/O accessor reading that same cache — genuinely one query per refresh, matching the
    frozen contract's "one extra column on an already-scheduled query" wording.
Strategy actually used: as planned (RED 6 tests -> resolver extension -> new Protocol ->
  CompletionUseCase guard + 2 call sites -> deps.py/realtime_ws.py wiring -> main.py loader
  extension -> full-suite confirm). All 3 pre-identified traps were dodged exactly as planned
  (verified: `provider_chat_dispatch`'s frozen suite still passes unmodified; pyright 0 errors
  across all 6 touched files, confirming the new `ChatModalityLookup` Protocol didn't ripple into
  the ~14 fakes typed against `ProviderResolver`; `main.py`'s refresh is genuinely one SELECT).
  One self-caught refute-read-class correction during BUILD (not a defect — a rationale fix): §1's
  "rejected alternative" framing claimed a live per-request `ModelRow` SELECT would violate
  CompletionUseCase's "avoids a direct DB dependency" design principle; closer reading of
  `_check_input_modalities` shows it ALREADY does a live per-request SELECT via
  `SqlAlchemyInputModalityLookup` for the v55 guard — so a second live query would have been
  legal, just costlier, not forbidden. The CHOSEN cached-map design is still strictly better
  (zero I/O beats one query) and is unaffected; only the internal justification was overstated.
Safety rule (feature-specific): single-bill invariant — the guard MUST fire before credential
  resolution, the upstream call, and usage recording in both `complete()` and `stream()`.
Code lives in: `apps/gateway/src/`
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

- [x] all tests pass — 7/7 new `chat_modality_guard` tests green; full gateway suite 2143
      accounted for (2134 passed + 6 failed + 3 errors in the raw run); every failed/errored test
      re-run standalone: 8 of 9 passed clean (transient shared-Postgres/Redis cross-suite
      contention from concurrent pytest processes during this session's parallel refute-read
      agents — matches this repo's documented flake history); the 9th
      (`test_owner_sees_levels_vs_capacity`, `bandwidth_counter_view`) fails IDENTICALLY with this
      task's changes fully `git stash`-ed out — proven pre-existing and unrelated (a
      machine-timing-marginal test in an unrelated subsystem), not a regression from this task
- [x] coverage did not decrease — new guard lines exercised by 7 real HTTP/unit/WS tests (not
      trusting raw line-coverage %, per this session's established coverage.py/async-greenlet
      tracing-gap caveat — verified BEHAVIORALLY: exact status codes + upstream call counts +
      usage-recorder call counts, matching the preset-capability-validation precedent)
- [x] no test or contract was altered during build — `git diff` shows zero changes to any existing
      test file or to `.add/tasks/chat-modality-guard/TASK.md` §3 CONTRACT after freeze
- [x] the green was EARNED, not gamed — two independent adversarial refute-read subagents (see
      verdict below): correctness/wiring lens EARNED (found + fixed one real concurrency defect);
      security/safety lens SAFE TO SHIP (0 hard-stops)
- [x] concurrency / timing of the risky operation is safe — the refute-read found a genuine race
      (concurrent `refresh()` calls could tear `_map`/`_modality_map` across two different cycles);
      fixed with an `asyncio.Lock` around `refresh()`'s body + a regression test
      (`test_refresh_serializes_concurrent_calls`) empirically proven to fail without the fix
      (3 concurrent loader calls) and pass with it
- [x] no exposed secrets, injection openings, or unexpected dependencies — security-lens refute-read
      confirmed the new error-message shape mirrors the already-shipped images_use_case.py guard
      verbatim (no new injection sink); zero new third-party dependencies
- [x] layering & dependencies follow CONVENTIONS.md — new `ChatModalityLookup` Protocol lives in
      `domain/ports.py` (not infrastructure); `CatalogProviderResolver` (infrastructure) implements
      it structurally; `CompletionUseCase` (application) depends only on the Protocol, never the
      concrete resolver class — mirrors the existing `ProviderResolver`/`InputModalityLookup` shape
- [ ] a person reviewed and approved the change — Tin approved the CONTRACT freeze (§3, via
      AskUserQuestion) and the placement decision (5th task, same branch/PR); human line-by-line
      code review of the diff is still pending, deferred to PR #51 update (same open item already
      recorded for the other 4 v56 tasks)

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] chat to a KNOWN non-chat cached model -> 400 ERR_MODEL_MODALITY_MISMATCH, zero upstream
      calls, zero usage rows — confirmed by `test_chat_to_known_incompatible_model_rejected`
      (FakeCompletionUpstream.calls == 0, SpyRecorder.call_count == 0)
- [x] chat to a compatible/cached-"chat" model is byte-identical -> 200, upstream called once —
      confirmed by `test_chat_to_compatible_model_byte_identical`
- [x] chat to a model with UNKNOWN/uncached modality fails open -> 200, upstream called —
      confirmed by `test_chat_to_unknown_modality_model_is_fail_open` (seeded but resolver never
      refreshed after seeding)
- [x] realtime-WS chat inherits the guard via the SAME `provider_resolver` instance, no new
      app.state attribute — confirmed by `test_realtime_ws_real_chat_wires_chat_modality_lookup`
      asserting identity (`is _resolver_sentinel`) for both `provider_resolver` and
      `chat_modality_lookup` kwargs reaching the constructor
- [x] `CatalogProviderResolver`'s extension is additive/backward-compatible -> the frozen
      `provider_chat_dispatch` suite (loader-only construction) passes UNMODIFIED — confirmed by
      running `tests/provider_chat_dispatch/` green (10 tests, 0 changed)
- [x] refresh() costs exactly one real query per cycle (provider+modality from one SELECT) —
      confirmed by reading `main.py`'s `_load_provider_map`/`_load_modality_map`: the latter does
      zero I/O, only reads the closure-scoped cache the former just populated
- [x] the whole existing chat-test suite is unaffected (fail-open is safe by construction since no
      other suite refreshes the resolver post-seed) — confirmed by running
      `preset_capability_validation`/`preset_resolution_ingress`/`input_modality_guard` together
      (63 passed, 1 transient deadlock re-confirmed passing standalone) + the full gateway suite

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — every new symbol referenced: `modality_loader`/`_modality_map`/`modality_for`
      used by `main.py` + the new tests; `ChatModalityLookup` imported and used as the
      `chat_modality_lookup` param type in `use_cases.py`; `_check_chat_modality` called from both
      `complete()` and `stream()`; `chat_modality_lookup=` threaded at both `deps.py` and
      `realtime_ws.py` construction sites — grepped every new symbol name, zero orphans.
- [x] DEAD-CODE (code) — no new unused symbol; `modality_for`/`_check_chat_modality` are both
      called (by main.py wiring / by complete()+stream() respectively), not merely defined.
- [x] SEMANTIC (prose / non-code) — read in full: `catalog_provider_resolver.py` (both old and
      new versions), `ports.py`'s `ProviderResolver`/new `ChatModalityLookup` Protocols,
      `use_cases.py`'s `_check_input_modalities`/`_resolve_credential`/`complete()`/`stream()` in
      full, `main.py:630-760` wiring block, `deps.py:170-231`, `realtime_ws.py:215-320` — confirmed
      the call-order placement (after governance+input-modality guard, before credential
      resolution/upstream/usage) in both entry points.

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: 2 independent adversarial subagents (correctness/wiring lens + security/safety lens),
  spawned in parallel, each blind to the other's findings.
  Adversarially checked (correctness/wiring lens, 7 probes): call-order correctness in both
  complete()/stream(); wiring completeness across every CompletionUseCase construction site;
  backward compat of the frozen provider_chat_dispatch suite; refresh() cache-population
  ordering; empty-string-modality edge case; test-quality (vacuous-assert check); param
  shadowing between provider_resolver/chat_modality_lookup. Verdict: EARNED, with ONE REFUTED
  finding — a genuine concurrency race: concurrent refresh() calls (e.g. overlapping
  /internal/catalog/sync retries) could interleave main.py's paired provider/modality loader
  calls via the shared, unlocked `_last_modality_cache` closure, leaving `_map`/`_modality_map`
  populated from two DIFFERENT refresh cycles — empirically reproduced by the agent against the
  unguarded code. FIXED (this session, post-refute-read): `asyncio.Lock` around
  `CatalogProviderResolver.refresh()`'s body, serializing concurrent calls; a new regression test
  (`test_refresh_serializes_concurrent_calls`) was written and PROVEN to fail without the fix
  (3 concurrent loader entries) and pass with it (max 1) — verified by temporarily stripping the
  lock from a scratch copy of the file, not by trusting the fix blind.
  Adversarially checked (security/safety lens, 6 probes): single-bill invariant across BOTH
  complete()/stream() full method bodies; cross-tenant leakage risk in the new global,
  tenant-blind modality cache; fail-open abuse surface (bounded by ModelRow.modality's NOT NULL +
  server_default="chat" schema constraint — no permanent cache-miss is attacker-controllable);
  regression risk to the ~2000 other existing gateway tests (confirmed: only the frozen
  provider_chat_dispatch suite ever calls provider_resolver.refresh() in a test, and it never
  wires modality_loader — every other suite's models stay uncached/fail-open, safe by
  construction); error-message injection safety (mirrors the already-shipped images_use_case.py
  guard verbatim). Verdict: SAFE TO SHIP, zero HARD-STOPs. ONE non-blocking finding recorded as
  a SPEC delta below: `_check_chat_modality` checks only the literal resolved model_id/alias, not
  re-validated against FallbackModelRouter's rewritten candidates — a narrow DX gap (upstream
  still rejects cleanly today), not a security/billing hole, consistent with this guard family's
  own accepted severity framing (established at preset-capability-validation).

### GATE RECORD
Outcome: PASS
Reviewed by: Claude (self-review + 2 independent adversarial refute-read subagents, one finding
  fixed pre-gate) · Tin Dang approved the CONTRACT at freeze (fail-open-on-unknown-modality
  default) and the task's placement (5th v56 task, same branch/PR) via AskUserQuestion ·
  date: 2026-07-01 · human line-by-line code review of the final diff is the next step, at the
  PR #51 update (same outstanding item already recorded for the other 4 v56 tasks).

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): rate of `ERR_MODEL_MODALITY_MISMATCH` on
`/v1/chat/completions` + realtime-WS chat (should be ~0 in steady state — a nonzero rate signals
either a misconfigured preset or a genuinely stale cache window); `CatalogProviderResolver.refresh()`
latency/failure rate (the `provider_resolver_refresh_failed`/`provider_resolver_modality_refresh_failed`
warning logs).

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ v1 (approved by Tin Dang (via AskUserQuestion, 2026-07-01): "Freeze v1 as drafted".)
- [AI] build — strategy used: as planned (RED 6 tests -> resolver extension -> new Protocol ->
- [AI] verify — gate PASS (reviewed by Claude (self-review + 2 independent adversarial refute-read subagents, one finding)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
- [SPEC · open] `_check_chat_modality` validates only the literal resolved model_id/alias, not
  candidates `FallbackModelRouter` rewrites `payload["model"]` to on fallover — a fallback group
  mixing chat and non-chat candidates could route a fallen-over request to a non-chat model
  uncaught by this guard (evidence: security-lens refute-read, this session; no existing test
  covers this combination). Low real-world likelihood (requires a misconfigured fallback group);
  upstream still rejects cleanly today per this guard family's established severity framing — a
  DX gap, not a security/billing hole.
- [SPEC · dropped] widespread full-gateway-suite flakiness (32 failed/13 errors) observed on the
  FIRST full-suite run this VERIFY pass, caused by 2 concurrent adversarial refute-read subagents
  each running their own pytest processes against the SAME shared test Postgres/Redis
  concurrently with the main full-suite run — a self-inflicted process-management issue (not a
  code defect), resolved by re-running cleanly single-process (2134 passed/6 failed/3 errors, all
  but one transient/pre-existing on reconfirm). One genuinely pre-existing, unrelated failure
  survived (`bandwidth_counter_view::test_owner_sees_levels_vs_capacity`, a machine-timing-marginal
  test) — confirmed via `git stash` to fail identically with this task's changes fully removed;
  out of scope for this task.

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.
- [ADD · open] running independent adversarial refute-read subagents IN PARALLEL with a
  developer-driven full-suite verification run risks the exact "concurrent pytest processes on a
  shared test DB" hazard this same project already hit and partially hardened against earlier
  this session — evidence: the first full-suite run this VERIFY pass showed 32 failed/13 errors
  purely from this collision, resolved only by re-running clean. Future auto-mode parallel
  verification should either serialize test-running agents against the main-loop's own full-suite
  run, or explicitly scope subagents to read-only static analysis + a SINGLE small targeted test
  subset, never a second independent full/broad pytest invocation against the same shared DB.
