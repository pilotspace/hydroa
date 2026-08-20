---
type: Task
title: Wire the per-file audio cap into the /v1/realtime WS→STT bridge
status: done
depth: quick
sensitivity: security
milestone: release-hardening-p0
scope:
  - apps/gateway/src/gateway/proxy/api
  - apps/gateway/tests
gives:
  - S1 realtime_ws._real_stt — its TranscriptionUseCase construction passes max_file_bytes=settings.max_audio_upload_bytes (mirroring audio_deps), so an oversized buffered utterance is refused by the SAME per-file check as HTTP STT (413-class ERR_PAYLOAD_AUDIO_TOO_LARGE ProblemError) before upstream/billing
generated: { by: add/3.2.0, at: 2026-08-18 }
verified:
  - { by: "Tin Dang", at: 2026-08-19, act: freeze, authority: human, direction: "sha256:94d739f84455ed4c" }
  - { by: "cli", at: 2026-08-19, act: brief, authority: process, brief: "sha256:ff6616a72e88dedf" }
  - { by: "process:run", at: 2026-08-19, act: run, authority: process, outcome: PASS, receipt: /tasks/upload-bounds-realtime-stt.d/runs/1.md }
  - { by: "Tin Dang", at: 2026-08-19, act: gate, authority: human, outcome: PASS, receipt: /tasks/upload-bounds-realtime-stt.d/runs/1.md, brief: "sha256:ff6616a72e88dedf" }
advised_by: appsec-engineer
---
## CARD
goal: Close the Tin-directed follow-up from upload-bounds-audio's gate: the WS→STT bridge (realtime_ws.py:205) constructed TranscriptionUseCase without max_file_bytes, leaving the per-file cap off on that one path.
why: Defense-in-depth parity, not an open hole — the WS protocol already bounds utterance accumulation at realtime_max_utterance_bytes (its own utterance_too_large refusal). But the frozen upload-bounds contract's premise is that TranscriptionUseCase owns the per-file boundary; a construction site that silently opts out is exactly the drift the parent task's structural sweep exists to prevent (it cannot see constructor kwargs). One-kwarg fix, mirrored from audio_deps.py.
beat: done · next: add status

## RULES
<must>
- M1 _real_stt's TranscriptionUseCase receives max_file_bytes=settings.max_audio_upload_bytes (the SAME knob the HTTP path injects — never a second/realtime-specific knob), so an over-cap buffer raises the per-file ERR_PAYLOAD_AUDIO_TOO_LARGE ProblemError with zero upstream calls and zero usage records.
- M2 An at-cap buffer still transcribes (the wiring must not break the normal path or introduce an off-by-one against the parent task's strict->).
</must>
<reject>
- R:SECOND_KNOB a new realtime-specific cap setting (config drift between HTTP and WS STT) -> "SECOND_KNOB"
- R:WEAKENED_UTTERANCE_GATE touching realtime_max_utterance_bytes or the utterance_too_large protocol refusal (that layer stays exactly as shipped) -> "WEAKENED_UTTERANCE_GATE"
</reject>

## ASSUMPTIONS
- A1 [who] covers: S1 · n/a · same global knob, all tenants alike — settled by the parent task's A1
- A2 [which] covers: S1 · the request does not say which realtime legs are in; taking: ONLY the STT leg (_real_stt); TTS/chat legs carry no upload read · probe: checks call _real_stt directly -> if wrong, the other legs get their own audit later
- A3 [when] covers: S1 · n/a · boundary semantics (strict >) settled by the parent task's A3; M2's at-cap check pins it here
- A4 [absent] covers: S1 · the request does not say what cap=0 means here; taking: identical to HTTP (0 disables the per-file check; the utterance gate still bounds the WS path) -> if wrong, nothing new: the knob's semantics are the parent's
- A5 [order] covers: S1 · the request does not say where the refusal surfaces in the WS protocol; taking: the ProblemError propagates out of _real_stt exactly like every other use-case error on this path (the existing except-handling in realtime_ws owns the protocol frame) — this task changes NO error plumbing · probe: checks assert at the _real_stt seam, not the socket frame -> if wrong (a dedicated WS error frame wanted), that is protocol design, a separate task
- A6 [experience] covers: S1 · n/a · no new user-visible surface — the WS client experience is owned by the existing error plumbing (A5)

## PLAN
contract: one kwarg at realtime_ws.py's TranscriptionUseCase construction (max_file_bytes=_settings.max_audio_upload_bytes) + comment citing this task; red checks call _real_stt directly with a fake websocket (.app = a small-cap create_app) — no WS handshake needed.
scope: apps/gateway/src/gateway/proxy/api/realtime_ws.py · tests/upload_bounds/test_realtime_stt_cap.py

## EDGES
- E1 over-cap buffer -> ProblemError ERR_PAYLOAD_AUDIO_TOO_LARGE, zero provider/recorder calls
- E2 at-cap buffer -> transcript returned, exactly one provider call

## CHECKS
- test_realtime_stt_over_cap_refused · covers: M1, A2, A5, E1, R:SECOND_KNOB · _real_stt with cap+1 bytes raises the per-file ProblemError (code asserted), provider+recorder untouched; the cap read is settings.max_audio_upload_bytes (the fixture only sets THAT knob)
- test_realtime_stt_at_cap_passes · covers: M2, E2, R:WEAKENED_UTTERANCE_GATE · _real_stt with exactly cap bytes returns the faked transcript with one provider call (and no edit anywhere near the utterance gate — enforced by scope). GREEN-BY-DESIGN regression pin (the tests/audio_endpoints AU/AT-regression precedent): today everything passes uncapped, so this cannot be red — its job is to stay green after Build proves no off-by-one.
red-first: every check MUST fail first (the M2 row above is the one disclosed green-by-design regression pin).

## EVIDENCE
receipt: <runs/<n>.md>
gate: <PASS | RISK-ACCEPTED | HARD-STOP>

## LESSONS
- <lesson> -> add learn <lens>
