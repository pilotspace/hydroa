════════════════════════════════════════════════════════════════════════
 v11 · LiteLLM parity slice 9 — JSON-mode / structured outputs across providers
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     4/4 done           CRITERIA  6/6 met
 GATES     4 PASS             WAIVERS   none

 goal  a tenant sends response_format (json_object or json_schema) to a
       chat model on any provider (OpenRouter, Anthropic, Gemini) and
       gets back JSON-conformant output — non-stream and streaming —
       with native translation, billing, tool-use, and v8 routing intact

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 response-format-contract    done      PASS 12†   ●●●●●●●●
 gemini-json-mode            done      PASS 8†    ●●●●●●●●
 anthropic-json-mode         done      PASS 10†   ●●●●●●●●
 json-mode-live-verify       done      PASS 0     ●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done
 † counted at the §4-declared path

 EXIT CRITERIA  ●●●●●●●●●● 6/6 met

 LEARNINGS (13 carried)
   • DDD · open · response_format enters the domain as a canonical
     OpenAI directive with TWO native mechanisms across providers —
     native structured-output (Gemini responseMimeType/responseSchema)
     vs tool-COERCION (Anthropic, no native field) — plus a
     gateway-owned reserved coercion tool name; the model output always
     returns as message.content JSON string, never a new field
     (evidence: response_format_translation.py frozen; 15/15 green).
   • SDD · open · the freeze-first SHARED-SEAM pattern (v9 dispatch, v10
     tools) repeats a THIRD time for response_format, and this time the
     seam COMPOSES with a prior seam — the Anthropic json_schema path
     reuses v10's Tool/ToolChoiceNamed + tool helpers rather than
     inventing a parallel mechanism (evidence: build_json_coercion_tool
     returns canonical v10 types).
   • ADD · open · a contract task can prove its
     passthrough/byte-identical pins GREEN-BY-DESIGN against UNCHANGED
     dispatch code with a spy adapter — 3 of 15 tests guard
     response_format-unstripped + openrouter-verbatim +
     no-rf-byte-identical and pass before any provider build (evidence:
     reused the v10 _SpyAdapter/_ScriptedResolver pattern verbatim).
   • ADD · open · verified the raw-dict passthrough invariant IN CODE
     before freezing (router.py:42 reads `body: dict[str, Any]` and
     forwards it) so the contract pins a real invariant — the recurring
     v10 lesson applied again (evidence: §1 assumption confirmed
     pre-freeze).
   • TDD · open · the full `-m 'not e2e'` suite is NON-DETERMINISTIC
     against the shared dev Postgres (5433): 16/34/44 failures across
     runs, all `ForeignKeyViolationError: api_keys_tenant_id_fkey` in
     DB-touching suites; each failing suite passes IN ISOLATION
     (tests/keys 20/20). The trustworthy per-change gate is the no-DB
     blast-radius run (translation+dispatch suites, 100/100, 1.2s) — the
     recurring v8 CI-flake; the foundation needs per-test DB isolation
     (txn-rollback fixture / template DB) so the full suite is
     deterministic, OR a documented `make test-fast` that excludes DB
     suites for per-change gating (evidence: this build's 16/34/44
     variance with a zero-blast-radius pure module).
   • SDD · open · response_format on a native-field provider (Gemini) is
     REQUEST-SIDE ONLY: responseMimeType/responseSchema added to the
     existing generationConfig, output already maps to message.content
     via the unchanged v9 response path — no response/SSE code
     (evidence: gemini-json-mode touched only _openai_to_gemini_request;
     38/38 gemini suites green).
   • ADD · open · the frozen-contract extractor
     (extract_response_format) is the SHARED no-op/validation gate every
     provider reuses — Gemini gets the byte-identical guarantee + the
     two rejections for free by calling it, rather than re-implementing
     the parse (evidence: 1 import + 1 call delivered the whole request
     branch).
   • DDD · open · a provider with no native structured-output field
     satisfies json_schema by COERCION — a synthetic forced tool whose
     tool_use is UNWRAPPED back into message.content (tool_use → content
     inversion); the gateway-owned json_output name is the correlation
     key on every leg (request build, response unwrap, stream route)
     (evidence: anthropic-json-mode 10/10 green incl. streaming unwrap).
   • SDD · open · response_format COMPOSES with v10 tools rather than
     conflicting — the coercion tool is APPENDED alongside caller tools,
     only the json_output block is unwrapped, caller tools still surface
     as tool_calls; a new directive seam reused a prior seam's machinery
     wholesale (evidence: test_coercion_composes_with_caller_tool_call +
     test_json_schema_composes_with_caller_tools green).
   • ADD · open · the streaming unwrap needed THREE coordinated
     touchpoints in one SSE pass (content_block_start marks the coercion
     block, input_json_delta routes by that index to delta.content,
     message_delta overrides finish to "stop") — a per-call state pair
     (coercion_block_index/saw_coercion) bridges them; the same shape
     recurs for any provider that streams a coerced block (evidence:
     test_streaming_coercion_block_streams_as_content green).
   • ADD · open · response_format (request-side native for Gemini,
     tool-coercion for Anthropic, passthrough for OpenRouter) fits the
     one-stateless-stub live harness with no new infra — evidence: 13/13
     ×2, both passes exit 0, port :9925 seed-then-restart resolver
     refresh worked first-try.
   • TDD · open · the live checks served as the red→green suite for the
     coerce+unwrap path (red against a v10-only gateway, green after v11
     tasks 2+3); the _sse_has_tool_calls guard makes the no-leak
     invariant observable, not just asserted-absent — evidence: C1/C2 NO
     tool_calls confirmed on both non-stream and stream.
   • SDD · open · freezing the harness contract (stub surfaces + overlay
     env + check list) let stub/overlay/verify be built independently
     and compose first-try — evidence: C1–C6 passed on the first live
     run after build, no harness rework.

 DECIDE NEXT  consolidate learnings + archive-milestone v11
════════════════════════════════════════════════════════════════════════