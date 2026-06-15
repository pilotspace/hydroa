════════════════════════════════════════════════════════════════════════
 v19 · Reliability — uniform retries, error-aware fallback, response & semantic caching
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     6/6 done           CRITERIA  6/6 met
 GATES     6 PASS             WAIVERS   none

 goal  a tenant's LLM request survives transient upstream failures and
       model outages — uniform error-aware retries across every
       provider, smarter model fallback, pre-first-byte streaming
       resilience, and response + semantic cache reuse — with billing
       accuracy preserved and byte-identical behavior at default
       settings

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 retry-seam-unify            done      PASS 8†    ●●●●●●●●●
 error-aware-fallback        done      PASS 18†   ●●●●●●●●●
 cache-controls              done      PASS 15†   ●●●●●●●●●
 streaming-resilience        done      PASS 0     ●●●●●●●●●
 semantic-cache              done      PASS 7†    ●●●●●●●●●
 reliability-verify          done      PASS 2†    ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 6/6 met

 LEARNINGS (6 carried)
   • ADD · open · A build-phase lint/format pass that touches the task's
     own test files trips the §5 scope-gate (test files aren't in the
     src Scope); declaring the test dir in §5 + re-crossing tests→build
     (sanctioned re-snapshot) resolves it cleanly. Evidence: ruff-format
     on the 3 new test files diverged them from the tests→build
     snapshot. Lesson: declare the test surface in §5 when the build
     will lint/format newly-authored tests, OR run format inside the
     tests phase before the snapshot.
   • TDD · open · The adversarial refute-read caught a real
     is_last/deadline mislabeling bug the green suite missed (the
     existing deadline + exhausted tests didn't exercise
     is_last-WITH-active-deadline). Evidence: refute-read REAL-BUG
     finding → fixed. Lesson: a cumulative-deadline interacts with the
     exhaustion boundary; a verify-gate refute-read earns its keep on
     retry/timeout logic where boundary states are easy to leave
     untested.
   • ADD · open · Editing the §3 pseudocode comment AFTER the
     tests→build snapshot trips the tamper tripwire (it md5s the WHOLE
     §3 body, not just the signature block). Evidence: reverted the
     comment edit to keep the tripwire green. Lesson: post-freeze
     refinements go in §6/§7, never in §3 — the frozen body is immutable
     bytes, comments included. <!-- competency deltas are written
     `open`; the human consolidates at fold. -->
   • ADD · open · A frozen §3 RANGE can silently contradict a §1 REJECT
     enumeration (here: §3 "status 400-499" vs §1 "429 already
     retry-handled") — the freeze gate did not catch it; the adversarial
     refute-read did. Lesson: when §3 states a broad range, cross-check
     it against §1's explicit rejects at freeze. Evidence: refute-read
     RISK finding → classifier now excludes 408/429.
   • TDD · open · A pure classifier's pattern list must be tested in
     BOTH directions — true-positives (provider-real messages) AND
     false-positives (generic 400s like "field too long", "blocked by
     firewall") — because a too-broad pattern fails DANGEROUS (spurious
     fallover), not safe. Evidence: 5 guard tests added in-loop after
     the refute-read flagged bare `"too long"`/`"safety"`/`"blocked
     by"`.
   • ADD · open · Acting on adversarial-review findings in-loop
     (re-cross tests→build to re-snapshot) keeps the gate honest without
     weakening any test — a verify-time refinement is legitimate when it
     STRENGTHENS assertions and leaves §3 byte-identical. Evidence: this
     task's PASS after the refine-and-re-cross.

 DECIDE NEXT  consolidate learnings + archive-milestone v19
════════════════════════════════════════════════════════════════════════