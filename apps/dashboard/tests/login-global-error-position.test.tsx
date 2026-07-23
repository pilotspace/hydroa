/**
 * login-global-error-position — frontdoor-polish task 2
 *
 * The failed-password-login message (`globalError`) must render INSIDE the
 * password affordance, beside the "Log in" button the visitor just clicked —
 * not at a fixed slot above the reordered `[data-slot="login-entry-routes"]`
 * region, where the corporate ordering paints it far from that button.
 *
 * RED before build: today the alert renders between the Email field and the
 * region — outside the Log in button's container — so both containment
 * assertions fail for the right reason (wrong position), not a broken harness.
 */

import { describe, it, expect } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";

import { LoginForm } from "@/components/auth/LoginForm";

const LOGIN_URL = "http://localhost:3000/api/auth/login";

function getRegion(container: HTMLElement): HTMLElement {
  const region = container.querySelector('[data-slot="login-entry-routes"]');
  expect(region).not.toBeNull();
  return region as HTMLElement;
}

describe("LoginForm — globalError renders beside the Log in button", () => {
  const user = userEvent.setup();

  it("test_failed_login_error_renders_beside_the_log_in_button", async () => {
    // covers: M1, M2, M4 — GLOBAL_ERROR_NOT_ANCHORED,
    // GLOBAL_ERROR_SEMANTICS_CHANGED, GLOBAL_ERROR_DUPLICATED
    server.use(
      http.post(LOGIN_URL, () =>
        HttpResponse.json(
          {
            type: "about:blank",
            title: "Invalid credentials",
            status: 401,
            code: "ERR_AUTH_INVALID_CREDENTIALS",
          },
          { status: 401 },
        ),
      ),
    );
    render(<LoginForm />);

    await user.type(screen.getByLabelText(/^email$/i), "ada@acme.io");
    await user.type(screen.getByLabelText(/password/i), "hunter12345");
    const logIn = screen.getByRole("button", { name: /^log in$/i });
    await user.click(logIn);

    const alert = await screen.findByText("Invalid credentials");

    // M2 — semantics byte-unchanged: same role/aria/text as shipped.
    expect(alert).toHaveAttribute("role", "alert");
    expect(alert).toHaveAttribute("aria-live", "polite");

    // M4 — exactly one instance: the old fixed slot is removed, not duplicated.
    expect(screen.getAllByText("Invalid credentials")).toHaveLength(1);

    // M1 — the error lives in the Log in button's own container (passwordRoute),
    // positioned ABOVE the button (Tin's freeze decision: the message is read
    // before the button it explains).
    expect(logIn.parentElement).toContainElement(alert);
    expect(
      logIn.compareDocumentPosition(alert) & Node.DOCUMENT_POSITION_PRECEDING,
    ).toBeTruthy();
  });

  it("test_error_travels_with_the_password_route_under_corporate_reordering", async () => {
    // covers: M2 (Boundary: generic fallback text), M3, M4 —
    // GLOBAL_ERROR_NOT_ANCHORED, ENTRY_INVARIANTS_TOUCHED
    server.use(http.post(LOGIN_URL, () => HttpResponse.error()));
    const { container } = render(<LoginForm />);

    // A corporate email flips the region to SSO-first — "Log in" is no longer
    // the first control, which is exactly when the fixed top slot misleads.
    await user.type(screen.getByLabelText(/^email$/i), "dana@acme-corp.com");
    await user.type(screen.getByLabelText(/password/i), "hunter12345");
    const region = getRegion(container);
    await waitFor(() =>
      expect(region).toHaveAttribute("data-domain-class", "corporate"),
    );

    const logIn = within(region).getByRole("button", { name: /^log in$/i });
    await user.click(logIn);

    // Boundary shape 2: the thrown fetch takes the generic catch fallback.
    const alert = await screen.findByText("An unexpected error occurred");

    // M3 — the error travels WITH the password subtree INSIDE the reordered
    // region, beside the button — not at the fixed slot above the region.
    expect(region).toContainElement(alert);
    expect(logIn.parentElement).toContainElement(alert);

    // ENTRY_INVARIANTS_TOUCHED — ordering and affordance presence unchanged:
    // still corporate, all four affordances present.
    expect(region).toHaveAttribute("data-domain-class", "corporate");
    expect(
      within(region).getByRole("button", { name: /sign in with sso/i }),
    ).toBeInTheDocument();
    expect(
      within(region).getByRole("button", { name: /saml/i }),
    ).toBeInTheDocument();
    expect(
      within(region).getByRole("link", { name: /create a workspace/i }),
    ).toBeInTheDocument();
  });
});
