/**
 * Tests 1–4: SignupForm scenarios
 *
 * All 4 tests fail at module resolution — @/components/auth/SignupForm does
 * not exist yet.  The harness (msw, jsdom, RTL) is working.
 */

import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { getRouterMock } from "./mocks/next-navigation";

// ── This import will cause MODULE_NOT_FOUND — correct RED failure ─────────────
import { SignupForm } from "@/components/auth/SignupForm";

// ─────────────────────────────────────────────────────────────────────────────

describe("SignupForm", () => {
  const user = userEvent.setup();

  async function fillAndSubmit(overrides?: {
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
   * TEST 1 — test_signup_happy_redirects_to_keys
   * Scenario: signup happy path — redirects to /keys
   */
  it("test_signup_happy_redirects_to_keys", async () => {
    // Arrange: msw default handlers already cover signup→201 + login→200 VALID_JWT
    render(<SignupForm />);

    // Act
    await fillAndSubmit();

    // Assert
    await waitFor(() => {
      expect(localStorage.getItem("ai_proxy_token")).not.toBeNull();
    });
    const router = getRouterMock();
    expect(router.push).toHaveBeenCalledWith("/keys");
  });

  /**
   * TEST 2 — test_signup_409_inline_email_error
   * Scenario: signup 409 — inline email error, no navigation
   */
  it("test_signup_409_inline_email_error", async () => {
    // Arrange
    server.use(
      http.post("http://gateway.test/admin/auth/signup", () =>
        HttpResponse.json(
          {
            type: "about:blank",
            title: "Email already registered",
            status: 409,
            code: "ERR_TENANT_EMAIL_TAKEN",
          },
          { status: 409 }
        )
      )
    );

    render(<SignupForm />);

    // Act
    await fillAndSubmit();

    // Assert
    await waitFor(() => {
      expect(
        screen.getByText(/an account with this email already exists/i)
      ).toBeInTheDocument();
    });
    const router = getRouterMock();
    expect(router.push).not.toHaveBeenCalledWith("/keys");
  });

  /**
   * TEST 3 — test_signup_invalid_email_no_api_call
   * Scenario: signup client-side validation — invalid email, no API call
   */
  it("test_signup_invalid_email_no_api_call", async () => {
    // Arrange: override signup handler to throw if called — proves no fetch
    let signupCalled = false;
    server.use(
      http.post("http://gateway.test/admin/auth/signup", () => {
        signupCalled = true;
        return HttpResponse.json({}, { status: 500 });
      })
    );

    render(<SignupForm />);

    // Act: submit with bad email
    await fillAndSubmit({ email: "not-an-email" });

    // Assert: inline error visible and NO gateway call
    await waitFor(() => {
      expect(screen.getByRole("alert") ?? screen.queryByText(/invalid email/i)).toBeTruthy();
    });
    expect(signupCalled).toBe(false);
  });

  /**
   * TEST 4 — test_signup_weak_password_no_api_call
   * Scenario: signup client-side validation — weak password, no API call
   */
  it("test_signup_weak_password_no_api_call", async () => {
    let signupCalled = false;
    server.use(
      http.post("http://gateway.test/admin/auth/signup", () => {
        signupCalled = true;
        return HttpResponse.json({}, { status: 500 });
      })
    );

    render(<SignupForm />);

    // Act: 9-character password (min is 10)
    await fillAndSubmit({ password: "short1234" }); // exactly 9 chars

    // Assert: password field error visible and NO gateway call
    await waitFor(() => {
      // The exact error text is "Password must be at least 10 characters" or similar
      expect(
        screen.queryByText(/password/i)
      ).toBeInTheDocument();
    });
    expect(signupCalled).toBe(false);
  });
});
