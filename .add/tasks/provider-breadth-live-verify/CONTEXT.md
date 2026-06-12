# Shared context — provider-breadth-live-verify (v9 task 4/4) — HARNESS BUILD

Frozen spec: `.add/tasks/provider-breadth-live-verify/TASK.md` (§1–§4; §3 FROZEN @ v1).
Read it FIRST and in full. Do NOT edit it. This task writes a LIVE e2e harness; there are NO unit
tests (evidence = the live double-pass). The orchestrator runs the stack + double-pass after you build.

## Goal of this BUILD
Produce three artifacts that mirror the EXISTING v7/v8 harness patterns exactly:
1. `scripts/v9_provider_stub.py` — one host HTTP stub (127.0.0.1:9923) serving openrouter +
   Anthropic + Gemini wire formats (incl. SSE) per §3.
2. `infra/docker-compose.e2e.v9.yml` — additive overlay (gateway.environment only) per §3.
3. `scripts/live_v9_verify.py` — the C1–C7 check script through the TLS edge per §2/§3.
Do NOT touch any production source under apps/gateway/src. Do NOT git commit. Do NOT edit TASK.md.

## Mirror these EXISTING files (read them — copy their structure/idioms verbatim)
- `scripts/v8_router_stub.py` — http.server ThreadingHTTPServer stub on 127.0.0.1; do_POST/do_GET
  routing; make_stub_server / start_stub_in_thread; JSON body read; how it returns JSON + SSE bytes.
  COPY this structure. Bind 127.0.0.1 ONLY (never 0.0.0.0).
- `scripts/live_v8_verify.py` — the check-script skeleton: BASE/CA constants, run_id=int(time.time()),
  RESULTS list + record()/print summary + exit code, the `psql(sql)` docker-exec helper
  (PG_CONTAINER="hydroa-e2e-postgres-1"), the urllib HTTPS helper with the CA cert, EDGE_PACE_S /
  EDGE_SETTLE_S pacing for the Envoy 50 req/s bucket, GW_CONTAINER restart usage. COPY these.
- `scripts/live_v7_verify.py` — the PROVIDER-MODEL seeding precedent: read its `_seed_*` /
  INSERT INTO models (id,name,context_length,active,modality,provider,created_at,updated_at) +
  pricing_snapshots, AND its embeddings check (POST /v1/embeddings + usage_records assertion),
  AND its tenant+key creation flow (admin signup → create key → bearer). COPY these idioms.
- `infra/docker-compose.e2e.v7.yml` and `.v8.yml` — overlay shape (services: gateway: environment:),
  the host.docker.internal base_url convention, the GATEWAY_OPENROUTER_API_KEY placeholder note.
- `apps/gateway/src/gateway/proxy/infrastructure/anthropic_upstream.py` +
  `gemini_upstream.py` — the EXACT request the gateway will POST to your stub (so the stub's
  expected paths/bodies match): Anthropic → POST {base}/messages; Gemini chat → POST
  {base}/models/{model}:generateContent (+ :streamGenerateContent?alt=sse); Gemini embed → POST
  {base}/models/{model}:embedContent | :batchEmbedContents. The stub must accept exactly these.
  Also read `apps/gateway/tests/anthropic_provider/test_anthropic_provider.py` (the _ANTHROPIC_SSE
  fixture) and `tests/gemini_provider/test_gemini_provider.py` (_GEMINI_SSE) — the stub's SSE bytes
  MUST match those shapes (the adapters already parse them).

