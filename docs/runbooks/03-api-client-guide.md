# 03 — API client guide

How an app or script calls LLMs through Hydroa. The proxy is **OpenAI-wire
compatible**: point an OpenAI SDK at the edge with your `sk-` key and most things
just work, across OpenRouter / OpenAI / Anthropic / Gemini / Bedrock / Azure.

All examples were **verified live** against `make edge` (`http://127.0.0.1:8080`).

- [Authentication](#authentication)
- [Chat completions](#chat-completions)
  - [Streaming](#streaming)
  - [Model selection & routing](#model-selection--routing)
  - [Tools / function calling](#tools--function-calling)
  - [Vision / multimodal](#vision--multimodal)
  - [JSON mode & web search](#json-mode--web-search)
  - [Caching](#caching)
- [Embeddings](#embeddings)
- [Images](#images)
- [Audio (STT & TTS)](#audio-stt--tts)
- [Realtime (voice WS + relay)](#realtime)
- [AI-app features: memory, conversations, artifacts, video](#ai-app-features)
- [Agent OAuth device flow (headless agents)](#agent-oauth-device-flow)
- [Error envelope](#error-envelope)
- [Endpoint reference](#endpoint-reference)

---

## Authentication

Every client endpoint reads one header:

```
Authorization: Bearer <token>
```

Two token kinds are accepted:

| Token | Format | Where accepted |
|-------|--------|----------------|
| **API key** | `sk-<key_id_hex>.<secret>` | all `/v1/*` endpoints |
| **Agent token** | opaque (non-`sk-`), minted via [device flow](#agent-oauth-device-flow) | `/v1/chat/completions` |

Get an API key from your tenant admin (or the dashboard `/app/keys`) — see
[Admin guide §2](./02-admin-guide.md#2-api-keys).

```bash
E=http://127.0.0.1:8080
SK=sk-019f09edafd47b51a8a8fdbb7bea2784.YsBie9rhV_Og_…   # your key

# OpenAI SDK style:
#   base_url = http://127.0.0.1:8080/v1
#   api_key  = $SK
```

Failure responses (anti-enumeration: identical body for unknown vs. wrong secret):

```json
{"type":"about:blank","title":"Invalid API key","status":401,"code":"ERR_AUTH_INVALID_KEY"}
```
Expired key → `401 ERR_AUTH_KEY_EXPIRED`. No/garbage auth on `/v1/*` → `401` at the
edge (verified live).

### Using an OpenAI SDK

Because the surface is OpenAI-wire compatible, point any OpenAI SDK at the edge —
set the **base URL to `<edge>/v1`** and the **API key to your `sk-…`**. The model
string selects the provider (see [Model selection](#model-selection--routing)).

**Python** (`pip install openai`):
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8080/v1",   # the Hydroa edge, not api.openai.com
    api_key="sk-019f09ed….YsBie9rhV_Og_…", # your Hydroa key
)

resp = client.chat.completions.create(
    model="google/gemini-2.5-flash-lite",
    messages=[{"role": "user", "content": "Say hello from Hydroa."}],
)
print(resp.choices[0].message.content)

# Streaming
for chunk in client.chat.completions.create(
    model="google/gemini-2.5-flash-lite",
    messages=[{"role": "user", "content": "Count to three."}],
    stream=True,
):
    print(chunk.choices[0].delta.content or "", end="")
```

**Node / TypeScript** (`npm i openai`):
```ts
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: "http://127.0.0.1:8080/v1",
  apiKey: process.env.HYDROA_KEY, // "sk-…"
});

const resp = await client.chat.completions.create({
  model: "google/gemini-2.5-flash-lite",
  messages: [{ role: "user", content: "Say hello from Hydroa." }],
});
console.log(resp.choices[0].message.content);
```

The SDK’s `embeddings`, `images`, and `audio` helpers work the same way. Errors
arrive as the SDK’s `APIError` carrying the [problem+json](#error-envelope) `code`
and `status`. An **agent token** works identically — just pass it as `api_key`.

---

## Chat completions

```bash
curl -s -X POST $E/v1/chat/completions \
  -H "authorization: Bearer $SK" -H 'content-type: application/json' \
  -d '{"model":"google/gemini-2.5-flash-lite",
       "messages":[{"role":"user","content":"Reply with exactly: hydroa runbook live test ok"}],
       "max_tokens":40}'
```
**Verified live → `HTTP 200`:**
```json
{"id":"gen-…","object":"chat.completion","model":"google/gemini-2.5-flash-lite",
 "provider":"Google",
 "choices":[{"index":0,"finish_reason":"stop",
   "message":{"role":"assistant","content":"hydroa runbook live test ok"}}],
 "usage":{"prompt_tokens":11,"completion_tokens":7,"total_tokens":18,"cost":3.9e-06}}
```

The response is the upstream OpenAI-shaped object, passed through verbatim
(non-finite floats replaced with `null`). Request fields the gateway acts on:
`model` (required, non-empty), `messages` (required, non-empty), `stream`,
`tools`/`functions`, `response_format`, `web_search`; everything else
(`temperature`, `max_tokens`, `top_p`, …) is forwarded as-is.

### Streaming

Set `"stream": true` → `Content-Type: text/event-stream`, OpenAI-style `data:`
frames, terminated by `data: [DONE]`.

```bash
curl -s -N -X POST $E/v1/chat/completions \
  -H "authorization: Bearer $SK" -H 'content-type: application/json' \
  -d '{"model":"google/gemini-2.5-flash-lite",
       "messages":[{"role":"user","content":"Count: 1 2 3"}],"max_tokens":30,"stream":true}'
```
**Verified live:**
```
: OPENROUTER PROCESSING

data: {"id":"gen-…","object":"chat.completion.chunk","choices":[{"delta":{"content":"Okay","role":"assistant"}}]}

data: {"id":"gen-…","choices":[{"delta":{"content":", you've"}}]}
…
data: {"id":"gen-…","choices":[{"delta":{},"finish_reason":"length"}],
       "usage":{"prompt_tokens":8,"completion_tokens":30,"total_tokens":38,"cost":1.28e-05}}

data: [DONE]
```

> **Mid-stream failures** (provider error, peer close) emit a terminal SSE error
> frame **before** `[DONE]` — the HTTP status is already `200`:
> ```
> data: {"error":{"type":"upstream_error","code":"ERR_UPSTREAM_UNAVAILABLE","message":"…"}}
>
> data: [DONE]
> ```

### Model selection & routing

The `model` string maps to a provider via the **catalog** (synced from
OpenRouter). Examples in the live catalog (339 models): `google/gemini-2.5-pro`,
`google/gemini-2.5-flash-lite`, `anthropic/claude-*`, `openai/gpt-*`,
`meta-llama/llama-3.1-8b-instruct`.

An operator can also define **model groups** (an alias → ordered deployments)
with a routing strategy (`ordered`, `simple-shuffle`, `least-busy`, `latency`) and
automatic fallback when a deployment is unhealthy. As a client you just send the
alias as `model`. See [Admin guide §8](./02-admin-guide.md#8-routing-configuration).

Model errors: unknown → `400 ERR_MODEL_UNKNOWN` (verified); disabled for tenant →
`403 ERR_MODEL_DISABLED`; not in your key’s allowlist → `403 ERR_MODEL_NOT_ALLOWED`.

### Tools / function calling

Pass OpenAI-format `tools` (or legacy `functions`); they’re forwarded verbatim and
translated per provider (e.g. into Anthropic / Gemini tool schemas):

```json
{"model":"openai/gpt-4o-mini",
 "messages":[{"role":"user","content":"What's the weather in Paris?"}],
 "tools":[{"type":"function","function":{
   "name":"get_weather",
   "parameters":{"type":"object","properties":{"city":{"type":"string"}}}}}]}
```

### Vision / multimodal

Embed images as standard OpenAI `image_url` content parts:

```json
{"model":"google/gemini-2.5-flash",
 "messages":[{"role":"user","content":[
   {"type":"text","text":"Describe this image."},
   {"type":"image_url","image_url":{"url":"data:image/jpeg;base64,<b64>"}}]}]}
```

For Gemini, parts are translated to native `inlineData`. **Only `data:` URIs are
accepted on the Gemini path** (SSRF guard); external URLs → `400
ERR_UNSUPPORTED_CONTENT_PART`. `video_url` parts are also supported for Gemini.
Inline payloads are capped (`GATEWAY_GEMINI_INLINE_MAX_BYTES`, 20 MiB default).

### JSON mode & web search

```json
{"model":"openai/gpt-4o-mini","messages":[…],"response_format":{"type":"json_object"}}
```
`response_format` is passed through. **Web search** (`"web_search": true`) is
stripped unless the operator sets `GATEWAY_WEB_SEARCH_ENABLED=true`, in which case
it’s translated into provider-native grounding tools.

### Caching

Non-streaming responses can be cached in Redis (per-tenant, toggled by the admin —
[§9](./02-admin-guide.md#9-guardrails--cache)). Influence it per request:

- `Cache-Control: max-age=<N>` — set TTL (capped by `GATEWAY_CACHE_MAX_TTL_SECONDS`)
- `Cache-Control: no-cache` — bypass

The response carries `X-Cache: HIT|MISS`. A semantic (embedding-similarity) cache
is available when the operator enables it.

---

## Embeddings

```bash
curl -s -X POST $E/v1/embeddings -H "authorization: Bearer $SK" \
  -H 'content-type: application/json' \
  -d '{"model":"openai/text-embedding-3-small","input":"hello world"}'
```
Returns the upstream OpenAI-shaped `{"data":[{"embedding":[…],"index":0}], …}`.
`input` may be a string or array. Same `Cache-Control` / `X-Cache` semantics as
chat. Providers: openrouter, openai, google, bedrock, azure.

---

## Images

```bash
curl -s -X POST $E/v1/images/generations -H "authorization: Bearer $SK" \
  -H 'content-type: application/json' \
  -d '{"model":"openai/dall-e-3","prompt":"a red bicycle, studio lighting"}'
```
Returns the upstream `{"created":…,"data":[{"url":"…"}]}`. Extra fields (`n`,
`size`, `quality`, …) are forwarded. No caching on image generation.

---

## Audio (STT & TTS)

**Transcription** (multipart):
```bash
curl -s -X POST $E/v1/audio/transcriptions -H "authorization: Bearer $SK" \
  -F file=@speech.mp3 -F model=whisper-1
# → {"text":"…transcript…"}
```
`/v1/audio/translations` is identical but always outputs English. Billing derives
audio duration server-side (`per_second`), clamped by
`GATEWAY_STT_MAX_DURATION_SECONDS`.

**Text-to-speech** (streams audio bytes):
```bash
curl -s -X POST $E/v1/audio/speech -H "authorization: Bearer $SK" \
  -H 'content-type: application/json' \
  -d '{"model":"tts-1","input":"Hello from Hydroa","voice":"alloy"}' --output out.mp3
```
All governance errors (auth, budget, input too long) are returned as
`application/problem+json` **before** the `200` is committed — you never get a
`200` with an error body. Input length capped by `GATEWAY_TTS_MAX_INPUT_CHARACTERS`
(→ `413` over-cap). Billing fires once up front (`per_character`).

---

## Realtime

Two WebSocket endpoints. **Auth is over the socket**, not a header — the first
text frame must be `{"type":"auth","token":"sk-…"}` (browsers can’t set headers on
WS). No provider session opens before a successful handshake.

### Turn-based voice — `WS /v1/realtime`

```
client → {"type":"auth","token":"sk-…"}
server → {"type":"ready"}                       # or close(4401 bad token / 4408 timeout)
client → <binary audio bytes>*                  # bounded by GATEWAY_REALTIME_MAX_UTTERANCE_BYTES
client → {"type":"commit","model_stt":"whisper-1","model_chat":"…","model_tts":"tts-1","voice":"alloy"}
server → {"type":"transcript","text":"…"}
server → {"type":"reply","text":"…"}
server → <binary TTS bytes>*
server → {"type":"turn_done"}
```
Non-fatal errors keep the socket open: `{"type":"error","code":"utterance_too_large|no_audio|bad_frame|stt_failed|chat_failed|tts_failed"}`.

### Full-duplex relay — `WS /v1/realtime/relay`

Same auth frame, then the gateway dials the configured realtime provider
(`GATEWAY_REALTIME_RELAY_PROVIDER` = `openai` or `gemini`) and relays both
directions. Close codes: `4401` bad token, `4408` timeout/idle, `4404` no provider
configured (honest degrade), `4503` upstream/breaker, `1011` upstream error,
`1000` clean.

---

## AI-app features

Higher-level surfaces beyond raw model calls. All `sk-` authenticated, all
tenant-scoped (cross-tenant access → `404`).

### Memory — `/v1/memories`
```bash
curl -s -X POST $E/v1/memories -H "authorization: Bearer $SK" \
  -H 'content-type: application/json' -d '{"content":"user prefers metric units"}'
# → 201 {"id":"…","content":"…","created_at":"…"}
curl -s "$E/v1/memories?limit=50" -H "authorization: Bearer $SK"
curl -s -X POST $E/v1/memories/search -H "authorization: Bearer $SK" \
  -H 'content-type: application/json' -d '{"query":"units","top_k":10}'
curl -s -X DELETE $E/v1/memories/$ID -H "authorization: Bearer $SK"     # 204
```
Embedding is best-effort (`GATEWAY_MEMORY_EMBEDDING_MODEL`); if absent, create
still returns `201` and search falls back to text. Raw vectors are never returned.

### Conversations — `/v1/conversations`
```bash
curl -s -X POST $E/v1/conversations -H "authorization: Bearer $SK" \
  -H 'content-type: application/json' -d '{"title":"support thread"}'           # 201
curl -s "$E/v1/conversations?limit=50" -H "authorization: Bearer $SK"
curl -s $E/v1/conversations/$ID -H "authorization: Bearer $SK"                  # with messages
curl -s -X POST $E/v1/conversations/$ID/messages -H "authorization: Bearer $SK" \
  -H 'content-type: application/json' -d '{"role":"user","content":"hello"}'    # 201
curl -s -X DELETE $E/v1/conversations/$ID -H "authorization: Bearer $SK"        # 204
```
`role` ∈ `user|assistant|system`; content ≤ 1,000,000 chars.

### Artifacts — `/v1/artifacts`
```bash
curl -s -X POST $E/v1/artifacts -H "authorization: Bearer $SK" \
  -H 'content-type: application/json' \
  -d '{"name":"logo.png","content_type":"image/png","content_base64":"<b64>"}'  # 201
curl -s "$E/v1/artifacts?limit=50" -H "authorization: Bearer $SK"               # metadata only
curl -s $E/v1/artifacts/$ID -H "authorization: Bearer $SK" --output logo.png    # download
curl -s -X DELETE $E/v1/artifacts/$ID -H "authorization: Bearer $SK"            # 204
```
Bytes go to S3/MinIO if configured, else inline Postgres. Size capped by
`GATEWAY_ARTIFACT_MAX_BYTES` (→ `413`); download is always
`Content-Disposition: attachment` (XSS-safe). S3 unreachable → `503
ERR_OBJECT_STORE_UNAVAILABLE`.

### Video generation — `/v1/video/generations` (async jobs)
```bash
curl -s -X POST $E/v1/video/generations -H "authorization: Bearer $SK" \
  -H 'content-type: application/json' -d '{"model":"…","prompt":"a kite over the sea"}'
# → 200 {"id":"…","status":"queued", …}   (non-blocking)
curl -s $E/v1/video/generations/$JOB -H "authorization: Bearer $SK"
# → {"status":"succeeded","result_artifact_id":"…"}  → download via /v1/artifacts/{id}
```
Status: `queued → running → succeeded|failed`. Durable (restart-surviving) when
`GATEWAY_VIDEO_DURABLE_QUEUE_ENABLED=true`.

---

## Agent OAuth device flow

For headless agents (CI, scripts) that need a scoped, expiring token without a
long-lived `sk-` key. RFC 8628. Three actors: the **agent** polls, a **human**
approves.

```bash
# 1. Agent starts the flow (public)
curl -s -X POST $E/oauth/device/authorize -H 'content-type: application/json' -d '{"scope":"proxy"}'
# Verified live → {"user_code":"KXXL-RJZZ","device_code":"_MBtcElOF6…",
#                  "verification_uri":"…","interval":5,"expires_in":600}

# 2. Agent polls before approval (public)
curl -s -X POST $E/oauth/token -H 'content-type: application/json' \
  -d '{"grant_type":"urn:ietf:params:oauth:grant-type:device_code","device_code":"…"}'
# → 400 {"error":"authorization_pending"}   (slow_down if you poll too fast)

# 3. Human approves in their session (JWT) — binding comes from the JWT, not the body
curl -s -X POST $E/oauth/device/approve -H "authorization: Bearer $USER_JWT" \
  -H 'content-type: application/json' -d '{"user_code":"KXXL-RJZZ"}'
# → 200 {"status":"approved"}

# 4. Agent polls again → token minted (single-use)
curl -s -X POST $E/oauth/token -H 'content-type: application/json' \
  -d '{"grant_type":"urn:ietf:params:oauth:grant-type:device_code","device_code":"…"}'
# Verified live → {"access_token":"nWZipGFThW…","token_type":"Bearer",
#                  "expires_in":3600,"scope":"proxy","refresh_token":"…"}

# 5. Use the token like an API key
curl -s -X POST $E/v1/chat/completions -H "authorization: Bearer nWZipGFThW…" \
  -H 'content-type: application/json' \
  -d '{"model":"google/gemini-2.5-flash-lite","messages":[{"role":"user","content":"say: agent ok"}],"max_tokens":20}'
# Verified live → 200  "Agent ok."
```

Token state machine on `/oauth/token`: `authorization_pending` → `slow_down` →
(`access_denied` | `expired_token` | `invalid_grant`) → `200`. Each agent token
carries a **monthly spend cap** (`GATEWAY_AGENT_OAUTH_DEFAULT_BUDGET_USD`, default
$100). Tokens are opaque (not JWTs), SHA-256-hashed at rest, never logged.

---

## Error envelope

All gateway-generated errors use **RFC 9457 problem+json**:

```json
{"type":"about:blank","title":"Model 'no/such-model-xyz' is not available",
 "status":400,"code":"ERR_MODEL_UNKNOWN"}
```
(`detail` is added when present; `Retry-After` accompanies 429/503.) Common codes:

| code | status | meaning |
|------|--------|---------|
| `ERR_AUTH_INVALID_KEY` | 401 | missing/unknown key or agent token |
| `ERR_AUTH_KEY_EXPIRED` | 401 | key past `expires_at` |
| `ERR_MODEL_UNKNOWN` | 400 | model not in catalog |
| `ERR_MODEL_DISABLED` / `ERR_MODEL_NOT_ALLOWED` | 403 | disabled for tenant / not in key allowlist |
| `ERR_PAYLOAD_INVALID` | 422 | bad/missing field |
| `ERR_PAYLOAD_INPUT_TOO_LONG` | 413 | TTS/artifact over cap |
| `ERR_BUDGET_EXCEEDED` | 402 | tenant/team/key budget hit |
| `ERR_RATE_LIMITED` | 429 | RPM/TPM (has `Retry-After`) |
| `ERR_BANDWIDTH_EXHAUSTED` | 503 | per-key byte/s bucket (has `Retry-After`) |
| `ERR_UPSTREAM_UNAVAILABLE` | 502 | upstream 5xx / circuit open |
| `ERR_PROVIDER_UNAVAILABLE` | 503 | provider not configured / BYOK key missing |
| `ERR_*_NOT_FOUND` | 404 | memory / conversation / artifact / video (incl. cross-tenant) |

Mid-stream, the same is delivered as a terminal SSE frame + `[DONE]` (see
[Streaming](#streaming)).

---

## Endpoint reference

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| POST | `/v1/chat/completions` | sk- / agent | streaming, tools, vision, json, web_search |
| POST | `/v1/embeddings` | sk- | cacheable |
| POST | `/v1/images/generations` | sk- | |
| POST | `/v1/audio/transcriptions` · `/translations` | sk- | multipart |
| POST | `/v1/audio/speech` | sk- | streams audio |
| WS | `/v1/realtime` | auth-over-WS | turn-based voice |
| WS | `/v1/realtime/relay` | auth-over-WS | full-duplex relay |
| POST/GET/DELETE | `/v1/memories[...]` · `/v1/memories/search` | sk- | |
| POST/GET/DELETE | `/v1/conversations[...]` · `/messages` | sk- | |
| POST/GET/DELETE | `/v1/artifacts[...]` | sk- | |
| POST/GET | `/v1/video/generations[/{job}]` | sk- | async |
| POST | `/oauth/device/authorize` · `/oauth/token` | public | device flow |
| POST | `/oauth/device/approve` · `/deny` | user JWT | approval |

**Next:** [04 — Multi-tenant guide](./04-multi-tenant-guide.md) ·
[02 — Admin guide](./02-admin-guide.md)
