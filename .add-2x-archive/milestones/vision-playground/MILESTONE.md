# MILESTONE: Vision Playground

goal: The Vision workspace becomes a Console-grade multimodal inspector: media preview, a multi-turn Q&A thread with markdown answers, and a model picker, over /v1/chat/completions image/video content parts.
rationale: new-major — milestone 5 of the "AI feature depth (Console-grade)" program. Pass-through rebuild of the thin vision surface over /v1/chat/completions multimodal content parts (no backend delta). Built in parallel (worktree-isolated agent), reconciled to the milestone line.
stage: production · status: active · created: 2026-06-30T11:08:38+00:00

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  A Console-grade multimodal inspector at /app/vision — media (image/video) drop/upload + preview, a multi-turn role=log Q&A thread with markdown answers, a Gemini-multimodal model picker, per-response metadata; pass-through over /v1/chat/completions image_url/video_url content parts.
Out: streaming answers (single-shot non-stream kept); non-Gemini multimodal providers; image generation; document/PDF understanding; backend changes (none — pass-through).

## Shared decisions & glossary deltas   (living — every task must honor these)
- Pass-through over /api/gw/v1/chat/completions (image_url/video_url parts) + /admin/catalog/models; answers render ONLY via MessageMarkdown (no raw-HTML injection); media preview from user data URL only; four UI states + WCAG 2.2 AA; reuse chat Console language.

## Shared / risky contracts (freeze these first)
- Vision media-preview + multi-turn markdown thread render contract (+ the image_url/video_url content-part shape) -> owning task `vision-console-rebuild`.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] vision-console-rebuild   depends-on: none   — gate=PASS. Media preview + multi-turn markdown Q&A thread + Gemini model picker + per-response metadata; 12/12 tests; XSS-safe answers. (Combined Console-grade rebuild, parallel build.)

## Exit criteria (observable; map each to the task that delivers it)
- [x] An operator can attach an image/video, ask multiple questions, and read markdown answers in a thread with per-response metadata — XSS-safe   (← vision-console-rebuild)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : state.json task record only (tracking); add.py/templates untouched.
- skill / book : untouched.
- dashboard (product) : /app/vision rebuilt Console-grade — VisionWorkspace (media drop/preview, role=log multi-turn markdown thread via MessageMarkdown, Gemini model picker, per-response metadata); lib/vision.ts + askVisionWithMeta (askVision unchanged). Pass-through over /v1/chat/completions.
- gateway (product) : untouched (pass-through).

### Cross-task evidence   (one row per task)
- vision-console-rebuild : gate=PASS · tests=12 green (8 original + 4 new) · full combined suite 897/0 · security review CLEAN (MessageMarkdown no rehype-raw, data-URL-only media) · residue=MIME-guard-before-readAsDataURL (defensive NIT, §7 delta)

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which): the single exit criterion → vision-console-rebuild row.
- goal: "The Vision workspace becomes a Console-grade multimodal inspector …" MET — media preview + multi-turn markdown Q&A thread + model picker + metadata, XSS-safe, 12 green tests + security PASS prove the surface end-to-end.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] Ship in the bundled "AI feature depth playgrounds" PR → main; Tin reviews + merges.
- [ ] No migration (pass-through) — rides the normal dashboard release.
- [ ] Fold §7 deltas at release time (MIME guard before readAsDataURL — defensive UX nit).
