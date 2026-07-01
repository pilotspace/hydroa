# MILESTONE: Video Playground

goal: The Video workspace becomes a Console-grade generation studio: prompt+params composer, a job gallery with rich status, inline video preview on success and download, over the existing async /v1/video/generations jobs.
rationale: new-major — milestone 6 (final) of the "AI feature depth (Console-grade)" program. Pass-through rebuild of the thin video surface over the existing async /v1/video/generations* job endpoints (+ /v1/artifacts download) — no backend delta. Built in parallel (worktree-isolated agent), reconciled to the milestone line.
stage: production · status: active · created: 2026-06-30T11:08:39+00:00

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  A Console-grade video generation studio at /app/video — prompt + optional params composer (catalog-narrowed model picker), a job gallery (newest-first) with rich status badges, inline <video> preview on success (blob object URL) + download, honest-degrade on no-provider-configured; polling that stops at terminal/unmount; pass-through over /v1/video/generations* + /v1/artifacts download.
Out: real video-provider adapters (HONEST no-provider degrade preserved — provider integration is a backend/external-key milestone); video editing/trimming; thumbnail generation; backend changes (none — pass-through).

## Shared decisions & glossary deltas   (living — every task must honor these)
- Pass-through over /api/gw/v1/video/generations* + /v1/artifacts/{id} (no gateway change); video preview from blob object URLs ONLY (revoked on unmount); poll loop bounded (stop at terminal/unmount, soft error keeps last-good); does NOT edit lib/artifacts.ts (consumes downloadArtifact as-is); four UI states + WCAG 2.2 AA; reuse chat Console language.

## Shared / risky contracts (freeze these first)
- Video composer + job-gallery + inline-player render contract (+ bounded poll loop, blob-only preview) -> owning task `video-console-rebuild`.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] video-console-rebuild   depends-on: none   — gate=PASS. Prompt+params composer + job gallery (rich status) + inline blob video preview + download, polling stops at terminal; 13/13 tests; lib/artifacts.ts untouched. (Combined Console-grade rebuild, parallel build.)

## Exit criteria (observable; map each to the task that delivers it)
- [x] An operator can compose a video job with params, watch its status in a gallery, preview the result inline on success (blob-only) and download it — with honest degrade when no provider is configured   (← video-console-rebuild)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : state.json task record only (tracking); add.py/templates untouched.
- skill / book : untouched.
- dashboard (product) : /app/video rebuilt Console-grade — VideoWorkspace (composer with model picker + optional params, job gallery with status badges, inline <video> blob preview, download, bounded polling); lib/video.ts createVideoJob + optional params (params serialized only when non-empty, {model,prompt} callers byte-identical); lib/artifacts.ts UNTOUCHED (downloadArtifact consumed). Pass-through over /v1/video/generations*.
- gateway (product) : untouched (pass-through; HONEST no-provider degrade preserved).

### Cross-task evidence   (one row per task)
- video-console-rebuild : gate=PASS · tests=13 green (11 original + 2 new) · full combined suite 897/0 · security review CLEAN (<video> blob-only revoked, friendlyError plain string, lib/artifacts.ts untouched) · residue=none

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which): the single exit criterion → video-console-rebuild row.
- goal: "The Video workspace becomes a Console-grade generation studio …" MET — composer + job gallery + inline blob preview + download with honest degrade, 13 green tests + security PASS prove the surface end-to-end.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] Ship in the bundled "AI feature depth playgrounds" PR → main; Tin reviews + merges.
- [ ] No migration (pass-through) — rides the normal dashboard release.
- [ ] Fold §7 deltas at release time (real video-provider adapter = external/backend milestone; none material here).
