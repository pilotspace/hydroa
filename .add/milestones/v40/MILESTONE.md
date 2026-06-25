# MILESTONE: Chat workspace + streaming

goal: A signed-in user can hold a multi-turn, live-streaming conversation with any catalog model from an in-dashboard chat workspace that shows each turn's token cost.
rationale: split_required → milestone 1 of 9 (program v40–v48; v39 is the parallel OAuth-device-flow effort) in the confirmed "AI Application Platform" program
  (Tin 2026-06-25: direction "both — API first, then UI"; first milestone "chat workspace + streaming";
  voice = breadth-now/realtime-later; video = understanding-first/generation-next). Relationship to map:
  EXTENDS the v1–v38 gateway + admin-dashboard arc by adding the FIRST end-user product surface — today
  the dashboard has 13 admin pages and NO way to actually use the AI; and it CLOSES the BFF SSE-streaming
  gap (apps/dashboard/app/api/gw/[...path]/route.ts buffers every response via `await upstream.json()`).
  DEPENDS-ON nothing (the `/v1/chat/completions` stream + `/v1/models` catalog already ship); UNBLOCKS
  demoing every later capability (voice/video/memory/artifacts) inside the workspace.

stage: production · status: active · created: 2026-06-25

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:
  - BFF SSE streaming passthrough: the dashboard proxy streams `text/event-stream` chat responses
    incrementally end-to-end (no buffering), with client-abort → upstream-abort propagation so the
    gateway's existing disconnect-billing fires. Non-stream JSON paths stay byte-identical.
  - In-dashboard chat workspace at `/app/chat`: model picker (from the existing catalog `/v1/models`),
    multi-turn thread, message composer, live token-by-token streaming render, Stop control, basic
    request controls (system prompt + temperature), the four UI states, and a role-open nav entry.
  - Per-turn cost readout: each assistant turn shows token usage + cost read from the stream's usage
    frame, with an honest placeholder when usage is absent.
Out:
  - Server-side conversation persistence / sessions — chat history is CLIENT-side only this milestone;
    durable remote sessions are the v43 "Remote sessions / conversations" milestone.
  - Web-search toggle in chat (→ v41), voice/audio in chat (→ voice milestones), image/file attachments,
    artifacts rendering (→ v45), video (→ v46) — the workspace is text-chat only here.
  - Tool/function-call UI, multimodal inputs, prompt saving/management, sharing, export.
  - Any gateway or data-plane change — `/v1/chat/completions` and `/v1/models` are consumed UNCHANGED;
    v40 is BFF + dashboard UI only.

## Shared decisions & glossary deltas   (living — every task must honor these)
- STREAMING-PROXY (NEW glossary): the BFF forwards an upstream `text/event-stream` body to the client
  INCREMENTALLY (pipe the readable stream; never `await .json()`), and propagates client disconnect to
  the upstream fetch (AbortController) so the gateway disconnect-billing path is preserved. The existing
  non-stream JSON passthrough and the fail-closed auth behavior (no cookie → 401, upstream 401 → clear
  cookie) stay byte-identical. Riskiest contract — freeze first.
- The chat workspace is available to EVERY role (no `minRole`) — it is the product's primary surface;
  it reads the model list from the existing catalog `/v1/models` and enforces nothing the gateway
  doesn't already enforce.
- COST HONESTY: per-turn cost derives from the platform's existing usage signal (the terminal usage
  frame); when usage is absent the UI shows an honest "—"/"not available", never a fabricated number
  (mirrors v27 billing-honesty + v38 slo-honesty).
- All FE honors WCAG-AA + the v23/v24 design-token bar (loading·empty·error·success states, keyboard
  operability, a11y landmarks); the BFF honors timeout + bounded handling and preserves its IO invariants.

## Shared / risky contracts (freeze these first)
- Streaming-proxy passthrough behavior (stream detection · pipe · abort propagation · non-stream byte-identical) -> owning task `streaming-bff`
- Chat workspace data contract (message model · streaming hook · usage-frame shape the cost readout consumes) -> owning task `chat-workspace-page`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [ ] streaming-bff        depends-on: none                 — BFF streams SSE chat-completions end-to-end (detect event-stream → pipe upstream body; AbortController disconnect propagation); non-stream JSON + auth paths byte-identical. FREEZES the streaming-proxy contract.
- [ ] chat-workspace-page  depends-on: streaming-bff        — `/app/chat`: multi-turn thread, composer, live token-by-token streaming render, Stop, four UI states, role-open nav entry; sends to a caller-selected model + params. FREEZES the chat UI data contract.
- [ ] chat-model-controls  depends-on: chat-workspace-page  — Model picker (from the catalog `/v1/models`) + request controls (system prompt, temperature) feeding the workspace.
- [ ] chat-cost-readout    depends-on: chat-workspace-page  — Per-turn token usage + cost readout from the stream's terminal usage frame, with an honest placeholder when usage is unavailable.

## Exit criteria (observable; map each to the task that delivers it)
- [ ] A signed-in user can open `/app/chat`, pick any catalog model, and hold a multi-turn conversation whose assistant responses stream token-by-token, with a working Stop control   (← streaming-bff, chat-workspace-page, chat-model-controls)
- [ ] Each assistant turn shows its token usage and cost, or an honest placeholder when usage is unavailable   (← chat-cost-readout)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- dashboard : <BFF streaming route + /app/chat workspace + cost readout — filled at close>
- gateway   : <expected untouched — v40 consumes /v1 unchanged; confirm at close>
- tooling / skill / book : <expected untouched>

### Cross-task evidence   (one row per task)
- <slug> : gate=<PASS|RISK-ACCEPTED> · tests=<n green> · residue=<none|note>

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [ ] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- goal: <restate the milestone goal — and the one evidence line that proves the ship meets it>

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] commit each task on a `feat/v40-chat-workspace` branch (BFF streaming → chat page → cost readout → .add bookkeeping)
- [ ] open PR to main; Tin reviews + merges (HTTPS push per [[git-push-https-gotcha]])
- [ ] v40 joins the releasable set (v33–v38 already pending); bundle into the next release cut when Tin calls it (release.md)
