# MILESTONE: LiteLLM parity slice 5 — multi-modal & multi-provider

goal: a tenant can call embeddings, image-generation, and audio (speech-to-text + text-to-speech)
endpoints through the same authenticated, governed, accurately-billed proxy — each served by a
direct provider selected per model modality, while OpenRouter still serves chat unchanged
rationale: new-major — LiteLLM's defining value is the UNIFIED multi-modal, multi-provider API;
v1–v6 built a chat-only proxy on a single chat-only upstream (OpenRouter). This slice opens the
non-chat surface, which forces the upstream-intake decision deferred since v3. Scoped per Tin Dang's
"Full multi-modal + multi-provider" + "provider-selection seam" selections (2026-06-12).
stage: production · status: active · created: 2026-06-12

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  a provider-selection seam (a typed upstream-provider routing layer; the catalog model's
     MODALITY + provider decide which upstream serves a request — never the client); a first
     DIRECT provider adapter (OpenAI — the only provider serving all three target modalities with
     OpenAI-compatible shapes) wired behind the seam; OpenRouter retained as the default CHAT
     provider, byte-identical to v6; a per-unit PRICING model (pricing_unit discriminator:
     per_token | per_image | per_second | per_character + quantity fields on the ledger; recorder
     dispatch on unit; single-bill invariant preserved); three new endpoint families through the
     SAME governance pipeline (auth → allowlist → catalog → budget → rate-limit → record):
     POST /v1/embeddings (token-priced), POST /v1/images/generations (per-image-priced),
     POST /v1/audio/transcriptions (STT, multipart in, per-second-priced) + POST /v1/audio/speech
     (TTS, per-character-priced, byte streaming out); catalog gains a modality + provider
     discriminator and a population path for the direct provider's models; live close harness
     (provider stub overlay + double-pass).
Out: a THIRD provider (the seam is generic but only OpenAI is built — Anthropic/Vertex/etc. are
     their own future intake); routing STRATEGIES across providers for the SAME modality
     (latency/cost — a provider per modality is deterministic here; the v6 strategy seam is not
     extended); fine-tuning / batch / assistants / realtime APIs (separate surfaces); embeddings-
     backed semantic cache (semantic-cache stayed deferred and stays deferred); client-selectable
     provider override (provider is decided by catalog metadata only); image/audio CONTENT
     guardrails (text guardrails are chat-only; non-chat content moderation is a future slice).

## Shared decisions & glossary deltas   (living — every task must honor these)
- SUPERSESSION of the locked "OpenRouter as sole upstream" decision: OpenRouter remains the
  DEFAULT chat provider (v6 byte-identical); direct providers are ADDITIVE, selected per modality
  by catalog metadata. Recorded as a Key Decision at the provider-seam freeze; OpenRouter-only
  deployments keep working (a non-configured direct provider ⇒ its modality endpoints return a
  clean 503, never affecting chat).
- Provider selection is SERVER-decided: the catalog row's (modality, provider) determines the
  upstream. A client never names a provider; an unknown/unpriced model is rejected at the entry
  catalog check (reuse the v6 alias-aware check shape). Modalities: chat · embedding · image ·
  audio_stt · audio_tts.
- Billing follows REALITY and never double-bills: every request bills exactly once for the SERVED
  (provider, model, pricing_unit); budget / rate-limit / allowlist run against the served model;
  the recorder + typed-extras seam is the ONLY ledger write path (carried from v6). Per-unit
  dispatch: per_token (embeddings/chat), per_image (images), per_second (STT), per_character (TTS).
  Quantity (tokens | images | seconds | characters) lands in the ledger; cost = quantity × unit
  price × (1 + tenant markup).
- Defaults preserve v6: the chat path, OpenRouter wiring, and existing pricing rows are untouched;
  new endpoints/providers/pricing-units are additive. New env knobs use the GATEWAY_ prefix; the
  direct-provider key (e.g. GATEWAY_OPENAI_API_KEY) is handled exactly like the OpenRouter key —
  never logged, echoed, or committed.
- Streaming boundary (carried from v6): only TTS streams bytes out; embeddings/images/STT are
  non-streaming. The "no retry/fallback after the first forwarded byte" rule holds for TTS.
