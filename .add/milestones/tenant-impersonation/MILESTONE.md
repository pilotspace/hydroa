# MILESTONE: Tenant Impersonation

goal: A superadmin can act as a specific tenant user in a time-boxed, fully audited impersonation session where the real actor stays distinguishable in every downstream record
rationale: part of the "Full 5, admin-first" superadmin/platform-tenant roadmap (see `platform-identity`/`platform-admin-console`), sequenced third. Sized alongside `team-member-invite`/`platform-access-plan` per Tin's 2026-07-03 instruction to build in parallel.
stage: production · status: active · created: 2026-07-02T15:53:53+00:00
release: 0.10.0

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.
>
> Backfilled 2026-07-05: `impersonation-session-lifecycle` built and shipped (PR #58) before this
> doc's Scope/Tasks/Exit-criteria sections were ever filled in (only the header existed). The
> breakdown below is reconstructed from that task's own §0 GROUND + §7 OBSERVE disclosures, not a
> fresh design pass — flag anything that reads wrong rather than treat it as settled.

## Scope
In:
  - A superadmin can Mint a time-boxed (`GATEWAY_IMPERSONATION_SESSION_TTL_SECONDS`, default 900s)
    session against any of the 6 non-superadmin roles' users, receiving a JWT whose claims carry
    both the acting superadmin's identity and the impersonated target — every downstream record
    (audit, usage) stays attributable to the real actor, never silently as the target.
  - A superadmin can End a session explicitly, using their ORIGINAL (non-impersonation) JWT —
    never the active impersonation token itself (deliberate containment: the impersonation token
    can start a session but never end one, closing a self-extension loop).
  - At most one concurrent impersonation session per superadmin credential (M7); full Mint/End
    audit emission via the existing `emit_platform_audit` primitive.
Out (deferred to sibling tasks, not this task's job):
  - Any dashboard surface to Mint/End a session, or to see one in progress — `impersonation-ui`.
  - Live/real-time session revocation: today, an ended session's JWT remains technically valid
    against self-service routes until its own ≤900s natural expiry elapses (bounded, TTL-capped,
    superadmin-bearer-token-gated window) — closing that window is `impersonation-live-session-guard`.

## Shared decisions & glossary deltas   (living — every task must honor these)
- **End requires the ORIGINAL JWT, never the impersonation token** (FROZEN in
  `impersonation-session-lifecycle` §3) — any future task touching Mint/End must preserve this;
  reversing it reopens a containment-exception question that draft deliberately closed by avoiding it.
- **Concurrency cap is per-superadmin-credential, not per-target** — M7 caps at one *active Mint* per
  superadmin, not one per target user; a per-target lock was considered and rejected (real complexity,
  no stated need).

## Shared / risky contracts (freeze these first)
- `TokenService.issue()`'s `impersonation`/`ttl_seconds` optional kwargs (both `None` ⇒ byte-identical
  claims to every existing caller) -> owning task `impersonation-session-lifecycle` (already frozen @ v1)
- Whether `impersonation-ui`'s client-state cost of retaining two tokens (the active impersonation
  token + the original, needed for End) is acceptable as-is, or whether End's contract should change to
  admit a narrower "identity-admitting" dependency instead -> genuinely open, carried forward as a SPEC
  delta by `impersonation-session-lifecycle` — owning task for resolving it: `impersonation-ui`

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] impersonation-session-lifecycle   depends-on: none                             — Mint/End session lifecycle, TTL-capped JWT, one-concurrent-per-superadmin cap, full audit emission. DONE (PR #58).
- [ ] impersonation-ui                   depends-on: impersonation-session-lifecycle  — Superadmin-facing dashboard surface to Mint/End a session and see one in progress; must resolve the two-token (original + active) client-state question left open above.
- [ ] impersonation-live-session-guard   depends-on: impersonation-session-lifecycle  — Closes the bounded post-End window where an already-ended session's JWT still validates until its own natural TTL expiry. Tin flagged this as a live, undecided deploy-timing question: ship `impersonation-session-lifecycle` alone and accept the bounded window for now, or hold production deploy until this task ships alongside it.

## Exit criteria (observable; map each to the task that delivers it)
- [x] A superadmin can Mint a session against an eligible target user and receive a working,
      time-boxed impersonation JWT   (← impersonation-session-lifecycle)
- [x] Every Mint and End is audited via `emit_platform_audit`, with the real superadmin identity
      always distinguishable from the impersonated target in the record   (← impersonation-session-lifecycle)
- [x] A superadmin can Mint and End a session from the dashboard, without calling the API directly   (← impersonation-ui)
- [x] An ended session's token stops working immediately, not just at its natural TTL expiry   (← impersonation-live-session-guard)

## Close — ship review   (AI fills when every task is done — the evidence behind the engine gate, read before the boxes are checked)
> Whole-milestone, cross-task review the AI fills in. It is the evidence behind the EXISTING engine
> gate (milestone-done / checking the Exit-criteria boxes) — NOT a new approval. Tool-agnostic.
> Backfilled 2026-07-15 from an independent adversarial re-verification against `origin/main`@`6447eaa`
> (all 4 exit criteria re-confirmed by live code; all 5 security shared-decisions HOLD, no VIOLATED).

### Ship by domain   (what changed, per bounded context)
- tooling : untouched — no add.py / state.json / template changes; this is a product-code milestone.
- skill   : untouched.
- book    : untouched.
- gateway : new `platform_impersonation_router.py` (Mint/End), `impersonation_session_guard.py`
  (live per-request fail-closed revocation read), `impersonation_sessions` table
  (`1d563bf9b143_...`), `TokenService.issue()` `impersonation`/`ttl_seconds` kwargs, config
  `impersonation_session_ttl_seconds`=900 + `impersonation_live_check_timeout_seconds`=2.0.
- dashboard : impersonation BFF (`/api/platform/impersonation` Mint/Status/End), path-aware token
  resolution in `gw/[...path]` (admin/platform always → original session; else impersonation cookie),
  `impersonation-cookie.ts` fail-closed envelope codec, `ImpersonationBanner` + per-row action.

### Cross-task evidence   (one row per task)
- impersonation-session-lifecycle : gate=PASS · Mint/End + TTL-capped JWT + one-concurrent cap + full audit (PR #58) · residue=none (ImpersonationSession dataclass unused — cosmetic).
- impersonation-ui               : gate=PASS · dashboard Mint/End + banner; End reads ONLY original session cookie · residue=none.
- impersonation-live-session-guard : gate=PASS · fail-closed live revocation read at single authz choke point; pattern since adopted by newer routers (SAML/domain-claims) · residue=none.

### Goal met?   (map the evidence back to this milestone's Exit criteria — read before the Exit-criteria boxes are checked)
- [x] each Exit criterion above is satisfied by a Cross-task evidence row or a Ship-by-domain change (cite which)
- criterion 1 (Mint time-boxed JWT) ← impersonation-session-lifecycle; criterion 2 (audit real actor
  distinguishable) ← impersonation-session-lifecycle — **structurally guaranteed**: an impersonation
  JWT's role is always the target's own (never SUPERADMIN), so it cannot pass `require_superadmin` and
  cannot reach Mint/End; the audited `identity` is therefore only ever a real superadmin's; criterion 3
  (dashboard Mint/End) ← impersonation-ui; criterion 4 (ended token dies immediately) ← impersonation-live-session-guard.
- goal: a superadmin acts as a tenant user in a time-boxed, fully audited session with the real actor
  always distinguishable — proven by the dual-identity design (primary claims = target's; real actor
  carried only in the additive `ImpersonationContext`) + fail-closed live revocation guard.

## Release steps   (AI-DEFINED — fill the ordered steps to ship this milestone; engine records, human gate)
> The AI writes the release steps for THIS milestone here (hints, not engine commands). MERGE is one
> small step among them. These feed the release scope (release.md) when the cut is bundled.
- [x] All 3 tasks already merged to main (PRs #58 lifecycle; ui + live-session-guard, 006f791 2026-07-05).
- [ ] Fold cross-task deltas + add GLOSSARY "impersonation session" term at milestone fold.
- [ ] Bundle into the next release cut (release.md) — already effectively shipped in 0.8.0/0.9.0 code; confirm attribution row.
