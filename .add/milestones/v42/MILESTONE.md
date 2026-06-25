# MILESTONE: Voice breadth (multi-provider STT/TTS)

goal: A signed-in user can transcribe speech and synthesize voice through more than one provider (incl. Azure OpenAI audio), with a TTS input ceiling and a dashboard voice surface — beyond today's OpenAI-only audio.
rationale: new-major → milestone 3 of 9 (program v40–v48, "AI Application Platform"). Tin 2026-06-26 "implement all, best decision". Recon (2026-06-26): STT (`/v1/audio/transcriptions`) + TTS (`/v1/audio/speech`) ALREADY ship but OpenAI-ONLY (non-OpenAI audio adapters hard-raise); TTS bills per_character at-start with NO input ceiling (abuse/billing vector); no `/v1/audio/translations`; no dashboard voice UI. "Breadth" = a 2nd provider + close the gaps + a usable surface. EXTENDS the v21 Azure (deployment-URL + AAD) + v25 BYOK + v27 audio-billing arcs.
stage: production · status: active · created: 2026-06-26

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - Azure OpenAI audio provider: real STT (`/audio/transcriptions`) + TTS (`/audio/speech`) via the v21 Azure deployment-URL + AAD/api-key pattern (Azure OpenAI audio is the SAME OpenAI wire — a thin passthrough, NOT the separate Azure Cognitive Speech API). Makes audio genuinely multi-provider; offline-testable via the independent-oracle stub.
  - TTS input guardrail: a default-ON `GATEWAY_TTS_MAX_INPUT_CHARACTERS` ceiling (mirrors `GATEWAY_STT_MAX_DURATION_SECONDS`) so an unbounded `input` can't drive runaway per_character billing; over-cap → a clear 4xx BEFORE any upstream call/bill.
  - `/v1/audio/translations` (Whisper translate-to-English): a second STT route reusing `TranscriptionUseCase` (~80% reuse), upstream path `/audio/translations`.
  - Dashboard voice surface `/app/voice`: STT file-upload → transcript preview + TTS text→voice→inline `<audio>` playback, via the BFF (which already proxies `audio/*`). Role-open, mirrors the v40 chat workspace ethos.
Out:
  - Deepgram / ElevenLabs / Azure Cognitive Speech (separate non-OpenAI-wire APIs) — deferred; Azure OpenAI audio is the first breadth provider (lowest risk, max reuse). OpenRouter-proxied Whisper deferred (recon RISK: OpenRouter multipart support unverified).
  - STT streaming / real-time ASR partial transcripts; live mic capture (MediaRecorder) in the dashboard — whole-file STT only this milestone (TTS already streams bytes).
  - TTS disconnect-refund (bills at-start; mid-stream abort keeps the bill) — a known limitation, tracked as a delta, not closed here.
  - Any change to the existing OpenAI STT/TTS behavior — additive only; OpenAI paths stay byte-identical.

## Shared decisions & glossary deltas   (living — every task must honor these)
- AUDIO-PROVIDER-BREADTH (NEW glossary): audio providers register in the SAME `ProviderRegistry` (modality `audio_stt`/`audio_tts`) as today; a new provider supplies real `post_multipart` (STT) + `stream_bytes` (TTS). Azure OpenAI audio is OpenAI-wire over the deployment-URL base (reuse v21 Azure auth). BYOK per-tenant credential resolution applies (v25).
- DEFAULT-SAFE GUARDRAIL: the TTS input cap is ON by default (unlike v41's default-OFF feature flags) because it closes a billing/abuse vector — but it rejects BEFORE billing/upstream (no partial charge). Pre-stream governance only (mirrors the existing SpeechUseCase pre-stream error model).
- BILLING HONESTY (v27): STT per_second, TTS per_character — unchanged; the new provider bills on the same units; never fabricate a duration/char count.
- FE honors WCAG-AA + v23/v24 tokens (loading/empty/error/success, keyboard, a11y); the BFF preserves its audio/* streaming + fail-closed auth.

## Shared / risky contracts (freeze these first)
- Audio-provider extension shape (the real post_multipart/stream_bytes seam + Azure deployment-URL/auth reuse) -> owning task `azure-audio-provider`
- TTS input-cap contract (knob + the reject-before-bill envelope) -> owning task `tts-input-guardrails`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] azure-audio-provider      depends-on: none                     — real Azure OpenAI STT (`post_multipart`) + TTS (`stream_bytes`) over the v21 deployment-URL + AAD/api-key; register `azure` for audio_stt/audio_tts; BYOK. FREEZES the audio-provider extension pattern.
- [ ] tts-input-guardrails       depends-on: none                     — default-ON `GATEWAY_TTS_MAX_INPUT_CHARACTERS`; over-cap → 4xx BEFORE upstream/bill; OpenAI + Azure honored.
- [ ] audio-translations-endpoint depends-on: none                    — `POST /v1/audio/translations` reusing `TranscriptionUseCase` (upstream `/audio/translations`); per_second billing.
- [ ] voice-playground            depends-on: azure-audio-provider     — `/app/voice`: STT upload→transcript + TTS text→voice→inline playback, via the BFF; role-open nav entry.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] STT and TTS each work through MORE than one provider — a request routed to an Azure audio model reaches Azure (deployment-URL, OpenAI-wire) and returns a transcript / streamed audio; OpenAI paths unchanged   (← azure-audio-provider)
- [ ] A TTS request whose `input` exceeds `GATEWAY_TTS_MAX_INPUT_CHARACTERS` is rejected with a clear 4xx BEFORE any upstream call or bill; within-cap is unaffected   (← tts-input-guardrails)
- [ ] `POST /v1/audio/translations` transcribes-and-translates audio to English and bills per second   (← audio-translations-endpoint)
- [ ] A signed-in user can, in `/app/voice`, upload audio to get a transcript and type text to hear synthesized speech   (← voice-playground)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : <add.py / state.json / templates — what shipped, or "untouched">
- skill   : <SKILL.md / phases/* / guides — what shipped, or "untouched">
- book    : <docs/* — what shipped, or "untouched">

### Cross-task evidence   (one row per task)
- <slug> : gate=<PASS|RISK-ACCEPTED> · tests=<n green> · residue=<none|note>

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [ ] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: <restate the milestone goal — and the one evidence line that proves the ship meets it>

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] <step — e.g. open a PR from the Close ship-review above; the human reviews + merges>
- [ ] <step — e.g. export the ship-review to a hand-off doc, e.g. `pandoc CLOSE.md -o close.docx`>
- [ ] <step — e.g. tag / publish / deploy  (human-run, per release.md)>
