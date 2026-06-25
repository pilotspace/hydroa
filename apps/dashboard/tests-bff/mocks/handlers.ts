/**
 * tests-bff/mocks/handlers.ts — default msw handlers for the BFF suite
 *
 * Two interception layers:
 *   1. http://gateway.test/* — simulates the upstream gateway (for BFF
 *      server-side fetch calls in route handler unit tests)
 *   2. http://localhost:3000/api/* — simulates the BFF routes themselves
 *      (for component/hook tests that use bff-client.ts)
 *
 * The VALID_SESSION_JWT is a structurally valid JWT with a future exp, role
 * "owner", and all claims needed by /api/auth/me. Same base64url encoding as
 * the v1 handlers.ts VALID_JWT.
 */

import { http, HttpResponse } from "msw";

const GATEWAY = "http://gateway.test";
const APP = "http://localhost:3000";

/** A minimal but structurally valid JWT — matches /api/auth/me claim shape */
export const VALID_SESSION_JWT = [
  btoa(JSON.stringify({ alg: "HS256", typ: "JWT" }))
    .replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_"),
  btoa(JSON.stringify({
    sub: "user-1",
    user_id: "00000000-0000-0000-0000-000000000001",
    tenant_id: "00000000-0000-0000-0000-000000000099",
    email: "ada@acme.io",
    role: "owner",
    exp: Math.floor(Date.now() / 1000) + 86400,
  })).replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_"),
  "fakesignature",
].join(".");

/** Gateway-level default handlers (for BFF route handler unit tests) */
export const gatewayHandlers = [
  http.post(`${GATEWAY}/admin/auth/login`, () =>
    HttpResponse.json({
      access_token: VALID_SESSION_JWT,
      token_type: "bearer",
      expires_in: 86400,
    })
  ),

  http.post(`${GATEWAY}/admin/auth/signup`, () =>
    HttpResponse.json({ tenant_id: "t-1", user_id: "u-1" }, { status: 201 })
  ),

  http.get(`${GATEWAY}/admin/keys`, () =>
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

  http.post(`${GATEWAY}/admin/keys`, () =>
    HttpResponse.json(
      { key_id: "kid-new", name: "new-key", key: "sk-new.DEFAULTSECRET" },
      { status: 201 }
    )
  ),

  http.delete(`${GATEWAY}/admin/keys/:key_id`, () =>
    new HttpResponse(null, { status: 204 })
  ),

  http.get(`${GATEWAY}/admin/usage`, () =>
    HttpResponse.json({
      total_cost_usd: "1.23",
      total_requests: 3,
      total_prompt_tokens: 300,
      total_completion_tokens: 150,
      records: [],
    })
  ),

  http.get(`${GATEWAY}/admin/budget`, () =>
    HttpResponse.json({ budget_usd_monthly: "25.00", spent_usd_month: "10.50" })
  ),

  http.put(`${GATEWAY}/admin/budget`, () =>
    HttpResponse.json({ budget_usd_monthly: "25.00" })
  ),

  http.get(`${GATEWAY}/v1/models`, () =>
    HttpResponse.json({ object: "list", data: [] })
  ),
];

/** Same-origin BFF route handlers (for component/hook tests using bff-client.ts) */
export const bffHandlers = [
  http.post(`${APP}/api/auth/login`, () =>
    HttpResponse.json({ ok: true })
  ),

  http.post(`${APP}/api/auth/signup`, () =>
    HttpResponse.json({ ok: true }, { status: 201 })
  ),

  http.post(`${APP}/api/auth/logout`, () =>
    HttpResponse.json({ ok: true })
  ),

  http.get(`${APP}/api/auth/me`, () =>
    HttpResponse.json({
      user_id: "00000000-0000-0000-0000-000000000001",
      tenant_id: "00000000-0000-0000-0000-000000000099",
      email: "ada@acme.io",
      role: "owner",
      // exp:null mirrors the hardened relay route's stable shape (the gateway
      // enforces expiry; no consumer reads exp) — no mock/contract drift.
      exp: null,
    })
  ),

  // NOTE: no broad gateway catch-all wildcard. The shared defaults must be CONCRETE per-path so a
  // forgotten per-test gw handler hits `onUnhandledRequest:"error"` and fails LOUDLY (a broad
  // wildcard returns wrong-but-silent data — the v15-folded convention). Tests that exercise a
  // gw path register their own `server.use(...)`. The common read default (the keys list, used by
  // the most surfaces) stays as a concrete handler below; everything else is per-test explicit.
  http.get(`${APP}/api/gw/admin/keys`, () =>
    HttpResponse.json([{ key_id: "kid-default", name: "default-key" }])
  ),

  // Baseline catalog-model list (v40 chat-model-controls): the chat ModelPicker
  // fetches this on mount, so every ChatWorkspace render needs it stubbed.
  // Tests asserting specific picker behavior override with their own server.use(...).
  http.get(`${APP}/api/gw/v1/models`, () =>
    HttpResponse.json({
      object: "list",
      data: [{ id: "openai/gpt-4o", name: "GPT-4o", context_length: 128000 }],
    })
  ),
];

export const defaultHandlers = [...gatewayHandlers, ...bffHandlers];
