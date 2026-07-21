/**
 * tests-bff/oidc-login-relay.test.tsx — RED suite for v15 oidc-login-relay.
 *
 * The pre-auth BFF relay GET /api/auth/oidc/login forwards the gateway's
 * GET /auth/oidc/login 302 + Set-Cookie verbatim WITHOUT an auth check (the
 * /api/gw/* catch-all 401s pre-auth). Plus a "Sign in with SSO" navigation link
 * on the login form.
 *
 * fetch is mocked directly (not msw) for deterministic control of the upstream
 * 302 / Set-Cookie / throw and to assert redirect:"manual" + the forwarded URL.
 *
 * RED before build: `@/app/api/auth/oidc/login/route` does not exist (module
 * not found) and LoginForm has no SSO link.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { NextRequest } from "next/server";
import { render, screen } from "@testing-library/react";
import React from "react";

import { GET as oidcLoginRelay } from "@/app/api/auth/oidc/login/route";
import { LoginForm } from "@/components/auth/LoginForm";

const IDP_LOCATION =
  "https://idp.example.com/authorize?response_type=code&client_id=abc&redirect_uri=https%3A%2F%2Fgw%2Fauth%2Foidc%2Fcallback&scope=openid+email+profile&state=S1&nonce=N1";
const OIDC_COOKIES = [
  "oidc_state=S1; HttpOnly; SameSite=Lax; Path=/auth/oidc; Max-Age=300",
  "oidc_nonce=N1; HttpOnly; SameSite=Lax; Path=/auth/oidc; Max-Age=300",
  "oidc_tenant_id=t-1; HttpOnly; SameSite=Lax; Path=/auth/oidc; Max-Age=300",
];

/** Build a real Response carrying a 302 + Location + the three oidc cookies. */
function gateway302(): Response {
  const h = new Headers();
  h.append("location", IDP_LOCATION);
  for (const c of OIDC_COOKIES) h.append("set-cookie", c);
  return new Response(null, { status: 302, headers: h });
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

function relayRequest(query = ""): NextRequest {
  return new NextRequest(`http://localhost:3000/api/auth/oidc/login${query}`, {
    method: "GET",
  });
}

describe("oidc-login-relay — GET /api/auth/oidc/login", () => {
  it("test_relay_forwards_302_and_cookies", async () => {
    fetchMock.mockResolvedValue(gateway302());
    const res = await oidcLoginRelay(relayRequest());

    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe(IDP_LOCATION);
    const cookies = res.headers.getSetCookie();
    expect(cookies).toHaveLength(3);
    expect(cookies.some((c) => c.startsWith("oidc_state="))).toBe(true);
    expect(cookies.some((c) => c.startsWith("oidc_nonce="))).toBe(true);
    expect(cookies.some((c) => c.startsWith("oidc_tenant_id="))).toBe(true);
  });

  it("test_relay_no_session_still_relays", async () => {
    fetchMock.mockResolvedValue(gateway302());
    // request carries NO ai_proxy_session cookie
    const res = await oidcLoginRelay(relayRequest());
    expect(res.status).toBe(302); // NOT 401
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("test_relay_forwards_only_domain", async () => {
    fetchMock.mockResolvedValue(gateway302());
    await oidcLoginRelay(
      relayRequest("?domain=acme.com&redirect_uri=https://evil.test&next=/x&state=hax"),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const calledUrl = String(fetchMock.mock.calls[0][0]);
    const u = new URL(calledUrl);
    expect(u.pathname).toBe("/auth/oidc/login");
    expect(u.searchParams.get("domain")).toBe("acme.com");
    expect(u.searchParams.has("redirect_uri")).toBe(false);
    expect(u.searchParams.has("next")).toBe(false);
    expect(u.searchParams.has("state")).toBe(false);
  });

  it("test_relay_does_not_follow_redirect", async () => {
    fetchMock.mockResolvedValue(gateway302());
    const res = await oidcLoginRelay(relayRequest());
    // the relay returned the IdP Location itself (did not fetch the IdP)
    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe(IDP_LOCATION);
    // and it asked the fetch layer NOT to follow the 302
    const opts = fetchMock.mock.calls[0][1] as RequestInit;
    expect(opts.redirect).toBe("manual");
  });

  it("test_relay_forwards_404_not_configured", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ code: "ERR_OIDC_NOT_CONFIGURED" }), {
        status: 404,
        headers: { "content-type": "application/json" },
      }),
    );
    const res = await oidcLoginRelay(relayRequest());
    expect(res.status).toBe(404);
    const body = (await res.json()) as { code?: string };
    expect(body.code).toBe("ERR_OIDC_NOT_CONFIGURED");
  });

  it("test_relay_gateway_unreachable_502", async () => {
    fetchMock.mockRejectedValue(new Error("ECONNREFUSED"));
    const res = await oidcLoginRelay(relayRequest());
    expect(res.status).toBe(502);
    const body = (await res.json()) as { code?: string };
    expect(body.code).toBe("ERR_BFF_GATEWAY_UNREACHABLE");
    // no upstream error detail / secret leaked
    expect(JSON.stringify(body)).not.toContain("ECONNREFUSED");
  });

  it("test_relay_gateway_5xx_sanitized_502_no_body_leak", async () => {
    // an unauthenticated pre-auth route must NOT relay an upstream 5xx body —
    // a gateway error becomes a sanitized 502 (defense-in-depth, security review).
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ internal: "postgres://admin:s3cr3t@db/prod" }), {
        status: 500,
        headers: { "content-type": "application/json" },
      }),
    );
    const res = await oidcLoginRelay(relayRequest());
    expect(res.status).toBe(502);
    const raw = await res.text();
    expect(raw).not.toContain("s3cr3t");
    expect(raw).not.toContain("postgres");
    expect(JSON.parse(raw).code).toBe("ERR_BFF_GATEWAY_ERROR");
  });

  it("test_relay_malicious_domain_is_encoded_not_smuggled", async () => {
    // a crafted domain value must stay a single encoded param — it cannot
    // inject extra query params or break out of the query (SSRF/smuggle guard).
    fetchMock.mockResolvedValue(gateway302());
    await oidcLoginRelay(relayRequest("?domain=foo%26state%3DINJECTED%23frag"));
    const u = new URL(String(fetchMock.mock.calls[0][0]));
    // exactly one param, fully decoded back to the literal hostile string
    expect([...u.searchParams.keys()]).toEqual(["domain"]);
    expect(u.searchParams.get("domain")).toBe("foo&state=INJECTED#frag");
    expect(u.searchParams.has("state")).toBe(false);
    expect(u.pathname).toBe("/auth/oidc/login");
  });

  it("test_relay_calls_gateway_oidc_login", async () => {
    fetchMock.mockResolvedValue(gateway302());
    await oidcLoginRelay(relayRequest());
    const calledUrl = String(fetchMock.mock.calls[0][0]);
    // host-agnostic: pin the gateway OIDC-login path with no query when no domain
    expect(calledUrl).toMatch(/\/auth\/oidc\/login$/);
  });
});

describe("LoginForm — SSO entry", () => {
  it("test_loginform_has_sso_button_and_domain_field", () => {
    render(<LoginForm />);
    // SSO is now a Button (sso-login-button task supersedes the v24 <a> design);
    // the per-tenant ?domain= behavior is covered by tests/sso-login.test.tsx.
    expect(
      screen.getByRole("button", { name: /sign in with sso/i }),
    ).toBeInTheDocument();
    // RETARGET (merge-login-email-field): the separate "Work email or domain" input is retired —
    // the merged "Email" field below now drives SSO as well as password login. The guarantee this
    // test encodes (the SSO button has a field feeding its ?domain=) is unchanged; the control moved.
    expect(screen.queryByLabelText(/work email or domain/i)).toBeNull();
    // the email/password form is unchanged — and is now also the SSO domain source
    expect(screen.getByLabelText(/^email$/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/password/i)).toBeInTheDocument();
  });
});
