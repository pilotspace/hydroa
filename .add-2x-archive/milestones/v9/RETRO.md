════════════════════════════════════════════════════════════════════════
 v9 · LiteLLM parity slice 7 — provider breadth (Anthropic + Google Gemini, chat + embeddings)
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     4/4 done           CRITERIA  5/5 met
 GATES     4 PASS             WAIVERS   none

 goal  a tenant calls Anthropic (chat) and Google Gemini (chat +
       embeddings) models through the same OpenAI-compatible /v1
       surface, with native-API translation, billing, governance, and v8
       routing intact

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 provider-chat-dispatch      done      PASS 0     ●●●●●●●●
 anthropic-provider          done      PASS 9†    ●●●●●●●●
 gemini-provider             done      PASS 7†    ●●●●●●●●
 provider-breadth-live-veri… done      PASS 0     ●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 5/5 met

 LEARNINGS (16 carried)
   • DDD · open · Provider became a first-class routing dimension on
     chat via a thin dispatch port (ProviderResolver) + wrapper, leaving
     the v8 router/use-case/billing untouched — additive supersession
     over a frozen seam works at the composition root (evidence: 0 edits
     to fallback_router.py/use_cases.py; openrouter path byte-identical,
     593 green).
   • TDD · open · Front-loaded red suite covered the dispatch seam in
     isolation with fakes (resolver + adapter spies) — no DB/network —
     so BUILD was a pure green-make; the gap it MISSED was the 7
     prior-milestone white-box wiring asserts on
     app.state.completion_upstream (evidence: target 10/10 green but
     full gate surfaced 7 wiring failures). Lesson: a contract that
     RELOCATES a named app.state seam must enumerate the prior
     wiring-regression tests that assert that seam.
   • ADD · open · The CONTEXT.md "do-not-touch frozen tests" list should
     be derived by grepping the changed app.state attribute, not
     hand-enumerated — the 3 wiring suites (provider_seam,
     retry_policy_wiring, upstream_base_url) were the foreseeable blast
     radius of moving completion_upstream and were not pre-listed
     (evidence: this verify caught them, not the freeze).
   • SDD · open · A public `app.state.openrouter_completion_upstream`
     seam is now the canonical anchor for the v6
     production-wiring-regression rule (Settings→live-adapter threading)
     — future seam relocations should expose a stable public name rather
     than let tests reach private internals.
   • SDD · open · First real schema-TRANSLATION surface
     (OpenAI⇄Anthropic) landed as a self-contained adapter with
     module-level pure helpers — non-stream + stream + usage + errors —
     proving the v9 dispatch seam carries a non-OpenAI wire format
     end-to-end (evidence: 16/16 green incl. a realistic recorded-stream
     drift detector; extract_usage_from_sse cross-check passes).
     Reusable template for every future provider.
   • DDD · open · Decision recorded: raw per-provider httpx translation
     OVER vendor SDKs — matches the LiteLLM parity target (its
     llms/anthropic is hand-rolled httpx) and keeps ONE resilience
     contract
     (CircuitBreaker/timeout/UpstreamUnavailableError/v8-fallback)
     across all upstreams; avoids per-provider SDK dependency sprawl
     (anthropic+google-genai+boto3+azure…). Applies to Gemini + all
     later providers (evidence: human steer 2026-06-13).
   • TDD · open · FOLLOW-UP (non-blocking): stream() buffers the FULL
     Anthropic event sequence before emitting any OpenAI chunk → output
     + billing correct, but time-to-first-byte == full generation time
     (not incremental). The frozen `_translate_anthropic_sse(events)`
     helper (sync, consumes the whole iterable) shaped this; true
     incremental streaming needs a stateful per-event translator
     (process-one-event + finalize) without changing that frozen
     signature. Track for a streaming- latency hardening slice (applies
     to Gemini too). Evidence: anthropic_upstream.py stream() buffers
     into `events` then translates after the `async with` closes.
   • ADD · open · The live-verify (task 4) is the designated catch for
     the freeze's least-sure flag (SSE field names validated against
     documented fixtures, not a live key) — the CI hardening test
     reduces but does not eliminate that risk; the milestone must not
     close until task-4's recorded- stream replay passes the TLS-edge
     double-pass.
   • SDD · open · Gemini exercised BOTH provider seams at once — chat
     via the v9 CompletionUpstream dispatch AND embeddings via the v7
     UpstreamProvider registry — proving a single provider can span both
     without touching either frozen seam (evidence: 19/19 green; both
     wiring tests assert presence/absence on each seam). Confirms the
     v7+v9 seam split composes cleanly.
   • DDD · open · Provider value-set widened to
     {openrouter,openai,anthropic,google} across chat + embeddings with
     NO datastore/migration change (catalog ModelRow.provider already
     TEXT) — the "provider as first-class routing dimension" glossary
     delta is fully realized for v9's scope.
   • TDD · open · FOLLOW-UP (non-blocking, shared with anthropic):
     Gemini stream() also buffers the full SSE before emitting → correct
     billing, not incremental TTFB. One streaming-latency hardening
     slice should cover BOTH anthropic + gemini (same
     buffer-then-translate shape).
   • UDD · open · FOLLOW-UP: google-embedding usage is a chars/4
     ESTIMATE (Gemini embed returns no token count) → embedding SPEND is
     approximate for one provider. If exact embedding billing matters to
     a tenant, this needs a real tokenizer or a Gemini countTokens
     pre-call (a cost/accuracy slice). Evidence: _gemini_embed_to_openai
     estimates usage; flagged as the freeze's ⚠ least-sure point.
   • ADD · open · The v9 milestone closed via the foundation's
     live-double-pass rule with ZERO harness iteration — the
     seed-then-restart-gateway resolver-refresh mechanism worked
     first-try (the freeze ⚠ flag's mitigation held). Evidence: 35/35
     ×2, first run clean. Confirms the freeze-first per-provider
     methodology (dispatch seam frozen first, each provider's
     translation fixture-grounded + verified by its own unit suite, then
     ONE live double-pass) is the repeatable shape for adding a
     provider.
   • SDD · open · A single path-routed host stub proved sufficient to
     exercise three distinct provider wire formats
     (OpenAI/Anthropic/Gemini incl. SSE + embeddings) through the real
     TLS edge — the per-provider e2e harness does not need one stub per
     provider. Reusable for the next provider slice.
   • DDD · open · Provider breadth is now end-to-end real: catalog
     provider ∈ {openrouter,openai, anthropic,google} routes chat +
     (google) embeddings through the OpenAI-compatible surface with
     billing on the served id and governance intact — the v9 glossary
     delta ("provider as a first-class routing dimension on every
     modality") is fully realized and live-verified.
   • TDD · open · Carry-forward follow-ups (from tasks 2–3, still open):
     incremental SSE streaming for anthropic+gemini (both buffer today);
     exact Gemini-embedding token counting (chars/4 estimate). The live
     run did not surface new defects — these remain deliberate,
     documented scope cuts, not bugs.

 DECIDE NEXT  consolidate learnings + archive-milestone v9
════════════════════════════════════════════════════════════════════════