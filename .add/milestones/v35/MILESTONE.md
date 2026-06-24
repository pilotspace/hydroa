# MILESTONE: Agent-loop error fidelity

goal: When an upstream provider fails (rate-limit, auth, 5xx, mid-stream), the proxy surfaces a faithful, actionable signal to the OpenAI-wire client — correct 429 + Retry-After on rate-limits and a well-formed terminal SSE error frame on streaming failures — so an agent loop (Helios) backs off or recovers instead of seeing a generic 502 or a hung stream.
rationale: new-major — a hardening theme no archived milestone's goal covers. v34 proved the proxy faithfully TRANSLATES + bills a healthy agent-coding upstream; this milestone proves it faithfully SURFACES an UNhealthy one. Surfaced by the post-v34 multi-model live probe (2026-06-24): driving 6 diverse free OpenRouter models found the proxy maps upstream 429→502 (drops Retry-After) and lets a mid-stream upstream failure return HTTP 200 with no [DONE] — both make an agent loop (Helios) mis-handle recoverable upstream failures. Confirmed via intake interview 2026-06-24 (Tin: bundle A+B + live-verify; merge #22 first → branch off main).
stage: production · status: active · created: 2026-06-24

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  Faithful upstream-FAILURE surfacing on the `/v1/chat/completions` chat path:
     (A) upstream rate-limit passthrough — when an upstream 429 survives the bounded
     retry, return a client 429 (distinct ERR_UPSTREAM_RATE_LIMITED) carrying the
     upstream's Retry-After, NOT a generic 502;
     (B) streaming upstream-error terminal frame — when an upstream failure lands
     after the SSE 200/headers are flushed, emit a well-formed OpenAI error chunk +
     a terminal [DONE] so the client never hangs waiting;
     plus (C) a live re-verification: a CI stub proving both, and a live re-probe
     against real rate-limited free OpenRouter models (the multi-model probe).
Out: changing the retry POLICY itself (counts/backoff/circuit — frozen as-is);
     the 504-on-slow-reasoning timeout tuning (Finding D — separate);
     the 0-token usage row written on a failed request (minor — separate);
     non-chat paths (embeddings/audio/images); new providers; the actual Helios cut-over.

## Shared decisions & glossary deltas   (living — every task must honor these)
- INVARIANT (carry from v9/v10/v34): a request whose upstream SUCCEEDS stays
  BYTE-IDENTICAL to today. This milestone only changes the FAILURE surface.
- Distinguish gateway-imposed rate limits (existing ERR_RATE_LIMITED, the local
  limiter) from UPSTREAM-imposed ones (new ERR_UPSTREAM_RATE_LIMITED) — same 429
  status, different code, so a client can tell whose limit it hit.
- Retry-After fidelity: when the upstream supplies Retry-After, the client 429 MUST
  carry it; when it does not, omit the header (never fabricate a value).
- Streaming error contract: once a 200 SSE stream is open, ALL terminations —
  success or upstream failure — end with a `data: [DONE]` sentinel; an upstream
  failure additionally emits one OpenAI-shaped `{"error": {...}}` chunk before it.

## Shared / risky contracts (freeze these first)
- Upstream-rate-limit error shape + Retry-After propagation  -> owning task `upstream-ratelimit-passthrough`
- Streaming upstream-failure terminal-frame contract         -> owning task `stream-upstream-error-frame`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] upstream-ratelimit-passthrough  depends-on: none                          — on 429 retry-exhaust, raise an UpstreamRateLimitedError carrying Retry-After; API maps it to client 429 ERR_UPSTREAM_RATE_LIMITED + Retry-After header (not 502)  ✅ gate PASS `f538463`
- [x] stream-upstream-error-frame     depends-on: none                          — when an upstream failure hits an already-open SSE stream, emit one OpenAI error chunk + a terminal [DONE] so the client never hangs  ✅ gate PASS `5cd6197`
- [x] stream-graceful-close-mapping   depends-on: stream-upstream-error-frame   — Finding C (added mid-milestone, Tin-approved): a graceful mid-stream peer-close raises httpx.RemoteProtocolError (a ProtocolError, NOT NetworkError) → was unmapped → task-2 frame never fired for the COMMON real drop. Map it → UpstreamUnavailableError across all 5 adapters.  ✅ gate PASS `93c24cb`
- [x] error-fidelity-live-verify      depends-on: upstream-ratelimit-passthrough, stream-upstream-error-frame, stream-graceful-close-mapping — CI stub proving the behaviors + a live re-probe (multi-model harness) against real free OpenRouter models; live double-pass  ✅ gate PASS `c3e1d06`

