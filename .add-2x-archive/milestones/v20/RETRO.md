════════════════════════════════════════════════════════════════════════
 v20 · Enterprise provider: AWS Bedrock (SigV4 · Converse chat/stream/tools · Titan embeddings)
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     6/6 done           CRITERIA  6/6 met
 GATES     6 PASS             WAIVERS   none

 goal  a tenant can call AWS Bedrock models through the proxy on the
       same OpenAI-compatible surface — chat, streaming, tool-use,
       embeddings — authenticated via SigV4, billed accurately, opt-in
       and byte-identical when disabled

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 bedrock-sigv4-auth          done      PASS 11†   ●●●●●●●●●
 bedrock-chat                done      PASS 14†   ●●●●●●●●●
 bedrock-streaming           done      PASS 3†    ●●●●●●●●●
 bedrock-tools               done      PASS 8†    ●●●●●●●●●
 bedrock-embeddings          done      PASS 1†    ●●●●●●●●●
 bedrock-verify              done      PASS 2†    ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 6/6 met

 LEARNINGS (5 carried)
   • TDD · open · An external-protocol signer/encoder must be tested
     against REALISTIC provider-specific inputs, not just the canonical
     "happy" vector — all original SigV4 fixtures used path "/", which
     hid that the path was signed RAW; real Bedrock model IDs carry a
     ':' version suffix that AWS canonicalizes to %3A, so raw-':'
     signing 403s every versioned-model call. Evidence: the verify-gate
     refute-read added SV8 (a ':'-path test) which was RED against the
     green-but-incomplete impl. Lesson: for a signer, add at least one
     fixture using the ACTUAL target service's path/identifier shape.
   • ADD · open · Pin a security primitive's core math to an
     AUTHORITATIVE published vector via a small exposed seam (here
     _signature() pinned to AWS get-vanilla 5fa00fa3…), so higher-level
     self-computed expectations (the contract variant) ride on a
     non-self-referential anchor — this is how the green stays
     trustworthy when the public API's exact shape has no published
     known-answer. Evidence: SV0 anchors SV1/SV2/SV8.
   • TDD · open · An independent-oracle stub (re-impl the auth + pin to
     the vendor's published test vector) turns a "live double-pass" into
     a CI-able cryptographic cross-check — far stronger than
     MockTransport AND not gated on docker (evidence: BV1 pins to AWS
     get-vanilla 5fa00fa3…, BV3 proves rejection, BV2 proves the real
     %3A-path signature passes; all in the no-DB floor).
   • ADD · open · The §5 scope-token grammar cannot express a
     project-root-level file (bare token = sibling-of-previous-dir; only
     '/'-containing tokens resolve to root) — so a
     Makefile/top-level-file edit needs either an unconventional '../'
     token or its own handling. Carried follow-up: add the 6 bedrock
     suites to the `make test-fast` floor as a standalone Makefile edit
     (or fold-time). Evidence: this task's gate returned scope_violation
     on a bare `Makefile` token that resolved to `infra/Makefile`.
   • ADD · open · A live-infra verify task should split into (a) a
     docker-free earned-green core that fully proves the logic and (b)
     operator scripts for the edge/cache/billing pass — so the gate
     never blocks on bringing up a heavy stack, while the residue stays
     honest. Evidence: bedrock-verify auto-gated on the pytest core; the
     TLS-edge ×2 is ready-residue.

 DECIDE NEXT  consolidate learnings + archive-milestone v20
════════════════════════════════════════════════════════════════════════