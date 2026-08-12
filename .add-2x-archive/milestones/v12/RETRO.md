════════════════════════════════════════════════════════════════════════
 v12 · Billing accuracy + ops hardening — pay down v7/v9/v11 follow-up debt
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     5/5 done           CRITERIA  5/5 met
 GATES     5 PASS             WAIVERS   none

 goal  the proxy bills exactly for every modality (no estimates), fails
       fast and clearly on misconfiguration, surfaces soft-budget alerts
       uniformly across chat and non-chat, and runs a deterministic test
       suite

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 gemini-embed-tokens         done      PASS 0     ●●●●●●●●
 empty-key-boot-guard        done      PASS 0     ●●●●●●●●
 nonchat-soft-budget-alert   done      PASS 0     ●●●●●●●●
 test-db-isolation           done      PASS 0     ●●●●●●●●
 v12-live-verify             done      PASS 0     ●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 EXIT CRITERIA  ●●●●●●●●●● 5/5 met

 LEARNINGS (5 carried)
   • SDD · open · a provider count with no inline usage is recovered via
     a SEPARATE provider count endpoint (Gemini :countTokens) on the
     SAME adapter, made FAIL-SAFE (None → documented estimate fallback)
     so billing accuracy never becomes an availability gate — evidence:
     countTokens 5xx/timeout/missing-totalTokens all fall back green,
     embed 200 never failed.
   • TDD · open · a billing-behavior CHANGE supersedes the prior
     estimate tests rather than weakening them — the v9 estimate tests
     were updated to the exact-count contract (handlers serve
     :countTokens) and documented as supersession at the freeze;
     green-by-design fallback tests (missing-totalTokens / timeout /
     no-count-on-embed-fail) passed pre-build — evidence: 6
     RED-for-right-reason + 3 green-by-design, 68/68 blast radius
     post-build.
   • ADD · open · a fail-safe count leg must be excluded from the embed
     circuit breaker — a best-effort billing call sharing the client
     must not open the breaker on the product path (evidence:
     `_count_gemini_tokens` returns None without touching the breaker;
     embed-fail test proves the count leg never runs before a successful
     embed).
   • ADD · open · some misconfigurations are observable ONLY at the
     raw-environment level, not the parsed-config level — Settings
     collapses unset and set-empty to "", so a boot guard that must tell
     "disabled" from "misconfigured" reads os.environ directly
     (evidence: the Settings-level wiring tests stay green while the
     env-level guard catches present-empty).
   • SDD · open · a fail-fast boot guard at the composition root
     converts an opaque per-request 500 into a clear startup error
     naming the fix — the v7+v8 empty-bearer class is eliminated at the
     boundary rather than handled per-adapter (evidence: create_app
     raises before any adapter; 7/7 green incl. the two create_app
     boot-path tests).

 DECIDE NEXT  consolidate learnings + archive-milestone v12
════════════════════════════════════════════════════════════════════════