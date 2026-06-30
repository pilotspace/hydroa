# MILESTONE: Chat — Console-grade playground

goal: The Chat workspace becomes a true Console-grade playground: full sampling-parameter control, tool/function calling, multimodal attachments, rich per-run metadata + cost, and first-class conversation management — a surface an operator runs real LLM work on.
rationale: new-major — milestone 1 of the "AI feature depth (Console-grade)" program. Born when v54's UI-only `ai-feature-pages-redesign` task was descoped: GROUND reconnaissance showed chat·voice·memory·artifacts·vision·video are thin CRUD playgrounds, and Tin (AskUserQuestion 2026-06-28) chose to "design the real product (new backend OK), Console-grade — OpenAI Playground / Anthropic Console feel," starting with Chat (the most mature, sets the quality bar). UNLIKE v54 this is NOT UI-only / byte-identical: it is a real feature rebuild — new contracts + TDD; existing chat tests evolve with the new contracts (legitimate feature change, never a contract-weakening). Reconnaissance baseline: the detached `.add/tasks/ai-feature-pages-redesign/TASK.md` §0 GROUND (full chat seam/testid map) + `tmp/ai-feature-build-spec.md`.
stage: production · status: active · created: 2026-06-28T14:48:43+00:00

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  A true Console-grade Chat playground for `/app/chat`. A 3-pane layout (sessions · conversation · a parameters/inspector panel). Full OpenAI-compatible sampling control (temperature · top_p · max_tokens · stop sequences · frequency/presence penalty · seed · response_format text|json_object) sent through the existing `/v1/chat/completions` wire (pass-through; no new provider work). Tool / function calling (define JSON-schema tools, render the model's tool_calls, supply a tool result, continue the run). Multimodal image attachments in the composer (content-part format the wire already accepts). Rich per-turn metadata (model · finish_reason · prompt/completion/total tokens · latency · cost) + a session/run inspector + running cost. First-class conversation management (rename · duplicate/fork · export JSON+markdown · search) — the ONE backend delta: a conversation rename/metadata endpoint. Streaming + cancel preserved.
Out: The other five features (voice·memory·artifacts·vision·video — each its own program milestone). Server-side tool EXECUTION (we render tool_calls + accept a manually-supplied result; the gateway does not run tools). Server-stored prompt presets (system-prompt presets start client-side; a preset store is a later delta if wanted). Side-by-side multi-model/param COMPARE (a stretch — deferred to a follow-up). Multi-user collaboration / share links. Any new provider/model capability beyond what the OpenAI-compatible wire already forwards.

## Shared decisions & glossary deltas   (living — every task must honor these)
- **Feature rebuild, NOT byte-identical** — this milestone changes the chat contracts; existing chat tests (tests-bff/chat-*, tests/chat-message-markdown) evolve WITH the new contracts via red/green TDD. Weakening a test to dodge a real regression is still forbidden; changing a test because the contract legitimately changed is the method.
- **Pass-through first** — sampling params, tools, and image content-parts ride the existing `/v1/chat/completions` proxy; prefer NO gateway change. Only conversation rename/metadata is a genuine backend delta (PATCH `/v1/conversations/:id`).
- **Design-before-code (UDD)** — the shell task carries a Console-grade design-confirm gate: Tin approves a captured Playground design BEFORE any build (re-establishes the quality bar after the rejected thin mocks).
- **Four UI states + a11y by construction** — Loading/Empty/Error/Success from `states.tsx`; one h1; decorative icons aria-hidden; WCAG 2.2 AA; the streaming thread stays a `role=log` live region.
- **Design-for-failure** — streaming uses AbortController + cancel; tool/continue loops bound retries; param inputs validate client-side before the wire call; no retry-storm on a settled 4xx.

## Shared / risky contracts (freeze these first)
- **Playground layout + design system** (the 3-pane shell, the parameters/inspector panel anatomy, the Console visual language) -> owning task `chat-playground-shell`. Every later task consumes this frozen shell — freeze it (with the design-confirm) first.
- **Completions request param contract** (which sampling/tool/attachment fields the UI sends through `/v1/chat/completions`, and how tool_calls + usage + finish_reason are read back) -> owning task `chat-parameters-panel` (params) + `chat-tools-functions` (tools).
- **Conversation metadata contract** (PATCH `/v1/conversations/:id` rename/metadata — the one backend delta + migration if needed) -> owning task `chat-conversation-mgmt`.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] chat-playground-shell     depends-on: none                 — The 3-pane Console layout (sessions · conversation · parameters/inspector) + the Playground design system; carries the design-confirm gate. Freezes the shell the rest consume.
- [x] chat-parameters-panel     depends-on: chat-playground-shell — Full sampling control (temperature·top_p·max_tokens·stop·frequency/presence penalty·seed·response_format) wired pass-through to `/v1/chat/completions`; validated, persisted per session.
- [x] chat-tools-functions      depends-on: chat-playground-shell — Define JSON-schema tools, send `tools`/`tool_choice`, render the model's `tool_calls`, supply a tool result, continue the run.
- [x] chat-attachments          depends-on: chat-playground-shell — Attach images to a user message (content-part format) with preview + size guard; the model answers about them.
- [x] chat-run-metadata-cost    depends-on: chat-playground-shell — Per-turn inspector (model·finish_reason·tokens·latency·cost) + session/run totals + running cost meter, from the usage frame already streamed.
- [x] chat-conversation-mgmt    depends-on: none                 — Rename·duplicate/fork·export(JSON+markdown)·search conversations; backend PATCH `/v1/conversations/:id` (the milestone's one backend delta).

## Exit criteria (observable; map each to the task that delivers it)
- [x] The chat surface is a 3-pane Console-grade playground matching an approved design   (← chat-playground-shell)
- [x] An operator can set temperature·top_p·max_tokens·stop·penalties·seed·response_format and the values reach the model on the next run   (← chat-parameters-panel)
- [x] An operator can define a tool, see the model's tool_call, supply a result, and the run continues with that result   (← chat-tools-functions)
- [x] An operator can attach an image to a message and the model answers about it   (← chat-attachments)
- [x] Each assistant turn shows model·finish_reason·tokens·latency·cost, and the session shows a running total   (← chat-run-metadata-cost)
- [x] An operator can rename, fork, export, and search conversations   (← chat-conversation-mgmt)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : state.json task records only (tracking); add.py/templates untouched.
- skill   : untouched.
- book    : untouched.
- dashboard (product) : the /app/chat surface rebuilt to Console-grade — 3-pane shell + design system (ConversationTopBar/InspectorPanel/ChatHistorySidebar), sampling-parameter panel, JSON-tool calling + tool-call cards, image attachments (AttachmentPreview + lib/chat/attachments.ts), per-turn metadata + session cost (CostReadout), conversation rename/fork/export/search. Streaming consumer (use-chat-stream.ts) extended for tools, content-parts, and finish_reason — pass-through, off-path byte-identical.
- gateway (product) : ONE additive delta — PATCH /v1/conversations/{id} (rename), tenant-scoped, no migration. No other gateway change (sampling/tools/attachments all ride the existing /v1/chat/completions).

### Cross-task evidence   (one row per task)
- chat-playground-shell    : gate=PASS · design-confirmed shell · residue=none
- chat-parameters-panel    : gate=PASS · omitted-when-unset sampling, provider-aware · residue=none
- chat-tools-functions     : gate=PASS · 10 tests · adversarial refute EARNED · residue=none
- chat-attachments         : gate=PASS · 13 tests (incl. falsified concurrent-cap regression) · residue=none material (persist/paste-drag/cross-provider data-URL = §7 deltas)
- chat-run-metadata-cost   : gate=PASS · 7 tests · finish_reason + session cost · residue=Inspector "Run" tab placeholder (§7 delta)
- chat-conversation-mgmt   : gate=PASS · gateway 47 (incl. 12 rename, cross-tenant 404 no-leak) + dashboard 11 · residue=image-parts not persisted on fork (§7 delta)
- INTEGRATION : merged on the up-to-date branch (agents built from a stale base → cherry-picked + resolved, #1-#4 preserved). Dashboard vitest 858/0, gateway conversations 47 passed, tsc 0, eslint 0.

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which): shell→shell row; sampling→parameters row; tools→tools row; attachments→attachments row; metadata/cost→metadata-cost row; rename/fork/export/search→conversation-mgmt row.
- goal: "The Chat workspace becomes a true Console-grade playground … a surface an operator runs real LLM work on." MET — an operator can configure full sampling, define+run tools, attach images, see per-turn model·finish_reason·tokens·latency·cost + a running session total, and rename/fork/export/search conversations; 905 tests green across dashboard+gateway prove the surface end-to-end.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] Open ONE PR for the whole chat-playground line (7 commits #1–#6 + state reconcile) from `feat/v54-ui-refinement` → `main`; Tin reviews + merges.
- [ ] After merge, run the gateway migration check is N/A (no new migration); deploy rides the normal dashboard + gateway release (no schema change).
- [ ] Fold this milestone's §7 deltas at release time (persist attachments, paste/drag, cross-provider data-URL verify, Inspector "Run" tab) — bundle into the next release notes (human-run, per release.md).
