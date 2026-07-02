# TASK: Streaming model-group alias must bill on served candidate, not the alias (B1 revenue leak)

slug: stream-alias-billing · created: 2026-07-02 · stage: production
autonomy: auto
phase: done   <!-- fast lane: ground -> specify -> contract -> tests -> build -> verify -> observe -> done -->
fast: true

> Fast lane — one small task, minimal sections, filled top-to-bottom. The trust floor still
> holds: a FROZEN §3 contract · ≥1 red test before build · a recorded §6 gate (security = HARD-STOP).

---

## 0 · GROUND — the real codebase

Touches (files · symbols):
  - `proxy/application/fallback_router.py:FallbackModelRouter.stream` (420-447) — alias path rewrites
    `payload["model"] = _strategy_order(alias, candidates)[0]` (the *routed primary*), delegates to
    `upstream.stream(rewritten)`, returns `AsyncIterator[bytes]` — **discards the served id.**
  - `…FallbackModelRouter.stream_resilient` (448-490) — opt-in (default OFF); commits whichever
    candidate survives pre-first-byte fallover; returns `(first_chunk, rest)` — **discards served id.**
  - `proxy/application/streaming_resilience.py:open_resilient_stream` (24-63) — returns `(first, rest)`
    on the committed attempt; commit point is where the served id is knowable for the resilient path.
  - `proxy/application/use_cases.py:CompletionUseCase.stream` — `_stream_model_id = model_id` (the ALIAS)
    @1559; billing `_fire_record_with_raw(…, model=model_id, …)` @2026-2036 (clean close) and @1914-1926
    (disconnect); span @2097-2108 uses `_stream_model_id`. All bill the alias.
  - `proxy/application/routing_strategy.py` — OrderedStrategy (deterministic) · **SimpleShuffleStrategy
    (weighted-RANDOM `_rng.choices`)** · LeastBusyStrategy / LatencyStrategy (LIVE-STATE dependent).
    ⇒ the served candidate is **NOT recomputable by the caller** for 3 of 4 strategies.
  - `…FallbackModelRouter.candidates_for` (187-193) — returns the **RAW** group order, NOT the strategy
    order; billing `candidates_for(alias)[0]` is wrong under any non-Ordered strategy.

Honors (patterns / conventions):
  - **F7 frozen invariant** (`tests/model_fallbacks`): complete() bills the served candidate, NOT the
    alias (`use_cases.py:1453-1463`). This task EXTENDS that invariant to the streaming path.
  - **F11 frozen** (`tests/model_fallbacks`): `stream()` returns a generator, routes to the first
    candidate, no stream fallback → MUST stay green (out-channel is an OPTIONAL kwarg — no signature break).
  - Pricing snapshots are keyed on the **catalog candidate id**, never on an alias or the provider's
    echoed response `model` string → billing must use the routed candidate id, not `body["model"]`.

Anchors the contract cites: `FallbackModelRouter.stream`, `FallbackModelRouter.stream_resilient`,
`open_resilient_stream`, `CompletionUseCase.stream` billing sites (@2026, @1914, @2101).

---

## 1 · SPECIFY — the rules

Feature: Streaming completions bill on the **served candidate id** (the catalog model actually routed
to), not the model-group alias — for every routing strategy and both stream paths.

Must:
  - When a streaming request targets a model-group alias, the usage record `model` (BOTH clean-close and
    disconnect paths) MUST equal the candidate the router actually routed/committed to — the exact value
    used to rewrite `payload["model"]`.
  - Correct for ALL strategies incl. non-deterministic (simple-shuffle) and state-dependent
    (least-busy / latency): the served id is **captured from the routing decision, never recomputed.**
  - Plain (non-alias) model id: billing `model` unchanged (= the request model string) — matches F3.
  - `stream()` return type and F11 behavior unchanged (served id exposed via an OPTIONAL out-channel).
Reject:
  - (no new error path — this is a billing-attribution correction)
Accept: Given alias "gpt4-ha"→[A,B] and a strategy that routes to B, When a client streams
  model="gpt4-ha" to a clean close with a usage frame, Then the usage record `model` == B (served
  candidate) — not "gpt4-ha" and not necessarily A.
Assumptions: ⚠ biggest risk: the OUT-CHANNEL shape (optional `on_served` callback) is the contract point
  most likely to want revision — alternatives are a return-value change (breaks F11) or a mutable holder.
  If wrong: re-freeze + rewire use_cases capture. No data-model change either way.

---

## 3 · CONTRACT — freeze the shape

