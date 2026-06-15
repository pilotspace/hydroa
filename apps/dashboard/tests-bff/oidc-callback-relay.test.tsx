/**
 * tests-bff/oidc-callback-relay.test.tsx — RED suite for v17 oidc-callback-relay.
 *
 * The pre-auth BFF relay GET /auth/oidc/callback carries the browser through the
 * gateway's code→session exchange (the gateway does state/nonce/token/exchange and
 * mints ai_proxy_session). The relay:
 *   - forwards code+state (query) + ONLY the 3 oidc_* cookies upstream,
 *   - on gateway 3xx → forwards Location + every Set-Cookie verbatim (empty body),
 *   - on gateway 4xx → 302 /login?sso_error=<gateway code>,
 *   - on gateway 5xx/unexpected/unreachable → 302 /login?sso_error=upstream (no body leak),
 *   - NEVER puts a token/secret in any response body.
 * Plus the login page surfaces a generic SSO-failure alert when ?sso_error is set.
 *
 * fetch is mocked directly (not msw) for deterministic upstream control — mirrors
 * the v15 oidc-login-relay suite.
 *
 * RED before build: `@/app/auth/oidc/callback/route` does not exist (module not
 * found) and the login page has no sso_error alert.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { NextRequest } from "next/server";
import { render, screen } from "@testing-library/react";
import React from "react";

// ── RED: these fail until Build creates the route + adds the login-page alert ──
import { GET as oidcCallbackRelay } from "@/app/auth/oidc/callback/route";
import LoginPage from "@/app/(auth)/login/page";

const FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1MSJ9.fakesignature";
const POST_LOGIN = "/";

/** A real gateway success Response: 302 + Location + session cookie + cleared oidc_*. */
function gatewaySuccess(): Response {
  const h = new Headers();
  h.append("location", POST_LOGIN);
  h.append(
    "set-cookie",
    `ai_proxy_session=${FAKE_JWT}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=86400`,
  );
  h.append("set-cookie", "oidc_state=; HttpOnly; SameSite=Lax; Path=/auth/oidc; Max-Age=0");
  h.append("set-cookie", "oidc_nonce=; HttpOnly; SameSite=Lax; Path=/auth/oidc; Max-Age=0");
  h.append("set-cookie", "oidc_tenant_id=; HttpOnly; SameSite=Lax; Path=/auth/oidc; Max-Age=0");
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

/** Browser → callback request: code+state query, oidc_* cookies + an unrelated session cookie. */
function callbackRequest(query = "?code=C1&state=S1"): NextRequest {
  return new NextRequest(`http://localhost:3000/auth/oidc/callback${query}`, {
    method: "GET",
    headers: {
      cookie:
        "oidc_state=S1; oidc_nonce=N1; oidc_tenant_id=t-1; ai_proxy_session=STALE_SESSION; other=x",
    },
  });
}

describe("oidc-callback-relay — GET /auth/oidc/callback", () => {
  it("test_callback_success_forwards_location_and_cookies", async () => {
    fetchMock.mockResolvedValue(gatewaySuccess());
    const res = await oidcCallbackRelay(callbackRequest());

    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe(POST_LOGIN);
    const cookies = res.headers.getSetCookie();
    expect(cookies.some((c) => c.startsWith("ai_proxy_session="))).toBe(true);
    expect(cookies.filter((c) => c.startsWith("oidc_")).length).toBe(3); // cleared oidc_*
    // body is empty (no token in the body)
    const body = await res.text();
    expect(body).toBe("");
  });

  it("test_only_oidc_cookies_forwarded_upstream", async () => {
    fetchMock.mockResolvedValue(gatewaySuccess());
    await oidcCallbackRelay(callbackRequest());

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const opts = fetchMock.mock.calls[0][1] as RequestInit;
    const sentCookie = new Headers(opts.headers).get("cookie") ?? "";
    expect(sentCookie).toContain("oidc_state=S1");
    expect(sentCookie).toContain("oidc_nonce=N1");
    expect(sentCookie).toContain("oidc_tenant_id=t-1");
    // minimal disclosure: the stale session + unrelated cookies are NOT forwarded upstream
    expect(sentCookie).not.toContain("ai_proxy_session");
    expect(sentCookie).not.toContain("other=");
    // and the code/state were forwarded
    const calledUrl = new URL(String(fetchMock.mock.calls[0][0]));
    expect(calledUrl.pathname).toBe("/auth/oidc/callback");
    expect(calledUrl.searchParams.get("code")).toBe("C1");
    expect(calledUrl.searchParams.get("state")).toBe("S1");
    // and the relay does NOT follow the gateway redirect server-side
    expect(opts.redirect).toBe("manual");
  });

  it("test_callback_4xx_bounces_to_login", async () => {
    fetchMock.mockResolvedValue(gatewayProblem("ERR_OIDC_STATE_MISMATCH", 400));
    const res = await oidcCallbackRelay(callbackRequest());

    expect(res.status).toBe(302);
    const loc = res.headers.get("location") ?? "";
    expect(loc.startsWith("/login?sso_error=")).toBe(true);
    expect(loc).toContain("ERR_OIDC_STATE_MISMATCH");
    // no token / no upstream body in the relay response
    expect(await res.text()).toBe("");
    // and the gateway problem body is not leaked anywhere in the response
    expect(res.headers.getSetCookie().some((c) => c.includes("ai_proxy_session"))).toBe(false);
  });

  it("test_callback_5xx_sanitized", async () => {
    fetchMock.mockResolvedValue(
      new Response("gateway exploded: secret-trace", { status: 500 }),
    );
    const res = await oidcCallbackRelay(callbackRequest());

    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("/login?sso_error=upstream");
    const body = await res.text();
    expect(body).toBe("");
    expect(body).not.toContain("secret-trace");
  });

  it("test_callback_gateway_unreachable", async () => {
    fetchMock.mockRejectedValue(new Error("ECONNREFUSED"));
    const res = await oidcCallbackRelay(callbackRequest());

    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("/login?sso_error=upstream");
    expect(await res.text()).toBe("");
  });

  it("test_no_token_in_any_body", async () => {
    // success path: token only in the forwarded Set-Cookie, never the body
    fetchMock.mockResolvedValue(gatewaySuccess());
    const ok = await oidcCallbackRelay(callbackRequest());
    expect(await ok.text()).not.toContain(FAKE_JWT);
    // 4xx path
    fetchMock.mockResolvedValue(gatewayProblem("ERR_OIDC_TOKEN_INVALID", 400));
    const bad = await oidcCallbackRelay(callbackRequest());
    expect(await bad.text()).not.toContain(FAKE_JWT);
  });
});

describe("login page — SSO error surface", () => {
  it("test_login_page_shows_sso_error", async () => {
    const ui = await LoginPage({ searchParams: Promise.resolve({ sso_error: "ERR_OIDC_STATE_MISMATCH" }) });
    render(ui);
    const alert = screen.getByRole("alert");
    expect(alert).toBeInTheDocument();
    // a real generic message is shown (not a blank alert)
    expect(alert).toHaveTextContent("Single sign-on failed");
    // generic message — the raw gateway code is NOT shown to the user
    expect(alert).not.toHaveTextContent("ERR_OIDC_STATE_MISMATCH");
  });

  it("test_login_page_no_error_when_absent", async () => {
    const ui = await LoginPage({ searchParams: Promise.resolve({}) });
    render(ui);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
