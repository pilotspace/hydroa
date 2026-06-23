════════════════════════════════════════════════════════════════════════
 v33 · Reconciliation & disconnect-billing hardening
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     4/4 done           CRITERIA  4/4 met
 GATES     4 PASS             WAIVERS   none

 goal  Make the reconciliation and disconnect-billing pipeline
       trustworthy under bad config and partial streams: nonsense config
       fails loud at startup, the unbilled-upstream leak filter is
       explicitly provider-scoped, residual non-OpenRouter disconnect
       rows become recoverable, and no passthrough response can crash on
       non-finite numbers.

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 passthrough-nonfinite-sani… done      PASS 0     ●●●●●●●●●
 reconcile-cost-basis-filter done      PASS 0     ●●●●●●●●●
 disconnect-provider-cost    done      PASS 0     ●●●●●●●●●
 drift-threshold-validation  done      PASS 10†   ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 4/4 met

 LEARNINGS (8 carried)
   • ADD · open · a v28 §7 delta named the FIX SITES verbatim
     ("images_router:49, embeddings_router:64, proxy/api/router.py:82")
     — a precise carry-over delta makes the next task's ground nearly
     free; reward writing fix-site-specific deltas (evidence: §0 came
     straight from the delta).
   • TDD · open · the v28 placement (use_case) was NOT the right place
     for the siblings — re-derive placement from THIS task's control
     flow (chat/embeddings cache-HIT bodies bypass the use_case → only
     converge at the router), don't copy the precedent's location
     blindly (evidence: §0 PLACEMENT DECISION).
   • ADD · open · re-grounding a pre-written task stub against HEAD
     before specify caught that the cost_basis='provider' guard already
     shipped in v29/v30 — the real deliverable was the net-new audit +
     belt-and-suspenders FILTER, not the already-present guard
     (evidence: §0 ground vs reconcile_window lines 120-124)
   • TDD · open · when a primitive scans globally over a ledger NOT
     truncated between tests, scope every assertion to the
     just-signed-up tenant (filter by tenant_id) or cross-test row
     persistence makes the empty-case assertion flaky (evidence:
     test_audit_empty would see test_audit_finds's breach under a global
     count)
   • TDD · open · round-trip recorder→flusher→ledger on a dedicated
     Redis index (/9 + flushdb) is the honest way to test recorder
     costing changes — a mocked recorder would have hidden the
     created_at/tz interaction the wide-window fix surfaced (evidence:
     test_partial_disconnect first failed on reconcile windowing, not
     the stamp)
   • ADD · open · a billing-semantics fork mid-build (stamp vs audit vs
     both) is a genuine decision point even under autonomy:auto —
     AskUserQuestion resolved it without a security HARD-STOP, and the
     chosen 'both' composed cleanly with the sibling task's cost_basis
     audit (evidence: stamp uses cost_basis='provider' so
     audit_cost_basis_breaches needs no exemption)
   • ADD · open · a `phase: done` task stub that was never committed can
     be STALE — re-ground against the live code before reusing it: here
     the threshold half had already shipped in v30, so the task's real
     remaining scope was only the sibling interval knob (evidence: §0
     RE-GROUND; config.py already had `_validate_drift_threshold` + its
     5 tests).
   • TDD · open · when extending a family of validators, mirror the
     sibling's EXACT error-code/message shape and assert via the same
     `pytest.raises(match=CODE)` pattern — the new test slotted beside
     the 5 existing threshold tests with zero new scaffolding (evidence:
     test_config.py v33 block mirrors the v30 block).

 SPEC DELTAS    47 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone v33
════════════════════════════════════════════════════════════════════════