# TASK: Voice mic capture + autoplay + cost (close deferrals)

slug: voice-mic-capture-deferrals · created: 2026-06-30 · stage: production
autonomy: auto
phase: done   <!-- fast lane: ground -> specify -> contract -> tests -> build -> verify -> observe -> done -->
fast: true

> Fast lane — built IN-TREE (the voice surface lives only on feat/voice-playground, not main, so no worktree).
> Floor held: FROZEN §3 · red tests before build · recorded §6 gate. Closes the 3 honest-open deferrals from voice-playground-shell.

---

## 0 · GROUND — the real codebase

Touches (files · symbols): `apps/dashboard/components/voice/VoicePlayground.tsx` (seam props + handleRecordStart/Stop + handleMicTurn + sessionCost) · `VoiceComposer.tsx` (pointer record handlers + aria-pressed) · `VoiceThread.tsx` (autoPlay on latest turn + cost chip) · `voice-types.ts` (VoiceRecorder interface) · NEW `apps/dashboard/lib/voice-cost.ts`
Context (working folder): `apps/dashboard/tests-bff/voice-playground.test.tsx` (14 → 19); reused chat cost helper from `components/chat/ChatWorkspace.tsx` (lines 289-391, /admin/catalog/models price map) extracted to lib/voice-cost.ts.
Honors (patterns / conventions): BFF-only pass-through; getUserMedia permission/insecure-context = designed states; play()-rejection-safe; no fabricated cost ("—"/undefined when unpriced); four UI states + a11y; no gateway change, no new dep.
Anchors the contract cites: VoicePlayground seam props, VoiceRecorder, lib/voice-cost.ts computeTurnCost, /v1/audio/transcriptions + /v1/chat/completions + /v1/audio/speech

---

## 1 · SPECIFY — the rules

Feature: close voice's 3 deferrals — real hold-to-record mic capture, TTS reply autoplay, per-turn + session cost.
Must:
  - (1) Real getUserMedia/MediaRecorder hold-to-record behind an INJECTABLE seam (`requestMicStream?`, `createRecorder?` props; real defaults) → captured Blob feeds the existing STT→chat→TTS pipeline; permission-denied / insecure-context degrade to the upload path with no retry storm. (2) TTS reply autoplay on the latest audio turn (play() rejection swallowed). (3) per-turn meta.cost via the reused chat cost helper + a running session total.
  - Preserve all 14 original voice behaviors; four UI states; one h1 "Voice"; record button announces recording (aria-pressed); object URLs revoked.
Reject:
  - no mic path (denied/insecure) -> record inactive / "Mic not available", upload still works -> "mic_degrade_safe"
  - unknown model price -> cost undefined / "—", never a fabricated $0 -> "no_fabricated_cost"
  - STT-only turn (no assistant audio) -> no autoplay / no <audio> -> "no_spurious_autoplay"
Accept: Given an injected recorder + a priced model, When the operator holds-to-record a spoken turn, Then it transcribes → chats → the spoken reply autoplays, and the turn shows models·tokens·latency·cost with the session pill summing it.
Assumptions: STT/TTS tokens are provider-billed and NOT returned client-side → only the chat usage frame is priced (documented, honest); biggest risk = a fabricated cost, pinned by test_unknown_model_shows_no_cost.

---

## 3 · CONTRACT — freeze the shape

```
VoicePlayground:
  seam   -> optional requestMicStream?():Promise<MediaStream> + createRecorder?(stream):VoiceRecorder (real getUserMedia/MediaRecorder defaults)
  record -> VoiceComposer pointer-down→requestMicStream→createRecorder.start()→phase=recording; pointer-up/leave/cancel→stop():Promise<Blob>→existing turn pipeline
  cost   -> lib/voice-cost.ts computeTurnCost(model,p,c,priceMap)->number|undefined (priceMap from /admin/catalog/models); meta.cost set; sessionCost = useMemo sum
  autoplay-> VoiceThread audio autoPlay={turn.id===latestAudioTurnId}; ref.play() best-effort (rejection swallowed)
voice-types.ts: VoiceRecorder { start():void; stop():Promise<Blob> }
Pass-through over the existing audio+chat proxies — no gateway change.
```

`Least-sure flag surfaced at freeze:` [contract] cost must never coerce unknown→$0 (only the chat usage frame is priced; STT/TTS provider-billed) — pinned by test_unknown_model_shows_no_cost.
Status: FROZEN @ v1 — approved by Tin Dang (project-lead autonomous approval under the standing "ship all playground features" goal)

---

## 4 · TESTS — failing-first (red)

Plan: 5 new ids in tests-bff/voice-playground.test.tsx (3 red→green, 2 passed immediately) + the 14 originals preserved:
  test_hold_to_record_drives_pipeline · test_tts_reply_autoplays · test_stt_only_does_not_autoplay · test_voice_turn_cost_populated · test_unknown_model_shows_no_cost.
Tests live in: `apps/dashboard/tests-bff/voice-playground.test.tsx`

---

## 5 · BUILD — AI writes code

Scope (may touch): `apps/dashboard/components/voice/*` `apps/dashboard/lib/voice-cost.ts` `apps/dashboard/tests-bff/voice-playground.test.tsx`
Strategy & known-problem fixes: red tests → injectable recorder seam + handleMicTurn + autoplay targeting + computeTurnCost wiring → green; trap: jsdom has no MediaRecorder (dodged via injectable seam); trap: play() rejects (swallowed); trap: fabricated cost (dodged via undefined→"—").
Strategy actually used: as planned (in-tree frontend agent; cost helper extracted from ChatWorkspace rather than duplicated).
Code lives in: `apps/dashboard/components/voice/` + `apps/dashboard/lib/voice-cost.ts`   ·   Constraints: change no original test, no contract; no new deps.

---

## 6 · VERIFY — evidence + gate

- [x] all tests pass · coverage held · no original test or contract altered during build (voice file 19/19 = 14 original + 5 new; full dashboard suite 902/0, up from 897)
- [x] green was EARNED — orchestrator re-ran `vitest run tests-bff/voice-playground.test.tsx` → 19 passed + `tsc --noEmit` → 0 errors FIRST-HAND; agent self-verify + eslint 0
- [x] no exposed secrets, injection openings, or unexpected dependencies (pass-through only; no new dep; media-permission states are designed; play() rejection-safe; no fabricated cost)

Build expectations (from §1 Accept + §3 CONTRACT): hold-to-record drives the spoken turn pipeline, latest reply autoplays, per-turn + session cost shown (honest "—" when unpriced) — confirmed by the 5 new test ids + the 902/0 suite.

### GATE RECORD
Outcome: PASS
Reviewed by: Tin Dang (orchestrator-driven; orchestrator re-ran voice tests 19/19 + tsc 0 first-hand) · date: 2026-06-30
<!-- OBSERVE: [SPEC · open] STT/TTS token cost is provider-billed, not priced client-side (honest residue, documented); realtime-WS spoken modes still deferred to the realtime-voice milestone. -->
