════════════════════════════════════════════════════════════════════════
 v25 · Tenant-managed provider credentials (BYOK)
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     7/7 done           CRITERIA  6/6 met
 GATES     7 PASS             WAIVERS   none

 goal  a tenant configures its own provider API keys in tenant settings,
       and every upstream LLM call authenticates with that tenant's keys
       — resolved per request, encrypted at rest — fully replacing the
       platform's system-env provider keys.

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 provider-credential-store   done      PASS 12†   ●●●●●●●●●
 credential-resolution-seam  done      PASS 0     ●●●●●●●●●
 dynamic-auth-byok           done      PASS 7†    ●●●●●●●●●
 provider-config-admin-api   done      PASS 0     ●●●●●●●●●
 provider-config-ui          done      PASS 0     ●●●●●●●●●
 byok-live-verify            done      PASS 2†    ●●●●●●●●●
 openai-chat-complete        done      PASS 1†    ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 6/6 met

 LEARNINGS (5 carried)
   • TDD · open · a subagent's per-task test run
     (tests/provider_credential_store + tests/migrations) declared green
     but MISSED a second hardcoded table-manifest in
     tests/guardrails/test_guardrails_core.py — only the FULL-suite
     blast-radius run caught the regression. Lesson: never accept a
     delegated "green" on a schema-touching change without the full
     suite. (evidence: test_guardrails_core_migration_column_exists
     failed on the 1052-run, passed after the sanctioned manifest
     append.)
   • ADD · open · the §5 scope-anchor freezes from a SINGLE physical
     line and a build that legitimately must touch files beyond it needs
     an explicit amend + re-snapshot (`phase tests` → `advance`) — hit
     4× here (main.py/env.py · tenants ORM + migrations manifest ·
     guardrails manifest · gate-added test). The pattern holds; the
     friction is real. (evidence: 4 SCOPE-AMENDED notes in §5.)
   • ADD · open · the mandated adversarial security refute-read found a
     real DB-coverage gap (Azure api_key encrypt→decrypt never
     DB-tested) that the all-green suite hid — the independent skeptic
     earns its keep on risk:high secret tasks; the human gate then chose
     to close it rather than accept it. (evidence: §6 Adversarial review
     findings → gap CLOSED.)
   • TDD · open · earned-green tested the adapter's transport
     (post_json) but not the dispatch contract (complete) — the live
     pass caught the gap; protocol-surface tests must assert isinstance
     against the Protocol the caller uses.
   • ADD · open · a `# type: ignore` that masks a Protocol mismatch is a
     latent 500; the verify task is what surfaced it end-to-end.

 DECIDE NEXT  consolidate learnings + archive-milestone v25
════════════════════════════════════════════════════════════════════════