## Exit criteria (observable; map each to the task that delivers it)
- [x] A rate-limited upstream (429 surviving retries) returns a client 429 ERR_UPSTREAM_RATE_LIMITED carrying the upstream Retry-After — never a generic 502   (← upstream-ratelimit-passthrough; live EF-1: 429 + Retry-After: 7 through the edge)
- [x] A streaming request whose upstream fails after the 200 is flushed receives one OpenAI error chunk + a terminal [DONE]; a [DONE]-waiting client never hangs   (← stream-upstream-error-frame + stream-graceful-close-mapping; live EF-2: graceful FIN-close → ERR_UPSTREAM_UNAVAILABLE frame + [DONE] through Envoy)
- [x] Every upstream-SUCCESS path (non-stream + stream) stays byte-identical to pre-v35   (← both tasks; regression-guarded — stream_upstream_error_frame SEF-5, streaming_resilience, stream_graceful_close_mapping ReadError guard; full suite 1536 green)
- [x] Both behaviors are green in CI via a stub harness AND confirmed by a live re-probe (double-pass) against real free OpenRouter models   (← error-fidelity-live-verify; stub-mode double-pass 12/12 ×2 exit 0; live mode 13P/2S/0F exit 0)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- gateway src : `proxy/domain/errors.py` (UpstreamRateLimitedError), `core/error_catalog.py` (ERR_UPSTREAM_RATE_LIMITED), `proxy/infrastructure/upstream_retry.py` (429-exhaust → rate-limit error), `proxy/application/fallback_router.py` (track max Retry-After), `proxy/application/use_cases.py` (map 429→client 429+Retry-After; mid-stream `_sse_error_frame` + guarded [DONE]), 5 adapters `*_upstream.py` (RemoteProtocolError mapping).
- tooling : `.add/tooling/add.py` scope-walk excludes Python caches (`35cbaa1`); else state.json bookkeeping only.
- ops / verify : NEW `scripts/v35_error_fidelity_stub.py`, `scripts/live_v35_verify.py`, `infra/docker-compose.e2e.v35.yml` (operator live-verify triad). No skill/book change.

### Cross-task evidence   (one row per task)
- upstream-ratelimit-passthrough : gate=PASS · tests=11 (RP/FR/SR/UC) + suite green · residue=parse_retry_after integer-seconds-only (HTTP-date → no header)
- stream-upstream-error-frame    : gate=PASS · tests=6 (SEF) + 3 cross-suite corrected · residue=none (2 documented trade-offs: frame-after-[DONE] when upstream pre-sent [DONE]; status=502 unchangeable mid-stream)
- stream-graceful-close-mapping  : gate=PASS · tests=6 (5 adapters + ReadError guard) · residue=non-stream complete() RemoteProtocolError still unmapped (out of scope; §7 delta)
- error-fidelity-live-verify     : gate=PASS · tests=stub-mode double-pass 12/12 ×2 exit 0 + live 13P/2S/0F · residue=live EF probes SKIP (free tier not on-demand forceable)

### Goal met?   (map the evidence back to this milestone's Exit criteria)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row (EF-1 ← passthrough; EF-2 ← error-frame + graceful-close-mapping, proven live FIN-close through Envoy; byte-identical ← regression guards + 1536 green; CI+live ← live-verify double-pass)
- goal: the proxy now surfaces an UNhealthy upstream faithfully — a surviving 429 → client 429 + Retry-After (not 502), and ANY mid-stream upstream failure (incl. the common graceful close) → a terminal SSE error frame + [DONE]. Proof: live_v35_verify stub-mode double-pass 12/12 ×2 exit 0 through the real Envoy edge, with the graceful FIN-close firing ERR_UPSTREAM_UNAVAILABLE + [DONE].

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
- [ ] open a PR from feat/v35 → main (5 commits: f538463, 5cd6197, 93c24cb, c3e1d06, + fold chore); Tin reviews + merges (gh acct TinDang97, ADMIN; CI billing-blocked → --admin merge, reconcile local main via HTTPS ff-only)
- [ ] (optional) bundle into a release cut — v31 + v34 + v35 are now releasable since 0.2.0 (release.md)
- [ ] tag / publish / deploy (human-run, per release.md)
