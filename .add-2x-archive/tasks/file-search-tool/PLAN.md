# PLAN: file_search tool in /v1/responses + chat; per_query metering

slug: file-search-tool · created: 2026-07-24 · stage: production
milestone: managed-rag-finetune
autonomy: conservative
risk: high
phase: done
> One file = one task — an ATOMIC node: persist the interface (contract · red suite · scope · verdict); reason everything else in-context, don't write essays. The phase marker above is the single source of truth (`add.py phase`).

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: file_search tool — attach a vector_store_id and the gateway retrieves top-k chunks from the tenant's store, grounds the model answer, and meters ONE per_query unit — in BOTH /v1/responses and /v1/chat/completions.
Framings weighed: shared pre-dispatch grounding seam inside CompletionUseCase.complete()/stream() (chosen — the ONE chokepoint both ingresses already funnel through; mirrors `_strip_web_search_flag`) · retrieval inside `_translate_tools` (rejected — translation is sync/pure; retrieval is async DB+embedding IO belonging in the app layer, and chat has no translate step) · a provider-native file_search passthrough (rejected — providers don't dial the tenant's private store; grounding is gateway-side).

Must:
<must>
  - M1 — RETRIEVE: when the request `tools` array carries `{"type":"file_search","vector_store_ids":[<id>]}`, embed the LAST user message with the STORE's OWN `embedding_model` (VectorStoreRow.embedding_model) via the PER-TENANT breaker (VectorStoreEmbeddingClient.embed), then top-k cosine search over `vector_store_chunks` scoped by `tenant_id` AND `store_id` (new VectorStoreFileRepository.search_chunks over the frozen HNSW cosine index; ZERO DDL), nearest-first, bounded by top-k. TOP-K BOUNDS (Tin-frozen, OpenAI parity): `max_num_results` absent → default 20; provided value is CLAMPED to [1, 50] (values > 50 → 50, values < 1 → 1) — no new error code, the clamp bounds injected-token cost + the prompt-injection surface.
  - M2 — GROUND both surfaces: the retrieved chunk text is injected as grounding context into `messages` BEFORE the upstream dial, via ONE shared seam reached by BOTH /v1/responses and /v1/chat/completions (file_search survives the Responses→chat translation, then both hit CompletionUseCase). The GROUNDED (post-injection) message list is what any stored-response persist receives.
  - M3 — METER once: exactly ONE `per_query` usage record per file_search invocation (quantity=1, NEVER per chunk), priced via the shared pricing_snapshot/rate-card mechanism against a dedicated BILLING-ONLY synthetic catalog model `file_search` (pricing_unit='per_query', non-NULL unit_usd_per_unit — the whisper-1/per_second precedent). "per_query" is added to BOTH recorder `_known_units` literals in lockstep. This fires as a SECOND record beside the LLM per_token record (the per_image precedent). The query-embedding tokens are NOT separately metered (the flat per_query rate absorbs that COGS).
  - M4 — DEFAULT PATH byte-identical: a request with NO file_search tool engages ZERO new plumbing — no retrieval, no embedding call, no per_query record, upstream body unchanged (early-return before any new code).
  - M5 — NO LEAK upstream: after grounding, the `file_search` tool is STRIPPED from the outgoing upstream payload — the provider never receives a hosted tool it cannot service.
  - M6 — ZDR fail-closed at-rest (SECURITY): retrieved chunk text must never persist at-rest for a ZDR tenant. BOTH persist paths — `persist_stored_response` (non-stream) AND `wrap_streaming_persist` (stream), in responses_store/application/persistence.py — re-read ZDR ATOMICALLY inside the SAME transaction as the context_messages insert and fail closed — closing the check-at-entry / persist-after-await TOCTOU window (the twice-HARD-STOPPED zdr-toctou pattern; file_search widens the blast radius to 3rd-party doc chunks).
  - M7 — retrieval+meter run EXACTLY ONCE, before any breaker/dispatch retry loop (a retry must never re-retrieve or double-meter).
</must>
Reject:
<reject>
  - unknown OR cross-tenant vector_store_id (search returns []/store not owned) -> "vector_store_not_found" (404, uniform, zero leak — raised BEFORE any per_query metering)
  - file_search tool present but `vector_store_ids` missing/empty/not-a-list -> "file_search_vector_store_ids_required" (422)
  - query-embedding upstream unavailable (breaker OPEN or timeout after the one idempotent retry) -> "upstream_unavailable" (503) — fail-closed: NO per_query record, NO ungrounded silent answer
  - ZDR tenant on a store-persist path (entry gate OR atomic re-check) -> "zdr_payload_blocked" (403) — existing code + M6 re-check