```
# fallback_router.py
FallbackModelRouter.stream(
    payload, upstream=None, *, on_served: Callable[[str], None] | None = None
) -> AsyncIterator[bytes]
    # alias  -> on_served(primary)      where primary = _strategy_order(alias, candidates)[0]  (the
    #                                    SAME value written to payload["model"]) — fired before return
    # plain  -> on_served(model_id)
    # on_served=None -> behaviour byte-identical to today (F11 green)

FallbackModelRouter.stream_resilient(
    payload, upstream=None, *, on_served: Callable[[str], None] | None = None
) -> tuple[bytes | None, AsyncIterator[bytes]]
    # on_served(committed_candidate) fired at pre-first-byte COMMIT (the attempt whose first chunk
    #   was obtained) — threaded via open_resilient_stream's on_committed.

# streaming_resilience.py
open_resilient_stream(
    attempts, open_stream, on_fallover=None, on_committed: Callable[[str], None] | None = None
)   # fires on_committed(model_id) for the attempt that commits (before yielding first_chunk)

# use_cases.py CompletionUseCase.stream
#   served_holder captured via on_served/on_committed; used for model= in BOTH _fire_record_with_raw
#   sites (@2026 clean-close, @1914 disconnect) AND _stream_model_id (span). Default (no alias / router
#   absent) = the request model_id (unchanged).
```

