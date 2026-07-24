# PLAN: Stateless /v1/responses wire on the chat seam

slug: responses-api-core · created: 2026-07-24 · stage: production
milestone: api-surface-parity
autonomy: auto   <!-- manual<conservative<auto — lower for high-risk (`add.py autonomy set`); a `component: <name>` line joins that root to §3 Scope; task edges: `--depends-on`/`--extends`/`--relates-to`; high-risk/method-defining? declare `risk: high` on the slug line; headless agent-crossed freeze? declare `gate_mode: ai-plan-verify` here (human floor: security|data|architecture never AI-frozen) -->
phase: build   <!-- direction→build→verify→done; direction drafts §1–§4 (rules · change plan · red suite) to the ONE freeze -->
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: POST /v1/responses — the OpenAI Responses wire (non-stream + SSE stream), stateless, translated onto the EXISTING chat-completions seam; the milestone's freeze-first shared contract (wave-2 responses-state-store and every SDK consumer build against this wire).
Framings weighed: boundary-translation router-file onto CompletionUseCase unchanged (chosen — `messages_router.py` precedent for router SHAPE; deliberately the OPPOSITE of it on errors — no envelope translation, /v1/responses keeps chat's problem+json posture since both are the OpenAI dialect family; zero new inference/billing path, byte-identical default path structurally guaranteed) · new ResponsesUseCase beside CompletionUseCase (rejected: duplicates governance/billing chokepoint — the class of bug the milestone Ground forbids) · upstream-native /v1/responses passthrough to providers that speak it (rejected: fractures billing/usage extraction per provider; the seam bills on chat-shape frames).
Must:
<must>
  - M1 non-stream: POST /v1/responses `{model, input}` → 200 Response object: `id` `resp_`-prefixed, `object:"response"`, `status:"completed"`, `output` = one `message` item (`type:"message"`, `id` `msg_`-prefixed, `role:"assistant"`, `content:[{type:"output_text", text, annotations:[]}]`), `store:false` echoed, `model` = served model — produced by exactly ONE `CompletionUseCase.complete()` round-trip, governance/cache/billing chokepoint UNCHANGED.
  - M2 request translation (Responses → internal chat shape): `input` string → one user message; `input` item list → messages (`input_text`/string content); `instructions` → prepended system message; `max_output_tokens` → `max_tokens`; flattened `tools[{type:"function",name,description,parameters,strict}]` → chat-nested `function` tools; `tool_choice {type:"function",name}` → chat nested form; `text.format {type:"json_object"|"json_schema",…}` → `response_format`; `function_call` + `function_call_output` input items → assistant `tool_calls` turn + `role:"tool"` message (call_id preserved).
  - M3 output translation (chat → Responses): assistant `tool_calls` → `function_call` output items (`call_id` preserved, `name`, `arguments` JSON string, `id` `fc_`-prefixed); `finish_reason:"length"` → `status:"incomplete"` + `incomplete_details:{reason:"max_output_tokens"}`.
  - M4 usage + billing: response `usage` = `{input_tokens, output_tokens, total_tokens, input_tokens_details:{cached_tokens}, output_tokens_details:{reasoning_tokens}}` mapped from the chat frame (absent details default 0); exactly ONE usage_records write per served request via the EXISTING recorder inside the use case, billed on the SERVED model — the translator never touches billing.
  - M5 streaming: `stream:true` → `text/event-stream` of named-event frames (`event: <type>` + `data: {...}`), each carrying a monotonically increasing `sequence_number` from 0; at minimum `response.created` → `response.in_progress` → `response.output_item.added` → `response.content_part.added` → `response.output_text.delta`* → `response.output_text.done` → `response.content_part.done` → `response.output_item.done` → terminal `response.completed` whose `response` carries the full final Response incl. usage; NO `data: [DONE]` sentinel; exactly one usage_records write (recorded inside `use_case.stream()`, unchanged).
  - M6 honest stream failure: an upstream mid-stream error frame → terminal `response.failed` event whose `response.status:"failed"` carries `error:{code,message}`; the stream ends; no fabricated completion.
  - M7 governance parity: every gateway-generated rejection (authn, model, budget, rate-limit, credit, tier, residency, guardrail) is the IDENTICAL ProblemError → problem+json posture `/v1/chat/completions` produces — same status, same `code`, same opacity (uniform 401/404, sso-oracle lesson); upstream 4xx/5xx bodies pass through verbatim (both dialects share the OpenAI error envelope).
  - M8 byte-identical default path: the chat path is untouched except one `include_router` line in main.py; a request not using /v1/responses engages ZERO new plumbing (regression floor: existing chat suite green).
</must>
Reject:
<reject>
  - `background: true` -> "ERR_RESPONSES_BACKGROUND_UNSUPPORTED" (400; upstream never dialed, no usage row)
  - any hosted/built-in tool type (`web_search`, `web_search_preview`, `file_search`, `computer_use_preview`, `code_interpreter`, `image_generation`, `mcp`, `local_shell`) -> "ERR_RESPONSES_TOOL_UNSUPPORTED" (400; upstream never dialed, no usage row)
  - `store: true` OR any `previous_response_id` -> "ERR_RESPONSES_STORE_UNSUPPORTED" (400; the wave-2 extension point — see Contract "State extension point")
  - non-JSON body · non-object body · missing/empty `input` (empty string or empty array) · unknown input-item type -> "ERR_PAYLOAD_INVALID" (400; runs to completion BEFORE any governance call — malformed body never partially consumes a hold, messages_router safety rule)
</reject>
After:
<after>
  - The SDK caller holds a valid Response (or contracted error); exactly one usage_records row exists per served request, billed on the served model; NOTHING was persisted about the response itself (stateless — no new table, no payload at rest, ZDR/retention/residency untouched by construction).
</after>
Boundary: `input` as bare string vs item array (both tested) · message `content` as string vs typed-part list · tool `arguments` as JSON string (identical encoding both dialects, asserted not assumed) · SSE frame = `event:` line + `data:` line (vs chat's data-only frames — parser in tests speaks both).
<assumptions>
  ⚠ [contract] The emitted SSE event SUBSET (M5) satisfies the official `openai` SDK's streaming iterator — vocabulary verified against live OpenAI docs today (developers.openai.com streaming-events) but SDK tolerance of omitted event types (e.g. hosted-tool events we never emit) is [ASSUMED] until the milestone's live-SDK smoke; if wrong: rework the stream translator only — additive events don't move this contract.
  ⚠ `store` defaults FALSE here (OpenAI's server default is true) — a deliberate stateless-core divergence, echoed honestly in the body; if wrong for SDK apps that assume implicit storage: wave-2 flips the default under ITS contract; the accepted field shape (this freeze) is unchanged.
  - usage detail fields absent from a provider frame default 0 (not omitted) — [DERIVED] from SDK typed models requiring the keys; cost if wrong: cosmetic, additive fix.
</assumptions>

<!-- §2 (the old standalone SCENARIOS section) was RETIRED — pass/fail cases now live with the tests in §4 · TESTS & SCENARIOS. The §3–§7 numbers are unchanged so the freeze parser and every §-reference keep working; the jump from §1 to §3 is intentional. -->

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Contract (freeze the shape — the HARD, tamper-guarded core; ground it in the REAL code in-context, cite symbols not line numbers)

```
POST /v1/responses   body: { model, input: str|[item], instructions?, max_output_tokens?,
                             temperature?, top_p?, stream?, tools?[flattened], tool_choice?,
                             text?: {format}, metadata?, user?, parallel_tool_calls?,
                             store?(=false), previous_response_id?(rejected), background?(rejected) }
  200 -> Response { id:"resp_…", object:"response", created_at, status:"completed"|"incomplete"|"failed",
                    model, output:[ {type:"message",id:"msg_…",role:"assistant",status,
                                     content:[{type:"output_text",text,annotations:[]}]}
                                  | {type:"function_call",id:"fc_…",call_id,name,arguments,status} ],
                    usage:{input_tokens,output_tokens,total_tokens,
                           input_tokens_details:{cached_tokens},output_tokens_details:{reasoning_tokens}},
                    store:false, previous_response_id:null, incomplete_details, error,
                    instructions, metadata, temperature, top_p, tools, tool_choice, text,
                    parallel_tool_calls, truncation:"disabled" }
  200 (stream:true) -> text/event-stream: `event:`-named frames, sequence_number monotonic,
                    terminal `response.completed` (or `response.failed`) carrying the full Response;
                    NO `data: [DONE]`.
  400 -> problem+json { code: "ERR_PAYLOAD_INVALID" | "ERR_RESPONSES_BACKGROUND_UNSUPPORTED"
                      | "ERR_RESPONSES_TOOL_UNSUPPORTED" | "ERR_RESPONSES_STORE_UNSUPPORTED" }
  401/402/403/404/422/429 -> the UNCHANGED chat-seam ProblemError catalog, problem+json, byte-identical
                    posture to /v1/chat/completions (no wire-local error envelope — OpenAI-dialect family).
Schema: NO new tables, NO payload at rest. Reads/writes: usage_records via the existing recorder
        inside CompletionUseCase only. batch_processor is NEVER passed (a /v1/responses request
        never batch-diverts). Envoy: covered by the existing `/v1/` ext_authz route (envoy.yaml
        "M3: /v1/* → ext_authz enabled") — zero infra change.
State extension point (wave-2, contracted NOW so no re-freeze): `store` (bool, default false) and
        `previous_response_id` (str) are ACCEPTED-SHAPE fields of this wire; this task terminates
        both with "ERR_RESPONSES_STORE_UNSUPPORTED". Ownership of their ACCEPTANCE (persistence,
        chaining, GET/DELETE /v1/responses/{id}, cross-tenant 404) transfers to responses-state-store,
        which flips behavior under ITS OWN frozen contract; the field names, types, defaults, and the
        Response echo fields (`store`, `previous_response_id`) frozen here are what it attaches to.
```

Anchors (the Contract may cite ONLY these — all [OBSERVED] this session):
`gateway/proxy/api/router.py::completions` · `gateway/proxy/api/messages_router.py::messages` (+ `_translate_request_body` safety rule) · `gateway/proxy/application/use_cases.py::CompletionUseCase.complete/.stream` · `gateway/proxy/api/deps.py::get_completion_use_case/get_completion_upstream/get_usage_recorder/get_raw_key_ingress` · `gateway/core/errors.py::on_problem` (ProblemError → problem+json handler) · `gateway/core/error_catalog.py` (ERR_* registry; new ERR_RESPONSES_* land beside it) · `gateway/proxy/application/json_sanitize.py::sanitize_non_finite` · `gateway/proxy/domain/ports.py::BatchDivertedStream` (fail-closed guard, mirrors messages_router) · `gateway/main.py::include_router(messages_router)` (registration point) · `infra/envoy/envoy.yaml` `/v1/` ext_authz route.

Target (measurable): all §4 tests green (17 red today); exactly 1 usage_records write per served request asserted in-suite (non-stream + stream); 0 writes on every reject path; regression floor (existing chat + anthropic-ingress suites) stays green; `make ci` Pyright strict clean. Live-SDK stream smoke is the milestone-level release step (can't be shown by this suite; confirmed at Release steps).
Status: FROZEN @ v1 — approved by Tin Dang
Reported: <yes — the freeze report (banner/ARC/SHAPE) rendered before this froze | no>

### Build-strategy — Scope (may touch) is HARD scope-lock; the rest is SOFT (the builder self-improves and records actual at verify)
Scope (may touch): `apps/gateway/src/gateway/proxy/api/responses_router.py` · `apps/gateway/src/gateway/proxy/infrastructure/openai_responses_ingress.py` · `apps/gateway/src/gateway/main.py` · `apps/gateway/src/gateway/core/error_catalog.py` · `apps/gateway/tests/responses_api_core/`
Regression floor: `apps/gateway/tests/proxy/` (chat seam) + `apps/gateway/tests/anthropic_messages_ingress/` (sibling ingress precedent) — run before the gate.
Persona (optional): `protocol-translation-engineer` (byte-identical passthrough floor · billing on served model with native usage frame · exactly one terminal usage-carrying frame · per-dialect shape differences named, never assumed).

Strategy (SOFT, ordered): 1) `openai_responses_ingress.py` — pure translation functions (request→internal, internal→Response, chat-SSE→Responses-SSE stepper), mirroring `anthropic_ingress.py`'s module shape; 2) `responses_router.py` — validate/reject BEFORE governance (messages_router `_translate_request_body` safety rule), then delegate to `CompletionUseCase.complete/.stream` unchanged; 3) register in `main.py`; 4) drive §4 green; translation module stays IO-free (no outbound IO added ⇒ no new timeout/retry/breaker surface — the dial stays inside the existing breaker-wrapped upstream).

Least-sure flag surfaced at freeze: [contract] the M5 SSE event-sequence subset — event names + sequence_number + no-[DONE] are doc-verified today, but official-SDK tolerance of the emitted SUBSET is assumed until the live-SDK release smoke; if wrong the stream translator reworks additively without moving this contract.

### AI-verify record (required when gate_mode: ai-plan-verify)
- [ ] §3 PLAN grounding anchors resolve in the current tree
- [ ] §1 every Must + every Reject present, each Reject paired with an error code
- [ ] §3 Contract shape is concrete (no template placeholder text remains)
- [ ] Lowest-confidence flag surfaced and substantive (mirrors unflagged_freeze's own bar)
Verified by: <agent-id> · at: <ISO-8601 UTC timestamp>

<!-- The freeze IS the one approval, led by the bundle's lowest-confidence flag — Contract + Scope (may touch) = HARD (tamper-guarded); Strategy · Regression floor · Persona = SOFT/optional. Approved -> Status: FROZEN @ vN — approved by <name>; changing a frozen Contract = change request back to SPECIFY. Scope tokens, backticked: `./…` = this task dir · a "/" token = project root · a bare name = sibling of the previous token's dir · a directory covers its whole subtree · outside-root drops fail-closed · absent line = UNDECLARED (grandfathered, never retro-red). -->

---

## 4 · TESTS & SCENARIOS — failing-first suite or acceptance checks (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_non_stream_basic_response: string input → 200 Response shape (resp_/msg_ ids, output_text, store:false echo) via one upstream call · covers: M1
  - test_non_stream_bills_exactly_one_usage_record: recorder sees 1 row, served model, status 200 · covers: M4
  - test_input_items_and_instructions_translate: instructions+item list → system+user chat messages; max_output_tokens→max_tokens · covers: M2
  - test_flattened_tools_translate_and_tool_call_returns: flattened tool → chat-nested in upstream payload; upstream tool_calls → function_call output item, call_id preserved · covers: M2, M3
  - test_function_call_round_trip_input_items: function_call + function_call_output items → assistant tool_calls + role:tool messages · covers: M2
  - test_text_format_json_schema_maps_to_response_format: text.format json_schema → chat response_format · covers: M2
  - test_usage_mapping_names_and_details: prompt/completion→input/output token names, details default 0 · covers: M4
  - test_length_finish_reason_maps_to_incomplete: finish_reason length → status incomplete + incomplete_details.reason=max_output_tokens · covers: M3
  - test_stream_event_sequence_and_terminal_completed: named events in order, monotonic sequence_number, terminal response.completed w/ full usage, NO [DONE] · covers: M5
  - test_stream_bills_exactly_one_usage_record: 1 recorder row after stream drain · covers: M4, M5
  - test_mid_stream_error_emits_response_failed: error frame → terminal response.failed, stream ends · covers: M6
  - test_reject_background_mode: 400 ERR_RESPONSES_BACKGROUND_UNSUPPORTED; upstream.calls==0; 0 usage rows · covers: R:ERR_RESPONSES_BACKGROUND_UNSUPPORTED
  - test_reject_hosted_tool_types: web_search + file_search each 400 ERR_RESPONSES_TOOL_UNSUPPORTED; upstream.calls==0; 0 usage rows · covers: R:ERR_RESPONSES_TOOL_UNSUPPORTED
  - test_reject_store_true_and_previous_response_id: each → 400 ERR_RESPONSES_STORE_UNSUPPORTED; upstream.calls==0; 0 usage rows (wave-2 extension point stays terminal here) · covers: R:ERR_RESPONSES_STORE_UNSUPPORTED
  - test_reject_malformed_and_empty_input: non-JSON body · empty input list · unknown item type → 400 ERR_PAYLOAD_INVALID; upstream.calls==0; 0 usage rows · covers: R:ERR_PAYLOAD_INVALID
  - test_governance_parity_unauthenticated_matches_chat: no credential → same status+code problem+json as /v1/chat/completions (compared live, not hardcoded) · covers: M7
  - test_default_path_untouched_chat_still_serves: /v1/chat/completions request through the same app serves byte-identically to its fixture expectation (persona floor) · covers: M8
</test_plan>

Prose build-guidance (not gated): `input_image` parts translate to chat `image_url` parts (secondary modality — build it, no red test gates it) · `tool_choice` STRING forms (`"auto"`/`"none"`/`"required"`) pass through unchanged — identical shape in both dialects (advisor finding: state it so no builder special-cases only the object form) · `reasoning` is accepted-and-ignored (reasoning-model SDK callers routinely send it; ignoring is a decision, not an accident) · `include` values are accepted-and-ignored (additive output enrichment for surfaces we don't host) · `truncation` echoes "disabled" · `metadata`/`user` echoed verbatim · `finish_reason:"content_filter"` → status incomplete reason "content_filter" · non-finite floats ride the existing `sanitize_non_finite` chokepoint.

Rigor: one red test per §1 Must/Reject — the PRIMARY cases + primary edge cases — is the gated floor. Minor/secondary behaviors are DESCRIBED in prose below as build-guidance — no `covers:` tag, no red test, not gated. Add a Given/When/Then line inline ONLY when a human stakeholder needs a readable case — never as ceremony; the test_plan is the canonical encoding of every scenario.

Tests live in: `apps/gateway/tests/responses_api_core/` · MUST run red (missing implementation) before Build.
RED evidence (2026-07-24): `GATEWAY_TEST_DATABASE_URL=…responses_api_core uv run pytest tests/responses_api_core/ -q` → **16 failed, 1 passed in 18.89s** — every /v1/responses test fails receiving **404 Not Found** (`{"detail":"Not Found"}`; route absent, `main.py` registers no responses_router) — red for the RIGHT reason (missing implementation; harness healthy: signup/key/model fixtures all succeed before the 404). The 1 pass is `test_default_path_untouched_chat_still_serves` (M8): it exercises the EXISTING chat path, green-by-design pre-change — it is the byte-identical-passthrough BASELINE the build must keep green, not a vacuous test.
<!-- declare paths as backticked tokens on this line: `./…` = this task dir · a token with "/" = the project root · a bare name = a sibling of the previous token's dir · a directory counts its *.py files (non-recursive) · declared counts marked † · outside the project root counts 0. The test_plan bullets' `covers:` tails are machine-read too: `add.py locate path::test_name` resolves a failing test to the frozen §3 clause it proves -->
<!-- NON-CODING task (kind: docs · release · infra, or a non-coding project)? §4 is a failing-first ACCEPTANCE CHECK, not a script — verifiable pass/fail evidence (mkdocs build succeeds · §X covers A/B/C · every internal link resolves), red before the artifact exists and green after. Set `Tests live in: evidence` (no `./tests/`). The red→green discipline holds; only the must-be-executable-code requirement is lifted. -->

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: <fill at VERIFY — what you ACTUALLY did (or "as planned"); harvested into §7 Decisions (ADR)>
Code lives in: `apps/gateway/src/gateway/proxy/`
Spawn (multi-agent): build/verify subagent spawns default `isolation: worktree`; cross-agent advisor — spawn `add-advisor` (an agent OTHER than the builder) for the freeze `--cross` and the §6 refute-read; `self` only when solo.
Constraints: do NOT change any test or the frozen §3 contract; stay inside §3 Scope (an out-of-scope build fails the gate: scope_violation); keep the §3 Regression floor green; allow-list packages only; ask if unclear.

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests (or §4 acceptance checks) pass — including the §3 Regression floor (host suite)
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
Verdict: <EARNED | NOT-EARNED>
By: <self | agent-id> · adversarially checked: <what was probed>

### GATE RECORD
Reported: <yes — the gate report (banner/ARC) rendered before this outcome recorded | no>
Outcome: <PASS | RISK-ACCEPTED | HARD-STOP>
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: <name> · date: <date>

<!-- Security is ALWAYS HARD-STOP; record exactly one outcome — no silent pass. The Refute-read verdict is recorded, never engine-blocked; a human spot-audit backstops anything unrecorded. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
<harvested at done from §1/§3/§5/§6 — do not hand-edit; one actor-tagged line per decision, refilled only while this placeholder stands>

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