</reject>
After:
<after>
  - The model answer was grounded on the top-k retrieved chunks; EXACTLY ONE per_query record (quantity=1) was written with cost > $0; the file_search tool did not leak to the provider; a valid search returning 0 chunks STILL wrote one per_query record (parity); a rejected search (404 / 503) wrote NONE; the default (no-file_search) path is byte-identical to today.
</after>
Boundary: the file_search tool shape the tests must speak is `{"type":"file_search", "vector_store_ids":["vs_"+32hex], "max_num_results"?: int}` — the identical object in the /v1/responses `tools` array and the /v1/chat/completions `tools` array (one shape, two ingresses). `max_num_results` absent → 20; present → clamped to [1,50]. No file_search tool in the request = the default path (M4).
<assumptions>
  ⚠ [contract] The M6 ZDR atomic re-check is the least-sure part: the exact mechanism — re-SELECT the tenant's ZDR flag inside the persist insert transaction and abort fail-closed — must sit ATOMIC with the context_messages write, and `persist_stored_response` (responses_store/application/persistence.py) has NO such re-check today. If the re-check is placed non-atomically (or the slow-double test is weak), a ZDR tenant's retrieved 3rd-party chunk text lands at-rest — a security regression. If wrong: silent ZDR breach on the store path (HARD-STOP-class).
  ⚠ [contract] per_query bills against a NEW synthetic `file_search` catalog model. If that model/pricing_snapshot row is not seeded (billing-only), per_query resolves a NULL unit price → $0 silent under-bill. Advisor-confirmed candidate A; residual risk is the seed wiring, not the shape.
</assumptions>

---

## 3 · PLAN — the change plan: ground · contract · build-strategy ▸ docs/05-step-3-plan.md

### Contract (freeze the shape — the HARD, tamper-guarded core; ground it in the REAL code in-context, cite symbols not line numbers)

```
# No NEW HTTP route. file_search rides the EXISTING request bodies of two frozen endpoints:
POST /v1/responses            body: { ..., tools:[{type:"file_search", vector_store_ids:[id], max_num_results?}] }
POST /v1/chat/completions     body: { ..., tools:[{type:"file_search", vector_store_ids:[id], max_num_results?}] }
  200 -> the endpoint's EXISTING success body, answer grounded on retrieved chunks; file_search tool absent from the upstream payload
  404 -> { error: "vector_store_not_found" }                 (unknown / cross-tenant store — raised before metering)
  422 -> { error: "file_search_vector_store_ids_required" }  (missing/empty vector_store_ids)
  503 -> { error: "upstream_unavailable" }                   (query-embedding breaker open / timeout — fail-closed, no bill)
  403 -> { error: "zdr_payload_blocked" }                    (ZDR tenant on store-persist path — entry gate + M6 atomic re-check)

# NEW read method (ZERO DDL — over frozen wave-1/2 chunk schema + HNSW cosine index):
VectorStoreFileRepository.search_chunks(*, tenant_id: uuid, store_id: uuid,
    query_embedding: list[float], top_k: int) -> list[RetrievedChunk]
  ORDER BY embedding <=> :q ASC   WHERE tenant_id=:t AND vector_store_id=:s   LIMIT :top_k
  top_k = clamp(max_num_results or 20, 1, 50)   (Tin-frozen OpenAI-parity bound; caller clamps)
  cross-tenant/unknown store -> []  (router maps [] -> 404 vector_store_not_found; never a leak)

# SHARED grounding seam (app layer — pre-dispatch, both ingresses reach it):
CompletionUseCase.complete()/.stream(): early-return when no file_search tool (M4 byte-identical);
  else retrieve (M1) -> inject grounding into messages (M2) -> strip file_search from body (M5)
  -> ONE per_query record (M3) -> dispatch ONCE (M7). file_search preserved into chat body by
  openai_responses_ingress._translate_tools (currently DROPPED) + removed from _HOSTED_TOOL_TYPES.

Metering (reuse — NO new billing plumbing):
  recorder._known_units += "per_query" (BOTH literals, lockstep); non-token branch prices
  quantity*unit_usd_per_unit*(1+markup). _fire_record_with_raw(model="file_search",
  pricing_unit="per_query", quantity=Decimal(1)). Seed a BILLING-ONLY catalog model
  "file_search" (models row + pricing_snapshot: pricing_unit='per_query', unit_usd_per_unit set;
  NOT in GET /v1/models, NOT chat-dispatchable). Migration chains to head b3d8f21ca9e6.

ZDR (M6): persist_stored_response (responses_store/application/persistence.py) re-reads the
  tenant ZDR flag with a LOCK-TAKING read — `SELECT zdr_enabled FROM tenants WHERE id=:t FOR UPDATE`
  (mirror compliance _is_zdr_locked) — inside the SAME insert txn, so a concurrent flip BLOCKS on the
  row lock and can never land between the re-read and the INSERT commit; ZDR=true -> abort fail-closed,
  zero rows written. (The plain non-locking is_zdr port is INSUFFICIENT here — it leaves a re-read→commit
  TOCTOU window; CR v2.) Streaming path delegates, so the one locked re-check guards both.

Grounder wiring (CR v2 — F1): the FileSearchGrounder is CONSTRUCTED and passed into CompletionUseCase
  at BOTH production build sites — proxy/api/deps.py (HTTP) AND proxy/api/realtime_ws.py — so file_search
  is serviced end-to-end (M2) on the real endpoints and the M5 strip actually runs (an accepted file_search
  is NEVER forwarded raw to the provider). Without this the ingress-accept change is a prod M5 leak regression.

Schema: READS vector_stores + vector_store_chunks (frozen; tenant_id+store_id filter, HNSW cosine).
  WRITES: usage_records via the frozen recorder (one per_query row). Seed-only INSERT into
  models + pricing_snapshots (the synthetic file_search rate). NO new table, NO altered column.
```

