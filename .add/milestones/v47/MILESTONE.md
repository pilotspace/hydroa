# MILESTONE: Realtime voice (turn-based WebSocket session)

goal: An API key holder can hold a live voice conversation over a single WebSocket: stream audio in, get a transcript, an assistant reply, and synthesized audio back — reusing the v42 STT/TTS + chat pipeline with zero new dependency.
rationale: new-major → milestone 8 of 9 (program v40–v48). Tin's checkpoint: reuse-only MVP, keep full-auto ([[v46-v48-reuse-only-decision]]). v42 shipped one-shot HTTP STT/TTS; "realtime voice" = a live, persistent voice SESSION. The reuse MVP is a turn-based loop over a single WebSocket (Starlette WS — already a dep, ZERO new package) that chains the EXISTING TranscriptionUseCase (STT) → chat completion → SpeechUseCase (TTS). The full-duplex, barge-in, sub-second provider-realtime relay (OpenAI Realtime / Gemini Live) is a documented SCALE delta. The dashboard live-voice UI is deferred (Next's BFF cannot proxy WebSockets — a same-origin WS relay or a direct-gateway-WS + short-lived-token mint is genuinely NEW infra, not reuse; v42's /app/voice already covers the non-realtime UI need).
stage: production · status: active · created: 2026-06-26

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - A gateway WebSocket endpoint `/v1/realtime` (FastAPI/Starlette WS — no new dep) that:
    1. Authenticates a first `{type:"auth", token:"sk-..."}` message via the SAME KeyAuthenticator (+ expiry gate) as v43–v46; closes (4401) on missing/invalid/expired; an auth-timeout closes the socket.
    2. Runs a turn loop: the client streams audio (binary frames) then sends `{type:"commit", ...models}`; the server runs STT (TranscriptionUseCase) → emits `{type:"transcript", text}` → a chat completion → `{type:"reply", text}` → TTS (SpeechUseCase) → streams audio frames → `{type:"turn_done"}`.
    3. Design-for-failure: a per-utterance audio size cap (reject over-cap → error frame), any provider/use-case error → `{type:"error", code, message}` (the socket stays usable or closes gracefully), client disconnect → clean teardown (WebSocketDisconnect, no leaked tasks).
  - Starlette TestClient `websocket_connect` tests with the STT/chat/TTS use-cases STUBBED on app.state (no live providers / no network) — mirror the v42/v44 stub seams.
Out:
  - The full-duplex / barge-in / sub-second provider-realtime relay (OpenAI Realtime, Gemini Live) — a documented SCALE delta (needs a provider realtime API + a bidirectional stream pump).
  - The dashboard live-voice UI — DEFERRED (Next BFF can't proxy WS; needs new infra). v42 /app/voice covers the non-realtime UI today.
  - Envoy edge WebSocket-upgrade + per-message auth config — a DEPLOY-time delta (the deployed edge ext_authz is HTTP; the WS endpoint is correct + TestClient-verified at the gateway, but the edge may need WS-upgrade config to expose it publicly).
  - Persisting the conversation (could later reuse v43 conversations) — a delta.

## Shared decisions & glossary deltas   (living — every task must honor these)
- REALTIME SESSION (NEW glossary): a single WebSocket carrying an authenticated, multi-turn voice conversation; each TURN = audio-in → transcript → reply → audio-out. Turn-based (not full-duplex) in the MVP.
- AUTH-OVER-WS (security): the FIRST message must be `{type:"auth", token}`; authenticate via KeyAuthenticator + the v43–v46 expiry gate; no other frame is processed until authed; a bad/missing/expired token or an auth timeout → close with a 4xxx code. The token is NEVER in the URL (no query-param token — avoids log leakage).
- REUSE: chains the EXISTING TranscriptionUseCase + chat use-case + SpeechUseCase; no new provider, no new dependency. Billing/governance ride those use-cases unchanged.
- DESIGN-FOR-FAILURE: auth timeout · per-utterance size cap · provider error → an error frame (not a silent hang or a 500) · disconnect → clean teardown.

## Shared / risky contracts (freeze these first)
- The `/v1/realtime` WS protocol (frame types + the auth handshake + the turn loop + the error/disconnect behavior) -> owning task `realtime-voice`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] realtime-voice   depends-on: none   — gateway WebSocket `/v1/realtime`: first-message sk- auth (+ expiry, timeout), then an audio→STT→chat→TTS→audio turn loop reusing v42 use-cases; size cap + error-frame + clean disconnect teardown. Starlette TestClient tests (stubbed use-cases). FREEZES the WS protocol.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] An API key holder can open a WebSocket to `/v1/realtime`, authenticate with their sk- key, stream an utterance, and receive a transcript + a reply + synthesized audio back over the same socket; a missing/invalid/expired token is rejected (close); an over-cap utterance and a provider failure each yield a clean error frame (never a hang or a crash); a client disconnect tears the session down cleanly   (← realtime-voice)

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
