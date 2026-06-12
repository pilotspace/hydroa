════════════════════════════════════════════════════════════════════════
 v8 · LiteLLM parity slice 6 — router & load-balancing
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     5/5 done           CRITERIA  6/6 met
 GATES     5 PASS             WAIVERS   none

 goal  a model alias with multiple deployments distributes requests
       across them by a configured routing strategy, honoring
       per-deployment rate limits, with v6 fallback+cooldown and billing
       intact

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 deployment-model            done      PASS 0     ●●●●●●●●
 routing-strategy            done      PASS 0     ●●●●●●●●
 balance-strategies          done      PASS 0     ●●●●●●●●
 deployment-limits           done      PASS 0     ●●●●●●●●
 v8-live-verify              done      PASS 0     ●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 EXIT CRITERIA  ●●●●●●●●●● 6/6 met

 LEARNINGS (17 carried)
   • SDD · open · extending a frozen config value (model_groups:
     list[str] → list[Deployment]) is safest as an ADDITIVE second view
     (settings.deployments) plus a preserved string-view property, NOT a
     type change to the bound field — the field's exact-shape consumers
     (here /admin/routing RA1/RA8, frozen) keep reading the old view
     unchanged (evidence: 63/63 v6 routing/fallback regression green;
     routing_admin_router.py untouched).
   • TDD · open · CI has a cluster of timing/environmental flaky tests
     (health_alerting s07–s11 fixed-50ms async-write race;
     semantic_cache; response_caching; a guardrails case) that produce a
     ROTATING red set independent of the change under test — a green
     gate needs a flaky-isolation pass (full-suite-minus-flaky
     deterministic green) plus a stash-reproduction to attribute reds
     (evidence: two make-ci runs, disjoint 5-red sets; s11 fails ~4/5 on
     clean main). Candidate fix: poll-until-row instead of a fixed
     sleep.
   • ADD · open · A pure-sync seam with no `await` is the cleanest
     concurrency story under an asyncio event loop (atomic within one
     step) — but it pins the seam sync. When a known async successor
     exists, freeze the SUPERSESSION NOTE in the contract up front (done
     here) so the re-pin is a planned follow-up, not a surprise
     re-freeze. (evidence: §3 SUPERSESSION NOTE + the sync-vs-async
     least-sure flag surfaced at freeze.)
   • TDD · open · Weighted-random behavior is assertable
     deterministically via an injected `random.Random(seed)` + a
     1000-draw distribution band (0.80<b_share<0.98), not by mocking —
     keeps the test honest about the real algorithm. (evidence: test_rs3
     green, stable across runs.)
   • SDD · open · A default strategy that returns its input unchanged
     (`OrderedStrategy → list(candidates)`) is the
     byte-identical-preservation lever: the entire v6 fallback loop is
     reused verbatim and the frozen suites stay green with zero
     loop-body edits. (evidence: model_fallbacks + routing_admin + proxy
     suites green under the new seam.)
   • ADD · open · The "frozen behavioral pin → supersession" pattern
     works ADDITIVELY: a frozen sync seam (order()) is superseded by
     adding an OPTIONAL async capability (aorder) selected via
     isinstance at the call site — the frozen tests keep calling order()
     synchronously and stay green, zero re-freeze. (evidence:
     routing-strategy test_rs1..rs8 + model_fallbacks all green under
     the async-aware router.) This is the reusable recipe for evolving
     any frozen Protocol.
   • SDD · open · A fail-OPEN port that returns a NEUTRAL value on error
     (in_flight=0 / ewma=0.0) makes the consumer degrade gracefully to a
     deterministic default (declared order) with no try/except at the
     call site beyond the port boundary — the optimization never becomes
     a correctness gate. (evidence: BS7 — a raising load_gate still
     serves declared-first.)
   • TDD · open · `make ci` runs `ruff check .` (the WHOLE tree incl.
     tests/); a subagent that lints only `src/` can miss a test-file RUF
     rule (here RUF001 ambiguous `α` in an assert message). Lesson:
     brief build/test subagents to run the SAME lint scope as the gate,
     or the orchestrator re-lints tests/ before the authoritative run.
     (evidence: make ci #1 aborted at lint on tests/balance_strategies;
     fixed cosmetically, no assertion touched.)
   • ADD · open · The `rtk` tee log filename/rotation is unreliable for
     a re-run; capturing the authoritative pytest+coverage to an
     orchestrator-owned path (`> /tmp/...log 2>&1`) is the robust way to
     read the real gate result when the wrapper log is missing.
     (evidence: the re-run produced no new rtk make_ci log; direct
     capture gave 572 passed / 81.79%.)
   • SDD · open · Reusing an EXISTING error-catalog spec (RATE_LIMITED →
     429) for a new domain error (AllDeploymentsSaturatedError) keeps
     the HTTP contract centralized: the router raises a DOMAIN error, a
     single additive use-case except clause maps it — no new status/code
     literal, no API handler change. (evidence: DL2b asserts the catalog
     produces 429; the clause is the only use_cases.py edit and sits
     before the broad handler.)
   • ADD · open · "Filter UPSTREAM of the strategy" is the clean place
     for a cross-cutting candidate constraint (saturation) — it composes
     with EVERY strategy (ordered/shuffle/least-busy/latency) and the v6
     loop without touching any of them, because the strategy only ever
     sees survivors. (evidence: DL8 — least-busy orders the post-filter
     survivor set; v6 fallback unchanged.)
   • TDD · open · A test that asserts an attribute on an EXISTING type
     must match that type's real surface — DL2b first asserted
     `ProblemError.status_code` but the field is `.status`; caught at
     test-review by reading core/errors.py before the front froze, not
     at build (where it would have been a red-for-wrong-reason). Lesson:
     when a front test touches existing code, verify its API at
     authoring time. (evidence: the DL2b `.status` correction.)
   • DDD · open · Three orthogonal per-deployment gates now coexist
     cleanly because each is a distinct domain concept with its own
     port: cooldown (UNHEALTHY, v6) · load (IN-FLIGHT/LATENCY,
     balance-strategies) · limit (SATURATED, this task). Naming them as
     separate Saturated/Cooled/ in-flight glossary terms kept the router
     logic and the 429-vs-503 distinction unambiguous. (evidence: DL7 —
     saturated≠cooled produces 429≠503.)
   • ADD · open · live-verify overlays must be self-contained: each
     overlay that drives an upstream must SET its own non-secret
     placeholder key, never inherit it from a sibling overlay it doesn't
     compose. Evidence: v8 stack (base+v4+v5+v6+v8, no v7) booted with
     an empty OpenRouter key → every upstream chat 500'd ("Illegal
     header value b'Bearer '"), the identical v7 C5 failure; fixed by
     baking the placeholder into the v8 overlay.
   • ADD · open · a cooldown/health-gate live check should assert the
     AUTHORITATIVE gate state (GET /admin/routing snapshot_state), not
     infer it from upstream-stub call counters: under a
     non-deterministic strategy (simple-shuffle) + upstream retries the
     counter is muddied and the inference flaked. Evidence: C5
     stub-counter version failed (primary counter 3->6 under retries);
     the /admin/routing-poll version passed 29/29 ×2.
   • TDD · open · a live harness that fires bursts must respect the edge
     rate limit (Envoy local_ratelimit = 50 req/s global): C1's
     40-request distribution sample + C5's trip loop drained the bucket
     and 429'd a following /admin/keys call ("local_rate_limited");
     pacing bursts under the bucket (50 ms/req) + a settle fixed it. A
     statistical check (weighted distribution) needs volume, so it needs
     pacing — the two are coupled.
   • SDD · open · proving load-balanced distribution LIVE needs a
     per-deployment served-count readout the v6 stub lacked; the v8
     stub's GET /__counters made weighted-shuffle observable
     (dep-a:dep-b ≈ 8:32 then 13:27 over weight 1:3). A router that
     distributes is only trustworthy once distribution is *observable*
     at the edge, not just unit-asserted.

 DECIDE NEXT  consolidate learnings + archive-milestone v8
════════════════════════════════════════════════════════════════════════