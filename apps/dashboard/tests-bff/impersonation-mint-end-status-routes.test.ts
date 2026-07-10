/**
 * tests-bff/impersonation-mint-end-status-routes.test.ts
 *
 * RED suite for the impersonation-ui task's 3 NEW BFF routes (TASK.md §3 CONTRACT
 * Part A): POST/GET /api/platform/impersonation (Mint/Status) and
 * DELETE /api/platform/impersonation/sessions/{sessionId} (End).
 *
 * Covers the M1 cookie-envelope design end-to-end: Mint WRITES the envelope
 * ({token, session_id, expires_at}, URI-encoded JSON — never a bare JWT) from the
 * gateway's own authoritative response fields; Status READS it back (recovering
 * session_id/expires_at even on a fresh page load, with zero gateway change,
 * since GET /admin/auth/me itself carries neither field); End reads ONLY the
 * ORIGINAL ai_proxy_session cookie, unconditionally, even when the impersonation
 * cookie is also present on the same request (the client-side mirror of the
 * sibling impersonation-session-lifecycle task's own frozen "End requires the
 * ORIGINAL JWT" contract — TASK.md Safety rule, §5).
 *
 * Pattern: import the handler functions directly, construct a NextRequest (with a
 * combined Cookie: header via requestWithCookies), call the handler, assert on the
 * NextResponse — mirrors tests-bff/route-handlers.test.ts's own direct-handler-
 * invocation convention exactly.
 *
 * RED failure mode: every import below throws MODULE_NOT_FOUND — none of these 3
 * route files exist yet (Build creates them). Kept in its own file, separate from
 * impersonation-gw-proxy-cookie-precedence.test.ts, which imports the ALREADY-
 * EXISTING /api/gw/[...path]/route.ts and so reds for a different (logic-
 * assertion) reason — a file mixing a not-yet-existing import with an existing
 * one would fail to load ENTIRELY (Node/vitest resolves all top-level static
 * imports before running a single test), collaterally hiding the existing file's
 * own real logic-assertion failures behind an uninformative crash.
 */

import { describe, it, expect } from "vitest";
import { NextRequest } from "next/server";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";

// ── RED: these imports fail until Build creates the route handler files ────────
import {
  POST as mintHandler,
  GET as statusHandler,
} from "@/app/api/platform/impersonation/route";
import { DELETE as endHandler } from "@/app/api/platform/impersonation/sessions/[sessionId]/route";

// ─────────────────────────────────────────────────────────────────────────────

const GATEWAY = "http://gateway.test";
const MINT_URL = "http://localhost:3000/api/platform/impersonation";
const STATUS_URL = "http://localhost:3000/api/platform/impersonation";
const endUrl = (sessionId: string) =>
  `http://localhost:3000/api/platform/impersonation/sessions/${sessionId}`;

const TENANT_ID = "11111111-1111-1111-1111-111111111111";
const USER_ID = "22222222-2222-2222-2222-222222222222";
const SESSION_ID = "33333333-3333-3333-3333-333333333333";
const ORIGINAL_TOKEN = "original-session-jwt-001";
const IMPERSONATION_TOKEN = "impersonation-jwt-002";

