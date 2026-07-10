════════════════════════════════════════════════════════════════════════
 logs-explorer-guardrails-v2 · Request Logs Explorer + Guardrails v2
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     8/8 done           CRITERIA  7/7 met
 GATES     8 PASS             WAIVERS   none

 goal  A tenant admin can opt into PII-scrubbed request/response
       capture, explore and replay logged calls from the console, and
       enforce per-key guardrail policies with ML moderation, output
       schema validation, and guardrail analytics.
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 payload-capture-store       done      PASS 0     ●●●●●●●●●
 per-key-guardrail-policies  done      PASS 0     ●●●●●●●●●
 ml-moderation-layer         done      PASS 0     ●●●●●●●●●
 output-schema-validation    done      PASS 14†   ●●●●●●●●●
 logs-explorer-api           done      PASS 0     ●●●●●●●●●
 logs-explorer-ui            done      PASS 0     ●●●●●●●●●
 guardrail-analytics         done      PASS 0     ●●●●●●●●●
 request-log-metering-fields done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 GATED BY
   payload-capture-store    PASS Tin Dang <tindang.ht97@gmail.com>
   per-key-guardrail-polic… PASS Tin Dang <tindang.ht97@gmail.com>
   ml-moderation-layer      PASS Tin Dang <tindang.ht97@gmail.com>
   output-schema-validation PASS Tin Dang <tindang.ht97@gmail.com>
   logs-explorer-api        PASS Tin Dang <tindang.ht97@gmail.com>
   logs-explorer-ui         PASS Tin Dang <tindang.ht97@gmail.com>
   guardrail-analytics      PASS Tin Dang <tindang.ht97@gmail.com>
   request-log-metering-fi… PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 7/7 met

 LEARNINGS (2 carried)
   • DDD · open · a guardrail check that performs real outbound IO needs
     a THIRD verdict state (`unchecked`) beyond the deterministic
     checks' `passed`/`blocked`/`audited` vocabulary, plus its own
     config axis (`failure_mode`, orthogonal to `mode`) — the first
     guardrail in this codebase with an external failure mode of its own
     (evidence: §1 M6, Glossary delta "Unchecked").
   • SDD · open · a BYOK provider used for an ANCILLARY IO seam
     (moderation) needs an ISOLATED CircuitBreaker/client instance from
     the SAME provider's PRIMARY seam (chat completions) — sharing one
     adapter instance across two independent failure domains would
     cross-contaminate breaker state; worth a general pattern note for
     any future secondary use of an existing provider adapter (evidence:
     §0 R3, §1 M8).

 SPEC DELTAS    273 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone
              logs-explorer-guardrails-v2
════════════════════════════════════════════════════════════════════════