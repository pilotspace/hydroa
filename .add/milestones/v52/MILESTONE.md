# MILESTONE: Full-duplex realtime voice relay (provider-agnostic seam)

goal: A browser holds a full-duplex voice session over a provider-agnostic /v1/realtime/relay WS that is relayed bidirectionally to a real provider realtime API (OpenAI Realtime or Gemini Live) behind a swappable seam, with auth-over-WS, design-for-failure on the pump, and honest-degrade when no provider is configured.
rationale: sub-milestone — the SECOND of two "build the deferred items FOR REAL" micro-milestones Tin ordered (v51 artifacts→MinIO DONE; this is v52 realtime, before video-jobs-backend). Builds the full-duplex provider relay v47 explicitly rejected as a scale-delta. Decisions locked with Tin: NORMALIZED provider-agnostic seam (not transparent passthrough) + a NEW `/v1/realtime/relay` endpoint (v47 `/v1/realtime` turn-based loop untouched); BOTH OpenAI Realtime + Gemini Live adapters; live-verify credential-gated (no key → adapters ship code-complete with stubbed/unit tests).
stage: production · status: active · created: 2026-06-26

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  A NEW provider-agnostic full-duplex relay: a `RealtimeRelaySession` seam (Protocol) + a normalized gateway WS frame protocol + a bidirectional PUMP with design-for-failure · TWO adapters (OpenAI Realtime, Gemini Live) translating gateway frames ↔ provider-native events · a NEW `/v1/realtime/relay` WS endpoint (first-frame auth-over-WS reused from v47) selecting the configured provider, honest-degrade when none configured · Envoy WS-upgrade config for the relay path · unit/stub tests for the seam + both adapters + the endpoint · a skip-gated, credential-gated live-verify harness.
Out: real LIVE provider verification (needs OpenAI/Gemini realtime API keys — HARD-STOP, ships SKIPPED) · per-second/token BILLING & usage metering of relayed audio (relay usage events through; metering = a documented scale delta) · the v47 turn-based `/v1/realtime` loop (untouched) · a browser/UI client (dashboard work, separate) · transcoding/resampling audio formats (pass provider-native codecs through the seam).

## Shared decisions & glossary deltas   (living — every task must honor these)
- RELAY SEAM (NEW glossary): `RealtimeRelaySession` = a provider adapter Protocol — `connect()` (with timeout) → `send_client_event(frame)` / `send_audio(bytes)` → async-iter `provider_events()` normalized to gateway frames → `aclose()`. The endpoint owns the PUMP; the adapter owns provider-protocol translation only.
- NORMALIZED FRAME PROTOCOL (HARD, Tin): the client speaks ONE gateway realtime frame protocol (JSON control frames + audio bytes); adapters translate to/from provider-native events. The client NEVER sees provider wire format — swap providers without a client change.
- AUTH-OVER-WS (HARD, security — inherited from v47): no frame other than the first `{type:"auth",token}` frame is processed until authenticated; bad/missing/expired token OR auth timeout → close (4401/4408); token NEVER in the URL/query. Reuse v47 `_authenticate_token` (KeyAuthenticator + expiry).
- DESIGN-FOR-FAILURE (HARD): provider connect has a timeout; the bidirectional pump runs two cancel-safe relay tasks; a provider disconnect → close the client with a typed code (+ drain); a client disconnect → cancel in-flight + close the provider (no leaked task/socket); a bounded send queue (backpressure); any adapter raise → an `{type:"error"}` frame, never a 500/hang.
- HONEST-DEGRADE (HARD): if no realtime provider is configured (no key/disabled), `/v1/realtime/relay` closes with a typed code (e.g. 4404 "no realtime provider configured") — never a silent hang or a fake session. The v47 turn-based path stays the configured-nothing fallback surface.
- CREDENTIAL-GATED LIVE-VERIFY (Tin): adapters ship code-complete + unit-tested against FAKE provider sockets; the live test is skip-gated on the provider key and ships SKIPPED (a documented HARD-STOP, not a silent gap).

## Shared / risky contracts (freeze these first)
- `RealtimeRelaySession` seam Protocol + the normalized gateway frame protocol + relay config knobs -> owning task `realtime-relay-port` (frozen first; both adapters + the endpoint consume it).

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] realtime-relay-port      depends-on: none                — NEW `RealtimeRelaySession` Protocol + normalized gateway frame protocol + a bidirectional PUMP engine with design-for-failure (connect timeout · two cancel-safe relay tasks · provider/client disconnect handling · bounded backpressure · error-frame-not-500). Unit-tested with a FAKE provider session. FREEZES the seam + frame + config contract.
- [ ] openai-realtime-adapter  depends-on: realtime-relay-port — `OpenAIRealtimeSession` implementing the seam over the OpenAI Realtime WS, translating gateway frames ↔ OpenAI events (session.update · input_audio_buffer.append/commit · response.audio.delta · …). Unit-tested against a fake OpenAI socket; NO live key.
- [ ] gemini-live-adapter      depends-on: realtime-relay-port — `GeminiLiveSession` over the Gemini Live (BidiGenerateContent) WS, translating gateway frames ↔ Gemini Live messages. Unit-tested against a fake Gemini socket; NO live key.
- [ ] realtime-relay-endpoint  depends-on: openai-realtime-adapter, gemini-live-adapter — NEW `/v1/realtime/relay` WS handler: first-frame auth → select the configured provider adapter → run the pump; honest-degrade (no provider → typed close). Envoy WS-upgrade config for the path; main.py include. WS unit tests via Starlette TestClient + fake adapters.
- [ ] realtime-relay-live-verify depends-on: realtime-relay-endpoint — skip-gated + credential-gated live harness (real OpenAI/Gemini realtime) proving the full-duplex round-trip; ships SKIPPED without a key (documented HARD-STOP), ready to run when a key exists.

