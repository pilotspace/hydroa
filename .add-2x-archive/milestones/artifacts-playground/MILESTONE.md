# MILESTONE: Artifacts Playground

goal: The Artifacts workspace becomes a Console-grade artifact library/file-manager: typed list with search, inline image/text/JSON preview, drag-drop upload, download and delete, over the existing /v1/artifacts endpoints.
rationale: new-major — milestone 4 of the "AI feature depth (Console-grade)" program. Pass-through rebuild of the thin artifacts surface over the existing /v1/artifacts* endpoints (no backend delta). Built in parallel (worktree-isolated agent), reconciled to the milestone line.
stage: production · status: active · created: 2026-06-30T11:08:38+00:00

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  A Console-grade artifact library/file-manager at /app/artifacts — typed list + search/sort, inline image/text/JSON preview, drag-drop + picker upload, download, guarded delete; pass-through over /v1/artifacts*.
Out: artifact editing/versioning; folders/collections; sharing; non-image binary preview; backend changes (none — pass-through).

## Shared decisions & glossary deltas   (living — every task must honor these)
- Pass-through over /api/gw/v1/artifacts* (no gateway change); XSS-safe previews (escaped text/JSON, blob object URLs only); downloadArtifact signature stable (shared with video); four UI states + WCAG 2.2 AA; reuse chat Console language.

## Shared / risky contracts (freeze these first)
- Artifact list + preview + upload render contract (and the shared lib/artifacts.ts downloadArtifact stability) -> owning task `artifacts-console-rebuild`.

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] artifacts-console-rebuild   depends-on: none   — gate=PASS. File-manager list+search+sort, image/text/JSON preview (XSS-safe), drag-drop upload, download+delete; 23/23 tests; downloadArtifact stable. (Combined Console-grade rebuild, parallel build.)

## Exit criteria (observable; map each to the task that delivers it)
- [x] An operator can browse/search/sort artifacts, preview images and text/JSON inline (XSS-safe), upload via drag-drop or picker, download and delete   (← artifacts-console-rebuild)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.

### Ship by domain   (what changed, per bounded context)
- tooling : state.json task record only (tracking); add.py/templates untouched.
- skill / book : untouched.
- dashboard (product) : /app/artifacts rebuilt Console-grade — ArtifactsWorkspace (list+search+sort, image/text/JSON preview, drag-drop upload, guarded delete); lib/artifacts.ts + fetchArtifactText (downloadArtifact signature unchanged, shared with video). Pass-through over /v1/artifacts*.
- gateway (product) : untouched (pass-through).

### Cross-task evidence   (one row per task)
- artifacts-console-rebuild : gate=PASS · tests=23 green (11 original + 12 new) · full combined suite 897/0 · security review CLEAN (escaped previews, blob-only object URLs) · residue=none

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which): the single exit criterion → artifacts-console-rebuild row.
- goal: "The Artifacts workspace becomes a Console-grade artifact library/file-manager …" MET — file-manager with search/sort/preview/drag-drop/download/delete, XSS-safe, 23 green tests + security PASS prove the surface end-to-end.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [ ] Ship in the bundled "AI feature depth playgrounds" PR → main; Tin reviews + merges.
- [ ] No migration (pass-through) — rides the normal dashboard release.
- [ ] Fold §7 deltas at release time (none material for artifacts).
