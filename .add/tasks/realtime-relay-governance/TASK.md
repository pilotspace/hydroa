# TASK: B2: realtime relay authz/rate-limit + usage/audit rows

slug: realtime-relay-governance · created: 2026-07-02 · stage: production
autonomy: auto
phase: done

> One file = one task. Fill sections top-to-bottom; the `add` skill drives each phase.
> When a phase is unclear, read its book chapter in `.add/docs/` (linked per section).
> The phase marker above is the single source of truth — keep it in sync via `add.py phase`.

---

## 0 · GROUND — the real codebase ▸ docs/02-the-flow.md

Touches (files · symbols · signatures):
  - `apps/gateway/src/gateway/proxy/api/realtime_relay_ws.py:realtime_relay` — the WS handler. Today: `accept()` → first-frame auth-over-WS (`_authenticate`, unchanged, 4401/4408) → `_build_session` (stub or `_real_session_factory`, 4404 honest-degrade) → `RelayPump.run()`. NO governance (allowlist/catalog/budget/RPM/TPM) runs between a successful `_authenticate` and the provider dial — this is the gap.
  - `apps/gateway/src/gateway/proxy/api/realtime_relay_ws.py:_make_relay_usage_callback` — the per-turn usage-capture callback already wired for OpenAI (built `gpt-realtime-relay-billing`, MERGED to main, `usage_source="realtime_relay"`). Calls `usage_recorder.record(tenant_id, key_id, model, usage, status=200, usage_source=...)` — **omits `team_id`** (every other governed path passes `team_id=authz.team_id`; this is a silent team-budget-attribution gap).
  - `apps/gateway/src/gateway/proxy/api/realtime_relay_ws.py:_real_session_factory` — builds `OpenAIRealtimeSession(..., on_usage=_make_relay_usage_callback(...))` but builds `GeminiLiveSession(...)` with **no `on_usage` at all** — confirmed by reading `gemini_live.py`'s `__init__` (no `on_usage` param exists there today). Gemini relay spend is currently **categorically unmetered** whenever `settings.realtime_relay_provider == "gemini"`.
  - `apps/gateway/src/gateway/proxy/infrastructure/openai_realtime.py:OpenAIRealtimeSession.events` / `._translate_realtime_usage` (staticmethod) — the SHIPPED per-turn capture shape: on `response.done`, if `on_usage` is set and `usage` is a dict, translates OpenAI's `input_token_details`/`output_token_details` into the recorder-canonical shape and awaits `on_usage(...)`, swallowing any exception (never disrupts the relay). This is the exact shape Gemini's fix must mirror.
  - `apps/gateway/src/gateway/proxy/infrastructure/gemini_live.py:GeminiLiveSession.__init__` / `.events` / `._translate_server_message` (staticmethod) — NO `on_usage` param; `_translate_server_message` never reads any usage/token field from a Gemini `serverContent`/`turnComplete` message today (only `modelTurn.parts`, `turnComplete`, `error`). Whether Gemini's Live (BidiGenerateContent) protocol even EXPOSES a per-turn usage field, and under what key, is **unconfirmed by this codebase** — see the §1 ⚠ top assumption.
  - `apps/gateway/src/gateway/proxy/application/realtime_relay_pump.py:RelayPump.__init__` / `._client_to_provider` — owns connect/breaker/teardown; takes an optional `breaker: CircuitBreaker | None = None`. The client→provider loop sends every frame (control or audio) with NO throughput pacing — no bandwidth-bucket integration exists here, unlike `CompletionUseCase`'s streaming loop.
  - `apps/gateway/src/gateway/proxy/application/governance.py:NonChatGovernance` / `.authorize(raw_key, model_id, *, estimated_tokens=None)` — the ALREADY-SHIPPED, REUSED-BY-STT/TTS nine-step governance gate (auth → expiry → allowlist → catalog → per-key budget → team budget → tenant budget → RPM → TPM). Constructed identically in `apps/gateway/src/gateway/proxy/api/realtime_ws.py:_real_stt`/`_real_chat`/`_real_tts` (the v47 turn-based sibling) — that endpoint runs this FULL gate on **every single turn**; the relay endpoint runs NONE of it, ever, for the life of a session. `estimated_tokens=None` deliberately skips the TPM step (Step 9) when the caller has no token estimate — already the documented behavior for embeddings/images/audio, not a new invention.
  - `apps/gateway/src/gateway/core/error_catalog.py:AUTH_KEY_INVALID/AUTH_KEY_EXPIRED(401)`, `MODEL_UNKNOWN(400)`, `MODEL_NOT_ALLOWED/MODEL_DISABLED(403)`, `BUDGET_EXCEEDED(402)`, `RATE_LIMITED(429)` — `ErrorSpec.exc()` raises `gateway/core/errors.py:ProblemError(status, code, title, ...)`; `.status` is a plain `int` attribute readable from the caught exception.
  - `apps/gateway/src/gateway/rate_limits/application/passthrough.py:PassthroughBandwidthBucket` and `apps/gateway/src/gateway/rate_limits/domain/ports.py:BandwidthBucket.acquire(key_id, estimated_tokens, max_wait_s) -> BandwidthGrant` / `BandwidthExhaustedError` (`rate_limits/domain/errors.py`) — the v36 per-key throughput primitive `CompletionUseCase` already paces streaming chunks through (`use_cases.py:2092-2095`); FAIL-OPEN on Redis error; default-OFF (`Settings.bandwidth_tokens_per_sec = 0` ⇒ `PassthroughBandwidthBucket` is wired at `deps.py:189-194`).
  - `apps/gateway/src/gateway/audit/domain/audit_event.py:AuditEvent` (frozen `audit-log-store` contract) — `__post_init__` raises `audit_missing_actor` when `tenant_id is not None and actor_user_id is None`. `AuthzResult` (`keys/domain/entities.py`) carries `tenant_id`/`key_id` but **no user identity at all** — a key-authenticated relay session structurally cannot supply `actor_user_id`. Existing precedent for this exact fork: `platform_tenants_router`'s bulk tenant-list audit event and `ops.platform_credential_resolve` both use `tenant_id=None` ("system-level event") to stay inside the frozen invariant (`apps/gateway/tests/admin_console_audit/test_admin_console_audit.py:242-262`).
  - `apps/gateway/src/gateway/audit/application/audit_writer.py:record_audit(session_factory, event)` — fire-and-forget, own session, swallows all exceptions; the established call shape (`provider_keys_admin_router.py:237-249`): `asyncio.ensure_future(record_audit(app.state.sessionmaker, AuditEvent(id=uuid4(), tenant_id=..., actor_user_id=..., actor_email=..., action=..., target_type=..., target_id=..., result=..., metadata={...}, created_at=...)))`.
  - `infra/envoy/envoy.yaml` and `infra/envoy/envoy-prod.yaml` (local/e2e dev stack) — route `/v1/*` (prefix) uniformly through `ext_authz` with NO carve-out for `/v1/realtime/`. **Confirmed DRIFT**: `charts/ai-proxy/templates/envoy-configmap.yaml:126-138` (the K8s/prod Helm chart, already deployed) has an explicit `{ match: { prefix: "/v1/realtime/" } }` route with `ext_authz: { disabled: true }`, placed BEFORE the general `/v1/` rule, with a comment explaining exactly why: a browser `WebSocket` client cannot set an `Authorization` header on the upgrade GET, so ext_authz would 401 the handshake before Envoy proxies it, and in-band auth-over-WS is meant to be the SOLE authenticator on this path. The two dev-stack files never got this carve-out — a pre-existing bug this ground pass surfaced, not introduced by this task.
  - `apps/gateway/tests/realtime_relay/test_carveout_invariant.py:test_only_websocket_routes_under_realtime_carveout` / `test_relay_ws_is_under_the_carveout` — FROZEN, already-shipped guard tests (from `e2e-platform-features` v53) pinning that carve-out invariant against the app's route table; both MUST stay green, unmodified, after this task.
