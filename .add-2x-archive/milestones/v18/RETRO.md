════════════════════════════════════════════════════════════════════════
 v18 · Auth session hardening
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     1/1 done           CRITERIA  4/4 met
 GATES     1 PASS             WAIVERS   none

 goal  the dashboard BFF verifies the session JWT before trusting any
       identity claim — delegating to the gateway's authoritative GET
       /admin/auth/me (no secret sprawl) — and the test harness reaches
       a true 0 unhandled-request leak, closing the carried v17 auth/me
       follow-ups with zero behavioral regression

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 auth-me-session-verify      done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 EXIT CRITERIA  ●●●●●●●●●● 4/4 met

 LEARNINGS (5 carried)
   • SDD · open · a "UX-only" BFF endpoint that returns identity claims
     is a TRUST BOUNDARY: it must VERIFY the session token's signature
     (not base64-decode an unverified payload), even when the gateway
     enforces RBAC on proxied requests — the dashboard nav/role still
     derives from these claims (evidence: the escalated /api/auth/me
     gap; forged-token test now 401s fail-closed).
   • SDD · open · BFF-relay-to-the-authoritative-verifier beats local
     secret verification: forward the cookie as `Authorization: Bearer`
     to the gateway's existing `GET /admin/auth/me` (HS256+iss+exp) — no
     secret sprawl into the dashboard, ONE verifier that can't drift.
     Reusable for any BFF-trusts-a-token surface (evidence: route is a
     relay holding no signing secret; reuses the login/oidc relay
     shape).
   • TDD · open · an msw default handler must be an INITIAL handler
     passed to `setupServer(...)`, NEVER a runtime `server.use()` in a
     setupFile — `afterEach(resetHandlers())` wipes runtime handlers
     after test
   • ADD · open · a server-side fetch RELAY must set `redirect:
     "manual"` + treat every non-200 as fail-closed: a followed 3xx can
     chain to a trusted 200 from another origin (a fail-OPEN identity
     bypass) — caught by the adversarial refute-read, fixed in-scope
     (evidence: redirect→503 test).
   • ADD · open · a structural source-grep guard must be PRECISE, not a
     bare keyword: `/SECRET/i` false-positived on a comment that
     EXPLAINS the absence of a secret; the precise form matches
     `process.env.*(secret|key|hmac|…)` + jwt-lib imports + verify-call
     names (evidence: the test-precision fix during build; recurring
     "over-broad assert" smell from the v15/v17 TDD folds).

 DECIDE NEXT  consolidate learnings + archive-milestone v18
════════════════════════════════════════════════════════════════════════