`Least-sure flag surfaced at freeze:` [contract] the out-channel = optional `on_served` callback.
  Chosen over a return-value change (would break F11's `stream()` signature) and over a mutable holder
  (less explicit). It captures the resilient COMMIT-time value correctly. If wrong: re-freeze.
Status: FROZEN @ v1 — approved by Tin Dang
<!-- Freeze needs human approval: this changes the router's streaming API AND touches billing. -->

---

## 4 · TESTS — failing-first (red)

Plan (unit, fakes — mirror `tests/stream_usage_completeness/conftest.py`; NO real DB/Redis so a single
scoped `uv run pytest tests/stream_alias_billing/ -q --no-cov -p no:cacheprovider` run is safe):
  - `test_stream_alias_bills_served_candidate_not_alias` — alias→[A,B], strategy stub routes to B; drive
    CompletionUseCase.stream to a clean close with a usage frame; assert recorded `usage.model == "B"`.
    RED today (records the alias).
  - `test_stream_served_matches_routed_under_reordering` — strategy stub returns [B, A]; assert billed
    model == `upstream.stream_calls[0]["model"]` (the model actually called). Guards the recompute-
    mismatch bug (candidates_for[0] would give A).
  - `test_stream_resilient_bills_committed_candidate_after_fallover` — A fails pre-first-byte, B commits;
    assert billed == "B" (not A, not alias).
  - Regression: F11 in `tests/model_fallbacks` stays green (stream() with no on_served unchanged).
Tests live in: `./tests/` · MUST run red before Build.

---

## 5 · BUILD — AI writes code

Scope (may touch): `src/gateway/proxy/application/fallback_router.py`,
  `src/gateway/proxy/application/streaming_resilience.py`,
  `src/gateway/proxy/application/use_cases.py` · tests under `apps/gateway/tests/stream_alias_billing/`.
Strategy & known-problem fixes:
  1. Add optional `on_committed` to `open_resilient_stream`; fire at commit (before first yield).
  2. Add optional `on_served` to `stream()` (fire with primary) + `stream_resilient()` (via on_committed).
  3. In `CompletionUseCase.stream`, capture served id into a local via the callback; use it for `model=`
     at both `_fire_record_with_raw` sites and for `_stream_model_id`. KNOWN TRAP: do NOT recompute the
     order caller-side (non-deterministic under simple-shuffle) — use only the captured value.
Strategy actually used: as planned (steps 1-3) PLUS two fixes surfaced during build/regression:
  (a) the OpenRouter inline cost-recovery call (`use_cases.py` disconnect handler, `recover(model=...)`)
  also keyed on the alias — repointed to the captured served id so the recovered cost row re-prices on
  the same catalog candidate as the disconnect billing row (test `openrouter_cost_recovery_wiring`
  asserts recovery.model == disconnect_record.model — the invariant is preserved, both now = served).
  Also repointed the SUCCESS stream span (`_emit_span_fire_forget`) to the served id for consistency.
  (b) CONTRACT ENUMERATION GAP (adversarial review): §3 listed TWO charged `_fire_record_with_raw`
  sites (@clean-close, @disconnect). Build/review found a THIRD charged (status=200, usage-bearing)
  site — the bandwidth-pacing mid-stream SHED (truncation billing of the streamed prefix, ~L1830) —
  ALSO keyed on the alias → the identical $0-on-alias leak on a rarer trigger (bandwidth cap hit
  mid-stream on an alias). The existing `tests/stream_bandwidth_pacing` shed test asserts only
  `call_count`, so it was invisible. Repointed to `_stream_model_id` (served; post-commit here).
  This EXTENDS the frozen contract's enumeration per §1's governing rule ("bill on the served
  candidate id … for every routing strategy and both stream paths") — a spec-intent completion, NOT
  a contract weakening; the API shape is unchanged (no new param, no signature change). Surfaced for
  ratification at the gate (see §6). Added a 4th red→green test (`test_bandwidth_shed_bills_served_
  candidate_not_alias`) to guard it.
  DELIBERATELY NOT CHANGED: the mid-stream 502 error record (`_fire_record`, ~L1857) stays on the
  alias — it is usage=None (NOT charged → $0 either way, no revenue leak) AND the default-path
  pre-byte "poisoned generator" reconstruction also lands there while being semantically pre-commit,
  so "post-commit → served" is not cleanly applicable. Out of B1 scope; noted for a future
  observability-attribution pass. Keeps this diff 1:1 with the revenue leak for line-by-line review.
  Served id captured via `on_served`/`on_committed` into `_served_holder`; `_stream_model_id` set to it
  after routing/commit. Plain (non-alias) + no-router paths unchanged (served == request model_id).
Constraints: change no test, no contract SHAPE; allow-list packages only. (The §3 enumeration was
  EXTENDED to a same-class charged site per §1 intent, not weakened — flagged for gate ratification.)

---

## 6 · VERIFY — evidence + gate

- [x] all §4 tests pass (4/4) · no test or contract SHAPE altered during build (contract FROZEN @ v1;
      §3 enumeration EXTENDED to a 3rd same-class charged site per §1 intent — see gate disclosure below)
- [x] green was EARNED — served id is the CAPTURED routed value, not a recompute (proven by
      test_default_stream_bills_routed_primary_under_reordering: strategy routes to B, billed == B,
      not candidates_for[0]==A); F11 + streaming_resilience green — full logic regression 72 passed
      (incl. openrouter_cost_recovery_wiring equality invariant + stream_bandwidth_pacing shed regression).
- [x] ruff clean · pyright 0 errors project-scope (include=src/gateway; tests are outside CI pyright scope)
- [x] no exposed secrets, injection openings, or unexpected dependencies (security = HARD-STOP) — the
      change adds an optional callback param + billing-attribution only; no new deps, no IO, no secrets.
- [x] ADVERSARIAL REVIEW (advisor, Opus): enumerated ALL stream() record sites — charged
      (1830-shed/1933-disconnect/2048-clean-close → served) · pre-commit errors (1598/1616/1738/1754 →
      alias, correct) · cost-recovery+span (→ served) · tpm (no model field). Nothing left to hunt.
      Observability suite: 15 span tests pass (incl. streaming+model_group); the 1 error is the
      environmental `DROP TABLE tenants` FK-cascade (v56 `tenant_model_presets`), not the span change.

Evidence:
  - `uv run pytest tests/stream_alias_billing/` → 4 passed (each was red first: "billed on 'fast'").
  - `uv run pytest tests/stream_bandwidth_pacing/ tests/streaming_resilience/ tests/model_fallbacks/
    tests/stream_usage_completeness/ tests/stream_disconnect_billing/ tests/openrouter_cost_recovery_wiring/`
    → 72 passed (F11 + shed + cost-recovery-equality all green).
  - `uv run pytest tests/observability/` → 15 passed (span change safe; 1 environmental DB error).
  - ruff: All checks passed · pyright (project scope): 0 errors, 0 warnings.
  - NOTE: DB-backed suites (su6, cost-recovery app-build, usage/, observability tenant-id) ERROR on
    asyncpg teardown (`DROP TABLE tenants` FK cascade / setup) — CONFIRMED environmental (untouched
    tests/usage/ errors identically); NOT a B1 regression. Full `make ci` re-run once test Postgres
    healthy is the residual check.

Build expectations MET: a streaming alias request now records usage keyed on the served catalog
candidate at ALL THREE charged sites (clean-close · disconnect · bandwidth-shed) → a real pricing
snapshot resolves → non-$0 bill (was silently $0 on the alias).

### GATE RECORD
Outcome: PASS — Tin authorized "ratify + gate + commit" (2026-07-02). The §3 contract-enumeration
  extension (3rd charged site = bandwidth shed) is RATIFIED as a spec-intent completion (API shape
  unchanged). Build red→green, full regression green, security = n/a (billing-attribution only, no new
  IO/deps/secrets). Committed on branch fix/stream-alias-billing; push/PR pending a separate go-ahead.

⚠ RATIFIED AT GATE (contract enumeration gap — surfaced, not hidden): §3 CONTRACT (FROZEN @ v1) listed
  TWO charged `_fire_record_with_raw` sites to repoint (clean-close @2048, disconnect @1933).
  Adversarial review found a THIRD charged (status=200) site — the bandwidth mid-stream SHED (~L1830) —
  with the SAME $0-on-alias leak. It was extended to `_stream_model_id` per §1's governing rule ("bill
  on the served candidate … for every routing strategy and both stream paths"). This is a spec-intent
  COMPLETION, not a contract weakening: the API shape is unchanged (no new/changed param or signature),
  and a new red→green test guards it. Same precedent as the cost-recovery consistency fix already in §5.
  → Tin: please ratify this extension (or direct otherwise) as part of the gate.
  DEFERRED (not done, by design): mid-stream 502 error record (~L1857) stays on the alias — usage=None
  (no revenue), and the poisoned-generator seam makes "post-commit→served" ambiguous there. Noted for a
  future observability-attribution pass.

Reviewed by: Tin Dang · date: 2026-07-02
Residual: re-run `make ci` (full suite incl. DB) once the shared test Postgres is healthy.
