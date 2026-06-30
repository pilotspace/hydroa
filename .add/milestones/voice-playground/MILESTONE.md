# MILESTONE: Voice Playground

goal: The Voice workspace becomes a true Console-grade playground: live microphone capture → speech-to-text, a hands-free voice turn loop, rich text-to-speech playback, and a per-turn session transcript with metadata + cost — a surface an operator runs real spoken LLM work on.
rationale: new-major — milestone 2 of the "AI feature depth (Console-grade)" program (after chat-playground, milestone 1, merged). Like chat, this is a real feature rebuild of a thin surface, NOT byte-identical: today `components/voice/VoicePlayground.tsx` is a working-but-shallow STT-file-upload + TTS pair (no mic, no streaming, no session). GROUND map (Explore 2026-06-30): the voice BACKEND is mature and already exposed — STT `/v1/audio/transcriptions` + `/v1/audio/translations`, TTS `/v1/audio/speech`, plus realtime WS `/v1/realtime` (v47 turn-based) and `/v1/realtime/relay` (v52 full-duplex) — so this milestone is FRONTEND-led, pass-through over the existing HTTP audio endpoints, mirroring chat's "pass-through + at most one backend delta". Relationship: extends the chat-playground pattern (shared Console shell language, design-confirm gate); depends-on the v42/v47/v52 audio+realtime backend; overlaps nothing. The realtime-WS modes are deliberately OUT (see Scope Out) — the Next.js BFF cannot proxy WebSocket and the data-plane sk- token never reaches the browser, so a realtime voice UI needs a browser-token-exposure (security) + WS-transport decision that is its own milestone, not this one.
stage: production · status: active · created: 2026-06-30T10:55:41+00:00

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  A Console-grade Voice playground for `/app/voice`. A workspace layout (a voice composer with live microphone capture · a transcript/conversation thread · a voice/model controls panel) in the same Console visual language as chat. Live mic capture (getUserMedia/MediaRecorder) → speech-to-text through the existing `POST /v1/audio/transcriptions` (push-to-talk AND file upload preserved), with segmented transcript rendering. A hands-free **voice turn loop** — mic → STT → chat completion (`/v1/chat/completions`) → TTS — orchestrated CLIENT-side over the existing HTTP endpoints (no realtime WS). Rich text-to-speech via `POST /v1/audio/speech` — voice picker, response_format, playback + per-utterance replay. A per-turn session transcript with metadata (STT model · chat model · TTS voice · audio duration · tokens · latency · cost) + a running session total. Streaming/cancel preserved where the HTTP shape allows (TTS stream, abortable turns). At most ONE backend delta: voice-session/transcript persistence IF a task proves the existing `/v1/conversations` store cannot carry it — pass-through is preferred.
Out: The realtime WebSocket modes — turn-based `/v1/realtime` (v47) AND full-duplex relay `/v1/realtime/relay` (v52) — are DEFERRED to a follow-up "realtime voice" milestone: they require (a) a browser→gateway WS transport since the Next.js BFF cannot proxy WS, and (b) exposing a short-lived data-plane token to the browser (a security surface the current server-side-only sk- mint forbids). Browser-token exposure / WS sidecar / CORS-public-gateway decisions. Non-OpenAI audio provider adapters (STT/TTS implemented only in `openai_provider.py` — Gemini/Bedrock/Anthropic audio is a provider-layer gap, out of scope). Relay-path usage billing. Server-side voice activity detection / barge-in. Speaker diarization. Multi-language UI beyond what the STT `language` param already forwards.

