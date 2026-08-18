---
type: Task
title: Bound every multipart upload read — audio STT/translation cap with structured 413
status: done
depth: standard
sensitivity: security
milestone: release-hardening-p0
scope:
  - apps/gateway/src/gateway/proxy/application
  - apps/gateway/src/gateway/proxy/api
  - apps/gateway/src/gateway/core
  - apps/gateway/src/gateway/main.py
  - apps/gateway/tests
gives:
  - S1 POST /v1/audio/transcriptions + /v1/audio/translations — a precise per-FILE cap (settings.max_audio_upload_bytes) enforced in TranscriptionUseCase with a structured 413 ERR_PAYLOAD_AUDIO_TOO_LARGE, before upstream and before any usage record; the edge route cap gains multipart headroom (the files-uploads precedent) so this per-file check owns the exact boundary
  - S2 structural multipart-bounds guard — a test that pins: every multipart-consuming route prefix carries a finite BodySizeLimitMiddleware cap, default_cap is finite, and every upload-field `.read()` in proxy/application flows through a capped reader (allowlist sweep)
generated: { by: add/3.2.0, at: 2026-08-18 }
verified:
  - { by: "Tin Dang", at: 2026-08-18, act: freeze, authority: human, direction: "sha256:3db7201c7a5f558b" }
  - { by: "cli", at: 2026-08-18, act: brief, authority: process, brief: "sha256:a17d2ae7b99685fe" }
  - { by: "process:run", at: 2026-08-18, act: run, authority: process, outcome: PASS, receipt: /tasks/upload-bounds-audio.d/runs/1.md }
  - { by: "Tin Dang", at: 2026-08-18, act: refreeze, authority: human, direction: "sha256:a45e2367178f324c" }
  - { by: "cli", at: 2026-08-18, act: brief, authority: process, brief: "sha256:18bf0e05ce8068d8" }
  - { by: "process:run", at: 2026-08-18, act: run, authority: process, outcome: PASS, receipt: /tasks/upload-bounds-audio.d/runs/2.md }
  - { by: "Tin Dang", at: 2026-08-18, act: gate, authority: human, outcome: PASS, receipt: /tasks/upload-bounds-audio.d/runs/2.md, brief: "sha256:18bf0e05ce8068d8" }
advised_by: appsec-engineer
---
## CARD
goal: Close P0-6 (depth review artifact 6816985f, "Audio uploads have no size cap"): give audio the images/files per-file-cap depth and pin the whole multipart surface structurally.
why: The review's headline ("unbounded multipart read") is PARTLY false — BodySizeLimitMiddleware has capped /v1/audio/ total-body at max_audio_upload_bytes since #65 (main.py route_caps, outermost layer). The REAL gaps: (a) no per-file check exists, so the contracted boundary error is the coarse edge 413 and a raw file of exactly max_audio_upload_bytes is WRONGLY refused (multipart framing pushes total body over the equal route cap — the exact defect files-uploads fixed with _files_route_cap headroom); (b) images enforce an in-use-case cap (PAYLOAD_IMAGE_TOO_LARGE) and audio has no equivalent defense-in-depth; (c) nothing structurally prevents a future multipart route from shipping unbounded.
beat: done · next: add status

## RULES
<must>
- M1 A multipart audio file larger than settings.max_audio_upload_bytes on /v1/audio/transcriptions AND /v1/audio/translations is refused with a structured 413 problem code ERR_PAYLOAD_AUDIO_TOO_LARGE — never a raw 500, never a truncated pass-through.
- M2 The oversized-file refusal fires BEFORE the upstream provider call and BEFORE any usage/billing record (the images `_read_capped_upload` "before upstream/bill" placement).
- M3 A raw audio file of EXACTLY max_audio_upload_bytes must reach the handler (not be pre-empted by the edge cap): the /v1/audio/ route cap carries multipart headroom over max_audio_upload_bytes, mirroring _files_route_cap, so the per-file check is the sole decider at the boundary.
- M4 The edge guard stays authoritative for the coarse bound: a total body beyond cap+headroom (or with a lying/absent Content-Length) is still refused 413 ERR_REQUEST_BODY_TOO_LARGE before any handler/governance runs — byte-identical middleware semantics, only the /v1/audio/ number changes.
- M5 A structural check pins the multipart surface: every multipart-consuming route prefix (/v1/audio/, /v1/files, /v1/images via /v1/) resolves to a finite BodySizeLimitMiddleware cap, default_cap is finite, and every upload-field `.read()` in proxy/application is inside an allowlisted capped reader.
- M6 The per-file cap follows the repo's escape-hatch convention: value 0 disables the PER-FILE check only (mirrors image_edit_max_bytes/tts_max_input_characters); the edge cap semantics for 0 are untouched.
</must>
<reject>
- R:UNBOUNDED_READ any request-body read path with no finite bound between edge and buffer — including a future multipart route that ships with no route-cap entry -> "UNBOUNDED_READ"
- R:BILLED_OVERSIZE an oversized upload that produces a usage record or an upstream call -> "BILLED_OVERSIZE"
- R:WEAKENED_EDGE loosening/removing an EXISTING middleware cap ("/v1/", "/admin/", "/v1/files", default_cap) or its lying-Content-Length mid-stream behavior to make in-handler checks reachable -> "WEAKENED_EDGE"
- R:BOUNDARY_THEATER a per-file cap that can never fire in production because the outer route cap pre-empts it at every size (the pre-headroom deadcode shape) -> "BOUNDARY_THEATER"
</reject>

