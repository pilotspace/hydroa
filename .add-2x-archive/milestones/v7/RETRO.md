════════════════════════════════════════════════════════════════════════
 v7 · LiteLLM parity slice 5 — multi-modal & multi-provider
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     6/6 done           CRITERIA  6/6 met
 GATES     6 PASS             WAIVERS   none

 goal  a tenant can call embeddings, image-generation, and audio
       (speech-to-text + text-to-speech)

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 provider-seam               done      PASS 11†   ●●●●●●●●
 pricing-units               done      PASS 2†    ●●●●●●●●
 embeddings-endpoint         done      PASS 0     ●●●●●●●●
 images-endpoint             done      PASS 0     ●●●●●●●●
 audio-endpoints             done      PASS 0     ●●●●●●●●
 v7-live-verify              done      PASS 0     ●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 6/6 met

 LEARNINGS (10 carried)
   • DDD · open · Modality is a domain concept stored as TEXT with a
     Literal type alias — not a DB ENUM. This avoids ALTER TYPE
     migrations for future modality additions. Evidence: the five
     modality values are bounded but may grow (e.g. "video" in a future
     slice).
   • ADD · open · The chat-untouched boundary is a cross-cutting
     invariant that cannot be enforced by the type system alone — it
     requires explicit "do NOT use get_completion_upstream from non-chat
     endpoints" constraints in downstream task contracts. Evidence: §3
     inviolable boundary note + PS9 wiring regression.
   • SDD · open · NonChatGovernance drops the chat M11 soft-budget-alert
     seam on the non-chat path (HARD 402 is preserved; only the advisory
     fire-and-forget alert is absent). Evidence: frozen [contract] flag
     + governance.py `_check_per_key_budget` omits
     `persist_soft_budget_alert`. Soft-budget-alert parity for
     embeddings/images/audio should be revisited at v7 close (a future
     slice could add an alert seam shared by chat + non-chat, or accept
     the gap explicitly).
   • ADD · open · The chat-untouched invariant has no compile-time
     enforcement — it rests on EM11 + a manual `git diff --stat` of the
     three INVIOLABLE files. Evidence: §3 INVIOLABLE note + the verify
     WIRING check. A future improvement: an ArchUnit-style test
     asserting non-chat modules never import CompletionUseCase's private
     governance methods.
   • SDD · open · NonChatGovernance drops the chat M11 soft-budget-alert
     seam on the non-chat path (inherited from embeddings-endpoint
     disposition 1). HARD 402 is preserved; advisory alert absent.
     Soft-budget-alert parity for images should be revisited at v7 close
     together with embeddings/audio.
   • ADD · open · The billed-quantity fallback policy (actual-returned
     vs requested-n) is a business decision not a technical one. The
     [contract] flag at §3 top ensures it is surfaced before freeze.
     </output>
   • SDD · open · STT duration source depends on verbose_json
     response_format — caller must explicitly request it for accurate
     billing; absent duration → $0 cost, WARN only. Evidence: AU2b test
     + [contract] flag in §3.
   • SDD · open · TTS bill-at-start: customers charged for stream
     failures after 200 is committed. Evidence: AT2 test + [contract]
     flag in §3. Matches OpenAI billing model.
   • ADD · open · live-verify e2e closes need their upstream creds
     self-contained in the overlay, not sourced from operator shell env
     — the v7 stack came up with an empty GATEWAY_OPENROUTER_API_KEY and
     C5 failed opaquely (evidence: C5 500 "Illegal header value b'Bearer
     '"; fixed by baking a placeholder into the v7 overlay). Consider
     auditing v4–v6 overlays for the same shell-env dependency.
   • SDD · open · an empty-but-present upstream key produces a
     client-side 500 with no actionable message; the spec should require
     a boot-time guard that rejects a configured-yet-empty upstream key
     (evidence: the only C5 failure mode this loop).

 DECIDE NEXT  consolidate learnings + archive-milestone v7
════════════════════════════════════════════════════════════════════════