/**
 * tests-bff/saml-callback-relay.test.tsx — RED suite for domain-auto-assign-login
 * TASK.md §3 v1, M6 (NET-NEW dashboard SAML callback relay).
 *
 * Ground: "NO apps/dashboard/app/auth/saml/ relay route exists — SAML has ZERO
 * dashboard wiring (no login button, no callback relay); OIDC is the only live
 * dashboard SSO surface." This suite pins the NEW POST /auth/saml/callback relay,
 * mirroring app/auth/oidc/callback/route.ts's own conventions EXACTLY:
 *   - forwards the SAMLResponse (+ RelayState) form body upstream to the gateway's
 *     POST /auth/saml/acs (the tenant is resolved server-side via the pending-
 *     request store — no cookies are forwarded, unlike OIDC's 3 handshake cookies),
 *   - on gateway 3xx -> forwards Location + every Set-Cookie verbatim (empty body),
 *     including a `?joined=1` success param (M6/M3),
 *   - on gateway 4xx -> 302 /login?sso_error=<gateway code>,
 *   - on gateway 5xx/unreachable -> 302 /login?sso_error=upstream (no body leak),
 *   - NEVER puts a token/secret in any response body.
 *
 * fetch is mocked directly (not msw) for deterministic upstream control — mirrors
 * tests-bff/oidc-callback-relay.test.tsx's own convention exactly.
 *
 * RED before build: `@/app/auth/saml/callback/route` does not exist (module not found).
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { NextRequest } from "next/server";

// ── RED: fails until Build creates this NEW route (mirrors app/auth/oidc/callback/route.ts) ──
import { POST as samlCallbackRelay } from "@/app/auth/saml/callback/route";

const FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1MSJ9.fakesignature";

/** A real gateway ACS success Response: 302 + Location(+?joined=1) + session cookie. */
function gatewaySuccess(location = "/?joined=1"): Response {
  const h = new Headers();
  h.append("location", location);
  h.append(
    "set-cookie",
    `ai_proxy_session=${FAKE_JWT}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=86400`,
  );
  return new Response(null, { status: 302, headers: h });
}

function gatewayProblem(code: string, status: number): Response {
  return new Response(JSON.stringify({ title: code, status, code }), {
    status,
    headers: { "content-type": "application/json" },
  });
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** Browser -> callback POST: the IdP's HTTP-POST-binding form submission. */
function callbackRequest(fields: Record<string, string> = { SAMLResponse: "PHNhbWw+ZmFrZTwvc2FtbD4=" }): NextRequest {
  const body = new URLSearchParams(fields);
  return new NextRequest("http://localhost:3000/auth/saml/callback", {
    method: "POST",
    headers: { "content-type": "application/x-www-form-urlencoded" },
    body: body.toString(),
  });
}

describe("saml-callback-relay — POST /auth/saml/callback (M6)", () => {
  it("test_saml_callback_success_forwards_location_and_cookies_with_joined", async () => {
    // covers: M6 — 3xx forwards Location (incl. ?joined=1) + Set-Cookie verbatim, empty body
    fetchMock.mockResolvedValue(gatewaySuccess("/?joined=1"));
    const res = await samlCallbackRelay(callbackRequest());

    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("/?joined=1");
    const cookies = res.headers.getSetCookie();
    expect(cookies.some((c) => c.startsWith("ai_proxy_session="))).toBe(true);
    const body = await res.text();
    expect(body).toBe("");
  });

  it("test_saml_callback_forwards_samlresponse_upstream", async () => {
    // covers: M6 — the SAMLResponse form field reaches the gateway's /auth/saml/acs verbatim
    fetchMock.mockResolvedValue(gatewaySuccess());
    await samlCallbackRelay(callbackRequest({ SAMLResponse: "RkFLRV9TQU1MX1JFU1BPTlNF", RelayState: "" }));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const calledUrl = new URL(String(fetchMock.mock.calls[0][0]));
    expect(calledUrl.pathname).toBe("/auth/saml/acs");
    const opts = fetchMock.mock.calls[0][1] as RequestInit;
    const sentBody = String(opts.body ?? "");
    expect(sentBody).toContain("SAMLResponse=RkFLRV9TQU1MX1JFU1BPTlNF");
    // the relay does NOT follow the gateway redirect server-side
    expect(opts.redirect).toBe("manual");
  });

  it("test_saml_callback_4xx_bounces_to_login", async () => {
    // covers: R2 — a failed SAML ACS never emits ?joined; it bounces with a sanitized code
    fetchMock.mockResolvedValue(gatewayProblem("ERR_SAML_SIGNATURE_INVALID", 401));
    const res = await samlCallbackRelay(callbackRequest());

    expect(res.status).toBe(302);
    const loc = res.headers.get("location") ?? "";
    expect(loc.startsWith("/login?sso_error=")).toBe(true);
    expect(loc).toContain("ERR_SAML_SIGNATURE_INVALID");
    expect(loc).not.toContain("joined");
    expect(await res.text()).toBe("");
  });

  it("test_saml_callback_5xx_sanitized", async () => {
    fetchMock.mockResolvedValue(new Response("gateway exploded: secret-trace", { status: 500 }));
    const res = await samlCallbackRelay(callbackRequest());

    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("/login?sso_error=upstream");
    const body = await res.text();
    expect(body).toBe("");
    expect(body).not.toContain("secret-trace");
  });

  it("test_no_token_in_any_saml_body", async () => {
    // covers: M6 — no token/secret ever appears in the relay's response BODY
    fetchMock.mockResolvedValue(gatewaySuccess());
    const ok = await samlCallbackRelay(callbackRequest());
    expect(await ok.text()).not.toContain(FAKE_JWT);

    fetchMock.mockResolvedValue(gatewayProblem("ERR_SAML_TENANT_CONFLICT", 409));
    const bad = await samlCallbackRelay(callbackRequest());
    expect(await bad.text()).not.toContain(FAKE_JWT);
  });
});
