/**
 * sso-login-button (v31) — TASK.md §4, scenarios OW-SSO 1–5.
 *
 * Adds a "Work email or domain" field to LoginForm that drives the EXISTING
 * "Sign in with SSO" button's ?domain= (per-tenant OIDC). The button does a
 * full-page navigation via window.location.assign (mirrors the original <a href>,
 * so the browser follows the relay's 302 chain to the IdP — a fetch could not).
 *
 * RED before BUILD: the domain field + handler don't exist yet — the "Sign in
 * with SSO" control is a bare <a> with a static href, so the assign spy is never
 * called and the field label is absent.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "./mocks/server";
import { LoginForm } from "@/components/auth/LoginForm";

const OIDC_LOGIN = "/api/auth/oidc/login";

describe("LoginForm — SSO domain field", () => {
  const user = userEvent.setup();
  // jsdom's window.location.assign is non-configurable, so spyOn fails; redefine
  // window.location wholesale with a mock assign for the duration of each test.
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
  });
  afterEach(() => {
    Object.defineProperty(window, "location", {
      configurable: true,
      writable: true,
      value: originalLocation,
    });
  });

  async function clickSso(value?: string) {
    if (value) {
      await user.type(screen.getByLabelText(/work email or domain/i), value);
    }
    await user.click(screen.getByRole("button", { name: /sign in with sso/i }));
  }

  it("test_sso_with_email_sends_domain", async () => {
    render(<LoginForm />);
    await clickSso("alice@acme.com");
    expect(assign).toHaveBeenCalledWith(`${OIDC_LOGIN}?domain=acme.com`);
  });

  it("test_sso_with_bare_domain", async () => {
    render(<LoginForm />);
    await clickSso("acme.com");
    expect(assign).toHaveBeenCalledWith(`${OIDC_LOGIN}?domain=acme.com`);
  });

  it("test_sso_empty_keeps_env_fallback", async () => {
    render(<LoginForm />);
    await clickSso(); // empty field
    expect(assign).toHaveBeenCalledWith(OIDC_LOGIN); // no ?domain=
  });

  it("test_sso_malformed_blocks_navigation", async () => {
    render(<LoginForm />);
    await clickSso("notadomain"); // no dot → invalid
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(assign).not.toHaveBeenCalled();
  });

  it("test_password_login_unaffected", async () => {
    let loginCalled = false;
    server.use(
      http.post("http://localhost:3000/api/auth/login", () => {
        loginCalled = true;
        return HttpResponse.json({ ok: true }, { status: 200 });
      }),
    );
    render(<LoginForm />);

    // Type into the SSO field too — it must not interfere with password login.
    await user.type(screen.getByLabelText(/work email or domain/i), "acme.com");
    await user.type(screen.getByLabelText(/^email$/i), "ada@acme.io");
    await user.type(screen.getByLabelText(/password/i), "hunter12345");
    await user.click(screen.getByRole("button", { name: /^log in$/i }));

    await waitFor(() => expect(loginCalled).toBe(true));
    expect(assign).not.toHaveBeenCalled(); // password submit ≠ SSO navigation
  });
});
