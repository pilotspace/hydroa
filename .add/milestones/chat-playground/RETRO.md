════════════════════════════════════════════════════════════════════════
 chat-playground · Chat — Console-grade playground
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     6/6 done           CRITERIA  6/6 met
 GATES     6 PASS             WAIVERS   none

 goal  The Chat workspace becomes a true Console-grade playground: full
       sampling-parameter control, tool/function calling, multimodal
       attachments, rich per-run metadata + cost, and first-class
       conversation management — a surface an operator runs real LLM
       work on.
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 chat-playground-shell       done      PASS 0     ●●●●●●●●●
 chat-parameters-panel       done      PASS 0     ●●●●●●●●●
 chat-tools-functions        done      PASS 0     ●●●●●●●●●
 chat-attachments            done      PASS 0     ●●●●●●●●●
 chat-run-metadata-cost      done      PASS 0     ●●●●●●●●●
 chat-conversation-mgmt      done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 GATED BY
   chat-playground-shell    PASS Tin Dang <tindang.ht97@gmail.com>
   chat-parameters-panel    PASS Tin Dang <tindang.ht97@gmail.com>
   chat-tools-functions     PASS Tin Dang <tindang.ht97@gmail.com>
   chat-attachments         PASS Tin Dang <tindang.ht97@gmail.com>
   chat-run-metadata-cost   PASS Tin Dang <tindang.ht97@gmail.com>
   chat-conversation-mgmt   PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 6/6 met

 LEARNINGS (11 carried)
   • DDD · open · "pass-through" is not capability-neutral: the
     OpenAI-compatible wire hides that providers DROP or 400 on params.
     Research-before-build (verify the seam against the live provider
     APIs + the gateway's real translation) caught a misleading-no-op UX
     before it shipped (evidence: Tin's "research first" instruction →
     the provider-variance findings → v2 gating change-request).
   • TDD · open · a body-capture MSW harness (assert the POST body, not
     component internals) makes pass-through param wiring + provider
     gating provable without a real gateway (evidence:
     chat-parameters.test.tsx body box + the model-switch gating case).
   • UDD · open · honest gating > silent no-op: disabling + annotating
     ("Ignored by <Provider>") an unsupported control, and omitting it
     from the body, is the truthful UI when the backend would silently
     drop it (evidence: the live-vs-gated capture).
   • TDD · open · An auto-PASS suite can be green yet leave a
     forbidden-behaviour unasserted (D1: "onTurnComplete must NOT fire
     on a tool turn" was structurally true but unpinned). The
     adversarial refute-read caught it; closing it needed a hook-level
     test, not another UI test (evidence: ChatWorkspace owns
     onTurnComplete internally — the seam is only assertable at
     useChatStream).
   • ADD · open · A frozen-contract clause can be satisfied by a
     more-robust SUPERSET of its literal wording (C1: detect-tool-calls
     vs detect-finish_reason). Honest path = record the deviation as a
     SPEC delta, not silently edit the contract (evidence: refute-read
     flagged the literal mismatch; behaviour is correct).
   • TDD · open · when a refute-read FLAGs "the test sidesteps a race",
     close it by REPLACING the sidestep with a test that drives THROUGH
     the race AND falsifying that test against the buggy code (evidence:
     test_count_cap_holds_under_concurrent_picks fires 5 picks in one
     synchronous burst — FAILS on the stale-snapshot impl, PASSES on the
     live-count re-check; that falsification is what rebuts the
     "test-structure cheat" verdict).
   • TDD · open · an async event handler that enforces a cap by reading
     a React ref/state snapshot at entry is racy under concurrent
     invocations; enforce against a LIVE count (synchronous ref bump on
     admit + post-await re-check), not a per-call local counter
     (evidence: 5 concurrent onPickFiles each read length=0 and
     over-admitted).
   • ADD · open · `cli.js update` from the LOCAL plugin marketplace can
     DOWNGRADE the engine (marketplace stale at 1.12.0 < project 1.13.0)
     and dirties .add/tooling + .add/docs + .claude/skills +
     .add/.add-version — restore all from git HEAD (every file is
     tracked); the npx registry route is unreliable in this env
     (evidence: this session's downgrade + git-checkout recovery).
   • ADD · open · restoring tracked NON-scope files DURING verify
     re-trips the scope anchor (the snapshot was taken at build entry
     while those files were still dirty) → the honest reset is to
     re-cross tests→build (`add.py phase build`) to re-snapshot the
     clean tree, then advance + gate (evidence: scope_violation on 5
     .claude/skills/add/* files that were clean at gate time).
   • TDD · open · The finish_reason capture strategy
     (last-non-empty-seen) should be validated against real
     Anthropic/Gemini provider wire format (evidence: assumption flagged
     at freeze)
   • SDD · open · raw SQLAlchemy UPDATE does not trigger ORM onupdate
     hooks — workaround: always supply updated_at=now() explicitly in
     VALUES (evidence: rename_title implementation; mirrors
     append_message lesson from v40)

 SPEC DELTAS    208 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone chat-playground
════════════════════════════════════════════════════════════════════════