Context (working folder):
  - `.add/tasks/gpt-realtime-relay-billing/TASK.md` — DONE + MERGED: froze **per-turn** billing (one `usage_records` row per `response.done`, never a session-aggregate) specifically BECAUSE Tin rejected session-aggregation via `AskUserQuestion` (2026-07-02): an abnormally-terminated session would lose ALL usage under aggregation. Any Gemini usage-capture design in THIS task must mirror the same per-turn shape, not accumulate-and-flush-at-close.
  - `.add/tasks/realtime-relay-endpoint/TASK.md` (DONE, FROZEN @ v1) and `.add/tasks/realtime-relay-port/TASK.md` — the frozen `RealtimeRelaySession`/`RealtimeClientTransport` Protocols and the auth-over-WS (4401/4404/4408) contract THIS task must not contradict; both already flag their own Envoy edits as "not unit-exercised, no live Envoy in CI" — this task's Envoy fix inherits the same honest gap.
  - `apps/gateway/src/gateway/proxy/api/realtime_ws.py` (v47, turn-based `/v1/realtime`) — the existing PARITY TARGET: every turn runs the full `NonChatGovernance.authorize()` gate via a freshly-built `NonChatGovernance` instance inside `_real_stt`/`_real_chat`/`_real_tts`, using `app.state.budget_guard`, `app.state.rate_limiter`, and a Redis handle pulled off the budget guard (`getattr(_budget_guard, "_redis", None)`) — the exact construction pattern this task's relay endpoint should reuse for its ONE connect-time governance call.
Honors (patterns / conventions):
  - ONE SHARED GOVERNANCE GATE (embeddings-endpoint / images-endpoint / audio-endpoints / v47 realtime precedent): `NonChatGovernance` is the single reusable predicate for every non-chat-completions modality; a second hand-rolled copy of allowlist/budget/RPM checks in the relay handler would violate this project's own established doctrine.
  - CLOSE-CODE CONVENTION (realtime-relay-endpoint v1, already shipped): a WS close code is `4000 + <the HTTP status the same rejection would produce over REST>` — `4401`, `4404` (bespoke, no REST equivalent), `4408` (bespoke, no REST equivalent) are already live. This generalizes MECHANICALLY for `ProblemError`-backed rejections (`4000 + exc.status`) rather than a hand-maintained table that can drift from `error_catalog.py`.
  - FAIL-OPEN redis-backed checks (budget/RPM/TPM/bandwidth) degrade to "admit" on Redis error, matching the SRE persona's fail-open doctrine already proven across every existing governance gate — never adding a NEW opaque-failure mode.
  - AUDIT fire-and-forget + swallow-all (`record_audit`), NEVER gates the guarded action's own outcome — a relay session's open/close/reject is never blocked or delayed by an audit write.
  - `PROJECT.md` DDD fold ("pass-through is not capability-neutral... research-before-build") and the SRE persona's "assumption about external tooling behavior is re-validated LIVE" rule — both apply directly to the Gemini usage-field assumption below.
Anchors the contract cites: `NonChatGovernance.authorize` · `ProblemError.status` · `realtime_relay_ws.py:realtime_relay` / `_real_session_factory` / `_make_relay_usage_callback` · `OpenAIRealtimeSession.__init__(on_usage=...)` (the shape to mirror) · `GeminiLiveSession.__init__` (gains `on_usage`) · `RelayPump.__init__` (gains `bandwidth_bucket`) · `BandwidthBucket.acquire` / `BandwidthExhaustedError` · `AuditEvent` / `record_audit` · `infra/envoy/envoy.yaml` / `envoy-prod.yaml` (the `/v1/realtime/` carve-out to add) · `charts/ai-proxy/templates/envoy-configmap.yaml:126-138` (the block to byte-mirror) · `test_carveout_invariant.py` (must stay green)

---

## 1 · SPECIFY — the rules ▸ docs/03-step-1-specify.md

Feature: Bring `/v1/realtime/relay` to governance parity with every other `/v1` surface — authz
  (allowlist/catalog/budget/RPM), usage metering (close the Gemini unmetered-spend gap + a
  team-attribution gap), bandwidth pacing, audit visibility, and an Envoy edge-config drift fix —
  without touching the frozen auth-over-WS handshake or the frozen relay-seam Protocols.
Framings weighed:
  - **(chosen)** Reuse `NonChatGovernance.authorize()` ONCE at connect-time (after auth-over-WS
    succeeds, before the provider session is built), keyed on the DIALED model
    (`settings.realtime_relay_openai_model`/`realtime_relay_gemini_model`); mirror the ALREADY-SHIPPED
    OpenAI `on_usage` callback shape onto `GeminiLiveSession` (additive constructor param, no Protocol
    widening); add an optional `bandwidth_bucket` param to `RelayPump` mirroring its existing optional
    `breaker` param; add fire-and-forget `AuditEvent` lifecycle events via the existing `record_audit`;
    fix the confirmed Envoy carve-out drift. Chosen because every piece reuses an ALREADY-PROVEN
    primitive from this exact codebase — zero new governance machinery, matching the "one shared gate"
    doctrine.
  - **(rejected)** Reimplement allowlist/budget/RPM checks inline in `realtime_relay_ws.py` — a second
    hand-rolled copy of `NonChatGovernance`'s nine steps, the exact anti-pattern this project's own
    shared-predicate doctrine exists to prevent (two copies drift when one is edited without the other).
  - **(rejected)** Session-aggregate usage capture (`session.usage()` read once after `pump.run()`
    completes, accumulating turns in the adapter) — this SHAPE was explicitly weighed and REJECTED
    already, for the OpenAI path, at the `gpt-realtime-relay-billing` freeze (Tin, `AskUserQuestion`,
    2026-07-02): an abnormally-terminated session (crash/network-drop) would lose ALL usage for that
    session, a strictly worse revenue-leak surface than per-turn. Gemini's fix must mirror the SAME
    per-turn `on_usage` callback shape already shipped for OpenAI, not reopen a settled decision.
