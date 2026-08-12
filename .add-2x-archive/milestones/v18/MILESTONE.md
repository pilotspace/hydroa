# MILESTONE: Auth session hardening

goal: the dashboard BFF verifies the session JWT before trusting any identity claim — delegating to the gateway's authoritative GET /admin/auth/me (no secret sprawl) — and the test harness reaches a true 0 unhandled-request leak, closing the carried v17 auth/me follow-ups with zero behavioral regression
rationale: sub-milestone — at the v17 fold Tin RECLASSIFIED the `/api/auth/me` no-verify (previously a "settled UX-only tradeoff") as a real defense-in-depth SECURITY gap, and chose to clear the carried auth/me debt FIRST before sizing the next LiteLLM-parity milestone. A focused, security-scoped milestone keeps the parity arc unpolluted.
stage: production · status: active · created: 2026-06-15

> SDD living doc for this milestone. Keep it THIN: breadth, shared decisions, and
> exit criteria only — per-task detail lives in each `.add/tasks/<slug>/TASK.md`,
> written just-in-time. Update this doc whenever a task reveals a milestone gap.

## Scope
In:  the BFF `GET /api/auth/me` becomes a VERIFYING relay (delegates to the gateway's authoritative
     `GET /admin/auth/me`); the test harness reaches a true 0 unhandled `/api/auth/me` leak.
Out: gateway-side changes (the verify endpoint already exists, unchanged); local HS256 verification in
     the BFF (rejected — secret sprawl); SSO/OIDC login changes; any other surface's auth (separate work).

## Shared decisions & glossary deltas   (living — every task must honor these)
- A same-origin BFF endpoint that returns identity claims is a TRUST BOUNDARY — it must verify (or
  delegate verification of) the session token; "the gateway enforces on proxied requests" is insufficient.
- The gateway stays the SOLE holder of the JWT signing secret + the authoritative verifier (no sprawl).

## Shared / risky contracts (freeze these first)
- `GET /api/auth/me` (BFF relay verify; fail-closed 401/503; exp:null) -> owning task auth-me-session-verify (FROZEN @ v1)

## Tasks (breadth-first decomposition; detail lives in each TASK.md)
- [x] auth-me-session-verify   depends-on: none   — BFF verifies the session JWT via gateway relay + true 0-leak harness

## Exit criteria (observable; map each to the task that delivers it)
- [x] A forged / unsigned / expired session cookie no longer yields trusted identity claims from
      GET /api/auth/me — it 401s fail-closed (← auth-me-session-verify; verifier: auth-me-verify.test.ts forged/invalid/upstream tests)
- [x] The dashboard BFF holds NO JWT signing secret — verification is delegated to the gateway
      (← auth-me-session-verify; verifier: structural no-signing-secret test)
- [x] The full dashboard test suite reaches a TRUE 0 unhandled /api/auth/me request — the carried v17
      0-leak is closed (← auth-me-session-verify; verifier: full-suite stderr unhandled-count = 0, run ×2)
- [x] Zero behavioral regression on the committed floor: 263 tests green, coverage 88.35% lines,
      eslint 0, tsc 0 (← auth-me-session-verify; verifier: the §6 evidence block)
