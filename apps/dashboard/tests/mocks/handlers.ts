/**
 * Default msw handlers — baseline happy-path responses.
 * Individual tests override these via server.use(...) before rendering.
 *
 * Base URL: http://gateway.test  (matches GATEWAY_URL in setup.ts)
 */

import { http, HttpResponse } from "msw";

const BASE = "http://gateway.test";
const APP = "http://localhost:3000";

/** A minimal but structurally valid JWT.
 *  header.payload.signature where payload = { sub: "u1", exp: far-future }
 *  Base64url-encoded so the client-side expiry check can decode it.
 */
export const VALID_JWT = [
  btoa(JSON.stringify({ alg: "HS256", typ: "JWT" }))
    .replace(/=/g, "")
    .replace(/\+/g, "-")
    .replace(/\//g, "_"),
  btoa(
    JSON.stringify({ sub: "user-1", exp: Math.floor(Date.now() / 1000) + 86400 })
  )
    .replace(/=/g, "")
    .replace(/\+/g, "-")
    .replace(/\//g, "_"),
  "fakesignature",
].join(".");

export const defaultHandlers = [
  // ── auth ──────────────────────────────────────────────────────────────────

  // Same-origin BFF identity default (v18). MUST be an INITIAL handler (not a
  // runtime server.use) so afterEach resetHandlers() PRESERVES it — otherwise it
  // is wiped after test #1 and later useCurrentUser renders leak an unhandled
  // /api/auth/me (the carried v17 0-leak, load-dependent). Defaults to role
  // "member"; tests needing "owner" override via server.use (LIFO precedence).
  // exp:null mirrors the hardened relay route's stable shape (no consumer reads it).
  http.get(`${APP}/api/auth/me`, () =>
    HttpResponse.json({
      user_id: "00000000-0000-0000-0000-000000000001",
      tenant_id: "00000000-0000-0000-0000-000000000099",
      email: "ada@acme.io",
      role: "member",
      exp: null,
    })
  ),

  // impersonation-ui (M7): DashboardShell now calls useImpersonationStatus()
  // unconditionally, so every existing test that renders DashboardShell (directly,
  // or via a page that mounts inside it) makes this call for the first time. MUST
  // be an INITIAL handler (not a runtime server.use) for the same reason as the
  // /api/auth/me default above — resetHandlers() must preserve it. Tests asserting
  // impersonation-specific behavior override via their own server.use(...).
  http.get(`${APP}/api/platform/impersonation`, () =>
    HttpResponse.json({ active: false })
  ),

  http.post(`${BASE}/admin/auth/signup`, () =>
    HttpResponse.json(
      { tenant_id: "t-1", user_id: "u-1" },
      { status: 201 }
    )
  ),

  http.post(`${BASE}/admin/auth/login`, () =>
    HttpResponse.json({
      access_token: VALID_JWT,
      token_type: "bearer",
      expires_in: 86400,
    })
  ),

  // ── keys ──────────────────────────────────────────────────────────────────

  http.get(`${BASE}/admin/keys`, () =>
    HttpResponse.json([
      {
        key_id: "kid-default",
        name: "default-key",
        prefix: "sk-default",
        created_at: "2026-01-01T00:00:00Z",
        revoked_at: null,
      },
    ])
  ),

  http.post(`${BASE}/admin/keys`, () =>
    HttpResponse.json(
      {
        key_id: "kid-new",
        name: "new-key",
        key: "sk-new.DEFAULTSECRET",
      },
      { status: 201 }
    )
  ),

  http.delete(`${BASE}/admin/keys/:key_id`, () =>
    new HttpResponse(null, { status: 204 })
  ),

  // residency-tiers-ui (M7/M8): ModelsPage now fires GET /admin/residency-policy
  // unconditionally (its own independent read). MUST be an INITIAL handler (not a
  // runtime server.use) for the same reason as /api/auth/me / impersonation above —
  // resetHandlers() must preserve it so tests/catalog-sync.test.tsx (and any other
  // pre-existing ModelsPage render) stays green without per-test edits. Default =
  // unpinned; tests asserting ineligibility behavior override per scenario.
  http.get(`${APP}/api/gw/admin/residency-policy`, () =>
    HttpResponse.json({ region: null, updated_at: null })
  ),

  // residency-tiers-ui (M10): CreateKeyDialog reads GET /admin/service-tiers once per
  // open (plain fetch, works with or without a QueryClientProvider ancestor — several
  // pre-existing tests in tests/keys-dialog-a11y.test.tsx and tests/ui-ux-verify.test.tsx
  // render CreateKeyDialog standalone with NO QueryClientProvider). Default = the
  // service-tiers seed so those tests stay green untouched.
  http.get(`${APP}/api/gw/admin/service-tiers`, () =>
    HttpResponse.json({ default_tier: "standard", priority_markup_pct: "25" })
  ),

  // self-serve-plans-catalog (M4): PlanSeatsPage now fires GET /admin/plans unconditionally
  // (its own independent read feeding the Upgrade dialog's live menu). MUST be an INITIAL
  // handler (not a runtime server.use) for the same reason as residency-policy/service-tiers
  // above — resetHandlers() must preserve it so every existing PlanSeatsPage render (e.g.
  // tests/billing-plan.test.tsx) stays green untouched. Default = empty catalog; tests
  // asserting live-menu content override per scenario.
  http.get(`${APP}/api/gw/admin/plans`, () => HttpResponse.json({ plans: [] })),

  // dns-verify-softeners (M8): the domain-claims console's challenge card fires
  // GET /admin/domain-claims/registrar-hint?domain= as soon as a claim is created
  // (its NS-inferred deep-link display). MUST be an INITIAL handler (not a runtime
  // server.use) for the same reason as the reads above — resetHandlers() must
  // preserve it so the frozen domain-claims-console create-flow test (which renders
  // the card without mocking this new read) stays green under onUnhandledRequest:
  // "error". Default = the graceful fallback shape (a static provider list); tests
  // asserting a concrete deep link override via server.use per scenario.
  http.get(`${APP}/api/gw/admin/domain-claims/registrar-hint`, ({ request }) => {
    const domain = new URL(request.url).searchParams.get("domain") ?? "";
    return HttpResponse.json({ domain, registrar: null, deep_link_url: null, fallback: true });
  }),

  // invite-by-domain-ui (M1/M2): DomainClaimsSettings now fires GET
  // /admin/domain-invite-links unconditionally (its own independent read feeding
  // <DomainInviteLinkSection>'s activeLink prop). MUST be an INITIAL handler (not
  // a runtime server.use) for the same reason as registrar-hint above —
  // resetHandlers() must preserve it so every pre-existing DomainClaimsSettings
  // render (e.g. tests/domain-claims-console.test.tsx, which has verified rows
  // and does not mock this new read) stays green under onUnhandledRequest:
  // "error". Default = no active links; tests asserting a concrete active link
  // override via server.use per scenario.
  http.get(`${APP}/api/gw/admin/domain-invite-links`, () => HttpResponse.json({ links: [] })),
];