Must:
<must>
  - **M1 (authz parity)**: After auth-over-WS succeeds (existing `_authenticate` call, 4401/4408
    outcomes byte-identical, UNCHANGED) and BEFORE `_build_session` is called, construct a
    `NonChatGovernance` the same way `realtime_ws.py:_real_stt` does (`app.state.budget_guard`,
    `app.state.rate_limiter`, a Redis handle, `app.state.sessionmaker`) and call
    `.authorize(raw_key=token, model_id=<dialed model>, estimated_tokens=None)`. The dialed model is
    resolved by the SAME `settings.realtime_relay_provider` selector `_real_session_factory` already
    uses (`openai` → `realtime_relay_openai_model`, `gemini` → `realtime_relay_gemini_model`); an
    unconfigured provider is UNCHANGED (falls through to the existing 4404 honest-degrade, governance
    never runs for a provider that doesn't exist). `estimated_tokens=None` deliberately skips the TPM
    pre-flight (Step 9) — there is no token estimate for a continuous audio stream (see Reject).
  - **M2 (close-code mapping, mechanical)**: A `ProblemError` raised by `.authorize()` closes the
    socket with `4000 + exc.status` (`4400` MODEL_UNKNOWN · `4402` BUDGET_EXCEEDED · `4403`
    MODEL_NOT_ALLOWED/MODEL_DISABLED · `4429` RATE_LIMITED) — computed from `exc.status`, NEVER a
    hand-maintained parallel table that could drift from `error_catalog.py`. No provider session is
    built when governance raises (extends the existing "no session before auth" invariant to "no
    session before governance").
  - **M3 (Gemini usage-metering gap)**: `GeminiLiveSession.__init__` gains an additive
    `on_usage: Callable[[dict[str, Any]], Awaitable[None]] | None = None` parameter, mirroring
    `OpenAIRealtimeSession`'s ALREADY-SHIPPED shape verbatim (never widens the `RealtimeRelaySession`
    Protocol). `_real_session_factory` wires the SAME `_make_relay_usage_callback(...)` into
    `GeminiLiveSession` that OpenAI already receives (currently: only OpenAI gets one). When a Gemini
    turn-boundary server message (the same message `_translate_server_message` reads to emit
    `{"type":"response.done"}`) carries a per-turn usage/token field, translate it into the SAME
    recorder-canonical shape `_translate_realtime_usage` produces for OpenAI (`prompt_tokens`,
    `completion_tokens`, nested `*_token_details`), degrading any absent/non-numeric sub-field to 0
    (never raising, never fabricating a non-zero estimate). Any capture/record failure is swallowed +
    logged, exactly like the shipped OpenAI path — NEVER disrupts the live relay session. Reuses the
    write-behind pipe verbatim (`usage_recorder.record()` → Redis Stream `usage:events` →
    `UsageLedgerFlusher`); `usage_source="realtime_relay"` (the SAME discriminator OpenAI already uses
    — `model_id` disambiguates provider, no new discriminator value needed).
  - **M4 (team-budget attribution fix)**: `_make_relay_usage_callback`'s call to
    `usage_recorder.record(...)` gains `team_id=authz.team_id` (currently omitted) — every other
    governed path passes it; without it, a team-budgeted key's relay usage is billed but never counted
    against the team spend counter `NonChatGovernance`'s Step 6 reads. Applies to BOTH providers (the
    fix lives in the ONE shared callback builder, not per-adapter).
  - **M5 (bandwidth pacing)**: `RelayPump.__init__` gains an additive
    `bandwidth_bucket: BandwidthBucket | None = None` (mirrors the existing optional `breaker` param),
    defaulting to `PassthroughBandwidthBucket()` when absent (byte-identical / zero pacing — matches
    the v36 bandwidth-token-bucket default-OFF precedent). When configured, `_client_to_provider` paces
    each AUDIO frame (never control frames) via `bandwidth_bucket.acquire(key_id, len(frame),
    max_wait_s)` before `send_audio` — the SAME per-chunk pacing shape `CompletionUseCase`'s streaming
    loop already uses. `key_id` (`authz.key_id`) and `max_wait_s` (`settings.bandwidth_max_wait_seconds`
    — the SAME setting the chat path already reads) are threaded in from the endpoint via `RelayPump`'s
    constructor, not re-derived. `BandwidthExhaustedError` closes 4429 (the SAME code as a connect-time
    RATE_LIMITED rejection — both mean "you're going too fast"), kept DISTINCT from the pump's existing
    4503 provider-unavailable path (a self-imposed pace cap is not "the provider is down").
  - **M6 (audit lifecycle events)**: Emit fire-and-forget `AuditEvent`s via the EXISTING
    `record_audit()` (no migration, no new table) at three points: `realtime_relay.session_opened`
    (the moment M1 governance PASSES, before the provider dial), `realtime_relay.session_closed`
    (after `pump.run()` returns; metadata carries the pump's close code), and
    `realtime_relay.session_rejected` (a governance rejection from M1/M2 — identity is already known
    at this point). Metadata carries `{"provider": ..., "model": ...}` plus event-specific fields
    (close code / rejection reason) — NEVER token/key material. A PRE-IDENTITY rejection (bad/missing/
    expired token, auth timeout — the EXISTING 4401/4408 paths) is NOT audited: no tenant is confirmed
    at that point to scope the event to (consistent with how a failed API-key auth elsewhere in this
    app is not audit-logged either). The actor-scoping SHAPE (`tenant_id=None` system-level event vs. a
    new key-actor field) is the freeze fork below — Tin decides, this task does not.
  - **M7 (Envoy drift fix)**: `infra/envoy/envoy.yaml` and `infra/envoy/envoy-prod.yaml` gain a
    `{ match: { prefix: "/v1/realtime/" } }` route (ext_authz disabled), placed BEFORE the existing
    general `/v1/` rule (first-match-wins), byte-mirroring the ALREADY-SHIPPED
    `charts/ai-proxy/templates/envoy-configmap.yaml:126-138` block. A PRE-EXISTING bug this task's
    ground pass surfaced (the local/e2e dev stack currently lacks the carve-out the K8s/prod chart
    already has) — not introduced by this task, fixed because it is directly the "verify in-process
    parity vs. what the edge already does" question this task exists to answer.
  - **M8 (frozen invariants unchanged)**: `test_carveout_invariant.py`'s two existing tests stay green,
    unmodified — this task adds no non-WS route under `/v1/realtime/`. The `RealtimeRelaySession` /
    `RealtimeClientTransport` Protocols (`domain/realtime_relay.py`) are UNCHANGED — every new
    capability (usage capture, bandwidth pacing) rides an ADDITIVE constructor parameter on a concrete
    adapter/pump, never a Protocol method. The v47 `/v1/realtime` turn-based path and the auth-over-WS
    4401/4404/4408 contract stay byte-identical.
</must>
Reject:
<reject>
  - governance PASSES identity (auth-over-WS) but FAILS allowlist/catalog/budget/RPM ->
    close `4000 + exc.status` (M2's table); NO provider session opened; nothing relayed ->
    `governance_rejected` (also produces the M6 `session_rejected` audit row)
  - a continuous audio relay session has no discrete token estimate ahead of time -> the TPM
    pre-flight (Step 9) is DELIBERATELY skipped (`estimated_tokens=None`), documented here and in the
    contract, never silently — the bandwidth pacer (M5) is the actual byte/throughput governance
    surface for an open relay session, TPM is not
  - a Gemini turn-boundary message carries NO usage/token field (either because Gemini Live's
    BidiGenerateContent protocol has none, or this particular turn omits it) -> record NOTHING for
    that turn, logged at DEBUG `gemini_usage_absent_skip` (mirrors the shipped OpenAI
    `usage_absent_skip` precedent) -> NEVER a fabricated non-zero estimate masquerading as a real count
  - the bandwidth pacer's bounded wait is spent before a grant (`BandwidthExhaustedError`) mid-session
    -> close 4429; distinct from the pump's existing 4503 (provider-down) outcome -> the client was
    throttled by ITS OWN cap, not refused by the provider
  - the audit write itself fails (Redis/DB unavailable) -> swallow + log, per `record_audit`'s
    EXISTING fail-open contract -> the relay session's own outcome is UNAFFECTED, never gated on an
    audit write succeeding
  - a future task adds a non-WebSocket route under `/v1/realtime/` -> already guarded by the FROZEN
    `test_only_websocket_routes_under_realtime_carveout` test (unmodified by this task) -> fails loudly
    at CI, not a silent edge-auth hole
</reject>
After:
<after>
  - a relay session that passes auth-over-WS but fails budget/allowlist/RPM is refused BEFORE any
    provider dial, with an observable close code and (identity-known) one audit row — the same
    protection every other `/v1` surface already has
  - every OpenAI relay turn already bills (unchanged, shipped); every GEMINI relay turn ALSO produces
    a `usage_records` row whenever the provider furnishes usage data — Gemini relay spend is no longer
    categorically unmetered by design (a live-API-shape risk remains, see the ⚠ below)
  - a team-budgeted key's relay usage correctly counts against its team's spend counter (previously
    silently excluded)
  - a long-lived relay session's audio throughput is paced by the SAME bandwidth-bucket primitive the
    chat streaming path already uses, default-OFF / byte-identical when unconfigured
  - the local/e2e Envoy stack matches the K8s chart's ext_authz carve-out — no more silent 3-file drift
  - session open/close/reject events land in `audit_events` per whichever actor-scoping option Tin
    picks at freeze
  - the v47 `/v1/realtime` turn-based path, the auth-over-WS 4401/4404/4408 contract, and the
    `RealtimeRelaySession`/`RealtimeClientTransport` Protocols are byte-identical to today
</after>
Assumptions — lowest-confidence first:
<assumptions>
  ⚠ **TOP** — whether Gemini Live's BidiGenerateContent server messages expose ANY per-turn
  usage/token-count field (a `usageMetadata`-shaped key or similar), and whether it is per-turn or
  session-cumulative, is UNVERIFIED against live Gemini docs/API in this ground pass — lowest
  confidence because `gemini_live.py`'s own `_translate_server_message` never reads or names such a
  field anywhere today, and this ground pass is a static code read, not a live-doc/API check (mirrors
  PROJECT.md's own folded DDD lesson: "pass-through is not capability-neutral... research-before-build"
  and the SRE persona's "external-tooling assumption re-validated LIVE each milestone" rule). If wrong
  (no such field exists, or it's cumulative not per-turn): M3 cannot ship as "bill it" — it degrades to
  a documented, honest "$0/unmetered gap persists for Gemini relay, structurally, until Gemini Live
  exposes per-turn usage" (a Reject-and-document outcome, never a guessed/fabricated field name). Cost:
  this is EXACTLY the revenue-leak risk the milestone diagnostic (B2) named — getting it wrong silently
  (guessing a field name that doesn't exist, or double/mis-reading a cumulative total as per-turn) would
  ship a worse bug than the current honest gap. Mitigation: BUILD must live-verify (find-docs / a live
  smoke test against the real Gemini Live API) the exact message shape BEFORE implementing the
  translator; this task's freeze already accepts EITHER outcome (field found → M3 ships; field absent →
  M3 becomes a documented Reject) so a "gap confirmed, not fixed" VERIFY does not have to reopen SPECIFY.
  - [ ] the audit actor-scoping fork (Option A `tenant_id=None` system-level event vs. Option B a new
    key-actor `AuditEvent` field) — Tin decides at freeze, see §3. Medium confidence Option A is the
    right DEFAULT (zero cross-task change, precedented elsewhere in this exact codebase) but it is
    Tin's call, not this task's to make unilaterally.
  - [ ] once-at-connect RPM/budget vs. periodically re-checking a long-lived open session — chosen
    once-at-connect (mirrors "opening a session = one governed request"); medium confidence. If wrong: a
    tenant whose budget is exhausted mid-session keeps their ALREADY-open relay running until
    idle-timeout/disconnect — a documented residual risk (see After), not a silent gap, but genuinely
    open whether Tin wants tighter mid-session enforcement in a follow-up.
  - [ ] the `infra/envoy/*.yaml` carve-out fix is believed mechanical/low-risk (byte-mirrors the chart's
    ALREADY-SHIPPED, presumably-deployed-and-working block) but is UNTESTED by this task's own suite (no
    live Envoy in CI) — the SAME honest gap `realtime-relay-endpoint`'s own Envoy edit already flagged
    for itself; lower confidence than the others only because a YAML routing-rule ordering mistake is a
    classic silent-until-deployed failure mode, not because the content itself is in doubt.
</assumptions>

---

## 2 · SCENARIOS — pass/fail cases ▸ docs/04-step-2-scenarios.md

<scenarios>

```gherkin
Scenario: authed relay passes governance and opens a session (M1, M2 happy path)
  Given a valid auth stub, a key with an allowlisted/active dialed model, budget headroom, and RPM headroom
  When a client connects, authenticates, and the endpoint runs NonChatGovernance.authorize() on the dialed model
  Then the provider session is built and the pump runs normally
  And a realtime_relay.session_opened audit event is scheduled (M6)

Scenario: dialed model unknown in the catalog closes 4400 (M1, M2, Reject)
  Given a valid auth stub but the dialed model is inactive/absent from the catalog
  When a client connects and authenticates
  Then authorize() raises MODEL_UNKNOWN and the socket closes with code 4400
  And no provider session is built
  And a realtime_relay.session_rejected audit event is scheduled with the tenant/key already known

Scenario: dialed model not on the key's allowlist or tenant-disabled closes 4403 (M1, M2, Reject)
  Given a valid auth stub whose key's model_allowlist excludes the dialed model (or the model is TENANT_DISABLED)
  When a client connects and authenticates
  Then authorize() raises MODEL_NOT_ALLOWED or MODEL_DISABLED and the socket closes with code 4403
  And no provider session is built

Scenario: per-key/team/tenant budget exhausted closes 4402 (M1, M2, Reject)
  Given a valid auth stub whose key/team/tenant has exhausted its monthly budget
  When a client connects and authenticates
  Then authorize() raises BUDGET_EXCEEDED and the socket closes with code 4402
  And no provider session is built

Scenario: RPM exceeded at connect closes 4429 (M1, M2, Reject)
  Given a valid auth stub whose key has an rpm_limit already saturated for this window
  When a client connects and authenticates
  Then authorize() raises RATE_LIMITED and the socket closes with code 4429
  And no provider session is built

Scenario: TPM pre-flight is never invoked for a relay connect (Reject — documented inapplicability)
  Given a valid auth stub whose key has a tpm_limit configured
  When a client connects, authenticates, and governance runs with estimated_tokens=None
  Then the TPM step (Step 9) is skipped — no RATE_LIMITED is raised on TPM grounds at connect
  And the session proceeds to bandwidth-paced relay instead (M5)

Scenario: existing auth-over-WS outcomes are unchanged (M8, regression guard)
  Given the existing bad-token / first-frame-not-auth / auth-timeout / no-provider stubs from realtime-relay-endpoint's own suite
  When each is exercised exactly as that task's own tests already do
  Then the socket still closes 4401 / 4401 / 4408 / 4404 respectively
  And NonChatGovernance.authorize() is never reached (identity failed first, unchanged ordering)

Scenario: OpenAI relay turn still bills per-turn, team_id now included (M4 fix, regression + fix)
  Given a fake OpenAI session whose events() yields a response.done with a usage object, and an authz with team_id set
  When the turn completes
  Then usage_recorder.record() is called with team_id=authz.team_id (previously omitted)
  And the recorded model/tenant_id/key_id/cost fields are unchanged from the shipped behavior

Scenario: Gemini relay turn with usage data now bills (M3, After — gated on the live-verified field name)
  Given a fake Gemini session whose events() yields a turn-boundary message carrying the (live-verified) usage field
  When the turn completes
  Then GeminiLiveSession's on_usage callback fires with the recorder-canonical shape
  And usage_recorder.record() is called with usage_source="realtime_relay" and the Gemini dialed model id

Scenario: Gemini relay turn with no usage data records nothing, honestly (M3, Reject)
  Given a fake Gemini session whose turn-boundary message carries no usage/token field
  When the turn completes
  Then on_usage is never invoked and no usage_records row is written
  And a DEBUG log gemini_usage_absent_skip is emitted
  And the relay session is undisturbed (frames still relayed normally)

Scenario: bandwidth pacing grants an audio frame within cap (M5 happy path)
  Given a configured bandwidth_bucket with headroom for the frame size
  When the client sends an audio frame
  Then bandwidth_bucket.acquire(key_id, len(frame), max_wait_s) grants immediately (or after a bounded wait)
  And the frame is forwarded to the provider unchanged

Scenario: bandwidth pacing exhausted mid-session closes 4429 (M5, Reject)
  Given a configured bandwidth_bucket whose bounded wait would be exceeded for the next audio frame
  When the client sends that audio frame
  Then BandwidthExhaustedError propagates and the pump closes the socket with code 4429
  And this 4429 is distinguishable from the pump's existing 4503 provider-unavailable outcome (different trigger, same code family)

Scenario: bandwidth_bucket absent is byte-identical to today (M5, After)
  Given no bandwidth_bucket is configured on the pump (default PassthroughBandwidthBucket)
  When audio frames are relayed
  Then no pacing delay is introduced and behavior matches the pre-change pump exactly

Scenario: audit events are scheduled fire-and-forget for open/close/reject (M6)
  Given a session that opens, relays normally, and closes on pump outcome
  When the endpoint runs
  Then a session_opened AuditEvent is scheduled right after governance passes
  And a session_closed AuditEvent is scheduled right after pump.run() returns, carrying the close code
  And neither scheduling call blocks or delays the relay itself

Scenario: pre-identity rejection is never audited (M6, Reject — scope boundary)
  Given a bad/missing/expired token or an auth timeout (the existing 4401/4408 paths)
  When the socket closes
  Then no AuditEvent is scheduled — no tenant is confirmed to scope it to
  And this is unchanged from today's (zero-audit) behavior for those paths

Scenario: audit write failure never disrupts the relay (M6, Reject)
  Given record_audit's underlying session_factory raises (DB/Redis unavailable)
  When a session_opened or session_closed event is scheduled
  Then the exception is swallowed and logged per record_audit's existing fail-open contract
  And the relay session's own outcome (frames relayed, close code) is unaffected

Scenario: local/e2e Envoy stack now carves out /v1/realtime/ like the K8s chart (M7)
  Given infra/envoy/envoy.yaml and infra/envoy/envoy-prod.yaml after this task's fix
  When the route table is rendered
  Then a { prefix: "/v1/realtime/" } route with ext_authz disabled appears BEFORE the general /v1/ rule
  And it is byte-identical in shape to charts/ai-proxy/templates/envoy-configmap.yaml's existing block

Scenario: the frozen carve-out guard tests stay green (M8, regression guard)
  Given the app's route table after this task's changes
  When test_only_websocket_routes_under_realtime_carveout and test_relay_ws_is_under_the_carveout run
  Then both pass unmodified — no non-WS route was added under /v1/realtime/
```

</scenarios>

---

## 3 · CONTRACT — freeze the shape ▸ docs/05-step-3-contract.md

```
WS /v1/realtime/relay   (path + first-frame auth-over-WS contract UNCHANGED from realtime-relay-endpoint v1)

ADDITIVE governance step — realtime_relay_ws.py:realtime_relay, after _authenticate succeeds,
BEFORE _build_session is called:

  governance = NonChatGovernance(
      authenticator=<same SqlAlchemyKeyAuthenticator shape realtime_ws.py:_real_stt builds>,
      model_checker=SqlAlchemyModelChecker(session),
      budget_guard=app.state.budget_guard,
      rate_limiter=getattr(app.state, "rate_limiter", None),
      redis_client=<same getattr(budget_guard, "_redis", None) pattern realtime_ws.py uses>,
      session_factory=app.state.sessionmaker,
  )
  dialed_model = (
      settings.realtime_relay_openai_model if settings.realtime_relay_provider == "openai"
      else settings.realtime_relay_gemini_model if settings.realtime_relay_provider == "gemini"
      else None   # unconfigured provider — governance never runs, falls through to existing 4404
  )
  try:
      authz = await governance.authorize(raw_key=token, model_id=dialed_model, estimated_tokens=None)
  except ProblemError as exc:
      await websocket.close(4000 + exc.status)   # mechanical: 4400/4402/4403/4429
      schedule session_rejected audit event (identity known)
      return

  close codes (extends the existing v1 table — v1 codes UNCHANGED):
    4400  MODEL_UNKNOWN            (governance, NEW)
    4401  AUTH_KEY_INVALID / AUTH_KEY_EXPIRED / first-frame-not-auth   (v1, unchanged)
    4402  BUDGET_EXCEEDED          (governance, NEW — per-key / team / tenant)
    4403  MODEL_NOT_ALLOWED / MODEL_DISABLED   (governance, NEW)
    4404  no realtime provider configured      (v1, unchanged — honest-degrade)
    4408  no auth frame within realtime_auth_timeout_seconds   (v1, unchanged)
    4429  RATE_LIMITED (RPM at connect, governance, NEW) | BandwidthExhaustedError (mid-session, M5, NEW)
    4503 / 1011 / 1000   the pump's existing relay-outcome codes (t1, unchanged)
  No provider session is opened when governance raises — extends "no session before auth" to
  "no session before governance." TPM (Step 9) is SKIPPED (estimated_tokens=None) — deliberate,
  documented inapplicability for a continuous audio stream, not a gap.

RealtimeRelaySession / RealtimeClientTransport Protocols (domain/realtime_relay.py): UNCHANGED.

GeminiLiveSession.__init__ (infrastructure/gemini_live.py) gains:
  on_usage: Callable[[dict[str, Any]], Awaitable[None]] | None = None   # mirrors OpenAI verbatim
  Fires once per turn-boundary server message IF a usage/token field is present (exact field name
  verified live at BUILD — §1 ⚠ top assumption); absent -> no callback, DEBUG log
  gemini_usage_absent_skip. Never raises into events(); a callback failure is swallowed + logged.

realtime_relay_ws.py:_make_relay_usage_callback(...) gains: pass team_id=authz.team_id through to
  usage_recorder.record(...) (currently omitted — M4 fix, applies to BOTH providers).
realtime_relay_ws.py:_real_session_factory(...) wires the SAME callback into GeminiLiveSession that
  OpenAIRealtimeSession already receives (today: OpenAI only).

RelayPump.__init__ (application/realtime_relay_pump.py) gains:
  bandwidth_bucket: BandwidthBucket | None = None   # mirrors the existing optional `breaker` param
  key_id: uuid.UUID | None = None                   # needed only when bandwidth_bucket is set
  Default: PassthroughBandwidthBucket() when bandwidth_bucket is None -> byte-identical, no pacing.
  _client_to_provider paces each AUDIO frame (never control frames) via
  bandwidth_bucket.acquire(key_id, len(frame), settings.bandwidth_max_wait_seconds) before
  self._s.send_audio(...). BandwidthExhaustedError -> close 4429 (distinct from the existing 4503
  RealtimeProviderUnavailableError path — same "too fast" family as the connect-time RATE_LIMITED).

Audit (fire-and-forget via the EXISTING record_audit()/AuditEvent — NO migration in the default option):
  realtime_relay.session_opened    — scheduled the moment M1 governance PASSES, before session build
  realtime_relay.session_closed    — scheduled after pump.run() returns; metadata carries close_code
  realtime_relay.session_rejected  — a POST-IDENTITY governance rejection (M1/M2)
  A PRE-IDENTITY rejection (4401/4408, existing v1 paths) is NEVER audited — no confirmed tenant.
  metadata (every event): {"provider": <provider>, "model": <dialed model>, ...event-specific fields
  (close_code / rejection reason)} — NEVER token/key material.

  *** FREEZE FORK — RESOLVED: Tin chose Option B (2026-07-10, AskUserQuestion) ***
  Option B — FROZEN CHOICE. Relay audit events are TENANT-SCOPED and queryable via list_for_tenant.
    This SANCTIONS a scoped change-request against the FROZEN audit-log-store contract (Tin widened
    this task's scope at freeze to include it):
    - Add a nullable `actor_key_id: uuid.UUID | None` field to AuditEvent (audit/domain/audit_event.py).
    - Relax the `audit_missing_actor` invariant in `__post_init__`: a tenant-scoped event
      (tenant_id is not None) is valid if it carries EITHER actor_user_id OR actor_key_id.
    - One additive nullable-column migration on `audit_events` (actor_key_id), parented on the current
      single alembic head; the new column carried through audit_events ORM + the writer's INSERT.
    - The `audit_events` table already exists in both SANCTIONED-EDIT test manifests
      (tests/migrations/test_migrations.py EXPECTED_TABLES, tests/guardrails no-new-tables) — a COLUMN
      add needs no manifest change, but the audit-log-store frozen contract's own §3 tests must be
      re-crossed under the change-request (relaxed invariant is additive: user-actor events unaffected).
    AuditEvent(tenant_id=authz.tenant_id, actor_user_id=None, actor_key_id=authz.key_id,
               actor_email=None, action="realtime_relay.session_*", target_type="realtime_relay",
               target_id=str(key_id), result="success"|"rejected",
               metadata={"provider": ..., "model": ..., ...event-specific})
  Option A — NOT CHOSEN (system-level tenant_id=None event; recorded for the ADR trail only).

Envoy (infra/envoy/envoy.yaml, infra/envoy/envoy-prod.yaml — byte-mirror
  charts/ai-proxy/templates/envoy-configmap.yaml:126-138, placed BEFORE the existing general "/v1/" rule):
    - match: { prefix: "/v1/realtime/" }
      route: { cluster: gateway_cluster }
      typed_per_filter_config:
        envoy.filters.http.ext_authz:
          "@type": type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthzPerRoute
          disabled: true

Settings: NO new fields. Bandwidth pacing reuses settings.bandwidth_max_wait_seconds + an optional
  app.state.bandwidth_bucket (the SAME app.state singleton deps.py already wires for the chat path —
  this task threads it into the relay endpoint too, it does not invent a second one).

Schema: ONE additive nullable-column migration on audit_events (actor_key_id uuid NULL) — Option B, Tin's
  frozen choice. Parent on the current single alembic head. Additive + nullable → byte-identical for every
  existing audit path (user-actor events keep actor_user_id, actor_key_id stays NULL).
```

Status: FROZEN @ v1 — Tin approved 2026-07-10 (AskUserQuestion). Audit actor-scoping = Option B (actor_key_id,
tenant-scoped, sanctioned change-request against audit-log-store). All other §3 clauses frozen as drafted.
Least-sure flag surfaced at freeze: (ranked; RESOLVED at build/verify — see notes)
  1. [spec] **TOP** — Gemini Live per-turn usage-field existence/shape is UNVERIFIED against the live
     API/docs (§1 ⚠). BUILD must live-verify before implementing M3's translator; if absent, M3
     degrades to a documented Reject (honest unmetered gap persists), never a guessed field name —
     both outcomes are already accepted by this freeze so VERIFY does not need to reopen SPECIFY.
  2. [contract] the audit actor-scoping fork (Option A system-level default vs. Option B a new
     key-actor field + change-request) — Tin picks at freeze; recommend Option A unless tenant-scoped
     audit queryability for relay sessions is a near-term product requirement.
  3. [spec] once-at-connect governance vs. periodic mid-session re-check — chosen once-at-connect;
     documented residual risk that a budget-exhausted tenant's ALREADY-open session keeps running
     until idle-timeout/disconnect.
  4. [ground] the infra/envoy/*.yaml carve-out fix is untested by this task's own suite (no live Envoy
     in CI) — same honest gap `realtime-relay-endpoint` already flagged for its own Envoy edit.

Persona note: drafted under the **Application Security Engineer** persona
(`.add/personas/appsec-engineer.md`) — this task's diagnostic origin (B2, enterprise-readiness audit)
frames it as a governance/security gap ("ungoverned relay = unaudited security surface"), and the
persona's defense-in-depth doctrine ("no single layer being wrong or bypassed is sufficient") directly
motivated M1/M2 (governance gates the relay exactly as it gates every other modality, mechanically
derived from the shared `ProblemError.status`, never a second hand-rolled table) and M8 (the frozen
carve-out/Protocol invariants are re-verified, not just trusted). Billing-precision discipline
(Decimal-only, provenanced, never-silent-$0) shaped M3/M4's honest-degrade requirement; SRE fail-open
doctrine shaped M5's Redis-error handling and the Envoy-drift finding (M7).

---

## 4 · TESTS — failing-first suite (red) ▸ docs/06-step-4-tests.md

Coverage target: every §2 scenario has one executable test; no test weakened/skipped.
Plan (one test per scenario, asserting behavior not internals):
<test_plan>
  - test_governance_pass_opens_session / test_governance_called_with_estimated_tokens_none —
    M1 happy path + estimated_tokens=None (TPM skip)
  - test_model_unknown_closes_4400 / test_model_not_allowed_closes_4403 /
    test_model_disabled_closes_4403 / test_budget_exceeded_closes_4402 /
    test_rpm_exceeded_closes_4429 — M2 mechanical close-code mapping
  - test_bad_token_4401_governance_never_reached / test_auth_timeout_4408_governance_never_reached /
    test_no_provider_configured_governance_never_reached — M8 regression guard (identity fails
    first; governance unreached)
  - test_turn_complete_with_usage_metadata_triggers_capture / ..._maps_cached_content_tokens.../
    ..._captures_once_per_turn_not_merged — M3 Gemini usage capture (field LIVE-VERIFIED)
  - test_turn_complete_without_usage_metadata_records_nothing /
    test_non_turn_boundary_message_never_fires... / test_on_usage_failure_is_swallowed... /
    test_on_usage_absent_is_backward_compatible — M3 Reject + failure-isolation
  - test_make_relay_usage_callback_passes_team_id / ..._team_id_defaults_none /
    test_real_session_factory_wires_team_id_for_openai / ..._wires_on_usage_for_gemini /
    ..._authz_without_team_id_attr_defensively — M4 team-attribution fix (both providers)
  - test_bandwidth_grant_within_cap_forwards_frame_unchanged /
    test_bandwidth_exhausted_mid_session_closes_4429 /
    test_bandwidth_exhausted_does_not_trip_the_provider_breaker /
    test_bandwidth_bucket_absent_is_byte_identical_to_today — M5 pacing + Reject + isolation
  - test_session_opened_audit_scheduled_on_governance_pass /
    test_session_closed_audit_carries_close_code /
    test_session_rejected_audit_scheduled_on_governance_failure /
    test_pre_identity_rejection_not_audited / test_audit_write_failure_does_not_disrupt_relay —
    M6 audit lifecycle + both Reject scopes
  - test_envoy_yaml_has_realtime_carveout_before_general_v1 / ..._envoy_prod_yaml_... /
    ..._dev_stack_carveout_shape_matches_the_shipped_k8s_chart_route — M7 (static YAML pin;
    no live Envoy in CI, same honest gap realtime-relay-endpoint flagged for itself)
  - test_only_websocket_routes_under_realtime_carveout / test_relay_ws_is_under_the_carveout —
    M8 frozen carve-out guard, unmodified
  - test_tenant_scoped_event_valid_with_only_actor_key_id /
    ..._still_rejected_when_both_actor_fields_absent / ..._actor_key_id_defaults_to_none... /
    ..._actor_key_id_persists_and_round_trips / ..._user_actor_events_unaffected... — audit
    change-request (Option B) re-crossed; audit-log-store's own frozen tests stay green unmodified
</test_plan>

Tests live in: `apps/gateway/tests/realtime_relay/` `apps/gateway/tests/audit/`
  `apps/gateway/tests/gpt_realtime_relay_billing/` (one pre-existing test flipped, see below)
  `apps/gateway/migrations/versions/` (new file, untested by suite per §3)
  `infra/envoy/` (edited, pinned by tests/realtime_relay/test_envoy_carveout.py)
MUST run red (missing implementation) before Build — confirmed 2026-07-10 (32 new tests RED
for the right reason: TypeError/AttributeError/KeyError on the missing param/field, one
assertion-mismatch on the not-yet-wired 4400 code — never an import/collection error).

Pre-existing test superseded (documented, not silently weakened):
  `tests/gpt_realtime_relay_billing/test_usage_capture.py::test_gemini_live_constructor_has_no_on_usage_param`
  renamed to `test_gemini_live_constructor_gained_on_usage_param_per_relay_governance_task` and its
  assertion flipped (on_usage now REQUIRED, default None) — this task's own frozen §3 (M3)
  explicitly and knowingly supersedes that earlier task's own regression pin; docstring explains
  the supersession in place.
Pre-existing test harness extended, assertions unchanged (necessary consequence of a mandatory
new connect-time step on a shared endpoint, not a weakening):
  `tests/realtime_relay/test_relay_endpoint.py` — added a default permissive
  `realtime_relay_governance_authorize` stub + `app.state.sessionmaker`, mirroring the file's
  OWN pre-existing authenticate/session-factory stub convention; every existing assertion
  (close codes, full-duplex frame order) is byte-identical.

---

## 5 · BUILD — AI writes code ▸ docs/07-step-5-build.md

Scope (may touch): `apps/gateway/src/gateway/proxy/api/realtime_relay_ws.py`
  `apps/gateway/src/gateway/proxy/application/realtime_relay_pump.py`
  `apps/gateway/src/gateway/proxy/infrastructure/gemini_live.py`
  `apps/gateway/src/gateway/audit/domain/audit_event.py`
  `apps/gateway/src/gateway/audit/infrastructure/audit_events_orm.py`
  `apps/gateway/src/gateway/audit/infrastructure/audit_repository.py`
  `apps/gateway/migrations/versions/` `infra/envoy/envoy.yaml` `infra/envoy/envoy-prod.yaml`
  `apps/gateway/tests/realtime_relay/` `apps/gateway/tests/audit/`
  `apps/gateway/tests/gpt_realtime_relay_billing/test_usage_capture.py` (one superseded test only)
Strategy (ordered batches): 1. ground the exact anchors (serena/Read) 2. migration file
  (parent on current single head) 3. RED suite, one file per M-clause 4. GREEN: audit domain
  relaxation → ORM/repo → Gemini on_usage + live-verified translator → RelayPump bandwidth
  pacing → endpoint governance/audit/team_id wiring (last, since it composes everything else)
  5. Envoy YAML carve-out + its static-shape test 6. full regression pass + ruff + pyright.
Known-problem fixes: Gemini usage field name unverified → live-verified via WebSearch+WebFetch
  against ai.google.dev/api/live + a real forum-posted raw payload before writing the
  translator (never guessed) · pre-existing test_relay_endpoint.py would crash on the new
  mandatory governance step → added a default permissive governance stub mirroring its own
  established seam convention · RelayPump.close_code needed for the session_closed audit event
  → added as a plain attribute set once in _teardown (no Protocol widening) · BandwidthExhaustedError
  must not trip the provider circuit breaker → explicit isolated branch in run()'s outcome dispatch.
Strategy actually used: as planned (all 6 batches executed in this order; no deviation).
Safety rule (feature-specific): governance and audit-scheduling never gate or delay the relay
  itself — governance raises BEFORE any provider dial (no partial session ever built), and every
  audit schedule is asyncio.ensure_future + fire-and-forget (record_audit's own fail-open
  contract already swallows DB/Redis failure; verified by test_audit_write_failure_does_not_disrupt_relay).
Code lives in: `apps/gateway/src/gateway/` `infra/envoy/`
Constraints: do NOT change any test or the contract; allow-list packages only; ask if unclear.
  (PyYAML already a project dependency — no new package added.)

---

## 6 · VERIFY — evidence + non-functional review ▸ docs/08-step-6-verify.md

- [ ] all tests pass
- [ ] coverage did not decrease
- [ ] no test or contract was altered during build
- [ ] the green was EARNED, not gamed — no overfit to fixtures, vacuous asserts, or stubbed-away logic (score with an adversarial refute-read — a subagent recommended under `autonomy: auto`; a confirmed cheat is HARD-STOP)
- [ ] concurrency / timing of the risky operation is safe
- [ ] no exposed secrets, injection openings, or unexpected dependencies
- [ ] layering & dependencies follow CONVENTIONS.md
- [ ] a person reviewed and approved the change

### Build expectations — what "correct" looks like (fill BEFORE build; confirm each at the gate)
> Pre-declare the OBSERVABLE outcomes a correct build must produce — derived from §2 SCENARIOS
> + §3 CONTRACT — so this gate checks the build is RIGHT, not merely that tests are green. Each
> row is evidence you can SEE, not a restatement of a test name.
- [x] A relay connect whose model fails allowlist/catalog/budget/RPM closes with 4000+status
      (4400/4402/4403/4429) BEFORE any provider dial — confirmed by test_relay_governance.py + adversarial
      trace of realtime_relay_ws.realtime_relay (governance precedes _build_session on every dialed path).
- [x] A Gemini relay turn emits exactly ONE usage_records row per turnComplete-boundary message carrying
      usageMetadata (never on interim messages) — confirmed by test_gemini_usage_capture.py +
      test_non_turn_boundary_message_never_fires; live-re-verified usageMetadata⇄turnComplete co-occurrence.
- [x] Relay usage rows carry team_id=authz.team_id (team-budget attribution parity) — confirmed by
      test_relay_team_id.py.
- [x] A tenant-scoped audit event is valid with actor_key_id alone and STILL rejected with neither actor —
      confirmed by test_audit_actor_key_id.py + test_tenant_scoped_event_still_rejected_when_both_absent;
      migration 511ad8a7b65e additive/nullable, single alembic head, autogenerate-empty-diff green.
- [x] session_opened/closed/rejected audit rows scheduled fire-and-forget, never gating the relay —
      confirmed by test_relay_audit.py.
- [x] bandwidth_bucket default (PassthroughBandwidthBucket) is byte-identical when unset; the 2 dev-stack
      envoy configs byte-mirror the chart carve-out — confirmed by test_relay_bandwidth.py +
      test_envoy_carveout.py + frozen test_carveout_invariant.py unchanged/green.

### Deep checks — do not skim (fill the path that applies; the resolver judges which)
- [x] WIRING (code) — actor_key_id threaded domain→ORM→repository→INSERT; on_usage wired into GeminiLiveSession
      via _real_session_factory; bandwidth_bucket threaded into RelayPump; governance called in realtime_relay.
      Confirmed by adversarial VERIFY (every new symbol referenced; ruff/pyright clean).
- [x] DEAD-CODE (code) — one dead test helper found (`_drain_ensure_future` in test_relay_audit.py, works
      today via Starlette teardown) → recorded as §7 spec-delta, non-blocking. No orphaned src symbol.
- [x] SEMANTIC — Gemini Live usageMetadata shape read in full from ai.google.dev/api/live + a live captured
      payload (re-verified independently at VERIFY, not trusting the build's own claim).

### Refute-read verdict — the earned-green check (record it; required for an auto-PASS)
> Under autonomy: auto the AI auto-resolves Verify, so the earned-green refute-read MUST be
> recorded here (the engine never spawns it — you do; NOT-EARNED -> `add.py heal`). The engine
> MEASURES it is filled (`audit: refute_unrecorded`); it never auto-blocks — a human spot-audit
> is the backstop. A human-gated (conservative/manual) task may leave it for the human's judgment.
Verdict: EARNED
By: agent add-verify (a7ba061888524c84e) · adversarially checked: audit-invariant symmetry (actor-less event
still rejected), Gemini usage double/under-count (live-re-verified field shape + turn-boundary gate),
governance ordering + close-code mapping + a disconfirmed UnboundLocalError suspicion, bandwidth byte-identity,
envoy carve-out byte-parity, poison-in-batch resilience. Full suite 2619 passed / 0 failed. VERDICT CLEAN.

### GATE RECORD
Outcome: PASS
If RISK-ACCEPTED -> owner: <name> · ticket: <link> · expires: <date>   (never for a security gap)
Reviewed by: Tin Dang · date: 2026-07-10

---

## 7 · OBSERVE — feed the next loop ▸ docs/09-the-loop.md

Watch (reuse scenarios as monitors): <error rate / per-rejection rate / latency>

### Decisions (ADR)
- [AI] specify — chose <unrecorded>
- [human] freeze — froze §3 @ <unrecorded> (approved by <unrecorded>)
- [AI] build — strategy used: as planned (all 6 batches executed in this order; no deviation).
- [AI] verify — gate PASS (reviewed by Tin Dang)

### Spec delta
Forward changes for the next loop — each re-enters at Specify as the next task. One line
each, tagged `[SPEC · open|seeded|dropped]`, with evidence (e.g. `[SPEC · open] rate-limit
the retry path (evidence: prod herd spikes)`). See the `add` skill's `deltas.md`.
  - [SPEC · open] collapse bandwidth-pacing gate to one signal — drop the `key_id is not None`
    co-condition at realtime_relay_pump.py:103; today a configured bucket + missing key_id silently
    no-ops (unreachable via the endpoint, which always threads authz.key_id). (evidence: adversarial VERIFY 🟡)
  - [SPEC · open] wire or delete the dead `_drain_ensure_future()` helper in tests/realtime_relay/
    test_relay_audit.py; standardize on this repo's explicit `asyncio.sleep(0.05)` WS fire-and-forget
    drain idiom. Works today only via Starlette teardown's implicit loop-iteration. (evidence: adversarial VERIFY 🟡)

### Competency deltas
What did this loop teach the foundation? One line each, tagged by competency
(`DDD · SDD · UDD · TDD · ADD`), status `open`, with evidence. See the `add` skill's `deltas.md`.

  - [DDD · open] Gemini Live re-bills the FULL cumulative context every turn (growing per-turn
    promptTokenCount is real spend, not a double-count bug) — fold into PROJECT.md billing-precision
    notes so a future engineer doesn't "fix" it. (evidence: live forum + docs re-verified at VERIFY)
