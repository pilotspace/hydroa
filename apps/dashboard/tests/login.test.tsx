/**
 * Tests 5–6: LoginForm scenarios
 *
 * Both tests fail at module resolution — @/components/auth/LoginForm does not
 * exist yet.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { getRouterMock } from "./mocks/next-navigation";

// ── This import will cause MODULE_NOT_FOUND — correct RED failure ─────────────
import { LoginForm } from "@/components/auth/LoginForm";

// ─────────────────────────────────────────────────────────────────────────────

describe("LoginForm", () => {
  const user = userEvent.setup();

  async function fillAndSubmit(overrides?: {
    email?: string;
    password?: string;
  }) {
    const email = overrides?.email ?? "ada@acme.io";
    const password = overrides?.password ?? "hunter12345";

    await user.type(screen.getByLabelText(/email/i), email);
    await user.type(screen.getByLabelText(/password/i), password);
    await user.click(screen.getByRole("button", { name: /log in|sign in/i }));
  }

  /**
   * TEST 6 — test_login_401_shows_error_no_navigation
   * Scenario: login 401 — shows error message, no navigation
   */
  it("test_login_401_shows_error_no_navigation", async () => {
    // Arrange
    server.use(
      http.post("http://localhost:3000/api/auth/login", () =>
        HttpResponse.json(
          {
            type: "about:blank",
            title: "Invalid credentials",
            status: 401,
            code: "ERR_AUTH_INVALID_CREDENTIALS",
          },
          { status: 401 }
        )
      )
    );

    render(<LoginForm />);

    // Act
    await fillAndSubmit();

    // Assert
    await waitFor(() => {
      expect(screen.getByText(/invalid credentials/i)).toBeInTheDocument();
    });
    const router = getRouterMock();
    expect(router.push).not.toHaveBeenCalledWith("/keys");
    expect(localStorage.getItem("ai_proxy_token")).toBeNull();
  });
});
