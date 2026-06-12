════════════════════════════════════════════════════════════════════════
 v10 · LiteLLM parity slice 8 — tool-use / function-calling across providers
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     4/4 done           CRITERIA  5/5 met
 GATES     4 PASS             WAIVERS   none

 goal  a tenant sends OpenAI tools + tool_choice to a chat model on any
       provider (OpenRouter, Anthropic, Gemini) and gets OpenAI-shaped
       tool_calls back — non-stream and streaming — with native
       translation, billing, and v8 routing intact

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 tool-use-contract           done      PASS 11†   ●●●●●●●●
 anthropic-tool-use          done      PASS 10†   ●●●●●●●●
 gemini-tool-use             done      PASS 8†    ●●●●●●●●
 tool-use-live-verify        done      PASS 0     ●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 5/5 met

 LEARNINGS (15 carried)
   • DDD · open · Tool/function-call enters the domain as a canonical
     (OpenAI) vocabulary every provider maps to/from, with tool-call-id
     SYNTHESIS as a first-class concept for id-less native providers
     (Gemini) — the id is gateway-owned, name+index-derived,
     secret-free. Evidence: tool_translation.py frozen shapes +
     synthesize_tool_call_id; 16/16 green.
   • SDD · open · The freeze-first SHARED-SEAM pattern (v9 dispatch
     wrapper) repeats for a richer request/response SHAPE: freeze the
     canonical types + pure helpers + the passthrough/byte-identical
     pins FIRST, let each provider build its native translation against
     them. Evidence: this task delivers zero provider logic, only the
     seam + characterization.
   • TDD · open · A freeze-first contract task's red suite mixes UNIT
     tests (the new helpers) with CHARACTERIZATION pins (tools flow
     unstripped through the v9 dispatch seam; no-tools byte-identical) —
     the pins guard a behavior that already works so the provider tasks
     cannot silently break it. Evidence:
     test_request_passthrough_tools_unstripped +
     test_no_tools_request_byte_identical_v9 green against unchanged v9
     code.
   • ADD · open · Verified the request-side assumption IN CODE before
     freezing (router.py:42 forwards a raw dict) so the contract pins a
     real invariant, not a hoped-for one — the chat body must stay a raw
     dict (a Pydantic model would strip tools and break passthrough).
     Evidence: §1 framing rejected the ChatRequest-model option on this
     ground.
   • DDD · open · Anthropic's tool model is CONTENT-BLOCK-based
     (tool_use / tool_result blocks inside a message's content list),
     not a sibling field like OpenAI's tool_calls — translation
     restructures the MESSAGE shape (assistant tool_calls → content
     blocks; a run of role:"tool" → one user turn of tool_result
     blocks), not just adds a field. Evidence:
     _assistant_tool_calls_to_content + the merge loop; 13/13 green.
   • SDD · open · The v9 per-provider helper triad
     (request/response/SSE) is the natural extension point for a richer
     shape: tools landed as additive branches in the SAME 3 helpers with
     zero adapter-class change — the v9 seam absorbed a non-trivial new
     request/response shape without a re-freeze. Evidence:
     anthropic_upstream.py adapter unchanged; only the 3 pure helpers
     grew.
   • TDD · open · Two of the 10 red tests (no-tools-byte-identical,
     string-content-unchanged) were GREEN-BY-DESIGN from the start —
     they pin v9 preservation, so they MUST stay green through the
     build; the other 8 drive the new behavior. The frozen v9 suite (16
     tests) is the load-bearing regression guard. Evidence: 2 passed at
     red phase, all 29 green after build.
   • ADD · open · A streaming tool-call needs an index REMAP: Anthropic
     content-block index (text + tool blocks interleaved) ≠ OpenAI
     tool_calls index (tool calls only) — a block_to_tc dict keyed off
     the content-block index bridges them. This remap recurs for any
     provider whose stream interleaves text and tool events. Evidence:
     test_streaming_tool_call_fragments green with the block_to_tc
     mapping.
   • DDD · open · The tool-call-id SYNTHESIS concept (frozen v10) earns
     its keep on Gemini: the response synthesizes ids for id-less
     functionCalls, and the follow-up correlates the result back BY NAME
     via an id→name map rebuilt from the assistant tool_calls echoed in
     the same request — id is for the OpenAI client, name is for Gemini.
     Evidence: _gemini_to_openai synth + the id_to_name pre-pass; 11/11
     green.
   • SDD · open · Each provider's tool translation is ASYMMETRIC in
     streaming granularity but UNIFORM at the OpenAI seam: Gemini emits
     one combined id+name+arguments fragment (whole functionCall in one
     part) while Anthropic streams id+name then incremental
     input_json_delta — both produced via the SAME build_tool_call_delta
     helper. The frozen streaming-fragment shape absorbed both without
     change. Evidence: gemini one-shot vs anthropic multi-fragment,
     identical helper.
   • TDD · open · The name-correlation needed a TWO-MESSAGE fixture
     (assistant tool_calls turn + the tool message) to test honestly — a
     tool message alone cannot exercise the id→name resolution.
     Single-message unit fixtures would have hidden the core risk.
     Evidence: test_tool_result_name_correlated uses the full 3-message
     history.
   • ADD · open · Provider tool-translation is now a REPEATABLE 4-step
     shape (request tools/tool_choice + message restructure · response
     native-call→tool_calls · streaming native-event→delta fragment ·
     no-tools byte-identical pin), proven twice (anthropic + gemini).
     The next provider (Bedrock/Azure) follows the same template against
     the frozen contract. Evidence: anthropic-tool-use + gemini-tool-use
     landed identically-shaped.
   • ADD · open · live e2e proof of a multi-turn protocol fits the
     one-stateless-stub harness pattern (request-inspection turn
     discrimination) — evidence: 18/18 ×2, both passes exit 0, no
     turn-state bug.
   • TDD · open · operator-run live checks served as the red→green suite
     for cross-provider translation (red against v9-only gateway, green
     after v10 tasks 2+3) — evidence: C1–C4 failed pre-build, passed
     post-build; C5 byte-identical throughout.
   • SDD · open · the frozen harness contract (stub surfaces + overlay
     env + check list) let the stub/overlay/verify be built
     independently and compose first-try — evidence: seed-then-restart
     resolver refresh worked first-try on the new :9924 port.

 DECIDE NEXT  consolidate learnings + archive-milestone v10
════════════════════════════════════════════════════════════════════════