## Shared decisions & glossary deltas   (living — every task must honor these)
- **Feature rebuild, NOT byte-identical** — this milestone changes the voice surface contracts; the existing `tests-bff/voice-playground.test.tsx` evolves WITH the new contracts via red/green TDD. Weakening a test to dodge a real regression is still forbidden; changing a test because the contract legitimately changed is the method.
- **Pass-through first** — STT, TTS, and the turn loop ride the existing `/v1/audio/transcriptions`, `/v1/audio/speech`, and `/v1/chat/completions` proxies. Prefer NO gateway change. A voice-session persistence delta is allowed ONLY if a task proves the existing conversations store cannot carry the transcript.
- **No realtime WS in this milestone** — every voice turn is HTTP request/response, client-orchestrated. The mature `/v1/realtime*` endpoints stay untouched and developer-facing (Scope Out).
- **Mic capture is a first-class, fail-safe surface** — getUserMedia requires a secure context + user permission. Permission-denied, insecure-context (no `mediaDevices`), no-input-device, and empty-utterance are designed states, not crashes. Captured audio is encoded to a MIME the STT path accepts (the BFF forwards binary unmangled; 32 MiB / 4 h caps already enforced server-side).
- **Design-before-code (UDD)** — the shell task carries a Console-grade design-confirm gate: a captured Voice-workspace design is approved BEFORE any build, reusing chat's Console language.
- **Four UI states + a11y by construction** — Loading/Empty/Error/Success from `states.tsx`; one h1; decorative icons aria-hidden; WCAG 2.2 AA; the transcript thread is a `role=log` live region; the mic control announces recording state.
- **Design-for-failure** — turns use AbortController + cancel; mic permission/insecure-context degrade to upload; STT/TTS upstream errors surface problem+json `title`; no retry-storm on a settled 4xx; the turn loop bounds itself (no infinite re-trigger).

## Shared / risky contracts (freeze these first)
- **Voice workspace layout + design system** (the composer + mic control + transcript thread + voice/model controls panel; the Console voice visual language) -> owning task `voice-playground-shell`. Every later task consumes this frozen shell — freeze it (with the design-confirm) first.
- **Audio capture contract** (how the browser captures + encodes mic audio — MIME, recorder config, chunking — and the FormData shape the STT path consumes) -> owning task `voice-mic-capture-stt`. The turn loop + session tasks consume it.
- **Voice-session / transcript metadata contract** (the per-turn record: STT/chat/TTS models · duration · tokens · latency · cost; and whether persistence reuses `/v1/conversations` or needs the one backend delta) -> owning task `voice-session-transcript`.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
> NOTE: the 5-task plan was delivered as ONE combined Console-grade rebuild (parallel worktree build) under `voice-playground-shell`, which shipped the shell + STT-upload + TTS + text-driven turn-loop + per-turn metadata. A focused follow-up `voice-mic-capture-deferrals` closes the 3 deferred gaps (real MediaRecorder capture, TTS autoplay, per-turn cost).
- [x] voice-playground-shell    depends-on: none                  — gate=PASS. The Console-grade voice layout + STT/TTS/turn-loop/metadata (combined build). Freezes the shell.
- [ ] voice-mic-capture-deferrals depends-on: voice-playground-shell — Real getUserMedia/MediaRecorder hold-to-record (testable seam) + TTS reply autoplay + populate per-turn cost. Closes exit criteria #2/#4/#5.
- [x] voice-tts-playback        — delivered in the combined build (TTS voice/format pickers + playback controls).
- [x] voice-turn-loop           — delivered in the combined build (text-driven mic→STT→chat→TTS path; the SPOKEN-input path lands with voice-mic-capture-deferrals).
- [x] voice-session-transcript  — delivered in the combined build (per-turn models/tokens/latency + running session; cost field populated by voice-mic-capture-deferrals).

## Exit criteria (observable; map each to the task that delivers it)
- [x] The voice surface is a Console-grade playground (composer · mic control · transcript thread · controls) matching an approved design   (← voice-playground-shell)
- [ ] An operator can hold-to-talk into the microphone and see their speech transcribed by the model   (← voice-mic-capture-deferrals: real MediaRecorder capture)
- [x] An operator can type or pick text, choose a voice, and hear it spoken with playback controls   (← voice-playground-shell)
- [ ] An operator can speak a turn and hear a spoken model reply without typing (mic → STT → chat → TTS)   (← voice-mic-capture-deferrals: spoken-input path + autoplay; text path already works)
- [ ] Each voice turn shows its models · audio duration · tokens · latency · cost, and the session shows a running total   (← voice-mic-capture-deferrals: cost population; models/tokens/latency already shown)

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
- [ ] Open ONE PR for the whole voice-playground line from a feature branch → `main`; Tin reviews + merges.
- [ ] No new migration expected (pass-through) — if the voice-session delta is taken, run the migration check before deploy.
- [ ] Fold this milestone's §7 deltas at release time (realtime-WS voice, non-OpenAI audio adapters, relay billing) — bundle into the next release notes (human-run, per release.md).
