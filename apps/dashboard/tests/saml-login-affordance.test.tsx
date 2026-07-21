/**
 * tests/saml-login-affordance.test.tsx — RED suite for domain-auto-assign-login
 * TASK.md §3 v1, M6 (dashboard SAML login affordance).
 *
 * Ground: "NO apps/dashboard/app/auth/saml/ relay route exists — SAML has ZERO
 * dashboard wiring (no login button, no callback relay)." This suite pins the
 * NEW SAML SSO affordance on the login surface, mirroring tests/sso-login.test.tsx's
 * own OIDC "Sign in with SSO" button conventions: the same "Work email or domain"
 * field drives a full-page `window.location.assign` navigation (a fetch alone
 * cannot follow the relay's redirect chain to the IdP).
 *
 * RED before build: no button with an accessible name matching /saml/i exists on
 * LoginForm today.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { LoginForm } from "@/components/auth/LoginForm";

describe("LoginForm — SAML SSO affordance (M6)", () => {
  const user = userEvent.setup();
  // jsdom's window.location.assign is non-configurable, so spyOn fails; redefine
  // window.location wholesale with a mock assign for the duration of each test —
  // mirrors tests/sso-login.test.tsx's own setup exactly.
  const assign = vi.fn();
  let originalLocation: Location;

  beforeEach(() => {
    originalLocation = window.location;
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: {
        ...originalLocation,
        assign,
        href: "http://localhost:3000/login",
        origin: "http://localhost:3000",
      },
    });
    assign.mockClear();
    localStorage.clear();
  });
  afterEach(() => {
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: originalLocation,
    });
    localStorage.clear();
  });

  it("test_saml_affordance_present_on_login_surface", async () => {
    // covers: M6 — the dashboard login surface offers a SAML SSO option
    render(<LoginForm />);
    expect(screen.getByRole("button", { name: /saml/i })).toBeInTheDocument();
  });

  it("test_saml_affordance_navigates_with_domain", async () => {
    // covers: M6 — clicking it, with a domain entered, full-page-navigates toward
    // the SAML login-init path carrying the resolved domain (mirrors the OIDC
    // button's own ?domain= navigation shape). Retarget (merge-login-email-field
    // §3): selector only — the domain is now typed into the merged "Email" field.
    render(<LoginForm />);
    await user.type(screen.getByLabelText(/^email$/i), "alice@acme.com");
    await user.click(screen.getByRole("button", { name: /saml/i }));

    await waitFor(() => expect(assign).toHaveBeenCalled());
    const target = String(assign.mock.calls[0][0]);
    expect(target).toContain("saml/login");
    expect(target).toContain("domain=acme.com");
  });
});