Target (measurable): (a) the §4 suite green — top-k retrieval nearest-first + tenant/store scoped ([] cross-tenant), file_search accepted+preserved for both ingresses, per_query priced > $0 at quantity=1 (1×0.0025×1.20 = 0.00300000) with tokens=0, default-path translation byte-identical. (b) EXACTLY ONE per_query record per file_search request (0 on 404/503; 1 on a 0-hit search). (c) M6 ZDR slow-double: a ZDR flip false→true mid-await writes ZERO context_messages rows. (d) The full gateway regression floor (proxy + usage + vector_stores + responses suites) stays green — confirmed by running those suites pre-gate (byte-identical default path can't be shown by a single assert; the untouched-path suites passing is the evidence).
Status: FROZEN @ v1 — approved by Tin
Reported: no

### Build-strategy — Scope (may touch) is HARD scope-lock; the rest is SOFT (the builder self-improves and records actual at verify)
Scope (may touch): `apps/gateway/src/gateway/vector_stores/` `apps/gateway/src/gateway/proxy/infrastructure/openai_responses_ingress.py` `apps/gateway/src/gateway/proxy/application/use_cases.py` `apps/gateway/src/gateway/proxy/api/deps.py` `apps/gateway/src/gateway/proxy/api/realtime_ws.py` `apps/gateway/src/gateway/usage/application/recorder.py` `apps/gateway/src/gateway/responses_store/application/persistence.py` `apps/gateway/src/gateway/catalog/` `apps/gateway/migrations/versions/` `apps/gateway/tests/file_search_tool/`
Regression floor: the gateway suites over the touched paths must stay green — run `tests/vector_store_core/ tests/vector_store_files/ tests/pricing_units/ tests/responses_api_core/ tests/responses_state_store/` (+ a proxy smoke) before the gate; the byte-identical default path (M4) is evidenced by these untouched-path suites passing.
Persona (optional): generic — Principal Backend Engineer, multi-tenant LLM gateway / RAG retrieval / usage metering (no domain persona file under `.add/personas/` fits this billing+retrieval shape).

Least-sure flag surfaced at freeze: [contract] M6 ZDR at-rest fail-closed — the atomic re-check inside `persist_stored_response`'s insert transaction is the part I trust least: it is a SECURITY requirement, the existing persist path has NO ZDR re-check, and a non-atomic placement leaves the twice-HARD-STOPPED check-at-entry/persist-after-await TOCTOU window open for retrieved 3rd-party chunk text. Its §4 slow-double test is authored FIRST in build (the grounding+persist seam it drives does not exist at direction). Runner-up: the billing-only synthetic `file_search` catalog model seed (candidate A, advisor-confirmed).

### AI-verify record (required when gate_mode: ai-plan-verify)
- [ ] §3 PLAN grounding anchors resolve in the current tree
- [ ] §1 every Must + every Reject present, each Reject paired with an error code
- [ ] §3 Contract shape is concrete (no template placeholder text remains)
- [ ] Lowest-confidence flag surfaced and substantive (mirrors unflagged_freeze's own bar)
Verified by: <agent-id> · at: <ISO-8601 UTC timestamp>

---

## 4 · TESTS & SCENARIOS — failing-first suite or acceptance checks (red) ▸ docs/06-step-4-tests.md

<test_plan>
  - test_search_returns_nearest_chunks_top_k_ordered: seed store+chunks A/B, query near A → A ranks first, top_k=1 bounds → 1 result · covers: M1
  - test_search_is_tenant_and_store_scoped_no_leak: tenant A queries tenant B's store id → [] (zero leak) · covers: M1, R:vector_store_not_found
  - test_file_search_tool_is_accepted_not_rejected: validate_responses_request no longer raises ERR_RESPONSES_TOOL_UNSUPPORTED for file_search · covers: M2
  - test_file_search_tool_preserved_into_chat_body: responses_request_to_chat keeps the file_search tool + vector_store_ids in chat body tools (both ingresses then share the seam) · covers: M2
  - test_per_query_priced_via_unit_rate_tokens_zero: recorder prices per_query = 1×unit×(1+markup) via the snapshot, tokens=0, event carries pricing_unit='per_query' + quantity='1' · covers: M3
  - test_per_query_single_bill_one_record_per_search: one file_search invocation → exactly ONE per_query event, quantity=1 (never per chunk) · covers: M3
  - test_default_path_no_file_search_translation_unchanged: no file_search → chat translation byte-identical (green regression pin, guards M4)
  - [BUILD-AUTHORED, gated before M5/M6 pass — the seam they drive does not exist at direction]
    test_file_search_stripped_from_upstream_payload: grounded request → outgoing provider body has no file_search tool · covers: M5
    test_zdr_atomic_recheck_slow_double_fail_closed: SLOW ZDR double flips false→true mid-await → persist_stored_response writes ZERO context_messages rows · covers: M6 (SECURITY)
    test_embedding_failure_fails_closed_no_bill: breaker-open/timeout → 503, ZERO per_query record · covers: R:upstream_unavailable, M7
    test_missing_vector_store_ids_rejected: file_search with empty vector_store_ids → 422 · covers: R:file_search_vector_store_ids_required
    test_zero_hit_search_still_meters_one: valid store, 0 chunks matched → still ONE per_query record · covers: M3 (parity edge)
</test_plan>

Rigor: the 7 tests above the divider are WRITTEN and RUN RED now (the gated direction floor — one red per PRIMARY Must/Reject reachable without the wired seam). The 5 below the divider are authored FIRST thing in build because the grounding+persist seam they exercise does not exist yet — M6 (ZDR security) leads that list and is the security gate. Edge cases swept: 0-hit-still-meters (parity), 404-before-meter, cross-tenant no-leak, embedding-failure fail-closed, retry-double-meter (M7). Prompt-injection surface is bounded by the top-k max (contract).

Tests live in: `apps/gateway/tests/file_search_tool/` · MUST run red (missing implementation) before Build.

### RED evidence (run 2026-07-25, MAIN tree, DB gateway_test_file_search_tool)
Command: `GATEWAY_TEST_DATABASE_URL=…/gateway_test_file_search_tool uv run pytest tests/file_search_tool/ --override-ini="addopts=" -q`
Result: **6 failed, 1 passed** — every gated red fails for the RIGHT reason (missing impl), not import/harness:
- `test_search_returns_nearest_chunks_top_k_ordered` → `AttributeError: 'VectorStoreFileRepository' object has no attribute 'search_chunks'` (M1 method absent)
- `test_search_is_tenant_and_store_scoped_no_leak` → same AttributeError (M1)
- `test_file_search_tool_is_accepted_not_rejected` → fails: validate_responses_request raises ERR_RESPONSES_TOOL_UNSUPPORTED (file_search still in _HOSTED_TOOL_TYPES) (M2)
- `test_file_search_tool_preserved_into_chat_body` → fails: chat body has no file_search tool (_translate_tools drops it) (M2)
- `test_per_query_priced_via_unit_rate_tokens_zero` → fails: recorder emitted `cost_usd='0.0'`, `pricing_unit='per_token'`, `quantity=''` — per_query fell through to the per_token branch (not in _known_units) (M3)
- `test_per_query_single_bill_one_record_per_search` → fails: 0 per_query events (same fallthrough) (M3)
- `test_default_path_no_file_search_translation_unchanged` → **PASSED** (byte-identical default-path regression pin; green today by design, guards M4)

---

## 5 · BUILD — AI writes the code (execution) ▸ docs/07-step-5-build.md

Strategy actually used: <fill at VERIFY — what you ACTUALLY did (or "as planned"); harvested into §7 Decisions (ADR)>
Code lives in: `src/`
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
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-25

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

### Decisions (ADR)
- [AI] specify — chose shared pre-dispatch grounding seam inside CompletionUseCase.complete()/stream(); rejected retrieval inside `_translate_tools` (rejected — translation is sync/pure; retrieval is async DB+embedding IO belonging in the app layer, and chat has no translate step) · a provider-native file_search passthrough (rejected — providers don't dial the tenant's private store; grounding is gateway-side).
- [human] freeze — froze §3 @ v1 (approved by Tin)
- [AI] build — strategy used: as planned
- [human] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
One line per forward change, tagged `[SPEC · open|seeded|dropped]` + evidence — each re-enters at Specify (`deltas.md`).

### Competency deltas
One lesson per line: `[DDD|SDD|UDD|TDD|ADD · open] the learning (evidence: …)` — see `deltas.md`.