- Every new app.state/test seam ships its paired production-wiring regression test (foundation v6
  rule). Every new endpoint reuses the frozen governance seams (checker/budget/rate-limit/recorder)
  — resilience/governance wraps the new upstream call sites; their contracts are unchanged.
- GLOSSARY gains: modality, provider, direct_provider, provider_selection, pricing_unit, quantity.

## Shared / risky contracts (freeze these first)
- provider-selection seam shape + catalog (modality, provider) discriminator + direct-provider
  adapter interface -> owning task provider-seam
- per-unit pricing model (pricing_snapshots + usage_records schema + recorder unit-dispatch +
  single-bill) -> owning task pricing-units
- /v1/embeddings request+response + token unit -> owning task embeddings-endpoint
- /v1/images/generations request+response + per-image unit -> owning task images-endpoint
- /v1/audio/{transcriptions,speech} request+response + per-second/per-character units -> owning task audio-endpoints

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] provider-seam       depends-on: none                        — typed upstream-provider routing seam; catalog (modality, provider) discriminator + population; OpenAI direct adapter wired behind it; OpenRouter default for chat (v6-identical)
- [ ] pricing-units       depends-on: none                        — pricing_unit discriminator + quantity fields on pricing_snapshots/usage_records; recorder unit-dispatch; single-bill preserved; v6 token rows untouched
- [ ] embeddings-endpoint depends-on: provider-seam, pricing-units — POST /v1/embeddings; generalized payload validate (input, not messages); token-priced; governance pipeline reused
- [ ] images-endpoint     depends-on: provider-seam, pricing-units — POST /v1/images/generations; per-image pricing; no token usage; quantity = image count
- [ ] audio-endpoints     depends-on: provider-seam, pricing-units — POST /v1/audio/transcriptions (STT, multipart, per-second) + /v1/audio/speech (TTS, per-character, byte streaming)
- [ ] v7-live-verify      depends-on: all-of-above                 — live close: provider stub overlay (docker-compose.e2e.v7.yml) + scripts/live_v7_verify.py; double-pass rule

## Exit criteria (observable; map each to the task that delivers it)
- [x] A request to an embedding model is routed to the direct provider, passes the full governance pipeline, and produces exactly ONE ledger row priced per_token at the embedding model's unit price (← embeddings-endpoint) — embeddings-endpoint gate PASS (11/11); live C1: one per_token row, cost_usd>0.
- [x] An image-generation request is served and billed PER-IMAGE (not per-token); the ledger row carries pricing_unit=per_image and the image quantity (← images-endpoint) — images-endpoint gate PASS; live C2: one per_image row, quantity=2 (== entries returned, no over-bill).
- [x] An STT transcription bills per_second and a TTS speech request bills per_character; each is one ledger row with the correct unit and quantity; TTS streams bytes with the v6 no-retry-after-first-byte boundary (← audio-endpoints) — audio-endpoints gate PASS (18 tests); live C3: per_second qty=12.5; C4: per_character qty=len(input); TTS bills-at-start before stream.
- [x] Chat completions remain byte-identical to v6 — OpenRouter is the default chat provider, modality routing is additive, and an OpenRouter-only deployment (direct provider unconfigured) returns a clean 503 on non-chat endpoints without affecting chat (← provider-seam) — provider-seam gate PASS; chat source byte-identical (git diff INVIOLABLE files); live C5: chat 200 via OpenRouter/v6 path; "openai" added to registry only when key non-empty.
- [x] The ledger never double-bills and every non-chat row records the served provider, model, pricing_unit, and quantity; budget and rate-limit checks run against the served model (← pricing-units) — pricing-units gate PASS; live C1–C4 each assert EXACTLY ONE row with the correct pricing_unit + quantity (single-bill preserved per modality).
- [x] All of the above proven LIVE through the TLS edge with a provider stub overlay, two consecutive clean passes (← v7-live-verify) — v7-live-verify gate PASS; double-pass 20/20 ×2, both exit 0 (tmp/v7_pass1.log, tmp/v7_pass2.log) via https://localhost:8443.
