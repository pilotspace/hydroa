════════════════════════════════════════════════════════════════════════
 v2 · Production-ready metered proxy
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     6/6 done           CRITERIA  6/6 met
 GATES     6 PASS             WAIVERS   none

 goal  the v1 MVP runs in production posture — TLS at the edge,
       migration-managed schema, cookie-based dashboard auth, full
       observability, and a live-verified OpenRouter billing path

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 db-migrations               done      PASS 0     ●●●●●●●●
 live-upstream-smoke         done      PASS 6†    ●●●●●●●●
 edge-tls                    done      PASS 0     ●●●●●●●●
 auth-bff                    done      PASS 0     ●●●●●●●●
 observability               done      PASS 0     ●●●●●●●●
 ops-hardening               done      PASS 11†   ●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 6/6 met

 LEARNINGS (7 carried)
   • SDD · open · mock fixtures that mirror an assumed wire format can
     pass while live billing silently fails — pin at least one VERBATIM
     live-captured frame per external protocol (evidence: v1 streaming
     tests green while live ledger recorded 0/0 vs upstream 24/73)
   • TDD · open · for stream parsers, fragmentation is part of the input
     domain — include split-at-midpoint and byte-by-byte cases by
     default (evidence: tests/smoke parametrized fragmentation caught
     the rewrite regression-free) <!-- e.g. - [DDD · open] the model
     missed multi-tenancy (evidence: scenario_x failed) -->
   • ADD · open · freeze-time orchestrator review of label/dimension
     design pays for itself — an exit criterion phrased as a specific
     rate (402) must be expressible from the contracted labels, not an
     aggregate (evidence: status_class→status_code amendment pre-freeze)
   • TDD · open · a frozen test arrange that invents endpoints can push
     builders into expanding product surface — arranges must use
     canonical routes, and builders must treat 'Modules touched' as a
     hard boundary (evidence: /tenants compat router rejected at review,
     disposition in §3)
   • SDD · open · BaseHTTPMiddleware isolates contextvars in a child
     task; binding-through-middleware designs need pure-ASGI middleware
     (evidence: tenant_id binding lost under BaseHTTPMiddleware, visible
     with raw ASGI) <!-- e.g. - [DDD · open] the model missed
     multi-tenancy (evidence: scenario_x failed) -->
   • TDD · open · batch-bounded loops (read N per iteration) hide
     early-exit defects when the emptiness check looks at the wrong set
     — assert drains against backlogs LARGER than the batch size in
     future drain tests (evidence: flush_once count=100 + PEL-only check
     exited early; caught at orchestrator review, not by the suite)
   • ADD · open · runbook advice that prescribes config
     (stop_grace_period) should be enforced in the artifact it
     describes, not just documented (evidence: prod compose now carries
     stop_grace_period 15s) <!-- e.g. - [DDD · open] the model missed
     multi-tenancy (evidence: scenario_x failed) -->

 DECIDE NEXT  consolidate learnings + archive-milestone v2
════════════════════════════════════════════════════════════════════════