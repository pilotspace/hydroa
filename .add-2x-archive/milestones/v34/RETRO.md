════════════════════════════════════════════════════════════════════════
 v34 · Helios agent-coding integration readiness
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     7/7 done           CRITERIA  7/7 met
 GATES     7 PASS             WAIVERS   none

 goal  A real AI coding agent (Helios) can drive sustained coding
       sessions through the proxy — streaming tool-calls, prompt
       caching, and reasoning all working with accurate cost tracking
       and graceful behavior under load — proven by a CI-gated stub
       suite and a live double-pass.

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 agent-coding-stub-harness   done      PASS 23†   ●●●●●●●●●
 reasoning-passthrough       done      PASS 8†    ●●●●●●●●●
 prompt-cache-passthrough    done      PASS 10†   ●●●●●●●●●
 parallel-tool-streaming-ve… done      PASS 0     ●●●●●●●●●
 disconnect-billing-all-pro… done      PASS 0     ●●●●●●●●●
 concurrency-load-guard      done      PASS 0     ●●●●●●●●●
 helios-live-smoke           done      PASS 18†   ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 7/7 met

 LEARNINGS (16 carried)
   • TDD · open · discovering the real test-injection seam (pure helpers
     vs post-translation stub vs real-adapter+MockTransport) BEFORE
     specifying prevented a wrong contract — the §3 freeze flag
     (two-seam sufficiency) was resolved by adding SEAM C, not
     discovered mid-build (evidence: SEAM C added at freeze on Tin's
     call).
   • ADD · open · a frozen `native: dict | list[bytes]` type was kept
     intact by SPLITTING coverage (SEAM A non-stream dict, SEAM C stream
     bytes) instead of widening the contract — a scenario/test
     refinement, not a change-request (evidence: §2/§4 edits pre-build,
     §3 untouched). See the `add` skill's `deltas.md`. <!-- e.g. - [DDD
     · open] the model missed multi-tenancy (evidence: scenario_x
     failed) -->
   • SDD · open · delegating D1 to web research (Tin) beat my
     fixed-number guess — the OpenRouter ratio formula scales with
     max_tokens and is the industry convention; surfacing "investigate
     latest docs" as a freeze option is worth repeating for
     provider-API-shaped decisions (evidence: ratio formula replaced
     low=1024/med=8000/high=16000).
   • TDD · open · asserting the ratio FORMULA in tests (compute expected
     both sides) not a hardcoded number means the test survives a tuning
     of the constants without becoming a change-detector (evidence:
     _expected_anthropic_budget mirrors the impl). See the `add` skill's
     `deltas.md`. <!-- e.g. - [DDD · open] the model missed
     multi-tenancy (evidence: scenario_x failed) -->
   • TDD · open · adversarial refute-read found 3 test-assertion gaps on
     an otherwise-correct build (PC12 baseline-equality, PC5 stream
     cache_creation, missing Gemini-stream) — closed by STRENGTHENING
     not weakening; refute-read pays off even at 0.91 (evidence: gaps
     were real coverage holes for contract-cited behavior)
   • ADD · open · expanding a frozen-DRAFT bundle at the freeze decision
     (Tin chose to add the cache-write tier) cleanly widened scope from
     translator-only → +migration/recorder/flusher/2 ORMs without
     re-running earlier phases — the freeze IS the right place to absorb
     a scope decision (evidence: one approval, one coherent bundle)
   • TDD · open · adversarial refute-read surfaced DEAD code
     (_saw_tool_call set-but-unread) that tests alone didn't catch —
     wiring it as a fail-safe + a no-stopReason test turned a latent
     risk into covered robustness (evidence: refute-read item 6)
   • ADD · open · a verify-phase robustness improvement that is a STRICT
     SUPERSET of a frozen contract clause ("finish() unchanged") is best
     recorded as a documented v1.1 amendment with a test, not a silent
     edit — keeps the frozen artifact honest while honoring the
     design-for-failure mandate (evidence: this task's §3 finish()
     amendment)
   • TDD · open · a refute-read caught a billing-critical COVERAGE gap
     (no end-to-end test for recoverable→estimate=False, the
     anti-double-count predicate) that all green tests missed — billing
     invariants need an explicit end-to-end test, not just inspection
     (evidence: refute-read D2)
   • ADD · open · when a new milestone's contract intentionally changes
     a PRIOR milestone's behavior, the prior milestone's test must be
     updated as part of THIS task and the change called out as
     contract-mandated (not a silent weakening) — the refute-read must
     verify the edit is legitimate (evidence: the v33
     test_gen_id_disconnect_is_not_stamped flip)
   • ADD · open · ContextVar across the adapter async-generator boundary
     is a viable side-channel (mirrors the credential contextvar) —
     avoids Protocol/signature churn; verify propagation with a SEAM-C
     test before relying on it (evidence: the least-sure flag resolved
     green)
   • TDD · open · a slot-hold canary that samples in_flight DURING a
     slow stream is the decisive test for an ASGI back-pressure
     middleware — it proves the slot bounds CONCURRENT streams (not
     request-starts) and validates the await-app-spans-stream assumption
     (evidence: the least-sure flag resolved by
     test_slot_held_for_whole_stream)
   • ADD · open · `asyncio.wait_for(sem.acquire(), timeout=0)` is NOT a
     reliable non-blocking acquire (fires TimeoutError even with free
     slots in 3.12); use `if not sem.locked(): await sem.acquire()`
     which acquires synchronously in one event-loop turn (no interleave)
     (evidence: build finding, refute-read CPython-level confirmation)
   • TDD · open · live LLM criteria are non-deterministic: a reasoning
     model needs bounded max_tokens or its stream outlasts the read
     window (C2 [DONE] truncation), and opportunistic upstream caching
     needs a warmup+retry (cache-write must register before a read hits)
     — bound + retry, never assume (evidence: C2/C4 flaked until fixed).
     A retry that breaks on first hit must still honor min-call-count
     invariants (C4a/C4c needed ≥2 calls; a warm-cache pass-2 hit on
     attempt 1 broke that until guarded).
   • ADD · open · a hard EXTERNAL wall (provider credits) is not a
     HARD-STOP of the work — surface it, offer concrete unblock options
     (AskUserQuestion), and re-frame the contract as a Tin-approved
     change request (v1 Gemini → v2 OpenRouter) rather than silently
     editing the frozen shape (evidence: this session).
   • ADD · open · redirecting the provider-under-test is a contract
     amendment, not a constant swap: passthrough (OpenRouter) vs native
     (Gemini) genuinely changes C4 (cache mechanism) and C5 (recoverable
     vs estimate) — re-frame provider-accurately, never weaken
     (evidence: v2 amendment).

 SPEC DELTAS    62 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v34
════════════════════════════════════════════════════════════════════════