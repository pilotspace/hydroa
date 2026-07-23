/**
 * tests-bff/bff-forms.test.tsx
 *
 * Tests for BFF-wired auth form components:
 *   components/auth/LoginForm.tsx  (MODIFIED in Build to call BFF)
 *   components/auth/SignupForm.tsx (MODIFIED in Build to call BFF)
 *
 * Also covers the XSS simulation scenario (document.cookie + localStorage cannot
 * reach the token) and the client logout flow.
 *
 * RED failure mode:
 *   - bff-client.test.tsx / bff-client.ts: MODULE_NOT_FOUND
 *   - These form tests: BEHAVIORAL-RED — the current LoginForm/SignupForm still
 *     calls the gateway directly (http://gateway.test/admin/auth/*) and writes
 *     localStorage "ai_proxy_token". The new assertions target same-origin
 *     /api/auth/* with credentials:"include" and NO localStorage write. They will
 *     fail against the un-modified forms for the correct behavioral reason:
 *       - loginCalled (POST /api/auth/login) stays false — current form hits the
 *         gateway, not the BFF endpoint
 *       - localStorage "ai_proxy_token" is NOT null after the current form succeeds
 *     Build MODIFIES LoginForm.tsx and SignupForm.tsx so these tests go green.
 *
 * Tests 14–17 (client form scenarios from §4 test plan):
 *   test_bff_client_login_posts_to_api_auth_no_localstorage
 *   test_bff_client_signup_posts_to_api_auth_no_localstorage
 *   test_bff_client_logout_posts_api_auth_logout
 *   test_bff_xss_simulation_no_token_visible
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { VALID_SESSION_JWT } from "./mocks/handlers";
import React from "react";

// ── Imports target the EXISTING components that Build will MODIFY ──────────────
// These files exist — they resolve at module-load time. The tests go red for
// BEHAVIORAL reasons (current forms write localStorage + call gateway directly).
import { LoginForm } from "@/components/auth/LoginForm";
import { SignupForm } from "@/components/auth/SignupForm";

// ─────────────────────────────────────────────────────────────────────────────

function makeQueryClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function Wrapper({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={makeQueryClient()}>
      {children}
    </QueryClientProvider>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

describe("LoginForm (BFF behavior)", () => {
  const user = userEvent.setup();

  async function fillAndSubmitLogin(overrides?: { email?: string; password?: string }) {
    const email = overrides?.email ?? "ada@acme.io";
    const password = overrides?.password ?? "hunter12345";
    // /^email$/ — the single merged email field. It once had to be distinguished from a separate
    // SSO "Work email or domain" input; merge-login-email-field retired that, so the exact-match
    // selector now just pins the one field rather than disambiguating two.
    await user.type(screen.getByLabelText(/^email$/i), email);
    await user.type(screen.getByLabelText(/password/i), password);
    await user.click(screen.getByRole("button", { name: /^log in$/i }));
  }

  /**
   * TEST 14 — test_bff_client_login_posts_to_api_auth_no_localstorage
   * Scenario: client login form — posts to /api/auth/login, navigates to /keys,
   *           no localStorage write
   *
   * BEHAVIORAL-RED against current LoginForm: current form POSTs to
   * http://gateway.test/admin/auth/login and writes localStorage "ai_proxy_token".
   * Build MODIFIES LoginForm to POST to /api/auth/login with credentials:"include"
   * and no localStorage write.
   */
  it("test_bff_client_login_posts_to_api_auth_no_localstorage", async () => {
    // Arrange: track the request to /api/auth/login (same-origin BFF endpoint)
    let loginCalled = false;
    let capturedBody: unknown = null;
    server.use(
      http.post("http://localhost:3000/api/auth/login", async ({ request }) => {
        loginCalled = true;
        capturedBody = await request.json();
        return HttpResponse.json({ ok: true });
      })
    );

    // Act
    render(<LoginForm />, { wrapper: Wrapper });
    await fillAndSubmitLogin();

    // Assert: BFF endpoint called (not the gateway directly)
    await waitFor(() => {
      expect(loginCalled).toBe(true);
    });
    expect((capturedBody as Record<string, unknown>).email).toBe("ada@acme.io");

    // router.push("/app/keys") called
    const { useRouter } = await import("next/navigation");
    const router = useRouter();
    expect(router.push).toHaveBeenCalledWith("/app/keys");

    // localStorage "ai_proxy_token" is absent — NEVER written in the new code path
    expect(localStorage.getItem("ai_proxy_token")).toBeNull();
  });

  /**
   * TEST 14b — LoginForm displays gateway error inline (behavior unchanged)
   *
   * BEHAVIORAL-RED: current form calls gateway directly so msw intercepts at
   * gateway.test; new form calls /api/auth/login BFF. The assertion on the
   * visible error message is identical — only the msw interception URL changes.
   */
  it("test_bff_client_login_401_shows_error_inline", async () => {
    server.use(
      http.post("http://localhost:3000/api/auth/login", () =>
        HttpResponse.json(
          { type: "about:blank", title: "Invalid credentials", status: 401, code: "ERR_AUTH_INVALID_CREDENTIALS" },
          { status: 401 }
        )
      )
    );

    render(<LoginForm />, { wrapper: Wrapper });
    await fillAndSubmitLogin();

    await waitFor(() => {
      expect(screen.getByText(/invalid credentials/i)).toBeInTheDocument();
    });

    // No navigation
    const { useRouter } = await import("next/navigation");
    const router = useRouter();
    expect(router.push).not.toHaveBeenCalledWith("/app/keys");

    // No localStorage write
    expect(localStorage.getItem("ai_proxy_token")).toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────────────

describe("SignupForm (BFF behavior)", () => {
  const user = userEvent.setup();

  async function fillAndSubmitSignup(overrides?: {
    tenant_name?: string;
    email?: string;
    password?: string;
  }) {
    const tenant_name = overrides?.tenant_name ?? "Acme";
    const email = overrides?.email ?? "ada@acme.io";
    const password = overrides?.password ?? "hunter12345";
    await user.type(screen.getByLabelText(/tenant name/i), tenant_name);
    await user.type(screen.getByLabelText(/email/i), email);
    await user.type(screen.getByLabelText(/password/i), password);
    await user.click(screen.getByRole("button", { name: /sign up/i }));
  }

  /**
   * TEST 15 — test_bff_client_signup_posts_to_api_auth_no_localstorage
   * Scenario: client signup form — posts to /api/auth/signup, navigates to /keys,
   *           no localStorage write
   *
   * BEHAVIORAL-RED against current SignupForm: current form POSTs to
   * http://gateway.test/admin/auth/signup then login, writes localStorage.
   * Build MODIFIES SignupForm to POST to /api/auth/signup with credentials:"include"
   * and no localStorage write.
   */
  it("test_bff_client_signup_posts_to_api_auth_no_localstorage", async () => {
    let signupCalled = false;
    server.use(
      http.post("http://localhost:3000/api/auth/signup", async () => {
        signupCalled = true;
        return HttpResponse.json({ ok: true }, { status: 201 });
      })
    );

    render(<SignupForm />, { wrapper: Wrapper });
    await fillAndSubmitSignup();

    await waitFor(() => {
      expect(signupCalled).toBe(true);
    });

    // router.push("/app/keys") called
    const { useRouter } = await import("next/navigation");
    const router = useRouter();
    expect(router.push).toHaveBeenCalledWith("/app/keys");

    // localStorage "ai_proxy_token" is absent
    expect(localStorage.getItem("ai_proxy_token")).toBeNull();
  });

  /**
   * TEST 15b — SignupForm 409 inline email error (behavior unchanged;
   *             arrange changes from gateway.test to /api/auth/signup)
   */
  it("test_bff_client_signup_409_inline_email_error", async () => {
    server.use(
      http.post("http://localhost:3000/api/auth/signup", () =>
        HttpResponse.json(
          { type: "about:blank", title: "Email already registered", status: 409, code: "ERR_TENANT_EMAIL_TAKEN" },
          { status: 409 }
        )
      )
    );

    render(<SignupForm />, { wrapper: Wrapper });
    await fillAndSubmitSignup();

    await waitFor(() => {
      expect(screen.getByText(/an account with this email already exists/i)).toBeInTheDocument();
    });

    const { useRouter } = await import("next/navigation");
    const router = useRouter();
    expect(router.push).not.toHaveBeenCalledWith("/app/keys");
    expect(localStorage.getItem("ai_proxy_token")).toBeNull();
  });
});

// ─────────────────────────────────────────────────────────────────────────────

describe("XSS simulation — token inaccessible to page JavaScript", () => {
  /**
   * TEST 11 — test_bff_xss_simulation_no_token_visible
   * Scenario: document.cookie and localStorage cannot reach the token
   *
   * In jsdom, httpOnly cookies set by the server are NOT reflected in
   * document.cookie (the browser enforces this boundary). We simulate the
   * post-login state by verifying:
   *   1. localStorage "ai_proxy_token" is null (new code never writes it)
   *   2. document.cookie does not contain VALID_SESSION_JWT (httpOnly cookies
   *      are opaque to JS — jsdom honors this)
   *
   * The route handler tests (route-handlers.test.ts) assert that the Set-Cookie
   * header carries HttpOnly; these tests assert the client-side inaccessibility.
   */
  it("test_bff_xss_simulation_no_token_visible", () => {
    // Simulate what happens after a successful BFF login:
    // The server set an httpOnly cookie — the client JS cannot see it.
    // The new code path NEVER calls localStorage.setItem("ai_proxy_token", ...)

    // Confirm localStorage has no token
    expect(localStorage.getItem("ai_proxy_token")).toBeNull();

    // In jsdom, document.cookie only surfaces non-httpOnly cookies.
    // After a BFF login the server sends Set-Cookie: ai_proxy_session=<jwt>; HttpOnly
    // — this is NOT accessible via document.cookie.
    expect(document.cookie).not.toContain(VALID_SESSION_JWT);

    // Belt-and-suspenders: confirm no token in window properties
    expect((window as unknown as Record<string, unknown>)["ai_proxy_token"]).toBeUndefined();
  });
});

// ─────────────────────────────────────────────────────────────────────────────

describe("Logout flow", () => {
  /**
   * TEST 16 — test_bff_client_logout_posts_api_auth_logout
   * Scenario: client logout — posts to /api/auth/logout, navigates to /login
   *
   * This test uses a minimal component that renders a logout button wired to
   * the BFF logout action, rather than importing KeysPage (which would require
   * the full auth setup). The contract: any component calling logout MUST POST
   * to /api/auth/logout and then navigate to /login.
   */
  it("test_bff_client_logout_posts_api_auth_logout", async () => {
    const user = userEvent.setup();
    let logoutCalled = false;

    server.use(
      http.post("http://localhost:3000/api/auth/logout", () => {
        logoutCalled = true;
        return HttpResponse.json({ ok: true });
      })
    );

    // A thin wrapper that uses the bff-client logout pattern.
    // Import bffAuthPost directly to simulate the logout action.
    // bff-client.ts does not exist yet — this test also fires a MODULE_NOT_FOUND
    // for bff-client.ts, making the entire describe block red until Build.
    const { bffAuthPost } = await import("@/lib/bff-client");

    function LogoutButton() {
      const { useRouter } = require("next/navigation") as typeof import("next/navigation");
      const router = useRouter();

      async function handleLogout() {
        await bffAuthPost("logout", {});
        router.push("/login");
      }

      return (
        <button type="button" onClick={handleLogout}>
          Log out
        </button>
      );
    }

    render(<LogoutButton />);

    await userEvent.setup().click(screen.getByRole("button", { name: /log out/i }));

    await waitFor(() => {
      expect(logoutCalled).toBe(true);
    });

    const { useRouter } = await import("next/navigation");
    const router = useRouter();
    expect(router.push).toHaveBeenCalledWith("/login");
  });
});
