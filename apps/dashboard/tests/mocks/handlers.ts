/**
 * Default msw handlers — baseline happy-path responses.
 * Individual tests override these via server.use(...) before rendering.
 *
 * Base URL: http://gateway.test  (matches NEXT_PUBLIC_GATEWAY_URL in setup.ts)
 */

import { http, HttpResponse } from "msw";

const BASE = "http://gateway.test";

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
