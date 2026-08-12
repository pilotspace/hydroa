# TASK: e2e double-pass through the TLS edge: Anthropic + Gemini chat (stream + non-stream) + Gemini embeddings via a single per-provider stub; billing on served id with correct usage; governance 401/402 intact; openrouter byte-identical

slug: provider-breadth-live-verify · created: 2026-06-13 · stage: production · risk: high · autonomy: conservative
phase: done   <!-- specify -> scenarios -> contract -> tests -> build -> verify -> observe -> done -->
<!-- high-risk/method-defining scope? declare `risk: high` on the slug line above and lower
     the autonomy level with `autonomy: conservative` — the engine refuses an unguarded completion
     (`unguarded_high_risk_auto`, run.md guard). A comment is never a declaration. -->

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: prove the v9 provider-breadth milestone LIVE — a tenant calls Anthropic (chat) and
Google Gemini (chat + embeddings) through the OpenAI-compatible /v1 surface, end-to-end through
the Envoy TLS edge (https://localhost:8443), with native translation, billing on the served model
id, governance intact, and the openrouter path byte-identical. Closed by a DOUBLE-PASS (two
consecutive clean runs — the foundation rule). Harness = ONE host stub serving all three provider
wire formats + an additive docker-compose.e2e.v9.yml overlay + a live_v9_verify.py check script;
the harness artifacts are NOT unit-tested (per v6/v7/v8 precedent — their evidence IS the live
double-pass).

Framings weighed:
  - **One per-provider stub serving openrouter + anthropic + gemini wire formats, plain
    (non-group) provider models, gateway-restart to refresh the resolver (chosen)**: a single
    daemon-thread HTTP stub (mirrors v7/v8) routes by path: `/api/v1/chat/completions`
    (openrouter, OpenAI shape), `/v1/messages` (Anthropic), `/v1beta/models/{m}:generateContent|
    :streamGenerateContent|:embedContent|:batchEmbedContents` (Gemini). The overlay points all
    three GATEWAY_*_BASE_URL at it. Catalog models are seeded as PLAIN ids (provider=anthropic/
    google/openrouter) — the v8 router passes plain ids through transparently (confirmed
    fallback_router.py:237), so no model groups needed; the dispatch wrapper resolves provider
    from the catalog. Seed active=true then `docker compose restart gateway --wait` so the
    lifespan resolver.refresh() reads the seeded provider rows (avoids the sync-deactivates-stub
    race).
  - **Two stubs on two ports (rejected)**: more moving parts; one path-routed stub is simpler.
  - **Live keys against real Anthropic/Gemini (rejected)**: never — secrets out of the live
    close; placeholder keys only; the stub validates nothing secret (v7 lesson).

Must:
<must>
  - HARNESS: `scripts/v9_provider_stub.py` — a host HTTP stub on 127.0.0.1:9923 serving:
    * `POST /api/v1/chat/completions` → OpenAI chat.completion echo with usage{prompt_tokens:5,
      completion_tokens:3,total_tokens:8} (openrouter byte-identical check).
    * `POST /v1/messages` → Anthropic message {id,type:message,role:assistant,content:[{type:text,
      text}],stop_reason:end_turn,usage:{input_tokens:7,output_tokens:4}}; when body.stream==true,
      respond with the Anthropic SSE event sequence (message_start…message_stop) as text/event-stream.
    * `POST /v1beta/models/{model}:generateContent` → Gemini {candidates:[{content:{parts:[{text}],
      role:model},finishReason:STOP}],usageMetadata:{promptTokenCount:9,candidatesTokenCount:6,
      totalTokenCount:15}}; `:streamGenerateContent` (alt=sse) → Gemini SSE `data:` chunks;
      `:embedContent` → {embedding:{values:[0.1,0.2,0.3]}}; `:batchEmbedContents` →
      {embeddings:[{values:[...]},...]} one per request, order-preserved.
    * a `/__health` GET for readiness; bind 127.0.0.1 ONLY (never 0.0.0.0).
  - OVERLAY: `infra/docker-compose.e2e.v9.yml` — additive on base+v4+v5+v6; sets
    GATEWAY_ANTHROPIC_API_KEY="stub-anthropic-key",
    GATEWAY_ANTHROPIC_BASE_URL="http://host.docker.internal:9923/v1",
    GATEWAY_GOOGLE_API_KEY="stub-google-key",
    GATEWAY_GOOGLE_BASE_URL="http://host.docker.internal:9923/v1beta",
    GATEWAY_OPENROUTER_BASE_URL="http://host.docker.internal:9923/api/v1",
    GATEWAY_OPENROUTER_API_KEY="stub-openrouter-key" (empty-bearer lesson). No real secret.
  - VERIFY: `scripts/live_v9_verify.py` — starts the stub in a daemon thread, seeds catalog rows +
    pricing via docker-exec psql, restarts the gateway to refresh the resolver, then runs through
    https://localhost:8443 with a fresh run_id (int(time.time())) and a fresh tenant+key:
    * C1 ANTHROPIC CHAT (non-stream): chat the anthropic model → 200 OpenAI chat.completion with
      content from the stub; exactly 1 usage_records row on the SERVED id with prompt_tokens=7,
      completion_tokens=4, cost_usd>0.
    * C2 ANTHROPIC CHAT STREAM: stream=true → OpenAI chat.completion.chunk SSE incl. a terminal
      usage frame; billing row with the streamed usage (7/4).
    * C3 GEMINI CHAT (non-stream): chat the google chat model → 200 OpenAI shape; 1 usage row,
      prompt_tokens=9, completion_tokens=6.
    * C4 GEMINI CHAT STREAM: stream=true → OpenAI chunks + terminal usage frame; billing row.
    * C5 GEMINI EMBEDDINGS: POST /v1/embeddings with input=["a","bb"] on the google embed model →
      200 OpenAI {object:list,data:[…order-preserved…]}; 1 usage row (estimated usage present).
    * C6 GOVERNANCE: chat the anthropic model with NO bearer → 401, 0 usage rows; (budget) a key
      with a tiny budget over-spent → 402 ERR_BUDGET_EXCEEDED.
    * C7 OPENROUTER BYTE-IDENTICAL + TLS: chat an openrouter-provider model → 200 via the stub's
      OpenAI path; 1 usage row; ALL checks through the TLS edge with the CA cert.
  - DOUBLE-PASS: the script is idempotent per run_id; the orchestrator runs it TWICE; both exit 0.
  - SECURITY: placeholder keys only; stub binds 127.0.0.1; no key in logs/URLs; usage rows keyed
    on deployment/model id only (no secrets); never cat/echo any .env.
</must>
Reject:
<reject>
  - any check fails / non-2xx where 2xx expected / wrong usage tokens / missing billing row ->
    the script prints the failing assertion + exits 1 (no silent pass; the milestone stays open).
  - a real provider api key anywhere in the harness/overlay/logs -> HARD-STOP (security).
  - the stub binding 0.0.0.0 -> rejected (127.0.0.1 only).
  - openrouter/openai path behaving differently from v8 -> a regression; fail C7.
</reject>
After:
<after>
  - two consecutive clean runs (double-pass) prove all v9 MILESTONE exit criteria live through the
    TLS edge; the milestone can be closed (milestone-done) + folded to foundation v10.
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ The seed-then-restart-gateway step refreshes the provider_resolver so the seeded provider rows
    resolve correctly — lowest confidence because it depends on the lifespan refresh reading the DB
    AND the seeded rows staying active=true (no source sync deactivating them); if wrong, chat to a
    provider model 404s or dispatch-falls-back to openrouter. Cost: caught immediately on the first
    run (C1 fails loud); mitigation = seed AFTER any sync, restart, then assert provider routing in C1.
  - [ ] host.docker.internal resolves from the gateway container to the host stub (works on Docker
    Desktop/macOS; the v7/v8 overlays already rely on it) — if not, add an extra_hosts mapping.
  - [ ] the Anthropic/Gemini stub SSE shapes match what the adapters parse — they are the SAME
    fixtures the unit suites already pass against; the live run confirms them end-to-end.
</assumptions>

<!-- EXIT: every rule stated, every rejection named; assumptions ranked lowest-confidence first, the top one or two ⚠-flagged with why + cost (or, for trivial scope, an honest "none material" that still names the single biggest risk). -->

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: C1 Anthropic chat non-stream billed on served id
  Given a seeded provider=anthropic chat model + a fresh tenant key, through the TLS edge
  When POST /v1/chat/completions {model:<anthropic>, messages:[...]} with the bearer
  Then 200 OpenAI chat.completion with the stub's content + finish_reason "stop"
  And exactly 1 usage_records row on the served model id with prompt_tokens=7, completion_tokens=4, cost_usd>0

Scenario: C2 Anthropic chat stream
  Given the same anthropic model
  When POST /v1/chat/completions {stream:true}
  Then the response is OpenAI chat.completion.chunk SSE with a terminal usage frame then [DONE]
  And exactly 1 usage_records row with the streamed usage (7/4)

Scenario: C3 Gemini chat non-stream
  Given a seeded provider=google chat model
  When POST /v1/chat/completions {model:<google-chat>, messages:[...]}
  Then 200 OpenAI chat.completion; 1 usage row prompt_tokens=9, completion_tokens=6

Scenario: C4 Gemini chat stream
  Given the google chat model
  When POST /v1/chat/completions {stream:true}
  Then OpenAI chunks + terminal usage frame + [DONE]; 1 usage row

Scenario: C5 Gemini embeddings order-preserved
  Given a seeded provider=google embedding model
  When POST /v1/embeddings {model:<google-embed>, input:["a","bb"]}
  Then 200 OpenAI {object:list, data:[index 0, index 1]} in order; 1 usage row with estimated usage

Scenario: C6 governance intact across providers
  Given the anthropic model
  When POST /v1/chat/completions with NO bearer
  Then 401 and 0 usage_records rows
  And a key whose budget is exhausted returns 402 ERR_BUDGET_EXCEEDED

Scenario: C7 openrouter byte-identical + TLS
  Given a seeded provider=openrouter chat model
  When POST /v1/chat/completions through https://localhost:8443
  Then 200 via the stub's OpenAI path with usage{5,3,8}; 1 usage row
  And every check above traversed the Envoy TLS edge (CA-verified)

Scenario: DOUBLE-PASS close
  Given all C1–C7 pass
  When the script is run a second time with a fresh run_id
  Then it again exits 0 (two consecutive clean passes) — the milestone may close
```

</scenarios>

<!-- EXIT: one scenario per Must AND per Reject; each result is observable. -->

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
HARNESS ARTIFACTS (operator-run; evidence = the live double-pass, NOT unit tests):

scripts/v9_provider_stub.py   (host HTTP stub, 127.0.0.1:9923 ONLY)
  GET  /__health                              -> 200 {"status":"ok"}
  POST /api/v1/chat/completions               -> 200 OpenAI chat.completion echo,
                                                 usage{prompt_tokens:5,completion_tokens:3,total_tokens:8}
  POST /v1/messages         (Anthropic)       -> 200 {id,type:"message",role:"assistant",
                                                 content:[{type:"text",text:"<echo>"}],model,
                                                 stop_reason:"end_turn",usage:{input_tokens:7,output_tokens:4}}
                            (body.stream==true)-> text/event-stream: message_start (usage.input_tokens:7),
                                                 content_block_delta x2 (text_delta), message_delta
                                                 (stop_reason:end_turn, usage.output_tokens:4), message_stop
  POST /v1beta/models/{model}:generateContent -> 200 {candidates:[{content:{parts:[{text}],role:"model"},
                                                 finishReason:"STOP"}],usageMetadata:{promptTokenCount:9,
                                                 candidatesTokenCount:6,totalTokenCount:15}}
  POST /v1beta/models/{model}:streamGenerateContent?alt=sse
                                              -> text/event-stream: data:{candidates:[{content:{parts:
                                                 [{text}]}}]} x2 then data:{candidates:[{...,finishReason:
                                                 "STOP"}],usageMetadata:{9,6,15}}
  POST /v1beta/models/{model}:embedContent    -> 200 {embedding:{values:[0.1,0.2,0.3]}}
  POST /v1beta/models/{model}:batchEmbedContents -> 200 {embeddings:[{values:[...]} per request]} order-preserved

infra/docker-compose.e2e.v9.yml   (additive overlay on base+v4+v5+v6; gateway.environment only):
  GATEWAY_ANTHROPIC_API_KEY:  "stub-anthropic-key"
  GATEWAY_ANTHROPIC_BASE_URL: "http://host.docker.internal:9923/v1"
  GATEWAY_GOOGLE_API_KEY:     "stub-google-key"
  GATEWAY_GOOGLE_BASE_URL:    "http://host.docker.internal:9923/v1beta"
  GATEWAY_OPENROUTER_BASE_URL:"http://host.docker.internal:9923/api/v1"
  GATEWAY_OPENROUTER_API_KEY: "stub-openrouter-key"
  (no real secret; all placeholders. The Anthropic adapter posts to {base}/messages and the Gemini
   adapter to {base}/models/{m}:<verb>, so the base_url suffixes /v1 and /v1beta line up.)

scripts/live_v9_verify.py   (operator-run through https://localhost:8443):
  setup: start stub thread → wait /__health → seed via docker-exec psql into hydroa-e2e-postgres-1:
    INSERT INTO models (id,name,context_length,active,modality,provider,created_at,updated_at) for
      v9-anthropic-chat   provider=anthropic modality=chat
      v9-google-chat      provider=google    modality=chat
      v9-google-embed     provider=google    modality=embedding
      v9-openrouter-chat  provider=openrouter modality=chat
    + matching pricing_snapshots (prompt/completion unit price > 0 so cost_usd>0)
    → docker compose ... restart gateway → --wait  (lifespan resolver.refresh() reads seeded rows)
  checks C1–C7 (see §2). Each asserts HTTP status + body shape + a usage_records SELECT via psql.
  TLS: BASE=https://localhost:8443, CA=infra/envoy/certs/dev-ca.pem. run_id=int(time.time()).
  Envoy global 50 req/s bucket: pace bursty loops (EDGE_PACE_S≈0.05) + settle between sections.
  Exit 0 iff all checks pass; else print the failing assertion + exit 1.

DB read (assertions): SELECT model_id, prompt_tokens, completion_tokens, cost_usd, status FROM
  usage_records WHERE <tenant/key for this run_id>. No secret/key string ever selected or printed.
```

Status: FROZEN @ v1 — approved by Tin Dang (delegated auto mode, 2026-06-13)

Least-sure flag surfaced at freeze: [scenario/contract] the seed-then-restart-gateway step must
land the seeded provider rows in the provider_resolver WHILE keeping them active=true (no source
sync deactivating them) — if the resolver doesn't pick them up, C1 dispatch-falls-back to openrouter
or 404s. Cost: bounded — it fails loud on the FIRST run, before any close. Mitigation pinned in the
flow: seed AFTER any catalog sync, restart the gateway (lifespan refresh reads the DB), and C1 is the
canary. This task is a VERIFICATION harness (no production code changes) — the only "contract" is the
stub/overlay/verify interface above; the milestone's real contracts were frozen in tasks 1–3.
<!-- Approved -> Status: FROZEN @ vN. Changing a frozen contract = change request back to SPECIFY. -->

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: n/a — this is a LIVE e2e verification task. Per the v6/v7/v8 precedent, the harness
artifacts (stub, overlay, verify script) are NOT unit-tested; their evidence is the live double-pass
through the TLS edge. The "red→green" here is: the checks C1–C7 FAIL before tasks 1–3 exist (they
do — provider routing is new) and PASS once the providers are wired (they are) and proven live.
Plan (the verify script IS the executable test; one check per scenario):
<test_plan>
  - C1 anthropic chat non-stream: 200 OpenAI shape + 1 usage row (7/4) on served id, cost>0
  - C2 anthropic chat stream: OpenAI chunk SSE + terminal usage frame + 1 usage row (7/4)
  - C3 gemini chat non-stream: 200 + 1 usage row (9/6)
  - C4 gemini chat stream: OpenAI chunks + terminal usage + 1 usage row
  - C5 gemini embeddings: OpenAI list order-preserved + 1 usage row (estimate present)
  - C6 governance: no-bearer → 401 (0 rows); budget-exhausted → 402
  - C7 openrouter byte-identical + TLS: 200 via stub OpenAI path (5/3/8); all via the TLS edge
  - DOUBLE-PASS: re-run with fresh run_id → exits 0 again
</test_plan>

Tests live in: `scripts/live_v9_verify.py` (the executable check script) · proven RED→GREEN by the
live run, not a pytest suite (harness evidence = the double-pass, per v6/v7/v8).
<!-- declare paths as backticked tokens on this line: `./…` = this task dir ·
     a token with "/" = project root · a bare name = sibling of the previous
     token's dir · a directory counts its *.py files (non-recursive); reports
     mark declared counts with † · anything resolving outside the project root counts 0 -->

<!-- EXIT: one test per scenario; suite red for the RIGHT reason; target recorded. -->

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Safety rule (feature-specific): <e.g. debit+credit in one atomic transaction>
Code lives in: `./src/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.

<!-- EXIT: all green; coverage held; no test/contract touched; no unlisted dependency. -->

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [x] all tests pass — LIVE DOUBLE-PASS: two consecutive clean runs, 35/35 checks each, through the
      Envoy TLS edge (https://localhost:8443). C1 Anthropic chat (7/4 billed on served id, cost>0) ·
      C2 Anthropic stream (terminal usage frame 7/4) · C3 Gemini chat (9/6) · C4 Gemini stream (9/6) ·
      C5 Gemini embeddings (order-preserved, estimated usage, cost>0) · C6 governance (401 + 402, 0
      rows) · C7 openrouter byte-identical (5/3/8). First run passed first-try (no harness iteration).
- [x] coverage did not decrease — n/a (no production source changed); the unit suite was 628 passed /
      82.47% at the gemini-provider close and is unaffected by this harness-only task.
- [x] no test or contract was altered — no apps/gateway/src/** change; only 3 new harness artifacts
      (scripts/v9_provider_stub.py, infra/docker-compose.e2e.v9.yml, scripts/live_v9_verify.py).
- [x] concurrency / timing safe — checks paced under the Envoy global 50 req/s bucket (EDGE_PACE_S
      0.05 + settle); async usage flush handled by polling the usage_records SELECT; gateway restart
      gated on a health poll before checks run.
- [x] no exposed secrets / injection / unexpected deps — PLACEHOLDER keys only (stub-anthropic-key /
      stub-google-key / stub-openrouter-key); stub binds 127.0.0.1 ONLY; no key/header value logged;
      usage_records SELECT reads model_id/tokens/cost/status — never a key/secret column. No new dep.
- [x] layering & dependencies follow CONVENTIONS.md — harness lives in scripts/ + infra/ (outside the
      app); mirrors the v7/v8 harness structure exactly.
- [x] a person reviewed and approved — delegated auto mode (Tin Dang, 2026-06-13); orchestrator
      manually reviewed the seeding/restart/check flow + ran the live double-pass; security clean.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (live) — every v9 production seam exercised end-to-end through the TLS edge: the
      CatalogProviderResolver resolved provider=anthropic/google/openrouter from the seeded catalog
      rows (after the restart refresh); ProviderAwareCompletionUpstream dispatched to each adapter;
      the Anthropic + Gemini translations produced OpenAI-shaped responses + terminal usage frames;
      billing keyed on the served model id with the native token counts. The ⚠ freeze flag
      (seed-then-restart refreshes the resolver) was CONFIRMED — C1 passed first-try, no fallback.
- [x] DEAD-CODE — n/a (harness scripts; all exercised by the run).
- [x] SEMANTIC — orchestrator read the stub + verify flow in full before running.

### GATE RECORD
Outcome: PASS
Evidence: live double-pass 35/35 ×2 through https://localhost:8443 (CA-verified); both runs exit 0
          with fresh run_ids; openrouter/openai byte-identical (C7); governance 401/402 intact (C6);
          billing on served id with native usage for both providers + estimated usage for Gemini
          embeddings. Security: placeholder keys only, stub 127.0.0.1, no secret logged → no HARD-STOP.
Reviewed by: Tin Dang (delegated auto mode) · date: 2026-06-13 · security: clean

<!-- A security finding is ALWAYS HARD-STOP. Record exactly one outcome — no silent pass. -->

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): per-provider chat 5xx/fallback rate; streamed-call usage==0 rate
(SSE drift canary); provider-resolution misses (provider model dispatch-falling-back to openrouter =
a catalog/resolver-refresh gap); Gemini embedding estimated-vs-actual spend skew. The C1–C7 checks
double as production smoke probes (the same shapes a synthetic monitor would assert).
Spec delta for the next loop: v9 is COMPLETE — both providers proven live on both seams. Next parity
slices (post-fold): tool-use/function-calling translation (text+usage first was the v9 scope), AWS
Bedrock + Azure (distinct auth surfaces — SigV4 / Azure deployment URLs), the streaming-latency
hardening (incremental SSE for anthropic+gemini), and exact Gemini-embedding token counting.

### Competency deltas
- [ADD · folded] The v9 milestone closed via the foundation's live-double-pass rule with ZERO harness
  iteration — the seed-then-restart-gateway resolver-refresh mechanism worked first-try (the freeze
  ⚠ flag's mitigation held). Evidence: 35/35 ×2, first run clean. Confirms the freeze-first
  per-provider methodology (dispatch seam frozen first, each provider's translation fixture-grounded
  + verified by its own unit suite, then ONE live double-pass) is the repeatable shape for adding a
  provider.
- [SDD · folded] A single path-routed host stub proved sufficient to exercise three distinct provider
  wire formats (OpenAI/Anthropic/Gemini incl. SSE + embeddings) through the real TLS edge — the
  per-provider e2e harness does not need one stub per provider. Reusable for the next provider slice.
- [DDD · folded] Provider breadth is now end-to-end real: catalog provider ∈ {openrouter,openai,
  anthropic,google} routes chat + (google) embeddings through the OpenAI-compatible surface with
  billing on the served id and governance intact — the v9 glossary delta ("provider as a first-class
  routing dimension on every modality") is fully realized and live-verified.
- [TDD · folded] Carry-forward follow-ups (from tasks 2–3, still open): incremental SSE streaming for
  anthropic+gemini (both buffer today); exact Gemini-embedding token counting (chars/4 estimate). The
  live run did not surface new defects — these remain deliberate, documented scope cuts, not bugs.