## Stub details (scripts/v9_provider_stub.py)
- ThreadingHTTPServer on 127.0.0.1:9923. do_GET handles `/__health` → 200 {"status":"ok"}.
- do_POST routes by self.path:
  - `/api/v1/chat/completions` → 200 OpenAI chat.completion; echo model from body; usage
    {prompt_tokens:5,completion_tokens:3,total_tokens:8}; choices[0].message.content="ok-openrouter".
  - `/v1/messages` → if body.get("stream") → 200 text/event-stream with the Anthropic event seq:
    message_start (message.usage.input_tokens:7), content_block_start, content_block_delta×2
    (text_delta "Hello"/" world"), content_block_stop, message_delta (stop_reason:end_turn,
    usage.output_tokens:4), message_stop. Else → 200 {id:"msg_stub",type:"message",role:"assistant",
    model:<echo>,content:[{type:"text",text:"Hello world"}],stop_reason:"end_turn",stop_sequence:null,
    usage:{input_tokens:7,output_tokens:4}}.
  - path matches `/v1beta/models/<model>:generateContent` → 200 {candidates:[{content:{parts:
    [{text:"Hello gemini"}],role:"model"},finishReason:"STOP",index:0}],usageMetadata:
    {promptTokenCount:9,candidatesTokenCount:6,totalTokenCount:15}}.
  - `:streamGenerateContent` → 200 text/event-stream: data:{candidates:[{content:{parts:[{text:
    "Hello"}],role:"model"}}]}\n\n , data:{candidates:[{content:{parts:[{text:" gemini"}],role:
    "model"}}]}\n\n , data:{candidates:[{content:{parts:[{text:""}],role:"model"},finishReason:
    "STOP"}],usageMetadata:{promptTokenCount:9,candidatesTokenCount:6,totalTokenCount:15}}\n\n
  - `:embedContent` → 200 {embedding:{values:[0.1,0.2,0.3]}}.
  - `:batchEmbedContents` → read body["requests"]; return {embeddings:[{values:[0.1,0.2,0.3]} for
    each request]} (one per request, order-preserved).
- Parse the `:verb` and model from the path (path is /v1beta/models/<model>:<verb>; model may
  contain no slash for the stub's seeded ids). Never log/echo any header value (keys).

## Overlay (infra/docker-compose.e2e.v9.yml)
Exactly the §3 env block under services: gateway: environment:. Header comment must state it composes
ADDITIVELY on base+v4+v5+v6 and list the full `-f` chain. Placeholders only — no real secret.

## Verify script (scripts/live_v9_verify.py) — flow
1. start_stub_in_thread(); poll http://127.0.0.1:9923/__health until 200 (ceiling ~10s).
2. SEED (psql via docker exec hydroa-e2e-postgres-1): INSERT the 4 models + pricing_snapshots
   (v9-anthropic-chat/anthropic/chat, v9-google-chat/google/chat, v9-google-embed/google/embedding,
   v9-openrouter-chat/openrouter/chat), all active=true, prices>0. Use INSERT ... ON CONFLICT DO
   UPDATE so re-runs are idempotent.
3. RESTART gateway so the lifespan provider_resolver.refresh() reads the seeded rows:
   `docker compose -f infra/docker-compose.e2e.yml -f ...v4 -f ...v5 -f ...v6 -f ...v9 restart gateway`
   then poll GET https://localhost:8443 readiness (/internal/health/ready via the edge, or the
   gateway /healthz) until ready (ceiling ~30s). (The seeded rows are active=true and NOT re-synced,
   so they survive the restart.)
4. Create a fresh tenant + key (mirror live_v7/_v8 admin signup + key create); get the bearer.
5. Run C1–C7 (see §2). For each: HTTP through https://localhost:8443 with the CA cert; assert
   status + body; then psql SELECT prompt_tokens/completion_tokens/cost_usd/status FROM
   usage_records for this run's key to assert the billing row. Pace bursty calls (EDGE_PACE_S) and
   settle (EDGE_SETTLE_S) to stay under the Envoy 50 req/s bucket. Usage flushing may be async —
   poll the usage_records SELECT with a short ceiling (mirror how v8 waits for the billing row).
6. Print a PASS/FAIL summary; exit 0 iff all pass.
Idempotent per run_id=int(time.time()); the orchestrator runs it twice.

## NON-NEGOTIABLE
- Stub binds 127.0.0.1 only. Placeholder keys only — NEVER a real provider key anywhere. Never
  log/echo/print a header value or any .env. usage_records SELECT must not select key/secret columns.
- Do NOT modify apps/gateway/src/**. Do NOT git commit. Do NOT edit TASK.md.
- ruff-clean the three new files (`cd apps/gateway && uv run ruff check ../../scripts/v9_provider_stub.py
  ../../scripts/live_v9_verify.py` — match how v8 scripts pass ruff; if scripts/ is outside ruff scope,
  at least keep them import-clean and runnable with `uv run --project apps/gateway python`).

## Deliverables
- The 3 files. Report: the exact `docker compose ... up --build -d --wait` command chain (base+v4+v5+
  v6+v9) and the `uv run --project apps/gateway python scripts/live_v9_verify.py` invocation the
  orchestrator will run. Do NOT bring up docker or run the verify yourself — just build the artifacts
  and report the run commands + any assumptions. Confidence scores (6 dims).
