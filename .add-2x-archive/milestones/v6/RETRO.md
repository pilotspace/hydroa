════════════════════════════════════════════════════════════════════════
 v6 · LiteLLM parity slice 4 — routing & resilience
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     5/5 done           CRITERIA  6/6 met
 GATES     5 PASS             WAIVERS   none

 goal  a tenant's completion survives upstream failure — bounded retries
       with backoff, ordered model fallbacks, per-model cooldown circuit
       breaking, and admin-visible upstream health — without ever
       double-billing or corrupting the ledger

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 retry-policy                done      PASS 2†    ●●●●●●●●
 model-fallbacks             done      PASS 5†    ●●●●●●●●
 cooldown-circuit            done      PASS 2†    ●●●●●●●●
 routing-admin               done      PASS 0     ●●●●●●●●
 v6-live-verify              done      PASS 6†    ●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 6/6 met

 LEARNINGS (9 carried)
   • SDD · open · The "NEVER retry" prose in a frozen task required a
     SUPERSESSION pattern rather than file edit — evidence:
     proxy-completions TASK.md §1 pin; JwksKeyCache precedent confirmed
     the approach works across milestones.
   • TDD · open · Full-jitter backoff requires monkeypatching both
     `random.uniform` AND `asyncio.sleep` to assert timing without
     wall-clock waits — evidence: R4 design captures the sleep call
     rather than measuring elapsed time.
   • ADD · open · Risk=high tasks need explicit retryable-classification
     tables in §1 to prevent ambiguous build-phase interpretation; the
     table format proved load-bearing here.
   • SDD · open · Served-model billing required surfacing the concrete
     model id from the response body rather than passing the router's
     input — evidence: F7 scenario; response_body["model"] is the only
     authoritative served-model signal at the use-case boundary.
   • TDD · open · GREEN-BY-DESIGN tests (F11) are a valid pattern for
     "absence of behavior" — marking them explicitly in the plan
     prevents confusion at red-phase verification.
   • ADD · open · Parallel tasks sharing a protocol definition
     (ModelHealthGate) require the owning task to define the frozen
     interface before the consuming task builds against it.
   • SDD · open · Half-open probe semantics in a distributed, TTL-keyed
     system require explicit state-table documentation — the "HALF_OPEN"
     state in the in-process breaker has no direct Redis analogue;
     evidence: needed a 5-row state table to express what the in-process
     breaker does with 3 enum values.
   • TDD · open · Fake Redis for concurrent SET NX tests must serialize
     asyncio tasks carefully — asyncio.gather does not guarantee
     interleaving order; the fake must process commands atomically
     (single-threaded asyncio = no actual concurrency in fake) which
     means the NX test requires task ordering discipline (one task
     yields before the other checks); evidence: C4 design.
   • ADD · open · The concurrent-probe race is the canonical example of
     a [contract]-level flag that cannot be fully resolved by spec alone
     — it requires acceptance criteria on the TTL relationship (probe
     request duration < probe TTL); this should become a BUILD
     constraint, not just a §3 flag.

 DECIDE NEXT  consolidate learnings + archive-milestone v6
════════════════════════════════════════════════════════════════════════