/** Build a combined `Cookie:` header from a name->value map (skips absent keys). */
function requestWithCookies(
  url: string,
  method: string,
  cookies: Record<string, string>,
  body?: unknown,
): NextRequest {
  const cookieHeader = Object.entries(cookies)
    .map(([k, v]) => `${k}=${v}`)
    .join("; ");
  const headers: Record<string, string> = {};
  if (cookieHeader) headers.Cookie = cookieHeader;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  return new NextRequest(url, {
    method,
    headers,
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
}

/** M1: the impersonation cookie's VALUE is a URI-encoded JSON envelope, never a bare JWT. */
function buildEnvelope(payload: {
  token: string;
  session_id: string;
  expires_at: number;
}): string {
  return encodeURIComponent(JSON.stringify(payload));
}

function getSetCookie(res: Response): string | null {
  return res.headers.get("set-cookie") ?? res.headers.get("Set-Cookie");
}

/** Extract + decode ONE named cookie's envelope out of a Set-Cookie header value. */
function decodeEnvelopeFromSetCookie(
  setCookie: string,
  cookieName: string,
): { token: string; session_id: string; expires_at: number } | null {
  const match = setCookie.match(new RegExp(`${cookieName}=([^;]+)`));
  if (!match) return null;
  try {
    return JSON.parse(decodeURIComponent(match[1])) as {
      token: string;
      session_id: string;
      expires_at: number;
    };
  } catch {
    return null;
  }
}

// ─────────────────────────────────────────────────────────────────────────────

describe("POST /api/platform/impersonation (Mint)", () => {
  it("test_mint_relays_original_cookie_sets_impersonation_cookie_never_echoes_token", async () => {
    let capturedAuth: string | null = null;
    server.use(
      http.post(
        `${GATEWAY}/admin/platform/tenants/${TENANT_ID}/users/${USER_ID}/impersonate`,
        ({ request }) => {
          capturedAuth = request.headers.get("Authorization");
          return HttpResponse.json(
            {
              token: IMPERSONATION_TOKEN,
              expires_in: 900,
              session_id: SESSION_ID,
              target: {
                user_id: USER_ID,
                tenant_id: TENANT_ID,
                email: "bob@acme.io",
                role: "member",
              },
            },
            { status: 201 },
          );
        },
      ),
    );

    const req = requestWithCookies(
      MINT_URL,
      "POST",
      { ai_proxy_session: ORIGINAL_TOKEN },
      { tenant_id: TENANT_ID, user_id: USER_ID },
    );

    const res = await mintHandler(req);

    // Gateway received the ORIGINAL session's token — Mint never authenticates
    // with anything else (there is no impersonation cookie yet at Mint time).
    expect(capturedAuth).toBe(`Bearer ${ORIGINAL_TOKEN}`);

    expect(res.status).toBe(201);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.token).toBeUndefined(); // never echoed in the JSON body
    expect(body.session_id).toBe(SESSION_ID);
    expect(body.expires_in).toBe(900);
    expect(body.target).toEqual({
      user_id: USER_ID,
      tenant_id: TENANT_ID,
      email: "bob@acme.io",
      role: "member",
    });
    expect(JSON.stringify(body)).not.toContain(IMPERSONATION_TOKEN);

    // Set-Cookie names ONLY the impersonation cookie — ai_proxy_session is
    // never re-set (or touched) by a successful mint.
    const setCookie = getSetCookie(res);
    expect(setCookie).not.toBeNull();
    expect(setCookie).toContain("ai_proxy_impersonation_session=");
    expect(setCookie).not.toContain("ai_proxy_session=");
    expect(setCookie?.toLowerCase()).toContain("httponly");
    expect(setCookie?.toLowerCase()).toContain("samesite=strict");
    expect(setCookie?.toLowerCase()).toContain("path=/");
    expect(setCookie).toContain("Max-Age=900");

    // The cookie's VALUE is the M1 envelope — not a bare JWT — built from Mint's
    // own authoritative response fields (token/session_id/expires_in).
    const decoded = decodeEnvelopeFromSetCookie(
      setCookie as string,
      "ai_proxy_impersonation_session",
    );
    expect(decoded).not.toBeNull();
    expect(decoded?.token).toBe(IMPERSONATION_TOKEN);
    expect(decoded?.session_id).toBe(SESSION_ID);
    expect(Math.abs((decoded?.expires_at ?? 0) - (Date.now() + 900_000))).toBeLessThan(5_000);
  });

  it("test_mint_no_session_cookie_401_never_calls_upstream", async () => {
    let upstreamCalled = false;
    server.use(
      http.post(
        `${GATEWAY}/admin/platform/tenants/${TENANT_ID}/users/${USER_ID}/impersonate`,
        () => {
          upstreamCalled = true;
          return HttpResponse.json({}, { status: 201 });
        },
      ),
    );

    const req = requestWithCookies(MINT_URL, "POST", {}, { tenant_id: TENANT_ID, user_id: USER_ID });
    const res = await mintHandler(req);

    expect(res.status).toBe(401);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_AUTH_NO_SESSION");
    expect(upstreamCalled).toBe(false);
    expect(getSetCookie(res)).toBeNull();
  });

  it("test_mint_relays_gateway_403_target_invalid", async () => {
    server.use(
      http.post(
        `${GATEWAY}/admin/platform/tenants/${TENANT_ID}/users/${USER_ID}/impersonate`,
        () =>
          HttpResponse.json(
            {
              type: "about:blank",
              title: "Cannot impersonate into the platform tenant",
              status: 403,
              code: "ERR_IMPERSONATION_TARGET_INVALID",
            },
            { status: 403 },
          ),
      ),
    );

    const req = requestWithCookies(
      MINT_URL,
      "POST",
      { ai_proxy_session: ORIGINAL_TOKEN },
      { tenant_id: TENANT_ID, user_id: USER_ID },
    );
    const res = await mintHandler(req);

    expect(res.status).toBe(403);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_IMPERSONATION_TARGET_INVALID");
    expect(getSetCookie(res)).toBeNull();
  });

  it("test_mint_relays_gateway_409_already_active", async () => {
    server.use(
      http.post(
        `${GATEWAY}/admin/platform/tenants/${TENANT_ID}/users/${USER_ID}/impersonate`,
        () =>
          HttpResponse.json(
            {
              type: "about:blank",
              title: "An impersonation session is already active",
              status: 409,
              code: "ERR_IMPERSONATION_SESSION_ALREADY_ACTIVE",
            },
            { status: 409 },
          ),
      ),
    );

    const req = requestWithCookies(
      MINT_URL,
      "POST",
      { ai_proxy_session: ORIGINAL_TOKEN },
      { tenant_id: TENANT_ID, user_id: USER_ID },
    );
    const res = await mintHandler(req);

    expect(res.status).toBe(409);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_IMPERSONATION_SESSION_ALREADY_ACTIVE");
    expect(getSetCookie(res)).toBeNull();
  });
});

describe("GET /api/platform/impersonation (Status)", () => {
  it("test_status_no_impersonation_cookie_returns_inactive", async () => {
    let meCalled = false;
    server.use(
      http.get(`${GATEWAY}/admin/auth/me`, () => {
        meCalled = true;
        return HttpResponse.json({
          user_id: "x",
          tenant_id: "y",
          email: "z@acme.io",
          role: "member",
        });
      }),
    );

    const req = requestWithCookies(STATUS_URL, "GET", {});
    const res = await statusHandler(req);

    expect(res.status).toBe(200);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toEqual({ active: false });
    // Nothing to resolve — neither relay call is worth making.
    expect(meCalled).toBe(false);
  });

  it("test_status_active_resolves_target_and_actor_via_two_relay_calls", async () => {
    server.use(
      http.get(`${GATEWAY}/admin/auth/me`, ({ request }) => {
        const auth = request.headers.get("Authorization");
        if (auth === `Bearer ${IMPERSONATION_TOKEN}`) {
          return HttpResponse.json({
            user_id: USER_ID,
            tenant_id: TENANT_ID,
            email: "bob@acme.io",
            role: "member",
          });
        }
        if (auth === `Bearer ${ORIGINAL_TOKEN}`) {
          return HttpResponse.json({
            user_id: "actor-1",
            tenant_id: "platform-tenant-id",
            email: "root@platform.internal",
            role: "superadmin",
          });
        }
        return HttpResponse.json({ code: "ERR_AUTH_INVALID_SESSION" }, { status: 401 });
      }),
    );

    const expiresAt = Date.now() + 15 * 60 * 1000;
    const envelope = buildEnvelope({
      token: IMPERSONATION_TOKEN,
      session_id: SESSION_ID,
      expires_at: expiresAt,
    });
    const req = requestWithCookies(STATUS_URL, "GET", {
      ai_proxy_session: ORIGINAL_TOKEN,
      ai_proxy_impersonation_session: envelope,
    });
    const res = await statusHandler(req);

    expect(res.status).toBe(200);
    const body = (await res.json()) as Record<string, unknown>;
    // session_id/expires_at come from the M1 envelope directly, never from
    // GET /me (which carries neither field) — the reason this survives refresh.
    expect(body).toEqual({
      active: true,
      session_id: SESSION_ID,
      expires_at: expiresAt,
      target: { user_id: USER_ID, tenant_id: TENANT_ID, email: "bob@acme.io", role: "member" },
      actor: { email: "root@platform.internal", role: "superadmin" },
    });
  });

  it("test_status_invalid_impersonation_cookie_self_heals", async () => {
    server.use(
      http.get(`${GATEWAY}/admin/auth/me`, () =>
        HttpResponse.json({ code: "ERR_AUTH_INVALID_SESSION" }, { status: 401 }),
      ),
    );

    const envelope = buildEnvelope({
      token: "a-token-the-gateway-now-rejects",
      session_id: SESSION_ID,
      expires_at: Date.now() + 60_000,
    });
    const req = requestWithCookies(STATUS_URL, "GET", {
      ai_proxy_impersonation_session: envelope,
    });
    const res = await statusHandler(req);

    expect(res.status).toBe(200);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toEqual({ active: false });

    const setCookie = getSetCookie(res);
    expect(setCookie).not.toBeNull();
    expect(setCookie).toContain("ai_proxy_impersonation_session=");
    const cookieLower = setCookie?.toLowerCase() ?? "";
    expect(cookieLower.includes("max-age=0") || cookieLower.includes("expires=")).toBe(true);
  });

  it("test_status_actor_relay_failure_degrades_to_actor_null", async () => {
    server.use(
      http.get(`${GATEWAY}/admin/auth/me`, ({ request }) => {
        const auth = request.headers.get("Authorization");
        if (auth === `Bearer ${IMPERSONATION_TOKEN}`) {
          return HttpResponse.json({
            user_id: USER_ID,
            tenant_id: TENANT_ID,
            email: "bob@acme.io",
            role: "member",
          });
        }
        return HttpResponse.json({ code: "ERR_AUTH_INVALID_SESSION" }, { status: 401 });
      }),
    );

    const expiresAt = Date.now() + 10 * 60 * 1000;
    const envelope = buildEnvelope({
      token: IMPERSONATION_TOKEN,
      session_id: SESSION_ID,
      expires_at: expiresAt,
    });
    // No ai_proxy_session at all — nothing to relay for the actor. Partial info
    // (target/session_id/expires_at/End control) beats a hard failure.
    const req = requestWithCookies(STATUS_URL, "GET", {
      ai_proxy_impersonation_session: envelope,
    });
    const res = await statusHandler(req);

    expect(res.status).toBe(200);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toEqual({
      active: true,
      session_id: SESSION_ID,
      expires_at: expiresAt,
      target: { user_id: USER_ID, tenant_id: TENANT_ID, email: "bob@acme.io", role: "member" },
      actor: null,
    });
  });

  it("test_status_malformed_envelope_degrades_to_inactive_never_throws", async () => {
    const req = requestWithCookies(STATUS_URL, "GET", {
      ai_proxy_impersonation_session: "not-a-valid-json-envelope",
    });

    const res = await statusHandler(req);

    // Never a thrown exception / 500 — a corrupted envelope degrades CLOSED.
    expect(res.status).toBe(200);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body).toEqual({ active: false });
    const setCookie = getSetCookie(res);
    expect(setCookie).not.toBeNull();
    expect(setCookie).toContain("ai_proxy_impersonation_session=");
  });
});