## ASSUMPTIONS
- A1 [who] covers: S1 · the request does not say whose uploads are capped; taking: the cap is global (per-request, all tenants/keys alike — a Settings knob, not a plan entitlement) · probe: no tenant/plan lookup in the cap path -> if wrong (plan-tiered upload sizes wanted), the knob moves to entitlements later; the global cap stays the floor
- A2 [which] covers: S1 · the request does not say which endpoints are in; taking: the two multipart STT surfaces (transcriptions + translations — same use case, upstream_path switch); TTS /v1/audio/speech is JSON-body (already under /v1/ JSON cap + tts_max_input_characters) and is OUT · probe: translations exercised by its own check -> if wrong, speech needs its own audit later; nothing here touches it
- A3 [when] covers: S1 · the request does not say where the boundary falls; taking: len(file_bytes) > max_audio_upload_bytes rejects; EXACTLY equal passes (strict >, the images helper's comparison) · probe: exact-cap file reaches the handler (M3 check) -> if wrong, off-by-one at 25 MiB — visible, cheap to flip
- A4 [absent] covers: S1 · the request does not say what cap=0 means; taking: 0 disables the per-file check only (images/tts convention), while the edge route cap for 0 falls back to a large finite ceiling via the headroom helper (the _files_route_cap 0-branch precedent — NEVER unlimited) · probe: helper unit branch -> if wrong, an operator setting 0 to "disable" would block everything at the edge (cap 0+headroom) — the finite-ceiling branch prevents exactly that
- A5 [order] covers: S1 · the request does not say where in the pipeline the per-file check sits; taking: at the existing Step-6 read (post-governance-authorize, pre-upstream, pre-bill) — moving the read before governance would reorder a FROZEN sibling flow for zero security gain (the full body is already buffered by form() under the edge cap either way) · probe: M2's check asserts zero upstream calls AND zero usage records on reject -> if wrong (pre-governance wanted), it is a one-line move, but it would touch the frozen transcription flow contract
- A6 [experience] covers: S1 · the request does not say what the refused caller sees; taking: RFC 9457 problem+json with a distinct code (ERR_PAYLOAD_AUDIO_TOO_LARGE) so an SDK can tell "shrink the file" from the coarse edge ERR_REQUEST_BODY_TOO_LARGE · probe: M1 checks assert the code string -> if wrong, one catalog rename pre-release
- A7 [who] covers: S2 · the request does not say who maintains the sweep; taking: the allowlist lives IN the structural test with a SANCTIONED-EDIT comment convention (the table-manifests pattern) — a new `.read()` site fails CI until it is either routed through a capped reader or explicitly allowlisted · probe: the sweep is a repo-source scan, not a runtime probe -> if wrong (too noisy), the allowlist loosens to per-module granularity
- A8 [which] covers: S2 · the request does not say which reads count; taking: `await <field>.read()` on multipart/upload fields under src/gateway/proxy/application + files/api (request-body reads); object-store/provider-response reads are OUT (not request bodies) · probe: sweep test names its population -> if wrong, the population widens in a follow-up; the pattern is additive
- A9 [when] covers: S2 · n/a · the structural pins are stateless source/config asserts — no timing dimension exists to sweep
- A10 [absent] covers: S2 · the request does not say what happens if the middleware entry vanishes; taking: the structural test FAILS (that is its job — R:UNBOUNDED_READ's tripwire), it does not silently re-derive a cap -> if wrong, nothing: fail-loud is the point
- A11 [order] covers: S2 · n/a · no ordering among the structural asserts is meaningful
- A12 [experience] covers: S2 · the request does not say what a developer who trips the sweep should experience; taking: the failure message NAMES the offending file:line + enclosing function and states the remedy (bound the read, then allowlist with a SANCTIONED EDIT comment citing the bounding task) — a tripped guard must teach, not just refuse · probe: the sweep assert's message carries the violation list -> if wrong (too terse), the message grows; the gate is unaffected

## PLAN
contract: TranscriptionUseCase gains keyword-only `max_file_bytes: int = 0` (0=off, legacy/test parity; prod DI injects settings.max_audio_upload_bytes) enforced at the Step-6 read via a local `_read_capped_upload`-mirror raising PAYLOAD_AUDIO_TOO_LARGE (new ErrorSpec 413 "ERR_PAYLOAD_AUDIO_TOO_LARGE" in core/error_catalog.py, named per PAYLOAD_IMAGE_TOO_LARGE). main.py: `_audio_route_cap(max_audio_upload_bytes)` = cap + 1 MiB headroom (0 -> the large finite ceiling), replacing the bare cap in route_caps. audio_deps DI passes the knob. New suite tests/upload_bounds/ (structural sweep + boundary tests reusing the audio_endpoints FakeAudioProvider harness).
scope: apps/gateway/src/gateway/proxy/application/audio_use_case.py · proxy/api/audio_deps.py · core/error_catalog.py · main.py · tests/upload_bounds/

## EDGES
- E1 oversized file on /v1/audio/transcriptions -> 413 ERR_PAYLOAD_AUDIO_TOO_LARGE
- E2 oversized file on /v1/audio/translations -> same refusal (shared use case, both routes proven)
- E3 raw file of exactly max_audio_upload_bytes -> passes the edge, reaches the handler (headroom)
- E4 total body over cap+headroom -> 413 ERR_REQUEST_BODY_TOO_LARGE at the edge, zero handler/governance work (regression pin on M4)
- E5 reject leaves zero upstream calls and zero usage records (fake provider + recorder both untouched)
- E6 max_file_bytes=0 -> per-file check disabled; helper returns the finite-ceiling route cap (never unlimited)
- E7 a hypothetical uncapped `.read()` site / missing route-cap entry -> the structural sweep test fails (proven red by construction during authoring)

## CHECKS
- test_oversized_transcription_413_audio_code · covers: M1, M3, A1, A3, A6, E1, R:BOUNDARY_THEATER · a file of max_audio_upload_bytes+1 (body within cap+headroom) gets 413 ERR_PAYLOAD_AUDIO_TOO_LARGE from the per-file check — RED today: the equal route cap pre-empts with ERR_REQUEST_BODY_TOO_LARGE
- test_oversized_translation_413_audio_code · covers: M1, A2, E2 · same refusal on /v1/audio/translations
- test_exact_cap_file_reaches_handler · covers: M3, A3, E3 · a raw file of exactly the cap is NOT refused by the edge; the faked upstream answers 200 — RED today (framing overflows the equal cap)
- test_oversized_rejected_before_upstream_and_billing · covers: M2, A5, E5, R:BILLED_OVERSIZE · on the per-file reject, FakeAudioProvider.post_multipart_calls == [] and the usage recorder call_count == 0
- test_edge_cap_still_authoritative_beyond_headroom · covers: M4, E4, R:WEAKENED_EDGE · a body over cap+headroom is refused 413 ERR_REQUEST_BODY_TOO_LARGE with zero governance/provider work (pins the edge layer against weakening)
- test_cap_zero_disables_per_file_check_only · covers: M6, A4, E6 · with max_file_bytes=0 an over-cap read passes the per-file check (helper-level), and _audio_route_cap(0) returns the finite ceiling, never 0/unlimited
- test_multipart_surface_structurally_bounded · covers: M5, A7, A8, A10, A12, E7, R:UNBOUNDED_READ · route_caps contains a finite entry for every multipart prefix + finite default_cap, AND every upload-field `.read()` under proxy/application sits inside an allowlisted capped reader (fails on a new unlisted site)
red-first: every check MUST fail first.

## EVIDENCE
receipt: <runs/<n>.md>
gate: <PASS | RISK-ACCEPTED | HARD-STOP>

## LESSONS
- <lesson> -> add learn <lens>
