# MILESTONE: Billing accuracy + ops hardening — pay down v7/v9/v11 follow-up debt

goal: the proxy bills exactly for every modality (no estimates), fails fast and clearly on misconfiguration, surfaces soft-budget alerts uniformly across chat and non-chat, and runs a deterministic test suite
rationale: intake → `sub-milestone` — a hardening slice that pays down four CONFIRMED open follow-ups carried in the foundation (v7/v9/v11 folds), too big for one task but smaller than a parity slice. The items are independent (no single shared seam), so the decomposition is breadth-first with NO freeze-first dependency. Each closes a named gap the foundation already records: (1) Gemini embeddings bill an ESTIMATE not exact tokens (v9 open); (2) a configured-yet-empty upstream key yields an opaque client-side 500 instead of a clear boot error (v7 open, DOUBLY evidenced v7+v8 live); (3) the chat soft-budget ADVISORY alert is dropped on the non-chat path (v7 open, 3 inherited deltas); (4) the full `-m 'not e2e'` suite is NON-DETERMINISTIC against the shared dev Postgres (FK-violation flake, recurring since v8). Value: correct billing + fail-fast ops + uniform governance + a trustworthy CI gate.
stage: production · status: active · created: 2026-06-13

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  Four independent hardening fixes + a live close:
     - **Exact Gemini-embed token billing**: replace the `max(1, ceil(total_chars/4))`
       ESTIMATE in `gemini_upstream._gemini_embed_to_openai` with an EXACT count, so
       embeddings bill on real tokens like every other modality. The mechanism (Gemini
       `:countTokens` round-trip vs a local tokenizer) is a tradeoff the task contract
       settles; billing still keys on the SERVED model id.
     - **Empty-key boot guard**: a startup guard that rejects a configured-yet-EMPTY
       upstream API key (e.g. `GATEWAY_*_API_KEY=""`) with a clear boot error, instead of
       the opaque downstream 500 ("Illegal header value b'Bearer '") seen live in v7+v8.
       A configured key is either absent (provider disabled) or non-empty.
     - **Non-chat soft-budget alert**: extend `NonChatGovernance` to fire the SAME
       advisory soft-budget alert the chat path fires (reusing `persist_soft_budget_alert`
       + the `alert_events` table), so embeddings/images/audio surface a soft-budget
       crossing identically to chat. HARD 402 enforcement is already present and unchanged.
     - **Deterministic test DB isolation**: make the full `-m 'not e2e'` suite
       deterministic — per-test DB isolation (transaction-rollback fixture / template DB)
       so DB-touching suites stop cross-polluting, killing the FK-violation flake — and/or
       a documented `make test-fast` for per-change gating. The blast-radius no-DB run stays
       the fast gate; this makes the FULL suite trustworthy too.
     - **Live close**: a double-pass e2e proving exact Gemini-embed billing, the non-chat
       soft-budget alert firing, and the empty-key boot guard rejecting at startup.
Out: NEW billing dimensions or pricing units (per-token/image/second/character unchanged);
     soft-budget alert DELIVERY/notification channels (health-alerting owns delivery — this
     only ensures the EVENT is written for non-chat); changing the chat soft-budget path
     (already shipped — this mirrors it); the exact-token mechanism becoming a per-provider
     framework (Gemini-embed only this milestone); the v13 UI/UX work (separate milestone);
     remaining carried opens not listed above (incremental-SSE TTFB, parallel-tool-call
     streaming, same-name Gemini disambiguation, strict-mode response_format).

## Shared decisions & glossary deltas   (living — every task must honor these)
- GLOSSARY: **exact token billing** — every modality bills on a real token/quantity count
  from the provider or a deterministic local count; an ESTIMATE is a documented fallback of
  LAST resort, never the default. Gemini embeddings graduate from estimate → exact.
- GLOSSARY: **boot guard** — a startup-time precondition check (in the gateway lifespan /
  settings validation) that converts a misconfiguration into a clear, fail-fast boot error
  BEFORE the first request, never an opaque per-request 500.
- The non-chat soft-budget alert MUST reuse the chat path's `persist_soft_budget_alert` +
  `alert_events` (same `dedupe_key` format `soft_budget:{key_id}:{YYYYMM}`, same fire-and-
  forget asyncio pattern) — one alert seam, not a parallel re-implementation.
- CAPABILITY SEAM RULE (foundation, user-mandated): any additive capability on a frozen port
  uses the **typed-extras seam** (`TypedDict(total=False)` + implementation-declared
  `supported_extras: frozenset`), NEVER `inspect.signature`/`hasattr` runtime reflection —
  the chat soft-budget path's legacy `hasattr` seam is NOT the pattern to copy.
- Additive / behavior-preserving: each fix is additive; the hard-402 budget path, the chat
  soft-budget path, and billing on the served id stay byte-identical; no frozen contract or
  test is weakened.

## Shared / risky contracts (freeze these first)
- No single cross-task shared seam (the four fixes are independent). The riskiest contract is
  per-task and frozen in its own TASK.md §3: the **exact-token mechanism** for Gemini
  embeddings (the `:countTokens` round-trip vs local-tokenizer tradeoff, incl. the latency /
  dependency cost and the estimate-fallback rule) → owning task `gemini-embed-tokens`.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] gemini-embed-tokens        depends-on: none                                            — replace the chars/4 estimate in `_gemini_embed_to_openai` with an EXACT Gemini-embed token count (mechanism settled at freeze); bill on real tokens; estimate becomes a documented last-resort fallback; non-Gemini billing untouched.
- [ ] empty-key-boot-guard       depends-on: none                                            — add a startup guard rejecting a configured-yet-EMPTY upstream API key with a clear boot error (closes the opaque "Bearer ''" 500 seen live v7+v8); absent key = provider disabled (allowed), empty key = boot failure.
- [ ] nonchat-soft-budget-alert  depends-on: none                                            — extend `NonChatGovernance` to fire the soft-budget alert via the shared `persist_soft_budget_alert`/`alert_events` seam (typed-extras, not hasattr); embeddings/images/audio surface a crossing identically to chat; HARD 402 unchanged.
- [ ] test-db-isolation          depends-on: none                                            — make the full `-m 'not e2e'` suite deterministic via per-test DB isolation (txn-rollback fixture / template DB), killing the FK-violation flake; document `make test-fast` for per-change gating; no production source change.
- [ ] v12-live-verify            depends-on: gemini-embed-tokens, empty-key-boot-guard, nonchat-soft-budget-alert — live double-pass through the TLS edge: exact Gemini-embed tokens billed (not estimated), non-chat soft-budget alert event written, empty-key boot guard rejects at startup; two consecutive clean passes.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] A Gemini embeddings request bills on an EXACT token count (usage row matches a real count, not `ceil(chars/4)`); non-Gemini billing is unchanged (← gemini-embed-tokens)
- [ ] Booting the gateway with a configured-yet-empty upstream API key fails fast with a clear boot error; an absent key leaves that provider cleanly disabled (← empty-key-boot-guard)
- [ ] An embeddings/images/audio request that crosses a key's soft budget writes a `soft_budget_exceeded` alert_event (same dedupe semantics as chat); HARD 402 still enforced (← nonchat-soft-budget-alert)
- [ ] The full `-m 'not e2e'` suite runs deterministically (no FK-violation flake) across repeated runs; `make test-fast` is documented (← test-db-isolation)
- [ ] All behavioral items above proven LIVE through the TLS edge, two consecutive clean passes (← v12-live-verify)