describe("DELETE /api/platform/impersonation/sessions/{sessionId} (End)", () => {
  it("test_end_uses_original_cookie_never_impersonation_even_when_both_present", async () => {
    let capturedAuth: string | null = null;
    server.use(
      http.delete(
        `${GATEWAY}/admin/platform/impersonation/sessions/${SESSION_ID}`,
        ({ request }) => {
          capturedAuth = request.headers.get("Authorization");
          return new HttpResponse(null, { status: 204 });
        },
      ),
    );

    const envelope = buildEnvelope({
      token: IMPERSONATION_TOKEN,
      session_id: SESSION_ID,
      expires_at: Date.now() + 60_000,
    });
    const req = requestWithCookies(endUrl(SESSION_ID), "DELETE", {
      ai_proxy_session: ORIGINAL_TOKEN,
      ai_proxy_impersonation_session: envelope,
    });

    const res = await endHandler(req, { params: Promise.resolve({ sessionId: SESSION_ID }) });

    // The frozen sibling contract's own "End requires the ORIGINAL JWT" rule,
    // mirrored client-side: non-negotiable even though both cookies are present.
    expect(capturedAuth).toBe(`Bearer ${ORIGINAL_TOKEN}`);
    expect(res.status).toBe(204);
  });

  it("test_end_no_session_cookie_401_never_calls_upstream", async () => {
    let upstreamCalled = false;
    server.use(
      http.delete(`${GATEWAY}/admin/platform/impersonation/sessions/${SESSION_ID}`, () => {
        upstreamCalled = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );

    // Impersonation cookie present, ORIGINAL absent — must still 401. End never
    // falls back to the impersonation cookie under any circumstance.
    const envelope = buildEnvelope({
      token: IMPERSONATION_TOKEN,
      session_id: SESSION_ID,
      expires_at: Date.now() + 60_000,
    });
    const req = requestWithCookies(endUrl(SESSION_ID), "DELETE", {
      ai_proxy_impersonation_session: envelope,
    });

    const res = await endHandler(req, { params: Promise.resolve({ sessionId: SESSION_ID }) });

    expect(res.status).toBe(401);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_AUTH_NO_SESSION");
    expect(upstreamCalled).toBe(false);
  });

  it("test_end_clears_impersonation_cookie_on_204", async () => {
    server.use(
      http.delete(
        `${GATEWAY}/admin/platform/impersonation/sessions/${SESSION_ID}`,
        () => new HttpResponse(null, { status: 204 }),
      ),
    );

    const req = requestWithCookies(endUrl(SESSION_ID), "DELETE", {
      ai_proxy_session: ORIGINAL_TOKEN,
    });
    const res = await endHandler(req, { params: Promise.resolve({ sessionId: SESSION_ID }) });

    expect(res.status).toBe(204);
    const setCookie = getSetCookie(res);
    expect(setCookie).not.toBeNull();
    expect(setCookie).toContain("ai_proxy_impersonation_session=");
    expect(setCookie?.toLowerCase()).toContain("max-age=0");
  });

  it("test_end_409_already_ended_does_not_clear_cookie", async () => {
    server.use(
      http.delete(
        `${GATEWAY}/admin/platform/impersonation/sessions/${SESSION_ID}`,
        () =>
          HttpResponse.json(
            {
              type: "about:blank",
              title: "Session already ended",
              status: 409,
              code: "ERR_IMPERSONATION_SESSION_ALREADY_ENDED",
            },
            { status: 409 },
          ),
      ),
    );

    const req = requestWithCookies(endUrl(SESSION_ID), "DELETE", {
      ai_proxy_session: ORIGINAL_TOKEN,
    });
    const res = await endHandler(req, { params: Promise.resolve({ sessionId: SESSION_ID }) });

    expect(res.status).toBe(409);
    const body = (await res.json()) as Record<string, unknown>;
    expect(body.code).toBe("ERR_IMPERSONATION_SESSION_ALREADY_ENDED");
    // An already-ended/expired session on the server should not have its client
    // cookie silently cleared behind a 409 — a retry stays diagnosable.
    expect(getSetCookie(res)).toBeNull();
  });
});
