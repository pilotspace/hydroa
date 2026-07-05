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
];