## Exit criteria (observable; map each to the task that delivers it)
- [x] A client opens `/v1/realtime/relay`, authenticates via the first frame, and exchanges normalized control + audio frames that are pumped bidirectionally to a provider session — proven with a FAKE provider socket end-to-end (audio in → provider → audio-delta frames out)   (verify: realtime-relay-endpoint test_authed_full_duplex_relay)
- [x] The seam is provider-agnostic: BOTH `OpenAIRealtimeSession` and `GeminiLiveSession` satisfy the `RealtimeRelaySession` Protocol and translate the same gateway frames to/from their provider-native events — proven by per-adapter unit tests against fake provider sockets   (verify: openai-realtime-adapter + gemini-live-adapter test_satisfies_seam_protocol)
- [x] Design-for-failure holds: a provider connect timeout / provider disconnect / client disconnect / adapter raise each yields a typed close or `{error}` frame with no leaked task or socket, never a 500 or hang   (verify: realtime-relay-port pump tests 4503/1011/4408/1000 + no-leaked-task assertions)
- [x] Auth-over-WS + honest-degrade hold: no frame is relayed before auth (bad/expired/timeout → 4401/4408, token never in URL); no provider configured → typed close (4404), never a silent hang   (verify: realtime-relay-endpoint test_bad_token_4401/_auth_timeout_4408/_url_token_ignored_4401/_no_provider_4404)
- [x] The live full-duplex round-trip harness exists and is skip-gated on the provider key — it SKIPS cleanly without a key (documented HARD-STOP for real verification) and is ready to run when one is provided   (verify: realtime-relay-live-verify test_relay_live SKIPPED)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway (proxy/domain)         : NEW `realtime_relay.py` — the FROZEN seam: `RealtimeRelaySession` + `RealtimeClientTransport` Protocols, `ControlFrame`/`RelayFrame` (dict=control, bytes=audio), typed errors.
- gateway (proxy/application)    : NEW `realtime_relay_pump.py` — `RelayPump` (breaker+timeout connect → 2 cancel-safe tasks → typed-close teardown; NO send queue per Tin's freeze).
- gateway (proxy/infrastructure) : NEW `realtime_ws_client.py` (`RealtimeWebSocket` seam + `connect_websocket`, transitive `websockets`, no new dep) · NEW `openai_realtime.py` (`OpenAIRealtimeSession`) · NEW `gemini_live.py` (`GeminiLiveSession`).
- gateway (proxy/api)            : NEW `realtime_relay_ws.py` — `/v1/realtime/relay` WS handler (auth-over-WS → provider select → pump; honest-degrade 4404) + `_StarletteRelayTransport`.
- gateway (core/config + main)   : 5 `realtime_relay_*` knobs; `realtime_relay_router` included.
- infra (envoy)                  : `upgrade_configs:[websocket]` added to envoy.yaml (:8080+:8443) + envoy-prod.yaml (:8443) — enables WS at the edge for BOTH /v1/realtime paths.
- tests                          : NEW `tests/realtime_relay/` (pump · openai · gemini · endpoint · live) — 31 passed + 1 skipped; Makefile test-fast extended.
- tooling/skill/book             : untouched.

### Cross-task evidence   (one row per task)
- realtime-relay-port        : gate=PASS · tests=11 green · residue=none (send-queue dropped per Tin's freeze)
- openai-realtime-adapter    : gate=PASS · tests=6 green · residue=transcript event-name flagged (t5 live oracle)
- gemini-live-adapter        : gate=PASS · tests=7 green · residue=v1beta field names flagged (tolerant nav + t5 oracle); SECURITY F1 (key-in-URL) FIXED here post-review
- realtime-relay-endpoint    : gate=PASS (Tin security sign-off) · tests=6 green · residue=real key-resolution + Envoy not CI-exercised (t5/deploy paths)
- realtime-relay-live-verify : gate=PASS · tests=1 skipped (credential-gated) · residue=none

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row (EC1/EC4←endpoint, EC2←adapters, EC3←port, EC5←live-verify); independent security-expert review BLOCK (F1 Gemini key-in-URL) resolved + regression-tested; Tin signed off the auth-over-WS gate.
- goal: a browser holds a full-duplex voice session over a provider-agnostic `/v1/realtime/relay` WS relayed bidirectionally to a real provider (OpenAI Realtime / Gemini Live) behind a swappable seam, with auth-over-WS + design-for-failure + honest-degrade. PROVEN: the endpoint pumps a fake provider session end-to-end (`test_authed_full_duplex_relay`), both adapters satisfy the same seam, and the live round-trip harness is ready (skip-gated) for a real key.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] commit the v52 work on a feature branch (gateway relay seam + adapters + endpoint + Envoy + ledger) — message per CLAUDE.md format
- [ ] open a PR from the Close ship-review above; Tin reviews + merges (note the security review + F1 fix in the PR body)
- [ ] (deploy-time) set `GATEWAY_REALTIME_RELAY_PROVIDER` + the provider key to activate; run the t5 live-verify with `GATEWAY_REALTIME_RELAY_LIVE=1` to prove the real round-trip
- [ ] fold + archive v52 (consolidate the 8 open deltas), then bundle into the next release cut (human-run, per release.md)
