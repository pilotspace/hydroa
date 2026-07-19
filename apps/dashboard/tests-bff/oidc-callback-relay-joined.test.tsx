/**
 * tests-bff/oidc-callback-relay-joined.test.tsx — RED-suite addition for
 * domain-auto-assign-login TASK.md §3 v1, M7 (OIDC relay carries ?joined=1 through).
 *
 * NEW FILE — does not edit the frozen tests-bff/oidc-callback-relay.test.tsx
 * (ADD's own non-negotiable: never weaken/edit a frozen test).
 *
 * §1 lowest-confidence assumption (⚠ flagged in TASK.md): "a query param appended
 * to the gateway's post-login redirect Location survives the OIDC relay intact —
 * lowest confidence because a relay that RECONSTRUCTS (vs forwards) the URL would
 * drop it." Reading app/auth/oidc/callback/route.ts today shows the success branch
 * copies `upstream.headers.get("location")` verbatim onto the relay response with
 * no parsing/reconstruction — so this is expected to be a GREEN-BY-DESIGN
 * regression pin (mirrors the documented pattern in
 * tests/domain_routing_unification/test_domain_routing_unification.py), not a red
 * one: M7 needs no dashboard code change, only this pin so a future "helpful"
 * rewrite of the relay can never silently drop the join signal again.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { NextRequest } from "next/server";

import { GET as oidcCallbackRelay } from "@/app/auth/oidc/callback/route";

const FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1MSJ9.fakesignature";

function gatewaySuccessWithJoined(): Response {
  const h = new Headers();
  h.append("location", "/?joined=1");
  h.append(
    "set-cookie",
    `ai_proxy_session=${FAKE_JWT}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=86400`,
  );
  return new Response(null, { status: 302, headers: h });
}

function gatewaySuccessNoJoined(): Response {
  const h = new Headers();
  h.append("location", "/");
  h.append(
    "set-cookie",
    `ai_proxy_session=${FAKE_JWT}; HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=86400`,
  );
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

function callbackRequest(query = "?code=C1&state=S1"): NextRequest {
  return new NextRequest(`http://localhost:3000/auth/oidc/callback${query}`, {
    method: "GET",
    headers: { cookie: "oidc_state=S1; oidc_nonce=N1; oidc_tenant_id=t-1" },
  });
}

describe("oidc-callback-relay — ?joined=1 passthrough (M7)", () => {
  it("test_oidc_relay_forwards_joined_param_on_new_provision", async () => {
    fetchMock.mockResolvedValue(gatewaySuccessWithJoined());
    const res = await oidcCallbackRelay(callbackRequest());

    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("/?joined=1");
  });

  it("test_oidc_relay_forwards_no_joined_param_on_returning_user", async () => {
    fetchMock.mockResolvedValue(gatewaySuccessNoJoined());
    const res = await oidcCallbackRelay(callbackRequest());

    expect(res.status).toBe(302);
    expect(res.headers.get("location")).toBe("/");
    expect(res.headers.get("location")).not.toContain("joined");
  });
});
